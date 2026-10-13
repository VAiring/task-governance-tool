"""Real isolated Git checks for the sole integrated local-commit exception."""

from contextlib import contextmanager
import tempfile
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


class FinalizationGitTests(unittest.TestCase):
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
            verified = verify_git_snapshot_commit(repo, candidate, expected_base_revision=base,
                                                  expected_fingerprint=snapshot.fingerprint)
            self.assertEqual(verified.fingerprint, snapshot.fingerprint)
            self.assertEqual(verified.entry_count, 60_001)
            self.assertEqual((repo / ".git/index").read_bytes(), index)
            self.assertEqual((repo / "tracked.txt").read_bytes(), worktree)
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), base)
            # An unreviewed baseline leaf also participates in the full hash.
            replacement = git(repo, "rev-parse", ":tracked.txt").stdout.strip()
            git(repo, "update-index", "--cacheinfo", f"100644,{replacement},{paths[0]}")
            wrong = commit_tree(repo, git(repo, "write-tree").stdout.strip(), base, message="Wrong unchanged leaf")
            with self.assertRaises(GitSnapshotError):
                verify_git_snapshot_commit(repo, wrong, expected_base_revision=base,
                                           expected_fingerprint=snapshot.fingerprint)
            with self.assertRaises(adapter.FinalizationGitError):
                self.candidate(repo, binding)
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), base)

    @contextmanager
    def project(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "project"
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
