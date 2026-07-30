# NON-AUTHORITATIVE HISTORY — v0.10.0 Publication Capture

> [!CAUTION]
> This file preserves `docs/implementation-roadmap.md` exactly as it existed at publication commit
> `a9b80ce177a6dead10d51a070b76ff01f7af0294`, preceded only by this banner. It is not current authority.
> Use the active [implementation roadmap](../../../implementation-roadmap.md). Words such as current,
> approved, pending, or implemented below describe only the captured revision
> and cannot satisfy a current contract, verification, or review gate.

Source path: `docs/implementation-roadmap.md`
Source commit: `a9b80ce177a6dead10d51a070b76ff01f7af0294`
Current replacement: [docs/implementation-roadmap.md](../../../implementation-roadmap.md)

---

# task-governance-tool Current Implementation Roadmap

Status: the implemented baseline is v0.10.0, SQLite schema v16, and Viewer
snapshot v4 accepting source schemas v5-v16. TG-M18.4 fixed that baseline at
`b0df647d9caf693afc0ff46aecf71a2c4739c864`. TG-M19.0 fixed the release
correctness contract at `1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1`,
TG-M19.1 completed the active specification/design consolidation at
`cbf75372617e90ca0b54746ae27f24a4e67cb292`; TG-M19.2 through TG-M19.5 are
complete. TG-M19.6 accepted commit
`fe9fdafd207cab9d0966785f4b340fe3224397fa`, but its acceptance was invalidated
before any remote write when TG-M19.7 exposed the TG-M19.6A Contract-privacy
compatibility defect. TG-M19.6A is the current corrective unit; TG-M19.6 must
then be reaccepted at a new exact commit before a fresh TG-M19.7 approval.
TG-M12.3 remains independently blocked.

This active roadmap contains current execution rules, a concise completion
index, the one older approved blocked unit, and the current or remaining
TG-M19 units. Full milestone narratives and superseded forward-test evidence
are indexed by [`docs/history/README.md`](history/README.md) as immutable,
non-authoritative lineage. Historical wording never fills a current-contract
gap or revives a removed command, path, install layout, or workflow.

## Authority And Execution Rules

- Re-read root `AGENTS.md`, `docs/specification.md`, `docs/design.md`, this
  roadmap, and root `plan.md` before each milestone or execution unit.
- `docs/specification.md` owns supported product behavior,
  `docs/design.md` owns implementation structure, this roadmap owns execution
  order and gates, and `plan.md` owns current decisions and open issues. The
  project-local Task database is the operational Task/evidence record.
- Before implementation, declare the intended outcome, write scope,
  verification gate, and review tier. Do not expand a unit beyond its approved
  Contract.
- A sequential blocker stops its own lane or dependency chain, not unrelated
  ready work. Optional work may proceed only when its own dependencies and
  authority are satisfied.
- Verification must pass for the exact final revision. Tier 2 units require
  two independent current-target reviews with no valid High or Medium finding;
  Tier 1 requires one independent review or the documented fallback.
- Repository, target-project, Git, network, licensing, and publication writes
  are authorized only by the exact owning unit. A general roadmap approval
  does not authorize an external operation.
- Use only the current implemented public CLI. Planned surfaces inside a
  blocked or dependency-gated unit are acceptance boundaries, not commands to
  invoke before that unit is implemented and synchronized.
- Current work must be understandable from the five active governing
  documents and directly coupled implementation/tests without reading
  historical captures.

## Concise Completion Index

Each SHA below was independently resolved as a commit and verified as an
ancestor of the TG-M19.2 activation source
`cbf75372617e90ca0b54746ae27f24a4e67cb292`. For units represented in the
current Task database, the SHA is the recorded final-unit completion revision.
The first two rows predate durable Task completion records and therefore name
reviewed lineage-closing commits rather than retroactively invented evidence.

