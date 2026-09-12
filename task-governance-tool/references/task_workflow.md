# Task Workflow

Use the section for the current operation; unrelated sections are not prerequisites.
Examples run from the target-project root. On Linux/macOS replace `python`
with `python3`. `<task-id>` comes from `task context` at
`data.selected.task.task_id`, or from a successful `task add` at
`data.task.task_id`; never substitute a title or invent an ID.

## Contents

- [Source Of Truth And Install Boundary](#source-of-truth-and-install-boundary)
- [First Use And Optional Diagnosis](#first-use-and-optional-diagnosis)
- [Bounded Operating Loop](#bounded-operating-loop)
- [Selection And Execution Boundary](#selection-and-execution-boundary)
- [Task Contract](#task-contract)
  - [Concise Contracts With Current Owners](#concise-contracts-with-current-owners)
- [Optional Effort Advisory](#optional-effort-advisory)
- [Optional Continuation Checkpoint](#optional-continuation-checkpoint)
- [Pause, Resume, And Block](#pause-resume-and-block)
- [Scope Control And Local Handoff](#scope-control-and-local-handoff)
- [Review And Completion](#review-and-completion)
- [Taskize Or Add Scope](#taskize-or-add-scope)
- [Safety Boundary](#safety-boundary)

## Source Of Truth And Install Boundary

The target project's applicable `AGENTS.md`, specifications, design, tests,
and current user instructions outrank local Task state. Before changing
material, read their relevant current responsibilities and exceptions.
The [Skill invocation](../SKILL.md#scope-and-invocation) owns the physical
project-scoped install and execution-directory rules. If state reports
`project_relocation_required`, use the [explicit preview/approval procedure](cli_contracts.md#setup);
never infer move/copy/fork intent or confirm automatically.

## First Use And Optional Diagnosis

After physical installation and ignore protection, run the one explicit first-use
operation when the user intends to use taskgov:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

Setup is the only command that initializes or migrates state. It also opts into
local continuity and repairs canonical offline projections, noninteractively
and idempotently. The normal Skill flow supplies no maintenance-policy options.
When the database is missing, setup selects valid managed recovery material;
it does not ask the LLM for a path or generation or silently replace invalid
recovery material with empty state. See [setup results and failure handling](cli_contracts.md#setup)
when previewing or interpreting setup.

For an upgrade, preserve project-local state and run explicit setup.
There is no downgrade or restore command. Release rollback restores one
matched pre-migration package, database, and managed-artifact set together;
never run an older runtime against a newer schema, mix generations, or treat a
Git checkout alone as state rollback.

Only for explicit diagnosis, install/release validation, or a returned
setup/migration/package/layout/state-readiness error, use the read-only
[doctor](cli_contracts.md#doctor):

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py doctor --json
```

Doctor never initializes or repairs and is not a prerequisite for setup or normal
work. Recognized advisory/maintenance results keep `suggested_action=continue`;
do not turn them into a question, handoff, pause, blocker, or routine stop.

## Bounded Operating Loop

Use this normal no-finding Tier 2 task sequence. Examples run from the
target-project root; optional branches are read only when their condition occurs.

1. Immediately after registration, use its ready `context_preparation.context`
   as the context below, without another read. Otherwise select or resume with
   **no Task ID argument**:

   ```powershell
   python .agents/skills/task-governance-tool/scripts/taskgov.py task context --json
   ```

   On `ok=true`, use `data.selected.task.task_id` as `<task-id>` below.
   The tool resumes the first `in_progress` or `review_pending` Task, otherwise
   selects the first ready candidate. `data.current` recalls held work;
   `data.selected` provides the complete Contract, latest checkpoint, and gates.
   Do not reread current/next/show. `selection=none` is successful absence;
   `ok=false` stops selection rather than falling through to another candidate.
   For explicit inspection of a known Task only, `task show <task-id>` requires
   that returned ID; [audit](cli_contracts.md#task-audit-detail) is historical
   investigation, never another normal read or current completion evidence.
2. If `data.selected.task.status=ready` and its implementation is authorized,
   start that Task:

   ```powershell
   python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status in_progress --json
   ```

   An already active/review-pending Task, including one registered for authorized
   immediate work as described in [registration](#registration-contract-and-ordering),
   needs no redundant start write. Selection and stored status do not grant
   implementation permission.
   State the intended outcome, write scope, verification gate, and review tier
   from current authority before implementation. Respect
   [selection and lane boundaries](#selection-and-execution-boundary).
3. Finish the exact governed material. Use [handoff](#scope-control-and-local-handoff)
   for out-of-scope discoveries and [checkpoint](#optional-continuation-checkpoint)
   only at a useful continuation boundary.
4. Only when `data.selected.effort_advisory_enabled=true`, make one
   [Effort observation](#optional-effort-advisory) at the verification/review boundary.
5. [Set the exact review target](#set-the-review-target).
   Keep `data.task.review_target_generation`, `data.verification_route`, and
   `data.blocking_code` from that same successful response; no post-target
   show call or inferred route is needed.
6. Apply that response:
   - `not_required` or `runner_pass`, with null `blocking_code`: proceed
     without external verification or a Verification Receipt;
   - `receipt_required`, with null code: run the governed verification against
     that exact material, then record one aggregate result as below;
   - `blocked` with a non-null returned gate code: stop closed;
   - any other/missing pair: stop closed.

   Only in the Receipt-required branch, use the actual measured result,
   duration, coverage, and the returned generation:

   ```powershell
   python .agents/skills/task-governance-tool/scripts/taskgov.py verification receipt add <task-id> --result <pass|fail|timeout> --duration-ms <milliseconds> --scope-coverage <full|partial> --expected-target-generation <generation> --json
   ```

   `full` describes the whole Task verification expectation, not merely success.
   Taskgov does not run this external verification or retain its command/output.
   If the verifier already emits the [fixed structured result](cli_contracts.md#structured-verification-result),
   send its bytes unchanged with `--from-stdin` instead of those four options;
   do not add an LLM reading/conversion step. Otherwise keep the manual form.
   A `fail`, `timeout`, or `partial` Receipt requires a fresh target **before**
   another run can become current; a Receipt cannot override a blocked Runner.
   See [Receipt conditions](cli_contracts.md#verification-receipt) only for their
   exact bounds or a rejected/stale basis.
   Registration `ok=true` does not mean verification passed. Continue only with
   `data.review_preparation.status=ready`, using its `packet` directly; do not
   run a second prepare/show/context. `blocked` or `failed` does not permit
   review continuation. For preparation-only failure or an uncertain response,
   follow [same-Receipt retry](cli_contracts.md#verification-receipt); never
   blindly register the result again.
7. [Record the actual reviews](#prepare-and-record-reviews) using the returned
   Packet. Only the Receiptless routes need standalone Packet preparation.
8. After the current gates pass, [complete with the appropriate evidence](#complete-work).

`doctor`, completion `--check`, and `task checkpoint` are optional and absent
from the default success path. No mandatory question, additional confirmation,
or routine user-return stop is added.

## Selection And Execution Boundary

- Treat `optional` tasks as actionable when `status=ready`.
- Treat `sequential` tasks as actionable only when they are ready and every
  earlier task in the same lane is `done` or `cancelled`.
- Keep unrelated optional tasks and other lanes actionable when one lane
  blocks.
- Preserve deterministic priority/lane/order selection from the CLI; do not
  re-rank candidates semantically inside the Skill.
- Treat `paused_tasks_present` from `task next` as an advisory recall hint.
  In the normal context flow, use its returned recall without an additional
  read. For explicit held-work inspection, the bounded paused subset remains
  available without changing returned candidates:

  ```powershell
  python .agents/skills/task-governance-tool/scripts/taskgov.py task current --repo <target-project> --status paused --json
  ```

Before starting each execution unit, state its intended outcome, write scope,
verification gate, and review tier. Update the task only after those values
come from current authority.



## Task Contract

Use a Contract only when scope and acceptance are already explicit in the
current user request, an approved roadmap, or task-registration input. Copy
those values deterministically. Never ask for missing Contract fields and
never infer that duration, effort, or risk makes a Contract mandatory.

Record revision 1 during registration. For work explicitly authorized in a
conversation, a stable reference can be recorded as follows:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task add --repo <target-project> --title "Bounded change" --contract-scope "Authorized files and behavior" --contract-acceptance "Exact completion condition" --contract-constraints "No unrelated cleanup" --contract-authority-ref "conversation:example-session:approved-change-1" --json
```

Replace the example session and instruction identifiers with the actual stable
reference to the authorizing instruction, not its text. `conversation:` is an
illustrative convention, not a required prefix; other stable authority
identifiers remain valid, and the initial authority reference remains optional.
Recording a reference does not authenticate an instruction or grant approval.

Alternatively, activate revision 1 only on an exact revision-zero
`ready|blocked -> in_progress` transition:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --contract-scope "Authorized files and behavior" --contract-acceptance "Exact completion condition" --json
```

Make a later semantic revision only from later explicit authority and include
its reason:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --contract-scope "Revised explicit scope" --contract-acceptance "Revised explicit acceptance" --contract-authority-ref "user_instruction:<task-id>:<revision>" --contract-change-reason "User changed the accepted boundary" --json
```

The reserved `user_instruction:` form checks the actual Task ID returned by
taskgov, not a conversation ID, and an allowed positive Contract revision.
For a semantic edit, use the next Contract revision. The existing concurrent
write path also accepts the current revision and binds it to the next allocated
revision; an exact content replay may use an older positive same-Task reference.
A new `task add` has not returned its Task ID yet: the conversation reference
above avoids guessing that ID. A conversation ID in the reserved Task-ID slot
is rejected. These checks do not authenticate user approval.

Do not use a document produced by the current task to authorize that task's own
expansion. Hand off proposed hardening outside the current Contract. A
canonically unchanged Contract is a write-free replay. A semantic revision
invalidates current completion/review eligibility so fresh gates apply without
another scope question.

Any explicitly supplied constraints use strict normal privacy validation.
Omitting later constraints preserves the already-validated prior bytes,
including [bounded legacy counter forms](cli_contracts.md#errors-and-privacy); that carry-forward is not acceptance
of caller-supplied legacy vocabulary and grants no authority. For future
external-operation intent or evidence, use
`operation_sequence=<positive canonical integer>` only as correlation or
idempotency metadata. Current explicit authority for the operation remains
separate.

### Concise Contracts With Current Owners

A concise Contract can retain the Task-specific outcome, scope, acceptance,
exceptions, and existing consultation conditions while referencing shared rules
at their current owners. References do not replace those Task-specific facts or
create a second authority. There is no word/line target or mandatory truncation.

For example, suppose an explicit registration instruction is:

> Register a Task to clarify the existing CLI example in README.md. A directly
> linked example fixture may also change, only if needed to validate that example.
> The example must match the implemented CLI and pass existing document checks
> and Tier 1 review. Keep CLI behavior unchanged; consult the user if changing it
> is necessary. Apply the project's current shared mutation and verification
> rules and its current product authority routing.

In this illustrative target, those owners are `AGENTS.md#target-project-safety`,
`AGENTS.md#testing-and-verification-rules`, and
`docs/authority.md#trigger-routing`. These references resolve from that target's
root, not the installed package. Their names are examples, not required project
files; use the governing project's actual current owners and their applicable
conditions and exceptions.

The existing `task add` fields can capture that instruction without copying the
shared rules into the Contract:

| Field | Example value |
|---|---|
| `--title` | Clarify the README CLI example |
| `--contract-scope` | Clarify README.md's existing CLI example; only its directly linked fixture may also change, if needed to validate the example. |
| `--contract-acceptance` | Example matches implemented CLI; existing document checks and Tier 1 review pass. |
| `--contract-constraints` | Preserve CLI behavior; consult the user if changing it is necessary. Apply current AGENTS.md#target-project-safety and AGENTS.md#testing-and-verification-rules, with product owners routed by docs/authority.md#trigger-routing. |
| `--contract-authority-ref` | conversation:example-session:approved-change-1 |
| `--review-tier` | 1 |

The fixture exception and consultation condition are explicit input in this
example, not defaults to add to other Tasks. Referring to common owners keeps
their rules applicable without duplicating them or broadening permission.

## Optional Effort Advisory

The selected detail from `task context` (also available through explicit
`task show`) supplies `effort_advisory_enabled=true|false`. Only `true`
mechanically adds:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task effort --repo <target-project> <task-id> --read-only --json
```

Run it once at the existing verification/review boundary. Continue directly
when `suggested_action=continue`. When
`suggested_action=reconcile_scope`, read
[reconciliation.md](reconciliation.md) and run one non-blocking
session-local episode for the whole result, not one episode per exceeded
metric. Neither action by itself asks the user, creates a handoff, expands
acceptance, pauses, blocks, fails, or adds a completion/review gate. A separate
concrete safety problem still follows the project's existing safety rules.

Do not create or change an Effort Advisory profile without explicit
project/user authority. Repeated observations add no value and require no
acknowledgement.

## Optional Continuation Checkpoint

Record a checkpoint only at a genuine continuation boundary when a compact
structured resume note is useful:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task checkpoint --repo <target-project> <task-id> --summary "Completed bounded implementation slice" --next-action "Run focused verification" --unresolved-risk "Review exact staged revision" --json
```

Keep summary and next action within 1,024 UTF-8 bytes each. Supply
`--unresolved-risk` at most eight times, at most 512 bytes each and 4,096 bytes
combined; the caller payload is capped at 6,144 bytes. Exact replay of the
latest checkpoint for the same Contract revision writes nothing.

A checkpoint is optional. Never require it for pause, resume, review, or
completion. It does not change task status, selection, gates, or
`tasks.updated_at`. Default `task current`, `task show`, and the selected
detail from `task context` expose only the
latest checkpoint; compact selection intentionally omits its content.
New checkpoint content uses strict normal privacy validation. The bounded
legacy reader exists only to return an already-stored checkpoint summary with
[bounded legacy counter forms](cli_contracts.md#errors-and-privacy)
unchanged; it creates no Task or external-operation authority.

## Pause, Resume, And Block

Pause only `in_progress` or `review_pending` work with a concise reason:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status paused --pause-reason "Waiting for a safe continuation window" --json
```

Use held-work recall already returned in `task context`; only for explicit
held-work inspection use `task current --status paused`. Resume the known Task
explicitly:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --json
```

Record a blocking condition on its owning task:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for user decision on the accepted boundary" --json
```

After blocking one lane, return to `task context` to resume another active Task
or select unrelated ready work with its complete context.

## Scope Control And Local Handoff

Classify each discovery once:

1. Keep it in the current Task only when it is within accepted scope and
   current authority safely permits the repair. This includes
   acceptance-required work and regressions introduced by that Task; a failing
   test alone establishes neither condition.
2. Record it as the current Task's blocker only when it prevents acceptance
   and safe authorized work for the affected Task or lane is exhausted.
3. Otherwise, durably hand it off before continuing:

   ```powershell
   python .agents/skills/task-governance-tool/scripts/taskgov.py handoff record --repo <target-project> <task-id> --summary "Concise out-of-scope discovery" --rationale "Outside current acceptance" --json
   ```

Always use `handoff record`, regardless of whether Issue tooling is absent or
may be added later. This release stores `pending_handoff` locally and performs
no delivery, claim, semantic triage, prioritization, or Issue lifecycle work.
A successful handoff never expands acceptance or changes task selection,
completion, events, or timestamps.

Exact canonical replay returns the existing record. Supply
`--occurrence-id <stable-id>` only when a user or deterministic source already
provides a stable identity for a genuinely distinct occurrence.

Rediscover records with:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff list --repo <target-project> --json
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff show --repo <target-project> <handoff-id> --json
```

Use `handoff withdraw` only when the user explicitly withdraws or handles an
undelivered record out of band:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff withdraw --repo <target-project> <handoff-id> --reason "Explicit user direction" --json
```

If a record returns `privacy_rejected`, never repeat, quote, log, store, or
forward the rejected raw input. Make at most one new attempt with a newly
written concise sanitized abstraction. If local persistence returns
`handoff_not_persisted`, stop only that execution unit until the same record is
durable or the user explicitly accepts forgetting risk.

## Review And Completion

Follow the [normal loop](#bounded-operating-loop) for sequencing. Use the
applicable subsection below for exact target, review, completion, or repair work.

### Set The Review Target

Only after the exact material is ready, stage precisely the intended Git files
through the project's approved Git workflow, then capture the staged candidate:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review target set <task-id> --kind git_snapshot --json
```

Use the Task ID from `data.selected.task.task_id` in the context response.
`git_snapshot` rejects `--revision`; unstaged/untracked material is excluded.
For already committed or non-Git material, the existing `git_commit`,
`diff_fingerprint`, or `external_revision` kind requires `--revision`.
See [target input](cli_contracts.md#review-evidence) for those forms.

Every successful set advances the generation. Retain
`data.task.review_target_generation` and apply that response's
`data.verification_route` / `data.blocking_code` in the normal loop.
A migrated capture-version-0 target is read-only lineage: new Verification
Receipts, Review Receipts, Findings, and completion require a fresh target.
Preparing its packet and resolving an existing Finding remain allowed;
the old target is never upgraded in place.

### Prepare And Record Reviews

For a Receipt-required route, use the actual `data.review_preparation.packet`
returned with status `ready` by registration. For `not_required` or `runner_pass`
only, after handling the exact-target requirement, prepare one packet:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review prepare <task-id> --read-only --json
```

Give that actual packet to the required independent reviewers; do not rebuild
Task/Contract/target prompts from separate reads. The command launches no
reviewer and stores no packet/result. Use the Packet's `review_target`,
`contract.revision`, and `task.task_id` for result binding. Standalone
preparation success by itself is not verification gate success.
Follow its target-kind inspection instruction:

- `git_snapshot`: matching stage-0 index against the stored base, never
  unstaged or untracked worktree material;
- `git_commit`: target commit tree/blobs against first parent, or empty tree
  for a root, not ambient HEAD/worktree;
- `diff_fingerprint` or `external_revision`: no PASS until exact supplied
  material is demonstrably bound to that value.

Request each reviewer's actual verdict, sanitized summary, Findings, and
provenance in the [structured result format](cli_contracts.md#structured-review-results).
Combine only `receipts` arrays whose Task ID, Contract revision, and complete
target tuple match exactly, preserving returned values. Do not fill missing
provenance or rewrite judgment. Then send the assembled UTF-8 JSON once:

```powershell
$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$resultJson | python .agents/skills/task-governance-tool/scripts/taskgov.py review result add <task-id> --json
```

Invalid input saves no prefix. Obtain corrections from their actual source.
If a response is lost, inspect recorded state before retrying; committed replay
is not idempotent. JSON grants no approval. Use a named-reviewer approval flag
only with actual current user approval under the existing fallback rule.
If the received review is not in that structured format but supplies the actual
Receipt declarations, use the [single Receipt path](cli_contracts.md#review-provenance)
and record its actual Findings through the existing Finding command. Do not
invent missing verdicts, provenance, or target binding to manufacture either
input. Correct invalid/incomplete results at their source; splitting a rejected
batch into single calls must not bypass its rejection. This is a choice from
the received result, not another routine comparison, probe, or reference read.

Tier 2 normally requires two distinct independent PASS receipts for the exact
current target/generation. Taskgov deterministically evaluates qualifying PASS
receipts and changes-requested receipts only for the current review target and
generation. Any unresolved high or medium finding from any recorded generation
still blocks. Distinct reviewer keys prove distinct stored strings, not distinct
people, LLMs, machines, independent processes, independence, or authenticated
provenance. Caller declarations do not prove actual model/Skill use or review truth.

### Repair Findings

For returned Findings, use their actual `review_finding_id` from the successful
registration response (batch: `data.receipts[].findings[].finding.review_finding_id`).
After confirming fixes, record [selected resolutions](cli_contracts.md#structured-finding-resolutions);
share a reason only for explicitly grouped IDs, never derive resolution from PASS.
Preserve successful resolutions after a lost response and resubmit only IDs
proven still open. Unselected Findings and original content remain unchanged.

A current-generation `changes_requested` Receipt or unresolved high/medium
Finding blocks completion. After a meaningful fix, set a newer target and obtain
fresh qualifying reviews. A result that remains blocking counts as an
unsuccessful remediation cycle. If test or review failure recurs after an
attempted repair, read [reconciliation.md](reconciliation.md) before another
materially equivalent repair.

### Complete Work

For a reviewed `git_snapshot`, create the completion commit through the
project's approved Git workflow without changing the reviewed staged tree.
Use the full commit ID returned by that workflow as `<hash>`:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete <task-id> --verification-complete --review-complete --completion-evidence-kind git_commit --completion-revision <hash> --json
```

The commit must have exactly one parent equal to the captured base and the same
tree; root and merge commits do not satisfy a snapshot target. Taskgov does not
stage, create commits/branches, push, open PRs, or write Issue comments.
Completion revalidates current verification/review gates, unresolved Findings,
sequential predecessors, and the evidence binding.

Only when applicable, use one of the other evidence forms instead:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete <task-id> --verification-complete --review-complete --completion-evidence-kind external_revision --completion-revision <revision> --completion-evidence-reason "Approved external release" --external-revision-approved --json
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete <task-id> --verification-complete --review-complete --commit-not-required --json
```

External revision requires an actual approved durable revision and reason.
`commit_not_required` is for no managed material change and requires a matching
`diff_fingerprint` target. These forms are mutually exclusive.

Optional inspection only: add `--check --read-only` to the intended complete
command. This is absent from normal success, records no authorization token,
and never replaces the write's fresh revalidation.
Maintenance warnings preserve the successful business result; follow
[continuity warnings](cli_contracts.md#internal-continuity-boundary), not a new retry loop.

### Reopen

Done Tasks are write-locked. Reopen approved follow-up work only as an isolated
transition using the known Task ID:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status in_progress --reopen-reason "Approved follow-up correction" --json
```

Do not combine reopen with other edits. History is preserved for audit, not
current gates; fresh verification basis, target, reviews, and completion
evidence are required.

## Taskize Or Add Scope

Use this guidance for two and only two explicit authority events: a request to
register or taskize already-authorized work, and an explicit scope addition to
an `in_progress` or `review_pending` Task. Discovery, a test failure, an Effort
result, task size, difficulty, or model preference does not invoke it. Replies
and clarifications about the same outcome stay in the same event; only
materially changed scope, order, or permission authority starts another event.
Keep the authority envelope, candidates, fragment coupling, grouping, and Tier
basis session-local; do not persist a classifier record, worksheet, or basis.

### One-Pass Select-Split-Merge

Apply this fixed sequence once:

1. **Select** one authority envelope containing the complete authorized
   outcome, its permission boundary, binding order, explicit Contract facts,
   and binding Review Tier mappings.
2. **Split** once into flat candidate responsibilities where separate completion
   has a concrete purpose: separate approval, usable early handoff, or finishing
   one outcome while another is held. Being separately implementable or testable,
   or having different files, modules, or feature names, is not enough. Conserve
   the complete, non-overlapping scope and explicit permission unions without
   omission or expansion; use only order representable by existing lane/order.
3. **Merge** coupled fragments and internal work on the same outcome when separate
   completion would change no decision or use and mainly repeat gates. Form all
   concrete transitive groups in one global pass. Preserve explicit approval,
   order, and acceptance boundaries, and keep responsibilities separate when
   combining them would obscure which changes and checks satisfy each acceptance
   condition. Shared files or tests alone do not force a Merge; ambiguous
   ownership uses the fallback below instead of a guessed owner.
4. Treat the result as final. Do not recursively classify, run a second Merge,
   re-Split, create a parent/child graph, or optimize by size or Task count.

A provisional candidate states one bounded responsibility, its authorized
consumed inputs and produced outputs, and any concrete fragment-to-owner
coupling. It may expose that it cannot yet stand alone; final viability is
judged only after Merge. Each final group must own its bounded responsibility,
leave a correct repository state after represented predecessors, carry locally
attributable verification and review, use existing lane/order, and be resumable
by a fresh agent from its Contract, routed authority, and predecessor outputs.
A foundation need not provide standalone user value: its separate completion
must enable already-authorized downstream work to proceed, not merely anticipate
a possible future consumer. Every group keeps verification and review sufficient
for its entire combined scope. Use these criteria in the existing pass, without
scores, token estimates, recorded split reasons, or extra confirmation steps.

For example, a JSON contract needed for an approved consumer team's early start
may remain separate from its later CLI consumer. If both only complete one
feature with no separate decision or use, their different tests do not justify
separate Tasks. A fixture-only fragment Merges into its concrete consumer. A
tutorial with a separately scheduled handoff can remain independent even when
it shares files or checks with the feature.

### Registration, Contract, And Ordering

Register only explicit user-approved work. Register a finalized multiple-Task
set once with `task add --from-stdin`, using explicit common values as described
in [CLI contracts](cli_contracts.md#task-add); single-Task flags remain available.
Registration grants no implementation, target-project, Git,
network, or external-operation permission. A non-zero Contract copies only
explicit scope, acceptance, constraints, and authority reference.
For initial reference examples and the reserved Task-ID form, see
[Task Contract](#task-contract).

When registration **and immediate implementation** are already authorized and
the Task can start without bypassing existing selection or predecessor order,
record that decision with `task add --status in_progress` (or the item's
`status` in structured input). Otherwise retain the appropriate initial state,
normally `ready`. Do not mark new work active to displace an existing active
Task or an earlier selected ready candidate. Registration-only authority,
`selection=none`, and omitted candidate rows do not establish start permission
or absence of competing work; paused/blocked rows alone do not prevent unrelated
ready work. Use the registration response's ready `data.context_preparation.context`
for the selected Task's full Contract and current gates, without another context
read or a separate start edit for an already active Task. Use its selected ID,
which need not be a newly registered Task. If preparation failed, registration
still committed: recover with `task context`, not another add. See the exact
[registration result](cli_contracts.md#task-add). Neither initial status nor
context selection authorizes the work.

When the outcome and registration permission are clear but the authority lacks
truthful split or complete Contract detail, register one whole-outcome
revision-zero Task. Ask one grouped question containing all and only the missing
facts, with no partial write, only when user-mandated separate boundaries cannot
be represented truthfully or the outcome or permission boundary is too unclear
for one honest whole-outcome Task. Missing size, implementation, path, or test
detail alone does not cross that sole question boundary.

When binding authority requires a design decision first, register only the
design responsibility unless the same authority already states truthful ordered
design and implementation Contracts. Register later implementation only in a
later explicit event based on the produced design authority. Every final Task
retains its own locally attributable verification and review; a later
integration review never excuses a deficient slice.

For a bounded non-mechanical contributor-guide update with no protected delta,
Tier 1, explicit registration and immediate-work permission, and all earlier
Tasks in its lane complete, an eligible registration may be:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task add --repo <target-project> --title "Clarify localized contributor guide" --kind sequential --lane TG-EXAMPLE --order 20 --priority normal --status in_progress --review-tier 1 --json
```

Do not invent dependency graphs or import a project plan. Initial `done` and
initial `paused` are rejected. For an initially blocked task, supply
`--blocked-reason`. Let the deterministic CLI fill omitted sequential
lane/order fields and return them.

### Review Tier Selection

A binding authority mapping is the minimum Review Tier for its governed scope.
Ordinary registration or splitting cannot lower that floor. With no binding
mapping, use this closed fallback:

| Scope delta | Review Tier floor |
|---|---|
| Schema or migration; JSON contract; CLI write behavior; target-project mutation; privacy or logging; Skill trigger; verification, review, or completion gate; milestone or plan acceptance; implementation-binding normative documentation | Tier 2 |
| Wholly mechanical and meaning-preserving work | Tier 0 |
| Every other bounded scope | Tier 1 |

An explicitly authorized higher Tier is allowed. Unknown facts, size,
difficulty, duration, failure count, safety wording, or reviewer availability
never alone selects Tier 2. Splitting cannot lower a Task's applicable floor,
and a sibling Task's Tier does not propagate. Every final Task must satisfy its
own gate rather than relying on later integration review.

### Explicit Mid-Task Scope Addition

Apply the same completion-purpose and Merge criteria once to the addition and
its relationship to the current responsibility, then choose one disposition:

- `keep-current`: already-covered scope makes no Contract write and preserves
  the current Tier;
- revise or Merge into current: use the maximum of the current Tier and every
  resulting floor. When higher, raise the Tier first and then make the semantic
  Contract revision; never auto-lower or advance review between those writes;
- successor: use the successor's own scope floor and existing lane/order.
  Register it only when explicit taskization or separate placement is present;
  otherwise make one write-free proposal; or
- Handoff: when truthful placement is not yet possible, use the existing
  bounded Handoff and then continue, pause, or block only under their existing
  conditions.

Moving already-covered work requires explicit repartition authority. An
unrepresentable order creates no implicit dependency. Scope preservation occurs
before any pause or block, and this guidance adds no normal-loop call. A
semantic Contract revision retains the existing target and evidence
invalidation effects; `keep-current` preserves them only under ordinary
exact-target rules.

### Partial-Add Recovery

A structured batch either registers every input or rolls back its entire set;
after a confirmed rollback, correct the invalid input and resubmit that explicit
set. A missing response is not a confirmed rollback: inspect the registered
Tasks first, preserve successes, and add only a proven missing remainder under
current authority. Never blindly replay the batch or delete successful Tasks.
No extra confirmation is needed after a successful mapped response.

If a strict subset of a multi-Task `task add` sequence succeeds, stop and read
the exact registered set. During the same uninterrupted event, compare it with
the still-transient final set, preserve successful additions and the authorized
remainder, then add only groups proven missing after the ordinary failure is
resolved. Do not delete, duplicate, repartition, batch-retry, or rerun
Select-Split-Merge.

If interruption loses the transient final set, do not reconstruct or rerun it.
Use existing Handoff only for a truthful bounded remainder summary. Any later
write requires current explicit authority; use the sole grouped question when
the exact remainder or permission is unclear, or one confirmed revision-zero
remainder Task when both are clear.

## Safety Boundary

- Keep secrets, tokens, authorization data, raw stdout/stderr, stack traces,
  environment dumps, private prompts/reasoning, full chats/reviews, and large
  diffs out of local task inputs.
- Do not submit `dispatch_authorization=<value>` or a
  `"dispatch_authorization":<value>` JSON key. Both are rejected for new
  input. Use neutral `operation_sequence` only as non-authorizing correlation
  or idempotency evidence.
- Use concise summaries, reasons, notes, checkpoints, findings, and receipts.
- Do not let inspection authorize target-file, Git, Issue, PR, network, or
  external-service mutation.
- Leave Evidence JSON, Viewer, and backup maintenance to setup and bounded
  same-process post-commit processing. Do not make an LLM choose, schedule, or
  monitor it.
