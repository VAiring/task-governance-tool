# TG-M24 Verification Runner Current Execution Contract

<a id="tg-m24-verification-runner"></a>

> [!IMPORTANT]
> CURRENT FORMAL AUTHORITY.
> This document owns the TG-M24 repair and acceptance sequence.
> TG-M24.1 and TG-M24.1A are accepted predecessors, but their adversarial-code
> qualification details are excluded from this sequence's gates. TG-M24.1B is
> superseded and non-gating. TG-M24.R1, TG-M24.R2A, and TG-M24.R2B are accepted
> predecessors. TG-M24.R2C, TG-M24.R4A, and TG-M24.R4V are accepted predecessors; accepted
> R4A left its inventory-approved retired repository material and dedicated
> tests physically absent, with no archive or dormant copy. Accepted R4V adds
> only the dependency-pure, legacy-stable Runner value-model foundation and
> exact-candidate manifest closure, with no product activation. TG-M24.R3A and
> TG-M24.R3B are accepted predecessors for the schema-v20 migration/storage and
> public-activation baseline; TG-M24.R4B, TG-M24.R5, TG-M24.2A, and TG-M24.2B
> are accepted predecessors. TG-M24.2C owns current formal authority, and every
> later unit in this document remains inactive until its immediate predecessor
> is accepted.
> Its scope remains trusted-local, and its permission boundary is explicit
> opt-in.
> The separate TG-M24.R2 bootstrap Task supplies only a reviewed phase-one
> checkpoint to R1 and activates no Runner or product behavior.

The active [specification](../specification.md) and [design](../design.md) own
supported behavior and implementation structure. This document is the sole
detailed owner/router for the named TG-M24 units' purpose, scope, order,
dependency, permission boundary, and gates. Root [plan.md](../../plan.md) owns
cross-sequence gateways, current decisions, open issues, and static contracts
not delegated here. The Task database owns live state and evidence.

At the R1 cutover the supported product remains v0.12.0/schema v19. R1 changes
authority and the live Task graph only. It activates no Runner, schema, public
CLI, Skill branch, completion gate, network action, credential access, or
target-project mutation.

## Trusted-Local Boundary

Eligibility is restricted to a trusted-local repository and explicit opt-in.
An untrusted or external target uses the M21 manual verification fallback.
Launch uses fixed argv, shell=false, and a credential-excluding environment.
A Job Object, timeout, and bounded output constrain the process tree.
Private temporary materialization is single-owner and cleanup is blocking.
Raw stdout and stderr are transient and are never persisted; only the closed Runner outcome is persisted.
The Runner does not claim network isolation, hostile-code containment, or zero capability.
Loading this contract does not activate product code or a Runner runtime.
Candidate C, B-to-C, LPAC, AppContainer, ETW, and registry recovery are not current M24 gates.
Accepted R4A physically removed the inventory-approved retired Candidate,
LPAC/AppContainer, profile/ACL, dedicated native, and Candidate-only runtime
material and its dedicated tests. No archive, dormant copy, or replacement
security layer remains. This physical absence does not expand the threat model
or turn any retired qualification route into a current gate.

The optional Runner is adopted only for a repository that the user treats as
trusted local code and only after explicit project opt-in. Eligibility is not
inferred from a path, Git state, profile match, or successful prior run. An
untrusted repository, external target, manual or visual check, unsupported
platform, absent opt-in, or uncertain eligibility is never executed and stays
on the existing M21 caller-attested manual fallback.

This boundary is process control for trusted tests, not hostile-code isolation.
It does not claim network isolation, zero OS capability, or containment of
malicious code. Trusted tests can retain ordinary OS reach allowed to the
credential-clean child account and host. LPAC, AppContainer, zero-capability
tokens, Package-SID-only ACLs, a selected Candidate runtime, ETW diagnosis,
registry/profile recovery, diagnostic transfer, supervisor layers, trust-root
hardening, and a fault matrix are not current M24 requirements or completion
gates.

The retained minimum floor is closed and testable:

- one explicit project-owned plan is bound to the exact Task, Contract,
  verification criterion, and review target;
- argv is a closed literal array, executable resolution is fixed, `shell` is
  false, and `PATH` lookup is forbidden;
- the exact target is materialized into a private owned tree; ambient working
  copy execution, traversal/reparse admission, and copy-back are forbidden;
- the child environment excludes credentials and other unapproved inherited
  values;
- on Windows, a Job owns the process tree before trusted project code runs and
  enforces bounded time, CPU, memory, process count, and output;
- timeout, cancel, start uncertainty, or nonzero result terminates and waits
  for descendant/process zero before success can be considered;
