"""Bounded lifecycle composition for already admitted answer collections.

No scheduler, worker owner, native message send or second finalizer lives here.
The caller supplies the existing public executor and saved preparation service.
"""

import json
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Callable

from .review_answer import AnswerError
from .review_answer_host import _uuid
from .review_answer_hook import collect_event
from .review_answer_values import basis_digest, validate_binding


def _fail(reason="identity"):
    raise AnswerError("binding_mismatch", reason=reason)


def bind_initial(service, host, reviewer_ids, *, creation_context=False,
                 known_conflict=False, assignments=None, reviewer_keys=None):
    """Admit ordered returned children or reuse exact immutable saved bindings.

    creation_context describes the live dispatching flow, never recovered state.
    A restarted caller can reuse saved bindings but cannot construct missing ones.
    Null launch fields are deliberately unknown; no execution is inferred.
    """
    records = service.repository.read()
    if type(reviewer_ids) not in (tuple, list) or len(reviewer_ids) != len(records):
        _fail()
    children = tuple(_uuid(value) for value in reviewer_ids)
    if len(set(children)) != len(children):
        _fail()
    keys = (tuple(row["binding"]["reviewer_key"] if row["binding"] else child for row, child in zip(records, children))
            if reviewer_keys is None else tuple(reviewer_keys))
    if len(keys) != len(children):
        _fail()
    if assignments is None:
        assignments = tuple(row["binding"]["assignment"] if row["binding"] else
            dict(kind="independent", review_profiles=[], review_lenses=[], requested_model=None,
                 requested_effort=None, requested_skill=None, context_relation=None) for row in records)
    if type(assignments) not in (tuple, list) or len(assignments) != len(records):
        _fail()
    missing = []
    for slot, (record, child, key, assignment) in enumerate(zip(records, children, keys, assignments), 1):
        service.admit_basis(record["basis"], slot)
        binding = record["binding"]
        if binding is None:
            missing.append(slot)
        elif (binding["reviewer_session_id"] != child or binding["reviewer_key"] != key
                or binding["assignment"] != assignment):
            _fail("conflict")
    if not missing:
        return records
    if creation_context is not True or known_conflict is not False:
        _fail()
    observed = host.bind_reviewers(children, creation_context=True, known_conflict=False)
    if type(observed) is not tuple or len(observed) != len(records):
        _fail()
    candidates = []
    for slot, (record, child, key, assignment, fact) in enumerate(zip(records, children, keys, assignments, observed), 1):
        if (type(fact) is not dict or set(fact) != {"dispatch_id", "parent_session_id", "reviewer_session_id", "initial_turn_id", "source"}
                or fact["reviewer_session_id"] != child):
            _fail()
        binding = validate_binding(dict(basis_digest=basis_digest(record["basis"]), slot=slot,
            reviewer_key=key, dispatch_id=fact["dispatch_id"], parent_session_id=fact["parent_session_id"],
            reviewer_session_id=child, initial_turn_id=fact["initial_turn_id"], binding_source=fact["source"], assignment=assignment), record["basis"])
        if record["binding"] is not None and record["binding"] != binding:
            _fail("conflict")
        candidates.append(binding)
    for slot in missing:
        service.bind(slot, candidates[slot - 1])
    return service.repository.read()


def collect_bound(service, host, reviewer_ids=None, *, explicit_recovery=False):
    """Recover each selected saved source once; keep per-slot failures bounded."""
    records = service.repository.read()
    if reviewer_ids is None:
        selected = None
    else:
        if type(reviewer_ids) not in (tuple, list) or not 1 <= len(reviewer_ids) <= 8:
            _fail()
        selected = tuple(_uuid(value) for value in reviewer_ids)
        if len(set(selected)) != len(selected):
            _fail()
        known = {row["binding"]["reviewer_session_id"] for row in records if row["binding"] is not None}
        if not set(selected) <= known:
            _fail()
    collected, unavailable = [], []
    for slot, record in enumerate(records, 1):
        binding = record["binding"]
        if selected is not None and (binding is None or binding["reviewer_session_id"] not in selected):
            continue
        try:
            service.recover(slot, host, explicit_recovery=explicit_recovery)
            collected.append(slot)
        except AnswerError as error:
            unavailable.append(dict(slot=slot, failure=error.failure))
        except Exception:
            unavailable.append(dict(slot=slot, failure={"class": "collection_unavailable", "field": None, "reason": "incomplete"}))
    return dict(collected=collected, unavailable=unavailable)


