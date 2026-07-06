import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
FIXTURE_PATH = ROOT / "fixtures" / "task-status-mvp" / "tasks.json"


def run_taskgov(*args):
    return subprocess.run(
        [sys.executable, "scripts/taskgov.py", *args],
        cwd=SKILL_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


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
    return json.loads(result.stdout)["data"]["task"]


class TaskStatusFixtureTests(unittest.TestCase):
    def test_fixture_can_seed_temp_database_through_public_cli(self):
        fixture = load_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "synthetic-repo"

            seeded = [add_fixture_task(db, repo, task) for task in fixture["tasks"]]

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
            self.assertFalse(repo.exists())


if __name__ == "__main__":
    unittest.main()
