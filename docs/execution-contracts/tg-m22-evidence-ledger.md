# TG-M22 Evidence Ledger Current And Conditional Execution Contract

> [!IMPORTANT]
> MIXED FORMAL AUTHORITY: TG-M22.3/SCHEMA V19 IS CURRENT; TG-M22.4 IS
> INACTIVE. Load this document only when the current Task Contract or
> [authority index](../authority.md) routes to TG-M22. It does not activate
> TG-M22.4, Analyzer, or Runner.

The active [specification](../specification.md) owns current product behavior,
and the active [design](../design.md) owns implementation structure. This
document owns exact accepted TG-M22.1A/TG-M22.2/TG-M21.5 predecessor detail,
current TG-M22.3 execution and acceptance detail, plus the remaining inactive
unit's purpose, scope, order, dependencies, permissions, and gates.
Root [plan.md](../../plan.md) owns cross-sequence gateways, decisions, open
issues, and non-delegated static contracts. The Task database owns live state
and evidence.

<a id="tg-m22-1a"></a>

## TG-M22.1A Review Method And Provenance

Task `tg_task_0e1d93d81eb843ab` defines the closed versioned Review-provenance
matrix implemented by the accepted schema-v18 TG-M22.2 foundation and retained
by current TG-M22.3. This anchor owns that exact
supporting design contract and activates no later unit by itself.

### Versioned Public Union And Structural Seal

From schema v18, every public Review Receipt object adds exactly one
`review_provenance` value. A new `independent` or `self_review_fallback`
Receipt has a v1 object with exactly these keys in its allow-list:

```text
review_provenance_id provenance_version reviewer_class model_state
declared_model_id skill_state declared_skill_id declared_skill_version
review_profiles review_lenses context_relation method_codes assurance_class
producer_class producer_version digest
```

Its ID is `tg_review_provenance_` plus 16 lowercase hexadecimal characters;
version is integer `1`; assurance/producer is exactly
`bound_attestation/trusted_caller/1`; and `digest` is
`sha256:<64-lowercase-hex>`. The digest is SHA-256 over
`taskgov-review-provenance-v1\0` plus canonical sorted-key compact UTF-8 JSON
containing exactly `project_id`, `task_id`, `review_receipt_id`, `receipt_kind`,
`target`, and every v1 public field from `provenance_version` through
`producer_version`. `target` is exactly
`{kind,value,base_revision,generation,capture_version}`. The input excludes the
random provenance ID and digest.

A pre-v18 `independent` or `self_review_fallback` Receipt has no native row and
projects the same keys with `provenance_version=0`, null ID/digest and null v1
semantic fields/collections, plus exactly
`legacy_unknown/legacy_migration/1`. Version zero states absence; it does not
infer `unknown` from reviewer key, summary, kind, or verdict. An existing or
new Tier-0 `not_required` Receipt remains the current gate-disposition record
but projects `review_provenance=null` and owns no provenance row. Thus legacy
absence, explicit v1 unknown, empty v1 code sets, and not-required are distinct.

### Closed Vocabularies, Bounds, And Ordering

The exact scalar enums are:

```text
reviewer_class   human llm deterministic_tool hybrid unknown
model_state      declared not_applicable unknown
skill_state      declared not_applicable not_used unknown
context_relation same_context forked_context fresh_context external_context
                 not_applicable unknown
```

The exact profile, lens, and method enum orders are:

```text
review_profiles general authority_contract implementation verification
                migration_compatibility privacy_safety release_acceptance
review_lenses   correctness contract_compliance state_completion_integrity
                privacy target_safety verification_regression
                migration_compatibility maintainability accessibility
                performance release_integrity
method_codes    review_packet_inspection authority_cross_check diff_inspection
                source_inspection test_inspection
                verification_evidence_inspection artifact_inspection
                runtime_observation deterministic_rule_check
```

At most four profiles, eight lenses, and eight methods may be supplied. Each
is a set: duplicates are invalid, empty is valid, and stored/public order is
the fixed enum order regardless of option order. `context_relation` is one
required code. Downstream repetition means the same allowed code across
distinct v1 Receipts or bundles; a duplicate within one collection is invalid.
It is only a caller declaration about supplied context; no code proves
separation or independence. Declared model and Skill IDs are 1-128
ASCII bytes matching `[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}`. Declared Skill
version is 1-64 ASCII bytes matching
`[A-Za-z0-9][A-Za-z0-9._+-]{0,63}`. They are preserved byte-for-byte after
the normal privacy check and are identifiers, not free-form capability claims.

The existing `review receipt add` leaf adds required
`--reviewer-class`, `--model-state`, `--skill-state`, and
`--context-relation`, optional `--declared-model-id`,
`--declared-skill-id`, and `--declared-skill-version`, and repeatable
`--review-profile`, `--review-lens`, and `--review-method` only for
`independent` and `self_review_fallback`. No value is defaulted or inferred.
Every provenance option is forbidden for `not_required`. Type, enum, bound,
duplicate, grammar, or cross-field failure uses the existing
`invalid_review_evidence` boundary with fixed sanitized detail; the common
privacy rejection keeps its precedence and no rejected value is emitted.

The accepted case matrix is closed as follows:

| Case | `reviewer_class` | `model_state` and declared model ID | `skill_state` and declared Skill ID/version | Required interpretation |
|---|---|---|---|---|
| Human | `human` | `not_applicable`; ID absent | `not_applicable`; ID/version absent | A model and Skill are not prerequisites for a human review. |
| LLM without Skill | `llm` | `declared` with supplied ID, or `unknown` with no ID when applicable data is unavailable | `not_used`; ID/version absent | No Skill use is a valid explicit state. |
| LLM with Skill | `llm` | `declared` with supplied ID, or `unknown` with no ID | `declared` with supplied ID and version, or `unknown` with neither | Declared values are stored only when supplied; no value is inferred. |
| Deterministic tool | `deterministic_tool` | `not_applicable`; ID absent | `not_applicable`; ID/version absent | Tool output is not relabeled as model or Skill use. |
| Hybrid | `hybrid` | `declared` with supplied ID, or `unknown` with no ID | `declared` with supplied ID/version, `not_used`, or `unknown` | Each component retains its own applicability and declaration state. |
| Applicable data unavailable | `unknown` | `unknown`; ID absent | `unknown`; ID/version absent | Unknown is explicit and is never guessed from reviewer key or summary. |
| Review not required | existing Tier-0 `not_required` Receipt only | no model state | no Skill state | The gate disposition remains; its `review_provenance` is null and it has no provenance row. |
| Legacy v17 independent/self-review Receipt | no native v1 subrecord | v0 absence only | v0 absence only | Projection is exactly `legacy_unknown/legacy_migration`; original Receipt assertions and assurance remain unchanged. |

The matrix is exact. Human and deterministic-tool rows require both states
`not_applicable`. LLM and hybrid rows require model `declared` with its ID or
`unknown` without one; their Skill state is `declared` with both ID/version,
`not_used` without either, or `unknown` without either. Reviewer class
`unknown` requires both states `unknown`. `declared` always requires its
corresponding value or values and every other state requires them absent.
Receipt verdict does not change the matrix. Profile/lens/method sets and
context are otherwise independently chosen from their closed values and add no
capability, applicability, or further cross-field inference.

### Storage, Migration, Read, And Downstream Binding

M22.2 adds `review_provenance_basis_version` and nullable
`review_provenance_id` to `review_receipts`, plus normalized immutable
`review_receipt_provenance` and `review_receipt_provenance_codes` tables. The
latter stores only `profile|lens|method`, a zero-based contiguous ordinal
within one provenance and kind after fixed-enum ordering, and an allowed code;
duplicate provenance/kind/code or provenance/kind/ordinal is invalid, and no
JSON or arbitrary map is stored. Deferred same-Receipt ownership plus
insert/read guards enforce: migrated independent/self-review is `0/null`, new
independent/self-review is `1/non-null` with exactly one v1 row, and
`not_required` is `0/null`. New legacy-shaped independent/self-review inserts
are forbidden. Receipt and provenance rows/codes commit atomically.

Migration 18 adds only the zero/null discriminator to old Receipts and creates
no provenance row, ID, digest, declared value, or Evidence Reference. Reentry,
setup, backup, recovery, and every stored Review reader validate the exact
version/kind/null/code/digest matrix. A schema-v17 source uses its existing
shape; a schema-v18 source with malformed provenance is structural failure,
never the Task-verification candidate-local recovery exception. Public Receipt
add/show reads emit the union above. Review Packet keeps its existing keys but
its required-output/receipt-command text requests the same v1 fields. Viewer
validates then discards provenance and adds no field, filter, or UI.

Explicit v1 values remain `bound_attestation/trusted_caller/1`. The structure
proves no identity, execution, competence, independence, quality, diversity,
or truth and changes no reviewer-key distinctness, PASS counting, Tier, or
completion gate. The original Review Receipt assertion remains
`bound_attestation/trusted_caller/1` even when its distinct absent provenance
projects `legacy_unknown/legacy_migration/1`. It stores no person identity,
session, prompt, chat, reasoning, raw output, command, log, environment,
credential, or provider body.

M22.2 binds the native v1/null provenance subset inside the Review Receipt
Evidence Reference source projection and reference digest, without changing
the Receipt assertion's assurance. A migrated v0 Receipt gets no Reference.
M22.3 copies the native v1/null subset into each Bundle/JSON `review_receipts`
member; pre-v19 legacy cycles remain index-only and expose no Receipt. M22.4
owns the complete matrix acceptance. M23 cites v1 ID/digest, reports null
not-required, and reports index-only legacy absence without inventing v0
Receipt data or upgrading assurance. The current schema-v19 boundary established
by TG-M22.2/TG-M21.5 and extended by TG-M22.3 adds no
separate public leaf, normal-loop call, Viewer UI, network behavior, or
target-project authority.

Completion requires the closed v1 code vocabularies and validation matrix in
the formal documents, synchronized downstream Task Contracts, exact
documentation consistency and diff checks, a current full-coverage
Verification Receipt bound to the exact review-target generation, two distinct
independent Tier 2 PASS reviews, and no unresolved High or Medium finding.
Those gates do not authorize runtime, schema, package, generated-artifact,
network, or target-project writes.

<a id="tg-m22-sequence"></a>

## Mixed Current And Inactive Sequence

All units use sequential Tier 2 lane `TG-M22-EVIDENCE-LEDGER`. TG-M22.1A is
the accepted design prerequisite, TG-M22.2 and TG-M21.5 are accepted
predecessors, TG-M22.3 is current, and the later row remains inactive.

| Unit/order | Task | Dependency | Bounded outcome and gate |
|---|---|---|---|
| TG-M22.1A / 25 | `tg_task_0e1d93d81eb843ab` | accepted TG-M21.4D and completed TG-DOC.1 | Accepted prerequisite: freeze the closed Review-provenance case/code/validation matrix in formal documents and downstream Contracts only; require exact documentation consistency and diff, a current exact-target Verification Receipt, two independent Tier 2 PASS reviews, and no unresolved High/Medium finding. |
| TG-M22.2 / 30 | `tg_task_88bfe19eb6cffe2e` | accepted TG-M22.1A | Accepted predecessor: schema v18 capture, 1,000-character durable/read capacity, and Review provenance while preserving its 500-character public ingress and recovery/read boundaries; full offline/package checks and two reviews. |
| TG-M21.5 / 40 | `tg_task_e7701fb907020905` | accepted TG-M22.2 | Accepted predecessor: raise only explicit public verification admission to 1,000 characters without changing storage or the shared validator; focused/full checks and two reviews. |
| TG-M22.3 / 50 | `tg_task_ae6f52c4f7b25549` | accepted TG-M21.5 | Current: activate schema v19 native bundles and deterministic JSON publication without Analyzer, Runner, or gate expansion; migration/recovery/full offline checks and two reviews. |
| TG-M22.4 / 60 | `tg_task_0a90b4caf566a8fd` | accepted TG-M22.3 | Inactive: later accept the combined legacy/current, provenance, capacity, recovery, bundle, JSON, privacy, and no-write matrix with bounded corrections only; full checks and two reviews. |