def collect_hook_event(event, *, service, host):
    """Explicit typed MCP event argument, never fabricated executor metadata."""
    if (type(event) is not dict or set(event) !=
            {"hook_event_name", "session_id", "agent_id", "turn_id", "stop_hook_active"}):
        raise AnswerError("collection_unavailable", reason="shape")
    try:
        raw = json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise AnswerError("collection_unavailable", reason="shape") from None
    return collect_event(raw, service=service, host=host)


@dataclass(frozen=True)
class ReviewerHookAdmission:
    turn_id: str
    check: Callable[[], None]


@contextmanager
def reviewer_hook_admission(service, parent_id, reviewer_id, turn_id, *, admit_current):
    """Limit one genuine reviewer executor to its already assigned active turn.

    The event only locates a candidate. Existing current-basis admission and
    validated saved records provide the independent parent/slot/source binding.
    Reuse each existing admission boundary, including those under the write
    lease, so a correction or changed preparation cannot redirect this caller.
    """
    from .review_wait_runtime.review_wait_host import HostAdapterError
    parent, reviewer = _uuid(parent_id), _uuid(reviewer_id)
    caller_turn = None if turn_id is None else _uuid(turn_id)
    if not callable(admit_current):
        _fail()
    current_admission = service.admit_basis

    def selected():
        records = service.repository.read()
        matches = [(slot, row) for slot, row in enumerate(records, 1)
                   if row["binding"] is not None and row["binding"]["reviewer_session_id"] == reviewer]
        if len(matches) != 1:
            _fail()
        slot, row = matches[0]
        if (row["binding"]["parent_session_id"] != parent
                or row["basis"]["execution_basis"]["parent_session_id"] != parent):
            _fail()
        return slot, row

    slot, initial = selected()
    saved_basis, saved_binding = deepcopy(initial["basis"]), deepcopy(initial["binding"])

    def active_source(row):
        correction = row["correction"]
        if correction is not None and (correction["effect"] != "accepted" or correction["correction_turn_id"] is None):
            raise AnswerError("collection_unavailable", reason="effect_unknown")
        ordinal = 0 if correction is None else 1
        if len(row["attempts"]) <= ordinal:
            _fail()
        return ordinal, _uuid(row["attempts"][ordinal]["turn_id"])

    # A missing hook executor turn does not make the active attempt movable.
    # Pin the independently saved source for this entire call, including checks
    # after helper initialization and under the original-publication lease.
    saved_source = active_source(initial)
    if caller_turn is not None and caller_turn != saved_source[1]:
        raise HostAdapterError("caller_mismatch")

    def check():
        # One admission rechecks the active Task, complete captured preparation,
        # native/issued source and Packet. It closes its native snapshot before
        # operational reads; every invocation is fresh at this effect boundary.
        admit_current(saved_basis, slot)
        current_slot, row = selected()
        if current_slot != slot or row["basis"] != saved_basis or row["binding"] != saved_binding:
            _fail()
        if active_source(row) != saved_source:
            raise HostAdapterError("caller_mismatch")

    def admitted(value, index):
        if index != slot or value != saved_basis:
            _fail()
        check()

    check()
    service.admit_basis = admitted
    try:
        yield ReviewerHookAdmission(turn_id=saved_source[1], check=check)
    finally:
        service.admit_basis = current_admission


