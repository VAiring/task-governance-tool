import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.verification_runner_runtime import (
        RUNNER_IMPLEMENTATION_DIGEST_DOMAIN,
        VerificationRunnerRuntimeError,
        capture_runner_implementation,
    )
finally:
    sys.path.pop(0)


def sha256_label(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def build_skill(root: Path) -> Path:
    skill = root / "skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"skill\n")
    (skill / "scripts" / "core.py").write_bytes(b"VALUE = 1\n")
    core_files = {
        "SKILL.md": sha256_label((skill / "SKILL.md").read_bytes()),
        "scripts/core.py": sha256_label((skill / "scripts" / "core.py").read_bytes()),
    }
    manifest = {
        "manifest_version": 1,
        "package_name": "task-governance-tool",
        "package_version": "1.2.3",
        "release_origin": "github:example/task-governance-tool",
        "core_files": core_files,
    }
    (skill / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return skill


def canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class VerificationRunnerRuntimeTests(unittest.TestCase):
    def test_implementation_digest_binds_strict_manifest_and_verified_core_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = build_skill(Path(tmp))
            identity = capture_runner_implementation(
                skill,
                expected_package_version="1.2.3",
            )
            canonical = {
                "core_files": dict(identity.core_files),
                "manifest_version": 1,
                "package_name": "task-governance-tool",
                "package_version": "1.2.3",
            }
            self.assertEqual(identity.implementation_version, "taskgov-verification-runner/1")
            self.assertEqual(
                identity.implementation_digest,
                "sha256:"
                + hashlib.sha256(
                    RUNNER_IMPLEMENTATION_DIGEST_DOMAIN + canonical_bytes(canonical)
                ).hexdigest(),
            )

            (skill / "scripts" / "core.py").write_bytes(b"changed\n")
            with self.assertRaises(VerificationRunnerRuntimeError) as changed:
                capture_runner_implementation(skill, expected_package_version="1.2.3")
            self.assertEqual(changed.exception.code, "policy_mismatch")

    def test_implementation_identity_rejects_missing_unknown_and_version_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, mutate, version in (
                ("missing", lambda skill: (skill / "SKILL.md").unlink(), "1.2.3"),
                (
                    "unknown",
                    lambda skill: (skill / "unexpected.py").write_bytes(b"pass\n"),
                    "1.2.3",
                ),
                ("version", lambda _skill: None, "9.9.9"),
            ):
                with self.subTest(name=name):
                    skill = build_skill(root / name)
                    mutate(skill)
                    with self.assertRaises(VerificationRunnerRuntimeError) as raised:
                        capture_runner_implementation(
                            skill,
                            expected_package_version=version,
                        )
                    self.assertEqual(raised.exception.code, "policy_mismatch")


if __name__ == "__main__":
    unittest.main()