No unit grants publication, push, tag, Release, external-service, network, or
target-project mutation authority. Live status, blocker detail, review target,
evidence, and completion history remain solely in the Task database.

<a id="tg-m22-conditional-product"></a>

## Current TG-M22.3 And Conditional Later Product Acceptance Detail

## Current TG-M21.4 Verification Subject Correction Detail

TG-M21.4, Task `tg_task_6ae822dd1a77c095`, defines the tool-owned subject
correction implemented by accepted TG-M22.2 and retained by current TG-M22.3.
Its authority reference is
`conversation_decision:2026-08-02:tool-owned-verification-subject-interrupt`.
This section is accepted schema-v18 predecessor detail retained by the current schema-v19 candidate; the active
specification remains the product-behavior owner.

### TG-M21.4A Schema-v18 Capacity Compatibility Matrix

TG-M21.4A, Task `tg_task_95c5e968c8fe7e4b`, defines the capacity split used
by accepted M22.2 and current M21.5. Its authority reference is
`conversation_decision:2026-08-02:schema-v18-verification-capacity-compatibility-correction`.
It adds no behavior beyond the exact capacity states below:

| Reachable state | Durable storage and stored-read/internal-derived capacity | Explicit public `task add` / `task edit --verification` admission |
|---|---:|---:|
| legacy/source schema v17 | 500 characters | 500 characters |
| accepted TG-M22.2 v0.12.0 / schema v18 | 1,000 characters | 500 characters |
| accepted TG-M21.5 v0.12.0 / schema v18 | 1,000 characters | 1,000 characters |

The schema-v18 durable limit applies to the exact Task verification bytes and
every value derived from or bound to them: authority snapshots, verification
criteria, expectation digests, subjects, Receipts and reviews, completion and
reopen/cycle history, Task-show and Review Packet reads, Viewer capture,
setup/migration reentry, backup, recovery, and the schema-v19 Bundle/JSON
consumer. M22.2 must accept and preserve valid stored values through exactly
1,000 characters on every such path even though its two explicit public Task
ingress points still reject 501 characters. A metadata-only or lifecycle
write against a Task already containing 501-1,000 characters must not reuse
the narrower public-input validator or partially write before failing.

M22.2 therefore establishes the durable/read boundary with no schema or
capability marker beyond schema v18. Because M21.5 retains the same package
and schema identity, M22.2 code must safely open, validate, read, project,
retarget, review, complete, reopen, set up, back up, and recover a database
containing a valid 1,000-character verification value written after M21.5.
Values over 1,000 remain invalid and fail closed without partial mutation.
Existing privacy checks and aggregate output caps remain unchanged; exceeding
an existing aggregate projection cap follows that projection's existing
bounded failure contract and is not stored-state corruption.

Stored validation is source-schema-aware before any migration or recovery
write: a schema-v17 source permits at most 500 characters, while a schema-v18
or later supported source permits at most 1,000. Setup must not migrate a
schema-v17 501-1,000-character row and thereby legitimize invalid source
state. Migration, reentry, recovery, and schema-v19 activation reject invalid
stored state before DDL/history, publication, or business rows can commit.

## Current And Conditional TG-M21.4A Capacity Compatibility Details

At the M22.2 public ingress, even an explicit same-value verification replay
of 501-1,000 characters is rejected before a transaction. The existing input
order remains privacy before length. Conversely, a stored value over its
schema limit or failing privacy is reported through the owning sanitized
stored-state inconsistency result, never as caller `invalid_argument` or
`privacy_rejected`, and leaves Task, event, generation, snapshot, criterion,
reference, Receipt, finding, cycle, manifest, and generated artifacts
unchanged.

Accepted M21.5 changed only the two explicit public Task-ingress admission checks from
500 to 1,000. It adds no migration, backfill, stored-reader change, schema or
package identity, other field-limit change, new command, or normal-loop call.
M22.2 owns stored/read compatibility tests using pre-admitted 501-, 1,000-,
and rejecting 1,001-character state across all named paths. M21.5 owns the
public 500/501/1,000/1,001 boundary and an offline compatibility replay of the
exact complete M22.2 completion package against its newly admitted state; a
mixed-revision package is not evidence. Both ASCII and multi-byte boundary
values are covered without raising any existing UTF-8 aggregate output cap.
M22.3 consumes the already-valid 1,000-character criterion; M22.4 accepts the
full matrix.

## Current TG-M21.4 Verification Subject Details

### Tool-Owned Subject And Legacy Matrix

At schema v18, one versioned verification subject is the deterministic
structural identity of the exact verification criterion selected by taskgov
for the locked Receipt basis. It is not a command, command description,
verification run, result, coverage claim, project decision, or evidence that
taskgov executed anything. Its complete identity is the following existing or
schema-v18-owned tuple:

```text
subject_basis_version, authority_snapshot_id, verification_criterion_id,
project_id, task_id, contract_revision, target_kind, target_value,
target_base_revision, target_generation
```

The authority snapshot and whole-field verification criterion are created and
bound by the schema-v18 target-capture contract below. A nonempty verification
criterion requires subject basis version 1 and non-null matching snapshot and
criterion IDs. A verification expectation whose trimmed text is empty has no
criterion and therefore no subject. Contract revision and the complete target
tuple remain the existing Receipt fields; taskgov never asks the caller to
repeat them or to supply a subject value.

Schema v18 adds exactly these subject-binding fields to both Verification
Receipts and completion cycles:

```text
verification_subject_basis_version
subject_authority_snapshot_id
subject_verification_criterion_id
```

The version/null matrix is closed:

| Durable row | Subject basis | Snapshot / criterion | Legacy label meaning |
|---|---:|---|---|
| any Receipt or cycle that already existed at v17 | `0` | both null | Existing Receipt `command_label` bytes remain a caller-supplied legacy label; cycles gain no inferred label or subject. |
| new schema-v18 Receipt | `1` | both non-null and equal to the capture-version-1 target binding | No caller label exists. |
| new schema-v18 native cycle with nonempty verification | `1` | both non-null and equal to its qualifying subject-v1 Receipt | The linked Receipt remains required and `pass/full`. |
| new schema-v18 native cycle with trimmed-empty verification | `1` | both null | No Receipt or subject is invented. |
| the existing partial legacy reopen bridge | `0` | both null | Its existing verification-basis-v0 behavior is unchanged. |

This discriminator is independent of the existing cycle
`verification_basis_version`. In particular, a valid v17 native cycle keeps
verification basis version 1 while receiving subject basis version 0/null/null.
Migration never changes that cycle, its linked Receipt, or its meaning.

The v17 `verification_receipts.command_label TEXT NOT NULL` storage column is
retained so migration requires no table rebuild and no existing row update.
For a new subject-v1 Receipt, taskgov alone stores the exact internal
compatibility value `taskgov-owned-verification-subject-v1` in that legacy
column. The value is not caller input, a public label, an Evidence Reference
field, a bundle/JSON field, or a digest input. Version-aware storage validation
accepts the existing bounded legacy-label predicate only for subject-basis
zero rows and accepts only that fixed internal value for subject-basis one.

The schema-v18 public `verification_subject` object has exactly:

```text
basis_version kind authority_snapshot_id verification_criterion_id
legacy_caller_label
```

For subject basis 0 it is exactly version `0`, kind
`legacy_caller_label`, null snapshot/criterion IDs, and the unchanged stored
v17 label. For subject basis 1 it is exactly version `1`, kind
`task_verification_criterion`, the two non-null IDs, and null
`legacy_caller_label`. The complete Contract and target binding remains in the
Receipt's existing `contract_revision` and `source_revision`; neither form is
allowed to claim that a caller identity is authenticated.

That five-key object is the schema-v18 Receipt/read-model union needed to
distinguish legacy rows. The schema-v19 native Bundle/JSON projection is a
separate no-extra-key subject object with exactly `basis_version`, `kind`,
`authority_snapshot_id`, and `verification_criterion_id`. It accepts only
subject basis 1, so the `legacy_caller_label` key itself is absent rather than
present with a null value. Parent Bundle Task, Contract, and target objects
complete the same compound identity.

### CLI, Read Model, And Gate Transition

Schema-v18 activation retains the same 21st public leaf and removes only the
caller label option. Its caller fields become exactly:

```text
taskgov verification receipt add <task-id>
  --result <pass|fail|timeout>
  --duration-ms <nonnegative-int64>
  --scope-coverage <full|partial>
  --expected-target-generation <positive-int64>
```

`--command-label` is no longer accepted, no subject option replaces it, and
the fixed success text remains unchanged because it never displayed a label.
Validation retains read-only rejection followed by result, duration, coverage,
and expected-generation validation, then the existing Task/status/
expectation/target/currentness/uniqueness sequence. Under the writer lock,
taskgov copies the capture-version-1 target's exact snapshot and verification
criterion binding. Expected generation, Contract, expectation digest, target
freshness, one Receipt per generation, failure/timeout/partial recovery, the
explicit `--verification-complete`, and the `pass/full` completion gate are
unchanged.

From schema v18, the public Receipt replaces top-level `command_label` with
the `verification_subject` object and otherwise retains its exact v17 fields.
This applies to the Receipt-add result and `task show` recent rows. The Task-
show `verification_evidence` object additionally includes exactly one
`current_verification_subject`, null without a capture-version-1 nonempty
criterion and otherwise the same subject-v1 object. For an active or review-
pending Task whose verification expectation is nonempty after trimming and
whose retained target has capture version 0, its gate reports
`evidence_basis_stale` before Receipt-required/blocking evaluation. An empty
verification expectation keeps the existing Task-show gate
`required=false,satisfied=true,blocking_code=null` and a null subject even on
capture version 0; the broader source-write guard below still applies to
review and completion. Retargeting creates the subject basis and restores the
normal gate.

At schema v18 the `task show` gate `blocking_code` enum therefore adds exactly
`evidence_basis_stale` for that nonempty-verification branch, after target
absence and before `verification_receipt_required|verification_receipt_blocking`.
The existing `task complete --check` allowed readiness codes add the same value
after target absence for any capture-version-0 target, before verification or
review evidence evaluation, because completion creates ledger sources even
when verification is empty. Its fixed generic suggested action is exactly
`resolve evidence_basis_stale before completing the task`; every other code,
priority, output key, and size bound remains unchanged.

A done v17 cycle is not retrospectively made stale. Subject-basis-zero done
cycles continue to validate and project under their exact v17 verification-
basis and Receipt-link rules, with any old label explicitly marked
`legacy_caller_label`. Reopen preserves that cycle as audit history, clears
the current target normally, and requires a fresh capture-version-1 target.
When the resulting current verification is nonempty, later completion also
requires its subject-v1 Receipt; when it is trimmed-empty, the fresh target and
later basis-v1/null/null cycle intentionally have no subject or Receipt.
Corrupt subject versions, null matrices, ownership, or target/criterion
bindings fail through
`invalid_verification_evidence` for a Receipt or
`completion_history_inconsistent` for a cycle without exposing stored values.

The subject is structural taskgov-owned binding metadata. The Receipt's
reported result, duration, and coverage remain
`bound_attestation/trusted_caller/1`; creating the subject does not make the
Receipt `machine_observed` or `deterministically_derived`, authenticate a
caller or process, or upgrade its truth. No command body, arguments, exit
code, output, environment, prompt, chat, diff, secret, or arbitrary prose is
added.

### Migration And Downstream Activation

