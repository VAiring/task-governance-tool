"""One fixed effect root, immutable old observations and bounded actual rounds."""

from contextlib import closing
from dataclasses import replace
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_review_continuation import basis, A, B, ident
from task_governance_tool.review_finalization_repository import FinalizationRecord, FinalizationRepository, _encode
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from task_governance_tool.review_wait_runtime.review_wait_basis import _FIELDS


def root_record(**changes):
    full = basis()
    source = {**full, "parent_thread_id": full["parent_session_id"], "task_status": "in_progress"}
    native = {key: source[key] for key in _FIELDS}
    return FinalizationRecord(native,"reviews/old/packet.json","a"*64,("reviews/old/a.json","reviews/old/b.json"),"refs/heads/codex/test",**changes)


def round_record(sequence=1):
    root = root_record()
    return FinalizationRecord(root.basis,f"reviews/round-{sequence}/packet.json","b"*64,(f"reviews/round-{sequence}/a.json",),root.branch,
        continuation_id=ident("tg_review_continuation_",sequence), continuation_sequence=sequence,
        preparation_id=ident("tg_review_preparation_"),active_sessions=(A,))


class FinalizationContinuationRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-finalization-rounds-")
        self.addCleanup(temporary.cleanup)
        self.repo = FinalizationRepository(Path(temporary.name).resolve()/"finalization.sqlite")

    def initialize(self, record=None):
        with self.repo.serial(initial=record or root_record()):
            pass

    def append(self, record=None):
        with self.repo.serial() as lease:
            return self.repo.append_round(lease,record or round_record())

    def registered(self):
        with self.repo.serial() as lease:
            current = self.repo.advance(lease,registration="dispatching",originals=((A,"c"*64),),
                original_locations=(("reviews/round-1/packet.json","reviews/round-1/a.json"),),
                retained_receipt_ids=(ident("tg_review_receipt_",2),))
            return self.repo.advance(lease,registration="succeeded",receipt_ids=(ident("tg_review_receipt_"),))

    def test_explicit_conversion_preserves_exact_v1_payload_and_original_round(self):
        original = root_record(reviewer_observations=((A,"old-turn","failed"),),reviewer_blocker="finalization_reviewer_failed")
        self.initialize(original)
        before = self.repo.path.read_bytes()
        self.assertEqual(self.repo.read(),original)
        self.assertEqual(self.repo.path.read_bytes(),before)
        current = self.append()
        self.assertEqual(current.continuation_id,round_record().continuation_id)
        self.assertIsNone(current.reviewer_blocker)
        self.assertEqual(self.repo.read_rounds()[0],original)
        with closing(sqlite3.connect(self.repo.path)) as connection:
            self.assertEqual(connection.execute("SELECT payload FROM rounds WHERE sequence=0").fetchone()[0],_encode(original))

    def test_next_round_does_not_reset_candidate_or_successful_registration_history(self):
        self.initialize()
        self.append()
        self.registered()
        with self.repo.serial() as lease:
            self.repo.advance(lease,candidate="c"*40)
        second = self.append(round_record(2))
        self.assertEqual(second.candidate,"c"*40)
        self.assertEqual(second.registration,"not_started")
        self.assertEqual(self.repo.read_rounds()[1].registration,"succeeded")
        self.assertEqual(self.append(round_record(2)),second)

    def test_unknown_registration_dispatch_does_not_convert(self):
        original = root_record(registration="dispatching",originals=((A,"a"*64),(B,"b"*64)))
        self.initialize(original)
        with self.assertRaises(RepositoryError) as caught:
            self.append()
        self.assertEqual(caught.exception.code,"review_continuation_effect_unknown")
        self.assertEqual(self.repo.read(),original)

    def test_root_commit_and_completion_advance_once_without_rewriting_old_round(self):
        self.initialize()
        self.append()
        self.registered()
        before = self.repo.read_rounds()[0]
        with self.repo.serial() as lease:
            self.repo.advance(lease,candidate="c"*40)
            self.repo.advance(lease,commit="dispatching")
            self.repo.advance(lease,commit="succeeded")
            self.repo.advance(lease,completion="dispatching")
            final = self.repo.advance(lease,completion="succeeded")
        self.assertEqual(final.candidate,"c"*40)
        self.assertEqual(self.repo.read_rounds()[0],before)
        with self.assertRaises(RepositoryError):
            self.append(round_record(2))

    def test_rejected_registration_replacement_preserves_old_dispatch_and_fixed_link(self):
        original = root_record(registration="dispatching", originals=((A,"a"*64),(B,"b"*64)))
        self.initialize(original)
        replacement = replace(round_record(), replaced_registration_round=0)
        self.assertEqual(self.append(replacement), replacement)
        self.assertEqual(self.repo.read_rounds()[0], original)
        self.assertEqual(self.append(replacement), replacement)
        with self.assertRaises(RepositoryError):
            self.append(round_record())  # Reentry cannot drop the retirement link.
        with self.repo.serial() as lease, self.assertRaises(RepositoryError):
            self.repo.advance(lease, replaced_registration_round=None)
        self.assertEqual(self.repo.read_rounds()[0], original)

    def test_replacement_marker_cannot_bypass_unknown_git_effect(self):
        original = root_record(registration="succeeded", originals=((A,"a"*64),(B,"b"*64)),
            receipt_ids=(ident("tg_review_receipt_"),ident("tg_review_receipt_",2)),
            candidate="c"*40, commit="dispatching")
        self.initialize(original)
        with self.assertRaises(RepositoryError):
            self.append(replace(round_record(), replaced_registration_round=0))
        self.assertEqual(self.repo.read(), original)

    def test_conversion_failure_rolls_back_to_exact_readable_v1(self):
        self.initialize()
        from task_governance_tool import review_finalization_continuation_repository as continued
        real = continued._parts
        def fail_after_append(connection):
            value = real(connection)
            if len(value[1])>1:
                raise RepositoryError("state_unreadable")
            return value
        with mock.patch.object(continued,"_parts",side_effect=fail_after_append):
            with self.assertRaises(RepositoryError):
                self.append()
        self.assertEqual(self.repo.read(),root_record())
        with closing(sqlite3.connect(self.repo.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone(),(1,))


if __name__=="__main__":
    unittest.main()
