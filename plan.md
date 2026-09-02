# task-governance-tool Current Decisions And Open Issues

Decision baseline: v0.10.0 is the immutable published release; its exact
identity lives in `docs/release-install.md`. The current unpublished local
candidate is v0.13.0/schema v21/Viewer snapshot v4 with source compatibility
v5-v21 and 21 public command leaves. This plan retains current decisions,
unfinished static contracts, and open issues only. Completed execution
narrative is indexed as non-authoritative history, while the Task database,
queried through the public CLI, solely owns live execution status and evidence.

This file owns current decisions, explicit open issues, and user-decision
gates. It is not the product contract, execution ledger, or evidence store:

- [`docs/specification.md`](docs/specification.md) owns product behavior.
- [`docs/design.md`](docs/design.md) owns implementation structure.
- [`docs/authority.md`](docs/authority.md) owns the concise mandatory and
  selective read routing without transferring product/design ownership.
- This plan owns current decisions, open issues, cross-sequence gateways, and
  static contracts not delegated by `docs/authority.md`. It is not a progress
  table.
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
- The accepted trusted-local Runner decision removes Candidate C, B-to-C,
  LPAC/AppContainer, ETW, and registry-recovery native matrices from current
  Runner qualification and completion gates. The accepted retirement decision
  left the inventory-approved retired Candidate, LPAC/AppContainer, profile/ACL,
  dedicated native,
  and Candidate-only runtime material and its dedicated tests physically
  absent, with no archive or dormant copy. Their absence neither qualifies the
  Runner nor adds a security gate. Current verification remains limited to the
  smallest realistic process, cleanup, privacy, migration, compatibility, and
  package test sets required by the active specification and design.

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

<a id="m25-select-split-merge-register"></a>

### M25 Active Select-Split-Merge-Register Guidance

M25.1, Task `tg_task_8e33e15cd97a28ee`, in lane
`TG-M25-TASK-DECOMPOSITION` at order 10, is sequenced after TG-RPA.6; the Task
database owns fulfillment of that gateway. It is a Tier 2 docs-only unit for
two explicit authority events: a request to register or taskize authorized
work, and an explicit scope addition to an in-progress or review-pending Task.
M25.2, Task `tg_task_d891cd538d9e7364`, at order 20 activates that accepted
contract only in Skill and task-workflow guidance after M25.1; the Task database
owns fulfillment of the activation and its review gates.

The accepted design selects stable responsibilities, performs one flat Split,
then one global Merge of all fragment-only transitive groups and never
re-Splits. Final slices conserve exact scope and permissions, declare consumed
inputs and produced outputs, leave a correct ordered repository state, carry
locally attributable verification/review, and fit a fresh-agent context without
requiring standalone user value. Sharing files, tests, commands, or fixtures
alone does not force Merge.

Contracts copy explicit authority only. Missing split or Contract detail falls
back to one whole-outcome revision-zero Task when outcome and permission are
clear; exact mandated-boundary conflicts or unclear outcome/permission use one
grouped question and no partial write. Partial-add recovery preserves successful
registrations and adds only proven omissions without deletion or repartition
while the same event retains its transient final set. After interruption loses
that set, no reconstruction occurs; current explicit authority governs a
grouped question when the exact remainder or permission is unclear, or one
confirmed revision-zero remainder Task when both are clear.

Binding authority supplies a Review Tier floor. With no mapping, the closed
fallback is Tier 2 for schema/migration, JSON contract, CLI write behavior,
target mutation, privacy/logging, Skill trigger, verification/review/completion
gate, milestone/plan acceptance, and implementation-binding normative docs;
Tier 0 for wholly mechanical meaning-preserving work; and Tier 1 otherwise.
Unknown, size, difficulty, duration, failure count, safety wording, or reviewer
availability never alone selects Tier 2. Mid-Task keep preserves Tier, revise
raises only to a higher resulting floor and never auto-lowers, a successor uses
its own scope, and Merge into current uses the maximum. A required Tier raise
precedes Contract revision, so failure cannot leave expanded scope below its
floor. Later integration review never replaces each Task's required gate.