Migration 18 adds the three nullable/defaulted subject fields and their
version-aware indexes/triggers without rebuilding the Receipt or cycle table
and without updating, deleting, relabeling, or reclassifying a v17 row. Every
existing row reads as subject basis 0/null/null; its pre-migration columns,
IDs, counts, ordering, target, timestamp, label bytes, Receipt links, and cycle
digests remain unchanged. It creates no subject binding or Evidence Reference
for an old target, Receipt, or cycle. Reentry validates the exact zero/one
matrix and performs no reconciliation or backfill.

This repository's M22.2 self-host completion is part of that reentry boundary.
After schema-v18 setup, any pre-activation target, Receipt, and review material
for M22.2 remains capture-version-0 audit lineage and cannot qualify. M22.2
must set one fresh exact target, rerun verification, record one subject-v1
Receipt, prepare review once, and obtain fresh exact-generation Tier 2 review
Receipts before completion. Migration or setup never upgrades the old rows,
and no special self-host command or bypass exists.

The bounded dependent sequence is:

1. Accepted TG-M22.2 provides the schema-v18 subject fields, target-owned
   subject derivation, label-free CLI, Task-show transition, cycle validation,
   Evidence Ledger capture foundation, and 1,000-character durable/read
   capacity while public Task add/edit admission remains 500.
2. Accepted TG-M21.5, after schema v18 and before schema v19, changed only explicit
   public Task add/edit verification admission from 500 to 1,000 characters;
   it performs no migration, stored-reader change, or subject redesign.
3. Current TG-M22.3 admits only subject-v1 qualifying Receipts to native bundles and
   Evidence JSON and includes no legacy caller label or internal compatibility
   value.
4. Inactive TG-M22.4 later accepts the combined legacy/current, 1,000-character, bundle, and
   projection behavior. M23/M24 producers remain separate.

All four activation/acceptance units retain one public Receipt write leaf,
zero additional normal-loop LLM calls, current project authority, and their
separate exact-target verification and Tier 2 review gates.

## Current TG-M22.3 Evidence Ledger And Bundle Contract

TG-M22.1, Task `tg_task_0a3c0d361da10f49`, defines the shared design for a
canonical local Evidence Ledger, a future immutable Bundle per native
completion cycle, and deterministic sanitized JSON. Its authority reference is
`conversation_decision:2026-08-01:roadmap-retirement-and-evidence-sequence`.
This section is current acceptance detail for TG-M22.3 over the accepted
TG-M22.2/schema-v18 capture foundation and TG-M21.5 admission boundary; its
schema-v19 Bundle/JSON clauses are active. The current
v0.12.0 candidate is schema v19, Viewer snapshot v4 with sources v5-v19, 21
public leaves, and the ten-or-eleven-call Skill flow.

Accepted TG-M22.2 established the v0.12.0/schema-v18 candidate with the accepted
TG-M21.4 subject correction. Accepted TG-M21.5 retained that schema/package
identity while changing only public verification-text ingress; current
TG-M22.3 retains v0.12.0 while advancing its schema to v19. No unit claims a
published tag, Release, remote commit, or immutable artifact identity.

The ledger is evidence storage, not project-decision authority. Governing Git
documents and current explicit user decisions continue to own purpose, scope,
acceptance, permissions, and execution gates. The ledger records their bounded
Task-local projection and exact evidence binding; it never invents or resolves
an acceptance criterion, project test strategy, reviewer identity, external
truth, or semantic conclusion.

### Assurance And Producer Vocabulary

Every evidence reference has one `assurance_class` and an independent
versioned `producer_class`. Assurance is exactly one of:

| Assurance class | Supported meaning |
|---|---|
| `machine_observed` | A bounded deterministic taskgov component directly observed the stated local process or Git-object fact. It does not authenticate the machine, environment, user, or external meaning. |
| `bound_attestation` | The trusted caller recorded a bounded assertion bound to the exact Task, Contract, target, and applicable generation. Taskgov stores and validates the binding but does not prove the assertion. |
| `deterministically_derived` | A pure versioned rule derived the value from validated source records. The result cannot have stronger assurance than those sources. |
| `external_reference` | A bounded external identity is retained without claiming that taskgov observed its content, existence, authority, or semantics. |
| `legacy_unknown` | Required origin or basis predates capture or is unavailable. No migration, repair, or projection may fill or strengthen it. |
| `llm_derived` | A future analyzer produced a non-authoritative semantic claim from a cited native bundle or exact legacy-index entry. The label authorizes no canonical ledger write and never satisfies a verification, review, completion, or release gate. |

`producer_class` is exactly `taskgov_core`, `taskgov_git`, `trusted_caller`,
`legacy_migration`, `external_system`, `batch_analyzer`, or
`verification_runner`, plus a positive `producer_version`. TG-M22 activates
only the producers needed by its owning units. M23 may use `batch_analyzer`
only as metadata on its separate report artifacts; M23 activation enables no
canonical `derived_analysis` Evidence Reference or criterion-link writer. That
writer remains disabled pending separately indexed post-M23 authority.
`verification_runner` remains reserved until its own later activation.
Producer values are provenance labels, not authentication, signatures,
process identity, independence, or authority.

The required current mappings are fixed:

- exact Git object, mode, and path observations are `machine_observed` from
  `taskgov_git`;
- canonical digests, generations, deterministic links, and gate snapshots are
  `deterministically_derived` from `taskgov_core`;
- M21 Verification Receipts and the original Review Receipt/Finding assertions
  remain `bound_attestation` from `trusted_caller`; explicit v1 Review
  provenance is separately caller-attested, absent legacy provenance alone is
  `legacy_unknown/legacy_migration`, and null not-required provenance has no
  class; the same caller assurance also applies only to the caller's
  `commit_not_required` completion assertion;
- a locally resolved Git completion commit is `machine_observed` from
  `taskgov_git`, while an approved external completion revision remains
  `external_reference` from `external_system`;
- an approved external review target remains `external_reference` from
  `external_system`;
- migrated absence remains `legacy_unknown` from `legacy_migration`; and
- no bundling, projection, regeneration, later analysis, or Runner activation
  may relabel an existing fact or transitively upgrade its assurance.

### Authority Snapshot And Whole-Field Criteria

Schema v18 adds an immutable `authority_snapshot` with stable ID
`tg_authority_snapshot_<16-lowercase-hex>`, a positive Task-local generation,
and a domain-separated digest. It captures exactly the current Task title,
description, review tier, verification expectation, Contract revision, scope,
acceptance, constraints, authority reference, and whether the Contract is
specified. It contains no prompt, conversation, hidden reasoning, arbitrary
document body, or inferred authority.

Task add creates the first snapshot. A canonical change to title, description,
verification, review tier, or current Contract creates the next snapshot in
the same Task transaction; status, priority, kind, lane/order, tags, notes,
holds, checkpoints, targets, evidence, and completion state do not by
themselves create one. Exact same-basis replay writes nothing. An
authority-bearing change after review targeting has begun clears the target,
advances its generation, and invalidates current verification and review
eligibility using the established Contract/verification invalidation shape.
This prevents an old target from silently binding a changed purpose or gate.

Revision-zero Contract state is captured explicitly as
`contract_unspecified`; empty scope or acceptance is never invented. Migration
may capture the exact currently stored Task basis but does not reconstruct a
historical basis, approval event, or old snapshot. A pre-v18 target remains
capture version 0 with no synthesized snapshot or manifest binding and must be
set again before it can support a post-v19 native Evidence Bundle.

From schema v18, a capture-version-0 target is read-only lineage. Verification
Receipt add, Review Receipt add, Review Finding add, and either done path would
create a new evidence source and therefore fail `evidence_basis_stale` with the
fixed message `current evidence basis must be captured again`; setting a fresh
target creates capture version 1 and restores those writes. Review preparation
and resolution of an already stored Finding remain allowed because they create
no evidence source. The new guard runs after existing target structure and any
caller expected-generation equality check, and before source uniqueness,
Receipt/review sufficiency, or completion insertion. No command synthesizes
bindings for the old target.

A `contract_criterion` has stable ID
`tg_contract_criterion_<16-lowercase-hex>`, kind `acceptance` or
`verification`, exact sanitized whole-field text, its domain-separated digest,
and Task ownership. One Contract acceptance field and one Task verification
field are each at most one criterion. Taskgov never splits prose, creates a
checklist, rewrites wording, judges satisfiability, or calls a model to infer
subcriteria. Scope and constraints remain immutable authority context rather
than automatically satisfied criteria. Revision-zero acceptance and a
verification expectation whose trimmed text is empty use explicit omission
codes rather than an invented criterion. The authority snapshot and its basis
digest nevertheless retain the exact stored verification bytes, including the
empty string or legacy surrounding and whitespace-only bytes; criterion
omission never normalizes or rewrites them. An unchanged same-Task kind/digest
may reuse the existing immutable criterion.

Review target setting binds its new generation to the exact current authority
snapshot and applicable criterion IDs. Those bindings are part of currentness
along with the existing Task, Contract, verification digest, and four-field
target tuple. When the verification criterion exists, that same operation also
establishes subject basis version 1 from the snapshot/criterion binding. No
subject is created for an omitted verification criterion, and a capture-
version-0 target retains subject basis zero/null/null.

### Deterministic Artifact Manifest

Every post-v18 review target receives exactly one immutable
`artifact_manifest`, ID
`tg_artifact_manifest_<16-lowercase-hex>`, in the same target-setting
operation. No caller file-list option or additional Skill call exists.

For `git_snapshot`, taskgov compares the exact captured HEAD tree with the
stable stage-0 index. For `git_commit`, it compares a root commit with the
empty tree or another commit with its first parent. Each complete entry stores
only a safe project-relative POSIX path, `add`, `modify`, `delete`, or
`rename`, and nullable before/after Git mode and full object ID. It stores no
blob, patch, file content, raw diff, diff hunk, untracked or unstaged content,
or absolute path.

Rename classification is deterministic and does not use a similarity
heuristic. A delete and add become one rename only when their mode/object pair
is identical and occurs exactly once on each side. Ambiguous duplicates and
content-changing moves remain one delete plus one add. Mode-only and content
changes at one path are modifications. Submodule and symlink entries retain
only their Git mode and object ID.

The entry value matrix is exact:

| Kind | `old_path` / before mode/object | `new_path` / after mode/object | Additional invariant |
|---|---|---|---|
| `add` | all null | all non-null | none |
| `delete` | all non-null | all null | none |
| `modify` | all non-null | all non-null | old and new path are equal; before and after mode/object pairs differ |
| `rename` | all non-null | all non-null | paths differ; before and after mode/object pairs are equal |

After exact rename conversion, entries are sorted by `(old_path if non-null
else new_path)`, then `(new_path if non-null else old_path)`, then kind rank
`add,modify,delete,rename`, then before mode/object and after mode/object. Paths
and non-null text compare as unsigned UTF-8 bytes; null sorts before non-null.
Only after that sort are zero-based contiguous ordinals `0..entry_count-1`
assigned. No earlier tree, merge, delete, add, or pairing position survives as
an ordering input.

`diff_fingerprint` and `external_revision` produce one
`opaque_target` manifest with zero artifact entries and omission
`artifact_content_not_observed`; taskgov never fabricates Git evidence for
them. Every Git manifest is complete or absent: an unsafe/non-UTF-8 path, a
path over 240 UTF-8 bytes, more than 10,000 entries, more than 16,777,216
canonical manifest bytes, object loss, or capture drift rejects the target
write without a truncated record. Observation is shell-free, bounded,
network-disabled, and read-only. The manifest digest is SHA-256 over the
domain `taskgov-artifact-manifest-v1\0` and canonical content.

