# Responsibility-Based Modularization Roadmap

<a id="modularization-roadmap"></a>

## Purpose And Authority

The user approved this initiative's overall direction and its persistence on
2026-09-08. This document owns the finite destination, exclusions, and broad
sequence for repository code and documentation decomposition. It is a
conditional planning owner routed by the [authority index](authority.md), not
a replacement for current product behavior or implementation contracts.

The [specification](specification.md), [design](design.md), their delegated
detail owners, and exact live Task Contracts remain applicable. Existing
third-wave contracts are unchanged; this roadmap adds no acceptance or review
gate to them. Live status and evidence belong only in the public Task CLI.
The earlier assessment and third-wave work motivate this destination; their
progress and completion evidence are not mirrored here.

Approval to record this direction does not register Tasks, authorize later
implementation, or transfer another session's work. Future taskization and
execution use the existing [planning approval boundary](../AGENTS.md#execution-plan-before-substantial-implementation)
and [M25 guidance](../plan.md#m25-select-split-merge-register). These broad
stages are not an approved execution-unit set or new lane prerequisites.

## Target Architecture And Code Scope

Keep one installable package and one SQLite database. Separate responsibilities
within that product, without introducing services, extra databases, a generic
repository framework, or a general workflow engine. Small execution slices
serve the fixed destination below; finding another movable helper does not
automatically extend the initiative.

The main code priority is the separation of common DB infrastructure from
business-specific persistence and stored-data validation. Organize the
following five responsibility groups; names and file counts are design choices,
not acceptance quotas.

| Responsibility | Intended boundary |
|---|---|
| Common DB foundations | Existing shared types, errors, connection configuration, version observation, and migration coordination. Keep domain-specific records with their domain rather than collecting them into a new giant common module. |
| Operational state | Binding, backup metadata, and Viewer/Evidence publication metadata, distinct from filesystem work and setup orchestration. |
| Review, Verification Receipt, and completion history | Cohesive persistence and stored-data validation for these records, retaining their shared completion relationships. |
| Evidence | Authority/criteria, References, Bundle validation and persistence, and projection snapshot capture, grouped by responsibility and consistent read scope. |
| Runner records | Stored execution-graph validation and generation/terminal persistence, separate from process execution policy. |

The destination for `storage.py` is a readable common entry and coordination
boundary, not an empty file. Moving all its contents into one differently named
utility module would not achieve the intended separation. Long cohesive
migration sections may remain; reorganizing every old migration is not required.

The next code focus is the remaining entry-point mixing in CLI, Task, and
Setup: command reception and dispatch, assembly of read projections, state
changes, and orchestration of setup cases. Choose concrete seams after the
DB responsibilities settle, within these named areas rather than expanding
to unrelated subsystems. Keep the outer owner of a complete operation clear.

Preserve the useful existing separation of Evidence construction/publication,
Viewer rendering/configuration/maintenance, and Runner process, Windows, lease,
and lifecycle work. Within the Runner service, current-basis reading/selection
versus launch orchestration is a bounded later candidate, not a demand to
redesign Runner execution. Further splitting of backup recovery internals is
not a mandatory finish condition for this initiative.

Tests follow the responsibilities actually changed. Split mixed large test
groups or extract fixtures only where that makes the changed behavior easier
to locate. Synchronize directly affected imports, patch targets, package
inventory, document routes, and existing test-lane/Runner registries as needed.
Do not rename every milestone-named test or build a universal fixture framework.

## Documentation Destination

Keep `specification.md` and `design.md` as distinct whole-product entry points
with shared rules and direct routes. In addition to the existing Viewer and
Runner Plan authoring detail owners, organize detail around these six areas:

| Responsibility | Detail to place together |
|---|---|
| Task, Contract, and ordinary Skill operation | State, selection, Contract, and stored Task/Contract relationships; M25, Checkpoint, local Handoff, and Advisory can remain separate sections in this area. |
| Review, Verification, and completion | Targets, Review Packets, receipts, completion eligibility, cycles, and reopen; refer to Evidence for exchange formats. |
| Evidence | Authority snapshots, criteria, References, provenance, Bundle/index formats, JSON projection, and the independent reader. |
| DB persistence and migration | Supported schemas, physical structures, migration, reentry, and preservation of existing rows; no document per schema generation. |
| Setup and state operation | Identity, binding, relocation, recovery candidates, backup, and maintenance. |
| Runner execution | Plan values, target admission, materialization, process execution, cleanup, service coordination, stored graph, and module registry; refer to the existing authoring/control owners. |

These are responsibility destinations, not a fixed number of files. Keep
behavioral specification distinct from implementation design, with conditions
and exceptions intact. Shared CLI/output, privacy, connection/transaction, and
cross-feature selection rules keep one authoritative home. In particular, a
shared current Runner/completion protocol must not be mistaken for merely an
old migration detail or copied into several feature documents.

`AGENTS.md`, `authority.md`, `plan.md`, and `README.md` are not additional
physical-splitting targets; update their necessary routes without duplicating
detail. Portable Skill package references keep their existing ownership.
Apply the [artifact authoring guide](artifact-authoring.md), not a hard file
size or reading-limit policy.

## Broad Sequence And Dependencies

The third-wave scope remains as approved. After that work, use the following
broad sequence when preparing bounded execution units:

| Stage | Intended outcome and expected areas |
|---|---|
| Establish destinations and simpler boundaries | Route the document responsibilities and clarify common DB foundations; begin with relatively independent operational-state repositories. Main areas: current document owners, storage, and affected operational consumers/tests. |
| Separate business persistence | Work through Review/Receipt, Evidence, completion-history, and Runner-record responsibilities according to their actual dependencies. Main areas: storage, the corresponding repositories/validators, formal owners, and focused tests. |
| Finish the named entry-point boundaries | Address remaining CLI/Task/Setup entry mixing and associated test organization; consider the bounded Runner service seam above. Do not start a new subsystem-wide redesign. |
| Integrate and close | Check the resulting responsibility map and existing integration/CI expectations, then retire this roadmap into history as described below. Finish the initiative without automatically proposing another wave. |

These stages express dependency direction, not a global serialization gate.
Independent document or code slices may proceed in parallel when they do not
conflict in shared owners. Place DB/migration and Setup detail after their code
ownership is sufficiently settled to avoid repeated large routing changes.
Actual sequential/optional classification, lanes, exact write scope, and
verification/review gates belong to the later approved execution-unit set and
its Task Contracts, not to inferred dependencies from this table.

## Coupling To Preserve And Work Outside Scope

Retain the existing operation owners and all-or-nothing behavior for:

- completion Bundle/cycle, Task state, event, and source-generation updates;
- Runner terminal observation, Evidence links, and cleanup recording;
- migration configuration changes, commit/rollback, and configuration restore;
- same-snapshot reads and their connection/transaction ownership; and
- resource acquisition, publication, and failure-path cleanup.

Module separation does not divide these transactions. Keep Git observation,
Runner execution, and file publication outside the short SQLite writer as
required by the current design. Retain externally consumed Evidence JSON,
independent Evidence validation, and supported setup/relocation/recovery.

This initiative does not include Linux/macOS implementation, complete OS
abstraction, feature retirement, compatibility removal, schema/JSON behavior
changes, changed approval or privacy rules, universal filesystem primitives,
or a wholesale test rewrite. Naming similarity alone is not a reason to merge
helpers with different identity or resource-lifetime requirements. Concrete
behavior changes or materially stronger acceptance require the existing user
decision process; they are not silently absorbed into decomposition.

## Verification And Finish

Each later execution unit uses proportionate existing verification and its
declared review tier under [AGENTS.md](../AGENTS.md#review-standard). Document
moves synchronize routes, anchors, directly coupled fixtures/checkers, and
required history handling. Code moves preserve directly affected behavior and
tests rather than creating a new comprehensive proof framework.

At integration, use the existing document/package/inventory checks and relevant
integration tests, followed by the applicable existing CI policy. This roadmap
does not authorize a push or CI dispatch, and does not add full-suite or
wall-clock qualification to every small slice. Existing release qualification
remains separate and unchanged.

The decomposition work is ready for closeout when, for the named scope:

- the governing rules, implementation owner, and corresponding tests for each
  major responsibility are straightforward to locate;
- unrelated business persistence/validation is no longer indiscriminately
  mixed into the common DB foundation;
- transaction, snapshot, publication, and cleanup owners remain identifiable;
- moved responsibilities have an actual consumer and no unnecessary duplicate
  implementation; and
- the applicable existing verification and review requirements are met.

These are the overall destination, not retroactive gates on third-wave Tasks.
No zero-large-file, zero-circular-import, every-function-moved, all-OS, or
exhaustive dependency-absence proof is required. Further decomposition after
this endpoint is a separate decision prompted by a concrete maintenance need,
not an automatic next wave.

## Roadmap Retirement And Subsequent Development

Retirement of this dedicated file is part of the initiative's final closeout,
not an optional future cleanup. Include that bounded retirement step when
preparing the final execution units. Keep the file active while dependent work
needs it, under the existing
[documentation-maintenance rules](../AGENTS.md#documentation-maintenance).

Once the dependent Tasks are done or explicitly superseded/abandoned:

1. Keep the adopted, durable behavior, architecture, and operating rules in
   their appropriate current specification, design, or operating-guide owners.
   Retain unresolved decisions and gateways in their appropriate current
   owners, including `plan.md` where applicable; do not move a rule needed for
   ongoing development exclusively into history.
2. Preserve this complete final roadmap as one immutable, explicitly
   non-authoritative capture under `docs/history/` and index it in
   `docs/history/README.md`.
3. In the same reviewed transition, remove the physical active
   `docs/modularization-roadmap.md`, its conditional authority route, and its
   active `plan.md` entry. Synchronize the associated checker/fixture
   registrations and affected links. Leave no live Task dependent on a removed
   anchor. Add and index the history destination before switching active routes.

After retirement, normal development follows the adopted rules in the current
owners; this roadmap is no longer required reading or a second authority.
Its historical copy remains available only for retrospective reference.
This instruction records the closeout scope; it neither performs the move now
nor authorizes Task registration or implementation. The concrete retirement
transition remains part of the explicitly authorized closeout work under
AGENTS.md, rather than a side effect of Task completion.
