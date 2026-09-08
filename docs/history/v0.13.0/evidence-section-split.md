# Evidence Section Split Capture

> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves only the Evidence sections before their document split.
> Words such as current, approved, or implemented describe the captured revision,
> not current authority. This history cannot fill an active-contract gap, satisfy
> a current gate, or authorize a removed behavior.

- Source commit: `c3c9323370ab24f6eddf10694ccf34e9c4ebbc3a`
- Source paths: `docs/specification.md` and `docs/design.md`; the exact
  section boundaries are identified with each captured block below.
- Capture unit: `TG-MOD.12`
- Active replacements:
  [Evidence specification](../../evidence-specification.md)
  and [Evidence design](../../evidence-design.md).
  [Repository authority](../../authority.md) routes the unchanged common owners.
  Use the public CLI for live Task state and evidence.

## Captured Section 1: Versioned Review Provenance And Bundle Boundary

Source path: `docs/specification.md`

Source range: `### Versioned Review Provenance And Bundle Boundary` through immediately before `### Git Snapshot And Target Binding`.

````markdown
### Versioned Review Provenance And Bundle Boundary

New `independent` and `self_review_fallback` Receipts use the existing
`review receipt add` leaf. Every public Review Receipt has exactly one
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

````

## Captured Section 2: Authority Snapshot, Whole-Field Criteria, And References

Source path: `docs/specification.md`

Source range: `### Authority Snapshot, Whole-Field Criteria, And References` through immediately before `### Receipt Meaning And Record`.

````markdown
### Authority Snapshot, Whole-Field Criteria, And References

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
is active only for the audit graph defined below and remains
ineligible for every verification and completion gate.

````

## Captured Section 3: Bundle Foundation, References, Finding Snapshots, And Canonical Formats

Source path: `docs/specification.md`

Source range: `<a id="schema-v19-bundle-foundation-schema-v20v21-native-writer-and-evidence-json"></a>` through immediately before `## Recovery Candidate Validity Contract`.

````markdown
<a id="schema-v19-bundle-foundation-schema-v20v21-native-writer-and-evidence-json"></a>

### Schema-v19 Bundle Foundation, Schema-v20/v21/v22 Native Writer, And Evidence JSON

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
Contention or failure preserves the last-good index and committed Task result, leaves projection due, and adds only `evidence_projection_deferred` or `evidence_projection_failed`. Setup is the sole explicit repair; doctor only reports stored projection facts. Evidence JSON exposes no Evidence command, custom path, Viewer field/UI, browser launch, server, watcher, or network action and invokes neither Analyzer nor Runner. A current schema-v22 Runner-backed Bundle projects only the already-sanitized stored observation fixed below; publication adds no normal-loop call.

### Assurance, Evidence References, And Finding Snapshots

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

### Canonical Evidence Bundle And Index Formats

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
| `finding_snapshots` | exact snapshot rows in the Finding order above |
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
by the shared schema-v21/v22 verification-basis contract. Existing v1/v2 bytes and
digests are immutable.

Publication captures one coherent DB generation, validates all selected rows,
writes and flushes immutable Bundle files first, and atomically replaces the
index last. The index is the filesystem commit point. Consumers ignore
unreferenced files and reject wrong identity/version/project/digest. Taskgov
also compares index generation with canonical SQLite; a standalone consumer
can prove only file self-consistency and declared generation. The index covers
every cycle, has at most 100,000 entries and 67,108,864 UTF-8 bytes, and is
never truncated. A Bundle is capped at 16,777,216 bytes.

````

## Captured Section 4: Evidence Interpretation And Retired Analyzer Boundary

Source path: `docs/specification.md`

Source range: `## Evidence Interpretation And Retired Analyzer Boundary` through immediately before `## Trusted-Local Verification Runner`.

````markdown
## Evidence Interpretation And Retired Analyzer Boundary

Facts, caller declarations, LLM inference, and uncertainty are not
interchangeable. Derived explanations do not satisfy verification, review,
completion, or release gates and cannot upgrade the assurance of their sources.

The M23 Analyzer runtime is retired. `derived_analysis`, `llm_derived`, and
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

````

## Captured Section 5: Provenance, Evidence Ledger, And Bundle Structure

Source path: `docs/design.md`

Source range: `### Provenance, Evidence Ledger, And Bundle Structure` through immediately before `### Review Packet`.

