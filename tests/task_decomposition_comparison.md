# Completion-Benefit Guidance Comparison

This is the evaluation protocol for the completion-benefit guidance change,
not a Task-user worksheet, normal-loop step, or product output contract.
Inputs are the eight fictional cases in `task_decomposition_cases.json`.
The fixture-integrity test checks their structure, not natural-language rules
or expected Task counts. Semantic judgments belong to the evaluator.

## Fixed Comparison Conditions

Before either arm runs, preserve the baseline and candidate `SKILL.md`,
`references/task_workflow.md`, and `references/cli_contracts.md`, plus hashes
of those files, the cases, this protocol, and the identical transport prompt.
The baseline is the committed instruction version immediately before this
change. The candidate is the exact reviewed candidate, not a later revision.

Use fresh minimal-context agents with the same model (`gpt-6-astra`) and
`medium` reasoning effort. Each arm receives its instruction snapshot and
all eight cases in their fixed order. Neither receives the other arm, this
evaluation rubric, expected counts, desired reductions, or prior results.
Ask for ordinary registration/edit proposals and a short user-facing message,
not scores, estimated tokens, or a separate split-rationale record. The test
transport may identify each case and group its proposed operations; that
envelope is not a new public CLI format.

Preserve both complete responses, failures included. Do not rerun selectively
or repair a proposal before scoring it. A transport failure can be reported
as unconfirmed. Changes to instructions or input require a newly identified
comparison, not replacement of an inconvenient result.

The proposing agents do not implement, contact services, or mutate projects.
An evaluator replays their exact proposed public Task operations in disposable
local physical-install fixtures, using only existing test helpers and CLI.
Generated Task IDs replace fictional IDs mechanically; this is not permission
to change the proposal's decisions. Inspect actual stored Task/Contract/Tier
and ordering effects as well as the response. Preserve rejected operations as
results. For the mid-Task case, seed the stated current responsibility and
current-target evidence using existing public fixture flows before replay.
Do not install into or rerun the existing Run06/Run07 target projects.

## Evaluator-Only Judgments

For every case, inspect all explicit scope and acceptance requirements, not
just presence of their IDs. Check complete, non-duplicated ownership, permission
limits and conditional approvals, representable ordering, attributable checks,
correct intermediate state, and resumability. Registration must not imply
implementation or external-operation authority. A combined Task keeps the
checks and review floor of its entire scope; no omitted gate is a saving.

- C01: distinguish migration, mutation, reporting, tests, and documentation
  needed for one usable returns outcome from actual separate completion use.
  Preserve migration history, price/revision/response retention, return and
  stock rules, rollback, retry, and immutable old fixtures.
- C02: investigation, rendering, and verification are phases of the same
  requested screen outcome. Preserve all visual and numeric acceptance.
- C03: algorithmic complexity and byte-boundary cases do not themselves
  require separate completion or a higher Tier. Preserve the complete
  feed/finalize/reset and failure behavior.
- C04: preserve independent editorial acceptance and conditional production
  approval. Do not grant publishing permission or omit the planned publishing
  responsibility merely because its execution permission is still pending.
- C05: preserve independent delivery and acceptance of the two screens while
  one is held. Shared components and tests do not require combining them.
- C06: an accepted foundation can enable the already-authorized waiting
  consumer without standalone user value. Preserve this actual use and the
  preview order; do not register or absorb the existing import-adapter work.
- C07: retain the separate security and storage acceptance decisions and
  their change/check attribution even in one release with shared files.
  Aggregate test success cannot replace either owner's acceptance. Do not
  turn code, documentation, and tests into artificial independent outcomes.
- C08: keep the already-covered responsibility in the current Task. For a
  revision, raise its Tier before a semantic Contract change when required;
  old evidence cannot satisfy the revised Contract. A separately usable/held
  sheet may use its own ordered successor and floor. Do not move existing
  scope without repartition authority, duplicate it, or expand write paths.

Evaluate both excessive separation and excessive combination. Alternative
partitions are not failures solely because their counts differ; identify the
concrete decision/use, acceptance, or safety consequence. Do not invent a
minimum-count goal or a universal size boundary. Compare the same cases in
both arms, including unchanged and inconclusive results.

## Separate Observations And Limits

Report, separately:

- proposed new Task count and edits to existing Tasks;
- expected verification gate cycles and independent review passes implied by
  the resulting scopes and Tiers, not measured execution time;
- complete visible response length and user-facing explanation length;
- observed model usage if exposed by the execution tool, otherwise explicitly
  unavailable (do not substitute character-to-token estimates);
- scope, permission, ordering, Tier, evidence, and acceptance-attribution
  results from both the responses and public-CLI fixture projections.

This bounded, single-sample-per-arm comparison can support a local behavioral
observation, not statistical performance claims. It does not measure whole
development token savings, coding quality equivalence, or end-to-end runtime.
Keep evaluation artifacts outside public source commits and preserve them
locally with their input/version identifiers. They are test evidence, not
current authority or a new reusable workflow engine.
