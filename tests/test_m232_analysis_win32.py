from __future__ import annotations

import os
import sys
import tempfile
import unittest
import ctypes
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool._analysis_win32 import (  # noqa: E402
    DP,
    FILE_ADD_SUBDIRECTORY,
    LOCK_FILE,
    OPEN_EXISTING,
    OC,
    OP,
    RH,
    ROOT as ROOT_ALIAS,
    S0,
    SC,
    SP,
    TH,
    ExplicitSecurityDescriptor,
    ExactSecurityProof,
    FileOpenAlias,
    Win32BoundaryError,
    Win32QuarantineRequired,
    abi_layout,
    create_relative_directory,
    create_relative_file,
    current_token_privileges,
    enumerate_held_directory,
    handle_is_inheritable,
    lock_byte_zero,
    open_or_create_relative_lock,
    open_or_create_status_directory,
    open_no_follow,
    open_relative_directory,
    open_relative_file,
    open_relative_file_if_present,
    prove_exact_handle_security,
    prove_held_directory_empty,
    prove_held_directory_membership,
    prove_held_membership,
    query_handle_identity,
    query_handle_name,
    read_handle_capped,
    remove_relative_directory,
    remove_relative_file,
    replace_relative_file,
    rollback_relative_handle,
    rename_handle_no_replace,
    set_delete_disposition,
    unlock_byte_zero,
    write_handle_flush,
    write_flush_reread,
)

from task_governance_tool import _analysis_win32 as win32_boundary  # noqa: E402


def _existing_alias(alias: FileOpenAlias) -> FileOpenAlias:
    return FileOpenAlias(alias.access, alias.share, OPEN_EXISTING, alias.flags)


