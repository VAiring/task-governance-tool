# Evidence Specification

This document owns Review provenance, authority snapshots, whole-field criteria,
Evidence References, sealed Bundle/Finding snapshots, canonical Evidence JSON,
and the retained interpretation boundary delegated by the
[product specification](specification.md#current-schema-v22-verification-ledger-and-bundle-contract).
Implementation structure belongs in the [Evidence design](evidence-design.md).
[Review operations](review-completion-specification.md),
[Verification Receipt eligibility](review-completion-specification.md#verification-receipt-eligibility-and-manual-completion),
[completion history](review-completion-specification.md#completion-cycle-history), and
[Task operations](task-operation-specification.md) retain their own contracts.
The shared [schema-v21 Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol),
[current persistence](database-specification.md#current-schema-v22-persistence-contract),
[Runner execution](runner-execution-specification.md),
[maintenance](setup-state-specification.md#same-process-maintenance), and
[privacy/stable errors](specification.md#privacy-safety-and-stable-errors) remain
with their existing owners, routed by the [authority index](authority.md).

<a id="versioned-review-provenance-and-bundle-boundary"></a>

## Versioned Review Provenance And Bundle Boundary

New `independent` and `self_review_fallback` Receipts use `review receipt add`
or [structured result registration](review-completion-specification.md#structured-review-results).
Both retain the same individual evidence and provenance. Every public Review Receipt has exactly one
`review_provenance` union. A native independent/fallback Receipt exposes one v1
object with exactly these keys:

```text
review_provenance_id provenance_version reviewer_class model_state
declared_model_id skill_state declared_skill_id declared_skill_version
review_profiles review_lenses context_relation method_codes assurance_class
producer_class producer_version digest
```

The ID is `tg_review_provenance_` plus 16 lowercase hexadecimal characters;
`provenance_version` is integer `1`; assurance and producer are exactly
`bound_attestation/trusted_caller/1`; and `digest` is
`sha256:<64-lowercase-hex>`. The digest is SHA-256 over
`taskgov-review-provenance-v1\0` plus canonical sorted-key compact UTF-8 JSON
containing exactly `project_id`, `task_id`, `review_receipt_id`, `receipt_kind`,
`target`, and every v1 public field from `provenance_version` through
`producer_version`. `target` is exactly
`{kind,value,base_revision,generation,capture_version}`. The random provenance
ID and digest are excluded from that input.

A pre-v18 independent/fallback Receipt has no native row and projects the same
keys with `provenance_version=0`, null ID/digest and null v1 semantic fields and
collections, plus exactly `legacy_unknown/legacy_migration/1`. A Tier-0
`not_required` Receipt projects `review_provenance=null` and owns no provenance
row. Legacy absence, explicit v1 unknown, empty v1 code sets, and not-required
are distinct states; no migration or reader infers one from reviewer key,
summary, kind, or verdict.

The exact scalar vocabularies and fixed collection orders are:

```text
reviewer_class   human llm deterministic_tool hybrid unknown
model_state      declared not_applicable unknown
skill_state      declared not_applicable not_used unknown
context_relation same_context forked_context fresh_context external_context
                 not_applicable unknown
review_profiles  general authority_contract implementation verification
                 migration_compatibility privacy_safety release_acceptance
review_lenses    correctness contract_compliance state_completion_integrity
                 privacy target_safety verification_regression
                 migration_compatibility maintainability accessibility
                 performance release_integrity
method_codes     review_packet_inspection authority_cross_check diff_inspection
                 source_inspection test_inspection
                 verification_evidence_inspection artifact_inspection
                 runtime_observation deterministic_rule_check
```

At most four profiles, eight lenses, and eight methods are accepted. Each is a
set: duplicates are invalid, empty is valid, and storage/public projection uses
the fixed enum order regardless of option order. `context_relation` is one
required code. Declared model and Skill IDs are 1-128 ASCII bytes matching
`[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}`; declared Skill version is 1-64 ASCII
bytes matching `[A-Za-z0-9][A-Za-z0-9._+-]{0,63}`. Values are preserved
byte-for-byte after the common privacy check and are identifiers, not
free-form capability claims.

The existing leaf requires `--reviewer-class`, `--model-state`,
`--skill-state`, and `--context-relation`; permits optional
`--declared-model-id`, `--declared-skill-id`, and
`--declared-skill-version`; and permits repeatable `--review-profile`,
`--review-lens`, and `--review-method` only for independent/fallback Receipts.
No value is defaulted or inferred. Every provenance option is forbidden for
`not_required`. Type, enum, bound, duplicate, grammar, or cross-field failure
uses `invalid_review_evidence`; privacy rejection retains precedence and emits
no rejected value.

| Case | `reviewer_class` | Model state and ID | Skill state and ID/version |
|---|---|---|---|
| Human | `human` | `not_applicable`, no ID | `not_applicable`, no ID/version |
| LLM without Skill | `llm` | `declared` with ID, or `unknown` without ID | `not_used`, no ID/version |
| LLM with Skill | `llm` | `declared` with ID, or `unknown` without ID | `declared` with ID/version, or `unknown` with neither |
| Deterministic tool | `deterministic_tool` | `not_applicable`, no ID | `not_applicable`, no ID/version |
| Hybrid | `hybrid` | `declared` with ID, or `unknown` without ID | `declared` with ID/version, `not_used`, or `unknown` |
| Applicable data unavailable | `unknown` | `unknown`, no ID | `unknown`, no ID/version |
| Review not required | no provenance object | no model state | no Skill state |
| Legacy independent/fallback | v0 absence | `legacy_unknown` | `legacy_unknown` |

The matrix is exact. Human and deterministic-tool require both states
`not_applicable`; LLM and hybrid require model `declared` with its ID or
`unknown` without one; declared Skill use requires both ID and version;
`not_used` and `unknown` require both absent; reviewer class `unknown` requires
both states `unknown`. Verdict does not alter this matrix. Profile/lens/method
sets and context add no capability, applicability, or further inference.

Schema v18 stores the version discriminator and nullable provenance ID on the
Receipt plus normalized immutable `review_receipt_provenance` and
`review_receipt_provenance_codes` rows. Code rows contain only
`profile|lens|method`, a zero-based contiguous ordinal within kind, and an
allowed code; duplicate code or ordinal is invalid. Migrated
independent/fallback is `0/null`, native independent/fallback is `1/non-null`
with exactly one v1 row, and `not_required` is `0/null`. The Receipt,
provenance, and code rows commit atomically. Migration creates no provenance,
ID, digest, declaration, or Evidence Reference, and every reentry/read path
validates the exact version/kind/null/code/digest matrix.

Native v1/null provenance is included in the Review Receipt Evidence Reference
and native Bundle. Migrated v0 Receipts have neither and cannot enter a native
Bundle. Viewer snapshot v4 validates and discards provenance with no field,
panel, filter, or UI. The original Receipt assertion remains caller-attested;
neither it nor provenance proves identity, execution, competence,
independence, quality, diversity, or truth, changes a gate, or stores a person,
session, prompt, chat, reasoning, raw output, command, log, environment,
credential, or provider body.

<a id="authority-snapshot-whole-field-criteria-and-references"></a>

## Authority Snapshot, Whole-Field Criteria, And References

Each Task owns a positive-generation immutable authority snapshot of exactly
its title, description, review tier, exact verification bytes, Contract
revision/state/scope/acceptance/constraints/authority reference, producer, and
domain-separated canonical digest. Task add creates generation 1. A semantic
title, description, tier, verification, or Contract change creates the next
snapshot in the same transaction; unrelated status or metadata changes do
not. Migration creates one `legacy_migration` snapshot of the exact current
stored basis and never reconstructs earlier authority. Acceptance and
verification criteria are immutable exact whole fields with
kind-specific domain-separated digests. Taskgov never splits, rewrites,
judges, or infers subcriteria. Revision-zero acceptance and trimmed-empty
verification have no criterion, while the snapshot still preserves exact
verification whitespace. Target capture binds the current snapshot and
nullable criterion IDs. Every native schema-v18 manifest, Verification Receipt, Review Receipt, Review Finding, and completion-evidence source receives one immutable Evidence
Reference in the same transaction. Its digest covers the exact source
projection, ownership, Contract/snapshot/criteria, four-field target, optional
completion cycle, and closed assurance/producer/version. Git observation is
`machine_observed/taskgov_git/1`; caller assertions are
`bound_attestation/trusted_caller/1`; external revisions are
`external_reference/external_system/1`. Callers cannot select or upgrade those
classes. Migration synthesizes no historical Reference. Schema-v19 and
schema-v20 criterion links, native Bundles, and Evidence JSON are active;
the retired `derived_analysis` source is not admitted by current schema v22.
The schema-v20 Runner writer
is active only for the [Runner audit graph](runner-execution-specification.md#parent-service-and-audit-graph) and remains
ineligible for every verification and completion gate.

<a id="schema-v19-bundle-foundation-schema-v20v21-native-writer-and-evidence-json"></a>

## Schema-v19 Bundle Foundation, Schema-v20/v21/v22 Native Writer, And Evidence JSON

Migration 19 `completion_evidence_bundles` adds immutable criterion links, Bundle membership and Finding snapshots, completion Bundles, cycle `evidence_basis_version`/bundle linkage, and Evidence projection state. Every existing cycle becomes version 0 with null bundle ID; migration creates no historical Bundle or link and projects that absence only as `legacy_unknown`.
Every native schema-v19 completion atomically inserts one version-1 Bundle with
its cycle, Task update, event, links, selected gate evidence, Finding snapshots,
and projection-generation advance. Schema v20 and the marker-zero schema-v21
baseline instead insert one version-2 Bundle whose basis is
`caller_attestation` with the qualifying Receipt for nonempty verification or
`not_required` with no Receipt for trimmed-empty verification, and its Runner
observation is null. The current schema-v22 writer retains both branches and additionally
admits the exact qualifying schema-v21-protocol `runner_observation` branch, reusing its
existing Runner Reference and criterion link as Bundle members. The sole partial
legacy reopen bridge stays version 0/null and advances only that generation. A
Bundle is complete or the completion fails before write; its canonical payload
is capped at 16 MiB.

Bundle v2 adds exactly the root `verification_basis` object and
`runner_observation` field to the v1 payload. For the caller-attestation and
not-required branches,
`verification_basis` has exactly `basis_version=1`, the derived `kind`, the
matching nullable `verification_receipt_id`, and
`runner_observation_id=null`; `runner_observation` is null. Its envelope uses
`format_version=2` and digest domain
`taskgov-completion-evidence-bundle-v2\0`. The preserved v1 envelope, payload,
domain, bytes, and digest are unchanged.

Evidence JSON is a deterministic one-way SQLite projection. Canonical sorted-key compact UTF-8 JSON uses integer-only JSON values where numeric, preserves valid Unicode without normalization, and ends each file with one LF. A schema-v20-through-v22 index uses envelope `format_version=2`, digest domain `taskgov-evidence-index-v2\0`, and adds exactly nullable `bundle_format_version` to each entry: null for `legacy_unknown`, 1 for a preserved v1 Bundle, and 2 for a native v2 Bundle. Native entries reference `bundles/<completion-evidence-bundle-id>.json`; the projection may therefore reference preserved source-19/v1 and source-20/21/v2 Bundles alongside new source-22/v2 Bundles without rewriting existing payload bytes or digests. The index reports its actual database source schema. Pre-v19 entries are `legacy_unknown` with null Bundle/file fields.
The index includes every cycle, is ordered by Task ID, ordinal, and cycle ID, and is capped at 100,000 entries and 64 MiB without truncation. Publication flushes immutable Bundle files and atomically replaces `index.json` last; SQLite remains canonical, unreferenced files are ignored, and JSON is never imported or used to repair the database.
Contention or failure preserves the last-good index and committed Task result, leaves projection due, and adds only `evidence_projection_deferred` or `evidence_projection_failed`. Setup is the sole explicit repair; doctor only reports stored projection facts. Evidence JSON exposes no Evidence command, custom path, Viewer field/UI, browser launch, server, watcher, or network action and invokes neither Analyzer nor Runner. A current schema-v22 Runner-backed Bundle projects only the already-sanitized stored observation under the [shared Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol); publication adds no normal-loop call.

<a id="assurance-evidence-references-and-finding-snapshots"></a>

## Assurance, Evidence References, And Finding Snapshots

Every Evidence Reference has exactly one assurance class and an independent
versioned producer:

```text
assurance_class = machine_observed | bound_attestation |
  deterministically_derived | external_reference | legacy_unknown
producer_class = taskgov_core | taskgov_git | trusted_caller |
  legacy_migration | external_system | verification_runner
producer_version = positive integer
```

`machine_observed` means a bounded deterministic component directly observed
the stated local process or Git-object fact, not that taskgov authenticated the
machine, environment, user, or external meaning. `bound_attestation` is a
trusted caller assertion bound to the exact Task, Contract, target, and
generation. `deterministically_derived` is a pure versioned derivation that
cannot be stronger than its sources. `external_reference` retains an external
identity without claiming its content, existence, authority, or semantics.
`legacy_unknown` is irrecoverable absent origin; migration and projection must
not fill or strengthen it. Retired `llm_derived` and `batch_analyzer` occur only
in old-schema compatibility/rejection vocabulary, not the current allow-lists.
Inference never satisfies a verification, review, completion, or release gate.
Producer values are labels, not authentication, signatures, process identity,
independence, or authority.

The v1 source dispatch is exact and uses producer version 1:

| Source kind/state | Assurance / producer | Immutable source projection | Current criterion relation |
|---|---|---|---|
| `artifact_manifest/complete_git` | `machine_observed/taskgov_git` | manifest ID/state/object format/comparison base/entry count/digest/null omission | acceptance `completion_basis`, when present |
| `artifact_manifest/opaque_target/diff_fingerprint` | `bound_attestation/trusted_caller` | manifest ID/state/target kind/digest/`artifact_content_not_observed` | acceptance `completion_basis`, when present |
| `artifact_manifest/opaque_target/external_revision` | `external_reference/external_system` | the same closed opaque projection | acceptance `completion_basis`, when present |
| `verification_receipt/recorded` | `bound_attestation/trusted_caller` | Receipt ID, subject basis and IDs, result, duration, coverage, creation time; no legacy label or compatibility value | verification `verification_attestation`, exactly once |
| `review_receipt/recorded` | `bound_attestation/trusted_caller` | Receipt ID, reviewer key, kind, verdict, summary, approval, creation time, and native v1/null provenance | acceptance `review_assessment` for each selected qualifying Receipt, when present |
| `review_finding/recorded` | `bound_attestation/trusted_caller` | Finding ID, Receipt ID, severity, original summary, creation time | acceptance `review_finding` for current-generation Findings, when present |
| `completion_evidence/git_commit` | `machine_observed/taskgov_git` | cycle ID/time and exact six-field completion evidence | acceptance `completion_basis`, when present |
| `completion_evidence/external_revision` | `external_reference/external_system` | the same closed completion projection | acceptance `completion_basis`, when present |
| `completion_evidence/commit_not_required` | `bound_attestation/trusted_caller` | the same closed completion projection | acceptance `completion_basis`, when present |
| `runner_observation/recorded` | `machine_observed/verification_runner` | the closed sanitized Runner observation projection | verification `runner_observation` only for the active gate-eligible branch |

Each `evidence_reference` stores exact project/Task/Contract ownership,
authority snapshot, nullable criteria, complete target tuple, source ID/state,
assurance/producer/version, nullable completion-cycle ID, and digest. Criteria
must equal the snapshot links; Verification Receipt references require
subject-basis one and its matching verification criterion; only completion
evidence has a cycle ID; and a Finding copies its Receipt binding. Callers
supply none of the dispatch, assurance, producer, binding, or relation.
Evidence Reference IDs are
`tg_evidence_reference_<16-lowercase-hex>`.

The reference digest is SHA-256 over
`taskgov-evidence-reference-v1\0` plus canonical JSON containing the exact
source kind/state and projection, every required/nullable binding,
assurance/producer/version, and no random reference ID or creation time. A
Finding reference excludes `status`, `resolution_summary`, and `resolved_at`;
resolution does not mutate or supersede it. A criterion link copies its source
class and producer exactly; no link, Bundle, projection, analysis, or Runner
operation transitively upgrades assurance.

Criterion links use IDs `tg_criterion_evidence_link_<16-lower-hex>` and exactly
`verification_attestation|review_assessment|review_finding|completion_basis|
runner_observation`. Construction is mechanical: when
acceptance exists, the current manifest and completion evidence receive
`completion_basis`, selected qualifying Review Receipts receive
`review_assessment`, and current-generation Findings receive `review_finding`;
when verification exists, its selected manual Receipt receives
`verification_attestation`, or the gate-eligible Runner Reference receives
`runner_observation`. Absent criteria omit their links without removing valid
Bundle members. Current schema v22 has no `derived_analysis` Reference/link
reservation or writer; old-schema DDL remains available for compatibility.

A Bundle Finding snapshot contains all current-target-generation Findings and
all earlier high/medium Findings, excludes earlier low Findings, and orders by
target generation, creation time, then Finding ID. It has exactly
`review_finding_id`, `review_receipt_id`, `target_generation`, `severity`,
`summary`, `status`, `resolution_summary`, `created_at`, `resolved_at`,
`evidence_reference_id`, `assurance_class`, `producer_class`,
`producer_version`, and `digest`. Native Findings carry their Reference and
`bound_attestation/trusted_caller/1`; pre-v18 Findings have a null Reference and
`legacy_unknown/legacy_migration/1`, create no link, and add
`historical_finding_reference_absent` once. The snapshot digest is SHA-256 over
`taskgov-completion-bundle-finding-snapshot-v1\0` plus canonical JSON of every
preceding field except `digest`.

Every native completion inserts exactly one immutable Bundle with ID
`tg_completion_evidence_bundle_<16-lowercase-hex>` in the same transaction as
its cycle, Task update, event, and source-generation advance. The complete
Bundle-v1 omission vocabulary, in order, is exactly
`acceptance_criterion_absent`, `verification_criterion_absent`,
`artifact_content_not_observed`, and
`historical_finding_reference_absent`. They respectively mean revision-zero
Contract acceptance, trimmed-empty Task verification, an opaque target, and at
least one selected pre-v18 Finding with unknown Reference provenance. Empty
Finding arrays and tier-derived Receipt counts are represented directly, not as
omissions. Pre-v19 cycle absence is represented only by an index
`legacy_unknown` entry and never by a native Bundle. Reopen validates but never
edits a prior cycle or Bundle; later completion creates a new one and historical
Bundles never satisfy a current gate.

<a id="canonical-evidence-bundle-and-index-formats"></a>

## Canonical Evidence Bundle And Index Formats

The only generated paths are
`state/current/evidence/index.json` and
`state/current/evidence/bundles/<completion-evidence-bundle-id>.json` beneath
the ignored canonical package state. There is no public command, custom path,
import, endpoint, watcher, background worker, or Viewer Evidence UI.

Canonical JSON accepts only null, Boolean, string, integer, array, and object;
rejects floats, nonfinite values, lone surrogates, duplicate keys, and invalid
Unicode; emits shortest-decimal integers and lowercase literals; uses the short
escape for quote, backslash, and supported controls and lowercase `\u00xx` for
other JSON controls; does not escape slash or valid non-ASCII scalars; orders
object keys by Unicode code point; emits no insignificant whitespace; and never
normalizes text. Durable files are BOM-free UTF-8 with one terminal LF.

A preserved Bundle-v1 file is exactly this no-extra-key envelope plus LF:

```text
{"bundle_digest":"sha256:<64-lowercase-hex>","format_version":1,"payload":<bundle-payload>}
```

Its payload has exactly these keys and values:

| Key | Exact value |
|---|---|
| `artifact_manifest` | `{artifact_manifest_id,state,object_format,comparison_base,digest,omission_code,entries}`; nullable fields are null and entries are exact ordinal rows `{ordinal,kind,old_path,new_path,before_mode,before_object_id,after_mode,after_object_id}` |
| `authority_snapshot` | `{authority_snapshot_id,generation,digest}` |
| `bundle_id` / `bundle_version` | Bundle ID / integer `1` |
| `completion_cycle_id` / `cycle_ordinal` / `sealed_at` | cycle ID / positive integer / canonical UTC string |
| `completion_evidence` | `{kind,revision,reason,external_revision_approved,completion_commit_required,completion_commit_hash}` with flags as `0|1` |
| `contract` | `{revision,specified,scope,acceptance,constraints,authority_ref}` with Boolean `specified` |
| `criteria` | `{criterion_id,kind,text,digest}` rows ordered acceptance then verification; absent criteria have no row |
| `criterion_links` | objects `{criterion_evidence_link_id,criterion_id,evidence_reference_id,relation,assurance_class,producer_class,producer_version}` ordered by criterion kind, criterion ID, fixed relation order, Reference ID, then link ID |
| `evidence_references` | objects `{evidence_reference_id,source_kind,source_state,source_id,assurance_class,producer_class,producer_version,contract_revision,authority_snapshot_id,acceptance_criterion_id,verification_criterion_id,target_kind,target_value,target_base_revision,target_generation,completion_cycle_id,digest}` ordered by fixed source-kind order, source ID, then Reference ID; nullable bindings are null |
| `finding_snapshots` | exact snapshot rows in the [Finding order](#assurance-evidence-references-and-finding-snapshots) |
| `omissions` | unique strings in fixed omission order |
| `project_id` | project ID |
| `review_receipts` | selected objects `{review_receipt_id,reviewer_key,receipt_kind,verdict,summary,user_approved,created_at,review_provenance}` in qualifying gate-basis order; `user_approved` is `0|1` and provenance is v1 or null |
| `source_schema_version` | integer `19` |
| `target` | `{kind,value,base_revision,generation,capture_version}` |
| `task` | `{task_id,title,description,review_tier,verification}` |
| `verification_receipt` | null without a verification criterion; otherwise `{verification_receipt_id,verification_subject,result,duration_ms,scope_coverage,created_at}` with subject exactly `{basis_version,kind,authority_snapshot_id,verification_criterion_id}`, basis 1/kind `task_verification_criterion`, and no legacy-label key |

The fixed relation order is
`verification_attestation,review_assessment,review_finding,completion_basis,
runner_observation`; the fixed Reference source-kind order is
`artifact_manifest,verification_receipt,review_receipt,review_finding,
completion_evidence,runner_observation`. Text tie-breakers use
unsigned UTF-8 order; generations and ordinals compare numerically. The v1
Bundle digest is SHA-256 over
`taskgov-completion-evidence-bundle-v1\0` plus canonical payload bytes without
LF. The file digest hashes the complete envelope and LF.

A preserved v1 index is the exact no-extra-key envelope
`{"format_version":1,"index_digest":"sha256:<64-lowercase-hex>","payload":<index-payload>}`
plus LF. Its payload has exactly `source_schema_version=19`, `project_id`,
nonnegative `projection_generation`, `bundle_count`, `legacy_count`, and
`entries`. Each entry has exactly `task_id`, `completion_cycle_id`,
`cycle_ordinal`, `bundle_state`, `bundle_id`, `bundle_file`, `bundle_digest`,
`file_digest`, and `sealed_at`. Native entries have state `native` and five
non-null Bundle/file/seal fields; `legacy_unknown` entries have all five null.
Entries order by Task ID, cycle ordinal, then cycle ID and counts equal their
states. The index digest is SHA-256 over `taskgov-evidence-index-v1\0` plus
canonical payload bytes without LF.

Current index format v2 preserves that payload and ordering and adds exactly
nullable `bundle_format_version` to each entry: null for `legacy_unknown`, 1
for preserved Bundle v1, and 2 for Bundle v2. It uses domain
`taskgov-evidence-index-v2\0`. Bundle v2 preserves every v1 member and adds only
the closed `verification_basis` and nullable `runner_observation` roots defined
by the [shared schema-v21/v22 verification-basis contract](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol). Existing v1/v2 bytes and
digests are immutable.

Runner References and Bundle v2 retain the captured `runner_policy_digest`
and the existing measurement members under the
[Runner policy and accounting contract](runner-execution-specification.md#runner-policy-and-accounting).
No producer or reader substitutes zero for an unmeasured value or reinterprets
captured measurements using the current OS. The exact POSIX policy identity
selects its explicit unmeasured-auxiliary shape; this changes no envelope,
member set, domain, assurance, or previously sealed bytes.

Standalone Evidence validation preserves the earlier admission of any
well-formed SHA-256 policy label with the legacy accounting shape. In
particular, a legacy qualifying PASS still has all three measured values;
accepting its opaque label does not identify it as the native Windows policy
or admit that label to a canonical Runner graph. Only the exact POSIX label
selects the new measurement shape. Native graph admission remains with the
Runner owner, and standalone file consistency never establishes current
execution or completion authority.

Publication captures one coherent DB generation, validates all selected rows,
writes and flushes immutable Bundle files first, and atomically replaces the
index last. The index is the filesystem commit point. Consumers ignore
unreferenced files and reject wrong identity/version/project/digest. Taskgov
also compares index generation with canonical SQLite; a standalone consumer
can prove only file self-consistency and declared generation. The index covers
every cycle, has at most 100,000 entries and 67,108,864 UTF-8 bytes, and is
never truncated. A Bundle is capped at 16,777,216 bytes.

<a id="evidence-interpretation-and-retired-analyzer-boundary"></a>

## Evidence Interpretation And Retired Analyzer Boundary

Facts, caller declarations, LLM inference, and uncertainty are not
interchangeable. Derived explanations do not satisfy verification, review,
completion, or release gates and cannot upgrade the assurance of their sources.

The Analyzer runtime is retired. `derived_analysis`, `llm_derived`, and
`batch_analyzer` remain only in old-schema compatibility/rejection vocabulary;
current schema v22 removes their unused reservations. Unrelated
`deterministically_derived` uses remain supported. Reservation cleanup preserves
valid durable rows, Evidence formats, canonical byte/digest rules, and Runner gates.
Existing ignored `state/current/analysis/` artifacts remain untouched and inert;
there is no cleanup, import, relocation, or repair path for them.

Independent Evidence reading/validation is retained only in repository tests.
It checks file self-consistency, declared generation, versions, identities,
canonical bytes/digests, relations, and existing bounded semantics/privacy
rules. It does not authenticate authors, prove source-code correctness, or
establish freshness against SQLite. No runtime reader, report schema, renderer,
citation-generation subsystem, or future Reporting adapter is retained. Any
later non-test reader path requires a separate product decision.
