# Review And Completion Implementation Design

This document owns typed completion, review target/receipt/finding gates,
Review Packets, completion-cycle capture and reopen, and manual Verification
Receipt integration delegated by the
[implementation design](design.md#completion-evidence-and-review).
Product behavior belongs in the
[Review and completion specification](review-completion-specification.md).
[Evidence construction and formats](evidence-design.md) and
[ordinary Task state, Contracts, and Checkpoints](task-operation-design.md)
retain their separate owners. Shared [runtime ownership](design.md#runtime-module-boundaries),
[CLI/serialization](design.md#public-cli-and-serialization),
[connection/transaction rules](design.md#journal-and-connection-rules),
[migration](design.md#migration-sequence),
[current persistence](design.md#schema22-reservation-cleanup-design),
[the schema-v21/v22 Runner gate protocol](design.md#schema21-runner-gate-basis-design),
[post-commit coordination](setup-state-design.md#post-commit-coordinator),
[privacy/failure rules](design.md#privacy-safety-and-failure-boundaries), and
[global test design](design.md#validation-and-test-design) remain with the owners
routed by the [authority index](authority.md).

<a id="typed-completion-evidence"></a>

## Typed Completion Evidence

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

<a id="review-target-and-git-snapshot"></a>

## Review Target And Git Snapshot

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

<a id="receipts-findings-and-gate"></a>

## Receipts, Findings, And Gate

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

<a id="review-packet"></a>

## Review Packet

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

<a id="completion-cycle-history"></a>

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

Within `tasks.py`, `_seal_native_completion_locked` accepts the caller-owned
connection, project and Task identity, proposed-done Task, completion time,
and already-selected Runner basis. It seals the current gates, References,
Bundle and cycle, returning the cycle ID without opening or committing a
transaction. `edit_task` retains the outer savepoint, ordering checks, Task
update, lane recheck, event and rollback; `completion_workflow` retains
preflight orchestration outside the writer.

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

<a id="current-schema-v22-manual-receipt-arm-and-bundle-integration"></a>

<a id="schema-v21-manual-receipt-arm-and-bundle-integration"></a>

## Current Schema-v22 Manual Receipt Arm And Bundle Integration

This section defines the manual Verification Receipt arm and its integration
with the sole three-branch selector under
[Schema-v21 Runner Gate-Basis Design](design.md#schema21-runner-gate-basis-design),
retained by [current schema v22](design.md#schema22-reservation-cleanup-design). It
does not rewrite the immutable v0.10.0 artifact or claim a later published
artifact identity. Schema, parser, completion, Viewer compatibility, Skill
guidance, package inventory, and tests form one supported boundary.

### Ownership And Data Model

`verification_receipts.py` owns Receipt input validation,
exact-current classification, manual-Receipt-arm evaluation, and the bounded public read
model. The single service selector combines that arm with the closed Runner
basis; callers do not choose a branch. `verification_receipt_repository.py`
owns stored-row validation and Receipt snapshot/append queries on the caller's
connection. `storage.py` retains schema, migration, full database admission,
and the schema-enforced Receipt uniqueness boundary. `tasks.py` and
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
branch and all other marker-`2` states use the [closed selector matrix](design.md#closed-gate-tags-and-parent-guards). The
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
subject tables and columns described in
[the Evidence foundation](evidence-design.md#schema-v18-capture-and-subject-foundation), preserves exact v17 projections,
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
