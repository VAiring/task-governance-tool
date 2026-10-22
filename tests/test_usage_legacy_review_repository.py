"""Real legacy registrations retain conservative usage across capture/restart."""

from contextlib import closing
import json
import unittest
from unittest import mock

from tests import test_review_session_install as install_support
from tests.test_review_session_repository import REVIEWER, SECOND
from tests.test_usage_attribution import turn
from tests.test_usage_collection import event, usage
from task_governance_tool import storage, usage_response_repository as policy
from task_governance_tool.state_resolver import observe_current_root, resolve_project_state
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_collection import register_source
from task_governance_tool.usage_publication_repository import UsagePublicationRepository
from task_governance_tool.usage_values import UsageError


class LegacyReviewRepositoryTests(unittest.TestCase):
    invoke = install_support.ReviewSessionInstallTests.invoke
    cli = install_support.ReviewSessionInstallTests.cli
    helper = install_support.ReviewSessionInstallTests.helper
    original = install_support.ReviewSessionInstallTests.original

    def setUp(self):
        install_support.ReviewSessionInstallTests.setUp(self)
        self.target = resolve_project_state(skill_root=self.install.skill_root, repo=self.root).target
        self.logs = self.root / "fixture-logs"
        self.logs.mkdir()

    def register(self, source):
        read = self.invoke(self.install.skill_root / "scripts/review_handoff.py",
            ["read", "--repo", str(self.root), "--packet", self.packet_path], caller=REVIEWER)
        self.assertEqual(read.returncode, 0, read.stdout or read.stderr)
        self.displayed = json.loads(read.stdout)
        if source == "direct":
            self.terminal = self.cli("review", "result", "add", self.task,
                                    raw=self.original("direct"), caller=REVIEWER)
        else:
            output = self.handoff["review_requests"][0]["result_path"]
            self.terminal = self.helper("save", "--packet", self.packet_path, "--output", output,
                                        caller=REVIEWER, raw=self.original("handoff"))
            self.helper("submit", "--packet", self.packet_path, output)

    def repository(self, name):
        repository = UsagePublicationRepository(self.root / (name + ".sqlite"),
            self.target.project.project_id, observe_current_root(self.root).canonical_path_hash, 1)
        repository.initialize()
        return repository

    def reader(self):
        return closing(storage.connect_initialized_readonly(self.target))

    def records(self, *, first=5000, later=4000, read=True, tie=False):
        session = REVIEWER.session_id
        rows = [event("session_meta", id=session, cwd=str(self.root),
                      model_provider="openai", cli_version="0.162.0-alpha.17.2")]
        for number in range(1, 6):
            rows.extend((event("event_msg", type="task_started", turn_id=turn(number),
                               started_at=first if number == 5 else number * 1000),
                         event("turn_context", turn_id=turn(number), model="fixture-model")))
            if number == 4 or (number == 2 and read):
                rows.append(event("response_item", type="custom_tool_call_output", call_id=f"boundary-{number}",
                    internal_chat_message_metadata_passthrough={"turn_id": turn(number)},
                    output=json.dumps(self.displayed if number == 2 else self.terminal)))
            rows.append(usage(f"r{number}", thread=session, turn=turn(number)))
        if not tie:
            rows.append(event("event_msg", type="task_started", turn_id=turn(5), started_at=later))
        return rows

    def ingest(self, repository, rows, name, mode):
        source = SourceInput(REVIEWER.session_id, self.logs / (name + ".jsonl"), self.logs, self.root)
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        register_source(repository, source, REVIEWER)
        if mode == "resumed":
            repository.collect_source(source, max_records=len(rows) - 1)
            repository = UsagePublicationRepository(repository.path, *repository.basis)
        for _ in range(len(rows) + 1):
            if repository.cursor(source.source_id, source.thread_id).offset == source.path.stat().st_size:
                return repository
            repository.collect_source(source, max_records=4096 if mode == "full" else 1)
        self.fail("finite fixture did not finish")

    def projection(self, repository):
        with self.reader() as core:
            result = repository.attribution(core)
        # Include response membership, classification, diagnostics and totals.
        return result["components"]

    def check_orders(self, source):
        self.register(source)
        core_before = self.install.db_path.read_bytes()
        projections = []
        for first, later in ((5000, 4000), (4000, 5000), (5000, 2000), (2000, 5000), (5000, 9000)):
            for mode in ("full", "sliced", "resumed"):
                name = f"case-{first}-{later}-{mode}"
                with self.subTest(source=source, first=first, later=later, mode=mode):
                    repository = self.ingest(self.repository(name), self.records(first=first, later=later), name, mode)
                    repository.refresh(self.reader)
                    parts = self.projection(repository)
                    self.assertEqual({tuple(key) for part in parts for key in part["response_keys"]}, {("openai", "r2")})
                    self.assertEqual({part["assignment"] for part in parts}, {"partial"})
                    self.assertTrue(all("review_boundary_unknown" in part["diagnostics"] for part in parts))
                    projections.append(parts)
                    # Persisted capture and a new repository instance agree.
                    repository = UsagePublicationRepository(repository.path, *repository.basis)
                    repository.refresh(self.reader)
                    self.assertEqual(parts, self.projection(repository))
        self.assertTrue(all(value == projections[0] for value in projections))
        for read, tie, first in ((False, False, 5000), (True, True, 4000), (False, True, 4000),
                                 (True, True, 5000)):
            name = f"edge-{read}-{tie}-{first}"
            repository = self.ingest(self.repository(name),
                self.records(read=read, tie=tie, first=first), name, "sliced")
            repository.refresh(self.reader)
            parts = self.projection(repository)
            healthy = tie and first == 5000
            expected = {("openai", f"r{n}") for n in (2, 3, 4)} if healthy else {("openai", "r2")} if read else set()
            self.assertEqual({tuple(key) for part in parts for key in part["response_keys"]}, expected)
            self.assertEqual({part["assignment"] for part in parts}, {"exclusive" if healthy else "partial"})
        self.assertEqual(self.install.db_path.read_bytes(), core_before)

    def test_handoff_full_sliced_restart_and_arrival_orders_match(self):
        self.check_orders("handoff")

    def test_direct_full_sliced_restart_and_arrival_orders_match(self):
        self.check_orders("direct")

    def test_policy_refresh_preserves_counters_history_members_and_completion_links(self):
        self.register("handoff")
        self.cli("review", "result", "add", self.task, raw=self.original("second"), caller=SECOND)
        self.cli("task", "complete", self.task, "--verification-complete", "--review-complete", "--commit-not-required")
        repository = self.ingest(self.repository("history"), self.records(), "history", "resumed")
        original = policy.reviewer_intervals
        def previous_policy(*args, **kwargs):
            return original(*args, **{**kwargs, "conservative_legacy": False})
        def saved():
            with repository.connection() as connection:
                return {table: tuple(map(tuple, connection.execute("SELECT * FROM " + table + " ORDER BY 1,2")))
                    for table in ("usage_responses", "usage_snapshots", "usage_snapshot_members", "usage_cycle_links")}
        with mock.patch.object(policy, "_LEGACY_REVIEW_BASIS", "legacy-review-intervals-v0"), \
                mock.patch.object(policy, "reviewer_intervals", side_effect=previous_policy):
            repository.refresh(self.reader)
            old = saved()
            with self.reader() as core:
                previous = repository.read(core, task_id=self.task)
            self.assertEqual(previous["total"]["response_count"], 3)
        self.assertTrue(old["usage_cycle_links"])
        core_before, numerical_before = self.install.db_path.read_bytes(), repository.path.read_bytes()
        with self.reader() as core, self.assertRaises(UsageError):
            repository.read(core, task_id=self.task)
        self.assertEqual(repository.path.read_bytes(), numerical_before)
        repository.refresh(self.reader)
        after = saved()
        self.assertEqual(old["usage_responses"], after["usage_responses"])
        for table in ("usage_snapshots", "usage_snapshot_members", "usage_cycle_links"):
            self.assertTrue(set(old[table]) <= set(after[table]), table)
        with self.reader() as core:
            current = repository.read(core, task_id=self.task)
        self.assertEqual(current["total"]["response_count"], 1)
        self.assertEqual(current["periods"][0]["completion_cycle_id"], previous["periods"][0]["completion_cycle_id"])
        self.assertEqual(self.install.db_path.read_bytes(), core_before)
        self.assertEqual({part["assignment"] for part in self.projection(repository)}, {"partial"})
