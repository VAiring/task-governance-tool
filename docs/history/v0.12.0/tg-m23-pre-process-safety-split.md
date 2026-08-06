# TG-M23 Pre-Process-Safety-Split Authority Capture

> [!CAUTION]
> NON-AUTHORITATIVE HISTORY. This file preserves the exact body of
> `docs/execution-contracts/tg-m23-derived-evidence.md` from source commit
> `7313483a9fd160f0ec8127b013d9f5533d2d16ab` before its one-level
> process-safety authority split. Words such as accepted, inactive, current,
> or owner inside the captured body describe only that source revision and are
> not current authority. Current replacements are the
> [TG-M23 core owner/router](../../execution-contracts/tg-m23-derived-evidence.md#tg-m23-derived-evidence)
> and the
> [TG-M23 process-safety owner](../../execution-contracts/tg-m23-process-safety.md#tg-m23-process-safety).
> This capture activates no behavior and cannot fill an authority gap or
> satisfy a current verification, review, or completion gate.
>
> Captured body begins below.

# TG-M23 Derived Evidence Conditional Execution Contract

<a id="tg-m23-derived-evidence"></a>

> [!IMPORTANT]
> CONDITIONAL FORMAL AUTHORITY — ACCEPTED BUT INACTIVE. Load this document only
> when the current Task Contract or [authority index](../authority.md) routes to
> TG-M23. It does not activate an outbox, worker, model adapter, report store,
> network use, schema, CLI, Skill call, gate, or Task mutation.

The active [specification](../specification.md) and [design](../design.md) own
supported behavior. This document is the sole detailed owner of the accepted
inactive units' purpose, scope, order, dependencies, permission boundaries,
and gates below. Root [plan.md](../../plan.md) owns cross-sequence gateways,
current decisions, open issues, and static contracts not delegated here. The
Task database owns live state and evidence.

## Sequence Boundary

TG-M23 is sequential Tier 2 work in lane `TG-M23-DERIVED-EVIDENCE`:

| Unit/order | Task | Dependency |
|---|---|---|
| TG-M23.1 / 10 | `tg_task_722ac8a308a23d1c` | accepted TG-M22.4 |
| TG-M23.2 / 20 | `tg_task_d5511d2ca7db93dc` | accepted TG-M23.1 |
| TG-M23.3 / 30 | `tg_task_0ada32d2b4f9759d` | accepted TG-M23.2 |

The sequence consumes either a sealed native M22 Bundle or one exact
`legacy_unknown` entry from the validated canonical M22 Evidence index and
never reads SQLite directly. Canonical evidence remains independent of the
analyzer. Derived reports cannot mutate a bundle, Task, Review, Verification,
completion, Git, or any gate and add no normal Task-loop call.

Review provenance is reproduced exactly rather than inferred. Explicit v1
provenance remains `bound_attestation/trusted_caller/1`; the schema-v18 public
absence state remains `legacy_unknown/legacy_migration`, and the original
legacy Receipt's assurance is unchanged. Human/no-model, LLM/no-Skill,
declared model or Skill, deterministic-tool, hybrid, `unknown`,
`not_required`, `not_applicable`, `not_used`, and index-only legacy absence
remain distinct. Descriptive grouping and visibility of the same declared
profile or method across distinct Receipts never establishes identity,
competence, independence, quality, diversity, or truth and never affects reviewer-key
distinctness, PASS, findings, Tier, verification, or completion.

The analyzer consumes only what the exact M22 Bundle/JSON boundary carries. A
native Receipt report cites its Receipt and bundle binding; v1 additionally
cites its provenance ID, version, and digest and reproduces every scalar and
canonically ordered profile/lens/method code. A `not_required` Receipt renders
as a gate disposition with null provenance, not as v0 or explicit unknown.
Migrated v0 Receipts have no Evidence Reference and cannot enter a native
bundle. A pre-v19 index-only cycle therefore reports `legacy_unknown` from its
cycle/index binding and states that Receipt/provenance detail is unavailable;
it never fabricates a v0 object, Receipt citation, provenance ID, or digest.
Grouping is allowed only over actually declared v1 values; repetition means
the same allowed code across distinct v1 Receipts or bundles, never a duplicate
inside one Receipt collection. Index-only absence, null, and an empty v1
collection remain separate.

<a id="tg-m23-1"></a>

## TG-M23.1 Design

Task `tg_task_722ac8a308a23d1c` is documentation/design only. It may freeze
deterministic local outbox descriptors, ephemeral bounded
exact-basis packets, a separate worker and validator, immutable analysis/report
revisions, exact criterion/artifact/evidence citations, omission and
uncertainty reporting, and human-readable rendering. It must define a strict
offline deterministic baseline plus an optional isolated Codex non-interactive
path with bounded cost, retry, timeout, cancellation, and failure states.

Eligible packet source/citation data is limited to sanitized data selected from
one exact sealed bundle or one validated legacy index entry, never SQLite, full
conversations, or unrestricted repository content. The legacy source allow-
list contains only the index `project_id`, `projection_generation`,
`index_digest`, and the entry's exact nine fields; its five bundle/file
identity and seal-time values remain null.
Reports must separate observed target/gate facts, trusted-caller declarations,
legacy absence, and `llm_derived` non-authoritative conclusions.
Reproducibility may record bounded producer, model, prompt-schema, input, and
output digests but must not claim authenticated model or actor identity.

This unit activates no runtime, schema, public CLI, Skill loop, worker, network,
credential use, or report publication. It may not infer provenance from
`reviewer_key` or summary text, invent missing model/Skill values, collapse
closed states, score reviews, or retain raw chat, prompt bodies, reasoning,
logs, large diffs, environments, secrets, session IDs, arbitrary provider
bodies, or unrestricted content.

Completion requires exact core/worker and privacy boundaries, exact citations
and provenance rendering, bounded offline/optional paths, zero normal-loop
calls or mutations, a bounded M23.2/M23.3 split, documentation checks, an exact
diff and current Verification Receipt, and two independent Tier 2 reviews with
no unresolved High or Medium finding.

<a id="tg-m23-2"></a>

## TG-M23.2 Activation

Task `tg_task_d5511d2ca7db93dc` may implement only the exact design accepted in
TG-M23.1: deduplicated local jobs, bounded native-bundle or legacy-index
packets, a separate worker, optional Codex adapter, schema and citation validation,
immutable report revisions, timeout/cancel/retry/status handling, and local
publication. The adapter consumes canonical M22 Bundle/JSON through the
approved boundary and never SQLite.

Offline rendering must work without inference. Optional configured inference
must be isolated and emit schema-valid `llm_derived` output bound by recorded
digests. Invalid, timed-out, unavailable, or rejected analysis is bounded and
non-blocking. No output may change canonical evidence, assurance, reviewer
distinctness, a Task, a gate, or a completion decision. Live credentialed or
paid validation requires separate exact current authority.

Completion requires deterministic job/packet deduplication, exact provenance
and citation cases across every closed state above, replay and failure safety,
no fake values, inference-based state, scoring, assurance upgrade, direct DB
access, Task/gate mutation, or added Task-loop call, plus privacy, package,
focused/full offline checks, exact diff, a current Verification Receipt, and
two independent Tier 2 reviews with no unresolved High or Medium finding.

<a id="tg-m23-3"></a>

## TG-M23.3 Integrated Acceptance

Task `tg_task_0ada32d2b4f9759d` accepts the exact TG-M23.2 target across
multiple bundles and legacy index entries, queue/restart/replay/failure,
offline rendering, and optional mocked-model analysis. It must trace every value and conclusion to
the available stable bundle, cycle, artifact, Verification Receipt, Review
Receipt, and provenance IDs, versions, and digests; an index-only legacy cycle
instead cites only its exact cycle/index binding. It covers all reachable
conditional and legacy states plus the same profile or method across distinct
Receipts/bundles.

Reports must remain useful while preserving the separation between facts,
trusted-caller declarations, legacy absence, and inference. No missing value is
fabricated, no repetition notice implies dependence or poor quality, and every
failure mode remains bounded and non-blocking. Canonical evidence, assurance,
all gates, and the normal Skill call count remain unchanged.

Offline and mocked acceptance is sufficient when live external-model execution
has no separate authority; that live check is `not_applicable` and cannot block
completion. If a separate exact credential/data/cost authority is granted, its
exact live smoke must pass. Completion otherwise requires focused/full offline,
privacy, package/release, exact-diff and realistic integrated checks, a current
Verification Receipt, and two independent Tier 2 reviews with no unresolved
High or Medium finding. Only bounded corrections inside TG-M23.1 are allowed.

## Deferred Detail Rule

Within the native-bundle/legacy-index source and citation boundary fixed above,
TG-M23.1 owns any exact packet schema, module layout, local storage location,
adapter protocol, or report format needed by TG-M23.2. This pre-design contract
does not select those details. Any semantic expansion, gate authority, direct
database access, new public leaf, new normal-loop call, or network behavior
requires separate explicit authority.
