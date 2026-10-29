# Integrity Validation Placement And Transition Design

## Authority, Activation And Retirement

This initiative-scoped conditional owner formalizes the user's approved
[proposal v2.1](proposals/integrity-check-placement-proposal-v2.1.md). The user
authorized design, follow-up Task registration and completion of the design
Task on 2026-10-29. It owns the new placement rules, transition boundaries,
protection prerequisite and acceptance below. It does not activate a runtime
change, execute a backup, authorize live restore, or replace current gates.
The proposal is retained input, not a competing runtime specification.

The [authority index](authority.md) routes this owner and the
[transition inventory](integrity-validation-inventory.md). Existing behavior
remains owned by [database](database-specification.md),
[Task operation](task-operation-specification.md),
[Review/completion](review-completion-specification.md),
[Evidence](evidence-specification.md), [Viewer](viewer-specification.md) and
[Setup/state](setup-state-specification.md), with their paired designs.
Runner authoring and execution retain their respective
[Plan](runner-plan-authoring-specification.md) and
[execution](runner-execution-specification.md) owners.
Each implementation unit updates its affected durable owners in the same
reviewed revision as its code and tests. An unconverted consumer keeps its
current admission; merely publishing this design never weakens it.

In particular, the earlier [operation-admission plan](operation-admission-plan.md)
retained full physical/FK and structural checks in ordinary entry. This design
changes that boundary only through the units below. It does not retroactively
reinterpret the earlier Tasks' acceptance. Earlier schema migrations and their
historical fingerprints remain supported; no retrospective data repair is added.

Live status, Task IDs, results, checkpoints and completion evidence belong to
the public CLI. The unit labels below are document anchors, not another Task DB.
Retirement requires all dependent Tasks complete or explicitly superseded,
durable rules in their current owners, and no unresolved dependent gateway.
History/routing retirement still requires its own authorized atomic transition.

## Outcome And Non-Goals

Ordinary operations validate the data and relationships they consume or may
change. They do not certify unrelated historical data or run full-database
physical/reference scans merely to enter a command. Sealing and external
acceptance validate the complete relevant graph. Full audits remain with
explicit diagnosis, lifecycle acceptance and the backup's private copy.

The implementation must remove obsolete call edges, not just leave the old
checks hidden behind another default or add a second full check beside them.
There is no supported interval with all old checks removed and replacements
absent. One unit cuts over its consumers and retires their old calls together.
A backup is a recovery point, not authorization to run an unguarded intermediate
version or proof that later legitimate work can be discarded without loss.

No process-wide certificate for mutable DB contents, mtime/size-based trust,
periodic auditor, new service, per-method logging framework or ordinary Skill
polling/diagnostic prerequisite is introduced. Malicious direct DB editing is
outside this initiative's guarantee. No new hash is required for every row.
Unknown, unavailable, conflicting and corrupt data never become valid or zero
merely to improve performance. Existing privacy bounds and sanitized errors apply.

## Placement And Preserved Guarantees

| Class | Meaning | Ordinary operation | Acceptance or audit |
|---|---|---|---|
| A | Physical SQLite structure | Handle actual read errors; no full DB scan at routine entry | Required physical tests on the accepted/audited copy or state |
| B | Referential integrity | Enforce FKs and validate consumed edges, including reverse identity arms | Full foreign-key audit where the whole state is accepted |
| C | Schema and format contract | One admitted structural basis per coherent read; bounded owned-object/version checks | Exact supported schema, history and applicable object inventory |
| D | Domain semantics and provenance | Selected data and dependency closure; current eligibility and writer basis | Complete relevant sealed graph, or whole domain for explicit full audit |
| E | Sealed content identity | Hash only the actually consumed immutable content, with bounded reuse | Establish content and binding before sealing or certifying a copy |

`quick_check`, `integrity_check` and `foreign_key_check` retain distinct meaning.
The first two are not interchangeable guarantees; FK checking is not a physical
check. `foreign_keys=ON`, read transactions, writer locks, conditional updates,
uniqueness and CHECK constraints are retained controls, not obsolete audits.

Target scope is a dependency closure, not necessarily one row or one Task ID.
It includes current Contract/authority/target, owner and execution generation,
required prior Findings and continuation ancestry, referenced Receipts and
cross-Task slot/lane predicates. Foreign ownership must be rejected when reached
through either identity arm. Filtering must not hide malformed target rows.
An ordinary operation does not traverse the other Task's unrelated history.

