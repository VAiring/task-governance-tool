# task-governance-tool Current Decisions And Open Issues

The [release/install owner](docs/release-install.md) records the immutable
published release and current unpublished candidate, including schema, Viewer
compatibility and command inventory. This plan retains current decisions,
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
- Optional [ordinary-Task preapproval](docs/task-operation-specification.md#optional-ordinary-task-preapproval)
  separates approved local records from existing Contract, quality, configuration,
  Runner, Git and external authorization. Host activation is explicit; configuration
  examples or fixture success do not establish a reduction in live approval reviews.

### Review, Completion, State, And Viewer

- Qualifying PASS receipts and changes-requested receipts are evaluated only
  for the current review target and generation. Any unresolved High or Medium
  finding from any recorded generation blocks completion. Distinct reviewer
  keys prove distinct stored strings, not people, machines, independence, or
  authenticated provenance; the trusted caller records results truthfully.
- Completion requires typed evidence. Reopen preserves append-only completion
  cycles but historical evidence never satisfies the new current gate.
- Review waiting uses the existing host API as defined by
  [Review/completion](docs/review-completion-specification.md#host-owned-review-waiting),
  within the current tool and execution limits. The approved
  [parent-turn replacement](docs/proposals/review-wait-notification.md) owns the
  ten-minute check and all-ended resumption acceptance: one designated writer
  deletes the same reservation, confirms deletion, then sends once through
  public MCP to the same idle parent. This supersedes the earlier shortening
  acceptance for the replacement. The explicitly authorized
  [same-parent development experiment](docs/review-completion-specification.md#development-direct-review-wake)
  tests that ordered path while preserving the earlier PAUSED-only one-shot
  probe and its evidence. Neither experiment activates an installed workflow.
  Integration verification and synchronization remain required for completion;
  pending-turn guidance does not satisfy the replacement.
- The accepted correction replaces the review supervisor with the packaged
  deterministic service. The approved execution owner also covers canonical
  storage, explicit Setup policy, handoff/Skill synchronization and fresh
  integrated host verification. [Review/completion](docs/review-completion-specification.md#host-owned-review-waiting)
  owns these boundaries. Importing, testing or enabling local policy alone
  does not configure, trust or connect a host controller.
- Durable project identity is separate from mutable filesystem binding. A path
  mismatch is not move/copy/fork intent; only explicit setup confirmation may
  advance a binding.
- Generated state uses the governed root's fixed `.taskgov/` boundary while
  Skill code/config remain in the physical package. The explicit offline
  [separation contract](docs/setup-state-specification.md#project-state-separation)
  preserves identity and defines source retention, retirement and retry;
  it adds no schema, arbitrary path or normal-loop call. Its three-unit
  execution boundary remains in the separately routed conditional plan.
- The user-approved 2026-09-21 migration prerequisite leaves completion of an
  unfinished predecessor migration with the compatible pre-separation setup;
  new separation preserves/refuses it. See the same separation contract for
  its exact applicability. Healthy-source migration and normal Task work do
  not gain another setup call.
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
- Completed release approval objects and exact gate evidence authorize no future
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
- Pull requests schedule all three base-lane jobs on Python 3.12 and `fast` on
  3.14; pushes to `main` schedule every base lane on both versions; manual
  `workflow_dispatch` runs `all` on both versions by default.
- Complete discovery and base-lane ownership are validated before event
  selection. Pull-request and push release selections retain the two
  deterministic backup/Viewer functional-capacity tests and defer only the two
  closed wall-clock qualification identities. Manual `workflow_dispatch`
  defers nothing in its default full mode, so both full-version `all` jobs
  execute the qualifiers.
- An additional platform job runs the closed common-processing,
  CLI-startup, applicable artifact-operation, Runner-preparation, and private
  Runner-process selection owned by `tools/test_lanes.py` on Ubuntu 24.04 x86-64
  and macOS 15 Apple Silicon with
  Python 3.12. It retains complete discovery and lane validation before
  selection. Both hosts also select existing physical-install, Task completion,
  Evidence, Viewer, Backup, and representative recovery integration tests by
  exact identity through the same entry. Published support remains with the
  release/install contract; Runner preparation follows its current
  [design](docs/runner-execution-design.md#typed-process-value-boundary), and
  private process tests follow their [execution owner](docs/runner-execution-design.md#private-posix-process-execution).
  Both hosts additionally select the public Runner-to-completion/Evidence module
  and the portable state-separation record, resolver and explicit setup
  cutover/retry modules,
  and existing exact Runner cases for uncertain process cleanup, restart cleanup,
  and pending/cleanup-only/stale completion refusal. Selected applicable tests
  must execute without SKIP; this does not prohibit legitimate OS-inapplicable
  skips in the exhaustive Windows suite. Test selection does not itself activate
  an unsupported public Runner route.
- Manual `platform_only=true` runs policy validation and these platform checks
  without the Windows full matrix or release-candidate gate. It is an explicit
  platform-check route, never a substitute for full candidate qualification.
  Omission or `false` preserves the existing Windows event matrix and full
  manual gate; pull-request and push coverage are unchanged.
- A future release candidate requires the explicit aggregate manual gate after
  policy validation, both full-version Windows jobs, and both platform checks.
  Every dependency must succeed; a failure or skipped dependency cannot qualify.
  This repository CI policy
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
- Exact stored legacy `dispatch_authorization` counter forms remain readable
  only in legacy Contract constraints and checkpoint summaries. The reader
  preserves their original bytes and grants no write, dispatch, or other
  authority; all new input and completion-history public text use the normal
  strict privacy boundary.
- Omitting constraints during an otherwise valid Contract revision continues
  to carry forward the already-validated prior constraints bytes, including
  bounded legacy lineage. This is preservation, not acceptance of caller-
  supplied legacy vocabulary.

<a id="m25-select-split-merge-register"></a>
<a id="m25-active-select-split-merge-register-guidance"></a>

### Task Decomposition And Registration Guidance

Select-Split-Merge-Register remains active only as Skill instruction-layer guidance for two explicit
authority events: a request to register or taskize already-authorized work, and
an explicit scope addition to an in-progress or review-pending Task. The active
product and implementation contracts are the
[specification](docs/task-operation-specification.md#task-decomposition-and-registration)
and [design](docs/task-operation-design.md#task-decomposition-and-registration-design);
the concise operating rule and complete procedure remain in
[SKILL.md](task-governance-tool/SKILL.md) and the
[Task workflow](task-governance-tool/references/task_workflow.md).

The retained decision is one authority envelope, one flat Split, and one global
Merge at boundaries where separate completion serves a concrete decision or
use, as defined in the active owners. Final groups conserve exact scope
and permissions, use existing lane/order, leave a correct ordered repository
state, own attributable verification and review, and remain resumable without
prior chat. Shared files, tests, commands, or fixtures alone do not force a
Merge.

Registration and Contract population copy only explicit authority. Honest
revision-zero, grouped-question, partial-add recovery, design-first, and Review
Tier-floor behavior remain those of the active owners above. This guidance adds
no command, schema, JSON/database field, Viewer/Runner behavior, dependency
model, normal-loop call, network use, target mutation, or automatic execution
unit. The Task database alone owns live state and evidence.

<a id="current-verification-receipt"></a>

### Current Verification Receipt Decision

The schema-v18-origin Verification Receipt behavior retained by current schema
v22 is defined by the active [specification](docs/specification.md) and
[design](docs/design.md). Completed
Verification Receipt design, activation, acceptance, and correction narrative is preserved only
in [indexed non-authoritative history](docs/history/v0.11.0/pre-m22-completed-execution.md).
That history supplies no current gate or implementation authority.

<a id="current-evidence-bundle-json"></a>

<a id="current-schema-v21-evidence-bundle-and-json-decision"></a>

### Current Schema-v22 Evidence Bundle And JSON Decision

The v0.13.0 candidate uses the schema-v18 capture foundation and public
verification-admission boundary, schema-v19 immutable native completion
Bundles, criterion links, Finding snapshots, and the fixed one-way Evidence
JSON projection. Current schema-v22 native completions write source-22 Bundle v2 through
the closed basis union: the two manual arms retain a null Runner observation,
while the qualifying Runner arm reuses its exact sanitized stored observation.
The format-v2 index reports its actual source schema and can reference preserved
source-19/v1 and source-20/21/v2 Bundles without rewriting their bytes or digests.
Pre-v19 cycles remain index-only as `legacy_unknown`; SQLite remains
canonical. Setup repairs the projection, doctor observes it read-only, and
post-commit maintenance runs Evidence, Viewer, then backup. Viewer snapshot v4
accepts v5-v22 but adds no Evidence UI.

Schema v22 uses the current
[persistence contract](docs/database-specification.md#current-schema-v22-persistence-contract)
and [reservation-cleanup design](docs/database-design.md#schema22-reservation-cleanup-design):
explicit setup reaches 22 from supported older sources and exact-22 reentry is
validation-only. Current Evidence enum/order and DDL allow-lists no longer
reserve `derived_analysis`, `llm_derived`, or `batch_analyzer`; old-schema DDL
and rejection vocabulary remain compatibility-owned. Valid rows and shared
Evidence tables, the schema-v21 Runner protocol, and the Skill loop are unchanged.

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

The adopted decision keeps bounded Runner Plan authoring active only through the
existing `task edit <task-id> --runner-plan-action
replace|rebind|detach|disable` option; `replace` alone consumes the strict
`RunnerPlanDraftV1`. The current
[specification](docs/runner-plan-authoring-specification.md#current-runner-plan-authoring-and-control-contract)
owns behavior, the current
[design](docs/runner-plan-authoring-design.md#current-runner-plan-authoring-and-control-design) owns
implementation structure, [AGENTS.md](AGENTS.md#target-project-safety) owns the
durable mutation boundary, and the
[README](README.md#explicit-runner-plan-authoring) owns operator opt-in guidance.

Authoring remains optional and outside the normal Skill loop. It retains the
21-leaf CLI, PlanV1, setup non-generation, the one canonical ignored
`config/verification-runner.json`, and `review target set` as the sole Runner
dispatch. Taskgov-managed publication is authorized only by the explicit action
invocation and grants no other target write, setup side effect, Runner launch,
Skill trigger, Git or network operation, external CI or publication,
target-project installation, or unrelated mutation. After an unconfirmed
post-Task-commit Plan disposition, Runner execution must not be relied on until
an explicit Plan-only repair succeeds.

The adopted action set and permission boundary are closed. PlanV2, another
command or execution route, automatic command inference or mutation, a
re-enable action or restore workflow, a SQLite journal, broader safety claims,
or a Skill or normal-loop change requires separate explicit authority.

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

<a id="setup-optional-feature-selection"></a>

### Approved Setup Optional Feature Selection Contract

This is the approved, pending-implementation static contract for the
2026-10-03 request to organize the specification and register a repair Task.
It does not activate new runtime behavior or authorize starting implementation,
changing this project's live optional settings, or trusting host hooks.
The existing product and design owners remain current until the reviewed
implementation synchronizes them. Live Task state and evidence belong only to
the public CLI.

#### Outcome And Setup Conversation

After the existing basic setup succeeds, return enough structured information
for the calling LLM to offer the optional features together instead of ending
with only a completion notice. Setup itself remains noninteractive; the LLM
collects the user's choices and applies only the authorized selections through
a bounded setup configuration path. The final notice states the actual result.

- Offer numerical usage collection, trusted-local verification Runner,
  Effort Advisory, and Viewer automatic reload. Existing setup maintenance,
  backups, Evidence JSON, and Viewer generation are not additional switches.
- Distinguish an undecided preference, explicit ON/OFF choice, effective local
  configuration, and a prerequisite that setup cannot observe. Unknown or
  invalid configuration is not evidence of OFF, ON, or successful application.
- Preserve existing valid settings and explicitly recorded choices. Persist
  ON and OFF choices across sessions and repeated setup, including a decision
  to leave an absent feature disabled, so it is not repeatedly offered as new.
  A later explicit request may change the choice. An unanswered question is
  not an OFF choice and must not be recorded as consent.
- Ask only for unresolved selections during setup, together where practical;
  reuse answers already supplied in the conversation. A settings-change request
  may revisit an existing choice. Do not add a question or command to ordinary
  Task work, and do not make optional choices a basic-setup completion gate.
- Retain valid feature-specific values when changing ON/OFF. Explain any
  necessary value, such as a new reload interval, when offering activation;
  use the supported configuration semantics without adding mandatory tuning.

#### Feature Meaning And Boundaries

| Feature | Meaning of the choice and remaining prerequisite |
|---|---|
| Numerical usage collection | ON requests this project's taskgov collector. Setup reports definition preparation separately from host trust and actual collection. Host trust remains user-operated and unobserved, never inferred from ON or prepared definitions. OFF must prevent taskgov collection even if its hook was previously trusted, while preserving accumulated numerical records and unrelated hooks. It does not revoke or change host/user-wide trust. |
| Verification Runner | ON/OFF controls the project-level trusted-local opt-in while preserving existing Task entries and limits. ON may be configured before a Task Plan exists; it does not fabricate a Task, command, Plan entry, coverage, or execution result. Task-specific current Plans remain separately authored, and execution remains at the existing review-target boundary. An enabled Runner with no entry for the current Task retains the existing manual verification route; stale, invalid, or ambiguous entries retain their existing rejection behavior. |
| Effort Advisory | Apply the selected enablement using the supported project profile, preserving configured thresholds. It remains informational with the existing optional observation flow; selection adds no completion condition or new mandatory threshold. |
| Viewer automatic reload | Apply the selected reload behavior and preserve an existing valid interval. OFF must stop reload rather than merely record a preference. The generated Viewer continues to exist and receive ordinary maintenance; browser launch and automatic reload remain distinct. |

Readiness and recorded preference must remain distinguishable. In particular,
an ON choice with hook trust unconfirmed or no Runner Task entry must not be
reported as observed collection or ready-to-run verification. Actual local
configuration, including separately authorized manual changes, must not be
misreported based only on an older choice record.

Collection OFF applies to subsequent collector invocations; it does not require
terminating a running process. Effort enablement must not invent warning
thresholds or require them: the existing empty-threshold profile remains valid.

#### Write Scope And Permission

One coherent implementation unit owns the setup response and bounded
selection-application path, durable project-local choice retention, the four
feature integrations, setup-only Skill guidance, and their coupled tests and
formal documentation. Do not split this outcome solely by configuration file
or feature name. Register one sequential Tier 2 Task in lane
`TG-SETUP-FEATURES-20261003`, order 10; no predecessor in this lane or separately
usable intermediate delivery is required.

Use existing canonical project/package resolution and project-local settings;
do not introduce user-wide configuration, another state root, or a network
service. The implementation specifies the exact bounded input/output and
choice-storage format in its current owners. A stored choice is not host-trust
evidence or a second source for Task authority. A settings operation must not
silently replace unrelated settings, Runner entries, or hook handlers.

Basic setup without feature-selection input inspects/reports optional choices
without silently enabling them. Existing required setup work, including hook
definition preparation, remains governed by its current contract until the
synchronized feature implementation. Applying choices requires the user's
selection; the same answer authorizes its bounded application without a second
routine approval. Preview/read-only paths write nothing. Reapplying an unchanged
choice is idempotent; a later setup preserves the selected disabled state.
Report per-feature configuration failures and any partial application truthfully; do not
mark a requested choice effective before its application succeeds. Optional
application failure must preserve already successful basic setup and must not
start a migration again, execute verification, or invoke collection as a test.

Current owners to synchronize are
[Setup behavior](docs/setup-state-specification.md#setup-contract) and
[structure](docs/setup-state-design.md#setup-plan-and-stages),
[Runner Plan authoring](docs/runner-plan-authoring-specification.md#current-runner-plan-authoring-and-control-contract)
and its linked design, the shared
[Runner eligibility](docs/runner-execution-specification.md#eligibility-plan-and-materialization),
[Effort Advisory](docs/task-operation-specification.md#effort-advisory),
and [Viewer reload](docs/viewer-specification.md#optional-visibility-aware-reload)
and their linked designs. Update directly coupled CLI help/contracts, setup
Skill references, operator examples, package artifacts, and existing durable
permission wording that would otherwise conflict with the explicit setup
selection path. Keep detailed selection guidance on the setup-only reference
route, not in the ordinary Task loop.

This contract permits a future explicit setup choice to configure Runner
enablement, Effort, and Viewer reload despite their current non-generation
rules. It does not authorize setup to author Task-specific Runner steps, launch
a Runner or browser, change host trust, install a package, or perform external
operations. Existing usage accuracy, verification, review, and completion
semantics remain unchanged. Implementation and local Git actions require their
own applicable start/mutation authority; registration is not that authority.

#### Verification, Review, And Retirement

Verify in isolated local fixtures: first setup and combined choices; actual
ON/OFF effects; existing settings and explicit OFF retention across repeated
setup and a fresh caller; later choice changes; unchanged replay; no writes in
preview; invalid/failed/partial settings application without loss of basic
setup or unrelated settings; and truthful readiness in JSON/text and Skill
conversation. Include hook trust remaining unknown, collection OFF preserving
recorded usage, Runner ON before any Task entry and with an existing valid
entry, and manual verification when there is no entry for the current Task.
Preserve the existing
normal-operation and failure behavior of the affected features.

Use focused setup/configuration/CLI/feature tests and package validation, the
repository document-contract checker, and representative setup conversations.
No network or real host trust/configuration mutation is required for these
checks. No new token benchmark, production installation, or unrelated proof
gate is part of acceptance. Tier 2 requires two independent review passes with
no unresolved High/Medium findings for the current implementation target.

This static contract remains the positive scope/acceptance owner until the
registered Task is completed or explicitly superseded and its durable rules
have been synchronized into their current owners. Retire or reduce it only
through the separately authorized documentation-maintenance procedure; do not
copy live Task progress into this section.

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
