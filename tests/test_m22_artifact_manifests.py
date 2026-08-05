import hashlib
import io
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    import task_governance_tool.artifact_manifest as artifact_manifest
    from task_governance_tool.artifact_manifest import (
        ARTIFACT_MANIFEST_DOMAIN,
        ArtifactLeaf,
        ArtifactManifestError,
        ArtifactManifestSpec,
        ArtifactObservation,
        build_artifact_entries,
        build_artifact_manifest,
        observe_git_commit_manifest,
        observe_staged_git_manifest,
        opaque_artifact_observation,
        validate_artifact_path,
    )
    from task_governance_tool.evidence_ledger import (
        EvidenceLedgerError,
        TargetCaptureBinding,
        canonical_json_bytes,
    )
    from task_governance_tool.git_snapshot import (
        GitSnapshotEntry,
        GitSnapshotError,
        manifest_fingerprint,
        run_git_bytes,
        stream_index_fingerprint,
    )
finally:
    sys.path.pop(0)


SNAPSHOT_ID = "tg_authority_snapshot_0123456789abcdef"
OID_A = "1" * 40
OID_B = "2" * 40
OID_C = "3" * 40


def leaf(path, oid=OID_A, mode="100644"):
    return ArtifactLeaf(path, mode, oid)


def target_binding(observation, generation=1):
    return TargetCaptureBinding(
        target_kind=observation.target_kind,
        target_value=observation.target_value,
        target_base_revision=observation.target_base_revision,
        target_generation=generation,
        authority_snapshot_id=SNAPSHOT_ID,
        acceptance_criterion_id=None,
        verification_criterion_id=None,
    )


def git(repo: Path, *args: str, input_text: str | None = None):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )


def commit(repo: Path, message: str) -> str:
    git(repo, "-c", "user.name=TaskGov Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


class FakePopen:
    def __init__(
        self,
        stdout: bytes,
        *,
        timeout: bool = False,
        stubborn: bool = False,
    ):
        self.stdout = io.BytesIO(stdout)
        self.returncode = 0
        self.timeout = timeout
        self.stubborn = stubborn
        self.killed = False

    def wait(self, timeout=None):
        if self.timeout and (not self.killed or self.stubborn):
            raise subprocess.TimeoutExpired(["git"], timeout)
        return -9 if self.killed else self.returncode

    def kill(self):
        self.killed = True


class BlockingStdout:
    def __init__(self):
        self.closed = threading.Event()

    def read(self, _size):
        self.closed.wait(timeout=5)
        return b""

    def close(self):
        self.closed.set()


class ObjectLossAfterFirstCheck:
    def __init__(self):
        self.real_run = subprocess.run
        self.batch_count = 0

    def __call__(self, *args, **kwargs):
        command = args[0]
        is_batch_check = any(
            isinstance(argument, str) and argument.startswith("--batch-check=")
            for argument in command
        )
        if is_batch_check:
            self.batch_count += 1
            if self.batch_count == 2:
                object_id = kwargs["input"].splitlines()[0]
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=object_id + b" missing\n",
                    stderr=b"private Git object detail",
                )
        return self.real_run(*args, **kwargs)


