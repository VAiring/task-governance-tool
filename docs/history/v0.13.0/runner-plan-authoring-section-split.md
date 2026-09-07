# Runner Plan Authoring Section Split Capture

> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves only the Runner Plan authoring/control sections before
> their document split. Words such as current, approved, or implemented describe
> the captured revision, not current authority. This history cannot fill an
> active-contract gap, satisfy a current gate, or authorize a removed behavior.

- Source commit: `aed16ab456d7074f2647b102c14e894430ff7bc2`
- Source sections:
  `docs/specification.md#current-runner-plan-authoring-and-control-contract` and
  `docs/design.md#current-runner-plan-authoring-and-control-design`
- Capture unit: `TG-ARCH.13`
- Active replacements:
  [Runner Plan authoring specification](../../runner-plan-authoring-specification.md)
  and [Runner Plan authoring design](../../runner-plan-authoring-design.md).
  [Repository authority](../../authority.md) routes the unchanged common owners.
  Use the public CLI for live Task state and evidence.

## Captured Product Section

Source path: `docs/specification.md`

````markdown
## Current Runner Plan Authoring And Control Contract

This section is the current implementation contract for bounded Runner Plan
authoring. The product retains exactly 21 public command leaves and adds only an
explicit action option to the existing `task edit` leaf. This contract grants no
config write, Task side effect, process launch, target mutation, or external
operation without that invocation. `review target set` remains the sole Runner
dispatch. Schema v22, setup non-generation, current PlanV1 capture/resolution,
Runner execution, Evidence, Viewer, and completion behavior remain unchanged.

### Closed Draft And Actions

Authoring reuses the one existing ignored physical file
`<physical-package>/config/verification-runner.json` and the exact current
PlanV1, EntryV1, and StepV1 member sets. It adds no PlanV2, second config,
per-entry enabled flag, tombstone, setup generation, or Git-tracked plan. The
only new caller document is strict UTF-8 JSON from standard input with the
closed shape:

```text
RunnerPlanDraftV1 = version, steps
```

`version` is exactly integer `1`. The raw stdin document is capped at 65,536
UTF-8 bytes by reading at most one byte beyond the limit before rejection.
`steps` is one through 16 exact StepV1 objects under all existing per-step,
aggregate timeout, literal-argument, and separate 65,536-byte final-plan
bounds. Unknown, missing, duplicate, floating,
non-finite, differently typed, malformed, trailing, empty, or over-bound input
is rejected before mutation. The draft never accepts Task ID, Contract
revision, expectation or criterion digest, coverage, Plan ID, trust, shell,
PATH lookup, review target, or command-discovery input. Taskgov derives the
addressed Task ID and exact future Contract/verification basis and fixes
`coverage="full"`; it does not infer a command, entrypoint, argument, coverage,
test sufficiency, or trust from repository files, project documentation,
verification prose, prior evidence, or an LLM decision.

After bounded UTF-8, duplicate-free JSON, closed-member, and member-type
recognition, every caller-supplied StepV1 string leaf (`step_id`, `mode`,
`entrypoint`, each `argv` item, and `cwd`) is passed unchanged through the
existing common deny-by-default privacy guard under the fixed field label
`Runner Plan draft`. This check precedes that leaf's enum, grammar, UTF-8,
UTF-16, and candidate validation. A privacy rejection for an otherwise
recognized string leaf therefore takes precedence over `invalid_argument` and
returns `privacy_rejected`; malformed or duplicate JSON, unknown or missing
members, non-string member types, and raw-document overflow remain
`invalid_argument`. The guard is draft-input admission only: it neither changes
current PlanV1 reader validation nor reclassifies an existing manually authored
Plan source.

The existing-leaf option is
`task edit <task-id> --runner-plan-action <action>`, where the closed action set
and effects are:

| Action | Standard input | Exact Plan effect |
|---|---|---|
| `replace` | one RunnerPlanDraftV1 | Remove every entry for the addressed Task and insert one future-basis entry with the supplied steps at the earliest removed position, or append it when none existed. It is the only initial-set/upsert action. |
| `rebind` | not read | Require one existing entry for the Task, preserve its steps and position, and replace only its basis with the exact future basis. |
| `detach` | not read | Remove every entry for the addressed Task while preserving all other relative order; absence is an idempotent no-op and an empty entries array remains a valid retained Plan. |
| `disable` | not read | Set only global `trusted_local=false`; preserve Plan ID, every entry, and their order. An absent or already-disabled Plan is an idempotent no-op and no file is created for absence. |

On the first `replace` when the file is absent, taskgov creates PlanV1 with
`version=1`, fixed `plan_id="taskgov-local-plan"`,
`trusted_local=true`, and the one derived entry. On every present Plan,
`plan_id`, unrelated entries, and their order are preserved. `replace`,
`rebind`, and `detach` never change `trusted_local`; in particular, they do not
re-enable a disabled Plan. This contract adds no dedicated re-enable action or
restore workflow. It does not claim that a user cannot later make a separately
authorized direct local edit.

Per-Task cardinality is closed: `replace` and `detach` deterministically repair
zero, one, or multiple distinct-basis entries as defined above; `rebind`
requires exactly one and returns a bounded missing-entry or ambiguity error
otherwise; and `disable` is independent of Task-entry count after structural
Plan validation. An exact duplicate basis remains the existing invalid
`plan_ambiguous` source and no action rewrites it.

The first absent-file `replace` may create only the canonical physical
`config` directory when it is lexically absent, after the package-root,
containment, no-reparse, current-index, and effective-ignore checks succeed.
No other action creates that directory. A publication failure may leave only
that identity-owned empty ignored directory; it is not a Plan source or
authority and is never recursively cleaned as part of authoring.

### One-Invocation Task Coordination

A Plan action may be the only requested mutation, or it may accompany one
actual Task basis change in the same public invocation and under the same user
approval. A basis change is exactly a new Task Contract revision or a changed
Task `verification` value. Title, description, status, priority, kind,
lane/order, tags, notes, review tier, pause/block state, reopen, completion, and
review-target changes do not independently require or authorize a Plan update.

When one valid present Plan has `trusted_local=true` and its sole entry for the
Task exactly matches the pre-edit current basis, a basis-changing edit must
include exactly one of `replace`, `rebind`, `detach`, or `disable`. Taskgov does
not choose an action or automatically carry the entry forward. An absent,
disabled, unreadable, malformed, ambiguous, already-stale, or no-entry Plan
does not hold Task authority hostage: a Task-only edit may still proceed under
the ordinary Task contract and the existing Runner remains fallback or
fail-closed. A requested Plan action still requires a valid source appropriate
to that action.

One invocation may combine a Plan action with other ordinary metadata fields
only when it also produces an actual basis change. Plan-only use supplies no
other Task-edit field. Runner Plan actions are incompatible with done,
completion-evidence, reopen, verification-complete, and review-complete modes;
they never invoke completion's Runner selector. A config-only `replace` or
`rebind` requires a current nonterminal Task and positive Contract plus
verification criterion. Config-only `detach` and global `disable` may address
an existing terminal Task because they perform no Task business write. The
positional Task ID binds project and Task scope; for `disable` it does not make
the global flag task-local.

The direct `task edit` invocation is the mutation authority. An agent should
present the combined Task and Plan disposition before invoking it, but no
mandatory preview command, confirmation token, second approval, or second
normal-path CLI round trip is added. `--read-only` retains its existing
write-rejection semantics.

### Ordered Commit And Partial Success

The coordinator first captures and validates the current Task, Contract,
criterion, Plan source, action, draft, option combination, and expected Plan
identity/digest. For a Task edit it derives the future basis and pure action
result in memory inside the existing bounded Task transaction, including
complete candidate Plan bytes only when normalized semantics change, then
commits and closes SQLite. Only afterward must the publisher revalidate the
expected absent-or-current Plan source for every action. A semantic no-op uses
that boundary only to confirm the expected source and performs no publication;
a changed action publishes the complete candidate by a single-file atomic
replacement. No SQLite writer spans a filesystem read or write.