List/count/selection work that intrinsically needs multiple rows remains valid.
The performance requirement applies to additional validation of unrelated data,
not to suppressing requested output, exact counts or required shared partitions.
Input/privacy/path checks and protocol decoding retain their own boundaries.

Full acceptance remains for initial construction, restore, normalization and
post-migration publication. `doctor` returns results without persisting an
audit record or repairing anything. Normal successful journal recovery alone
does not trigger a full scan. Busy/cancelled/rolled-back operations are not
corruption evidence; a concrete inconsistency or recovery failure uses the
existing explicit diagnosis/recovery path, without an automatic audit loop.

### Coherent Read And Reuse

A private admitted-read object may carry the resolved project/binding, schema
version and rule version, owned-definition result and already validated selected
facts. It is produced only by repository admission, not supplied by the caller.
Its lifetime is lexical and tied to the actual connection and transaction epoch,
not just `in_transaction=true`. Existing savepoint/epoch techniques may be reused.
There is no persisted validation table or public bypass flag.

The first consumer establishes the applicable basis. Synchronous consumers in
that same read reuse it instead of reopening a DB, repeating schema/history
SELECTs or reconstructing an identical graph. A standalone consumer establishes
its own basis. Exit, connection replacement, transaction end/restart, relevant
local mutation, DDL or binding/restore change invalidates the corresponding
facts. A schema-only fact is not a valid Task or review gate fact.

Within a writer transaction, immutable originals can be borrowed while their
immutability holds. Mutable Task/Receipt/Finding/current-pointer facts must be
read or invalidated after a relevant write. Preflight facts never authorize a
later writer: fresh writer admission and exact basis comparison remain required.
External Git/host/filesystem effects end the reuse scope before the subsequent
effect guard. No read lock is stretched across those effects to keep a cache valid.

Owned schema metadata may be fetched in a bounded batch instead of one query
per definition. The comparison must preserve expected definitions, attached
objects and marker identity; `MAX(version)` alone is not schema validation.
The first implementation uses same-read sharing only. A cache across unrelated
connections/processes is not part of this design and requires separate authority.

### Sealed Content And Current Eligibility

A digest certifies only the closed content and context included in its encoding.
It must bind object kind/format/rules and project/Task/target/Contract identities
where applicable. Referenced content outside that seal still needs its own
admission. An old PASS or Bundle can retain identical bytes after reopen while
being ineligible for the current gate. Current selection, supersession,
ownership and restored references therefore remain target-scoped checks.

Use existing digests and immutable originals. Reuse their validation within an
admitted read; do not reserialize an entire history just to replace SQL by hashing.
Mutable rows use selected-value sharing, not a new permanent hash scheme.
Hash/decoding work is measured by bytes and invocations as well as SQL count.

## Transition Inventory And Closure

The companion inventory names definitions, consumers, guarantee, read scope,
disposition, new responsibility, implementation owner and regression anchors.
Every discovered family is assigned. Shared helpers have separate rows when
ordinary entry and lifecycle acceptance use them differently. Maintain/retain is
an explicit disposition, not an omission from the list.

Before each unit changes code, expand its assigned families to the exact current
callsites and compare reverse callers, direct SQL and wrappers. Record additions
under the same responsibility; do not silently expand another Task's Contract.
Use `rg` plus call/import and test inspection; a keyword count is not coverage.
At closure, every retired edge has its replacement or deletion rationale, every
retained edge has its owner, and no unclassified or unowned affected edge remains.
The inventory is design/transition evidence, not a new runtime registry.

Existing work keeps its current scope and order:

| Existing Task | Sole owned change | Relationship to this initiative |
|---|---|---|
| `tg_task_956dfaa96df08a57` | Remove ordinary Task Usage retrieval | Preserve its normal-display boundary; do not register it again |
| `tg_task_a5f10d38b4deac04` | Incremental Evidence publication without full DB audit | Own Evidence capture/index/publication changes; consume common admission |
| `tg_task_469a9a022b76f9e7` | Changed-target Usage reconciliation | Own global basis/ownership/response reclassification reduction |
| `tg_task_7044a15b7de5aa3a` | POSIX lock safety of live DB file reads | Preserve/consume its safe connection/file identity primitive; never bypass it for a cache |
| `tg_task_0697e3afb197fac4` | Collected Receipt/Finding write consistency | Preserve its rejection and atomicity guarantees while changing read placement |

These are scope references, not assertions of completion. Public CLI checks at
unit boundaries determine the actual predecessor state. Shared files alone do
not authorize moving work from those Tasks. Cross-owner integration must check
their finished outputs without changing their Contracts.

