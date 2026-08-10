# task-governance-tool Current Decisions And Open Issues

Decision baseline: v0.10.0 is the immutable published release; its exact
identity lives in `docs/release-install.md`. The current unpublished local
candidate is v0.12.0/schema v19/Viewer snapshot v4 with source compatibility
v5-v19 and 21 public command leaves. This plan retains current decisions,
unfinished static contracts, and open issues only. Completed execution
narrative is indexed as non-authoritative history, while the Task database,
queried through the public CLI, solely owns live execution status and evidence.

This file owns current decisions, explicit open issues, and user-decision
gates. It is not the product contract, execution ledger, or evidence store:

- [`docs/specification.md`](docs/specification.md) owns product behavior.
- [`docs/design.md`](docs/design.md) owns implementation structure.
- [`docs/authority.md`](docs/authority.md) owns the concise mandatory and
  selective read routing; its execution registry points to exact current or
  inactive detail without transferring product/design ownership or activating
  an inactive unit.
- This plan owns current decisions, open issues, cross-sequence gateways, and
  static contracts not delegated by `docs/authority.md`. It is not a progress
  table. Indexed execution contracts own their routed current or inactive unit
  detail.
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
- The current closed mandatory-native set contains exactly
  `test_m241a_lpac_portability.RunnerLpacPortabilityNativeTests.test_real_lpac_portability_matrix_and_cleanup`.
  When selected in
  `integration` or `all`, its `unittest` SKIP makes that lane non-PASS.
  Missing, renamed, duplicate, or non-`integration` assignment fails policy
  validation; lanes that do not select it and unrelated optional SKIPs retain
  their existing meaning. The portability matrix is a direct private-seam
  proof of both separate Jobs/tokens, the allow/deny/allow triple, no-resume
  paths, and cleanup; it does not call `run_process_steps` or depend on the
  full Runner process/registry matrix. That full matrix remains an inactive
  TG-M24.2 completion gate and is not part of the repository mandatory-native
  set. After accepted TG-M24.1B releases it, TG-M24.2 must add its stable ID
  with non-SKIP enforcement before acceptance;
  until then, the accepted standalone portability ID remains the complete set.

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

The schema-v18-origin Verification Receipt behavior retained by current schema
v19 is defined by the active [specification](docs/specification.md) and
[design](docs/design.md). Completed
M21 design, activation, acceptance, and correction narrative is preserved only
in [indexed non-authoritative history](docs/history/v0.11.0/pre-m22-completed-execution.md).
That history supplies no current gate or implementation authority.

<a id="tg-m22-1a"></a>

### Current Schema-v19 Evidence Bundle And JSON Decision

The v0.12.0 candidate retains the accepted TG-M22.2 capture foundation,
TG-M21.5 admission boundary, and TG-M22.3 schema-v19 immutable native
completion Bundles, criterion links and Finding snapshots, plus the
fixed one-way Evidence JSON v1 projection. Pre-v19 cycles remain index-only as
`legacy_unknown`; SQLite remains canonical. Setup repairs the projection,
doctor observes it read-only, and post-commit maintenance runs Evidence,
Viewer, then backup. Viewer snapshot v4 accepts v5-v19 but adds no Evidence UI.
No Runner, public leaf, normal-loop call, network/live-model action, or target
mutation is activated. Accepted TG-M22.4 completed integrated acceptance and
accepted TG-M23.1 froze the derived-evidence design; accepted TG-M23.2 supplies
only its bounded offline/mock implementation predecessor.

### Accepted TG-M22.4/TG-M23/TG-M24.1/TG-M24.1A, Current TG-M24.1B, And Inactive Later TG-M24 Gateway

