"""Normal mixed consumers stay local; explicit audit remains independent."""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from time import perf_counter
import unittest
from unittest import mock

from tests.test_evidence_projection_capture import make_fixture
from tests.evidence_reader_oracle import read_evidence_index, iter_evidence_sources
from tests.integrity_scale_support import fixed_completion_histories, fixture_id_band, public_command
from tests.test_post_commit_maintenance import invoke
from tests.test_viewer_renderer import embedded_snapshot
from tests.verification_receipt_test_support import (
    add_task, seed_current_review_evidence, add_receipt,
)
from task_governance_tool import async_backup_opportunity, storage, evidence_publication, evidence_projection
from task_governance_tool import viewer, viewer_maintenance, maintenance
from task_governance_tool.sqlite_admission import AdmissionConnection


def thin_index_reads(project_id):
    """Only these two complete SELECTs may scale with the published inventory."""
    membership = f"""
        SELECT cycle.completion_cycle_id, cycle.task_id, cycle.saved_cycle_ordinal,
               cycle.evidence_basis_version, cycle.completion_evidence_bundle_id,
               bundle.project_id AS bundle_project_id, bundle.task_id AS bundle_task_id,
               bundle.completion_cycle_id AS bundle_cycle_id,
               bundle.cycle_ordinal AS bundle_ordinal, bundle.bundle_digest,
               bundle.bundle_version, bundle.sealed_at
          FROM task_completion_cycles AS cycle
          LEFT JOIN completion_evidence_bundles AS bundle
            ON bundle.completion_evidence_bundle_id = cycle.completion_evidence_bundle_id
         WHERE cycle.project_id = '{project_id}'
         ORDER BY cycle.task_id COLLATE BINARY, cycle.saved_cycle_ordinal,
                  cycle.completion_cycle_id COLLATE BINARY
    """
    count = f"SELECT COUNT(*) FROM completion_evidence_bundles WHERE project_id = '{project_id}'"
    return {" ".join(membership.split()): "index_entries", count: "bundle_count"}


@contextmanager
def observe_sql(*, forbid_audit=True, output_reads=None):
    """Observe actual statements on all SQLite connections, including aliases."""
    result = dict(sql=0, vm=0, connections=0, transactions=0, audits=[])
    original = sqlite3.connect

    def trace(sql):
        result["sql"] += 1
        if sql.lstrip().upper().startswith("BEGIN"):
            result["transactions"] += 1

    def authorize(action, first, second, database, trigger):
        if action == sqlite3.SQLITE_PRAGMA and first.lower() in {
                "quick_check", "integrity_check", "foreign_key_check"}:
            result["audits"].append(first.lower())
            if forbid_audit:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        result["connections"] += 1
        output = None

        def statement(sql):
            nonlocal output
            trace(sql)
            if output_reads:
                name = output_reads.get(" ".join(sql.split()))
                output = result["output_reads"].get(name)
                if output is not None:
                    output["calls"] += 1

        def progress():
            result["vm"] += 1
            if output is not None:
                output["vm"] += 1
            return 0

        connection.set_trace_callback(statement)
        connection.set_progress_handler(progress, 1)
        connection.set_authorizer(authorize)
        return connection

    if output_reads:
        result["output_reads"] = {name: {"calls": 0, "vm": 0} for name in output_reads.values()}
    with mock.patch.object(sqlite3, "connect", new=connect):
        start = perf_counter()
        yield result
        result["seconds"] = round(perf_counter() - start, 6)


@contextmanager
def measure_seal_inserts(writer):
    """Count precisely the two execute calls with native deferred FK scans."""
    inserts = {}
    writer["seal_vm"] = {}
    execute = AdmissionConnection.execute

    def observed(connection, sql, *args):
        seal = re.match(r"\s*INSERT INTO (task_completion_cycles|completion_evidence_bundles)\s*\(", sql, re.I)
        if not seal:
            return execute(connection, sql, *args)
        table = seal[1].lower()
        if table in inserts:
            raise AssertionError(f"Multiple seal inserts into {table}")
        start = writer["vm"]
        result = execute(connection, sql, *args)
        writer["seal_vm"][table] = writer["vm"] - start
        inserts[table] = (sql, args)
        return result

    with mock.patch.object(AdmissionConnection, "execute", observed):
        yield inserts


