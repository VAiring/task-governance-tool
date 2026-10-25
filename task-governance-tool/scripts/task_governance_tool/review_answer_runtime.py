"""Bounded lifecycle composition for already admitted answer collections.

No scheduler, worker owner, native message send or second finalizer lives here.
The caller supplies the existing public executor and saved preparation service.
"""

import hashlib
import json
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Callable

from .review_answer import AnswerError
from .review_answer_host import PublicAnswerHost, _uuid
from .review_answer_hook import collect_event
from .review_answer_values import basis_digest, validate_binding, validate_source


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
        service.check_basis(record["basis"], slot)
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
        common = dict(basis_digest=basis_digest(record["basis"]), slot=slot,
            reviewer_key=key, parent_session_id=fact["parent_session_id"], reviewer_session_id=child,
            assignment=assignment)
        if record["basis"]["version"] == 2:
            from .review_answer_collection import source
            validate_source(fact["source"], "dispatch_binding")
            if fact["source"]["version"] != 1:
                _fail()
            common.update(tracking=dict(kind="initial", boundary_turn_id=fact["initial_turn_id"],
                boundary="inclusive", dispatch_id=fact["dispatch_id"]),
                binding_source={**source("reviewer_tracking"), "version": 2})
        else:
            common.update(dispatch_id=fact["dispatch_id"], initial_turn_id=fact["initial_turn_id"],
                          binding_source=fact["source"])
        binding = validate_binding(common, record["basis"])
        if record["binding"] is not None and record["binding"] != binding:
            _fail("conflict")
        candidates.append(binding)
    for slot in missing:
        service.bind(slot, candidates[slot - 1])
    return service.repository.read()


def collect_bound(service, host, reviewer_ids=None, *, explicit_recovery=False, on_retryable=None):
    """Recover each selected saved source once; keep per-slot failures bounded."""
    from .review_wait_runtime.review_wait_repository import RepositoryError

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
        except AnswerError as error:
            failure = error.failure
            retryable = False
        except Exception as error:
            failure = {"class": "collection_unavailable", "field": None, "reason": "incomplete"}
            retryable = isinstance(error, RepositoryError) and error.code == "writer_busy"
        else:
            collected.append(slot)
            continue
        if retryable and binding is not None and on_retryable is not None:
            on_retryable(binding["reviewer_session_id"])
        unavailable.append(dict(slot=slot, failure=failure))
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
    turn_id: str | None
    check: Callable[[], None]


@contextmanager
def reviewer_hook_admission(service, parent_id, reviewer_id, turn_id, *, admit_current):
    """Limit one genuine reviewer executor to its saved reviewer assignment.

    The event only locates a candidate. Existing current-basis admission and
    validated saved records provide the independent parent/slot/source binding.
    Reuse each existing admission boundary, including those under the write
    lease, so a correction or changed preparation cannot redirect this caller.
    """
    from .review_wait_runtime.review_wait_host import HostAdapterError
    from .state_resolver import DatabaseValidationReuse
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

    # Pin the saved assignment for this call and the exact attempt for v1.
    # Tracking v2 observes the latest answer without attributing it to the
    # caller's hook turn. Recheck after helper initialization and under lease.
    tracking = initial["basis"]["version"] == 2
    saved_source = None if tracking else active_source(initial)
    if not tracking and caller_turn is not None and caller_turn != saved_source[1]:
        raise HostAdapterError("caller_mismatch")

    validation_reuse = DatabaseValidationReuse()

    def check():
        # One admission rechecks the active Task, complete captured preparation,
        # native/issued source and Packet. It closes its native snapshot before
        # operational reads; every invocation is fresh at this effect boundary.
        try:
            admit_current(saved_basis, slot, validation_reuse=validation_reuse)
            current_slot, row = selected()
            if current_slot != slot or row["basis"] != saved_basis or row["binding"] != saved_binding:
                _fail()
            if not tracking and active_source(row) != saved_source:
                raise HostAdapterError("caller_mismatch")
        except BaseException:
            validation_reuse.clear()
            raise

    def admitted(value, index):
        if index != slot or value != saved_basis:
            validation_reuse.clear()
            _fail()
        check()

    try:
        service.admit_basis = admitted
        service.check_basis(saved_basis, slot)
        yield ReviewerHookAdmission(turn_id=None if tracking else saved_source[1],
                                    check=lambda: service.check_basis(saved_basis, slot))
    finally:
        validation_reuse.clear()
        service.admit_basis = current_admission