Product behavior established by accepted TG-M22.3 remains owned by the active
specification and its implementation structure by the active design. Exact
TG-M22.1A/TG-M22.2/TG-M21.5/TG-M22.3/TG-M22.4 accepted-predecessor detail is
owned at the stable
[TG-M22 sequence anchor](docs/execution-contracts/tg-m22-evidence-ledger.md#tg-m22-sequence).
Accepted TG-M23.1 design, bounded TG-M23.2 implementation, and TG-M23.3
offline/mock integrated-acceptance detail are owned and routed by the [TG-M23 mixed execution contract](docs/execution-contracts/tg-m23-derived-evidence.md#tg-m23-derived-evidence). Its exact Windows process-containment, private-temp, and atomic-publication/recovery seam is delegated without overlapping unit ownership to the [TG-M23 process-safety contract](docs/execution-contracts/tg-m23-process-safety.md#tg-m23-process-safety).
No TG-M23 unit is current. SQLite, `storage.py`, public CLI/Skill,
network/live-model action, gate mutation, and Task mutation remain outside the
accepted scope. Accepted TG-M24.1 freezes only the exact design in the routed
M24 owner, and its bounded TG-M24.1A correction is also accepted. Current
zero-capability LPAC runtime qualification-and-supply authority belongs to
TG-M24.1B, while TG-M24.2 through TG-M24.4 remain inactive. The permanent
correction retains class-46
proof where supported, adds only the exact error-87 public-`AccessCheck`
semantic route, gives the LPAC child and never-resumed normal control the same
four creation attributes with only the AAP-policy `DWORD` differing as `1`
versus `0`, and makes mandatory native SKIP non-PASS. Omission cannot define
the normal control, `WIN://NOALLAPPPKG` is not a decision input, and the forced
opposite route is fault-injected integration rather than native selector
evidence. Its semantic gate uses SYSTEM-owned/grouped descriptors with exactly
two non-generic bit-1 allow ACEs (coordinator user plus selected AAP or Package
SID), no extra ACE, and exact normal-AAP allow / LPAC-AAP deny / LPAC-Package
allow outcomes. TG-M24.1B may qualify and pin only the first passing runtime
candidate under its routed safety and decision gates. After accepted TG-M24.1B,
TG-M24.2 must integrate the accepted private seam, repair the
fail-closed registry `0x80070002` boundary without accepting any registry
failure as success, and add the full process/registry matrix stable ID with
non-SKIP enforcement before acceptance. Until those verified completion
changes land, the candidate remains v0.12.0/schema v19 and no Runner runtime is
active. Sequential ordering and Tier 2 gates remain mandatory, and live state
and evidence remain solely in the Task database.

<a id="tg-m24-sequence"></a>

### Accepted TG-M24.1 Design And TG-M24.1A Correction, Current TG-M24.1B, And Approved TG-M24 Verification Runner Sequence

TG-M24 is approved sequential Tier 2 work in lane
`TG-M24-VERIFICATION-RUNNER`. TG-M24.1 is the accepted design predecessor after
accepted TG-DOC.2, and TG-M24.1A is its accepted bounded correction. Current
runtime qualification-and-supply authority belongs to TG-M24.1B; TG-M24.2,
TG-M24.3, and TG-M24.4 remain inactive. Exact unit identity, order, dependency,
permission, and gate detail is owned by the
[TG-M24 Verification Runner execution contract](docs/execution-contracts/tg-m24-verification-runner.md),
not duplicated in this gateway.

TG-M24.1B may consume only its routed qualification scope after accepted
TG-M24.1A. TG-M24.2 may begin only after accepted TG-M24.1B and must bind a
fresh generation to its selected runtime digest; TG-M24.3 and TG-M24.4 may
advance only in order after their accepted predecessors. Current qualification
authority alone activates no Runner, schema, CLI, Skill, or gate. The sequence
grants no arbitrary command or
model choice, analyzer gate authority, raw-output retention, external-project
execution, publication, push, tag, or Release authority.

<a id="tg-doc-sequence"></a>

### TG-DOC Documentation Governance Sequence

This plan owns the two non-product documentation units and their cross-sequence
gateways. The Task database remains the sole owner of live status and evidence.

| Unit/order | Task | Lane | Dependency | Authority status and successor gate |
|---|---|---|---|---|
| TG-DOC.2 / 40 | `tg_task_bf2aa245019f5c9f` | `TG-M23-DERIVED-EVIDENCE` | accepted TG-M23.3 | accepted predecessor; required before TG-M24.1 |
| TG-DOC.3 / 20 | `tg_task_99371b8db2d43eb2` | `TG-DOC-LIFECYCLE` | accepted TG-M24.4 and accepted TG-DOC.2 | inactive post-M24 |

<a id="tg-doc-2"></a>

### TG-DOC.2 Accepted Post-M23 Documentation Governance Reconciliation

The canonical row above solely owns TG-DOC.2 identity, lane and order,
dependency, and successor gate. This section owns its documentation-governance
scope and gates.

Its scope is governing documentation, `tools/document_contract.py`, coupled
documentation tests, and repository-local read-only verification. It classifies
every enforced documentation rule as retain, replace, or remove with an explicit
owner, rationale, measured consumer, unit, severity, and change procedure where
applicable. Unsupported hidden reduction thresholds, checker self-size caps,
wording hashes, global Markdown bans, unexplained line ceilings, and magic-window
checks are removed or replaced by justified semantic checks. Authority
completeness, history non-authority and provenance, routes, anchors, links, Task
identity/order, no-live-state rules, and bounded selective reading remain
protected with positive and negative tests.

TG-DOC.2 changes no supported runtime, schema, migration, storage, public
CLI/JSON, Viewer, Skill behavior, package/install identity, evidence assurance,
completion or review gate, network behavior, or target-project authority. Its
accepted result requires focused document/history/release checks, the full
offline suite, an exact-target Verification Receipt, and two Tier 2 reviews
with no unresolved High or Medium finding.

The checker control inventory is complete at the following semantic level.
Presentation wording and line layout are not control units.

| Rule family | Decision | Owner | Measured consumer and unit | Severity | Rationale and change procedure |
|---|---|---|---|---|---|
| required files and deterministic text decoding | retain | `AGENTS.md` start rule and `docs/authority.md` | checker and task agent; canonical path and UTF-8 document bytes | blocking | Missing, linked, undecodable, BOM-prefixed, or unterminated authority is ambiguous. Change only in an atomic Tier 2 authority transition. |
| registry, owner graph, routes, anchors, and local links | replace | `docs/authority.md` | selective-read agent and checker; owner, path, and `path#anchor` relation | blocking | Validate closed semantic membership, exact-case reachability, and uniqueness without JSON-key order or prose hashes. Change the owner and checker atomically with Tier 2 review. |
| document role, agent start routing, and authority-status declarations | replace | `AGENTS.md`, `docs/authority.md`, and the execution-contract index | task agent; required role, section, and current/inactive relation | blocking | Required owners and status must remain unambiguous, but equivalent wording is allowed. Change only with the owning authority and Tier 2 review. |
| M22, M23, M24, and TG-DOC sequence identity | replace | each canonical execution owner; `plan.md#tg-doc-sequence` for TG-DOC | task orchestrator and checker; unit, Task ID, lane/order, dependency, and activation class | blocking | Structural rows replace whole-table digests and the former duplicated M24 prose table. Change requires approved Task authority and Tier 2 synchronization. |
| live Task/evidence exclusion from Git documents | retain | `AGENTS.md` and `docs/authority.md` | task agent and Task database; active Git-document state constellation or volatile evidence ID | blocking | Git prose must not become a stale status/evidence mirror. Any static exception needs explicit authority and a negative fixture. |
| history role, safe files, exactly-once index, and immutable provenance | replace | `docs/history/README.md` | lineage auditor; capture path, first visible structural declaration, source commit, and exact archived bytes | blocking | Structural placement replaces the first-2,048-byte heuristic; dedicated history tests retain archive/source hashes. Captures are never edited; add a new capture and index route atomically. |
| ordinary-search history exclusion | replace | `AGENTS.md` history-search policy | repository search; literal positive `/docs/history/` rule and subsequent actual-subtree negations | blocking | Semantic exclusion replaces exact whole-file `.ignore` bytes and permits unrelated rules without normalizing ineffective whitespace or separators. Change only with the owning search policy and negative fixture. |
| deterministic read-only checker behavior | retain | `README.md` development checks | local/CI caller; invocation, result, and pre/post file bytes | blocking | The checker must be offline, repeatable, sanitized on failure, and non-mutating. Change with focused tests and Tier 2 review. |
| document growth observation | replace | this documentation-governance decision | maintainer and reviewer; raw UTF-8 bytes per routed document and mandatory start set | advisory | Report measurements without a pass/fail threshold. A future blocking limit requires a named capacity consumer, measured evidence, explicit user approval, and Tier 2 review. |
| representative product, algorithm, and process-safety contract canaries | retain | `docs/specification.md`, `docs/design.md`, and the exact routed execution-contract owner | runtime implementer and focused tests; versioned field, byte vector, ownership boundary, or safety invariant | blocking | Preserve representative structural canaries for schema/Viewer/evidence projection, M23 byte framing and layout, and Windows lease/process/publication safety without pinning unrelated prose. Change the owning product authority and coupled focused test together with Tier 2 review. |
| 90-percent reduction, line ceilings, 650/800 self-caps, active wording/banner/section hashes, global Markdown bans, and giant prose needles | remove | TG-DOC.2 decision | none; no supported consumer or meaningful unit was demonstrated | none | These controls distorted edits or pinned wording without product value. Reintroduction requires a named consumer and unit, measured evidence, explicit user approval, and Tier 2 review. |

Runtime, privacy, packet, output, SQLite, Viewer, Bundle, manifest, and public
input limits are product controls owned by the specification and design, not
documentation-budget rules. Immutable history hashes and product digest vectors
also remain valid provenance or algorithm checks rather than active-prose pins.

<a id="tg-doc-3"></a>

### TG-DOC.3 Inactive Post-M24 Documentation Normalization And History Closure

The canonical row above solely owns TG-DOC.3 identity, lane and order,
dependencies, and inactive status. This section owns its preserved post-M24
scope and gates.

It preserves the former post-M24 closure: fold final supported M21-M24 behavior
into subsystem-oriented active specification and design, route completed
execution narrative through indexed non-authoritative history, and leave this
plan limited to unfinished static contracts, current decisions, and open issues.
Its write boundary is documentation, editorial Skill/reference synchronization,
coupled tests, and repository-local read-only checks. It may not rewrite history,
make history authoritative, copy live Task state or evidence into Git, retire an
unfinished contract, reintroduce unsupported documentation controls, or change
supported runtime, schema, CLI/JSON, Viewer, package, evidence, gate, network, or
target-project behavior. Completion requires authority, link, history, release,
Skill/reference, full offline, exact-target Receipt, and two Tier 2 review gates.

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
