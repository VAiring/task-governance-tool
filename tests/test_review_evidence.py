import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    initialize_taskgov_internal,
    remove_v18_evidence_ledger_for_test,
    run_taskgov_internal,
)
from tests.review_test_helpers import seed_review_evidence_connection


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
FINGERPRINT_A = "sha256:" + "a" * 64
FINGERPRINT_B = "sha256:" + "b" * 64

sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.reviews import (
        ReviewEvidenceError,
        read_review_evidence,
        set_git_snapshot_target,
    )
    from task_governance_tool.storage import (
        DATABASE_BUSY_MESSAGE,
        StorageError,
        apply_completion_evidence_bundle_migration,
        apply_evidence_ledger_capture_migration,
        connect_existing,
        connect_initialized,
        resolve_database_target,
        validate_current_database,
        validate_evidence_ledger_storage,
    )
    from task_governance_tool import tasks as task_service
    from task_governance_tool import reviews as review_service
    from task_governance_tool import storage as storage_service
    from task_governance_tool.tasks import (
        TaskValidationError,
        edit_task,
        list_tasks_for_viewer,
    )
finally:
    sys.path.pop(0)


def run_taskgov(*args):
    return run_taskgov_internal(*args)


def payload(result):
    return json.loads(result.stdout)


def init_db(db, repo):
    initialize_taskgov_internal(repo=repo, db=db)


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
    if kind != "not_required":
        args.extend(
            [
                "--reviewer-class", "human",
                "--model-state", "not_applicable",
                "--skill-state", "not_applicable",
                "--context-relation", "external_context",
                "--review-profile", "general",
                "--review-lens", "correctness",
                "--review-method", "review_packet_inspection",
            ]
        )
    args.append("--json")
    return run_taskgov(*args)


def done(db, repo, task_id):
    return run_taskgov(
        "task", "edit", "--repo", str(repo), "--db", str(db), task_id,
        "--status", "done", "--verification-complete", "--review-complete",
        "--commit-not-required", "--json",
    )


def database_target(db, repo):
    return resolve_database_target(
        repo=repo,
        db=db,
        script_path=SKILL_ROOT / "scripts" / "taskgov.py",
    )


def internal_git_snapshot_target(db, repo, task_id):
    target = database_target(db, repo)
    with closing(connect_initialized(target)) as connection:
        with connection:
            return set_git_snapshot_target(
                connection,
                target.project,
                task_id,
                database_target=target,
            )


