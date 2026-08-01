from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    initialize_taskgov_internal,
    run_taskgov_internal,
)
from tests.review_test_helpers import seed_review_evidence
from tests.test_completion_cycle_history import (
    make_v14_target,
    migrate_to_v15,
    seed_v14_tasks,
)

from task_governance_tool import cli as cli_service
from task_governance_tool import storage as storage_service
from task_governance_tool.maintenance import MutationOutcome
from task_governance_tool.storage import (
    StorageError,
    apply_completion_cycle_capture_activation_migration,
    apply_verification_receipts_migration,
    connect,
    connect_initialized,
    connect_snapshot_readonly,
    current_schema_version,
    resolve_database_target,
    verification_expectation_digest,
)
from task_governance_tool.tasks import read_internal_task
from task_governance_tool.verification_receipts import (
    PUBLIC_VERIFICATION_RECEIPT_FIELDS,
    VerificationReceiptError,
    add_verification_receipt,
    current_verification_gate,
    normalize_verification_receipt_input,
)
from task_governance_tool.viewer import build_viewer_snapshot


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPT_PATH = SKILL_ROOT / "scripts" / "taskgov.py"
FINGERPRINT_A = "sha256:" + "a" * 64
FINGERPRINT_B = "sha256:" + "b" * 64
DEFAULT_VERIFICATION = "python -m unittest tests.test_verification_receipts"


def run_taskgov(*args: str, maintenance_enabled: bool = False):
    return run_taskgov_internal(
        *args,
        maintenance_enabled=maintenance_enabled,
    )


def payload(result) -> dict:
    return json.loads(result.stdout)


def initialize(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    db = root / "state" / "taskgov.sqlite"
    repo.mkdir()
    initialize_taskgov_internal(repo=repo, db=db)
    return repo, db


def add_task(
    db: Path,
    repo: Path,
    *,
    title: str = "Verification Receipt task",
    status: str = "in_progress",
    verification: str = DEFAULT_VERIFICATION,
    review_tier: int = 0,
) -> dict:
    result = run_taskgov(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
        "--status",
        status,
        "--review-tier",
        str(review_tier),
        "--verification",
        verification,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return payload(result)["data"]["task"]


def set_target(
    db: Path,
    repo: Path,
    task_id: str,
    *,
    fingerprint: str = FINGERPRINT_A,
) -> int:
    result = run_taskgov(
        "review",
        "target",
        "set",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--kind",
        "diff_fingerprint",
        "--revision",
        fingerprint,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return int(payload(result)["data"]["task"]["review_target_generation"])


def add_receipt(
    db: Path,
    repo: Path,
    task_id: str,
    generation: int,
    *,
    command_label: str = "focused unittest",
    result: str = "pass",
    duration_ms: int | str = 25,
    scope_coverage: str = "full",
    json_output: bool = True,
    read_only: bool = False,
    maintenance_enabled: bool = False,
):
    args = [
        "verification",
        "receipt",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--command-label",
        command_label,
        "--result",
        result,
        "--duration-ms",
        str(duration_ms),
        "--scope-coverage",
        scope_coverage,
        "--expected-target-generation",
        str(generation),
    ]
    if read_only:
        args.append("--read-only")
    if json_output:
        args.append("--json")
    return run_taskgov(
        *args,
        maintenance_enabled=maintenance_enabled,
    )


def show_task(
    db: Path,
    repo: Path,
    task_id: str,
    *,
    json_output: bool,
):
    args = [
        "task",
        "show",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--read-only",
    ]
    if json_output:
        args.append("--json")
    return run_taskgov(*args)


def completion(
    db: Path,
    repo: Path,
    task_id: str,
    *,
    check: bool = False,
):
    args = [
        "task",
        "complete",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--verification-complete",
        "--review-complete",
        "--commit-not-required",
    ]
    if check:
        args.extend(("--check", "--read-only"))
    args.append("--json")
    return run_taskgov(*args)


def target_for(db: Path, repo: Path):
    return resolve_database_target(
        repo=repo,
        db=db,
        script_path=SCRIPT_PATH,
    )


def table_count(db: Path, table: str) -> int:
    with closing(sqlite3.connect(db)) as connection:
        return int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        )


def initialize_v16_fixture(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    target = make_v14_target(root)
    task_ids = seed_v14_tasks(
        target,
        variants=("external_revision",),
        include_ready=True,
    )
    migrate_to_v15(target)
    with closing(connect(target.db_path)) as connection:
        apply_completion_cycle_capture_activation_migration(connection)
        if current_schema_version(connection) != 16:
            raise AssertionError("schema-v16 fixture activation failed")
    return target, task_ids["external_revision"]


class VerificationReceiptValidationTests(unittest.TestCase):
    def test_normalization_accepts_closed_values_and_signed_int64_boundaries(self):
        for result in ("pass", "fail", "timeout"):
            for coverage in ("full", "partial"):
                with self.subTest(result=result, coverage=coverage):
                    values = normalize_verification_receipt_input(
                        command_label="x" * 200,
                        result=result,
                        duration_ms=(1 << 63) - 1,
                        scope_coverage=coverage,
                        expected_target_generation=(1 << 63) - 1,
                    )
                    self.assertEqual(values.command_label, "x" * 200)
                    self.assertEqual(values.result, result)
                    self.assertEqual(values.duration_ms, (1 << 63) - 1)
                    self.assertEqual(values.scope_coverage, coverage)
                    self.assertEqual(
                        values.expected_target_generation,
                        (1 << 63) - 1,
                    )

        zero_duration = normalize_verification_receipt_input(
            command_label="zero duration",
            result="pass",
            duration_ms=0,
            scope_coverage="full",
            expected_target_generation=1,
        )
        self.assertEqual(zero_duration.duration_ms, 0)

        descriptive_label = normalize_verification_receipt_input(
            command_label="Full offline all-lane verification (784 tests)",
            result="pass",
            duration_ms=1,
            scope_coverage="full",
            expected_target_generation=1,
        )
        self.assertEqual(
            descriptive_label.command_label,
            "Full offline all-lane verification (784 tests)",
        )

    def test_normalization_rejects_each_field_with_the_fixed_contract(self):
        cases = (
            (
                {"command_label": ""},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "x" * 201},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "line one\nline two"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "python -m pytest tests -q"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "uv run python checks.py"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "python.exe checks.py"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "pytest.exe tests"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "verify.ps1"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "node tests.js"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "poetry run pytest"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "npm ci"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "pnpm lint"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "yarn lint"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "pip install package"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "cargo fmt"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "go vet ./..."},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "dotnet build"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "python checks"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "python.exe checks"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "uv sync"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "coverage report"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "echo ok & type result.txt"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "& .\\verify.ps1"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": ". .\\verify.ps1"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": ". ./verify.sh"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "focused checks && type output.log"},
                "invalid_verification_evidence",
                "command_label must be a non-empty sanitized label of at most 200 characters",
            ),
            (
                {"command_label": "Authorization: Bearer secret-value"},
                "privacy_rejected",
                "command_label appears to contain a secret, raw log, or dump content",
            ),
            (
                {"result": "success"},
                "invalid_verification_evidence",
                "result must be one of pass, fail, or timeout",
            ),
            (
                {"duration_ms": -1},
                "invalid_verification_evidence",
                "duration_ms must be a nonnegative signed-64-bit integer",
            ),
            (
                {"duration_ms": 1 << 63},
                "invalid_verification_evidence",
                "duration_ms must be a nonnegative signed-64-bit integer",
            ),
            (
                {"scope_coverage": "complete"},
                "invalid_verification_evidence",
                "scope_coverage must be full or partial",
            ),
            (
                {"expected_target_generation": 0},
                "invalid_verification_evidence",
                "expected_target_generation must be a positive signed-64-bit integer",
            ),
            (
                {"expected_target_generation": 1 << 63},
                "invalid_verification_evidence",
                "expected_target_generation must be a positive signed-64-bit integer",
            ),
        )
        baseline = {
            "command_label": "focused unittest",
            "result": "pass",
            "duration_ms": 1,
            "scope_coverage": "full",
            "expected_target_generation": 1,
        }
        for override, code, message in cases:
            with self.subTest(override=override):
                with self.assertRaises(VerificationReceiptError) as raised:
                    normalize_verification_receipt_input(
                        **{**baseline, **override}
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.message, message)

        with self.assertRaises(VerificationReceiptError) as fail_fast:
            normalize_verification_receipt_input(
                command_label="",
                result="success",
                duration_ms=-1,
                scope_coverage="complete",
                expected_target_generation=0,
            )
        self.assertEqual(fail_fast.exception.field, "command_label")


