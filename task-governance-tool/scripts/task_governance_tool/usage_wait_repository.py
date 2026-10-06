"""Numerical schema 4 and immutable core anchors for review-wait decisions.

Only explicit setup migrates. Helper capture and replay use admitted core reads;
neither creates a Receipt nor writes core Task state or a timer.
"""

from dataclasses import asdict

from task_governance_tool.usage_repository import UsageRepository, _DDL
from task_governance_tool.usage_attribution_repository import UsageAttributionRepository, _ATTRIBUTION_DDL
from task_governance_tool.usage_evidence_repository import UsageEvidenceRepository, _EVIDENCE_DDL
from task_governance_tool.usage_evidence import digest
from task_governance_tool.usage_wait_attribution import WaitObservation, binding_from_metadata, wait_intervals
from task_governance_tool.usage_values import UsageError


_MIGRATION = """CREATE TABLE usage_migrations (
        version INTEGER PRIMARY KEY CHECK(version IN (1,2,3,4)),
        name TEXT NOT NULL CHECK((version=1 AND name='response_collection') OR
          (version=2 AND name='turn_attribution') OR (version=3 AND name='immutable_usage_evidence') OR
          (version=4 AND name='review_wait_attribution')))"""
_WAIT_DDL = ("""CREATE TABLE usage_wait_turns (
        thread_id TEXT NOT NULL REFERENCES usage_sessions(thread_id), turn_id TEXT NOT NULL,
        project_id TEXT NOT NULL, task_id TEXT NOT NULL, execution_id TEXT NOT NULL,
        contract_revision INTEGER NOT NULL CHECK(contract_revision >= 0),
        target_kind TEXT NOT NULL, target_value TEXT NOT NULL, target_base_revision TEXT NOT NULL,
        target_generation INTEGER NOT NULL CHECK(target_generation > 0), artifact_manifest_id TEXT NOT NULL,
        PRIMARY KEY(thread_id,turn_id,project_id,task_id,execution_id,contract_revision,
                    target_kind,target_value,target_base_revision,target_generation,artifact_manifest_id))""",)


def wait_anchor(core, observation):
    """Exact historical capture and execution, never the newest Task pointers.

    The helper validated their association while current. Both immutable IDs
    must still exist after restore; equal target text/generation is insufficient.
    The admitted core reader owns full stored-Evidence validation.
    """
    rows = core.execute("""
        SELECT m.artifact_manifest_id, m.digest, m.authority_snapshot_id,
               m.acceptance_criterion_id, m.verification_criterion_id,
               r.evidence_reference_id, r.digest AS reference_digest
        FROM artifact_manifests m
        JOIN authority_snapshots s ON s.authority_snapshot_id=m.authority_snapshot_id
          AND s.project_id=m.project_id AND s.task_id=m.task_id
        JOIN task_executions x ON x.execution_id=? AND x.project_id=m.project_id AND x.task_id=m.task_id
        JOIN evidence_references r ON r.source_kind='artifact_manifest' AND r.source_state=m.state
          AND r.source_id=m.artifact_manifest_id AND r.project_id=m.project_id AND r.task_id=m.task_id
          AND r.contract_revision=s.contract_revision AND r.authority_snapshot_id=m.authority_snapshot_id
          AND r.acceptance_criterion_id IS m.acceptance_criterion_id
          AND r.verification_criterion_id IS m.verification_criterion_id
          AND r.target_kind=m.target_kind AND r.target_value=m.target_value
          AND r.target_base_revision=m.target_base_revision AND r.target_generation=m.target_generation
        WHERE m.artifact_manifest_id=? AND m.project_id=? AND m.task_id=?
          AND s.contract_revision=? AND m.target_kind=? AND m.target_value=?
          AND m.target_base_revision=? AND m.target_generation=? LIMIT 2
        """, (observation.execution_id, observation.artifact_manifest_id,
              observation.project_id, observation.task_id, observation.contract_revision,
              observation.target_kind, observation.target_value, observation.target_base_revision,
              observation.target_generation)).fetchall()
    return tuple(rows[0]) if len(rows) == 1 else None


