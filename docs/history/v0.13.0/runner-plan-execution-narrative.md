> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves completed Runner Plan execution narrative, not current
> product behavior, implementation authority, live Task state, or evidence.
> Internal words such as current, approved, accepted, active, or implemented
> describe only the captured revision. This file cannot fill a current
> authority gap or satisfy a current gate.

# Runner Plan Authoring Execution Narrative Capture

- Source path: `plan.md`
- Source commit: `0a4ad29ce3f45d2057dacc0ecb5dd6ba95b314b3`
- Capture unit: `TG-RMAP.3`
- Current replacements:
  [specification](../../specification.md#current-runner-plan-authoring-and-control-contract),
  [design](../../design.md#current-runner-plan-authoring-and-control-design),
  [AGENTS.md](../../../AGENTS.md#target-project-safety),
  [README.md](../../../README.md#explicit-runner-plan-authoring), and
  [plan](../../../plan.md#runner-plan-authoring).
  Use the public CLI for live Task state and evidence.
- Capture purpose: preserve the complete pre-compaction TG-RPA.1 through
  TG-RPA.6 ordering, responsibility allocation, activation, accepted behavior
  summary, and one-time permission narrative from the committed source.

The exact source section begins below and ends before the next plan anchor.

````markdown
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
````
