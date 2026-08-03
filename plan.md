# task-governance-tool Current Decisions And Open Issues

Decision baseline: v0.10.0 is the immutable published release; its exact
identity lives in `docs/release-install.md`. The current unpublished local
candidate is v0.11.0/schema v17/Viewer snapshot v4 with source compatibility
v5-v17 and 21 public command leaves. This plan retains current decisions,
unfinished static contracts, and open issues only. Completed execution
narrative is indexed as non-authoritative history, while the Task database,
queried through the public CLI, solely owns live execution status and evidence.

This file owns current decisions, explicit open issues, and user-decision
gates. It is not the product contract, execution ledger, or evidence store:

- [`docs/specification.md`](docs/specification.md) owns product behavior.
- [`docs/design.md`](docs/design.md) owns implementation structure.
- [`docs/authority.md`](docs/authority.md) owns the concise mandatory and
  selective read routing; its conditional registry points to accepted inactive
  execution detail without activating it.
- This plan owns current decisions, open issues, cross-sequence gateways, and
  static contracts not delegated by `docs/authority.md`. It is not a progress
  table. Indexed conditional documents own their named inactive units.
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

<a id="tg-m20s-3"></a>

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

<a id="current-verification-receipt"></a>

### Current Verification Receipt Decision

The current schema-v17 Verification Receipt behavior is defined by the active
[specification](docs/specification.md) and [design](docs/design.md). Completed
M21 design, activation, acceptance, and correction narrative is preserved only
in [indexed non-authoritative history](docs/history/v0.11.0/pre-m22-completed-execution.md).
That history supplies no current gate or implementation authority.

<a id="tg-m22-1a"></a>

### TG-M22.1A Review Provenance Prerequisite

TG-M22.1A `tg_task_0e1d93d81eb843ab` is a sequential Tier 2
execution unit in lane `TG-M22-EVIDENCE-LEDGER` at order 25. It depends on
accepted TG-M21.4D and completed TG-DOC.1
`tg_task_7d03a44b6733fee4`. It freezes the closed conditional Review
provenance matrix and synchronizes formal documents plus downstream Task
Contracts only. Human/model-not-applicable, LLM/Skill-not-used, declared,
unknown, not-required, and legacy states remain distinct without proving
identity, competence, independence, or truth.

This unit changes no v17 runtime, schema, command, normal-loop call, Viewer UI,
package, network, or target-project behavior. Completion requires exact
documentation consistency, an exact-diff current Verification Receipt, and
two independent Tier 2 reviews. Its detailed accepted boundary and the later
M22 units are owned conditionally by the
[TG-M22 Evidence Ledger execution contract](docs/execution-contracts/tg-m22-evidence-ledger.md).

### Conditional TG-M22 And TG-M23 Sequences

After TG-M22.1A, the exact M22 purpose, order, dependencies, permissions, and
gates are owned by the
[TG-M22 conditional execution contract](docs/execution-contracts/tg-m22-evidence-ledger.md).
The exact approved-but-inactive M23 sequence is owned by the
[TG-M23 conditional execution contract](docs/execution-contracts/tg-m23-derived-evidence.md).
Those contracts are read only when their owning sequence or a directly
coupled cross-cutting decision is in scope. They activate no behavior by being
indexed. Their sequential ordering and Tier 2 gates remain mandatory, and
live status, blockers, targets, evidence, reviews, and completion history
remain solely in the Task database.

<a id="tg-m24-sequence"></a>

### Approved TG-M24 Verification Runner Sequence

TG-M24 is approved but inactive sequential Tier 2 work in lane
`TG-M24-VERIFICATION-RUNNER`. Its exact conditional detail is owned by the
[TG-M24 Verification Runner execution contract](docs/execution-contracts/tg-m24-verification-runner.md).

| Unit/order | Task | Dependency | Purpose, permission boundary, and completion gate |
|---|---|---|---|
| TG-M24.1 / 10 | `tg_task_29aa63124900ad95` | accepted TG-M23.3 | Design project-owned verification plans, shell-free argv, exact Task/Contract/expectation/target binding, environment/network/mutation/resource policy, sanitized runner-observed evidence, shadow-to-gate staging, and the M21 fallback. Activate no Runner, schema, CLI, Skill, gate, network, credential, or target mutation. Require exact documentation checks and diff, a current Receipt, and two independent Tier 2 reviews. |
| TG-M24.2 / 20 | `tg_task_fafad7bc62df7576` | accepted TG-M24.1 | Implement only the approved bounded Runner and append-only evidence in shadow mode; existing M21 and completion gates remain unchanged. Execute only an explicit current project-owned plan, and require separate exact authority for any live external-project run. Require migration, safety, package, focused/full offline checks, exact diff, a current Receipt, and two Tier 2 reviews. |
| TG-M24.3 / 30 | `tg_task_dc015144091f8e60` | accepted TG-M24.2 | Make one qualifying exact-current complete-plan Runner result an explicit versioned completion basis while retaining the M21 caller-attested Receipt for unsupported, manual, visual, external, or unavailable-Runner cases. Analyzer output, arbitrary commands, and new normal-loop LLM leaves gain no gate authority. Require full offline, package/release consistency, exact diff, a current Receipt, and two independent Tier 2 reviews. |
| TG-M24.4 / 40 | `tg_task_f81f2d126f033a59` | accepted TG-M24.3 | Accept Runner safety, provenance, the completion gate, M21 fallback, Evidence Bundle/JSON, Analyzer coexistence, legacy/history, and realistic supported/unsupported flows, with bounded corrections inside M24.1 only. Runs outside approved fixtures or this repository require separate exact project authority. Require focused/full and authorized forward checks, exact diff, a current Receipt, and two independent Tier 2 reviews. |

The sequence may advance only in that order. It grants no arbitrary command or
model choice, analyzer gate authority, raw-output retention, external-project
execution, publication, push, tag, or Release authority.

<a id="tg-doc-2"></a>

### TG-DOC.2 Post-M24 Documentation Closure

TG-DOC.2 `tg_task_bf2aa245019f5c9f` is a sequential Tier 2 documentation unit
in lane `TG-DOC-LIFECYCLE` at order 20, dependent on accepted TG-M24.4. It
folds the final supported M21-M24 behavior into subsystem-oriented active
specification and design sections, routes completed execution narrative to
indexed non-authoritative history, and leaves this plan limited to unfinished
static contracts, current decisions, and open issues.

Its write permission is limited to documentation, editorial Skill/reference
synchronization, repository-local read-only checks, and coupled tests. It may
not change runtime, schema, migrations, storage, public CLI/JSON, Viewer,
package/install identity, evidence assurance, fallback or completion gates,
network behavior, or target-project authority. It may not copy live Task state
or evidence into Git documents.

The documentation budget established by TG-DOC.1 is a blocking gate:
before/after read-set measurements must retain a material reduction, bounded
document/read-set limits must pass, and TG-DOC.2 may not relax those limits to
obtain PASS. Completed M21-M24 narratives must be immutably indexed through
`docs/history/README.md`; history may not fill an active authority gap, and no
unfinished contract may be retired. Completion requires authority/link/history
coverage, full offline checks, an exact-diff current Verification Receipt, and
two independent Tier 2 reviews with no unresolved High or Medium finding.

<a id="tg-m12-3"></a>

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
