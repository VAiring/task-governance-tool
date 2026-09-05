# task-governance-tool Current Decisions And Open Issues

Decision baseline: v0.10.0 is the immutable published release; its exact
identity lives in `docs/release-install.md`. The current unpublished local
candidate is v0.13.0/schema v22/Viewer snapshot v4 with source compatibility
v5-v22 and 21 public command leaves. This plan retains current decisions,
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

M25 remains active only as Skill instruction-layer guidance for two explicit
authority events: a request to register or taskize already-authorized work, and
an explicit scope addition to an in-progress or review-pending Task. The active
product and implementation contracts are the
[specification](docs/specification.md#current-m25-select-split-merge-register-contract)
and [design](docs/design.md#current-m25-select-split-merge-register-design);
the concise operating rule and complete procedure remain in
[SKILL.md](task-governance-tool/SKILL.md) and the
[Task workflow](task-governance-tool/references/task_workflow.md).

The retained decision is one authority envelope, one flat Split, and one global
Merge at stable responsibility boundaries. Final groups conserve exact scope
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
M21 design, activation, acceptance, and correction narrative is preserved only
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
[persistence contract](docs/specification.md#current-schema-v22-persistence-contract)
and [reservation-cleanup design](docs/design.md#schema22-reservation-cleanup-design):
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
[specification](docs/specification.md#current-runner-plan-authoring-and-control-contract)
owns behavior, the current
[design](docs/design.md#current-runner-plan-authoring-and-control-design) owns
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

<a id="privacy-matcher-corrections-and-runner-naming"></a>

### Approved Follow-Up — Privacy Matcher Corrections And Runner Naming

The user approved registration of the bounded follow-up below. These are
execution contracts, not a live status record. Current
privacy behavior remains owned by the specification's "Privacy, Safety, And
Stable Errors" and the design's "Privacy, Safety, And Failure Boundaries".
Registration does not start implementation or authorize Git operations,
setup/migration, live Runner execution, configuration publication, or release.

Retain Evidence JSON automatic projection and its external-consumer purpose,
Review provenance, Viewer reload state, Handoff delivery reservations for a
future Issue Skill, active Runner cleanup/gates and historical compatibility,
Setup/relocation/recovery, and Effort Advisory. None is a cleanup target here.
Do not extend this work into a schema change, platform port, common security
framework, new setting, LLM classifier, normal-loop call, or routine approval.

TG-PMC.1, TG-PMC.2, and TG-PMC.4 form lane `TG-PMC` at orders 10, 20, and 30;
each later implementation preserves the earlier accepted cases. TG-PMC.3 and
TG-RNC.1 are independently selectable optional-kind Tasks, not predecessors or
final integration gates.
Each implementation unit includes its own affected documentation, tests, and
package-manifest synchronization; do not postpone those to another unit.
Use synthetic examples and isolated test state, not real private content or
the live Task database, for regression tests. Existing valid stored content,
sealed Bundle bytes, and historical evidence must remain readable without
rewriting them. Verification Receipt fields and automatic Runner stream
retention remain unchanged throughout.

Each privacy unit is Tier 2 and requires two independent reviews. TG-RNC.1 is
Tier 1 and requires one independent review for the narrow name change and its
package-identity impact. Use focused checks attributable to each unit, plus
the document/release contract and diff checks where affected. Update and check
the test-lane inventory only if its membership changes. This follow-up adds
no repeated full-suite, performance, platform, or exhaustive adversarial gate;
the repository's existing release/CI policy remains separate and unchanged.

<a id="tg-pmc-1"></a>

#### TG-PMC.1 — Nonsecret Numeric Metadata

Input: the current shared privacy guard and independent Evidence test oracle.
Scope/output: correct false rejection of the three numeric metadata keys
`max_tokens`, `token_count`, and `password_length`. Define their exact numeric
syntax in the affected current privacy documentation, and update the matcher,
directly coupled tests, independent oracle policy, and package manifest in the
same unit. Representative accepted values are `max_tokens=4096`,
`token_count=1024`, and `password_length=12`.
Acceptance: these examples survive caller validation, stored readback, and a
representative completion-to-Evidence/oracle round trip. Existing credential
and dump regressions still reject; a recognized metadata span does not hide a
credential elsewhere in the input. Do not allow arbitrary similarly suffixed
keys or arbitrary nonnumeric values. Ambiguous token assignments and status
words are not made safe merely because the surrounding sentence describes a
fix. Do not globally weaken the common guard or add per-project exceptions.
Verification: focused `test_task_validation` coverage and the relevant existing
Evidence round-trip/oracle tests, including mixed allowed-and-rejected input;
affected document/release contract checks and `git diff --check`.

<a id="tg-pmc-2"></a>

#### TG-PMC.2 — Fully Redacted Credential Examples

Input: the matcher and accepted numeric cases from TG-PMC.1.
Scope/output: allow a complete literal `<redacted>` placeholder in a documented
credential-example form, including `Authorization: Bearer <redacted>`, while
continuing to inspect all remaining text. Update the current privacy docs,
shared guard, directly coupled tests, independent oracle policy, and manifest
together. This is recognition of already-redacted text, not an automatic
redactor or an invitation to supply a real credential.
Acceptance: the documented fully redacted example passes validation, readback,
and a representative Evidence/oracle round trip. Partial redaction, content
appended to the credential value, and a separate detectable credential remain
rejected. An arbitrary angle-bracket word, status word, or introductory verb
does not grant a whole-input bypass. TG-PMC.1 cases and existing unrelated
privacy/legacy-read behavior remain valid.
Verification: focused placeholder-boundary and mixed-content tests alongside
the existing Task validation and relevant Evidence round-trip/oracle tests;
affected document/release contract checks and `git diff --check`.

<a id="tg-pmc-3"></a>

#### TG-PMC.3 — Short Diagnostic Quotation Policy

Input: the approved direction to admit limited manually entered diagnostic
quotations instead of rejecting solely on a stream heading. The exact allowed
fields, length/line boundary, and quotation format have not been decided.
Scope/output: produce one small policy recommendation in this pending section,
with concrete allowed/rejected examples, those unresolved choices, and the
exact current documentation/validator consumers that would need alignment.
Use `Investigate failure: stderr: permission denied` as a representative
useful case. Explain the benefit and residual disclosure risk. Any proposed
new cap must be justified against existing field limits and concrete needs,
not added merely to make the policy stricter.
Acceptance: the recommendation distinguishes manual short quotations from
automatic Runner capture and Verification Receipts; retains rejection of
detected credentials, log blocks, stack traces, environment dumps and large
diffs; and requires no new setting, classifier or routine approval. List the
remaining user decision explicitly. The Task is complete when that bounded
proposal is documented and reviewed; it need not wait for implementation.
This is design-only: do not activate changed privacy rules or create an
implementation Task automatically. A later explicit registration event based
on the accepted policy owns implementation. TG-PMC.1, TG-PMC.2 and TG-RNC.1
do not wait for this decision.
Verification: check the examples and affected-field inventory against current
validators; document-contract check and `git diff --check`; two independent
reviews of scope, tradeoffs and achievable acceptance, not a new test harness.

##### Active Contract — Manual Diagnostic Quotations

TG-PMC.4 activates the user-approved two-field, single-line manual diagnostic
quotation policy. Current behavior is owned by the specification's
[Privacy, Safety, And Stable Errors](docs/specification.md#privacy-safety-and-stable-errors)
and the design's
[Privacy, Safety, And Failure Boundaries](docs/design.md#privacy-safety-and-failure-boundaries).
Those owners define the exact form, privacy checks, consumer boundaries, and
non-expansion rules; the execution contract below preserves this Task's scope,
acceptance matrix, and gates without duplicating the active product contract.

<a id="tg-pmc-4"></a>

#### TG-PMC.4 — Manual Diagnostic Quotation Implementation

Input: the accepted manual diagnostic quotation policy above and the completed
TG-PMC.1/TG-PMC.2 privacy corrections. Scope/output: activate the exact
two-field, one-line form in the current privacy sections of the specification
and design, `tasks.py`'s common caller/stored validation, the existing
authority-snapshot and Review Packet consumer paths, and the independent
Evidence reader policy. Add focused caller, stored-read,
completion-to-Evidence, Review Packet, and independent-reader tests, and
synchronize affected package manifest digests and the existing test-lane
inventory if membership changes.
Do not create a schema migration, a new configuration or mode, an automatic
redactor, a semantic classifier, or a shared implementation dependency between
the production matcher and independent test oracle.

Acceptance: the two accepted examples pass unchanged as Task `title` and
`description` through caller validation, stored readback, Review Packet where
applicable, and a representative completion-to-Evidence/oracle round trip. No
other field gains this quotation exception: every field that currently treats
raw-output headings strictly continues to reject the same text, while fields
outside that strict set retain their existing behavior. Bare headings, multiple
headings, and other malformed candidates receive no new quotation exception;
pre-existing field-specific behavior, including the benign-title allowance,
remains unchanged. Empty context or quotation, line boundaries, trailing line
breaks, detected credentials, stack frames, log blocks, environment dumps, and
raw/large diffs remain rejected in the governed title/description form. The full
original field and extracted body are both checked, with no recursive quotation
exception.
Existing numeric metadata,
fully redacted Bearer, ordinary multiline description, strict raw-output, and
legacy-read cases remain valid. Current valid stored bytes and sealed Evidence
bytes are not rewritten. Public CLI/JSON shape, error codes/messages, field
limits, Verification Receipts, Runner capture, review/completion gates, and
target-project mutation behavior are unchanged.

Verification: run focused Task/privacy, stored consumer, Review Packet,
completion-to-Evidence, and independent reader/oracle tests containing the
complete accepted/rejected matrix; run the document contract, release contract,
test-lane check when membership changes, and `git diff --check`. Use synthetic
content and isolated test state. Do not add a full-suite, platform matrix,
adversarial campaign, performance gate, live Runner, external CI dispatch, or
new test framework to this unit. This privacy behavior change is Tier 2 and
requires two independent exact-target reviews with no blocking findings.

<a id="tg-rnc-1"></a>

#### TG-RNC.1 — Optional Runner Internal Entrypoint Name

Input: the existing optional Runner service entrypoint and its CLI/test callers.
Scope/output: rename `set_review_target_with_shadow_runner` to
`set_review_target_with_optional_runner` in the service, its export list, CLI
call site and directly referring tests; synchronize the package manifest.
Acceptance: callers use the new name with unchanged public CLI, JSON, schema,
gate behavior and persistence. Preserve historical migration names, sandbox
tables/fields, digest domains/keys and old fixtures; do not chase every
occurrence of "shadow" or "sandbox". Package-byte identity changes continue to
make prior Runner qualification stale by the existing rules while keeping old
evidence readable; do not bypass that check or re-run the live project here.
Verification: exact-symbol reference check, focused tests in the existing
Runner Plan CLI/service/gate/acceptance modules and the existing identity-change
regression; release/document contract checks and `git diff --check`. Do not
add a new native-process or sandbox qualification matrix for a name change.

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
