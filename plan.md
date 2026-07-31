# task-governance-tool Current Decisions And Open Issues

Status: v0.10.0 is published from exact commit
`a9b80ce177a6dead10d51a070b76ff01f7af0294`. Remote `main` and the
lightweight tag `v0.10.0` resolve to that commit. GitHub Release
`362617903` has prerelease visibility and contains the accepted archive and
checksum. TG-M19.0 through TG-M19.10, including TG-M19.6A and TG-M19.6B, are
complete. TG-M19.11 through TG-M19.14 are also complete. TG-M20.1 through
TG-M20.5 form the approved operational-baseline sequence. TG-M20.1 is
complete at `a77afbe0140fef416cceeee529e9ff2c985a8e4d`, and TG-M20.2 is
active. TG-M12.3 remains separately blocked.

This file owns current decisions, explicit open issues, and user-decision
gates. It is not the product contract, execution ledger, or evidence store:

- [`docs/specification.md`](docs/specification.md) owns product behavior.
- [`docs/design.md`](docs/design.md) owns implementation structure.
- [`docs/implementation-roadmap.md`](docs/implementation-roadmap.md) owns
  approved execution units, order, blockers, verification, review gates, and
  the concise completion index.
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
  [`docs/release-install.md`](docs/release-install.md); the active roadmap
  owns completion evidence routing and post-release execution order.

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

### M20 Operational-Baseline Boundary

- TG-M20 observes exact product baseline
  `43c91d5987b0c35c66f834789aea782e98dcaff7`. The TG-M20.1 completion commit
  owns the observation contract but does not replace or modify that product
  baseline.
- Baseline collection precedes any Skill behavior change. M20 adds no product
  telemetry, public command, schema/Viewer field, normal-loop call,
  Verification Receipt, test-strategy engine, Task-splitting operation, or
  parent/child Task model.
- Evidence remains explicitly stratified as `machine_observed`,
  `historically_reconstructed`, or `observer_attested`. It is study material,
  not authority, and cannot satisfy verification, review, completion, release,
  or product-acceptance gates.
- TG-M21 Verification Receipts, a small Skill-only verification-
  proportionality guardrail, and bounded user-approved Task decomposition are
  three separate decisions. None is bundled into another, and no follow-up
  implementation Task is registered without current explicit user approval.
- One contaminated incident, an unreconstructable historical absence, timing
  or size alone, and the context-rich parent conversation cannot establish
  causation or justify a positive decision.

### M20 Frozen Decision Rules

TG-M20.5 records each feature decision as `proceed_to_design`,
`no_follow_up`, or `observe_more`.

An eligible M20.3 scenario bundle contains one eligible
`fresh_agent_trial`, one eligible `verification_proportionality`
`trial_measurement`, and its eligible state pair for the same scenario/trial.
An eligible non-control M20.4 pair contains the attestation and
`split_pressure` measurement records for both fixed arms in the same cohort
with the same `workload_digest`; it has no arm-level state pair because a
bounded arm may use multiple Tasks. The Handoff control alone additionally
requires its sole arm's state pair.

Two evidence channels materially support a predicate only when an
observer-attested code/classification and the exact machine-observed field
specified below both satisfy it for the same eligible bundle; mere channel
presence is insufficient. A critical unknown is a partial, excluded, missing,
capped, or `unknown` field that participates in the predicate and could change
whether its threshold is met. A conflict is a state/measurement mismatch for
the same revision, generation, count, or episode identity, or a disagreement
between paired cohort or workload-digest facts. A conflict or critical unknown
always produces `observe_more`.

A verification fact code is material only when it was needed to judge
verification sufficiency, reproduce the result, or resume safely; a
nice-to-have detail is excluded. The following rules are then mechanical.

- TG-M21 is `proceed_to_design` only when the same material code—one of
  `command_label`, `result`, `source_revision`, `duration`, or
  `scope_coverage`—appears in `verification_fact_codes` for at least two
  distinct eligible M20.3
  bundles, each corresponding after-state has
  `verification_detail="absent"`, and each attestation has
  `minimal_receipt_fit="yes"`. Manual re-entry is supporting evidence only
  when that code is also in `manual_reentry_fact_codes`.
