"""Bridge immutable preparation locators to collected originals and admission.

Only retained, resolver-derived stores and prepared paths are read. Collection
leases span the public writer; all operational/file I/O finishes beforehand.
No new core writer, host lookup, directory discovery or automatic retry exists.
"""

from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from . import review_handoff_transport as files
from .review_answer import AnswerError, decode_answer
from .review_answer_collection import CollectionService, CollectionInvalidated
from .review_answer_collection_repository import CollectionRepository
from .review_answer_transport import (CollectedItem, CollectedSubmission, LegacyItem, SettledRegistrationRecovery, frame_collected_submission,
    answer_input_error)
from .review_answer_values import basis_digest
from .review_handoff_scope import resolve
from .review_preparation_repository import PreparationRepository
from .review_wait_basis_repository import read_review_basis
from .storage import connect_initialized_readonly
from .state_paths import path_lexically_exists


def _bad():
    return AnswerError("binding_mismatch", reason="identity")


def _admit_fresh_source(paths, root, collection_basis, slot, repository, history, packet_path, result_paths):
    """Recheck issuance after host/file I/O without acquiring earlier leases.

    A retained original is read through a separate admission mode. A still
    unselected initial reviewer alone may finish after a continuation starts.
    """
    from .review_continuation import selection_digest, basis_digest as execution_basis_digest
    from .review_wait_runtime.continuation_transition_repository import TransitionRepository
    basis, continuation = collection_basis["execution_basis"], collection_basis["continuation"]
    group = tuple(row for row in history if row.preparation_id == root.preparation_id)
    path = paths.review_continuation_store(basis["parent_session_id"], basis["task_id"], basis["target_generation"])
    rounds = TransitionRepository(path).read() if path_lexically_exists(path) else ()
    latest = rounds[-1] if rounds else None
    binding = repository.read()[slot - 1]["binding"]
    if continuation is None:
        relevant = tuple(row for row in history if row.basis["target_generation"] == basis["target_generation"])
        if relevant and (binding is None or any(binding["reviewer_session_id"] == choice.session_id
                for row in relevant for choice in row.selected)):
            raise _bad()
        # A fence can precede native admission. Its digest alone cannot prove
        # that an initial reviewer is unselected, so wait for the native row.
        if latest is not None and (not group or latest.preparation_id != root.preparation_id
                or latest.continuation_id != group[-1].continuation_id
                or group[-1].basis != basis or latest.basis_digest != execution_basis_digest(basis)
                or latest.selection_digest != selection_digest(group[-1].selected)):
            raise _bad()
        return
    if (not group or group[-1].continuation_id != continuation["continuation_id"]
            or group[-1].sequence != continuation["sequence"]
            or group[-1].state not in ("prepared", "registered")
            or group[-1].basis != basis or group[-1].route != root.route
            or selection_digest(group[-1].selected) != continuation["selection_digest"]
            or latest is None or latest.preparation_id != root.preparation_id
            or latest.continuation_id != continuation["continuation_id"]
            or latest.continuation_sequence != continuation["sequence"]
            or latest.basis_digest != execution_basis_digest(basis)
            or latest.selection_digest != continuation["selection_digest"]
            or latest.phase not in ("prepared", "published")
            or latest.answer_protocol != "review-answer-v1"
            or latest.packet_path != packet_path or latest.result_paths != result_paths
            or len(latest.collection_basis_digests) < slot
            or latest.collection_basis_digests[slot - 1] != basis_digest(collection_basis)
            or "sha256:" + latest.packet_digest != collection_basis["packet_digest"]
            or len(group[-1].selected) < slot
            or binding is not None and binding["reviewer_session_id"] != group[-1].selected[slot - 1].session_id):
        raise _bad()


