"""Pure lifecycle tests for the TG-M24.1B runtime diagnostic fixture."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from task_governance_tool import _verification_runner_lpac_win32 as lpac
from tests import m241b_lpac_runtime_diagnostic_fixture as fixture
from tests import m241b_runtime_qualification_support as evidence
from tests import m241b_runtime_trace_win32 as runtime_trace


_RUNTIME_DIGEST = "sha256:" + "a" * 64
_DACL_DIGEST = "sha256:" + "d" * 64
_WINDOW_BINDING = "window-sha256:" + "b" * 64


class _FakeChild:
    def __init__(self) -> None:
        self.process_id = 37
        self.process = object()


class _FakeContext:
    def __init__(
        self,
        log: list[str],
        *,
        route: str = lpac.LPAC_PROOF_CLASS_46,
        fail_at: str | None = None,
        cleanup_complete: bool = True,
    ) -> None:
        self.log = log
        self.route = route
        self.fail_at = fail_at
        self.cleanup_complete = cleanup_complete
        self.application = Path("C:/private/runtime/python.exe")
        capture = fixture.RuntimeCapture(
            (3, 14, 0),
            (
                fixture.RuntimeEntry(
                    "python.exe", 1, "1" * 64, Path("C:/source/python.exe")
                ),
                fixture.RuntimeEntry(
                    "python314.dll",
                    1,
                    "2" * 64,
                    Path("C:/source/python314.dll"),
                ),
            ),
            _RUNTIME_DIGEST,
            1,
            2,
            Path("C:/source"),
        )
        self.inventory = fixture.build_collector_inventory(capture)
        self.runtime_digest = _RUNTIME_DIGEST
        self.dacl_proof = fixture.TreeDaclProof(3, 400, _DACL_DIGEST)
        self.child = _FakeChild()

    def _step(self, name: str) -> None:
        self.log.append(name)
        if self.fail_at == name:
            raise fixture.RuntimeDiagnosticError("diagnostic_boundary_violation")

    def create_suspended_child(self) -> _FakeChild:
        self._step("context.create")
        return self.child

    def prove_route_and_control(self, child: object) -> tuple[str, bool]:
        self.assert_child(child)
        self._step("context.route")
        if self.route == lpac.LPAC_PROOF_ACCESS_CHECK:
            self._step("context.control_cleanup")
            return self.route, True
        return self.route, False

    def prove_runtime_access(self, child: object) -> None:
        self.assert_child(child)
        self._step("context.access")

    def close_child_security(self, child: object) -> None:
        self.assert_child(child)
        self._step("context.close_security")

    def resume_once(self, child: object) -> None:
        self.assert_child(child)
        self._step("context.resume")

    def wait_access_denied(self, child: object) -> None:
        self.assert_child(child)
        self._step("context.wait_access_denied")

    def finish_child_and_reprove(self, child: object) -> None:
        self.assert_child(child)
        self._step("context.finish")

    def close(self) -> fixture.DiagnosticCleanupProof:
        self.log.append("context.close")
        if self.fail_at == "context.close":
            raise fixture.RuntimeDiagnosticError("diagnostic_cleanup_failed")
        return fixture.DiagnosticCleanupProof(
            self.cleanup_complete,
            self.cleanup_complete,
            self.cleanup_complete,
            self.cleanup_complete,
            self.cleanup_complete,
        )

    def assert_child(self, child: object) -> None:
        if child is not self.child:
            raise AssertionError("wrong child")


def _quality(
    manifest: evidence.InventoryManifest,
) -> evidence.CollectionQualityProof:
    return evidence.bind_collection_quality(
        subject_proof=evidence.STOCK_CHILD_ACCESS_DENIED_PROOF,
        window_binding=_WINDOW_BINDING,
        inventory_manifest=manifest,
        planes=tuple(
            evidence.PlaneCollectionQualityInput(
                plane=plane,
                collection_schema=evidence.COLLECTION_SCHEMA,
                probe_available=True,
                lossless=True,
                overflowed=False,
                plane_scope_complete=True,
                correlation_complete=True,
                cleanup_proved=True,
            )
            for plane in evidence.PLANE_ORDER
        ),
    )


def _classification(
    inventory: fixture.CollectorInventory,
) -> fixture.CollectorClassification:
    quality = _quality(inventory.manifest)
    objects = {
        plane.plane: plane.object_refs[0] for plane in inventory.manifest.planes
    }
    operations = {
        "file_access": "file_create",
        "dll_image_load": "image_map",
        "registry_access": "registry_open",
        "code_integrity_policy": "image_policy_validate",
    }
    policies = {
        "file_access": "file_io",
        "dll_image_load": "image_loader",
        "registry_access": "registry_access",
        "code_integrity_policy": "code_integrity",
    }
    planes = []
    for plane in evidence.PLANE_ORDER:
        denial = plane == "file_access"
        planes.append(
            {
                "object_ref": objects[plane] if denial else None,
                "operation": operations[plane],
                "outcome": "denial" if denial else "observed_no_denial",
                "plane": plane,
                "policy": policies[plane],
                "reason": None,
            }
        )
    payload = {
        "candidate_id": evidence.CURRENT_CANDIDATE_ID,
        "collection_proof_digest": quality.proof_digest,
        "exit_binding": evidence.STOCK_CHILD_ACCESS_DENIED_PROOF.exit_binding,
        "inventory_manifest_digest": inventory.manifest.manifest_digest,
        "planes": planes,
        "runtime_digest": inventory.runtime_digest,
        "schema_version": evidence.SCHEMA_VERSION,
        "subject": evidence.STOCK_CHILD_ACCESS_DENIED_PROOF.subject,
    }
    document = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return fixture.CollectorClassification(document, quality, True)


class _FakeCollector:
    def __init__(
        self,
        log: list[str],
        *,
        fail_at: str | None = None,
        invalid_document: bool = False,
        abort_fails: bool = False,
    ) -> None:
        self.log = log
        self.fail_at = fail_at
        self.invalid_document = invalid_document
        self.abort_fails = abort_fails
        self.inventory: fixture.CollectorInventory | None = None

    def _step(self, name: str) -> None:
        self.log.append(name)
        if self.fail_at == name:
            raise RuntimeError("raw provider detail must be normalized")

    def start_for_suspended_child(
        self,
        *,
        application: Path,
        inventory: fixture.CollectorInventory,
        process_id: int,
        process_handle: object,
    ) -> None:
        self._step("collector.start")
        if not application.is_absolute():
            raise AssertionError("application must be exact and absolute")
        self.inventory = inventory
        if process_id != 37 or process_handle is None:
            raise AssertionError("collector binding drift")

    def stop_and_classify(
        self, *, access_denied: bool
    ) -> fixture.CollectorClassification:
        self._step("collector.stop")
        if access_denied is not True or self.inventory is None:
            raise AssertionError("baseline binding drift")
        result = _classification(self.inventory)
        if self.invalid_document:
            return fixture.CollectorClassification(
                b'{"raw_path":"C:/secret"}', result.quality, True
            )
        return result

    def abort(self) -> None:
        self.log.append("collector.abort")
        if self.abort_fails:
            raise RuntimeError("raw abort detail")


class RuntimeDiagnosticLifecycleTests(unittest.TestCase):
    def test_success_orders_proof_collector_resume_and_cleanup(self) -> None:
        log: list[str] = []
        context = _FakeContext(log)
        result = fixture._execute_diagnostic(context, _FakeCollector(log))
        self.assertEqual(
            log,
            [
                "context.create",
                "context.route",
                "context.access",
                "context.close_security",
                "collector.start",
                "context.resume",
                "context.wait_access_denied",
                "collector.stop",
                "context.finish",
                "context.close",
            ],
        )
        self.assertEqual(result.runtime_digest, _RUNTIME_DIGEST)
        self.assertEqual(result.resume_count, 1)
        self.assertTrue(result.access_denied_baseline)
        self.assertFalse(result.normal_control_created)
        self.assertFalse(result.root_cause.has_inconclusive)
        self.assertEqual(
            result.cleanup,
            fixture.DiagnosticCleanupProof(True, True, True, True, True),
        )

    def test_portable_route_control_is_clean_before_collector_and_resume(self) -> None:
        log: list[str] = []
        context = _FakeContext(log, route=lpac.LPAC_PROOF_ACCESS_CHECK)
        result = fixture._execute_diagnostic(context, _FakeCollector(log))
        self.assertTrue(result.normal_control_created)
        self.assertLess(
            log.index("context.control_cleanup"), log.index("collector.start")
        )
        self.assertLess(log.index("context.close_security"), log.index("context.resume"))

    def test_collector_start_failure_aborts_and_never_resumes(self) -> None:
        log: list[str] = []
        with self.assertRaisesRegex(
            fixture.RuntimeDiagnosticError, "failed closed"
        ) as raised:
            fixture._execute_diagnostic(
                _FakeContext(log), _FakeCollector(log, fail_at="collector.start")
            )
        self.assertEqual(raised.exception.code, "diagnostic_collector_failed")
        self.assertIn("collector.abort", log)
        self.assertIn("context.close", log)
        self.assertNotIn("context.resume", log)
        self.assertNotIn("raw provider", str(raised.exception))

    def test_collector_start_binding_failure_aborts_and_never_resumes(self) -> None:
        log: list[str] = []
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            fixture._execute_diagnostic(
                _FakeContext(log), _FakeCollector(log, fail_at="collector.start")
            )
        self.assertEqual(raised.exception.code, "diagnostic_collector_failed")
        self.assertIn("collector.abort", log)
        self.assertNotIn("context.resume", log)
        self.assertEqual(log[-1], "context.close")

    def test_pre_resume_boundary_failure_never_starts_collector(self) -> None:
        log: list[str] = []
        with self.assertRaises(fixture.RuntimeDiagnosticError):
            fixture._execute_diagnostic(
                _FakeContext(log, fail_at="context.access"), _FakeCollector(log)
            )
        self.assertNotIn("collector.start", log)
        self.assertNotIn("context.resume", log)
        self.assertEqual(log[-1], "context.close")

    def test_baseline_mismatch_aborts_collection_and_cleans(self) -> None:
        log: list[str] = []
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            fixture._execute_diagnostic(
                _FakeContext(log, fail_at="context.wait_access_denied"),
                _FakeCollector(log),
            )
        self.assertEqual(raised.exception.code, "diagnostic_boundary_violation")
        self.assertIn("context.resume", log)
        self.assertIn("collector.abort", log)
        self.assertNotIn("collector.stop", log)
        self.assertEqual(log[-1], "context.close")

    def test_collector_stop_failure_aborts_and_cleanup_still_runs(self) -> None:
        log: list[str] = []
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            fixture._execute_diagnostic(
                _FakeContext(log), _FakeCollector(log, fail_at="collector.stop")
            )
        self.assertEqual(raised.exception.code, "diagnostic_collector_failed")
        self.assertIn("collector.abort", log)
        self.assertEqual(log[-1], "context.close")

    def test_abort_or_context_cleanup_uncertainty_is_cleanup_failure(self) -> None:
        for abort_fails, context_fail in ((True, None), (False, "context.close")):
            with self.subTest(abort_fails=abort_fails, context_fail=context_fail):
                log: list[str] = []
                with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                    fixture._execute_diagnostic(
                        _FakeContext(log, fail_at=context_fail),
                        _FakeCollector(
                            log,
                            fail_at="collector.start" if abort_fails else None,
                            abort_fails=abort_fails,
                        ),
                    )
                self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")

    def test_incomplete_cleanup_proof_dominates_success_and_boundary_failure(self) -> None:
        for fail_at in (None, "context.wait_access_denied"):
            with self.subTest(fail_at=fail_at):
                log: list[str] = []
                with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                    fixture._execute_diagnostic(
                        _FakeContext(
                            log,
                            fail_at=fail_at,
                            cleanup_complete=False,
                        ),
                        _FakeCollector(log),
                    )
                self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
                self.assertEqual(log[-1], "context.close")

    def test_setup_error_is_dominated_by_incomplete_cleanup_proof(self) -> None:
        incomplete = fixture.DiagnosticCleanupProof(
            False, False, False, False, False
        )
        with (
            patch.object(
                fixture.tempfile,
                "TemporaryDirectory",
                side_effect=OSError("PRIVATE_SETUP_CANARY"),
            ),
            patch.object(
                fixture._RealDiagnosticContext,
                "close",
                return_value=incomplete,
            ),
            self.assertRaises(fixture.RuntimeDiagnosticError) as raised,
        ):
            fixture._RealDiagnosticContext.create()
        self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
        self.assertNotIn("PRIVATE_SETUP_CANARY", str(raised.exception))

    def test_early_setup_error_with_vacuous_cleanup_keeps_safe_error(self) -> None:
        with (
            patch.object(
                fixture.tempfile,
                "TemporaryDirectory",
                side_effect=OSError("PRIVATE_EARLY_SETUP_CANARY"),
            ),
            self.assertRaises(fixture.RuntimeDiagnosticError) as raised,
        ):
            fixture._RealDiagnosticContext.create()
        self.assertEqual(raised.exception.code, "diagnostic_boundary_violation")
        self.assertNotIn("PRIVATE_EARLY_SETUP_CANARY", str(raised.exception))

    def test_child_job_zero_becomes_unproved_immediately_after_job_acquisition(self) -> None:
        context = fixture._RealDiagnosticContext()
        fake_job = object()
        with (
            patch.object(
                fixture.portability._FixtureJob,
                "create",
                return_value=fake_job,
            ),
            patch.object(
                fixture.portability._FixtureStdio,
                "create",
                side_effect=OSError("PRIVATE_STDIO_CANARY"),
            ),
            self.assertRaises(OSError),
        ):
            context.create_suspended_child()
        self.assertIs(context._child_job, fake_job)
        self.assertFalse(context._child_job_zero)
        context._child_job = None

    def test_control_job_create_failure_keeps_vacuous_cleanup_proved(self) -> None:
        context = fixture._RealDiagnosticContext()
        child = object.__new__(lpac.SuspendedLpacChild)
        child.primary_token = object()
        context._child = child
        with (
            patch.object(
                fixture.lpac,
                "prove_lpac_route",
                return_value=SimpleNamespace(route=lpac.LPAC_PROOF_ACCESS_CHECK),
            ),
            patch.object(
                fixture.portability._FixtureJob,
                "create",
                side_effect=fixture.RuntimeDiagnosticError(
                    "diagnostic_boundary_violation"
                ),
            ),
            self.assertRaises(fixture.RuntimeDiagnosticError) as raised,
        ):
            context.prove_route_and_control(child)
        self.assertEqual(raised.exception.code, "diagnostic_boundary_violation")
        self.assertTrue(context._control_job_zero)
        self.assertIsNone(context._control_job)
        context._child = None
        self.assertEqual(
            context.close(),
            fixture.DiagnosticCleanupProof(True, True, True, True, True),
        )

    def test_invalid_classification_is_rejected_after_native_cleanup(self) -> None:
        log: list[str] = []
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            fixture._execute_diagnostic(
                _FakeContext(log), _FakeCollector(log, invalid_document=True)
            )
        self.assertEqual(raised.exception.code, "diagnostic_collector_failed")
        self.assertEqual(log[-1], "context.close")

    def test_result_contract_has_no_raw_process_or_path_fields(self) -> None:
        names = {item.name for item in fields(fixture.NativeDiagnosticResult)}
        forbidden = {
            "application",
            "argv",
            "environment",
            "exit_code",
            "log",
            "path",
            "process_handle",
            "process_id",
            "provider",
            "status",
            "stderr",
            "stdout",
        }
        self.assertTrue(names.isdisjoint(forbidden))
        log: list[str] = []
        rendered = repr(
            fixture._execute_diagnostic(_FakeContext(log), _FakeCollector(log))
        )
        self.assertNotIn("C:/", rendered)
        self.assertNotIn("process_id", rendered)


class RuntimeMirrorAndAclPolicyTests(unittest.TestCase):
    @staticmethod
    def _entry(relative: str, path: Path) -> fixture.RuntimeEntry:
        body = path.read_bytes()
        return fixture.RuntimeEntry(
            relative, len(body), hashlib.sha256(body).hexdigest(), path
        )

    def test_runtime_copy_close_fault_still_attempts_both_descriptors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="taskgov-m241b-copy-") as root:
            source = Path(root) / "source.bin"
            destination = Path(root) / "destination.bin"
            content = b"bounded-runtime-copy"
            source.write_bytes(content)
            entry = fixture.RuntimeEntry(
                "source.bin",
                len(content),
                hashlib.sha256(content).hexdigest(),
                source,
            )
            actual_close = fixture.os.close
            closed: list[int] = []

            def close_with_first_fault(descriptor: int) -> None:
                closed.append(descriptor)
                actual_close(descriptor)
                if len(closed) == 1:
                    raise OSError("PRIVATE_CLOSE_CANARY")

            with (
                patch.object(
                    fixture.os, "close", side_effect=close_with_first_fault
                ),
                self.assertRaises(fixture.RuntimeDiagnosticError) as raised,
            ):
                fixture._copy_runtime_entry(entry, destination)
            self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
            self.assertEqual(len(closed), 2)
            self.assertEqual(len(set(closed)), 2)
            self.assertNotIn("PRIVATE_CLOSE_CANARY", str(raised.exception))

    def test_descriptor_sid_allocation_and_close_faults_cleanup_all_owned_sids(self) -> None:
        class FakeSid:
            def __init__(self, value: str, *, fail_close: bool = False) -> None:
                self.value = value
                self.pointer = object()
                self.fail_close = fail_close
                self.closed = False

            def close(self) -> None:
                self.closed = True
                if self.fail_close:
                    raise RuntimeError("PRIVATE_SID_CLOSE_CANARY")

        created: list[FakeSid] = []

        def allocate(value: str) -> FakeSid:
            if len(created) == 2:
                raise fixture.RuntimeDiagnosticError(
                    "diagnostic_boundary_violation"
                )
            sid = FakeSid(value)
            created.append(sid)
            return sid

        with (
            patch.object(fixture.lpac, "_OwnedLocalSid", side_effect=allocate),
            self.assertRaises(fixture.RuntimeDiagnosticError) as raised,
        ):
            fixture._ExactFilesystemDescriptor(
                "S-1-5-21-1-2-3-4", "S-1-15-2-1-2-3-4"
            )
        self.assertEqual(raised.exception.code, "diagnostic_boundary_violation")
        self.assertTrue(created)
        self.assertTrue(all(item.closed for item in created))

        first = FakeSid("first", fail_close=True)
        second = FakeSid("second")
        descriptor = object.__new__(fixture._ExactFilesystemDescriptor)
        descriptor.pointer = fixture.LPVOID()
        descriptor.dacl = fixture.LPVOID()
        descriptor._sids = [first, second]
        with self.assertRaises(fixture.RuntimeDiagnosticError) as cleanup:
            descriptor.close()
        self.assertEqual(cleanup.exception.code, "diagnostic_cleanup_failed")
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertEqual(descriptor._sids, [])

    def test_small_runtime_mirror_is_digest_bound_and_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m241b-runtime-pure-") as value:
            root = Path(value)
            source = root / "source"
            destination = root / "mirror"
            (source / "DLLs").mkdir(parents=True)
            (source / "Lib").mkdir()
            destination.mkdir()
            files = {
                "python.exe": b"exe",
                "python314.dll": b"runtime",
                "DLLs/_ssl.pyd": b"extension",
                "Lib/os.py": b"pass\n",
            }
            for relative, body in files.items():
                path = source.joinpath(*relative.split("/"))
                path.write_bytes(body)
            entries = tuple(
                self._entry(relative, source.joinpath(*relative.split("/")))
                for relative in sorted(files, key=lambda item: item.encode("utf-8"))
            )
            provisional = fixture.RuntimeCapture(
                (3, 14, 0), entries, "", 0, sum(item.size_bytes for item in entries), source
            )
            canonical = provisional.canonical_value()
            digest = fixture._domain_digest(fixture.RUNTIME_DIGEST_DOMAIN, canonical)
            capture = fixture.RuntimeCapture(
                (3, 14, 0),
                entries,
                digest,
                len(fixture._canonical_bytes(canonical)),
                sum(item.size_bytes for item in entries),
                source,
            )
            mirror = fixture.mirror_cpython_runtime(capture, destination)
            fixture.prove_runtime_mirror(capture, mirror)
            target = destination / "Lib" / "os.py"
            os.chmod(target, 0o666)
            target.write_bytes(b"tampered")
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture.prove_runtime_mirror(capture, mirror)

    def test_diagnostic_argv_uses_exact_runner_flags_and_fixture_bootstrap(self) -> None:
        runtime = Path("C:/attempt/runtime/python.exe")
        argv = fixture.build_diagnostic_argv(runtime)
        self.assertEqual(
            argv,
            (
                str(runtime),
                "-I",
                "-B",
                "-X",
                "utf8",
                "-c",
                fixture.FIXED_DIAGNOSTIC_BOOTSTRAP,
            ),
        )
        self.assertTrue({"-S", "-E", "-s"}.isdisjoint(argv))

    def test_package_sid_dacl_scope_is_only_exact_runtime_root(self) -> None:
        mirror = fixture.RuntimeMirror(
            (),
            _RUNTIME_DIGEST,
            1,
            0,
            Path("C:/attempt/runtime"),
            Path("C:/attempt/runtime/python.exe"),
        )
        expected = fixture.TreeDaclProof(1, 100, _DACL_DIGEST)
        with patch.object(
            fixture, "seal_runtime_tree", return_value=expected
        ) as sealed:
            self.assertEqual(
                fixture._seal_exact_runtime_scope(
                    mirror, "S-1-15-2-123"
                ),
                expected,
            )
        sealed.assert_called_once_with(
            Path("C:/attempt/runtime"), "S-1-15-2-123"
        )

    def test_collector_inventory_is_closed_unique_and_digest_bound(self) -> None:
        context = _FakeContext([])
        inventory = context.inventory
        self.assertEqual(
            tuple(plane.plane for plane in inventory.manifest.planes),
            evidence.PLANE_ORDER,
        )
        refs = [item.object_ref for item in inventory.objects]
        self.assertEqual(len(refs), len(set(refs)))
        self.assertTrue(all(item.startswith("inventory-sha256:") for item in refs))
        self.assertNotIn("C:/", repr(inventory))

    def test_exact_resolver_cross_plane_binding_never_uses_category_bucket(self) -> None:
        capture = fixture.RuntimeCapture(
            (3, 14, 0),
            (
                fixture.RuntimeEntry(
                    "python.exe", 1, "1" * 64, Path("C:/source/python.exe")
                ),
                fixture.RuntimeEntry(
                    "python314.dll",
                    1,
                    "2" * 64,
                    Path("C:/source/python314.dll"),
                ),
                fixture.RuntimeEntry(
                    "Lib/encodings/utf_8.py",
                    1,
                    "3" * 64,
                    Path("C:/source/Lib/encodings/utf_8.py"),
                ),
            ),
            _RUNTIME_DIGEST,
            1,
            3,
            Path("C:/source"),
        )
        inventory = fixture.build_collector_inventory(
            capture, system_images=("kernel32.dll", "ntdll.dll")
        )
        file_ref = inventory.resolve(
            plane="file_access",
            match_kind="exact_file_identity",
            component="runtime-relative:python.exe",
        )
        image_ref = inventory.resolve(
            plane="dll_image_load",
            match_kind="exact_image_identity",
            component="runtime-image:python.exe",
        )
        ci_ref = inventory.resolve(
            plane="code_integrity_policy",
            match_kind="exact_ci_image_identity",
            component="runtime-image:python.exe",
        )
        self.assertEqual(len({file_ref, image_ref, ci_ref}), 3)
        self.assertIsNone(
            inventory.resolve(
                plane="file_access",
                match_kind="exact_file_identity",
                component="runtime/top-level",
            )
        )
        self.assertIsNone(
            inventory.resolve(
                plane="dll_image_load",
                match_kind="exact_image_identity",
                component="system32-image:unknown.dll",
            )
        )

    def test_exact_package_mask_has_no_write_delete_or_broad_principal(self) -> None:
        trustees, masks = fixture._expected_dacl(
            "S-1-5-21-100", "S-1-15-2-123"
        )
        self.assertEqual(
            trustees,
            (
                fixture.SYSTEM_SID,
                "S-1-5-21-100",
                fixture.OWNER_RIGHTS_SID,
                "S-1-15-2-123",
            ),
        )
        self.assertNotIn(lpac.ALL_APPLICATION_PACKAGES_SID, trustees)
        self.assertEqual(masks[-1], fixture.EXACT_PACKAGE_MASK)
        for right in fixture._DENIED_FILE_RIGHTS:
            self.assertEqual(masks[-1] & right, 0)
        self.assertEqual(masks[-1] & 0xF0000000, 0)

    def test_security_info_rejects_partial_descriptor_without_group(self) -> None:
        def get_security_info(
            _handle,
            _kind,
            _requested,
            owner,
            _group,
            dacl,
            _sacl,
            descriptor,
        ) -> int:
            owner._obj.value = 11
            dacl._obj.value = 12
            descriptor._obj.value = 13
            return 0

        fake = SimpleNamespace(
            advapi32=SimpleNamespace(GetSecurityInfo=get_security_info)
        )
        token = lpac.OwnedHandle(99)
        with patch.object(fixture, "_dacl_apis", return_value=fake), patch.object(
            lpac, "_local_free"
        ):
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture._security_info(token)

    def test_access_check_rejects_noncanonical_native_results(self) -> None:
        token = lpac.OwnedHandle(101)
        desired = fixture.FILE_WRITE_DATA

        def invoke(kind: str) -> None:
            def access_check(
                _descriptor,
                _token,
                requested,
                mapping,
                privilege,
                privilege_length,
                granted,
                allowed,
            ) -> bool:
                privilege_value = privilege._obj
                privilege_value.PrivilegeCount = 0
                privilege_value.Control = 0
                privilege_value.Privilege[0].Luid.LowPart = 0
                privilege_value.Privilege[0].Luid.HighPart = 0
                privilege_value.Privilege[0].Attributes = 0
                privilege_length._obj.value = fixture.ctypes.sizeof(
                    lpac._PRIVILEGE_SET_ONE
                )
                granted._obj.value = 0
                allowed._obj.value = 0
                if kind == "privilege_count":
                    privilege_value.PrivilegeCount = 1
                elif kind == "privilege_length":
                    privilege_length._obj.value -= 1
                elif kind == "granted_extra":
                    granted._obj.value = int(requested) | 1
                    allowed._obj.value = 1
                elif kind == "mapping_drift":
                    mapping._obj.GenericWrite = 0
                return True

            fake = SimpleNamespace(
                advapi32=SimpleNamespace(AccessCheck=access_check)
            )
            with patch.object(
                lpac, "_query_exact_token_dword"
            ) as token_query, patch.object(lpac, "_apis", return_value=fake):
                with self.assertRaises(
                    (lpac.LpacProofError, fixture.RuntimeDiagnosticError)
                ):
                    fixture._access_allowed(fixture.LPVOID(1), token, desired)
                self.assertEqual(token_query.call_count, 2)

        for kind in (
            "privilege_count",
            "privilege_length",
            "granted_extra",
            "mapping_drift",
        ):
            with self.subTest(kind=kind):
                invoke(kind)


class RealtimeCollectorAdapterTests(unittest.TestCase):
    def _factory(
        self,
        *,
        log: list[str],
        inventory: fixture.CollectorInventory,
        application: Path,
        fail_start: bool = False,
        fail_stop: bool = False,
        cleanup_proved: bool = True,
        invalid_candidate: bool = False,
        abort_fails: bool = False,
    ):
        test_case = self

        class FakeRealtimeCollector:
            def __init__(self, binding: runtime_trace.InventoryBinding) -> None:
                log.append("trace.factory")
                self.binding = binding

            def start_for_suspended_child(
                self,
                *,
                process_id: int,
                process_handle: int,
                initial_image_object_ref: str,
            ) -> None:
                log.append("trace.start")
                test_case.assertEqual(process_id, 37)
                test_case.assertEqual(process_handle, 777)
                test_case.assertEqual(
                    initial_image_object_ref,
                    self.binding.resolve("dll_image_load", str(application)),
                )
                if fail_start:
                    raise runtime_trace.RuntimeTraceError("trace_unavailable")

            def record_subject_proof(self, proof: str) -> None:
                log.append("trace.subject")
                test_case.assertEqual(proof, runtime_trace.SUBJECT_ACCESS_DENIED)

            def stop(self) -> runtime_trace.RuntimeTraceResult:
                log.append("trace.stop")
                if fail_stop:
                    raise runtime_trace.RuntimeTraceError("trace_unavailable")
                refs = {
                    plane.plane: plane.object_refs[0]
                    for plane in inventory.manifest.planes
                }
                quality = tuple(
                    runtime_trace.PlaneTraceQuality(
                        plane,
                        (
                            runtime_trace.UNKNOWN_COLLECTION_SCHEMA
                            if plane == "code_integrity_policy"
                            else runtime_trace.COLLECTION_SCHEMA
                        ),
                        plane != "code_integrity_policy",
                        True,
                        False,
                        True,
                        True,
                        True,
                    )
                    for plane in evidence.PLANE_ORDER
                )
                planes = (
                    runtime_trace.PlaneTraceResult(
                        "file_access",
                        "denial",
                        refs["file_access"],
                        "file_create",
                        "file_io",
                        None,
                    ),
                    runtime_trace.PlaneTraceResult(
                        "dll_image_load",
                        "observed_no_denial",
                        None,
                        "image_map",
                        "image_loader",
                        None,
                    ),
                    runtime_trace.PlaneTraceResult(
                        "registry_access",
                        "observed_no_denial",
                        None,
                        "registry_open",
                        "registry_access",
                        None,
                    ),
                    runtime_trace.PlaneTraceResult(
                        "code_integrity_policy",
                        "inconclusive",
                        None,
                        "image_policy_validate",
                        "code_integrity",
                        "probe_unavailable",
                    ),
                )
                return runtime_trace.RuntimeTraceResult(
                    (
                        "current-runtime-drift"
                        if invalid_candidate
                        else evidence.CURRENT_CANDIDATE_ID
                    ),
                    inventory.runtime_digest,
                    runtime_trace.SUBJECT_ACCESS_DENIED,
                    4,
                    256,
                    4,
                    cleanup_proved,
                    _WINDOW_BINDING,
                    quality,
                    planes,
                )

            def abort(self) -> None:
                log.append("trace.abort")
                if abort_fails:
                    raise runtime_trace.RuntimeTraceError("trace_cleanup_unproved")

        return FakeRealtimeCollector

    def test_adapter_connects_exact_binding_to_lifecycle_and_scrubs_resolver(self) -> None:
        log: list[str] = []
        context = _FakeContext(log)
        context.child.process = lpac.OwnedHandle(777)
        bindings: list[runtime_trace.InventoryBinding] = []
        base_factory = self._factory(
            log=log,
            inventory=context.inventory,
            application=context.application,
        )

        def factory(binding: runtime_trace.InventoryBinding):
            bindings.append(binding)
            return base_factory(binding)

        adapter = fixture._RealtimeCollectorAdapter(
            collector_factory=factory,
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
        )
        result = fixture._execute_diagnostic(context, adapter)
        self.assertTrue(result.root_cause.has_inconclusive)
        self.assertEqual(
            log,
            [
                "context.create",
                "context.route",
                "context.access",
                "context.close_security",
                "trace.factory",
                "trace.start",
                "context.resume",
                "context.wait_access_denied",
                "trace.subject",
                "trace.stop",
                "context.finish",
                "context.close",
            ],
        )
        self.assertEqual(len(bindings), 1)
        self.assertIsNone(
            bindings[0].resolve("dll_image_load", str(context.application))
        )

    def test_adapter_start_failure_calls_owned_abort_and_never_resumes(self) -> None:
        log: list[str] = []
        context = _FakeContext(log)
        context.child.process = lpac.OwnedHandle(777)
        adapter = fixture._RealtimeCollectorAdapter(
            collector_factory=self._factory(
                log=log,
                inventory=context.inventory,
                application=context.application,
                fail_start=True,
            ),
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
        )
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            fixture._execute_diagnostic(context, adapter)
        self.assertEqual(raised.exception.code, "diagnostic_collector_failed")
        self.assertIn("trace.abort", log)
        self.assertNotIn("context.resume", log)

    def test_adapter_stop_faults_abort_once_and_cleanup_failure_dominates(self) -> None:
        cases = (
            ({"fail_stop": True}, "diagnostic_collector_failed"),
            ({"cleanup_proved": False}, "diagnostic_cleanup_failed"),
            ({"invalid_candidate": True}, "diagnostic_collector_failed"),
            (
                {"fail_stop": True, "abort_fails": True},
                "diagnostic_cleanup_failed",
            ),
        )
        for options, expected_code in cases:
            with self.subTest(options=options):
                log: list[str] = []
                context = _FakeContext(log)
                context.child.process = lpac.OwnedHandle(777)
                adapter = fixture._RealtimeCollectorAdapter(
                    collector_factory=self._factory(
                        log=log,
                        inventory=context.inventory,
                        application=context.application,
                        **options,
                    ),
                    system32_root=Path("C:/Windows/System32"),
                    current_user_sid="S-1-5-21-100",
                )
                with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                    fixture._execute_diagnostic(context, adapter)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(log.count("trace.abort"), 1)
                self.assertEqual(log[-1], "context.close")

    def test_concrete_realtime_entrypoint_uses_adapter(self) -> None:
        sentinel = object()
        with patch.object(
            fixture, "run_current_runtime_diagnostic", return_value=sentinel
        ) as run:
            self.assertIs(fixture.run_realtime_current_runtime_diagnostic(), sentinel)
        collector = run.call_args.args[0]
        self.assertIsInstance(collector, fixture._RealtimeCollectorAdapter)


if __name__ == "__main__":
    unittest.main()