def _source_context(repo, skill, binding, *, observe_policy=None):
    """Read one indexed preparation and its explicit transition, without scans."""
    from .review_handoff_scope import resolve
    from .review_preparation_repository import PreparationRepository
    from .review_wait_runtime.continuation_transition_repository import TransitionRepository
    from .setup_feature_config import answer_collection_allowed
    from .state_paths import path_lexically_exists
    enabled = answer_collection_allowed(skill)
    if observe_policy is not None:
        observe_policy(enabled)
    if not enabled:
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


def _source_service(repo, skill, context, *, initial=False, observe_policy=None):
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
    def enabled():
        allowed = answer_collection_allowed(skill)
        if observe_policy is not None:
            observe_policy(allowed)
        return allowed
    return service_for(repo, resolution, root, basis, enabled=enabled)


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


def sources_for_reviewers(repo, skill, binding, reviewer_ids, *, observe_policy=None):
    """Resolve exact fresh sources from issuance, selection history and bindings.

    Latest selected requests and never-selected initial slots are the only
    candidates. Retained originals and obsolete rounds are not fallbacks.
    The returned slots keep source identities separate from response positions.
    """
    from .review_continuation_repository import read_continuations
    from .review_wait_basis_repository import read_review_basis
    from .storage import connect_initialized_readonly
    context = _source_context(repo, skill, binding, observe_policy=observe_policy)
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
    with closing(connect_initialized_readonly(resolution.target, operation_local=True, review_task_id=binding.task_id)) as connection:
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
            services[initial] = _source_service(repo, skill, context, initial=initial,
                                                observe_policy=observe_policy)
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
        service.check_basis(records[slot - 1]["basis"], slot)
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
        group.service.check_basis(row["basis"], slot)
    return selected