| Completed scope | Final Task, when present | Completion or lineage SHA | Reusable evidence boundary |
|---|---|---|---|
| TG-M1.1-TG-M4.3 MVP | pre-Task-DB | `86fccf389e6e16c6ba2fdcaf5acd39d32c26b911` | Full MVP contract pass. |
| TG-M4.O1 and TG-M5.O1-TG-M5.O6 follow-ups | pre-Task-DB | `b7b41ad458f59f9ad2ae8dcb0a7c56493d24a6ab` | Project-scoped install-guidance closure after the reviewed MVP follow-ups. |
| TG-M6 | `tg_task_306c6ac4199122fb` | `f017ee228d435d892fb7136c5e79b3063320fac5` | Stored `legacy_unverified` completion revision and legacy-main rehearsal source. |
| TG-M7 | `tg_task_f18ca3ea0982df9a` | `a9843337666c55a6866edf616b4b3d47af76426a` | Stored `legacy_unverified` completion revision; `cdd4acc7e51c0f9a4053eddba1b3d19e2b40fe93` is the later status-reconciliation descendant. |
| TG-M8 | `tg_task_5950b5447de43993` | `0af9d1b57d648c801b021f5a6fff5779b04b0fb8` | Governance-hardening acceptance. |
| TG-M9 | `tg_task_ca49db07778db0e4` | `57583dba5dc1c04fe5f167602a4f89f1dd464639` | Paused-work visibility acceptance. |
| TG-M11 | `tg_task_b62ad41d367d5e01` | `b46a188f3b8df3d9702f99b0eb190f04923bdc1f` | Completion-integrity acceptance. |
| TG-M12.1, TG-M12.2, TG-M12.O1, and TG-M12.O2 | `tg_task_a23869622600a71c` | `143090ffb5e140804e27e350769c319e3be6a237` | Scope control, local handoff, Task Contract, Effort Advisory, and package self-status; TG-M12.3 is excluded and remains blocked below. |
| TG-M13 | `tg_task_2302ce786a28d1b0` | `a024c07dc9d587d62ecf3705a409ac62806ce11b` | SQLite/read-transaction and project-scoped release-hardening acceptance. |
| TG-M14 | `tg_task_c30e126d19b2a0e2` | `b1ce82f1aeffc226ba827231228727ee5a2b35c5` | v0.8.0 daily-UX and local-continuity acceptance. |
| TG-M15 through TG-M15.6 | `tg_task_1c4ab208be113c8a` | `1927b65fc2437ec799a6591ec1db9f7cb4373fe8` | Pre-publication corrections and bounded Viewer reload/state acceptance. |
| TG-M16 | `tg_task_1a2f5af057e45ef1` | `e0b109d67074015b5494757fa64cf7524ebaa92d` | Reduced-loop behavioral acceptance; TG-M16.3 remains cancelled. |
| TG-M17 | `tg_task_c8054d1c57087956` | `29ac34d8c6e96c2cf091e504abe05b5485a54dd0` | Stable identity and relocation acceptance. |
| TG-M18 | `tg_task_fa3a57ae3089e3fc` | `b0df647d9caf693afc0ff46aecf71a2c4739c864` | v0.10.0/schema-v16/Viewer-v4 completion-cycle-history baseline. |
| TG-M19.0 | `tg_task_2b95de205e3f92e3` | `1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1` | Release correctness and cutover contract. |
| TG-M19.1 | `tg_task_ba59e260cc2c58a6` | `cbf75372617e90ca0b54746ae27f24a4e67cb292` | Active specification/design consolidation and indexed immutable history. |
| TG-M19.2 | `tg_task_20fd398141755a65` | `2af0382c54615640fbd8475a59f374b1b71804c4` | Roadmap/plan/evidence authority split. |
| TG-M19.3 | `tg_task_b71ac20177aae41a` | `4040e923cfdbd8b3f65d8883187a57578d64c092` | Apache-2.0 licensing-authority and attribution acceptance. |
| TG-M19.4 | `tg_task_7cc967fc224440cb` | `639bc74adfd1f5e15996d1416bd064f1b9303edc` | Public package, release guidance, and review-trust synchronization. |
| TG-M19.5 | `tg_task_bd93525dc71f4dcd` | `fe9fdafd207cab9d0966785f4b340fe3224397fa` | Isolated legacy upgrade and paired rollback rehearsal. |
| TG-M19.6 first acceptance, now invalidated | `tg_task_67a3f3e73b913bfb` | `fe9fdafd207cab9d0966785f4b340fe3224397fa` | Historical completion cycle only; TG-M19.6A requires a new candidate and assets before any remote write. |

Terminal units with no completion SHA are not missing evidence:

- TG-M10.1 Bounded Acceptance Checklist was cancelled and never became current
  product scope.
- TG-M15.7 Source Self-Host Viewer Refresh Profile was cancelled; its later
  consideration remains a pending local handoff.
- TG-M16.3 Atomic Versioned Bootstrap Policy Ensure was cancelled; TG-M16.4
  explicitly completed without it.

## Approved Blocked Work

### TG-M12.3 Versioned Issue Adapter, Claims, And Due Sync

Task: `tg_task_1f7503aca5e32cdc`
Kind: sequential
Lane: `SCOPE-CONTROL`
Lane order: 40
Review tier: Tier 2
Depends on: completed TG-M12.2, a separately approved versioned Issue Skill
intake contract, governing permission updates, and explicit user approval of
the integration boundary
Status: blocked
Blocker: `Requires TG-M12.2, a separately approved Issue Skill intake contract, governing permission updates, and explicit user approval of the integration boundary.`

Intended outcome:

- Connect existing and new pending handoffs through one future explicitly
  enabled, versioned local Issue intake boundary.
