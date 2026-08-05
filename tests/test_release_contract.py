from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.release_contract import (
    CHECKER_INVOCATION,
    EXPECTED_RELEASE_ORIGIN,
    OFFICIAL_APACHE_2_LICENSE_SHA256,
    check_release_contract,
    collect_runtime_contract,
    forbidden_tracked_artifact,
)
from tools.test_lanes import (
    CI_CHECK_INVOCATION,
    CI_LANE_INVOCATION,
    CI_MATRIX_INVOCATION,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"


def tracked_paths() -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError("tracked test inventory is unavailable")
    return tuple(
        item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    )


BASE_TRACKED_PATHS = tracked_paths()
RUNTIME_CONTRACT = collect_runtime_contract()


def copy_release_fixture(destination: Path) -> Path:
    fixture = destination / "repository"
    fixture.mkdir()
    shutil.copy2(ROOT / "LICENSE", fixture / "LICENSE")
    shutil.copy2(ROOT / "README.md", fixture / "README.md")

    (fixture / ".github" / "workflows").mkdir(parents=True)
    shutil.copy2(
        ROOT / ".github" / "workflows" / "ci.yml",
        fixture / ".github" / "workflows" / "ci.yml",
    )

    (fixture / "tools").mkdir()
    for name in ("release_contract.py", "test_lanes.py"):
        shutil.copy2(ROOT / "tools" / name, fixture / "tools" / name)

    (fixture / "docs" / "releases").mkdir(parents=True)
    for relative in (
        "specification.md",
        "design.md",
        "release-install.md",
        "releases/v0.10.0.md",
        "releases/v0.11.0.md",
        "releases/v0.12.0.md",
    ):
        source = ROOT / "docs" / Path(*relative.split("/"))
        target = fixture / "docs" / Path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    shutil.copytree(
        SKILL_ROOT,
        fixture / "task-governance-tool",
        ignore=shutil.ignore_patterns(
            "config",
            "state",
            "__pycache__",
            "*.pyc",
            ".pytest_cache",
        ),
    )
    return fixture


def file_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file()
    )


def issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def check_fixture(
    fixture: Path,
    *,
    inventory: tuple[str, ...] = BASE_TRACKED_PATHS,
):
    return check_release_contract(
        fixture,
        tracked_paths=inventory,
        runtime_contract=RUNTIME_CONTRACT,
    )


