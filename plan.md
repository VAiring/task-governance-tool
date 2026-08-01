# task-governance-tool Current Decisions And Open Issues

Status: v0.10.0 is published from exact commit
`a9b80ce177a6dead10d51a070b76ff01f7af0294`. Remote `main` and the
lightweight tag `v0.10.0` resolve to that commit. GitHub Release
`362617903` has prerelease visibility and contains the accepted archive and
checksum. TG-M19.0 through TG-M19.10, including TG-M19.6A and TG-M19.6B, are
complete. TG-M19.11 through TG-M19.14 are also complete. TG-M20.1 through
TG-M20.5 form the approved operational-baseline sequence. TG-M20.1 is
complete at `a77afbe0140fef416cceeee529e9ff2c985a8e4d`, TG-M20.2 is
complete at `e49e5aca68a7bf1c9829afb50d2c6a38835a4f03`, TG-M20.3 is
complete at `800ed153dc9671f011ea4715f50d92ea464bc12b`, TG-M20.4 is
complete at `ed15a85b6d1c328a9d1ac9b6a1448b50c1389481`, and TG-M20.5 is
complete at `e5167e2d9d54493900b9d88672f1e53304cfa5b1`. TG-M21.1 is complete
at `fc2e0870ad9bf70830a082df168ad1992e07b51d`. The TG-M20S successor
observation has reached its frozen `proceed_to_design` result and no-rerun
retirement boundary. TG-M20S.3 now freezes an accepted but inactive Tier 2
decomposition design. TG-M21.1A through TG-M21.3 are
approved and registered behind their static predecessor chain. TG-M12.3
remains separately blocked; live status remains solely in the Task database.

This file owns current decisions, explicit open issues, and user-decision
gates. It is not the product contract, execution ledger, or evidence store:

- [`docs/specification.md`](docs/specification.md) owns product behavior.
- [`docs/design.md`](docs/design.md) owns implementation structure.
- [`docs/implementation-roadmap.md`](docs/implementation-roadmap.md) owns
  approved execution units, order, blockers, verification, review gates, and
  the concise completion index.
- The project-local Task database owns live Task, handoff, review, checkpoint,
  and completion state. This file does not mirror volatile handoff IDs or
  counts; inspect them through the public CLI.
- [`docs/history/README.md`](docs/history/README.md) is the sole historical
  index. Historical text never fills an active-contract gap or satisfies a
  current gate.

## Current Decisions

### Product And Authority Boundary

- The product remains a reusable, local-first Codex Skill plus deterministic
  Python CLI. A governed project's instructions, requirements, design, tests,
  and decision log remain authority over that project.
- The Skill name is `task-governance-tool`; the CLI is `taskgov`. Exact
  commands, arguments, envelopes, limits, and errors are defined only in the
  active specification and package references.
- Stateful use supports one physical project-scoped copy at
  `<target-project>/.agents/skills/task-governance-tool`. User-wide, symlink,
  junction, and competing project-scoped stateful layouts remain unsupported.
  This repository retains only the bounded development self-host exception in
  the active specification and design.
- SQLite is a generated local helper store, not project-decision authority.
  Databases, backups, Viewer output, sidecars, caches, and runtime state remain
  outside commits and release artifacts.
- Inspection is read-only by default. Target mutation, Git mutation, network
  use, Issue delivery, and external publication each require exact current
  authority for that operation.

### Daily Task Workflow

- `setup` remains the sole public initializer, migrator, maintenance opt-in,
  relocation-confirmation boundary, and canonical Viewer repair flow.
  `doctor` remains the sole read-only diagnostic and is not a normal-loop
  prerequisite.
- Task Skill owns purpose, scope, acceptance, current state, next work,
  blockers, local handoff, review/completion evidence, and acceptance-driven
  completion. It does not become an Issue tracker, semantic triage system,
  project-specific test strategy, or general workflow engine.
- Sequential order is lane-local. A blocked lane does not stop unrelated ready
  work; `paused` remains an intentional hold distinct from `blocked`.
- A Task Contract copies already-explicit authority. Semantic expansion needs
  later explicit authority and invalidates only the current projections
  defined by the specification.
- Out-of-scope discoveries use one local handoff operation regardless of
  adapter availability. The Task database—not this plan—owns their identifiers,
  count, delivery state, and lifecycle. A pending handoff never expands current
  acceptance or blocks an otherwise complete Task.
