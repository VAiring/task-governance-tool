"""Contract-bearing collected answers must survive the actual completion writer."""
from __future__ import annotations

import unittest
from contextlib import closing
from tests.review_answer_fixture_support import ReviewContract, review_fixture
from tests.test_m23s_schema22_validation import _bundle_artifacts
from tests import evidence_reader_oracle as reader
from tests.verification_receipt_test_support import add_receipt, completion
from task_governance_tool import storage


class CollectedContractCompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(review_fixture(
            schema_version=31,
            contract=ReviewContract(
                scope="Implement bounded task",
                acceptance="Two independent passes",
                authority_ref="user:isolated-completion-regression",
            ),
        ))

    def test_check_and_actual_completion_agree_with_contract_and_v2_passes(self):
        fixture = self.fixture
        self.assertEqual(fixture.basis["contract_revision"], 1)
        self.assertIsNotNone(fixture.basis["acceptance_criterion_id"])
        with closing(storage.connect_initialized(fixture.target)) as connection:
            for index in (1, 2):
                receipt, _ = fixture.add(connection, index)
                self.assertEqual(receipt["review_provenance"]["provenance_version"], 2)
            connection.commit()
        self.assert_completion(gate_basis_version=1)

    def test_contract_bearing_continuation_writes_gate_basis_two_bundle(self):
        self.fixture.register_collected_source()
        self.assert_completion(gate_basis_version=2)

    def assert_completion(self, *, gate_basis_version):
        fixture = self.fixture
        verified = add_receipt(fixture.db, fixture.repo,
            fixture.basis["task_id"], fixture.basis["target_generation"])
        self.assertEqual(verified.returncode, 0, verified.stdout)
        checked = completion(fixture.db, fixture.repo, fixture.basis["task_id"], check=True)
        self.assertEqual(checked.returncode, 0, checked.stdout)
        completed = completion(fixture.db, fixture.repo, fixture.basis["task_id"])
        self.assertEqual(completed.returncode, 0, completed.stdout)
        with closing(storage.connect_initialized(fixture.target)) as connection:
            self.assertEqual(connection.execute("SELECT status FROM tasks WHERE task_id=?",
                (fixture.basis["task_id"],)).fetchone()[0], "done")
            links = connection.execute("SELECT producer_class FROM criterion_evidence_links "
                "WHERE criterion_id=? AND relation='review_assessment'",
                (fixture.basis["acceptance_criterion_id"],)).fetchall()
            self.assertEqual([row[0] for row in links], ["taskgov_answer_collector"] * 2)
            self.assertEqual(tuple(connection.execute("SELECT source_schema_version,bundle_version "
                "FROM completion_evidence_bundles").fetchone()), (31, 4))
            self.assertEqual(connection.execute("SELECT gate_basis_version FROM task_completion_cycles").fetchone()[0],
                gate_basis_version)
            _, artifacts = _bundle_artifacts(connection, fixture.basis["project_id"])
            artifact = next(iter(artifacts.values()))
            payload = artifact.payload
            entry = dict(task_id=payload["task"]["task_id"],
                completion_cycle_id=payload["completion_cycle_id"], cycle_ordinal=payload["cycle_ordinal"],
                bundle_state="native", bundle_id=payload["bundle_id"],
                bundle_file="bundles/" + payload["bundle_id"] + ".json",
                bundle_digest=artifact.bundle_digest, file_digest=artifact.file_digest,
                sealed_at=payload["sealed_at"], bundle_format_version=4)
            self.assertEqual(reader._validate_bundle_payload(payload, entry=entry,
                project_id=fixture.basis["project_id"], bundle_format_version=4,
                maximum_source_schema_version=31), payload)


if __name__ == "__main__":
    unittest.main()
