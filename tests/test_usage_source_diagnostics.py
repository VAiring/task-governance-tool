"""Source gaps follow admitted work without changing numerical allocation."""

from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
import unittest
from unittest import mock

from tests import test_usage_response_attribution as pure
from tests import test_usage_publication as publication
from tests import test_usage_collected_review as collected
from tests import test_usage_legacy_review_repository as legacy
from tests import test_usage_wait_repository as waiting
from tests import test_usage_wait_attribution as wait_pure
from tests.test_usage_collection import PARENT
from task_governance_tool import usage_response_attribution as policy
from task_governance_tool import usage_response_repository as repository_policy
from task_governance_tool import usage_evidence_service as service
from task_governance_tool.usage_publication_repository import UsagePublicationRepository
from task_governance_tool.usage_values import UsageError


GAPS = {"source_unreadable", "prefix_verification_deferred"}


def numbers(value):
    """Compare every allocation/numerical field while excluding source gaps."""
    value = deepcopy(value)
    for part in value["components"]:
        part["diagnostics"] = sorted(set(part["diagnostics"]) - GAPS)
    return value


class SourceDiagnosticPartitionTests(unittest.TestCase):
    def build(self, actions, *, diagnostics=None, numerical_mode="normal", **options):
        project = policy.project_responses
        def run(*args, **kwargs):
            if numerical_mode == "missing":
                args = (*args[:-1], ())
            elif numerical_mode == "conflicting":
                kwargs["conflicting_responses"] = frozenset((row.provider, row.response_id) for row in args[-1])
            return project(*args, **kwargs, thread_diagnostics=diagnostics or {})
        with mock.patch.object(pure, "project_responses", side_effect=run):
            return pure.ResponsePartitionTests().build(actions, **options)

    def compare(self, actions, **options):
        baseline = self.build(actions, **options)
        result = self.build(actions, diagnostics={pure.THREAD: GAPS}, **options)
        self.assertEqual(numbers(result), numbers(baseline))
        self.assertTrue(result["components"])
        self.assertTrue(all(GAPS <= set(part["diagnostics"]) for part in result["components"]))
        return result

    def test_same_response_end_stop_release_and_review_pending(self):
        for status in ("done", "paused", "blocked", "ready", "cancelled", "review_pending"):
            for null_release in (False, True) if status in {"ready", "cancelled"} else (False,):
                with self.subTest(status=status, null_release=null_release):
                    self.compare(((pure.A, pure.X, "ready", "in_progress", 1),
                        (pure.A, None if null_release else pure.X, "in_progress", status, 1)), count=3)

    def test_preparation_tail_shared_and_stop_resume_keep_exact_keys_and_totals(self):
        scenarios = (
            (((pure.A, pure.X, "ready", "in_progress", 2),
              (pure.A, pure.X, "in_progress", "done", 2)), (0, 1)),
            (((pure.A, pure.X, "ready", "in_progress", 1),
              (pure.A, pure.X, "in_progress", "done", 2),
              (pure.B, pure.Y, "ready", "in_progress", 2),
              (pure.B, pure.Y, "in_progress", "done", 2)), (0,)),
            (((pure.A, pure.X, "ready", "in_progress", 1),
              (pure.A, pure.X, "in_progress", "paused", 2),
              (pure.A, pure.X, "paused", "in_progress", 2)), ()),
            (((pure.A, pure.X, "ready", "in_progress", 1),
              (pure.A, pure.X, "in_progress", "done", 3)), ()),
        )
        for actions, checks in scenarios:
            with self.subTest(actions=actions):
                self.compare(actions, count=5, checks=checks)

    def test_turn_fallback_receives_gaps_only_for_allocated_responses(self):
        self.compare(((pure.A, pure.X, "ready", "in_progress", 1),
                      (pure.A, pure.X, "in_progress", "done", 2)),
                     count=4, missing=(1, 2), fallback_mode="segments")

    def test_same_response_missing_or_conflicting_values_keep_admitted_source_gaps(self):
        for mode in ("missing", "conflicting"):
            for status in ("done", "paused", "blocked", "ready", "cancelled"):
                for null_release in (False, True) if status in {"ready", "cancelled"} else (False,):
                    with self.subTest(mode=mode, status=status, null_release=null_release):
                        actions = ((pure.A, pure.X, "ready", "in_progress", 0),
                            (pure.A, None if null_release else pure.X, "in_progress", status, 0))
                        result = self.compare(actions, count=1, numerical_mode=mode)
                        self.assertTrue(all(not part["models"] and not part["response_keys"]
                                            for part in result["components"]))
                        self.assertEqual(self.build(actions, count=1, numerical_mode=mode),
                            self.build(actions, count=1, numerical_mode=mode, diagnostics={PARENT: GAPS}))

    def test_wait_placeholder_sources_are_not_admitted(self):
        owner = pure.Interval(pure.A, pure.X, None, pure.THREAD, pure.turn(2), pure.turn(2), True)
        first = wait_pure.fact(2, task=pure.A, execution=pure.X, thread=PARENT)
        second_sender = "41234567-89ab-7cde-8fab-0123456789ab"
        other = replace(first, thread_id=second_sender)
        turns = (pure.TurnObservation(PARENT, pure.turn(2), 2),
                 pure.TurnObservation(second_sender, pure.turn(2), 2))
        actions = ((pure.A, pure.X, "ready", "in_progress", 0),
                   (pure.A, pure.X, "in_progress", "done", 0))
        for observations, valid in (((first,), frozenset()),
                                    ((first, other), frozenset((first, other)))):
            with self.subTest(observations=observations):
                additional = wait_pure.wait_intervals((owner,), turns, observations, valid)
                result = self.build(actions, count=1, additional=additional,
                                    diagnostics={PARENT: GAPS, second_sender: GAPS})
                self.assertEqual(result, self.build(actions, count=1, additional=additional))
                self.assertTrue(all("boundary_unknown" in part["diagnostics"] for part in result["components"]))
        # An admitted sender/turn with no numerical observation still supplies
        # diagnostics. Unknown numeric coverage is not failed source admission.
        valid = wait_pure.wait_intervals((owner,), turns, (first,), frozenset((first,)))
        result = self.build(actions, count=1, additional=valid, diagnostics={PARENT: GAPS})
        self.assertTrue(all(GAPS <= set(part["diagnostics"]) for part in result["components"]))

    def test_validated_interval_without_observations_retains_source_gaps(self):
        actions = ((pure.A, pure.X, "ready", "in_progress", 1),)
        interval = pure.Interval(pure.A, pure.X, None, PARENT, pure.turn(2), pure.turn(2), True, source_admitted=True)
        baseline = self.build(actions, additional=(interval,))
        result = self.build(actions, additional=(interval,), diagnostics={PARENT: GAPS})
        self.assertEqual(numbers(result), numbers(baseline))
        self.assertTrue(all(GAPS <= set(part["diagnostics"]) for part in result["components"]))

    def test_unrelated_thread_diagnostics_do_not_leak(self):
        actions = ((pure.A, pure.X, "ready", "in_progress", 1),
                   (pure.A, pure.X, "in_progress", "done", 2))
        self.assertEqual(self.build(actions), self.build(actions, diagnostics={PARENT: GAPS}))


