from __future__ import annotations

import hashlib
import tempfile
import unittest
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest import mock

from tests import test_m243b_schema21_compatibility as schema21_fixture
from tests import test_m23s_schema22_validation as validation_fixture
from tests.evidence_reader_oracle import (
    EvidenceConsumerError,
    ValidatedEvidenceSource,
    read_evidence_index,
    validate_evidence_source,
)
from tests.evidence_test_support import domain_digest, reference_json_bytes, valid_native_payload
from tests.m14_test_support import tree_snapshot
from tests.test_m23s_schema22_migration import _logical_snapshot, _source_copy
from task_governance_tool import evidence_projection as projection


storage = validation_fixture.storage
BUNDLE_V1_DOMAIN = b"taskgov-completion-evidence-bundle-v1\0"
BUNDLE_V2_DOMAIN = b"taskgov-completion-evidence-bundle-v2\0"
INDEX_V2_DOMAIN = b"taskgov-evidence-index-v2\0"


def _payload(kind: str, *, source_version: int = 22):
    if kind == "not_required":
        payload = schema21_fixture._source21_not_required_payload()
    elif kind == "runner_observation":
        payload = schema21_fixture._source21_runner_payload()
        # The reused fixture replaces the receipt reference in place. The
        # fixed Reference order puts this sole Runner reference after the
        # already ordered artifact, review, and completion references.
        references = payload["evidence_references"]
        runner = next(row for row in references if row["source_kind"] == kind)
        references.remove(runner)
        references.append(runner)
    else:
        if kind != "caller_attestation":
            raise AssertionError("unsupported fixture basis")
        payload = valid_native_payload()
        payload["bundle_version"] = 2
        payload["verification_basis"] = {
            "basis_version": 1,
            "kind": kind,
            "runner_observation_id": None,
            "verification_receipt_id": payload["verification_receipt"]["verification_receipt_id"],
        }
        payload["runner_observation"] = None
    payload["source_schema_version"] = source_version
    return payload


def _reference_envelope(payload):
    version = payload["bundle_version"]
    return {
        "format_version": version,
        "bundle_digest": domain_digest(
            BUNDLE_V1_DOMAIN if version == 1 else BUNDLE_V2_DOMAIN, payload
        ),
        "payload": deepcopy(payload),
    }


def _index_payload(envelope, *, source_version=22):
    payload = envelope["payload"]
    entry = {
        "task_id": payload["task"]["task_id"],
        "completion_cycle_id": payload["completion_cycle_id"],
        "cycle_ordinal": payload["cycle_ordinal"],
        "bundle_state": "native",
        "bundle_id": payload["bundle_id"],
        "bundle_file": f"bundles/{payload['bundle_id']}.json",
        "bundle_digest": envelope["bundle_digest"],
        "file_digest": "sha256:" + hashlib.sha256(reference_json_bytes(envelope) + b"\n").hexdigest(),
        "sealed_at": payload["sealed_at"],
    }
    if source_version != 19:
        entry["bundle_format_version"] = envelope["format_version"]
    return {
        "source_schema_version": source_version,
        "project_id": payload["project_id"],
        "projection_generation": 8,
        "bundle_count": 1,
        "legacy_count": 0,
        "entries": [entry],
    }


def _basis(index, entry):
    return {
        "index_format_version": index.envelope["format_version"],
        "source_schema_version": index.payload["source_schema_version"],
        "project_id": index.payload["project_id"],
        "projection_generation": index.payload["projection_generation"],
        "index_digest": index.index_digest,
        "entry": deepcopy(entry),
    }


def _source(envelope, *, index_source_version=22):
    index = projection.build_index_artifact(
        _index_payload(envelope, source_version=index_source_version)
    )
    return ValidatedEvidenceSource(
        "native_bundle", _basis(index, index.payload["entries"][0]), envelope
    )


@contextmanager
def _source22_stored_bundle(connection, project_id, task_id):
    """Test-owned sealed-payload fixture; this does not exercise completion."""
    basis, artifacts = validation_fixture._bundle_artifacts(connection, project_id)
    record = next(row for row in basis.native_bundles if row.bundle.task_id == task_id)
    bundle_id = record.bundle.completion_evidence_bundle_id
    payload = deepcopy(artifacts[bundle_id].payload)
    payload["source_schema_version"] = 22
    artifact = projection.build_bundle_artifact(payload)
    trigger = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='trg_completion_evidence_bundles_no_update'"
    ).fetchone()[0]
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DROP TRIGGER trg_completion_evidence_bundles_no_update")
        # Physical21's CHECK is bypassed only for the impossible-future fixture.
        if basis.source_schema_version == 21:
            connection.execute("PRAGMA ignore_check_constraints = ON")
        changed = connection.execute(
            "UPDATE completion_evidence_bundles SET source_schema_version=22, "
            "bundle_digest=?, payload_size_bytes=? WHERE completion_evidence_bundle_id=?",
            (artifact.bundle_digest, len(artifact.payload_bytes), bundle_id),
        )
        if changed.rowcount != 1:
            raise AssertionError("fixture must change exactly one sealed Bundle")
        connection.execute("PRAGMA ignore_check_constraints = OFF")
        connection.execute(trigger)
        validate_owned = (
            storage._validate_schema22_owned_contract
            if basis.source_schema_version == 22
            else storage._validate_schema21_owned_contract
        )
        validate_owned(connection)
        yield bundle_id, artifact
    finally:
        connection.rollback()
        connection.execute("PRAGMA ignore_check_constraints = OFF")


