# task-governance-tool Current Implementation Roadmap

Status: v0.10.0 is published with prerelease visibility from exact commit
`a9b80ce177a6dead10d51a070b76ff01f7af0294`. Remote `main` and the
lightweight tag `v0.10.0` resolve to that commit, and GitHub Release
`362617903` contains the accepted archive and checksum. TG-M19.0 through
TG-M19.14, including TG-M19.6A and TG-M19.6B, are complete. TG-M12.3 remains
independently blocked. The approved operational-baseline sequence is
TG-M20.1 through TG-M20.5. TG-M20.1 is complete at
`a77afbe0140fef416cceeee529e9ff2c985a8e4d`, TG-M20.2 is complete at
`e49e5aca68a7bf1c9829afb50d2c6a38835a4f03`, TG-M20.3 is complete at
`800ed153dc9671f011ea4715f50d92ea464bc12b`, and TG-M20.4 is the active
source-revision unit.

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
| TG-M19.13 | `tg_task_704ecd1d1e2f7552` | `27e7ef08c70c1434b9aac8474b3006dbbc6ec3b8` | Deterministic test lanes and CI event/version policy. |
| TG-M19.14 | `tg_task_0f76a52915987511` | `43c91d5987b0c35c66f834789aea782e98dcaff7` | Legacy release-vocabulary compatibility and neutral future-operation boundary. |
| TG-M20.1 | `tg_task_43fd4b96c9ca92a1` | `a77afbe0140fef416cceeee529e9ff2c985a8e4d` | Frozen operational-baseline authority, inventory, privacy boundary, and decision rules. |
| TG-M20.2 | `tg_task_2885725486bec173` | `e49e5aca68a7bf1c9829afb50d2c6a38835a4f03` | Offline repository-only observation harness and sanitized retrospective baseline. |
| TG-M20.3 | `tg_task_8efb270f74360308` | `800ed153dc9671f011ea4715f50d92ea464bc12b` | Fresh-agent verification-proportionality baseline and terminal no-rerun receipt. |

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

## Approved Operational Baseline Sequence

These five units are one sequential lane in lane-order sequence. The
project-local Task database remains the live operational record. The detailed
TG-M19.11 through TG-M19.14 execution narrative removed from this active
roadmap is preserved by the
[post-release history capture](history/v0.10.0/post-release/implementation-roadmap.md).

The product baseline under observation is exact commit
`43c91d5987b0c35c66f834789aea782e98dcaff7`. TG-M20.1 completion revision
`a77afbe0140fef416cceeee529e9ff2c985a8e4d` owns the observation contract;
it does not replace or modify that product baseline. TG-M20 is a one-time
repository-development study, not taskgov telemetry or a product subsystem.
Its evidence is non-authoritative and cannot satisfy a Task verification,
review, completion, release, or product-acceptance gate.