def _source_context(repo, skill, binding):
    """Read one indexed preparation and its explicit transition, without scans."""
    from .review_handoff_scope import resolve
    from .review_preparation_repository import PreparationRepository
    from .review_wait_runtime.continuation_transition_repository import TransitionRepository
    from .setup_feature_config import answer_collection_allowed
    from .state_paths import path_lexically_exists
    if not answer_collection_allowed(skill):
        return None
    resolution = resolve(repo, writing=False)
    paths = resolution.paths
    path = paths.review_preparation_store(binding.parent_thread_id, binding.task_id, binding.target_generation)
    if not path_lexically_exists(path):
        return None
    _records, root = PreparationRepository(path).read()
    expected = {**root.basis, "parent_thread_id": root.basis["parent_session_id"]}
    if any(expected.get(key) != value for key, value in asdict(binding).items() if key != "wait_id"):
        _fail()
    current = None
    transition_path = paths.review_continuation_store(binding.parent_thread_id, binding.task_id, binding.target_generation)
    if path_lexically_exists(transition_path):
        rounds = TransitionRepository(transition_path).read()
        if rounds:
            current = rounds[-1]
            if current.preparation_id != root.preparation_id:
                _fail("conflict")
    return resolution, root, current


def _source_service(repo, skill, context, *, initial=False):
    from .review_answer_handoff import service_for
    from .review_answer_collection_repository import CollectionRepository
    from .setup_feature_config import answer_collection_allowed
    resolution, root, transition = context
    if transition is None or initial:
        if root.version != 2:
            return None
        basis = root.collection_bases[0]
    else:
        if transition.phase not in ("prepared", "published"):
            _fail("conflict")
        if transition.answer_protocol == "legacy":
            return None
        value = root.basis
        store = resolution.paths.review_answer_collection_store(value["parent_session_id"], value["task_id"],
            value["target_generation"], root.preparation_id, transition.continuation_id)
        from .review_answer_continuation_preparation import issued_bases
        basis = issued_bases(root, transition, CollectionRepository(store).read())[0]
    return service_for(repo, resolution, root, basis, enabled=lambda: answer_collection_allowed(skill))


def service_for_wait(repo, skill, binding):
    """Read the issued current collection; this alone does not select reviewers."""
    context = _source_context(repo, skill, binding)
    return None if context is None else _source_service(repo, skill, context)


@dataclass(frozen=True)
class SourceGroup:
    service: object
    reviewer_ids: tuple
    source_slots: tuple
    response_slots: tuple


def sources_for_reviewers(repo, skill, binding, reviewer_ids):
    """Resolve exact fresh sources from issuance, selection history and bindings.

    Latest selected requests and never-selected initial slots are the only
    candidates. Retained originals and obsolete rounds are not fallbacks.
    The returned slots keep source identities separate from response positions.
    """
    from .review_continuation_repository import read_continuations
    from .review_wait_basis_repository import read_review_basis
    from .storage import connect_initialized_readonly
    context = _source_context(repo, skill, binding)
    if context is None:
        return None
    resolution, root, transition = context
    if root.version != 2 and (transition is None or transition.answer_protocol == "legacy"):
        return None
    if type(reviewer_ids) not in (tuple, list) or not 1 <= len(reviewer_ids) <= 8:
        _fail()
    children = tuple(_uuid(value) for value in reviewer_ids)
    if len(set(children)) != len(children) or binding.parent_thread_id in children:
        _fail()
    with closing(connect_initialized_readonly(resolution.target)) as connection:
        if read_review_basis(connection, project_id=root.basis["project_id"], task_id=binding.task_id) != root.basis:
            _fail()
        history = read_continuations(connection, project_id=root.basis["project_id"], task_id=binding.task_id)
    relevant = tuple(row for row in history if row.basis["target_generation"] == binding.target_generation)
    group = tuple(row for row in relevant if row.preparation_id == root.preparation_id)
    latest = group[-1] if group else None
    ever_selected = {choice.session_id for row in relevant for choice in row.selected}
    selected = () if latest is None else tuple(choice.session_id for choice in latest.selected)
    services, grouped, order = {}, {}, []
    for position, child in enumerate(children, 1):
        initial = child not in ever_selected
        if not initial and (child not in selected or transition is None
                or transition.continuation_id != latest.continuation_id):
            _fail()
        if initial not in services:
            services[initial] = _source_service(repo, skill, context, initial=initial)
        service = services[initial]
        if service is None:
            # An entirely legacy request remains normal non-applicability.
            # Mixed requests cannot silently omit an unsupported source.
            grouped.setdefault(None, []).append(position)
            continue
        records = service.repository.read()
        matches = [slot for slot, row in enumerate(records, 1)
                   if row["binding"] is not None and row["binding"]["reviewer_session_id"] == child]
        if len(matches) == 1:
            slot = matches[0]
        elif not matches and not relevant and transition is None and len(children) == len(records):
            # Only the complete original dispatch may enter bind_initial.
            # That operation independently proves each new child and turn.
            slot = position
            if records[slot - 1]["binding"] is not None:
                _fail()
        elif not matches and not initial:
            slot = selected.index(child) + 1
            if slot > len(records) or records[slot - 1]["binding"] is not None:
                _fail()
        else:
            _fail()
        service.admit_basis(records[slot - 1]["basis"], slot)
        order.append((1 if initial and relevant else 0, slot))
        grouped.setdefault(initial, []).append((child, slot, position))
    if None in grouped:
        if len(grouped) != 1:
            _fail()
        return None
    if order != sorted(order):
        _fail()
    return tuple(SourceGroup(services[key], tuple(item[0] for item in items),
        tuple(item[1] for item in items), tuple(item[2] for item in items)) for key, items in grouped.items())