def arm_sources(group, host, *, repo, skill, binding):
    """Arm only a newly issued tracking continuation before its follow-up send.

    Dedication is native initial proof or an explicit saved legacy declaration.
    Repeating arm reuses the immutable baseline without another host read.
    """
    context = _source_context(repo, skill, binding)
    if context is None:
        _fail()
    resolution, root, transition = context
    migrated = None
    if getattr(root, "version", 2) == 1:
        from .review_answer_dedication import current, assignment
        from .review_continuation_handoff_repository import read_state
        from .review_wait_runtime.continuation_transition_repository import TransitionRepository
        from .session_identity import CallerIdentity
        history = TransitionRepository(resolution.paths.review_continuation_store(
            binding.parent_thread_id, binding.task_id, binding.target_generation)).read()
        state = read_state(resolution.target, binding.task_id, CallerIdentity(binding.parent_thread_id))
        if transition is None or not history or history[-1] != transition or state.basis != root.basis:
            _fail()
        migrated = {row.session_id: row for row in current(root, history, state, transition)}
        originals = ()
    else:
        initial = _source_service(repo, skill, context, initial=True)
        if initial is None:
            _fail()
        originals = initial.repository.read()
    records = group.service.repository.read()
    candidates = []
    for child, slot in zip(group.reviewer_ids, group.source_slots):
        row = records[slot - 1]
        group.service.check_basis(row["basis"], slot)
        if row["basis"]["version"] != 2 or row["basis"]["continuation"] is None:
            _fail()
        if migrated is not None:
            dedication = migrated.get(child)
            if dedication is None:
                _fail()
            previous = dict(reviewer_key=dedication.reviewer_key, assignment=assignment())
        else:
            prior = [(original_slot, item) for original_slot, item in enumerate(originals, 1)
                     if item["binding"] is not None and item["binding"]["reviewer_session_id"] == child]
            if len(prior) != 1:
                _fail()
            original_slot, original = prior[0]
            # Recheck independent issuance, even when this round is already bound.
            if (original_slot > len(root.collection_bases)
                    or original["basis"] != root.collection_bases[original_slot - 1]):
                _fail()
            previous = original["binding"]
            if (original["basis"]["execution_basis"] != row["basis"]["execution_basis"]
                    or previous["parent_session_id"] != binding.parent_thread_id
                    or original["basis"]["continuation"] is not None):
                _fail()
            if original["basis"]["version"] == 2 and previous["tracking"]["kind"] != "initial":
                _fail()
        if row["binding"] is not None:
            if (row["binding"]["reviewer_session_id"] != child
                or row["binding"]["reviewer_key"] != previous["reviewer_key"]
                or row["binding"]["assignment"] != previous["assignment"]):
                _fail()
            candidates.append((slot, row["binding"]))
            continue
        observed = host.observe_tracking_baseline(child)
        if (type(observed) is not dict or set(observed) != {"turn_id", "terminal", "binding_source"}
                or observed["terminal"] not in ("completed", "failed", "interrupted")):
            _fail()
        _uuid(observed["turn_id"])
        validate_source(observed["binding_source"], "reviewer_tracking")
        if observed["binding_source"]["version"] != 2:
            _fail()
        candidate = dict(basis_digest=basis_digest(row["basis"]), slot=slot,
            reviewer_key=previous["reviewer_key"], parent_session_id=binding.parent_thread_id,
            reviewer_session_id=child, assignment=deepcopy(previous["assignment"]),
            tracking=dict(kind="continuation", boundary_turn_id=observed["turn_id"],
                boundary="exclusive", dispatch_id=None), binding_source=observed["binding_source"])
        candidates.append((slot, validate_binding(candidate, row["basis"])))
    for slot, candidate in candidates:
        group.service.bind(slot, candidate)
    return tuple(group.service.repository.read()[slot - 1] for slot in group.source_slots)


def bound_turn(record):
    """Occurrence identity; a tracking boundary is not an accepted answer turn."""
    if record["basis"]["version"] == 2:
        return record["binding"]["tracking"]["boundary_turn_id"]
    return record["attempts"][-1]["turn_id"]


def _tracking_projection(row, child):
    if row["basis"]["version"] != 2:
        return None
    if row["tracking_state"] == "invalidated":
        _fail()
    binding = row["binding"]
    if binding is None:
        if row["basis"]["continuation"] is not None:
            _fail()
        return None
    if binding["reviewer_session_id"] != child:
        _fail()
    attempt = row["attempts"][0]
    return dict(boundary_turn_id=bound_turn(row),
        frozen=row["tracking_state"] == "frozen",
        source_identity=deepcopy(attempt["source_identity"]),
        terminal=attempt["terminal"])


def tracking_for_reviewers(repo, skill, binding, reviewer_ids):
    groups = sources_for_reviewers(repo, skill, binding, reviewer_ids)
    result = {}
    for group in groups or ():
        records = group.service.repository.read()
        for child, slot in zip(group.reviewer_ids, group.source_slots):
            row = records[slot - 1]
            if row["basis"]["version"] != 2:
                continue
            group.service.check_basis(row["basis"], slot)
            projection = _tracking_projection(row, child)
            if projection is not None:
                result[child] = projection
    return result


class TrackingReadmissionRequired(Exception):
    """A new slot binding or re-enabled collection needs fresh source admission."""


class _TrackingCollectionHost(PublicAnswerHost):
    """Retain only the candidate identity actually read by the fresh collector."""

    def __init__(self, host, parent_session_id, parent_turn_id, observer):
        super().__init__(host, parent_session_id, parent_turn_id)
        self._observer = observer

    def observe_latest_answer(self, **arguments):
        if not self._observer.collection_allowed():
            raise AnswerError("collection_unavailable", reason="incomplete")
        return self._observer._observe_candidate(super().observe_latest_answer, arguments)


