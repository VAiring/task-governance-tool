# Operation-Scoped Admission Correction

## Approved Boundary And Owners

The user approved these three sequential execution units, their persistence,
and implementation on 2026-10-20. This plan owns their scope, ordering and
acceptance only. Current product and design owners remain those routed by
[authority.md](authority.md). Live Task state and evidence belong exclusively
to the public Task CLI.

The outcome is to stop ordinary operations from revalidating unrelated Tasks'
semantic histories. Physical SQLite integrity and foreign-key checks,
schema/object/migration-history and project binding checks remain. Each
operation still validates its actual inputs, selected Task and necessary
relationships in its own snapshot and again under its existing writer lock.
There is no cross-operation cache, caller-controlled bypass or public option.

The required cross-Task predicates remain: predecessor done/cancelled status,
affected-lane ordering and uniqueness, session execution/completion slots,
and rejection of foreign evidence or ownership links. Selected review and
completion operations retain all own history required by their existing gates;
unrelated historical corruption is not certified or repaired by an ordinary
operation. Setup, doctor, migration/reentry and recovery retain global audits.

No schema, live Setup, settings, Hook, host-trust, external service, dependency
installation, Push or CI change is included. Existing unrelated worktree changes
must be preserved. Any newly discovered defect outside these boundaries follows
the existing handoff and user-decision rules.

## Execution Units

All units are sequential in lane `operation-admission`, in the order below.
No unit is optional or independently reorderable. Each leaves its declared
consumers correct while later consumers retain their existing global admission.
The first unit establishes a reviewed internal boundary that the next two reuse.

### Ordinary Task Operations

Order 1 establishes the internal distinction between structural admission and
operation-specific semantic validation. Apply it to Task registration,
start/state/metadata/checkpoint operations and bounded normal Task reads,
including context and completion. Remove unrelated ownership, note, Runner
and review-history traversal from those operations without dropping their
selected-data or locked-basis validation. Keep required cross-Task predicates.
Preserve global defaults for consumers not yet switched by their own unit.

Expected changes are the shared storage/resolver boundary, ordinary Task
adapters/repositories, their formal owners and focused tests. This unit also
lands this execution plan and its authority/checker routing in one revision.
It does not change maintenance or review-service consumers merely because
they share a connection helper.

Acceptance requires public physical-install coverage of start, metadata edit,
bounded selection/detail and completion; unrelated-history isolation; relevant
corruption and foreign-link rejection; ordering/slot failures; locked concurrent
change rejection; and absence of whole-project semantic validation in the
ordinary business path. Physical/schema/binding failures must still reject.

### Review Service Operations

Order 2 consumes the first unit's stable internal boundary for Review Packet,
answer collection/registration, continuation, waiting and integrated-finalization
core reads/writes. Validate the original Task, exact preparation/basis, actual
reviewer, first adopted original and required own history. Preserve all existing
fences, leases, recovery rules, effect ownership and no-unknown-replay behavior.
Do not revive retired tracking or let a changed basis authorize an old worker.

Expected changes are the review service and transport adapters, their coupled
repositories, Review/completion and database/setup owners, and regression tests.
No host behavior or Hook definition is changed. If runtime reload is actually
needed, report the precise boundary and wait for the user's restart.

Acceptance requires normal and failure/recovery coverage for collection,
registration, continuation and finalization; own-history/foreign-link rejection;
unrelated-history isolation; and proof that repeated selected core observations
do not silently return to global semantic admission. Existing registration,
commit, completion and notification success must never be replayed.

### Maintenance Eligibility And Publication

Order 3 separates Evidence, Viewer and backup eligibility metadata reads from
the full validation needed to publish a whole-project artifact or valid backup.
Disabled, current or not-due work must not traverse all Task histories merely
to decide that no publication is needed. Actual whole-project publication and
backup retain their complete validation, generation checks, locks and bounded
retry/cleanup rules. Metadata is rechecked at existing effect boundaries;
an earlier eligibility observation is never a publication proof.

Expected changes are maintenance, Evidence/Viewer publication and backup
adapters, their formal owners and focused integration/performance tests.
Do not change intervals, opt-in values, test thresholds, output datasets,
durability guarantees or warnings to obtain a pass.

Acceptance requires disabled/current/not-due paths without global semantic
traversal; due publication with full validation and unchanged artifacts;
concurrent generation/opt-out/lock failure coverage; last-good artifact and
business-result preservation; and integration with the first two units.

## Verification, Review And Completion

Every unit is Tier 2. Run the narrow meaningful offline boundary tests plus
coupled compatibility tests, document-contract and release checks. Verify
public help/JSON/error/read-only behavior when affected. Use disposable
databases and projects, not the live state, for corruption and concurrency
tests. Keep schema/migration and global-consumer regression coverage.

Obtain two independent reviews of each exact final target using GPT-6.1 Sol,
high effort, as explicitly selected by the user. Resolve high/medium Findings,
record current verification and reviews, create a scoped local commit and
complete each Task only through existing gates. No unit may rely on the next
unit's review to satisfy its own gate. Continue ready approved work at unit
boundaries; managed review waiting follows the current Skill.

## Completion And Retirement

The initiative is complete only when all three Tasks meet their own gates and
the final integration preserves the declared local/global boundary. Durable
behavior must be synchronized into the database, Task, Review/completion,
Setup/state and Evidence/Viewer owners actually affected by each unit.

This plan remains an active conditional execution owner until every dependent
Task is done or explicitly superseded and its durable rules have current owners.
Later retirement requires a separately authorized atomic history/routing
transition under AGENTS.md; Task completion alone does not perform it.
