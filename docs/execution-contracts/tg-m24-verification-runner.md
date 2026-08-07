# TG-M24 Verification Runner Conditional Execution Contract

<a id="tg-m24-verification-runner"></a>

> [!IMPORTANT]
> CONDITIONAL FORMAL AUTHORITY — ACCEPTED BUT INACTIVE. Load this document only
> when the current Task Contract or [authority index](../authority.md) routes to
> TG-M24. It does not activate a Runner, command execution, schema, CLI, Skill
> behavior, completion gate, network use, credential use, or target mutation.

The active [specification](../specification.md) and [design](../design.md) own
supported behavior. This document is the sole detailed owner of the accepted
inactive units' purpose, scope, order, dependencies, permission boundaries,
and gates below. Root [plan.md](../../plan.md) owns cross-sequence gateways,
current decisions, open issues, and static contracts not delegated here. Its
TG-DOC gateway routes here instead of duplicating this canonical table. The
Task database owns live state and evidence.

## Sequence Boundary

TG-M24 is sequential Tier 2 work in lane `TG-M24-VERIFICATION-RUNNER`. It
remains inactive and begins only after accepted TG-DOC.2:

| Unit/order | Task | Dependency | Purpose, permission boundary, and completion gate |
|---|---|---|---|
| TG-M24.1 / 10 | `tg_task_29aa63124900ad95` | accepted TG-DOC.2 | Design project-owned verification plans, shell-free argv, exact Task/Contract/expectation/target binding, environment/network/mutation/resource policy, sanitized runner-observed evidence, shadow-to-gate staging, and the M21 fallback. Activate no Runner, schema, CLI, Skill, gate, network, credential, or target mutation. Require exact documentation checks and diff, a current Receipt, and two independent Tier 2 reviews. |
| TG-M24.2 / 20 | `tg_task_fafad7bc62df7576` | accepted TG-M24.1 | Implement only the approved bounded Runner and append-only evidence in shadow mode; existing M21 and completion gates remain unchanged. Execute only an explicit current project-owned plan, and require separate exact authority for any live external-project run. Require migration, safety, package, focused/full offline checks, exact diff, a current Receipt, and two Tier 2 reviews. |
| TG-M24.3 / 30 | `tg_task_dc015144091f8e60` | accepted TG-M24.2 | Make one qualifying exact-current complete-plan Runner result an explicit versioned completion basis while retaining the M21 caller-attested Receipt for unsupported, manual, visual, external, or unavailable-Runner cases. Analyzer output, arbitrary commands, and new normal-loop LLM leaves gain no gate authority. Require full offline, package/release consistency, exact diff, a current Receipt, and two independent Tier 2 reviews. |
| TG-M24.4 / 40 | `tg_task_f81f2d126f033a59` | accepted TG-M24.3 | Accept Runner safety, provenance, the completion gate, M21 fallback, Evidence Bundle/JSON, Analyzer coexistence, legacy/history, and realistic supported/unsupported flows, with bounded corrections inside M24.1 only. Runs outside approved fixtures or this repository require separate exact project authority. Require focused/full and authorized forward checks, exact diff, a current Receipt, and two independent Tier 2 reviews. |

The sequence never lets an LLM choose project tests or arbitrary commands.
Only an explicit current project-owned verification plan may authorize bounded
shell-free argv against its exact Task, Contract, verification expectation,
plan, target, and generation. Authority, policy, or basis drift fails closed.
Environment, working directory, sandbox, network, mutation, resource, timeout,
cancellation, crash, concurrency, retry, and output retention are deny-by-
default and must preserve honest provenance limits.

Runner-observed evidence and the M21 caller-attested `pass/full` Receipt remain
distinct and are never relabeled or upgraded. Shadow observation precedes gate
authority. M21 remains an auditable fallback for unsupported, manual, visual,
external, or unavailable-Runner verification. Derived M23 analysis never gains
gate authority. The normal main-LLM loop must lose decisions, not gain a leaf or
command.

<a id="tg-m24-1"></a>

## TG-M24.1 Design

Task `tg_task_29aa63124900ad95` is documentation/design only. It may freeze
project-owned verification-plan authority, approved
shell-free argv resolution, sandbox and working-directory policy, exact basis
binding, lifecycle and resource controls, sanitized result and digest capture,
idempotency, crash/concurrency recovery, Runner producer and assurance
semantics, completion-cycle evolution, and the exact M24.2 shadow, M24.3 gate
integration/fallback, and M24.4 acceptance split.

The design must distinguish Runner-observed facts from M21 caller attestations,
retain the manual fallback, stage shadow evidence before gate eligibility,
preserve legacy completion meaning, deny prohibited output, and state what the
Runner cannot prove about identity, environment authenticity, external
effects, or test quality. It may not import a project test strategy into
taskgov or store command bodies, raw outputs, logs, environments, prompts,
chats, reasoning, secrets, credentials, or unbounded diffs.

