import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    initialize_taskgov_internal,
    run_taskgov_internal,
)
from tests.review_test_helpers import FINGERPRINT, seed_review_evidence


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "taskgov.py"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import completion_workflow  # noqa: E402
from task_governance_tool import tasks as task_service  # noqa: E402
from task_governance_tool.completion import CompletionRequest  # noqa: E402
from task_governance_tool.reviews import (  # noqa: E402
    add_review_receipt,
    set_review_target,
)
from task_governance_tool.storage import (  # noqa: E402
    ProjectIdentity,
    StorageError,
    apply_completion_cycle_capture_activation_migration,
    apply_completion_evidence_bundle_migration,
    apply_evidence_ledger_capture_migration,
    apply_verification_receipts_migration,
    connect,
    connect_initialized_readonly,
    read_completion_histories_for_tasks,
    resolve_database_target,
)
from tests.test_completion_cycle_activation import make_captureless_done  # noqa: E402
from tests.test_completion_cycle_history import (  # noqa: E402
    make_v14_target,
    migrate_to_v15,
)


def run_json(*args):
    result = run_taskgov_internal(*args)
    payload = json.loads(result.stdout)
    return result, payload


def add_task(db: Path, repo: Path, title: str, *, tier: int = 1):
    result, payload = run_json(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
        "--status",
        "in_progress",
        "--review-tier",
        str(tier),
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return payload["data"]["task"]


def completion_args(
    db: Path,
    repo: Path,
    task_id: str,
    *,
    command: str,
    kind: str = "commit_not_required",
    revision: str = "",
    reason: str = "",
):
    if command == "task.complete":
        args = [
            "task",
            "complete",
            task_id,
            "--repo",
            str(repo),
            "--db",
            str(db),
        ]
    else:
        args = [
            "task",
            "edit",
            "--repo",
            str(repo),
            "--db",
            str(db),
            task_id,
            "--status",
            "done",
        ]
    args.extend(["--verification-complete", "--review-complete"])
    if kind == "commit_not_required":
        args.append("--commit-not-required")
    else:
        args.extend(["--completion-evidence-kind", kind])
        args.extend(["--completion-revision", revision])
        if reason:
            args.extend(["--completion-evidence-reason", reason])
        if kind == "external_revision":
            args.append("--external-revision-approved")
    args.append("--json")
    return args


def test_project(connection, db: Path, task_id: str):
    row = connection.execute(
        "SELECT project_id FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise AssertionError("missing test task")
    return ProjectIdentity(
        project_id=str(row[0]),
        canonical_repo=(db.parent / "repo").resolve(),
        canonical_path_hash="0" * 64,
        display_name="test-project",
    )


def insert_receipt(
    db: Path,
    task_id: str,
    _receipt_id: str,
    *,
    reviewer: str,
    kind: str,
    verdict: str,
    user_approved: int,
    summary: str = "",
):
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        provenance = (
            {}
            if kind == "not_required"
            else {
                "reviewer_class": "human",
                "model_state": "not_applicable",
                "skill_state": "not_applicable",
                "review_profiles": ["general"],
                "review_lenses": ["correctness"],
                "context_relation": "external_context",
                "review_methods": ["review_packet_inspection"],
            }
        )
        add_review_receipt(
            connection,
            test_project(connection, db, task_id),
            task_id,
            reviewer=reviewer,
            kind=kind,
            verdict=verdict,
            summary=summary,
            user_approved=bool(user_approved),
            **provenance,
        )
        connection.commit()


def set_diff_target_with_fallback(db: Path, task_id: str):
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        project = test_project(connection, db, task_id)
        set_review_target(
            connection,
            project,
            task_id,
            kind="diff_fingerprint",
            revision=FINGERPRINT,
        )
        add_review_receipt(
            connection,
            project,
            task_id,
            reviewer="self",
            kind="self_review_fallback",
            verdict="pass",
            summary="approved fallback",
            user_approved=True,
            reviewer_class="human",
            model_state="not_applicable",
            skill_state="not_applicable",
            review_profiles=["general"],
            review_lenses=["correctness"],
            context_relation="external_context",
            review_methods=["review_packet_inspection"],
        )
        connection.commit()


class CompletionCycleLifecycleTests(unittest.TestCase):
    def test_both_done_paths_capture_and_combined_edit_keeps_event_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "taskgov.sqlite"
            repo.mkdir()
            initialize_taskgov_internal(repo=repo, db=db)

            thin = add_task(db, repo, "Thin completion", tier=0)
            seed_review_evidence(db, thin["task_id"])
            thin_result, thin_payload = run_json(
                *completion_args(
                    db,
                    repo,
                    thin["task_id"],
                    command="task.complete",
                )
            )
            self.assertEqual(thin_result.returncode, 0, thin_result.stdout)

            combined = add_task(db, repo, "Combined completion", tier=1)
            seed_review_evidence(db, combined["task_id"])
            combined_args = completion_args(
                db,
                repo,
                combined["task_id"],
                command="task.edit",
            )
            combined_args[-1:-1] = [
                "--title",
                "Combined final title",
                "--review-tier",
                "2",
                "--review-tier-change-reason",
                "Scope reached Tier 2",
            ]
            rejected, rejected_payload = run_json(*combined_args)
            self.assertEqual(rejected.returncode, 1, rejected.stdout)
            self.assertEqual(
                rejected_payload["errors"],
                [{
                    "code": "invalid_status_transition",
                    "message": (
                        "authority changes require a fresh target before "
                        "review or completion"
                    ),
                }],
            )

            authority_edit, authority_payload = run_json(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                combined["task_id"],
                "--title",
                "Combined final title",
                "--review-tier",
                "2",
                "--review-tier-change-reason",
                "Scope reached Tier 2",
                "--json",
            )
            self.assertEqual(authority_edit.returncode, 0, authority_edit.stdout)
            self.assertEqual(
                authority_payload["data"]["event"]["event_type"],
                "review_tier_changed",
            )
            seed_review_evidence(db, combined["task_id"])
            edit_result, edit_payload = run_json(
                *completion_args(
                    db,
                    repo,
                    combined["task_id"],
                    command="task.complete",
                )
            )
            self.assertEqual(edit_result.returncode, 0, edit_result.stdout)

            self.assertNotIn(
                "completion_cycle_id",
                thin_payload["data"]["event"],
            )
            self.assertNotIn(
                "completion_cycle_id",
                edit_payload["data"]["event"],
            )
            self.assertEqual(
                edit_payload["data"]["event"]["event_type"],
                "task_updated",
            )
            self.assertEqual(
                edit_payload["data"]["task"]["title"],
                "Combined final title",
            )
            self.assertEqual(edit_payload["data"]["task"]["verification"], "")

            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                tasks = {
                    row["task_id"]: row
                    for row in connection.execute(
                        """
                        SELECT task_id, completion_history_coverage
                          FROM tasks
                         WHERE task_id IN (?, ?)
                        """,
                        (thin["task_id"], combined["task_id"]),
                    )
                }
                cycles = {
                    row["task_id"]: row
                    for row in connection.execute(
                        "SELECT * FROM task_completion_cycles"
                    )
                }
                links = {
                    row["task_id"]: row
                    for row in connection.execute(
                        """
                        SELECT task_id, event_type, completion_cycle_id
                          FROM task_events
                         WHERE completion_cycle_id IS NOT NULL
                        """
                    )
                }
            self.assertTrue(
                all(
                    row["completion_history_coverage"] == "complete"
                    for row in tasks.values()
                )
            )
            self.assertEqual(cycles[thin["task_id"]]["origin"], "native_done")
            self.assertEqual(cycles[thin["task_id"]]["completeness"], "complete")
            self.assertEqual(cycles[thin["task_id"]]["review_basis_kind"], "not_required")
            self.assertEqual(cycles[combined["task_id"]]["review_tier"], 2)
            self.assertEqual(
                cycles[combined["task_id"]]["review_basis_kind"],
                "independent_passes",
            )
            self.assertEqual(
                cycles[combined["task_id"]]["verification_expectation"],
                "unspecified",
            )
            self.assertEqual(links[thin["task_id"]]["event_type"], "task_updated")
            self.assertEqual(
                links[combined["task_id"]]["event_type"],
                "task_updated",
            )
            self.assertEqual(
                links[combined["task_id"]]["completion_cycle_id"],
                cycles[combined["task_id"]]["completion_cycle_id"],
            )

    def test_independent_basis_beats_fallback_and_tier2_can_use_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "taskgov.sqlite"
            repo.mkdir()
            initialize_taskgov_internal(repo=repo, db=db)

            independent = add_task(db, repo, "External independent", tier=1)
            seed_review_evidence(
                db,
                independent["task_id"],
                target_kind="external_revision",
                target_value="release-A",
            )
            insert_receipt(
                db,
                independent["task_id"],
                "tg_review_receipt_0000000000000001",
                reviewer="self",
                kind="self_review_fallback",
                verdict="pass",
                user_approved=0,
                summary="documented fallback",
            )
            result, _ = run_json(
                *completion_args(
                    db,
                    repo,
                    independent["task_id"],
                    command="task.complete",
                    kind="external_revision",
                    revision="release-A",
                    reason="User-approved durable revision",
                )
            )
            self.assertEqual(result.returncode, 0, result.stdout)

            fallback = add_task(db, repo, "Tier 2 fallback", tier=2)
            set_diff_target_with_fallback(db, fallback["task_id"])
            result, _ = run_json(
                *completion_args(
                    db,
                    repo,
                    fallback["task_id"],
                    command="task.complete",
                )
            )
            self.assertEqual(result.returncode, 0, result.stdout)

            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                first = connection.execute(
                    """
                    SELECT cycle.*, receipt.receipt_kind
                      FROM task_completion_cycles AS cycle
                      JOIN review_receipts AS receipt
                        ON receipt.review_receipt_id =
                           cycle.qualifying_receipt_id_1
                     WHERE cycle.task_id = ?
                    """,
                    (independent["task_id"],),
                ).fetchone()
                second = connection.execute(
                    """
                    SELECT *
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (fallback["task_id"],),
                ).fetchone()
            self.assertEqual(first["review_basis_kind"], "independent_passes")
            self.assertEqual(first["receipt_kind"], "independent")
            self.assertEqual(first["completion_evidence_kind"], "external_revision")
            self.assertEqual(second["review_basis_kind"], "self_review_fallback")
            self.assertEqual(second["qualifying_independent_passes"], 0)

    def test_done_reopen_done_requires_fresh_target_and_links_each_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "taskgov.sqlite"
            repo.mkdir()
            initialize_taskgov_internal(repo=repo, db=db)
            same_second = "2026-07-30T08:30:00Z"
            with mock.patch(
                "task_governance_tool.tasks.utc_now",
                return_value=same_second,
            ):
                added, added_payload = run_json(
                    "task",
                    "add",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    "--title",
                    "Repeated lifecycle",
                    "--status",
                    "in_progress",
                    "--review-tier",
                    "1",
                    "--contract-scope",
                    "First bounded scope",
                    "--contract-acceptance",
                    "First acceptance passes",
                    "--contract-constraints",
                    "No network",
                    "--contract-authority-ref",
                    "roadmap:TG-M18.4",
                    "--json",
                )
                self.assertEqual(added.returncode, 0, added.stdout)
                task = added_payload["data"]["task"]
                seed_review_evidence(
                    db,
                    task["task_id"],
                    target_kind="external_revision",
                    target_value="release-A",
                )
                first, _ = run_json(
                    *completion_args(
                        db,
                        repo,
                        task["task_id"],
                        command="task.complete",
                        kind="external_revision",
                        revision="release-A",
                        reason="First accepted revision",
                    )
                )
                self.assertEqual(first.returncode, 0, first.stdout)

                reopened, reopen_payload = run_json(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task["task_id"],
                    "--status",
                    "in_progress",
                    "--reopen-reason",
                    "Acceptance changed",
                    "--json",
                )
                self.assertEqual(reopened.returncode, 0, reopened.stdout)
                self.assertNotIn(
                    "completion_cycle_id",
                    reopen_payload["data"]["event"],
                )

                stale, stale_payload = run_json(
                    *completion_args(
                        db,
                        repo,
                        task["task_id"],
                        command="task.complete",
                        kind="external_revision",
                        revision="release-A",
                        reason="Historical evidence must not count",
                    )
                )
                self.assertNotEqual(stale.returncode, 0)
                self.assertEqual(
                    stale_payload["errors"][0]["code"],
                    "review_target_required",
                )

                revised, revised_payload = run_json(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task["task_id"],
                    "--contract-scope",
                    "Second bounded scope",
                    "--contract-acceptance",
                    "Second acceptance passes",
                    "--contract-constraints",
                    "No network",
                    "--contract-authority-ref",
                    "roadmap:TG-M18.4",
                    "--contract-change-reason",
                    "Acceptance changed after reopen",
                    "--json",
                )
                self.assertEqual(revised.returncode, 0, revised.stdout)
                self.assertEqual(
                    revised_payload["data"]["contract_write"],
                    {"recorded": True, "revision": 2},
                )

                seed_review_evidence(
                    db,
                    task["task_id"],
                    target_kind="external_revision",
                    target_value="release-B",
                )
                second_args = list(
                    completion_args(
                        db,
                        repo,
                        task["task_id"],
                        command="task.edit",
                        kind="external_revision",
                        revision="release-B",
                        reason="Second accepted revision",
                    )
                )
                missing_verification, missing_verification_payload = run_json(
                    *(
                        item
                        for item in second_args
                        if item != "--verification-complete"
                    )
                )
                self.assertNotEqual(missing_verification.returncode, 0)
                self.assertEqual(
                    missing_verification_payload["errors"][0]["code"],
                    "verification_required",
                )
                missing_review, missing_review_payload = run_json(
                    *(
                        item
                        for item in second_args
                        if item != "--review-complete"
                    )
                )
                self.assertNotEqual(missing_review.returncode, 0)
                self.assertEqual(
                    missing_review_payload["errors"][0]["code"],
                    "review_required",
                )
                second, _ = run_json(*second_args)
                self.assertEqual(second.returncode, 0, second.stdout)

            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                cycles = connection.execute(
                    """
                    SELECT completion_cycle_id, saved_cycle_ordinal,
                           completion_evidence_revision,
                           review_target_generation, contract_revision,
                           completed_at
                      FROM task_completion_cycles
                     WHERE task_id = ?
                     ORDER BY saved_cycle_ordinal
                    """,
                    (task["task_id"],),
                ).fetchall()
                links = connection.execute(
                    """
                    SELECT event_type, completion_cycle_id
                      FROM task_events
                     WHERE task_id = ?
                       AND completion_cycle_id IS NOT NULL
                     ORDER BY rowid
                    """,
                    (task["task_id"],),
                ).fetchall()
            self.assertEqual(
                [row["saved_cycle_ordinal"] for row in cycles],
                [1, 2],
            )
            self.assertEqual(
                [row["completion_evidence_revision"] for row in cycles],
                ["release-A", "release-B"],
            )
            self.assertEqual(
                [row["contract_revision"] for row in cycles],
                [1, 2],
            )
            self.assertEqual(
                [row["completed_at"] for row in cycles],
                [same_second, same_second],
            )
            self.assertGreater(
                cycles[1]["review_target_generation"],
                cycles[0]["review_target_generation"],
            )
            self.assertEqual(
                [(row["event_type"], row["completion_cycle_id"]) for row in links],
                [
                    ("task_updated", cycles[0]["completion_cycle_id"]),
                    ("task_reopened", cycles[0]["completion_cycle_id"]),
                    ("task_updated", cycles[1]["completion_cycle_id"]),
                ],
            )

    def test_reopen_rejects_missing_reused_and_generation_overflow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "taskgov.sqlite"
            repo.mkdir()
            initialize_taskgov_internal(repo=repo, db=db)

            missing = add_task(db, repo, "Missing cycle", tier=0)
            seed_review_evidence(db, missing["task_id"])
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE tasks
                       SET status = 'done',
                           completed_at = '2026-07-30T09:00:00Z',
                           updated_at = '2026-07-30T09:00:00Z',
                           completion_evidence_kind = 'commit_not_required',
                           completion_evidence_revision = '',
                           completion_evidence_reason = '',
                           external_revision_approved = 0,
                           completion_commit_required = 0,
                           completion_commit_hash = ''
                     WHERE task_id = ?
                    """,
                    (missing["task_id"],),
                )
                connection.commit()
            result, payload = run_json(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                missing["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "Must fail closed",
                "--json",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                payload["errors"],
                [{
                    "code": "completion_history_inconsistent",
                    "message": "stored completion history is inconsistent",
                }],
            )

            overflow = add_task(db, repo, "Generation overflow", tier=0)
            seed_review_evidence(db, overflow["task_id"])
            with closing(sqlite3.connect(db)) as connection:
                original_generation = connection.execute(
                    "SELECT review_target_generation FROM tasks WHERE task_id = ?",
                    (overflow["task_id"],),
                ).fetchone()[0]
                connection.execute(
                    """
                    UPDATE tasks
                       SET review_target_generation = ?
                     WHERE task_id = ?
                    """,
                    ((1 << 63) - 1, overflow["task_id"]),
                )
                connection.commit()
            before_overflow = db.read_bytes()
            completed, payload = run_json(
                *completion_args(
                    db,
                    repo,
                    overflow["task_id"],
                    command="task.complete",
                )
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertEqual(
                payload["errors"],
                [{
                    "code": "project_state_unreadable",
                    "message": "project state could not be read safely",
                }],
            )
            self.assertEqual(db.read_bytes(), before_overflow)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE tasks
                       SET review_target_generation = ?
                     WHERE task_id = ?
                    """,
                    (original_generation, overflow["task_id"]),
                )
                connection.commit()

            reused = add_task(db, repo, "Reused cycle", tier=0)
            seed_review_evidence(db, reused["task_id"])
            completed, _ = run_json(
                *completion_args(
                    db,
                    repo,
                    reused["task_id"],
                    command="task.complete",
                )
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            reopened, _ = run_json(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                reused["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "First reopen",
                "--json",
            )
            self.assertEqual(reopened.returncode, 0, reopened.stdout)
            with closing(sqlite3.connect(db)) as connection:
                cycle = connection.execute(
                    """
                    SELECT * FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (reused["task_id"],),
                ).fetchone()
                connection.execute(
                    """
                    UPDATE tasks
                       SET status = 'done', completed_at = ?,
                           updated_at = ?, review_tier = ?,
                           completion_evidence_kind = ?,
                           completion_evidence_revision = ?,
                           completion_evidence_reason = ?,
                           external_revision_approved = ?,
                           completion_commit_required = ?,
                           completion_commit_hash = ?,
                           review_target_kind = ?,
                           review_target_value = ?,
                           review_target_base_revision = ?,
                           review_target_generation = ?
                     WHERE task_id = ?
                    """,
                    (
                        cycle[6],
                        "2026-07-30T09:30:00Z",
                        cycle[9],
                        cycle[12],
                        cycle[13],
                        cycle[14],
                        cycle[15],
                        cycle[16],
                        cycle[17],
                        cycle[18],
                        cycle[19],
                        cycle[20],
                        cycle[21],
                        reused["task_id"],
                    ),
                )
                before_events = connection.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (reused["task_id"],),
                ).fetchone()[0]
                connection.commit()
            result, payload = run_json(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                reused["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "Second link is forbidden",
                "--json",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                payload["errors"][0]["code"],
                "completion_history_inconsistent",
            )
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                        (reused["task_id"],),
                    ).fetchone()[0],
                    before_events,
                )

    def test_reopen_rejects_native_cycle_missing_completion_event_without_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "taskgov.sqlite"
            repo.mkdir()
            initialize_taskgov_internal(repo=repo, db=db)
            task = add_task(db, repo, "Missing completion event", tier=0)
            seed_review_evidence(db, task["task_id"])
            completed, _ = run_json(
                *completion_args(
                    db,
                    repo,
                    task["task_id"],
                    command="task.complete",
                )
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    DELETE FROM task_events
                     WHERE task_id = ?
                       AND completion_cycle_id IS NOT NULL
                       AND event_type IN (
                         'task_updated', 'review_tier_changed'
                       )
                    """,
                    (task["task_id"],),
                )
                connection.commit()
                before = (
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone(),
                    connection.execute(
                        """
                        SELECT * FROM task_completion_cycles
                         WHERE task_id = ? ORDER BY saved_cycle_ordinal
                        """,
                        (task["task_id"],),
                    ).fetchall(),
                    connection.execute(
                        """
                        SELECT * FROM task_events
                         WHERE task_id = ? ORDER BY rowid
                        """,
                        (task["task_id"],),
                    ).fetchall(),
                )

            reopened, payload = run_json(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "Missing completion event must fail closed",
                "--json",
            )
            self.assertEqual(reopened.returncode, 2)
            self.assertEqual(
                payload["errors"],
                [{
                    "code": "completion_history_inconsistent",
                    "message": "stored completion history is inconsistent",
                }],
            )

            with closing(sqlite3.connect(db)) as connection:
                after = (
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone(),
                    connection.execute(
                        """
                        SELECT * FROM task_completion_cycles
                         WHERE task_id = ? ORDER BY saved_cycle_ordinal
                        """,
                        (task["task_id"],),
                    ).fetchall(),
                    connection.execute(
                        """
                        SELECT * FROM task_events
                         WHERE task_id = ? ORDER BY rowid
                        """,
                        (task["task_id"],),
                    ).fetchall(),
                )
            self.assertEqual(after, before)

    def test_native_completion_reference_corruption_fails_show_batch_and_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "taskgov.sqlite"
            repo.mkdir()
            initialize_taskgov_internal(repo=repo, db=db)
            task = add_task(db, repo, "Completion Reference integrity", tier=0)
            seed_review_evidence(db, task["task_id"])
            completed, _ = run_json(
                *completion_args(
                    db,
                    repo,
                    task["task_id"],
                    command="task.complete",
                )
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SCRIPT_PATH,
            )

            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                reference = dict(
                    connection.execute(
                        """
                        SELECT reference.*
                          FROM evidence_references AS reference
                          JOIN task_completion_cycles AS cycle
                            ON cycle.completion_cycle_id = reference.source_id
                         WHERE reference.source_kind = 'completion_evidence'
                           AND cycle.task_id = ?
                        """,
                        (task["task_id"],),
                    ).fetchone()
                )
                delete_trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_evidence_references_no_delete'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_evidence_references_no_delete"
                )
                connection.execute(
                    "DELETE FROM evidence_references "
                    "WHERE evidence_reference_id = ?",
                    (reference["evidence_reference_id"],),
                )
                connection.execute(delete_trigger_sql)
                connection.commit()

            shown, shown_payload = run_json(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--read-only",
                "--json",
            )
            self.assertEqual(shown.returncode, 2)
            self.assertEqual(
                shown_payload["errors"],
                [{
                    "code": "evidence_ledger_inconsistent",
                    "message": "stored evidence ledger is inconsistent",
                }],
            )

            with closing(sqlite3.connect(db)) as connection:
                fields = tuple(reference)
                connection.execute(
                    f"INSERT INTO evidence_references({', '.join(fields)}) "
                    f"VALUES ({', '.join('?' for _ in fields)})",
                    tuple(reference[field] for field in fields),
                )
                update_trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_evidence_references_no_update'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_evidence_references_no_update"
                )
                connection.execute(
                    """
                    UPDATE evidence_references
                       SET digest = ?
                     WHERE evidence_reference_id = ?
                    """,
                    (
                        "sha256:" + "0" * 64,
                        reference["evidence_reference_id"],
                    ),
                )
                connection.execute(update_trigger_sql)
                connection.commit()

            with closing(connect_initialized_readonly(target)) as connection:
                traced_sql: list[str] = []
                connection.set_trace_callback(traced_sql.append)
                with self.assertRaises(StorageError) as batch_error:
                    read_completion_histories_for_tasks(
                        connection,
                        project_id=target.project.project_id,
                        task_ids=(task["task_id"],),
                    )
                connection.set_trace_callback(None)
                self.assertEqual(
                    batch_error.exception.code,
                    "evidence_ledger_inconsistent",
                )
                self.assertEqual(
                    batch_error.exception.message,
                    "stored evidence ledger is inconsistent",
                )
                selected_queries = [
                    statement
                    for statement in traced_sql
                    if "WITH selected_sources(value)" in statement
                ]
                self.assertEqual(len(selected_queries), 1)
                plan_details = [
                    str(row[3])
                    for row in connection.execute(
                        "EXPLAIN QUERY PLAN " + selected_queries[0]
                    ).fetchall()
                ]
                self.assertTrue(
                    any(
                        "SEARCH owner USING COVERING INDEX "
                        "idx_tasks_project_task_identity" in detail
                        for detail in plan_details
                    ),
                    plan_details,
                )
                self.assertTrue(
                    any(
                        "SEARCH reference USING INDEX "
                        "idx_evidence_references_source" in detail
                        for detail in plan_details
                    ),
                    plan_details,
                )
                self.assertFalse(
                    any("SCAN reference" in detail for detail in plan_details),
                    plan_details,
                )
                self.assertFalse(
                    any(
                        "SCAN owner" in detail or "SCAN tasks" in detail
                        for detail in plan_details
                    ),
                    plan_details,
                )

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "DROP TRIGGER trg_evidence_references_no_update"
                )
                connection.execute(
                    """
                    UPDATE evidence_references
                       SET digest = ?, completion_cycle_id = NULL
                     WHERE evidence_reference_id = ?
                    """,
                    (
                        reference["digest"],
                        reference["evidence_reference_id"],
                    ),
                )
                connection.execute(update_trigger_sql)
                connection.commit()
                before = tuple(
                    tuple(row)
                    for table_name, order_by in (
                        ("tasks", "task_id"),
                        ("task_completion_cycles", "completion_cycle_id"),
                        ("task_events", "rowid"),
                        ("evidence_references", "evidence_reference_id"),
                    )
                    for row in connection.execute(
                        f"SELECT * FROM {table_name} "
                        f"WHERE task_id = ? ORDER BY {order_by}",
                        (task["task_id"],),
                    ).fetchall()
                )

            reopened, reopened_payload = run_json(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "Completion Reference binding must fail closed",
                "--json",
            )
            self.assertEqual(reopened.returncode, 2)
            self.assertEqual(
                reopened_payload["errors"],
                [{
                    "code": "evidence_ledger_inconsistent",
                    "message": "stored evidence ledger is inconsistent",
                }],
            )
            with closing(sqlite3.connect(db)) as connection:
                after = tuple(
                    tuple(row)
                    for table_name, order_by in (
                        ("tasks", "task_id"),
                        ("task_completion_cycles", "completion_cycle_id"),
                        ("task_events", "rowid"),
                        ("evidence_references", "evidence_reference_id"),
                    )
                    for row in connection.execute(
                        f"SELECT * FROM {table_name} "
                        f"WHERE task_id = ? ORDER BY {order_by}",
                        (task["task_id"],),
                    ).fetchall()
                )
            self.assertEqual(after, before)

    def test_legacy_no_cycle_reopen_bridges_and_post_link_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            target = make_v14_target(Path(temp))
            migrate_to_v15(target)
            repo, db = target.project.canonical_repo, target.db_path
            with closing(connect(db)) as connection:
                tasks = [
                    task_service.add_task(
                        connection,
                        target.project,
                        title=f"Legacy bridge {case}",
                        kind="optional",
                    ).task
                    for case in ("success", "rollback")
                ]
                connection.commit()
            successful, rolled_back = tasks

            with closing(connect(db)) as connection:
                apply_completion_cycle_capture_activation_migration(connection)
                apply_verification_receipts_migration(connection)

            completed_at = {
                successful["task_id"]: "2026-07-30T09:40:00Z",
                rolled_back["task_id"]: "2026-07-30T09:41:00Z",
            }
            with closing(connect(db)) as connection:
                for task_id, completion_time in completed_at.items():
                    make_captureless_done(
                        connection,
                        project_id=target.project.project_id,
                        task_id=task_id,
                        completed_at=completion_time,
                    )
                connection.commit()
                eligible_count = connection.execute(
                    """
                    SELECT COUNT(*)
                      FROM tasks AS task
                     WHERE task.task_id IN (?, ?)
                       AND task.status = 'done'
                       AND task.completion_history_coverage = 'legacy_unknown'
                       AND NOT EXISTS (
                         SELECT 1
                           FROM task_completion_cycles AS cycle
                          WHERE cycle.task_id = task.task_id
                       )
                    """,
                    (successful["task_id"], rolled_back["task_id"]),
                ).fetchone()[0]
                self.assertEqual(eligible_count, 2)

            def reopen_args(task, reason):
                return (
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--status", "in_progress",
                    "--reopen-reason", reason, "--json",
                )

            schema17_bytes = db.read_bytes()
            blocked, blocked_payload = run_json(
                *reopen_args(
                    successful,
                    "Schema 17 requires explicit setup migration",
                )
            )
            self.assertEqual(blocked.returncode, 2, blocked.stdout)
            self.assertEqual(
                blocked_payload["errors"],
                [{
                    "code": "migration_required",
                    "message": (
                        "database schema version 17 does not match supported "
                        "version 19; run setup to migrate"
                    ),
                }],
            )
            self.assertEqual(db.read_bytes(), schema17_bytes)

            with closing(connect(db)) as connection:
                apply_evidence_ledger_capture_migration(connection)
                apply_completion_evidence_bundle_migration(connection)

            reopened, reopen_payload = run_json(
                *reopen_args(
                    successful,
                    "Exercise the legacy compatibility bridge",
                )
            )
            self.assertEqual(reopened.returncode, 0, reopened.stdout)
            self.assertNotIn(
                "completion_cycle_id",
                reopen_payload["data"]["event"],
            )

            with closing(connect(db)) as connection:
                rows = connection.execute(
                    """
                    SELECT task.status, task.completed_at,
                           task.completion_history_coverage,
                           task.review_target_generation,
                           cycle.completion_cycle_id,
                           cycle.saved_cycle_ordinal, cycle.origin,
                           cycle.completeness, cycle.completed_at,
                           event.task_event_id, event.event_type,
                           event.completion_cycle_id
                      FROM tasks AS task
                      JOIN task_completion_cycles AS cycle
                        ON cycle.task_id = task.task_id
                      JOIN task_events AS event
                        ON event.task_id = task.task_id
                       AND event.event_type = 'task_reopened'
                     WHERE task.task_id = ?
                    """,
                    (successful["task_id"],),
                ).fetchall()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(
                tuple(row[:4]),
                ("in_progress", None, "legacy_unknown", 1),
            )
            self.assertEqual(
                tuple(row[5:9]),
                (
                    1,
                    "legacy_current_done",
                    "partial",
                    completed_at[successful["task_id"]],
                ),
            )
            self.assertEqual(
                tuple(row[9:11]),
                (
                    reopen_payload["data"]["event"]["task_event_id"],
                    "task_reopened",
                ),
            )
            self.assertEqual(row[11], row[4])

            def database_dump():
                with closing(sqlite3.connect(db)) as connection:
                    return "\n".join(connection.iterdump())

            before_failure = database_dump()
            with mock.patch(
                "task_governance_tool.effort.record_task_transition",
                side_effect=RuntimeError("injected post-link effort failure"),
            ) as record_effort:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected post-link effort failure",
                ):
                    run_taskgov_internal(
                        *reopen_args(
                            rolled_back,
                            "Roll back the whole compatibility bridge",
                        )
                    )

            record_effort.assert_called_once()
            self.assertEqual(database_dump(), before_failure)

    def test_native_authority_mutation_fails_closed_without_database_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "taskgov.sqlite"
            repo.mkdir()
            initialize_taskgov_internal(repo=repo, db=db)
            external_revision = (
                "github-actions-run:VAiring/task-governance-tool:"
                "30561916953:1"
            )
            legacy_constraints = (
                "Required user approval:\n"
                "- The initial approval names `dispatch_authorization=1`; "
                "every later fresh dispatch approval increments it by exactly "
                "one.\n"
                "Approval:\n"
                '{"action":"push_and_dispatch","dispatch_authorization":3,'
                '"schema":"m19.7-approval-v1"}'
            )
            legacy_checkpoint_summary = (
                '{"branch":"codex/project-scoped-install-guidance",'
                '"branch_head":"a9b80ce177a6dead10d51a070b76ff01f7af0294",'
                '"dispatch_authorization":3,'
                '"gen":"tg_gate_c61b9e41063a7767","job":"test",'
                '"py312":"success","py314":"success",'
                '"rc":"a9b80ce177a6dead10d51a070b76ff01f7af0294",'
                '"remote":"origin","repo":"VAiring/task-governance-tool",'
                '"run_attempt":1,"run_event":"workflow_dispatch",'
                '"run_head":"a9b80ce177a6dead10d51a070b76ff01f7af0294",'
                '"run_id":30561916953,"schema":"m19.7-evidence-v1",'
                '"workflow":".github/workflows/ci.yml",'
                '"workflow_name":"CI"}'
            )

            added, added_payload = run_json(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "TG-M19.7 legacy read fixture",
                "--status",
                "in_progress",
                "--review-tier",
                "2",
                "--contract-scope",
                "Publish only the accepted candidate.",
                "--contract-acceptance",
                "The exact candidate CI run passes.",
                "--contract-constraints",
                "Temporary safe fixture content.",
                "--contract-authority-ref",
                "roadmap:TG-M19.7",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stdout)
            task_id = added_payload["data"]["task"]["task_id"]

            checkpoint, checkpoint_payload = run_json(
                "task",
                "checkpoint",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--summary",
                "Temporary safe checkpoint.",
                "--next-action",
                "Bind this evidence, pass both reviews, and complete TG-M19.7.",
                "--json",
            )
            self.assertEqual(checkpoint.returncode, 0, checkpoint.stdout)
            checkpoint_id = checkpoint_payload["data"]["checkpoint"][
                "checkpoint_id"
            ]

            seed_review_evidence(
                db,
                task_id,
                target_kind="external_revision",
                target_value=external_revision,
            )
            completed, _ = run_json(
                *completion_args(
                    db,
                    repo,
                    task_id,
                    command="task.complete",
                    kind="external_revision",
                    revision=external_revision,
                    reason=(
                        "Exact approved candidate-branch CI run is the durable "
                        "external revision."
                    ),
                )
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE task_contract_revisions
                       SET constraints_text = ?
                     WHERE task_id = ? AND revision = 1
                    """,
                    (legacy_constraints, task_id),
                )
                connection.execute(
                    """
                    UPDATE task_checkpoints
                       SET summary = ?
                     WHERE checkpoint_id = ?
                    """,
                    (legacy_checkpoint_summary, checkpoint_id),
                )
                connection.commit()

            def stored_rows():
                with closing(sqlite3.connect(db)) as connection:
                    return (
                        connection.execute(
                            """
                            SELECT * FROM task_contract_revisions
                             WHERE task_id = ? ORDER BY revision
                            """,
                            (task_id,),
                        ).fetchall(),
                        connection.execute(
                            """
                            SELECT * FROM task_checkpoints
                             WHERE task_id = ? ORDER BY created_at, rowid
                            """,
                            (task_id,),
                        ).fetchall(),
                        connection.execute(
                            """
                            SELECT * FROM task_completion_cycles
                             WHERE task_id = ?
                             ORDER BY saved_cycle_ordinal
                            """,
                            (task_id,),
                        ).fetchall(),
                    )

            sidecars = tuple(
                Path(str(db) + suffix)
                for suffix in ("-journal", "-wal", "-shm")
            )
            before_rows = stored_rows()
            before_hash = hashlib.sha256(db.read_bytes()).digest()
            self.assertTrue(all(not sidecar.exists() for sidecar in sidecars))

            shown, shown_payload = run_json(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--read-only",
                "--json",
            )

            self.assertEqual(shown.returncode, 2, shown.stdout)
            self.assertEqual(
                shown_payload["errors"],
                [{
                    "code": "project_state_unreadable",
                    "message": "project state could not be read safely",
                }],
            )
            self.assertIsNone(shown_payload["data"]["task"])
            self.assertIsNone(shown_payload["data"]["completion_history"])

            self.assertEqual(
                hashlib.sha256(db.read_bytes()).digest(),
                before_hash,
            )
            self.assertEqual(stored_rows(), before_rows)
            self.assertTrue(all(not sidecar.exists() for sidecar in sidecars))

    def test_check_privacy_and_injected_event_failure_write_no_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db = root / "repo", root / "taskgov.sqlite"
            repo.mkdir()
            initialize_taskgov_internal(repo=repo, db=db)
            task = add_task(db, repo, "No failed cycle", tier=0)
            seed_review_evidence(db, task["task_id"])

            checked, check_payload = run_json(
                *completion_args(
                    db,
                    repo,
                    task["task_id"],
                    command="task.complete",
                )[:-1],
                "--check",
                "--read-only",
                "--json",
            )
            self.assertEqual(checked.returncode, 0, checked.stdout)
            self.assertTrue(check_payload["data"]["ready"])
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_completion_cycles"
                    ).fetchone()[0],
                    0,
                )

            private_args = completion_args(
                db,
                repo,
                task["task_id"],
                command="task.complete",
                kind="external_revision",
                revision="release-private",
                reason="token=secret",
            )
            private, private_payload = run_json(*private_args)
            self.assertNotEqual(private.returncode, 0)
            self.assertEqual(
                private_payload["errors"][0]["code"],
                "privacy_rejected",
            )

            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SCRIPT_PATH,
            )
            request = CompletionRequest(
                task_id=task["task_id"],
                verification_complete=True,
                review_complete=True,
                completion_evidence_kind="commit_not_required",
            )
            with mock.patch.object(
                task_service,
                "create_task_event",
                side_effect=RuntimeError("injected event failure"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected event failure",
                ):
                    completion_workflow.execute_completion_request(
                        target,
                        request,
                    )

            with closing(sqlite3.connect(db)) as connection:
                task_row = connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                cycle_count = connection.execute(
                    "SELECT COUNT(*) FROM task_completion_cycles"
                ).fetchone()[0]
                linked_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM task_events
                     WHERE completion_cycle_id IS NOT NULL
                    """
                ).fetchone()[0]
            self.assertEqual(task_row[0], "in_progress")
            self.assertEqual(cycle_count, 0)
            self.assertEqual(linked_count, 0)


if __name__ == "__main__":
    unittest.main()