class PureManifestTests(unittest.TestCase):
    def test_exact_unique_rename_modify_add_delete_and_ordinals(self):
        entries = build_artifact_entries(
            (
                leaf("rename-old.txt", OID_A),
                leaf("modify.txt", OID_A),
                leaf("delete.txt", OID_B),
            ),
            (
                leaf("rename-new.txt", OID_A),
                leaf("modify.txt", OID_C),
                leaf("add.txt", OID_B, "100755"),
            ),
        )
        self.assertEqual([entry.ordinal for entry in entries], list(range(4)))
        self.assertEqual(
            [(entry.kind, entry.old_path, entry.new_path) for entry in entries],
            [
                ("add", None, "add.txt"),
                ("delete", "delete.txt", None),
                ("modify", "modify.txt", "modify.txt"),
                ("rename", "rename-old.txt", "rename-new.txt"),
            ],
        )

    def test_ambiguous_duplicate_content_stays_deletes_and_adds(self):
        entries = build_artifact_entries(
            (leaf("old-a", OID_A), leaf("old-b", OID_A)),
            (leaf("new-a", OID_A), leaf("new-b", OID_A)),
        )
        self.assertEqual({entry.kind for entry in entries}, {"add", "delete"})
        self.assertEqual(len(entries), 4)

    def test_path_sort_uses_unsigned_utf8_bytes(self):
        entries = build_artifact_entries((), (leaf("z"), leaf("é"), leaf("あ")))
        expected = sorted(["z", "é", "あ"], key=lambda value: value.encode("utf-8"))
        self.assertEqual([entry.new_path for entry in entries], expected)

    def test_unsafe_paths_use_only_fixed_error(self):
        for value in (
            "",
            "/absolute",
            "../escape",
            "a/../b",
            "a\\b",
            "C:escape",
            "a//b",
            "a\0b",
            "a\nb",
            "zero\u200bwidth",
            "line\u2028separator",
            "x" * 241,
            "\ud800",
        ):
            with self.subTest(value=repr(value)), self.assertRaises(ArtifactManifestError) as raised:
                validate_artifact_path(value)
            self.assertEqual(raised.exception.code, "artifact_manifest_path_unsafe")
            self.assertEqual(str(raised.exception), "artifact manifest contains an unsafe project path")

    def test_windows_escape_components_are_rejected_at_every_depth(self):
        for value in (
            "safe/CON/leaf.txt",
            "safe/NUL.txt",
            "safe/cOm1.bin/leaf.txt",
            "safe/lPt9/leaf.txt",
            "safe/aux.data",
            "safe/name:stream/leaf.txt",
            "safe/name<part/leaf.txt",
            "safe/name>part/leaf.txt",
            'safe/name"part/leaf.txt',
            "safe/name|part/leaf.txt",
            "safe/name?part/leaf.txt",
            "safe/name*part/leaf.txt",
            "safe/trailing./leaf.txt",
            "safe/trailing /leaf.txt",
        ):
            with self.subTest(value=value), self.assertRaises(ArtifactManifestError) as raised:
                validate_artifact_path(value)
            self.assertEqual(raised.exception.code, "artifact_manifest_path_unsafe")
            self.assertEqual(str(raised.exception), "artifact manifest contains an unsafe project path")

        for value in (
            "CLOCK$",
            "CLOCK$.txt",
            "safe/CLOCK$/leaf.txt",
            "safe/CLOCK$.txt",
            "safe/console.txt",
            "safe/nulled.txt",
            "safe/com0.txt",
            "safe/com10.txt",
            "safe/lpt0.txt",
            "safe/space inside/leaf.txt",
        ):
            with self.subTest(value=value):
                self.assertEqual(validate_artifact_path(value), value)

    def test_opaque_manifest_has_exact_keys_and_domain_digest(self):
        observation = opaque_artifact_observation(
            target_kind="diff_fingerprint",
            target_value="sha256:" + "a" * 64,
        )
        manifest = build_artifact_manifest(observation, target_binding(observation))
        self.assertEqual(manifest.entries, ())
        self.assertIsNone(manifest.object_format)
        self.assertEqual(manifest.omission_code, "artifact_content_not_observed")
        expected = "sha256:" + hashlib.sha256(
            ARTIFACT_MANIFEST_DOMAIN + canonical_json_bytes(manifest.canonical_value())
        ).hexdigest()
        self.assertEqual(manifest.digest, expected)

    def test_count_and_canonical_size_bounds_are_fail_closed(self):
        with self.assertRaises(ArtifactManifestError) as count_error:
            build_artifact_entries((), tuple(leaf(f"p{index}") for index in range(10_001)))
        self.assertEqual(count_error.exception.code, "artifact_manifest_too_large")

        observation = opaque_artifact_observation(
            target_kind="external_revision",
            target_value="external",
        )
        with patch(
            "task_governance_tool.artifact_manifest.ARTIFACT_MANIFEST_BYTE_LIMIT",
            1,
        ), self.assertRaises(ArtifactManifestError) as size_error:
            build_artifact_manifest(observation, target_binding(observation))
        self.assertEqual(size_error.exception.code, "artifact_manifest_too_large")
        self.assertEqual(str(size_error.exception), "artifact manifest exceeds the supported size")

    def test_snapshot_observation_requires_comparison_base_to_equal_target_base(self):
        with self.assertRaises(ArtifactManifestError) as raised:
            ArtifactObservation(
                state="complete_git",
                object_format="sha1",
                comparison_base=OID_A,
                target_kind="git_snapshot",
                target_value="sha256:" + "a" * 64,
                target_base_revision=OID_B,
            )
        self.assertEqual(raised.exception.code, "artifact_manifest_stale")

    def test_persisted_spec_rejects_recomputed_mismatched_snapshot_base(self):
        observation = ArtifactObservation(
            state="complete_git",
            object_format="sha1",
            comparison_base=OID_A,
            target_kind="git_snapshot",
            target_value="sha256:" + "a" * 64,
            target_base_revision=OID_A,
        )
        manifest = build_artifact_manifest(observation, target_binding(observation))
        canonical = manifest.canonical_value()
        canonical["comparison_base"] = OID_B
        encoded = canonical_json_bytes(canonical)
        digest = "sha256:" + hashlib.sha256(
            ARTIFACT_MANIFEST_DOMAIN + encoded
        ).hexdigest()
        with self.assertRaises(EvidenceLedgerError) as raised:
            ArtifactManifestSpec(
                state=manifest.state,
                object_format=manifest.object_format,
                comparison_base=OID_B,
                target_kind=manifest.target_kind,
                target_value=manifest.target_value,
                target_base_revision=manifest.target_base_revision,
                target_generation=manifest.target_generation,
                authority_snapshot_id=manifest.authority_snapshot_id,
                acceptance_criterion_id=manifest.acceptance_criterion_id,
                verification_criterion_id=manifest.verification_criterion_id,
                omission_code=manifest.omission_code,
                entries=manifest.entries,
                digest=digest,
                canonical_size=len(encoded),
            )
        self.assertEqual(getattr(raised.exception, "code", None), "evidence_ledger_inconsistent")

    def test_sha256_git_ids_are_consistent_with_object_format(self):
        base = "a" * 64
        observation = ArtifactObservation(
            state="complete_git",
            object_format="sha256",
            comparison_base=base,
            target_kind="git_snapshot",
            target_value="sha256:" + "b" * 64,
            target_base_revision=base,
            after_leaves=(leaf("safe.txt", "c" * 64),),
        )
        self.assertEqual(
            build_artifact_manifest(observation, target_binding(observation)).object_format,
            "sha256",
        )
        with self.assertRaises(ArtifactManifestError) as raised:
            ArtifactObservation(
                state="complete_git",
                object_format="sha256",
                comparison_base=base,
                target_kind="git_commit",
                target_value=OID_A,
                target_base_revision="",
            )
        self.assertEqual(raised.exception.code, "artifact_manifest_stale")


