# Database Persistence And Migration Section Split Capture

> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves only the database persistence and migration sections
> before their document split. Words such as current, approved, or implemented
> describe the captured revision, not current authority. This history cannot
> fill an active-contract gap, satisfy a current gate, or authorize a removed
> behavior.

- Source commit: `524e48698ddaf5f4bc118dadb417eb75039a0994`
- Source paths: `docs/specification.md` and `docs/design.md`; the exact
  section boundaries are identified with each captured block below.
- Capture unit: `TG-MOD.14`
- Active replacements:
  [Database specification](../../database-specification.md)
  and [Database design](../../database-design.md).
  [Repository authority](../../authority.md) routes the unchanged common owners.
  Use the public CLI for live Task state and evidence.

## Captured Section 1: Migration And Activation Boundary

Source path: `docs/specification.md`

Source range: `### Migration And Activation Boundary` through immediately before `<a id="schema-v19-bundle-foundation-schema-v20v21-native-writer-and-evidence-json"></a>`.

````markdown
### Migration And Activation Boundary

Schema v17 migration `verification_receipts` creates
one append-only Receipt table and adds the three internal completion-cycle
basis fields through the storage/repository layer. Receipt ownership, target,
uniqueness, link, and qualifying relationships are validated in SQLite and
again on read. Existing cycle rows receive only the version-0/null-digest/
null-link legacy discriminator; the migration synthesizes no Receipt from Task
verification prose, events, `verification_attestation`, completion cycles, M20
observations, command history, or review receipts. Existing done Tasks and
cycles therefore remain honest legacy attestation history. The insert guard
also preserves the pre-existing sole compatibility bridge's exact
`legacy_current_done` partial version-0/null/null shape while rejecting every
other new version-0 cycle.

Migration 18 `evidence_ledger_capture` adds immutable authority snapshots,
whole-field criteria and links, normalized Review provenance, artifact
manifests and entries, Evidence References, current snapshot pointers,
capture-version target bindings, and Verification subject columns. It creates
one exact current-basis legacy snapshot per Task but no historical target
binding, manifest, Reference, provenance row, subject, Receipt, Finding, or
cycle. Reentry validates exact ownership, digests, matrices, triggers, quick
check, and foreign keys without reconciliation or backfill.

````

## Captured Section 2: Initialization And Supported Schemas

Source path: `docs/specification.md`

Source range: `### Initialization And Supported Schemas` through immediately before `### Schema-v20 Foundation And Admission`.

````markdown
### Initialization And Supported Schemas

`setup` is the sole public initializer and migrator. No Task, handoff, review,
doctor, read, or write command creates/migrates a missing/old database.
Missing state is `db_not_initialized`; supported older state is
`migration_required`; a newer schema is `schema_too_new`. Old binaries reject
newer state and never downgrade/write it.

Fresh setup creates schema v22. Structurally complete contiguous source schemas
v1-v21 are setup-only migration inputs; v22 is idempotent current state.
Schema sequence is:

| Version | Durable addition |
|---:|---|
| v1-v4 | original Task/event/project and completion-evidence lineage |
| v5 | structured review target, receipt, and finding state |
| v6 | Git-snapshot base revision |
| v7 | local handoff outbox |
| v8 | immutable Task Contract revisions |
| v9 | optional Effort basis/activity metadata |
| v10 | maintenance opt-in, policy, latest backup/outcome/applied retention |
| v11 | managed backup generation ledger |
| v12 | append-only checkpoints |
| v13 | Viewer source/render generation and outcomes |
| v14 | stable identity, binding/history, and cleanup metadata |
| v15 | completion-cycle history |
| v16 | marker-only native capture activation |
| v17 | immutable Verification Receipts and completion-cycle verification basis |
| v18 | authority/criterion capture, Review provenance, target manifests, Evidence References, and Verification subjects |
| v19 | native completion Bundles, criterion links/Finding snapshots, and Evidence JSON projection state |
| v20 | verification Runner shadow storage and Bundle-v2 null-Runner tagged union |
| v21 | verification Runner gate-basis tags using the existing schema-v20 structures |
| v22 | retired Analyzer reservation cleanup in the existing Evidence/Bundle tables |

Each migration is transactional, idempotent, rollback-tested, validates
contiguous history and required objects/rows, preserves project/business IDs
and durable records, and passes `quick_check` and foreign keys. Acceptance
retains the realistic 12-Task/191-event fixture and historical completion/
review trace through every supported source version. No migration parses
private prose to invent structure.

````

## Captured Section 3: Schema-v20 Foundation And Admission

Source path: `docs/specification.md`

Source range: `### Schema-v20 Foundation And Admission` through immediately before `<a id="current-schema-v21-persistence-contract"></a>`.

````markdown
### Schema-v20 Foundation And Admission

The migration implementation retains one non-public helper restricted to an
explicitly injected database path. It migrates one caller-owned disposable v19
database in
place, at the same path, inside one `BEGIN IMMEDIATE` transaction. It performs
no copy, backup, publication, managed recovery, or canonical-state operation.
Rollback restores the logical schema and data; SQLite file-byte identity is not
claimed.

Migration 20 is exactly `verification_runner_shadow`. It preserves every
existing v19 business row and stable ID, including the canonical payload bytes
and digest of each existing version-1 Bundle. Existing Task Runner markers are
zero and existing cycle/Bundle Runner basis fields are null; the four new
immutable Runner tables and new Runner Evidence rows start empty. Schema-v20
Bundle version 2 admits only `caller_attestation` or `not_required`, and its
Runner observation pointer is always null. Any Runner observation, Reference,
or criterion link remains standalone audit history and is never a completion
cycle or Bundle basis at schema v20. Every schema-v20 Runner record is
gate-ineligible version `0`. The migration owns only the complete physical DDL
and storage-parent integrity; plan, process, observation, cleanup, and service
admission are separate current Runner subsystem boundaries.
The Bundle table rebuild does not preserve or replay arbitrary caller DDL. A
persistent index or trigger whose name is not migration-owned but whose
`sqlite_master.tbl_name` is `completion_evidence_bundles` is unsupported
attached residue: successful migration removes it with the old v19 Bundle
table, while transaction rollback restores it. Unrelated standalone objects
not attached to the rebuilt table remain unchanged.
Marker-only, partial-owned-object, same-version owned-object drift, a known
later marker, busy/contention, integrity, or foreign-key failure is fail-closed
and leaves no partial migration.

The supported schema-v20 compatibility foundation retains the
Bundle-v2 null-Runner payload/serialization/digest writer, and schema-v20
compatibility for Evidence JSON, Viewer, and managed backup/recovery. The
schema activation itself creates no Runner resolution, attempt, sandbox event,
observation, Evidence Reference/link, Bundle member, or Runner projection.
Canonical database migration occurs only through explicit public setup.

Public admission distinguishes a complete v19 source from a hybrid before any
mutation. A database declared as v19 but containing any recognized v20-owned
table, explicit index, trigger, or column fails closed in the canonical
resolver, setup inspection/migration, Viewer, and managed backup/recovery paths.
Recognition follows SQLite's case-insensitive identifier equality: any catalog
object occupying a v20-owned table, explicit-index, or trigger name is a
collision, while a column marker is scoped to its designated parent table.
Generated columns are column markers under the same rule.
Complete-v20 migration admission accepts either empty Runner tables and
Reference/link sets or the exact audit-only Runner graph below.
It rejects a malformed, duplicate, foreign-owned, partially linked, or
gate-eligible Runner graph. Every Task Runner-basis marker remains zero and
every cycle/Bundle Runner-observation pointer remains null; native Bundle-v2
`caller_attestation` and `not_required` verification basis remains valid.
This check occurs before any database or sidecar write, migration backup,
recovery copy/publication, Viewer publication, or managed-backup write. A complete v19 source alone may invoke migration
20; a complete v20 source continues through migrations 21 and 22, and a complete
v21 source invokes migration 22. Exact v22 receives validation-only reentry. Unrelated extra
objects retain the existing policy, including deliberate removal of
unsupported unowned indexes/triggers attached to the rebuilt Bundle table and
preservation of unrelated standalone objects.

