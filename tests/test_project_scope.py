import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


try:
    from m14_test_support import (
        file_snapshot,
        json_payload,
        make_physical_install,
    )
except ModuleNotFoundError:
    from tests.m14_test_support import (
        file_snapshot,
        json_payload,
        make_physical_install,
    )

from task_governance_tool import doctor as doctor_service
from task_governance_tool import project_scope as project_scope_service
from task_governance_tool import setup as setup_service
from task_governance_tool.completion import safe_git_environment


def run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Taskgov Tests",
            "-c",
            "user.email=taskgov-tests@example.invalid",
            "-C",
            str(repo),
            *arguments,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def initialize_repository(repo: Path, *, commit: bool = False) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, "init", "--quiet")
    if commit:
        (repo / "anchor.txt").write_text("fixture\n", encoding="utf-8")
        run_git(repo, "add", "anchor.txt")
        run_git(repo, "commit", "--quiet", "-m", "fixture")


class ProjectScopeIgnoreTests(unittest.TestCase):
    def test_no_marker_skips_git_and_scan_error_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "project"
            repo.mkdir()

            with mock.patch.object(
                project_scope_service.subprocess,
                "run",
            ) as run:
                self.assertTrue(
                    project_scope_service._state_is_ignored(repo, "ordinary")
                )
                run.assert_not_called()

            with (
                mock.patch.object(
                    project_scope_service.os,
                    "lstat",
                    side_effect=PermissionError,
                ),
                mock.patch.object(
                    project_scope_service.subprocess,
                    "run",
                ) as run,
            ):
                self.assertFalse(
                    project_scope_service._state_is_ignored(repo, "ordinary")
                )
                run.assert_not_called()

    def test_process_contract_uses_one_fixed_directory_operand(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = (Path(tmp) / "project").resolve()
            repo.mkdir()
            cases = (
                ("ordinary", ".agents/skills/task-governance-tool/state/"),
                ("source", "task-governance-tool/state/"),
            )
            for layout, operand in cases:
                with (
                    self.subTest(layout=layout),
                    mock.patch.object(
                        project_scope_service,
                        "_has_enclosing_git_marker",
                        return_value=True,
                    ),
                    mock.patch.object(
                        project_scope_service.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], 0),
                    ) as run,
                ):
                    self.assertTrue(
                        project_scope_service._state_is_ignored(repo, layout)
                    )
                    run.assert_called_once()
                    command = run.call_args.args[0]
                    self.assertEqual(
                        command,
                        [
                            "git",
                            "-c",
                            f"safe.directory={repo.as_posix()}",
                            "-C",
                            str(repo),
                            "-c",
                            "core.fsmonitor=false",
                            "check-ignore",
                            "--quiet",
                            "--no-index",
                            "--",
                            operand,
                        ],
                    )
                    self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
                    self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
                    self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
                    self.assertEqual(run.call_args.kwargs["timeout"], 2)
                    self.assertFalse(run.call_args.kwargs["check"])
                    self.assertFalse(run.call_args.kwargs["shell"])
                    self.assertEqual(
                        run.call_args.kwargs["env"],
                        safe_git_environment(),
                    )

    def test_process_failures_all_reject(self):
        failures = (
            subprocess.CompletedProcess([], 1),
            subprocess.CompletedProcess([], 128),
            subprocess.TimeoutExpired("git", 2),
            OSError("launch failed"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "project"
            repo.mkdir()
            for failure in failures:
                kwargs = (
                    {"return_value": failure}
                    if isinstance(failure, subprocess.CompletedProcess)
                    else {"side_effect": failure}
                )
                with (
                    self.subTest(failure=type(failure).__name__),
                    mock.patch.object(
                        project_scope_service,
                        "_has_enclosing_git_marker",
                        return_value=True,
                    ),
                    mock.patch.object(
                        project_scope_service.subprocess,
                        "run",
                        **kwargs,
                    ) as run,
                ):
                    self.assertFalse(
                        project_scope_service._state_is_ignored(repo, "ordinary")
                    )
                    run.assert_called_once()

    def test_enclosing_rule_accepts_nested_target_without_rerooting(self):
        with tempfile.TemporaryDirectory() as tmp:
            enclosing = Path(tmp) / "worktree"
            initialize_repository(enclosing)
            install = make_physical_install(enclosing / "nested")
            (enclosing / ".gitignore").write_text(
                "/nested/project/.agents/skills/task-governance-tool/state/\n",
                encoding="utf-8",
            )

            setup = install.run("setup", "--json")

            self.assertEqual(setup.returncode, 0, setup.stderr)
            payload = json_payload(setup)
            self.assertEqual(payload["project_id"], install.project_id)
            self.assertTrue(install.db_path.is_file())
            self.assertFalse((enclosing / ".agents").exists())

            target_before = file_snapshot(install.project_root)
            git_before = file_snapshot(enclosing / ".git")
            doctor = install.run("doctor", "--json")
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            self.assertEqual(file_snapshot(install.project_root), target_before)
            self.assertEqual(file_snapshot(enclosing / ".git"), git_before)

    def test_effective_negation_rejects_setup_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            enclosing = Path(tmp) / "worktree"
            initialize_repository(enclosing)
            install = make_physical_install(enclosing / "nested")
            (enclosing / ".gitignore").write_text(
                (
                    "/nested/project/.agents/skills/task-governance-tool/state/\n"
                    "!/nested/project/.agents/skills/task-governance-tool/state/\n"
                ),
                encoding="utf-8",
            )
            before = file_snapshot(enclosing)

            result = install.run("setup", "--json")

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                json_payload(result)["errors"],
                [{
                    "code": "state_ignore_required",
                    "message": "project-local state must be ignored before setup",
                }],
            )
            self.assertEqual(file_snapshot(enclosing), before)
            self.assertFalse((install.skill_root / "state").exists())

    def test_linked_worktree_and_submodule_gitfile_use_nearest_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = root / "primary"
            linked = root / "linked"
            initialize_repository(primary, commit=True)
            run_git(primary, "worktree", "add", "--quiet", "--detach", str(linked))
            linked_install = make_physical_install(linked / "nested")
            (linked / ".gitignore").write_text(
                "/nested/project/.agents/skills/task-governance-tool/state/\n",
                encoding="utf-8",
            )
            self.assertTrue((linked / ".git").is_file())
            self.assertTrue(
                project_scope_service._state_is_ignored(
                    linked_install.project_root,
                    "ordinary",
                )
            )

            superproject = root / "superproject"
            initialize_repository(superproject)
            nested_install = make_physical_install(superproject)
            separate_admin = root / "nested-admin"
            init = subprocess.run(
                [
                    "git",
                    "init",
                    "--quiet",
                    f"--separate-git-dir={separate_admin}",
                    str(nested_install.project_root),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            (superproject / ".gitignore").write_text(
                "/project/.agents/skills/task-governance-tool/state/\n",
                encoding="utf-8",
            )
            self.assertTrue((nested_install.project_root / ".git").is_file())
            self.assertFalse(
                project_scope_service._state_is_ignored(
                    nested_install.project_root,
                    "ordinary",
                )
            )
            (nested_install.project_root / ".gitignore").write_text(
                "/.agents/skills/task-governance-tool/state/\n",
                encoding="utf-8",
            )
            self.assertTrue(
                project_scope_service._state_is_ignored(
                    nested_install.project_root,
                    "ordinary",
                )
            )

    def test_setup_preview_write_and_doctor_each_check_ignore_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview_install = make_physical_install(
                Path(tmp) / "preview",
                git_managed=True,
            )
            original = project_scope_service._state_is_ignored
            with mock.patch.object(
                project_scope_service,
                "_state_is_ignored",
                wraps=original,
            ) as check:
                preview = setup_service.run_setup(
                    repo=str(preview_install.project_root),
                    repo_explicit=True,
                    script_path=preview_install.entrypoint,
                    read_only=True,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )
                self.assertTrue(preview.ok)
                self.assertEqual(check.call_count, 1)

            install = make_physical_install(Path(tmp) / "write", git_managed=True)
            with mock.patch.object(
                project_scope_service,
                "_state_is_ignored",
                wraps=original,
            ) as check:
                setup = setup_service.run_setup(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    read_only=False,
                    backup_interval_minutes=None,
                    backup_generations=None,
                )
                self.assertTrue(setup.ok)
                self.assertEqual(check.call_count, 1)

            with mock.patch.object(
                project_scope_service,
                "_state_is_ignored",
                wraps=original,
            ) as check:
                doctor = doctor_service.run_doctor(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                )
                self.assertTrue(doctor.ok)
                self.assertEqual(check.call_count, 1)

            with mock.patch.object(
                project_scope_service,
                "_state_is_ignored",
            ) as check:
                inspection = project_scope_service.inspect_project_scope(
                    repo=install.project_root,
                    repo_explicit=True,
                    script_path=install.entrypoint,
                    include_runtime=False,
                    include_package=False,
                    include_ignore=False,
                )
                self.assertIsNotNone(inspection.scope)
                check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
