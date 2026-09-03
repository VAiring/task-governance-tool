from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool import verification_runner_git as git_module
    from task_governance_tool.verification_runner import (
        RUNNER_CONTRACT_VERSION,
        RUNNER_IMPLEMENTATION_VERSION,
        RUNNER_TRIGGER,
        resolution_idempotency_digest,
    )
    from task_governance_tool.verification_runner_git import (
        MATERIALIZATION_ERROR_MESSAGE,
        TARGET_DEPTH_LIMIT,
        TARGET_DIRECTORY_LIMIT,
        TARGET_ERROR_MESSAGE,
        TARGET_FILE_LIMIT,
        TARGET_MATERIAL_DOMAIN,
        TARGET_STALE_MESSAGE,
        TARGET_TOTAL_BYTE_LIMIT,
        TARGET_UNSUPPORTED_MESSAGE,
        RunnerTargetEntry,
        RunnerTargetObservation,
        VerificationRunnerGitError,
        materialize_runner_target,
        observe_commit_runner_target,
        observe_staged_runner_target,
        preflight_runner_material,
        preflight_runner_snapshot_successor_material_digest,
    )
    from task_governance_tool.verification_runner_plan import (
        VerificationRunnerPlanSource,
        resolve_verification_runner_plan,
    )
finally:
    sys.path.pop(0)


def git(repo: Path, *arguments: str, input_bytes: bytes | None = None):
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        input=input_bytes,
        stdin=subprocess.DEVNULL if input_bytes is None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def commit(repo: Path, message: str) -> str:
    return git(
        repo,
        "-c",
        "user.name=Runner Git Test",
        "-c",
        "user.email=runner-git@example.invalid",
        "commit",
        "--quiet",
        "-m",
        message,
    ).stdout.decode("ascii").strip() or git(repo, "rev-parse", "HEAD").stdout.decode(
        "ascii"
    ).strip()


