import ast
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import verification_runner_plan_edit as edit_module  # noqa: E402
from task_governance_tool.reviews import (  # noqa: E402
    read_review_target_authority_basis,
)
from task_governance_tool.storage import (  # noqa: E402
    DatabaseTarget,
    ProjectIdentity,
    connect_initialized,
    contract_criterion_digest,
    initialize_uuid_database,
    project_identity,
    verification_expectation_digest,
)
from task_governance_tool.tasks import (  # noqa: E402
    TaskRepositoryError,
    add_task,
)
from task_governance_tool.verification_runner_plan import (  # noqa: E402
    PLAN_VERSION,
    VerificationRunnerPlan,
    VerificationRunnerPlanBasis,
    VerificationRunnerPlanEntry,
    VerificationRunnerPlanError,
    decode_verification_runner_plan,
    encode_verification_runner_plan,
)
from task_governance_tool.verification_runner_plan_authoring import (  # noqa: E402
    decode_runner_plan_draft,
)
from task_governance_tool.verification_runner_plan_publisher import (  # noqa: E402
    CONFIRM_RUNNER_PLAN_SOURCE,
    RUNNER_PLAN_CHANGED_MESSAGE,
    RUNNER_PLAN_UPDATE_FAILED_MESSAGE,
)


CURRENT_VERIFICATION = (
    "python -m unittest -q tests.test_m242_runner_plan_edit"
)
FUTURE_VERIFICATION = CURRENT_VERIFICATION + " --future"
UUID_HEX = "00112233445546778899aabbccddeeff"


def canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def draft_blob(*, argv=None):
    return canonical_json_bytes(
        {
            "version": 1,
            "steps": [
                {
                    "step_id": "focused",
                    "mode": "script",
                    "entrypoint": "tests/test_m242_runner_plan_edit.py",
                    "argv": ["--literal", "value"] if argv is None else argv,
                    "cwd": ".",
                    "timeout_seconds": 30,
                    "cpu_seconds": 30,
                    "memory_mib": 128,
                    "process_limit": 2,
                    "output_byte_limit": 1_048_576,
                }
            ],
        }
    )


def run_git(repo, *arguments):
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError("local Git fixture command failed")


@dataclass(frozen=True)
class RunnerPlanEditFixture:
    repo: Path
    package: Path
    target: DatabaseTarget
    task_id: str

    @property
    def plan_path(self):
        return self.package / "config" / "verification-runner.json"

    def database_dump(self):
        with closing(sqlite3.connect(self.target.db_path)) as connection:
            return tuple(connection.iterdump())

    def task_row(self):
        with closing(sqlite3.connect(self.target.db_path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (self.task_id,),
            ).fetchone()
            if row is None:
                raise AssertionError("fixture Task is missing")
            return dict(row)

    def event_count(self):
        with closing(sqlite3.connect(self.target.db_path)) as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                (self.task_id,),
            ).fetchone()[0]

    def basis(self):
        with closing(connect_initialized(self.target)) as connection:
            authority = read_review_target_authority_basis(
                connection,
                self.target.project,
                self.task_id,
            )
        if authority.verification_criterion_digest is None:
            raise AssertionError("fixture verification criterion is missing")
        return VerificationRunnerPlanBasis(
            task_id=self.task_id,
            contract_revision=int(
                authority.task["current_contract_revision"]
            ),
            verification_expectation_digest=(
                authority.verification_expectation_digest
            ),
            verification_criterion_digest=(
                authority.verification_criterion_digest
            ),
        )

    def write_exact_plan(self, *, trusted_local=True, basis=None):
        basis = self.basis() if basis is None else basis
        steps = decode_runner_plan_draft(draft_blob()).steps
        plan = VerificationRunnerPlan(
            version=PLAN_VERSION,
            plan_id="focused-plan",
            trusted_local=trusted_local,
            entries=(
                VerificationRunnerPlanEntry(
                    task_id=basis.task_id,
                    contract_revision=basis.contract_revision,
                    verification_expectation_digest=(
                        basis.verification_expectation_digest
                    ),
                    verification_criterion_digest=(
                        basis.verification_criterion_digest
                    ),
                    coverage="full",
                    steps=steps,
                ),
            ),
        )
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        raw = encode_verification_runner_plan(plan)
        self.plan_path.write_bytes(raw)
        return raw