- The Skill-only proportional-verification guardrail is
  `proceed_to_design` only when the same responsibility-pattern code appears
  in at least two distinct eligible M20.3 bundles, both have
  `instruction_fit="yes"`, and the machine mapping is satisfied in each:
  `duplicate_contract_assertion` requires
  `duplicate_contract_locations>=2`;
  `duplicate_inventory_owner` requires
  `inventory_owner_fanout>=2`; `fixture_copy` requires
  `fixture_copy_groups>=1`; `nondetecting_regression` requires
  `target_change_result="not_detected"`; and
  `unbounded_verification_escalation` requires
  `verification_escalation` to be `all_first` or `repeated_all`.
- One M20.4 pair qualifies for decomposition only when its broad and bounded
  records have identical ordered episode IDs, every bounded-arm episode has
  all four independence fields `yes`, and at least one exact improvement
  holds. For each arm define, over all of its ordered episodes,
  `contract_revision_delta_sum=sum(contract_revision_after-contract_revision_before)`
  and `review_cycle_sum=sum(review_cycles)`. The three improvements are:
  a same-ID broad episode has at least one independence field `no` and at
  least one position in its design-defined machine delta vector is greater
  than the same bounded episode's vector position;
  `bounded.contract_revision_delta_sum <= broad.contract_revision_delta_sum - 1`;
  or `bounded.review_cycle_sum <= broad.review_cycle_sum - 1`.
- The Handoff control is eligible support only when its attestation has cause
  `out_of_scope_control` and response `handoff`, its state pair has
  `before.task_status=after.task_status="in_progress"`,
  `after.handoffs_pending=before.handoffs_pending+1` with delivered and
  withdrawn counts unchanged and completion cycles unchanged, and its sole
  split episode has zero file, module, line, Contract-revision, and
  review-generation delta, `governance_cycles=1`, and `review_cycles=0`.
  Task decomposition is `proceed_to_design` only with two qualifying
  non-control pairs and that eligible machine-and-observer-supported Handoff
  control.

Adequate negative evidence is also fixed:

- TG-M21 and the Skill guardrail may be `no_follow_up` only when all three
  planned M20.3 bundles are eligible, no conflict exists, and the applicable
  positive predicate is satisfied in fewer than two bundles.
- For decomposition let `E` be the eligible count among the four planned
  non-control pairs, `Q` the qualifying count among those eligible pairs, and
  `U=4-E`. It may be `no_follow_up` only when the Handoff control is eligible,
  no conflict exists, `E>=3`, and `Q+U<2`. Thus with one unavailable pair,
  zero eligible pairs may qualify; one qualifying pair requires the fourth
  pair to be eligible before a negative decision.
- Any decision that meets neither its positive nor negative rule is
  `observe_more`. M20.5 must name the missing record/field or conflict,
  propose 1-3 new scenario IDs with one attempt each in a separately approved
  observation unit, and state the stop condition as the first satisfied
  positive/negative rule or exhaustion of that fixed inventory.

Size, line, case, and timing values never satisfy a predicate by themselves.
The current conversation and earlier reported incident are excluded samples,
not extra support. `proceed_to_design` authorizes only the smallest separately
reviewed design proposal; it does not authorize implementation, behavior
activation, or Task registration.

## Current Blocker And User Decision

| Unit | Task | Current gate |
|---|---|---|
| TG-M12.3 | `tg_task_1f7503aca5e32cdc` | Blocked until a separately approved versioned Issue Skill intake contract, governing permission update, and explicit integration approval exist. |

The approved TG-M20.1-TG-M20.5 sequence requires no additional product-scope
decision within its recorded boundaries. A positive M20.5 result still needs
explicit user approval before a follow-up design or implementation Task is
registered. A materially different write, external operation, scope expansion,
or changed acceptance also requires explicit authority.

## Open Issues And Deferred Candidates

These items are not implementation authority. Each needs a separately approved
contract and execution unit.

- TG-M20.5 must decide separately whether evidence supports detailed TG-M21
  Verification Receipt design, a small Skill-only proportional-verification
  guardrail, bounded Task-decomposition design, or further observation.
- Decide whether later product scope should add project-profile detection,
  dependency graphs, or Git integration beyond the current read-only snapshot,
  completion validation, and bounded Review Packet.
- Design the approved follow-up default-browser launch boundary described
  above.
- Reassess stale warnings and event-history or current/list pagination only
  after operational evidence shows the current bounded checkpoint and
  projections are insufficient. Checklist/child execution units remain
  inactive while M20 evaluates a narrower decomposition boundary.
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
