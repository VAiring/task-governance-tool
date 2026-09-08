# Review And Completion Specification

This document owns Review Target/Receipt/Finding operations, Git snapshot
binding, Review Packets, completion evidence and eligibility, completion-cycle
history, and Verification Receipt operations delegated by the
[product specification](specification.md#review-and-completion).
Implementation structure belongs in the
[Review and completion design](review-completion-design.md).
[Evidence formats and provenance](evidence-specification.md) and
[ordinary Task state and Checkpoints](task-operation-specification.md) retain
their separate owners. Shared [CLI/output](specification.md#public-cli-and-output-contract),
[privacy and stable errors](specification.md#privacy-safety-and-stable-errors),
[SQLite operation](specification.md#sqlite-migration-and-concurrency),
[current persistence](specification.md#current-schema-v22-persistence-contract),
[the schema-v21/v22 Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol),
and [maintenance](setup-state-specification.md#same-process-maintenance) remain with the
owners routed by the [authority index](authority.md).

<a id="review-target-receipt-and-finding-ledger"></a>

## Review Target, Receipt, And Finding Ledger

A current target is one of:

- `git_commit`: a canonical existing commit;
- `git_snapshot`: an internally captured staged snapshot;
- `diff_fingerprint`: exact `sha256:<64-lowercase-hex>`; or
- `external_revision`: a caller-approved durable external identity.

Every target set, including identical or A-to-B-to-A values, increments a
positive signed-64-bit generation. Historical receipts never reactivate.
Target setting is forbidden on done Tasks.

`review receipt add` binds a sanitized reviewer key, independent/fallback/
not-required kind, PASS or changes-requested verdict, summary, user-approval
flag when required, and timestamps to the exact current target/generation.
One reviewer cannot replace or contradict a receipt in the same generation.
`review finding add` requires a same-project/Task receipt and severity;
resolution preserves the original finding.

Tier 2 requires two PASS receipts from distinct independent reviewer keys;
Tier 1 requires one. Tier 2 fallback requires one documented self-review PASS
plus explicit user approval; Tier 1 fallback requires the self-review PASS.
Tier 0 permits not-required only with a mechanical-change rationale. Any
current-generation changes-requested receipt, unresolved high/medium finding
from any recorded generation, or fresh-review-required condition blocks
completion. Resolving a blocking finding is insufficient: advance to a newer
target and obtain fresh receipts.

Reviewer keys prove only normalized-string distinctness. The trusted caller
attests the actual returned result; taskgov neither launches/authenticates a
reviewer nor proves person/model/process identity, independence, expertise,
provenance, or summary truth.

Public receipt kinds are exactly `independent`, `self_review_fallback`, and
`not_required`; verdicts are `pass`, `changes_requested`, and `not_required`.
Detailed rows remain in `review_receipts` and `review_findings`. Completion
cycle basis selection is independent of the existing diagnostic
`fallback_kind` projection.

`task show.review_evidence` exposes the current target/generation, tier
requirement, qualifying current-generation counts, fallback state, bounded
recent receipts/findings, and blocking counts including
`changes_requested_current_generation`. It never emits raw review content.

<a id="git-snapshot-and-target-binding"></a>

## Git Snapshot And Target Binding

`review target set --kind git_snapshot` accepts no caller revision. It reads a
canonical HEAD and stage-0 index, rejecting unborn HEAD, unmerged index,
zero-object intent-to-add, and sparse-directory entries. A canonical manifest
binds base commit, mode, object ID, and raw path bytes; its SHA-256 plus base
are stored. No Git state, hook, config, worktree, object, index, or ref changes.
Unstaged/untracked content is outside the snapshot.

Every newly set target is capture version 1 and atomically binds the current
authority snapshot, nullable acceptance and verification criterion IDs, and
one immutable artifact manifest. Git targets contain the complete bounded
HEAD-tree/index or commit/first-parent leaf difference; root commits compare
with the empty tree. Exact mode/object pairs become a rename only when unique
on both sides. Other moves remain delete/add. Safe relative POSIX paths, full
object IDs, fixed bytewise ordering, 10,000 entries, and 16 MiB canonical JSON
are hard limits. Diff-fingerprint and external targets instead receive one
zero-entry `opaque_target` manifest with `artifact_content_not_observed`. No
blob, patch, raw diff, untracked content, or caller file list is retained.
On success, the same target-set service returns exactly one closed route for the
target it just stored. `verification_route` is exactly `not_required` for an
empty verification expectation, `receipt_required` for the existing marker-`0`
manual path or exact closed no-launch fallback, `runner_pass` for the exact
qualifying complete-plan Runner pass, or `blocked` for every other stored Runner
terminal. `blocking_code` is null for the first three routes and is exactly the
existing `verification_receipt_blocking` code for `blocked`. These fields expose
no Runner ID, observation, gate tuple, Receipt ID, command body, or raw result.
The text success output is unchanged.
Pre-v18 targets keep their tuple as capture version 0 with null snapshot,
criterion, and manifest bindings. They are audit-only: Verification Receipt,
Review Receipt, Review Finding, and completion source creation fails
`evidence_basis_stale` / `current evidence basis must be captured again`.
Review preparation and resolution of an existing Finding remain allowed.
Setting a fresh target is the only repair; no migration or command upgrades an
old target in place.

At `git_commit` completion, review binding succeeds when the current target is
the identical canonical commit, or when a snapshot target's completion commit
has exactly one parent equal to the stored base and its commit-tree manifest
has the identical fingerprint. Merge/wrong-parent/tree mismatch is
`review_target_mismatch`. External completion requires identical external
target; commit-not-required requires a diff fingerprint.

Git commits are resolved read-only. A unique abbreviation is accepted and
stored as a canonical full object ID; missing, ambiguous, or non-commit input
is `git_commit_not_found_or_ambiguous`. Done transition re-resolves stored Git
completion and target evidence immediately before update. Git observation is
outside the SQLite writer.

<a id="review-packet"></a>

## Review Packet

`review prepare <task-id>` is bounded read-only stdout generation for all four
target kinds. Missing target returns `review_target_missing` and
`review target is required before preparing a review packet`.

Snapshot targets are recaptured; commit targets resolve the commit and list
first-parent changes (root against empty tree); diff/external targets perform
no Git and report paths unavailable. Target-specific behavior is internal and
adds no Skill branch.

Data keys are exactly `task`, `contract`, `review_target`,
`changed_paths_available`, `changed_paths`, `changed_paths_total`,
`changed_paths_truncated`, `review_focus`, `required_output`, and
`receipt_command`. Task contains ID/title/status/verification/tier; Contract
contains revision/scope/acceptance/constraints; target contains kind/value/base/
generation. No diff, result, raw output, prompt, conversation, secret, absolute
path, or caller-authored focus is included.

At most 100 bytewise-ordered changed paths, 240 UTF-8 bytes each and 16,384
bytes total, are returned. Unsafe paths fail `review_packet_path_unsafe` with
`review packet contains an unsafe project path`; no path is hidden. Complete
text or JSON is at most 32,768 UTF-8 bytes or fails
`review_packet_too_large` with `review packet exceeds the supported size`.
Git observation uses at most 10 subprocesses.

Four common focus rows are Contract compliance; state/completion integrity;
privacy/target safety; and verification/regression. The fifth mechanically
states the exact snapshot, commit, supplied diff-fingerprint material, or
supplied external material boundary. Required output is verdict PASS or
CHANGES_REQUESTED, severity-ordered exact file/line findings, remaining risks,
and recommended changes. Receipt command is a non-executed attestation shape.

Text order is `Task`, `Status`, `Verification`, `Contract revision`, `Scope`,
`Acceptance`, `Constraints`, `Review target`, `Changed paths`, `Review focus`,
`Required output`, `Receipt command`, LF-terminated. After Git, a second short
read revalidates Task, Contract, and every target field/generation; drift fails
`review_packet_stale` with `review context changed while preparing the packet`.

<a id="completion-evidence-and-commands"></a>

## Completion Evidence And Commands

Every done transition supplies exactly one:

- `git_commit`: canonical existing target-project commit;
- `external_revision`: revision plus reason and
  `--external-revision-approved`; or
- `commit_not_required`: explicit assertion that no managed material changed,
  with no revision.

External revision is never inferred. Missing approval is
`external_revision_approval_required`. The compatibility
`--completion-commit-hash` is Git-only and cannot store a generic string.
Existing migrated legacy values remain `legacy_unverified`; they are not
retrospectively strengthened or invalidated.

Managed material is source-controlled content or a user-approved durable asset
whose final state must be traceable. Generated local state, ignored SQLite,
caches, locks, logs, and scratch files are not managed material unless current
authority explicitly says otherwise. `commit_not_required` is valid only when
no managed material changed.

Both thin `task complete` and compatibility `task edit --status done` require
verification and review confirmations, typed completion evidence, sequential
eligibility, a matching current target, the qualifying current basis selected
by the sole schema-v21/v22 selector, sufficient current-generation review, and no
blocking finding/receipt. The manual arm uses the current Verification Receipt;
the Runner-pass arm uses its qualifying observation with a null Receipt link.
They use the same transition service.

Thin completion emits `command="task.complete"` and data exactly `task`,
`changed_fields`, and `event`; text starts
`Task completed: <task-id>`. It accepts no non-completion edit.

`task complete --check` accepts the same proposed evidence and confirmations,
is read-only, invokes the exact shared fail-fast validator, and is not an
authorization token. It captures one coherent basis, closes before Git, then
revalidates in a second coherent read. Drift returns not-ready with
`completion_check_stale`.

Check output is at most 8,192 UTF-8 bytes. Data is exactly `task_id`, `ready`,
`status`, `blocking_codes`, `contract_revision`,
`review_target_generation`, `completion_evidence_kind`, and
`suggested_action`. It returns only the first code in existing validation
order. Allowed readiness codes are `invalid_status_transition`,
`sequential_predecessor_incomplete`, `verification_required`,
`review_required`, `completion_evidence_conflict`,
`external_revision_approval_required`, `commit_required`,
`git_commit_not_found_or_ambiguous`, `invalid_review_evidence`,
`review_target_required`, `evidence_basis_stale`, `review_target_mismatch`,
`verification_receipt_required`, `verification_receipt_blocking`,
`review_finding_unresolved`, `review_changes_requested`,
`review_receipts_insufficient`, and `completion_check_stale`. Parse/privacy,
not-found, project/schema/journal/busy/storage/internal failures remain command
errors.

Check text is exactly three LF-terminated lines:
`Task <task_id>: ready|not ready`,
`Blocking: none|<comma-separated codes>`, and
`Suggested action: <bounded suggested_action>`.

<a id="completion-cycle-history"></a>

## Completion Cycle History

Schema v15 adds `tasks.completion_history_coverage` (`legacy_unknown` or
`complete`, default legacy unknown), nullable internal
`task_events.completion_cycle_id`, and append-only
`task_completion_cycles`. Schema v16 is a marker-only
`completion_cycle_capture_activation` migration: it adds no object but prevents
a schema-v15 binary from writing after native capture activates. Schema v17
adds the Verification Receipt basis discriminator and link described under
[Receipt eligibility](#verification-receipt-eligibility-and-manual-completion).

Each cycle ID is `tg_completion_cycle_<16-lowercase-hex>` with a positive
signed-64-bit per-Task ordinal beginning at 1 and increasing exactly by one.
It stores ownership; origin (`native_done` or `legacy_current_done`);
completeness (`complete` or `partial`); completion/record times; Contract
revision; tier; specified/unspecified verification expectation and nullable
attestation; the exact six-field completion evidence; exact four-field review
target; and versioned accepted gate-basis counts plus up to two qualifying
review-receipt IDs. Schema v17 additionally stores internal
`verification_basis_version`, `verification_expectation_digest`, and
`verification_receipt_id` fields without changing the public cycle shape.

The migration is named `completion_cycle_history`. Internal cycle fields
include `recorded_at`, `gate_basis_version`, and `review_basis_kind`; these are
project-owned validated values and do not broaden the public allow-list.

Post-v17 native rows are complete, have non-null completion time, true verification
attestation, gate basis v1, zero changes-requested/open-high/open-medium/
fresh-review-required, verification basis v1, and a valid tier basis:

- Tier 1/2 selects enough distinct independent PASS receipts ordered by
  `reviewer_key, review_receipt_id`;
- only when insufficient, Tier 1/2 selects the first valid fallback by receipt
  ID, including Tier 2 approval;
- Tier 0 selects the first valid not-required receipt.

The stored v1 basis kind is exactly `independent_passes`,
`self_review_fallback`, or `not_required`; a v0 legacy row uses `unknown`.

Migrated legacy rows are partial, have null attestation, unknown review basis,
gate basis v0 with null counts and no review-receipt IDs, verification basis
v0 with null digest/link, and may preserve `none` or `legacy_unverified`
without strengthening it. Cycles are immutable and never satisfy a current
gate.

Migration 15 is transactional: every existing Task remains
`legacy_unknown`; existing event bytes/links remain unchanged; each currently
done Task receives exactly one ordinal-1 partial cycle copied only from its
current projection; other statuses receive none. It never parses event prose
or infers reopened cycles.

Activation to v16 rereads current done unknown-coverage Tasks in binary Task-ID
order. It reuses an exact latest un-reopened partial cycle or appends one next
partial cycle; mismatch, overflow, ownership error, or inconsistent link rolls
back the whole activation and marker. From v16, new Tasks explicitly receive
complete coverage; older Tasks remain legacy-unknown permanently.

Migration 17 assigns every pre-existing cycle, including a complete
`native_done` cycle, verification basis v0 with null expectation digest and
null Verification Receipt link while preserving every existing field value
and public meaning. It synthesizes no Receipt. After migration, every new native
cycle uses verification basis v1; only the exact partial
`legacy_current_done` reopen bridge may still insert v0/null/null.

`legacy_history_incomplete` is true when coverage is not complete, any cycle is
partial, or any `task_reopened` event has null cycle link. A fresh
schema-v16-or-later Task with no cycle is complete-history and false.

Both done paths insert one complete verification-basis-v1 cycle, update Task,
create the existing linked completion event, record effort/Viewer state, and
commit atomically under one short writer after all external observation. A
nonempty manual arm links the exact qualifying pass/full Verification Receipt;
a nonempty Runner arm stores a null Receipt link and the exact qualifying
Runner-observation pointer. An expectation whose trimmed text is empty stores
the digest of its exact existing bytes and null links. The exact empty string uses
the fixed empty-text digest, while legacy whitespace-only bytes are not
rewritten. Reopen requires the current done projection to equal the latest
un-reopened cycle; it links the new reopen event and resets current state
atomically. The sole compatibility bridge may create an ordinal-1 partial
verification-basis-v0/null/null cycle for an unknown-coverage done Task with
no cycle. Other mismatch is `completion_history_inconsistent` and no write.

Default `task show` adds exactly one `completion_history` object with
`total`, `returned_count`, `truncated`, `legacy_history_incomplete`, and
`cycles`. It returns the newest complete-row prefix, maximum 10. Each cycle is
at most 8,192 bytes and the complete component at most 32,768, measured with
canonical compact UTF-8 JSON including actual wrapper counts. It never skips
or partially serializes an oversized row.

Public cycle fields are exactly `completion_cycle_id`,
`saved_cycle_ordinal`, `origin`, `completeness`, `completed_at`,
`contract_revision`, `review_tier`, `verification_expectation`,
`verification_attestation`, `completion_evidence`, `review_target`, and
`gate_basis`. Nested evidence has `kind`, `revision`, `reason`,
`external_revision_approved`, `completion_commit_required`, and
`completion_commit_hash`; target has `kind`, `value`, `base_revision`,
`generation`; gate basis has `version`, `kind`,
`required_independent_passes`, `qualifying_independent_passes`,
`changes_requested`, `open_high`, `open_medium`, `fresh_review_required`, and
`qualifying_receipt_ids`.

Gate-basis v0 emits six null counts and an empty receipt array; v1 emits integer
counts and exactly one qualifying ID for Tier-0/Tier-1/fallback or two for
Tier-2 independent basis. Attestation is true or null, never false. Public
events remain only `task_event_id`, `task_id`, `project_id`, `event_type`,
`summary`, and `created_at`; internal cycle link is never emitted.

Before a cycle is emitted, every public free-form completion-evidence and
review-target text field is revalidated with the normal strict privacy guard.
Completion history has no M19.7 compatibility exception. Private or corrupt
stored text fails with the existing sanitized
`completion_history_inconsistent` result rather than being redacted or
returned.

Text show prints history returned/total/truncation/incompleteness and only the
latest cycle's ordinal, origin/completeness, time, evidence kind, target
kind/generation, and review-basis kind. Other list/current/next/effort/packet
outputs remain unchanged and history has no option or pagination.

The only new error is exit-2 `completion_history_inconsistent` with exact
message `stored completion history is inconsistent`. It covers required-cycle
absence, projection mismatch, reused reopen link, ordinal overflow, invalid
gate relationship, and cycle/event ownership conflict; it is not a completion
check blocker code.

<a id="receipt-meaning-and-record"></a>

## Receipt Meaning And Record

One Receipt represents one verification run reported by the trusted
caller after the caller ran it outside taskgov. The only material observation
facts are:

```text
result source_revision duration scope_coverage
```

Stable ownership IDs, current Contract revision, a verification-expectation
digest, and recording time are structural binding metadata, not additional
observed facts. The public Receipt has exactly:

```text
verification_receipt_id project_id task_id contract_revision
verification_subject result duration_ms scope_coverage source_revision created_at
```

`verification_receipt_id` is `tg_verification_receipt_` plus 16 lowercase hex
characters. `verification_subject` is tool-owned and has exactly these keys:

```text
basis_version kind authority_snapshot_id verification_criterion_id
legacy_caller_label
```

Its closed compatibility matrix is:

| Durable row | Subject basis | Snapshot / criterion | Public subject |
|---|---:|---|---|
| Receipt or cycle migrated from schema v17 | `0` | both null | Receipt kind `legacy_caller_label` with the unchanged stored label; a cycle gains no inferred label or subject |
| Native Receipt | `1` | both non-null and equal to the capture-version-1 target binding | kind `task_verification_criterion`, the two IDs, and null legacy label |
| Native cycle with nonempty verification | `1` | both non-null and equal to its qualifying subject-v1 Receipt | the linked Receipt remains required and `pass/full` on the manual branch |
| Native cycle with trimmed-empty verification | `1` | both null | no Receipt or subject is invented |
| Exact partial legacy-reopen bridge | `0` | both null | its existing verification-basis-v0 behavior is unchanged |

The subject discriminator is independent of cycle
`verification_basis_version`; a valid v17 native cycle keeps verification
basis 1 while receiving subject basis 0/null/null. The retained physical
`command_label TEXT NOT NULL` column receives the unchanged caller label only
for basis-zero Receipts and the fixed internal compatibility value
`taskgov-owned-verification-subject-v1` only for basis-one Receipts. That value
is never caller input, a public label, an Evidence Reference or Bundle field,
or a digest input.

The public basis-zero subject is exactly version `0`, kind
`legacy_caller_label`, null snapshot/criterion IDs, and the unchanged label.
The basis-one subject is version `1`, kind `task_verification_criterion`, the
two non-null IDs, and null `legacy_caller_label`. A native Bundle uses a
separate four-key subject object containing only `basis_version`, `kind`,
`authority_snapshot_id`, and `verification_criterion_id`; because only basis
one is admitted, the legacy-label key is absent rather than null. Parent Task,
Contract, and target objects complete the compound identity.

`result` is exactly `pass`, `fail`,
or `timeout`. `duration_ms` is a nonnegative signed-64-bit integer.
`scope_coverage` is exactly `full` or `partial`; `full` is the caller's claim
that this run covers the entire exact current Task `verification` text, while
`partial` is audit context and cannot satisfy completion.

`source_revision` is not caller-authored text. It is the exact locked current
review-target object with keys `kind`, `value`, `base_revision`, and
`generation`. The Receipt writer copies that tuple, the current Contract
revision, and the SHA-256 of domain-separated exact stored verification text
in one short transaction. Its stored representation is the lowercase 64-hex
SHA-256 of `b"taskgov-verification-expectation-v1\0"` followed by the exact
UTF-8 verification bytes. The digest is internal binding data and is not in
the public Receipt. ID and canonical UTC `created_at` are also tool-owned.

For a branch that requires a Receipt, the normal order is: finish exact material,
set the existing review target, and retain its returned generation and closed
route. Only `verification_route=receipt_required` runs the governed verification
against that material and records the Receipt with that generation as the
expected basis. `not_required` and `runner_pass` proceed without that run or
Receipt; `blocked` and unexpected route/code pairs stop closed. The default
Tier-2 no-finding manual/fallback bound is ten governance calls, or eleven when
Effort Advisory is mechanically enabled; a Receiptless Runner pass is one call
lower.

Receipt recording is allowed only for an in-progress or review-pending Task
with verification text that is nonempty after trimming and a nonempty current
review target. At most
one immutable aggregate Receipt is allowed per Task target generation. It
changes no Task timestamp, event, status, Contract, target, review evidence,
completion evidence, or Handoff. A second attempt, including after `fail`,
`timeout`, or `partial`, requires explicitly setting a fresh target generation;
otherwise it fails with `verification_receipt_already_recorded` and message
`verification evidence is already recorded for the current target`.

The public `verification receipt add` command does not run the caller-attested
verification represented by that Receipt, authenticate its caller or process,
assess test quality, infer coverage, or prove the result or that the run
actually exercised the copied target. Invoking Receipt add is the caller's
attestation of those facts. It stores no command body or argument, exit code,
stdout/stderr, log, environment, exception, stack trace, prompt/chat, diff,
credential, or free-form coverage prose. A gate-ineligible version-0 graph
created by the audit-only schema-v20 Runner remains separate: it neither
creates nor qualifies a Receipt and cannot satisfy the manual verification or
completion gate. Current gate-eligible version-1 Runner selection is governed
only by the [shared schema-v21/v22 Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol). Approved exceptions, result-file import,
configured runners that create, import, or qualify Receipts, signatures, and
debug retention are outside this initial Receipt contract.

<a id="verification-receipt-eligibility-and-manual-completion"></a>

## Verification Receipt Eligibility And Manual Completion

A Receipt is exact-current only when all of its project, Task, Contract
revision, verification-expectation digest, and complete source-revision tuple
equal the locked current values. All other Receipts remain append-only audit
history and never reactivate.

The current explicit `--verification-complete` assertion remains required for
every done transition. This section defines the manual Receipt arm consumed by the
schema-v21/v22 three-branch selector in the [shared schema-v21/v22 Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol).
When Task `verification` is empty on marker `0`, no Receipt is required and the
current attestation behavior is preserved. When the selector chooses the manual
arm for nonempty verification, completion additionally requires the unique
exact-current Receipt to have `result=pass` and `scope_coverage=full`. A missing
Receipt is `verification_receipt_required`; any other result/coverage
combination is `verification_receipt_blocking`. A qualifying Runner-pass arm is
Receiptless and is governed only by the [shared schema-v21/v22 Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol). Recovery of the manual
arm requires explicitly setting a fresh target generation and recording fresh
verification; setting an identical
target already advances generation under the current target contract. Partial
coverage never aggregates mechanically because taskgov owns no project test
strategy.

A semantic Task `verification` edit after review targeting begins clears the
current target and completion evidence, advances target generation, moves
review-pending back to in-progress, and requires fresh verification and
review when no status is supplied. In the same edit, an explicit
`in_progress`, `paused`, `blocked`, or `cancelled` transition follows its
normal validation after invalidation; explicit `review_pending` or `done` is
rejected because the target was cleared. Completion-evidence options in that
edit are rejected as `completion_evidence_conflict` rather than being silently
discarded. Contract revision and reopen retain their existing invalidation
behavior. None of these cases deletes historical Receipts. A failed Receipt
does not itself pause, block, revise, hand off, or otherwise mutate the Task.

The immutable completion cycle already stores the exact Task target tuple.
Schema v17 added internal `verification_basis_version`, nullable
`verification_expectation_digest`, and nullable `verification_receipt_id`
 fields to each completion cycle. Existing cycles migrate as version 0 with
 both nullable fields null. Every post-activation native cycle is version 1 and
 stores the same domain-separated digest computed from its exact Task
 verification text, including the empty string and any preserved whitespace.
 Through schema v20, and for the schema-v21/v22 `caller_attestation` arm, a
 verification expectation whose trimmed text is nonempty additionally requires
 a foreign-key link to the unique qualifying exact-current Receipt. The
 schema-v21/v22 `not_required` and `runner_observation` arms require a null Receipt
 link and are instead constrained by the [shared schema-v21/v22 Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol).
Every normal post-activation native completion must insert version 1. The sole
existing reopen compatibility bridge may still insert version 0/null/null only
for its exact unknown-coverage done/no-cycle case; it remains
`legacy_current_done` and partial and cannot satisfy a current gate. No other
post-migration version-0 insert is valid. This discriminator makes an absent
link honest legacy lineage for old cycles and a fail-closed inconsistency for
a new native cycle instead of inferring validity merely from whether a Receipt
row happens to exist.

Schema v18 independently adds `verification_subject_basis_version` and
nullable subject snapshot/criterion IDs. Existing Receipts and cycles retain
subject version 0/null/null, including valid v17 verification-basis-version-1
cycles. A new Receipt and nonempty native cycle require subject version 1 and
the exact capture snapshot/verification criterion; trimmed-empty verification
uses subject version 1 with null IDs and no Receipt. No migration infers a
subject or changes an old label.

The digest and link are internal and do not add a completion-history field.
The linked Receipt must have the same project, Task, Contract revision,
stored expectation digest, and complete target tuple as the cycle and must be
`pass/full`.
Existing cycles and pre-v17 completions receive no synthesized Receipt and
keep their existing public meaning. Initial activation adds no new public
completion-history or Viewer Receipt projection.

Completion-check fail-fast ordering retains the existing missing-attestation
and target checks, then applies the schema-v21/v22 three-branch selector before
review-receipt sufficiency. When that selector chooses the manual Receipt arm, the
missing/blocking Verification Receipt gate retains these readiness codes and
fixed messages:

| Code | Message |
|---|---|
| `verification_receipt_required` | `current verification evidence is required` |
| `verification_receipt_blocking` | `current verification evidence does not satisfy the required result and coverage` |

Invalid stored Receipt structure or binding fails closed with
`invalid_verification_evidence` and message
`stored verification evidence is inconsistent`; no unsafe value is returned.
An invalid completion-cycle basis version or link fails through the existing
`completion_history_inconsistent` contract before a success projection.

<a id="public-and-read-projection"></a>

## Public And Read Projection

The sole Verification Receipt write leaf is public leaf number 21:

```text
taskgov verification receipt add
```

It accepts Task ID plus exactly `--result`, `--duration-ms`, `--scope-coverage`, and
`--expected-target-generation`, together with applicable common `--repo`,
`--json`, and `--read-only`. Expected generation is a positive integer copied
from the target-set result and is only an optimistic concurrency guard; it is
not a second source revision. The writer compares it under the same lock as
Contract, verification expectation, and target capture. Mismatch fails with
`verification_basis_stale` and message
`verification target changed after the reported run`, with no row or
maintenance. The command accepts no caller source revision, Contract revision,
timestamp, command body, result body, or arbitrary file. Successful data is
exactly `receipt`; read-only rejects before any database or maintenance write.

After applicable common CLI, project, and state preflight, Receipt-add
validation is fail-fast in this exact order: `--read-only`; Task ID, result,
duration, coverage, and expected generation; Task existence; done-state
rejection; other Task status; nonempty verification expectation; structurally
valid current target; expected-generation equality; capture-version-1 basis;
the current schema-v22 Runner selector; and same-generation uniqueness. The fixed
service failures are:

| Condition | Code | Message |
|---|---|---|
| `--read-only` | `invalid_argument` | `verification receipt add cannot run with --read-only because it writes the database` |
| invalid result | `invalid_verification_evidence` | `result must be one of pass, fail, or timeout` |
| invalid duration | `invalid_verification_evidence` | `duration_ms must be a nonnegative signed-64-bit integer` |
| invalid coverage | `invalid_verification_evidence` | `scope_coverage must be full or partial` |
| invalid expected generation | `invalid_verification_evidence` | `expected_target_generation must be a positive signed-64-bit integer` |
| done Task | `done_task_requires_reopen` | `done task writes require an explicit reopen` |
| any other disallowed status | `invalid_status_transition` | `verification evidence may be recorded only for an in-progress or review-pending task` |
| empty Task verification | `verification_expectation_required` | `task verification must be specified before recording verification evidence` |
| missing current target | `review_target_required` | `set a current review target before recording verification evidence` |
| expected generation differs | `verification_basis_stale` | `verification target changed after the reported run` |
| retained capture version 0 | `evidence_basis_stale` | `current evidence basis must be captured again` |
| Receipt already exists | `verification_receipt_already_recorded` | `verification evidence is already recorded for the current target` |

Task ID syntax/privacy and not-found retain their existing codes. A malformed
stored target or Receipt uses
`invalid_verification_evidence`, not a missing-target code. Concurrency is
rechecked under the writer lock in the same semantic order; no failed call
publishes backup or Viewer maintenance.

Successful text is exactly three LF-separated lines with no event line:

```text
Verification receipt recorded: <verification_receipt_id>
Result: <result>  Coverage: <scope_coverage>
Source: <kind>/generation <generation>
```

`task show` alone adds `verification_evidence` with exactly `expectation`,
`contract_revision`, `source_revision`, `current_verification_subject`,
`gate`, `counts`, and
`recent_receipts`. Gate contains `required`, `satisfied`, `blocking_code`, and
`qualifying_receipt_id`. Counts contains `receipts_total`,
`receipts_exact_current`, `qualifying_exact_current`, and
`blocking_exact_current`. Recent receipts are newest-first, at most 10, and
use the public Receipt allow-list. The existing Task target remains the source
of the current source-revision object. Other list/current/next/compact/review
packet projections remain unchanged.

The projection types and null rules are fixed:

- `expectation` is the exact existing Task verification string;
  `contract_revision` is a nonnegative integer;
- `source_revision` is null when no current target exists. Otherwise it has
  exactly string `kind`, string `value`, nullable string `base_revision`, and
  positive integer `generation`; base is a full Git object ID only for
  `git_snapshot` and null for every other kind;
- `current_verification_subject` is null unless a capture-version-1 target has
  a nonempty verification criterion; otherwise it is the native subject object;
- `gate.required` and `gate.satisfied` are Booleans;
  `gate.blocking_code` is null or exactly `review_target_required`,
  `evidence_basis_stale`, `verification_receipt_required`, or
  `verification_receipt_blocking`; and
  `gate.qualifying_receipt_id` is null or one Receipt ID;
- all four count values are nonnegative integers. Exact-current, qualifying,
  and blocking counts are each zero or one; and
- `recent_receipts` is an array ordered by
  `created_at DESC, verification_receipt_id DESC`.
  Every row has exactly the [public Receipt fields](#receipt-meaning-and-record), including its nested
  `source_revision`; no digest or internal cycle link is exposed.

For an expectation empty after trimming on marker `0`, the gate is
`required=false`, `satisfied=true`, with both nullable fields null. For a
nonempty expectation on a non-done Task, no target yields
`review_target_required` and a capture-version-0 target yields
`evidence_basis_stale`. The manual Receipt arm then yields Receipt-required,
Receipt-blocking, or satisfied with the qualifying Receipt ID. Marker-`2`
targets instead use the [shared schema-v21/v22 Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol): only the exact closed
no-launch fallback delegates to those manual Receipt values, while an exact qualifying
Runner pass is satisfied with a null Receipt ID. A legacy done Task whose matching
completion cycle has basis version 0, whether migrated or created by the sole
compatibility bridge, is an explicit legacy exemption:
`required=false`, `satisfied=true`, both nullable fields null, and
`source_revision` may be null. A version-1 done cycle must obey its stored
expectation/link rule and any mismatch fails closed instead of projecting a
gate.

Successful JSON `task.show` includes this one top-level key in its exact data
contract. Failure data also contains
`verification_evidence=null`. Text `task show` remains byte-for-byte unchanged
and does not summarize Receipt state; agents use JSON for the new gate.

There is no Receipt list/show/import/export command and no Viewer Receipt
panel or snapshot field. The Viewer accepts source schemas through v22 while
retaining snapshot v4 content. Its existing
bounded batch completion-history read internally joins only the Receipt fields needed
to validate version-1 cycle and subject links plus provenance, manifests, and
References, fails closed on inconsistency, then discards them; no ledger
dataset or fact enters the snapshot. Receipt writes are not
Viewer-relevant and perform no Viewer refresh; a successful write remains
backup-eligible through the existing post-commit coordinator. Failed or read-
only calls invoke neither artifact path.