@contextmanager
def runner_plan_edit_fixture():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        repo = (root / "repo").resolve()
        package = (repo / "package").resolve()
        package.mkdir(parents=True)
        run_git(repo, "init", "--quiet")
        (repo / ".gitignore").write_text(
            "/package/config/\n/package/state/\n",
            encoding="utf-8",
        )

        observed = project_identity(repo)
        db_path = package / "state" / "current" / "taskgov.sqlite"
        unbound = DatabaseTarget(
            project=observed,
            db_path=db_path,
            explicit_db=True,
            skill_root=package,
            canonical_fixed=True,
        )
        initialize_uuid_database(
            unbound,
            project_id_factory=lambda: UUID_HEX,
            clock=lambda: "2026-09-02T00:00:00Z",
        )
        target = replace(
            unbound,
            project=ProjectIdentity(
                project_id=f"tg_project_{UUID_HEX}",
                canonical_repo=observed.canonical_repo,
                canonical_path_hash=observed.canonical_path_hash,
                display_name=observed.display_name,
            ),
            binding_path_hash=observed.canonical_path_hash,
            binding_generation=1,
        )
        with closing(connect_initialized(target)) as connection:
            with connection:
                added = add_task(
                    connection,
                    target.project,
                    database_target=target,
                    title="Runner Plan edit fixture",
                    description="Focused internal coordinator coverage",
                    status="in_progress",
                    review_tier=2,
                    verification=CURRENT_VERIFICATION,
                    contract_scope="Coordinate one Task and Plan action",
                    contract_acceptance="Preserve ordered commit semantics",
                )
        yield RunnerPlanEditFixture(
            repo=repo,
            package=package,
            target=target,
            task_id=str(added.task["task_id"]),
        )


class TrackingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.closed = False

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.connection.__exit__(exc_type, exc_value, traceback)

    def close(self):
        self.closed = True
        self.connection.close()


