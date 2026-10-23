"""Read-only, selected-assignment admission for pending numerical locators.

This repository authorizes retaining a location, never reading an unregistered
reviewer's log, collecting an answer, or satisfying a review gate. Only native
collected-answer assignments qualify. Legacy review registration is unchanged.
No Task/preparation inventory, answer body or reviewed material is consumed.
"""

from contextlib import closing
from hashlib import sha256
import json
from time import monotonic

from . import review_continuation as continuation
from . import review_preparation_repository as preparation
from . import review_answer_values as answers
from .review_answer_collection_repository import CollectionRepository
from .review_session_repository import read_bindings, _receipt_target
from .review_wait_runtime import continuation_transition_repository as transitions
from .review_wait_runtime.request_continuation import decode as decode_round
from .review_wait_runtime.review_wait_repository import ReviewWaitRepository
from .session_identity import is_session_id
from .state_paths import path_lexically_exists
from .state_resolver import canonical_state_paths
from .storage import connect_initialized_readonly
from .task_ownership import read_basis as read_owner
from .usage_values import UsageError


_NATIVE = ("project_id", "project_path_hash", "project_binding_generation", "task_id",
    "contract_revision", "execution_id", "ownership_generation", "target_kind", "target_value",
    "target_base_revision", "target_generation", "parent_session_id")
_FIELDS = frozenset((*_NATIVE, "version", "reviewer_session_id", "preparation_id",
    "continuation_id", "slot", "assignment_digest", "source_basis_digest"))


def validate_pending_review(value):
    """Closed scalar persistence shape, without I/O or admission side effects."""
    if type(value) is not dict or set(value) != _FIELDS or type(value["version"]) is not int or value["version"] != 1:
        raise UsageError()
    for key, minimum in (("contract_revision", 0), ("project_binding_generation", 1),
            ("ownership_generation", 1), ("target_generation", 1), ("slot", 1)):
        if type(value[key]) is not int or not minimum <= value[key] <= (8 if key == "slot" else 2**63 - 1):
            raise UsageError()
    for key, prefix in (("task_id", "tg_task_"), ("execution_id", "tg_execution_"),
            ("preparation_id", "tg_review_preparation_")):
        if not continuation.identifier(value[key], prefix):
            raise UsageError()
    if value["continuation_id"] is not None and not continuation.identifier(value["continuation_id"], "tg_review_continuation_"):
        raise UsageError()
    if (not continuation._digest(value["project_path_hash"])
        or not all(is_session_id(value[key]) for key in ("parent_session_id", "reviewer_session_id"))
        or value["parent_session_id"] == value["reviewer_session_id"]):
        raise UsageError()
    for key in ("assignment_digest", "source_basis_digest"):
        if type(value[key]) is not str or not value[key].startswith("sha256:") or not continuation._digest(value[key][7:]):
            raise UsageError()
    for key in ("project_id", "target_value", "target_base_revision"):
        item = value[key]
        if (type(item) is not str or len(item) > 500 or (not item and key != "target_base_revision")
            or any(ord(char) < 32 or ord(char) == 127 for char in item)):
            raise UsageError()
        try:
            item.encode("utf-8", errors="strict")
        except UnicodeError:
            raise UsageError() from None
    if (value["target_kind"] not in ("git_commit", "git_snapshot", "diff_fingerprint", "external_revision")
        or (value["target_kind"] == "git_snapshot") != bool(value["target_base_revision"])):
        raise UsageError()
    return dict(value)


def _check_time(deadline):
    if deadline is not None and monotonic() >= deadline:
        raise UsageError()


def _digest(value):
    return "sha256:" + sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _paths(target):
    if not target.canonical_fixed or target.skill_root is None:
        raise UsageError()
    paths = canonical_state_paths(target.skill_root, repo=target.project.canonical_repo)
    if paths.database != target.db_path:
        raise UsageError()
    return paths


def _same_project(target, basis):
    return (basis["project_id"], basis["project_path_hash"], basis["project_binding_generation"]) == (
        target.project.project_id, target.binding_path_hash, target.binding_generation)


def _native(connection, target, task_id, parent_id):
    """One selected current row plus its validated ownership relationship."""
    owner = read_owner(connection, project_id=target.project.project_id, task_id=task_id)
    if owner.state not in ("owned", "completion_only") or (owner.owner_session_id or owner.completion_session_id) != parent_id:
        return None
    row = connection.execute("SELECT current_contract_revision,review_target_kind,review_target_value,"
        "review_target_base_revision,review_target_generation FROM tasks WHERE project_id=? AND task_id=?",
        (target.project.project_id, task_id)).fetchone()
    if row is None or row["review_target_generation"] < 1:
        return None
    return dict(project_id=target.project.project_id, project_path_hash=target.binding_path_hash,
        project_binding_generation=target.binding_generation, task_id=task_id,
        contract_revision=row["current_contract_revision"], execution_id=owner.execution_id,
        ownership_generation=owner.generation, parent_session_id=parent_id,
        **{"target_" + key: row["review_target_" + key] for key in ("kind", "value", "base_revision", "generation")})