Schema v20 is an audit-only Runner foundation and never becomes a qualifying
Runner gate basis. The current schema-v22 delta below retains the schema-v21
qualifying Runner protocol with explicit manual fallback.

````

## Captured Section 4: Current Schema-v22 Persistence Contract

Source path: `docs/specification.md`

Source range: `<a id="current-schema-v21-persistence-contract"></a>` through immediately before `<a id="schema-v21-persistence-contract"></a>`.

````markdown
<a id="current-schema-v21-persistence-contract"></a>
<a id="current-schema-v22-persistence-contract"></a>

### Current Schema-v22 Persistence Contract

Schema v22 is the public schema constant and setup target. Setup reaches it
through the existing ordered migrations from complete v1-v21 sources; v20
continues through 21 and then 22 rather than returning early. Exact-v22
reentry is validation-only. Ordinary commands never migrate or repair state.

Migration 22 is exactly `evidence_reservation_cleanup`. It rebuilds only
`evidence_references`, `criterion_evidence_links`, and
`completion_evidence_bundles`, restores their owned indexes/triggers and the
coupled Evidence cycle guard, and retains all business columns and the
35-table/42-index/59-trigger inventory. Current allow-lists remove
`derived_analysis` source/relation, `llm_derived` assurance, and `batch_analyzer`
producer; `deterministically_derived` and all other valid Evidence remain.
Complete source validation rejects unexpected retired-value rows and unowned
indexes/triggers attached to any of those three rebuilt tables before rebuild;
it never deletes, converts, or invents a replacement for such rows or objects.
Unrelated objects retain the established preservation policy.

Valid business rows, IDs, relations, provenance, completion history, and sealed
source-19/v1 and source-20/21/v2 Bundle payloads/source versions/bytes/digests
are preserved. The Bundle tagged union adds source 22/format 2 with the same
three basis arms as source 21 below. New native completion obtains source 22
from its locked database basis for both the stored Bundle and its payload;
the native cycle guard requires that source. No caller chooses it. The format-2
index reports the actual container schema 22 and may change bytes/digest while
referencing unchanged old Bundles. A source-22 Bundle is not admitted in a
physical schema-21 container. No format, digest domain, or evidence assurance
is upgraded by migration or projection.

Migration follows the existing foreign-key-off/legacy-alter-on transaction,
exact row/object preservation, integrity checks, marker-last, and connection-
setting restoration pattern. It rechecks preserved rows after the marker and
full v22 validation before commit, including effects of admitted unrelated
triggers. Failure restores logical schema/data, not byte-identical SQLite
files. Exact-v22 reentry rejects unexpected attachments and migration temporary
residue without repair. Setup retains its pre-migration managed backup and
matched package/database/artifact rollback boundary; older code rejects v22.

Current Task/Receipt/completion and Runner selection retain the schema-v21
protocol below, including audit-only old graphs, no-relaunch recovery, live
implementation-drift invalidation, and self-contained done history. Setup,
doctor, backup/recovery, resolver/relocation, and Viewer admit v22 through their
existing boundaries. Recovery extends the same verification-only candidate-
local rejection rule through source 22; every other structural Task/Runner/
Bundle fault remains set-fatal. Viewer remains snapshot v4, accepts v5-v22,
validates and discards Evidence/Runner internals, and changes no public field
or UI. No CLI shape, Skill trigger/procedure/call, config, Runner runtime, gate,
or general migration framework is added.

````

## Captured Section 5: Migration Sequence

Source path: `docs/design.md`

Source range: `### Migration Sequence` through immediately before `### Schema-v20 Physical Foundation`.

````markdown
### Migration Sequence

`schema_migrations` is contiguous, named, and validated with required objects
and known later-version markers. Only explicit setup invokes migration. Other
commands never create parents, create a database, stamp missing history, or
perform a reverse migration. Older binaries reject newer schemas.

Current sequential migrations are:

| Version | Name / owned state |
| --- | --- |
| 1 | initial task, event, tool-event, and project metadata schema |
| 2 | compatibility completion-commit requirement/hash projection |
| 3 | `paused` status and exact pause-reason matrix |
| 4 | typed completion evidence |
| 5 | review target, receipt, and finding evidence |
| 6 | Git-snapshot base and receipt target expansion |
| 7 | local handoff outbox |
| 8 | immutable Task Contract revisions and current pointer |
| 9 | Effort Advisory activity generations and bases |
| 10 | one-way project maintenance and backup/Viewer outcome state |
| 11 | managed backup generation inventory |
| 12 | append-only typed checkpoints |
| 13 | Viewer source/render generation state |
| 14 | stable identity and append-only path-binding history |
| 15 | immutable completion-cycle history and internal event linkage |
| 16 | marker-only native completion-capture activation |
| 17 | immutable Verification Receipts and completion-cycle verification basis |
| 18 | authority/criterion capture, Review provenance, target manifests, Evidence References, and Verification subjects |
| 19 | native completion Bundles, criterion links/Finding snapshots, and Evidence JSON projection state |
| 20 | verification Runner shadow storage and Bundle-v2 null-Runner tagged union |
| 21 | verification Runner gate-basis tags using the existing schema-v20 structures |
| 22 | retired Analyzer reservation cleanup in the existing Evidence/Bundle tables |

Every migration is ordered, idempotent on reentry, transactional, and
rollback-tested. Reentry validates rather than synthesizing missing data.
Table rebuilds preserve foreign keys and IDs and restore foreign-key
enforcement in a `finally` path. Migration validation uses
`PRAGMA quick_check`, `PRAGMA foreign_key_check`, exact row/object
preservation, and the sanitized realistic 12-task/191-event fixture with nine
historical completion hashes and representative review, Contract, handoff,
checkpoint, maintenance, identity, and completion traces. The sole current
exception is migration 20's Bundle-rebuild retirement of the
unsupported attached residue defined below; it changes no other migration.

The fixed-state setup migrator accepts complete source schemas v1-v21 and
treats v22 as current. Legacy `state/projects` discovery is intentionally
narrower: v1-v13 plus the explicit schema-v14 legacy-layout transition.
Viewer compatibility is independent and accepts source schemas v5-v22.
Incomplete history, a missing required object/row, a later marker, too-new
state, unsupported layout, foreign identity, or corrupt integrity fails closed.

````

## Captured Section 6: Schema-v20 Physical Foundation

Source path: `docs/design.md`

Source range: `### Schema-v20 Physical Foundation` through immediately before `<a id="schema22-reservation-cleanup-design"></a>`.

````markdown
### Schema-v20 Physical Foundation

One internal storage/repository seam accepts an explicitly injected
caller-owned disposable v19 database. It migrates that database in place at the
same path and is the sole non-public exception to the rule that explicit setup
is the only migrator. It is not routed from the CLI, setup planner,
canonical-state resolver, maintenance, managed backup/recovery, Evidence, or
Viewer. It performs no copy, backup, publication, or path replacement. Public
`SCHEMA_VERSION`, `setup.py`, and all schema-v19 writer/reader routes remain
unchanged.

The migration marker is exactly version `20`, name
`verification_runner_shadow`. A complete v19 source is required. To permit the
Bundle table rebuild, foreign-key enforcement is disabled before
`BEGIN IMMEDIATE` and remains disabled for the entire migration transaction.
`PRAGMA foreign_key_check` must return no row before commit; after commit or
rollback, a `finally` path restores and rechecks foreign-key enforcement as on.
Any error rolls the transaction back. This guarantees logical schema/data
restoration, not byte-identical SQLite file restoration. Lock contention maps
to the existing busy outcome, and no partial column, object, copied row, or
marker may survive. Reentry on exact v20 is validation-only. A missing or
changed v20-owned object, a wrong owned column, marker-only state, a known later
migration marker, a conflicting object using an owned name, integrity failure,
or foreign-key failure is rejected rather than repaired. An unrelated extra
object outside the owned names and not attached to the owned Bundle table being
rebuilt is not, by itself, a schema-v20 failure.

