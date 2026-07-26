import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import self_status as self_status_module
from task_governance_tool.self_status import inspect_local_package


MANIFEST_NAME = "release-manifest.json"
EXCLUDED_ROOTS = {"adapters", "config", "state"}


def copy_skill(destination: Path) -> Path:
    copied = destination / "task-governance-tool"
    shutil.copytree(
        SKILL_ROOT,
        copied,
        ignore=shutil.ignore_patterns("state", "__pycache__", "*.pyc"),
    )
    return copied


def core_paths(skill_root: Path) -> list[Path]:
    paths = []
    for path in skill_root.rglob("*"):
        relative = path.relative_to(skill_root)
        if relative.parts[0] in EXCLUDED_ROOTS:
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if relative.as_posix() == MANIFEST_NAME:
            continue
        if path.is_file():
            paths.append(relative)
    return sorted(paths, key=lambda item: item.as_posix())


def write_manifest(
    skill_root: Path,
    *,
    package_version: str = "0.7.0",
    core_files: dict[str, str] | None = None,
) -> Path:
    if core_files is None:
        core_files = {
            relative.as_posix(): (
                "sha256:"
                + hashlib.sha256((skill_root / relative).read_bytes()).hexdigest()
            )
            for relative in core_paths(skill_root)
        }
    manifest = {
        "manifest_version": 1,
        "package_name": "task-governance-tool",
        "package_version": package_version,
        "release_origin": "github:VAiring/task-governance-tool",
        "core_files": dict(sorted(core_files.items())),
    }
    path = skill_root / MANIFEST_NAME
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def content_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class SelfStatusTests(unittest.TestCase):
    def test_isolated_cli_reports_clean_without_writing(self):
        source_result = inspect_local_package(SKILL_ROOT, installed_version="0.7.0")
        self.assertEqual(source_result.status, "clean")
        self.assertEqual(source_result.changed_core_count, 0)

        with tempfile.TemporaryDirectory() as tmp:
            copied = copy_skill(Path(tmp))
            write_manifest(copied)
            before = content_snapshot(copied)
            ignored_repo = Path(tmp) / "must-not-be-created"
            ignored_db = Path(tmp) / "must-not-exist.sqlite"
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)

            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "scripts/taskgov.py",
                    "self",
                    "status",
                    "--repo",
                    str(ignored_repo),
                    "--db",
                    str(ignored_db),
                    "--read-only",
                    "--json",
                ],
                cwd=copied,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "self.status")
            self.assertIsNone(payload["project_id"])
            self.assertIsNone(payload["db_path"])
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(
                payload["data"],
                {
                    "package_name": "task-governance-tool",
                    "package_version": "0.7.0",
                    "release_origin": "github:VAiring/task-governance-tool",
                    "manifest_version": 1,
                    "status": "clean",
                    "changed_core_count": 0,
                    "changed_core_paths": [],
                    "changed_core_paths_truncated": False,
                    "unknown_reasons": [],
                    "suggested_action": "continue",
                },
            )
            self.assertEqual(content_snapshot(copied), before)
            self.assertFalse((copied / "state").exists())
            self.assertFalse(any(copied.rglob("__pycache__")))
            self.assertFalse(ignored_repo.exists())
            self.assertFalse(ignored_db.exists())

            text_result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "scripts/taskgov.py",
                    "self",
                    "status",
                ],
                cwd=copied,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(text_result.returncode, 0, text_result.stderr)
            self.assertIn("Status: clean", text_result.stdout)
            self.assertIn("Suggested action: continue", text_result.stdout)
            self.assertEqual(content_snapshot(copied), before)

    def test_changed_missing_and_unexpected_core_are_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = copy_skill(Path(tmp))
            write_manifest(copied)
            (copied / "SKILL.md").write_text("locally changed\n", encoding="utf-8")
            (copied / "references" / "task_workflow.md").unlink()
            (copied / "unexpected.py").write_text("pass\n", encoding="utf-8")

            result = inspect_local_package(copied, installed_version="0.7.0")

            self.assertEqual(result.status, "modified")
            self.assertEqual(result.suggested_action, "continue")
            self.assertEqual(result.changed_core_count, 3)
            self.assertEqual(
                result.changed_core_paths,
                (
                    "SKILL.md",
                    "references/task_workflow.md",
                    "unexpected.py",
                ),
            )
            self.assertFalse(result.changed_core_paths_truncated)

    def test_changed_paths_are_sorted_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = copy_skill(Path(tmp))
            write_manifest(copied)
            for index in range(25):
                (copied / f"unexpected-{index:02d}.txt").write_text(
                    "local\n",
                    encoding="utf-8",
                )

            result = inspect_local_package(copied, installed_version="0.7.0")

            self.assertEqual(result.status, "modified")
            self.assertEqual(result.changed_core_count, 25)
            self.assertEqual(len(result.changed_core_paths), 20)
            self.assertEqual(
                list(result.changed_core_paths),
                sorted(result.changed_core_paths),
            )
            self.assertTrue(result.changed_core_paths_truncated)

    def test_declared_extension_and_generated_directories_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = copy_skill(Path(tmp))
            write_manifest(copied)
            for relative in (
                Path("config") / "local.json",
                Path("adapters") / "local.py",
                Path("state") / "projects" / "example" / "taskgov.sqlite",
                Path("scripts") / "task_governance_tool" / "__pycache__" / "local.pyc",
            ):
                path = copied / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Authorization: Bearer must-not-be-read\n", encoding="utf-8")

            result = inspect_local_package(copied, installed_version="0.7.0")

            self.assertEqual(result.status, "clean")
            serialized = json.dumps(result.to_data())
            self.assertNotIn("must-not-be-read", serialized)
            self.assertEqual(result.changed_core_count, 0)

    def test_manifest_failures_are_unknown_and_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copied = copy_skill(root / "missing")
            (copied / MANIFEST_NAME).unlink()
            missing = inspect_local_package(copied, installed_version="0.7.0")
            self.assertEqual(missing.status, "unknown")
            self.assertEqual(missing.unknown_reasons, ("manifest_missing",))
            self.assertEqual(missing.suggested_action, "continue")

            invalid = copy_skill(root / "invalid")
            (invalid / MANIFEST_NAME).write_text("{", encoding="utf-8")
            invalid_result = inspect_local_package(invalid, installed_version="0.7.0")
            self.assertEqual(invalid_result.status, "unknown")
            self.assertEqual(invalid_result.unknown_reasons, ("manifest_invalid",))

            mismatched = copy_skill(root / "mismatched")
            write_manifest(mismatched, package_version="9.9.9")
            mismatch_result = inspect_local_package(
                mismatched,
                installed_version="0.7.0",
            )
            self.assertEqual(mismatch_result.status, "unknown")
            self.assertEqual(
                mismatch_result.unknown_reasons,
                ("package_version_mismatch",),
            )

            traversal = copy_skill(root / "traversal")
            write_manifest(
                traversal,
                core_files={"../outside.txt": "sha256:" + ("0" * 64)},
            )
            outside = root / "outside.txt"
            outside.write_text("must-not-be-read", encoding="utf-8")
            traversal_result = inspect_local_package(
                traversal,
                installed_version="0.7.0",
            )
            self.assertEqual(traversal_result.status, "unknown")
            self.assertEqual(
                traversal_result.unknown_reasons,
                ("manifest_invalid",),
            )

            huge_integer = copy_skill(root / "huge-integer")
            huge_manifest = write_manifest(huge_integer)
            huge_manifest.write_text(
                huge_manifest.read_text(encoding="utf-8").replace(
                    '"manifest_version": 1',
                    '"manifest_version": ' + ("9" * 5000),
                ),
                encoding="utf-8",
            )
            huge_result = inspect_local_package(
                huge_integer,
                installed_version="0.7.0",
            )
            self.assertEqual(huge_result.status, "unknown")
            self.assertEqual(huge_result.unknown_reasons, ("manifest_invalid",))

            long_origin = copy_skill(root / "long-origin")
            origin_manifest = write_manifest(long_origin)
            origin_payload = json.loads(origin_manifest.read_text(encoding="utf-8"))
            origin_payload["release_origin"] = "github:" + ("a" * 201) + "/repo"
            origin_manifest.write_text(
                json.dumps(origin_payload),
                encoding="utf-8",
            )
            origin_result = inspect_local_package(
                long_origin,
                installed_version="0.7.0",
            )
            self.assertEqual(origin_result.status, "unknown")
            self.assertEqual(origin_result.unknown_reasons, ("manifest_invalid",))

    def test_entry_limit_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            limited = copy_skill(root / "limited")
            write_manifest(limited)
            with mock.patch.object(
                self_status_module,
                "MAX_PACKAGE_ENTRIES",
                3,
            ):
                limited_result = inspect_local_package(
                    limited,
                    installed_version="0.7.0",
                )
            self.assertEqual(limited_result.status, "unknown")
            self.assertEqual(
                limited_result.unknown_reasons,
                ("inspection_limit_exceeded",),
            )

    def test_persistent_directory_replacement_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copied = copy_skill(root / "installed")
            write_manifest(copied)
            replacement = root / "replacement"
            previous = root / "previous"
            shutil.copytree(copied / "references", replacement)
            original_hash = self_status_module._hash_core_file
            replaced = False

            def replace_before_hash(path, *, remaining_total_bytes):
                nonlocal replaced
                if not replaced and path.parent.name == "references":
                    path.parent.rename(previous)
                    replacement.rename(path.parent)
                    replaced = True
                return original_hash(
                    path,
                    remaining_total_bytes=remaining_total_bytes,
                )

            with mock.patch.object(
                self_status_module,
                "_hash_core_file",
                side_effect=replace_before_hash,
            ):
                result = inspect_local_package(
                    copied,
                    installed_version="0.7.0",
                )

            self.assertTrue(replaced)
            self.assertEqual(result.status, "unknown")
            self.assertEqual(result.unknown_reasons, ("inspection_incomplete",))
            self.assertEqual(result.suggested_action, "continue")


if __name__ == "__main__":
    unittest.main()
