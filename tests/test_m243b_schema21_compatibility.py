from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing, contextmanager
from copy import deepcopy
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from tests.evidence_test_support import (
    EVIDENCE_REFERENCE_DOMAIN,
    domain_digest,
    reference_json_bytes,
    refresh_bundle_seals,
    valid_native_payload,
)
from tests.m14_test_support import (
    make_physical_install,
    refresh_test_manifest,
    tree_snapshot,
)

from tests.evidence_reader_oracle import (
    EvidenceConsumerError,
    ValidatedEvidenceSource,
    read_evidence_index,
    validate_evidence_source,
)
from task_governance_tool.evidence_ledger import (
    EvidenceLedgerError,
    EvidenceSource,
    TargetCaptureBinding,
    build_evidence_reference,
)
from task_governance_tool.evidence_projection import (
    BUNDLE_V2_DOMAIN,
    EvidenceProjectionError,
    build_bundle_artifact,
    build_index_artifact,
    build_projection_bundle_artifact,
    publish_setup_evidence_projection,
)
from task_governance_tool.relocation import RelocationContext, RelocationTokenError
from task_governance_tool import storage as storage_module
from task_governance_tool.storage import (
    CompletionCycle,
    CompletionGateBasis,
    verification_expectation_digest,
)
from task_governance_tool.verification_runner import (
    RUNNER_CONTRACT_VERSION,
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_POLICY_DIGEST,
    RUNNER_TRIGGER,
    resolution_idempotency_digest,
    runner_observation_source_projection,
    verification_runner_attempt_digest,
    verification_runner_observation_digest,
    verification_runner_sandbox_event_digest,
)
from task_governance_tool.verification_runner_runtime import (
    capture_runner_implementation,
)
from task_governance_tool.verification_receipts import (
    VerificationGate,
    VerificationReceiptError,
    VerificationReceiptInput,
    _gate_from_exact,
    _validate_add_basis,
    _validate_done_cycle,
    read_verification_evidence,
)
from task_governance_tool.viewer import build_viewer_snapshot


@contextmanager
def _schema21_runtime():
    """Keep historical schema21 service fixtures at their original boundary."""
    from tests.test_m242_r3b_schema20_activation import _SCHEMA20_RUNTIME_PATCH_TARGETS

    with ExitStack() as stack:
        for target in _SCHEMA20_RUNTIME_PATCH_TARGETS:
            stack.enter_context(mock.patch(target, 21))
        yield


_SCHEMA21_RUNTIME = None


def setUpModule() -> None:
    global _SCHEMA21_RUNTIME
    _SCHEMA21_RUNTIME = _schema21_runtime()
    _SCHEMA21_RUNTIME.__enter__()


def tearDownModule() -> None:
    global _SCHEMA21_RUNTIME
    _SCHEMA21_RUNTIME.__exit__(None, None, None)
    _SCHEMA21_RUNTIME = None


def _physical_current21_install(root: Path, *, git_managed: bool = False):
    """Pin this copied package only; never change the public current runtime."""
    install = make_physical_install(root, git_managed=git_managed)
    installed_storage = install.skill_root / "scripts" / "task_governance_tool" / "storage.py"
    source = installed_storage.read_text(encoding="utf-8")
    current_declaration = "SCHEMA_VERSION = 22"
    if source.count(current_declaration) != 1:
        raise AssertionError("schema21 fixture requires the exact current declaration")
    installed_storage.write_text(
        source.replace(current_declaration, "SCHEMA_VERSION = 21", 1),
        encoding="utf-8", newline="\n",
    )
    refresh_test_manifest(install.skill_root)
    return install


def _source21_not_required_payload() -> dict[str, object]:
    payload = deepcopy(valid_native_payload())
    payload["source_schema_version"] = 21
    payload["bundle_version"] = 2
    payload["task"]["verification"] = ""
    payload["criteria"] = [
        row for row in payload["criteria"] if row["kind"] != "verification"
    ]
    payload["criterion_links"] = [
        row
        for row in payload["criterion_links"]
        if row["relation"] != "verification_attestation"
    ]
    payload["evidence_references"] = [
        row
        for row in payload["evidence_references"]
        if row["source_kind"] != "verification_receipt"
    ]
    for reference in payload["evidence_references"]:
        reference["verification_criterion_id"] = None
    payload["verification_receipt"] = None
    payload["omissions"] = ["verification_criterion_absent"]
    payload["verification_basis"] = {
        "basis_version": 1,
        "kind": "not_required",
        "runner_observation_id": None,
        "verification_receipt_id": None,
    }
    payload["runner_observation"] = None
    refresh_bundle_seals(payload)
    return payload


def _runner_projection() -> dict[str, object]:
    return {
        "observation_id": "tg_verification_runner_observation_1212121212121212",
        "gate_eligibility_version": 1,
        "route": "runner",
        "reason": None,
        "outcome": "pass",
        "launch_state": "launched",
        "complete_plan": 1,
        "total_step_count": 1,
        "completed_step_count": 1,
        "failed_step_ordinal": None,
        "started_at": "2026-08-28T00:00:00Z",
        "finished_at": "2026-08-28T00:00:01Z",
        "duration_ms": 1000,
        "cpu_time_ms": 10,
        "peak_job_memory_bytes": 4096,
        "total_process_count": 1,
        "plan_blob_object_id": None,
        "plan_raw_digest": "sha256:" + "1" * 64,
        "plan_id": "trusted-local-plan",
        "plan_version": 1,
        "plan_semantic_digest": "sha256:" + "2" * 64,
        "runner_implementation_version": "taskgov-verification-runner/1",
        "runner_implementation_digest": "sha256:" + "3" * 64,
        "runner_policy_digest": "sha256:" + "4" * 64,
        "runtime_digest": None,
        "sanitized_result_digest": "sha256:" + "5" * 64,
    }


def _source21_runner_payload() -> dict[str, object]:
    payload = deepcopy(valid_native_payload())
    payload["source_schema_version"] = 21
    payload["bundle_version"] = 2
    payload["verification_receipt"] = None
    runner = _runner_projection()
    payload["verification_basis"] = {
        "basis_version": 1,
        "kind": "runner_observation",
        "runner_observation_id": runner["observation_id"],
        "verification_receipt_id": None,
    }
    payload["runner_observation"] = runner

    reference = next(
        row
        for row in payload["evidence_references"]
        if row["source_kind"] == "verification_receipt"
    )
    reference.update(
        {
            "source_kind": "runner_observation",
            "source_state": "recorded",
            "source_id": runner["observation_id"],
            "assurance_class": "machine_observed",
            "producer_class": "verification_runner",
            "completion_cycle_id": None,
        }
    )
    link = next(
        row
        for row in payload["criterion_links"]
        if row["relation"] == "verification_attestation"
    )
    link.update(
        {
            "relation": "runner_observation",
            "assurance_class": "machine_observed",
            "producer_class": "verification_runner",
        }
    )
    refresh_bundle_seals(payload)
    _refresh_runner_reference_digest(payload)
    return payload


def _refresh_runner_reference_digest(payload: dict[str, object]) -> None:
    runner = payload["runner_observation"]
    reference = next(
        row
        for row in payload["evidence_references"]
        if row["source_kind"] == "runner_observation"
    )
    reference["digest"] = domain_digest(
        EVIDENCE_REFERENCE_DOMAIN,
        {
            "acceptance_criterion_id": reference["acceptance_criterion_id"],
            "assurance_class": reference["assurance_class"],
            "authority_snapshot_id": reference["authority_snapshot_id"],
            "completion_cycle_id": reference["completion_cycle_id"],
            "contract_revision": reference["contract_revision"],
            "producer_class": reference["producer_class"],
            "producer_version": reference["producer_version"],
            "project_id": payload["project_id"],
            "source_id": reference["source_id"],
            "source_kind": reference["source_kind"],
            "source_projection": runner,
            "source_state": reference["source_state"],
            "target_base_revision": reference["target_base_revision"] or "",
            "target_generation": reference["target_generation"],
            "target_kind": reference["target_kind"],
            "target_value": reference["target_value"],
            "task_id": payload["task"]["task_id"],
            "verification_criterion_id": reference[
                "verification_criterion_id"
            ],
        },
    )


def _entry_for(bundle) -> dict[str, object]:
    payload = bundle.payload
    return {
        "task_id": payload["task"]["task_id"],
        "completion_cycle_id": payload["completion_cycle_id"],
        "cycle_ordinal": payload["cycle_ordinal"],
        "bundle_state": "native",
        "bundle_id": payload["bundle_id"],
        "bundle_file": f"bundles/{payload['bundle_id']}.json",
        "bundle_format_version": 2,
        "bundle_digest": bundle.bundle_digest,
        "file_digest": bundle.file_digest,
        "sealed_at": payload["sealed_at"],
    }