class Schema22PureProjectionTests(unittest.TestCase):
    def test_three_v2_bases_match_independent_reader_and_unchanged_domains(self):
        for kind in ("caller_attestation", "not_required", "runner_observation"):
            with self.subTest(kind=kind):
                payload = _payload(kind)
                artifact = projection.build_bundle_artifact(payload)
                self.assertEqual(artifact.envelope, _reference_envelope(payload))
                self.assertEqual(artifact.payload_bytes, reference_json_bytes(payload))
                self.assertEqual(artifact.document, reference_json_bytes(artifact.envelope) + b"\n")
                self.assertEqual((artifact.payload["source_schema_version"], artifact.envelope["format_version"]), (22, 2))
                index = projection.build_index_artifact(_index_payload(artifact.envelope))
                self.assertEqual(index.envelope["format_version"], 2)
                self.assertEqual(index.index_digest, domain_digest(INDEX_V2_DOMAIN, index.payload))
                source = _source(artifact.envelope)
                self.assertEqual(source.source, artifact.envelope)
                self.assertEqual(source.source_basis, _basis(index, index.payload["entries"][0]))
                self.assertEqual(source.source["payload"]["verification_basis"]["kind"], kind)

    def test_closed_versions_schema_ceiling_and_bundle_digest_reject(self):
        future = _payload("not_required", source_version=23)
        impossible_v1 = valid_native_payload()
        impossible_v1["source_schema_version"] = 22
        for payload in (future, impossible_v1):
            with self.subTest(version=(payload["source_schema_version"], payload["bundle_version"])):
                with self.assertRaises(projection.EvidenceProjectionError):
                    projection.build_bundle_artifact(payload)
                with self.assertRaises(EvidenceConsumerError):
                    _source(_reference_envelope(payload))
        envelope = _reference_envelope(_payload("caller_attestation"))
        with self.assertRaises(EvidenceConsumerError):
            _source(envelope, index_source_version=21)
        envelope["bundle_digest"] = "sha256:" + "0" * 64
        with self.assertRaises(EvidenceConsumerError):
            _source(envelope)

    def test_future_index_and_v1_source22_index_reject_read_only(self):
        envelope = _reference_envelope(_payload("not_required"))
        future = _index_payload(envelope, source_version=23)
        with self.assertRaises(projection.EvidenceProjectionError):
            projection.build_index_artifact(future)
        for payload, format_version in ((future, 2), (_index_payload(envelope), 1)):
            with self.subTest(source=payload["source_schema_version"], format=format_version):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    (root / "index.json").write_bytes(reference_json_bytes({
                        "format_version": format_version,
                        "index_digest": domain_digest(INDEX_V2_DOMAIN, payload),
                        "payload": payload,
                    }) + b"\n")
                    before = tree_snapshot(root)
                    with self.assertRaises(EvidenceConsumerError):
                        read_evidence_index(root)
                    self.assertEqual(tree_snapshot(root), before)

    def test_source22_index_keeps_source19_20_21_bundle_files_exact(self):
        payloads = (
            valid_native_payload(),
            _payload("not_required", source_version=20),
            _payload("runner_observation", source_version=21),
        )
        for payload in payloads:
            with self.subTest(source=payload["source_schema_version"]), tempfile.TemporaryDirectory() as temporary:
                artifact = projection.build_bundle_artifact(payload)
                original_index = projection.build_index_artifact(_index_payload(
                    artifact.envelope, source_version=payload["source_schema_version"]
                ))
                root = Path(temporary)
                bundles = root / "bundles"
                bundles.mkdir()
                bundle_path = bundles / f"{payload['bundle_id']}.json"
                bundle_path.write_bytes(artifact.document)
                (root / "index.json").write_bytes(original_index.document)
                original_bundle = tree_snapshot(bundles), bundle_path.stat().st_mtime_ns
                index = projection.build_index_artifact(_index_payload(artifact.envelope))
                (root / "index.json").write_bytes(index.document)
                before = tree_snapshot(root)
                selected = read_evidence_index(root, expected_project_id=payload["project_id"])
                self.assertEqual((selected.source_schema_version, selected.format_version), (22, 2))
                source = validate_evidence_source(selected, selected.entries[0])
                self.assertEqual(source.source_basis, _basis(index, index.payload["entries"][0]))
                self.assertEqual(source.source, artifact.envelope)
                self.assertEqual(source.source_basis["entry"]["bundle_format_version"], payload["bundle_version"])
                self.assertEqual(bundle_path.read_bytes(), artifact.document)
                self.assertEqual((tree_snapshot(bundles), bundle_path.stat().st_mtime_ns), original_bundle)
                self.assertEqual(tree_snapshot(root), before)


