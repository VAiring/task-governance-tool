import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY_ROOT = ROOT / "docs" / "history"
CAPTURE_M19_1 = "1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1"
CAPTURE_M19_2 = "cbf75372617e90ca0b54746ae27f24a4e67cb292"
CAPTURE_M19_PUBLICATION = "a9b80ce177a6dead10d51a070b76ff01f7af0294"
CAPTURE_M20_BASELINE = "43c91d5987b0c35c66f834789aea782e98dcaff7"
M20_RETIREMENT_ANCHOR = "dd662a861f3a224bc17f021e0dc0ed6f20be6bc1"
M20_HISTORY_SHA256 = "1164c65d0270aeef35311a061064c23cf14c1726ad647568598e0fcb2718405d"
M20_COMPLETION = "e5167e2d9d54493900b9d88672f1e53304cfa5b1"


ARCHIVES = {
    "specification.md": {
        "source": "docs/specification.md",
        "replacement": "docs/specification.md",
        "capture": CAPTURE_M19_1,
        "body_size": 265_213,
        "body_sha256": "539c46baa30e76d57f673aaa1380fe4aa1134c9a62ba38b0068125e26b9551c1",
        "archive_sha256": "88e07c7bf42e8d74e6ad0c6ea122f79f3c51a3fca90a0ff8bc09cf0c362effce",
    },
    "design.md": {
        "source": "docs/design.md",
        "replacement": "docs/design.md",
        "capture": CAPTURE_M19_1,
        "body_size": 232_101,
        "body_sha256": "f20a36fb9f94aedab1c1ccb51c4b77b356c2608f047df911e10b9bd7127456e9",
        "archive_sha256": "05d4c1943401d7a78748a0e36fcc446b79b76b23a128445c955c76dc69d18b64",
    },
    "implementation-roadmap.md": {
        "source": "docs/implementation-roadmap.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_2,
        "body_size": 185_236,
        "body_sha256": "c964e94d5a608834dd5b267700b42470c2b27cc056f064d23886856e8cae5781",
        "archive_sha256": "360fba978b79bac78187cc129fa266ce64be212e58422128c0e6afbb5c1166d2",
    },
    "plan.md": {
        "source": "plan.md",
        "replacement": "plan.md",
        "capture": CAPTURE_M19_2,
        "body_size": 97_546,
        "body_sha256": "0f5f2a3300065c9dd3c8f590df72f504a9e59d90b86dad59aed3807eaa734b34",
        "archive_sha256": "beeb67cd1091c6e0b44779c07aad3724c14fe60ebf6c4527e2faf0afb46feb87",
    },
    "forward-tests/completion-commit-flow.md": {
        "source": "docs/forward-tests/completion-commit-flow.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_2,
        "body_size": 1_844,
        "body_sha256": "b2d63b8fcb870776378b6ad7d2a769a0f13aa0345387bca13abbb85ebb093893",
        "archive_sha256": "71f4e4bbbbb912ae5c7ff5da5a0c8f8d0d11a0f23a22ec4dd09f5e146dc0b72a",
    },
    "forward-tests/static-task-viewer.md": {
        "source": "docs/forward-tests/static-task-viewer.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_2,
        "body_size": 8_225,
        "body_sha256": "b2024fdaf40390a5d264d0b8f88f01e10a03d50fdbbf693266a738144f245e84",
        "archive_sha256": "4719f800c65b97817ea3856cdb162b39e0b9896ed8b6e284b4238be22af8f3ee",
    },
    "forward-tests/tg-m11-git-snapshot-completion.md": {
        "source": "docs/forward-tests/tg-m11-git-snapshot-completion.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_2,
        "body_size": 3_615,
        "body_sha256": "3011aab18cffb359b26c7e4d3b558416a5c5a9a3bf87bc5b721e07e65455965f",
        "archive_sha256": "9cb2bdce8158c022a5524038e13a8ec43a60da02877336606a3707cefa35d7dc",
    },
    "forward-tests/tg-m12-local-handoff.md": {
        "source": "docs/forward-tests/tg-m12-local-handoff.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_2,
        "body_size": 3_971,
        "body_sha256": "fa74d2693c51b9e0e989e31ea60d743e3252e382918210c3113e187a2e49fd7b",
        "archive_sha256": "4ff8d14a132a602041a8bd8acf077889c6f417ec877330964285b8e5df8016c8",
    },
    "forward-tests/tg-m12-task-contract.md": {
        "source": "docs/forward-tests/tg-m12-task-contract.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_2,
        "body_size": 2_717,
        "body_sha256": "7640535a0e395b2f4117b444815febba3c2298d6d3fe82348b659aa643560c67",
        "archive_sha256": "f546b4171f6c88eeefc1a1b2a20fb680a753be0775818c3af0c0b09a983489b5",
    },
    "forward-tests/tg-m16-loop-discipline.md": {
        "source": "docs/forward-tests/tg-m16-loop-discipline.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_2,
        "body_size": 4_628,
        "body_sha256": "25e198429a089291d6fd13ca989c4023830012b0cd5d976b0d815959275c18b7",
        "archive_sha256": "ace9e727961d78574f36e6e67a02a26938c0e48288bedcb39c686a645ebfde14",
    },
    "forward-tests/tg-m18-completion-history.md": {
        "source": "docs/forward-tests/tg-m18-completion-history.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_2,
        "body_size": 2_653,
        "body_sha256": "6cfb9b56e35248a0c2ce8501bd7d8ac3b60dfae2916dac0124a22d17adebb68c",
        "archive_sha256": "5f8820ab3a6c5d1cac8bf78c9e277b6a7898b787ce2bca95bd22c0b6e342c570",
    },
    "forward-tests/tg-m8-resume-and-completion.md": {
        "source": "docs/forward-tests/tg-m8-resume-and-completion.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_2,
        "body_size": 3_341,
        "body_sha256": "75a81eb877fda70eabf11deef31fad9e319cb94c80787cead44fc21ca6fc96d6",
        "archive_sha256": "d48b9c446d67b6e2de0735a873f2335ec533bb0f30c0a5f5b9d317fa578984ff",
    },
    "release-publication/specification.md": {
        "source": "docs/specification.md",
        "replacement": "docs/specification.md",
        "capture": CAPTURE_M19_PUBLICATION,
        "index_heading": "Publication capture of `docs/specification.md`",
        "body_size": 103_742,
        "body_sha256": "1f522a3c6918d92af2d753f8a477da50ba1caa3f530dc5a44ace847617afd2d9",
        "archive_sha256": "3dc98bba47256d3d7e0b5679a22d90736780f508bb206735ebfde194f3e12e28",
    },
    "release-publication/design.md": {
        "source": "docs/design.md",
        "replacement": "docs/design.md",
        "capture": CAPTURE_M19_PUBLICATION,
        "index_heading": "Publication capture of `docs/design.md`",
        "body_size": 85_484,
        "body_sha256": "71edc6b16a2df71ca36c92e763722f947f49fbfe94cf0328757a6342f76f89cb",
        "archive_sha256": "3a5bd780f0b766b63adcfdb4a1292d06d547adc9884be9d24fd1df0d600849d9",
    },
    "release-publication/implementation-roadmap.md": {
        "source": "docs/implementation-roadmap.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M19_PUBLICATION,
        "index_heading": "Publication capture of `docs/implementation-roadmap.md`",
        "body_size": 38_328,
        "body_sha256": "93cc7f8ca8c865c734c40bb076b35be45df12500945b379615fa93ed2f822a67",
        "archive_sha256": "f22639b3b64860782fedf66bfd67f709bc0382ebeb02b6173ed53a880ae1c7d8",
    },
    "release-publication/plan.md": {
        "source": "plan.md",
        "replacement": "plan.md",
        "capture": CAPTURE_M19_PUBLICATION,
        "index_heading": "Publication capture of `plan.md`",
        "body_size": 11_036,
        "body_sha256": "ad35b396fdb29c66eae6405b9bdde9932e6cc5b38f8438233a8078539cb3886e",
        "archive_sha256": "54a57bd6abf2511054b9bddc7b59fc3b7f0d324880e7084fbd36ae56123afe39",
    },
    "release-publication/release-install.md": {
        "source": "docs/release-install.md",
        "replacement": "docs/release-install.md",
        "capture": CAPTURE_M19_PUBLICATION,
        "index_heading": "Publication capture of `docs/release-install.md`",
        "body_size": 21_372,
        "body_sha256": "ea799a0f9538fdc08849cc0f39ad79ddb311cd1eca8c18442c34c956aa4118ba",
        "archive_sha256": "24e43ff9129299d529658ab7267e986bf9f773aa491661e956e33292bd587a03",
    },
    "post-release/implementation-roadmap.md": {
        "source": "docs/implementation-roadmap.md",
        "replacement": "docs/implementation-roadmap.md",
        "capture": CAPTURE_M20_BASELINE,
        "index_heading": (
            "Post-release capture of `docs/implementation-roadmap.md`"
        ),
        "body_size": 16_980,
        "body_sha256": "96e4bf4d4827e2797a12928b6297b1afd83d447f716cfd777b5f15bdaaaf5537",
        "archive_sha256": "d3ff9d64f1d1cd85b859082822a1d82c85b19f2722339dac000e2adc4112821e",
    },
}

