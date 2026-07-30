# task-governance-tool Current Implementation Roadmap

Status: v0.10.0 is published with prerelease visibility from exact commit
`a9b80ce177a6dead10d51a070b76ff01f7af0294`. Remote `main` and the
lightweight tag `v0.10.0` resolve to that commit, and GitHub Release
`362617903` contains the accepted archive and checksum. TG-M19.0 through
TG-M19.10, including TG-M19.6A and TG-M19.6B, are complete. The approved
post-release sequence is TG-M19.11 through TG-M19.14. TG-M12.3 remains
independently blocked. TG-M19.11 and TG-M19.12 are complete; TG-M19.13 is the
active source-revision unit.

This active roadmap owns approved execution order, verification gates, review
tiers, and the concise completion index. The project-local Task database owns
live operational state and evidence; this file does not mirror volatile
handoff rows or generated state. Completed one-time release execution detail is
indexed by [`docs/history/README.md`](history/README.md) as immutable,
non-authoritative lineage and never satisfies a current gate.

## Authority And Execution Rules

- Re-read root `AGENTS.md`, `docs/specification.md`, `docs/design.md`,
  this roadmap, and root `plan.md` before every execution unit.
- `docs/specification.md` owns product behavior, `docs/design.md` owns
  implementation structure, this roadmap owns execution order and gates, and
  `plan.md` owns current decisions and open issues.
- Before implementation, declare intended outcome, write scope, verification
  gate, and review tier. Do not expand an execution unit beyond approved
  authority.
- Sequential blocking is lane-local. A failed gate blocks completion of the
  affected unit but does not stop safe repair or unrelated authorized work.
- Verification must pass for the exact final target. Tier 2 requires two
  distinct current-target independent PASS receipts, no current-generation
  changes-requested receipt, and no unresolved High or Medium finding from any
  generation. Tier 1 requires one independent pass or its documented fallback.
- Repository, target-project, Git, network, licensing, and publication writes
  require the exact owning authority. General roadmap approval never grants a
  materially different mutation.
- Use only the implemented public CLI. Planned surfaces are acceptance
  boundaries, not commands, until their implementation and synchronization
  gates complete.
- Current behavior must be implementable from the active governing documents
  and directly coupled code/tests without historical text.

## Concise Completion Index

The first two rows predate durable Task completion records and name reviewed
lineage-closing commits rather than invented evidence. For later rows, the
revision is the public Task completion evidence. An external revision or
commit-not-required fingerprint is shown instead of inventing a Git commit.