def review_target_state(db, task_id):
    with closing(sqlite3.connect(db)) as connection:
        return connection.execute(
            """
            SELECT review_target_kind, review_target_value,
                   review_target_base_revision, review_target_generation
              FROM tasks WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()


def git_status(repo):
    return subprocess.run(
        [
            "git", "--no-optional-locks", "-C", str(repo),
            "status", "--porcelain=v2", "--branch",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def commit_staged(repo, message="completion"):
    subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.name=TaskGov Test",
            "-c", "user.email=taskgov@example.invalid", "commit", "--quiet",
            "-m", message,
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def target_and_two_passes(db, repo, task_id, kind, revision=None):
    target_args = [
        "review", "target", "set",
        "--repo", str(repo), "--db", str(db), task_id,
        "--kind", kind,
    ]
    if revision is not None:
        target_args.extend(["--revision", revision])
    target_args.append("--json")
    target = run_taskgov(*target_args)
    if target.returncode:
        raise AssertionError(target.stderr or target.stdout)
    for reviewer in ("reviewer-a", "reviewer-b"):
        receipt = receipt_add(db, repo, task_id, reviewer)
        if receipt.returncode:
            raise AssertionError(receipt.stderr or receipt.stdout)
    return payload(target)["data"]


def snapshot_target_and_two_passes(db, repo, task_id):
    return target_and_two_passes(
        db,
        repo,
        task_id,
        "git_snapshot",
    )


def current_changes_and_medium_finding(db, repo, task_id):
    target_set_result = target_set(db, repo, task_id)
    if target_set_result.returncode:
        raise AssertionError(target_set_result.stderr or target_set_result.stdout)
    for reviewer in ("reviewer-a", "reviewer-b"):
        result = receipt_add(db, repo, task_id, reviewer)
        if result.returncode:
            raise AssertionError(result.stderr or result.stdout)
    changes = receipt_add(
        db,
        repo,
        task_id,
        "reviewer-c",
        verdict="changes_requested",
        summary="A current correction is required",
    )
    if changes.returncode:
        raise AssertionError(changes.stderr or changes.stdout)
    receipt_id = payload(changes)["data"]["receipt"]["review_receipt_id"]
    finding = run_taskgov(
        "review", "finding", "add",
        "--repo", str(repo), "--db", str(db), task_id,
        "--receipt-id", receipt_id,
        "--severity", "medium",
        "--summary", "A current medium-severity finding remains open",
        "--json",
    )
    if finding.returncode:
        raise AssertionError(finding.stderr or finding.stdout)
    return (
        receipt_id,
        payload(finding)["data"]["finding"]["review_finding_id"],
    )


class ReviewEvidenceTests(unittest.TestCase):
    def assert_current_changes_and_medium_gate(self, evidence):
        self.assertEqual(
            evidence["counts"]["changes_requested_current_generation"],
            1,
        )
        self.assertEqual(evidence["counts"]["open_medium"], 1)
        self.assertFalse(evidence["gate"]["satisfied"])

    def test_zero_receipt_projection_uses_bounded_membership_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            repo.mkdir()
            init_db(db, repo)
            target = database_target(db, repo)

            for tier in (0, 1, 2):
                for target_is_set in (False, True):
                    with self.subTest(tier=tier, target_is_set=target_is_set):
                        task = add_task(
                            db,
                            repo,
                            tier=tier,
                            title=f"Zero receipt tier {tier} target {target_is_set}",
                        )
                        if target_is_set:
                            targeted = target_set(db, repo, task["task_id"])
                            self.assertEqual(targeted.returncode, 0, targeted.stdout)

                        with closing(connect_initialized(target)) as connection:
                            task_row = connection.execute(
                                "SELECT * FROM tasks WHERE task_id = ?",
                                (task["task_id"],),
                            ).fetchone()
                            traced_sql = []
                            connection.set_trace_callback(traced_sql.append)
                            evidence = read_review_evidence(
                                connection,
                                target.project.project_id,
                                task["task_id"],
                                validated_task=task_row,
                            )
                            connection.set_trace_callback(None)

                        expected_target = (
                            {
                                "kind": "diff_fingerprint",
                                "value": FINGERPRINT_A,
                                "generation": 1,
                            }
                            if target_is_set
                            else {"kind": "", "value": "", "generation": 0}
                        )
                        self.assertEqual(
                            evidence,
                            {
                                "target": expected_target,
                                "gate": {
                                    "review_tier": tier,
                                    "required_independent_passes": tier,
                                    "qualifying_independent_passes": 0,
                                    "fallback_kind": None,
                                    "satisfied": False,
                                },
                                "counts": {
                                    "receipts_total": 0,
                                    "receipts_current_generation": 0,
                                    "changes_requested_current_generation": 0,
                                    "open_high": 0,
                                    "open_medium": 0,
                                    "open_low": 0,
                                },
                                "blocking_findings": [],
                                "recent_receipts": [],
                                "recent_findings": [],
                            },
                        )
                        review_sql = [
                            " ".join(statement.lower().split())
                            for statement in traced_sql
                            if "review_receipts" in statement.lower()
                            or "review_findings" in statement.lower()
                            or "evidence_references" in statement.lower()
                        ]
                        self.assertEqual(len(review_sql), 4)
                        self.assertTrue(
                            all("values (" in statement for statement in review_sql)
                        )
                        self.assertTrue(
                            all(
                                " union " not in statement
                                and " group by " not in statement
                                and " order by " not in statement
                                for statement in review_sql
                            )
                        )
                        self.assertIn("from review_receipts", review_sql[0])
                        self.assertIn("'review_receipt'", review_sql[1])
                        self.assertIn(
                            "from review_findings as finding",
                            review_sql[2],
                        )
                        self.assertIn(
                            "left join review_receipts as receipt",
                            review_sql[2],
                        )
                        self.assertIn("'review_finding'", review_sql[3])
                        with closing(connect_initialized(target)) as plan_connection:
                            review_plans = [
                                [
                                    str(row[3]).lower()
                                    for row in plan_connection.execute(
                                        "EXPLAIN QUERY PLAN " + statement
                                    ).fetchall()
                                ]
                                for statement in review_sql
                            ]
                        for reference_plan in (
                            review_plans[1],
                            review_plans[3],
                        ):
                            self.assertTrue(
                                any(
                                    "search reference using" in detail
                                    and "idx_evidence_references_source"
                                    in detail
                                    for detail in reference_plan
                                ),
                                reference_plan,
                            )
                            self.assertFalse(
                                any(
                                    "scan reference" in detail
                                    for detail in reference_plan
                                ),
                                reference_plan,
                            )
                        finding_scan_steps = [
                            index
                            for index, detail in enumerate(review_plans[2])
                            if "scan finding" in detail
                        ]
                        receipt_search_steps = [
                            index
                            for index, detail in enumerate(review_plans[2])
                            if "search receipt using" in detail
                            and "sqlite_autoindex_review_receipts_1"
                            in detail
                        ]
                        self.assertEqual(
                            len(finding_scan_steps),
                            1,
                            review_plans[2],
                        )
                        self.assertTrue(
                            receipt_search_steps,
                            review_plans[2],
                        )
                        self.assertLess(
                            finding_scan_steps[0],
                            receipt_search_steps[0],
                            review_plans[2],
                        )

    def test_review_git_preflight_rejects_an_existing_database_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            target = database_target(db, repo)

            cases = (
                ("git_commit", "HEAD", "resolve_git_commit"),
                ("git_snapshot", None, "observe_staged_git_manifest"),
            )
            for kind, revision, patched_name in cases:
                with self.subTest(kind=kind):
                    with closing(connect_initialized(target)) as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        with mock.patch.object(
                            review_service,
                            patched_name,
                            side_effect=AssertionError(
                                "Git must not run inside the database transaction"
                            ),
                        ) as git_mock:
                            with self.assertRaises(
                                task_service.TaskRepositoryError
                            ) as raised:
                                review_service.set_requested_review_target(
                                    connection,
                                    target.project,
                                    task["task_id"],
                                    kind=kind,
                                    revision=revision,
                                    database_target=target,
                                )
                        connection.rollback()
                    self.assertEqual(raised.exception.code, "internal_error")
                    self.assertEqual(git_mock.call_count, 0)

            self.assertEqual(
                review_target_state(db, task["task_id"]),
                ("", "", "", 0),
            )
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    1,
                )

    def test_task_git_preflight_rejects_an_existing_database_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_git_repo(repo)
            init_db(db, repo)
            task = add_task(db, repo)
            target = database_target(db, repo)

            with closing(connect_initialized(target)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                with mock.patch.object(
                    task_service,
                    "completion_evidence_values",
                    side_effect=AssertionError(
                        "Git must not run inside the database transaction"
                    ),
                ) as git_mock:
                    with self.assertRaises(
                        task_service.TaskRepositoryError
                    ) as raised:
                        edit_task(
                            connection,
                            target.project,
                            task["task_id"],
                            completion_evidence_kind="git_commit",
                            completion_revision="HEAD",
                            database_target=target,
                        )
                connection.rollback()

            self.assertEqual(raised.exception.code, "internal_error")
            self.assertEqual(git_mock.call_count, 0)
            with closing(sqlite3.connect(db)) as connection:
                stored = connection.execute(
                    """
                    SELECT status, completion_evidence_kind,
                           completion_evidence_revision
                      FROM tasks
                     WHERE task_id = ?
                    """,
                    (task["task_id"],),
                ).fetchone()
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]
            self.assertEqual(stored, ("ready", "none", ""))
            self.assertEqual(event_count, 1)

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

    def test_current_receipt_validation_is_chunked_before_gate_aggregation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "state" / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id)
            receipt_add(db, repo, task_id, "reviewer-a")
            receipt_add(db, repo, task_id, "reviewer-b")
            target = database_target(db, repo)
            observed_chunks: list[set[str]] = []
            real_validate = review_service.validate_selected_task_receipt_evidence

            def observe_validation(connection, **kwargs):
                observed_chunks.append(set(kwargs["review_receipt_ids"]))
                return real_validate(connection, **kwargs)

            with (
                closing(connect_initialized(target)) as connection,
                mock.patch.object(
                    review_service,
                    "COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE",
                    1,
                ),
                mock.patch.object(
                    review_service,
                    "validate_selected_task_receipt_evidence",
                    side_effect=observe_validation,
                ),
            ):
                evidence = read_review_evidence(
                    connection,
                    target.project.project_id,
                    task_id,
                )

            self.assertTrue(evidence["gate"]["satisfied"])
            self.assertEqual(
                [len(chunk) for chunk in observed_chunks],
                [1, 1, 1, 1],
            )

    def test_v17_review_inventory_rejects_blob_source_aliases(self):
        private_marker = "Authorization: Bearer legacy-owner-secret"
        cases = (
            "receipt_task_id",
            "receipt_project_id",
            "finding_parent_id",
        )
        with tempfile.TemporaryDirectory() as temp:
            for case in cases:
                with self.subTest(case=case):
                    root = Path(temp) / case
                    repo, db = root / "repo", root / "tasks.sqlite"
                    repo.mkdir(parents=True)
                    init_db(db, repo)
                    task = add_task(db, repo)
                    task_id = task["task_id"]
                    receipt_id, finding_id = current_changes_and_medium_finding(
                        db,
                        repo,
                        task_id,
                    )
                    target = database_target(db, repo)
                    with closing(connect_initialized(target)) as connection:
                        remove_v18_evidence_ledger_for_test(connection)
                        task_row = connection.execute(
                            "SELECT * FROM tasks WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()
                        self.assert_current_changes_and_medium_gate(
                            read_review_evidence(
                                connection,
                                target.project.project_id,
                                task_id,
                                validated_task=task_row,
                                source_schema_version=17,
                            )
                        )
                        connection.execute("PRAGMA foreign_keys = OFF")
                        if case == "receipt_task_id":
                            connection.execute(
                                "UPDATE review_receipts "
                                "SET task_id = CAST(? AS BLOB) "
                                "WHERE review_receipt_id = ?",
                                (task_id, receipt_id),
                            )
                        elif case == "receipt_project_id":
                            connection.execute(
                                "UPDATE review_receipts SET project_id = ? "
                                "WHERE review_receipt_id = ?",
                                (private_marker, receipt_id),
                            )
                        else:
                            connection.execute(
                                "UPDATE review_findings "
                                "SET review_receipt_id = CAST(? AS BLOB) "
                                "WHERE review_finding_id = ?",
                                (receipt_id, finding_id),
                            )
                        connection.commit()
                        connection.execute("PRAGMA foreign_keys = ON")
                        before = db.read_bytes()
                        with (
                            mock.patch.object(
                                review_service,
                                "COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE",
                                1,
                            ),
                            self.assertRaises(StorageError) as failure,
                        ):
                            read_review_evidence(
                                connection,
                                target.project.project_id,
                                task_id,
                                validated_task=task_row,
                                source_schema_version=17,
                            )

                        self.assertEqual(
                            failure.exception.code,
                            "evidence_ledger_inconsistent",
                        )
                        self.assertEqual(
                            failure.exception.message,
                            "stored evidence ledger is inconsistent",
                        )
                        self.assertNotIn(
                            private_marker,
                            str(failure.exception),
                        )
                        self.assertEqual(db.read_bytes(), before)

    def _create_v17_legacy_finding_fixture(
        self,
        root,
        *,
        include_foreign_task=False,
    ):
        repo, db = root / "repo", root / "tasks.sqlite"
        repo.mkdir(parents=True)
        init_db(db, repo)
        task_id = add_task(db, repo)["task_id"]
        foreign_task_id = (
            add_task(db, repo, title="Foreign alias task")["task_id"]
            if include_foreign_task
            else None
        )
        target = database_target(db, repo)
        with closing(connect_initialized(target)) as connection:
            remove_v18_evidence_ledger_for_test(connection)
            for selected_task_id in (
                task_id,
                *((foreign_task_id,) if foreign_task_id is not None else ()),
            ):
                seed_review_evidence_connection(
                    connection,
                    selected_task_id,
                    repo_path=repo,
                )
            legacy_receipt_id = str(
                connection.execute(
                    "SELECT review_receipt_id FROM review_receipts "
                    "WHERE task_id = ? ORDER BY review_receipt_id LIMIT 1",
                    (task_id,),
                ).fetchone()[0]
            )
            legacy_finding_id = "tg_review_finding_" + ("1" * 16)
            connection.execute(
                """
                INSERT INTO review_findings(
                  review_finding_id, review_receipt_id, severity, status,
                  summary, resolution_summary, created_at, resolved_at
                ) VALUES (
                  ?, ?, 'medium', 'open',
                  'Legacy blocker must remain visible', '',
                  '2026-07-22T00:00:00Z', NULL
                )
                """,
                (legacy_finding_id, legacy_receipt_id),
            )
            connection.commit()
            foreign_receipt_id = (
                str(
                    connection.execute(
                        "SELECT review_receipt_id FROM review_receipts "
                        "WHERE task_id = ? ORDER BY review_receipt_id LIMIT 1",
                        (foreign_task_id,),
                    ).fetchone()[0]
                )
                if foreign_task_id is not None
                else None
            )
        return (
            repo,
            db,
            target,
            task_id,
            legacy_receipt_id,
            legacy_finding_id,
            foreign_receipt_id,
        )

    def _set_current_two_passes(self, db, repo, task_id):
        current_target = target_set(db, repo, task_id, FINGERPRINT_B)
        self.assertEqual(
            current_target.returncode,
            0,
            current_target.stdout,
        )
        for reviewer in ("current-reviewer-a", "current-reviewer-b"):
            receipt = receipt_add(db, repo, task_id, reviewer)
            self.assertEqual(receipt.returncode, 0, receipt.stdout)

    def _assert_finding_public_failure(
        self,
        *,
        db,
        repo,
        task_id,
        finding_id,
        include_write=False,
    ):
        commands = [
            (
                "task", "show", "--repo", str(repo), "--db", str(db),
                task_id, "--json",
            ),
            (
                "task", "complete", "--repo", str(repo), "--db", str(db),
                task_id, "--verification-complete", "--review-complete",
                "--commit-not-required", "--check", "--read-only", "--json",
            ),
            (
                "review", "finding", "resolve", "--repo", str(repo),
                "--db", str(db), finding_id, "--resolution", "Resolved",
                "--json",
            ),
        ]
        if include_write:
            commands.insert(
                2,
                (
                    "task", "complete", "--repo", str(repo), "--db",
                    str(db), task_id, "--verification-complete",
                    "--review-complete", "--commit-not-required", "--json",
                ),
            )
        before = db.read_bytes()
        for command in commands:
            result = run_taskgov(*command)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(
                payload(result)["errors"][0]["code"],
                "evidence_ledger_inconsistent",
            )
            self.assertEqual(db.read_bytes(), before)

    def _corrupt_finding_parent(self, db, finding_id, *, as_blob=False):
        missing_receipt_id = "tg_review_receipt_" + ("0" * 16)
        stored_receipt_id = (
            sqlite3.Binary(missing_receipt_id.encode("utf-8"))
            if as_blob
            else missing_receipt_id
        )
        with closing(sqlite3.connect(db)) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "UPDATE review_findings SET review_receipt_id = ? "
                "WHERE review_finding_id = ?",
                (stored_receipt_id, finding_id),
            )
            connection.commit()
            violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            self.assertTrue(
                any(row[0] == "review_findings" for row in violations)
            )
            self.assertEqual(
                connection.execute(
                    "SELECT severity, status, typeof(review_receipt_id) "
                    "FROM review_findings "
                    "WHERE review_finding_id = ?",
                    (finding_id,),
                ).fetchone(),
                ("medium", "open", "blob" if as_blob else "text"),
            )

    def test_v17_dangling_finding_parent_fails_read_and_migration_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            (
                _repo,
                db,
                target,
                task_id,
                _receipt_id,
                finding_id,
                _foreign_receipt_id,
            ) = self._create_v17_legacy_finding_fixture(Path(temp))
            self._corrupt_finding_parent(db, finding_id)
            before = db.read_bytes()

            with closing(connect_existing(db)) as connection:
                task_row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                changes_before = connection.total_changes
                with self.assertRaises(StorageError) as failure:
                    read_review_evidence(
                        connection,
                        target.project.project_id,
                        task_id,
                        validated_task=task_row,
                        source_schema_version=17,
                    )
                self.assertEqual(
                    failure.exception.code,
                    "evidence_ledger_inconsistent",
                )
                self.assertFalse(connection.in_transaction)
                self.assertEqual(connection.total_changes, changes_before)
                with self.assertRaises(StorageError) as migration_failure:
                    apply_evidence_ledger_capture_migration(connection)
                self.assertEqual(
                    migration_failure.exception.code,
                    "project_state_unreadable",
                )
                self.assertFalse(connection.in_transaction)
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    17,
                )
            self.assertEqual(db.read_bytes(), before)

    def test_migrated_dangling_finding_parent_fails_public_gate_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            (
                repo,
                db,
                target,
                task_id,
                receipt_id,
                finding_id,
                _foreign_receipt_id,
            ) = self._create_v17_legacy_finding_fixture(Path(temp))
            with closing(connect_existing(db)) as connection:
                apply_evidence_ledger_capture_migration(connection)
                apply_completion_evidence_bundle_migration(connection)
                self.assertEqual(
                    connection.execute(
                        "SELECT review_provenance_basis_version "
                        "FROM review_receipts WHERE review_receipt_id = ?",
                        (receipt_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM evidence_references "
                        "WHERE source_id IN (?, ?)",
                        (receipt_id, finding_id),
                    ).fetchone()[0],
                    0,
                )
            self._set_current_two_passes(db, repo, task_id)

            with closing(connect_existing(db)) as connection:
                task_row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                evidence = read_review_evidence(
                    connection,
                    target.project.project_id,
                    task_id,
                    validated_task=task_row,
                )
                self.assertEqual(evidence["counts"]["open_medium"], 1)
                self.assertFalse(evidence["gate"]["satisfied"])

            self._corrupt_finding_parent(db, finding_id)
            before = db.read_bytes()
            with closing(connect_existing(db)) as connection:
                task_row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                changes_before = connection.total_changes
                for validator in (
                    lambda: read_review_evidence(
                        connection,
                        target.project.project_id,
                        task_id,
                        validated_task=task_row,
                    ),
                    lambda: validate_evidence_ledger_storage(connection),
                ):
                    with self.assertRaises(StorageError) as failure:
                        validator()
                    self.assertEqual(
                        failure.exception.code,
                        "evidence_ledger_inconsistent",
                    )
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(connection.total_changes, changes_before)
            self.assertEqual(db.read_bytes(), before)

            self._corrupt_finding_parent(db, finding_id, as_blob=True)
            self._assert_finding_public_failure(
                db=db,
                repo=repo,
                task_id=task_id,
                finding_id=finding_id,
                include_write=True,
            )

    def test_review_finding_parent_alias_collisions_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            for mode in ("missing-exact", "duplicate-candidate"):
                with self.subTest(mode=mode):
                    root = Path(temp) / mode
                    (
                        repo,
                        db,
                        target,
                        task_id,
                        selected_receipt_id,
                        finding_id,
                        foreign_receipt_id,
                    ) = self._create_v17_legacy_finding_fixture(
                        root,
                        include_foreign_task=True,
                    )
                    if foreign_receipt_id is None:
                        raise AssertionError("foreign receipt fixture is missing")
                    with closing(connect_existing(db)) as connection:
                        apply_evidence_ledger_capture_migration(connection)
                        apply_completion_evidence_bundle_migration(connection)
                    self._set_current_two_passes(db, repo, task_id)

                    alias_id = (
                        "tg_review_receipt_" + ("3" * 16)
                        if mode == "missing-exact"
                        else selected_receipt_id
                    )
                    with closing(sqlite3.connect(db)) as connection:
                        connection.execute("PRAGMA foreign_keys = OFF")
                        if mode == "missing-exact":
                            connection.execute(
                                "UPDATE review_findings "
                                "SET review_receipt_id = ? "
                                "WHERE review_finding_id = ?",
                                (alias_id, finding_id),
                            )
                        connection.execute(
                            "UPDATE review_receipts "
                            "SET review_receipt_id = ? "
                            "WHERE review_receipt_id = ?",
                            (
                                sqlite3.Binary(alias_id.encode("utf-8")),
                                foreign_receipt_id,
                            ),
                        )
                        connection.commit()
                        candidates = connection.execute(
                            "SELECT typeof(review_receipt_id), task_id "
                            "FROM review_receipts "
                            "WHERE review_receipt_id IN (?, CAST(? AS BLOB))",
                            (alias_id, alias_id),
                        ).fetchall()
                        self.assertEqual(
                            {row[0] for row in candidates},
                            (
                                {"blob"}
                                if mode == "missing-exact"
                                else {"text", "blob"}
                            ),
                        )

                    before = db.read_bytes()
                    with closing(connect_existing(db)) as connection:
                        task_row = connection.execute(
                            "SELECT * FROM tasks WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()
                        changes_before = connection.total_changes
                        with self.assertRaises(StorageError) as failure:
                            read_review_evidence(
                                connection,
                                target.project.project_id,
                                task_id,
                                validated_task=task_row,
                            )
                        self.assertEqual(
                            failure.exception.code,
                            "evidence_ledger_inconsistent",
                        )
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(
                            connection.total_changes,
                            changes_before,
                        )
                    self.assertEqual(db.read_bytes(), before)
                    self._assert_finding_public_failure(
                        db=db,
                        repo=repo,
                        task_id=task_id,
                        finding_id=finding_id,
                    )

    def test_invalid_finding_domain_values_cannot_escape_source_stream(self):
        cases = (
            ("status", "critical"),
            ("severity", 1.5),
        )
        with tempfile.TemporaryDirectory() as temp:
            for source_schema_version in (17, 18):
                for field, invalid_value in cases:
                    with self.subTest(
                        source_schema_version=source_schema_version,
                        field=field,
                    ):
                        root = (
                            Path(temp)
                            / f"v{source_schema_version}-{field}"
                        )
                        repo, db = root / "repo", root / "tasks.sqlite"
                        repo.mkdir(parents=True)
                        init_db(db, repo)
                        task = add_task(db, repo)
                        task_id = task["task_id"]
                        targeted = target_set(db, repo, task_id)
                        self.assertEqual(
                            targeted.returncode,
                            0,
                            targeted.stdout,
                        )
                        first = receipt_add(
                            db,
                            repo,
                            task_id,
                            "reviewer-a",
                        )
                        self.assertEqual(first.returncode, 0, first.stdout)
                        second = receipt_add(
                            db,
                            repo,
                            task_id,
                            "reviewer-b",
                        )
                        self.assertEqual(second.returncode, 0, second.stdout)
                        receipt_id = payload(first)["data"]["receipt"][
                            "review_receipt_id"
                        ]
                        added = run_taskgov(
                            "review", "finding", "add",
                            "--repo", str(repo), "--db", str(db), task_id,
                            "--receipt-id", receipt_id,
                            "--severity", "medium",
                            "--summary", "This finding must remain visible",
                            "--json",
                        )
                        self.assertEqual(added.returncode, 0, added.stdout)
                        finding_id = payload(added)["data"]["finding"][
                            "review_finding_id"
                        ]
                        target = database_target(db, repo)

                        with closing(connect_initialized(target)) as connection:
                            if source_schema_version == 17:
                                remove_v18_evidence_ledger_for_test(connection)
                            else:
                                trigger_sql = connection.execute(
                                    "SELECT sql FROM sqlite_master "
                                    "WHERE type = 'trigger' AND name = ?",
                                    ("trg_evidence_references_no_delete",),
                                ).fetchone()[0]
                                connection.execute(
                                    "DROP TRIGGER "
                                    "trg_evidence_references_no_delete"
                                )
                                deleted = connection.execute(
                                    "DELETE FROM evidence_references "
                                    "WHERE source_kind = 'review_finding' "
                                    "AND source_id = ?",
                                    (finding_id,),
                                )
                                self.assertEqual(deleted.rowcount, 1)
                                connection.execute(trigger_sql)
                                connection.commit()
                            connection.execute(
                                "PRAGMA ignore_check_constraints = ON"
                            )
                            if field == "status":
                                connection.execute(
                                    "UPDATE review_findings SET status = ? "
                                    "WHERE review_finding_id = ?",
                                    (invalid_value, finding_id),
                                )
                            else:
                                connection.execute(
                                    "UPDATE review_findings SET severity = ? "
                                    "WHERE review_finding_id = ?",
                                    (invalid_value, finding_id),
                                )
                            connection.commit()
                            connection.execute(
                                "PRAGMA ignore_check_constraints = OFF"
                            )
                            task_row = connection.execute(
                                "SELECT * FROM tasks WHERE task_id = ?",
                                (task_id,),
                            ).fetchone()
                            before = db.read_bytes()
                            with (
                                mock.patch.object(
                                    review_service,
                                    "COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE",
                                    1,
                                ),
                                self.assertRaises(StorageError) as failure,
                            ):
                                read_review_evidence(
                                    connection,
                                    target.project.project_id,
                                    task_id,
                                    validated_task=task_row,
                                    source_schema_version=(
                                        source_schema_version
                                    ),
                                )
                            self.assertEqual(
                                failure.exception.code,
                                "evidence_ledger_inconsistent",
                            )
                            self.assertEqual(db.read_bytes(), before)

                        if source_schema_version == 18:
                            blocked = done(db, repo, task_id)
                            self.assertEqual(
                                blocked.returncode,
                                2,
                                blocked.stdout,
                            )
                            self.assertEqual(
                                payload(blocked)["errors"][0]["code"],
                                "evidence_ledger_inconsistent",
                            )
                            self.assertEqual(db.read_bytes(), before)

    def test_v18_blob_hidden_blockers_fail_show_check_and_write_closed(self):
        private_marker = "Authorization: Bearer review-owner-secret"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            receipt_id, finding_id = current_changes_and_medium_finding(
                db,
                repo,
                task_id,
            )
            target = database_target(db, repo)
            with closing(connect_existing(db)) as connection:
                trigger_sql = connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'trigger' AND name = ?",
                    ("trg_evidence_references_no_update",),
                ).fetchone()[0]
                task_row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                self.assert_current_changes_and_medium_gate(
                    read_review_evidence(
                        connection,
                        target.project.project_id,
                        task_id,
                        validated_task=task_row,
                    )
                )
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "DROP TRIGGER trg_evidence_references_no_update"
                )
                connection.execute(
                    "UPDATE review_receipts "
                    "SET task_id = CAST(? AS BLOB), summary = ? "
                    "WHERE review_receipt_id = ?",
                    (task_id, private_marker, receipt_id),
                )
                connection.execute(
                    "UPDATE evidence_references "
                    "SET project_id = ?, task_id = CAST(? AS BLOB) "
                    "WHERE (source_kind = 'review_receipt' AND source_id = ?) "
                    "OR (source_kind = 'review_finding' AND source_id = ?)",
                    (
                        private_marker,
                        task_id,
                        receipt_id,
                        finding_id,
                    ),
                )
                connection.execute(trigger_sql)
                connection.commit()
                connection.execute("PRAGMA foreign_keys = ON")

                before = db.read_bytes()
                with (
                    mock.patch.object(
                        review_service,
                        "COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE",
                        1,
                    ),
                    self.assertRaises(StorageError) as direct_failure,
                ):
                    read_review_evidence(
                        connection,
                        target.project.project_id,
                        task_id,
                        validated_task=task_row,
                    )
                self.assertEqual(
                    direct_failure.exception.code,
                    "evidence_ledger_inconsistent",
                )
                self.assertEqual(db.read_bytes(), before)

            commands = (
                (
                    "task", "show", "--repo", str(repo), "--db", str(db),
                    task_id, "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--check", "--read-only", "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--json",
                ),
            )
            for command in commands:
                with self.subTest(command=command[1]):
                    result = run_taskgov(*command)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(
                        payload(result)["errors"][0]["code"],
                        "evidence_ledger_inconsistent",
                    )
                    self.assertNotIn(private_marker, result.stdout)
                    self.assertNotIn(private_marker, result.stderr)
                    self.assertEqual(db.read_bytes(), before)

    def test_v18_orphan_reference_blob_alias_is_not_omitted(self):
        private_marker = "Authorization: Bearer orphan-owner-secret"
        with tempfile.TemporaryDirectory() as temp:
            for source_kind in ("review_receipt", "review_finding"):
                with self.subTest(source_kind=source_kind):
                    root = Path(temp) / source_kind
                    repo, db = root / "repo", root / "tasks.sqlite"
                    repo.mkdir(parents=True)
                    init_db(db, repo)
                    task = add_task(db, repo)
                    task_id = task["task_id"]
                    current_changes_and_medium_finding(
                        db,
                        repo,
                        task_id,
                    )
                    target = database_target(db, repo)

                    with closing(connect_existing(db)) as connection:
                        task_row = connection.execute(
                            "SELECT * FROM tasks WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()
                        self.assert_current_changes_and_medium_gate(
                            read_review_evidence(
                                connection,
                                target.project.project_id,
                                task_id,
                                validated_task=task_row,
                            )
                        )
                        reference = dict(connection.execute(
                            "SELECT * FROM evidence_references "
                            "WHERE source_kind = ? LIMIT 1",
                            (source_kind,),
                        ).fetchone())
                        id_suffix = (
                            "ffffffffffffffff"
                            if source_kind == "review_receipt"
                            else "eeeeeeeeeeeeeeee"
                        )
                        reference.update(
                            evidence_reference_id=(
                                f"tg_evidence_reference_{id_suffix}"
                            ),
                            project_id=sqlite3.Binary(
                                target.project.project_id.encode("ascii")
                            ),
                            task_id=sqlite3.Binary(task_id.encode("ascii")),
                            source_state=private_marker,
                            source_id=f"tg_{source_kind}_{id_suffix}",
                        )
                        columns = tuple(reference)
                        connection.execute("PRAGMA foreign_keys = OFF")
                        connection.execute(
                            f"INSERT INTO evidence_references "
                            f"({', '.join(columns)}) VALUES "
                            f"({', '.join('?' for _ in columns)})",
                            tuple(reference[column] for column in columns),
                        )
                        connection.commit()
                        connection.execute("PRAGMA foreign_keys = ON")

                        before = db.read_bytes()
                        with (
                            mock.patch.object(
                                review_service,
                                "COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE",
                                1,
                            ),
                            self.assertRaises(StorageError) as direct_failure,
                        ):
                            read_review_evidence(
                                connection,
                                target.project.project_id,
                                task_id,
                                validated_task=task_row,
                            )
                        self.assertEqual(
                            direct_failure.exception.code,
                            "evidence_ledger_inconsistent",
                        )
                        self.assertEqual(db.read_bytes(), before)

                    commands = (
                        (
                            "task", "show", "--repo", str(repo),
                            "--db", str(db), task_id, "--json",
                        ),
                        (
                            "task", "complete", "--repo", str(repo),
                            "--db", str(db), task_id,
                            "--verification-complete", "--review-complete",
                            "--commit-not-required", "--check",
                            "--read-only", "--json",
                        ),
                        (
                            "task", "complete", "--repo", str(repo),
                            "--db", str(db), task_id,
                            "--verification-complete", "--review-complete",
                            "--commit-not-required", "--json",
                        ),
                    )
                    for command in commands:
                        result = run_taskgov(*command)
                        self.assertEqual(result.returncode, 2, result.stdout)
                        self.assertEqual(
                            payload(result)["errors"][0]["code"],
                            "project_state_unreadable",
                        )
                        self.assertNotIn(private_marker, result.stdout)
                        self.assertNotIn(private_marker, result.stderr)
                        self.assertEqual(db.read_bytes(), before)

    def test_foreign_project_reference_is_outside_selected_review_owner(self):
        private_marker = "Authorization: Bearer foreign-owner-secret"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            targeted = target_set(db, repo, task_id)
            self.assertEqual(targeted.returncode, 0, targeted.stdout)
            recorded = receipt_add(db, repo, task_id, "reviewer-a")
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            target = database_target(db, repo)

            with closing(connect_existing(db)) as connection:
                reference = dict(connection.execute(
                    "SELECT * FROM evidence_references "
                    "WHERE source_kind = 'review_receipt' LIMIT 1"
                ).fetchone())
                reference.update(
                    evidence_reference_id=(
                        "tg_evidence_reference_dddddddddddddddd"
                    ),
                    project_id=private_marker,
                    source_id="tg_review_receipt_dddddddddddddddd",
                )
                columns = tuple(reference)
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    f"INSERT INTO evidence_references "
                    f"({', '.join(columns)}) VALUES "
                    f"({', '.join('?' for _ in columns)})",
                    tuple(reference[column] for column in columns),
                )
                connection.commit()
                connection.execute("PRAGMA foreign_keys = ON")
                task_row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                before = db.read_bytes()
                evidence = read_review_evidence(
                    connection,
                    target.project.project_id,
                    task_id,
                    validated_task=task_row,
                )

            self.assertEqual(evidence["counts"]["receipts_total"], 1)
            self.assertNotIn(
                private_marker,
                json.dumps(evidence, sort_keys=True),
            )
            self.assertEqual(db.read_bytes(), before)

    def test_current_changes_requested_requires_new_target_and_fresh_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id)
            receipt_add(db, repo, task_id, "reviewer-a")
            receipt_add(db, repo, task_id, "reviewer-b")
            receipt_add(
                db,
                repo,
                task_id,
                "reviewer-c",
                verdict="changes_requested",
                summary="A correction is still required",
            )
            shown = run_taskgov(
                "task", "show", "--repo", str(repo), "--db", str(db),
                task_id, "--json",
            )
            evidence = payload(shown)["data"]["review_evidence"]
            self.assertEqual(
                evidence["counts"]["changes_requested_current_generation"], 1
            )
            self.assertFalse(evidence["gate"]["satisfied"])
            before = db.read_bytes()

            blocked = done(db, repo, task_id)

            self.assertEqual(blocked.returncode, 1, blocked.stdout)
            self.assertEqual(
                payload(blocked)["errors"][0]["code"], "review_changes_requested"
            )
            self.assertEqual(db.read_bytes(), before)

            target_set(db, repo, task_id)
            receipt_add(db, repo, task_id, "reviewer-a")
            receipt_add(db, repo, task_id, "reviewer-b")
            refreshed = run_taskgov(
                "task", "show", "--repo", str(repo), "--db", str(db),
                task_id, "--json",
            )
            self.assertEqual(
                payload(refreshed)["data"]["review_evidence"]["counts"][
                    "changes_requested_current_generation"
                ],
                0,
            )
            self.assertEqual(done(db, repo, task_id).returncode, 0)

    def test_current_review_verdict_tamper_fails_show_check_and_complete_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id)
            receipt_add(db, repo, task_id, "reviewer-a")
            receipt_add(db, repo, task_id, "reviewer-b")
            receipt_add(
                db,
                repo,
                task_id,
                "reviewer-c",
                verdict="changes_requested",
                summary="A correction is required",
            )
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE review_receipts
                       SET verdict = 'pass'
                     WHERE reviewer_key = 'reviewer-c'
                    """
                )
                connection.commit()

            commands = (
                (
                    "task", "show", "--repo", str(repo), "--db", str(db),
                    task_id, "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--check", "--read-only", "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--json",
                ),
            )
            for command in commands:
                with self.subTest(command=command[1]):
                    result = run_taskgov(*command)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(
                        payload(result)["errors"][0]["code"],
                        "evidence_ledger_inconsistent",
                    )

    def test_current_review_target_tamper_cannot_hide_beyond_recent_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id, FINGERPRINT_A)
            target_set(db, repo, task_id, FINGERPRINT_B)
            receipt_add(
                db,
                repo,
                task_id,
                "blocking-reviewer",
                verdict="changes_requested",
                summary="A current correction is required",
            )
            for index in range(11):
                recorded = receipt_add(
                    db,
                    repo,
                    task_id,
                    f"passing-reviewer-{index}",
                )
                self.assertEqual(recorded.returncode, 0, recorded.stdout)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE review_receipts
                       SET target_value = ?, target_generation = 1
                     WHERE reviewer_key = 'blocking-reviewer'
                    """,
                    (FINGERPRINT_A,),
                )
                connection.commit()

            before = db.read_bytes()
            commands = (
                (
                    "task", "show", "--repo", str(repo), "--db", str(db),
                    task_id, "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--check", "--read-only", "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--json",
                ),
            )
            for command in commands:
                result = run_taskgov(*command)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(
                    payload(result)["errors"][0]["code"],
                    "evidence_ledger_inconsistent",
                )
                self.assertEqual(db.read_bytes(), before)

    def test_current_review_ownership_tamper_cannot_trigger_empty_shortcut(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id)
            receipt_add(db, repo, task_id, "reviewer-a")
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE review_receipts SET project_id = 'tampered-project'"
                )
                connection.commit()

            before = db.read_bytes()
            commands = (
                (
                    "task", "show", "--repo", str(repo), "--db", str(db),
                    task_id, "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--check", "--read-only", "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--json",
                ),
            )
            for command in commands:
                result = run_taskgov(*command)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(
                    payload(result)["errors"][0]["code"],
                    "project_state_unreadable",
                )
                self.assertEqual(db.read_bytes(), before)

    def test_finding_add_rejects_corrupt_parent_evidence_before_writes(self):
        cases = (
            "receipt",
            "provenance",
            "reference",
            "reference_binding",
            "parent_alias",
        )
        with tempfile.TemporaryDirectory() as temp:
            for case in cases:
                with self.subTest(case=case):
                    root = Path(temp) / case
                    repo, db = root / "repo", root / "tasks.sqlite"
                    repo.mkdir(parents=True)
                    init_db(db, repo)
                    task = add_task(db, repo)
                    task_id = task["task_id"]
                    target_set(db, repo, task_id)
                    recorded = receipt_add(db, repo, task_id, "reviewer-a")
                    self.assertEqual(recorded.returncode, 0, recorded.stdout)
                    receipt_id = payload(recorded)["data"]["receipt"][
                        "review_receipt_id"
                    ]
                    foreign_receipt_id = None
                    if case == "parent_alias":
                        foreign_task = add_task(
                            db,
                            repo,
                            title="Foreign review task",
                        )
                        target_set(db, repo, foreign_task["task_id"])
                        foreign_recorded = receipt_add(
                            db,
                            repo,
                            foreign_task["task_id"],
                            "foreign-reviewer",
                        )
                        self.assertEqual(
                            foreign_recorded.returncode,
                            0,
                            foreign_recorded.stdout,
                        )
                        foreign_receipt_id = payload(foreign_recorded)["data"][
                            "receipt"
                        ]["review_receipt_id"]
                    target = database_target(db, repo)
                    with closing(connect_initialized(target)) as connection:
                        if case == "receipt":
                            connection.execute(
                                "UPDATE review_receipts SET summary = ? "
                                "WHERE review_receipt_id = ?",
                                ("Tampered parent Receipt", receipt_id),
                            )
                        elif case == "provenance":
                            trigger_sql = connection.execute(
                                "SELECT sql FROM sqlite_master "
                                "WHERE type = 'trigger' AND name = ?",
                                ("trg_review_receipt_provenance_no_update",),
                            ).fetchone()[0]
                            connection.execute(
                                "DROP TRIGGER "
                                "trg_review_receipt_provenance_no_update"
                            )
                            connection.execute(
                                "UPDATE review_receipt_provenance "
                                "SET digest = ?",
                                ("sha256:" + "0" * 64,),
                            )
                            connection.execute(trigger_sql)
                        elif case == "reference":
                            trigger_sql = connection.execute(
                                "SELECT sql FROM sqlite_master "
                                "WHERE type = 'trigger' AND name = ?",
                                ("trg_evidence_references_no_delete",),
                            ).fetchone()[0]
                            connection.execute(
                                "DROP TRIGGER trg_evidence_references_no_delete"
                            )
                            deleted = connection.execute(
                                "DELETE FROM evidence_references "
                                "WHERE source_kind = 'review_receipt' "
                                "AND source_id = ?",
                                (receipt_id,),
                            )
                            self.assertEqual(deleted.rowcount, 1)
                            connection.execute(trigger_sql)
                        elif case == "reference_binding":
                            trigger_sql = connection.execute(
                                "SELECT sql FROM sqlite_master "
                                "WHERE type = 'trigger' AND name = ?",
                                ("trg_evidence_references_no_update",),
                            ).fetchone()[0]
                            connection.execute(
                                "DROP TRIGGER trg_evidence_references_no_update"
                            )
                            changed = connection.execute(
                                "UPDATE evidence_references "
                                "SET target_generation = target_generation + 1 "
                                "WHERE source_kind = 'review_receipt' "
                                "AND source_id = ?",
                                (receipt_id,),
                            )
                            self.assertEqual(changed.rowcount, 1)
                            connection.execute(trigger_sql)
                        else:
                            connection.execute("PRAGMA foreign_keys = OFF")
                            changed = connection.execute(
                                "UPDATE review_receipts "
                                "SET review_receipt_id = CAST(? AS BLOB) "
                                "WHERE review_receipt_id = ?",
                                (receipt_id, foreign_receipt_id),
                            )
                            self.assertEqual(changed.rowcount, 1)
                        connection.commit()
                        if case == "parent_alias":
                            connection.execute("PRAGMA foreign_keys = ON")
                            candidates = connection.execute(
                                "SELECT typeof(review_receipt_id) "
                                "FROM review_receipts "
                                "WHERE review_receipt_id IN "
                                "(?, CAST(? AS BLOB))",
                                (receipt_id, receipt_id),
                            ).fetchall()
                            self.assertEqual(
                                {row[0] for row in candidates},
                                {"text", "blob"},
                            )

                        before_bytes = db.read_bytes()
                        changes_before = connection.total_changes
                        counts_before = tuple(
                            connection.execute(
                                f"SELECT COUNT(*) FROM {table_name}"
                            ).fetchone()[0]
                            for table_name in (
                                "review_findings",
                                "evidence_references",
                                "task_events",
                            )
                        )
                        with self.assertRaises(StorageError) as failure:
                            with connection:
                                review_service.add_review_finding(
                                    connection,
                                    target.project,
                                    task_id,
                                    receipt_id=receipt_id,
                                    severity="medium",
                                    summary="Parent evidence must be valid",
                                    database_target=target,
                                )

                        self.assertEqual(
                            failure.exception.code,
                            "evidence_ledger_inconsistent",
                        )
                        self.assertEqual(
                            failure.exception.message,
                            "stored evidence ledger is inconsistent",
                        )
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(connection.total_changes, changes_before)
                        self.assertEqual(
                            tuple(
                                connection.execute(
                                    f"SELECT COUNT(*) FROM {table_name}"
                                ).fetchone()[0]
                                for table_name in (
                                    "review_findings",
                                    "evidence_references",
                                    "task_events",
                                )
                            ),
                            counts_before,
                        )
                        self.assertEqual(db.read_bytes(), before_bytes)

    def test_finding_parent_candidate_read_preserves_database_busy(self):
        for error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            with self.subTest(error_code=error_code):
                connection = mock.Mock()
                busy = sqlite3.OperationalError(
                    "Authorization: Bearer sensitive SQLite lock detail"
                )
                busy.sqlite_errorcode = error_code
                connection.execute.side_effect = busy

                with self.assertRaises(StorageError) as failure:
                    review_service._read_unique_owned_review_receipt(
                        connection,
                        project_id="project-a",
                        task_id="tg_task_" + "1" * 16,
                        receipt_id="tg_review_receipt_" + "2" * 16,
                    )

                self.assertEqual(failure.exception.code, "database_busy")
                self.assertEqual(
                    failure.exception.message,
                    DATABASE_BUSY_MESSAGE,
                )
                self.assertNotIn("Authorization", str(failure.exception))

    def test_review_evidence_inventory_preserves_database_busy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id)
            recorded = receipt_add(db, repo, task_id, "reviewer-a")
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            target = database_target(db, repo)
            before_bytes = db.read_bytes()

            for error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                with self.subTest(error_code=error_code):
                    busy = sqlite3.OperationalError(
                        "Authorization: Bearer sensitive inventory lock detail"
                    )
                    busy.sqlite_errorcode = error_code
                    with (
                        closing(connect_existing(db)) as connection,
                        mock.patch.object(
                            review_service,
                            "validate_selected_task_receipt_evidence",
                            side_effect=busy,
                        ),
                        self.assertRaises(StorageError) as failure,
                    ):
                        read_review_evidence(
                            connection,
                            target.project.project_id,
                            task_id,
                        )

                    self.assertEqual(failure.exception.code, "database_busy")
                    self.assertEqual(
                        failure.exception.message,
                        DATABASE_BUSY_MESSAGE,
                    )
                    self.assertNotIn("Authorization", str(failure.exception))
                    self.assertEqual(db.read_bytes(), before_bytes)

    def test_finding_add_selected_validation_preserves_database_busy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id)
            recorded = receipt_add(db, repo, task_id, "reviewer-a")
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            receipt_id = payload(recorded)["data"]["receipt"][
                "review_receipt_id"
            ]
            target = database_target(db, repo)
            before_bytes = db.read_bytes()

            for error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                with self.subTest(error_code=error_code):
                    busy = sqlite3.OperationalError(
                        "Authorization: Bearer sensitive validation lock detail"
                    )
                    busy.sqlite_errorcode = error_code
                    with closing(connect_initialized(target)) as connection:
                        changes_before = connection.total_changes
                        counts_before = tuple(
                            connection.execute(
                                f"SELECT COUNT(*) FROM {table_name}"
                            ).fetchone()[0]
                            for table_name in (
                                "review_findings",
                                "evidence_references",
                                "task_events",
                            )
                        )
                        with (
                            mock.patch.object(
                                storage_service,
                                "_selected_storage_rows_by_ids",
                                side_effect=busy,
                            ),
                            self.assertRaises(StorageError) as failure,
                        ):
                            with connection:
                                review_service.add_review_finding(
                                    connection,
                                    target.project,
                                    task_id,
                                    receipt_id=receipt_id,
                                    severity="medium",
                                    summary="Busy validation must not write",
                                    database_target=target,
                                )

                        self.assertEqual(
                            failure.exception.code,
                            "database_busy",
                        )
                        self.assertEqual(
                            failure.exception.message,
                            DATABASE_BUSY_MESSAGE,
                        )
                        self.assertNotIn(
                            "Authorization",
                            str(failure.exception),
                        )
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(
                            connection.total_changes,
                            changes_before,
                        )
                        self.assertEqual(
                            tuple(
                                connection.execute(
                                    f"SELECT COUNT(*) FROM {table_name}"
                                ).fetchone()[0]
                                for table_name in (
                                    "review_findings",
                                    "evidence_references",
                                    "task_events",
                                )
                            ),
                            counts_before,
                        )
                    self.assertEqual(db.read_bytes(), before_bytes)

    def test_selected_task_reference_inventory_preserves_database_busy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id)
            target = database_target(db, repo)
            before_bytes = db.read_bytes()

            for error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                with self.subTest(error_code=error_code):
                    busy = sqlite3.OperationalError(
                        "Authorization: Bearer sensitive Reference lock detail"
                    )
                    busy.sqlite_errorcode = error_code
                    with closing(connect_initialized(target)) as connection:
                        changes_before = connection.total_changes
                        with (
                            mock.patch.object(
                                storage_service,
                                "_validate_selected_reference_source_chunk",
                                side_effect=busy,
                            ),
                            self.assertRaises(StorageError) as failure,
                        ):
                            task_service.fetch_validated_current_task_row(
                                connection,
                                project_id=target.project.project_id,
                                task_id=task_id,
                            )

                        self.assertEqual(
                            failure.exception.code,
                            "database_busy",
                        )
                        self.assertEqual(
                            failure.exception.message,
                            DATABASE_BUSY_MESSAGE,
                        )
                        self.assertNotIn(
                            "Authorization",
                            str(failure.exception),
                        )
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(
                            connection.total_changes,
                            changes_before,
                        )
                    self.assertEqual(db.read_bytes(), before_bytes)

    def test_historical_finding_parent_ownership_tamper_cannot_hide_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id, FINGERPRINT_A)
            historical_receipt = receipt_add(
                db,
                repo,
                task_id,
                "historical-reviewer",
            )
            historical_receipt_id = payload(historical_receipt)["data"][
                "receipt"
            ]["review_receipt_id"]
            finding = run_taskgov(
                "review", "finding", "add",
                "--repo", str(repo), "--db", str(db), task_id,
                "--receipt-id", historical_receipt_id,
                "--severity", "high",
                "--summary", "Historical blocker must remain visible",
                "--json",
            )
            self.assertEqual(finding.returncode, 0, finding.stdout)
            target_set(db, repo, task_id, FINGERPRINT_B)
            receipt_add(db, repo, task_id, "current-reviewer-a")
            receipt_add(db, repo, task_id, "current-reviewer-b")

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE review_receipts
                       SET project_id = 'tampered-project'
                     WHERE review_receipt_id = ?
                    """,
                    (historical_receipt_id,),
                )
                connection.commit()

            before = db.read_bytes()
            commands = (
                (
                    "task", "show", "--repo", str(repo), "--db", str(db),
                    task_id, "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--check", "--read-only", "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--json",
                ),
            )
            for command in commands:
                result = run_taskgov(*command)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(
                    payload(result)["errors"][0]["code"],
                    "project_state_unreadable",
                )
                self.assertEqual(db.read_bytes(), before)

    def test_historical_finding_severity_tamper_cannot_hide_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id, FINGERPRINT_A)
            historical_receipt = receipt_add(
                db,
                repo,
                task_id,
                "historical-reviewer",
            )
            historical_receipt_id = payload(historical_receipt)["data"][
                "receipt"
            ]["review_receipt_id"]
            finding = run_taskgov(
                "review", "finding", "add",
                "--repo", str(repo), "--db", str(db), task_id,
                "--receipt-id", historical_receipt_id,
                "--severity", "high",
                "--summary", "Historical blocker severity is immutable evidence",
                "--json",
            )
            self.assertEqual(finding.returncode, 0, finding.stdout)
            finding_id = payload(finding)["data"]["finding"][
                "review_finding_id"
            ]
            target_set(db, repo, task_id, FINGERPRINT_B)
            receipt_add(db, repo, task_id, "current-reviewer-a")
            receipt_add(db, repo, task_id, "current-reviewer-b")

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE review_findings
                       SET severity = 'low'
                     WHERE review_finding_id = ?
                    """,
                    (finding_id,),
                )
                connection.commit()

            before = db.read_bytes()
            commands = (
                (
                    "task", "show", "--repo", str(repo), "--db", str(db),
                    task_id, "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--check", "--read-only", "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--json",
                ),
            )
            for command in commands:
                result = run_taskgov(*command)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(
                    payload(result)["errors"][0]["code"],
                    "evidence_ledger_inconsistent",
                )
                self.assertEqual(db.read_bytes(), before)

    def test_malformed_historical_finding_resolution_cannot_hide_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id, FINGERPRINT_A)
            historical_receipt = receipt_add(
                db,
                repo,
                task_id,
                "historical-reviewer",
            )
            historical_receipt_id = payload(historical_receipt)["data"][
                "receipt"
            ]["review_receipt_id"]
            finding = run_taskgov(
                "review", "finding", "add",
                "--repo", str(repo), "--db", str(db), task_id,
                "--receipt-id", historical_receipt_id,
                "--severity", "high",
                "--summary", "A resolved finding requires resolution evidence",
                "--json",
            )
            self.assertEqual(finding.returncode, 0, finding.stdout)
            finding_id = payload(finding)["data"]["finding"][
                "review_finding_id"
            ]
            target_set(db, repo, task_id, FINGERPRINT_B)
            receipt_add(db, repo, task_id, "current-reviewer-a")
            receipt_add(db, repo, task_id, "current-reviewer-b")

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE review_findings
                       SET status = 'resolved', resolution_summary = '',
                           resolved_at = NULL
                     WHERE review_finding_id = ?
                    """,
                    (finding_id,),
                )
                connection.commit()

            before = db.read_bytes()
            commands = (
                (
                    "task", "show", "--repo", str(repo), "--db", str(db),
                    task_id, "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--check", "--read-only", "--json",
                ),
                (
                    "task", "complete", "--repo", str(repo), "--db", str(db),
                    task_id, "--verification-complete", "--review-complete",
                    "--commit-not-required", "--json",
                ),
            )
            for command in commands:
                result = run_taskgov(*command)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(
                    payload(result)["errors"][0]["code"],
                    "evidence_ledger_inconsistent",
                )
                self.assertEqual(db.read_bytes(), before)

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
            self.assertIsNone(
                payload(not_required)["data"]["receipt"]["review_provenance"]
            )
            self.assertEqual(done(db, repo, tier_zero["task_id"]).returncode, 0)

    def test_capture_zero_target_rejects_new_review_receipt_as_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task_id = add_task(db, repo, tier=1)["task_id"]
            self.assertEqual(target_set(db, repo, task_id).returncode, 0)
            target = database_target(db, repo)

            with closing(connect_initialized(target)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        """
                        UPDATE tasks
                           SET review_target_capture_version = 0,
                               review_target_authority_snapshot_id = NULL,
                               review_target_acceptance_criterion_id = NULL,
                               review_target_verification_criterion_id = NULL,
                               review_target_artifact_manifest_id = NULL
                         WHERE task_id = ?
                        """,
                        (task_id,),
                    )
                    with self.assertRaises(ReviewEvidenceError) as raised:
                        review_service.add_review_receipt(
                            connection,
                            target.project,
                            task_id,
                            reviewer="legacy-reviewer",
                            kind="independent",
                            verdict="pass",
                            reviewer_class="human",
                            model_state="not_applicable",
                            skill_state="not_applicable",
                            review_profiles=["general"],
                            review_lenses=["correctness"],
                            context_relation="external_context",
                            review_methods=["review_packet_inspection"],
                        )
                    self.assertEqual(raised.exception.code, "evidence_basis_stale")
                finally:
                    connection.rollback()

            self.assertEqual(
                receipt_add(db, repo, task_id, "current-reviewer").returncode,
                0,
            )

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

    def test_done_task_rejects_all_review_writes_before_payload_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id)
            receipt_a = receipt_add(db, repo, task_id, "reviewer-a")
            receipt_add(db, repo, task_id, "reviewer-b")
            receipt_id = payload(receipt_a)["data"]["receipt"]["review_receipt_id"]
            finding = run_taskgov(
                "review",
                "finding",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--receipt-id",
                receipt_id,
                "--severity",
                "low",
                "--summary",
                "Non-blocking wording issue",
                "--json",
            )
            finding_id = payload(finding)["data"]["finding"]["review_finding_id"]
            self.assertEqual(done(db, repo, task_id).returncode, 0)

            valid_writes = (
                (
                    (
                        "review", "target", "set", "--repo", str(repo), "--db", str(db),
                        task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT_B,
                        "--json",
                    ),
                    {"task": None, "changed_fields": [], "event": None},
                ),
                (
                    (
                        "review", "receipt", "add", "--repo", str(repo), "--db", str(db),
                        task_id, "--reviewer", "reviewer-c", "--kind", "independent",
                        "--verdict", "pass", "--json",
                    ),
                    {"receipt": None, "event": None},
                ),
                (
                    (
                        "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                        task_id, "--receipt-id", receipt_id, "--severity", "low",
                        "--summary", "Another concise finding", "--json",
                    ),
                    {"finding": None, "event": None},
                ),
                (
                    (
                        "review", "finding", "resolve", "--repo", str(repo), "--db", str(db),
                        finding_id, "--resolution", "Corrected and verified", "--json",
                    ),
                    {"finding": None, "event": None},
                ),
            )
            for command, empty_data in valid_writes:
                with self.subTest(command=command[:4]):
                    before = db.read_bytes()
                    result = run_taskgov(*command)
                    body = payload(result)
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertEqual(
                        body["errors"][0]["code"],
                        "done_task_requires_reopen",
                    )
                    self.assertEqual(body["data"], empty_data)
                    self.assertEqual(db.read_bytes(), before)

            invalid_payloads = (
                (
                    "review", "target", "set", "--repo", str(repo), "--db", str(db),
                    task_id, "--kind", "unsupported", "--revision", "bad", "--json",
                ),
                (
                    "review", "receipt", "add", "--repo", str(repo), "--db", str(db),
                    task_id, "--reviewer", "reviewer-d", "--kind", "independent",
                    "--verdict", "unsupported", "--json",
                ),
                (
                    "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                    task_id, "--receipt-id", receipt_id, "--severity", "critical",
                    "--summary", "Invalid severity", "--json",
                ),
                (
                    "review", "finding", "resolve", "--repo", str(repo), "--db", str(db),
                    finding_id, "--resolution", "token=secret", "--json",
                ),
            )
            for command in invalid_payloads:
                with self.subTest(command=command[:4]):
                    before = db.read_bytes()
                    result = run_taskgov(*command)
                    self.assertEqual(
                        payload(result)["errors"][0]["code"],
                        "done_task_requires_reopen",
                    )
                    self.assertEqual(db.read_bytes(), before)

    def test_malformed_review_commands_remain_parse_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            malformed = (
                (
                    "review", "target", "set", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--kind", "diff_fingerprint", "--json",
                ),
                (
                    "review", "receipt", "add", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--reviewer", "reviewer", "--kind", "independent",
                    "--json",
                ),
                (
                    "review", "finding", "add", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--receipt-id", "receipt", "--severity", "low",
                    "--json",
                ),
                (
                    "review", "finding", "resolve", "--repo", str(repo), "--db", str(db),
                    "finding", "--json",
                ),
            )
            before = db.read_bytes()
            for command in malformed:
                with self.subTest(command=command[:4]):
                    result = run_taskgov(*command)
                    body = payload(result)
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertEqual(body["command"], "parse")
                    self.assertEqual(body["errors"][0]["code"], "invalid_argument")
                    self.assertEqual(body["data"], {})
                    self.assertEqual(db.read_bytes(), before)

    def test_reopened_tier_two_task_requires_fresh_independent_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task = add_task(db, repo)
            task_id = task["task_id"]
            target_set(db, repo, task_id)
            receipt_add(db, repo, task_id, "reviewer-a")
            receipt_add(db, repo, task_id, "reviewer-b")
            self.assertEqual(done(db, repo, task_id).returncode, 0)
            with closing(sqlite3.connect(db)) as connection:
                old_receipts = connection.execute(
                    "SELECT COUNT(*) FROM review_receipts WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
                old_generation = connection.execute(
                    "SELECT review_target_generation FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]

            reopened = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), task_id,
                "--status", "in_progress", "--reopen-reason", "Acceptance changed",
                "--json",
            )
            self.assertEqual(reopened.returncode, 0, reopened.stdout)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT review_target_generation FROM tasks WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()[0],
                    old_generation + 1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_receipts WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()[0],
                    old_receipts,
                )

            downgrade = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), task_id,
                "--review-tier", "1", "--review-tier-change-reason",
                "Trying to reuse old review", "--json",
            )
            self.assertEqual(
                payload(downgrade)["errors"][0]["code"],
                "review_tier_downgrade_forbidden",
            )
            self.assertEqual(
                payload(done(db, repo, task_id))["errors"][0]["code"],
                "review_target_required",
            )
            self.assertEqual(target_set(db, repo, task_id, FINGERPRINT_B).returncode, 0)
            self.assertEqual(receipt_add(db, repo, task_id, "reviewer-a").returncode, 0)
            self.assertEqual(
                payload(done(db, repo, task_id))["errors"][0]["code"],
                "review_receipts_insufficient",
            )
            self.assertEqual(receipt_add(db, repo, task_id, "reviewer-b").returncode, 0)
            self.assertEqual(done(db, repo, task_id).returncode, 0)

    def test_public_git_snapshot_target_projects_base_but_viewer_keeps_it_private(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            base = init_git_repo(repo)
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]

            before_db, before_git = db.read_bytes(), git_status(repo)
            revision_rejected = run_taskgov(
                "review", "target", "set",
                "--repo", str(repo), "--db", str(db), task_id,
                "--kind", "git_snapshot", "--revision", "caller-supplied",
                "--json",
            )
            self.assertEqual(
                revision_rejected.returncode,
                1,
                revision_rejected.stdout,
            )
            self.assertEqual(
                payload(revision_rejected)["errors"][0]["code"],
                "invalid_review_evidence",
            )
            self.assertEqual(db.read_bytes(), before_db)
            self.assertEqual(git_status(repo), before_git)

            read_only = run_taskgov(
                "review", "target", "set",
                "--repo", str(repo), "--db", str(db), task_id,
                "--kind", "git_snapshot", "--read-only", "--json",
            )
            self.assertEqual(read_only.returncode, 1, read_only.stdout)
            self.assertEqual(
                payload(read_only)["errors"][0]["code"],
                "invalid_argument",
            )
            self.assertEqual(db.read_bytes(), before_db)
            self.assertEqual(git_status(repo), before_git)

            target_set_result = run_taskgov(
                "review", "target", "set",
                "--repo", str(repo), "--db", str(db), task_id,
                "--kind", "git_snapshot", "--json",
            )
            self.assertEqual(
                target_set_result.returncode,
                0,
                target_set_result.stdout,
            )
            target_data = payload(target_set_result)["data"]
            fingerprint = target_data["task"]["review_target_value"]
            self.assertEqual(
                review_target_state(db, task_id),
                ("git_snapshot", fingerprint, base, 1),
            )
            self.assertTrue(fingerprint.startswith("sha256:"))
            self.assertEqual(
                target_data["task"]["review_target_base_revision"],
                base,
            )
            self.assertIn(
                "review_target_base_revision",
                target_data["changed_fields"],
            )
            self.assertEqual(git_status(repo), before_git)
            self.assertNotIn(str(repo), target_set_result.stdout)
            self.assertNotIn("reviewed.txt", target_set_result.stdout)

            recorded = receipt_add(db, repo, task_id, "reviewer-a")
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            receipt = payload(recorded)["data"]["receipt"]
            self.assertEqual(
                receipt["review_provenance"]["provenance_version"], 1
            )
            self.assertEqual(
                receipt["review_provenance"]["assurance_class"],
                "bound_attestation",
            )
            with closing(sqlite3.connect(db)) as connection:
                receipt_base = connection.execute(
                    "SELECT target_base_revision FROM review_receipts "
                    "WHERE review_receipt_id = ?",
                    (receipt["review_receipt_id"],),
                ).fetchone()[0]
            self.assertEqual(receipt_base, base)

            target = database_target(db, repo)
            with closing(connect_initialized(target)) as connection:
                evidence = read_review_evidence(
                    connection, target.project.project_id, task_id
                )
                viewer_tasks = list_tasks_for_viewer(connection, target.project).tasks
            shown = payload(
                run_taskgov(
                    "task", "show",
                    "--repo", str(repo), "--db", str(db), task_id, "--json",
                )
            )
            self.assertEqual(evidence["counts"]["receipts_current_generation"], 1)
            self.assertEqual(evidence["gate"]["qualifying_independent_passes"], 1)
            self.assertEqual(
                shown["data"]["task"]["review_target_base_revision"],
                base,
            )
            shown_provenance = shown["data"]["review_evidence"][
                "recent_receipts"
            ][0]["review_provenance"]
            self.assertEqual(shown_provenance, receipt["review_provenance"])
            self.assertNotIn(
                "review_provenance",
                json.dumps(viewer_tasks, sort_keys=True),
            )
            for projection in (
                receipt,
                evidence,
                viewer_tasks,
            ):
                serialized = json.dumps(projection, sort_keys=True)
                self.assertNotIn("review_target_base_revision", serialized)
                self.assertNotIn("target_base_revision", serialized)
                self.assertNotIn(base, serialized)

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE review_receipts SET target_base_revision = ? "
                    "WHERE review_receipt_id = ?",
                    ("0" * 40, receipt["review_receipt_id"]),
                )
                connection.commit()
            unreadable = run_taskgov(
                "task", "show", "--repo", str(repo), "--db", str(db),
                task_id, "--json",
            )
            self.assertEqual(unreadable.returncode, 2, unreadable.stdout)
            self.assertEqual(
                payload(unreadable)["errors"][0]["code"],
                "evidence_ledger_inconsistent",
            )
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE review_receipts SET target_base_revision = ? "
                    "WHERE review_receipt_id = ?",
                    (base, receipt["review_receipt_id"]),
                )
                connection.commit()

            reset = target_set(db, repo, task_id, FINGERPRINT_B)
            self.assertEqual(reset.returncode, 0, reset.stdout)
            self.assertEqual(
                review_target_state(db, task_id),
                ("diff_fingerprint", FINGERPRINT_B, "", 2),
            )
            self.assertEqual(
                payload(reset)["data"]["task"]["review_target_base_revision"],
                "",
            )

    def test_delayed_git_commit_target_resolution_allows_unrelated_handoff_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            revision = init_git_repo(repo)
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            target = database_target(db, repo)
            started = threading.Event()
            release = threading.Event()
            original_resolve = review_service.resolve_git_commit

            def delayed_resolve(repo_path, requested):
                resolved = original_resolve(repo_path, requested)
                started.set()
                if not release.wait(10):
                    raise AssertionError("Git commit preflight was not released")
                return resolved

            def set_target():
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        return review_service.set_requested_review_target(
                            connection,
                            target.project,
                            task_id,
                            kind="git_commit",
                            revision=revision[:12],
                            database_target=target,
                        )

            with mock.patch.object(
                review_service,
                "resolve_git_commit",
                side_effect=delayed_resolve,
            ):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(set_target)
                    self.assertTrue(started.wait(10))
                    handoff = run_taskgov(
                        "handoff",
                        "record",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task_id,
                        "--summary",
                        "Unrelated discovery during Git target preflight",
                        "--json",
                    )
                    self.assertEqual(handoff.returncode, 0, handoff.stdout)
                    release.set()
                    result = future.result(timeout=10)

            self.assertEqual(result.task["review_target_generation"], 1)
            self.assertEqual(result.task["review_target_value"], revision)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM handoff_records"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_delayed_git_snapshot_capture_allows_unrelated_handoff_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            target = database_target(db, repo)
            started = threading.Event()
            release = threading.Event()
            original_capture = review_service.observe_staged_git_manifest

            def delayed_capture(repo_path):
                snapshot = original_capture(repo_path)
                started.set()
                if not release.wait(10):
                    raise AssertionError("Git snapshot preflight was not released")
                return snapshot

            def set_target():
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        return set_git_snapshot_target(
                            connection,
                            target.project,
                            task_id,
                            database_target=target,
                        )

            with mock.patch.object(
                review_service,
                "observe_staged_git_manifest",
                side_effect=delayed_capture,
            ):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(set_target)
                    self.assertTrue(started.wait(10))
                    handoff = run_taskgov(
                        "handoff",
                        "record",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task_id,
                        "--summary",
                        "Unrelated discovery during Git snapshot preflight",
                        "--json",
                    )
                    self.assertEqual(handoff.returncode, 0, handoff.stdout)
                    release.set()
                    result = future.result(timeout=10)

            self.assertEqual(result.task["review_target_generation"], 1)
            self.assertEqual(result.task["review_target_kind"], "git_snapshot")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM handoff_records"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_delayed_completion_snapshot_comparison_allows_unrelated_task_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            unrelated_task_id = add_task(
                db,
                repo,
                title="Unrelated task",
            )["task_id"]
            (repo / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "--", "reviewed.txt"],
                check=True,
            )
            snapshot_target_and_two_passes(db, repo, task_id)
            completion_commit = commit_staged(repo)
            target = database_target(db, repo)
            started = threading.Event()
            release = threading.Event()
            original_verify = task_service.verify_git_snapshot_commit

            def delayed_verify(*args, **kwargs):
                verified = original_verify(*args, **kwargs)
                started.set()
                if not release.wait(10):
                    raise AssertionError("completion preflight was not released")
                return verified

            def complete():
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        return edit_task(
                            connection,
                            target.project,
                            task_id,
                            status="done",
                            verification_complete=True,
                            review_complete=True,
                            completion_commit_hash=completion_commit,
                            database_target=target,
                        )

            with mock.patch.object(
                task_service,
                "verify_git_snapshot_commit",
                side_effect=delayed_verify,
            ):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(complete)
                    self.assertTrue(started.wait(10))
                    unrelated = run_taskgov(
                        "task",
                        "edit",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        unrelated_task_id,
                        "--add-note",
                        "Independent progress during completion preflight",
                        "--json",
                    )
                    self.assertEqual(unrelated.returncode, 0, unrelated.stdout)
                    release.set()
                    result = future.result(timeout=10)

            self.assertEqual(result.task["status"], "done")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM task_events
                         WHERE task_id = ? AND event_type = 'task_updated'
                        """,
                        (task_id,),
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_concurrent_internal_snapshot_targets_reject_stale_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            target = database_target(db, repo)
            capture_barrier = threading.Barrier(2)
            original_capture = review_service.observe_staged_git_manifest

            def synchronized_capture(repo_path):
                snapshot = original_capture(repo_path)
                capture_barrier.wait()
                return snapshot

            def set_target(_):
                with closing(connect_existing(db)) as connection:
                    validate_current_database(connection, target)
                    self.assertFalse(connection.in_transaction)
                    try:
                        with connection:
                            result = set_git_snapshot_target(
                                connection, target.project, task_id
                            )
                        return ("ok", result.task["review_target_generation"])
                    except ReviewEvidenceError as exc:
                        return ("error", exc.code)

            with mock.patch.object(
                review_service,
                "observe_staged_git_manifest",
                side_effect=synchronized_capture,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(set_target, range(2)))

            self.assertCountEqual(outcomes, [("ok", 1), ("error", "invalid_argument")])
            self.assertEqual(review_target_state(db, task_id)[3], 1)
            with closing(sqlite3.connect(db)) as connection:
                events = connection.execute(
                    "SELECT COUNT(*) FROM task_events "
                    "WHERE task_id = ? AND event_type = 'review_target_set'",
                    (task_id,),
                ).fetchone()[0]
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(events, 1)

    def test_schema_six_reopen_clears_snapshot_base_and_advances_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            base = init_git_repo(repo)
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            old_generation = internal_git_snapshot_target(
                db, repo, task_id
            ).task["review_target_generation"]
            self.assertEqual(
                receipt_add(db, repo, task_id, "reviewer-a").returncode,
                0,
            )
            self.assertEqual(
                receipt_add(db, repo, task_id, "reviewer-b").returncode,
                0,
            )
            subprocess.run(
                [
                    "git", "-C", str(repo), "-c", "user.name=TaskGov Test",
                    "-c", "user.email=taskgov@example.invalid", "commit",
                    "--quiet", "--allow-empty", "-m", "completed snapshot",
                ],
                check=True,
            )
            completion_commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            completed = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                task_id, "--status", "done", "--verification-complete",
                "--review-complete", "--completion-evidence-kind",
                "git_commit", "--completion-revision", completion_commit,
                "--json",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)

            reopened = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), task_id,
                "--status", "in_progress", "--reopen-reason",
                "Acceptance changed", "--json",
            )
            self.assertEqual(reopened.returncode, 0, reopened.stdout)
            self.assertEqual(
                review_target_state(db, task_id),
                ("", "", "", old_generation + 1),
            )
            serialized = json.dumps(payload(reopened), sort_keys=True)
            self.assertNotIn("review_target_base_revision", serialized)
            self.assertNotIn(base, serialized)

    def test_invalid_snapshot_base_fails_closed_before_review_tier_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task_id = add_task(db, repo, tier=2)["task_id"]
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE tasks SET review_target_base_revision = ? "
                    "WHERE task_id = ?",
                    ("a" * 40, task_id),
                )
                connection.commit()
            before = db.read_bytes()

            rejected = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), task_id,
                "--review-tier", "1", "--review-tier-change-reason",
                "Review scope is now localized", "--json",
            )
            self.assertEqual(rejected.returncode, 2, rejected.stdout)
            self.assertEqual(
                payload(rejected)["errors"][0]["code"],
                "project_state_unreadable",
            )
            self.assertEqual(db.read_bytes(), before)

    def test_snapshot_completion_rejects_a_root_commit_without_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            base = init_git_repo(repo)
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            internal_git_snapshot_target(db, repo, task_id)
            for reviewer in ("reviewer-a", "reviewer-b"):
                self.assertEqual(
                    receipt_add(db, repo, task_id, reviewer).returncode, 0
                )
            before_db, before_git = db.read_bytes(), git_status(repo)

            rejected = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), task_id,
                "--status", "done", "--verification-complete", "--review-complete",
                "--completion-commit-hash", base, "--json",
            )
            body = payload(rejected)
            self.assertEqual(rejected.returncode, 1, rejected.stdout)
            self.assertEqual(body["errors"][0]["code"], "review_target_mismatch")
            self.assertEqual(
                body["data"],
                {"task": None, "changed_fields": [], "event": None},
            )
            self.assertEqual(db.read_bytes(), before_db)
            self.assertEqual(git_status(repo), before_git)

    def test_snapshot_completion_binds_two_reviews_to_the_later_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            base = init_git_repo(repo)
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            reviewed = repo / "reviewed.txt"
            reviewed.write_text("reviewed\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "reviewed.txt"],
                check=True,
            )

            target = snapshot_target_and_two_passes(db, repo, task_id)
            self.assertEqual(
                target["task"]["review_target_base_revision"],
                base,
            )
            completion = commit_staged(repo)
            before_git = git_status(repo)
            completed = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), task_id,
                "--status", "done", "--verification-complete",
                "--review-complete", "--completion-commit-hash", completion,
                "--json",
            )

            self.assertEqual(completed.returncode, 0, completed.stdout)
            shown = payload(
                run_taskgov(
                    "task", "show", "--repo", str(repo), "--db", str(db),
                    task_id, "--json",
                )
            )["data"]
            self.assertEqual(shown["task"]["status"], "done")
            self.assertEqual(
                shown["task"]["completion_evidence_revision"],
                completion,
            )
            self.assertEqual(
                shown["review_evidence"]["counts"]["receipts_total"],
                2,
            )
            self.assertEqual(
                shown["review_evidence"]["target"]["generation"],
                1,
            )
            self.assertTrue(shown["review_evidence"]["gate"]["satisfied"])
            history = shown["completion_history"]
            self.assertEqual(
                (
                    history["total"],
                    history["returned_count"],
                    history["truncated"],
                    history["legacy_history_incomplete"],
                ),
                (1, 1, False, False),
            )
            cycle = history["cycles"][0]
            self.assertEqual(
                cycle["completion_evidence"]["kind"],
                "git_commit",
            )
            self.assertEqual(cycle["review_target"]["kind"], "git_snapshot")
            self.assertEqual(cycle["review_tier"], 2)
            self.assertEqual(
                cycle["gate_basis"]["kind"],
                "independent_passes",
            )
            self.assertEqual(
                len(cycle["gate_basis"]["qualifying_receipt_ids"]),
                2,
            )
            self.assertEqual(git_status(repo), before_git)

    def test_snapshot_completion_rejects_a_commit_changed_after_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            init_git_repo(repo)
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            reviewed = repo / "reviewed.txt"
            reviewed.write_text("reviewed\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "reviewed.txt"],
                check=True,
            )
            snapshot_target_and_two_passes(db, repo, task_id)

            reviewed.write_text("changed after review\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "reviewed.txt"],
                check=True,
            )
            completion = commit_staged(repo, "changed completion")
            before_db, before_git = db.read_bytes(), git_status(repo)
            rejected = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), task_id,
                "--status", "done", "--verification-complete",
                "--review-complete", "--completion-commit-hash", completion,
                "--json",
            )

            self.assertEqual(rejected.returncode, 1, rejected.stdout)
            self.assertEqual(
                payload(rejected)["errors"][0]["code"],
                "review_target_mismatch",
            )
            self.assertEqual(db.read_bytes(), before_db)
            self.assertEqual(git_status(repo), before_git)

    def test_public_completion_binding_accepts_exact_non_snapshot_pairs(self):
        cases = ("git_commit", "external_revision", "commit_not_required")
        for completion_kind in cases:
            with self.subTest(completion_kind=completion_kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo, db = root / "repo", root / "tasks.sqlite"
                git_revision = init_git_repo(repo)
                init_db(db, repo)
                task_id = add_task(db, repo)["task_id"]
                if completion_kind == "git_commit":
                    target_and_two_passes(
                        db,
                        repo,
                        task_id,
                        "git_commit",
                        git_revision,
                    )
                    evidence_args = ("--completion-commit-hash", git_revision)
                elif completion_kind == "external_revision":
                    target_and_two_passes(
                        db,
                        repo,
                        task_id,
                        "external_revision",
                        "release-reviewed",
                    )
                    evidence_args = (
                        "--completion-evidence-kind",
                        "external_revision",
                        "--completion-revision",
                        "release-reviewed",
                        "--completion-evidence-reason",
                        "Approved external release",
                        "--external-revision-approved",
                    )
                else:
                    target_and_two_passes(
                        db,
                        repo,
                        task_id,
                        "diff_fingerprint",
                        FINGERPRINT_A,
                    )
                    evidence_args = ("--commit-not-required",)

                completed = run_taskgov(
                    "task", "edit",
                    "--repo", str(repo), "--db", str(db), task_id,
                    "--status", "done", "--verification-complete",
                    "--review-complete", *evidence_args, "--json",
                )

                self.assertEqual(completed.returncode, 0, completed.stdout)
                shown = payload(
                    run_taskgov(
                        "task", "show",
                        "--repo", str(repo), "--db", str(db), task_id,
                        "--json",
                    )
                )["data"]
                self.assertEqual(shown["task"]["status"], "done")
                self.assertEqual(
                    shown["review_evidence"]["counts"]["receipts_total"],
                    2,
                )
                self.assertEqual(
                    shown["review_evidence"]["target"]["generation"],
                    1,
                )
                history = shown["completion_history"]
                self.assertEqual(
                    (
                        history["total"],
                        history["returned_count"],
                        history["truncated"],
                        history["legacy_history_incomplete"],
                    ),
                    (1, 1, False, False),
                )
                cycle = history["cycles"][0]
                expected_target_kind = {
                    "git_commit": "git_commit",
                    "external_revision": "external_revision",
                    "commit_not_required": "diff_fingerprint",
                }[completion_kind]
                self.assertEqual(
                    cycle["completion_evidence"]["kind"],
                    completion_kind,
                )
                self.assertEqual(
                    cycle["review_target"]["kind"],
                    expected_target_kind,
                )
                self.assertEqual(cycle["review_tier"], 2)
                self.assertEqual(
                    cycle["gate_basis"]["kind"],
                    "independent_passes",
                )
                self.assertEqual(
                    len(cycle["gate_basis"]["qualifying_receipt_ids"]),
                    2,
                )

    def test_public_completion_binding_rejects_non_snapshot_mismatches(self):
        cases = ("git_commit", "external_revision", "diff_fingerprint")
        for target_kind in cases:
            with self.subTest(target_kind=target_kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo, db = root / "repo", root / "tasks.sqlite"
                reviewed_commit = init_git_repo(repo)
                init_db(db, repo)
                task_id = add_task(db, repo)["task_id"]
                if target_kind == "git_commit":
                    target_and_two_passes(
                        db,
                        repo,
                        task_id,
                        target_kind,
                        reviewed_commit,
                    )
                    (repo / "different.txt").write_text(
                        "different\n",
                        encoding="utf-8",
                    )
                    subprocess.run(
                        ["git", "-C", str(repo), "add", "different.txt"],
                        check=True,
                    )
                    different_commit = commit_staged(
                        repo,
                        "different completion",
                    )
                    evidence_args = (
                        "--completion-commit-hash",
                        different_commit,
                    )
                elif target_kind == "external_revision":
                    target_and_two_passes(
                        db,
                        repo,
                        task_id,
                        target_kind,
                        "release-reviewed",
                    )
                    evidence_args = (
                        "--completion-evidence-kind",
                        "external_revision",
                        "--completion-revision",
                        "release-different",
                        "--completion-evidence-reason",
                        "Approved external release",
                        "--external-revision-approved",
                    )
                else:
                    target_and_two_passes(
                        db,
                        repo,
                        task_id,
                        target_kind,
                        FINGERPRINT_A,
                    )
                    evidence_args = (
                        "--completion-commit-hash",
                        reviewed_commit,
                    )
                before_db, before_git = db.read_bytes(), git_status(repo)

                rejected = run_taskgov(
                    "task", "edit",
                    "--repo", str(repo), "--db", str(db), task_id,
                    "--status", "done", "--verification-complete",
                    "--review-complete", *evidence_args, "--json",
                )

                self.assertEqual(rejected.returncode, 1, rejected.stdout)
                self.assertEqual(
                    payload(rejected)["errors"][0]["code"],
                    "review_target_mismatch",
                )
                self.assertEqual(db.read_bytes(), before_db)
                self.assertEqual(git_status(repo), before_git)

    def test_locked_binding_rechecks_a_concurrently_reset_review_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            target_set(db, repo, task_id)
            receipt_add(db, repo, task_id, "reviewer-a")
            receipt_add(db, repo, task_id, "reviewer-b")
            target = database_target(db, repo)
            original_binding = task_service.revalidate_done_git_evidence
            binding_calls = 0

            def reset_between_checks(task, **kwargs):
                nonlocal binding_calls
                binding_calls += 1
                original_binding(task, **kwargs)
                if binding_calls == 1:
                    with closing(connect_existing(db)) as writer:
                        seed_review_evidence_connection(
                            writer,
                            task_id,
                            target_kind="external_revision",
                            target_value="concurrent-release",
                        )
                        writer.commit()

            with closing(connect_existing(db)) as connection:
                validate_current_database(connection, target)
                with mock.patch.object(
                    task_service,
                    "revalidate_done_git_evidence",
                    side_effect=reset_between_checks,
                ):
                    with self.assertRaises(TaskValidationError) as raised:
                        edit_task(
                            connection,
                            target.project,
                            task_id,
                            status="done",
                            verification_complete=True,
                            review_complete=True,
                            commit_not_required=True,
                        )
                connection.rollback()

            self.assertEqual(raised.exception.code, "review_target_mismatch")
            self.assertEqual(binding_calls, 1)
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    """
                    SELECT status, completion_evidence_kind,
                           review_target_kind, review_target_value
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(
                row,
                (
                    "ready",
                    "none",
                    "external_revision",
                    "concurrent-release",
                ),
            )

    def test_concurrent_completion_allows_one_done_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "tasks.sqlite"
            repo.mkdir()
            init_db(db, repo)
            task_id = add_task(db, repo)["task_id"]
            self.assertEqual(target_set(db, repo, task_id).returncode, 0)
            self.assertEqual(
                receipt_add(db, repo, task_id, "reviewer-a").returncode,
                0,
            )
            self.assertEqual(
                receipt_add(db, repo, task_id, "reviewer-b").returncode,
                0,
            )
            target = database_target(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                before_events = connection.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            barrier = threading.Barrier(2)
            prelock_barrier = threading.Barrier(2)
            thread_calls = threading.local()
            original_binding = task_service.revalidate_done_git_evidence

            def synchronize_first_prelock_check(task, **kwargs):
                call_count = getattr(thread_calls, "count", 0) + 1
                thread_calls.count = call_count
                if call_count == 1:
                    prelock_barrier.wait()
                return original_binding(task, **kwargs)

            def complete(_worker):
                with closing(connect_existing(db)) as connection:
                    try:
                        validate_current_database(connection, target)
                        barrier.wait()
                        with connection:
                            edit_task(
                                connection,
                                target.project,
                                task_id,
                                status="done",
                                verification_complete=True,
                                review_complete=True,
                                commit_not_required=True,
                            )
                        return "ok"
                    except task_service.TaskRepositoryError as exc:
                        return exc.code

            with mock.patch.object(
                task_service,
                "revalidate_done_git_evidence",
                side_effect=synchronize_first_prelock_check,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(complete, range(2)))

            self.assertCountEqual(
                outcomes,
                ["ok", "done_task_requires_reopen"],
            )
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    """
                    SELECT status, completion_evidence_kind
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                after_events = connection.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
            self.assertEqual(row, ("done", "commit_not_required"))
            self.assertEqual(after_events, before_events + 1)

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
