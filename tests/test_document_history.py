import hashlib
import json
import re
import subprocess
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY_ROOT = ROOT / "docs" / "history"
CAPTURE_M19_1 = "1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1"
CAPTURE_M19_2 = "cbf75372617e90ca0b54746ae27f24a4e67cb292"
CAPTURE_M19_PUBLICATION = "a9b80ce177a6dead10d51a070b76ff01f7af0294"
CAPTURE_M20_BASELINE = "43c91d5987b0c35c66f834789aea782e98dcaff7"
M20_RETIREMENT_ANCHOR = "dd662a861f3a224bc17f021e0dc0ed6f20be6bc1"
M20_HISTORY_SHA256 = "1164c65d0270aeef35311a061064c23cf14c1726ad647568598e0fcb2718405d"
M20S_RETIREMENT_ANCHOR = "0eaeb9691c0ca4c316ad541ee4dd634287f1ccef"
M20S_HISTORY_SHA256 = "9f7064d5fb74fe4e6a10c44d4e9ebb70b1b2b6a3969843d7e68775e26668432d"
PRE_M22_HISTORY_SHA256 = "426d27bb9189349439b7a2c5764bad0b2035d9cdd6dfd40b7152d45d2054728f"
M23_AUTHORITY_SPLIT_SOURCE_COMMIT = "7313483a9fd160f0ec8127b013d9f5533d2d16ab"
M23_AUTHORITY_SPLIT_ARCHIVE_SHA256 = (
    "86058642778a135dca524d7f2ae89091ba9740c6883cff9acedabcf0b1f6ba9c"
)
M23_AUTHORITY_SPLIT_SOURCE = (
    "docs/execution-contracts/tg-m23-derived-evidence.md"
)
M23_AUTHORITY_SPLIT_RELATIVE = (
    "v0.12.0/tg-m23-pre-process-safety-split.md"
)
M23_AUTHORITY_SPLIT_CAPTURE = HISTORY_ROOT / M23_AUTHORITY_SPLIT_RELATIVE
M243_DECOMPOSITION_SOURCE_COMMIT = "41bf97710d84f8a4f99274ad94efd559b74a7f22"
M243_DECOMPOSITION_ARCHIVE_SHA256 = (
    "31463eff15f38934498fc36a4fbf57be13a3a9e5bc211f45969ab82a8fa24c4d"
)
M243_DECOMPOSITION_BODY_SHA256 = (
    "4e35dc2cf24623b06912dd33f7fb4795bbeca3073d1789f5049bfd0be97b5bad"
)
M243_DECOMPOSITION_BODY_SIZE = 38_323
M243_DECOMPOSITION_SOURCE = (
    "docs/execution-contracts/tg-m24-verification-runner.md"
)
M243_DECOMPOSITION_RELATIVE = "v0.12.0/tg-m24-pre-m243-decomposition.md"
M243_DECOMPOSITION_CAPTURE = HISTORY_ROOT / M243_DECOMPOSITION_RELATIVE
POST_M24_SOURCE_COMMIT = "cd08834d023ac5967e2ae18004aff7b41277ee99"
POST_M24_CAPTURE_ROOT = (
    HISTORY_ROOT / "v0.13.0" / "post-m24-completed-execution"
)
POST_M24_CAPTURES = {
    "execution-contract-index.md": {
        "source": "docs/execution-contracts/README.md",
        "body_size": 5_638,
        "body_sha256": (
            "8d668f649d997ea70dc86eeacb16fd2a9f617acc979d16c59e7779e7f1e1bb14"
        ),
        "archive_sha256": (
            "e0e45928f6a97c7c7ad78a726794cb6c86251be6af4cde4788f63a298e6a2348"
        ),
    },
    "tg-m22-evidence-ledger.md": {
        "source": "docs/execution-contracts/tg-m22-evidence-ledger.md",
        "body_size": 108_080,
        "body_sha256": (
            "c0ee276951dde5c267ee72c829b453bb442c565964dfc04dd51e30aff00593f2"
        ),
        "archive_sha256": (
            "64576563a31216f873d45a3b77921aca9956edab0fc4e70d7bed9cfcf6cf463f"
        ),
    },
    "tg-m23-derived-evidence.md": {
        "source": "docs/execution-contracts/tg-m23-derived-evidence.md",
        "body_size": 24_823,
        "body_sha256": (
            "c7df648cb60228b57767011fa5516b324f54f41dc73e36bb1ac9d55697227cee"
        ),
        "archive_sha256": (
            "8c439d9855cb9eb1187b729faba3c457ceacc03c7cc6c6adffb0d26c01257216"
        ),
    },
    "tg-m23-process-safety.md": {
        "source": "docs/execution-contracts/tg-m23-process-safety.md",
        "body_size": 19_970,
        "body_sha256": (
            "e20fde1319b93887b8525bc2f715ed6980e298aef10c610e784410207969f485"
        ),
        "archive_sha256": (
            "5cae9f76493bd792a1a347a0ff6cd7653448311211530ae7b7fda3a2be18357f"
        ),
    },
    "tg-m24-verification-runner.md": {
        "source": "docs/execution-contracts/tg-m24-verification-runner.md",
        "body_size": 43_720,
        "body_sha256": (
            "53e2d3a2d0c2a6be6e713f2949262c414b33f16c32d2e5c2c63cfde791757c41"
        ),
        "archive_sha256": (
            "1f5961cf94c876158f364878178ba6d1dd4d663a6bc08a07c39a6c55a93fe6c9"
        ),
    },
    "plan.md": {
        "source": "plan.md",
        "body_size": 35_150,
        "body_sha256": (
            "705331e4cdd8f5b4225a130950bf3352c2db7289294e9a5f1854b72b3cd14744"
        ),
        "archive_sha256": (
            "10cd682d69a01c48d845f9c7e301e6d71cd939f8b970fa3369f699edf6433312"
        ),
    },
}
M20S3_TASK = "tg_task_286129dbca4d25ab"
ROADMAP_SOURCE_COMMIT = "af5e19545e4f5b59817c70fbc5e2763c0dbf2e1e"
RETIRED_ROADMAP = ROOT / "docs" / "implementation-roadmap.md"
ROADMAP_RETIREMENT_RELATIVE = (
    "roadmap-retirement/implementation-roadmap.md"
)
ROADMAP_RETIREMENT_CAPTURE = (
    HISTORY_ROOT / "v0.10.0" / ROADMAP_RETIREMENT_RELATIVE
)
ROADMAP_REPLACEMENT_LINKS = (
    "../../AGENTS.md",
    "../specification.md",
    "../design.md",
    "../../plan.md",
)
CURRENT_AUTHORITY_REPLACEMENT_LINKS = (
    "../authority.md",
    "../specification.md",
    "../design.md",
    "../../plan.md",
)


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