That canonical content is one no-extra-key object with exactly `state`,
`object_format`, `comparison_base`, `target_kind`, `target_value`,
`target_base_revision`, `target_generation`, `authority_snapshot_id`,
`acceptance_criterion_id`, `verification_criterion_id`, `omission_code`, and
`entries`. Nullable values are JSON null. Each entry has exactly `ordinal`,
`kind`, `old_path`, `new_path`, `before_mode`, `before_object_id`,
`after_mode`, and `after_object_id`; entries are in ordinal order after the
specified merge/rename pass. The random manifest ID and creation time are not
digest inputs. `object_format` is `sha1` or `sha256` for complete Git and null
for opaque targets. The JSON canonicalization is the fixed v1 encoding defined
below for Evidence JSON.

The current schema-v19 package uses all of these fixed failures, with the
manifest subset established by TG-M22.2 and the bundle-size failure activated
by TG-M22.3:

| Code | Message |
|---|---|
| `artifact_manifest_path_unsafe` | `artifact manifest contains an unsafe project path` |
| `artifact_manifest_too_large` | `artifact manifest exceeds the supported size` |
| `artifact_manifest_stale` | `Git material changed while capturing the artifact manifest` |
| `evidence_basis_stale` | `current evidence basis must be captured again` |
| `evidence_ledger_inconsistent` | `stored evidence ledger is inconsistent` |
| `evidence_bundle_too_large` | `completion evidence bundle exceeds the supported size` |

They expose no path, count, object ID, digest, target value, row content, Git
output, or exception. `evidence_basis_stale` is the schema-v18-or-later result
for any source-creating evidence write against a retained capture-version-0
target and is resolved by setting a fresh target; it never upgrades or repairs
that old target in place.

Failure routing is closed. Existing target precondition failures such as an
unborn HEAD, unmerged or unsupported index, invalid repository, or unresolved
commit retain their existing review/Git error. After those preconditions pass,
unsafe, non-UTF-8, control, traversal, absolute, over-240-byte, or otherwise
nonportable paths are `artifact_manifest_path_unsafe`; entry-count or canonical
byte overflow is `artifact_manifest_too_large`; missing objects, object-format
change, or any pre/post HEAD, index, tree, mode, object, or path drift is
`artifact_manifest_stale`. A structurally impossible persisted row, binding,
enum, digest, or ownership relation is `evidence_ledger_inconsistent`. No
manifest failure is silently reclassified, truncated, or exposed verbatim.

### Evidence References, Links, And Native Bundles

Schema v18 stores immutable versioned `evidence_reference` records for
the existing artifact manifest, Verification Receipt, Review Receipt,
Review Finding, and completion-evidence sources written after activation.
Each reference identifies its exact source row or closed completion value,
assurance class, producer class/version, Task, Contract, authority snapshot,
target tuple, criterion bindings, and a domain-separated digest. Completion
evidence additionally binds its exact completion cycle. It copies no raw
command, output, environment, prompt, diff, or arbitrary body.

The v1 source dispatch is exact; producer version is `1` throughout:

| Source kind/state | Assurance / producer | Immutable source projection | Current criterion relation |
|---|---|---|---|
| `artifact_manifest/complete_git` | `machine_observed/taskgov_git` | manifest ID/state/object format/comparison base/entry count/manifest digest/null omission | acceptance `completion_basis`, when acceptance exists |
| `artifact_manifest/opaque_target/diff_fingerprint` | `bound_attestation/trusted_caller` | manifest ID/state/target kind/manifest digest/`artifact_content_not_observed` | acceptance `completion_basis`, when acceptance exists |
| `artifact_manifest/opaque_target/external_revision` | `external_reference/external_system` | manifest ID/state/target kind/manifest digest/`artifact_content_not_observed` | acceptance `completion_basis`, when acceptance exists |
| `verification_receipt` | `bound_attestation/trusted_caller` | Receipt ID, subject-basis version, authority snapshot ID, verification criterion ID, result, duration, coverage, and creation time; no legacy caller label or internal compatibility value | verification `verification_attestation`, exactly once |
| `review_receipt` | `bound_attestation/trusted_caller` | Receipt ID, reviewer key, kind, verdict, summary, approval flag, creation time, and native v1/null `review_provenance`; nested v1 provenance retains its own assurance/producer, while migrated public v0 Receipts create no Reference | acceptance `review_assessment`, once for each selected qualifying Receipt when acceptance exists |
| `review_finding` | `bound_attestation/trusted_caller` | Finding ID, Receipt ID, severity, original summary, and creation time | acceptance `review_finding` only for the current target generation when acceptance exists |
| `completion_evidence/git_commit` | `machine_observed/taskgov_git` | cycle ID/time and the exact six-field completion-evidence value | acceptance `completion_basis`, when acceptance exists |
| `completion_evidence/external_revision` | `external_reference/external_system` | cycle ID/time and the exact six-field completion-evidence value | acceptance `completion_basis`, when acceptance exists |
| `completion_evidence/commit_not_required` | `bound_attestation/trusted_caller` | cycle ID/time and the exact six-field completion-evidence value | acceptance `completion_basis`, when acceptance exists |

`source_state` is exactly `complete_git|opaque_target` for a manifest, whose
bound target kind completes opaque dispatch,
`recorded` for each Receipt or Finding, and the exact completion kind for
completion evidence. `source_id` is the stored source-row ID, or the completion
cycle ID for completion evidence. No caller-provided subtype is accepted.

Every row in the table requires the exact project, Task, Contract revision,
authority snapshot ID, nullable acceptance/verification criterion IDs, and
four-field target tuple. The criterion IDs must equal that snapshot's links;
the Verification Receipt requires subject basis version 1 and a non-null
matching verification criterion, while
the other nullable criterion bindings preserve an honestly absent criterion.
Only completion evidence has a non-null cycle ID. A Review Finding copies the
binding of its Receipt. No other required/null combination is valid.

The evidence-reference digest is SHA-256 over
`taskgov-evidence-reference-v1\0` plus canonical JSON containing exactly the
source kind/state, the immutable source projection named above, all required
and nullable binding fields, assurance class, producer class, and producer
version. It excludes the random evidence-reference ID and reference creation
time. For a Review Finding it deliberately excludes `status`,
`resolution_summary`, and `resolved_at`; resolution neither mutates nor
supersedes the original reference. Schema v19 snapshots those mutable fields
separately at seal time. A manifest reference classifies the bounded manifest
observation, while its digest is only its structural seal; digest derivation
does not create a second claim or change the reference's assurance. Any link
copies the reference class and producer/version exactly. There is no
transitive assurance upgrade.

Schema v19 adds immutable `criterion_evidence_link` records with IDs
`tg_criterion_evidence_link_<16-lowercase-hex>`. A link binds one criterion to
one evidence reference with a closed relation:
`verification_attestation`, `review_assessment`, `review_finding`,
`completion_basis`, `derived_analysis`, or `runner_observation`. The link
copies the source assurance and producer metadata and cannot assert a stronger
class. M22 uses only the current verification, review, finding, and completion
relations; the last two producer-specific relations remain inactive.

Link construction is mechanical and closed. If acceptance exists, one current
manifest and one current completion-evidence reference receive
`completion_basis`; the exact qualifying Review Receipt IDs already selected
by the completion gate each receive `review_assessment`; and every Finding
from the current target generation receives `review_finding`. If verification
exists, its unique current pass/full Receipt receives
`verification_attestation`. If the corresponding criterion is absent, those
links are omitted and the source references remain bundle members. No other
M22 source-kind, criterion-kind, relation, or cardinality is valid.
`derived_analysis` may link `derived_analysis` to either criterion only after
separate canonical-ledger-writer authority beyond M23, and
`runner_observation` may later link `runner_observation` to verification only
after M24 authority; both writers remain disabled in M22.

Finding snapshot selection is also closed. It includes every Finding from the
current target generation, plus every high/medium Finding from an earlier
generation; completion already requires every included earlier high/medium
Finding to be resolved. Earlier low Findings are excluded. Rows are ordered by
target generation, creation time, then Finding ID. A snapshot contains exactly
`review_finding_id`, `review_receipt_id`, `target_generation`, `severity`,
`summary`, `status`, `resolution_summary`, `created_at`, `resolved_at`,
`evidence_reference_id`, `assurance_class`, `producer_class`,
`producer_version`, and `digest`. A post-v18 Finding copies its non-null
reference and `bound_attestation/trusted_caller/1`. A pre-v18 Finding has null
reference and exactly `legacy_unknown/legacy_migration/1`; this bundle-time
snapshot does not synthesize an evidence reference, authority binding, or
criterion link, and adds `historical_finding_reference_absent` once to bundle
omissions. Only current-generation referenced snapshots can have a criterion
link; earlier and legacy snapshots remain explicit unlinked gate history.

The snapshot digest is `sha256:` plus SHA-256 over
`taskgov-completion-bundle-finding-snapshot-v1\0` followed by canonical JSON
of exactly the preceding fields in that key set, excluding only `digest`.
Null is encoded as JSON null. Thus resolution is frozen without upgrading an
unknown historical origin.

Every native completion after schema-v19 activation inserts exactly one
`completion_evidence_bundle`, ID
`tg_completion_evidence_bundle_<16-lowercase-hex>`, in the same SQLite
transaction as its completion cycle, Task update, event, and source-generation
advance. The cycle stores evidence-basis version 1 and the exact bundle ID;
the bundle stores the exact cycle ID/ordinal, Task and Contract basis,
authority snapshot, criteria and links, target tuple, artifact manifest,
qualifying M21 Verification Receipt when required, deterministically selected
Review Receipts, the gate-relevant Review Finding state as an immutable
seal-time snapshot, completion evidence, assurance/producer metadata, closed
omission codes, seal time, and a canonical bundle digest.

Every completion-cycle insertion advances the Evidence projection source
generation exactly once in its own transaction, including the post-v19
partial `legacy_current_done` reopen bridge. A native completion advances it
with its bundle; the bridge inserts no bundle but makes its new
`legacy_unknown` index entry due. No other write advances this generation.

The bundle allow-list contains only the already-sanitized Task/Contract text,
criterion text, relative artifact identities, existing public Receipt and
Finding summaries, stable IDs, closed enums, counts, timestamps, and digests.
It contains no event log, raw SQLite row, database or absolute path, command
body or arguments, exit code, stdout/stderr, exception, environment, blob,
patch, prompt, chat, private reasoning, credential, or arbitrary extension
object. One canonical bundle is at most 16,777,216 UTF-8 bytes; completion
fails before its DB write rather than sealing a truncated bundle.

The complete v1 native-bundle omission vocabulary, in order, is exactly
`acceptance_criterion_absent`, `verification_criterion_absent`,
`artifact_content_not_observed`, and
`historical_finding_reference_absent`. The first
denotes revision-zero/unspecified Contract acceptance, the second denotes
exact Task verification whose trimmed text is empty, the third denotes an
opaque manifest, and the fourth denotes at least one selected pre-v18 Finding
with honestly unknown reference provenance. Empty qualifying-Finding arrays
and tier-derived Receipt counts are represented directly, not by omission
codes. Pre-v19 cycle absence is represented only by the index entry state
`legacy_unknown` and never by a native bundle.

Bundle/cycle ownership uses deferred same-Task foreign keys, and every
snapshot, criterion, manifest, reference, link, finding snapshot, and bundle
is update/delete protected. Reopen validates but never edits the prior cycle
or bundle. A later completion uses fresh current target, verification, review,
and completion evidence and inserts the next cycle and a new bundle.
Historical bundles never satisfy a current gate.

### Fixed Sanitized JSON Projection

The only generated Evidence JSON paths are resolver-owned and contained under
the ignored package-local state:

```text
state/current/evidence/index.json
state/current/evidence/bundles/<completion-evidence-bundle-id>.json
```

There is no public command, custom path, export option, JSON-to-DB import,
network endpoint, server, watcher, background worker, or Viewer Evidence UI.
SQLite remains canonical. A separate local report consumer may read the JSON
without opening SQLite but may never use it to write or strengthen ledger
state.

