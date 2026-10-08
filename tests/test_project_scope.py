import errno
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout


try:
    from m14_test_support import (
        file_snapshot,
        json_payload,
        make_physical_install,
        tree_snapshot,
    )
except ModuleNotFoundError:
    from tests.m14_test_support import (
        file_snapshot,
        json_payload,
        make_physical_install,
        tree_snapshot,
    )

from task_governance_tool import doctor as doctor_service
from task_governance_tool import cli
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


class ProjectScopeStatePathTests(unittest.TestCase):
    def test_generated_targets_are_project_local_and_skill_stays_physical(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "project"
            skill = repo / ".agents" / "skills" / "task-governance-tool"
            skill.mkdir(parents=True)
            paths = project_scope_service.canonical_state_paths(skill, repo=repo)
            self.assertEqual(paths.state_root, repo.resolve() / ".taskgov")
            self.assertEqual(paths.skill_root, skill.resolve())
            self.assertTrue(project_scope_service._state_path_is_valid(skill, repo))
            self.assertFalse(paths.state_root.exists())

    def test_project_local_target_type_damage_is_rejected(self):
        for relative in (".taskgov", ".taskgov/current", ".taskgov/current/backups"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "project"
                skill = repo / "task-governance-tool"
                skill.mkdir(parents=True)
                damaged = repo / relative
                damaged.parent.mkdir(parents=True, exist_ok=True)
                damaged.write_bytes(b"not a directory")
                before = file_snapshot(repo)
                self.assertFalse(project_scope_service._state_path_is_valid(skill, repo))
                self.assertEqual(before, file_snapshot(repo))


class ProjectRootDiagnosisTests(unittest.TestCase):
    commands = (
        ("task", "context"),
        ("task", "add", "--title", "Must not be recorded"),
        ("doctor",),
        ("setup", "--read-only"),
        ("setup",),
    )
    inaccessible_message = (
        "project root could not be inspected safely; "
        "check access permissions and execution context"
    )

    def run_cli(self, install, command, repo, *, as_json=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cli, "cli_script_path", return_value=install.entrypoint),
            redirect_stdout(stdout), redirect_stderr(stderr),
        ):
            code = cli.main([
                *command, "--repo", str(repo), *(["--json"] if as_json else []),
            ])
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_failure(self, install, command, repo, expected):
        code, output, error = self.run_cli(install, command, repo)
        self.assertEqual(code, 2)
        self.assertEqual(error, "")
        payload = json.loads(output)
        self.assertEqual(set(payload), {"ok", "command", "project_id", "data", "warnings", "errors"})
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["project_id"])
        message = (self.inaccessible_message if expected == "project_root_uninspectable"
                   else "project root must be an existing directory")
        self.assertEqual(payload["errors"], [{"code": expected, "message": message}])
        self.assertEqual(payload["warnings"], [])
        self.assertNotIn(str(install.project_root), output)
        self.assertNotIn("private-detail", output)
        if command == ("task", "context"):
            self.assertEqual(payload["data"], {
                "selection": "none", "current": None, "next": None, "selected": None,
            })
        if command == ("doctor",):
            data = payload["data"]
            self.assertFalse(data["setup_eligible"])
            self.assertEqual(data["components"]["project_state"]["code"],
                "project_uninspectable" if expected == "project_root_uninspectable" else "invalid_project")
            for key in ("task_summary", "handoff_delivery", "maintenance"):
                self.assertEqual(data["components"][key], {"code": "unavailable"})

    def test_missing_non_directory_and_invalid_path_are_not_access_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            file = Path(tmp) / "file"
            file.write_text("fixture", encoding="utf-8")
            before = tree_snapshot(Path(tmp))
            for repo in (Path(tmp) / "missing", file, file / "child", "invalid\0path"):
                for command in self.commands:
                    with self.subTest(repo=str(repo), command=command):
                        self.assert_failure(install, command, repo, "invalid_project_root")
            self.assertEqual(tree_snapshot(Path(tmp)), before)

    def test_strict_resolution_failures_are_fixed_and_never_reach_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = tree_snapshot(install.project_root)
            original = Path.resolve
            for failure in (PermissionError, OSError, RuntimeError, FileNotFoundError, NotADirectoryError):
                expected = ("invalid_project_root" if failure in (FileNotFoundError, NotADirectoryError)
                            else "project_root_uninspectable")

                def resolve(path, strict=False):
                    if path == install.project_root and strict:
                        raise failure("private-detail")
                    return original(path, strict=strict)

                with (
                    mock.patch.object(Path, "resolve", resolve),
                    mock.patch.object(project_scope_service, "_state_path_is_valid") as state,
                    mock.patch.object(project_scope_service, "_state_is_ignored") as ignore,
                    mock.patch.object(cli, "resolve_project_state") as resolver,
                    mock.patch.object(doctor_service, "resolve_project_state") as doctor_resolver,
                    mock.patch.object(setup_service, "resolve_setup_project_state") as setup_resolver,
                ):
                    for command in self.commands:
                        with self.subTest(failure=failure.__name__, command=command):
                            self.assert_failure(install, command, install.project_root, expected)
                    for guarded in (state, ignore, resolver, doctor_resolver, setup_resolver):
                        guarded.assert_not_called()
                self.assertEqual(tree_snapshot(install.project_root), before)

    @unittest.skipUnless(os.name == "nt", "Windows native invalid-name semantics")
    def test_windows_invalid_names_return_invalid_root_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = tree_snapshot(install.project_root)
            for name in ("<bad>", "bad|name"):
                for command in self.commands:
                    with self.subTest(name=name, command=command):
                        self.assert_failure(install, command, install.project_root / name, "invalid_project_root")
            self.assertEqual(tree_snapshot(install.project_root), before)

    def test_only_explicit_windows_invalid_name_proves_invalid_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = tree_snapshot(install.project_root)
            invalid_name = OSError(errno.EINVAL, "private-detail")
            invalid_name.winerror = 123
            for failure, expected in (
                (invalid_name, "invalid_project_root"),
                (OSError(errno.EINVAL, "private-detail"), "project_root_uninspectable"),
                (PermissionError(errno.EACCES, "private-detail"), "project_root_uninspectable"),
            ):
                with mock.patch.object(project_scope_service, "_physical_directory", side_effect=failure):
                    for command in self.commands:
                        with self.subTest(expected=expected, command=command):
                            self.assert_failure(install, command, install.project_root, expected)
            self.assertEqual(tree_snapshot(install.project_root), before)

    def test_metadata_and_lexical_failures_do_not_claim_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = tree_snapshot(install.project_root)
            original = project_scope_service.os.lstat
            # Exercise actual metadata code, including the ancestor-link scan.
            for denied in (install.project_root, install.project_root.parent):
                def lstat(path, *args, **kwargs):
                    if Path(path) == denied:
                        raise PermissionError("private-detail")
                    return original(path, *args, **kwargs)

                with mock.patch.object(project_scope_service, "os", SimpleNamespace(
                    path=project_scope_service.os.path, lstat=lstat,
                )):
                    for command in self.commands:
                        with self.subTest(denied=denied.name, command=command):
                            self.assert_failure(install, command, install.project_root, "project_root_uninspectable")
            with mock.patch.object(project_scope_service, "_absolute_lexical_path", side_effect=RuntimeError("private-detail")):
                self.assert_failure(install, ("task", "context"), install.project_root, "project_root_uninspectable")
                code, output, error = self.run_cli(install, ("task", "context"), install.project_root, as_json=False)
                self.assertEqual(code, 2)
                self.assertIn(self.inaccessible_message, output + error)
                self.assertNotIn("private-detail", output + error)
                self.assertNotIn(str(install.project_root), output + error)
            self.assertEqual(tree_snapshot(install.project_root), before)

    def test_valid_context_needs_no_doctor_or_ignore_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            code, output, _ = self.run_cli(install, ("setup",), install.project_root)
            self.assertEqual(code, 0, output)
            before = tree_snapshot(install.project_root)
            with (
                mock.patch.object(doctor_service, "run_doctor") as doctor,
                mock.patch.object(project_scope_service, "_state_is_ignored") as ignore,
            ):
                code, output, _ = self.run_cli(install, ("task", "context"), install.project_root)
                self.assertEqual(code, 0, output)
                self.assertTrue(json.loads(output)["ok"])
                doctor.assert_not_called()
                ignore.assert_not_called()
            self.assertEqual(tree_snapshot(install.project_root), before)


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
                ("ordinary", ".taskgov/"),
                ("source", ".taskgov/"),
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
                "/nested/project/.taskgov/\n",
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
                    "/nested/project/.taskgov/\n"
                    "!/nested/project/.taskgov/\n"
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
            self.assertFalse((install.project_root / ".taskgov").exists())

    def test_linked_worktree_and_submodule_gitfile_use_nearest_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = root / "primary"
            linked = root / "linked"
            initialize_repository(primary, commit=True)
            run_git(primary, "worktree", "add", "--quiet", "--detach", str(linked))
            linked_install = make_physical_install(linked / "nested")
            (linked / ".gitignore").write_text(
                "/nested/project/.taskgov/\n",
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
                "/project/.taskgov/\n",
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
                "/.taskgov/\n",
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