@dataclass(frozen=True)
class _CollectionAdmission:
    repo: Path
    resolution: object
    root: object
    repository: CollectionRepository
    issued_digests: tuple
    packet_path: str
    packet_digest: str
    result_paths: tuple
    current: bool
    retained: bool
    enabled: object

    def _issued(self, value, slot):
        if self.enabled is not None and not self.enabled():
            raise AnswerError("collection_unavailable", reason="transport_unavailable")
        if (type(slot) is not int or not 1 <= slot <= len(self.issued_digests)
                or basis_digest(value) != self.issued_digests[slot - 1]):
            raise _bad()

    def _preparation(self, paths):
        basis = self.root.basis
        _records, current_root = PreparationRepository(paths.review_preparation_store(
            basis["parent_session_id"], basis["task_id"], basis["target_generation"])).read()
        # Keep the complete captured record, including physical destinations.
        if current_root != self.root:
            raise _bad()

    def _source(self, paths, value, slot, history):
        _admit_fresh_source(paths, self.root, value, slot, self.repository, history,
            self.packet_path, self.result_paths)

    def _packet(self):
        if sha256(files.read_bounded(files.checked_path(self.repo, self.packet_path), files.PACKET_LIMIT)).hexdigest() != self.packet_digest:
            raise _bad()

    def _material(self, value):
        """Reobserve only the governed HEAD/index target, outside core SQLite."""
        execution = value["execution_basis"]
        if not self.current or value["version"] != 2 or execution["target_kind"] != "git_snapshot":
            return
        from .git_snapshot import capture_git_snapshot
        try:
            snapshot = capture_git_snapshot(self.repo)
        except Exception:
            raise AnswerError("collection_unavailable", reason="transport_unavailable") from None
        if (snapshot.base_revision != execution["target_base_revision"]
                or snapshot.fingerprint != execution["target_value"]):
            raise CollectionInvalidated()

    def __call__(self, value, slot):
        self._issued(value, slot)
        if self.current:
            self._preparation(self.resolution.paths)
            with closing(connect_initialized_readonly(self.resolution.target, operation_local=True, review_task_id=self.root.basis["task_id"])) as connection:
                if read_review_basis(connection, project_id=self.root.basis["project_id"],
                        task_id=self.root.basis["task_id"]) != self.root.basis:
                    raise _bad()
                if not self.retained:
                    from .review_continuation_repository import read_continuations
                    history = read_continuations(connection, project_id=self.root.basis["project_id"],
                        task_id=self.root.basis["task_id"])
            if not self.retained:
                self._source(self.resolution.paths, value, slot, history)
        self._packet()
        self._material(value)

    def check_hook(self, value, slot, *, parent_id, validation_reuse=None):
        """One fresh native observation per hook boundary, never an effect token.

        The resolver admits a fresh query-only structural snapshot. Target rows stay
        fresh, and the snapshot closes before any operational or Packet read.
        No connection or observed native values survive this invocation.
        """
        from .review_continuation_repository import read_continuations
        from .session_identity import CallerIdentity
        from .tasks import list_current_tasks
        self._issued(value, slot)
        if not self.current or self.retained or self.root.basis["parent_session_id"] != parent_id:
            raise _bad()
        resolution = resolve(self.repo, writing=False, retain_read_connection=True,
            **({"validation_reuse": validation_reuse} if validation_reuse is not None else {}))
        with closing(resolution.read_connection) as connection:
            current = list_current_tasks(connection, resolution.target.project, limit=2,
                caller=CallerIdentity(parent_id), for_context=True)
            active = [row for row in current.tasks if row["status"] in ("in_progress", "review_pending")]
            if len(active) != 1 or active[0]["task_id"] != self.root.basis["task_id"]:
                raise _bad()
            from .storage import current_schema_version, validate_task_review_storage
            validate_task_review_storage(connection, project_id=resolution.target.project.project_id,
                task_id=active[0]["task_id"], source_schema_version=current_schema_version(connection))
            if read_review_basis(connection, project_id=resolution.target.project.project_id,
                    task_id=active[0]["task_id"]) != self.root.basis:
                raise _bad()
            history = read_continuations(connection, project_id=self.root.basis["project_id"],
                task_id=self.root.basis["task_id"])
        # The fresh canonical resolver must still name this exact collection.
        basis, continuation = value["execution_basis"], value["continuation"]
        path = resolution.paths.review_answer_collection_store(basis["parent_session_id"], basis["task_id"],
            basis["target_generation"], self.root.preparation_id,
            continuation["continuation_id"] if continuation else None)
        if path != self.repository.path:
            raise _bad()
        self._preparation(resolution.paths)
        self._source(resolution.paths, value, slot, history)
        self._packet()
        self._material(value)