Bundle files and the index use canonical sorted-key compact UTF-8 JSON with one
terminal LF. Canonical JSON accepts only JSON null/boolean/string/integer,
array, and object values; rejects floats, nonfinite values, lone surrogates,
duplicate keys, and invalid Unicode; emits integers in shortest decimal form;
uses lowercase `true`, `false`, and `null`; escapes quote, backslash, and JSON
control characters using the short escape where defined and lowercase
`\u00xx` otherwise; does not escape slash or valid non-ASCII scalars; orders
object keys by Unicode code point; and uses no insignificant whitespace. Text
is never Unicode-normalized. Arrays use the explicit orders below.

A bundle file is exactly this no-extra-key envelope plus LF:

```text
{"bundle_digest":"sha256:<64-lowercase-hex>","format_version":1,"payload":<bundle-payload>}
```

The bundle payload has exactly these keys:

| Key | Exact value |
|---|---|
| `artifact_manifest` | object `{artifact_manifest_id,state,object_format,comparison_base,digest,omission_code,entries}`; nullable fields are JSON null; entries contain exactly `{ordinal,kind,old_path,new_path,before_mode,before_object_id,after_mode,after_object_id}` in ordinal order |
| `authority_snapshot` | object `{authority_snapshot_id,generation,digest}` |
| `bundle_id` / `bundle_version` | bundle ID string / integer `1` |
| `completion_cycle_id` / `cycle_ordinal` / `sealed_at` | strings except positive integer ordinal; stored UTC timestamp |
| `completion_evidence` | exactly `{kind,revision,reason,external_revision_approved,completion_commit_required,completion_commit_hash}` with the two flags as integers `0|1` |
| `contract` | exactly `{revision,specified,scope,acceptance,constraints,authority_ref}` with boolean `specified` |
| `criteria` | objects `{criterion_id,kind,text,digest}` ordered acceptance then verification; absent criteria have no row |
| `criterion_links` | objects `{criterion_evidence_link_id,criterion_id,evidence_reference_id,relation,assurance_class,producer_class,producer_version}` ordered by criterion kind `acceptance,verification`, criterion ID, relation order `verification_attestation,review_assessment,review_finding,completion_basis,derived_analysis,runner_observation`, evidence-reference ID, then link ID |
| `evidence_references` | objects `{evidence_reference_id,source_kind,source_state,source_id,assurance_class,producer_class,producer_version,contract_revision,authority_snapshot_id,acceptance_criterion_id,verification_criterion_id,target_kind,target_value,target_base_revision,target_generation,completion_cycle_id,digest}` ordered by source-kind `artifact_manifest,verification_receipt,review_receipt,review_finding,completion_evidence,derived_analysis,runner_observation`, source ID, then reference ID; nullable bindings are JSON null |
| `finding_snapshots` | the exact snapshot fields defined above, in the defined Finding order |
| `omissions` | unique strings in the fixed omission order |
| `project_id` | project ID string |
| `review_receipts` | selected objects `{review_receipt_id,reviewer_key,receipt_kind,verdict,summary,user_approved,created_at,review_provenance}` in the exact `qualifying_receipt_ids` gate-basis order; `user_approved` is integer `0|1`, and native provenance is v1 or null because migrated v0 Receipts have no Reference and cannot enter a native bundle |
| `source_schema_version` | integer `19` |
| `target` | exactly `{kind,value,base_revision,generation,capture_version}`, with nullable base revision and positive integers |
| `task` | exactly `{task_id,title,description,review_tier,verification}` |
| `verification_receipt` | JSON null when verification criterion is absent; otherwise exactly `{verification_receipt_id,verification_subject,result,duration_ms,scope_coverage,created_at}`, where `verification_subject` is exactly `{basis_version,kind,authority_snapshot_id,verification_criterion_id}` with basis `1` and kind `task_verification_criterion`; the legacy-label key and internal compatibility value are absent |

Every textual tie-breaker in those array rules uses unsigned UTF-8 byte order;
integer generations/ordinals compare numerically. Stored timestamps use the
existing canonical UTC form, so the Finding timestamp position is likewise
deterministic. No database incidental row order is observable.

The stored DB bundle digest, envelope `bundle_digest`, and native index-entry
`bundle_digest` are byte-for-byte equal. They are `sha256:` plus SHA-256 over
`taskgov-completion-evidence-bundle-v1\0` followed by the canonical UTF-8 bytes
of `payload`, without LF. The native index-entry `file_digest` is instead
`sha256:` plus SHA-256 over the complete bundle-file bytes including envelope
and terminal LF.

An index file is exactly
`{"format_version":1,"index_digest":"sha256:<64-lowercase-hex>","payload":<index-payload>}`
plus LF. Its payload has exactly `source_schema_version` (integer `19`),
`project_id`, `projection_generation` (nonnegative integer), `bundle_count`,
`legacy_count`, and `entries`. Each entry has exactly `task_id`,
`completion_cycle_id`, `cycle_ordinal`, `bundle_state`, `bundle_id`,
`bundle_file`, `bundle_digest`, `file_digest`, and `sealed_at`. Native state is
`native` and all five nullable bundle/file identity and seal-time values are
strings; legacy state is `legacy_unknown` and those five values are null.
Entries are ordered by Task
ID, cycle ordinal, then cycle ID. Counts equal the corresponding entry states.
`index_digest` is `sha256:` plus SHA-256 over
`taskgov-evidence-index-v1\0` followed by canonical index-payload bytes without
LF; the projection-state digest equals it. Stored seal/publication-basis times
are used, so the same validated DB generation produces byte-identical files.

The fixed native `bundle_file` is
`bundles/<completion-evidence-bundle-id>.json`; it is safe relative POSIX text.
The index has one entry for every completion cycle, at most 100,000 entries
and 67,108,864 UTF-8 bytes, and is never truncated.

Publication captures one coherent DB generation, validates every selected
row, writes and flushes immutable bundle files first, then atomically replaces
`index.json` last. The index is the filesystem commit point. Consumers ignore
unreferenced files and reject a referenced file with the wrong identity,
version, project, or digest. Taskgov additionally compares the index
generation with the canonical DB; missing, behind, ahead, unknown-version,
wrong-project, or digest-inconsistent projection is due or repair-required.
A standalone consumer can prove only the self-consistency and declared
generation of its files, not freshness against an inaccessible DB.

The DB commit remains successful if publication is contended or fails. The
last-good index remains, projection state remains due, and at most one fixed
continuation warning is appended:

| Code | Message |
|---|---|
| `evidence_projection_deferred` | `Evidence projection refresh was deferred; task result is unchanged` |
| `evidence_projection_failed` | `Evidence projection refresh did not complete; task result is unchanged` |

Post-commit order is Evidence projection, Viewer refresh, then due backup;
each stage is independent and bounded. Setup is the sole explicit repair and
adds `evidence_projection_publish` to its ordered write vocabulary plus an
`evidence_status` of `not_present`, `current`, `published`, or
`repair_required`. Doctor reports only stored Evidence-projection
generation/outcome facts in a maintenance `evidence` object with exactly
`code`, `due`, `source_generation`, `published_generation`,
`last_success_at`, and `last_outcome`; it never repairs. These public shape
changes are active in the synchronized TG-M22.3 contracts.

### Legacy, M21, And Future Producer Boundaries

Migration to v18 creates no artifact manifest or evidence reference for an
old target or historical Receipt. Migration to v19 gives every existing
completion cycle evidence-basis version 0 and a null bundle ID, inserts no
bundle, and projects the absence as `legacy_unknown`. The existing partial
legacy reopen bridge remains version 0/null and advances projection generation
when it inserts a cycle. Neither migration parses prose, events, Git history,
M20 observations, or review summaries to invent evidence. A later bundle may
include a selected pre-v18 Finding only as the nullable-reference
`legacy_unknown/legacy_migration/1` snapshot defined above. Recompletion after
reopen uses a new version-1 bundle while the old cycle remains unchanged and
unbundled.

M21 Verification Receipt uniqueness, exact-current rules, failure/timeout/
partial recovery, completion-cycle link, and caller-attested result/coverage
meaning remain unchanged. TG-M21.4 changes only the schema-v18 caller-label/
subject boundary defined above. A native bundle requires the exact subject-
basis-one Receipt ID and target tuple; it never admits a subject-basis-zero
Receipt or legacy caller label, aggregates partial Receipts, retries a
generation, or converts a Receipt to `machine_observed`.

M23 may later create separate append-only `llm_derived` analysis revisions
outside the canonical ledger. A native revision cites exact bundle/criterion/
link IDs and digests; a legacy revision cites only its exact index/cycle
binding. Both remain outside the sealed bundle and every current gate and
create no Evidence Reference or criterion link. M24 may later add a versioned
`verification_runner` producer and a new tagged verification basis. Shadow
Runner evidence remains gate-ineligible until that later activation; gate
integration must preserve the M21 caller Receipt as an explicit fallback and
must not rewrite any existing class, cycle, bundle, or JSON digest.

### Current And Conditional Activation Units

The approved sequence is:

1. **Accepted TG-M22.2 / schema v18:** provides authority snapshots, whole-field
   criteria, subject-basis fields, label-free Receipt input, the versioned
   Task-show subject projection, evidence references, deterministic Git
   manifests, target capture bindings, native-cycle subject/completion
   references, capture-version-0 write rejection, the 1,000-character
   durable/read boundary with 500-character public Task ingress, migration
   validation, and
   Viewer snapshot-v4 compatibility through v18; synchronize the durable
   `AGENTS.md` Receipt-retention guardrail and perform the exact self-host
   reentry above. No bundle or Evidence JSON is written.
2. **Accepted TG-M21.5 / schema v18:** raises only explicit public Task add/edit
   verification admission from 500 to 1,000 characters after subject
   activation and before bundles, with no stored-reader, migration, subject,
   public-leaf, or normal-loop-call change.
3. **Current TG-M22.3 / schema v19:** activates version-1 native bundles and criterion
   links, projection generation/state, fixed JSON publication, setup repair,
   subject-only Receipt projection, post-commit warnings, and Viewer snapshot-
   v4 source compatibility through v19. The public command inventory and
   normal Skill call count remain 21 and ten-or-eleven.
4. **Inactive TG-M22.4:** later accept the exact v18/v19 sequence through the approved legacy
   row/cycle, subject, 1,000-character, migration, lifecycle, Git, privacy,
   repair, consumer, package, release, full-offline, forward-test, and two-
   review gates, applying only bounded corrections within this design.

TG-M22.2 and TG-M21.5 are accepted coherent predecessor boundaries. Current
TG-M22.3 is the supported runtime/formal-doc/Skill/package/Viewer boundary.
TG-M22 authorizes no
release, push, tag, external model, network, report narrative, target-project
mutation, Analyzer, or Runner operation.

<a id="tg-m22-conditional-design"></a>

## Current TG-M22.3 And Conditional Later Implementation Detail

## Current TG-M21.4 Verification Subject Design Detail

TG-M21.4 defines the schema-v18 vertical transition implemented by current
TG-M22.2 before any Evidence Bundle exists. The active design remains the
implementation-structure owner.

### Additive Storage And Legacy Preservation

`verification_receipts.py` continues to own Receipt gate semantics and public
projection. At schema v18 it replaces caller label normalization with one
subject builder that consumes only the locked target's capture version,
authority snapshot ID, verification criterion ID, current Contract revision,
and complete target tuple. This compound structural binding is the
`verification_subject`; it is not another free-form record and therefore adds
no table, random ID, caller field, or command.

`storage.py` adds these columns independently to `verification_receipts` and
`task_completion_cycles`:

```text
verification_subject_basis_version INTEGER NOT NULL DEFAULT 0
  CHECK (verification_subject_basis_version IN (0, 1))
subject_authority_snapshot_id TEXT NULL
  REFERENCES authority_snapshots(authority_snapshot_id)
subject_verification_criterion_id TEXT NULL
  REFERENCES contract_criteria(criterion_id)
```

The authority/criterion tables are created first, then the additions use
`ALTER TABLE ... ADD COLUMN` plus version-aware indexes and insert guards.
The scalar foreign keys prove existence. Insert triggers and the shared read
validator enforce the closed version/null matrix, snapshot-to-verification-
criterion membership, project/Task ownership, and the exact locked target
binding; the cycle trigger additionally permits basis zero only for the
existing exact partial-reopen bridge. They do not rebuild either table or
execute an `UPDATE` over a v17 business row. Existing update/delete denial
triggers protect the new columns automatically. Every old Receipt and cycle
consequently reads as subject basis 0/null/null while every original column,
ID, label, target, timestamp, Receipt link, cycle relationship, and ordering
remains unchanged.

The existing cycle `verification_basis_version` remains independent. A v17
native cycle keeps verification basis 1 and receives subject basis 0; the
legacy partial bridge keeps verification basis 0 and subject basis 0. Every
new schema-v18 native cycle uses subject basis 1, with matching non-null
snapshot/criterion IDs and the same subject-v1 Receipt when verification is
nonempty, or both IDs and the Receipt link null when it is trimmed-empty. No
new subject-basis-zero Receipt or native cycle is permitted.

The physical v17 `command_label TEXT NOT NULL` column remains to avoid a table
rewrite. A new subject-v1 writer stores only the exact internal constant
`taskgov-owned-verification-subject-v1`; it never accepts that value from a
caller. Version-aware row validation applies the legacy label predicate only
to basis-zero rows and requires the constant only for basis one. Public
formatters and Evidence digest builders never read that constant.

Migration fingerprints every pre-existing Receipt/cycle projection over its
exact v17 columns before the additive DDL and proves that projection and count
unchanged afterward; it does not compare `SELECT *` or database-file bytes.
The M22.1-approved current-basis authority snapshots and criteria are still
created, but the subject addition synthesizes no subject, Evidence Reference,
target binding, or cycle relation for an old target, Receipt, or cycle. It then
validates the zero/one null matrix, ownership triggers, quick check, and foreign
keys before recording migration 18. Reentry performs validation only. Injected
failures before the history row roll back the DDL and leave v17 usable.

### Target, CLI, Projection, And Gate Integration

The schema-v18 target transaction already planned by M22 binds capture version
1, authority snapshot, acceptance/verification criteria, and artifact
manifest. That tuple is the sole source of a subject-v1 binding. An omitted
verification criterion yields no subject. Capture-version-zero targets keep
null bindings and return `evidence_basis_stale` for active Receipt or
completion writes.

`cli.py` removes `--command-label` from the existing leaf and passes only
result, duration, coverage, and expected target generation. No subject option
or compatibility alias is added. The successful text formatter remains
unchanged. The parser/help, concise Skill, and workflow reference switch in
the same schema-v18 package commit so an active instruction can never request
an option the runtime no longer accepts.

The schema-v18 Receipt allow-list replaces `command_label` with exactly one
`verification_subject` object. Its keys are `basis_version`, `kind`,
`authority_snapshot_id`, `verification_criterion_id`, and
`legacy_caller_label`. A basis-zero row projects its preserved label and null
IDs as `legacy_caller_label`; a basis-one row projects the two IDs, null legacy
label, and `task_verification_criterion`. The parent Receipt's existing
Contract revision and source-revision tuple complete the binding.

`task show.verification_evidence` adds `current_verification_subject`, derived
from the current capture binding, and uses the same versioned object for recent
rows. With nonempty verification, active capture-zero work reports
`evidence_basis_stale` before missing or blocking Receipt status. Empty
verification preserves the existing satisfied Task-show verification gate and
null subject, although review/completion source writes still reject capture
zero. A done subject-basis-zero cycle is instead validated under the exact v17
linked-Receipt rules and remains readable. Reopen always requires a fresh
capture-version-1 target; only a resulting nonempty verification requires a
subject-v1 Receipt, while trimmed-empty verification creates neither and later
closes with the defined basis-v1/null/null cycle.

The Task-show gate enum adds `evidence_basis_stale` only to that nonempty branch.
The `task complete --check` readiness-code allow-list adds it after target
absence for every capture-zero target and before verification/review evidence,
because completion creates ledger sources even when verification is empty.
Completion-check formatting keeps the generic fixed suggestion
`resolve evidence_basis_stale before completing the task`; no other ordering,
key, or formatter changes.

Result, duration, coverage, expected-generation concurrency, expectation
digest, one-Receipt-per-generation, `pass/full`, and explicit completion
attestation remain owned by the existing evaluator. Receipt assurance remains
`bound_attestation/trusted_caller/1`. The subject only proves taskgov's
deterministic choice of an existing authority binding; it does not upgrade the
reported run or authenticate a caller, process, environment, or result.

Focused schema-v18 tests do not accumulate the new matrix in the already large
`test_verification_receipts.py`. That owner receives only edits to its existing
result/gate compatibility cases. A dedicated
`test_m22_verification_subjects.py` owns subject migration/reentry/corruption,
CLI/Task-show shape, stale/fresh binding, recovery, and done/reopen cases; M22
ledger integration remains in its own focused module. Shared builders live in
a non-discovered test-support module instead of being copied. Every new test
module is explicitly assigned to the integration lane in `tools/test_lanes.py`.
Together the focused tests cover exact old-column fingerprints, label-option
removal, target/Contract/criterion drift, Viewer validation, privacy, parser/
help, 21 leaves, and the ten-or-eleven-call limit.

For this repository's self-hosted M22.2 run, schema-v18 setup leaves all
pre-activation target, Receipt, and review rows at capture version zero. The
normal workflow must retarget the exact post-migration material, rerun checks,
record a subject-v1 Receipt, prepare review once, and collect fresh Tier 2
reviews. No migration repair, compatibility alias, or completion bypass
qualifies the old evidence.

### Verification Capacity Ownership And Same-schema Compatibility

TG-M21.4A, Task `tg_task_95c5e968c8fe7e4b`, separates caller admission from
durable/read validation for the accepted schema-v18 boundary. Accepted M22.2 retained the
500-character public boundary while introducing the schema-aware stored limit.
Accepted M21.5 gives caller admission its dedicated owner without changing the
stored owner:

```text
TASK_VERIFICATION_INPUT_LIMIT = 1000
verification_stored_limit(schema <= 17) = 500
verification_stored_limit(schema >= 18) = 1000
```

The input constant is used only when a caller explicitly supplies Task
verification to `task add` or `task edit`. The schema-aware stored limit is
used for database rows and every internal derivative. In particular,
`verification_receipts.py` current-basis, Task-show, Receipt-add, and
completion reads and `review_packet.py` must use stored validation rather than
the caller-input helper. Untouched verification bytes on metadata, Contract,
target, review, lifecycle, completion, or reopen writes are likewise stored
state; those writes must never validate them against the narrower input cap.
An explicit 501-1,000-character value at M22.2 is rejected even when equal to
stored bytes, using the existing privacy-before-length input ordering and
before opening the transaction.

At M22.2, `storage.py`, authority-snapshot and criterion builders, digest and
subject builders, Receipt/review repositories, completion planning and cycle
validation, history projection, Task show, Review Packet, Viewer batching,
setup/migration reentry, backup, recovery, and future Bundle inputs all accept
and preserve 501-1,000 characters. SQLite constraints or triggers may reject
values over 1,000 but may not encode a 500-character schema-v18 stored cap.
The shared stored-text helper continues the ordinary privacy check and rejects
over-1,000 or structurally inconsistent state with the owning fixed sanitized
error before any mutation; it never truncates, normalizes, or rewrites bytes.
Aggregate output caps keep their existing behavior and are not stored-field
validators.

Migration and recovery choose the stored limit from the source schema before
any copy, DDL, history row, or canonical publication. Schema v17 permits 500;
schema v18 and later supported sources permit 1,000. Thus invalid 501-1,000
bytes in a v17 source are not laundered by migration. Backup discovery applies
the specification's TG-M21.4B matrix per candidate: only stored Task-
verification privacy/capacity rejection may expose an older eligible candidate;
structural failure remains set-fatal and retains its specific resolver result
where applicable, otherwise `project_state_unreadable`. A structurally coherent
set with no eligible candidate solely because every current-binding candidate
is locally rejected returns `setup_restore_failed` without a canonical
database; post-plan drift or restore/publication failure retains that separate
restore boundary. Stored overflow or privacy failure in an authoritative
primary or another non-selection context maps to the owning sanitized stored-
context inconsistency result, not caller `invalid_argument` or
`privacy_rejected`, with no partial business, evidence, generation, or artifact
write.

Accepted M21.5 changed only `TASK_VERIFICATION_INPUT_LIMIT` from 500 to 1,000 and the
directly coupled public add/edit help, formal wording, package metadata, and
boundary tests. It must not change the schema-aware stored limit, DDL,
migration history, snapshot/criterion representation, Receipt/review gate,
Viewer reader, setup/recovery logic, or another field limit. Both M22.2 and
M21.5 therefore remain v0.12.0/schema v18 with no capability marker.

M22.2 tests seed valid schema-v18 501- and 1,000-character Tasks through an
internal storage fixture while proving public add/edit still reject 501,
exercise every stored/read/internal path above, reject 1,001, and inject
failures to prove atomic no-partial-write behavior. This matrix includes list,
default and compact current/next, doctor, and Task-loading checkpoint, Handoff,
and Effort paths even where verification is not projected. ASCII and
multi-byte 1,000-code-point fixtures remain distinct from unchanged UTF-8
aggregate output caps.

The exact M22.2 compatibility baseline is completion commit `b954372b30ff3a0b08fca9b804f78b08004825d3`
with `task-governance-tool` package tree `e2bbd5a0e34859680118b25565daa15ca63b8c05`. M21.5 tests prove public
500/501/1,000/1,001 boundaries, materialize that exact complete M22.2 package
offline into an isolated temporary project, and run it against a database
produced by the complete M21.5 writer. A package assembled from mixed
revisions or by changing only the input constant is invalid. The M22.2
baseline must safely list, show, select, edit without verification, revise a
Contract, set up, project, retarget, record Receipt/review/finding evidence,
complete, reopen, back up, and recover that post-M21.5 database. M22.3 tests
consume its 1,000-character criterion without changing capacity; M22.4
repeats the three-state and cross-revision matrix.

### Bounded Downstream Split

Accepted TG-M22.2 owns this subject activation together with the schema-v18
criterion and target foundation plus the 1,000-character durable/read boundary.
Accepted TG-M21.5 retained the same schema/package identity and changed only the
public Task add/edit admission to 1,000.
Current TG-M22.3 owns subject-only bundle/JSON projection, and TG-M22.4 owns integrated
legacy/current acceptance. No unit adds a public leaf, normal-loop call,
Runner, analyzer, or raw retained content.

## Current TG-M22.3 Evidence Ledger And Bundle Design

TG-M22.1 plus the TG-M21.4/TG-M21.4A corrections define the accepted TG-M22.2
schema-v18 subject/capture foundation, accepted TG-M21.5 admission slice, and
the current TG-M22.3 projection slice. The current schema-v19 package provides
1,000-character durable/read and public-ingress capacity, completion Bundles,
and generated JSON. Viewer v5-v19 source range, the Skill call graph, and the
public 21-leaf parser are synchronized without adding a UI or command.

TG-M22.2 advances the unpublished package candidate to v0.12.0 with schema
v18. TG-M21.5 retained that identity; TG-M22.3 advances schema to
v19. This development sequence makes no published-version, tag, Release,
remote-commit, or artifact-identity claim.

### Current Capture And Projection Module Ownership