## Pre-Change Protection Design

### Explicit Capture Surface

The current CLI has no force-now, wait-for-completion backup command. Re-running
Setup at the current schema, changing backup policy or making an artificial Task
edit is not a substitute. The opportunity file is not proof of a captured copy.

Unit IV-P introduces a narrow proposed `backup protect` operation, only for
explicit maintenance. This is a future API, not an available command to run now.
It requires the existing canonical resolver and has no `--db`, arbitrary source,
automatic restore, configuration toggle or periodic invocation. Normal Skill
work does not call it. It does not migrate a source or repair its data.

The operation uses a dedicated, fixed `.taskgov/protection/<capture-id>/` layout
outside routine rotation. The generated ID and file names are tool-owned. Path
admission preserves effective ignore, regular physical paths, no links/junctions,
no overwrite and existing project binding checks. A private sibling staging area
is published only after successful validation, with the manifest last. Failed
staging is never a successful capture and cannot replace a previous protection.
Explicit removal of retained protections is outside this initiative.

Capture core and, when present, the numerical Usage store using consistent
SQLite backup operations and existing repository connection controls. Close live
connections before heavy inspection. Reuse private-copy validators and immutable
content-proof primitives, not routine publication/pruning as a whole.
The source DB is not hashed by opening its file alongside SQLite connections.
Normal writer contention follows bounded existing failure handling, not an
unbounded retry or a long source lock across validation.

Core and Usage are independent snapshots. A closed manifest records format,
project/binding identity, component presence, schema/rule versions, capture
times, file names, size/digest and validation result. No raw SQL, rows, prompt,
log or secret is copied into metadata. No common instant or paired transaction
is claimed. Missing Usage is explicit absence; an existing but unreadable,
uncopyable or invalid Usage store prevents a complete protection success.

Operational wait/finalization state and host effects are not business backup
components. Preserve their existing recovery/fencing rules; a core restore must
never revive an old worker or replay an unknown commit/send/registration.
A protected copy is not evidence of current Task completion.

### Recovery Proof And Activation Gate

Protection must establish a recovery procedure, not merely two copied files.
IV-P's isolated acceptance restores the protected core through repository-owned
normalization and applicable migration/admission, validating Task/Evidence
relations and managed-backup metadata. It separately restores/admits Usage and
reconciles its references against restored core; absent anchors remain
unknown/unbound rather than being assigned to a different execution.
Copied or normalized outputs cannot inherit the original content certificate.
Live overwrite/restore is a separate explicit user operation, never automatic.

Plain core bytes are not a current managed recovery candidate. The protection
format therefore seals a newly identified, unregistered managed-format core
generation beside the copy. Its valid generation key must be later than every
generation embedded in the captured repository; its ID is not inserted into the
live ledger. Unprovable ordering or invalid source metadata fails protection;
do not invent a timestamp or rewrite the source to pass.

The isolated adapter materializes that one generation in a private recovery
inbox. Existing candidate admission permits bounded older row-only generations,
and repository normalization removes those unmatched rows only from the private
restored DB, imports the candidate and resets derived Viewer state. Historical
backup bodies need not all be copied. Validate the allowed normalization delta
and equality of business records. This adapter does not add arbitrary-directory
imports to ordinary Setup or weaken its no-clobber/binding rules. IV-P must prove
this format through the actual recovery repository, not a mock of that boundary.

The capture manifest and verification record must survive normal backup rotation.
The procedure identifies capture time, source identities, code/schema basis,
storage location and the isolated restore result. Later legitimate writes are
not in that capture; any real restore must account for them rather than imply
lossless rollback to the initial point.

After design and before the first validation-behavior change, run the new
maintenance capture under explicit authorization and confirm successful
publication plus the isolated recovery check. If source admission/copy/validation
or recovery proof fails, do not start a checker cutover. Correctness repairs
needed to admit the baseline keep their existing Tasks; never disable the old
check merely to obtain a backup. Later units verify that protection still exists
and is recoverable for their binding, physical-source incarnation and schema/
package basis; refresh and rehearse it if those compatibility conditions change.
Ordinary later Task writes do not invalidate the captured point or require a
new capture per command. They remain explicitly outside that point's coverage.

## Ordered Implementation Units

The user authorized registering these follow-ups; registration is not execution.
Use one sequential lane after the design Task. Each unit has its own verification,
two independent Tier 2 reviews and coherent commit. Existing external Tasks keep
their lanes; a cross-owner prerequisite is checked explicitly before dependent
work, without inventing another dependency graph or silently reordering them.