Design-first registers no automatic implementation Task. M25.2 activates only
the two explicit Skill routing events and adds no command, schema, JSON or
database field, Viewer/Runner change, parent/child or dependency model,
normal-loop call, network use, target mutation, or automatic execution unit.
The exact Task DB record owns live state.

<a id="current-verification-receipt"></a>

### Current Verification Receipt Decision

The schema-v18-origin Verification Receipt behavior retained by current schema
v21 is defined by the active [specification](docs/specification.md) and
[design](docs/design.md). Completed
M21 design, activation, acceptance, and correction narrative is preserved only
in [indexed non-authoritative history](docs/history/v0.11.0/pre-m22-completed-execution.md).
That history supplies no current gate or implementation authority.

<a id="current-evidence-bundle-json"></a>

### Current Schema-v21 Evidence Bundle And JSON Decision

The v0.13.0 candidate uses the schema-v18 capture foundation and public
verification-admission boundary, schema-v19 immutable native completion
Bundles, criterion links, Finding snapshots, and the fixed one-way Evidence
JSON projection. Current schema-v21 native completions write Bundle v2 through
the closed basis union: the two manual arms retain a null Runner observation,
while the qualifying Runner arm reuses its exact sanitized stored observation.
The format-v2 index can reference preserved Bundle-v1 bytes without rewriting
them. Pre-v19 cycles remain index-only as `legacy_unknown`; SQLite remains
canonical. Setup repairs the projection, doctor observes it read-only, and
post-commit maintenance runs Evidence, Viewer, then backup. Viewer snapshot v4
accepts v5-v21 but adds no Evidence UI.

The standalone Runner audit writer is reached only through the existing exact
target-set dispatch. The completion gate admits only the exact qualifying pass
or closed no-launch manual fallback, and Evidence JSON projects only an
already-stored sanitized qualifying observation.
Projection adds no Runner invocation or normal-loop call, Viewer UI, public
leaf, network/live-model action, or target mutation. Gate integration adds only
the closed `verification_route` and nullable `blocking_code` fields to the
existing target-set JSON success response, so the Skill selects the branch
without inference or a second `task show`.

<a id="runner-plan-authoring"></a>

### Approved Active Runner Plan Authoring And Control Contract

The user approved one bounded follow-up that makes the existing physical
Runner Plan authorable without adding a command leaf or another Runner
execution decision. The active contract retains the exact 21-leaf CLI, setup
never generates the Plan, and `review target set` remains the sole Runner
dispatch. The active [specification](docs/specification.md) owns the closed
behavior and [design](docs/design.md) owns its implementation boundaries; this
section owns the decision and execution sequence.

The frozen choices are:

- add only `task edit <task-id> --runner-plan-action
  replace|rebind|detach|disable`; `replace` alone consumes one strict bounded
  `RunnerPlanDraftV1` from standard input;
- keep the existing PlanV1 and canonical ignored
  `config/verification-runner.json`; the first absent-file `replace` uses fixed
  `plan_id=taskgov-local-plan` and explicit `trusted_local=true`, while every
  present-plan action preserves Plan ID and no action re-enables a disabled
  Plan;
- derive Task ID, future Contract revision, verification expectation and
  criterion digests, and full coverage mechanically; never discover or infer
  steps, trust, coverage, or test sufficiency;
- privacy-check every recognized caller StepV1 string leaf through the existing
  common guard before its grammar or size validation, with
  `privacy_rejected` precedence and no change to current PlanV1 reader
  admission;
- require one explicit Plan disposition in the same invocation when an actual
  Contract/verification basis edit would stale a currently enabled exact-match
  entry; do not require it for metadata/status edits or automatically follow
  the Task;
- allow a Plan-only invocation for initial/repair/update/disable work, so the
  normal basis-change path uses one command and one user approval rather than a
  mandatory preview/apply pair;
- after the SQLite writer closes, revalidate the originally captured physical
  Plan source for every action; a semantic no-op performs confirmation only and
  never rewrites or normalizes the file;
