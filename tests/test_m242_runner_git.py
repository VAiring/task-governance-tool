import hashlib
import json
import os
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
    import task_governance_tool.verification_runner_git as runner_git
    import task_governance_tool.verification_runner_lifecycle as runner_lifecycle
    from task_governance_tool.artifact_manifest import (
        ArtifactLeaf,
        ArtifactObservation,
        GitArtifactObservation,
    )
    from task_governance_tool.state_paths import verification_runner_state_paths
    from task_governance_tool.verification_runner_git import (
        MATERIALIZATION_DIGEST_DOMAIN,
        VerificationRunnerGitError,
        materialize_runner_target,
        observe_commit_runner_target,
        observe_staged_runner_target,
        preflight_runner_material,
        prove_materialized_runner_target,
    )
    from task_governance_tool.verification_runner_lifecycle import (
        create_attempt_directories,
        inspect_runner_layout,
        remove_attempt_tree,
        zero_wait_runner_lock,
    )
finally:
    sys.path.pop(0)


PLAN_PATH = "skill/config/verification-runner.json"
PLAN_BYTES = b'{"schema_version":1,"profile":"verification-runner-v1"}\n'


def git(repo: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout


def init_repo(root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    (repo / "skill" / "config").mkdir(parents=True)
    (repo / PLAN_PATH).write_bytes(PLAN_BYTES)
    (repo / "tracked.txt").write_bytes(b"committed\n")
    git(repo, "add", "--", PLAN_PATH, "tracked.txt")
    git(
        repo,
        "-c",
        "user.name=TaskGov Test",
        "-c",
        "user.email=taskgov@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "base",
    )
    return repo, git(repo, "rev-parse", "HEAD").decode("ascii").strip()


def all_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def observation_for_paths(paths: tuple[str, ...]) -> GitArtifactObservation:
    artifact = ArtifactObservation(
        state="complete_git",
        object_format="sha1",
        comparison_base="1" * 40,
        target_kind="git_commit",
        target_value="2" * 40,
        target_base_revision="",
    )
    return GitArtifactObservation(
        artifact,
        tuple(
            ArtifactLeaf(path, "100644", "3" * 40)
            for path in sorted(paths, key=lambda value: value.encode("utf-8"))
        ),
    )


class VerificationRunnerGitTests(unittest.TestCase):
    def test_target_and_runtime_envelopes_fit_cleanup_capacity(self):
        fixed_directories = (
            1
            + len(runner_lifecycle._EXPECTED_ATTEMPT_CHILDREN)
            + len(runner_lifecycle._EXPECTED_SCRATCH_CHILDREN)
        )
        self.assertEqual(runner_git.RUNNER_TARGET_PATH_COMPONENT_LIMIT, 64)
        self.assertEqual(
            runner_git.RUNNER_TARGET_PATH_COMPONENT_LIMIT,
            runner_lifecycle._MAX_ATTEMPT_DEPTH,
        )
    def test_materialized_child_admission_counts_files_and_unique_parents(self):
        root_files = tuple(f"f{ordinal:05d}" for ordinal in range(9_998))
        exact_boundary = (*root_files, "z/leaf")
        self.assertTrue(runner_git._materialized_shape_supported(exact_boundary))
        self.assertFalse(
            runner_git._materialized_shape_supported((*exact_boundary, "zz"))
        )

        with mock.patch.object(
            runner_git,
            "RUNNER_MATERIALIZED_CHILD_ENTRY_LIMIT",
            4,
        ):
            accepted = runner_git._validate_inventory(
                observation_for_paths(("a", "b", "z/leaf")),
                plan_relative_path=PLAN_PATH,
            )
            self.assertEqual(len(accepted.inventory), 3)
            with self.assertRaises(VerificationRunnerGitError) as raised:
                runner_git._validate_inventory(
                    observation_for_paths(("a", "b", "c", "z/leaf")),
                    plan_relative_path=PLAN_PATH,
                )
        self.assertEqual(raised.exception.code, "unsupported_target")

    def test_target_path_component_limit_matches_cleanup_depth(self):
        accepted_path = "/".join((*(("a",) * 63), "leaf"))
        rejected_path = "/".join((*(("a",) * 64), "leaf"))
        accepted = runner_git._validate_inventory(
            observation_for_paths((accepted_path,)),
            plan_relative_path=PLAN_PATH,
        )
        self.assertEqual(accepted.inventory[0].path, accepted_path)

        with self.assertRaises(VerificationRunnerGitError) as raised:
            runner_git._validate_inventory(
                observation_for_paths((rejected_path,)),
                plan_relative_path=PLAN_PATH,
            )
        self.assertEqual(raised.exception.code, "unsupported_target")

    def test_depth_boundary_materializes_proves_and_is_cleanup_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = init_repo(root)
            relative = "/".join((*(("a",) * 63), "boundary.py"))
            source = repo.joinpath(*relative.split("/"))
            source.parent.mkdir(parents=True)
            source.write_bytes(b"raise SystemExit(0)\n")
            git(repo, "add", "--", relative)

            target = observe_staged_runner_target(
                repo,
                plan_relative_path=PLAN_PATH,
            )
            material = preflight_runner_material(repo, target)
            fixed_root = root / "current"
            fixed_root.mkdir()
            paths = verification_runner_state_paths(fixed_root)
            attempt_id = "tg_verification_runner_attempt_0123456789abcdef"
            with zero_wait_runner_lock(paths):
                attempt_paths = create_attempt_directories(paths, attempt_id)
                materialized = materialize_runner_target(
                    repo,
                    material,
                    attempt_paths.target,
                )
                self.assertIs(
                    prove_materialized_runner_target(materialized),
                    materialized,
                )
                remove_attempt_tree(paths, attempt_id)
                self.assertEqual(inspect_runner_layout(paths).attempt_ids, ())

    def test_staged_inventory_plan_and_materialization_exclude_ambient_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = init_repo(root)
            staged_plan = PLAN_BYTES.replace(b"}\n", b',"enabled":true}\n')
            (repo / PLAN_PATH).write_bytes(staged_plan)
            (repo / "staged.txt").write_bytes(b"staged bytes\n")
            git(repo, "add", "--", PLAN_PATH, "staged.txt")
            (repo / PLAN_PATH).write_bytes(b"ambient plan must not run\n")
            (repo / "staged.txt").write_bytes(b"unstaged bytes\n")
            (repo / "untracked.txt").write_bytes(b"untracked bytes\n")

            target = observe_staged_runner_target(repo, plan_relative_path=PLAN_PATH)
            material = preflight_runner_material(repo, target)
            destination = root / "private-target"
            destination.mkdir()
            result = materialize_runner_target(repo, material, destination)

            files = all_files(destination)
            self.assertEqual(files[PLAN_PATH], staged_plan)
            self.assertEqual(files["staged.txt"], b"staged bytes\n")
            self.assertEqual(files["tracked.txt"], b"committed\n")
            self.assertNotIn("untracked.txt", files)
            self.assertFalse(any(part.casefold() == ".git" for path in files for part in path.split("/")))
            self.assertEqual(material.plan_raw_blob, staged_plan)
            self.assertEqual(
                material.plan_raw_digest,
                "sha256:" + hashlib.sha256(staged_plan).hexdigest(),
            )
            self.assertEqual(result.target_material_digest, material.target_material_digest)

            canonical = {
                "format_version": 1,
                "entries": [entry.canonical_value() for entry in material.entries],
            }
            expected = "sha256:" + hashlib.sha256(
                MATERIALIZATION_DIGEST_DOMAIN
                + json.dumps(
                    canonical,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(material.target_material_digest, expected)

    def test_commit_target_ignores_later_worktree_and_index_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit_id = init_repo(root)
            (repo / PLAN_PATH).write_bytes(b"ambient changed plan\n")
            (repo / "later.txt").write_bytes(b"later\n")
            git(repo, "add", "--", PLAN_PATH, "later.txt")

            target = observe_commit_runner_target(
                repo,
                commit_id,
                plan_relative_path=PLAN_PATH,
            )
            material = preflight_runner_material(repo, target)
            destination = root / "commit-target"
            destination.mkdir()
            materialize_runner_target(repo, material, destination)

            files = all_files(destination)
            self.assertEqual(files[PLAN_PATH], PLAN_BYTES)
            self.assertNotIn("later.txt", files)
            self.assertEqual(target.artifact.target_value, commit_id)

    def test_duplicate_blob_is_streamed_once_and_materialized_as_distinct_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = init_repo(root)
            shared = b"same object\n"
            (repo / "a.txt").write_bytes(shared)
            (repo / "b.txt").write_bytes(shared)
            git(repo, "add", "--", "a.txt", "b.txt")
            target = observe_staged_runner_target(repo, plan_relative_path=PLAN_PATH)
            calls: list[tuple[str, ...]] = []
            original = runner_git._stream_git_blobs

            def record(repo_path, object_ids, **kwargs):
                calls.append(object_ids)
                return original(repo_path, object_ids, **kwargs)

            with mock.patch.object(runner_git, "_stream_git_blobs", side_effect=record):
                material = preflight_runner_material(repo, target)
                destination = root / "duplicate-target"
                destination.mkdir()
                materialize_runner_target(repo, material, destination)

            shared_oid = next(entry.object_id for entry in target.inventory if entry.path == "a.txt")
            self.assertTrue(all(call.count(shared_oid) == 1 for call in calls))
            self.assertEqual((destination / "a.txt").read_bytes(), shared)
            self.assertEqual((destination / "b.txt").read_bytes(), shared)
            self.assertFalse(os.path.samefile(destination / "a.txt", destination / "b.txt"))

    def test_target_drift_is_detected_before_any_materialization_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = init_repo(root)
            target = observe_staged_runner_target(repo, plan_relative_path=PLAN_PATH)
            material = preflight_runner_material(repo, target)
            (repo / "drift.txt").write_bytes(b"drift\n")
            git(repo, "add", "--", "drift.txt")
            destination = root / "target"
            destination.mkdir()

            with self.assertRaises(VerificationRunnerGitError) as raised:
                materialize_runner_target(repo, material, destination)

            self.assertEqual(raised.exception.code, "target_drift")
            self.assertEqual(list(destination.iterdir()), [])

    def test_final_private_target_proof_rehashes_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = init_repo(root)
            target = observe_staged_runner_target(repo, plan_relative_path=PLAN_PATH)
            material = preflight_runner_material(repo, target)
            destination = root / "target"
            destination.mkdir()
            materialized = materialize_runner_target(repo, material, destination)

            self.assertIs(prove_materialized_runner_target(materialized), materialized)
            first = destination.joinpath(*material.entries[0].path.split("/"))
            first.write_bytes(b"post-launch drift\n")
            with self.assertRaises(VerificationRunnerGitError) as raised:
                prove_materialized_runner_target(materialized)
            self.assertEqual(raised.exception.code, "materialization_failed")

    def test_final_target_proof_stops_at_unexpected_fanout_sentinel_without_mutation(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = init_repo(root)
            target = observe_staged_runner_target(repo, plan_relative_path=PLAN_PATH)
            material = preflight_runner_material(repo, target)
            destination = root / "target"
            destination.mkdir()
            materialized = materialize_runner_target(repo, material, destination)
            extras = tuple(
                destination / f"unexpected-{ordinal:02d}.txt"
                for ordinal in range(8)
            )
            for extra in extras:
                extra.write_bytes(b"must remain unchanged\n")
            before = all_files(destination)
            real_scandir = os.scandir
            root_yields = 0
            expected_root_children = len(
                {entry.path.split("/", 1)[0] for entry in materialized.entries}
            )

            class LazyScandir:
                def __init__(self, directory):
                    self.directory = Path(directory)
                    self.iterator = real_scandir(directory)

                def __enter__(self):
                    self.iterator.__enter__()
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return self.iterator.__exit__(exc_type, exc, traceback)

                def __iter__(self):
                    return self

                def __next__(self):
                    nonlocal root_yields
                    entry = next(self.iterator)
                    if self.directory == destination:
                        root_yields += 1
                        if root_yields > expected_root_children + 1:
                            raise AssertionError(
                                "proof enumerated beyond the cap sentinel"
                            )
                    return entry

            with (
                mock.patch.object(
                    runner_git.os,
                    "scandir",
                    side_effect=LazyScandir,
                ),
                self.assertRaises(VerificationRunnerGitError) as raised,
            ):
                prove_materialized_runner_target(materialized)

            self.assertEqual(raised.exception.code, "materialization_failed")
            self.assertEqual(root_yields, expected_root_children + 1)
            self.assertEqual(all_files(destination), before)

    def test_final_target_proof_checks_deadline_after_each_lazy_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "deadline-target"
            directory.mkdir()
            (directory / "first").write_bytes(b"first\n")
            (directory / "second").write_bytes(b"second\n")
            before = all_files(directory)
            real_scandir = os.scandir
            reads = 0

            class LazyScandir:
                def __init__(self, path):
                    self.iterator = real_scandir(path)

                def __enter__(self):
                    self.iterator.__enter__()
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return self.iterator.__exit__(exc_type, exc, traceback)

                def __iter__(self):
                    return self

                def __next__(self):
                    nonlocal reads
                    entry = next(self.iterator)
                    reads += 1
                    if reads > 1:
                        raise AssertionError("deadline did not stop enumeration")
                    return entry

            budget = runner_git._MaterializedTraversalBudget(
                maximum_entries=10,
                deadline=100.0,
            )
            with (
                mock.patch.object(
                    runner_git.os,
                    "scandir",
                    side_effect=LazyScandir,
                ),
                mock.patch.object(
                    runner_git.time,
                    "monotonic",
                    side_effect=(99.0, 101.0),
                ),
                self.assertRaises(VerificationRunnerGitError) as raised,
            ):
                runner_git._bounded_materialized_children(
                    directory,
                    budget=budget,
                    maximum_items=10,
                )
            self.assertEqual(raised.exception.code, "materialization_failed")
            self.assertEqual(reads, 1)
            self.assertEqual(budget.observed_entries, 0)
            self.assertEqual(all_files(directory), before)

    def test_runner_rejects_modes_case_collisions_and_git_metadata_paths(self):
        artifact = ArtifactObservation(
            state="complete_git",
            object_format="sha1",
            comparison_base="1" * 40,
            target_kind="git_commit",
            target_value="2" * 40,
            target_base_revision="",
        )
        cases = (
            (
                ArtifactLeaf("link", "120000", "3" * 40),
            ),
            (
                ArtifactLeaf("A.txt", "100644", "3" * 40),
                ArtifactLeaf("a.txt", "100644", "4" * 40),
            ),
            (
                ArtifactLeaf(".GIT/config", "100644", "3" * 40),
            ),
            (
                ArtifactLeaf("e\u0301.txt", "100644", "3" * 40),
            ),
        )
        for inventory in cases:
            with self.subTest(inventory=inventory):
                observed = GitArtifactObservation(artifact, inventory)
                with self.assertRaises(VerificationRunnerGitError) as raised:
                    runner_git._validate_inventory(observed, plan_relative_path=PLAN_PATH)
                self.assertEqual(raised.exception.code, "unsupported_target")

    def test_entry_blob_total_and_plan_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = init_repo(root)
            (repo / "second.txt").write_bytes(b"second\n")
            git(repo, "add", "--", "second.txt")
            with mock.patch.object(runner_git, "RUNNER_ENTRY_LIMIT", 1):
                with self.assertRaises(VerificationRunnerGitError) as raised:
                    observe_staged_runner_target(repo, plan_relative_path=PLAN_PATH)
                self.assertEqual(raised.exception.code, "unsupported_target")

            target = observe_staged_runner_target(repo, plan_relative_path=PLAN_PATH)
            with mock.patch.object(runner_git, "RUNNER_BLOB_BYTE_LIMIT", 1):
                with self.assertRaises(VerificationRunnerGitError) as raised:
                    preflight_runner_material(repo, target)
                self.assertEqual(raised.exception.code, "unsupported_target")
            with mock.patch.object(runner_git, "RUNNER_TOTAL_BLOB_BYTE_LIMIT", 1):
                with self.assertRaises(VerificationRunnerGitError) as raised:
                    preflight_runner_material(repo, target)
                self.assertEqual(raised.exception.code, "unsupported_target")

    def test_oversized_plan_is_not_downgraded_to_unsupported_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = init_repo(root)
            oversized = b"{" + b" " * runner_git.PLAN_BLOB_BYTE_LIMIT
            (repo / PLAN_PATH).write_bytes(oversized)
            git(repo, "add", "--", PLAN_PATH)
            target = observe_staged_runner_target(repo, plan_relative_path=PLAN_PATH)

            with (
                mock.patch.object(
                    runner_git,
                    "RUNNER_BLOB_BYTE_LIMIT",
                    runner_git.PLAN_BLOB_BYTE_LIMIT,
                ),
                self.assertRaises(VerificationRunnerGitError) as raised,
            ):
                preflight_runner_material(repo, target)

            self.assertEqual(raised.exception.code, "plan_invalid")
            self.assertEqual(
                str(raised.exception),
                "verification Runner plan is too large",
            )
            self.assertNotIn(str(repo), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
