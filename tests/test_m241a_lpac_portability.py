from __future__ import annotations

import ctypes
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _verification_runner_lpac_win32 as lpac  # noqa: E402
from tests import m241a_lpac_native_fixture as native_fixture  # noqa: E402

run_native_probe = native_fixture.run_native_probe


_PACKAGE_SID = "S-1-15-2-100-101-102-103-104-105-106-107"


class RunnerLpacPortabilityPureTests(unittest.TestCase):
    def _class46_api(
        self, *, success: bool, error: int = 0, value: int = 1, returned: int = 4
    ) -> tuple[SimpleNamespace, list[tuple[int, int]], dict[str, int]]:
        calls: list[tuple[int, int]] = []
        state = {"error": 0}

        def query(_handle, information_class, buffer, size, returned_pointer):
            calls.append((int(information_class), int(size)))
            state["error"] = error
            ctypes.cast(
                returned_pointer, ctypes.POINTER(lpac.DWORD)
            ).contents.value = returned
            if buffer:
                ctypes.cast(buffer, ctypes.POINTER(lpac.DWORD)).contents.value = value
            return int(success)

        return (
            SimpleNamespace(advapi32=SimpleNamespace(GetTokenInformation=query)),
            calls,
            state,
        )

    def test_class46_selector_uses_one_fixed_dword_and_exact_error_87(self):
        token = lpac.OwnedHandle(41)
        api, calls, state = self._class46_api(success=True)
        with patch.object(lpac, "_apis", return_value=api), patch.object(
            lpac, "_set_last_error", side_effect=lambda value: state.update(error=value)
        ), patch.object(lpac, "_get_last_error", side_effect=lambda: state["error"]):
            self.assertEqual(
                lpac._query_lpac_class46_dword(token), lpac.LPAC_PROOF_CLASS_46
            )
        self.assertEqual(
            calls,
            [(lpac.TOKEN_IS_LESS_PRIVILEGED_APP_CONTAINER, ctypes.sizeof(lpac.DWORD))],
        )

        api, _calls, state = self._class46_api(success=False, error=87, returned=0)
        with patch.object(lpac, "_apis", return_value=api), patch.object(
            lpac, "_set_last_error", side_effect=lambda value: state.update(error=value)
        ), patch.object(lpac, "_get_last_error", side_effect=lambda: state["error"]):
            self.assertEqual(
                lpac._query_lpac_class46_dword(token),
                lpac.LPAC_PROOF_ACCESS_CHECK,
            )

        for error in (0, 2, 5, 122):
            api, _calls, state = self._class46_api(
                success=False, error=error, returned=0
            )
            with self.subTest(error=error), patch.object(
                lpac, "_apis", return_value=api
            ), patch.object(
                lpac,
                "_set_last_error",
                side_effect=lambda value, state=state: state.update(error=value),
            ), patch.object(
                lpac, "_get_last_error", side_effect=lambda state=state: state["error"]
            ), self.assertRaises(lpac.LpacProofError) as raised:
                lpac._query_lpac_class46_dword(token)
            self.assertEqual(raised.exception.code, "sandbox_boundary_violation")

    def test_class46_success_rejects_every_malformed_value_or_length(self):
        token = lpac.OwnedHandle(42)
        for value, returned in ((0, 4), (2, 4), (1, 0), (1, 8)):
            api, _calls, state = self._class46_api(
                success=True, value=value, returned=returned
            )
            with self.subTest(value=value, returned=returned), patch.object(
                lpac, "_apis", return_value=api
            ), patch.object(
                lpac,
                "_set_last_error",
                side_effect=lambda new, state=state: state.update(error=new),
            ), patch.object(
                lpac, "_get_last_error", side_effect=lambda state=state: state["error"]
            ), self.assertRaises(lpac.LpacProofError):
                lpac._query_lpac_class46_dword(token)

    def test_accesscheck_result_requires_zero_privilege_and_exact_mask(self):
        size = ctypes.sizeof(lpac._PRIVILEGE_SET_ONE)
        allowed = lpac._AccessCheckResult(size, 0, 0, 0, 0, 0, 1, 1)
        denied = replace(allowed, granted_access=0, access_status=0)
        self.assertTrue(
            lpac._validate_access_check_result(allowed, desired=lpac.FILE_READ_DATA)
        )
        self.assertFalse(
            lpac._validate_access_check_result(denied, desired=lpac.FILE_READ_DATA)
        )
        malformed = (
            replace(allowed, privilege_length=size + 1),
            replace(allowed, privilege_count=1),
            replace(allowed, privilege_control=1),
            replace(allowed, privilege_luid_low=1),
            replace(allowed, privilege_luid_high=1),
            replace(allowed, privilege_attributes=1),
            replace(allowed, granted_access=3),
            replace(allowed, access_status=2),
            replace(denied, granted_access=1),
        )
        for result in malformed:
            with self.subTest(result=result), self.assertRaises(lpac.LpacProofError):
                lpac._validate_access_check_result(
                    result, desired=lpac.FILE_READ_DATA
                )

    def test_descriptor_validator_is_exact_system_protected_two_ace_shape(self):
        sid_lengths = (12, 16)
        ace_sizes = tuple(8 + value for value in sid_lengths)
        acl_size = ctypes.sizeof(lpac._ACL) + sum(ace_sizes)
        state = lpac._AppContainerGrantDescriptorState(
            1,
            lpac.SE_DACL_PRESENT | lpac.SE_DACL_PROTECTED,
            lpac.SYSTEM_SID,
            False,
            lpac.SYSTEM_SID,
            False,
            True,
            False,
            True,
            2,
            acl_size,
            2,
            2,
            acl_size,
            0,
            (0, 0),
            (0, 0),
            ace_sizes,
            sid_lengths,
            (1, 1),
            ("S-1-5-21-1", _PACKAGE_SID),
        )
        lpac._validate_appcontainer_grant_descriptor(
            state, "S-1-5-21-1", _PACKAGE_SID
        )
        for changed in (
            replace(state, owner_sid="S-1-5-32-545"),
            replace(state, group_sid="S-1-5-32-545"),
            replace(state, control=lpac.SE_DACL_PRESENT),
            replace(state, ace_count=3),
            replace(state, ace_masks=(1, 2)),
            replace(state, trustees=(_PACKAGE_SID, "S-1-5-21-1")),
            replace(state, acl_bytes_free=4),
        ):
            with self.subTest(changed=changed), self.assertRaises(
                lpac.LpacProofError
            ):
                lpac._validate_appcontainer_grant_descriptor(
                    changed, "S-1-5-21-1", _PACKAGE_SID
                )

    def test_normal_and_lpac_creation_shapes_differ_only_by_policy(self):
        class Capture:
            def __init__(self):
                self.attribute_ids = []
                self.policies = []

            def add(self, attribute, value, _size):
                self.attribute_ids.append(attribute)
                if (
                    attribute
                    == lpac.PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY
                ):
                    self.policies.append(int(value.value))

        capabilities = lpac._SECURITY_CAPABILITIES(None, None, 0, 0)
        jobs = (lpac.HANDLE * 1)(lpac.HANDLE(31))
        inherited = (lpac.HANDLE * 3)(
            lpac.HANDLE(32), lpac.HANDLE(33), lpac.HANDLE(34)
        )
        for policy in (0, lpac.PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT):
            capture = Capture()
            lpac._add_creation_attributes(
                capture, capabilities, lpac.DWORD(policy), jobs, inherited
            )
            self.assertEqual(
                tuple(capture.attribute_ids), lpac.APPCONTAINER_CREATION_ATTRIBUTES
            )
            self.assertEqual(capture.policies, [policy])

    def test_accesscheck_semantics_requires_allow_deny_allow_and_distinct_jobs(self):
        normal_creation = lpac.CreationAttributeProof(
            lpac.APPCONTAINER_CREATION_ATTRIBUTES,
            0,
            101,
            (102, 103, 104),
            lpac.EXACT_CREATION_FLAGS,
            "sha256:" + "1" * 64,
        )
        lpac_creation = replace(
            normal_creation,
            app_policy_dword=1,
            job_handle=201,
            inherited_handles=(202, 203, 204),
        )
        normal = lpac.SuspendedNormalControl(
            lpac.OwnedHandle(11),
            lpac.OwnedHandle(12),
            lpac.OwnedHandle(13),
            1,
            normal_creation,
        )
        child = lpac.SuspendedLpacChild(
            lpac.OwnedHandle(21),
            lpac.OwnedHandle(22),
            lpac.OwnedHandle(23),
            2,
            lpac_creation,
        )
        duplicates = [lpac.OwnedHandle(31), lpac.OwnedHandle(32)]
        with patch.object(
            lpac,
            "_prove_token_identity",
            return_value=lpac.TokenIdentityProof(True, _PACKAGE_SID, 0),
        ), patch.object(
            lpac, "duplicate_impersonation_token", side_effect=duplicates
        ), patch.object(
            lpac, "_appcontainer_grant_allowed", side_effect=(True, False, True)
        ), patch.object(lpac, "_close_duplicate"):
            proof = lpac.prove_access_check_semantics(normal, child, _PACKAGE_SID)
        self.assertEqual(
            (
                proof.normal_aap_allowed,
                proof.lpac_aap_allowed,
                proof.lpac_package_allowed,
            ),
            (True, False, True),
        )
        self.assertEqual(proof.descriptor_owner_sid, lpac.SYSTEM_SID)
        self.assertEqual(proof.descriptor_group_sid, lpac.SYSTEM_SID)
        self.assertEqual(proof.descriptor_ace_count, 2)

        for values in ((False, False, True), (True, True, True), (True, False, False)):
            duplicates = [lpac.OwnedHandle(41), lpac.OwnedHandle(42)]
            with self.subTest(values=values), patch.object(
                lpac,
                "_prove_token_identity",
                return_value=lpac.TokenIdentityProof(True, _PACKAGE_SID, 0),
            ), patch.object(
                lpac, "duplicate_impersonation_token", side_effect=duplicates
            ), patch.object(
                lpac, "_appcontainer_grant_allowed", side_effect=values
            ), patch.object(lpac, "_close_duplicate"), self.assertRaises(
                lpac.LpacProofError
            ):
                lpac.prove_access_check_semantics(normal, child, _PACKAGE_SID)

    def test_both_suspended_process_types_expose_no_resume_operation(self):
        self.assertFalse(hasattr(lpac.SuspendedLpacChild, "resume_once"))
        self.assertFalse(hasattr(lpac.SuspendedNormalControl, "resume_once"))
        self.assertFalse(hasattr(lpac.SuspendedLpacChild, "resume"))
        self.assertFalse(hasattr(lpac.SuspendedNormalControl, "resume"))

    @unittest.skipUnless(os.name == "nt", "Windows environment fixture")
    def test_fixture_environment_is_closed_and_contains_no_ambient_variables(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="taskgov-m241a-env-") as scratch:
            block = native_fixture._environment_block(str(Path(scratch).resolve()))
        entries = tuple(item for item in block[:-2].split("\0") if item)
        keys = {item.split("=", 1)[0].upper() for item in entries}
        self.assertEqual(
            keys,
            {
                "APPDATA",
                "HOME",
                "LOCALAPPDATA",
                "PYTHONDONTWRITEBYTECODE",
                "PYTHONNOUSERSITE",
                "PYTHONUTF8",
                "SYSTEMROOT",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "WINDIR",
            },
        )
        self.assertTrue(
            keys.isdisjoint(
                {
                    "PATH",
                    "PYTHONPATH",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "CI",
                    "CODEX_HOME",
                    "GITHUB_TOKEN",
                    "AWS_ACCESS_KEY_ID",
                    "AZURE_CLIENT_SECRET",
                    "COOKIE",
                    "TOKEN",
                }
            )
        )