def capture_wait_metadata(core, *, project_id, packet, caller):
    """Read one current Packet association and capture its immutable manifest ID.

    The caller supplies one admitted read transaction and the complete validated
    Packet. Sender identity is captured by the helper, never entered by an LLM.
    """
    from task_governance_tool.tasks import read_internal_task
    from task_governance_tool.task_ownership import read_basis
    from task_governance_tool.contracts import read_current_contract
    try:
        task_id, context = packet["task"]["task_id"], packet["review_session_context"]
        current = read_internal_task(core, project_id, task_id)
        ownership = read_basis(core, project_id=project_id, task_id=task_id)
        target = packet["review_target"]
        contract = read_current_contract(core, project_id=project_id, task_id=task_id,
                                        current_revision=current["current_contract_revision"])
        expected_contract = {key: contract[key] for key in
                             ("revision", "scope", "acceptance", "constraints", "authority_ref")}
        # Preserve the existing handoff compatibility with old packets whose
        # Contract display predates the authority-reference field.
        if "authority_ref" not in packet["contract"]:
            expected_contract.pop("authority_ref")
        if (current is None or context != {"version": 1, "project_id": project_id,
                                           "execution_id": ownership.execution_id}
                or ownership.state not in ("owned", "completion_only")
                or ownership.execution_id is None or current["review_target_capture_version"] != 1
                or expected_contract != packet["contract"]
                or any(packet["task"].get(key) != current[key] for key in
                       ("title", "verification", "verification_not_required_reason", "review_tier"))
                or target != {"kind": current["review_target_kind"], "value": current["review_target_value"],
                              "base_revision": current["review_target_base_revision"],
                              "generation": current["review_target_generation"]}):
            raise UsageError("boundary_unknown")
        metadata = {"version": 1, "session_id": caller.require(), "project_id": project_id, "task_id": task_id,
                    "execution_id": ownership.execution_id, "contract_revision": packet["contract"]["revision"],
                    "review_target": dict(target), "artifact_manifest_id": current["review_target_artifact_manifest_id"]}
        if wait_anchor(core, binding_from_metadata(metadata)) is None:
            raise UsageError("boundary_unknown")
        return metadata
    except UsageError:
        raise
    except Exception:
        raise UsageError("boundary_unknown") from None


class UsageWaitRepository(UsageEvidenceRepository):
    schema_statements = (_MIGRATION, *_DDL[1:], *_ATTRIBUTION_DDL, *_EVIDENCE_DDL, *_WAIT_DDL)
    migrations = (*UsageEvidenceRepository.migrations, (4, "review_wait_attribution"))

    def inspect(self):
        try:
            return UsageRepository.inspect(self)
        except UsageError as error:
            if error.code != "usage_schema_invalid":
                raise
        for previous in (UsageEvidenceRepository, UsageAttributionRepository, UsageRepository):
            try:
                with previous(self.path, *self.basis).connection():
                    return "migration_required"
            except UsageError as error:
                if error.code != "usage_schema_invalid":
                    raise
        raise UsageError("usage_schema_invalid")

    def initialize(self):
        if self.inspect() != "migration_required":
            return UsageRepository.initialize(self)
        for previous in (UsageEvidenceRepository, UsageAttributionRepository, UsageRepository):
            old = previous(self.path, *self.basis)
            try:
                with old.connection(write=True) as connection:
                    if (connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                            or connection.execute("PRAGMA foreign_key_check").fetchone()):
                        raise UsageError()
                    connection.execute("ALTER TABLE usage_migrations RENAME TO usage_migrations_old")
                    connection.execute(_MIGRATION)
                    connection.execute("INSERT INTO usage_migrations SELECT * FROM usage_migrations_old")
                    connection.execute("DROP TABLE usage_migrations_old")
                    if previous is UsageRepository:
                        for statement in _ATTRIBUTION_DDL:
                            connection.execute(statement)
                        connection.execute("INSERT INTO usage_migrations VALUES (2,'turn_attribution')")
                    if previous is not UsageEvidenceRepository:
                        for statement in _EVIDENCE_DDL:
                            connection.execute(statement)
                        connection.execute("INSERT INTO usage_migrations VALUES (3,'immutable_usage_evidence')")
                    for statement in _WAIT_DDL:
                        connection.execute(statement)
                    self._before_wait_marker(connection)
                    connection.execute("INSERT INTO usage_migrations VALUES (4,'review_wait_attribution')")
                    self._validate(connection)
                return "migrated"
            except UsageError as error:
                if error.code != "usage_schema_invalid":
                    raise
        raise UsageError("usage_schema_invalid")

    def _before_wait_marker(self, connection):
        """Fault-injection seam before the setup-only migration commits."""

    def _record_wait(self, connection, observation):
        if observation.project_id != self.basis[0]:
            raise UsageError("boundary_unknown")
        connection.execute("INSERT OR IGNORE INTO usage_wait_turns VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           tuple(getattr(observation, field) for field in (
                               "thread_id", "turn_id", "project_id", "task_id", "execution_id", "contract_revision",
                               "target_kind", "target_value", "target_base_revision", "target_generation", "artifact_manifest_id")))

    def _wait_observations(self, connection):
        return tuple(WaitObservation(**dict(row)) for row in connection.execute(
            "SELECT * FROM usage_wait_turns ORDER BY execution_id,artifact_manifest_id,thread_id,turn_id,"
            "project_id,task_id,contract_revision,target_kind,target_value,target_base_revision,target_generation"))

    def _additional_intervals(self, core, connection, owners, turns, conflicts):
        observations = self._wait_observations(connection)
        valid = frozenset(item for item in observations if item.project_id == self.basis[0]
                          and wait_anchor(core, item) is not None)
        return wait_intervals(owners, turns, observations, valid, conflicting_turns=conflicts)

    def _core_basis(self, core, connection):
        basis, periods = super()._core_basis(core, connection)
        anchors = [(asdict(item), wait_anchor(core, item)) for item in self._wait_observations(connection)]
        return digest({"core": basis, "wait_anchors": anchors}), periods
