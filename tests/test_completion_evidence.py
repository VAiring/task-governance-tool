import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.review_test_helpers import seed_review_evidence
from tests.m14_test_support import (
    initialize_taskgov_internal,
    run_taskgov_internal,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_PATH = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_PATH))
try:
    from task_governance_tool.completion import CompletionEvidenceError, resolve_git_commit
finally:
    sys.path.pop(0)


def run_taskgov(*args):
    return run_taskgov_internal(*args)


def git(repo, *args, input_text=None, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def init_git_repo(repo):
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


def init_db(db, repo):
    initialize_taskgov_internal(repo=repo, db=db)


def add_task(db, repo, title="Evidence task"):
    result = run_taskgov(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)["data"]["task"]


def edit(db, repo, task_id, *args):
    return run_taskgov(
        "task",
        "edit",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        *args,
        "--json",
    )


def completion_state(db, task_id):
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT completion_evidence_kind, completion_evidence_revision,
                   completion_evidence_reason, external_revision_approved,
                   completion_commit_required, completion_commit_hash
              FROM tasks
             WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        return dict(row)


def git_state(repo):
    return {
        "head": git(repo, "rev-parse", "HEAD").stdout,
        "refs": git(repo, "show-ref", "--head").stdout,
        "status": git(repo, "status", "--porcelain=v2", "--untracked-files=all").stdout,
        "index": (repo / ".git" / "index").read_bytes(),
        "config": (repo / ".git" / "config").read_bytes(),
    }


def write_ambiguous_commit_objects(repo):
    tree = git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    seen = {}
    collision = None
    for index in range(5000):
        content = (
            f"tree {tree}\n"
            "author Test <test@example.invalid> 0 +0000\n"
            "committer Test <test@example.invalid> 0 +0000\n"
            f"\ncollision {index}\n"
        )
        encoded = content.encode("utf-8")
        object_id = hashlib.sha1(b"commit " + str(len(encoded)).encode("ascii") + b"\0" + encoded).hexdigest()
        prefix = object_id[:4]
        if prefix in seen and seen[prefix][0] != object_id:
            collision = (prefix, seen[prefix][1], content)
            break
        seen[prefix] = (object_id, content)
    if collision is None:
        raise AssertionError("could not synthesize an abbreviated-object collision")
    prefix, first, second = collision
    for content in (first, second):
        git(
            repo,
            "hash-object",
            "-t",
            "commit",
            "--literally",
            "-w",
            "--stdin",
            input_text=content,
        )
    return prefix


class CompletionEvidenceTests(unittest.TestCase):
    def test_short_git_hash_is_canonicalized_and_validation_does_not_change_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            revision = init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            before = git_state(repo)

            result = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "git_commit",
                "--completion-revision",
                revision[:12],
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(git_state(repo), before)
            self.assertEqual(
                completion_state(db, task["task_id"]),
                {
                    "completion_evidence_kind": "git_commit",
                    "completion_evidence_revision": revision,
                    "completion_evidence_reason": "",
                    "external_revision_approved": 0,
                    "completion_commit_required": 1,
                    "completion_commit_hash": revision,
                },
            )

    def test_annotated_tag_peels_to_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            revision = init_git_repo(repo)
            git(
                repo,
                "-c",
                "user.name=TaskGov Test",
                "-c",
                "user.email=taskgov@example.invalid",
                "tag",
                "-a",
                "release-test",
                "-m",
                "release",
            )
            init_db(db, repo)
            task = add_task(db, repo)

            result = edit(
                db,
                repo,
                task["task_id"],
                "--completion-commit-hash",
                "release-test",
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(completion_state(db, task["task_id"])["completion_commit_hash"], revision)

    def test_missing_or_blank_explicit_git_revision_uses_git_error_without_db_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            before = db.read_bytes()

            for extra in ((), ("--completion-revision", "   ")):
                with self.subTest(extra=extra):
                    result = edit(
                        db,
                        repo,
                        task["task_id"],
                        "--completion-evidence-kind",
                        "git_commit",
                        *extra,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(
                        json.loads(result.stdout)["errors"][0]["code"],
                        "git_commit_not_found_or_ambiguous",
                    )
                    self.assertEqual(db.read_bytes(), before)
            alias = edit(
                db,
                repo,
                task["task_id"],
                "--completion-commit-hash",
                "   ",
            )
            self.assertEqual(alias.returncode, 1)
            self.assertEqual(
                json.loads(alias.stdout)["errors"][0]["code"],
                "git_commit_not_found_or_ambiguous",
            )
            self.assertEqual(db.read_bytes(), before)

    def test_missing_blob_ambiguous_and_option_shaped_revisions_are_rejected_without_db_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            blob = git(repo, "hash-object", "-w", "--stdin", input_text="blob\n").stdout.strip()
            ambiguous = write_ambiguous_commit_objects(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                baseline_events = connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]

            for revision in ("missing-revision", blob, ambiguous):
                with self.subTest(revision=revision):
                    result = edit(
                        db,
                        repo,
                        task["task_id"],
                        "--completion-evidence-kind",
                        "git_commit",
                        "--completion-revision",
                        revision,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(
                        json.loads(result.stdout)["errors"][0]["code"],
                        "git_commit_not_found_or_ambiguous",
                    )
                    self.assertEqual(completion_state(db, task["task_id"])["completion_evidence_kind"], "none")
            option_shaped = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "git_commit",
                "--completion-revision=--help",
            )
            self.assertEqual(
                json.loads(option_shaped.stdout)["errors"][0]["code"],
                "git_commit_not_found_or_ambiguous",
            )
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0],
                    baseline_events,
                )

    def test_external_revision_requires_reason_and_approval_and_syncs_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)

            missing_approval = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-42",
                "--completion-evidence-reason",
                "Approved release archive",
            )
            self.assertEqual(
                json.loads(missing_approval.stdout)["errors"][0]["code"],
                "external_revision_approval_required",
            )

            result = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-42",
                "--completion-evidence-reason",
                "Approved release archive",
                "--external-revision-approved",
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("outside target Git history", json.loads(result.stdout)["data"]["event"]["summary"])
            self.assertEqual(
                completion_state(db, task["task_id"]),
                {
                    "completion_evidence_kind": "external_revision",
                    "completion_evidence_revision": "release-42",
                    "completion_evidence_reason": "Approved release archive",
                    "external_revision_approved": 1,
                    "completion_commit_required": 1,
                    "completion_commit_hash": "release-42",
                },
            )
            seed_review_evidence(
                db,
                task["task_id"],
                target_kind="external_revision",
                target_value="release-42",
            )
            completed = edit(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertEqual(json.loads(completed.stdout)["data"]["task"]["status"], "done")

    def test_external_revision_rejects_whitespace_only_values_and_strips_saved_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)

            rejected = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "   ",
                "--completion-evidence-reason",
                "   ",
                "--external-revision-approved",
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(
                json.loads(rejected.stdout)["errors"][0]["code"],
                "completion_evidence_conflict",
            )
            self.assertEqual(completion_state(db, task["task_id"])["completion_evidence_kind"], "none")

            saved = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "  release-42  ",
                "--completion-evidence-reason",
                "  Approved release archive  ",
                "--external-revision-approved",
            )
            self.assertEqual(saved.returncode, 0, saved.stdout)
            state = completion_state(db, task["task_id"])
            self.assertEqual(state["completion_evidence_revision"], "release-42")
            self.assertEqual(state["completion_evidence_reason"], "Approved release archive")

    def test_pause_combined_with_external_evidence_keeps_approval_audit_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            started = edit(db, repo, task["task_id"], "--status", "in_progress")
            self.assertEqual(started.returncode, 0, started.stdout)

            paused = edit(
                db,
                repo,
                task["task_id"],
                "--status",
                "paused",
                "--pause-reason",
                "p" * 1000,
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-42",
                "--completion-evidence-reason",
                "Approved release archive",
                "--external-revision-approved",
            )

            self.assertEqual(paused.returncode, 0, paused.stdout)
            summary = json.loads(paused.stdout)["data"]["event"]["summary"]
            self.assertLessEqual(len(summary), 1000)
            self.assertIn("external revision approved as durable source outside target Git history", summary)

    def test_evidence_kind_switch_clears_stale_fields_and_conflicts_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            first = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-42",
                "--completion-evidence-reason",
                "Approved release archive",
                "--external-revision-approved",
            )
            self.assertEqual(first.returncode, 0, first.stdout)

            cleared = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "commit_not_required",
            )
            self.assertEqual(cleared.returncode, 0, cleared.stdout)
            self.assertEqual(completion_state(db, task["task_id"])["completion_evidence_reason"], "")
            self.assertEqual(completion_state(db, task["task_id"])["external_revision_approved"], 0)

            conflict = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "commit_not_required",
                "--completion-revision",
                "stale",
            )
            self.assertEqual(json.loads(conflict.stdout)["errors"][0]["code"], "completion_evidence_conflict")

    def test_external_reason_privacy_rejection_and_read_only_precede_storage_or_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            before_db = db.read_bytes()
            before_git = git_state(repo)

            rejected = edit(
                db,
                repo,
                task["task_id"],
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-42",
                "--completion-evidence-reason",
                "Authorization: Bearer secret",
                "--external-revision-approved",
            )
            self.assertEqual(json.loads(rejected.stdout)["errors"][0]["code"], "privacy_rejected")
            self.assertEqual(db.read_bytes(), before_db)

            read_only = edit(
                db,
                repo,
                task["task_id"],
                "--read-only",
                "--completion-evidence-kind",
                "git_commit",
                "--completion-revision",
                "missing",
            )
            self.assertEqual(json.loads(read_only.stdout)["errors"][0]["code"], "invalid_argument")
            self.assertEqual(db.read_bytes(), before_db)
            self.assertEqual(git_state(repo), before_git)

    def test_new_evidence_text_fields_reject_raw_output_stack_and_diff_without_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            before = db.read_bytes()
            cases = (
                ("stdout: build failed", "Approved archive"),
                ("release-42", "Log output:\nfailed line"),
                ("release-42", "Traceback (most recent call last):\n  failure"),
                ("release-42", "diff --git a/file b/file\n@@ -1 +1 @@\n-old\n+new"),
            )

            for revision, reason in cases:
                with self.subTest(revision=revision, reason=reason):
                    result = edit(
                        db,
                        repo,
                        task["task_id"],
                        "--completion-evidence-kind",
                        "external_revision",
                        "--completion-revision",
                        revision,
                        "--completion-evidence-reason",
                        reason,
                        "--external-revision-approved",
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(
                        json.loads(result.stdout)["errors"][0]["code"],
                        "privacy_rejected",
                    )
                    self.assertEqual(db.read_bytes(), before)

    def test_legacy_unverified_evidence_cannot_close_a_reopened_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE tasks
                       SET completion_evidence_kind = 'legacy_unverified',
                           completion_evidence_revision = 'historical-value',
                           completion_commit_required = 1,
                           completion_commit_hash = 'historical-value'
                     WHERE task_id = ?
                    """,
                    (task["task_id"],),
                )
                connection.commit()
            before = db.read_bytes()

            result = edit(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                json.loads(result.stdout)["errors"][0]["code"],
                "completion_evidence_conflict",
            )
            self.assertEqual(db.read_bytes(), before)

    def test_direct_whitespace_external_evidence_cannot_satisfy_done_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "taskgov.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE tasks
                       SET completion_evidence_kind = 'external_revision',
                           completion_evidence_revision = '   ',
                           completion_evidence_reason = '   ',
                           external_revision_approved = 1,
                           completion_commit_required = 1,
                           completion_commit_hash = '   '
                     WHERE task_id = ?
                    """,
                    (task["task_id"],),
                )
                connection.commit()
            before = db.read_bytes()

            result = edit(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                json.loads(result.stdout)["errors"][0]["code"],
                "project_state_unreadable",
            )
            self.assertEqual(db.read_bytes(), before)

    def test_git_resolver_rejects_leading_hyphen_before_subprocess_and_multiple_lines(self):
        with mock.patch("task_governance_tool.completion.subprocess.run") as run:
            with self.assertRaises(CompletionEvidenceError) as caught:
                resolve_git_commit(Path("repo"), "--help")
            self.assertEqual(caught.exception.code, "git_commit_not_found_or_ambiguous")
            run.assert_not_called()

        fake = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="a" * 40 + "\n" + "b" * 40 + "\n",
            stderr="",
        )
        with mock.patch("task_governance_tool.completion.subprocess.run", return_value=fake):
            with self.assertRaises(CompletionEvidenceError):
                resolve_git_commit(Path("repo"), "HEAD")

        success = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="a" * 40 + "\n",
            stderr="",
        )
        with mock.patch(
            "task_governance_tool.completion.subprocess.run",
            return_value=success,
        ) as run, mock.patch.dict(
            "task_governance_tool.completion.os.environ",
            {
                "PATH": "safe-path",
                "GIT_CONFIG_COUNT": "1",
                "GIT_INDEX_FILE": "untrusted-index",
            },
            clear=True,
        ):
            self.assertEqual(resolve_git_commit(Path("repo"), "abc1234"), "a" * 40)
            argv = run.call_args.args[0]
            environment = run.call_args.kwargs["env"]
            self.assertEqual(argv[-2:], ["--end-of-options", "abc1234^{commit}"])
            self.assertEqual(
                argv[1:3],
                [
                    "-c",
                    f"safe.directory={Path('repo').resolve(strict=False).as_posix()}",
                ],
            )
            self.assertEqual(
                argv[3:5],
                ["-C", str(Path("repo").resolve(strict=False))],
            )
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertEqual(environment["PATH"], "safe-path")
            self.assertNotIn("GIT_CONFIG_COUNT", environment)
            self.assertNotIn("GIT_INDEX_FILE", environment)
            self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
            self.assertEqual(environment["GIT_NO_LAZY_FETCH"], "1")
            self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
            self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")

    def test_done_revalidates_unresolvable_stored_git_evidence_without_writes(self):
        missing_commit = "f" * 40
        for target in ("completion", "review"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo, db = root / "repo", root / "tasks.sqlite"
                init_git_repo(repo)
                init_db(db, repo)
                task = add_task(db, repo)
                seed_review_evidence(db, task["task_id"])
                with closing(sqlite3.connect(db)) as connection:
                    if target == "completion":
                        connection.execute(
                            """
                            UPDATE tasks
                               SET completion_evidence_kind = 'git_commit',
                                   completion_evidence_revision = ?,
                                   completion_commit_required = 1,
                                   completion_commit_hash = ?
                             WHERE task_id = ?
                            """,
                            (missing_commit, missing_commit, task["task_id"]),
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE tasks
                               SET review_target_kind = 'git_commit',
                                   review_target_value = ?
                             WHERE task_id = ?
                            """,
                            (missing_commit, task["task_id"]),
                        )
                        connection.execute(
                            """
                            UPDATE review_receipts
                               SET target_kind = 'git_commit', target_value = ?
                             WHERE task_id = ?
                            """,
                            (missing_commit, task["task_id"]),
                        )
                    connection.commit()
                before_db = db.read_bytes()
                before_git = git_state(repo)
                evidence_args = () if target == "completion" else ("--commit-not-required",)

                result = edit(
                    db,
                    repo,
                    task["task_id"],
                    "--status",
                    "done",
                    "--verification-complete",
                    "--review-complete",
                    *evidence_args,
                )

                payload = json.loads(result.stdout)
                if target == "review":
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(
                        payload["errors"],
                        [{
                            "code": "project_state_unreadable",
                            "message": "project state could not be read safely",
                        }],
                    )
                else:
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        "git_commit_not_found_or_ambiguous",
                    )
                self.assertEqual(db.read_bytes(), before_db)
                self.assertEqual(git_state(repo), before_git)


if __name__ == "__main__":
    unittest.main()