def _schema(connection, schema, migration, deadline):
    _check_time(deadline)
    connection.set_progress_handler(lambda: int(deadline is not None and monotonic() >= deadline), 1000)
    expected = sorted(("trigger" if sql.startswith("CREATE TRIGGER") else "table", name,
        "preparations" if sql.startswith("CREATE TRIGGER") else name, sql) for name, sql in schema.items())
    actual = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    if (actual != expected or connection.execute("PRAGMA user_version").fetchone() != (1,)
        or connection.execute("SELECT * FROM schema_history").fetchall() != [(1, migration)]):
        raise UsageError()


def _root(paths, native, deadline):
    path = paths.review_preparation_store(native["parent_session_id"], native["task_id"], native["target_generation"])
    with ReviewWaitRepository(path)._connection() as connection:
        _schema(connection, preparation._SCHEMA, "review-preparation-v1", deadline)
        row = connection.execute("SELECT p.preparation_id,p.record_json FROM current AS c "
            "JOIN preparations AS p ON p.preparation_id=c.preparation_id WHERE c.singleton=1").fetchone()
        if row is None:
            raise UsageError()
        result = preparation._decode(row[1])
        if row[0] != result.preparation_id:
            raise UsageError()
        return result


def _round(paths, native, deadline):
    path = paths.review_continuation_store(native["parent_session_id"], native["task_id"], native["target_generation"])
    if not path_lexically_exists(path):
        return None
    with ReviewWaitRepository(path)._connection() as connection:
        _schema(connection, transitions._SCHEMA, "review-continuation-transition-v1", deadline)
        row = connection.execute("SELECT r.preparation_id,r.sequence,r.continuation_id,r.payload FROM current_round AS c "
            "JOIN rounds AS r ON r.ordinal=c.ordinal WHERE c.singleton=1").fetchone()
        if row is None:
            raise UsageError()
        result = decode_round(row[3])
        if row[:3] != (result.preparation_id, result.continuation_sequence, result.continuation_id):
            raise UsageError()
        return result


def _current_round(connection, root, operational):
    row = connection.execute("SELECT * FROM review_continuations WHERE task_id=? AND preparation_id=? "
        "ORDER BY sequence DESC LIMIT 1", (root.basis["task_id"], root.preparation_id)).fetchone()
    if row is None and operational is None:
        return None, ()
    if (row is None or operational is None or operational.preparation_id != root.preparation_id
        or row["continuation_id"] != operational.continuation_id or row["sequence"] != operational.continuation_sequence
        or row["project_id"] != root.basis["project_id"] or row["target_generation"] != root.basis["target_generation"]
        or continuation.validate_basis(json.loads(row["basis_json"])) != root.basis
        or row["basis_digest"] != continuation.basis_digest(root.basis)
        or row["basis_digest"] != operational.basis_digest or row["selection_digest"] != operational.selection_digest
        or row["route"] != root.route):
        raise UsageError()  # Includes the fence-before-native publication window.
    choices = connection.execute("SELECT session_id,action,prior_receipt_id,reason FROM review_continuation_reviewers "
        "WHERE continuation_id=? ORDER BY session_id LIMIT 9", (row["continuation_id"],)).fetchall()
    selected = continuation.validate_selection(dict(version=1, reviewers=[dict(choice) for choice in choices]))
    if continuation.selection_digest(selected) != row["selection_digest"]:
        raise UsageError()
    return row, selected


def _collection(paths, native, root, continuation_id, deadline):
    _check_time(deadline)
    repository = CollectionRepository(paths.review_answer_collection_store(native["parent_session_id"],
        native["task_id"], native["target_generation"], root.preparation_id, continuation_id))
    repository._connection_timeout = 0
    with repository._connection() as connection:
        connection.set_progress_handler(lambda: int(deadline is not None and monotonic() >= deadline), 1000)
        result = repository._load(connection)
    _check_time(deadline)
    return result