The Task transaction and Plan publication are two separately committed
operations; they are not one atomic transaction. A Task/DB failure leaves the
canonical Plan unchanged. A detected source drift, failed required source
confirmation, or publication failure after a Task commit never rolls the Task
back and never guesses, merges, or overwrites newer Plan bytes. The requested
Plan disposition is then unconfirmed. Whether the expected source stayed
unchanged or another authorized writer changed it, no eligibility state is
inferred; the Task basis commit itself can change an unchanged entry's prior
exact-match status. The caller must not rely
on Runner execution until one explicit config-only `rebind`, `replace`,
`detach`, or `disable` succeeds. There is no compensating Task edit, two-phase
commit, pending SQLite row, retry daemon, or automatic repair.

The source check is a bounded compare-before-replace guard, not a claim of
cross-process linearizability, power-loss durability, Git/config transaction
atomicity, or zero drift after the final check. Existing target-set admission
remains the final freshness owner. Readers may observe only a complete old or
complete new canonical file; temporary material is bounded, private, and
removed on ordinary failure.

Without a Plan action, `task edit` retains its exact current success and
failure data. With an action, success adds exactly:

```text
runner_plan_update = action, status
action = replace|rebind|detach|disable
status = updated|unchanged|unconfirmed
```

`unchanged` means the pure action produces the same normalized Plan semantics,
including absent-source or missing-entry `detach`, absent/already-disabled
`disable`, and an otherwise identical replacement or rebind. A semantic no-op
has no candidate publication, does not normalize valid noncanonical raw bytes
as a side effect, and returns `unchanged` only after post-transaction
confirmation that the expected source still matches. `updated` means changed
normalized semantics were successfully published as complete canonical bytes.
`unconfirmed` is available only after an actual Task commit followed by an
unsuccessful required Plan source confirmation or publication.

Plan-only success returns the current Task, empty `changed_fields`, null
`event`, no Task/Contract write, and `updated|unchanged`. If required source
confirmation or publication fails after an actual Task commit, the command is
a successful partial result with the committed Task/event,
`status=unconfirmed`, and exactly one Runner-Plan-originated bounded warning:

```text
task_applied_runner_plan_unconfirmed
Task update completed but Runner Plan disposition is unconfirmed; apply an explicit Plan action before relying on Runner execution
```

Ordinary post-commit maintenance still receives exactly one opportunity for
that committed Task mutation. Its existing zero through three maintenance
warnings, when any, follow the one authoring warning in their existing order.
A pre-commit failure or config-only source-confirmation/publication failure
remains an ordinary failed command with the existing empty Task-edit failure
data and no maintenance. An action-bearing text success appends exactly
`Runner Plan: <action> <status>` after the existing Task/Contract lines; an
invocation without an action remains byte-compatible.

The new authoring failure map is closed:

| Condition | Code / exit | Fixed public message |
|---|---|---|
| caller StepV1 string rejected by the common privacy guard | `privacy_rejected` / 1 | `Runner Plan draft appears to contain a secret, raw log, or dump content` |
| invalid action, stdin draft, candidate, or caller value | `invalid_argument` / 1 | `arguments are invalid` |
| incompatible Task-edit options | `invalid_option_combination` / 1 | `Runner Plan action cannot be combined with these task edit options` |
| required disposition omitted for an enabled exact-match entry | `runner_plan_action_required` / 1 | `Runner Plan action is required for this Task basis change` |
| `rebind` has no Task entry | `runner_plan_entry_required` / 1 | `Runner Plan entry is required for rebind` |
| unsafe, malformed, over-bound, duplicate-basis, or ambiguous source | existing `plan_source_invalid|plan_invalid|plan_too_large|plan_ambiguous` / 2 | existing sanitized Plan message |
| expected source changes before a config-only confirmation or publication | `runner_plan_changed` / 2 | `Runner Plan changed before update; no Plan change was made` |
| another config-only source-confirmation or publication failure | `runner_plan_update_failed` / 2 | `Runner Plan update did not complete` |