This unit activates no Runner, execution, schema, gate, public CLI, Skill,
network, credential, or target mutation. Completion requires the complete
authority/allow-list, exact basis, policy, lifecycle, privacy, provenance,
shadow/fallback/history, and zero-added-main-LLM-decision design; a bounded
implementation/acceptance split; documentation checks; exact diff and current
Verification Receipt; and two independent Tier 2 reviews with no unresolved
High or Medium finding.

<a id="tg-m24-2"></a>

## TG-M24.2 Shadow Runner And Evidence Capture

Task `tg_task_fafad7bc62df7576` may implement only the exact design accepted in
TG-M24.1: the plan resolver, shell-free process and
policy boundary, execution lifecycle, append-only Runner-observed records,
deterministic scheduling or collection outside the main LLM loop, bounded
diagnostic status, and Evidence Bundle linkage.

It operates strictly in shadow mode. Existing M21 Receipt and completion gates
remain authoritative; a Runner result can neither satisfy nor block completion
or mutate Task, Review, M21, or gate state. Outcomes for pass, fail, timeout,
cancellation, crash, retry, duplication, and concurrent drift must be
append-only, sanitized, provenance-labeled, idempotent, and exact-basis bound.
Live external-project execution requires its own exact project authority.

Completion requires fail-closed plan/policy/basis drift, no shell, implicit
network, uncontrolled mutation, raw output, credentials, arbitrary/model-based
command choice, Viewer UI, or added main-LLM decision; plus migration, safety,
privacy, temporary-project, Evidence Bundle, package, focused/full offline
checks, exact diff, a current Verification Receipt, and two independent Tier 2
reviews with no unresolved High or Medium finding.

<a id="tg-m24-3"></a>

## TG-M24.3 Gate Integration And M21 Fallback

Task `tg_task_dc015144091f8e60` starts only after the accepted shadow target. It
may add the exact TG-M24.1 versioned completion-basis
discriminator and deterministic eligibility evaluator so one qualifying
exact-current Runner-observed result for the complete approved plan may satisfy
the verification evidence gate.

The selected basis and precedence/fallback rule must be explicit in Task reads,
Evidence Bundles, completion cycles, concise Skill/reference flow, migration,
Viewer compatibility, and package contracts. Target, Contract, expectation,
plan, or Runner change invalidates only the specified current basis. Historical
cycles preserve the gate version actually used. Supported Runner use removes
the main-LLM Receipt action without adding another LLM command; M21 remains
usable and auditable for unsupported/manual/visual/external/unavailable cases.

Completion requires exact-current qualification and plan completeness;
fail/timeout/cancel blocking and fresh-run recovery; invalidation, concurrency,
legacy migration/reopen and history correctness; honest M21 fallback; no
assurance relabeling, synthesized old evidence, Analyzer gate, arbitrary test
choice, new normal-loop leaf, weakened Review/completion gate, or unrelated
redesign; plus full offline, privacy, Viewer, package/release, Skill call-count,
exact-diff checks, a current Verification Receipt, and two independent Tier 2
reviews with no unresolved High or Medium finding.

<a id="tg-m24-4"></a>

## TG-M24.4 Integrated Acceptance

Task `tg_task_f81f2d126f033a59` accepts the exact M24.2 Runner and M24.3 gate
target across project-plan authority, process safety,
provenance, Evidence Ledger/JSON, M23 coexistence, completion history, legacy,
M21 fallback, package/release consistency, and realistic supported and
unsupported verification flows. Only bounded corrections required by TG-M24.1
are allowed.

Realistic supported tasks must complete from exact project-owned plans without
a manual Receipt action or added LLM command. Failures, stale bases, crashes,
duplicates, and concurrency must recover without false success. Unsupported or
manual work must use the honest M21 fallback. Old and new completion histories
remain distinguishable, and Bundles/reports preserve assurance classes.

Completion requires approved-plan pass/fail/timeout/cancel; stale Task,
Contract, expectation and plan; crash/restart and concurrent worker; resource,
environment, network and mutation denial; privacy/output limits; legacy
migration/reopen; fallback; Analyzer coexistence; Bundle/JSON; Viewer; Skill
call counts; package/release; focused/full offline and authorized realistic
forward checks; exact diff; a current Verification Receipt; and two independent
Tier 2 reviews with no unresolved High or Medium finding. Execution outside
bounded fixtures or this repository requires separate exact project authority.

## Deferred Detail Rule

TG-M24.1 owns the exact schema, storage, plan format, module/process layout,
public projection, and compatibility changes needed by later units. This
pre-design contract deliberately selects no schema number, table, CLI leaf, or
Viewer detail. Semantic expansion, arbitrary command/model choice, raw-output
retention, Analyzer gate authority, fallback removal, external execution,
publication, push, tag, or Release needs separate explicit authority.
