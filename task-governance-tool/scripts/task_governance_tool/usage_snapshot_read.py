"""Transaction-local immutable snapshot inputs and overlap indexes.

The owning repository creates this value inside one numerical transaction and
passes it explicitly to capture/display consumers. It is never a repository
cache, publication proof, or input to a later connection.
"""

from collections import defaultdict
from itertools import batched

from task_governance_tool.usage_evidence import encode, RESPONSE_FORMAT
from task_governance_tool.usage_values import UsageError


# Admitted keys contain ASCII provider labels (120) and response IDs (200).
# This bounds each internal JSON request well below the 16 MiB document limit,
# without imposing that per-document limit on the union of valid snapshots.
_READ_BATCH_SIZE = 4096


class SnapshotRead:
    def __init__(self, repository, connection, rows, documents):
        self.repository, self.connection = repository, connection
        self.bodies = documents
        self.check()
        self.members = {row["snapshot_id"]: [] for row in rows}
        self.predecessors = {identity: [] for identity in self.members}
        for batch in batched(self.members, _READ_BATCH_SIZE):
            self.check()
            identities = encode(batch).decode()
            for row in connection.execute(
                "SELECT snapshot_id,provider,response_id FROM usage_snapshot_members "
                "WHERE snapshot_id IN (SELECT value FROM json_each(?)) ORDER BY provider,response_id",
                (identities,),
            ):
                self.members[row["snapshot_id"]].append((row["provider"], row["response_id"]))
            for row in connection.execute(
                "SELECT successor_id,predecessor_id FROM usage_supersessions "
                "WHERE successor_id IN (SELECT value FROM json_each(?)) ORDER BY predecessor_id", (identities,),
            ):
                self.predecessors[row["successor_id"]].append(row["predecessor_id"])
        self._responses = {}
        self.responses({key for keys in self.members.values() for key in keys})
        self.documents = [repository._snapshot(connection, row, read=self) for row in rows]
        self.by_id = {item["snapshot_id"]: item for item in self.documents}
        self.order = {identity: i for i, identity in enumerate(self.by_id)}
        self.by_execution, self.by_response = defaultdict(set), defaultdict(set)
        self.by_partition, self.retired = defaultdict(set), set()
        self.legacy, self.empty = set(), set()
        for item in self.documents:
            self.check()
            identity = item["snapshot_id"]
            for execution in item["executions"]:
                self.by_execution[execution].add(identity)
            for key in self.members[identity]:
                self.by_response[key].add(identity)
            if item["format"] != RESPONSE_FORMAT:
                self.legacy.add(identity)
            if not item["response_count"]:
                self.empty.add(identity)

    def responses(self, keys):
        self.check()
        keys = set(map(tuple, keys))
        missing = keys - self._responses.keys()
        for batch in batched(missing, _READ_BATCH_SIZE):
            self.check()
            for item in self.repository._responses(self.connection, batch):
                self._responses[(item.provider, item.response_id)] = item
        return [self._responses[key] for key in sorted(keys)]

    def check(self):
        if not self.connection.in_transaction:
            raise UsageError()
        check = getattr(self.repository, "check_budget", None)
        if check is not None:
            check()

    def execution_ids(self, executions):
        return set().union(*(self.by_execution.get(item, set()) for item in executions))

    def ordered(self, identities):
        return [self.by_id[identity] for identity in sorted(identities, key=self.order.__getitem__)]

    def prepare_partitions(self, component_identity, selected_keys):
        for item in self.documents:
            self.check()
            identity = item["snapshot_id"]
            self.by_partition[component_identity(item)].add(identity)
            if item["response_count"] and not selected_keys.intersection(self.members[identity]):
                self.retired.add(identity)

    def partition_overlaps(self, component, component_identity):
        same_execution = self.execution_ids(component["executions"])
        identities = same_execution & (self.legacy | self.retired | self.empty)
        if not component["response_keys"]:
            identities.update(same_execution)
        identities.update(self.by_partition.get(component_identity(component), set()))
        for key in component["response_keys"]:
            identities.update(self.by_response.get(tuple(key), set()) - self.legacy)
        return self.ordered(identities)