def _assignment(connection, paths, native, reviewer_id, deadline):
    root = _root(paths, native, deadline)
    if any(root.basis[key] != native[key] for key in _NATIVE):
        return None
    operational = _round(paths, native, deadline)
    current, selected = _current_round(connection, root, operational)
    selected_ids = [item.session_id for item in selected]
    continuation_id = None
    if reviewer_id in selected_ids:
        if (current["state"] not in ("prepared", "registered") or operational.phase not in ("prepared", "published")
            or operational.answer_protocol != "review-answer-v1"):
            return None
        continuation_id = current["continuation_id"]
        issued = operational.collection_basis_digests
    else:
        if root.version != 2:
            return None  # Legacy roots may have collected successors, not initial slots.
        # The current preparation has at most 64 native rounds. A selected
        # reviewer cannot fall back to its initial assignment after replacement.
        prior = connection.execute("SELECT 1 FROM review_continuations AS c JOIN review_continuation_reviewers AS r "
            "ON r.continuation_id=c.continuation_id WHERE c.task_id=? AND c.preparation_id=? AND r.session_id=? LIMIT 1",
            (native["task_id"], root.preparation_id, reviewer_id)).fetchone()
        if prior is not None:
            return None
        issued = tuple(answers.basis_digest(item) for item in root.collection_bases)
    records = _collection(paths, native, root, continuation_id, deadline)
    matches = [(slot, record) for slot, record in enumerate(records, 1)
        if record["binding"] is not None and record["binding"]["reviewer_session_id"] == reviewer_id]
    if len(matches) != 1:
        return None
    slot, record = matches[0]
    basis, binding = record["basis"], record["binding"]
    if (record.get("tracking_state") == "invalidated" or binding["parent_session_id"] != native["parent_session_id"]
        or basis["execution_basis"] != root.basis or basis["preparation_id"] != root.preparation_id
        or slot > len(issued) or answers.basis_digest(basis) != issued[slot - 1]
        or (basis["continuation"] is None) != (continuation_id is None)):
        return None
    if continuation_id is not None and (basis["continuation"] != dict(continuation_id=continuation_id,
            sequence=current["sequence"], selection_digest=current["selection_digest"])
        or slot > len(selected_ids) or selected_ids[slot - 1] != reviewer_id
        or basis["packet_digest"] != "sha256:" + operational.packet_digest):
        return None
    return validate_pending_review(dict(version=1, **native, reviewer_session_id=reviewer_id,
        preparation_id=root.preparation_id, continuation_id=continuation_id, slot=slot,
        assignment_digest=_digest(binding), source_basis_digest=answers.basis_digest(basis)))


def _registered(connection, pending):
    """Exact immutable Receipt/source relation, including completed Tasks.

    A session registered for some other review is insufficient. The collected
    source's issued basis, slot, reviewer and native execution must all match.
    This selected metadata read grants no completion/evidence admission.
    """
    rows = connection.execute("SELECT review_receipt_id,basis_json FROM review_collection_sources "
        "WHERE preparation_id=? AND slot=? AND reviewer_session_id=? AND basis_digest=? LIMIT 3",
        (pending["preparation_id"], pending["slot"], pending["reviewer_session_id"], pending["source_basis_digest"])).fetchall()
    if len(rows) > 2:
        raise UsageError()
    for row in rows:
        basis = answers.validate_basis(json.loads(row["basis_json"]))
        prior = basis["continuation"]
        if (answers.basis_digest(basis) != pending["source_basis_digest"]
            or basis["preparation_id"] != pending["preparation_id"]
            or (prior["continuation_id"] if prior else None) != pending["continuation_id"]
            or any(basis["execution_basis"][key] != pending[key] for key in _NATIVE)):
            raise UsageError()
        binding = read_bindings(connection, receipt_ids={row["review_receipt_id"]}).get(row["review_receipt_id"])
        target, kind, _ = _receipt_target(connection, row["review_receipt_id"])
        if (binding is not None and binding.session_id == pending["reviewer_session_id"]
            and binding.execution_id == pending["execution_id"] and binding.binding_source == "handoff"
            and kind == "independent" and all(getattr(target, key) == pending[key] for key in target.__dataclass_fields__)):
            return True
    return False


def find_pending_assignment(target, parent_id, reviewer_id, deadline=None):
    """Return one proved actual assignment, or None without granting retention."""
    try:
        _check_time(deadline)
        if not is_session_id(parent_id) or not is_session_id(reviewer_id) or parent_id == reviewer_id:
            return None
        paths = _paths(target)
        with closing(connect_initialized_readonly(target, operation_local=True, deadline=deadline)) as connection:
            rows = connection.execute("SELECT task_id FROM task_ownership WHERE project_id=? "
                "AND coalesce(owner_session_id,completion_session_id)=? AND state IN ('owned','completion_only') LIMIT 2",
                (target.project.project_id, parent_id)).fetchall()
            if len(rows) != 1:
                return None
            native = _native(connection, target, rows[0][0], parent_id)
            result = _assignment(connection, paths, native, reviewer_id, deadline) if native is not None else None
            _check_time(deadline)
            return result
    except Exception:
        return None


def pending_assignment_state(target, basis, deadline=None):
    """Fresh selected read: registered, pending, known revoked, or unknown.

    A transient/missing operational store never proves revocation. A committed
    native cancellation/basis change does, unless its exact Receipt is already
    registered in this same snapshot. No age or unrelated receipt decides this.
    """
    try:
        pending = validate_pending_review(basis)
        _check_time(deadline)
        if not _same_project(target, pending):
            return "revoked"
        paths = _paths(target)
        with closing(connect_initialized_readonly(target, operation_local=True, deadline=deadline)) as connection:
            if _registered(connection, pending):
                _check_time(deadline)
                return "registered"
            native = _native(connection, target, pending["task_id"], pending["parent_session_id"])
            if native is None or any(native[key] != pending[key] for key in _NATIVE):
                return "revoked"
            current = _assignment(connection, paths, native, pending["reviewer_session_id"], deadline)
            _check_time(deadline)
            # A changed operational slot is not proof that the old source can
            # never register: native continuation may retain that original.
            # Keep the location opaque until an exact Receipt or a decisive
            # native cancellation/basis change is observed.
            return "pending" if current == pending else "unknown"
    except Exception:
        return "unknown"