class GitObservationTests(unittest.TestCase):
    def test_commit_capture_rejects_nested_windows_escape_components(self):
        for unsafe_path in (
            b"safe/NUL.txt",
            b"safe/name:stream/leaf.txt",
            b"safe/name<part/leaf.txt",
            b"safe/name>part/leaf.txt",
            b'safe/name"part/leaf.txt',
            b"safe/name|part/leaf.txt",
            b"safe/name?part/leaf.txt",
            b"safe/name*part/leaf.txt",
            b"safe/trailing./leaf.txt",
            b"safe/trailing /leaf.txt",
        ):
            payload = (
                b":000000 100644 "
                + b"0" * 40
                + b" "
                + OID_A.encode("ascii")
                + b" A\0"
                + unsafe_path
                + b"\0"
            )
            with self.subTest(path=unsafe_path), self.assertRaises(
                ArtifactManifestError
            ) as raised:
                artifact_manifest._raw_diff_leaves(
                    payload,
                    "sha1",
                    stale=False,
                    commit=True,
                )
            self.assertEqual(raised.exception.code, "artifact_manifest_path_unsafe")
            self.assertEqual(str(raised.exception), "artifact manifest contains an unsafe project path")

    def test_raw_diff_count_limit_is_applied_before_manifest_expansion(self):
        payload = b"".join(
            b":000000 100644 "
            + b"0" * 40
            + b" "
            + OID_A.encode("ascii")
            + b" A\0"
            + f"added-{index}.txt".encode("ascii")
            + b"\0"
            for index in range(2)
        )
        with patch.object(
            artifact_manifest,
            "_RAW_DIFF_RECORD_LIMIT",
            1,
        ), self.assertRaises(ArtifactManifestError) as raised:
            artifact_manifest._raw_diff_leaves(
                payload,
                "sha1",
                stale=False,
                commit=False,
            )
        self.assertEqual(raised.exception.code, "artifact_manifest_too_large")

    def test_raw_diff_allows_two_records_per_exact_rename_before_final_gate(self):
        zero = b"0" * 40

        def raw_record(kind: bytes, path: bytes, oid: str) -> bytes:
            object_id = oid.encode("ascii")
            if kind == b"D":
                return (
                    b":100644 000000 "
                    + object_id
                    + b" "
                    + zero
                    + b" D\0"
                    + path
                    + b"\0"
                )
            return (
                b":000000 100644 "
                + zero
                + b" "
                + object_id
                + b" A\0"
                + path
                + b"\0"
            )

        payload = b"".join(
            (
                raw_record(b"D", b"old-a.txt", OID_A),
                raw_record(b"D", b"old-b.txt", OID_B),
                raw_record(b"A", b"new-a.txt", OID_A),
                raw_record(b"A", b"new-b.txt", OID_B),
            )
        )
        with patch.object(
            artifact_manifest,
            "ARTIFACT_ENTRY_LIMIT",
            2,
        ), patch.object(
            artifact_manifest,
            "_RAW_DIFF_RECORD_LIMIT",
            4,
        ):
            before, after = artifact_manifest._raw_diff_leaves(
                payload,
                "sha1",
                stale=False,
                commit=False,
            )
            entries = build_artifact_entries(before, after)
        self.assertEqual(len(entries), 2)
        self.assertEqual({entry.kind for entry in entries}, {"rename"})

    def test_bounded_git_reader_sanitizes_overflow_timeout_and_malformed_index(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "base.txt")
            commit(repo, "root")
            with self.assertRaises(GitSnapshotError) as overflow:
                run_git_bytes(
                    repo,
                    ["rev-parse", "HEAD"],
                    code="bounded_overflow",
                    message="bounded Git read failed",
                    output_limit=1,
                )
            self.assertEqual(overflow.exception.code, "bounded_overflow")
            self.assertEqual(str(overflow.exception), "bounded Git read failed")

        timed_process = FakePopen(b"", timeout=True)
        with patch(
            "task_governance_tool.git_snapshot.subprocess.Popen",
            return_value=timed_process,
        ), self.assertRaises(GitSnapshotError) as timed_out:
            run_git_bytes(
                Path("unused"),
                ["rev-parse", "HEAD"],
                code="bounded_timeout",
                message="bounded Git read failed",
            )
        self.assertTrue(timed_process.killed)
        self.assertEqual(timed_out.exception.code, "bounded_timeout")

        stubborn_process = FakePopen(b"", timeout=True, stubborn=True)
        with patch(
            "task_governance_tool.git_snapshot.subprocess.Popen",
            return_value=stubborn_process,
        ), self.assertRaises(GitSnapshotError) as cleanup_failed:
            run_git_bytes(
                Path("unused"),
                ["rev-parse", "HEAD"],
                code="bounded_cleanup",
                message="bounded Git read failed",
            )
        self.assertTrue(stubborn_process.killed)
        self.assertEqual(cleanup_failed.exception.code, "bounded_cleanup")

        malformed_process = FakePopen(b"not-an-index-record\0")
        with patch(
            "task_governance_tool.git_snapshot.subprocess.Popen",
            return_value=malformed_process,
        ), self.assertRaises(GitSnapshotError) as malformed:
            stream_index_fingerprint(Path("unused"), OID_A)
        self.assertEqual(malformed.exception.code, "invalid_review_evidence")

        overlong_record = (
            b"100644 "
            + OID_A.encode("ascii")
            + b" 0\t"
            + b"x" * 241
            + b"\0"
        )
        with patch(
            "task_governance_tool.git_snapshot.subprocess.Popen",
            return_value=FakePopen(overlong_record),
        ), self.assertRaises(ArtifactManifestError) as path_overflow:
            stream_index_fingerprint(
                Path("unused"),
                OID_A,
                record_byte_limit=(
                    artifact_manifest._INDEX_RECORD_FIXED_BYTES
                    + 40
                    + artifact_manifest.ARTIFACT_PATH_BYTE_LIMIT
                ),
                record_overflow_error=artifact_manifest._path_unsafe,
                consume_entry=artifact_manifest._stream_entry_validator(
                    "sha1",
                    stale=False,
                ),
            )
        self.assertEqual(path_overflow.exception.code, "artifact_manifest_path_unsafe")

        with patch(
            "task_governance_tool.git_snapshot.subprocess.Popen",
            return_value=FakePopen(overlong_record),
        ), self.assertRaises(ArtifactManifestError) as repeat_overflow:
            stream_index_fingerprint(
                Path("unused"),
                OID_A,
                code="artifact_manifest_stale",
                message="Git material changed while capturing the artifact manifest",
                record_byte_limit=(
                    artifact_manifest._INDEX_RECORD_FIXED_BYTES
                    + 40
                    + artifact_manifest.ARTIFACT_PATH_BYTE_LIMIT
                ),
                record_overflow_error=artifact_manifest._stale,
                consume_entry=artifact_manifest._stream_entry_validator(
                    "sha1",
                    stale=True,
                ),
            )
        self.assertEqual(repeat_overflow.exception.code, "artifact_manifest_stale")

    def test_streamed_sha256_index_fingerprint_matches_canonical_bytes(self):
        base = "a" * 64
        entries = (
            GitSnapshotEntry(b"100644", b"b" * 64, b"a.txt"),
            GitSnapshotEntry(b"100755", b"c" * 64, b"z.txt"),
        )
        payload = b"".join(
            entry.mode
            + b" "
            + entry.object_id
            + b" 0\t"
            + entry.path
            + b"\0"
            for entry in entries
        )
        with patch(
            "task_governance_tool.git_snapshot.subprocess.Popen",
            return_value=FakePopen(payload),
        ):
            streamed = stream_index_fingerprint(Path("unused"), base)
        self.assertEqual(streamed.entry_count, len(entries))
        self.assertEqual(streamed.fingerprint, manifest_fingerprint(base, list(entries)))

    def test_timeout_cleanup_closes_a_blocked_reader_within_fixed_grace(self):
        process = FakePopen(b"", timeout=True, stubborn=True)
        process.stdout = BlockingStdout()
        with patch(
            "task_governance_tool.git_snapshot.subprocess.Popen",
            return_value=process,
        ), patch(
            "task_governance_tool.git_snapshot.GIT_TERMINATION_GRACE_SECONDS",
            0.01,
        ), self.assertRaises(GitSnapshotError) as raised:
            run_git_bytes(
                Path("unused"),
                ["rev-parse", "HEAD"],
                code="bounded_cleanup",
                message="bounded Git read failed",
            )
        self.assertTrue(process.killed)
        self.assertTrue(process.stdout.closed.is_set())
        self.assertEqual(raised.exception.code, "bounded_cleanup")

    def test_large_mostly_unchanged_index_retains_only_changed_manifest_leaves(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            shared_blob = git(
                repo,
                "hash-object",
                "-w",
                "--stdin",
                input_text="shared\n",
            ).stdout.strip()
            tree_input = "".join(
                f"100644 blob {shared_blob}\ttracked-{index:05d}.txt\0"
                for index in range(10_050)
            )
            tree = git(repo, "mktree", "-z", input_text=tree_input).stdout.strip()
            root = git(
                repo,
                "-c",
                "user.name=TaskGov Test",
                "-c",
                "user.email=test@example.invalid",
                "commit-tree",
                tree,
                input_text="root\n",
            ).stdout.strip()
            git(repo, "update-ref", "refs/heads/master", root)
            git(repo, "read-tree", root)
            changed_blob = git(
                repo,
                "hash-object",
                "-w",
                "--stdin",
                input_text="changed\n",
            ).stdout.strip()
            git(
                repo,
                "update-index",
                "--add",
                "--cacheinfo",
                "100644",
                changed_blob,
                "changed.txt",
            )

            observation = observe_staged_git_manifest(repo)
            manifest = build_artifact_manifest(observation, target_binding(observation))

            self.assertEqual(len(observation.before_leaves), 0)
            self.assertEqual(len(observation.after_leaves), 1)
            self.assertEqual(
                [(entry.kind, entry.new_path) for entry in manifest.entries],
                [("add", "changed.txt")],
            )

    def test_batched_object_validation_failures_are_sanitized_stale(self):
        checks = (
            f"{OID_A} missing\n".encode("ascii"),
            f"{OID_A} tree 1\n".encode("ascii"),
            f"{OID_B} blob 1\n".encode("ascii"),
            f"{OID_A} blob -1\n".encode("ascii"),
            f"{OID_A} blob 01\n".encode("ascii"),
        )
        for stdout in checks:
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=stdout,
                stderr=b"private Git detail",
            )
            with self.subTest(stdout=stdout), patch.object(
                artifact_manifest.subprocess,
                "run",
                return_value=completed,
            ), self.assertRaises(ArtifactManifestError) as raised:
                artifact_manifest._require_manifest_objects(
                    Path("unused"),
                    (),
                    (leaf("safe.txt", OID_A),),
                )
            self.assertEqual(raised.exception.code, "artifact_manifest_stale")
            self.assertEqual(
                str(raised.exception),
                "Git material changed while capturing the artifact manifest",
            )

        with patch.object(
            artifact_manifest.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["git"], 15),
        ), self.assertRaises(ArtifactManifestError) as raised:
            artifact_manifest._require_manifest_objects(
                Path("unused"),
                (),
                (leaf("safe.txt", OID_A),),
            )
        self.assertEqual(raised.exception.code, "artifact_manifest_stale")
        self.assertEqual(
            str(raised.exception),
            "Git material changed while capturing the artifact manifest",
        )

    def test_staged_observation_is_head_tree_vs_index_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            (repo / "old.txt").write_text("same\n", encoding="utf-8")
            (repo / "modify.txt").write_text("before\n", encoding="utf-8")
            git(repo, "add", "--all")
            head = commit(repo, "root")
            (repo / "old.txt").rename(repo / "new.txt")
            (repo / "modify.txt").write_text("after\n", encoding="utf-8")
            git(repo, "add", "--all")
            status_before = git(repo, "status", "--porcelain=v1").stdout

            observation = observe_staged_git_manifest(repo)
            manifest = build_artifact_manifest(observation, target_binding(observation))

            self.assertEqual(observation.comparison_base, head)
            self.assertEqual(observation.target_base_revision, head)
            self.assertEqual(
                [(entry.kind, entry.old_path, entry.new_path) for entry in manifest.entries],
                [
                    ("modify", "modify.txt", "modify.txt"),
                    ("rename", "old.txt", "new.txt"),
                ],
            )
            self.assertEqual(git(repo, "status", "--porcelain=v1").stdout, status_before)

    def test_staged_observation_batches_changed_object_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "base.txt")
            commit(repo, "root")
            for index in range(64):
                (repo / f"added-{index:03d}.txt").write_text(
                    f"unique-{index}\n",
                    encoding="utf-8",
                )
            git(repo, "add", "--all")

            real_run = subprocess.run
            with patch(
                "subprocess.run",
                side_effect=lambda *args, **kwargs: real_run(*args, **kwargs),
            ) as spawned:
                observation = observe_staged_git_manifest(repo)

            batch_calls = [
                call
                for call in spawned.call_args_list
                if any(
                    isinstance(argument, str) and argument.startswith("--batch-check=")
                    for argument in call.args[0]
                )
            ]
            self.assertEqual(len(observation.after_leaves) - len(observation.before_leaves), 64)
            self.assertEqual(len(batch_calls), 2)
            self.assertLessEqual(spawned.call_count, 16)
            requests = [call.kwargs["input"] for call in batch_calls]
            self.assertEqual(requests[0], requests[1])
            self.assertEqual(len(requests[0].splitlines()), 64)
            for call in batch_calls:
                self.assertEqual(call.kwargs["timeout"], 15)
                self.assertEqual(call.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")

    def test_staged_observation_rejects_object_loss_after_repeat_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "base.txt")
            head_before = commit(repo, "root")
            (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
            git(repo, "add", "staged.txt")
            index_before = (repo / ".git" / "index").read_bytes()
            status_before = git(repo, "status", "--porcelain=v1").stdout

            object_loss = ObjectLossAfterFirstCheck()

            with patch.object(
                artifact_manifest.subprocess,
                "run",
                side_effect=object_loss,
            ) as spawned, self.assertRaises(ArtifactManifestError) as raised:
                observe_staged_git_manifest(repo)

            self.assertEqual(object_loss.batch_count, 2)
            self.assertLessEqual(spawned.call_count, 16)
            self.assertEqual(raised.exception.code, "artifact_manifest_stale")
            self.assertEqual(
                str(raised.exception),
                "Git material changed while capturing the artifact manifest",
            )
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), head_before)
            self.assertEqual((repo / ".git" / "index").read_bytes(), index_before)
            self.assertEqual(git(repo, "status", "--porcelain=v1").stdout, status_before)

    def test_staged_observation_rejects_index_mutation_during_object_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "base.txt")
            commit(repo, "root")
            (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
            git(repo, "add", "staged.txt")

            real_run = subprocess.run
            mutation_count = 0

            def mutate_index_during_batch(*args, **kwargs):
                nonlocal mutation_count
                command = args[0]
                if any(
                    isinstance(argument, str) and argument.startswith("--batch-check=")
                    for argument in command
                ):
                    mutation_count += 1
                    (repo / "late.txt").write_text("late\n", encoding="utf-8")
                    real_run(
                        ["git", "-C", str(repo), "add", "late.txt"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                    )
                return real_run(*args, **kwargs)

            with patch(
                "subprocess.run",
                side_effect=mutate_index_during_batch,
            ), self.assertRaises(ArtifactManifestError) as raised:
                observe_staged_git_manifest(repo)
            self.assertEqual(mutation_count, 1)
            self.assertEqual(raised.exception.code, "artifact_manifest_stale")
            self.assertEqual(
                str(raised.exception),
                "Git material changed while capturing the artifact manifest",
            )

    def test_commit_observation_rejects_ref_mutation_during_object_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "base.txt")
            commit(repo, "root")
            (repo / "target.txt").write_text("target\n", encoding="utf-8")
            git(repo, "add", "target.txt")
            commit(repo, "target")

            real_run = subprocess.run
            mutation_count = 0

            def mutate_ref_during_batch(*args, **kwargs):
                nonlocal mutation_count
                command = args[0]
                if any(
                    isinstance(argument, str) and argument.startswith("--batch-check=")
                    for argument in command
                ):
                    mutation_count += 1
                    (repo / "late.txt").write_text("late\n", encoding="utf-8")
                    real_run(
                        ["git", "-C", str(repo), "add", "late.txt"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                    )
                    real_run(
                        [
                            "git",
                            "-C",
                            str(repo),
                            "-c",
                            "user.name=TaskGov Test",
                            "-c",
                            "user.email=test@example.invalid",
                            "commit",
                            "--quiet",
                            "-m",
                            "late",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                    )
                return real_run(*args, **kwargs)

            with patch(
                "subprocess.run",
                side_effect=mutate_ref_during_batch,
            ), self.assertRaises(ArtifactManifestError) as raised:
                observe_git_commit_manifest(repo, "HEAD")
            self.assertEqual(mutation_count, 1)
            self.assertEqual(raised.exception.code, "artifact_manifest_stale")
            self.assertEqual(
                str(raised.exception),
                "Git material changed while capturing the artifact manifest",
            )

    def test_commit_observation_rejects_object_loss_after_repeat_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "base.txt")
            commit(repo, "root")
            (repo / "target.txt").write_text("target\n", encoding="utf-8")
            git(repo, "add", "target.txt")
            target = commit(repo, "target")
            index_before = (repo / ".git" / "index").read_bytes()
            status_before = git(repo, "status", "--porcelain=v1").stdout

            object_loss = ObjectLossAfterFirstCheck()

            with patch.object(
                artifact_manifest.subprocess,
                "run",
                side_effect=object_loss,
            ) as spawned, self.assertRaises(ArtifactManifestError) as raised:
                observe_git_commit_manifest(repo, target)

            self.assertEqual(object_loss.batch_count, 2)
            self.assertLessEqual(spawned.call_count, 16)
            self.assertEqual(raised.exception.code, "artifact_manifest_stale")
            self.assertEqual(
                str(raised.exception),
                "Git material changed while capturing the artifact manifest",
            )
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), target)
            self.assertEqual((repo / ".git" / "index").read_bytes(), index_before)
            self.assertEqual(git(repo, "status", "--porcelain=v1").stdout, status_before)

    def test_commit_observation_uses_first_parent_and_root_empty_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            (repo / "one.txt").write_text("one\n", encoding="utf-8")
            git(repo, "add", "one.txt")
            root = commit(repo, "root")
            root_observation = observe_git_commit_manifest(repo, root)
            root_manifest = build_artifact_manifest(root_observation, target_binding(root_observation))
            self.assertEqual([entry.kind for entry in root_manifest.entries], ["add"])
            self.assertEqual(root_observation.comparison_base, "4b825dc642cb6eb9a060e54bf8d69288fbee4904")

            (repo / "two.txt").write_text("two\n", encoding="utf-8")
            git(repo, "add", "two.txt")
            child = commit(repo, "child")
            child_observation = observe_git_commit_manifest(repo, child)
            child_manifest = build_artifact_manifest(child_observation, target_binding(child_observation))
            self.assertEqual(child_observation.comparison_base, root)
            self.assertEqual(child_observation.target_value, child)
            self.assertEqual(
                [(entry.kind, entry.new_path) for entry in child_manifest.entries],
                [("add", "two.txt")],
            )


if __name__ == "__main__":
    unittest.main()