After a Task commit, any later Plan source-revalidation, drift, or publication
failure maps instead to the successful unconfirmed partial result above.
Existing Task, Contract, database, and storage errors keep their current
mappings. No response, warning, event, log,
database, Evidence, Viewer, or history projection contains Runner Draft/Plan
bytes, steps, argv, publisher paths, rejected input, or publisher exception
detail.

### Disable, Evidence, And Activation Boundary

`disable` affects only admission of future attempts; it neither cancels an
in-flight process nor deletes entries, canonical files, Runner graph rows,
observations, References, Bundles, completion cycles, or history. `detach`
removes every entry for the addressed Task from the current Plan and no other
entry. A Task basis change makes older Runner
observations historical; it never converts them into a current Receipt or lets
them satisfy a new current gate.

Authoring never calls `review target set`, captures a review target, launches a
process, creates a Receipt or Runner graph, or changes the existing
target-plan/process/lifecycle/native/completion/Evidence/Viewer paths. The
activation revision synchronizes `AGENTS.md`, the public CLI, active
specification/design/plan, CLI contract reference, README opt-in examples,
package manifest, and focused tests. The Skill and normal Task loop remain
unchanged. TG-RPA.6 performs acceptance-only full offline validation; a
correction returns to its owning predecessor Task.

````

## Captured Implementation Section

Source path: `docs/design.md`

````markdown
## Current Runner Plan Authoring And Control Design

This is the current implementation design for bounded Runner Plan authoring.
The CLI retains 21 leaves and exposes the one explicit Plan action option on
`task edit`; reading this section alone authorizes no config write, Task edit,
process launch, target mutation, or external operation. Setup never creates the
config, and the existing `review target set` parent service remains the sole
Runner dispatch. Schema v22 and the current Runner, Evidence, Viewer, and
completion graphs do not change.

### Separate Authoring Control Boundary

The activated design keeps Plan authoring outside the closed Runner execution
graph. It adds these exact ownership boundaries:

| Owner | Responsibility | Forbidden responsibility |
|---|---|---|
| `verification_runner_plan.py` | Existing physical capture/resolution plus shared pure PlanV1 value decode, validation, and canonical encoding used by both readers and authoring. | No Plan publication, Task/SQLite write, CLI decision, process launch, or Runner graph write. |
| `verification_runner_plan_authoring.py` | Strict RunnerPlanDraftV1 decode and pure `replace|rebind|detach|disable` transforms over one validated PlanV1 value. | No filesystem, SQLite, Git, target, CLI, process, Evidence, Viewer, or logging I/O. |
| `verification_runner_plan_publisher.py` | Capture/revalidate the one canonical physical authoring source for every action and, when supplied, publish one already-canonical bounded candidate through the complete-file replacement boundary. | No Task or Contract decision, database access, action selection, process launch, target materialization, or Runner graph write. |
| `verification_runner_plan_edit.py` | Parent control orchestration, option compatibility, current/future basis selection, DB-first sequencing, publisher invocation, and typed success/partial-success result. | No parser/text formatting, Runner dispatch, process/lifecycle/native call, schema change, Evidence/Viewer write, automatic action, or command inference. |
| `cli.py` | Parse the one action option, read bounded stdin for `replace`, call the parent control service, format the closed result/warning, and schedule ordinary maintenance for a committed Task mutation. | No Plan semantics, basis derivation, physical publication, Runner launch, or second approval protocol. |

The new control-edge set is exactly:

```text
cli -> verification_runner_plan_edit
verification_runner_plan_edit -> tasks/contracts/reviews
verification_runner_plan_edit -> verification_runner_plan_authoring
verification_runner_plan_edit -> verification_runner_plan_publisher
verification_runner_plan_publisher -> verification_runner_plan
verification_runner_plan_publisher -> state_paths
verification_runner_plan_authoring -> verification_runner_plan
verification_runner_plan_authoring -> tasks (common privacy guard only)
```

These edges do not alter the existing Runner-layer registry or add a reverse
edge into `verification_runner_service`, `verification_runner_process`,
`verification_runner_lifecycle`, `_verification_runner_win32`, storage,
completion, Evidence, or Viewer. The execution reader continues to capture and
resolve the same PlanV1 bytes; it neither imports nor invokes authoring or its
publisher.