The current schema-v19 package uses three narrow modules introduced by
TG-M22.2 and one projection module activated by TG-M22.3:

- `review_provenance.py` owns the closed enum/matrix, existing-leaf input,
  canonical public union, and provenance digest; it owns no SQLite access.
- `evidence_ledger.py` owns assurance/producer validation, authority-basis
  canonicalization, whole-field criteria, evidence references, and current
  canonical public allow-lists. Active link, Bundle, and omission assembly is
  delegated to `evidence_projection.py`; SQLite persistence stays repository-owned.
- `artifact_manifest.py` owns bounded shell-free Git tree/index observation,
  exact artifact entry normalization, deterministic rename pairing, and
  canonical manifest digests. It reuses the safe process runner and stable
  snapshot primitives from `git_snapshot.py` without routing complete
  manifests through the truncated Review Packet projection.
- `evidence_projection.py` owns coherent ledger
  capture, canonical Bundle/index JSON, digest validation, index-last atomic
  publication, generation comparison, last-good preservation, and repair.

`storage.py` remains the only SQLite owner. `tasks.py` and `contracts.py`
invoke authority capture inside their existing savepoints;
`verification_receipts.py` derives subject-v1 bindings and it and `reviews.py`
create typed evidence references inside their own existing writes; the target
service creates one manifest and subject-capable binding atomically;
the completion workflow passes a fully prepared bundle basis into the existing
native-cycle savepoint. Feature modules never open raw SQLite connections.

`state_resolver.py` and `state_paths.py` are the sole owners of the
fixed Evidence directory, index, bundle directory, and lock. `state_transition.py`
recognizes only those generated files in a bounded setup stage.
`maintenance.py` keeps `MutationOutcome(state_changed, viewer_relevant)` and
may retry due Evidence projection after every changed mutation; only a cycle
insert advances its generation. `setup.py` owns
explicit repair. `cli.py` adds only the approved setup/doctor fields and
warnings; it adds no command or evidence-export parser branch.

### Versioned Assurance And Producer Model

The storage representation uses a closed `assurance_class` enum:

```text
machine_observed bound_attestation deterministically_derived
external_reference legacy_unknown llm_derived
```

Producer identity is stored independently as `producer_class` plus a positive
`producer_version`:

```text
taskgov_core taskgov_git trusted_caller legacy_migration external_system
batch_analyzer verification_runner
```

Schema v18 admits the reserved future values structurally so a later migration
does not need to reinterpret old records. Canonical repository writers allow
only branches explicitly authorized for canonical storage; M23 report
activation is not such authority. A producer class is not a user, reviewer,
process, model, executable, machine, signature, or trust proof. Derived records
preserve source IDs and classes; the validator rejects any assurance upgrade.

The current mapping is implemented as the fixed source/state dispatch table in
the specification, not caller input. A complete Git manifest reference is
`machine_observed/taskgov_git/1`; its canonical digest is a structural seal,
not a separately classed claim. An opaque diff-fingerprint manifest is
`bound_attestation/trusted_caller/1`; an opaque external-revision manifest is
`external_reference/external_system/1`. M21 Receipt and review inputs are
`bound_attestation/trusted_caller/1`. Completion evidence dispatches by its
closed kind to Git-observed, external-reference, or caller-attested exactly as
specified. Migration-only absence is legacy unknown. M23's separate report-
artifact producer and the M24 Runner branch remain unreachable until their own
schema/service activations. M23 activation leaves the canonical
`derived_analysis` writer unreachable pending separate post-M23 authority.

### Schema V18 Capture Foundation

Migration 18 is named `evidence_ledger_capture`. It adds:

```text
authority_snapshots
contract_criteria
authority_snapshot_criteria
review_receipt_provenance
review_receipt_provenance_codes
artifact_manifests
artifact_manifest_entries
evidence_references
```

and the minimum current-pointer, target-binding, Review-provenance
version/ID, and TG-M21.4 subject columns needed to connect them to Tasks,
Receipts, and cycles. The provenance relation and subject columns are the
additive sets defined above; neither accepts arbitrary JSON or caller-owned
assurance. All record IDs use their
specification prefixes plus 16 random
lowercase hexadecimal characters. Every owned table includes project and Task
keys, composite foreign keys, deterministic uniqueness, canonical timestamp
and digest checks, and update/delete denial triggers.

An authority snapshot stores its Task-local positive generation, Task title
and description, review tier, exact verification text and digest, Contract
revision and exact scope/acceptance/constraints/authority reference, explicit
specified/unspecified state, canonical basis digest, producer metadata, and
creation time. The digest input is canonical sorted-key compact UTF-8 JSON
under `taskgov-authority-snapshot-v1\0`; it excludes random row ID and creation
time so semantically identical input is detectable before insert. The Task
stores the current snapshot ID/generation.

Task add inserts Task, initial Contract when supplied, criteria, snapshot, and
the existing event in one savepoint. An authority-bearing edit first computes
the complete resulting Task/Contract basis, reuses a same-content criterion,
allocates a new snapshot only when that basis changes, and applies the current
target invalidation in the same transaction. Concurrent writers serialize on
the Task and snapshot generation; a replay produces no extra row or event.

Criteria are immutable whole values, never parsed sections. The exact unique
basis is Task, kind (`acceptance|verification`), and the SHA-256 of
`taskgov-contract-criterion-v1\0`, kind, a NUL separator, and exact normalized
UTF-8 text. Snapshot-to-criterion links record which acceptance and
verification values applied. Contract revision zero and verification whose
trimmed text is empty have no invented criterion and use fixed omission state
in the snapshot/bundle basis. The snapshot and its digest still retain the
exact verification bytes, including legacy whitespace; criterion selection
never trims or rewrites stored authority.

Migration 18 creates one `legacy_migration` snapshot of the exact current
stored basis for each Task and reusable criteria only for actual stored
values. It does not claim or reconstruct an earlier basis. Existing nonempty
review targets retain their current tuple but get capture version 0 and null
snapshot/manifest bindings; existing Receipts, findings, cycles, and events get
no synthesized evidence reference or subject. Existing Receipts and cycles
receive only the additive subject-basis-zero/null/null defaults without row
updates or a table rebuild. A new target set is capture version 1, binds the
current snapshot/criteria, and is required before a schema-v19 native bundle
can close.

Schema-v18 Task, authority-snapshot, criterion, and reentry validation use the
TG-M21.4A schema-aware 1,000-character stored limit. Accepted M21.5 independently
caps public Task add/edit input at 1,000; no capture object, trigger, migration
validator, or read projection may substitute caller-input validation for the
source-schema-aware stored limit.

The v18 service guard treats capture version 0 as read-only lineage. It runs
inside the locked basis check for Verification Receipt add, Review Receipt
add, Review Finding add, and completion, returning `evidence_basis_stale`
before any source, event, or maintenance write. Review preparation and
existing-Finding resolution do not create a reference and remain allowed.
For a nonempty verification criterion, Receipt add copies the target's exact
snapshot and criterion IDs as subject basis one, writes only the fixed internal
compatibility value to the legacy label column, and emits the versioned public
subject. It accepts no label or subject argument.
Every new native completion at v18 inserts its closed completion-evidence
reference and subject-basis-one cycle together with deferred ownership in the
existing completion savepoint; it creates no bundle or Evidence JSON.

An `evidence_reference` stores one closed source kind/state and stable source
ID or canonical closed completion value, assurance, producer/version,
ownership, exact Contract/snapshot/nullable-criterion/four-field-target
binding, nullable cycle ID, source-content digest, and time. The repository
uses one constant dispatch keyed by source kind/state and, for an opaque
manifest, target kind; it materializes the exact required/null matrix and
immutable source projections in the specification. It accepts no assurance,
producer, binding, or relation from a caller. Current source kinds are
`artifact_manifest`, `verification_receipt`,
`review_receipt`, `review_finding`, and `completion_evidence`; reserved kinds
`derived_analysis` and `runner_observation` remain writer-disabled.

The stored state is `complete_git|opaque_target` for manifests, `recorded` for
Receipt/Finding rows, and the exact closed completion kind for completion
evidence. The source ID is the source-row ID or completion-cycle ID. These are
repository constants, not caller fields.

Reference creation shares the source write transaction. Its digest helper
serializes exactly source kind/state, the specification source projection,
all binding fields, and assurance/producer/version beneath
`taskgov-evidence-reference-v1\0`; it excludes random reference ID and
creation time. The Review Finding projection is only Finding ID, Receipt ID,
severity, original summary, and creation time. Status, resolution summary, and
resolution time are excluded, so later resolution cannot invalidate, mutate,
or supersede that reference. Schema v19 captures the exact mutable state in a
separate immutable seal-time snapshot. Validators recompute the dispatch and
digest from source rows and fail on every class upgrade or invalid null.

### Exact Git Manifest Capture

`artifact_manifest.py` observes complete tree leaves, not Review Packet path
summaries. It uses the existing safe Git environment, argument arrays, null
stdin, bounded timeouts, disabled optional locks/fsmonitor/lazy fetch/external
diff/text conversion, and pre/post stability observations. It never invokes a
shell, hook, checkout, index write, object fetch, network, or caller command.

For a staged target it derives the base tree from the exact HEAD and the target
tree from the exact stage-0 index already accepted by Git snapshot capture.
For a commit target it resolves the exact commit and its first parent, or the
empty tree for a root. Tree leaves normalize to:

```text
relative_posix_path mode full_object_id
```

The merge of bytewise path-ordered before/after leaves produces add, delete,
and modify entries. A second deterministic pass groups delete/add pairs by
exact mode and object ID; only a one-delete/one-add group becomes rename.
Ambiguous duplicate blobs and content-changing moves remain delete plus add.
This classification is independent of Git rename heuristics and configuration.

Each stored entry enforces the specification's four-row required/null matrix.
After rename conversion, one pure sorter uses the exact old-or-new primary,
new-or-old secondary, kind rank, mode, and object-ID tuple, with null first and
unsigned UTF-8 byte comparison, then assigns contiguous zero-based ordinals.
No SQLite, Git, grouping, or input iteration order reaches the digest. Paths
must be safe UTF-8 relative POSIX names, at most 240 bytes, with no
absolute, traversal, NUL, control, backslash, or platform-escape form. At most
10,000 entries and 16 MiB of canonical manifest JSON are accepted. Overflow,
unsafe path, missing object, or pre/post drift aborts target setting without a
manifest, Task target, event, or maintenance effect. No truncation is valid.

A fingerprint target stores one zero-entry `opaque_target` manifest with
`bound_attestation/trusted_caller/1`; an external target stores the same state
with `external_reference/external_system/1`. Both use omission
`artifact_content_not_observed`. A Git target stores `complete_git`, the
comparison base, object format, complete target tuple, authority snapshot,
entry count, and SHA-256 over
`taskgov-artifact-manifest-v1\0` plus canonical content. The target row stores
capture version 1, authority snapshot ID, acceptance/verification criterion
IDs, and manifest ID. All are revalidated in the short writer after Git closes.

The capture service preserves existing precondition errors through initial
repository/HEAD/index/commit validation. Its own result mapping is exhaustive:
path decoding or safety failures use `artifact_manifest_path_unsafe`; count or
canonical-size overflow uses `artifact_manifest_too_large`; object loss,
object-format change, and pre/post observation drift use
`artifact_manifest_stale`. Repository validation of stored manifests uses
`evidence_ledger_inconsistent`. These branches return only the fixed sanitized
messages and never partial entries or Git output.

### Schema V19 Bundle Foundation

Migration 19 is named `completion_evidence_bundles`. It adds:

```text
criterion_evidence_links
completion_evidence_bundles
completion_bundle_members
completion_bundle_finding_snapshots
evidence_projection_state
```