- Effort Advisory and reduced-loop reconciliation are deterministic,
  non-authoritative guidance. They add no normal-path question, persisted
  retry counter, automatic Task mutation, or unrelated-lane stop. Tests are
  never weakened merely to obtain a pass.

### Review, Completion, State, And Viewer

- Qualifying PASS receipts and changes-requested receipts are evaluated only
  for the current review target and generation. Any unresolved High or Medium
  finding from any recorded generation blocks completion. Distinct reviewer
  keys prove distinct stored strings, not people, machines, independence, or
  authenticated provenance; the trusted caller records results truthfully.
- Completion requires typed evidence. Reopen preserves append-only completion
  cycles but historical evidence never satisfies the new current gate.
- Durable project identity is separate from mutable filesystem binding. A path
  mismatch is not move/copy/fork intent; only explicit setup confirmation may
  advance a binding.
- The Viewer is a generated offline projection. Optional same-file reload and
  bounded one-shot UI-state handoff are presentation-only and add no Skill
  trigger, service, network action, or browser launch.
- Direct launch of the generated Viewer in the operating system's configured
  default browser remains a follow-up candidate. Its command/option, repair
  behavior, error contract, verification, and execution unit are undecided.

### Published Release Boundary

- The canonical v0.10.0 release identity is exact commit
  `a9b80ce177a6dead10d51a070b76ff01f7af0294`, lightweight tag `v0.10.0`,
  and GitHub Release `362617903` with prerelease visibility.
- Original copyrightable Omoronine-owned material in the reviewed tracked and
  shipped scope is licensed under Apache-2.0. Root and package `LICENSE`
  bytes are the same official text; no concrete `NOTICE` duty was identified.
- The accepted Release body, tag, archive, and checksum are immutable by
  project policy. Defects use a reviewed forward-fix candidate and new version,
  not history rewrite, retag, asset replacement, or Release deletion.
- Completed M19 approval objects and exact gate evidence authorize no future
  write. Any future release, push, tag, Release, or CI dispatch needs its own
  exact current authority.
- Exact artifact identities and install/upgrade boundaries live in
  [`docs/release-install.md`](docs/release-install.md); the active roadmap
  owns completion evidence routing and post-release execution order.

### Repository Verification Policy

- The repository-only deterministic runner owns three exhaustive,
  module-level base lanes: `fast`, `integration`, and `release`. `all` is the
  unchanged standard-discovery suite, not another maintained inventory.
  Missing, duplicate, unassigned, stale, or loader-failed test material stops
  before execution.
- Pull requests run the complete partition on Python 3.12 and `fast` on 3.14;
  pushes to `main` run every base lane on both versions; manual
  `workflow_dispatch` runs `all` on both versions.
- A future release candidate requires the explicit aggregate manual gate after
  policy validation and both full-version jobs. This repository CI policy
  grants no push, dispatch, tag, Release, or other external mutation
  authority.

### Release Vocabulary And Legacy Read Boundary

- New caller input uses `operation_sequence=<positive canonical integer>` only
  as neutral correlation or idempotency evidence. It never grants authority,
  and current approval for an external operation remains separate.
- Exact stored TG-M19.7 `dispatch_authorization` counter forms remain readable
  only in legacy Contract constraints and checkpoint summaries. The reader
  preserves their original bytes and grants no write, dispatch, or other
  authority; all new input and completion-history public text use the normal
  strict privacy boundary.
- Omitting constraints during an otherwise valid Contract revision continues
  to carry forward the already-validated prior constraints bytes, including
  bounded legacy lineage. This is preservation, not acceptance of caller-
  supplied legacy vocabulary.

### M20 Operational-Baseline Boundary

- TG-M20 observes exact product baseline
  `43c91d5987b0c35c66f834789aea782e98dcaff7`. The TG-M20.1 completion commit
  owns the observation contract but does not replace or modify that product
  baseline.
- Baseline collection precedes any Skill behavior change. M20 adds no product
  telemetry, public command, schema/Viewer field, normal-loop call,
  Verification Receipt, test-strategy engine, Task-splitting operation, or
  parent/child Task model.
- Evidence remains explicitly stratified as `machine_observed`,
  `historically_reconstructed`, or `observer_attested`. It is study material,
  not authority, and cannot satisfy verification, review, completion, release,
  or product-acceptance gates.