class SourceDiagnosticRepositoryTests(unittest.TestCase):
    def fixture(self, kind):
        helper = kind()
        self.addCleanup(helper.doCleanups)
        helper.setUp()
        return helper

    def gap(self, repository, source):
        for code in GAPS:
            repository.record_gap(source.source_id, source.thread_id, code)

    def assert_published(self, helper, task, *, quality="incomplete"):
        self.assertEqual(helper.refresh()["publication"], "current")
        period = helper.read(task)["periods"][0]
        self.assertTrue(GAPS <= set(period["gaps"]))
        self.assertEqual(period["quality"], quality)
        index = json.loads(helper.target.resolved_usage_index.read_bytes())
        self.assertTrue(index["current_snapshot_ids"])
        for identity in index["current_snapshot_ids"]:
            snapshot = json.loads((helper.target.resolved_usage_snapshots / (identity + ".json")).read_bytes())
            self.assertTrue(GAPS <= set(snapshot["gaps"]))
            self.assertEqual(snapshot["quality"], quality)
        return period

    def test_same_response_full_and_sliced_collection_publish_identical_numbers_and_gaps(self):
        for limit in (1, 4096):
            with self.subTest(limit=limit):
                h = self.fixture(publication.UsagePublicationTests)
                rows = h.operation("start", h.acknowledgement(publication.A, 1, "in_progress"), "r0", direct=True)
                rows += h.operation("done", h.acknowledgement(publication.A, 1, "done", cycle="A"), "r0", direct=True)
                h.write_turn(rows)
                h.ingest(max_records=limit)
                self.assertEqual(h.refresh()["publication"], "current")
                old = h.read(publication.A)
                self.gap(h.repository, h.source)
                period = self.assert_published(h, publication.A)
                self.assertEqual(period["models"], old["periods"][0]["models"])
                self.assertEqual(period["models"][0]["total_tokens"], 120)
                with h.repository.connection() as connection:
                    keys = {key for identity in period["snapshot_ids"] for key in h.repository.members(connection, identity)}
                self.assertEqual(keys, {("openai", "r0")})
                # Close this helper's patches before the next full/sliced case.
                h.doCleanups()

    def test_old_policy_gaps_are_corrected_by_successor_without_rewriting_history(self):
        h = self.fixture(publication.UsagePublicationTests)
        h.write_turn(h.operation("start", h.acknowledgement(publication.A, 1, "in_progress"), "r0", direct=True)
            + h.operation("done", h.acknowledgement(publication.A, 1, "done", cycle="A"), "r0", direct=True))
        h.ingest()
        self.gap(h.repository, h.source)
        project = policy.project_responses
        def previous(*args, **kwargs):
            # This ended-in-one-response input had no final active execution
            # under v5, so the actual previous projection omitted source gaps.
            kwargs["thread_diagnostics"] = {}
            return project(*args, **kwargs)
        with mock.patch.object(policy, "ALGORITHM", "response-partitions-v5"), \
                mock.patch.object(repository_policy, "ALGORITHM", "response-partitions-v5"), \
                mock.patch.object(repository_policy, "project_responses", side_effect=previous):
            self.assertEqual(h.refresh()["publication"], "current")
            old = h.read(audit=True)
        old_files = {path: path.read_bytes() for path in h.target.resolved_usage_snapshots.glob("*.json")}
        core_before = h.core_path.read_bytes()
        db_before = h.repository.path.read_bytes()
        with self.assertRaises(UsageError):
            h.read(publication.A)
        self.assertEqual(db_before, h.repository.path.read_bytes())
        h.restart()
        self.assert_published(h, publication.A)
        self.assertEqual(core_before, h.core_path.read_bytes())
        self.assertTrue(all(path.read_bytes() == body for path, body in old_files.items()))
        current = h.read(audit=True)
        self.assertTrue(all(item in current["snapshots"] for item in old["snapshots"]))
        self.assertTrue(all(item in current["cycle_links"] for item in old["cycle_links"]))

    def test_same_response_missing_and_conflicting_values_publish_diagnostics_without_counts(self):
        for mode in ("missing", "conflicting"):
            with self.subTest(mode=mode):
                h = self.fixture(publication.UsagePublicationTests)
                rows = h.operation("start", h.acknowledgement(publication.A, 1, "in_progress"), "r0", direct=True)
                rows += h.operation("done", h.acknowledgement(publication.A, 1, "done", cycle="A"), "r0", direct=True)
                if mode == "missing":
                    rows = [row for row in rows if row["type"] != "token_usage_record"]
                else:
                    rows = deepcopy(rows)
                    payload = [row for row in rows if row["type"] == "token_usage_record"][-1]["payload"]
                    payload["usage"] = {**payload["usage"], "input_tokens": 101, "total_tokens": 121}
                h.write_turn(rows)
                h.ingest(max_records=1)
                self.gap(h.repository, h.source)
                period = self.assert_published(h, publication.A,
                    quality="conflicting" if mode == "conflicting" else "incomplete")
                self.assertEqual(period["models"], [])
                self.assertEqual(period["response_count"], 0)
                h.doCleanups()

    def test_native_review_registration_source_gap_reaches_task_and_snapshot(self):
        h = self.fixture(collected.CollectedReviewUsageTests)
        items = h.items((1, 2))
        h.register(items)
        source = h.source(items[0].session_binding.session_id)
        h.write(source, h.records(source.thread_id))
        h.ingest(source, max_records=1)
        h.repository.refresh(h.reader)
        before, keys = h.read(), h.keys()
        self.gap(h.repository, source)
        h.repository.refresh(h.reader)
        after = h.read()
        self.assertEqual(after["periods"][0]["models"], before["periods"][0]["models"])
        self.assertEqual(h.keys(), keys)
        self.assertEqual(after["periods"][0]["models"][0]["total_tokens"], 360)
        self.assertTrue(GAPS <= set(after["periods"][0]["gaps"]))
        with mock.patch.object(service, "repository_for", return_value=h.repository):
            self.assertEqual(service.refresh_usage(h.fixture.target)["publication"], "current")
        for identity in h.read(audit=True)["current_snapshot_ids"]:
            value = json.loads((h.fixture.target.resolved_usage_snapshots / (identity + ".json")).read_bytes())
            self.assertTrue(GAPS <= set(value["gaps"]))

    def test_legacy_registered_review_and_wait_intervals_retain_source_diagnostics(self):
        h = self.fixture(legacy.LegacyReviewRepositoryTests)
        h.register("handoff")
        repository = h.ingest(h.repository("diagnostic"), h.records(tie=True, first=5000), "diagnostic", "sliced")
        before = h.projection(repository)
        source = legacy.SourceInput(legacy.REVIEWER.session_id, h.logs / "diagnostic.jsonl", h.logs, h.root)
        self.gap(repository, source)
        repository.refresh(h.reader)
        after = h.projection(repository)
        self.assertTrue(after)
        self.assertEqual(numbers({"components": before}), numbers({"components": after}))
        self.assertTrue(all(GAPS <= set(part["diagnostics"]) for part in after))
        h.doCleanups()

        h = self.fixture(waiting.UsageWaitRepositoryTests)
        anchor = h.prepare_wait()
        # The wait session is independent from the owner thread. Its actual
        # decision marker must carry its source gaps through the shared consumer.
        anchor = replace(anchor, thread_id=PARENT)
        h.repository.record_host_receipt(anchor)
        h.repository = UsagePublicationRepository(h.repository.path, *h.repository.basis)
        h.repository.initialize()
        source = collected.SourceInput(PARENT, h.logs / "wait.jsonl", h.logs, h.root)
        source.path.write_text(json.dumps(collected.event("session_meta", id=PARENT, cwd=str(h.root), model_provider="openai")) + "\n"
            + json.dumps(collected.event("event_msg", type="task_started", turn_id=anchor.turn_id, started_at=5)) + "\n"
            + json.dumps(collected.usage("wait", thread=PARENT, turn=anchor.turn_id)) + "\n", encoding="utf-8")
        collected.register_source(h.repository, source, collected.CallerIdentity(PARENT))
        h.repository.collect_source(source)
        with h.core_reader() as core:
            before = h.repository.attribution(core)
        self.gap(h.repository, source)
        h.repository.refresh(h.core_reader)
        with h.core_reader() as core:
            after = h.repository.attribution(core)
        self.assertEqual(numbers(before), numbers(after))
        self.assertTrue(any(("openai", "wait") in part["response_keys"] for part in after["components"]))
        self.assertTrue(all(GAPS <= set(part["diagnostics"]) for part in after["components"]))
        # A restore that removes the immutable anchor must revoke the source
        # admission, while retaining old immutable snapshots as history.
        with h.repository.connection() as connection:
            old_documents = dict(connection.execute("SELECT snapshot_id,document FROM usage_snapshots"))
        with sqlite3.connect(h.core_path) as core:
            core.execute("DELETE FROM artifact_manifests WHERE artifact_manifest_id=?", (anchor.artifact_manifest_id,))
        h.repository.refresh(h.core_reader)
        with h.core_reader() as core:
            restored = h.repository.attribution(core)
        self.assertTrue(all(not (GAPS & set(part["diagnostics"])) for part in restored["components"]))
        self.assertTrue(all(("openai", "wait") not in part["response_keys"] for part in restored["components"]))
        with h.repository.connection() as connection:
            documents = dict(connection.execute("SELECT snapshot_id,document FROM usage_snapshots"))
        self.assertTrue(all(documents[key] == value for key, value in old_documents.items()))


if __name__ == "__main__":
    unittest.main()
