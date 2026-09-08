# Runner Plan Authoring And Control Design

This document owns the current Runner Plan authoring/control implementation
structure delegated by the [implementation design](design.md#current-runner-plan-authoring-and-control-design),
for the behavior in the
[Runner Plan authoring specification](runner-plan-authoring-specification.md).
The shared [Plan values and target admission](runner-execution-design.md#target-plan-implementation),
[Task Contract revisions](task-operation-design.md#immutable-task-contract-revisions), and
[post-commit coordinator](setup-state-design.md#post-commit-coordinator) remain at their
existing owners.

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
verification_runner_plan_authoring -> task_values (common privacy guard only)
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
existing pure `task_values.reject_private_or_raw_content` guard to every recognized
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
