"""Fresh structural watermarks fence resumable classification and publication."""

from .usage_evidence import digest, encode
from .usage_reconciliation_core import cursor, revision, _decode
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_identity (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),document TEXT NOT NULL,digest TEXT NOT NULL,
        observed_generation INTEGER NOT NULL CHECK(observed_generation>=0))""",
    """CREATE TABLE usage_reconcile_input_basis (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),document TEXT NOT NULL,digest TEXT NOT NULL)""",
    "CREATE TABLE usage_reconcile_global_progress (kind TEXT PRIMARY KEY,position TEXT NOT NULL)",
)
INITIAL_QUEUES = (
    "changes", "source_epochs", "source_work", "core_work", "task_work", "participant_work",
    "interval_turn_work", "relation_task_work", "input_turn_work", "turn_queue", "turn_effects", "base_key_work",
)
EFFECT_QUEUES = (
    "event_work", "pending_work", "rule_work", "interval_work", "range_work", "overlay_turn_work",
    "response_work", "diagnostic_work", "execution_work", "global_work",
)


def numerical_identity(repository, connection):
    from .usage_response_attribution import ALGORITHM
    stat = repository.path.stat(follow_symlinks=False)
    if not stat.st_ino:
        raise UsageError("usage_schema_invalid")
    observed = repository.reconciliation_revision(connection)
    return {"basis": list(repository.basis), "epoch": observed.epoch, "schema": repository.migrations[-1][0],
            "algorithm": ALGORITHM, "file": [stat.st_dev, stat.st_ino]}, observed.generation


def admit_identity(repository, connection):
    identity, generation = numerical_identity(repository, connection)
    row = connection.execute("SELECT * FROM usage_reconcile_identity WHERE singleton=1").fetchone()
    previous = _decode(row)
    reset = previous != identity or generation < row["observed_generation"]
    if reset:
        repository._reset_reconciliation(connection)
    if reset or row["observed_generation"] != generation:
        connection.execute("INSERT INTO usage_reconcile_identity VALUES (1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
            "document=excluded.document,digest=excluded.digest,observed_generation=excluded.observed_generation",
            (encode(identity).decode(), digest(identity), generation))
    return reset


def pending(connection, queues):
    return any(connection.execute("SELECT 1 FROM usage_reconcile_" + table + " LIMIT 1").fetchone() for table in queues)


def current_inputs(repository, core, connection):
    current, consumed = revision(core, repository.basis[0]), cursor(connection)
    identity, generation = numerical_identity(repository, connection)
    admitted = connection.execute("SELECT * FROM usage_reconcile_identity WHERE singleton=1").fetchone()
    baseline = connection.execute("SELECT complete FROM usage_reconcile_baseline WHERE singleton=1").fetchone()
    if (consumed is None or not consumed["complete"] or consumed["identity"] != current.identity()
            or consumed["through"] != current.generation or _decode(admitted) != identity
            or admitted["observed_generation"] != generation or baseline is None or not baseline[0]
            or pending(connection, INITIAL_QUEUES)):
        raise UsageError("usage_pending")
    return {"core": [current.identity(), current.generation], "numerical": [identity, generation]}


def capture_inputs(repository, core, connection):
    value = current_inputs(repository, core, connection)
    connection.execute("INSERT INTO usage_reconcile_input_basis VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET "
        "document=excluded.document,digest=excluded.digest", (encode(value).decode(), digest(value)))
    return value


def require_inputs(repository, core, connection, *, classified=False):
    value = current_inputs(repository, core, connection)
    captured = _decode(connection.execute("SELECT document,digest FROM usage_reconcile_input_basis WHERE singleton=1").fetchone())
    if captured != value or (classified and pending(connection, EFFECT_QUEUES)):
        raise UsageError("usage_pending")
    return value


def global_page(connection, *, limit=128):
    work = connection.execute("SELECT kind FROM usage_reconcile_global_work LIMIT 1").fetchone()
    if work is None:
        return None
    position = connection.execute("SELECT position FROM usage_reconcile_global_progress WHERE kind=?", (work[0],)).fetchone()
    rows = tuple(connection.execute("SELECT component_id FROM usage_reconcile_components WHERE component_id>? ORDER BY component_id LIMIT ?",
                                    (position[0] if position else "", limit)))
    connection.executemany("INSERT OR IGNORE INTO usage_reconcile_component_work VALUES (?)", rows)
    if len(rows) < limit:
        connection.execute("DELETE FROM usage_reconcile_global_work WHERE kind=?", (work[0],))
        connection.execute("DELETE FROM usage_reconcile_global_progress WHERE kind=?", (work[0],))
    else:
        connection.execute("INSERT INTO usage_reconcile_global_progress VALUES (?,?) ON CONFLICT(kind) DO UPDATE SET position=excluded.position",
                           (work[0], rows[-1][0]))
    return len(rows)