and internal `evidence_basis_version` plus nullable
`completion_evidence_bundle_id` to `task_completion_cycles`. Existing cycles
receive version 0/null. The only post-v19 version-0 insert remains the exact
partial legacy reopen bridge. A normal native cycle requires version 1 and a
same-project/same-Task bundle. Deferred composite foreign keys allow cycle and
bundle rows to be inserted together without an update to either immutable row.

Criterion links are append-only and unique by criterion, evidence reference,
and closed relation. One repository constant enforces the specification
matrix: acceptance receives current manifest and completion evidence as
`completion_basis`, gate-selected Review Receipts as `review_assessment`, and
current-generation Findings as `review_finding`; verification receives only
the unique current qualifying Receipt as `verification_attestation`. When an
acceptance criterion is absent, otherwise required manifest, review, Finding,
or completion references remain members without an acceptance link. When the
verification criterion is absent, no Verification Receipt, reference, or link
is invented. All other M22 pairings and cardinalities are rejected. Reserved
analyzer/Runner pairings remain feature-disabled.

`completion_bundle_members` freezes the exact link and source-reference set
used by one bundle. Finding selection takes all current-generation Findings
plus all earlier high/medium Findings, excludes earlier low Findings, and
orders by generation, creation time, and ID. A post-v18 snapshot copies its
reference and bound-attestation provenance. A selected pre-v18 Finding stores
a null reference plus `legacy_unknown/legacy_migration/1`, creates no
reference/link, and sets the fixed historical-Finding omission. The snapshot
digest helper uses
`taskgov-completion-bundle-finding-snapshot-v1\0` plus canonical JSON of the
exact specification fields excluding only the digest. Only a referenced
current-generation snapshot can link to current acceptance; older resolved
high/medium snapshots remain unlinked gate history. No query later joins
mutable Finding state to rewrite a sealed row or JSON file.

Before `BEGIN IMMEDIATE`, the completion workflow prepares the exact Git
completion plan and canonical JSON-shaped bundle basis without writing a file.
Under the existing short writer it rereads Task, Contract, current authority
snapshot, criterion bindings, target/capture version, manifest, current M21
nullable subject-v1 Receipt, review receipts/findings, and completion proposal;
reevaluates all
current gates; selects the same deterministic nullable Receipt basis as the
cycle; then computes the complete bundle payload and size. The savepoint
inserts links,
finding snapshots, bundle, cycle, Task update, completion event, and Evidence
source-generation increment atomically. Any drift, invalid reference,
assurance mismatch, digest mismatch, or bundle over 16 MiB rolls everything
back.

The bundle assembler emits the exact no-extra-key payload objects and array
orders in the specification. Its omission list is the fixed ordered subset of
`acceptance_criterion_absent`, `verification_criterion_absent`,
`artifact_content_not_observed`, and
`historical_finding_reference_absent`; no repository branch may add free-form
or unknown omissions. The stored bundle digest is SHA-256 over
`taskgov-completion-evidence-bundle-v1\0` and canonical payload bytes without
LF and is identical to the later envelope and index-entry bundle digest. It
contains no general row serializer, extension map, or publication-time value.

Reopen checks the current cycle and bundle relation but never modifies either.
The reopened Task clears current target and evidence through the existing
path. Its later completion binds a new capture-version-1 target and creates the
next cycle/bundle. Historical cycle/bundle rows are audit-only.

### Evidence Projection State And Publication

`CanonicalStatePaths` and `DatabaseTarget` gain only:

```text
evidence_root
evidence_index
evidence_bundles
evidence_lock
```

All resolve beneath fixed `state/current/evidence`. The fixed bundle filename
is `<completion-evidence-bundle-id>.json`; no caller path exists. Resolution rejects links,
reparse points, nonregular files, containment changes, DB aliases, unknown
recognized names, and unsafe stage content. Generated files remain excluded
from package manifests and source commits.

`evidence_projection_state` stores nonnegative source generation, nullable
published generation not above source, nullable index digest, and the same
closed `succeeded|deferred|failed` outcome/time shape used by maintenance.
Every completion-cycle insertion advances source generation exactly once in
that same transaction. This includes a version-1 bundle/cycle and the sole
version-0 partial legacy reopen bridge; the latter makes a new
`legacy_unknown` index entry due without inventing a bundle. Authority,
criteria, target, Receipt, review, and a reopen that inserts no bridge cycle do
not advance it or publish an unsealed partial bundle.

The projector uses one query-only transaction to capture project/schema,
projection generation, every completion cycle, native bundle and members, and
legacy state in bounded batches. It closes SQLite before rendering. One
canonical encoder implements the specification's integer-only, exact-Unicode,
sorted-key, compact UTF-8 rules and rejects every unsupported value. It renders
the exact bundle and index envelopes with no extra keys. Bundle arrays use
stored ordinal/matrix order; index entries use Task ID, cycle ordinal, and
cycle ID order. Bundle payload digest equals the stored DB digest; file digest
hashes the complete envelope plus LF; index payload digest equals projection
state. There is no current clock input, so a same-basis repair is byte-identical.

Publication under one zero-wait Evidence lock is:

1. validate or atomically publish every bundle file required by the captured
   generation through same-directory flushed temporaries;
2. render, flush, and atomically replace the index last;
3. conditionally record the captured published generation and index digest in
   a short transaction; and
4. recheck once, allowing at most one follow-up capture when a concurrent
   completion advanced the generation.

The index is the commit point. A bundle file not named by a valid index is not
part of the public projection. A referenced file must match ID, project,
format, and digest. Missing, behind, ahead, wrong-project, unknown-version,
unsafe, or digest-mismatched state is never consumed by taskgov. The DB remains
authoritative and setup can regenerate one-way. No JSON file is imported or
used to repair DB state.

The index is capped at 100,000 entries and 64 MiB and is never truncated; each
bundle retains the 16-MiB cap. A bound violation leaves the prior index and
records failure. A standalone report reader validates format/project/digests
and reports the declared generation; only taskgov can compare that generation
with the inaccessible canonical DB and prove freshness.

The post-commit coordinator runs Evidence projection, Viewer refresh, then
due backup, independently. Completion remains successful after Evidence lock
contention or rendering failure and adds only the fixed deferred/failed
warning. Every later state-changed business mutation may retry one due Evidence
refresh without an LLM decision. Setup directly repairs missing/stale/corrupt
projection after migration/configuration and before Viewer publication;
read-only setup reports the planned write and does nothing. Doctor reads the
stored generation/outcome only.

TG-M22.3 updates setup data with `evidence_status`, adds
`evidence_projection_publish` to the ordered setup write vocabulary, adds one
fixed Evidence maintenance object to doctor, and synchronizes the corresponding
stable errors/warnings. These are additive changes to existing leaves, not a
new leaf.

### Migration, Viewer, Packaging, And Legacy Rules

Migration 18 fingerprints and preserves every v17 business row before adding
capture objects and one current-basis snapshot per Task. It creates no
historical target binding, manifest, evidence reference, Receipt, finding, or
cycle. Migration 19 adds only version-0/null bundle discriminators to existing
cycles and an initial projection generation/state; it creates no historical
bundle or criterion link. Reentry validates exact objects, triggers, ownership,
digests, counts, and absence of invented evidence.

Viewer snapshot v4 accepts source schema v18 at M22.2 and v19 at M22.3 while
retaining its exact content and UI. It does not expose authority snapshots,
criteria, artifacts, links, bundles, or Evidence projection state. Its
completion-history reader validates the added cycle discriminator internally
and discards the bundle ID. Evidence projection generation is independent of
Viewer source generation.

M22.2, M21.5, and M22.3 each synchronize the unpublished package candidate,
manifest, release checker, Skill/reference wording, formal docs, and applicable
setup/recovery/staging, migration, and Viewer compatibility in the same commit
as their reachable behavior. M22.2 updates the durable `AGENTS.md` Receipt
retention allow-list from the current label-only rule to the versioned legacy
label and tool-owned structural-subject boundary. M22.3 updates its generated
artifact routing so the fixed Evidence projection joins DB, backup, and Viewer.
None publishes a tag, Release, or remote mutation.

Pre-v19 cycles remain visible only as index entries with
`bundle_state=legacy_unknown`; there is no bundle file and no attempted Git or
prose reconstruction. The legacy reopen bridge remains version 0/null and
advances Evidence source generation when it inserts that cycle. A selected
pre-v18 high/medium Finding is represented only by the nullable-reference
legacy snapshot and omission defined above. A post-v19 recompletion creates
only a new version-1 bundle for the new cycle. V17 Receipts retain their exact
row, reviewer key, summary, target, verdict, caller-attested class, and v17
done-cycle rules; Review reads project provenance v0, but migration creates no
provenance row/reference/bundle. A native bundle accepts
only subject basis one and serializes exactly `basis_version`, `kind`,
`authority_snapshot_id`, and `verification_criterion_id`. The schema-v18
read-model union's `legacy_caller_label` key and the internal compatibility
value are both absent; result/coverage remain caller-attested.

### Future M23 And M24 Seams

The M23 analyzer may later receive one exact native bundle or validated
`legacy_unknown` index entry through a bounded core-created packet and publish
a separate `llm_derived` revision outside canonical Evidence References and
criterion links. It may not update a bundle, create canonical evidence, change
a Task or gate, or read SQLite directly. M22 activates no worker, outbox,
remote model, report narrative, retry policy, or canonical analyzer writer.

The M24 Runner may later add a runner-observation table and a new tagged
verification-basis/bundle version. A Runner can classify only its directly
observed argv-plan execution facts as machine-observed; project test selection,
environment authenticity, external effects, and old caller evidence do not
inherit that class. Shadow evidence remains gate-ineligible. Later gate
activation must retain the M21 Receipt path as an explicit fallback and never
rewrite existing cycles, bundles, links, or digests.

### Current And Conditional Implementation And Acceptance Units

1. **Accepted TG-M22.2** owns schema v18, authority/criterion/reference repositories,
   subject-basis Receipt/cycle fields, label-free CLI/Task-show transition, Git
   manifest capture, Task/Contract/target/Receipt/Finding/completion integration
   including capture-v0 rejection and native-cycle references, the 1,000-
   character stored/read boundary with 500-character public Task ingress,
   migration,
   self-host retarget/reverification/review, Viewer-v4 v18 compatibility,
   synchronized `AGENTS.md` retention, docs/Skill/package, split focused/full
   tests, exact diff, Verification Receipt, and two Tier 2 reviews. It creates
   no bundle or Evidence JSON.
2. **Accepted TG-M21.5** owns only the public Task add/edit verification-admission
   change from 500 to 1,000 after schema v18; stored readers and DDL remain
   unchanged, with no migration, subject redesign, leaf, or normal-loop call.
3. **Current TG-M22.3** owns schema v19, native bundle sealing, immutable criterion
   links and finding snapshots, projection state/resolver/staging/setup/
   maintenance, subject-based index-last JSON publication, Viewer-v4 v19
   compatibility, synchronized governing/Skill/package surfaces, full tests,
   exact diff, Verification Receipt, and two Tier 2 reviews.
4. **Inactive TG-M22.4** owns realistic legacy/current, subject, 1,000-character, and
   Evidence integrated acceptance plus only bounded repairs inside the accepted
   design. It does not activate M23, M24, remote inference, Viewer Evidence UI,
   another command, or another Skill-loop call.

Every unit is Tier 2 and lands as a coherent completion commit. Schema v18 is
the synchronized accepted TG-M22.2/TG-M21.5 predecessor boundary; schema v19
is current only as its exact owning code, migrations, Viewer source range,
package, tests, and formal contracts agree.

## Downstream Boundary

TG-M23 reporting authority is in
[the M23 conditional contract](tg-m23-derived-evidence.md). TG-M24 Runner
authority is in
[the M24 conditional contract](tg-m24-verification-runner.md). Neither is
activated by this document.