def service_for(repo, resolution, root, collection_basis, *, current=True, retained=False, enabled=None):
    """Admit fresh effects by default; retained=True is saved-source reading.

    Retained callers separately prove capture/consumer inventory and may not
    use that mode for new answer collection. current=False is historical only.
    """
    repo = Path(repo)
    basis = collection_basis["execution_basis"]
    continuation = collection_basis["continuation"]
    packet_path, packet_digest, result_paths = root.packet_path, root.packet_digest, root.result_paths
    issued_bases = root.collection_bases
    issued_digests = tuple(basis_digest(value) for value in issued_bases)
    if continuation is not None:
        from .review_wait_runtime.continuation_transition_repository import TransitionRepository
        rounds = TransitionRepository(resolution.paths.review_continuation_store(
            basis["parent_session_id"], basis["task_id"], basis["target_generation"])).read()
        matches = [row for row in rounds if row.continuation_id == continuation["continuation_id"]
            and row.preparation_id == root.preparation_id and row.continuation_sequence == continuation["sequence"]
            and row.phase in ("prepared", "published")]
        if len(matches) != 1:
            raise _bad()
        issued = matches[0]
        if issued.answer_protocol != "review-answer-v1" or not issued.collection_basis_digests:
            raise _bad()
        packet_path, packet_digest, result_paths = issued.packet_path, issued.packet_digest, issued.result_paths
        issued_digests = issued.collection_basis_digests
    if (collection_basis["preparation_id"] != root.preparation_id or collection_basis["execution_basis"] != root.basis
        or collection_basis["packet_digest"] != "sha256:" + packet_digest or collection_basis["route"] != root.route):
        raise _bad()
    path = resolution.paths.review_answer_collection_store(basis["parent_session_id"], basis["task_id"],
        basis["target_generation"], root.preparation_id,
        continuation["continuation_id"] if continuation else None)
    repository = CollectionRepository(path)
    admit = _CollectionAdmission(repo, resolution, root, repository, issued_digests,
        packet_path, packet_digest, result_paths, current, retained, enabled)
    return CollectionService(repo, repository, original_paths=result_paths, admit_basis=admit)


def root_for(repo, collection_basis):
    resolution = resolve(repo, writing=False)
    basis = collection_basis["execution_basis"]
    if resolution.target.project.project_id != basis["project_id"]:
        raise _bad()
    records, current = PreparationRepository(resolution.paths.review_preparation_store(
        basis["parent_session_id"], basis["task_id"], basis["target_generation"])).read()
    matches = [row for row in records if row.preparation_id == collection_basis["preparation_id"]]
    if len(matches) != 1 or current is None or matches[0].preparation_id != current.preparation_id:
        raise _bad()
    return resolution, matches[0]


def initial_submission(repo, root, resolution, originals):
    if root.version != 2 or tuple(originals) != root.result_paths:
        raise _bad()
    service = service_for(repo, resolution, root, root.collection_bases[0])
    try:
        with service.admission() as (_lease, sources):
            items = tuple(CollectedItem(slot, raw, metadata, decode_answer(raw))
                for slot, (raw, metadata) in enumerate(sources, 1))
            # Read each exact prepared original a second time immediately before
            # framing; a saved acknowledgement never replaces this confirmation.
            for item in items:
                if files.read_bounded(files.checked_path(repo, item.metadata["original"]["path"]), 262144) != item.raw:
                    raise _bad()
            return root.basis["task_id"], frame_collected_submission(root.preparation_id, items)
    except AnswerError as error:
        raise answer_input_error(error) from None


