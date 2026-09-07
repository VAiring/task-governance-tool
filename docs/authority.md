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

The specification remains the product-behavior owner and the design remains
the implementation-structure owner. No other route transfers either role.

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
| Supported product behavior, public CLI/JSON, persistence, privacy, setup, Viewer, or current gate | Exact section in `docs/specification.md` |
| Module ownership, storage/process boundary, migration mechanics, or test architecture | Exact section in `docs/design.md` |
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
  "schema": "taskgov-document-authority-v6",
  "mandatory_start": ["AGENTS.md", "docs/authority.md", "live_task_contract"],
  "current": ["docs/specification.md", "docs/design.md", "plan.md"],
  "mixed_execution": [],
  "conditional": [],
  "history_index": "docs/history/README.md"
}
```
