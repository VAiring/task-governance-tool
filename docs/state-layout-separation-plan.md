# Project State Separation: Design And Execution Plan

## Authority And Activation

This initiative-scoped plan is the conditional owner of the already registered
three sequential execution units below. The user approved its persistence and
the first unit's isolated design/prototype work on 2026-09-19. It does not
activate a different production path, authorize migration of live data, or
authorize the implementation/comparison units before their stated gates.

The current [package layout](specification.md#package-runtime-and-generated-state),
[state resolver](setup-state-design.md#fixed-state-resolver),
[setup contract](setup-state-specification.md#setup-contract), and repository
operating rules remain in force until the implementation unit makes a reviewed,
coherent switch. The design below is a proposal to test, not a claim of an
implemented migration or measured permission benefit. Live status and evidence
belong only in the public Task CLI.

Retire this plan only after all dependent Tasks are completed or explicitly
superseded, the implemented behavior has current specification/design owners,
remaining decisions are preserved, and the separately authorized historical
transition satisfies [AGENTS.md](../AGENTS.md#documentation-maintenance).

## Proposed Fixed Layout

For both an ordinary installation and development self-host, use the governed
project root, not the enclosing Git worktree or current shell directory:

```text
<project>/
  .agents/skills/task-governance-tool/    ordinary physical code and config
  task-governance-tool/                  self-host code and config, instead
  .taskgov/
    taskgov-state.lock                   setup transition lock
    current/
      taskgov.sqlite                    DB and its rollback-journal sidecar
      backups/                          managed generations and artifact lock
      evidence/                         index, Bundles, lock, sibling temporaries
      viewer/                           HTML, lock, sibling temporaries
      verification-runner/              lock, attempts and quarantine
```

The two package alternatives remain mutually exclusive. No new installation
layout, user-wide state, path option, environment override, per-Task selector,
or second active DB is added. `.taskgov/` is the entire generated-state and
Git-ignore boundary; setup/doctor check its effective ignore with the existing
bounded Git mechanism. Taskgov still does not edit the project's ignore rules.
The old package-local state exclusion remains during migration/retirement.

Keep `config/verification-runner.json`, `config/viewer.json`, and
`config/effort-advisory.json` inside the physical Skill. They are explicit local
configuration, not routine generated state. Plan publication, configuration
editing, Skill installation/update, and Git writes retain their existing
separate permissions. Ordinary state updates should need none of those writes.

`CanonicalStatePaths` must derive the new paths from the already validated
project root and physical package observation. `DatabaseTarget` transports
them; downstream code must not independently concatenate `.taskgov` paths.
Preserve existing internal path-injection seams for tests, not public use.

## Preserved Behavior And Data

- Preserve project/Task IDs, Contracts, events, target generations, Receipts,
  Findings, cycles, Bundle IDs/digests, maintenance policy and generation rows.
- A state move within the same project is not relocation: retain project
  binding generation, path hash and complete binding history. Real project
  relocation still follows the current explicit confirmation procedure.
- Retain schema v22 unless a separately explained implementation necessity is
  found. A new schema version is not justified merely by a filesystem move.
- Preserve current read-only behavior, global/local admission boundaries,
  privacy validation and evidence gates. Historical evidence never becomes
  current evidence because it was copied.
- Carry all validated managed backups and valid generated Evidence/Viewer
  artifacts. Preserve Runner lifecycle material without executing or repairing
  it merely because its parent path changes. Reconcile only as already owned
  by the Runner lifecycle contract; active attempts prevent cutover.
- New writes use only the fixed new state root after activation. Preserve
  same-directory temporary publication, no-replace semantics where required,
  zero-wait artifact locks and existing maintenance ordering. No daemon,
  background migration or new normal-loop CLI call is introduced.
- Moving state outside protected Skill files removes that host write barrier;
  it does not make state tamper-proof. Existing structural/digest/identity
  validation remains, but a workspace writer can still alter generated data.

## Proposed Migration Boundary

Use explicit `setup --read-only` for preview and explicit write `setup` for
cutover. No ordinary command, doctor, import, or read repairs the layout.
Preview checks source/destination, identity/binding, inventories and required
permissions without creating directories, locks, temporaries or artifacts.

Use an offline maintenance-window migration: stop Taskgov writers, enabled Runner
processes and other operations using the state; update the one physical
package; then run setup. Do not kill processes, change ACLs, or silently switch
to an online migration. Existing CLI versions do not share a new layout lock,
so acquiring that lock alone cannot prove that all old writers are stopped.
This is a prerequisite for cutover, not an optional online mode. Acquire the
new-root transition lock, then the old-root transition lock, then the source
Runner, Evidence, Viewer and backup locks in that order, using the existing
zero-wait lock primitives. Observable contention or unsafe state stops the
transition without replacing either primary. Keep the candidate private until
publication/activation; locks copied as artifacts do not transfer ownership.
Normal activated-state maintenance retains its existing new-root lock rules.

### Selection And Recovery Rules

| Observed layout | New CLI / setup proposal |
|---|---|
| Neither location has durable state or migration residue | Ordinary reads report setup required; explicit setup initializes one new project at the new location. |
| Only valid old state exists | Ordinary calls require migration. Preview describes the cutover; write setup preserves/validates it before activation. |
| Only valid activated new business state exists, with its matching old-path barrier and no old business DB | Use the new state. Installation/upgrade must preserve the retirement barrier described below. |
| Activated new state exists but the required old-path barrier is missing or mismatched | Do not admit ordinary use or initialize old state. Explicit setup must validate and resolve this incomplete/conflicting layout without changing project identity or replacing the new DB. |
| Old state plus a private candidate exists | No normal use of the candidate. Only setup may resume the exact validated transition or report an incomplete/conflicting state. |
| Activated new state plus its matching old retirement barrier exists | Use only the new state; old retained artifacts are recovery material, never a fallback. |
| Two usable databases, mismatched identities, an unowned stage, or unexplained residue exist | Preserve both and stop. Do not pick the newest timestamp, merge rows, overwrite, or initialize another DB. |
| New primary is missing/corrupt after activation | Apply the existing new-root recovery contract; never silently return to old state. Invalid present primary remains authoritative and blocking. |

Legacy `state/projects/<id>` and backup-only sources remain supported through
their existing validators and bounded setup normalization. Do not turn an
unrecognized old directory into a fresh installation. Establish which source
schemas/layouts can be normalized in the private candidate, and prove the
same supported cases in unit 2; no live source-schema upgrade is implicit in
the design/prototype unit.

### Cutover And Old-Binary Barrier Candidate

The smallest candidate worth testing is a private staged copy plus a permanent
old-path barrier, not a continuing dual-write or synchronization layer:

1. Under the existing relevant state/artifact/Runner locks, reject busy or
   unsafe state and establish a coherent source observation. Keep a complete
   recoverable source set before replacing any old entry.
2. Build a private candidate below `.taskgov/` through the existing storage and
   artifact primitives. Validate DB/identity/binding, backup envelope,
   Evidence/Viewer and Runner inventory. Preserve original material until the
   transition's outcome is known; do not use raw copying of an open SQLite file.
3. After source-drift checks and writer quiescence, durably prepare a small
   recognized non-SQLite retirement file beside the old canonical primary and
   atomically replace that validated primary. For a missing primary, publish
   the marker without replacing an unexpected entry. Do not rename the old
   primary away and then create the marker: that leaves an initialization gap.
   Preserve the original primary in the recoverable source set. Current old resolver
   code attempts a present primary and rejects an unreadable DB before backup
   or legacy fallback. Test the actual supported old binaries rather than
   assuming every historical version has this behavior.
4. Publish the complete candidate no-replace and activate it only after the
   qualified old binaries cannot use the old location normally. A small setup-owned transition record
   distinguishes private, fenced and activated phases. Publishing the activated
   record is the activation point; ordinary admission requires both that record
   and its matching old-path barrier. The record is internal migration metadata,
   not a new public CLI option or business schema.
5. A crash before activation leaves setup-resumable recovery material, not
   permission for ordinary initialization. A crash after activation keeps new
   state authoritative. Retry must validate the recorded phase, not guess
   from timestamps or repeat an uncertain destructive step.

The barrier contains no original business rows or caller-selected path. It is
not a second Task database. Ordinary new-state calls neither rewrite it nor
request access to protected package storage. New installations also need a
setup-time old-path barrier so a qualified old CLI cannot create a parallel project.
Never delete a barrier automatically during package replacement or rollback.

This candidate is deliberately NOT claimed to prevent arbitrary concurrently
running old executables: open handles and close/replace races differ by OS.
The selected boundary is the offline prerequisite and fail-fast lock order
above, with the private/fenced/activated interruption and retry rules below.
It does not claim automatic detection of every open old process or an online
cutover. If implementation cannot preserve that boundary without a broader
change, return the concrete decision to the user; do not silently weaken
acceptance or add a generic locking framework.

### Approved Old-Executable Boundary

The user confirmed on 2026-09-20 that preventing v0.1 re-execution is not
required. Do not add a legacy-location fence for that family or treat its
absence as an adoption blocker. Keep schema-v2 source-data migration support;
source compatibility is separate from executable rejection.

The barrier guarantee is limited to the qualified fixed-layout binaries:
v0.10.0 at `a9b80ce177a6dead10d51a070b76ff01f7af0294` and the pre-separation
baseline at `c997fb65d58c598dac20f430498edf58b612fe32`. The v0.1.0/schema-v2
upgrade baseline resolves `state/projects/<path-derived-id>` and may initialize
that location without consulting the fixed-primary marker; its re-execution
is outside this guarantee. Arbitrary historical `--db` selection is also
outside a canonical-location fence. This decision changes neither source-data
preservation nor the new CLI's single-active-state admission/recovery rules.

### Bounded Transition And Retry Candidate

Use one owned transition directory below `.taskgov/` for a recoverable source
set and private candidate, outside ordinary managed-backup pruning. The record
needs only a version, transition ID, phase, project ID, source layout/schema/
binding basis and source/prepared-candidate inventory digests. Fresh setup has
no source basis. The old marker binds its format/version, transition ID and
project ID; neither object accepts caller-selected paths or contains business
rows. Do not compare an active DB with its pre-activation content digest after
ordinary writes have begun.

Retry validates actual objects as well as the last phase, because filesystem
publication and phase recording are separate operations:

| Last record and observed objects | Explicit setup action |
|---|---|
| Private, unchanged old source | Revalidate the owned recovery set and private candidate before continuing. |
| Private, matching retirement marker already present | Validate source/candidate and recognize completed fencing; do not replace another file. |
| Fenced, complete candidate still private | Publish no-replace after validation. |
| Fenced, candidate already published at new `current` | Validate that exact publication and finish activation. |
| Activated, new primary missing or corrupt | Use only the existing new-root recovery contract. |
| Mismatched marker, source drift, competing DB or unexplained residue | Preserve material and stop; no automatic choice or cleanup. |

The experiment covers process interruption at these boundaries. Existing
file-flush and rename helpers do not establish power-loss durability across
both locations; do not advertise that stronger guarantee. Existing legacy
stage inventories also cannot simply be reused: their small legacy file set
does not cover current Evidence Bundles and Runner lifecycle trees. Unit 2
must preserve the current domain inventories through a narrowly owned
separation transition, not relax their validators or introduce a generic
filesystem migration framework.

### Rollback And Retention

Before activation, recovery may restore the validated recovery snapshot only
while no new business writes have been admitted, after validating the complete
transition. Preserve its schema, logical rows, identity, binding and managed
artifacts; an SQLite backup snapshot need not have the original DB-file bytes.
After activation and new writes, an old snapshot is stale: returning to it is
an explicitly selected paired rollback with possible loss of later work, not
an automatic error fallback. Follow the existing
[paired rollback boundary](release-install.md#release-upgrade-and-paired-rollback).
Keep code, config and generated-state sets compatible and keep only one active
location. No reverse-schema migration, automatic merge, or raw SQL repair is
part of this initiative. Retain the one inventoried recovery-source set without
automatic expiry or pruning. It is never an active DB or automatic fallback.
Deleting that recovery set later requires a separately authorized, exact-scope
cleanup; this initiative adds no cleanup scheduler or retention subsystem.

## Isolated Prototype And Adoption Decisions

Use only `検証用プロジェクト/state-layout-prototype-20260919/` for the approved
prototype, with separate ordinary-install and development-self-host fixtures.
Do not reuse or change prior Run inputs, outputs, or the repository's live
state. Start with synthetic records; later real-data validation uses a
separately identified isolated copy, never the live original.

For representative permission tests, the ordinary fixture itself must be the
Windows Codex workspace root with its `.agents` and `.git` protection active.
Running a nested fixture from a broader writable parent is not equivalent.
If that exact host context is unavailable, record the gap rather than calling
a nested-directory success proof of protection. Do not modify global Codex
settings or count ACL changes/admin/escalated execution as ordinary success.

Preparation can require separately recorded access for installing the copied
Skill and initializing protected metadata. Once prepared, invoke the copied
public CLI through the ordinary execution path: create/update a synthetic
Task and exercise enabled generated-artifact maintenance. Verify returned
results AND changed paths, including SQLite sidecars, locks and temporaries;
hash the protected code/config before and after. A read-only command or bare
file touch is not sufficient. The prototype's minimal resolver adaptation
stays in its copy and is not shipped as production implementation.

Resolve these concrete items inside unit 1 before adoption:

- Demonstrate ordinary new-root writes under the actual protected host context.
- Rehearse old CLI ordinary access and setup against the retirement file;
  test fresh-install and legacy/backup-only cases rather than only current DBs.
- Determine the transition record, OS-supported publication/lock ordering,
  recovery-source retention and crash/retry behavior; expose any unsupported
  concurrency rather than claiming a migration guarantee from a file move.
- Confirm config/Skill update preservation and that all state producers use
  the common resolver; ordinary consumers must not require old-location writes.

These are targeted design experiments. They do not replace unit 2's migration
and three-OS product regressions or unit 3's complete real-host workflow and
same-condition usage comparison. No permission or token reduction is promised.

## Execution Units And Gates

All three units are sequential in the existing default lane; none is optional.
No new Task decomposition or extra implementation phase is introduced.

| Unit / Task | Outcome and scope | Verification and exit |
|---|---|---|
| 1 / `tg_task_6ff71d970b206713` | Fixed-layout design, bounded isolated prototype, migration decisions, owner-change and acceptance mapping. No production switch. | Document/link checks, actual prototype observations, exact-target Tier 2 independent reviews twice; resolve medium/high findings and adoption decisions. Present adopted design before unit 2. |
| 2 / `tg_task_6dad6e196d536430` | Implement adopted resolver, setup/migration/recovery, all generated paths, installation/update and coupled current docs/tests. | Prior adopted design and implementation approval; original acceptance 1-4 plus acceptance 7 product regressions on supported Windows/Linux/macOS and Python configurations; release/document/Skill checks, existing CI, its own two Tier 2 reviews. |
| 3 / `tg_task_ec6802cc70baad9c` | Full Windows protected-host Task loop and controlled old/new comparisons using unit 2's identified verified candidate. | Original acceptance 5-6 and acceptance 7 real-host effect; registration/update/verification/reviews/completion/reopen/backup/Viewer, multiple fresh conversations with matched conditions, honest missing-data and OS limits, its own two Tier 2 reviews. |

Unit 1 remains incomplete until prototype and current review gates pass; a
review of this proposal alone cannot complete it. Unit 2 completion is not a
claim that unit 3's real-host effects were measured. Missing required test
environments remain missing, never silently converted to mock-only success.

## Responsibility And Formal-Switch Map

| Responsibility | Existing implementation / owner to change in unit 2 |
|---|---|
| Canonical paths, project root, ignore, physical containment | `state_resolver.py`, `state_paths.py`, `project_scope.py`; [package](specification.md#package-runtime-and-generated-state), [resolver](setup-state-design.md#fixed-state-resolver), [ignore](setup-state-specification.md#effective-git-ignore-preflight) |
| Preview, cutover, resume, paired recovery | `setup.py`, `state_transition.py`, `backup.py`, storage/repository layer; [setup behavior](setup-state-specification.md#setup-contract), [stages](setup-state-design.md#setup-plan-and-stages), [identity](setup-state-specification.md#stable-project-identity-and-relocation) |
| DB admission/transactions, initialization path and ID preservation | Storage and binding repositories, including the fixed-root assertion in `initialize_uuid_database`; [connection rules](design.md#journal-and-connection-rules), [database owner](database-design.md), [database behavior](database-specification.md) |
| Evidence/Viewer output, locks and temporaries | Existing publication/maintenance services; [Evidence](evidence-design.md#schema-v19-bundle-and-evidence-publication), [Viewer](viewer-design.md#snapshot-and-publication); generated formats and gates unchanged |
| Runner paths, locks, attempts, quarantine | Existing Runner lifecycle/service; [Runner](runner-execution-design.md#runner-parent-service-and-audit-graph); trust/opt-in/process guarantees unchanged |
| Installation, upgrade, package inventory, local config | [Release/install](release-install.md), package manifest, whole `.taskgov/` tracked-artifact exclusion in `tools/release_contract.py` and coupled tests; no config or generated state in release artifacts |
| Agent use and repository durability rules | Relevant `AGENTS.md` state-boundary rules, Skill/reference examples, README and plan decisions synchronized at the formal switch; no additional normal-loop call |

This plan adds only conditional routing and structural document checks now.
Current operational owners are not prematurely rewritten to advertise the
proposal. The implementation revision must update all affected owners and
direct consumers together, not rely on this conditional plan to fill a gap in
the current product contract.
