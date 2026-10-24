"""Numerical schema 9: indexed attempt watermarks, without rewriting history."""

from task_governance_tool.usage_locator_repository import UsageLocatorRepository


_MIGRATION = """CREATE TABLE usage_migrations (
        version INTEGER PRIMARY KEY CHECK(version IN (1,2,3,4,5,6,7,8,9)),
        name TEXT NOT NULL CHECK((version=1 AND name='response_collection') OR
          (version=2 AND name='turn_attribution') OR (version=3 AND name='immutable_usage_evidence') OR
          (version=4 AND name='review_wait_attribution') OR (version=5 AND name='incremental_collection') OR
          (version=6 AND name='response_attribution') OR (version=7 AND name='progressive_publication') OR
          (version=8 AND name='source_locators') OR (version=9 AND name='indexed_work_order')))"""
_ATTEMPT_INDEX = "CREATE INDEX usage_work_order_by_attempt ON usage_work_order(last_attempt)"


class UsageWorkOrderRepository(UsageLocatorRepository):
    schema_statements = (_MIGRATION, *UsageLocatorRepository.schema_statements[1:], _ATTEMPT_INDEX)
    migrations = (*UsageLocatorRepository.migrations, (9, "indexed_work_order"))
    _previous_repositories = (UsageLocatorRepository, *UsageLocatorRepository._previous_repositories)

    def _after_numerical_upgrade(self, connection, previous):
        # Schema 8 already has current continuations. Earlier versions retain
        # their exact predecessor's migration policy and replay requirement.
        if previous is not UsageLocatorRepository:
            super()._after_numerical_upgrade(connection, previous)
        self._before_work_order_marker(connection)

    def _before_work_order_marker(self, connection):
        """Fault seam before committing the index and complete migration rows."""