| Completed scope | Final Task, when present | Completion or lineage revision | Reusable evidence boundary |
|---|---|---|---|
| TG-M1.1-TG-M4.3 MVP | pre-Task-DB | `86fccf389e6e16c6ba2fdcaf5acd39d32c26b911` | Full MVP contract pass. |
| TG-M4.O1 and TG-M5.O1-TG-M5.O6 follow-ups | pre-Task-DB | `b7b41ad458f59f9ad2ae8dcb0a7c56493d24a6ab` | Project-scoped install-guidance closure. |
| TG-M6 | `tg_task_306c6ac4199122fb` | `f017ee228d435d892fb7136c5e79b3063320fac5` | Legacy v0.1.0/schema-v2 rehearsal source. |
| TG-M7 | `tg_task_f18ca3ea0982df9a` | `a9843337666c55a6866edf616b4b3d47af76426a` | Stored legacy completion revision. |
| TG-M8 | `tg_task_5950b5447de43993` | `0af9d1b57d648c801b021f5a6fff5779b04b0fb8` | Governance-hardening acceptance. |
| TG-M9 | `tg_task_ca49db07778db0e4` | `57583dba5dc1c04fe5f167602a4f89f1dd464639` | Paused-work visibility acceptance. |
| TG-M11 | `tg_task_b62ad41d367d5e01` | `b46a188f3b8df3d9702f99b0eb190f04923bdc1f` | Completion-integrity acceptance. |
| TG-M12.1, TG-M12.2, TG-M12.O1, and TG-M12.O2 | `tg_task_a23869622600a71c` | `143090ffb5e140804e27e350769c319e3be6a237` | Scope, handoff, Contract, Effort, and package-status acceptance; M12.3 excluded. |
| TG-M13 | `tg_task_2302ce786a28d1b0` | `a024c07dc9d587d62ecf3705a409ac62806ce11b` | SQLite/read-transaction and release hardening. |
| TG-M14 | `tg_task_c30e126d19b2a0e2` | `b1ce82f1aeffc226ba827231228727ee5a2b35c5` | Daily UX and local continuity. |
| TG-M15 through TG-M15.6 | `tg_task_1c4ab208be113c8a` | `1927b65fc2437ec799a6591ec1db9f7cb4373fe8` | Viewer reload/state acceptance. |
| TG-M16 | `tg_task_1a2f5af057e45ef1` | `e0b109d67074015b5494757fa64cf7524ebaa92d` | TG-M16.4 reduced-loop behavioral acceptance. |
| TG-M17 | `tg_task_c8054d1c57087956` | `29ac34d8c6e96c2cf091e504abe05b5485a54dd0` | Stable identity and relocation. |
| TG-M18 | `tg_task_fa3a57ae3089e3fc` | `b0df647d9caf693afc0ff46aecf71a2c4739c864` | Schema-v16/Viewer-v4 completion history baseline. |
| TG-M19.0 | `tg_task_2b95de205e3f92e3` | `1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1` | Release-correctness and cutover contract. |
| TG-M19.1 | `tg_task_ba59e260cc2c58a6` | `cbf75372617e90ca0b54746ae27f24a4e67cb292` | Active specification/design consolidation. |
| TG-M19.2 | `tg_task_20fd398141755a65` | `2af0382c54615640fbd8475a59f374b1b71804c4` | Roadmap/plan/history authority split. |
| TG-M19.3 | `tg_task_b71ac20177aae41a` | `4040e923cfdbd8b3f65d8883187a57578d64c092` | Apache-2.0 authority and attribution boundary. |
| TG-M19.4 | `tg_task_7cc967fc224440cb` | `639bc74adfd1f5e15996d1416bd064f1b9303edc` | Public guidance and review-trust synchronization. |
| TG-M19.5 | `tg_task_bd93525dc71f4dcd` | `fe9fdafd207cab9d0966785f4b340fe3224397fa` | Isolated legacy upgrade and paired rollback. |
| TG-M19.6A | `tg_task_2fc57c401dd2855d` | `5ce64e1eae239d78e185d68349784cfe0c069f00` | Release-counter Contract/privacy compatibility correction. |
| TG-M19.6B | `tg_task_cacf382b827c58d5` | `a9b80ce177a6dead10d51a070b76ff01f7af0294` | CI release-identity self-check correction. |
| TG-M19.6 | `tg_task_67a3f3e73b913bfb` | `a9b80ce177a6dead10d51a070b76ff01f7af0294` | Final exact-SHA candidate and deterministic accepted assets. |
| TG-M19.7 | `tg_task_5b8796de20a32d39` | `github-actions-run:VAiring/task-governance-tool:30561916953:1` | Exact candidate CI, Python 3.12/3.14 success. |
| TG-M19.8 | `tg_task_79791addafcf0e00` | `a9b80ce177a6dead10d51a070b76ff01f7af0294` | Normal fast-forward of remote `main`. |
| TG-M19.9 | `tg_task_418792bf98f211af` | `sha256:ed79ea10ff9e07dd44f86c6ef9e3979bd296c1fc731b06148d2f01f70ae763ac` | Commit not required; remote-main CI run `30565181070:1` accepted. |
| TG-M19.10 | `tg_task_9807bdc4ddc5ba37` | `github-release:VAiring/task-governance-tool:362617903` | Lightweight tag and GitHub prerelease publication. |
| TG-M19.11 | `tg_task_e452e6eb7dcf0e08` | `f5d7ed4706eac41c422690f16e5791893fdb1989` | Post-release active-authority and history reconciliation. |
| TG-M19.12 | `tg_task_d0e8ac1287bd07a4` | `f3f1945916f99e32b66c9bb15d3a673dbff61c5a` | Offline release-contract checker consolidation. |

Every M19 Task in the completion index above has a satisfied final Tier 2 gate
and no unresolved High or Medium finding in any recorded generation. Some
migrated Tasks intentionally report `legacy_history_incomplete=true`; the
completion index does not claim an exhaustive reconstruction of pre-v15
history.

Terminal units with no completion revision are not missing evidence:
TG-M10.1, TG-M15.7, and TG-M16.3 were cancelled and never became current
product scope.

## Published v0.10.0 Evidence Boundary

| Item | Accepted value |
|---|---|
| Commit, remote `main`, tag target | `a9b80ce177a6dead10d51a070b76ff01f7af0294` |
| Tag | unpeeled lightweight `v0.10.0` |
| GitHub Release | ID `362617903`, prerelease |
| Archive | `task-governance-tool-0.10.0.zip`, 238727 bytes |
| Archive SHA-256 | `99fc2345fd036091349c47f7379eee25b8b3b4c8873c0f74aaceac323bb82a03` |
| Checksum-file SHA-256 | `9cdc99bd26cc4887bd88ef2ec638659224f0a0d8f567edb12a3800d59a8b6764` |
| Release-notes SHA-256 | `aaa118a3fbbb261ec6a24f7a80f50f161e606a86857f99e17f957f34ba044a03` |
| License | Apache-2.0; holder Omoronine; no concrete NOTICE duty |
| Smoke checks | default branch, tag, and archive isolated installs passed |

This table is a concise acceptance index, not a replacement for current
product contracts or Task evidence. The canonical artifact/install record is
[`docs/release-install.md`](release-install.md); published Release-body bytes
remain [`docs/releases/v0.10.0.md`](releases/v0.10.0.md).

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

## Approved Post-Release Sequence