def _consumer_source(payload: dict[str, object]) -> ValidatedEvidenceSource:
    envelope = {
        "bundle_digest": domain_digest(BUNDLE_V2_DOMAIN, payload),
        "format_version": 2,
        "payload": payload,
    }
    document = reference_json_bytes(envelope) + b"\n"
    entry = {
        "task_id": payload["task"]["task_id"],
        "completion_cycle_id": payload["completion_cycle_id"],
        "cycle_ordinal": payload["cycle_ordinal"],
        "bundle_state": "native",
        "bundle_id": payload["bundle_id"],
        "bundle_file": f"bundles/{payload['bundle_id']}.json",
        "bundle_format_version": 2,
        "bundle_digest": envelope["bundle_digest"],
        "file_digest": "sha256:" + hashlib.sha256(document).hexdigest(),
        "sealed_at": payload["sealed_at"],
    }
    return ValidatedEvidenceSource(
        "native_bundle",
        {
            "index_format_version": 2,
            "source_schema_version": 21,
            "project_id": payload["project_id"],
            "projection_generation": 1,
            "index_digest": "sha256:" + "9" * 64,
            "entry": entry,
        },
        envelope,
    )


def _live_task(*, marker: int = 2) -> dict[str, object]:
    return {
        "project_id": "project-000000000000",
        "task_id": "tg_task_0000000000000000",
        "status": "in_progress",
        "verification": "verify",
        "current_contract_revision": 1,
        "review_target_kind": "git_commit",
        "review_target_value": "a" * 40,
        "review_target_base_revision": "",
        "review_target_generation": 1,
        "review_target_capture_version": 1,
        "review_target_authority_snapshot_id": (
            "tg_authority_snapshot_0000000000000000"
        ),
        "review_target_verification_criterion_id": (
            "tg_contract_criterion_0000000000000000"
        ),
        "review_target_runner_basis_version": marker,
    }


