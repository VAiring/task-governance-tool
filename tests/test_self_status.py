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
try:
    from m14_test_support import make_physical_install
except ModuleNotFoundError:
    from tests.m14_test_support import make_physical_install


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


def create_windows_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


class SelfStatusTests(unittest.TestCase):
    def test_doctor_reuses_clean_package_inspection_without_writing(self):
        source_result = inspect_local_package(SKILL_ROOT, installed_version="0.7.0")
        self.assertEqual(source_result.status, "clean")
        self.assertEqual(source_result.changed_core_count, 0)

        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = content_snapshot(install.skill_root)

            result = install.run("doctor", "--read-only", "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "doctor")
            self.assertNotIn("db_path", payload)
            package = payload["data"]["components"]["package"]
            self.assertEqual(package["status"], "clean")
            self.assertEqual(package["changed_core_count"], 0)
            self.assertEqual(package["changed_core_paths"], [])
            self.assertEqual(package["unknown_reasons"], [])
            self.assertEqual(content_snapshot(install.skill_root), before)
            self.assertFalse((install.skill_root / "state").exists())
            self.assertFalse(any(install.skill_root.rglob("__pycache__")))

            text_result = install.run("doctor")
            self.assertEqual(text_result.returncode, 0, text_result.stderr)
            self.assertEqual(text_result.stdout, "Doctor: setup_required\n")
            self.assertEqual(content_snapshot(install.skill_root), before)

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

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_junction_install_is_diagnosed_and_stateful_commands_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            physical = copy_skill(root / "physical")
            write_manifest(physical)
            junction = root / "linked-skill"
            create_windows_junction(junction, physical)
            before = content_snapshot(physical)
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)

            with mock.patch.object(self_status_module, "_load_manifest") as load_manifest:
                direct = inspect_local_package(
                    junction,
                    installed_version="0.7.0",
                )
            load_manifest.assert_not_called()
            self.assertEqual(
                direct.unknown_reasons,
                ("unsupported_install_layout",),
            )

            repo = root / "target-project"
            repo.mkdir()
            status = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "scripts/taskgov.py",
                    "doctor",
                    "--repo",
                    str(repo),
                    "--json",
                ],
                cwd=junction,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(status.returncode, 2, status.stderr)
            status_payload = json.loads(status.stdout)
            package = status_payload["data"]["components"]["package"]
            self.assertEqual(package["status"], "unknown")
            self.assertEqual(
                package["unknown_reasons"],
                ["unsupported_install_layout"],
            )
            self.assertEqual(
                status_payload["data"]["suggested_action"],
                "continue",
            )
            self.assertEqual(status_payload["command"], "doctor")
            self.assertNotIn("db_path", status_payload)
            self.assertEqual(
                status_payload["data"]["components"]["project_state"]["code"],
                "invalid_layout",
            )
            self.assertEqual(status_payload["warnings"][0]["code"], "package_status_unknown")
            self.assertEqual(status_payload["errors"][0]["code"], "unsupported_install_layout")

            initialized = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "scripts/taskgov.py",
                    "setup",
                    "--repo",
                    str(repo),
                    "--json",
                ],
                cwd=junction,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(initialized.returncode, 2, initialized.stderr)
            initialized_payload = json.loads(initialized.stdout)
            self.assertEqual(initialized_payload["command"], "setup")
            self.assertNotIn("db_path", initialized_payload)
            self.assertEqual(
                initialized_payload["errors"],
                [{
                    "code": "unsupported_install_layout",
                    "message": (
                        "stateful use requires one supported physical "
                        "project-scoped package layout"
                    ),
                }],
            )
            serialized = json.dumps(initialized_payload)
            self.assertNotIn(str(junction), serialized)
            self.assertNotIn(str(physical), serialized)
            self.assertFalse((physical / "state").exists())
            self.assertTrue(repo.is_dir())
            self.assertEqual(list(repo.iterdir()), [])
            self.assertEqual(content_snapshot(physical), before)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_junction_skills_parent_cannot_redirect_project_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_skills = root / "shared-skills"
            shared_skills.mkdir()
            physical = copy_skill(shared_skills)
            write_manifest(physical)
            target = root / "target-project"
            agents = target / ".agents"
            agents.mkdir(parents=True)
            create_windows_junction(agents / "skills", shared_skills)
            before = content_snapshot(physical)
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            entrypoint = (
                ".agents/skills/task-governance-tool/scripts/taskgov.py"
            )

            status = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    entrypoint,
                    "doctor",
                    "--json",
                ],
                cwd=target,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(status.returncode, 2, status.stderr)
            status_payload = json.loads(status.stdout)
            package = status_payload["data"]["components"]["package"]
            self.assertEqual(package["status"], "unknown")
            self.assertEqual(
                package["unknown_reasons"],
                ["unsupported_install_layout"],
            )
            self.assertEqual(status_payload["command"], "doctor")
            self.assertNotIn("db_path", status_payload)
            self.assertEqual(
                status_payload["data"]["components"]["project_state"]["code"],
                "invalid_layout",
            )
            self.assertEqual(status_payload["warnings"][0]["code"], "package_status_unknown")
            self.assertEqual(status_payload["errors"][0]["code"], "unsupported_install_layout")

            initialized = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    entrypoint,
                    "setup",
                    "--json",
                ],
                cwd=target,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(initialized.returncode, 2, initialized.stderr)
            initialized_payload = json.loads(initialized.stdout)
            self.assertEqual(initialized_payload["command"], "setup")
            self.assertNotIn("db_path", initialized_payload)
            self.assertEqual(
                initialized_payload["errors"][0]["code"],
                "unsupported_install_layout",
            )
            serialized = json.dumps(initialized_payload)
            self.assertNotIn(str(agents / "skills"), serialized)
            self.assertNotIn(str(shared_skills), serialized)
            self.assertFalse((physical / "state").exists())
            self.assertEqual(content_snapshot(physical), before)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_junction_scripts_directory_cannot_redirect_project_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = copy_skill(root / "shared")
            write_manifest(shared)
            target = root / "target-project"
            installed = copy_skill(
                target / ".agents" / "skills",
            )
            write_manifest(installed)
            shutil.rmtree(installed / "scripts")
            create_windows_junction(installed / "scripts", shared / "scripts")
            shared_before = content_snapshot(shared)
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            entrypoint = (
                ".agents/skills/task-governance-tool/scripts/taskgov.py"
            )

            status = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    entrypoint,
                    "doctor",
                    "--json",
                ],
                cwd=target,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(status.returncode, 2, status.stderr)
            status_payload = json.loads(status.stdout)
            package = status_payload["data"]["components"]["package"]
            self.assertEqual(package["status"], "unknown")
            self.assertEqual(
                package["unknown_reasons"],
                ["unsupported_install_layout"],
            )
            self.assertEqual(
                status_payload["data"]["suggested_action"],
                "continue",
            )
            self.assertEqual(
                status_payload["data"]["components"]["project_state"]["code"],
                "invalid_layout",
            )

            initialized = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    entrypoint,
                    "setup",
                    "--json",
                ],
                cwd=target,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(initialized.returncode, 2, initialized.stderr)
            initialized_payload = json.loads(initialized.stdout)
            self.assertEqual(initialized_payload["command"], "setup")
            self.assertNotIn("db_path", initialized_payload)
            self.assertEqual(
                initialized_payload["errors"][0]["code"],
                "unsupported_install_layout",
            )
            self.assertFalse((shared / "state").exists())
            self.assertFalse((installed / "state").exists())
            self.assertEqual(content_snapshot(shared), shared_before)

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