def canonical_json_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class RunnerGitTests(unittest.TestCase):
    def make_repo(self, temporary: str) -> Path:
        repo = Path(temporary) / "repo"
        repo.mkdir()
        git(repo, "init", "--quiet")
        return repo.resolve()

    def seed_commit(self, repo: Path, files: dict[str, bytes]) -> str:
        for relative_path, payload in files.items():
            path = repo.joinpath(*relative_path.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        git(repo, "add", "--all")
        commit(repo, "seed")
        return git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()

    def assert_git_error(self, callable_, code: str, message: str):
        with self.assertRaises(VerificationRunnerGitError) as raised:
            callable_()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), message)
        return raised.exception

    def prepare_commit_material(self, repo: Path, revision: str):
        observed = observe_commit_runner_target(repo, revision)
        return observed, preflight_runner_material(repo, observed)

    def test_exact_commit_materialization_is_read_only_and_never_executes_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            marker = Path(temporary) / "target-code-ran"
            script = (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed')\n"
            ).encode("utf-8")
            target = self.seed_commit(
                repo,
                {
                    "payload.txt": b"committed bytes\n",
                    "nested/runner.py": script,
                },
            )
            (repo / "payload.txt").write_bytes(b"ambient working bytes\n")
            status_before = git(repo, "status", "--porcelain=v1", "-z").stdout
            index_before = (repo / ".git" / "index").read_bytes()
            refs_before = git(repo, "show-ref").stdout

            observation, material = self.prepare_commit_material(repo, target)
            repeated = observe_commit_runner_target(repo, target)
            destination = Path(temporary) / "private-target"
            destination.mkdir()
            result = materialize_runner_target(repo, material, destination)

            self.assertEqual(observation, repeated)
            self.assertEqual(
                (destination / "payload.txt").read_bytes(), b"committed bytes\n"
            )
            self.assertEqual((destination / "nested" / "runner.py").read_bytes(), script)
            self.assertFalse(marker.exists())
            self.assertEqual((repo / "payload.txt").read_bytes(), b"ambient working bytes\n")
            self.assertEqual((repo / ".git" / "index").read_bytes(), index_before)
            self.assertEqual(git(repo, "show-ref").stdout, refs_before)
            self.assertEqual(git(repo, "status", "--porcelain=v1", "-z").stdout, status_before)
            self.assertEqual(result.target_material_digest, observation.target_material_digest)
            self.assertEqual((result.entry_count, result.directory_count), (2, 1))

            digest_value = {
                "entries": [entry.canonical_value() for entry in observation.entries],
                "object_format": observation.object_format,
                "target_base_revision": observation.artifact.target_base_revision,
                "target_kind": observation.artifact.target_kind,
                "target_value": observation.artifact.target_value,
            }
            payload = canonical_json_bytes(digest_value)
            expected = "sha256:" + hashlib.sha256(
                TARGET_MATERIAL_DOMAIN + payload
            ).hexdigest()
            self.assertEqual(observation.target_material_digest, expected)
            self.assertNotEqual(
                observation.target_material_digest,
                "sha256:" + hashlib.sha256(payload).hexdigest(),
            )

    def test_ignored_repo_local_private_destination_preserves_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            target = self.seed_commit(
                repo,
                {
                    ".gitignore": (
                        b"/.agents/skills/task-governance-tool/state/\n"
                    ),
                    "payload.txt": b"committed bytes\n",
                },
            )
            (repo / "payload.txt").write_bytes(b"ambient working bytes\n")
            observed, material = self.prepare_commit_material(repo, target)

            for invalid_destination in (repo, repo.parent):
                with self.subTest(destination=invalid_destination), mock.patch.object(
                    git_module.os,
                    "scandir",
                ) as scan:
                    # Isolate the relationship guard from the earlier
                    # nonempty-destination rejection.
                    scan.return_value.__enter__.return_value = iter(())
                    self.assert_git_error(
                        lambda destination=invalid_destination: (
                            materialize_runner_target(repo, material, destination)
                        ),
                        "materialization_failed",
                        MATERIALIZATION_ERROR_MESSAGE,
                    )
                    scan.assert_called_once_with(invalid_destination)

            destination = (
                repo
                / ".agents"
                / "skills"
                / "task-governance-tool"
                / "state"
                / "current"
                / "verification-runner"
                / "attempts"
                / "tg_verification_runner_attempt_0123456789abcdef"
                / "target"
            )
            destination.mkdir(parents=True)
            git(
                repo,
                "check-ignore",
                "--quiet",
                "--",
                str(destination.relative_to(repo)),
            )
            payload_before = (repo / "payload.txt").read_bytes()
            index_before = (repo / ".git" / "index").read_bytes()
            refs_before = git(repo, "show-ref").stdout
            status_before = git(repo, "status", "--porcelain=v1", "-z").stdout

            result = materialize_runner_target(repo, material, destination)

            self.assertEqual(
                (destination / "payload.txt").read_bytes(),
                b"committed bytes\n",
            )
            self.assertEqual(
                (destination / ".gitignore").read_bytes(),
                b"/.agents/skills/task-governance-tool/state/\n",
            )
            self.assertEqual((repo / "payload.txt").read_bytes(), payload_before)
            self.assertEqual((repo / ".git" / "index").read_bytes(), index_before)
            self.assertEqual(git(repo, "show-ref").stdout, refs_before)
            self.assertEqual(
                git(repo, "status", "--porcelain=v1", "-z").stdout,
                status_before,
            )
            self.assertEqual(
                result.target_material_digest,
                observed.target_material_digest,
            )
            self.assertEqual((result.entry_count, result.directory_count), (2, 0))

    def test_staged_materialization_uses_index_bytes_and_omits_ambient_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            self.seed_commit(repo, {"tracked.txt": b"base\n"})
            (repo / "tracked.txt").write_bytes(b"staged\n")
            git(repo, "add", "tracked.txt")
            (repo / "tracked.txt").write_bytes(b"unstaged\n")
            (repo / "untracked.txt").write_bytes(b"untracked\n")
            status_before = git(repo, "status", "--porcelain=v1", "-z").stdout
            index_before = (repo / ".git" / "index").read_bytes()

            observed = observe_staged_runner_target(repo)
            repeated = observe_staged_runner_target(repo)
            material = preflight_runner_material(repo, observed)
            destination = Path(temporary) / "staged-target"
            destination.mkdir()
            result = materialize_runner_target(repo, material, destination)

            self.assertEqual(observed, repeated)
            self.assertEqual((destination / "tracked.txt").read_bytes(), b"staged\n")
            self.assertFalse((destination / "untracked.txt").exists())
            self.assertEqual((repo / "tracked.txt").read_bytes(), b"unstaged\n")
            self.assertEqual((repo / "untracked.txt").read_bytes(), b"untracked\n")
            self.assertEqual((repo / ".git" / "index").read_bytes(), index_before)
            self.assertEqual(git(repo, "status", "--porcelain=v1", "-z").stdout, status_before)
            self.assertEqual(result.target_material_digest, observed.target_material_digest)

    def test_reviewed_snapshot_digest_survives_only_its_exact_successor_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            self.seed_commit(repo, {"tracked.txt": b"base\n"})
            (repo / "tracked.txt").write_bytes(b"reviewed\n")
            git(repo, "add", "tracked.txt")
            reviewed = observe_staged_runner_target(repo)
            reviewed_material = preflight_runner_material(repo, reviewed)

            commit(repo, "reviewed target")
            completion_revision = (
                git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
            )
            self.assertEqual(git(repo, "diff", "--cached", "--quiet").returncode, 0)
            successor_digest = preflight_runner_snapshot_successor_material_digest(
                repo,
                completion_revision,
                expected_base_revision=reviewed.artifact.target_base_revision,
                expected_fingerprint=reviewed.artifact.target_value,
            )
            self.assertEqual(successor_digest, reviewed_material.target_material_digest)

            (repo / "later.txt").write_bytes(b"later\n")
            git(repo, "add", "later.txt")
            commit(repo, "later descendant")
            descendant = git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
            self.assert_git_error(
                lambda: preflight_runner_snapshot_successor_material_digest(
                    repo,
                    descendant,
                    expected_base_revision=reviewed.artifact.target_base_revision,
                    expected_fingerprint=reviewed.artifact.target_value,
                ),
                "target_stale",
                TARGET_STALE_MESSAGE,
            )

    def test_existing_pure_seal_binds_actual_target_and_plan_outputs(self):
        task_id = "tg_task_0123456789abcdef"
        expectation_digest = "a" * 64
        criterion_digest = "sha256:" + "b" * 64
        raw_plan = canonical_json_bytes(
            {
                "entries": [
                    {
                        "contract_revision": 2,
                        "coverage": "full",
                        "steps": [
                            {
                                "argv": ["private-argument"],
                                "cpu_seconds": 10,
                                "cwd": ".",
                                "entrypoint": "checks/run.py",
                                "memory_mib": 64,
                                "mode": "script",
                                "output_byte_limit": 1_048_576,
                                "process_limit": 1,
                                "step_id": "focused",
                                "timeout_seconds": 20,
                            }
                        ],
                        "task_id": task_id,
                        "verification_criterion_digest": criterion_digest,
                        "verification_expectation_digest": expectation_digest,
                    }
                ],
                "plan_id": "integration-plan",
                "trusted_local": True,
                "version": 1,
            }
        )
        source = VerificationRunnerPlanSource(
            raw_blob=raw_plan,
            raw_digest="sha256:" + hashlib.sha256(raw_plan).hexdigest(),
        )
        plan = resolve_verification_runner_plan(
            source,
            task_id=task_id,
            contract_revision=2,
            verification_expectation_digest=expectation_digest,
            verification_criterion_digest=criterion_digest,
        )

        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            target_revision = self.seed_commit(repo, {"checks/run.py": b"pass\n"})
            target = observe_commit_runner_target(repo, target_revision)
            material = preflight_runner_material(repo, target)

        values = {
            "project_id": "project-1",
            "task_id": task_id,
            "contract_revision": 2,
            "authority_snapshot_id": "tg_authority_snapshot_1111111111111111",
            "verification_criterion_id": "tg_contract_criterion_2222222222222222",
            "verification_expectation_digest": expectation_digest,
            "verification_criterion_digest": criterion_digest,
            "target_kind": target.artifact.target_kind,
            "target_value": target.artifact.target_value,
            "target_base_revision": target.artifact.target_base_revision,
            "target_generation": 3,
            "target_capture_version": 1,
            "artifact_manifest_id": "tg_artifact_manifest_3333333333333333",
            "target_material_digest": material.target_material_digest,
            "plan_state": plan.plan_state,
            "plan_blob_object_id": plan.plan_blob_object_id,
            "plan_raw_digest": plan.plan_raw_digest,
            "plan_id": plan.plan_id,
            "plan_version": plan.plan_version,
            "plan_semantic_digest": plan.plan_semantic_digest,
            "selected_entry_digest": plan.selected_entry_digest,
            "coverage": plan.coverage,
            "step_count": plan.step_count,
            "runner_contract_version": RUNNER_CONTRACT_VERSION,
            "runner_implementation_version": RUNNER_IMPLEMENTATION_VERSION,
            "runner_implementation_digest": "sha256:" + "4" * 64,
            "runner_policy_digest": "sha256:" + "5" * 64,
            "sandbox_provider": None,
            "sandbox_policy_digest": None,
            "runtime_digest": None,
            "gate_eligibility_version": 0,
            "trigger": RUNNER_TRIGGER,
            "route": plan.route,
            "reason": plan.reason,
        }
        sealed = resolution_idempotency_digest(values)
        self.assertNotIn(b"private-argument", canonical_json_bytes(values))
        self.assertNotIn("steps", values)
        mutations = {
            "task_id": "tg_task_fedcba9876543210",
            "contract_revision": 3,
            "verification_expectation_digest": "c" * 64,
            "verification_criterion_digest": "sha256:" + "d" * 64,
            "target_value": "e" * len(target.artifact.target_value),
            "target_base_revision": "f" * 40,
            "target_generation": 4,
            "target_material_digest": "sha256:" + "6" * 64,
            "plan_raw_digest": "sha256:" + "7" * 64,
            "plan_semantic_digest": "sha256:" + "8" * 64,
            "selected_entry_digest": "sha256:" + "9" * 64,
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                changed = dict(values)
                changed[field] = replacement
                self.assertNotEqual(resolution_idempotency_digest(changed), sealed)

    def test_symlink_and_submodule_index_entries_are_unsupported(self):
        cases = (("120000", "symlink"), ("160000", "submodule"))
        for mode, name in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                repo = self.make_repo(temporary)
                head = self.seed_commit(repo, {"base.txt": b"base\n"})
                object_id = (
                    git(repo, "hash-object", "-w", "--stdin", input_bytes=b"base.txt")
                    .stdout.decode("ascii")
                    .strip()
                    if mode == "120000"
                    else head
                )
                git(
                    repo,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    mode,
                    object_id,
                    name,
                )
                self.assert_git_error(
                    lambda: observe_staged_runner_target(repo),
                    "target_unsupported",
                    TARGET_UNSUPPORTED_MESSAGE,
                )

    def test_sparse_index_and_material_bounds_are_classified_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            self.seed_commit(
                repo,
                {"keep/a.txt": b"keep\n", "drop/b.txt": b"drop\n"},
            )
            git(repo, "sparse-checkout", "init", "--cone", "--sparse-index")
            git(repo, "sparse-checkout", "set", "keep")
            sparse = git(repo, "ls-files", "--sparse", "--stage").stdout
            self.assertIn(b"040000", sparse)
            self.assert_git_error(
                lambda: observe_staged_runner_target(repo),
                "target_unsupported",
                TARGET_UNSUPPORTED_MESSAGE,
            )

        object_id = "1" * 40
        exact_depth = RunnerTargetEntry(
            "/".join(["d"] * (TARGET_DEPTH_LIMIT - 1) + ["f"]),
            "100644",
            object_id,
        )
        self.assertEqual(
            len(
                git_module._validate_entry_set(
                    (exact_depth,), object_format="sha1"
                )
            ),
            TARGET_DEPTH_LIMIT - 1,
        )
        over_depth = RunnerTargetEntry(
            "/".join(["d"] * TARGET_DEPTH_LIMIT + ["f"]),
            "100644",
            object_id,
        )
        self.assert_git_error(
            lambda: git_module._validate_entry_set(
                (over_depth,), object_format="sha1"
            ),
            "target_too_large",
            TARGET_ERROR_MESSAGE,
        )

        exact_files = tuple(
            RunnerTargetEntry(f"f{index:05d}", "100644", object_id)
            for index in range(TARGET_FILE_LIMIT)
        )
        self.assertEqual(
            git_module._validate_entry_set(exact_files, object_format="sha1"),
            (),
        )
        self.assert_git_error(
            lambda: git_module._validate_entry_set(
                exact_files
                + (RunnerTargetEntry("overflow", "100644", object_id),),
                object_format="sha1",
            ),
            "target_too_large",
            TARGET_ERROR_MESSAGE,
        )

        directories_per_deep_file = TARGET_DEPTH_LIMIT - 1
        full_deep_files, remainder = divmod(
            TARGET_DIRECTORY_LIMIT, directories_per_deep_file
        )
        exact_directory_entries = tuple(
            RunnerTargetEntry(
                "/".join(
                    [f"p{index:03d}"]
                    + ["d"] * (TARGET_DEPTH_LIMIT - 2)
                    + ["f"]
                ),
                "100644",
                object_id,
            )
            for index in range(full_deep_files)
        ) + (
            RunnerTargetEntry(
                "/".join(
                    [f"p{full_deep_files:03d}"]
                    + ["d"] * (remainder - 1)
                    + ["f"]
                ),
                "100644",
                object_id,
            ),
        )
        self.assertEqual(
            len(
                git_module._validate_entry_set(
                    exact_directory_entries, object_format="sha1"
                )
            ),
            TARGET_DIRECTORY_LIMIT,
        )
        self.assert_git_error(
            lambda: git_module._validate_entry_set(
                exact_directory_entries
                + (RunnerTargetEntry("z/f", "100644", object_id),),
                object_format="sha1",
            ),
            "target_too_large",
            TARGET_ERROR_MESSAGE,
        )

        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            target = self.seed_commit(repo, {"only.txt": b"bytes\n"})
            observed = observe_commit_runner_target(repo, target)
            exact_size = (
                f"{observed.entries[0].object_id} blob "
                f"{TARGET_TOTAL_BYTE_LIMIT}\n"
            ).encode("ascii")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=exact_size, stderr=b""
            )
            with mock.patch.object(
                git_module.subprocess, "run", return_value=completed
            ):
                self.assertEqual(
                    git_module._batch_object_sizes(repo, observed),
                    ((observed.entries[0].object_id, TARGET_TOTAL_BYTE_LIMIT),),
                )

            oversized = (
                f"{observed.entries[0].object_id} blob "
                f"{TARGET_TOTAL_BYTE_LIMIT + 1}\n"
            ).encode("ascii")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=oversized, stderr=b""
            )
            with mock.patch.object(
                git_module.subprocess, "run", return_value=completed
            ):
                self.assert_git_error(
                    lambda: git_module._batch_object_sizes(repo, observed),
                    "target_too_large",
                    TARGET_ERROR_MESSAGE,
                )

    def test_unsafe_and_windows_colliding_paths_are_unsupported(self):
        object_id = "1" * 40
        for path in ("../escape", "CON.txt", "bad\\name"):
            with self.subTest(path=path):
                self.assert_git_error(
                    lambda path=path: RunnerTargetEntry(path, "100644", object_id),
                    "target_unsupported",
                    TARGET_UNSUPPORTED_MESSAGE,
                )

        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            target = self.seed_commit(repo, {"base.txt": b"base\n"})
            baseline = observe_commit_runner_target(repo, target)
            collisions = (
                (
                    RunnerTargetEntry("A.txt", "100644", object_id),
                    RunnerTargetEntry("a.txt", "100644", object_id),
                ),
                (
                    RunnerTargetEntry("e\u0301.txt", "100644", object_id),
                    RunnerTargetEntry("é.txt", "100644", object_id),
                ),
                (
                    RunnerTargetEntry("a", "100644", object_id),
                    RunnerTargetEntry("a/b", "100644", object_id),
                ),
            )
            for entries in collisions:
                with self.subTest(entries=entries):
                    self.assert_git_error(
                        lambda entries=entries: RunnerTargetObservation(
                            artifact=baseline.artifact,
                            object_format=baseline.object_format,
                            entries=entries,
                            target_material_digest="sha256:" + "0" * 64,
                        ),
                        "target_unsupported",
                        TARGET_UNSUPPORTED_MESSAGE,
                    )

    def test_target_drift_and_object_loss_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            self.seed_commit(repo, {"tracked.txt": b"base\n"})
            observed = observe_staged_runner_target(repo)
            (repo / "late.txt").write_bytes(b"late\n")
            git(repo, "add", "late.txt")
            self.assert_git_error(
                lambda: preflight_runner_material(repo, observed),
                "target_stale",
                TARGET_STALE_MESSAGE,
            )

        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            target = self.seed_commit(repo, {"only.txt": b"object bytes\n"})
            observed = observe_commit_runner_target(repo, target)
            object_id = observed.entries[0].object_id
            loose_object = repo / ".git" / "objects" / object_id[:2] / object_id[2:]
            self.assertTrue(loose_object.is_file())
            loose_object.chmod(0o600)
            loose_object.unlink()
            error = self.assert_git_error(
                lambda: preflight_runner_material(repo, observed),
                "target_stale",
                TARGET_STALE_MESSAGE,
            )
            self.assertNotIn(object_id, str(error))

    def test_nonempty_and_reparse_uncertain_destinations_are_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            target = self.seed_commit(repo, {"only.txt": b"bytes\n"})
            _observed, material = self.prepare_commit_material(repo, target)

            nonempty = Path(temporary) / "nonempty"
            nonempty.mkdir()
            sentinel = nonempty / "sentinel.txt"
            sentinel.write_bytes(b"preserve")
            self.assert_git_error(
                lambda: materialize_runner_target(repo, material, nonempty),
                "materialization_failed",
                MATERIALIZATION_ERROR_MESSAGE,
            )
            self.assertEqual(sentinel.read_bytes(), b"preserve")

            uncertain = Path(temporary) / "reparse-uncertain"
            uncertain.mkdir()
            real_inspect = git_module.inspect_physical_directory

            def reject_destination(path, *args, **kwargs):
                if Path(path) == uncertain:
                    raise git_module.StatePathError()
                return real_inspect(path, *args, **kwargs)

            with mock.patch.object(
                git_module,
                "inspect_physical_directory",
                side_effect=reject_destination,
            ):
                self.assert_git_error(
                    lambda: materialize_runner_target(repo, material, uncertain),
                    "materialization_failed",
                    MATERIALIZATION_ERROR_MESSAGE,
                )
            self.assertEqual(list(uncertain.iterdir()), [])

    def test_post_write_extra_entry_is_denied_by_exact_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(temporary)
            target = self.seed_commit(repo, {"only.txt": b"bytes\n"})
            _observed, material = self.prepare_commit_material(repo, target)
            destination = Path(temporary) / "extra-entry"
            destination.mkdir()
            real_stream = git_module._stream_blob_to_file

            def stream_and_inject(repo_arg, destination_arg, entry, size, object_format):
                real_stream(repo_arg, destination_arg, entry, size, object_format)
                (destination_arg / "unexpected.txt").write_bytes(b"unexpected")

            with mock.patch.object(
                git_module,
                "_stream_blob_to_file",
                side_effect=stream_and_inject,
            ):
                self.assert_git_error(
                    lambda: materialize_runner_target(repo, material, destination),
                    "materialization_failed",
                    MATERIALIZATION_ERROR_MESSAGE,
                )
            self.assertTrue((destination / "unexpected.txt").is_file())
            self.assertEqual((repo / "only.txt").read_bytes(), b"bytes\n")

    def test_size_tamper_is_rejected_before_opening_a_file_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve() / "private"
            destination.mkdir()
            path = destination / "short.txt"
            path.write_bytes(b"")
            with mock.patch.object(git_module.os, "open") as opened:
                self.assert_git_error(
                    lambda: git_module._hash_materialized_file(
                        path,
                        destination=destination,
                        expected_size=1,
                        object_format="sha1",
                    ),
                    "materialization_failed",
                    MATERIALIZATION_ERROR_MESSAGE,
                )
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
