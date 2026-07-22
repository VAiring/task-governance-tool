import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
FINGERPRINT_A = "sha256:" + "a" * 64
FINGERPRINT_B = "sha256:" + "b" * 64


def run_taskgov(*args):
    return subprocess.run(
        [sys.executable, "scripts/taskgov.py", *args],
        cwd=SKILL_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def payload(result):
    return json.loads(result.stdout)


def init_db(db, repo):
    result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)


def init_git_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.name=TaskGov Test",
            "-c", "user.email=taskgov@example.invalid", "commit", "--quiet",
            "--allow-empty", "-m", "review target",
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def add_task(db, repo, *, tier=2, title="Review task"):
    result = run_taskgov(
        "task", "add", "--repo", str(repo), "--db", str(db),
        "--title", title, "--review-tier", str(tier), "--json",
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return payload(result)["data"]["task"]


def target_set(db, repo, task_id, revision=FINGERPRINT_A):
    return run_taskgov(
        "review", "target", "set", "--repo", str(repo), "--db", str(db),
        task_id, "--kind", "diff_fingerprint", "--revision", revision, "--json",
    )


def receipt_add(
    db,
    repo,
    task_id,
    reviewer,
    *,
    kind="independent",
    verdict="pass",
    summary="",
    approved=False,
):
    args = [
        "review", "receipt", "add", "--repo", str(repo), "--db", str(db),
        task_id, "--reviewer", reviewer, "--kind", kind, "--verdict", verdict,
    ]
    if summary:
        args.extend(["--summary", summary])
    if approved:
        args.append("--user-approved")
    args.append("--json")
    return run_taskgov(*args)


def done(db, repo, task_id):
    return run_taskgov(
        "task", "edit", "--repo", str(repo), "--db", str(db), task_id,
        "--status", "done", "--verification-complete", "--review-complete",
        "--commit-not-required", "--json",
    )


class ReviewEvidenceTests(unittest.TestCase):
    def test_git_review_target_is_validated_canonically_without_git_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            revision = init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            before = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain=v2", "--branch"],
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout
            result = run_taskgov(
                "review", "target", "set", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--kind", "git_commit", "--revision", revision[:12],
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(payload(result)["data"]["task"]["review_target_value"], revision)
            missing = run_taskgov(
                "review", "target", "set", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--kind", "git_commit", "--revision", "not-a-commit",
                "--json",
            )
            self.assertEqual(payload(missing)["errors"][0]["code"], "git_commit_not_found_or_ambiguous")
            blank = run_taskgov(
                "review", "target", "set", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--kind", "git_commit", "--revision", "   ", "--json",
            )
            self.assertEqual(payload(blank)["errors"][0]["code"], "git_commit_not_found_or_ambiguous")
            after = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain=v2", "--branch"],
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout
            self.assertEqual(after, before)

    def test_tier_two_requires_two_distinct_passes_for_current_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "state" / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)

            missing_target = done(db, repo, task["task_id"])
            self.assertEqual(payload(missing_target)["errors"][0]["code"], "review_target_required")
            self.assertEqual(target_set(db, repo, task["task_id"]).returncode, 0)
            self.assertEqual(receipt_add(db, repo, task["task_id"], "reviewer-a").returncode, 0)
            one_pass = done(db, repo, task["task_id"])
            self.assertEqual(payload(one_pass)["errors"][0]["code"], "review_receipts_insufficient")
            self.assertEqual(receipt_add(db, repo, task["task_id"], "reviewer-b").returncode, 0)
            completed = done(db, repo, task["task_id"])
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertEqual(payload(completed)["data"]["task"]["status"], "done")

    def test_same_reviewer_cannot_replace_or_contradict_receipt_in_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            target_set(db, repo, task["task_id"])
            receipt_add(db, repo, task["task_id"], "reviewer-a")
            duplicate = receipt_add(
                db, repo, task["task_id"], "reviewer-a",
                verdict="changes_requested", summary="A correction is required",
            )
            self.assertEqual(
                payload(duplicate)["errors"][0]["code"],
                "review_receipt_already_recorded",
            )
            target_set(db, repo, task["task_id"], FINGERPRINT_A)
            fresh = receipt_add(
                db, repo, task["task_id"], "reviewer-a",
                verdict="changes_requested", summary="A correction is required",
            )
            self.assertEqual(fresh.returncode, 0)
            self.assertEqual(payload(fresh)["data"]["receipt"]["target_generation"], 2)

    def test_target_reset_invalidates_old_receipts_even_for_same_value(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            target_set(db, repo, task["task_id"])
            receipt_add(db, repo, task["task_id"], "reviewer-a")
            receipt_add(db, repo, task["task_id"], "reviewer-b")
            target_set(db, repo, task["task_id"], FINGERPRINT_B)
            target_set(db, repo, task["task_id"], FINGERPRINT_A)
            blocked = done(db, repo, task["task_id"])
            self.assertEqual(payload(blocked)["errors"][0]["code"], "review_receipts_insufficient")
            shown = run_taskgov(
                "task", "show", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--json",
            )
            evidence = payload(shown)["data"]["review_evidence"]
            self.assertEqual(evidence["target"]["generation"], 3)
            self.assertEqual(evidence["gate"]["qualifying_independent_passes"], 0)
            self.assertEqual(evidence["counts"]["receipts_total"], 2)

    def test_high_finding_resolution_requires_new_target_and_fresh_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            target_set(db, repo, task["task_id"])
            first = receipt_add(db, repo, task["task_id"], "reviewer-a")
            receipt_add(db, repo, task["task_id"], "reviewer-b")
            receipt_id = payload(first)["data"]["receipt"]["review_receipt_id"]
            added = run_taskgov(
                "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--receipt-id", receipt_id, "--severity", "high",
                "--summary", "The migration needs a rollback guard", "--json",
            )
            finding_id = payload(added)["data"]["finding"]["review_finding_id"]
            self.assertEqual(payload(done(db, repo, task["task_id"]))["errors"][0]["code"], "review_finding_unresolved")
            resolved = run_taskgov(
                "review", "finding", "resolve", "--repo", str(repo), "--db", str(db),
                finding_id, "--resolution", "Rollback guard added and verified", "--json",
            )
            self.assertEqual(resolved.returncode, 0)
            still_stale = done(db, repo, task["task_id"])
            self.assertEqual(payload(still_stale)["errors"][0]["code"], "review_finding_unresolved")
            target_set(db, repo, task["task_id"], FINGERPRINT_B)
            receipt_add(db, repo, task["task_id"], "reviewer-a")
            receipt_add(db, repo, task["task_id"], "reviewer-b")
            self.assertEqual(done(db, repo, task["task_id"]).returncode, 0)

    def test_tier_fallback_combinations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            tier_one = add_task(db, repo, tier=1, title="Tier one")
            target_set(db, repo, tier_one["task_id"])
            fallback_one = receipt_add(
                db, repo, tier_one["task_id"], "self-one",
                kind="self_review_fallback", summary="Tooling unavailable; focused self-review passed",
            )
            self.assertEqual(fallback_one.returncode, 0)
            self.assertEqual(done(db, repo, tier_one["task_id"]).returncode, 0)

            tier_two = add_task(db, repo, tier=2, title="Tier two fallback")
            target_set(db, repo, tier_two["task_id"])
            no_approval = receipt_add(
                db, repo, tier_two["task_id"], "self-two",
                kind="self_review_fallback", summary="Strongest feasible self-review passed",
            )
            self.assertEqual(payload(no_approval)["errors"][0]["code"], "invalid_review_evidence")
            approved = receipt_add(
                db, repo, tier_two["task_id"], "self-two",
                kind="self_review_fallback", summary="Strongest feasible self-review passed",
                approved=True,
            )
            self.assertEqual(approved.returncode, 0)
            self.assertEqual(done(db, repo, tier_two["task_id"]).returncode, 0)

            tier_zero = add_task(db, repo, tier=0, title="Tier zero")
            target_set(db, repo, tier_zero["task_id"])
            not_required = receipt_add(
                db, repo, tier_zero["task_id"], "mechanical-review",
                kind="not_required", verdict="not_required",
                summary="Mechanical formatting only",
            )
            self.assertEqual(not_required.returncode, 0)
            self.assertEqual(done(db, repo, tier_zero["task_id"]).returncode, 0)

    def test_invalid_receipt_combination_matrix_writes_no_receipt_or_event(self):
        cases = (
            (1, "independent", "not_required", "", False),
            (1, "independent", "pass", "", True),
            (0, "self_review_fallback", "pass", "Self review", False),
            (1, "self_review_fallback", "pass", "Self review", True),
            (2, "self_review_fallback", "pass", "Self review", False),
            (1, "not_required", "not_required", "Mechanical", False),
            (2, "not_required", "not_required", "Mechanical", False),
            (0, "not_required", "not_required", "", False),
            (1, "independent", "changes_requested", "", False),
        )
        for index, (tier, kind, verdict, summary, approved) in enumerate(cases):
            with self.subTest(index=index, tier=tier, kind=kind, verdict=verdict), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo, db = root / "repo", root / "tasks.sqlite"
                repo.mkdir()
                init_db(db, repo)
                task = add_task(db, repo, tier=tier)
                target_set(db, repo, task["task_id"])
                with closing(sqlite3.connect(db)) as connection:
                    before = (
                        connection.execute("SELECT COUNT(1) FROM review_receipts").fetchone()[0],
                        connection.execute("SELECT COUNT(1) FROM task_events").fetchone()[0],
                    )
                result = receipt_add(
                    db,
                    repo,
                    task["task_id"],
                    f"invalid-reviewer-{index}",
                    kind=kind,
                    verdict=verdict,
                    summary=summary,
                    approved=approved,
                )
                self.assertEqual(payload(result)["errors"][0]["code"], "invalid_review_evidence")
                with closing(sqlite3.connect(db)) as connection:
                    after = (
                        connection.execute("SELECT COUNT(1) FROM review_receipts").fetchone()[0],
                        connection.execute("SELECT COUNT(1) FROM task_events").fetchone()[0],
                    )
                self.assertEqual(after, before)

    def test_historical_receipt_rejects_new_finding_and_open_low_is_nonblocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            target_set(db, repo, task["task_id"])
            old_receipt = receipt_add(db, repo, task["task_id"], "old-reviewer")
            old_receipt_id = payload(old_receipt)["data"]["receipt"]["review_receipt_id"]
            target_set(db, repo, task["task_id"], FINGERPRINT_B)
            historical = run_taskgov(
                "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--receipt-id", old_receipt_id, "--severity", "high",
                "--summary", "Historical receipt cannot attach here", "--json",
            )
            self.assertEqual(payload(historical)["errors"][0]["code"], "review_receipt_mismatch")
            first = receipt_add(db, repo, task["task_id"], "reviewer-a")
            receipt_add(db, repo, task["task_id"], "reviewer-b")
            receipt_id = payload(first)["data"]["receipt"]["review_receipt_id"]
            low = run_taskgov(
                "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--receipt-id", receipt_id, "--severity", "low",
                "--summary", "Optional wording improvement", "--json",
            )
            self.assertEqual(low.returncode, 0)
            self.assertEqual(done(db, repo, task["task_id"]).returncode, 0)

    def test_receipt_ownership_privacy_and_read_only_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            first = add_task(db, repo, title="First")
            second = add_task(db, repo, title="Second")
            target_set(db, repo, first["task_id"])
            target_set(db, repo, second["task_id"])
            receipt = receipt_add(db, repo, first["task_id"], "reviewer-a")
            receipt_id = payload(receipt)["data"]["receipt"]["review_receipt_id"]
            mismatch = run_taskgov(
                "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                second["task_id"], "--receipt-id", receipt_id, "--severity", "medium",
                "--summary", "Mismatch", "--json",
            )
            self.assertEqual(payload(mismatch)["errors"][0]["code"], "review_receipt_mismatch")
            private = receipt_add(
                db, repo, first["task_id"], "reviewer-secret",
                verdict="changes_requested", summary="Authorization: Bearer secret",
            )
            self.assertEqual(payload(private)["errors"][0]["code"], "privacy_rejected")
            before = db.read_bytes()
            read_only = run_taskgov(
                "review", "target", "set", "--repo", str(repo), "--db", str(db),
                first["task_id"], "--kind", "diff_fingerprint", "--revision", FINGERPRINT_B,
                "--read-only", "--json",
            )
            self.assertEqual(read_only.returncode, 1)
            self.assertEqual(db.read_bytes(), before)

    def test_task_show_bounds_receipts_and_findings_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            for generation in range(12):
                target_set(db, repo, task["task_id"], "sha256:" + f"{generation:064x}")
                receipt = receipt_add(db, repo, task["task_id"], f"reviewer-{generation}")
                receipt_id = payload(receipt)["data"]["receipt"]["review_receipt_id"]
                run_taskgov(
                    "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--receipt-id", receipt_id, "--severity", "low",
                    "--summary", f"Low finding {generation}", "--json",
                )
            before = db.read_bytes()
            shown = run_taskgov(
                "task", "show", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--json",
            )
            self.assertEqual(shown.returncode, 0)
            evidence = payload(shown)["data"]["review_evidence"]
            self.assertEqual(len(evidence["recent_receipts"]), 10)
            self.assertEqual(len(evidence["recent_findings"]), 10)
            self.assertEqual(evidence["counts"]["receipts_total"], 12)
            self.assertEqual(db.read_bytes(), before)

    def test_every_new_free_form_field_uses_privacy_and_size_guards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            private_target = run_taskgov(
                "review", "target", "set", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--kind", "external_revision",
                "--revision", "Authorization: Bearer secret", "--json",
            )
            self.assertEqual(payload(private_target)["errors"][0]["code"], "privacy_rejected")
            target_set(db, repo, task["task_id"])
            before_private_prompt = db.read_bytes()
            private_prompt = receipt_add(
                db, repo, task["task_id"], "prompt-reviewer",
                verdict="changes_requested",
                summary="Private reasoning:\nHidden internal reasoning text",
            )
            self.assertEqual(payload(private_prompt)["errors"][0]["code"], "privacy_rejected")
            self.assertEqual(db.read_bytes(), before_private_prompt)
            with closing(sqlite3.connect(db)) as connection:
                before_event_count = connection.execute("SELECT COUNT(1) FROM task_events").fetchone()[0]
            for index, private_text in enumerate((
                "Private reasoning: hidden internal reasoning",
                "System prompt: hidden instructions",
                "Review transcript: user said hidden content",
            )):
                with self.subTest(private_text=private_text):
                    before_one_line = db.read_bytes()
                    one_line = receipt_add(
                        db, repo, task["task_id"], f"private-reviewer-{index}",
                        verdict="changes_requested", summary=private_text,
                    )
                    self.assertEqual(payload(one_line)["errors"][0]["code"], "privacy_rejected")
                    self.assertEqual(db.read_bytes(), before_one_line)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(1) FROM task_events").fetchone()[0],
                    before_event_count,
                )
            benign = receipt_add(
                db, repo, task["task_id"], "benign-reviewer",
                verdict="changes_requested",
                summary="Private reasoning was not stored; the public finding is concise",
            )
            self.assertEqual(benign.returncode, 0, benign.stdout)
            private_reviewer = receipt_add(db, repo, task["task_id"], "Bearer sk-test")
            self.assertEqual(payload(private_reviewer)["errors"][0]["code"], "privacy_rejected")
            oversized_summary = receipt_add(
                db, repo, task["task_id"], "reviewer-a",
                verdict="changes_requested", summary="x" * 1001,
            )
            self.assertEqual(payload(oversized_summary)["errors"][0]["code"], "invalid_argument")
            receipt = receipt_add(db, repo, task["task_id"], "reviewer-a")
            receipt_id = payload(receipt)["data"]["receipt"]["review_receipt_id"]
            private_finding = run_taskgov(
                "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--receipt-id", receipt_id, "--severity", "low",
                "--summary", "stdout: secret output", "--json",
            )
            self.assertEqual(payload(private_finding)["errors"][0]["code"], "privacy_rejected")
            finding = run_taskgov(
                "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--receipt-id", receipt_id, "--severity", "low",
                "--summary", "Small wording issue", "--json",
            )
            finding_id = payload(finding)["data"]["finding"]["review_finding_id"]
            before_transcript = db.read_bytes()
            transcript_resolution = run_taskgov(
                "review", "finding", "resolve", "--repo", str(repo), "--db", str(db),
                finding_id, "--resolution", "Review transcript:\nRaw reviewer conversation", "--json",
            )
            self.assertEqual(payload(transcript_resolution)["errors"][0]["code"], "privacy_rejected")
            self.assertEqual(db.read_bytes(), before_transcript)
            private_resolution = run_taskgov(
                "review", "finding", "resolve", "--repo", str(repo), "--db", str(db),
                finding_id, "--resolution", "Traceback (most recent call last)", "--json",
            )
            self.assertEqual(payload(private_resolution)["errors"][0]["code"], "privacy_rejected")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM review_findings WHERE review_finding_id = ?",
                        (finding_id,),
                    ).fetchone()[0],
                    "open",
                )

    def test_concurrent_duplicate_reviewer_records_exactly_one_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            target_set(db, repo, task["task_id"])
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda _: receipt_add(db, repo, task["task_id"], "same-reviewer"),
                        range(2),
                    )
                )
            codes = [
                "ok" if result.returncode == 0 else payload(result)["errors"][0]["code"]
                for result in results
            ]
            self.assertCountEqual(codes, ["ok", "review_receipt_already_recorded"])
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
