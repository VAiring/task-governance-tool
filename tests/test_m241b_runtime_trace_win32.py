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


def _finish(reducer: trace._TraceReducer) -> trace.RuntimeTraceResult:
    reducer.record_subject_proof(trace.SUBJECT_ACCESS_DENIED)
    reducer.end_window(200)
    reducer.mark_cleanup_proved()
    return reducer.finish()


def _close_manifest_window(
    reducer: trace._TraceReducer, *planes: str
) -> None:
    for plane in planes:
        reducer.bind_manifest_identity(plane, _PROVIDER_BINDINGS[plane])
        reducer.mark_manifest_available(plane)


class RuntimeTraceReducerPureTests(unittest.TestCase):
    def test_exact_pid_qpc_irp_and_manifest_semantics_form_four_closed_planes(self):
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
                ("file_access", "denial"),
                ("dll_image_load", "observed_no_denial"),
                ("registry_access", "observed_no_denial"),
                ("code_integrity_policy", "observed_no_denial"),
            ),
        )
        self.assertEqual(result.planes[0].object_ref, _OBJECTS["file_access"][0])
        self.assertIsNone(result.planes[1].object_ref)
        self.assertFalse(result.has_inconclusive)
        self.assertRegex(result.window_binding, r"\Awindow-sha256:[0-9a-f]{64}\Z")
        self.assertTrue(
            all(item.collection_schema == trace.COLLECTION_SCHEMA for item in result.quality)
        )
        self.assertTrue(all(item.probe_available for item in result.quality))
        self.assertTrue(all(item.lossless for item in result.quality))
        self.assertTrue(all(item.plane_scope_complete for item in result.quality))
        self.assertTrue(all(item.correlation_complete for item in result.quality))

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
        self.assertEqual(result.planes[0].reason, "observation_ambiguous")
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

    def test_success_followed_by_ambiguity_never_becomes_observed_no_denial(self):
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
                    result.planes[index].reason, "observation_ambiguous"
                )
                self.assertFalse(result.quality[index].correlation_complete)
                self.assertEqual(
                    result.planes[index].reason,
                    trace._quality_failure_reason(result.quality[index]),
                )

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
        self.assertTrue(all(item.reason == "cleanup_unproved" for item in result.planes))
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

    def test_exact_ci_denial_is_positive_proof_without_negative_closure(self):
        reducer = _bound_reducer()
        reducer.code_integrity_observation(
            timestamp=110,
            raw_process_identity="python-image",
            raw_object_identity="runtime-image",
            semantic="denial",
        )

        result = _finish(reducer)

        self.assertEqual(result.planes[3].outcome, "denial")
        self.assertEqual(
            result.planes[3].object_ref,
            _OBJECTS["code_integrity_policy"][0],
        )
        self.assertTrue(result.quality[3].probe_available)
        self.assertEqual(
            result.quality[3].collection_schema, trace.COLLECTION_SCHEMA
        )
        self.assertFalse(reducer._negative_window_closure["code_integrity_policy"])

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
        self.assertEqual(_finish(exact).planes[1].outcome, "denial")

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
        self.assertEqual(
            _finish(other_status).planes[1].reason, "observation_ambiguous"
        )

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
                initial_image_object_ref=_OBJECTS["dll_image_load"][0],
            )

        self.assertEqual(raised.exception.code, "trace_session_collision")
        self.assertEqual(port.events, ["process_id", "query"])

    def test_held_process_handle_must_resolve_to_exact_pid_before_start(self):
        port = _FakePort()
        collector = trace.RealtimeRuntimeCollector(_binding(), port=port)

        with self.assertRaises(trace.RuntimeTraceError) as raised:
            collector.start_for_suspended_child(
                process_id=42,
                process_handle=900,
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
        self.assertEqual(trace.EXACT_KERNEL_ENABLE_FLAGS, 0x16020005)
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
                if control == trace._EVENT_TRACE_CONTROL_QUERY:
                    return trace._ERROR_WMI_INSTANCE_NOT_FOUND
                props.EventsLost = 2
                props.LogBuffersLost = 3
                props.RealTimeBuffersLost = 4
                return 0

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
        stopped = port.stop_session(handle)

        self.assertEqual(handle, 44)
        self.assertEqual(
            capable, frozenset({"file_access", "registry_access"})
        )
        self.assertEqual(stopped, trace.StopTraceResult(True, 2, 3, 4))

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
        unrelated.EventHeader.ProcessId = 99
        unrelated.EventHeader.TimeStamp = 110
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

    def test_target_kernel_version_and_exact_width_drift_are_inconclusive(self):
        reducer = _bound_reducer()
        port = object.__new__(trace._WindowsEtwPort)
        port._reducer = reducer
        port._integer = lambda *_args: (_ for _ in ()).throw(
            AssertionError("version drift payload decoded")
        )
        port._string = port._integer

        drift = trace._EVENT_RECORD()
        drift.EventHeader.ProviderId = trace._GUID.from_uuid(
            trace._FILE_PROVIDER_UUID
        )
        drift.EventHeader.EventDescriptor.Opcode = 64
        drift.EventHeader.EventDescriptor.Version = 1
        drift.EventHeader.ProcessId = 41
        drift.EventHeader.TimeStamp = 110
        port.translate(ctypes.pointer(drift))
        result = _finish(reducer)
        self.assertEqual(result.planes[0].reason, "collection_schema_unproved")
        self.assertEqual(
            result.planes[0].reason,
            trace._quality_failure_reason(result.quality[0]),
        )

        exact = object.__new__(trace._WindowsEtwPort)
        exact._property = lambda _record, _name: b"\x01\x00\x00\x00"
        self.assertIsNone(exact._integer(ctypes.pointer(drift), "IrpPtr", 8))
        self.assertEqual(exact._integer(ctypes.pointer(drift), "NtStatus", 4), 1)

    def test_kernel_templates_freeze_v2_exact_names_widths_and_registry_semantics(self):
        summary = {
            (str(item.provider), item.opcode, item.version): (
                item.operation,
                item.fields,
            )
            for item in trace._KERNEL_EVENT_TEMPLATES
        }
        self.assertEqual(
            summary[(str(trace._FILE_PROVIDER_UUID), 64, 2)],
            ("file_create", (("IrpPtr", "uint", 8), ("OpenPath", "utf16", None))),
        )
        self.assertEqual(
            summary[(str(trace._FILE_PROVIDER_UUID), 76, 2)],
            ("file_create", (("IrpPtr", "uint", 8), ("NtStatus", "uint", 4))),
        )
        self.assertEqual(
            summary[(str(trace._IMAGE_PROVIDER_UUID), 10, 2)],
            (
                "image_map",
                (("ProcessId", "uint", 4), ("FileName", "utf16", None)),
            ),
        )
        for opcode, operation in trace._REGISTRY_OPERATION_BY_OPCODE.items():
            self.assertEqual(
                summary[(str(trace._REGISTRY_PROVIDER_UUID), opcode, 2)],
                (
                    operation,
                    (("Status", "uint", 4), ("KeyName", "utf16", None)),
                ),
            )

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
        self.assertEqual(result.planes[1].outcome, "denial")

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
        self.assertEqual(result.planes[3].outcome, "denial")

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
        self.assertEqual(
            single_result.planes[3].reason, "observation_ambiguous"
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


if __name__ == "__main__":
    unittest.main()
