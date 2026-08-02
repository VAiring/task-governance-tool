# task-governance-tool Current Decisions And Open Issues

Decision baseline: v0.10.0 is published from exact commit
`a9b80ce177a6dead10d51a070b76ff01f7af0294`; remote `main` and lightweight
tag `v0.10.0` resolve to that commit, and GitHub Release `362617903` has
prerelease visibility. The M20 studies selected the accepted but inactive
TG-M20S.3 decomposition design and the now-current TG-M21 Verification Receipt
contract. The approved M21 activation/acceptance sequence, the accepted but
inactive TG-M21.4 Verification Subject and TG-M21.4A capacity corrections, the
accepted but inactive TG-M22 Evidence Ledger design and activation sequence, and the revision-zero
TG-M12.3 prerequisites are retained below. The Task database,
queried through the public CLI, is the sole owner of live execution status and evidence.
The current unpublished local candidate identity is v0.11.0/schema v17/Viewer
snapshot v4 with source compatibility v5-v17 and 21 public command leaves; it
claims no tag, remote commit, or published Release.

This file owns current decisions, explicit open issues, and user-decision
gates. It is not the product contract, execution ledger, or evidence store:

- [`docs/specification.md`](docs/specification.md) owns product behavior.
- [`docs/design.md`](docs/design.md) owns implementation structure.
- This plan owns approved static execution purpose, scope, dependency order,
  permission boundaries, verification/review/completion gates, current
  decisions, and open issues. It is not a progress table.
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
  [`docs/release-install.md`](docs/release-install.md). This plan owns only
  approved static execution gates; the Task database owns live completion
  evidence and [`docs/history/README.md`](docs/history/README.md) indexes
  completed lineage.

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
  three separate decisions. None is bundled into another. TG-M21.1 froze the
  Receipt design and the atomic TG-M21.2 boundary activates it as current
  post-publication product behavior; TG-M21.3 is its exact-target acceptance
  unit. The bounded decomposition successor is separately approved and
  accepted as TG-M20S.3 inactive design authority, not current product
  behavior.
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

### TG-M21 Current Verification Receipt Decision

The current unpublished v0.11.0 candidate contract is schema v17 with 21 command
leaves, the bounded caller-attested Verification Receipt write/readiness gate,
versioned completion-cycle Receipt linkage, Viewer snapshot-v4 source
compatibility through v17, and the synchronized Skill call order. This current
contract neither rewrites the immutable v0.10.0 publication nor authorizes
publishing, tagging, or pushing the candidate.

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
- one bounded `verification receipt add` write and one additive bounded
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

The retained static sequence has one atomic activation boundary and one
exact-target acceptance boundary; the Task database owns live state:

1. **TG-M21.2 atomic vertical activation:** own schema v17 and its immutable
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

### Current Authority Layout And Approved M21 Sequence

TG-M21.1A `tg_task_a6f5ec3147440e53` and TG-M21.1B
`tg_task_8e30cf88c9018824` established the current authority layout without
changing product behavior. Product behavior remains in the specification,
implementation structure in the design, durable agent rules in `AGENTS.md`,
and approved static execution contracts in this plan. The project-local Task
database, inspected through the public CLI, owns current, ready, blocked,
review, and completion state and evidence but is not project-decision
authority.

All four M21 units are sequential Tier 2 work in lane
`TG-M21-VERIFICATION-RECEIPTS`. Their retained static contracts are:

| Unit/order | Task | Dependency | Purpose, bounded scope, permission, and completion gate |
|---|---|---|---|
| TG-M21.1A / 12 | `tg_task_a6f5ec3147440e53` | accepted TG-M20S.2 | Design the ownership map in `plan.md` plus documentation validation only; switch no routing or behavior; require inventory checks and two Tier 2 reviews. |
| TG-M21.1B / 14 | `tg_task_8e30cf88c9018824` | accepted TG-M21.1A | Use the user's physical-deletion authority for the closed repository inventory only; atomically preserve positive authority and final history without a progress mirror; require self-host/focused/full offline checks and two Tier 2 reviews. |
| TG-M21.2 / 20 | `tg_task_2f6fd712dd83f250` | accepted TG-M21.1B | Atomically activate the exact schema-v17 Receipt storage, gate, CLI/show, cycle-link, Viewer-compatibility, Skill/reference, package, documentation, and test contract in the specification/design; execute no project command or network/external mutation; require focused/full/release checks and two Tier 2 reviews. |
| TG-M21.3 / 30 | `tg_task_a42cb5d0383980bd` | accepted TG-M21.2 | Accept that exact activation with migration, privacy, concurrency, stale/current, recovery, package, release, and realistic offline scenarios plus bounded fixes only; add no redesign or new surface; require the full suite and two Tier 2 reviews. |

