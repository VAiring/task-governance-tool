from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool import verification_runner_plan as plan_module
    from task_governance_tool.verification_runner_plan import (
        PLAN_ARG_LIMIT,
        PLAN_BLOB_UTF8_BYTE_LIMIT,
        PLAN_ENTRY_DOMAIN,
        PLAN_ENTRY_LIMIT,
        PLAN_ERROR_MESSAGE,
        PLAN_SEMANTIC_DOMAIN,
        PLAN_SOURCE_ERROR_MESSAGE,
        PLAN_STEP_LIMIT,
        VerificationRunnerPlanError,
        VerificationRunnerPlanSource,
        capture_verification_runner_plan,
        resolve_verification_runner_plan,
    )
finally:
    sys.path.pop(0)


TASK_ID = "tg_task_0123456789abcdef"
OTHER_TASK_ID = "tg_task_fedcba9876543210"
CONTRACT_REVISION = 7
EXPECTATION_DIGEST = "a" * 64
CRITERION_DIGEST = "sha256:" + "b" * 64


def canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def domain_digest(domain, value):
    return "sha256:" + hashlib.sha256(domain + canonical_json_bytes(value)).hexdigest()


def step_payload(**updates):
    value = {
        "step_id": "focused",
        "mode": "script",
        "entrypoint": "tests/focused_check.py",
        "argv": ["--literal", "value"],
        "cwd": ".",
        "timeout_seconds": 30,
        "cpu_seconds": 30,
        "memory_mib": 128,
        "process_limit": 2,
        "output_byte_limit": 1_048_576,
    }
    value.update(updates)
    return value


def entry_payload(**updates):
    value = {
        "task_id": TASK_ID,
        "contract_revision": CONTRACT_REVISION,
        "verification_expectation_digest": EXPECTATION_DIGEST,
        "verification_criterion_digest": CRITERION_DIGEST,
        "coverage": "full",
        "steps": [step_payload()],
    }
    value.update(updates)
    return value


def plan_payload(*, trusted_local=True, entries=None, **updates):
    value = {
        "version": 1,
        "plan_id": "project-plan",
        "trusted_local": trusted_local,
        "entries": [entry_payload()] if entries is None else entries,
    }
    value.update(updates)
    return value


def source_from_raw(raw):
    return VerificationRunnerPlanSource(
        raw_blob=raw,
        raw_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
    )


def source_from_value(value):
    return source_from_raw(canonical_json_bytes(value))


def resolve(source, **updates):
    basis = {
        "task_id": TASK_ID,
        "contract_revision": CONTRACT_REVISION,
        "verification_expectation_digest": EXPECTATION_DIGEST,
        "verification_criterion_digest": CRITERION_DIGEST,
    }
    basis.update(updates)
    return resolve_verification_runner_plan(source, **basis)


def canonical_step(value, ordinal):
    return {
        "argv": value["argv"],
        "cpu_seconds": value["cpu_seconds"],
        "cwd": value["cwd"],
        "entrypoint": value["entrypoint"],
        "memory_mib": value["memory_mib"],
        "mode": value["mode"],
        "ordinal": ordinal,
        "output_byte_limit": value["output_byte_limit"],
        "path_lookup": False,
        "process_limit": value["process_limit"],
        "shell": False,
        "step_id": value["step_id"],
        "timeout_seconds": value["timeout_seconds"],
    }


def canonical_entry(value):
    return {
        "contract_revision": value["contract_revision"],
        "coverage": value["coverage"],
        "steps": [
            canonical_step(step, ordinal)
            for ordinal, step in enumerate(value["steps"], start=1)
        ],
        "task_id": value["task_id"],
        "verification_criterion_digest": value["verification_criterion_digest"],
        "verification_expectation_digest": value[
            "verification_expectation_digest"
        ],
    }


