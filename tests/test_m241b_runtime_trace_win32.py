from __future__ import annotations

import ctypes
import inspect
import io
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tests import m241b_runtime_trace_win32 as trace


_DEPENDENCY_REF = "inventory-sha256:" + "5" * 64
_OTHER_IMAGE_REF = "inventory-sha256:" + "6" * 64
_FILE_OTHER_REF = "inventory-sha256:" + "7" * 64
_REGISTRY_OTHER_REF = "inventory-sha256:" + "8" * 64
_OBJECTS = {
    "file_access": ("inventory-sha256:" + "1" * 64,),
    "dll_image_load": (
        "inventory-sha256:" + "2" * 64,
        _DEPENDENCY_REF,
    ),
    "registry_access": ("inventory-sha256:" + "3" * 64,),
    "code_integrity_policy": ("inventory-sha256:" + "4" * 64,),
}
_DIGEST = "sha256:" + "a" * 64
_PROVIDER_BINDINGS = {
    "dll_image_load": "provider-sha256:" + "b" * 64,
    "code_integrity_policy": "provider-sha256:" + "c" * 64,
}


def _binding(*, resolver=None, dependency_resolver=None) -> trace.InventoryBinding:
    if resolver is None:
        resolver = lambda plane, _raw: _OBJECTS[plane][0]
    if dependency_resolver is None:
        dependency_resolver = lambda _raw: _DEPENDENCY_REF
    return trace.InventoryBinding(
        runtime_digest=_DIGEST,
        objects_by_plane=dict(_OBJECTS),
        loader_dependency_refs=(_DEPENDENCY_REF,),
        path_resolver=resolver,
        loader_dependency_resolver=dependency_resolver,
    )


def _multi_binding() -> trace.InventoryBinding:
    objects = dict(_OBJECTS)
    objects["file_access"] = (
        _OBJECTS["file_access"][0],
        _FILE_OTHER_REF,
    )
    objects["registry_access"] = (
        _OBJECTS["registry_access"][0],
        _REGISTRY_OTHER_REF,
    )
    routes = {
        ("file_access", "file-a"): _OBJECTS["file_access"][0],
        ("file_access", "file-b"): _FILE_OTHER_REF,
        ("registry_access", "reg-a"): _OBJECTS["registry_access"][0],
        ("registry_access", "reg-b"): _REGISTRY_OTHER_REF,
        ("dll_image_load", "image"): _OBJECTS["dll_image_load"][0],
        ("code_integrity_policy", "ci"): _OBJECTS[
            "code_integrity_policy"
        ][0],
    }
    return trace.InventoryBinding(
        runtime_digest=_DIGEST,
        objects_by_plane=objects,
        loader_dependency_refs=(_DEPENDENCY_REF,),
        path_resolver=lambda plane, raw: routes.get((plane, raw)),
        loader_dependency_resolver=lambda _raw: _DEPENDENCY_REF,
    )


def _bound_reducer() -> trace._TraceReducer:
    reducer = trace._TraceReducer(_binding())
    reducer.bind(
        pid=41,
        qpc_start=100,
        initial_image_ref=_OBJECTS["dll_image_load"][0],
    )
    reducer.mark_probe_available("file_access")
    reducer.mark_probe_available("registry_access")
    return reducer


def _snapshot(
    *,
    flags: int = trace.EXACT_KERNEL_ENABLE_FLAGS,
    mode: int = trace.EXACT_LOG_FILE_MODE,
    context: int = 1,
    events_lost: int = 0,
    log_buffers_lost: int = 0,
    realtime_buffers_lost: int = 0,
) -> trace.KernelSessionSnapshot:
    return trace.KernelSessionSnapshot(
        flags,
        mode,
        context,
        events_lost,
        log_buffers_lost,
        realtime_buffers_lost,
    )


def _kernel_bound_reducer(
    inventory: trace.InventoryBinding | None = None,
) -> trace._TraceReducer:
    reducer = trace._TraceReducer(_binding() if inventory is None else inventory)
    reducer.prebind_subject(pid=41, primary_thread_id=71)
    reducer.record_kernel_session_snapshot(stage="start", snapshot=_snapshot())
    reducer.bind(
        pid=41,
        qpc_start=100,
        initial_image_ref=_OBJECTS["dll_image_load"][0],
        primary_thread_id=71,
    )
    reducer.mark_probe_available("file_access")
    reducer.mark_probe_available("registry_access")
    return reducer


def _finish(reducer: trace._TraceReducer) -> trace.RuntimeTraceResult:
    reducer.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
    reducer.end_window(200)
    reducer.mark_cleanup_proved()
    return reducer.finish()


def _finish_kernel(reducer: trace._TraceReducer) -> trace.RuntimeTraceResult:
    reducer.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
    reducer.record_kernel_session_snapshot(stage="pre_stop", snapshot=_snapshot())
    reducer.end_window(200)
    reducer.record_kernel_session_snapshot(stage="stop", snapshot=_snapshot())
    reducer.mark_cleanup_proved()
    return reducer.finish()


def _close_manifest_window(
    reducer: trace._TraceReducer, *planes: str
) -> None:
    for plane in planes:
        reducer.bind_manifest_identity(plane, _PROVIDER_BINDINGS[plane])
        reducer.mark_manifest_available(plane)


