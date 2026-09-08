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

- `cli_parser.py` owns public parser construction, common options, and
  sanitized parser error types.
- `cli.py` preprocesses lexical root options, uses that parser, orchestrates
  state and maintenance, and dispatches services and output emission.
- `cli_output.py` owns the shared `CommandResult`, result construction, JSON
  size/identity fitting, bounded errors, and JSON/text stream emission.
- `cli_text.py` formats the existing Task, completion-check, Handoff, Review,
  and Verification Receipt human text from supplied projections. It does not
  select Tasks or perform operations; Review Packet rendering stays in
  `review_packet.py`.
- `compact.py` owns compact task projections and final byte caps.
- `project_scope.py` validates the governed root, physical package layout,
  self-host exception, containment, and effective Git-ignore preflight.
- `state_paths.py` defines fixed state names and shared containment and
  physical-identity validation.
- `windows_no_replace.py` owns the Windows no-replace rename operation using
  those shared validators; callers retain publication and cleanup policy.
- `state_resolver.py` is the sole production resolver for fixed state, bounded
  legacy discovery, identity, binding, recovery observations, and artifact
  targets.
- `state_transition.py` owns private staging, no-clobber publication, and
  bounded legacy cleanup.
- `sqlite_connection.py` owns configured SQLite connections, registered SQL
  functions, journal/sidecar preflight, and sanitized low-level errors.
- `project_binding_repository.py` owns validated binding snapshots and lineage,
  binding compare-and-swap, and cleanup-pending metadata updates. It uses the
  initialized connection/writer entry in `storage.py`; relocation intent,
  filesystem cleanup, and initialization orchestration remain outside it.
- `backup_metadata_repository.py` owns maintenance-policy rows and atomic
  managed-backup generation, pointer, outcome, and retention metadata. It uses
  storage-owned admission, including the existing migration-source path;
  physical copy, reconciliation, pruning, locking, and recovery selection stay
  with their existing callers.
- `viewer_metadata_repository.py` owns Viewer maintenance read/seed and
  publication/attempt-outcome metadata. It keeps storage-owned admission and
  the existing short writer transactions; snapshot capture, rendering, file
  publication, policy, and locking stay with their existing callers.
- `evidence_projection_metadata_repository.py` owns projection-state read/seed,
  cycle-owned source-generation advance, and publication outcomes. Locked entries
  reuse the caller's transaction; the outer outcome writer uses storage admission.
  Bundle construction/capture and file publication remain with existing owners.
- `storage.py` remains the shared entry for migrations, initialized admission,
  repositories, and transaction-scoped queries, re-exporting the connection
  primitives, binding APIs, and operational metadata APIs for existing callers.
  Feature modules do not open raw SQLite connections; Task-row error mapping remains with
  stored-state validation.
- `schema_completion_cycles.py` owns the ordered completion-cycle SQL
  definitions; migration execution and transaction ownership stay in `storage.py`.
- `schema_verification_receipts.py` owns the ordered Verification Receipt SQL
  definitions and versioned completion Verification-basis guards; storage
  retains migrations, validation, and transaction ownership.
- `schema_evidence_ledger.py` owns the ordered Evidence Ledger capture SQL and
  its provenance trigger; storage retains validation and migration execution.
- `schema_completion_evidence_bundles.py` owns the ordered Bundle SQL
  definitions, versioned Bundle tables, criterion-link matrices, cycle
  Evidence-basis guards, and their immutable predecessor schema tags; storage
  retains migration, sealing, and Bundle/cycle transactions.
- `schema_verification_runner.py` owns versioned Runner table, index, and
  trigger SQL and pure SQL normalization support; storage retains schema
  recognition, migration composition/execution, and transaction ownership.
- `task_values.py` owns shared Task scalar/text/privacy validation, its
  constants, and the existing `TaskValidationError` type. Task, Contract,
  Review, and storage consumers import these values directly.
- `tasks.py`, `ordering.py`, and `selection.py` own Task operation validation,
  lifecycle, current/list projections, the shared sequential predecessor
  predicate, and next-task selection.
