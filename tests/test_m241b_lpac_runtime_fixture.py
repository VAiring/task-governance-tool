"""Pure lifecycle tests for the TG-M24.1B runtime diagnostic fixture."""

from __future__ import annotations

import hashlib
import ctypes
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict, fields, replace
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


def _proof_kwargs(
    capture: fixture.RuntimeCapture,
    *,
    relations: tuple[fixture.RuntimeImportRelation, ...] | None = None,
    api_relations: tuple[fixture.ApiSetHostRelation, ...] = (),
    system_images: tuple[str, ...] = ("kernel32.dll", "ntdll.dll"),
    known_contracts: tuple[str, ...] = ("kernel32.dll", "ntdll.dll"),
) -> dict[str, object]:
    if relations is None:
        version_dll = next(
            item.path
            for item in capture.entries
            if "/" not in item.path
            and item.path.startswith("python")
            and item.path.endswith(".dll")
        )
        relations = (
            fixture.RuntimeImportRelation(
                "python.exe", "normal", version_dll
            ),
            fixture.RuntimeImportRelation(
                version_dll, "normal", "kernel32.dll"
            ),
        )
    provenance = fixture._bind_runtime_import_provenance(capture, relations)
    api = fixture._bind_api_set_qualification(provenance, api_relations)
    rows = tuple(
        sorted(
            (
                ("DllDirectory", r"%SystemRoot%\System32", 2),
                *(
                    (name.removesuffix(".dll"), name, 1)
                    for name in known_contracts
                ),
            )
        )
    )
    known = fixture._bind_known_dll_qualification(
        system_images, (0, len(rows), 7), rows
    )
    return {
        "import_provenance": provenance,
        "api_set_qualification": api,
        "known_dll_qualification": known,
        "system_images": system_images,
    }


def _build_inventory(
    capture: fixture.RuntimeCapture,
    **overrides: object,
) -> fixture.CollectorInventory:
    arguments = _proof_kwargs(capture)
    arguments.update(overrides)
    return fixture._build_collector_inventory_from_proofs(capture, **arguments)


class _FakeAuthorityLease(fixture._InventoryAuthorityLease):
    def __init__(
        self,
        *,
        fail_on_reprove: int | None = None,
        close_failures: int = 0,
        log: list[str] | None = None,
    ) -> None:
        self.inventory: fixture.CollectorInventory | None = None
        self.fail_on_reprove = fail_on_reprove
        self.close_failures = close_failures
        self.log = log
        self.reprove_calls = 0
        self.close_calls = 0

    def initialize(self, inventory: fixture.CollectorInventory) -> None:
        if self.inventory is not None:
            raise fixture.RuntimeDiagnosticError("diagnostic_collector_failed")
        self.inventory = inventory
        if self.log is not None:
            self.log.append("authority.initialize")
        self.reprove()

    def reprove(self) -> None:
        self.reprove_calls += 1
        if self.log is not None:
            self.log.append("authority.reprove")
        if (
            self.inventory is None
            or self.fail_on_reprove == self.reprove_calls
        ):
            raise fixture.RuntimeDiagnosticError("diagnostic_collector_failed")

    def close(self) -> None:
        self.close_calls += 1
        if self.log is not None:
            self.log.append("authority.close")
        if self.close_failures:
            self.close_failures -= 1
            raise fixture.RuntimeDiagnosticError("diagnostic_cleanup_failed")
        self.inventory = None


class _FakeAuthorityLeaseFactory:
    def __init__(
        self,
        *,
        fail_on_reprove: int | None = None,
        close_failures: int = 0,
        log: list[str] | None = None,
    ) -> None:
        self.fail_on_reprove = fail_on_reprove
        self.close_failures = close_failures
        self.log = log
        self.leases: list[_FakeAuthorityLease] = []

    def __call__(self) -> _FakeAuthorityLease:
        lease = _FakeAuthorityLease(
            fail_on_reprove=self.fail_on_reprove,
            close_failures=self.close_failures,
            log=self.log,
        )
        self.leases.append(lease)
        return lease


def _put_u16(body: bytearray, offset: int, value: int) -> None:
    body[offset : offset + 2] = value.to_bytes(2, "little")


def _put_u32(body: bytearray, offset: int, value: int) -> None:
    body[offset : offset + 4] = value.to_bytes(4, "little")


def _minimal_pe(
    image_name: str,
    *,
    normal: tuple[str, ...] = (),
    delay: tuple[str, ...] = (),
    delay_attributes: int = 1,
    section_characteristics: int = 0x40000040,
) -> bytes:
    body = bytearray(0x1000)
    pe = 0x80
    coff = pe + 4
    optional = coff + 20
    section = optional + 0xF0
    body[0:2] = b"MZ"
    _put_u32(body, 0x3C, pe)
    body[pe : pe + 4] = b"PE\0\0"
    _put_u16(body, coff, 0x8664)
    _put_u16(body, coff + 2, 1)
    _put_u16(body, coff + 16, 0xF0)
    characteristics = 0x0002 | (0x2000 if image_name.endswith(".dll") else 0)
    _put_u16(body, coff + 18, characteristics)
    _put_u16(body, optional, 0x20B)
    _put_u32(body, optional + 32, 0x1000)
    _put_u32(body, optional + 36, 0x200)
    _put_u32(body, optional + 56, 0x2000)
    _put_u32(body, optional + 60, 0x200)
    _put_u32(body, optional + 108, 16)
    body[section : section + 8] = b".rdata\0\0"
    _put_u32(body, section + 8, 0xE00)
    _put_u32(body, section + 12, 0x1000)
    _put_u32(body, section + 16, 0xE00)
    _put_u32(body, section + 20, 0x200)
    _put_u32(body, section + 36, section_characteristics)

    def raw(rva: int) -> int:
        return 0x200 + rva - 0x1000

    name_rva = 0x1300
    data_rva = 0x1700

    def store_name(name: str) -> int:
        nonlocal name_rva
        encoded = name.encode("ascii", "strict") + b"\0"
        current = name_rva
        body[raw(current) : raw(current) + len(encoded)] = encoded
        name_rva += len(encoded)
        return current

    if normal:
        table_rva = 0x1000
        table_size = (len(normal) + 1) * 20
        _put_u32(body, optional + 120, table_rva)
        _put_u32(body, optional + 124, table_size)
        for index, name in enumerate(normal):
            descriptor = raw(table_rva) + index * 20
            _put_u32(body, descriptor, data_rva)
            _put_u32(body, descriptor + 4, 0x12345678)
            _put_u32(body, descriptor + 8, 7)
            _put_u32(body, descriptor + 12, store_name(name))
            _put_u32(body, descriptor + 16, data_rva + 8)
            data_rva += 0x20
    if delay:
        table_rva = 0x1100
        table_size = (len(delay) + 1) * 32
        _put_u32(body, optional + 216, table_rva)
        _put_u32(body, optional + 220, table_size)
        for index, name in enumerate(delay):
            descriptor = raw(table_rva) + index * 32
            values = (
                delay_attributes,
                store_name(name),
                data_rva,
                data_rva + 8,
                data_rva + 16,
                data_rva + 24,
                data_rva + 32,
                0x12345678,
            )
            for field_index, value in enumerate(values):
                _put_u32(body, descriptor + field_index * 4, value)
            data_rva += 0x40
    return bytes(body)


def _capture_mirror(
    root: Path, images: dict[str, bytes]
) -> tuple[fixture.RuntimeCapture, fixture.RuntimeMirror]:
    entries: list[fixture.RuntimeEntry] = []
    for name in sorted(images):
        path = root / name
        path.write_bytes(images[name])
        entries.append(
            fixture.RuntimeEntry(
                name,
                len(images[name]),
                hashlib.sha256(images[name]).hexdigest(),
                path,
            )
        )
    capture = fixture.RuntimeCapture(
        (3, 14, 0),
        tuple(entries),
        _RUNTIME_DIGEST,
        1,
        sum(item.size_bytes for item in entries),
        root,
    )
    mirror = fixture.RuntimeMirror(
        tuple(entries),
        capture.runtime_digest,
        capture.canonical_size,
        capture.total_bytes,
        root,
        root / "python.exe",
    )
    return capture, mirror


class _FakeApiQuery:
    def __init__(
        self,
        mappings: dict[str, str],
        *,
        implemented: bool = True,
        size_hresult: int = 0x8007007A,
        fill_hresult: int = 0,
        actual_delta: int = 0,
        terminate: bool = True,
        drift_hosts: dict[str, str] | None = None,
    ) -> None:
        self.mappings = mappings
        self.implemented = implemented
        self.size_hresult = size_hresult
        self.fill_hresult = fill_hresult
        self.actual_delta = actual_delta
        self.terminate = terminate
        self.drift_hosts = drift_hosts or {}
        self.fill_calls = 0
        self.calls: list[tuple[str, bytes, int]] = []

    def IsApiSetImplemented(self, query: bytes) -> int:
        self.calls.append(("implemented", query, 0))
        return int(self.implemented and query.decode("ascii") + ".dll" in self.mappings)

    def GetApiSetModuleBaseName(
        self, query: bytes, length: int, output: object, actual: object
    ) -> int:
        contract = query.decode("ascii") + ".dll"
        host = self.mappings[contract]
        if output is not None and self.fill_calls >= len(self.mappings):
            host = self.drift_hosts.get(contract, host)
        required = len(host.encode("utf-16-le")) // 2 + 1
        pointer = ctypes.cast(actual, ctypes.POINTER(ctypes.c_uint32))
        pointer[0] = required + (self.actual_delta if output is not None else 0)
        self.calls.append(("mapping", query, length))
        if output is None:
            return self.size_hresult
        for index, character in enumerate(host):
            output[index] = character
        if self.terminate:
            output[required - 1] = "\0"
        self.fill_calls += 1
        return self.fill_hresult


def _held_preflight_authorities(
    *,
    api_qualification: fixture.ApiSetQualification,
    known_qualification: fixture.KnownDllQualification,
    system_images: tuple[str, ...],
) -> tuple[fixture._ApiSetApiLease, fixture._KnownDllRegistryLease]:
    query = _FakeApiQuery(
        {
            relation.contract: relation.host
            for relation in api_qualification.relations
        }
    )
    api = fixture._ApiSetApiLease()
    api._module = 0x42
    api.IsApiSetImplemented = query.IsApiSetImplemented
    api.GetApiSetModuleBaseName = query.GetApiSetModuleBaseName
    known = fixture._KnownDllRegistryLease()
    known._key = object()
    known._winreg = object()
    known._system_images = system_images
    known.qualification = lambda: known_qualification
    return api, known