class DocumentHistoryTests(unittest.TestCase):
    def test_current_contract_owners_are_routed_without_retired_duplicates(self):
        authority = (ROOT / "docs" / "authority.md").read_text(
            encoding="utf-8-sig"
        )
        specification = (ROOT / "docs" / "specification.md").read_text(
            encoding="utf-8-sig"
        )
        design = (ROOT / "docs" / "design.md").read_text(encoding="utf-8-sig")
        plan = (ROOT / "plan.md").read_text(encoding="utf-8-sig")

        selective_start = authority.index("## Selective Current Authority")
        selective_end = authority.index("\n## ", selective_start + 1)
        selective_routes = authority[selective_start:selective_end]
        self.assertEqual(
            Counter(re.findall(r"\[[^]]+\]\(([^)]+)\)", selective_routes)),
            Counter({"specification.md": 1, "design.md": 1, "../plan.md": 1}),
        )
        self.assertEqual(
            authority.count('<a id="document-authority-index"></a>'),
            1,
        )

        current_contracts = (
            "## Recovery Candidate Validity Contract",
            "## Stored Task Read And Privacy Contract",
            "## Stored Contract Pointer Integrity Contract",
        )
        starts = []
        for heading in current_contracts:
            with self.subTest(heading=heading):
                self.assertEqual(specification.count(heading), 1)
                self.assertNotIn(heading, design)
                start = specification.index(heading)
                end = specification.find("\n## ", start + 1)
                section = specification[start : len(specification) if end < 0 else end]
                starts.append(start)
        self.assertEqual(starts, sorted(starts))

        implementation_owners = (
            "### Shared Stored Task Row/Batch Validator",
            "### Stored Contract Pointer Relationship Boundary",
        )
        for heading in implementation_owners:
            with self.subTest(implementation_owner=heading):
                self.assertEqual(design.count(heading), 1)
                self.assertNotIn(heading, specification)

        m25_owners = (
            (
                specification,
                "## Current M25 Select-Split-Merge-Register Contract",
                "\n## ",
            ),
            (
                design,
                "## Current M25 Select-Split-Merge-Register Design",
                "\n## ",
            ),
            (
                plan,
                "### M25 Active Select-Split-Merge-Register Guidance",
                "\n### ",
            ),
        )
        for owner, heading, next_heading in m25_owners:
            with self.subTest(m25_owner=heading):
                self.assertEqual(owner.count(heading), 1)
                start = owner.index(heading)
                end = owner.find(next_heading, start + 1)
                section = owner[start : len(owner) if end < 0 else end]
                self.assertNotIn("Inactive", section)
        self.assertNotIn(
            "## Accepted But Inactive M25 Select-Split-Merge-Register Contract",
            specification,
        )
        self.assertNotIn(
            "## Accepted But Inactive M25 Select-Split-Merge-Register Design",
            design,
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

    def test_final_roadmap_capture_has_exact_source_body_and_retirement_prefix(self):
        captured = subprocess.run(
            [
                "git",
                "show",
                f"{ROADMAP_SOURCE_COMMIT}:docs/implementation-roadmap.md",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(captured.returncode, 0)
        self.assertEqual(len(captured.stdout), 34_165)
        self.assertEqual(
            hashlib.sha256(captured.stdout).hexdigest(),
            "8e436273cc37371f26aee595f6d5f76e9c31a7825d0ebe1d3b9ec79319d7a139",
        )

        data = ROADMAP_RETIREMENT_CAPTURE.read_bytes()
        self.assertTrue(data.endswith(captured.stdout))
        body = data[-len(captured.stdout) :]
        self.assertEqual(body, captured.stdout)
        prefix = data[: -len(captured.stdout)].decode("utf-8")
        for marker in (
            "NON-AUTHORITATIVE HISTORY",
            "FINAL ROADMAP RETIREMENT CAPTURE",
            "docs/implementation-roadmap.md",
            ROADMAP_SOURCE_COMMIT,
            "Retired from the active governing set",
            "public CLI for live Task state and evidence",
            "Captured body begins below.",
        ):
            self.assertIn(marker, prefix)
        for target in (
            "../../../../AGENTS.md",
            "../../../specification.md",
            "../../../design.md",
            "../../../../plan.md",
        ):
            self.assertIn(target, prefix)

    def test_history_index_routes_retired_entries_and_indexes_every_capture(self):
        index = (HISTORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("not current", index)
        self.assertIn("append-only", index)
        self.assertNotIn("](../implementation-roadmap.md)", index)
        self.assertNotIn("](../execution-contracts/", index)
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
                normalized_section = " ".join(section.split())
                self.assertIn(expected["capture"], section)
                self.assertEqual(section.count(f"](v0.10.0/{relative})"), 1)
                if expected["replacement"] == "docs/implementation-roadmap.md":
                    for target in ROADMAP_REPLACEMENT_LINKS:
                        self.assertEqual(section.count(f"]({target})"), 1)
                    self.assertIn(
                        "public CLI for live Task state and evidence",
                        normalized_section,
                    )
                else:
                    self.assertIn(expected["replacement"], section)

        final_heading = (
            "Final retirement capture of `docs/implementation-roadmap.md`"
        )
        final_sections = re.findall(
            rf"^### {re.escape(final_heading)}\n(.*?)(?=^### |^## |\Z)",
            index,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertEqual(len(final_sections), 1)
        final_section = final_sections[0]
        normalized_final_section = " ".join(final_section.split())
        self.assertIn(ROADMAP_SOURCE_COMMIT, final_section)
        self.assertEqual(
            final_section.count(
                "](v0.10.0/roadmap-retirement/implementation-roadmap.md)"
            ),
            1,
        )
        for target in ROADMAP_REPLACEMENT_LINKS:
            self.assertEqual(final_section.count(f"]({target})"), 1)
        self.assertIn(
            "public CLI for live Task state and evidence",
            normalized_final_section,
        )

        indexed_archives = (
            set(ARCHIVES)
            | STUDY_HISTORIES
            | {ROADMAP_RETIREMENT_RELATIVE}
        )
        actual_archives = {
            path.relative_to(HISTORY_ROOT / "v0.10.0").as_posix()
            for path in (HISTORY_ROOT / "v0.10.0").rglob("*.md")
        }
        self.assertEqual(actual_archives, indexed_archives)

        pre_m22_relative = "v0.11.0/pre-m22-completed-execution.md"
        pre_m22_bytes = (HISTORY_ROOT / pre_m22_relative).read_bytes()
        self.assertEqual(
            hashlib.sha256(pre_m22_bytes).hexdigest(),
            PRE_M22_HISTORY_SHA256,
        )
        self.assertEqual(index.count(f"]({pre_m22_relative})"), 1)

    def test_m23_retirement_capture_preserves_exact_source_sections(self):
        relative = "v0.13.0/m23-analyzer-retirement.md"
        source_commit = "5c58f1038a5f7df46b393ec5fc5cf454fa38eb36"
        data = (HISTORY_ROOT / relative).read_bytes()
        self.assertIn(source_commit.encode("ascii"), data)
        captured = data.split(b"````markdown\n")[1:]
        sections = (
            (
                "docs/specification.md",
                b"## Derived-Evidence Analyzer\n",
                b"## Trusted-Local Verification Runner",
            ),
            (
                "docs/design.md",
                b'<a id="derived-evidence-analyzer-process-and-publication-boundary"></a>',
                b"## Completion Cycle History",
            ),
        )
        self.assertEqual(len(captured), len(sections))
        for block, (path, start_marker, end_marker) in zip(captured, sections):
            with self.subTest(path=path):
                source = subprocess.run(
                    ["git", "show", f"{source_commit}:{path}"],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(source.returncode, 0)
                start = source.stdout.index(start_marker)
                end = source.stdout.index(end_marker, start)
                self.assertEqual(block.split(b"````\n", 1)[0], source.stdout[start:end])
        index = (HISTORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(index.count(f"]({relative})"), 1)
        self.assertIn(source_commit, index)

    def test_m23_authority_split_capture_preserves_exact_source_provenance(self):
        data = M23_AUTHORITY_SPLIT_CAPTURE.read_bytes()
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            M23_AUTHORITY_SPLIT_ARCHIVE_SHA256,
        )

        source = subprocess.run(
            [
                "git",
                "show",
                f"{M23_AUTHORITY_SPLIT_SOURCE_COMMIT}:"
                f"{M23_AUTHORITY_SPLIT_SOURCE}",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(source.returncode, 0)
        self.assertTrue(data.endswith(source.stdout))
        prefix = data[: -len(source.stdout)].decode("utf-8")
        for marker in (
            "NON-AUTHORITATIVE HISTORY",
            M23_AUTHORITY_SPLIT_SOURCE,
            M23_AUTHORITY_SPLIT_SOURCE_COMMIT,
            "Captured body begins below.",
            "../../execution-contracts/tg-m23-derived-evidence.md#tg-m23-derived-evidence",
            "../../execution-contracts/tg-m23-process-safety.md#tg-m23-process-safety",
        ):
            self.assertIn(marker, prefix)

        index = (HISTORY_ROOT / "README.md").read_text(encoding="utf-8")
        heading = (
            "Pre-process-safety split capture of "
            "`docs/execution-contracts/tg-m23-derived-evidence.md`"
        )
        sections = re.findall(
            rf"^### {re.escape(heading)}\n(.*?)(?=^### |^## |\Z)",
            index,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertEqual(len(sections), 1)
        section = sections[0]
        self.assertEqual(
            index.count(f"]({M23_AUTHORITY_SPLIT_RELATIVE})"),
            1,
        )
        self.assertEqual(section.count(M23_AUTHORITY_SPLIT_SOURCE_COMMIT), 1)
        self.assertIn("Capture unit: `TG-M23.1`", section)
        for target in CURRENT_AUTHORITY_REPLACEMENT_LINKS:
            self.assertEqual(section.count(f"]({target})"), 1)

    def test_m243_decomposition_capture_preserves_exact_source_provenance(self):
        data = M243_DECOMPOSITION_CAPTURE.read_bytes()
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            M243_DECOMPOSITION_ARCHIVE_SHA256,
        )

        source = subprocess.run(
            [
                "git",
                "show",
                f"{M243_DECOMPOSITION_SOURCE_COMMIT}:"
                f"{M243_DECOMPOSITION_SOURCE}",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(source.returncode, 0)
        self.assertEqual(len(source.stdout), M243_DECOMPOSITION_BODY_SIZE)
        self.assertEqual(
            hashlib.sha256(source.stdout).hexdigest(),
            M243_DECOMPOSITION_BODY_SHA256,
        )
        self.assertTrue(data.endswith(source.stdout))
        body = data[-M243_DECOMPOSITION_BODY_SIZE:]
        self.assertEqual(body, source.stdout)
        prefix = data[:-M243_DECOMPOSITION_BODY_SIZE].decode("utf-8")
        for marker in (
            "NON-AUTHORITATIVE HISTORY",
            M243_DECOMPOSITION_SOURCE,
            M243_DECOMPOSITION_SOURCE_COMMIT,
            "Captured body begins below.",
            "../../execution-contracts/tg-m24-verification-runner.md#tg-m24-3a",
            "../../execution-contracts/tg-m24-verification-runner.md#tg-m24-3b",
            "../../execution-contracts/tg-m24-verification-runner.md#tg-m24-3c",
        ):
            self.assertIn(marker, prefix)

        index = (HISTORY_ROOT / "README.md").read_text(encoding="utf-8")
        heading = (
            "Pre-M24.3-decomposition capture of "
            "`docs/execution-contracts/tg-m24-verification-runner.md`"
        )
        sections = re.findall(
            rf"^### {re.escape(heading)}\n(.*?)(?=^### |^## |\Z)",
            index,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertEqual(len(sections), 1)
        section = sections[0]
        self.assertEqual(index.count(f"]({M243_DECOMPOSITION_RELATIVE})"), 1)
        self.assertEqual(section.count(M243_DECOMPOSITION_SOURCE_COMMIT), 1)
        self.assertIn("Capture unit: `TG-M24.3`", section)
        for target in CURRENT_AUTHORITY_REPLACEMENT_LINKS:
            self.assertEqual(section.count(f"]({target})"), 1)

    def test_post_m24_capture_set_preserves_exact_source_provenance(self):
        actual_names = {
            path.name for path in POST_M24_CAPTURE_ROOT.glob("*.md")
        }
        self.assertEqual(actual_names, set(POST_M24_CAPTURES))

        index = (HISTORY_ROOT / "README.md").read_text(encoding="utf-8")
        heading = "v0.13.0 Post-M24 Completed Execution Lineage"
        sections = re.findall(
            rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)",
            index,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertEqual(len(sections), 1)
        section = sections[0]
        self.assertEqual(section.count(POST_M24_SOURCE_COMMIT), 1)
        self.assertEqual(section.count("Capture unit: `TG-DOC.3`"), 1)
        for target in CURRENT_AUTHORITY_REPLACEMENT_LINKS:
            self.assertEqual(section.count(f"]({target})"), 1)

        for relative, expected in POST_M24_CAPTURES.items():
            with self.subTest(relative=relative):
                capture = POST_M24_CAPTURE_ROOT / relative
                data = capture.read_bytes()
                self.assertEqual(
                    hashlib.sha256(data).hexdigest(),
                    expected["archive_sha256"],
                )

                source = subprocess.run(
                    [
                        "git",
                        "show",
                        f"{POST_M24_SOURCE_COMMIT}:{expected['source']}",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(source.returncode, 0)
                self.assertEqual(len(source.stdout), expected["body_size"])
                self.assertEqual(
                    hashlib.sha256(source.stdout).hexdigest(),
                    expected["body_sha256"],
                )
                self.assertTrue(data.endswith(source.stdout))
                body = data[-expected["body_size"] :]
                self.assertEqual(body, source.stdout)

                prefix = data[: -expected["body_size"]].decode("utf-8")
                self.assertTrue(
                    prefix.startswith(
                        "> [!CAUTION]\n"
                        "> **NON-AUTHORITATIVE HISTORY**\n"
                    )
                )
                for structural_field in (
                    f"> - Source path: `{expected['source']}`",
                    f"> - Source commit: `{POST_M24_SOURCE_COMMIT}`",
                    "> - Current replacements:",
                    "> - Capture unit: `TG-DOC.3`",
                    "> The exact source blob begins below.",
                ):
                    self.assertEqual(prefix.count(structural_field), 1)

                indexed_relative = (
                    "v0.13.0/post-m24-completed-execution/" + relative
                )
                self.assertEqual(index.count(f"]({indexed_relative})"), 1)
                self.assertEqual(section.count(f"`{expected['source']}`"), 1)

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

    def test_m20s_study_is_retired_to_a_no_rerun_tombstone(self):
        receipt_root = ROOT / "fixtures" / "m20s"
        receipt_paths = sorted(receipt_root.glob("*.json"))
        self.assertEqual(
            [path.name for path in receipt_paths],
            ["decomposition-collection-receipt.json"],
        )
        receipt = json.loads(receipt_paths[0].read_text(encoding="utf-8"))
        self.assertEqual(
            receipt["schema"],
            "m20s-decomposition-collection-receipt-v1",
        )
        self.assertEqual(receipt["status"], "closed")
        self.assertEqual(receipt["artifact_status"], "retired")
        self.assertEqual(
            receipt["retirement_revision"],
            M20S_RETIREMENT_ANCHOR,
        )
        self.assertEqual(receipt["attempted_pairs"], 1)
        self.assertEqual(receipt["attempted_arms"], 2)
        self.assertEqual(receipt["record_count"], 4)
        self.assertEqual(receipt["eligible_pairs"], 2)
        self.assertEqual(receipt["qualifying_pairs"], 2)
        self.assertEqual(receipt["unavailable_pairs"], 2)
        self.assertEqual(receipt["decision"], "proceed_to_design")
        self.assertEqual(receipt["corpus_bytes"], 6308)
        self.assertEqual(
            receipt["corpus_sha256"],
            "07c365b4008c387073f5dfe70fa8ac00c0aa6fca7033796db0698b5b910cd664",
        )

        anchor = subprocess.run(
            [
                "git",
                "cat-file",
                "-e",
                f"{M20S_RETIREMENT_ANCHOR}^{{commit}}",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(anchor.returncode, 0)
        history_path = (
            HISTORY_ROOT / "v0.10.0" / "m20s-task-decomposition.md"
        )
        self.assertEqual(
            hashlib.sha256(history_path.read_bytes()).hexdigest(),
            M20S_HISTORY_SHA256,
        )
        history = history_path.read_text(encoding="utf-8")
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

    def test_retired_completion_index_preserves_pre_task_db_lineage(self):
        retirement_data = ROADMAP_RETIREMENT_CAPTURE.read_bytes()
        roadmap = retirement_data[-34_165:].decode("utf-8-sig")
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