````markdown
### Provenance, Evidence Ledger, And Bundle Structure

The subsections below are the current implementation owner for Review
provenance, schema-v18 capture, schema-v19 Bundle construction and projection,
and their schema-v20-through-v22 Runner integration. They preserve legacy rows
without inventing evidence, keep SQLite ownership in `storage.py`, and exclude
the retired `derived_analysis` reservation from current schema v22.

#### Capture And Projection Module Ownership

- `review_provenance.py` owns the closed enum/matrix, public v1/v0/null union,
  option normalization, and provenance digest; it owns no SQLite access.
- `evidence_ledger.py` owns assurance/producer validation, authority-basis
  canonicalization, whole-field criteria, Evidence Reference projections and
  digests, and public allow-lists. Active link, Bundle, omission, and Runner
  source assembly is delegated to the owning service/projection boundary.
- `artifact_manifest.py` owns bounded shell-free Git tree/index observation,
  exact artifact-entry normalization, deterministic rename pairing/order, and
  manifest digests. It reuses safe process and stable-snapshot primitives from
  `git_snapshot.py` without routing full manifests through Review Packet.
- `evidence_projection.py` owns the codec, canonical Bundle/index JSON and
  digest validation, native Bundle construction, stored Bundle reconstruction,
  and rendering of captured `EvidenceProjectionBasis` values.
- `evidence_publication.py` consumes `DatabaseTarget` and observation time,
  captures through the storage API, calls those retained builders, and returns
  the existing refresh result or physical status. It owns physical path checks,
  lock/temp/rename handling, Bundle-first/index-last publication, generation
  comparison and outcome recording, last-good preservation, and setup repair.
  Capture closes its read connection before rendering; the separate index
  generation guard retains its read connection through atomic replacement.
  Completion-time construction stays in its existing transaction boundary;
  construction-side storage value types remain shared without a DTO layer.

`storage.py` remains the only SQLite owner. `tasks.py` and `contracts.py`
capture authority inside existing savepoints; `verification_receipts.py`
derives subject-v1 bindings; it and `reviews.py` create typed References inside
their source writes; target capture creates one manifest and subject-capable
basis atomically; completion passes a fully prepared Bundle basis into its
native-cycle savepoint. No feature module opens raw SQLite.

`state_paths.py` and `state_resolver.py` alone own the fixed Evidence root,
index, Bundle directory, and lock. `state_transition.py` recognizes those
generated files only in bounded setup. `maintenance.py` may retry a due
projection after a changed mutation, but only a cycle insert advances source
generation. `setup.py` is the repair owner. `cli.py` owns only existing
setup/doctor fields and warnings and has no Evidence export/import branch.

#### Schema-v18 Capture And Subject Foundation

Migration 18 `evidence_ledger_capture` adds:

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

It also adds the minimum current-snapshot pointers, target capture bindings,
Review-provenance discriminator/ID, and Verification-subject columns. Every
owned table carries project and Task keys, composite foreign keys,
deterministic uniqueness, canonical timestamp/digest checks, and update/delete
denial triggers. No arbitrary JSON or caller-owned assurance is stored.

The subject columns on `verification_receipts` and
`task_completion_cycles` are independently additive:

```text
verification_subject_basis_version INTEGER NOT NULL DEFAULT 0
  CHECK (verification_subject_basis_version IN (0, 1))
subject_authority_snapshot_id TEXT NULL
  REFERENCES authority_snapshots(authority_snapshot_id)
subject_verification_criterion_id TEXT NULL
  REFERENCES contract_criteria(criterion_id)
```

Authority/criterion tables are created before these `ALTER TABLE` additions.
Version-aware indexes, insert guards, and shared readers enforce the exact
version/null matrix, snapshot-to-verification-criterion membership,
project/Task ownership, and locked target binding. Cycle guards additionally
permit subject basis zero only for the exact partial legacy-reopen bridge. No
old Receipt or cycle table is rebuilt or updated: every old row reads
0/null/null with its original ID, caller label, target, timestamp, Receipt link,
cycle relation, and ordering unchanged.

