> [!CAUTION]
> **NON-AUTHORITATIVE STUDY HISTORY — DO NOT USE AS CURRENT VERIFICATION
> EVIDENCE OR PRODUCT CONTRACT.**
> This document preserves the reviewed TG-M20 synthesis. Current behavior and
> execution authority remain in the active specification, design, roadmap,
> root `AGENTS.md`, and root `plan.md`.

# TG-M20 Operational Baseline Synthesis

Date: 2026-08-01 JST

TG-M20 was a one-time, offline repository-development study. It observed exact
product baseline `43c91d5987b0c35c66f834789aea782e98dcaff7` under the frozen
contract completed at `a77afbe0140fef416cceeee529e9ff2c985a8e4d`. It did not
instrument taskgov, change the Skill, or create product verification,
test-strategy, Task-splitting, or parent/child behavior.

## Inputs And Provenance

Before retirement, each ignored corpus passed its deterministic full-corpus
check and matched its tracked terminal receipt. The receipts remain as
no-rerun tombstones after the corpus and temporary study machinery are
removed.

| Unit | Records | Eligible | Partial | Excluded | Corpus bytes | Corpus SHA-256 | Outcome |
|---|---:|---:|---:|---:|---:|---|---|
| M20.2 | 46 | 10 | 0 | 36 | 42,193 | `5f27b710f14420e63c56feaa777f4610666503f83cdc24d31706366af6d3c12b` | `retrospective_launch_failed` |
| M20.3 | 9 | 6 | 0 | 3 | 10,670 | `1a471bc4da568411767a7a4fb80274a8a068d4c7a76dd2b22356ad1941d1f0ed` | `collection_complete` |
| M20.4 | 19 | 14 | 1 | 4 | 22,481 | `596ca608e242bb0f3c502f35fa7aec4bed720274eb0c27e62febde2ae14fdab7` | `collection_complete` |

All three collections used protocol digest
`e43c315897f952607c703660a0d629c7a739cf1b1a17b6080a25f1b8f896a6ae`.
M20.4 additionally used canonical episode-plan digest
`78460bc2036f43ed44cb2612be3db6a0815bbe1a1e0747f28806def6db11dda4`.
The authority revision, product baseline, protocol digest, corpus digest,
record count, byte count, eligibility counts, and terminal outcome are retained
in the three receipts under `fixtures/m20/`.

Evidence was kept stratified. `machine_observed`,
`historically_reconstructed`, and `observer_attested` rows were never treated
as interchangeable. A partial, excluded, missing, capped, or unknown
predicate-critical value was not silently imputed, and an excluded scenario
was not rerun or replaced inside TG-M20.

## M20.2 Repository Baseline

The retrospective inventory contained 36 `historically_reconstructed` rows:
12 each for preparation, publication, and post-release reconstruction. All 36
were excluded with `source_missing`, so they provide neither historical
frequency nor causal evidence and were not substituted into M20.3 or M20.4.

The prospective repository harness produced 10 eligible `machine_observed`
rows across five fixed governance scenarios: one CLI-invocation row and one
state projection per scenario. Their 42 bounded operations all returned
success. The summed recorded duration was 32,550 ms, but duration was
supporting context only and never satisfied a decision predicate.

| Scenario | Eligible channels | Observed terminal transition |
|---|---|---|
| `gov_tier2_snapshot` | CLI invocation, state projection | `in_progress` to `done`; one completion cycle and review generation |
| `gov_reopen_rereview` | CLI invocation, state projection | `done` to `done`; completion cycles 1 to 2 and review generation 1 to 3 |
| `gov_pause_resume` | CLI invocation, state projection | `paused` to `in_progress` |
| `gov_tier1_commitless` | CLI invocation, state projection | `in_progress` to `done`; one completion cycle and review generation |
| `gov_handoff_continue` | CLI invocation, state projection | Task remained `in_progress`; pending Handoffs 0 to 1 |

## M20.3 Verification Proportionality

An eligible bundle required an observer attestation, a machine measurement,
and a state pair for the same scenario and trial. Two of the three planned
bundles were eligible.

