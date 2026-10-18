"""Select the issued protocol with real local stores and native Task basis.

Public host pages and already admitted follow-up bindings are offline fixtures;
these tests do not establish real-host follow-up support or activate hooks.
"""

from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tests.review_handoff_fixture_support import handoff_fixture
from tests import test_review_preparation as preparation_fixtures
from tests.test_review_answer_pure_values import metadata_fixture, TURN1, TURN2
from tests.test_review_continuation import A, B, ident
from task_governance_tool import review_answer_runtime as runtime
from task_governance_tool import review_handoff_scope, setup_feature_config, storage
from task_governance_tool import review_handoff_transport as files
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_collection_repository import CollectionRepository, _encode as encode_record
from task_governance_tool.review_answer_collection import source
from task_governance_tool.review_answer_values import basis_digest
from task_governance_tool.review_handoff_transport import HandoffError
from task_governance_tool.review_preparation_repository import PreparationRepository
from task_governance_tool.review_wait_runtime.continuation_transition_repository import TransitionRepository
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.request_continuation import encode
from task_governance_tool.review_wait_runtime.review_wait_controller import WaitBinding
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from task_governance_tool.review_wait_runtime.review_wait_server import _call


C = "00000000-0000-4000-8000-000000000004"


class RuntimeSelectionTests(unittest.TestCase):
    def ready(self, *, version=1, protocol=None, count=2):
        fixture = self.enterContext(handoff_fixture(schema_version=30, contract=None))
        fixture.ready()
        self.fixture = fixture
        paths = fixture.resolution.paths
        paths.review_answer_collection_store = lambda parent, task, generation, preparation, continuation=None: (
            fixture.root / ("continued-answers.sqlite" if continuation else "initial-answers.sqlite"))
        self.enterContext(patch.object(review_handoff_scope, "resolve", return_value=fixture.resolution))
        from task_governance_tool import review_answer_handoff
        self.admission_connections = []
        def resolved(*_args, retain_read_connection=False, **_kwargs):
            if not retain_read_connection:
                return fixture.resolution
            connection = storage.connect_initialized_readonly(fixture.target)
            self.admission_connections.append(connection)
            return SimpleNamespace(**vars(fixture.resolution), read_connection=connection)
        self.enterContext(patch.object(review_answer_handoff, "resolve", side_effect=resolved))
        self.allowed = self.enterContext(patch.object(setup_feature_config, "answer_collection_allowed", return_value=True))
        preparation = PreparationRepository(fixture.root / "preparation.sqlite")
        self.preparation = preparation
        self.root = preparation.read()[1]
        self.assertEqual(self.root.version, 1)
        if version == 2:
            initial = metadata_fixture()[0]["basis"]
            initial.update(preparation_id=ident("tg_review_preparation_", 2), execution_basis=self.root.basis,
                route=self.root.route, packet_digest="sha256:" + self.root.packet_digest)
            bases = tuple({**initial, "request_digest": "sha256:" + str(index) * 64} for index in range(1, count + 1))
            result_paths = self.root.result_paths if count == 2 else self.root.result_paths + ("reviews/c.json",)
            root = replace(self.root, version=2, preparation_id=initial["preparation_id"], collection_bases=bases,
                result_paths=result_paths)
            with preparation.serial() as lease:
                preparation.publish(lease, root)
            self.root = root
            CollectionRepository(paths.review_answer_collection_store(None, None, None, None)).initialize(bases)
        self.round = None
        if protocol is not None:
            self.allowed.return_value = protocol == "review-answer-v1"
            prepared = fixture.call()
            self.assertTrue(prepared["ok"], prepared)
            self.allowed.return_value = True
            self.round = TransitionRepository(fixture.root / "transition.sqlite").read()[-1]
            self.assertEqual(self.round.answer_protocol, protocol)
        basis = self.root.basis
        self.binding = WaitBinding(wait_id="answer-selection", **{
            name: basis["parent_session_id"] if name == "parent_thread_id" else basis[name]
            for name in WaitBinding.__dataclass_fields__ if name != "wait_id"})
        return fixture

    def service(self, binding=None):
        return runtime.service_for_wait(self.fixture.repo, None, binding or self.binding)

    def snapshot(self):
        return {path.relative_to(self.fixture.repo).as_posix(): path.read_bytes()
            for path in self.fixture.repo.rglob("*") if path.is_file() and ".git" not in path.parts}

    def replace_round(self, **changes):
        changed = replace(self.round, **changes)
        with closing(sqlite3.connect(self.fixture.root / "transition.sqlite")) as connection, connection:
            connection.execute("UPDATE rounds SET payload=? WHERE continuation_id=?",
                (encode(changed), changed.continuation_id))
        self.round = changed

    def public_collector(self, reviewer_ids=(A,), *, summary=None):
        metadata = dict(threadId=self.binding.parent_thread_id, turnId=TURN2)
        host = Mock(_metadata=metadata)
        answer = metadata_fixture()[1].raw
        if summary is not None:
            answer = json.dumps({**json.loads(answer), "summary": summary}).encode()
        def page(_operation, arguments):
            return dict(schemaVersion=1,
            thread=dict(id=arguments["threadId"], kind="codex", hostId="local"),
            page=dict(order="newest_first", limit=2, hasMore=False, nextCursor=None),
            turns=[dict(id=getattr(self, "page_turns", {}).get(arguments["threadId"], TURN1),
                status="completed", error=None, startedAt=1, completedAt=2, durationMs=1,
                items=[dict(type="agentMessage", id="message-1", phase="final_answer", text=answer.decode())])])
        host._json_call.side_effect = page
        worker = Mock(side_effect=AssertionError("collection started a worker"))
        session = ProjectReviewWaitSession(ProjectConfig(self.fixture.repo, self.fixture.repo / "server.mjs",
            self.fixture.repo, "UTC"), basis_factory=lambda *args, **kwargs: lambda: self.binding,
            managed_host_factory=lambda **kwargs: host, session_factory=worker)
        self.enterContext(patch.object(session, "_location", return_value=self.fixture.resolution.paths))
        self.public_session = session
        request = dict(name="review_answer_collect", arguments=dict(task_id=self.binding.task_id,
            reviewer_ids=list(reviewer_ids)), _meta=metadata)
        return lambda: json.loads(_call(request, session)["content"][0]["text"]), host, worker, answer

    def mixed(self, selected=A, *, count=2, separate_stores=False):
        self.ready(version=2, count=count)
        initial = self.bound_service()
        if separate_stores:
            paths = self.fixture.resolution.paths
            initial_path = initial.repository.path
            paths.review_answer_collection_store = lambda parent, task, generation, preparation, continuation=None: (
                self.fixture.root / (continuation + ".sqlite") if continuation else initial_path)
        self.fixture.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=selected,
            action="supplement", prior_receipt_id=None, reason="Continue the selected source")])).encode()
        prepared = self.fixture.call()
        self.assertTrue(prepared["ok"], prepared)
        self.round = TransitionRepository(self.fixture.root / "transition.sqlite").read()[-1]
        return initial, prepared["handoff"]

    def public_hook(self, child, *, reviewer_caller=False):
        _call_parent, host, worker, answer = self.public_collector((child,))
        session = self.public_session
        metadata = dict(threadId=child if reviewer_caller else self.binding.parent_thread_id,
            turnId=TURN1 if reviewer_caller else TURN2)
        host._metadata = metadata
        session.answer_read_host_factory = lambda **kwargs: host
        request = dict(name="review_answer_collect", arguments=dict(event=dict(
            hook_event_name="SubagentStop", session_id=self.binding.parent_thread_id,
            agent_id=child, turn_id=TURN2, stop_hook_active=False)), _meta=metadata)
        return lambda: json.loads(_call(request, session)["content"][0]["text"]), host, worker, answer

    def bind_continued(self, actor):
        service = self.service()
        record = service.repository.read()[0]
        binding = metadata_fixture()[0]["binding"]
        binding.update(basis_digest=basis_digest(record["basis"]), reviewer_key=actor,
            reviewer_session_id=actor, parent_session_id=self.binding.parent_thread_id, initial_turn_id=TURN2)
        self.page_turns = {actor: TURN2}
        service.bind(1, binding)
        return service

    def test_public_parent_resolves_late_initial_sibling_after_restart(self):
        initial, _ = self.mixed()
        call, host, worker, answer = self.public_collector((B,))
        self.assertEqual(call(), dict(ok=True, status="collected", task_id=self.binding.task_id,
            bound=True, collected=[1], unavailable=[]))
        self.assertEqual((self.fixture.repo / initial.original_paths[1]).read_bytes(), answer)
        self.assertEqual(host._json_call.call_count, 1)
        # A separate session reconstructs its exact source from durable state.
        restarted, new_host, _, _ = self.public_collector((B,))
        self.assertTrue(restarted()["ok"])
        new_host._json_call.assert_not_called()
        worker.assert_not_called()

    def test_public_hook_resolves_late_initial_sibling_after_restart(self):
        initial, _ = self.mixed()
        call, host, worker, answer = self.public_hook(B, reviewer_caller=True)
        self.assertEqual(call(), {})
        self.assertEqual((self.fixture.repo / initial.original_paths[1]).read_bytes(), answer)
        host._json_call.assert_called_once_with("read_thread", dict(threadId=B, hostId="local", turnLimit=2,
            includeOutputs=False, maxOutputCharsPerItem=256))
        restarted, new_host, _, _ = self.public_hook(B)
        self.assertEqual(restarted(), {})
        new_host._json_call.assert_not_called()
        worker.assert_not_called()

    def test_public_mixed_sources_keep_response_positions_when_native_slots_collide(self):
        initial, _ = self.mixed(B)
        current = self.bind_continued(B)
        call, host, worker, answer = self.public_collector((B, A))
        result = call()
        self.assertEqual(result, dict(ok=True, status="collected", task_id=self.binding.task_id,
            bound=True, collected=[1, 2], unavailable=[]))
        self.assertEqual(current.repository.read()[0]["binding"]["slot"], 1)
        self.assertEqual(initial.repository.read()[0]["binding"]["slot"], 1)
        for path in (current.original_paths[0], initial.original_paths[0]):
            self.assertEqual((self.fixture.repo / path).read_bytes(), answer)
        self.assertEqual(host._json_call.call_count, 2)
        worker.assert_not_called()

    def test_public_mixed_unavailable_response_does_not_alias_source_slots(self):
        initial, _ = self.mixed(B)
        self.bind_continued(B)
        call, host, _worker, _answer = self.public_collector((B, A))
        page = host._json_call.side_effect
        def observed(operation, arguments):
            if arguments["threadId"] == B:
                raise OSError("isolated missing public page")
            return page(operation, arguments)
        host._json_call.side_effect = observed
        result = call()
        self.assertFalse(result["ok"])
        self.assertEqual(result["collected"], [2])
        self.assertEqual([row["slot"] for row in result["unavailable"]], [1])
        self.assertEqual(result["unavailable"][0]["failure"]["class"], "collection_unavailable")
        self.assertTrue((self.fixture.repo / initial.original_paths[0]).is_file())

    def test_public_mixed_sources_reject_reversed_order_and_old_selected_binding(self):
        self.mixed(B)
        before = self.snapshot()
        for reviewers in ((A, B), (B,)):
            with self.subTest(reviewers=reviewers):
                call, host, worker, _ = self.public_collector(reviewers)
                result = call()
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], "binding_mismatch")
                host._json_call.assert_not_called()
                worker.assert_not_called()
                self.assertEqual(self.snapshot(), before)
        hook, host, _, _ = self.public_hook(B, reviewer_caller=True)
        self.assertFalse(hook()["ok"])
        host._json_call.assert_not_called()

    def test_public_noncontiguous_subset_has_request_positions_and_preserves_omitted_sources(self):
        initial, _ = self.mixed(B, count=3)
        self.bind_continued(B)
        before_a = initial.repository.read()[0]
        call, host, _, answer = self.public_collector((B, C))
        self.assertEqual(call()["collected"], [1, 2])
        self.assertEqual(initial.repository.read()[2]["binding"]["slot"], 3)
        self.assertEqual((self.fixture.repo / initial.original_paths[2]).read_bytes(), answer)
        self.assertEqual(initial.repository.read()[0], before_a)
        self.assertFalse((self.fixture.repo / initial.original_paths[0]).exists())
        self.assertEqual(host._json_call.call_count, 2)

    def test_public_partial_unbound_initial_never_infers_an_assignment(self):
        self.ready(version=2)
        before = self.snapshot()
        call, host, _, _ = self.public_collector((B,))
        self.assertEqual(call()["error"], "binding_mismatch")
        host._json_call.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_public_noncontiguous_subset_failure_uses_request_position(self):
        initial, _ = self.mixed(B, count=3)
        self.bind_continued(B)
        call, host, _, _ = self.public_collector((B, C))
        page = host._json_call.side_effect
        def observed(operation, arguments):
            if arguments["threadId"] == C:
                raise OSError("isolated missing subset answer")
            return page(operation, arguments)
        host._json_call.side_effect = observed
        result = call()
        self.assertFalse(result["ok"])
        self.assertEqual(result["collected"], [1])
        self.assertEqual([row["slot"] for row in result["unavailable"]], [2])
        self.assertEqual(initial.repository.read()[2]["binding"]["slot"], 3)
        self.assertFalse((self.fixture.repo / initial.original_paths[0]).exists())
        self.assertFalse((self.fixture.repo / initial.original_paths[2]).exists())

    def test_public_mixed_old_generation_and_replaced_preparation_are_rejected(self):
        for changed in ("generation", "preparation"):
            with self.subTest(changed=changed):
                self.mixed()
                if changed == "generation":
                    self.binding = replace(self.binding, target_generation=self.binding.target_generation + 1)
                else:
                    identity = ident("tg_review_preparation_", 3)
                    replacement = replace(self.root, preparation_id=identity,
                        collection_bases=tuple({**value, "preparation_id": identity} for value in self.root.collection_bases))
                    with self.preparation.serial() as lease:
                        self.preparation.publish(lease, replacement)
                before = self.snapshot()
                call, host, _, _ = self.public_collector((B,))
                self.assertEqual(call()["error"], "binding_mismatch")
                host._json_call.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_public_parent_and_hook_reject_sources_after_native_target_advances(self):
        from tests.verification_receipt_test_support import set_target
        self.mixed()
        generation = set_target(self.fixture.db, self.fixture.repo, self.binding.task_id,
            fingerprint="sha256:" + "f" * 64)
        self.assertGreater(generation, self.binding.target_generation)
        before = self.snapshot()
        for kind in ("parent", "hook"):
            with self.subTest(kind=kind):
                call, host, _, _ = self.public_collector((B,)) if kind == "parent" else self.public_hook(B)
                self.assertEqual(call()["error"], "binding_mismatch")
                host._json_call.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_late_initial_original_reaches_explicit_capture_assembly_and_registration(self):
        from task_governance_tool import review_handoff_continuation as continuation
        from task_governance_tool import review_answer_registration as registration
        from task_governance_tool.review_answer_transport import decode_collected_submission
        initial, _ = self.mixed(separate_stores=True)
        call, _, _, _ = self.public_collector((B,), summary="Late initial source reviewed")
        self.assertTrue(call()["ok"])
        saved = (self.fixture.repo / initial.original_paths[1]).read_bytes()
        # A newly arrived original was not present at the first continuation's
        # capture boundary. Existing explicit continuation captures it, rather
        # than manufacturing a native capture during read-only assembly.
        self.fixture.args.directory = "reviews/continue-2"
        next_round = self.fixture.call()
        self.assertTrue(next_round["ok"], next_round)
        handoff = next_round["handoff"]
        self.round = TransitionRepository(self.fixture.root / "transition.sqlite").read()[-1]
        current = self.bind_continued(A)
        collect, _, _, _ = self.public_collector((A,), summary="Current selected source reviewed")
        self.assertTrue(collect()["ok"])
        task, framed = continuation.assemble_submission(self.fixture.repo, handoff["packet_path"],
            [row["result_path"] for row in handoff["review_requests"]], caller=self.fixture.caller)
        submission = decode_collected_submission(framed)
        self.assertEqual([row.metadata["binding"]["reviewer_session_id"] for row in submission.items], [A, B])
        self.assertEqual([row.metadata["binding"]["slot"] for row in submission.items], [1, 2])
        context = SimpleNamespace(repo=self.fixture.repo, caller=self.fixture.caller,
            args=SimpleNamespace(task_id=task, user_approved_reviewers=()))
        result, warnings = registration.register(context, self.fixture.target, submission)
        self.assertEqual(warnings, [])
        self.assertTrue(result["review_gate"]["satisfied"])
        self.assertEqual(len(result["receipts"]), 2)
        self.assertEqual(initial.repository.read()[1]["registration"]["state"], "registered")
        self.assertEqual(current.repository.read()[0]["registration"]["state"], "registered")
        self.assertEqual((self.fixture.repo / initial.original_paths[1]).read_bytes(), saved)

    def bound_service(self):
        service = self.service()
        for slot, (record, actor) in enumerate(zip(service.repository.read(), (A, B, C)), 1):
            binding = metadata_fixture()[0]["binding"]
            binding.update(basis_digest=basis_digest(record["basis"]), slot=slot, reviewer_key=actor,
                reviewer_session_id=actor, parent_session_id=self.binding.parent_thread_id)
            service.bind(slot, binding)
        return service

    def assert_native_reads_closed(self):
        for connection in self.admission_connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_hook_check_uses_one_full_native_snapshot_and_closes_before_physical_reads(self):
        self.ready(version=2)
        service = self.bound_service()
        original_preparation_read, original_checked = PreparationRepository.read, files.checked_path
        def preparation_read(repository):
            self.assert_native_reads_closed()
            return original_preparation_read(repository)
        def checked(*args, **kwargs):
            self.assert_native_reads_closed()
            return original_checked(*args, **kwargs)
        with patch.object(storage, "validate_current_database", wraps=storage.validate_current_database) as validate, \
                patch.object(PreparationRepository, "read", new=preparation_read), \
                patch.object(files, "checked_path", side_effect=checked), \
                patch.object(files, "read_bounded", wraps=files.read_bounded) as packet_read:
            for _ in range(2):
                service.admit_basis.check_hook(self.root.collection_bases[0], 1,
                    parent_id=self.binding.parent_thread_id)
            self.assertEqual(validate.call_count, 2)
            self.assertEqual(packet_read.call_count, 2)
        self.assertEqual(len(self.admission_connections), 2)
        self.assertIsNot(self.admission_connections[0], self.admission_connections[1])
        self.assert_native_reads_closed()

    def test_hook_check_preserves_full_captured_preparation_paths(self):
        self.ready(version=2)
        service = self.bound_service()
        records, root = self.preparation.read()
        for change in (dict(packet_path="reviews/other-packet.json"),
                       dict(result_paths=("reviews/other-answer.json", root.result_paths[1]))):
            with self.subTest(change=change), \
                    patch.object(PreparationRepository, "read", return_value=(records, replace(root, **change))), \
                    patch.object(files, "read_bounded") as packet_read, \
                    self.assertRaises(AnswerError):
                service.admit_basis.check_hook(self.root.collection_bases[0], 1,
                    parent_id=self.binding.parent_thread_id)
            packet_read.assert_not_called()
            self.assert_native_reads_closed()

    def test_hook_check_refreshes_native_target_and_closes_failed_snapshot(self):
        from tests.verification_receipt_test_support import set_target
        self.ready(version=2)
        service = self.bound_service()
        service.admit_basis.check_hook(self.root.collection_bases[0], 1,
            parent_id=self.binding.parent_thread_id)
        set_target(self.fixture.db, self.fixture.repo, self.binding.task_id, fingerprint="sha256:" + "f" * 64)
        with patch.object(PreparationRepository, "read") as preparation_read, self.assertRaises(AnswerError):
            service.admit_basis.check_hook(self.root.collection_bases[0], 1,
                parent_id=self.binding.parent_thread_id)
        preparation_read.assert_not_called()
        self.assert_native_reads_closed()

    def test_hook_check_rechecks_opt_in_before_native_or_packet_read(self):
        self.ready(version=2)
        service = self.bound_service()
        self.allowed.return_value = False
        with patch.object(files, "read_bounded") as packet_read, self.assertRaises(AnswerError) as caught:
            service.admit_basis.check_hook(self.root.collection_bases[0], 1,
                parent_id=self.binding.parent_thread_id)
        self.assertEqual(caught.exception.failure["reason"], "transport_unavailable")
        self.assertEqual(self.admission_connections, [])
        packet_read.assert_not_called()

    def test_hook_check_retains_global_validation_of_unselected_event_rows(self):
        from tests.verification_receipt_test_support import add_task
        self.ready(version=2)
        service = self.bound_service()
        unrelated = add_task(self.fixture.db, self.fixture.repo, title="Unselected fixture", status="ready")
        with closing(storage.connect(self.fixture.db)) as connection, connection:
            trigger = connection.execute("SELECT sql FROM sqlite_schema WHERE name='trg_task_events_note_body_immutable'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_task_events_note_body_immutable")
            # Preserve schema and corrupt only an unrelated event's typed
            # content: a creation event cannot carry a note body.
            connection.execute("UPDATE task_events SET note_body='Invalid isolated event note' WHERE task_id=?",
                (unrelated["task_id"],))
            connection.execute(trigger)
        before = self.snapshot()
        with patch.object(PreparationRepository, "read") as preparation_read, \
                patch.object(files, "read_bounded") as packet_read, self.assertRaises(storage.StorageError):
            service.admit_basis.check_hook(self.root.collection_bases[0], 1,
                parent_id=self.binding.parent_thread_id)
        preparation_read.assert_not_called()
        packet_read.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_reviewer_hook_rechecks_native_selection_after_host_observation(self):
        self.ready(version=2)
        service = self.bound_service()
        call, host, _, _ = self.public_hook(A, reviewer_caller=True)
        observed = host._json_call.side_effect
        before = service.repository.read()
        def selected(operation, arguments):
            result = observed(operation, arguments)
            preparation_fixtures.PreparationPublicationTests.pending(self.fixture, A)
            return result
        host._json_call.side_effect = selected
        self.assertEqual(call()["error"], "binding_mismatch")
        self.assertEqual(service.repository.read(), before)
        self.assertFalse((self.fixture.repo / service.original_paths[0]).exists())
        host._json_call.assert_called_once()
        self.assert_native_reads_closed()

    def test_reviewer_hook_rechecks_ownership_after_host_observation(self):
        from tests.verification_receipt_test_support import run_taskgov, payload
        from task_governance_tool.session_identity import CallerIdentity
        self.ready(version=2)
        service = self.bound_service()
        call, host, _, _ = self.public_hook(A, reviewer_caller=True)
        observed = host._json_call.side_effect
        before = service.repository.read()
        def transferred(operation, arguments):
            result = observed(operation, arguments)
            self.assert_native_reads_closed()
            with patch("task_governance_tool.cli.capture_caller_identity", return_value=CallerIdentity(B)):
                for status, extra in (("paused", ("--pause-reason", "Isolated ownership transfer")),
                                      ("in_progress", ())):
                    changed = payload(run_taskgov("task", "edit", self.binding.task_id,
                        "--repo", str(self.fixture.repo), "--db", str(self.fixture.db),
                        "--status", status, *extra, "--json"))
                    self.assertTrue(changed["ok"], changed)
            self.assertEqual(changed["data"]["task"]["ownership"]["owner_session_id"], B)
            return result
        host._json_call.side_effect = transferred
        self.assertEqual(call()["error"], "binding_mismatch")
        self.assertEqual(service.repository.read(), before)
        self.assertFalse((self.fixture.repo / service.original_paths[0]).exists())
        host._json_call.assert_called_once()
        self.assert_native_reads_closed()

    def test_reviewer_hook_rechecks_preparation_after_host_observation(self):
        self.ready(version=2)
        service = self.bound_service()
        call, host, _, _ = self.public_hook(A, reviewer_caller=True)
        observed = host._json_call.side_effect
        before = service.repository.read()
        def replaced(operation, arguments):
            result = observed(operation, arguments)
            self.assert_native_reads_closed()
            identity = ident("tg_review_preparation_", 3)
            replacement = replace(self.root, preparation_id=identity,
                collection_bases=tuple({**basis, "preparation_id": identity} for basis in self.root.collection_bases))
            with self.preparation.serial() as lease:
                self.preparation.publish(lease, replacement)
            return result
        host._json_call.side_effect = replaced
        self.assertEqual(call()["error"], "binding_mismatch")
        self.assertEqual(service.repository.read(), before)
        self.assertFalse((self.fixture.repo / service.original_paths[0]).exists())
        host._json_call.assert_called_once()
        self.assert_native_reads_closed()

    def test_reviewer_hook_rechecks_canonical_collection_path_after_host_observation(self):
        self.ready(version=2)
        service = self.bound_service()
        call, host, _, _ = self.public_hook(A, reviewer_caller=True)
        observed = host._json_call.side_effect
        before = service.repository.read()
        alternate = self.fixture.root / "other-collection.sqlite"
        def relocated(operation, arguments):
            result = observed(operation, arguments)
            self.fixture.resolution.paths.review_answer_collection_store = lambda *_: alternate
            return result
        host._json_call.side_effect = relocated
        self.assertEqual(call()["error"], "binding_mismatch")
        self.assertEqual(service.repository.read(), before)
        self.assertFalse((self.fixture.repo / service.original_paths[0]).exists())
        self.assertFalse(alternate.exists())
        host._json_call.assert_called_once()
        self.assert_native_reads_closed()

    def test_reviewer_hook_packet_drift_after_observation_prevents_publication(self):
        self.ready(version=2)
        service = self.bound_service()
        call, host, _, _ = self.public_hook(A, reviewer_caller=True)
        observed = host._json_call.side_effect
        before = service.repository.read()
        def changed(operation, arguments):
            result = observed(operation, arguments)
            packet = self.fixture.repo / self.root.packet_path
            packet.write_bytes(packet.read_bytes() + b" ")
            return result
        host._json_call.side_effect = changed
        self.assertEqual(call()["error"], "binding_mismatch")
        self.assertEqual(service.repository.read(), before)
        self.assertFalse((self.fixture.repo / service.original_paths[0]).exists())
        self.assert_native_reads_closed()

    def test_reviewer_hook_drift_after_creation_preserves_original_and_unknown(self):
        from tests.verification_receipt_test_support import set_target
        self.ready(version=2)
        service = self.bound_service()
        call, host, _, answer = self.public_hook(A, reviewer_caller=True)
        write = files.write_exclusive
        def created(path, raw):
            self.assert_native_reads_closed()
            write(path, raw)
            set_target(self.fixture.db, self.fixture.repo, self.binding.task_id, fingerprint="sha256:" + "f" * 64)
        with patch.object(files, "write_exclusive", side_effect=created) as publish:
            result = call()
        self.assertEqual(result["error"], "save_unknown")
        self.assertEqual((self.fixture.repo / service.original_paths[0]).read_bytes(), answer)
        self.assertEqual(service.repository.read()[0]["attempts"][0]["publication"]["state"], "unknown")
        publish.assert_called_once()
        host._json_call.assert_called_once()
        self.assert_native_reads_closed()

    def observed(self, actor=A):
        metadata, answer = metadata_fixture()
        return dict(source_identity={**metadata["source_identity"], "reviewer_session_id": actor},
            answer_bytes=answer.raw, complete=True, terminal="completed",
            answer_source=source("answer_observation"), terminal_source=source("terminal_observation"))

    def assert_stale_recovery(self, service, change):
        before = service.repository.read()
        host = Mock()
        def observe(**_kwargs):
            change()
            return self.observed()
        host.observe_answer.side_effect = observe
        result = runtime.collect_bound(service, host, [A])
        self.assertEqual(result, dict(collected=[], unavailable=[dict(slot=1,
            failure={"class": "binding_mismatch", "field": None, "reason": "identity"})]))
        host.observe_answer.assert_called_once()
        self.assertEqual(service.repository.read(), before)
        self.assertFalse((self.fixture.repo / service.original_paths[0]).exists())

    def test_native_selected_successor_during_host_read_rejects_before_publication(self):
        self.ready(version=2)
        service = self.bound_service()
        self.assert_stale_recovery(service,
            lambda: preparation_fixtures.PreparationPublicationTests.pending(self.fixture, A))
        self.assertFalse((self.fixture.root / "transition.sqlite").exists())

    def test_prepared_selected_successor_during_host_read_rejects_before_publication(self):
        self.ready(version=2)
        service = self.bound_service()
        def advance():
            result = self.fixture.call()
            self.assertTrue(result["ok"], result)
        self.assert_stale_recovery(service, advance)

    def test_native_successor_invalidates_collected_continuation_before_transition_advances(self):
        from task_governance_tool import review_continuation as values, review_continuation_repository as records
        self.ready(protocol="review-answer-v1")
        service = self.bound_service()
        transition = self.round
        def advance():
            with closing(storage.connect_initialized(self.fixture.target)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                prior = records.read_continuations(connection, project_id=self.root.basis["project_id"],
                    task_id=self.root.basis["task_id"])[-1]
                records.admit_locked(connection, basis=self.root.basis, preparation_id=self.root.preparation_id,
                    route=self.root.route, selected=prior.selected, caller=self.fixture.caller,
                    retirement=values.RetirementProof(prior.continuation_id, values.basis_digest(self.root.basis), None, None))
        self.assert_stale_recovery(service, advance)
        self.assertEqual(TransitionRepository(self.fixture.root / "transition.sqlite").read()[-1], transition)

    def test_preparation_replacement_during_host_read_rejects_before_publication(self):
        self.ready(version=2)
        service = self.bound_service()
        def advance():
            identity = ident("tg_review_preparation_", 3)
            replacement = replace(self.root, preparation_id=identity,
                collection_bases=tuple({**value, "preparation_id": identity} for value in self.root.collection_bases))
            with self.preparation.serial() as lease:
                self.preparation.publish(lease, replacement)
        self.assert_stale_recovery(service, advance)

    def test_transition_basis_digest_change_during_host_read_rejects_before_publication(self):
        self.ready(protocol="review-answer-v1")
        service = self.bound_service()
        self.assert_stale_recovery(service, lambda: self.replace_round(basis_digest="f" * 64))

    def test_unselected_initial_reviewer_can_finish_after_native_selection(self):
        self.ready(version=2)
        service = self.bound_service()
        preparation_fixtures.PreparationPublicationTests.pending(self.fixture, A)
        host = Mock()
        host.observe_answer.return_value = self.observed(B)
        self.assertEqual(runtime.collect_bound(service, host, [B]), dict(collected=[2], unavailable=[]))
        self.assertEqual((self.fixture.repo / service.original_paths[1]).read_bytes(), metadata_fixture()[1].raw)
        host.observe_answer.assert_called_once()

    def test_retained_and_historical_reads_preserve_saved_sources(self):
        from task_governance_tool.review_answer_handoff import service_for
        self.ready(version=2)
        service = self.bound_service()
        service.observe(1, self.observed())
        saved = service.readback(1)
        preparation_fixtures.PreparationPublicationTests.pending(self.fixture, A)
        with self.assertRaises(AnswerError):
            service.readback(1)
        retained = service_for(self.fixture.repo, self.fixture.resolution, self.root,
            self.root.collection_bases[0], retained=True)
        self.assertEqual(retained.readback(1), saved)
        historical = service_for(self.fixture.repo, self.fixture.resolution, self.root,
            self.root.collection_bases[0], current=False)
        with patch("task_governance_tool.review_answer_handoff.connect_initialized_readonly",
                   side_effect=AssertionError("historical read consulted current Task")):
            self.assertEqual(historical.readback(1), saved)

    def test_legacy_root_collected_continuation_reuses_binding_through_public_collection(self):
        self.ready(protocol="review-answer-v1")
        before = self.snapshot()
        service = self.service()
        self.assertIsNotNone(service)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(service.original_paths, self.round.result_paths)
        record = service.repository.read()[0]
        binding = metadata_fixture()[0]["binding"]
        binding.update(basis_digest=basis_digest(record["basis"]), parent_session_id=self.binding.parent_thread_id)
        # Independent, already admitted follow-up binding; no host inference.
        service.bind(1, binding)
        core_before, preparation_before = self.fixture.db.read_bytes(), self.preparation.path.read_bytes()
        transition_before = (self.fixture.root / "transition.sqlite").read_bytes()
        call, host, worker, answer = self.public_collector()
        self.assertEqual(call(), dict(ok=True, status="collected", task_id=self.binding.task_id,
            bound=True, collected=[1], unavailable=[]))
        host._json_call.assert_called_once_with("read_thread", dict(threadId=A, hostId="local", turnLimit=2,
            includeOutputs=False, maxOutputCharsPerItem=256))
        self.assertEqual((self.fixture.repo / service.original_paths[0]).read_bytes(), answer)
        saved = service.repository.read()[0]
        self.assertEqual(saved["binding"], binding)
        self.assertEqual(saved["attempts"][0]["publication"]["state"], "saved")
        self.assertEqual(saved["registration"]["state"], "not_started")
        after = self.snapshot()
        self.assertTrue(call()["ok"])
        self.assertEqual(self.snapshot(), after)
        self.assertEqual(host._json_call.call_count, 1)
        worker.assert_not_called()
        self.assertEqual(self.fixture.db.read_bytes(), core_before)
        self.assertEqual(self.preparation.path.read_bytes(), preparation_before)
        self.assertEqual((self.fixture.root / "transition.sqlite").read_bytes(), transition_before)

    def test_unbound_continuation_does_not_invent_followup_turn(self):
        self.ready(protocol="review-answer-v1")
        before = self.snapshot()
        call, host, worker, _ = self.public_collector()
        result = call()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "binding_mismatch")
        host._json_call.assert_not_called()
        worker.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_unissued_basis_keeps_binding_failure_through_public_and_collector_boundaries(self):
        self.ready(version=2)
        service = self.service()
        for slot, (record, reviewer) in enumerate(zip(service.repository.read(), (A, B)), 1):
            binding = metadata_fixture()[0]["binding"]
            binding.update(basis_digest=basis_digest(record["basis"]), slot=slot, reviewer_key=reviewer,
                reviewer_session_id=reviewer, parent_session_id=self.binding.parent_thread_id)
            service.bind(slot, binding)
        # Reseal only mutable operational state; the issued root stays unchanged.
        changed = service.repository.read()[0]
        changed["basis"]["request_digest"] = "sha256:" + "f" * 64
        changed["binding"]["basis_digest"] = basis_digest(changed["basis"])
        with closing(sqlite3.connect(service.repository.path)) as connection, connection:
            connection.execute("UPDATE slots SET record_json=? WHERE slot=1", (encode_record(changed),))
        before = self.snapshot()
        failure = {"class": "binding_mismatch", "field": None, "reason": "identity"}
        call, host, worker, _ = self.public_collector((A, B))
        result = call()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "binding_mismatch")
        self.assertEqual(result["failure"], failure)
        host._json_call.assert_not_called()
        worker.assert_not_called()
        collector_host = Mock()
        self.assertEqual(runtime.collect_bound(service, collector_host, [A]),
            dict(collected=[], unavailable=[dict(slot=1, failure=failure)]))
        self.assertEqual(collector_host.mock_calls, [])
        self.assertEqual(self.preparation.read()[1], self.root)
        self.assertEqual(self.snapshot(), before)

    def test_initial_legacy_and_collected_roots_keep_their_issued_protocol(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.ready(version=version)
                before = self.snapshot()
                service = self.service()
                self.assertEqual(service is None, version == 1)
                if service is not None:
                    self.assertEqual(service.original_paths, self.root.result_paths)
                    service.admit_basis(self.root.collection_bases[0], 1)
                self.assertEqual(self.snapshot(), before)

    def test_current_legacy_round_never_falls_back_to_collected_initial_root(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.ready(version=version, protocol="legacy")
                before = self.snapshot()
                with patch.object(CollectionRepository, "read", side_effect=AssertionError("legacy round read collection")):
                    self.assertIsNone(self.service())
                self.assertEqual(self.snapshot(), before)

    def test_collected_round_missing_pins_store_or_valid_bytes_stays_closed(self):
        for version in (1, 2):
            for failure in ("pins", "missing", "corrupt", "mismatched"):
                with self.subTest(version=version, failure=failure):
                    self.ready(version=version, protocol="review-answer-v1")
                    store = self.fixture.root / "continued-answers.sqlite"
                    if failure == "pins":
                        self.replace_round(collection_basis_digests=())
                    elif failure == "missing":
                        store.rename(store.with_name("retained-answers.sqlite"))
                    elif failure == "corrupt":
                        store.write_bytes(b"invalid isolated collection fixture")
                    else:
                        self.replace_round(collection_basis_digests=("sha256:" + "f" * 64,))
                    before = self.snapshot()
                    with self.assertRaises((HandoffError, RepositoryError)):
                        self.service()
                    self.assertEqual(self.snapshot(), before)

    def test_identity_mismatch_rejects_before_legacy_or_collected_selection(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.ready(version=version)
                before = self.snapshot()
                for change in (dict(parent_thread_id=B), dict(task_id=ident("tg_task_", 9)),
                               dict(target_generation=self.binding.target_generation + 1)):
                    with self.subTest(change=change), self.assertRaises(AnswerError):
                        self.service(replace(self.binding, **change))
                self.assertEqual(self.snapshot(), before)

    def test_unready_or_other_preparation_round_cannot_select_a_service(self):
        for change in ("settled", "preparation"):
            with self.subTest(change=change):
                self.ready(protocol="review-answer-v1")
                self.replace_round(**(dict(phase="settled") if change == "settled" else
                    dict(preparation_id=ident("tg_review_preparation_", 9))))
                before = self.snapshot()
                with self.assertRaises((AnswerError, RepositoryError)):
                    self.service()
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
