"""Temporary M23 facade; Evidence re-exports retire with TG-M23R.10."""

from __future__ import annotations

from tests.evidence_test_support import (
    ARTIFACT_MANIFEST_DOMAIN,
    BUNDLE_DOMAIN,
    CRITERION_DOMAIN,
    EVIDENCE_REFERENCE_DOMAIN,
    INDEX_DOMAIN,
    REVIEW_PROVENANCE_DOMAIN,
    _reference_source_projection,
    domain_digest,
    index_entries,
    reference_json_bytes,
    refresh_bundle_seals,
    refresh_inner_digests,
    reidentify_native_payload,
    sample_payload,
    sealed_bundle,
    v1_native_payload,
    valid_native_payload,
    write_evidence_tree,
    write_mixed_evidence_tree,
)


def expected_markdown_v1(envelope: dict[str, object]) -> bytes:
    """Return the test-owned exact Markdown v1 framing for one report."""

    payload = envelope["payload"]
    identity = {
        "report_schema_version": envelope["report_schema_version"],
        "report_digest": envelope["report_digest"],
        "report_id": payload["report_id"],
        "analysis_job_id": payload["analysis_job_id"],
        "source_kind": payload["source_kind"],
        "source_key": payload["source_key"],
        "recipe_digest": payload["recipe_digest"],
        "inference_state": payload["inference_state"],
    }
    values = (
        ("Identity", identity),
        ("Structural Facts", payload["structural_facts"]),
        ("Trusted Caller Declarations", payload["trusted_caller_declarations"]),
        ("Legacy Absence", payload["legacy_absence"]),
        ("LLM Derived", payload["llm_derived"]),
        ("Omissions", payload["omissions"]),
        ("Uncertainties", payload["uncertainties"]),
        ("Declared Code Occurrences", payload["declared_code_occurrences"]),
        ("Citations", payload["citations"]),
        ("Reproducibility", payload["reproducibility"]),
    )
    blocks = [
        b"## "
        + name.encode("utf-8")
        + b"\n\n    "
        + reference_json_bytes(value)
        for name, value in values
    ]
    return (
        b"# Task Governance Analysis Report v1\n\n"
        + b"\n\n".join(blocks)
        + b"\n"
    )


def held_analysis_tree_snapshot(paths, *, session=None):
    """Return the exact handle-only analysis namespace snapshot."""

    from task_governance_tool import _analysis_win32 as win32_boundary
    from task_governance_tool.analysis_outbox import AnalysisOutboxSession

    owned = session is None
    if owned:
        session = AnalysisOutboxSession.acquire(paths)
    opened = []
    rows = []
    try:
        root = session._borrow_analysis_root()
        rows.extend(
            ("analysis", item.name, item.file_id, item.size, item.is_directory)
            for item in win32_boundary.enumerate_held_directory(
                root,
                maximum_entries=6,
            )
        )
        parents = (
            ("outbox", session._directories.outbox),
            ("status", session._directories.status),
        )
        extras = []
        for name in ("reports", "rendered", "tmp"):
            handle = win32_boundary.open_relative_directory(
                root,
                name,
                win32_boundary.R0,
                kind="test-snapshot-" + name,
            )
            opened.append(handle)
            extras.append((name, handle))
        for directory_name, parent in parents + tuple(extras):
            entries = win32_boundary.enumerate_held_directory(
                parent,
                maximum_entries=100_000 if directory_name != "tmp" else 32,
            )
            for entry in entries:
                content = None
                if not entry.is_directory:
                    leaf = win32_boundary.open_relative_file_if_present(
                        parent,
                        entry.name,
                        maximum=10_000_000,
                        kind="test-snapshot-leaf",
                    )
                    if leaf is None:
                        raise AssertionError("snapshot leaf disappeared")
                    try:
                        content = win32_boundary.read_handle_capped(
                            leaf,
                            maximum=10_000_000,
                        )
                    finally:
                        leaf.close()
                rows.append(
                    (
                        directory_name,
                        entry.name,
                        entry.file_id,
                        entry.size,
                        entry.is_directory,
                        entry.is_reparse,
                        content,
                    )
                )
        return tuple(sorted(rows, key=repr))
    finally:
        for handle in reversed(opened):
            handle.close()
        if owned:
            session.release_normal()