A persistent index or trigger with an unowned name but with
`sqlite_master.tbl_name = 'completion_evidence_bundles'` is unsupported
attached residue. Successful migration deletes it with the old v19 Bundle
table and never replays arbitrary attached DDL. Transaction rollback restores
it, and reentry observes its established absence. No other unrelated object is
deleted by this rule.

#### Exact Schema-v20 Physical Contract

The final required inventory is 35 tables including `schema_migrations`, 42
explicit indexes, and 59 triggers. Relative to v19, migration 20 adds four
tables, ten explicit indexes, and twelve triggers; appends five columns;
rebuilds only `completion_evidence_bundles`; and replaces one existing
criterion-link matrix trigger without changing the trigger count. Public
schema-v20 activation adds no further DDL.

This contract owns physical structure, not the future Runner decision model.
The `plan_state`, `coverage`, `route`, `launch_state`, `outcome`, and `reason`
values are bounded storage codes only. The `trigger` and `event_kind` constants
identify structural record kinds without granting business meaning. Closed
taxonomies and cross-field matrices, pending-cleanup admission, and cleanup
acceptance belong to the target-plan, process, and parent-service boundaries.

The following predicates are literal DDL abbreviations, not open design slots:

- `runner_id(value,prefix)`: `value` is `TEXT`, its length equals the prefix
  length plus 16, it starts with `prefix`, and its 16-character suffix contains
  no character outside `[0-9a-f]`.
- `hex64(value)`: `value` has length 64 and contains no character outside
  `[0-9a-f]`.
- `sha256(value)`: `value` has length 71, starts with `sha256:`, and
  `hex64(substr(value,8,64))` holds.
- `code(value)`: `value` has length 1 through 64, its first character is in
  `[a-z]`, and every later character is in `[a-z0-9_]`; equivalently
  `length(value) BETWEEN 1 AND 64 AND substr(value,1,1) GLOB '[a-z]' AND
  substr(value,2) NOT GLOB '*[^a-z0-9_]*'`.
- `text_n(value,n)`: `value` has length 1 through `n`. A nullable use is exactly
  `value IS NULL OR` the named predicate.

The four immutable tables have no column defaults and use this exact column
order and independent column checks:

- `verification_runner_resolutions`: `verification_runner_resolution_id TEXT
  PRIMARY KEY` with `runner_id` prefix `tg_verification_runner_resolution_`;
  `project_id TEXT NOT NULL`; `task_id TEXT NOT NULL`; `contract_revision
  INTEGER NOT NULL CHECK (contract_revision >= 1)`; `authority_snapshot_id TEXT
  NOT NULL`; `verification_criterion_id TEXT NOT NULL`;
  `verification_expectation_digest TEXT NOT NULL` with `hex64`;
  `verification_criterion_digest TEXT NOT NULL` with `sha256`; `target_kind
  TEXT NOT NULL` with `code`; `target_value TEXT NOT NULL` with
  `text_n(target_value,500)`; nullable `target_base_revision TEXT` with
  `text_n(target_base_revision,128)`; `target_generation INTEGER NOT NULL CHECK
  (target_generation >= 1)`; `target_capture_version INTEGER NOT NULL CHECK
  (target_capture_version = 1)`; `artifact_manifest_id TEXT NOT NULL`; nullable
  `target_material_digest TEXT` with `sha256`; `plan_state TEXT NOT NULL` with
  `code`; nullable `plan_blob_object_id TEXT` with
  `text_n(plan_blob_object_id,500)`; nullable `plan_raw_digest TEXT` with
  `sha256`; nullable `plan_id TEXT` with `text_n(plan_id,200)`; nullable
  `plan_version INTEGER CHECK (plan_version >= 1)`; nullable
  `plan_semantic_digest TEXT` with `sha256`; nullable `selected_entry_digest
  TEXT` with `sha256`; `coverage TEXT NOT NULL` with `code`; `step_count INTEGER
  NOT NULL CHECK (step_count BETWEEN 0 AND 16)`; `runner_contract_version
  INTEGER NOT NULL CHECK (runner_contract_version = 1)`;
  `runner_implementation_version TEXT NOT NULL CHECK
  (runner_implementation_version = 'taskgov-verification-runner/1')`;
  `runner_implementation_digest TEXT NOT NULL` with `sha256`;
  `runner_policy_digest TEXT NOT NULL` with `sha256`; nullable `runtime_digest
  TEXT` with `sha256`; `gate_eligibility_version INTEGER NOT NULL CHECK
  (gate_eligibility_version = 0)`; `trigger TEXT NOT NULL CHECK
  (trigger = 'review_target_set_v1')`; `route TEXT NOT NULL` with `code`;
  nullable `reason TEXT` with `code`;
  `idempotency_digest TEXT NOT NULL` with `sha256`; `created_at TEXT NOT NULL`.
- `verification_runner_attempts`: `verification_runner_attempt_id TEXT PRIMARY
  KEY` with `runner_id` prefix `tg_verification_runner_attempt_`; `project_id
  TEXT NOT NULL`; `task_id TEXT NOT NULL`; `target_generation INTEGER NOT NULL
  CHECK (target_generation >= 1)`; `gate_eligibility_version INTEGER NOT NULL
  CHECK (gate_eligibility_version = 0)`; `verification_runner_resolution_id TEXT
  NOT NULL`; `target_material_digest TEXT NOT NULL` with `sha256`;
  `runner_implementation_digest TEXT NOT NULL` with `sha256`; `attempt_digest
  TEXT NOT NULL` with `sha256`; `intent_recorded_at TEXT NOT NULL`.
- `verification_runner_sandbox_events`: `verification_runner_sandbox_event_id
  TEXT PRIMARY KEY` with `runner_id` prefix
  `tg_verification_runner_sandbox_event_`; `project_id TEXT NOT NULL`; `task_id
  TEXT NOT NULL`; `target_generation INTEGER NOT NULL CHECK (target_generation
  >= 1)`; `verification_runner_attempt_id TEXT NOT NULL`; `event_kind TEXT NOT
  NULL CHECK (event_kind = 'attempt_cleanup_succeeded')`; `event_digest TEXT NOT
  NULL` with `sha256`; nullable `terminal_observation_id TEXT`; `created_at TEXT
  NOT NULL`. The fixed code names the record shape only: no table
  check requires a terminal observation or treats the row as accepted cleanup.
- `verification_runner_observations`: `verification_runner_observation_id TEXT
  PRIMARY KEY` with `runner_id` prefix `tg_verification_runner_observation_`;
  `project_id TEXT NOT NULL`; `task_id TEXT NOT NULL`; `target_generation
  INTEGER NOT NULL CHECK (target_generation >= 1)`; `gate_eligibility_version
  INTEGER NOT NULL CHECK (gate_eligibility_version = 0)`;
  `verification_runner_resolution_id TEXT NOT NULL`; nullable
  `verification_runner_attempt_id TEXT`; `runner_implementation_digest TEXT NOT
  NULL` with `sha256`; `route TEXT NOT NULL` with `code`; `launch_state TEXT NOT
  NULL` with `code`; `outcome TEXT NOT NULL` with `code`; nullable `reason TEXT`
  with `code`; `complete_plan INTEGER NOT NULL CHECK (complete_plan IN (0,1))`;
  `total_step_count INTEGER NOT NULL CHECK (total_step_count BETWEEN 0 AND 16)`;
  `completed_step_count INTEGER NOT NULL CHECK (completed_step_count BETWEEN 0
  AND total_step_count)`; nullable `failed_step_ordinal INTEGER CHECK
  (failed_step_ordinal BETWEEN 1 AND total_step_count)`; `started_at TEXT NOT
  NULL`; `finished_at TEXT NOT NULL`; `duration_ms INTEGER NOT NULL CHECK
  (duration_ms >= 0)`; nullable `cpu_time_ms INTEGER CHECK (cpu_time_ms >= 0)`;
  nullable `peak_job_memory_bytes INTEGER CHECK (peak_job_memory_bytes >= 0)`;
  nullable `total_process_count INTEGER CHECK (total_process_count >= 0)`;
  `sanitized_result_digest TEXT NOT NULL` with `sha256`; `created_at TEXT NOT
  NULL`. No physical check pairs route, launch, outcome, reason, plan completion,
  resource fields, attempt presence, or cleanup.