def git(repo, *arguments, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


class VerificationRunnerPlanTestCase(unittest.TestCase):
    def assert_plan_error(self, callable_, code=None, source=False):
        with self.assertRaises(VerificationRunnerPlanError) as raised:
            callable_()
        if code is not None:
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(
            str(raised.exception),
            PLAN_SOURCE_ERROR_MESSAGE if source else PLAN_ERROR_MESSAGE,
        )
        return raised.exception

    def make_repo(self, temporary, *, ignored=True):
        repo = Path(temporary) / "repo"
        package = repo / "package"
        package.mkdir(parents=True)
        git(repo, "init", "--quiet")
        if ignored:
            (repo / ".gitignore").write_text(
                "/package/config/\n", encoding="utf-8"
            )
        return repo.resolve(), package.resolve()

    def write_physical_plan(self, package, raw):
        config = package / "config"
        config.mkdir(parents=True, exist_ok=True)
        plan = config / "verification-runner.json"
        plan.write_bytes(raw)
        return plan


class PhysicalPlanCaptureTests(VerificationRunnerPlanTestCase):
    def test_absent_config_or_file_is_the_only_capture_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo, package = self.make_repo(temporary)
            self.assertIsNone(capture_verification_runner_plan(repo, package))

            (package / "config").mkdir()
            self.assertIsNone(capture_verification_runner_plan(repo, package))

            (package / "config" / "verification-runner.json").mkdir()
            self.assert_plan_error(
                lambda: capture_verification_runner_plan(repo, package),
                "plan_source_invalid",
                source=True,
            )

    def test_capture_requires_index_unregistered_and_effectively_ignored(self):
        raw = canonical_json_bytes(plan_payload())
        with tempfile.TemporaryDirectory() as temporary:
            repo, package = self.make_repo(temporary)
            plan = self.write_physical_plan(package, raw)
            operand = plan.relative_to(repo).as_posix()

            tracked = git(
                repo, "ls-files", "--cached", "--stage", "-z", "--", operand
            )
            ignored = git(repo, "check-ignore", "--quiet", "--no-index", "--", operand)
            self.assertEqual(tracked.stdout, b"")
            self.assertEqual(ignored.returncode, 0)

            captured = capture_verification_runner_plan(repo, package)
            self.assertEqual(captured.raw_blob, raw)
            self.assertEqual(
                captured.raw_digest,
                "sha256:" + hashlib.sha256(raw).hexdigest(),
            )

            git(repo, "add", "-f", "--", operand)
            self.assert_plan_error(
                lambda: capture_verification_runner_plan(repo, package),
                "plan_source_invalid",
                source=True,
            )

        with tempfile.TemporaryDirectory() as temporary:
            repo, package = self.make_repo(temporary, ignored=False)
            self.write_physical_plan(package, raw)
            self.assert_plan_error(
                lambda: capture_verification_runner_plan(repo, package),
                "plan_source_invalid",
                source=True,
            )

    def test_capture_rejects_hardlink_and_case_variant_index_aliases(self):
        raw = canonical_json_bytes(plan_payload())
        with tempfile.TemporaryDirectory() as temporary:
            repo, package = self.make_repo(temporary)
            tracked = repo / "tracked-plan.json"
            tracked.write_bytes(raw)
            git(repo, "add", "tracked-plan.json")
            plan = package / "config" / "verification-runner.json"
            plan.parent.mkdir()
            plan.hardlink_to(tracked)
            self.assertGreater(plan.stat().st_nlink, 1)
            self.assert_plan_error(
                lambda: capture_verification_runner_plan(repo, package),
                "plan_source_invalid",
                source=True,
            )

        with tempfile.TemporaryDirectory() as temporary:
            repo, package = self.make_repo(temporary)
            plan = self.write_physical_plan(package, raw)
            blob_source = repo / "blob-source.json"
            blob_source.write_bytes(raw)
            object_id = git(repo, "hash-object", "-w", "blob-source.json").stdout
            alias = "Package/Config/Verification-Runner.json"
            git(
                repo,
                "update-index",
                "--add",
                "--cacheinfo",
                "100644",
                object_id.decode("ascii").strip(),
                alias,
            )
            operand = plan.relative_to(repo).as_posix()
            self.assertEqual(
                git(
                    repo,
                    "ls-files",
                    "--cached",
                    "--stage",
                    "-z",
                    "--",
                    operand,
                ).stdout,
                b"",
            )
            self.assertIn(
                alias.encode("utf-8"),
                git(
                    repo,
                    "ls-files",
                    "--cached",
                    "--stage",
                    "-z",
                    "--",
                    f":(icase,literal){operand}",
                ).stdout,
            )
            self.assert_plan_error(
                lambda: capture_verification_runner_plan(repo, package),
                "plan_source_invalid",
                source=True,
            )

    def test_capture_is_bounded_and_rechecks_identity_and_local_only_status(self):
        raw = canonical_json_bytes(plan_payload())
        padded = raw + b" " * (PLAN_BLOB_UTF8_BYTE_LIMIT - len(raw))
        with tempfile.TemporaryDirectory() as temporary:
            repo, package = self.make_repo(temporary)
            plan = self.write_physical_plan(package, padded)
            with mock.patch.object(
                plan_module, "_plan_is_local_only", return_value=True
            ):
                self.assertEqual(
                    capture_verification_runner_plan(repo, package).raw_blob,
                    padded,
                )

                plan.write_bytes(padded + b" ")
                self.assert_plan_error(
                    lambda: capture_verification_runner_plan(repo, package),
                    "plan_source_invalid",
                    source=True,
                )

            plan.write_bytes(raw)
            with mock.patch.object(
                plan_module, "_plan_is_local_only", return_value=True
            ):
                with mock.patch.object(
                    plan_module, "_same_file_identity", return_value=False
                ):
                    self.assert_plan_error(
                        lambda: capture_verification_runner_plan(repo, package),
                        "plan_source_invalid",
                        source=True,
                    )

            with mock.patch.object(
                plan_module, "_plan_is_local_only", side_effect=[True, False]
            ):
                self.assert_plan_error(
                    lambda: capture_verification_runner_plan(repo, package),
                    "plan_source_invalid",
                    source=True,
                )

    def test_capture_rejects_relative_or_out_of_repo_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo, package = self.make_repo(temporary)
            self.assert_plan_error(
                lambda: capture_verification_runner_plan(Path("repo"), package),
                "plan_source_invalid",
                source=True,
            )
            outside = Path(temporary).resolve() / "outside"
            outside.mkdir()
            self.assert_plan_error(
                lambda: capture_verification_runner_plan(repo, outside),
                "plan_source_invalid",
                source=True,
            )

    def test_selected_git_target_plan_never_overrides_physical_local_plan(self):
        historical = canonical_json_bytes(plan_payload(trusted_local=False))
        physical_value = plan_payload()
        physical_value["entries"][0]["steps"][0]["argv"] = ["physical-only"]
        physical = canonical_json_bytes(physical_value)

        with tempfile.TemporaryDirectory() as temporary:
            repo, package = self.make_repo(temporary, ignored=False)
            plan = self.write_physical_plan(package, historical)
            operand = plan.relative_to(repo).as_posix()
            git(repo, "add", "--", operand)
            git(
                repo,
                "-c",
                "user.name=Plan Test",
                "-c",
                "user.email=plan-test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "historical target plan",
            )
            target_revision = git(repo, "rev-parse", "HEAD").stdout.strip().decode()
            git(repo, "rm", "--cached", "--quiet", "--", operand)
            (repo / ".gitignore").write_text(
                "/package/config/\n", encoding="utf-8"
            )
            plan.write_bytes(physical)

            self.assertEqual(
                git(repo, "show", f"{target_revision}:{operand}").stdout,
                historical,
            )
            with mock.patch.object(
                plan_module, "_plan_is_local_only", return_value=True
            ):
                captured = capture_verification_runner_plan(repo, package)
            self.assertEqual(captured.raw_blob, physical)
            selected = resolve(captured)
            self.assertEqual(selected.steps[0].argv, ("physical-only",))
            self.assertIsNone(selected.plan_blob_object_id)

            parameters = tuple(inspect.signature(capture_verification_runner_plan).parameters)
            self.assertEqual(parameters, ("repo", "physical_package_root"))


class PlanResolutionTests(VerificationRunnerPlanTestCase):
    def test_exact_fallback_matrix(self):
        absent = resolve(None)
        self.assertEqual(
            (
                absent.plan_state,
                absent.route,
                absent.reason,
                absent.plan_blob_object_id,
                absent.plan_raw_digest,
                absent.plan_id,
                absent.plan_version,
                absent.plan_semantic_digest,
                absent.selected_entry_digest,
                absent.coverage,
                absent.steps,
            ),
            (
                "absent",
                "m21_fallback",
                "plan_absent",
                None,
                None,
                None,
                None,
                None,
                None,
                "not_applicable",
                (),
            ),
        )

        disabled_source = source_from_value(plan_payload(trusted_local=False))
        disabled = resolve(disabled_source)
        self.assertEqual(
            (disabled.plan_state, disabled.route, disabled.reason),
            ("disabled", "m21_fallback", "trusted_local_disabled"),
        )
        self.assertEqual(disabled.plan_raw_digest, disabled_source.raw_digest)
        self.assertEqual(disabled.plan_id, "project-plan")
        self.assertEqual(disabled.plan_version, 1)
        self.assertIsNotNone(disabled.plan_semantic_digest)
        self.assertIsNone(disabled.selected_entry_digest)
        self.assertEqual((disabled.coverage, disabled.steps), ("not_applicable", ()))

        other = entry_payload(task_id=OTHER_TASK_ID)
        no_match_source = source_from_value(plan_payload(entries=[other]))
        no_match = resolve(no_match_source)
        self.assertEqual(
            (no_match.plan_state, no_match.route, no_match.reason),
            ("no_match", "m21_fallback", "plan_entry_absent"),
        )
        self.assertEqual(no_match.plan_raw_digest, no_match_source.raw_digest)
        self.assertEqual(no_match.plan_id, "project-plan")
        self.assertEqual(no_match.plan_version, 1)
        self.assertIsNotNone(no_match.plan_semantic_digest)
        self.assertIsNone(no_match.selected_entry_digest)
        self.assertEqual((no_match.coverage, no_match.steps), ("not_applicable", ()))

    def test_exact_basis_selects_only_closed_literal_steps(self):
        script = step_payload()
        module = step_payload(
            step_id="module",
            mode="module",
            entrypoint="tests.focused_check",
            argv=["--second"],
        )
        value = plan_payload(entries=[entry_payload(steps=[script, module])])
        source = source_from_value(value)
        selected = resolve(source)

        self.assertEqual(
            (selected.plan_state, selected.route, selected.reason),
            ("runner", "runner", None),
        )
        self.assertEqual(selected.plan_blob_object_id, None)
        self.assertEqual(selected.plan_raw_digest, source.raw_digest)
        self.assertEqual(selected.plan_id, "project-plan")
        self.assertEqual(selected.plan_version, 1)
        self.assertEqual(selected.coverage, "full")
        self.assertEqual(selected.step_count, 2)
        self.assertEqual([step.ordinal for step in selected.steps], [1, 2])
        self.assertTrue(all(step.shell is False for step in selected.steps))
        self.assertTrue(all(step.path_lookup is False for step in selected.steps))
        self.assertEqual(selected.steps[0].argv, ("--literal", "value"))

    def test_current_task_stale_partial_or_multiple_entries_fail_closed(self):
        mismatches = (
            {"contract_revision": CONTRACT_REVISION + 1},
            {"verification_expectation_digest": "c" * 64},
            {"verification_criterion_digest": "sha256:" + "d" * 64},
        )
        for update in mismatches:
            with self.subTest(update=update):
                stale = entry_payload(**update)
                self.assert_plan_error(
                    lambda stale=stale: resolve(
                        source_from_value(plan_payload(entries=[stale]))
                    ),
                    "plan_basis_mismatch",
                )

        exact = entry_payload()
        stale = entry_payload(contract_revision=CONTRACT_REVISION + 1)
        self.assert_plan_error(
            lambda: resolve(source_from_value(plan_payload(entries=[exact, stale]))),
            "plan_ambiguous",
        )

        duplicate = deepcopy(exact)
        duplicate["steps"][0]["argv"] = ["different"]
        self.assert_plan_error(
            lambda: resolve(source_from_value(plan_payload(entries=[exact, duplicate]))),
            "plan_ambiguous",
        )

    def test_raw_semantic_and_selected_entry_digests_have_exact_domains(self):
        value = plan_payload()
        raw_one = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        reordered = {
            "entries": value["entries"],
            "trusted_local": value["trusted_local"],
            "plan_id": value["plan_id"],
            "version": value["version"],
        }
        raw_two = json.dumps(
            reordered, ensure_ascii=False, separators=(",", ":")
        ).encode()
        first = resolve(source_from_raw(raw_one))
        second = resolve(source_from_raw(raw_two))

        normalized_entry = canonical_entry(value["entries"][0])
        normalized_plan = {
            "entries": [normalized_entry],
            "plan_id": "project-plan",
            "trusted_local": True,
            "version": 1,
        }
        self.assertNotEqual(first.plan_raw_digest, second.plan_raw_digest)
        self.assertEqual(
            first.plan_semantic_digest,
            domain_digest(PLAN_SEMANTIC_DOMAIN, normalized_plan),
        )
        self.assertEqual(first.plan_semantic_digest, second.plan_semantic_digest)
        self.assertEqual(
            first.selected_entry_digest,
            domain_digest(PLAN_ENTRY_DOMAIN, normalized_entry),
        )
        self.assertEqual(first.selected_entry_digest, second.selected_entry_digest)

        changed = deepcopy(value)
        changed["entries"][0]["steps"][0]["argv"] = ["changed"]
        changed_resolution = resolve(source_from_value(changed))
        self.assertNotEqual(
            changed_resolution.plan_semantic_digest, first.plan_semantic_digest
        )
        self.assertNotEqual(
            changed_resolution.selected_entry_digest, first.selected_entry_digest
        )


class StrictPlanValidationTests(VerificationRunnerPlanTestCase):
    def assert_invalid_value(self, value, code="plan_invalid"):
        self.assert_plan_error(lambda: resolve(source_from_value(value)), code)

    def test_source_digest_and_blob_bound_are_strict(self):
        raw = canonical_json_bytes(plan_payload())
        self.assert_plan_error(
            lambda: VerificationRunnerPlanSource(raw_blob=raw, raw_digest="sha256:" + "0" * 64),
            "plan_source_invalid",
            source=True,
        )
        self.assert_plan_error(
            lambda: VerificationRunnerPlanSource(
                raw_blob=b" " * (PLAN_BLOB_UTF8_BYTE_LIMIT + 1),
                raw_digest="sha256:" + "0" * 64,
            ),
            "plan_source_invalid",
            source=True,
        )
        self.assert_plan_error(
            lambda: resolve(source_from_raw(b"\xff")), "plan_invalid"
        )

    def test_json_is_strict_for_duplicates_numbers_shape_and_members(self):
        duplicate = (
            b'{"version":1,"version":1,"plan_id":"project-plan",'
            b'"trusted_local":true,"entries":[]}'
        )
        invalid_raw = (
            duplicate,
            b"{",
            b"[]",
            b'{"version":1.0,"plan_id":"project-plan","trusted_local":true,"entries":[]}',
            b'{"version":NaN,"plan_id":"project-plan","trusted_local":true,"entries":[]}',
        )
        for raw in invalid_raw:
            with self.subTest(raw=raw[:32]):
                self.assert_plan_error(
                    lambda raw=raw: resolve(source_from_raw(raw)), "plan_invalid"
                )

        mutations = []
        extra_top = plan_payload()
        extra_top["target_revision"] = "untrusted"
        mutations.append(extra_top)
        extra_entry = plan_payload()
        extra_entry["entries"][0]["criterion_id"] = "untrusted"
        mutations.append(extra_entry)
        for forbidden in ("shell", "path_lookup", "executable", "environment", "command"):
            value = plan_payload()
            value["entries"][0]["steps"][0][forbidden] = False
            mutations.append(value)
        wrong_types = (plan_payload(version=True), plan_payload(trusted_local=1))
        for value in (*mutations, *wrong_types):
            with self.subTest(value=value):
                self.assert_invalid_value(value)

        wrong_entries_type = plan_payload(entries={})
        self.assert_plan_error(
            lambda: resolve(source_from_value(wrong_entries_type))
        )

    def test_plan_entry_step_and_argument_cardinality_bounds(self):
        too_many_entries = [
            entry_payload(task_id=f"tg_task_{number:016x}")
            for number in range(PLAN_ENTRY_LIMIT + 1)
        ]
        self.assert_invalid_value(
            plan_payload(entries=too_many_entries), "plan_too_large"
        )

        no_steps = plan_payload(entries=[entry_payload(steps=[])])
        self.assert_invalid_value(no_steps)
        too_many_steps = [
            step_payload(step_id=f"step-{number}")
            for number in range(PLAN_STEP_LIMIT + 1)
        ]
        self.assert_invalid_value(
            plan_payload(entries=[entry_payload(steps=too_many_steps)])
        )
        duplicate_steps = [step_payload(), step_payload()]
        self.assert_invalid_value(
            plan_payload(entries=[entry_payload(steps=duplicate_steps)])
        )
        over_total_timeout = [
            step_payload(step_id="one", timeout_seconds=900),
            step_payload(step_id="two", timeout_seconds=900),
            step_payload(step_id="three", timeout_seconds=1),
        ]
        self.assert_invalid_value(
            plan_payload(entries=[entry_payload(steps=over_total_timeout)])
        )

        too_many_args = plan_payload()
        too_many_args["entries"][0]["steps"][0]["argv"] = [
            "value"
        ] * (PLAN_ARG_LIMIT + 1)
        self.assert_invalid_value(too_many_args)

    def test_literal_and_closed_step_fields_are_strictly_bounded(self):
        invalid_step_updates = (
            {"mode": "executable"},
            {"entrypoint": "/absolute.py"},
            {"entrypoint": "tests/check.txt"},
            {"cwd": "../escape"},
            {"timeout_seconds": 0},
            {"timeout_seconds": 901},
            {"cpu_seconds": True},
            {"memory_mib": 63},
            {"memory_mib": 2_049},
            {"process_limit": 0},
            {"process_limit": 33},
            {"output_byte_limit": 1_048_575},
            {"argv": ["a" * 4_097]},
            {"argv": ["line\nbreak"]},
        )
        for update in invalid_step_updates:
            with self.subTest(update=update):
                value = plan_payload(
                    entries=[entry_payload(steps=[step_payload(**update)])]
                )
                self.assert_invalid_value(value)

        size_boundary = plan_payload()
        size_boundary["entries"][0]["steps"][0]["argv"] = ["a" * 4_096]
        selected = resolve(source_from_value(size_boundary))
        self.assertEqual(len(selected.steps[0].argv[0]), 4_096)

        count_boundary = plan_payload()
        count_boundary["entries"][0]["steps"][0]["argv"] = [
            "value"
        ] * PLAN_ARG_LIMIT
        selected = resolve(source_from_value(count_boundary))
        self.assertEqual(len(selected.steps[0].argv), PLAN_ARG_LIMIT)

    def test_identity_coverage_and_digest_grammars_are_closed(self):
        invalid_entries = (
            entry_payload(task_id="tg_task_not-a-digest"),
            entry_payload(contract_revision=0),
            entry_payload(contract_revision=True),
            entry_payload(verification_expectation_digest="sha256:" + "a" * 64),
            entry_payload(verification_criterion_digest="b" * 64),
            entry_payload(coverage="partial"),
        )
        for entry in invalid_entries:
            with self.subTest(entry=entry):
                self.assert_invalid_value(plan_payload(entries=[entry]))

        invalid_plans = (
            plan_payload(plan_id="UPPER"),
            plan_payload(plan_id="x" * 65),
            plan_payload(version=2),
        )
        for value in invalid_plans:
            with self.subTest(value=value):
                self.assert_invalid_value(value)

        invalid_bases = (
            {"task_id": OTHER_TASK_ID.upper()},
            {"contract_revision": 0},
            {"verification_expectation_digest": "sha256:" + "a" * 64},
            {"verification_criterion_digest": "b" * 64},
        )
        source = source_from_value(plan_payload())
        for update in invalid_bases:
            with self.subTest(update=update):
                self.assert_plan_error(lambda update=update: resolve(source, **update))

    def test_errors_are_sanitized_and_do_not_echo_plan_content(self):
        secret = "TOP_SECRET_PLAN_LITERAL"
        raw = (
            '{"version":1,"plan_id":"project-plan","trusted_local":true,'
            f'"entries":[],"{secret}":true}}'
        ).encode()
        error = self.assert_plan_error(lambda: resolve(source_from_raw(raw)))
        self.assertNotIn(secret, error.code)
        self.assertNotIn(secret, str(error))


if __name__ == "__main__":
    unittest.main()