@unittest.skipUnless(os.name == "nt", "mandatory native Windows LPAC matrix")
class RunnerLpacPortabilityNativeTests(unittest.TestCase):
    def assert_clean(self, result):
        self.assertTrue(result.all_handles_closed)
        self.assertTrue(result.profile_absent)
        self.assertEqual(result.resumes, ())
        self.assertTrue(result.jobs)
        for job in result.jobs:
            self.assertEqual(job.total_processes, 1)
            self.assertEqual(job.active_processes, 0)

    def assert_access_route(self, result):
        self.assertEqual(result.route, lpac.LPAC_PROOF_ACCESS_CHECK)
        self.assertEqual(result.controls, 1)
        self.assertEqual(len(result.creations), 2)
        lpac_creation, normal_creation = result.creations
        self.assertEqual(
            lpac_creation.attribute_ids, lpac.APPCONTAINER_CREATION_ATTRIBUTES
        )
        self.assertEqual(
            normal_creation.attribute_ids, lpac.APPCONTAINER_CREATION_ATTRIBUTES
        )
        self.assertEqual(lpac_creation.app_policy_dword, 1)
        self.assertEqual(normal_creation.app_policy_dword, 0)
        self.assertEqual(
            lpac_creation.launch_basis_digest,
            normal_creation.launch_basis_digest,
        )
        self.assertNotEqual(lpac_creation.job_handle, normal_creation.job_handle)
        self.assertEqual(len(set(lpac_creation.inherited_handles)), 3)
        self.assertEqual(len(set(normal_creation.inherited_handles)), 3)
        self.assertEqual(
            result.grants[:2],
            (
                (lpac.ALL_APPLICATION_PACKAGES_SID, True),
                (lpac.ALL_APPLICATION_PACKAGES_SID, False),
            ),
        )
        self.assertEqual(len(result.grants), 3)
        self.assertNotEqual(result.grants[2][0], lpac.ALL_APPLICATION_PACKAGES_SID)
        self.assertTrue(result.grants[2][1])

    def test_real_lpac_portability_matrix_and_cleanup(self):
        natural = run_native_probe()
        self.assertEqual((natural.outcome, natural.reason), ("pass", None))
        self.assertTrue(natural.native_selector)
        self.assertIn(
            natural.route,
            {lpac.LPAC_PROOF_CLASS_46, lpac.LPAC_PROOF_ACCESS_CHECK},
        )
        self.assert_clean(natural)
        if natural.route == lpac.LPAC_PROOF_ACCESS_CHECK:
            self.assert_access_route(natural)
        else:
            self.assertEqual(natural.controls, 0)
            self.assertEqual(len(natural.creations), 1)
            self.assertEqual(natural.grants, ())

        opposite_route = (
            lpac.LPAC_PROOF_ACCESS_CHECK
            if natural.route == lpac.LPAC_PROOF_CLASS_46
            else lpac.LPAC_PROOF_CLASS_46
        )
        opposite = run_native_probe(selector_override=opposite_route)
        self.assertEqual((opposite.outcome, opposite.reason), ("pass", None))
        self.assertFalse(opposite.native_selector)
        self.assertEqual(opposite.route, opposite_route)
        self.assert_clean(opposite)
        if opposite_route == lpac.LPAC_PROOF_ACCESS_CHECK:
            self.assert_access_route(opposite)
        else:
            self.assertEqual(opposite.controls, 0)

        rejected = run_native_probe(
            selector_override=lpac.LPAC_PROOF_ACCESS_CHECK,
            semantic_fault=True,
        )
        self.assertEqual(
            (rejected.outcome, rejected.reason),
            ("sandbox_violation", "sandbox_boundary_violation"),
        )
        self.assertEqual(rejected.route, lpac.LPAC_PROOF_ACCESS_CHECK)
        self.assert_clean(rejected)

        unknown = run_native_probe(selector_override="unknown")
        self.assertEqual(
            (unknown.outcome, unknown.reason),
            ("sandbox_violation", "sandbox_boundary_violation"),
        )
        self.assertIsNone(unknown.route)
        self.assert_clean(unknown)

        cleanup = run_native_probe(
            selector_override=lpac.LPAC_PROOF_ACCESS_CHECK,
            cleanup_uncertain=True,
        )
        self.assertEqual(
            (cleanup.outcome, cleanup.reason),
            ("sandbox_cleanup_failed", "sandbox_cleanup_failed"),
        )
        self.assert_clean(cleanup)

        profile_absence = run_native_probe(
            selector_override=lpac.LPAC_PROOF_CLASS_46,
            profile_absence_uncertain=True,
        )
        self.assertEqual(
            (profile_absence.outcome, profile_absence.reason),
            ("sandbox_cleanup_failed", "sandbox_cleanup_failed"),
        )
        self.assert_clean(profile_absence)
