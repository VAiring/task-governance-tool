import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.cli import build_parser, handle_web_export, make_context  # noqa: E402
from task_governance_tool.storage import (  # noqa: E402
    default_viewer_output_path,
    resolve_database_target,
)
from task_governance_tool.viewer import (  # noqa: E402
    ViewerError,
    ViewerOutputTarget,
    has_windows_invalid_filename,
    resolve_viewer_output_target,
    validate_existing_output,
    write_viewer_html,
)


def run_taskgov(*args, skill_root=SKILL_ROOT, isolated=False):
    command = [sys.executable]
    if isolated:
        command.extend(["-I", "-S"])
    command.extend(["scripts/taskgov.py", *args])
    return subprocess.run(
        command,
        cwd=skill_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def init_db(db, repo, *, skill_root=SKILL_ROOT):
    result = run_taskgov(
        "db",
        "init",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--json",
        skill_root=skill_root,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def add_task(db, repo, title="Viewer task", *, skill_root=SKILL_ROOT):
    if not db.exists():
        init_db(db, repo, skill_root=skill_root)
    result = run_taskgov(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
        "--json",
        skill_root=skill_root,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)["data"]["task"]


def table_counts(db):
    with closing(sqlite3.connect(db)) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("tasks", "task_events", "tool_events", "schema_migrations")
        }


def embedded_snapshot(path):
    html = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="taskgov-snapshot" type="application/octet-stream">([^<]+)</script>',
        html,
    )
    if match is None:
        raise AssertionError("embedded snapshot was not found")
    return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))


def empty_export_data(output_path):
    return {
        "output_path": output_path,
        "written": False,
        "replaced": False,
        "task_count": 0,
        "event_count": 0,
        "generated_at": None,
        "snapshot_version": 2,
    }


