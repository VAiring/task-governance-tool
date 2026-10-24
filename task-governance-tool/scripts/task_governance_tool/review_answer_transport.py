"""Typed transport of collected originals; no files, host calls or SQL.

This is a separate arm from reviewer-authored version-one result documents.
Source metadata accompanies unchanged original bytes and never supplies a
review verdict, an inferred provenance declaration or registration authority.
"""

from dataclasses import dataclass
import base64
import binascii
import hashlib
import json

from .review_continuation import RetainedOriginal, identifier
from .review_session_repository import ReviewSessionBinding, ReviewSessionTarget


FORMAT = "taskgov-collected-review-results-v1"
INITIAL_FORMAT = "taskgov-collected-review-results-v2"
LIMIT = 262144


def input_error(classification="format_invalid", field=None, reason="shape"):
    """Use the existing CLI error shape without forwarding untrusted details."""
    from .review_answer import AnswerError
    from .review_answer_values import _failure
    from .review_results import ReviewResultInputError
    failure = dict(**{"class": classification}, field=field, reason=reason)
    try:
        _failure(failure)
    except (AnswerError, TypeError):
        classification, field, reason = "format_invalid", None, "shape"
    return ReviewResultInputError(classification,
        "collected review answer: " + reason, field)


def answer_input_error(error):
    failure = error.failure
    classification = failure["class"]
    if classification == "format_invalid" and failure["reason"] == "identity":
        classification = "binding_mismatch"
    return input_error(classification, failure["field"], failure["reason"])


def _invalid():
    return input_error()


def _binding():
    return input_error("binding_mismatch", reason="identity")


def _capacity(field=None):
    return input_error("capacity_exceeded", field, "bound")


def _digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class CollectedItem:
    slot: int
    raw: bytes
    metadata: dict
    answer: object

    @property
    def session_binding(self):
        basis = self.metadata["basis"]["execution_basis"]
        return ReviewSessionBinding(self.metadata["source_identity"]["reviewer_session_id"],
            basis["execution_id"], "handoff", self.metadata["original"]["digest"].removeprefix("sha256:"))

    @property
    def target(self):
        basis = self.metadata["basis"]["execution_basis"]
        return ReviewSessionTarget(**{name: basis[name] for name in ReviewSessionTarget.__dataclass_fields__})


@dataclass(frozen=True)
class LegacyItem:
    slot: int
    raw: bytes
    source_packet_digest: str
    metadata: dict
    bound: object

    @property
    def session_binding(self):
        return self.bound.binding

    @property
    def target(self):
        return self.bound.target


@dataclass(frozen=True)
class InitialOriginal:
    """Uncaptured initial source proposed for the current atomic batch."""
    session_id: str
    original_digest: str
    metadata_digest: str

    def __post_init__(self):
        from .review_answer_values import session, digest
        session(self.session_id)
        digest(self.original_digest)
        digest(self.metadata_digest)

    def as_dict(self):
        return dict(kind="initial", session_id=self.session_id,
                    original_digest=self.original_digest, metadata_digest=self.metadata_digest)


@dataclass(frozen=True)
class SourceUse:
    """One decoded role, shared by physical admission and native publication."""
    slot: int
    session_id: str
    kind: str
    reference: object = None


@dataclass(frozen=True)
class CollectedSubmission:
    preparation_id: str
    continuation: dict | None
    items: tuple
    retained_receipt_ids: tuple[str, ...]
    retained_originals: tuple
    submission_digest: str
    raw: bytes
    source_uses: tuple[SourceUse, ...] = ()

    def use_for(self, slot):
        matches = tuple(row for row in self.source_uses if row.slot == slot)
        if len(matches) != 1:
            raise _binding()
        return matches[0]

    @property
    def bindings(self):
        # Shared finalizer observations need actual sessions and original hashes,
        # not a fabricated legacy sidecar or provenance declaration.
        from .review_session_transport import BoundResult
        return tuple(item.bound if type(item) is LegacyItem else
            BoundResult(item.target, item.session_binding) for item in self.items)

    @property
    def task_id(self):
        return self.items[0].target.task_id


@dataclass(frozen=True)
class SettledRegistrationRecovery:
    """Internal request to establish settled effects, never a proof or permit."""
    preparation_id: str
    submission_digest: str

    def require_submission(self, submission, finalization_basis):
        from .review_finalization_basis import FinalizationBasis
        if (type(finalization_basis) is not FinalizationBasis
                or type(submission) is not CollectedSubmission
                or self.preparation_id != submission.preparation_id
                or self.submission_digest != submission.submission_digest
                or not 1 <= len(submission.items) <= 8
                or any(type(item) is not CollectedItem or item.metadata["basis"]["route"] != "integrated"
                       for item in submission.items)):
            raise input_error("registration_unknown", reason="effect_unknown")