STUDY_HISTORIES = {
    "m20-operational-baseline.md",
    "m20s-task-decomposition.md",
}


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


class DocumentHistoryTests(unittest.TestCase):
    def test_m21_design_and_m20s_closed_decision_are_indexed(self):
        specification = (ROOT / "docs" / "specification.md").read_text(
            encoding="utf-8-sig"
        )
        design = (ROOT / "docs" / "design.md").read_text(encoding="utf-8-sig")
        roadmap = (ROOT / "docs" / "implementation-roadmap.md").read_text(
            encoding="utf-8-sig"
        )
        plan = (ROOT / "plan.md").read_text(encoding="utf-8-sig")

        self.assertIn(
            "| TG-M20.5 | `tg_task_f6c19be1c10ad3ab` | "
            f"`{M20_COMPLETION}` |",
            roadmap,
        )
        self.assertIn("TG-M21.1 Verification Receipt Design Contract", roadmap)
        self.assertIn("Task: `tg_task_cf03643f368c2c1a`", roadmap)
        self.assertIn("TG-M20S Closed Successor Observation", roadmap)
        self.assertIn("`tg_task_ddfbf721eced8c58`", roadmap)
        self.assertIn("`tg_task_e591f30d546ba69e`", roadmap)
        self.assertIn("E=2,Q=2,U=2", specification + design + roadmap + plan)
        self.assertIn(
            "history/v0.10.0/m20s-task-decomposition.md",
            specification + design + roadmap + plan,
        )
        self.assertNotIn(
            "Approved Temporary TG-M20S",
            specification + design + roadmap + plan,
        )
        self.assertIn("TG-M21.1A, TG-M21.1B, TG-M21.2, and TG-M21.3", roadmap)
        self.assertIn("approved and registered", roadmap)

        for document in (specification, design):
            self.assertIn("Approved But Inactive TG-M21 Verification Receipt", document)
            self.assertIn("schema v16", document)
            self.assertTrue("20 command" in document or "20-leaf" in document)
            self.assertIn("verification receipt add", document)

        for marker in (
            "command_label",
            "result",
            "source_revision",
            "duration",
            "scope_coverage",
            "verification_receipt_required",
            "verification_receipt_blocking",
            "expected-target-generation",
            "verification_basis_version",
            "verification_expectation_digest",
            "verification_receipt_id",
            "no per-Task Receipt query",
            "pass/full",
            "TG-M21.2 atomic vertical activation",
            "TG-M21.3 integrated acceptance",
        ):
            self.assertIn(marker, specification + design + plan)

        self.assertIn(
            "The implementation sequence is approved and registered",
            plan,
        )
        m21_section = plan[plan.index("### TG-M21.1") :]
        for task_id in (
            "tg_task_a6f5ec3147440e53",
            "tg_task_8e30cf88c9018824",
            "tg_task_2f6fd712dd83f250",
            "tg_task_a42cb5d0383980bd",
        ):
            self.assertIn(task_id, m21_section)

        storage = (
            ROOT
            / "task-governance-tool"
            / "scripts"
            / "task_governance_tool"
            / "storage.py"
        ).read_text(encoding="utf-8")
        skill = (ROOT / "task-governance-tool" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("SCHEMA_VERSION = 16", storage)
        self.assertNotIn("verification receipt add", skill)
        self.assertFalse(
            (
                ROOT
                / "task-governance-tool"
                / "scripts"
                / "task_governance_tool"
                / "verification_receipts.py"
            ).exists()
        )

    def test_archives_are_fixed_exact_captures_with_non_authority_banners(self):
        version_root = HISTORY_ROOT / "v0.10.0"
        for relative, expected in ARCHIVES.items():
            with self.subTest(relative=relative):
                data = (version_root / relative).read_bytes()
                self.assertEqual(
                    hashlib.sha256(data).hexdigest(),
                    expected["archive_sha256"],
                )
                body = data[-expected["body_size"] :]
                self.assertEqual(
                    hashlib.sha256(body).hexdigest(),
                    expected["body_sha256"],
                )
                captured = subprocess.run(
                    [
                        "git",
                        "show",
                        f"{expected['capture']}:{expected['source']}",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(captured.returncode, 0)
                self.assertEqual(body, captured.stdout)
                banner = data[: -expected["body_size"]].decode("utf-8")
                self.assertIn("NON-AUTHORITATIVE HISTORY", banner)
                self.assertIn(expected["source"], banner)
                self.assertIn(expected["capture"], banner)
                self.assertIn(expected["replacement"], banner)

    def test_history_index_preserves_old_prefix_and_indexes_every_capture(self):
        index_bytes = (HISTORY_ROOT / "README.md").read_bytes()
        captured_prefix = index_bytes[:5477]
        self.assertEqual(
            git_blob_sha1(captured_prefix),
            "3d90c4da97e65b895e6a904defce171ae84b6b62",
        )
        index = index_bytes.decode("utf-8")
        self.assertIn("not current", index)
        self.assertIn("append-only", index)
        for relative, expected in ARCHIVES.items():
            with self.subTest(relative=relative):
                heading = expected.get(
                    "index_heading",
                    f"`{expected['source']}`",
                )
                sections = re.findall(
                    rf"^### {re.escape(heading)}\n(.*?)(?=^### |^## |\Z)",
                    index,
                    flags=re.MULTILINE | re.DOTALL,
                )
                self.assertEqual(len(sections), 1)
                section = sections[0]
                self.assertIn(expected["capture"], section)
                self.assertIn(expected["replacement"], section)
                self.assertEqual(section.count(f"](v0.10.0/{relative})"), 1)

        indexed_archives = set(ARCHIVES) | STUDY_HISTORIES
        actual_archives = {
            path.relative_to(HISTORY_ROOT / "v0.10.0").as_posix()
            for path in (HISTORY_ROOT / "v0.10.0").rglob("*.md")
        }
        self.assertEqual(actual_archives, indexed_archives)

    def test_m20_study_is_retired_to_no_rerun_tombstones(self):
        receipt_root = ROOT / "fixtures" / "m20"
        receipt_paths = sorted(receipt_root.glob("*.json"))
        self.assertEqual(
            [path.name for path in receipt_paths],
            [
                "m20.2-collection-receipt.json",
                "m20.3-collection-receipt.json",
                "m20.4-collection-receipt.json",
            ],
        )
        for path in receipt_paths:
            with self.subTest(receipt=path.name):
                receipt = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(receipt["status"], "closed")
                self.assertEqual(receipt["artifact_status"], "retired")
                self.assertEqual(
                    receipt["retirement_revision"],
                    M20_RETIREMENT_ANCHOR,
                )

        anchor = subprocess.run(
            ["git", "cat-file", "-e", f"{M20_RETIREMENT_ANCHOR}^{{commit}}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(anchor.returncode, 0)
        history = HISTORY_ROOT / "v0.10.0" / "m20-operational-baseline.md"
        self.assertEqual(
            hashlib.sha256(history.read_bytes()).hexdigest(),
            M20_HISTORY_SHA256,
        )

        self.assertFalse((ROOT / "dist" / "m20").exists())
        self.assertFalse((ROOT / "dist" / "M20_TEMPORARY_CONTEXT.md").exists())
        self.assertFalse((ROOT / "dist" / "m20.4-trials-800ed1").exists())
        self.assertFalse(any((ROOT / "tools").glob("m20_*.py")))
        self.assertFalse(any((ROOT / "tests").glob("test_m20_*.py")))
        self.assertFalse(any((ROOT / "tools" / "__pycache__").glob("m20_*.pyc")))
        self.assertFalse(
            any((ROOT / "tests" / "__pycache__").glob("test_m20_*.pyc"))
        )

    def test_m20s_study_is_closed_and_temporary_assets_are_removed(self):
        receipt_root = ROOT / "fixtures" / "m20s"
        receipt_paths = sorted(receipt_root.glob("*.json"))
        self.assertEqual(
            [path.name for path in receipt_paths],
            ["decomposition-collection-receipt.json"],
        )
        receipt = json.loads(receipt_paths[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], "m20s-decomposition-collection-receipt-v1")
        self.assertEqual(receipt["status"], "closed")
        self.assertEqual(receipt["artifact_status"], "retained")
        self.assertIsNone(receipt["retirement_revision"])
        self.assertEqual(receipt["attempted_pairs"], 1)
        self.assertEqual(receipt["attempted_arms"], 2)
        self.assertEqual(receipt["record_count"], 4)
        self.assertEqual(receipt["eligible_pairs"], 2)
        self.assertEqual(receipt["qualifying_pairs"], 2)
        self.assertEqual(receipt["unavailable_pairs"], 2)
        self.assertEqual(receipt["decision"], "proceed_to_design")

        history = (
            HISTORY_ROOT / "v0.10.0" / "m20s-task-decomposition.md"
        ).read_text(encoding="utf-8")
        for marker in (
            "NON-AUTHORITATIVE STUDY HISTORY",
            "sp_user_expansion_alternate",
            "E=2",
            "Q=2",
            "U=2",
            "proceed_to_design",
            "never launched",
            "separately approved Tier 2",
            "insufficient material to reconstruct, rerun, or rescore",
        ):
            self.assertIn(marker, history)

        self.assertFalse((ROOT / "dist" / "m20s").exists())
        for retired in (
            ROOT / "tools" / "m20s_decomposition_harness.py",
            ROOT / "tests" / "test_m20s_decomposition_harness.py",
            ROOT / "fixtures" / "m20s" / "protocol-v1.json",
            ROOT / "fixtures" / "m20s" / "episode-plan-v1.json",
        ):
            self.assertFalse(retired.exists())
        self.assertFalse(
            any(
                (ROOT / "tests" / "__pycache__").glob(
                    "test_m20s_decomposition_harness*.pyc"
                )
            )
        )

    def test_governing_and_historical_markdown_links_resolve(self):
        documents = [
            ROOT / "AGENTS.md",
            ROOT / "plan.md",
            ROOT / "docs" / "specification.md",
            ROOT / "docs" / "design.md",
            ROOT / "docs" / "implementation-roadmap.md",
            HISTORY_ROOT / "README.md",
            HISTORY_ROOT / "v0.10.0" / "m20-operational-baseline.md",
            HISTORY_ROOT / "v0.10.0" / "m20s-task-decomposition.md",
        ]
        link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
        checked = 0
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for target in link_pattern.findall(text):
                path_text = target.split("#", 1)[0]
                if not path_text or "://" in path_text or path_text.startswith("mailto:"):
                    continue
                checked += 1
                resolved = (document.parent / path_text).resolve()
                self.assertTrue(
                    resolved.is_file(),
                    f"{document.relative_to(ROOT)} -> {target}",
                )

        for relative, expected in ARCHIVES.items():
            document = HISTORY_ROOT / "v0.10.0" / relative
            data = document.read_bytes()
            banner = data[: -expected["body_size"]].decode("utf-8")
            for target in link_pattern.findall(banner):
                path_text = target.split("#", 1)[0]
                if not path_text or "://" in path_text:
                    continue
                checked += 1
                resolved = (document.parent / path_text).resolve()
                self.assertTrue(
                    resolved.is_file(),
                    f"{document.relative_to(ROOT)} banner -> {target}",
                )
        self.assertGreaterEqual(checked, 40)

    def test_active_authority_is_concise_and_forward_evidence_is_historical(self):
        roadmap = (ROOT / "docs" / "implementation-roadmap.md").read_text(
            encoding="utf-8-sig"
        )
        plan = (ROOT / "plan.md").read_text(encoding="utf-8-sig")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertLess(len(roadmap.splitlines()), 1_500)
        self.assertLess(len(plan.splitlines()), 500)
        self.assertNotIn("## Milestone TG-M1:", roadmap)
        self.assertNotIn("## Implementation Execution Status", plan)
        self.assertFalse(any((ROOT / "docs" / "forward-tests").glob("*.md")))
        self.assertIn("docs/history/README.md", agents)
        self.assertIn("non-authoritative", agents)
        self.assertIn("docs/history/README.md", roadmap)
        self.assertIn("docs/history/README.md", plan)

    def test_post_release_authority_is_synchronized_and_plan_avoids_handoff_mirror(
        self,
    ):
        active_paths = (
            ROOT / "docs" / "specification.md",
            ROOT / "docs" / "design.md",
            ROOT / "docs" / "implementation-roadmap.md",
            ROOT / "plan.md",
            ROOT / "README.md",
            ROOT / "docs" / "release-install.md",
        )
        active = {
            path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8-sig")
            for path in active_paths
        }
        for relative, text in active.items():
            with self.subTest(relative=relative):
                self.assertIn(CAPTURE_M19_PUBLICATION, text)
                self.assertIn("362617903", text)
                self.assertIn("prerelease", text.lower())

        combined = "\n".join(active.values())
        for stale in (
            "TG-M19.6A is the current corrective unit",
            "reacceptance required after TG-M19.6A",
            "Apache-2.0 is selected but not applied until",
            "selected but cannot be applied until",
            "Before publication, run at least",
        ):
            self.assertNotIn(stale, combined)

        roadmap = active["docs/implementation-roadmap.md"]
        for task_id in (
            "tg_task_e452e6eb7dcf0e08",
            "tg_task_d0e8ac1287bd07a4",
            "tg_task_704ecd1d1e2f7552",
            "tg_task_0f76a52915987511",
        ):
            self.assertIn(task_id, roadmap)

        m20_units = (
            ("tg_task_43fd4b96c9ca92a1", 10, 2),
            ("tg_task_2885725486bec173", 20, 2),
            ("tg_task_8efb270f74360308", 30, 1),
            ("tg_task_787f976a5e9daa7e", 40, 1),
            ("tg_task_f6c19be1c10ad3ab", 50, 2),
        )
        positions = []
        for task_id, lane_order, review_tier in m20_units:
            positions.append(roadmap.index(task_id))
            self.assertRegex(
                roadmap,
                (
                    rf"Task: `{task_id}`\n"
                    rf"Lane/order: `TG-M20-OPERATIONAL-BASELINE` / "
                    rf"{lane_order}\n"
                    rf"Review tier: Tier {review_tier}\n"
                ),
            )
        self.assertEqual(positions, sorted(positions))

        normalized_combined = " ".join(combined.split())
        self.assertNotIn("TG-M19.14 is active", normalized_combined)
        design = active["docs/design.md"]
        self.assertIn("Completed TG-M20 Study Boundary", design)
        self.assertIn("history/v0.10.0/m20-operational-baseline.md", design)
        for retired_contract_marker in (
            "m20-operational-observation-v1",
            "m20-observation-v1\\0",
            "There is no rerun, replacement subject",
        ):
            self.assertNotIn(retired_contract_marker, design)

        m20_history = (
            HISTORY_ROOT / "v0.10.0" / "m20-operational-baseline.md"
        ).read_text(encoding="utf-8")
        for synthesis_marker in (
            "NON-AUTHORITATIVE STUDY HISTORY",
            CAPTURE_M20_BASELINE,
            "machine_observed",
            "historically_reconstructed",
            "observer_attested",
            "vp_cli_contract",
            "sp_handoff_control",
            "E=1`, `Q=1`, and `U=3",
            "TG-M21 Verification Receipts | `proceed_to_design`",
            "Skill-only proportional-verification guardrail | `observe_more`",
            "Bounded user-approved Task decomposition | `observe_more`",
        ):
            self.assertIn(synthesis_marker, m20_history)

        plan = active["plan.md"]
        handoff_scan = combined + "\n" + (ROOT / "AGENTS.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("## Pending Local Handoffs", handoff_scan)
        self.assertNotRegex(handoff_scan, r"tg_handoff_[0-9a-f]{16}")
        self.assertNotRegex(
            handoff_scan,
            (
                r"(?i)(?:reports|contains|has)[^.]{0,120}\b"
                r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
                r"[^.]{0,80}(?:pending[_ -]?handoff|handoff records?)"
            ),
        )
        normalized_plan = " ".join(plan.split())
        self.assertRegex(
            normalized_plan,
            r"Task database[^.]{0,200}handoff",
        )
        self.assertRegex(
            normalized_plan,
            r"does not mirror[^.]{0,100}handoff",
        )
        for candidate in (
            "project-profile detection",
            "Verification Receipt design",
            "dependency graphs",
            "default-browser launch",
            "event-history or current/list pagination",
            "once-daily GitHub update check",
            "versioned local intake and transport contract",
        ):
            self.assertIn(candidate, normalized_plan)

        specification = " ".join(
            active["docs/specification.md"].lower().split()
        )
        design = " ".join(design.lower().split())
        self.assertIn(
            "current-generation changes-requested receipt",
            specification,
        )
        self.assertIn(
            "unresolved high/medium finding from any recorded generation",
            specification,
        )
        self.assertIn("across all task receipts", design)

        release_body = (ROOT / "docs" / "releases" / "v0.10.0.md").read_bytes()
        self.assertEqual(
            hashlib.sha256(release_body).hexdigest(),
            "aaa118a3fbbb261ec6a24f7a80f50f161e606a86857f99e17f957f34ba044a03",
        )

    def test_completion_index_keeps_full_revisions_and_exact_older_blocker(self):
        roadmap = (ROOT / "docs" / "implementation-roadmap.md").read_text(
            encoding="utf-8-sig"
        )
        expected_rows = {
            "TG-M1.1-TG-M4.3 MVP": (
                "pre-Task-DB",
                "86fccf389e6e16c6ba2fdcaf5acd39d32c26b911",
            ),
            "TG-M4.O1 and TG-M5.O1-TG-M5.O6 follow-ups": (
                "pre-Task-DB",
                "b7b41ad458f59f9ad2ae8dcb0a7c56493d24a6ab",
            ),
            "TG-M6": (
                "tg_task_306c6ac4199122fb",
                "f017ee228d435d892fb7136c5e79b3063320fac5",
            ),
            "TG-M7": (
                "tg_task_f18ca3ea0982df9a",
                "a9843337666c55a6866edf616b4b3d47af76426a",
            ),
            "TG-M8": (
                "tg_task_5950b5447de43993",
                "0af9d1b57d648c801b021f5a6fff5779b04b0fb8",
            ),
            "TG-M9": (
                "tg_task_ca49db07778db0e4",
                "57583dba5dc1c04fe5f167602a4f89f1dd464639",
            ),
            "TG-M11": (
                "tg_task_b62ad41d367d5e01",
                "b46a188f3b8df3d9702f99b0eb190f04923bdc1f",
            ),
            "TG-M12.1, TG-M12.2, TG-M12.O1, and TG-M12.O2": (
                "tg_task_a23869622600a71c",
                "143090ffb5e140804e27e350769c319e3be6a237",
            ),
            "TG-M13": (
                "tg_task_2302ce786a28d1b0",
                "a024c07dc9d587d62ecf3705a409ac62806ce11b",
            ),
            "TG-M14": (
                "tg_task_c30e126d19b2a0e2",
                "b1ce82f1aeffc226ba827231228727ee5a2b35c5",
            ),
            "TG-M15 through TG-M15.6": (
                "tg_task_1c4ab208be113c8a",
                "1927b65fc2437ec799a6591ec1db9f7cb4373fe8",
            ),
            "TG-M16": (
                "tg_task_1a2f5af057e45ef1",
                "e0b109d67074015b5494757fa64cf7524ebaa92d",
            ),
            "TG-M17": (
                "tg_task_c8054d1c57087956",
                "29ac34d8c6e96c2cf091e504abe05b5485a54dd0",
            ),
            "TG-M18": (
                "tg_task_fa3a57ae3089e3fc",
                "b0df647d9caf693afc0ff46aecf71a2c4739c864",
            ),
            "TG-M19.0": (
                "tg_task_2b95de205e3f92e3",
                "1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1",
            ),
            "TG-M19.1": (
                "tg_task_ba59e260cc2c58a6",
                "cbf75372617e90ca0b54746ae27f24a4e67cb292",
            ),
            "TG-M19.2": (
                "tg_task_20fd398141755a65",
                "2af0382c54615640fbd8475a59f374b1b71804c4",
            ),
            "TG-M19.3": (
                "tg_task_b71ac20177aae41a",
                "4040e923cfdbd8b3f65d8883187a57578d64c092",
            ),
            "TG-M19.4": (
                "tg_task_7cc967fc224440cb",
                "639bc74adfd1f5e15996d1416bd064f1b9303edc",
            ),
            "TG-M19.5": (
                "tg_task_bd93525dc71f4dcd",
                "fe9fdafd207cab9d0966785f4b340fe3224397fa",
            ),
            "TG-M19.6A": (
                "tg_task_2fc57c401dd2855d",
                "5ce64e1eae239d78e185d68349784cfe0c069f00",
            ),
            "TG-M19.6B": (
                "tg_task_cacf382b827c58d5",
                CAPTURE_M19_PUBLICATION,
            ),
            "TG-M19.6": (
                "tg_task_67a3f3e73b913bfb",
                CAPTURE_M19_PUBLICATION,
            ),
            "TG-M19.7": (
                "tg_task_5b8796de20a32d39",
                "github-actions-run:VAiring/task-governance-tool:"
                "30561916953:1",
            ),
            "TG-M19.8": (
                "tg_task_79791addafcf0e00",
                CAPTURE_M19_PUBLICATION,
            ),
            "TG-M19.9": (
                "tg_task_418792bf98f211af",
                "sha256:"
                "ed79ea10ff9e07dd44f86c6ef9e3979bd296c1fc731b06148d2f01f70ae763ac",
            ),
            "TG-M19.10": (
                "tg_task_9807bdc4ddc5ba37",
                "github-release:VAiring/task-governance-tool:362617903",
            ),
            "TG-M19.11": (
                "tg_task_e452e6eb7dcf0e08",
                "f5d7ed4706eac41c422690f16e5791893fdb1989",
            ),
            "TG-M19.12": (
                "tg_task_d0e8ac1287bd07a4",
                "f3f1945916f99e32b66c9bb15d3a673dbff61c5a",
            ),
            "TG-M19.13": (
                "tg_task_704ecd1d1e2f7552",
                "27e7ef08c70c1434b9aac8474b3006dbbc6ec3b8",
            ),
            "TG-M19.14": (
                "tg_task_0f76a52915987511",
                CAPTURE_M20_BASELINE,
            ),
        }
        lines = roadmap.splitlines()
        for scope, (task_id, revision) in expected_rows.items():
            with self.subTest(scope=scope):
                matches = [
                    line for line in lines if line.startswith(f"| {scope} |")
                ]
                self.assertEqual(len(matches), 1)
                row = matches[0]
                task_cell = (
                    task_id
                    if task_id == "pre-Task-DB"
                    else f"`{task_id}`"
                )
                self.assertIn(f"| {task_cell} |", row)
                self.assertIn(revision, row)

        normalized = " ".join(roadmap.split())
        self.assertIn("Task: `tg_task_1f7503aca5e32cdc`", roadmap)
        self.assertIn(
            "Requires TG-M12.2, a separately approved Issue Skill intake "
            "contract, governing permission updates, and explicit user "
            "approval of the integration boundary.",
            normalized,
        )
        for durable_m12_boundary in (
            "fixed bounded retry",
            "bounded receiver acceptance receipt",
            "no public command name is active or invocable",
            "must come from the separately approved receiver contract",
            "zero additional LLM decisions",
            "one receiver item under concurrent claim/acknowledgement",
            "no local-withdrawn plus receiver-accepted race",
        ):
            self.assertIn(durable_m12_boundary, normalized)


if __name__ == "__main__":
    unittest.main()
