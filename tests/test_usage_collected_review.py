"""Native collected Receipts bound reviewer usage without a fabricated save."""

from contextlib import closing, contextmanager
import json
import unittest
from unittest import mock

from tests.review_answer_fixture_support import review_fixture
from tests.test_review_answer_pure_values import TURN1
from tests.test_usage_attribution import turn
from tests.test_usage_collection import event, usage
from tests.verification_receipt_test_support import add_receipt, completion
from task_governance_tool import storage
from task_governance_tool.review_answer_transport import (
    CollectedItem, decode_collected_submission, frame_collected_submission,
)
from task_governance_tool.review_collected_results import add_collected_review_results
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_collection import register_source
from task_governance_tool.usage_publication_repository import UsagePublicationRepository


class CollectedReviewUsageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(review_fixture(schema_version=31))
        self.root = self.fixture.repo
        self.logs = self.root / "usage-fixture-logs"
        self.logs.mkdir()
        self.repository = self.new_repository("usage")

    def new_repository(self, name):
        basis = self.fixture.basis
        repository = UsagePublicationRepository(self.root / (name + ".sqlite"),
            basis["project_id"], basis["project_path_hash"], basis["project_binding_generation"])
        repository.initialize()
        return repository

    def reader(self):
        return closing(storage.connect_initialized_readonly(self.fixture.target))

    def items(self, slots=(1,)):
        return tuple(CollectedItem(slot, answer.raw, metadata, answer)
            for slot in slots for metadata, answer in (self.fixture.metadata(slot),))

    def register(self, items):
        submitted = decode_collected_submission(frame_collected_submission(
            items[0].metadata["basis"]["preparation_id"], items))
        with closing(storage.connect_initialized(self.fixture.target)) as core, core:
            return add_collected_review_results(core, self.fixture.target.project,
                self.fixture.task_id, submitted, caller=self.fixture.caller,
                database_target=self.fixture.target)

    def source(self, session, *, name=None):
        source = SourceInput(session, self.logs / ((name or session) + ".jsonl"), self.logs, self.root)
        return source

    def read_display(self):
        return {**self.fixture.packet, "context_check": "matched_at_read"}

    def records(self, session, *, prefix="review", read=True, endpoint=TURN1):
        result = [event("session_meta", id=session, cwd=str(self.root),
            model_provider="openai", cli_version="0.162.0-alpha.17.2")]
        for index, identity in enumerate((turn(1), turn(2), endpoint, turn(4)), 1):
            result.extend((event("event_msg", type="task_started", turn_id=identity, started_at=index),
                event("turn_context", turn_id=identity, model="fixture-model", effort="high")))
            if index == 1 and read:
                result.append(event("response_item", type="custom_tool_call_output", call_id="read",
                    internal_chat_message_metadata_passthrough={"turn_id": identity},
                    output=json.dumps(self.read_display())))
            result.append(usage(prefix + str(index), thread=session, turn=identity))
        return result

    def write(self, source, records):
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in records))

    def ingest(self, source, *, repository=None, max_records=4096):
        repository = repository or self.repository
        register_source(repository, source, CallerIdentity(source.thread_id))
        for _ in range(100):
            if repository.cursor(source.source_id, source.thread_id).offset == source.path.stat().st_size:
                return
            repository.collect_source(source, max_records=max_records)
        self.fail("finite synthetic source did not finish within 100 batches")

    def read(self, repository=None, *, audit=False):
        with self.reader() as core:
            return (repository or self.repository).read(core, task_id=None if audit else self.fixture.task_id,
                audit=audit)

    def keys(self, repository=None):
        repository = repository or self.repository
        index = self.read(repository, audit=True)
        with repository.connection() as connection:
            return {key for identity in index["current_snapshot_ids"]
                for key in repository.members(connection, identity)}

    def history(self):
        with self.repository.connection() as connection:
            return dict(connection.execute("SELECT snapshot_id,document FROM usage_snapshots"))

    def observed_refresh(self, operation=None):
        """Count feature validation after unchanged core admission has finished."""
        from task_governance_tool import review_answer_repository, review_session_repository

        observations = []
        @contextmanager
        def reader():
            with self.reader() as core:
                with mock.patch.object(review_answer_repository, "validate_provenance_relation",
                        wraps=review_answer_repository.validate_provenance_relation) as provenance, \
                        mock.patch.object(review_session_repository, "read_bindings",
                        wraps=review_session_repository.read_bindings) as bindings, \
                        mock.patch.object(review_session_repository, "read_binding_targets",
                        wraps=review_session_repository.read_binding_targets) as targets:
                    yield core
                    observations.append((provenance.call_count, bindings.call_count + targets.call_count))
        (operation or self.repository.refresh)(reader)
        return observations

    def test_capture_shares_review_inputs_without_changing_projection_or_history(self):
        from task_governance_tool.usage_evidence_repository import UsageEvidenceRepository

        items = self.items((1, 2))
        self.register(items)
        for index, item in enumerate(items):
            source = self.source(item.session_binding.session_id)
            self.write(source, self.records(source.thread_id, prefix=f"slot{index}-"))
            self.ingest(source)
        # The previous unshared pipeline remains an independent baseline for
        # capture bytes, membership, diagnostics and immutable successors.
        previous_pipeline = lambda core, connection: UsageEvidenceRepository._capture_projection(
            self.repository, core, connection)
        with mock.patch.object(self.repository, "_capture_projection", side_effect=previous_pipeline):
            self.assertEqual(self.observed_refresh(), [(4, 3)])
        before, index_before, task_before = self.history(), self.read(audit=True), self.read()
        with self.repository.connection() as connection:
            members_before = list(map(tuple, connection.execute("SELECT * FROM usage_snapshot_members")))
        self.assertEqual(self.observed_refresh(), [(2, 1)])
        self.assertEqual(self.history(), before)
        self.assertEqual(self.read(audit=True), index_before)
        self.assertEqual(self.read(), task_before)
        with self.repository.connection() as connection:
            self.assertEqual(list(map(tuple, connection.execute("SELECT * FROM usage_snapshot_members"))), members_before)
        # Reusing the repository object does not reuse the preceding read's
        # verification, even when every core row is unchanged.
        self.assertEqual(self.observed_refresh(), [(2, 1)])

    def test_next_capture_reloads_registered_review_inputs(self):
        items = self.items((1, 2))
        self.register(items[:1])
        for index, item in enumerate(items):
            source = self.source(item.session_binding.session_id)
            self.write(source, self.records(source.thread_id, prefix=f"slot{index}-"))
            self.ingest(source)
        self.assertEqual(self.observed_refresh(), [(1, 1)])
        self.assertEqual(self.read()["total"]["response_count"], 3)
        before = self.history()
        self.register(items[1:])
        self.assertEqual(self.observed_refresh(), [(2, 1)])
        self.assertEqual(self.read()["total"]["response_count"], 6)
        self.assertEqual({key: self.history()[key] for key in before}, before)

    def test_publication_guard_and_standalone_read_revalidate_inputs(self):
        from task_governance_tool import review_answer_repository

        self.register(self.items())
        captured = []
        def prepare(reader):
            captured.append(self.repository.prepare_publication(reader))
        self.assertEqual(self.observed_refresh(prepare), [(1, 1)])
        index, state = captured[0]
        while state["position"] < state["total"]:
            state = self.repository.advance_publication(state)
        # A later guard has its own admitted core transaction. It cannot use
        # capture's earlier verification, nor can a standalone read.
        def guard(reader):
            with self.repository.publication_guard(reader, index, state):
                pass
        self.assertEqual(self.observed_refresh(guard), [(1, 1)])
        with self.reader() as core:
            with mock.patch.object(review_answer_repository, "validate_provenance_relation",
                    wraps=review_answer_repository.validate_provenance_relation) as provenance:
                self.repository.read(core, task_id=self.fixture.task_id)
                self.assertEqual(provenance.call_count, 1)

    def test_native_endpoint_includes_middle_work_and_excludes_later_and_parent_forward(self):
        item, = self.items()
        registered = self.register((item,))
        session = item.session_binding.session_id
        source = self.source(session)
        self.write(source, self.records(session))
        self.ingest(source)
        parent = self.source(self.fixture.caller.session_id)
        forwarded = self.records(parent.thread_id, prefix="parent")
        forwarded.append(event("response_item", type="custom_tool_call_output", call_id="forward",
            internal_chat_message_metadata_passthrough={"turn_id": turn(4)}, output=json.dumps({
                "ok": True, "command": "review.result.add", "project_id": self.fixture.basis["project_id"],
                "data": registered})))
        self.write(parent, forwarded)
        self.ingest(parent)
        core_before = self.fixture.db.read_bytes()
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), {("openai", "review" + str(index)) for index in (1, 2, 3)})
        observed = self.read()
        self.assertEqual(observed["total"]["models"][0]["total_tokens"], 360)
        self.assertEqual(observed["periods"][0]["work_units"][0]["exclusive"]["response_count"], 3)
        self.assertNotEqual(observed["periods"][0]["quality"], "complete")
        self.assertEqual(self.fixture.db.read_bytes(), core_before)

    def test_registration_refresh_replaces_unknown_snapshot_without_rewriting_history(self):
        item, = self.items()
        session = item.session_binding.session_id
        source = self.source(session)
        self.write(source, self.records(session))
        self.ingest(source)
        self.repository.refresh(self.reader)
        before, old_index = self.history(), self.read(audit=True)
        self.assertEqual(self.read()["total"]["response_count"], 0)
        self.register((item,))
        # A core change cannot make a previously captured zero current by read.
        from task_governance_tool.usage_values import UsageError
        with self.assertRaises(UsageError):
            self.read()
        self.repository.refresh(self.reader)
        self.assertEqual(self.read()["total"]["response_count"], 3)
        after = self.history()
        self.assertEqual({key: after[key] for key in before}, before)
        index = self.read(audit=True)
        self.assertTrue(set(old_index["current_snapshot_ids"]).isdisjoint(index["current_snapshot_ids"]))
        successors = [value for value in index["snapshots"] if value["snapshot_id"] in index["current_snapshot_ids"]]
        self.assertTrue(any(set(value["predecessors"]).intersection(before) for value in successors))

    def test_endpoint_projection_version_alone_invalidates_capture_without_mutating_core_or_history(self):
        from unittest import mock
        from task_governance_tool import usage_response_repository as policy
        from task_governance_tool.usage_values import UsageError
        item, = self.items()
        self.register((item,))
        source = self.source(item.session_binding.session_id)
        self.write(source, self.records(source.thread_id))
        self.ingest(source)
        core_before = self.fixture.db.read_bytes()
        self.assertNotEqual(policy._COLLECTED_ENDPOINT_BASIS, "collected-review-endpoints-v3")
        # Hold native Receipt, source, counters and geometry fixed. Only the
        # projection version changes, independently of a registration event.
        with mock.patch.object(policy, "_COLLECTED_ENDPOINT_BASIS", "collected-review-endpoints-v3"):
            self.repository.refresh(self.reader)
            original = self.history()
            prior = self.read()
        numerical_before = self.repository.path.read_bytes()
        with self.assertRaises(UsageError):
            self.read()
        self.assertEqual(self.repository.path.read_bytes(), numerical_before)
        self.repository.refresh(self.reader)
        self.assertEqual(self.read()["total"], prior["total"])
        self.assertEqual(self.keys(), {("openai", "review" + str(index)) for index in (1, 2, 3)})
        self.assertEqual({key: self.history()[key] for key in original}, original)
        self.assertEqual(self.fixture.db.read_bytes(), core_before)

    def test_late_final_numbers_full_and_sliced_collection_match_after_restart(self):
        item, = self.items()
        self.register((item,))
        source = self.source(item.session_binding.session_id)
        records = self.records(source.thread_id)
        final = next(row for row in records if row["type"] == "token_usage_record"
            and row["payload"]["response_id"] == "review3")
        self.write(source, [row for row in records if row is not final])
        self.ingest(source, max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), {("openai", "review1"), ("openai", "review2")})
        before = self.history()
        with source.path.open("ab") as stream:
            stream.write(json.dumps(final).encode() + b"\n")
        self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)
        self.ingest(source, max_records=1)
        self.repository.refresh(self.reader)
        full = self.new_repository("full")
        self.ingest(source, repository=full)
        full.refresh(self.reader)
        self.assertEqual(self.keys(), self.keys(full))
        for field in ("total", "coverage", "attribution"):
            self.assertEqual(self.read()[field], self.read(full)[field])
        self.assertEqual(self.read()["periods"][0]["gaps"], self.read(full)["periods"][0]["gaps"])
        self.assertEqual(self.read()["total"]["response_count"], 3)
        self.assertEqual({key: self.history()[key] for key in before}, before)

    def test_missing_read_counts_only_known_final_turn_with_partial_coverage(self):
        item, = self.items()
        self.register((item,))
        source = self.source(item.session_binding.session_id)
        self.write(source, self.records(source.thread_id, read=False))
        self.ingest(source)
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), {("openai", "review3")})
        period = self.read()["periods"][0]
        self.assertIn("review_boundary_unknown", period["gaps"])
        # Missing interval coverage does not make this known final turn's
        # execution assignment ambiguous. The range remains incomplete.
        self.assertEqual(period["quality"], "incomplete")
        self.assertEqual(period["work_units"][0]["exclusive"]["response_count"], 1)

    def test_missing_endpoint_order_preserves_known_read_until_late_order_restores_range(self):
        item, = self.items()
        self.register((item,))
        source = self.source(item.session_binding.session_id)
        records = self.records(source.thread_id)
        endpoint_order = next(row for row in records if row["type"] == "event_msg"
            and row["payload"].get("turn_id") == TURN1)
        self.write(source, [row for row in records if row is not endpoint_order])
        self.ingest(source, max_records=1)
        core_before = self.fixture.db.read_bytes()
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), {("openai", "review1")})
        period = self.read()["periods"][0]
        self.assertEqual(period["quality"], "incomplete")
        self.assertIn("boundary_unknown", period["gaps"])
        self.assertIn("review_boundary_unknown", period["gaps"])
        original = self.history()
        # The missing real ordering record arrives later; no inferred timestamp
        # or synthetic save is needed to recover the original native range.
        with source.path.open("ab") as stream:
            stream.write(json.dumps(endpoint_order).encode() + b"\n")
        self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)
        self.ingest(source, max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), {("openai", "review" + str(index)) for index in (1, 2, 3)})
        self.assertEqual({key: self.history()[key] for key in original}, original)
        self.assertEqual(self.fixture.db.read_bytes(), core_before)

    def test_conflicting_endpoint_order_retains_only_known_read_after_restart(self):
        item, = self.items()
        self.register((item,))
        source = self.source(item.session_binding.session_id)
        records = self.records(source.thread_id)
        records.append(event("event_msg", type="task_started", turn_id=TURN1, started_at=99))
        self.write(source, records)
        self.ingest(source, max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), {("openai", "review1")})
        period = self.read()["periods"][0]
        self.assertEqual(period["quality"], "incomplete")
        self.assertIn("boundary_unknown", period["gaps"])
        self.assertIn("review_boundary_unknown", period["gaps"])
        original = self.history()
        self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), {("openai", "review1")})
        self.assertEqual(self.history(), original)

    def test_unordered_endpoint_without_read_never_assigns_observed_final_numbers(self):
        item, = self.items()
        self.register((item,))
        for order in ("missing", "conflicting"):
            with self.subTest(order=order):
                # Each scenario owns its numerical store so the conflicting
                # order in one source cannot contaminate the other scenario.
                self.repository = self.new_repository("no-read-" + order)
                source = self.source(item.session_binding.session_id, name=order)
                records = self.records(source.thread_id, read=False)
                if order == "missing":
                    records = [row for row in records if not (row["type"] == "event_msg"
                        and row["payload"].get("turn_id") == TURN1)]
                else:
                    records.append(event("event_msg", type="task_started", turn_id=TURN1, started_at=99))
                self.write(source, records)
                self.ingest(source, max_records=1)
                self.repository.refresh(self.reader)
                self.assertEqual(self.keys(), set())
                observed = self.read()
                self.assertEqual(observed["total"], {"response_count": 0, "models": []})
                period = observed["periods"][0]
                self.assertEqual(period["quality"], "incomplete")
                self.assertIn("review_boundary_unknown", period["gaps"])
                self.assertIn("boundary_unknown", period["gaps"])

    def test_unregistered_retained_capture_does_not_create_participation(self):
        predecessor, metadata, _ = self.fixture.collected_source_continuation()
        self.fixture.retain_collected_source(predecessor, metadata)
        source = self.source(metadata["source_identity"]["reviewer_session_id"])
        self.write(source, self.records(source.thread_id))
        self.ingest(source)
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), set())
        self.assertEqual(self.read()["total"], {"response_count": 0, "models": []})

    def test_distinct_turns_with_same_endpoint_order_never_expand_native_range(self):
        item, = self.items()
        self.register((item,))
        for has_read in (True, False):
            with self.subTest(read=has_read):
                name = "endpoint-tie-" + str(has_read)
                source = self.source(item.session_binding.session_id, name=name)
                records = self.records(source.thread_id, read=has_read)
                for row in records:
                    if row["type"] == "event_msg" and row["payload"].get("turn_id") == turn(4):
                        row["payload"]["started_at"] = 3
                self.write(source, records)
                expected = {("openai", "review1")} if has_read else set()
                projections = []
                for mode in ("full", "sliced", "resumed"):
                    self.repository = self.new_repository(name + "-" + mode)
                    if mode == "resumed":
                        register_source(self.repository, source, CallerIdentity(source.thread_id))
                        self.repository.collect_source(source, max_records=7)
                        self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)
                    self.ingest(source, max_records=4096 if mode == "full" else 1)
                    core_before = self.fixture.db.read_bytes()
                    self.repository.refresh(self.reader)
                    self.assertEqual(self.keys(), expected)
                    observed = self.read()
                    period = observed["periods"][0]
                    self.assertEqual(period["quality"], "incomplete")
                    self.assertIn("boundary_unknown", period["gaps"])
                    self.assertIn("review_boundary_unknown", period["gaps"])
                    self.assertEqual(observed["total"]["response_count"], len(expected))
                    if has_read:
                        self.assertEqual(observed["total"]["models"][0]["total_tokens"], 120)
                    else:
                        self.assertEqual(observed["total"]["models"], [])
                    projections.append((observed["total"], observed["attribution"], period["gaps"]))
                    self.assertEqual(self.fixture.db.read_bytes(), core_before)
                self.assertEqual(projections[0], projections[1])
                self.assertEqual(projections[0], projections[2])

    def test_colliding_read_order_retains_only_safe_native_endpoint(self):
        item, = self.items()
        self.register((item,))
        source = self.source(item.session_binding.session_id)
        records = self.records(source.thread_id)
        for row in records:
            if row["type"] == "event_msg" and row["payload"].get("turn_id") == turn(2):
                row["payload"]["started_at"] = 1
        self.write(source, records)
        self.ingest(source, max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), {("openai", "review3")})
        period = self.read()["periods"][0]
        self.assertEqual(period["quality"], "incomplete")
        self.assertIn("review_boundary_unknown", period["gaps"])

    def test_persisted_peer_conflicts_close_native_range_in_all_collection_modes(self):
        item, = self.items()
        self.register((item,))
        # Both arrival orders matter: the store keeps the first start only.
        # Even a retained disagreement outside the apparent range cannot prove
        # that discarded alternate starts did not collide with a boundary.
        by_order = {}
        for first, later in ((4, 3), (3, 4), (4, 1), (1, 4), (4, 99), (99, 4), (4, 4)):
            projections = []
            for mode in ("full", "sliced", "resumed"):
                with self.subTest(first=first, later=later, mode=mode):
                    name = f"peer-{first}-{later}-{mode}"
                    self.repository = self.new_repository(name)
                    source = self.source(item.session_binding.session_id, name=name)
                    records = self.records(source.thread_id)
                    for row in records:
                        if row["type"] == "event_msg" and row["payload"].get("turn_id") == turn(4):
                            row["payload"]["started_at"] = first
                    records.append(event("event_msg", type="task_started", turn_id=turn(4), started_at=later))
                    self.write(source, records)
                    if mode == "resumed":
                        register_source(self.repository, source, CallerIdentity(source.thread_id))
                        self.repository.collect_source(source, max_records=len(records) - 1)
                        self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)
                    self.ingest(source, max_records=4096 if mode == "full" else 1)
                    core_before = self.fixture.db.read_bytes()
                    self.repository.refresh(self.reader)
                    expected = {("openai", "review1")} if first != later else {
                        ("openai", "review" + str(index)) for index in (1, 2, 3)}
                    self.assertEqual(self.keys(), expected)
                    observed = self.read()
                    period = observed["periods"][0]
                    self.assertEqual(observed["total"]["models"][0]["total_tokens"], 120 * len(expected))
                    if first != later:
                        self.assertEqual(period["quality"], "incomplete")
                        self.assertIn("boundary_unknown", period["gaps"])
                        self.assertIn("review_boundary_unknown", period["gaps"])
                    else:
                        self.assertNotIn("review_boundary_unknown", period["gaps"])
                    projections.append((observed["total"], observed["attribution"], period["gaps"]))
                    self.assertEqual(self.fixture.db.read_bytes(), core_before)
            self.assertEqual(projections[0], projections[1])
            self.assertEqual(projections[0], projections[2])
            by_order[first, later] = projections[0]
        for first, later in ((4, 3), (4, 1), (4, 99)):
            self.assertEqual(by_order[first, later], by_order[later, first])

    def test_peer_conflict_without_read_has_no_native_endpoint_only_fallback(self):
        item, = self.items()
        self.register((item,))
        source = self.source(item.session_binding.session_id)
        records = self.records(source.thread_id, read=False)
        records.append(event("event_msg", type="task_started", turn_id=turn(4), started_at=3))
        self.write(source, records)
        self.ingest(source, max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), set())
        self.assertEqual(self.read()["total"], {"response_count": 0, "models": []})
        self.assertIn("review_boundary_unknown", self.read()["periods"][0]["gaps"])

    def test_own_conflicted_read_is_excluded_but_second_definite_read_survives(self):
        item, = self.items()
        self.register((item,))
        for second_read in (False, True):
            with self.subTest(second_read=second_read):
                name = "own-read-conflict-" + str(second_read)
                self.repository = self.new_repository(name)
                source = self.source(item.session_binding.session_id, name=name)
                records = self.records(source.thread_id)
                records.append(event("event_msg", type="task_started", turn_id=turn(1), started_at=99))
                if second_read:
                    records.append(event("response_item", type="custom_tool_call_output", call_id="read-again",
                        internal_chat_message_metadata_passthrough={"turn_id": turn(2)},
                        output=json.dumps(self.read_display())))
                self.write(source, records)
                self.ingest(source, max_records=1)
                self.repository.refresh(self.reader)
                self.assertEqual(self.keys(), {("openai", "review2")} if second_read else set())
                self.assertIn("review_boundary_unknown", self.read()["periods"][0]["gaps"])

    def test_peer_conflict_from_another_segment_does_not_taint_another_session(self):
        items = self.items((1, 2))
        self.register(items)
        projections = []
        for peer_first in (False, True):
            with self.subTest(peer_first=peer_first):
                self.repository = self.new_repository("segments-" + str(peer_first))
                peer = self.source(items[0].session_binding.session_id, name="conflicting-segment")
                self.write(peer, [self.records(peer.thread_id)[0],
                    event("event_msg", type="task_started", turn_id=turn(4), started_at=1)])
                if peer_first:
                    self.ingest(peer, max_records=1)
                for index, item in enumerate(items, 1):
                    source = self.source(item.session_binding.session_id)
                    self.write(source, self.records(source.thread_id, prefix=f"session{index}-"))
                    self.ingest(source, max_records=1)
                self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)
                if not peer_first:
                    self.ingest(peer, max_records=1)
                core_before = self.fixture.db.read_bytes()
                self.repository.refresh(self.reader)
                self.assertEqual(self.keys(), {("openai", "session1-1")} |
                    {("openai", "session2-" + str(index)) for index in (1, 2, 3)})
                self.assertEqual(self.read()["total"]["models"][0]["total_tokens"], 480)
                self.assertEqual(self.fixture.db.read_bytes(), core_before)
                observed = self.read()
                projections.append((observed["total"], observed["attribution"], observed["periods"][0]["gaps"]))
        self.assertEqual(projections[0], projections[1])

    def test_nonconflicted_read_tie_remains_excluded_with_another_turn_conflict(self):
        item, = self.items()
        self.register((item,))
        source = self.source(item.session_binding.session_id)
        records = self.records(source.thread_id)
        for row in records:
            if row["type"] == "event_msg" and row["payload"].get("turn_id") == turn(2):
                row["payload"]["started_at"] = 1
        records.append(event("event_msg", type="task_started", turn_id=turn(4), started_at=99))
        self.write(source, records)
        self.ingest(source, max_records=1)
        self.repository.refresh(self.reader)
        # Neither ambiguous read ordering nor an otherwise known endpoint can
        # restore a range after the same-session conflict fences that range.
        self.assertEqual(self.keys(), set())
        self.assertIn("review_boundary_unknown", self.read()["periods"][0]["gaps"])

    def test_same_order_in_distinct_reviewer_sessions_is_not_a_collision(self):
        items = self.items((1, 2))
        self.register(items)
        for index, item in enumerate(items, 1):
            source = self.source(item.session_binding.session_id)
            self.write(source, self.records(source.thread_id, prefix=f"session{index}-"))
            self.ingest(source, max_records=1)
        self.repository.refresh(self.reader)
        self.assertEqual(self.keys(), {("openai", f"session{session}-{index}")
            for session in (1, 2) for index in (1, 2, 3)})
        observed = self.read()
        self.assertEqual(observed["total"]["models"][0]["total_tokens"], 720)
        self.assertNotIn("boundary_unknown", observed["periods"][0]["gaps"])
        self.assertNotIn("review_boundary_unknown", observed["periods"][0]["gaps"])

    def test_late_order_conflicts_invalidate_capture_and_preserve_history(self):
        from task_governance_tool.usage_values import UsageError
        item, = self.items()
        self.register((item,))
        for peer, later in ((5, 3), (4, 3), (4, 1), (4, 99)):
            with self.subTest(peer=peer, later=later):
                name = f"late-collision-{peer}-{later}"
                self.repository = self.new_repository(name)
                source = self.source(item.session_binding.session_id, name=name)
                self.write(source, self.records(source.thread_id))
                self.ingest(source)
                self.repository.refresh(self.reader)
                self.assertEqual(self.keys(), {("openai", "review" + str(index)) for index in (1, 2, 3)})
                original, core_before = self.history(), self.fixture.db.read_bytes()
                with self.repository.connection() as connection:
                    counters = tuple(map(tuple, connection.execute("SELECT * FROM usage_responses ORDER BY response_id")))
                with source.path.open("ab") as stream:
                    stream.write(json.dumps(event("event_msg", type="task_started",
                        turn_id=turn(peer), started_at=later)).encode() + b"\n")
                self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)
                self.ingest(source, max_records=1)
                numerical_before = self.repository.path.read_bytes()
                with self.assertRaises(UsageError):
                    self.read()
                self.assertEqual(self.repository.path.read_bytes(), numerical_before)
                self.repository.refresh(self.reader)
                self.assertEqual(self.keys(), {("openai", "review1")})
                self.assertEqual({key: self.history()[key] for key in original}, original)
                self.assertEqual(self.fixture.db.read_bytes(), core_before)
                with self.repository.connection() as connection:
                    self.assertEqual(tuple(map(tuple, connection.execute(
                        "SELECT * FROM usage_responses ORDER BY response_id"))), counters)
                full = self.new_repository(name + "-full")
                self.ingest(source, repository=full)
                full.refresh(self.reader)
                self.assertEqual(self.keys(), self.keys(full))
                for field in ("total", "attribution"):
                    self.assertEqual(self.read()[field], self.read(full)[field])
                self.assertEqual(self.read()["periods"][0]["gaps"], self.read(full)["periods"][0]["gaps"])

    def test_registered_retained_source_counts_original_turn_once_through_continuations(self):
        self.fixture.register_collected_source()
        for slot in (1, 2):
            item, = self.items((slot,))
            source = self.source(item.session_binding.session_id)
            self.write(source, self.records(source.thread_id, prefix=f"slot{slot}-"))
            self.ingest(source)
        self.repository.refresh(self.reader)
        expected = {("openai", f"slot{slot}-{index}") for slot in (1, 2) for index in (1, 2, 3)}
        self.assertEqual(self.keys(), expected)
        before = self.history()
        self.repository.refresh(self.reader)
        self.assertEqual(self.history(), before)
        self.assertEqual(self.keys(), expected)

    def test_late_usage_after_actual_completion_preserves_original_cycle_and_snapshot_links(self):
        items = self.items((1, 2))
        self.register(items)
        source = self.source(items[0].session_binding.session_id)
        self.write(source, self.records(source.thread_id))
        self.ingest(source)
        verified = add_receipt(self.fixture.db, self.root, self.fixture.task_id,
            self.fixture.basis["target_generation"])
        self.assertEqual(verified.returncode, 0, verified.stdout)
        completed = completion(self.fixture.db, self.root, self.fixture.task_id)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.repository.refresh(self.reader)
        initial = self.read()
        cycle = initial["periods"][0]["completion_cycle_id"]
        self.assertIsNotNone(cycle)
        before = self.history()
        with self.repository.connection() as connection:
            links = set(map(tuple, connection.execute("SELECT * FROM usage_cycle_links")))
        core_before = self.fixture.db.read_bytes()
        with source.path.open("ab") as stream:
            stream.write(json.dumps(usage("late-final", thread=source.thread_id, turn=TURN1)).encode() + b"\n")
        self.ingest(source, max_records=1)
        self.repository.refresh(self.reader)
        observed = self.read()
        self.assertEqual(observed["periods"][0]["completion_cycle_id"], cycle)
        self.assertEqual(observed["total"]["response_count"], 4)
        self.assertEqual({key: self.history()[key] for key in before}, before)
        with self.repository.connection() as connection:
            after_links = set(map(tuple, connection.execute("SELECT * FROM usage_cycle_links")))
        self.assertTrue(links < after_links)
        self.assertEqual({row[1] for row in after_links}, {cycle})
        self.assertEqual(self.fixture.db.read_bytes(), core_before)

    def test_native_reader_rejects_source_mismatch_or_missing_session_without_repairs(self):
        from task_governance_tool.review_answer_repository import read_collected_review_endpoints
        from task_governance_tool.usage_values import UsageError
        item, = self.items()
        registered = self.register((item,))
        identity = registered["receipts"][0]["receipt"]["review_receipt_id"]
        self.repository.refresh(self.reader)
        numerical_before = self.repository.path.read_bytes()
        cases = (("review_collection_sources", "turn_id", turn(9)),
            ("review_collection_sources", "original_digest", "sha256:" + "f" * 64),
            ("review_receipts", "target_generation", 999),
            ("review_receipt_sessions", None, None))
        for table, field, replacement in cases:
            with self.subTest(table=table, field=field), closing(storage.connect_initialized(self.fixture.target)) as core:
                core.execute("BEGIN")
                try:
                    # Simulate corrupt restored rows only inside the disposable
                    # database transaction; original triggers/rows roll back.
                    triggers = core.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                        (table,)).fetchall()
                    for trigger in triggers:
                        core.execute('DROP TRIGGER "' + trigger[0].replace('"', '""') + '"')
                    if field is None:
                        core.execute("DELETE FROM " + table + " WHERE review_receipt_id=?", (identity,))
                    else:
                        core.execute("UPDATE " + table + " SET " + field + "=? WHERE review_receipt_id=?",
                            (replacement, identity))
                    core.execute("PRAGMA query_only=ON")
                    with self.assertRaises(storage.StorageError):
                        read_collected_review_endpoints(core, receipt_ids={identity})
                    # A previously successful capture cannot exempt a later
                    # read from source/binding/target validation. This bypasses
                    # global admission only to exercise the feature boundary.
                    with self.assertRaises(UsageError) as failure:
                        with self.repository.connection(write=True) as connection:
                            self.repository._refresh(core, connection)
                    self.assertEqual(failure.exception.code, "usage_unavailable")
                    self.assertEqual(self.repository.path.read_bytes(), numerical_before)
                finally:
                    core.execute("PRAGMA query_only=OFF")
                    core.rollback()
        with self.reader() as core:
            endpoint, = read_collected_review_endpoints(core, receipt_ids={identity})
            self.assertEqual(endpoint.turn_id, TURN1)
            self.assertEqual(endpoint.binding, item.session_binding)


if __name__ == "__main__":
    unittest.main()
