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
Their Task operation, Viewer, Runner execution, Runner Plan authoring, Evidence,
Review/completion, Setup/state operation, and database persistence/migration sections
delegate only the corresponding detail below; product and implementation ownership remain distinct.

## Viewer Detail Authority

- [Viewer product behavior](viewer-specification.md)
- [Viewer implementation structure](viewer-design.md)

These are the current owners of the delegated Viewer detail. Shared contracts
remain with their existing owners and are linked directly from the detail.

## Runner Plan Authoring Detail Authority

- [Runner Plan authoring behavior](runner-plan-authoring-specification.md)
- [Runner Plan authoring structure](runner-plan-authoring-design.md)

These are the current owners of the delegated authoring/control detail. Shared
Plan values and target admission belong to the Runner execution owners below;
maintenance belongs to the Setup/state operation owners below. Task Contract detail belongs
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

## Evidence Detail Authority

- [Evidence behavior](evidence-specification.md)
- [Evidence structure](evidence-design.md)

These are the current owners of provenance, authority snapshots, whole-field
criteria, References, Bundle/Finding snapshots, canonical Evidence JSON,
publication, and the test-only independent reader. Review and Verification
operations, completion gates, and ordinary Task state remain separate. Shared
schema-v21/v22 Runner protocol, persistence, privacy, connection/transaction,
maintenance, and global test contracts remain with their existing owners and
are linked directly from the detail.

## Review And Completion Detail Authority

- [Review and completion behavior](review-completion-specification.md)
- [Review and completion structure](review-completion-design.md)

These are the current owners of Review targets, receipts, Findings, Packets,
Verification Receipt operations and eligibility, completion evidence, cycles,
and reopen integration. Evidence formats/provenance and ordinary Task state,
Contracts, and Checkpoints remain separate. Shared CLI/output, persistence,
migration, schema-v21/v22 Runner protocol, privacy, connection/transaction,
maintenance, and global test contracts remain with their existing owners and
are linked directly from the detail.

## Setup And State Operation Detail Authority

- [Setup and state operation behavior](setup-state-specification.md)
- [Setup and state operation structure](setup-state-design.md)

These are the current owners of Doctor/effective-ignore preflight, recovery
candidate validity, fixed state resolution, identity/binding/relocation, and
setup/backup/maintenance detail. Shared CLI/output, package layout, persistence,
migration, privacy, connection/transaction, and global test contracts remain
with their existing owners. Stored Task validation, Evidence publication, and
Viewer publication remain separate coupled responsibilities and are linked
directly from the detail.

## Database Persistence And Migration Detail Authority

- [Database persistence and migration behavior](database-specification.md)
- [Database persistence and migration structure](database-design.md)

These are the current owners of supported schemas, physical structures,
migration, admission, reentry, and row preservation. The shared current Runner
protocol and operational read/write boundary remain in the product specification;
runtime ownership and connection/transaction rules remain in the implementation
design. Setup, recovery policy, domain repositories, and public completion
operations retain their separately routed owners.

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
| Supported product behavior, public CLI/JSON, persistence, privacy, setup, Viewer, or current gate | Exact section in `docs/specification.md`; Viewer detail in `docs/viewer-specification.md`; Runner Plan authoring detail in `docs/runner-plan-authoring-specification.md`; Task operation detail in `docs/task-operation-specification.md`; Runner execution detail in `docs/runner-execution-specification.md`; Evidence detail in `docs/evidence-specification.md`; Review/completion detail in `docs/review-completion-specification.md`; Setup/state operation detail in `docs/setup-state-specification.md`; database detail in `docs/database-specification.md` |
| Module ownership, storage/process boundary, migration mechanics, or test architecture | Exact section in `docs/design.md`; Viewer detail in `docs/viewer-design.md`; Runner Plan authoring detail in `docs/runner-plan-authoring-design.md`; Task operation detail in `docs/task-operation-design.md`; Runner execution detail in `docs/runner-execution-design.md`; Evidence detail in `docs/evidence-design.md`; Review/completion detail in `docs/review-completion-design.md`; Setup/state operation detail in `docs/setup-state-design.md`; database detail in `docs/database-design.md` |
| Current decision, open issue, cross-sequence gateway, or static contract | Exact section in `plan.md` |
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
  "schema": "taskgov-document-authority-v15",
  "mandatory_start": ["AGENTS.md", "docs/authority.md", "live_task_contract"],
  "current": ["docs/specification.md", "docs/design.md", "plan.md", "docs/viewer-specification.md", "docs/viewer-design.md", "docs/runner-plan-authoring-specification.md", "docs/runner-plan-authoring-design.md", "docs/task-operation-specification.md", "docs/task-operation-design.md", "docs/runner-execution-specification.md", "docs/runner-execution-design.md", "docs/evidence-specification.md", "docs/evidence-design.md", "docs/review-completion-specification.md", "docs/review-completion-design.md", "docs/setup-state-specification.md", "docs/setup-state-design.md", "docs/database-specification.md", "docs/database-design.md"],
  "mixed_execution": [],
  "conditional": [],
  "history_index": "docs/history/README.md"
}
```