### IV-P: Explicit Maintenance Protection

Implement the capture surface, protected artifact, validation/retention boundary
and isolated recovery proof above. Synchronize Setup/state, database and CLI
owners, packaging and explicit maintenance guidance. No normal-loop instruction,
new background service or unconditional automatic backup is added.

Completion requires core-only and core-plus-Usage cases, absent versus failed
Usage, contention, copy interruption, manifest-last/no-replace publication,
source replacement, invalid content, retained-generation survival and isolated
restore/normalization. Confirm the real pre-change protection only after the
operation is verified; no old checker is changed by this unit. The POSIX safety
Task's primitives must be available for any overlapping live-file operations.

### IV-S: Common Admission And Schema Sharing

Establish the private same-read structural basis and explicit ordinary versus
full-audit admission. Remove ordinary resolver A/B scans and unconsumed global
project-history checks while preserving exact current structural/project
admission, FK enforcement and fresh writers. Own the blanket cycle-count versus
Evidence-generation check in common entry; retain its full-audit counterpart and
coordinate Evidence publication's own targeted generation check.

This is the enabling boundary for later units. Do not switch unassigned global
consumers by changing an ambiguous default. Standalone/old-schema/Setup/doctor/
restore paths remain correct. Tests cover all consumer modes, schema/history
faults, foreign bindings, transaction restart, same-transaction writes, separate
connections and read-to-writer drift. Count scan work, not just helper calls.

### IV-T: Ordinary Task And Handoff Consumers

Apply the shared basis to add/edit/state/reopen/checkpoint/handoff and bounded
list/current/next/context/show/effort consumers. Preserve complete selected rows,
Contract pointers, privacy and current owner/lane/combined-slot predicates.
Reuse validated selected values, including next-selection ownership currently
read twice. Keep inherently required selection/count work and explicit Runner
Plan action admission. Convert both config-only and combined Plan-action global
entries to their complete selected Task/Contract/owner closure; preserve fresh
authorization, pure candidate construction under the writer, and bounded
filesystem publication after closure, including committed-Task/unconfirmed-Plan
partial success. Update Runner authoring owners with this change.
Completion's ordinary state boundary belongs here;
Review/Evidence internals remain with their own units.

Acceptance covers every public leaf, idempotent/no-write responses, wrong types
and relationships, concurrent status/Contract/owner changes, rollback and
selected-set size versus unrelated-history size. Existing sharing is retained,
not rebuilt as a second mechanism. Normal Usage omission is an external output.

### IV-R: Review And Operational State Consumers

Apply admitted-read sharing to Review Packet, Finding/Receipt registration,
continuation, collection, wait and finalization reads. Preserve all required
own-history, identity arms, stored-original and effective-head relationships.
Current applicability, source leases, writer basis and unknown-effect fences
remain fresh. Reuse immutable material and already computed graph results only
inside the allowed read/mutation boundary.

Include optional Runner selection, launch-intent T1, terminal T2, cleanup and
pending-reconciliation core consumers. Replace unrelated terminal-history graph
audits with the selected/newly sealed graph and required pending inventory.
The project-wide actual pending set and its absence proof remain required:
dangling attempts or missing terminal/cleanup links must not disappear through
a join or filter. Keep the one Runner OS lock, short SQLite transactions,
physical observation between separate reads, atomic T1/T2 and cleanup proof.
An already admitted attempt must finish mandatory cleanup/audit after ownership
changes; a fresh owner requirement must not strand it. Update Runner execution
owners and verify these boundaries without adding launch or host authority.

Operational DBs are bounded independent stores, not core-wide audits. Remove
Preparation's blanket FK/full-history repetition only with exact selected
preparation/root/pointer checks. Eliminate repeated decoding of identical
request rounds/finalization views in one snapshot. Preserve their small fixed
schema checks, bounded history acceptance and write-time forward-transition
validation. Do not erase lifecycle or failed/unknown records to reduce work.

Acceptance includes collection/continuation/registration/completion, stale and
foreign originals, retained/resolved Findings, source leases, duplicate/replay,
missing or malformed operational roots, and fresh effect checks after a host/Git
boundary. Consume the collected-Finding correctness Task's accepted writer rules.

### IV-U: Numerical Store Admission Sharing

Apply coherent schema/binding admission to numerical repositories and immediate
consumers. Share identical schema/history/binding facts within one actual read;
keep initialization/migration physical/FK audits, row/format validation and
separate publication guards. Do not change numerical attribution or replay its
whole core basis here: changed-target reconciliation remains the existing Usage
Task. Do not add numerical availability as a Task completion prerequisite.