def native_fk_work(target, inserts):
    """Budget only the native FK loop's nonmatching path for unrelated rows."""
    if set(inserts) != {"task_completion_cycles", "completion_evidence_bundles"}:
        raise AssertionError("Both measured seal inserts are required")
    budgets = {}
    with closing(storage.connect_readonly(target.db_path)) as connection:
        tables = dict(connection.execute("SELECT rootpage, tbl_name FROM sqlite_schema WHERE rootpage > 0"))
        for table, (sql, args) in inserts.items():
            plan = connection.execute("EXPLAIN " + sql, *args).fetchall()
            opened = {}
            budgets[table] = 0
            for pc, op in enumerate(plan):
                if pc and op[0] == 0:  # Trigger subprograms are application work.
                    break
                if op[1] == "OpenRead":
                    opened[op[2]] = op[3]  # cursor number -> B-tree root page
                if op[1] != "Next":
                    continue
                start = op[3]
                rewind = plan[start - 1]
                body = plan[start:pc + 1]
                if (rewind[1:4] != ("Rewind", op[2], pc + 1)
                        or [step[1] for step in body] != ["Column", "Ne", "FkCounter", "Next"]
                        or body[0][2] != op[2] or body[1][3] != pc
                        or body[2][2:4] != (1, -1)):
                    continue
                child = tables[opened[op[2]]]
                if child not in {"task_completion_cycles", "completion_evidence_bundles", "evidence_references"}:
                    raise AssertionError(f"Unclassified FK scan of {child}")
                rows = connection.execute(f'SELECT COUNT(*) FROM "{child}"').fetchone()[0]
                # On an unrelated row Ne jumps directly to Next, skipping FkCounter.
                budgets[table] += rows * (len(body) - 1)
    return budgets