Every new migration-20 Runner foreign key uses `ON UPDATE RESTRICT ON DELETE
RESTRICT`. All new Runner foreign keys are `NOT DEFERRABLE` except the
event-to-terminal-observation cycle, which is `DEFERRABLE INITIALLY DEFERRED`.
The exact new column mappings are:

- resolution `(project_id,task_id)` -> Task `(project_id,task_id)`;
  `(project_id,task_id,authority_snapshot_id)` -> authority snapshot
  `(project_id,task_id,authority_snapshot_id)`;
  `(project_id,task_id,verification_criterion_id)` -> Contract criterion
  `(project_id,task_id,criterion_id)`; and
  `(project_id,task_id,artifact_manifest_id)` -> artifact manifest
  `(project_id,task_id,artifact_manifest_id)`;
- attempt `(project_id,task_id,target_generation,
  verification_runner_resolution_id)` -> the same four-column resolution
  parent key;
- observation `(project_id,task_id,target_generation,
  verification_runner_resolution_id)` -> the resolution parent key, and its
  nullable `(project_id,task_id,target_generation,
  verification_runner_attempt_id)` -> the attempt parent key;
- sandbox event `(project_id,task_id,target_generation,
  verification_runner_attempt_id)` -> the attempt parent key, and its nullable
  `(project_id,task_id,target_generation,terminal_observation_id)` -> the
  observation parent key;
- Bundle `(project_id,task_id,target_generation,
  verification_runner_observation_id)` -> the observation parent key.

The Bundle rebuild restores every pre-v20 foreign-key clause verbatim from the
accepted v19 definition. It therefore preserves each clause's implicit
`NO ACTION` behavior without adding explicit `ON UPDATE` or `ON DELETE` text,
and the existing completion-cycle foreign key remains `DEFERRABLE INITIALLY
DEFERRED`. The `RESTRICT` rule above applies only to new migration-20 Runner
foreign keys and does not rewrite an existing Bundle clause.

Migration 20 creates exactly these ten indexes. `UNIQUE` is stated explicitly;
every other index is non-unique:

| Index | Ordered columns | Constraint |
|---|---|---|
| `idx_verification_runner_resolutions_parent` | `project_id, task_id, target_generation, verification_runner_resolution_id` | `UNIQUE` parent key |
| `idx_verification_runner_resolutions_task_generation` | `project_id, task_id, target_generation` | lookup |
| `idx_verification_runner_attempts_parent` | `project_id, task_id, target_generation, verification_runner_attempt_id` | `UNIQUE` parent key |
| `idx_verification_runner_attempts_task_generation` | `project_id, task_id, target_generation` | lookup |
| `idx_verification_runner_attempts_resolution` | `project_id, task_id, target_generation, verification_runner_resolution_id` | lookup |
| `idx_verification_runner_sandbox_events_attempt_kind` | `project_id, task_id, target_generation, verification_runner_attempt_id, event_kind` | lookup |
| `idx_verification_runner_observations_parent` | `project_id, task_id, target_generation, verification_runner_observation_id` | `UNIQUE` parent key |
| `idx_verification_runner_observations_task_generation` | `project_id, task_id, target_generation` | lookup |
| `idx_verification_runner_observations_resolution` | `project_id, task_id, target_generation, verification_runner_resolution_id` | lookup |
| `idx_verification_runner_observations_attempt` | `project_id, task_id, target_generation, verification_runner_attempt_id` | lookup with exact predicate `WHERE verification_runner_attempt_id IS NOT NULL` |

These indexes impose no per-generation or per-parent attempt, event, or
observation cardinality. Such admission rules belong to the Runner service.

Migration 20 creates exactly twelve Runner triggers. The eight immutable
triggers have these exact timing/table/body definitions:

| Trigger | Exact definition |
|---|---|
| `trg_verification_runner_resolutions_no_update` | `BEFORE UPDATE ON verification_runner_resolutions FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_resolutions_no_delete` | `BEFORE DELETE ON verification_runner_resolutions FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_attempts_no_update` | `BEFORE UPDATE ON verification_runner_attempts FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_attempts_no_delete` | `BEFORE DELETE ON verification_runner_attempts FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_sandbox_events_no_update` | `BEFORE UPDATE ON verification_runner_sandbox_events FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_sandbox_events_no_delete` | `BEFORE DELETE ON verification_runner_sandbox_events FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_observations_no_update` | `BEFORE UPDATE ON verification_runner_observations FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_observations_no_delete` | `BEFORE DELETE ON verification_runner_observations FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |

Each remaining trigger is `BEFORE INSERT FOR EACH ROW`; its `WHEN NOT EXISTS`
subquery is the corresponding exact parent predicate below, and its body is
`BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END`. There is no
alternate action. The four exact parent predicates are:

- `trg_verification_runner_resolutions_parent_insert` joins required parents
  `tasks` as `t`, `authority_snapshots` as `s`, `artifact_manifests` as `m`,
  verification `contract_criteria` as `vc`, and its required verification
  `authority_snapshot_criteria` membership. It requires matching
  `project_id/task_id`; `t.current_contract_revision = NEW.contract_revision`;
  `t.review_target_authority_snapshot_id = NEW.authority_snapshot_id`;
  `t.review_target_acceptance_criterion_id IS m.acceptance_criterion_id`;
  `t.review_target_verification_criterion_id = NEW.verification_criterion_id`;
  `t.review_target_kind = NEW.target_kind`; `t.review_target_value =
  NEW.target_value`; `t.review_target_base_revision =
  COALESCE(NEW.target_base_revision,'')`; `t.review_target_generation =
  NEW.target_generation`; `t.review_target_capture_version =
  NEW.target_capture_version`;
  `t.review_target_artifact_manifest_id = NEW.artifact_manifest_id`; and
  `t.review_target_runner_basis_version = 0`. Snapshot `s` must have the same
  owner and ID, `s.contract_revision = NEW.contract_revision`, and
  `s.verification_digest = NEW.verification_expectation_digest`. Criterion `vc`
  must have ID `NEW.verification_criterion_id`, kind `verification`, and
  `vc.digest = NEW.verification_criterion_digest`, with a snapshot membership
  for `NEW.authority_snapshot_id`. Manifest `m` must have ID
  `NEW.artifact_manifest_id`; the same `project_id`, `task_id`,
  `authority_snapshot_id`; `m.acceptance_criterion_id IS
  t.review_target_acceptance_criterion_id`; `m.verification_criterion_id =
  NEW.verification_criterion_id`; `m.target_kind = NEW.target_kind`;
  `m.target_value = NEW.target_value`; `m.target_base_revision =
  COALESCE(NEW.target_base_revision,'')`; `m.target_generation =
  NEW.target_generation`. Contract revision is bound by Task plus snapshot, and
  capture version is bound by Task; `artifact_manifests` has neither column and
  the trigger performs neither comparison. If
  `t.review_target_acceptance_criterion_id IS NULL`, no acceptance criterion or
  acceptance membership is joined or required. Otherwise the same parent
  predicate additionally requires one same-owner `contract_criteria` row `ac`
  whose ID is `t.review_target_acceptance_criterion_id` and whose kind is
  `acceptance`, plus one same-owner snapshot membership for `ac.criterion_id`.
- `trg_verification_runner_attempts_parent_insert` searches one resolution with
  the same owner, target generation, and resolution ID and requires
  `resolution.target_material_digest = NEW.target_material_digest` and
  `resolution.runner_implementation_digest =
  NEW.runner_implementation_digest`.
- `trg_verification_runner_observations_parent_insert` searches one resolution
  with the same owner, target generation, and resolution ID and requires its
  Runner-implementation digest to equal NEW. If
  `NEW.verification_runner_attempt_id` is non-null, the same `EXISTS` predicate
  also requires one attempt with that owner/generation/ID, the same resolution
  ID, and the same Runner-implementation digest.
- `trg_verification_runner_sandbox_events_parent_insert` searches one attempt
  with the same owner, target generation, and attempt ID. If
  `NEW.terminal_observation_id` is non-null, the same `EXISTS` predicate also
  requires one observation with that owner/generation/ID whose attempt ID
  equals NEW. The fixed event code is not interpreted as cleanup proof.

No trigger scans for an earlier uncleaned attempt or enforces plan, route,
launch, outcome, reason, resource, or cleanup acceptance.

Migration 20 appends `tasks.review_target_runner_basis_version INTEGER NOT
NULL DEFAULT 0 CHECK (review_target_runner_basis_version IN (0,2))`. It then
appends, in this order, nullable `TEXT` columns
`task_completion_cycles.verification_basis_kind` and
`task_completion_cycles.verification_runner_observation_id`; neither cycle
column has a physical `CHECK` or foreign key in schema v20.

`completion_evidence_bundles` is rebuilt from the schema-v19 definition. All
v19 columns keep their exact order and definitions except that
`source_schema_version` and `bundle_version` become `INTEGER NOT NULL` with no
individual single-value `CHECK`; their allowed pair is owned only by the tagged
union below. Nullable `verification_basis_kind TEXT CHECK
(verification_basis_kind IS NULL OR verification_basis_kind IN
('caller_attestation','not_required'))` and nullable
`verification_runner_observation_id TEXT` are inserted, in that order, before
the exact trailing columns `omission_mask`, `sealed_at`, `bundle_digest`, and
`payload_size_bytes`. The rebuild adds the Bundle observation foreign key
listed above and this exact tagged-union `CHECK`:

```sql
(
  source_schema_version = 19
  AND bundle_version = 1
  AND verification_basis_kind IS NULL
  AND verification_runner_observation_id IS NULL
)
OR (
  source_schema_version = 20
  AND bundle_version = 2
  AND verification_basis_kind = 'caller_attestation'
  AND verification_receipt_id IS NOT NULL
  AND verification_runner_observation_id IS NULL
)
OR (
  source_schema_version = 20
  AND bundle_version = 2
  AND verification_basis_kind = 'not_required'
  AND verification_receipt_id IS NULL
  AND verification_runner_observation_id IS NULL
)
```

The rebuild restores `idx_completion_evidence_bundles_task_cycle`, the Bundle
immutable trigger pair, and the existing member, Finding-snapshot, and
cycle-basis matrix triggers from the accepted v19 definitions. It replaces
`trg_criterion_evidence_links_matrix_insert` with the v19 arms unchanged plus
one dormant physical arm for relation `runner_observation`: its Evidence
Reference must use source kind `runner_observation`, its criterion must equal
the Reference verification criterion, and attribution must be exactly
`machine_observed`, producer `verification_runner`, version `1`. Migration
creates no Runner Reference, criterion link, projection, or Bundle member; the
parent service owns the first durable audit mapping and write.

Only those owned Bundle objects are restored. Any persistent unowned index or
trigger attached to the old Bundle table is deleted with that table on a
successful migration; its arbitrary DDL is not replayed. A rollback restores
the attached object transactionally, while unrelated standalone objects remain
unchanged.

For every v19 row the new Task marker is `0`, both new cycle columns are null,
and both new Bundle columns are null. All four Runner tables and all new Runner
Evidence/criterion-link sets are empty. Every pre-v20 table is compared on its
original ordered columns, so all business rows/IDs and existing version-1
Bundle payload bytes/digests remain equal. The version-20 marker is inserted
only after those checks; exact schema validation, full storage validation,
`quick_check`, and foreign-key check precede commit.

Focused migration tests cover normalized `sqlite_master`, `table_xinfo`,
foreign-key, ordered-index, and trigger inventory; permissive physical
cardinality; the null-Runner Bundle union; a positive resolution parent with
nullable acceptance and present verification criterion; marker-only and
partial-owned-object drift; unrelated standalone objects; successful retirement
and rollback restoration of unowned Bundle-attached indexes/triggers; v19 row,
ID, Bundle-byte, and object preservation; reentry; rollback; and contention.
Public setup/schema behavior is tested separately from the storage-only
migration helper so test imports do not expand that helper's authority.

The final storage model deliberately omits the legacy shim fields
`sandbox_provider`, `sandbox_policy_digest`, and `sandbox_instance_digest`.
When constructing or verifying the compatible digest/source projection, the
storage adapter supplies literal null for `sandbox_provider` and
`sandbox_policy_digest` on a resolution and for `sandbox_instance_digest` on an
attempt. These adapter-only keys are never persisted as schema-v20 columns.
No provider/policy/instance shim is an active storage or Evidence consumer.

#### Schema-v20 Public Contract

The public schema-v20 contract adds no further table, column, index, or trigger.
It owns the schema constant/setup target, Bundle-v2 null-Runner writer,
Evidence JSON, Viewer, and managed backup/recovery compatibility. Schema
activation itself creates no Runner resolution, attempt, sandbox event,
observation, Evidence Reference/link, Bundle member, or Runner projection; the
parent service owns those audit writes. Canonical migration occurs only through
explicit public setup, never as an implicit effect of Git materialization.

The public storage admission layer classifies complete v19, complete v20, and
hybrid state before invoking migration or another schema-aware consumer. A
database whose migration history declares v19 but that contains any recognized
v20-owned table, explicit index, trigger, or column is rejected before any
database or sidecar write, migration backup, recovery copy/publication, Viewer
publication, or managed-backup write. The canonical resolver, setup inspection/migration,
Viewer source validator, and managed backup/recovery validator all use this
same owned-inventory check. Complete v19 reaches migrations 20, 21, and 22;
complete v20 reaches 21 and 22; complete v21 reaches 22; exact v22 takes
validation-only reentry. Unrelated extra objects retain the migration-20 policy: an
unowned index/trigger attached to the Bundle table is removed
only by a successful complete-v19 migration, while unrelated standalone
objects remain unchanged. Migration-21 hybrid markers are recognized before
any write.
Owned-marker recognition uses SQLite `NOCASE` identifier equality across
catalog object types and scopes ordinary or generated columns to their
designated parent table.
Complete-v20 admission uses one shared full audit-graph validator. It admits
the empty state, a single pending
intent, a cleanup-only restart terminal, or the exact complete terminal graph
defined by the specification; it rejects every malformed, duplicate,
foreign-owned, partially linked, or gate-eligible variant. Task Runner basis
remains zero and cycle/Bundle Runner observation pointers remain null, while
native Bundle-v2 `caller_attestation` or `not_required` basis is admitted. The
validator is repeated in each independent operational read, writer, Viewer,
backup, and recovery snapshot rather than cached across them.

At schema v20 the native completion transaction derived
`verification_basis_kind` as `caller_attestation` when a qualifying
Verification Receipt is linked and as `not_required` for trimmed-empty
verification, wrote Bundle version 2/source schema 20, and always wrote a null
Runner-observation pointer. Schema v21 retains those manual branches and
additionally writes the
exact qualifying `runner_observation` branch, reusing its existing Runner
Reference and criterion link as Bundle members. Existing Bundle-v1 payload bytes
and digests remain immutable, and Evidence JSON may project both preserved v1
and native v2 Bundles. The manual branches create no Runner member or projection;
the Runner branch uses only its already-sanitized stored observation.

`evidence_projection.py` selects the format from the validated source schema,
never from caller input. For schemas v20-v22 it extends the v1 payload key set only
with `verification_basis` and `runner_observation`. The former is the exact
four-key object fixed by the specification; the latter is null for manual
branches and populated only for the qualifying Runner branch. It
seals the sorted-key compact payload with
`taskgov-completion-evidence-bundle-v2\0`, writes envelope
`format_version=2`, and recomputes the stored digest and byte count from those
exact bytes. Projection reconstructs and validates a stored v1 or v2 Bundle by
its own tagged-union row without converting either format.

The schema-v20-through-v22 index uses `taskgov-evidence-index-v2\0`, envelope
`format_version=2`, and the prior entry key set plus
`bundle_format_version`. The new field is null for legacy entries, 1 for a
preserved v1 Bundle, and 2 for a native v2 Bundle. The index remains ordered
and index-last; its other limits and publication behavior are unchanged.

````

## Captured Section 7: Current Schema-v22 Reservation Cleanup Design

Source path: `docs/design.md`

Source range: `<a id="schema22-reservation-cleanup-design"></a>` through immediately before `<a id="schema21-runner-gate-basis-design"></a>`.

````markdown
<a id="schema22-reservation-cleanup-design"></a>

## Current Schema-v22 Reservation Cleanup Design

`storage.py` sets the public schema target to 22 and composes the existing
migration sequence through `_migrate_schema22_connection`. A complete source
20 runs 21 then 22, source 21 runs 22, and fresh/older construction reaches the
same final v22 objects and contiguous markers 1 through 22. Exact-v22 reentry
opens a validation-only transaction and rolls it back without a write.

Migration `22/evidence_reservation_cleanup` rebuilds exactly
`evidence_references`, `criterion_evidence_links`, and
`completion_evidence_bundles`. Their unchanged ordered business columns retain
all valid rows and IDs. The 14 owned replacement objects comprise those tables,
their indexes/triggers, and `trg_task_completion_cycles_evidence_basis_insert`;
the full inventory remains 35 tables, 42 explicit indexes, and 59 triggers.
Current DDL and enum/order dispatch remove only `derived_analysis` source and
relation, `llm_derived` assurance, and `batch_analyzer` producer. Old version-
specific definitions remain source-admission/compatibility owners.

The helper requires no active transaction, `foreign_keys=ON`, and
`legacy_alter_table=OFF`. It sets foreign keys off and legacy alter on before
`BEGIN IMMEDIATE`, validates complete source 21, and rejects unowned attachments
to those three tables and exact temporary-name collisions before DDL. Existing
stored-source validation rejects retired-value rows without conversion or
deletion. The only temporary names are `evidence_references_v21`,
`criterion_evidence_links_v21`, and `completion_evidence_bundles_v21`.

It snapshots all business tables on their original ordered columns and the
SQL of objects outside the replacements; drops the coupled Evidence cycle
guard; renames/recreates/copies the three tables; drops their old copies in
reverse order; and restores the owned objects and cycle guard. It proves row
and unrelated-object equality, exact owned structure, quick check, and foreign
keys before inserting marker 22 as the last mutation. Full v22 validation and
another preserved-row comparison follow before commit, so an admitted unrelated
marker trigger cannot silently change business rows. Failure rolls back the
whole logical transaction; `finally` restores and verifies both connection
settings. Reentry validates attachments/temporary-name absence and never repairs.

The Bundle CHECK retains source-19/v1 and source-20/21/v2 arms unchanged and adds
source-22/v2 with the same three basis arms as source 21. The locked completion
basis carries the validated physical source version into both payload and row;
the native cycle guard requires source 22. Shared stored-row validation compares
Bundle source with the observed container, including selected-history reads,
so source 22 cannot be accepted in physical 21. Pure encoding remains
source-version-aware and introduces no format or digest domain. Old sealed
Bundle bytes/digests are never relabelled or resealed; index format 2 reports
container 22 while referencing those unchanged files.

Task/Receipt/lifecycle and Runner capability checks admit 22 under the unchanged
schema-v21 protocol below. Current validation, setup/doctor, backup/recovery,
resolver/relocation, and Viewer use their existing source-aware dispatch; no
parallel reader or candidate runtime exists. Global stored-state validation
remains global. Recovery defers only the existing Task-verification privacy/
capacity rejection until structural Runner/Bundle checks complete. Viewer
extends its same-transaction one-shot Task-batch proof to 22, remains snapshot
v4, and discards Evidence/Runner details before its unchanged projection.
Setup retains normal pre-migration backup and matched rollback; no new policy,
config, process/Runner gate, Skill procedure, or public command is introduced.

````

## Captured Section 8: Schema-v21 Runner Gate-Basis Design

Source path: `docs/design.md`

Source range: `<a id="schema21-runner-gate-basis-design"></a>` through immediately before `## Stable Project Identity, Binding, And Relocation`.

