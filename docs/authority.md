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

TG-M22.1A, TG-M22.2, TG-M21.5, TG-M22.3, TG-M22.4, TG-M23.1, bounded
offline/mock TG-M23.2, and offline/mock TG-M23.3 integrated Analyzer acceptance
are accepted predecessors. No TG-M23 execution unit is current; network/live
Analyzer acceptance remains outside the accepted scope. The
derived-evidence contract is the sole unit owner/router; its process-safety
route delegates one non-overlapping physical-safety seam and owns no unit state
or core semantics.

## Conditional Formal Authority

- [TG-M24 Verification Runner](execution-contracts/tg-m24-verification-runner.md)

This sequence is accepted but inactive. Load only the exact document and
explicit ASCII anchor named by the current Task Contract or a directly coupled
cross-cutting decision. Indexing or reading one does not activate behavior.

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
| TG-M24 unit detail | Exact inactive unit in the routed execution contract and ASCII anchor above |
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

The following JSON is the repository-visible TG-DOC.1 routing and size
baseline. Line and byte ceilings are blocking maintenance gates, not permission
to omit a current invariant or weaken a contract.
Within a fixed sequence, a unit named in neither registry array is an accepted
predecessor.

```json
{
  "schema": "taskgov-document-authority-v3",
  "baseline": {
    "commit": "695b240178681a072b5cbd73845dff8e31a281d6",
    "mandatory_files": 4,
    "lines": 7404,
    "bytes": 428058
  },
  "budgets": {
    "AGENTS.md": {"lines": 500, "bytes": 30000},
    "docs/authority.md": {"lines": 160, "bytes": 16000},
    "docs/specification.md": {"lines": 2500, "bytes": 150000},
    "docs/design.md": {"lines": 2450, "bytes": 135000},
    "plan.md": {"lines": 375, "bytes": 35000},
    "docs/execution-contracts/tg-m22-evidence-ledger.md": {"lines": 1750, "bytes": 125000},
    "docs/execution-contracts/tg-m23-derived-evidence.md": {"lines": 250, "bytes": 30000},
    "docs/execution-contracts/tg-m23-process-safety.md": {"lines": 120, "bytes": 20000},
    "docs/execution-contracts/tg-m24-verification-runner.md": {"lines": 280, "bytes": 32000}
  },
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
    }
  ],
  "conditional": [
    "docs/execution-contracts/tg-m24-verification-runner.md"
  ],
  "history_index": "docs/history/README.md"
}
```