class RegistrationAdmission:
    def __init__(self, submission, groups, replacements=None):
        self.submission, self.groups = submission, groups
        self.replacements = replacements or {}

    def _not_dispatched(self, *, allow_replacement=False):
        for service, lease, items in self.groups:
            records = service.repository.read()
            for item in items:
                if records[item.metadata["binding"]["slot"]-1]["registration"] != dict(
                        state="not_started", submission_digest=None, receipt_id=None):
                    if allow_replacement and item.slot in self.replacements:
                        continue
                    return False
        return True

    def prove_not_dispatched(self):
        """Positive full-source evidence while every collection lease is held.

        This settles only an outer finalizer reservation, never a dispatched
        source effect. Legacy/mixed inventories have no such complete proof.
        The normal writer must reacquire admission and recheck before dispatch.
        """
        items = tuple(sorted((item for _service, _lease, group in self.groups
                              for item in group), key=lambda item: item.slot))
        return (bool(items) and all(type(item) is CollectedItem for item in self.submission.items)
                and items == self.submission.items and self._not_dispatched())

    def registration_states(self):
        """Complete read-only projection under the already held source leases.

        This is a diagnostic snapshot, not a dispatch or recovery permit. Legacy
        items have no collection registration state; never invent one for them.
        """
        from .review_answer_values import digest, identifier
        items = self.submission.items
        collected = tuple(item for item in items if type(item) is CollectedItem)
        admitted = tuple(sorted((item for _service, _lease, group in self.groups
                                 for item in group), key=lambda item: item.slot))
        if (not 1 <= len(items) <= 8 or admitted != collected
                or tuple(item.slot for item in items) != tuple(range(1, len(items)+1))
                or any(type(item) not in (CollectedItem, LegacyItem) for item in items)):
            raise _bad()
        states = {}
        for service, _lease, group in self.groups:
            records = service.repository.read()
            for item in group:
                source_slot = item.metadata["binding"]["slot"]
                value = records[source_slot-1]["registration"]
                if (type(value) is not dict or set(value) != {"state", "submission_digest", "receipt_id"}
                        or value["state"] not in ("not_started", "dispatched", "unknown", "registered")
                        or (value["state"] == "not_started") != (value["submission_digest"] is None)
                        or (value["state"] == "registered") != (value["receipt_id"] is not None)):
                    raise _bad()
                if value["submission_digest"] is not None:
                    digest(value["submission_digest"])
                if value["receipt_id"] is not None:
                    identifier(value["receipt_id"], "tg_review_receipt_")
                states[item.slot] = dict(slot=item.slot, protocol="review-answer-v1", source_slot=source_slot,
                    state=value["state"], receipt_id=value["receipt_id"], submission_digest_match=(
                        "absent" if value["submission_digest"] is None else "matching"
                        if value["submission_digest"] == self.submission.submission_digest else "different"))
        return [states[item.slot] if type(item) is CollectedItem else dict(slot=item.slot,
            protocol="legacy-review-result-v1", source_slot=None, state=None,
            submission_digest_match=None, receipt_id=None) for item in items]

    def require_settled_recovery(self, request, finalization_basis):
        if type(request) is not SettledRegistrationRecovery:
            raise AnswerError("registration_unknown", reason="effect_unknown")
        request.require_submission(self.submission, finalization_basis)
        if any(row["protocol"] != "review-answer-v1" or row["state"] not in ("dispatched", "unknown")
                or row["submission_digest_match"] != "matching" or row["receipt_id"] is not None
                for row in self.registration_states()):
            raise AnswerError("registration_unknown", reason="effect_unknown")

    def dispatch(self):
        # Check every source store before persisting any registration intent.
        if not self._not_dispatched(allow_replacement=True):
            raise AnswerError("registration_unknown", reason="effect_unknown")
        for service, lease, items in self.groups:
            service.mark_registration_locked(lease,
                tuple(item.metadata["binding"]["slot"] for item in items), self.submission.submission_digest,
                **({"replacements": {item.metadata["binding"]["slot"]: self.replacements[item.slot]
                    for item in items if item.slot in self.replacements}} if self.replacements else {}))

    def confirm(self, results):
        receipts = results["receipts"]
        if len(receipts) != len(self.submission.items):
            raise _bad()
        saved = {item.slot: result["receipt"]["review_receipt_id"] for item, result in zip(self.submission.items, receipts)}
        for service, lease, items in self.groups:
            for item in items:
                service.confirm_registration_locked(lease, item.metadata["binding"]["slot"],
                    self.submission.submission_digest, saved[item.slot])


