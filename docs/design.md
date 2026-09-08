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
Select-Split-Merge-Register is active only in the Skill instruction layer.
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
- `cli_handoff.py` owns the Handoff command family and its fixed result shapes.
  It reuses CLI context/read helpers; state resolution, retained-read cleanup,
  dispatch, and post-commit coordination remain in `cli.py`.
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
- `no_replace.py` owns the OS-neutral no-replace entry using those shared
  validators and selecting `windows_no_replace.py`, `linux_no_replace.py`, or
  `macos_no_replace.py` for the native move; callers retain publication and
  cleanup policy.
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
  primitives, binding, operational metadata, Review, Verification Receipt, and
  completion-history/Bundle, Evidence, and Runner repository APIs for existing callers.
  Feature modules do not open raw SQLite connections; Task-row error mapping remains with
  stored-state validation.
- `schema_completion_cycles.py` owns the ordered completion-cycle SQL
  definitions; migration execution and transaction ownership stay in `storage.py`.
- `schema_verification_receipts.py` owns the ordered Verification Receipt SQL
  definitions and versioned completion Verification-basis guards; storage
  retains migrations, validation, and transaction ownership.
- `schema_evidence_ledger.py` owns the ordered Evidence Ledger capture SQL and
  its provenance trigger; storage retains schema validation and migration execution.
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
- `task_show_projection.py` assembles Task detail on the caller's snapshot,
  including its result type and internal latest-history text fallback.
  CLI Runner selection and connection orchestration remain in `cli.py`.
- `stored_task_validation.py` owns source-schema-aware stored Task row/batch
  validation, raw fetches, Contract relationships, and same-snapshot Task reads.
  `storage.py` supplies source-schema limits and the fixed stored-state failure
  boundary rather than duplicating Task semantics.
- `completion.py`, `completion_workflow.py`, and `git_snapshot.py` own typed
  completion evidence, read-only Git observations, completion planning, and
  review-to-commit snapshot binding.
- `completion_history_repository.py` owns latest, single-Task, and batched
  history reads plus their metadata queries on the caller's connection.
  It uses shared storage cycle/Receipt checks and the Evidence validation repository.
- `completion_history_projection.py` owns the bounded public cycle projection;
  `storage.py` alone inserts immutable cycles.
- `completion_bundle_repository.py` owns prepared Bundle validation and
  Bundle/member/Finding-snapshot persistence on the caller's writer. It uses
  the Evidence validation repository's stored Bundle reader; native Bundle/cycle
  coordination remains in `storage.py`;
  the repository neither opens a connection nor commits the operation.
- `verification_receipts.py` owns caller Receipt validation, exact-current
  classification, completion-gate evaluation, and the bounded Task-show read
  model.
- `verification_receipt_repository.py` owns Receipt snapshots, append queries,
  and version-aware legacy-label/internal-subject stored-row validation using
  the caller's connection and writer. `storage.py` retains schema/migrations
  and full database admission; Evidence References, events, and outer
  commit/rollback remain with their existing owners.
- `review_provenance.py` owns the closed Review provenance matrix, canonical
  v1/v0/null public union, and provenance digest without SQLite access.
- `review_repository.py` owns stored Review Receipt/Finding validation,
  provenance relation reads, and atomic Receipt/provenance insertion using the
  caller's connection and writer. Shared admission, Evidence Reference creation,
  events, and outer commit/rollback remain with their existing owners.
- `evidence_ledger.py` owns authority/criterion canonicalization, closed
  assurance/producer dispatch, Evidence Reference projections and digests,
  and capture-version source guards without SQLite access.
- `evidence_repository.py` owns authority/criterion, manifest, Reference, and
  criterion-link persistence and their local stored-data validation on the
  caller's connection. Its manifest-only Reference reader is shared by selected
  Task and Runner consumers.
- `evidence_validation_repository.py` owns shared all-source and selected
  Evidence validation, Bundle relation/history validation, stored/native Bundle
  acquisition, and projection snapshot assembly on the caller's connection.
  It consumes the local Evidence, Runner, and Bundle persistence repositories;
  global admission, recovery policy, and outer transactions remain storage-owned.
- `evidence_projection.py` owns canonical Bundle/index construction, stored
  Bundle reconstruction, digest validation, and captured-basis rendering.
- `verification_runner_repository.py` owns stored Runner record types, graph
  validation, generation/current snapshot reads, and T1, terminal, and restart-
  cleanup database operations on the caller's connection. It uses the local
  Evidence repository APIs; storage retains schema admission and the Evidence
  validation repository owns Bundle validation. The service retains selection,
  process/lock ownership, and outer commit/rollback.
