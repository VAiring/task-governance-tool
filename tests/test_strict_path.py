"""Standard-first resolution and bounded native fallback failure boundaries."""

import ctypes
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.m14_test_support import SOURCE_SCRIPTS_ROOT  # initializes imports
from task_governance_tool import strict_path


ROOT = r"\Device\HarddiskVolume3"
TARGET = ROOT + r"\Project\Long Name"
DOS = r"C:\Project\Long Name"


def denied(code=5):
    error = PermissionError("private-detail")
    if code is not None:
        error.winerror = code
    return error


class InputPath:
    def __init__(self, value=DOS, observed=DOS):
        self.value, self.observed = value, observed

    def __str__(self):
        return self.value

    def resolve(self, *, strict):
        if strict:
            raise AssertionError("native branch must not repeat the strict resolver")
        return self.observed


class StrictPathTests(unittest.TestCase):
    def test_standard_success_is_returned_without_native_restrictions(self):
        for result in (Path("normal"), Path(r"\\server\share"), Path(r"Z:\alias")):
            with self.subTest(result=result):
                path = mock.Mock(spec=Path)
                path.resolve.return_value = result
                with mock.patch.object(strict_path, "_resolve_windows") as native:
                    self.assertIs(strict_path.resolve_strict_path(path), result)
                path.resolve.assert_called_once_with(strict=True)
                native.assert_not_called()

    def test_only_windows_access_denial_enters_native_branch(self):
        for platform, error in [("posix", denied()), ("nt", denied(None)),
                                *[("nt", denied(code)) for code in (2, 3, 32, 123)],
                                ("nt", ValueError("invalid")), ("nt", RuntimeError("loop"))]:
            with self.subTest(platform=platform, error=error):
                path = mock.Mock(spec=Path)
                path.resolve.side_effect = error
                with (mock.patch.object(strict_path, "os", SimpleNamespace(name=platform)),
                      mock.patch.object(strict_path, "_resolve_windows") as native):
                    with self.assertRaises(type(error)) as caught:
                        strict_path.resolve_strict_path(path)
                    self.assertIs(caught.exception, error)
                    native.assert_not_called()

    def test_native_success_and_uncertainty_keep_standard_failure_identity(self):
        path = mock.Mock(spec=Path)
        original = denied()
        path.resolve.side_effect = original
        with mock.patch.object(strict_path, "os", SimpleNamespace(name="nt")):
            expected = Path("physical")
            with mock.patch.object(strict_path, "_resolve_windows", return_value=expected):
                self.assertIs(strict_path.resolve_strict_path(path), expected)
            for error in (OSError(), ValueError(), RuntimeError(), TypeError()):
                with mock.patch.object(strict_path, "_resolve_windows", side_effect=error):
                    with self.assertRaises(PermissionError) as caught:
                        strict_path.resolve_strict_path(path)
                    self.assertIs(caught.exception, original)

    def native(self, observations, *, path=None):
        api = mock.Mock()
        api.final_nt.side_effect = observations
        with mock.patch.object(strict_path, "_WindowsMetadata", return_value=api):
            result = strict_path._resolve_windows(path or InputPath())
        return result, api

    def test_native_suffix_normalizes_short_name_and_parent_components(self):
        for value in (r"C:\Project\LONGNA~1", r"C:\Project\unused\..\Long Name"):
            result, api = self.native([ROOT, TARGET, TARGET, ROOT], path=InputPath(value))
            self.assertEqual(result, Path(DOS))
            self.assertEqual(api.final_nt.call_args_list, [
                mock.call("C:\\"), mock.call(strict_path.ntpath.abspath(value)),
                mock.call(DOS), mock.call("C:\\"),
            ])

    def test_root_itself_and_case_normalization_are_consistent(self):
        result, _ = self.native([ROOT, ROOT, ROOT, ROOT.lower()],
                               path=InputPath("c:\\", "c:\\"))
        self.assertEqual(result, Path("C:\\"))

    def test_root_target_reopen_binding_and_metadata_uncertainty_refuse(self):
        cases = [
            [ROOT + r"\subst", TARGET, TARGET, ROOT],
            [ROOT, r"\Device\HarddiskVolume30\Project", TARGET, ROOT],
            [ROOT, r"\Device\HarddiskVolume4\Project", TARGET, ROOT],
            [ROOT, TARGET, TARGET.lower(), ROOT],
            [ROOT, TARGET, TARGET, r"\Device\HarddiskVolume4"],
        ]
        for position in range(4):
            row = [ROOT, TARGET, TARGET, ROOT]
            row[position] = OSError("metadata-refused")
            cases.append(row)
        for row in cases:
            with self.subTest(row=row), self.assertRaises(OSError):
                self.native(row)
        with self.assertRaises(OSError):
            self.native([ROOT, TARGET, TARGET, ROOT],
                        path=InputPath(observed=r"C:\Project\LONGNA~1"))

    def test_unsupported_inputs_and_ambiguous_native_suffix_refuse(self):
        for value in (r"\\server\share", r"\\?\C:\Project", r"C:\bad:stream",
                      "C:\\bad\x00", r"C:\bad.", r"C:\NUL.txt", r"C:\COM¹"):
            with self.subTest(value=value):
                with mock.patch.object(strict_path, "_WindowsMetadata") as native:
                    with self.assertRaises(OSError):
                        strict_path._resolve_windows(InputPath(value))
                    native.assert_not_called()
        for suffix in (r"\..\escape", r"\x\.\y", r"\bad:stream", "\\bad ",
                       r"\NUL.txt", "\\bad\x00", r"\x\\y", r"\LPT³", r"\x/y"):
            with self.subTest(suffix=suffix), self.assertRaises(OSError):
                strict_path._rebuild_dos_path(DOS, ROOT, ROOT + suffix)


