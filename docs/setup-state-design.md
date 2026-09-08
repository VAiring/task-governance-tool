# Setup And State Operation Implementation Design

This document owns the fixed state resolver, identity/binding/relocation, and
setup/Doctor/backup/maintenance structure delegated by the
[implementation design](design.md). Product behavior belongs in the
[Setup and state operation specification](setup-state-specification.md).
Shared [runtime ownership](design.md#runtime-module-boundaries),
[connection/transaction rules](design.md#journal-and-connection-rules),
[migration](design.md#migration-sequence),
[current persistence](design.md#schema22-reservation-cleanup-design),
[privacy/failure rules](design.md#privacy-safety-and-failure-boundaries), and
[global test design](design.md#validation-and-test-design) retain their owners.
[Stored Task validation](task-operation-design.md#shared-stored-task-rowbatch-validator),
[Evidence publication](evidence-design.md#schema-v19-bundle-and-evidence-publication),
and [Viewer publication](viewer-design.md#snapshot-and-publication) remain directly
coupled responsibilities. The [authority index](authority.md) routes shared
contracts without duplicating them here.

<a id="fixed-state-resolver"></a>

## Fixed State Resolver

One physical package owns:

```text
state root       <physical-skill>/state
transition lock  <state-root>/taskgov-state.lock
fixed root       <state-root>/current
database         <fixed-root>/taskgov.sqlite
managed backups  <fixed-root>/backups
Evidence index   <fixed-root>/evidence/index.json
Evidence bundles <fixed-root>/evidence/bundles
Evidence lock    <fixed-root>/evidence/taskgov-evidence.lock
Viewer           <fixed-root>/viewer/task-viewer.html
Runner root       <fixed-root>/verification-runner
Runner lock       <fixed-root>/verification-runner/taskgov-verification-runner.lock
Runner attempts   <fixed-root>/verification-runner/attempts
Runner quarantine <fixed-root>/verification-runner/quarantine
```

The resolver returns canonical paths, the in-memory governed root/hash/display
observation, stored identity/binding when available, source schema, layout
state (`missing`, `fixed_current_v1`, or `legacy_projects_v1`), binding state
(`unbound`, `matching`, or `relocation_required`), and an optional deep
setup-only recovery/legacy observation. None of its raw paths or path hashes
crosses a formatter.

For the Runner subsystem, `CanonicalStatePaths` derives the four fixed paths above
and `DatabaseTarget` carries them to the parent service. Internal test targets
derive the same names beneath their injected database parent. No service,
repository, lifecycle, or process module reconstructs the fixed root, and the
layout is created only after an exact Runner route has passed pre-T1
preflight. The lifecycle owner continues to require the existing physical
fixed root as `Runner root.parent`.

Fixed-primary normal consumers validate only the authoritative primary and
derive canonical artifact targets. Setup, recovery, transition, and legacy
discovery use the deep bounded inventory projection. The caller selects the
projection mechanically; it is not a CLI or LLM choice. Every DB-backed leaf
uses the same resolver observation. Feature code may not reconstruct a
project ID or state path.

<a id="stable-project-identity-binding-and-relocation"></a>

## Stable Project Identity, Binding, And Relocation

### Schema V14 Identity

Fresh setup creates `project_id=tg_project_<uuid4 hex>`,
`identity_scheme=uuid_v1`, binding generation 1, and reason `fresh_setup`.
Migrated databases keep their legacy sanitized-name-plus-12-hex
`legacy_path_v1` ID. Durable business rows never change project ID.

`project_meta.canonical_path_hash` is the current binding, not identity.
Schema v14 also stores:

- `binding_generation >= 1`;
- reason `legacy_migration`, `fresh_setup`, or `confirmed_relocation`;
- canonical binding timestamp;
- bounded sanitized display name; and
- the exact cleanup state triple: either pending `0` with null inventory/hash,
  or pending `1` with canonical inventory and its lowercase SHA-256.

`project_path_binding_history` is append-only and keyed by project plus
generation. Generation 1 has no previous hash; each later entry's previous
hash equals the prior current hash and has reason `confirmed_relocation` plus a
token digest. Current metadata must equal the maximum history row. Triggers
prevent identity/creation changes, project deletion, history update/delete,
and invalid cleanup triples. Validation checks consecutive lineage,
scheme-specific IDs, lowercase 64-hex hashes, canonical timestamps, bounded
display text, and canonical cleanup inventory.

`project_binding_repository.py` compares identity, expected generation, and old hash
inside `BEGIN IMMEDIATE`, rejects a same-hash change and signed-64-bit
generation overflow, inserts generation `N+1`, updates the current binding,
and increments Viewer source generation in the same transaction. A missing or
overflowing Viewer row rolls the whole binding change back. The token digest,
not token text, is retained in history.

### Bounded Legacy And Recovery Discovery

Legacy inspection runs only when fixed primary and higher-precedence fixed
recovery are absent. It scans only direct entries under `state/projects`,
consumes at most 64, and rejects an unknown entry, a 65th entry, link/reparse
component, escape, invalid candidate, or multiple candidates without creating
or writing anything.

The candidate validator checks:

- exact legacy directory/database names and one project row;
- contiguous schema and required objects without later markers;
- operational journal mode, quick check, and foreign keys;
- identity/scheme, directory equality, and v14 binding lineage;
- every recognized managed backup as the same identity/scheme with a lineage
  prefix of the mechanically newest source;
- coherent schema-specific backup pointer/rows/files, with at most 21
  retained-plus-in-flight generation identities;
- canonical database, backup, Evidence, Viewer, lock, and recognized temporary paths; and
- at most one bounded regular temporary for each exact restore, backup, and
  Viewer/Evidence temporary-name grammar.

Recovery classification is deliberately two-phase. The resolver opens every
recognized candidate and completes the physical, SQLite, schema, identity,
lineage, metadata, and whole-set checks above against the mechanically newest
structural head. It then calls the storage-owned exact Task-verification
validator with that candidate's source schema. The validator returns normally
or raises a sanitized internal privacy/capacity rejection; every other storage
failure remains structural and set-fatal. The current implementation accepts
at most 500 characters through schema v17 and 1,000 at schema v18-v22. The source
schema is selected before migration or recovery publication; no recovery-only
limit or migration laundering exists.

The structural phase also validates each candidate as the immutable
pre-publication repository snapshot for its ordered physical prefix. One
set-wide generation registry requires every occurrence of a generation ID in
a filename, embedded row, or maintenance pointer to carry identical complete
metadata. A v11+
candidate has its pointer at its last embedded generation row, exactly its own
generation is file-only in that prefix, and bounded row-only generations are
strictly older. A v10 candidate has no rows and its optional pointer is older
than the candidate. With a retained physical predecessor, that pointer equals
the predecessor's complete metadata; only the first retained candidate may
refer to an older generation ID absent from the complete physical candidate
set. Candidate schema versions are not required to be monotonic because an
older fallback can produce a new pre-migration backup after a newer rejected
head.
The storage layer owns one raw SQLite integer-storage validator used by the
resolver's generation-row and maintenance-pointer reads and by ordinary
maintenance/managed-generation repository reads. It accepts only
`type(value) is int` before semantic range validation; present `REAL`, `TEXT`,
`BLOB`, or other non-`INTEGER` values are never normalized with `int(...)`.
Resolver and setup paths translate that defect to the structural
`project_state_unreadable` result before selection or publication, while the
same helper keeps ordinary reader and setup-reentry behavior aligned.
Candidate-derived rollback-journal, WAL, and SHM names, including filesystem
case aliases, are rejected regardless of size. The storage classifier scans
all Task rows so a later malformed SQLite value cannot be hidden by an earlier
local rejection; privacy wins over capacity only after that structural scan
completes.

Each internal managed-backup observation includes metadata, source schema,
stored project/binding lineage, content eligibility, path, and the regular
file's device/inode/size/mtime identity. Fixed and legacy backup-only
resolution retain the complete ordered observation set, select the newest
content-eligible candidate at the current binding, and expose both the
selection and full set to setup. Setup requires its recovery selector to match
that exact selected observation, then compares the complete observation again
while holding the artifact lock. Any candidate or classification drift is a
restore failure; there is no under-lock reselection.

Content eligibility also requires exact identity scheme, binding generation,
canonical path hash, and full binding lineage equality with the structural
head. Earlier valid lineage-prefix artifacts remain structurally observable
but cannot be selected across a binding generation, including an A-to-B-to-A
path history.

Fixed recovery carries that immutable ordered inventory into the restore
service. The service reclassifies the complete set at entry, before private
repository normalization, and immediately before its no-replace canonical
link; both deep resolver results must equal the planned observation and the
newest eligible member must still be the planned selection. Before
normalization, the private SQLite copy must exactly retain the selected
candidate's project/binding, source schema, generation rows, maintenance
pointer, and content classification. After normalization it is opened again
through the schema-aware resolver validator and must have every required
object plus the exact all-artifact row set and mechanical-head pointer. Legacy
backup-only publication pins the selected source identity and the same
repository observation, validates both
the source and copied stage, checks every copied backup against its planned
identity, and resolves the complete legacy source again immediately before the
no-replace stage rename. Any mismatch maps to `setup_restore_failed` and the
private stage is removed without publishing fixed state.

The shallow backup-service inventory refresh runs before each fixed deep
resolver comparison. The publication-side deep comparison is the last
source-set check before directory/canonical-destination guards and the
no-replace link, so a weaker rescan cannot invalidate its conclusion. When a
primary is present, ordinary reads keep primary precedence, but setup and
staged validation still invoke the Task-verification classifier for every
backup: local privacy/capacity results are ignored for selection, while a
malformed stored value remains structural and set-fatal.

The successful return from `restore_managed_backup` is the durable
publication boundary because it occurs only after the canonical no-replace
link. Setup appends `database_restore` immediately on that return, then checks
the returned schema and reads post-link setup state. Failure in either later
check keeps `setup_restore_failed` but carries the durable restore prefix;
failure before return keeps the prefix empty. The restore primitive always
removes its sibling temporary in `finally`, and a retry observes the canonical
primary instead of reselecting or publishing another candidate.

The recovery-specific backup scan uses the same storage validator and excludes
only its privacy/capacity rejection from selection. A structurally coherent set
with only local rejections has no eligible candidate and reaches
`setup_restore_failed`; corrupt, foreign, unsafe, duplicate, overflow, or
otherwise structurally invalid recognized material retains its specific
resolver result where applicable and otherwise reaches
`project_state_unreadable`. Recovery normalization
keeps every structurally coherent artifact in the ordinary generation
row/file/pointer envelope even when the copied candidate is older; local
rejection never hides or legalizes a missing or mismatched relation. It does
not rewrite rejected artifacts, while later ordinary retention keeps its
existing authority over managed generations.

Focused recovery-boundary ownership is split by behavior rather than accumulated in one
module. `test_m214b_recovery_boundaries.py` owns current fixed-layout,
selection, metadata, and TOCTOU behavior in the integration lane;
`test_m214b_legacy_recovery_boundaries.py` owns legacy-primary, schema-v10, and
mixed-schema recovery in the release lane. Shared mutation helpers live in the
non-discovered `m214b_test_support.py`, while the common tree snapshot helper
captures complete managed-state names, kinds, sizes, and contents for no-write
assertions. New recovery cases extend their owning module instead of restoring
one mixed oversized file. TOCTOU coverage observes the phase order around a
drift injection, proves that the last deep resolver comparison follows the
final shallow inventory refresh, and proves that the canonical publish call is
not reached; it does not freeze a private helper call count.

Other contained names are opaque user material and are preserved. A fixed
primary always wins even when invalid; the resolver never falls back around
it. After whole-set validation, fixed missing-primary recovery selects the
newest content-eligible current-binding generation and requires the
mechanically newest binding head to match the governed root. Earlier
generations may be exact lineage prefixes. A moved backup-only legacy source,
foreign/divergent identity, corrupt primary, or unsupported journal is never
a relocation fallback.

Same-binding legacy primary or backup-only state is setup-publishable to fixed
state. Normal commands report migration-required until that atomic
publication. A primary-backed moved source is read-only
`project_relocation_required`; a moved fixed primary is likewise usable only
through explicit relocation confirmation.

### Relocation Token

`relocation.py` emits an opaque, non-secret token:

```text
tgr1.<unpadded-base64url-canonical-json>.<sha256>
```

Its exact payload binds version 1, project ID/scheme, current binding
generation, distinct old/new 64-hex hashes, source layout, source schema, issue
time, and expiry exactly 900 seconds later. A fixed-current source may be
schema v1-v22; `legacy_projects_v1` is capped at the explicit v14 transition,
while ordinary legacy discovery remains v1-v13 plus that v14 shape. Canonical JSON uses
sorted keys, compact separators, UTF-8, and `ensure_ascii=True`; the checksum
is SHA-256 over ASCII `tgr1.<payload>` and is compared with
`hmac.compare_digest`. The whole token is capped at 2,048 ASCII bytes.
Padding, noncanonical encoding, duplicate/missing/extra keys, bad types,
ranges, or times are rejected.

Preview fixes the clock and is no-write. A valid token requires
`issued_at <= now < expires_at`, but handling first checks structural
validity/checksum and successful-token digest reuse. A successfully applied
token therefore remains `relocation_token_used` after expiry. A changed
source, identity, generation, binding, or governed-root observation is stale;
a matching context is not relocation-required. Rejected token values never
appear in output.

`setup --read-only` may emit the fixed six-key relocation preview containing
the token and expiry but no path/hash. Agents must present that preview and
wait for explicit current approval; they never auto-confirm.
`--read-only --confirm-relocation` is rejected before scope inspection.
Write-mode mismatch without the exact token remains no-write.

### Staged Publication And Cleanup

Write-mode setup acquires locks in this order:

1. zero-wait package state-transition lock;
2. relevant source/fixed managed-backup artifact lock; and
3. short private or canonical SQLite transactions.

It never holds a SQLite writer while copying, rendering, invoking Git,
publishing a directory, or cleaning legacy files. Normal business commands do
not acquire the transition lock; canonical rebind instead uses SQLite
compare-and-swap.

Legacy publication and confirmed moved-legacy publication share one bounded
primitive:

1. Revalidate scope, ignore, destination absence, journal, schema, integrity,
   identity/binding, artifact inventory, and optional token under the
   transition lock.
2. Create exclusive, durably flushed paired
   `state/.current-stage-<32hex>.owner` and
   `state/.current-stage-<32hex>` entries. The compact owner record is at most
   2,048 ASCII bytes and binds version, stage ID, project ID, and source
   inventory fingerprint without a path.
3. Admit only database/optional rollback journal, up to 21 managed backups,
   canonical artifact locks, Viewer HTML, and at most one recognized bounded
   temporary per class; at most 32 regular files are staged, each bounded by
   source database size plus 16 MiB. Unsafe, unknown, duplicate, oversized, or
   unowned residue is preserved and stops.
4. Copy the selected database through SQLite backup and copy only validated
   managed/last-good artifacts. Reconcile backup state inside the private
   stage, apply required migrations and maintenance configuration, apply a
   confirmed binding change, persist cleanup intent, and render/validate the
   current Viewer there.
5. Build canonical inventory JSON
   `{"entries":[...],"v":1}` from 1-32 unique recognized source files. Each
   entry contains only kind, relative POSIX name, lowercase SHA-256, and size;
   sorting is by UTF-8 name bytes, canonical bytes are at most 16,384, and the
   fingerprint is their SHA-256.
6. Validate the complete staged database, history, artifacts, Viewer,
   sidecar absence, and binding.
7. Rename the complete directory no-replace to absent `state/current`, then
   remove the external owner marker. A concurrent destination is never
   overwritten.

A pre-publication crash leaves the source authoritative and only a fully
validated owned residue eligible for bounded setup cleanup. A
post-publication crash leaves fixed state authoritative and legacy cleanup
pending. A port without equivalent no-replace directory semantics must fail
closed.

Pending legacy cleanup derives its retirement directory as
`state/.legacy-cleanup-<sha256(project-id)>`. The persisted canonical
inventory, never directory enumeration, is the allow-list. Each recorded file
must be absent or match kind, size, and SHA-256 at exactly one old/retirement
location. Cleanup first no-replace moves remaining old files, then deletes
verified retirement files one by one and only proven-empty owned directories.
Unexpected or changed content stops; unrelated old content remains. One short
transaction clears cleanup state after the recorded set and retirement
directory are absent. Token-free setup resumes a valid pending cleanup.

Fixed-state relocation performs only the binding transaction, then publishes
the Viewer by its generation contract. A crash or Viewer failure after commit
leaves relocation durable and Viewer due; token-free setup repairs it. It does
not manufacture an immediate backup, so loss of the primary before later
managed publication is an explicit bounded recovery window.

<a id="setup-doctor-backup-and-maintenance"></a>

## Setup, Doctor, Backup, And Maintenance

### Shared Scope Preflight

Setup and doctor validate a physical target directory, canonical package,
layout, state ownership, and package identity. If the target or an ancestor
has a `.git` marker, `project_scope.py` runs exactly one bounded, shell-free
effective-ignore command before SQLite:

```text
git -C <governed-target> -c core.fsmonitor=false \
  check-ignore --quiet --no-index -- <target-relative-state-directory>
```

The operand is exactly the ordinary
`.agents/skills/task-governance-tool/state/` or self-host
`task-governance-tool/state/`, with forward slashes and directory semantics.
The process uses safe Git environment, null stdin/stdout/stderr, and a
two-second timeout. Return 0 is accepted; every other result is
`state_ignore_required`. No marker means a valid non-Git target and no
process. The governed target is never re-rooted. Normal task/handoff/review
commands perform no ignore process.

### Doctor

Doctor combines:

1. bounded package-manifest inspection;
2. the conditional one-process ignore observation; and
3. at most one journal preflight and coherent project snapshot.

It does not claim cross-source atomicity. `project_state` owns layout and DB
readiness. When unavailable, task/handoff/maintenance details use fixed
unavailable objects. Package modified/unknown is advisory even when project
state produces exit 2. Relocation mismatch is a successful
`relocation_required` project-state advisory, never a token. Every recognized
advisory has `suggested_action=continue`; doctor creates no lock, directory,
sidecar, database, event, backup, Evidence, or Viewer.

The maintenance projection adds one fixed `evidence` object beside backup and Viewer with `code`, `due`, `source_generation`, `published_generation`, `last_success_at`, and `last_outcome`; doctor reads stored facts only.

The coherent project snapshot loads complete Task rows once and validates the
whole batch with the shared stored-Task/Contract validator before computing Task
counts. On a
Task fault, doctor maps project state to `unreadable`, every other
project-backed component to `unavailable`, and setup eligibility to false;
the package observation remains independent.

The package inspector strictly parses manifest v1, caps it at 256 KiB and 512
core entries, rejects unknown/duplicate keys, invalid identity/version/origin/
hash/path, traversal/backslashes/absolute paths, casefold collisions, and
entries in excluded regions. It enumerates physical regular core without
following links. Only root `config/`, `adapters/`, generated `state/`,
`__pycache__/`, and `*.pyc` are excluded. Files are streamed with per-file and
aggregate bounds and pre/post identity checks.

Results are `clean`, `modified`, or `unknown`; paths are bounded relative names
sorted and capped to 20. No content, expected/actual hash, absolute path, link
target, or OS error is emitted. The co-located manifest is unsigned and is a
local drift detector, not authentication. Doctor never downloads, repairs,
updates, or compares upstream state.

### Setup Plan And Stages

Read-only setup returns a deterministic plan for restore, initialization,
migration backup, migration, maintenance configuration, Evidence publication, and Viewer publication
plus effective backup interval/retention. It creates no directory, lock,
temporary, sidecar, SQLite recovery connection, or HTML.
Setup serialization adds `evidence_status`; its ordered write list places `evidence_projection_publish` after maintenance/binding and before `viewer_publish`.

Write setup revalidates scope immediately before each irreversible stage. A
completed stage is not rolled back because a later stage fails; the exact
ordered partial result makes rerun resume. Initialization, migration, recovery,
relocation, maintenance opt-in, and Evidence/Viewer publication use services directly,
not removed CLI subprocesses.

When canonical DB is absent, setup scans only canonical managed backups:

- no managed name => fresh initialization;
- at least one eligible same-project/current-binding generation => newest
  eligible `(published_at, generation_id)` recovery while the mechanically
  newest generation remains the structural head;
- a structurally coherent set whose current-binding candidates are all locally
  rejected only for stored Task-verification privacy/capacity =>
  `setup_restore_failed` without initialization; and
- a structural, identity, binding, lineage, metadata, repository, retention,
  sidecar, or set-envelope failure => the specific resolver result where
  applicable, otherwise `project_state_unreadable`.

Invalid, foreign, linked, or unrelated files are preserved. Once a recovery
candidate plan exists, drift, copy, normalization, or no-clobber publication
failure maps to `setup_restore_failed`. Recovery holds the artifact lock,
revalidates the candidate, copies it through SQLite backup to a
sibling temporary DB, reconciles only supported backup metadata, validates
schema/identity/quick/FK/regular-file state, and publishes no-clobber. A
lexical rollback journal beside a missing canonical DB is rejected before
selection and publication and never opened/deleted. A concurrently appearing
canonical DB is never overwritten.

A supported old recovery migrates normally; current recovery proceeds to
Evidence and then Viewer. Fresh initialization repeats candidate/journal absence under the lock
immediately before creation. Setup never silently substitutes initialization
for a stale recovery plan.

### Maintenance Policy And Managed Backups

`backup_metadata_repository.py` owns the shared maintenance-policy read/seed
and backup-policy configuration, plus managed-generation, pointer, outcome,
and retention writes and their direct record validation. The shared
`ProjectMaintenanceState` retains its Viewer fields; Viewer publication writes
belong to `viewer_metadata_repository.py`. Storage retains schema admission and migration
orchestration, and backup/setup retain physical publication and reconciliation.

Schema v10 has one `project_maintenance` row with immutable one-way
`enabled_at`, backup interval/generation policy, applied retention, shared
backup success/outcome/latest generation, and Viewer success/outcome. Setup
defaults a new policy to 30 minutes and three generations. Public bounds are
1-1,440 minutes and 1-20 generations. Omitted values preserve existing
policy; equal explicit values replay. A policy-only change does no copy/prune,
and a reduced retention becomes applied only with the next successful
publication.

The sole backup primitive:

1. opens a rollback-journal source through the operational boundary;
2. copies with `sqlite3.Connection.backup` to a fresh contained temporary;
3. validates schema support, identity/binding, quick check, and foreign keys;
4. closes it;
5. atomically publishes a canonical generation with fixed
   publication-retention metadata; and
6. returns only bounded generation/time metadata.

Within one copy attempt, the source, temporary-copy, and immediate
pre-publication source validations may reuse successful privacy checks keyed by
the exact mode, field, and stored value. The attempt starts with an empty cache,
never caches a rejection, and still repeats every schema, row, digest,
relationship, identity, quick-check, foreign-key, and current-source validation
at all three phases. For routine backup only, after all three validations and
the generation-row record succeed, a frozen copy of those exact successes may
seed the immediately following post-publication reconciliation. Each retained
artifact receives an independent mutable copy; artifact-local successes or
rejections never flow back to the seed or another artifact. Every cache is
cleared on success or failure. Pre-publication reconciliation, recovery,
Viewer, setup and its reconciliation, ordinary discovery, and later backup
attempts never receive that seed.

Managed names are exactly:

```text
taskgov-backup-v1_<YYYYMMDDTHHMMSSZ>_<32hex>_r<1-20>.sqlite
```

The generation ID is `tg_backup_<same 32hex>`. Other names and nonregular,
linked, invalid, or foreign files are never managed or pruned.

Schema v11 `managed_backup_generations` is the authoritative retention set.
Every setup/routine backup under the same zero-wait artifact lock reconciles
at most the bounded file/row crash residue: import one valid file-only
generation, remove an unusable row target without following an untrusted path,
and complete file-before-row pruning using applied retention. Publication
writes the file, then inserts its row and updates pointer/outcome/applied
retention in one short transaction, then prunes file before row. Failure leaves
the work due. Crash residue is bounded to the prior valid set plus one
in-flight generation.

Setup migration always makes the pre-migration managed backup before changing
schema and binds metadata/pointer before pruning. Rollback therefore means
restoring a matching package, database, and artifact set together; no reverse
migration exists.

### Post-Commit Coordinator

Business services retain internal `MutationOutcome(changed, viewer_relevant)`. After commit and connection close, every changed mutation may retry due Evidence projection; the coordinator runs:

1. Evidence projection when due;
2. Viewer refresh when relevant; then
3. one backup attempt when due.

They are independent same-process bounded work, never a thread, daemon, timer,
queue, scheduler, service, sleep, or retry loop. Each uses its own zero-wait
one-byte OS lock; lock-file existence is not ownership. Read, error, replay,
no-op, configuration-only setup, doctor, Effort, and maintenance metadata
writes do not invoke it. Runner success and fallback retain the one original
target-set coordinator pass; a post-T1 Runner error does not invoke maintenance
and leaves the already-advanced due state for a later normal opportunity or
setup repair. Runner-internal writes never invoke a second pass.

Backup is due when no success exists, the last outcome is deferred/failed, or
the configured interval elapsed. Failed attempts do not advance success.
Eligible mutations are Task add/edit/complete/checkpoint, handoff
record/withdraw, and review target/receipt/finding add/resolve. Handoffs are
not Viewer-relevant because the snapshot excludes the outbox. Verification
Receipt add is backup-eligible and Viewer-ineligible because the snapshot has
no Receipt dataset or field.

Only completion-cycle insertion increments Evidence source generation in its business transaction; a later changed mutation may retry an already-due projection without a third outcome flag. Viewer-relevant mutations increment source generation through `task_events` in the same transaction. Viewer refresh renders and rechecks once at most; setup uses direct Evidence/Viewer stages and does not re-enter the coordinator.

Only fixed warnings are added after a successful primary command:

```text
evidence_projection_deferred | evidence_projection_failed
viewer_refresh_deferred | viewer_refresh_failed
backup_deferred         | backup_failed
```

Messages state that the primary result is unchanged and include no path, exception, hash, raw output, retry, stop, or model choice. Doctor observes the latest outcome without starting work.
