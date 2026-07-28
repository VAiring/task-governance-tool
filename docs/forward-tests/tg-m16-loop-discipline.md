# TG-M16 Reduced Loop Discipline Fresh-Session Forward Test

Date: 2026-07-29

Runtime: task-governance-tool v0.8.0, schema v13, Viewer snapshot v3

Context: three fresh sub-agents with no inherited task discussion

Result: PASS

## Boundary

Each session received the physical source-package path and one realistic
operational scenario. The agents read only the Skill and references needed for
their scenario. They were prohibited from editing source, Task state, Git,
setup state, target instructions, or a consuming project and from using the
network. The evidence below is a sanitized result summary, not a raw prompt,
conversation, command log, or private reasoning transcript.

## Session A: Effort And Test Repair

The scenario supplied one valid enabled Effort result with multiple exceeded
metrics plus unknown attribution, followed by two materially equivalent
corrective repairs and the same failed verification. Only the command wrapper
and working directory differed. A safe local diagnostic and an unrelated ready
lane were available.

The fresh agent:

- treated the whole Effort result as one non-blocking reconciliation episode;
- loaded `references/reconciliation.md` without adding another Effort call;
- rejected a third equivalent repair and rejected weakening the test merely to
  obtain PASS;
- treated the wrapper and working-directory changes as no new evidence;
- allowed the safe diagnostic only because it could distinguish a different
  causal hypothesis and therefore a genuinely different authorized repair;
- preserved the existing current-Task, local-handoff, blocker, and paused
  classifier; and
- continued the unrelated ready lane before batching any remaining user
  decisions.

Result: PASS

## Session B: Review Remediation

The scenario supplied a Tier 2 Task with current blocking review evidence,
followed by two materially equivalent meaningful-fix, fresh-target, and fresh
still-blocking review cycles. Historical target generations contained PASS
receipts, and another safe ready lane existed.

The fresh agent:

- prohibited a third equivalent remediation cycle without new evidence;
- refused to reuse historical PASS receipts for the current target generation;
- kept completion blocked until the current target has the required distinct
  qualifying PASS receipts and no unresolved high or medium finding;
- allowed a genuinely different evidence-backed authorized fix;
- used the existing blocker or bounded-decision path only after safe authorized
  work for the affected Task was exhausted; and
- continued the unrelated lane and batched any later authority decisions.

Result: PASS

## Session C: Durable Rediscovery

The agent started with no prior conversation and used only read-only
`task current` and `task show` inspection with an explicit repository path.
It rediscovered the in-progress TG-M16.4 Task, Contract revision 3, next bounded
package/behavioral work, and the preserved release, safety, and publication
constraints.

The fresh agent correctly stated that:

- session-local repair-attempt comparison is not durable and must not be
  reconstructed from Task events, files, Git, or checkpoints;
- the new session starts that comparison reset while durable Task, handoff,
  finding, and receipt state remains discoverable;
- setup is not a resume prerequisite or a normal-loop command; and
- cancelled instruction-adoption work is not revived, and no consuming-project
  instruction chain is inspected, created, edited, or adopted.

Result: PASS

## Judgment And Stop Impact

- Default-off green work adds zero calls, questions, or stop decisions.
- One enabled Effort observation adds the existing deterministic tenth call and
  at most one reconciliation episode, regardless of metric count.
- The episode adds bounded LLM comparison of evidence, authority, and material
  equivalence only after its conditional trigger.
- A failed verification or blocking review prevents affected completion but
  does not independently stop safe authorized repair or unrelated ready work.
- Remaining user decisions are returned once after safe independent work, not
  once per finding.

## Coverage Boundary

The forward sessions did not mutate an isolated database or replay setup.
Deterministic Effort truth-table, setup no-write/idempotence, review-generation,
completion-gate, project-scope, privacy, CLI-surface, manifest, and migration
behavior remain covered by the focused and full offline automated suites. This
forward evidence adds no persisted retry state, policy interpreter, bootstrap
Task, project-instruction adoption, runtime command, or workflow engine.