- stdout/stderr are bounded and transient; raw output, argv, environment,
  credentials, stack traces, and private paths are never persisted; and
- one owner performs deterministic private-tree cleanup. Cleanup uncertainty
  is never converted into success.

Only the exact Task Contract's pass criteria, cleanup gate, and privacy gate
are blocking for an authorized unit. Once its declared Prepare boundary passes,
other diagnostic roughness is recorded as residual risk. A Contract may permit
one non-retaining diagnosis and one local repair inside its existing scope. If
the same preparation class fails again, the unit blocks without another retry,
new file, transfer path, supervisor, trust-root layer, or fault-matrix asset.

## Sequence Boundary

Main work is sequential Tier 2 work in lane `TG-M24-TRUSTED-RUNNER`. `current`
and `inactive` below describe formal execution authority, not live Task status
or evidence.

| Unit/order | Task | Dependency | Purpose, permission boundary, and completion gate |
|---|---|---|---|
| TG-M24.R1 / 10 | `tg_task_8af2eee60acb0830` | reviewed R2 bootstrap boundary | Atomically cut authority and the live graph to this trusted-local sequence without product activation. |
| TG-M24.R2A / 20 | `tg_task_96a03f1d76799f79` | accepted TG-M24.R1 | Inventory every M24 delta against the accepted pre-M24 baseline without changing source bytes or adding an inventory artifact. |
| TG-M24.R2B / 25 | `tg_task_ca8d0d81cd1962ab` | accepted TG-M24.R2A | Map changed tests to current requirements or regressions and identify obsolete or meaningless tests without editing behavior. |
| TG-M24.R2C / 30 | `tg_task_252701fe03f530af` | accepted TG-M24.R2B | Freeze the retained Runner ownership and one-way dependency boundary needed for M25; add no process or IPC layer. |
| TG-M24.R4A / 40 | `tg_task_83d2af496ac84982` | accepted TG-M24.R2C | Physically delete only inventory-approved retired repository assets and their dedicated tests, with no archive or dormant copy. |
| TG-M24.R4V / 45 | `tg_task_006bee9937e25af9` | accepted TG-M24.R4A | Establish the dependency-pure, legacy-stable Runner value model and exact-candidate manifest without product activation. |
| TG-M24.R3A / 50 | `tg_task_a6d113455aa2cdfe` | accepted TG-M24.R4V | Establish only an injected-path private schema-v20 migration/storage rehearsal while the public candidate remains schema v19. |
| TG-M24.R3B / 60 | `tg_task_c343ed2ec8acedf8` | accepted TG-M24.R3A | Add the separately reviewed public schema-v20 activation and Bundle/Evidence/Viewer/recovery compatibility without process launch or gate authority. |
| TG-M24.R4B / 70 | `tg_task_e04fd31e6713cfa1` | accepted TG-M24.R3B | Repair pre-M24 behavior and frozen dependency violations before execution is enabled. |
| TG-M24.R5 / 80 | `tg_task_89e9ac8d34df2e95` | accepted TG-M24.R4B | Perform the one authorized physical retirement of fixed OS-temp diagnostic residue. |
| TG-M24.2A / 90 | `tg_task_2c6fd4707ac1e81b` | accepted TG-M24.R5 | Implement trusted-plan authority and exact private target materialization without launching a command. |
| TG-M24.2B / 100 | `tg_task_f8880aeb93c3ad52` | accepted TG-M24.2A | Implement the bounded trusted-local process adapter and deterministic cleanup. |
| TG-M24.2C / 110 | `tg_task_8cc06027db5be49f` | accepted TG-M24.2B | Integrate audit-only shadow observations while preserving M21 completion semantics. |
| TG-M24.2D / 120 | `tg_task_fafad7bc62df7576` | accepted TG-M24.2C | Accept the complete shadow Runner and evidence slice; activate no completion-gate authority. |
| TG-M24.3 / 130 | `tg_task_dc015144091f8e60` | accepted TG-M24.2D | Integrate a qualifying exact-current Runner basis while preserving explicit M21 fallback. |
| TG-M24.4A / 140 | `tg_task_0da786589eb5144a` | accepted TG-M24.3 | Accept supported, fallback, failure, cleanup, and privacy flows. |
| TG-M24.4B / 150 | `tg_task_220ff054e445f40e` | accepted TG-M24.4A | Accept legacy, core, v19-to-v20 and v20-to-v21 migration, recovery, and fresh-schema-v21 compatibility. |
| TG-M24.4C / 160 | `tg_task_b0a3bf776bea1e93` | accepted TG-M24.4B | Accept the final v0.13/schema-v21 package and release-candidate boundary. |
| TG-M24.4D / 170 | `tg_task_f81f2d126f033a59` | accepted TG-M24.4C | Perform final integrated Runner acceptance without adding corrective infrastructure. |
| TG-M24.CP4 / 180 | `tg_task_a9e1229d594594d4` | accepted TG-M24.4D | Close M24 only with a clean, no-new-debt, M25-ready repair checkpoint. |

