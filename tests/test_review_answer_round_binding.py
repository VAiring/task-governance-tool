"""Immutable issued answer-basis pins in bounded continuation round journals."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.test_review_wait_continuation_transition import pending
from task_governance_tool.review_wait_runtime.continuation_transition_repository import TransitionRepository
from task_governance_tool.review_wait_runtime.request_continuation import decode, encode
from task_governance_tool.review_wait_runtime.request_repository import RequestRepository
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError


PINS = ("sha256:" + "a" * 64,)


def collected(*, count=1, phase="settled"):
    return replace(pending(), answer_protocol="review-answer-v1", phase=phase,
        packet_digest=None if phase == "fenced" else "c" * 64,
        result_paths=tuple(f"reviews/round-1/answer-{slot}.json" for slot in range(1, count + 1)))


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class AnswerRoundValueTests(unittest.TestCase):
    def test_legacy_and_old_tagged_bytes_remain_exact_without_inferred_pins(self):
        for record in (pending(), collected(phase="prepared"), collected(phase="published")):
            old = asdict(record)
            del old["collection_basis_digests"]
            if record.answer_protocol == "legacy":
                del old["answer_protocol"]
            raw = canonical(old)
            with self.subTest(protocol=record.answer_protocol, phase=record.phase):
                self.assertEqual(encode(record), raw)
                self.assertEqual(encode(decode(raw)), raw)
                self.assertEqual(decode(raw).collection_basis_digests, ())

    def test_pins_are_closed_ordered_and_bound_to_collected_packet_slots(self):
        record = collected(count=2)
        pins = (PINS[0], "sha256:" + "b" * 64)
        pinned = replace(record, collection_basis_digests=pins)
        self.assertEqual(decode(encode(pinned)), pinned)
        self.assertNotEqual(encode(pinned), encode(replace(pinned, collection_basis_digests=tuple(reversed(pins)))))
        for value in ([], [*pins], pins[:1], (*pins, PINS[0]), (pins[0], "a" * 64),
                      (pins[0], "sha256:" + "A" * 64), (pins[0], None)):
            with self.subTest(value=value), self.assertRaises(RepositoryError):
                replace(record, collection_basis_digests=value)
        for changes in (dict(answer_protocol="legacy"), dict(phase="fenced", packet_digest=None),
                        dict(packet_digest=None)):
            with self.subTest(changes=changes), self.assertRaises(RepositoryError):
                replace(pinned, **changes)
        for value in ([], None, "sha256:" + "a" * 64):
            raw = json.loads(encode(collected()))
            raw["collection_basis_digests"] = value
            with self.subTest(encoded=value), self.assertRaises(RepositoryError):
                decode(canonical(raw))
        raw = json.loads(encode(pinned))
        raw["collection_basis_digest"] = raw.pop("collection_basis_digests")
        with self.assertRaises(RepositoryError):
            decode(canonical(raw))

    def test_eight_complete_basis_digests_stay_bounded_and_allow_equal_slots(self):
        record = replace(collected(count=8), collection_basis_digests=PINS * 8)
        raw = encode(record)
        self.assertLess(len(raw.encode("utf-8")), 32768)
        self.assertEqual(decode(raw).collection_basis_digests, PINS * 8)


class AnswerRoundTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-answer-round-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repository = TransitionRepository(self.root / "transition.sqlite")
        self.initial = collected(phase="fenced")
        with self.repository.serial(create=True) as lease:
            fenced = self.repository.fence(lease, self.initial)
            self.settled = self.repository.advance(lease, fenced, phase="settled")

    def bind_packet(self):
        with self.repository.serial() as lease:
            return self.repository.bind_packet(lease, self.settled, "c" * 64)

    def test_pin_freeze_is_once_only_and_exact_lost_ack_reentry_is_idempotent(self):
        intended = self.bind_packet()
        with self.repository.serial() as lease:
            pinned = self.repository.bind_collection_bases(lease, intended, PINS)
            self.assertEqual(self.repository.bind_collection_bases(lease, intended, PINS), pinned)
            self.assertEqual(self.repository.bind_collection_bases(lease, pinned, PINS), pinned)
            with self.assertRaises(RepositoryError):
                self.repository.bind_collection_bases(lease, pinned, ("sha256:" + "b" * 64,))
            with self.assertRaises(RepositoryError):
                self.repository.bind_collection_bases(lease, intended, ("sha256:" + "b" * 64,))
        before = self.repository.path.read_bytes()
        self.assertEqual(TransitionRepository(self.repository.path).read(), (pinned,))
        self.assertEqual(self.repository.path.read_bytes(), before)

    def test_pins_survive_phase_changes_request_copy_and_initial_fence_reentry(self):
        intended = self.bind_packet()
        with self.repository.serial() as lease:
            pinned = self.repository.bind_collection_bases(lease, intended, PINS)
            prepared = self.repository.advance(lease, pinned, phase="prepared", packet_path=pinned.packet_path,
                packet_digest=pinned.packet_digest, result_paths=pinned.result_paths)
            self.assertEqual(self.repository.bind_collection_bases(lease, prepared, PINS), prepared)
            published = self.repository.advance(lease, prepared, phase="published")
            self.assertEqual(self.repository.fence(lease, self.initial), published)
            self.assertEqual(self.repository.bind_collection_bases(lease, published, PINS), published)
        request = RequestRepository(self.root / "request.sqlite")
        with request.serial(create=True) as lease:
            request.install_round(lease, prepared)
            copied = request.publish_without_wait(lease, prepared)
        self.assertEqual(copied, published)
        self.assertEqual(RequestRepository(request.path).read_rounds(), (published,))
        self.assertEqual(TransitionRepository(self.repository.path).read(), (published,))

    def test_freeze_requires_collected_settled_packet_and_complete_tuple(self):
        with self.repository.serial() as lease:
            with self.assertRaises(RepositoryError):
                self.repository.bind_collection_bases(lease, self.settled, PINS)
        intended = self.bind_packet()
        before = self.repository.read()
        with self.repository.serial() as lease:
            for record, pins in ((self.initial, PINS), (replace(intended, answer_protocol="legacy"), PINS),
                                 (intended, ()), (intended, list(PINS)), (intended, PINS * 2)):
                with self.subTest(record=record, pins=pins), self.assertRaises(RepositoryError):
                    self.repository.bind_collection_bases(lease, record, pins)
        self.assertEqual(self.repository.read(), before)

    def test_old_tagged_prepared_round_cannot_gain_inferred_pins(self):
        intended = self.bind_packet()
        with self.repository.serial() as lease:
            prepared = self.repository.advance(lease, intended, phase="prepared", packet_path=intended.packet_path,
                packet_digest=intended.packet_digest, result_paths=intended.result_paths)
            with self.assertRaises(RepositoryError):
                self.repository.bind_collection_bases(lease, prepared, PINS)
        self.assertEqual(self.repository.read(), (prepared,))

    def test_failed_freeze_transaction_does_not_publish_pins(self):
        intended = self.bind_packet()
        load = self.repository._load

        def reject_pinned(connection):
            records = load(connection)
            if records[-1].collection_basis_digests:
                raise RepositoryError("state_unreadable")
            return records

        with self.repository.serial() as lease:
            with mock.patch.object(self.repository, "_load", side_effect=reject_pinned):
                with self.assertRaises(RepositoryError):
                    self.repository.bind_collection_bases(lease, intended, PINS)
        self.assertEqual(self.repository.read(), (intended,))


if __name__ == "__main__":
    unittest.main()