class Schema22StoredProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = validation_fixture.Schema22StoredValidationTests
        cls.fixture.setUpClass()
        cls.addClassCleanup(cls.fixture.doClassCleanups)
        cls.target = cls.fixture.target

    def test_migrated22_render_uses_current_index_and_retains_old_bundle_bytes(self):
        with _source_copy(self.target.db_path) as connection:
            project_id = self.target.project.project_id
            before_basis, before_artifacts = validation_fixture._bundle_artifacts(connection, project_id)
            with mock.patch.object(projection, "SCHEMA_VERSION", 21):
                original = projection._render_projection(before_basis)
            self.assertTrue(storage._migrate_schema22_connection(connection))
            snapshot = _logical_snapshot(connection)
            connection.execute("PRAGMA query_only = ON")
            basis = storage.capture_evidence_projection_basis(connection, project_id=project_id)
            rendered = projection._render_projection(basis)
            self.assertEqual(rendered.index.payload["source_schema_version"], 22)
            self.assertEqual(rendered.index.envelope["format_version"], 2)
            self.assertEqual(rendered.source_generation, original.source_generation)
            self.assertEqual(rendered.index.payload["entries"], original.index.payload["entries"])
            for bundle_id, artifact in rendered.bundles:
                self.assertEqual(artifact, before_artifacts[bundle_id])
                self.assertEqual(artifact.payload["source_schema_version"], 21)
                entry = next(row for row in rendered.index.payload["entries"] if row["bundle_id"] == bundle_id)
                source = ValidatedEvidenceSource("native_bundle", _basis(rendered.index, entry), artifact.envelope)
                self.assertEqual(source.source, artifact.envelope)
            self.assertEqual(_logical_snapshot(connection), snapshot)

    def test_source22_manual_and_runner_payloads_validate_in_actual22_only(self):
        project_id = self.target.project.project_id
        for task_id in (self.fixture.manual_task, self.fixture.runner_task):
            with self.subTest(task=task_id), _source_copy(self.target.db_path) as connection:
                self.assertTrue(storage._migrate_schema22_connection(connection))
                _basis_before, original = validation_fixture._bundle_artifacts(connection, project_id)
                with _source22_stored_bundle(connection, project_id, task_id) as (bundle_id, artifact):
                    snapshot = _logical_snapshot(connection)
                    storage.validate_schema22_storage(connection)
                    basis, artifacts = validation_fixture._bundle_artifacts(connection, project_id)
                    self.assertEqual(artifacts[bundle_id], artifact)
                    for other_id in set(artifacts) - {bundle_id}:
                        self.assertEqual(artifacts[other_id], original[other_id])
                    rendered = projection._render_projection(basis)
                    entry = next(row for row in rendered.index.payload["entries"] if row["bundle_id"] == bundle_id)
                    source = ValidatedEvidenceSource("native_bundle", _basis(rendered.index, entry), artifact.envelope)
                    self.assertEqual(source.source, artifact.envelope)
                    self.assertEqual(source.source_basis["source_schema_version"], 22)
                    self.assertEqual(source.source["payload"]["source_schema_version"], 22)
                    storage.read_completion_history(connection, project_id=project_id, task_id=task_id)
                    self.assertEqual(_logical_snapshot(connection), snapshot)
                storage.validate_schema22_storage(connection)

    def test_physical21_selected_history_rejects_future22_with_original_ddl(self):
        project_id = self.target.project.project_id
        with _source_copy(self.target.db_path) as connection:
            with _source22_stored_bundle(connection, project_id, self.fixture.manual_task):
                snapshot = _logical_snapshot(connection)
                with self.assertRaises(storage.StorageError) as rejected:
                    storage.read_completion_history(
                        connection, project_id=project_id, task_id=self.fixture.manual_task
                    )
                self.assertEqual(rejected.exception.code, "completion_history_inconsistent")
                with self.assertRaises(storage.StorageError):
                    validation_fixture._bundle_artifacts(connection, project_id)
                self.assertEqual(_logical_snapshot(connection), snapshot)
            storage.validate_schema21_storage(connection)


if __name__ == "__main__":
    unittest.main()
