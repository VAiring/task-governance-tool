# Evidence Implementation Design

Numerical usage publication is independent of the sealed Evidence publisher
and its core ledger. Its store, replay and physical publication responsibilities
belong to [immutable usage persistence](database-design.md#immutable-usage-persistence).
It reuses low-level physical-file primitives, not Bundle mutation or Viewer
maintenance; the existing Evidence schema, bytes and publication protocol stay
unchanged.

This document owns provenance, Evidence Ledger capture, Bundle construction and
projection, publication, and the test-only independent reader delegated by the
[implementation design](design.md#provenance-evidence-ledger-and-bundle-structure),
for the behavior in the [Evidence specification](evidence-specification.md).
[Review operations](review-completion-design.md),
[native completion transactions](review-completion-design.md#native-done-and-reopen-transactions),
[manual Receipt integration](review-completion-design.md#current-schema-v22-manual-receipt-arm-and-bundle-integration),
and [Task operations](task-operation-design.md) retain their own owners.
Shared [runtime ownership](design.md#runtime-module-boundaries),
[connection/transaction rules](design.md#journal-and-connection-rules),
[schema-v21 Runner gate basis](database-design.md#schema21-runner-gate-basis-design),
[current persistence](database-design.md#schema22-reservation-cleanup-design),
[Runner execution](runner-execution-design.md),
[post-commit coordination](setup-state-design.md#post-commit-coordinator),
[privacy/failure rules](design.md#privacy-safety-and-failure-boundaries), and
[global test design](design.md#validation-and-test-design) remain with the owners
routed by the [authority index](authority.md).

<a id="provenance-evidence-ledger-and-bundle-structure"></a>

## Provenance, Evidence Ledger, And Bundle Structure

This section is the current implementation owner for Review
provenance, schema-v18 capture, schema-v19 Bundle construction and projection,
and their schema-v20-through-v22 Runner integration. They preserve legacy rows
without inventing evidence, keep SQLite access in the storage/repository
boundary, and exclude
the retired `derived_analysis` reservation from current schema v22.

### Capture And Projection Module Ownership

- `review_provenance.py` owns the closed enum/matrix, public v1/v0/null union,
  option normalization, provenance digest and explicit v2 dispatch; it owns no SQLite access.
- `review_answer.py` owns bounded answer decoding and privacy-first failure
  classification; `review_answer_values.py` owns closed collection metadata and
  native/public capture values; `review_answer_provenance.py` owns the pure v2
  builder, contextual digest and validator. `review_answer_repository.py` owns
  immutable v2/source/dispatch/capture persistence and comparison in the caller's
  core transaction. Operational journals and transport files stay separate.
- `review_repository.py` owns stored Receipt/Finding projection validation,
  provenance/code relation reads, and Receipt/provenance insertion on the
  caller-owned connection. Reference creation, events, and outer transaction
  ownership remain in the existing service/storage boundary.
- `evidence_ledger.py` owns assurance/producer validation, authority-basis
  canonicalization, whole-field criteria, Evidence Reference projections and
  digests, and public allow-lists. Active link, Bundle, omission, and Runner
  source assembly is delegated to the owning service/projection boundary.
- `evidence_repository.py` owns authority/criteria capture and reads, stored
  manifest validation, Reference reads and row comparison, and manifest,
  Reference, and criterion-link persistence. These operations reuse the caller's
  connection and transaction. The selected Task and Runner readers consume its
  manifest-only Reference validation; the shared all-source coordinator reuses
  the same manifest expectations and owner-indexed Reference query without
  narrowing its source set. Selected Task all-kind inventory remains separate.
- `artifact_manifest.py` owns bounded shell-free Git tree/index observation,
  exact artifact-entry normalization, deterministic rename pairing/order, and
  manifest digests. It reuses safe process and stable-snapshot primitives from
  `git_snapshot.py` without routing full manifests through Review Packet.
- `evidence_projection.py` owns the codec, canonical Bundle/index JSON and
  digest validation, native Bundle construction, stored Bundle reconstruction,
  and rendering of captured `EvidenceProjectionBasis` values.
- `completion_bundle_repository.py` owns prepared Bundle validation and
  Bundle/member/Finding-snapshot writes on the caller's connection, reusing
  local criterion-link persistence and the shared Evidence validation
  repository's stored Bundle reader.
  Native sealing, cycle coordination, and outer commit/rollback stay with their
  existing owners.
- `evidence_validation_repository.py` owns all-source and selected Evidence
  validation, Bundle row/relation and selected-history validation, stored/native
  Bundle acquisition, and Evidence projection snapshot assembly. It consumes
  the existing local Evidence, Runner graph, and prepared Bundle validators
  without duplicating them. Global versus selected scope, exact historical
  generation, and same-snapshot validated values retain their existing meaning.
- `evidence_projection_metadata_repository.py` owns projection-state read/seed,
  source-generation advance, and outcome records. Cycle insertion and its
  generation advance retain one caller-owned writer; the outer outcome entry
  keeps the initialized writer boundary. Viewer generation logic stays separate.
- `evidence_publication.py` consumes `DatabaseTarget` and observation time,
  captures complete publication documents through the Evidence validation
  repository API and returns the existing refresh result or physical status.
  It owns physical path checks,
  lock/temp/rename handling, Bundle-first/index-last publication, generation
  comparison and outcome-recording calls, last-good preservation, and setup repair.
  Capture validates and constructs documents in its query-only transaction,
  releases the raw basis and closes its reader before physical file/temp I/O;
  the separate index generation guard retains its reader through replacement.
  Completion-time construction stays in its existing transaction boundary;
  construction-side storage value types remain shared without a DTO layer.

`storage.py` retains shared SQLite admission, recovery validation policy, native
cycle coordination, and cycle persistence. It consumes the Evidence and Review
repositories under the owning operation's global or selected validation scope;
necessary shared value types remain there. The
projection metadata repository owns only its state rows.
`tasks.py` and `contracts.py`
capture authority inside existing savepoints; `verification_receipts.py`
derives subject-v1 bindings; it and `reviews.py` create typed References inside
their source writes; target capture creates one manifest and subject-capable
basis atomically; completion passes a fully prepared Bundle basis into its
native-cycle savepoint. No feature module opens raw SQLite.

Native completion validates its newly persisted cycle through the existing
selected-cycle Evidence and selected Bundle-history validators, followed by
its stored projection readback. It does not invoke whole-store Bundle replay
after insertion. The cycle/source-generation advance remains atomic; global
projection/Setup/recovery consumers and legacy cycle archival retain full-store
validation. See [completion review admission](database-specification.md#completion-review-admission).

`state_paths.py` and `state_resolver.py` alone own the fixed Evidence root,
index, Bundle directory, and lock. `state_transition.py` recognizes those
generated files only in bounded setup. `maintenance.py` may retry a due
projection after a changed mutation, but only a cycle insert advances source
generation. `setup.py` is the repair owner. `cli.py` owns only existing
setup/doctor fields and warnings and has no Evidence export/import branch.

### Schema-v18 Capture And Subject Foundation

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

### Evidence Reference And Manifest Construction

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

Runner source construction preserves the resolution's existing policy identity
and observation measurement values. Core Reference and Bundle validators reuse
the pure value-model accounting predicate, with each consumer retaining its
own admission rules from the
[Evidence format contract](evidence-specification.md#canonical-evidence-bundle-and-index-formats).
The [Runner owner](runner-execution-specification.md#runner-policy-and-accounting)
defines measurement meaning; projection adds neither a current-OS comparison
nor a null-to-zero conversion. Existing JSON members and digest domains remain
unchanged.

`artifact_manifest.py` reads complete Git leaves, not Review Packet summaries.
For snapshots it compares exact HEAD with stable stage-0 index; for commits it
compares the exact commit with its first parent or empty tree. Leaves normalize
to `relative_posix_path mode full_object_id`. A bytewise path merge produces
add/delete/modify; a second pass converts only a unique exact mode/object
delete-add pair to rename. Ambiguous duplicates and content-changing moves stay
delete+add. One pure sorter applies the [artifact-entry tuple](review-completion-specification.md#git-snapshot-and-target-binding) with null first and
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

### Schema-v19 Bundle And Evidence Publication

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
later completion create a fresh target, cycle, and Bundle. Schema 28 retains
the exact format-2 encoding and adds source-28 admission after schema 27.
Source-19 through source-27 Bundles remain readable without resealing. The
execution-to-cycle association is a separate immutable same-transaction link,
not a new Bundle member or a rewrite of historical cycles. The waiver reason
is checked on Task/current gate and immutable cycle, but is deliberately absent
from the authority snapshot and Bundle payload; the Packet carries it for review.

`CanonicalStatePaths` and `DatabaseTarget` add only `evidence_root`,
`evidence_index`, `evidence_bundles`, and `evidence_lock` beneath
`<governed-project>/.taskgov/current/evidence`. The fixed Bundle filename is its Bundle ID plus
`.json`; no caller path exists. Resolution rejects links, reparse points,
nonregular files, containment changes, DB aliases, unknown recognized names,
and unsafe staged content. Generated Evidence remains outside manifests and
source commits.

Projection state stores nonnegative source generation, nullable published
generation no greater than source, nullable index digest, and the closed
maintenance outcome/time. Every cycle insertion advances source generation
exactly once, including the legacy bridge; no other write does. The projector
captures project/schema, generation, all cycles, native Bundles/members, and
legacy state in one query-only transaction. Its publication entry validates
the complete ledger, cycles and Bundle relations, then renders each raw basis
once in that same read. Canonical Bundle construction is also record validation:
it retains the stored digest/payload-size comparison and document size bound.
Each successful construction is immediately reduced to document bytes and the
Bundle/file digests needed for the index. The full index is validated and built
before any Bundle is physically published. Only document bytes, IDs, digests and
generation leave this entry; payload dictionaries, payload bytes, envelopes and
the raw basis do not survive into publication. The capture reader closes before
physical file/temp I/O. A later generation capture releases the previous
documents and independently validates its new source.

This reuse is confined to one capture/render call. Standalone storage and raw
basis validators still perform their complete canonical validation, and pure
rendering of a separately supplied basis validates its records independently.
No certificate is attached to a shared value type or carried into a fresh guard,
writer, transaction or later capture. Native completion and the independent
Reader retain their existing validation and value contracts.

Retained publication memory still scales with all Bundle document bytes plus
the complete index and its metadata. This is not streaming, spooling, an O(1)
memory bound or a persistent cache. Capacity comparisons include acquisition of
the raw basis and canonical construction, not just post-capture rendering.
One encoder owns the exact canonical JSON and array orders. There is no clock
input, so same-basis repair is byte-identical.
`evidence_projection` owns native Bundle format selection and the shared index
format/domain selector. The completion writer stores the version from the
validated Bundle plan instead of selecting it again. Structural validators and
the independent test Reader retain their existing admission responsibilities.

Under the zero-wait Evidence lock publication validates or atomically writes
all required Bundle files through flushed same-directory temporaries. Before
capture, `_capture` reads maintenance and generation through structural
admission. Routine disabled/current observations skip full-source validation
and Bundle construction, including after lock acquisition or a bounded
follow-up. A due capture, force repair or physical-status inspection validates
the complete database in that same read snapshot before constructing a basis.
The index-generation guard retains its global admission. Publication writes
Bundles first, flushes
and atomically replaces the index last, conditionally records the captured
generation/digest in a short transaction, then rechecks once and permits at
most one follow-up capture. The index is the commit point. Missing, ahead,
behind, wrong-project/version/digest, unsafe, or otherwise mismatched projection
is never consumed. SQLite stays authoritative and setup regenerates only
one-way; no JSON is imported. Failure or contention keeps the last-good index,
records the fixed maintenance outcome, and does not undo the business mutation.

## Continuation Graph Validation

Schema-30, schema-31, schema-32, schema-33 and schema-34 sealing use Bundle 4 and index 3 under their versioned digest domains,
without changing a previously sealed format. Format 4 always carries nullable
`review_continuation_basis`; continuation completions freeze graph 2, whose
only added root field is `collected_captures`. Ordinary completion keeps null.
Source versions and index entry discriminators route exact versions; formats
one through three never accept the v2 arm through a widened old validator.

Pure graph validation first checks each collected public capture's closed
metadata, basis and evidence digest, then consumer/source ancestry and the
source continuation's exact ID/sequence/selection digest, same full
execution basis/preparation/route, slot/session uniqueness, correction-turn
relation and registered consumption. Every transfer of one source preserves
identical source metadata even in superseded historical null-link captures.
It compares the linked v2 provenance's assignment, request/attestation,
original/payload metadata, source identity and three observation sources,
and compares the source binding's reviewer key with the linked Receipt's key
in both native relation and frozen graph validation. It then validates
Receipt/Finding normalization, session binding, Reference digest
and effective judgment selection. Current changes-requested and unable heads
both prevent sealing. Digest recomputation cannot waive graph relationships.

Native capture creation derives the path-free projection and its public
evidence digest atomically from the complete validated native metadata used
for the physical seal. Native sealing/reentry and projection enumerate the
frozen graph's consumer continuation IDs, acquire each consumer's native capture
inventory once, and compare the complete ordered deterministic projection with
the frozen list. Checking only supplied capture rows cannot detect omissions.
Consumers outside the frozen graph are excluded so later history preserves old
Bundle bytes. The comparison does not accept an independently supplied public
claim. Public graph validation reads no transport file and
does not recompute the private metadata seal. Initial-context registration
history remains a native source/inventory fact; the public graph validates
declared consumed captures without inventing absent prior registration.

`review_continuation_evidence.validate_graph` composes pure checks in dependency
order: closed row indexes; complete continuation history and reviewer results;
retained-original ancestry, metadata and consumption; optional Bundle basis;
Receipt assertions, References and session bindings; recomputed effective heads;
Finding assertions and explicit resolution links; then optional Bundle Receipt
selection and the existing canonical size bound. Error normalization remains at
this shared entry. Each helper consumes only call-local row indexes or validated
history and judgment values; it does not mutate, cache or persist the graph.

History validation checks the continuation row's integer target generation
before comparing it with the validated basis. Receipt validation likewise
checks the judgment metadata's integer revision before comparing it with the
Receipt revision. Both use the existing strict positive signed-64-bit predicate;
value equality alone cannot establish either field's type.

The same entry serves sealing, projection and historical reentry. The repository
still acquires source rows and compares frozen assertions with native evidence
in its caller's transaction, as described by
[continuation storage](database-design.md#conditional-review-continuation-storage).
The pure check alone proves declared consistency, not current authority.
`ContinuationEvidenceValidationTests` in the registration suite and the
retirement suite exercise responsibility boundaries and the whole entry with
the same corruptions; snapshot and native Bundle tests cover their composition.

<a id="test-only-independent-evidence-reader"></a>

## Test-Only Independent Evidence Reader

`tests/evidence_reader_oracle.py` owns the retained independent Evidence
index/Bundle reader and validation core. `tests/evidence_reader_codec.py` owns
its pure canonical JSON codec; `tests/evidence_test_support.py` owns reusable
fixtures with a separate reference encoder. The oracle preserves the full
selected index entry, including its Bundle-version discriminator, and the exact
Bundle envelope or explicit legacy absence. Its consumers include the
independent-reader regression tests, Runner schema-v20 Evidence compatibility
(`tests/test_m242_evidence_compatibility.py`), schema-v21 compatibility
(`tests/test_m243b_schema21_compatibility.py`), and legacy/fresh completion
acceptance (`tests/test_m244b_legacy_fresh_acceptance.py`).

These test helpers import neither SQLite/storage nor the producing Evidence
semantic validator. They may reuse the existing bounded physical-filesystem
primitives in `state_paths.py`; independence does not require duplicate I/O or
Windows infrastructure. They have no Analyzer descriptor, packet, report,
outbox, process, model, or publication responsibility.

`tests/review_continuation_evidence_oracle.py` separately interprets the
[complete continuation graph](evidence-specification.md#conditional-continuation-evidence).
It recomputes ancestry, retained originals, assertion/Reference digests,
resolution links and selected heads without calling the product graph helpers.
Refactoring the product validator does not transfer that independent obligation.
Its graph-two arm independently checks the path-free collected metadata,
contextual v2 provenance, transfer/ancestry and Receipt relations. Bundle-four
and index-three readers retain all old encodings and version-specific domains;
they do not call the producing validators or inspect private original files.

The independent reader implements the same
[captured-policy accounting rules](evidence-specification.md#canonical-evidence-bundle-and-index-formats)
locally without importing the producing accounting predicate. Its regression
cases distinguish preserved opaque-policy/full-metric Bundles from the exact
POSIX policy's unmeasured auxiliary values, reject invalid combinations, and
retain original source versions, bytes, and digests.

The installable package contains no Analyzer or standalone Evidence-reader
runtime. Analyzer-only path derivation is removed from `state_paths.py` and
`state_resolver.py`; shared Evidence paths and filesystem primitives retain
their existing owners and behavior. Old ignored analysis artifacts are left
inert, with no cleanup or migration. There is no replacement runtime reader or
reporting adapter.
