# Backup Validation Reuse Execution Plan

## Authority, Scope, And Retirement

The user approved persistence and execution of these three sequential stages
on 2026-10-28 for the existing Task `tg_task_4619f965dec4d31e`. They are internal
stages of one completion responsibility, not additional Tasks or optional work.
Live status and evidence remain in the public CLI.

This conditional owner is routed by [authority](authority.md). Durable behavior
belongs to [Setup/state](setup-state-specification.md), structure to its
[design](setup-state-design.md), and database admission to the
[database specification](database-specification.md). Synchronize those owners
with the reviewed implementation before adoption. This plan does not itself
activate runtime behavior.

The plan is eligible for retirement when this Task is done, durable contracts
have current owners, and no live Task depends on its anchors. Physical retirement
still requires the separately authorized authority transition in AGENTS.md.

## Accepted Outcome And Boundaries

Healthy-primary Setup must not deeply inspect or hash unrelated old backups.
When an immutable backup is actually needed, exact content identity and a valid
bounded inspection record may replace only the internal checks that record
certifies. Physical safety, sidecars, current binding, inventory relationships,
selection and publication continuity remain fresh checks.

Records live outside the database being hashed and remain usable after primary
loss. They bind project, generation, source schema, inspection rules/version,
mode/scope, classification and final closed-file content. Structural-only success
cannot certify full recovery content. Missing or obsolete records require actual
inspection when needed; corrupt records, mismatching bytes or identities are
not silently re-enrolled as known-good. Old backups are not rewritten in bulk.

The existing migration-protection copy, asynchronous launch/interval/exclusion,
atomic publication, retention authority and last-good preservation remain.
Records are derived validation results, never generation ownership, restoration
authority or Task/review/completion evidence. No new service, timer, ordinary LLM
command, unbounded history, live Setup/restore, configuration change or live-data
fault injection is authorized. Preserve other contributors' working changes.

## Sequential Stages

### 1. Inspection Record And Publication Contract

Define the closed bounded record, validation-rule invalidation and the safe
file/record/generation publication sequence. Reuse fixed path resolution and
physical-file primitives. Establish a successful full inspection of the final
consistent copy; never assume source and SQLite copy bytes are identical or
embed a file's own digest inside itself. Define missing, stale, malformed,
orphaned and interrupted-publication behavior and bounded retention.

Expected areas: Setup/state owners, a focused record/inspection module, shared
backup primitives and their isolated codec, physical-safety and crash tests.
Gate: records cannot turn a failed/partial inspection into success, confer
ownership alone, or certify changed bytes, another project or generation.

### 2. Necessary Inspection And Reuse Integration

Separate healthy-primary Setup from recovery inventory inspection. Apply the
same content-proof boundary to synchronous migration protection, asynchronous
capture, discovery, reconciliation, retention and recovery. Reuse one result
within a coherent inspection and avoid repeated whole-byte reads without
dropping fresh checks required at distinct effect boundaries. A restore's
copied, normalized or migrated output receives its own required inspection.
Same-byte replacement after planning remains continuity drift.

Expected areas: setup, resolver, backup and asynchronous capture/repository,
directly coupled database contracts, CLI/Skill guidance and fixtures. Review
every analogous call path, including preview, retry, legacy recovery and explicit
diagnosis. Explicit global diagnostics keep their complete-audit responsibility.
Gate: healthy-primary Setup performs no unnecessary old-backup content/hash
work; needed old/obsolete records fall back narrowly, while valid records avoid
the certified internal audit without weakening current relationships.

### 3. Qualification And Completion

Run isolated corruption, replacement, sidecar, binding/inventory drift,
publication interruption, concurrency, old-schema and missing-primary recovery
regressions. Verify read-only/preview writes nothing, originals and the last
normal generation survive failure, and orphan records cannot restore or delete.
Compare representative Task/Evidence histories and retained-generation counts
before/after: deep audits, SQL, hashed bytes, read/write lock time and elapsed
time. Separate logging ON/OFF and inclusive timings; do not sum nested durations.
Assert work boundaries rather than a private helper's exact call sequence.

Run related document/distribution checks and relevant Setup, backup, resolver,
recovery and maintenance regressions. Two independent Astra-high reviews must
cover the exact final Tier 2 target. Repair blocking Findings and check analogous
paths, then verify/review corrected material. Commit only this coherent Task's
reviewed changes and complete through the authorized automatic route.

These stages are sequential, none optional. Their intermediate checks do not
complete the Task. No schema migration or live configuration application is
implied; any newly necessary live operation retains its existing permission
boundary. The separately approved RV-01 and RV-02 Tasks keep their own contracts,
reviews and commits and are not expanded by this plan.
