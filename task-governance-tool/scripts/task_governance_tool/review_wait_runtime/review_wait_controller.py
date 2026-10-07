"""Source-only review-wait decisions; no host, filesystem, usage or Task writes.

Inputs are already admitted observations, not evidence of host authenticity.
The integration must serialize one wait across processes, persist the returned
intent before its external effect, and refresh its exact Task and host basis.
Restoring a snapshot never authorizes replay of an outstanding operation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
import re
from threading import RLock
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SHORT_RULE = "FREQ=MINUTELY;INTERVAL=1"
TEN_MINUTE_RULE = "FREQ=MINUTELY;INTERVAL=10"
MAX_REVIEWERS = 64
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,255}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_DAILY = re.compile(r"FREQ=DAILY;BYHOUR=([0-9]{1,2});BYMINUTE=([0-9]{1,2});BYSECOND=([0-9]{1,2});COUNT=1\Z")
TerminalStatus = Literal["completed", "failed", "interrupted"]


class ControllerError(ValueError):
    def __init__(self) -> None:
        super().__init__("Invalid review-wait state or input.")


def _require(condition: bool) -> None:
    if not condition:
        raise ControllerError()


def _token(value: object, *, empty: bool = False) -> None:
    _require(type(value) is str and ((empty and value == "") or bool(_TOKEN.fullmatch(value))))


def _positive(value: object, *, zero: bool = False) -> None:
    _require(type(value) is int and (0 if zero else 1) <= value <= 2**53 - 1)


def _stored_text(value: object, limit: int) -> None:
    # Current core admission owns privacy and domain validation. Preserve its
    # legal Unicode IDs/revision labels verbatim rather than narrowing them to
    # the controller's own ASCII operation-token syntax.
    _require(type(value) is str and bool(value.strip()) and len(value) <= limit and "\x00" not in value)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ControllerError() from None


def _instant(value: object) -> datetime:
    _require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _rule(value: object, *, daily: bool = False) -> None:
    _require(type(value) is str)
    match = _DAILY.fullmatch(value)
    _require((not daily and value in (SHORT_RULE, TEN_MINUTE_RULE)) or
             bool(match and int(match[1]) < 24 and int(match[2]) < 60 and int(match[3]) < 60))


@dataclass(frozen=True)
class WaitBinding:
    wait_id: str
    project_id: str
    project_path_hash: str
    project_binding_generation: int
    task_id: str
    execution_id: str
    contract_revision: int
    target_kind: str
    target_value: str
    target_base_revision: str
    target_generation: int
    artifact_manifest_id: str
    parent_thread_id: str
    ownership_generation: int

    def __post_init__(self) -> None:
        for name in ("wait_id", "project_id", "execution_id", "target_kind",
                     "artifact_manifest_id", "parent_thread_id"):
            _token(getattr(self, name))
        _stored_text(self.task_id, 128)
        _stored_text(self.target_value, 500)
        _require(len(self.wait_id) <= 128)
        _token(self.target_base_revision, empty=True)
        _require(type(self.project_path_hash) is str and bool(_DIGEST.fullmatch(self.project_path_hash)))
        _positive(self.contract_revision, zero=True)
        for value in (self.project_binding_generation, self.target_generation,
                      self.ownership_generation):
            _positive(value)


@dataclass(frozen=True)
class Reviewer:
    reviewer_id: str
    turn_id: str

    def __post_init__(self) -> None:
        _token(self.reviewer_id)
        _token(self.turn_id)


@dataclass(frozen=True)
class Reservation:
    timer_id: str
    parent_thread_id: str
    rule: str
    timezone: str
    status: Literal["ACTIVE", "PAUSED"]
    identity_digest: str

    def __post_init__(self) -> None:
        _token(self.timer_id)
        _token(self.parent_thread_id)
        _token(self.timezone)
        _rule(self.rule)
        _require(type(self.status) is str and self.status in ("ACTIVE", "PAUSED"))
        _require(type(self.identity_digest) is str and bool(_DIGEST.fullmatch(self.identity_digest)))


@dataclass(frozen=True)
class Appointment:
    chosen_at: datetime
    due_at: datetime
    rule: str
    timezone: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "chosen_at", _instant(self.chosen_at))
        object.__setattr__(self, "due_at", _instant(self.due_at))
        _require(self.due_at - self.chosen_at == timedelta(minutes=10))
        _require(self.chosen_at.microsecond == 0 and self.due_at.microsecond == 0)
        _rule(self.rule, daily=True)
        _token(self.timezone)
        try:
            zone = (timezone.utc if self.timezone in ("UTC", "Etc/UTC", "Etc/GMT", "GMT")
                    else ZoneInfo(self.timezone))
        except (ZoneInfoNotFoundError, ValueError):
            raise ControllerError() from None
        local = self.due_at.astimezone(zone)
        match = _DAILY.fullmatch(self.rule)
        _require((local.hour, local.minute, local.second) == tuple(map(int, match.groups())))
        # A daily clock rule cannot distinguish the two occurrences of a fold.
        # Keep the explicit instant only when the matching wall clock is unique.
        wall = local.replace(tzinfo=None)
        candidates = {wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                      for fold in (0, 1)
                      if wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                      .astimezone(zone).replace(tzinfo=None) == wall}
        _require(candidates == {self.due_at})


@dataclass(frozen=True)
class OperationIntent:
    operation_id: str
    kind: Literal["arm", "shorten", "pause", "rearm"]
    expected_arm: int
    before: Reservation
    after: Reservation
    appointment: Appointment | None = None

    def __post_init__(self) -> None:
        _token(self.operation_id)
        _positive(self.expected_arm, zero=True)
        _require(type(self.kind) is str and self.kind in ("arm", "shorten", "pause", "rearm"))
        _require(type(self.before) is Reservation and type(self.after) is Reservation)
        _require((self.before.timer_id, self.before.parent_thread_id, self.before.identity_digest) ==
                 (self.after.timer_id, self.after.parent_thread_id, self.after.identity_digest))
        if self.kind in ("arm", "rearm"):
            _require(type(self.appointment) is Appointment and self.before.status == "PAUSED")
            _require(self.after == replace(self.before, status="ACTIVE", rule=self.appointment.rule,
                                           timezone=self.appointment.timezone))
        else:
            _require(self.appointment is None and self.before.status == "ACTIVE")
            expected = (replace(self.before, status="PAUSED") if self.kind == "pause"
                        else replace(self.before, rule=SHORT_RULE))
            _require(self.after == expected)


class ReviewWaitController:
    """One serial decision owner. External serialization/durability is separate.

    ``identity_digest`` binds all immutable heartbeat fields (including prompt,
    name and destination) without retaining their content. Host adapters must
    admit these values and the actual configured timezone; this core cannot.
    ``settle`` requires proof the previous operation has finished, in addition
    to exact readback. A timeout followed by an old value is not such proof.
    """

    def __init__(self, binding: WaitBinding, reviewers: tuple[Reviewer, ...],
                 reservation: Reservation) -> None:
        _require(type(binding) is WaitBinding and type(reservation) is Reservation)
        _require(type(reviewers) is tuple and 1 <= len(reviewers) <= MAX_REVIEWERS and
                 all(type(item) is Reviewer for item in reviewers))
        _require(len({item.reviewer_id for item in reviewers}) == len(reviewers))
        _require(binding.parent_thread_id not in {item.reviewer_id for item in reviewers})
        _require(reservation.parent_thread_id == binding.parent_thread_id and reservation.status == "PAUSED")
        self._lock = RLock()
        self._binding = binding
        self._reviewers = tuple(sorted(reviewers, key=lambda item: item.reviewer_id))
        self._reservation = reservation
        self._arm = 0
        self._phase = "preparing"
        self._appointment: Appointment | None = None
        self._terminal: dict[str, str] = {}
        self._shortening_attempted = False
        self._shortened = False
        self._timing_unknown = False
        self._pending: OperationIntent | None = None
        self._outcome_unknown = False
        self._stop_confirmed = True
        self._checked_arm: int | None = None
        self._consumed_wake_id: str | None = None
        self._operation_sequence = 0

    @property
    def binding(self) -> WaitBinding:
        return self._binding

    @property
    def reviewers(self) -> tuple[Reviewer, ...]:
        return self._reviewers

    @property
    def reservation(self) -> Reservation:
        return self._reservation

    @property
    def pending(self) -> OperationIntent | None:
        return self._pending

    @property
    def arm_number(self) -> int:
        return self._arm

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def all_ended(self) -> bool:
        return len(self._terminal) == len(self._reviewers)

    @property
    def ready(self) -> bool:
        return self._phase == "waiting" and self._pending is None and not self._timing_unknown

    @property
    def stop_confirmed(self) -> bool:
        return self._stop_confirmed and self._pending is None

    @property
    def needs_reconciliation(self) -> bool:
        return self._outcome_unknown or self._timing_unknown

    def _parent(self, parent_thread_id: str, binding: WaitBinding, arm: int) -> bool:
        return (type(binding) is WaitBinding and binding == self._binding and
                type(parent_thread_id) is str and parent_thread_id == self._binding.parent_thread_id and
                type(arm) is int and arm == self._arm)

    def _fresh(self, readback: Reservation | None) -> bool:
        if type(readback) is Reservation and readback == self._reservation:
            return True
        self._timing_unknown = True
        return False

    def _intent(self, kind: str, after: Reservation,
                appointment: Appointment | None = None) -> OperationIntent:
        _require(self._pending is None)
        sequence = self._operation_sequence + 1
        _positive(sequence)
        pending = OperationIntent(f"{self._binding.wait_id}:{sequence}", kind,
                                  self._arm, self._reservation, after, appointment)
        self._operation_sequence, self._pending = sequence, pending
        self._outcome_unknown = False
        return self._pending

    def arm(self, *, parent_thread_id: str, binding: WaitBinding, appointment: Appointment,
            readback: Reservation | None, now: datetime) -> OperationIntent | None:
        with self._lock:
            now = _instant(now)
            _require(type(appointment) is Appointment)
            if (not self._parent(parent_thread_id, binding, 0) or self._phase != "preparing" or
                    self._pending is not None or not appointment.chosen_at <= now < appointment.due_at):
                return None
            if not self._fresh(readback):
                return None
            if appointment.timezone != self._reservation.timezone:
                self._timing_unknown = True
                return None
            return self._intent("arm", replace(self._reservation, status="ACTIVE", rule=appointment.rule,
                                                timezone=appointment.timezone), appointment)

    def observe_terminal(self, *, binding: WaitBinding, reviewer: Reviewer,
                         status: TerminalStatus) -> bool:
        """Admit an actual terminal latestTurn, never a SubagentStop candidate."""
        with self._lock:
            if (type(binding) is not WaitBinding or binding != self._binding or
                    type(reviewer) is not Reviewer or reviewer not in self._reviewers or
                    type(status) is not str or status not in ("completed", "failed", "interrupted") or
                    reviewer.reviewer_id in self._terminal):
                return False
            self._terminal[reviewer.reviewer_id] = status
            if self.all_ended and self._phase == "checking":
                self._phase = "closed"
            return True

    def maybe_shorten(self, *, binding: WaitBinding, expected_arm: int,
                      readback: Reservation | None, now: datetime) -> OperationIntent | None:
        with self._lock:
            now = _instant(now)
            if (binding != self._binding or type(expected_arm) is not int or expected_arm != self._arm or
                    self._phase != "waiting" or not self.all_ended or self._shortening_attempted or
                    self._pending is not None):
                return None
            if not self._fresh(readback):
                return None
            if now < self._appointment.chosen_at:
                self._timing_unknown = True
                return None
            self._timing_unknown = False
            if self._appointment.due_at - now < timedelta(seconds=90):
                return None
            self._shortening_attempted = True
            return self._intent("shorten", replace(self._reservation, rule=SHORT_RULE))

    def check(self, *, parent_thread_id: str, binding: WaitBinding, expected_arm: int,
              wake_id: str, wake_time: datetime, readback: Reservation | None) -> OperationIntent | None:
        with self._lock:
            _token(wake_id)
            wake_time = _instant(wake_time)
            if (not self._parent(parent_thread_id, binding, expected_arm) or self._phase != "waiting" or
                    self._pending is not None or self._consumed_wake_id is not None or
                    (not self.all_ended and wake_time < self._appointment.due_at)):
                return None
            if not self._fresh(readback):
                return None
            self._consumed_wake_id = wake_id
            self._checked_arm = self._arm
            self._phase = "closed" if self.all_ended else "checking"
            return self._intent("pause", replace(self._reservation, status="PAUSED"))

    def rearm(self, *, parent_thread_id: str, binding: WaitBinding, expected_arm: int,
              appointment: Appointment, readback: Reservation | None, now: datetime,
              healthy: bool) -> OperationIntent | None:
        with self._lock:
            now = _instant(now)
            _require(type(appointment) is Appointment and type(healthy) is bool)
            if (not self._parent(parent_thread_id, binding, expected_arm) or not healthy or self.all_ended or
                    self._phase != "checking" or self._checked_arm != self._arm or not self.stop_confirmed or
                    not appointment.chosen_at <= now < appointment.due_at):
                return None
            if not self._fresh(readback):
                return None
            if appointment.timezone != self._reservation.timezone:
                self._timing_unknown = True
                return None
            return self._intent("rearm", replace(self._reservation, status="ACTIVE", rule=appointment.rule,
                                                  timezone=appointment.timezone), appointment)

    def cancel(self, *, parent_thread_id: str, binding: WaitBinding, expected_arm: int,
               readback: Reservation | None) -> OperationIntent | None:
        with self._lock:
            if not self._parent(parent_thread_id, binding, expected_arm):
                return None
            # This latch may advance while an external effect is outstanding.
            # No competing host effect is emitted until that operation settles.
            self._phase = "closed"
            if self._pending is not None or not self._fresh(readback):
                return None
            if self._reservation.status == "PAUSED":
                self._stop_confirmed = True
                return None
            return self._intent("pause", replace(self._reservation, status="PAUSED"))

    def settle(self, operation_id: str, *, readback: Reservation | None, now: datetime,
               operation_finished: bool = False) -> bool:
        """Resolve only quiescent exact outcomes; unknown never authorizes retry."""
        with self._lock:
            now = _instant(now)
            _require(type(operation_finished) is bool)
            intent = self._pending
            if intent is None or operation_id != intent.operation_id:
                return False
            if (not operation_finished or type(readback) is not Reservation or
                    readback not in (intent.before, intent.after)):
                self._outcome_unknown = True
                return False
            self._pending = None
            self._outcome_unknown = False
            self._timing_unknown = False
            applied = readback == intent.after
            if not applied:
                return True
            self._reservation = readback
            self._stop_confirmed = readback.status == "PAUSED"
            if intent.kind in ("arm", "rearm"):
                self._arm += 1
                self._appointment = intent.appointment
                self._shortening_attempted = False
                self._shortened = False
                self._consumed_wake_id = None
                self._checked_arm = None
                if self._phase != "closed":
                    self._phase = "waiting"
                    if now >= self._appointment.due_at:
                        self._timing_unknown = True
                        self._phase = "closed" if self.all_ended else "checking"
                        self._checked_arm = self._arm
            elif intent.kind == "shorten":
                self._shortened = True
            return True

    def to_snapshot(self) -> dict:
        """Closed bounded data for an explicit repository; contains no secrets."""
        with self._lock:
            return {"version": 1, "binding": asdict(self._binding),
                    "reviewers": [asdict(item) for item in self._reviewers],
                    "reservation": asdict(self._reservation), "arm": self._arm, "phase": self._phase,
                    "appointment": _encode_appointment(self._appointment),
                    "terminal": [{"reviewer_id": item.reviewer_id, "turn_id": item.turn_id,
                                  "status": self._terminal[item.reviewer_id]}
                                 for item in self._reviewers if item.reviewer_id in self._terminal],
                    "shortening_attempted": self._shortening_attempted, "shortened": self._shortened,
                    "timing_unknown": self._timing_unknown, "pending": _encode_intent(self._pending),
                    "outcome_unknown": self._outcome_unknown, "stop_confirmed": self._stop_confirmed,
                    "checked_arm": self._checked_arm, "consumed_wake_id": self._consumed_wake_id,
                    "operation_sequence": self._operation_sequence}

    @classmethod
    def from_snapshot(cls, payload: dict) -> ReviewWaitController:
        """Validate storage data. Outstanding intents remain blocked, not replayed."""
        keys = {"version", "binding", "reviewers", "reservation", "arm", "phase", "appointment", "terminal",
                "shortening_attempted", "shortened", "timing_unknown", "pending", "outcome_unknown",
                "stop_confirmed", "checked_arm", "consumed_wake_id", "operation_sequence"}
        _closed(payload, keys)
        _require(type(payload["version"]) is int and payload["version"] == 1)
        binding = _decode(WaitBinding, payload["binding"])
        reservation = _decode(Reservation, payload["reservation"])
        _require(type(payload["reviewers"]) is list and len(payload["reviewers"]) <= MAX_REVIEWERS)
        reviewers = tuple(_decode(Reviewer, item) for item in payload["reviewers"])
        result = cls(binding, reviewers, replace(reservation, status="PAUSED"))
        result._reservation = reservation
        for name in ("shortening_attempted", "shortened", "timing_unknown", "outcome_unknown", "stop_confirmed"):
            _require(type(payload[name]) is bool)
            setattr(result, "_" + name, payload[name])
        _positive(payload["arm"], zero=True)
        _positive(payload["operation_sequence"], zero=True)
        _require(type(payload["phase"]) is str and payload["phase"] in ("preparing", "waiting", "checking", "closed"))
        result._arm, result._phase = payload["arm"], payload["phase"]
        result._operation_sequence = payload["operation_sequence"]
        result._appointment = _decode_appointment(payload["appointment"])
        result._pending = _decode_intent(payload["pending"])
        checked, wake = payload["checked_arm"], payload["consumed_wake_id"]
        if checked is not None:
            _positive(checked)
            _require(checked == result._arm)
        if wake is not None:
            _token(wake)
            _require(checked is not None)
        result._checked_arm, result._consumed_wake_id = checked, wake
        _require(type(payload["terminal"]) is list and len(payload["terminal"]) <= len(reviewers))
        for terminal in payload["terminal"]:
            _closed(terminal, {"reviewer_id", "turn_id", "status"})
            reviewer = Reviewer(terminal["reviewer_id"], terminal["turn_id"])
            _require(reviewer in reviewers and reviewer.reviewer_id not in result._terminal and
                     type(terminal["status"]) is str and terminal["status"] in ("completed", "failed", "interrupted"))
            result._terminal[reviewer.reviewer_id] = terminal["status"]
        result._validate_state()
        return result

    def _validate_state(self) -> None:
        _require(self._operation_sequence >= self._arm)
        _require((self._arm == 0) == (self._appointment is None))
        _require(self._phase != "preparing" or self._arm == 0)
        _require(self._phase not in ("waiting", "checking") or self._arm > 0)
        _require(self._stop_confirmed == (self._reservation.status == "PAUSED"))
        _require(not self._shortened or (self._shortening_attempted and self.all_ended))
        _require(not self._shortening_attempted or (self.all_ended and self._arm > 0))
        _require(not self._outcome_unknown or self._pending is not None)
        if self._arm == 0:
            _require(self._reservation.status == "PAUSED" and self._checked_arm is None)
        else:
            _require(self._reservation.timezone == self._appointment.timezone)
            _require(self._reservation.rule == (SHORT_RULE if self._shortened else self._appointment.rule))
        if self._phase == "waiting":
            _require(self._reservation.status == "ACTIVE" and self._checked_arm is None)
        if self._phase == "checking":
            _require(not self.all_ended and self._checked_arm == self._arm)
        if self._pending is not None:
            intent = self._pending
            _require(intent.before == self._reservation and intent.expected_arm == self._arm)
            _require(intent.operation_id == f"{self._binding.wait_id}:{self._operation_sequence}")
            if intent.kind == "arm":
                _require(self._arm == 0 and self._phase in ("preparing", "closed"))
            elif intent.kind == "rearm":
                _require(self._arm > 0 and self._phase in ("checking", "closed") and self._checked_arm == self._arm)
            elif intent.kind == "shorten":
                _require(self._shortening_attempted and not self._shortened and self._phase in ("waiting", "closed"))
            else:
                _require(self._phase in ("checking", "closed"))


def _closed(value: object, keys: set[str]) -> None:
    _require(type(value) is dict and set(value) == keys)


def _decode(kind: type, value: object):
    _closed(value, {field.name for field in fields(kind)})
    try:
        return kind(**value)
    except (TypeError, ValueError, OverflowError):
        raise ControllerError() from None


def _encode_appointment(value: Appointment | None) -> dict | None:
    if value is None:
        return None
    return {**asdict(value), "chosen_at": value.chosen_at.isoformat(), "due_at": value.due_at.isoformat()}


def _decode_appointment(value: object) -> Appointment | None:
    if value is None:
        return None
    _closed(value, {field.name for field in fields(Appointment)})
    try:
        _require(type(value["chosen_at"]) is str and len(value["chosen_at"]) <= 40 and
                 type(value["due_at"]) is str and len(value["due_at"]) <= 40)
        return Appointment(datetime.fromisoformat(value["chosen_at"]), datetime.fromisoformat(value["due_at"]),
                           value["rule"], value["timezone"])
    except (TypeError, ValueError, OverflowError):
        raise ControllerError() from None


def _encode_intent(value: OperationIntent | None) -> dict | None:
    return None if value is None else {**asdict(value), "appointment": _encode_appointment(value.appointment)}


def _decode_intent(value: object) -> OperationIntent | None:
    if value is None:
        return None
    _closed(value, {field.name for field in fields(OperationIntent)})
    return OperationIntent(value["operation_id"], value["kind"], value["expected_arm"],
                           _decode(Reservation, value["before"]), _decode(Reservation, value["after"]),
                           _decode_appointment(value["appointment"]))
