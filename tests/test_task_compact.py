import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
try:
    from task_governance_tool.compact import (
        COMPACT_CURRENT_DATA_FIELDS,
        COMPACT_CURRENT_MAX_BYTES,
        COMPACT_CURRENT_TASK_FIELDS,
        COMPACT_EVENT_SUMMARY_MAX_BYTES,
        COMPACT_LATEST_EVENT_FIELDS,
        COMPACT_NEXT_DATA_FIELDS,
        COMPACT_NEXT_MAX_BYTES,
        COMPACT_NEXT_TASK_FIELDS,
        CompactProjectionError,
        build_compact_current_data,
        build_compact_next_data,
        compact_current_empty_data,
        compact_next_empty_data,
        project_compact_current_task,
        project_compact_next_task,
        truncate_utf8,
    )
    from task_governance_tool.cli import (
        CommandResult,
        fit_bounded_json_identity,
        fit_bounded_json_result,
        serialized_json_size,
    )
finally:
    sys.path.pop(0)


def current_task(index, *, long=False, summary="latest event"):
    reason = "b" * 1000 if long else "blocked"
    return {
        "task_id": f"tg_task_current_{index:03d}",
        "project_id": "private-project-id",
        "title": ("t" * 200) if long else f"Current {index}",
        "description": "omitted description",
        "status": "blocked",
        "kind": "optional",
        "lane": "CURRENT",
        "lane_order": index,
        "priority": "high",
        "review_tier": 2,
        "blocked_reason": reason,
        "pause_reason": "",
        "latest_event": {
            "task_event_id": f"tg_event_{index:03d}",
            "task_id": f"tg_task_current_{index:03d}",
            "project_id": "private-project-id",
            "event_type": "task_updated",
            "summary": summary,
            "created_at": "2026-07-27T00:00:00Z",
            "private": "omitted",
        },
        "suggested_next_action": (
            f"resolve or reassess the blocker: {reason}"
        ),
        "contract": {"scope": "omitted"},
        "checkpoint": {"summary": "omitted"},
    }


def next_task(index, *, long=False):
    return {
        "task_id": f"tg_task_next_{index:03d}",
        "project_id": "private-project-id",
        "title": ("t" * 200) if long else f"Next {index}",
        "description": "omitted description",
        "status": "ready",
        "kind": "optional",
        "lane": "NEXT",
        "lane_order": index,
        "priority": "normal",
        "review_tier": 2,
        "tags": ("g" * 500) if long else "compact",
        "verification": "omitted verification",
        "contract": {"scope": "omitted"},
        "checkpoint": {"summary": "omitted"},
    }


