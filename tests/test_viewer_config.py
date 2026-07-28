from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import viewer_config  # noqa: E402
from task_governance_tool.viewer_config import (  # noqa: E402
    VIEWER_CONFIG_MAX_BYTES,
    ViewerConfigError,
    load_viewer_refresh_interval,
)


def valid_payload(interval: int = 30) -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile": "visibility-refresh-v1",
        "refresh_interval_seconds": interval,
    }


def write_config(root: Path, content: bytes) -> Path:
    path = root / "config" / "viewer.json"
    path.parent.mkdir()
    path.write_bytes(content)
    return path


class ViewerConfigTests(unittest.TestCase):
    def test_absent_config_directory_or_file_disables_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            self.assertEqual(load_viewer_refresh_interval(root), 0)
            (root / "config").mkdir()
            self.assertEqual(load_viewer_refresh_interval(root), 0)

    def test_valid_bounds_are_loaded(self):
        for interval in (5, 30, 3600):
            with self.subTest(interval=interval), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_config(
                    root,
                    json.dumps(valid_payload(interval)).encode("utf-8"),
                )

                self.assertEqual(
                    load_viewer_refresh_interval(root),
                    interval,
                )

    def test_strict_json_shape_and_values_are_rejected(self):
        invalid_documents = {
            "non_object": "[]",
            "malformed": "{",
            "duplicate": (
                '{"schema_version":1,"schema_version":1,'
                '"profile":"visibility-refresh-v1",'
                '"refresh_interval_seconds":30}'
            ),
            "unknown": json.dumps({**valid_payload(), "extra": 1}),
            "missing": json.dumps(
                {
                    "schema_version": 1,
                    "profile": "visibility-refresh-v1",
                }
            ),
            "wrong_schema": json.dumps(
                {**valid_payload(), "schema_version": 2}
            ),
            "bool_schema": json.dumps(
                {**valid_payload(), "schema_version": True}
            ),
            "wrong_profile": json.dumps(
                {**valid_payload(), "profile": "other"}
            ),
            "bool_interval": json.dumps(
                {**valid_payload(), "refresh_interval_seconds": True}
            ),
            "float_interval": json.dumps(
                {**valid_payload(), "refresh_interval_seconds": 30.0}
            ),
            "string_interval": json.dumps(
                {**valid_payload(), "refresh_interval_seconds": "30"}
            ),
            "null_interval": json.dumps(
                {**valid_payload(), "refresh_interval_seconds": None}
            ),
            "below_range": json.dumps(valid_payload(4)),
            "above_range": json.dumps(valid_payload(3601)),
            "non_finite": (
                '{"schema_version":1,"profile":"visibility-refresh-v1",'
                '"refresh_interval_seconds":NaN}'
            ),
            "oversized_integer": (
                '{"schema_version":1,"profile":"visibility-refresh-v1",'
                '"refresh_interval_seconds":123456789012345678901}'
            ),
        }
        for label, document in invalid_documents.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_config(root, document.encode("utf-8"))

                with self.assertRaises(ViewerConfigError) as failure:
                    load_viewer_refresh_interval(root)

                self.assertEqual(
                    str(failure.exception),
                    "viewer refresh profile is invalid",
                )

    def test_invalid_utf8_and_size_cap_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root, b"\xff")
            with self.assertRaises(ViewerConfigError):
                load_viewer_refresh_interval(root)

        encoded = json.dumps(
            valid_payload(),
            separators=(",", ":"),
        ).encode("utf-8")
        exact = encoded + b" " * (VIEWER_CONFIG_MAX_BYTES - len(encoded))
        self.assertEqual(len(exact), VIEWER_CONFIG_MAX_BYTES)
        for size, accepted in (
            (exact, True),
            (exact + b" ", False),
        ):
            with self.subTest(bytes=len(size)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_config(root, size)
                if accepted:
                    self.assertEqual(load_viewer_refresh_interval(root), 30)
                else:
                    with self.assertRaises(ViewerConfigError):
                        load_viewer_refresh_interval(root)

    def test_non_regular_config_and_parent_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config" / "viewer.json"
            path.mkdir(parents=True)
            with self.assertRaises(ViewerConfigError):
                load_viewer_refresh_interval(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").write_text("not a directory", encoding="utf-8")
            with self.assertRaises(ViewerConfigError):
                load_viewer_refresh_interval(root)

    def test_live_broken_and_parent_links_are_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_root = root / "config"
            config_root.mkdir()
            target = root / "target.json"
            target.write_text(json.dumps(valid_payload()), encoding="utf-8")
            linked = config_root / "viewer.json"
            try:
                linked.symlink_to(target)
            except OSError:
                linked.write_text(
                    json.dumps(valid_payload()),
                    encoding="utf-8",
                )
                linked_inode = linked.stat().st_ino
                real_is_reparse = viewer_config._is_reparse
                with mock.patch.object(
                    viewer_config,
                    "_is_reparse",
                    side_effect=lambda details: (
                        details.st_ino == linked_inode
                        or real_is_reparse(details)
                    ),
                ):
                    with self.assertRaises(ViewerConfigError):
                        load_viewer_refresh_interval(root)
                linked.unlink()
                link_stat = os.stat_result(
                    (stat.S_IFLNK | 0o777, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                )
                real_lstat = Path.lstat

                def broken_link_lstat(path):
                    if path == linked:
                        return link_stat
                    return real_lstat(path)

                with mock.patch.object(
                    Path,
                    "lstat",
                    autospec=True,
                    side_effect=broken_link_lstat,
                ):
                    with self.assertRaises(ViewerConfigError):
                        load_viewer_refresh_interval(root)
            else:
                with self.assertRaises(ViewerConfigError):
                    load_viewer_refresh_interval(root)
                linked.unlink()
                linked.symlink_to(root / "missing.json")
                with self.assertRaises(ViewerConfigError):
                    load_viewer_refresh_interval(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_root = root / "actual-config"
            target_root.mkdir()
            (target_root / "viewer.json").write_text(
                json.dumps(valid_payload()),
                encoding="utf-8",
            )
            try:
                (root / "config").symlink_to(
                    target_root,
                    target_is_directory=True,
                )
            except OSError:
                config_root = root / "config"
                config_root.mkdir()
                (config_root / "viewer.json").write_text(
                    json.dumps(valid_payload()),
                    encoding="utf-8",
                )
                config_inode = config_root.stat().st_ino
                real_is_reparse = viewer_config._is_reparse
                with mock.patch.object(
                    viewer_config,
                    "_is_reparse",
                    side_effect=lambda details: (
                        details.st_ino == config_inode
                        or real_is_reparse(details)
                    ),
                ):
                    with self.assertRaises(ViewerConfigError):
                        load_viewer_refresh_interval(root)
            else:
                with self.assertRaises(ViewerConfigError):
                    load_viewer_refresh_interval(root)

    def test_replacement_observation_fails_with_fixed_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_config(
                root,
                json.dumps(valid_payload()).encode("utf-8"),
            )
            replacement = path.with_name("replacement.json")
            replacement.write_text(
                json.dumps(valid_payload(31)),
                encoding="utf-8",
            )
            real_close = viewer_config.os.close
            replaced = False

            def close_and_replace(descriptor):
                nonlocal replaced
                real_close(descriptor)
                if not replaced:
                    replacement.replace(path)
                    replaced = True

            with mock.patch.object(
                viewer_config.os,
                "close",
                side_effect=close_and_replace,
            ):
                with self.assertRaises(ViewerConfigError) as failure:
                    load_viewer_refresh_interval(root)

            self.assertTrue(replaced)
            self.assertEqual(
                str(failure.exception),
                "viewer refresh profile is invalid",
            )
            self.assertNotIn(str(path), str(failure.exception))


if __name__ == "__main__":
    unittest.main()