- `verification_runner_selection.py` owns current-basis and snapshot selection,
  stored physical-basis checks, and the terminal classification shared by launch.
  It remains in the Runner service layer; `verification_runner_service.py`
  retains T1/T2 orchestration, process execution, locking, and cleanup.
- `evidence_publication.py` owns storage-backed capture, fixed-path publication,
  generation/outcome recording, and read-only physical projection status.
- `artifact_manifest.py` owns safe bounded Git leaf observation, exact rename
  classification/order, opaque/complete manifests, and manifest digests. Git
  observation is separate from the short DB binding transaction.
- `reviews.py` owns review target, receipt, finding operations and deterministic
  gate evaluation; `review_packet.py` owns bounded read-only review context.
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

### OS Operation Boundary

The [approved platform expansion](specification.md#package-runtime-and-generated-state)
separates only operations whose mechanics differ by OS. The following is the
implementation boundary for that expansion; the module inventory above and
the [Runner registry](runner-execution-design.md#closed-runner-slice-module-registry)
continue to describe the implemented runtime until their owning changes land.

| Operation | Shared or existing caller responsibility | OS implementation responsibility |
|---|---|---|
| Artifact lock | Validate the target and open-file identity, own the lifetime, and map contention/failure through existing callers. | Acquire and release a non-waiting exclusive lock. |
| No-replace publication | Validate containment, source/destination identity, publication order, and failure cleanup. | Perform the no-replace move without an overwrite fallback. |
| Execution environment | Select the admitted runtime and its use period under the Runner contract. | Observe the executable through OS-specific mechanics and construct the OS environment. |
| Process management | Define the execution request; interpret results, decide cleanup acceptance, and construct/persist Evidence through the existing Runner owners. | Launch, monitor, stop, enforce the applicable OS limits, and release native resources. |

Use small internal operation entry points to select the OS implementation;
consumers call the same operation without repeating Windows/Linux/macOS
selection. Do not introduce a general plugin registry or wrap standard-library
operations that already provide the required portable behavior. Task completion,
SQLite transaction ownership, canonical path resolution, and Evidence assembly
do not move into native adapters.

`artifact_lock.py` retains shared lock validation/lifetime; the native
acquire/release mechanism changes behind
it. Windows acquire/release calls reside in `_artifact_lock_windows.py`,
selected internally by `artifact_lock.py`; callers keep the existing
`zero_wait_artifact_lock` entry point and error handling. On Linux and macOS
the internal acquire/release functions use non-waiting `fcntl.flock` on the
independently opened descriptor, so same-process and cross-process contention
are both rejected. Other OS lock mechanics remain unchanged.
`no_replace.py` retains containment and identity checks before and after the
native move;
`windows_no_replace.py` owns only the Windows rename call, while
`linux_no_replace.py` calls libc `renameat2` with `RENAME_NOREPLACE`, and
`macos_no_replace.py` calls `renamex_np` with `RENAME_EXCL`. Both use
filesystem-encoded paths. Missing API or rejected operation raises a failure;
neither falls back to rename, replace, or copy/delete. These OS
primitives do not themselves establish full ordinary-function support.
Setup, backup, Evidence, and Viewer callers retain their operation-specific
publication and recovery policy.
Existing replace-based publication is not converted to no-replace publication.
The existing same-process double-acquisition rejection and subsequent lock
reuse remain part of the lock behavior, not just cross-process exclusion.

Real-OS checks begin with the added file operations and ordinary Task/storage
flows, using the early CI entry where needed. Runner environment/process
changes follow normal-function acceptance and remain a separate implementation
responsibility; this boundary neither changes its current executable-hold,
resource, result, nor cleanup contract. No all-function relocation, file-count
target, or Windows-equivalent native implementation is an acceptance condition.

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

[Setup and state operation design](setup-state-design.md#fixed-state-resolver) owns this complete
section. Shared contracts retain their existing owners.

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

Current detail is owned by the [Database persistence and migration design](database-design.md#migration-sequence).

### Schema-v20 Physical Foundation

<a id="exact-schema-v20-physical-contract"></a>
<a id="schema-v20-public-contract"></a>

Current detail is owned by the [Database persistence and migration design](database-design.md#schema-v20-physical-foundation).

<a id="schema22-reservation-cleanup-design"></a>

## Current Schema-v22 Reservation Cleanup Design

Current detail is owned by the [Database persistence and migration design](database-design.md#current-schema-v22-reservation-cleanup-design).

<a id="schema21-runner-gate-basis-design"></a>

## Schema-v21 Runner Gate-Basis Design

<a id="migration-identity-and-exact-owned-delta"></a>
<a id="closed-gate-tags-and-parent-guards"></a>
<a id="migration-reentry-and-preservation-algorithm"></a>
<a id="bundle-evidence-viewer-backup-and-recovery-compatibility"></a>

Current detail is owned by the [Database persistence and migration design](database-design.md#schema-v21-runner-gate-basis-design).

## Stable Project Identity, Binding, And Relocation

<a id="schema-v14-identity"></a>
<a id="bounded-legacy-and-recovery-discovery"></a>
<a id="relocation-token"></a>
<a id="staged-publication-and-cleanup"></a>

[Setup and state operation design](setup-state-design.md#stable-project-identity-binding-and-relocation) owns this complete
section, including the compatibility subsection anchors above. Shared contracts retain their existing owners.

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

<a id="approved-tg-m16-reduced-loop-discipline-trial-design"></a>

## Reduced Loop Discipline Design

Current detail is owned by the [Task operation design](task-operation-design.md#reduced-loop-discipline-design).

## Setup, Doctor, Backup, And Maintenance

<a id="shared-scope-preflight"></a>
<a id="doctor"></a>
<a id="setup-plan-and-stages"></a>
<a id="maintenance-policy-and-managed-backups"></a>
<a id="post-commit-coordinator"></a>

[Setup and state operation design](setup-state-design.md#setup-doctor-backup-and-maintenance) owns this complete
section, including the compatibility subsection anchors above. Shared contracts retain their existing owners.

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

The bounded legacy counter stored-text helper creates a privacy-only guard view:
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
network request as product behavior. Release operations are repository
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

<a id="current-m25-select-split-merge-register-design"></a>

## Task Decomposition And Registration Design

<a id="session-local-select-split-merge-classifier"></a>
<a id="registration-adapter-and-partial-add-recovery"></a>
<a id="review-tier-resolver"></a>
<a id="mid-task-adapter-and-state-effects"></a>
<a id="atomic-instruction-layer-synchronization-boundary"></a>
<a id="neutral-forward-test-boundary"></a>

Current detail is owned by the [Task operation design](task-operation-design.md#task-decomposition-and-registration-design).

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
- Reduced-loop fresh-session behavioral fixtures plus the current manual/fallback
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
The mixed backup/Viewer performance module is the only timing allocation:
its exact four discovered identities must equal the closed functional/capacity
and manual-timing sets, or validation fails before execution. Local lane runs
without a CI event retain the complete base lane. Pull-request and push
selection remove only the two manual-timing identities; full manual dispatch
removes nothing. The separate platform selection is an ordered filter of
complete validated discovery in `tools/test_lanes.py`. `PLATFORM_SMOKE_MODULES`
selects CLI startup/help, pure Task validation, and implemented artifact
operations; OS-specific native cases run only on their applicable host.
`PLATFORM_ORDINARY_TEST_IDS` adds selected existing physical-install, Task
completion, Evidence, Viewer, Backup, and recovery cases on Linux. The host
selection is repository CI policy, not a product command or LLM choice. Missing
selected identities fail before execution. This is not a fourth base lane and
does not disable or duplicate tests in the exhaustive suite.

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
on both versions with the same exact deferral. Default manual `workflow_dispatch` runs
monolithic `all` on both versions without that deferral, and an
`always()` aggregate job fails unless policy validation and the full matrix
succeed. The workflow trigger remains limited to pull requests, pushes to
`main`, and manual dispatch; a candidate gate cannot be replaced by a skipped
dependency job.

The additional platform job uses Ubuntu 24.04 x86-64 and macOS 15
Apple Silicon with Python 3.12 and the same repository runner's
`--platform-smoke` entry. Linux includes the ordinary-flow selection; macOS
currently retains the common and artifact-operation selection. Manual
`platform_only=true` selects policy and platform
checks alone; the Windows matrix and candidate gate are explicitly skipped and
no release qualification is claimed. The default full route retains both
Windows versions and its aggregate gate. Platform checks do not themselves
qualify a release or establish Runner support; published platform support
remains owned by the release/install contract.

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

Any extension to Select-Split-Merge-Register must preserve local-first
operation, current privacy and target-project safety, explicit authority for
mutation, narrow
repository boundaries, and concise Skill guidance. It requires synchronized
specification, design, plan, tests, and review rather than reuse of a historical
design capture.