`verification_runner_plan.py` may expose immutable Plan/Entry value objects and
pure canonical decode/encode helpers instead of duplicating its existing
closed validators. Its current `VerificationRunnerPlanSource`, source capture,
error sanitization, normalized digest, exact-basis selection, and fallback/
block behavior remain byte- and semantics-compatible. The authoring transform
accepts only a validated value and the future basis supplied by its parent. It
preserves Plan ID, global trust except for `disable`, all unrelated entries,
and their order, with the cardinality and insertion rules fixed by the
specification. It creates the fixed initial Plan only for `replace` against an
absent source. No action reads the ambient target, verifies entrypoint
existence, predicts command success, or evaluates test coverage.

The draft decoder, and not the shared PlanV1 reader decoder, applies the
existing pure `tasks.reject_private_or_raw_content` guard to every recognized
caller-supplied StepV1 string leaf with the fixed field label
`Runner Plan draft`. It performs that complete leaf pass before enum, grammar,
UTF-8, UTF-16, candidate, or transform validation, returns the existing
`privacy_rejected` error without the rejected value, and emits no candidate on
failure. It does not call `validate_text`, add a second privacy pattern set, or
change admission of an existing physical PlanV1 source.

`runner_plan_update.status=unchanged` means the pure action produces the same
normalized Plan semantics. A semantic no-op returns no publication candidate
and does not normalize valid noncanonical bytes as a side effect, but it still
requires post-transaction confirmation of the expected source before the
parent may return `unchanged`. Changed semantics produce canonical candidate
bytes, and their successful publication is `updated`. The pure action result
therefore determines the intended status without a later semantic guess; the
publisher determines whether that disposition was safely confirmed.

### Publisher Boundary

The publisher receives the already resolved governed repository root and
physical package root, one expected `RunnerPlanAuthoringSource`, and either
complete canonical candidate bytes or an explicit confirmation-only marker.
The closed source record contains state
`absent_directory|absent_file|present`, the observed package/config/file
physical identities applicable to that state, and raw bytes/digest only for a
present file. It is a new authoring-only value; the current execution
`VerificationRunnerPlanSource` stays unchanged. Capture and revalidation reuse
the current no-follow physical path, regular-single-link, size, case-alias,
current-index absence, and effective-ignore rules for
`config/verification-runner.json`. It never accepts a caller path and never
changes an index entry, selected review-target material, database, setup
artifact, release artifact, or any target-project path other than the one
canonical ignored config directory/file authorized by the invocation.

Every action invokes this boundary after the SQLite writer is closed. The
publisher first revalidates the expected source record. With the confirmation-
only marker it then returns success without creating a directory, temporary
file, or canonical file. With candidate bytes it proceeds to publication. A
source mismatch is never treated as an unchanged semantic result.

Only `replace` against `absent_directory` may create the exact physical
`config` directory, exclusively and without a reparse traversal, after the
repository/index/ignore checks pass. No other action creates it. If later
publication fails, that identity-owned empty ignored directory may remain; it
contains no Plan source and is not recursively removed. A present foreign,
linked, replaced, or nonempty unexpected directory fails closed.

For a detected mismatch, unsafe path, concurrent source change, invalid
candidate, temporary-file failure, or replacement failure, the publisher
returns one sanitized closed failure and does not claim a confirmed disposition
or a new canonical Plan.
Temporary material is created exclusively in the resolved or just-created
physical config directory, has one bounded purpose, is never a Plan source,
and is removed on ordinary failure. Successful publication replaces the
canonical name only with complete bytes; a reader sees a complete old or new
file, not a partially written JSON document.

This is a compare-before-replace guard and complete-file atomic replacement,
not a linearizable filesystem compare-and-swap primitive. It promises no
cross-process exclusion after the last comparison, power-loss durability,
Git/config transaction, or atomicity with SQLite. The later target-set capture
and exact-basis comparison remain the authoritative final freshness checks.

### Parent Coordination Sequence

`verification_runner_plan_edit.py` owns one bounded operation:

1. resolve the existing project/package boundary and read the selected stored
   Task, its Contract/criterion basis, and the physical Plan source;
