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

Schema v22 uses the approved [reservation cleanup](#m23-schema-cleanup):
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

<a id="m23-retirement"></a>

### M23 Retirement And Evidence Test Oracle

The approved outcome is to retire the M23 Analyzer subsystem and retain only
its independent Evidence-reading/validation capability, necessary test helpers,
and the small non-authority/compatibility boundary below. The retained reader
belongs to repository tests, not the installable runtime. This section owns the
static execution contracts; the Task database owns status and evidence.
TG-M23R.10 is the atomic runtime and active-authority retirement boundary.
The preparatory contracts below retain the Analyzer only before that boundary;
the current product excludes it and retains the test-only reader. Registration
alone does not authorize implementation or Git operations.

The retained capability checks Evidence index/Bundle file self-consistency,
declared generation, versions, identities, canonical bytes/digests, relations,
and the existing bounded semantics/privacy rules. It does not authenticate
authors, prove source-code correctness, or establish freshness against SQLite.
It imports neither SQLite/storage nor the producing Evidence semantic validator.
Existing bounded filesystem primitives may be reused; independence does not
require reimplementing file I/O or Windows safety infrastructure.

The final product boundary retains the rule that facts, caller declarations,
LLM inference, and uncertainty are not interchangeable, and that derived
explanations do not satisfy verification, review, or completion gates. It does
not retain a report schema, renderer, citation-generation subsystem, or future
Reporting adapter. Any later non-test reader path is a separate product decision.

Common scope and permission boundaries for every unit:

- Preserve schema v21, the public CLI/JSON and normal Skill loop, Bundle v1/v2
  and index formats/digests, existing durable rows, and Runner gate semantics.
  Keep `derived_analysis`, `llm_derived`, and `batch_analyzer` as writer-disabled
  compatibility vocabulary; retain unrelated `deterministically_derived` uses.
  Do not create a migration, rewrite existing evidence, or require all M23
  strings to disappear.
- Leave existing ignored `state/current/analysis/` artifacts untouched and
  inert after retirement. Do not add cleanup, import, relocation, or repair.
  Package replacement continues to preserve state and the three named local
  configuration files under `docs/release-install.md`.
- No new runtime reader, CLI, config, automatic execution, completion gate,
  live model, Linux support, or M24 redesign. Do not repair M23's v2 report
  pipeline before deleting it or transplant its Win32/process/outbox foundation.
- Preparation may copy the existing reader/codec into tests and adapt only the
  Analyzer-specific wrappers. Do not rewrite its validators wholesale, add a
  generic validation framework, or expand the existing threat model. Temporary
  duplicate runtime/test code and the old support facade retire at TG-M23R.10.
- This request authorizes formal planning and ready Task registration only.
  Later execution requires user approval of the sequence. Neither registration
  nor execution approval implies Git staging/commit/push, external CI, release,
  installation into a real project, or unrelated filesystem mutation.
  Existing Git-backed review/completion and tracked-inventory checks still need
  separately authorized exact Git material; do not weaken those checks or add a
  workaround when that approval is absent.

All units are sequential in lane `TG-M23R`, at orders 10 through 110 below.
Each consumes completed predecessor outputs and its cited current owners;
same-lane ordering represents the dependencies without a new dependency model.
This deliberately serializes the two schema-consumer migrations so their shared
fixtures and final cutover inventory have one resumable order.

| Unit / order | Bounded responsibility | Review |
|---|---|---|
| TG-M23R.1 / 10 | Separate reusable Evidence fixtures | Tier 1 |
| TG-M23R.2 / 20 | Preserve the independent canonical codec in tests | Tier 1 |
| TG-M23R.3 / 30 | Extract the neutral test-only Evidence reader | Tier 1 |
| TG-M23R.4 / 40 | Preserve existing reader rejection/regression coverage | Tier 1 |
| TG-M23R.5 / 50 | Migrate schema-v20 Evidence test consumers | Tier 1 |
| TG-M23R.6 / 60 | Migrate schema-v21/Runner Evidence test consumers | Tier 1 |
| TG-M23R.7 / 70 | Exercise completion-to-JSON-to-reader integration | Tier 1 |
| TG-M23R.8 / 80 | Prepare package-replacement preservation coverage | Tier 1 |
| TG-M23R.9 / 90 | Prepare Runner implementation-identity compatibility coverage | Tier 1 |
| TG-M23R.10 / 100 | Retire runtime, packaging, and current authority atomically | Tier 2 |
| TG-M23R.11 / 110 | Run final offline integrated acceptance | Tier 2 |

<a id="tg-m23r-1"></a>

#### TG-M23R.1 — Reusable Evidence Fixtures

Scope: extract only Evidence constants, reference encoding/digest/seal helpers,
native/legacy Bundle/index builders, and ordinary tree snapshots from
`tests/m23_test_support.py` into `tests/evidence_test_support.py`. Keep Analyzer
helpers in the old support file and re-export moved names there until cutover.
Retain the existing M22 payload fixture; do not duplicate its implementation.

Acceptance: moved fixtures produce the same bytes/digests and serve both old
Analyzer tests and future reader tests. The fixture's `json.dumps` reference
encoder stays independent of the reader codec. Verification: focused fixture
parity and representative existing consumer tests; import/discovery checks for
the four M24 consumer modules named in units 5–7. No runtime edits.

<a id="tg-m23r-2"></a>

#### TG-M23R.2 — Independent Canonical Codec

Scope: copy the Evidence-needed pure canonical encoder/parser and minimum error
support from `analysis_contracts.py` into `tests/evidence_reader_codec.py`, with
focused tests. Keep the original runtime module unchanged; copy no descriptor,
recipe, job, packet, report, retry, or process contract.

Acceptance: existing literal canonical-byte vectors, duplicate-key, float,
noncanonical, and invalid-UTF-8 checks pass independently of the producer and
fixture encoder. Verification: the new focused codec tests and lane discovery.

<a id="tg-m23r-3"></a>

#### TG-M23R.3 — Neutral Test-Only Reader

Scope: copy the existing `evidence_consumer.py` read/validation core into
`tests/evidence_reader_oracle.py`, using unit 2's codec. Replace only its
Analyzer-dependent source wrapper/basis validation, remove descriptor replay,
and preserve the full selected index entry, including v2
`bundle_format_version`. Keep the runtime consumer unchanged.

Acceptance: v1 native/legacy and one existing v2 fixture can be read without
changing source files; the neutral result preserves index version, selected
entry, and exact Bundle envelope or explicit legacy absence. There is no
Analyzer, storage, producer-validator, packet, or report dependency. Existing
`state_paths` bounded read primitives remain reusable. Verification: focused
positive/read-only/binding tests, import-boundary review, and lane discovery.
This is a test helper, not a new public schema or API.

<a id="tg-m23r-4"></a>

#### TG-M23R.4 — Reader Regression Coverage

Scope: adapt the Evidence-only cases from
`tests/test_m232_evidence_consumer.py` into a neutral reader test module.
Preserve canonical/literal digest, resealed semantic tamper, relation, path,
provenance, privacy, timestamp, and no-write checks against unit 3's reader.
Do not carry over the descriptor-replay case; keep the original M23 tests until
unit 10. Correct a defect in the extracted copy only within those existing
Evidence requirements.

Acceptance: the retained cases detect the same supported-format regressions
without importing Analyzer runtime. Verification: neutral reader/codec tests
and lane discovery. No exhaustive new adversarial matrix or producer hardening.

<a id="tg-m23r-5"></a>

#### TG-M23R.5 — Schema-v20 Test Consumers

Scope: switch Evidence fixture/reader uses in
`tests/test_m242_evidence_compatibility.py` and
`tests/test_m242_r3b_schema20_activation.py` to the neutral helpers. Retain the
old descriptor-replay block and only its required imports until unit 10.

Acceptance: schema-v20 null-Runner v2, v2 index referencing unchanged v1 Bundle,
and legacy null/absence retain their existing meaning and version information.
Verification: the affected compatibility/activation cases plus neutral reader
tests and lane discovery. No migration, storage, or projection implementation
change and no new Analyzer acceptance.

<a id="tg-m23r-6"></a>

#### TG-M23R.6 — Schema-v21 And Runner Test Consumers

Scope: migrate reader/fixture uses in
`tests/test_m243b_schema21_compatibility.py` to the neutral helpers. Use existing
fixtures to cover source-v21 v2 caller-attestation, not-required, and qualifying
Runner-observation forms; preserve source-schema/index binding and existing
Runner wrong-type/identifier rejection checks.

Acceptance: the full v2 entry and sanitized Runner projection survive the
reader without schema downgrade or source mutation. Verification: affected
reader compatibility cases, the existing stored-history canaries affected by
helper changes, and lane discovery. Do not change Runner gates, storage,
recovery, or the producer to satisfy the oracle.

<a id="tg-m23r-7"></a>

#### TG-M23R.7 — Completion-To-Reader Integration

Scope: adapt `tests/test_m244b_legacy_fresh_acceptance.py` so its existing
isolated completion and Evidence publication fixtures feed the independent
reader directly. Keep the old packet-only block and its minimal imports until
unit 10; it is not the new reader's acceptance endpoint.

Acceptance: existing v19-to-v20-to-v21 history preservation and fresh v21
completion-to-Bundle/index-to-reader checks pass, including the Runner-backed
and not-required fixtures. Assert preserved version/basis and before/after
source snapshots. Verification: this focused integration module and its
directly changed helper tests. No report, explanation format, or runtime route.

<a id="tg-m23r-8"></a>

#### TG-M23R.8 — Package Replacement Preservation

Scope: reuse `tests/test_m19_legacy_upgrade_rehearsal.py` package-overlay and
physical-install helpers for a bounded replacement regression. Cover exact
manifest-owned core replacement while preserving existing database/Evidence,
an inert analysis-artifact sentinel, and the three supported config files.
Keep additions test-only and locally owned; no installer or cleanup feature.
Adapt the shared test inventory helper to the candidate manifest where its
current `git ls-files` selection would require already-deleted files, and check
its existing callers. The replacement regression itself must not require Git
staging; the release checker's existing tracked-inventory gate is unchanged.

Acceptance: the test passes before and after retirement by comparing installed
core with the candidate's actual inventory, not a fixed M23 file count. State
and config bytes remain unchanged by package replacement; obsolete nonmanifest
core is not retained. Verification: the focused replacement case and affected
helper tests, with lane discovery if a test module is added. Do not require a
new historical binary/archive, a live project upgrade, or another schema matrix.

<a id="tg-m23r-9"></a>

#### TG-M23R.9 — Runner Identity Compatibility

Scope: add only missing focused assertions to existing Runner runtime/gate and
schema-v21 history tests, reusing their disposable package/graph fixtures.
Exercise a valid changed manifest implementation identity, live-basis staleness,
and a completed historical Runner graph/Bundle retaining its captured identity.

Acceptance: package changes require the existing fresh live target basis;
historical completion still reads without rebinding to the installed digest.
No immutable observation/Bundle is rewritten or promoted. Verification: the
focused identity/live-stale/history cases. Reuse already sufficient cases rather
than duplicate them. The existing public Plan-to-`runner_pass` canary is run in
unit 11; do not add another native-process harness or change Runner behavior.

<a id="tg-m23r-10"></a>

#### TG-M23R.10 — Atomic Runtime And Authority Retirement

Scope: after units 1–9, remove `analysis_contracts.py`, `analysis_packet.py`,
`analysis_outbox.py`, `analysis_validator.py`, `analysis_renderer.py`,
`analysis_worker.py`, `codex_analysis_adapter.py`, `_analysis_windows_process.py`,
`_analysis_win32.py`, and the packaged `evidence_consumer.py`. Remove the now
superseded M23 dedicated tests/support, the two temporary M24 Analyzer blocks,
Analyzer-only `state_paths.py`/`state_resolver.py` names and fields, and their
manifest/test-lane entries. Refresh hashes for actually changed packaged files.

In the same reviewed revision, capture the retiring current Analyzer sections
with their exact source commit and non-authoritative banner into a new document
indexed by `docs/history/README.md`; do not alter old captures or archive runtime
code separately. Replace current M23 support/process/report contracts in
`docs/specification.md` and `docs/design.md` with the short Evidence
non-authority/compatibility rule and test-oracle ownership. Synchronize direct
current references in README, candidate release/install notes, plan, and
document-contract tests where required. Keep the existing authority registry;
add no new conditional owner or AGENTS contract inventory.

Acceptance: runtime removal, exact package/test inventory, history indexing,
and active-document switch form one coherent change, never separate committed
intermediate states. The retained reader tests and four M24 modules import and
pass; no live runtime consumer depends on a deleted module. Reserved vocabulary,
schema/data, supported config, and old analysis artifacts remain unchanged.
Verification: affected reader/M24/resolver/replacement/identity tests, document
and release contract checkers, test-lane check, and `git diff --check`. Tier 2
requires two independent reviews of that exact switch. Full suite belongs to
unit 11, not every preparatory Task. Fix only direct cutover defects here.

<a id="tg-m23r-11"></a>

#### TG-M23R.11 — Final Offline Acceptance

Scope: acceptance only of the exact retired candidate. Run the existing full
offline suite through `python -B tools/test_lanes.py --repo . --lane all`, plus
document/release contract, lane-inventory, and diff checks. Confirm the suite
includes the retained reader matrix, replacement/history coverage, and existing
public Plan-to-`runner_pass` Windows canary; included tests need no duplicate run.

Acceptance: all required local checks pass and two independent Tier 2 reviews
find no blocking regression or scope expansion. Report actual environment and
results without claiming GitHub CI or publication. Corrections return to their
owning unit through the existing workflow; this Task does not authorize broad
repair, weaker tests, skipped native acceptance, or new implementation. A
failure is evidence for bounded diagnosis, not authority to enlarge this plan.

Across the sequence, each Tier 1 unit needs one independent review and Tier 2
needs two. New test modules are assigned once in `tools/test_lanes.py` and the
partition is checked. Per-unit verification is focused on changed behavior;
the full suite is reserved for unit 11. No numeric file/line/time limit, repeated
whole-project audit, new mandatory worksheet, or speculative safety gate is
added to make a Task complete.

<a id="m23-schema-cleanup"></a>

### M23 Retirement Follow-Up — Schema Reservation Cleanup

This approved follow-up removes the retired Analyzer's unused vocabulary from
the current schema, not the shared Evidence tables or their valid records.
It follows TG-M23R.11 in the same `TG-M23R` sequential lane, at orders
120–190. The eight TG-M23S units below own future execution detail; the Task
database alone owns their status. Registration authorizes this plan and ready
Task records, not implementation, migration of the self-host database, Git
operations, external CI, installation, or release.

The existing TG-M23R.1–11 contracts remain unchanged. Their schema-v21 and
reserved-vocabulary preservation rule is the predecessor boundary, not a
prohibition on this separately approved follow-up. Current supported behavior
stays schema v21 until TG-M23S.7 activates the prepared schema v22 change and
synchronizes the applicable specification, design, and candidate documents.

#### Outcome And Bounded Compatibility

- Remove `derived_analysis` from the source-kind/relation CHECK allow-lists,
  `llm_derived` from assurance allow-lists, and `batch_analyzer` from producer
  allow-lists in current `evidence_references` and `criterion_evidence_links`.
  Remove corresponding unused current enum/order/dispatch reservations.
  Keep the tables, columns, valid source kinds, `deterministically_derived`,
  and the independent test-only Evidence reader.
- Preserve supported old-schema definitions and migration/read compatibility.
  Legacy DDL, rejection tests, and historical explanations may still name the
  retired values. No repository-wide zero-string test or unrelated schema,
  handoff, source-file, or document decomposition is part of this cleanup.
- Use one forward migration from complete schema v21 to v22. The physical
  rebuild includes the two Evidence tables and
  `completion_evidence_bundles`, whose source-version CHECK must admit new
  source-22/v2 Bundles. Restore their owned indexes/triggers and the coupled
  completion-cycle guard. No business column/table or Runner protocol is added.
- Preserve existing business rows, IDs, relations, provenance, completed
  history, and sealed Bundle payloads/source versions/digests. Existing Bundle
  files remain byte-identical. A regenerated index truthfully reports source
  schema 22 and may change bytes/digest while referencing those same old
  Bundles. New Bundles are source-22/format-2; index format 2, Bundle digest
  domains, Viewer snapshot 4, CLI shapes, and verification/review rules remain.
- Existing writers cannot create the retired values. Migration validates its
  actual source using the existing storage boundaries; unexpected reserved-value
  rows are rejected without deletion, conversion, or inferred replacement.
  An unowned index/trigger attached to one of the three rebuilt tables is
  rejected before rebuild rather than silently lost or replayed. Unrelated
  objects retain the existing preservation policy. No live-project census or
  manual cleanup is a prerequisite for implementing this migration.
- Reuse the established transaction, integrity/foreign-key validation,
  marker-last, rollback, and connection-setting restoration patterns. Rollback
  means logical schema/data preservation, not byte-identical SQLite files.
  Explicit setup retains the existing pre-migration backup and paired-rollback
  contract; ordinary commands never migrate or repair state.
- Preserve M24 manual/not-required/Runner branches, no-relaunch recovery,
  live implementation-drift invalidation, and self-contained historical Runner
  evidence. This is version compatibility, not a new Runner gate or safety
  framework. No Analyzer resurrection, model call, new runtime reader, CLI,
  config, normal-loop call, platform port, or general migration framework.

Preparation uses the existing explicit-connection helpers and isolated test
databases. Units 1–6 keep the public current-schema target and ordinary schema-21
behavior unchanged; they may extend narrow version-aware helpers for explicit
schema-22 tests, but introduce no rehearsal command, alternate state mode, or
parallel candidate runtime. Each packaged-code change refreshes its actual
manifest digests, and each new test module is classified once in the existing
test lanes. Tests for legacy schema 21 remain explicit old-schema tests rather
than being indiscriminately rewritten to expect 22.

All eight units are Tier 2 because they change schema, its coupled contracts or
write/gate boundaries, or final acceptance. Each requires two independent
reviews of its own target. Units 1–7 use focused checks for the changed boundary
plus applicable document/release/lane and diff checks. The existing full offline
suite runs in unit 8, not at every preparation boundary. Reuse sufficient
existing tests; add cases only for new version/rebuild behavior, not a fresh
cross-product of every legacy fault. No numeric size/time gate or repeated
whole-project audit is added.

<a id="tg-m23s-1"></a>

#### TG-M23S.1 — Schema-v22 Definitions

Input: the accepted M23 retirement and current schema-v21 definitions.
Scope: define the three v22 replacement tables, their owned indexes/triggers,
the coupled cycle guard, and exact schema-object recognition in `storage.py`.
Keep old version-specific SQL available for source admission and migration.
Output: a privately constructible, recognizable v22 physical schema with the
six reservation allow-lists narrowed and source-22/v2 Bundle admission.
Acceptance: focused temporary-database DDL tests prove the intended CHECK/FK/
index/trigger changes and rejection of the removed values, with unchanged
business columns and unchanged schema-21 definitions/public initialization.
No migration algorithm, public activation, or broad storage refactor.

<a id="tg-m23s-2"></a>

#### TG-M23S.2 — Stored Evidence And History Validation

Input: unit 1's exact schema definitions.
Scope: make existing storage-owned Evidence, Bundle, cycle, and Runner-graph
validators explicitly understand schema 22 with the same schema-21 semantic
rules and narrowed current vocabulary. Preserve old source-version validation.
Output: validation of a schema-22 database containing retained source-19/20/21
Bundles/history, including its existing storage-owned projection-basis capture,
without a public schema switch. New source-22 payload validation is unit 4.
Before unit 5 enables explicit v22 Task capabilities, storage maps physical v22
to the unchanged v21 stored-Task field/privacy capabilities; its container identity stays
22. This does not enable current Task lifecycle or Runner selection on v22.
Selected history retains the source-19/v1 null verification-basis tag as
non-Runner history; the subsequent Bundle validation still checks its sealed
source/version and complete basis without promoting it.
Acceptance: focused valid/corrupt fixture tests cover retained
manual and Runner history, source-version discrimination, reserved-value
rejection, and the existing no-promotion/no-privacy-upgrade boundary. Exact-21
guards must neither wrongly reject 22 nor silently skip its history checks.
Current Task lifecycle/Runner selection belongs to unit 5; no new gate matrix.

<a id="tg-m23s-3"></a>

#### TG-M23S.3 — Transactional Schema-v21 To v22 Migration

Input: units 1–2's physical definitions and stored-state validators.
Scope: implement the one explicit-connection migration/reentry path, rebuilding
only the three declared tables and coupled owned objects. Preserve accepted
source rows and unrelated objects, reject unexpected reserved rows or attached
unowned objects before rebuild, and record the new marker last.
Output: tested migration callable independently of public setup activation;
its populated fixtures retain source-19/20/21 Bundles, not new source-22 output.
The private connection helper retains the established foreign-key-off /
legacy-alter-on transaction pattern. It uses only `evidence_references_v21`,
`criterion_evidence_links_v21`, and `completion_evidence_bundles_v21` as
temporary table names, restores the coupled Evidence cycle guard, and compares
all existing business rows plus object SQL outside those replacements before
the marker-last validation/commit. Row preservation is checked again after the
marker, including effects of admitted unrelated triggers. Exact-v22 reentry
rejects attached unowned objects and temporary-name residue without repairing
either.
Acceptance: representative populated v21 Evidence/Runner history survives;
fresh construction reaches the same v22 owned schema; exact-v22 reentry is
validation-only; representative failures during rebuild and before commit
roll back schema/data and restore connection settings. Run quick/FK checks and
preservation assertions, including sealed Bundle payload/digest identity.
No reverse migration, data deletion/repair, live database edit, or exhaustive
fault-injection framework. Public migration dispatch remains unchanged here.

<a id="tg-m23s-4"></a>

#### TG-M23S.4 — Evidence Projection And Independent Reader Compatibility

Input: migrated v22 fixtures from unit 3 and the retained test-only reader.
Scope: extend `evidence_projection.py` and the independent test reader/support
to admit source-22/v2 Bundle/index material without a new format or domain,
including the coupled storage validation of new source-22 payloads.
Output: pure fixtures/builders generate and independently validate new
schema-22 Evidence; actual Task completion-writer integration is unit 5.
A new index can reference unchanged source-19/v1 and source-20/21/v2 Bundles.
Pure rendering admits the explicit schema-22 container while the public target
remains 21; index identity comes from that container and Bundle identity from
each sealed row. Shared stored-row validation admits source-22 Bundles only in
a physical schema-22 container, including selected-history reads, without
changing the current completion writer or adding per-Bundle schema reads.
Acceptance: focused producer-to-oracle tests cover manual/not-required/Runner
arms, truthful source versions, old Bundle bytes/digests, index refresh, and
representative version/digest rejection. The oracle does not import the
producer's semantic validator or SQLite. No report, runtime-reader route,
new publication mechanism, or requirement to preserve regenerated index bytes.

<a id="tg-m23s-5"></a>

#### TG-M23S.5 — Task Lifecycle And Runner Schema Compatibility

Input: units 2–4's schema-aware validation, migration, and Evidence encoding.
Scope: adapt existing current-state Task/verification/completion/Runner
repository paths to schema 22 and derive new Bundle source version from the
actual database. Remove exact-21 assumptions only where they represent the
same supported gate capability. Output: existing lifecycle and selector work
on explicit v22 test databases while ordinary CLI remains on v21.
Stored Task capabilities admit v22 directly, retiring unit 2's temporary
v22-to-v21 field-validation bridge while preserving the older v20 bridge.
The locked completion basis carries its validated physical schema version;
both the native JSON payload and stored Bundle use that same value. No caller
selects the version and no sealed historical Bundle is relabelled.
Acceptance: focused tests cover manual Receipt, not-required, qualifying Runner
pass, the existing fallback/blocking classifications, reopen/fresh targeting,
and retained historical Runner evidence. Reuse current gate matrices and
implementation-drift tests; do not add a native process harness or alter Plan,
process, cleanup, receipt, review, or completion semantics.

<a id="tg-m23s-6"></a>

#### TG-M23S.6 — Setup And Projection Consumer Compatibility

Input: units 1–5's explicit schema-22 database and lifecycle support.
Scope: prepare source-schema-22 validation/dispatch in setup, doctor, backup,
recovery, resolver/relocation, and Viewer helpers. Keep old source handling and
the public migration target at 21 until unit 7.
Output: isolated v22 consumer fixtures work through the existing boundaries.
The public current/newer-schema guards remain at 21. Tests initialize 21 and
privately migrate their disposable database before locally substituting only
the needed consumer schema constants to exercise the prepared 22 dispatch.
They do not replace validators or classifications, create another runtime or
state path, or claim public activation; an unmodified public entry still
rejects 22 until unit 7. Recovery retains the existing deferred Task-only
verification rejection after structural checks, and Viewer reuses its exact
validated-batch proof without changing snapshot v4 or current-artifact rules.
Acceptance: representative schema-22 backup/recovery, read-only diagnosis,
Viewer-v4, and relocation source-version tests pass with unchanged public
fields, candidate-local versus set-fatal error handling, config preservation,
and no-relaunch behavior. Reuse existing recovery and Viewer fixtures rather
than duplicating their complete matrices. No new maintenance policy, recovery
algorithm, artifact type, configuration, or live self-host setup.

<a id="tg-m23s-7"></a>

#### TG-M23S.7 — Atomic Current-Schema Activation And Residue Removal

Input: the independently verified outputs of units 1–6.
Scope: switch `SCHEMA_VERSION` and all supported setup migration/reentry
dispatch paths to 22, including the current early returns for source 20/21.
Remove remaining current-only Analyzer enum/order reservations while retaining
necessary legacy compatibility and negative tests. Synchronize the exact
affected specification/design sections, `plan.md` current decisions (not the
unchanged TG-M23R.1–11 contracts), README, CLI reference, candidate
release/install notes, manifest, and version-sensitive contract tests. Align
current-schema wording in `SKILL.md` and `references/task_workflow.md` without
changing their triggers, procedures, or normal-loop calls.
Output: one coherent current schema-22 candidate with no partial public cutover.
Acceptance: fresh setup, populated v21 upgrade, v22 no-write reentry, new
completion-to-Evidence/oracle, and Viewer/backup canaries pass; package and
document versions agree. Current definitions reject all three retired values;
valid evidence and old compatibility still work. This unit wires prepared
work and fixes direct integration defects, not a deferred migration/consumer
implementation backlog. No release, Git mutation, or real self-host migration
is authorized by this registration.

<a id="tg-m23s-8"></a>

#### TG-M23S.8 — Final Schema Cleanup Acceptance

Input: unit 7's coherent current candidate.
Scope: run `python -B tools/test_lanes.py --repo . --lane all` and the existing
document/release/lane/diff checks, including isolated package upgrade and
matched rollback coverage. Acceptance: all required local checks and two
independent reviews pass; existing Evidence/Task identities, old Bundle bytes,
the three supported configs, and historical Runner behavior are preserved.
Confirm the existing public Plan-to-`runner_pass` canary is included, without
duplicating its execution or claiming external CI/publication. Report remaining
legacy vocabulary by its compatibility owner, not as failed physical cleanup.
This is acceptance, not new implementation or permission to weaken a test;
direct defects follow the existing bounded repair/reconciliation workflow.

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

<a id="python-314-exception-reporting"></a>

### Approved Follow-Up — Python 3.14 Exception Reporting

The user approved task registration for the bounded exception-compatibility
correction below. Python 3.14 unittest reporting can fail while updating a
chained frozen exception's traceback. In the observed storage boundary,
`TaskValidationError` is the frozen cause; `StorageError` itself is not frozen.
The intended outcome is to report the original error and continue subsequent
tests, not to suppress an error or convert a failing test into a pass.

Keep the specification's "Package, Runtime, And Generated State", "JSON, Text,
Limits, And Exit Status", and "Privacy, Safety, And Stable Errors" unchanged.
The design's "Runtime Module Boundaries", "Test-Only Independent Evidence
Reader", and "Validation And Test Design" govern ownership and verification.
This is not a Python-version increase, privacy-policy revision, schema change,
CLI/JSON change, Runner behavior change, or exception-framework redesign.
Registration alone starts no implementation, Git operation, live Runner,
configuration publication, setup/migration, external CI, or release.

The finite scope is 15 production exception classes and two test-only exception
classes. Use ordinary traceback-compatible dataclasses, following the existing
`VerificationRunnerPlanError` precedent. Preserve class identities, inheritance,
constructor fields/defaults, field equality, string rendering, sanitized error
fields, and exception chaining. Also preserve `ArtifactLockError.contended`
and `VerificationRunnerRuntimeError.handle_cleanup_state` / `handles_closed`.
Removing frozen exception-field enforcement and its generated hash is intended;
current inspection found no hash-container or immutability dependency on these
errors. Do not introduce unsafe hashing or a new exception base to preserve an
unused property. Normal immutable data records remain frozen. Keep the Evidence
oracle/codec independent from storage and production semantic validators.

Use sequential lane `TG-PY314`, orders 10, 20, and 30. The first unit establishes
the actual-route regression and a small parameterized test pattern; later units
extend it with their own exception cases. Each unit leaves its own errors fixed
and has its own attributable tests, manifest synchronization, and one independent
Tier 1 review. Later coverage never substitutes for an earlier unit's gate.
The lane has no dependency on TG-PMC, TG-RNC, or future cleanup. Shared-file
edits must preserve those Tasks' unrelated work; no other lane is paused or
reordered by this registration. The completed RC-CI.2 is not reopened.

Common verification is direct traceback compatibility, chained-error reporting
through standard unittest APIs, continuation to a following test, and unchanged
error attributes. A synthetic inner test must still be reported as an error;
the enclosing regression asserts that behavior without leaving a failing test
in standard discovery. Avoid exact traceback-text matching, standard-library
patches, swallowed failures, and a new generic test framework. Use an injected
sanitized validation failure at the actual storage helper boundary rather than
making a short diagnostic phrase's rejection a permanent privacy requirement.

Run the focused reporting regressions on Python 3.14. Run the same small tests
on 3.12 if already available; otherwise report that local coverage limitation
and retain the existing CI policy, without downloading an interpreter or adding
a new platform gate. Put new small reporting coverage in the existing `fast`
lane so the current PR policy exercises it on 3.14. Update/check lane membership
if a new module is added. Each unit also runs focused adjacent tests, the release
contract check, and `git diff --check`; run the document checker if documents
change. No repeated full-suite, performance campaign, native-process matrix,
external CI dispatch, or live-project Runner run is added to these local gates.
The existing release-candidate aggregate CI requirement remains separate.

<a id="tg-py314-1"></a>

#### TG-PY314.1 — Task And Review Exception Compatibility

Input: current Task/review exceptions and the existing Runner Plan exception
regression as a pattern. Scope/output: fix `TaskValidationError` and
`TaskRepositoryError` in `tasks.py`, `CompletionEvidenceError` in `completion.py`,
`HandoffError` in `handoffs.py`, `ReviewPacketError` in `review_packet.py`,
`ReviewProvenanceError` in `review_provenance.py`, and `VerificationReceiptError`
in `verification_receipts.py`; synchronize affected release-manifest digests.
Add the actual `_validate_evidence_ledger_stored_privacy` chained-error regression
and the seven exception cases in a small reporting test module or existing
focused tests. No storage or privacy algorithm change is required.
Acceptance: the original sanitized storage error is reported, a following test
runs, and the seven exception classes meet the common compatibility checks.
Verification: reporting cases plus focused existing Task validation, completion,
handoff, review-packet/provenance, and Receipt error tests; common checks and
one independent Tier 1 review. Later units' still-frozen errors are not this
unit's completion condition.

<a id="tg-py314-2"></a>

#### TG-PY314.2 — Evidence And Artifact Exception Compatibility

Input: TG-PY314.1's verified reporting pattern. Scope/output: fix
`ArtifactLockError` in `artifact_lock.py`, `ArtifactManifestError` in
`artifact_manifest.py`, `EvidenceLedgerError` in `evidence_ledger.py`,
`EvidenceProjectionError` in `evidence_projection.py`, and `ViewerError` in
`viewer.py`; also fix test-only `EvidenceConsumerError` in
`tests/evidence_reader_oracle.py` and `EvidenceCodecError` in
`tests/evidence_reader_codec.py`. Extend reporting cases and synchronize the
five packaged modules' manifest digests. Do not alter file publication, locking,
Bundle validation, privacy policy, Viewer behavior, or oracle independence.
Acceptance: all seven added cases meet the common checks, including unchanged
lock-contention classification and test-reader rejection attributes.
Verification: reporting cases and focused existing artifact, Evidence, Viewer
error-path, and independent reader/codec tests; common checks and one independent
Tier 1 review. No exhaustive I/O race or recovery matrix is added.

<a id="tg-py314-3"></a>

#### TG-PY314.3 — Git And Runner Exception Compatibility

Input: the reporting coverage from TG-PY314.1 and TG-PY314.2. Scope/output: fix
`GitSnapshotError` in `git_snapshot.py`, `VerificationRunnerGitError` in
`verification_runner_git.py`, and `VerificationRunnerRuntimeError` in
`verification_runner_runtime.py`; extend reporting cases and synchronize their
manifest digests. Preserve cleanup-state fields/properties, Git observation,
Runner qualification, and normal package-identity invalidation of old evidence.
Acceptance: the three new cases pass and the small aggregate reporting test
covers the full 15-production/two-test-only set with earlier cases still passing.
The already-compatible `VerificationRunnerPlanError` regression remains valid;
normal frozen data-record assertions still pass. No broad exception or module
refactor is included. Verification: aggregate reporting cases, focused existing
Git snapshot/Runner Git/runtime error and cleanup-state tests, existing Runner
Plan traceback and relevant package-identity regressions, common checks, and
one independent Tier 1 review. Report the originating handoff's fix status but
do not withdraw it without explicit user direction.

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