@unittest.skipUnless(os.name == "nt", "typed Win32 boundary")
class AnalysisWin32Tests(unittest.TestCase):
    @staticmethod
    def _untrusted_security() -> ExplicitSecurityDescriptor:
        return ExplicitSecurityDescriptor.from_sddl("D:P(A;;GA;;;WD)")

    def test_exact_ctypes_abi_and_read_only_current_token_query(self):
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(DP.access, 0x00100087)
        self.assertEqual(DP.share, 0x00000002)
        self.assertEqual(S0.access, 0x00120087)
        self.assertEqual(S0.share, 0x00000002)
        self.assertEqual(S0.disposition, win32_boundary.NATIVE_FILE_OPEN_IF)
        self.assertEqual(S0.flags, 0x02200000)
        self.assertEqual(S0.access & 0x00010000, 0)
        self.assertEqual(S0.share & 0x00000004, 0)
        self.assertEqual(LOCK_FILE.share, 0x00000003)
        self.assertEqual(ROOT_ALIAS.share, 0)
        self.assertEqual(ROOT_ALIAS.access, 0x00130087)
        self.assertEqual(
            ROOT_ALIAS.access & FILE_ADD_SUBDIRECTORY,
            FILE_ADD_SUBDIRECTORY,
        )
        self.assertEqual(TH.access & 0x00020000, 0x00020000)
        self.assertEqual(RH.access & 0x00020000, 0x00020000)
        self.assertEqual(ROOT_ALIAS.access & 0x00020000, 0x00020000)
        self.assertEqual(
            abi_layout(),
            {
                "byte_size": 1,
                "wchar_size": 2,
                "dword_size": 4,
                "long_size": 4,
                "handle_size": pointer_size,
                "pointer_size": pointer_size,
                "overlapped_size": 32 if pointer_size == 8 else 20,
                "overlapped_event_offset": 24 if pointer_size == 8 else 16,
                "file_id_128_size": 16,
                "file_id_info_file_id_offset": 8,
                "file_id_info_size": 24,
                "file_standard_allocation_offset": 0,
                "file_standard_eof_offset": 8,
                "file_standard_links_offset": 16,
                "file_standard_delete_offset": 20,
                "file_standard_directory_offset": 21,
                "file_standard_size": 24,
                "file_attribute_tag_size": 8,
                "file_disposition_size": 1,
                "file_name_name_offset": 4,
                "file_id_extd_name_offset": 88,
                "luid_size": 8,
                "luid_attributes_offset": 8,
                "luid_attributes_size": 12,
                "token_privileges_entries_offset": 4,
                "file_rename_root_offset": 8 if pointer_size == 8 else 4,
                "file_rename_length_offset": 16 if pointer_size == 8 else 8,
                "file_rename_name_offset": 20 if pointer_size == 8 else 12,
                "file_rename_size": 24 if pointer_size == 8 else 16,
                "native_file_rename_root_offset": 8 if pointer_size == 8 else 4,
                "native_file_rename_length_offset": 16 if pointer_size == 8 else 8,
                "native_file_rename_name_offset": 20 if pointer_size == 8 else 12,
                "native_file_rename_size": 24 if pointer_size == 8 else 16,
                "unicode_string_buffer_offset": 8 if pointer_size == 8 else 4,
                "unicode_string_size": 16 if pointer_size == 8 else 8,
                "object_attributes_root_offset": 8 if pointer_size == 8 else 4,
                "object_attributes_name_offset": 16 if pointer_size == 8 else 8,
                "object_attributes_attributes_offset": (
                    24 if pointer_size == 8 else 12
                ),
                "object_attributes_security_offset": (
                    32 if pointer_size == 8 else 16
                ),
                "object_attributes_qos_offset": 40 if pointer_size == 8 else 20,
                "object_attributes_size": 48 if pointer_size == 8 else 24,
                "io_status_information_offset": pointer_size,
                "io_status_size": pointer_size * 2,
                "sid_attributes_attributes_offset": (
                    8 if pointer_size == 8 else 4
                ),
                "sid_attributes_size": 16 if pointer_size == 8 else 8,
                "token_user_size": 16 if pointer_size == 8 else 8,
                "acl_size_information_size": 12,
                "acl_revision_information_size": 4,
                "ace_header_size": 4,
                "access_ace_mask_offset": 4,
                "access_ace_prefix_size": 8,
            },
        )
        privileges = current_token_privileges()
        self.assertIsInstance(privileges, frozenset)
        self.assertTrue(privileges)
        self.assertTrue(all(isinstance(item, str) and item for item in privileges))

    def test_relative_root_create_exact_acl_collision_and_empty_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fresh-root"
            parent = open_no_follow(
                temporary,
                ROOT_ALIAS,
                expect_directory=True,
                kind="attempt-parent",
            )
            security = ExplicitSecurityDescriptor.root()
            created = None
            fixed_child = None
            try:
                created = create_relative_directory(
                    parent,
                    path.name,
                    security,
                    kind="attempt-root",
                )
                identity = query_handle_identity(created)
                self.assertTrue(identity.is_directory)
                self.assertFalse(identity.is_reparse)
                self.assertFalse(identity.delete_pending)
                self.assertEqual(identity.link_count, 1)
                self.assertFalse(handle_is_inheritable(created))
                self.assertEqual(
                    query_handle_name(created),
                    query_handle_name(parent).rstrip("\\") + "\\fresh-root",
                )
                self.assertTrue(
                    identity.same_object(
                        prove_held_directory_membership(
                            created,
                            parent,
                            "fresh-root",
                        )
                    )
                )
                proof = prove_exact_handle_security(created, security)
                self.assertIsInstance(proof, ExactSecurityProof)
                self.assertEqual(proof.policy, "root")
                self.assertTrue(proof.protected)
                self.assertEqual(proof.ace_kinds, ("deny", "allow", "allow"))
                self.assertEqual(
                    proof.ace_masks,
                    (0x000C0000, 0x00130087, 0x000000A0),
                )
                self.assertEqual(
                    proof.trustees,
                    ("owner_rights", "current_user", "restricted_code"),
                )

                fixed_child = create_relative_directory(
                    created,
                    "fixed-child",
                    security,
                    kind="analysis-fixed-child",
                )
                fixed_identity = query_handle_identity(fixed_child)
                self.assertTrue(fixed_identity.is_directory)
                self.assertEqual(
                    query_handle_name(fixed_child),
                    query_handle_name(created).rstrip("\\") + "\\fixed-child",
                )
                fixed_cleanup = rollback_relative_handle(
                    fixed_child,
                    created,
                    "fixed-child",
                    original=fixed_identity,
                )
                fixed_child = None
                self.assertEqual(fixed_cleanup.outcome, "original_absent")
                self.assertFalse(fixed_cleanup.foreign_replaced)

                with self.assertRaises(Win32BoundaryError) as raised:
                    create_relative_directory(parent, path.name, security)
                self.assertEqual(raised.exception.code, "analysis_destination_exists")
                self.assertTrue(identity.same_object(query_handle_identity(created)))

                cleanup = rollback_relative_handle(
                    created,
                    parent,
                    path.name,
                    original=identity,
                )
                created = None
                self.assertEqual(cleanup.outcome, "original_absent")
                self.assertFalse(cleanup.foreign_replaced)
                self.assertFalse(path.exists())
                self.assertEqual(
                    enumerate_held_directory(parent, maximum_entries=0),
                    (),
                )
            finally:
                if (
                    fixed_child is not None
                    and not fixed_child.closed
                    and created is not None
                    and not created.closed
                ):
                    remove_relative_directory(
                        fixed_child,
                        created,
                        "fixed-child",
                    )
                if created is not None and not created.closed:
                    remove_relative_directory(created, parent, path.name)
                security.close()
                parent.close()

    def test_status_directory_s0_direct_open_or_create_exact_proofs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = open_no_follow(
                temporary,
                ROOT_ALIAS,
                expect_directory=True,
                kind="analysis-root",
            )
            security = ExplicitSecurityDescriptor.root()
            first = second = cleanup = None
            created = False
            try:
                calls = []
                original = win32_boundary._nt_create_relative

                def observe_open_or_create(*args, **kwargs):
                    calls.append(dict(kwargs))
                    return original(*args, **kwargs)

                with patch.object(
                    win32_boundary,
                    "_nt_create_relative",
                    side_effect=observe_open_or_create,
                ):
                    opened = open_or_create_status_directory(
                        root,
                        "status",
                        security,
                        kind="analysis-status-s0",
                    )
                first = opened.handle
                created = opened.created
                self.assertTrue(created)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["desired_access"], S0.access)
                self.assertEqual(calls[0]["share_access"], S0.share)
                self.assertEqual(
                    calls[0]["disposition"],
                    S0.disposition,
                )
                self.assertEqual(calls[0]["create_options"], 0x00204021)
                first_identity = query_handle_identity(first)
                self.assertTrue(first_identity.is_directory)
                self.assertFalse(first_identity.is_reparse)
                self.assertFalse(first_identity.delete_pending)
                self.assertEqual(first_identity.link_count, 1)
                self.assertFalse(handle_is_inheritable(first))
                self.assertEqual(
                    query_handle_name(first),
                    query_handle_name(root).rstrip("\\") + "\\status",
                )
                self.assertTrue(
                    first_identity.same_object(
                        win32_boundary._prove_held_child(
                            first,
                            root,
                            "status",
                            expect_directory=True,
                        )
                    )
                )
                first_proof = prove_exact_handle_security(first, security)
                self.assertEqual(first_proof.policy, "root")
                self.assertEqual(
                    first_proof.ace_masks,
                    (0x000C0000, 0x00130087, 0x000000A0),
                )
                first.close()
                first = None

                reopened = open_or_create_status_directory(
                    root,
                    "status",
                    security,
                    kind="analysis-status-s0-existing",
                )
                second = reopened.handle
                self.assertFalse(reopened.created)
                self.assertTrue(
                    first_identity.same_object(query_handle_identity(second))
                )
                self.assertFalse(handle_is_inheritable(second))
                self.assertTrue(
                    first_identity.same_object(
                        win32_boundary._prove_held_child(
                            second,
                            root,
                            "status",
                            expect_directory=True,
                        )
                    )
                )
                self.assertEqual(
                    prove_exact_handle_security(second, security),
                    first_proof,
                )
            finally:
                if first is not None and not first.closed:
                    first.close()
                if second is not None and not second.closed:
                    second.close()
                if created:
                    cleanup = open_relative_directory(
                        root,
                        "status",
                        ROOT_ALIAS,
                        kind="analysis-status-cleanup",
                    )
                    remove_relative_directory(cleanup, root, "status")
                    cleanup = None
                security.close()
                root.close()

    def test_status_directory_created_proof_failure_retains_s0_quarantine(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = open_no_follow(
                temporary,
                ROOT_ALIAS,
                expect_directory=True,
                kind="analysis-root",
            )
            security = ExplicitSecurityDescriptor.root()
            retained = cleanup = None
            try:
                with (
                    patch.object(
                        win32_boundary,
                        "prove_exact_handle_security",
                        side_effect=Win32BoundaryError("injected_acl_failure"),
                    ),
                    self.assertRaises(Win32QuarantineRequired) as raised,
                ):
                    open_or_create_status_directory(
                        root,
                        "status",
                        security,
                    )
                retained = raised.exception.handle
                self.assertEqual(
                    raised.exception.phase,
                    "status_directory_create_unproved",
                )
                self.assertIsNotNone(retained)
                self.assertFalse(retained.closed)
                self.assertTrue(
                    query_handle_identity(retained).same_object(
                        win32_boundary._prove_held_child(
                            retained,
                            root,
                            "status",
                            expect_directory=True,
                        )
                    )
                )
                retained.close()
                retained = None
                cleanup = open_relative_directory(
                    root,
                    "status",
                    ROOT_ALIAS,
                    kind="analysis-status-quarantine-cleanup",
                )
                remove_relative_directory(cleanup, root, "status")
                cleanup = None
            finally:
                if retained is not None and not retained.closed:
                    retained.close()
                security.close()
                root.close()

    def test_lock_is_noninheritable_identity_stable_and_exclusive(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = open_no_follow(
                temporary,
                ROOT_ALIAS,
                expect_directory=True,
                kind="analysis-parent",
            )
            first = second = None
            lock = None
            try:
                path = Path(temporary) / "taskgov-analysis.lock"
                path.write_bytes(b"")
                existing_lock = _existing_alias(LOCK_FILE)
                first = open_no_follow(
                    path,
                    existing_lock,
                    expect_directory=False,
                    kind="lease",
                    parent=parent,
                    basename=path.name,
                )
                second = open_no_follow(
                    path,
                    existing_lock,
                    expect_directory=False,
                    kind="lease-contender",
                    parent=parent,
                    basename=path.name,
                )
                self.assertFalse(handle_is_inheritable(first))
                before = query_handle_identity(first)
                self.assertEqual(before.link_count, 1)
                self.assertFalse(before.is_reparse)
                lock = lock_byte_zero(first)
                with self.assertRaises(Win32BoundaryError) as raised:
                    lock_byte_zero(second)
                self.assertEqual(raised.exception.code, "analysis_busy")
                self.assertTrue(before.same_object(query_handle_identity(first)))
                unlock_byte_zero(lock)
                self.assertTrue(lock.released)
                lock = None
            finally:
                if lock is not None and not lock.released:
                    unlock_byte_zero(lock)
                if second is not None and not second.closed:
                    second.close()
                if first is not None and not first.closed:
                    first.close()
                if not parent.closed:
                    parent.close()

    def test_lease_parent_share_violation_busy_mapping_is_explicit_and_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            held = open_no_follow(
                temporary,
                ROOT_ALIAS,
                expect_directory=True,
                kind="held-lease-parent",
            )
            try:
                with self.assertRaises(Win32BoundaryError) as raised:
                    open_no_follow(
                        temporary,
                        ROOT_ALIAS,
                        expect_directory=True,
                        kind="lease-parent-contender",
                        lease_parent_busy_on_sharing_violation=True,
                    )
                self.assertEqual(raised.exception.code, "analysis_busy")

                with self.assertRaises(Win32BoundaryError) as default_raised:
                    open_no_follow(
                        temporary,
                        ROOT_ALIAS,
                        expect_directory=True,
                        kind="default-parent-contender",
                    )
                self.assertEqual(
                    default_raised.exception.code,
                    "analysis_process_unsafe",
                )
            finally:
                held.close()

            kernel32 = win32_boundary._kernel32()

            def access_denied(*_args):
                ctypes.set_last_error(5)
                return win32_boundary.INVALID_HANDLE_VALUE

            with patch.object(
                kernel32,
                "CreateFileW",
                side_effect=access_denied,
            ):
                with self.assertRaises(Win32BoundaryError) as denied:
                    open_no_follow(
                        temporary,
                        ROOT_ALIAS,
                        expect_directory=True,
                        kind="denied-lease-parent",
                        lease_parent_busy_on_sharing_violation=True,
                    )
            self.assertEqual(denied.exception.code, "analysis_process_unsafe")

            with patch.object(
                kernel32,
                "CreateFileW",
                side_effect=AssertionError("unexpected CreateFileW"),
            ):
                with self.assertRaises(Win32BoundaryError) as invalid:
                    open_no_follow(
                        temporary,
                        DP,
                        expect_directory=True,
                        kind="invalid-busy-scope",
                        lease_parent_busy_on_sharing_violation=True,
                    )
            self.assertEqual(invalid.exception.code, "analysis_argument_invalid")

            with patch.object(
                kernel32,
                "CreateFileW",
                side_effect=AssertionError("unexpected CreateFileW"),
            ):
                with self.assertRaises(Win32BoundaryError) as relative:
                    open_no_follow(
                        "relative-lease-parent",
                        ROOT_ALIAS,
                        expect_directory=True,
                        kind="invalid-relative-busy-scope",
                        lease_parent_busy_on_sharing_violation=True,
                    )
            self.assertEqual(relative.exception.code, "analysis_argument_invalid")

    def test_relative_report_temp_immediate_df_acl_parent_and_no_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "leaf.json"
            parent = open_no_follow(
                temporary,
                ROOT_ALIAS,
                expect_directory=True,
                kind="parent",
            )
            security = ExplicitSecurityDescriptor.report_temp()
            untrusted = self._untrusted_security()
            leaf = None
            try:
                with self.assertRaises(Win32BoundaryError) as raised:
                    create_relative_file(
                        parent,
                        path.name,
                        TH,
                        untrusted,
                    )
                self.assertEqual(raised.exception.code, "analysis_argument_invalid")
                self.assertFalse(path.exists())

                for invalid in ("..\\wrong.tmp", "wrong.tmp:stream", "bad\0name"):
                    with self.assertRaises(Win32BoundaryError) as raised:
                        create_relative_file(
                            parent,
                            invalid,
                            TH,
                            security,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "analysis_argument_invalid",
                    )

                leaf = create_relative_file(
                    parent,
                    path.name,
                    TH,
                    security,
                    kind="report-temp",
                )
                identity = query_handle_identity(leaf)
                self.assertFalse(identity.is_directory)
                self.assertFalse(identity.is_reparse)
                self.assertTrue(identity.delete_pending)
                self.assertEqual(identity.link_count, 0)
                self.assertEqual(identity.size, 0)
                self.assertFalse(handle_is_inheritable(leaf))
                self.assertTrue(
                    identity.same_object(
                        prove_held_membership(leaf, parent, path.name)
                    )
                )
                proof = prove_exact_handle_security(leaf, security)
                self.assertEqual(proof.policy, "report-temp")
                self.assertEqual(proof.ace_kinds, ("deny", "allow"))
                self.assertEqual(
                    proof.ace_masks,
                    (0x000C0000, TH.access),
                )
                self.assertEqual(
                    proof.trustees,
                    ("owner_rights", "current_user"),
                )

                with self.assertRaises(Win32BoundaryError) as raised:
                    create_relative_file(
                        parent,
                        path.name,
                        TH,
                        security,
                    )
                self.assertEqual(raised.exception.code, "analysis_destination_exists")
                self.assertEqual(query_handle_identity(leaf).size, 0)

                remove_relative_file(leaf, parent, path.name)
                leaf = None
                self.assertFalse(path.exists())

                path.write_bytes(b"preexisting")
                with self.assertRaises(Win32BoundaryError) as raised:
                    create_relative_file(
                        parent,
                        path.name,
                        TH,
                        security,
                    )
                self.assertEqual(raised.exception.code, "analysis_destination_exists")
                self.assertEqual(path.read_bytes(), b"preexisting")
            finally:
                if leaf is not None and not leaf.closed:
                    remove_relative_file(leaf, parent, path.name)
                untrusted.close()
                security.close()
                parent.close()

    def test_relative_private_leaf_alias_set_is_closed_and_delete_on_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = open_no_follow(
                root,
                ROOT_ALIAS,
                expect_directory=True,
                kind="private-leaf-parent",
            )
            try:
                for alias, basename in (
                    (OP, "output.json"),
                    (SP, "output-schema.json"),
                ):
                    security = ExplicitSecurityDescriptor.private_leaf(alias)
                    leaf = None
                    try:
                        leaf = create_relative_file(
                            parent,
                            basename,
                            alias,
                            security,
                        )
                        expected_aces = security._expected_aces
                        self.assertEqual(len(expected_aces), 3)
                        masks = tuple(item.mask for item in expected_aces)
                        trustees = tuple(item.trustee for item in expected_aces)
                        if alias is OP:
                            self.assertEqual(
                                masks,
                                (
                                    0x000C0000,
                                    OP.access | OC.access,
                                    OC.access,
                                ),
                            )
                            self.assertEqual(
                                (OP.access | OC.access) & OC.access,
                                OC.access,
                            )
                        else:
                            self.assertEqual(
                                masks,
                                (0x000C0000, SP.access, SC.access),
                            )
                        self.assertEqual(
                            trustees,
                            ("owner_rights", "current_user", "restricted_code"),
                        )
                        identity = query_handle_identity(leaf)
                        self.assertFalse(identity.is_directory)
                        self.assertFalse(identity.is_reparse)
                        # Native FILE_DELETE_ON_CLOSE is bound to the open but
                        # is not reported as FileStandardInfo.DeletePending.
                        self.assertFalse(identity.delete_pending)
                        self.assertEqual(identity.link_count, 1)
                        self.assertEqual(identity.size, 0)
                        if alias is OP:
                            consumer = open_relative_file(
                                parent,
                                basename,
                                OC,
                                owner_handle=leaf,
                            )
                            try:
                                write_handle_flush(consumer, b"fixture", maximum=32)
                            finally:
                                consumer.close()
                            self.assertEqual(
                                read_handle_capped(leaf, maximum=32),
                                b"fixture",
                            )
                        else:
                            write_flush_reread(leaf, b"{}", maximum=32)
                            consumer = open_relative_file(parent, basename, SC)
                            try:
                                self.assertEqual(
                                    read_handle_capped(consumer, maximum=32),
                                    b"{}",
                                )
                            finally:
                                consumer.close()
                        remove_relative_file(leaf, parent, basename)
                        leaf = None
                        self.assertFalse((root / basename).exists())
                    finally:
                        if leaf is not None and not leaf.closed:
                            remove_relative_file(leaf, parent, basename)
                        security.close()

                forged = FileOpenAlias(OP.access, OP.share, OP.disposition, OP.flags)
                security = ExplicitSecurityDescriptor.private_leaf(OP)
                try:
                    with self.assertRaises(Win32BoundaryError) as raised:
                        create_relative_file(
                            parent,
                            "forged.json",
                            forged,
                            security,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "analysis_argument_invalid",
                    )
                    self.assertFalse((root / "forged.json").exists())
                finally:
                    security.close()
            finally:
                parent.close()

    def test_hard_linked_leaf_is_rejected_without_content_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.json"
            second = Path(temporary) / "second.json"
            first.write_bytes(b"do-not-read")
            os.link(first, second)
            with self.assertRaises(Win32BoundaryError):
                open_no_follow(
                    first,
                    RH,
                    expect_directory=False,
                    kind="foreign-hard-link",
                )
            self.assertEqual(first.read_bytes(), b"do-not-read")
            self.assertEqual(second.read_bytes(), b"do-not-read")

    def test_relative_open_rejects_wrong_parent_type_reparse_and_link_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            regular = root / "regular.json"
            regular.write_bytes(b"safe")
            outside = root / "outside"
            outside.mkdir()
            (outside / "other.json").write_bytes(b"outside")
            directory_leaf = root / "directory.json"
            directory_leaf.mkdir()
            hard_first = root / "hard-first.json"
            hard_second = root / "hard-second.json"
            hard_first.write_bytes(b"linked")
            os.link(hard_first, hard_second)
            parent = open_no_follow(
                root,
                ROOT_ALIAS,
                expect_directory=True,
                kind="relative-parent",
            )
            opened = None
            try:
                opened = open_relative_file(parent, regular.name)
                identity = query_handle_identity(opened)
                self.assertFalse(identity.is_directory)
                self.assertFalse(identity.is_reparse)
                self.assertEqual(identity.link_count, 1)
                self.assertTrue(
                    identity.same_object(
                        prove_held_membership(opened, parent, regular.name)
                    )
                )
                opened.close()
                opened = None

                for basename in ("other.json", directory_leaf.name, hard_first.name):
                    with self.subTest(basename=basename):
                        with self.assertRaises(Win32BoundaryError):
                            open_relative_file(parent, basename)
                self.assertEqual((outside / "other.json").read_bytes(), b"outside")
                self.assertEqual(hard_first.read_bytes(), b"linked")
                self.assertEqual(hard_second.read_bytes(), b"linked")

                reparse = root / "reparse.json"
                try:
                    os.symlink(regular, reparse)
                except OSError:
                    reparse = None
                if reparse is not None:
                    with self.assertRaises(Win32BoundaryError):
                        open_relative_file(parent, reparse.name)
                    self.assertEqual(regular.read_bytes(), b"safe")
            finally:
                if opened is not None and not opened.closed:
                    opened.close()
                parent.close()

    def test_relative_directory_open_uses_exact_dp_r0_and_rejects_unsafe_leaf(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = root / "child"
            child.mkdir()
            regular = root / "regular"
            regular.write_bytes(b"file")
            nested = root / "nested"
            nested.mkdir()
            (nested / "wrong-parent").mkdir()
            reparse = root / "directory-reparse"
            try:
                os.symlink(child, reparse, target_is_directory=True)
            except OSError:
                reparse = None
            parent = open_no_follow(
                root,
                ROOT_ALIAS,
                expect_directory=True,
                kind="directory-parent",
            )
            opened = None
            try:
                for alias in (DP, ROOT_ALIAS):
                    opened = open_relative_directory(parent, child.name, alias)
                    identity = query_handle_identity(opened)
                    parent_identity = query_handle_identity(parent)
                    self.assertTrue(identity.is_directory)
                    self.assertFalse(identity.is_reparse)
                    self.assertEqual(identity.link_count, 1)
                    self.assertEqual(
                        identity.volume_serial_number,
                        parent_identity.volume_serial_number,
                    )
                    self.assertFalse(handle_is_inheritable(opened))
                    opened.close()
                    opened = None

                forged = FileOpenAlias(
                    DP.access,
                    DP.share,
                    DP.disposition,
                    DP.flags,
                )
                with self.assertRaises(Win32BoundaryError) as raised:
                    open_relative_directory(parent, child.name, forged)
                self.assertEqual(raised.exception.code, "analysis_argument_invalid")

                for basename in (regular.name, "wrong-parent"):
                    with self.subTest(basename=basename):
                        with self.assertRaises(Win32BoundaryError):
                            open_relative_directory(parent, basename, DP)
                self.assertEqual(regular.read_bytes(), b"file")
                if reparse is not None:
                    with self.assertRaises(Win32BoundaryError):
                        open_relative_directory(parent, reparse.name, DP)
                    self.assertTrue(child.is_dir())
            finally:
                if opened is not None and not opened.closed:
                    opened.close()
                parent.close()

    def test_open_if_present_distinguishes_exact_missing_access_type_and_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            present = root / "present.json"
            present.write_bytes(b"12345")
            wrong_type = root / "wrong-type.json"
            wrong_type.mkdir()
            reparse = root / "reparse.json"
            try:
                os.symlink(present, reparse)
            except OSError:
                reparse = None
            parent = open_no_follow(
                root,
                ROOT_ALIAS,
                expect_directory=True,
                kind="recovery-parent",
            )
            opened = None
            try:
                self.assertIsNone(
                    open_relative_file_if_present(
                        parent,
                        "missing.json",
                        maximum=5,
                    )
                )
                ntdll = win32_boundary._ntdll()
                with patch.object(
                    ntdll,
                    "NtCreateFile",
                    return_value=ctypes.c_int32(
                        win32_boundary.STATUS_OBJECT_NAME_NOT_FOUND
                    ).value,
                ):
                    self.assertIsNone(
                        open_relative_file_if_present(
                            parent,
                            "mock-missing.json",
                            maximum=5,
                        )
                    )
                with (
                    patch.object(
                        ntdll,
                        "NtCreateFile",
                        return_value=ctypes.c_int32(
                            win32_boundary.STATUS_OBJECT_PATH_NOT_FOUND
                        ).value,
                    ),
                    self.assertRaises(Win32BoundaryError) as path_missing,
                ):
                    open_relative_file_if_present(
                        parent,
                        "unsafe-path.json",
                        maximum=5,
                    )
                self.assertEqual(
                    path_missing.exception.code,
                    "analysis_process_unsafe",
                )
                with (
                    patch.object(
                        ntdll,
                        "NtCreateFile",
                        return_value=ctypes.c_int32(
                            win32_boundary.STATUS_ACCESS_DENIED
                        ).value,
                    ),
                    self.assertRaises(Win32BoundaryError) as raised,
                ):
                    open_relative_file_if_present(
                        parent,
                        "access-denied.json",
                        maximum=5,
                    )
                self.assertEqual(raised.exception.code, "analysis_process_unsafe")

                with self.assertRaises(Win32BoundaryError) as raised:
                    open_relative_file_if_present(
                        parent,
                        present.name,
                        maximum=4,
                    )
                self.assertEqual(raised.exception.code, "analysis_file_too_large")
                self.assertEqual(present.read_bytes(), b"12345")

                opened = open_relative_file_if_present(
                    parent,
                    present.name,
                    maximum=5,
                )
                self.assertIsNotNone(opened)
                self.assertEqual(read_handle_capped(opened, maximum=5), b"12345")
                opened.close()
                opened = None

                with self.assertRaises(Win32BoundaryError):
                    open_relative_file_if_present(
                        parent,
                        wrong_type.name,
                        maximum=5,
                    )
                if reparse is not None:
                    with self.assertRaises(Win32BoundaryError):
                        open_relative_file_if_present(
                            parent,
                            reparse.name,
                            maximum=5,
                        )
                self.assertEqual(present.read_bytes(), b"12345")
            finally:
                if opened is not None and not opened.closed:
                    opened.close()
                parent.close()

    def test_rollback_classifies_foreign_replacement_without_touching_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = open_no_follow(
                root,
                DP,
                expect_directory=True,
                kind="rollback-parent",
            )
            security = ExplicitSecurityDescriptor.report_temp()
            original_handle = replacement = None
            try:
                original_handle = create_relative_file(
                    parent,
                    "report.json",
                    TH,
                    security,
                )
                write_flush_reread(original_handle, b"original", maximum=32)
                original = set_delete_disposition(
                    original_handle,
                    delete=False,
                )
                original_close = win32_boundary.OwnedHandle.close
                replacement_holder: list[object] = []

                def close_then_replace(candidate):
                    nonlocal replacement
                    original_close(candidate)
                    if candidate is original_handle and not replacement_holder:
                        replacement = create_relative_file(
                            parent,
                            "report.json",
                            TH,
                            security,
                            kind="foreign-replacement",
                        )
                        write_flush_reread(replacement, b"foreign", maximum=32)
                        set_delete_disposition(replacement, delete=False)
                        replacement_holder.append(replacement)

                with patch.object(
                    win32_boundary.OwnedHandle,
                    "close",
                    new=close_then_replace,
                ):
                    result = rollback_relative_handle(
                        original_handle,
                        parent,
                        "report.json",
                        original=original,
                    )
                original_handle = None
                self.assertEqual(result.outcome, "foreign_replaced")
                self.assertTrue(result.foreign_replaced)
                self.assertIsNotNone(replacement)
                self.assertEqual(
                    read_handle_capped(replacement, maximum=32),
                    b"foreign",
                )
                replacement_identity = query_handle_identity(replacement)
                cleanup = rollback_relative_handle(
                    replacement,
                    parent,
                    "report.json",
                    original=replacement_identity,
                )
                replacement = None
                self.assertEqual(cleanup.outcome, "original_absent")
            finally:
                if replacement is not None and not replacement.closed:
                    remove_relative_file(replacement, parent, "report.json")
                if original_handle is not None and not original_handle.closed:
                    remove_relative_file(original_handle, parent, "report.json")
                security.close()
                parent.close()

    def test_promoted_leaf_rollback_close_failure_quarantines_then_recovers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = open_no_follow(
                root,
                DP,
                expect_directory=True,
                kind="rollback-parent",
            )
            security = ExplicitSecurityDescriptor.report_temp()
            handle = None
            kernel32 = win32_boundary._kernel32()
            try:
                handle = create_relative_file(
                    parent,
                    "report.md",
                    TH,
                    security,
                )
                write_flush_reread(handle, b"report", maximum=32)
                original = set_delete_disposition(handle, delete=False)
                with (
                    patch.object(kernel32, "CloseHandle", return_value=0),
                    self.assertRaises(Win32QuarantineRequired) as raised,
                ):
                    rollback_relative_handle(
                        handle,
                        parent,
                        "report.md",
                        original=original,
                    )
                self.assertEqual(
                    raised.exception.phase,
                    "created_object_cleanup_unproved",
                )
                self.assertIs(raised.exception.handle, handle)
                self.assertFalse(handle.closed)
                pending = query_handle_identity(handle)
                self.assertTrue(pending.delete_pending)
                self.assertEqual(pending.link_count, 0)

                cleanup = rollback_relative_handle(
                    handle,
                    parent,
                    "report.md",
                    original=original,
                )
                handle = None
                self.assertEqual(cleanup.outcome, "original_absent")
                self.assertFalse((root / "report.md").exists())
            finally:
                if handle is not None and not handle.closed:
                    remove_relative_file(handle, parent, "report.md")
                security.close()
                parent.close()

    def test_relative_lock_reports_created_opened_and_removes_failed_create(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = open_no_follow(
                root,
                ROOT_ALIAS,
                expect_directory=True,
                kind="lease-parent",
            )
            security = ExplicitSecurityDescriptor.lease()
            first = second = retained = None
            try:
                first_result = open_or_create_relative_lock(
                    parent,
                    "taskgov-analysis.lock",
                    security,
                )
                first = first_result.handle
                self.assertTrue(first_result.created)
                self.assertFalse(handle_is_inheritable(first))
                self.assertEqual(
                    prove_exact_handle_security(first, security).policy,
                    "lease",
                )
                first_identity = query_handle_identity(first)
                first.close()
                first = None

                second_result = open_or_create_relative_lock(
                    parent,
                    "taskgov-analysis.lock",
                    security,
                )
                second = second_result.handle
                self.assertFalse(second_result.created)
                self.assertTrue(
                    first_identity.same_object(query_handle_identity(second))
                )
                second.close()
                second = None
                self.assertTrue((root / "taskgov-analysis.lock").is_file())

                native_results = []
                delete_handles = []
                original_native = win32_boundary._nt_create_relative
                original_delete = win32_boundary._set_delete_disposition_unchecked

                def tracked_native(*args, **kwargs):
                    result = original_native(*args, **kwargs)
                    native_results.append(result)
                    return result

                def tracked_delete(handle, *, delete):
                    delete_handles.append(handle)
                    return original_delete(handle, delete=delete)

                with (
                    patch.object(
                        win32_boundary,
                        "_nt_create_relative",
                        side_effect=tracked_native,
                    ),
                    patch.object(
                        win32_boundary,
                        "_set_delete_disposition_unchecked",
                        side_effect=tracked_delete,
                    ),
                    patch.object(
                        win32_boundary,
                        "prove_exact_handle_security",
                        side_effect=Win32BoundaryError("injected_acl_failure"),
                    ),
                    self.assertRaises(Win32BoundaryError) as raised,
                ):
                    open_or_create_relative_lock(
                        parent,
                        "failed-create.lock",
                        security,
                    )
                self.assertEqual(raised.exception.code, "injected_acl_failure")
                self.assertEqual(len(native_results), 1)
                created_handle = native_results[0][0]
                self.assertEqual(delete_handles, [created_handle])
                self.assertTrue(created_handle.closed)
                self.assertFalse((root / "failed-create.lock").exists())

                kernel32 = win32_boundary._kernel32()
                with (
                    patch.object(
                        win32_boundary,
                        "prove_exact_handle_security",
                        side_effect=Win32BoundaryError("injected_acl_failure"),
                    ),
                    patch.object(
                        kernel32,
                        "SetFileInformationByHandle",
                        return_value=0,
                    ),
                    self.assertRaises(Win32QuarantineRequired) as quarantine,
                ):
                    open_or_create_relative_lock(
                        parent,
                        "uncertain-create.lock",
                        security,
                    )
                retained = quarantine.exception.handle
                self.assertEqual(
                    quarantine.exception.phase,
                    "created_object_cleanup_unproved",
                )
                self.assertIsNotNone(retained)
                self.assertFalse(retained.closed)
                retained_identity = query_handle_identity(retained)
                self.assertFalse(retained_identity.delete_pending)
                cleanup = rollback_relative_handle(
                    retained,
                    parent,
                    "uncertain-create.lock",
                    original=retained_identity,
                )
                retained = None
                self.assertEqual(cleanup.outcome, "original_absent")
            finally:
                if retained is not None and not retained.closed:
                    rollback_relative_handle(
                        retained,
                        parent,
                        "uncertain-create.lock",
                        original=query_handle_identity(retained),
                    )
                if second is not None and not second.closed:
                    second.close()
                if first is not None and not first.closed:
                    first.close()
                security.close()
                parent.close()

    def test_post_proof_directory_cleanup_failure_retains_quarantine_handle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "quarantined-root"
            parent = open_no_follow(
                root,
                ROOT_ALIAS,
                expect_directory=True,
                kind="quarantine-parent",
            )
            security = ExplicitSecurityDescriptor.root()
            retained = None
            kernel32 = win32_boundary._kernel32()
            try:
                original_close = kernel32.CloseHandle
                with (
                    patch.object(
                        win32_boundary,
                        "prove_exact_handle_security",
                        side_effect=Win32BoundaryError("injected_acl_failure"),
                    ),
                    patch.object(kernel32, "CloseHandle", return_value=0),
                    self.assertRaises(Win32QuarantineRequired) as raised,
                ):
                    create_relative_directory(parent, path.name, security)
                retained = raised.exception.handle
                self.assertEqual(
                    raised.exception.phase,
                    "created_object_cleanup_unproved",
                )
                self.assertIsNotNone(retained)
                self.assertFalse(retained.closed)
                retained_identity = query_handle_identity(retained)
                self.assertTrue(retained_identity.delete_pending)
                self.assertTrue(
                    any(
                        entry.file_id == retained_identity.file_id
                        for entry in enumerate_held_directory(
                            parent,
                            maximum_entries=1,
                        )
                    )
                )

                with patch.object(kernel32, "CloseHandle", original_close):
                    win32_boundary._cleanup_created_relative(
                        retained,
                        parent,
                        path.name,
                        original=query_handle_identity(retained),
                    )
                retained = None
                self.assertFalse(path.exists())
            finally:
                if retained is not None and not retained.closed:
                    win32_boundary._cleanup_created_relative(
                        retained,
                        parent,
                        path.name,
                        original=query_handle_identity(retained),
                    )
                security.close()
                parent.close()

    def test_directory_enumeration_stops_at_limit_and_spans_buffers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(700):
                (root / f"{index:04d}-{'x' * 80}").write_bytes(b"")
            handle = open_no_follow(
                root,
                ROOT_ALIAS,
                expect_directory=True,
                kind="bounded-directory",
            )
            try:
                with self.assertRaises(Win32BoundaryError) as raised:
                    enumerate_held_directory(handle, maximum_entries=32)
                self.assertEqual(
                    raised.exception.code,
                    "analysis_directory_entry_limit",
                )
            finally:
                handle.close()
            handle = open_no_follow(
                root,
                ROOT_ALIAS,
                expect_directory=True,
                kind="multi-buffer-directory",
            )
            try:
                entries = enumerate_held_directory(handle, maximum_entries=700)
                self.assertEqual(len(entries), 700)
                self.assertEqual(entries[0].name[:5], "0000-")
                self.assertEqual(entries[-1].name[:5], "0699-")
            finally:
                handle.close()

    def test_cleanup_failure_retains_explicit_quarantine_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.write_bytes(b"x")
            os.link(first, second)
            kernel32 = win32_boundary._kernel32()
            with patch.object(kernel32, "CloseHandle", return_value=0):
                with self.assertRaises(Win32QuarantineRequired) as raised:
                    open_no_follow(
                        first,
                        RH,
                        expect_directory=False,
                        kind="quarantined-handle",
                    )
            retained = raised.exception.handle
            self.assertEqual(raised.exception.phase, "handle_close_unproved")
            self.assertFalse(retained.closed)
            retained.close()

            lock_path = root / "lock"
            lock_path.write_bytes(b"")
            parent = open_no_follow(
                root,
                ROOT_ALIAS,
                expect_directory=True,
                kind="lock-parent",
            )
            handle = open_no_follow(
                lock_path,
                _existing_alias(LOCK_FILE),
                expect_directory=False,
                kind="lock",
                parent=parent,
                basename="lock",
            )
            retained_lock = None
            try:
                identity = query_handle_identity(handle)
                changed = replace(identity, link_count=2)
                with (
                    patch.object(
                        win32_boundary,
                        "query_handle_identity",
                        side_effect=(identity, changed),
                    ),
                    patch.object(kernel32, "UnlockFileEx", return_value=0),
                ):
                    with self.assertRaises(Win32QuarantineRequired) as raised:
                        lock_byte_zero(handle)
                retained_lock = raised.exception.lock
                self.assertEqual(raised.exception.phase, "lock_release_unproved")
                self.assertIsNotNone(retained_lock)
                self.assertFalse(retained_lock.released)
                unlock_byte_zero(retained_lock)
                retained_lock = None
            finally:
                if retained_lock is not None and not retained_lock.released:
                    unlock_byte_zero(retained_lock)
                handle.close()
                parent.close()

    def test_rename_rejects_invalid_utf16_and_over_cap_before_native_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.tmp"
            source_path.write_bytes(b"safe")
            parent = open_no_follow(
                root,
                DP,
                expect_directory=True,
                kind="rename-parent",
            )
            source = open_no_follow(
                source_path,
                _existing_alias(TH),
                expect_directory=False,
                kind="rename-source",
                parent=parent,
                basename="source.tmp",
            )
            try:
                for invalid in (
                    "nul\0name",
                    "\ud800",
                    "x" * 65_537,
                ):
                    with self.subTest(length=len(invalid)):
                        with (
                            patch.object(
                                win32_boundary,
                                "_ntdll",
                                side_effect=AssertionError("native call forbidden"),
                            ),
                            self.assertRaises(Win32BoundaryError) as raised,
                        ):
                            rename_handle_no_replace(source, parent, invalid)
                        self.assertEqual(
                            raised.exception.code,
                            "analysis_argument_invalid",
                        )
                prove_held_membership(source, parent, "source.tmp")
                self.assertEqual(read_handle_capped(source, maximum=16), b"safe")
            finally:
                source.close()
                parent.close()

    def test_held_enumeration_write_flush_delete_and_no_replace_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source_path = base / "source"
            destination_path = base / "destination"
            source_path.mkdir()
            destination_path.mkdir()
            source_parent = open_no_follow(
                source_path,
                ROOT_ALIAS,
                expect_directory=True,
                kind="source-parent",
            )
            destination_parent = open_no_follow(
                destination_path,
                DP,
                expect_directory=True,
                kind="destination-parent",
            )
            promoted = collision = None
            try:
                prove_held_directory_empty(source_parent)
                prove_held_directory_empty(destination_parent)

                temporary_name = "report.tmp"
                (destination_path / temporary_name).write_bytes(b"")
                promoted = open_no_follow(
                    destination_path / temporary_name,
                    _existing_alias(TH),
                    expect_directory=False,
                    kind="report-temp",
                    parent=destination_parent,
                    basename=temporary_name,
                )
                initial = query_handle_identity(promoted)
                self.assertEqual(initial.link_count, 1)
                self.assertEqual(
                    query_handle_name(promoted),
                    query_handle_name(destination_parent).rstrip("\\")
                    + "\\"
                    + temporary_name,
                )
                marked = set_delete_disposition(promoted, delete=True)
                self.assertTrue(marked.delete_pending)
                self.assertEqual(marked.link_count, 0)
                payload = b'{"report":1}\n'
                final_identity = write_flush_reread(
                    promoted,
                    payload,
                    maximum=65_536,
                )
                self.assertTrue(initial.same_object(final_identity))
                self.assertEqual(read_handle_capped(promoted, maximum=65_536), payload)
                cleared = set_delete_disposition(promoted, delete=False)
                self.assertFalse(cleared.delete_pending)
                self.assertEqual(cleared.link_count, 1)
                renamed = rename_handle_no_replace(
                    promoted,
                    destination_parent,
                    "report.json",
                )
                self.assertTrue(initial.same_object(renamed))
                self.assertTrue(
                    renamed.same_object(
                        prove_held_membership(
                            promoted,
                            destination_parent,
                            "report.json",
                        )
                    )
                )
                self.assertEqual(
                    tuple(
                        item.name
                        for item in enumerate_held_directory(
                            destination_parent,
                            maximum_entries=32,
                        )
                    ),
                    ("report.json",),
                )

                (destination_path / "collision.json").write_bytes(b"existing")
                (destination_path / "collision.tmp").write_bytes(b"")

                collision = open_no_follow(
                    destination_path / "collision.tmp",
                    _existing_alias(TH),
                    expect_directory=False,
                    kind="collision-temp",
                    parent=destination_parent,
                    basename="collision.tmp",
                )
                set_delete_disposition(collision, delete=True)
                write_flush_reread(collision, b"candidate", maximum=65_536)
                set_delete_disposition(collision, delete=False)
                with self.assertRaises(Win32BoundaryError) as raised:
                    rename_handle_no_replace(
                        collision,
                        destination_parent,
                        "collision.json",
                    )
                self.assertEqual(raised.exception.code, "analysis_destination_exists")
                prove_held_membership(
                    collision,
                    destination_parent,
                    "collision.tmp",
                )
                set_delete_disposition(collision, delete=True)
                collision.close()
                collision = None
                self.assertFalse((destination_path / "collision.tmp").exists())
                self.assertEqual(
                    (destination_path / "collision.json").read_bytes(),
                    b"existing",
                )
            finally:
                if collision is not None and not collision.closed:
                    try:
                        set_delete_disposition(collision, delete=True)
                    finally:
                        collision.close()
                if promoted is not None and not promoted.closed:
                    promoted.close()
                if not destination_parent.closed:
                    destination_parent.close()
                if not source_parent.closed:
                    source_parent.close()

    def test_s0_relative_replace_preserves_source_identity_and_exact_membership(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = open_no_follow(
                temporary,
                ROOT_ALIAS,
                expect_directory=True,
                kind="analysis-root",
            )
            root_security = ExplicitSecurityDescriptor.root()
            leaf_security = ExplicitSecurityDescriptor.report_temp()
            status = destination = source = cleanup = None
            source_basename = "status.tmp"
            try:
                status_result = open_or_create_status_directory(
                    root,
                    "status",
                    root_security,
                    kind="analysis-status-s0",
                )
                status = status_result.handle
                self.assertTrue(status_result.created)
                status_before = query_handle_identity(status)

                destination = create_relative_file(
                    status,
                    "status.json",
                    TH,
                    leaf_security,
                    kind="status-current",
                )
                write_flush_reread(destination, b"old", maximum=32)
                destination_before = set_delete_disposition(
                    destination,
                    delete=False,
                )
                destination.close()
                destination = None

                source = create_relative_file(
                    status,
                    source_basename,
                    TH,
                    leaf_security,
                    kind="status-temp",
                )
                write_flush_reread(source, b"new", maximum=32)
                source_before = set_delete_disposition(source, delete=False)

                replaced = replace_relative_file(
                    source,
                    status,
                    source_basename,
                    "status.json",
                )
                source_basename = "status.json"
                self.assertTrue(source_before.same_object(replaced))
                self.assertFalse(destination_before.same_object(replaced))
                self.assertEqual(read_handle_capped(source, maximum=32), b"new")
                self.assertTrue(
                    replaced.same_object(
                        prove_held_membership(source, status, "status.json")
                    )
                )
                self.assertTrue(
                    status_before.same_object(query_handle_identity(status))
                )
                self.assertEqual(
                    tuple(
                        (entry.name, entry.file_id)
                        for entry in enumerate_held_directory(
                            status,
                            maximum_entries=1,
                        )
                    ),
                    (("status.json", replaced.file_id),),
                )

                removed = rollback_relative_handle(
                    source,
                    status,
                    source_basename,
                    original=replaced,
                )
                source = None
                self.assertEqual(removed.outcome, "original_absent")
                self.assertFalse(removed.foreign_replaced)
                self.assertEqual(
                    enumerate_held_directory(status, maximum_entries=0),
                    (),
                )
            finally:
                if source is not None and not source.closed:
                    remove_relative_file(source, status, source_basename)
                if destination is not None and not destination.closed:
                    remove_relative_file(destination, status, "status.json")
                if status is not None and not status.closed:
                    status.close()
                cleanup = open_relative_directory(
                    root,
                    "status",
                    ROOT_ALIAS,
                    kind="analysis-status-cleanup",
                )
                remove_relative_directory(cleanup, root, "status")
                cleanup = None
                leaf_security.close()
                root_security.close()
                root.close()

    def test_relative_replace_validation_no_write_and_quarantine_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = open_no_follow(
                temporary,
                ROOT_ALIAS,
                expect_directory=True,
                kind="analysis-root",
            )
            root_security = ExplicitSecurityDescriptor.root()
            leaf_security = ExplicitSecurityDescriptor.report_temp()
            status = destination = source = cleanup = None
            try:
                status = open_or_create_status_directory(
                    root,
                    "status",
                    root_security,
                ).handle
                destination = create_relative_file(
                    status,
                    "status.json",
                    TH,
                    leaf_security,
                    kind="status-current",
                )
                write_flush_reread(destination, b"old", maximum=32)
                destination_before = set_delete_disposition(
                    destination,
                    delete=False,
                )
                destination.close()
                destination = None
                source = create_relative_file(
                    status,
                    "status.tmp",
                    TH,
                    leaf_security,
                    kind="status-temp",
                )
                write_flush_reread(source, b"new", maximum=32)
                source_before = set_delete_disposition(source, delete=False)

                for parent, destination_name, code in (
                    (status, "..\\status.json", "analysis_argument_invalid"),
                    (root, "status.json", "analysis_membership_unproved"),
                ):
                    with (
                        self.subTest(destination=destination_name),
                        patch.object(
                            win32_boundary,
                            "_ntdll",
                            side_effect=AssertionError("native call forbidden"),
                        ),
                        self.assertRaises(Win32BoundaryError) as raised,
                    ):
                        replace_relative_file(
                            source,
                            parent,
                            "status.tmp",
                            destination_name,
                        )
                    self.assertEqual(raised.exception.code, code)

                with (
                    patch.object(
                        win32_boundary,
                        "_snapshot_closed_relative_file_identity",
                        return_value=replace(destination_before, is_reparse=True),
                    ),
                    patch.object(
                        win32_boundary,
                        "_ntdll",
                        side_effect=AssertionError("native call forbidden"),
                    ),
                    self.assertRaises(Win32BoundaryError),
                ):
                    replace_relative_file(
                        source,
                        status,
                        "status.tmp",
                        "status.json",
                    )

                with (
                    patch.object(
                        win32_boundary._ntdll(),
                        "NtSetInformationFile",
                        return_value=0xC0000022,
                    ),
                    self.assertRaises(Win32BoundaryError) as raised,
                ):
                    replace_relative_file(
                        source,
                        status,
                        "status.tmp",
                        "status.json",
                    )
                self.assertEqual(
                    raised.exception.code,
                    "analysis_replace_not_applied",
                )
                self.assertEqual(query_handle_identity(source), source_before)
                prove_held_membership(source, status, "status.tmp")
                observed = open_relative_file(status, "status.json")
                try:
                    self.assertEqual(
                        query_handle_identity(observed),
                        destination_before,
                    )
                    self.assertEqual(
                        read_handle_capped(observed, maximum=32),
                        b"old",
                    )
                finally:
                    observed.close()

                with (
                    patch.object(
                        win32_boundary._ntdll(),
                        "NtSetInformationFile",
                        return_value=win32_boundary.STATUS_PENDING,
                    ),
                    self.assertRaises(Win32QuarantineRequired) as raised,
                ):
                    replace_relative_file(
                        source,
                        status,
                        "status.tmp",
                        "status.json",
                    )
                self.assertEqual(
                    raised.exception.phase,
                    "relative_replace_native_completion_unproved",
                )
                self.assertIs(raised.exception.handle, source)
                self.assertEqual(query_handle_identity(source), source_before)
                prove_held_membership(source, status, "status.tmp")
            finally:
                if source is not None and not source.closed:
                    remove_relative_file(source, status, "status.tmp")
                if destination is not None and not destination.closed:
                    destination.close()
                if status is not None and not status.closed:
                    remaining = open_relative_file(status, "status.json")
                    remove_relative_file(remaining, status, "status.json")
                    status.close()
                cleanup = open_relative_directory(
                    root,
                    "status",
                    ROOT_ALIAS,
                    kind="analysis-status-cleanup",
                )
                remove_relative_directory(cleanup, root, "status")
                cleanup = None
                leaf_security.close()
                root_security.close()
                root.close()

if __name__ == "__main__":
    unittest.main()