class RuntimeTraceReducerPureTests(unittest.TestCase):
    def test_exact_observations_cannot_close_unproved_file_loader_or_ci_scope(self):
        reducer = _bound_reducer()
        _close_manifest_window(
            reducer, "dll_image_load", "code_integrity_policy"
        )
        self.assertTrue(reducer.begin_callback())
        self.assertTrue(reducer.inspect_payload(32))
        reducer.file_begin(
            pid=41, timestamp=110, irp=501, raw_identity="private-file-canary"
        )
        reducer.file_complete(
            timestamp=111,
            irp=501,
            status=0xC0000022,
            exact_pid_scope=False,
        )
        reducer.registry_operation(
            pid=41,
            timestamp=112,
            raw_identity="private-registry-canary",
            status=0,
            operation="registry_open",
        )
        reducer.code_integrity_observation(
            timestamp=113,
            raw_process_identity="private-python-canary",
            raw_object_identity="private-ci-canary",
            semantic="audit",
        )

        result = _finish(reducer)

        self.assertEqual(result.candidate_id, trace.CURRENT_RUNTIME_CANDIDATE)
        self.assertEqual(result.runtime_digest, _DIGEST)
        self.assertEqual(result.subject_proof, trace.SUBJECT_ACCESS_DENIED)
        self.assertTrue(result.cleanup_proved)
        self.assertEqual(
            tuple((item.plane, item.outcome) for item in result.planes),
            (
                ("file_access", "inconclusive"),
                ("dll_image_load", "inconclusive"),
                ("registry_access", "inconclusive"),
                ("code_integrity_policy", "inconclusive"),
            ),
        )
        self.assertEqual(
            reducer._planes["file_access"].denials,
            {(_OBJECTS["file_access"][0], "file_create")},
        )
        self.assertIsNone(result.planes[0].object_ref)
        self.assertEqual(result.planes[0].reason, "plane_scope_unproved")
        self.assertIsNone(result.planes[1].object_ref)
        self.assertEqual(result.planes[1].reason, "plane_scope_unproved")
        self.assertIsNone(result.planes[3].object_ref)
        self.assertEqual(result.planes[3].reason, "plane_scope_unproved")
        self.assertTrue(result.has_inconclusive)
        self.assertRegex(result.window_binding, r"\Awindow-sha256:[0-9a-f]{64}\Z")
        self.assertTrue(
            all(item.collection_schema == trace.COLLECTION_SCHEMA for item in result.quality)
        )
        self.assertTrue(all(item.probe_available for item in result.quality))
        self.assertTrue(all(item.lossless for item in result.quality))
        self.assertEqual(
            tuple(item.plane_scope_complete for item in result.quality),
            (False, False, False, False),
        )
        self.assertTrue(all(item.correlation_complete for item in result.quality))
        for forbidden_claim in ("qualified", "qualification", "pass", "passed"):
            self.assertFalse(hasattr(result, forbidden_claim))

    def test_wrong_pid_and_out_of_window_records_do_not_bind_to_child(self):
        reducer = _bound_reducer()
        reducer.file_begin(pid=40, timestamp=110, irp=1, raw_identity="secret")
        reducer.registry_operation(
            pid=41,
            timestamp=99,
            raw_identity="secret",
            status=0xC0000022,
            operation="registry_open",
        )
        reducer.code_integrity_observation(
            timestamp=99,
            raw_process_identity="secret",
            raw_object_identity="secret",
            semantic="denial",
        )

        result = _finish(reducer)

        self.assertEqual(result.planes[0].outcome, "inconclusive")
        self.assertEqual(result.planes[2].outcome, "inconclusive")
        self.assertEqual(result.planes[3].outcome, "inconclusive")
        self.assertEqual(result.planes[1].outcome, "inconclusive")

    def test_file_begin_is_bound_to_completion_only_by_irp(self):
        reducer = _bound_reducer()
        reducer.file_begin(pid=41, timestamp=110, irp=9, raw_identity="known")
        reducer.file_complete(
            timestamp=111,
            irp=8,
            status=0xC0000022,
            exact_pid_scope=True,
        )

        result = _finish(reducer)

        self.assertEqual(result.planes[0].outcome, "inconclusive")
        self.assertEqual(result.planes[0].reason, "plane_scope_unproved")
        self.assertFalse(result.quality[0].correlation_complete)

    def test_unmatched_pending_irp_and_nonclosed_status_are_inconclusive(self):
        for mode in ("pending", "other_status"):
            with self.subTest(mode=mode):
                reducer = _bound_reducer()
                reducer.file_begin(
                    pid=41, timestamp=110, irp=10, raw_identity="known"
                )
                if mode == "other_status":
                    reducer.file_complete(
                        timestamp=111,
                        irp=10,
                        status=0xC0000034,
                        exact_pid_scope=False,
                    )
                result = _finish(reducer)
                self.assertEqual(result.planes[0].outcome, "inconclusive")

    def test_exact_non_access_denied_statuses_preserve_correlation(self):
        reducer = _bound_reducer()
        reducer.file_begin(pid=41, timestamp=110, irp=1, raw_identity="known")
        reducer.file_complete(
            timestamp=111, irp=1, status=0, exact_pid_scope=False
        )
        reducer.file_begin(pid=41, timestamp=112, irp=2, raw_identity="known")
        reducer.file_complete(
            timestamp=113,
            irp=2,
            status=0xC0000034,
            exact_pid_scope=False,
        )
        reducer.registry_operation(
            pid=41,
            timestamp=114,
            raw_identity="known",
            status=0,
            operation="registry_open",
        )
        reducer.registry_operation(
            pid=41,
            timestamp=115,
            raw_identity="known",
            status=0xC0000034,
            operation="registry_query",
        )

        result = _finish(reducer)

        for index in (0, 2):
            with self.subTest(plane=result.planes[index].plane):
                self.assertEqual(result.planes[index].outcome, "inconclusive")
                self.assertEqual(
                    result.planes[index].reason, "plane_scope_unproved"
                )
                self.assertTrue(result.quality[index].correlation_complete)
                self.assertEqual(
                    result.planes[index].reason,
                    trace._quality_failure_reason(result.quality[index]),
                )
        self.assertEqual(reducer._planes["file_access"].successes, 2)
        self.assertEqual(reducer._planes["registry_access"].successes, 2)

    def test_rundown_image_is_never_claimed_as_an_actual_load(self):
        reducer = _bound_reducer()
        before = reducer._planes["dll_image_load"].successes
        reducer.image_load(
            pid=41,
            timestamp=110,
            raw_identity="known",
            is_rundown=True,
        )

        self.assertEqual(reducer._planes["dll_image_load"].successes, before)

    def test_absent_ci_observation_remains_inconclusive(self):
        reducer = _bound_reducer()
        _close_manifest_window(reducer, "dll_image_load")
        reducer.file_begin(pid=41, timestamp=110, irp=1, raw_identity="known")
        reducer.file_complete(
            timestamp=111, irp=1, status=0, exact_pid_scope=False
        )
        reducer.registry_operation(
            pid=41,
            timestamp=112,
            raw_identity="known",
            status=0,
            operation="registry_open",
        )

        result = _finish(reducer)

        self.assertEqual(result.planes[3].outcome, "inconclusive")
        self.assertEqual(result.planes[3].reason, "probe_unavailable")

    def test_successful_initial_and_runtime_images_without_failure_schema_block(self):
        reducer = _bound_reducer()
        reducer.image_load(
            pid=41,
            timestamp=110,
            raw_identity="known-successful-image",
        )

        result = _finish(reducer)

        self.assertEqual(result.planes[1].outcome, "inconclusive")
        self.assertEqual(result.planes[1].reason, "probe_unavailable")
        self.assertFalse(result.quality[1].probe_available)
        self.assertEqual(
            result.quality[1].collection_schema, trace.UNKNOWN_COLLECTION_SCHEMA
        )

    def test_cleanup_is_unproved_until_controller_marks_full_lifecycle(self):
        reducer = _bound_reducer()
        _close_manifest_window(
            reducer, "dll_image_load", "code_integrity_policy"
        )
        reducer.file_begin(pid=41, timestamp=110, irp=1, raw_identity="known")
        reducer.file_complete(
            timestamp=111, irp=1, status=0xC0000022, exact_pid_scope=False
        )
        reducer.registry_operation(
            pid=41,
            timestamp=112,
            raw_identity="known",
            status=0,
            operation="registry_open",
        )
        reducer.code_integrity_observation(
            timestamp=113,
            raw_process_identity="known-process",
            raw_object_identity="known-image",
            semantic="audit",
        )
        reducer.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
        reducer.end_window(200)

        result = reducer.finish()

        self.assertFalse(result.cleanup_proved)
        self.assertTrue(all(item.outcome == "inconclusive" for item in result.planes))
        self.assertEqual(
            tuple(item.reason for item in result.planes),
            (
                "plane_scope_unproved",
                "plane_scope_unproved",
                "plane_scope_unproved",
                "plane_scope_unproved",
            ),
        )
        self.assertTrue(all(not item.cleanup_proved for item in result.quality))

    def test_loss_unknown_schema_and_missing_subject_proof_fail_closed(self):
        mutations = (
            lambda item: item.mark_lost_events(),
            lambda item: item.mark_schema_unknown("registry_access"),
            lambda item: None,
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                reducer = _bound_reducer()
                mutate(reducer)
                if index < 2:
                    result = _finish(reducer)
                else:
                    reducer.end_window(200)
                    result = reducer.finish()
                self.assertTrue(result.has_inconclusive)
                if index == 0 or index == 2:
                    self.assertTrue(
                        all(item.outcome == "inconclusive" for item in result.planes)
                    )

    def test_callback_payload_record_and_pending_caps_are_fail_closed(self):
        reducers = []

        callback = _bound_reducer()
        for _ in range(trace.MAX_CALLBACKS + 1):
            callback.begin_callback()
        reducers.append(callback)

        payload = _bound_reducer()
        self.assertFalse(
            payload.inspect_payload(trace.MAX_INSPECTED_PAYLOAD_BYTES + 1)
        )
        reducers.append(payload)

        records = _bound_reducer()
        for index in range(trace.MAX_CHILD_RECORDS):
            records.image_load(
                pid=41,
                timestamp=110,
                raw_identity=f"raw-{index}",
            )
        reducers.append(records)

        pending = _bound_reducer()
        for index in range(trace.MAX_PENDING_IRPS + 1):
            pending.file_begin(
                pid=41,
                timestamp=110,
                irp=index + 1,
                raw_identity=f"raw-{index}",
            )
        reducers.append(pending)

        for index, reducer in enumerate(reducers):
            with self.subTest(case=index):
                result = _finish(reducer)
                self.assertTrue(result.has_inconclusive)
                self.assertTrue(
                    any(item.reason == "observation_overflow" for item in result.planes)
                )
                self.assertLessEqual(result.callbacks, trace.MAX_CALLBACKS)
                self.assertLessEqual(
                    result.inspected_payload_bytes,
                    trace.MAX_INSPECTED_PAYLOAD_BYTES,
                )
                self.assertLessEqual(result.child_records, trace.MAX_CHILD_RECORDS)

    def test_unknown_inventory_identity_is_never_retained(self):
        canary = (
            "Authorization: Bearer SECRET_TRACE_CANARY "
            r"C:\\Users\\private\\python.exe provider=<raw> pid=777"
        )
        reducer = trace._TraceReducer(_binding(resolver=lambda _plane, _raw: None))
        reducer.bind(
            pid=41,
            qpc_start=100,
            initial_image_ref=_OBJECTS["dll_image_load"][0],
        )
        reducer.mark_probe_available("file_access")
        reducer.mark_probe_available("registry_access")
        reducer.file_begin(
            pid=41, timestamp=110, irp=1, raw_identity=canary
        )
        reducer.file_complete(
            timestamp=111,
            irp=1,
            status=0xC0000022,
            exact_pid_scope=False,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = _finish(reducer)

        combined = repr(result) + repr(reducer.__dict__) + stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(canary, combined)
        self.assertEqual(result.planes[0].outcome, "inconclusive")
        self.assertEqual(result.planes[0].reason, "plane_scope_unproved")
        self.assertFalse(result.quality[0].plane_scope_complete)

    def test_inventory_binding_is_typed_unique_and_nonrevealing(self):
        binding = _binding()
        self.assertNotIn("private", repr(binding))
        wrong_path = _binding(
            resolver=lambda plane, _raw: (
                _DEPENDENCY_REF
                if plane == "dll_image_load"
                else _OBJECTS[plane][0]
            )
        )
        wrong_dependency = _binding(
            dependency_resolver=lambda _raw: _OBJECTS["dll_image_load"][0]
        )
        self.assertIsNone(
            wrong_path.resolve("dll_image_load", "dependency.dll")
        )
        self.assertIsNone(
            wrong_dependency.resolve_loader_dependency("python314.dll")
        )
        invalid = dict(_OBJECTS)
        invalid["file_access"] = _OBJECTS["dll_image_load"]
        with self.assertRaises(trace.RuntimeTraceError) as raised:
            trace.InventoryBinding(
                runtime_digest=_DIGEST,
                objects_by_plane=invalid,
                loader_dependency_refs=(_DEPENDENCY_REF,),
                path_resolver=lambda _plane, _raw: None,
                loader_dependency_resolver=lambda _raw: None,
            )
        self.assertEqual(raised.exception.code, "trace_binding_invalid")
        for dependency_refs in (
            (),
            ("inventory-sha256:" + "f" * 64,),
            _OBJECTS["dll_image_load"],
            (["inventory-sha256:" + "f" * 64],),
        ):
            with self.subTest(dependency_refs=dependency_refs):
                with self.assertRaises(trace.RuntimeTraceError):
                    trace.InventoryBinding(
                        runtime_digest=_DIGEST,
                        objects_by_plane=dict(_OBJECTS),
                        loader_dependency_refs=dependency_refs,
                        path_resolver=lambda _plane, _raw: None,
                        loader_dependency_resolver=lambda _raw: None,
                    )
        with self.assertRaises(trace.RuntimeTraceError):
            trace._TraceReducer(_binding()).bind(
                pid=41,
                qpc_start=100,
                initial_image_ref=_DEPENDENCY_REF,
            )

    def test_exact_manifest_callback_never_promotes_negative_window_closure(self):
        reducer = _bound_reducer()
        reducer.code_integrity_observation(
            timestamp=110,
            raw_process_identity="python-image",
            raw_object_identity="runtime-image",
            semantic="audit",
        )

        result = _finish(reducer)

        ci = result.planes[3]
        self.assertEqual(ci.outcome, "inconclusive")
        self.assertEqual(ci.reason, "plane_scope_unproved")
        self.assertFalse(reducer._negative_window_closure["code_integrity_policy"])

    def test_exact_ci_denial_remains_intermediate_without_scope_proof(self):
        reducer = _bound_reducer()
        reducer.code_integrity_observation(
            timestamp=110,
            raw_process_identity="python-image",
            raw_object_identity="runtime-image",
            semantic="denial",
        )

        result = _finish(reducer)

        self.assertEqual(
            reducer._planes["code_integrity_policy"].denials,
            {(_OBJECTS["code_integrity_policy"][0], "image_policy_validate")},
        )
        self.assertEqual(result.planes[3].outcome, "inconclusive")
        self.assertIsNone(result.planes[3].object_ref)
        self.assertEqual(result.planes[3].reason, "plane_scope_unproved")
        self.assertTrue(result.quality[3].probe_available)
        self.assertEqual(
            result.quality[3].collection_schema, trace.COLLECTION_SCHEMA
        )
        self.assertFalse(reducer._negative_window_closure["code_integrity_policy"])

    def test_ci_denial_without_exact_payload_process_is_only_ambiguous(self):
        reducer = _bound_reducer()
        _close_manifest_window(reducer, "code_integrity_policy")

        reducer.code_integrity_observation(
            timestamp=110,
            semantic="denial",
            raw_process_identity=None,
            raw_object_identity="runtime-image",
        )

        self.assertFalse(reducer._planes["code_integrity_policy"].denials)
        self.assertIn(
            "observation_ambiguous",
            reducer._planes["code_integrity_policy"].reasons,
        )
        result = _finish(reducer)
        self.assertEqual(result.planes[3].outcome, "inconclusive")
        self.assertEqual(result.planes[3].reason, "plane_scope_unproved")
        self.assertFalse(result.quality[3].correlation_complete)

    def test_user_loader_denial_requires_exact_initial_process_and_dependency(self):
        initial = _OBJECTS["dll_image_load"][0]
        other = _OTHER_IMAGE_REF
        objects = dict(_OBJECTS)
        objects["dll_image_load"] = (initial, _DEPENDENCY_REF, other)

        def resolver(plane: str, raw: str) -> str | None:
            if plane == "dll_image_load":
                return {
                    "python.exe": initial,
                    "other.exe": other,
                }.get(raw)
            return _OBJECTS[plane][0]

        def dependency_resolver(raw: str) -> str | None:
            return _DEPENDENCY_REF if raw == "dependency.dll" else None

        def build() -> trace._TraceReducer:
            reducer = trace._TraceReducer(
                trace.InventoryBinding(
                    runtime_digest=_DIGEST,
                    objects_by_plane=objects,
                    loader_dependency_refs=(_DEPENDENCY_REF,),
                    path_resolver=resolver,
                    loader_dependency_resolver=dependency_resolver,
                )
            )
            reducer.bind(pid=41, qpc_start=100, initial_image_ref=initial)
            reducer.bind_manifest_identity(
                "dll_image_load", _PROVIDER_BINDINGS["dll_image_load"]
            )
            reducer.mark_manifest_available("dll_image_load")
            return reducer

        exact = build()
        exact.user_loader_observation(
            pid=41,
            timestamp=110,
            semantic="status_denial",
            raw_process_identity="python.exe",
            raw_object_identity="dependency.dll",
            failure_status=0xC0000022,
        )
        self.assertEqual(
            exact._planes["dll_image_load"].denials,
            {(_DEPENDENCY_REF, "image_map")},
        )
        exact_result = _finish(exact)
        self.assertEqual(exact_result.planes[1].outcome, "inconclusive")
        self.assertEqual(exact_result.planes[1].reason, "plane_scope_unproved")

        mismatch = build()
        mismatch.user_loader_observation(
            pid=41,
            timestamp=110,
            semantic="status_denial",
            raw_process_identity="other.exe",
            raw_object_identity="dependency.dll",
            failure_status=0xC0000022,
        )
        mismatch_result = _finish(mismatch)
        self.assertEqual(mismatch_result.planes[1].outcome, "inconclusive")
        self.assertEqual(
            mismatch_result.planes[1].reason, "plane_scope_unproved"
        )

        for unresolved_contract in (
            "api-ms-win-core-file-l1-1-0.dll",
            "ext-ms-win-session-wtsapi32-l1-1-0.dll",
        ):
            with self.subTest(unresolved_contract=unresolved_contract):
                unresolved = build()
                unresolved.user_loader_observation(
                    pid=41,
                    timestamp=110,
                    semantic="status_denial",
                    raw_process_identity="python.exe",
                    raw_object_identity=unresolved_contract,
                    failure_status=0xC0000022,
                )
                unresolved_result = _finish(unresolved)
                self.assertEqual(
                    unresolved_result.planes[1].outcome, "inconclusive"
                )
                self.assertEqual(
                    unresolved_result.planes[1].reason,
                    "plane_scope_unproved",
                )

        other_status = build()
        other_status.user_loader_observation(
            pid=41,
            timestamp=110,
            semantic="status_denial",
            raw_process_identity="python.exe",
            raw_object_identity="dependency.dll",
            failure_status=0xC0000034,
        )
        other_status_result = _finish(other_status)
        self.assertEqual(
            other_status_result.planes[1].reason, "plane_scope_unproved"
        )
        self.assertFalse(other_status_result.quality[1].correlation_complete)

    def test_ci_exact_object_with_other_known_process_is_inconclusive(self):
        initial = _OBJECTS["dll_image_load"][0]
        other = _OTHER_IMAGE_REF
        objects = dict(_OBJECTS)
        objects["dll_image_load"] = (initial, _DEPENDENCY_REF, other)

        def resolver(plane: str, raw: str) -> str | None:
            if plane == "dll_image_load":
                return {"python.exe": initial, "other.exe": other}.get(raw)
            if plane == "code_integrity_policy" and raw == "runtime.dll":
                return _OBJECTS[plane][0]
            return None

        reducer = trace._TraceReducer(
            trace.InventoryBinding(
                runtime_digest=_DIGEST,
                objects_by_plane=objects,
                loader_dependency_refs=(_DEPENDENCY_REF,),
                path_resolver=resolver,
                loader_dependency_resolver=lambda _raw: None,
            )
        )
        reducer.bind(pid=41, qpc_start=100, initial_image_ref=initial)
        reducer.bind_manifest_identity(
            "code_integrity_policy",
            _PROVIDER_BINDINGS["code_integrity_policy"],
        )
        reducer.mark_manifest_available("code_integrity_policy")
        reducer.code_integrity_observation(
            timestamp=110,
            semantic="denial",
            raw_process_identity="other.exe",
            raw_object_identity="runtime.dll",
        )

        result = _finish(reducer)

        self.assertEqual(result.planes[3].outcome, "inconclusive")
        self.assertEqual(result.planes[3].reason, "plane_scope_unproved")

    def test_window_binding_commits_verified_provider_descriptor_identity(self):
        results = []
        for suffix in ("b", "d"):
            reducer = _bound_reducer()
            reducer.bind_manifest_identity(
                "dll_image_load", "provider-sha256:" + suffix * 64
            )
            reducer.mark_manifest_available("dll_image_load")
            results.append(_finish(reducer).window_binding)

        self.assertNotEqual(results[0], results[1])

    def test_manifest_schema_downgrade_is_sticky_across_later_exact_denial(self):
        reducer = _bound_reducer()
        _close_manifest_window(reducer, "code_integrity_policy")
        reducer.mark_schema_unknown("code_integrity_policy")
        reducer.code_integrity_observation(
            timestamp=110,
            semantic="denial",
            raw_process_identity="python.exe",
            raw_object_identity="runtime.dll",
        )

        result = _finish(reducer)

        self.assertEqual(result.planes[3].outcome, "inconclusive")
        self.assertEqual(
            result.planes[3].reason, "collection_schema_unproved"
        )

    def test_manifest_raw_identifiers_are_callback_local_only(self):
        process_canary = r"C:\Private\SECRET_PROCESS_CANARY\python.exe"
        object_canary = "SECRET_DEPENDENCY_CANARY.dll"
        reducer = _bound_reducer()
        reducer.user_loader_observation(
            pid=41,
            timestamp=110,
            semantic="status_denial",
            raw_process_identity=process_canary,
            raw_object_identity=object_canary,
            failure_status=0xC0000022,
        )
        reducer.code_integrity_observation(
            timestamp=111,
            semantic="denial",
            raw_process_identity=process_canary,
            raw_object_identity=object_canary,
        )

        retained = repr(reducer.__dict__) + repr(_finish(reducer))

        self.assertNotIn(process_canary, retained)
        self.assertNotIn(object_canary, retained)


class _FakePort:
    def __init__(
        self,
        *,
        collision: bool = False,
        stop_ok: bool = True,
        remains_present: bool = False,
        open_error: bool = False,
        manifest_capable_planes: frozenset[str] = frozenset(
            {"file_access", "registry_access"}
        ),
        loss_counters: tuple[int, int, int] = (0, 0, 0),
    ) -> None:
        self.collision = collision
        self.stop_ok = stop_ok
        self.remains_present = remains_present
        self.open_error = open_error
        self.manifest_capable_planes = manifest_capable_planes
        self.loss_counters = loss_counters
        self.events: list[str] = []
        self.stopped = threading.Event()
        self.started = False
        self.qpc_value = 100

    def process_id(self, process_handle: int) -> int:
        self.events.append("process_id")
        return 41 if process_handle == 900 else 0

    def thread_binding(
        self, process_handle: int, thread_handle: int
    ) -> tuple[int, int]:
        self.events.append("thread_binding")
        return (41, 71) if (process_handle, thread_handle) == (900, 901) else (0, 0)

    def qpc(self) -> int:
        self.events.append("qpc")
        value = self.qpc_value
        self.qpc_value += 100
        return value

    def session_present(self) -> bool:
        self.events.append("query")
        if not self.started:
            return self.collision
        return self.remains_present

    def start_session(self) -> int:
        self.events.append("start")
        self.started = True
        return 44

    def enable_session(self, owned_session_handle: int) -> frozenset[str]:
        if owned_session_handle != 44:
            raise trace.RuntimeTraceError("trace_unavailable")
        return self.manifest_capable_planes

    def query_session(
        self, owned_session_handle: int
    ) -> trace.KernelSessionSnapshot:
        self.events.append("scope_query")
        if owned_session_handle != 44:
            raise trace.RuntimeTraceError("trace_unavailable")
        return trace.KernelSessionSnapshot(
            trace.EXACT_KERNEL_ENABLE_FLAGS,
            trace.EXACT_LOG_FILE_MODE,
            1,
            0,
            0,
            0,
        )

    def open_consumer(self, record_callback, loss_callback) -> int:
        del record_callback, loss_callback
        self.events.append("open")
        if self.open_error:
            raise trace.RuntimeTraceError("trace_unavailable")
        return 55

    def process(self, trace_handle: int) -> int:
        self.events.append("process")
        if trace_handle != 55:
            return -1
        self.stopped.wait(2.0)
        self.events.append("process_end")
        return 0

    def stop_session(self, owned_session_handle: int) -> trace.StopTraceResult:
        self.events.append("stop")
        self.stopped.set()
        return trace.StopTraceResult(
            self.stop_ok and owned_session_handle == 44,
            *self.loss_counters,
            trace.KernelSessionSnapshot(
                trace.EXACT_KERNEL_ENABLE_FLAGS,
                trace.EXACT_LOG_FILE_MODE,
                1,
                *self.loss_counters,
            ),
        )

    def close_consumer(self, trace_handle: int) -> bool:
        self.events.append("close")
        return trace_handle == 55


class RuntimeTraceControllerPureTests(unittest.TestCase):
    def test_collision_is_blocked_without_adopt_open_or_stop(self):
        port = _FakePort(collision=True)
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)

        with self.assertRaises(trace.RuntimeTraceError) as raised:
            collector.start_for_suspended_child(
                process_id=41,
                process_handle=900,
                thread_handle=901,
                initial_image_object_ref=_OBJECTS["dll_image_load"][0],
            )

        self.assertEqual(raised.exception.code, "trace_session_collision")
        self.assertEqual(
            port.events, ["process_id", "thread_binding", "query"]
        )

    def test_held_process_handle_must_resolve_to_exact_pid_before_start(self):
        port = _FakePort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)

        with self.assertRaises(trace.RuntimeTraceError) as raised:
            collector.start_for_suspended_child(
                process_id=42,
                process_handle=900,
                thread_handle=901,
                initial_image_object_ref=_OBJECTS["dll_image_load"][0],
            )

        self.assertEqual(raised.exception.code, "trace_binding_invalid")
        self.assertNotIn("start", port.events)

    def test_success_cleanup_order_is_stop_join_close_then_absence_query(self):
        port = _FakePort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)

        result = collector.stop()

        stop = port.events.index("stop")
        process_end = port.events.index("process_end")
        close = port.events.index("close")
        final_query = len(port.events) - 1
        self.assertLess(stop, process_end)
        self.assertLess(process_end, close)
        self.assertEqual(port.events[final_query], "query")
        self.assertLess(close, final_query)
        self.assertTrue(result.cleanup_proved)

    def test_cleanup_uncertainty_maps_every_plane_to_inconclusive(self):
        port = _FakePort(stop_ok=False, remains_present=True)
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)

        result = collector.stop()

        self.assertFalse(result.cleanup_proved)
        self.assertTrue(all(item.outcome == "inconclusive" for item in result.planes))
        self.assertTrue(all(not item.cleanup_proved for item in result.quality))
        self.assertEqual(
            tuple(item.reason for item in result.planes),
            tuple(trace._quality_failure_reason(item) for item in result.quality),
        )

    def test_start_failure_stops_only_owned_handle_and_proves_absence(self):
        port = _FakePort(open_error=True)
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)

        with self.assertRaises(trace.RuntimeTraceError) as raised:
            collector.start_for_suspended_child(
                process_id=41,
                process_handle=900,
                thread_handle=901,
                initial_image_object_ref=_OBJECTS["dll_image_load"][0],
            )

        self.assertEqual(raised.exception.code, "trace_unavailable")
        self.assertEqual(port.events.count("stop"), 1)
        self.assertNotIn("close", port.events)
        self.assertEqual(port.events[-1], "query")

    def test_manifest_enable_failure_is_closed_probe_unavailable(self):
        port = _FakePort(
            manifest_capable_planes=frozenset(
                {"file_access", "registry_access"}
            )
        )
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)

        result = collector.stop()

        self.assertEqual(result.planes[3].outcome, "inconclusive")
        self.assertEqual(result.planes[3].reason, "probe_unavailable")

    def test_stop_statistics_loss_is_inconclusive_even_when_callbacks_report_none(self):
        port = _FakePort(loss_counters=(1, 0, 0))
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)

        result = collector.stop()

        self.assertTrue(result.has_inconclusive)
        self.assertTrue(all(item.outcome == "inconclusive" for item in result.planes))
        self.assertEqual(result.planes[0].reason, "observation_overflow")
        self.assertTrue(all(not item.lossless for item in result.quality))

    def test_abort_uses_owned_stop_join_close_absence_and_is_idempotent_before_start(self):
        fresh_port = _FakePort()
        fresh = trace.RealtimeRuntimeCollector(_binding(), port=fresh_port)
        fresh.abort()
        self.assertEqual(fresh_port.events, [])

        port = _FakePort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )

        collector.abort()

        self.assertLess(port.events.index("stop"), port.events.index("process_end"))
        self.assertLess(port.events.index("process_end"), port.events.index("close"))
        self.assertEqual(port.events[-1], "query")

    def test_terminal_guard_prevents_late_timer_mutation_or_second_stop(self):
        port = _FakePort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
        result = collector.stop()
        stops = port.events.count("stop")

        collector._expire_session()
        after = collector._reducer.finish()

        self.assertEqual(port.events.count("stop"), stops)
        self.assertEqual(after, result)

    def test_failed_stop_retains_owned_handle_for_one_abort_retry(self):
        class RetryPort(_FakePort):
            def __init__(self) -> None:
                super().__init__()
                self.stop_attempts = 0

            def stop_session(self, owned_session_handle: int) -> trace.StopTraceResult:
                self.events.append("stop")
                self.stop_attempts += 1
                self.stopped.set()
                return trace.StopTraceResult(
                    self.stop_attempts >= 2 and owned_session_handle == 44,
                    0,
                    0,
                    0,
                )

            def session_present(self) -> bool:
                self.events.append("query")
                return self.started and self.stop_attempts < 2

        port = RetryPort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)

        result = collector.stop()

        self.assertFalse(result.cleanup_proved)
        self.assertEqual(collector._owned_session, 44)
        self.assertEqual(port.events.count("close"), 1)
        collector.abort()
        self.assertEqual(port.events.count("stop"), 2)
        self.assertEqual(port.events.count("close"), 1)
        self.assertIsNone(collector._owned_session)
        self.assertIsNone(collector._consumer)

    def test_successful_stop_with_close_failure_retries_only_unfinished_phase(self):
        class RetryClosePort(_FakePort):
            def __init__(self) -> None:
                super().__init__()
                self.close_attempts = 0

            def close_consumer(self, trace_handle: int) -> bool:
                self.events.append("close")
                self.close_attempts += 1
                return self.close_attempts >= 2 and trace_handle == 55

        port = RetryClosePort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)

        result = collector.stop()

        self.assertFalse(result.cleanup_proved)
        self.assertTrue(collector._session_stopped)
        self.assertEqual(port.events.count("stop"), 1)
        collector.abort()
        self.assertEqual(port.events.count("stop"), 1)
        self.assertEqual(port.events.count("close"), 2)
        self.assertIsNone(collector._owned_session)


