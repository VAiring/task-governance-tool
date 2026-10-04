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
[migration](database-design.md#migration-sequence),
[current persistence](database-design.md#schema22-reservation-cleanup-design),
[the schema-v21/v22 Runner gate protocol](database-design.md#schema21-runner-gate-basis-design),
[post-commit coordination](setup-state-design.md#post-commit-coordinator),
[privacy/failure rules](design.md#privacy-safety-and-failure-boundaries), and
[global test design](design.md#validation-and-test-design) remain with the owners
routed by the [authority index](authority.md).

<a id="typed-completion-evidence"></a>

## Typed Completion Evidence

Documentation status: **削除予定（計測後判断・実装維持）** for the legacy
`task edit --completion-commit-hash` input. The implementation still accepts
its Git-only hash and synchronizes the compatibility projection. Skill guidance
instead uses typed `--completion-evidence-kind git_commit --completion-revision`
with `task complete` for completion. Only usage guidance is removed now;
execution measurement and any later implementation removal require separate
decisions. This note is not a runtime flag, a removal authorization, or a change
to stored evidence, help, errors, or gates.

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

Caller-side success-only composition of Git staging and target capture, or Git
commit/full-ID retrieval and completion, uses the existing commands unchanged.
It introduces no runtime wrapper or transaction spanning Git and SQLite. A
saved target or successful commit is retained on downstream failure; uncertain
responses are reconciled through existing public state before tail-only retry.

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

Normal Task detail obtains current Receipt summaries and every operational
Finding through a show-only collector in the existing fully validated inventory
stream. It neither filters the recent-ten projection nor repeats validation
queries. Existing readers and gate consumers retain their default bounded
projection; the final normal show projection removes duplicated target/tier
fields and emits each operational Finding once. Audit retains the prior shape.

### Structured Finding Resolution

`finding_resolutions.py` validates the closed grouped stdin shape and expands
only explicit ID/reason groups into existing normalized scalar inputs. It owns
no SQL, selection, judgment, target capture, or automatic retry.
`cli.py` rejects incompatible input modes before state resolution, consumes
bounded binary stdin only after read-only rejection, and retains its initialized
connection, outer transaction, sanitized failure, and post-commit maintenance.
`cli_text.py` formats the flattened results using existing single-Finding text.

`reviews.py::resolve_review_findings` validates the flat inputs, locks and
rereads the declared Task using the existing writer boundary, and checks each
Finding's same-project/same-Task ownership on that writer. It delegates each
resolution to unchanged `resolve_review_finding`, retaining stored-ledger checks,
done/replay rejection, original content, timestamps, events, and revalidation.
An outer batch savepoint rolls back a successful prefix on any failure, even
when a repository caller catches it. No per-item commit or partial-success
result is emitted. Existing single resolution callers and global admission
remain unchanged. The service deliberately does not use current-capture or
Receipt-creation freshness checks: historical Finding resolution remains valid.
No schema, new durable batch record, alternative gate, or new normal-loop step
is introduced.

### Structured Result Registration

`review_results.py` owns the closed version-1 JSON decoder, exact type and
capacity checks, input-only approval-key matching, and batch orchestration.
It reuses Receipt/provenance/Finding normalization and the existing individual
writers; it owns no SQL, schema, batch ledger, or replacement gate.

Result-input diagnostics are a dedicated `ReviewEvidenceError` subtype emitted
from those existing checks. A closed set of schema paths and fixed reasons
supplies its field/message; no general path parser or original-position ledger
is introduced. `[]` deliberately leaves document/element position unspecified.
Element checks name an array element; legacy multi-field Receipt errors name
the Receipt group instead of treating their field label as a unique cause.
The provenance validator can attach field/reason metadata while preserving its
legacy exception code/message/field; only result normalization consumes that
metadata. The result CLI and handoff helper expose the dedicated diagnostic,
not arbitrary exception fields or contents. Shape/privacy/semantic ordering,
accepted values, normalization and transaction boundaries are unchanged.

The CLI reads bounded binary stdin and decodes before opening the initialized
connection. The decoder accepts the existing document or an array of 1–8
complete documents within the same whole-input byte cap. It validates each
document before comparing exact identity/target values, concatenates only
`receipts` in input order, and validates the combined capacity. It returns the
same single-document shape to the unchanged normalizer and writer. It neither
reads paths nor interprets reviewer messages or merges judgments.
All entries are normalized before acquiring the writer. The
service then uses the existing writer-lock/revalidation boundary and compares
the submitted Task, Contract revision and complete target against the locked
Task before any insert. A concurrent tier or authority change cannot reuse
pre-lock normalization. Each Receipt and its Findings use the same connection,
including their individual References, provenance and events.

After the inserts, the service captures `read_review_evidence` on that same
connection and validated locked Task, then calls `first_review_gate_error`.
It does not reproduce predicates, restrict blockers to the recent window,
enforce this observation as a write gate, or perform Git/external I/O. The
response selects compact Receipt/Finding fields from the actual saved results,
keeps their input order and complete new Finding bodies, and carries the locked
Task/Contract/target basis once in `review_gate`. Explicit omission metadata
distinguishes presentation from the unchanged storage/audit projections.

The CLI owns the one outer transaction: insert or gate-read exceptions escape
it before conversion to sanitized failure output. There are no per-entry commits or
successful-prefix returns. Commit and connection close precede formatting and
one changed/Viewer-relevant maintenance result. Legacy single-item callers and
global state admission retain their existing contracts. Replay detection uses
the existing Task/generation/reviewer uniqueness, without a new durable ID.
For this command only, a post-commit text rendering exception retains success
data with a fixed display warning. Successful emission includes stdout flush
inside the same guard. An emission/flush exception returns a fixed
saved-outcome stderr diagnostic and exit 2 without another stdout attempt or
replay. The failed stdout descriptor is redirected to the OS null sink where
available to prevent a second write during interpreter finalization; custom
embedded streams without a descriptor retain caller-owned lifecycle. Neither
path reports rollback of already committed rows. Unavailable
output still requires existing public-state recovery, not a new receipt ledger.

`scripts/review_handoff.py` delegates to `review_handoff.py` for caller-owned
original-file transport. It has no storage connection: save reuses the pure
result decoder/normalizer before exclusive creation and after physical readback;
submit preserves raw bytes, adds only array framing, and sends them once to
the sibling public CLI through binary stdin. No LLM-built collector, alternate
writer or producer-specific adapter is required. The registration transaction
and current-state revalidation stay here and are not replaced by saved-file checks.
This module also owns the pure complete-Packet validator shared by prepare,
read, save and submit. It retains the existing stored-constraints compatibility,
without repairing incomplete input or introducing another Packet contract.

`review_handoff_preparation.py` owns the same helper's explicit `prepare`
operation: three fixed sibling CLI invocations, bounded binary response capture,
exit/envelope and Packet consistency checks, creation of the named unused
ignored directory chain and complete Packet, and exact save/submit instructions.
It reuses transport path/read/write checks and the existing structured Receipt
input decoder; it owns no SQL, authority inference or arbitrary-command runner.
Source mutation outcome, sanitized warnings, and transport outcome are separate.
After target dispatch, a valid failure envelope is still persistence-unknown
unless it is the CLI's pre-dispatch `parse` rejection. The helper retains the
source exit/error and adds only a fixed state-inspection warning; it does not
deduce T1 or cleanup commits from error codes or change Runner transactions.
Receipt rejection and read-only recovery keep their existing classification.
Non-ready, malformed or uncertain responses produce no reviewer requests;
post-write failure retains residue. Capture starts before the source call and
does not add a normal query, log file, retry or reviewer-launch operation.
For every missing directory, `os.name == "nt"` selects default `Path.mkdir()`
to inherit the parent ACL; POSIX retains `mode=0o700`. No existing permissions
are rewritten, and no SID policy, native ACL adapter or caller option is added.

The same module owns read-only saved-Packet display: existing transport reads
and complete preparation validation precede `review_packet.py`'s pure
independent-role projection. The helper parser requires explicit `--role
independent`; neither tier nor slots select a role. Generated ordinary review
requests supply that read command instead of raw-file reading and contain the
ordinary procedure themselves. `_review_material` reuses `artifact_manifest.py`
observers and entry builder, without another fingerprint algorithm, to return
the complete immutable-object delta and dependency/read command templates.
The same module streams the immutable dependency revision through the existing
`git_snapshot.stream_tree_entries`, excluding delta paths and retaining only a
count/byte-bounded inventory prefix while counting omitted entries. It does not
infer relevance, scan the worktree or retain blob content. The additional
directory-list and `cat-file --batch` templates reuse `_material_command`:
it now emits short calls to the existing helper's closed `material` operation.
Discovery and selected multi-blob reads keep the existing safe-Git environment.
Batch output keeps Git's
per-object framing and missing/non-blob outcomes, not a new success assertion.
Generated guidance preserves path/side identity, bounded-display recovery,
snapshot overlay and later dependency discovery. Inventory and bodies never
enter the saved Packet or DB; save/submit and reviewer isolation are unchanged.
The same module's `read_material` owns only five fixed reads: blob, batch,
diff, dependency and directory. It reuses `completion.FULL_GIT_OBJECT_ID`,
manifest path validation and `completion.safe_git_command/safe_git_environment`;
no configurable runner or stored wrapper is introduced. Named `--path=` keeps
an empty root argument intact on PowerShell 5.1. Batch validates each bounded
ID line before forwarding it to `cat-file --batch`, streams output directly,
and closes/reaps the child, terminating it on invalid input or interruption.
It retains no bodies or ID list and adds no batch-count cap. A stream failure
returns nonzero with a sanitized diagnostic, never a JSON success envelope
appended to Git bytes; a previously delivered prefix is not complete. Individual
reads forward the fixed Git process's raw output and exit. No Task query,
Packet mutation, storage connection, body persistence or permission change is
added to material retrieval. The already generated immutable selectors and
existing read/submit checks remain the binding boundary.
The child inherits no Git overrides and cannot lazily fetch missing material.
Generated host-specific literal-substitution instructions and fully bound shell
arguments preserve quotes and shell punctuation, including PowerShell's five
single-quote delimiter characters, without changing the parent's environment.
One fixed public `review prepare` capture then compares saved Task/Contract,
target and path metadata and rechecks original Packet bytes before display.
That read has no direct storage access or mutation; no reviewer check/show,
new file or role classifier is added. Opaque targets explicitly require supplied
material/binding, not inferred Git content. Existing observation limits and
sanitized failure codes apply without partial material. `review_results.py` renders the applicable independent
format branch from the existing vocabulary owners; it does not filter prose
with patterns or change the decoder/normalizer. Default full instructions and
Packet remain unchanged for existing consumers and alternative review paths.

The transport module owns bounded physical reads, explicit ignored-path checks,
exclusive creation and retained failed-write residue. Standard-library file
operations and the existing safe Git environment suffice; no native adapter or
state-path resolver mode is introduced. Identity, type, links, size and mtime
are compared across lstat/fstat; ctime is compared only within the same API
because Windows reports different ctime meanings through those APIs. The
complete Packet's identity/template consistency and tier supply context for
existing result validation, not a second live Task admission decision.
`tests/test_review_handoff.py` exercises original-file transport;
`test_review_handoff_preparation.py` starts before actual installed source CLI
invocation and exercises ready/non-ready and injected capture/file failures.
Both cover portable filesystem behavior; `test_review_handoff_windows.py`
owns the native PowerShell transport case outside POSIX platform selection. Existing
result registration tests retain writer atomicity and concurrent-state checks.

### Host-Owned Review Waiting

Review waiting is caller guidance over the exposed host API, not a Python
runtime responsibility. The host owns the blocking timeout, mailbox delivery
and early return. The parent interprets actual outcomes using its existing
dispatch handles and Task/Contract/target context. No adapter, scheduler,
persisted deadline, notification ledger, new user-entered ID or helper operation
is introduced. `review_handoff.py` and `review_handoff_preparation.py` retain
their original transport responsibilities; core registration, ownership and
gates retain their current writers and validation.

The [waiting behavior](review-completion-specification.md#host-owned-review-waiting)
is carried in the workflow's common Review And Completion introduction, so
shared-file and direct transport receive it without another reference read.
Existing reference-retrieval and package checks verify delivery; semantic
scenario review checks completion, messages, timeout, interruption and host
limits. There is no new timer state machine to test or claim as implemented.

<a id="review-packet"></a>

<a id="conditional-core-reviewer-binding-structure"></a>

### Core Reviewer Binding Structure

The [schema-25 contract](review-completion-specification.md#conditional-reviewer-session-binding)
is active after explicit setup migration. `review_session_transport.py`
owns pure exact-byte/digest and closed-envelope validation, with no filesystem,
environment or database access. Handoff save obtains the existing typed caller
identity once; submit retains it instead of capturing the submitting parent's
identity. Packet creation includes the execution in its existing stability
comparison. No new LLM-entered fields or template judgments are added.

`review_session_repository.py` owns the immutable core relation and its exact
Receipt/Reference/execution joins. `reviews.py` and `review_results.py` use a
typed bound-reviewer argument only for Receipt/Finding registration; all other
calls retain the ordinary owner guard. Preflight observations are rechecked
under the existing short writer. Alias detection and insertion serialize in
that writer; global and selected Receipt validation consume the same core
relation, never numerical state. Source execution belongs to the Receipt's
project/Task; historical bindings remain valid after ownership/target advance.

Migration and its consumer integration are owned by the
[database design](database-design.md#conditional-reviewer-binding-migration).
The focused repository, transport, migration and installed-entrypoint tests
cover rollback, concurrent aliases, parent forwarding, direct reviewers,
stale/malformed bindings, old unbound input and numerical-store isolation.

`usage_review_attribution.py` projects the existing successful read display,
bound-save `review_session` metadata and direct registration acknowledgement
into numerical boundaries. It neither changes core permissions nor reads the
review judgment. The numerical repository revalidates committed core bindings
and the exact Task/Contract/target/execution before using those boundaries.
An unresolved boundary remains partial numerical coverage; it is never a
reason to resubmit a Receipt or downgrade its core binding.

After successful bound original save, `usage_lifecycle.register_reviewer`
best-effort records only the actual caller in the existing numerical registry.
It admits the same project through the resolver, never migrates or reads logs,
and suppresses numerical failure without changing the save acknowledgement.
Read/material remain read-only. Later core bindings can recover registration.

## Review Packet

`review_packet_binding.py` owns only a pure, transient SHA-256 binding of
validated public Task values plus immutable Contract revision. It uses sorted,
compact ASCII JSON with the `taskgov-review-packet-binding-v1` NUL-terminated
domain prefix. `reviews.py` computes it from the existing locked post-write
row; the Runner service carries it unchanged to the CLI. No new DB read,
schema, persisted token, or admission rule is added to capture it.

After target/Runner processing, the CLI invokes the existing Packet handler
only for `not_required`/`runner_pass`, with a fresh connection and that binding.
`review_packet.py` compares it against the full validated Task and Contract
revision in both existing reads, including before initial Git observation.
The optional `--expected-binding` retry uses precisely the same checks.
`task_show_projection.py` derives the same value only in the explicit audit
projection for lost-response investigation. It is not gate evidence.
Post-save preparation failures remain nested partial success, with the
original mutation outcome and one outer maintenance pass. Normal context,
manual Receipt binding, Packet limits, and final gates remain unchanged.

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
output, result template/instructions, and the batch-registration argv shape are
allow-listed. `review_results.py` owns pure rendering helpers beside its existing
closed decoder: the template copies only validated Packet identity and leaves
all Receipt/provenance claims and collections null. Provenance field names,
enums and identifier grammars come from the existing definitions; explanatory
combination rules do not implement another validator. `review_packet.py` adds
these projections only after its existing revalidation and renders the same
template as compact ASCII JSON in text output. The unchanged CLI Packet-size
boundary includes both fields. No DB/Git read or write is added. The builder
does not launch a reviewer, execute/import a receipt, store a packet, or
include a diff, transcript, prompt, stdout/stderr, secret, or absolute path.

The Contract projection includes `authority_ref` from the already-read and
validated current Contract, unchanged and without resolving it. Text emits
`Authority reference` after constraints; independent JSON retains the same
field. The shared pure handoff validator accepts only the existing Contract
shape or that shape plus a privacy-checked, bounded string `authority_ref`.
For saved old Packets, reviewer read compares the existing fields against the
current projection without its new reference field; it never inserts a value
into the saved Packet or display. A supplied reference is compared exactly.
Contract revision and full target revalidation, result templates, original
bytes, output limits and registration checks remain unchanged.

Its separate pure independent display preserves all Packet material/template
and required-output fields, omits the parent receipt command and selects only
the applicable result explanation. It never mutates the original Packet or
fills claims. Helper tests exercise complete-before-display validation, original
byte and Finding conservation, stale registration rejection and read/save
failure paths; retrieval tests check the reviewer route without importing the
parent operation subtree. Generated request prose covers reviewer judgment and
result return; the paired read output covers exact material retrieval and its
exceptions. Their shared explanations can be consolidated without changing
selectors, command templates, JSON shape or review/save/registration checks.
Local bytes/call observations are not token savings.

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

Documentation status: **削除予定（計測後判断・実装維持）** for
`task edit --status done`. It remains accepted, but Skill usage guidance routes
completion to `task complete` with typed evidence. It is not an identical alias:
the edit path retains its permitted combined-edit behavior and response/event
contract, while the thin command accepts no non-completion edit. Shared validators
and native capture remain intact. Runtime messages that still direct callers to
`task edit --status done` (including verification/review confirmation and missing
evidence errors in `tasks.py`) remain unchanged and are a pending cleanup item.
Measurement and implementation deletion are later separate decisions; this
documentation marker authorizes neither and adds no completion prerequisite.

Both `task complete` and compatibility `task edit --status done` perform Git
preflight outside SQLite and converge on one locked native capture:

Within `tasks.py`, `_seal_native_completion_locked` accepts the caller-owned
connection, project and Task identity, proposed-done Task, completion time,
and already-selected Runner basis. It seals the current gates, References,
Bundle and cycle, returning the cycle ID without opening or committing a
transaction. `edit_task` retains the outer savepoint, ordering checks, Task
update, lane recheck, event and rollback; `completion_workflow` retains
preflight orchestration outside the writer.

`task_ownership.py` supplies the captured ownership basis and same-writer
generation check. The completion basis carries it separately from quality
evidence. Both completion paths require the owner/completion owner; `--check`
uses the same rule without acquiring a slot. Runner target admission checks
ownership before T1, but committed execution cleanup and terminal audit never
depend on later caller ownership. Packet preparation bindings omit the
caller-relative ownership projection; ownership freshness is checked separately.

1. validate schema v25, identity/binding, optimistic ownership/Task/authority/target
   capture basis, Contract, sequential ordering, and evidence;
2. reread Verification Receipts and review receipts/findings, evaluate the
   current verification and review gates, and select their deterministic
   bases;
3. choose canonical completion time and next ordinal;
4. insert links, Finding snapshots, one immutable Bundle-v2 row carrying the
   selected caller-attestation, not-required, or Runner-observation basis, and
   one complete verification/subject/evidence-basis-v1 `native_done` cycle;
5. link the current execution to that cycle, release ownership and update the
   current Task to done with identical evidence;
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
cycle already exists. It acquires a new execution and checks the session's active
slot in the same writer; failure rolls back the reopen as a whole. A later done
uses fresh gates and the next ordinal.

### Public History Projection

`completion_history_repository.py` owns latest-cycle, single-Task and batch
history retrieval and metadata queries. It uses the caller's connection and
the existing storage-owned cycle/Receipt checks and shared Evidence validation
repository. The latter owns Bundle validation/acquisition; shared types and
completion writes remain in storage.
The single-Task reader retains its selected Bundle-history validation, while
the Viewer batch reader retains its upstream global-snapshot validation
assumption. These paths do not acquire a connection or start a transaction.

`task show` reads total, incomplete-legacy aggregate, and newest-first rows in
the same snapshot as all other Task data, regardless of display mode. Normal
presentation keeps only total and legacy incompleteness after validation;
`--audit` exposes the previous exact wrapper:

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
privacy matcher before building a public cycle. It does not use the legacy
`dispatch_authorization` counter projection for stored Contract constraints
and checkpoint summaries. A rejection maps to the fixed
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
[Schema-v21 Runner Gate-Basis Design](database-design.md#schema21-runner-gate-basis-design),
retained by [current schema v22](database-design.md#schema22-reservation-cleanup-design). It
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
`cli.py` owns stdin transport/dispatch/formatting for the one Receipt write leaf;
`cli_parser.py` owns its two mutually exclusive input modes.
`verification_results.py` decodes the bounded fixed JSON declaration into the
existing four Receipt inputs and verifies its Task ID against the command.

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
Target setting remains before either verification branch. The current
[normal-loop call budget](review-completion-specification.md#receipt-meaning-and-record)
is unchanged by the Receipt input mode.

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
source revision, Contract revision, timestamp, Receipt ID, exit code, raw output,
exception, or arbitrary result document. The optional fixed stdin declaration
is defined by the [structured result owner](review-completion-specification.md#structured-verification-result).
The CLI rejects mixed/incomplete modes before state resolution, then rejects
read-only before reading at most the byte limit plus one from binary stdin.
The decoder rejects duplicate keys, non-integer numbers and non-UTF-8 input,
validates the closed shape and all typed strings' privacy, and reuses existing
Task-ID and Receipt-field normalizers. A document Task mismatch is stale evidence.
No writer is opened until decoding succeeds; the returned four values enter
the unchanged `add_verification_receipt` service. This keeps generation/Contract
invalidation, global admission, physical Runner selection outside the writer,
locked revalidation, persistence, rollback and post-commit policy identical.
No producer adapter, result ledger or coverage inference is introduced.
A failed or timeout row records
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
bind the row to new material. CLI formatting retains the three-line Receipt
prefix and appends preparation status and Packet/errors; no synthetic Task
event is added.

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
empty expectation + explicit nonempty not-required reason
  => receipt gate not required; existing attestation remains required
empty expectation + no not-required reason
  => verification_requirement_unspecified; required=true, satisfied=false
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
after trimming requires an explicit nonempty not-required reason, then inserts
version 1 with its exact-byte digest and a null Receipt link; the exact empty
string uses the empty-text digest. Schema 23 stores the reason in Task/Packet
and immutable cycle, not in the Bundle or authority snapshot. The pure gate
returns `verification_requirement_unspecified` for an undeclared live Task;
the completion-check allow-list and actual writer share that rejection.
Historical done reads retain legacy NULL declarations without weakening a new
completion; non-NULL cycle reasons must match the Task. Any ownership,
target, digest, link, or gate drift rolls everything back. Existing
`verification_attestation=true` remains in the cycle, so the Receipt
strengthens rather than silently replaces the current explicit assertion.
Version-0/null/null marks only migrated cycles and the sole exact compatibility
bridge. A version-1 nonempty cycle whose stored tagged arm has neither its valid
linked Receipt nor its valid qualifying Runner observation is
`completion_history_inconsistent`, never an inferred legacy success.

The one Receipt command is `verification receipt add`. Its CLI handler commits
the existing Receipt transaction before invoking the existing Packet handler
for pass/full, with a fresh read connection and the recorded Receipt ID.
Success data is `receipt` plus `review_preparation`; registration errors remain
failures, while post-commit preparation errors are explicit partial success.
The original mutation outcome and one outer maintenance pass are preserved.
`verification_receipts.py` owns exact-current Receipt-ID binding using its
existing snapshot validator. `review_packet.py` applies it in both existing
reads, including readonly `--verification-receipt-id` retries. No generic
workflow engine, new ledger, global admission change, or completion selector
is introduced. Packet-only retries cannot write or change the recorded basis.
`task show` obtains Receipt totals, exact-current
counts, gate, and newest 10 rows in the same query-only transaction as the
Task. A show-only collector retains the validated exact-current row for normal
presentation; filtering recent rows cannot establish that exact-current row.
The pure normal projection removes repeated expectation, Contract revision and
target, retaining the subject, gate, current counts, and concise current result.
`--audit` preserves the previous detailed `verification_evidence` object. Both
use the same validation and public allow-lists; no separate Receipt command,
pagination, storage mutation, or gate branch is added.

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
Empty marker-`0` expectation is satisfied only for an explicit waiver or
validated prior done history. An unspecified active Task is required and
unsatisfied with `verification_requirement_unspecified`; reopening old blank
history does not supply a waiver. A nonempty
active Task first reports target-required or stale basis; its selected manual
arm then reports Receipt-required, Receipt-blocking, or satisfied, while its
qualifying Runner arm reports satisfied with a null Receipt ID. A done Task
backed by a matching version-0 cycle reports the
explicit legacy exemption even when its target is absent, including the sole
bridge-created cycle. A version-1 done cycle is validated against its stored
manual or Runner branch before projection. Task-show failure data includes a null
`verification_evidence`; audit text Task show remains unchanged. The Receipt write
retains its three-line prefix, followed by preparation status and Packet or
sanitized errors under the public connection contract.

The audit completion-history JSON remains unchanged. The current done Task
retains its target and Receipt, so normal `task show.verification_evidence` exposes
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
