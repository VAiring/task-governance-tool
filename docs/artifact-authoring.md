# Artifact Authoring Guide

This repository-operating guide provides the authoring detail delegated by
[AGENTS.md](../AGENTS.md#documentation-maintenance). AGENTS.md remains the
durable operating-rule owner. Product behavior belongs in
[specification.md](specification.md), implementation structure in
[design.md](design.md), and current decisions in [plan.md](../plan.md).
The [authority index](authority.md#document-authority-index) routes those owners.

Apply this guidance when creating or substantially revising documentation,
source modules, and their related tests. A narrow edit does not require a
rewrite of the containing file. Existing material does not need to meet the
size suggestions below for an otherwise accepted Task to finish. This guide
does not authorize repository-wide splitting, renaming, or a decomposition
roadmap. Reading and escalation continue to follow the current
[Source Of Truth](../AGENTS.md#source-of-truth) and
[Reread Rule](../AGENTS.md#reread-rule).

## Short Entry Points And One Home Per Rule

Keep entry documents useful for orientation: purpose, ownership, applicability,
and direct links to the relevant detail. Put each rule in one authoritative
home and link to it from summaries, examples, and consumer guidance. A summary
should help a reader select the owner without becoming a competing contract.

When material duplicates a rule, prefer replacing that duplication with a link
or relocating the detail to its existing owner. Follow the
[documentation-maintenance policy](../AGENTS.md#documentation-maintenance) for
retirement and history; splitting a document is not permission to retire its
authority or weaken an unfinished Task's contract.

## Complete Responsibility-Based Sections

Organize detail around the responsibility a reader needs to understand or
change. Keep the applicable conditions, exceptions, and outcomes together.
Do not separate a rule from its exception just to shorten a section. A section
should make its scope and dependencies clear enough to interpret its content
with the linked owners.

Use descriptive headings and direct links with meaningful labels. Link to a
specific section when only that responsibility is relevant; retain stable
anchors when reorganizing material or update the affected callers in the same
change. Avoid chains of index pages that hide the actual owner. These are
authoring choices, not limits on the number of links or documents a reader may
need.

## Current Rules And Development Lineage

When authoring or revising current specifications, designs, or shipped Skill
guidance, describe behavior by its function and responsibility. Do not use a
development Task number or milestone as a substitute for a feature name,
applicability condition, or current rule. The text and its directly linked
current owners must explain the applicable behavior, conditions, exceptions,
permissions, and gates without requiring a past Task or development-history
lookup.

Keep completed Tasks' work allocation and one-time change instructions separate
from durable product and implementation rules. Apply the existing
[documentation-maintenance policy](../AGENTS.md#documentation-maintenance) when
authorized cleanup removes or relocates that execution prose: retain every
still-applicable rule in its current owner, preserve unfinished Tasks' authority
and anchors, and follow the existing history and authority-transition rules.
Completion alone does not authorize a document or history mutation.

This is not a blanket ban on numbers or identifiers. Fixed contract values,
schema and migration versions, actual file names, and compatibility anchors
may need their exact spelling; explain their present meaning where used rather
than renaming them solely for this guidance. Approved unfinished execution
plans may identify their Tasks, and non-authoritative history and immutable
published records may preserve development lineage. Neither supplies missing
current rules.

Within the existing review of the changed material, check whether a reader who
does not know the development Tasks can identify the current rule and its
exceptions, and whether completed execution instructions have been mistaken
for current obligations. Apply this check to the authorized write scope; it
adds no review stage, approval step, normal-loop command, or unrelated retrofit.

## Code Boundaries And Related Tests

Group code by its reason to change and the responsibility it owns. A useful
boundary makes the inputs, outputs, side effects, and dependencies understandable
from the interface, nearby explanation, and directly coupled tests. Add a short
explanation where those facts would otherwise be unclear; do not require a
standard annotation on every function or audit existing interfaces wholesale.

Keep transaction and cleanup ownership identifiable. When an authorized change
separates code, preserve who begins, commits, or rolls back a transaction, who
owns acquired resources, and who performs cleanup on the relevant failure paths.
Keeping strongly coupled work together can be clearer than splitting it. This
is a design principle for the changed responsibility, not an exhaustive new
transaction or cleanup proof obligation.

For newly organized material, name modules and functions for what they do, and
make the corresponding tests discoverable by responsibility. A descriptive
module name, matching test name, or direct cross-reference can show the
relationship without requiring one test file per source file. For example,
`tools/document_contract.py` and `tests/test_document_contract.py` make the
document-checking relationship apparent. Test behavior and relevant failure
cases according to the existing Task gates, rather than mirroring private code
structure.

## Soft Size Guidance

Use these approximate ranges to help decide whether a responsibility is easy
to find and understand:

| Material | Authoring heuristic |
|---|---|
| Entry documents | Around 100-250 lines often leaves room for orientation and direct routes. |
| Detailed documents or ordinary source modules | Around 300-800 lines can be a useful working range when the responsibility is coherent. |
| Material approaching or exceeding 1,000 lines | Consider whether it contains responsibilities with different reasons to change. |

A responsibility review can conclude that keeping the material together is
clearer. These ranges are neither minimums to pad toward nor maximums to enforce,
and do not establish a universal GPT reading limit. Correct boundaries and
complete conditions matter more than a line count; long cohesive material and
short complete material are both valid.

Size alone creates no Task, CI failure, blocker, required split, or new review
gate. This guide adds no hard bounds on lines, files, functions, links, imports,
or read-set size, and no fresh-agent benchmark. Use the current Task's accepted
scope, verification, and review requirements.

## Finding The Owner For A Proposed Change

From the live Task Contract and the authority index, identify the existing
owner of the behavior or structure being changed. Use headings, anchors, and
search to locate its complete relevant sections, then read their conditions,
exceptions, and required common contracts with the coupled implementation and
tests. A search hit helps locate a responsibility; it does not replace reading
that responsibility. This works within the current large, unsplit files and
does not depend on a future physical decomposition.

Follow concrete dependencies when the material reveals additional impact, as
required by the [Reread Rule](../AGENTS.md#reread-rule). For example, a helper
call may lead to a shared validation rule and its consumer tests; a schema
relationship may lead to a migration or compatibility owner. The initial links
do not cap that reading. Conversely, a schema or CLI label alone does not make
every document relevant. Update the owning rule and affected consumers together
when the authorized change requires it.

For example, a change to repository document routing is explained in
`docs/authority.md` and checked by `tools/document_contract.py`, with its
focused fixtures in `tests/test_document_contract.py`. A product-behavior change
instead starts with its specification owner and corresponding design, code,
and tests. Continue investigating a real missing route or material conflict;
if it remains unresolved, follow AGENTS.md before changing the affected rule.
When the governing rules, affected boundary, and verification basis are known
and no concrete unresolved material impact question remains, normal discovery
is sufficient. There is no obligation to prove the absence of every possible
dependency elsewhere in the repository.

These examples aid discovery; they add no recurring Task-loop exercise, reading
log, counter, score, time budget, approval per read, or read/file/link-depth cap.
The substantive product, permission, privacy, verification, and review rules
are unchanged.