The repository-only schema and collection boundaries are owned by
[the implementation design](design.md#tg-m20-repository-only-observation-design).
[Root plan.md](../plan.md#m20-frozen-decision-rules) owns the frozen
go/no-go rules. No unit activates Verification Receipts, test-strategy
ownership, runtime Task splitting, parent/child Tasks, a new Skill trigger, or
a public command.

### TG-M20.1 Operational Baseline Contract And Authority Activation

Task: `tg_task_43fd4b96c9ca92a1`
Lane/order: `TG-M20-OPERATIONAL-BASELINE` / 10
Review tier: Tier 2
Source-revision state: completed at
`a77afbe0140fef416cceeee529e9ff2c985a8e4d`

Intended outcome:

- Close TG-M19.14 with its exact Task and completion commit.
- Freeze all five M20 units, the bounded observation schema, privacy and
  missing-data semantics, evidence routing, fresh-agent neutrality, and
  decision rules before collecting data.

Write scope:

- active roadmap, design, plan, append-only history routing, one exact
  historical capture, and directly coupled document-history tests;
- no harness, runtime, package, Skill, CLI, SQLite, Viewer, release artifact,
  network, or target-project behavior change.

Verification:

- Confirm TG-M19.14 Task `tg_task_0f76a52915987511` completed at
  `43c91d5987b0c35c66f834789aea782e98dcaff7`.
- Verify the five Task IDs, lane order, review tiers, historical capture,
  schema bounds, privacy exclusions, unchanged product surfaces, focused
  document/release checks, diff check, and two exact-target Tier 2 reviews.

### TG-M20.2 Repository-Only Observation Harness And Retrospective Baseline

Task: `tg_task_2885725486bec173`
Lane/order: `TG-M20-OPERATIONAL-BASELINE` / 20
Review tier: Tier 2
Depends on: completed TG-M20.1
Source-revision state: complete at
`e49e5aca68a7bf1c9829afb50d2c6a38835a4f03`

Intended outcome:

- Implement an offline deterministic repository-only harness for fixed,
  isolated scenarios and the M20 schema.
- Separate prospective machine measurements from mechanically reconstructable
  M19 quantities and explicitly unavailable history.

Write scope:

- root repository tools, focused tests, isolated fixtures, and ignored
  reduced observation artifacts defined by TG-M20.1;
- no installable-package, public-runtime, product-state instrumentation,
  arbitrary command runner, or real consuming-project mutation.

Verification:

- Prove canonical serialization, bounds, privacy, timeouts, no shell, no
  network, no product or canonical-state write, deterministic replay, and
  readback through existing public boundaries. Validate every record and each
  complete fixed-unit corpus against the frozen byte caps.
- Freeze and review the request/control construction protocol, reducer-manifest
  schema, digest preimages, and deletion lifecycle. Exact request, neutral
  clarification, and selector bytes are never persisted as a shared control
  master.
- Bind every fresh control bundle to its exact frozen unit/scenario/arm/trial.
  Pin the separate M20.4 episode-plan supplement to the immutable M20.2
  protocol, authority, and baseline without rewriting collection provenance.
- Validate the sanitized M19 reconstruction against Task DB and Git, run
  applicable test lanes and diff check, and obtain two exact-target Tier 2
  reviews.

### TG-M20.3 Verification Proportionality Fresh-Agent Baseline

Task: `tg_task_8efb270f74360308`
Lane/order: `TG-M20-OPERATIONAL-BASELINE` / 30
Review tier: Tier 1
Depends on: completed TG-M20.2
Source-revision state: complete at
`800ed153dc9671f011ea4715f50d92ea464bc12b`

Intended outcome:

- Use the unchanged Skill in fresh, isolated trials to measure distinct risk
  coverage, responsibility-level redundancy, owner fanout, target-change
  sensitivity, fixture/checker reuse, verification escalation, maintenance
  fanout, and supporting size/time deltas.
- Produce enough stratified evidence to decide whether a later small
  Skill-only guardrail or Verification Receipt design is justified.

Mandatory constraints:

- Fresh subjects receive no parent conversation, temporary M20 memo,
  hypothesis, rubric, expected verdict, suspected failure, or preferred
  solution. The context-rich parent designs and scores but is not a subject.
- Fewer tests are never rewarded mechanically. Raw prompts, chats, reasoning,
  reviews, diffs, paths, and stream bodies are not retained.

Verification:

- Run the frozen scenario set against the exact baseline with fresh agents,
  validate source identity and bounded records, independently assess
  classifications/privacy, and confirm no Skill, product, canonical Task DB,
  or live-project mutation.
- Immediately before launch, independently review each exact ephemeral control
  bundle and its digest; destroy its bytes at the design-defined boundary and
  retain only the digest in reduced evidence.
- Obtain the required current-target Tier 1 review with no blocking finding.

### TG-M20.4 Task Boundary And Split-Pressure Fresh-Agent Baseline

Task: `tg_task_787f976a5e9daa7e`
Lane/order: `TG-M20-OPERATIONAL-BASELINE` / 40
Review tier: Tier 1
Depends on: completed TG-M20.3
Source-revision state: active

Intended outcome:

- Compare paired broad versus pre-authorized bounded Tasks and a Handoff
  control for multi-outcome intake, in-scope discovery, user expansion, and
  mid-task implementation/verification/review pressure.
- Measure whether candidate work remains independently acceptable,
  verifiable, committable, and completable, together with Contract, review,
  footprint, and cycle effects.

Mandatory constraints:

- This conversation and the earlier reported incident are design context only,
  never scored fresh-agent evidence.
- The bounded arm is authorized at intake; no runtime splitting, automatic
  Task creation, parent/child schema, unauthorized acceptance revision, or
  live canonical-state mutation is introduced.
- An out-of-scope Handoff control must remain a Handoff and is not counted as
  successful or failed Task splitting.

Verification:

- Execute the frozen paired and control cases with fresh agents, validate
  bounded episode evidence, the Handoff-control state pair, and privacy;
  independently review the classifications and obtain the required
  current-target Tier 1 review.
- Bind every M20.4 terminal receipt and later aggregate to the exact pinned
  episode-plan digest, and require reduced measurement and attestation episode
  IDs to match that plan.
- Immediately before launch, independently review each exact ephemeral control
  bundle and its digest; paired arms share the same workload digest, and no raw
  control bytes survive the design-defined boundary.

### TG-M20.5 Operational Baseline Synthesis And Follow-Up Decisions

Task: `tg_task_f6c19be1c10ad3ab`
Lane/order: `TG-M20-OPERATIONAL-BASELINE` / 50
Review tier: Tier 2
Depends on: completed TG-M20.4

Intended outcome:

- Reproduce bounded aggregates while keeping machine-observed,
  historically-reconstructed, and observer-attested evidence stratified.
- Decide separately whether to proceed to design for TG-M21 Verification
  Receipts, a small Skill-only verification-proportionality guardrail, bounded
  user-approved Task decomposition, or a bounded further-observation unit.

Write scope:

- sanitized M20 evidence, aggregate analysis, a non-authoritative history
  capture, and only the durable decisions that survive in active roadmap/plan;
- retirement of the root-only M20 collector and reconstruction tools, their
  dedicated tests, and the M20 study protocol fixtures after synthesis, while
  retaining tracked terminal collection receipts as no-rerun tombstones;
- no chosen solution implementation and no follow-up implementation Task
  registration without current explicit user approval.

Verification:

- Validate provenance, denominators, exclusions, unknowns, aggregate
  reproducibility, privacy, scenario limitations, active-document consistency,
  applicable focused checks, diff check, and two exact-target Tier 2 reviews.
- Confirm each trial's raw material and ephemeral control bundle were removed
  immediately after its single reduction attempt regardless of validation
  result and that no shared control master exists. Then delete the ignored
  `dist/M20_TEMPORARY_CONTEXT.md` and reduced corpus, retire the temporary M20
  study scaffolding named above, and mark the retained terminal receipts with
  the corpus retirement state after reviewed decisions have been routed.

## Roadmap Completion Criteria

The approved M20 sequence is complete only when TG-M20.1 through TG-M20.5 each
has exact verification evidence, its declared current-target review gate, no
unresolved High or Medium finding in any generation, and typed completion
evidence. Study observations never replace those gates.

A positive M20.5 result authorizes only the smallest separately approved design
proposal named by the frozen decision rules. It does not activate or register
an implementation Task. TG-M12.3 remains blocked until all of its separate
authority prerequisites exist; it is not implied by completion of M20.
