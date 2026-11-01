"""Real isolated Git checks for the sole integrated local-commit exception."""

from contextlib import contextmanager
import os
import tempfile
import shutil
import stat
import unittest
from unittest.mock import patch
from pathlib import Path

from tests.test_git_snapshot import git, init_git_repo, capture_git_snapshot, commit_tree
from task_governance_tool.git_snapshot import (
    GIT_CAPTURE_BYTE_LIMIT, GitSnapshotError, stream_index_fingerprint,
    stream_tree_entries, verify_git_snapshot_commit,
)
from task_governance_tool.artifact_manifest import observe_staged_git_manifest, build_artifact_entries
from task_governance_tool import review_finalization_git as adapter
from task_governance_tool import git_snapshot as snapshots


class FinalizationGitTests(unittest.TestCase):
    def test_candidate_content_walk_is_shared_but_manual_and_later_calls_are_fresh(self):
        with self.project() as (repo, binding), patch.object(snapshots, "stream_tree_entries",
                wraps=snapshots.stream_tree_entries) as walks:
            with snapshots.snapshot_commit_validation() as validation:
                candidate = adapter.create_candidate(repo, **binding, task_id="tg_task_0123456789abcdef",
                                                     git_validation=validation)
                self.assertFalse(adapter.published(repo, candidate=candidate, **binding, git_validation=validation))
                adapter.publish_candidate(repo, candidate=candidate, **binding, git_validation=validation)
                self.assertTrue(adapter.published(repo, candidate=candidate, **binding, git_validation=validation))
                self.assertEqual(walks.call_count, 1)
                # Explicit manual work nested inside the scope is independent.
                verify_git_snapshot_commit(repo, candidate, expected_base_revision=binding["base"],
                                           expected_fingerprint=binding["fingerprint"])
                self.assertEqual(walks.call_count, 2)
            adapter.published(repo, candidate=candidate, **binding, git_validation=validation)
            self.assertEqual(walks.call_count, 3)
            with snapshots.snapshot_commit_validation() as restarted:
                adapter.published(repo, candidate=candidate, **binding, git_validation=restarted)
                self.assertEqual(walks.call_count, 4)

    def test_content_reuse_never_skips_mutable_publication_checks(self):
        for changed in ("index", "base", "branch", "lock", "gate"):
            with self.subTest(changed=changed), self.project() as (repo, binding), \
                    snapshots.snapshot_commit_validation() as validation:
                candidate = adapter.create_candidate(repo, **binding, task_id="tg_task_0123456789abcdef",
                                                     git_validation=validation)
                if changed == "index":
                    (repo / "tracked.txt").write_text("later")
                    git(repo, "add", "tracked.txt")
                elif changed == "base":
                    git(repo, "commit", "--quiet", "-m", "other")
                elif changed == "branch":
                    git(repo, "checkout", "-b", "other")
                elif changed == "lock":
                    (repo / ".git/index.lock").write_bytes(b"foreign")
                def revalidate():
                    if changed == "gate":
                        raise adapter.FinalizationGitError("completion_check_stale")
                before = git(repo, "rev-parse", "HEAD").stdout
                with self.assertRaises(adapter.FinalizationGitError) as rejected:
                    adapter.publish_candidate(repo, candidate=candidate, **binding,
                                              git_validation=validation, revalidate=revalidate)
                expected_code = {"lock": "finalization_git_busy", "gate": "completion_check_stale"}.get(
                    changed, "review_target_mismatch")
                self.assertEqual(rejected.exception.code, expected_code)
                self.assertEqual(git(repo, "rev-parse", "HEAD").stdout, before)
                if changed == "lock":
                    self.assertEqual((repo / ".git/index.lock").read_bytes(), b"foreign")

    def test_exact_candidate_source_scope_and_failed_observation_bound_reuse(self):
        with self.project() as (repo, binding):
            candidate = self.candidate(repo, binding)
            other = repo.parent / "copy"
            tree = git(repo, "rev-parse", candidate + "^{tree}").stdout.strip()
            bad_parent = commit_tree(repo, tree, candidate, message="wrong parent")
            wrong_tree = commit_tree(repo, git(repo, "rev-parse", binding["base"] + "^{tree}").stdout.strip(),
                                     binding["base"], message="wrong tree")
            # The replacement must retain the malformed objects being tested.
            shutil.copytree(repo, other)
            options = dict(expected_base_revision=binding["base"], expected_fingerprint=binding["fingerprint"])
            with snapshots.snapshot_commit_validation() as validation, patch.object(snapshots, "stream_tree_entries",
                    wraps=snapshots.stream_tree_entries) as walks:
                def verify(path=repo, revision=candidate, **changes):
                    return verify_git_snapshot_commit(path, revision, **(options | changes), git_validation=validation)
                original = verify()
                self.assertEqual(verify(), original)
                self.assertEqual(walks.call_count, 1)
                self.assertEqual(verify(other), original)
                self.assertEqual(walks.call_count, 2)
                nested = capture_git_snapshot(repo / "nested")
                verify(repo / "nested", expected_fingerprint=nested.fingerprint)
                self.assertEqual(walks.call_count, 3)
                verify()
                before = walks.call_count
                repo.rename(repo.parent / "replaced")
                shutil.copytree(other, repo)
                self.assertEqual(verify(), original)
                self.assertEqual(walks.call_count, before + 1, "replaced physical repository is a new source")
                for revision, changes, expected_code in (
                        (candidate, {"expected_fingerprint": "sha256:" + "0" * 64}, "review_target_mismatch"),
                        (candidate, {"expected_base_revision": candidate}, "review_target_mismatch"),
                        (bad_parent, {}, "review_target_mismatch"),
                        (wrong_tree, {}, "review_target_mismatch"),
                        ("1" * 40, {}, "git_commit_not_found_or_ambiguous")):
                    with self.subTest(revision=revision, changes=changes):
                        verify()
                        with self.assertRaises(GitSnapshotError) as rejected:
                            verify(revision=revision, **changes)
                        self.assertEqual(rejected.exception.code, expected_code)
                        before = walks.call_count
                        self.assertEqual(verify(), original)
                        self.assertEqual(walks.call_count, before + 1)

    def test_missing_cached_tree_preserves_completion_error_and_invalidates_reuse(self):
        with self.project() as (repo, binding), snapshots.snapshot_commit_validation() as validation:
            candidate = self.candidate(repo, binding)
            tree = git(repo, "rev-parse", candidate + "^{tree}").stdout.strip()
            loose_tree = repo / ".git" / "objects" / tree[:2] / tree[2:]
            original_tree = loose_tree.read_bytes()
            original_mode = stat.S_IMODE(loose_tree.stat().st_mode)
            options = dict(expected_base_revision=binding["base"], expected_fingerprint=binding["fingerprint"])
            with patch.object(snapshots, "stream_tree_entries", wraps=snapshots.stream_tree_entries) as walks:
                expected = verify_git_snapshot_commit(repo, candidate, **options, git_validation=validation)
                loose_tree.chmod(original_mode | stat.S_IWRITE)
                loose_tree.unlink()
                try:
                    errors = []
                    for scope in (validation, None):
                        with self.assertRaises(GitSnapshotError) as rejected:
                            verify_git_snapshot_commit(repo, candidate, **options, git_validation=scope)
                        errors.append((rejected.exception.code, rejected.exception.field))
                    self.assertEqual(errors, [("review_target_mismatch", "completion_revision")] * 2)
                finally:
                    loose_tree.write_bytes(original_tree)
                    loose_tree.chmod(original_mode)
                before = walks.call_count
                self.assertEqual(verify_git_snapshot_commit(repo, candidate, **options, git_validation=validation), expected)
                self.assertEqual(walks.call_count, before + 1)

    def test_source_observation_failures_use_completion_errors_and_invalidate_reuse(self):
        for failed_arguments in (["rev-parse", "--path-format=absolute", "--git-dir"],
                                 ["rev-parse", "--path-format=absolute", "--git-common-dir"],
                                 ["rev-parse", "--show-prefix"]):
            with self.subTest(arguments=failed_arguments), self.project() as (repo, binding), \
                    snapshots.snapshot_commit_validation() as validation:
                candidate = self.candidate(repo, binding)
                options = dict(expected_base_revision=binding["base"], expected_fingerprint=binding["fingerprint"])
                read = snapshots.run_git_bytes
                def unavailable_source(project, arguments, **kwargs):
                    if arguments == failed_arguments:
                        arguments = ["rev-parse", "--verify", "refs/heads/missing-source-observation"]
                    return read(project, arguments, **kwargs)
                with patch.object(snapshots, "stream_tree_entries", wraps=snapshots.stream_tree_entries) as walks:
                    expected = verify_git_snapshot_commit(repo, candidate, **options, git_validation=validation)
                    with patch.object(snapshots, "run_git_bytes", side_effect=unavailable_source):
                        with self.assertRaises(GitSnapshotError) as rejected:
                            verify_git_snapshot_commit(repo, candidate, **options, git_validation=validation)
                    self.assertEqual((rejected.exception.code, rejected.exception.field),
                                     ("review_target_mismatch", "completion_revision"))
                    self.assertEqual(verify_git_snapshot_commit(repo, candidate, **options, git_validation=validation), expected)
                    self.assertEqual(walks.call_count, 2)

    def test_missing_repository_preserves_fresh_error_precedence_and_invalidates_reuse(self):
        with self.project() as (repo, binding), snapshots.snapshot_commit_validation() as validation:
            candidate = self.candidate(repo, binding)
            options = dict(expected_base_revision=binding["base"], expected_fingerprint=binding["fingerprint"])
            moved = repo.parent / "moved"
            with patch.object(snapshots, "stream_tree_entries", wraps=snapshots.stream_tree_entries) as walks:
                expected = verify_git_snapshot_commit(repo, candidate, **options, git_validation=validation)
                for changes, code, field in (({}, "git_commit_not_found_or_ambiguous", "completion_revision"),
                        ({"expected_base_revision": "invalid"}, "review_target_mismatch", "review_target_value"),
                        ({"expected_fingerprint": "invalid"}, "review_target_mismatch", "review_target_value")):
                    with self.subTest(changes=changes):
                        verify_git_snapshot_commit(repo, candidate, **options, git_validation=validation)
                        repo.rename(moved)
                        try:
                            errors = []
                            for scope in (validation, None):
                                with self.assertRaises(GitSnapshotError) as rejected:
                                    verify_git_snapshot_commit(repo, candidate, **(options | changes),
                                                               git_validation=scope)
                                errors.append((rejected.exception.code, rejected.exception.field))
                            self.assertEqual(errors, [(code, field)] * 2)
                        finally:
                            moved.rename(repo)
                        before = walks.call_count
                        self.assertEqual(verify_git_snapshot_commit(repo, candidate, **options,
                                                                   git_validation=validation), expected)
                        self.assertEqual(walks.call_count, before + 1)

    def test_source_filesystem_and_encoding_failures_are_bounded_on_both_observations(self):
        for failure in ("root_resolve", "git_resolve", "root_stat", "git_stat", "encoding", "nul_path"):
            for observation in (1, 2):
                with self.subTest(failure=failure, observation=observation), self.project() as (repo, binding), \
                        snapshots.snapshot_commit_validation() as validation:
                    candidate = self.candidate(repo, binding)
                    options = dict(expected_base_revision=binding["base"], expected_fingerprint=binding["fingerprint"])
                    expected = verify_git_snapshot_commit(repo, candidate, **options, git_validation=validation)
                    resolve, read_stat, read_git = Path.resolve, Path.stat, snapshots.run_git_bytes
                    identity = snapshots._read_snapshot_source_identity
                    seen = 0
                    def resolving(path, *args, **kwargs):
                        if kwargs.get("strict") and ((failure == "root_resolve" and path == repo)
                                or (failure == "git_resolve" and path == repo / ".git")):
                            raise FileNotFoundError("isolated source unavailable")
                        return resolve(path, *args, **kwargs)
                    def stating(path, *args, **kwargs):
                        if ((failure == "root_stat" and path == repo)
                                or (failure == "git_stat" and path == repo / ".git")):
                            raise PermissionError("isolated source unavailable")
                        return read_stat(path, *args, **kwargs)
                    def reading(project, arguments, **kwargs):
                        if arguments == ["rev-parse", "--path-format=absolute", "--git-dir"]:
                            if failure == "encoding":
                                return b"\xff\n"
                            if failure == "nul_path":
                                return b"bad\x00path\n"
                        return read_git(project, arguments, **kwargs)
                    def observe(project):
                        nonlocal seen
                        seen += 1
                        if seen != observation:
                            return identity(project)
                        with patch.object(Path, "resolve", resolving), patch.object(Path, "stat", stating), \
                                patch.object(snapshots, "run_git_bytes", side_effect=reading):
                            return identity(project)
                    with patch.object(snapshots, "_read_snapshot_source_identity", side_effect=observe):
                        with self.assertRaises(GitSnapshotError) as rejected:
                            verify_git_snapshot_commit(repo, candidate, **options, git_validation=validation)
                    self.assertEqual((rejected.exception.code, rejected.exception.field),
                                     ("review_target_mismatch", "completion_revision"))
                    self.assertIsNone(validation.key)
                    self.assertIsNone(validation.snapshot)
                    with patch.object(snapshots, "stream_tree_entries", wraps=snapshots.stream_tree_entries) as walks:
                        self.assertEqual(verify_git_snapshot_commit(repo, candidate, **options,
                                                                   git_validation=validation), expected)
                        self.assertEqual(walks.call_count, 1)

    def test_source_paths_preserve_unicode_separators_and_filesystem_bytes(self):
        names = ["repo-" + value + "-path" for value in ("\u0085", "\u2028", "\u2029")]
        if os.name != "nt":
            names += ["repo-with\nembedded-and-final\n", os.fsdecode(b"repo-\xff-path")]
        for name in names:
            with self.subTest(name=name), self.project(name=name) as (repo, binding), \
                    snapshots.snapshot_commit_validation() as validation:
                candidate = self.candidate(repo, binding)
                options = dict(expected_base_revision=binding["base"], expected_fingerprint=binding["fingerprint"])
                expected = verify_git_snapshot_commit(repo, candidate, **options)
                with patch.object(snapshots, "stream_tree_entries", wraps=snapshots.stream_tree_entries) as walks:
                    self.assertEqual(verify_git_snapshot_commit(repo, candidate, **options,
                                                               git_validation=validation), expected)
                    self.assertEqual(verify_git_snapshot_commit(repo, candidate, **options,
                                                               git_validation=validation), expected)
                    self.assertEqual(walks.call_count, 1)

    def test_sha256_candidate_preserves_full_fingerprint_and_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "project"
            repo.mkdir()
            git(repo, "init", "--quiet", "--object-format=sha256")
            git(repo, "config", "user.name", "TaskGov Test")
            git(repo, "config", "user.email", "taskgov@example.invalid")
            (repo / "tracked.txt").write_text("baseline\n")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "--quiet", "-m", "baseline")
            (repo / "tracked.txt").write_text("reviewed\n")
            git(repo, "add", "tracked.txt")
            snapshot = capture_git_snapshot(repo)
            self.assertEqual(len(snapshot.base_revision), 64)
            binding = dict(base=snapshot.base_revision, fingerprint=snapshot.fingerprint,
                           expected_branch=adapter.branch(repo))
            index = (repo / ".git/index").read_bytes()
            candidate = self.candidate(repo, binding)
            self.assertEqual(len(candidate), 64)
            self.assertEqual(verify_git_snapshot_commit(repo, candidate, expected_base_revision=binding["base"],
                                                       expected_fingerprint=binding["fingerprint"]), snapshot)
            adapter.publish_candidate(repo, candidate=candidate, **binding)
            self.assertEqual((repo / ".git/index").read_bytes(), index)
            self.assertTrue(adapter.published(repo, candidate=candidate, **binding))

    def test_listing_above_byte_capture_limit_keeps_full_snapshot_and_candidate_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "project"
            initial = init_git_repo(repo)
            git(repo, "config", "user.name", "TaskGov Test")
            git(repo, "config", "user.email", "taskgov@example.invalid")
            blob = git(repo, "rev-parse", "HEAD:tracked.txt").stdout.strip()
            paths = [f"{number:06d}-" + "x" * 233 for number in range(60_000)]
            self.assertTrue(all(len(path.encode()) == 240 for path in paths))
            raw = "".join(f"100644 blob {blob}\t{path}\0" for path in paths + ["tracked.txt"])
            tree = git(repo, "mktree", "-z", input_text=raw).stdout.strip()
            base = commit_tree(repo, tree, initial, message="Large unchanged tree")
            git(repo, "update-ref", "HEAD", base, initial)
            git(repo, "read-tree", base)
            (repo / "tracked.txt").write_text("one reviewed change\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            snapshot = capture_git_snapshot(repo)
            self.assertEqual(snapshot.changed_paths, (b"tracked.txt",))
            self.assertEqual(snapshot.entry_count, 60_001)
            self.assertGreater(stream_index_fingerprint(repo, base).identity.byte_count, GIT_CAPTURE_BYTE_LIMIT)
            self.assertGreater(stream_tree_entries(repo, tree, object_id_length=40).identity.byte_count,
                               GIT_CAPTURE_BYTE_LIMIT)
            manifest = observe_staged_git_manifest(repo)
            self.assertEqual(manifest.target_value, snapshot.fingerprint)
            self.assertEqual(manifest.target_base_revision, base)
            self.assertEqual(len(build_artifact_entries(manifest.before_leaves, manifest.after_leaves)), 1)
            index, worktree = (repo / ".git/index").read_bytes(), (repo / "tracked.txt").read_bytes()
            binding = dict(base=base, fingerprint=snapshot.fingerprint, expected_branch=adapter.branch(repo))
            candidate = self.candidate(repo, binding)
            with snapshots.snapshot_commit_validation() as validation, patch.object(snapshots, "stream_tree_entries",
                    wraps=snapshots.stream_tree_entries) as walks:
                verified = verify_git_snapshot_commit(repo, candidate, expected_base_revision=base,
                    expected_fingerprint=snapshot.fingerprint, git_validation=validation)
                repeated = verify_git_snapshot_commit(repo, candidate, expected_base_revision=base,
                    expected_fingerprint=snapshot.fingerprint, git_validation=validation)
                self.assertEqual(repeated, verified)
                self.assertEqual(walks.call_count, 1)
            self.assertEqual(verified.fingerprint, snapshot.fingerprint)
            self.assertEqual(verified.entry_count, 60_001)
            self.assertEqual((repo / ".git/index").read_bytes(), index)
            self.assertEqual((repo / "tracked.txt").read_bytes(), worktree)
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), base)
            # An unreviewed baseline leaf also participates in the full hash.
            replacement = git(repo, "rev-parse", ":tracked.txt").stdout.strip()
            git(repo, "update-index", "--cacheinfo", f"100644,{replacement},{paths[0]}")
            wrong = commit_tree(repo, git(repo, "write-tree").stdout.strip(), base, message="Wrong unchanged leaf")
            with self.assertRaises(GitSnapshotError) as rejected:
                verify_git_snapshot_commit(repo, wrong, expected_base_revision=base,
                                           expected_fingerprint=snapshot.fingerprint)
            self.assertEqual(rejected.exception.code, "review_target_mismatch")
            with self.assertRaises(adapter.FinalizationGitError):
                self.candidate(repo, binding)
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), base)

    @contextmanager
    def project(self, *, name="project"):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / name
            init_git_repo(repo)
            git(repo, "config", "user.name", "TaskGov Test")
            git(repo, "config", "user.email", "taskgov@example.invalid")
            (repo / "nested").mkdir()
            (repo / "nested" / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
            git(repo, "add", "nested/reviewed.txt")
            snapshot = capture_git_snapshot(repo)
            binding = dict(base=snapshot.base_revision, fingerprint=snapshot.fingerprint,
                           expected_branch=adapter.branch(repo))
            yield repo, binding

    def candidate(self, repo, binding):
        return adapter.create_candidate(repo, **binding, task_id="tg_task_0123456789abcdef")

    def test_fixed_tree_commit_preserves_index_and_unrelated_work(self):
        with self.project() as (repo, binding):
            (repo / "tracked.txt").write_text("unstaged\n")
            (repo / "untracked.txt").write_text("reference only\n")
            before = (repo / ".git/index").read_bytes()
            candidate = self.candidate(repo, binding)
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), binding["base"])
            adapter.publish_candidate(repo, candidate=candidate, **binding)
            self.assertTrue(adapter.published(repo, candidate=candidate, **binding))
            self.assertEqual((repo / ".git/index").read_bytes(), before)
            self.assertEqual(git(repo, "show", "HEAD:tracked.txt").stdout, "baseline\n")
            self.assertEqual((repo / "tracked.txt").read_text(), "unstaged\n")
            self.assertEqual((repo / "untracked.txt").read_text(), "reference only\n")
            self.assertEqual(git(repo, "rev-list", "--count", "HEAD").stdout.strip(), "2")

    def test_changed_index_or_base_never_publishes_candidate(self):
        for changed in ("index", "base", "branch"):
            with self.subTest(changed=changed), self.project() as (repo, binding):
                candidate = self.candidate(repo, binding)
                if changed == "index":
                    (repo / "tracked.txt").write_text("later")
                    git(repo, "add", "tracked.txt")
                elif changed == "base":
                    git(repo, "commit", "--quiet", "-m", "other")
                else:
                    git(repo, "checkout", "-b", "other")
                before = git(repo, "rev-parse", "HEAD").stdout.strip()
                with self.assertRaises(adapter.FinalizationGitError):
                    adapter.publish_candidate(repo, candidate=candidate, **binding)
                self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), before)

    def test_foreign_lock_is_preserved_and_no_ref_moves(self):
        with self.project() as (repo, binding):
            candidate = self.candidate(repo, binding)
            lock = repo / ".git/HEAD.lock"
            lock.write_bytes(b"foreign")
            with self.assertRaises(adapter.FinalizationGitError) as raised:
                adapter.publish_candidate(repo, candidate=candidate, **binding)
            self.assertEqual(raised.exception.code, "finalization_git_busy")
            self.assertEqual(lock.read_bytes(), b"foreign")
            self.assertFalse((repo / ".git/index.lock").exists())
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), binding["base"])

    def test_gate_rejection_inside_locks_leaves_branch_unchanged(self):
        with self.project() as (repo, binding):
            candidate = self.candidate(repo, binding)
            def reject():
                raise adapter.FinalizationGitError("completion_check_stale")
            with self.assertRaises(adapter.FinalizationGitError):
                adapter.publish_candidate(repo, candidate=candidate, **binding, revalidate=reject)
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), binding["base"])

    def test_lost_publish_response_is_observable_without_second_commit(self):
        with self.project() as (repo, binding):
            candidate = self.candidate(repo, binding)
            original = adapter._publish_transaction
            def lost(*args, **kwargs):
                result = original(*args, **kwargs)
                raise adapter.FinalizationGitError("finalization_git_outcome_unknown")
            with patch.object(adapter, "_publish_transaction", side_effect=lost):
                with self.assertRaises(adapter.FinalizationGitError):
                    adapter.publish_candidate(repo, candidate=candidate, **binding)
            self.assertTrue(adapter.published(repo, candidate=candidate, **binding))
            self.assertEqual(git(repo, "rev-list", "--count", "HEAD").stdout.strip(), "2")

    def test_nested_project_is_explicitly_unsupported(self):
        with self.project() as (repo, binding):
            with self.assertRaises(adapter.FinalizationGitError) as raised:
                self.candidate(repo / "nested", binding)
            self.assertEqual(raised.exception.code, "finalization_git_unsupported")


if __name__ == "__main__":
    unittest.main()