| Scenario | Bundle | Verification Receipt support | Skill-guardrail support |
|---|---|---|---|
| `vp_cli_contract` | Excluded: all three rows `source_missing` | Not counted | Not counted |
| `vp_state_transition` | Eligible | All five material fact codes; after-state detail absent; minimal-receipt fit yes | Not qualifying: attested `unbounded_verification_escalation`, machine value `proportional` |
| `vp_release_contract` | Eligible | All five material fact codes; after-state detail absent; minimal-receipt fit yes | Not qualifying: attested `nondetecting_regression`, machine value `detected` |

For both eligible bundles, the same five codes appeared in
`verification_fact_codes`: `command_label`, `result`, `source_revision`,
`duration`, and `scope_coverage`. Both corresponding after-states had
`verification_detail="absent"`, and both attestations had
`minimal_receipt_fit="yes"`. The same codes appeared in manual-reentry data,
but that was supporting evidence only.

The Skill-guardrail numerator was 0 qualifying bundles. The observer labels
did not agree with the frozen machine mappings in either eligible bundle. The
one capped duration in `vp_state_transition` did not participate in those
mapping predicates and therefore was not a critical unknown. Because
`vp_cli_contract` was permanently excluded, neither the positive threshold of
two supporting bundles nor the negative requirement that all three planned
bundles be eligible was met.

The bounded cost context for the two eligible trials was:

| Scenario | Governance / reference / reviewer invocations | Manual inputs | Product files / lines | Test files / cases / lines | Verification steps / recorded ms |
|---|---|---:|---|---|---|
| `vp_state_transition` | 8 / 8 / 2 | 1 | 5 / 22 | 1 / 2 / 21 | 6 / 478,247; includes one 300,000 ms capped timeout |
| `vp_release_contract` | 16 / 14 / 6 | 1 | 2 / 24 | 1 / 1 / 29 | 8 / 392,171; all uncapped successes |

These counts and durations describe the fixed trials only. In particular, a
capped duration is not an estimate of uncapped runtime, and none of these
values independently changed a feature decision.

## M20.4 Task Boundary And Split Pressure

The non-control denominator was four planned broad/bounded pairs. An eligible
pair required both arms' attestations and measurements with matching cohort,
workload, and ordered episode identities. The Handoff control was evaluated
separately.

| Pair | Eligible | Qualifying | Reason |
|---|---:|---:|---|
| `sp_multi_outcome_intake` | Yes | Yes | Every bounded episode had all four independence fields `yes`; the broad primary outcome had `completion_independent=no` and a larger machine line delta (17 versus 14). |
| `sp_in_scope_discovery` | No | — | Broad attestation and measurement were contaminated and excluded. |
| `sp_user_expansion` | No | — | Bounded attestation was partial because `reference_opens` was not observable. Known contract/review deltas were not enough to override partial eligibility. |
| `sp_cross_module_failure` | No | — | Bounded attestation and measurement had source drift and were excluded. |

Thus `E=1`, `Q=1`, and `U=3`. The positive rule required two qualifying
pairs. The negative rule required at least three eligible pairs and
`Q+U<2`. Neither rule was satisfied.

The `sp_handoff_control` bundle was eligible: the Task stayed `in_progress`,
pending Handoffs increased from 0 to 1, delivered/withdrawn Handoffs and
completion cycles did not change, every footprint/Contract/review delta was
zero, and the episode recorded one governance cycle and zero review cycles.
This supports preservation of the Handoff boundary; it is not a successful or
failed Task-splitting sample.

The emitted arm-level cost context is shown below. `—` means the arm payload
was deliberately absent after exclusion; `?` is the partial bounded
attestation's unobservable reference count. Machine deltas are summed across
the arm's fixed episodes and ordered as files/modules/lines/Contract revisions,
then governance/review cycles.