class IntegrityConsumerClosureTests(unittest.TestCase):
    def target(self, history=2):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = make_fixture(root, count=history)
        self.assertEqual(evidence_publication.publish_setup_evidence_projection(target).code, "succeeded")
        self.assertEqual(viewer_maintenance.publish_setup_viewer(target).code, "succeeded")
        return target

    def command(self, target, *args, maintenance_enabled=True):
        code, out, err = invoke(target, *args, "--json", maintenance_enabled=maintenance_enabled)
        self.assertEqual(code, 0, out + err)
        result = json.loads(out)
        self.assertFalse(result.get("warnings"), result)
        return result

    def selected(self, target):
        task = add_task(target.db_path, target.project.canonical_repo,
                        title="Selected cross-consumer task", review_tier=2)
        generation = seed_current_review_evidence(target.db_path, target.project.canonical_repo, task["task_id"])
        result = add_receipt(target.db_path, target.project.canonical_repo, task["task_id"], generation)
        self.assertEqual(result.returncode, 0, result.stdout)
        return task["task_id"]

    def complete(self, target, task, *, maintenance_enabled=True):
        return self.command(target, "task", "complete", task,
            "--verification-complete", "--review-complete", "--commit-not-required",
            maintenance_enabled=maintenance_enabled)

    def oracle(self, target, count):
        index = read_evidence_index(target.resolved_evidence_root,
                                    expected_project_id=target.project.project_id)
        self.assertEqual(len(index.entries), count)
        self.assertEqual(len(tuple(iter_evidence_sources(index))), count)
        with closing(storage.connect_initialized_readonly(target)) as connection:
            full = storage.capture_current_evidence_projection(connection, target)
        self.assertEqual(target.resolved_evidence_index.read_bytes(), full.index.document)
        shown = embedded_snapshot(target.resolved_viewer_path.read_text(encoding="utf-8"))
        with closing(storage.connect_snapshot_readonly(target.db_path)) as connection:
            expected = viewer.build_viewer_snapshot(connection, target, generated_at=shown["generated_at"])
        self.assertEqual(shown, expected.snapshot)

    def test_review_completion_and_both_projections_without_ordinary_audits(self):
        target = self.target()
        old = {p.name: p.read_bytes() for p in target.resolved_evidence_bundles.iterdir()}
        with observe_sql() as observed, mock.patch.object(evidence_projection,
                "build_projection_bundle_artifact", wraps=evidence_projection.build_projection_bundle_artifact) as builds:
            task = self.selected(target)
            self.command(target, "task", "checkpoint", task,
                         "--summary", "Mixed consumer check", "--next-action", "Complete")
            self.complete(target, task)
            # Completion readback and publication independently validate the new
            # Bundle. Neither may reconstruct an acknowledged unrelated Bundle.
            self.assertGreater(builds.call_count, 0)
            self.assertEqual({call.args[0].bundle.task_id for call in builds.call_args_list}, {task})
            count = builds.call_count
            self.assertEqual(maintenance.run_post_commit_maintenance(target,
                maintenance.MutationOutcome(True, True)), [])
            self.assertEqual(builds.call_count, count)
        self.assertEqual(observed["audits"], [])
        self.assertGreater(observed["connections"], 1)
        for name, raw in old.items():
            self.assertEqual((target.resolved_evidence_bundles / name).read_bytes(), raw)
        shown = embedded_snapshot(target.resolved_viewer_path.read_text(encoding="utf-8"))
        self.assertEqual(next(row["status"] for row in shown["tasks"] if row["task_id"] == task), "done")
        self.oracle(target, 3)

    def test_hidden_old_note_is_audit_fault_not_an_ordinary_completion_gate(self):
        target = self.target()
        # Preserve exact schema: change only an immutable value in this disposable DB.
        with closing(storage.connect(target.db_path)) as connection:
            ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_task_events_note_body_immutable'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_task_events_note_body_immutable")
            connection.execute("UPDATE task_events SET note_body='Invalid retained note' WHERE event_type='task_added'")
            connection.execute(ddl)
            connection.commit()
        with observe_sql() as observed:
            task = self.selected(target)
            self.complete(target, task)
        self.assertEqual(observed["audits"], [])
        last_good = target.resolved_evidence_index.read_bytes()
        with self.assertRaises(storage.StorageError):
            with closing(storage.connect_initialized_readonly(target)):
                pass
        self.assertEqual(evidence_publication.publish_setup_evidence_projection(target).code, "failed")
        self.assertEqual(target.resolved_evidence_index.read_bytes(), last_good)
        index = read_evidence_index(target.resolved_evidence_root)
        self.assertEqual(len(tuple(iter_evidence_sources(index))), 3)

    def test_fixed_selected_check_and_incremental_publication_separate_output_growth(self):
        results = []
        task, histories = self.enterContext(fixed_completion_histories())
        for history, target in histories:
            self.assertEqual(evidence_publication.publish_setup_evidence_projection(target).code, "succeeded")
            self.assertEqual(viewer_maintenance.publish_setup_viewer(target).code, "succeeded")
            with observe_sql() as selected:
                gate = public_command(target, "task", "complete", task, "--check", "--read-only",
                                      "--verification-complete", "--review-complete", "--commit-not-required")
            self.assertIs(gate["data"]["ready"], True)
            self.assertEqual(gate["data"]["blocking_codes"], [])
            documents = []
            build = evidence_projection.build_projection_bundle_artifact
            def capture(*args, **kwargs):
                value = build(*args, **kwargs)
                documents.append((args[0].bundle.task_id, len(value.document)))
                return value
            with mock.patch.object(evidence_projection, "build_projection_bundle_artifact", new=capture):
                with fixture_id_band('b'), observe_sql() as writer, measure_seal_inserts(writer) as inserts:
                    public_command(target, "task", "complete", task, "--verification-complete",
                                   "--review-complete", "--commit-not-required")
                writer["other_vm"] = writer["vm"] - sum(writer["seal_vm"].values())
                writer["fk_work"] = native_fk_work(target, inserts)
                # Accept the real backup opportunity in the disposable project.
                # The service lease requires no worker or external host process.
                with async_backup_opportunity.service_lease(target) as owner:
                    compatibility = async_backup_opportunity.current_compatibility()
                    async_backup_opportunity.activate_service(target, owner, compatibility)
                    with observe_sql(output_reads=thin_index_reads(target.project.project_id)) as publish:
                        self.assertEqual(maintenance.run_post_commit_maintenance(
                            target, maintenance.MutationOutcome(True, True)), [])
                for name, work in publish["output_reads"].items():
                    self.assertEqual(work["calls"], 1, name)
                    self.assertGreater(work["vm"], 0, name)
                publish["other_vm"] = publish["vm"] - sum(
                    work["vm"] for work in publish["output_reads"].values())
            self.assertEqual(selected["audits"] + writer["audits"] + publish["audits"], [])
            self.assertGreater(len(documents), 0)
            self.assertEqual({task_id for task_id, size in documents}, {task})
            self.oracle(target, history + 1)
            results.append(dict(history=history, selected=selected, writer=writer, publication=publish,
                bundle_builds=len(documents), new_bundle_bytes=sum(size for task_id, size in documents),
                index_bytes=target.resolved_evidence_index.stat().st_size,
                viewer_bytes=target.resolved_viewer_path.stat().st_size))
        small, large = results
        print("integrity-consumer-cost " + json.dumps(results, sort_keys=True))
        # A fixed selected closure must not traverse the added unrelated history.
        with self.subTest(stage="completion check"):
            for counter in ("connections", "sql", "vm"):
                self.assertEqual(small["selected"][counter], large["selected"][counter], counter)
        small_write, large_write = small["writer"], large["writer"]
        with self.subTest(stage="completion writer"):
            for counter in ("sql", "connections", "transactions", "other_vm"):
                self.assertEqual(small_write[counter], large_write[counter], counter)
        for table in small_write["seal_vm"]:
            with self.subTest(seal=table):
                growth = large_write["seal_vm"][table] - small_write["seal_vm"][table]
                fk_growth = large_write["fk_work"][table] - small_write["fk_work"][table]
                self.assertLessEqual(growth, fk_growth, table)
        # Complete Viewer output grows in bytes. Its delta reads, all publication
        # guards and the unpublished Bundle's semantic validation are fixed.
        with self.subTest(stage="publication"):
            for counter in ("sql", "connections", "transactions", "other_vm"):
                self.assertEqual(small["publication"][counter], large["publication"][counter],
                                 f"publication {counter}")
        self.assertEqual(large["bundle_builds"], small["bundle_builds"])
        self.assertEqual(large["new_bundle_bytes"], small["new_bundle_bytes"])
        self.assertGreater(large["index_bytes"], small["index_bytes"])
        self.assertGreater(large["viewer_bytes"], small["viewer_bytes"])


if __name__ == "__main__":
    unittest.main()