- Add claim/lease safety, fixed bounded retry, acknowledgement reconciliation,
  and deterministic due processing without importing Issue lifecycle into the
  Task Skill.

Write scope after every blocker is separately satisfied:

- one concrete local adapter and explicit project-scoped configuration;
- claim, acknowledgement, fixed retry, due processing, crash reconciliation,
  and bounded delivery-status projection;
- synchronized specification, design, Skill/reference, README, release, and
  offline integration tests; and
- no public command name is active or invocable from this planned unit until
  its owning contract, implementation, and package synchronization complete.

Mandatory constraints:

- Do not start until the exact Issue intake/transport/version contract and
  governing permission update exist.
- Keep the same current local handoff-recording workflow regardless of receiver
  presence. Pending delivery never expands the source Task's acceptance.
- Never open, initialize, migrate, or edit an Issue database directly; never
  use a shell, URL, network, GitHub, or arbitrary dynamic project code.
- Store only a bounded receiver acceptance receipt. Exclude semantic duplicate
  handling, priority, triage, resolution, resulting Task creation, Issue
  import/lifecycle sync, and reverse synchronization.
- Exact lease duration, batch bound, retry stages, and acknowledgement
  transport must come from the separately approved receiver contract rather
  than inference from this historical plan.

Verification:

- Cover absent, disabled, success, retryable-result stages independent of claim
  count, permanent, exhausted, pending drain, crash reconciliation, claim and
  withdraw race, receiver idempotency, permission, version, privacy, no shell
  or network, zero additional LLM decisions, and the full integration suite.
- Prove one receiver item under concurrent claim/acknowledgement, deterministic
  due state, no local-withdrawn plus receiver-accepted race, and fail-closed
  pending behavior for permission or version mismatch.
- Run the exact current documentation, package, privacy, concurrency, offline
  suite, diff, and two-current-target Tier 2 review gates.

## TG-M19 Release Correctness

TG-M19 is one sequential release lane plus the independently ordered
TG-M19.6A corrective Task. It converts the completed v0.10.0 candidate into
concise active authority, licensed and rehearsed release material, and a
deliberately staged remote publication. TG-M19.6A makes the existing privacy
contract and implementation agree; it adds no public command, schema change,
Viewer change, normal-loop judgment, or new authority.

The immutable M18 baseline for this lane is:

- M18.4 completion SHA:
  `b0df647d9caf693afc0ff46aecf71a2c4739c864`;
- legacy `main` reference:
  `f017ee228d435d892fb7136c5e79b3063320fac5`;
- authoritative development lineage:
  `codex/project-scoped-install-guidance`, which is a descendant of that
  legacy `main`;
- package version `0.10.0`, SQLite schema v16, Viewer snapshot v4 accepting
  source schemas v5-v16;
- proposed immutable-by-policy lightweight tag `v0.10.0`;
- publication remote `origin` and repository identity
  `VAiring/task-governance-tool`;
- installable asset name `task-governance-tool-0.10.0.zip`, containing exactly
  one `task-governance-tool/` root; and
- checksum asset name `task-governance-tool-0.10.0.zip.sha256`, whose exact
  UTF-8-without-BOM content is the lowercase 64-character archive SHA-256,
  two ASCII spaces, the archive basename, and one LF.

The M18.4 SHA is a fixed prerequisite, not the later Release Candidate SHA.
TG-M19.0 through TG-M19.5 may add reviewed documentation, licensing, package,
tests, fixtures, or release-process commits. TG-M19.6 alone freezes the later
exact Release Candidate and its asset bytes. Any tracked change after that
freeze invalidates TG-M19.6 and every downstream candidate gate.

Remote-tracking references are observations, not publication authority.
TG-M19.6 and every remote gate must freshly resolve exactly one fetch endpoint
and exactly one push endpoint, require both to normalize to the approved
repository identity, then read the remote and freeze or revalidate the
expected `origin/main` and candidate-branch heads. Missing, additional, or
duplicate endpoints stop even when their normalized identities match. A stale
local `origin/*` reference never satisfies a remote gate. Normal non-force
pushes are the only permitted ref updates.

The three external mutation approvals are separate and cannot be inferred from
one another:

1. TG-M19.7: publish the exact accepted candidate to the named candidate
   branch and run the named exact-SHA candidate CI route;
2. TG-M19.8: fast-forward `main` from the separately approved expected remote
   head to that unchanged candidate; and
3. TG-M19.10: create the approved tag and GitHub Release with the already
   accepted assets.

TG-M19.3 has its own non-publication user decision for licensing authority.
No earlier approval authorizes a later gate.