- TG-M21 Verification Receipts, a small Skill-only verification-
  proportionality guardrail, and bounded user-approved Task decomposition are
  three separate decisions. None is bundled into another. TG-M21.1 completed
  its inactive design; the M21 implementation sequence is now separately
  approved and registered. The bounded decomposition successor is separately
  approved and accepted as TG-M20S.3 inactive design authority, not current
  product behavior.
- One contaminated incident, an unreconstructable historical absence, timing
  or size alone, and the context-rich parent conversation cannot establish
  causation or justify a positive decision.
- The reviewed stratified aggregate, exact denominators, exclusions,
  limitations, and retired observation details are preserved only as
  [non-authoritative TG-M20 history](docs/history/v0.10.0/m20-operational-baseline.md).

### M20 Recorded Decisions

TG-M20.5 applied the frozen rules to the fixed M20.2-M20.4 corpus. The
detailed aggregate, arithmetic, exclusions, and decision basis are
preserved in
[non-authoritative study history](docs/history/v0.10.0/m20-operational-baseline.md);
they are no longer an active collection or rerun contract.

| Candidate | Decision | Current boundary |
|---|---|---|
| TG-M21 Verification Receipts | `proceed_to_design` | Two eligible M20.3 bundles supported all five material facts: `command_label`, `result`, `source_revision`, `duration`, and `scope_coverage`. |
| Skill-only proportional-verification guardrail | `observe_more` | 0 bundles satisfied both the observer pattern and frozen machine mapping; one of three planned bundles was permanently excluded. |
| Bounded user-approved Task decomposition | `observe_more` | The four-pair denominator produced `E=1,Q=1,U=3`; the separate Handoff control was eligible. |
| Bounded further observation | `proceed_to_design` | M20 itself registered no Task; a later explicit user decision separately registered the now-closed TG-M20S study. |

The M20 decision for TG-M21 by itself authorized only the smallest separately
reviewed design proposal for a bounded sanitized receipt. It did not select
storage, schema, CLI, Viewer, or Skill behavior; later implementation authority
comes only from the separate registered Task Contracts below.

A later verification-guardrail observation proposal may use
`vp_cli_parser_followup`, `vp_viewer_contract_followup`, and
`vp_migration_contract_followup`, one attempt per scenario with no rerun. Its
approved contract must define a successor denominator because the original
excluded CLI bundle cannot be made eligible. It should stop at the first
satisfied successor positive/negative rule or fixed-inventory exhaustion; two
qualifying bundles remain necessary for positive support.

The completed decomposition successor decision is recorded below and in its
non-authoritative history. The verification-guardrail inventory remains a
proposal, not execution authority, and still requires explicit user approval.
Timing, size, line, and test-count values remain supporting context only. The
context-rich parent conversation and earlier incident remain excluded
samples. A positive decision never authorizes behavior activation.

### TG-M20S Recorded Successor Decision

The separately approved TG-M20S study has a closed terminal result. It used
Task `tg_task_ddfbf721eced8c58`, Contract revision 1,
`conversation_decision:2026-08-01:interrupt-successor-task-decomposition-observation`,
baseline `43c91d5987b0c35c66f834789aea782e98dcaff7`, and package tree
`529abf7ac4e4ed778b383c90b6ac5f2fedc71615`.

Only `sp_user_expansion_alternate` launched. Both fresh arms were eligible,
the bounded episodes satisfied all four independence predicates, and the
bounded Contract-revision delta sum was zero versus one for the broad arm. The
pair qualified, moving `E=1,Q=1,U=3` to `E=2,Q=2,U=2` and selecting
`proceed_to_design`. The two later fixed pairs were not launched.

The result itself supported considering a separately approved Tier 2 design
for bounded user-approved Task decomposition and did not automatically
register one. Evidence is limited to one repository, scenario, pair, and
model/tool cohort; it does not establish a general causal effect or
authenticated reviewer independence. The no-rerun receipt and
[non-authoritative history](docs/history/v0.10.0/m20s-task-decomposition.md)
preserve the bounded decision and limitations after temporary assets and the
reduced corpus are retired.

### TG-M20S.3 Accepted Inactive Decomposition Design

The later explicit user decision registered TG-M20S.3, Task
`tg_task_286129dbca4d25ab`, in lane `TG-M20S-TASK-DECOMPOSITION` at order 30,
dependent on accepted TG-M20S.2. It is a Tier 2 design-only unit for two
explicit authority events: a request to register or taskize authorized work,
and an explicit scope addition to an in-progress or review-pending Task.

