import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from tests.m14_test_support import (  # noqa: E402
    file_snapshot,
    initialize_taskgov_internal,
    json_payload,
    make_physical_install,
    run_taskgov_internal,
)
from task_governance_tool import review_packet as packet_module  # noqa: E402
from task_governance_tool.review_packet import (  # noqa: E402
    OVERSIZED_PACKET_MESSAGE,
    REVIEW_FOCUS,
    REQUIRED_OUTPUT,
    REVIEW_PACKET_MAX_GIT_SUBPROCESSES,
    TARGET_INSPECTION_FOCUS,
    ReviewPacketError,
    project_changed_paths,
)
from task_governance_tool.reviews import set_review_target  # noqa: E402
from task_governance_tool.storage import (  # noqa: E402
    StorageError,
    connect_initialized,
    resolve_database_target,
)


FINGERPRINT_A = "sha256:" + ("a" * 64)
FINGERPRINT_B = "sha256:" + ("b" * 64)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def initialize_repo(repo: Path) -> str:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    git(repo, "config", "user.name", "TaskGov Test")
    git(repo, "config", "user.email", "taskgov@example.invalid")
    (repo / "root.txt").write_text("root\n", encoding="utf-8")
    git(repo, "add", "root.txt")
    git(repo, "commit", "--quiet", "-m", "root")
    return git(repo, "rev-parse", "HEAD")


def add_task(
    db: Path,
    repo: Path,
    *,
    title: str = "Review packet task",
    contract: bool = True,
    description: str = "",
    tags: str = "",
) -> str:
    args = [
        "task",
        "add",
        "--db",
        str(db),
        "--repo",
        str(repo),
        "--title",
        title,
        "--description",
        description,
        "--tags",
        tags,
        "--status",
        "in_progress",
        "--review-tier",
        "2",
        "--verification",
        "python -m unittest",
        "--json",
    ]
    if contract:
        args.extend(
            [
                "--contract-scope",
                "Implement the bounded packet",
                "--contract-acceptance",
                "All packet checks pass",
                "--contract-constraints",
                "No writes or network",
                "--contract-authority-ref",
                "roadmap:M14.5",
            ]
        )
    result = run_taskgov_internal(*args, maintenance_enabled=False)
    if result.returncode != 0:
        raise AssertionError(result.stdout or result.stderr)
    return json_payload(result)["data"]["task"]["task_id"]


def set_target(
    db: Path,
    repo: Path,
    task_id: str,
    *,
    kind: str,
    revision: str | None = None,
) -> dict:
    args = [
        "review",
        "target",
        "set",
        "--db",
        str(db),
        "--repo",
        str(repo),
        task_id,
        "--kind",
        kind,
        "--json",
    ]
    if revision is not None:
        args.extend(["--revision", revision])
    result = run_taskgov_internal(*args, maintenance_enabled=False)
    if result.returncode != 0:
        raise AssertionError(result.stdout or result.stderr)
    return json_payload(result)


def prepare(db: Path, repo: Path, task_id: str, *, json_output: bool = True):
    args = [
        "review",
        "prepare",
        "--db",
        str(db),
        "--repo",
        str(repo),
        task_id,
        "--read-only",
    ]
    if json_output:
        args.append("--json")
    return run_taskgov_internal(*args, maintenance_enabled=False)


def database_target(db: Path, repo: Path):
    return resolve_database_target(
        repo=repo,
        db=db,
        script_path=SKILL_ROOT / "scripts" / "taskgov.py",
    )


