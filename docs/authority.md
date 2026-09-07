# Repository Authority Index

<a id="document-authority-index"></a>

This index routes each Task to the smallest sufficient current authority set.
It is authority routing, not a product contract, execution ledger, or evidence
store. Live Task state and evidence remain in the project-local Task database
and are read only through the public CLI.

## Mandatory Start Set

- [AGENTS.md](../AGENTS.md)

This `docs/authority.md` file and the exact live Task Contract read through the
public CLI are also mandatory at every Task and execution-unit boundary. Use
their routes before loading any other governing document.

## Selective Current Authority

- [Product behavior](specification.md)
- [Implementation structure](design.md)
- [Current decisions, open issues, gateways, and non-delegated static contracts](../plan.md)

Use the Task Contract and trigger table below to locate complete relevant
owner sections, including conditions, exceptions, and directly required common
contracts. Read the coupled code, schemas, tests, configuration, examples, and
fixtures. These routes are a starting set, not a maximum: follow concrete
dependencies that reveal further impact under AGENTS.md's Reread Rule.

The specification and design retain their product and implementation roles.
Their Task operation, Viewer, Runner execution, and Runner Plan authoring sections
delegate only the corresponding detail below; product and implementation ownership remain distinct.

## Viewer Detail Authority

- [Viewer product behavior](viewer-specification.md)
- [Viewer implementation structure](viewer-design.md)

These are the current owners of the delegated Viewer detail. Shared contracts
remain in the specification and design and are linked directly from the detail.

## Runner Plan Authoring Detail Authority

- [Runner Plan authoring behavior](runner-plan-authoring-specification.md)
- [Runner Plan authoring structure](runner-plan-authoring-design.md)

These are the current owners of the delegated authoring/control detail. Shared
Plan values and target admission belong to the Runner execution owners below;
maintenance remains in the specification and design. Task Contract detail belongs
to the Task operation owners below. The authoring/control documents link directly
to each applicable owner.

## Task Operation Detail Authority

- [Task operation behavior](task-operation-specification.md)
- [Task operation structure](task-operation-design.md)

These are the current owners of Task selection/state, Contract, Checkpoint,
Handoff, Advisory, and instruction-layer detail. Shared CLI/output, privacy,
connection/transaction, review/completion, Evidence, and Runner contracts remain
with their existing owners and are linked directly from the detail.

## Runner Execution Detail Authority

- [Runner execution behavior](runner-execution-specification.md)
- [Runner execution structure](runner-execution-design.md)

These are the current owners of trusted-local eligibility, Plan values, target
materialization, process, cleanup, parent-service audit, and the closed Runner
module registry. Runner Plan authoring/control remains separate. Shared
review/completion, persistence and schema-v21/v22 gate protocol, privacy,
connection/transaction, maintenance, and global test contracts remain with their
existing owners and are linked directly from the detail.

## Conditional Initiative Roadmaps

- [Responsibility-based modularization](modularization-roadmap.md)

This is the approved planning direction for the named initiative, not current
product behavior or an authorization to start later execution units. Read it
for explicitly authorized initiative planning/taskization or work whose exact
Task Contract selects it. Existing third-wave contracts are unchanged.

## Delegated Repository Operating Guides

- [Artifact authoring](artifact-authoring.md)

This is an authoring guide delegated by root `AGENTS.md`, which remains the
durable repository operating-rule owner. It applies to new or substantially
revised material and is not an independent product or implementation authority.
The mandatory start set stays above; AGENTS.md owns the reading rules, with
their selective routing and escalation summarized here.

## Non-Authoritative History

- [Historical documentation index](history/README.md)

Do not load or search history in normal work. Use it only when the current Task
explicitly needs lineage, migration review, rationale recovery, or superseded
evidence discovery. History never fills a current authority gap or satisfies a
current gate.

## Trigger Routing

| Trigger | Required selective route |
|---|---|
| Supported product behavior, public CLI/JSON, persistence, privacy, setup, Viewer, or current gate | Exact section in `docs/specification.md`; Viewer detail in `docs/viewer-specification.md`; Runner Plan authoring detail in `docs/runner-plan-authoring-specification.md`; Task operation detail in `docs/task-operation-specification.md`; Runner execution detail in `docs/runner-execution-specification.md` |
| Module ownership, storage/process boundary, migration mechanics, or test architecture | Exact section in `docs/design.md`; Viewer detail in `docs/viewer-design.md`; Runner Plan authoring detail in `docs/runner-plan-authoring-design.md`; Task operation detail in `docs/task-operation-design.md`; Runner execution detail in `docs/runner-execution-design.md` |
| Current decision, open issue, cross-sequence gateway, or static contract | Exact section in `plan.md` |
| Authorized modularization planning or a Task selecting that initiative | `docs/modularization-roadmap.md`; existing product/design owners and exact Task Contract still apply |
| Published artifact, install, upgrade, tag, or Release identity | `docs/release-install.md` |
| Live status, blocker, target, evidence, review, or completion history | Public CLI and live Task Contract; no Git-document mirror |
| Historical lineage or retired evidence | `docs/history/README.md`, only after naming the exceptional reason |

Investigate a missing or ambiguous route through the relevant owners and
concrete dependencies. If required authority or a material conflict remains
unresolved, stop before the affected semantic write and record an open issue
or ask the user. A Task Contract copies existing explicit authority; it does
not create product scope or acceptance by itself.

## Full-Read Escalation

Expand from the selected sections when actual authority-layout changes,
cross-owner impact, conflicting owners, or incomplete routes require wider or
full reads of the affected governing documents. A schema, CLI, privacy, or
other category label alone does not force every owner document to be read in
full. The affected requirements and their conditions remain mandatory.

Normal investigation is sufficient once the governing rules, affected boundary,
and verification basis are identified and no concrete unresolved material
impact question remains. No exhaustive proof of absent dependencies is needed.
AGENTS.md's Reread Rule owns this policy; this index imposes no read-set limit
or additional approval per read.

## Machine-Readable Registry

The following closed JSON is the repository-visible semantic authority route.
JSON object-key order is presentation only; array order and membership are the
enforced meaning.

```json
{
  "schema": "taskgov-document-authority-v10",
  "mandatory_start": ["AGENTS.md", "docs/authority.md", "live_task_contract"],
  "current": ["docs/specification.md", "docs/design.md", "plan.md", "docs/viewer-specification.md", "docs/viewer-design.md", "docs/runner-plan-authoring-specification.md", "docs/runner-plan-authoring-design.md", "docs/task-operation-specification.md", "docs/task-operation-design.md", "docs/runner-execution-specification.md", "docs/runner-execution-design.md"],
  "mixed_execution": [],
  "conditional": ["docs/modularization-roadmap.md"],
  "history_index": "docs/history/README.md"
}
```