class RunnerPlanEditTests(unittest.TestCase):
    def assert_error_code(self, code, callback):
        with self.assertRaises(Exception) as raised:
            callback()
        self.assertEqual(getattr(raised.exception, "code", None), code)
        return raised.exception

    def assert_plan_matches_basis(self, fixture, basis):
        plan = decode_verification_runner_plan(fixture.plan_path.read_bytes())
        matches = [
            entry for entry in plan.entries if entry.task_id == fixture.task_id
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].basis(), basis)

    def test_config_only_replace_replay_and_terminal_detach_are_task_write_free(self):
        with runner_plan_edit_fixture() as fixture:
            before = fixture.database_dump()
            first = edit_module.edit_task_with_runner_plan(
                fixture.target,
                fixture.task_id,
                runner_plan_action="replace",
                runner_plan_draft_blob=draft_blob(),
            )

            self.assertEqual(first.task_mutation, "none")
            self.assertEqual(first.edit_result.changed_fields, [])
            self.assertIsNone(first.edit_result.event)
            self.assertEqual(first.runner_plan_update.action, "replace")
            self.assertEqual(first.runner_plan_update.status, "updated")
            self.assertEqual(fixture.database_dump(), before)
            self.assert_plan_matches_basis(fixture, fixture.basis())
            published = fixture.plan_path.read_bytes()

            real_publish = edit_module.publish_verification_runner_plan
            with mock.patch.object(
                edit_module,
                "publish_verification_runner_plan",
                wraps=real_publish,
            ) as publish:
                replay = edit_module.edit_task_with_runner_plan(
                    fixture.target,
                    fixture.task_id,
                    runner_plan_action="replace",
                    runner_plan_draft_blob=draft_blob(),
                )
            self.assertEqual(replay.task_mutation, "none")
            self.assertEqual(replay.runner_plan_update.status, "unchanged")
            self.assertIs(
                publish.call_args.args[3],
                CONFIRM_RUNNER_PLAN_SOURCE,
            )
            self.assertEqual(fixture.plan_path.read_bytes(), published)
            self.assertEqual(fixture.database_dump(), before)

            cancelled = edit_module.edit_task_with_runner_plan(
                fixture.target,
                fixture.task_id,
                status="cancelled",
            )
            self.assertEqual(cancelled.task_mutation, "committed")
            terminal_database = fixture.database_dump()
            terminal_plan = fixture.plan_path.read_bytes()
            for action, draft in (
                ("replace", draft_blob()),
                ("rebind", None),
            ):
                with self.subTest(terminal_action=action):
                    self.assert_error_code(
                        "invalid_argument",
                        lambda action=action, draft=draft: (
                            edit_module.edit_task_with_runner_plan(
                                fixture.target,
                                fixture.task_id,
                                runner_plan_action=action,
                                runner_plan_draft_blob=draft,
                            )
                        ),
                    )
                    self.assertEqual(
                        fixture.database_dump(),
                        terminal_database,
                    )
                    self.assertEqual(
                        fixture.plan_path.read_bytes(),
                        terminal_plan,
                    )
            detached = edit_module.edit_task_with_runner_plan(
                fixture.target,
                fixture.task_id,
                runner_plan_action="detach",
            )
            self.assertEqual(detached.task_mutation, "none")
            self.assertEqual(detached.runner_plan_update.status, "updated")
            self.assertEqual(fixture.database_dump(), terminal_database)
            self.assertEqual(
                decode_verification_runner_plan(
                    fixture.plan_path.read_bytes()
                ).entries,
                (),
            )

    def test_exact_current_entry_requires_action_but_malformed_plan_does_not_hold_task_edit(self):
        with runner_plan_edit_fixture() as fixture:
            original = fixture.write_exact_plan()
            before = fixture.database_dump()
            error = self.assert_error_code(
                "runner_plan_action_required",
                lambda: edit_module.edit_task_with_runner_plan(
                    fixture.target,
                    fixture.task_id,
                    verification=FUTURE_VERIFICATION,
                ),
            )
            self.assertEqual(
                str(error),
                edit_module.RUNNER_PLAN_ACTION_REQUIRED_MESSAGE,
            )
            self.assertEqual(fixture.database_dump(), before)
            self.assertEqual(fixture.plan_path.read_bytes(), original)

            malformed = b'{"version":1,"malformed":true}'
            fixture.plan_path.write_bytes(malformed)
            with mock.patch.object(
                edit_module,
                "publish_verification_runner_plan",
            ) as publish:
                result = edit_module.edit_task_with_runner_plan(
                    fixture.target,
                    fixture.task_id,
                    verification=FUTURE_VERIFICATION,
                )
            self.assertEqual(result.task_mutation, "committed")
            self.assertIsNone(result.runner_plan_update)
            self.assertEqual(
                fixture.task_row()["verification"],
                FUTURE_VERIFICATION,
            )
            self.assertEqual(fixture.plan_path.read_bytes(), malformed)
            publish.assert_not_called()

    def test_combined_boundaries_close_writer_for_updated_and_unchanged(self):
        cases = (
            ("rebind", True, "updated", False),
            ("disable", False, "unchanged", True),
        )
        for action, trusted, expected_status, confirmation_only in cases:
            with self.subTest(action=action), runner_plan_edit_fixture() as fixture:
                original = fixture.write_exact_plan(trusted_local=trusted)
                events_before = fixture.event_count()
                trackers = []
                real_connect = edit_module.connect_initialized
                real_capture = edit_module.capture_runner_plan_authoring_source
                real_publish = edit_module.publish_verification_runner_plan

                def tracked_connect(target):
                    tracked = TrackingConnection(real_connect(target))
                    trackers.append(tracked)
                    return tracked

                def capture(*args, **kwargs):
                    self.assertEqual(len(trackers), 1)
                    self.assertFalse(trackers[0].closed)
                    self.assertFalse(trackers[0].in_transaction)
                    return real_capture(*args, **kwargs)

                def publish(*args, **kwargs):
                    self.assertEqual(len(trackers), 1)
                    self.assertTrue(trackers[0].closed)
                    self.assertEqual(
                        fixture.task_row()["verification"],
                        FUTURE_VERIFICATION,
                    )
                    self.assertEqual(fixture.event_count(), events_before + 1)
                    if confirmation_only:
                        self.assertIs(args[3], CONFIRM_RUNNER_PLAN_SOURCE)
                    else:
                        self.assertIs(type(args[3]), bytes)
                    return real_publish(*args, **kwargs)

                with (
                    mock.patch.object(
                        edit_module,
                        "connect_initialized",
                        side_effect=tracked_connect,
                    ),
                    mock.patch.object(
                        edit_module,
                        "capture_runner_plan_authoring_source",
                        side_effect=capture,
                    ),
                    mock.patch.object(
                        edit_module,
                        "publish_verification_runner_plan",
                        side_effect=publish,
                    ),
                ):
                    result = edit_module.edit_task_with_runner_plan(
                        fixture.target,
                        fixture.task_id,
                        runner_plan_action=action,
                        verification=FUTURE_VERIFICATION,
                    )

                self.assertEqual(result.task_mutation, "committed")
                self.assertEqual(
                    result.runner_plan_update.status,
                    expected_status,
                )
                if confirmation_only:
                    self.assertEqual(fixture.plan_path.read_bytes(), original)
                else:
                    self.assert_plan_matches_basis(fixture, fixture.basis())

    def test_contract_revision_uses_future_basis_and_replay_is_plan_only(self):
        with runner_plan_edit_fixture() as fixture:
            fixture.write_exact_plan()
            revised = edit_module.edit_task_with_runner_plan(
                fixture.target,
                fixture.task_id,
                runner_plan_action="rebind",
                contract_scope="Coordinate revised Task and Plan basis",
                contract_acceptance="Preserve ordered commit semantics",
                contract_authority_ref=(
                    f"user_instruction:{fixture.task_id}:2"
                ),
                contract_change_reason="Exercise future Contract basis",
            )
            self.assertEqual(revised.task_mutation, "committed")
            self.assertEqual(revised.runner_plan_update.status, "updated")
            self.assertEqual(
                revised.edit_result.contract_write,
                {"recorded": True, "revision": 2},
            )
            self.assert_plan_matches_basis(fixture, fixture.basis())

            database = fixture.database_dump()
            plan = fixture.plan_path.read_bytes()
            self.assert_error_code(
                "invalid_option_combination",
                lambda: edit_module.edit_task_with_runner_plan(
                    fixture.target,
                    fixture.task_id,
                    runner_plan_action="rebind",
                    contract_scope="Coordinate revised Task and Plan basis",
                    contract_acceptance="Preserve ordered commit semantics",
                    contract_authority_ref=(
                        f"user_instruction:{fixture.task_id}:2"
                    ),
                    contract_change_reason="Exercise future Contract basis",
                ),
            )
            self.assertEqual(fixture.database_dump(), database)
            self.assertEqual(fixture.plan_path.read_bytes(), plan)

            replay = edit_module.edit_task_with_runner_plan(
                fixture.target,
                fixture.task_id,
                runner_plan_action="rebind",
            )
            self.assertEqual(replay.task_mutation, "none")
            self.assertEqual(replay.runner_plan_update.status, "unchanged")
            self.assertEqual(fixture.database_dump(), database)

    def test_failure_after_task_edit_rolls_back_and_never_publishes(self):
        with runner_plan_edit_fixture() as fixture:
            original = fixture.write_exact_plan()
            before = fixture.database_dump()
            real_edit = edit_module.edit_task

            def fail_after_edit(*args, **kwargs):
                real_edit(*args, **kwargs)
                raise TaskRepositoryError(
                    "database_busy",
                    "injected commit-path failure",
                )

            with (
                mock.patch.object(
                    edit_module,
                    "edit_task",
                    side_effect=fail_after_edit,
                ),
                mock.patch.object(
                    edit_module,
                    "publish_verification_runner_plan",
                ) as publish,
            ):
                self.assert_error_code(
                    "database_busy",
                    lambda: edit_module.edit_task_with_runner_plan(
                        fixture.target,
                        fixture.task_id,
                        runner_plan_action="rebind",
                        verification=FUTURE_VERIFICATION,
                    ),
                )
            publish.assert_not_called()
            self.assertEqual(fixture.database_dump(), before)
            self.assertEqual(fixture.plan_path.read_bytes(), original)

    def test_config_only_drift_and_publisher_failure_remain_ordinary_errors(self):
        cases = (
            (
                "rebind",
                "runner_plan_changed",
                RUNNER_PLAN_CHANGED_MESSAGE,
            ),
            (
                "disable",
                "runner_plan_update_failed",
                RUNNER_PLAN_UPDATE_FAILED_MESSAGE,
            ),
        )
        for action, code, message in cases:
            with self.subTest(code=code), runner_plan_edit_fixture() as fixture:
                original = fixture.write_exact_plan()
                before = fixture.database_dump()
                with mock.patch.object(
                    edit_module,
                    "publish_verification_runner_plan",
                    side_effect=VerificationRunnerPlanError(
                        code=code,
                        message=message,
                    ),
                ):
                    error = self.assert_error_code(
                        code,
                        lambda: edit_module.edit_task_with_runner_plan(
                            fixture.target,
                            fixture.task_id,
                            runner_plan_action=action,
                        ),
                    )
                self.assertEqual(str(error), message)
                self.assertEqual(fixture.database_dump(), before)
                self.assertEqual(fixture.plan_path.read_bytes(), original)

    def test_postcommit_plan_failures_are_sanitized_unconfirmed_and_repairable(self):
        for code in ("runner_plan_changed", "runner_plan_update_failed"):
            with self.subTest(code=code), runner_plan_edit_fixture() as fixture:
                original = fixture.write_exact_plan()
                secret = "token=private-value C:\\private\\plan.json"
                with mock.patch.object(
                    edit_module,
                    "publish_verification_runner_plan",
                    side_effect=VerificationRunnerPlanError(
                        code=code,
                        message=secret,
                    ),
                ):
                    partial = edit_module.edit_task_with_runner_plan(
                        fixture.target,
                        fixture.task_id,
                        runner_plan_action="rebind",
                        verification=FUTURE_VERIFICATION,
                    )

                self.assertEqual(partial.task_mutation, "committed")
                self.assertEqual(
                    partial.runner_plan_update.status,
                    "unconfirmed",
                )
                self.assertIsNotNone(partial.edit_result.event)
                self.assertNotIn(secret, repr(partial))
                self.assertEqual(
                    fixture.task_row()["verification"],
                    FUTURE_VERIFICATION,
                )
                self.assertEqual(fixture.plan_path.read_bytes(), original)

                committed = fixture.database_dump()
                repaired = edit_module.edit_task_with_runner_plan(
                    fixture.target,
                    fixture.task_id,
                    runner_plan_action="rebind",
                )
                self.assertEqual(repaired.task_mutation, "none")
                self.assertEqual(
                    repaired.runner_plan_update.status,
                    "updated",
                )
                self.assertEqual(fixture.database_dump(), committed)
                self.assert_plan_matches_basis(fixture, fixture.basis())

    def test_postcommit_source_confirmation_failure_keeps_task_commit_unconfirmed(self):
        with runner_plan_edit_fixture() as fixture:
            current = fixture.basis()
            prospective = replace(
                current,
                verification_expectation_digest=(
                    verification_expectation_digest(FUTURE_VERIFICATION)
                ),
                verification_criterion_digest=contract_criterion_digest(
                    "verification",
                    FUTURE_VERIFICATION,
                ),
            )
            self.assertNotEqual(prospective, current)
            original = fixture.write_exact_plan(basis=prospective)
            events_before = fixture.event_count()

            with mock.patch.object(
                edit_module,
                "publish_verification_runner_plan",
                side_effect=VerificationRunnerPlanError(
                    code="runner_plan_changed",
                    message=RUNNER_PLAN_CHANGED_MESSAGE,
                ),
            ) as publish:
                partial = edit_module.edit_task_with_runner_plan(
                    fixture.target,
                    fixture.task_id,
                    runner_plan_action="rebind",
                    verification=FUTURE_VERIFICATION,
                )

            publish.assert_called_once()
            self.assertIs(publish.call_args.args[3], CONFIRM_RUNNER_PLAN_SOURCE)
            self.assertEqual(partial.task_mutation, "committed")
            self.assertEqual(partial.runner_plan_update.status, "unconfirmed")
            self.assertIsNotNone(partial.edit_result.event)
            self.assertIn("verification", partial.edit_result.changed_fields)
            self.assertEqual(
                fixture.task_row()["verification"],
                FUTURE_VERIFICATION,
            )
            self.assertEqual(fixture.event_count(), events_before + 1)
            self.assertEqual(fixture.plan_path.read_bytes(), original)

    def test_private_draft_and_incompatible_options_fail_before_mutation_or_launch(self):
        with runner_plan_edit_fixture() as fixture:
            before = fixture.database_dump()
            selector = mock.Mock(side_effect=AssertionError("Runner launched"))
            with (
                mock.patch.object(edit_module, "edit_task") as task_edit,
                mock.patch.object(
                    edit_module,
                    "publish_verification_runner_plan",
                ) as publish,
            ):
                private = self.assert_error_code(
                    "privacy_rejected",
                    lambda: edit_module.edit_task_with_runner_plan(
                        fixture.target,
                        fixture.task_id,
                        runner_plan_action="replace",
                        runner_plan_draft_blob=draft_blob(
                            argv=["token=private-value"]
                        ),
                    ),
                )
                self.assertNotIn("private-value", str(private))

                self.assert_error_code(
                    "invalid_option_combination",
                    lambda: edit_module.edit_task_with_runner_plan(
                        fixture.target,
                        fixture.task_id,
                        runner_plan_action="detach",
                        runner_selector=selector,
                        status="done",
                        verification_complete=True,
                        review_complete=True,
                        commit_not_required=True,
                    ),
                )
                task_edit.assert_not_called()
                publish.assert_not_called()
            selector.assert_not_called()
            self.assertEqual(fixture.database_dump(), before)
            self.assertFalse(fixture.plan_path.exists())

    def test_dependency_surface_has_no_runner_launch_or_follow_on_writes(self):
        source = Path(edit_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        forbidden = (
            "subprocess",
            "task_governance_tool._verification_runner_win32",
            "task_governance_tool.verification_runner_lifecycle",
            "task_governance_tool.verification_runner_process",
            "task_governance_tool.verification_runner_runtime",
            "task_governance_tool.verification_runner_service",
            "task_governance_tool.evidence_projection",
            "task_governance_tool.viewer",
            "task_governance_tool.setup",
        )
        self.assertTrue(set(forbidden).isdisjoint(imported))


if __name__ == "__main__":
    unittest.main()