````markdown
<a id="schema21-runner-gate-basis-design"></a>

## Schema-v21 Runner Gate-Basis Design

This section retains the exact schema-v21 migration/storage implementation and
qualifying Runner protocol inherited by schema v22. Its source-21 Bundle and
migration statements describe that supported predecessor; the
[current v22 delta](#schema22-reservation-cleanup-design) owns current source
identity, reservations, setup target, and consumer upper bounds. The manual
Verification Receipt remains the explicit fallback.

### Migration Identity And Exact Owned Delta

Migration 21 is exactly version `21`, name
`verification_runner_gate_basis`. It adds no table, column, explicit index, or
trigger. The final required inventory therefore remains 35 tables including
`schema_migrations`, 42 explicit indexes, and 59 triggers. It rebuilds exactly
these four tables and no other table:

1. `completion_evidence_bundles`;
2. `verification_runner_resolutions`;
3. `verification_runner_attempts`; and
4. `verification_runner_observations`.

Every column retains its schema-v20 name, order, nullability, default, foreign
key, and non-gate check. The only Runner-table DDL change is that
`gate_eligibility_version` on resolutions, attempts, and observations changes
from `CHECK (gate_eligibility_version = 0)` to
`CHECK (gate_eligibility_version IN (0, 1))`. The sandbox-event table has no
gate-eligibility column and is not rebuilt. The Task column remains exactly
`review_target_runner_basis_version INTEGER NOT NULL DEFAULT 0 CHECK
(review_target_runner_basis_version IN (0, 2))`; neither completion-cycle
column is rebuilt or gains a column check or foreign key.

The rebuilt Bundle table retains Bundle version 2 and all schema-v20 columns,
keys, and foreign keys. Its nullable `verification_basis_kind` column check is
widened only to
`caller_attestation|not_required|runner_observation`. Its table-level tagged
union is exactly:

```text
source 19 / Bundle 1 / kind null / Runner pointer null
source 20 / Bundle 2 / caller_attestation / Receipt nonnull / Runner pointer null
source 20 / Bundle 2 / not_required       / Receipt null    / Runner pointer null
source 21 / Bundle 2 / caller_attestation / Receipt nonnull / Runner pointer null
source 21 / Bundle 2 / not_required       / Receipt null    / Runner pointer null
source 21 / Bundle 2 / runner_observation / Receipt null    / Runner pointer nonnull
```

No other source-schema/Bundle-version/basis/pointer combination is valid.
Preserved source-19 and source-20 rows are not rewritten to source 21. A native
schema-v21 completion writes source schema 21 and Bundle version 2; no Bundle
version 3, Evidence Index version 3, new envelope member, or compatibility
conversion is introduced.

Migration 21 recreates under their existing names the one Bundle index, the
nine indexes attached to the three rebuilt Runner tables, and their existing
immutable and parent triggers. Their order, uniqueness, predicates, error
codes, and names remain unchanged except for the eligibility relations below.
It also replaces, without changing the trigger count, exactly these two
cycle-insert guards:

- `trg_task_completion_cycles_verification_basis_insert`; and
- `trg_task_completion_cycles_evidence_basis_insert`.

All other owned tables, columns, indexes, triggers, and their normalized SQL
remain the schema-v20 definitions. In particular,
`trg_verification_runner_sandbox_events_parent_insert` and
`trg_criterion_evidence_links_matrix_insert` already supply the required event
and `runner_observation` Reference/link relations and are unchanged.

### Closed Gate Tags And Parent Guards

`gate_eligibility_version=0` continues to mean audit-only. Version `1` means
only that the exact target generation was selected under the schema-v21 Runner
basis protocol; it does not itself mean pass. Within one Runner graph,
resolution, attempt, and observation eligibility values must be identical.
The current target's Task marker is `0` for an audit-only or ordinary manual-Receipt
target and `2` for a target selected under the version-1 Runner protocol.
Historical graphs retain their stored tags after a later target generation;
only the graph at the Task's exact current target can be a completion basis.

The recreated parent guards make these literal changes and no others:

- `trg_verification_runner_resolutions_parent_insert` keeps its complete
  schema-v20 Task/Contract/authority/criterion/manifest/target predicate, but
  replaces `Task marker = 0` with the exact pair
  `(NEW eligibility = 0 AND Task marker = 0) OR
  (NEW eligibility = 1 AND Task marker = 2)`.
- `trg_verification_runner_attempts_parent_insert` additionally requires the
  attempt eligibility to equal its resolution eligibility.
- `trg_verification_runner_observations_parent_insert` additionally requires
  the observation eligibility to equal its resolution eligibility and, when an
  attempt is present, that attempt eligibility to equal it too.
- The sandbox-event guard remains unchanged: its exact attempt and optional
  terminal-observation joins inherit eligibility equality from those parent
  rows.

The full stored-state validator retains the existing four cardinality shapes
(`no admitted attempt`, `pending intent`, `restart cleanup only`, and `complete
terminal graph`) for audit-only graphs and admits the same applicable pending,
cleanup-only, and terminal shapes for a marker-2 eligibility-one current target.
It validates and exposes a structurally sound pending, cleanup-only, or launched
non-pass state to read, Viewer, backup, and recovery consumers; it does not
misclassify such a state as corrupt merely because it cannot complete. A
marker-2 target without its atomic resolution/attempt T1, a current marker/tag
mismatch, or a malformed graph remains invalid. Separately, the completion
basis selector, rather than a caller or a DDL tag alone, recognizes these three
closed cases:

- marker `0`: no gate-eligible current graph; manual
  `caller_attestation|not_required` completion remains available;
- marker `2` plus one complete version-1 terminal graph whose observation has
  `route=m21_fallback`, `launch_state=no_launch`,
  `outcome=blocked_prelaunch`, reason `runtime_unavailable` or
  `process_setup_failed`, `complete_plan=0`, and the existing proved process,
  handle, output-discard, lifecycle, and private-tree cleanup: the selected
  Runner did not launch, so a fresh exact-current manual Receipt may be used; and
- marker `2` plus one complete version-1 terminal graph whose observation has
  `route=runner`, `launch_state=launched`, `outcome=pass`, null reason,
  `complete_plan=1`, equal positive planned/total/completed step counts, and
  null failed ordinal: that observation is the sole qualifying Runner basis.

For either marker-2 terminal case, the graph must have exactly one resolution,
attempt, cleanup event, observation, Runner Evidence Reference, and
`runner_observation` verification-criterion link for the exact current
generation. Ownership; current Contract and authority/criterion digests;
target, capture, manifest, target-material, plan, implementation, and policy
identities; parent IDs; idempotency/source digests; event-to-observation link;
and the existing closed process-result matrix must all validate. For a live,
non-done Task, the qualifying pass additionally requires the exact currently
installed manifest-bound Runner implementation identity. That current-package
comparison belongs to service preflight, not a SQLite trigger or historical
replay. A pending intent, cleanup-only predecessor, missing
or duplicate member, eligibility mixture, stale generation, target or Contract
drift, malformed graph, or any structurally valid terminal other than the exact
closed no-launch fallback or qualifying pass is not a manual fallback and blocks
completion for that selected marker-2 basis.

The replacement verification-basis cycle guard retains the version-zero
legacy arm. Its native version-one arm admits exactly one of:

- the existing manual specified/full-pass Receipt or trimmed-empty
  `not_required` basis while the Task marker is `0`;
- the existing specified/full-pass manual Receipt basis while marker `2` has the
  exact closed no-launch terminal graph above; or
- a specified verification expectation, null Receipt, kind
  `runner_observation`, and nonnull observation pointer equal to the exact
  stored qualifying-pass graph above.

The replacement evidence-basis cycle guard retains the legacy source-19
Bundle relation and requires every native cycle to reference its unique
same-owner/same-ordinal Bundle. It additionally requires cycle and Bundle
`verification_basis_kind` and Runner pointer to be identical. A source-21
caller/not-required Bundle must match one of the two manual cycle arms; a
source-21 Runner Bundle must match the qualifying-pass arm. Its Runner
observation pointer must select that exact current-generation observation.
These guards preserve the existing deferred Bundle-to-cycle relation and add no
cycle-table rebuild.

The two cycle guards validate only SQLite-stored tags, parents, pointers,
captured identities, and graph relations. They never inspect the filesystem or
current package manifest. Before inserting a new Runner-backed completion, the
service selector owns current package inspection and equality with the captured
Runner implementation identity. A valid done cycle, including one restored by
recovery, instead revalidates identity equality wholly inside its stored graph
and Bundle and is not rebound to the currently installed implementation.

The manual writer creates only Task marker `0` and source-21 Bundle-v2
`caller_attestation|not_required` rows. The Runner target-set service alone may
create marker `2` with a gate-eligibility-version-`1` graph. Shared schema-v21
readers, Viewer, backup, and recovery validators understand both shapes. A
manual completion writer encountering marker `2` fails closed with no cycle,
Bundle, event, Evidence, Viewer, or backup mutation and never reinterprets that
state as ordinary fallback. An explicit fresh target generation is required
before marker-0 manual behavior resumes. This is a service-level selection
boundary and adds no migration or DDL.

The schema-v21 public gate adapter consumes only a fully validated Task and the
internal basis selector. It retains the exact Receipt-era
`verification_evidence` JSON shape and keeps subject, counts, and recent rows
Receipt-only. For a live, non-done Task, marker zero delegates unchanged to the
manual Receipt arm. For marker two, basis freshness precedes outcome: any
structurally valid but non-current graph,
pending graph, or cleanup-only graph maps to `evidence_basis_stale`. Every
exact-current structurally valid terminal other than the exact closed no-launch
fallback or qualifying pass maps to `verification_receipt_blocking`; the exact
closed no-launch delegates to the manual arm, and the exact qualifying pass maps to
satisfied with both public nullable gate fields null. Receipt-add
accepts marker two only for that exact-current no-launch fallback; every other
marker-two branch returns `evidence_basis_stale` before uniqueness. Completion
applies the same branch result before review sufficiency.

A valid done version-one cycle takes precedence over the live matrix. The
adapter revalidates the stored cycle/Bundle arm and replays its manual or Runner gate
projection; a Runner arm is satisfied with both nullable gate fields null only
when its captured stored graph/Bundle identity matches. It never compares a
done arm with the currently installed implementation, and this historical read
does not authorize a new live Runner completion. Existing argument,
Task/status, expectation, target, generation, and capture checks retain their
order before the applicable selector. Active service preflight returns the
existing `package_core_modified` or `package_status_unknown` result when it
cannot establish the current installed identity; after successful package
inspection, an identity mismatch is stale before outcome mapping.
Malformed Runner/Task storage remains `project_state_unreadable`, malformed
cycle/Bundle history remains `completion_history_inconsistent`, and malformed
Receipt storage remains `invalid_verification_evidence`; no new public error or
field is introduced.

Contract revision, verification expectation or criterion, authority snapshot,
review-target tuple or generation, artifact manifest, plan/selected-entry,
target material, implementation/policy identity, reopen, or retarget drift
prevents an earlier Runner graph from being current. Existing invalidation and
reopen paths clear marker `2` together with the current target and require a
fresh generation; they never update or delete an immutable Runner row, cycle,
Bundle, Reference, link, or historical completion basis.

### Migration, Reentry, And Preservation Algorithm

A v20-to-v21 migration is admitted only from a complete schema-v20 database
that passes the exact current schema-v20 object, Task/Contract, Evidence,
Bundle, Runner audit-graph, `quick_check`, and foreign-key validators. This
includes only the current empty, pending-intent, restart-cleaned, and complete
terminal audit shapes. Thus all predecessor Runner eligibility values and Task
markers are zero and all cycle/Bundle Runner pointers are null. A v20 marker
with any v21 widened table or replacement-trigger definition, a v21 marker with
any v20 definition, a temporary-name collision, partial owned state, known
later marker, integrity or foreign-key failure, or an already gate-eligible row
is rejected before any database, sidecar, backup, Evidence, or Viewer write.

The migrator requires `foreign_keys=ON` and `legacy_alter_table=OFF` on entry,
then sets foreign keys off and legacy alter on before `BEGIN IMMEDIATE`. The
legacy setting prevents SQLite table renames from retargeting the unchanged
foreign keys held by completion cycles, Bundle members, sandbox events, or the
other rebuilt tables. It uses only these exact temporary table names:

```text
completion_evidence_bundles_v20
verification_runner_resolutions_v20
verification_runner_attempts_v20
verification_runner_observations_v20
```

Inside the one transaction it snapshots all existing business tables on their
ordered schema-v20 columns and the normalized owned/unrelated object inventory;
drops the two cycle guards; renames the four old tables; creates the exact v21
resolution, attempt, observation, and Bundle tables; copies every row by the
complete unchanged ordered column list; drops the four old tables; and
recreates the exact owned indexes, immutable triggers, parent guards, and cycle
guards. Parent-before-child creation and copy order is resolution, attempt,
observation, Bundle. The temporary tables are dropped child-before-parent:
Bundle, observation, attempt, resolution.

Source admission, migration, and reentry use this closed extra-object matrix:

| Extra object | Complete-v20 admission | Successful migration 21 | Rollback | Exact-v21 reentry |
|---|---|---|---|---|
| unowned index/trigger attached to `completion_evidence_bundles` | reject before any write under the existing v20 Bundle rule | not reached | not applicable | reject |
| unowned index/trigger attached to `verification_runner_resolutions`, `verification_runner_attempts`, or `verification_runner_observations` | admit under the existing v20 unrelated-extra rule | retire with the old rebuilt table; never replay its DDL | restore with the old table | reject |
| unowned object attached to `verification_runner_sandbox_events` or another table that migration 21 does not rebuild | admit when the existing v20 unrelated-extra rule admits it | preserve its row/SQL unchanged | preserve | preserve when otherwise valid |
| unrelated standalone table/view/index/trigger | admit when the existing v20 unrelated-extra rule admits it | preserve its row/SQL unchanged | preserve | preserve when otherwise valid |

This matrix does not broaden complete-v20 admission. In particular, migration
21 never consumes the Bundle-attached residue that only a complete-v19 source
could have retired during migration 20. For the three rebuilt Runner tables,
transaction rollback restores every admitted attached object even though a
successful migration intentionally retires it.

Before inserting the marker, the migrator proves exact projection equality for
every pre-v21 table on its original ordered columns, exact IDs and row counts,
unchanged source-19/source-20 Bundle payload bytes and digests, expected
owned-object SQL/counts, absence of temporary tables and retired attached
residue, preservation of unrelated standalone objects, `quick_check`, and an
empty foreign-key check. The version-21 marker is the last mutation. Full
schema-v21 and stored-state validation follows before commit. Any injected or
organic failure rolls back every rename, row, trigger, object, and marker. The
`finally` path restores `legacy_alter_table=OFF` and `foreign_keys=ON` and
verifies both; rollback promises logical schema/data identity, not byte-identical
SQLite files.

Fresh schema v21 produces the same final owned SQL and contiguous markers
1 through 21, with empty Runner tables and no invented basis. Exact v21 reentry
is validation-only: it creates, deletes, rewrites, upgrades, or repairs nothing.
Migration never changes eligibility `0` to `1`, Task marker `0` to `2`, adds a
cycle/Bundle Runner pointer, creates a Runner Reference/member/link, or
synthesizes qualification from a schema-v20 audit observation.

### Bundle, Evidence, Viewer, Backup, And Recovery Compatibility

Bundle-v2 serialization remains domain
`taskgov-completion-evidence-bundle-v2\0` and the existing sorted compact key
set. The `verification_basis` object remains its exact four-key object. For a
Runner arm, `runner_observation` is the existing sanitized Runner source
projection with exactly these keys and no others:

```text
observation_id, gate_eligibility_version, route, reason, outcome, launch_state,
complete_plan, total_step_count, completed_step_count, failed_step_ordinal,
started_at, finished_at, duration_ms, cpu_time_ms, peak_job_memory_bytes,
total_process_count, plan_blob_object_id, plan_raw_digest, plan_id, plan_version,
plan_semantic_digest, runner_implementation_version,
runner_implementation_digest, runner_policy_digest, runtime_digest,
sanitized_result_digest
```

The projection is recomputed from the exact observation/resolution graph and
must equal its existing Reference source projection and digest. It never adds
stdout/stderr, command/argv, environment, exit code, exception, credential,
absolute/private path, raw plan, raw target, or debug text. The existing Runner
Reference and verification-criterion link become the Runner Bundle's bound
evidence members; they are reused, not duplicated or caller-authored. Manual arms
keep `runner_observation=null` and their existing members. Preserved Bundles are
never resealed.

Evidence Index remains v2 with domain `taskgov-evidence-index-v2\0`; a native
schema-v21 Bundle still has `bundle_format_version=2`. Publication remains
query-only capture, Bundle-first/index-last, atomic replacement, and SQLite as
the sole authority. Viewer snapshot remains v4 and expands source compatibility
only from v5-v20 to v5-v21. The v21 reader validates the complete tagged graph,
Bundle members and sanitized projection, then discards all Runner-only fields;
the public Viewer field/UI allow-list, CSP, text-only DOM rule, artifact cap,
and generation behavior do not change.

At the schema-v21 boundary, setup creates v21, migrates complete v1-v20 sources,
and treats v21 as validation-only state; current setup continues to v22 as above.
Managed backup publication-retention suffixes
remain `r<1-20>`; source-schema 21 admission does not change that independent
retention field. All other filename, identity, locking, staging, validation,
and publication rules are unchanged. A
v20 primary or backup receives its normal pre-migration managed backup before
migration 21. Recovery validates each candidate at its declared schema, may
stage-and-migrate a complete v20 candidate to v21, and validates a v21 candidate
without migration. The private Runner tree remains outside SQLite backup;
restored pending intents keep the existing no-relaunch, cleanup-only recovery
rule. A completed historical Runner basis is self-contained in its validated
SQLite graph and Bundle and never depends on a mutable plan file or private
attempt tree.

Schema-v21 stored Tasks use the complete schema-v20 Task, Contract-pointer,
privacy, and relationship validator unchanged, including the exact 1,000-code-
point `verification` capacity. Managed recovery applies the same candidate-
local rule to source schemas 18 through 21: only that field's privacy or
capacity failure rejects one candidate locally. Wrong storage class, cross-
field or relationship failure, another Task fault, or any Runner/Bundle graph
fault remains whole-set fatal.

No reverse migration exists. Rollback to an older package requires restoring a
matching package, database, managed-backup set, Evidence index/Bundles, and
Viewer artifact from the same accepted pre-migration generation; an older
binary must reject schema v21. Invalid, partial, foreign, stale, over-bound, or
privacy-unsafe candidates fail closed before publication and preserve the
last-good canonical database, backup ledger, Evidence index, and Viewer. Each
failure uses its existing sanitized schema, project-state, Evidence, Runner, or
service owner; schema-v21 representation adds no public error code or raw
diagnostic field.

Schema-v21 verification covers normalized DDL and the unchanged 35/42/59
inventory; fresh v21; exact v20-to-v21 row/object/projection preservation;
marker-last rollback at every rebuild stage; same-version no-write reentry;
hybrid rejection; Bundle-attached pre-write rejection; rebuilt-Runner-table
attachment retirement and rollback restoration; sandbox-table and standalone-
object preservation; version-0 audit and version-1 structural graphs; manual-only
marker-2 completion rejection; Bundle-v2/Evidence-v2/Viewer-v4 sources v5-v21;
managed backup `r1-20`; recovery and rollback matching; privacy deny-list;
schema-v21 Task `verification` 1,000/1,001 boundaries; source-v21 candidate-
local privacy/capacity rejection with later-candidate selection; set-fatal
handling for every other Task/relationship/Runner/Bundle fault; and public
schema/setup activation without a new CLI leaf or Skill/Runner/gate behavior.

````
