"""Incremental unknown/unassigned model totals; nullable counters stay unknown."""

from .usage_evidence import digest, encode
from .usage_reconciliation_core import _decode
from .usage_repository import _response
from .usage_values import METRICS, UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_unattributed_keys (
        provider TEXT NOT NULL,response_id TEXT NOT NULL,kind TEXT NOT NULL,document TEXT NOT NULL,digest TEXT NOT NULL,
        PRIMARY KEY(provider,response_id))""",
    """CREATE TABLE usage_reconcile_unattributed_models (
        kind TEXT NOT NULL,model_key TEXT NOT NULL,document TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(kind,model_key))""",
)


def _apply(connection, kind, value, sign):
    key = encode([value["provider"], value["model"]]).decode()
    old = _decode(connection.execute("SELECT document,digest FROM usage_reconcile_unattributed_models WHERE kind=? AND model_key=?", (kind, key)).fetchone())
    aggregate = old or {"provider": value["provider"], "model": value["model"], "response_count": 0,
                        "sums": [0] * len(METRICS), "missing": [0] * len(METRICS)}
    aggregate["response_count"] += sign
    for ordinal, count in enumerate(value["counts"]):
        aggregate["sums"][ordinal] += sign * (count or 0)
        aggregate["missing"][ordinal] += sign * int(count is None)
    if (aggregate["response_count"] < 0 or any(item < 0 for item in aggregate["sums"] + aggregate["missing"])
            or any(item > aggregate["response_count"] for item in aggregate["missing"])):
        raise UsageError("usage_schema_invalid")
    if aggregate["response_count"] == 0:
        if any(aggregate["sums"] + aggregate["missing"]):
            raise UsageError("usage_schema_invalid")
        connection.execute("DELETE FROM usage_reconcile_unattributed_models WHERE kind=? AND model_key=?", (kind, key))
    else:
        connection.execute("INSERT INTO usage_reconcile_unattributed_models VALUES (?,?,?,?) ON CONFLICT(kind,model_key) "
            "DO UPDATE SET document=excluded.document,digest=excluded.digest", (kind, key, encode(aggregate).decode(), digest(aggregate)))


def reconcile(connection, provider, response):
    row = connection.execute("SELECT * FROM usage_reconcile_unattributed_keys WHERE provider=? AND response_id=?", (provider, response)).fetchone()
    before = _decode(row)
    current = connection.execute("SELECT response.*,result.unknown FROM usage_reconcile_response_result result "
        "JOIN usage_responses response USING(provider,response_id) WHERE result.provider=? AND result.response_id=? "
        "AND (result.unknown=1 OR result.component_id IS NULL)", (provider, response)).fetchone()
    after = None
    if current is not None:
        original = _response(current)
        after = {"provider": original.provider, "model": original.model, "counts": list(original.counts)}
    kind = "unknown" if current is not None and current["unknown"] else "unassigned"
    if before == after and (before is None or kind == row["kind"]):
        return
    if before is not None:
        _apply(connection, row["kind"], before, -1)
    connection.execute("DELETE FROM usage_reconcile_unattributed_keys WHERE provider=? AND response_id=?", (provider, response))
    if after is not None:
        _apply(connection, kind, after, 1)
        connection.execute("INSERT INTO usage_reconcile_unattributed_keys VALUES (?,?,?,?,?)",
                           (provider, response, kind, encode(after).decode(), digest(after)))


def totals(connection):
    result = {kind: {"response_count": 0, "models": []} for kind in ("unknown", "unassigned")}
    for row in connection.execute("SELECT * FROM usage_reconcile_unattributed_models ORDER BY kind,model_key"):
        value = _decode(row)
        if row["kind"] not in result or row["model_key"] != encode([value["provider"], value["model"]]).decode():
            raise UsageError("usage_schema_invalid")
        model = {key: value[key] for key in ("provider", "model", "response_count")}
        model.update({metric: None if value["missing"][i] else value["sums"][i] for i, metric in enumerate(METRICS)})
        result[row["kind"]]["models"].append(model)
        result[row["kind"]]["response_count"] += value["response_count"]
    # Match the original aggregation order, including unknown model names.
    for value in result.values():
        value["models"].sort(key=lambda item: (item["provider"], item["model"] or ""))
    return result