The final retired source body is indexed as
[non-authoritative roadmap history](docs/history/v0.10.0/roadmap-retirement/implementation-roadmap.md),
captured from exact commit `af5e19545e4f5b59817c70fbc5e2763c0dbf2e1e`.
It supplies lineage only and never fills an active-contract gap or satisfies a
current gate. Completed execution details, current/ready/blocked/review/done
state, blocker detail, targets, evidence, and completion commits are queried
through the public CLI rather than mirrored in Git documents.

### TG-M21.4 And TG-M21.4A Accepted Inactive Corrections

TG-M21.4 `tg_task_6ae822dd1a77c095` is a Tier 2 design-only interrupt after accepted TG-M22.1 and before schema-v18 activation.
Current schema-v17 Receipts, caller-authored `command_label` bytes, linked cycles, CLI, Skill, and Task-show projection remain unchanged.
Schema v18 instead derives an immutable taskgov-owned subject from each fresh target's nonempty verification criterion.
New input retains result, duration, coverage, and expected generation but drops label composition; v17 rows remain explicit legacy labels and never enter a native M22 bundle.
TG-M21.4A `tg_task_95c5e968c8fe7e4b` keeps v17 storage/read/write at 500; makes M22.2 schema-v18 storage/read/internal processing 1,000 while public Task add/edit stays 500; and leaves M21.5 only the public-ingress change to 1,000.
Exact subject, same-schema compatibility, and capacity contracts live in the active specification and design.

### TG-M22 Accepted Inactive Evidence Ledger Decision

TG-M22 freezes a canonical local Evidence Ledger, one immutable bundle per future native completion cycle, and a fixed one-way DB-to-JSON projection.
Exact assurance/producer, criterion, manifest, bundle, legacy, repair, privacy, and future-producer contracts live in the specification/design; this plan owns only sequence and gates.

TG-M22.1 changes no behavior. TG-M21.4 corrects its inactive Receipt projection before TG-M22.2 advances v0.12.0 to schema v18 subject/ledger capture; TG-M22.3 retains v0.12.0 at schema v19.
All retain 21 leaves/call bounds; old rows/cycles remain legacy, caller attestations stay `bound_attestation`, and JSON remains a non-authoritative one-way projection.

### Approved TG-M22 Sequence

All seven units, including the three M21 additions, are sequential Tier 2 work in lane
`TG-M22-EVIDENCE-LEDGER`. Live status, targets, evidence, blockers, and
completion commits remain solely in the Task database.

| Unit/order | Task | Dependency | Purpose, bounded scope, permission, and completion gate |
|---|---|---|---|
| TG-M22.1 / 10 | `tg_task_0a3c0d361da10f49` | accepted TG-M21.3 | Freeze the Evidence Ledger, assurance/producer, authority/criterion, artifact, bundle, JSON, legacy, repair, and future-producer design in `plan.md`, `docs/specification.md`, and `docs/design.md` only. Change no runtime, schema, CLI, Skill, Viewer, package, generated artifact, or network behavior. Require active-document consistency, exact diff, current Verification Receipt, and two exact-target Tier 2 reviews. |
| TG-M21.4 / 15 | `tg_task_6ae822dd1a77c095` | accepted TG-M22.1 | Freeze the inactive tool-owned verification-subject correction, preserve v17 rows/cycles and behavior, allocate its v18/v19 transition, revise dependent Task Contracts, and require exact diff, current Receipt, and two reviews. |
| TG-M21.4A / 18 | `tg_task_95c5e968c8fe7e4b` | accepted TG-M21.4 | Correct schema-v18 capacity ownership in the three active documents and dependent Task Contracts only: M22.2 pre-admits 1,000-character durable/read/internal state but keeps public Task add/edit at 500; M21.5 changes only public ingress. Require current-v17 invariance, same-schema forward-data compatibility, exact diff, current Receipt, and two reviews. |
| TG-M22.2 / 20 | `tg_task_88bfe19eb6cffe2e` | accepted TG-M21.4A | Activate schema v18 authority snapshots, criteria, verification subjects, evidence references, deterministic manifests, and 1,000-character durable/read capacity while public Task add/edit remains 500. Preserve v17 Receipt/cycle bytes as subject-basis zero; fresh targets create basis one, Receipt input drops caller labels, and capture-v0 evidence writes fail closed. Preserve 21 leaves/call graph; add no bundle/JSON, analyzer, Runner, caller artifact list, raw content, or decision authority. Synchronize `AGENTS.md` retention, docs, Skill/reference, Viewer/package/migrations, split focused/full tests, record the exact compatibility package baseline, self-host retarget/reverification/review, exact-target Receipt, and two reviews. |
| TG-M21.5 / 25 | `tg_task_e7701fb907020905` | accepted TG-M22.2 | Raise only explicit public Task add/edit verification admission from 500 to 1,000 characters at schema v18; change no stored reader, DDL, migration, backfill, other field limit, subject, leaf, or call. Preserve exact bytes, privacy, output caps, and compatibility with the complete recorded M22.2 package; require focused/full tests, exact-target Receipt, and two reviews. |
| TG-M22.3 / 30 | `tg_task_ae6f52c4f7b25549` | accepted TG-M21.5 | Activate schema v19 native bundles, criterion links/finding snapshots, projection generation, subject-based Evidence JSON and index-last publication, setup repair, and bounded maintenance. Consume the M22.2-stored/M21.5-public 1,000-character boundary without changing it. Native output contains the tool-owned subject and caller-attested result/coverage but no legacy caller label. Forbid import, custom output, service/network, narrative, analyzer, Runner, Viewer evidence UI, raw content, new leaf, or new LLM call; require synchronized contracts, migration/recovery/full-offline/exact-diff Receipt, and two reviews. |
| TG-M22.4 / 40 | `tg_task_0a90b4caf566a8fd` | accepted TG-M22.3 | Accept M22.2/M21.5/M22.3 across the exact v17 500/500, M22.2 v18 500-public/1,000-stored, and M21.5 v18 1,000/1,000 matrix; unchanged legacy rows/cycles; safe M22.2 operation on post-M21.5 state; capture-v0 rejection; Task/Contract/target/verification/review/completion/reopen; Git artifacts; immutable bundles; DB/JSON equivalence; repair; privacy/size/no-write; command counts; local consumer; package/release; full offline; and forward scenarios. Permit only bounded corrections; add no M23/M24 activation, Viewer evidence UI, command, unrelated refactor, or weakened test. Require exact-target Receipt and two reviews. |