def envelope_size(command, data, *, warnings=()):
    payload = {
        "ok": True,
        "command": command,
        "project_id": "task-governance-tool-test",
        "db_path": "C:\\project\\.agents\\skills\\task-governance-tool\\state\\taskgov.sqlite",
        "data": data,
        "warnings": list(warnings),
        "errors": [],
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return len(rendered.replace("\n", "\r\n").encode("utf-8"))


def run_taskgov(*args):
    return subprocess.run(
        [sys.executable, "scripts/taskgov.py", *args],
        cwd=SKILL_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_taskgov_raw(*args):
    return subprocess.run(
        [sys.executable, "scripts/taskgov.py", *args],
        cwd=SKILL_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def init_db(db, repo):
    result = run_taskgov(
        "db",
        "init",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


def add_task(db, repo, title, *extra):
    result = run_taskgov(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
        *extra,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)["data"]["task"]


class CompactTaskProjectionTests(unittest.TestCase):
    def test_contract_allow_lists_are_exact(self):
        self.assertEqual(
            COMPACT_CURRENT_DATA_FIELDS,
            (
                "tasks",
                "total_matching",
                "returned_count",
                "limit",
                "statuses",
                "truncated",
            ),
        )
        self.assertEqual(
            COMPACT_CURRENT_TASK_FIELDS,
            (
                "task_id",
                "title",
                "status",
                "kind",
                "lane",
                "lane_order",
                "priority",
                "review_tier",
                "blocked_reason",
                "pause_reason",
                "latest_event",
                "suggested_next_action",
            ),
        )
        self.assertEqual(
            COMPACT_LATEST_EVENT_FIELDS,
            ("event_type", "summary", "created_at", "summary_truncated"),
        )
        self.assertEqual(
            COMPACT_NEXT_DATA_FIELDS,
            (
                "tasks",
                "total_matching",
                "returned_count",
                "limit",
                "truncated",
            ),
        )
        self.assertEqual(
            COMPACT_NEXT_TASK_FIELDS,
            (
                "task_id",
                "title",
                "kind",
                "lane",
                "lane_order",
                "priority",
                "review_tier",
                "tags",
                "suggested_next_action",
            ),
        )

    def test_utf8_truncation_preserves_code_point_boundaries(self):
        under = "あ" * 85
        exact = under + "a"
        over = exact + "é"

        self.assertEqual(len(under.encode("utf-8")), 255)
        self.assertEqual(truncate_utf8(under), (under, False))
        self.assertEqual(len(exact.encode("utf-8")), 256)
        self.assertEqual(truncate_utf8(exact), (exact, False))
        self.assertEqual(truncate_utf8(over), (exact, True))

        emoji_exact = "😀" * 64
        emoji_result, emoji_truncated = truncate_utf8(emoji_exact + "x")
        self.assertEqual(len(emoji_exact.encode("utf-8")), 256)
        self.assertEqual(emoji_result, emoji_exact)
        self.assertTrue(emoji_truncated)
        self.assertEqual(
            len(emoji_result.encode("utf-8")),
            COMPACT_EVENT_SUMMARY_MAX_BYTES,
        )

    def test_current_projection_has_exact_fields_and_bounded_event(self):
        projected = project_compact_current_task(
            current_task(1, summary=("😀" * 65))
        )

        self.assertEqual(tuple(projected), COMPACT_CURRENT_TASK_FIELDS)
        self.assertEqual(
            tuple(projected["latest_event"]),
            COMPACT_LATEST_EVENT_FIELDS,
        )
        self.assertEqual(projected["latest_event"]["summary"], "😀" * 64)
        self.assertTrue(projected["latest_event"]["summary_truncated"])
        self.assertLessEqual(
            len(projected["latest_event"]["summary"].encode("utf-8")),
            COMPACT_EVENT_SUMMARY_MAX_BYTES,
        )
        serialized = json.dumps(projected, sort_keys=True)
        for omitted in (
            "project_id",
            "description",
            "task_event_id",
            "contract",
            "checkpoint",
            "private",
        ):
            self.assertNotIn(omitted, serialized)

    def test_current_projection_preserves_empty_latest_event(self):
        task = current_task(1)
        task["latest_event"] = {}

        projected = project_compact_current_task(task)

        self.assertEqual(projected["latest_event"], {})

    def test_next_projection_has_exact_fields_and_existing_ready_action(self):
        projected = project_compact_next_task(next_task(1))

        self.assertEqual(tuple(projected), COMPACT_NEXT_TASK_FIELDS)
        self.assertEqual(
            projected["suggested_next_action"],
            "Start work, then update the task state when work begins or the status changes.",
        )
        serialized = json.dumps(projected, sort_keys=True)
        for omitted in (
            "status",
            "project_id",
            "description",
            "verification",
            "contract",
            "checkpoint",
        ):
            self.assertNotIn(f'"{omitted}"', serialized)

    def test_current_cap_keeps_only_a_complete_deterministic_prefix(self):
        tasks = [
            current_task(index, long=True, summary=("é" * 200))
            for index in range(20)
        ]
        size = lambda data: envelope_size("task.current", data)

        data = build_compact_current_data(
            tasks,
            total_matching=37,
            limit=20,
            statuses=("in_progress", "review_pending", "paused", "blocked"),
            serialized_size=size,
        )

        self.assertEqual(tuple(data), COMPACT_CURRENT_DATA_FIELDS)
        self.assertEqual(data["total_matching"], 37)
        self.assertEqual(data["returned_count"], len(data["tasks"]))
        self.assertGreater(data["returned_count"], 0)
        self.assertLess(data["returned_count"], len(tasks))
        self.assertTrue(data["truncated"])
        self.assertLessEqual(size(data), COMPACT_CURRENT_MAX_BYTES)
        self.assertEqual(
            [task["task_id"] for task in data["tasks"]],
            [
                task["task_id"]
                for task in tasks[: data["returned_count"]]
            ],
        )
        for task in data["tasks"]:
            self.assertEqual(tuple(task), COMPACT_CURRENT_TASK_FIELDS)
            self.assertEqual(len(task["blocked_reason"]), 1000)

        next_row = project_compact_current_task(
            tasks[data["returned_count"]]
        )
        too_large = dict(data)
        too_large["tasks"] = [*data["tasks"], next_row]
        too_large["returned_count"] += 1
        too_large["truncated"] = too_large["returned_count"] < len(tasks)
        self.assertGreater(size(too_large), COMPACT_CURRENT_MAX_BYTES)

    def test_next_cap_includes_warnings_and_keeps_complete_prefix(self):
        tasks = [next_task(index, long=True) for index in range(100)]
        warnings = (
            {
                "code": "paused_tasks_present",
                "message": (
                    "12 paused tasks exist; "
                    "run taskgov task current --status paused"
                ),
            },
        )
        size = lambda data: envelope_size(
            "task.next",
            data,
            warnings=warnings,
        )

        data = build_compact_next_data(
            tasks,
            total_matching=125,
            limit=100,
            serialized_size=size,
        )

        self.assertEqual(tuple(data), COMPACT_NEXT_DATA_FIELDS)
        self.assertEqual(data["total_matching"], 125)
        self.assertEqual(data["returned_count"], len(data["tasks"]))
        self.assertGreater(data["returned_count"], 0)
        self.assertLess(data["returned_count"], len(tasks))
        self.assertTrue(data["truncated"])
        self.assertLessEqual(size(data), COMPACT_NEXT_MAX_BYTES)
        self.assertEqual(
            [task["task_id"] for task in data["tasks"]],
            [
                task["task_id"]
                for task in tasks[: data["returned_count"]]
            ],
        )
        self.assertTrue(
            all(tuple(task) == COMPACT_NEXT_TASK_FIELDS for task in data["tasks"])
        )

        next_row = project_compact_next_task(tasks[data["returned_count"]])
        too_large = dict(data)
        too_large["tasks"] = [*data["tasks"], next_row]
        too_large["returned_count"] += 1
        too_large["truncated"] = too_large["returned_count"] < len(tasks)
        self.assertGreater(size(too_large), COMPACT_NEXT_MAX_BYTES)

    def test_query_limit_does_not_claim_byte_truncation(self):
        data = build_compact_next_data(
            [next_task(1)],
            total_matching=10,
            limit=1,
            serialized_size=lambda value: envelope_size("task.next", value),
        )

        self.assertEqual(data["total_matching"], 10)
        self.assertEqual(data["returned_count"], 1)
        self.assertFalse(data["truncated"])

    def test_empty_data_helpers_use_exact_contract_shapes(self):
        current = compact_current_empty_data(("paused",))
        next_data = compact_next_empty_data()

        self.assertEqual(tuple(current), COMPACT_CURRENT_DATA_FIELDS)
        self.assertEqual(
            current,
            {
                "tasks": [],
                "total_matching": 0,
                "returned_count": 0,
                "limit": 0,
                "statuses": ["paused"],
                "truncated": False,
            },
        )
        self.assertEqual(tuple(next_data), COMPACT_NEXT_DATA_FIELDS)
        self.assertEqual(
            next_data,
            {
                "tasks": [],
                "total_matching": 0,
                "returned_count": 0,
                "limit": 0,
                "truncated": False,
            },
        )

    def test_builder_rejects_an_oversized_rowless_envelope(self):
        with self.assertRaisesRegex(
            CompactProjectionError,
            "without task rows",
        ):
            build_compact_next_data(
                [],
                total_matching=0,
                limit=5,
                serialized_size=lambda data: COMPACT_NEXT_MAX_BYTES + 1,
            )

    def test_exact_rowless_metadata_controls_identity_fitting(self):
        placeholder = compact_next_empty_data(limit=100)
        exact_rowless = compact_next_empty_data(
            limit=100,
            total_matching=100,
            truncated=True,
        )
        prototype = CommandResult(
            ok=True,
            command="task.next",
            project_id="task-governance-tool-test",
            db_path="",
            data=exact_rowless,
        )
        boundary_length = (
            COMPACT_NEXT_MAX_BYTES
            - serialized_json_size(prototype, placeholder)
        )
        self.assertGreater(boundary_length, 0)
        boundary = replace(
            prototype,
            db_path="x" * boundary_length,
        )
        self.assertEqual(
            serialized_json_size(boundary, placeholder),
            COMPACT_NEXT_MAX_BYTES,
        )
        self.assertGreater(
            serialized_json_size(boundary, exact_rowless),
            COMPACT_NEXT_MAX_BYTES,
        )

        fitted = fit_bounded_json_identity(
            boundary,
            exact_rowless,
            max_bytes=COMPACT_NEXT_MAX_BYTES,
        )

        self.assertIsNone(fitted.db_path)
        data = build_compact_next_data(
            [next_task(index, long=True) for index in range(100)],
            total_matching=100,
            limit=100,
            serialized_size=lambda candidate: serialized_json_size(
                fitted,
                candidate,
            ),
        )
        self.assertLessEqual(
            serialized_json_size(fitted, data),
            COMPACT_NEXT_MAX_BYTES,
        )

    def test_sanitized_error_preserves_identity_that_then_fits(self):
        result = CommandResult(
            ok=False,
            command="task.next",
            project_id="tg-project",
            db_path="C:/short/taskgov.sqlite",
            data=compact_next_empty_data(limit=5),
            errors=[
                {
                    "code": "project_mismatch",
                    "message": "x" * (COMPACT_NEXT_MAX_BYTES * 2),
                }
            ],
            exit_code=2,
        )

        fitted = fit_bounded_json_result(
            result,
            max_bytes=COMPACT_NEXT_MAX_BYTES,
        )

        self.assertEqual(fitted.project_id, result.project_id)
        self.assertEqual(fitted.db_path, result.db_path)
        self.assertEqual(
            fitted.errors,
            [
                {
                    "code": "project_mismatch",
                    "message": (
                        "diagnostic details omitted to satisfy the "
                        "bounded output limit"
                    ),
                }
            ],
        )


class CompactTaskCliTests(unittest.TestCase):
    def test_compact_without_json_fails_before_state_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_db = Path(tmp) / "missing" / "taskgov.sqlite"

            for leaf in ("current", "next"):
                with self.subTest(leaf=leaf):
                    result = run_taskgov(
                        "task",
                        leaf,
                        "--compact",
                        "--repo",
                        str(Path(tmp) / "missing-repo"),
                        "--db",
                        str(missing_db),
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "--compact requires --json\n")
                    self.assertFalse(missing_db.exists())
                    self.assertFalse(missing_db.parent.exists())

    def test_compact_error_envelopes_bound_long_diagnostic_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            long_db = "x" * 26_000
            for leaf, cap in (
                ("current", COMPACT_CURRENT_MAX_BYTES),
                ("next", COMPACT_NEXT_MAX_BYTES),
            ):
                with self.subTest(leaf=leaf):
                    result = run_taskgov_raw(
                        "task",
                        leaf,
                        "--repo",
                        str(repo),
                        "--db",
                        long_db,
                        "--compact",
                        "--json",
                        "--read-only",
                    )

                    self.assertEqual(result.returncode, 2)
                    payload = json.loads(result.stdout.decode("utf-8"))
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        "db_not_initialized",
                    )
                    self.assertIsNone(payload["db_path"])
                    normalized = result.stdout.replace(b"\r\n", b"\n")
                    portable = normalized.replace(b"\n", b"\r\n")
                    self.assertLessEqual(len(portable), cap)
                    self.assertLessEqual(len(result.stdout), cap)

            long_repo_result = run_taskgov_raw(
                "task",
                "next",
                "--repo",
                "r" * 26_000,
                "--db",
                str(Path(tmp) / "missing.sqlite"),
                "--compact",
                "--json",
                "--read-only",
            )
            self.assertEqual(long_repo_result.returncode, 2)
            long_repo_payload = json.loads(
                long_repo_result.stdout.decode("utf-8")
            )
            self.assertEqual(
                long_repo_payload["errors"][0]["code"],
                "db_not_initialized",
            )
            self.assertIsNone(long_repo_payload["project_id"])
            self.assertIsNone(long_repo_payload["db_path"])
            normalized = long_repo_result.stdout.replace(b"\r\n", b"\n")
            portable = normalized.replace(b"\n", b"\r\n")
            self.assertLessEqual(len(portable), COMPACT_NEXT_MAX_BYTES)
            self.assertLessEqual(
                len(long_repo_result.stdout),
                COMPACT_NEXT_MAX_BYTES,
            )

    def test_compact_dynamic_and_parse_errors_use_final_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            owner_repo = Path(tmp) / "owner"
            init_db(db, owner_repo)
            long_repo = "r" * 26_000
            for leaf, cap in (
                ("current", COMPACT_CURRENT_MAX_BYTES),
                ("next", COMPACT_NEXT_MAX_BYTES),
            ):
                with self.subTest(kind="project_mismatch", leaf=leaf):
                    mismatch = run_taskgov_raw(
                        "task",
                        leaf,
                        "--repo",
                        long_repo,
                        "--db",
                        str(db),
                        "--compact",
                        "--json",
                        "--read-only",
                    )
                    self.assertEqual(mismatch.returncode, 2)
                    payload = json.loads(mismatch.stdout.decode("utf-8"))
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        "project_mismatch",
                    )
                    self.assertEqual(
                        payload["errors"][0]["message"],
                        (
                            "diagnostic details omitted to satisfy the "
                            "bounded output limit"
                        ),
                    )
                    normalized = mismatch.stdout.replace(b"\r\n", b"\n")
                    portable = normalized.replace(b"\n", b"\r\n")
                    self.assertLessEqual(len(portable), cap)
                    self.assertLessEqual(len(mismatch.stdout), cap)

                with self.subTest(kind="parse", leaf=leaf):
                    rejected = run_taskgov_raw(
                        "task",
                        leaf,
                        "--compact",
                        "--json",
                        "--unknown-" + ("u" * 26_000),
                    )
                    self.assertEqual(rejected.returncode, 1)
                    payload = json.loads(rejected.stdout.decode("utf-8"))
                    self.assertEqual(payload["command"], "parse")
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        "invalid_argument",
                    )
                    self.assertEqual(
                        payload["errors"][0]["message"],
                        (
                            "diagnostic details omitted to satisfy the "
                            "bounded output limit"
                        ),
                    )
                    normalized = rejected.stdout.replace(b"\r\n", b"\n")
                    portable = normalized.replace(b"\n", b"\r\n")
                    self.assertLessEqual(len(portable), cap)
                    self.assertLessEqual(len(rejected.stdout), cap)

                with self.subTest(kind="abbreviated", leaf=leaf):
                    abbreviated = run_taskgov_raw(
                        "task",
                        leaf,
                        "--repo",
                        long_repo,
                        "--db",
                        str(db),
                        "--comp",
                        "--j",
                        "--read-only",
                    )
                    self.assertEqual(abbreviated.returncode, 2)
                    payload = json.loads(
                        abbreviated.stdout.decode("utf-8")
                    )
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        "project_mismatch",
                    )
                    normalized = abbreviated.stdout.replace(
                        b"\r\n",
                        b"\n",
                    )
                    portable = normalized.replace(b"\n", b"\r\n")
                    self.assertLessEqual(len(portable), cap)
                    self.assertLessEqual(len(abbreviated.stdout), cap)

                with self.subTest(kind="abbreviated_parse", leaf=leaf):
                    abbreviated_parse = run_taskgov_raw(
                        "task",
                        leaf,
                        "--comp",
                        "--j",
                        "--unknown-" + ("u" * 26_000),
                    )
                    self.assertEqual(abbreviated_parse.returncode, 1)
                    payload = json.loads(
                        abbreviated_parse.stdout.decode("utf-8")
                    )
                    self.assertEqual(payload["command"], "parse")
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        "invalid_argument",
                    )
                    normalized = abbreviated_parse.stdout.replace(
                        b"\r\n",
                        b"\n",
                    )
                    portable = normalized.replace(b"\n", b"\r\n")
                    self.assertLessEqual(len(portable), cap)
                    self.assertLessEqual(
                        len(abbreviated_parse.stdout),
                        cap,
                    )

            end_of_options = run_taskgov_raw(
                "task",
                "next",
                "--",
                "--comp",
                "--j",
                "u" * 17_000,
            )
            self.assertEqual(end_of_options.returncode, 1)
            self.assertEqual(end_of_options.stdout, b"")
            self.assertNotEqual(end_of_options.stderr, b"")

    def test_compact_cli_uses_exact_shapes_and_limit_before_byte_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            for index in range(7):
                add_task(db, repo, f"Ready {index}")
            add_task(
                db,
                repo,
                "Current",
                "--status",
                "in_progress",
            )
            before = db.read_bytes()

            next_result = run_taskgov(
                "task",
                "next",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--limit",
                "2",
                "--compact",
                "--json",
                "--read-only",
            )
            current_result = run_taskgov(
                "task",
                "current",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--compact",
                "--json",
                "--read-only",
            )

            self.assertEqual(next_result.returncode, 0, next_result.stderr)
            self.assertEqual(current_result.returncode, 0, current_result.stderr)
            next_data = json.loads(next_result.stdout)["data"]
            current_data = json.loads(current_result.stdout)["data"]
            self.assertEqual(tuple(next_data), tuple(sorted(COMPACT_NEXT_DATA_FIELDS)))
            self.assertEqual(next_data["total_matching"], 7)
            self.assertEqual(next_data["returned_count"], 2)
            self.assertFalse(next_data["truncated"])
            self.assertEqual(
                set(next_data["tasks"][0]),
                set(COMPACT_NEXT_TASK_FIELDS),
            )
            self.assertEqual(
                tuple(current_data),
                tuple(sorted(COMPACT_CURRENT_DATA_FIELDS)),
            )
            self.assertEqual(current_data["total_matching"], 1)
            self.assertEqual(current_data["returned_count"], 1)
            self.assertEqual(
                set(current_data["tasks"][0]),
                set(COMPACT_CURRENT_TASK_FIELDS),
            )
            self.assertLessEqual(
                len(next_result.stdout.encode("utf-8")),
                COMPACT_NEXT_MAX_BYTES,
            )
            self.assertLessEqual(
                len(current_result.stdout.encode("utf-8")),
                COMPACT_CURRENT_MAX_BYTES,
            )
            self.assertEqual(db.read_bytes(), before)

    def test_compact_cli_caps_portable_raw_stdout_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            for index in range(20):
                extra = ["--tags", "g" * 500]
                if index == 14:
                    extra.extend(["--lane", "l" * 200])
                add_task(
                    db,
                    repo,
                    "t" * 200,
                    *extra,
                )
            paused = add_task(
                db,
                repo,
                "Paused warning source",
                "--status",
                "in_progress",
            )
            pause_result = run_taskgov(
                "task",
                "edit",
                paused["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--status",
                "paused",
                "--pause-reason",
                "Waiting for a deterministic dependency",
                "--json",
            )
            self.assertEqual(
                pause_result.returncode,
                0,
                pause_result.stderr,
            )

            result = run_taskgov_raw(
                "task",
                "next",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--limit",
                "100",
                "--compact",
                "--json",
                "--read-only",
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr.decode("utf-8", errors="replace"),
            )
            payload = json.loads(result.stdout.decode("utf-8"))
            self.assertTrue(payload["data"]["truncated"])
            self.assertGreater(payload["data"]["returned_count"], 0)
            self.assertLess(payload["data"]["returned_count"], 15)
            normalized = result.stdout.replace(b"\r\n", b"\n")
            portable = normalized.replace(b"\n", b"\r\n")
            self.assertGreater(len(portable), 15_000)
            self.assertLessEqual(
                len(portable),
                COMPACT_NEXT_MAX_BYTES,
            )
            self.assertLessEqual(
                len(result.stdout),
                COMPACT_NEXT_MAX_BYTES,
            )


if __name__ == "__main__":
    unittest.main()
