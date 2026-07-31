"""Unit-bound no-rerun lifecycle for the fixed M20.3/M20.4 study.

This is repository-development tooling, not an installable package module or
public ``taskgov`` command.  It deliberately does not launch subjects or
retain raw trial/control material.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.m20_observation import (
    COLLECTION_RECEIPT_SCHEMA,
    CORPUS_FAILURE_SCHEMA,
    M20_4_EPISODE_PLAN_CANONICAL_SHA256,
    PROTOCOL_CANONICAL_SHA256,
    CollectionLock,
    M20ObservationError,
    _atomic_write,
    _exact_keys,
    _excluded_rows,
    _fail,
    _fixed_output,
    _load_json_bytes,
    _privacy_check,
    _sanitize_records_for_retention,
    canonical_json_bytes,
    derive_inventory,
    load_m20_4_episode_plan,
    load_protocol,
    observation_id,
    serialize_corpus,
    validate_observation,
)


JOURNAL_SCHEMA = "m20-fresh-attempt-journal-v1"
SUPPORTED_UNITS = frozenset({"M20.3", "M20.4"})
_UNIT_STEMS = {"M20.3": "m20.3", "M20.4": "m20.4"}
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_ATTEMPT_ID = re.compile(r"[a-z0-9._-]{1,64}\Z")
_COMMITMENT_KEYS = frozenset(
    {
        "workload_digest",
        "control_digest",
        "observer_config_digest",
        "trial_root_digest",
        "trial_root_parent_digest",
        "trial_root_identity_digest",
    }
)


def trial_root_digest(path: Path) -> str:
    """Hash one normalized absolute trial path without retaining the path."""

    candidate = Path(path)
    if not candidate.is_absolute():
        _fail("unsafe_source_root")
    normalized = os.path.normcase(str(candidate.resolve(strict=False)))
    return hashlib.sha256(
        b"m20-trial-root-v1\0" + normalized.encode("utf-8", errors="strict")
    ).hexdigest()


def trial_root_parent_digest(path: Path) -> str:
    """Hash the normalized parent shared by every M20.4 trial root."""

    candidate = Path(path)
    if not candidate.is_absolute():
        _fail("unsafe_source_root")
    return trial_root_digest(candidate.resolve(strict=False).parent)


def trial_root_identity_digest(path: Path) -> str:
    """Bind a live trial root to its normalized path and directory identity."""

    candidate = Path(path).resolve(strict=True)
    try:
        info = candidate.stat()
    except OSError:
        _fail("source_missing")
    if not candidate.is_dir():
        _fail("unsafe_source_root")
    payload = canonical_json_bytes(
        {
            "path_digest": trial_root_digest(candidate),
            "device": info.st_dev,
            "inode": info.st_ino,
        }
    )
    return hashlib.sha256(b"m20-trial-identity-v1\0" + payload).hexdigest()


def _commitment(value: Any) -> dict[str, str]:
    item = _exact_keys(value, _COMMITMENT_KEYS)
    if any(
        not isinstance(item[key], str) or _HEX_64.fullmatch(item[key]) is None
        for key in _COMMITMENT_KEYS
    ):
        _fail("source_drift")
    return dict(item)
_RECEIPT_CORE_KEYS = frozenset(
    {
        "schema",
        "unit",
        "authority_revision",
        "baseline_revision",
        "protocol_sha256",
        "status",
        "artifact_status",
        "retirement_revision",
        "record_count",
        "corpus_bytes",
        "corpus_sha256",
        "eligible_records",
        "partial_records",
        "excluded_records",
        "outcome",
    }
)


def _unit(value: str) -> str:
    if value not in SUPPORTED_UNITS:
        _fail("parse_failed")
    return value


class FreshCollectionLifecycle:
    """Own one fixed M20.3 or M20.4 collection lifecycle.

    Raw projects and ephemeral control bundles, when materialized by the
    external trial orchestrator, must live below ``raw_root`` and
    ``control_root`` respectively.  This class never reads them.  Finalization
    succeeds only after both roots have been removed.
    """

    def __init__(self, repo_root: Path, unit: str) -> None:
        self.repo_root = Path(repo_root).resolve(strict=True)
        self.unit = _unit(unit)
        stem = _UNIT_STEMS[self.unit]
        self.receipt_path = self.repo_root / "fixtures" / "m20" / (
            f"{stem}-collection-receipt.json"
        )
        self.corpus_path = self.repo_root / "dist" / "m20" / (
            f"{stem}-observations.json"
        )
        self.journal_path = self.repo_root / "dist" / "m20" / (
            f"{stem}-attempt-journal.json"
        )
        self.lock_path = self.repo_root / "dist" / "m20" / (
            f"{stem}-collector.lock"
        )
        self.raw_root = self.repo_root / "dist" / "m20" / f"{stem}-raw"
        self.control_root = self.repo_root / "dist" / "m20" / f"{stem}-control"

    def _receipt_exists(self) -> bool:
        return os.path.lexists(self.receipt_path)

    def _ensure_open(self) -> None:
        # This check intentionally precedes protocol, journal, corpus, or raw
        # source access.  The tracked receipt is the no-rerun tombstone.
        if self._receipt_exists():
            _fail("collection_closed")

    def _protocol(self) -> dict[str, Any]:
        return load_protocol(self.repo_root)

    def _ignored_path(self, path: Path) -> Path:
        return _fixed_output(
            self.repo_root,
            path.relative_to(self.repo_root).as_posix(),
            require_ignored=True,
        )

    def _tracked_path(self, path: Path) -> Path:
        return _fixed_output(
            self.repo_root,
            path.relative_to(self.repo_root).as_posix(),
            require_ignored=False,
        )

    def _paths(self) -> None:
        self.receipt_path = self._tracked_path(self.receipt_path)
        self.corpus_path = self._ignored_path(self.corpus_path)
        self.journal_path = self._ignored_path(self.journal_path)
        self.lock_path = self._ignored_path(self.lock_path)
        self.raw_root = self._ignored_path(self.raw_root)
        self.control_root = self._ignored_path(self.control_root)

    def _inventory(
        self, protocol: Mapping[str, Any]
    ) -> tuple[tuple[Any, ...], ...]:
        rows = derive_inventory(protocol, self.unit)
        if not rows or any(row[2] is None for row in rows):
            _fail("source_drift")
        return rows

    def _rows_by_attempt(
        self, protocol: Mapping[str, Any]
    ) -> dict[str, tuple[tuple[Any, ...], ...]]:
        grouped: dict[str, list[tuple[Any, ...]]] = {}
        for row in self._inventory(protocol):
            grouped.setdefault(str(row[2]), []).append(row)
        return {key: tuple(value) for key, value in grouped.items()}

    def _journal_identity(self, protocol: Mapping[str, Any]) -> dict[str, Any]:
        authority = protocol["authority"]
        identity = {
            "schema": JOURNAL_SCHEMA,
            "unit": self.unit,
            "contract_id": authority["contract_id"],
            "contract_revision": authority["contract_revision"],
            "baseline_revision": authority["baseline_revision"],
            "authority_revision": authority["authority_revision"],
            "protocol_sha256": PROTOCOL_CANONICAL_SHA256,
        }
        if self.unit == "M20.4":
            load_m20_4_episode_plan(protocol, self.repo_root)
            identity["episode_plan_canonical_sha256"] = (
                M20_4_EPISODE_PLAN_CANONICAL_SHA256
            )
        return identity

    def _empty_journal(self, protocol: Mapping[str, Any]) -> dict[str, Any]:
        return {**self._journal_identity(protocol), "attempts": {}}

    def _read_journal(self, protocol: Mapping[str, Any]) -> dict[str, Any]:
        if not self.journal_path.exists():
            return self._empty_journal(protocol)
        if self.journal_path.is_symlink() or not self.journal_path.is_file():
            _fail("source_drift")
        try:
            raw = self.journal_path.read_bytes()
        except OSError:
            _fail("source_missing")
        if (
            not raw
            or len(raw) > protocol["bounds"]["unit_corpus_bytes"] + 32_768
        ):
            _fail("source_drift")
        _privacy_check(raw)
        value = _load_json_bytes(raw)
        identity = self._journal_identity(protocol)
        root = _exact_keys(value, {*identity, "attempts"})
        if (
            any(root[key] != expected for key, expected in identity.items())
            or not isinstance(root["attempts"], dict)
            or raw != canonical_json_bytes(root)
        ):
            _fail("source_drift")
        rows_by_attempt = self._rows_by_attempt(protocol)
        for attempt_id, raw_state in root["attempts"].items():
            if attempt_id not in rows_by_attempt:
                _fail("source_drift")
            state_keys = (
                {"status", "records", "commitment"}
                if self.unit == "M20.4"
                else {"status", "records"}
            )
            state = _exact_keys(raw_state, state_keys)
            if self.unit == "M20.4":
                _commitment(state["commitment"])
            if state["status"] in ("started", "reducing"):
                if state["records"] != []:
                    _fail("source_drift")
                continue
            if state["status"] != "reduced" or not isinstance(
                state["records"], list
            ):
                _fail("source_drift")
            records = [validate_observation(protocol, item) for item in state["records"]]
            expected = {
                observation_id(protocol, row) for row in rows_by_attempt[attempt_id]
            }
            actual = [record["observation_id"] for record in records]
            if len(actual) != len(expected) or set(actual) != expected:
                _fail("source_drift")
        if self.unit == "M20.4":
            commitments = [
                _commitment(state["commitment"])
                for state in root["attempts"].values()
            ]
            root_digests = [item["trial_root_digest"] for item in commitments]
            parent_digests = {
                item["trial_root_parent_digest"] for item in commitments
            }
            if len(root_digests) != len(set(root_digests)) or len(parent_digests) > 1:
                _fail("source_drift")
        return dict(root)

    def _write_journal(self, journal: Mapping[str, Any]) -> None:
        payload = canonical_json_bytes(journal)
        _privacy_check(payload)
        _atomic_write(self.journal_path, payload)

    def expected_attempts(self) -> tuple[str, ...]:
        self._ensure_open()
        protocol = self._protocol()
        return tuple(
            sorted(self._rows_by_attempt(protocol), key=lambda value: value.encode("ascii"))
        )

    def attempt_lock_path(self, attempt_id: str) -> Path:
        """Return the fixed per-attempt lock shared by start, capture, and resume."""

        if (
            not isinstance(attempt_id, str)
            or _ATTEMPT_ID.fullmatch(attempt_id) is None
            or attempt_id not in self._rows_by_attempt(self._protocol())
        ):
            _fail("source_drift")
        return self.lock_path.with_name(f"{self.lock_path.name}.{attempt_id}.capture")

    def attempt_commitment(self, attempt_id: str) -> dict[str, str]:
        """Return one safe M20.4 launch commitment for internal controller use."""

        if self.unit != "M20.4":
            _fail("source_drift")
        self._ensure_open()
        protocol = self._protocol()
        self._paths()
        journal = self._read_journal(protocol)
        state = journal["attempts"].get(attempt_id)
        if state is None or state["status"] not in {"started", "reducing"}:
            _fail("attempt_not_started")
        return _commitment(state["commitment"])

    def _validate_start_group(
        self,
        attempt_ids: Sequence[str],
        rows_by_attempt: Mapping[str, Sequence[Sequence[Any]]],
    ) -> None:
        if not attempt_ids or len(attempt_ids) != len(set(attempt_ids)):
            _fail("parse_failed")
        if any(attempt_id not in rows_by_attempt for attempt_id in attempt_ids):
            _fail("source_drift")
        if self.unit == "M20.3":
            if len(attempt_ids) != 1:
                _fail("source_drift")
            return
        scenarios = {str(rows_by_attempt[item][0][1]) for item in attempt_ids}
        if len(scenarios) != 1:
            _fail("source_drift")
        scenario_id = next(iter(scenarios))
        expected = (
            {f"{scenario_id}.broad.01"}
            if scenario_id == "sp_handoff_control"
            else {
                f"{scenario_id}.broad.01",
                f"{scenario_id}.bounded.01",
            }
        )
        if set(attempt_ids) != expected:
            _fail("paired_start_required")

    def start(
        self,
        attempt_id: str,
        *,
        commitment: Mapping[str, str] | None = None,
    ) -> None:
        commitments = None if commitment is None else {attempt_id: commitment}
        self.start_many((attempt_id,), commitments=commitments)

    def start_many(
        self,
        attempt_ids: Iterable[str],
        *,
        commitments: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._ensure_open()
        identifiers = tuple(attempt_ids)
        protocol = self._protocol()
        self._paths()
        rows_by_attempt = self._rows_by_attempt(protocol)
        self._validate_start_group(identifiers, rows_by_attempt)
        if self.unit == "M20.4":
            if not isinstance(commitments, Mapping) or set(commitments) != set(
                identifiers
            ):
                _fail("control_commitment_required")
            validated_commitments = {
                attempt_id: _commitment(commitments[attempt_id])
                for attempt_id in identifiers
            }
        else:
            if commitments is not None:
                _fail("source_drift")
            validated_commitments = {}
        with ExitStack() as attempt_locks:
            if self.unit == "M20.4":
                for attempt_id in sorted(identifiers, key=lambda value: value.encode("ascii")):
                    attempt_locks.enter_context(
                        CollectionLock(self.attempt_lock_path(attempt_id))
                    )
            with CollectionLock(self.lock_path):
                self._ensure_open()
                journal = self._read_journal(protocol)
                if any(item in journal["attempts"] for item in identifiers):
                    _fail("attempt_already_started")
                if self.unit == "M20.4":
                    candidate_roots = [
                        validated_commitments[attempt_id]["trial_root_digest"]
                        for attempt_id in identifiers
                    ]
                    candidate_parents = {
                        validated_commitments[attempt_id]["trial_root_parent_digest"]
                        for attempt_id in identifiers
                    }
                    existing_roots = {
                        state["commitment"]["trial_root_digest"]
                        for state in journal["attempts"].values()
                    }
                    existing_parents = {
                        state["commitment"]["trial_root_parent_digest"]
                        for state in journal["attempts"].values()
                    }
                    if (
                        len(set(candidate_roots)) != len(candidate_roots)
                        or set(candidate_roots) & existing_roots
                        or len(candidate_parents) != 1
                        or (existing_parents and candidate_parents != existing_parents)
                    ):
                        _fail("contaminated")
                for attempt_id in identifiers:
                    state = {
                        "status": "started",
                        "records": [],
                    }
                    if self.unit == "M20.4":
                        state["commitment"] = validated_commitments[attempt_id]
                    journal["attempts"][attempt_id] = state
                self._write_journal(journal)

    def resume_started(self) -> tuple[str, ...]:
        """Terminalize interrupted starts or reductions without relaunching."""

        self._ensure_open()
        protocol = self._protocol()
        self._paths()
        rows_by_attempt = self._rows_by_attempt(protocol)
        with ExitStack() as attempt_locks:
            if self.unit == "M20.4":
                for attempt_id in sorted(
                    rows_by_attempt, key=lambda value: value.encode("ascii")
                ):
                    attempt_locks.enter_context(
                        CollectionLock(self.attempt_lock_path(attempt_id))
                    )
            with CollectionLock(self.lock_path):
                self._ensure_open()
                journal = self._read_journal(protocol)
                interrupted = tuple(
                    sorted(
                        (
                            attempt_id
                            for attempt_id, state in journal["attempts"].items()
                            if state["status"] in ("started", "reducing")
                        ),
                        key=lambda value: value.encode("ascii"),
                    )
                )
                for attempt_id in interrupted:
                    replacement = {
                        "status": "reduced",
                        "records": _excluded_rows(
                            protocol,
                            rows_by_attempt[attempt_id],
                            "source_missing",
                        ),
                    }
                    if self.unit == "M20.4":
                        replacement["commitment"] = journal["attempts"][attempt_id][
                            "commitment"
                        ]
                    journal["attempts"][attempt_id] = replacement
                if interrupted:
                    self._write_journal(journal)
                return interrupted

    def claim(self, attempt_id: str) -> None:
        """Atomically claim one started attempt before opening trial sources."""

        self._ensure_open()
        protocol = self._protocol()
        self._paths()
        rows_by_attempt = self._rows_by_attempt(protocol)
        if attempt_id not in rows_by_attempt:
            _fail("source_drift")
        with CollectionLock(self.lock_path):
            self._ensure_open()
            journal = self._read_journal(protocol)
            state = journal["attempts"].get(attempt_id)
            if state is None or state["status"] != "started":
                _fail("attempt_not_started")
            replacement = {
                "status": "reducing",
                "records": [],
            }
            if self.unit == "M20.4":
                replacement["commitment"] = state["commitment"]
            journal["attempts"][attempt_id] = replacement
            self._write_journal(journal)

    def finish(
        self,
        attempt_id: str,
        records: Iterable[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        """Finish exactly one claimed reduction with only its frozen rows."""

        self._ensure_open()
        protocol = self._protocol()
        self._paths()
        rows_by_attempt = self._rows_by_attempt(protocol)
        if attempt_id not in rows_by_attempt:
            _fail("source_drift")
        with CollectionLock(self.lock_path):
            self._ensure_open()
            journal = self._read_journal(protocol)
            state = journal["attempts"].get(attempt_id)
            if state is None or state["status"] != "reducing":
                _fail("attempt_not_started")
            maximum = len(rows_by_attempt[attempt_id])
            bounded_records = tuple(itertools.islice(records, maximum + 1))
            retained = _sanitize_records_for_retention(
                protocol,
                rows_by_attempt[attempt_id],
                bounded_records,
            )
            replacement = {
                "status": "reduced",
                "records": retained,
            }
            if self.unit == "M20.4":
                replacement["commitment"] = state["commitment"]
            journal["attempts"][attempt_id] = replacement
            self._write_journal(journal)
        return tuple(retained)

    def _readback_corpus(
        self, protocol: Mapping[str, Any], raw: bytes
    ) -> list[dict[str, Any]] | None:
        if not raw or len(raw) > protocol["bounds"]["unit_corpus_bytes"]:
            _fail("source_drift")
        parsed = _load_json_bytes(raw)
        if isinstance(parsed, dict):
            failure = _exact_keys(
                parsed,
                {"schema", "unit", "reason", "record_count", "candidate_bytes"},
            )
            if (
                failure["schema"] != CORPUS_FAILURE_SCHEMA
                or failure["unit"] != self.unit
                or failure["reason"] != "cap_exceeded"
                or type(failure["record_count"]) is not int
                or type(failure["candidate_bytes"]) is not int
                or failure["record_count"] < 0
                or failure["candidate_bytes"] < 0
                or canonical_json_bytes(failure) != raw
            ):
                _fail("source_drift")
            return None
        if not isinstance(parsed, list):
            _fail("parse_failed")
        canonical = serialize_corpus(protocol, self.unit, parsed)
        if canonical != raw:
            _fail("source_drift")
        _privacy_check(raw)
        return parsed

    def _receipt(
        self,
        protocol: Mapping[str, Any],
        raw: bytes,
        records: Sequence[Mapping[str, Any]] | None,
    ) -> dict[str, Any]:
        counts = Counter(
            str(record["eligibility"]) for record in (records or ())
        )
        receipt = {
            "schema": COLLECTION_RECEIPT_SCHEMA,
            "unit": self.unit,
            "authority_revision": protocol["authority"]["authority_revision"],
            "baseline_revision": protocol["authority"]["baseline_revision"],
            "protocol_sha256": PROTOCOL_CANONICAL_SHA256,
            "status": "closed",
            "artifact_status": "retained",
            "retirement_revision": None,
            "record_count": 0 if records is None else len(records),
            "corpus_bytes": len(raw),
            "corpus_sha256": hashlib.sha256(raw).hexdigest(),
            "eligible_records": counts["eligible"],
            "partial_records": counts["partial"],
            "excluded_records": counts["excluded"],
            "outcome": "collection_complete",
        }
        if self.unit == "M20.4":
            receipt["episode_plan_canonical_sha256"] = (
                M20_4_EPISODE_PLAN_CANONICAL_SHA256
            )
        return receipt

    def _validate_receipt(
        self,
        protocol: Mapping[str, Any],
        raw_receipt: bytes,
        corpus: bytes,
        records: Sequence[Mapping[str, Any]] | None,
    ) -> dict[str, Any]:
        if not raw_receipt or len(raw_receipt) > 4096:
            _fail("source_drift")
        expected_keys = set(_RECEIPT_CORE_KEYS)
        if self.unit == "M20.4":
            expected_keys.add("episode_plan_canonical_sha256")
        receipt = _exact_keys(_load_json_bytes(raw_receipt), expected_keys)
        if raw_receipt != canonical_json_bytes(receipt) + b"\n":
            _fail("source_drift")
        expected = self._receipt(protocol, corpus, records)
        if receipt != expected:
            _fail("source_drift")
        return dict(receipt)

    def _extra_source_roots(
        self, roots: Iterable[Path]
    ) -> tuple[Path, ...]:
        validated: list[Path] = []
        try:
            bounded = tuple(itertools.islice(roots, 33))
        except TypeError:
            _fail("unsafe_source_root")
        if len(bounded) > 32:
            _fail("unsafe_source_root")
        for raw_root in bounded:
            try:
                candidate = Path(raw_root)
            except TypeError:
                _fail("unsafe_source_root")
            if not candidate.is_absolute():
                _fail("unsafe_source_root")
            resolved = candidate.resolve(strict=False)
            filesystem_root = Path(resolved.anchor).resolve(strict=False)
            try:
                self.repo_root.relative_to(resolved)
                contains_repository = True
            except ValueError:
                contains_repository = False
            if (
                resolved == filesystem_root
                or resolved.parent == filesystem_root
                or resolved == self.repo_root
                or contains_repository
            ):
                _fail("unsafe_source_root")
            validated.append(resolved)
        if len(validated) != len(set(validated)):
            _fail("unsafe_source_root")
        return tuple(validated)

    def _sources_absent(self, extra_source_roots: Iterable[Path] = ()) -> None:
        extra = self._extra_source_roots(extra_source_roots)
        if os.path.lexists(self.raw_root) or os.path.lexists(self.control_root):
            _fail("raw_material_present")
        if any(os.path.lexists(path) for path in extra):
            _fail("raw_material_present")

    def finalize(
        self, *, extra_source_roots: Iterable[Path] = ()
    ) -> dict[str, Any]:
        """Close a complete unit after all raw/control sources were removed."""

        supplied_roots = tuple(extra_source_roots)
        self._ensure_open()
        protocol = self._protocol()
        self._paths()
        expected = self._rows_by_attempt(protocol)
        with CollectionLock(self.lock_path):
            self._ensure_open()
            journal = self._read_journal(protocol)
            if set(journal["attempts"]) != set(expected) or any(
                state["status"] != "reduced"
                for state in journal["attempts"].values()
            ):
                _fail("collection_incomplete")
            if self.unit == "M20.4":
                committed_roots = {
                    state["commitment"]["trial_root_digest"]
                    for state in journal["attempts"].values()
                }
                supplied_digests = {
                    trial_root_digest(path) for path in supplied_roots
                }
                if (
                    len(supplied_roots) != len(journal["attempts"])
                    or len(supplied_digests) != len(supplied_roots)
                    or supplied_digests != committed_roots
                ):
                    _fail("raw_material_present")
            self._sources_absent(supplied_roots)
            records: list[dict[str, Any]] = []
            for attempt_id in sorted(
                journal["attempts"], key=lambda value: value.encode("ascii")
            ):
                records.extend(journal["attempts"][attempt_id]["records"])
            corpus = serialize_corpus(protocol, self.unit, records)
            _privacy_check(corpus)
            _atomic_write(self.corpus_path, corpus)
            try:
                retained = self.corpus_path.read_bytes()
            except OSError:
                _fail("source_missing")
            readback = self._readback_corpus(protocol, retained)
            receipt = self._receipt(protocol, retained, readback)
            _atomic_write(
                self.receipt_path,
                canonical_json_bytes(receipt) + b"\n",
            )
            try:
                written_receipt = self.receipt_path.read_bytes()
            except OSError:
                _fail("source_missing")
            self._validate_receipt(
                protocol,
                written_receipt,
                retained,
                readback,
            )
            try:
                self.journal_path.unlink()
            except OSError:
                _fail("artifact_delete_failed")
            return receipt


def check_fresh_collection(repo_root: Path, unit: str) -> dict[str, Any]:
    """Read-only validation of an open or terminal fresh collection."""

    lifecycle = FreshCollectionLifecycle(repo_root, unit)
    receipt_exists = lifecycle._receipt_exists()
    protocol = lifecycle._protocol()
    lifecycle._paths()
    if not receipt_exists:
        if lifecycle.corpus_path.exists():
            _fail("source_drift")
        journal = lifecycle._read_journal(protocol)
        started = sum(
            state["status"] in ("started", "reducing")
            for state in journal["attempts"].values()
        )
        reduced = sum(
            state["status"] == "reduced" for state in journal["attempts"].values()
        )
        return {
            "artifact_status": "absent",
            "started_attempts": started,
            "reduced_attempts": reduced,
            "record_count": sum(
                len(state["records"])
                for state in journal["attempts"].values()
                if state["status"] == "reduced"
            ),
        }
    if lifecycle.journal_path.exists():
        _fail("source_drift")
    lifecycle._sources_absent()
    if not lifecycle.corpus_path.is_file() or lifecycle.corpus_path.is_symlink():
        _fail("source_missing")
    try:
        corpus = lifecycle.corpus_path.read_bytes()
        raw_receipt = lifecycle.receipt_path.read_bytes()
    except OSError:
        _fail("source_missing")
    records = lifecycle._readback_corpus(protocol, corpus)
    receipt = lifecycle._validate_receipt(
        protocol,
        raw_receipt,
        corpus,
        records,
    )
    return {
        "artifact_status": receipt["artifact_status"],
        "started_attempts": 0,
        "reduced_attempts": len(lifecycle._rows_by_attempt(protocol)),
        "record_count": receipt["record_count"],
        "corpus_bytes": receipt["corpus_bytes"],
        "corpus_sha256": receipt["corpus_sha256"],
    }


__all__ = [
    "FreshCollectionLifecycle",
    "JOURNAL_SCHEMA",
    "check_fresh_collection",
    "trial_root_digest",
    "trial_root_identity_digest",
    "trial_root_parent_digest",
]
