# Asynchronous Routine Backup Execution Plan

## Authority, Scope, And Retirement

The user approved this plan's three sequential stages and persistence on
2026-10-26, within the existing Task `tg_task_55fce7cfef0e7b53`. They are internal
stages of one Task, not new Tasks or optional follow-up work. Live ownership,
status, verification and review evidence remain in the public Task CLI.

This conditional execution owner is indexed by [authority](authority.md).
Durable behavior belongs to [Setup/state](setup-state-specification.md),
implementation to its [design](setup-state-design.md), and database admission
to the [database owner](database-specification.md). It does not independently
activate a worker or change the installed product.

The predecessor removes synchronous ordinary-command backup calls. This Task
replaces only that route. Migration protection, explicit Setup and recovery
copies, retained valid generations, Evidence, Viewer and completion gates stay
within their existing responsibilities. The approved temporary interval without
automatic ordinary backup ends with the reviewed replacement.

The plan becomes eligible for retirement when the Task is done, the durable
rules are present in their current owners, and no unfinished Task depends on
these sections. Any later physical retirement follows AGENTS.md's separately
authorized, single-revision authority transition; completion alone does not
delete this document.

## Accepted Outcome And Boundaries

- Successful changed business mutations provide the opportunity to start one
  due backup using the configured interval. No new LLM command is necessary.
- The business command publishes one bounded opportunity file and returns;
  it waits for neither worker startup nor backup completion. A service-owned
  child can continue after the CLI's command-execution scope exits.
- One project has at most one active backup, sharing publication exclusion
  with explicit Setup. Repeated updates do not accumulate requests or workers.
- A consistent SQLite copy is captured with a short live-source lifetime.
  Complete structural, relationship and privacy validation runs on that copy.
  Fresh live guards and metadata writers consume only their required identity,
  binding, schema and maintenance/generation state.
- Ordinary updates after capture do not invalidate the snapshot or require an
  unbounded follow-up attempt. They are covered by a later interval opportunity.
- Startup failure and interrupted work remain distinguishable from success.
  The next ordinary update can recover without trusting a stale PID or saved
  running label. Read-only diagnosis never repairs or launches work.
- Capture time, successful publication and Task completion are distinct facts.
  Unknown observations remain unknown. Failure preserves the last valid backup.
- No live-database fault/performance experiment, existing-backup deletion,
  optional-feature reconfiguration or unrelated logging optimization is included.

### Service-Owned Launch Qualification

The approved startup revision uses the already configured project MCP service
as the launch owner, with a separate one-attempt backup worker. The service
accepts only a bounded local backup opportunity; it does not perform the copy,
wait for its completion, or attach backup lifetime to a review wait. Backup
completion ends the backup worker, not the service. The CLI neither starts a
second service nor falls back to synchronous copying when admission is absent
or uncertain. No additional LLM command or unbounded request queue is added.

First qualify this boundary in an isolated installed project. Exercise an
actual service process and worker across separate command-execution lifetimes,
then prove duplicate admission, client disappearance, service interruption and
recovery preserve existing exclusion and successful generations. An open test
service proves that topology only; it does not certify the configured Codex
MCP service's lifecycle. Keep that distinction in the qualification result.
Do not modify live host settings, restart the current review service, or test
faults against the canonical development database for this experiment.

The user approved production adoption of the isolated-qualified fixed-file
transport on 2026-10-26. It is local, project-bound, bounded and narrowly typed,
with no caller-selected executable or state path. Define service availability,
caller admission, shutdown and uncertain-result behavior in the existing
Setup/state owners and synchronize the MCP service lifecycle owner.
The single coalesced opportunity has no request history or acknowledgement
claim: publication means requested, not started. Its service checks only that file at a ten-second
interval, never the database, and admits a new request identity at most once
while it remains the current observed request. It is not a historical replay
ledger. Startup records the existing request as a baseline;
recovery requires a subsequent eligible mutation. An OS service lease and its
fresh owner identity fence the request across service restarts and takeover.
Protocol/schema agreement is a cheap publication check. Loaded/deployed code
agreement is checked only before an actually due worker launch. The CLI's
eligibility read consumes only current binding and maintenance policy; it does
not repeat full schema or Task-history validation. Service startup identifies its
current target; after acquiring service ownership it revalidates that exact
target and enabled policy before publishing presence. Admission or presence
activation failure ends that observer until service restart; standby lease
retries and idle file observations perform no database or package scan.
Production qualification must still pass the physical-file, concurrency and
host gates; the isolated prototype alone is not completion evidence.
The copy, validation, publication and recovery responsibilities below remain
unchanged; this is a startup revision within stage one, not a new work lane.