The remaining pre-created M19.7-M19.10 Task rows remain planning records at
Contract revision zero until their own start. First correct any verification or
review-tier drift by a separate metadata-only edit while status is unchanged.
Then use the specification's exact roadmap-bullet mapping in a second
Contract-options-plus-`in_progress` edit to activate revision 1. Do not
bulk-edit them, infer a new requirement, combine metadata with Contract
activation, or begin work with an empty Review Packet. A named user-decision
or external-write blocker must be satisfied first. The Contract labels the
roadmap status only as the satisfied activation-source status; it must not copy
`blocked` or `ready` as if that were the current Task status.

Before requesting approval for M19.8 or M19.10, a read-only upstream
run-ID/attempt preflight may avoid a stale question but persists no approval.
After approval, first activate the later Task and thereby persist its Approval
object, then repeat the check before the first external write. A mismatch
returns the later Task to `blocked` before reopening the prior gate so lane
invariants remain valid. When that gate is reaccepted, unchanged mutation
values resume under the stored Contract; changed values require a fresh
approval and Contract revision.

### TG-M19.2 Roadmap Plan And Evidence Split

Task: `tg_task_20fd398141755a65`
Kind: sequential
Lane: `TG-M19-RELEASE-CORRECTNESS`
Lane order: 30
Review tier: Tier 2
Depends on: completed TG-M19.1
Status: completed at `2af0382c54615640fbd8475a59f374b1b71804c4`

Intended outcome:

- Reduce the active roadmap to approved unfinished work plus a concise
  completion index, and reduce `plan.md` to current decisions and open issues.
- Move historical milestone narratives and superseded forward-test evidence
  behind the same indexed non-authority boundary.

Write scope:

- root `AGENTS.md` only for the durable roadmap/plan routing switch;
- active `docs/implementation-roadmap.md` and `plan.md`;
- immutable roadmap/plan lineage and superseded forward evidence under
  `docs/history/v0.10.0/`, its append-only index, and path-sensitive tests;
- no runtime, schema, CLI, package behavior, LICENSE, or external operation.

Verification:

- switch AGENTS/roadmap/plan authority together in one internally valid
  reviewed revision;
- preserve exact unfinished Task IDs, dependencies, blockers, decisions, open
  issues, completion SHAs, and reusable evidence without reviving removed
  commands as guidance;
- retain the full unstarted TG-M19.3-TG-M19.10 sections verbatim so later
  Contract activation has a stable deterministic source;
- history banners, index entries, links, content retention, and required
  rereads are complete and unambiguous;
- docs consistency, focused path/authority tests, full offline suite,
  `git diff --check`, and two exact-target Tier 2 reviews pass.

### TG-M19.3 Apache-2.0 License And Attribution Boundary

Task: `tg_task_b71ac20177aae41a`
Kind: sequential
Lane: `TG-M19-RELEASE-CORRECTNESS`
Lane order: 40
Review tier: Tier 2
Depends on: completed TG-M19.2 and the licensing decision below
Status: completed at `4040e923cfdbd8b3f65d8883187a57578d64c092`

Required user decision:

- explicitly confirm authority to license all included personal work;
- provide the exact copyright-holder text and covered tracked/shipped scope;
- identify any employer, contractor, contributor, copied-reference, or
  third-party restriction or applicable open-source policy; and
- approve Apache License 2.0 for the resolved scope. Git author identity alone
  is never proof of ownership or authority.

Intended outcome:

- Apply the official unmodified Apache License 2.0 text to the licensable
  repository and installable package only after the required authority is
  recorded.
- Make attribution and exclusions honest without manufacturing NOTICE duties.

Write scope:

- matching root and `task-governance-tool/LICENSE` files;
- a NOTICE only when a concrete attribution duty requires it;
- attribution/public guidance, package required-file inventory, manifest,
  CI/release checks, and focused licensing tests;
- ignored root references remain excluded unless the user deliberately brings
  them into the tracked/shipped scope;
- no runtime behavior, schema, command, generated state, or external operation.

Verification:

- audit all tracked code, docs, HTML, tests, fixtures, workflow files, and
  shipped package files against the user's authority statement;
- resolve or exclude anything that is not licensable under the approved scope;
- root/package LICENSE bytes match the official text, package LICENSE is
  shipped and manifest-covered, and NOTICE exists only for concrete duties;
- presence/equality/inventory checks, full offline suite, package isolation,
  `git diff --check`, and two exact-target Tier 2 reviews pass.

### TG-M19.4 Public Package And Review Trust Synchronization

Task: `tg_task_7cc967fc224440cb`
Kind: sequential
Lane: `TG-M19-RELEASE-CORRECTNESS`
Lane order: 50
Review tier: Tier 2
Depends on: completed TG-M19.3
Status: completed at `639bc74adfd1f5e15996d1416bd064f1b9303edc`

Intended outcome:

- Synchronize README, release guidance, active Skill metadata, one-level
  references, CLI help, release notes, and manifest with the consolidated
  v0.10.0 authority and licensing boundary.
- State the actual trust provided by stored review evidence without implying
  provenance that the tool does not prove.

Write scope:

- README, release/install and release-note guidance, active Skill metadata and
  one-level references, help text, manifest/integrity inventory, CI, and
  directly coupled tests;
- canonical UTF-8/LF Release body `docs/releases/v0.10.0.md`, exact Release
  title `task-governance-tool v0.10.0`, and the documented
  `git-archive-v1` recipe used later without a tracked builder;
- no new command, option, schema, review kind, identity proof, external
  operation, or target-project mutation.

Verification:

- public and packaged guidance agrees with runtime on 20 command leaves,
  v0.10.0/schema v16/Viewer v4 sources v5-v16, supported install layout,
  licensing, upgrade/rollback, and release identity;
- wording says only that current-target/current-generation recorded receipts
  and findings deterministically satisfy the configured gate;
- distinct reviewer keys prove distinct stored strings only, not a distinct
  person, LLM, machine, independent process, or authenticated provenance;
- the canonical Release body bytes, workflow path/name/job/runtime matrix, and
  archive recipe agree with `Release Candidate Evidence v1`;
- isolated package, help, doctor, manifest, full offline/forward suites,
  `git diff --check`, and two exact-target Tier 2 reviews pass.

### TG-M19.5 Legacy Upgrade And Paired Rollback Rehearsal

Task: `tg_task_bd93525dc71f4dcd`
Kind: sequential
Lane: `TG-M19-RELEASE-CORRECTNESS`
Lane order: 60
Review tier: Tier 2
Depends on: completed TG-M19.4
Status: completed at `fe9fdafd207cab9d0966785f4b340fe3224397fa`

Intended outcome:

- Rehearse clean installation and the supported transition from the exact
  legacy-main package/schema-v2 baseline into the later candidate.
- Prove an honest rollback that restores package, pre-migration database, and
  managed artifacts as one compatibility point.

Write scope:

- isolated fixtures, acceptance harnesses, release rehearsal evidence, and
  directly coupled documentation/tests;
- all package/state copies live outside canonical project state;
- no canonical database, target project, remote ref, tag, or Release change,
  and no product behavior. A discovered behavior defect blocks this unit or
  becomes a separately authorized Task rather than expanding rehearsal scope.

Verification:

- use legacy commit `f017ee228d435d892fb7136c5e79b3063320fac5`
  with its v0.1.0 package and schema-v2 state, then upgrade isolated copies to
  the exact candidate package and schema v16;
- preserve supported layouts, Task/event/completion evidence, IDs, privacy,
  migration recovery, and package integrity;
- restore the legacy package, pre-migration DB, and managed artifacts together
  and prove restart; never run old code against the migrated schema;
- user-wide and custom-`--db` layouts remain explicitly unsupported, are
  refused without mutation, and are not converted into a migration path;
- Git rollback remains a new forward-fix candidate, never force, reset,
  history replacement, or tag rewrite;
- full migration/recovery/package checks, offline suite, `git diff --check`,
  and two exact-target Tier 2 reviews pass.

### TG-M19.6A Contract Privacy Identifier Correction

Task: `tg_task_2fc57c401dd2855d`
Kind: optional
Review tier: Tier 2
Depends on: explicit user reapproval and completed TG-M19.5
Status: in_progress

Intended outcome:

- Accept the mandated structured M19.7 dispatch authorization identifier when
  it is not a credential header.
- Preserve rejection of actual Authorization header forms and every other
  existing privacy pattern.
- Invalidate the prior candidate locally and require TG-M19.6 reacceptance
  before any remote write.

Write scope:

- narrow only direct Authorization-assignment handling through the bounded
  release-counter exception in `tasks.py`;
- directly coupled privacy and Task Contract regression tests;
- synchronized active specification, design, roadmap, plan status, and package
  integrity manifest; and
- no public CLI, SQLite schema, Viewer, release identity, accepted-asset, Git
  remote, or network change.

Verification:

- prove the exact normalized M19.7 Required user approval constraints pass the
  common privacy guard;
- prove start-of-text and hyphen-prefixed Authorization assignments, Bearer
  credentials, and every existing secret/raw-content pattern remain rejected;
- run focused privacy and Contract tests, the complete offline suite, package
  self-check, and `git diff --check`; and
- obtain two independent Tier 2 passes on one exact correction commit before
  completing this Task and reopening TG-M19.6.

### TG-M19.6 Exact-SHA Local Release Candidate Acceptance

Task: `tg_task_67a3f3e73b913bfb`
Kind: sequential
Lane: `TG-M19-RELEASE-CORRECTNESS`
Lane order: 70
Review tier: Tier 2
Depends on: completed TG-M19.5 and TG-M19.6A
Status: reacceptance required after TG-M19.6A