class WebExportTests(unittest.TestCase):
    def test_web_export_help_exposes_output_and_read_only(self):
        result = run_taskgov("web", "export", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--output OUTPUT", result.stdout)
        self.assertIn("--read-only", result.stdout)
        self.assertIn("self-contained offline task viewer", result.stdout)

    def test_missing_web_subcommand_is_structured_validation_error(self):
        result = run_taskgov("--json", "web")

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "parse")
        self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
        self.assertIn("web requires a subcommand", payload["errors"][0]["message"])

    def test_common_options_work_before_web_command_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            output = root / "viewer.html"

            result = run_taskgov(
                "--repo", str(repo), "--db", str(db), "--json", "--read-only",
                "web", "export", "--output", str(output),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["command"], "web.export")
            self.assertFalse(payload["data"]["written"])
            self.assertFalse(output.exists())

    def test_explicit_export_writes_and_atomically_replaces_without_database_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "database" / "taskgov.sqlite"
            db.parent.mkdir()
            output = root / "exports" / "viewer.html"
            output.parent.mkdir()
            task = add_task(db, repo, "Rendered viewer task")
            counts_before = table_counts(db)

            first = run_taskgov(
                "web",
                "export",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--output",
                str(output),
                "--json",
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            payload = json.loads(first.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "web.export")
            self.assertEqual(payload["data"]["output_path"], str(output.resolve()))
            self.assertTrue(payload["data"]["written"])
            self.assertFalse(payload["data"]["replaced"])
            self.assertEqual(payload["data"]["task_count"], 1)
            self.assertEqual(payload["data"]["event_count"], 1)
            self.assertEqual(payload["data"]["snapshot_version"], 2)
            self.assertRegex(payload["data"]["generated_at"], r"Z$")
            snapshot = embedded_snapshot(output)
            self.assertEqual(snapshot["tasks"][0]["task_id"], task["task_id"])
            self.assertEqual(snapshot["tasks"][0]["title"], "Rendered viewer task")
            self.assertEqual(table_counts(db), counts_before)

            second = run_taskgov(
                "web",
                "export",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--output",
                str(output),
                "--json",
            )
            second_payload = json.loads(second.stdout)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertTrue(second_payload["data"]["replaced"])
            self.assertTrue(second_payload["data"]["written"])
            self.assertEqual(table_counts(db), counts_before)
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(str(db) + suffix).exists())
            self.assertEqual(list(output.parent.glob(".task-viewer-*.tmp")), [])

    def test_read_only_preview_creates_no_output_or_directory_and_writes_no_database_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.sqlite"
            add_task(db, repo)
            output = root / "exports" / "viewer.html"
            output.parent.mkdir()
            entries_before = set(output.parent.iterdir())
            counts_before = table_counts(db)

            result = run_taskgov(
                "web",
                "export",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--output",
                str(output),
                "--read-only",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["data"]["written"])
            self.assertFalse(payload["data"]["replaced"])
            self.assertEqual(payload["data"]["task_count"], 1)
            self.assertFalse(output.exists())
            self.assertEqual(set(output.parent.iterdir()), entries_before)
            self.assertEqual(table_counts(db), counts_before)

    def test_default_preview_path_is_skill_local_even_with_explicit_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "unique-viewer-repo"
            repo.mkdir()
            db = root / "outside" / "explicit.sqlite"
            db.parent.mkdir()
            init_payload = init_db(db, repo)
            expected = default_viewer_output_path(SKILL_ROOT, init_payload["project_id"])
            self.assertFalse(expected.parent.exists())

            result = run_taskgov(
                "web",
                "export",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--read-only",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["data"]["output_path"], str(expected))
            self.assertFalse(expected.parent.exists())
            self.assertNotEqual(expected.parent, db.parent)

    def test_invalid_explicit_output_paths_return_stable_empty_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "missing.sqlite"
            existing_directory = root / "directory.html"
            existing_directory.mkdir()
            cases = (
                (root / "viewer.txt", "output_path_invalid"),
                (root / "missing-parent" / "viewer.html", "output_parent_missing"),
                (existing_directory, "output_path_invalid"),
                (repo / "viewer.html", "output_path_invalid"),
            )

            for output, code in cases:
                with self.subTest(output=output, code=code):
                    result = run_taskgov(
                        "web",
                        "export",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        "--output",
                        str(output),
                        "--json",
                    )
                    self.assertEqual(result.returncode, 1, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["errors"][0]["code"], code)
                    self.assertEqual(payload["data"], empty_export_data(None))
                    self.assertFalse(db.exists())

    def test_symbolic_link_output_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            target_file = root / "target.html"
            target_file.write_text("original", encoding="utf-8")
            link = root / "linked.html"
            try:
                os.symlink(target_file, link)
            except OSError as exc:
                self.skipTest(f"symbolic links unavailable: {exc}")

            result = run_taskgov(
                "web",
                "export",
                "--repo",
                str(repo),
                "--db",
                str(root / "missing.sqlite"),
                "--output",
                str(link),
                "--json",
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "output_path_invalid")
            self.assertEqual(target_file.read_text(encoding="utf-8"), "original")

    def test_symbolic_link_detection_is_enforced_without_platform_privileges(self):
        path = Path("viewer-link.html")

        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(ViewerError) as failure:
                validate_existing_output(path)

        self.assertEqual(failure.exception.code, "output_path_invalid")

    def test_default_output_rejects_existing_directory_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skill"
            script_path = skill_root / "scripts" / "taskgov.py"
            target = resolve_database_target(
                repo=root / "repo",
                db=root / "taskgov.sqlite",
                script_path=script_path,
            )
            output = default_viewer_output_path(skill_root, target.project.project_id)
            output.mkdir(parents=True)

            with self.assertRaises(ViewerError) as failure:
                resolve_viewer_output_target(
                    output=None,
                    skill_root=skill_root,
                    database_target=target,
                )

            self.assertEqual(failure.exception.code, "output_path_invalid")

    def test_output_must_not_be_the_database_or_a_hard_link_to_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.html"
            init_db(db, repo)
            original = db.read_bytes()

            same_path = run_taskgov(
                "web", "export", "--repo", str(repo), "--db", str(db),
                "--output", str(db), "--json",
            )
            self.assertEqual(same_path.returncode, 1, same_path.stderr)
            self.assertEqual(json.loads(same_path.stdout)["errors"][0]["code"], "output_path_invalid")
            self.assertEqual(db.read_bytes(), original)

            linked_output = root / "linked-viewer.html"
            try:
                os.link(db, linked_output)
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")
            linked = run_taskgov(
                "web", "export", "--repo", str(repo), "--db", str(db),
                "--output", str(linked_output), "--json",
            )
            self.assertEqual(linked.returncode, 1, linked.stderr)
            self.assertEqual(json.loads(linked.stdout)["errors"][0]["code"], "output_path_invalid")
            self.assertEqual(db.read_bytes(), original)

    def test_default_output_must_not_equal_explicit_database_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skill"
            script_path = skill_root / "scripts" / "taskgov.py"
            preliminary = resolve_database_target(
                repo=root / "repo",
                db=root / "placeholder.sqlite",
                script_path=script_path,
            )
            output = default_viewer_output_path(skill_root, preliminary.project.project_id)
            target = resolve_database_target(
                repo=root / "repo",
                db=output,
                script_path=script_path,
            )

            with self.assertRaises(ViewerError) as failure:
                resolve_viewer_output_target(
                    output=None,
                    skill_root=skill_root,
                    database_target=target,
                )

            self.assertEqual(failure.exception.code, "output_path_invalid")

    @unittest.skipUnless(os.name == "nt", "Windows device path behavior")
    def test_windows_device_output_path_is_rejected_before_containment_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            device_output = "\\\\?\\" + str(repo / "viewer.html")

            result = run_taskgov(
                "web", "export", "--repo", str(repo),
                "--db", str(root / "missing.sqlite"),
                "--output", device_output, "--json",
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "output_path_invalid")
            self.assertEqual(payload["data"], empty_export_data(None))

    @unittest.skipUnless(os.name == "nt", "Windows reserved path behavior")
    def test_windows_reserved_output_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            for name in ("NUL.html", "con.htm", "COM1.HTML", "LPT\u00b9.html"):
                with self.subTest(name=name):
                    result = run_taskgov(
                        "web", "export", "--repo", str(repo),
                        "--db", str(root / "missing.sqlite"),
                        "--output", str(root / name), "--json",
                    )
                    self.assertEqual(result.returncode, 1, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["errors"][0]["code"], "output_path_invalid")
                    self.assertEqual(payload["data"], empty_export_data(None))

    @unittest.skipUnless(os.name == "nt", "Windows filename behavior")
    def test_windows_invalid_output_filename_characters_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            for name in ("viewer?.html", "bad|name.htm", 'quote".html'):
                with self.subTest(name=name):
                    result = run_taskgov(
                        "web", "export", "--repo", str(repo),
                        "--db", str(root / "missing.sqlite"),
                        "--output", str(root / name), "--json",
                    )
                    self.assertEqual(result.returncode, 1, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["errors"][0]["code"], "output_path_invalid")
                    self.assertEqual(payload["data"], empty_export_data(None))

        self.assertTrue(has_windows_invalid_filename("viewer\x01.html"))

    def test_default_output_reparse_parent_is_rejected_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skill"
            target = resolve_database_target(
                repo=root / "repo",
                db=root / "taskgov.sqlite",
                script_path=skill_root / "scripts" / "taskgov.py",
            )

            with patch(
                "task_governance_tool.viewer.path_is_reparse_point",
                side_effect=lambda path: path.name == "viewer",
            ):
                with self.assertRaises(ViewerError) as failure:
                    resolve_viewer_output_target(
                        output=None,
                        skill_root=skill_root,
                        database_target=target,
                    )

            self.assertEqual(failure.exception.code, "output_path_invalid")

    def test_explicit_output_parent_identity_change_is_rejected_before_temp_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "viewer.html"
            target = ViewerOutputTarget(
                path=output,
                explicit=True,
                parent_identity=(-1, -1),
            )

            with self.assertRaises(ViewerError) as failure:
                write_viewer_html(target, "new viewer")

            self.assertEqual(failure.exception.code, "output_path_invalid")
            self.assertFalse(output.exists())
            self.assertEqual(list(output.parent.glob(".task-viewer-*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_explicit_output_rejects_parent_replaced_by_junction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            approved_parent = root / "approved"
            approved_parent.mkdir()
            target = resolve_database_target(
                repo=repo,
                db=root / "taskgov.sqlite",
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )
            output_target = resolve_viewer_output_target(
                output=approved_parent / "viewer.html",
                skill_root=SKILL_ROOT,
                database_target=target,
            )
            moved_parent = root / "approved-original"
            approved_parent.rename(moved_parent)
            redirected = root / "redirected"
            redirected.mkdir()
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(approved_parent), str(redirected)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest(f"junctions unavailable: {created.stderr or created.stdout}")

            with self.assertRaises(ViewerError) as failure:
                write_viewer_html(output_target, "new viewer")

            self.assertEqual(failure.exception.code, "output_path_invalid")
            self.assertFalse((redirected / "viewer.html").exists())
            self.assertEqual(list(redirected.glob(".task-viewer-*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_default_output_rejects_windows_junction_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skill"
            target = resolve_database_target(
                repo=root / "repo",
                db=root / "taskgov.sqlite",
                script_path=skill_root / "scripts" / "taskgov.py",
            )
            output = default_viewer_output_path(skill_root, target.project.project_id)
            output.parent.parent.mkdir(parents=True)
            redirected = root / "redirected"
            redirected.mkdir()
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(output.parent), str(redirected)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest(f"junctions unavailable: {created.stderr or created.stdout}")

            with self.assertRaises(ViewerError) as failure:
                resolve_viewer_output_target(
                    output=None,
                    skill_root=skill_root,
                    database_target=target,
                )

            self.assertEqual(failure.exception.code, "output_path_invalid")
            self.assertFalse((redirected / "task-viewer.html").exists())

    def test_database_errors_keep_resolved_output_and_do_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "viewer.html"
            repo_one = root / "repo-one"
            repo_two = root / "repo-two"
            repo_one.mkdir()
            repo_two.mkdir()

            missing_db = root / "missing.sqlite"
            missing = run_taskgov(
                "web", "export", "--repo", str(repo_one), "--db", str(missing_db),
                "--output", str(output), "--json",
            )
            missing_payload = json.loads(missing.stdout)
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(missing_payload["errors"][0]["code"], "db_not_initialized")
            self.assertEqual(missing_payload["data"], empty_export_data(str(output.resolve())))

            incomplete_db = root / "incomplete.sqlite"
            with closing(sqlite3.connect(incomplete_db)):
                pass
            migration = run_taskgov(
                "web", "export", "--repo", str(repo_one), "--db", str(incomplete_db),
                "--output", str(output), "--json",
            )
            self.assertEqual(json.loads(migration.stdout)["errors"][0]["code"], "migration_required")

            owned_db = root / "owned.sqlite"
            init_db(owned_db, repo_one)
            mismatch = run_taskgov(
                "web", "export", "--repo", str(repo_two), "--db", str(owned_db),
                "--output", str(output), "--json",
            )
            self.assertEqual(json.loads(mismatch.stdout)["errors"][0]["code"], "project_mismatch")
            self.assertFalse(output.exists())

    def test_active_wal_rejection_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.sqlite"
            add_task(db, repo)
            output = root / "viewer.html"
            output.write_text("previous viewer", encoding="utf-8")

            with closing(sqlite3.connect(db)) as writer:
                writer.execute("PRAGMA journal_mode = WAL")
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE project_meta SET display_name = display_name")

                result = run_taskgov(
                    "web", "export", "--repo", str(repo), "--db", str(db),
                    "--output", str(output), "--json",
                )

                writer.rollback()

            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "internal_error")
            self.assertEqual(payload["data"], empty_export_data(str(output.resolve())))
            self.assertEqual(output.read_text(encoding="utf-8"), "previous viewer")

    def test_clean_persistent_wal_mode_is_rejected_without_creating_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
            for suffix in ("-wal", "-shm"):
                self.assertFalse(Path(str(db) + suffix).exists())
            output = root / "viewer.html"
            output.write_text("previous viewer", encoding="utf-8")

            result = run_taskgov(
                "web", "export", "--repo", str(repo), "--db", str(db),
                "--output", str(output), "--read-only", "--json",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "internal_error")
            self.assertIn("WAL journal mode", payload["errors"][0]["message"])
            self.assertEqual(payload["data"], empty_export_data(str(output.resolve())))
            self.assertEqual(output.read_text(encoding="utf-8"), "previous viewer")
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(str(db) + suffix).exists())

    def test_atomic_write_failure_preserves_original_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "viewer.html"
            output.write_text("original viewer", encoding="utf-8")
            parent = output.parent.stat()
            target = ViewerOutputTarget(
                path=output,
                explicit=True,
                parent_identity=(parent.st_dev, parent.st_ino),
            )

            with patch("task_governance_tool.viewer.os.replace", side_effect=OSError("failed")):
                with self.assertRaises(ViewerError) as failure:
                    write_viewer_html(target, "new viewer")

            self.assertEqual(failure.exception.code, "output_write_failed")
            self.assertEqual(output.read_text(encoding="utf-8"), "original viewer")
            self.assertEqual(list(output.parent.glob(".task-viewer-*.tmp")), [])

    def test_cli_maps_write_failure_to_command_specific_error_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            output = root / "viewer.html"
            args = build_parser().parse_args(
                [
                    "web", "export", "--repo", str(repo), "--db", str(db),
                    "--output", str(output), "--json",
                ]
            )

            with patch(
                "task_governance_tool.cli.write_viewer_html",
                side_effect=ViewerError("output_write_failed", "simulated failure"),
            ):
                result = handle_web_export(make_context(args))

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.errors[0]["code"], "output_write_failed")
            self.assertEqual(result.data, empty_export_data(str(output.resolve())))
            self.assertFalse(output.exists())

    def test_cli_maps_late_output_path_failure_to_usage_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            output = root / "viewer.html"
            args = build_parser().parse_args(
                [
                    "web", "export", "--repo", str(repo), "--db", str(db),
                    "--output", str(output), "--json",
                ]
            )

            with patch(
                "task_governance_tool.cli.write_viewer_html",
                side_effect=ViewerError("output_path_invalid", "late path change"),
            ):
                result = handle_web_export(make_context(args))

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.errors[0]["code"], "output_path_invalid")
            self.assertEqual(result.data, empty_export_data(str(output.resolve())))
            self.assertFalse(output.exists())

    def test_cli_maps_unexpected_render_failure_to_internal_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            output = root / "viewer.html"
            args = build_parser().parse_args(
                [
                    "web", "export", "--repo", str(repo), "--db", str(db),
                    "--output", str(output), "--json",
                ]
            )

            with patch(
                "task_governance_tool.cli.render_viewer_html",
                side_effect=ValueError("unexpected renderer failure"),
            ):
                result = handle_web_export(make_context(args))

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.errors[0]["code"], "internal_error")
            self.assertEqual(result.data, empty_export_data(str(output.resolve())))
            self.assertFalse(output.exists())

    def test_text_preview_output_is_concise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            output = root / "viewer.html"

            result = run_taskgov(
                "web", "export", "--repo", str(repo), "--db", str(db),
                "--output", str(output), "--read-only",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLessEqual(len(result.stdout.strip().splitlines()), 3)
            self.assertIn("Viewer previewed:", result.stdout)
            self.assertIn("Tasks: 0  Events: 0", result.stdout)
            self.assertIn("Generated:", result.stdout)

    def test_project_scoped_skill_copy_exports_default_and_approved_state_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target-project"
            installed = repo / ".agents" / "skills" / "task-governance-tool"
            installed.parent.mkdir(parents=True)
            shutil.copytree(
                SKILL_ROOT,
                installed,
                ignore=shutil.ignore_patterns("state", "__pycache__", "*.pyc", ".pytest_cache"),
            )

            init_result = run_taskgov(
                "db", "init", "--repo", str(repo), "--json",
                skill_root=installed, isolated=True,
            )
            self.assertEqual(init_result.returncode, 0, init_result.stderr)
            project_id = json.loads(init_result.stdout)["project_id"]
            add_result = run_taskgov(
                "task", "add", "--repo", str(repo), "--title", "Installed copy task", "--json",
                skill_root=installed, isolated=True,
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)

            exported = run_taskgov(
                "web", "export", "--repo", str(repo), "--json",
                skill_root=installed, isolated=True,
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
            payload = json.loads(exported.stdout)
            expected = default_viewer_output_path(installed, project_id)
            self.assertEqual(payload["data"]["output_path"], str(expected))
            self.assertTrue(expected.exists())
            self.assertEqual(embedded_snapshot(expected)["tasks"][0]["title"], "Installed copy task")

            approved = expected.parent / "approved.html"
            explicit = run_taskgov(
                "web", "export", "--repo", str(repo), "--output", str(approved), "--json",
                skill_root=installed, isolated=True,
            )
            self.assertEqual(explicit.returncode, 0, explicit.stderr)
            self.assertTrue(approved.exists())


if __name__ == "__main__":
    unittest.main()