- `stored_task_validation.py` owns source-schema-aware stored Task row/batch
  validation, raw fetches, Contract relationships, and same-snapshot Task reads.
  `storage.py` supplies source-schema limits and the fixed stored-state failure
  boundary rather than duplicating Task semantics.
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
- `evidence_projection.py` owns canonical Bundle/index construction, stored
  Bundle reconstruction, digest validation, and captured-basis rendering.
- `evidence_publication.py` owns storage-backed capture, fixed-path publication,
  generation/outcome recording, and read-only physical projection status.
- `artifact_manifest.py` owns safe bounded Git leaf observation, exact rename
  classification/order, opaque/complete manifests, and manifest digests. Git
  observation is separate from the short DB binding transaction.
- `reviews.py` owns review target, receipt, finding, and deterministic gate
  evaluation; `review_packet.py` owns bounded read-only review context.
- `contract_content.py` owns Contract field selection, normalization, authority
  value validation, and supplied stored-row content validation/projection.
- `contracts.py` owns Contract reads, immutable revisions, and invalidation.
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
[Trusted-Local Runner Architecture](runner-execution-design.md#trusted-local-runner-architecture).

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
manifest modules, and preserves discovery order when filtering. Within the one
mixed backup/Viewer performance module, a closed four-ID allocation identifies
two deterministic functional/capacity tests and two manual wall-clock
qualification tests. CI event filtering occurs only after the complete module
manifest and this allocation validate. `all` is a meta-lane over the unchanged
standard suite, not a fourth maintained list.
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

`cli_output.py` carries the shared result and formats its public envelope.
Its internal mutation and maintenance metadata is preserved for `cli.py`,
never serialized. Command context, dispatch, connection ownership, and the
post-commit coordinator remain in the orchestration layer.

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
existing-database read, write, or migration, the storage caller uses
`sqlite_connection.py` journal preflight, which:

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

`project_binding_repository.py` compares identity, expected generation, and old hash
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

<a id="task-model-and-events"></a>
<a id="shared-stored-task-rowbatch-validator"></a>
<a id="stored-contract-pointer-relationship-boundary"></a>
<a id="sequential-ordering"></a>
<a id="done-immutability-and-reopen"></a>

Current detail is owned by the [Task operation design](task-operation-design.md#task-state-and-selection).

## Completion Evidence And Review

[Review, Verification, and completion structure](review-completion-design.md)
has one owner; Evidence provenance and construction retain their separate
delegation below.

### Typed Completion Evidence

Current detail is owned by the [Review and completion design](review-completion-design.md#typed-completion-evidence).

### Review Target And Git Snapshot

Current detail is owned by the [Review and completion design](review-completion-design.md#review-target-and-git-snapshot).

### Receipts, Findings, And Gate

Current detail is owned by the [Review and completion design](review-completion-design.md#receipts-findings-and-gate).

### Provenance, Evidence Ledger, And Bundle Structure

<a id="capture-and-projection-module-ownership"></a>
<a id="schema-v18-capture-and-subject-foundation"></a>
<a id="evidence-reference-and-manifest-construction"></a>
<a id="schema-v19-bundle-and-evidence-publication"></a>

Current detail is owned by the [Evidence design](evidence-design.md#provenance-evidence-ledger-and-bundle-structure).

### Review Packet

Current detail is owned by the [Review and completion design](review-completion-design.md#review-packet).

## Test-Only Independent Evidence Reader

Current detail is owned by the [Evidence design](evidence-design.md#test-only-independent-evidence-reader).

## Completion Cycle History

<a id="schema-v15-through-v20-activation"></a>
<a id="native-done-and-reopen-transactions"></a>
<a id="public-history-projection"></a>

Current detail is owned by the [Review and completion design](review-completion-design.md#completion-cycle-history).

<a id="schema-v21-manual-receipt-arm-and-bundle-integration"></a>

## Current Schema-v22 Manual Receipt Arm And Bundle Integration

<a id="ownership-and-data-model"></a>
<a id="receipt-write-and-freshness"></a>
<a id="completion-and-read-integration"></a>
<a id="schema-viewer-packaging-and-tests"></a>

Current detail is owned by the [Review and completion design](review-completion-design.md#current-schema-v22-manual-receipt-arm-and-bundle-integration).

## Task Contracts, Checkpoints, Handoffs, And Effort

<a id="immutable-task-contract-revisions"></a>
<a id="typed-checkpoints"></a>
<a id="local-handoff-outbox"></a>
<a id="optional-effort-advisory"></a>

Current detail is owned by the [Task operation design](task-operation-design.md#task-contracts-checkpoints-handoffs-and-effort).

## Approved TG-M16 Reduced Loop Discipline Trial Design

Current detail is owned by the [Task operation design](task-operation-design.md#approved-tg-m16-reduced-loop-discipline-trial-design).

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

`backup_metadata_repository.py` owns the shared maintenance-policy read/seed
and backup-policy configuration, plus managed-generation, pointer, outcome,
and retention writes and their direct record validation. The shared
`ProjectMaintenanceState` retains its Viewer fields; Viewer publication writes
belong to `viewer_metadata_repository.py`. Storage retains schema admission and migration
orchestration, and backup/setup retain physical publication and reconciliation.

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

The current snapshot publication, presentation profile, browser application,
and security structure is owned by the [Viewer design](viewer-design.md).
Shared state, validation, completion-history, and post-commit orchestration
remain in their respective sections of this document.

## Privacy, Safety, And Failure Boundaries

`task_values.py` is the shared value-validation owner. Caller input and the
existing bounded stored-legacy mode remain distinct paths using the same
exception type and unchanged validation order. Task operation records,
lifecycle, stored-row relationships, and database work remain outside it;
`tasks.py` imports the shared exception rather than defining a second type.

Every free-form input uses the common deny-by-default privacy guard. Fields
with defined contracts also have code-point/UTF-8 bounds; the existing `lane`
and `blocked_reason` fields are privacy-validated but have no numeric character
cap. `lane` is trimmed; `blocked_reason` is retained as supplied after
string/privacy and state validation. The guard rejects likely credentials and raw dumps,
including authorization/Bearer headers, private-key blocks, password/token/API
key assignments, Python traceback headers, raw stdout/stderr headings, and
repeated raw Git diffs.

The broad sensitive-key assignment branch excludes only the three exact
numeric metadata forms defined in the specification (`max_tokens`,
`token_count`, `password_length`). A branch-local lexical exclusion keeps the
original input visible to every other detector; it neither short-circuits the
guard nor transforms stored bytes. The test-only Evidence reader independently
implements the same policy without importing the production guard. Existing
field limits, JSON scalar handling, legacy reads, and error mapping are unchanged.

The authorization-assignment branch similarly excludes only the complete
plain-text redacted Bearer example defined by the specification. Its lexical
header/value boundaries are checked within that branch; all other detectors
still see the original input. The independent Evidence oracle implements the
same policy separately. No sentinel replacement, automatic redactor, new mode,
or stored-content rewrite is introduced.

The Task `title`/`description` manual-diagnostic branch is field-gated beside
the existing raw-output matcher. It recognizes exactly one case-sensitive
literal `: stderr: ` or `: stdout: ` delimiter, nonblank context and body, and
requires `value.splitlines() == [value]`; comparing the value itself, rather
than only the returned element count, rejects trailing line breaks. A candidate
with one literal delimiter but an empty side or a line boundary remains a
privacy rejection. Bare, nonliteral, and multiple-delimiter candidates receive
no new branch and retain the existing field-specific raw-output decision;
therefore strict `description` still rejects them while `title` retains its
pre-existing benign-wording allowance.

The common guard applies every existing detector to the unchanged full value.
For a valid form it admits only the raw-output match whose offset is the outer
literal heading, then applies the guard to the extracted body with the manual
branch disabled and raw-output handling forced strict. This makes an inline
stack frame visible at the body boundary and prevents nested headings from
using either the manual branch or the title wording allowance. Other fields
retain their existing raw-output behavior. Stored Task validation, authority
snapshot capture/read, and Review Packet preparation already route
`task_title`/`task_description` through the `title`/`description` names and need
no separate matcher or shape change. Completion Evidence retains the existing
Task fields. The test-only Evidence reader implements the same decisions
independently and does not import the production matcher.

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
authorization material, automatically captured or free-standing raw
stdout/stderr, stack traces, environment dumps, full prompts/conversations,
private reasoning, raw reviews, large diffs, unsafe paths, and OS/SQLite/Git
exception detail. The only stream-text exception is the validated manual Task
quotation above, retained within existing Task title/description fields rather
than a new stream field. Review, Contract, handoff, checkpoint, completion, and
history projections use explicit allow-lists and revalidate stored text before
output.

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

<a id="session-local-select-split-merge-classifier"></a>
<a id="registration-adapter-and-partial-add-recovery"></a>
<a id="review-tier-resolver"></a>
<a id="mid-task-adapter-and-state-effects"></a>
<a id="atomic-instruction-layer-synchronization-boundary"></a>
<a id="neutral-forward-test-boundary"></a>

Current detail is owned by the [Task operation design](task-operation-design.md#current-m25-select-split-merge-register-design).

<a id="trusted-local-runner-architecture"></a>

## Trusted-Local Runner Architecture

<a id="closed-runner-slice-module-registry"></a>
<a id="target-plan-implementation"></a>
<a id="typed-process-value-boundary"></a>
<a id="cleanup-acceptance-and-privacy"></a>

Current detail is owned by the [Runner execution design](runner-execution-design.md#trusted-local-runner-architecture).

## Runner Parent Service And Audit Graph

Current detail is owned by the [Runner execution design](runner-execution-design.md#runner-parent-service-and-audit-graph).

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
  deterministic performance-fixture behavior/capacity, manual timing
  qualification, package/license, active documents, workflow policy, and
  integrated release acceptance.

Every run first validates the complete manifest. A base lane is an ordered
filter of standard discovery, while `all` runs the original discovered suite
in its original order. New `test*.py` modules therefore fail closed until
classified; new methods in an owned module inherit that module's lane.
The mixed backup/Viewer performance module is the only narrower allocation:
its exact four discovered identities must equal the closed functional/capacity
and manual-timing sets, or validation fails before execution. Local lane runs
without a CI event retain the complete base lane. Pull-request and push
selection remove only the two manual-timing identities; manual dispatch removes
nothing.

The retired LPAC module, mandatory native fixture, and dedicated route tests are
absent from standard discovery. No such residue remains
in the standard test partition, which continues to contain only the three base
lanes `fast`, `integration`, and `release`. No replacement meta-test framework,
implementation-shape-only assertion, disabled test, or new SKIP may stand in
for a test that detects a current requirement or regression.

CI obtains one compact include matrix from this same policy. Pull requests run
all three base lanes on Python 3.12 and `fast` on 3.14; the release selection
retains deterministic performance-fixture behavior/capacity but defers the two
wall-clock qualifiers. Pushes to `main` run all three base lanes independently
on both versions with the same exact deferral. Manual `workflow_dispatch` runs
monolithic `all` on both versions without that deferral, and an
`always()` aggregate job fails unless policy validation and the full matrix
succeed. The workflow trigger remains limited to pull requests, pushes to
`main`, and manual dispatch; a candidate gate cannot be replaced by a skipped
dependency job.

Tier-2 changes use two independent exact-target reviews. Tests hard-fail count,
byte, subprocess, attempt, and render limits; performance budgets never justify
loosening a deterministic bound or adding asynchronous architecture.

The release-lane backup/Viewer performance module separates two deterministic
functional/capacity methods from two wall-clock qualification methods through
the closed test-ID allocation above. Each fixed fixture uses fresh database
copies for one warm-up round and six measured rounds. The three-mode qualifier
uses all six permutations once, while the two-mode qualifier repeats both
orders equally. One local helper classifies the same-round paired enabled-minus-
disabled overhead using the six-sample median, and the median observation at
each command position against the mode-specific budgets: 10 seconds for backup-
only and Viewer-only total overhead, 12 seconds for combined Viewer-plus-backup
total overhead, and below 5 seconds for every command-position median. The
helper emits only bounded numeric diagnostics. The warm-up is excluded from
qualification; no functional, count, byte, attempt, render, or call failure is
excluded or statistically masked. Only the manual release-candidate jobs use
the timing methods as blocking evidence.

## Current Runner Plan Authoring And Control Design

The current Runner Plan authoring/control structure is owned by the
[Runner Plan authoring design](runner-plan-authoring-design.md).
The authoring detail links directly to the Runner execution and Task operation
owners. Shared post-commit coordination remains in this document.

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