class _TrackingObserver:
    """Bounded, disposable progress hints; never source admission or evidence."""

    def __init__(self, admitted, observed, collection_policy, collection_enabled):
        self._admitted = admitted
        self._observed = observed
        self._sources = {child: (slot, binding)
            for _repository, _count, selected in admitted
            for child, slot, _basis, binding in selected}
        self._candidate_keys = {}
        self._retry_requested = {}
        self._collection_policy = collection_policy
        self._collection_disabled = not collection_enabled

    def collection_disabled(self):
        """Discard only unadopted comparison hints, never saved source state."""
        self._collection_disabled = True
        self._candidate_keys.clear()
        self._retry_requested.clear()

    def observe_collection_policy(self, enabled):
        """Retain a denial seen by fresh admission even if policy changes again."""
        if not enabled:
            self.collection_disabled()

    def collection_allowed(self):
        """Fresh body-read policy; renewed collection requires source admission."""
        if not self._collection_policy():
            self.collection_disabled()
            return False
        if self._collection_disabled:
            raise TrackingReadmissionRequired()
        return True

    def __call__(self):
        if self._collection_disabled:
            self.collection_allowed()
        result, collectable = {}, set()
        try:
            for repository, count, selected in self._admitted:
                records = repository.read_observation()
                if len(records) != count:
                    _fail()
                for child, slot, expected_basis, expected_binding in selected:
                    row = records[slot - 1]
                    if row["basis"] != expected_basis:
                        _fail()
                    projection = _tracking_projection(row, child)
                    if row["binding"] != expected_binding:
                        if expected_binding is None and row["binding"] is not None:
                            raise TrackingReadmissionRequired()
                        _fail()
                    if projection is not None:
                        result[child] = projection
                        attempt = row["attempts"][0]
                        if (row["tracking_state"] == "watching"
                                and row["registration"]["state"] == "not_started"
                                and attempt["publication"] is None):
                            collectable.add(child)
        except (AnswerError, TrackingReadmissionRequired):
            self._candidate_keys.clear()
            self._retry_requested.clear()
            self._observed = {}
            raise
        self._observed = result
        for child, candidate in tuple(self._retry_requested.items()):
            if child in collectable and self._candidate_keys.get(child) == candidate:
                self._forget_candidate(child)
            self._retry_requested.pop(child, None)
        return deepcopy(result)

    def _forget_candidate(self, reviewer_id):
        self._candidate_keys.pop(reviewer_id, None)
        self._retry_requested.pop(reviewer_id, None)

    def retry_unprocessed(self, reviewer_id):
        """Remember a typed pre-lease busy until the next fresh observation.

        collect_bound calls this only for native writer_busy, never inferred
        from a generic result. No retry is authorized until __call__
        rechecks watching, publication absence and not-started registration.
        A busy observation leaves this bounded request for the next poll.
        """
        candidate = self._candidate_keys.get(reviewer_id)
        if candidate is not None and candidate[0] == "complete":
            self._retry_requested[reviewer_id] = candidate

    def make_host(self, host, parent_session_id, parent_turn_id=None):
        return _TrackingCollectionHost(host, parent_session_id, parent_turn_id, self)

    def _candidate_arguments(self, reviewer_id):
        slot, binding = self._sources[reviewer_id]
        tracking = binding["tracking"]
        return dict(basis_digest=binding["basis_digest"], slot=slot,
            reviewer_session_id=reviewer_id, boundary_turn_id=tracking["boundary_turn_id"],
            boundary=tracking["boundary"])

    def _observe_candidate(self, read, arguments):
        """Use the same bounded key for the probe and the actual collection read."""
        from .review_answer_collection import AnswerTurnInProgress

        reviewer_id = arguments["reviewer_session_id"]
        if reviewer_id not in self._sources or arguments != self._candidate_arguments(reviewer_id):
            _fail()
        try:
            observed = read(**arguments)
        except AnswerTurnInProgress as pending:
            self._candidate_keys[reviewer_id] = ("pending", pending.turn_id)
            raise
        except AnswerError as error:
            failure = error.failure
            self._candidate_keys[reviewer_id] = (
                "unavailable", failure["class"], failure["field"], failure["reason"])
            raise
        except Exception:
            self._candidate_keys[reviewer_id] = (
                "unavailable", "collection_unavailable", None, "transport_unavailable")
            raise
        identity = observed["source_identity"]
        self._candidate_keys[reviewer_id] = (
            "complete", identity["reviewer_session_id"], identity["turn_id"], identity["message_id"],
            hashlib.sha256(observed["answer_bytes"]).digest(), observed["terminal"])
        return observed

    def candidate_changed(self, host, reviewer_id):
        """Probe only host completeness/identity; fresh collection reads again.

        The last __call__ snapshot is only a hint. This method opens no core
        connection or lease and parses no review JSON. A bounded opaque digest
        distinguishes changed bytes under the same public message ID. Neither
        bytes nor digest is logged, persisted, published or used as evidence.
        """
        from .review_answer_collection import AnswerTurnInProgress

        projection = self._observed.get(reviewer_id)
        if projection is None:
            return False
        if projection["frozen"]:
            self._forget_candidate(reviewer_id)
            return False
        if not self.collection_allowed():
            return None  # No candidate observation or handled-progress claim.
        previous = self._candidate_keys.get(reviewer_id)
        try:
            self._observe_candidate(host.observe_latest_answer, self._candidate_arguments(reviewer_id))
        except AnswerTurnInProgress:
            return False
        except Exception:
            pass
        return self._candidate_keys.get(reviewer_id) != previous


