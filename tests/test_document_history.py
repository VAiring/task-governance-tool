import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY_ROOT = ROOT / "docs" / "history"
CAPTURE_M19_1 = "1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1"
CAPTURE_M19_2 = "cbf75372617e90ca0b54746ae27f24a4e67cb292"


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
}


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


class DocumentHistoryTests(unittest.TestCase):
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
                self.assertEqual(
                    index.count(f"### `{expected['source']}`"),
                    1,
                )
                self.assertIn(expected["capture"], index)
                self.assertIn(f"v0.10.0/{relative}", index)

    def test_governing_and_historical_markdown_links_resolve(self):
        documents = [
            ROOT / "AGENTS.md",
            ROOT / "plan.md",
            ROOT / "docs" / "specification.md",
            ROOT / "docs" / "design.md",
            ROOT / "docs" / "implementation-roadmap.md",
            HISTORY_ROOT / "README.md",
        ]
        documents.extend(
            HISTORY_ROOT / "v0.10.0" / relative for relative in ARCHIVES
        )
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

    def test_unstarted_m19_contracts_remain_exact_and_plan_keeps_open_state(self):
        roadmap = (ROOT / "docs" / "implementation-roadmap.md").read_text(
            encoding="utf-8-sig"
        )
        start = roadmap.index(
            "### TG-M19.3 Apache-2.0 License And Attribution Boundary"
        )
        end = roadmap.index("## Roadmap Completion Criteria", start)
        unstarted_contracts = roadmap[start:end].encode("utf-8")
        self.assertEqual(len(unstarted_contracts), 19_245)
        self.assertEqual(
            hashlib.sha256(unstarted_contracts).hexdigest(),
            "30af0dc83cd6b56be39d607e11a4b60b12ef8e783ed0e091da4be2f5dd90c29d",
        )

        plan = (ROOT / "plan.md").read_text(encoding="utf-8")
        for task_id in (
            "tg_task_20fd398141755a65",
            "tg_task_b71ac20177aae41a",
            "tg_task_5b8796de20a32d39",
            "tg_task_79791addafcf0e00",
            "tg_task_9807bdc4ddc5ba37",
            "tg_task_1f7503aca5e32cdc",
        ):
            self.assertIn(task_id, plan)
        for handoff_id in (
            "tg_handoff_4001907257f93856",
            "tg_handoff_d5e1081385c3c568",
            "tg_handoff_696a19cba075d56e",
            "tg_handoff_f62d99cb033e95ee",
            "tg_handoff_c87a159f6583349d",
            "tg_handoff_3952e6681a58a101",
            "tg_handoff_d85090045f2addb1",
        ):
            self.assertIn(handoff_id, plan)
        normalized_plan = " ".join(plan.split())
        for candidate in (
            "project-profile detection",
            "verification-run recording",
            "dependency graphs",
            "default-browser launch",
            "event-history or current/list pagination",
            "once-daily GitHub update check",
            "versioned local intake and transport contract",
        ):
            self.assertIn(candidate, normalized_plan)

    def test_completion_index_keeps_full_revisions_and_exact_older_blocker(self):
        roadmap = (ROOT / "docs" / "implementation-roadmap.md").read_text(
            encoding="utf-8-sig"
        )
        expected_rows = {
            "TG-M1.1-TG-M4.3 MVP": "86fccf389e6e16c6ba2fdcaf5acd39d32c26b911",
            "TG-M4.O1 and TG-M5.O1-TG-M5.O6 follow-ups": "b7b41ad458f59f9ad2ae8dcb0a7c56493d24a6ab",
            "TG-M6": "f017ee228d435d892fb7136c5e79b3063320fac5",
            "TG-M7": "a9843337666c55a6866edf616b4b3d47af76426a",
            "TG-M8": "0af9d1b57d648c801b021f5a6fff5779b04b0fb8",
            "TG-M9": "57583dba5dc1c04fe5f167602a4f89f1dd464639",
            "TG-M11": "b46a188f3b8df3d9702f99b0eb190f04923bdc1f",
            "TG-M12.1, TG-M12.2, TG-M12.O1, and TG-M12.O2": "143090ffb5e140804e27e350769c319e3be6a237",
            "TG-M13": "a024c07dc9d587d62ecf3705a409ac62806ce11b",
            "TG-M14": "b1ce82f1aeffc226ba827231228727ee5a2b35c5",
            "TG-M15 through TG-M15.6": "1927b65fc2437ec799a6591ec1db9f7cb4373fe8",
            "TG-M16": "e0b109d67074015b5494757fa64cf7524ebaa92d",
            "TG-M17": "29ac34d8c6e96c2cf091e504abe05b5485a54dd0",
            "TG-M18": "b0df647d9caf693afc0ff46aecf71a2c4739c864",
            "TG-M19.0": "1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1",
            "TG-M19.1": "cbf75372617e90ca0b54746ae27f24a4e67cb292",
        }
        for scope, revision in expected_rows.items():
            with self.subTest(scope=scope):
                row = next(
                    line
                    for line in roadmap.splitlines()
                    if line.startswith(f"| {scope} |")
                )
                self.assertIn(revision, row)

        normalized = " ".join(roadmap.split())
        self.assertIn("Task: `tg_task_1f7503aca5e32cdc`", roadmap)
        self.assertIn(
            "Requires TG-M12.2, a separately approved Issue Skill intake "
            "contract, governing permission updates, and explicit user "
            "approval of the integration boundary.",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
