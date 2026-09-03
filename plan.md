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

<a id="m23-retirement"></a>

### Approved Pending M23 Retirement And Evidence Test Oracle

The approved outcome is to retire the M23 Analyzer subsystem and retain only
its independent Evidence-reading/validation capability, necessary test helpers,
and the small non-authority/compatibility boundary below. The retained reader
belongs to repository tests, not the installable runtime. This section owns the
pending static execution contracts; the Task database owns status and evidence.
Registration does not start implementation. Until TG-M23R.10's atomic switch,
the current Analyzer specification and design remain applicable to the existing
runtime. Preparatory Tasks leave that runtime and its dedicated tests working.

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
