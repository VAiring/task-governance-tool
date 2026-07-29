import queue
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "taskgov.py"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.relocation import (  # noqa: E402
    RelocationContext,
    decode_relocation_token,
    encode_relocation_token,
)
from task_governance_tool.storage import (  # noqa: E402
    SQLITE_INT64_MAX,
    DatabaseTarget,
    StorageError,
    apply_completion_cycle_history_migration,
    connect,
    insert_completion_cycle_locked,
    read_completion_histories_for_tasks,
    read_completion_history,
    read_latest_completion_cycle,
    resolve_database_target,
    select_completion_gate_basis_locked,
    validate_completion_cycle_storage,
)
from task_governance_tool.tasks import add_task  # noqa: E402
from tests.test_project_identity_bindings import (  # noqa: E402
    create_v13_target,
    migrate_to_v14,
)


PROJECT_UUID = "tg_project_00112233445546778899aabbccddeeff"
OLD_HASH = "1" * 64
NEW_HASH = "2" * 64
EXTERNAL_REVISION = "external-revision-1"
COMPLETED_AT = "2026-07-30T04:00:00Z"

CYCLE_COLUMNS = (
    "completion_cycle_id",
    "project_id",
    "task_id",
    "saved_cycle_ordinal",
    "origin",
    "completeness",
    "completed_at",
    "recorded_at",
    "contract_revision",
    "review_tier",
    "verification_expectation",
    "verification_attestation",
    "completion_evidence_kind",
    "completion_evidence_revision",
    "completion_evidence_reason",
    "external_revision_approved",
    "completion_commit_required",
    "completion_commit_hash",
    "review_target_kind",
    "review_target_value",
    "review_target_base_revision",
    "review_target_generation",
    "gate_basis_version",
    "review_basis_kind",
    "required_independent_passes",
    "qualifying_independent_passes",
    "changes_requested_count",
    "open_high_count",
    "open_medium_count",
    "fresh_review_required_count",
    "qualifying_receipt_id_1",
    "qualifying_receipt_id_2",
)


def make_v15_target(root: Path) -> DatabaseTarget:
    repo = root / "repo"
    repo.mkdir()
    target = resolve_database_target(
        repo=repo,
        db=root / "taskgov.sqlite",
        script_path=SCRIPT_PATH,
    )
    create_v13_target(target)
    migrate_to_v14(target)
    with closing(connect(target.db_path)) as connection:
        apply_completion_cycle_history_migration(connection)
    return target


def add_ready_task(target: DatabaseTarget, title: str = "Repository task") -> str:
    with closing(connect(target.db_path)) as connection:
        task_id = add_task(
            connection,
            target.project,
            title=title,
        ).task["task_id"]
        connection.commit()
    return str(task_id)


def make_captureless_done(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    completed_at: str = COMPLETED_AT,
) -> None:
    connection.execute(
        """
        UPDATE tasks
           SET status = 'done',
               completed_at = ?,
               updated_at = ?,
               completion_evidence_kind = 'commit_not_required',
               completion_evidence_revision = '',
               completion_evidence_reason = '',
               external_revision_approved = 0,
               completion_commit_required = 0,
               completion_commit_hash = ''
         WHERE project_id = ? AND task_id = ?
        """,
        (completed_at, completed_at, project_id, task_id),
    )