The reviewed R2 bootstrap boundary is separate sequential support in lane
`TG-M24-BOOTSTRAP`, order 10. It permits R1 to consume the proven review-target
decoupling relation but establishes no bootstrap completion evidence or product
feature. It is not a second main lane, predecessor product feature, Runner
activation, or reusable successor evidence.

<a id="tg-m24-1"></a>

## TG-M24.1 Accepted Design Predecessor Compatibility Route

Task `tg_task_29aa63124900ad95` remains an accepted predecessor for lineage and
stable links. Its durable concepts are retained only where restated in the
current trusted-local boundary. Its former hostile-code isolation design is
not current authority and supplies no gate, artifact, test obligation, or
success evidence to a current unit.

<a id="tg-m24-1a"></a>

## TG-M24.1A Accepted Portability Predecessor Compatibility Route

Task `tg_task_56e212c793a42272` remains an accepted predecessor for lineage and
stable links. Its native portability proof is not a current M24 gate or a
mandatory test for this sequence. Retained implementation and tests are decided
only by TG-M24.R2A/R2B inventory; their approved retired subset was physically
removed by accepted TG-M24.R4A.

<a id="tg-m24-1b"></a>

## TG-M24.1B Superseded Non-Gating Compatibility Route

Task `tg_task_bb218653b56f76ed` is superseded. Its fixed Candidate-C,
Candidate-B-to-C, LPAC/AppContainer, zero-capability, ETW, transfer, recovery,
supervisor, trust-root, and fault-matrix requirements do not block, activate,
or satisfy any current M24 unit. This anchor is retained only so existing links
fail safely until inventory-approved physical deletion and document
normalization; it is not accepted-predecessor authority.

<a id="tg-m24-r1"></a>

## TG-M24.R1 Accepted Authority And Sequence Cutover

Task `tg_task_8af2eee60acb0830` owned one atomic documentation and live-Task
graph switch. It replaces the superseded adversarial qualification route with
the trusted-local boundary above, keeps the existing product inactive, moves
the retained legacy Tasks to orders 120, 130, and 170, and leaves CP4 at 180.
No old dependency, duplicate order, dangling Task, or parallel current M24
authority may remain.

R1 may change only governing owners, directly coupled document checks, and the
public live Task graph. It may not change product, schema, CLI, Runner runtime,
cleanup targets, native tests, or unrelated WIP. Completion requires internally
consistent authority/specification/design/plan/execution/release routes, exact
lane and dependency checks, a clean exact scoped diff, one current `pass/full`
Verification Receipt, and two independent Tier 2 PASS reviews with no open High
or Medium finding.

<a id="tg-m24-r2a"></a>

## TG-M24.R2A Accepted Debt Asset Baseline Inventory

Task `tg_task_96a03f1d76799f79` inventories every tracked, staged, unstaged,
untracked, package, authority, test, and temporary M24 delta against baseline
`888a77759cfc59376089b8ebdc509e748638f603`. Every path receives exactly one
owner, consumer, disposition, package status, and coverage classification.
Unclassified paths must be zero. This unit writes no source byte, inventory
file, archive, or cleanup action. Completion requires exact-diff checks and two
independent Tier 2 PASS reviews.

<a id="tg-m24-r2b"></a>

## TG-M24.R2B Accepted Test Portfolio Value Audit

Task `tg_task_ca8d0d81cd1962ab` maps every M24-added or materially changed test
to one current requirement, observed regression, compatibility boundary, or
critical process/cleanup/privacy boundary. Retired-security, duplicate,
implementation-shape-only, arbitrary natural-language-inference,
mock-order-only, disabled, new-SKIP, and non-detecting tests receive a deletion
or consolidation disposition. This audit edits no production behavior or test
and creates no meta-test framework. Completion requires complete lane ownership
and two independent Tier 2 PASS reviews.

<a id="tg-m24-r2c"></a>

## TG-M24.R2C Accepted M25-Ready Architecture Boundary Freeze