Intended outcome:

- Accept one clean exact commit and deterministic installable archive locally,
  without changing a managed file or any remote Git state.
- Freeze every value needed for the three later exact-value approvals.

Write scope:

- an isolated clean checkout of the exact candidate and the three accepted
  files under ignored repository-root
  `dist/task-governance-tool-0.10.0/<RC_SHA>/<EVIDENCE_SHA256>/`;
- ignored Task governance evidence may record the accepted values;
- no tracked-file edit, canonical-state rehearsal, push, PR, workflow
  dispatch, `main` change, tag, or GitHub Release.

Verification:

- run the exact `git-archive-v1` recipe twice from the repository root with the
  full `RC_SHA` as tree-ish and `task-governance-tool` as the sole pathspec;
  record the exact Git version and require byte-identical results;
- require ignored physical `dist/` staging, exact archive/checksum/
  `release-candidate-v1.json` bytes, evidence-generation isolation, bounded
  partial recovery, atomic final-directory rename, deterministic rediscovery,
  no conflicting accepted entry, and retention through M19.10; loss or
  mismatch returns to M19.6 with a new `gen` and evidence directory;
- verify exact inventory/exclusions, matching official root/package LICENSE
  files, manifest coverage/hashes, 20 leaves, doctor, and an isolated install;
- run Python 3.12/3.14-equivalent offline and forward suites, migration/
  rollback acceptance, `git diff --check`, and two exact-target Tier 2 reviews;
- freshly normalize the `origin` fetch/push repository identity, then read
  remote heads and tag namespace without changing them or retaining raw URLs;
- record and read back the specification's complete canonical
  `Release Candidate Evidence v1` object in the M19.6 Task checkpoint,
  including a fresh mechanical `gen`, TG-M19.5's canonical Git completion
  revision as `legacy`, and the CI workflow/check set; prove `legacy` resolves
  as a commit, equals the stored TG-M19.5 completion revision, and is an
  ancestor of `rc`, and retain the accepted local asset bytes;
- set `git_commit` review target `rc`, supply the exact checkpoint plus matching
  commit/assets/evidence to both Tier 2 reviewers, and complete with the same
  canonical `git_commit`; any record or asset change re-sets the target,
  advances its generation, and repeats acceptance;
- require the proposed tag to be absent. Any tracked or accepted-asset byte
  change invalidates this acceptance and returns the lane to a new M19.6 SHA.

### TG-M19.7 Release Candidate Remote CI Gate

Task: `tg_task_5b8796de20a32d39`
Kind: sequential
Lane: `TG-M19-RELEASE-CORRECTNESS`
Lane order: 80
Review tier: Tier 2
Depends on: completed TG-M19.6 and the approval below
Status: blocked on fresh exact-value approval

Required user approval:

- name remote `origin`, repository `VAiring/task-governance-tool`, the accepted
  Release Candidate SHA, expected candidate-branch SHA, candidate branch,
  expected `origin/main` SHA, proposed tag, archive and checksum names/hashes,
  the exact CI workflow/dispatch boundary, and acknowledge the exact resulting
  GitHub Actions run as the durable external completion source. The initial
  approval names `dispatch_authorization=1`; every later fresh dispatch
  approval increments it by exactly one.

Intended outcome:

- Publish only the accepted commit to the approved candidate branch and prove
  Windows Python 3.12/3.14 CI on that unchanged SHA before any `main` update.

Write scope:

- one normal non-force push of the accepted SHA to the approved candidate
  branch, the approved exact-ref workflow dispatch, read-only remote evidence,
  and local Task/review evidence;
- no PR creation; ambient PR checks are read-only non-gating context only;
- no `main` update, tag, Release, accepted-asset replacement, or managed-file
  change.

Verification:

- freshly normalize both remote endpoints and require canonical repository
  identity without retaining or printing raw URLs or credentials;
- fresh remote read has exactly three outcomes: the accepted SHA means the
  branch stage is already complete and needs no push; the approved expected
  pre-write SHA permits the one normal non-force push; any other value stops;
- after a performed or already-complete branch stage, confirm the remote
  candidate head equals the accepted SHA; a rejected push stops the gate;
- because feature-branch push is not a CI trigger and pull-request CI may test
  a synthetic merge SHA, dispatch the existing CI workflow at the exact remote
  candidate ref and require the run's `head_sha` to equal the accepted SHA;
- before dispatch, select only runs matching the exact workflow,
  `workflow_dispatch` event, candidate ref, and accepted SHA. Reuse the
  greatest integer run ID and then greatest attempt when it succeeded, wait
  when that attempt is queued or running, stop when it completed
  non-successfully, and dispatch only when no exact run exists;
