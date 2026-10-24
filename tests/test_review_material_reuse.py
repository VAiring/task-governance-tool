"""Real-repository checks for immutable review material reuse only."""
from collections import Counter
from contextlib import closing, contextmanager
import json
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.review_answer_fixture_support import attach_existing, review_fixture
from tests.test_review_continuation import A, B
from task_governance_tool import reviews, review_results, storage
from task_governance_tool import review_session_transport as transport
from task_governance_tool.session_identity import CallerIdentity

PROFILES = []


@contextmanager
def measured():
    counts = Counter()
    def profile(frame, event, value):
        if 'task_governance_tool' not in frame.f_code.co_filename:
            return
        name = frame.f_code.co_name
        if event == 'call' and name in ('_validate_artifact_manifest_storage', '_validated_authority_context'):
            counts[name] += 1
        if event == 'return' and name == '_validate_artifact_manifest_storage' and value is not None:
            counts['entries'] += sum(record.row['entry_count'] for record in value[0].values())
    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        yield counts
    finally:
        sys.setprofile(previous)


def git_target(fixture, entries):
    def git(*arguments):
        subprocess.run(['git', '-C', str(fixture.repo), *arguments],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    git('init', '-q')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-q', '--allow-empty', '-m', 'fixture')
    for index in range(entries):
        (fixture.repo / f'entry-{index}.txt').write_text('fixture\n', encoding='utf-8')
    git('add', '--', '.')
    with closing(storage.connect_initialized(fixture.target, operation_local=True,
            review_task_id=fixture.task_id)) as connection, connection:
        reviews.set_git_snapshot_target(connection, fixture.target.project, fixture.task_id,
            database_target=fixture.target, caller=fixture.caller)
    return attach_existing(db=fixture.db, repo=fixture.repo, task_id=fixture.task_id,
        schema_version=33, target=fixture.target)


def register(fixture, findings, *, collected=False):
    if collected:
        from task_governance_tool.review_answer_transport import CollectedItem, frame_collected_submission, decode_collected_submission
        from task_governance_tool.review_collected_results import add_collected_review_results
        items = []
        for slot in (1, 2):
            metadata, answer = fixture.metadata(slot, findings=findings if slot == 1 else [])
            items.append(CollectedItem(slot, answer.raw, metadata, answer))
        payload = decode_collected_submission(frame_collected_submission(
            items[0].metadata['basis']['preparation_id'], items))
    else:
        raw, metadata = [], []
        for actor in (A, B):
            original, _ = fixture.original(actor, findings=findings if actor == A else [])
            document = json.loads(original)
            document['review_target'] = dict(fixture.packet['review_target'])
            original = json.dumps(document, separators=(',', ':')).encode()
            raw.append(original)
            metadata.append(transport.metadata_for(fixture.packet, original, CallerIdentity(actor)))
        payload = transport.decode_submission(transport.frame_submission(raw, metadata))
    with closing(storage.connect_initialized(fixture.target, operation_local=True,
            review_task_id=fixture.task_id)) as connection, connection:
        with measured() as counts:
            if collected:
                result = add_collected_review_results(connection, fixture.target.project,
                    fixture.task_id, payload, database_target=fixture.target, caller=fixture.caller)
            else:
                result = review_results.add_review_results(connection, fixture.target.project,
                    fixture.task_id, payload.document, database_target=fixture.target,
                    caller=fixture.caller, session_bindings=payload.bindings)
    return result, dict(counts)


def run_case(count, entries, *, collected=False):
    with review_fixture(schema_version=33) as fixture:
        fixture = git_target(fixture, entries)
        findings = [dict(severity='low', summary=f'Condition {i}') for i in range(count)]
        result, registration = register(fixture, findings, collected=collected)
        ids = [item['finding']['review_finding_id'] for item in result['receipts'][0]['findings']]
        assert [item['finding']['summary'] for item in result['receipts'][0]['findings']] == [f['summary'] for f in findings]
        assert result['review_gate']['satisfied']
        with closing(storage.connect_initialized(fixture.target, operation_local=True,
                review_task_id=fixture.task_id)) as connection, connection:
            originals = [dict(connection.execute('SELECT * FROM review_findings WHERE review_finding_id=?', (i,)).fetchone()) for i in ids]
            with measured() as counts:
                resolved = reviews.resolve_review_findings(connection, fixture.target.project,
                    fixture.task_id, resolutions=[dict(finding_id=i, resolution='Confirmed correction') for i in ids],
                    caller=fixture.caller, database_target=fixture.target)
            resolution = dict(counts)
            for old, new in zip(originals, resolved):
                assert new.finding['status'] == 'resolved'
                for key in ('review_finding_id', 'review_receipt_id', 'severity', 'summary', 'created_at'):
                    assert new.finding[key] == old[key]
        with closing(storage.connect_initialized_readonly(fixture.target,
                operation_local=True, review_task_id=fixture.task_id)) as connection:
            with measured() as counts:
                shown = reviews.read_review_evidence(connection, fixture.basis['project_id'], fixture.task_id,
                    source_schema_version=33)
            projection = dict(counts)
            assert shown['gate']['satisfied'] and shown['counts']['open_low'] == 0
        from task_governance_tool.review_finalization_repository import reported_review_evidence
        record = SimpleNamespace(basis=fixture.basis, continuation_id=None, retained_receipt_ids=())
        with measured() as counts:
            report_receipts, report_findings = reported_review_evidence(fixture.target, record,
                [item['receipt']['review_receipt_id'] for item in result['receipts']])
        report = dict(counts)
        assert {item['review_finding_id'] for item in report_findings} == set(ids)
        assert len(report_receipts) == 2
        return dict(findings=count, entries=entries, collected=collected,
            registration=registration, resolution=resolution, projection=projection, report=report,
            output=dict(registered=count, resolved=len(resolved), reported=len(report_findings), gate=shown['gate'], counts=shown['counts']))


def continuation_original(fixture, actor, *, verdict='pass', findings=(), continued=None):
    raw, _ = fixture.original(actor, verdict=verdict, findings=findings, continued=continued)
    document = json.loads(raw)
    document['review_target'] = dict(fixture.packet['review_target'])
    raw = json.dumps(document, separators=(',', ':')).encode()
    packet = dict(fixture.packet)
    if continued is not None:
        packet['review_session_context'] = dict(version=2, project_id=fixture.basis['project_id'],
            execution_id=fixture.basis['execution_id'], preparation_id=continued.preparation_id,
            continuation_id=continued.continuation_id, continuation_sequence=continued.sequence,
            selected_reviewers=[dict(session_id=row.session_id, action=row.action,
                prior_receipt_id=row.prior_receipt_id) for row in continued.selected])
    return raw, transport.metadata_for(packet, raw, CallerIdentity(actor))


@contextmanager
def reassessment_fixture(count, entries):
    with review_fixture(schema_version=33) as fixture:
        fixture = git_target(fixture, entries)
        initial = fixture.register([
            continuation_original(fixture, A, verdict='changes_requested',
                findings=[dict(severity='medium', summary=f'Reconsider condition {i}') for i in range(count)]),
            continuation_original(fixture, B)])
        prior = initial['receipts'][0]['receipt']['review_receipt_id']
        retained = initial['receipts'][1]['receipt']['review_receipt_id']
        ids = [item['finding']['review_finding_id'] for item in initial['receipts'][0]['findings']]
        record = fixture.prepare(prior=prior)
        result = fixture.register([continuation_original(fixture, A, continued=record)],
            continued=record, retained=(retained,))
        yield fixture, ids, result['receipts'][0]['receipt']['review_receipt_id'], retained


def run_reassessment_case(count, entries, *, already_resolved=False):
    with reassessment_fixture(count, entries) as (fixture, ids, successor, _):
        with closing(storage.connect_initialized(fixture.target, operation_local=True,
                review_task_id=fixture.task_id)) as connection, connection:
            if already_resolved:
                reviews.resolve_review_findings(connection, fixture.target.project, fixture.task_id,
                    resolutions=[dict(finding_id=i, resolution='Original correction') for i in ids],
                    caller=fixture.caller, database_target=fixture.target)
            originals = [dict(connection.execute('SELECT * FROM review_findings WHERE review_finding_id=?', (i,)).fetchone()) for i in ids]
            with measured() as counts:
                result = reviews.resolve_review_findings(connection, fixture.target.project, fixture.task_id,
                    resolutions=[dict(finding_id=i, resolution='Confirmed by reassessment') for i in ids],
                    reassessment_receipt=successor, caller=fixture.caller, database_target=fixture.target)
            for original, resolved in zip(originals, result):
                assert resolved.finding['status'] == 'resolved'
                keys = ('review_finding_id', 'review_receipt_id', 'severity', 'summary', 'created_at')
                if already_resolved:
                    keys += ('resolution_summary', 'resolved_at')
                assert all(resolved.finding[key] == original[key] for key in keys)
            links = connection.execute('SELECT * FROM review_reassessment_resolutions').fetchall()
            assert {row['review_finding_id'] for row in links} == set(ids)
            assert all(row['successor_receipt_id'] == successor for row in links)
            evidence = reviews.read_review_evidence(connection, fixture.basis['project_id'],
                fixture.task_id, source_schema_version=33)
            assert evidence['gate']['satisfied']
        return dict(findings=count, entries=entries, already_resolved=already_resolved,
            resolution=dict(counts), output=dict(resolved=len(result), links=len(links), gate=evidence['gate']))


class ReviewMaterialReuseTests(unittest.TestCase):
    def test_findings_and_entries_vary_independently_with_same_outputs(self):
        observed = []
        for entries in (1, 16):
            for count in (1, 8, 64):
                with self.subTest(entries=entries, findings=count):
                    observed.append(run_case(count, entries))
        PROFILES.extend(observed)
        for operation in ('registration', 'resolution', 'projection', 'report'):
            for entries in (1, 16):
                counts = [row[operation] for row in observed if row['entries'] == entries]
                self.assertTrue(all(item == counts[0] for item in counts), counts)
        self.assertLess(observed[-1]['resolution']['_validate_artifact_manifest_storage'], 6)

    def test_collected_registration_uses_same_bounded_materials_and_gate(self):
        results = [run_case(count, 4, collected=True) for count in (1, 8, 64)]
        PROFILES.extend(results)
        self.assertTrue(all(row['registration'] == results[0]['registration'] for row in results))

    def test_scope_rejects_other_connection_task_and_expired_borrow(self):
        from task_governance_tool.review_material_repository import review_materials
        with review_fixture(schema_version=33) as fixture:
            with closing(storage.connect_initialized(fixture.target)) as first, closing(storage.connect_initialized(fixture.target)) as second:
                project_id = fixture.basis['project_id']
                with review_materials(first, project_id=project_id, task_id=fixture.task_id) as value:
                    value.require(first, project_id, fixture.task_id)
                    with self.assertRaises(storage.StorageError):
                        value.require(second, project_id, fixture.task_id)
                    with self.assertRaises(storage.StorageError):
                        value.require(first, project_id, 'another-task')
                with self.assertRaises(storage.StorageError):
                    value.require(first, project_id, fixture.task_id)
                self.assertFalse(first.in_transaction)

    def test_batch_detects_task_basis_drift_and_rolls_back_all_findings(self):
        actual = review_results.add_review_finding
        with review_fixture(schema_version=33) as fixture:
            calls = []
            def changed(connection, *args, **kwargs):
                if calls:
                    connection.execute('UPDATE tasks SET priority=? WHERE task_id=?', ('high', fixture.task_id))
                calls.append(1)
                return actual(connection, *args, **kwargs)
            with mock.patch.object(review_results, 'add_review_finding', side_effect=changed):
                with self.assertRaises(reviews.ReviewEvidenceError):
                    register(fixture, [dict(severity='low', summary=f'Condition {i}') for i in range(3)])
            with closing(storage.connect_initialized_readonly(fixture.target)) as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM review_findings').fetchone()[0], 0)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM review_receipts').fetchone()[0], 0)

    def test_commit_or_rollback_then_new_transaction_cannot_reuse_borrow(self):
        from task_governance_tool.review_material_repository import review_materials
        with review_fixture(schema_version=33) as fixture:
            with closing(storage.connect_initialized(fixture.target)) as connection:
                for end in (connection.commit, connection.rollback):
                    with self.subTest(end=end.__name__), self.assertRaises(storage.StorageError):
                        with review_materials(connection, project_id=fixture.basis['project_id'], task_id=fixture.task_id) as value:
                            end()
                            connection.execute('BEGIN')
                            value.require(connection, fixture.basis['project_id'], fixture.task_id)
                    self.assertFalse(connection.in_transaction)

    def test_owner_change_between_findings_rejects_and_rolls_back(self):
        from task_governance_tool.task_values import TaskValidationError
        actual = review_results.add_review_finding
        with review_fixture(schema_version=33) as fixture:
            calls = []
            def changed(connection, *args, **kwargs):
                if calls:
                    connection.execute('UPDATE task_ownership SET generation=generation+1 WHERE task_id=?', (fixture.task_id,))
                calls.append(1)
                return actual(connection, *args, **kwargs)
            with mock.patch.object(review_results, 'add_review_finding', side_effect=changed):
                with self.assertRaises(TaskValidationError) as raised:
                    register(fixture, [dict(severity='low', summary=f'Condition {i}') for i in range(2)])
            self.assertEqual(raised.exception.code, 'task_ownership_changed')
            with closing(storage.connect_initialized_readonly(fixture.target)) as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM review_findings').fetchone()[0], 0)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM review_receipts').fetchone()[0], 0)

    def test_schema_change_invalidates_materials_and_fresh_scope_rejects_corruption(self):
        from task_governance_tool.review_material_repository import review_materials
        with review_fixture(schema_version=33) as fixture:
            fixture = git_target(fixture, 4)
            with closing(storage.connect_initialized(fixture.target)) as connection:
                original = dict(connection.execute('SELECT * FROM artifact_manifests WHERE task_id=?', (fixture.task_id,)).fetchone())
                arguments = dict(project_id=fixture.basis['project_id'], task_id=fixture.task_id,
                    manifest_ids={original['artifact_manifest_id']}, snapshot_ids={original['authority_snapshot_id']})
                connection.execute('BEGIN')
                with self.assertRaises(storage.StorageError):
                    with review_materials(connection, project_id=fixture.basis['project_id'], task_id=fixture.task_id) as value:
                        value.selected(connection, **arguments)
                        triggers = connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='artifact_manifests'").fetchall()
                        for row in triggers:
                            connection.execute('DROP TRIGGER "' + row[0].replace('"', '""') + '"')
                        connection.execute('UPDATE artifact_manifests SET digest=? WHERE artifact_manifest_id=?', ('sha256:' + '0' * 64, original['artifact_manifest_id']))
                        value.selected(connection, **arguments)
                # A new borrow must validate originals again, even on the same
                # connection. Only this disposable transaction is corrupted.
                with self.assertRaises(storage.StorageError):
                    with review_materials(connection, project_id=fixture.basis['project_id'], task_id=fixture.task_id) as value:
                        value.selected(connection, **arguments)
                connection.rollback()

    def test_reassessment_batches_share_materials_for_open_and_resolved_findings(self):
        results = [run_reassessment_case(count, entries, already_resolved=resolved)
            for resolved in (False, True) for entries in (1, 16) for count in (1, 8, 64)]
        PROFILES.extend(results)
        for resolved in (False, True):
            for entries in (1, 16):
                counts = [row['resolution'] for row in results
                    if row['already_resolved'] == resolved and row['entries'] == entries]
                self.assertTrue(all(row == counts[0] for row in counts), counts)

    def test_late_invalid_successor_rolls_back_all_resolutions_and_links(self):
        from task_governance_tool import review_continuation_repository as repository
        from task_governance_tool.task_values import TaskValidationError
        actual = repository.append_resolution_locked
        with reassessment_fixture(3, 4) as (fixture, ids, successor, unrelated):
            with closing(storage.connect_initialized(fixture.target)) as connection, connection:
                tables = ('tasks', 'review_findings', 'review_reassessment_resolutions', 'task_events')
                before = storage._selected_table_projection_snapshot(connection, tables)
                calls = []
                def invalidate_later(*args, **kwargs):
                    if calls:
                        kwargs['successor_id'] = unrelated
                    calls.append(1)
                    return actual(*args, **kwargs)
                with mock.patch.object(repository, 'append_resolution_locked', side_effect=invalidate_later):
                    with self.assertRaises(TaskValidationError) as raised:
                        reviews.resolve_review_findings(connection, fixture.target.project, fixture.task_id,
                            resolutions=[dict(finding_id=i, resolution='Confirm correction') for i in ids],
                            reassessment_receipt=successor, caller=fixture.caller, database_target=fixture.target)
                self.assertEqual(raised.exception.code, 'invalid_review_evidence')
                self.assertEqual(len(calls), 2)
                self.assertEqual(storage._selected_table_projection_snapshot(connection, tables), before)

    def test_continuation_read_chunks_do_not_repeat_fixed_material_validation(self):
        from task_governance_tool import review_continuation_repository as repository
        from task_governance_tool import review_continuation_evidence_repository as evidence_repository
        with reassessment_fixture(8, 16) as (fixture, ids, successor, _):
            with closing(storage.connect_initialized(fixture.target)) as connection, connection:
                reviews.resolve_review_findings(connection, fixture.target.project, fixture.task_id,
                    resolutions=[dict(finding_id=i, resolution='Confirm correction') for i in ids],
                    reassessment_receipt=successor, caller=fixture.caller, database_target=fixture.target)
                for reader in (
                    lambda: repository.read_judgments(connection, basis=fixture.basis),
                    lambda: evidence_repository.read_resolution_links(connection,
                        project_id=fixture.basis['project_id'], task_id=fixture.task_id)):
                    with measured() as normal_counts:
                        normal = reader()
                    with mock.patch.object(storage, 'COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE', 1):
                        with measured() as fragmented_counts:
                            fragmented = reader()
                    self.assertEqual(normal, fragmented)
                    self.assertEqual(normal_counts, fragmented_counts)


if __name__ == '__main__':
    unittest.main()