The sequence may advance only in that order. M22.2, M21.5, and M22.3 each land
a coherent supported intermediate state; the two schema-v18 revisions remain
forward-data compatible without a new marker, and none may expose behavior
whose Viewer compatibility, package inventory, or current formal contracts
are unsynchronized. M22 grants no publish, tag, Release, push, external-
service, or target-project mutation authority.

### TG-M12.3 Approved Static Contract

Task: `tg_task_1f7503aca5e32cdc`
Kind/lane/order: sequential / `SCOPE-CONTROL` / 40
Review tier: Tier 2
Task Contract: revision zero; this section is its positive static authority
Depends on: TG-M12.2, a separately approved versioned Issue Skill intake
contract, governing permission updates, and explicit user approval of the
integration boundary

Intended outcome:

- Connect existing and new pending handoffs through one future explicitly
  enabled, versioned local Issue intake boundary.
- Add claim/lease safety, fixed bounded retry, acknowledgement reconciliation,
  and deterministic due processing without importing Issue lifecycle into the
  Task Skill.

Write scope after every prerequisite is separately satisfied:

- one concrete local adapter and explicit project-scoped configuration;
- claim, acknowledgement, fixed retry, due processing, crash reconciliation,
  and bounded delivery-status projection;
- synchronized specification, design, Skill/reference, README, release, and
  offline integration tests; and
- no public command becomes active until its contract, implementation, and
  package synchronization complete.

Mandatory constraints:

- Do not start until the exact Issue intake/transport/version contract and
  governing permission update exist.
- Keep the current local handoff-recording workflow regardless of receiver
  presence. Pending delivery never expands the source Task's acceptance.
- Never open, initialize, migrate, or edit an Issue database directly; never
  use a shell, URL, network, GitHub, or arbitrary dynamic project code.
- Store only a bounded receiver acceptance receipt. Exclude semantic duplicate
  handling, priority, triage, resolution, resulting Task creation, Issue
  import/lifecycle sync, and reverse synchronization.
- Exact lease duration, batch bound, retry stages, and acknowledgement
  transport must come from the separately approved receiver contract.

Verification and completion gate:

- Cover absent, disabled, success, retryable-result stages independent of
  claim count, permanent, exhausted, pending drain, crash reconciliation,
  claim/withdraw race, receiver idempotency, permission, version, privacy, no
  shell/network, zero additional LLM decisions, and the full integration suite.
- Prove one receiver item under concurrent claim/acknowledgement, deterministic
  due state, no local-withdrawn plus receiver-accepted race, and fail-closed
  pending behavior for permission or version mismatch.
- Run the exact current documentation, package, privacy, concurrency, offline
  suite, diff, and two current-target Tier 2 review gates.

Only these static prerequisites and gates live here. The Task database owns
TG-M12.3's current state, blocker detail, review evidence, and completion
history. A materially different write, external operation, scope expansion,
or changed acceptance still requires explicit authority.

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