class ReleaseContractCheckerTests(unittest.TestCase):
    def test_current_repository_passes_with_owner_derived_runtime_values(self):
        result = check_release_contract(ROOT)
        runtime = collect_runtime_contract()

        self.assertTrue(result.ok, result.issues)
        self.assertEqual(result.runtime, runtime)
        self.assertEqual(len(runtime.public_commands), 21)
        self.assertEqual(result.ci_python_versions, ("3.12", "3.14"))
        self.assertEqual(result.manifest_core_count, 44)
        manifest = json.loads(
            (SKILL_ROOT / "release-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["release_origin"], EXPECTED_RELEASE_ORIGIN)
        self.assertEqual(
            hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest(),
            OFFICIAL_APACHE_2_LICENSE_SHA256,
        )

    def test_clean_fixture_is_deterministic_and_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = copy_release_fixture(Path(temporary))
            before = file_snapshot(fixture)

            first = check_fixture(fixture)
            second = check_fixture(fixture)

            self.assertTrue(first.ok, first.issues)
            self.assertEqual(first, second)
            self.assertEqual(file_snapshot(fixture), before)
            self.assertFalse(any(fixture.rglob("__pycache__")))

    def test_manifest_missing_declared_missing_extra_and_hash_mismatch_fail(self):
        cases = (
            (
                "manifest_missing",
                lambda fixture: (
                    fixture
                    / "task-governance-tool"
                    / "release-manifest.json"
                ).unlink(),
                BASE_TRACKED_PATHS,
                "manifest_missing",
            ),
            (
                "declared_file_missing",
                lambda fixture: (
                    fixture
                    / "task-governance-tool"
                    / "assets"
                    / "task-viewer.template.html"
                ).unlink(),
                BASE_TRACKED_PATHS,
                "package_integrity_mismatch",
            ),
            (
                "extra_core_file",
                lambda fixture: (
                    fixture / "task-governance-tool" / "unexpected.txt"
                ).write_text("unexpected\n", encoding="utf-8"),
                (*BASE_TRACKED_PATHS, "task-governance-tool/unexpected.txt"),
                "package_integrity_mismatch",
            ),
            (
                "hash_mismatch",
                lambda fixture: (
                    fixture / "task-governance-tool" / "SKILL.md"
                ).write_text("changed\n", encoding="utf-8"),
                BASE_TRACKED_PATHS,
                "package_integrity_mismatch",
            ),
        )
        for name, mutate, inventory, expected_code in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                fixture = copy_release_fixture(Path(temporary))
                mutate(fixture)

                result = check_fixture(fixture, inventory=inventory)

                self.assertFalse(result.ok)
                self.assertIn(expected_code, issue_codes(result))

    def test_license_mismatch_manifest_coverage_and_notice_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = copy_release_fixture(Path(temporary))
            (fixture / "LICENSE").write_text(
                "not the approved license\n",
                encoding="utf-8",
            )

            mismatch = check_fixture(fixture)

            self.assertIn("license_mismatch", issue_codes(mismatch))
            self.assertIn("license_not_official", issue_codes(mismatch))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = copy_release_fixture(Path(temporary))
            manifest_path = (
                fixture / "task-governance-tool" / "release-manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["core_files"].pop("LICENSE")
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (fixture / "NOTICE").write_text("unreviewed\n", encoding="utf-8")

            result = check_fixture(
                fixture,
                inventory=(*BASE_TRACKED_PATHS, "NOTICE"),
            )

            self.assertIn("license_manifest_mismatch", issue_codes(result))
            self.assertIn("notice_present", issue_codes(result))

    def test_manifest_skill_and_agent_metadata_invalidity_fail(self):
        cases = (
            (
                "manifest",
                lambda fixture: _replace_manifest_origin(
                    fixture,
                    "not-a-release-origin",
                ),
                "manifest_invalid",
            ),
            (
                "valid_but_wrong_origin",
                lambda fixture: _replace_manifest_origin(
                    fixture,
                    "github:OtherOwner/other-repository",
                ),
                "release_origin_mismatch",
            ),
            (
                "skill",
                lambda fixture: (
                    fixture / "task-governance-tool" / "SKILL.md"
                ).write_text(
                    "---\nname: wrong-name\ndescription: task trigger\n---\n",
                    encoding="utf-8",
                ),
                "skill_metadata_invalid",
            ),
            (
                "agent",
                lambda fixture: (
                    fixture
                    / "task-governance-tool"
                    / "agents"
                    / "openai.yaml"
                ).write_text(
                    'interface:\n  short_description: "too short"\n',
                    encoding="utf-8",
                ),
                "agent_metadata_invalid",
            ),
        )
        for name, mutate, expected_code in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                fixture = copy_release_fixture(Path(temporary))
                mutate(fixture)

                result = check_fixture(fixture)

                self.assertFalse(result.ok)
                self.assertIn(expected_code, issue_codes(result))

    def test_each_command_inventory_and_release_identity_drift_fail(self):
        command_documents = (
            ("README.md", "`taskgov task show`", "`taskgov task bogus`"),
            (
                "docs/specification.md",
                "`taskgov task show`",
                "`taskgov task bogus`",
            ),
            (
                "docs/design.md",
                "\ntask show\n",
                "\ntask bogus\n",
            ),
            (
                "docs/release-install.md",
                "`taskgov task show`",
                "`taskgov task bogus`",
            ),
            (
                "task-governance-tool/references/cli_contracts.md",
                "8. `task show`",
                "8. `task bogus`",
            ),
        )
        for relative, original, changed in command_documents:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                fixture = copy_release_fixture(Path(temporary))
                path = fixture / Path(*relative.split("/"))
                text = path.read_text(encoding="utf-8")
                self.assertIn(original, text)
                path.write_text(text.replace(original, changed, 1), encoding="utf-8")

                result = check_fixture(fixture)

                self.assertIn("documented_cli_mismatch", issue_codes(result))
                self.assertIn(
                    relative,
                    {issue.subject for issue in result.issues},
                )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = copy_release_fixture(Path(temporary))
            release = fixture / "docs" / "release-install.md"
            release.write_text(
                release.read_text(encoding="utf-8").replace(
                    "| SQLite schema | v19 |",
                    "| SQLite schema | v99 |",
                    1,
                ),
                encoding="utf-8",
            )

            result = check_fixture(fixture)

            self.assertIn("documented_runtime_mismatch", issue_codes(result))

    def test_generated_tracked_artifacts_fail_but_untracked_state_does_not(self):
        forbidden = (
            "references/copied.md",
            "research.md",
            "task-governance-tool/state/current/taskgov.sqlite",
            "task-governance-tool/config/viewer.json",
            ".agents/skills/example/state/taskgov.sqlite-wal",
            "output/task-viewer.html",
            "cache/__pycache__/module.pyc",
            "scratch/run.log",
            "scratch/output.tmp",
        )
        for relative in forbidden:
            with self.subTest(relative=relative):
                self.assertTrue(forbidden_tracked_artifact(relative))
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = copy_release_fixture(Path(temporary))
                    result = check_fixture(
                        fixture,
                        inventory=(*BASE_TRACKED_PATHS, relative),
                    )
                    matching = [
                        issue
                        for issue in result.issues
                        if issue.code == "generated_artifact_tracked"
                        and issue.subject == relative
                    ]
                    self.assertEqual(len(matching), 1)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = copy_release_fixture(Path(temporary))
            state = (
                fixture
                / "task-governance-tool"
                / "state"
                / "current"
                / "taskgov.sqlite"
            )
            state.parent.mkdir(parents=True)
            state.write_bytes(b"local generated state")
            config = fixture / "task-governance-tool" / "config" / "viewer.json"
            config.parent.mkdir()
            config.write_text(
                '{"schema_version":1,"profile":"visibility-refresh-v1",'
                '"refresh_interval_seconds":30}',
                encoding="utf-8",
            )

            result = check_fixture(fixture)

            self.assertTrue(result.ok, result.issues)

    def test_ci_uses_one_checker_without_the_removed_duplicate_matrices(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(workflow.count(CHECKER_INVOCATION), 1)
        self.assertEqual(workflow.count(CI_CHECK_INVOCATION), 1)
        self.assertEqual(workflow.count(CI_MATRIX_INVOCATION), 1)
        self.assertEqual(workflow.count(CI_LANE_INVOCATION), 1)
        for removed in (
            "$requiredFiles",
            "$publicTokens",
            "Get-FileHash",
            "Guard generated artifacts",
        ):
            self.assertNotIn(removed, workflow)
        self.assertNotIn("python -m unittest discover -s tests", workflow)
        self.assertIn("Full release-candidate gate", workflow)
        self.assertIn(
            "matrix: ${{ fromJSON(needs.policy.outputs.matrix) }}",
            workflow,
        )
        self.assertIn("doctor --repo . --read-only --json", workflow)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = copy_release_fixture(Path(temporary))
            fixture_workflow = fixture / ".github" / "workflows" / "ci.yml"
            fixture_workflow.write_text(
                fixture_workflow.read_text(encoding="utf-8").replace(
                    f"run: {CHECKER_INVOCATION}",
                    f"# run: {CHECKER_INVOCATION}",
                    1,
                ),
                encoding="utf-8",
            )
            commented = check_fixture(fixture)
            self.assertIn("ci_checker_wiring_invalid", issue_codes(commented))

    def test_ci_policy_wiring_event_and_candidate_drift_fail(self):
        def relocate_candidate_condition(text: str) -> str:
            candidate = (
                "    if: ${{ always() && github.event_name == "
                "'workflow_dispatch' }}\n"
            )
            changed = text.replace(
                candidate,
                "    if: ${{ always() && github.event_name == 'push' }}\n",
                1,
            )
            return changed.replace(
                "  test:\n",
                f"  test:\n{candidate}",
                1,
            )

        mutations = (
            (
                "lane_commented",
                lambda text: text.replace(
                    f"run: {CI_LANE_INVOCATION}",
                    f"# run: {CI_LANE_INVOCATION}",
                    1,
                ),
                "ci_test_policy_wiring_invalid",
            ),
            (
                "matrix_commented",
                lambda text: text.replace(
                    f"          $matrix = {CI_MATRIX_INVOCATION}",
                    f"          # $matrix = {CI_MATRIX_INVOCATION}",
                    1,
                ),
                "ci_test_policy_wiring_invalid",
            ),
            (
                "matrix_overridden",
                lambda text: text.replace(
                    '          "matrix=$matrix" | Out-File',
                    "          $matrix = '{\"include\":[]}'\n"
                    '          "matrix=$matrix" | Out-File',
                    1,
                ),
                "ci_test_policy_wiring_invalid",
            ),
            (
                "test_job_restricted_to_dispatch",
                lambda text: text.replace(
                    "  test:\n",
                    "  test:\n"
                    "    if: github.event_name == 'workflow_dispatch'\n",
                    1,
                ),
                "ci_test_policy_wiring_invalid",
            ),
            (
                "test_job_continue_on_error",
                lambda text: text.replace(
                    "  test:\n",
                    "  test:\n    continue-on-error: true\n",
                    1,
                ),
                "ci_test_policy_wiring_invalid",
            ),
            (
                "permission_write",
                lambda text: text.replace(
                    "  contents: read",
                    "  contents: write",
                    1,
                ),
                "ci_test_policy_wiring_invalid",
            ),
            (
                "extra_job_with_write_permission",
                lambda text: text
                + "\n  unapproved:\n"
                "    runs-on: windows-latest\n"
                "    permissions:\n"
                "      contents: write\n"
                "    steps:\n"
                "      - name: Mutate repository\n"
                "        run: Write-Output mutation\n",
                "ci_test_policy_wiring_invalid",
            ),
            (
                "extra_policy_step",
                lambda text: text.replace(
                    "  test:\n",
                    "      - name: Unapproved policy action\n"
                    "        run: Write-Output mutation\n\n"
                    "  test:\n",
                    1,
                ),
                "ci_test_policy_wiring_invalid",
            ),
            (
                "push_branch",
                lambda text: text.replace("- main", "- release", 1),
                "ci_event_policy_invalid",
            ),
            (
                "extra_event",
                lambda text: text.replace(
                    "  workflow_dispatch:\n",
                    "  workflow_dispatch:\n  schedule:\n",
                    1,
                ),
                "ci_event_policy_invalid",
            ),
            (
                "candidate_event",
                lambda text: text.replace(
                    "github.event_name == 'workflow_dispatch'",
                    "github.event_name == 'push'",
                    1,
                ),
                "ci_candidate_gate_invalid",
            ),
            (
                "candidate_condition_relocated",
                relocate_candidate_condition,
                "ci_candidate_gate_invalid",
            ),
            (
                "matrix_source",
                lambda text: text.replace(
                    "matrix: ${{ fromJSON(needs.policy.outputs.matrix) }}",
                    "matrix: ${{ fromJSON('{}') }}",
                    1,
                ),
                "ci_test_policy_wiring_invalid",
            ),
        )
        for name, mutate, expected_code in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                fixture = copy_release_fixture(Path(temporary))
                fixture_workflow = fixture / ".github" / "workflows" / "ci.yml"
                original = fixture_workflow.read_text(encoding="utf-8")
                fixture_workflow.write_text(
                    mutate(original),
                    encoding="utf-8",
                )

                result = check_fixture(fixture)

                self.assertIn(expected_code, issue_codes(result), result.issues)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = copy_release_fixture(Path(temporary))
            (fixture / "tools" / "test_lanes.py").unlink()

            result = check_fixture(fixture)

            self.assertIn("required_file_missing", issue_codes(result))

    def test_uninjected_runtime_facts_fail_closed_for_another_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = copy_release_fixture(Path(temporary))

            result = check_release_contract(
                fixture,
                tracked_paths=BASE_TRACKED_PATHS,
            )

            self.assertIn("runtime_owner_mismatch", issue_codes(result))


def _replace_manifest_origin(fixture: Path, origin: str) -> None:
    manifest_path = fixture / "task-governance-tool" / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_origin"] = origin
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
