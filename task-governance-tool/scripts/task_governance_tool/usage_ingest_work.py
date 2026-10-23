"""One event's bounded ingest/audit allowance, shared by discovery and locators."""

from collections import deque

from task_governance_tool.usage_values import UsageError


class IngestWork:
    def __init__(self, repository, known_sources, thread, deadline, *, limit, current_limit, clock):
        self.repository = repository
        self.known_sources = set(known_sources)
        self.thread = thread
        self.deadline = deadline
        self.limit = limit
        self.current_limit = current_limit
        self.clock = clock
        self.slots = self.current_slots = 0
        self.attempted = set()
        self.successful = set()
        self.repeatable = set()
        self.audited = set()
        self.gaps = set()

    def run(self, sources, identities, hint, *, ceiling=None, before_attempt=None):
        """Use an already bounded, ordered candidate page; never select all history."""
        ceiling = self.limit if ceiling is None else min(self.limit, ceiling)

        def collect(identity):
            if self.slots >= ceiling or self.clock() >= self.deadline:
                self.gaps.add("usage_pending")
                return False
            source = sources[identity]
            if identity not in self.attempted and before_attempt is not None:
                if not before_attempt(identity):
                    return True
            if self.clock() >= self.deadline:
                self.gaps.add("usage_pending")
                return False
            self.slots += 1
            self.current_slots += int(source.thread_id == self.thread)
            self.attempted.add(identity)
            self.repeatable.discard(identity)
            try:
                replay = False
                if identity in self.known_sources and identity not in self.audited:
                    audit = self.repository.collect_source(source, lane="audit")
                    self.audited.add(identity)
                    replay = audit.get("replay", False)
                result = self.repository.collect_source(source, record_attempt=True)
                self.successful.add(identity)
                if result.get("replay"):
                    self.repository.attempted(identity)
                if (not replay and not result.get("replay")
                        and result["pending"] == "more_records" and result["progressed"]):
                    self.repeatable.add(identity)
            except UsageError as error:
                self.gaps.add(error.code)
                if error.code != "usage_pending":
                    self.repository.attempted(identity)
                    if identity in self.known_sources:
                        self.repository.record_gap(identity, source.thread_id, error.code)
            return True

        remaining = [identity for identity in identities if identity not in self.attempted]
        if remaining:
            collect(remaining[0])
        current = [key for key in remaining if sources[key].thread_id == self.thread]
        if hint is not None and hint.source_id in current:
            current.remove(hint.source_id)
            current.insert(0, hint.source_id)
        queue = deque(current)
        while queue and self.current_slots < self.current_limit:
            identity = queue.popleft()
            if identity in self.attempted and identity not in self.repeatable:
                continue
            if not collect(identity):
                break
            if identity in self.repeatable:
                queue.append(identity)
        for identity in remaining:
            if identity not in self.attempted and not collect(identity):
                break