class VerificationReceiptIntegrationTests(unittest.TestCase):
    def test_service_records_one_public_receipt_without_mutating_task_or_events(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            generation = set_target(db, repo, task["task_id"])
            target = target_for(db, repo)
            with closing(sqlite3.connect(db)) as raw:
                before_task = raw.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                before_events = raw.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]

            with closing(connect_initialized(target)) as connection:
                with connection:
                    result = add_verification_receipt(
                        connection,
                        target.project,
                        task["task_id"],
                        command_label="focused unittest",
                        result="pass",
                        duration_ms=0,
                        scope_coverage="full",
                        expected_target_generation=generation,
                        database_target=target,
                    )
                stored_task = read_internal_task(
                    connection,
                    target.project.project_id,
                    task["task_id"],
                )
                gate = current_verification_gate(
                    connection,
                    task=stored_task,
                )

            receipt = result.receipt
            self.assertEqual(tuple(receipt), PUBLIC_VERIFICATION_RECEIPT_FIELDS)
            self.assertEqual(receipt["result"], "pass")
            self.assertEqual(receipt["duration_ms"], 0)
            self.assertEqual(receipt["scope_coverage"], "full")
            self.assertEqual(
                receipt["source_revision"],
                {
                    "kind": "diff_fingerprint",
                    "value": FINGERPRINT_A,
                    "base_revision": None,
                    "generation": generation,
                },
            )
            self.assertTrue(gate.satisfied)
            self.assertEqual(
                gate.qualifying_receipt_id,
                receipt["verification_receipt_id"],
            )
            with closing(sqlite3.connect(db)) as raw:
                self.assertEqual(
                    raw.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone(),
                    before_task,
                )
                self.assertEqual(
                    raw.execute(
                        "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    before_events,
                )

    def test_result_coverage_matrix_drives_only_the_exact_current_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            cases = tuple(
                (result, coverage)
                for result in ("pass", "fail", "timeout")
                for coverage in ("full", "partial")
            )

            for index, (result, coverage) in enumerate(cases, start=1):
                with self.subTest(result=result, coverage=coverage):
                    generation = set_target(db, repo, task_id)
                    before = payload(
                        show_task(
                            db,
                            repo,
                            task_id,
                            json_output=True,
                        )
                    )["data"]["verification_evidence"]
                    self.assertEqual(
                        before["gate"]["blocking_code"],
                        "verification_receipt_required",
                    )

                    recorded = add_receipt(
                        db,
                        repo,
                        task_id,
                        generation,
                        result=result,
                        scope_coverage=coverage,
                    )
                    self.assertEqual(recorded.returncode, 0, recorded.stdout)
                    receipt = payload(recorded)["data"]["receipt"]
                    shown = payload(
                        show_task(
                            db,
                            repo,
                            task_id,
                            json_output=True,
                        )
                    )["data"]["verification_evidence"]

                    qualifies = result == "pass" and coverage == "full"
                    self.assertEqual(shown["gate"]["satisfied"], qualifies)
                    self.assertEqual(
                        shown["gate"]["blocking_code"],
                        None if qualifies else "verification_receipt_blocking",
                    )
                    self.assertEqual(
                        shown["gate"]["qualifying_receipt_id"],
                        receipt["verification_receipt_id"] if qualifies else None,
                    )
                    self.assertEqual(
                        shown["counts"],
                        {
                            "receipts_total": index,
                            "receipts_exact_current": 1,
                            "qualifying_exact_current": int(qualifies),
                            "blocking_exact_current": int(not qualifies),
                        },
                    )

    def test_expected_generation_and_duplicate_fail_closed_then_fresh_target_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            first_generation = set_target(db, repo, task_id)

            stale = add_receipt(
                db,
                repo,
                task_id,
                first_generation + 1,
            )
            self.assertEqual(stale.returncode, 1, stale.stdout)
            self.assertEqual(
                payload(stale)["errors"][0],
                {
                    "code": "verification_basis_stale",
                    "message": "verification target changed after the reported run",
                },
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)

            first = add_receipt(db, repo, task_id, first_generation)
            self.assertEqual(first.returncode, 0, first.stdout)
            duplicate = add_receipt(db, repo, task_id, first_generation)
            self.assertEqual(duplicate.returncode, 1, duplicate.stdout)
            self.assertEqual(
                payload(duplicate)["errors"][0]["code"],
                "verification_receipt_already_recorded",
            )

            second_generation = set_target(
                db,
                repo,
                task_id,
                fingerprint=FINGERPRINT_B,
            )
            old_basis = add_receipt(db, repo, task_id, first_generation)
            self.assertEqual(old_basis.returncode, 1, old_basis.stdout)
            self.assertEqual(
                payload(old_basis)["errors"][0]["code"],
                "verification_basis_stale",
            )
            second = add_receipt(db, repo, task_id, second_generation)
            self.assertEqual(second.returncode, 0, second.stdout)
            self.assertEqual(table_count(db, "verification_receipts"), 2)

    def test_status_expectation_and_target_errors_use_fixed_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))

            ready = add_task(
                db,
                repo,
                title="Ready task",
                status="ready",
            )
            ready_result = add_receipt(db, repo, ready["task_id"], 1)
            self.assertEqual(
                payload(ready_result)["errors"][0]["code"],
                "invalid_status_transition",
            )

            empty = add_task(
                db,
                repo,
                title="Empty expectation",
                verification="",
            )
            empty_generation = set_target(db, repo, empty["task_id"])
            empty_gate = payload(
                show_task(
                    db,
                    repo,
                    empty["task_id"],
                    json_output=True,
                )
            )["data"]["verification_evidence"]["gate"]
            self.assertEqual(
                empty_gate,
                {
                    "required": False,
                    "satisfied": True,
                    "blocking_code": None,
                    "qualifying_receipt_id": None,
                },
            )
            empty_result = add_receipt(
                db,
                repo,
                empty["task_id"],
                empty_generation,
            )
            self.assertEqual(
                payload(empty_result)["errors"][0]["code"],
                "verification_expectation_required",
            )

            targetless = add_task(
                db,
                repo,
                title="Missing target",
            )
            targetless_result = add_receipt(
                db,
                repo,
                targetless["task_id"],
                1,
            )
            self.assertEqual(
                payload(targetless_result)["errors"][0]["code"],
                "review_target_required",
            )
            targetless_gate = payload(
                show_task(
                    db,
                    repo,
                    targetless["task_id"],
                    json_output=True,
                )
            )["data"]["verification_evidence"]["gate"]
            self.assertEqual(
                targetless_gate["blocking_code"],
                "review_target_required",
            )

            done = add_task(
                db,
                repo,
                title="Done task",
                verification="",
            )
            seed_review_evidence(db, done["task_id"])
            completed = completion(db, repo, done["task_id"])
            self.assertEqual(completed.returncode, 0, completed.stdout)
            done_result = add_receipt(db, repo, done["task_id"], 1)
            self.assertEqual(
                payload(done_result)["errors"][0]["code"],
                "done_task_requires_reopen",
            )

            for result in (
                ready_result,
                empty_result,
                targetless_result,
                done_result,
            ):
                self.assertEqual(
                    payload(result)["data"],
                    {"receipt": None},
                )
            self.assertEqual(table_count(db, "verification_receipts"), 0)

    def test_cli_read_only_privacy_text_and_backup_only_outcome(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
                return_value=[],
            ) as maintenance:
                read_only = add_receipt(
                    db,
                    repo,
                    "invalid-task-id",
                    0,
                    command_label="",
                    result="invalid",
                    duration_ms=-1,
                    scope_coverage="invalid",
                    read_only=True,
                    maintenance_enabled=True,
                )
                self.assertEqual(read_only.returncode, 1, read_only.stdout)
                self.assertEqual(
                    payload(read_only)["errors"][0],
                    {
                        "code": "invalid_argument",
                        "message": (
                            "verification receipt add cannot run with --read-only "
                            "because it writes the database"
                        ),
                    },
                )
                maintenance.assert_not_called()

                private = add_receipt(
                    db,
                    repo,
                    task_id,
                    generation,
                    command_label="Authorization: Bearer secret-value",
                    maintenance_enabled=True,
                )
                self.assertEqual(private.returncode, 1, private.stdout)
                self.assertEqual(
                    payload(private)["errors"][0]["code"],
                    "privacy_rejected",
                )
                maintenance.assert_not_called()

                recorded = add_receipt(
                    db,
                    repo,
                    task_id,
                    generation,
                    result="timeout",
                    scope_coverage="partial",
                    json_output=False,
                    maintenance_enabled=True,
                )
                self.assertEqual(recorded.returncode, 0, recorded.stderr)
                with closing(sqlite3.connect(db)) as connection:
                    receipt_id = connection.execute(
                        "SELECT verification_receipt_id FROM verification_receipts"
                    ).fetchone()[0]
                self.assertEqual(
                    recorded.stdout,
                    (
                        f"Verification receipt recorded: {receipt_id}\n"
                        "Result: timeout  Coverage: partial\n"
                        f"Source: diff_fingerprint/generation {generation}\n"
                    ),
                )
                maintenance.assert_called_once()
                outcome = maintenance.call_args.args[1]
                self.assertEqual(
                    outcome,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=False,
                    ),
                )

    def test_task_show_adds_exact_json_projection_and_keeps_text_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)
            before_text = show_task(
                db,
                repo,
                task_id,
                json_output=False,
            ).stdout

            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            recorded_payload = payload(recorded)
            self.assertEqual(set(recorded_payload["data"]), {"receipt"})
            receipt = recorded_payload["data"]["receipt"]
            after_text = show_task(
                db,
                repo,
                task_id,
                json_output=False,
            ).stdout
            self.assertEqual(after_text, before_text)

            shown = payload(
                show_task(
                    db,
                    repo,
                    task_id,
                    json_output=True,
                )
            )
            evidence = shown["data"]["verification_evidence"]
            self.assertEqual(
                set(evidence),
                {
                    "expectation",
                    "contract_revision",
                    "source_revision",
                    "gate",
                    "counts",
                    "recent_receipts",
                },
            )
            self.assertEqual(evidence["expectation"], DEFAULT_VERIFICATION)
            self.assertEqual(
                set(evidence["source_revision"]),
                {"kind", "value", "base_revision", "generation"},
            )
            self.assertEqual(
                set(evidence["gate"]),
                {
                    "required",
                    "satisfied",
                    "blocking_code",
                    "qualifying_receipt_id",
                },
            )
            self.assertEqual(
                set(evidence["counts"]),
                {
                    "receipts_total",
                    "receipts_exact_current",
                    "qualifying_exact_current",
                    "blocking_exact_current",
                },
            )
            self.assertEqual(evidence["recent_receipts"], [receipt])
            self.assertNotIn("verification_expectation_digest", receipt)
            self.assertNotIn("verification_basis_version", receipt)

            missing = show_task(
                db,
                repo,
                "tg_task_ffffffffffffffff",
                json_output=True,
            )
            self.assertEqual(missing.returncode, 1, missing.stdout)
            self.assertIsNone(
                payload(missing)["data"]["verification_evidence"]
            )

    def test_completion_check_and_write_require_fresh_pass_full_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            seed_review_evidence(db, task_id)

            required = completion(db, repo, task_id, check=True)
            self.assertEqual(required.returncode, 0, required.stdout)
            self.assertFalse(payload(required)["data"]["ready"])
            self.assertEqual(
                payload(required)["data"]["blocking_codes"],
                ["verification_receipt_required"],
            )
            rejected_write = completion(db, repo, task_id)
            self.assertEqual(rejected_write.returncode, 1, rejected_write.stdout)
            self.assertEqual(
                payload(rejected_write)["errors"][0]["code"],
                "verification_receipt_required",
            )

            blocked_receipt = add_receipt(
                db,
                repo,
                task_id,
                1,
                result="fail",
                scope_coverage="full",
            )
            self.assertEqual(blocked_receipt.returncode, 0, blocked_receipt.stdout)
            blocking = completion(db, repo, task_id, check=True)
            self.assertEqual(
                payload(blocking)["data"]["blocking_codes"],
                ["verification_receipt_blocking"],
            )

            seed_review_evidence(db, task_id)
            qualifying = add_receipt(db, repo, task_id, 2)
            self.assertEqual(qualifying.returncode, 0, qualifying.stdout)
            receipt_id = payload(qualifying)["data"]["receipt"][
                "verification_receipt_id"
            ]
            ready = completion(db, repo, task_id, check=True)
            self.assertTrue(payload(ready)["data"]["ready"])
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                cycle = connection.execute(
                    """
                    SELECT verification_basis_version,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(cycle[0], 1)
            self.assertEqual(
                cycle[1],
                verification_expectation_digest(DEFAULT_VERIFICATION),
            )
            self.assertEqual(cycle[2], receipt_id)

            shown = payload(
                show_task(
                    db,
                    repo,
                    task_id,
                    json_output=True,
                )
            )
            public_cycle = shown["data"]["completion_history"]["cycles"][0]
            self.assertNotIn("verification_basis_version", public_cycle)
            self.assertNotIn("verification_expectation_digest", public_cycle)
            self.assertNotIn("verification_receipt_id", public_cycle)

    def test_v16_migration_preserves_rows_without_synthesizing_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            target, task_id = initialize_v16_fixture(Path(temp))
            with closing(connect(target.db_path)) as connection:
                task_columns = tuple(
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(tasks)"
                    ).fetchall()
                )
                cycle_columns = tuple(
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(task_completion_cycles)"
                    ).fetchall()
                )
                task_before = tuple(
                    connection.execute(
                        "SELECT * FROM tasks ORDER BY task_id"
                    ).fetchall()
                )
                cycle_before = tuple(
                    connection.execute(
                        "SELECT * FROM task_completion_cycles ORDER BY rowid"
                    ).fetchall()
                )
                event_before = tuple(
                    connection.execute(
                        "SELECT * FROM task_events ORDER BY rowid"
                    ).fetchall()
                )

                apply_verification_receipts_migration(connection)

                self.assertEqual(current_schema_version(connection), 17)
                self.assertEqual(
                    connection.execute(
                        "SELECT name FROM schema_migrations WHERE version = 17"
                    ).fetchone()[0],
                    "verification_receipts",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM verification_receipts"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            f"SELECT {', '.join(task_columns)} "
                            "FROM tasks ORDER BY task_id"
                        ).fetchall()
                    ),
                    task_before,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            f"SELECT {', '.join(cycle_columns)} "
                            "FROM task_completion_cycles ORDER BY rowid"
                        ).fetchall()
                    ),
                    cycle_before,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            "SELECT * FROM task_events ORDER BY rowid"
                        ).fetchall()
                    ),
                    event_before,
                )
                migrated_cycle = connection.execute(
                    """
                    SELECT verification_basis_version,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                self.assertEqual(tuple(migrated_cycle), (0, None, None))

                changes_before_reentry = connection.total_changes
                apply_verification_receipts_migration(connection)
                self.assertEqual(connection.total_changes, changes_before_reentry)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version = 17"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM verification_receipts"
                    ).fetchone()[0],
                    0,
                )

    def test_v17_migration_rolls_back_every_injected_stage_and_reenters(self):
        failure_stages = (
            "after_receipt_schema",
            "after_cycle_columns",
            "after_triggers",
            "after_marker",
            "before_commit",
        )
        with tempfile.TemporaryDirectory() as temp:
            for stage in failure_stages:
                with self.subTest(stage=stage):
                    target, task_id = initialize_v16_fixture(
                        Path(temp) / stage
                    )
                    with closing(connect(target.db_path)) as connection:
                        cycle_before = tuple(
                            connection.execute(
                                "SELECT * FROM task_completion_cycles ORDER BY rowid"
                            ).fetchall()
                        )
                        with self.assertRaises(StorageError) as failure:
                            apply_verification_receipts_migration(
                                connection,
                                fail_stage=stage,
                            )
                        self.assertEqual(failure.exception.code, "internal_error")
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(current_schema_version(connection), 16)
                        self.assertIsNone(
                            connection.execute(
                                """
                                SELECT name FROM sqlite_master
                                 WHERE type = 'table'
                                   AND name = 'verification_receipts'
                                """
                            ).fetchone()
                        )
                        cycle_columns = {
                            str(row["name"])
                            for row in connection.execute(
                                "PRAGMA table_info(task_completion_cycles)"
                            ).fetchall()
                        }
                        self.assertNotIn(
                            "verification_basis_version",
                            cycle_columns,
                        )
                        self.assertEqual(
                            tuple(
                                connection.execute(
                                    "SELECT * FROM task_completion_cycles ORDER BY rowid"
                                ).fetchall()
                            ),
                            cycle_before,
                        )

                        apply_verification_receipts_migration(connection)
                        self.assertEqual(current_schema_version(connection), 17)
                        self.assertEqual(
                            tuple(
                                connection.execute(
                                    """
                                    SELECT verification_basis_version,
                                           verification_expectation_digest,
                                           verification_receipt_id
                                      FROM task_completion_cycles
                                     WHERE task_id = ?
                                    """,
                                    (task_id,),
                                ).fetchone()
                            ),
                            (0, None, None),
                        )
                        apply_verification_receipts_migration(connection)
                        self.assertEqual(
                            connection.execute(
                                "SELECT COUNT(*) FROM schema_migrations WHERE version = 17"
                            ).fetchone()[0],
                            1,
                        )

    def test_writer_lock_reread_rejects_concurrent_target_drift_without_row(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)
            target = target_for(db, repo)
            actual_begin = storage_service.begin_initialized_write

            def drift_then_lock(connection, database_target):
                with closing(sqlite3.connect(db)) as concurrent:
                    with concurrent:
                        concurrent.execute(
                            """
                            UPDATE tasks
                               SET review_target_value = ?,
                                   review_target_generation = ?
                             WHERE task_id = ?
                            """,
                            (FINGERPRINT_B, generation + 1, task_id),
                        )
                return actual_begin(connection, database_target)

            with closing(connect_initialized(target)) as connection:
                with mock.patch(
                    "task_governance_tool.verification_receipts.begin_initialized_write",
                    side_effect=drift_then_lock,
                ):
                    with self.assertRaises(VerificationReceiptError) as stale:
                        with connection:
                            add_verification_receipt(
                                connection,
                                target.project,
                                task_id,
                                command_label="concurrent focused unittest",
                                result="pass",
                                duration_ms=10,
                                scope_coverage="full",
                                expected_target_generation=generation,
                                database_target=target,
                            )
            self.assertEqual(stale.exception.code, "verification_basis_stale")
            self.assertEqual(table_count(db, "verification_receipts"), 0)
            with closing(sqlite3.connect(db)) as connection:
                current_target = connection.execute(
                    """
                    SELECT review_target_value, review_target_generation
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(current_target, (FINGERPRINT_B, generation + 1))

    def test_receipt_rows_are_immutable_and_unique_per_target_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)
            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            receipt_id = payload(recorded)["data"]["receipt"][
                "verification_receipt_id"
            ]

            for statement, expected_message in (
                (
                    "UPDATE verification_receipts "
                    "SET command_label = 'changed' "
                    "WHERE verification_receipt_id = ?",
                    "immutable_verification_receipt",
                ),
                (
                    "DELETE FROM verification_receipts "
                    "WHERE verification_receipt_id = ?",
                    "immutable_verification_receipt",
                ),
            ):
                with self.subTest(statement=statement):
                    with closing(sqlite3.connect(db)) as connection:
                        with self.assertRaises(sqlite3.IntegrityError) as rejected:
                            connection.execute(statement, (receipt_id,))
                        connection.rollback()
                    self.assertIn(expected_message, str(rejected.exception))
                    self.assertEqual(table_count(db, "verification_receipts"), 1)

            duplicate_id = (
                "tg_verification_receipt_0000000000000000"
                if receipt_id != "tg_verification_receipt_0000000000000000"
                else "tg_verification_receipt_1111111111111111"
            )
            with closing(connect(db)) as connection:
                with self.assertRaises(sqlite3.IntegrityError) as duplicate:
                    connection.execute(
                        """
                        INSERT INTO verification_receipts(
                          verification_receipt_id, project_id, task_id,
                          contract_revision, verification_expectation_digest,
                          command_label, result, duration_ms, scope_coverage,
                          target_kind, target_value, target_base_revision,
                          target_generation, created_at
                        )
                        SELECT ?, project_id, task_id, contract_revision,
                               verification_expectation_digest,
                               'duplicate aggregate', result, duration_ms,
                               scope_coverage, target_kind, target_value,
                               target_base_revision, target_generation, created_at
                          FROM verification_receipts
                         WHERE verification_receipt_id = ?
                        """,
                        (duplicate_id, receipt_id),
                    )
                connection.rollback()
            self.assertIn("UNIQUE constraint failed", str(duplicate.exception))
            self.assertEqual(table_count(db, "verification_receipts"), 1)

    def test_raw_wrong_current_digest_fails_storage_and_task_show_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)
            receipt_id = "tg_verification_receipt_deadbeefdeadbeef"
            wrong_digest = "f" * 64

            with closing(connect(db)) as connection:
                basis = connection.execute(
                    """
                    SELECT project_id, current_contract_revision,
                           review_target_kind, review_target_value,
                           review_target_base_revision
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                parameters = (
                    receipt_id,
                    basis["project_id"],
                    task_id,
                    basis["current_contract_revision"],
                    wrong_digest,
                    "Corrupt direct insert",
                    basis["review_target_kind"],
                    basis["review_target_value"],
                    basis["review_target_base_revision"],
                    generation,
                )
                statement = """
                    INSERT INTO verification_receipts(
                      verification_receipt_id, project_id, task_id,
                      contract_revision, verification_expectation_digest,
                      command_label, result, duration_ms, scope_coverage,
                      target_kind, target_value, target_base_revision,
                      target_generation, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pass', 1, 'full',
                              ?, ?, ?, ?, '2026-08-01T15:00:00Z')
                """
                with connection:
                    connection.execute(statement, parameters)

                with self.assertRaises(StorageError) as stored:
                    storage_service.validate_verification_receipt_storage(
                        connection
                    )
                self.assertEqual(
                    stored.exception.code,
                    "invalid_verification_evidence",
                )

            shown = show_task(db, repo, task_id, json_output=True)
            self.assertEqual(shown.returncode, 2, shown.stdout)
            shown_payload = payload(shown)
            self.assertEqual(
                shown_payload["errors"][0],
                {
                    "code": "invalid_verification_evidence",
                    "message": "stored verification evidence is inconsistent",
                },
            )
            self.assertIsNone(shown_payload["data"]["verification_evidence"])

    def test_raw_command_line_label_fails_storage_and_task_show_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)

            with closing(connect(db)) as connection:
                basis = connection.execute(
                    """
                    SELECT project_id, verification,
                           current_contract_revision,
                           review_target_kind, review_target_value,
                           review_target_base_revision
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                with connection:
                    connection.execute(
                        """
                        INSERT INTO verification_receipts(
                          verification_receipt_id, project_id, task_id,
                          contract_revision, verification_expectation_digest,
                          command_label, result, duration_ms, scope_coverage,
                          target_kind, target_value, target_base_revision,
                          target_generation, created_at
                        ) VALUES (
                          'tg_verification_receipt_feedfacefeedface', ?, ?, ?, ?,
                          'python -m pytest tests -q', 'pass', 1, 'full',
                          ?, ?, ?, ?, '2026-08-01T15:01:00Z'
                        )
                        """,
                        (
                            basis["project_id"],
                            task_id,
                            basis["current_contract_revision"],
                            verification_expectation_digest(
                                basis["verification"]
                            ),
                            basis["review_target_kind"],
                            basis["review_target_value"],
                            basis["review_target_base_revision"],
                            generation,
                        ),
                    )
                with self.assertRaises(StorageError) as stored:
                    storage_service.validate_verification_receipt_storage(
                        connection
                    )
                self.assertEqual(
                    stored.exception.code,
                    "invalid_verification_evidence",
                )

            shown = show_task(db, repo, task_id, json_output=True)
            self.assertEqual(shown.returncode, 2, shown.stdout)
            self.assertEqual(
                payload(shown)["errors"][0]["code"],
                "invalid_verification_evidence",
            )
            self.assertIsNone(
                payload(shown)["data"]["verification_evidence"]
            )

    def test_raw_receipt_for_empty_current_expectation_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Empty expectation corruption",
                verification="",
            )
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)

            with closing(connect(db)) as connection:
                basis = connection.execute(
                    """
                    SELECT project_id, verification,
                           current_contract_revision,
                           review_target_kind, review_target_value,
                           review_target_base_revision
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_verification_receipts_locked_basis_insert'
                    """
                ).fetchone()[0]
                with connection:
                    connection.execute(
                        "DROP TRIGGER trg_verification_receipts_locked_basis_insert"
                    )
                    connection.execute(
                        """
                        INSERT INTO verification_receipts(
                          verification_receipt_id, project_id, task_id,
                          contract_revision, verification_expectation_digest,
                          command_label, result, duration_ms, scope_coverage,
                          target_kind, target_value, target_base_revision,
                          target_generation, created_at
                        ) VALUES (
                          'tg_verification_receipt_0123456789abcdef', ?, ?, ?, ?,
                          'Corrupt empty expectation', 'pass', 1, 'full',
                          ?, ?, ?, ?, '2026-08-01T15:02:00Z'
                        )
                        """,
                        (
                            basis["project_id"],
                            task_id,
                            basis["current_contract_revision"],
                            verification_expectation_digest(
                                basis["verification"]
                            ),
                            basis["review_target_kind"],
                            basis["review_target_value"],
                            basis["review_target_base_revision"],
                            generation,
                        ),
                    )
                    connection.execute(trigger_sql)

                with self.assertRaises(StorageError) as stored:
                    storage_service.validate_verification_receipt_storage(
                        connection
                    )
                self.assertEqual(
                    stored.exception.code,
                    "invalid_verification_evidence",
                )

            shown = show_task(db, repo, task_id, json_output=True)
            self.assertEqual(shown.returncode, 2, shown.stdout)
            self.assertEqual(
                payload(shown)["errors"][0]["code"],
                "invalid_verification_evidence",
            )
            self.assertIsNone(
                payload(shown)["data"]["verification_evidence"]
            )

    def test_viewer_accepts_valid_v1_link_and_rejects_corrupt_receipt_link(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            seed_review_evidence(db, task_id)
            recorded = add_receipt(db, repo, task_id, 1)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            target = target_for(db, repo)

            with closing(connect_snapshot_readonly(db)) as connection:
                valid = build_viewer_snapshot(
                    connection,
                    target,
                    generated_at="2026-08-01T00:00:00Z",
                ).snapshot
            self.assertEqual(valid["snapshot_version"], 4)
            self.assertEqual(valid["source_schema_version"], 17)
            projected = next(
                item for item in valid["tasks"] if item["task_id"] == task_id
            )
            self.assertEqual(
                projected["completion_history"]["cycles"][0]["origin"],
                "native_done",
            )
            serialized = json.dumps(valid)
            self.assertNotIn("verification_receipt_id", serialized)
            self.assertNotIn("verification_evidence", projected)

            with closing(sqlite3.connect(db)) as connection:
                with connection:
                    trigger_sql = connection.execute(
                        """
                        SELECT sql FROM sqlite_master
                         WHERE type = 'trigger'
                           AND name = 'trg_verification_receipts_no_update'
                        """
                    ).fetchone()[0]
                    connection.execute(
                        "DROP TRIGGER trg_verification_receipts_no_update"
                    )
                    connection.execute(
                        "UPDATE verification_receipts SET result = 'fail'"
                    )
                    connection.execute(trigger_sql)

            with closing(connect_snapshot_readonly(db)) as connection:
                with self.assertRaises(StorageError) as corrupt:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(
                corrupt.exception.code,
                "completion_history_inconsistent",
            )

    def test_empty_verification_native_cycle_uses_v1_digest_and_null_link(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Empty native verification",
                verification="",
            )
            task_id = task["task_id"]
            seed_review_evidence(db, task_id)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                cycle = connection.execute(
                    """
                    SELECT verification_basis_version,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(
                cycle,
                (
                    1,
                    verification_expectation_digest(""),
                    None,
                ),
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)
            gate = payload(
                show_task(db, repo, task_id, json_output=True)
            )["data"]["verification_evidence"]["gate"]
            self.assertEqual(
                gate,
                {
                    "required": False,
                    "satisfied": True,
                    "blocking_code": None,
                    "qualifying_receipt_id": None,
                },
            )

    def test_whitespace_only_verification_binds_exact_digest_without_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            exact_verification = " \t "
            task = add_task(
                db,
                repo,
                title="Whitespace-only verification",
                verification=exact_verification,
            )
            task_id = task["task_id"]
            seed_review_evidence(db, task_id)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                cycle = connection.execute(
                    """
                    SELECT verification_expectation,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(
                cycle,
                (
                    "unspecified",
                    verification_expectation_digest(exact_verification),
                    None,
                ),
            )
            evidence = payload(
                show_task(db, repo, task_id, json_output=True)
            )["data"]["verification_evidence"]
            self.assertEqual(evidence["expectation"], exact_verification)
            self.assertEqual(
                evidence["gate"],
                {
                    "required": False,
                    "satisfied": True,
                    "blocking_code": None,
                    "qualifying_receipt_id": None,
                },
            )

            target = target_for(db, repo)
            with closing(connect_snapshot_readonly(db)) as connection:
                build_viewer_snapshot(connection, target)
            with closing(sqlite3.connect(db)) as connection:
                with connection:
                    connection.execute(
                        "UPDATE tasks SET verification = ? WHERE task_id = ?",
                        (" \n ", task_id),
                    )
            with closing(connect_snapshot_readonly(db)) as connection:
                with self.assertRaises(StorageError) as corrupt_viewer:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(
                corrupt_viewer.exception.code,
                "completion_history_inconsistent",
            )

    def test_empty_verification_cycle_digest_corruption_fails_task_show_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Corrupt empty verification basis",
                verification="",
            )
            task_id = task["task_id"]
            seed_review_evidence(db, task_id)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                with connection:
                    trigger_sql = connection.execute(
                        """
                        SELECT sql FROM sqlite_master
                         WHERE type = 'trigger'
                           AND name = 'trg_task_completion_cycles_no_update'
                        """
                    ).fetchone()[0]
                    connection.execute(
                        "DROP TRIGGER trg_task_completion_cycles_no_update"
                    )
                    connection.execute(
                        """
                        UPDATE task_completion_cycles
                           SET verification_expectation_digest = ?
                         WHERE task_id = ?
                        """,
                        ("f" * 64, task_id),
                    )
                    connection.execute(trigger_sql)

            shown = show_task(db, repo, task_id, json_output=True)
            self.assertEqual(shown.returncode, 2, shown.stdout)
            shown_payload = payload(shown)
            self.assertEqual(
                shown_payload["errors"][0],
                {
                    "code": "completion_history_inconsistent",
                    "message": "stored completion history is inconsistent",
                },
            )
            self.assertIsNone(shown_payload["data"]["verification_evidence"])

            target = target_for(db, repo)
            with closing(connect_snapshot_readonly(db)) as connection:
                with self.assertRaises(StorageError) as corrupt_viewer:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(
                corrupt_viewer.exception.code,
                "completion_history_inconsistent",
            )

    def test_reopen_rejects_done_task_verification_digest_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Corrupt done verification expectation",
            )
            task_id = task["task_id"]
            seed_review_evidence(db, task_id)
            with closing(sqlite3.connect(db)) as connection:
                generation = connection.execute(
                    "SELECT review_target_generation FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                with connection:
                    connection.execute(
                        "UPDATE tasks SET verification = ? WHERE task_id = ?",
                        (DEFAULT_VERIFICATION + " changed", task_id),
                    )

            reopened = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--status",
                "in_progress",
                "--reopen-reason",
                "Acceptance changed",
                "--json",
            )
            self.assertEqual(reopened.returncode, 2, reopened.stdout)
            self.assertEqual(
                payload(reopened)["errors"][0],
                {
                    "code": "completion_history_inconsistent",
                    "message": "stored completion history is inconsistent",
                },
            )
            with closing(sqlite3.connect(db)) as connection:
                stored = connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                cycle_count = connection.execute(
                    "SELECT COUNT(*) FROM task_completion_cycles WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            self.assertEqual(stored[0], "done")
            self.assertEqual(cycle_count, 1)

    def test_missing_receipt_precedes_insufficient_and_blocking_review_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Receipt ordering audit",
                review_tier=2,
            )
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)

            insufficient = completion(db, repo, task_id)
            self.assertEqual(insufficient.returncode, 1, insufficient.stdout)
            self.assertEqual(
                payload(insufficient)["errors"][0]["code"],
                "verification_receipt_required",
            )

            changes_requested = run_taskgov(
                "review",
                "receipt",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--reviewer",
                "ordering-auditor",
                "--kind",
                "independent",
                "--verdict",
                "changes_requested",
                "--summary",
                "Current target needs changes",
                "--json",
            )
            self.assertEqual(
                changes_requested.returncode,
                0,
                changes_requested.stdout,
            )
            review_evidence = payload(
                show_task(db, repo, task_id, json_output=True)
            )["data"]["review_evidence"]
            self.assertEqual(
                review_evidence["counts"][
                    "changes_requested_current_generation"
                ],
                1,
            )
            self.assertEqual(
                review_evidence["target"]["generation"],
                generation,
            )

            blocking = completion(db, repo, task_id)
            self.assertEqual(blocking.returncode, 1, blocking.stdout)
            self.assertEqual(
                payload(blocking)["errors"][0]["code"],
                "verification_receipt_required",
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)

    def test_contract_revision_retires_receipt_and_advances_target_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            added = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Contract-bound receipt",
                "--status",
                "in_progress",
                "--review-tier",
                "0",
                "--verification",
                DEFAULT_VERIFICATION,
                "--contract-scope",
                "Initial exact scope",
                "--contract-acceptance",
                "Initial exact acceptance",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stdout)
            task_id = payload(added)["data"]["task"]["task_id"]
            generation = set_target(db, repo, task_id)
            self.assertEqual(generation, 1)
            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            old_receipt = payload(recorded)["data"]["receipt"]
            self.assertEqual(old_receipt["contract_revision"], 1)

            revised = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--contract-scope",
                "Revised exact scope",
                "--contract-acceptance",
                "Revised exact acceptance",
                "--contract-authority-ref",
                f"user_instruction:{task_id}:2",
                "--contract-change-reason",
                "User revised the accepted boundary",
                "--json",
            )
            self.assertEqual(revised.returncode, 0, revised.stdout)
            revised_task = payload(revised)["data"]["task"]
            self.assertEqual(revised_task["status"], "in_progress")
            with closing(sqlite3.connect(db)) as connection:
                current_basis = connection.execute(
                    """
                    SELECT current_contract_revision,
                           review_target_kind, review_target_value,
                           review_target_base_revision,
                           review_target_generation
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(current_basis, (2, "", "", "", 2))

            evidence = payload(
                show_task(db, repo, task_id, json_output=True)
            )["data"]["verification_evidence"]
            self.assertEqual(evidence["contract_revision"], 2)
            self.assertIsNone(evidence["source_revision"])
            self.assertEqual(
                evidence["counts"],
                {
                    "receipts_total": 1,
                    "receipts_exact_current": 0,
                    "qualifying_exact_current": 0,
                    "blocking_exact_current": 0,
                },
            )
            self.assertEqual(
                evidence["gate"]["blocking_code"],
                "review_target_required",
            )
            self.assertEqual(evidence["recent_receipts"], [old_receipt])

    def test_v17_reentry_rejects_altered_completion_cycle_check_constraint(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            with closing(sqlite3.connect(db)) as connection:
                table_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'table'
                       AND name = 'task_completion_cycles'
                    """
                ).fetchone()[0]
                altered_sql = table_sql.replace(
                    "CHECK (verification_basis_version IN (0, 1))",
                    "CHECK (verification_basis_version IN (0, 1, 2))",
                )
                self.assertNotEqual(altered_sql, table_sql)
                connection.execute("PRAGMA writable_schema = ON")
                connection.execute(
                    """
                    UPDATE sqlite_master SET sql = ?
                     WHERE type = 'table'
                       AND name = 'task_completion_cycles'
                    """,
                    (altered_sql,),
                )
                connection.execute("PRAGMA writable_schema = OFF")
                schema_cookie = int(
                    connection.execute("PRAGMA schema_version").fetchone()[0]
                )
                connection.execute(
                    f"PRAGMA schema_version = {schema_cookie + 1}"
                )
                connection.commit()

            with closing(connect(db)) as connection:
                with self.assertRaises(StorageError) as rejected:
                    apply_verification_receipts_migration(connection)
            self.assertEqual(rejected.exception.code, "project_state_unreadable")

    def test_semantic_verification_edit_invalidates_target_and_keeps_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            seed_review_evidence(db, task_id)
            recorded = add_receipt(db, repo, task_id, 1)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)

            evidence = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-1",
                "--completion-evidence-reason",
                "Published by the governed release process",
                "--external-revision-approved",
                "--json",
            )
            self.assertEqual(evidence.returncode, 0, evidence.stdout)
            pending = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--status",
                "review_pending",
                "--json",
            )
            self.assertEqual(pending.returncode, 0, pending.stdout)

            edited = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--verification",
                "python -m unittest tests.test_verification_receipts -v",
                "--json",
            )
            self.assertEqual(edited.returncode, 0, edited.stdout)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                updated = dict(
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                )
            self.assertEqual(updated["status"], "in_progress")
            self.assertEqual(updated["completion_evidence_kind"], "none")
            self.assertEqual(updated["completion_evidence_revision"], "")
            self.assertEqual(updated["review_target_kind"], "")
            self.assertEqual(updated["review_target_value"], "")
            self.assertEqual(updated["review_target_base_revision"], "")
            self.assertEqual(updated["review_target_generation"], 2)
            self.assertEqual(table_count(db, "verification_receipts"), 1)
            self.assertEqual(table_count(db, "review_receipts"), 1)

            shown = payload(
                show_task(
                    db,
                    repo,
                    task_id,
                    json_output=True,
                )
            )["data"]["verification_evidence"]
            self.assertEqual(shown["counts"]["receipts_total"], 1)
            self.assertEqual(shown["counts"]["receipts_exact_current"], 0)
            self.assertEqual(
                shown["gate"]["blocking_code"],
                "review_target_required",
            )
            self.assertIsNone(shown["source_revision"])

    def test_semantic_verification_edit_preserves_explicit_safe_status(self):
        cases = (
            ("in_progress", (), ""),
            ("paused", ("--pause-reason", "Awaiting local input"), ""),
            ("blocked", ("--blocked-reason", "Dependency unavailable"), "Dependency unavailable"),
            ("cancelled", (), ""),
        )
        for requested_status, extra_args, expected_blocked_reason in cases:
            with (
                self.subTest(status=requested_status),
                tempfile.TemporaryDirectory() as temp,
            ):
                repo, db = initialize(Path(temp))
                task = add_task(db, repo)
                task_id = task["task_id"]
                seed_review_evidence(db, task_id)
                pending = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                    "--status",
                    "review_pending",
                    "--json",
                )
                self.assertEqual(pending.returncode, 0, pending.stdout)

                edited = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                    "--verification",
                    "Focused verification after target invalidation",
                    "--status",
                    requested_status,
                    *extra_args,
                    "--json",
                )
                self.assertEqual(edited.returncode, 0, edited.stdout)
                updated = payload(edited)["data"]["task"]
                self.assertEqual(updated["status"], requested_status)
                self.assertEqual(
                    updated["blocked_reason"],
                    expected_blocked_reason,
                )
                with closing(sqlite3.connect(db)) as connection:
                    stored = connection.execute(
                        """
                        SELECT completion_evidence_kind,
                               review_target_kind,
                               review_target_generation
                          FROM tasks WHERE task_id = ?
                        """,
                        (task_id,),
                    ).fetchone()
                self.assertEqual(stored, ("none", "", 2))

    def test_semantic_verification_edit_rejects_freshness_conflicts(self):
        cases = (
            (
                (
                    "--status",
                    "ready",
                ),
                "invalid_status_transition",
            ),
            (
                (
                    "--status",
                    "review_pending",
                ),
                "invalid_status_transition",
            ),
            (
                (
                    "--completion-evidence-kind",
                    "external_revision",
                    "--completion-revision",
                    "release-2",
                    "--completion-evidence-reason",
                    "Governed publication",
                    "--external-revision-approved",
                ),
                "completion_evidence_conflict",
            ),
            (
                (
                    "--status",
                    "done",
                    "--verification-complete",
                    "--review-complete",
                    "--commit-not-required",
                ),
                "invalid_status_transition",
            ),
        )
        for extra_args, expected_code in cases:
            with (
                self.subTest(code=expected_code),
                tempfile.TemporaryDirectory() as temp,
            ):
                repo, db = initialize(Path(temp))
                task = add_task(db, repo)
                task_id = task["task_id"]
                seed_review_evidence(db, task_id)
                receipt = add_receipt(db, repo, task_id, 1)
                self.assertEqual(receipt.returncode, 0, receipt.stdout)
                pending = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                    "--status",
                    "review_pending",
                    "--json",
                )
                self.assertEqual(pending.returncode, 0, pending.stdout)

                rejected = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                    "--verification",
                    "Conflicting changed verification",
                    *extra_args,
                    "--json",
                )
                self.assertEqual(rejected.returncode, 1, rejected.stdout)
                self.assertEqual(
                    payload(rejected)["errors"][0]["code"],
                    expected_code,
                )
                with closing(sqlite3.connect(db)) as connection:
                    stored = connection.execute(
                        """
                        SELECT status, verification,
                               review_target_generation, review_target_kind
                          FROM tasks WHERE task_id = ?
                        """,
                        (task_id,),
                    ).fetchone()
                self.assertEqual(
                    stored,
                    (
                        "review_pending",
                        DEFAULT_VERIFICATION,
                        1,
                        "diff_fingerprint",
                    ),
                )


if __name__ == "__main__":
    unittest.main()