2. validate the action, Task-field compatibility, current matching-entry
   rule, and the bounded stdin draft, without writing;
3. for Plan-only work, obtain the exact current basis through a read
   transaction and bypass `edit_task`, so no Task event or business write is
   created;
4. for a real basis-changing edit, use the existing Task/Contract write
   transaction, derive its exact future Task/Contract/expectation/criterion
   basis, compute the pure action result, and build and validate full candidate
   Plan bytes in memory only when semantics change, then commit and close
   SQLite;
5. for every action, invoke the publisher only after the writer is closed,
   using the originally captured expected source and either the candidate or
   the confirmation-only marker; and
6. return the typed Plan result together with the existing Task result and one
   mutation classification for CLI maintenance.

The locked Task reread must still match the preflight basis. Candidate creation
inside the Task transaction performs only pure computation; no temporary or
canonical filesystem write occurs while SQLite owns a writer. If Task
validation, Contract revision, basis derivation, candidate bounds, or DB commit
fails, the transaction rolls back and publication is never called.

After a committed basis change, a publisher source-confirmation or publication
failure returns the committed Task result plus
`runner_plan_update.status=unconfirmed`; it does not reopen SQLite or compensate
the Task. Whether the expected source stayed unchanged or another authorized
writer caused the mismatch, the service makes no eligibility claim; the Task
basis commit itself can change an unchanged entry's prior exact-match status.
The caller must not rely on Runner execution until a later explicit Plan-only
action succeeds. A Plan-only publisher failure has no Task result to preserve
and uses the ordinary failed Task-edit envelope. No hidden retry, journal,
pending row, second connection, worker, daemon, or automatic follow-up exists.

The CLI maps a committed Task plus Plan failure to `ok=true`, the fixed
`task_applied_runner_plan_unconfirmed` warning, and the closed unconfirmed
projection. This keeps the existing coordinator eligible for exactly one
ordinary post-commit maintenance opportunity for the actual Task mutation.
The one authoring warning precedes any existing maintenance warnings and is not
a claim that the requested Plan action succeeded or that current eligibility
is known. A config-only success or failure performs no Task maintenance; an
invocation without a Plan action remains byte-shape compatible with the
existing CLI.

Completion/done/reopen modes are rejected before this control service because
the compatibility completion path has its own Runner selector and cannot be
combined with authoring. A combined edit must produce an actual basis change;
other metadata may accompany that change but cannot make a Plan action valid
by itself. A Plan-only branch is the only supported way to make a config change
without a basis edit. `replace` implies one bounded stdin read; other actions
do not read stdin.

### Activation And Test Allocation

The sequential implementation allocation is deliberately narrow:

- TG-RPA.2 owns only shared pure Plan values, draft decode, transforms, and
  focused action/round-trip/privacy tests, including privacy precedence over a
  recognized leaf's grammar or size failure and no candidate emission, plus
  release-manifest synchronization for its changed packaged files;
- TG-RPA.3 owns only the physical publisher and focused path, no-op
  confirmation, drift, replacement, failure-cleanup, and privacy tests, plus
  release-manifest
  synchronization for its changed packaged files;
- TG-RPA.4 owns only the internal coordination service and focused DB/order/
  partial-success/no-launch tests, plus release-manifest synchronization for
  its changed packaged files;
- TG-RPA.5 alone connects the public parser and synchronizes active formal
  documents, `AGENTS.md`, CLI contract reference, README opt-in examples, final
  package-manifest state, help/output behavior, and focused end-to-end tests;
- TG-RPA.6 changes no product behavior and runs final exact-target acceptance,
  including the full deterministic offline suite.

Every unit is Tier 2 and receives two independent exact-target reviews. Earlier
code units run their focused tests plus manifest/lane/document checks as
applicable; they do not repeat the full suite. The full suite is reserved for
TG-RPA.6. No unit adds a schema/migration, PlanV2, setup/doctor/Viewer/Evidence
route, Skill trigger, public leaf, Runner execution command, automatic command
discovery, re-enable action, hostile-code claim, network action, or M24
redesign.

````
