from __future__ import annotations

import copy
import io
import json
import unittest
from collections.abc import Iterator, Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from typing import Any

from tests import m241b_runtime_qualification_support as support


_RUNTIME_DIGEST = "sha256:" + "a" * 64
_WINDOW_BINDING = "window-sha256:" + "b" * 64
_SEMANTICS = (
    ("file_access", "file_create", "file_io"),
    ("dll_image_load", "image_map", "image_loader"),
    ("registry_access", "registry_open", "registry_access"),
    ("code_integrity_policy", "image_policy_validate", "code_integrity"),
)
_OBJECTS_BY_PLANE = {
    plane: (
        f"inventory-sha256:{(index * 2 + 1):064x}",
        f"inventory-sha256:{(index * 2 + 2):064x}",
    )
    for index, (plane, _operation, _policy) in enumerate(_SEMANTICS)
}


class _UnhashableKeysMapping(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def keys(self) -> Any:
        return [[], *support.PLANE_ORDER[1:]]


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _inventory(
    *,
    runtime_digest: str = _RUNTIME_DIGEST,
    objects_by_plane: dict[str, tuple[str, ...]] | None = None,
) -> support.InventoryManifest:
    return support.bind_inventory_manifest(
        runtime_digest=runtime_digest,
        objects_by_plane=(
            copy.deepcopy(_OBJECTS_BY_PLANE)
            if objects_by_plane is None
            else objects_by_plane
        ),
    )


def _quality_inputs(
    *,
    plane_overrides: dict[str, dict[str, object]] | None = None,
) -> tuple[support.PlaneCollectionQualityInput, ...]:
    overrides = {} if plane_overrides is None else plane_overrides
    result = []
    for plane, _operation, _policy in _SEMANTICS:
        values: dict[str, object] = {
            "plane": plane,
            "collection_schema": support.COLLECTION_SCHEMA,
            "probe_available": True,
            "lossless": True,
            "overflowed": False,
            "plane_scope_complete": True,
            "correlation_complete": True,
            "cleanup_proved": True,
        }
        values.update(overrides.get(plane, {}))
        result.append(support.PlaneCollectionQualityInput(**values))  # type: ignore[arg-type]
    return tuple(result)


def _quality(
    inventory: support.InventoryManifest,
    *,
    plane_overrides: dict[str, dict[str, object]] | None = None,
    window_binding: str = _WINDOW_BINDING,
    subject_proof: object = support.STOCK_CHILD_ACCESS_DENIED_PROOF,
) -> support.CollectionQualityProof:
    return support.bind_collection_quality(
        subject_proof=subject_proof,  # type: ignore[arg-type]
        window_binding=window_binding,
        inventory_manifest=inventory,
        planes=_quality_inputs(plane_overrides=plane_overrides),
    )


def _payload(
    *,
    inventory: support.InventoryManifest | None = None,
    quality: support.CollectionQualityProof | None = None,
) -> dict[str, object]:
    bound_inventory = _inventory() if inventory is None else inventory
    bound_quality = _quality(bound_inventory) if quality is None else quality
    return {
        "schema_version": 1,
        "candidate_id": support.CURRENT_CANDIDATE_ID,
        "runtime_digest": _RUNTIME_DIGEST,
        "subject": support.STOCK_CHILD_SUBJECT,
        "exit_binding": support.STOCK_CHILD_EXIT,
        "inventory_manifest_digest": bound_inventory.manifest_digest,
        "collection_proof_digest": bound_quality.proof_digest,
        "planes": [
            {
                "plane": plane,
                "outcome": "denial" if index == 0 else "observed_no_denial",
                "object_ref": (
                    _OBJECTS_BY_PLANE[plane][0] if index == 0 else None
                ),
                "operation": operation,
                "policy": policy,
                "reason": None,
            }
            for index, (plane, operation, policy) in enumerate(_SEMANTICS)
        ],
    }


class RootCauseEvidencePureTests(unittest.TestCase):
    def load(
        self,
        payload: object | None = None,
        *,
        expected_candidate_id: object = support.CURRENT_CANDIDATE_ID,
        expected_runtime_digest: object = _RUNTIME_DIGEST,
        subject_proof: object = support.STOCK_CHILD_ACCESS_DENIED_PROOF,
        inventory_manifest: object | None = None,
        collection_quality: object | None = None,
    ) -> support.RootCauseEvidence:
        inventory = _inventory() if inventory_manifest is None else inventory_manifest
        quality = (
            _quality(inventory)  # type: ignore[arg-type]
            if collection_quality is None
            else collection_quality
        )
        return support.load_root_cause_evidence(
            _canonical(_payload() if payload is None else payload),
            expected_candidate_id=expected_candidate_id,  # type: ignore[arg-type]
            expected_runtime_digest=expected_runtime_digest,  # type: ignore[arg-type]
            subject_proof=subject_proof,  # type: ignore[arg-type]
            inventory_manifest=inventory,  # type: ignore[arg-type]
            collection_quality=quality,  # type: ignore[arg-type]
        )

    def assert_invalid(self, payload: object | None = None, **kwargs: object) -> None:
        with self.assertRaises(support.RootCauseEvidenceError) as raised:
            self.load(payload, **kwargs)
        self.assertEqual(str(raised.exception), "root_cause_evidence_invalid")

    def test_valid_p1_document_returns_only_digest_bound_frozen_evidence(self):
        inventory = _inventory()
        quality = _quality(inventory)

        evidence = self.load(
            inventory_manifest=inventory,
            collection_quality=quality,
        )

        self.assertEqual(support.SCHEMA_NAME, "root-cause-evidence-v1")
        self.assertEqual(evidence.candidate_id, "current_runtime_unchanged")
        self.assertEqual(evidence.runtime_digest, _RUNTIME_DIGEST)
        self.assertEqual(evidence.subject, support.STOCK_CHILD_SUBJECT)
        self.assertEqual(evidence.exit_binding, support.STOCK_CHILD_EXIT)
        self.assertEqual(evidence.exit_binding, "status_access_denied_0xc0000022")
        self.assertEqual(evidence.inventory_manifest_digest, inventory.manifest_digest)
        self.assertEqual(evidence.collection_proof_digest, quality.proof_digest)
        self.assertEqual(evidence.collection_window_binding, _WINDOW_BINDING)
        self.assertEqual(
            tuple(plane.plane for plane in evidence.planes), support.PLANE_ORDER
        )
        self.assertIsNone(evidence.planes[1].object_ref)
        self.assertFalse(evidence.has_inconclusive)
        self.assertFalse(hasattr(evidence, "selected_candidate"))
        self.assertFalse(hasattr(evidence, "qualified"))
        with self.assertRaises(FrozenInstanceError):
            evidence.candidate_id = "later_candidate"  # type: ignore[misc]

    def test_only_current_runtime_candidate_and_trusted_expected_value_are_valid(self):
        later_candidates = (
            "current_runtime_package_sid_rx",
            "official_embeddable_distribution",
            "isolated_native_host",
        )
        for candidate in later_candidates:
            with self.subTest(case=candidate):
                payload = _payload()
                payload["candidate_id"] = candidate
                self.assert_invalid(payload)
                self.assert_invalid(expected_candidate_id=candidate)
        self.assert_invalid(expected_candidate_id=1)

    def test_runtime_digest_is_bound_across_expected_document_and_manifests(self):
        wrong_digest = "sha256:" + "c" * 64
        payload = _payload()
        payload["runtime_digest"] = wrong_digest
        self.assert_invalid(payload)
        self.assert_invalid(expected_runtime_digest=wrong_digest)

        wrong_inventory = _inventory(runtime_digest=wrong_digest)
        self.assert_invalid(inventory_manifest=wrong_inventory)

        inventory = _inventory()
        changed_objects = copy.deepcopy(_OBJECTS_BY_PLANE)
        changed_objects["file_access"] = (
            *changed_objects["file_access"],
            "inventory-sha256:" + "f" * 64,
        )
        changed_manifest = _inventory(objects_by_plane=changed_objects)
        quality = _quality(inventory)
        self.assert_invalid(
            inventory_manifest=changed_manifest,
            collection_quality=quality,
        )

    def test_canonical_document_binds_subject_exit_manifest_and_collection_window(self):
        inventory = _inventory()
        quality = _quality(inventory)
        mutations = (
            ("subject", "different_subject"),
            ("exit_binding", "status_access_violation_0xc0000005"),
            ("inventory_manifest_digest", "sha256:" + "c" * 64),
            ("collection_proof_digest", "sha256:" + "d" * 64),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                payload = _payload(inventory=inventory, quality=quality)
                payload[field] = value
                self.assert_invalid(
                    payload,
                    inventory_manifest=inventory,
                    collection_quality=quality,
                )

        changed_window_quality = _quality(
            inventory,
            window_binding="window-sha256:" + "e" * 64,
        )
        self.assertNotEqual(
            changed_window_quality.proof_digest,
            quality.proof_digest,
        )
        self.assert_invalid(
            _payload(inventory=inventory, quality=quality),
            inventory_manifest=inventory,
            collection_quality=changed_window_quality,
        )

    def test_inventory_manifest_is_typed_ordered_and_cross_plane_unique(self):
        missing = copy.deepcopy(_OBJECTS_BY_PLANE)
        missing.pop("registry_access")
        extra = copy.deepcopy(_OBJECTS_BY_PLANE)
        extra["extra"] = ("inventory-sha256:" + "e" * 64,)
        unsorted = copy.deepcopy(_OBJECTS_BY_PLANE)
        unsorted["file_access"] = tuple(reversed(unsorted["file_access"]))
        duplicate = copy.deepcopy(_OBJECTS_BY_PLANE)
        duplicate["file_access"] = (
            duplicate["file_access"][0],
            duplicate["file_access"][0],
        )
        cross_plane = copy.deepcopy(_OBJECTS_BY_PLANE)
        cross_plane["dll_image_load"] = (
            cross_plane["file_access"][0],
            cross_plane["dll_image_load"][1],
        )
        raw_path = copy.deepcopy(_OBJECTS_BY_PLANE)
        raw_path["file_access"] = (r"C:\\private\\python.exe",)
        arbitrary_set = copy.deepcopy(_OBJECTS_BY_PLANE)
        arbitrary_set["file_access"] = set(arbitrary_set["file_access"])  # type: ignore[assignment]
        for index, objects in enumerate(
            (missing, extra, unsorted, duplicate, cross_plane, raw_path, arbitrary_set)
        ):
            with self.subTest(case=index), self.assertRaises(
                support.RootCauseEvidenceError
            ):
                support.bind_inventory_manifest(
                    runtime_digest=_RUNTIME_DIGEST,
                    objects_by_plane=objects,
                )

        self.assert_invalid(inventory_manifest=set(_OBJECTS_BY_PLANE))
        valid_inventory = _inventory()
        forged = object.__new__(support.InventoryManifest)
        self.assert_invalid(
            inventory_manifest=forged,
            collection_quality=_quality(valid_inventory),
        )

    def test_public_binders_normalize_hostile_structural_inputs(self):
        non_string_key = copy.deepcopy(_OBJECTS_BY_PLANE)
        non_string_key[1] = non_string_key.pop("file_access")  # type: ignore[index]
        for objects in (non_string_key, _UnhashableKeysMapping()):
            with self.subTest(mapping_type=type(objects).__name__), self.assertRaises(
                support.RootCauseEvidenceError
            ) as raised:
                support.bind_inventory_manifest(
                    runtime_digest=_RUNTIME_DIGEST,
                    objects_by_plane=objects,  # type: ignore[arg-type]
                )
            self.assertEqual(str(raised.exception), "root_cause_evidence_invalid")

        inventory = _inventory()
        partial = object.__new__(support.PlaneCollectionQualityInput)
        planes = (partial, *_quality_inputs()[1:])
        with self.assertRaises(support.RootCauseEvidenceError) as raised:
            support.bind_collection_quality(
                subject_proof=support.STOCK_CHILD_ACCESS_DENIED_PROOF,
                window_binding=_WINDOW_BINDING,
                inventory_manifest=inventory,
                planes=planes,
            )
        self.assertEqual(str(raised.exception), "root_cause_evidence_invalid")

    def test_denial_rejects_unknown_and_cross_plane_object_references(self):
        unknown = _payload()
        unknown["planes"][0]["object_ref"] = (  # type: ignore[index]
            "inventory-sha256:" + "f" * 64
        )
        cross_plane = _payload()
        cross_plane["planes"][0]["object_ref"] = _OBJECTS_BY_PLANE[  # type: ignore[index]
            "dll_image_load"
        ][0]
        for index, payload in enumerate((unknown, cross_plane)):
            with self.subTest(case=index):
                self.assert_invalid(payload)

    def test_operations_and_policies_are_closed_and_plane_typed(self):
        invalid = (
            (0, "operation", "file_open_read"),
            (0, "operation", "registry_open"),
            (0, "operation", "unknown_operation"),
            (0, "policy", "registry_access"),
            (2, "operation", "file_create"),
            (2, "policy", "file_io"),
        )
        for plane_index, field, value in invalid:
            with self.subTest(plane=plane_index, field=field, value=value):
                payload = _payload()
                payload["planes"][plane_index][field] = value  # type: ignore[index]
                self.assert_invalid(payload)

        for registry_operation in sorted(
            support._PLANE_OPERATIONS["registry_access"]
        ):
            with self.subTest(registry_operation=registry_operation):
                payload = _payload()
                payload["planes"][2]["operation"] = registry_operation  # type: ignore[index]
                evidence = self.load(payload)
                self.assertEqual(evidence.planes[2].operation, registry_operation)

        for file_operation in sorted(support._PLANE_OPERATIONS["file_access"]):
            with self.subTest(file_operation=file_operation):
                payload = _payload()
                payload["planes"][0]["operation"] = file_operation  # type: ignore[index]
                evidence = self.load(payload)
                self.assertEqual(evidence.planes[0].operation, file_operation)

    def test_stock_child_access_denied_proof_is_exact_and_no_raw_status_is_input(self):
        self.assert_invalid(subject_proof="0xC0000005")
        forged = object.__new__(support.StockChildSubjectProof)
        object.__setattr__(forged, "subject", support.STOCK_CHILD_SUBJECT)
        object.__setattr__(forged, "exit_binding", "status_access_violation")
        self.assert_invalid(subject_proof=forged)
        with self.assertRaises(TypeError):
            support.StockChildSubjectProof(  # type: ignore[call-arg]
                subject=support.STOCK_CHILD_SUBJECT,
                exit_binding="0xC0000022",
            )

    def test_observed_no_denial_requires_complete_plane_scope_not_one_object(self):
        payload = _payload()
        payload["planes"][1]["object_ref"] = _OBJECTS_BY_PLANE[  # type: ignore[index]
            "dll_image_load"
        ][0]
        self.assert_invalid(payload)

    def test_quality_failures_require_the_exact_inconclusive_reason(self):
        cases = (
            ({"probe_available": False}, "probe_unavailable"),
            (
                {"collection_schema": support.UNKNOWN_COLLECTION_SCHEMA},
                "collection_schema_unproved",
            ),
            ({"lossless": False}, "observation_overflow"),
            ({"overflowed": True}, "observation_overflow"),
            ({"plane_scope_complete": False}, "plane_scope_unproved"),
            ({"correlation_complete": False}, "observation_ambiguous"),
            ({"cleanup_proved": False}, "cleanup_unproved"),
        )
        for index, (override, reason) in enumerate(cases):
            with self.subTest(case=index):
                inventory = _inventory()
                quality = _quality(
                    inventory,
                    plane_overrides={"dll_image_load": override},
                )
                self.assert_invalid(
                    inventory_manifest=inventory,
                    collection_quality=quality,
                )

                payload = _payload(inventory=inventory, quality=quality)
                payload["planes"][1].update(  # type: ignore[index]
                    outcome="inconclusive",
                    object_ref=None,
                    reason=reason,
                )
                evidence = self.load(
                    payload,
                    inventory_manifest=inventory,
                    collection_quality=quality,
                )
                self.assertTrue(evidence.has_inconclusive)

                payload["planes"][1]["reason"] = "probe_unavailable"  # type: ignore[index]
                if reason != "probe_unavailable":
                    self.assert_invalid(
                        payload,
                        inventory_manifest=inventory,
                        collection_quality=quality,
                    )

    def test_quality_proof_rejects_wrong_window_plane_digest_and_types(self):
        inventory = _inventory()
        with self.assertRaises(support.RootCauseEvidenceError):
            _quality(inventory, window_binding="window:raw")
        with self.assertRaises(support.RootCauseEvidenceError):
            _quality(
                inventory,
                plane_overrides={"file_access": {"lossless": 1}},
            )

        quality = _quality(inventory)
        object.__setattr__(
            quality.planes[0],
            "plane_inventory_digest",
            "sha256:" + "d" * 64,
        )
        self.assert_invalid(
            inventory_manifest=inventory,
            collection_quality=quality,
        )
        self.assert_invalid(
            inventory_manifest=inventory,
            collection_quality=object.__new__(support.CollectionQualityProof),
        )

        forged_digest = _quality(inventory)
        object.__setattr__(
            forged_digest,
            "proof_digest",
            "sha256:" + "f" * 64,
        )
        self.assert_invalid(
            _payload(inventory=inventory, quality=forged_digest),
            inventory_manifest=inventory,
            collection_quality=forged_digest,
        )

    def test_negative_quality_cannot_support_denial_and_still_needs_one_denial(self):
        inventory = _inventory()
        quality = _quality(
            inventory,
            plane_overrides={"file_access": {"cleanup_proved": False}},
        )
        self.assert_invalid(
            inventory_manifest=inventory,
            collection_quality=quality,
        )

        payload = _payload(inventory=inventory, quality=quality)
        payload["planes"][0].update(  # type: ignore[index]
            outcome="inconclusive",
            object_ref=None,
            reason="cleanup_unproved",
        )
        self.assert_invalid(
            payload,
            inventory_manifest=inventory,
            collection_quality=quality,
        )

        payload["planes"][1].update(  # type: ignore[index]
            outcome="denial",
            object_ref=_OBJECTS_BY_PLANE["dll_image_load"][0],
            reason=None,
        )
        evidence = self.load(
            payload,
            inventory_manifest=inventory,
            collection_quality=quality,
        )
        self.assertTrue(evidence.has_inconclusive)

    def test_top_plane_keys_semantics_and_canonical_bytes_are_exact(self):
        mutations = []
        missing = _payload()
        missing.pop("runtime_digest")
        mutations.append(missing)
        extra = _payload()
        extra["extra"] = None
        mutations.append(extra)
        wrong_schema = _payload()
        wrong_schema["schema_version"] = True
        mutations.append(wrong_schema)
        plane_extra = _payload()
        plane_extra["planes"][0]["detail"] = "forbidden"  # type: ignore[index]
        mutations.append(plane_extra)
        for field, value in (
            ("plane", "filesystem"),
            ("outcome", "failed"),
            ("operation", "read C:\\private"),
            ("policy", "provider policy text"),
        ):
            changed = _payload()
            changed["planes"][0][field] = value  # type: ignore[index]
            mutations.append(changed)
        for index, payload in enumerate(mutations):
            with self.subTest(case=index):
                self.assert_invalid(payload)

        inventory = _inventory()
        quality = _quality(inventory)
        documents: tuple[object, ...] = (
            json.dumps(_payload(), indent=2, sort_keys=True).encode("utf-8"),
            _canonical(_payload()) + b"\n",
            b"\xff",
            b"[]",
            b'{"schema_version":' + b"9" * 5_000 + b"}",
            b" " * (support.EVIDENCE_MAX_BYTES + 1),
            bytearray(_canonical(_payload())),
        )
        for index, document in enumerate(documents):
            with self.subTest(document_case=index), self.assertRaises(
                support.RootCauseEvidenceError
            ):
                support.load_root_cause_evidence(
                    document,  # type: ignore[arg-type]
                    expected_candidate_id=support.CURRENT_CANDIDATE_ID,
                    expected_runtime_digest=_RUNTIME_DIGEST,
                    subject_proof=support.STOCK_CHILD_ACCESS_DENIED_PROOF,
                    inventory_manifest=inventory,
                    collection_quality=quality,
                )

    def test_duplicate_json_keys_are_rejected_at_every_object_level(self):
        document = _canonical(_payload())
        changed_documents = (
            b'{"candidate_id":"current_runtime_unchanged",' + document[1:],
            document.replace(
                b'{"object_ref":', b'{"object_ref":null,"object_ref":', 1
            ),
        )
        inventory = _inventory()
        quality = _quality(inventory)
        for index, changed in enumerate(changed_documents):
            with self.subTest(case=index), self.assertRaises(
                support.RootCauseEvidenceError
            ) as raised:
                support.load_root_cause_evidence(
                    changed,
                    expected_candidate_id=support.CURRENT_CANDIDATE_ID,
                    expected_runtime_digest=_RUNTIME_DIGEST,
                    subject_proof=support.STOCK_CHILD_ACCESS_DENIED_PROOF,
                    inventory_manifest=inventory,
                    collection_quality=quality,
                )
            self.assertEqual(str(raised.exception), "root_cause_evidence_invalid")

    def test_raw_fields_and_secret_canary_never_enter_model_error_or_streams(self):
        forbidden = (
            "path",
            "pid",
            "sid",
            "argv",
            "env",
            "status",
            "provider",
            "log",
            "error",
            "free_text",
        )
        for field in forbidden:
            payload = _payload()
            payload[field] = "sensitive"
            self.assert_invalid(payload)
            payload = _payload()
            payload["planes"][0][field] = "sensitive"  # type: ignore[index]
            self.assert_invalid(payload)

        canary = (
            "Authorization: Bearer SECRET_CANARY_123 "
            r"C:\\Users\\private\\runtime.exe provider=<raw>"
        )
        mutations = []
        for field in ("candidate_id", "runtime_digest"):
            payload = _payload()
            payload[field] = canary
            mutations.append(payload)
        for field in ("plane", "outcome", "object_ref", "operation", "policy", "reason"):
            payload = _payload()
            payload["planes"][0][field] = canary  # type: ignore[index]
            mutations.append(payload)
        raw_field = _payload()
        raw_field["provider"] = canary
        mutations.append(raw_field)

        stdout = io.StringIO()
        stderr = io.StringIO()
        for index, payload in enumerate(mutations):
            with self.subTest(case=index), redirect_stdout(stdout), redirect_stderr(
                stderr
            ):
                with self.assertRaises(support.RootCauseEvidenceError) as raised:
                    self.load(payload)
                self.assertEqual(
                    str(raised.exception), "root_cause_evidence_invalid"
                )
                self.assertNotIn(canary, str(raised.exception))
                self.assertNotIn(canary, repr(raised.exception))
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
