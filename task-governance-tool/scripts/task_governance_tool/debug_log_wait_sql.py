"""Finite already-observed SQLite context for wait failures, never lock proof."""
import json

# Charged against the wait reservation, not additional operation capacity.
CONTEXT_BYTES = 16 * 1024
CONNECTIONS = 8


class WaitSqlContext:
    def __init__(self, emit, drop):
        self.emit, self.drop = emit, drop
        self.connections = {}
        self.sizes = {}
        self.size = 0

    def observe(self, record, identity):
        """Only Collector-projected records enter this bounded cache."""
        connection_id = record["connection_id"]
        phase, outcome = record["phase"], record["outcome"]
        failed = outcome in {"failed", "cancelled"}
        current = self.connections.get(connection_id)
        if current is None:
            if len(self.connections) >= CONNECTIONS:
                self.drop("db_context_limit")
                if failed:
                    self.emit("wait.db_failure", {**identity, "failure": record,
                        "context": None, "coverage": "partial", "contender": "unknown"},
                        failed=True, important=True)
                return
            current = {"open": None, "begin": None, "failure": None, "end": None, "close": None,
                       "failure_identity": None}
        else:
            current = dict(current)
        if phase == "open":
            current["open"] = record
        elif phase in {"begin", "implicit_begin"} and current["failure"] is None:
            # A nested failed BEGIN is not the start of the active transaction.
            if outcome in {"succeeded", "observed"} and record.get("lifetime") == "observed":
                current["begin"] = record
                current["end"] = None
        elif phase == "transaction_end":
            if current["failure"] is None:
                current["begin"] = None
            elif (record.get("transaction_id") is not None
                    and record.get("transaction_id") == current["failure"].get("transaction_id")
                    and record.get("lifetime") == "observed" and current["end"] is None):
                current["end"] = record
        elif phase == "close":
            current["close"] = record
        first_failure = failed and current["failure"] is None
        if first_failure:
            current["failure"] = record
            current["failure_identity"] = dict(identity)
            begin = current["begin"]
            if (begin is not None and (record.get("transaction_id") != begin.get("transaction_id")
                    or record.get("lifetime") != "observed")):
                current["begin"] = None
            current["end"] = None
        elif current["failure"] is None and record.get("lifetime") == "unknown":
            # Script/context internals can invalidate a formerly observed start.
            current["begin"] = None
        size = len(json.dumps(current, separators=(",", ":")).encode())
        previous_size = self.sizes.get(connection_id, 0)
        if self.size - previous_size + size > CONTEXT_BYTES:
            self.drop("db_context_limit")
            if failed:
                self.emit("wait.db_failure", {**identity, "failure": record,
                    "context": None, "coverage": "partial", "contender": "unknown"},
                    failed=True, important=True)
        else:
            self.connections[connection_id] = current
            self.sizes[connection_id] = size
            self.size += size - previous_size
            if first_failure:
                self._publish(current)

    def _publish(self, current):
        self.emit("wait.db_failure", {**current["failure_identity"], "failure": current["failure"],
            "context": {key: value for key, value in current.items() if key != "failure_identity"},
            "coverage": "partial", "contender": "unknown"}, failed=True, important=True)

    def closed(self, connection_id):
        """Collector confirms native close after every observed trailing event."""
        current = self.connections.pop(connection_id, None)
        self.size -= self.sizes.pop(connection_id, 0)
        if current is not None and current["failure"] is not None and current["close"] is not None:
            self._publish(current)