Acceptance covers all repository subclasses, absent/old/current stores, schema
and binding faults, missing optional values, transaction/connection replacement,
local mutation, deadline continuation and standalone consumers. Measure metadata
reads separately from the external reconciliation Task's algorithmic work.

### IV-V: Viewer Targeted Guards

Replace `viewer_metadata_repository._basis` global quick/FK scans with selected
control and data checks. Admission of changed Task projections must explicitly
validate values that were previously assumed valid because of quick_check;
do not filter malformed statuses/severities out before validating them.
Preserve opt-in, exact generation/cursor/binding, fresh publication guards,
display-cache digest/format validation, rebuild recovery and last-good behavior.

Acceptance includes unchanged/delta/rebuild, invalid selected values, unrelated
history growth, cache corruption, generation changes, concurrent updates,
publication interruption and no writes on inspection. Normal delta work must
not quietly fall back to full semantic history merely to establish eligibility.

### IV-L: Lifecycle Audit Composition

Reconcile Setup, doctor, initialization, supported migration, restore and backup
acceptance against the new placement. Keep final predecessor-data/row identity/
history preservation and required post-normalization checks. Share only identical
results for the same state and guarantee. Historical migration bodies are not
rewritten wholesale; caller composition or current reentry changes must preserve
every supported transition and its rollback behavior.

Retain copy-side full checks, content/rule/scope-bound backup reuse and fresh
physical/selection/publication guards. Do not add source full scans to routine
backup, mutate old backups or write from doctor. Test current/old sources,
corruption classes, marker-triggered mutations, interruption and restoration.
No timer, interval or policy change is an accepted performance fix.

### IV-X: Cross-Consumer Closure And Old-Path Retirement

After the preceding units and the existing Evidence/Usage/correctness inputs are
complete, reconcile the inventory with actual callsites and reverse imports.
Delete obsolete helpers, fallbacks and misleading instructions only after no
supported consumer needs them. A retained old-schema or audit helper is explicitly
classified, not counted as a forgotten routine check.

Run mixed-consumer integration, independent lifecycle/restore checks and fixed-
target scale comparisons. Confirm no ordinary A/B full scans, no unrelated
semantic-history validation, no stale reusable result and no missing current
gate. Resolve unassigned residuals before completion; a residual that changes
accepted scope is reported rather than silently included. This final gate does
not substitute for any unit's own tests or reviews.

## Verification And Failure Matrix

| Case | Required result |
|---|---|
| More unrelated Tasks/history, same selected closure | No growth in additional inspected rows/VM work from unrelated validation; SQL and elapsed time reported separately |
| Larger selected closure or requested list | Correct complete result; necessary work distinguished from extra audit |
| Malformed selected row/reference or foreign identity arm | Reject before output/mutation; never hidden by a prefilter |
| Old sealed bytes after target/Contract/reopen/supersession | Content may match; current eligibility still rejects |
| Same snapshot, duplicate read | Reuse admitted value without extra full scan/parse |
| Local write, commit/rollback then BEGIN, connection/DDL/restore change | Invalidate affected facts; establish the new basis |
| Preflight then competing writer/host/Git effect | Fresh locked/effect guard detects drift; no stale authorization |
| Normal journal recovery, cancellation or busy | Existing bounded outcome; no automatic full-audit loop |
| Explicit diagnosis/acceptance/copy | Required whole-scope faults still detected; doctor writes nothing |
| Protection request without published valid artifact | Not protected; no checker cutover |
| Protected core with independent or absent Usage | Explicit coverage/time distinction; missing anchors unknown/unbound |
| Mid-cutover failure | Coherent previous or converted consumer retained; no global bypass |

Use disposable DBs and projects for malformed/corrupt, concurrency and restore
tests. Do not inject faults into live state. SQL count alone is insufficient:
one query can still scan every Task, and repeated hashing may dominate without
SQL. Measure inspected rows or VM work, hashes/bytes, connection/transaction
counts and inclusive elapsed time without summing nested spans. Use existing
logging; do not add a diagnostic platform to this initiative.

Every unit runs focused behavior tests, applicable schema/migration/read-only/
JSON/error tests, document-contract and packaging checks. Tier 2 requires two
independent reviews of the exact target and resolution of blocking Findings.
The design Task verifies routing, this matrix and inventory-to-unit coverage;
it does not claim product regressions or live protection have already passed.
