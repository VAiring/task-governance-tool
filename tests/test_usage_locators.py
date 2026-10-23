"""Retained path hints remain bounded, root-bound and subject to fresh admission."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from task_governance_tool.usage_adapter import read_batch
from task_governance_tool.usage_sources import (
    MAX_LOCATOR_BYTES, SourceLocator, archive_locator, locator_from_source,
    source_from_locator, source_hint, source_roots, validate_locator,
)
from task_governance_tool.usage_values import UsageError


THREAD = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
SEGMENT = "33333333-3333-4333-8333-333333333333"


class UsageLocatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()
        self.roots = source_roots({"CODEX_HOME": str(self.base / "host")})
        self.path = self.roots[0] / "2026/10/24" / f"rollout-test-{THREAD}.jsonl"

    def hint(self, path=None, thread=THREAD):
        return source_hint(thread, str(path or self.path), self.roots, self.project)

    def capture(self, path=None, thread=THREAD):
        return locator_from_source(self.hint(path, thread), self.roots)

    def write(self, path=None, thread=THREAD):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"type": "session_meta", "payload": {
            "id": thread, "cwd": str(self.project), "model_provider": "openai"}}
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    def test_missing_file_and_parents_can_be_retained_then_freshly_read(self):
        source = self.hint()
        with mock.patch.object(Path, "open", side_effect=AssertionError("capture must not read a body")):
            locator = self.capture()
        self.assertIsInstance(locator, SourceLocator)
        self.assertEqual(locator.source_id, source.source_id)
        # The stored values alone reconstruct the same hint after a caller restart.
        retained = SourceLocator(**json.loads(json.dumps(asdict(locator))))
        restored = source_from_locator(retained, self.roots, self.project)
        self.assertEqual(restored.source_id, source.source_id)
        with self.assertRaises(UsageError):
            read_batch(restored)
        self.write()
        self.assertEqual(read_batch(source_from_locator(retained, self.roots, self.project)).thread_id, THREAD)
        self.assertNotIn(locator.relative_path, repr(locator))

    def test_nonstandard_current_hint_is_preserved_without_archive_guess(self):
        path = self.roots[0] / "custom-layout/log file.jsonl"
        self.assertIsNotNone(self.hint(path))
        locator = self.capture(path)
        self.assertIsNotNone(locator)
        self.assertEqual(source_from_locator(locator, self.roots, self.project).source_id, self.hint(path).source_id)
        self.assertIsNone(archive_locator(locator, self.roots, self.project))

    def test_root_change_and_tampered_path_hash_are_rejected(self):
        locator = self.capture()
        other_roots = source_roots({"CODEX_HOME": str(self.base / "other-host")})
        for candidate, roots in (
            (locator, other_roots),
            (replace(locator, source_id="0" * 64), self.roots),
            (replace(locator, root_fingerprint="0" * 64), self.roots),
        ):
            with self.subTest(candidate=candidate, changed_roots=roots != self.roots):
                with self.assertRaises(UsageError) as error:
                    source_from_locator(candidate, roots, self.project)
                self.assertEqual(error.exception.code, "source_unreadable")

    def test_stored_shape_rejects_escaping_and_noncanonical_paths_before_io(self):
        locator = self.capture()
        bad_paths = ["", ".", "..", "../outside.jsonl", "/absolute.jsonl", "a/../b", "a/./b",
                     "a//b", "a/", "a\\b", "C:/file", "a:stream", "a\0b", "a\nb",
                     "x" * (MAX_LOCATOR_BYTES + 1), "\ud800"]
        with mock.patch.object(Path, "lstat", side_effect=AssertionError("invalid values must not touch the filesystem")):
            for relative in bad_paths:
                with self.subTest(relative=repr(relative)):
                    with self.assertRaises(UsageError):
                        source_from_locator(replace(locator, relative_path=relative), self.roots, self.project)
            for field, value in (("root_kind", "unknown"), ("thread_id", "not-a-uuid"),
                                 ("source_id", "A" * 64), ("root_fingerprint", True),
                                 ("relative_path", 1)):
                with self.subTest(field=field):
                    with self.assertRaises(UsageError):
                        validate_locator(replace(locator, **{field: value}))
            with self.assertRaises(UsageError):
                validate_locator(asdict(locator))

    def test_directory_and_file_parent_are_not_retained(self):
        self.path.mkdir(parents=True)
        self.assertIsNone(self.capture())
        parent_file = self.roots[0] / "parent-file"
        parent_file.write_text("", encoding="utf-8")
        self.assertIsNone(self.capture(parent_file / "missing.jsonl"))

    def test_existing_source_is_rechecked_after_capture(self):
        self.write()
        locator = self.capture()
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(UsageError):
            source_from_locator(locator, self.roots, self.project)

    def test_locator_never_replaces_header_identity_admission(self):
        self.write()
        locator = self.capture()
        self.write(thread=OTHER)
        source = source_from_locator(locator, self.roots, self.project)
        with self.assertRaises(UsageError) as error:
            read_batch(source)
        self.assertEqual(error.exception.code, "source_unreadable")

    def test_archive_uses_one_exact_basename_and_new_path_identity_without_inventory(self):
        path = self.path.with_name(f"rollout-test-{THREAD}_{SEGMENT}.jsonl")
        locator = self.capture(path)
        with mock.patch("os.scandir", side_effect=AssertionError("no directory inventory")), \
                mock.patch.object(Path, "iterdir", side_effect=AssertionError("no directory inventory")):
            archived = archive_locator(locator, self.roots, self.project)
            self.assertEqual(archived.root_kind, "archive")
            self.assertEqual(archived.relative_path, path.name)
            self.assertNotEqual(archived.source_id, locator.source_id)
            self.assertEqual(source_from_locator(archived, self.roots, self.project).path.name, path.name)
            self.assertIsNone(archive_locator(archived, self.roots, self.project))
        self.write(path)
        archive_path = self.roots[1] / path.name
        self.write(archive_path)
        # Coexistence does not overwrite the original candidate or its source ID.
        self.assertEqual(self.capture(path), locator)
        self.assertEqual(archive_locator(locator, self.roots, self.project), archived)
        path.unlink()
        self.assertEqual(archive_locator(locator, self.roots, self.project), archived)

    def test_segment_id_does_not_authorize_archive_derivation(self):
        path = self.path.with_name(f"rollout-test-{OTHER}_{THREAD}.jsonl")
        locator = self.capture(path)
        self.assertIsNotNone(locator)
        self.assertIsNone(archive_locator(locator, self.roots, self.project))

    def test_link_file_and_link_parent_are_rejected(self):
        self.write()
        other = self.base / "other"
        other.mkdir()
        link = self.roots[0] / "linked"
        try:
            os.symlink(other, link, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {type(error).__name__}")
        self.assertIsNone(self.capture(link / "missing.jsonl"))
        linked_file = self.roots[0] / "linked-file.jsonl"
        os.symlink(self.path, linked_file)
        self.assertIsNone(self.capture(linked_file))

    def test_reparse_metadata_is_rejected_without_following_or_opening(self):
        self.write()
        locator = self.capture()
        original = Path.lstat
        for unsafe in (self.path, self.path.parent, self.roots[0]):
            def observed(path):
                details = original(path)
                if os.path.normcase(str(path)) != os.path.normcase(str(unsafe)):
                    return details
                return SimpleNamespace(st_mode=details.st_mode, st_dev=details.st_dev,
                    st_ino=details.st_ino, st_size=details.st_size, st_mtime_ns=details.st_mtime_ns,
                    st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            with self.subTest(entry=unsafe.name), mock.patch.object(Path, "lstat", observed), \
                    mock.patch.object(Path, "open", side_effect=AssertionError("unsafe entry must not be opened")):
                self.assertIsNone(self.capture())
                with self.assertRaises(UsageError):
                    source_from_locator(locator, self.roots, self.project)


if __name__ == "__main__":
    unittest.main()
