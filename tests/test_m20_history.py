import ast
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.m20_history as history


ROOT = Path(__file__).resolve().parents[1]


def synthetic_protocol():
    cohorts = []
    for ordinal, revision_character in enumerate(("a", "b", "c"), start=1):
        cohorts.append(
            {
                "scenario_id": f"synthetic_cohort_{ordinal}",
                "tasks": [
                    [
                        f"TG-M19.{ordinal}",
                        f"tg_task_{ordinal:016x}",
                        revision_character * 40,
                        "completion_revision",
                    ]
                ],
            }
        )
    return {
        "schema": "m20-repository-protocol-v1",
        "authority": {
            "contract_id": "TG-M20-OPERATIONAL-BASELINE",
            "contract_revision": 1,
            "baseline_revision": "f" * 40,
            "authority_revision": "e" * 40,
        },
        "m20_2": {"retrospective_cohorts": cohorts},
        "retrospective_metrics": [
            *history._TASK_FACT_METRICS,
            history._GIT_METRIC,
        ],
    }


def synthetic_facts(task_id, *, incomplete=False):
    return history._TaskFacts(
        task_id=task_id,
        completion_cycles=1,
        reopens=2,
        legacy_history_incomplete=incomplete,
        contract_revisions=3,
        review_receipts=4,
        changes_requested_receipts=5,
        findings_open_high=6,
        findings_open_medium=7,
        findings_open_low=8,
        handoffs_pending=9,
        handoffs_delivered=10,
        handoffs_withdrawn=11,
    )