class _FakeAliasLease(fixture._AliasLease):
    def __init__(
        self,
        path: Path,
        identity_number: int,
        *,
        close_failures: int = 0,
        reprove_fails: bool = False,
    ) -> None:
        value = str(path).replace("/", "\\")
        tail = value[3:] if len(value) > 3 else "root"
        self.aliases = (
            "\\\\?\\" + value,
            "\\Device\\HarddiskVolume9\\" + tail,
        )
        self.file_identity = (
            9,
            identity_number.to_bytes(16, "little"),
        )
        self.close_failures = close_failures
        self.reprove_fails = reprove_fails
        self.reprove_calls = 0
        self.close_calls = 0
        self.closed = False

    def reprove(self) -> None:
        self.reprove_calls += 1
        if self.closed or self.reprove_calls != 1 or self.reprove_fails:
            raise fixture.RuntimeDiagnosticError("diagnostic_collector_failed")

    def close(self) -> None:
        self.close_calls += 1
        if self.close_failures:
            self.close_failures -= 1
            raise fixture.RuntimeDiagnosticError("diagnostic_cleanup_failed")
        self.aliases = ("", "")
        self.file_identity = (0, b"")
        self.closed = True


class _FakeAliasLeaseFactory:
    def __init__(
        self,
        *,
        first_close_failures: int = 0,
        first_reprove_fails: bool = False,
    ) -> None:
        self.first_close_failures = first_close_failures
        self.first_reprove_fails = first_reprove_fails
        self.leases: list[_FakeAliasLease] = []
        self.bindings: list[tuple[Path, _FakeAliasLease]] = []

    def __call__(
        self, path: Path, *, is_directory: bool
    ) -> _FakeAliasLease:
        self.assert_directory_shape(path, is_directory)
        lease = _FakeAliasLease(
            path,
            len(self.leases) + 1,
            close_failures=(
                self.first_close_failures if not self.leases else 0
            ),
            reprove_fails=self.first_reprove_fails and not self.leases,
        )
        self.leases.append(lease)
        self.bindings.append((path, lease))
        return lease

    @staticmethod
    def assert_directory_shape(path: Path, is_directory: bool) -> None:
        expected_directory = path.suffix == "" and path.name not in {
            "python.exe",
        }
        if path.name.casefold().endswith((".dll", ".exe", ".py")):
            expected_directory = False
        if is_directory is not expected_directory:
            raise AssertionError("directory binding drift")