- immediately before that one dispatch, append and read back the canonical
  `m19.7-dispatch-intent-v1` checkpoint with the approved
  `dispatch_authorization`. Reuse its `gen` and authorization value in final
  evidence and never dispatch again for that generation after success, timeout,
  ambiguous response, or process loss; if no exact run becomes observable,
  stop until the incremented fresh approval is stored in a new Contract
  revision with the specified change reason, authority reference, and new
  `gen`;
- require both Windows Python 3.12 and 3.14 jobs and all required checks to
  pass; an ambient PR check never replaces exact-SHA evidence;
- after an ambiguous push result, read back: the accepted SHA is success, the
  approved old head permits only the identical normal non-force retry, and any
  other head stops the gate;
- any code, document, manifest, LICENSE, or artifact change invalidates the
  candidate and returns to TG-M19.6;
- record and read back the canonical `m19.7-evidence-v1` checkpoint, form the
  specified matching `external_revision` target from its exact selected
  run/attempt rather than a URL or local fingerprint, supply the sanitized
  remote/run/asset evidence to two current-generation
  Tier 2 reviews, and complete with the identical approved external revision
  and exact specified reason.

### TG-M19.8 Main Fast-Forward Cutover

Task: `tg_task_79791addafcf0e00`
Kind: sequential
Lane: `TG-M19-RELEASE-CORRECTNESS`
Lane order: 90
Review tier: Tier 2
Depends on: completed TG-M19.7 and the approval below
Status: blocked on fresh exact-value approval

Required user approval:

- name remote `origin`, repository `VAiring/task-governance-tool`, the unchanged
  accepted candidate SHA and expected current `origin/main` SHA, and explicitly
  authorize the one direct fast-forward update of `refs/heads/main`.

Intended outcome:

- Move remote `main` only by fast-forward from the approved legacy head to the
  CI-accepted candidate.

Write scope:

- fresh fetch/readback, ancestor proof, one normal non-force direct push of the
  accepted SHA to `refs/heads/main`, and local Task/review evidence;
- no merge commit, squash, rebase, force, history replacement, additional
  commit, tag, Release, or asset change.

Verification:

- freshly normalize both remote endpoints to the approved repository identity;
- immediately before changing `main`, reselect the latest exact M19.7
  workflow-dispatch run and require its run ID/attempt still equals the
  M19.7 checkpoint and remains successful; any change returns to fresh M19.7
  acceptance rather than cutting over;
- fresh remote `main` has exactly three outcomes: the accepted candidate means
  the cutover stage is already complete and needs no push; the separately
  approved expected SHA permits the one normal non-force push after proving
  that SHA is an ancestor of the candidate; any other SHA stops;
- branch-protection or permission refusal stops the lane; do not route through
  a PR merge mode that changes or adds commits;
- after the push, require remote `main` to equal the accepted SHA exactly;
- after an uncertain transport result, read back first: accepted SHA is
  success, approved old SHA permits only the same non-force retry, and any
  other SHA stops the lane;
- record and read back the canonical `m19.8-evidence-v1` checkpoint, including
  the exact selected M19.7 run ID and attempt, set `git_commit` target `rc`,
  pass two matching Tier 2 reviews, and complete with the same canonical
  `git_commit`. No tag or Release is created.

### TG-M19.9 Remote Main Exact-SHA Release Gate

Task: `tg_task_418792bf98f211af`
Kind: sequential
Lane: `TG-M19-RELEASE-CORRECTNESS`
Lane order: 100
Review tier: Tier 2
Depends on: completed TG-M19.8
Status: ready, dependency-gated

Intended outcome:

- Verify the updated remote `main`, its automatically triggered required CI,
  and the unchanged accepted release assets before allowing publication.

Write scope:

- read-only remote/ref/Actions/asset inspection and local Task/review evidence;
- no managed-file, branch, ref, tag, Release, or asset mutation.

Verification:

- freshly normalize both remote endpoints to the frozen repository identity;
- remote `main` equals the accepted candidate SHA exactly;
- required Windows Python 3.12/3.14 and release checks passed for that exact
  `main` SHA, not another branch, merge, or rerun revision;
- select only exact `push` runs for the named workflow, `main` ref, and
  candidate SHA; the greatest integer run ID and then greatest attempt is
  authoritative and must be successful;
- version, tag proposal, archive bytes/name/hash, checksum bytes/name/hash,
  release title/notes digest, and legacy-reference evidence still equal the
  M19.6 freeze;
- on failure, publication stops and correction uses a newly accepted
  forward-fix candidate through M19.6 onward; never force `main`, retag, or
  delete history as routine rollback;