def _admit_retained_items(resolution, submission, items):
    """Require the exact native capture, not merely a caller's old context."""
    from .review_answer_repository import read_native_collected_captures
    if submission.continuation is None or not items:
        raise _bad()
    task_id = items[0].metadata["basis"]["execution_basis"]["task_id"]
    with closing(connect_initialized_readonly(resolution.target, operation_local=True, review_task_id=task_id)) as connection:
        captured = read_native_collected_captures(connection, submission.continuation["continuation_id"])
    for item in items:
        matches = [(capture, metadata) for capture, metadata in captured
            if capture["consumer_slot"] == item.slot and metadata == item.metadata]
        if len(matches) != 1 or matches[0][0] not in submission.retained_originals:
            raise _bad()


def _admit_initial_legacy(repo, resolution, root, item):
    """Confirm an initial legacy sidecar at its original issued destination."""
    from .review_handoff_originals import read_original
    if item.bound.continuation is not None or item.source_packet_digest != "sha256:" + root.packet_digest:
        raise _bad()
    matches = []
    for path in root.result_paths:
        physical = files.relative_path(repo, path)
        if path_lexically_exists(physical):
            source = read_original(repo, root.packet_path, path, preparation=(resolution, root))
            if source.bound.binding.session_id == item.session_binding.session_id:
                matches.append(source)
    if (len(matches) != 1 or matches[0].collected_metadata is not None
        or matches[0].raw != item.raw or matches[0].metadata != item.metadata):
        raise _bad()


def _admit_initial_inventory(resolution, root, submission):
    """Complete batch coverage and initial-source roles, before dispatch."""
    from .review_continuation_repository import read_continuations, read_judgments
    from .review_continuation import select_effective, quality_identity, selection_digest
    with closing(connect_initialized_readonly(resolution.target, operation_local=True,
            review_task_id=root.basis["task_id"])) as connection:
        history = read_continuations(connection, project_id=root.basis["project_id"],
                                     task_id=root.basis["task_id"])
        current = tuple(row for row in history if row.preparation_id == root.preparation_id)
        identity = submission.continuation
        if (identity is None or not current or current[-1].state not in ("prepared", "registered")
            or current[-1].basis != root.basis
            or identity != dict(continuation_id=current[-1].continuation_id,
                sequence=current[-1].sequence, selection_digest=selection_digest(current[-1].selected))):
            raise _bad()
        nodes = read_judgments(connection, basis=root.basis, _validated_continuations=history)
        heads = select_effective(nodes, quality_basis=quality_identity(root.basis)).heads
        known = {node.receipt_id: node.session_id for node in heads}
        retained = tuple(known.get(identifier) for identifier in submission.retained_receipt_ids)
        actors = tuple(item.session_binding.session_id for item in submission.items) + retained
        if (None in retained or len(actors) != len(root.result_paths)
            or len(set(actors)) != len(actors)):
            raise _bad()
        ever_selected = {choice.session_id for record in history
            if record.basis["target_generation"] == root.basis["target_generation"] for choice in record.selected}
        registered = {node.session_id for node in nodes}
        if any(use.session_id in ever_selected for use in submission.source_uses if use.kind == "initial"):
            raise _bad()
        if current[-1].state == "registered":
            from .review_answer_repository import read_continuation_dispatch
            committed = read_continuation_dispatch(connection, current[-1])
            if (committed is None or committed["submission_digest"] != submission.submission_digest
                or tuple(entry["reviewer_session_id"] for entry in committed["entries"]) !=
                   tuple(item.session_binding.session_id for item in submission.items)):
                raise _bad()
        elif any(use.session_id in registered for use in submission.source_uses if use.kind == "initial"):
            raise _bad()