class _FakeChild:
    def __init__(self) -> None:
        self.process_id = 37
        self.process = lpac.OwnedHandle(777)
        self.thread = lpac.OwnedHandle(778)


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
        self.inventory = _build_inventory(capture)
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
        thread_handle: object,
    ) -> None:
        self._step("collector.start")
        if not application.is_absolute():
            raise AssertionError("application must be exact and absolute")
        self.inventory = inventory
        if process_id != 37 or process_handle is None or thread_handle is None:
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

    def test_preflight_builder_error_close_retry_releases_both_and_dominates(self) -> None:
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
        provenance = fixture._bind_runtime_import_provenance(
            capture,
            (
                fixture.RuntimeImportRelation(
                    "python.exe", "normal", "python314.dll"
                ),
            ),
        )
        system_images = ("kernel32.dll", "ntdll.dll")
        known = fixture._bind_known_dll_qualification(
            system_images,
            (0, 2, 7),
            (
                ("kernel32", "kernel32.dll", 1),
                ("ntdll", "ntdll.dll", 1),
            ),
        )

        class Profile:
            sid = "S-1-15-2-999"
            created = False
            deleted = False

            def create(self) -> None:
                self.created = True

            def delete(self) -> None:
                self.created = False
                self.deleted = True

            def is_absent(self) -> bool:
                return self.deleted

        class ApiOwner(_FakeApiQuery):
            def __init__(self) -> None:
                super().__init__({})
                self.opened = False
                self.close_calls = 0

            def open(self) -> None:
                self.opened = True

            def close(self) -> None:
                self.close_calls += 1
                if self.close_calls == 1:
                    raise OSError("PRIVATE_API_CLOSE_CANARY")
                self.opened = False

        class KnownOwner:
            def __init__(self) -> None:
                self.opened = False
                self.close_calls = 0

            def open(self, images: tuple[str, ...]) -> None:
                if images != system_images:
                    raise AssertionError("unexpected KnownDLL image set")
                self.opened = True

            def qualification(self) -> fixture.KnownDllQualification:
                return known

            def close(self) -> None:
                self.close_calls += 1
                self.opened = False

        profile = Profile()
        api_owner = ApiOwner()
        known_owner = KnownOwner()

        def mirror_runtime(
            _capture: fixture.RuntimeCapture, root: Path
        ) -> fixture.RuntimeMirror:
            return fixture.RuntimeMirror(
                capture.entries,
                capture.runtime_digest,
                capture.canonical_size,
                capture.total_bytes,
                root,
                root / "python.exe",
            )

        def fail_builder(*_args: object, **arguments: object) -> object:
            self.assertIs(arguments["api_authority"], api_owner)
            self.assertIs(arguments["known_dll_authority"], known_owner)
            self.assertTrue(api_owner.opened)
            self.assertTrue(known_owner.opened)
            raise fixture.RuntimeDiagnosticError("diagnostic_unavailable")

        with (
            patch.object(
                fixture.portability, "_FixtureProfile", return_value=profile
            ),
            patch.object(
                fixture,
                "capture_current_cpython_runtime",
                return_value=capture,
            ),
            patch.object(
                fixture, "mirror_cpython_runtime", side_effect=mirror_runtime
            ),
            patch.object(
                fixture,
                "_seal_exact_runtime_scope",
                return_value=fixture.TreeDaclProof(1, 1, _DACL_DIGEST),
            ),
            patch.object(fixture, "prove_runtime_mirror", return_value=None),
            patch.object(
                fixture,
                "extract_runtime_import_provenance",
                return_value=provenance,
            ),
            patch.object(fixture, "_ApiSetApiLease", return_value=api_owner),
            patch.object(
                fixture, "_KnownDllRegistryLease", return_value=known_owner
            ),
            patch.object(
                fixture,
                "_verified_system_image_basenames",
                return_value=system_images,
            ),
            patch.object(
                fixture, "build_collector_inventory", side_effect=fail_builder
            ),
            self.assertRaises(fixture.RuntimeDiagnosticError) as raised,
        ):
            fixture._RealDiagnosticContext.create()
        self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
        self.assertNotIn("PRIVATE_API_CLOSE_CANARY", str(raised.exception))
        self.assertEqual(api_owner.close_calls, 2)
        self.assertEqual(known_owner.close_calls, 1)
        self.assertFalse(api_owner.opened)
        self.assertFalse(known_owner.opened)
        self.assertTrue(profile.deleted)

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
        inventory = _build_inventory(capture)
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
        kernel_contract = next(
            item
            for item in inventory.objects
            if item.loader_binding is not None
            and item.loader_binding.logical_name == "kernel32.dll"
        )
        self.assertEqual(kernel_contract.match_kind, "exact_known_dll_import")
        self.assertIsNotNone(
            inventory.resolve(
                plane="dll_image_load",
                match_kind=kernel_contract.match_kind,
                component=kernel_contract.component,
            )
        )
        self.assertIsNone(
            inventory.resolve(
                plane="dll_image_load",
                match_kind="exact_image_identity",
                component="system32-image:unknown.dll",
            )
        )

    def test_known_dll_registry_contract_is_bounded_exact_and_collision_closed(self) -> None:
        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        def module(values):
            def open_key(_hive, path, _reserved, access):
                self.assertEqual(
                    path,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs",
                )
                self.assertEqual(access, 0x1 | 0x100)
                return Key()

            return SimpleNamespace(
                KEY_QUERY_VALUE=0x1,
                KEY_WOW64_64KEY=0x100,
                HKEY_LOCAL_MACHINE=object(),
                REG_SZ=1,
                REG_EXPAND_SZ=2,
                OpenKey=open_key,
                CloseKey=lambda _key: None,
                QueryInfoKey=lambda _key: (0, len(values), 0),
                EnumValue=lambda _key, index: values[index],
            )

        values = (
            ("DllDirectory", r"%SystemRoot%\System32", 2),
            ("kernel32", "kernel32.dll", 1),
            ("ntdll", "ntdll.dll", 1),
        )
        with patch.dict(sys.modules, {"winreg": module(values)}):
            self.assertEqual(
                fixture._verified_known_dll_contracts(
                    ("kernel32.dll", "ntdll.dll")
                ).contracts,
                ("kernel32.dll", "ntdll.dll"),
            )

        duplicate = values + (("KERNEL32-copy", "KERNEL32.DLL", 1),)
        with patch.dict(sys.modules, {"winreg": module(duplicate)}):
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture._verified_known_dll_contracts(
                    ("kernel32.dll", "ntdll.dll")
                )

        oversized = module(values)
        oversized.QueryInfoKey = lambda _key: (
            0,
            fixture._KNOWN_DLL_VALUE_LIMIT + 1,
            0,
        )
        with patch.dict(sys.modules, {"winreg": oversized}):
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture._verified_known_dll_contracts(
                    ("kernel32.dll", "ntdll.dll")
                )

        wrong_type = values + (("ignored", 7, 4),)
        with patch.dict(sys.modules, {"winreg": module(wrong_type)}):
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture._verified_known_dll_contracts(
                    ("kernel32.dll", "ntdll.dll")
                )

        metadata_drift = module(values)
        query_calls = 0

        def drifting_metadata(_key):
            nonlocal query_calls
            query_calls += 1
            return (0, len(values), 1 if query_calls >= 2 else 0)

        metadata_drift.QueryInfoKey = drifting_metadata
        with patch.dict(sys.modules, {"winreg": metadata_drift}):
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture._verified_known_dll_contracts(
                    ("kernel32.dll", "ntdll.dll")
                )

        between_pass_drift = module(values)
        between_query_calls = 0

        def drifting_between_passes(_key):
            nonlocal between_query_calls
            between_query_calls += 1
            return (
                0,
                len(values),
                0 if between_query_calls <= 2 else 1,
            )

        between_pass_drift.QueryInfoKey = drifting_between_passes
        with patch.dict(sys.modules, {"winreg": between_pass_drift}):
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture._verified_known_dll_contracts(
                    ("kernel32.dll", "ntdll.dll")
                )

        value_drift = module(values)
        enum_calls = 0

        def drifting_value(_key, index):
            nonlocal enum_calls
            enum_calls += 1
            row = values[index]
            if enum_calls > len(values) and index == 1:
                return (row[0], "kernelbase.dll", row[2])
            return row

        value_drift.EnumValue = drifting_value
        with patch.dict(sys.modules, {"winreg": value_drift}):
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture._verified_known_dll_contracts(
                    ("kernel32.dll", "kernelbase.dll", "ntdll.dll")
                )

        for attribute, value in (
            ("KEY_WOW64_64KEY", None),
            ("KEY_WOW64_64KEY", 0),
            ("KEY_WOW64_64KEY", "0x100"),
            ("KEY_QUERY_VALUE", 0),
        ):
            with self.subTest(attribute=attribute, value=value):
                wrong_view = module(values)
                if value is None:
                    delattr(wrong_view, attribute)
                else:
                    setattr(wrong_view, attribute, value)
                with patch.dict(sys.modules, {"winreg": wrong_view}):
                    with self.assertRaises(fixture.RuntimeDiagnosticError):
                        fixture._verified_known_dll_contracts(
                            ("kernel32.dll", "ntdll.dll")
                        )

    def test_resolver_separates_physical_and_logical_domains_and_scrubs_leases(self) -> None:
        context = _FakeContext([])
        factory = _FakeAliasLeaseFactory()
        resolver = fixture._ExactInventoryResolver()
        resolver.initialize(
            application=context.application,
            inventory=context.inventory,
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
            alias_lease_factory=factory,
            authority_lease_factory=_FakeAuthorityLeaseFactory(),
            ordinal_equal=lambda first, second: first.casefold()
            == second.casefold(),
        )
        initial = context.inventory.resolve(
            plane="dll_image_load",
            match_kind="exact_image_identity",
            component="runtime-image:python.exe",
        )
        dependency_object = next(
            item
            for item in context.inventory.objects
            if item.loader_binding is not None
            and item.loader_binding.logical_name == "python314.dll"
        )
        dependency = dependency_object.object_ref
        self.assertEqual(
            dependency_object.match_kind, "exact_static_pe_import"
        )
        application_lease = next(
            lease
            for path, lease in factory.bindings
            if path == context.application
        )
        for alias in application_lease.aliases:
            self.assertEqual(
                resolver("dll_image_load", alias.swapcase()), initial
            )
        self.assertEqual(
            resolver.resolve_loader_dependency("PYTHON314.DLL"), dependency
        )
        self.assertIsNone(
            resolver("dll_image_load", "python314.dll")
        )
        self.assertIsNone(
            resolver.resolve_loader_dependency(
                "C:/private/runtime/python314.dll"
            )
        )
        self.assertIsNone(
            resolver.resolve_loader_dependency(
                "api-ms-win-crt-runtime-l1-1-0.dll"
            )
        )
        self.assertIsNone(
            resolver.resolve_loader_dependency(
                "ext-ms-win-session-wtsapi32-l1-1-0.dll"
            )
        )
        self.assertFalse(
            any(
                "api-ms-" in item.component or "ext-ms-" in item.component
                for item in context.inventory.objects
            )
        )
        self.assertIsNone(
            resolver(
                "dll_image_load",
                r"\Device\HarddiskVolume9\runtime\..\python.exe",
            )
        )
        resolver.close()
        self.assertTrue(all(lease.closed for lease in factory.leases))
        self.assertFalse(resolver._paths["dll_image_load"])
        self.assertFalse(resolver._loader_dependencies)
        self.assertIsNone(
            resolver.resolve_loader_dependency("python314.dll")
        )

    def test_dependency_origin_collision_and_plane_cap_fail_closed(self) -> None:
        entries = (
            fixture.RuntimeEntry(
                "python.exe", 1, "1" * 64, Path("C:/source/python.exe")
            ),
            fixture.RuntimeEntry(
                "python314.dll",
                1,
                "2" * 64,
                Path("C:/source/python314.dll"),
            ),
        )
        capture = fixture.RuntimeCapture(
            (3, 14, 0), entries, _RUNTIME_DIGEST, 1, 2, Path("C:/source")
        )
        with self.assertRaises(fixture.RuntimeDiagnosticError):
            system_images = (
                "kernel32.dll",
                "ntdll.dll",
                "python314.dll",
            )
            fixture._build_collector_inventory_from_proofs(
                capture,
                **_proof_kwargs(capture, system_images=system_images),
            )

        many_entries = list(entries)
        for index in range(40):
            many_entries.append(
                fixture.RuntimeEntry(
                    f"runtime{index:02d}.dll",
                    1,
                    f"{index + 10:064x}"[-64:],
                    Path(f"C:/source/runtime{index:02d}.dll"),
                )
            )
        oversized = fixture.RuntimeCapture(
            (3, 14, 0),
            tuple(many_entries),
            _RUNTIME_DIGEST,
            1,
            len(many_entries),
            Path("C:/source"),
        )
        with self.assertRaises(fixture.RuntimeDiagnosticError):
            _build_inventory(oversized)

        runtime_capture = fixture.RuntimeCapture(
            (3, 14, 0),
            entries
            + (
                fixture.RuntimeEntry(
                    "shared.dll",
                    1,
                    "3" * 64,
                    Path("C:/source/shared.dll"),
                ),
            ),
            _RUNTIME_DIGEST,
            1,
            3,
            Path("C:/source"),
        )
        known_capture = fixture.RuntimeCapture(
            (3, 14, 0), entries, _RUNTIME_DIGEST, 1, 2, Path("C:/source")
        )
        runtime_relations = (
            fixture.RuntimeImportRelation(
                "python.exe", "normal", "shared.dll"
            ),
        )
        runtime_inventory = fixture._build_collector_inventory_from_proofs(
            runtime_capture,
            **_proof_kwargs(runtime_capture, relations=runtime_relations),
        )
        known_system = ("kernel32.dll", "ntdll.dll", "shared.dll")
        known_inventory = fixture._build_collector_inventory_from_proofs(
            known_capture,
            **_proof_kwargs(
                known_capture,
                relations=runtime_relations,
                system_images=known_system,
                known_contracts=known_system,
            ),
        )
        runtime_object = next(
            item
            for item in runtime_inventory.objects
            if item.loader_binding is not None
            and item.loader_binding.logical_name == "shared.dll"
        )
        known_object = next(
            item
            for item in known_inventory.objects
            if item.loader_binding is not None
            and item.loader_binding.logical_name == "shared.dll"
        )
        runtime_ref = runtime_object.object_ref
        known_ref = known_object.object_ref
        self.assertEqual(runtime_object.loader_binding.origin, "pe_import")
        self.assertEqual(known_object.loader_binding.origin, "known_dll")
        self.assertIsNotNone(runtime_ref)
        self.assertIsNotNone(known_ref)
        self.assertNotEqual(runtime_ref, known_ref)
        self.assertNotEqual(
            runtime_inventory.manifest.manifest_digest,
            known_inventory.manifest.manifest_digest,
        )

    def test_resolver_constructor_fault_closes_every_acquired_lease(self) -> None:
        context = _FakeContext([])
        factory = _FakeAliasLeaseFactory()

        def malformed(path: Path, *, is_directory: bool):
            lease = factory(path, is_directory=is_directory)
            if len(factory.leases) == 2:
                lease.aliases = ("not-a-dos-path", "not-an-nt-path")
            return lease

        resolver = fixture._ExactInventoryResolver()
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            resolver.initialize(
                application=context.application,
                inventory=context.inventory,
                system32_root=Path("C:/Windows/System32"),
                current_user_sid="S-1-5-21-100",
                alias_lease_factory=malformed,
                authority_lease_factory=_FakeAuthorityLeaseFactory(),
                ordinal_equal=lambda first, second: first.casefold()
                == second.casefold(),
            )
        self.assertEqual(raised.exception.code, "diagnostic_collector_failed")
        self.assertTrue(factory.leases)
        self.assertTrue(all(lease.closed for lease in factory.leases))

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

    def test_windows_ordinal_comparison_does_not_truncate_astral_paths(self) -> None:
        calls: list[tuple[str, int, str, int, bool]] = []

        class Kernel:
            @staticmethod
            def CompareStringOrdinal(first, first_count, second, second_count, ignore_case):
                calls.append(
                    (first, first_count, second, second_count, bool(ignore_case))
                )
                return 2 if first.casefold() == second.casefold() else 1

        prefix = "dos:C:\\private\\\U0001F600"
        with patch.object(
            fixture,
            "_dacl_apis",
            return_value=SimpleNamespace(kernel32=Kernel()),
        ):
            self.assertTrue(
                fixture._windows_ordinal_equal(prefix + "A", prefix + "a")
            )
            self.assertFalse(
                fixture._windows_ordinal_equal(prefix + "A", prefix + "B")
            )
        self.assertEqual(
            tuple(
                (first_count, second_count, ignore_case)
                for _, first_count, _, second_count, ignore_case in calls
            ),
            ((-1, -1, True), (-1, -1, True)),
        )

    def test_trusted_alias_lease_binds_same_held_file_and_retries_close(self) -> None:
        class Kernel:
            def __init__(self, **faults):
                self.faults = faults
                self.file_id_calls = 0
                self.close_calls = 0
                self.create_call = None

            def CreateFileW(self, *args):
                self.create_call = args
                return 707

            def GetFileInformationByHandleEx(
                self, _handle, information_class, pointer, _size
            ):
                if information_class == fixture.FILE_ATTRIBUTE_TAG_INFO:
                    value = pointer._obj
                    value.FileAttributes = (
                        fixture.FILE_ATTRIBUTE_DIRECTORY
                        if self.faults.get("directory")
                        else 0
                    )
                    if self.faults.get("reparse"):
                        value.FileAttributes |= fixture.FILE_ATTRIBUTE_REPARSE_POINT
                        value.ReparseTag = 0xA000000C
                    return True
                if information_class == fixture.FILE_STANDARD_INFO:
                    value = pointer._obj
                    value.NumberOfLinks = (
                        0 if self.faults.get("links_zero") else 1
                    )
                    value.DeletePending = bool(
                        self.faults.get("delete_pending")
                    )
                    value.Directory = bool(self.faults.get("directory"))
                    return True
                if information_class == fixture.FILE_ID_INFO:
                    self.file_id_calls += 1
                    value = pointer._obj
                    value.VolumeSerialNumber = 9
                    identity = 2 if (
                        self.faults.get("identity_drift")
                        and self.file_id_calls == 2
                    ) else (3 if self.faults.get("window_identity_drift") else 1)
                    for index in range(16):
                        value.FileId.Identifier[index] = (
                            identity if index == 0 else 0
                        )
                    return True
                return False

            def GetHandleInformation(self, _handle, pointer):
                pointer._obj.value = (
                    lpac.HANDLE_FLAG_INHERIT
                    if self.faults.get("inherited")
                    else 0
                )
                return True

            def GetFinalPathNameByHandleW(
                self, _handle, buffer, _capacity, flags
            ):
                value = (
                    r"\\?\C:\private\runtime\python.exe"
                    if flags == fixture.VOLUME_NAME_DOS
                    else r"\Device\HarddiskVolume9\private\runtime\python.exe"
                )
                if self.faults.get("astral_path"):
                    value = value.replace("runtime", "run\U0001F600time")
                if self.faults.get("dot_path") and flags == fixture.VOLUME_NAME_NT:
                    value = r"\Device\HarddiskVolume9\private\..\python.exe"
                if self.faults.get("window_alias_drift"):
                    value = value.replace("python.exe", "replaced.exe")
                buffer.value = value
                if self.faults.get("bad_length"):
                    return len(value) + 1
                if self.faults.get("oversize"):
                    return len(buffer)
                return len(value.encode("utf-16-le", "strict")) // 2

            def CompareStringOrdinal(
                self, first, first_length, second, second_length, ignore_case
            ):
                if self.faults.get("compare_failure"):
                    return 0
                self.assert_compare = (
                    first_length,
                    second_length,
                    bool(ignore_case),
                )
                return 2 if first.casefold() == second.casefold() else 1

            def CloseHandle(self, _handle):
                self.close_calls += 1
                return self.close_calls > self.faults.get("close_failures", 0)

        def open_with(kernel, *, is_directory=False):
            with patch.object(
                fixture,
                "_dacl_apis",
                return_value=SimpleNamespace(kernel32=kernel),
            ):
                return fixture._open_trusted_alias_lease(
                    Path("C:/private/runtime/python.exe"),
                    is_directory=is_directory,
                )

        kernel = Kernel()
        lease = open_with(kernel)
        self.assertNotEqual(lease.aliases, ("", ""))
        self.assertEqual(lease.file_identity, (9, b"\x01" + b"\0" * 15))
        self.assertEqual(kernel.file_id_calls, 2)
        self.assertEqual(kernel.create_call[1], lpac.FILE_READ_ATTRIBUTES)
        self.assertEqual(
            kernel.create_call[2],
            fixture.FILE_SHARE_READ
            | fixture.FILE_SHARE_WRITE
            | fixture.FILE_SHARE_DELETE,
        )
        self.assertEqual(
            kernel.create_call[5], fixture.FILE_FLAG_OPEN_REPARSE_POINT
        )
        with patch.object(
            fixture,
            "_dacl_apis",
            return_value=SimpleNamespace(kernel32=kernel),
        ):
            lease.reprove()
            lease.close()
        self.assertEqual(kernel.file_id_calls, 4)
        self.assertEqual(kernel.assert_compare[-1], True)
        self.assertTrue(lease._handle.closed)

        for fault in (
            "reparse",
            "identity_drift",
            "inherited",
            "directory",
            "bad_length",
            "oversize",
            "dot_path",
            "delete_pending",
            "links_zero",
        ):
            with self.subTest(fault=fault):
                broken = Kernel(**{fault: True})
                invalid = open_with(broken)
                self.assertEqual(invalid.aliases, ("", ""))
                with patch.object(
                    fixture,
                    "_dacl_apis",
                    return_value=SimpleNamespace(kernel32=broken),
                ):
                    invalid.close()
                self.assertTrue(invalid._handle.closed)

        directory_kernel = Kernel(directory=True)
        directory_lease = open_with(directory_kernel, is_directory=True)
        self.assertEqual(
            directory_kernel.create_call[5],
            fixture.FILE_FLAG_OPEN_REPARSE_POINT
            | fixture.FILE_FLAG_BACKUP_SEMANTICS,
        )
        with patch.object(
            fixture,
            "_dacl_apis",
            return_value=SimpleNamespace(kernel32=directory_kernel),
        ):
            directory_lease.close()

        close_fault = Kernel(close_failures=1)
        retained = open_with(close_fault)
        with patch.object(
            fixture,
            "_dacl_apis",
            return_value=SimpleNamespace(kernel32=close_fault),
        ):
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                retained.close()
            self.assertFalse(retained._handle.closed)
            retained.close()
        self.assertTrue(retained._handle.closed)

        astral_kernel = Kernel(astral_path=True)
        astral_lease = open_with(astral_kernel)
        self.assertIn("\U0001F600", astral_lease.aliases[0])
        with patch.object(
            fixture,
            "_dacl_apis",
            return_value=SimpleNamespace(kernel32=astral_kernel),
        ):
            astral_lease.reprove()
            astral_lease.close()
        self.assertTrue(astral_lease._handle.closed)

        for fault in (
            "window_identity_drift",
            "window_alias_drift",
            "reparse",
            "delete_pending",
            "inherited",
            "compare_failure",
        ):
            with self.subTest(window_fault=fault):
                window = Kernel()
                held = open_with(window)
                window.faults[fault] = True
                with patch.object(
                    fixture,
                    "_dacl_apis",
                    return_value=SimpleNamespace(kernel32=window),
                ):
                    with self.assertRaises(fixture.RuntimeDiagnosticError):
                        held.reprove()
                    held.close()
                self.assertTrue(held._handle.closed)

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


