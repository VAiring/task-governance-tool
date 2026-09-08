# Evidence Implementation Design

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
[schema-v21 Runner gate basis](design.md#schema21-runner-gate-basis-design),
[current persistence](design.md#schema22-reservation-cleanup-design),
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
  option normalization, and provenance digest; it owns no SQLite access.
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
  captures through the Evidence validation repository API, calls those retained
  builders, and returns the existing refresh result or physical status.
  It owns physical path checks,
  lock/temp/rename handling, Bundle-first/index-last publication, generation
  comparison and outcome-recording calls, last-good preservation, and setup repair.
  Capture closes its read connection before rendering; the separate index
  generation guard retains its read connection through atomic replacement.
  Completion-time construction stays in its existing transaction boundary;
  construction-side storage value types remain shared without a DTO layer.

`storage.py` retains shared SQLite admission, recovery validation policy, native
cycle coordination, and cycle persistence. It consumes the Evidence and Review
repositories without changing global versus selected validation scope;
necessary shared value types remain there. The
projection metadata repository owns only its state rows.
`tasks.py` and `contracts.py`
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

<a id="test-only-independent-evidence-reader"></a>

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