class RuntimeTraceNativeDefinitionTests(unittest.TestCase):
    def test_session_identity_modes_flags_and_caps_are_exact_and_bounded(self):
        self.assertEqual(
            trace.SESSION_NAME, "OpenAI.TaskGov.M241B.RuntimeQualification"
        )
        self.assertEqual(
            str(trace.SESSION_GUID), "82a6642f-b340-4db5-8d53-09dd78e03262"
        )
        self.assertEqual(trace.EXACT_KERNEL_ENABLE_FLAGS, 0x16020007)
        self.assertEqual(trace.EXACT_LOG_FILE_MODE, 0x0A000100)
        self.assertEqual(trace.EXACT_PROCESS_TRACE_MODE, 0x10001100)
        self.assertLessEqual(trace.MAX_DURATION_SECONDS, 15.0)
        self.assertLessEqual(trace.MAX_CALLBACKS, 100_000)
        self.assertLessEqual(trace.MAX_INSPECTED_PAYLOAD_BYTES, 16 * 1024 * 1024)
        self.assertLessEqual(trace.MAX_CHILD_RECORDS, 512)
        self.assertLessEqual(trace.MAX_PENDING_IRPS, 512)

        raw, props = trace._WindowsEtwPort._properties()
        self.assertEqual(props.Wnode.BufferSize, len(raw))
        self.assertEqual(props.Wnode.Guid.key(), trace.SESSION_GUID.bytes_le)
        self.assertEqual(props.Wnode.ClientContext, 1)
        self.assertEqual(props.EnableFlags, trace.EXACT_KERNEL_ENABLE_FLAGS)
        self.assertEqual(props.LogFileMode, trace.EXACT_LOG_FILE_MODE)
        self.assertEqual(props.LogFileNameOffset, 0)
        self.assertGreater(props.LoggerNameOffset, 0)
        self.assertNotIn("del raw", inspect.getsource(trace._WindowsEtwPort))

    def test_properties_buffer_remains_readable_in_start_query_and_stop_calls(self):
        class FakeAdvapi:
            def __init__(self) -> None:
                self.query_absent = True

            @staticmethod
            def _props(pointer):
                return ctypes.cast(
                    pointer, ctypes.POINTER(trace._EVENT_TRACE_PROPERTIES)
                ).contents

            def StartTraceW(self, handle, name, properties):
                props = self._props(properties)
                self._assert_live(name, props)
                ctypes.cast(handle, ctypes.POINTER(trace.TRACEHANDLE)).contents.value = 44
                return 0

            def EnableTraceEx2(self, *_args):
                return 0

            def ControlTraceW(self, _handle, name, properties, control):
                props = self._props(properties)
                self._assert_live(name, props)
                self._assert_zero_query_input(props)
                if control == trace._EVENT_TRACE_CONTROL_QUERY:
                    if self.query_absent:
                        return trace._ERROR_WMI_INSTANCE_NOT_FOUND
                    props.EnableFlags = trace.EXACT_KERNEL_ENABLE_FLAGS
                    props.LogFileMode = trace.EXACT_LOG_FILE_MODE
                    props.Wnode.ClientContext = 1
                    return 0
                props.EnableFlags = trace.EXACT_KERNEL_ENABLE_FLAGS
                props.LogFileMode = trace.EXACT_LOG_FILE_MODE
                props.Wnode.ClientContext = 1
                props.EventsLost = 2
                props.LogBuffersLost = 3
                props.RealTimeBuffersLost = 4
                return 0

            @staticmethod
            def _assert_zero_query_input(props):
                if any(
                    int(value)
                    for value in (
                        props.Wnode.ClientContext,
                        props.Wnode.Flags,
                        props.BufferSize,
                        props.MinimumBuffers,
                        props.MaximumBuffers,
                        props.LogFileMode,
                        props.FlushTimer,
                        props.EnableFlags,
                        props.EventsLost,
                        props.LogBuffersLost,
                        props.RealTimeBuffersLost,
                    )
                ):
                    raise AssertionError("QUERY/STOP input was not zeroed")

            @staticmethod
            def _assert_live(name, props):
                self_name = ctypes.wstring_at(
                    ctypes.addressof(props) + int(props.LoggerNameOffset)
                )
                if name != trace.SESSION_NAME or self_name != trace.SESSION_NAME:
                    raise AssertionError("properties buffer lifetime failure")

        port = object.__new__(trace._WindowsEtwPort)
        port._advapi = FakeAdvapi()

        self.assertFalse(port.session_present())
        handle = port.start_session()
        capable = port.enable_session(handle)
        port._advapi.query_absent = False
        queried = port.query_session(handle)
        stopped = port.stop_session(handle)

        self.assertEqual(handle, 44)
        self.assertEqual(
            capable, frozenset({"file_access", "registry_access"})
        )
        self.assertEqual(queried, _snapshot())
        self.assertEqual(
            stopped,
            trace.StopTraceResult(
                True,
                2,
                3,
                4,
                _snapshot(events_lost=2, log_buffers_lost=3, realtime_buffers_lost=4),
            ),
        )

    def test_provider_exception_retains_owned_session_for_abort_retry(self):
        class RetryEnablePort(_FakePort):
            def __init__(self) -> None:
                super().__init__()
                self.stop_attempts = 0

            def enable_session(self, owned_session_handle: int) -> frozenset[str]:
                if owned_session_handle != 44:
                    raise AssertionError("wrong owned session")
                self.events.append("enable")
                raise MemoryError("PRIVATE_PROVIDER_CANARY")

            def stop_session(self, owned_session_handle: int) -> trace.StopTraceResult:
                self.events.append("stop")
                self.stop_attempts += 1
                return trace.StopTraceResult(
                    self.stop_attempts >= 2 and owned_session_handle == 44,
                    0,
                    0,
                    0,
                )

            def session_present(self) -> bool:
                self.events.append("query")
                return self.started and self.stop_attempts < 2

        port = RetryEnablePort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        with self.assertRaises(trace.RuntimeTraceError) as raised:
            collector.start_for_suspended_child(
                process_id=41,
                process_handle=900,
                thread_handle=901,
                initial_image_object_ref=_OBJECTS["dll_image_load"][0],
            )

        self.assertEqual(raised.exception.code, "trace_cleanup_unproved")
        self.assertNotIn("PRIVATE_PROVIDER_CANARY", str(raised.exception))
        self.assertEqual(collector._owned_session, 44)
        collector.abort()
        self.assertEqual(port.events.count("stop"), 2)
        self.assertIsNone(collector._owned_session)

    def test_unrelated_or_synthetic_metadata_never_triggers_child_schema_decode(self):
        reducer = _bound_reducer()
        port = object.__new__(trace._WindowsEtwPort)
        port._reducer = reducer
        port._integer = lambda *_args: (_ for _ in ()).throw(
            AssertionError("unrelated payload decoded")
        )
        port._string = port._integer

        unrelated = trace._EVENT_RECORD()
        unrelated.EventHeader.ProviderId = trace._GUID.from_uuid(
            trace.uuid.UUID("90cbdc39-4a3e-11d1-84f4-0000f80464e3")
        )
        unrelated.EventHeader.EventDescriptor.Opcode = 64
        unrelated.EventHeader.EventDescriptor.Version = 2
        unrelated.EventHeader.ProcessId = 99
        unrelated.EventHeader.TimeStamp = 99
        port.translate(ctypes.pointer(unrelated))

        metadata = trace._EVENT_RECORD()
        metadata.EventHeader.ProviderId = trace._GUID.from_uuid(
            trace.uuid.UUID("68fdd900-4a3e-11d1-84f4-0000f80464e3")
        )
        metadata.EventHeader.ProcessId = 41
        metadata.EventHeader.TimeStamp = 110
        port.translate(ctypes.pointer(metadata))

        reducer.mark_cleanup_proved()
        reducer.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
        reducer.end_window(200)
        result = reducer.finish()
        self.assertEqual(result.quality[0].collection_schema, trace.COLLECTION_SCHEMA)

    def test_target_kernel_unknown_opcode_or_version_is_sticky_after_success(self):
        cases = (
            ("file_access", trace._FILE_PROVIDER_UUID, 64, 1, 0),
            ("file_access", trace._FILE_PROVIDER_UUID, 64, 4, 0),
            ("file_access", trace._FILE_PROVIDER_UUID, 76, 1, 0),
            ("file_access", trace._FILE_PROVIDER_UUID, 78, 2, 0),
            ("registry_access", trace._REGISTRY_PROVIDER_UUID, 10, 1, 2),
            ("registry_access", trace._REGISTRY_PROVIDER_UUID, 29, 2, 2),
            ("registry_access", trace._REGISTRY_PROVIDER_UUID, 99, 2, 2),
        )
        for plane, provider, opcode, version, plane_index in cases:
            with self.subTest(plane=plane, opcode=opcode, version=version):
                reducer = _bound_reducer()
                if plane == "file_access":
                    reducer.file_begin(
                        pid=41, timestamp=105, irp=700, raw_identity="known"
                    )
                    reducer.file_complete(
                        timestamp=106,
                        irp=700,
                        status=0,
                        exact_pid_scope=False,
                    )
                else:
                    reducer.registry_operation(
                        pid=41,
                        timestamp=105,
                        raw_identity="known",
                        status=0,
                        operation="registry_open",
                    )
                self.assertEqual(reducer._planes[plane].successes, 1)

                port = object.__new__(trace._WindowsEtwPort)
                port._reducer = reducer
                port._integer = lambda *_args: (_ for _ in ()).throw(
                    AssertionError("unknown kernel payload decoded")
                )
                port._string = port._integer
                drift = trace._EVENT_RECORD()
                drift.EventHeader.ProviderId = trace._GUID.from_uuid(provider)
                drift.EventHeader.EventDescriptor.Opcode = opcode
                drift.EventHeader.EventDescriptor.Version = version
                drift.EventHeader.ProcessId = 41
                drift.EventHeader.TimeStamp = 110
                drift.UserDataLength = 16
                port.translate(ctypes.pointer(drift))

                if plane == "file_access":
                    reducer.file_begin(
                        pid=41, timestamp=115, irp=701, raw_identity="known"
                    )
                    reducer.file_complete(
                        timestamp=116,
                        irp=701,
                        status=0,
                        exact_pid_scope=False,
                    )
                else:
                    reducer.registry_operation(
                        pid=41,
                        timestamp=115,
                        raw_identity="known",
                        status=0,
                        operation="registry_query",
                    )
                reducer.mark_probe_available(plane)
                self.assertEqual(reducer._planes[plane].successes, 2)

                result = _finish(reducer)
                self.assertEqual(
                    result.planes[plane_index].outcome, "inconclusive"
                )
                self.assertEqual(
                    result.planes[plane_index].reason,
                    "collection_schema_unproved",
                )
                self.assertEqual(
                    result.planes[plane_index].reason,
                    trace._quality_failure_reason(result.quality[plane_index]),
                )

    def test_unknown_kernel_events_are_global_inside_the_qpc_window(self):
        reducer = _bound_reducer()
        port = object.__new__(trace._WindowsEtwPort)
        port._reducer = reducer
        port._integer = lambda *_args: (_ for _ in ()).throw(
            AssertionError("out-of-scope kernel payload decoded")
        )
        port._string = port._integer
        for provider, opcode, pid, timestamp in (
            (trace._FILE_PROVIDER_UUID, 65, 41, 99),
            (trace._FILE_PROVIDER_UUID, 99, 42, 110),
            (trace._REGISTRY_PROVIDER_UUID, 99, 41, 99),
            (trace._REGISTRY_PROVIDER_UUID, 99, 42, 110),
        ):
            record = trace._EVENT_RECORD()
            record.EventHeader.ProviderId = trace._GUID.from_uuid(provider)
            record.EventHeader.EventDescriptor.Opcode = opcode
            record.EventHeader.EventDescriptor.Version = 2
            record.EventHeader.ProcessId = pid
            record.EventHeader.TimeStamp = timestamp
            port.translate(ctypes.pointer(record))

        self.assertFalse(reducer._schema_proved["file_access"])
        self.assertFalse(reducer._schema_proved["registry_access"])

    def test_unknown_file_completion_version_uses_pending_target_irp(self):
        for header_pid in (0, 42):
            with self.subTest(header_pid=header_pid):
                reducer = _bound_reducer()
                reducer.file_begin(
                    pid=41, timestamp=105, irp=800, raw_identity="known"
                )
                port = object.__new__(trace._WindowsEtwPort)
                port._reducer = reducer
                port._integer = lambda *_args: (_ for _ in ()).throw(
                    AssertionError("unknown completion payload decoded")
                )
                port._string = port._integer
                record = trace._EVENT_RECORD()
                record.EventHeader.ProviderId = trace._GUID.from_uuid(
                    trace._FILE_PROVIDER_UUID
                )
                record.EventHeader.EventDescriptor.Opcode = 76
                record.EventHeader.EventDescriptor.Version = 1
                record.EventHeader.ProcessId = header_pid
                record.EventHeader.TimeStamp = 110
                record.UserDataLength = 16

                port.translate(ctypes.pointer(record))

                self.assertTrue(reducer.expects_file_completion())
                self.assertFalse(reducer._schema_proved["file_access"])
                self.assertIn(
                    "file_access", reducer._kernel_schema_uncertain
                )
                reducer.mark_probe_available("file_access")
                self.assertFalse(reducer._schema_proved["file_access"])
                result = _finish(reducer)
                self.assertEqual(
                    result.planes[0].reason,
                    "collection_schema_unproved",
                )

    def test_unknown_file_completion_without_pending_or_window_is_ignored(self):
        cases = (
            (0, 110, False),
            (42, 110, False),
            (0, 99, True),
        )
        for header_pid, timestamp, pending in cases:
            with self.subTest(
                header_pid=header_pid,
                timestamp=timestamp,
                pending=pending,
            ):
                reducer = _bound_reducer()
                if pending:
                    reducer.file_begin(
                        pid=41, timestamp=105, irp=801, raw_identity="known"
                    )
                port = object.__new__(trace._WindowsEtwPort)
                port._reducer = reducer
                port._integer = lambda *_args: (_ for _ in ()).throw(
                    AssertionError("out-of-scope completion payload decoded")
                )
                port._string = port._integer
                record = trace._EVENT_RECORD()
                record.EventHeader.ProviderId = trace._GUID.from_uuid(
                    trace._FILE_PROVIDER_UUID
                )
                record.EventHeader.EventDescriptor.Opcode = 76
                record.EventHeader.EventDescriptor.Version = 1
                record.EventHeader.ProcessId = header_pid
                record.EventHeader.TimeStamp = timestamp

                port.translate(ctypes.pointer(record))

                self.assertFalse(reducer._schema_proved["file_access"])
                self.assertIn(
                    "file_access", reducer._kernel_schema_uncertain
                )

    def test_exact_integer_width_drift_is_rejected(self):
        drift = trace._EVENT_RECORD()
        exact = object.__new__(trace._WindowsEtwPort)
        exact._property = lambda _record, _name: b"\x01\x00\x00\x00"
        self.assertIsNone(exact._integer(ctypes.pointer(drift), "IrpPtr", 8))
        self.assertEqual(exact._integer(ctypes.pointer(drift), "NtStatus", 4), 1)

    def test_kernel_templates_freeze_v2_v3_exact_names_widths_and_registry_semantics(self):
        self.assertTrue(trace._kernel_partition_is_complete())
        self.assertEqual(len(trace._KERNEL_EVENT_TEMPLATES), 59)
        summary = {
            (item.provider, item.opcode, item.version): item
            for item in trace._KERNEL_EVENT_TEMPLATES
        }
        self.assertEqual(
            {
                (item.opcode, item.version)
                for item in trace._KERNEL_EVENT_TEMPLATES
                if item.provider == trace._FILE_PROVIDER_UUID
            },
            {
                (opcode, version)
                for version in (2, 3)
                for opcode in (*trace._FILE_NAME_OPCODES, *range(64, 78))
            },
        )
        self.assertEqual(
            summary[(trace._FILE_PROVIDER_UUID, 64, 2)].fields,
            (
                ("IrpPtr", "uint", 8),
                ("TTID", "pointer", 8),
                ("FileObject", "uint", 8),
                ("CreateOptions", "uint", 4),
                ("FileAttributes", "uint", 4),
                ("ShareAccess", "uint", 4),
                ("OpenPath", "utf16", None),
            ),
        )
        self.assertEqual(
            summary[(trace._FILE_PROVIDER_UUID, 76, 2)].fields,
            (
                ("IrpPtr", "uint", 8),
                ("ExtraInfo", "uint", 8),
                ("NtStatus", "uint", 4),
            ),
        )
        self.assertEqual(
            summary[(trace._FILE_PROVIDER_UUID, 64, 3)].fields,
            (
                ("IrpPtr", "pointer", 8),
                ("FileObject", "pointer", 8),
                ("TTID", "uint", 4),
                ("CreateOptions", "uint", 4),
                ("FileAttributes", "uint", 4),
                ("ShareAccess", "uint", 4),
                ("OpenPath", "utf16", None),
            ),
        )
        self.assertEqual(
            summary[(trace._FILE_PROVIDER_UUID, 69, 3)].fields,
            (
                ("IrpPtr", "pointer", 8),
                ("FileObject", "pointer", 8),
                ("FileKey", "pointer", 8),
                ("ExtraInfo", "pointer", 8),
                ("TTID", "uint", 4),
                ("InfoClass", "uint", 4),
            ),
        )
        self.assertEqual(
            summary[(trace._FILE_PROVIDER_UUID, 76, 3)].fields,
            (
                ("IrpPtr", "pointer", 8),
                ("ExtraInfo", "pointer", 8),
                ("NtStatus", "uint", 4),
            ),
        )
        for opcode in trace._THREAD_OPCODES:
            item = summary[(trace._THREAD_PROVIDER_UUID, opcode, 3)]
            self.assertEqual(item.operation, "thread_lifecycle")
            self.assertEqual(
                item.field_contract, "consumed_attribution_subset"
            )
            self.assertEqual(item.fields, trace._THREAD_V3_FIELDS)
            self.assertNotIn(
                "ThreadName", {name for name, _kind, _width in item.fields}
            )
        registry_fields = (
            ("InitialTime", "sint", 8),
            ("Status", "uint", 4),
            ("Index", "uint", 4),
            ("KeyHandle", "uint", 8),
            ("KeyName", "utf16", None),
        )
        for opcode in range(10, 28):
            item = summary[(trace._REGISTRY_PROVIDER_UUID, opcode, 2)]
            self.assertEqual(
                item.operation, trace._REGISTRY_OPERATION_BY_OPCODE[opcode]
            )
            self.assertEqual(item.fields, registry_fields)

        original = trace._KERNEL_EVENT_TEMPLATES
        first = original[0]
        altered = trace._KernelEventTemplate(
            first.provider,
            first.opcode,
            first.version,
            first.plane,
            "tampered_operation",
            first.fields,
            first.field_contract,
        )
        reordered = trace._KernelEventTemplate(
            first.provider,
            first.opcode,
            first.version,
            first.plane,
            first.operation,
            tuple(reversed(first.fields)),
            first.field_contract,
        )
        wrong_kind = trace._KernelEventTemplate(
            first.provider,
            first.opcode,
            first.version,
            first.plane,
            first.operation,
            (
                (first.fields[0][0], "sint", first.fields[0][2]),
                *first.fields[1:],
            ),
            first.field_contract,
        )
        wrong_contract = trace._KernelEventTemplate(
            first.provider,
            first.opcode,
            first.version,
            first.plane,
            first.operation,
            first.fields,
            "consumed_attribution_subset",
        )
        extra = trace._KernelEventTemplate(
            trace.uuid.UUID("11111111-1111-1111-1111-111111111111"),
            1,
            1,
            "file_access",
            "file_name",
            first.fields,
        )
        try:
            for mutation in (
                original[:-1],
                (*original, first),
                (*original, extra),
                (altered, *original[1:]),
                (reordered, *original[1:]),
                (wrong_kind, *original[1:]),
                (wrong_contract, *original[1:]),
            ):
                with self.subTest(mutation=len(mutation)):
                    trace._KERNEL_EVENT_TEMPLATES = tuple(mutation)
                    self.assertFalse(trace._kernel_partition_is_complete())
        finally:
            trace._KERNEL_EVENT_TEMPLATES = original

    def test_image_load_requires_exact_payload_and_header_process_binding(self):
        def record(header_pid: int) -> trace._EVENT_RECORD:
            item = trace._EVENT_RECORD()
            item.EventHeader.ProviderId = trace._GUID.from_uuid(
                trace._IMAGE_PROVIDER_UUID
            )
            item.EventHeader.EventDescriptor.Opcode = 10
            item.EventHeader.EventDescriptor.Version = 2
            item.EventHeader.ProcessId = header_pid
            item.EventHeader.TimeStamp = 110
            item.UserDataLength = 16
            return item

        reducer = _bound_reducer()
        port = object.__new__(trace._WindowsEtwPort)
        port._reducer = reducer
        port._integer = lambda _record, name, width: (
            41 if name == "ProcessId" and width == 4 else None
        )
        port._string = lambda _record, name: (
            "known-successful-image" if name == "FileName" else None
        )
        port.translate(ctypes.pointer(record(41)))
        self.assertEqual(reducer._planes["dll_image_load"].successes, 2)

        mismatch = _bound_reducer()
        mismatch.mark_probe_available("dll_image_load")
        mismatch_port = object.__new__(trace._WindowsEtwPort)
        mismatch_port._reducer = mismatch
        mismatch_port._integer = port._integer
        mismatch_port._string = port._string
        mismatch_port.translate(ctypes.pointer(record(40)))
        result = _finish(mismatch)
        self.assertEqual(
            result.planes[1].reason, "collection_schema_unproved"
        )

    def test_manifest_providers_freeze_keywords_and_host_descriptor_identity(self):
        self.assertEqual(
            tuple(str(item.provider) for item in trace._MANIFEST_PROVIDERS),
            (
                "b059b83f-d946-4b13-87ca-4292839dc2f2",
                "4ee76bd8-3cf4-44a0-a0ac-3937643e37a3",
            ),
        )
        self.assertEqual(
            tuple(item.plane for item in trace._MANIFEST_PROVIDERS),
            ("dll_image_load", "code_integrity_policy"),
        )
        user_loader, code_integrity = trace._MANIFEST_PROVIDERS
        self.assertEqual(
            user_loader.any_keyword,
            0x40 | 0x200 | 0x2000000000000000 | 0x8000000000000000,
        )
        self.assertEqual(user_loader.descriptor_count, 12)
        self.assertEqual(
            user_loader.descriptor_digest,
            "b83c0f51b04fd4a2ca1536755e871ca45a2d4cf7bb3f9fcaf0268852f04eb411",
        )
        self.assertEqual(code_integrity.descriptor_count, 185)
        self.assertEqual(code_integrity.any_keyword, 0xC000000000000000)
        self.assertEqual(
            code_integrity.descriptor_digest,
            "838156db3a1d893d43e1cfd39d7908603ad38a5e33077cc0c1db2d753c6a55ea",
        )

    def test_user_loader_all_twelve_templates_are_exactly_partitioned(self):
        def field(name, kind="utf16", width=None):
            return (name, kind, width)

        expected = {
            1: ("ancillary", None, "FileName", None, (field("FileName"),)),
            2: (
                "fatal",
                "ProcessFileNamePath",
                None,
                None,
                (
                    field("ProcessFileNamePathLength", "uint", 2),
                    field("ProcessFileNamePath"),
                ),
            ),
            3: (
                "status_denial",
                "ProcessImagePath",
                "ImportDllName",
                "FailureReason",
                (
                    field("FailureReason", "uint32", 4),
                    field("ImportDllName"),
                    field("ProcessImagePath"),
                ),
            ),
            4: ("ancillary", "FileName", None, None, (field("FileName"),)),
            5: (
                "ancillary",
                None,
                "DLLName",
                None,
                (
                    field("ProcessId", "uint32", 4),
                    field("SuspendProcessRequest", "uint32", 4),
                    field("DLLName"),
                ),
            ),
            6: ("fatal", "FileName", None, None, (field("FileName"),)),
            7: ("fatal", "FileName", None, None, (field("FileName"),)),
            8: (
                "status_denial",
                None,
                "ImportDllName",
                "FailureReason",
                (
                    field("FailureReason", "uint32", 4),
                    field("ImportDllName"),
                    field("ExportModule"),
                ),
            ),
            9: ("fatal", "FileName", None, None, (field("FileName"),)),
            10: (
                "status_denial",
                "ProcessImagePath",
                "ImportDllName",
                "FailureReason",
                (
                    field("FailureReason", "uint32", 4),
                    field("ImportDllName"),
                    field("ProcessImagePath"),
                ),
            ),
            11: (
                "ancillary",
                "ProcessImagePath",
                "FoundDllPath",
                None,
                (
                    field("ProcessImagePath"),
                    field("CurDirDllPath"),
                    field("FoundDllPath"),
                ),
            ),
            12: (
                "fatal",
                "ProcessImagePath",
                "CurDirDllPath",
                None,
                (field("ProcessImagePath"), field("CurDirDllPath")),
            ),
        }
        actual = {
            item.event_id: (
                item.semantic,
                item.process_field,
                item.object_field,
                item.status_field,
                tuple(
                    (field.name, field.kind, field.width)
                    for field in item.fields
                ),
            )
            for item in trace._USER_LOADER_TEMPLATES
        }
        self.assertEqual(actual, expected)

    def test_ci_enforced_versions_and_status_spellings_are_closed(self):
        expected = {}
        expected_keys = {"denial": set(), "fatal": set(), "audit": set()}

        def add(
            category,
            keys,
            *,
            semantic,
            file_field,
            process_field=None,
            status_field=None,
            status_kind="hexint32",
        ):
            fields = []
            if file_field is not None:
                fields.append((file_field, "utf16", None))
            if process_field is not None:
                fields.append((process_field, "utf16", None))
            if status_field is not None:
                fields.append((status_field, status_kind, 4))
            signature = (
                semantic,
                process_field,
                file_field,
                status_field,
                tuple(fields),
            )
            for key in keys:
                self.assertNotIn(key, expected)
                expected[key] = signature
                expected_keys[category].add(key)

        add(
            "denial",
            ((3004, 1),),
            semantic="denial",
            file_field="FileNameBuffer",
            process_field="ProcessNameBuffer",
        )
        for event_id, status_kind in ((3033, "uint32"), (3063, "hexint32"), (3068, "uint32")):
            add(
                "denial",
                ((event_id, 0),),
                semantic="denial",
                file_field="FileNameBuffer",
                process_field="ProcessNameBuffer",
                status_field="Status",
                status_kind=status_kind,
            )
        for event_id, versions, uint_end in (
            (3077, range(6), 2),
            (3079, range(4), 2),
            (3081, range(13), 8),
        ):
            for version in versions:
                add(
                    "denial",
                    ((event_id, version),),
                    semantic="denial",
                    file_field="File Name",
                    process_field="Process Name",
                    status_field="Status",
                    status_kind="uint32" if version <= uint_end else "hexint32",
                )
        add(
            "denial",
            ((3086, 0),),
            semantic="denial",
            file_field="FileNameBuffer",
            process_field="ProcessNameBuffer",
            status_field="Status",
            status_kind="uint32",
        )
        add(
            "denial",
            ((3111, 0),),
            semantic="denial",
            file_field="FileNameBuffer",
            process_field="ProcessNameBuffer",
            status_field="Status",
        )
        add(
            "denial",
            ((3119, 0),),
            semantic="denial",
            file_field="File Name",
            process_field="Process Name",
            status_field="Status",
        )

        for event_id in (3002, 3023, 3036):
            add(
                "fatal",
                ((event_id, 0),),
                semantic="fatal",
                file_field="FileNameBuffer",
            )
            add(
                "fatal",
                ((event_id, 1),),
                semantic="fatal",
                file_field="FileNameBuffer",
                process_field="ProcessNameBuffer",
            )
        for event_id in (3004, 3026, 3072, 3073, 3104):
            add(
                "fatal",
                ((event_id, 0),),
                semantic="fatal",
                file_field="FileNameBuffer",
            )
        add(
            "fatal",
            ((3010, 0),),
            semantic="fatal",
            file_field="FileNameBuffer",
        )
        add(
            "fatal",
            ((3010, 1),),
            semantic="fatal",
            file_field="FileNameBuffer",
            status_field="Status",
        )
        add(
            "fatal",
            ((3074, 0),),
            semantic="global_fatal",
            file_field=None,
            status_field="Status",
        )
        add(
            "fatal",
            ((3087, 0),),
            semantic="fatal",
            file_field="FileNameBuffer",
            status_field="Status",
        )
        add(
            "fatal",
            ((3087, 1),),
            semantic="fatal",
            file_field="FileNameBuffer",
            process_field="ProcessNameBuffer",
            status_field="Status",
        )
        for event_id in (3106, 3107):
            add(
                "fatal",
                ((event_id, 0),),
                semantic="fatal",
                file_field="FileNameBuffer",
                status_field="Status",
            )
        add(
            "fatal",
            ((3092, 0), (3092, 1)),
            semantic="fatal",
            file_field="FileName",
            status_field="StatusCode",
        )
        add(
            "fatal",
            ((3114, 0),),
            semantic="fatal",
            file_field="FileName",
            process_field="ProcessName",
            status_field="Status",
        )
        add(
            "fatal",
            ((3118, 0),),
            semantic="fatal",
            file_field="FileNameBuffer",
            status_field="DefenderStatusCode",
        )

        for event_id in (3001, 3032):
            add(
                "audit",
                ((event_id, 0),),
                semantic="audit",
                file_field="FileNameBuffer",
            )
            add(
                "audit",
                ((event_id, 1),),
                semantic="audit",
                file_field="FileNameBuffer",
                process_field="ProcessNameBuffer",
            )
        add(
            "audit",
            ((3034, 0),),
            semantic="audit",
            file_field="FileNameBuffer",
            process_field="ProcessNameBuffer",
            status_field="Status",
            status_kind="uint32",
        )
        for event_id, status_kind in (
            (3064, "hexint32"),
            (3065, "hexint32"),
            (3066, "uint32"),
            (3067, "uint32"),
        ):
            add(
                "audit",
                ((event_id, 0),),
                semantic="audit",
                file_field="FileNameBuffer",
                process_field="ProcessNameBuffer",
                status_field="Status",
                status_kind=status_kind,
            )
        for event_id, versions, uint_end in (
            (3076, range(6), 2),
            (3078, range(4), 2),
            (3080, range(13), 8),
        ):
            for version in versions:
                add(
                    "audit",
                    ((event_id, version),),
                    semantic="audit",
                    file_field="File Name",
                    process_field="Process Name",
                    status_field="Status",
                    status_kind="uint32" if version <= uint_end else "hexint32",
                )
        add(
            "audit",
            ((3082, 0),),
            semantic="audit",
            file_field="FileNameBuffer",
        )
        for event_id, versions in (
            (3088, (0,)),
            (3090, (0,)),
            (3091, (0, 1)),
        ):
            add(
                "audit",
                tuple((event_id, version) for version in versions),
                semantic="audit",
                file_field="FileName",
                status_field="StatusCode",
            )
        add(
            "audit",
            tuple((3089, version) for version in range(4)),
            semantic="audit",
            file_field=None,
        )
        add(
            "audit",
            ((3112, 0),),
            semantic="audit",
            file_field="FileNameBuffer",
            process_field="ProcessNameBuffer",
            status_field="Status",
            status_kind="uint32",
        )
        add(
            "audit",
            ((3115, 0),),
            semantic="audit",
            file_field="FileName",
            process_field="ProcessName",
            status_field="Status",
        )
        add(
            "audit",
            ((3117, 0),),
            semantic="audit",
            file_field="File Name",
            process_field="Process Name",
        )

        categories = {
            "denial": trace._CI_DENIAL_TEMPLATES,
            "fatal": trace._CI_FATAL_TEMPLATES,
            "audit": trace._CI_AUDIT_TEMPLATES,
        }
        actual = {}
        for category, templates in categories.items():
            keys = {(item.event_id, item.version) for item in templates}
            self.assertEqual(keys, expected_keys[category])
            for item in templates:
                key = (item.event_id, item.version)
                self.assertNotIn(key, actual)
                actual[key] = (
                    item.semantic,
                    item.process_field,
                    item.object_field,
                    item.status_field,
                    tuple(
                        (field.name, field.kind, field.width)
                        for field in item.fields
                    ),
                )
        self.assertEqual(actual, expected)
        self.assertEqual(actual[(3114, 0)][0], "fatal")
        self.assertEqual(actual[(3089, 0)][1:4], (None, None, None))

        keys = [
            (item.provider, item.event_id, item.version)
            for item in trace._MANIFEST_TEMPLATES
        ]
        self.assertEqual(len(keys), len(set(keys)))
        for capability in trace._MANIFEST_CAPABILITIES:
            template_keys = {
                (item.event_id, item.version)
                for item in trace._MANIFEST_TEMPLATES
                if item.provider == capability.provider
            }
            self.assertTrue(capability.closure_frozen)
            self.assertEqual(set(capability.required_events), template_keys)

    def test_user_loader_descriptor_digest_covers_all_twelve_host_events(self):
        descriptors = (
            (1, 0, 17, 4, 0, 0, 0x2000000000000010),
            (2, 0, 17, 4, 0, 0, 0x2000000000000020),
            (3, 0, 17, 4, 0, 0, 0x2000000000000040),
            (4, 0, 17, 4, 0, 0, 0x2000000000000080),
            (5, 0, 17, 4, 0, 0, 0x2000000000000100),
            (6, 0, 17, 3, 0, 0, 0x2000000000000200),
            (7, 0, 17, 3, 0, 0, 0x2000000000000200),
            (8, 0, 17, 4, 0, 0, 0x2000000000000000),
            (9, 0, 17, 1, 0, 0, 0x2000000000000200),
            (10, 0, 9, 4, 0, 0, 0x8000000000000000),
            (11, 0, 9, 3, 0, 0, 0x8000000000000000),
            (12, 0, 9, 2, 0, 0, 0x8000000000000000),
        )
        self.assertEqual(
            trace._provider_descriptor_digest(
                trace._USER_LOADER_PROVIDER_UUID, descriptors
            ),
            trace._MANIFEST_PROVIDERS[0].descriptor_digest,
        )
        self.assertIsNone(
            trace._provider_descriptor_digest(
                trace._USER_LOADER_PROVIDER_UUID,
                (descriptors[0], descriptors[0]),
            )
        )

    def test_provider_descriptor_enumeration_fails_closed_on_every_drift(self):
        descriptor = (3, 0, 17, 4, 0, 0, 0x2000000000000040)
        provider_id = trace.uuid.UUID("b059b83f-d946-4b13-87ca-4292839dc2f2")
        digest = trace._provider_descriptor_digest(provider_id, (descriptor,))
        self.assertIsNotNone(digest)

        class FakeTdh:
            def __init__(
                self,
                descriptors,
                *,
                first_status=trace._ERROR_INSUFFICIENT_BUFFER,
                second_status=trace._ERROR_SUCCESS,
                reserved=0,
                second_size_delta=0,
            ):
                self.descriptors = descriptors
                self.first_status = first_status
                self.second_status = second_status
                self.reserved = reserved
                self.second_size_delta = second_size_delta

            def TdhEnumerateManifestProviderEvents(
                self, _guid, buffer, size_pointer
            ):
                size = ctypes.sizeof(trace._PROVIDER_EVENT_INFO_HEADER) + (
                    len(self.descriptors) * ctypes.sizeof(trace._EVENT_DESCRIPTOR)
                )
                size_pointer._obj.value = size
                if buffer is None:
                    return self.first_status
                if self.second_status != trace._ERROR_SUCCESS:
                    return self.second_status
                raw = ctypes.create_string_buffer(size)
                header = trace._PROVIDER_EVENT_INFO_HEADER.from_buffer(raw)
                header.NumberOfEvents = len(self.descriptors)
                header.Reserved = self.reserved
                offset = ctypes.sizeof(trace._PROVIDER_EVENT_INFO_HEADER)
                for index, values in enumerate(self.descriptors):
                    item = trace._EVENT_DESCRIPTOR.from_buffer(
                        raw,
                        offset + index * ctypes.sizeof(trace._EVENT_DESCRIPTOR),
                    )
                    (
                        item.Id,
                        item.Version,
                        item.Channel,
                        item.Level,
                        item.Opcode,
                        item.Task,
                        item.Keyword,
                    ) = values
                ctypes.memmove(buffer.value, raw, size)
                size_pointer._obj.value = size + self.second_size_delta
                return trace._ERROR_SUCCESS

        def binding(fake_tdh, *, count=1, expected_digest=digest):
            port = object.__new__(trace._WindowsEtwPort)
            port._tdh = fake_tdh
            provider = trace._ManifestProvider(
                provider_id,
                0,
                "dll_image_load",
                count,
                expected_digest,
            )
            return port._provider_descriptor_binding(provider)

        self.assertEqual(
            binding(FakeTdh((descriptor,))), "provider-sha256:" + digest
        )
        self.assertIsNone(
            binding(FakeTdh((descriptor,)), expected_digest="0" * 64)
        )
        self.assertIsNone(binding(FakeTdh((descriptor,)), count=2))
        self.assertIsNone(binding(FakeTdh((descriptor, descriptor)), count=2))
        self.assertIsNone(binding(FakeTdh((descriptor,), reserved=1)))
        self.assertIsNone(
            binding(FakeTdh((descriptor,), second_status=trace._ERROR_NOT_FOUND))
        )
        self.assertIsNone(
            binding(FakeTdh((descriptor,), second_size_delta=1))
        )

    @staticmethod
    def _manifest_record(
        provider: trace.uuid.UUID,
        event_id: int,
        version: int,
        *,
        pid: int,
        timestamp: int = 110,
    ) -> trace._EVENT_RECORD:
        record = trace._EVENT_RECORD()
        record.EventHeader.ProviderId = trace._GUID.from_uuid(provider)
        record.EventHeader.EventDescriptor.Id = event_id
        record.EventHeader.EventDescriptor.Version = version
        record.EventHeader.ProcessId = pid
        record.EventHeader.TimeStamp = timestamp
        record.UserDataLength = 32
        return record

    def test_user_loader_denial_requires_exact_child_pid_and_status(self):
        reducer = _bound_reducer()
        _close_manifest_window(reducer, "dll_image_load")
        port = object.__new__(trace._WindowsEtwPort)
        port._reducer = reducer
        port._integer = lambda _record, name, width: (
            0xC0000022 if name == "FailureReason" and width == 4 else None
        )
        port._string = lambda _record, name: {
            "ImportDllName": "dependency.dll",
            "ProcessImagePath": "python.exe",
        }.get(name)

        wrong_pid = self._manifest_record(
            trace._USER_LOADER_PROVIDER_UUID, 3, 0, pid=42
        )
        port.translate(ctypes.pointer(wrong_pid))
        self.assertFalse(reducer._planes["dll_image_load"].denials)

        exact = self._manifest_record(
            trace._USER_LOADER_PROVIDER_UUID, 3, 0, pid=41
        )
        port.translate(ctypes.pointer(exact))
        result = _finish(reducer)
        self.assertEqual(
            reducer._planes["dll_image_load"].denials,
            {(_DEPENDENCY_REF, "image_map")},
        )
        self.assertEqual(result.planes[1].outcome, "inconclusive")
        self.assertEqual(result.planes[1].reason, "plane_scope_unproved")

    def test_ci_uses_payload_binding_not_header_pid_and_3114_is_never_denial(self):
        reducer = _bound_reducer()
        _close_manifest_window(reducer, "code_integrity_policy")
        port = object.__new__(trace._WindowsEtwPort)
        port._reducer = reducer
        port._integer = lambda _record, name, width: (
            0xC0000022 if name == "Status" and width == 4 else None
        )
        port._string = lambda _record, name: {
            "FileNameBuffer": "runtime.dll",
            "ProcessNameBuffer": "python.exe",
        }.get(name)
        record = self._manifest_record(
            trace._CI_PROVIDER_UUID, 3033, 0, pid=999
        )

        port.translate(ctypes.pointer(record))
        result = _finish(reducer)
        self.assertEqual(
            reducer._planes["code_integrity_policy"].denials,
            {(_OBJECTS["code_integrity_policy"][0], "image_policy_validate")},
        )
        self.assertEqual(result.planes[3].outcome, "inconclusive")
        self.assertEqual(result.planes[3].reason, "plane_scope_unproved")

        single = _bound_reducer()
        _close_manifest_window(single, "code_integrity_policy")
        single_port = object.__new__(trace._WindowsEtwPort)
        single_port._reducer = single
        single_port._integer = port._integer
        single_port._string = lambda _record, name: {
            "FileName": "runtime.dll",
            "ProcessName": "python.exe",
        }.get(name)
        event_3114 = self._manifest_record(
            trace._CI_PROVIDER_UUID, 3114, 0, pid=41
        )
        single_port.translate(ctypes.pointer(event_3114))
        single_result = _finish(single)
        self.assertEqual(single_result.planes[3].outcome, "inconclusive")
        self.assertIn(
            "observation_ambiguous",
            single._planes["code_integrity_policy"].reasons,
        )
        self.assertEqual(
            single_result.planes[3].reason, "plane_scope_unproved"
        )

    def test_unpartitioned_ci_event_invalidates_full_negative_closure(self):
        reducer = _bound_reducer()
        _close_manifest_window(reducer, "code_integrity_policy")
        port = object.__new__(trace._WindowsEtwPort)
        port._reducer = reducer
        port._integer = lambda *_args: (_ for _ in ()).throw(
            AssertionError("unpartitioned payload decoded")
        )
        port._string = port._integer
        record = self._manifest_record(
            trace._CI_PROVIDER_UUID, 3038, 2, pid=999
        )

        port.translate(ctypes.pointer(record))
        result = _finish(reducer)

        self.assertEqual(result.planes[3].outcome, "inconclusive")
        self.assertEqual(
            result.planes[3].reason, "collection_schema_unproved"
        )

    def test_native_port_has_no_file_trace_subprocess_or_channel_mutation_route(self):
        source = inspect.getsource(trace)
        for forbidden in (
            "subprocess",
            "logman",
            "tracerpt",
            "wpr.exe",
            "EnableLog",
            "wevtutil",
            "LogFileName =",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("StartTraceW", source)
        self.assertIn("OpenTraceW", source)
        self.assertIn("ProcessTrace", source)
        self.assertIn("EnableTraceEx2", source)


class RuntimeTraceKernelRouteProofTests(unittest.TestCase):
    @staticmethod
    def _classic_record(
        provider,
        opcode: int,
        version: int,
        *,
        pid: int,
        timestamp: int,
    ) -> trace._EVENT_RECORD:
        item = trace._EVENT_RECORD()
        item.EventHeader.ProviderId = trace._GUID.from_uuid(provider)
        item.EventHeader.EventDescriptor.Id = trace._CLASSIC_EVENT_ID
        item.EventHeader.EventDescriptor.Opcode = opcode
        item.EventHeader.EventDescriptor.Version = version
        item.EventHeader.Flags = (
            trace._EVENT_HEADER_FLAG_CLASSIC_HEADER
            | trace._EVENT_HEADER_FLAG_64_BIT_HEADER
        )
        item.EventHeader.ProcessId = pid
        item.EventHeader.TimeStamp = timestamp
        item.UserDataLength = 32
        return item

    @staticmethod
    def _port_with_values(reducer, values_for_template):
        port = object.__new__(trace._WindowsEtwPort)
        port._reducer = reducer
        port._kernel_values = (
            lambda _record, template, **_kwargs: values_for_template(template)
        )
        return port

    def test_exact_silent_kernel_window_closes_only_file_and_registry(self):
        result = _finish_kernel(_kernel_bound_reducer())

        self.assertEqual(
            tuple((item.plane, item.outcome) for item in result.planes),
            (
                ("file_access", "observed_no_denial"),
                ("dll_image_load", "inconclusive"),
                ("registry_access", "observed_no_denial"),
                ("code_integrity_policy", "inconclusive"),
            ),
        )
        self.assertTrue(result.quality[0].plane_scope_complete)
        self.assertTrue(result.quality[2].plane_scope_complete)
        self.assertFalse(result.quality[1].plane_scope_complete)
        self.assertFalse(result.quality[3].plane_scope_complete)

        zero_duration = _kernel_bound_reducer()
        zero_duration.end_window(100)
        self.assertIsNone(zero_duration._qpc_end)
        self.assertIn(
            "observation_ambiguous", zero_duration._global_reasons
        )

    def test_session_snapshot_drift_loss_missing_stage_and_order_fail_closed(self):
        bad_snapshots = (
            _snapshot(flags=trace.EXACT_KERNEL_ENABLE_FLAGS ^ 1),
            _snapshot(mode=trace.EXACT_LOG_FILE_MODE ^ 1),
            _snapshot(context=0),
            _snapshot(events_lost=1),
            _snapshot(log_buffers_lost=1),
            _snapshot(realtime_buffers_lost=1),
        )
        for snapshot in bad_snapshots:
            with self.subTest(snapshot=snapshot):
                reducer = trace._TraceReducer(_binding())
                reducer.prebind_subject(pid=41, primary_thread_id=71)
                reducer.record_kernel_session_snapshot(
                    stage="start", snapshot=snapshot
                )
                reducer.bind(
                    pid=41,
                    qpc_start=100,
                    initial_image_ref=_OBJECTS["dll_image_load"][0],
                    primary_thread_id=71,
                )
                reducer.mark_probe_available("file_access")
                reducer.mark_probe_available("registry_access")
                result = _finish_kernel(reducer)
                self.assertEqual(result.planes[0].outcome, "inconclusive")
                self.assertEqual(result.planes[2].outcome, "inconclusive")

        missing = _kernel_bound_reducer()
        missing.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
        missing.end_window(200)
        missing.mark_cleanup_proved()
        missing_result = missing.finish()
        self.assertFalse(missing_result.quality[0].plane_scope_complete)
        self.assertFalse(missing_result.quality[2].plane_scope_complete)

        wrong_order = _kernel_bound_reducer()
        wrong_order.record_kernel_session_snapshot(
            stage="stop", snapshot=_snapshot()
        )
        wrong_result = _finish_kernel(wrong_order)
        self.assertFalse(wrong_result.quality[0].plane_scope_complete)
        self.assertFalse(wrong_result.quality[2].plane_scope_complete)

    def test_thread_epochs_are_order_independent_and_foreign_events_close(self):
        for delivery in ("start_first", "file_first"):
            with self.subTest(delivery=delivery):
                reducer = _kernel_bound_reducer(_multi_binding())
                if delivery == "start_first":
                    reducer.thread_event(
                        opcode=1, pid=41, thread_id=99, timestamp=110
                    )
                    reducer.file_begin(
                        timestamp=110,
                        irp=1,
                        raw_identity="file-a",
                        thread_id=99,
                        header_pid=0xFFFFFFFF,
                        file_object=7,
                    )
                else:
                    reducer.file_begin(
                        timestamp=110,
                        irp=1,
                        raw_identity="file-a",
                        thread_id=99,
                        header_pid=0xFFFFFFFF,
                        file_object=7,
                    )
                    reducer.thread_event(
                        opcode=1, pid=41, thread_id=99, timestamp=110
                    )
                self.assertFalse(
                    reducer._correlation_complete["file_access"]
                )

        exact = _kernel_bound_reducer(_multi_binding())
        exact.thread_event(opcode=1, pid=41, thread_id=99, timestamp=109)
        exact.file_begin(
            timestamp=110,
            irp=2,
            raw_identity="file-a",
            thread_id=99,
            header_pid=0xFFFFFFFF,
            file_object=8,
        )
        exact.file_complete(
            timestamp=111,
            irp=2,
            status=trace._STATUS_ACCESS_DENIED,
            exact_pid_scope=False,
        )
        exact_result = _finish_kernel(exact)
        self.assertEqual(exact_result.planes[0].outcome, "denial")
        self.assertTrue(exact_result.quality[0].correlation_complete)

        foreign = _kernel_bound_reducer(_multi_binding())
        foreign.thread_event(opcode=3, pid=42, thread_id=99, timestamp=90)
        foreign.file_begin(
            timestamp=110,
            irp=3,
            raw_identity=None,
            thread_id=99,
            header_pid=0xFFFFFFFF,
            file_object=8,
            file_key=9,
            operation="file_read",
        )
        self.assertFalse(foreign._deferred_file_begins)
        self.assertTrue(foreign._correlation_complete["file_access"])

    def test_thread_reuse_conflict_regression_unknown_and_cap_are_sticky(self):
        conflict = _kernel_bound_reducer()
        conflict.thread_event(opcode=1, pid=42, thread_id=71, timestamp=110)
        self.assertFalse(conflict._correlation_complete["file_access"])

        reuse = _kernel_bound_reducer()
        reuse.thread_event(opcode=1, pid=41, thread_id=99, timestamp=105)
        reuse.thread_event(opcode=2, pid=41, thread_id=99, timestamp=110)
        reuse.thread_event(opcode=1, pid=41, thread_id=99, timestamp=111)
        self.assertTrue(reuse._correlation_complete["file_access"])

        old_epoch = _kernel_bound_reducer(_multi_binding())
        old_epoch.file_begin(
            timestamp=110,
            irp=1,
            raw_identity="file-a",
            thread_id=99,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        old_epoch.thread_event(opcode=2, pid=42, thread_id=99, timestamp=110)
        old_epoch.thread_event(opcode=1, pid=41, thread_id=99, timestamp=110)
        self.assertFalse(old_epoch._correlation_complete["file_access"])

        regression = _kernel_bound_reducer()
        regression.thread_event(opcode=3, pid=42, thread_id=80, timestamp=120)
        regression.thread_event(opcode=3, pid=42, thread_id=81, timestamp=119)
        self.assertFalse(regression._correlation_complete["file_access"])

        unknown = _kernel_bound_reducer(_multi_binding())
        unknown.file_begin(
            timestamp=110,
            irp=1,
            raw_identity="file-a",
            thread_id=99,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        unknown_result = _finish_kernel(unknown)
        self.assertEqual(unknown_result.planes[0].outcome, "inconclusive")
        self.assertFalse(unknown_result.quality[0].correlation_complete)

        old_cap = trace.MAX_TRACKED_THREADS
        try:
            trace.MAX_TRACKED_THREADS = 2
            capped = _kernel_bound_reducer()
            capped.thread_event(opcode=3, pid=42, thread_id=80, timestamp=90)
            capped.thread_event(opcode=3, pid=42, thread_id=81, timestamp=91)
            self.assertTrue(capped._overflowed)
        finally:
            trace.MAX_TRACKED_THREADS = old_cap

    def test_every_file_route_reports_its_exact_operation(self):
        operations = tuple(sorted(trace._OPERATIONS["file_access"]))
        for denied in (False, True):
            for index, operation in enumerate(operations):
                with self.subTest(operation=operation, denied=denied):
                    reducer = _kernel_bound_reducer(_multi_binding())
                    file_key = 20 + index
                    if operation != "file_create":
                        reducer.file_name(
                            timestamp=105,
                            file_object=file_key,
                            raw_identity="file-a",
                        )
                    reducer.file_begin(
                        timestamp=110,
                        irp=100 + index,
                        raw_identity=(
                            "file-a" if operation == "file_create" else None
                        ),
                        thread_id=71,
                        header_pid=0xFFFFFFFF,
                        file_object=1000 + index,
                        file_key=(0 if operation == "file_create" else file_key),
                        operation=operation,
                    )
                    reducer.file_complete(
                        timestamp=111,
                        irp=100 + index,
                        status=(
                            trace._STATUS_ACCESS_DENIED
                            if denied
                            else 0xC0000034
                        ),
                        exact_pid_scope=False,
                    )
                    result = _finish_kernel(reducer)
                    self.assertEqual(
                        result.planes[0].outcome,
                        "denial" if denied else "observed_no_denial",
                    )
                    self.assertEqual(result.planes[0].operation, operation)

    def test_file_pointer_generations_prevent_stale_resurrection(self):
        stale = _kernel_bound_reducer(_multi_binding())
        stale.file_begin(
            timestamp=105,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        stale.file_complete(
            timestamp=106,
            irp=1,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        stale.file_begin(
            timestamp=107,
            irp=2,
            raw_identity="unknown",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        stale.file_complete(
            timestamp=108,
            irp=2,
            status=trace._STATUS_ACCESS_DENIED,
            exact_pid_scope=False,
        )
        stale.file_begin(
            timestamp=109,
            irp=3,
            raw_identity=None,
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
            operation="file_read",
        )
        stale.file_complete(
            timestamp=110,
            irp=3,
            status=trace._STATUS_ACCESS_DENIED,
            exact_pid_scope=False,
        )
        self.assertEqual(stale._planes["file_access"].denials, set())
        self.assertIsNone(stale._file_objects[7].object_ref)

        cross = _kernel_bound_reducer(_multi_binding())
        cross.file_begin(
            timestamp=104,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        cross.file_complete(
            timestamp=105,
            irp=1,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        cross.file_name(timestamp=106, file_object=8, raw_identity="file-a")
        cross.file_name(
            timestamp=107,
            file_object=8,
            raw_identity="ignored",
            remove=True,
        )
        cross.file_begin(
            timestamp=108,
            irp=2,
            raw_identity=None,
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
            file_key=8,
            operation="file_read",
        )
        cross.file_complete(
            timestamp=109,
            irp=2,
            status=trace._STATUS_ACCESS_DENIED,
            exact_pid_scope=False,
        )
        self.assertEqual(cross._planes["file_access"].denials, set())

        reverse = _kernel_bound_reducer(_multi_binding())
        reverse.file_begin(
            timestamp=110,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=9,
        )
        reverse.file_begin(
            timestamp=110,
            irp=2,
            raw_identity="file-b",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=9,
        )
        reverse.file_complete(
            timestamp=111,
            irp=2,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        reverse.file_complete(
            timestamp=112,
            irp=1,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        self.assertEqual(reverse._file_objects[9].object_ref, _FILE_OTHER_REF)
        self.assertFalse(reverse._correlation_complete["file_access"])

        conflict = _kernel_bound_reducer(_multi_binding())
        conflict.file_begin(
            timestamp=105,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        conflict.file_complete(
            timestamp=106,
            irp=1,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        conflict.file_begin(
            timestamp=107,
            irp=2,
            raw_identity="file-b",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        self.assertNotIn(2, conflict._pending)
        self.assertIsNone(conflict._file_objects[7].object_ref)
        self.assertFalse(conflict._correlation_complete["file_access"])

    def test_filekey_equal_and_older_lifecycle_cannot_resurrect(self):
        reducer = _kernel_bound_reducer(_multi_binding())
        reducer.file_name(timestamp=105, file_object=7, raw_identity="file-a")
        reducer.file_name(
            timestamp=110,
            file_object=7,
            raw_identity="ignored",
            remove=True,
        )
        reducer.file_name(timestamp=110, file_object=7, raw_identity="file-a")
        reducer.file_name(timestamp=109, file_object=7, raw_identity="file-a")
        self.assertIsNone(reducer._file_keys[7].object_ref)
        self.assertFalse(reducer._correlation_complete["file_access"])

    def test_every_registry_access_route_reports_exact_operation_and_default_value(self):
        access_opcodes = (*range(10, 22), 26)
        for opcode in access_opcodes:
            with self.subTest(opcode=opcode):
                reducer = _kernel_bound_reducer(_multi_binding())
                if opcode not in trace._REGISTRY_DIRECT_KEY_OPCODES:
                    reducer.registry_lifecycle(
                        opcode=22,
                        timestamp=105,
                        key_handle=7,
                        raw_identity="reg-a",
                        status=trace._STATUS_SUCCESS,
                        initial_time=0,
                    )
                reducer.registry_operation(
                    pid=41,
                    timestamp=110,
                    raw_identity=(
                        "reg-a"
                        if opcode in trace._REGISTRY_DIRECT_KEY_OPCODES
                        else ""
                    ),
                    status=trace._STATUS_ACCESS_DENIED,
                    operation=trace._REGISTRY_OPERATION_BY_OPCODE[opcode],
                    opcode=opcode,
                    key_handle=7,
                    initial_time=109,
                )
                result = _finish_kernel(reducer)
                self.assertEqual(result.planes[2].outcome, "denial")
                self.assertEqual(
                    result.planes[2].operation,
                    trace._REGISTRY_OPERATION_BY_OPCODE[opcode],
                )

    def test_registry_direct_key_routes_conflict_check_but_never_fallback(self):
        for opcode in (17, 20, 21, 26):
            with self.subTest(opcode=opcode):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.registry_lifecycle(
                    opcode=22,
                    timestamp=105,
                    key_handle=7,
                    raw_identity="reg-a",
                    status=trace._STATUS_SUCCESS,
                    initial_time=0,
                )
                reducer.registry_operation(
                    pid=41,
                    timestamp=110,
                    raw_identity="reg-b",
                    status=trace._STATUS_ACCESS_DENIED,
                    operation=trace._REGISTRY_OPERATION_BY_OPCODE[opcode],
                    opcode=opcode,
                    key_handle=7,
                    initial_time=109,
                )
                self.assertFalse(
                    reducer._correlation_complete["registry_access"]
                )
                self.assertEqual(
                    reducer._planes["registry_access"].denials, set()
                )

    def test_registry_direct_open_respects_pre_operation_generation_barrier(self):
        reducer = _kernel_bound_reducer(_multi_binding())
        reducer.registry_lifecycle(
            opcode=22,
            timestamp=110,
            key_handle=7,
            raw_identity="unknown",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        reducer.registry_operation(
            pid=41,
            timestamp=110,
            raw_identity="reg-a",
            status=trace._STATUS_SUCCESS,
            operation="registry_open",
            opcode=11,
            key_handle=7,
            initial_time=109,
        )
        reducer.registry_operation(
            pid=41,
            timestamp=120,
            raw_identity="",
            status=trace._STATUS_ACCESS_DENIED,
            operation="registry_query_value",
            opcode=16,
            key_handle=7,
            initial_time=119,
        )
        self.assertIsNone(reducer._registry_handles[7].object_ref)
        self.assertFalse(reducer._correlation_complete["registry_access"])
        self.assertEqual(reducer._planes["registry_access"].denials, set())

    def test_registry_rebind_and_setup_lifecycle_never_inherit_stale_kcb(self):
        for status in (trace._STATUS_ACCESS_DENIED, 0xC0000034):
            with self.subTest(status=status):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.registry_lifecycle(
                    opcode=22,
                    timestamp=105,
                    key_handle=7,
                    raw_identity="reg-a",
                    status=trace._STATUS_SUCCESS,
                    initial_time=0,
                )
                reducer.registry_operation(
                    pid=41,
                    timestamp=110,
                    raw_identity="unknown",
                    status=status,
                    operation="registry_open",
                    opcode=11,
                    key_handle=7,
                    initial_time=109,
                )
                reducer.registry_operation(
                    pid=41,
                    timestamp=111,
                    raw_identity="",
                    status=trace._STATUS_ACCESS_DENIED,
                    operation="registry_query_value",
                    opcode=16,
                    key_handle=7,
                    initial_time=110,
                )
                self.assertEqual(
                    reducer._planes["registry_access"].denials, set()
                )
                self.assertIsNone(
                    reducer._registry_handles[7].object_ref
                )

        conflict = _kernel_bound_reducer(_multi_binding())
        conflict.registry_lifecycle(
            opcode=22,
            timestamp=105,
            key_handle=7,
            raw_identity="reg-a",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        conflict.registry_operation(
            pid=41,
            timestamp=110,
            raw_identity="reg-b",
            status=trace._STATUS_SUCCESS,
            operation="registry_open",
            opcode=11,
            key_handle=7,
            initial_time=109,
        )
        self.assertIsNone(conflict._registry_handles[7].object_ref)
        self.assertFalse(conflict._correlation_complete["registry_access"])

        setup = trace._TraceReducer(_multi_binding())
        setup.prebind_subject(pid=41, primary_thread_id=71)
        setup.record_kernel_session_snapshot(stage="start", snapshot=_snapshot())
        setup.registry_lifecycle(
            opcode=24,
            timestamp=90,
            key_handle=7,
            raw_identity="reg-a",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        setup.registry_lifecycle(
            opcode=23,
            timestamp=95,
            key_handle=7,
            raw_identity="",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        setup.bind(
            pid=41,
            qpc_start=100,
            initial_image_ref=_OBJECTS["dll_image_load"][0],
            primary_thread_id=71,
        )
        setup.mark_probe_available("file_access")
        setup.mark_probe_available("registry_access")
        setup.registry_operation(
            pid=41,
            timestamp=110,
            raw_identity="",
            status=trace._STATUS_ACCESS_DENIED,
            operation="registry_query_value",
            opcode=16,
            key_handle=7,
            initial_time=109,
        )
        setup_result = _finish_kernel(setup)
        self.assertEqual(setup_result.planes[2].outcome, "inconclusive")
        self.assertEqual(setup._planes["registry_access"].denials, set())

    def test_registry_lifecycle_generations_and_empty_names_are_exact(self):
        for map_opcode in (22, 24, 25):
            with self.subTest(map_opcode=map_opcode):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.registry_lifecycle(
                    opcode=map_opcode,
                    timestamp=105,
                    key_handle=7,
                    raw_identity="reg-a",
                    status=trace._STATUS_SUCCESS,
                    initial_time=0,
                )
                self.assertEqual(
                    reducer._registry_handles[7].object_ref,
                    _OBJECTS["registry_access"][0],
                )
        for remove_opcode in (23, 27):
            with self.subTest(remove_opcode=remove_opcode):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.registry_lifecycle(
                    opcode=22,
                    timestamp=105,
                    key_handle=7,
                    raw_identity="reg-a",
                    status=trace._STATUS_SUCCESS,
                    initial_time=0,
                )
                reducer.registry_lifecycle(
                    opcode=remove_opcode,
                    timestamp=106,
                    key_handle=7,
                    raw_identity="",
                    status=trace._STATUS_SUCCESS,
                    initial_time=0,
                )
                self.assertIsNone(
                    reducer._registry_handles[7].object_ref
                )
                self.assertEqual(
                    reducer._planes["registry_access"].denials, set()
                )

        equal = _kernel_bound_reducer(_multi_binding())
        equal.registry_lifecycle(
            opcode=22,
            timestamp=110,
            key_handle=7,
            raw_identity="reg-a",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        equal.registry_lifecycle(
            opcode=23,
            timestamp=110,
            key_handle=7,
            raw_identity="",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        equal.registry_lifecycle(
            opcode=24,
            timestamp=109,
            key_handle=7,
            raw_identity="reg-a",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        self.assertIsNone(equal._registry_handles[7].object_ref)
        self.assertFalse(equal._correlation_complete["registry_access"])

    def test_registry_lifecycle_non_success_never_promotes_or_removes_authority(self):
        for opcode in trace._REGISTRY_LIFECYCLE_OPCODES:
            with self.subTest(opcode=opcode):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.registry_lifecycle(
                    opcode=22,
                    timestamp=105,
                    key_handle=7,
                    raw_identity="reg-a",
                    status=trace._STATUS_SUCCESS,
                    initial_time=0,
                )

                def lifecycle_failure_values(template):
                    values = {
                        name: ("reg-b" if kind == "utf16" else 0)
                        for name, kind, _width in template.fields
                    }
                    values.update(
                        InitialTime=1,
                        Status=0xC0000034,
                        KeyHandle=7,
                    )
                    return values

                port = self._port_with_values(
                    reducer, lifecycle_failure_values
                )
                record = self._classic_record(
                    trace._REGISTRY_PROVIDER_UUID,
                    opcode,
                    2,
                    pid=0,
                    timestamp=110,
                )
                port.translate(ctypes.pointer(record))
                reducer.registry_operation(
                    pid=41,
                    timestamp=120,
                    raw_identity="",
                    status=trace._STATUS_ACCESS_DENIED,
                    operation="registry_query_value",
                    opcode=16,
                    key_handle=7,
                    initial_time=119,
                )
                self.assertIsNone(
                    reducer._registry_handles[7].object_ref
                )
                self.assertFalse(
                    reducer._correlation_complete["registry_access"]
                )
                self.assertEqual(
                    reducer._planes["registry_access"].denials, set()
                )

        zero = _kernel_bound_reducer(_multi_binding())

        def zero_handle_values(template):
            return {
                name: (
                    "reg-a"
                    if kind == "utf16"
                    else (0xC0000034 if name == "Status" else 0)
                )
                for name, kind, _width in template.fields
            }

        port = self._port_with_values(zero, zero_handle_values)
        record = self._classic_record(
            trace._REGISTRY_PROVIDER_UUID,
            22,
            2,
            pid=0,
            timestamp=110,
        )
        port.translate(ctypes.pointer(record))
        self.assertNotIn(0, zero._registry_handles)
        self.assertFalse(zero._correlation_complete["registry_access"])

        sentinel = _kernel_bound_reducer(_multi_binding())
        sentinel.registry_lifecycle(
            opcode=22,
            timestamp=105,
            key_handle=7,
            raw_identity="reg-a",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        self.assertEqual(
            sentinel._registry_handles[7].object_ref,
            _OBJECTS["registry_access"][0],
        )

        for malformed_initial in (-1, 200):
            with self.subTest(malformed_initial=malformed_initial):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.registry_lifecycle(
                    opcode=22,
                    timestamp=104,
                    key_handle=7,
                    raw_identity="reg-a",
                    status=trace._STATUS_SUCCESS,
                    initial_time=0,
                )
                reducer.registry_lifecycle(
                    opcode=22,
                    timestamp=105,
                    key_handle=7,
                    raw_identity="reg-b",
                    status=trace._STATUS_SUCCESS,
                    initial_time=malformed_initial,
                )
                reducer.registry_operation(
                    pid=41,
                    timestamp=110,
                    raw_identity="",
                    status=trace._STATUS_ACCESS_DENIED,
                    operation="registry_query_value",
                    opcode=16,
                    key_handle=7,
                    initial_time=109,
                )
                self.assertIsNone(
                    reducer._registry_handles[7].object_ref
                )
                self.assertFalse(
                    reducer._correlation_complete["registry_access"]
                )
                self.assertEqual(
                    reducer._planes["registry_access"].denials, set()
                )

    def test_file_rundown_36_tombstones_prebind_and_only_strict_reuse_revives(self):
        for reuse in (False, True):
            with self.subTest(reuse=reuse):
                reducer = trace._TraceReducer(_multi_binding())
                reducer.prebind_subject(pid=41, primary_thread_id=71)
                reducer.record_kernel_session_snapshot(
                    stage="start", snapshot=_snapshot()
                )

                def name_values(template):
                    return {
                        name: ("file-a" if kind == "utf16" else 8)
                        for name, kind, _width in template.fields
                    }

                port = self._port_with_values(reducer, name_values)
                for opcode, timestamp in ((0, 90), (36, 95)):
                    record = self._classic_record(
                        trace._FILE_PROVIDER_UUID,
                        opcode,
                        2,
                        pid=0,
                        timestamp=timestamp,
                    )
                    port.translate(ctypes.pointer(record))
                reducer.bind(
                    pid=41,
                    qpc_start=100,
                    initial_image_ref=_OBJECTS["dll_image_load"][0],
                    primary_thread_id=71,
                )
                reducer.mark_probe_available("file_access")
                reducer.mark_probe_available("registry_access")
                if reuse:
                    record = self._classic_record(
                        trace._FILE_PROVIDER_UUID,
                        0,
                        2,
                        pid=0,
                        timestamp=105,
                    )
                    port.translate(ctypes.pointer(record))
                reducer.file_begin(
                    timestamp=110,
                    irp=1,
                    raw_identity=None,
                    thread_id=71,
                    header_pid=0xFFFFFFFF,
                    file_object=7,
                    file_key=8,
                    operation="file_read",
                )
                reducer.file_complete(
                    timestamp=111,
                    irp=1,
                    status=trace._STATUS_ACCESS_DENIED,
                    exact_pid_scope=False,
                )
                self.assertEqual(
                    reducer._planes["file_access"].denials,
                    (
                        {(_OBJECTS["file_access"][0], "file_read")}
                        if reuse
                        else set()
                    ),
                )

    def test_full_file_and_registry_native_partitions_translate(self):
        for version in (2, 3):
            for opcode in trace._FILE_NAME_OPCODES:
                with self.subTest(
                    provider="file-name", version=version, opcode=opcode
                ):
                    reducer = _kernel_bound_reducer(_multi_binding())
                    if opcode in {35, 36}:
                        reducer.file_name(
                            timestamp=105,
                            file_object=8,
                            raw_identity="file-a",
                        )

                    def name_values(template):
                        return {
                            name: ("file-a" if kind == "utf16" else 8)
                            for name, kind, _width in template.fields
                        }

                    port = self._port_with_values(reducer, name_values)
                    record = self._classic_record(
                        trace._FILE_PROVIDER_UUID,
                        opcode,
                        version,
                        pid=0,
                        timestamp=110,
                    )
                    port.translate(ctypes.pointer(record))
                    self.assertIn(8, reducer._file_keys)
                    self.assertEqual(
                        reducer._file_keys[8].object_ref,
                        (
                            None
                            if opcode in {35, 36}
                            else _OBJECTS["file_access"][0]
                        ),
                    )

            for opcode in (*range(64, 76), 77):
                with self.subTest(
                    provider="file", version=version, opcode=opcode
                ):
                    reducer = _kernel_bound_reducer(_multi_binding())
                    irp = 1000 + opcode
                    if opcode != 64:
                        reducer.file_name(
                            timestamp=105,
                            file_object=8,
                            raw_identity="file-a",
                        )

                    def file_values(template, *, _irp=irp):
                        values = {
                            name: ("" if kind == "utf16" else 1)
                            for name, kind, _width in template.fields
                        }
                        values.update(
                            IrpPtr=_irp,
                            TTID=71,
                            FileObject=7,
                            FileKey=8,
                            OpenPath="file-a",
                            NtStatus=0xC0000034,
                        )
                        return values

                    port = self._port_with_values(reducer, file_values)
                    begin = self._classic_record(
                        trace._FILE_PROVIDER_UUID,
                        opcode,
                        version,
                        pid=0xFFFFFFFF,
                        timestamp=110,
                    )
                    port.translate(ctypes.pointer(begin))
                    end = self._classic_record(
                        trace._FILE_PROVIDER_UUID,
                        76,
                        version,
                        pid=0xFFFFFFFF,
                        timestamp=111,
                    )
                    port.translate(ctypes.pointer(end))
                    result = _finish_kernel(reducer)
                    self.assertEqual(
                        result.planes[0].outcome, "observed_no_denial"
                    )
                    self.assertEqual(
                        result.planes[0].operation,
                        trace._FILE_OPERATION_BY_OPCODE[opcode],
                    )

        for opcode in (*range(10, 22), 26):
            with self.subTest(provider="registry", opcode=opcode):
                reducer = _kernel_bound_reducer(_multi_binding())
                if opcode not in {10, 11, 12}:
                    reducer.registry_lifecycle(
                        opcode=22,
                        timestamp=105,
                        key_handle=7,
                        raw_identity="reg-a",
                        status=trace._STATUS_SUCCESS,
                        initial_time=0,
                    )

                def registry_values(template, *, _opcode=opcode):
                    values = {
                        name: ("" if kind == "utf16" else 0)
                        for name, kind, _width in template.fields
                    }
                    values.update(
                        InitialTime=109,
                        Status=trace._STATUS_ACCESS_DENIED,
                        KeyHandle=7,
                        KeyName=(
                            "reg-a"
                            if _opcode in trace._REGISTRY_DIRECT_KEY_OPCODES
                            else ""
                        ),
                    )
                    return values

                port = self._port_with_values(reducer, registry_values)
                record = self._classic_record(
                    trace._REGISTRY_PROVIDER_UUID,
                    opcode,
                    2,
                    pid=41,
                    timestamp=110,
                )
                port.translate(ctypes.pointer(record))
                result = _finish_kernel(reducer)
                self.assertEqual(result.planes[2].outcome, "denial")
                self.assertEqual(
                    result.planes[2].operation,
                    trace._REGISTRY_OPERATION_BY_OPCODE[opcode],
                )

        for opcode in trace._REGISTRY_LIFECYCLE_OPCODES:
            with self.subTest(provider="registry-lifecycle", opcode=opcode):
                reducer = _kernel_bound_reducer(_multi_binding())
                if opcode in {23, 27}:
                    reducer.registry_lifecycle(
                        opcode=22,
                        timestamp=105,
                        key_handle=7,
                        raw_identity="reg-a",
                        status=trace._STATUS_SUCCESS,
                        initial_time=0,
                    )

                def lifecycle_values(template, *, _opcode=opcode):
                    return {
                        name: (
                            ("" if _opcode in {23, 27} else "reg-a")
                            if kind == "utf16"
                            else (7 if name == "KeyHandle" else 0)
                        )
                        for name, kind, _width in template.fields
                    }

                port = self._port_with_values(reducer, lifecycle_values)
                record = self._classic_record(
                    trace._REGISTRY_PROVIDER_UUID,
                    opcode,
                    2,
                    pid=0,
                    timestamp=110,
                )
                port.translate(ctypes.pointer(record))
                self.assertEqual(
                    reducer._planes["registry_access"].denials, set()
                )

    def test_file_v3_missing_fields_and_pointer_width_drift_fail_closed(self):
        templates = tuple(
            item
            for item in trace._KERNEL_EVENT_TEMPLATES
            if item.provider == trace._FILE_PROVIDER_UUID and item.version == 3
        )
        self.assertEqual(len(templates), 18)
        for template in templates:
            first_name, _first_kind, first_width = template.fields[0]
            self.assertEqual(first_width, 8)
            for fault in ("missing", "width"):
                with self.subTest(
                    opcode=template.opcode, version=3, fault=fault
                ):
                    reducer = _kernel_bound_reducer(_multi_binding())
                    port = object.__new__(trace._WindowsEtwPort)
                    port._reducer = reducer

                    def prop(_record, name):
                        width = next(
                            width
                            for field_name, _kind, width in template.fields
                            if field_name == name
                        )
                        if name == first_name and fault == "missing":
                            return None
                        if name == first_name and fault == "width":
                            width = 4
                        return (1).to_bytes(width, "little")

                    port._property = prop
                    port._string = lambda *_args, **_kwargs: "file-a"
                    record = self._classic_record(
                        trace._FILE_PROVIDER_UUID,
                        template.opcode,
                        3,
                        pid=41,
                        timestamp=110,
                    )
                    port.translate(ctypes.pointer(record))
                    self.assertFalse(reducer._schema_proved["file_access"])
                    self.assertIn(
                        "file_access", reducer._kernel_schema_uncertain
                    )

    def test_file_v2_ttid_pointer_slot_is_exact_uint32(self):
        for opcode in (64, 65, 67, 69, 72):
            template = next(
                item
                for item in trace._KERNEL_EVENT_TEMPLATES
                if item.provider == trace._FILE_PROVIDER_UUID
                and item.opcode == opcode
                and item.version == 2
            )
            self.assertIn(("TTID", "pointer", 8), template.fields)
            for fault in (None, "width4", "upper32"):
                with self.subTest(opcode=opcode, fault=fault):
                    reducer = _kernel_bound_reducer(_multi_binding())
                    if opcode != 64:
                        reducer.file_name(
                            timestamp=105,
                            file_object=8,
                            raw_identity="file-a",
                        )
                    irp = 2000 + opcode
                    port = object.__new__(trace._WindowsEtwPort)
                    port._reducer = reducer

                    def prop(_record, name):
                        width = next(
                            width
                            for field_name, _kind, width in template.fields
                            if field_name == name
                        )
                        values = {
                            "IrpPtr": irp,
                            "TTID": 71,
                            "FileObject": 7,
                            "FileKey": 8,
                        }
                        value = values.get(name, 1)
                        if name == "TTID" and fault == "width4":
                            width = 4
                        elif name == "TTID" and fault == "upper32":
                            value |= 1 << 32
                        return value.to_bytes(width, "little")

                    port._property = prop
                    port._string = lambda *_args, **_kwargs: "file-a"
                    record = self._classic_record(
                        trace._FILE_PROVIDER_UUID,
                        opcode,
                        2,
                        pid=0xFFFFFFFF,
                        timestamp=110,
                    )
                    port.translate(ctypes.pointer(record))
                    if fault is None:
                        self.assertIn(irp, reducer._pending)
                        self.assertTrue(
                            reducer._schema_proved["file_access"]
                        )
                    else:
                        self.assertNotIn(irp, reducer._pending)
                        self.assertFalse(
                            reducer._schema_proved["file_access"]
                        )

    def test_classic_header_unknown_global_and_header_pid_guards_are_closed(self):
        variants = (
            (0, trace._EVENT_HEADER_FLAG_CLASSIC_HEADER | trace._EVENT_HEADER_FLAG_64_BIT_HEADER),
            (trace._CLASSIC_EVENT_ID, trace._EVENT_HEADER_FLAG_CLASSIC_HEADER),
            (
                trace._CLASSIC_EVENT_ID,
                trace._EVENT_HEADER_FLAG_CLASSIC_HEADER
                | trace._EVENT_HEADER_FLAG_64_BIT_HEADER
                | trace._EVENT_HEADER_FLAG_32_BIT_HEADER,
            ),
        )
        for event_id, flags in variants:
            with self.subTest(event_id=event_id, flags=flags):
                reducer = _kernel_bound_reducer()
                port = self._port_with_values(
                    reducer,
                    lambda _template: (_ for _ in ()).throw(
                        AssertionError("bad classic header decoded")
                    ),
                )
                record = self._classic_record(
                    trace._FILE_PROVIDER_UUID,
                    64,
                    2,
                    pid=0xFFFFFFFF,
                    timestamp=110,
                )
                record.EventHeader.EventDescriptor.Id = event_id
                record.EventHeader.Flags = flags
                port.translate(ctypes.pointer(record))
                self.assertFalse(reducer._schema_proved["file_access"])

        for provider, opcode, plane in (
            (trace._FILE_PROVIDER_UUID, 99, "file_access"),
            (trace._REGISTRY_PROVIDER_UUID, 29, "registry_access"),
        ):
            with self.subTest(provider=provider):
                reducer = _kernel_bound_reducer()
                port = self._port_with_values(
                    reducer,
                    lambda _template: (_ for _ in ()).throw(
                        AssertionError("unknown classic event decoded")
                    ),
                )
                record = self._classic_record(
                    provider, opcode, 2, pid=0, timestamp=110
                )
                port.translate(ctypes.pointer(record))
                self.assertFalse(reducer._schema_proved[plane])

        sentinel = _kernel_bound_reducer(_multi_binding())
        sentinel.file_begin(
            timestamp=110,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        self.assertIn(1, sentinel._pending)
        conflicting = _kernel_bound_reducer(_multi_binding())
        conflicting.file_begin(
            timestamp=110,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=42,
            file_object=7,
        )
        self.assertNotIn(1, conflicting._pending)
        self.assertFalse(conflicting._correlation_complete["file_access"])

    def test_registry_empty_utf16_is_valid_only_when_call_site_allows_it(self):
        port = object.__new__(trace._WindowsEtwPort)
        record = trace._EVENT_RECORD()
        port._property = lambda _record, _name: b"\0\0"
        self.assertIsNone(port._string(ctypes.pointer(record), "KeyName"))
        self.assertEqual(
            port._string(
                ctypes.pointer(record), "KeyName", allow_empty=True
            ),
            "",
        )

    def test_file_opend_requires_strict_qpc_causality_before_status(self):
        for status in (trace._STATUS_ACCESS_DENIED, trace._STATUS_SUCCESS):
            for completion_time in (109, 110):
                with self.subTest(status=status, completion_time=completion_time):
                    reducer = _kernel_bound_reducer(_multi_binding())
                    reducer.file_begin(
                        timestamp=110,
                        irp=7,
                        raw_identity="file-a",
                        thread_id=71,
                        header_pid=0xFFFFFFFF,
                        file_object=7,
                    )
                    reducer.file_complete(
                        timestamp=completion_time,
                        irp=7,
                        status=status,
                        exact_pid_scope=False,
                    )
                    self.assertEqual(
                        reducer._planes["file_access"].denials, set()
                    )
                    self.assertEqual(
                        reducer._planes["file_access"].successes, 0
                    )
                    self.assertFalse(
                        reducer._correlation_complete["file_access"]
                    )
                    self.assertIsNone(
                        reducer._file_objects[7].object_ref
                    )

        reused = _kernel_bound_reducer(_multi_binding())
        reused.file_begin(
            timestamp=105,
            irp=9,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        reused.file_complete(
            timestamp=106,
            irp=9,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        reused.file_begin(
            timestamp=110,
            irp=9,
            raw_identity="file-b",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=8,
        )
        reused.file_complete(
            timestamp=107,
            irp=9,
            status=trace._STATUS_ACCESS_DENIED,
            exact_pid_scope=False,
        )
        self.assertNotIn(
            (_FILE_OTHER_REF, "file_create"),
            reused._planes["file_access"].denials,
        )
        self.assertFalse(reused._correlation_complete["file_access"])

    def test_kernel_access_qpc_boundaries_are_strict(self):
        file_start = _kernel_bound_reducer(_multi_binding())
        file_start.file_begin(
            timestamp=100,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        self.assertFalse(file_start._correlation_complete["file_access"])
        self.assertFalse(file_start._pending)

        file_end = _kernel_bound_reducer(_multi_binding())
        file_end.file_begin(
            timestamp=110,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        file_end.record_kernel_session_snapshot(
            stage="pre_stop", snapshot=_snapshot()
        )
        file_end.end_window(120)
        file_end.file_complete(
            timestamp=120,
            irp=1,
            status=trace._STATUS_ACCESS_DENIED,
            exact_pid_scope=False,
        )
        self.assertFalse(file_end._correlation_complete["file_access"])
        self.assertEqual(file_end._planes["file_access"].denials, set())

        for timestamp, initial_time, qpc_end in (
            (101, 100, None),
            (120, 110, 120),
        ):
            with self.subTest(
                plane="registry",
                timestamp=timestamp,
                initial_time=initial_time,
            ):
                reducer = _kernel_bound_reducer(_multi_binding())
                if qpc_end is not None:
                    reducer.record_kernel_session_snapshot(
                        stage="pre_stop", snapshot=_snapshot()
                    )
                    reducer.end_window(qpc_end)
                reducer.registry_operation(
                    pid=41,
                    timestamp=timestamp,
                    raw_identity="reg-a",
                    status=trace._STATUS_ACCESS_DENIED,
                    operation="registry_delete",
                    opcode=12,
                    key_handle=7,
                    initial_time=initial_time,
                )
                self.assertFalse(
                    reducer._correlation_complete["registry_access"]
                )
                self.assertEqual(
                    reducer._planes["registry_access"].denials, set()
                )

    def test_registry_initialtime_interval_is_signed_and_closed(self):
        port = object.__new__(trace._WindowsEtwPort)
        record = trace._EVENT_RECORD()
        port._property = lambda _record, _name: (-7).to_bytes(
            8, "little", signed=True
        )
        self.assertEqual(
            port._signed_integer(ctypes.pointer(record), "InitialTime", 8),
            -7,
        )

        for initial_time in (-1, 99, 100, 111):
            with self.subTest(initial_time=initial_time):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.registry_operation(
                    pid=41,
                    timestamp=110,
                    raw_identity="reg-a",
                    status=trace._STATUS_ACCESS_DENIED,
                    operation="registry_delete",
                    opcode=12,
                    key_handle=7,
                    initial_time=initial_time,
                )
                self.assertFalse(
                    reducer._correlation_complete["registry_access"]
                )
                self.assertEqual(
                    reducer._planes["registry_access"].denials, set()
                )
        for initial_time in (109, 110):
            with self.subTest(valid_initial_time=initial_time):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.registry_operation(
                    pid=41,
                    timestamp=110,
                    raw_identity="reg-a",
                    status=trace._STATUS_ACCESS_DENIED,
                    operation="registry_delete",
                    opcode=12,
                    key_handle=7,
                    initial_time=initial_time,
                )
                result = _finish_kernel(reducer)
                self.assertEqual(result.planes[2].outcome, "denial")

    def test_lifecycle_edges_and_target_uses_are_order_independent(self):
        for order in ("access_first", "delete_first"):
            with self.subTest(plane="registry", order=order):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.registry_lifecycle(
                    opcode=22,
                    timestamp=105,
                    key_handle=7,
                    raw_identity="reg-a",
                    status=trace._STATUS_SUCCESS,
                    initial_time=0,
                )

                def access():
                    reducer.registry_operation(
                        pid=41,
                        timestamp=110,
                        raw_identity="",
                        status=trace._STATUS_ACCESS_DENIED,
                        operation="registry_query_value",
                        opcode=16,
                        key_handle=7,
                        initial_time=109,
                    )

                def delete():
                    reducer.registry_lifecycle(
                        opcode=23,
                        timestamp=110,
                        key_handle=7,
                        raw_identity="",
                        status=trace._STATUS_SUCCESS,
                        initial_time=0,
                    )

                (access(), delete()) if order == "access_first" else (delete(), access())
                result = _finish_kernel(reducer)
                self.assertEqual(result.planes[2].outcome, "inconclusive")
                self.assertFalse(result.quality[2].correlation_complete)

        for order in ("access_first", "end_first", "name_delete_first"):
            with self.subTest(plane="file", order=order):
                reducer = _kernel_bound_reducer(_multi_binding())
                reducer.file_name(
                    timestamp=105,
                    file_object=8,
                    raw_identity="file-a",
                )

                def access():
                    reducer.file_begin(
                        timestamp=110,
                        irp=1,
                        raw_identity=None,
                        thread_id=71,
                        header_pid=0xFFFFFFFF,
                        file_object=7,
                        file_key=8,
                        operation="file_read",
                    )

                if order == "access_first":
                    access()
                    reducer.thread_event(
                        opcode=2, pid=41, thread_id=71, timestamp=110
                    )
                elif order == "end_first":
                    reducer.thread_event(
                        opcode=2, pid=41, thread_id=71, timestamp=110
                    )
                    access()
                else:
                    reducer.file_name(
                        timestamp=110,
                        file_object=8,
                        raw_identity="",
                        remove=True,
                    )
                    access()
                self.assertFalse(
                    reducer._correlation_complete["file_access"]
                )

    def test_late_setup_and_drain_lifecycle_records_are_not_dropped(self):
        registry = trace._TraceReducer(_multi_binding())
        registry.prebind_subject(pid=41, primary_thread_id=71)
        registry.record_kernel_session_snapshot(
            stage="start", snapshot=_snapshot()
        )
        registry.registry_lifecycle(
            opcode=24,
            timestamp=90,
            key_handle=7,
            raw_identity="reg-a",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        registry.bind(
            pid=41,
            qpc_start=100,
            initial_image_ref=_OBJECTS["dll_image_load"][0],
            primary_thread_id=71,
        )
        registry.mark_probe_available("file_access")
        registry.mark_probe_available("registry_access")
        registry.registry_lifecycle(
            opcode=23,
            timestamp=95,
            key_handle=7,
            raw_identity="",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        registry.registry_operation(
            pid=41,
            timestamp=110,
            raw_identity="",
            status=trace._STATUS_ACCESS_DENIED,
            operation="registry_query_value",
            opcode=16,
            key_handle=7,
            initial_time=109,
        )
        self.assertEqual(registry._planes["registry_access"].denials, set())

        file_route = trace._TraceReducer(_multi_binding())
        file_route.prebind_subject(pid=41, primary_thread_id=71)
        file_route.record_kernel_session_snapshot(
            stage="start", snapshot=_snapshot()
        )
        file_route.file_name(
            timestamp=90, file_object=8, raw_identity="file-a"
        )
        file_route.bind(
            pid=41,
            qpc_start=100,
            initial_image_ref=_OBJECTS["dll_image_load"][0],
            primary_thread_id=71,
        )
        file_route.mark_probe_available("file_access")
        file_route.mark_probe_available("registry_access")
        file_route.file_name(
            timestamp=95,
            file_object=8,
            raw_identity="",
            remove=True,
        )
        file_route.file_begin(
            timestamp=110,
            irp=1,
            raw_identity=None,
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
            file_key=8,
            operation="file_read",
        )
        self.assertIsNone(file_route._pending[1].object_ref)

        file_route.record_kernel_session_snapshot(
            stage="pre_stop", snapshot=_snapshot()
        )
        file_route.end_window(200)
        file_route.file_name(
            timestamp=150,
            file_object=8,
            raw_identity="file-a",
        )
        self.assertEqual(
            file_route._file_keys[8].object_ref,
            _OBJECTS["file_access"][0],
        )

    def test_unresolved_generation_watermarks_block_equal_old_bindings(self):
        file_route = _kernel_bound_reducer(_multi_binding())
        file_route.file_name(
            timestamp=110, file_object=7, raw_identity="unknown"
        )
        file_route.file_name(
            timestamp=110, file_object=7, raw_identity="file-a"
        )
        self.assertNotIn(7, file_route._file_keys)
        self.assertFalse(file_route._correlation_complete["file_access"])

        registry = _kernel_bound_reducer(_multi_binding())
        registry.registry_lifecycle(
            opcode=22,
            timestamp=110,
            key_handle=7,
            raw_identity="unknown",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        registry.registry_lifecycle(
            opcode=24,
            timestamp=110,
            key_handle=7,
            raw_identity="reg-a",
            status=trace._STATUS_SUCCESS,
            initial_time=0,
        )
        self.assertNotIn(7, registry._registry_handles)
        self.assertFalse(registry._correlation_complete["registry_access"])

    def test_cleanup_preserves_file_lifetime_until_close(self):
        reducer = _kernel_bound_reducer(_multi_binding())
        reducer.file_name(timestamp=104, file_object=8, raw_identity="file-a")
        reducer.file_begin(
            timestamp=105,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        reducer.file_complete(
            timestamp=106,
            irp=1,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        reducer.file_begin(
            timestamp=107,
            irp=2,
            raw_identity=None,
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
            file_key=8,
            operation="file_cleanup",
        )
        reducer.file_complete(
            timestamp=108,
            irp=2,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        self.assertEqual(
            reducer._file_objects[7].object_ref,
            _OBJECTS["file_access"][0],
        )
        self.assertEqual(
            reducer._file_keys[8].object_ref,
            _OBJECTS["file_access"][0],
        )
        reducer.file_begin(
            timestamp=109,
            irp=3,
            raw_identity=None,
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
            file_key=8,
            operation="file_close",
        )
        reducer.file_complete(
            timestamp=110,
            irp=3,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        self.assertIsNone(reducer._file_objects[7].object_ref)
        self.assertEqual(
            reducer._file_keys[8].object_ref,
            _OBJECTS["file_access"][0],
        )

    def test_foreign_file_conflicts_and_zero_thread_ids_are_unrelated(self):
        reducer = _kernel_bound_reducer(_multi_binding())
        reducer.thread_event(opcode=3, pid=42, thread_id=99, timestamp=90)
        reducer.file_name(timestamp=104, file_object=8, raw_identity="file-b")
        reducer.file_begin(
            timestamp=105,
            irp=1,
            raw_identity="file-a",
            thread_id=71,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        reducer.file_complete(
            timestamp=106,
            irp=1,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        reducer.file_begin(
            timestamp=110,
            irp=2,
            raw_identity="file-b",
            thread_id=99,
            header_pid=0xFFFFFFFF,
            file_object=7,
            file_key=8,
            operation="file_read",
        )
        self.assertTrue(reducer._correlation_complete["file_access"])

        reducer.thread_event(opcode=3, pid=0, thread_id=0, timestamp=111)
        reducer.file_begin(
            timestamp=112,
            irp=3,
            raw_identity="file-b",
            thread_id=0,
            header_pid=0,
            file_object=7,
        )
        self.assertTrue(reducer._correlation_complete["file_access"])
        reducer.file_begin(
            timestamp=113,
            irp=4,
            raw_identity="file-a",
            thread_id=0,
            header_pid=41,
            file_object=7,
        )
        self.assertFalse(reducer._correlation_complete["file_access"])

        contradiction = _kernel_bound_reducer(_multi_binding())
        contradiction.thread_event(
            opcode=3, pid=42, thread_id=99, timestamp=90
        )
        contradiction.file_begin(
            timestamp=110,
            irp=1,
            raw_identity="file-a",
            thread_id=99,
            header_pid=41,
            file_object=7,
        )
        self.assertNotIn(1, contradiction._pending)
        self.assertFalse(
            contradiction._correlation_complete["file_access"]
        )

        for opcode in (1, 3):
            with self.subTest(deferred_foreign_opcode=opcode):
                deferred = _kernel_bound_reducer(_multi_binding())
                deferred.file_begin(
                    timestamp=110,
                    irp=1,
                    raw_identity="file-a",
                    thread_id=99,
                    header_pid=41,
                    file_object=7,
                )
                self.assertEqual(len(deferred._deferred_file_begins), 1)
                deferred.thread_event(
                    opcode=opcode,
                    pid=42,
                    thread_id=99,
                    timestamp=105,
                )
                self.assertEqual(deferred._deferred_file_begins, [])
                self.assertFalse(
                    deferred._correlation_complete["file_access"]
                )

        reuse = _kernel_bound_reducer()
        reuse.thread_event(opcode=3, pid=42, thread_id=99, timestamp=90)
        reuse.thread_event(opcode=2, pid=42, thread_id=99, timestamp=110)
        reuse.thread_event(opcode=1, pid=43, thread_id=99, timestamp=120)
        self.assertTrue(reuse._correlation_complete["file_access"])

    def test_foreign_file_lifetime_edges_tombstone_global_fileobject(self):
        target_deferred = _kernel_bound_reducer(_multi_binding())
        target_deferred.file_begin(
            timestamp=110,
            irp=1,
            raw_identity="file-a",
            thread_id=99,
            header_pid=0xFFFFFFFF,
            file_object=7,
        )
        self.assertEqual(len(target_deferred._deferred_file_begins), 1)
        self.assertIsNone(target_deferred._file_objects[7].object_ref)
        target_deferred.thread_event(
            opcode=1, pid=41, thread_id=99, timestamp=105
        )
        self.assertEqual(target_deferred._deferred_file_begins, [])
        self.assertIn(1, target_deferred._pending)
        target_deferred.file_complete(
            timestamp=111,
            irp=1,
            status=trace._STATUS_SUCCESS,
            exact_pid_scope=False,
        )
        self.assertEqual(
            target_deferred._file_objects[7].object_ref,
            _OBJECTS["file_access"][0],
        )
        self.assertTrue(
            target_deferred._correlation_complete["file_access"]
        )

        for version in (2, 3):
            for operation in ("file_create", "file_close"):
                for owner_order in ("known", "deferred", "zero"):
                    opcode = 64 if operation == "file_create" else 66
                    with self.subTest(
                        version=version,
                        operation=operation,
                        owner_order=owner_order,
                    ):
                        reducer = _kernel_bound_reducer(_multi_binding())
                        if owner_order == "known":
                            reducer.thread_event(
                                opcode=3,
                                pid=42,
                                thread_id=99,
                                timestamp=90,
                            )
                        reducer.file_begin(
                            timestamp=105,
                            irp=1,
                            raw_identity="file-a",
                            thread_id=71,
                            header_pid=0xFFFFFFFF,
                            file_object=7,
                        )
                        reducer.file_complete(
                            timestamp=106,
                            irp=1,
                            status=trace._STATUS_SUCCESS,
                            exact_pid_scope=False,
                        )

                        def foreign_values(template):
                            values = {
                                name: ("" if kind == "utf16" else 1)
                                for name, kind, _width in template.fields
                            }
                            values.update(
                                IrpPtr=2,
                                TTID=(0 if owner_order == "zero" else 99),
                                FileObject=7,
                                FileKey=8,
                                OpenPath="file-b",
                            )
                            return values

                        port = self._port_with_values(
                            reducer, foreign_values
                        )
                        record = self._classic_record(
                            trace._FILE_PROVIDER_UUID,
                            opcode,
                            version,
                            pid=(
                                42
                                if owner_order == "zero"
                                else 0xFFFFFFFF
                            ),
                            timestamp=110,
                        )
                        port.translate(ctypes.pointer(record))
                        if owner_order == "deferred":
                            self.assertEqual(
                                len(reducer._deferred_file_begins), 1
                            )
                            reducer.thread_event(
                                opcode=3,
                                pid=42,
                                thread_id=99,
                                timestamp=107,
                            )
                        self.assertEqual(reducer._deferred_file_begins, [])
                        self.assertNotIn(2, reducer._pending)
                        self.assertIsNone(
                            reducer._file_objects[7].object_ref
                        )
                        self.assertTrue(
                            reducer._correlation_complete["file_access"]
                        )
                        reducer.file_begin(
                            timestamp=120,
                            irp=3,
                            raw_identity=None,
                            thread_id=71,
                            header_pid=0xFFFFFFFF,
                            file_object=7,
                            operation="file_read",
                        )
                        reducer.file_complete(
                            timestamp=121,
                            irp=3,
                            status=trace._STATUS_ACCESS_DENIED,
                            exact_pid_scope=False,
                        )
                        self.assertEqual(
                            reducer._planes["file_access"].denials, set()
                        )

                        if operation == "file_close":
                            equal_qpc = _kernel_bound_reducer(
                                _multi_binding()
                            )
                            if owner_order == "known":
                                equal_qpc.thread_event(
                                    opcode=3,
                                    pid=42,
                                    thread_id=99,
                                    timestamp=90,
                                )
                            equal_qpc.file_begin(
                                timestamp=105,
                                irp=1,
                                raw_identity="file-a",
                                thread_id=71,
                                header_pid=0xFFFFFFFF,
                                file_object=7,
                            )
                            equal_qpc.file_complete(
                                timestamp=106,
                                irp=1,
                                status=trace._STATUS_SUCCESS,
                                exact_pid_scope=False,
                            )
                            equal_qpc.file_begin(
                                timestamp=110,
                                irp=3,
                                raw_identity=None,
                                thread_id=71,
                                header_pid=0xFFFFFFFF,
                                file_object=7,
                                operation="file_read",
                            )
                            equal_port = self._port_with_values(
                                equal_qpc, foreign_values
                            )
                            equal_record = self._classic_record(
                                trace._FILE_PROVIDER_UUID,
                                opcode,
                                version,
                                pid=(
                                    42
                                    if owner_order == "zero"
                                    else 0xFFFFFFFF
                                ),
                                timestamp=110,
                            )
                            equal_port.translate(
                                ctypes.pointer(equal_record)
                            )
                            if owner_order == "deferred":
                                equal_qpc.thread_event(
                                    opcode=3,
                                    pid=42,
                                    thread_id=99,
                                    timestamp=107,
                                )
                            equal_qpc.file_complete(
                                timestamp=111,
                                irp=3,
                                status=trace._STATUS_ACCESS_DENIED,
                                exact_pid_scope=False,
                            )
                            self.assertFalse(
                                equal_qpc._correlation_complete[
                                    "file_access"
                                ]
                            )

    def test_thread_v3_tail_and_pointer_width_are_required(self):
        widths = {name: width for name, _kind, width in trace._THREAD_V3_FIELDS}
        for fault in ("missing_tail", "short_pointer"):
            with self.subTest(fault=fault):
                reducer = _kernel_bound_reducer()
                port = object.__new__(trace._WindowsEtwPort)
                port._reducer = reducer

                def prop(_record, name):
                    if fault == "missing_tail" and name == "ThreadFlags":
                        return None
                    width = widths[name]
                    if fault == "short_pointer" and name == "StackBase":
                        width = 4
                    value = 41 if name == "ProcessId" else 99
                    return int(value).to_bytes(width, "little")

                port._property = prop
                record = self._classic_record(
                    trace._THREAD_PROVIDER_UUID,
                    1,
                    3,
                    pid=999,
                    timestamp=110,
                )
                port.translate(ctypes.pointer(record))
                self.assertFalse(reducer._schema_proved["file_access"])

    def test_stop_flush_callbacks_inside_end_qpc_are_reduced(self):
        class DrainPort(_FakePort):
            def __init__(self) -> None:
                super().__init__()
                self.collector = None

            def stop_session(self, owned_session_handle: int):
                reducer = self.collector._reducer
                reducer.file_begin(
                    timestamp=110,
                    irp=1,
                    raw_identity="known",
                    pid=41,
                )
                reducer.file_complete(
                    timestamp=111,
                    irp=1,
                    status=trace._STATUS_ACCESS_DENIED,
                    exact_pid_scope=False,
                )
                reducer.registry_operation(
                    pid=41,
                    timestamp=112,
                    raw_identity="known",
                    status=trace._STATUS_ACCESS_DENIED,
                    operation="registry_delete",
                    opcode=12,
                    key_handle=7,
                    initial_time=109,
                )
                return super().stop_session(owned_session_handle)

        port = DrainPort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        port.collector = collector
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
        result = collector.stop()
        self.assertEqual(result.planes[0].outcome, "denial")
        self.assertEqual(result.planes[2].outcome, "denial")

    def test_process_cancelled_and_buffer_callback_false_are_not_coverage(self):
        class CancelledPort(_FakePort):
            def process(self, trace_handle: int) -> int:
                self.events.append("process")
                self.stopped.wait(2.0)
                self.events.append("process_end")
                return 1223

        port = CancelledPort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
        result = collector.stop()
        self.assertFalse(result.cleanup_proved)
        self.assertTrue(result.has_inconclusive)
        buffer_source = inspect.getsource(trace._WindowsEtwPort.open_consumer)
        self.assertIn("return 1", buffer_source)
        self.assertNotIn("return 0", buffer_source)

    def test_consumer_early_success_before_stop_is_never_silent_coverage(self):
        class EarlyPort(_FakePort):
            def process(self, trace_handle: int) -> int:
                self.events.append("process")
                return 0 if trace_handle == 55 else -1

        port = EarlyPort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        with self.assertRaises(trace.RuntimeTraceError) as raised:
            collector.start_for_suspended_child(
                process_id=41,
                process_handle=900,
                thread_handle=901,
                initial_image_object_ref=_OBJECTS["dll_image_load"][0],
            )
        self.assertIn(
            raised.exception.code, {"trace_unavailable", "trace_cleanup_unproved"}
        )
        self.assertTrue(collector._consumer_returned_early)

    def test_consumer_return_between_precheck_and_stop_is_sticky(self):
        class RacingPort(_FakePort):
            def __init__(self) -> None:
                super().__init__()
                self.release = threading.Event()
                self.query_count = 0

            def process(self, trace_handle: int) -> int:
                self.events.append("process")
                self.release.wait(2.0)
                self.events.append("process_end")
                return 0

            def query_session(self, owned_session_handle: int):
                self.query_count += 1
                if self.query_count == 2:
                    self.release.set()
                return super().query_session(owned_session_handle)

        port = RacingPort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)
        collector.start_for_suspended_child(
            process_id=41,
            process_handle=900,
            thread_handle=901,
            initial_image_object_ref=_OBJECTS["dll_image_load"][0],
        )
        collector.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
        port.release.set()
        collector._thread.join(2.0)
        result = collector.stop()
        self.assertFalse(result.cleanup_proved)
        self.assertTrue(result.has_inconclusive)


if __name__ == "__main__":
    unittest.main()