Task `tg_task_252701fe03f530af` froze, but did not implement, the closed
Runner-slice module registry and acyclic dependency graph in the active
[design](../design.md#tg-m24-r2c-runner-architecture-boundary). For the Runner
route, `cli.py` may call only `verification_runner_service.py`; the service is
the parent orchestrator and the sole owner of authority, Task/Contract and
criterion checks, canonical SQLite/repository coordination, target/plan
selection, Evidence/terminal persistence, maintenance/recovery coordination,
and final cleanup acceptance. Repository and persistence modules never launch
or import the process or OS adapter. The process adapter receives only the
closed typed bounded request plus its local Boolean cancellation signal,
returns only the closed bounded sanitized result, opens no canonical state,
and imports no CLI, service, repository, storage, Task, Contract, review,
Evidence, completion, setup, backup, maintenance, or business-gate module.

`verification_runner_process.py` owns only Job/process/stdio/timeout/
termination/wait/handle mechanics and reports process-tree zero and handle
closure. `verification_runner_lifecycle.py` owns only parent-requested private-
tree mechanics. `verification_runner_service.py` alone combines the process
and private-tree proofs, blocks on either uncertainty, and authorizes cleanup
success or terminal persistence. No raw output, argv, environment, credential,
private path, exit code, or exception body crosses that persistence boundary.
The design's logical request/result records add no serializer, IPC, worker,
process, queue, pipe, socket, RPC, spool, supervisor, retry layer, schema,
public CLI, or product activation.

The inventory-approved retired Candidate/LPAC/AppContainer/profile/ACL/ETW/
registry-recovery residue, direct service-to-retired-OS seam, and Candidate-
only runtime material are physically absent after accepted R4A. A
dependency-pure, legacy-stable value-model foundation and its reverse-import repair are
supplied by accepted R4V. A retained business-freshness callback in the process
adapter, other cycle, or second cleanup-acceptance owner remains
transitional nonconformance routed to R4B.
R3A/R3B and 2A/2B/2C own
only their already-declared later storage, Evidence, target/plan, process/
lifecycle, and parent-integration implementation slices. R2C repaired none of
them and changed no R2A/R2B disposition or action selector. Acceptance changed
only the five governing documents, the existing document checker, and its
existing test module; required structural status, graph, type/privacy, cleanup-
owner, transitional-routing, and non-activation checks; and required one fresh
exact-target `pass/full` Receipt plus two independent Tier 2 PASS reviews.

<a id="tg-m24-r4a"></a>

## TG-M24.R4A Accepted Retired Repository Asset Physical Deletion

Task `tg_task_83d2af496ac84982` physically deleted only R2A/R2B-approved retired
repository code, fixtures, tests, manifests, docs, and dormant switches. Its
accepted result leaves that approved set physically absent, with no archive,
stash, alternate copy, replacement security layer, or unrelated cleanup.
Retained consumers and unrelated WIP remained outside its write boundary.
Acceptance required retained-test ownership, package/document/lane
consistency, focused and exact-diff checks, and two independent Tier 2 PASS
reviews.

<a id="tg-m24-r4v"></a>

## TG-M24.R4V Accepted Dependency-Pure Legacy-Stable Runner Value Model Foundation

Task `tg_task_006bee9937e25af9` establishes the pure `verification_runner.py`
value model between accepted R4A and accepted R3A. It preserves the opaque
accepted Runner-policy seal and closed legacy record/projection shapes, uses
caller-supplied identifier tokens instead of entropy, and has no repository or
other project-module import. Its exact-candidate v0.12 manifest closes only the
accepted tree plus this foundation. Temporary provider/policy/instance names
remain fail-closed import shims only: R3A removes the storage instance consumer,
R3B removes Evidence provider/policy consumers, and R4B deletes the remaining
shims and fixed legacy policy API. R4V adds no schema, setup, Runner launch,
Evidence activation, public CLI, or gate authority. Completion required the
exact ten-path diff, focused pure-model/document/package checks, one fresh
exact-target `pass/full` Receipt, and two independent Tier 2 PASS reviews.

<a id="tg-m24-r3a"></a>

## TG-M24.R3A Accepted Schema-v20 Migration And Storage Baseline

Task `tg_task_a6d113455aa2cdfe` owns only a private, non-public v19-to-v20
migration and storage rehearsal reached through an explicitly injected path.
Its "idempotent v20 setup" means private migration, reentry, validation, and
database-level rollback; it does not mean public setup activation. During R3A
the public schema constant and setup target remain 19, Viewer compatibility
remains v5-v19, and the native schema-v19 version-1 Bundle writer remains
unchanged.

The rehearsal migrates one caller-owned disposable v19 database in place at the
same path under `BEGIN IMMEDIATE`; it performs no copy, backup, publication, or
managed recovery. Rollback restores logical schema/data and claims no
byte-identical SQLite file. It preserves every existing v19 business row and
stable ID, including existing version-1 Bundle payload bytes and digests, and
synthesizes no v20 business, Runner, Reference/link, Bundle, Evidence, or Viewer
row. It is unreachable from the public CLI, canonical-state resolver, managed
backup/recovery, and publication paths. It launches no Runner and grants no
completion-gate authority.

The Bundle table rebuild does not preserve or replay arbitrary caller DDL. A
persistent unowned index or trigger attached to
`completion_evidence_bundles` is unsupported residue: successful migration
physically removes it with the old table, rollback restores it transactionally,
and reentry preserves its absence. Unrelated standalone objects remain
unchanged.

Its exact physical target is migration `20/verification_runner_shadow`: four
immutable Runner tables, ten required indexes, twelve Runner triggers, the Task
marker, the two nullable cycle fields, and the Bundle-v2 null-Runner tagged
union fixed by the design. Existing v19 rows transform only to marker `0` and
null new fields; new Runner/Evidence rows are zero. Same-version reentry only
validates. Marker-only, missing or changed owned objects, a known later marker,
busy/contention, integrity, and foreign-key cases fail closed with no partial
write. R3A owns physical shape and storage-parent consistency, not the 2A-2C
plan, process, observation, or cleanup admission rules. R3A also removes the
storage instance-shim consumer allocated by accepted R4V, but not the shim body.

Completion requires focused private migration, reentry, injected rollback,
contention, exact business-row/stable-ID/Bundle-byte and owned-object
preservation, explicit attached-residue deletion and rollback restoration,
public-nonactivation, and storage checks in a dedicated storage-only R3A test
module that imports only storage and the accepted pure value model. The broad
`tests/test_m242_runner_storage.py`
module is not an R3A gate. Its stale Runner-positive Bundle oracle and other
candidate-specific/deferred R3B, 2C, business-cardinality, cleanup, or setup
cases follow R2B disposition: physically delete obsolete cases or move them to
their owner, with no SKIP/disabled residue. Required R3A oracles are normalized
schema/table/FK/index/trigger inventory, permissive multiple attempts and
observations, null-Runner Bundle, marker-only/partial/drift/unrelated-extra
handling, attached-residue deletion/rollback/reentry behavior, preservation,
contention, and public nonactivation. Public nonactivation is the compound gate
formed by
`tests.test_m242_r3a_schema20_storage.R3ASchema20StorageTests.test_public_schema_remains_19`
for the public schema/apply-migrations and storage Viewer boundaries,
`tests.test_m242_r3a_schema20_storage.R3ASchema20StorageTests.test_private_migration_inventory_preservation_and_reentry`
for native schema-19/Bundle-v1 generation and preservation, and the existing
setup-owned
`tests.test_setup.SetupCommandTests.test_fresh_preview_success_and_idempotent_replay_follow_exact_rows`
for `schema_to = 19`. The dedicated storage-only module must not import or
duplicate setup. Completion also requires an exact diff and two independent
Tier 2 PASS reviews.

<a id="tg-m24-r3b"></a>

## TG-M24.R3B Accepted Evidence And Projection Compatibility Baseline

Task `tg_task_c343ed2ec8acedf8` owns the separately reviewed public schema-v20
activation: the public schema constant and setup target, the Bundle-v2
null-Runner writer, and schema-v20 Evidence/JSON, Viewer, managed backup,
recovery, and legacy compatibility. It adds no DDL and must consume the accepted
R3A private storage baseline without rewriting its acceptance evidence. It
creates no Runner resolution, attempt, sandbox event, observation, Evidence
Reference/link, Bundle member, or Runner projection; 2C owns their first durable
mapping, write, and projection. It launches no process and grants no gate
authority. Existing M22 projections retain no raw output, argv, environment,
credential, or private path and fabricate no assurance.
R3B also removes the Evidence provider/policy-shim consumers allocated by R4V,
but leaves shim-body deletion to R4B. A complete v19 database must migrate to
exact schema v20, fresh setup must create exact v20, and complete v20 reentry is
validation-only. A database declared as v19 but containing any recognized
v20-owned table, index, trigger, or column must fail closed before mutation in
the resolver, setup inspection/migration, Viewer, and managed backup/recovery
paths; unrelated extra objects retain the governed R3A policy. Completion
requires exact Bundle-v2 null-Runner payload/digest, the format-v2 Evidence
index with explicit per-entry Bundle format, and preserved Bundle-v1
compatibility, focused compatibility and public-activation checks, all
applicable offline/document/release checks, a fresh exact-target `pass/full`
Verification Receipt, the matched-pair integration review over the exact
R3A/R3B commits, an exact diff, and two independent Tier 2 PASS reviews with no
open High or Medium finding.

R3A and R3B remain distinct sequential Tasks, commits, and fresh evidence
records. Acceptance of either unit alone authorizes no cutover. R3B's matched-
pair review may authorize only the code/main cutover. Canonical database
migration remains a later explicit public setup action at a separately approved
checkpoint.

<a id="tg-m24-r4b"></a>

## TG-M24.R4B Accepted Pre-Runner Core And Dependency Repair

Task `tg_task_e04fd31e6713cfa1` repairs only M1-M23 behavior and R2C dependency
violations exposed by retained WIP before Runner execution. Every changed core
module and test must map to current authority; duplicate or meaningless tests
follow R2B disposition. It also finalizes deletion of R4V's temporary
provider/policy/instance import shims and fixed legacy policy API after the
R3A/R3B consumers are gone. This unit adds no Runner execution or feature scope.
Completion requires full offline lanes from an exact clean target, exact diff,
and two independent Tier 2 PASS reviews.

<a id="tg-m24-r5"></a>

## TG-M24.R5 Accepted Fixed Diagnostic Residue Retirement

Task `tg_task_89e9ac8d34df2e95` proves the fixed literal identities, owned
inventories, and process/session/profile zero for the already identified
private OS-temp residue, then performs one bounded physical retirement using
the existing cleanup path. Unknown ownership, reparse, foreign or active state,
timeout, or cleanup uncertainty blocks without retry and retains no raw
diagnostic output. No archive or replacement infrastructure is allowed.
Completion requires absence proof, repository byte preservation, and two
independent Tier 2 PASS reviews.

<a id="tg-m24-2a"></a>

## TG-M24.2A Accepted Trusted Plan And Exact Target Materialization

Task `tg_task_2c6fd4707ac1e81b` implements explicit trusted-local opt-in,
project-owned plan authority independent of untrusted target changes, exact
Task/Contract/criterion/target and plan-digest binding, closed argv admission,
and safe bounded private materialization. Traversal, reparse, extra entry,
stale basis, ambient execution, copy-back, repository mutation, and process
launch are forbidden. Completion requires focused offline target and privacy
checks plus two independent Tier 2 PASS reviews.

The implementation is one bounded sequential slice: first close the dormant
PlanV1/state/digest and exact-Git-material contracts in the active specification
and design; then add `verification_runner_plan.py`,
`verification_runner_git.py`, their focused tests, directly coupled lane and
package-manifest reconciliation. The dormant parent service remains unchanged
and is not a TG-M24.2A consumer; its target/plan integration remains TG-M24.2C
scope. Read-only Git plumbing is permitted solely
to observe and stream exact objects. No target code or verification command is
launched. Accepted 2B supplied only runtime identity, lifecycle, process/native
adapters, and deterministic cleanup mechanics. Current 2C owns service
dispatch/persistence behavior, durable audit-only Runner rows/Evidence/
projection, and parent-service cleanup acceptance. CLI/JSON, Skill guidance,
schema, and DDL/migration remain unchanged; 2D onward remains inactive.

The 2A integration gate uses actual plan and target outputs with the existing
pure `resolution_idempotency_digest` to prove the closed current-basis,
review-target, target-material, and plan-digest composition without argv or raw
plan bytes. This is no service activation or durable Runner evidence; 2C still
owns orchestration, persistence, and dispatch consumption.

Verification must prove absent/false/no-match fallback; local-plan index
registration, non-ignore, and check-uncertainty denial; exact current-basis
selection; mismatch, ambiguity, malformed, over-bound, unsafe-path, symlink,
submodule, sparse, object-loss, drift, nonempty destination, reparse, and extra-
entry denial; exact commit and staged-index bytes; unstaged/untracked omission;
plan and target digest stability; zero copy-back and zero target-code launch;
package/lane/document consistency; exact diff; and two fresh independent Tier
2 PASS reviews with no unresolved High or Medium finding.

<a id="tg-m24-2b"></a>

## TG-M24.2B Accepted Bounded Local Process Runner

Task `tg_task_f8880aeb93c3ad52` implements shell-free literal argv, fixed
executable resolution without `PATH`, credential-clean environment, a private
working tree, Windows Job assignment before project code, process/time/CPU/
memory/output bounds, timeout/cancel handling, descendant zero, bounded
transient output, and deterministic cleanup with no copy-back. It adds none of
the retired security or recovery infrastructure. Completion requires focused
Windows pass/nonzero/timeout/cancel/start-failure and cleanup/privacy checks
plus two independent Tier 2 PASS reviews.

<a id="tg-m24-2c"></a>

## TG-M24.2C Current Shadow Observation And Evidence Capture

Task `tg_task_8cc06027db5be49f` integrates bounded Runner outcomes as audit-only
schema-v20 observations and Evidence links. Runner absence, ineligibility, or a
closed pre-launch failure does not block ordinary exact review-target capture;
storage or lifecycle atomicity uncertainty still fails closed. Runner results
cannot satisfy verification or completion, and M21 semantics remain unchanged.

The approved narrow implementation is exact:

- Before T1, an absent verification criterion, plan
  `absent|disabled|no_match`, an unsupported or non-addressable target, or a
  definite closed Runner-only pre-attempt failure takes the existing target-
  only path and creates zero durable Runner rows. TG-M24.2A malformed,
  ambiguous, stale, inconsistent, and over-bound cases remain blocking and
  create no target or Runner row.
- After preflight and before an eligible T1, acquire one zero-wait Runner lock
  and retain it through pending reconciliation, T1, process/lifecycle work,
  and terminal T2. Lock contention or lifecycle uncertainty fails closed with
  no T1; no SQLite writer is held across filesystem or process work.
- For an eligible route, the one short T1 writer atomically captures the
  ordinary exact target and exactly one resolution plus one attempt intent.
  A later failure preserves that committed target/intent prefix, invokes no
  maintenance, and returns a sanitized error without Runner success.
- Per target generation there is at most one resolution, attempt, cleanup
  event, and observation. A terminal observation has exactly one standalone
  Evidence Reference and one `runner_observation` verification-criterion link.
  A restart may instead have one cleanup event and no observation/Reference/
  link when the prior result is unknowable.
- After intent, only an accepted 2B result with process zero, handles closed,
  raw output discarded, and exact private-tree absence may become terminal.
  Launched results use `route=runner`; closed no-launch results use
  `route=m21_fallback`; result outcome/reason/launch state are otherwise mapped
  without a new code. `cleanup_failed` and any uncertain proof are not
  persistable observations.
- Restart never relaunches an attempt. Only the exact database-named known tree
  may be cleaned. Multiple pending attempts, a foreign tree, owner/basis/
  implementation drift, or cleanup uncertainty fails closed; proved drift
  cleanup may append only the null-observation cleanup event before error. The
  cleanup-performing call creates no new T1 and invokes no maintenance, but
  that cleanup-only state closes its old generation rather than permanently
  blocking Runner use; a later independent call may admit a new generation
  after zero actual pending attempts and empty fixed inventory are proved.
- Schema 20 and its DDL, all public commands and JSON success shapes, Skill,
  Bundle v2, Viewer UI, Evidence JSON, Task Runner markers, and every M21
  verification/completion gate remain unchanged. The private root and fixed
  `verification Runner orchestration policy v1` digest are exactly those
  owned by the current specification and design. Because 2B supplies no
  durable canonical runtime digest, resolution and source-projection
  `runtime_digest` are always null;
  execution identity is only the manifest-bound implementation digest and the
  fixed policy label.

Boundary failure is an explicit inspection checkpoint for this execution
unit. At the first T1 atomicity, intent/process, cleanup, restart, or terminal-
persistence failure, preserve the exact state and bounded diagnostics, do not
automatically relaunch or repeat a materially equivalent repair, and inspect
the boundary before continuing. Ask the user before any repair that would
change this narrow contract; a test is never weakened to pass.

Completion requires focused zero-row fallback, pre-T1 lock serialization,
atomic T1, launched and closed no-launch mapping, proof rejection,
cardinality/replay, concurrency, restart cleanup-only and next-generation
admission, drift, sanitized Reference/link, valid/malformed schema-v20 graph
admission, Bundle/Viewer non-projection, backup/recovery and v19/v20
compatibility checks; an exact diff;
full offline PASS; and two fresh independent Tier 2 PASS reviews with no open
High or Medium finding.

<a id="tg-m24-2"></a>

## TG-M24.2D Inactive Shadow Runner Integrated Acceptance

Task `tg_task_fafad7bc62df7576` accepts the complete 2A-2C shadow slice from a
fresh exact target. It proves trusted-local eligibility, exact materialization,
bounded process lifecycle, cleanup, privacy, audit-only observations, schema-
v19 compatibility, and unchanged M21 completion semantics. It activates no
Runner completion-gate authority and reuses no pre-cutover target, Receipt,
review, finding, or observation. Completion requires focused/native/full
offline checks, exact diff, and two independent Tier 2 PASS reviews.

<a id="tg-m24-3"></a>

## TG-M24.3 Inactive Runner Gate Integration And M21 Fallback

Task `tg_task_dc015144091f8e60` may let one qualifying exact-current complete-
plan trusted-local Runner result satisfy verification. Every launched non-pass
blocks that selected basis; only a closed no-launch fallback may use the M21
caller-attested Receipt. Unsupported, manual, visual, external, and untrusted
work always stays manual. Analyzer output, arbitrary commands, old evidence,
and caller override gain no authority. Schema-v20 shadow records cannot be
promoted to this gate; before M24.3 implementation, a separate schema-v21
contract is required. This contract does not choose a schema-v21 migration,
tag, or DDL. Completion requires basis/version, invalidation/recovery, legacy
history, projection, Skill/package, full offline, exact-diff, and two
independent Tier 2 PASS reviews.

<a id="tg-m24-4a"></a>

## TG-M24.4A Inactive Supported, Fallback, Failure, And Privacy Acceptance

Task `tg_task_0da786589eb5144a` validates clean-repository no-Runner/manual
fallback, shadow pass, nonzero, timeout, cancel, start failure, target/Contract
drift, one non-SKIP real Windows Job process-tree-zero/output-cap/owned-cleanup
flow, and the complete retention deny-list. Corrections return to their owning
implementation Task. Completion requires exact-target evidence and two
independent Tier 2 PASS reviews.

<a id="tg-m24-4b"></a>

## TG-M24.4B Inactive Legacy, Core, And Fresh-State Acceptance

Task `tg_task_220ff054e445f40e` preserves v19-to-v20 compatibility and validates
the separately approved v20-to-v21 migration, fresh schema v21, reentry,
backup/recovery, Task/Contract/target/Receipt/review/completion history, M1-M23
behavior, Evidence Bundle/JSON, Viewer, Analyzer coexistence, and exact clean-
target reproducibility. The schema-v21 contract is a separate Tier 2 decision
before M24.3; this document chooses no migration tag or DDL. No unexpected or
newly skipped test is accepted. Corrections return to their owner. Completion
requires full offline lanes, exact diff, and two independent Tier 2 PASS
reviews.

<a id="tg-m24-4c"></a>

## TG-M24.4C Inactive v0.13 Package And Release-Candidate Acceptance

Task `tg_task_b0a3bf776bea1e93` reconciles the final v0.13/schema-v21 package,
using the exact schema-v21 contract separately accepted for M24.3, with its
manifest, active documents, release note, archive inventory, fresh package
install, upgrade/rollback evidence, and retired-asset absence. It performs no
publication, push, tag, network action, or product correction. Completion
requires document/release/lane/full-offline checks, an exact target, and two
independent Tier 2 PASS reviews.

<a id="tg-m24-4"></a>

## TG-M24.4D Inactive Verification Runner Integrated Acceptance

Task `tg_task_f81f2d126f033a59` performs the final integrated acceptance of
the trusted-local Runner, Runner-observed basis, M21 fallback, process bounds,
cleanup/privacy, Evidence, legacy history, and package consistency. It adds no
new infrastructure; any correction returns to its owning Task. Execution
outside bounded fixtures or this repository requires separate exact project
authority. Completion requires authorized realistic forward checks, exact
diff, a current `pass/full` Receipt, and two independent Tier 2 PASS reviews
with no open High or Medium finding.

<a id="tg-m24-cp4"></a>

## TG-M24.CP4 Inactive Final No-Debt Repair Checkpoint And M25 Handoff

Task `tg_task_a9e1229d594594d4` closes M24 only from one clean reviewed
candidate. Against baseline `888a77759cfc59376089b8ebdc509e748638f603`,
every changed path must be owned and classified, unclassified and new debt must
be zero, and no debt class may be higher. Retired/archive/dormant/temp residue,
unowned/duplicate/disabled/new-SKIP/meaningless tests, package/document/schema/
DAG drift, or open M24 work blocks closure. CP4 changes no product byte and
records no correction. Completion requires all current checks, a current
`pass/full` Receipt, and two independent Tier 2 PASS reviews before the M25
handoff and TG-DOC.3 normalization may begin.

## Expansion Boundary

No unit may add a new plan field, executable resolver, platform/provider,
public leaf, normal-loop action, output-retention mode, Analyzer gate, network
promise, external execution, security layer, transfer mechanism, supervisor,
trust-root hardening, or fault matrix without separate explicit authority.
Failure of an inactive or future idea never weakens the M21 manual fallback or
expands the current Task Contract.