def tracking_observer_for_reviewers(repo, skill, binding, reviewer_ids):
    """Admit bounded source locations once, then observe only their records.

    Returned facts are progress hints, never evidence or effect authority.
    Callers keep their occurrence fence and use tracking_for_reviewers again
    at effect boundaries. A newly bound initial slot cannot inherit admission
    from its unbound predecessor; the caller must recreate this observer.
    """
    from .setup_feature_config import answer_collection_allowed
    collection_policy = lambda: answer_collection_allowed(skill)
    policy_observation = []
    groups = sources_for_reviewers(repo, skill, binding, reviewer_ids,
                                  observe_policy=policy_observation.append)
    collection_enabled = policy_observation[-1]
    admitted, observed = [], {}
    for group in groups or ():
        records = group.service.repository.read()
        selected = []
        for child, slot in zip(group.reviewer_ids, group.source_slots):
            row = records[slot - 1]
            if row["basis"]["version"] != 2:
                continue
            group.service.check_basis(row["basis"], slot)
            projection = _tracking_projection(row, child)
            if projection is not None:
                observed[child] = projection
            selected.append((child, slot, deepcopy(row["basis"]), deepcopy(row["binding"])))
        if selected:
            admitted.append((group.service.repository, len(records), tuple(selected)))

    return _TrackingObserver(tuple(admitted), observed, collection_policy, collection_enabled)


def collect_sources(group, host, *, explicit_recovery=False, on_retryable=None):
    result = collect_bound(group.service, host, group.reviewer_ids,
        explicit_recovery=explicit_recovery, on_retryable=on_retryable)
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
    with closing(connect_initialized_readonly(resolution.target, operation_local=True)) as connection:
        current = list_current_tasks(connection, resolution.target.project, limit=2,
                                     caller=CallerIdentity(parent_id), for_context=True)
        active = [row for row in current.tasks if row["status"] in ("in_progress", "review_pending")]
        if len(active) != 1:
            _fail()
        from .storage import current_schema_version, validate_task_review_storage
        validate_task_review_storage(connection, project_id=resolution.target.project.project_id,
            task_id=active[0]["task_id"], source_schema_version=current_schema_version(connection))
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
