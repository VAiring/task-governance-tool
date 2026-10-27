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

The detail pairs below are the current delegated owners of their named
responsibilities; specification and design roles remain distinct. Shared and
coupled contracts stay with their existing owners and are linked directly from
the applicable detail. The trigger table selects a root responsibility; these
links locate its detail. The machine registry validates the same authority graph.

## Viewer Detail Authority

- [Viewer product behavior](viewer-specification.md)
- [Viewer implementation structure](viewer-design.md)

## Conditional Incremental Viewer Design

The approved [incremental Viewer contract](viewer-specification.md#conditional-incremental-projection)
and [three-unit execution boundary](viewer-design.md#incremental-execution-boundaries)
are conditional sections within those owners. They do not activate the new
capture/publication behavior before the integration gate.

## Runner Plan Authoring Detail Authority

- [Runner Plan authoring behavior](runner-plan-authoring-specification.md)
- [Runner Plan authoring structure](runner-plan-authoring-design.md)

Authoring/control is separate from Runner execution's Plan values and target
admission, Setup/state maintenance, and Task operation's Contract detail.

## Task Operation Detail Authority

- [Task operation behavior](task-operation-specification.md)
- [Task operation structure](task-operation-design.md)

Task selection/state, Contract, Checkpoint, Handoff, Advisory, and instruction layer.

## Runner Execution Detail Authority

- [Runner execution behavior](runner-execution-specification.md)
- [Runner execution structure](runner-execution-design.md)

Trusted-local eligibility, Plan values, target
materialization, process, cleanup, parent-service audit, and the closed Runner
module registry. Runner Plan authoring/control remains separate.

## Evidence Detail Authority

- [Evidence behavior](evidence-specification.md)
- [Evidence structure](evidence-design.md)

Provenance, authority snapshots, whole-field
criteria, References, Bundle/Finding snapshots, canonical Evidence JSON,
publication, and the test-only independent reader. Review and Verification
operations, completion gates, and ordinary Task state remain separate.

## Review And Completion Detail Authority

- [Review and completion behavior](review-completion-specification.md)
- [Review and completion structure](review-completion-design.md)

Review targets, receipts, Findings, Packets,
Verification Receipt operations and eligibility, completion evidence, cycles,
and reopen integration. Evidence formats/provenance and ordinary Task state,
Contracts, and Checkpoints remain separate.

## Setup And State Operation Detail Authority

- [Setup and state operation behavior](setup-state-specification.md)
- [Setup and state operation structure](setup-state-design.md)

Doctor/effective-ignore preflight, recovery
candidate validity, fixed state resolution, identity/binding/relocation, and
setup/backup/maintenance. Stored Task validation, Evidence publication, and
Viewer publication remain separate coupled responsibilities.

## Database Persistence And Migration Detail Authority

- [Database persistence and migration behavior](database-specification.md)
- [Database persistence and migration structure](database-design.md)

Supported schemas, physical structures,
migration, admission, reentry, and row preservation. The shared current Runner
protocol and operational read/write boundary remain in the product specification;
runtime ownership and connection/transaction rules remain in the implementation
design. Setup, recovery policy, domain repositories, and public completion
operations retain their separately routed owners.

## Conditional State Separation Plan

[Project state separation](state-layout-separation-plan.md) owns the approved
three-unit execution boundaries and the proposed design pending isolated
validation and adoption. It does not activate a production layout change;
current Setup/state and other product owners remain controlling until the
reviewed implementation switch.

## Verification Declaration Correction

[Explicit verification declaration correction](verification-declaration-plan.md)
owns the approved single-unit repair boundary and its verification/review gates.
Task operation, Review/completion, database and shared Runner owners continue
to own the durable behavior and implementation contracts.

## Conditional Session Ownership And Usage Design

[Session ownership and usage evidence](session-usage-plan.md) owns the six
approved execution-unit boundaries and their conditional design. It does not
activate ownership, numerical collection, a schema migration or host hooks.
Current product owners remain controlling until the corresponding reviewed
implementation updates them. Unverified host prerequisites are explicit there.

## Conditional Response Usage Attribution Design

[Response usage attribution](usage-response-attribution-design.md) owns the
accepted response boundaries and feature-level design for per-execution usage,
partial observations, successful-operation correlation, and bounded sharing.
It is conditional on the corresponding implementation, verification, review,
and synchronization with the current owners. Database persistence, Task state,
Setup, and completion Evidence retain their existing ownership. This route
does not activate a runtime, schema, host setting, or completion-gate change.

## Conditional Review Finalization Plan

[Review finalization and same-parent result notification](review-finalization-plan.md)
owns the approved four sequential phases within the single integration Task.
It does not activate automatic registration, Git writes, completion or result
delivery before the corresponding reviewed implementation and host gates.
Review/completion and the directly coupled current owners retain durable
product and implementation authority.

## Conditional Review Continuation Plan

[Review continuation](review-continuation-plan.md) owns the single existing
Task's three sequential phases and conditional design for same-basis judgment
reuse, explicit reassessment, retained history and safe wait continuation.
Review/completion, Evidence, database and setup owners retain current authority.
Saving this design does not activate a schema, operation, worker or host effect.

## Conditional Review Answer Collection Design

[Review answer collection](review-answer-collection-design.md) owns the
conditional design for simplified reviewer JSON answers, bounded hook
collection, and retirement of reviewer answer save/submit commands only.
Material retrieval, verification commands, parent registration and current
completion gates retain their existing owners. This route does not activate
the proposed answer format, hook, schema, host setting or command retirement;
those require the corresponding reviewed implementation and host gates.

## Conditional Operation Admission Correction

[Operation-scoped admission correction](operation-admission-plan.md) owns the
three approved sequential units for ordinary Task operations, review services,
and maintenance eligibility. It does not activate an unimplemented boundary;
the current database, Task, Review/completion and Setup/state owners remain
controlling until each unit synchronizes its implementation and contracts.

## Conditional Logger Design

[Local debug and performance logging](logger-design.md) owns the conditional
design for the enabled-only common diagnostic foundation: protected arguments
and exceptions, method/transaction correlation, and daily log files. Product
behavior, privacy and read-only rules remain with the specification and design
owners linked there. Its adopted connection/entry inventory defines current
instrumentation; further subsystem instrumentation needs its own Task scope.
This route does not enable logging or change runtime configuration.

## Conditional Asynchronous Backup Plan

[Asynchronous routine backup](async-backup-plan.md) owns the approved three
sequential stages within the existing replacement Task: worker ownership,
short database capture/publication, and ordinary-command integration. Setup/state
and database owners retain the durable contracts. This plan does not activate
an unreviewed runtime or change Setup/migration/recovery protection.

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
| Supported product behavior, public CLI/JSON, persistence, privacy, setup, Viewer, or current gate | Exact section in `docs/specification.md` or its applicable delegated behavior owner above |
| Module ownership, storage/process boundary, migration mechanics, or test architecture | Exact section in `docs/design.md` or its applicable delegated structure owner above |
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
  "schema": "taskgov-document-authority-v27",
  "mandatory_start": ["AGENTS.md", "docs/authority.md", "live_task_contract"],
  "current": ["docs/specification.md", "docs/design.md", "plan.md", "docs/viewer-specification.md", "docs/viewer-design.md", "docs/runner-plan-authoring-specification.md", "docs/runner-plan-authoring-design.md", "docs/task-operation-specification.md", "docs/task-operation-design.md", "docs/runner-execution-specification.md", "docs/runner-execution-design.md", "docs/evidence-specification.md", "docs/evidence-design.md", "docs/review-completion-specification.md", "docs/review-completion-design.md", "docs/setup-state-specification.md", "docs/setup-state-design.md", "docs/database-specification.md", "docs/database-design.md"],
  "mixed_execution": [],
  "conditional": ["docs/state-layout-separation-plan.md", "docs/verification-declaration-plan.md", "docs/session-usage-plan.md", "docs/review-finalization-plan.md", "docs/usage-response-attribution-design.md", "docs/review-continuation-plan.md", "docs/review-answer-collection-design.md", "docs/operation-admission-plan.md", "docs/logger-design.md", "docs/async-backup-plan.md"],
  "history_index": "docs/history/README.md"
}
```