@contextmanager
def registration_admission(repo, submission, caller):
    if type(submission) is not CollectedSubmission:
        raise _bad()
    repo = Path(repo)
    grouped = {}
    try:
        for item in submission.items:
            if type(item) is not CollectedItem:
                continue  # Immutable legacy captures are checked by the core.
            basis = item.metadata["basis"]
            if basis["execution_basis"]["parent_session_id"] != caller.require():
                raise _bad()
            continuation = basis["continuation"]
            key = continuation["continuation_id"] if continuation else ""
            grouped.setdefault(key, []).append(item)
        with ExitStack() as stack:
            admitted = []
            root_context = None
            for key in sorted(grouped):
                items = sorted(grouped[key], key=lambda item: item.metadata["binding"]["slot"])
                resolution, root = root_for(repo, items[0].metadata["basis"])
                root_context = resolution, root
                if submission.continuation is None and (
                    root.version != 2 or tuple(item.slot for item in items) != tuple(range(1, len(root.result_paths)+1))):
                    raise _bad()
                retained = items[0].metadata["basis"]["continuation"] != submission.continuation
                if retained:
                    captured = [item for item in items if submission.use_for(item.slot).kind == "captured"]
                    if captured:
                        _admit_retained_items(resolution, submission, captured)
                service = service_for(repo, resolution, root, items[0].metadata["basis"], retained=retained)
                slots = tuple(item.metadata["binding"]["slot"] for item in items)
                lease, sources = stack.enter_context(service.admission(slots))
                if any(item.raw != raw or item.metadata != metadata for item, (raw, metadata) in zip(items, sources)):
                    raise _bad()
                for item in items:
                    if submission.use_for(item.slot).kind == "initial":
                        fresh = service_for(repo, resolution, root, item.metadata["basis"])
                        fresh.admit_basis(item.metadata["basis"], item.metadata["binding"]["slot"])
                admitted.append((service, lease, items))
            if any(use.kind == "initial" for use in submission.source_uses):
                if root_context is None:
                    raise _bad()
                resolution, root = root_context
                for item in submission.items:
                    if type(item) is LegacyItem and submission.use_for(item.slot).kind == "initial":
                        _admit_initial_legacy(repo, resolution, root, item)
                _admit_initial_inventory(resolution, root, submission)
            replacements = {}
            if root_context is not None:
                resolution, root = root_context
                from .review_wait_runtime.continuation_transition_repository import TransitionRepository
                path = resolution.paths.review_continuation_store(root.basis["parent_session_id"],
                    root.basis["task_id"], root.basis["target_generation"])
                if path_lexically_exists(path):
                    from .review_answer_rejection import replacement_for
                    history = TransitionRepository(path).read()
                    state = None
                    if any(row.rejected_answer_replacement is not None for row in history):
                        from .review_continuation_handoff_repository import read_state
                        state = read_state(resolution.target, root.basis["task_id"], caller)
                    consumer = submission.continuation["continuation_id"] if submission.continuation else None
                    for service, _lease, items in admitted:
                        records = service.repository.read()
                        for item in items:
                            replacement = replacement_for(root, history, item.metadata, consumer,
                                records[item.metadata["binding"]["slot"]-1]["registration"], state)
                            if replacement is not None:
                                replacements[item.slot] = replacement
            yield RegistrationAdmission(submission, admitted, replacements)
    except AnswerError as exc:
        raise answer_input_error(exc) from None
