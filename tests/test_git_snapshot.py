import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.git_snapshot import (
        GitSnapshotEntry,
        GitSnapshotError,
        capture_git_snapshot,
        manifest_fingerprint,
        parse_index_entries,
        verify_git_snapshot_commit,
    )
finally:
    sys.path.pop(0)


def git(repo: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def init_git_repo(repo: Path) -> str:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(
        repo,
        "-c",
        "user.name=TaskGov Test",
        "-c",
        "user.email=taskgov@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "baseline",
    )
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def commit_tree(repo: Path, tree: str, *parents: str, message: str) -> str:
    parent_args = [argument for parent in parents for argument in ("-p", parent)]
    return git(
        repo,
        "-c",
        "user.name=TaskGov Test",
        "-c",
        "user.email=taskgov@example.invalid",
        "commit-tree",
        tree,
        *parent_args,
        input_text=message + "\n",
    ).stdout.strip()


def repository_file_state(repo: Path) -> dict[str, bytes]:
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file()
    }


def stage_tree_change(repo: Path, change: str) -> None:
    if change == "added":
        (repo / "after-review.txt").write_text("added\n", encoding="utf-8")
        git(repo, "add", "after-review.txt")
    elif change == "removed":
        git(repo, "rm", "--cached", "reviewed.txt")
    elif change == "changed":
        (repo / "reviewed.txt").write_text("changed\n", encoding="utf-8")
        git(repo, "add", "reviewed.txt")
    elif change == "renamed":
        (repo / "reviewed.txt").rename(repo / "renamed.txt")
        git(repo, "add", "--all")
    elif change == "mode-changed":
        git(repo, "update-index", "--chmod=+x", "reviewed.txt")
    else:
        raise AssertionError(f"unsupported test change: {change}")