| Scenario / arm | Attestation / measurement eligibility | Governance / reference / reviewer invocations | Manual / clarification turns | Machine deltas | Cycles |
|---|---|---|---|---|---|
| `sp_multi_outcome_intake` broad | eligible / eligible | 11 / 5 / 2 | 2 / 0 | 2 / 2 / 31 / 0 | 5 / 1 |
| `sp_multi_outcome_intake` bounded | eligible / eligible | 13 / 2 / 2 | 2 / 0 | 2 / 2 / 28 / 0 | 7 / 2 |
| `sp_in_scope_discovery` broad | excluded / excluded | — | — | — | — |
| `sp_in_scope_discovery` bounded | eligible / eligible | 20 / 2 / 3 | 2 / 0 | 1 / 1 / 10 / 0 | 12 / 3 |
| `sp_user_expansion` broad | eligible / eligible | 15 / 7 / 2 | 2 / 0 | 2 / 2 / 2 / 1 | 8 / 4 |
| `sp_user_expansion` bounded | partial / eligible | 2 / ? / 0 | 2 / 2 | 1 / 1 / 1 / 0 | 0 / 0 |
| `sp_cross_module_failure` broad | eligible / eligible | 13 / 2 / 1 | 2 / 0 | 2 / 2 / 4 / 1 | 7 / 1 |
| `sp_cross_module_failure` bounded | excluded / excluded | — | — | — | — |
| `sp_handoff_control` broad | eligible / eligible | 3 / 2 / 0 | 1 / 0 | 0 / 0 / 0 / 0 | 1 / 0 |

Only the complete `sp_multi_outcome_intake` pair was eligible for a paired
comparison. The single-arm and partial rows are observation cost context, not
substitutes for their missing paired evidence.

## Frozen-Rule Decisions

| Candidate | Decision | Evidence-bounded rationale |
|---|---|---|
| TG-M21 Verification Receipts | `proceed_to_design` | Two distinct eligible M20.3 bundles independently satisfied the frozen rule for all five material fact codes. |
| Skill-only proportional-verification guardrail | `observe_more` | 0 qualifying bundles; positive evidence is insufficient and the missing CLI bundle prevents adequate negative evidence. |
| Bounded user-approved Task decomposition | `observe_more` | One of four pairs was eligible and qualifying (`E=1,Q=1,U=3`); neither positive nor negative threshold was met. |
| Bounded further observation | `proceed_to_design` | Additional fixed observation is justified only for the two `observe_more` candidates; no observation Task is registered by this decision. |

`proceed_to_design` authorizes only a small, separately reviewed proposal. It
does not authorize implementation, Skill activation, a public command, schema
work, or Task registration.

For a later verification-guardrail observation proposal, the candidate fixed
inventory is `vp_cli_parser_followup`, `vp_viewer_contract_followup`, and
`vp_migration_contract_followup`, one attempt per scenario and no rerun. A
future approved contract must define a successor denominator because the
original excluded CLI bundle cannot become eligible. Under such a contract,
the stop condition should be the first satisfied successor positive/negative
rule or exhaustion of the three fixed scenarios; positive support still needs
two qualifying bundles.

For a later decomposition observation proposal, the candidate fixed inventory
is `sp_user_expansion_alternate`, `sp_in_scope_discovery_alternate`, and
`sp_cross_module_failure_alternate`, one broad and one bounded attempt per
scenario and no rerun. A future approved contract must explicitly define
these as replacements for the three unavailable category slots. With the
existing qualifying pair retained, the first new eligible qualifying pair
would satisfy the positive rule. If all three replacement pairs were eligible
and nonqualifying, the combined values would be `E=4,Q=1,U=0` and satisfy the
negative rule. Exhaustion without either condition remains `observe_more`.

These inventories and stop conditions are proposals only. They intentionally
do not create execution authority or a follow-up Task.

## Limitations And Retirement

- The fixed samples are small and repository-specific. They do not establish
  population frequency, causation, model-wide behavior, or benefit in other
  projects.
- Observer attestations and reviewer-key strings do not authenticate a person,
  model, process, independence, or provenance.
- Timing, size, line, and test-count values are supporting context only.
- The context-rich parent conversation and the earlier reported incident were
  excluded from the scored evidence.
- Repository guarantees do not claim deletion from provider or platform
  service logs.

The aggregate was mechanically reproducible from the three fixed corpora
before retirement. After the reviewed decision was routed, the ignored memo,
corpora, locks, and trial remnants and the tracked root-only M20 tools, tests,
and protocol fixtures were removed. The retained receipts preserve the exact
input digests and retirement anchor but deliberately do not preserve enough
data to rerun or rescore the study. This is the no-rerun privacy boundary, not
lossless archival evidence.