These four units are one sequential lane in lane-order sequence. The state
labels below describe this source revision; the public Task database remains
the operational record and receives completion evidence after each reviewed
commit.

### TG-M19.11 Post-Release Authority And Documentation Reconciliation

Task: `tg_task_e452e6eb7dcf0e08`
Lane/order: `TG-M19-RELEASE-CORRECTNESS` / 120
Review tier: Tier 2
Source-revision state: complete at
`f5d7ed4706eac41c422690f16e5791893fdb1989`

Intended outcome:

- Synchronize active authority and public guidance with the completed v0.10.0
  release while preserving durable product contracts.
- Move one-time M19 execution detail to append-only history and establish a
  clean baseline before later design work.

Write scope:

- active specification, design, roadmap, plan, public guidance, history index
  and new captures, plus directly coupled document/history tests;
- no runtime, schema, CLI, package behavior, existing history body, published
  Release body, Git remote, or other external-state change.

Verification:

- Confirm M19.0-M19.10 including M19.6A/6B against public completion evidence.
- Verify exact published commit/main/tag/Release, Apache-2.0 wording,
  all-generation unresolved High/Medium semantics, history immutability/links,
  and removal of volatile handoff mirroring.
- Run focused document/history tests, full offline suite, `git diff --check`,
  and two exact-target Tier 2 reviews.

### TG-M19.12 Release Contract Checker Consolidation

Task: `tg_task_d0e8ac1287bd07a4`
Lane/order: `TG-M19-RELEASE-CORRECTNESS` / 130
Review tier: Tier 2
Source-revision state: complete at
`f3f1945916f99e32b66c9bb15d3a673dbff61c5a`

Intended outcome:

- Centralize duplicated release, package, CLI, license, and documentation
  consistency checks in one offline read-only checker shared by tests and CI.
- Preserve installed-CLI, no-write, isolation, and release-acceptance coverage
  without changing public product behavior.

Write scope:

- deterministic checker, focused fixtures/tests, CI test wiring, and directly
  coupled documentation;
- no public command, runtime state, schema, network, installation, or target
  mutation behavior.

Verification:

- Derive parser leaves/runtime versions from owning code and package inventory
  from the release manifest; remove duplicated CI inventories and fragile
  source-regex matrices.
- Prove manifest missing/extra/hash mismatch, license mismatch, metadata
  invalidity, CLI/document drift, and generated-artifact detection.
- Run focused and complete offline coverage, diff check, and two exact-target
  Tier 2 reviews.

### TG-M19.13 Deterministic Test Lanes And CI Policy

Task: `tg_task_704ecd1d1e2f7552`
Lane/order: `TG-M19-RELEASE-CORRECTNESS` / 140
Review tier: Tier 2
Source-revision state: active

Intended outcome:

- Partition the existing offline suite into deterministic fast, integration,
  release, and all lanes.
- Route CI events and Python versions through an explicit policy while
  preserving the complete test set and release-candidate assurance.

Write scope:

- test-lane manifests or selectors, deterministic runner/policy, CI wiring,
  focused tests, and directly coupled documentation;
- no removal or weakening of an existing test and no product behavior,
  network, target-project, or generated-state change.

Verification:

- Map every discovered test to exactly one lane and prove `all` equals prior
  full discovery.
- Fast covers parser, validation, state, and small repositories; integration
  covers setup, recovery, relocation, backup, Viewer, and normal migration;
  release covers legacy migration, upgrade/rollback, performance, package,
  license, docs, and workflow acceptance.
- Verify event/Python matrix and full candidate gate; run all lanes, diff
  check, and two exact-target Tier 2 reviews.

### TG-M19.14 Release Vocabulary Boundary And Legacy Privacy Compatibility

Task: `tg_task_0f76a52915987511`
Lane/order: `TG-M19-RELEASE-CORRECTNESS` / 150
Review tier: Tier 2
Source-revision state: approved, pending TG-M19.13

Intended outcome:

- Contain the repository-specific v0.10.0 release-counter exception at the
  generic privacy boundary.
- Preserve legacy stored Contract/checkpoint/history reads while using neutral
  future release vocabulary that needs no new project-specific exception.

Write scope:

- privacy validation and legacy-read compatibility boundary, focused fixtures
  and tests, specification/design/reference synchronization, and directly
  coupled public guidance;
- no rewrite of stored legacy evidence, schema change, credential relaxation,
  or new project-specific exception.

Verification:

- Read the legacy M19.7 Contract, checkpoint, and completion history unchanged.
- Prove future release vocabulary needs no exception while credential,
  Authorization header, token assignment, and compound forms remain rejected.
- Run focused privacy and complete offline suites, diff check, and two
  exact-target Tier 2 reviews.

## Roadmap Completion Criteria

The approved post-release sequence is complete only when TG-M19.11 through
TG-M19.14 each has exact verification evidence, its required two independent
Tier 2 passes, no unresolved High or Medium finding in any generation, and
typed completion evidence. TG-M12.3 remains blocked until all of its separate
authority prerequisites exist; it is not implied by completion of this lane.