The accepted design uses independently acceptable, verifiable, committable,
and completable scope to distinguish keep-current, revise-current,
propose-successor, handoff, pause, and block. Explicit taskization permits one
non-recursive bounded multi-Task registration pass when the candidates map
one-to-one to accepted outcomes and scope, order, and permissions are
unchanged. A proposed mid-Task successor still requires explicit user approval
before registration, and only one proposal is allowed per explicit addition.
Unknown or negative independence forbids decomposition rather than generating
more questions; exact whole-scope registration or current-Contract revision is
the safe fallback when authorized.

Design acceptance activates no current behavior and adds no automatic Task
creation, recursive or size-only split, command, schema, Viewer field,
parent/child model, background LLM work, network use, target-project mutation,
or implementation Task. In-scope discovery and cross-module failure remain
outside until separately supported. The exact Task DB record owns live state.

### TG-M21.1 Approved Inactive Design Decisions

TG-M21.1 is design authority only. The current product remains schema v16 with
20 command leaves, Boolean completion attestation, no Verification Receipt
write, and the existing Skill call order until a later implementation and
synchronization gate completes. This Verification Receipt design is therefore
an inactive acceptance boundary, not current behavior.

The smallest selected contract is:

- at most one append-only caller-attested aggregate Receipt per exact target
  generation; a failed, timed-out, or partial result requires a fresh target
  generation before another attempt;
- exactly five material fact classes: `command_label`, `result`,
  `source_revision`, `duration`, and `scope_coverage`; stable ownership IDs,
  current Contract revision, a verification-expectation digest, and recording
  time are structural binding metadata rather than extra observed facts;
- `source_revision` is the exact existing review-target kind, value, base, and
  generation copied by taskgov, never a second free-form target;
- Receipt add requires the target-set result's generation as an explicit
  optimistic concurrency guard, so a target, Contract, or verification change
  between the external run and recording fails stale instead of rebinding the
  result to untested material;
- `result` is the closed `pass`, `fail`, or `timeout` value;
- `scope_coverage` is the closed `full` or `partial` caller attestation, where
  `full` refers only to the complete current Task `verification` text and does
  not let taskgov choose or judge project tests;
- the existing `--verification-complete` remains required. For a nonempty
  verification expectation, completion additionally requires the unique
  exact-current Receipt to be `pass/full`. Any other current result is retired
  only by a fresh target generation, including an explicitly repeated
  identical target;
- changing the verification expectation after a target exists invalidates the
  target generation just as a Contract revision does, so both verification
  and review evidence must be fresh;
- schema v17 gives completion cycles an internal basis version, expectation
  digest, and nullable Receipt link: migrated cycles are version 0/null/null,
  while every new native cycle is version 1 with the exact expectation digest
  and links the qualifying Receipt whenever its verification expectation is
  nonempty. The sole existing unknown-coverage done/no-cycle reopen bridge may
  still create its exact partial legacy version-0/null/null cycle. This
  distinguishes honest legacy absence from
  corrupt post-activation absence without expanding public completion history;
  existing attestations, events, and M20 observations receive no synthesized
  Receipt or strengthened history;
- one future bounded `verification receipt add` write and one additive bounded
  `task show.verification_evidence` projection are sufficient. There is no
  list/import/export surface and no initial Viewer Receipt projection; Viewer
  work is limited to accepting the new source schema while keeping its
  existing snapshot content;
- taskgov does not execute commands, store a command body, exit code, stream,
  log, environment, exception, prompt, diff, credential, or arbitrary coverage
  prose, and does not prove result truth, provenance, coverage quality, or test
  proportionality.

This initial design intentionally omits an approved-exception Receipt,
structured required-label set, command orchestration, result-file import,
Viewer receipt detail, authentication/signatures, the separate Skill
proportionality guardrail, and Task decomposition.

The implementation sequence is approved and registered but remains inactive
until the authority-layout predecessors complete:

1. **TG-M21.2 atomic vertical activation:** add schema v17 and its immutable
   Receipt storage, repository/gate services, Viewer source compatibility, the
   one write leaf, bounded Task-show projection, completion/check gate,
   expectation invalidation, versioned completion-cycle Receipt linkage,
   backup-only maintenance behavior, and synchronized concise Skill/reference
   guidance in one exact reviewed activation. Schema v17 must not become
   reachable before all of those behaviors are coherent.
