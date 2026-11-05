"""Immutable package preparation must preserve physical validation boundaries."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from tests import m14_test_support as support
from tests import package_fixture_support as packages


class PackageFixtureTests(unittest.TestCase):
    def source(self, root):
        source = root / "source"
        source.mkdir()
        (source / "nested").mkdir()
        (source / "empty").mkdir()
        (source / "one.py").write_bytes(b"value = 'a'\n")
        (source / "nested/two.txt").write_bytes(b"second\n")
        (source / "config").mkdir()
        (source / "config/private.json").write_bytes(b"excluded")
        (source / "ignored.sqlite").write_bytes(b"excluded")
        (source / "release-manifest.json").write_text(
            json.dumps({"name":"fixture", "core_files":{}}), encoding="utf-8")
        return source

    def assert_manifest(self, root):
        manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
        expected = {path.as_posix(): "sha256:"+hashlib.sha256((root/path).read_bytes()).hexdigest()
                    for path in support._manifest_core_paths(root)}
        self.assertEqual(manifest["core_files"], expected)

    def test_real_current_content_and_manifest_match_independent_physical_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = support._copy_skill(root / "baseline")
            with mock.patch.object(support, "refresh_test_manifest", wraps=support.refresh_test_manifest) as refresh:
                basis = packages.freeze_package(support.SOURCE_SKILL_ROOT)
                first = support.make_physical_install(root / "first", package_basis=basis)
                second = support.make_physical_install(root / "second", package_basis=basis)
            self.assertEqual(refresh.call_count, 1)
            expected = support.file_snapshot(baseline)
            self.assertEqual(support.file_snapshot(first.skill_root), expected)
            self.assertEqual(support.file_snapshot(second.skill_root), expected)
            for relative in expected:
                left, right = first.skill_root / relative, second.skill_root / relative
                self.assertFalse(left.samefile(right))
                self.assertFalse(left.is_symlink())
                self.assertEqual(left.stat().st_nlink, 1)
                self.assertEqual(stat.S_IMODE(left.stat().st_mode), stat.S_IMODE((baseline/relative).stat().st_mode))
            self.assert_manifest(first.skill_root)
            result = first.run("setup", "--read-only", "--json")
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])
            self.assertFalse(first.db_path.exists())
            with self.assertRaises(FrozenInstanceError):
                basis.entries = ()

    def test_mutable_source_and_new_basis_do_not_reuse_same_path_size_or_mtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source(root)
            original = packages.freeze_package(source)
            info = (source / "one.py").stat()
            (source / "one.py").write_bytes(b"value = 'b'\n")
            os.utime(source / "one.py", ns=(info.st_atime_ns, info.st_mtime_ns))
            changed = packages.freeze_package(source)
            first, second = original.copy_to(root / "old"), changed.copy_to(root / "new")
            self.assertEqual((first/"one.py").read_bytes(), b"value = 'a'\n")
            self.assertEqual((second/"one.py").read_bytes(), b"value = 'b'\n")
            self.assertNotEqual((first/"release-manifest.json").read_bytes(), (second/"release-manifest.json").read_bytes())
            with mock.patch.object(support, "SOURCE_SKILL_ROOT", source):
                live = support._copy_skill(root / "live")
            self.assertEqual(support.file_snapshot(live), support.file_snapshot(second))
            (root/"other").mkdir()
            other = self.source(root/"other")
            (other/"one.py").write_bytes(b"value = 'c'\n")
            third = packages.freeze_package(other).copy_to(root / "different")
            self.assertEqual((third/"one.py").read_bytes(), b"value = 'c'\n")
            self.assert_manifest(third)

    def test_copy_changes_additions_and_deletions_regenerate_only_actual_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            basis = packages.freeze_package(self.source(root))
            first, second = basis.copy_to(root/"first"), basis.copy_to(root/"second")
            before = support.file_snapshot(second)
            (first/"one.py").write_bytes(b"edited\n")
            (first/"extra.txt").write_bytes(b"added\n")
            (first/"nested/two.txt").unlink()
            support.refresh_test_manifest(first)
            self.assert_manifest(first)
            self.assertEqual(support.file_snapshot(second), before)
            self.assertEqual(support.file_snapshot(basis.copy_to(root/"third")), before)
            self.assertTrue((first/"empty").is_dir())
            self.assertFalse((first/"config").exists())
            self.assertFalse((first/"ignored.sqlite").exists())

    def test_installed_cli_rejects_changed_bytes_and_corrupt_manifest_until_real_refresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            basis = packages.freeze_package(support.SOURCE_SKILL_ROOT)
            install = support.make_physical_install(root/"install", package_basis=basis)
            readme = install.skill_root/"SKILL.md"
            readme.write_bytes(readme.read_bytes()+b"\nFixture documentation change.\n")
            failed = install.run("setup", "--read-only", "--json")
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(json.loads(failed.stdout)["ok"])
            support.refresh_test_manifest(install.skill_root)
            valid = install.run("setup", "--read-only", "--json")
            self.assertEqual(valid.returncode, 0, valid.stdout or valid.stderr)
            self.assert_manifest(install.skill_root)
            manifest = install.skill_root/"release-manifest.json"
            manifest.write_bytes(b"{broken")
            failed = install.run("setup", "--read-only", "--json")
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(json.loads(failed.stdout)["ok"])
            pristine = basis.copy_to(root/"pristine")
            self.assertNotEqual(support.file_snapshot(pristine), support.file_snapshot(install.skill_root))
            self.assertFalse(install.db_path.exists())

    def test_old_schema_pin_refreshes_actual_copy_without_mutating_its_basis(self):
        from tests.test_setup import _switch_physical_fixture_schema
        from task_governance_tool import storage
        current = storage.SCHEMA_VERSION
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            basis = packages.freeze_package(support.SOURCE_SKILL_ROOT)
            install = support.make_physical_install(root/"old", package_basis=basis)
            _switch_physical_fixture_schema(install, current, 20)
            self.assert_manifest(install.skill_root)
            self.assertIn("SCHEMA_VERSION = 20", (install.skill_root/"scripts/task_governance_tool/storage.py").read_text(encoding="utf-8"))
            pristine = basis.copy_to(root/"pristine")
            self.assertIn(f"SCHEMA_VERSION = {current}", (pristine/"scripts/task_governance_tool/storage.py").read_text(encoding="utf-8"))
            result = install.run("setup", "--read-only", "--json")
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)

    def test_parallel_copies_and_changes_are_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            basis = packages.freeze_package(self.source(root))
            def create(index):
                copied = basis.copy_to(root/str(index))
                (copied/"one.py").write_bytes(str(index).encode("ascii"))
                support.refresh_test_manifest(copied)
                return copied
            with ThreadPoolExecutor(max_workers=4) as executor:
                copies = list(executor.map(create, range(4)))
            for index, copied in enumerate(copies):
                self.assertEqual((copied/"one.py").read_bytes(), str(index).encode("ascii"))
                self.assert_manifest(copied)
            self.assertEqual((basis.copy_to(root/"unchanged")/"one.py").read_bytes(), b"value = 'a'\n")

    def test_current_basis_is_shared_by_helper_import_aliases_and_parallel_first_use(self):
        alias_name = "taskgov_test_m14_alias"
        spec = importlib.util.spec_from_file_location(alias_name, support.__file__)
        alias = importlib.util.module_from_spec(spec)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(sys.modules, {alias_name:alias}):
            spec.loader.exec_module(alias)
            root = Path(temporary)
            basis = packages._CurrentPackageBasis()
            with mock.patch.object(packages, "CURRENT_TEST_PACKAGE", basis):
                with mock.patch.object(support, "refresh_test_manifest", wraps=support.refresh_test_manifest) as refresh:
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        futures = [executor.submit(module.make_current_physical_install, root/str(index))
                                   for index,module in enumerate((support, alias))]
                        installs = [future.result() for future in futures]
                    self.assertEqual(refresh.call_count, 1)
            self.assertEqual(support.file_snapshot(installs[0].skill_root), support.file_snapshot(installs[1].skill_root))
            protected = root/"occupied"
            protected.mkdir()
            (protected/"keep.txt").write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                basis.copy_to(protected)
            self.assertEqual((protected/"keep.txt").read_bytes(), b"keep")
            with self.assertRaises(ValueError):
                support._copy_skill(root/"bad", source_root=support.SOURCE_SKILL_ROOT, package_basis=basis)
            self.assertFalse((root/"bad").exists())
