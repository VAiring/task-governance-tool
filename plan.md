# task-governance-tool Current Decisions And Open Issues

Status: the implemented product baseline is v0.10.0, SQLite schema v16, and
Viewer snapshot v4 accepting source schemas v5-v16. TG-M18.4 fixed that
baseline at `b0df647d9caf693afc0ff46aecf71a2c4739c864`. TG-M19.0 fixed the
release-correctness contract, and TG-M19.1 completed the active
specification/design consolidation at
`cbf75372617e90ca0b54746ae27f24a4e67cb292`; TG-M19.2 through TG-M19.5 are
complete. The first TG-M19.6 local candidate acceptance was invalidated before
any remote write by the TG-M19.6A Contract-privacy compatibility correction.
After that narrow correction, TG-M19.6 must accept a new exact commit and
TG-M19.7 must receive fresh exact-value approval. TG-M12.3 remains separately
blocked.

This file is limited to current decisions that help route future work, explicit
open issues, and user-decision gates. It is not the product specification,
implementation design, execution ledger, or evidence store:

- [`docs/specification.md`](docs/specification.md) owns supported product
  behavior.
- [`docs/design.md`](docs/design.md) owns implementation structure.
- [`docs/implementation-roadmap.md`](docs/implementation-roadmap.md) owns
  execution-unit status, exact Task IDs, dependencies, blockers, verification
  gates, approvals, and the concise completion index.
- The project-local Task database owns operational Task, handoff, review, and
  completion state.
- [`docs/history/README.md`](docs/history/README.md) is the sole index for
  non-authoritative lineage. Historical text never fills a current-contract
  gap or revives a removed command, path, or workflow.

## Current Decisions

### Product And Authority Boundary

- The product remains a reusable, local-first Codex Skill plus deterministic
  Python CLI. A governed project's own instructions, requirements, design,
  tests, and decision log remain authority over that project.
- The Skill name is `task-governance-tool`; the CLI is `taskgov`. Exact
  commands, arguments, output envelopes, limits, and errors are defined only
  in the active specification and package references.
- The supported stateful install is one physical project-scoped copy at
  `<target-project>/.agents/skills/task-governance-tool`. User-wide, symlink,
  and junction stateful layouts remain unsupported. This repository retains
  only the bounded development self-host exception defined by the active
  specification and design.
- SQLite is a generated local helper store, not authority over project
  decisions. Generated database, backups, Viewer output, sidecars, caches, and
  runtime state remain outside commits and release artifacts.
- Read-only inspection remains the default. Target-project mutation, Git
  mutation, network use, Issue delivery, and external publication each require
  the exact current authority defined for that operation.

### Daily Task Workflow

- `setup` remains the sole public initializer, migrator, maintenance opt-in,
  relocation-confirmation boundary, and canonical Viewer repair flow.
  `doctor` remains the sole read-only diagnostic and is not a normal-loop
  prerequisite.
- Task Skill remains responsible for purpose, scope, acceptance, current
  state, next work, blockers, local handoff, review/completion evidence, and
  acceptance-driven completion. It does not become an Issue tracker, semantic
  triage system, project-specific test strategy, or general workflow engine.
- Sequential ordering is lane-local; a blocked lane does not stop unrelated
  ready work. `paused` remains an intentional hold distinct from `blocked`.
- A Task Contract is optional and copies already-explicit authority. Revision
  zero preserves the simple flow; semantic expansion requires later explicit
  authority and invalidates only the current completion/review projection as
  specified.
- Out-of-scope discoveries use one local handoff operation regardless of
  adapter availability. A pending handoff never expands current acceptance or
  blocks an otherwise complete Task.
- Effort Advisory and reduced-loop reconciliation are deterministic,
  non-authoritative guidance. They add no normal-path question, persisted
  retry counter, automatic Task mutation, or unrelated-lane stop. Tests are
  never weakened merely to obtain a pass.

### Review, Completion, State, And Viewer

- Current-target/current-generation stored receipts and findings satisfy only
  the configured deterministic review gate. Distinct reviewer keys prove
  distinct stored strings, not identity, independence, or authenticated
  provenance; the trusted caller remains responsible for truthful recording.
- Completion requires typed evidence. Reopen preserves append-only completion
  cycle history but never permits historical evidence to satisfy a new
  completion. Current Task fields remain the current projection.
- Durable project identity is separate from mutable filesystem binding. A path
  mismatch is not move/copy/fork intent; only the explicit setup confirmation
  flow may advance a binding.
- The Viewer remains a generated, self-contained, offline projection. Optional
  same-file reload and bounded one-shot UI-state handoff are presentation-only;
  they add no Skill trigger, background service, network action, or automatic
  browser launch.
- Direct launch of the generated Viewer in the operating system's configured
  default browser is approved only as a follow-up requirement. Its command or
  option, regeneration behavior, error contract, verification gates, and
  execution unit remain undecided.

### Release Direction