def _bytes(value):
    if type(value) is not str:
        raise _invalid()
    original = base64.b64decode(value, validate=True)
    if len(original) > LIMIT:
        raise _capacity()
    if not original or base64.b64encode(original).decode("ascii") != value:
        raise _invalid()
    return original


def _capture(value, *, initial=False):
    from .review_answer_values import validate_collected_capture
    if type(value) is not dict:
        raise _invalid()
    if value.get("kind") == "collected":
        return validate_collected_capture(value)
    if initial and value.get("kind") == "initial":
        if set(value) != {"kind", "session_id", "original_digest", "metadata_digest"}:
            raise _invalid()
        return InitialOriginal(value["session_id"], value["original_digest"], value["metadata_digest"])
    if set(value) != set(RetainedOriginal.__dataclass_fields__):
        raise _invalid()
    return RetainedOriginal(**value)


def _capture_session(value):
    return value["session_id"] if type(value) is dict else value.session_id


def decode_collected_submission(raw):
    from .review_answer import decode_answer, AnswerError, scan_strings, _unique, _number
    from .review_answer_values import _validate_metadata_with_answer, metadata_digest
    from .review_session_transport import validate_metadata as validate_legacy
    from .review_results import decode_review_results
    try:
        if type(raw) is not bytes or not raw:
            raise _invalid()
        if len(raw) > LIMIT:
            raise _capacity()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_float=_number, parse_constant=_number)
        scan_strings(value)
        keys = {"format", "preparation_id", "continuation", "items", "retained_receipt_ids", "retained_originals"}
        if (type(value) is not dict or set(value) != keys or value["format"] not in (FORMAT, INITIAL_FORMAT)
            or not identifier(value["preparation_id"], "tg_review_preparation_")
            or type(value["items"]) is not list
            or type(value["retained_receipt_ids"]) is not list
            or type(value["retained_originals"]) is not list):
            raise _invalid()
        if not value["items"]:
            raise _invalid()
        if len(value["items"]) > 8 or len(value["retained_originals"]) > 8:
            raise _capacity()
        continuation = value["continuation"]
        if continuation is not None:
            from .review_continuation import _digest as bare_digest
            if (type(continuation) is not dict or set(continuation) != {"continuation_id", "sequence", "selection_digest"}
                or not identifier(continuation["continuation_id"], "tg_review_continuation_")
                or type(continuation["sequence"]) is not int or not 1 <= continuation["sequence"] <= 64
                or not bare_digest(continuation["selection_digest"])):
                raise _invalid()
        retained = tuple(value["retained_receipt_ids"])
        if (any(not identifier(row, "tg_review_receipt_") for row in retained)
            or len(set(retained)) != len(retained)):
            raise _invalid()
        if len(retained) + len(value["items"]) > 8:
            raise _capacity()
        extended = value["format"] == INITIAL_FORMAT
        captures = tuple(_capture(row, initial=extended) for row in value["retained_originals"])
        if extended and (continuation is None or not any(type(row) is InitialOriginal for row in captures)):
            raise _invalid()
        capture_sessions = tuple(_capture_session(row) for row in captures)
        if (len(set(capture_sessions)) != len(capture_sessions)
            or capture_sessions != tuple(sorted(capture_sessions, key=lambda row: row.encode("utf-8")))
            or continuation is None and (retained or captures)):
            raise _invalid()
        by_session = dict(zip(capture_sessions, captures))
        consumed, sessions, slots, items, uses = set(), set(), [], [], []
        common_basis, common_route, findings = None, None, 0
        for entry in value["items"]:
            if (type(entry) is not dict or type(entry.get("slot")) is not int
                or not 1 <= entry["slot"] <= 8):
                raise _invalid()
            slot = entry["slot"]
            kind = entry.get("kind")
            if kind == "collected":
                if set(entry) != {"kind", "slot", "original_base64", "metadata"}:
                    raise _invalid()
                original = _bytes(entry["original_base64"])
                # Admit structural metadata before answer privacy; semantic
                # identity checks must not hide an answer privacy rejection.
                from .review_answer_values import shape, metadata_schema
                shape(entry["metadata"], metadata_schema(entry["metadata"]))
                answer = decode_answer(original)
                metadata = _validate_metadata_with_answer(entry["metadata"], answer)
                basis, binding = metadata["basis"], metadata["binding"]
                if basis["preparation_id"] != value["preparation_id"]:
                    raise _binding()
                if common_basis is None:
                    common_basis, common_route = basis["execution_basis"], basis["route"]
                elif (basis["execution_basis"], basis["route"]) != (common_basis, common_route):
                    raise _binding()
                session = metadata["source_identity"]["reviewer_session_id"]
                capture = by_session.get(session)
                if basis["continuation"] != continuation:
                    if type(capture) is InitialOriginal:
                        if (basis["continuation"] is not None
                            or capture.original_digest != metadata["original"]["digest"]
                            or capture.metadata_digest != metadata_digest(metadata)):
                            raise _binding()
                    elif (continuation is None or type(capture) is not dict or capture["consumer_continuation_id"] != continuation["continuation_id"]
                        or capture["consumer_slot"] != slot or capture["source_slot"] != binding["slot"]
                        or capture["source_preparation_id"] != basis["preparation_id"]
                        or capture["source_continuation_id"] != (basis["continuation"]["continuation_id"] if basis["continuation"] else None)
                        or capture["source_basis_digest"] != binding["basis_digest"]
                        or capture["original_digest"] != metadata["original"]["digest"]
                        or capture["metadata_digest"] != metadata_digest(metadata)):
                        raise _binding()
                    consumed.add(session)
                elif capture is not None or binding["slot"] != slot:
                    raise _binding()
                items.append(CollectedItem(slot, original, metadata, answer))
                findings += len(answer.payload["findings"])
            elif kind == "legacy":
                if set(entry) != {"kind", "slot", "source_packet_digest", "original_base64", "binding"}:
                    raise _invalid()
                if continuation is None:
                    raise _binding()
                original = _bytes(entry["original_base64"])
                bound = validate_legacy(entry["binding"], original)
                session = bound.binding.session_id
                capture = by_session.get(session)
                from .review_continuation import _digest as bare_digest
                if (type(capture) not in (RetainedOriginal, InitialOriginal) or type(entry["source_packet_digest"]) is not str
                    or not entry["source_packet_digest"].startswith("sha256:")
                    or not bare_digest(entry["source_packet_digest"][7:])
                    or bound.binding.original_result_digest != (
                        capture.original_digest.removeprefix("sha256:") if type(capture) is InitialOriginal
                        else capture.original_result_digest)):
                    raise _binding()
                from .review_session_transport import metadata_digest as legacy_seal
                if type(capture) is InitialOriginal:
                    if bound.continuation is not None or "sha256:" + legacy_seal(entry["binding"]) != capture.metadata_digest:
                        raise _binding()
                elif (legacy_seal(entry["binding"]) != capture.metadata_digest or bound.continuation is None
                      or bound.continuation.identity.continuation_id != capture.prior_continuation_id):
                    raise _binding()
                consumed.add(session)
                items.append(LegacyItem(slot, original, entry["source_packet_digest"], entry["binding"], bound))
                findings += len(decode_review_results(original)["receipts"][0]["findings"])
            else:
                raise _invalid()
            if session in sessions:
                raise _binding()
            sessions.add(session)
            slots.append(slot)
            uses.append(SourceUse(slot, session,
                "initial" if type(capture) is InitialOriginal else "captured" if capture is not None else "selected",
                capture))
        if findings > 64:
            raise _capacity("findings")
        if common_basis is None or slots != sorted(set(slots)):
            raise _invalid()
        if consumed != set(capture_sessions):
            raise _binding()
        if extended:
            ranks = {"selected": 0, "captured": 1, "initial": 2}
            if (slots != list(range(1, len(items) + 1))
                or [ranks[row.kind] for row in uses] != sorted(ranks[row.kind] for row in uses)
                or any([row.session_id for row in uses if row.kind == kind] !=
                       sorted(row.session_id for row in uses if row.kind == kind)
                       for kind in ("captured", "initial"))):
                raise _binding()
        target = items[0].target
        if any(item.target != target for item in items):
            raise _binding()
        return CollectedSubmission(value["preparation_id"], continuation, tuple(items),
            retained, captures, _digest(raw), raw, tuple(uses))
    except AnswerError as error:
        raise answer_input_error(error) from None
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, binascii.Error):
        raise _invalid() from None


def frame_collected_submission(preparation_id, items, *, continuation=None,
                               retained_receipt_ids=(), retained_originals=()):
    rows = []
    for item in items:
        if type(item) is CollectedItem:
            rows.append(dict(kind="collected", slot=item.slot,
                original_base64=base64.b64encode(item.raw).decode("ascii"), metadata=item.metadata))
        elif type(item) is LegacyItem:
            rows.append(dict(kind="legacy", slot=item.slot, source_packet_digest=item.source_packet_digest,
                original_base64=base64.b64encode(item.raw).decode("ascii"), binding=item.metadata))
        else:
            raise _invalid()
    value = dict(format=INITIAL_FORMAT if any(type(row) is InitialOriginal for row in retained_originals) else FORMAT,
        preparation_id=preparation_id, continuation=continuation, items=rows,
        retained_receipt_ids=list(retained_receipt_ids),
        retained_originals=[row.as_dict() if type(row) in (RetainedOriginal, InitialOriginal) else row for row in retained_originals])
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    decode_collected_submission(raw)
    return raw