class NativeHandleTests(unittest.TestCase):
    def api(self, *, handle=7, size=10, close=True):
        api = strict_path._WindowsMetadata.__new__(strict_path._WindowsMetadata)
        api.ctypes = ctypes
        api.kernel = mock.Mock()
        api.kernel.CreateFileW.return_value = handle
        def final(handle, buffer, capacity, flags):
            buffer.value = TARGET
            return size
        api.kernel.GetFinalPathNameByHandleW.side_effect = final
        api.kernel.CloseHandle.return_value = close
        return api

    def test_metadata_access_only_and_handle_closed(self):
        api = self.api()
        self.assertEqual(api.final_nt(DOS), TARGET)
        api.kernel.CreateFileW.assert_called_once_with(DOS, 0, 0, None, 3, 0x02000000, None)
        call = api.kernel.GetFinalPathNameByHandleW.call_args.args
        self.assertEqual((call[0], call[2], call[3]), (7, 32768, 2))
        api.kernel.CloseHandle.assert_called_once_with(7)

    def test_open_failure_has_no_handle_to_close(self):
        for handle in (None, ctypes.c_void_p(-1).value):
            api = self.api(handle=handle)
            with self.assertRaises(OSError):
                api.final_nt(DOS)
            api.kernel.GetFinalPathNameByHandleW.assert_not_called()
            api.kernel.CloseHandle.assert_not_called()

    def test_native_failure_overflow_and_close_failure_refuse(self):
        for size, close in ((0, True), (32768, True), (40000, True), (10, False)):
            api = self.api(size=size, close=close)
            with self.assertRaises(OSError):
                api.final_nt(DOS)
            api.kernel.CloseHandle.assert_called_once_with(7)
        api = self.api()
        api.kernel.GetFinalPathNameByHandleW.side_effect = OSError("native-failure")
        with self.assertRaises(OSError):
            api.final_nt(DOS)
        api.kernel.CloseHandle.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()
