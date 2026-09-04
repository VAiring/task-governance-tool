# task-governance-tool Current Implementation Design

Status: the immutable published product remains v0.10.0/schema v16/Viewer v4
sources v5-v16/20 leaves; its identity is fixed in `docs/release-install.md`.
The current unpublished candidate is v0.13.0 with SQLite schema v22, Viewer
snapshot v4 accepting source schemas v5-v22, and 21 public command leaves. Its
active implementation includes tool-owned Verification Receipt subjects,
versioned Review provenance, immutable Evidence References and completion
Bundles, deterministic Evidence JSON, and the explicitly opted-in trusted-local
verification Runner with a closed manual fallback. Schema v20 remains a
supported migration source and
audit-only Runner lineage; schema v22 is current persistence and retains the
schema-v21 Runner gate protocol unchanged.
M25 Select-Split-Merge-Register is active only in the Skill instruction layer.
The Task database owns live state and evidence; completed execution narrative
belongs only in indexed history.

This document is the current implementation design for the behavior specified
in `docs/specification.md`. The [authority index](authority.md) and live Task
Contract select the applicable current owners and sections. Historical design captures
under `docs/history/` are non-authoritative and are never needed to implement,
operate, migrate, or review the supported product.

## Design Summary

`task-governance-tool` is a small local-first Codex Skill and deterministic
Python CLI. The Skill supplies concise agent routing; the CLI owns structured
state transitions; SQLite owns local helper state; and the generated Viewer is
a read-only projection. Governing project documents, not SQLite or the Skill,
remain authority for project decisions.

The implementation deliberately avoids a general issue tracker or workflow
engine:

- normal operation is offline and uses no external model call;
- inspection does not create, migrate, recover, or repair state;
- target-project or Git mutation requires separate explicit authority;
- durable text uses its defined per-field bounds and is privacy-validated;
- external work never runs while a SQLite writer is held;
- state and artifacts have one physical project-scoped owner; and
- historical evidence cannot satisfy a current completion or review gate.

## Source And Package Layout

The source repository contains governing documents, tests, fixtures, and one
self-contained installable package:

```text
AGENTS.md
docs/
  authority.md
  specification.md
  design.md
  history/
plan.md
tools/
  document_contract.py
  release_contract.py
  test_lanes.py
task-governance-tool/
  release-manifest.json
  SKILL.md
  agents/openai.yaml
  assets/task-viewer.template.html
  references/
    task_workflow.md
    cli_contracts.md
    reconciliation.md
  scripts/
    taskgov.py
    task_governance_tool/
tests/
fixtures/
```

Normal stateful use supports exactly one physical package at:

```text
<governed-project>/.agents/skills/task-governance-tool
```

User-wide, symlink, junction, and other reparse-point installs are unsupported.
The source repository has one development-only self-host exception: an
explicit `--repo` may use the physical package at
`<repo>/task-governance-tool` when the four fixed source-shape marker files and the
fixed package entry/manifest files identify that source shape and no competing
project-scoped install exists. It uses the same package-local state resolver;
it is not a second state mode or install recommendation.

The supported runtime is Python 3.12 or newer on Windows. CI verifies exactly
Python 3.12 and 3.14; no Linux or macOS support claim is inferred.

The package is self-contained. `scripts/taskgov.py` disables bytecode creation
before package imports. Release archives contain the package only, including
its bundled HTML template and manifest, and exclude source-repository tests,
fixtures, root documents, local configuration, generated state, caches, and
logs.

## Runtime Module Boundaries

The implementation keeps these narrow ownership boundaries:

- `cli.py` parses the public surface, resolves lexical root options, dispatches
  services, and formats bounded JSON or text.
- `compact.py` owns compact task projections and final byte caps.
- `project_scope.py` validates the governed root, physical package layout,
  self-host exception, containment, and effective Git-ignore preflight.
- `state_paths.py` defines fixed state names and containment rules.
- `state_resolver.py` is the sole production resolver for fixed state, bounded
  legacy discovery, identity, binding, recovery observations, and artifact
  targets.
- `state_transition.py` owns private staging, no-clobber publication, and
  bounded legacy cleanup.
- `storage.py` owns SQLite connections, migrations, validation, repositories,
  and transaction-scoped queries. Feature modules do not open raw SQLite
  connections.
- `tasks.py`, `ordering.py`, and `selection.py` own task validation, lifecycle,
  current/list projections, the source-schema-aware stored Task row/batch
  and Contract-relationship validator, the shared sequential predecessor
  predicate, and next-task selection. `storage.py` supplies source-schema
  capability and the fixed stored-state failure boundary rather than
  duplicating Task semantics.
- `completion.py`, `completion_workflow.py`, and `git_snapshot.py` own typed
  completion evidence, read-only Git observations, completion planning, and
  review-to-commit snapshot binding.
- `completion_history_projection.py` owns the bounded public cycle projection;
  `storage.py` alone inserts immutable cycles.
- `verification_receipts.py` owns caller Receipt validation, exact-current
  classification, completion-gate evaluation, and the bounded Task-show read
  model; `storage.py` alone owns Receipt persistence, migration structure, and
  version-aware legacy-label/internal-subject stored-row validation.
- `review_provenance.py` owns the closed Review provenance matrix, canonical
  v1/v0/null public union, and provenance digest without SQLite access.
- `evidence_ledger.py` owns authority/criterion canonicalization, closed
  assurance/producer dispatch, Evidence Reference projections and digests,
  and capture-version source guards without SQLite access.
- `evidence_projection.py` owns canonical Bundle/index encoding, coherent
  projection capture, digest validation, and index-last publication.
- `artifact_manifest.py` owns safe bounded Git leaf observation, exact rename
  classification/order, opaque/complete manifests, and manifest digests. Git
  observation is separate from the short DB binding transaction.
- `reviews.py` owns review target, receipt, finding, and deterministic gate
  evaluation; `review_packet.py` owns bounded read-only review context.
- `contracts.py` owns immutable Task Contract revisions and invalidation.
- `checkpoints.py` owns optional append-only checkpoints.
- `handoffs.py` owns the local handoff outbox; selection never depends on
  handoff or adapter state.
- `effort.py` owns the optional deterministic Effort Advisory and no task
  transition.
- `setup.py` orchestrates explicit initialization, migration, recovery,
  relocation, maintenance opt-in, and direct Viewer repair.
- `doctor.py` combines read-only package, scope, and project observations.
- `self_status.py` is the bounded package-integrity inspector used internally
  by doctor; there is no public `self` command.
- `backup.py`, `artifact_lock.py`, and `maintenance.py` own managed SQLite
  copies, one-byte artifact locks, policy state, and post-commit coordination.
- `viewer.py`, `viewer_config.py`, and `viewer_maintenance.py` own compatible
  snapshot reads, strict presentation configuration, safe HTML rendering, and
  generation-based publication.