## Sequential Implementation Stages

### 1. Worker Startup, Ownership, And Observation

Implement a project-local, bounded operational record and separate launch and
worker exclusion. The existing service owns child creation and its startup
handshake; the originating CLI publishes only the bounded opportunity. An attempt
identifier fences late or interrupted children. OS ownership
and current observations govern recovery, not lock-file age or PID existence.
The child entry is internal, with no new public normal-loop command or scheduler.

Expected changes: dedicated async runtime/state modules and internal worker
entry, fixed-state path ownership and admission, with focused startup/ownership
tests. Preserve unrelated dirty source and diagnostic changes by developing in
an isolated checkout.

Stage verification: simultaneous launch, parent exit, child not starting,
startup timeout, stale attempt, absent/unsafe/malformed runtime state, read-only
observation and bounded retained state. No database lock is held across process
startup, waiting or other external effects.

### 2. Short Capture, Private Validation, And Publication

Add the routine-specific repository and copy/publication path. Keep the shared
Setup/migration/recovery primitive's validation contract intact. Capture and
close the live source, validate the private snapshot, and guard publication
against database replacement, schema/binding drift and conflicting generation
metadata. Business-row updates after capture are allowed.

Retain file-publication-before-metadata and file-before-row pruning. Recover
bounded file-only/row-only residue under publication ownership, without
importing or deleting unknown, linked or foreign artifacts. Success metadata
advances only after successful validated publication and its required commit.

Expected changes: dedicated async backup repository/capture modules, limited
shared helpers where needed, and Setup/database specifications and designs.

Stage verification: last-good preservation at copy, validation, publication,
metadata and prune failures; crash reconciliation; equal-count metadata changes;
database replacement; schema/binding changes; explicit Setup/recovery protection.
An independent Task writer must commit while heavy private validation runs.

### 3. Normal-Command Integration And Qualification

Connect the short launch opportunity after a successful business commit and
connection close. Preserve Evidence/Viewer responsibilities. Read-only, failed,
replayed, no-op, Setup/configuration and maintenance-internal operations do not
launch ordinary backups. Expose bounded runtime observation through existing
diagnosis and retain fixed warnings that do not change the business result.

Expected changes: maintenance, diagnosis/JSON contract, package manifest and
Skill/release guidance; integration, performance and subprocess tests. Synchronize
all applicable current owners before reviewing the final material.

Verification uses isolated canonical projects, controllable workers and actual
subprocesses. Cover first/due/not-due opportunities, updates during work, CLI
exit and child interruption, and recovery on the next update. Measure command
return latency, live read/write lifetime, SQL and connection counts alongside
data correctness. Counts and timing do not substitute for safety assertions.

## Gates And Completion

The stages are sequential and none is optional. They share one final coherent
review target and one Task completion; intermediate test success is not Task
completion. No new execution lane or independent follow-up is introduced.

Run the relevant backup, Setup/recovery, maintenance, CLI/Doctor, state-path,
document and distribution-contract checks, including focused failure and
concurrency regressions. Use existing repository test policy for broader gates.
Tier 2 requires two independent reviews of the exact final target and no
blocking high/medium Findings. Correct valid Findings and repeat affected
verification on the corrected material, following reconciliation when triggered.

Commit only the verified and reviewed Task-owned change, preserving others'
uncommitted work, and complete through the current public workflow. Report the
actual verification, review limitations and remaining relevant risks. Passing
local tests does not imply unrun CI/platform or live-host checks passed.