class ReviewPacketTests(unittest.TestCase):
    def test_all_target_kinds_have_one_bounded_allow_list_and_fixed_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            commit = initialize_repo(repo)
            initialize_taskgov_internal(repo=repo, db=db)
            task_id = add_task(db, repo)

            set_target(
                db,
                repo,
                task_id,
                kind="diff_fingerprint",
                revision=FINGERPRINT_A,
            )
            with (
                mock.patch.object(
                    packet_module,
                    "_observe_git_snapshot",
                    side_effect=AssertionError("unexpected Git"),
                ),
                mock.patch.object(
                    packet_module,
                    "_observe_git_commit",
                    side_effect=AssertionError("unexpected Git"),
                ),
            ):
                diff_result = prepare(db, repo, task_id)
            self.assertEqual(diff_result.returncode, 0, diff_result.stdout)
            diff_data = json_payload(diff_result)["data"]
            self.assertEqual(
                tuple(diff_data),
                (
                    "task",
                    "contract",
                    "review_target",
                    "changed_paths_available",
                    "changed_paths",
                    "changed_paths_total",
                    "changed_paths_truncated",
                    "review_focus",
                    "required_output",
                    "receipt_command",
                ),
            )
            self.assertEqual(tuple(diff_data["task"]), (
                "task_id", "title", "status", "verification", "review_tier",
            ))
            self.assertEqual(tuple(diff_data["contract"]), (
                "revision", "scope", "acceptance", "constraints",
            ))
            self.assertEqual(tuple(diff_data["review_target"]), (
                "kind", "value", "base_revision", "generation",
            ))
            self.assertFalse(diff_data["changed_paths_available"])
            self.assertEqual(diff_data["changed_paths"], [])
            self.assertEqual(
                diff_data["review_focus"],
                [
                    *REVIEW_FOCUS,
                    TARGET_INSPECTION_FOCUS["diff_fingerprint"],
                ],
            )
            self.assertEqual(diff_data["required_output"], list(REQUIRED_OUTPUT))
            self.assertEqual(
                diff_data["receipt_command"],
                (
                    f"taskgov review receipt add {task_id} "
                    "--reviewer <reviewer-key> --kind independent "
                    "--verdict <pass|changes_requested> "
                    "--summary <sanitized-summary> "
                    "--reviewer-class <human|llm|deterministic_tool|hybrid|unknown> "
                    "--model-state <declared|not_applicable|unknown> "
                    "--skill-state <declared|not_applicable|not_used|unknown> "
                    "--context-relation <same_context|forked_context|fresh_context|"
                    "external_context|not_applicable|unknown> "
                    "[--declared-model-id <id>] [--declared-skill-id <id> "
                    "--declared-skill-version <version>] "
                    "[--review-profile <profile>] [--review-lens <lens>] "
                    "[--review-method <method>] --json"
                ),
            )

            text_result = prepare(
                db,
                repo,
                task_id,
                json_output=False,
            )
            expected_text = (
                f'Task: {task_id} | "Review packet task" | review_tier=2\n'
                "Status: in_progress\n"
                'Verification: "python -m unittest"\n'
                "Contract revision: 1\n"
                'Scope: "Implement the bounded packet"\n'
                'Acceptance: "All packet checks pass"\n'
                'Constraints: "No writes or network"\n'
                f"Review target: kind=diff_fingerprint value=\"{FINGERPRINT_A}\" "
                'base_revision="" generation=1\n'
                "Changed paths: unavailable\n"
                "Review focus:\n"
                "- Contract compliance\n"
                "- state-transition and completion-gate integrity\n"
                "- privacy and target-project safety\n"
                "- verification sufficiency and regression risk\n"
                "- Exact target: do not return PASS unless the orchestrator "
                "provides the exact review material plus evidence binding it "
                "to review_target.value; the fingerprint alone cannot "
                "retrieve content\n"
                "Required output:\n"
                "- verdict PASS or CHANGES_REQUESTED\n"
                "- severity-ordered findings with exact file/line\n"
                "- remaining risks\n"
                "- recommended changes\n"
                "- review provenance: reviewer class, model and Skill declaration "
                "states, context relation, profiles, lenses, and methods\n"
                f"Receipt command: {diff_data['receipt_command']}\n"
            )
            self.assertEqual(text_result.stdout, expected_text)
            escaped_data = json.loads(json.dumps(diff_data))
            escaped_data["task"]["title"] = "line\u0085next\u2028more\u2029end"
            escaped_text = packet_module.format_review_packet_text(escaped_data)
            self.assertIn(
                '"line\\u0085next\\u2028more\\u2029end"',
                escaped_text,
            )
            self.assertEqual(len(escaped_text.splitlines()), len(expected_text.splitlines()))
            self.assertNotIn("\r", text_result.stdout)

            set_target(
                db,
                repo,
                task_id,
                kind="external_revision",
                revision="release-2026-07",
            )
            external = json_payload(prepare(db, repo, task_id))["data"]
            self.assertFalse(external["changed_paths_available"])
            self.assertEqual(external["review_target"]["kind"], "external_revision")
            self.assertEqual(
                external["review_focus"][-1],
                TARGET_INSPECTION_FOCUS["external_revision"],
            )

            set_target(
                db,
                repo,
                task_id,
                kind="git_commit",
                revision=commit,
            )
            (repo / "ambient-head.txt").write_text(
                "not part of the target commit\n",
                encoding="utf-8",
            )
            git(repo, "add", "ambient-head.txt")
            git(repo, "commit", "--quiet", "-m", "ambient head")
            real_run = subprocess.run
            with mock.patch(
                "subprocess.run",
                side_effect=lambda *args, **kwargs: real_run(*args, **kwargs),
            ) as spawned:
                commit_result = prepare(db, repo, task_id)
            commit_data = json_payload(commit_result)["data"]
            self.assertEqual(commit_data["changed_paths"], ["root.txt"])
            self.assertNotIn("ambient-head.txt", commit_data["changed_paths"])
            self.assertEqual(
                commit_data["review_focus"][-1],
                TARGET_INSPECTION_FOCUS["git_commit"],
            )
            self.assertLessEqual(
                spawned.call_count,
                REVIEW_PACKET_MAX_GIT_SUBPROCESSES,
            )
            self.assertTrue(
                all(call.args[0][0] == "git" for call in spawned.call_args_list)
            )

            (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
            git(repo, "add", "staged.txt")
            set_target(db, repo, task_id, kind="git_snapshot")
            (repo / "staged.txt").write_text(
                "unstaged replacement is outside the target\n",
                encoding="utf-8",
            )
            (repo / "untracked.txt").write_text(
                "untracked content is outside the target\n",
                encoding="utf-8",
            )
            real_run = subprocess.run
            with mock.patch(
                "subprocess.run",
                side_effect=lambda *args, **kwargs: real_run(*args, **kwargs),
            ) as spawned:
                snapshot_result = prepare(db, repo, task_id)
            snapshot_data = json_payload(snapshot_result)["data"]
            self.assertEqual(snapshot_data["changed_paths"], ["staged.txt"])
            self.assertNotIn("root.txt", snapshot_data["changed_paths"])
            self.assertNotIn("untracked.txt", snapshot_data["changed_paths"])
            self.assertEqual(
                snapshot_data["review_focus"][-1],
                TARGET_INSPECTION_FOCUS["git_snapshot"],
            )
            self.assertNotIn("unstaged replacement", snapshot_result.stdout)
            self.assertNotIn("untracked content", snapshot_result.stdout)
            self.assertLessEqual(
                spawned.call_count,
                REVIEW_PACKET_MAX_GIT_SUBPROCESSES,
            )

            revision_zero_id = add_task(
                db,
                repo,
                title="Revision zero",
                contract=False,
            )
            set_target(
                db,
                repo,
                revision_zero_id,
                kind="external_revision",
                revision="external-zero",
            )
            revision_zero = json_payload(
                prepare(db, repo, revision_zero_id)
            )["data"]["contract"]
            self.assertEqual(
                revision_zero,
                {
                    "revision": 0,
                    "scope": "",
                    "acceptance": "",
                    "constraints": "",
                },
            )

    def test_git_commit_root_and_merge_use_empty_tree_and_first_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            root_commit = initialize_repo(repo)
            initialize_taskgov_internal(repo=repo, db=db)
            task_id = add_task(db, repo)

            set_target(
                db,
                repo,
                task_id,
                kind="git_commit",
                revision=root_commit,
            )
            root_packet = json_payload(prepare(db, repo, task_id))["data"]
            self.assertEqual(root_packet["changed_paths"], ["root.txt"])

            main_branch = git(repo, "branch", "--show-current")
            git(repo, "checkout", "--quiet", "-b", "side", root_commit)
            (repo / "side.txt").write_text("side\n", encoding="utf-8")
            git(repo, "add", "side.txt")
            git(repo, "commit", "--quiet", "-m", "side")
            git(repo, "checkout", "--quiet", main_branch)
            (repo / "first-parent.txt").write_text("first\n", encoding="utf-8")
            git(repo, "add", "first-parent.txt")
            git(repo, "commit", "--quiet", "-m", "first parent")
            git(repo, "merge", "--quiet", "--no-ff", "--no-edit", "side")
            merge_commit = git(repo, "rev-parse", "HEAD")

            set_target(
                db,
                repo,
                task_id,
                kind="git_commit",
                revision=merge_commit,
            )
            merge_packet = json_payload(prepare(db, repo, task_id))["data"]
            self.assertEqual(merge_packet["changed_paths"], ["side.txt"])

    def test_path_projection_enforces_bytewise_and_all_three_bounds(self):
        paths, total, truncated = project_changed_paths(
            [b"z.txt", "ä.txt".encode("utf-8"), b"a.txt"]
        )
        self.assertEqual(paths, ["a.txt", "z.txt", "ä.txt"])
        self.assertEqual((total, truncated), (3, False))

        exact_240 = "界".encode("utf-8") * 80
        over_240 = exact_240 + b"a"
        paths, total, truncated = project_changed_paths(
            [exact_240, over_240]
        )
        self.assertEqual(paths, ["界" * 80])
        self.assertEqual((total, truncated), (2, True))

        count_paths = [f"{index:03d}.txt".encode() for index in range(101)]
        paths, total, truncated = project_changed_paths(count_paths)
        self.assertEqual(len(paths), 100)
        self.assertEqual((total, truncated), (101, True))

        def sized_path(index: int, size: int) -> bytes:
            prefix = f"{index:03d}-".encode()
            return prefix + (b"x" * (size - len(prefix)))

        exact_aggregate = [
            sized_path(index, 164 if index < 84 else 163)
            for index in range(100)
        ]
        paths, total, truncated = project_changed_paths(exact_aggregate)
        self.assertEqual(sum(len(path.encode()) for path in paths), 16_384)
        self.assertEqual((len(paths), total, truncated), (100, 100, False))

        over_aggregate = [*exact_aggregate[:-1], sized_path(99, 164)]
        paths, total, truncated = project_changed_paths(over_aggregate)
        self.assertLessEqual(
            sum(len(path.encode()) for path in paths),
            16_384,
        )
        self.assertEqual((total, truncated), (100, True))

    def test_unsafe_paths_fail_exactly_without_echo_or_partial_packet(self):
        unsafe_paths = (
            b"",
            b"\xff.txt",
            b"/absolute.txt",
            b"C:/drive.txt",
            b"C:drive-relative.txt",
            b"a\\b.txt",
            b"./dot.txt",
            b"a/../escape.txt",
            b"a//empty.txt",
            b"line\nbreak.txt",
            "line\u2028break.txt".encode("utf-8"),
            "line\u2029break.txt".encode("utf-8"),
        )
        for raw_path in unsafe_paths:
            with self.subTest(raw_path=raw_path):
                with self.assertRaises(ReviewPacketError) as raised:
                    project_changed_paths([raw_path])
                self.assertEqual(
                    (
                        raised.exception.code,
                        raised.exception.message,
                    ),
                    (
                        "review_packet_path_unsafe",
                        "review packet contains an unsafe project path",
                    ),
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            initialize_repo(repo)
            initialize_taskgov_internal(repo=repo, db=db)
            task_id = add_task(db, repo)
            set_target(
                db,
                repo,
                task_id,
                kind="diff_fingerprint",
                revision=FINGERPRINT_A,
            )
            with mock.patch.object(
                packet_module,
                "_observe_target",
                return_value=(True, (b"../SECRET-PATH-SENTINEL",)),
            ):
                result = prepare(db, repo, task_id)
            payload = json_payload(result)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(payload["data"], {})
            self.assertEqual(
                payload["errors"],
                [
                    {
                        "code": "review_packet_path_unsafe",
                        "message": (
                            "review packet contains an unsafe project path"
                        ),
                    }
                ],
            )
            self.assertNotIn("SECRET-PATH-SENTINEL", result.stdout)
            self.assertNotIn("changed_paths", result.stdout)

    def test_packet_size_failure_is_exact_and_emits_no_partial_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            initialize_repo(repo)
            initialize_taskgov_internal(repo=repo, db=db)
            result = run_taskgov_internal(
                "task",
                "add",
                "--db",
                str(db),
                "--repo",
                str(repo),
                "--title",
                "題" * 200,
                "--status",
                "in_progress",
                "--review-tier",
                "2",
                "--verification",
                "検" * 500,
                "--contract-scope",
                "範" * 4000,
                "--contract-acceptance",
                "受" * 4000,
                "--contract-constraints",
                "制" * 2000,
                "--contract-authority-ref",
                "roadmap:M14.5-size",
                "--json",
                maintenance_enabled=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            task_id = json_payload(result)["data"]["task"]["task_id"]
            set_target(
                db,
                repo,
                task_id,
                kind="external_revision",
                revision="release-size",
            )

            for json_output in (False, True):
                with self.subTest(json_output=json_output):
                    packet = prepare(
                        db,
                        repo,
                        task_id,
                        json_output=json_output,
                    )
                    self.assertEqual(packet.returncode, 1)
                    rendered = packet.stdout + packet.stderr
                    self.assertIn(OVERSIZED_PACKET_MESSAGE, rendered)
                    self.assertNotIn("範範範", rendered)
                    self.assertNotIn("changed_paths", rendered)
                    if json_output:
                        payload = json_payload(packet)
                        self.assertEqual(payload["data"], {})
                        self.assertEqual(
                            payload["errors"][0]["code"],
                            "review_packet_too_large",
                        )

    def test_revalidation_is_lock_free_and_stale_precedes_observation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            initialize_repo(repo)
            initialize_taskgov_internal(repo=repo, db=db)
            task_id = add_task(db, repo)
            set_target(
                db,
                repo,
                task_id,
                kind="diff_fingerprint",
                revision=FINGERPRINT_A,
            )
            target = database_target(db, repo)

            def mutate_target(*_args, **_kwargs):
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        set_review_target(
                            connection,
                            target.project,
                            task_id,
                            kind="diff_fingerprint",
                            revision=FINGERPRINT_B,
                            database_target=target,
                        )
                raise ReviewPacketError(
                    "review_packet_path_unsafe",
                    "review packet contains an unsafe project path",
                )

            with mock.patch.object(
                packet_module,
                "_observe_target",
                side_effect=mutate_target,
            ):
                result = prepare(db, repo, task_id)
            payload = json_payload(result)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                payload["errors"],
                [
                    {
                        "code": "review_packet_stale",
                        "message": (
                            "review context changed while preparing the packet"
                        ),
                    }
                ],
            )

    def test_revalidation_normalizes_identity_and_invalid_context_to_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            initialize_repo(repo)
            initialize_taskgov_internal(repo=repo, db=db)
            task_id = add_task(db, repo)
            set_target(
                db,
                repo,
                task_id,
                kind="diff_fingerprint",
                revision=FINGERPRINT_A,
            )

            real_connect = packet_module.connect_initialized_readonly
            connect_count = 0

            def identity_changed(target):
                nonlocal connect_count
                connect_count += 1
                if connect_count == 2:
                    raise StorageError(
                        "project_mismatch",
                        "database belongs to a different project",
                    )
                return real_connect(target)

            with mock.patch.object(
                packet_module,
                "connect_initialized_readonly",
                side_effect=identity_changed,
            ):
                identity_result = prepare(db, repo, task_id)
            self.assertEqual(
                json_payload(identity_result)["errors"][0]["code"],
                "review_packet_stale",
            )

            real_read_task = packet_module.read_internal_task
            read_count = 0

            def context_invalid(*args, **kwargs):
                nonlocal read_count
                read_count += 1
                stored = real_read_task(*args, **kwargs)
                if read_count == 2 and stored is not None:
                    stored = dict(stored)
                    stored["review_target_generation"] = 0
                return stored

            with mock.patch.object(
                packet_module,
                "read_internal_task",
                side_effect=context_invalid,
            ):
                invalid_result = prepare(db, repo, task_id)
            invalid_payload = json_payload(invalid_result)
            self.assertEqual(
                invalid_payload["errors"],
                [
                    {
                        "code": "review_packet_stale",
                        "message": (
                            "review context changed while preparing the packet"
                        ),
                    }
                ],
            )

    def test_missing_foreign_and_omitted_private_rows_are_safe_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            foreign = root / "foreign"
            db = root / "taskgov.sqlite"
            initialize_repo(repo)
            foreign.mkdir()
            initialize_taskgov_internal(repo=repo, db=db)
            task_id = add_task(
                db,
                repo,
                description="OMITTED-DESCRIPTION-SENTINEL",
                tags="OMITTED-TAGS-SENTINEL",
            )

            missing = prepare(db, repo, task_id)
            missing_payload = json_payload(missing)
            self.assertEqual(
                missing_payload["errors"],
                [
                    {
                        "code": "review_target_missing",
                        "message": (
                            "review target is required before preparing a "
                            "review packet"
                        ),
                    }
                ],
            )

            set_target(
                db,
                repo,
                task_id,
                kind="diff_fingerprint",
                revision=FINGERPRINT_A,
            )
            receipt = run_taskgov_internal(
                "review",
                "receipt",
                "add",
                "--db",
                str(db),
                "--repo",
                str(repo),
                task_id,
                "--reviewer",
                "reviewer-a",
                "--kind",
                "independent",
                "--verdict",
                "pass",
                "--summary",
                "OMITTED-RECEIPT-SENTINEL",
                "--reviewer-class",
                "human",
                "--model-state",
                "not_applicable",
                "--skill-state",
                "not_applicable",
                "--context-relation",
                "external_context",
                "--json",
                maintenance_enabled=False,
            )
            self.assertEqual(receipt.returncode, 0, receipt.stdout)
            before_db = db.read_bytes()
            before_repo = file_snapshot(repo)
            result = prepare(db, repo, task_id)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(db.read_bytes(), before_db)
            self.assertEqual(file_snapshot(repo), before_repo)
            for sentinel in (
                "OMITTED-DESCRIPTION-SENTINEL",
                "OMITTED-TAGS-SENTINEL",
                "OMITTED-RECEIPT-SENTINEL",
                str(repo.resolve()),
                str(db.resolve()),
            ):
                self.assertNotIn(sentinel, result.stdout)

            foreign_result = prepare(db, foreign, task_id)
            foreign_payload = json_payload(foreign_result)
            self.assertEqual(foreign_result.returncode, 2)
            self.assertEqual(
                foreign_payload["errors"][0]["code"],
                "project_mismatch",
            )

    def test_snapshot_change_after_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            initialize_repo(repo)
            initialize_taskgov_internal(repo=repo, db=db)
            task_id = add_task(db, repo)
            (repo / "first.txt").write_text("first\n", encoding="utf-8")
            git(repo, "add", "first.txt")
            set_target(db, repo, task_id, kind="git_snapshot")
            (repo / "second.txt").write_text("second\n", encoding="utf-8")
            git(repo, "add", "second.txt")

            result = prepare(db, repo, task_id)
            payload = json_payload(result)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                payload["errors"][0]["code"],
                "review_target_mismatch",
            )
            self.assertNotIn("expected", result.stdout.lower())
            self.assertNotIn("actual", result.stdout.lower())

    def test_public_text_is_utf8_with_one_lf_and_no_cr_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stderr)
            added = install.run(
                "task",
                "add",
                "--title",
                "Packet 🧭",
                "--status",
                "in_progress",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            targeted = install.run(
                "review",
                "target",
                "set",
                task_id,
                "--kind",
                "external_revision",
                "--revision",
                "release-lf",
                "--json",
            )
            self.assertEqual(targeted.returncode, 0, targeted.stderr)

            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(install.entrypoint),
                    "review",
                    "prepare",
                    task_id,
                    "--read-only",
                ],
                cwd=install.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Packet 🧭".encode("utf-8"), result.stdout)
            self.assertTrue(result.stdout.endswith(b"\n"))
            self.assertFalse(result.stdout.endswith(b"\n\n"))
            self.assertNotIn(b"\r", result.stdout)


if __name__ == "__main__":
    unittest.main()