- record and read back the canonical `m19.9-evidence-v1` checkpoint with
  `legacy` byte-equal to `rc-v1.legacy`, hash its summary into the current
  `diff_fingerprint`, pass two matching Tier 2 reviews, and complete with
  `commit_not_required`.

### TG-M19.10 Tag And GitHub Release Publication

Task: `tg_task_9807bdc4ddc5ba37`
Kind: sequential
Lane: `TG-M19-RELEASE-CORRECTNESS`
Lane order: 110
Review tier: Tier 2
Depends on: completed TG-M19.9 and the approval below
Status: blocked on fresh exact-value approval

Required user approval:

- name remote `origin`, repository `VAiring/task-governance-tool`, the exact
  accepted `main` SHA, version, lightweight tag, final Release visibility,
  whether temporary draft staging is permitted, Release title and notes
  digest, both accepted asset names and hashes, and acknowledge the exact
  resulting GitHub Release as the durable external completion source.

Intended outcome:

- Create the immutable-by-policy release tag and GitHub Release at the accepted
  `main` SHA and publish only the already accepted archive/checksum bytes.

Write scope:

- the approved tag ref, approved GitHub Release metadata/visibility, the two
  accepted assets, readback/install smoke evidence, and local Task evidence;
- no managed-file or branch change, asset rebuild/replacement, retag, Release
  deletion, or history rewrite.

Verification:

- freshly normalize both remote endpoints to the approved repository identity;
- immediately revalidate remote `main`, release identity, local accepted asset
  hashes, and tag state;
- before the first tag or Release write, reselect the latest exact M19.9 main
  push run and require its run ID/attempt still equals the M19.9 checkpoint and
  remains successful; any change returns to fresh M19.9 acceptance;
- when `v0.10.0` is absent, create and normally push that lightweight tag at
  the exact accepted `main` SHA; when it exists, require the unpeeled tag ref
  to identify the commit object directly and its object type to be `commit`,
  then reuse it only at that exact SHA. An annotated tag is a conflict even
  when its peeled SHA matches; any other target or type stops;
- when approved, use a temporary draft to upload and hash-readback both assets
  before changing to the approved final visibility; otherwise accept the
  explicitly approved partial-publication risk;
- an exact pre-existing stage from an interrupted approved attempt may be
  resumed idempotently: accept an exact tag/metadata/asset, upload only a
  missing accepted asset, and stop on any conflicting target, metadata, name,
  or byte hash;
- verify tag, remote `main`, Release metadata, archive, and checksum all bind
  the same candidate, then run default-branch, tag, and published-archive
  isolated install smokes;
- record and read back the canonical `m19.10-evidence-v1` checkpoint, form the
  specified matching `external_revision` target from its exact Release ID
  rather than a URL or local fingerprint, and include the unchanged M19.9 run
  ID/attempt in the checkpoint; supply the exact sanitized
  ref/Release/asset/smoke evidence to two current-generation Tier 2 reviews,
  and complete with the identical approved external revision and exact
  specified reason;
- never move/delete the tag, replace an asset, delete the Release, or rewrite
  history on mismatch. Stop and report; a published correction requires a new
  version, candidate, tag, and Release.

## Roadmap Completion Criteria

The currently approved roadmap is complete when:

- all approved sequential units through TG-M14.7 and approved TG-M15 units
  through TG-M15.6 are complete;
- TG-M16.0, TG-M16.1, TG-M16.2, and TG-M16.4 are complete, with TG-M16.3
  remaining cancelled;
- TG-M17.0 through TG-M17.5 are complete in lane order, including correction of
  the two stale future Task-record references before M17.4 and M17.5 begin;
- TG-M18.0 through TG-M18.4 complete in lane order with history remaining
  audit-only and the final v0.10.0/schema-v16/Viewer-v4 lifecycle matrix
  accepted;
- TG-M19.0 is complete against frozen M18.4 SHA
  `b0df647d9caf693afc0ff46aecf71a2c4739c864`;
- TG-M19.1 and TG-M19.2 complete their active/history authority switch before
  the user is asked for TG-M19.3 licensing authority;
- TG-M19.3 through TG-M19.5 complete in lane order with exact licensing and
  paired legacy rollback; independently ordered TG-M19.6A completes before
  TG-M19.6 reaccepts one frozen clean local Release Candidate;
- TG-M19.7, TG-M19.8, and TG-M19.10 each receive their own fresh exact-value
  approval, while TG-M19.9 proves the unchanged remote-main SHA between
  cutover and publication;
- publication leaves remote `main`, lightweight tag `v0.10.0`, GitHub Release,
  installable archive, and checksum bound to the same accepted candidate;
- every unit's documented verification and review gate has passed for its exact
  final revision;
- no valid High or Medium review finding remains unresolved;
- deferred items remain outside the active product contract; and
- no external mutation occurs before its exact gate and separate explicit user
  authorization.
