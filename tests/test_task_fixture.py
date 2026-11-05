import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock
from pathlib import Path
from tests.registration_output_support import registration_body

from tests.review_test_helpers import seed_review_evidence
from tests.current_database_fixture_support import EmptyDatabaseTemplate
from task_governance_tool import storage
from tests.m14_test_support import (
    initialize_taskgov_internal,
    run_taskgov_internal,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
FIXTURE_PATH = ROOT / "fixtures" / "task-status-mvp" / "tasks.json"


def run_taskgov(*args):
    return run_taskgov_internal(*args)


def load_fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def add_fixture_task(db, repo, task):
    args = [
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        task["title"],
        "--description",
        task.get("description", ""),
        "--kind",
        task["kind"],
        "--priority",
        task["priority"],
        "--status",
        task["status"],
        "--review-tier",
        str(task["review_tier"]),
        "--verification",
        task.get("verification", ""),
        "--tags",
        task.get("tags", ""),
        "--json",
    ]
    if task.get("lane"):
        args.extend(["--lane", task["lane"]])
    if "order" in task:
        args.extend(["--order", str(task["order"])])
    if task.get("blocked_reason"):
        args.extend(["--blocked-reason", task["blocked_reason"]])
    result = run_taskgov(*args)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    data = json.loads(result.stdout)["data"]
    return registration_body(data, data, "task")


class TaskStatusFixtureTests(unittest.TestCase):
    def test_shared_setup_clones_only_explicit_ordinary_cases(self):
        from tests.current_database_fixture_support import initialize_case_database, ordinary_current_database
        from tests import m14_test_support
        from tests import current_database_fixture_support as fixture
        class Cases:
            @ordinary_current_database
            def ordinary(self):
                pass
            def protected_or_new(self):
                pass
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = Cases()
            with mock.patch.object(m14_test_support, "initialize_taskgov_internal",
                                   wraps=m14_test_support.initialize_taskgov_internal) as real, \
                 mock.patch.object(fixture, "initialize_current_test_database",
                                   wraps=fixture.initialize_current_test_database) as copied:
                results = []
                for name in ("ordinary", "protected_or_new"):
                    case._testMethodName = name
                    results.append(initialize_case_database(case, repo=root / name,
                        db=root / name / "case.sqlite"))
                self.assertEqual(copied.call_count, 1)
                self.assertEqual(real.call_count, 1)
            self.assertNotEqual(results[0]["project_id"], results[1]["project_id"])
            self.assertTrue(all(row["schema_version"] == storage.SCHEMA_VERSION for row in results))

    def test_first_import_under_predecessor_pin_does_not_define_current_image(self):
        import importlib.util
        from tests.test_review_session_migration import candidate_runtime
        path = ROOT / "tests/current_database_fixture_support.py"
        current = storage.SCHEMA_VERSION
        with tempfile.TemporaryDirectory() as tmp, candidate_runtime(34):
            spec = importlib.util.spec_from_file_location("isolated_current_fixture", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.CURRENT_TEMPLATE_SCHEMA_VERSION, current)
            root = Path(tmp)
            with self.assertRaisesRegex(AssertionError, "pinned runtime"):
                module.EmptyDatabaseTemplate().initialize(db=root / "absent/db.sqlite", repo=root / "repo")
            self.assertFalse((root / "absent").exists())
            target = storage.resolve_database_target(repo=root / "repo", db=root / "old.sqlite",
                script_path=SKILL_ROOT / "scripts/taskgov.py")
            self.assertEqual(module.initialize_feature_test_target(target).schema_version, 34)

    def test_fixture_inventory_resolves_aliases_helpers_and_inheritance(self):
        from tests.database_fixture_inventory_support import inventory
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / "tests"
            tests.mkdir()
            (tests / "base.py").write_text(
                "from tests.m14_test_support import initialize_taskgov_internal as create\n"
                "from tests import current_database_fixture_support as fixture\n"
                "def prepare(repo, db): create(repo=repo, db=db)\n"
                "class Seed:\n"
                "    def __init__(self): create(repo='repo', db='db')\n"
                "def template_guard(repo, db): fixture.EmptyDatabaseTemplate().initialize(db=db, repo=repo)\n", encoding="utf-8")
            (tests / "consumer.py").write_text(
                "from tests.base import prepare as seed, template_guard as check, Seed as Factory\n"
                "import unittest\n"
                "class Base(unittest.TestCase):\n"
                "    def setUp(self): seed('repo', 'db')\n"
                "class Derived(Base):\n"
                "    def test_case(self): check('repo', 'db'); Factory()\n", encoding="utf-8")
            result = inventory(root)
            self.assertEqual([(r["file"], r["function"], r["operation"])
                              for r in result["direct"]],
                             [("tests/base.py", "Seed.__init__", "initialize_taskgov_internal"),
                              ("tests/base.py", "prepare", "initialize_taskgov_internal"),
                              ("tests/base.py", "template_guard", "initialize")])
            self.assertIn("tests.consumer.Base.setUp", result["indirect"])
            self.assertEqual(result["indirect_routes"]["tests.consumer.Base.setUp"],
                             ["tests.base.prepare"])
            self.assertEqual(result["indirect_routes"]["tests.consumer.Derived.test_case"],
                             ["tests.base.Seed.__init__", "tests.base.template_guard"])
            self.assertEqual(result["inherited_classes"]["tests.consumer.Derived"], ["Base"])
            self.assertEqual(result["import_candidates"], ["tests/base.py", "tests/consumer.py"])


    def test_inventory_keeps_module_and_nested_import_scopes_separate(self):
        from tests.database_fixture_inventory_support import inventory
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / "tests"
            tests.mkdir()
            for name in ("base", "other"):
                (tests / (name + ".py")).write_text(
                    "from tests.m14_test_support import initialize_taskgov_internal\n"
                    "def prepare(): initialize_taskgov_internal(repo='repo', db='db')\n", encoding="utf-8")
            (tests / "consumer.py").write_text(
                "from tests.base import prepare as seed\n"
                "def global_case(): seed()\n"
                "def local_case():\n"
                "    from tests.other import prepare as seed\n"
                "    seed()\n"
                "def nested_case():\n"
                "    seed()\n"
                "    def nested():\n"
                "        from tests.other import prepare as seed\n"
                "        seed()\n"
                "    nested()\n", encoding="utf-8")
            with (tests / "consumer.py").open("a", encoding="utf-8") as stream:
                stream.write("def lambda_case(): return lambda: seed()\n")
            routes = inventory(root)["indirect_routes"]
            self.assertEqual(routes["tests.consumer.global_case"], ["tests.base.prepare"])
            self.assertEqual(routes["tests.consumer.local_case"], ["tests.other.prepare"])
            self.assertEqual(routes["tests.consumer.nested_case"], ["tests.base.prepare", "tests.other.prepare"])
            self.assertEqual(routes["tests.consumer.lambda_case"], ["tests.base.prepare"])

    def test_current_template_rejects_pinned_runtime_before_any_write(self):
        template = EmptyDatabaseTemplate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for version in (29, 36):
                with self.subTest(version=version), mock.patch.object(storage, "SCHEMA_VERSION", version):
                    with self.assertRaisesRegex(AssertionError, "pinned runtime"):
                        template.initialize(db=root / "absent" / "case.sqlite", repo=root / "repo")
                    self.assertFalse((root / "absent").exists())

    def test_mixed_feature_target_preserves_real_predecessor_initialization(self):
        from tests.current_database_fixture_support import initialize_feature_test_target
        from tests.test_review_session_migration import candidate_runtime
        with tempfile.TemporaryDirectory() as tmp, candidate_runtime(34):
            root = Path(tmp)
            target = storage.resolve_database_target(repo=root / "repo", db=root / "old.sqlite",
                script_path=SKILL_ROOT / "scripts/taskgov.py")
            with mock.patch("tests.current_database_fixture_support.initialize_current_test_target",
                            side_effect=AssertionError("predecessor must not be cloned")):
                result = initialize_feature_test_target(target, real_initialization=False)
            self.assertTrue(result.created)
            self.assertEqual(result.schema_version, 34)
            self.assertEqual(result.migrations_applied, list(range(1, 35)))
            with closing(storage.connect(target.db_path)) as connection:
                self.assertEqual([row[0] for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version")], list(range(1, 35)))

    def test_current_template_matches_real_initialization(self):
        def snapshot(path):
            with closing(storage.connect(path)) as connection:
                schema = [tuple(row) for row in connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
                rows = {}
                for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                    # Names come only from the real product's schema inventory.
                    cursor = connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"')
                    columns = [column[0] for column in cursor.description]
                    values = []
                    for row in cursor:
                        normalized = []
                        for column, value in zip(columns, row):
                            if value is not None and column == "database_epoch":
                                self.assertRegex(value, r"^[0-9a-f]{32}$")
                                value = "<epoch>"
                            elif isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
                                storage.validate_utc_timestamp(value, field=column)
                                value = "<timestamp>"
                            normalized.append(value)
                        values.append(tuple(normalized))
                    rows[name] = values
                return schema, rows, connection.execute("PRAGMA user_version").fetchone()[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real, copied = root / "real.sqlite", root / "copy.sqlite"
            before = initialize_taskgov_internal(repo=root / "repo", db=real)
            after = EmptyDatabaseTemplate().initialize(repo=root / "repo", db=copied)
            self.assertEqual(after, before)
            self.assertEqual(snapshot(copied), snapshot(real))

    def test_current_template_parallel_copies_are_independent_and_migrate_once(self):
        template = EmptyDatabaseTemplate()
        original = storage.apply_migrations
        full_migrations = []
        def observe(connection, **kwargs):
            if storage.current_schema_version(connection) == 0:
                full_migrations.append(1)
            return original(connection, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(storage, "apply_migrations", side_effect=observe):
            root = Path(tmp)
            def prepare(index):
                db = root / str(index) / "case.sqlite"
                result = template.initialize(repo=root / str(index) / "repo", db=db)
                with closing(storage.connect(db)) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
                    connection.execute("CREATE TABLE case_sentinel(value INTEGER)")
                    connection.execute("INSERT INTO case_sentinel VALUES (?)", (index,))
                    connection.commit()
                return db, result["project_id"]
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(prepare, (2, 0, 1)))
            self.assertEqual(full_migrations, [1])
            self.assertEqual(len({project for _, project in results}), 3)
            for index, (db, _) in zip((2, 0, 1), results):
                with closing(storage.connect(db)) as connection:
                    self.assertEqual(connection.execute("SELECT value FROM case_sentinel").fetchall()[0][0], index)
            final = root / "final.sqlite"
            template.initialize(repo=root / "final", db=final)
            with closing(storage.connect(final)) as connection:
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='case_sentinel'").fetchone())
            unchanged = final.read_bytes()
            with self.assertRaises(FileExistsError):
                template.initialize(repo=root / "other", db=final)
            self.assertEqual(final.read_bytes(), unchanged)

    def test_current_template_preparation_cost_includes_image_generation(self):
        from tests.m14_test_support import run_taskgov_internal
        measurements = []
        for copied in (False, True, True, False):
            template = EmptyDatabaseTemplate()
            prepare_times, operation_times, migrations = [], [], []
            original = storage.apply_migrations
            def observe(connection, **kwargs):
                if storage.current_schema_version(connection) == 0:
                    migrations.append(1)
                return original(connection, **kwargs)
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(storage, "apply_migrations", side_effect=observe):
                for index in range(4):
                    root = Path(tmp) / str(index)
                    db, repo = root / "case.sqlite", root / "repo"
                    started = time.perf_counter()
                    initialize = template.initialize if copied else initialize_taskgov_internal
                    initialize(repo=repo, db=db)
                    prepare_times.append(time.perf_counter() - started)
                    started = time.perf_counter()
                    result = run_taskgov_internal("task", "add", "--title", "Same measured operation",
                        "--repo", str(repo), "--db", str(db), "--json", maintenance_enabled=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    operation_times.append(time.perf_counter() - started)
            measurements.append({"copied": copied, "preparation_seconds": prepare_times,
                "operation_seconds": operation_times, "full_migrations": len(migrations)})
        self.assertEqual([row["full_migrations"] for row in measurements], [4, 1, 1, 4])
        print("CURRENT_DB_FIXTURE " + json.dumps(measurements, sort_keys=True))

    def test_fixture_can_seed_temp_database_through_task_cli(self):
        fixture = load_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "synthetic-repo"

            EmptyDatabaseTemplate().initialize(repo=repo, db=db)

            seeded = []
            for task in fixture["tasks"]:
                # Distinct fixture Tasks may represent different working sessions.
                self.enterContext(mock.patch.dict(os.environ, {"CODEX_THREAD_ID": str(uuid.uuid4())}))
                initial = dict(task)
                target_status = initial.get("status", "ready")
                if target_status in ("done", "review_pending"):
                    initial["status"] = "in_progress"
                stored = add_fixture_task(db, repo, initial)
                if target_status == "review_pending":
                    pending = run_taskgov("task", "edit", stored["task_id"], "--repo", str(repo),
                                          "--db", str(db), "--status", "review_pending", "--json")
                    self.assertEqual(pending.returncode, 0, pending.stdout)
                    # Write acknowledgements omit unchanged Task context.
                    stored.update(json.loads(pending.stdout)["data"]["task"])
                if target_status == "done":
                    seed_review_evidence(db, stored["task_id"])
                    shown = run_taskgov(
                        "task",
                        "show",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        stored["task_id"],
                        "--json",
                    )
                    self.assertEqual(shown.returncode, 0, shown.stderr)
                    target_generation = json.loads(shown.stdout)["data"]["task"][
                        "review_target_generation"
                    ]
                    verification_receipt = run_taskgov(
                        "verification",
                        "receipt",
                        "add",
                        stored["task_id"],
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        "--result",
                        "pass",
                        "--duration-ms",
                        "1",
                        "--scope-coverage",
                        "full",
                        "--expected-target-generation",
                        str(target_generation),
                        "--json",
                    )
                    self.assertEqual(
                        verification_receipt.returncode,
                        0,
                        verification_receipt.stderr or verification_receipt.stdout,
                    )
                    completed = run_taskgov(
                        "task",
                        "edit",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        stored["task_id"],
                        "--status",
                        "done",
                        "--verification-complete",
                        "--review-complete",
                        "--commit-not-required",
                        "--json",
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    stored.update(json.loads(completed.stdout)["data"]["task"])
                seeded.append(stored)

            self.assertEqual(len(seeded), 7)
            by_status = {}
            for task in seeded:
                by_status[task["status"]] = by_status.get(task["status"], 0) + 1
            self.assertEqual(by_status["ready"], 3)
            self.assertEqual(by_status["blocked"], 1)
            self.assertEqual(by_status["review_pending"], 1)
            self.assertEqual(by_status["done"], 1)
            self.assertEqual(by_status["cancelled"], 1)
            self.assertEqual(
                [(task["lane"], task["lane_order"]) for task in seeded if task["kind"] == "sequential"],
                [("CORE", 10), ("CORE", 20), ("CORE", 30), ("DOCS", 10)],
            )

            list_result = run_taskgov(
                "task",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--include-done",
                "--json",
            )
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            payload = json.loads(list_result.stdout)
            self.assertEqual(payload["data"]["count"], 7)
            listed = {task["task_id"]: task for task in payload["data"]["tasks"]}
            self.assertEqual(set(listed), {task["task_id"] for task in seeded})
            for task in seeded:
                with self.subTest(task_id=task["task_id"]):
                    for field in ("status", "kind", "lane", "lane_order"):
                        self.assertEqual(listed[task["task_id"]][field], task[field])
            self.assertFalse(repo.exists())


if __name__ == "__main__":
    unittest.main()
