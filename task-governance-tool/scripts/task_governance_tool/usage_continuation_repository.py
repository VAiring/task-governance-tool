"""Repository reads for finite source traversal, never attempt-as-success.

Existing schema-10 queues and numerical cursors remain authoritative. Runtime
checkpoints only resume a bounded scan; each source is freshly admitted and
read by the ordinary collector. No SQL escapes this repository boundary.
"""

from contextlib import nullcontext

from .usage_incremental_repository import _discovery
from .usage_values import UsageError
from .usage_reconciliation_basis import numerical_identity
from .usage_evidence import digest


def checkpoint(repository, round_, *, connection=None):
    with repository.connection() if connection is None else nullcontext(connection) as connection:
        identity, generation = numerical_identity(repository, connection)
    if digest(identity) != round_["identity"] or generation < round_["generation"]:
        raise UsageError("cursor_stale")
    return {**round_, "generation": generation}


def begin(repository, *, connection=None):
    with repository.connection() if connection is None else nullcontext(connection) as connection:
        epoch = repository.reconciliation_revision(connection).epoch
        identity, generation = numerical_identity(repository, connection)
        _, position = _discovery(connection.execute("SELECT * FROM usage_discovery").fetchone())
        return dict(epoch=epoch, identity=digest(identity), generation=generation,
                    inventory_cycle=position["cycle"], phase="intake",
                    after_order=0, after_source="", through=0, reason="pending")


def inventory_needed(repository, round_, *, connection=None):
    with repository.connection() if connection is None else nullcontext(connection) as connection:
        checkpoint(repository, round_, connection=connection)
        if repository.reconciliation_revision(connection).epoch != round_["epoch"]:
            raise UsageError("cursor_stale")
        _, position = _discovery(connection.execute("SELECT * FROM usage_discovery").fetchone())
        return position["cycle"] <= round_["inventory_cycle"]


def source_round(repository, round_):
    with repository.connection() as connection:
        through = repository._sequence_value(connection)
    return {**round_, "phase": "sources", "through": through,
            "after_order": 0, "after_source": "", "reason": "pending"}


def sources(repository, round_, roots, *, limit=8):
    selected = []
    with repository.connection() as connection:
        if repository.reconciliation_revision(connection).epoch != round_["epoch"]:
            raise UsageError("cursor_stale")
        for kind, fingerprint in roots.items():
            selected.extend(repository._read_locator(row) for row in connection.execute(
                "SELECT * FROM usage_locators WHERE root_kind=? AND root_fingerprint=? "
                "AND (queue_order,source_id)>(?,?) AND queue_order<=? ORDER BY queue_order,source_id LIMIT ?",
                (kind, fingerprint, round_["after_order"], round_["after_source"], round_["through"], limit)))
    return sorted(selected, key=lambda item: (item.queue_order, item.locator.source_id))[:limit]


def added_sources(repository, round_, roots):
    with repository.connection() as connection:
        for kind, fingerprint in roots.items():
            if connection.execute("SELECT 1 FROM usage_locators WHERE root_kind=? AND root_fingerprint=? "
                    "AND queue_order>? LIMIT 1", (kind, fingerprint, round_["through"])).fetchone():
                return True
    return False


def waiting(repository, roots, *, connection=None):
    """External input/registration limits are not successful processing."""
    with repository.connection() if connection is None else nullcontext(connection) as connection:
        if connection.execute("SELECT 1 FROM usage_pending_locators LIMIT 1").fetchone():
            return "waiting_review"
        # Publication may admit Core participants after directory discovery.
        # Reconstruct missing-input diagnosis from that full admitted set even
        # when an earlier post-discovery diagnostic was interrupted.
        if connection.execute("SELECT 1 FROM usage_reconcile_core_participants participant WHERE NOT EXISTS "
                "(SELECT 1 FROM usage_sources source WHERE source.thread_id=participant.session_id) LIMIT 1").fetchone():
            return "waiting_input"
        if connection.execute("SELECT 1 FROM usage_sessions session WHERE NOT EXISTS "
                "(SELECT 1 FROM usage_sources source WHERE source.thread_id=session.thread_id) LIMIT 1").fetchone():
            return "waiting_input"
        if connection.execute("SELECT 1 FROM usage_sources source WHERE NOT EXISTS (SELECT 1 FROM usage_locators locator "
                "WHERE locator.source_id=source.source_id AND ((locator.root_kind='sessions' AND locator.root_fingerprint=?) "
                "OR (locator.root_kind='archive' AND locator.root_fingerprint=?))) LIMIT 1",
                (roots["sessions"], roots["archive"])).fetchone():
            return "waiting_input"
    return None


def completion(repository, round_, roots, intakes, connection):
    """Read completion facts only inside the caller's fresh publication guard."""
    status = waiting(repository, roots, connection=connection)
    inventory = inventory_needed(repository, round_, connection=connection)
    if status is None:
        status = "waiting_input" if intakes else round_["reason"]
        if status == "pending":
            status = "pending" if inventory else "caught_up"
    if status != "caught_up":
        if inventory:
            round_ = {**round_, "phase": "inventory" if status == "pending" else "intake"}
        else:
            round_ = begin(repository, connection=connection)
    return status, checkpoint(repository, round_, connection=connection)