def _insert_mapping(
    connection,
    table_name: str,
    values: dict[str, object],
) -> None:
    columns = tuple(values)
    connection.execute(
        f"INSERT INTO {table_name}({','.join(columns)}) VALUES "
        f"({','.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def _installed_json(testcase: unittest.TestCase, install, *arguments: str):
    result = install.run(*arguments, "--json")
    testcase.assertEqual(result.returncode, 0, result.stderr or result.stdout)
    payload = json.loads(result.stdout)
    testcase.assertTrue(payload["ok"], payload)
    return payload


def _seed_targeted_m21_fixture(
    testcase: unittest.TestCase,
    root: Path,
    *,
    record_receipt: bool,
    verification_required: bool = True,
    source_schema_version: int = 21,
    title: str = "Persisted schema21 Runner history",
):
    if source_schema_version not in (21, 22):
        raise AssertionError("completion fixture supports only historical21 or current22")
    install = (
        _physical_current21_install(root, git_managed=True)
        if source_schema_version == 21
        else make_physical_install(root, git_managed=True)
    )
    (install.project_root / "fixture.txt").write_text(
        "schema21 Runner compatibility fixture\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "--", ".gitignore", "fixture.txt"],
        cwd=install.project_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Taskgov Test",
            "-c",
            "user.email=taskgov-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Create schema21 Runner fixture target",
        ],
        cwd=install.project_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=install.project_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()

    setup = _installed_json(testcase, install, "setup")
    testcase.assertEqual(setup["data"]["schema_to"], source_schema_version)
    target = install.target
    added = _installed_json(
        testcase,
        install,
        "task",
        "add",
        "--repo",
        str(install.project_root),
        "--title",
        title,
        "--status",
        "in_progress",
        "--review-tier",
        "0",
        *(
            (
                "--verification",
                "python -m unittest tests.test_m243b_schema21_compatibility",
            )
            if verification_required
            else ()
        ),
        "--contract-scope",
        "Validate one persisted schema21 Runner history fixture.",
        "--contract-acceptance",
        "The stored Runner history replays through current readers.",
        "--contract-constraints",
        "Do not activate the live Runner completion writer.",
    )
    task_id = str(added["data"]["task"]["task_id"])
    targeted = _installed_json(
        testcase,
        install,
        "review",
        "target",
        "set",
        "--repo",
        str(install.project_root),
        task_id,
        "--kind",
        "git_commit",
        "--revision",
        commit,
    )
    generation = int(targeted["data"]["task"]["review_target_generation"])
    _installed_json(
        testcase,
        install,
        "review",
        "receipt",
        "add",
        "--repo",
        str(install.project_root),
        task_id,
        "--reviewer",
        "mechanical-review",
        "--kind",
        "not_required",
        "--verdict",
        "not_required",
        "--summary",
        "Tier zero review is not required",
    )
    if record_receipt and verification_required:
        _installed_json(
            testcase,
            install,
            "verification",
            "receipt",
            "add",
            "--repo",
            str(install.project_root),
            task_id,
            "--result",
            "pass",
            "--duration-ms",
            "25",
            "--scope-coverage",
            "full",
            "--expected-target-generation",
            str(generation),
        )
    return install, target, task_id, commit


def _seed_completed_m21_fixture(
    testcase: unittest.TestCase,
    root: Path,
    *,
    verification_required: bool = True,
    source_schema_version: int = 21,
    title: str = "Persisted schema21 Runner history",
):
    install, target, task_id, commit = _seed_targeted_m21_fixture(
        testcase,
        root,
        record_receipt=verification_required,
        verification_required=verification_required,
        source_schema_version=source_schema_version,
        title=title,
    )
    _installed_json(
        testcase,
        install,
        "task",
        "complete",
        "--repo",
        str(install.project_root),
        task_id,
        "--verification-complete",
        "--review-complete",
        "--completion-evidence-kind",
        "git_commit",
        "--completion-revision",
        commit,
    )
    return install, target, task_id


def _add_completed_m21_task(
    testcase: unittest.TestCase,
    install,
    *,
    title: str,
    commit: str,
) -> str:
    added = _installed_json(
        testcase,
        install,
        "task",
        "add",
        "--repo",
        str(install.project_root),
        "--title",
        title,
        "--status",
        "in_progress",
        "--review-tier",
        "0",
        "--verification",
        "python -m unittest tests.test_m243b_schema21_compatibility",
        "--contract-scope",
        "Validate another persisted schema21 Runner history fixture.",
        "--contract-acceptance",
        "The stored Runner history replays without repeated graph validation.",
        "--contract-constraints",
        "Do not activate the live Runner completion writer.",
    )
    task_id = str(added["data"]["task"]["task_id"])
    targeted = _installed_json(
        testcase,
        install,
        "review",
        "target",
        "set",
        "--repo",
        str(install.project_root),
        task_id,
        "--kind",
        "git_commit",
        "--revision",
        commit,
    )
    generation = int(targeted["data"]["task"]["review_target_generation"])
    _installed_json(
        testcase,
        install,
        "review",
        "receipt",
        "add",
        "--repo",
        str(install.project_root),
        task_id,
        "--reviewer",
        "mechanical-review",
        "--kind",
        "not_required",
        "--verdict",
        "not_required",
        "--summary",
        "Tier zero review is not required",
    )
    _installed_json(
        testcase,
        install,
        "verification",
        "receipt",
        "add",
        "--repo",
        str(install.project_root),
        task_id,
        "--result",
        "pass",
        "--duration-ms",
        "25",
        "--scope-coverage",
        "full",
        "--expected-target-generation",
        str(generation),
    )
    _installed_json(
        testcase,
        install,
        "task",
        "complete",
        "--repo",
        str(install.project_root),
        task_id,
        "--verification-complete",
        "--review-complete",
        "--completion-evidence-kind",
        "git_commit",
        "--completion-revision",
        commit,
    )
    return task_id


def _eligibility_one_graph(
    connection,
    *,
    task_id: str,
    token: str,
    terminal_branch: str | None = None,
) -> dict[str, object]:
    if len(token) != 16 or any(
        character not in "0123456789abcdef" for character in token
    ):
        raise AssertionError("Runner fixture token must be 16 lowercase hex characters")
    if terminal_branch not in {None, "fallback", "runner_pass", "other_terminal"}:
        raise AssertionError("unsupported Runner fixture terminal branch")

    task = connection.execute(
        "SELECT * FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if task is None:
        raise AssertionError("schema21 Runner fixture Task is missing")
    snapshot = connection.execute(
        "SELECT * FROM authority_snapshots WHERE authority_snapshot_id = ?",
        (task["review_target_authority_snapshot_id"],),
    ).fetchone()
    criterion = connection.execute(
        "SELECT * FROM contract_criteria WHERE criterion_id = ?",
        (task["review_target_verification_criterion_id"],),
    ).fetchone()
    manifest = connection.execute(
        "SELECT * FROM artifact_manifests WHERE artifact_manifest_id = ?",
        (task["review_target_artifact_manifest_id"],),
    ).fetchone()
    if snapshot is None or criterion is None or manifest is None:
        raise AssertionError("schema21 Runner fixture basis is incomplete")

    project_id = str(task["project_id"])
    observed_at = str(task["updated_at"])
    resolution_id = f"tg_verification_runner_resolution_{token}"
    attempt_id = f"tg_verification_runner_attempt_{token}"
    observation_id = f"tg_verification_runner_observation_{token}"
    target_material_digest = "sha256:" + "1" * 64
    implementation_digest = "sha256:" + "2" * 64
    resolution = {
        "verification_runner_resolution_id": resolution_id,
        "project_id": project_id,
        "task_id": task_id,
        "contract_revision": int(task["current_contract_revision"]),
        "authority_snapshot_id": str(task["review_target_authority_snapshot_id"]),
        "verification_criterion_id": str(
            task["review_target_verification_criterion_id"]
        ),
        "verification_expectation_digest": str(snapshot["verification_digest"]),
        "verification_criterion_digest": str(criterion["digest"]),
        "target_kind": str(task["review_target_kind"]),
        "target_value": str(task["review_target_value"]),
        "target_base_revision": (
            str(task["review_target_base_revision"])
            if task["review_target_base_revision"]
            else None
        ),
        "target_generation": int(task["review_target_generation"]),
        "target_capture_version": int(task["review_target_capture_version"]),
        "artifact_manifest_id": str(task["review_target_artifact_manifest_id"]),
        "target_material_digest": target_material_digest,
        "plan_state": "runner",
        "plan_blob_object_id": None,
        "plan_raw_digest": "sha256:" + "3" * 64,
        "plan_id": "schema21-compatibility",
        "plan_version": 1,
        "plan_semantic_digest": "sha256:" + "4" * 64,
        "selected_entry_digest": "sha256:" + "5" * 64,
        "coverage": "full",
        "step_count": 1,
        "runner_contract_version": RUNNER_CONTRACT_VERSION,
        "runner_implementation_version": RUNNER_IMPLEMENTATION_VERSION,
        "runner_implementation_digest": implementation_digest,
        "runner_policy_digest": RUNNER_POLICY_DIGEST,
        "runtime_digest": None,
        "gate_eligibility_version": 1,
        "trigger": RUNNER_TRIGGER,
        "route": "runner",
        "reason": None,
        "idempotency_digest": "sha256:" + "0" * 64,
        "created_at": observed_at,
    }
    resolution["idempotency_digest"] = resolution_idempotency_digest(
        storage_module._verification_runner_resolution_digest_projection(resolution)
    )
    attempt = {
        "verification_runner_attempt_id": attempt_id,
        "project_id": project_id,
        "task_id": task_id,
        "target_generation": int(task["review_target_generation"]),
        "gate_eligibility_version": 1,
        "verification_runner_resolution_id": resolution_id,
        "target_material_digest": target_material_digest,
        "runner_implementation_digest": implementation_digest,
        "attempt_digest": "sha256:" + "0" * 64,
        "intent_recorded_at": observed_at,
    }
    attempt["attempt_digest"] = verification_runner_attempt_digest(
        storage_module._verification_runner_attempt_digest_projection(attempt)
    )
    graph: dict[str, object] = {
        "task": dict(task),
        "resolution": resolution,
        "attempt": attempt,
        "observation": None,
        "cleanup": None,
        "reference": None,
        "link": None,
    }
    if terminal_branch is None:
        return graph

    if terminal_branch == "runner_pass":
        route, launch_state, outcome, reason = "runner", "launched", "pass", None
        complete_plan, completed_step_count = 1, 1
        duration_ms, cpu_time_ms, peak_memory, process_count = 25, 10, 4096, 1
    else:
        route = "m21_fallback"
        launch_state = "no_launch"
        outcome = "blocked_prelaunch"
        reason = (
            "runtime_unavailable"
            if terminal_branch == "fallback"
            else "process_boundary_unproved"
        )
        complete_plan, completed_step_count = 0, 0
        duration_ms, cpu_time_ms, peak_memory, process_count = 0, None, None, None
    observation_values = {
        "attempt_id": attempt_id,
        "completed_step_count": completed_step_count,
        "complete_plan": complete_plan,
        "cpu_time_ms": cpu_time_ms,
        "duration_ms": duration_ms,
        "failed_step_ordinal": None,
        "finished_at": observed_at,
        "gate_eligibility_version": 1,
        "launch_state": launch_state,
        "outcome": outcome,
        "peak_job_memory_bytes": peak_memory,
        "project_id": project_id,
        "reason": reason,
        "resolution_id": resolution_id,
        "runner_implementation_digest": implementation_digest,
        "started_at": observed_at,
        "target_generation": int(task["review_target_generation"]),
        "task_id": task_id,
        "route": route,
        "total_process_count": process_count,
        "total_step_count": 1,
    }
    observation = {
        "verification_runner_observation_id": observation_id,
        "project_id": project_id,
        "task_id": task_id,
        "target_generation": int(task["review_target_generation"]),
        "gate_eligibility_version": 1,
        "verification_runner_resolution_id": resolution_id,
        "verification_runner_attempt_id": attempt_id,
        "runner_implementation_digest": implementation_digest,
        "route": route,
        "launch_state": launch_state,
        "outcome": outcome,
        "reason": reason,
        "complete_plan": complete_plan,
        "total_step_count": 1,
        "completed_step_count": completed_step_count,
        "failed_step_ordinal": None,
        "started_at": observed_at,
        "finished_at": observed_at,
        "duration_ms": duration_ms,
        "cpu_time_ms": cpu_time_ms,
        "peak_job_memory_bytes": peak_memory,
        "total_process_count": process_count,
        "sanitized_result_digest": verification_runner_observation_digest(
            observation_values
        ),
        "created_at": observed_at,
    }
    cleanup_values = {
        "attempt_id": attempt_id,
        "event_kind": "attempt_cleanup_succeeded",
        "project_id": project_id,
        "target_generation": int(task["review_target_generation"]),
        "task_id": task_id,
        "terminal_observation_id": observation_id,
    }
    cleanup = {
        "verification_runner_sandbox_event_id": (
            f"tg_verification_runner_sandbox_event_{token}"
        ),
        "project_id": project_id,
        "task_id": task_id,
        "target_generation": int(task["review_target_generation"]),
        "verification_runner_attempt_id": attempt_id,
        "event_kind": "attempt_cleanup_succeeded",
        "event_digest": verification_runner_sandbox_event_digest(cleanup_values),
        "terminal_observation_id": observation_id,
        "created_at": observed_at,
    }
    source = EvidenceSource(
        source_kind="runner_observation",
        source_state="recorded",
        source_id=observation_id,
        source_projection=runner_observation_source_projection(
            observation=observation,
            resolution=resolution,
        ),
        _validated_runner_eligibility_version=1,
    )
    reference_spec = build_evidence_reference(
        source=source,
        project_id=project_id,
        task_id=task_id,
        contract_revision=int(task["current_contract_revision"]),
        binding=TargetCaptureBinding(
            target_kind=str(task["review_target_kind"]),
            target_value=str(task["review_target_value"]),
            target_base_revision=str(task["review_target_base_revision"]),
            target_generation=int(task["review_target_generation"]),
            authority_snapshot_id=str(task["review_target_authority_snapshot_id"]),
            acceptance_criterion_id=task["review_target_acceptance_criterion_id"],
            verification_criterion_id=str(
                task["review_target_verification_criterion_id"]
            ),
        ),
    )
    reference_id = f"tg_evidence_reference_{token}"
    reference = {
        "evidence_reference_id": reference_id,
        "project_id": project_id,
        "task_id": task_id,
        "source_kind": "runner_observation",
        "source_state": "recorded",
        "source_id": observation_id,
        "assurance_class": reference_spec.attribution.assurance_class,
        "producer_class": reference_spec.attribution.producer_class,
        "producer_version": reference_spec.attribution.producer_version,
        "contract_revision": int(task["current_contract_revision"]),
        "authority_snapshot_id": str(task["review_target_authority_snapshot_id"]),
        "acceptance_criterion_id": task["review_target_acceptance_criterion_id"],
        "verification_criterion_id": str(
            task["review_target_verification_criterion_id"]
        ),
        "target_kind": str(task["review_target_kind"]),
        "target_value": str(task["review_target_value"]),
        "target_base_revision": str(task["review_target_base_revision"]),
        "target_generation": int(task["review_target_generation"]),
        "completion_cycle_id": None,
        "digest": reference_spec.digest,
        "created_at": observed_at,
    }
    link = {
        "criterion_evidence_link_id": f"tg_criterion_evidence_link_{token}",
        "project_id": project_id,
        "task_id": task_id,
        "criterion_id": str(task["review_target_verification_criterion_id"]),
        "evidence_reference_id": reference_id,
        "relation": "runner_observation",
        "assurance_class": reference_spec.attribution.assurance_class,
        "producer_class": reference_spec.attribution.producer_class,
        "producer_version": reference_spec.attribution.producer_version,
        "created_at": observed_at,
    }
    graph.update(
        {
            "observation": observation,
            "cleanup": cleanup,
            "reference": reference,
            "link": link,
        }
    )
    return graph


def _cleanup_only_event(graph: dict[str, object], *, token: str) -> dict[str, object]:
    resolution = graph["resolution"]
    attempt = graph["attempt"]
    values = {
        "attempt_id": attempt["verification_runner_attempt_id"],
        "event_kind": "attempt_cleanup_succeeded",
        "project_id": resolution["project_id"],
        "target_generation": resolution["target_generation"],
        "task_id": resolution["task_id"],
        "terminal_observation_id": None,
    }
    return {
        "verification_runner_sandbox_event_id": (
            f"tg_verification_runner_sandbox_event_{token}"
        ),
        "project_id": resolution["project_id"],
        "task_id": resolution["task_id"],
        "target_generation": resolution["target_generation"],
        "verification_runner_attempt_id": attempt[
            "verification_runner_attempt_id"
        ],
        "event_kind": "attempt_cleanup_succeeded",
        "event_digest": verification_runner_sandbox_event_digest(values),
        "terminal_observation_id": None,
        "created_at": attempt["intent_recorded_at"],
    }


def _insert_eligibility_one_graph(connection, graph: dict[str, object]) -> None:
    task = graph["task"]
    changed = connection.execute(
        "UPDATE tasks SET review_target_runner_basis_version = 2 "
        "WHERE project_id = ? AND task_id = ?",
        (task["project_id"], task["task_id"]),
    )
    if changed.rowcount != 1:
        raise AssertionError("schema21 Runner fixture marker was not updated")
    _insert_mapping(connection, "verification_runner_resolutions", graph["resolution"])
    _insert_mapping(connection, "verification_runner_attempts", graph["attempt"])
    for table_name, name in (
        ("verification_runner_observations", "observation"),
        ("evidence_references", "reference"),
        ("criterion_evidence_links", "link"),
        ("verification_runner_sandbox_events", "cleanup"),
    ):
        if graph[name] is not None:
            _insert_mapping(connection, table_name, graph[name])


def _persist_later_runner_history_fixture(
    connection,
    *,
    task_id: str,
    original_payload: dict[str, object],
    token: str = "e" * 16,
):
    graph = _eligibility_one_graph(
        connection,
        task_id=task_id,
        token=token,
        terminal_branch="runner_pass",
    )
    task = graph["task"]
    project_id = str(task["project_id"])
    resolution = graph["resolution"]
    attempt = graph["attempt"]
    observation = graph["observation"]
    cleanup = graph["cleanup"]
    runner_reference = graph["reference"]
    observation_id = str(observation["verification_runner_observation_id"])
    observed_at = str(observation["created_at"])
    runner_projection = runner_observation_source_projection(
        observation=observation,
        resolution=resolution,
    )
    bundle = connection.execute(
        "SELECT * FROM completion_evidence_bundles WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if bundle is None:
        raise AssertionError("schema21 Runner fixture basis is incomplete")

    receipt_id = str(bundle["verification_receipt_id"])
    receipt_reference = connection.execute(
        "SELECT * FROM evidence_references WHERE project_id = ? AND task_id = ? "
        "AND source_kind = 'verification_receipt' AND source_id = ?",
        (project_id, task_id, receipt_id),
    ).fetchone()
    if receipt_reference is None:
        raise AssertionError("schema21 Runner fixture Receipt reference is missing")
    receipt_link = connection.execute(
        "SELECT * FROM criterion_evidence_links WHERE evidence_reference_id = ? "
        "AND relation = 'verification_attestation'",
        (receipt_reference["evidence_reference_id"],),
    ).fetchone()
    if receipt_link is None:
        raise AssertionError("schema21 Runner fixture Receipt link is missing")

    payload = deepcopy(original_payload)
    payload_reference = next(
        row
        for row in payload["evidence_references"]
        if row["source_kind"] == "verification_receipt"
    )
    payload_reference.update(
        {
            "source_kind": "runner_observation",
            "source_state": "recorded",
            "source_id": observation_id,
            "assurance_class": "machine_observed",
            "producer_class": "verification_runner",
            "digest": runner_reference["digest"],
        }
    )
    payload_link = next(
        row
        for row in payload["criterion_links"]
        if row["relation"] == "verification_attestation"
    )
    payload_link.update(
        {
            "relation": "runner_observation",
            "assurance_class": "machine_observed",
            "producer_class": "verification_runner",
        }
    )
    payload["verification_receipt"] = None
    payload["verification_basis"] = {
        "basis_version": 1,
        "kind": "runner_observation",
        "runner_observation_id": observation_id,
        "verification_receipt_id": None,
    }
    payload["runner_observation"] = runner_projection
    artifact = build_bundle_artifact(payload)

    mutable_triggers = (
        "trg_task_completion_cycles_no_update",
        "trg_completion_evidence_bundles_no_update",
        "trg_evidence_references_no_update",
        "trg_criterion_evidence_links_no_update",
        "trg_verification_receipts_no_delete",
    )
    stored_triggers = {
        str(row["name"]): str(row["sql"])
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
            f"AND name IN ({','.join('?' for _ in mutable_triggers)})",
            mutable_triggers,
        ).fetchall()
    }
    if set(stored_triggers) != set(mutable_triggers):
        raise AssertionError("schema21 Runner fixture immutable guards are missing")

    connection.execute("BEGIN IMMEDIATE")
    try:
        for trigger_name in mutable_triggers:
            connection.execute(f'DROP TRIGGER "{trigger_name}"')
        connection.execute(
            "UPDATE tasks SET review_target_runner_basis_version = 2 "
            "WHERE project_id = ? AND task_id = ?",
            (project_id, task_id),
        )
        _insert_mapping(connection, "verification_runner_resolutions", resolution)
        _insert_mapping(connection, "verification_runner_attempts", attempt)
        _insert_mapping(connection, "verification_runner_observations", observation)
        _insert_mapping(connection, "verification_runner_sandbox_events", cleanup)
        connection.execute(
            "UPDATE task_completion_cycles SET verification_receipt_id = NULL, "
            "verification_basis_kind = 'runner_observation', "
            "verification_runner_observation_id = ? "
            "WHERE completion_cycle_id = ?",
            (observation_id, bundle["completion_cycle_id"]),
        )
        connection.execute(
            "UPDATE completion_evidence_bundles SET verification_receipt_id = NULL, "
            "verification_basis_kind = 'runner_observation', "
            "verification_runner_observation_id = ?, bundle_digest = ?, "
            "payload_size_bytes = ? WHERE completion_evidence_bundle_id = ?",
            (
                observation_id,
                artifact.bundle_digest,
                len(artifact.payload_bytes),
                bundle["completion_evidence_bundle_id"],
            ),
        )
        connection.execute(
            "UPDATE evidence_references SET source_kind = 'runner_observation', "
            "source_state = 'recorded', source_id = ?, "
            "assurance_class = 'machine_observed', "
            "producer_class = 'verification_runner', digest = ?, created_at = ? "
            "WHERE evidence_reference_id = ?",
            (
                observation_id,
                runner_reference["digest"],
                observed_at,
                receipt_reference["evidence_reference_id"],
            ),
        )
        connection.execute(
            "UPDATE criterion_evidence_links SET relation = 'runner_observation', "
            "assurance_class = 'machine_observed', "
            "producer_class = 'verification_runner', created_at = ? "
            "WHERE criterion_evidence_link_id = ?",
            (observed_at, receipt_link["criterion_evidence_link_id"]),
        )
        connection.execute(
            "DELETE FROM verification_receipts WHERE verification_receipt_id = ?",
            (receipt_id,),
        )
        for trigger_name in mutable_triggers:
            connection.execute(stored_triggers[trigger_name])
        storage_module.validate_schema21_storage(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return artifact, observation_id


class M243BSchema21CompatibilityTests(unittest.TestCase):
    def test_source21_bundle_cannot_be_bound_to_schema20_index(self) -> None:
        bundle = build_bundle_artifact(_source21_not_required_payload())
        entry = _entry_for(bundle)
        index = build_index_artifact(
            {
                "source_schema_version": 20,
                "project_id": bundle.payload["project_id"],
                "projection_generation": 1,
                "bundle_count": 1,
                "legacy_count": 0,
                "entries": [entry],
            }
        )
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-index-binding-",
            dir=ROOT,
        ) as temporary:
            evidence_root = Path(temporary) / "evidence"
            bundles_root = evidence_root / "bundles"
            bundles_root.mkdir(parents=True)
            (evidence_root / "index.json").write_bytes(index.document)
            (bundles_root / f"{bundle.payload['bundle_id']}.json").write_bytes(
                bundle.document
            )
            validated_index = read_evidence_index(evidence_root)

            with self.assertRaises(EvidenceConsumerError):
                validate_evidence_source(
                    validated_index,
                    validated_index.entries[0],
                )

    def test_source21_keeps_bundle_and_index_format_v2(self) -> None:
        bundle = build_bundle_artifact(_source21_not_required_payload())
        entry = _entry_for(bundle)
        index = build_index_artifact(
            {
                "source_schema_version": 21,
                "project_id": bundle.payload["project_id"],
                "projection_generation": 1,
                "bundle_count": 1,
                "legacy_count": 0,
                "entries": [entry],
            }
        )
        source = ValidatedEvidenceSource(
            "native_bundle",
            {
                "index_format_version": 2,
                "source_schema_version": 21,
                "project_id": bundle.payload["project_id"],
                "projection_generation": 1,
                "index_digest": index.index_digest,
                "entry": entry,
            },
            bundle.envelope,
        )

        self.assertEqual(bundle.envelope["format_version"], 2)
        self.assertEqual(index.envelope["format_version"], 2)
        self.assertEqual(index.payload["source_schema_version"], 21)
        self.assertEqual(
            source.source["payload"]["verification_basis"]["kind"],
            "not_required",
        )
        self.assertEqual(source.source, bundle.envelope)
        self.assertEqual(source.source_basis["index_format_version"], 2)
        self.assertEqual(source.source_basis["source_schema_version"], 21)
        self.assertEqual(source.source_basis["entry"], entry)

    def test_source21_runner_history_is_read_only_compatible(self) -> None:
        bundle = build_bundle_artifact(_source21_runner_payload())
        entry = _entry_for(bundle)
        source = ValidatedEvidenceSource(
            "native_bundle",
            {
                "index_format_version": 2,
                "source_schema_version": 21,
                "project_id": bundle.payload["project_id"],
                "projection_generation": 2,
                "index_digest": "sha256:" + "9" * 64,
                "entry": entry,
            },
            bundle.envelope,
        )

        self.assertEqual(
            source.source["payload"]["runner_observation"],
            _runner_projection(),
        )
        self.assertEqual(source.source, bundle.envelope)
        self.assertEqual(source.source_basis["index_format_version"], 2)
        self.assertEqual(source.source_basis["source_schema_version"], 21)
        self.assertEqual(source.source_basis["entry"], entry)
        malformed = _source21_runner_payload()
        malformed["runner_observation"]["gate_eligibility_version"] = 0
        with self.assertRaises(EvidenceProjectionError):
            build_bundle_artifact(malformed)
        for plan_id in ("Uppercase", "lower:colon", "a" * 65):
            with self.subTest(plan_id=plan_id):
                malformed = _source21_runner_payload()
                malformed["runner_observation"]["plan_id"] = plan_id
                _refresh_runner_reference_digest(malformed)
                with self.assertRaises(EvidenceProjectionError):
                    build_bundle_artifact(malformed)
                with self.assertRaises(EvidenceConsumerError):
                    _consumer_source(malformed)
        for field in (
            "gate_eligibility_version",
            "complete_plan",
            "plan_version",
        ):
            with self.subTest(boolean_field=field):
                malformed = _source21_runner_payload()
                malformed["runner_observation"][field] = True
                _refresh_runner_reference_digest(malformed)
                with self.assertRaises(EvidenceProjectionError):
                    build_bundle_artifact(malformed)
                with self.assertRaises(EvidenceConsumerError):
                    _consumer_source(malformed)

    def test_private_runner_eligibility_seam_is_closed_and_digest_stable(
        self,
    ) -> None:
        runner = _runner_projection()
        source = EvidenceSource(
            source_kind="runner_observation",
            source_state="recorded",
            source_id=runner["observation_id"],
            source_projection=runner,
            _validated_runner_eligibility_version=1,
        )
        binding = TargetCaptureBinding(
            target_kind="git_commit",
            target_value="a" * 40,
            target_base_revision="",
            target_generation=1,
            authority_snapshot_id=(
                "tg_authority_snapshot_0000000000000000"
            ),
            acceptance_criterion_id=(
                "tg_contract_criterion_1111111111111111"
            ),
            verification_criterion_id=(
                "tg_contract_criterion_2222222222222222"
            ),
        )
        first = build_evidence_reference(
            source=source,
            project_id="project-000000000000",
            task_id="tg_task_0000000000000000",
            contract_revision=1,
            binding=binding,
        )
        second = build_evidence_reference(
            source=EvidenceSource(
                source_kind="runner_observation",
                source_state="recorded",
                source_id=runner["observation_id"],
                source_projection=runner,
                _validated_runner_eligibility_version=1,
            ),
            project_id="project-000000000000",
            task_id="tg_task_0000000000000000",
            contract_revision=1,
            binding=binding,
        )

        self.assertEqual(first.digest, second.digest)
        self.assertNotIn(
            "_validated_runner_eligibility_version",
            source.__dict__,
        )
        with self.assertRaises(EvidenceLedgerError):
            EvidenceSource(
                source_kind="runner_observation",
                source_state="recorded",
                source_id=runner["observation_id"],
                source_projection=runner,
            )
        with self.assertRaises(EvidenceLedgerError):
            EvidenceSource(
                source_kind="runner_observation",
                source_state="recorded",
                source_id=runner["observation_id"],
                source_projection=runner,
                _validated_runner_eligibility_version=0,
            )
        for field in ("gate_eligibility_version", "plan_version"):
            with self.subTest(boolean_field=field):
                malformed = dict(runner)
                malformed[field] = True
                with self.assertRaises(EvidenceLedgerError):
                    EvidenceSource(
                        source_kind="runner_observation",
                        source_state="recorded",
                        source_id=runner["observation_id"],
                        source_projection=malformed,
                        _validated_runner_eligibility_version=1,
                    )

    def test_live_marker_two_is_stale_before_receipt_uniqueness(self) -> None:
        task = _live_task()
        with mock.patch(
            "task_governance_tool.verification_receipts."
            "read_verification_receipt_snapshot"
        ) as read_snapshot:
            with self.assertRaises(VerificationReceiptError) as caught:
                _validate_add_basis(
                    None,
                    task=task,
                    project_id=task["project_id"],
                    task_id=task["task_id"],
                    values=VerificationReceiptInput("pass", 1, "full", 1),
                )
        self.assertEqual(caught.exception.code, "evidence_basis_stale")
        read_snapshot.assert_not_called()
        self.assertEqual(
            _gate_from_exact(
                expectation="verify",
                source_revision={"generation": 1},
                current_subject={"basis_version": 1},
                exact_rows=({"result": "pass", "scope_coverage": "full"},),
                runner_basis_version=2,
            ),
            VerificationGate(True, False, "evidence_basis_stale", None),
        )

    def test_valid_done_runner_cycle_replays_without_a_receipt(self) -> None:
        task = _live_task()
        task["status"] = "done"
        cycle = CompletionCycle(
            completion_cycle_id="tg_completion_cycle_0000000000000000",
            project_id=task["project_id"],
            task_id=task["task_id"],
            saved_cycle_ordinal=1,
            origin="native_done",
            completeness="complete",
            completed_at="2026-08-28T00:00:01Z",
            recorded_at="2026-08-28T00:00:01Z",
            contract_revision=1,
            review_tier=2,
            verification_expectation="specified",
            verification_attestation=True,
            completion_evidence_kind="git_commit",
            completion_evidence_revision="a" * 40,
            completion_evidence_reason="",
            external_revision_approved=False,
            completion_commit_required=True,
            completion_commit_hash="a" * 40,
            review_target_kind=task["review_target_kind"],
            review_target_value=task["review_target_value"],
            review_target_base_revision=task["review_target_base_revision"],
            review_target_generation=task["review_target_generation"],
            gate_basis=CompletionGateBasis(
                1,
                "independent_passes",
                2,
                2,
                0,
                0,
                0,
                0,
                (
                    "tg_review_receipt_1111111111111111",
                    "tg_review_receipt_2222222222222222",
                ),
            ),
            verification_basis_version=1,
            verification_expectation_digest=verification_expectation_digest(
                task["verification"]
            ),
            verification_receipt_id=None,
            verification_subject_basis_version=1,
            subject_authority_snapshot_id=(
                task["review_target_authority_snapshot_id"]
            ),
            subject_verification_criterion_id=(
                task["review_target_verification_criterion_id"]
            ),
            evidence_basis_version=1,
            completion_evidence_bundle_id=(
                "tg_completion_evidence_bundle_0000000000000000"
            ),
            verification_basis_kind="runner_observation",
            verification_runner_observation_id=(
                "tg_verification_runner_observation_0000000000000000"
            ),
        )
        storage_module._validate_completion_cycle(cycle)
        gate = _validate_done_cycle(
            task,
            cycle,
            expectation=task["verification"],
            contract_revision=1,
            digest=verification_expectation_digest(task["verification"]),
            source_revision={"generation": 1},
            gate=VerificationGate(True, False, "evidence_basis_stale", None),
        )
        self.assertEqual(gate, VerificationGate(True, True, None, None))

    def test_eligibility_one_pending_cleanup_and_invalidations_preserve_history(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-runner-invalidation-",
            dir=ROOT,
        ) as temporary:
            install, target, task_id, commit = _seed_targeted_m21_fixture(
                self,
                Path(temporary),
                record_receipt=False,
            )
            with closing(storage_module.connect(target.db_path)) as connection:
                first = _eligibility_one_graph(
                    connection,
                    task_id=task_id,
                    token="a" * 16,
                )
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    _insert_eligibility_one_graph(connection, first)
                    storage_module.validate_schema21_storage(connection)
                generation = int(first["resolution"]["target_generation"])
                pending = storage_module.read_verification_runner_generation_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    target_generation=generation,
                )
                self.assertEqual(pending["state"], "pending")
                pending_cleanup = (
                    storage_module.read_pending_verification_runner_cleanup(
                        connection,
                        project_id=target.project.project_id,
                    )
                )
                self.assertEqual(len(pending_cleanup), 1)
                self.assertEqual(
                    pending_cleanup[0]["resolution"].task_id, task_id
                )
                self.assertEqual(
                    pending_cleanup[0]["resolution"].target_generation,
                    generation,
                )
                self.assertEqual(pending_cleanup[0]["state"], "pending")

                cleanup = _cleanup_only_event(first, token="a" * 16)
                before_events = connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_sandbox_events"
                ).fetchone()[0]
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    persist_cleanup = getattr(
                        storage_module,
                        "persist_verification_runner_restart_cleanup_locked",
                    )
                    self.assertTrue(
                        persist_cleanup(
                            connection,
                            cleanup_event=(
                                storage_module.VerificationRunnerSandboxEvent(**cleanup)
                            ),
                        )
                    )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM verification_runner_sandbox_events"
                    ).fetchone()[0],
                    before_events + 1,
                )
                storage_module.validate_schema21_storage(connection)
                cleaned = storage_module.read_verification_runner_generation_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    target_generation=generation,
                )
                self.assertEqual(cleaned["state"], "restart_cleaned")
                self.assertEqual(
                    storage_module.read_pending_verification_runner_cleanup(
                        connection,
                        project_id=target.project.project_id,
                    ),
                    (),
                )

            _installed_json(
                self,
                install,
                "review",
                "target",
                "set",
                "--repo",
                str(install.project_root),
                task_id,
                "--kind",
                "git_commit",
                "--revision",
                commit,
            )
            with closing(storage_module.connect(target.db_path)) as connection:
                task = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                self.assertEqual(task["review_target_runner_basis_version"], 0)
                self.assertGreater(task["review_target_generation"], generation)
                retained = storage_module.read_verification_runner_generation_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    target_generation=generation,
                )
                self.assertEqual(retained["state"], "restart_cleaned")
                second = _eligibility_one_graph(
                    connection,
                    task_id=task_id,
                    token="b" * 16,
                )
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    _insert_eligibility_one_graph(connection, second)
                    storage_module.validate_schema21_storage(connection)

            revised = _installed_json(
                self,
                install,
                "task",
                "edit",
                "--repo",
                str(install.project_root),
                task_id,
                "--contract-scope",
                "Validate the revised schema21 compatibility target.",
                "--contract-acceptance",
                "The revised target keeps historical Runner evidence immutable.",
                "--contract-constraints",
                "Do not activate the schema21 Runner writer.",
                "--contract-authority-ref",
                "docs/execution-contracts/tg-m24-verification-runner.md#tg-m24-3b",
                "--contract-change-reason",
                "Exercise the existing Contract invalidation path.",
            )
            self.assertEqual(revised["data"]["contract_write"]["revision"], 2)
            with closing(storage_module.connect(target.db_path)) as connection:
                task = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                self.assertEqual(task["review_target_runner_basis_version"], 0)
                self.assertEqual(task["review_target_capture_version"], 0)
                second_generation = int(second["resolution"]["target_generation"])
                retained = storage_module.read_verification_runner_generation_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    target_generation=second_generation,
                )
                self.assertEqual(retained["state"], "pending")
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    _insert_mapping(
                        connection,
                        "verification_runner_sandbox_events",
                        _cleanup_only_event(second, token="b" * 16),
                    )
                    storage_module.validate_schema21_storage(connection)

            _installed_json(
                self,
                install,
                "review",
                "target",
                "set",
                "--repo",
                str(install.project_root),
                task_id,
                "--kind",
                "git_commit",
                "--revision",
                commit,
            )
            with closing(storage_module.connect(target.db_path)) as connection:
                third = _eligibility_one_graph(
                    connection,
                    task_id=task_id,
                    token="c" * 16,
                )
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    _insert_eligibility_one_graph(connection, third)
                    storage_module.validate_schema21_storage(connection)

            _installed_json(
                self,
                install,
                "task",
                "edit",
                "--repo",
                str(install.project_root),
                task_id,
                "--title",
                "Persisted schema21 Runner history after authority edit",
            )
            with closing(storage_module.connect(target.db_path)) as connection:
                task = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                self.assertEqual(task["review_target_runner_basis_version"], 0)
                self.assertEqual(task["review_target_capture_version"], 0)
                retained = storage_module.read_verification_runner_generation_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    target_generation=int(
                        third["resolution"]["target_generation"]
                    ),
                )
                self.assertEqual(retained["state"], "pending")
                storage_module.validate_schema21_storage(connection)

    def test_exact_receipt_requires_closed_no_launch_fallback_graph(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-runner-receipt-",
            dir=ROOT,
        ) as temporary:
            _install, target, task_id, _commit = _seed_targeted_m21_fixture(
                self,
                Path(temporary),
                record_receipt=True,
            )
            cases = (
                ("fallback", "fallback", "1" * 16, True),
                ("pending", None, "2" * 16, False),
                ("runner_pass", "runner_pass", "3" * 16, False),
                ("other_terminal", "other_terminal", "4" * 16, False),
            )
            with closing(storage_module.connect(target.db_path)) as connection:
                receipt_count = connection.execute(
                    "SELECT COUNT(*) FROM verification_receipts WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
                self.assertEqual(receipt_count, 1)
                for name, branch, token, accepted in cases:
                    with self.subTest(branch=name):
                        graph = _eligibility_one_graph(
                            connection,
                            task_id=task_id,
                            token=token,
                            terminal_branch=branch,
                        )
                        connection.execute("BEGIN IMMEDIATE")
                        try:
                            _insert_eligibility_one_graph(connection, graph)
                            if accepted:
                                storage_module.validate_schema21_storage(connection)
                            else:
                                with self.assertRaises(
                                    storage_module.StorageError
                                ) as rejected:
                                    storage_module.validate_schema21_storage(connection)
                                self.assertEqual(
                                    rejected.exception.code,
                                    "project_state_unreadable",
                                )
                        finally:
                            connection.rollback()
                storage_module.validate_schema21_storage(connection)

    def test_persisted_runner_history_replays_and_viewer_stays_v4(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-runner-history-",
            dir=ROOT,
        ) as temporary:
            install, target, task_id = _seed_completed_m21_fixture(
                self,
                Path(temporary),
            )
            publication = publish_setup_evidence_projection(
                target,
                observed_at="2026-08-28T00:00:00Z",
            )
            self.assertEqual(publication.code, "succeeded")
            index = json.loads(target.resolved_evidence_index.read_bytes())
            entry = next(
                row
                for row in index["payload"]["entries"]
                if row["task_id"] == task_id
            )
            original = json.loads(
                (target.resolved_evidence_root / entry["bundle_file"]).read_bytes()
            )["payload"]

            before = tree_snapshot(target.resolved_evidence_root)
            validated_index = read_evidence_index(target.resolved_evidence_root)
            source = validate_evidence_source(validated_index, entry)
            self.assertEqual(source.source["format_version"], 2)
            self.assertEqual(source.source["payload"], original)
            self.assertEqual(source.source_basis["index_format_version"], 2)
            self.assertEqual(source.source_basis["source_schema_version"], 21)
            self.assertEqual(source.source_basis["entry"], entry)
            self.assertEqual(
                source.source["payload"]["verification_basis"],
                {
                    "basis_version": 1,
                    "kind": "caller_attestation",
                    "verification_receipt_id": original["verification_receipt"][
                        "verification_receipt_id"
                    ],
                    "runner_observation_id": None,
                },
            )
            self.assertIsNone(source.source["payload"]["runner_observation"])
            self.assertEqual(tree_snapshot(target.resolved_evidence_root), before)

            with closing(storage_module.connect(target.db_path)) as connection:
                artifact, observation_id = _persist_later_runner_history_fixture(
                    connection,
                    task_id=task_id,
                    original_payload=original,
                )
                storage_module.validate_schema21_storage(connection)
                attempt_trigger = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = 'trg_verification_runner_attempts_no_update'"
                ).fetchone()
                self.assertIsNotNone(attempt_trigger)
                for corruption, replacements in (
                    ("eligibility_mixture", {"gate_eligibility_version": 0}),
                    (
                        "parent_identity",
                        {"runner_implementation_digest": "sha256:" + "9" * 64},
                    ),
                ):
                    with self.subTest(corruption=corruption):
                        stored_attempt = dict(
                            connection.execute(
                                "SELECT * FROM verification_runner_attempts "
                                "WHERE task_id = ?",
                                (task_id,),
                            ).fetchone()
                        )
                        corrupted_attempt = {**stored_attempt, **replacements}
                        attempt_projection = getattr(
                            storage_module,
                            "_verification_runner_attempt_digest_projection",
                        )
                        corrupted_attempt["attempt_digest"] = (
                            verification_runner_attempt_digest(
                                attempt_projection(corrupted_attempt)
                            )
                        )
                        connection.execute("BEGIN IMMEDIATE")
                        try:
                            connection.execute(
                                "DROP TRIGGER "
                                "trg_verification_runner_attempts_no_update"
                            )
                            connection.execute(
                                "UPDATE verification_runner_attempts "
                                "SET gate_eligibility_version = ?, "
                                "runner_implementation_digest = ?, "
                                "attempt_digest = ? WHERE task_id = ?",
                                (
                                    corrupted_attempt["gate_eligibility_version"],
                                    corrupted_attempt[
                                        "runner_implementation_digest"
                                    ],
                                    corrupted_attempt["attempt_digest"],
                                    task_id,
                                ),
                            )
                            connection.execute(str(attempt_trigger["sql"]))
                            with self.assertRaises(
                                storage_module.StorageError
                            ) as rejected:
                                storage_module.validate_schema21_storage(connection)
                            self.assertEqual(
                                rejected.exception.code,
                                "project_state_unreadable",
                            )
                        finally:
                            connection.rollback()
                storage_module.validate_schema21_storage(connection)
                projection = storage_module.capture_evidence_projection_basis(
                    connection,
                    project_id=target.project.project_id,
                )
                self.assertEqual(len(projection.native_bundles), 1)
                rebuilt = build_projection_bundle_artifact(
                    projection.native_bundles[0]
                )
                self.assertEqual(rebuilt.bundle_digest, artifact.bundle_digest)
                self.assertEqual(rebuilt.payload["source_schema_version"], 21)
                self.assertEqual(
                    rebuilt.payload["verification_basis"],
                    {
                        "basis_version": 1,
                        "kind": "runner_observation",
                        "runner_observation_id": observation_id,
                        "verification_receipt_id": None,
                    },
                )
                stored_task = dict(
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                )
                live_projection = read_verification_evidence(
                    connection,
                    task={**stored_task, "status": "in_progress"},
                )
                self.assertEqual(
                    live_projection["gate"],
                    {
                        "required": True,
                        "satisfied": False,
                        "blocking_code": "evidence_basis_stale",
                        "qualifying_receipt_id": None,
                    },
                )
                original_generation = int(stored_task["review_target_generation"])
                runner_tables = (
                    "verification_runner_resolutions",
                    "verification_runner_attempts",
                    "verification_runner_observations",
                    "verification_runner_sandbox_events",
                )
                runner_counts = tuple(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table_name} WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()[0]
                    for table_name in runner_tables
                )
                runner_graph_before = {
                    table_name: [
                        tuple(row)
                        for row in connection.execute(
                            f"SELECT * FROM {table_name} WHERE task_id = ? "
                            "ORDER BY rowid",
                            (task_id,),
                        ).fetchall()
                    ]
                    for table_name in runner_tables
                }

            captured_implementation = artifact.payload["runner_observation"][
                "runner_implementation_digest"
            ]
            history_before = tree_snapshot(install.skill_root / "state")
            implementation_before = capture_runner_implementation(install.skill_root)
            core = install.skill_root / "SKILL.md"
            core.write_bytes(
                core.read_bytes() + b"\n<!-- Historical package identity fixture. -->\n"
            )
            refresh_test_manifest(install.skill_root)
            implementation_after = capture_runner_implementation(install.skill_root)
            self.assertNotEqual(
                implementation_after.implementation_digest,
                implementation_before.implementation_digest,
            )
            self.assertNotEqual(
                captured_implementation,
                implementation_after.implementation_digest,
            )
            shown = _installed_json(
                self,
                install,
                "task",
                "show",
                "--repo",
                str(install.project_root),
                task_id,
            )
            self.assertEqual(
                shown["data"]["verification_evidence"]["gate"],
                {
                    "required": True,
                    "satisfied": True,
                    "blocking_code": None,
                    "qualifying_receipt_id": None,
                },
            )
            self.assertEqual(shown["data"]["completion_history"]["total"], 1)
            shown_cycle = shown["data"]["completion_history"]["cycles"][0]
            self.assertNotIn("runner_observation", shown_cycle)
            self.assertNotIn("verification_basis", shown_cycle)

            with closing(
                storage_module.connect_snapshot_readonly(target.db_path)
            ) as connection:
                snapshot = build_viewer_snapshot(
                    connection,
                    target,
                    generated_at="2026-08-28T00:01:00Z",
                ).snapshot
                runner_graph_after = {
                    table_name: [
                        tuple(row)
                        for row in connection.execute(
                            f"SELECT * FROM {table_name} WHERE task_id = ? "
                            "ORDER BY rowid",
                            (task_id,),
                        ).fetchall()
                    ]
                    for table_name in runner_tables
                }
                replayed_projection = storage_module.capture_evidence_projection_basis(
                    connection,
                    project_id=target.project.project_id,
                )
                self.assertEqual(len(replayed_projection.native_bundles), 1)
                replayed_bundle = build_projection_bundle_artifact(
                    replayed_projection.native_bundles[0]
                )
            self.assertEqual(runner_graph_after, runner_graph_before)
            self.assertEqual(replayed_bundle.bundle_digest, artifact.bundle_digest)
            self.assertEqual(replayed_bundle.payload_bytes, rebuilt.payload_bytes)
            self.assertEqual(replayed_bundle.document, rebuilt.document)
            self.assertEqual(
                replayed_bundle.payload["runner_observation"][
                    "runner_implementation_digest"
                ],
                captured_implementation,
            )
            self.assertEqual(tree_snapshot(install.skill_root / "state"), history_before)
            self.assertEqual(snapshot["snapshot_version"], 4)
            self.assertEqual(snapshot["source_schema_version"], 21)
            serialized = json.dumps(snapshot, sort_keys=True)
            self.assertNotIn("runner_observation", serialized)
            self.assertNotIn("verification_basis", serialized)

            _installed_json(
                self,
                install,
                "task",
                "edit",
                "--repo",
                str(install.project_root),
                task_id,
                "--status",
                "in_progress",
                "--reopen-reason",
                "Exercise the existing schema21 reopen invalidation path",
            )
            with closing(storage_module.connect(target.db_path)) as connection:
                reopened = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                self.assertEqual(reopened["status"], "in_progress")
                self.assertEqual(reopened["review_target_runner_basis_version"], 0)
                self.assertEqual(reopened["review_target_capture_version"], 0)
                self.assertGreater(
                    reopened["review_target_generation"],
                    original_generation,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table_name} WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()[0]
                        for table_name in (
                            "verification_runner_resolutions",
                            "verification_runner_attempts",
                            "verification_runner_observations",
                            "verification_runner_sandbox_events",
                        )
                    ),
                    runner_counts,
                )
                storage_module.validate_schema21_storage(connection)

    def test_task_show_rejects_corrupt_schema21_bundle_digest(self) -> None:
        for branch in ("fallback", "runner"):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-{branch}-bundle-read-",
                dir=ROOT,
            ) as temporary:
                install, target, task_id = _seed_completed_m21_fixture(
                    self,
                    Path(temporary),
                )
                with closing(storage_module.connect(target.db_path)) as connection:
                    projection = storage_module.capture_evidence_projection_basis(
                        connection,
                        project_id=target.project.project_id,
                    )
                    self.assertEqual(len(projection.native_bundles), 1)
                    original_artifact = build_projection_bundle_artifact(
                        projection.native_bundles[0]
                    )
                    if branch == "fallback":
                        graph = _eligibility_one_graph(
                            connection,
                            task_id=task_id,
                            token="d" * 16,
                            terminal_branch="fallback",
                        )
                        connection.execute("BEGIN IMMEDIATE")
                        try:
                            _insert_eligibility_one_graph(connection, graph)
                            storage_module.validate_schema21_storage(connection)
                            connection.commit()
                        except Exception:
                            connection.rollback()
                            raise
                        expected_digest = original_artifact.bundle_digest
                    else:
                        artifact, _observation_id = (
                            _persist_later_runner_history_fixture(
                                connection,
                                task_id=task_id,
                                original_payload=original_artifact.payload,
                            )
                        )
                        expected_digest = artifact.bundle_digest

                    corrupt_digest = "sha256:" + "0" * 64
                    self.assertNotEqual(expected_digest, corrupt_digest)
                    trigger = connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                        "AND name = 'trg_completion_evidence_bundles_no_update'"
                    ).fetchone()
                    self.assertIsNotNone(trigger)
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute(
                            "DROP TRIGGER trg_completion_evidence_bundles_no_update"
                        )
                        connection.execute(
                            "UPDATE completion_evidence_bundles "
                            "SET bundle_digest = ? WHERE task_id = ?",
                            (corrupt_digest, task_id),
                        )
                        connection.execute(str(trigger["sql"]))
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise

                result = install.run(
                    "task",
                    "show",
                    "--repo",
                    str(install.project_root),
                    task_id,
                    "--json",
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                payload = json.loads(result.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(
                    payload["errors"],
                    [
                        {
                            "code": "completion_history_inconsistent",
                            "message": "stored completion history is inconsistent",
                        }
                    ],
                )

    def test_task_show_rejects_changed_historical_schema21_fallback(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-changed-fallback-read-",
            dir=ROOT,
        ) as temporary:
            install, target, task_id = _seed_completed_m21_fixture(
                self,
                Path(temporary),
            )
            with closing(storage_module.connect(target.db_path)) as connection:
                fallback = _eligibility_one_graph(
                    connection,
                    task_id=task_id,
                    token="c" * 16,
                    terminal_branch="fallback",
                )
                changed = _eligibility_one_graph(
                    connection,
                    task_id=task_id,
                    token="c" * 16,
                    terminal_branch="other_terminal",
                )
                connection.execute("BEGIN IMMEDIATE")
                try:
                    _insert_eligibility_one_graph(connection, fallback)
                    storage_module.validate_schema21_storage(connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

            _installed_json(
                self,
                install,
                "task",
                "edit",
                "--repo",
                str(install.project_root),
                task_id,
                "--status",
                "in_progress",
                "--reopen-reason",
                "Exercise historical schema21 fallback validation",
            )

            observation = changed["observation"]
            reference = changed["reference"]
            trigger_names = (
                "trg_verification_runner_observations_no_update",
                "trg_evidence_references_no_update",
            )
            with closing(storage_module.connect(target.db_path)) as connection:
                trigger_rows = connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                    f"AND name IN ({','.join('?' for _ in trigger_names)})",
                    trigger_names,
                ).fetchall()
                triggers = {
                    str(row["name"]): str(row["sql"]) for row in trigger_rows
                }
                self.assertEqual(set(triggers), set(trigger_names))
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for trigger_name in trigger_names:
                        connection.execute(f'DROP TRIGGER "{trigger_name}"')
                    connection.execute(
                        "UPDATE verification_runner_observations "
                        "SET reason = ?, sanitized_result_digest = ? "
                        "WHERE verification_runner_observation_id = ?",
                        (
                            observation["reason"],
                            observation["sanitized_result_digest"],
                            observation["verification_runner_observation_id"],
                        ),
                    )
                    connection.execute(
                        "UPDATE evidence_references SET digest = ? "
                        "WHERE source_kind = 'runner_observation' AND source_id = ?",
                        (
                            reference["digest"],
                            observation["verification_runner_observation_id"],
                        ),
                    )
                    for trigger_name in trigger_names:
                        connection.execute(triggers[trigger_name])
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

            with closing(storage_module.connect(target.db_path)) as connection:
                with self.assertRaises(storage_module.StorageError) as rejected:
                    storage_module.validate_schema21_storage(connection)
                self.assertEqual(
                    rejected.exception.code,
                    "project_state_unreadable",
                )

            result = install.run(
                "task",
                "show",
                "--repo",
                str(install.project_root),
                task_id,
                "--json",
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["errors"],
                [
                    {
                        "code": "completion_history_inconsistent",
                        "message": "stored completion history is inconsistent",
                    }
                ],
            )

    def test_runner_graph_validation_is_not_repeated_per_bundle(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-runner-validation-bound-",
            dir=ROOT,
        ) as temporary:
            install, target, first_task_id = _seed_completed_m21_fixture(
                self,
                Path(temporary),
            )
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=install.project_root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout.strip()
            second_task_id = _add_completed_m21_task(
                self,
                install,
                title="Second persisted schema21 Runner history",
                commit=commit,
            )
            publication = publish_setup_evidence_projection(
                target,
                observed_at="2026-08-28T00:02:00Z",
            )
            self.assertEqual(publication.code, "succeeded")
            index = json.loads(target.resolved_evidence_index.read_bytes())
            payloads: dict[str, dict[str, object]] = {}
            for task_id in (first_task_id, second_task_id):
                entry = next(
                    row
                    for row in index["payload"]["entries"]
                    if row["task_id"] == task_id
                )
                payloads[task_id] = json.loads(
                    (target.resolved_evidence_root / entry["bundle_file"]).read_bytes()
                )["payload"]

            with closing(storage_module.connect(target.db_path)) as connection:
                for task_id, token in (
                    (first_task_id, "e" * 16),
                    (second_task_id, "f" * 16),
                ):
                    _persist_later_runner_history_fixture(
                        connection,
                        task_id=task_id,
                        original_payload=payloads[task_id],
                        token=token,
                    )
                graph_validator = (
                    storage_module._validated_verification_runner_graph
                )
                with mock.patch.object(
                    storage_module,
                    "_validated_verification_runner_graph",
                    wraps=graph_validator,
                ) as validate_graph:
                    storage_module.validate_completion_evidence_bundle_storage(
                        connection
                    )
                    self.assertEqual(validate_graph.call_count, 1)
                    validate_graph.reset_mock()
                    projection = storage_module.capture_evidence_projection_basis(
                        connection,
                        project_id=target.project.project_id,
                    )
                    self.assertEqual(validate_graph.call_count, 2)
                    validate_graph.reset_mock()
                    storage_module.validate_schema21_storage(connection)
                    self.assertEqual(validate_graph.call_count, 3)
                    validate_graph.reset_mock()
                    with closing(
                        storage_module.connect_snapshot_readonly(target.db_path)
                    ) as snapshot_connection:
                        snapshot = build_viewer_snapshot(
                            snapshot_connection,
                            target,
                            generated_at="2026-08-28T00:03:00Z",
                        ).snapshot
                    self.assertEqual(validate_graph.call_count, 2)
                self.assertEqual(len(projection.native_bundles), 2)
                self.assertEqual(snapshot["source_schema_version"], 21)

    def test_relocation_token_context_retains_source21_and_admits_prepared22(self) -> None:
        values = {
            "project_id": "project-000000000000",
            "identity_scheme": "legacy_path_v1",
            "binding_generation": 1,
            "old_path_hash": "1" * 64,
            "new_path_hash": "2" * 64,
            "source_layout": "fixed_current_v1",
        }
        self.assertEqual(
            RelocationContext(**values, source_schema_version=21).source_schema_version,
            21,
        )
        self.assertEqual(
            RelocationContext(**values, source_schema_version=22).source_schema_version,
            22,
        )
        with self.assertRaises(RelocationTokenError):
            RelocationContext(**values, source_schema_version=23)


if __name__ == "__main__":
    unittest.main()
