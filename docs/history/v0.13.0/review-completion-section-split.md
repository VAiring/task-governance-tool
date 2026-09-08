# Review And Completion Section Split Capture

> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves only the Review, Verification, and completion sections
> before their document split. Words such as current, approved, or implemented
> describe the captured revision, not current authority. This history cannot
> fill an active-contract gap, satisfy a current gate, or authorize a removed
> behavior.

- Source commit: `45f96941f8abb5b39d267710d5dceccee3623147`
- Source paths: `docs/specification.md` and `docs/design.md`; the exact
  section boundaries are identified with each captured block below.
- Capture unit: `TG-MOD.13`
- Active replacements:
  [Review and completion specification](../../review-completion-specification.md)
  and [Review and completion design](../../review-completion-design.md).
  [Repository authority](../../authority.md) routes the unchanged common owners.
  Use the public CLI for live Task state and evidence.

## Captured Section 1: Review Target, Receipt, And Finding Ledger

Source path: `docs/specification.md`

Source range: `### Review Target, Receipt, And Finding Ledger` through immediately before `### Versioned Review Provenance And Bundle Boundary`.

````markdown
### Review Target, Receipt, And Finding Ledger

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

````

## Captured Section 2: Git Snapshot And Target Binding

Source path: `docs/specification.md`

Source range: `### Git Snapshot And Target Binding` through immediately before `### Review Packet`.

````markdown
### Git Snapshot And Target Binding

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

````

## Captured Section 3: Review Packet

Source path: `docs/specification.md`

Source range: `### Review Packet` through immediately before `### Completion Evidence And Commands`.

````markdown
### Review Packet

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

````

## Captured Section 4: Completion Evidence And Commands

Source path: `docs/specification.md`

Source range: `### Completion Evidence And Commands` through immediately before `### Typed Checkpoint`.

````markdown
### Completion Evidence And Commands

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

````

## Captured Section 5: Completion Cycle History

Source path: `docs/specification.md`

Source range: `## Completion Cycle History` through immediately before `## Current M25 Select-Split-Merge-Register Contract`.

````markdown
## Completion Cycle History

Schema v15 adds `tasks.completion_history_coverage` (`legacy_unknown` or
`complete`, default legacy unknown), nullable internal
`task_events.completion_cycle_id`, and append-only
`task_completion_cycles`. Schema v16 is a marker-only
`completion_cycle_capture_activation` migration: it adds no object but prevents
a schema-v15 binary from writing after native capture activates. Schema v17
adds the Verification Receipt basis discriminator and link described below.

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

````

## Captured Section 6: Receipt Meaning And Record

Source path: `docs/specification.md`

Source range: `### Receipt Meaning And Record` through immediately before `### Verification Receipt Eligibility And Manual Completion`.

````markdown
### Receipt Meaning And Record

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
only by the shared schema-v21/v22 three-branch matrix below. Approved exceptions, result-file import,
configured runners that create, import, or qualify Receipts, signatures, and
debug retention are outside this initial Receipt contract.

````

## Captured Section 7: Verification Receipt Eligibility And Manual Completion

Source path: `docs/specification.md`

Source range: `### Verification Receipt Eligibility And Manual Completion` through immediately before `### Public And Read Projection`.

````markdown
### Verification Receipt Eligibility And Manual Completion

A Receipt is exact-current only when all of its project, Task, Contract
revision, verification-expectation digest, and complete source-revision tuple
equal the locked current values. All other Receipts remain append-only audit
history and never reactivate.

The current explicit `--verification-complete` assertion remains required for
every done transition. This section defines the manual Receipt arm consumed by the
schema-v21/v22 three-branch selector under Current Schema-v22 Persistence Contract.
When Task `verification` is empty on marker `0`, no Receipt is required and the
current attestation behavior is preserved. When the selector chooses the manual
arm for nonempty verification, completion additionally requires the unique
exact-current Receipt to have `result=pass` and `scope_coverage=full`. A missing
Receipt is `verification_receipt_required`; any other result/coverage
combination is `verification_receipt_blocking`. A qualifying Runner-pass arm is
Receiptless and is governed only by that later matrix. Recovery of the manual
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
 link and are instead constrained by the current tagged union below.
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

````

## Captured Section 8: Public And Read Projection

Source path: `docs/specification.md`

Source range: `### Public And Read Projection` through immediately before `### Migration And Activation Boundary`.

````markdown
### Public And Read Projection

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
  Every row has exactly the public Receipt fields above, including its nested
  `source_revision`; no digest or internal cycle link is exposed.

For an expectation empty after trimming on marker `0`, the gate is
`required=false`, `satisfied=true`, with both nullable fields null. For a
nonempty expectation on a non-done Task, no target yields
`review_target_required` and a capture-version-0 target yields
`evidence_basis_stale`. The manual Receipt arm then yields Receipt-required,
Receipt-blocking, or satisfied with the qualifying Receipt ID. Marker-`2`
targets instead use the shared schema-v21/v22 matrix below: only the exact closed
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

````

## Captured Section 9: Typed Completion Evidence

Source path: `docs/design.md`

Source range: `### Typed Completion Evidence` through immediately before `### Review Target And Git Snapshot`.

````markdown
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

````

## Captured Section 10: Review Target And Git Snapshot

Source path: `docs/design.md`

Source range: `### Review Target And Git Snapshot` through immediately before `### Receipts, Findings, And Gate`.

````markdown
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

````

## Captured Section 11: Receipts, Findings, And Gate

Source path: `docs/design.md`

Source range: `### Receipts, Findings, And Gate` through immediately before `### Provenance, Evidence Ledger, And Bundle Structure`.

````markdown
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

````

## Captured Section 12: Review Packet

Source path: `docs/design.md`

Source range: `### Review Packet` through immediately before `## Test-Only Independent Evidence Reader`.

````markdown
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

````

## Captured Section 13: Completion Cycle History

Source path: `docs/design.md`

Source range: `## Completion Cycle History` through immediately before `<a id="schema-v21-manual-receipt-arm-and-bundle-integration"></a>`.

````markdown
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

````

## Captured Section 14: Current Schema-v22 Manual Receipt Arm And Bundle Integration

Source path: `docs/design.md`

Source range: `<a id="schema-v21-manual-receipt-arm-and-bundle-integration"></a>` through immediately before `## Task Contracts, Checkpoints, Handoffs, And Effort`.

````markdown
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

````