def cycle_values_from_task(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    cycle_id: str,
    ordinal: int,
    recorded_at: str,
) -> dict[str, object]:
    row = connection.execute(
        "SELECT * FROM tasks WHERE project_id = ? AND task_id = ?",
        (project_id, task_id),
    ).fetchone()
    if row is None:
        raise AssertionError("test Task is missing")
    return {
        "completion_cycle_id": cycle_id,
        "project_id": project_id,
        "task_id": task_id,
        "saved_cycle_ordinal": ordinal,
        "origin": "legacy_current_done",
        "completeness": "partial",
        "completed_at": row["completed_at"],
        "recorded_at": recorded_at,
        "contract_revision": row["current_contract_revision"],
        "review_tier": row["review_tier"],
        "verification_expectation": (
            "specified" if str(row["verification"]).strip() else "unspecified"
        ),
        "verification_attestation": None,
        "completion_evidence_kind": row["completion_evidence_kind"],
        "completion_evidence_revision": row["completion_evidence_revision"],
        "completion_evidence_reason": row["completion_evidence_reason"],
        "external_revision_approved": row["external_revision_approved"],
        "completion_commit_required": row["completion_commit_required"],
        "completion_commit_hash": row["completion_commit_hash"],
        "review_target_kind": row["review_target_kind"],
        "review_target_value": row["review_target_value"],
        "review_target_base_revision": row["review_target_base_revision"],
        "review_target_generation": row["review_target_generation"],
        "gate_basis_version": 0,
        "review_basis_kind": "unknown",
        "required_independent_passes": None,
        "qualifying_independent_passes": None,
        "changes_requested_count": None,
        "open_high_count": None,
        "open_medium_count": None,
        "fresh_review_required_count": None,
        "qualifying_receipt_id_1": None,
        "qualifying_receipt_id_2": None,
    }


def insert_cycle_row(
    connection: sqlite3.Connection,
    values: dict[str, object],
) -> None:
    columns = ", ".join(CYCLE_COLUMNS)
    parameters = ", ".join(f":{column}" for column in CYCLE_COLUMNS)
    connection.execute(
        f"INSERT INTO task_completion_cycles({columns}) VALUES ({parameters})",
        values,
    )


def insert_review_receipt(
    connection: sqlite3.Connection,
    *,
    receipt_id: str,
    project_id: str,
    task_id: str,
    reviewer_key: str,
    receipt_kind: str,
    verdict: str,
    target_kind: str,
    target_value: str,
    target_generation: int,
    user_approved: int = 0,
    summary: str = "",
) -> None:
    connection.execute(
        """
        INSERT INTO review_receipts(
          review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
          verdict, target_kind, target_value, target_base_revision,
          target_generation, summary, user_approved, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?,
                  '2026-07-30T04:10:00Z')
        """,
        (
            receipt_id,
            task_id,
            project_id,
            reviewer_key,
            receipt_kind,
            verdict,
            target_kind,
            target_value,
            target_generation,
            summary,
            user_approved,
        ),
    )