def bind_sources(group, host, *, creation_context=False):
    """Reuse exact bound subsets; new binding still requires the full dispatch."""
    records = group.service.repository.read()
    selected = tuple(records[slot - 1] for slot in group.source_slots)
    if any(row["binding"] is None for row in selected):
        if group.source_slots != tuple(range(1, len(records) + 1)):
            _fail()
        creation_context = creation_context and all(row["basis"]["continuation"] is None for row in records)
        return bind_initial(group.service, host, group.reviewer_ids, creation_context=creation_context)
    for child, slot, row in zip(group.reviewer_ids, group.source_slots, selected):
        if row["binding"]["reviewer_session_id"] != child:
            _fail()
        group.service.admit_basis(row["basis"], slot)
    return selected


def collect_sources(group, host, *, explicit_recovery=False):
    result = collect_bound(group.service, host, group.reviewer_ids, explicit_recovery=explicit_recovery)
    positions = dict(zip(group.source_slots, group.response_slots))
    return dict(collected=[positions[slot] for slot in result["collected"]],
        unavailable=[{**item, "slot": positions[item["slot"]]} for item in result["unavailable"]])


def service_for_event(repo, skill, parent_id, child_id):
    """Use the existing current Task context index, never directory discovery."""
    from .review_handoff_scope import resolve
    from .review_wait_basis_repository import read_wait_basis
    from .review_wait_runtime.review_wait_controller import WaitBinding
    from .session_identity import CallerIdentity
    from .storage import connect_initialized_readonly
    from .tasks import list_current_tasks
    resolution = resolve(repo, writing=False)
    with closing(connect_initialized_readonly(resolution.target)) as connection:
        current = list_current_tasks(connection, resolution.target.project, limit=2,
                                     caller=CallerIdentity(parent_id), for_context=True)
        active = [row for row in current.tasks if row["status"] in ("in_progress", "review_pending")]
        if len(active) != 1:
            _fail()
        observed = read_wait_basis(connection, project_id=resolution.target.project.project_id, task_id=active[0]["task_id"])
        binding = WaitBinding(wait_id="answer-hook", **{key: value for key, value in observed.items()
            if key not in ("version", "task_status")})
    if binding.parent_thread_id != parent_id:
        _fail()
    groups = sources_for_reviewers(repo, skill, binding, (child_id,))
    if groups is None or len(groups) != 1:
        _fail()
    service = groups[0].service
    if sum(row["binding"] is not None and row["binding"]["reviewer_session_id"] == child_id
                              for row in service.repository.read()) != 1:
        _fail()
    return service, binding