2. **TG-M21.3 integrated acceptance:** exercise legacy migration, current and
   stale target/Contract/expectation cases, pass/fail/timeout recovery, privacy,
   no-write reads, completion history, Viewer compatibility, bounded call
   counts, package/release consistency, and realistic forward tests before
   declaring M21 complete.

TG-M21.1A (`tg_task_a6f5ec3147440e53`) first designs the authority-layout
retirement after accepted TG-M20S.2, and TG-M21.1B
(`tg_task_8e30cf88c9018824`) performs the reviewed atomic switch before
TG-M21.2. The registered activation and acceptance Tasks are respectively
`tg_task_2f6fd712dd83f250` and `tg_task_a42cb5d0383980bd`. Registration alone
does not activate the inactive design; a semantic change or expansion still
requires separate authority.

## Registered Work, Static Gates, And User Decisions

| Unit | Task | Current gate |
|---|---|---|
| TG-M20S.1 | `tg_task_ddfbf721eced8c58` | Protocol and harness freeze completed at `33948e3dd1c805e04eda0873c764dad15363175d`. |
| TG-M20S.2 | `tg_task_e591f30d546ba69e` | Closed study; `proceed_to_design`; Task DB owns exact review and completion evidence. |
| TG-M20S.3 | `tg_task_286129dbca4d25ab` | Accepted inactive design; Task DB owns exact review and completion state. |
| TG-M21.1A | `tg_task_a6f5ec3147440e53` | Statically depends on accepted TG-M20S.2; then proceeds without another decision. |
| TG-M21.1B | `tg_task_8e30cf88c9018824` | Depends on accepted TG-M21.1A. |
| TG-M21.2 | `tg_task_2f6fd712dd83f250` | Depends on accepted TG-M21.1B; remains inactive until atomic activation. |
| TG-M21.3 | `tg_task_a42cb5d0383980bd` | Depends on accepted TG-M21.2; owns integrated M21 acceptance. |
| TG-M12.3 | `tg_task_1f7503aca5e32cdc` | Blocked until a separately approved versioned Issue Skill intake contract, governing permission update, and explicit integration approval exist. |

The approved TG-M20.1-TG-M20.5 sequence and TG-M21.1 design are complete.
TG-M20S has its closed terminal decision, and TG-M20S.3 freezes accepted but
inactive design authority. The registered M21 follow-up
chain may proceed in the order above without another decision. This table
records static gates only; the Task database owns current, ready, blocked,
review, and completion state.
A materially different write, external operation, scope expansion, or changed
acceptance still requires explicit authority.

## Open Issues And Deferred Candidates

These items are not implementation authority. Each needs a separately approved
contract and execution unit.

- After accepted TG-M20S.3, decide separately whether to approve a bounded
  activation unit. Never register or activate implementation as a design side
  effect.
- Decide separately whether to approve the still-proposed verification-
  guardrail successor inventory before reconsidering that Skill-only guidance.
- Decide whether later product scope should add project-profile detection,
  dependency graphs, or Git integration beyond the current read-only snapshot,
  completion validation, and bounded Review Packet.
- Design the approved follow-up default-browser launch boundary described
  above.
- Reassess stale warnings and event-history or current/list pagination only
  after operational evidence shows the current bounded checkpoint and
  projections are insufficient. Checklist/child execution units remain
  inactive pending a separately approved successor observation or design.
- Revisit a once-daily GitHub update check only as a separately approved,
  opt-in local-cache/network feature. Normal Skill use remains offline and
  must not contact GitHub.
- Before TG-M12.3, define the Issue Skill's exact versioned local intake and
  transport contract. Keep semantic duplicate/recurrence handling, handoff
  paging/retention, multiple receivers, Issue import/sync/priority/triage,
  resulting-Task creation, advanced risk/fixture analysis, signed evidence,
  and child-task structure outside the current Task Skill core.

## Reference Material

- `references/KuraKoma_TASK_STATUS.md` is a copied example from another
  project. It is non-authoritative and is not current status or implementation
  order.
- Historical planning narratives, release-stage execution contracts, and
  superseded forward-test evidence are discoverable only through
  [`docs/history/README.md`](docs/history/README.md).
