"""Collect a bound final answer through short operational facts and shared I/O.

The service grants no host, Git, registration or completion permission. Its
admission callback checks the caller's live preparation/basis outside SQLite.
Neither construction nor inspection retries an interrupted effect.
"""

from copy import deepcopy
from contextlib import contextmanager
from uuid import uuid4

from .review_answer import AnswerError, decode_answer
from . import review_answer_values as values
from . import review_handoff_transport as files


def source(operation):
    return dict(adapter="codex-app-tools-review-answer", version=1, operation=operation)


def _fail(classification="collection_unavailable", reason="missing"):
    raise AnswerError(classification, reason=reason)


def _failure(classification="collection_unavailable", reason="missing"):
    return dict(**{"class": classification}, field=None, reason=reason)


def _ordinal(record):
    correction = record["correction"]
    if correction is None:
        return 0
    if correction["effect"] != "accepted" or correction["correction_turn_id"] is None:
        _fail(reason="effect_unknown")
    return 1


def _attempt(record, ordinal):
    return record["attempts"][ordinal]


class CollectionService:
    def __init__(self, repo, repository, *, original_paths, admit_basis):
        if not callable(admit_basis) or type(original_paths) not in (tuple, list) or not 1 <= len(original_paths) <= 8:
            _fail("binding_mismatch", "identity")
        # Prepared names are structural here; ignored physical checks occur at
        # each I/O boundary, not during a read-only service construction.
        if len(set(original_paths)) != len(original_paths):
            _fail("binding_mismatch", "identity")
        for path in original_paths:
            files.relative_path(repo, path)
        self.repo, self.repository = repo, repository
        self.original_paths = tuple(original_paths)
        self.admit_basis = admit_basis

    def _record(self, slot):
        records = self.repository.read()
        if type(slot) is not int or not 1 <= slot <= len(records) or len(records) != len(self.original_paths):
            _fail("binding_mismatch", "identity")
        return records[slot - 1]

    def bind(self, slot, binding):
        with self.repository.serial() as lease:
            record = self._record(slot)
            self.admit_basis(record["basis"], slot)
            binding = values.validate_binding(binding, record["basis"])
            if binding["slot"] != slot:
                _fail("binding_mismatch", "identity")
            if record["binding"] is not None:
                if record["binding"] != binding:
                    _fail("binding_mismatch", "conflict")
                return record
            def change(row):
                row["binding"] = binding
                row["attempts"][0]["turn_id"] = binding["initial_turn_id"]
                return row
            return self.repository.update(lease, slot, change)

    def _observation(self, record, observation):
        expected = {"source_identity", "answer_bytes", "complete", "terminal", "answer_source", "terminal_source"}
        if type(observation) is not dict or set(observation) != expected or observation["complete"] is not True:
            _fail(reason="incomplete")
        if record["binding"] is None:
            _fail("binding_mismatch", "identity")
        identity = observation["source_identity"]
        ordinal = _ordinal(record)
        if (type(identity) is not dict or set(identity) != {"reviewer_session_id", "turn_id", "message_id"}
                or identity["reviewer_session_id"] != record["binding"]["reviewer_session_id"]
                or identity["turn_id"] != record["attempts"][ordinal]["turn_id"]):
            _fail("binding_mismatch", "identity")
        values.validate_source(observation["answer_source"], "answer_observation")
        terminal = observation["terminal"]
        if terminal not in ("unknown", "in_progress", "completed", "failed", "interrupted"):
            _fail(reason="shape")
        if terminal == "unknown":
            if observation["terminal_source"] is not None:
                _fail(reason="shape")
        else:
            values.validate_source(observation["terminal_source"], "terminal_observation")
        return ordinal, identity

    def observe(self, slot, observation):
        """Accept one observation; duplicate bodies do not repeat publication."""
        with self.repository.serial() as lease:
            record = self._record(slot)
            self.admit_basis(record["basis"], slot)
            ordinal, identity = self._observation(record, observation)
            attempt = _attempt(record, ordinal)
            if (attempt["terminal"] in ("completed", "failed", "interrupted")
                    and observation["terminal"] != attempt["terminal"]):
                _fail("binding_mismatch", "conflict")
            if record["registration"]["state"] != "not_started":
                _fail("registration_unknown", "effect_unknown")
            if attempt["observation"] == "conflict":
                _fail("binding_mismatch", "conflict")
            if attempt["source_identity"] is not None and attempt["source_identity"] != identity:
                return self._latch_conflict(lease, slot, ordinal)
            # A shape/privacy-unknown failure has no comparable body/hash.
            # Returning it does not call two unknown observations duplicates.
            if (attempt["observation"] == "complete" and attempt["failure"] is not None
                    and attempt["observed_digest"] is None):
                raise AnswerError(**self._error_kwargs(attempt["failure"]))
            decoded, failure, digest = None, None, None
            try:
                decoded = decode_answer(observation["answer_bytes"])
                digest = decoded.original_digest
            except AnswerError as error:
                failure, digest = error.failure, error.observed_digest
            if attempt["observed_digest"] is not None:
                if digest != attempt["observed_digest"]:
                    return self._latch_conflict(lease, slot, ordinal)
                if attempt["failure"] is not None:
                    raise AnswerError(**self._error_kwargs(attempt["failure"]))
            def observed(row):
                current = row["attempts"][ordinal]
                current.update(source_identity=deepcopy(identity), observation="complete", observed_digest=digest, failure=failure)
                if current["terminal"] not in ("completed", "failed", "interrupted"):
                    current.update(terminal=observation["terminal"], terminal_source=observation["terminal_source"])
                return row
            record = self.repository.update(lease, slot, observed)
            if failure is not None:
                raise AnswerError(**self._error_kwargs(failure), privacy_admissible=digest is not None, observed_digest=digest)
            publication = record["attempts"][ordinal]["publication"]
            if publication is not None:
                if publication["state"] != "saved":
                    # Read-only restart/repeated delivery never adopts residue.
                    _fail("save_unknown", "effect_unknown")
                self._readback(record, slot, ordinal, require_terminal=False)
                return record
            original = dict(path=self.original_paths[slot - 1], byte_length=len(decoded.raw), digest=decoded.original_digest,
                payload_start=decoded.payload_start, payload_length=decoded.payload_length, payload_digest=decoded.payload_digest)
            # A correction never overwrites the invalid initial answer (which
            # was not published). There is one accepted original path per slot.
            values.validate_original(original, decoded.raw)
            path = files.checked_path(self.repo, original["path"])
            self.admit_basis(record["basis"], slot)
            def intent(row):
                row["attempts"][ordinal]["publication"] = dict(state="intent", original=original)
                return row
            record = self.repository.update(lease, slot, intent)
            try:
                self.admit_basis(record["basis"], slot)
                # Privacy and the full original are revalidated immediately
                # before I/O; no transaction spans this operation.
                decode_answer(decoded.raw)
                files.write_exclusive(path, decoded.raw)
                if files.read_bounded(files.checked_path(self.repo, original["path"]), 262144) != decoded.raw:
                    _fail("save_unknown", "conflict")
                self.admit_basis(record["basis"], slot)
                def saved(row):
                    row["attempts"][ordinal]["publication"]["state"] = "saved"
                    return row
                return self.repository.update(lease, slot, saved)
            except Exception:
                def uncertain(row):
                    row["attempts"][ordinal]["publication"]["state"] = "unknown"
                    return row
                self.repository.update(lease, slot, uncertain)
                _fail("save_unknown", "effect_unknown")

    @staticmethod
    def _error_kwargs(failure):
        return dict(classification=failure["class"], field=failure["field"], reason=failure["reason"])

    def _latch_conflict(self, lease, slot, ordinal):
        def change(row):
            row["attempts"][ordinal]["observation"] = "conflict"
            row["attempts"][ordinal]["failure"] = _failure("binding_mismatch", "conflict")
            return row
        self.repository.update(lease, slot, change)
        _fail("binding_mismatch", "conflict")

    def _readback(self, record, slot, ordinal, *, require_terminal=True):
        attempt = record["attempts"][ordinal]
        if attempt["publication"] is not None and attempt["publication"]["state"] != "saved":
            _fail("save_unknown", "effect_unknown")
        if attempt["failure"] is not None:
            raise AnswerError(**self._error_kwargs(attempt["failure"]))
        if (attempt["observation"] != "complete"
                or attempt["publication"] is None or attempt["publication"]["state"] != "saved"
                or require_terminal and attempt["terminal"] != "completed"):
            _fail(reason="incomplete")
        original = attempt["publication"]["original"]
        if original["path"] != self.original_paths[slot - 1]:
            _fail("binding_mismatch", "identity")
        raw = files.read_bounded(files.checked_path(self.repo, original["path"]), 262144)
        values.validate_original(original, raw)
        decode_answer(raw)
        correction = record["correction"]
        metadata = dict(basis=record["basis"], binding=record["binding"], attempt=ordinal,
            source_identity=attempt["source_identity"], original=original, answer_source=source("answer_observation"),
            terminal=attempt["terminal"], terminal_source=attempt["terminal_source"], correction_binding=None)
        if ordinal == 1:
            metadata["correction_binding"] = dict(intent_id=correction["intent_id"],
                initial_turn_id=record["binding"]["initial_turn_id"], correction_turn_id=correction["correction_turn_id"],
                source=correction["binding_source"])
        if require_terminal:
            values.validate_metadata(metadata, raw)
        return raw, metadata

    def readback(self, slot, *, require_terminal=True):
        """Confirm exact saved bytes; hook-only reads preserve pending terminal facts."""
        record = self._record(slot)
        self.admit_basis(record["basis"], slot)
        ordinal = _ordinal(record)
        result = self._readback(record, slot, ordinal, require_terminal=require_terminal)
        self.admit_basis(record["basis"], slot)
        if self._record(slot) != record:
            _fail("binding_mismatch", "conflict")
        # The helper's subsequent framing performs the second whole-batch
        # confirmation. This last local comparison closes this read's I/O.
        original = record["attempts"][ordinal]["publication"]["original"]
        if files.read_bounded(files.checked_path(self.repo, original["path"]), 262144) != result[0]:
            _fail("save_unknown", "conflict")
        return result

    def reconcile_publication(self, slot):
        """Explicit recovery only: adopt exact complete residue, never rewrite."""
        with self.repository.serial() as lease:
            record = self._record(slot)
            self.admit_basis(record["basis"], slot)
            ordinal = _ordinal(record)
            attempt = record["attempts"][ordinal]
            if (attempt["publication"] is None or attempt["publication"]["state"] not in ("intent", "unknown")
                    or attempt["observed_digest"] is None or attempt["failure"] is not None):
                _fail("save_unknown", "effect_unknown")
            original = attempt["publication"]["original"]
            if original["path"] != self.original_paths[slot - 1]:
                _fail("binding_mismatch", "identity")
            try:
                raw = files.read_bounded(files.checked_path(self.repo, original["path"]), 262144)
                values.validate_original(original, raw)
                decode_answer(raw)
            except Exception:
                # Missing, partial, changed or unsafe residue does not settle
                # the saved effect and never authorizes a new publication.
                _fail("save_unknown", "effect_unknown")
            self.admit_basis(record["basis"], slot)
            def change(row):
                row["attempts"][ordinal]["publication"]["state"] = "saved"
                return row
            try:
                return self.repository.update(lease, slot, change)
            except Exception:
                _fail("save_unknown", "effect_unknown")

    def recover(self, slot, host, *, explicit_recovery=False, require_terminal=True):
        """Recover one exact source; only the parent may reconcile saved intent."""
        record = self._record(slot)
        self.admit_basis(record["basis"], slot)
        if record["binding"] is None:
            _fail("binding_mismatch", "identity")
        ordinal = _ordinal(record)
        attempt = record["attempts"][ordinal]
        if attempt["publication"] is not None and attempt["publication"]["state"] != "saved":
            # An unresolved save precedes stored failures, just as in readback.
            # A conflict must remain latched and cannot authorize reconciliation.
            if explicit_recovery is not True or attempt["failure"] is not None:
                _fail("save_unknown", "effect_unknown")
            record = self.reconcile_publication(slot)
            attempt = record["attempts"][ordinal]
        if attempt["observation"] in ("complete", "conflict") and attempt["failure"] is not None:
            raise AnswerError(**self._error_kwargs(attempt["failure"]),
                privacy_admissible=attempt["observed_digest"] is not None,
                observed_digest=attempt["observed_digest"])
        if attempt["publication"] is not None:
            if (require_terminal and attempt["publication"]["state"] == "saved"
                    and attempt["terminal"] in ("unknown", "in_progress")):
                terminal = host.observe_terminal(basis_digest=values.basis_digest(record["basis"]), slot=slot,
                    attempt=ordinal, reviewer_session_id=record["binding"]["reviewer_session_id"],
                    turn_id=attempt["turn_id"])
                self.observe_terminal(slot, terminal)
            return self.readback(slot, require_terminal=require_terminal)
        identity = attempt["source_identity"]
        try:
            observed = host.observe_answer(basis_digest=values.basis_digest(record["basis"]), slot=slot, attempt=ordinal,
                reviewer_session_id=record["binding"]["reviewer_session_id"], turn_id=attempt["turn_id"],
                expected_message_id=identity["message_id"] if identity else None)
        except Exception as error:
            failure = error.failure if isinstance(error, AnswerError) else _failure(reason="transport_unavailable")
            # Persist only a fixed failure fact, never a rejected host body or
            # a hash of incomplete/shape-unknown content. This is not a retry.
            with self.repository.serial() as lease:
                current = self._record(slot)
                self.admit_basis(current["basis"], slot)
                if (_ordinal(current) == ordinal and current["binding"] == record["binding"]
                        and current["attempts"][ordinal]["observation"] in ("missing", "unavailable")
                        and current["registration"]["state"] == "not_started"):
                    def unavailable(row):
                        row["attempts"][ordinal].update(observation="unavailable", failure=failure)
                        return row
                    self.repository.update(lease, slot, unavailable)
            raise AnswerError(**self._error_kwargs(failure)) from None
        self.observe(slot, observed)
        return self.readback(slot, require_terminal=require_terminal)

    def observe_terminal(self, slot, observation):
        with self.repository.serial() as lease:
            record = self._record(slot)
            self.admit_basis(record["basis"], slot)
            ordinal = _ordinal(record)
            attempt = record["attempts"][ordinal]
            if (type(observation) is not dict or set(observation) != {"reviewer_session_id", "turn_id", "terminal", "terminal_source"}
                    or record["binding"] is None or observation["reviewer_session_id"] != record["binding"]["reviewer_session_id"]
                    or observation["turn_id"] != attempt["turn_id"]):
                _fail("binding_mismatch", "identity")
            if attempt["terminal"] in ("completed", "failed", "interrupted"):
                if observation["terminal"] != attempt["terminal"]:
                    _fail("binding_mismatch", "conflict")
                return record
            def change(row):
                row["attempts"][ordinal].update(terminal=observation["terminal"], terminal_source=observation["terminal_source"])
                return row
            return self.repository.update(lease, slot, change)

    def correction(self, slot, host, *, observer=None):
        """Reserve one format correction; a possible send is never repeated."""
        with self.repository.serial() as lease:
            record = self._record(slot)
            self.admit_basis(record["basis"], slot)
            if record["correction"] is not None:
                return record
            initial = record["attempts"][0]
            if (record["registration"]["state"] != "not_started" or initial["failure"] is None
                    or initial["failure"]["class"] != "format_invalid" or initial["observed_digest"] is None):
                _fail(reason="shape")
            intent = dict(intent_id="tg_review_correction_" + uuid4().hex[:16],
                initial_source_identity=initial["source_identity"], initial_digest=initial["observed_digest"],
                effect="not_sent", correction_turn_id=None, binding_source=None, failure=None)
            def reserve(row):
                row["correction"] = intent
                return row
            record = self.repository.update(lease, slot, reserve)
            try:
                if observer is None or observer(record) is not True:
                    _fail(reason="observer_unavailable")
                self.admit_basis(record["basis"], slot)
            except Exception:
                def unavailable(row):
                    row["correction"]["failure"] = _failure(reason="observer_unavailable")
                    return row
                return self.repository.update(lease, slot, unavailable)
            def dispatched(row):
                row["correction"]["effect"] = "unknown"
                return row
            record = self.repository.update(lease, slot, dispatched)
            try:
                result = host.send_correction(basis_digest=values.basis_digest(record["basis"]), slot=slot,
                    initial_turn_id=record["binding"]["initial_turn_id"], correction_intent_id=intent["intent_id"],
                    diagnostic=initial["failure"])
            except Exception:
                return record
            if type(result) is not dict or set(result) != {"effect", "correction_turn_id", "source"}:
                return record
            # The host may refuse before dispatch. Preserve that known fact;
            # this still consumes the saved opportunity and never retries.
            effect = result["effect"]
            if effect not in ("accepted", "failed", "unknown", "not_sent"):
                return record
            def settled(row):
                correction = row["correction"]
                correction.update(effect=effect, correction_turn_id=result["correction_turn_id"], binding_source=result["source"])
                if effect in ("not_sent", "failed", "unknown"):
                    correction["failure"] = _failure(reason="effect_unknown" if effect == "unknown" else "transport_unavailable")
                if effect == "accepted" and result["correction_turn_id"] is not None:
                    row["attempts"].append(dict(ordinal=1, turn_id=result["correction_turn_id"], source_identity=None,
                        observation="missing", observed_digest=None, terminal="unknown", terminal_source=None,
                        publication=None, failure=None))
                return row
            # A malformed acknowledgement after a possible send must retain
            # the durable unknown intent, not invent a binding or retry.
            try:
                candidate = settled(deepcopy(record))
                values.validate_record(candidate)
            except AnswerError:
                return record
            return self.repository.update(lease, slot, lambda _row: candidate)

    def mark_registration(self, slots, submission_digest):
        with self.repository.serial() as lease:
            return self.mark_registration_locked(lease, slots, submission_digest)

    def mark_registration_locked(self, lease, slots, submission_digest):
        """Save admitted source-slot intent before core I/O.

        Continuations may capture a subset of an older source store. The
        composer/core own the complete consumer inventory across source stores.
        """
        records = self.repository.read()
        slots = tuple(slots)
        if (not slots or any(type(slot) is not int or not 1 <= slot <= len(records) for slot in slots)
                or slots != tuple(sorted(set(slots)))):
            _fail("binding_mismatch", "identity")
        for slot in slots:
            record = records[slot - 1]
            self.admit_basis(record["basis"], slot)
            self._readback(record, slot, _ordinal(record))
            if record["registration"]["state"] != "not_started":
                _fail("registration_unknown", "effect_unknown")
        def dispatched(row):
            row["registration"] = dict(state="dispatched", submission_digest=submission_digest, receipt_id=None)
            return row
        return self.repository.update_many(lease, {slot: dispatched for slot in slots})

    @contextmanager
    def admission(self, slots=None):
        """Finish all I/O before yielding frozen inputs for the core writer.

        Lock order: finalization, collection, core writer. Never acquire a
        preparation/request/finalization lease beneath the collection lease.
        """
        with self.repository.serial() as lease:
            records = self.repository.read()
            selected = tuple(range(1, len(records) + 1)) if slots is None else tuple(slots)
            if not selected or len(set(selected)) != len(selected):
                _fail("binding_mismatch", "identity")
            results = []
            for slot in selected:
                record = self._record(slot)
                self.admit_basis(record["basis"], slot)
                results.append(self._readback(record, slot, _ordinal(record)))
            yield lease, tuple(results)

    def confirm_registration(self, slot, submission_digest, receipt_id):
        """Caller supplies an exact public successful response/reconciliation."""
        with self.repository.serial() as lease:
            return self.confirm_registration_locked(lease, slot, submission_digest, receipt_id)

    def confirm_registration_locked(self, lease, slot, submission_digest, receipt_id):
        record = self._record(slot)
        if (record["registration"]["state"] not in ("dispatched", "unknown", "registered")
                or record["registration"]["submission_digest"] != submission_digest):
            _fail("registration_unknown", "effect_unknown")
        def confirmed(row):
            row["registration"].update(state="registered", receipt_id=receipt_id)
            return row
        return self.repository.update(lease, slot, confirmed)

    def unknown_registration_locked(self, lease, slots, submission_digest):
        def uncertain(row):
            if (row["registration"]["state"] not in ("dispatched", "unknown")
                    or row["registration"]["submission_digest"] != submission_digest):
                _fail("registration_unknown", "effect_unknown")
            row["registration"]["state"] = "unknown"
            return row
        return self.repository.update_many(lease, {slot: uncertain for slot in slots})
