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

Read only the exact owner and section selected by the Task Contract and the
trigger table below. Directly coupled code, schemas, tests, configuration,
examples, and fixtures remain part of that selected read.

The specification remains the product-behavior owner and the design remains
the implementation-structure owner. Execution-detail routing below does not
transfer either role.

## Mixed Current And Conditional Execution Authority

- [TG-M22 Evidence Ledger sequence](execution-contracts/tg-m22-evidence-ledger.md#tg-m22-sequence)
- [TG-M23 Derived Evidence sequence](execution-contracts/tg-m23-derived-evidence.md#tg-m23-derived-evidence)
  - [delegated TG-M23 process safety](execution-contracts/tg-m23-process-safety.md#tg-m23-process-safety)
- [TG-M24 trusted-local Runner repair and acceptance sequence](execution-contracts/tg-m24-verification-runner.md#tg-m24-verification-runner)

TG-M22.1A, TG-M22.2, TG-M21.5, TG-M22.3, TG-M22.4, TG-M23.1, bounded
offline/mock TG-M23.2, and offline/mock TG-M23.3 integrated Analyzer acceptance
are accepted predecessors. TG-M24.1 design, TG-M24.1A, TG-M24.R1,
TG-M24.R2A, TG-M24.R2B, TG-M24.R2C, TG-M24.R4A, and TG-M24.R4V are accepted predecessors
whose retired adversarial qualification details supply no current gate.
Accepted R4A left its inventory-approved retired repository material and
dedicated tests physically absent, without an archive or dormant copy.
TG-M24.1B is superseded and non-gating. TG-M24.R3A, TG-M24.R3B, TG-M24.R4B,
TG-M24.R5, TG-M24.2A, TG-M24.2B, TG-M24.2C, and TG-M24.2D are accepted
predecessors; TG-M24.3A, TG-M24.3B, TG-M24.3C, TG-M24.4A, TG-M24.4B,
TG-M24.4C, and TG-M24.4D are accepted predecessors, and TG-M24.CP4 is the sole
current unit in the exact sequential order owned by the routed contract. The
separate TG-M24.R2 bootstrap checkpoint supported accepted R1 but activates no
product behavior.
No TG-M23 execution unit is current. TG-M24 Runner completion-gate authority
belongs only to accepted TG-M24.3C; accepted TG-M24.4A/TG-M24.4B/TG-M24.4C
and accepted TG-M24.4D own acceptance only. Current TG-M24.CP4 owns only the
final no-debt checkpoint and M25 handoff and changes no product byte;
network/live Analyzer acceptance remains outside the accepted scope. The
derived-evidence contract is the sole unit owner/router; its process-safety
route delegates one non-overlapping physical-safety seam and owns no unit state
or core semantics.

## Documentation Governance Sequence

- [TG-DOC sequence](../plan.md#tg-doc-sequence)

TG-DOC.2 is an accepted post-M23/pre-M24 documentation predecessor. TG-DOC.3
preserves the inactive post-M24 normalization scope after TG-M24.CP4. Neither
route changes product behavior. M24.1 design, M24.1A, R1, R2A, R2B, R2C,
R4A, R4V, R3A, R3B, R4B, R5, 2A, 2B, 2C, 2D, 3A, 3B, 3C, 4A, 4B, 4C, and 4D
are accepted predecessors, M24.1B is superseded, and CP4 is current. CP4
remains formally current through its Task acceptance until the separate
TG-DOC.3 activation synchronization; indexing or reading them activates no
product behavior.

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
| Current decision, open issue, cross-sequence gateway, or static contract not delegated below | Exact section in `plan.md` |
| TG-M22 unit purpose, scope, order, dependency, permission, or execution/acceptance gate | Stable `docs/execution-contracts/tg-m22-evidence-ledger.md#tg-m22-sequence` route and its exact accepted-predecessor sections; product behavior and implementation structure remain owned above |
| TG-M23 unit or core data detail | Exact accepted-predecessor, current activation, or inactive unit in `docs/execution-contracts/tg-m23-derived-evidence.md` and its ASCII anchor above |
| TG-M23 Windows process, private temporary tree, or atomic publication/recovery detail | Exact delegated route in `docs/execution-contracts/tg-m23-process-safety.md`, only through the TG-M23 core owner/router |
| Documentation governance or TG-DOC unit detail | [TG-DOC sequence](../plan.md#tg-doc-sequence), then the exact `plan.md#tg-doc-2` or `plan.md#tg-doc-3` unit anchor |
| TG-M24 unit detail | Exact accepted predecessor, current unit, inactive unit, or superseded unit in the routed current execution contract and ASCII anchor above |
| Published artifact, install, upgrade, tag, or Release identity | `docs/release-install.md` |
| Live status, blocker, target, evidence, review, or completion history | Public CLI and live Task Contract; no Git-document mirror |
| Historical lineage or retired evidence | `docs/history/README.md`, only after naming the exceptional reason |

If a route is missing or ambiguous, stop before a semantic write and record an
open issue or ask the user. A Task Contract copies existing explicit authority;
it does not create product scope or acceptance by itself.

## Full-Read Escalation

Read all of `docs/specification.md`, `docs/design.md`, `plan.md`, and every
affected conditional contract when the Task changes the authority layout;
cross-cuts behavior, schema, CLI, privacy, permissions, or completion gates;
finds conflicting owners; or cannot identify a complete exact route. This is
an explicit escalation, not the normal start path.

## Machine-Readable Registry

The following closed JSON is the repository-visible semantic authority route.
Within a sequence, a unit named in none of the current, inactive, or superseded
arrays is an accepted predecessor. A superseded unit is non-gating and supplies
no current authority. JSON object-key order is presentation only; array order,
owner, route, unit, and membership are the enforced meaning.

```json
{
  "schema": "taskgov-document-authority-v5",
  "mandatory_start": ["AGENTS.md", "docs/authority.md", "live_task_contract"],
  "current": ["docs/specification.md", "docs/design.md", "plan.md"],
  "mixed_execution": [
    {
      "path": "docs/execution-contracts/tg-m22-evidence-ledger.md",
      "route_anchor": "tg-m22-sequence",
      "current_units": [],
      "inactive_units": []
    },
    {
      "path": "docs/execution-contracts/tg-m23-derived-evidence.md",
      "route_anchor": "tg-m23-derived-evidence",
      "current_units": [],
      "inactive_units": [],
      "detail_routes": [
        {
          "path": "docs/execution-contracts/tg-m23-process-safety.md",
          "route_anchor": "tg-m23-process-safety",
          "parent_anchor": "tg-m23-1",
          "owner_scope": "windows_process_private_temp_atomic_publication"
        }
      ]
    },
    {
      "path": "docs/execution-contracts/tg-m24-verification-runner.md",
      "route_anchor": "tg-m24-verification-runner",
      "current_units": ["TG-M24.CP4"],
      "inactive_units": [],
      "superseded_units": ["TG-M24.1B"]
    }
  ],
  "documentation_sequence": {
    "path": "plan.md",
    "route_anchor": "tg-doc-sequence",
    "current_units": [],
    "inactive_units": ["TG-DOC.3"]
  },
  "conditional": [],
  "history_index": "docs/history/README.md"
}
```
