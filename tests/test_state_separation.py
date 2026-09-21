"""Closed cutover metadata, no-write admission and exact physical publication."""

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "task-governance-tool" / "scripts"))

from task_governance_tool.state_paths import hash_physical_file  # noqa: E402
from task_governance_tool.state_separation import (  # noqa: E402
    SeparationError,
    SeparationRecord,
    decode_separation_record,
    encode_separation_record,
    inspect_separation,
    publish_record,
    publish_retirement_marker,
    read_record,
    retirement_marker_bytes,
    separation_paths,
)


PROJECT_ID = "tg_project_123456781234423481231234567890ab"


def fresh_record():
    return SeparationRecord(1, "a" * 32, "private", PROJECT_ID)


def source_record():
    return replace(
        fresh_record(), source_layout="fixed_current_v1", source_schema_version=22,
        source_binding_generation=1, source_path_hash="1" * 64,
        source_fingerprint="2" * 64,
    )


class SeparationCodecTests(unittest.TestCase):
    def test_canonical_roundtrip_and_private_preparation(self):
        for record in (fresh_record(), source_record()):
            self.assertEqual(decode_separation_record(encode_separation_record(record)), record)
        legacy = replace(source_record(), source_schema_version=2, source_binding_generation=0)
        self.assertEqual(decode_separation_record(encode_separation_record(legacy)), legacy)

    def test_incomplete_record_cannot_claim_fenced_or_activated(self):
        for phase in ("fenced", "activated"):
            for record in (fresh_record(), source_record()):
                with self.subTest(phase=phase, source=record.source_layout):
                    with self.assertRaises(SeparationError):
                        encode_separation_record(replace(record, phase=phase))

    def test_rejects_unknown_duplicate_noncanonical_and_invalid_fields(self):
        data = encode_separation_record(source_record())
        cases = [
            data + b"\n", b"\xef\xbb\xbf" + data,
            data[:-1] + b',"v":1}',
            data.replace(b'"v":1', b'"v":true'),
        ]
        payload = json.loads(data)
        for field, value in (
            ("path", "elsewhere"), ("source_layout", []),
            ("source_schema_version", True), ("source_binding_generation", 0),
            ("phase", "other"), ("candidate_digest", "bad"),
            ("project_id", "arbitrary"),
        ):
            changed = {**payload, field: value}
            cases.append(json.dumps(changed, sort_keys=True, separators=(",", ":")).encode())
        for data in cases:
            with self.subTest(data=data), self.assertRaises(SeparationError):
                decode_separation_record(data)


class SeparationPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / ".taskgov"
        self.old = self.root / "package" / "state" / "current" / "taskgov.sqlite"
        self.old.parent.mkdir(parents=True)

    def observe(self):
        return inspect_separation(state_root=self.state, old_database=self.old)

    def prepare(self, *, source=False):
        self.state.mkdir()
        record = source_record() if source else fresh_record()
        if source:
            self.old.write_bytes(b"original database content")
        publish_record(self.state, record, expected=None)
        paths = separation_paths(self.state)
        paths.candidate.mkdir()
        (paths.candidate / "taskgov.sqlite").write_bytes(b"candidate")
        if source:
            paths.source.mkdir()
            (paths.source / "taskgov.sqlite").write_bytes(b"retained snapshot")
        complete = replace(
            record, candidate_digest="3" * 64,
            retained_digest="4" * 64 if source else None,
        )
        publish_record(self.state, complete, expected=record)
        return complete

    def test_absence_is_no_write_and_unowned_new_current_is_not_fresh(self):
        self.assertEqual(self.observe().state, "absent")
        self.assertFalse(self.state.exists())
        (self.state / "current").mkdir(parents=True)
        with self.assertRaises(SeparationError):
            self.observe()
        self.assertTrue((self.state / "current").is_dir())

    def test_fresh_and_source_cutovers_observe_actual_objects_after_each_phase(self):
        for source in (False, True):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temp:
                original_state, original_old = self.state, self.old
                self.state = Path(temp) / ".taskgov"
                self.old = Path(temp) / "package" / "state" / "current" / "taskgov.sqlite"
                self.old.parent.mkdir(parents=True)
                record = self.prepare(source=source)
                self.assertEqual(self.observe().state, "pending")
                expected = hash_physical_file(self.old, root=self.old.parent.parent) if source else None
                publish_retirement_marker(self.old, record, expected=expected)
                # Crash after fencing but before phase persistence remains recognizable.
                self.assertTrue(self.observe().marker_matches)
                fenced = replace(record, phase="fenced")
                publish_record(self.state, fenced, expected=record)
                separation_paths(self.state).candidate.rename(self.state / "current")
                self.assertEqual(self.observe().state, "pending")
                active = replace(fenced, phase="activated")
                publish_record(self.state, active, expected=fenced)
                self.assertEqual(self.observe().state, "activated")
                (self.state / "current" / "taskgov.sqlite").write_bytes(b"later business updates")
                self.assertEqual(self.observe().state, "activated")
                self.old.unlink()
                self.assertEqual(self.observe().state, "repair_barrier")
                self.old.write_bytes(b"competing old database")
                with self.assertRaises(SeparationError):
                    self.observe()
                self.state, self.old = original_state, original_old

    def test_interrupted_record_replace_replays_only_exact_prepared_successor(self):
        self.state.mkdir()
        record = fresh_record()
        publish_record(self.state, record, expected=None)
        successor = replace(record, candidate_digest="3" * 64)
        with patch("task_governance_tool.state_separation.os.replace", side_effect=OSError):
            with self.assertRaises(SeparationError):
                publish_record(self.state, successor, expected=record)
        self.assertEqual(read_record(self.state), record)
        self.assertTrue(separation_paths(self.state).next_record.is_file())
        with self.assertRaises(SeparationError):
            publish_record(self.state, replace(successor, candidate_digest="5" * 64), expected=record)
        publish_record(self.state, successor, expected=record)
        self.assertEqual(read_record(self.state), successor)

    def test_wrong_cas_and_retrograde_phase_preserve_record(self):
        record = self.prepare()
        with self.assertRaises(SeparationError):
            publish_record(self.state, replace(record, phase="activated"), expected=record)
        with self.assertRaises(SeparationError):
            publish_record(self.state, replace(record, project_id="project-0123456789ab"), expected=record)
        with self.assertRaises(SeparationError):
            publish_record(self.state, replace(record, candidate_digest="5" * 64), expected=record)
        self.assertEqual(read_record(self.state), record)

    def test_changed_source_and_occupied_missing_primary_never_overwritten(self):
        record = self.prepare(source=True)
        expected = hash_physical_file(self.old, root=self.old.parent.parent)
        self.old.write_bytes(b"changed source")
        with self.assertRaises(SeparationError):
            publish_retirement_marker(self.old, record, expected=expected)
        self.assertEqual(self.old.read_bytes(), b"changed source")
        with self.assertRaises(SeparationError):
            publish_retirement_marker(self.old, record, expected=None)
        self.assertEqual(self.old.read_bytes(), b"changed source")

    def test_marker_preparation_is_durable_and_retry_does_not_require_source_rename(self):
        record = self.prepare(source=True)
        expected = hash_physical_file(self.old, root=self.old.parent.parent)
        with patch("task_governance_tool.state_separation.os.replace", side_effect=OSError):
            with self.assertRaises(SeparationError):
                publish_retirement_marker(self.old, record, expected=expected)
        self.assertEqual(self.old.read_bytes(), b"original database content")
        publish_retirement_marker(self.old, record, expected=expected)
        self.assertEqual(self.old.read_bytes(), retirement_marker_bytes(record))
        self.assertEqual((separation_paths(self.state).source / "taskgov.sqlite").read_bytes(), b"retained snapshot")

    def test_fresh_record_cannot_hide_old_database_or_unexplained_residue(self):
        self.prepare()
        self.old.write_bytes(b"SQLite format 3\0" + b"x" * 4096)
        with self.assertRaises(SeparationError):
            self.observe()
        self.old.unlink()
        (self.state / "unexpected").write_bytes(b"preserve")
        with self.assertRaises(SeparationError):
            self.observe()
        self.assertEqual((self.state / "unexpected").read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