The physical legacy `command_label` column is retained. The native writer puts
only `taskgov-owned-verification-subject-v1` there; basis-zero validation alone
uses the legacy caller-label predicate, and basis-one validation accepts only
that constant. Public formatters and Evidence digest builders never read it.
`verification_receipts.py` builds the public subject only from locked capture
version, authority snapshot, verification criterion, Contract revision, and
complete target tuple. Neither CLI nor repository accepts a label or subject
argument.

An authority snapshot stores its positive Task-local generation, Task title
and description, review tier, exact verification text/digest, Contract
revision and exact scope/acceptance/constraints/authority, explicit specified
state, canonical basis digest, producer metadata, and time. Its digest uses
canonical JSON under `taskgov-authority-snapshot-v1\0`, excluding random ID and
time. Criteria are whole immutable values keyed by same-Task kind and the
SHA-256 of `taskgov-contract-criterion-v1\0`, kind, NUL, and exact UTF-8 text.
Task add and authority-bearing edit compute and insert snapshot/criteria within
the existing Task savepoint; replay produces no duplicate row or event.

Migration creates one `legacy_migration` snapshot of each Task's exact current
basis and never reconstructs history. Existing targets become capture version
0 with null snapshot/manifest bindings; Receipts, Findings, cycles, and events
receive no Reference or subject. New targets are capture version 1. Any
source-creating write against capture version 0 fails `evidence_basis_stale`
inside the locked basis check; Review Packet preparation and resolution of an
existing Finding remain permitted. Source schemas through 17 use the 500-code-
point stored verification limit and schemas 18+ use 1,000; current public
Task add/edit admission is 1,000. Stored paths never reuse public-input
validation or normalize/truncate bytes.

#### Evidence Reference And Manifest Construction

An `evidence_reference` stores one closed source kind/state and source ID or
closed completion value, assurance/producer/version, exact ownership,
Contract/snapshot/nullable-criterion/four-field-target binding, nullable cycle
ID, digest, and time. One constant repository dispatch derives every required
and null field and source projection; callers provide none. Current source
kinds are manifest, Verification Receipt, Review Receipt, Review Finding,
completion evidence, and gate-eligible Runner observation. The
`derived_analysis` source is not admitted by current schema v22. Reference creation shares the
source transaction. Its digest helper uses
`taskgov-evidence-reference-v1\0` and excludes random ID/time; for Findings it
also excludes mutable resolution fields. Validators recompute dispatch,
ownership, null matrices, and digest and reject every class upgrade.

`artifact_manifest.py` reads complete Git leaves, not Review Packet summaries.
For snapshots it compares exact HEAD with stable stage-0 index; for commits it
compares the exact commit with its first parent or empty tree. Leaves normalize
to `relative_posix_path mode full_object_id`. A bytewise path merge produces
add/delete/modify; a second pass converts only a unique exact mode/object
delete-add pair to rename. Ambiguous duplicates and content-changing moves stay
delete+add. One pure sorter applies the specification tuple with null first and
unsigned UTF-8 comparison, then assigns contiguous zero-based ordinals.

Observation is shell-free and uses bounded timeouts, null stdin, disabled
optional locks/fsmonitor/lazy fetch/external diff/text conversion, and pre/post
stability checks. It never invokes a hook, checkout, index write, fetch,
network, or caller command. Paths must be portable safe relative UTF-8 POSIX
text no longer than 240 bytes. At most 10,000 entries and 16 MiB canonical
manifest bytes are accepted. Overflow, unsafe path, object loss, object-format
change, or drift aborts target setting without a target, manifest, event, or
maintenance effect. Fingerprint/external targets create a zero-entry opaque
manifest with the fixed omission and their respective caller/external class.
The manifest digest covers the exact canonical content under
`taskgov-artifact-manifest-v1\0`; its random ID/time are excluded.

#### Schema-v19 Bundle And Evidence Publication

Migration 19 `completion_evidence_bundles` adds:

```text
criterion_evidence_links
completion_evidence_bundles
completion_bundle_members
completion_bundle_finding_snapshots
evidence_projection_state
```

It also adds `evidence_basis_version` and nullable
`completion_evidence_bundle_id` to cycles. Existing cycles become 0/null; only
the exact partial legacy bridge may later insert that shape. Native cycles use
version 1 and a same-project/same-Task Bundle. Deferred composite foreign keys
allow immutable cycle and Bundle rows to be inserted together.