- The current committed development lineage remains authority until the exact
  M19 main fast-forward gate succeeds. A branch name, remote-tracking ref, Git
  identity, or repository ownership observation is not publication or
  licensing authority.
- Apache-2.0 is selected in principle but is not applied until TG-M19.3
  receives the exact user licensing-authority statement and the tracked and
  shipped material passes the required audit.
- The exact v0.10.0 release identity, artifact recipe, evidence objects,
  ordered M19 gates, invalidation rules, and publication state machine live in
  the active specification and roadmap and are intentionally not duplicated
  here.
- Local acceptance, candidate-branch CI, main fast-forward, remote-main
  acceptance, tag creation, and GitHub Release publication remain separate
  gates. TG-M19.7, TG-M19.8, and TG-M19.10 each require their own fresh
  exact-value user approval; general roadmap, Task, push, or license approval
  cannot substitute.

## Current Blockers And User Decisions

The roadmap and Task database hold the exact current wording. This table is a
discovery summary, not a second Contract.

| Unit | Task | Current gate |
|---|---|---|
| TG-M19.6A | `tg_task_2fc57c401dd2855d` | Current narrow privacy-compatibility correction; complete exact verification and two Tier 2 reviews before reopening TG-M19.6. |
| TG-M19.6 | `tg_task_67a3f3e73b913bfb` | Reaccept a new exact commit and assets after TG-M19.6A; the prior local acceptance remains historical only. |
| TG-M19.7 | `tg_task_5b8796de20a32d39` | Depends on the newly accepted local candidate and a separate fresh exact-value push/dispatch approval. |
| TG-M19.8 | `tg_task_79791addafcf0e00` | Depends on exact candidate CI and a separate exact-value direct fast-forward approval. |
| TG-M19.10 | `tg_task_9807bdc4ddc5ba37` | Depends on remote-main acceptance and a separate exact-value tag/Release publication approval. |
| TG-M12.3 | `tg_task_1f7503aca5e32cdc` | Blocked until a separately approved versioned Issue Skill intake contract, governing permission update, and integration approval exist. |

No other user decision is required before completing TG-M19.6A and reaccepting
TG-M19.6. The exact completion history and remaining dependency-gated M19
units are indexed in the active implementation roadmap rather than repeated
here.

## Open Issues And Deferred Candidates

These items are not implementation authority. Each needs a separately approved
contract and execution unit before work begins.

- Decide whether later product scope should add project-profile detection,
  verification-run recording, dependency graphs, or Git integration beyond
  the current read-only snapshot, completion validation, and bounded Review
  Packet.
- Design the approved follow-up default-browser launch boundary described
  above.
- Reassess stale warnings, event-history or current/list pagination, and
  checklist/child execution units only after operational evidence shows the
  current bounded checkpoint and projections are insufficient.
- Revisit a once-daily GitHub update check only as a separately approved,
  opt-in local-cache/network feature. Normal Skill use remains offline and
  must not contact GitHub.
- Before TG-M12.3, define the Issue Skill's exact versioned local intake and
  transport contract. Keep semantic duplicate/recurrence handling, handoff
  paging/retention, multiple receivers, Issue import/sync/priority/triage,
  resulting-Task creation, advanced risk/fixture analysis, signed evidence,
  and child-task structure outside the current Task Skill core.

## Pending Local Handoffs

The project-local database currently reports the following nine records as
`pending_handoff`. This list preserves discovery and identifiers; the database
remains the lifecycle record. A subject already addressed by later work is not
automatically withdrawn, reopened, or converted into current scope by this
document change.

| Handoff | Candidate |
|---|---|
| `tg_handoff_4001907257f93856` | Design explicit project relocation and stable identity recovery. |
| `tg_handoff_d5e1081385c3c568` | Evaluate symlink and junction Skill installation support. |
| `tg_handoff_696a19cba075d56e` | Preserve completed Git and review evidence as reopen history. |
| `tg_handoff_f62d99cb033e95ee` | Reassess duplicate-versus-unsafe changed-path error precedence before adding a new Git path input source. |
| `tg_handoff_c87a159f6583349d` | Consider the bounded post-release M14.6 Viewer-maintenance cleanup without weakening safety checks. |
| `tg_handoff_3952e6681a58a101` | Reassess restore temporary identity binding and cleanup residue only with operational evidence. |
| `tg_handoff_d85090045f2addb1` | Reassess a Git-tracked source self-host Viewer refresh profile under an explicit release-policy choice. |
| `tg_handoff_9d181d809abb34d4` | Reconcile active roadmap and plan status headers with completed M19 licensing work. |
| `tg_handoff_0134aaa7530a4983` | Correct active M19.4 roadmap review-finding generation wording. |

## Reference Material

- `references/KuraKoma_TASK_STATUS.md` is a copied example from another
  project. It is non-authoritative and must not be used as this project's
  current status or implementation order.
- Historical planning narratives, milestone execution diaries, and superseded
  forward-test evidence are discoverable only through
  [`docs/history/README.md`](docs/history/README.md).
