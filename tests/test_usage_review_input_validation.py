"""Fresh usage reads reject drift in real direct and legacy handoff bindings."""
from contextlib import closing, contextmanager
import sqlite3
import unittest

from tests import test_review_session_install as install_support
from tests.test_review_session_repository import REVIEWER
from task_governance_tool import storage
from task_governance_tool import usage_evidence_service as service
from task_governance_tool.review_session_repository import read_bindings
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.task_ownership import validate_storage_rows
from task_governance_tool.usage_attribution_repository import registered_participants
from task_governance_tool.usage_values import UsageError


class UsageReviewInputValidationTests(unittest.TestCase):
    setUp = install_support.ReviewSessionInstallTests.setUp
    invoke = install_support.ReviewSessionInstallTests.invoke
    cli = install_support.ReviewSessionInstallTests.cli
    helper = install_support.ReviewSessionInstallTests.helper
    original = install_support.ReviewSessionInstallTests.original
    save = install_support.ReviewSessionInstallTests.save

    @contextmanager
    def reader(self):
        # Match the real lifecycle/publication admission, not the global audit
        # which would hide a missing relationship check in the usage consumer.
        with closing(storage.connect_initialized_readonly(self.target, operation_local=True)) as core:
            validate_storage_rows(core)
            yield core

    @contextmanager
    def changed_reference(self, change):
        # Only this disposable physical install is damaged. Restore immutable
        # triggers before any consumer opens it, then restore its original bytes.
        original = self.install.db_path.read_bytes()
        try:
            with closing(sqlite3.connect(self.install.db_path)) as core, core:
                triggers = core.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                                        "AND tbl_name='evidence_references'").fetchall()
                for name, _ in triggers:
                    core.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
                core.execute(change + " WHERE source_kind='review_receipt' AND source_id=?", (self.receipt_id,))
                for _, sql in triggers:
                    core.execute(sql)
            yield
        finally:
            self.install.db_path.write_bytes(original)

    def check_fresh_reads(self, registration):
        if registration == "direct":
            result = self.cli("review", "result", "add", self.task,
                              raw=self.original("direct"), caller=REVIEWER)
        else:
            output = self.save("handoff", REVIEWER)
            result = self.helper("submit", "--packet", self.packet_path, output)
        self.receipt_id = result["data"]["receipts"][0]["receipt"]["review_receipt_id"]
        resolution = resolve_project_state(skill_root=self.install.skill_root, repo=self.root)
        self.assertIsNotNone(resolution.target, resolution)
        self.target = resolution.target
        repository = service.repository_for(self.target)
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
        last_good = self.target.resolved_usage_index.read_bytes()
        index, state = repository.prepare_publication(self.reader)
        while state["position"] < state["total"]:
            state = repository.advance_publication(state)
        with self.reader() as core:
            self.assertIn(self.receipt_id, read_bindings(core))
            self.assertEqual(repository.read(core, task_id=self.task)["status"], "pending")
        with repository.publication_guard(self.reader, index, state):
            pass

        for change in (
            "UPDATE evidence_references SET target_value='sha256:" + "f" * 64 + "'",
            "UPDATE evidence_references SET target_generation=target_generation+1",
            "DELETE FROM evidence_references",
        ):
            with self.subTest(registration=registration, change=change), self.changed_reference(change):
                numerical_before = repository.path.read_bytes()
                with self.reader() as core:
                    # Existing participant and attribution paths already reject
                    # this drift; standalone selected reads must do so too.
                    for operation in (
                        lambda: read_bindings(core),
                        lambda: registered_participants(core, self.target.project.project_id),
                    ):
                        with self.assertRaises(storage.StorageError):
                            operation()
                    with self.assertRaises(UsageError):
                        repository.attribution(core)
                    for audit in (False, True):
                        with self.assertRaises(UsageError):
                            repository.read(core, task_id=self.task, audit=audit)
                    self.assertEqual(service.read_task_usage(self.target, core, self.task)["status"], "unknown")
                with self.assertRaises(UsageError):
                    with repository.publication_guard(self.reader, index, state):
                        self.fail("corrupt reviewer relation reached publication")
                self.assertEqual(repository.path.read_bytes(), numerical_before)
                self.assertEqual(service.refresh_usage(self.target)["status"], "unknown")
                self.assertEqual(self.target.resolved_usage_index.read_bytes(), last_good)

        # No poisoned repository cache survives restoration or a new read.
        with repository.publication_guard(self.reader, index, state):
            pass
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")

    def test_direct_binding_is_revalidated_after_capture(self):
        self.check_fresh_reads("direct")

    def test_legacy_handoff_binding_is_revalidated_after_capture(self):
        self.check_fresh_reads("handoff")