Criterion-link construction is one closed repository matrix. Acceptance links
the current manifest and completion evidence as `completion_basis`, selected
Review Receipts as `review_assessment`, and current-generation Findings as
`review_finding`; verification links either the unique manual Receipt as
`verification_attestation` or the qualifying Runner Reference as
`runner_observation`. Missing criteria omit links without omitting valid Bundle
members. Every other source/relation/cardinality is rejected.

Bundle members freeze the exact Reference/link set. Finding selection includes
all current-generation Findings plus earlier high/medium Findings, excludes
earlier lows, and orders by generation/time/ID. Native snapshots preserve their
Reference and class; selected pre-v18 snapshots use null Reference plus
`legacy_unknown/legacy_migration/1` and the historical-Finding omission. Their
digest uses `taskgov-completion-bundle-finding-snapshot-v1\0`. No later query
joins mutable Finding state to rewrite a sealed snapshot or file.

Before a completion writer, the workflow prepares exact Git completion and a
canonical JSON-shaped Bundle basis without writing a file. In the short writer
it rereads Task, Contract, authority snapshot, criteria, target/capture,
manifest, selected verification basis, Review Receipts/Findings, and completion
proposal; reevaluates all current gates; and computes the complete payload and
size. Links, snapshots, Bundle, cycle, Task update, event, and source-generation
advance commit atomically. Drift, invalid binding/class/digest, or the 16-MiB
cap rolls back the whole savepoint. Historical rows are immutable; reopen and a
later completion create a fresh target, cycle, and Bundle.

`CanonicalStatePaths` and `DatabaseTarget` add only `evidence_root`,
`evidence_index`, `evidence_bundles`, and `evidence_lock` beneath
`state/current/evidence`. The fixed Bundle filename is its Bundle ID plus
`.json`; no caller path exists. Resolution rejects links, reparse points,
nonregular files, containment changes, DB aliases, unknown recognized names,
and unsafe staged content. Generated Evidence remains outside manifests and
source commits.

Projection state stores nonnegative source generation, nullable published
generation no greater than source, nullable index digest, and the closed
maintenance outcome/time. Every cycle insertion advances source generation
exactly once, including the legacy bridge; no other write does. The projector
captures project/schema, generation, all cycles, native Bundles/members, and
legacy state in one query-only transaction and closes SQLite before rendering.
One encoder owns the exact canonical JSON and array orders. There is no clock
input, so same-basis repair is byte-identical.

Under the zero-wait Evidence lock publication validates or atomically writes
all required Bundle files through flushed same-directory temporaries, flushes
and atomically replaces the index last, conditionally records the captured
generation/digest in a short transaction, then rechecks once and permits at
most one follow-up capture. The index is the commit point. Missing, ahead,
behind, wrong-project/version/digest, unsafe, or otherwise mismatched projection
is never consumed. SQLite stays authoritative and setup regenerates only
one-way; no JSON is imported. Failure or contention keeps the last-good index,
records the fixed maintenance outcome, and does not undo the business mutation.

````

## Captured Section 6: Test-Only Independent Evidence Reader

Source path: `docs/design.md`

Source range: `## Test-Only Independent Evidence Reader` through immediately before `## Completion Cycle History`.

````markdown
## Test-Only Independent Evidence Reader

`tests/evidence_reader_oracle.py` owns the retained independent Evidence
index/Bundle reader and validation core. `tests/evidence_reader_codec.py` owns
its pure canonical JSON codec; `tests/evidence_test_support.py` owns reusable
fixtures with a separate reference encoder. The oracle preserves the full
selected index entry, including its Bundle-version discriminator, and the exact
Bundle envelope or explicit legacy absence. Its existing regression and M24
integration tests remain the consumers.

These test helpers import neither SQLite/storage nor the producing Evidence
semantic validator. They may reuse the existing bounded physical-filesystem
primitives in `state_paths.py`; independence does not require duplicate I/O or
Windows infrastructure. They have no Analyzer descriptor, packet, report,
outbox, process, model, or publication responsibility.

The installable package contains no M23 Analyzer or standalone Evidence-reader
runtime. Analyzer-only path derivation is removed from `state_paths.py` and
`state_resolver.py`; shared Evidence paths and filesystem primitives retain
their existing owners and behavior. Old ignored analysis artifacts are left
inert, with no cleanup or migration. There is no replacement runtime reader or
reporting adapter.

````