class M20HistoryTests(unittest.TestCase):
    def test_reconstruct_uses_protocol_owned_cohorts_and_metric_order(self):
        protocol = synthetic_protocol()
        protocol["m20_2"]["retrospective_cohorts"].reverse()
        protocol["retrospective_metrics"].reverse()
        captured = {
            cohort["scenario_id"]: (
                synthetic_facts(cohort["tasks"][0][1]),
            )
            for cohort in protocol["m20_2"]["retrospective_cohorts"]
        }

        def git_span(_repo, _baseline, tasks):
            return 1000, [tasks[0].expected_revision]

        with (
            mock.patch.object(
                history,
                "_validate_scope",
                return_value=(Path("synthetic-repo"), Path("synthetic-skill")),
            ),
            mock.patch.object(history, "_validate_completion_index"),
            mock.patch.object(history, "_capture_database", return_value=captured),
            mock.patch.object(history, "_git_span", side_effect=git_span),
        ):
            result = history.reconstruct_m19(
                Path("unused-repo"),
                Path("unused-skill"),
                protocol,
            )

        self.assertEqual(
            list(result),
            [
                cohort["scenario_id"]
                for cohort in protocol["m20_2"]["retrospective_cohorts"]
            ],
        )
        for records in result.values():
            self.assertEqual(
                [record["metric"] for record in records],
                protocol["retrospective_metrics"],
            )
            self.assertEqual(records[0]["metric"], history._GIT_METRIC)
            self.assertEqual(records[0]["value"], 1000)

    def test_git_failure_is_sanitized_and_isolated_to_one_cohort_metric(self):
        protocol = synthetic_protocol()
        captured = {
            cohort["scenario_id"]: (
                synthetic_facts(cohort["tasks"][0][1]),
            )
            for cohort in protocol["m20_2"]["retrospective_cohorts"]
        }
        calls = 0

        def git_span(_repo, _baseline, tasks):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise history.M20HistoryError("source_missing")
            return 2000, [tasks[0].expected_revision]

        with (
            mock.patch.object(
                history,
                "_validate_scope",
                return_value=(Path("synthetic-repo"), Path("synthetic-skill")),
            ),
            mock.patch.object(history, "_validate_completion_index"),
            mock.patch.object(history, "_capture_database", return_value=captured),
            mock.patch.object(history, "_git_span", side_effect=git_span),
        ):
            result = history.reconstruct_m19(
                Path("unused-repo"),
                Path("unused-skill"),
                protocol,
            )

        first = result["synthetic_cohort_1"]
        git_record = next(
            record for record in first if record["metric"] == history._GIT_METRIC
        )
        self.assertEqual(git_record["coverage"], "partial")
        self.assertIsNone(git_record["value"])
        self.assertEqual(
            git_record["unknowns"],
            [{"field": "value", "reasons": ["source_missing"]}],
        )
        self.assertEqual(
            next(
                record
                for record in first
                if record["metric"] == "contract_revisions"
            )["value"],
            3,
        )
        for scenario_id in ("synthetic_cohort_2", "synthetic_cohort_3"):
            later_git = next(
                record
                for record in result[scenario_id]
                if record["metric"] == history._GIT_METRIC
            )
            self.assertEqual(later_git["coverage"], "complete")

    def test_read_boundary_requires_one_query_only_transaction_and_no_changes(self):
        with tempfile.TemporaryDirectory() as raw_temp:
            database = Path(raw_temp) / "synthetic.sqlite"
            setup = sqlite3.connect(database)
            setup.execute("CREATE TABLE sample(value INTEGER NOT NULL)")
            setup.commit()
            setup.close()

            connection = sqlite3.connect(database)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            repository = history._HistoryRepository(connection, "synthetic-project")
            with mock.patch.object(history, "validate_completion_cycle_storage"):
                repository.validate_snapshot()
            connection.execute("SELECT value FROM sample").fetchall()
            repository.validate_unchanged()

            connection.commit()
            with self.assertRaises(history.M20HistoryError):
                repository.validate_unchanged()
            connection.execute("PRAGMA query_only = OFF")
            connection.execute("BEGIN")
            with self.assertRaises(history.M20HistoryError):
                repository.validate_unchanged()
            connection.rollback()
            connection.execute("INSERT INTO sample(value) VALUES (1)")
            connection.commit()
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            with self.assertRaises(history.M20HistoryError) as raised:
                repository.validate_unchanged()
            self.assertEqual(raised.exception.code, "source_drift")
            connection.close()

    def test_capture_revalidates_read_boundary_when_capture_fails(self):
        protocol = synthetic_protocol()
        _, _, cohorts = history._parse_protocol(protocol)
        database = Path("synthetic.sqlite").resolve(strict=False)
        connection = mock.Mock()
        target_project = SimpleNamespace(
            canonical_repo=Path("synthetic-repo"),
            project_id="synthetic-project",
        )
        resolution = SimpleNamespace(
            read_connection=connection,
            layout="fixed_current_v1",
            binding="matching",
            source_schema_version=history.SCHEMA_VERSION,
            fixed_recovery=None,
            target=SimpleNamespace(db_path=database, project=target_project),
            project_id="synthetic-project",
        )
        repository = mock.Mock()
        repository.capture.side_effect = history.M20HistoryError("source_missing")

        with (
            mock.patch.object(
                history,
                "canonical_state_paths",
                return_value=SimpleNamespace(database=database),
            ),
            mock.patch.object(history, "resolve_project_state", return_value=resolution),
            mock.patch.object(history, "consumer_error_code", return_value=None),
            mock.patch.object(history, "_HistoryRepository", return_value=repository),
        ):
            with self.assertRaises(history.M20HistoryError) as raised:
                history._capture_database(
                    Path("synthetic-repo"),
                    Path("synthetic-skill"),
                    cohorts,
                )

        self.assertEqual(raised.exception.code, "source_missing")
        repository.validate_snapshot.assert_called_once_with()
        repository.validate_unchanged.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_git_subprocess_is_shell_free_noninteractive_and_no_lazy_fetch(self):
        completed = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout=b"result\n",
            stderr=b"",
        )
        with (
            mock.patch.object(history, "safe_git_command", return_value=("git",)),
            mock.patch.object(
                history,
                "safe_git_environment",
                return_value={"SYNTHETIC": "1"},
            ),
            mock.patch.object(
                history.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            result = history._run_git(
                Path("synthetic-repo"),
                ("status", "--porcelain"),
                capture_stdout=True,
            )

        self.assertIs(result, completed)
        args, kwargs = run.call_args
        self.assertEqual(
            args[0],
            ["git", "-c", "core.fsmonitor=false", "status", "--porcelain"],
        )
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(kwargs["env"]["GCM_INTERACTIVE"], "Never")
        self.assertEqual(kwargs["env"]["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(kwargs["env"]["GIT_ATTR_NOSYSTEM"], "1")

    def test_history_sql_contains_no_mutating_statement(self):
        tree = ast.parse((ROOT / "tools" / "m20_history.py").read_text("utf-8"))
        forbidden = {
            "ALTER",
            "ANALYZE",
            "ATTACH",
            "CREATE",
            "DELETE",
            "DETACH",
            "DROP",
            "INSERT",
            "REINDEX",
            "REPLACE",
            "UPDATE",
            "VACUUM",
        }
        statements = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"execute", "executemany", "executescript"}:
                continue
            self.assertTrue(node.args)
            self.assertIsInstance(node.args[0], ast.Constant)
            self.assertIsInstance(node.args[0].value, str)
            statement = node.args[0].value.strip()
            statements.append(statement)
            self.assertNotIn(statement.split(None, 1)[0].upper(), forbidden)
        self.assertTrue(statements)

    def test_unexpected_failure_exposes_only_stable_reason_code(self):
        protocol = synthetic_protocol()
        secret = "Bearer synthetic-secret"
        with mock.patch.object(
            history,
            "_validate_scope",
            side_effect=RuntimeError(secret),
        ):
            with self.assertRaises(history.M20HistoryError) as raised:
                history.reconstruct_m19(
                    Path("unused-repo"),
                    Path("unused-skill"),
                    protocol,
                )
        self.assertEqual(raised.exception.code, "source_missing")
        self.assertEqual(str(raised.exception), "source_missing")
        self.assertNotIn(secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