- commit a real Task edit first and publish the Plan second. These are separate
  operations, not a cross-store atomic transaction. A later Plan failure keeps
  the Task, leaves the requested Plan disposition unconfirmed, reports the
  fixed `task_applied_runner_plan_unconfirmed` partial-success warning, and
  requires one explicit Plan-only repair before the caller relies on Runner
  execution;
- make global disable only `trusted_local=false` with entries and history
  retained; make Task disable only `detach`; neither cancels an in-flight
  attempt nor deletes prior Runner observations, Evidence, Bundles, or
  completion history; and
- document opt-in and operations in the root README and formal CLI reference
  at activation. Do not add a Skill trigger or a normal-loop README/Skill read.

No product choice remains open before implementation. An implementation Task
may report a concrete conflict with current code, tests, or platform behavior,
but it must not add preview tokens, PlanV2, a SQLite journal, automatic Task or
Plan mutation, command inference, a new execution route, a re-enable workflow,
or a broader safety claim as a repair.

The approved sequence is one sequential lane. The Task database owns each
unit's live status, Contract revision, target, evidence, and review history;
the table records only the static dependency and responsibility split:

| Order | Task | Static outcome and write boundary |
|---:|---|---|
| 10 | TG-RPA.1 / `tg_task_d3e404b8a6708d2e` | Freeze this inactive contract in AGENTS, specification, design, and plan and register the finite successor set. No package behavior. |
| 20 | TG-RPA.2 / `tg_task_471494a3a894d381` | Implement only strict privacy-first draft decoding and pure PlanV1 `replace|rebind|detach|disable` transforms with focused tests and corresponding packaged-file manifest updates. No I/O. |
| 30 | TG-RPA.3 / `tg_task_3a0295e436162d18` | Implement only the canonical physical Plan publisher, expected-source revalidation, complete-file replacement, physical failure tests, and corresponding packaged-file manifest updates. No DB or Runner dispatch. |
| 40 | TG-RPA.4 / `tg_task_0b3390367e9e1274` | Implement only internal DB-first Task-edit/Plan coordination, typed partial-success handling, focused tests, and corresponding packaged-file manifest updates. No public parser connection. |
| 50 | TG-RPA.5 / `tg_task_0f489923a89ffadc` | Connect only the existing `task edit` leaf and atomically synchronize AGENTS, current formal docs, CLI contract, README, final package manifest, help/output, and focused acceptance. Skill guidance remains unchanged. |
| 60 | TG-RPA.6 / `tg_task_bdb4541db361dd64` | Acceptance-only exact-target regression, all offline lanes, and two independent reviews. Product corrections return to the owning predecessor. |

Each unit depends on acceptance of every earlier order in this lane and is
Tier 2 with two independent exact-target reviews. TG-RPA.2 through TG-RPA.5 use
the narrowest focused tests plus the applicable release, lane, document, and
diff checks; a redundant full-suite requirement is deliberately excluded from
those units. TG-RPA.6 alone owns the final full deterministic offline suite.

TG-RPA.5 is the only activation gateway. Its one reviewed revision switches the
inactive markers and synchronizes implementation, public CLI/JSON/text,
`AGENTS.md`, formal owners, CLI reference, README, final package manifest, and
focused tests.
TG-RPA.6 validates but does not broaden the activated contract. Registration
authorizes governance records and the listed future
repository-local implementation only; it grants no commit, push, network,
external CI, publication, target-project installation, or unrelated mutation.

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
- The user has explicitly adopted the trusted-local, explicit-opt-in
  Runner direction. Do not broaden it into execution of untrusted/external
  targets, a hostile-code sandbox, network isolation, or an automatic normal-
  loop action. Any such expansion requires a separate future decision after
  current operational evidence is reviewed.

## Reference Material

- `references/KuraKoma_TASK_STATUS.md` is a copied example from another
  project. It is non-authoritative and is not current status or implementation
  order.
- Historical planning narratives, release-stage execution contracts, and
  superseded forward-test evidence are discoverable only through
  [`docs/history/README.md`](docs/history/README.md).