def seed_native_cycle(
    connection: sqlite3.Connection,
    *,
    target: DatabaseTarget,
    task_id: str,
    id_suffix: str,
    basis_kind: str = "independent_passes",
) -> tuple[str, str]:
    if len(id_suffix) != 16 or any(
        character not in "0123456789abcdef" for character in id_suffix
    ):
        raise AssertionError("native-cycle test suffix must be 16 lowercase hex")
    project_id = target.project.project_id
    receipt_id = f"tg_review_receipt_{id_suffix}"
    cycle_id = f"tg_completion_cycle_{id_suffix}"
    if basis_kind == "independent_passes":
        review_tier = 1
        receipt_kind = "independent"
        verdict = "pass"
        user_approved = 0
        summary = ""
        required = 1
        qualifying = 1
    elif basis_kind == "self_review_fallback":
        review_tier = 2
        receipt_kind = "self_review_fallback"
        verdict = "pass"
        user_approved = 1
        summary = ""
        required = 2
        qualifying = 0
    elif basis_kind == "not_required":
        review_tier = 0
        receipt_kind = "not_required"
        verdict = "not_required"
        user_approved = 0
        summary = "Mechanical review is not required."
        required = 0
        qualifying = 0
    else:
        raise AssertionError("unsupported native-cycle gate basis")
    make_captureless_done(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    connection.execute(
        """
        UPDATE tasks
           SET review_tier = ?,
               verification = 'offline checks passed',
               completion_evidence_kind = 'external_revision',
               completion_evidence_revision = ?,
               completion_evidence_reason = 'approved source',
               external_revision_approved = 1,
               completion_commit_required = 1,
               completion_commit_hash = ?,
               review_target_kind = 'external_revision',
               review_target_value = ?,
               review_target_base_revision = '',
               review_target_generation = 1
         WHERE project_id = ? AND task_id = ?
        """,
        (
            review_tier,
            EXTERNAL_REVISION,
            EXTERNAL_REVISION,
            EXTERNAL_REVISION,
            project_id,
            task_id,
        ),
    )
    insert_review_receipt(
        connection,
        receipt_id=receipt_id,
        project_id=project_id,
        task_id=task_id,
        reviewer_key="independent-reviewer",
        receipt_kind=receipt_kind,
        verdict=verdict,
        target_kind="external_revision",
        target_value=EXTERNAL_REVISION,
        target_generation=1,
        user_approved=user_approved,
        summary=summary,
    )
    values = cycle_values_from_task(
        connection,
        project_id=project_id,
        task_id=task_id,
        cycle_id=cycle_id,
        ordinal=1,
        recorded_at="2026-07-30T04:26:00Z",
    )
    values.update(
        {
            "origin": "native_done",
            "completeness": "complete",
            "verification_attestation": 1,
            "gate_basis_version": 1,
            "review_basis_kind": basis_kind,
            "required_independent_passes": required,
            "qualifying_independent_passes": qualifying,
            "changes_requested_count": 0,
            "open_high_count": 0,
            "open_medium_count": 0,
            "fresh_review_required_count": 0,
            "qualifying_receipt_id_1": receipt_id,
        }
    )
    insert_cycle_row(connection, values)
    connection.execute(
        """
        INSERT INTO task_events(
          task_event_id, task_id, project_id, event_type, summary,
          created_at, completion_cycle_id
        ) VALUES (
          ?, ?, ?, 'task_updated', 'Task completed.',
          '2026-07-30T04:26:00Z', ?
        )
        """,
        (f"tg_event_{id_suffix}", task_id, project_id, cycle_id),
    )
    return cycle_id, receipt_id


class CompletionCycleRepositoryTests(unittest.TestCase):
    def assert_sanitized_history_error(
        self,
        action,
        *,
        forbidden: tuple[str, ...] = (),
    ) -> None:
        with self.assertRaises(StorageError) as raised:
            action()
        self.assertEqual(
            raised.exception.code,
            "completion_history_inconsistent",
        )
        self.assertEqual(
            raised.exception.message,
            "stored completion history is inconsistent",
        )
        rendered = str(raised.exception)
        for value in forbidden:
            self.assertNotIn(value, rendered)

    def test_locked_partial_insert_and_bounded_readers_share_one_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v15_target(Path(tmp))
            task_id = add_ready_task(target)
            empty_task_id = add_ready_task(target, "Never completed")

            with closing(connect(target.db_path)) as connection:
                make_captureless_done(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                inserted = insert_completion_cycle_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    recorded_at="2026-07-30T04:20:00Z",
                )
                connection.commit()

                self.assertEqual(inserted.saved_cycle_ordinal, 1)
                self.assertEqual(inserted.origin, "legacy_current_done")
                self.assertEqual(inserted.completeness, "partial")
                self.assertIsNone(inserted.verification_attestation)
                self.assertEqual(inserted.gate_basis.version, 0)

                latest = read_latest_completion_cycle(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
                self.assertEqual(latest, inserted)

                history = read_completion_history(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
                self.assertEqual(history.total, 1)
                self.assertEqual(history.returned_count, 1)
                self.assertFalse(history.truncated)
                self.assertTrue(history.legacy_history_incomplete)
                self.assertEqual(history.cycles, (inserted,))

                batch = read_completion_histories_for_tasks(
                    connection,
                    project_id=target.project.project_id,
                    task_ids=(task_id, empty_task_id),
                )
                self.assertEqual(tuple(batch), (task_id, empty_task_id))
                self.assertEqual(batch[task_id], history)
                self.assertEqual(batch[empty_task_id].total, 0)
                self.assertEqual(batch[empty_task_id].cycles, ())
                self.assertTrue(
                    batch[empty_task_id].legacy_history_incomplete
                )

    def test_insert_participates_in_caller_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v15_target(Path(tmp))
            task_id = add_ready_task(target)
            with closing(connect(target.db_path)) as connection:
                make_captureless_done(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                insert_completion_cycle_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    recorded_at="2026-07-30T04:21:00Z",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_completion_cycles"
                    ).fetchone()[0],
                    1,
                )
                connection.rollback()
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_completion_cycles"
                    ).fetchone()[0],
                    0,
                )

    def test_insert_rejects_signed_64_bit_ordinal_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v15_target(Path(tmp))
            task_id = add_ready_task(target)
            with closing(connect(target.db_path)) as connection:
                make_captureless_done(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
                values = cycle_values_from_task(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    cycle_id="tg_completion_cycle_ffffffffffffffff",
                    ordinal=SQLITE_INT64_MAX,
                    recorded_at="2026-07-30T04:22:00Z",
                )
                insert_cycle_row(connection, values)
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                self.assert_sanitized_history_error(
                    lambda: insert_completion_cycle_locked(
                        connection,
                        project_id=target.project.project_id,
                        task_id=task_id,
                        recorded_at="2026-07-30T04:22:01Z",
                    ),
                    forbidden=(task_id,),
                )
                connection.rollback()
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_completion_cycles"
                    ).fetchone()[0],
                    1,
                )

    def test_two_writers_serialize_and_allocate_distinct_ordinals(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v15_target(Path(tmp))
            task_id = add_ready_task(target)
            with closing(connect(target.db_path)) as connection:
                make_captureless_done(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
                connection.commit()

            first_inserted = threading.Event()
            second_attempting = threading.Event()
            release_first = threading.Event()
            results: queue.Queue[tuple[str, int]] = queue.Queue()
            errors: queue.Queue[BaseException] = queue.Queue()

            def first_writer() -> None:
                try:
                    with closing(connect(target.db_path)) as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        cycle = insert_completion_cycle_locked(
                            connection,
                            project_id=target.project.project_id,
                            task_id=task_id,
                            recorded_at="2026-07-30T04:23:00Z",
                        )
                        results.put(("first", cycle.saved_cycle_ordinal))
                        first_inserted.set()
                        if not release_first.wait(5):
                            raise AssertionError("first writer release timed out")
                        connection.commit()
                except BaseException as exc:
                    errors.put(exc)
                    first_inserted.set()

            def second_writer() -> None:
                try:
                    if not first_inserted.wait(5):
                        raise AssertionError("first writer did not insert")
                    with closing(connect(target.db_path)) as connection:
                        second_attempting.set()
                        connection.execute("BEGIN IMMEDIATE")
                        cycle = insert_completion_cycle_locked(
                            connection,
                            project_id=target.project.project_id,
                            task_id=task_id,
                            recorded_at="2026-07-30T04:23:01Z",
                        )
                        results.put(("second", cycle.saved_cycle_ordinal))
                        connection.commit()
                except BaseException as exc:
                    errors.put(exc)
                    second_attempting.set()

            first = threading.Thread(target=first_writer)
            second = threading.Thread(target=second_writer)
            first.start()
            try:
                self.assertTrue(first_inserted.wait(5))
                second.start()
                self.assertTrue(second_attempting.wait(5))
            finally:
                release_first.set()
                first.join(10)
                if second.ident is not None:
                    second.join(10)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            if not errors.empty():
                raise errors.get()
            self.assertEqual(
                sorted(results.queue),
                [("first", 1), ("second", 2)],
            )
            with closing(connect(target.db_path)) as connection:
                self.assertEqual(
                    [
                        int(row[0])
                        for row in connection.execute(
                            """
                            SELECT saved_cycle_ordinal
                              FROM task_completion_cycles
                             ORDER BY saved_cycle_ordinal
                            """
                        )
                    ],
                    [1, 2],
                )

    def test_tier2_gate_selector_prefers_independent_passes_over_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v15_target(Path(tmp))
            task_id = add_ready_task(target)
            fallback_id = "tg_review_receipt_0000000000000001"
            reviewer_a_id = "tg_review_receipt_ffffffffffffffff"
            reviewer_b_id = "tg_review_receipt_0000000000000002"
            with closing(connect(target.db_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE tasks
                       SET review_tier = 2,
                           review_target_kind = 'external_revision',
                           review_target_value = ?,
                           review_target_base_revision = '',
                           review_target_generation = 1
                     WHERE project_id = ? AND task_id = ?
                    """,
                    (
                        EXTERNAL_REVISION,
                        target.project.project_id,
                        task_id,
                    ),
                )
                insert_review_receipt(
                    connection,
                    receipt_id=fallback_id,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    reviewer_key="fallback-reviewer",
                    receipt_kind="self_review_fallback",
                    verdict="pass",
                    target_kind="external_revision",
                    target_value=EXTERNAL_REVISION,
                    target_generation=1,
                    user_approved=1,
                )
                fallback_basis = select_completion_gate_basis_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
                self.assertEqual(
                    (
                        fallback_basis.kind,
                        fallback_basis.required_independent_passes,
                        fallback_basis.qualifying_independent_passes,
                        fallback_basis.qualifying_receipt_ids,
                    ),
                    ("self_review_fallback", 2, 0, (fallback_id,)),
                )

                insert_review_receipt(
                    connection,
                    receipt_id=reviewer_b_id,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    reviewer_key="reviewer-b",
                    receipt_kind="independent",
                    verdict="pass",
                    target_kind="external_revision",
                    target_value=EXTERNAL_REVISION,
                    target_generation=1,
                )
                insert_review_receipt(
                    connection,
                    receipt_id=reviewer_a_id,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    reviewer_key="reviewer-a",
                    receipt_kind="independent",
                    verdict="pass",
                    target_kind="external_revision",
                    target_value=EXTERNAL_REVISION,
                    target_generation=1,
                )
                independent_basis = select_completion_gate_basis_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
                self.assertEqual(
                    (
                        independent_basis.kind,
                        independent_basis.required_independent_passes,
                        independent_basis.qualifying_independent_passes,
                        independent_basis.qualifying_receipt_ids,
                    ),
                    (
                        "independent_passes",
                        2,
                        2,
                        (reviewer_a_id, reviewer_b_id),
                    ),
                )
                connection.rollback()

    def test_batch_reader_receipt_validation_does_not_scale_per_cycle(self):
        query_counts = []
        for task_count in (1, 3):
            with (
                self.subTest(task_count=task_count),
                tempfile.TemporaryDirectory() as tmp,
            ):
                target = make_v15_target(Path(tmp))
                task_ids = tuple(
                    add_ready_task(target, f"Native cycle {index}")
                    for index in range(task_count)
                )
                with closing(connect(target.db_path)) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    for index, task_id in enumerate(task_ids, start=1):
                        seed_native_cycle(
                            connection,
                            target=target,
                            task_id=task_id,
                            id_suffix=f"{index:016x}",
                        )
                    connection.commit()

                    traced: list[str] = []
                    connection.set_trace_callback(traced.append)
                    histories = read_completion_histories_for_tasks(
                        connection,
                        project_id=target.project.project_id,
                        task_ids=task_ids,
                    )
                    connection.set_trace_callback(None)
                    self.assertEqual(
                        [histories[task_id].total for task_id in task_ids],
                        [1] * task_count,
                    )
                    query_counts.append(
                        sum(
                            statement.lstrip().upper().startswith("SELECT")
                            for statement in traced
                        )
                    )
        self.assertLessEqual(query_counts[1], query_counts[0] + 1)

    def test_batch_reader_validates_fallback_and_not_required_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v15_target(Path(tmp))
            fallback_task = add_ready_task(target, "Fallback cycle")
            not_required_task = add_ready_task(target, "Not-required cycle")
            with closing(connect(target.db_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                seed_native_cycle(
                    connection,
                    target=target,
                    task_id=fallback_task,
                    id_suffix="4444444444444444",
                    basis_kind="self_review_fallback",
                )
                seed_native_cycle(
                    connection,
                    target=target,
                    task_id=not_required_task,
                    id_suffix="5555555555555555",
                    basis_kind="not_required",
                )
                connection.commit()
                histories = read_completion_histories_for_tasks(
                    connection,
                    project_id=target.project.project_id,
                    task_ids=(fallback_task, not_required_task),
                )
                self.assertEqual(
                    histories[fallback_task].cycles[0].gate_basis.kind,
                    "self_review_fallback",
                )
                self.assertEqual(
                    histories[not_required_task].cycles[0].gate_basis.kind,
                    "not_required",
                )

    def test_false_attestation_is_rejected_with_sanitized_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v15_target(Path(tmp))
            task_id = add_ready_task(target)
            with closing(connect(target.db_path)) as connection:
                make_captureless_done(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
                values = cycle_values_from_task(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    cycle_id="tg_completion_cycle_1111111111111111",
                    ordinal=1,
                    recorded_at="2026-07-30T04:24:00Z",
                )
                values["verification_attestation"] = 0
                connection.execute("PRAGMA ignore_check_constraints = ON")
                insert_cycle_row(connection, values)
                connection.execute("PRAGMA ignore_check_constraints = OFF")
                connection.commit()
                self.assert_sanitized_history_error(
                    lambda: read_latest_completion_cycle(
                        connection,
                        project_id=target.project.project_id,
                        task_id=task_id,
                    ),
                    forbidden=(task_id,),
                )

    def test_target_evidence_and_receipt_corruption_fail_sanitized(self):
        corruptions = (
            (
                "target",
                {
                    "review_target_kind": "git_commit",
                    "review_target_value": "not-a-git-object",
                    "review_target_generation": 1,
                },
                "not-a-git-object",
            ),
            (
                "evidence",
                {
                    "completion_evidence_kind": "git_commit",
                    "completion_evidence_revision": "not-a-git-object",
                    "completion_commit_required": 1,
                    "completion_commit_hash": "not-a-git-object",
                },
                "not-a-git-object",
            ),
        )
        for label, overrides, forbidden in corruptions:
            with (
                self.subTest(corruption=label),
                tempfile.TemporaryDirectory() as tmp,
            ):
                target = make_v15_target(Path(tmp))
                task_id = add_ready_task(target)
                with closing(connect(target.db_path)) as connection:
                    make_captureless_done(
                        connection,
                        project_id=target.project.project_id,
                        task_id=task_id,
                    )
                    values = cycle_values_from_task(
                        connection,
                        project_id=target.project.project_id,
                        task_id=task_id,
                        cycle_id="tg_completion_cycle_2222222222222222",
                        ordinal=1,
                        recorded_at="2026-07-30T04:25:00Z",
                    )
                    values.update(overrides)
                    insert_cycle_row(connection, values)
                    connection.commit()
                    self.assert_sanitized_history_error(
                        lambda: read_latest_completion_cycle(
                            connection,
                            project_id=target.project.project_id,
                            task_id=task_id,
                        ),
                        forbidden=(task_id, forbidden),
                    )

        with tempfile.TemporaryDirectory() as tmp:
            target = make_v15_target(Path(tmp))
            task_id = add_ready_task(target)
            with closing(connect(target.db_path)) as connection:
                cycle_id, receipt_id = seed_native_cycle(
                    connection,
                    target=target,
                    task_id=task_id,
                    id_suffix="3333333333333333",
                )
                connection.commit()
                validate_completion_cycle_storage(connection)

                connection.execute(
                    """
                    UPDATE review_receipts
                       SET verdict = 'changes_requested'
                     WHERE review_receipt_id = ?
                    """,
                    (receipt_id,),
                )
                connection.commit()
                self.assert_sanitized_history_error(
                    lambda: read_latest_completion_cycle(
                        connection,
                        project_id=target.project.project_id,
                        task_id=task_id,
                    ),
                    forbidden=(task_id, receipt_id, EXTERNAL_REVISION),
                )
                self.assert_sanitized_history_error(
                    lambda: read_completion_histories_for_tasks(
                        connection,
                        project_id=target.project.project_id,
                        task_ids=(task_id,),
                    ),
                    forbidden=(task_id, receipt_id, EXTERNAL_REVISION),
                )

    def test_fixed_current_relocation_token_accepts_source_schema_15(self):
        context = RelocationContext(
            project_id=PROJECT_UUID,
            identity_scheme="uuid_v1",
            binding_generation=1,
            old_path_hash=OLD_HASH,
            new_path_hash=NEW_HASH,
            source_layout="fixed_current_v1",
            source_schema_version=15,
        )
        token = encode_relocation_token(
            context,
            issued_at="2026-07-30T04:30:00Z",
        )
        claims = decode_relocation_token(
            token,
            now="2026-07-30T04:30:00Z",
        )
        self.assertEqual(claims.context, context)


if __name__ == "__main__":
    unittest.main()