class GitSnapshotTests(unittest.TestCase):
    def test_manifest_v1_is_fixed_and_sorts_raw_path_bytes(self):
        base = "1" * 40
        entries = [
            GitSnapshotEntry(b"100644", b"a" * 40, b"\xffraw"),
            GitSnapshotEntry(b"120000", b"c" * 40, b"z\nname"),
            GitSnapshotEntry(b"100755", b"b" * 40, b"a\tname"),
        ]
        expected = (
            "sha256:"
            "32ba28cad9be8b8579b3467cbd4c98682a876d2a57e6b57590fda7c90a5a6bfe"
        )

        self.assertEqual(manifest_fingerprint(base, entries), expected)
        self.assertEqual(manifest_fingerprint(base, list(reversed(entries))), expected)

        payload = b"".join(
            entry.mode
            + b" "
            + entry.object_id
            + b" 0\t"
            + entry.path
            + b"\0"
            for entry in entries
        )
        parsed = parse_index_entries(payload)
        self.assertEqual([entry.path for entry in parsed], [b"a\tname", b"z\nname", b"\xffraw"])
        self.assertEqual(manifest_fingerprint(base, parsed), expected)

    def test_index_parser_rejects_noncanonical_or_unsafe_records(self):
        valid_prefix = b"100644 " + b"a" * 40
        cases = {
            "missing_nul_terminator": valid_prefix + b" 0\tpath",
            "malformed": b"not-an-index-record\0",
            "nonzero_stage": valid_prefix + b" 2\tpath\0",
            "sparse_directory": b"040000 " + b"a" * 40 + b" 0\tdirectory/\0",
            "zero_object": b"100644 " + b"0" * 40 + b" 0\tintent\0",
        }

        for name, payload in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(GitSnapshotError) as raised:
                    parse_index_entries(payload)
                self.assertEqual(raised.exception.code, "invalid_review_evidence")

    def test_capture_rejects_real_intent_to_add_without_changing_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo)
            (repo / "intent.txt").write_text("not staged yet\n", encoding="utf-8")
            git(repo, "add", "--intent-to-add", "intent.txt")
            before = repository_file_state(repo)

            with self.assertRaises(GitSnapshotError) as raised:
                capture_git_snapshot(repo)

            self.assertEqual(raised.exception.code, "invalid_review_evidence")
            self.assertIn("intent-to-add", raised.exception.message)
            self.assertEqual(repository_file_state(repo), before)

    def test_capture_matches_single_parent_commit_and_ignores_unstaged_untracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            base = init_git_repo(repo)
            (repo / "staged.txt").write_text("reviewed\n", encoding="utf-8")
            git(repo, "add", "staged.txt")
            (repo / "tracked.txt").write_text("unstaged change\n", encoding="utf-8")
            (repo / "untracked.txt").write_text("outside snapshot\n", encoding="utf-8")

            before_capture = repository_file_state(repo)
            snapshot = capture_git_snapshot(repo)
            self.assertEqual(repository_file_state(repo), before_capture)
            self.assertEqual(snapshot.base_revision, base)
            self.assertEqual(snapshot.entry_count, 2)

            tree = git(repo, "write-tree").stdout.strip()
            candidate = commit_tree(repo, tree, base, message="completion")
            before_verify = repository_file_state(repo)
            verified = verify_git_snapshot_commit(
                repo,
                candidate,
                expected_base_revision=snapshot.base_revision,
                expected_fingerprint=snapshot.fingerprint,
            )

            self.assertEqual(repository_file_state(repo), before_verify)
            self.assertEqual(verified, snapshot)

    def test_completion_comparison_rejects_root_merge_wrong_base_and_tree_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            base = init_git_repo(repo)
            (repo / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
            git(repo, "add", "reviewed.txt")
            snapshot = capture_git_snapshot(repo)
            reviewed_tree = git(repo, "write-tree").stdout.strip()

            root = commit_tree(repo, reviewed_tree, message="root")
            wrong_base = commit_tree(repo, reviewed_tree, root, message="wrong base")
            merge = commit_tree(repo, reviewed_tree, base, root, message="merge")
            (repo / "after-review.txt").write_text("not reviewed\n", encoding="utf-8")
            git(repo, "add", "after-review.txt")
            changed_tree = git(repo, "write-tree").stdout.strip()
            tree_mismatch = commit_tree(repo, changed_tree, base, message="tree mismatch")

            before = repository_file_state(repo)
            for name, candidate in {
                "root": root,
                "wrong_base": wrong_base,
                "merge": merge,
                "tree_mismatch": tree_mismatch,
            }.items():
                with self.subTest(name=name):
                    with self.assertRaises(GitSnapshotError) as raised:
                        verify_git_snapshot_commit(
                            repo,
                            candidate,
                            expected_base_revision=snapshot.base_revision,
                            expected_fingerprint=snapshot.fingerprint,
                        )
                    self.assertEqual(raised.exception.code, "review_target_mismatch")
                    self.assertEqual(repository_file_state(repo), before)

    def test_completion_comparison_rejects_each_reviewed_tree_change(self):
        for change in ("added", "removed", "changed", "renamed", "mode-changed"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "repo"
                base = init_git_repo(repo)
                (repo / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
                git(repo, "add", "reviewed.txt")
                snapshot = capture_git_snapshot(repo)

                stage_tree_change(repo, change)
                changed_tree = git(repo, "write-tree").stdout.strip()
                candidate = commit_tree(repo, changed_tree, base, message=change)
                before = repository_file_state(repo)

                with self.assertRaises(GitSnapshotError) as raised:
                    verify_git_snapshot_commit(
                        repo,
                        candidate,
                        expected_base_revision=snapshot.base_revision,
                        expected_fingerprint=snapshot.fingerprint,
                    )

                self.assertEqual(raised.exception.code, "review_target_mismatch")
                self.assertEqual(repository_file_state(repo), before)

    def test_completion_comparison_rejects_hook_altered_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            base = init_git_repo(repo)
            (repo / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
            git(repo, "add", "reviewed.txt")
            snapshot = capture_git_snapshot(repo)
            hook = repo / ".git" / "hooks" / "pre-commit"
            hook.write_text(
                "#!/bin/sh\n"
                "printf 'hook altered\\n' > hook-altered.txt\n"
                "git add hook-altered.txt\n",
                encoding="utf-8",
                newline="\n",
            )
            hook.chmod(0o755)
            git(
                repo,
                "-c",
                "user.name=TaskGov Test",
                "-c",
                "user.email=taskgov@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "hook-altered completion",
            )
            candidate = git(repo, "rev-parse", "HEAD").stdout.strip()
            self.assertNotEqual(candidate, base)
            before = repository_file_state(repo)

            with self.assertRaises(GitSnapshotError) as raised:
                verify_git_snapshot_commit(
                    repo,
                    candidate,
                    expected_base_revision=snapshot.base_revision,
                    expected_fingerprint=snapshot.fingerprint,
                )

            self.assertEqual(raised.exception.code, "review_target_mismatch")
            self.assertEqual(repository_file_state(repo), before)


if __name__ == "__main__":
    unittest.main()
