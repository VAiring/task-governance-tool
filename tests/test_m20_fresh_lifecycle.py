import copy
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

from tools import m20_fresh_lifecycle
from tools.m20_fresh_lifecycle import (
    FreshCollectionLifecycle,
    check_fresh_collection,
)
from tools.m20_observation import (
    M20ObservationError,
    _excluded_rows,
    canonical_json_bytes,
    derive_inventory,
    load_protocol,
)


class FreshLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = load_protocol(ROOT)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        fixture_root = self.root / "fixtures" / "m20"
        fixture_root.mkdir(parents=True)
        for name in ("protocol-v1.json", "m20.4-episode-plan-v1.json"):
            shutil.copyfile(ROOT / "fixtures" / "m20" / name, fixture_root / name)
        (self.root / ".gitignore").write_text("/dist/\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )

    def tearDown(self):
        self.temp.cleanup()

    def assert_m20_error(self, callback, code):
        with self.assertRaises(M20ObservationError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def rows_by_attempt(self, unit):
        result = {}
        for row in derive_inventory(self.protocol, unit):
            result.setdefault(row[2], []).append(row)
        return result

    def finish_all_excluded(self, lifecycle, unit):
        rows = self.rows_by_attempt(unit)
        for attempt_id in lifecycle.expected_attempts():
            if unit == "M20.4" and attempt_id.endswith(".bounded.01"):
                continue
            if unit == "M20.4" and not attempt_id.startswith("sp_handoff_control"):
                scenario = rows[attempt_id][0][1]
                bounded = f"{scenario}.bounded.01"
                lifecycle.start_many((attempt_id, bounded))
                for paired_id in (attempt_id, bounded):
                    lifecycle.claim(paired_id)
                    lifecycle.finish(
                        paired_id,
                        _excluded_rows(
                            self.protocol,
                            rows[paired_id],
                            "source_missing",
                        ),
                    )
            else:
                lifecycle.start(attempt_id)
                lifecycle.claim(attempt_id)
                lifecycle.finish(
                    attempt_id,
                    _excluded_rows(
                        self.protocol,
                        rows[attempt_id],
                        "source_missing",
                    ),
                )

    def test_m20_3_finalize_writes_exact_terminal_receipt_and_checks(self):
        lifecycle = FreshCollectionLifecycle(self.root, "M20.3")
        self.assertEqual(len(lifecycle.expected_attempts()), 3)
        self.finish_all_excluded(lifecycle, "M20.3")
        receipt = lifecycle.finalize()

        self.assertEqual(receipt["schema"], "m20-collection-receipt-v1")
        self.assertEqual(receipt["unit"], "M20.3")
        self.assertEqual(receipt["status"], "closed")
        self.assertEqual(receipt["artifact_status"], "retained")
        self.assertEqual(receipt["outcome"], "collection_complete")
        self.assertEqual(receipt["record_count"], 9)
        self.assertEqual(receipt["excluded_records"], 9)
        self.assertNotIn("episode_plan_canonical_sha256", receipt)
        self.assertFalse(lifecycle.journal_path.exists())

        raw = lifecycle.receipt_path.read_bytes()
        self.assertEqual(raw, canonical_json_bytes(receipt) + b"\n")
        checked = check_fresh_collection(self.root, "M20.3")
        self.assertEqual(checked["artifact_status"], "retained")
        self.assertEqual(checked["record_count"], 9)
        self.assert_m20_error(
            lambda: lifecycle.start("vp_cli_contract.baseline.01"),
            "collection_closed",
        )

    def test_m20_4_requires_paired_start_and_binds_episode_digest(self):
        lifecycle = FreshCollectionLifecycle(self.root, "M20.4")
        self.assert_m20_error(
            lambda: lifecycle.start("sp_multi_outcome_intake.broad.01"),
            "paired_start_required",
        )
        self.finish_all_excluded(lifecycle, "M20.4")
        receipt = lifecycle.finalize()

        self.assertEqual(receipt["record_count"], 19)
        self.assertEqual(receipt["excluded_records"], 19)
        self.assertEqual(
            receipt["episode_plan_canonical_sha256"],
            m20_fresh_lifecycle.M20_4_EPISODE_PLAN_CANONICAL_SHA256,
        )
        checked = check_fresh_collection(self.root, "M20.4")
        self.assertEqual(checked["record_count"], 19)

    def test_resume_terminalizes_started_and_reducing_pair_without_relaunch(self):
        lifecycle = FreshCollectionLifecycle(self.root, "M20.4")
        pair = (
            "sp_in_scope_discovery.broad.01",
            "sp_in_scope_discovery.bounded.01",
        )
        lifecycle.start_many(pair)
        lifecycle.claim(pair[0])

        reopened = FreshCollectionLifecycle(self.root, "M20.4")
        self.assertEqual(reopened.resume_started(), tuple(sorted(pair)))
        journal = reopened._read_journal(self.protocol)
        for attempt_id in pair:
            state = journal["attempts"][attempt_id]
            self.assertEqual(state["status"], "reduced")
            self.assertEqual(
                {tuple(record["unknown_reasons"]) for record in state["records"]},
                {("source_missing",)},
            )
        self.assertEqual(reopened.resume_started(), ())
        self.assert_m20_error(
            lambda: reopened.start_many(pair),
            "attempt_already_started",
        )

    def test_two_simultaneous_claims_allow_exactly_one_reducer(self):
        attempt = "vp_cli_contract.baseline.01"
        lifecycle = FreshCollectionLifecycle(self.root, "M20.3")
        lifecycle.start(attempt)
        barrier = threading.Barrier(3)

        def claim_once():
            contender = FreshCollectionLifecycle(self.root, "M20.3")
            barrier.wait()
            try:
                contender.claim(attempt)
            except M20ObservationError as error:
                return error.code
            return "claimed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(executor.submit(claim_once) for _ in range(2))
            barrier.wait()
            results = tuple(future.result(timeout=10) for future in futures)

        self.assertEqual(results.count("claimed"), 1)
        self.assertEqual(
            len(
                [
                    result
                    for result in results
                    if result in {"attempt_not_started", "collection_busy"}
                ]
            ),
            1,
        )
        journal = lifecycle._read_journal(self.protocol)
        self.assertEqual(journal["attempts"][attempt]["status"], "reducing")

    def test_finish_accepts_only_a_claimed_reduction(self):
        lifecycle = FreshCollectionLifecycle(self.root, "M20.3")
        rows = self.rows_by_attempt("M20.3")
        attempt = "vp_cli_contract.baseline.01"
        records = _excluded_rows(
            self.protocol,
            rows[attempt],
            "source_missing",
        )
        lifecycle.start(attempt)
        self.assert_m20_error(
            lambda: lifecycle.finish(attempt, records),
            "attempt_not_started",
        )
        lifecycle.claim(attempt)
        self.assertEqual(len(lifecycle.finish(attempt, records)), 3)

    def test_finish_replaces_foreign_row_set_with_exact_excluded_rows(self):
        lifecycle = FreshCollectionLifecycle(self.root, "M20.3")
        rows = self.rows_by_attempt("M20.3")
        intended = "vp_cli_contract.baseline.01"
        foreign = "vp_state_transition.baseline.01"
        lifecycle.start(intended)
        lifecycle.claim(intended)
        retained = lifecycle.finish(
            intended,
            _excluded_rows(
                self.protocol,
                rows[foreign],
                "source_missing",
            ),
        )
        self.assertEqual(len(retained), 3)
        self.assertEqual({record["trial_id"] for record in retained}, {intended})
        self.assertEqual(
            {tuple(record["unknown_reasons"]) for record in retained},
            {("source_drift",)},
        )

    def test_finalize_requires_complete_attempts_and_absent_raw_control(self):
        lifecycle = FreshCollectionLifecycle(self.root, "M20.3")
        attempt = "vp_cli_contract.baseline.01"
        lifecycle.start(attempt)
        self.assert_m20_error(lifecycle.finalize, "collection_incomplete")

        lifecycle.resume_started()
        remaining = set(lifecycle.expected_attempts()) - {attempt}
        rows = self.rows_by_attempt("M20.3")
        for attempt_id in remaining:
            lifecycle.start(attempt_id)
            lifecycle.claim(attempt_id)
            lifecycle.finish(
                attempt_id,
                _excluded_rows(
                    self.protocol,
                    rows[attempt_id],
                    "source_missing",
                ),
            )
        lifecycle.raw_root.mkdir(parents=True)
        (lifecycle.raw_root / "private-source").write_text(
            "not retained", encoding="utf-8"
        )
        self.assert_m20_error(lifecycle.finalize, "raw_material_present")
        shutil.rmtree(lifecycle.raw_root)
        lifecycle.control_root.mkdir(parents=True)
        self.assert_m20_error(lifecycle.finalize, "raw_material_present")
        shutil.rmtree(lifecycle.control_root)
        self.assertEqual(lifecycle.finalize()["record_count"], 9)

    def test_finalize_requires_external_session_sources_to_be_absent(self):
        lifecycle = FreshCollectionLifecycle(self.root, "M20.3")
        self.finish_all_excluded(lifecycle, "M20.3")
        with tempfile.TemporaryDirectory() as external_temp:
            external_source = Path(external_temp).resolve() / "trial-source"
            external_source.mkdir()
            self.assert_m20_error(
                lambda: lifecycle.finalize(
                    extra_source_roots=(external_source,)
                ),
                "raw_material_present",
            )
            self.assertFalse(lifecycle.receipt_path.exists())
            shutil.rmtree(external_source)
            receipt = lifecycle.finalize(
                extra_source_roots=(external_source,)
            )
        self.assertEqual(receipt["record_count"], 9)
        self.assertNotIn("source", lifecycle.receipt_path.read_text(encoding="utf-8"))

    def test_finalize_rejects_relative_and_broad_external_source_roots(self):
        lifecycle = FreshCollectionLifecycle(self.root, "M20.3")
        self.finish_all_excluded(lifecycle, "M20.3")
        filesystem_root = Path(self.root.anchor)
        candidates = (
            Path("relative-trial-source"),
            filesystem_root,
            filesystem_root / "tmp",
            self.root,
            self.root.parent,
        )
        for candidate in candidates:
            with self.subTest(candidate=str(candidate)):
                self.assert_m20_error(
                    lambda candidate=candidate: lifecycle.finalize(
                        extra_source_roots=(candidate,)
                    ),
                    "unsafe_source_root",
                )
                self.assertFalse(lifecycle.receipt_path.exists())

    def test_existing_receipt_rejects_before_protocol_or_journal_access(self):
        root = Path(tempfile.mkdtemp(dir=self.root)).resolve()
        receipt = root / "fixtures" / "m20" / "m20.3-collection-receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_bytes(b"terminal tombstone")
        lifecycle = FreshCollectionLifecycle(root, "M20.3")
        with patch.object(
            m20_fresh_lifecycle,
            "load_protocol",
            side_effect=AssertionError("protocol source touched"),
        ) as load:
            self.assert_m20_error(
                lambda: lifecycle.start("vp_cli_contract.baseline.01"),
                "collection_closed",
            )
        load.assert_not_called()

    def test_journal_identity_tampering_fails_closed(self):
        lifecycle = FreshCollectionLifecycle(self.root, "M20.4")
        pair = (
            "sp_user_expansion.broad.01",
            "sp_user_expansion.bounded.01",
        )
        lifecycle.start_many(pair)
        journal = json.loads(lifecycle.journal_path.read_text(encoding="utf-8"))
        invalid_cases = []
        wrong_unit = copy.deepcopy(journal)
        wrong_unit["unit"] = "M20.3"
        invalid_cases.append(("unit", wrong_unit))
        wrong_protocol = copy.deepcopy(journal)
        wrong_protocol["protocol_sha256"] = "0" * 64
        invalid_cases.append(("protocol", wrong_protocol))
        wrong_episode = copy.deepcopy(journal)
        wrong_episode["episode_plan_canonical_sha256"] = "0" * 64
        invalid_cases.append(("episode_plan", wrong_episode))
        wrong_status = copy.deepcopy(journal)
        wrong_status["attempts"][pair[0]]["status"] = []
        invalid_cases.append(("attempt_status", wrong_status))

        for label, candidate in invalid_cases:
            with self.subTest(field=label):
                lifecycle.journal_path.write_bytes(canonical_json_bytes(candidate))
                self.assert_m20_error(lifecycle.resume_started, "source_drift")
        lifecycle.journal_path.write_bytes(canonical_json_bytes(journal))


if __name__ == "__main__":
    unittest.main()