The closed Runner architecture registry is at
[Trusted-Local Runner Architecture](#trusted-local-runner-architecture).

The bundled template is a complete offline HTML/CSS/JavaScript application. It
has no external dependency, server, database connection, or network API.

The root `tools/release_contract.py` is repository-only verification tooling,
not an installable package module or public CLI leaf. It disables bytecode
generation before importing package code, derives parser leaves and release
versions from their owning runtime modules, delegates packaged-core inspection
to the runtime manifest inspector, and reads the tracked inventory with one
bounded shell-free `git ls-files -z`. Its deterministic findings cover
manifest/package drift, release metadata, license, active command inventories,
CI wiring, and generated-artifact exclusions without creating state or
changing a target project.

The root `tools/test_lanes.py` is the single repository-only owner for the
module-level `fast`, `integration`, and `release` test manifest and the CI
event/Python/lane matrix. It discovers with the same `unittest` start,
top-level directory, and pattern as the prior full command, rejects loader
errors, duplicate IDs, duplicate ownership, unassigned modules, and stale
manifest modules, and preserves discovery order when filtering. `all` is a
meta-lane over the unchanged standard suite, not a fourth maintained list.
The runner disables bytecode generation, performs no network or repository
write, and is not an installable package module or public CLI leaf.

## Public CLI And Serialization

### Command Surface

The parser exposes exactly 21 command leaves:

```text
setup
doctor
task add
task list
task next
task current
task effort
task show
task edit
task complete
task checkpoint
handoff record
handoff list
handoff show
handoff withdraw
review prepare
review target set
review receipt add
review finding add
review finding resolve
verification receipt add
```

There are no public `db`, `self`, `web`, export, repair, maintenance, backup,
restore, relocation, or adapter commands. Public `--db` is rejected. Internal
path injection remains available only to repositories, services, and tests.
`--repo` defaults to the current directory because a governed project need not
be a Git repository; runtime never silently re-roots it to a Git worktree.
Invocation while the current directory is either supported package root
requires explicit `--repo`, preventing the package from becoming the governed
project accidentally. Setup alone accepts the bounded backup interval,
retention, and relocation-confirmation options; normal Skill routing supplies
backup defaults and passes a token only after the explicit preview/approval
flow.

Root preprocessing recognizes lexical `--json`, removed commands, and removed
or unknown root options before package, project, Git, or state resolution. A
rejected token or option value is never echoed. Argparse contains no
compatibility subparsers.

`setup` is the only initializer, migrator, recovery/relocation confirmer,
maintenance opt-in, and direct Viewer repair surface. `setup --read-only`
builds a no-write plan. `doctor` is the sole diagnostic, is inherently
read-only, never repairs anything, and is not a normal Task-loop prerequisite.

### Output Boundary

JSON uses the stable envelope:

```json
{
  "ok": true,
  "command": "task.show",
  "project_id": "tg_project_...",
  "data": {},
  "warnings": [],
  "errors": []
}
```

No public envelope contains `db_path` or another internal path. Errors use
stable codes and sanitized fixed messages. Text output is concise and
operational and likewise exposes no database, backup, Viewer, staging, or
package path.

Exit status is 0 for success, 1 for parser/input validation, and 2 for
database, migration, project-state, or service failure unless a command's
fixed contract narrows it.

Default read models and compact projections use explicit allow-lists. They
never serialize a raw SQLite row. Compact current and next results are capped
at 24,576 and 16,384 UTF-8 bytes. Completion check is capped at 8,192 bytes,
checkpoint caller input at 6,144 bytes, and a complete Review Packet at 32,768
bytes. The final JSON boundary also bounds lexical parse errors and replaces an
otherwise unbounded message with a fixed omission message.

Inspection leaves are no-create and no-write. A missing database,
migration-required source, unsupported journal, invalid identity/binding, or
busy database returns its stable error and the command's fixed empty data
shape. Successful business writes describe the record they committed; only
after commit may bounded maintenance warnings be appended.

## Canonical State And SQLite Boundary

### Fixed State Resolver

One physical package owns:

```text
state root       <physical-skill>/state
transition lock  <state-root>/taskgov-state.lock
fixed root       <state-root>/current
database         <fixed-root>/taskgov.sqlite
managed backups  <fixed-root>/backups
Evidence index   <fixed-root>/evidence/index.json
Evidence bundles <fixed-root>/evidence/bundles
Evidence lock    <fixed-root>/evidence/taskgov-evidence.lock
Viewer           <fixed-root>/viewer/task-viewer.html
Runner root       <fixed-root>/verification-runner
Runner lock       <fixed-root>/verification-runner/taskgov-verification-runner.lock
Runner attempts   <fixed-root>/verification-runner/attempts
Runner quarantine <fixed-root>/verification-runner/quarantine
```

The resolver returns canonical paths, the in-memory governed root/hash/display
observation, stored identity/binding when available, source schema, layout
state (`missing`, `fixed_current_v1`, or `legacy_projects_v1`), binding state
(`unbound`, `matching`, or `relocation_required`), and an optional deep
setup-only recovery/legacy observation. None of its raw paths or path hashes
crosses a formatter.

For the Runner subsystem, `CanonicalStatePaths` derives the four fixed paths above
and `DatabaseTarget` carries them to the parent service. Internal test targets
derive the same names beneath their injected database parent. No service,
repository, lifecycle, or process module reconstructs the fixed root, and the
layout is created only after an exact Runner route has passed pre-T1
preflight. The lifecycle owner continues to require the existing physical
fixed root as `Runner root.parent`.

Fixed-primary normal consumers validate only the authoritative primary and
derive canonical artifact targets. Setup, recovery, transition, and legacy
discovery use the deep bounded inventory projection. The caller selects the
projection mechanically; it is not a CLI or LLM choice. Every DB-backed leaf
uses the same resolver observation. Feature code may not reconstruct a
project ID or state path.

### Journal And Connection Rules

Live operational access supports rollback-journal SQLite only. Before any
existing-database read, write, or migration, `storage.py`:

1. rejects lexical `<db>-wal` or `<db>-shm` entries;
2. reads only the fixed SQLite header;
3. rejects a valid header whose read or write version indicates persistent
   WAL; and
4. leaves rollback `-journal` files to SQLite's normal locking/recovery rules.

The check does not open SQLite, checkpoint, change journal mode, delete a
sidecar, or disclose a path/header/OS error. WAL maps to
`unsupported_journal_mode`; unreadable headers use a sanitized internal error.
A short, invalid, or unknown header is not mislabeled as WAL and proceeds to
normal schema validation.

Read connections use:

```text
file:<absolute-uri>?mode=ro
PRAGMA query_only=ON
BEGIN
```

They never use `immutable=1`. Schema history, required objects, project
identity/binding, and command rows are read from the same transaction.
`task show` combines task, events, review evidence, handoff summary, Contract,
checkpoint, completion history, and Verification Receipt evidence in one
snapshot. Handoff list reads count and rows together. Viewer capture reads
generation, tasks, events, and history in one compatible-schema transaction.
`task next` deliberately uses one
transaction for status/paused advisory and another for candidates; cross-phase
staleness is advisory only, while each phase remains internally coherent.

Write services perform caller validation and all Git or other external
observation before opening the writer. They then:

1. run journal preflight;
2. acquire `BEGIN IMMEDIATE`;
3. revalidate schema, project ID, binding hash/generation, task status,
   ordering, Contract revision, review target/generation, and other
   operation-specific basis;
4. persist business rows and events in one savepoint/transaction; and
5. commit and close before formatting or maintenance.

`updated_at` alone is never a concurrency token. No external process, sleep,
backoff, copy, render, model call, or configured command runs while the writer
is held. Residual SQLite busy/locked results map after the normal driver wait to
`database_busy`, exit 2, with no raw SQLite text or retry question. Handoff
record may retry its whole fresh local transaction once; no general automatic
retry exists.

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

## Stable Project Identity, Binding, And Relocation

### Schema V14 Identity

Fresh setup creates `project_id=tg_project_<uuid4 hex>`,
`identity_scheme=uuid_v1`, binding generation 1, and reason `fresh_setup`.
Migrated databases keep their legacy sanitized-name-plus-12-hex
`legacy_path_v1` ID. Durable business rows never change project ID.

`project_meta.canonical_path_hash` is the current binding, not identity.
Schema v14 also stores:

- `binding_generation >= 1`;
- reason `legacy_migration`, `fresh_setup`, or `confirmed_relocation`;
- canonical binding timestamp;
- bounded sanitized display name; and
- the exact cleanup state triple: either pending `0` with null inventory/hash,
  or pending `1` with canonical inventory and its lowercase SHA-256.

`project_path_binding_history` is append-only and keyed by project plus
generation. Generation 1 has no previous hash; each later entry's previous
hash equals the prior current hash and has reason `confirmed_relocation` plus a
token digest. Current metadata must equal the maximum history row. Triggers
prevent identity/creation changes, project deletion, history update/delete,
and invalid cleanup triples. Validation checks consecutive lineage,
scheme-specific IDs, lowercase 64-hex hashes, canonical timestamps, bounded
display text, and canonical cleanup inventory.

The binding repository compares identity, expected generation, and old hash
inside `BEGIN IMMEDIATE`, rejects a same-hash change and signed-64-bit
generation overflow, inserts generation `N+1`, updates the current binding,
and increments Viewer source generation in the same transaction. A missing or
overflowing Viewer row rolls the whole binding change back. The token digest,
not token text, is retained in history.

### Bounded Legacy And Recovery Discovery

Legacy inspection runs only when fixed primary and higher-precedence fixed
recovery are absent. It scans only direct entries under `state/projects`,
consumes at most 64, and rejects an unknown entry, a 65th entry, link/reparse
component, escape, invalid candidate, or multiple candidates without creating
or writing anything.

The candidate validator checks:

- exact legacy directory/database names and one project row;
- contiguous schema and required objects without later markers;
- operational journal mode, quick check, and foreign keys;
- identity/scheme, directory equality, and v14 binding lineage;
- every recognized managed backup as the same identity/scheme with a lineage
  prefix of the mechanically newest source;
- coherent schema-specific backup pointer/rows/files, with at most 21
  retained-plus-in-flight generation identities;
- canonical database, backup, Evidence, Viewer, lock, and recognized temporary paths; and
- at most one bounded regular temporary for each exact restore, backup, and
  Viewer/Evidence temporary-name grammar.

Recovery classification is deliberately two-phase. The resolver opens every
recognized candidate and completes the physical, SQLite, schema, identity,
lineage, metadata, and whole-set checks above against the mechanically newest
structural head. It then calls the storage-owned exact Task-verification
validator with that candidate's source schema. The validator returns normally
or raises a sanitized internal privacy/capacity rejection; every other storage
failure remains structural and set-fatal. The current implementation accepts
at most 500 characters through schema v17 and 1,000 at schema v18-v22. The source
schema is selected before migration or recovery publication; no recovery-only
limit or migration laundering exists.

The structural phase also validates each candidate as the immutable
pre-publication repository snapshot for its ordered physical prefix. One
set-wide generation registry requires every occurrence of a generation ID in
a filename, embedded row, or maintenance pointer to carry identical complete
metadata. A v11+
candidate has its pointer at its last embedded generation row, exactly its own
generation is file-only in that prefix, and bounded row-only generations are
strictly older. A v10 candidate has no rows and its optional pointer is older
than the candidate. With a retained physical predecessor, that pointer equals
the predecessor's complete metadata; only the first retained candidate may
refer to an older generation ID absent from the complete physical candidate
set. Candidate schema versions are not required to be monotonic because an
older fallback can produce a new pre-migration backup after a newer rejected
head.
The storage layer owns one raw SQLite integer-storage validator used by the
resolver's generation-row and maintenance-pointer reads and by ordinary
maintenance/managed-generation repository reads. It accepts only
`type(value) is int` before semantic range validation; present `REAL`, `TEXT`,
`BLOB`, or other non-`INTEGER` values are never normalized with `int(...)`.
Resolver and setup paths translate that defect to the structural
`project_state_unreadable` result before selection or publication, while the
same helper keeps ordinary reader and setup-reentry behavior aligned.
Candidate-derived rollback-journal, WAL, and SHM names, including filesystem
case aliases, are rejected regardless of size. The storage classifier scans
all Task rows so a later malformed SQLite value cannot be hidden by an earlier
local rejection; privacy wins over capacity only after that structural scan
completes.

Each internal managed-backup observation includes metadata, source schema,
stored project/binding lineage, content eligibility, path, and the regular
file's device/inode/size/mtime identity. Fixed and legacy backup-only
resolution retain the complete ordered observation set, select the newest
content-eligible candidate at the current binding, and expose both the
selection and full set to setup. Setup requires its recovery selector to match
that exact selected observation, then compares the complete observation again
while holding the artifact lock. Any candidate or classification drift is a
restore failure; there is no under-lock reselection.

Content eligibility also requires exact identity scheme, binding generation,
canonical path hash, and full binding lineage equality with the structural
head. Earlier valid lineage-prefix artifacts remain structurally observable
but cannot be selected across a binding generation, including an A-to-B-to-A
path history.

Fixed recovery carries that immutable ordered inventory into the restore
service. The service reclassifies the complete set at entry, before private
repository normalization, and immediately before its no-replace canonical
link; both deep resolver results must equal the planned observation and the
newest eligible member must still be the planned selection. Before
normalization, the private SQLite copy must exactly retain the selected
candidate's project/binding, source schema, generation rows, maintenance
pointer, and content classification. After normalization it is opened again
through the schema-aware resolver validator and must have every required
object plus the exact all-artifact row set and mechanical-head pointer. Legacy
backup-only publication pins the selected source identity and the same
repository observation, validates both
the source and copied stage, checks every copied backup against its planned
identity, and resolves the complete legacy source again immediately before the
no-replace stage rename. Any mismatch maps to `setup_restore_failed` and the
private stage is removed without publishing fixed state.

The shallow backup-service inventory refresh runs before each fixed deep
resolver comparison. The publication-side deep comparison is the last
source-set check before directory/canonical-destination guards and the
no-replace link, so a weaker rescan cannot invalidate its conclusion. When a
primary is present, ordinary reads keep primary precedence, but setup and
staged validation still invoke the Task-verification classifier for every
backup: local privacy/capacity results are ignored for selection, while a
malformed stored value remains structural and set-fatal.

The successful return from `restore_managed_backup` is the durable
publication boundary because it occurs only after the canonical no-replace
link. Setup appends `database_restore` immediately on that return, then checks
the returned schema and reads post-link setup state. Failure in either later
check keeps `setup_restore_failed` but carries the durable restore prefix;
failure before return keeps the prefix empty. The restore primitive always
removes its sibling temporary in `finally`, and a retry observes the canonical
primary instead of reselecting or publishing another candidate.

The recovery-specific backup scan uses the same storage validator and excludes
only its privacy/capacity rejection from selection. A structurally coherent set
with only local rejections has no eligible candidate and reaches
`setup_restore_failed`; corrupt, foreign, unsafe, duplicate, overflow, or
otherwise structurally invalid recognized material retains its specific
resolver result where applicable and otherwise reaches
`project_state_unreadable`. Recovery normalization
keeps every structurally coherent artifact in the ordinary generation
row/file/pointer envelope even when the copied candidate is older; local
rejection never hides or legalizes a missing or mismatched relation. It does
not rewrite rejected artifacts, while later ordinary retention keeps its
existing authority over managed generations.

Focused recovery-boundary ownership is split by behavior rather than accumulated in one
module. `test_m214b_recovery_boundaries.py` owns current fixed-layout,
selection, metadata, and TOCTOU behavior in the integration lane;
`test_m214b_legacy_recovery_boundaries.py` owns legacy-primary, schema-v10, and
mixed-schema recovery in the release lane. Shared mutation helpers live in the
non-discovered `m214b_test_support.py`, while the common tree snapshot helper
captures complete managed-state names, kinds, sizes, and contents for no-write
assertions. New recovery cases extend their owning module instead of restoring
one mixed oversized file. TOCTOU coverage observes the phase order around a
drift injection, proves that the last deep resolver comparison follows the
final shallow inventory refresh, and proves that the canonical publish call is
not reached; it does not freeze a private helper call count.

Other contained names are opaque user material and are preserved. A fixed
primary always wins even when invalid; the resolver never falls back around
it. After whole-set validation, fixed missing-primary recovery selects the
newest content-eligible current-binding generation and requires the
mechanically newest binding head to match the governed root. Earlier
generations may be exact lineage prefixes. A moved backup-only legacy source,
foreign/divergent identity, corrupt primary, or unsupported journal is never
a relocation fallback.

Same-binding legacy primary or backup-only state is setup-publishable to fixed
state. Normal commands report migration-required until that atomic
publication. A primary-backed moved source is read-only
`project_relocation_required`; a moved fixed primary is likewise usable only
through explicit relocation confirmation.

### Relocation Token

`relocation.py` emits an opaque, non-secret token:

```text
tgr1.<unpadded-base64url-canonical-json>.<sha256>
```

Its exact payload binds version 1, project ID/scheme, current binding
generation, distinct old/new 64-hex hashes, source layout, source schema, issue
time, and expiry exactly 900 seconds later. A fixed-current source may be
schema v1-v22; `legacy_projects_v1` is capped at the explicit v14 transition,
while ordinary legacy discovery remains v1-v13 plus that v14 shape. Canonical JSON uses
sorted keys, compact separators, UTF-8, and `ensure_ascii=True`; the checksum
is SHA-256 over ASCII `tgr1.<payload>` and is compared with
`hmac.compare_digest`. The whole token is capped at 2,048 ASCII bytes.
Padding, noncanonical encoding, duplicate/missing/extra keys, bad types,
ranges, or times are rejected.

Preview fixes the clock and is no-write. A valid token requires
`issued_at <= now < expires_at`, but handling first checks structural
validity/checksum and successful-token digest reuse. A successfully applied
token therefore remains `relocation_token_used` after expiry. A changed
source, identity, generation, binding, or governed-root observation is stale;
a matching context is not relocation-required. Rejected token values never
appear in output.

`setup --read-only` may emit the fixed six-key relocation preview containing
the token and expiry but no path/hash. Agents must present that preview and
wait for explicit current approval; they never auto-confirm.
`--read-only --confirm-relocation` is rejected before scope inspection.
Write-mode mismatch without the exact token remains no-write.

### Staged Publication And Cleanup

Write-mode setup acquires locks in this order:

1. zero-wait package state-transition lock;
2. relevant source/fixed managed-backup artifact lock; and
3. short private or canonical SQLite transactions.

It never holds a SQLite writer while copying, rendering, invoking Git,
publishing a directory, or cleaning legacy files. Normal business commands do
not acquire the transition lock; canonical rebind instead uses SQLite
compare-and-swap.

Legacy publication and confirmed moved-legacy publication share one bounded
primitive:

1. Revalidate scope, ignore, destination absence, journal, schema, integrity,
   identity/binding, artifact inventory, and optional token under the
   transition lock.
2. Create exclusive, durably flushed paired
   `state/.current-stage-<32hex>.owner` and
   `state/.current-stage-<32hex>` entries. The compact owner record is at most
   2,048 ASCII bytes and binds version, stage ID, project ID, and source
   inventory fingerprint without a path.
3. Admit only database/optional rollback journal, up to 21 managed backups,
   canonical artifact locks, Viewer HTML, and at most one recognized bounded
   temporary per class; at most 32 regular files are staged, each bounded by
   source database size plus 16 MiB. Unsafe, unknown, duplicate, oversized, or
   unowned residue is preserved and stops.
4. Copy the selected database through SQLite backup and copy only validated
   managed/last-good artifacts. Reconcile backup state inside the private
   stage, apply required migrations and maintenance configuration, apply a
   confirmed binding change, persist cleanup intent, and render/validate the
   current Viewer there.
5. Build canonical inventory JSON
   `{"entries":[...],"v":1}` from 1-32 unique recognized source files. Each
   entry contains only kind, relative POSIX name, lowercase SHA-256, and size;
   sorting is by UTF-8 name bytes, canonical bytes are at most 16,384, and the
   fingerprint is their SHA-256.
6. Validate the complete staged database, history, artifacts, Viewer,
   sidecar absence, and binding.
7. Rename the complete directory no-replace to absent `state/current`, then
   remove the external owner marker. A concurrent destination is never
   overwritten.

A pre-publication crash leaves the source authoritative and only a fully
validated owned residue eligible for bounded setup cleanup. A
post-publication crash leaves fixed state authoritative and legacy cleanup
pending. A port without equivalent no-replace directory semantics must fail
closed.

Pending legacy cleanup derives its retirement directory as
`state/.legacy-cleanup-<sha256(project-id)>`. The persisted canonical
inventory, never directory enumeration, is the allow-list. Each recorded file
must be absent or match kind, size, and SHA-256 at exactly one old/retirement
location. Cleanup first no-replace moves remaining old files, then deletes
verified retirement files one by one and only proven-empty owned directories.
Unexpected or changed content stops; unrelated old content remains. One short
transaction clears cleanup state after the recorded set and retirement
directory are absent. Token-free setup resumes a valid pending cleanup.

Fixed-state relocation performs only the binding transaction, then publishes
the Viewer by its generation contract. A crash or Viewer failure after commit
leaves relocation durable and Viewer due; token-free setup repairs it. It does
not manufacture an immediate backup, so loss of the primary before later
managed publication is an explicit bounded recovery window.

## Task State And Selection

### Task Model And Events

Tasks have stable random `tg_task_...` IDs, project ownership, bounded title
and description, kind (`sequential` or `optional`), lane/order, priority
(`low`, `normal`, `high`, `urgent`), status, blocker/pause reasons, review tier,
verification text, tags, timestamps, completion evidence, current review
target, current Contract pointer, Effort activity, and completion-history
coverage. IDs never encode a path.

Statuses are:

```text
ready
in_progress
paused
blocked
review_pending
done
cancelled
```

Blocked requires a reason. Paused requires a pause reason exactly while paused
and may be entered only from in-progress or review-pending; its normal exit is
in-progress and clears the current reason. Initial paused and initial done are
forbidden. `completed_at` is set only on done and cleared on reopen.

Task events are append-only concise audit summaries with stable random IDs.
The internal completion-cycle link added in schema v15 is never in
`PUBLIC_EVENT_FIELDS`; all event-return paths construct the six-field
allow-list explicitly. Latest-event ordering is
`created_at DESC, rowid DESC`. Tool events remain bounded operational records,
not raw logs.

### Shared Stored Task Row/Batch Validator

`tasks.py` owns one source-schema-aware validator for complete stored Task
rows. Its capability object is constructed once per top-level read from the
already-observed source schema and describes the verification limit (500
through v17, 1,000 at v18) and the
presence of review-target base, Contract pointer, and completion-history
coverage fields. The validator receives rows and expected project identity; it
does not query schema metadata, mutate a row, or issue a database write.

Exact text/nullable-text and SQLite integer storage classes are checked before
any value helper can trim or coerce. The validator then applies field privacy
and capacity, stable identity, enum, canonical lane/order/timestamp, and Task
cross-field rules. It raises only `StorageError("project_state_unreadable",
"project state could not be read safely")`. Current Task converters remain
pure allow-list builders and are invoked only after their complete input batch
passes.
The public `task add` and explicit `task edit --verification` ingress is
independently capped at 1,000; every stored/read/internal path uses the source-
schema limit and never revalidates untouched bytes as new caller input.
The shared Task fetch helpers map non-busy SQLite query or UTF-8 decode failure
to that fixed error before a row reaches the validator, while preserving the
existing `database_busy` result for actual busy/locked state.

`list_tasks`, `list_current_tasks`, and `select_next_tasks` select complete
rows with their existing SQL bounds and validate that selected batch before
tag filtering, conversion, or compact omission; they never add a whole-project
validation query. Single-Task reads and lifecycle/write bases route through
the same validator. Whole-project consumers—doctor and Viewer—and setup or
recovery validation load every Task row without a project filter and pass that
complete ownership-checked batch. Managed recovery
sets its explicit verification-local flag so only that field's privacy or
source-capacity failure remains candidate-local; every other Task fault is
structural and set-fatal.

Viewer supplies the source version returned by snapshot validation. For exact
schema v18-v22, that validation completes the full Evidence Ledger and Task batch
checks before issuing one private, one-shot batch proof bound to the same
query-only connection and transaction, project, source version, exact sorted
Task IDs/count, issuance data version, and a fixed nested savepoint held only
in a module-private exact-object issuance registry. The private Viewer ordering
path revokes and consumes that proof once; mismatch, reconstruction, reuse, or
a changed
transaction fails closed. Other callers and source schemas v5-v17 retain the
ordinary complete Task validator. Review evidence consumes the already-
validated Task where available and derives column capability from that
version, removing the per-Task `PRAGMA` path. Routine Viewer failure occurs
before rendering/replacement and preserves the last-good file; its caller
applies the existing fixed maintenance warning.

### Stored Contract Pointer Relationship Boundary

For source schema v8 and later, `validate_stored_task_rows` receives the active
SQLite connection in addition to the already loaded complete Task batch. Only
after all stored-Task scalar checks pass, `tasks.py` extracts the exact selected
Task IDs and performs one `task_contract_revisions` query whose predicate is a
single JSON-encoded ID set consumed by `json_each`. The query intentionally
does not filter `project_id`, so a foreign owner using a selected Task ID is
observable as corruption. An empty batch and source schemas v1-v7 issue no
relationship query.

The relation predicate derives both the exact TEXT key and its UTF-8 BLOB form
from each selected Task ID. The BLOB form exists only to surface a
wrong-storage-class alias to the raw validator; it neither admits an unrelated
Task ID nor changes the selected-batch boundary into a general table audit.

The relationship reader returns raw `project_id`, `task_id`, and `revision`
values for only those selected IDs. Python validates exact TEXT identity and
positive SQLite INTEGER revision before calculating the latest revision; it
does not use `MAX(revision)` or `int(...)` before storage-class validation.
Revision zero requires no returned row. A positive pointer requires a matching
row, same-project/same-Task ownership for every returned row, and equality to
the raw latest revision. Duplicate, dangling, foreign, nonlatest,
revision-zero-with-row, decode, storage-class, and ownership faults all raise
the existing fixed stored-state `StorageError`.

The composed validator is called once by add post-read, list/current/next,
show, Viewer, `read_task`, `read_internal_task`, locked write-basis reads,
setup/doctor preflight, migration/reentry, and recovery. Review Packet,
checkpoint, handoff, Effort, and evidence/completion lifecycle paths inherit it
through their existing Task reader. No consumer owns a second relationship
rule or per-Task query. `contracts.py::read_current_contract` retains Contract
content validation after this boundary; unrelated Contract history is not a
general audit target. Recovery's verification-local result is evaluated only
after relationship validation, so every relationship fault remains structural
and set-fatal.

The shared single-Task fetch helper opens one short read transaction only when
its caller has not already established a transaction. The Task row and its
relationship rows therefore come from one SQLite snapshot even when another
writer commits a Contract revision between calls. Existing query-only and
locked write transactions are reused unchanged; the helper performs no write
and closes only the read transaction it owns.

### Sequential Ordering

One repository predicate determines whether an earlier same-project,
same-lane sequential row is incomplete. Only done and cancelled predecessors
are complete. `task next` and direct transitions to in-progress,
review-pending, or done use that predicate. Task add and edits that change
kind/lane/order/status validate every already-active, review-pending, and done
row in both affected lanes inside the serialized write, preventing
registration or reordering ahead of active successors. There is no override.

Lane input is trimmed and validated once. Sequential omission chooses the
deterministic default lane and next order; all integers fit SQLite signed
64-bit and next-order overflow fails before addition. The unique
project/lane/order index enforces final storage uniqueness.

Next-task order is priority (`urgent`, `high`, `normal`, `low`), lane,
lane-order with nulls last, creation time, then task ID. Default limit is 5.
Paused, blocked, active, review-pending, done, and cancelled rows are not next
candidates. A positive paused population adds one fixed count-only advisory
without changing candidate data or exit status.

`task list` uses the same priority, canonical-lane, nulls-last lane-order,
creation-time, and task-ID order. Its default limit is 20 and maximum is 100.

`task current` selects in-progress, review-pending, paused, and blocked rows,
optionally one valid status, with default limit 20 and maximum 100. It reuses
the latest event/checkpoint and deterministic fixed next-action mapping. It
does not calculate staleness or write a checkpoint. List/current/next remain
bounded and have no pagination cursor.

### Done Immutability And Reopen

Every task/review mutation loads the owner and applies the shared done guard.
A done Task accepts only the exact reopen edit:

- resulting status `in_progress`;
- one non-empty sanitized reopen reason; and
- no other task, note, completion, review, or Contract input.

The reopen writer checks review-generation overflow and sequential ordering,
clears current completion evidence and review target/base, advances review
generation, clears completion time and hold reasons, and appends
`task_reopened`. It preserves prior events, receipts, findings, Contract
revisions, and completion cycles. Schema-v16 reopen additionally validates and
links the latest saved cycle as described below. All other done writes return
`done_task_requires_reopen`.

A review-tier increase is a normal edit. A decrease needs one sanitized reason
and is permitted only while ready, in-progress, paused, or blocked, before any
review target ever existed: generation 0 and empty kind/value/base. It cannot
share completion input or a transition to review-pending/done. Generation
greater than zero permanently proves structured review started.

## Completion Evidence And Review

### Typed Completion Evidence

Schema v4 retains the old required/hash columns only as synchronized
compatibility projections. Current evidence kinds and matrices are:

- `none`: empty revision/reason, approval 0, required 1, empty hash;
- `git_commit`: canonical full commit ID as revision/hash, empty reason,
  approval 0, required 1;
- `external_revision`: bounded revision and reason, explicit approval 1,
  required 1, hash equal to revision;
- `commit_not_required`: empty revision/reason/hash, approval 0, required 0;
- `legacy_unverified`: migration-only partial history retaining the old hash;
  no normal write may choose it.

Changing kind clears fields not valid for the destination. Conflicting stale
fields return `completion_evidence_conflict`. External evidence always needs
the explicit acknowledgement, including in a Git project. `legacy_unverified`
cannot satisfy a new done after reopen.

Git commit resolution uses argument-vector, no-shell reads equivalent to:

```text
git -C <repo> rev-parse --verify --end-of-options <revision>^{commit}
```

Empty/option-shaped input, multiple output lines, non-hex output, ambiguity,
failure, or a noncanonical full ID is rejected. Optional locks and lazy object
fetching are disabled; no Git read may invoke a hook, network fetch, or write.
Stored Git completion and Git review targets are re-resolved under the
done-time plan and compared with the locked stored values.

Thin `task complete`, compatibility `task edit --status done`, and
`task complete --check` share one completion request and ordered validator.
Both write paths require explicit verification/review confirmations, typed
evidence, sequential eligibility, exact current target, the qualifying manual or
Runner basis chosen by the sole selector, a satisfied fresh review gate, and no
blocking receipt/finding. The manual arm uses a qualifying current Receipt;
the Runner-pass arm uses its qualifying observation with a null Receipt link.
Check is read-only and is not an
authorization token: it captures one coherent basis, closes SQLite for Git,
then performs a second coherent basis read. Drift yields
`completion_check_stale`; no readiness row or receipt is stored. Its bounded
projection returns only Task ID, ready/status, the first ordered blocking code,
Contract revision, target generation, proposed evidence kind, and fixed next
action. Marker `0` retains those two check reads and the manual write path;
only live marker `2` adds the Runner selection and selected-basis recapture.

### Review Target And Git Snapshot

The current review identity is the exact tuple:

```text
kind, value, base_revision, generation
```

Kinds are `git_commit`, `diff_fingerprint`, `external_revision`, and
`git_snapshot`. A diff fingerprint is canonical
`sha256:<64 lowercase hex>`. Setting any target advances generation even when
kind/value repeat, and old receipts become audit-only. Receipt creation copies
the complete tuple; callers cannot attach one to another target.

`git_snapshot` takes no caller revision. The Git service reads a stable
`HEAD^{commit}` and stage-0 `git ls-files --stage -z` index before/after
checks, consuming raw path bytes. It rejects unborn HEAD, nonzero stages,
intent-to-add zero objects, sparse directory entries, and capture instability.
The version-1 digest input is:

```text
"taskgov-git-snapshot-v1\0"
<base full object id> "\0"
for mode/object/path entries sorted by raw path bytes:
  <mode> "\0" <object id> "\0" <raw path bytes> "\0"
```

The target value is its SHA-256 fingerprint and base is HEAD. It excludes
unstaged and untracked content.

Target setting first performs bounded Git observation outside SQLite. A short
writer then rereads Task, Contract, current authority snapshot/criteria, and
expected generation and atomically inserts one capture-version-1 target,
manifest, manifest Evidence Reference, and event. Git targets compare complete
tree/index leaves or commit/first-parent leaves; root commits use the read-only
empty-tree identity. `artifact_manifest.py` validates safe UTF-8 POSIX paths,
full mode/object IDs, unique exact renames, fixed byte ordering and ordinals,
10,000 entries, 16 MiB canonical bytes, object presence, and pre/post stability.
Opaque targets insert zero entries and the fixed omission. The writer
revalidates the observation binding but never runs Git.

Migration leaves old targets at capture version 0 with null snapshot,
criterion, and manifest IDs. Source-producing Receipt/Finding/completion paths
check this after target structure and expected-generation equality but before
uniqueness or gate evaluation and return `evidence_basis_stale`. Preparation
and existing-Finding resolution remain read-only with respect to the ledger.

At completion, the proposed commit must have exactly one parent equal to the
stored base. Its recursive tree leaves are normalized to the same
mode/object/path form and must reproduce the fingerprint. Root and merge
commits are unsupported for snapshot binding. Hook-altered, added, removed,
renamed, mode-changed, or otherwise changed content fails
`review_target_mismatch`. A snapshot target may close only with Git-commit
completion evidence. A Git-commit target requires the identical canonical
Git completion commit, an external target requires the identical approved
external revision, and commit-not-required requires a canonical
diff-fingerprint target.

Pre-commit Runner reads continue to recapture the stage-zero index. Completion
passes its already resolved full commit ID into the same selector; for a stored
snapshot, the selector verifies the exact parent and tree fingerprint and
recomputes the stored snapshot material digest from that immutable commit tree,
without inferring target material from ambient HEAD or the post-commit index.

### Receipts, Findings, And Gate

Receipts are append-only and unique per Task, target generation, and bounded
caller-supplied reviewer key. Kinds are:

- `independent`: pass or changes-requested, never user-approved;
- `self_review_fallback`: summarized; a Tier-2 pass requires explicit user
  approval, a Tier-1 pass does not;
- `not_required`: Tier 0 only, verdict not-required, mechanical rationale, no
  approval.

A changes-requested receipt requires a summary and never satisfies the gate.
Replacing a same-generation reviewer result is forbidden; re-review sets a
fresh target generation.

Findings belong to a loaded same-project/same-task receipt and are high,
medium, or low. Resolution preserves the original row and adds only bounded
resolution text/time. Open high or medium findings across all task receipts
block. A resolved high/medium finding still requires a target generation newer
than its receipt and fresh passes. Any current-generation changes-requested
receipt blocks. Low findings are nonblocking.

The gate deterministically requires:

- Tier 0: one current not-required receipt;
- Tier 1: one distinct current independent PASS, or its valid fallback;
- Tier 2: two distinct current independent PASS reviewer keys, or one valid
  explicitly approved fallback when independent review tooling is unavailable;
- no current changes-requested receipt;
- no open high/medium finding; and
- no resolved high/medium finding at or after the current target generation.

Independent PASS rows are ordered by reviewer key then receipt ID and take
precedence over fallback. Tier 0/fallback selection is by receipt ID. Reviewer
key distinctness proves only different stored strings, not identity,
independence, provenance, expertise, target inspection, or authentication. The
trusted caller/orchestrator is responsible for truthful attestation.

### Provenance, Evidence Ledger, And Bundle Structure

The subsections below are the current implementation owner for Review
provenance, schema-v18 capture, schema-v19 Bundle construction and projection,
and their schema-v20-through-v22 Runner integration. They preserve legacy rows
without inventing evidence, keep SQLite ownership in `storage.py`, and exclude
the retired `derived_analysis` reservation from current schema v22.

#### Capture And Projection Module Ownership

- `review_provenance.py` owns the closed enum/matrix, public v1/v0/null union,
  option normalization, and provenance digest; it owns no SQLite access.
- `evidence_ledger.py` owns assurance/producer validation, authority-basis
  canonicalization, whole-field criteria, Evidence Reference projections and
  digests, and public allow-lists. Active link, Bundle, omission, and Runner
  source assembly is delegated to the owning service/projection boundary.
- `artifact_manifest.py` owns bounded shell-free Git tree/index observation,
  exact artifact-entry normalization, deterministic rename pairing/order, and
  manifest digests. It reuses safe process and stable-snapshot primitives from
  `git_snapshot.py` without routing full manifests through Review Packet.
- `evidence_projection.py` owns coherent ledger capture, canonical Bundle/index
  JSON, digest validation, generation comparison, index-last atomic
  publication, last-good preservation, and repair.

`storage.py` remains the only SQLite owner. `tasks.py` and `contracts.py`
capture authority inside existing savepoints; `verification_receipts.py`
derives subject-v1 bindings; it and `reviews.py` create typed References inside
their source writes; target capture creates one manifest and subject-capable
basis atomically; completion passes a fully prepared Bundle basis into its
native-cycle savepoint. No feature module opens raw SQLite.

`state_paths.py` and `state_resolver.py` alone own the fixed Evidence root,
index, Bundle directory, and lock. `state_transition.py` recognizes those
generated files only in bounded setup. `maintenance.py` may retry a due
projection after a changed mutation, but only a cycle insert advances source
generation. `setup.py` is the repair owner. `cli.py` owns only existing
setup/doctor fields and warnings and has no Evidence export/import branch.

#### Schema-v18 Capture And Subject Foundation

Migration 18 `evidence_ledger_capture` adds:

```text
authority_snapshots
contract_criteria
authority_snapshot_criteria
review_receipt_provenance
review_receipt_provenance_codes
artifact_manifests
artifact_manifest_entries
evidence_references
```

It also adds the minimum current-snapshot pointers, target capture bindings,
Review-provenance discriminator/ID, and Verification-subject columns. Every
owned table carries project and Task keys, composite foreign keys,
deterministic uniqueness, canonical timestamp/digest checks, and update/delete
denial triggers. No arbitrary JSON or caller-owned assurance is stored.

The subject columns on `verification_receipts` and
`task_completion_cycles` are independently additive:

```text
verification_subject_basis_version INTEGER NOT NULL DEFAULT 0
  CHECK (verification_subject_basis_version IN (0, 1))
subject_authority_snapshot_id TEXT NULL
  REFERENCES authority_snapshots(authority_snapshot_id)
subject_verification_criterion_id TEXT NULL
  REFERENCES contract_criteria(criterion_id)
```

Authority/criterion tables are created before these `ALTER TABLE` additions.
Version-aware indexes, insert guards, and shared readers enforce the exact
version/null matrix, snapshot-to-verification-criterion membership,
project/Task ownership, and locked target binding. Cycle guards additionally
permit subject basis zero only for the exact partial legacy-reopen bridge. No
old Receipt or cycle table is rebuilt or updated: every old row reads
0/null/null with its original ID, caller label, target, timestamp, Receipt link,
cycle relation, and ordering unchanged.

The physical legacy `command_label` column is retained. The native writer puts
only `taskgov-owned-verification-subject-v1` there; basis-zero validation alone
uses the legacy caller-label predicate, and basis-one validation accepts only
that constant. Public formatters and Evidence digest builders never read it.
`verification_receipts.py` builds the public subject only from locked capture
version, authority snapshot, verification criterion, Contract revision, and
complete target tuple. Neither CLI nor repository accepts a label or subject
argument.

An authority snapshot stores its positive Task-local generation, Task title
and description, review tier, exact verification text/digest, Contract
revision and exact scope/acceptance/constraints/authority, explicit specified
state, canonical basis digest, producer metadata, and time. Its digest uses
canonical JSON under `taskgov-authority-snapshot-v1\0`, excluding random ID and
time. Criteria are whole immutable values keyed by same-Task kind and the
SHA-256 of `taskgov-contract-criterion-v1\0`, kind, NUL, and exact UTF-8 text.
Task add and authority-bearing edit compute and insert snapshot/criteria within
the existing Task savepoint; replay produces no duplicate row or event.

Migration creates one `legacy_migration` snapshot of each Task's exact current
basis and never reconstructs history. Existing targets become capture version
0 with null snapshot/manifest bindings; Receipts, Findings, cycles, and events
receive no Reference or subject. New targets are capture version 1. Any
source-creating write against capture version 0 fails `evidence_basis_stale`
inside the locked basis check; Review Packet preparation and resolution of an
existing Finding remain permitted. Source schemas through 17 use the 500-code-
point stored verification limit and schemas 18+ use 1,000; current public
Task add/edit admission is 1,000. Stored paths never reuse public-input
validation or normalize/truncate bytes.

#### Evidence Reference And Manifest Construction

An `evidence_reference` stores one closed source kind/state and source ID or
closed completion value, assurance/producer/version, exact ownership,
Contract/snapshot/nullable-criterion/four-field-target binding, nullable cycle
ID, digest, and time. One constant repository dispatch derives every required
and null field and source projection; callers provide none. Current source
kinds are manifest, Verification Receipt, Review Receipt, Review Finding,
completion evidence, and gate-eligible Runner observation. The
`derived_analysis` source is not admitted by current schema v22. Reference creation shares the
source transaction. Its digest helper uses
`taskgov-evidence-reference-v1\0` and excludes random ID/time; for Findings it
also excludes mutable resolution fields. Validators recompute dispatch,
ownership, null matrices, and digest and reject every class upgrade.

`artifact_manifest.py` reads complete Git leaves, not Review Packet summaries.
For snapshots it compares exact HEAD with stable stage-0 index; for commits it
compares the exact commit with its first parent or empty tree. Leaves normalize
to `relative_posix_path mode full_object_id`. A bytewise path merge produces
add/delete/modify; a second pass converts only a unique exact mode/object
delete-add pair to rename. Ambiguous duplicates and content-changing moves stay
delete+add. One pure sorter applies the specification tuple with null first and
unsigned UTF-8 comparison, then assigns contiguous zero-based ordinals.

Observation is shell-free and uses bounded timeouts, null stdin, disabled
optional locks/fsmonitor/lazy fetch/external diff/text conversion, and pre/post
stability checks. It never invokes a hook, checkout, index write, fetch,
network, or caller command. Paths must be portable safe relative UTF-8 POSIX
text no longer than 240 bytes. At most 10,000 entries and 16 MiB canonical
manifest bytes are accepted. Overflow, unsafe path, object loss, object-format
change, or drift aborts target setting without a target, manifest, event, or
maintenance effect. Fingerprint/external targets create a zero-entry opaque
manifest with the fixed omission and their respective caller/external class.
The manifest digest covers the exact canonical content under
`taskgov-artifact-manifest-v1\0`; its random ID/time are excluded.

#### Schema-v19 Bundle And Evidence Publication

Migration 19 `completion_evidence_bundles` adds:

```text
criterion_evidence_links
completion_evidence_bundles
completion_bundle_members
completion_bundle_finding_snapshots
evidence_projection_state
```

It also adds `evidence_basis_version` and nullable
`completion_evidence_bundle_id` to cycles. Existing cycles become 0/null; only
the exact partial legacy bridge may later insert that shape. Native cycles use
version 1 and a same-project/same-Task Bundle. Deferred composite foreign keys
allow immutable cycle and Bundle rows to be inserted together.

Criterion-link construction is one closed repository matrix. Acceptance links
the current manifest and completion evidence as `completion_basis`, selected
Review Receipts as `review_assessment`, and current-generation Findings as
`review_finding`; verification links either the unique manual Receipt as
`verification_attestation` or the qualifying Runner Reference as
`runner_observation`. Missing criteria omit links without omitting valid Bundle
members. Every other source/relation/cardinality is rejected.

Bundle members freeze the exact Reference/link set. Finding selection includes
all current-generation Findings plus earlier high/medium Findings, excludes
earlier lows, and orders by generation/time/ID. Native snapshots preserve their
Reference and class; selected pre-v18 snapshots use null Reference plus
`legacy_unknown/legacy_migration/1` and the historical-Finding omission. Their
digest uses `taskgov-completion-bundle-finding-snapshot-v1\0`. No later query
joins mutable Finding state to rewrite a sealed snapshot or file.

Before a completion writer, the workflow prepares exact Git completion and a
canonical JSON-shaped Bundle basis without writing a file. In the short writer
it rereads Task, Contract, authority snapshot, criteria, target/capture,
manifest, selected verification basis, Review Receipts/Findings, and completion
proposal; reevaluates all current gates; and computes the complete payload and
size. Links, snapshots, Bundle, cycle, Task update, event, and source-generation
advance commit atomically. Drift, invalid binding/class/digest, or the 16-MiB
cap rolls back the whole savepoint. Historical rows are immutable; reopen and a
later completion create a fresh target, cycle, and Bundle.

`CanonicalStatePaths` and `DatabaseTarget` add only `evidence_root`,
`evidence_index`, `evidence_bundles`, and `evidence_lock` beneath
`state/current/evidence`. The fixed Bundle filename is its Bundle ID plus
`.json`; no caller path exists. Resolution rejects links, reparse points,
nonregular files, containment changes, DB aliases, unknown recognized names,
and unsafe staged content. Generated Evidence remains outside manifests and
source commits.

Projection state stores nonnegative source generation, nullable published
generation no greater than source, nullable index digest, and the closed
maintenance outcome/time. Every cycle insertion advances source generation
exactly once, including the legacy bridge; no other write does. The projector
captures project/schema, generation, all cycles, native Bundles/members, and
legacy state in one query-only transaction and closes SQLite before rendering.
One encoder owns the exact canonical JSON and array orders. There is no clock
input, so same-basis repair is byte-identical.

Under the zero-wait Evidence lock publication validates or atomically writes
all required Bundle files through flushed same-directory temporaries, flushes
and atomically replaces the index last, conditionally records the captured
generation/digest in a short transaction, then rechecks once and permits at
most one follow-up capture. The index is the commit point. Missing, ahead,
behind, wrong-project/version/digest, unsafe, or otherwise mismatched projection
is never consumed. SQLite stays authoritative and setup regenerates only
one-way; no JSON is imported. Failure or contention keeps the last-good index,
records the fixed maintenance outcome, and does not undo the business mutation.

### Review Packet

`review prepare` reads Task, Contract, target, and review counts in one
transaction, closes it for bounded Git observation, then reopens a short read
transaction to compare project/task identity, Contract revision, and the whole
target tuple. A change returns `review_packet_stale`.

For a snapshot it recaptures the exact base/index context. For a Git commit it
lists first-parent changes, using the empty tree for a root. Fingerprint and
external targets perform no Git read and state that exact caller-provided
material must be bound to the target before PASS. At most 10 shell-free Git
processes, 100 bytewise-sorted relative paths, 240 UTF-8 bytes per path, and
16,384 aggregate path bytes are allowed. Safe overflow is marked truncated;
an unsafe path or a packet above 32,768 bytes fails with no partial packet.

Task, Contract, target, changed paths, five fixed review-focus rows, required
output, and the existing receipt argv shape are allow-listed. The builder
does not launch a reviewer, execute/import a receipt, store a packet, or
include a diff, transcript, prompt, stdout/stderr, secret, or absolute path.

## Test-Only Independent Evidence Reader

`tests/evidence_reader_oracle.py` owns the retained independent Evidence
index/Bundle reader and validation core. `tests/evidence_reader_codec.py` owns
its pure canonical JSON codec; `tests/evidence_test_support.py` owns reusable
fixtures with a separate reference encoder. The oracle preserves the full
selected index entry, including its Bundle-version discriminator, and the exact
Bundle envelope or explicit legacy absence. Its existing regression and M24
integration tests remain the consumers.

These test helpers import neither SQLite/storage nor the producing Evidence
semantic validator. They may reuse the existing bounded physical-filesystem
primitives in `state_paths.py`; independence does not require duplicate I/O or
Windows infrastructure. They have no Analyzer descriptor, packet, report,
outbox, process, model, or publication responsibility.

The installable package contains no M23 Analyzer or standalone Evidence-reader
runtime. Analyzer-only path derivation is removed from `state_paths.py` and
`state_resolver.py`; shared Evidence paths and filesystem primitives retain
their existing owners and behavior. Old ignored analysis artifacts are left
inert, with no cleanup or migration. There is no replacement runtime reader or
reporting adapter.

## Completion Cycle History

### Schema V15 Through V20 Activation

Schema v15 adds immutable `task_completion_cycles`, Task coverage
`legacy_unknown|complete`, nullable internal `task_events.completion_cycle_id`,
parent/foreign-key support indexes, and triggers that prevent:

- every cycle update or deletion;
- changing Task coverage after insert; and
- changing an event's cycle link from null or one saved ID.

Cycle IDs have prefix `tg_completion_cycle_` plus 16 lowercase hex characters.
Ordinals are unique per project/Task, start at 1, and reject signed-64-bit
overflow. The row stores:

- origin `native_done|legacy_current_done` and completeness
  `complete|partial`;
- completion and record times;
- Contract revision, review tier, and verification expectation/attestation;
- the complete six-field completion evidence projection;
- the complete four-field review target;
- gate basis version and deterministic counts/basis kind; and
- up to two exact qualifying receipt IDs protected by composite foreign keys
  to the same Task and target tuple.

Schema v17 additionally stores internal verification basis version,
expectation digest, and nullable Verification Receipt link. Every pre-existing
cycle migrates as verification-basis version 0 with null digest/link. Every
new native cycle uses version 1 and stores the domain-separated digest of its
exact verification expectation. Through schema v20, and for the schema-v21/v22
`caller_attestation` arm, a trimmed-nonempty expectation links the qualifying
pass/full Receipt. Schema-v21/v22 `not_required` and `runner_observation` cycles
instead retain a null Receipt link and satisfy the current closed tagged union;
migration does not rewrite legacy whitespace-only values.
The sole partial `legacy_current_done` reopen bridge remains the only
post-v17 version-0/null/null insert.

Post-v17 native rows must be complete, have completion time, attestation true,
non-empty target, gate-basis version 1, zero blockers, and a valid Tier basis.
Legacy rows are partial, have null attestation, gate-basis version 0, unknown
basis, null counts/receipt slots, and retain an honest legacy completion
projection. Structural `CHECK`s enforce the matrices; repository validation
also enforces canonical hashes/fingerprints/text/timestamps and exact receipt
kind/verdict/approval/order.

Schema v19 independently adds cycle evidence-basis version and nullable Bundle ID. Existing cycles become version 0/null; every schema-v19 native cycle is version 1 with one immutable same-transaction Bundle, while the sole partial legacy bridge remains version 0/null. Schema v20 adds the nullable cycle verification-basis kind and Runner-observation pointer; every new native cycle remains evidence-basis version 1, derives the closed verification-basis kind, and has a null Runner pointer. Public completion history adds no Bundle or Runner field.

Migration 15:

1. fingerprints all prior business tables and counts;
2. creates columns, table, indexes, and immutability triggers in one immediate
   foreign-key-enabled transaction;
3. reads current done Tasks in binary Task-ID order;
4. inserts one ordinal-1 `legacy_current_done` partial row per current done
   Task using one migration timestamp and no inferred gate claim;
5. leaves every old event link null and every Task coverage
   `legacy_unknown`; and
6. proves preservation, quick check, and foreign keys before recording
   `completion_cycle_history`.

Reentry validates current structure/matrices without rerunning migration-time
cardinality assumptions.

Marker migration 16 requires complete v15 and all Tasks still
`legacy_unknown`. For each current done Task it compares the newest cycle with
completion time, Contract/review/verification values, all evidence and target
fields, and whether a reopen already linked it. It inserts the next partial
legacy cycle only when absent, different, or already linked; an exact unlinked
match is retained. It then records
`completion_cycle_capture_activation`. The marker adds no schema object.
Reentry validates and never reconciles twice. Schema-v16 Task creation
explicitly writes coverage `complete`; the column default remains
`legacy_unknown` for schema-v15 safety.

### Native Done And Reopen Transactions

Both `task complete` and compatibility `task edit --status done` perform Git
preflight outside SQLite and converge on one locked native capture:

1. validate schema v22, identity/binding, optimistic Task/authority/target
   capture basis, Contract, sequential ordering, and evidence;
2. reread Verification Receipts and review receipts/findings, evaluate the
   current verification and review gates, and select their deterministic
   bases;
3. choose canonical completion time and next ordinal;
4. insert links, Finding snapshots, one immutable Bundle-v2 row carrying the
   selected caller-attestation, not-required, or Runner-observation basis, and
   one complete verification/subject/evidence-basis-v1 `native_done` cycle;
5. update the current Task to done with identical evidence;
6. rerun lane invariants;
7. insert the existing completion event with the internal cycle link; and
8. advance Evidence and Viewer source generations and commit.

Any failure rolls all business rows back. Concurrent completions serialize; a
loser creates no second cycle. Read-only completion check inserts nothing.

Reopen locks the done Task, loads the highest cycle and linked reopen state,
and compares that cycle with the entire current completion projection. When
coverage is `legacy_unknown` and no cycle exists, it may insert the exact
ordinal-1 partial compatibility bridge. Complete coverage without a cycle,
any existing-cycle mismatch, or an already linked reopen returns
`completion_history_inconsistent`. It then clears current gates, preserves
coverage and cycles, inserts `task_reopened` linked to the validated cycle, and
advances Evidence generation only when it inserts the legacy bridge. It never revalidates historical Git material, uses historical
receipts as current eligibility, changes a cycle, or creates a bridge when a
cycle already exists. A later done uses fresh gates and the next ordinal.

### Public History Projection

`task show` reads total, incomplete-legacy aggregate, and newest-first rows in
the same snapshot as all other Task data. The exact wrapper is:

```json
{
  "total": 2,
  "returned_count": 2,
  "truncated": false,
  "legacy_history_incomplete": false,
  "cycles": []
}
```

`legacy_history_incomplete` is computed over all durable rows, not the bounded
return window. It is true when Task coverage is not complete, any saved cycle
is partial, or any `task_reopened` event has a null internal cycle link. A
fresh schema-v16-or-later Task with no cycle is complete history and false.

Each cycle and each candidate complete wrapper is measured using compact,
sorted-key, non-ASCII-preserving UTF-8 JSON. A cycle is at most 8,192 bytes;
the wrapper is at most 10 rows and 32,768 bytes. Collection stops at the first
non-fitting newest-first row. Version-0 basis uses JSON nulls and no receipt
IDs; version 1 uses integers and the stored one/two IDs without null
placeholders. Text prints only counts, flags, and newest non-content fields.

The formatter revalidates stored completion revision, evidence reason,
completion-hash, target value, and target-base text through the ordinary
privacy matcher before building a public cycle. It has no legacy M19.7
projection. A rejection maps to the fixed
`completion_history_inconsistent` error and never exposes the offending field
or value; `task show` and Viewer use this same formatter.

Viewer batch history is grouped/windowed for at most 500 selected Tasks and 10
cycles each, not one query per cycle. Snapshot v4 uses the identical wrapper.
Source schemas v5-v14 synthesize empty/incomplete history; v15-v22 use stored
rows. Sources v17-v22 validate linked Receipt ownership, basis, target, and
pass/full qualification; v19-v22 also validate and discard Bundle linkage, and
v20-v22 additionally validate the source-appropriate closed verification basis
and Runner pointer.
Neither public events nor Viewer disclose internal event-cycle
or Verification Receipt IDs.

The only new stable error is
`completion_history_inconsistent: stored completion history is inconsistent`.
It exposes no IDs, counts, values, hashes, SQL, or paths.

<a id="schema-v21-manual-receipt-arm-and-bundle-integration"></a>

## Current Schema-v22 Manual Receipt Arm And Bundle Integration

This section defines the manual Verification Receipt arm and its integration
with the sole three-branch selector under
[Schema-v21 Runner Gate-Basis Design](#schema21-runner-gate-basis-design),
retained by [current schema v22](#schema22-reservation-cleanup-design). It
does not rewrite the immutable v0.10.0 artifact or claim a later published
artifact identity. Schema, parser, completion, Viewer compatibility, Skill
guidance, package inventory, and tests form one supported boundary.

### Ownership And Data Model

`verification_receipts.py` owns Receipt input validation,
exact-current classification, manual-Receipt-arm evaluation, and the bounded public read
model. The single service selector combines that arm with the closed Runner
basis; callers do not choose a branch. `storage.py` alone owns schema, migration,
append/read queries, and Receipt uniqueness. `tasks.py` and
`completion_workflow.py` consume the selected gate result; neither opens raw
SQLite or interprets verification prose.
`cli.py` owns only parser/dispatch/formatting for the one Receipt write leaf.

Schema v17 added an immutable `verification_receipts` table with:

```text
verification_receipt_id project_id task_id contract_revision
verification_expectation_digest command_label result duration_ms
scope_coverage target_kind target_value target_base_revision
target_generation created_at
```

IDs use the `tg_verification_receipt_<16-lowercase-hex>` grammar. Result is
`pass|fail|timeout`; coverage is `full|partial`; duration is a nonnegative
signed-64-bit millisecond integer. Legacy labels are nonempty,
privacy-validated, and at most 200 characters; native v18 rows store only the
internal `taskgov-owned-verification-subject-v1` value in the retained column.
Target fields reuse the existing four-way matrix.
Composite indexes support project/Task/current Contract/expectation/target
gate reads without one query per Receipt. Update/delete triggers preserve the
append-only boundary. A unique project/Task/target-generation key permits one
aggregate Receipt and makes retry require an explicit fresh target.

The same migration adds three internal fields to `task_completion_cycles`:

```text
verification_basis_version verification_expectation_digest
verification_receipt_id
```

Existing rows receive version 0 with null digest and null Receipt link. New
native cycle insertion is constrained to version 1 and stores the same domain-
separated digest of its exact Task verification text, including empty text and
preserved whitespace. The `caller_attestation` arm requires a linked Receipt;
the `not_required` and `runner_observation` arms require a null Receipt link and
are constrained by the shared schema-v21/v22 verification-basis union. The Receipt ID
is a nullable foreign key; repository and projection validation additionally
prove identical ownership, Contract, stored digest, target tuple, and
`pass/full` qualification. Existing cycle immutability covers all three
fields. A migration-owned insert guard permits version 0/null/null only for the
existing sole `legacy_current_done` partial reopen bridge when a legacy-
unknown done Task has no cycle; every normal native insert requires version 1.
None of the internal fields is added to the public completion-history
projection.

Migration 18 adds subject-basis version and nullable authority-snapshot and
verification-criterion IDs to both Receipts and cycles without rebuilding or
updating old rows. Existing rows read as subject version 0/null/null. A native
Receipt and nonempty native cycle use version 1 with matching capture IDs;
trimmed-empty verification uses version 1/null/null and no Receipt. Triggers
and readers enforce same-project/Task snapshot-to-criterion membership and the
exact target binding.

The expectation digest is:

```text
sha256("taskgov-verification-expectation-v1\0" + exact stored UTF-8 verification)
```

Its stored representation is the lowercase 64-hex digest. It binds eligibility
without storing another copy of verification prose. The public projection
never emits the digest. The complete target tuple is copied
from the locked Task; no caller target field exists. `created_at` and ID are
allocated under the writer. Structural validation reuses the existing target,
timestamp, privacy, signed-integer, and ownership validators.

The activated Skill sequence is exact material, existing target set with its
returned generation and closed route retained, review prepare/record, then
completion. Only `verification_route=receipt_required` makes a marker-`0` or
exact closed no-launch fallback branch additionally run external verification
and add a Receipt with that expected generation. `not_required` and
`runner_pass` do not; `blocked` stops with its returned existing blocking code.
Target setting remains before either verification branch. The manual/fallback
path is bounded by 10 calls, or 11 with Effort Advisory; the Runner-pass path is
bounded by 9 or 10 respectively.

### Receipt Write And Freshness

The write service first validates result, duration, coverage, and positive
`expected_target_generation` without opening a writer, then starts
one short immediate transaction and rereads schema,
identity/binding, Task, Contract pointer, exact verification text, and target
tuple. It permits only in-progress or review-pending Tasks with specified
verification and a current target. Expected generation must equal the locked
target generation; drift returns `verification_basis_stale` without storage.
Capture version must then be 1 or the write returns `evidence_basis_stale`.
It derives the subject from the locked snapshot/criterion, computes the digest,
appends the Receipt and Evidence Reference without changing the Task or adding
an event, commits, and only then invokes backup-eligible, Viewer-ineligible
post-commit maintenance.

The caller's add invocation attests that the external run exercised the copied
target; taskgov does not prove that claim. The caller supplies no command body,
source revision, Contract revision, timestamp, ID, exit code, output,
exception, or arbitrary result document. A failed or timeout row records
evidence only and performs no Task/status/Contract/target/review/completion/
Handoff mutation. A second call in the same generation returns
`verification_receipt_already_recorded`; the caller can inspect `task show`,
then explicitly set a fresh target before another run.

Parser and common state errors retain their existing owners. The Receipt
service owns one ordered validator matching the specification: normalized
caller fields, Task existence, done/disallowed status, nonempty expectation,
stored target structure/presence, expected generation, capture version, then
the current schema-v22 Runner selector, then uniqueness. It is
used before and after the writer lock so a concurrent status, Contract,
expectation, or target change cannot select a different semantic ordering or
bind the row to new material. CLI formatting owns the exact three-line success
text and adds no synthetic Task event.

No label or subject argument exists. Version-aware stored validation applies
the legacy command-label predicate only to subject-zero rows and requires only
the fixed internal value for subject-one rows; public projection emits the
versioned subject union and never that internal value.

One shared Receipt evaluator classifies a row as exact-current only when project,
Task, Contract revision, expectation digest, and every target field equal the
coherent current basis. The sole completion selector delegates to this evaluator
only for marker `0` or the exact closed no-launch fallback; the qualifying Runner
branch and all other marker-`2` states use the closed selector matrix above. The
manual arm computes, in order:

```text
specified expectation + no exact-current Receipt
  => verification_receipt_required
specified expectation + Receipt other than pass/full
  => verification_receipt_blocking
specified expectation + pass/full
  => satisfied
unspecified expectation
  => receipt gate not required; existing attestation remains required
```

A nonempty capture-version-zero target instead reports
`evidence_basis_stale` before Receipt-required/blocking evaluation. Completion
check applies the same stale result for every capture-zero target because any
completion creates a new ledger source.

Old-target, old-Contract, and old-expectation rows remain visible only in the
bounded recent audit list and never affect that result. Any current non-
qualifying Receipt is retired by an explicit fresh target generation, not by
row update or delete.

A semantic edit of `tasks.verification` with a started target uses the existing
Contract-invalidation shape in the same Task transaction: clear typed
completion evidence and target kind/value/base, increment generation with
overflow protection, update the Task, and append the normal bounded event.
With no explicit status, review-pending moves to in-progress. Explicit active,
pause, block, or cancellation status follows ordinary transition validation;
explicit review-pending/done is rejected until a fresh target exists. The same
edit rejects caller completion-evidence options instead of applying and then
silently clearing them. Receipts and review rows remain immutable and become
historical. A same-content edit is a no-op under existing edit rules.

### Completion And Read Integration

The completion basis snapshot adds the current selected verification gate
summary and its nullable Receipt or Runner-observation basis. Outside-Git
preflight and locked revalidation both run the same sole selector. Ordered
validation keeps the explicit `verification_required` flag check and target
validation first, then applies the selected Runner/manual gate, then evaluates
current review findings/receipts. `task complete
--check` adds the two Receipt codes to its bounded allow-list; check never
writes or reserves a Receipt.

For specified verification, the manual arm proves that the unique current
Receipt is `pass/full` and inserts its Receipt foreign key; the qualifying
Runner arm proves the exact selected observation and inserts its Runner pointer
with a null Receipt link. Both then insert the version-1 cycle with the identical
expectation digest, Task update, and linked completion event in one transaction.
A native completion whose verification text is empty
after trimming inserts version 1 with its exact-byte digest and a null Receipt
link; the exact empty string uses the empty-text digest. Any ownership,
target, digest, link, or gate drift rolls everything back. Existing
`verification_attestation=true` remains in the cycle, so the Receipt
strengthens rather than silently replaces the current explicit assertion.
Version-0/null/null marks only migrated cycles and the sole exact compatibility
bridge. A version-1 nonempty cycle whose stored tagged arm has neither its valid
linked Receipt nor its valid qualifying Runner observation is
`completion_history_inconsistent`, never an inferred legacy success.

The one Receipt command is `verification receipt add`. Its success data is
exactly `receipt`. `task show` obtains Receipt totals, exact-current
counts, gate, and newest 10 rows in the same query-only transaction as the
Task and adds the fixed `verification_evidence` object. The formatter exposes
only the specification's public allow-lists; no separate list/show/import/
export command or pagination is added.

Canonical state resolution retains its existing global admitted-row validation.
After that boundary, the `task show` handler reads only the selected Task for
Runner routing and builds the complete show projection once. Marker `0` and
done history use that same Task-local connection; only live marker `2` releases
SQLite for physical selection and opens the final projection connection. No
branch adds a second global Runner-graph validation.

Recent rows replace the old top-level label with the five-key versioned
subject union. The parent read model adds `current_verification_subject`, null
without a capture-v1 verification criterion. Subject-zero done cycles retain
their exact v17 link/label rules; reopen clears current capture and requires a
fresh target.

The read model emits the exact types and nulls fixed by the specification.
Empty marker-`0` expectation is not required and is satisfied. A nonempty
active Task first reports target-required or stale basis; its selected manual
arm then reports Receipt-required, Receipt-blocking, or satisfied, while its
qualifying Runner arm reports satisfied with a null Receipt ID. A done Task
backed by a matching version-0 cycle reports the
explicit legacy exemption even when its target is absent, including the sole
bridge-created cycle. A version-1 done cycle is validated against its stored
manual or Runner branch before projection. Task-show failure data includes a null
`verification_evidence`; text Task show remains unchanged. The Receipt write
alone has the fixed three-line text projection.

The initial completion-history JSON remains unchanged. The current done Task
retains its target and Receipt, so `task show.verification_evidence` exposes
the current qualifying basis; the cycle target tuple also identifies it.
Reopen advances the target and makes it historical. A later history projection
would require separate evidence and approval rather than silently changing
Viewer snapshot v4.

### Schema, Viewer, Packaging, And Tests

Migration 17 `verification_receipts` creates the Receipt table, indexes, triggers,
the three completion-cycle basis columns and insert/link guards, and the schema-
history row. Existing cycles receive only version 0/null-digest/null-link as a
structural legacy discriminator. It never parses verification text or events
to infer runs or create Receipt evidence. Reentry validates the exact objects,
columns, constraints, immutable triggers, ownership/link relations, and
absence of invented Receipt rows. Migration 18 adds the capture/provenance/
subject tables and columns described above, preserves exact v17 projections,
and creates no historical evidence. Migration 19 adds Bundle/link/snapshot and
projection state plus cycle version/null fields without inventing a historical
Bundle. Migration 20 adds the audit-only Runner-storage objects and Bundle-v2
tagged union without inventing Runner or historical evidence. Migration 21
widens only the frozen gate-basis discriminators without promoting audit
history. Migration 22 removes retired Evidence reservations and adds the
source-22/v2 Bundle arms while preserving sealed history. Old binaries reject newer schemas normally; setup remains the sole
public migrator. The established reopen bridge remains the only
post-migration writer of the exact version-0/null/null legacy shape.

Viewer source compatibility accepts v5-v22, while snapshot v4 fields and UI
remain unchanged. Receipt writes are Viewer-ineligible. The existing bounded
batch completion-history
loader performs bounded joins for selected version-1 cycle Receipt links and
v18+ subject/provenance/manifest/Reference relations plus the v19-v22 Bundle
discriminator and source-aware Runner basis, validates them, and
discards every ledger field before snapshot formatting. There is no Receipt dataset,
snapshot field, panel, filter, or detail, and no per-Task Receipt query. This
compatibility update must land with the schema bump so explicit setup and later
unrelated Viewer maintenance cannot fail merely because the canonical state
migrated.

Focused tests reuse one matrix owner for result/coverage/current-binding gate
cases and existing migration/completion/Task-show helpers. They cover all
source schemas v1-v22, rollback/reentry, no legacy synthesis, exact target and
Contract invalidation, semantic verification edit, failure-generation reset,
unique per-generation ownership, version-0 legacy exemption, version-1
Receipt-link enforcement, the sole post-v17 legacy bridge, cycle-target
reconstruction, concurrent target/edit drift and expected-generation
rejection, read-only no-write, privacy rejection, byte/count bounds,
Evidence/backup maintenance, Viewer v22 compatibility including valid/corrupt
link, Bundle-discriminator, source-appropriate verification-basis, and Runner-
graph batch validation, parser/help/
output, and unchanged unrelated projections. Test
facts and command inventories must remain derived from their existing owners
rather than copied into CI or multiple test modules.

## Task Contracts, Checkpoints, Handoffs, And Effort

### Immutable Task Contract Revisions

Schema v8 gives Tasks a current revision pointer and adds append-only
`task_contract_revisions`. Revision 0 has no row and projects empty fields.
Each positive row stores normalized scope, acceptance, optional constraints,
stable authority reference, change reason, and timestamp. Repository reads
require the pointer to reference the latest same-project/same-task revision;
the shared Contract-pointer boundary enforces this before Contract projection or
Task-backed lifecycle use.
Scope and acceptance are each capped at 4,000 characters, constraints at
2,000, authority reference at 500, and change reason at 1,000.

Supplying any Contract option supplies the group and requires scope and
acceptance. Initial Contract recording is allowed:

- on Task add only for ready, in-progress, blocked, or review-pending; or
- for an existing revision-0 Task only in the exact ready/blocked to
  in-progress activation, with empty completion/review state and no companion
  mutation.

Initial change reason is empty. Later revisions are Contract-only edits while
ready, in-progress, paused, blocked, or review-pending and require a semantic
scope/acceptance/constraints change, bounded non-empty reason, and stable
authority reference. Done must reopen and cancelled rejects Contract input.
Authority can name a revisioned governing file/decision or exact
`user_instruction:<task-id>:<next-revision>`; raw prompt text and current Task
output are not authority.

Normalization converts CRLF/CR to LF and strips outer whitespace while
preserving internal text. Omitted later constraints retain the current value;
explicit empty removes them. Exact semantic replay returns the current
revision without write even if authority/reason labels differ. A real change
allocates the next revision under the writer, updates the pointer, resets all
completion evidence, clears/advances any started review target, moves
review-pending to in-progress, updates time, and appends a content-free
`contract_revised` event atomically. Old Contracts and review history remain.

Stored Contract projection has one narrow legacy M19.7 seam: only
`constraints_text` is validated through the bounded legacy reader and returned
unchanged. Normal Contract input never selects that reader. When later
constraints are omitted, the established carry-forward rule may copy those
already-validated bytes into the new immutable revision; this preserves
lineage and supplies neither caller-input acceptance nor authority.

Concurrent identical input records once and replays; different valid input
serializes into successive revisions. Current-or-next user-instruction
placeholders are rebound to the locked allocation. A lost-response retry with
the older placeholder may replay; an unrelated placeholder cannot authorize a
new semantic change.

### Typed Checkpoints

`task checkpoint` requires bounded summary and next action and accepts at most
eight bounded unresolved risks. Limits are 1,024 UTF-8 bytes for summary and
next action, 512 per risk, 4,096 aggregate risks, and 6,144 for caller input.
One append-only row stores those fields, Task/project, current Contract
revision, and time. The same transaction adds fixed event
`checkpoint_recorded` / `Checkpoint recorded` without content and does not
change `tasks.updated_at`.

Exact replay of the latest same-Contract checkpoint is no-write. Done Tasks
reject it. Current/show expose only the latest checkpoint; Viewer excludes
checkpoint content. A checkpoint is optional and changes no Task status,
scope, selection, review, evidence, or completion gate.

Only stored checkpoint `summary` projection may use the bounded M19.7 reader
for the former numeric `dispatch_authorization` JSON field. It returns the
original canonical stored summary and writes nothing. New summaries and all
other checkpoint fields use the ordinary privacy path.

### Local Handoff Outbox

Schema v7 owns `handoff_records` with source Task/current Contract revision,
canonical idempotency key, optional explicit occurrence ID, bounded
summary/rationale, state, adapter/delivery metadata, internal claim lease,
bounded receiver receipt, and withdrawal data. Public states are:

```text
pending_handoff
handed_off
handoff_withdrawn_by_user
```

The state matrix requires pending without terminal timestamps, handed-off with
acknowledgement time and no withdrawal, and withdrawn with user reason/time,
no receiver receipt, and zero delivery attempts. Claim tokens are never
public. Every stored free-form field and matrix is revalidated before output;
corrupt/private stored content returns a fixed internal error rather than
redaction.

Canonical compact JSON over project, source Task, source Contract revision,
normalized summary/rationale, and occurrence ID is SHA-256 hashed as the
unique idempotency key. Omission canonicalizes to empty; an explicit occurrence
must come from explicit user instruction or deterministic external identity.
Summary and rationale are each capped at 1,000 characters and occurrence ID at
200; an explicitly empty or invalid occurrence is rejected rather than treated
as omission.
Exact replay returns the row and writes nothing. The local transaction commits
before any possible delivery; success reports durable only after commit.
Handoff never changes source Task state, acceptance, timestamps, events, or
selection.

List defaults to pending, oldest-first, limit 20/max 100, with exact
`total_matching` and rows in one snapshot. Terminal states require explicit
filter. Show uses the full public allow-list, while Task show exposes only
per-state counts. Withdraw requires pending, zero attempts, no claim ever, and
a sanitized user reason; it is a single immediate transaction.

The shipped product has no receiver and no public sync command.
`adapter_enabled=false`; pending records remain durable and rediscoverable.
The schema reserves a local versioned idempotent claim/delivery state machine,
but a concrete Issue adapter remains blocked until a separately approved
local intake contract exists. Task Skill never performs Issue triage,
priority, lifecycle, network/GitHub access, arbitrary code loading, shell
execution, or Issue-database access.

Any later sink must use stable `handoff_id` as receiver idempotency key and
return only accepted/retryable/permanent plus a bounded receipt. Claims use
compare-and-swap and expiry; an ever-claimed row is never withdrawable.
Retry stages are fixed 60-second then 300-second waits, then exhausted, with
permanent error terminal for delivery attempts. This reserved state does not
add a normal Task-loop call until a real adapter is approved.

### Optional Effort Advisory

`effort.py` reads only strict optional
`<skill>/config/effort-advisory.json`. Version 1 accepts exactly profile
`informational-v1`, explicit enabled, and thresholds for five fixed metrics.
Missing or valid disabled configuration is off; invalid present configuration
is disabled with a bounded continuation diagnostic. There is no generic
configuration store, inheritance, environment override, writer, or configured
command runner.

Thresholds are nonnegative JSON integers for only changed files, changed
lines, changed modules, Contract revisions, and handoffs, and exceed only when
the measurement is strictly greater. An invalid profile uses the fixed
continuation warning; a threshold result uses at most one warning with code
`effort_advisory_threshold_exceeded`, key
`effort_advisory.threshold_exceeded.v1`, and fixed non-content message.

When enabled, the first transition to in-progress may best-effort capture
canonical Git basis, cleanliness, timestamp, project/subject activity
generations, and whether another task was active. Git observation is
argument-vector, optional-lock-disabled, no-lazy-fetch, fsmonitor-disabled,
submodule-ignored, no-external-diff/text-conversion, and read-only. Capture
failure stores no partial basis and never blocks start.

Metrics are changed files, changed lines, modules, Contract revision count,
and source-task handoff count. Attribution is unknown for non-Git, dirty or
uncertain endpoints, incomplete line coverage, or activity evidence of
overlap. Untracked/binary content makes line coverage unknown instead of
guessed. With a basis, pre/post DB observations are separately coherent around
Git; the post-read refresh detects overlap without keeping a transaction open.
Strict-off databases do no advisory bookkeeping.

The advisory never writes acknowledgement, asks a question, chooses a
handoff, changes status, expands scope/acceptance, or blocks completion.
`task show` mechanically exposes enablement. The Skill calls `task effort`
once at the existing verification/review boundary only when enabled.

## Approved TG-M16 Reduced Loop Discipline Trial Design

The existing Effort result chooses:

```text
valid enabled profile AND a nonempty ordered exceeded list
  => suggested_action=reconcile_scope
otherwise
  => suggested_action=continue
```

The same value appears in the one existing threshold warning. Attribution and
unknown reasons remain evidence only: unknown without an exceeded threshold
continues; an exceeded threshold with unknown attribution reconciles. This
adds no metric, profile field, database write, stored acknowledgement, routing
framework, or Task/handoff/review operation.

Reconciliation is session-local guidance, not persisted state.
`references/reconciliation.md` is loaded only after `reconcile_scope` or a
repeated test/review failure. After two materially equivalent failed repairs,
a third equivalent execution is prohibited without new evidence. Renaming a
wrapper, command, directory, Task, or execution unit is not new evidence; a
diagnostic is new only when its result can materially change the causal
hypothesis, authorized repair, or expected result. A fresh session resets the
comparison and relies on durable Task/event/review/handoff state.

Tests are never weakened merely to obtain PASS. A failing test or Effort
signal is evidence, not authority. A test may change only when current
authority establishes it is wrong; a Task Contract or acceptance change still
needs later explicit authority.

Scope reconciliation reuses the existing three-way classifier: work stays in
the current Task only when accepted scope and current authority cover it;
other discoveries go to local handoff; a blocker is used only after safe
authorized repair for the affected Task/lane is exhausted. Paused remains an
explicit temporary interruption, and unrelated ready lanes continue.
Remaining user decisions are batched.

Review remediation starts from the current blocking receipt/finding. A
meaningful fix sets a fresh target and obtains a fresh current-generation
result. A result that remains blocking counts as one unsuccessful cycle;
completion still requires fresh qualifying PASS receipts. After two
materially equivalent unsuccessful cycles without new evidence, no third
equivalent cycle runs; the same bounded blocker/decision path applies.

No M16 setup stage, Task seed, policy version, instruction-chain inspection,
target `AGENTS.md` mutation, persisted counter, alternate state machine, or
normal-loop call exists.

## Setup, Doctor, Backup, And Maintenance

### Shared Scope Preflight

Setup and doctor validate a physical target directory, canonical package,
layout, state ownership, and package identity. If the target or an ancestor
has a `.git` marker, `project_scope.py` runs exactly one bounded, shell-free
effective-ignore command before SQLite:

```text
git -C <governed-target> -c core.fsmonitor=false \
  check-ignore --quiet --no-index -- <target-relative-state-directory>
```

The operand is exactly the ordinary
`.agents/skills/task-governance-tool/state/` or self-host
`task-governance-tool/state/`, with forward slashes and directory semantics.
The process uses safe Git environment, null stdin/stdout/stderr, and a
two-second timeout. Return 0 is accepted; every other result is
`state_ignore_required`. No marker means a valid non-Git target and no
process. The governed target is never re-rooted. Normal task/handoff/review
commands perform no ignore process.

### Doctor

Doctor combines:

1. bounded package-manifest inspection;
2. the conditional one-process ignore observation; and
3. at most one journal preflight and coherent project snapshot.

It does not claim cross-source atomicity. `project_state` owns layout and DB
readiness. When unavailable, task/handoff/maintenance details use fixed
unavailable objects. Package modified/unknown is advisory even when project
state produces exit 2. Relocation mismatch is a successful
`relocation_required` project-state advisory, never a token. Every recognized
advisory has `suggested_action=continue`; doctor creates no lock, directory,
sidecar, database, event, backup, Evidence, or Viewer.

The maintenance projection adds one fixed `evidence` object beside backup and Viewer with `code`, `due`, `source_generation`, `published_generation`, `last_success_at`, and `last_outcome`; doctor reads stored facts only.

The coherent project snapshot loads complete Task rows once and validates the
whole batch with the shared stored-Task/Contract validator before computing Task
counts. On a
Task fault, doctor maps project state to `unreadable`, every other
project-backed component to `unavailable`, and setup eligibility to false;
the package observation remains independent.

The package inspector strictly parses manifest v1, caps it at 256 KiB and 512
core entries, rejects unknown/duplicate keys, invalid identity/version/origin/
hash/path, traversal/backslashes/absolute paths, casefold collisions, and
entries in excluded regions. It enumerates physical regular core without
following links. Only root `config/`, `adapters/`, generated `state/`,
`__pycache__/`, and `*.pyc` are excluded. Files are streamed with per-file and
aggregate bounds and pre/post identity checks.

Results are `clean`, `modified`, or `unknown`; paths are bounded relative names
sorted and capped to 20. No content, expected/actual hash, absolute path, link
target, or OS error is emitted. The co-located manifest is unsigned and is a
local drift detector, not authentication. Doctor never downloads, repairs,
updates, or compares upstream state.

### Setup Plan And Stages

Read-only setup returns a deterministic plan for restore, initialization,
migration backup, migration, maintenance configuration, Evidence publication, and Viewer publication
plus effective backup interval/retention. It creates no directory, lock,
temporary, sidecar, SQLite recovery connection, or HTML.
Setup serialization adds `evidence_status`; its ordered write list places `evidence_projection_publish` after maintenance/binding and before `viewer_publish`.

Write setup revalidates scope immediately before each irreversible stage. A
completed stage is not rolled back because a later stage fails; the exact
ordered partial result makes rerun resume. Initialization, migration, recovery,
relocation, maintenance opt-in, and Evidence/Viewer publication use services directly,
not removed CLI subprocesses.

When canonical DB is absent, setup scans only canonical managed backups:

- no managed name => fresh initialization;
- at least one eligible same-project/current-binding generation => newest
  eligible `(published_at, generation_id)` recovery while the mechanically
  newest generation remains the structural head;
- a structurally coherent set whose current-binding candidates are all locally
  rejected only for stored Task-verification privacy/capacity =>
  `setup_restore_failed` without initialization; and
- a structural, identity, binding, lineage, metadata, repository, retention,
  sidecar, or set-envelope failure => the specific resolver result where
  applicable, otherwise `project_state_unreadable`.

Invalid, foreign, linked, or unrelated files are preserved. Once a recovery
candidate plan exists, drift, copy, normalization, or no-clobber publication
failure maps to `setup_restore_failed`. Recovery holds the artifact lock,
revalidates the candidate, copies it through SQLite backup to a
sibling temporary DB, reconciles only supported backup metadata, validates
schema/identity/quick/FK/regular-file state, and publishes no-clobber. A
lexical rollback journal beside a missing canonical DB is rejected before
selection and publication and never opened/deleted. A concurrently appearing
canonical DB is never overwritten.

A supported old recovery migrates normally; current recovery proceeds to
Evidence and then Viewer. Fresh initialization repeats candidate/journal absence under the lock
immediately before creation. Setup never silently substitutes initialization
for a stale recovery plan.

### Maintenance Policy And Managed Backups

Schema v10 has one `project_maintenance` row with immutable one-way
`enabled_at`, backup interval/generation policy, applied retention, shared
backup success/outcome/latest generation, and Viewer success/outcome. Setup
defaults a new policy to 30 minutes and three generations. Public bounds are
1-1,440 minutes and 1-20 generations. Omitted values preserve existing
policy; equal explicit values replay. A policy-only change does no copy/prune,
and a reduced retention becomes applied only with the next successful
publication.

The sole backup primitive:

1. opens a rollback-journal source through the operational boundary;
2. copies with `sqlite3.Connection.backup` to a fresh contained temporary;
3. validates schema support, identity/binding, quick check, and foreign keys;
4. closes it;
5. atomically publishes a canonical generation with fixed
   publication-retention metadata; and
6. returns only bounded generation/time metadata.

Within one copy attempt, the source, temporary-copy, and immediate
pre-publication source validations may reuse successful privacy checks keyed by
the exact mode, field, and stored value. The attempt starts with an empty cache,
never caches a rejection, and still repeats every schema, row, digest,
relationship, identity, quick-check, foreign-key, and current-source validation
at all three phases. For routine backup only, after all three validations and
the generation-row record succeed, a frozen copy of those exact successes may
seed the immediately following post-publication reconciliation. Each retained
artifact receives an independent mutable copy; artifact-local successes or
rejections never flow back to the seed or another artifact. Every cache is
cleared on success or failure. Pre-publication reconciliation, recovery,
Viewer, setup and its reconciliation, ordinary discovery, and later backup
attempts never receive that seed.

Managed names are exactly:

```text
taskgov-backup-v1_<YYYYMMDDTHHMMSSZ>_<32hex>_r<1-20>.sqlite
```

The generation ID is `tg_backup_<same 32hex>`. Other names and nonregular,
linked, invalid, or foreign files are never managed or pruned.

Schema v11 `managed_backup_generations` is the authoritative retention set.
Every setup/routine backup under the same zero-wait artifact lock reconciles
at most the bounded file/row crash residue: import one valid file-only
generation, remove an unusable row target without following an untrusted path,
and complete file-before-row pruning using applied retention. Publication
writes the file, then inserts its row and updates pointer/outcome/applied
retention in one short transaction, then prunes file before row. Failure leaves
the work due. Crash residue is bounded to the prior valid set plus one
in-flight generation.

Setup migration always makes the pre-migration managed backup before changing
schema and binds metadata/pointer before pruning. Rollback therefore means
restoring a matching package, database, and artifact set together; no reverse
migration exists.

### Post-Commit Coordinator

Business services retain internal `MutationOutcome(changed, viewer_relevant)`. After commit and connection close, every changed mutation may retry due Evidence projection; the coordinator runs:

1. Evidence projection when due;
2. Viewer refresh when relevant; then
3. one backup attempt when due.

They are independent same-process bounded work, never a thread, daemon, timer,
queue, scheduler, service, sleep, or retry loop. Each uses its own zero-wait
one-byte OS lock; lock-file existence is not ownership. Read, error, replay,
no-op, configuration-only setup, doctor, Effort, and maintenance metadata
writes do not invoke it. Runner success and fallback retain the one original
target-set coordinator pass; a post-T1 Runner error does not invoke maintenance
and leaves the already-advanced due state for a later normal opportunity or
setup repair. Runner-internal writes never invoke a second pass.

Backup is due when no success exists, the last outcome is deferred/failed, or
the configured interval elapsed. Failed attempts do not advance success.
Eligible mutations are Task add/edit/complete/checkpoint, handoff
record/withdraw, and review target/receipt/finding add/resolve. Handoffs are
not Viewer-relevant because the snapshot excludes the outbox. Verification
Receipt add is backup-eligible and Viewer-ineligible because the snapshot has
no Receipt dataset or field.

Only completion-cycle insertion increments Evidence source generation in its business transaction; a later changed mutation may retry an already-due projection without a third outcome flag. Viewer-relevant mutations increment source generation through `task_events` in the same transaction. Viewer refresh renders and rechecks once at most; setup uses direct Evidence/Viewer stages and does not re-enter the coordinator.

Only fixed warnings are added after a successful primary command:

```text
evidence_projection_deferred | evidence_projection_failed
viewer_refresh_deferred | viewer_refresh_failed
backup_deferred         | backup_failed
```

Messages state that the primary result is unchanged and include no path, exception, hash, raw output, retry, stop, or model choice. Doctor observes the latest outcome without starting work.

## Static Viewer

### Snapshot And Publication

The Viewer is a replaceable projection, never authority:

```text
committed Viewer-relevant event
  -> source generation
  -> post-commit coordinator
  -> zero-wait Viewer lock
  -> one compatible query-only snapshot
  -> snapshot v4 + captured generation
  -> base64 UTF-8 JSON in bundled template
  -> atomic task-viewer.html replacement
  -> short rendered-generation update
```

Setup invokes the same canonical renderer directly. There is no public Viewer
command, output choice, browser launch, server, or browser-to-SQLite path.

The repository selects complete rows for all project Tasks in list order and
validates them as one source-schema-aware stored-Task/Contract batch before any
Task, review, or history projection. Exact v18-v22 snapshot validation first
validates the complete Task batch as part of the full Evidence Ledger, then the
same-transaction list-order query consumes the private proof above instead of
repeating scalar, privacy, and Contract validation. Sources v5-v17 and the
ordinary Viewer repository entry still validate the selected batch directly.
Validation failure occurs before rendering or replacement and preserves the
last-good HTML. It then selects at most 10 newest events per Task by time/rowid,
review evidence through the shared gate read model, and bounded completion
history. Snapshot v4 contains snapshot/source schema versions, generated time,
project ID/display only, counts, and explicit Task/event/evidence/history allow-
lists. It excludes repository/database paths, tool events, handoffs,
checkpoints, maintenance state, internal generation, event-cycle links,
environment, and raw review material.

Source schemas v5-v14 synthesize empty/incomplete completion history;
v15-v22 use stored cycles, reading completion histories in batches of at most
500 Task IDs. Sources v17-v22 validate version-1 cycle Receipt links; v19-v22
also validate and discard Bundle linkage, and v20-v22 validate the source-
appropriate closed verification basis and Runner graph. Sources v21 and v22 validate
the complete tagged union before discarding every Runner field. Every snapshot
reports its actual source schema and selects all
project Tasks; 500 Tasks is the accepted performance fixture rather than a
selection cap. The rendered artifact is capped at 64 MiB.

Snapshot JSON is deterministic UTF-8 and base64-encoded before insertion.
The template has exactly one snapshot placeholder and one decimal refresh
interval placeholder. Stored data reaches the DOM only through `textContent`
or text nodes, never HTML/eval/URL/style/event sinks.

The canonical output and lock remain below fixed state. Resolution rejects
reparse parents, containment changes, DB aliases, and linked/nonregular
destinations. Rendering writes a unique sibling temporary, flushes/closes it,
and `os.replace`s only after complete success; failure retains last-good HTML.

Schema v13 `viewer_maintenance_state` holds nonnegative source generation,
nullable rendered generation not above source, and fixed
`succeeded|deferred|failed` outcome/time. New/migrated state starts source 0
and rendered null. Under the lock, publication captures generation and rows in
one snapshot, closes SQLite, renders/replaces, then conditionally records the
captured generation without lowering a newer value. One recheck permits one
follow-up; later churn remains due.

### Presentation Profile

The only presentation policy is optional:

```text
<physical-skill>/config/viewer.json
```

Absence is valid and disables reload with interval 0. Taskgov never creates or
edits it. A present regular physical UTF-8 file is capped at 16,384 bytes and
must contain exactly:

```json
{
  "schema_version": 1,
  "profile": "visibility-refresh-v1",
  "refresh_interval_seconds": 30
}
```

The interval is a JSON integer 5-3,600; booleans, floats,
duplicate/unknown/missing keys, malformed/trailing JSON, links/reparse paths,
devices, directories, replacement races, or uninspectable metadata fail
closed with one sanitized error. The loader uses no-follow where available and
checks descriptor/path identity before and after the bounded read. Doctor does
not inspect this file.

One publication attempt loads the value once and reuses it for both possible
renders. Setup preview treats an invalid present profile as Viewer
repair-required but writes nothing; write setup and routine publication retain
last-good output and use existing incomplete/warning semantics. Missing or
changed profile/template bytes make setup repair the canonical page.

### Browser Application And Reload

The page provides a compact header, all-status summary, search/status/kind/
lane/priority/tag/terminal filters, responsive Task table, detail/events, and
empty state. Terminal Tasks are hidden by default but filterable. Native
controls, visible focus, labels, non-color status cues, bounded radii, and
wrapping support keyboard and narrow-screen use.

Reload scheduling activates only after successful fatal-UTF-8 decode and
render, with a valid nonzero interval and `file:` protocol. One reconciliation
function owns one timeout:

1. clear the prior handle;
2. stop if disabled, already requested, non-file, or hidden;
3. compute elapsed from page-load `performance.now()`;
4. request exactly one same-document reload once elapsed reaches interval; or
5. schedule only the monotonic remainder.

Timeout and visibility-change callbacks reuse that function. Hidden pages own
no timer. There is no interval timer, polling, wall-clock interval
calculation, fetch/XHR, storage, worker, database access, retry, or message
channel. Browser throttling may make reload late, never early.

Immediately before that automatic reload only, the page may make a one-shot
History API handoff with exact ordered keys:

```text
owner, schema_version, captured_at_ms, status, kind, lane, priority, tag,
terminal, selected_task_id, scroll_x, scroll_y, focus_id
```

Owner is `taskgov-viewer-auto-reload`, schema is 1, and compact UTF-8 JSON is at
most 4,096 bytes. Only allowed filter values, visible selected Task ID,
nonnegative finite scroll coordinates, and one of eight fixed control IDs may
be saved. Search text, Task/snapshot content, arbitrary selectors, URLs/paths,
dynamic-row focus, selection ranges, and nested UI state are prohibited. No
selection means no envelope and reload still proceeds.

Capture time is a nonnegative safe integer; status, kind, and priority are
empty or current enums; lane and tag are each at most 1,024 UTF-8 bytes;
selected Task ID is nonempty and at most 128 code points; coordinates are
finite integers from 0 through 2,147,483,647; and focus is empty or exactly
`search-filter`, `status-filter`, `kind-filter`, `lane-filter`,
`priority-filter`, `tag-filter`, `terminal-filter`, or `reset-filters`.

Save never overwrites a non-null non-owned state and calls only
`history.replaceState(candidate, "")` without URL. Failure never prevents
reload. On a file load, every owned state is cleared before snapshot decode,
even if malformed, stale, non-reload, or decode-fatal. Restore requires
successful clear, navigation type reload, exact keys/types/bounds/current
options, visible selection, current owner/version, and age 0-300,000 ms.
Invalid or failed restore returns filters, selection, focus, and scroll to
defaults.

On an enabled file page or one starting with owned state, manual
`history.scrollRestoration` must be set and read back before UI restoration.
If unsupported, state save/restore is disabled but owned state is still
best-effort cleared and reload continues. History state is browser-managed and
may survive session restoration; it is bounded and one-shot, not described as
memory-only.

The page never uses `pushState`, URL/query/fragment state, cookies, Web
Storage, IndexedDB, Cache API, service workers, cross-tab messaging, or manual
reload capture. It logs no state, bytes, validation reason, exception, URL, or
path. Saving/restoring must change neither `history.length` nor
`location.href`.

### Browser Security

The exact CSP is:

```text
default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; media-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; manifest-src 'none'; base-uri 'none'; form-action 'none'
```

Inline script/style is limited to the fixed single-file application;
`unsafe-eval` is absent. Stored values cannot create markup, script, style,
event handlers, URLs, or resource loads. The browser has no external URL,
telemetry, automatic launch, network API, or database-write code.

## Privacy, Safety, And Failure Boundaries

Every free-form input uses the common deny-by-default privacy guard. Fields
with defined contracts also have code-point/UTF-8 bounds; the existing `lane`
and `blocked_reason` fields are privacy-validated but have no numeric character
cap. `lane` is trimmed; `blocked_reason` is retained as supplied after
string/privacy and state validation. The guard rejects likely credentials and raw dumps,
including authorization/Bearer headers, private-key blocks, password/token/API
key assignments, Python traceback headers, raw stdout/stderr headings, and
repeated raw Git diffs.

The ordinary matcher receives caller text unchanged. It rejects both
`dispatch_authorization=<value>` and the JSON key
`"dispatch_authorization":<value>`, including numeric values; no generic
allow-list or mode exists. Neutral `operation_sequence=<positive canonical
integer>` passes without preprocessing and represents correlation or
idempotency evidence only. External authority remains an independent current
user/Contract decision.

The singular legacy M19.7 stored-text helper creates a privacy-only guard view:
it substitutes bounded lowercase positive-canonical-integer equality and
numeric JSON counter forms with a fixed non-secret sentinel, runs the complete
ordinary detector set, and returns the original text. Call sites are limited
to stored Contract constraints and stored checkpoint summary projection.
Completion history deliberately uses only the ordinary matcher. The helper
does not rewrite a row, broaden a schema or writer, or authorize a Task,
dispatch, Git, network, or other external mutation. Compound credential/token
content remains visible after substitution and fails closed.

Stored or emitted data excludes secrets, cookies, provider bodies,
authorization material, raw stdout/stderr, stack traces, environment dumps,
full prompts/conversations, private reasoning, raw reviews, large diffs,
unsafe paths, and OS/SQLite/Git exception detail. Review, Contract, handoff,
checkpoint, completion, and history projections use explicit allow-lists and
revalidate stored text before output.

Git subprocesses use fixed argument vectors, no shell, bounded timeout, safe
environment, disabled optional locks/lazy fetching, and no target-project
write. Taskgov never creates a commit, branch, PR, Issue, tag, Release, or
network request as product behavior. TG-M19 release operations are repository
release work performed only under their separate approvals, not new Taskgov
commands.

Read-only commands do not create SQLite sidecars, locks, directories, state,
Viewer output, Git changes, or target files. Atomic file publication and
last-good preservation protect generated artifacts. The threat model covers
ordinary misunderstanding, over-implementation, local races, crashes, and
accidental path changes; it does not add signing, hostile-kernel protection, or
general adversarial TOCTOU defense.

## Published Release Artifact And Compatibility Design

The v0.10.0 publication is an external projection of one reviewed repository
commit, not a product runtime subsystem. The exact accepted commit, remote
`main`, and unpeeled lightweight tag `v0.10.0` are
`a9b80ce177a6dead10d51a070b76ff01f7af0294`. GitHub Release `362617903`
has prerelease visibility. Runtime code does not derive current behavior from
a branch, tag, Release, local candidate directory, or historical Task evidence.

The release archive has exactly one `task-governance-tool/` root and is built
from the accepted commit with the package subtree as the sole pathspec. The
package `release-manifest.json` is the inventory and digest boundary for
packaged core files. Root and packaged `LICENSE` bytes are the same official
unmodified Apache-2.0 text, and the package license is manifest-covered.
Generated state, SQLite files and sidecars, backups, locks, Viewer output,
target configuration, source-repository tests and fixtures, root references,
caches, logs, secrets, and scratch output remain outside the artifact.

The published archive and checksum identities are fixed:

```text
archive              task-governance-tool-0.10.0.zip
archive sha256       99fc2345fd036091349c47f7379eee25b8b3b4c8873c0f74aaceac323bb82a03
checksum             task-governance-tool-0.10.0.zip.sha256
checksum sha256      9cdc99bd26cc4887bd88ef2ec638659224f0a0d8f567edb12a3800d59a8b6764
release notes        docs/releases/v0.10.0.md
notes sha256         aaa118a3fbbb261ec6a24f7a80f50f161e606a86857f99e17f957f34ba044a03
candidate CI         run 30561916953, attempt 1
remote-main CI       run 30565181070, attempt 1
```

Release staging, GitHub workflow selection, push readback, and Release upload
are orchestration outside the CLI architecture. They add no taskgov command,
schema table, runtime module, background process, network client, credential
store, or target-project mutation path. A later release may reuse the durable
principles—exact reviewed tree, manifest-complete package, deterministic local
checks, explicit authority for each external mutation, and readback after an
ambiguous write—but must define and approve its own exact identity and
evidence. Completed v0.10.0 approval objects and gate checkpoint schemas never
authorize a future operation.

The release boundary is immutable by policy. A defect creates a reviewed
forward-fix candidate and new version. Routine repair does not force-update
`main`, rewrite history, move or replace a published tag, replace an existing
asset, or delete a Release to hide disagreement.

The accepted upgrade rehearsal treated package files, database, and managed
artifacts as one compatibility point. It created legacy schema-v2 state with
the exact legacy package, preserved that state while installing v0.10.0,
allowed current `setup` to back up and migrate through schema v16, and proved
restart, identity/record preservation, quick check, foreign keys, and Viewer
publication in isolation. The paired rollback restored the matching legacy
package, database, and managed artifacts before running legacy code. Old code
against v16, reverse migration, mixed generations, arbitrary `--db` state,
and a source checkout used as state rollback remain unsupported.

The detailed M19 history switch, candidate staging, evidence schemas, remote
state machine, and just-in-time activation rules were one-time execution
design. The exact publication-commit form is indexed by
[the historical documentation index](history/README.md); no active product or
release guarantee depends on that historical copy.

## Current M25 Select-Split-Merge-Register Design

M25.1, Task `tg_task_8e33e15cd97a28ee`, froze the instruction-layer design in
`docs/specification.md`. M25.2, Task `tg_task_d891cd538d9e7364`, activates it
only in Skill guidance and the task-workflow package reference. The
deterministic CLI, repository interfaces, schema, Viewer, Runner, and command
inventory remain unchanged.

### Session-Local Select-Split-Merge Classifier

The Skill treats one semantic taskization or scope-addition outcome as
one session-local event and classifies it as either `registration` or
`mid_task_scope_addition`. Replies, clarifications, paraphrases, and answers
about that outcome remain the same event; only materially changed authority for
scope, order, or permission creates a new event. An in-scope discovery, test
failure, Effort result, cross-module failure, inferred dependency, task size,
or model preference does not create either event.

For the event, session-local reasoning holds only:

- the complete authorized outcome and unchanged permission boundary;
- explicit Contract facts, binding Review Tier mappings, order, and placement;
- flat candidate responsibilities with their consumed inputs, produced outputs,
  repository-state boundary, local verification/review attribution, and
  existing lane/order representation; and
- concrete fragment-to-owner coupling used by the one global Merge.

These values are not a database record, JSON contract, Task field, dependency
model, parser input, helper-owned worksheet, or prompt-log artifact. A fresh
agent boundary is satisfied when the final group's existing Task Contract,
routed authority, and declared predecessor outputs are sufficient; it has no
numeric token, file, line, or duration threshold.

The classifier runs this fixed sequence once:

1. `Select` the one explicit authority envelope and stable responsibility
   boundaries within it.
2. `Split` once into a flat candidate set whose exact unions conserve the whole
   outcome and explicit permission envelope without omission or expansion, and
   whose sequence uses representable order. A provisional candidate may expose
   missing repository-state or attributable-gate independence so that the Merge
   can attach that fragment to its concrete owner.
3. `Merge` all concretely coupled, fragment-only transitive groups in one global
   pass. Multiple disjoint groups may merge simultaneously; sharing files,
   tests, commands, or fixtures alone creates no coupling. Ambiguous ownership
   invokes the fallback rather than an arbitrary group.
4. Treat the resulting groups as final. There is no recursive classification,
   second Merge, re-Split, parent/child graph, or size-based optimization.

Only final groups must each own one bounded responsibility and its authorized
inputs/outputs, leave a correct repository state after represented predecessors,
carry locally attributable verification and review, and be resumable from
Contract, routed authority, and declared predecessor outputs.

### Registration Adapter And Partial-Add Recovery

For explicit taskization, each final group uses one existing `task add`. The
group's non-zero Contract copies only explicit scope, acceptance, constraints,
and authority reference. Inputs and outputs remain prose in those existing
fields, while order remains existing lane/order; no dependency or worksheet
field is introduced.

If outcome and registration permission are clear but split or Contract detail
is missing, the adapter adds one whole-outcome Task with Contract revision zero.
It asks no per-candidate question. It asks one grouped question, with no partial
write, only when exact user-mandated boundaries conflict with a truthful final
set or outcome/permission is too unclear for one honest whole-outcome Task.

If explicit authority requires an implementation-binding design decision first,
the adapter registers only that design responsibility unless the same authority
already states truthful ordered design and implementation Contracts. A later
implementation Task requires a later explicit registration event based on the
produced design authority; the adapter does not synthesize it.

After a strict subset of an existing `task add` sequence succeeds, the adapter
stops and reads the exact registered set. During the same uninterrupted event,
it compares that read with the still-transient final set, preserves successful
additions and the authorized remainder, then adds only groups proven missing
after the ordinary failure is resolved. It performs no deletion, duplicate add,
repartition, batch retry, or second Select-Split-Merge pass.

If interruption loses the transient final set, the adapter does not reconstruct
or rerun it. It uses existing Handoff only for a truthful bounded remainder
summary, never as a hidden worksheet or registration authority. Any later write
requires current explicit authority; unclear remainder or permission uses the
one grouped question, and a confirmed unsplit remainder may be one revision-zero
Task without changing successful additions.

Registration changes governance state only. It grants no implementation,
target-project, Git, network, or external-system permission.

### Review Tier Resolver

The instruction layer resolves each final group before registration:

1. apply every explicit binding authority floor governing that scope;
2. with no binding mapping, select Tier 2 for schema/migration, JSON contract,
   CLI write behavior, target mutation, privacy/logging, Skill trigger,
   verification/review/completion gate, milestone/plan acceptance, or
   implementation-binding normative documentation;
3. otherwise select Tier 0 only for wholly mechanical meaning-preserving work;
   and
4. select Tier 1 for every remaining scope.

An explicitly authorized higher Tier is permitted. Ordinary registration input
cannot lower a binding floor; only an explicit governance change can change the
mapping. Unknown, size, difficulty, duration, failure count, safety wording, or
reviewer availability never alone selects Tier 2. Each Task retains its own
review gate; later integration review is not a substitute.

### Mid-Task Adapter And State Effects

For an explicit addition to an `in_progress` or `review_pending` Task, the
Skill applies the same one-pass classifier to the addition and its
relationship to the current responsibility:

1. already-covered scope selects `keep-current`, performs no Contract write,
   and preserves the current Tier;
2. a final group kept in or globally merged into current computes
   `max(current Tier, resulting floors)`. When higher, the existing Tier edit
   occurs first; the semantic Contract-revision write follows. It never
   auto-lowers and review does not advance between those writes. A failed second
   write therefore leaves unchanged scope at a conservative higher Tier;
3. a successor uses its own scope floor and existing lane/order. The same
   message registers it only when explicit taskization or separate placement is
   present; otherwise the adapter emits one write-free proposal; and
4. an addition that cannot yet be placed truthfully uses existing bounded
   Handoff, then continue, pause, or block only under their existing conditions.

Moving already-covered work requires explicit repartition authority, and an
unrepresentable order never creates an implicit dependency. Existing semantic
revision continues to invalidate stale target, review, and completion evidence;
`keep-current` preserves them only under ordinary exact-target rules. A pending
proposal is not persisted as a new model or reconstructed from Handoff. The
same event cannot run another Split after global Merge. Scope preservation
precedes any pause or block, and the normal Task loop gains no new call.

### Atomic Instruction-Layer Synchronization Boundary

M25.2 changes these surfaces in one reviewed Tier 2 revision:

- add only the concise trigger gate and disposition rules to
  `task-governance-tool/SKILL.md`;
- place the full one-pass sequence, responsibility/fragment cases, Tier table,
  ordering examples, and recovery rules in
  `task-governance-tool/references/task_workflow.md`;
- update `task-governance-tool/release-manifest.json` only for the changed
  package digests under the current version/release rules;
- switch the formerly inactive markers and implementation-facing routing in
  `docs/specification.md` and `docs/design.md`, and synchronize the approved
  static execution contract in `plan.md`; and
- update `tests/test_skill_self_containment.py`,
  `tests/test_m14_integrated_acceptance.py`, and
  `tests/test_document_history.py`, adding a focused test module only if those
  owning suites cannot express the behavioral cases without duplication.

`task-governance-tool/agents/openai.yaml` is in the synchronization review set
and remains byte-identical because its current registration and
scope-preservation metadata already covers the two triggers. Scripts,
migrations, repositories, CLI parsing/output, Viewer code/template, and public
command leaves are outside the write set and must be proven unchanged.

### Neutral Forward-Test Boundary

M25.2 acceptance uses fresh, minimal-context agents that receive the
candidate Skill and neutral workloads, not the expected branch or M20S study
result. A separate evaluator checks both the response and resulting Task DB.
The fixed matrix includes:

- responsibility slices that share files, tests, commands, or fixtures yet
  remain separate; an internal enabling slice without standalone user value;
  and fragment ownership whose transitive groups merge once with no re-Split;
- exact scope/permission conservation, representable ordering, fresh-agent
  reconstruction from Contract/authority/predecessor output, and ambiguous
  ownership falling back instead of being guessed;
- one whole-outcome revision-zero Task, the sole grouped-question boundary,
  design-first without automatic implementation registration, same-event
  partial-add recovery that preserves successful writes and adds only proven
  omissions, and interruption after a partial add with no reconstruction;
- binding Tier floors, one example for every Tier 2 protected category, wholly
  mechanical Tier 0, residual Tier 1, every excluded Tier-2 rationale, explicit
  higher Tier, and no ordinary lowering of a binding floor;
- mid-Task keep, revise, successor, and merge-into-current Tier effects,
  including separate Contract/Tier writes and no auto-lowering; and
- unchanged current command leaves, schema, Runner, normal-loop call count,
  target/evidence freshness, Handoff bounds, and no invocation from discovery,
  test failure, Effort, or cross-module failure alone. Each Task's review gate
  must pass independently of later integration review.

Positive, negative, and unknown cases use parallel wording and equal available
authority so the prompt does not reveal the expected result. A valid result
must match the specification branch and actual stored effects; self-reported
intent alone is insufficient.

<a id="trusted-local-runner-architecture"></a>

## Trusted-Local Runner Architecture

This is the current explicit-opt-in adapter architecture for a repository the
user already trusts. Eligibility remains deny-by-default and
binds the current Task, Contract, verification criterion, exact target, and a
project-owned fixed plan. Untrusted, external, unsupported, or visual-only
targets route to manual Verification Receipt handling without starting a process.
A launch uses an absolute executable, literal argv, `shell=false`, no
PATH lookup, a closed credential-excluding environment, and an exact private
materialization. The target working tree is never the execution root and
receives no copy-back.

The `value_model` is dependency-pure and legacy-stable. It preserves the opaque
Runner-policy seal and closed compatible record shapes and uses caller-token
identifiers. No legacy storage, Evidence provider/policy, or fixed-policy shim
remains an active consumer.

### Closed Runner-Slice Module Registry

The table is the complete Runner-slice layer registry. `Allowed Runner-layer
imports` names the only forward edges between these layer identifiers;
imports within one row are same-layer. Standard-library imports and existing
shared primitives outside this exact module set transfer no listed ownership
and may not import back into a higher Runner layer. Because `cli.py` is shared
by all public leaves, its unrelated pre-existing imports are outside the
Runner route; its Runner dispatch has only the `cli -> service` edge.

| Layer id | Exact owned modules | Sole Runner responsibility | Allowed Runner-layer imports | Forbidden responsibility or reverse edge | Current boundary |
|---|---|---|---|---|---|
| `cli` | `cli.py` | Parse and format the existing public surface and dispatch the Runner route to the parent service. | `service` | No Runner eligibility, authority, persistence, process, native, or cleanup decision; no Runner dispatch to `process_adapter` or `os_adapter`. | Direct process/native CLI branches are physically absent. |
| `service` | `verification_runner_service.py` | Parent orchestration; sole ownership of opt-in and eligibility, Task/Contract/criterion freshness, canonical repository coordination, target/plan selection, Evidence and terminal persistence, maintenance/recovery coordination, and final cleanup acceptance. | `repository`, `target_plan`, `value_model`, `runtime_identity`, `lifecycle`, `process_adapter` | No OS mechanics and no delegation of authority, business gates, terminal persistence, or cleanup acceptance to a child layer. | It is the sole business and cleanup-acceptance owner. |
| `repository` | `storage.py`, `tasks.py`, `contracts.py`, `reviews.py`, `verification_receipts.py`, `completion.py`, `evidence_ledger.py`, `evidence_projection.py`, `maintenance.py` | Canonical SQLite, Task/Contract/review/completion state, Evidence, and maintenance repositories and business gates invoked only by the parent service. | `value_model` | No process launch; no import of `process_adapter` or `os_adapter`; no filesystem cleanup ownership. | Schema/Evidence compatibility stays repository-owned with no reverse edge. |
| `target_plan` | `artifact_manifest.py`, `verification_runner_git.py`, `verification_runner_plan.py` | Parent-invoked exact target observation/materialization and fixed-plan decode/validation. | `repository`, `value_model` | No CLI policy, canonical database ownership, completion decision, trusted-code or verification-command launch, terminal publication, or cleanup acceptance. | Target/plan code is read-only until parent-owned materialization. |
| `value_model` | `verification_runner.py` | Pure closed Runner identifiers, bounded codes, value validation, and domain encoding used across the boundary. | none | No I/O and no import of CLI, service, repository, persistence, target, runtime, lifecycle, process, native, or business-gate modules. | The module is dependency-pure and has no compatibility shim consumer. |
| `runtime_identity` | `verification_runner_runtime.py`, `self_status.py` | Parent-invoked fixed executable and package-integrity observation. | `repository`, `value_model` | No process launch, canonical database ownership, business gate, terminal publication, or cleanup acceptance. | Candidate-only runtime material is physically absent. |
| `lifecycle` | `verification_runner_lifecycle.py` | Parent-requested creation, inventory, quarantine, removal, and absence proof for the one owned private attempt tree. | none | No process start, Job/stdio/handle ownership, SQLite, Evidence, business gate, terminal publication, or final cleanup acceptance. | Profile/recovery alternatives are physically absent. |
| `process_adapter` | `verification_runner_process.py` | Consume the closed request, establish the Job before trusted code, enforce process/resource/output/time bounds, drain and discard output, terminate and wait for process-tree zero, close handles, and return the closed result. | `value_model`, `os_adapter` | No canonical state or target-tree cleanup; no import of CLI, service, repository, storage, Task, Contract, review, Evidence, completion, setup, backup, maintenance, or another business gate. | Candidate/AppContainer/profile/ACL branches and business-freshness callbacks are absent. |
| `os_adapter` | `_verification_runner_win32.py` | Thin Windows Job, process, stdio, accounting, termination, wait, and handle primitives. | `value_model` | No parent policy, repository, persistence, gate, cleanup acceptance, LPAC/AppContainer/profile/ACL/ETW/registry-recovery module, or reverse import. | Only thin native primitives remain. |

The complete inter-layer edge set is therefore exactly:

```text
cli -> service
service -> repository
service -> target_plan
service -> value_model
service -> runtime_identity
service -> lifecycle
service -> process_adapter
repository -> value_model
target_plan -> repository
target_plan -> value_model
runtime_identity -> repository
runtime_identity -> value_model
process_adapter -> value_model
process_adapter -> os_adapter
os_adapter -> value_model
```

An unlisted edge is forbidden, and the graph has no cycle. In particular,
repository/persistence code never imports or launches the process or OS
adapter; the process and OS adapters never open the canonical database or
state resolver and never import a parent or business-gate module. `basis_is_current`
or an equivalent Task/Contract/criterion/target freshness callback is a
business-gate reverse edge; the service performs those checks before launch,
between bounded adapter calls where applicable, and after the returned result.

### Target-Plan Implementation

The `target_plan` registry row owns
`verification_runner_git.py` and `verification_runner_plan.py`; it does not add
or change CLI dispatch, the parent service, SQLite, Evidence, completion,
runtime identity, lifecycle, process, or native behavior. The modules may use
the existing read-only Git/artifact primitives, pure Evidence canonical JSON,
shared physical-path primitives, and `verification_runner.py` constants. They
must not import a higher Runner layer or any process or OS adapter.

`verification_runner_git.py` owns these immutable in-process values and calls:

```
RunnerTargetEntry = relative_posix_path, mode, object_id
RunnerTargetObservation = artifact, object_format, entries,
  target_material_digest
RunnerMaterialization = target, object_sizes, total_bytes,
  target_material_digest
MaterializedRunnerTarget = target_material_digest, entry_count,
  directory_count, total_bytes

observe_staged_runner_target(repo)
observe_commit_runner_target(repo, revision)
preflight_runner_material(repo, observation)
materialize_runner_target(repo, material, destination)
```

Observation reuses the established stable stage-zero index or exact commit
capture, then captures the complete ordered leaf set twice around a repeated
artifact observation. It admits only modes `100644` and `100755`, validates the
portable path and case/normalization collision set, derives all directories,
and never selects a plan from target material. The target digest is
`sha256(TARGET_MATERIAL_DOMAIN || canonical_json(target kind/value/base,
object format, ordered entries))`, where
`TARGET_MATERIAL_DOMAIN` is
`taskgov-verification-runner-target-material-v1\0`.

Preflight batch-checks every distinct target object as a blob, binds its exact
size, enforces the 10,000-file, 30,000-derived-directory (excluding the supplied
destination root), depth-64, and 512-MiB target bounds, and repeats target
capture before returning. It never reads or identifies a plan.
Materialization accepts only that preflight value and an existing
empty physical destination. It creates derived directories and regular files
exclusively, streams each blob with the established bounded Git reader,
recomputes the Git object ID including its blob header, and performs a bounded
no-follow inventory/hash comparison against the complete admitted set. A final
target recapture must still match. It never reads working-tree file content,
extracts an archive, follows a link/reparse, replaces an entry, copies back, or
launches target code. Failure leaves cleanup acceptance to the separate
`lifecycle` owner.

The parent-created canonical attempt target may be beneath the governed
repository because both the ordinary project-scoped package and the self-host
package keep their ignored generated state there. That lexical relationship
does not make the repository root the execution destination or authorize an
arbitrary repository write: materialization still accepts only the supplied
already-owned empty physical directory, rejects a destination equal to or
containing the repository, and confines every exclusive write and inventory
check to that destination. The `lifecycle` owner remains responsible for its
creation and cleanup.

`verification_runner_plan.py` owns:

```
VerificationRunnerPlanSource = raw_blob, raw_digest
VerificationRunnerPlanStep = ordinal plus normalized StepV1,
  with shell=false and path_lookup=false
VerificationRunnerPlanResolution = plan_state, route, reason,
  plan_blob_object_id, plan_raw_digest, plan_id, plan_version,
  plan_semantic_digest, selected_entry_digest, coverage, steps

capture_verification_runner_plan(repo, physical_package_root)
  -> VerificationRunnerPlanSource | None
resolve_verification_runner_plan(source,
  task_id, contract_revision, verification_expectation_digest,
  verification_criterion_digest)
```

Capture reads only `config/verification-runner.json` beneath the already
resolved physical package. An absent config directory or absent file returns
`None`, not an empty source; an alias, reparse, non-regular file, over-bound
file, link count other than one, identity change, or read uncertainty blocks.
Before and after the stable
read, one fixed shell-free Git check requires the exact repo-relative plan path
to have no literal case-insensitive current-index match and the physical
spelling to match effective ignore policy. A Git failure, timeout, unexpected
output, non-ignored result, or registered case variant blocks; the check never
writes Git state.
The file is independent of the selected Git target and stays excluded from the
release manifest. A same-named target entry is not consulted. The resolver
decodes strict UTF-8 JSON with duplicate-member, float, non-finite,
unknown-member, and type rejection. It validates the exact specification shape
and only the plan-known scalar/collection bounds before selection. Final
Windows quoting, fixed executable/bootstrap insertion, materialized absolute
paths, and the `command_line_utf16_units` bound remain process-request
admission and are not evaluated by this module. Raw bytes use ordinary
labeled SHA-256. Canonical normalized plan and selected-entry values use the
domains `taskgov-verification-runner-plan-v1\0` and
`taskgov-verification-runner-plan-entry-v1\0`. `trusted_local = true` plus one
exact basis selects `plan_state=runner`, `route=runner`; false opt-in, absence,
or no current-Task entry is a closed manual fallback; a current-Task mismatch,
duplicate basis, ambiguity, or malformed input raises one sanitized bounded
plan error. Successful resolutions use exactly the specification matrix:
`absent/m21_fallback/plan_absent`,
`disabled/m21_fallback/trusted_local_disabled`,
`no_match/m21_fallback/plan_entry_absent`, or
`runner/runner/null`. The absent case has no raw, id, version, semantic, or
selected-entry value. The two present-plan fallbacks retain raw/id/version/
semantic identity but no selected entry; every fallback has
`coverage=not_applicable` and empty steps. The runner case has those plan
identities plus a selected-entry digest, `coverage=full`, and one through 16
steps. The existing nullable `plan_blob_object_id` stays null in every case;
raw and semantic digests provide the physical-plan binding. Arguments and
private values remain in-process only and are not members of the resolution
digest or any durable projection.

The integration boundary assembles actual plan-resolution and target-
materialization outputs into the existing pure
`resolution_idempotency_digest`. That seal already binds Task/Contract,
authority/criterion, review-target kind/value/base/generation, target material,
and plan raw/semantic/selected-entry identities without including plan bytes,
argv, or steps. `target_plan` does not import or invoke the parent service;
the parent service remains the owner of orchestration, persistence, and
dispatch consumption.

### Typed Process Value Boundary

The following are logical immutable in-process records, not a public schema or
implemented transport. Their member sets are closed:

```text
RunnerProcessRequestV1 = version, attempt_id, executable,
  materialized_root, scratch_root, clean_environment, steps, cancel_signal
RunnerProcessStepV1 = ordinal, step_id, mode, entrypoint, argv, cwd,
  shell, path_lookup, timeout_seconds, cpu_seconds, memory_mib,
  process_limit, output_byte_limit
RunnerProcessResultV1 = version, attempt_id, outcome, reason, launch_state,
  failed_step_ordinal, duration_ms, cpu_time_ms, peak_job_memory_bytes,
  total_process_count, process_zero, handles_closed, raw_output_discarded,
  steps
RunnerProcessStepResultV1 = ordinal, outcome, reason, launch_state,
  cpu_time_ms, peak_job_memory_bytes, total_process_count
RunnerPrivateTreeResultV1 = attempt_id, state
```

The closed scalar, path, and collection bounds are exactly:

```text
RunnerProcessBoundsV1:
  request_version = 1
  accepted_plan_blob_utf8_bytes <= 65536
  attempt_id = ASCII /tg_verification_runner_attempt_[0-9a-f]{16}/ (47 bytes)
  identifier = ASCII /[a-z0-9][a-z0-9._-]{0,63}/ (1..64 bytes)
  result_code = ASCII /[a-z][a-z0-9_]{0,63}/ (1..64 bytes)
  absolute_path = well-formed Unicode, absolute normalized Windows path, no NUL or Unicode Cc, no "." or ".." segment, 1..4096 UTF-8 bytes and 1..4096 UTF-16 code units
  relative_path = "." or 1..32 "/"-separated ASCII /[A-Za-z0-9_][A-Za-z0-9._-]{0,127}/ components, no "." or ".." component, total 1..512 bytes
  script_entrypoint = non-dot relative_path ending in ".py"
  module_entrypoint = 1..16 "."-separated ASCII /[A-Za-z_][A-Za-z0-9_]{0,63}/ components, total 1..512 bytes
  literal_arg = well-formed Unicode with no Unicode Cc, 0..4096 UTF-8 bytes and 0..4096 UTF-16 code units
  path_ownership = executable is a parent-verified fixed absolute package-runtime identity outside materialized_root and scratch_root with no PATH lookup; materialized_root and scratch_root are distinct target and scratch children of one owned attempt root; no symlink or reparse traversal
  resolved_relative_path = every entrypoint and cwd resolves beneath materialized_root
  step_count = 1..16; argv_count_per_step = 0..64
  timeout_seconds = 1..900; total_timeout_seconds = 1..1800
  cpu_seconds = 1..900; memory_mib = 64..2048; process_limit = 1..32
  output_byte_limit = 1048576
  command_line_utf16_units <= 24576 after exact Windows quoting and fixed bootstrap insertion
  clean_environment_entry_count = 11; clean_environment_value_utf8_bytes = 1..4096
  clean_environment_keys = APPDATA, HOME, LOCALAPPDATA, PYTHONDONTWRITEBYTECODE, PYTHONNOUSERSITE, PYTHONUTF8, SystemRoot, TEMP, TMP, USERPROFILE, WINDIR
  clean_environment_paths = APPDATA=scratch_root/roaming; HOME=USERPROFILE=scratch_root/home; LOCALAPPDATA=scratch_root/local; TEMP=TMP=scratch_root/tmp; SystemRoot=WINDIR=parent-verified Windows directory
  clean_environment_literals = PYTHONDONTWRITEBYTECODE=PYTHONNOUSERSITE=PYTHONUTF8="1"
  clean_environment_block_utf16_units <= 24576 including the terminal double NUL
  result_version = 1; result_attempt_id = request.attempt_id
  result_outcome = result_code; result_reason = null or result_code
  step_result_outcome = result_code; step_result_reason = null or result_code
  launch_state = no_launch|launched; private_tree_state = absent|uncertain
  result_step_count = 0..request.step_count and 0..16; result_step_ordinals = unique, request-ordered values in 1..request.step_count
  failed_step_ordinal = null or a value in 1..request.step_count
```

The service constructs the request only after parent-owned eligibility,
freshness, target, plan, executable, materialization, and credential-exclusion
checks. No variable-length request member inherits a bound from an inactive
execution unit. `executable`, `materialized_root`, and `scratch_root` are
`absolute_path` values observed by the parent without a symlink or reparse
traversal. `executable` is the parent-verified fixed absolute package-runtime
identity, uses no `PATH` lookup, and is outside `materialized_root` and
`scratch_root`. `materialized_root` and `scratch_root` belong to exactly one
private attempt root as its distinct `target` and `scratch` children. Every
resolved `entrypoint` and `cwd` remains under `materialized_root`.

`clean_environment` is an ordered tuple containing exactly the 11
case-insensitively unique keys `APPDATA`, `HOME`, `LOCALAPPDATA`,
`PYTHONDONTWRITEBYTECODE`, `PYTHONNOUSERSITE`, `PYTHONUTF8`, `SystemRoot`,
`TEMP`, `TMP`, `USERPROFILE`, and `WINDIR`, in that order. `APPDATA` is
`scratch_root/roaming`; `HOME` and `USERPROFILE` are `scratch_root/home`;
`LOCALAPPDATA` is `scratch_root/local`; `TEMP` and `TMP` are
`scratch_root/tmp`; `SystemRoot` and `WINDIR` are the same parent-verified
Windows directory; and the three `PYTHON*` values are exactly `"1"`. It has no
additional or ambient key, and all path values satisfy `absolute_path`.

The process boundary fixes the package-runtime executable source to the operating-system
image path of the current parent process. `sys.executable` is used only to
corroborate the same physical file; neither value is resolved through `PATH`,
configuration, a plan, or target material. The runtime-identity layer observes
every path component without following a symlink or reparse point, requires a
normalized absolute regular `python.exe` outside the owned target and scratch
trees, and holds a non-inheritable read handle that denies write and delete
sharing. The parent keeps that lease open from the final identity observation
through the complete process-adapter call and closes it on every exit. Failure
to establish or retain the lease is a sanitized admission failure with no
alternate executable. The process adapter consumes only the leased absolute
path in the closed request and does not import the runtime-identity layer.
Each lease serializes executable access, context state, and native close with a
private non-reentrant lock, so no two native close attempts overlap and access
cannot observe an in-progress close. Context entry is single-depth; nested
entry and direct close while a context is active fail closed, while the
matching context exit performs the one release transition. A definitive native
close failure retains ownership for a later serialized retry, whereas an
interrupted native close becomes uncertain and is never retried.

`steps` is a tuple whose ordinal is its one-based position. `step_id` satisfies
`identifier`; `mode` is exactly `script|module`; `entrypoint` satisfies the
matching entrypoint grammar; `argv` is a tuple of `literal_arg`; and `cwd`
satisfies `relative_path`. These counts, per-member sizes, and aggregate
command-line/environment-block limits are all enforced before process-adapter
entry. The request admits no other scalar or collection shape. Resource,
timeout, and output values use the exact numeric bounds above; `shell` and
`path_lookup` are exactly false. Request paths, argv, and environment are
transient and never copied into a result or durable row.
`cancel_signal` is a local in-process typed signal whose observable payload is
one Boolean; it contains no callback to SQLite, CLI policy, or a business gate,
is never persisted, and creates no transport.

For both result records, `outcome` is an adapter-local `result_code` and
`reason` is either null or an adapter-local `result_code`; `launch_state` uses
the exact closed set above. These codes are bounded sanitized structural
values, not arbitrary text. The value-model boundary owns only the closed
record-member sets, `result_code` grammar, nullability, and those member-to-
grammar bindings. The process adapter owns concrete local membership and
pairing, while the parent service owns the closed
durable/public mapping and projection. The parent service accepts no arbitrary
adapter text, remains the sole business-interpretation and persistence owner,
and persists only the mapped existing durable outcome. This freeze does not
alter the specification's existing closed durable outcome. Optional accounting
is nonnegative and bounded by the request. Result `steps` satisfies the exact
count, uniqueness, range, and order relation above, so it has at most one
sanitized result per request step. A result contains no exit code, output byte,
argv, environment value, credential, path, exception body, or arbitrary text.

The adapter-local pairings are exactly as follows; a slash joins
`outcome / reason / launch_state`, and `null` is the only absent reason:

`RunnerProcessStepPairingsV1`:

  blocked_prelaunch / runtime_unavailable|process_setup_failed|process_boundary_unproved|process_create_failed / no_launch
  pass / null / launched
  fail / step_nonzero / launched
  timeout / timeout / launched
  cancelled / cancelled / launched
  resource_exceeded / cpu_limit|memory_limit / launched
  output_rejected / output_limit / launched
  process_error / process_boundary_unproved|process_resume_failed|process_wait_failed|pipe_drain_failed|process_tree_unproved / launched
  process_error / process_create_failed / no_launch
  controller_interrupted / controller_interrupted / no_launch|launched
  cleanup_failed / process_cleanup_failed / no_launch|launched

`RunnerProcessResultPairingsV1`:

  blocked_prelaunch / runtime_unavailable|process_setup_failed|process_boundary_unproved|process_create_failed|cancelled|controller_interrupted / no_launch
  pass / null / launched
  fail / step_nonzero / launched
  timeout / timeout / launched
  cancelled / cancelled / launched
  resource_exceeded / cpu_limit|memory_limit / launched
  output_rejected / output_limit / launched
  process_error / runtime_unavailable|process_setup_failed|process_boundary_unproved|process_create_failed|process_resume_failed|process_wait_failed|pipe_drain_failed|process_tree_unproved / launched
  controller_interrupted / controller_interrupted / no_launch|launched
  cleanup_failed / process_cleanup_failed / no_launch|launched

`cpu_time_ms` is total user CPU time, `peak_job_memory_bytes` is the peak Job
memory observation, and `total_process_count` is the cumulative number of
processes created in the applicable per-step Jobs. Each is absent as one group
or is a nonnegative signed-64-bit integer. CPU and memory observations on a
successful or ordinary nonzero step do not exceed that step's request limit.
`process_limit` bounds simultaneously active processes in each Job; it is not
an invalid upper bound on the cumulative `total_process_count`. Request-level
CPU and process counts are checked sums and request-level memory is the checked
maximum. A limit or cleanup failure may omit accounting, but it never changes
the three mandatory Boolean cleanup proofs. Windows rejects creation beyond the
active-process limit, while the child may surface that rejection only as the
ordinary sanitized `step_nonzero`; the adapter adds no completion-port or notification
infrastructure solely to reclassify it as a resource result.

process_zero, handles_closed, and raw_output_discarded must all be true before
the service can accept process cleanup. The lifecycle layer separately returns
only the same safe `attempt_id` and the exact `private_tree_state` set above;
it returns no path or filesystem detail.

The lifecycle cleanup call accepts only its fixed parent-owned Runner paths
and one valid `attempt_id`. It returns `state=absent` only after a bounded
no-follow removal and a fresh proof that both the exact attempt and quarantine
entries are absent while their owning parent-directory identities remain
unchanged. Already-absent is an idempotent `absent` result. Simultaneous
attempt/quarantine entries, a foreign entry, reparse or identity change,
traversal bound or timeout, partial deletion, reappearance, or any observation
or deletion failure returns `state=uncertain`; it never authorizes an
out-of-root deletion, copy-back, alternate cleanup root, or diagnostic detail.
An invalid attempt identifier is rejected before cleanup rather than converted
to either result state.

### Cleanup Acceptance And Privacy

The process adapter is the sole owner of its Job, descendants, drains, and
handles. The lifecycle layer is the sole mechanic allowed to remove and prove
absence of the parent-owned private attempt tree. Neither is a business
acceptance owner. `verification_runner_service.py` is the single cleanup-
acceptance owner: it alone combines process-tree zero, handle closure, output
discard, and private-tree absence, blocks on any false or uncertain proof, and
alone authorizes cleanup success or terminal persistence. No recovery path,
maintenance module, repository, CLI branch, or later adapter may synthesize a
second acceptance decision.

Raw output, argv, environment, credentials, private paths, exit codes, and
exception bodies remain transient and are never stored. Cleanup or privacy
uncertainty fails closed. The records above define no serializer, file spool,
queue, pipe, socket, RPC, worker, daemon, subprocess wrapper, supervisor,
heartbeat, retry protocol, secondary state store, or second database
connection. Any later process separation requires explicit authority; this
boundary adds no IPC, schema, public CLI, Skill trigger, or second completion
gate.

This remains a reliability and privacy boundary for trusted code, not a
hostile-code sandbox or a claim of network isolation. Candidate C, B-to-C,
LPAC/AppContainer, Package-SID ACLs, ETW, registry/profile recovery, transfer
state machines, supervisors, trust-root hardening, and diagnostic fault
matrices are not qualification gates. Their code, tests, fixtures, manifests,
Candidate runtime material, and direct native seams are physically absent and
are not architecture nodes.

## Runner Parent Service And Audit Graph

`cli.py` keeps the existing `review target set` parser and formatters and calls
only `verification_runner_service.py` for that dispatch. The service consumes
one internal prepared-capture seam from `reviews.py`; the ordinary review
service delegates to the same seam, so target normalization, Git observation,
manifest construction, authority capture, event bytes, output, and maintenance
classification have one owner.

The service returns the stored target result with exactly
`verification_route` and `blocking_code` for JSON success. The text formatter
ignores those keys and remains byte-compatible; failure data remains the prior
three-key empty shape. The ordinary path derives `not_required` or
`receipt_required` from the captured verification expectation before its write.
The Runner path classifies the terminal observation after successful T2 with
one helper shared by the exact-current selector: closed no-launch fallback maps
to `receipt_required`, qualifying pass to `runner_pass`, and every other stored
terminal to `blocked` plus `verification_receipt_blocking`. No post-commit DB
reread, Runner reselection, or second public command is introduced.

The service performs target/plan/package/runtime and every deterministic
Runner-only preflight before a writer. A fallback or definite closed
pre-attempt Runner-only failure invokes one target-only T1 transaction. An
eligible Runner next validates the fixed lifecycle inventory and acquires the
zero-wait Runner lock before any T1 writer. Lock contention returns
`runner_busy`; lifecycle or inventory uncertainty returns
`runner_state_invalid`; both leave the target and Runner tables unchanged.
Under that retained lock, the service reconciles any pending intent before it
may invoke one short T1 transaction that revalidates the prepared Task,
Contract, criteria, target generation, manifest, plan, implementation, and
policy identities and atomically inserts:

```text
ordinary exact review target + artifact manifest/Reference + event
one verification_runner_resolution
one verification_runner_attempt intent
```

There is no eligible target-without-intent commit window. Conversely, no
Runner row is inserted for the fallback T1. A T1 failure rolls back every
member. The service retains the same Runner lock from pending reconciliation
through T1, materialization, process/lifecycle work, and terminal T2, but
closes each SQLite transaction before creating attempt directories,
materializing Git objects, leasing the executable, or calling the process
adapter. No SQLite writer spans filesystem or process work.

`storage.py` owns repository operations for exact same-generation graph reads,
the atomic T1 insert, cleanup-only append, and atomic terminal append. The
repository enforces at most one resolution, attempt, cleanup event, and
observation per project/Task/target generation despite the deliberately more
permissive physical indexes. Exact idempotency-digest replay returns the
existing row set; a different digest, extra row, ownership mismatch, or second
pending owner fails closed. `BEGIN IMMEDIATE` makes each same-generation
repository transaction atomic, while the retained Runner OS lock serializes
the complete reconciliation/T1/process/T2 route. A caller that cannot take
that lock performs no T1 and cannot launch a process.

The parent supplies one fresh 16-lowercase-hex token to each pure
`generate_runner_id` call. A resolution uses the target-plan seal and the
manifest-bound `RunnerImplementationIdentity`. Its
`runner_policy_digest` is the fixed
`verification Runner orchestration policy v1` label
`sha256:8910c1edfd525be0def6a2c3afb65adab11e5a32e9a60ebbf898c175ffd60fa8`.
The label is not recomputed from the manifest, target, plan, or runtime and
adds no sandbox or security claim; `runner_implementation_digest` separately
binds the strict current release-manifest identity. The process layer has
no durable canonical runtime digest, so `runtime_digest` is always null in the
resolution and Runner source projection; the implementation digest and fixed
policy label are the only durable execution identities.

For an admitted attempt, the service alone owns this sequence under the one
zero-wait Runner lock already acquired before T1:

```text
reconcile pending DB state and the fixed filesystem inventory
commit atomic T1 and close its SQLite writer
create the exact attempt target/scratch tree
materialize the admitted target and build the closed process request
hold the fixed-executable lease across the process call
consume one accepted RunnerProcessResultV1
prove process_zero + handles_closed + raw_output_discarded
prove exact attempt/quarantine absence through lifecycle cleanup
append one terminal graph, or fail closed without an observation
```

The terminal mapper copies `outcome`, `reason`, `launch_state`, ordered step
summary, duration, and optional accounting from the accepted process result without
adding a code. `launch_state=launched` maps to `route=runner`; `no_launch` maps
to `route=m21_fallback`. `complete_plan` is one only for a launched pass with
null reason, the complete planned ordinal set, every step passing, and no
failed ordinal. A definite post-intent runtime admission failure maps to
`blocked_prelaunch/runtime_unavailable/no_launch`; a definite tree,
materialization, or request-setup failure maps to
`blocked_prelaunch/process_setup_failed/no_launch` only after tree absence is
proved. `cleanup_failed` and any incomplete process/privacy/lifecycle proof
are not persistable observations.

One terminal repository transaction inserts exactly the observation, its
`attempt_cleanup_succeeded` event pointing to that observation, one
`runner_observation` Evidence Reference, and one verification-criterion link.
`evidence_ledger.py` admits this source only from the internal Runner dispatch,
derives `machine_observed/verification_runner/1`, recomputes the existing
sanitized source projection and digest, and rejects a caller-selected
assurance, producer, source, or criterion. The terminal transaction itself
creates no completion cycle or Bundle, advances neither Evidence nor Viewer
generation, and never creates a Verification Receipt. For
`gate_eligibility_version=0`, the Reference and link remain standalone audit
history and are never a Bundle member, completion basis, or Evidence JSON
source. For a schema-v21/v22 `gate_eligibility_version=1` qualifying pass, later
completion capture selects that exact stored observation, Reference, and link;
`evidence_projection.py` reuses the existing Reference and link as Bundle
members and emits only the sanitized projection in Bundle v2. The closed
no-launch fallback adds no Runner member, and every other terminal blocks
completion.

Restart reads immutable DB state before filesystem mutation. An attempt intent
without an observation is never relaunched. The service asks lifecycle to
remove only its exact known tree and, when absence is proved but the prior
process result is unknowable, appends only one cleanup event with null
`terminal_observation_id`. The cleanup-performing call then returns
`runner_state_invalid`, performs no new T1 or maintenance, and does not launch.
Storage classifies that old generation as complete `restart_cleaned`, not
pending. A later independent target-set call may create a new generation only
after the validator admits that exact cleanup-only predecessor, the pending
query returns zero attempts with neither observation nor cleanup event, and
the fixed inventory is empty. Target, authority, or installed-implementation
drift after intent takes the same cleanup-only route and then errors. A foreign
tree, simultaneous attempt/quarantine entries, more than one actual pending
attempt, unknown DB owner, root identity drift, or uncertain cleanup leaves
the attempt pending and fails closed.

Post-intent pending, basis drift, incomplete process proof, and cleanup or
terminal-state uncertainty use the existing sanitized `runner_state_invalid`
service failure; zero-wait lock contention uses `runner_busy`; a storage error
retains its existing code. No new public error code or envelope member is
added. The committed T1 target and intent survive a later service error.
Success and fallback perform exactly the existing one target-set post-commit
maintenance opportunity; a post-T1 error performs none and relies on the
already-advanced due state. Internal Runner writes add no maintenance call and
advance neither Evidence nor Viewer generation.

The shared Runner graph validator admits only the four states in the
specification cardinality table. Every observation has exactly one matching
Reference/link and cleanup event; a cleanup-only event has a null terminal
observation; every pending intent has neither; all other combinations fail.
Schema v20 admits only `gate_eligibility_version=0`, Task marker `0`, and null
completion-cycle/Bundle Runner pointers. Schemas v21 and v22 retain that audit-only
shape and additionally admit the closed version-`1`/marker-`2` shape; only an
exact qualifying pass may bind its observation and the two pre-existing
evidence identities into completion, while fallback keeps the manual Receipt
basis and null Runner pointers. Viewer validates and discards Runner-only data.
Managed backup copies the SQLite rows but not the private Runner tree; recovery
therefore treats a restored pending intent as an unknown-result cleanup-only
case after proving physical absence. The service path adds no further schema
object, migration, public projection, Bundle format, Skill text, or manual
Receipt-gate behavior.

## Validation And Test Design

The suite is standard-library-first, offline, and isolated. It must not mutate
a real consuming project or Git state. Tests cover:

- all 21 parser leaves, removed commands/options, help, text/JSON/error/compact
  envelopes, and byte limits;
- missing/old/too-new/invalid state with no creation or sidecars;
- every v1-v22 migration, rollback, idempotency, required-object marker,
  realistic preservation fixture, quick check, and foreign keys;
- task validation, ordering, pause/block/current/next, done/reopen,
  completion evidence, every review tier/target/receipt/finding, Contract,
  checkpoint, handoff, and Effort route;
- the shared stored-Task fault matrix once in a focused validator
  module, plus representative public/lifecycle/doctor/Viewer route canaries,
  selected-batch query bounds, no-write failure, and last-good publication in
  a separate consumer-boundary module;
- the Contract-pointer matrix in one focused relation module and its
  selected-batch single-query, pre-v8 no-query, recovery set-fatal, valid-state,
  lifecycle, doctor/setup, and last-good Viewer canaries in one separate
  consumer-boundary module rather than duplicating every command fixture;
- Git option safety, canonical commit resolution, snapshot/index/tree binding,
  no mutation, and no lazy network;
- concurrent readers/writers, short writer ownership, stable busy/WAL errors,
  and injected rollback points;
- UUID identity, legacy identity, fixed/legacy resolver inventories,
  same-binding publication, recovery, relocation token/replay/expiry, staged
  no-clobber publication, cleanup resume, and preservation of unrelated files;
- backup publication/reconciliation/retention/recovery and every crash
  boundary;
- Viewer v4 sources 5-22, completion-history bounds, version-1 Receipt-link,
  v19-v22 Bundle-discriminator validation, and v20-v22 source-appropriate
  Runner-graph validation, 500-ID history batching,
  the accepted 500-Task performance fixture, 64-MiB artifact cap,
  generation/last-good behavior, strict config, timer/visibility, one-shot
  History state, CSP, text-only DOM, and absence of storage/network APIs;
- package self-containment, manifest integrity, project-scoped/self-host
  layouts, ignore rules, Windows Python 3.12/3.14, and junction rejection;
- M16 fresh-session behavioral fixtures plus the current manual/fallback
  ten-call default flow and mechanically enabled eleven-call flow, with the
  Receiptless Runner-pass branch one call lower; and
- release archive reproducibility, license/manifest/archive inclusion,
  legacy upgrade/paired rollback, exact workflow identity, and sanitized
  release evidence.

Repository release consistency uses the same offline read-only checker from
focused unit fixtures and CI. Negative fixtures independently cover a missing
manifest or declared file, extra packaged core, digest mismatch, license or
manifest-license mismatch, invalid Skill/agent/manifest metadata, parser-to-
document drift, and tracked generated artifacts. The checker does not replace
installed-CLI, no-write, physical-isolation, migration, upgrade/rollback, or
release-acceptance behavior tests.

The deterministic repository runner assigns every discovered test module to
exactly one base lane:

- `fast` covers parser, validation, state transitions, pure helpers, and small
  temporary repositories;
- `integration` covers setup, recovery, relocation publication, backup,
  Viewer, concurrency, consumer layouts, and normal migration; and
- `release` covers legacy migration/recovery, upgrade and paired rollback,
  performance, package/license, active documents, workflow policy, and
  integrated release acceptance.

Every run first validates the complete manifest. A base lane is an ordered
filter of standard discovery, while `all` runs the original discovered suite
in its original order. New `test*.py` modules therefore fail closed until
classified; new methods in an owned module inherit that module's lane.

The retired LPAC module, mandatory native fixture, and dedicated route tests are
absent from standard discovery. No such residue remains
in the standard test partition, which continues to contain only the three base
lanes `fast`, `integration`, and `release`. No replacement meta-test framework,
implementation-shape-only assertion, disabled test, or new SKIP may stand in
for a test that detects a current requirement or regression.

CI obtains one compact include matrix from this same policy. Pull requests run
all three base lanes on Python 3.12 and `fast` on 3.14. Pushes to `main` run
all three base lanes independently on both versions. Manual
`workflow_dispatch` runs monolithic `all` on both versions, and an
`always()` aggregate job fails unless policy validation and the full matrix
succeed. The workflow trigger remains limited to pull requests, pushes to
`main`, and manual dispatch; a candidate gate cannot be replaced by a skipped
dependency job.

Tier-2 changes use two independent exact-target reviews. Tests hard-fail count,
byte, subprocess, attempt, and render limits; performance budgets never justify
loosening a deterministic bound or adding asynchronous architecture.

The release-lane backup/Viewer performance module separates deterministic
functional and capacity checks from timing classification. Each fixed fixture
uses fresh database copies for one warm-up round and six measured rounds. The
three-mode qualifier uses all six permutations once, while the two-mode
qualifier repeats both orders equally. One local helper classifies the same-
round paired enabled-minus-disabled overhead using the six-sample median, and
the median observation at each command position against the mode-specific
budgets: 10 seconds for backup-only and Viewer-only total overhead, 12 seconds
for combined Viewer-plus-backup total overhead, and below 5 seconds for every
command-position median. The helper emits only bounded numeric diagnostics.
The warm-up is excluded from qualification; no
functional, count, byte, attempt, render, or call failure is excluded or
statistically masked.

## Current Runner Plan Authoring And Control Design

This is the current implementation design for bounded Runner Plan authoring.
The CLI retains 21 leaves and exposes the one explicit Plan action option on
`task edit`; reading this section alone authorizes no config write, Task edit,
process launch, target mutation, or external operation. Setup never creates the
config, and the existing `review target set` parent service remains the sole
Runner dispatch. Schema v22 and the current Runner, Evidence, Viewer, and
completion graphs do not change.

### Separate Authoring Control Boundary

The activated design keeps Plan authoring outside the closed Runner execution
graph. It adds these exact ownership boundaries:

| Owner | Responsibility | Forbidden responsibility |
|---|---|---|
| `verification_runner_plan.py` | Existing physical capture/resolution plus shared pure PlanV1 value decode, validation, and canonical encoding used by both readers and authoring. | No Plan publication, Task/SQLite write, CLI decision, process launch, or Runner graph write. |
| `verification_runner_plan_authoring.py` | Strict RunnerPlanDraftV1 decode and pure `replace|rebind|detach|disable` transforms over one validated PlanV1 value. | No filesystem, SQLite, Git, target, CLI, process, Evidence, Viewer, or logging I/O. |
| `verification_runner_plan_publisher.py` | Capture/revalidate the one canonical physical authoring source for every action and, when supplied, publish one already-canonical bounded candidate through the complete-file replacement boundary. | No Task or Contract decision, database access, action selection, process launch, target materialization, or Runner graph write. |
| `verification_runner_plan_edit.py` | Parent control orchestration, option compatibility, current/future basis selection, DB-first sequencing, publisher invocation, and typed success/partial-success result. | No parser/text formatting, Runner dispatch, process/lifecycle/native call, schema change, Evidence/Viewer write, automatic action, or command inference. |
| `cli.py` | Parse the one action option, read bounded stdin for `replace`, call the parent control service, format the closed result/warning, and schedule ordinary maintenance for a committed Task mutation. | No Plan semantics, basis derivation, physical publication, Runner launch, or second approval protocol. |

The new control-edge set is exactly:

```text
cli -> verification_runner_plan_edit
verification_runner_plan_edit -> tasks/contracts/reviews
verification_runner_plan_edit -> verification_runner_plan_authoring
verification_runner_plan_edit -> verification_runner_plan_publisher
verification_runner_plan_publisher -> verification_runner_plan
verification_runner_plan_publisher -> state_paths
verification_runner_plan_authoring -> verification_runner_plan
verification_runner_plan_authoring -> tasks (common privacy guard only)
```

These edges do not alter the existing Runner-layer registry or add a reverse
edge into `verification_runner_service`, `verification_runner_process`,
`verification_runner_lifecycle`, `_verification_runner_win32`, storage,
completion, Evidence, or Viewer. The execution reader continues to capture and
resolve the same PlanV1 bytes; it neither imports nor invokes authoring or its
publisher.

`verification_runner_plan.py` may expose immutable Plan/Entry value objects and
pure canonical decode/encode helpers instead of duplicating its existing
closed validators. Its current `VerificationRunnerPlanSource`, source capture,
error sanitization, normalized digest, exact-basis selection, and fallback/
block behavior remain byte- and semantics-compatible. The authoring transform
accepts only a validated value and the future basis supplied by its parent. It
preserves Plan ID, global trust except for `disable`, all unrelated entries,
and their order, with the cardinality and insertion rules fixed by the
specification. It creates the fixed initial Plan only for `replace` against an
absent source. No action reads the ambient target, verifies entrypoint
existence, predicts command success, or evaluates test coverage.

The draft decoder, and not the shared PlanV1 reader decoder, applies the
existing pure `tasks.reject_private_or_raw_content` guard to every recognized
caller-supplied StepV1 string leaf with the fixed field label
`Runner Plan draft`. It performs that complete leaf pass before enum, grammar,
UTF-8, UTF-16, candidate, or transform validation, returns the existing
`privacy_rejected` error without the rejected value, and emits no candidate on
failure. It does not call `validate_text`, add a second privacy pattern set, or
change admission of an existing physical PlanV1 source.

`runner_plan_update.status=unchanged` means the pure action produces the same
normalized Plan semantics. A semantic no-op returns no publication candidate
and does not normalize valid noncanonical bytes as a side effect, but it still
requires post-transaction confirmation of the expected source before the
parent may return `unchanged`. Changed semantics produce canonical candidate
bytes, and their successful publication is `updated`. The pure action result
therefore determines the intended status without a later semantic guess; the
publisher determines whether that disposition was safely confirmed.

### Publisher Boundary

The publisher receives the already resolved governed repository root and
physical package root, one expected `RunnerPlanAuthoringSource`, and either
complete canonical candidate bytes or an explicit confirmation-only marker.
The closed source record contains state
`absent_directory|absent_file|present`, the observed package/config/file
physical identities applicable to that state, and raw bytes/digest only for a
present file. It is a new authoring-only value; the current execution
`VerificationRunnerPlanSource` stays unchanged. Capture and revalidation reuse
the current no-follow physical path, regular-single-link, size, case-alias,
current-index absence, and effective-ignore rules for
`config/verification-runner.json`. It never accepts a caller path and never
changes an index entry, selected review-target material, database, setup
artifact, release artifact, or any target-project path other than the one
canonical ignored config directory/file authorized by the invocation.

Every action invokes this boundary after the SQLite writer is closed. The
publisher first revalidates the expected source record. With the confirmation-
only marker it then returns success without creating a directory, temporary
file, or canonical file. With candidate bytes it proceeds to publication. A
source mismatch is never treated as an unchanged semantic result.

Only `replace` against `absent_directory` may create the exact physical
`config` directory, exclusively and without a reparse traversal, after the
repository/index/ignore checks pass. No other action creates it. If later
publication fails, that identity-owned empty ignored directory may remain; it
contains no Plan source and is not recursively removed. A present foreign,
linked, replaced, or nonempty unexpected directory fails closed.

For a detected mismatch, unsafe path, concurrent source change, invalid
candidate, temporary-file failure, or replacement failure, the publisher
returns one sanitized closed failure and does not claim a confirmed disposition
or a new canonical Plan.
Temporary material is created exclusively in the resolved or just-created
physical config directory, has one bounded purpose, is never a Plan source,
and is removed on ordinary failure. Successful publication replaces the
canonical name only with complete bytes; a reader sees a complete old or new
file, not a partially written JSON document.

This is a compare-before-replace guard and complete-file atomic replacement,
not a linearizable filesystem compare-and-swap primitive. It promises no
cross-process exclusion after the last comparison, power-loss durability,
Git/config transaction, or atomicity with SQLite. The later target-set capture
and exact-basis comparison remain the authoritative final freshness checks.

### Parent Coordination Sequence

`verification_runner_plan_edit.py` owns one bounded operation:

1. resolve the existing project/package boundary and read the selected stored
   Task, its Contract/criterion basis, and the physical Plan source;
2. validate the action, Task-field compatibility, current matching-entry
   rule, and the bounded stdin draft, without writing;
3. for Plan-only work, obtain the exact current basis through a read
   transaction and bypass `edit_task`, so no Task event or business write is
   created;
4. for a real basis-changing edit, use the existing Task/Contract write
   transaction, derive its exact future Task/Contract/expectation/criterion
   basis, compute the pure action result, and build and validate full candidate
   Plan bytes in memory only when semantics change, then commit and close
   SQLite;
5. for every action, invoke the publisher only after the writer is closed,
   using the originally captured expected source and either the candidate or
   the confirmation-only marker; and
6. return the typed Plan result together with the existing Task result and one
   mutation classification for CLI maintenance.

The locked Task reread must still match the preflight basis. Candidate creation
inside the Task transaction performs only pure computation; no temporary or
canonical filesystem write occurs while SQLite owns a writer. If Task
validation, Contract revision, basis derivation, candidate bounds, or DB commit
fails, the transaction rolls back and publication is never called.

After a committed basis change, a publisher source-confirmation or publication
failure returns the committed Task result plus
`runner_plan_update.status=unconfirmed`; it does not reopen SQLite or compensate
the Task. Whether the expected source stayed unchanged or another authorized
writer caused the mismatch, the service makes no eligibility claim; the Task
basis commit itself can change an unchanged entry's prior exact-match status.
The caller must not rely on Runner execution until a later explicit Plan-only
action succeeds. A Plan-only publisher failure has no Task result to preserve
and uses the ordinary failed Task-edit envelope. No hidden retry, journal,
pending row, second connection, worker, daemon, or automatic follow-up exists.

The CLI maps a committed Task plus Plan failure to `ok=true`, the fixed
`task_applied_runner_plan_unconfirmed` warning, and the closed unconfirmed
projection. This keeps the existing coordinator eligible for exactly one
ordinary post-commit maintenance opportunity for the actual Task mutation.
The one authoring warning precedes any existing maintenance warnings and is not
a claim that the requested Plan action succeeded or that current eligibility
is known. A config-only success or failure performs no Task maintenance; an
invocation without a Plan action remains byte-shape compatible with the
existing CLI.

Completion/done/reopen modes are rejected before this control service because
the compatibility completion path has its own Runner selector and cannot be
combined with authoring. A combined edit must produce an actual basis change;
other metadata may accompany that change but cannot make a Plan action valid
by itself. A Plan-only branch is the only supported way to make a config change
without a basis edit. `replace` implies one bounded stdin read; other actions
do not read stdin.

### Activation And Test Allocation

The sequential implementation allocation is deliberately narrow:

- TG-RPA.2 owns only shared pure Plan values, draft decode, transforms, and
  focused action/round-trip/privacy tests, including privacy precedence over a
  recognized leaf's grammar or size failure and no candidate emission, plus
  release-manifest synchronization for its changed packaged files;
- TG-RPA.3 owns only the physical publisher and focused path, no-op
  confirmation, drift, replacement, failure-cleanup, and privacy tests, plus
  release-manifest
  synchronization for its changed packaged files;
- TG-RPA.4 owns only the internal coordination service and focused DB/order/
  partial-success/no-launch tests, plus release-manifest synchronization for
  its changed packaged files;
- TG-RPA.5 alone connects the public parser and synchronizes active formal
  documents, `AGENTS.md`, CLI contract reference, README opt-in examples, final
  package-manifest state, help/output behavior, and focused end-to-end tests;
- TG-RPA.6 changes no product behavior and runs final exact-target acceptance,
  including the full deterministic offline suite.

Every unit is Tier 2 and receives two independent exact-target reviews. Earlier
code units run their focused tests plus manifest/lane/document checks as
applicable; they do not repeat the full suite. The full suite is reserved for
TG-RPA.6. No unit adds a schema/migration, PlanV2, setup/doctor/Viewer/Evidence
route, Skill trigger, public leaf, Runner execution command, automatic command
discovery, re-enable action, hostile-code claim, network action, or M24
redesign.

## Deferred Boundaries

Deferred work includes profile authoring, a public command or Skill trigger for
standalone verification-command execution, external Issue delivery, dependency
graphs, task import, pagination/search,
stale detection, parent/child/checklist execution units, manual backup/restore/
export, generic browser-state persistence, live server, browser launch,
network synchronization, and update checking.

Any extension to M25 Select-Split-Merge-Register must preserve local-first
operation, current privacy and target-project safety, explicit authority for
mutation, narrow
repository boundaries, and concise Skill guidance. It requires synchronized
specification, design, plan, tests, and review rather than reuse of a historical
design capture.