class PeImportAndApiSetQualificationTests(unittest.TestCase):
    def _capture_with_api(self) -> tuple[
        fixture.RuntimeCapture, fixture.RuntimeImportProvenance
    ]:
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
        provenance = fixture._bind_runtime_import_provenance(
            capture,
            (
                fixture.RuntimeImportRelation(
                    "python.exe",
                    "normal",
                    "api-ms-win-core-file-l1-1-0.dll",
                ),
            ),
        )
        return capture, provenance

    def test_exact_pe_normal_and_delay_provenance_is_digest_bound(self) -> None:
        api_contract = "api-ms-win-core-file-l1-1-0.dll"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            capture, mirror = _capture_mirror(
                root,
                {
                    "python.exe": _minimal_pe(
                        "python.exe",
                        normal=("python314.dll", "kernel32.dll"),
                    ),
                    "python314.dll": _minimal_pe(
                        "python314.dll",
                        normal=("kernel32.dll",),
                        delay=(api_contract,),
                    ),
                },
            )
            provenance = fixture.extract_runtime_import_provenance(
                capture, mirror
            )
        self.assertEqual(
            {
                (item.importer, item.table, item.dependency)
                for item in provenance.relations
            },
            {
                ("python.exe", "normal", "python314.dll"),
                ("python.exe", "normal", "kernel32.dll"),
                ("python314.dll", "normal", "kernel32.dll"),
                ("python314.dll", "delay", api_contract),
            },
        )
        self.assertRegex(provenance.provenance_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("python314", repr(provenance))
        self.assertNotIn(str(root), repr(provenance))

    def test_pe_parser_rejects_malformed_headers_sections_descriptors_and_names(self) -> None:
        normal_name = "kernel32.dll"
        delay_name = "api-ms-win-core-file-l1-1-0.dll"
        pe = 0x80
        coff = pe + 4
        optional = coff + 20
        section = optional + 0xF0
        normal_descriptor = 0x200
        delay_descriptor = 0x300
        mutations: dict[str, object] = {
            "machine": lambda body: _put_u16(body, coff, 0x14C),
            "not_executable": lambda body: _put_u16(body, coff + 18, 0x2000),
            "directory_count": lambda body: _put_u32(body, optional + 108, 13),
            "small_alignment_mismatch": lambda body: (
                _put_u32(body, optional + 32, 0x800),
                _put_u32(body, optional + 36, 0x200),
            ),
            "section_overlaps_headers": lambda body: _put_u32(
                body, section + 12, 0
            ),
            "section_raw_overlaps_headers": lambda body: _put_u32(
                body, section + 20, 0
            ),
            "writable_executable": lambda body: _put_u32(
                body, section + 36, 0xE0000040
            ),
            "unreadable_import_section": lambda body: _put_u32(
                body, section + 36, 0x00000040
            ),
            "normal_name_in_headers": lambda body: _put_u32(
                body, normal_descriptor + 12, 0x100
            ),
            "normal_directory_size": lambda body: _put_u32(
                body, optional + 124, 21
            ),
            "normal_missing_sentinel": lambda body: _put_u32(
                body, normal_descriptor + 20, 1
            ),
            "delay_va_attributes": lambda body: _put_u32(
                body, delay_descriptor, 0
            ),
            "delay_unknown_attributes": lambda body: _put_u32(
                body, delay_descriptor, 2
            ),
            "delay_name_in_writable_section": lambda body: _put_u32(
                body, section + 36, 0xC0000040
            ),
            "delay_rva_outside_raw": lambda body: _put_u32(
                body, delay_descriptor + 16, 0x1FFF
            ),
        }
        base = _minimal_pe(
            "python314.dll",
            normal=(normal_name,),
            delay=(delay_name,),
        )
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                body = bytearray(base)
                mutate(body)
                with self.assertRaises(fixture.RuntimeDiagnosticError):
                    fixture._PeImportReader(
                        bytes(body),
                        image_name="python314.dll",
                        deadline=fixture.time.monotonic() + 5.0,
                    ).imports()

        non_ascii = bytearray(base)
        non_ascii[0x500] = 0x80
        with self.assertRaises(fixture.RuntimeDiagnosticError):
            fixture._PeImportReader(
                bytes(non_ascii),
                image_name="python314.dll",
                deadline=fixture.time.monotonic() + 5.0,
            ).imports()

    def test_mirror_hash_drift_and_close_failure_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            capture, mirror = _capture_mirror(
                root,
                {
                    "python.exe": _minimal_pe(
                        "python.exe", normal=("python314.dll",)
                    ),
                    "python314.dll": _minimal_pe(
                        "python314.dll", normal=("kernel32.dll",)
                    ),
                },
            )
            (root / "python.exe").write_bytes(b"drift")
            with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                fixture.extract_runtime_import_provenance(capture, mirror)
            self.assertEqual(raised.exception.code, "diagnostic_unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "python.exe"
            body = _minimal_pe("python.exe", normal=("python314.dll",))
            path.write_bytes(body)
            entry = fixture.RuntimeEntry(
                "python.exe",
                len(body),
                hashlib.sha256(body).hexdigest(),
                path,
            )
            real_close = fixture.os.close

            def close_then_fail(descriptor: int) -> None:
                real_close(descriptor)
                raise OSError("PRIVATE_CLOSE_CANARY")

            with patch.object(fixture.os, "close", side_effect=close_then_fail):
                with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                    fixture._read_mirror_entry_bytes(
                        entry, deadline=fixture.time.monotonic() + 5.0
                    )
            self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
            self.assertNotIn("PRIVATE_CLOSE_CANARY", str(raised.exception))

    def test_pe_bounds_sentinels_names_timeout_and_global_name_cap(self) -> None:
        pe = 0x80
        coff = pe + 4
        optional = coff + 20
        section = optional + 0xF0
        base = _minimal_pe(
            "python314.dll", normal=("kernel32.dll",)
        )
        mutations: dict[str, object] = {
            "optional_magic": lambda body: _put_u16(body, optional, 0x10B),
            "optional_size": lambda body: _put_u16(body, coff + 16, 0xE8),
            "mixed_zero_rva": lambda body: _put_u32(body, optional + 120, 0),
            "mixed_zero_size": lambda body: _put_u32(body, optional + 124, 0),
            "cross_raw_range": lambda body: (
                _put_u32(body, optional + 120, 0x1DF0),
                _put_u32(body, optional + 124, 40),
            ),
            "descriptor_cap": lambda body: _put_u32(
                body,
                optional + 124,
                (fixture.PE_IMPORT_DESCRIPTOR_LIMIT + 1) * 20,
            ),
            "sentinel_tail": lambda body: (
                _put_u32(body, optional + 124, 60),
                _put_u32(body, 0x228, 1),
            ),
            "non_dll_name": lambda body: body.__setitem__(
                slice(0x500, 0x50D), b"kernel32.exe\0"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                body = bytearray(base)
                mutate(body)
                with self.assertRaises(fixture.RuntimeDiagnosticError):
                    fixture._PeImportReader(
                        bytes(body),
                        image_name="python314.dll",
                        deadline=fixture.time.monotonic() + 5.0,
                    ).imports()

        duplicate = _minimal_pe(
            "python314.dll",
            normal=("kernel32.dll", "kernel32.dll"),
        )
        missing_nul = bytearray(base)
        missing_nul[0x500 : 0x500 + 129] = b"A" * 129
        overlap = bytearray(base)
        _put_u16(overlap, coff + 2, 2)
        overlap[section + 40 : section + 80] = overlap[section : section + 40]
        for name, body in (
            ("duplicate", duplicate),
            ("missing_nul", bytes(missing_nul)),
            ("section_overlap", bytes(overlap)),
        ):
            with self.subTest(name=name):
                with self.assertRaises(fixture.RuntimeDiagnosticError):
                    fixture._PeImportReader(
                        body,
                        image_name="python314.dll",
                        deadline=fixture.time.monotonic() + 5.0,
                    ).imports()

        with patch.object(fixture.time, "monotonic", return_value=10.0):
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture._PeImportReader(
                    base,
                    image_name="python314.dll",
                    deadline=9.0,
                ).imports()

        first = tuple(f"dependency{index:02d}.dll" for index in range(33))
        second = tuple(f"dependency{index:02d}.dll" for index in range(33, 65))
        with tempfile.TemporaryDirectory() as temporary:
            capture, mirror = _capture_mirror(
                Path(temporary).resolve(),
                {
                    "python.exe": _minimal_pe("python.exe", normal=first),
                    "python314.dll": _minimal_pe(
                        "python314.dll", normal=second
                    ),
                },
            )
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture.extract_runtime_import_provenance(capture, mirror)

    def test_api_query_uses_extensionless_two_stage_double_snapshot(self) -> None:
        _capture, provenance = self._capture_with_api()
        contract = "api-ms-win-core-file-l1-1-0.dll"
        api = _FakeApiQuery({contract: "kernelbase.dll"})
        qualification = fixture.qualify_api_set_contracts(
            provenance, api=api
        )
        self.assertEqual(
            qualification.relations,
            (fixture.ApiSetHostRelation(contract, "kernelbase.dll"),),
        )
        self.assertEqual(
            [item[2] for item in api.calls if item[0] == "mapping"],
            [0, len("kernelbase.dll") + 1, 0, len("kernelbase.dll") + 1],
        )
        self.assertTrue(
            all(
                item[1] == b"api-ms-win-core-file-l1-1-0"
                for item in api.calls
            )
        )
        self.assertNotIn(contract, repr(qualification))

    def test_api_query_and_semantic_binders_fail_closed_on_every_drift(self) -> None:
        _capture, provenance = self._capture_with_api()
        contract = "api-ms-win-core-file-l1-1-0.dll"
        cases = (
            _FakeApiQuery({contract: "kernelbase.dll"}, implemented=False),
            _FakeApiQuery({contract: "kernelbase.dll"}, size_hresult=0),
            _FakeApiQuery({contract: "kernelbase.dll"}, fill_hresult=1),
            _FakeApiQuery({contract: "kernelbase.dll"}, actual_delta=1),
            _FakeApiQuery({contract: "kernelbase.dll"}, terminate=False),
            _FakeApiQuery({contract: ""}),
            _FakeApiQuery({contract: "a" * 256 + ".dll"}),
            _FakeApiQuery({contract: "kernel\0base.dll"}),
            _FakeApiQuery({contract: "KERNELBASE.DLL"}),
            _FakeApiQuery({contract: contract}),
            _FakeApiQuery(
                {contract: "api-ms-win-core-synch-l1-1-0.dll"}
            ),
            _FakeApiQuery(
                {contract: "kernelbase.dll"},
                drift_hosts={contract: "kernel32.dll"},
            ),
        )
        for api in cases:
            with self.subTest(api=api):
                with self.assertRaises(fixture.RuntimeDiagnosticError):
                    fixture.qualify_api_set_contracts(provenance, api=api)

        for api in (
            SimpleNamespace(
                IsApiSetImplemented=None,
                GetApiSetModuleBaseName=lambda *_args: 0,
            ),
            SimpleNamespace(
                IsApiSetImplemented=lambda _query: (_ for _ in ()).throw(
                    OSError("PRIVATE_API_EXCEPTION")
                ),
                GetApiSetModuleBaseName=lambda *_args: 0,
            ),
        ):
            with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                fixture.qualify_api_set_contracts(provenance, api=api)
            self.assertNotIn("PRIVATE_API_EXCEPTION", str(raised.exception))

        for host in (contract, "api-ms-win-core-synch-l1-1-0.dll"):
            with self.subTest(forged_host=host):
                with self.assertRaises(fixture.RuntimeDiagnosticError):
                    fixture._bind_api_set_qualification(
                        provenance,
                        (fixture.ApiSetHostRelation(contract, host),),
                    )
        host_ref = "inventory-sha256:" + "1" * 64
        invalid_bindings = (
            dict(
                logical_name=contract,
                origin="pe_import",
                host_name=None,
                host_object_ref=None,
                authority_digest=None,
            ),
            dict(
                logical_name=contract,
                origin="known_dll",
                host_name=contract,
                host_object_ref=host_ref,
                authority_digest=_RUNTIME_DIGEST,
            ),
            dict(
                logical_name=contract,
                origin="api_set",
                host_name=contract,
                host_object_ref=host_ref,
                authority_digest=_RUNTIME_DIGEST,
            ),
            dict(
                logical_name=contract,
                origin="api_set",
                host_name="api-ms-win-core-synch-l1-1-0.dll",
                host_object_ref=host_ref,
                authority_digest=_RUNTIME_DIGEST,
            ),
            dict(
                logical_name="ordinary.dll",
                origin="pe_import",
                host_name="ordinary.dll",
                host_object_ref=host_ref,
                authority_digest=_RUNTIME_DIGEST,
            ),
            dict(
                logical_name="kernel32.dll",
                origin="known_dll",
                host_name="kernel32.dll",
                host_object_ref=None,
                authority_digest=_RUNTIME_DIGEST,
            ),
        )
        for arguments in invalid_bindings:
            with self.subTest(binding=arguments):
                with self.assertRaises(fixture.RuntimeDiagnosticError):
                    fixture._bind_loader_dependency(
                        provenance_digest=provenance.provenance_digest,
                        **arguments,
                    )
        self.assertEqual(
            fixture._api_set_contract_identity(
                "ext-ms-win-session-wtsapi32-l0-1-0.dll"
            ),
            "ext-ms-win-session-wtsapi32-l0-1-0.dll",
        )
        self.assertIsNone(
            fixture._api_set_contract_identity(
                "api-ms-win-core-file-l1-1-0-1.dll"
            )
        )
        many_relations = tuple(
            fixture.RuntimeImportRelation(
                "python.exe",
                "normal",
                f"api-test-contract{index:03d}-l1-1-0.dll",
            )
            for index in range(fixture.API_SET_CONTRACT_LIMIT + 1)
        )
        many_provenance = fixture._bind_runtime_import_provenance(
            _capture, many_relations
        )
        with self.assertRaises(fixture.RuntimeDiagnosticError):
            fixture.qualify_api_set_contracts(
                many_provenance,
                api=SimpleNamespace(
                    IsApiSetImplemented=lambda _query: 1,
                    GetApiSetModuleBaseName=lambda *_args: 0,
                ),
            )

    def test_owned_apiquery_module_uses_fixed_secure_load_and_retryable_close(self) -> None:
        factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        implemented_callback = factory(ctypes.c_int, ctypes.c_char_p)(
            lambda _contract: 1
        )
        base_callback = factory(
            ctypes.c_long,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint32),
        )(lambda _contract, _length, _output, _actual: 0)
        addresses = {
            b"IsApiSetImplemented": ctypes.cast(
                implemented_callback, ctypes.c_void_p
            ).value,
            b"GetApiSetModuleBaseName": ctypes.cast(
                base_callback, ctypes.c_void_p
            ).value,
        }

        class Kernel:
            def __init__(self) -> None:
                self.load_calls: list[tuple[object, object, int]] = []
                self.free_failures = 1
                self.free_calls = 0

            def LoadLibraryExW(self, name, reserved, flags):
                self.load_calls.append((name, reserved, flags))
                return 0x1_0000_0042

            def GetProcAddress(self, _module, name):
                return addresses.get(name, 0)

            def FreeLibrary(self, _module):
                self.free_calls += 1
                if self.free_failures:
                    self.free_failures -= 1
                    return 0
                return 1

        kernel = Kernel()
        lease = fixture._ApiSetApiLease()
        with patch.object(
            fixture,
            "_dacl_apis",
            return_value=SimpleNamespace(kernel32=kernel),
        ):
            lease.open()
            self.assertEqual(
                kernel.load_calls,
                [
                    (
                        fixture.API_SET_QUERY_DLL,
                        None,
                        fixture.LOAD_LIBRARY_SEARCH_SYSTEM32,
                    )
                ],
            )
            with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                lease.close()
            self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
            self.assertNotEqual(lease._module, 0)
            lease.close()
        self.assertEqual(kernel.free_calls, 2)
        self.assertEqual(lease._module, 0)

    def test_win32_api_prototypes_and_exact_system_directory_boundary(self) -> None:
        class Function:
            def __init__(self) -> None:
                self.argtypes = None
                self.restype = None

            def __call__(self, *_args):
                return 1

        class Library:
            def __getattr__(self, name: str) -> Function:
                value = Function()
                setattr(self, name, value)
                return value

        kernel32 = Library()
        advapi32 = Library()
        with patch.object(
            fixture.ctypes,
            "WinDLL",
            side_effect=(kernel32, advapi32),
        ):
            apis = fixture._DaclApis()
        self.assertEqual(
            apis.kernel32.LoadLibraryExW.argtypes,
            [fixture.wintypes.LPCWSTR, fixture.HANDLE, fixture.DWORD],
        )
        self.assertIs(apis.kernel32.LoadLibraryExW.restype, fixture.HANDLE)
        self.assertEqual(
            apis.kernel32.GetProcAddress.argtypes,
            [fixture.HANDLE, ctypes.c_char_p],
        )
        self.assertIs(apis.kernel32.GetProcAddress.restype, fixture.LPVOID)
        self.assertIs(apis.kernel32.FreeLibrary.restype, fixture.wintypes.BOOL)

        with tempfile.TemporaryDirectory() as temporary:
            expected = str(Path(temporary).resolve())

            class SystemKernel:
                @staticmethod
                def GetSystemDirectoryW(buffer, capacity):
                    self.assertGreater(capacity, len(expected))
                    for index, character in enumerate(expected):
                        buffer[index] = character
                    buffer[len(expected)] = "\0"
                    return len(expected.encode("utf-16-le")) // 2

            with (
                patch.object(
                    fixture,
                    "_dacl_apis",
                    return_value=SimpleNamespace(kernel32=SystemKernel()),
                ),
                patch.object(fixture.platform, "machine", return_value="AMD64"),
            ):
                self.assertEqual(fixture._system32_root(), Path(expected))

            class WrongLengthKernel(SystemKernel):
                @staticmethod
                def GetSystemDirectoryW(buffer, capacity):
                    return SystemKernel.GetSystemDirectoryW(buffer, capacity) - 1

            with (
                patch.object(
                    fixture,
                    "_dacl_apis",
                    return_value=SimpleNamespace(
                        kernel32=WrongLengthKernel()
                    ),
                ),
                patch.object(fixture.platform, "machine", return_value="AMD64"),
                self.assertRaises(fixture.RuntimeDiagnosticError),
            ):
                fixture._system32_root()

    def test_known_dll_expanded_metadata_and_close_retry_are_owned(self) -> None:
        values = (
            ("DllDirectory", r"%SystemRoot%\System32", 2),
            ("kernel32", "kernel32.dll", 1),
            ("ntdll", "ntdll.dll", 1),
        )
        close_failures = 1

        def close_key(_key):
            nonlocal close_failures
            if close_failures:
                close_failures -= 1
                raise OSError("PRIVATE_KEY_CLOSE")

        module = SimpleNamespace(
            KEY_QUERY_VALUE=0x1,
            KEY_WOW64_64KEY=0x100,
            HKEY_LOCAL_MACHINE=object(),
            REG_SZ=1,
            REG_EXPAND_SZ=2,
            OpenKey=lambda *_args: object(),
            CloseKey=close_key,
            QueryInfoKey=lambda _key: (0, len(values), 7),
            EnumValue=lambda _key, index: values[index],
        )
        lease = fixture._KnownDllRegistryLease()
        with patch.dict(sys.modules, {"winreg": module}):
            lease.open(("kernel32.dll", "ntdll.dll"))
            qualification = lease.qualification()
            self.assertEqual(
                qualification.contracts,
                ("kernel32.dll", "ntdll.dll"),
            )
            self.assertEqual(
                tuple(item.name for item in fields(qualification)),
                ("contracts", "snapshot_digest"),
            )
            self.assertNotIn("snapshot_rows", asdict(qualification))
            self.assertNotIn("DllDirectory", repr(qualification))
            self.assertNotIn("SystemRoot", repr(qualification))
            metadata_drift = fixture._bind_known_dll_qualification(
                ("kernel32.dll", "ntdll.dll"),
                (0, len(values), 8),
                tuple(sorted(values)),
            )
            self.assertEqual(metadata_drift.contracts, qualification.contracts)
            self.assertNotEqual(
                metadata_drift.snapshot_digest,
                qualification.snapshot_digest,
            )
            with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                lease.close()
            self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
            self.assertIsNotNone(lease._key)
            lease.close()
        self.assertIsNone(lease._key)

    def test_held_authority_aggregates_api_and_known_reproof_and_close(self) -> None:
        capture, provenance = self._capture_with_api()
        contract = "api-ms-win-core-file-l1-1-0.dll"
        system_images = (
            "kernel32.dll",
            "kernelbase.dll",
            "ntdll.dll",
        )
        arguments = _proof_kwargs(
            capture,
            relations=provenance.relations,
            api_relations=(
                fixture.ApiSetHostRelation(contract, "kernelbase.dll"),
            ),
            system_images=system_images,
        )
        inventory = fixture._build_collector_inventory_from_proofs(
            capture, **arguments
        )

        class ApiOwner(_FakeApiQuery):
            def __init__(self) -> None:
                super().__init__({contract: "kernelbase.dll"})
                self.open_calls = 0
                self.close_calls = 0

            def open(self) -> None:
                self.open_calls += 1

            def close(self) -> None:
                self.close_calls += 1

        class KnownOwner:
            def __init__(self) -> None:
                self.open_calls: list[tuple[str, ...]] = []
                self.qualification_calls = 0
                self.close_calls = 0

            def open(self, images: tuple[str, ...]) -> None:
                self.open_calls.append(images)

            def qualification(self) -> fixture.KnownDllQualification:
                self.qualification_calls += 1
                return inventory.known_dll_qualification

            def close(self) -> None:
                self.close_calls += 1

        api_owner = ApiOwner()
        known_owner = KnownOwner()
        held = fixture._HeldInventoryAuthorityLease()
        with (
            patch.object(
                fixture, "_ApiSetApiLease", return_value=api_owner
            ),
            patch.object(
                fixture, "_KnownDllRegistryLease", return_value=known_owner
            ),
        ):
            held.initialize(inventory)
            held.reprove()
            held.close()
        self.assertEqual(api_owner.open_calls, 1)
        self.assertEqual(api_owner.fill_calls, 4)
        self.assertEqual(api_owner.close_calls, 1)
        self.assertEqual(known_owner.open_calls, [system_images])
        self.assertEqual(known_owner.qualification_calls, 2)
        self.assertEqual(known_owner.close_calls, 1)
        self.assertIsNone(held._inventory)

    def test_api_partial_open_keeps_module_owned_until_explicit_close(self) -> None:
        factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        implemented_callback = factory(ctypes.c_int, ctypes.c_char_p)(
            lambda _contract: 1
        )
        implemented_address = ctypes.cast(
            implemented_callback, ctypes.c_void_p
        ).value

        class Kernel:
            def __init__(self) -> None:
                self.free_failures = 1
                self.free_calls = 0

            def LoadLibraryExW(self, _name, _reserved, _flags):
                return 0x1_0000_0042

            def GetProcAddress(self, _module, name):
                return (
                    implemented_address
                    if name == b"IsApiSetImplemented"
                    else 0
                )

            def FreeLibrary(self, _module):
                self.free_calls += 1
                if self.free_failures:
                    self.free_failures -= 1
                    return 0
                return 1

        kernel = Kernel()
        lease = fixture._ApiSetApiLease()
        with patch.object(
            fixture,
            "_dacl_apis",
            return_value=SimpleNamespace(kernel32=kernel),
        ):
            with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                lease.open()
            self.assertEqual(raised.exception.code, "diagnostic_unavailable")
            self.assertNotEqual(lease._module, 0)
            with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                lease.close()
            self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
            self.assertNotEqual(lease._module, 0)
            lease.close()
        self.assertEqual(kernel.free_calls, 2)

    def test_held_authority_drift_and_close_failure_aggregate_fail_closed(self) -> None:
        capture, provenance = self._capture_with_api()
        contract = "api-ms-win-core-file-l1-1-0.dll"
        system_images = (
            "kernel32.dll",
            "kernelbase.dll",
            "ntdll.dll",
        )
        inventory = fixture._build_collector_inventory_from_proofs(
            capture,
            **_proof_kwargs(
                capture,
                relations=provenance.relations,
                api_relations=(
                    fixture.ApiSetHostRelation(contract, "kernelbase.dll"),
                ),
                system_images=system_images,
            ),
        )
        metadata_drift_known = fixture._bind_known_dll_qualification(
            system_images,
            (0, 3, 8),
            tuple(
                sorted(
                    (
                        ("DllDirectory", r"%SystemRoot%\System32", 2),
                        ("kernel32", "kernel32.dll", 1),
                        ("ntdll", "ntdll.dll", 1),
                    )
                )
            ),
        )

        class ApiOwner(_FakeApiQuery):
            def __init__(self, *, close_failures: int = 0) -> None:
                super().__init__({contract: "kernelbase.dll"})
                self.close_failures = close_failures
                self.close_calls = 0

            def open(self) -> None:
                return None

            def close(self) -> None:
                self.close_calls += 1
                if self.close_failures:
                    self.close_failures -= 1
                    raise fixture.RuntimeDiagnosticError(
                        "diagnostic_cleanup_failed"
                    )

        class KnownOwner:
            def __init__(self, *, drift_on: int | None = None) -> None:
                self.drift_on = drift_on
                self.calls = 0
                self.close_calls = 0

            def open(self, _images: tuple[str, ...]) -> None:
                return None

            def qualification(self) -> fixture.KnownDllQualification:
                self.calls += 1
                if self.calls == self.drift_on:
                    return metadata_drift_known
                return inventory.known_dll_qualification

            def close(self) -> None:
                self.close_calls += 1

        drift_api = ApiOwner()
        drift_known = KnownOwner(drift_on=2)
        held = fixture._HeldInventoryAuthorityLease()
        with (
            patch.object(fixture, "_ApiSetApiLease", return_value=drift_api),
            patch.object(
                fixture, "_KnownDllRegistryLease", return_value=drift_known
            ),
        ):
            held.initialize(inventory)
            with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                held.reprove()
            self.assertEqual(raised.exception.code, "diagnostic_collector_failed")
            held.close()

        close_api = ApiOwner(close_failures=1)
        close_known = KnownOwner()
        held = fixture._HeldInventoryAuthorityLease()
        with (
            patch.object(fixture, "_ApiSetApiLease", return_value=close_api),
            patch.object(
                fixture, "_KnownDllRegistryLease", return_value=close_known
            ),
        ):
            held.initialize(inventory)
            with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                held.close()
            self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
            self.assertIsNotNone(held._api)
            self.assertIsNone(held._known)
            self.assertEqual(close_known.close_calls, 1)
            held.close()
        self.assertEqual(close_api.close_calls, 2)
        self.assertIsNone(held._inventory)

    def test_safe_builder_rederives_provenance_and_binds_api_host_object(self) -> None:
        contract = "api-ms-win-core-file-l1-1-0.dll"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            capture, mirror = _capture_mirror(
                root,
                {
                    "python.exe": _minimal_pe(
                        "python.exe", normal=("python314.dll",)
                    ),
                    "python314.dll": _minimal_pe(
                        "python314.dll", delay=(contract,)
                    ),
                },
            )
            provenance = fixture.extract_runtime_import_provenance(
                capture, mirror
            )
            api = fixture._bind_api_set_qualification(
                provenance,
                (fixture.ApiSetHostRelation(contract, "kernelbase.dll"),),
            )
            system_images = (
                "kernel32.dll",
                "kernelbase.dll",
                "ntdll.dll",
            )
            known = fixture._bind_known_dll_qualification(
                system_images,
                (0, 3, 7),
                tuple(
                    sorted(
                        (
                            ("DllDirectory", r"%SystemRoot%\System32", 2),
                            ("kernel32", "kernel32.dll", 1),
                            ("ntdll", "ntdll.dll", 1),
                        )
                    )
                ),
            )
            api_owner, known_owner = _held_preflight_authorities(
                api_qualification=api,
                known_qualification=known,
                system_images=system_images,
            )
            inventory = fixture.build_collector_inventory(
                capture,
                mirror,
                import_provenance=provenance,
                api_set_qualification=api,
                known_dll_qualification=known,
                system_images=system_images,
                api_authority=api_owner,
                known_dll_authority=known_owner,
            )
            api_object = next(
                item
                for item in inventory.objects
                if item.loader_binding is not None
                and item.loader_binding.logical_name == contract
            )
            host_object = inventory.resolve(
                plane="dll_image_load",
                match_kind="exact_image_identity",
                component="system32-image:kernelbase.dll",
            )
            self.assertEqual(api_object.match_kind, "exact_api_set_import")
            self.assertEqual(api_object.loader_binding.host_object_ref, host_object)

            alternate_api = fixture._bind_api_set_qualification(
                provenance,
                (fixture.ApiSetHostRelation(contract, "kernel32.dll"),),
            )
            alternate_inventory = (
                fixture._build_collector_inventory_from_proofs(
                    capture,
                    import_provenance=provenance,
                    api_set_qualification=alternate_api,
                    known_dll_qualification=known,
                    system_images=system_images,
                )
            )
            alternate_object = next(
                item
                for item in alternate_inventory.objects
                if item.loader_binding is not None
                and item.loader_binding.logical_name == contract
            )
            self.assertNotEqual(api_object.object_ref, alternate_object.object_ref)
            self.assertNotEqual(
                inventory.manifest.manifest_digest,
                alternate_inventory.manifest.manifest_digest,
            )

            tampered_binding = fixture._bind_loader_dependency(
                logical_name=api_object.loader_binding.logical_name,
                origin=api_object.loader_binding.origin,
                host_name=api_object.loader_binding.host_name,
                host_object_ref="inventory-sha256:" + "f" * 64,
                provenance_digest=api_object.loader_binding.provenance_digest,
                authority_digest=api_object.loader_binding.authority_digest,
            )
            tampered_object = replace(
                api_object,
                component=fixture._loader_component(tampered_binding),
                loader_binding=tampered_binding,
            )
            tampered_inventory = replace(
                inventory,
                objects=tuple(
                    tampered_object if item is api_object else item
                    for item in inventory.objects
                ),
            )
            resolver = fixture._ExactInventoryResolver()
            aliases = _FakeAliasLeaseFactory()
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                resolver.initialize(
                    application=Path("C:/private/runtime/python.exe"),
                    inventory=tampered_inventory,
                    system32_root=Path("C:/Windows/System32"),
                    current_user_sid="S-1-5-21-100",
                    alias_lease_factory=aliases,
                    authority_lease_factory=_FakeAuthorityLeaseFactory(),
                    ordinal_equal=lambda first, second: first.casefold()
                    == second.casefold(),
                )
            self.assertTrue(all(item.closed for item in aliases.leases))

            forged = fixture._bind_runtime_import_provenance(
                capture,
                provenance.relations
                + (
                    fixture.RuntimeImportRelation(
                        "python.exe", "normal", "dynamic-only.dll"
                    ),
                ),
            )
            forged_api = fixture._bind_api_set_qualification(
                forged,
                (fixture.ApiSetHostRelation(contract, "kernelbase.dll"),),
            )
            with self.assertRaises(fixture.RuntimeDiagnosticError):
                fixture.build_collector_inventory(
                    capture,
                    mirror,
                    import_provenance=forged,
                    api_set_qualification=forged_api,
                    known_dll_qualification=known,
                    system_images=system_images,
                    api_authority=api_owner,
                    known_dll_authority=known_owner,
                )

            alternate_known = fixture._bind_known_dll_qualification(
                system_images,
                (0, 4, 8),
                tuple(
                    sorted(
                        (
                            ("DllDirectory", r"%SystemRoot%\System32", 2),
                            ("kernel32", "kernel32.dll", 1),
                            ("kernelbase", "kernelbase.dll", 1),
                            ("ntdll", "ntdll.dll", 1),
                        )
                    )
                ),
            )
            for label, candidate_api, candidate_known in (
                ("api", alternate_api, known),
                ("known", api, alternate_known),
            ):
                with self.subTest(forged_authority=label):
                    with self.assertRaises(
                        fixture.RuntimeDiagnosticError
                    ) as raised:
                        fixture.build_collector_inventory(
                            capture,
                            mirror,
                            import_provenance=provenance,
                            api_set_qualification=candidate_api,
                            known_dll_qualification=candidate_known,
                            system_images=system_images,
                            api_authority=api_owner,
                            known_dll_authority=known_owner,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "diagnostic_boundary_violation",
                    )
            retained = json.dumps(asdict(inventory), sort_keys=True)
            self.assertNotIn("snapshot_rows", retained)
            self.assertNotIn("DllDirectory", retained)
            self.assertNotIn("SystemRoot", retained)
        self.assertNotIn(contract, repr(inventory))
        self.assertNotIn(str(root), repr(inventory))

    def test_static_import_cap_and_unknown_dynamic_name_fail_closed(self) -> None:
        capture, _provenance = self._capture_with_api()
        relations = tuple(
            fixture.RuntimeImportRelation(
                "python.exe", "normal", f"dependency{index:02d}.dll"
            )
            for index in range(61)
        )
        provenance = fixture._bind_runtime_import_provenance(capture, relations)
        arguments = _proof_kwargs(capture, relations=relations)
        with self.assertRaises(fixture.RuntimeDiagnosticError):
            fixture._build_collector_inventory_from_proofs(
                capture, **arguments
            )

        context = _FakeContext([])
        resolver = fixture._ExactInventoryResolver()
        aliases = _FakeAliasLeaseFactory()
        resolver.initialize(
            application=context.application,
            inventory=context.inventory,
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
            alias_lease_factory=aliases,
            authority_lease_factory=_FakeAuthorityLeaseFactory(),
            ordinal_equal=lambda first, second: first.casefold()
            == second.casefold(),
        )
        self.assertIsNone(
            resolver.resolve_loader_dependency("dynamic-only.dll")
        )
        resolver.close()


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
                thread_handle: int,
                initial_image_object_ref: str,
            ) -> None:
                log.append("trace.start")
                test_case.assertEqual(process_id, 37)
                test_case.assertEqual(process_handle, 777)
                test_case.assertEqual(thread_handle, 778)
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

        aliases = _FakeAliasLeaseFactory()
        adapter = fixture._RealtimeCollectorAdapter(
            collector_factory=factory,
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
            alias_lease_factory=aliases,
            authority_lease_factory=_FakeAuthorityLeaseFactory(),
            ordinal_equal=lambda first, second: first.casefold()
            == second.casefold(),
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
        self.assertTrue(aliases.leases)
        self.assertTrue(all(lease.reprove_calls == 1 for lease in aliases.leases))
        self.assertTrue(all(lease.closed for lease in aliases.leases))
        self.assertIsNone(
            bindings[0].resolve("dll_image_load", str(context.application))
        )

    def test_authority_reproof_is_post_stop_and_cleanup_owned(self) -> None:
        log: list[str] = []
        context = _FakeContext(log)
        context.child.process = lpac.OwnedHandle(777)
        authorities = _FakeAuthorityLeaseFactory(log=log)
        adapter = fixture._RealtimeCollectorAdapter(
            collector_factory=self._factory(
                log=log,
                inventory=context.inventory,
                application=context.application,
            ),
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
            alias_lease_factory=_FakeAliasLeaseFactory(),
            authority_lease_factory=authorities,
            ordinal_equal=lambda first, second: first.casefold()
            == second.casefold(),
        )
        fixture._execute_diagnostic(context, adapter)
        self.assertEqual(len(authorities.leases), 1)
        authority = authorities.leases[0]
        self.assertEqual(authority.reprove_calls, 2)
        self.assertEqual(authority.close_calls, 1)
        self.assertIsNone(authority.inventory)
        reproof_indices = [
            index
            for index, value in enumerate(log)
            if value == "authority.reprove"
        ]
        self.assertEqual(len(reproof_indices), 2)
        self.assertLess(log.index("trace.stop"), reproof_indices[1])
        self.assertLess(reproof_indices[1], log.index("context.finish"))

    def test_authority_end_drift_discards_classification_and_close_retry_dominates(self) -> None:
        for fail_on_reprove, close_failures, expected in (
            (2, 0, "diagnostic_collector_failed"),
            (None, 1, "diagnostic_cleanup_failed"),
        ):
            with self.subTest(
                fail_on_reprove=fail_on_reprove,
                close_failures=close_failures,
            ):
                log: list[str] = []
                context = _FakeContext(log)
                context.child.process = lpac.OwnedHandle(777)
                authorities = _FakeAuthorityLeaseFactory(
                    fail_on_reprove=fail_on_reprove,
                    close_failures=close_failures,
                    log=log,
                )
                adapter = fixture._RealtimeCollectorAdapter(
                    collector_factory=self._factory(
                        log=log,
                        inventory=context.inventory,
                        application=context.application,
                    ),
                    system32_root=Path("C:/Windows/System32"),
                    current_user_sid="S-1-5-21-100",
                    alias_lease_factory=_FakeAliasLeaseFactory(),
                    authority_lease_factory=authorities,
                    ordinal_equal=lambda first, second: first.casefold()
                    == second.casefold(),
                )
                with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                    fixture._execute_diagnostic(context, adapter)
                self.assertEqual(raised.exception.code, expected)
                authority = authorities.leases[0]
                self.assertEqual(authority.reprove_calls, 2)
                self.assertEqual(
                    authority.close_calls, 2 if close_failures else 1
                )
                self.assertIsNone(authority.inventory)
                self.assertIn("trace.stop", log)
                self.assertIn("trace.abort", log)
                self.assertNotIn("context.finish", log)

    def test_adapter_discards_classification_when_end_window_reproof_fails(self) -> None:
        log: list[str] = []
        context = _FakeContext(log)
        context.child.process = lpac.OwnedHandle(777)
        aliases = _FakeAliasLeaseFactory(first_reprove_fails=True)
        adapter = fixture._RealtimeCollectorAdapter(
            collector_factory=self._factory(
                log=log,
                inventory=context.inventory,
                application=context.application,
            ),
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
            alias_lease_factory=aliases,
            authority_lease_factory=_FakeAuthorityLeaseFactory(),
            ordinal_equal=lambda first, second: first.casefold()
            == second.casefold(),
        )
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            fixture._execute_diagnostic(context, adapter)
        self.assertEqual(raised.exception.code, "diagnostic_collector_failed")
        self.assertIn("trace.stop", log)
        self.assertNotIn("context.finish", log)
        self.assertEqual(log.count("trace.abort"), 1)
        self.assertTrue(all(lease.reprove_calls == 1 for lease in aliases.leases))
        self.assertTrue(all(lease.closed for lease in aliases.leases))

    def test_adapter_start_failure_calls_owned_abort_and_never_resumes(self) -> None:
        log: list[str] = []
        context = _FakeContext(log)
        context.child.process = lpac.OwnedHandle(777)
        aliases = _FakeAliasLeaseFactory()
        adapter = fixture._RealtimeCollectorAdapter(
            collector_factory=self._factory(
                log=log,
                inventory=context.inventory,
                application=context.application,
                fail_start=True,
            ),
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
            alias_lease_factory=aliases,
            authority_lease_factory=_FakeAuthorityLeaseFactory(),
            ordinal_equal=lambda first, second: first.casefold()
            == second.casefold(),
        )
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            fixture._execute_diagnostic(context, adapter)
        self.assertEqual(raised.exception.code, "diagnostic_collector_failed")
        self.assertIn("trace.abort", log)
        self.assertNotIn("context.resume", log)
        self.assertTrue(all(lease.closed for lease in aliases.leases))

    def test_adapter_factory_and_binding_faults_close_alias_ownership(self) -> None:
        for fault in ("factory", "binding"):
            with self.subTest(fault=fault):
                log: list[str] = []
                context = _FakeContext(log)
                context.child.process = lpac.OwnedHandle(777)
                aliases = _FakeAliasLeaseFactory()

                def failing_factory(_binding):
                    raise RuntimeError("raw factory detail")

                adapter = fixture._RealtimeCollectorAdapter(
                    collector_factory=(
                        failing_factory
                        if fault == "factory"
                        else self._factory(
                            log=log,
                            inventory=context.inventory,
                            application=context.application,
                        )
                    ),
                    system32_root=Path("C:/Windows/System32"),
                    current_user_sid="S-1-5-21-100",
                    alias_lease_factory=aliases,
                    authority_lease_factory=_FakeAuthorityLeaseFactory(),
                    ordinal_equal=lambda first, second: first.casefold()
                    == second.casefold(),
                )
                binding_patch = (
                    patch.object(
                        runtime_trace,
                        "InventoryBinding",
                        side_effect=runtime_trace.RuntimeTraceError(
                            "trace_binding_invalid"
                        ),
                    )
                    if fault == "binding"
                    else patch.object(
                        runtime_trace,
                        "InventoryBinding",
                        wraps=runtime_trace.InventoryBinding,
                    )
                )
                with binding_patch:
                    with self.assertRaises(fixture.RuntimeDiagnosticError):
                        fixture._execute_diagnostic(context, adapter)
                self.assertNotIn("context.resume", log)
                self.assertTrue(all(lease.closed for lease in aliases.leases))

    def test_adapter_abort_retries_initialize_fault_close_on_same_lease(self) -> None:
        log: list[str] = []
        context = _FakeContext(log)
        context.child.process = lpac.OwnedHandle(777)
        aliases = _FakeAliasLeaseFactory(first_close_failures=1)

        def malformed(path: Path, *, is_directory: bool):
            lease = aliases(path, is_directory=is_directory)
            if len(aliases.leases) == 2:
                lease.aliases = ("not-a-dos-path", "not-an-nt-path")
            return lease

        adapter = fixture._RealtimeCollectorAdapter(
            collector_factory=self._factory(
                log=log,
                inventory=context.inventory,
                application=context.application,
            ),
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
            alias_lease_factory=malformed,
            authority_lease_factory=_FakeAuthorityLeaseFactory(),
            ordinal_equal=lambda first, second: first.casefold()
            == second.casefold(),
        )
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            fixture._execute_diagnostic(context, adapter)
        self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
        self.assertNotIn("trace.factory", log)
        self.assertNotIn("context.resume", log)
        self.assertEqual(aliases.leases[0].close_calls, 2)
        self.assertTrue(all(lease.closed for lease in aliases.leases))
        self.assertIsNone(adapter._resolver)
        self.assertTrue(adapter._terminal)

    def test_adapter_abort_retries_retained_alias_close_once(self) -> None:
        log: list[str] = []
        context = _FakeContext(log)
        context.child.process = lpac.OwnedHandle(777)
        aliases = _FakeAliasLeaseFactory(first_close_failures=1)
        adapter = fixture._RealtimeCollectorAdapter(
            collector_factory=self._factory(
                log=log,
                inventory=context.inventory,
                application=context.application,
            ),
            system32_root=Path("C:/Windows/System32"),
            current_user_sid="S-1-5-21-100",
            alias_lease_factory=aliases,
            authority_lease_factory=_FakeAuthorityLeaseFactory(),
            ordinal_equal=lambda first, second: first.casefold()
            == second.casefold(),
        )
        adapter.start_for_suspended_child(
            application=context.application,
            inventory=context.inventory,
            process_id=37,
            process_handle=context.child.process,
            thread_handle=context.child.thread,
        )
        with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
            adapter.abort()
        self.assertEqual(raised.exception.code, "diagnostic_cleanup_failed")
        self.assertIsNotNone(adapter._resolver)
        self.assertFalse(adapter._terminal)
        adapter.abort()
        self.assertIsNone(adapter._resolver)
        self.assertTrue(adapter._terminal)
        self.assertTrue(all(lease.closed for lease in aliases.leases))

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
                    alias_lease_factory=_FakeAliasLeaseFactory(),
                    authority_lease_factory=_FakeAuthorityLeaseFactory(),
                    ordinal_equal=lambda first, second: first.casefold()
                    == second.casefold(),
                )
                with self.assertRaises(fixture.RuntimeDiagnosticError) as raised:
                    fixture._execute_diagnostic(context, adapter)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(
                    log.count("trace.abort"),
                    2 if options.get("abort_fails") else 1,
                )
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
