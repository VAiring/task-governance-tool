# task-governance-tool Current Implementation Design

Status: the immutable published product remains v0.10.0/schema v16/Viewer v4 sources v5-v16/20 leaves; its identity is fixed in `docs/release-install.md`.
The current unpublished candidate is v0.12.0 with SQLite schema v19, Viewer
snapshot v4 accepting source schemas v5-v19, and 21 public command leaves. It retains the TG-M21 Receipt, the accepted TG-M22.2/TG-M21.5 capture/admission boundaries, the accepted TG-M22.3 native Bundles and fixed Evidence JSON v1, accepted TG-M22.4 integrated acceptance with managed recovery and stored-Task/Contract relationship validation, accepted TG-M23.1 derived-evidence design, accepted bounded offline/mock TG-M23.2 implementation, accepted TG-M23.3 offline/mock integrated Analyzer acceptance, and accepted documentation-only TG-M24.1 Runner design.
TG-M20S.3 and TG-M24.3 through TG-M24.4 remain inactive. No TG-M23 unit is current. The bounded TG-M24.1A Win32 LPAC proof seam and mandatory-native correction is accepted, and current shadow-Runner implementation authority belongs to TG-M24.2. Until TG-M24.2 completes, the candidate remains v0.12.0/schema v19 and only the standalone portability test is mandatory-native; this authority transition alone activates no Runner, process boundary, schema, CLI, Skill, completion gate, network, credential, or target mutation.
TG-M16.4 behavioral acceptance remains part of the current baseline. The Task
database owns live state and evidence.

This document is the current implementation design for the behavior specified
in `docs/specification.md`. The [authority index](authority.md) selects current
owners and exact current-or-conditional execution detail from the live Task
Contract. Historical design captures
under `docs/history/` are non-authoritative and are never needed to implement,
operate, migrate, or review the supported product.

## Design Summary

`task-governance-tool` is a small local-first Codex Skill and deterministic
Python CLI. The Skill supplies concise agent routing; the CLI owns structured
state transitions; SQLite owns local helper state; and the generated Viewer is
a read-only projection. Governing project documents, not SQLite or the Skill,
remain authority for project decisions.

The implementation deliberately avoids a general issue tracker or workflow
engine:

- normal operation is offline and uses no external model call;
- inspection does not create, migrate, recover, or repair state;
- target-project or Git mutation requires separate explicit authority;
- durable text uses its defined per-field bounds and is privacy-validated;
- external work never runs while a SQLite writer is held;
- state and artifacts have one physical project-scoped owner; and
- historical evidence cannot satisfy a current completion or review gate.

## Source And Package Layout

The source repository contains governing documents, tests, fixtures, and one
self-contained installable package:

```text
AGENTS.md
docs/
  authority.md
  specification.md
  design.md
  execution-contracts/
  history/
plan.md
tools/
  document_contract.py
  release_contract.py
  test_lanes.py
task-governance-tool/
  release-manifest.json
  SKILL.md
  agents/openai.yaml
  assets/task-viewer.template.html
  references/
    task_workflow.md
    cli_contracts.md
    reconciliation.md
  scripts/
    taskgov.py
    task_governance_tool/
tests/
fixtures/
```

Normal stateful use supports exactly one physical package at:

```text
<governed-project>/.agents/skills/task-governance-tool
```

User-wide, symlink, junction, and other reparse-point installs are unsupported.
The source repository has one development-only self-host exception: an
explicit `--repo` may use the physical package at
`<repo>/task-governance-tool` when the four fixed source-shape marker files and the
fixed package entry/manifest files identify that source shape and no competing
project-scoped install exists. It uses the same package-local state resolver;
it is not a second state mode or install recommendation.

The supported runtime is Python 3.12 or newer on Windows. CI verifies exactly
Python 3.12 and 3.14; no Linux or macOS support claim is inferred.

The package is self-contained. `scripts/taskgov.py` disables bytecode creation
before package imports. Release archives contain the package only, including
its bundled HTML template and manifest, and exclude source-repository tests,
fixtures, root documents, local configuration, generated state, caches, and
logs.

## Runtime Module Boundaries

The implementation keeps these narrow ownership boundaries:

- `cli.py` parses the public surface, resolves lexical root options, dispatches
  services, and formats bounded JSON or text.
- `compact.py` owns compact task projections and final byte caps.
- `project_scope.py` validates the governed root, physical package layout,
  self-host exception, containment, and effective Git-ignore preflight.
- `state_paths.py` defines fixed state names and containment rules.
- `state_resolver.py` is the sole production resolver for fixed state, bounded
  legacy discovery, identity, binding, recovery observations, and artifact
  targets.
- `state_transition.py` owns private staging, no-clobber publication, and
  bounded legacy cleanup.
- `storage.py` owns SQLite connections, migrations, validation, repositories,
  and transaction-scoped queries. Feature modules do not open raw SQLite
  connections.
- `tasks.py`, `ordering.py`, and `selection.py` own task validation, lifecycle,
  current/list projections, the source-schema-aware stored Task row/batch
  and Contract-relationship validator, the shared sequential predecessor
  predicate, and next-task selection. `storage.py` supplies source-schema
  capability and the fixed stored-state failure boundary rather than
  duplicating Task semantics.
- `completion.py`, `completion_workflow.py`, and `git_snapshot.py` own typed
  completion evidence, read-only Git observations, completion planning, and
  review-to-commit snapshot binding.
- `completion_history_projection.py` owns the bounded public cycle projection;
  `storage.py` alone inserts immutable cycles.
- `verification_receipts.py` owns caller Receipt validation, exact-current
  classification, completion-gate evaluation, and the bounded Task-show read
  model; `storage.py` alone owns Receipt persistence, migration structure, and
  version-aware legacy-label/internal-subject stored-row validation.
- `review_provenance.py` owns the closed Review provenance matrix, canonical
  v1/v0/null public union, and provenance digest without SQLite access.
- `evidence_ledger.py` owns authority/criterion canonicalization, closed
  assurance/producer dispatch, Evidence Reference projections and digests,
  and capture-version source guards without SQLite access.
- `evidence_projection.py` owns canonical Bundle/index encoding, coherent
  projection capture, digest validation, and index-last publication.
- `artifact_manifest.py` owns safe bounded Git leaf observation, exact rename
  classification/order, opaque/complete manifests, and manifest digests. Git
  observation is separate from the short DB binding transaction.
- `reviews.py` owns review target, receipt, finding, and deterministic gate
  evaluation; `review_packet.py` owns bounded read-only review context.
- `contracts.py` owns immutable Task Contract revisions and invalidation.
- `checkpoints.py` owns optional append-only checkpoints.
- `handoffs.py` owns the local handoff outbox; selection never depends on
  handoff or adapter state.
- `effort.py` owns the optional deterministic Effort Advisory and no task
  transition.
- `setup.py` orchestrates explicit initialization, migration, recovery,
  relocation, maintenance opt-in, and direct Viewer repair.
- `doctor.py` combines read-only package, scope, and project observations.
- `self_status.py` is the bounded package-integrity inspector used internally
  by doctor; there is no public `self` command.
- `backup.py`, `artifact_lock.py`, and `maintenance.py` own managed SQLite
  copies, one-byte artifact locks, policy state, and post-commit coordination.
- `viewer.py`, `viewer_config.py`, and `viewer_maintenance.py` own compatible
  snapshot reads, strict presentation configuration, safe HTML rendering, and
  generation-based publication.

The bundled template is a complete offline HTML/CSS/JavaScript application. It
has no external dependency, server, database connection, or network API.

The root `tools/release_contract.py` is repository-only verification tooling,
not an installable package module or public CLI leaf. It disables bytecode
generation before importing package code, derives parser leaves and release
versions from their owning runtime modules, delegates packaged-core inspection
to the runtime manifest inspector, and reads the tracked inventory with one
bounded shell-free `git ls-files -z`. Its deterministic findings cover
manifest/package drift, release metadata, license, active command inventories,
CI wiring, and generated-artifact exclusions without creating state or
changing a target project.

The root `tools/test_lanes.py` is the single repository-only owner for the
module-level `fast`, `integration`, and `release` test manifest and the CI
event/Python/lane matrix. It discovers with the same `unittest` start,
top-level directory, and pattern as the prior full command, rejects loader
errors, duplicate IDs, duplicate ownership, unassigned modules, and stale
manifest modules, and preserves discovery order when filtering. `all` is a
meta-lane over the unchanged standard suite, not a fourth maintained list.
The runner disables bytecode generation, performs no network or repository
write, and is not an installable package module or public CLI leaf.

## Public CLI And Serialization

### Command Surface

The parser exposes exactly 21 command leaves:

```text
setup
doctor
task add
task list
task next
task current
task effort
task show
task edit
task complete
task checkpoint
handoff record
handoff list
handoff show
handoff withdraw
review prepare
review target set
review receipt add
review finding add
review finding resolve
verification receipt add
```

There are no public `db`, `self`, `web`, export, repair, maintenance, backup,
restore, relocation, or adapter commands. Public `--db` is rejected. Internal
path injection remains available only to repositories, services, and tests.
`--repo` defaults to the current directory because a governed project need not
be a Git repository; runtime never silently re-roots it to a Git worktree.
Invocation while the current directory is either supported package root
requires explicit `--repo`, preventing the package from becoming the governed
project accidentally. Setup alone accepts the bounded backup interval,
retention, and relocation-confirmation options; normal Skill routing supplies
backup defaults and passes a token only after the explicit preview/approval
flow.

Root preprocessing recognizes lexical `--json`, removed commands, and removed
or unknown root options before package, project, Git, or state resolution. A
rejected token or option value is never echoed. Argparse contains no
compatibility subparsers.

`setup` is the only initializer, migrator, recovery/relocation confirmer,
maintenance opt-in, and direct Viewer repair surface. `setup --read-only`
builds a no-write plan. `doctor` is the sole diagnostic, is inherently
read-only, never repairs anything, and is not a normal Task-loop prerequisite.

### Output Boundary

JSON uses the stable envelope:

```json
{
  "ok": true,
  "command": "task.show",
  "project_id": "tg_project_...",
  "data": {},
  "warnings": [],
  "errors": []
}
```

No public envelope contains `db_path` or another internal path. Errors use
stable codes and sanitized fixed messages. Text output is concise and
operational and likewise exposes no database, backup, Viewer, staging, or
package path.

Exit status is 0 for success, 1 for parser/input validation, and 2 for
database, migration, project-state, or service failure unless a command's
fixed contract narrows it.

Default read models and compact projections use explicit allow-lists. They
never serialize a raw SQLite row. Compact current and next results are capped
at 24,576 and 16,384 UTF-8 bytes. Completion check is capped at 8,192 bytes,
checkpoint caller input at 6,144 bytes, and a complete Review Packet at 32,768
bytes. The final JSON boundary also bounds lexical parse errors and replaces an
otherwise unbounded message with a fixed omission message.

Inspection leaves are no-create and no-write. A missing database,
migration-required source, unsupported journal, invalid identity/binding, or
busy database returns its stable error and the command's fixed empty data
shape. Successful business writes describe the record they committed; only
after commit may bounded maintenance warnings be appended.

## Canonical State And SQLite Boundary

### Fixed State Resolver

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
```

The resolver returns canonical paths, the in-memory governed root/hash/display
observation, stored identity/binding when available, source schema, layout
state (`missing`, `fixed_current_v1`, or `legacy_projects_v1`), binding state
(`unbound`, `matching`, or `relocation_required`), and an optional deep
setup-only recovery/legacy observation. None of its raw paths or path hashes
crosses a formatter.

Fixed-primary normal consumers validate only the authoritative primary and
derive canonical artifact targets. Setup, recovery, transition, and legacy
discovery use the deep bounded inventory projection. The caller selects the
projection mechanically; it is not a CLI or LLM choice. Every DB-backed leaf
uses the same resolver observation. Feature code may not reconstruct a
project ID or state path.

### Journal And Connection Rules

Live operational access supports rollback-journal SQLite only. Before any
existing-database read, write, or migration, `storage.py`:

1. rejects lexical `<db>-wal` or `<db>-shm` entries;
2. reads only the fixed SQLite header;
3. rejects a valid header whose read or write version indicates persistent
   WAL; and
4. leaves rollback `-journal` files to SQLite's normal locking/recovery rules.

The check does not open SQLite, checkpoint, change journal mode, delete a
sidecar, or disclose a path/header/OS error. WAL maps to
`unsupported_journal_mode`; unreadable headers use a sanitized internal error.
A short, invalid, or unknown header is not mislabeled as WAL and proceeds to
normal schema validation.

Read connections use:

```text
file:<absolute-uri>?mode=ro
PRAGMA query_only=ON
BEGIN
```

They never use `immutable=1`. Schema history, required objects, project
identity/binding, and command rows are read from the same transaction.
`task show` combines task, events, review evidence, handoff summary, Contract,
checkpoint, completion history, and Verification Receipt evidence in one
snapshot. Handoff list reads count and rows together. Viewer capture reads
generation, tasks, events, and history in one compatible-schema transaction.
`task next` deliberately uses one
transaction for status/paused advisory and another for candidates; cross-phase
staleness is advisory only, while each phase remains internally coherent.

Write services perform caller validation and all Git or other external
observation before opening the writer. They then:

1. run journal preflight;
2. acquire `BEGIN IMMEDIATE`;
3. revalidate schema, project ID, binding hash/generation, task status,
   ordering, Contract revision, review target/generation, and other
   operation-specific basis;
4. persist business rows and events in one savepoint/transaction; and
5. commit and close before formatting or maintenance.

`updated_at` alone is never a concurrency token. No external process, sleep,
backoff, copy, render, model call, or configured command runs while the writer
is held. Residual SQLite busy/locked results map after the normal driver wait to
`database_busy`, exit 2, with no raw SQLite text or retry question. Handoff
record may retry its whole fresh local transaction once; no general automatic
retry exists.

### Migration Sequence

`schema_migrations` is contiguous, named, and validated with required objects
and known later-version markers. Only explicit setup invokes migration. Other
commands never create parents, create a database, stamp missing history, or
perform a reverse migration. Older binaries reject newer schemas.

Current sequential migrations are:

| Version | Name / owned state |
| --- | --- |
| 1 | initial task, event, tool-event, and project metadata schema |
| 2 | compatibility completion-commit requirement/hash projection |
| 3 | `paused` status and exact pause-reason matrix |
| 4 | typed completion evidence |
| 5 | review target, receipt, and finding evidence |
| 6 | Git-snapshot base and receipt target expansion |
| 7 | local handoff outbox |
| 8 | immutable Task Contract revisions and current pointer |
| 9 | Effort Advisory activity generations and bases |
| 10 | one-way project maintenance and backup/Viewer outcome state |
| 11 | managed backup generation inventory |
| 12 | append-only typed checkpoints |
| 13 | Viewer source/render generation state |
| 14 | stable identity and append-only path-binding history |
| 15 | immutable completion-cycle history and internal event linkage |
| 16 | marker-only native completion-capture activation |
| 17 | immutable Verification Receipts and completion-cycle verification basis |
| 18 | authority/criterion capture, Review provenance, target manifests, Evidence References, and Verification subjects |
| 19 | native completion Bundles, criterion links/Finding snapshots, and Evidence JSON projection state |

Every migration is ordered, idempotent on reentry, transactional, and
rollback-tested. Reentry validates rather than synthesizing missing data.
Table rebuilds preserve foreign keys and IDs and restore foreign-key
enforcement in a `finally` path. Migration validation uses
`PRAGMA quick_check`, `PRAGMA foreign_key_check`, exact row/object
preservation, and the sanitized realistic 12-task/191-event fixture with nine
historical completion hashes and representative review, Contract, handoff,
checkpoint, maintenance, identity, and completion traces.

The fixed-state setup migrator accepts complete source schemas v1-v18 and
treats v19 as current. Legacy `state/projects` discovery is intentionally
narrower: v1-v13 plus the explicit schema-v14 legacy-layout transition.
Viewer compatibility is independent and accepts source schemas v5-v19.
Incomplete history, a missing required object/row, a later marker, too-new
state, unsupported layout, foreign identity, or corrupt integrity fails closed.

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

The binding repository compares identity, expected generation, and old hash
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
at most 500 characters through schema v17 and 1,000 at schema v18 or v19. The source
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

Focused M21.4B ownership is split by behavior rather than accumulated in one
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
schema v1-v19; `legacy_projects_v1` is capped at the explicit v14 transition,
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

## Task State And Selection

### Task Model And Events

Tasks have stable random `tg_task_...` IDs, project ownership, bounded title
and description, kind (`sequential` or `optional`), lane/order, priority
(`low`, `normal`, `high`, `urgent`), status, blocker/pause reasons, review tier,
verification text, tags, timestamps, completion evidence, current review
target, current Contract pointer, Effort activity, and completion-history
coverage. IDs never encode a path.

Statuses are:

```text
ready
in_progress
paused
blocked
review_pending
done
cancelled
```

Blocked requires a reason. Paused requires a pause reason exactly while paused
and may be entered only from in-progress or review-pending; its normal exit is
in-progress and clears the current reason. Initial paused and initial done are
forbidden. `completed_at` is set only on done and cleared on reopen.

Task events are append-only concise audit summaries with stable random IDs.
The internal completion-cycle link added in schema v15 is never in
`PUBLIC_EVENT_FIELDS`; all event-return paths construct the six-field
allow-list explicitly. Latest-event ordering is
`created_at DESC, rowid DESC`. Tool events remain bounded operational records,
not raw logs.

### Current TG-M21.4C Shared Stored Task Row/Batch Validator

`tasks.py` owns one source-schema-aware validator for complete stored Task
rows. Its capability object is constructed once per top-level read from the
already-observed source schema and describes the verification limit (500
through v17, 1,000 at v18) and the
presence of review-target base, Contract pointer, and completion-history
coverage fields. The validator receives rows and expected project identity; it
does not query schema metadata, mutate a row, or issue a database write.

Exact text/nullable-text and SQLite integer storage classes are checked before
any value helper can trim or coerce. The validator then applies field privacy
and capacity, stable identity, enum, canonical lane/order/timestamp, and Task
cross-field rules. It raises only `StorageError("project_state_unreadable",
"project state could not be read safely")`. Current Task converters remain
pure allow-list builders and are invoked only after their complete input batch
passes.
The public `task add` and explicit `task edit --verification` ingress is
independently capped at 1,000; every stored/read/internal path uses the source-
schema limit and never revalidates untouched bytes as new caller input.
The shared Task fetch helpers map non-busy SQLite query or UTF-8 decode failure
to that fixed error before a row reaches the validator, while preserving the
existing `database_busy` result for actual busy/locked state.

`list_tasks`, `list_current_tasks`, and `select_next_tasks` select complete
rows with their existing SQL bounds and validate that selected batch before
tag filtering, conversion, or compact omission; they never add a whole-project
validation query. Single-Task reads and lifecycle/write bases route through
the same validator. Whole-project consumers—doctor and Viewer—and setup or
recovery validation load every Task row without a project filter and pass that
complete ownership-checked batch. Managed recovery
sets its explicit verification-local flag so only that field's privacy or
source-capacity failure remains candidate-local; every other Task fault is
structural and set-fatal.

Viewer supplies the source version returned by snapshot validation. For exact
schema v18 or v19, that validation completes the full Evidence Ledger and Task batch
checks before issuing one private, one-shot batch proof bound to the same
query-only connection and transaction, project, source version, exact sorted
Task IDs/count, issuance data version, and a fixed nested savepoint held only
in a module-private exact-object issuance registry. The private Viewer ordering
path revokes and consumes that proof once; mismatch, reconstruction, reuse, or
a changed
transaction fails closed. Other callers and source schemas v5-v17 retain the
ordinary complete Task validator. Review evidence consumes the already-
validated Task where available and derives column capability from that
version, removing the per-Task `PRAGMA` path. Routine Viewer failure occurs
before rendering/replacement and preserves the last-good file; its caller
applies the existing fixed maintenance warning.

### Current TG-M21.4D Stored Contract Pointer Relationship Boundary

For source schema v8 and later, `validate_stored_task_rows` receives the active
SQLite connection in addition to the already loaded complete Task batch. Only
after all TG-M21.4C scalar checks pass, `tasks.py` extracts the exact selected
Task IDs and performs one `task_contract_revisions` query whose predicate is a
single JSON-encoded ID set consumed by `json_each`. The query intentionally
does not filter `project_id`, so a foreign owner using a selected Task ID is
observable as corruption. An empty batch and source schemas v1-v7 issue no
relationship query.

The relation predicate derives both the exact TEXT key and its UTF-8 BLOB form
from each selected Task ID. The BLOB form exists only to surface a
wrong-storage-class alias to the raw validator; it neither admits an unrelated
Task ID nor changes the selected-batch boundary into a general table audit.

The relationship reader returns raw `project_id`, `task_id`, and `revision`
values for only those selected IDs. Python validates exact TEXT identity and
positive SQLite INTEGER revision before calculating the latest revision; it
does not use `MAX(revision)` or `int(...)` before storage-class validation.
Revision zero requires no returned row. A positive pointer requires a matching
row, same-project/same-Task ownership for every returned row, and equality to
the raw latest revision. Duplicate, dangling, foreign, nonlatest,
revision-zero-with-row, decode, storage-class, and ownership faults all raise
the existing fixed stored-state `StorageError`.

The composed validator is called once by add post-read, list/current/next,
show, Viewer, `read_task`, `read_internal_task`, locked write-basis reads,
setup/doctor preflight, migration/reentry, and recovery. Review Packet,
checkpoint, handoff, Effort, and evidence/completion lifecycle paths inherit it
through their existing Task reader. No consumer owns a second relationship
rule or per-Task query. `contracts.py::read_current_contract` retains Contract
content validation after this boundary; unrelated Contract history is not a
general audit target. Recovery's verification-local result is evaluated only
after relationship validation, so every relationship fault remains structural
and set-fatal.

The shared single-Task fetch helper opens one short read transaction only when
its caller has not already established a transaction. The Task row and its
relationship rows therefore come from one SQLite snapshot even when another
writer commits a Contract revision between calls. Existing query-only and
locked write transactions are reused unchanged; the helper performs no write
and closes only the read transaction it owns.

### Sequential Ordering

One repository predicate determines whether an earlier same-project,
same-lane sequential row is incomplete. Only done and cancelled predecessors
are complete. `task next` and direct transitions to in-progress,
review-pending, or done use that predicate. Task add and edits that change
kind/lane/order/status validate every already-active, review-pending, and done
row in both affected lanes inside the serialized write, preventing
registration or reordering ahead of active successors. There is no override.

Lane input is trimmed and validated once. Sequential omission chooses the
deterministic default lane and next order; all integers fit SQLite signed
64-bit and next-order overflow fails before addition. The unique
project/lane/order index enforces final storage uniqueness.

Next-task order is priority (`urgent`, `high`, `normal`, `low`), lane,
lane-order with nulls last, creation time, then task ID. Default limit is 5.
Paused, blocked, active, review-pending, done, and cancelled rows are not next
candidates. A positive paused population adds one fixed count-only advisory
without changing candidate data or exit status.

`task list` uses the same priority, canonical-lane, nulls-last lane-order,
creation-time, and task-ID order. Its default limit is 20 and maximum is 100.

`task current` selects in-progress, review-pending, paused, and blocked rows,
optionally one valid status, with default limit 20 and maximum 100. It reuses
the latest event/checkpoint and deterministic fixed next-action mapping. It
does not calculate staleness or write a checkpoint. List/current/next remain
bounded and have no pagination cursor.

### Done Immutability And Reopen

Every task/review mutation loads the owner and applies the shared done guard.
A done Task accepts only the exact reopen edit:

- resulting status `in_progress`;
- one non-empty sanitized reopen reason; and
- no other task, note, completion, review, or Contract input.

The reopen writer checks review-generation overflow and sequential ordering,
clears current completion evidence and review target/base, advances review
generation, clears completion time and hold reasons, and appends
`task_reopened`. It preserves prior events, receipts, findings, Contract
revisions, and completion cycles. Schema-v16 reopen additionally validates and
links the latest saved cycle as described below. All other done writes return
`done_task_requires_reopen`.

A review-tier increase is a normal edit. A decrease needs one sanitized reason
and is permitted only while ready, in-progress, paused, or blocked, before any
review target ever existed: generation 0 and empty kind/value/base. It cannot
share completion input or a transition to review-pending/done. Generation
greater than zero permanently proves structured review started.

## Completion Evidence And Review

### Typed Completion Evidence

Schema v4 retains the old required/hash columns only as synchronized
compatibility projections. Current evidence kinds and matrices are:

- `none`: empty revision/reason, approval 0, required 1, empty hash;
- `git_commit`: canonical full commit ID as revision/hash, empty reason,
  approval 0, required 1;
- `external_revision`: bounded revision and reason, explicit approval 1,
  required 1, hash equal to revision;
- `commit_not_required`: empty revision/reason/hash, approval 0, required 0;
- `legacy_unverified`: migration-only partial history retaining the old hash;
  no normal write may choose it.

Changing kind clears fields not valid for the destination. Conflicting stale
fields return `completion_evidence_conflict`. External evidence always needs
the explicit acknowledgement, including in a Git project. `legacy_unverified`
cannot satisfy a new done after reopen.

Git commit resolution uses argument-vector, no-shell reads equivalent to:

```text
git -C <repo> rev-parse --verify --end-of-options <revision>^{commit}
```

Empty/option-shaped input, multiple output lines, non-hex output, ambiguity,
failure, or a noncanonical full ID is rejected. Optional locks and lazy object
fetching are disabled; no Git read may invoke a hook, network fetch, or write.
Stored Git completion and Git review targets are re-resolved under the
done-time plan and compared with the locked stored values.

Thin `task complete`, compatibility `task edit --status done`, and
`task complete --check` share one completion request and ordered validator.
Both write paths require explicit verification/review confirmations, typed
evidence, sequential eligibility, exact current target, a qualifying current
Verification Receipt when verification is nonempty, a satisfied fresh review
gate, and no blocking receipt/finding. Check is read-only and is not an
authorization token: it captures one coherent basis, closes SQLite for Git,
then performs a second coherent basis read. Drift yields
`completion_check_stale`; no readiness row or receipt is stored. Its bounded
projection returns only Task ID, ready/status, the first ordered blocking code,
Contract revision, target generation, proposed evidence kind, and fixed next
action.

### Review Target And Git Snapshot

The current review identity is the exact tuple:

```text
kind, value, base_revision, generation
```

Kinds are `git_commit`, `diff_fingerprint`, `external_revision`, and
`git_snapshot`. A diff fingerprint is canonical
`sha256:<64 lowercase hex>`. Setting any target advances generation even when
kind/value repeat, and old receipts become audit-only. Receipt creation copies
the complete tuple; callers cannot attach one to another target.

`git_snapshot` takes no caller revision. The Git service reads a stable
`HEAD^{commit}` and stage-0 `git ls-files --stage -z` index before/after
checks, consuming raw path bytes. It rejects unborn HEAD, nonzero stages,
intent-to-add zero objects, sparse directory entries, and capture instability.
The version-1 digest input is:

```text
"taskgov-git-snapshot-v1\0"
<base full object id> "\0"
for mode/object/path entries sorted by raw path bytes:
  <mode> "\0" <object id> "\0" <raw path bytes> "\0"
```

The target value is its SHA-256 fingerprint and base is HEAD. It excludes
unstaged and untracked content.

Target setting first performs bounded Git observation outside SQLite. A short
writer then rereads Task, Contract, current authority snapshot/criteria, and
expected generation and atomically inserts one capture-version-1 target,
manifest, manifest Evidence Reference, and event. Git targets compare complete
tree/index leaves or commit/first-parent leaves; root commits use the read-only
empty-tree identity. `artifact_manifest.py` validates safe UTF-8 POSIX paths,
full mode/object IDs, unique exact renames, fixed byte ordering and ordinals,
10,000 entries, 16 MiB canonical bytes, object presence, and pre/post stability.
Opaque targets insert zero entries and the fixed omission. The writer
revalidates the observation binding but never runs Git.

Migration leaves old targets at capture version 0 with null snapshot,
criterion, and manifest IDs. Source-producing Receipt/Finding/completion paths
check this after target structure and expected-generation equality but before
uniqueness or gate evaluation and return `evidence_basis_stale`. Preparation
and existing-Finding resolution remain read-only with respect to the ledger.

At completion, the proposed commit must have exactly one parent equal to the
stored base. Its recursive tree leaves are normalized to the same
mode/object/path form and must reproduce the fingerprint. Root and merge
commits are unsupported for snapshot binding. Hook-altered, added, removed,
renamed, mode-changed, or otherwise changed content fails
`review_target_mismatch`. A snapshot target may close only with Git-commit
completion evidence. A Git-commit target requires the identical canonical
Git completion commit, an external target requires the identical approved
external revision, and commit-not-required requires a canonical
diff-fingerprint target.

### Receipts, Findings, And Gate

Receipts are append-only and unique per Task, target generation, and bounded
caller-supplied reviewer key. Kinds are:

- `independent`: pass or changes-requested, never user-approved;
- `self_review_fallback`: summarized; a Tier-2 pass requires explicit user
  approval, a Tier-1 pass does not;
- `not_required`: Tier 0 only, verdict not-required, mechanical rationale, no
  approval.

A changes-requested receipt requires a summary and never satisfies the gate.
Replacing a same-generation reviewer result is forbidden; re-review sets a
fresh target generation.

Findings belong to a loaded same-project/same-task receipt and are high,
medium, or low. Resolution preserves the original row and adds only bounded
resolution text/time. Open high or medium findings across all task receipts
block. A resolved high/medium finding still requires a target generation newer
than its receipt and fresh passes. Any current-generation changes-requested
receipt blocks. Low findings are nonblocking.

The gate deterministically requires:

- Tier 0: one current not-required receipt;
- Tier 1: one distinct current independent PASS, or its valid fallback;
- Tier 2: two distinct current independent PASS reviewer keys, or one valid
  explicitly approved fallback when independent review tooling is unavailable;
- no current changes-requested receipt;
- no open high/medium finding; and
- no resolved high/medium finding at or after the current target generation.

Independent PASS rows are ordered by reviewer key then receipt ID and take
precedence over fallback. Tier 0/fallback selection is by receipt ID. Reviewer
key distinctness proves only different stored strings, not identity,
independence, provenance, expertise, target inspection, or authentication. The
trusted caller/orchestrator is responsible for truthful attestation.

### Current Schema-v19 Provenance, Ledger, And Bundle Design

`review_provenance.py` owns the closed enum/matrix, input normalization,
public v1/v0/null union, and structural digest; `reviews.py` integrates it into
the existing Receipt write and Review Packet shape; `storage.py` alone owns
the additive schema, repositories, migration, and stored validation.

Migration 18 adds a version discriminator and nullable provenance ID to
`review_receipts`, plus one immutable normalized provenance table and one
ordered code table. Existing rows receive only zero/null. New independent and
self-review writes atomically insert a version-1 row and its canonical
profile/lens/method codes; new `not_required` writes retain zero/null. Deferred
same-Receipt ownership, insert guards, and shared readers reject a missing,
extra, cross-owned, noncanonical, or digest-mismatched relation. Migration and
reentry never parse reviewer key/summary or synthesize a provenance row.

The same migration owns `authority_snapshots`, `contract_criteria`,
`authority_snapshot_criteria`, `review_receipt_provenance`,
`review_receipt_provenance_codes`, `artifact_manifests`,
`artifact_manifest_entries`, and `evidence_references`, plus only their
current pointers and binding/version columns. Every ledger table is immutable,
same-project/Task owned, and digest/reentry validated.

The Receipt assertion and its provenance have separate assurance. Existing
Review Receipt facts remain caller-attested; explicit v1 provenance is also
`bound_attestation/trusted_caller/1`; absent legacy provenance alone projects
`legacy_unknown/legacy_migration/1`; and a not-required disposition has null
provenance. The existing gate evaluator consumes none of these fields. Viewer
snapshot readers validate and discard them. Schema-v18-or-v19 recovery treats any
provenance defect as structural set-fatal state, not as the narrowly allowed
Task-verification content rejection.

Current Review Receipt reads include the exact provenance union; only native
v1/null Receipts can enter a Review Receipt Evidence Reference/digest or native
Bundle, while a migrated v0 Receipt gets none. Generated Evidence JSON adds no
general JSON column, dynamic enum, model call, reviewer launcher, provenance
score, Viewer UI, public leaf, or normal-loop call.

`evidence_ledger.py` builds immutable current-basis snapshots and exact
whole-field acceptance/verification criteria inside Task/Contract savepoints.
Only title, description, review tier, exact verification, or Contract basis
changes advance the snapshot. Migration creates one exact current-basis
`legacy_migration` snapshot per Task, never historical authority. Criteria are
reused only by same-Task kind/digest and never parsed or rewritten.

Every schema-v18-or-later native manifest, Receipt, Finding, and completion source is inserted with one Evidence Reference in the source transaction. One closed dispatch derives assurance/producer/version and exact source projection; the caller supplies none. Completion References use deferred same-cycle ownership; validators recompute ownership, binding, null matrices, dispatch, and digest.
Schema v19 adds immutable criterion links, Bundle membership/Finding snapshots, completion Bundles, cycle evidence-basis linkage, and Evidence projection state. `evidence_projection.py` assembles and size-checks one canonical Bundle before the storage repository inserts its links, snapshots, Bundle, and cycle atomically; migrated cycles stay version 0/null and receive only `legacy_unknown` index entries. Canonical Analyzer and Runner writers remain inactive.
`evidence_projection.py` captures one query-only generation, closes SQLite, writes/validates Bundle files, replaces the index last, conditionally records publication, and follows up at most once. It imports nothing; SQLite remains canonical and failure preserves the committed mutation and last-good index.

### Review Packet

`review prepare` reads Task, Contract, target, and review counts in one
transaction, closes it for bounded Git observation, then reopens a short read
transaction to compare project/task identity, Contract revision, and the whole
target tuple. A change returns `review_packet_stale`.

For a snapshot it recaptures the exact base/index context. For a Git commit it
lists first-parent changes, using the empty tree for a root. Fingerprint and
external targets perform no Git read and state that exact caller-provided
material must be bound to the target before PASS. At most 10 shell-free Git
processes, 100 bytewise-sorted relative paths, 240 UTF-8 bytes per path, and
16,384 aggregate path bytes are allowed. Safe overflow is marked truncated;
an unsafe path or a packet above 32,768 bytes fails with no partial packet.

Task, Contract, target, changed paths, five fixed review-focus rows, required
output, and the existing receipt argv shape are allow-listed. The builder
does not launch a reviewer, execute/import a receipt, store a packet, or
include a diff, transcript, prompt, stdout/stderr, secret, or absolute path.

## Completion Cycle History

### Schema V15 Through V19 Activation

Schema v15 adds immutable `task_completion_cycles`, Task coverage
`legacy_unknown|complete`, nullable internal `task_events.completion_cycle_id`,
parent/foreign-key support indexes, and triggers that prevent:

- every cycle update or deletion;
- changing Task coverage after insert; and
- changing an event's cycle link from null or one saved ID.

Cycle IDs have prefix `tg_completion_cycle_` plus 16 lowercase hex characters.
Ordinals are unique per project/Task, start at 1, and reject signed-64-bit
overflow. The row stores:

- origin `native_done|legacy_current_done` and completeness
  `complete|partial`;
- completion and record times;
- Contract revision, review tier, and verification expectation/attestation;
- the complete six-field completion evidence projection;
- the complete four-field review target;
- gate basis version and deterministic counts/basis kind; and
- up to two exact qualifying receipt IDs protected by composite foreign keys
  to the same Task and target tuple.

Schema v17 additionally stores internal verification basis version,
expectation digest, and nullable Verification Receipt link. Every pre-existing
cycle migrates as verification-basis version 0 with null digest/link. Every
new native cycle uses version 1, stores the domain-separated digest of its
exact verification expectation, and links the qualifying pass/full Receipt
when that expectation's trimmed text is nonempty. An expectation whose trimmed
text is empty requires a null link while its digest still binds the exact stored
bytes; migration does not rewrite legacy whitespace-only values.
The sole partial `legacy_current_done` reopen bridge remains the only
post-v17 version-0/null/null insert.

Post-v17 native rows must be complete, have completion time, attestation true,
non-empty target, gate-basis version 1, zero blockers, and a valid Tier basis.
Legacy rows are partial, have null attestation, gate-basis version 0, unknown
basis, null counts/receipt slots, and retain an honest legacy completion
projection. Structural `CHECK`s enforce the matrices; repository validation
also enforces canonical hashes/fingerprints/text/timestamps and exact receipt
kind/verdict/approval/order.

Schema v19 independently adds cycle evidence-basis version and nullable Bundle ID. Existing cycles become version 0/null; every native cycle is version 1 with one immutable same-transaction Bundle, while the sole partial legacy bridge remains version 0/null. Public completion history adds no Bundle field.

Migration 15:

1. fingerprints all prior business tables and counts;
2. creates columns, table, indexes, and immutability triggers in one immediate
   foreign-key-enabled transaction;
3. reads current done Tasks in binary Task-ID order;
4. inserts one ordinal-1 `legacy_current_done` partial row per current done
   Task using one migration timestamp and no inferred gate claim;
5. leaves every old event link null and every Task coverage
   `legacy_unknown`; and
6. proves preservation, quick check, and foreign keys before recording
   `completion_cycle_history`.

Reentry validates current structure/matrices without rerunning migration-time
cardinality assumptions.

Marker migration 16 requires complete v15 and all Tasks still
`legacy_unknown`. For each current done Task it compares the newest cycle with
completion time, Contract/review/verification values, all evidence and target
fields, and whether a reopen already linked it. It inserts the next partial
legacy cycle only when absent, different, or already linked; an exact unlinked
match is retained. It then records
`completion_cycle_capture_activation`. The marker adds no schema object.
Reentry validates and never reconciles twice. Schema-v16 Task creation
explicitly writes coverage `complete`; the column default remains
`legacy_unknown` for schema-v15 safety.

### Native Done And Reopen Transactions

Both `task complete` and compatibility `task edit --status done` perform Git
preflight outside SQLite and converge on one locked native capture:

1. validate schema v19, identity/binding, optimistic Task/authority/target
   capture basis, Contract, sequential ordering, and evidence;
2. reread Verification Receipts and review receipts/findings, evaluate the
   current verification and review gates, and select their deterministic
   bases;
3. choose canonical completion time and next ordinal;
4. insert links, Finding snapshots, one immutable Bundle, and one complete
   verification/subject/evidence-basis-v1 `native_done` cycle;
5. update the current Task to done with identical evidence;
6. rerun lane invariants;
7. insert the existing completion event with the internal cycle link; and
8. advance Evidence and Viewer source generations and commit.

Any failure rolls all business rows back. Concurrent completions serialize; a
loser creates no second cycle. Read-only completion check inserts nothing.

Reopen locks the done Task, loads the highest cycle and linked reopen state,
and compares that cycle with the entire current completion projection. When
coverage is `legacy_unknown` and no cycle exists, it may insert the exact
ordinal-1 partial compatibility bridge. Complete coverage without a cycle,
any existing-cycle mismatch, or an already linked reopen returns
`completion_history_inconsistent`. It then clears current gates, preserves
coverage and cycles, inserts `task_reopened` linked to the validated cycle, and
advances Evidence generation only when it inserts the legacy bridge. It never revalidates historical Git material, uses historical
receipts as current eligibility, changes a cycle, or creates a bridge when a
cycle already exists. A later done uses fresh gates and the next ordinal.

### Public History Projection

`task show` reads total, incomplete-legacy aggregate, and newest-first rows in
the same snapshot as all other Task data. The exact wrapper is:

```json
{
  "total": 2,
  "returned_count": 2,
  "truncated": false,
  "legacy_history_incomplete": false,
  "cycles": []
}
```

`legacy_history_incomplete` is computed over all durable rows, not the bounded
return window. It is true when Task coverage is not complete, any saved cycle
is partial, or any `task_reopened` event has a null internal cycle link. A
fresh schema-v16-or-later Task with no cycle is complete history and false.

Each cycle and each candidate complete wrapper is measured using compact,
sorted-key, non-ASCII-preserving UTF-8 JSON. A cycle is at most 8,192 bytes;
the wrapper is at most 10 rows and 32,768 bytes. Collection stops at the first
non-fitting newest-first row. Version-0 basis uses JSON nulls and no receipt
IDs; version 1 uses integers and the stored one/two IDs without null
placeholders. Text prints only counts, flags, and newest non-content fields.

The formatter revalidates stored completion revision, evidence reason,
completion-hash, target value, and target-base text through the ordinary
privacy matcher before building a public cycle. It has no legacy M19.7
projection. A rejection maps to the fixed
`completion_history_inconsistent` error and never exposes the offending field
or value; `task show` and Viewer use this same formatter.

Viewer batch history is grouped/windowed for at most 500 selected Tasks and 10
cycles each, not one query per cycle. Snapshot v4 uses the identical wrapper.
Source schemas v5-v14 synthesize empty/incomplete history; v15-v19 use stored
rows. Sources v17-v19 validate linked Receipt ownership, basis, target, and
pass/full qualification; v19 also validates and discards Bundle linkage.
Neither public events nor Viewer disclose internal event-cycle
or Verification Receipt IDs.

The only new stable error is
`completion_history_inconsistent: stored completion history is inconsistent`.
It exposes no IDs, counts, values, hashes, SQL, or paths.

## Current Schema-v19 Verification Receipt And Bundle Design

This section defines the current schema-v19 implementation while retaining the
accepted schema-v18 Receipt boundary. It does not rewrite the immutable v0.10.0 artifact or claim a later
published artifact identity. Schema, parser, completion, Viewer compatibility,
Skill guidance, package inventory, and tests form one atomic supported
boundary. Its completed execution narrative is available only through the
history index.

### Ownership And Data Model

`verification_receipts.py` owns Receipt input validation,
exact-current classification, gate evaluation, and the bounded public read
model. `storage.py` alone owns schema, migration, append/read queries, and
Receipt uniqueness. `tasks.py` and `completion_workflow.py` consume the gate
result; neither opens raw SQLite or interprets verification prose.
`cli.py` owns only parser/dispatch/formatting for the one Receipt write leaf.

Schema v17 added an immutable `verification_receipts` table with:

```text
verification_receipt_id project_id task_id contract_revision
verification_expectation_digest command_label result duration_ms
scope_coverage target_kind target_value target_base_revision
target_generation created_at
```

IDs use the `tg_verification_receipt_<16-lowercase-hex>` grammar. Result is
`pass|fail|timeout`; coverage is `full|partial`; duration is a nonnegative
signed-64-bit millisecond integer. Legacy labels are nonempty,
privacy-validated, and at most 200 characters; native v18 rows store only the
internal `taskgov-owned-verification-subject-v1` value in the retained column.
Target fields reuse the existing four-way matrix.
Composite indexes support project/Task/current Contract/expectation/target
gate reads without one query per Receipt. Update/delete triggers preserve the
append-only boundary. A unique project/Task/target-generation key permits one
aggregate Receipt and makes retry require an explicit fresh target.

The same migration adds three internal fields to `task_completion_cycles`:

```text
verification_basis_version verification_expectation_digest
verification_receipt_id
```

Existing rows receive version 0 with null digest and null Receipt link. New
native cycle insertion is constrained to version 1 and stores the same domain-
separated digest of its exact Task verification text, including empty text and
preserved whitespace. Version 1 requires a linked Receipt when the stored
verification text is nonempty after trimming and requires a null link
otherwise. The Receipt ID
is a nullable foreign key; repository and projection validation additionally
prove identical ownership, Contract, stored digest, target tuple, and
`pass/full` qualification. Existing cycle immutability covers all three
fields. A migration-owned insert guard permits version 0/null/null only for the
existing sole `legacy_current_done` partial reopen bridge when a legacy-
unknown done Task has no cycle; every normal native insert requires version 1.
None of the internal fields is added to the public completion-history
projection.

Migration 18 adds subject-basis version and nullable authority-snapshot and
verification-criterion IDs to both Receipts and cycles without rebuilding or
updating old rows. Existing rows read as subject version 0/null/null. A native
Receipt and nonempty native cycle use version 1 with matching capture IDs;
trimmed-empty verification uses version 1/null/null and no Receipt. Triggers
and readers enforce same-project/Task snapshot-to-criterion membership and the
exact target binding.

The expectation digest is:

```text
sha256("taskgov-verification-expectation-v1\0" + exact stored UTF-8 verification)
```

Its stored representation is the lowercase 64-hex digest. It binds eligibility
without storing another copy of verification prose. The public projection
never emits the digest. The complete target tuple is copied
from the locked Task; no caller target field exists. `created_at` and ID are
allocated under the writer. Structural validation reuses the existing target,
timestamp, privacy, signed-integer, and ownership validators.

The activated Skill sequence is exact material, existing target set with its
returned generation retained, external verification, Receipt add with that
expected generation, review prepare/record, then completion. This moves target
setting before verification and adds exactly one normal-path call, making the
target bound 10 or 11 with Effort Advisory.

### Receipt Write And Freshness

The write service first validates result, duration, coverage, and positive
`expected_target_generation` without opening a writer, then starts
one short immediate transaction and rereads schema,
identity/binding, Task, Contract pointer, exact verification text, and target
tuple. It permits only in-progress or review-pending Tasks with specified
verification and a current target. Expected generation must equal the locked
target generation; drift returns `verification_basis_stale` without storage.
Capture version must then be 1 or the write returns `evidence_basis_stale`.
It derives the subject from the locked snapshot/criterion, computes the digest,
appends the Receipt and Evidence Reference without changing the Task or adding
an event, commits, and only then invokes backup-eligible, Viewer-ineligible
post-commit maintenance.

The caller's add invocation attests that the external run exercised the copied
target; taskgov does not prove that claim. The caller supplies no command body,
source revision, Contract revision, timestamp, ID, exit code, output,
exception, or arbitrary result document. A failed or timeout row records
evidence only and performs no Task/status/Contract/target/review/completion/
Handoff mutation. A second call in the same generation returns
`verification_receipt_already_recorded`; the caller can inspect `task show`,
then explicitly set a fresh target before another run.

Parser and common state errors retain their existing owners. The Receipt
service owns one ordered validator matching the specification: normalized
caller fields, Task existence, done/disallowed status, nonempty expectation,
stored target structure/presence, expected generation, capture version, then
uniqueness. It is
used before and after the writer lock so a concurrent status, Contract,
expectation, or target change cannot select a different semantic ordering or
bind the row to new material. CLI formatting owns the exact three-line success
text and adds no synthetic Task event.

No label or subject argument exists. Version-aware stored validation applies
the legacy command-label predicate only to subject-zero rows and requires only
the fixed internal value for subject-one rows; public projection emits the
versioned subject union and never that internal value.

One shared evaluator classifies a row as exact-current only when project,
Task, Contract revision, expectation digest, and every target field equal the
coherent current basis. The gate computes, in order:

```text
specified expectation + no exact-current Receipt
  => verification_receipt_required
specified expectation + Receipt other than pass/full
  => verification_receipt_blocking
specified expectation + pass/full
  => satisfied
unspecified expectation
  => receipt gate not required; existing attestation remains required
```

A nonempty capture-version-zero target instead reports
`evidence_basis_stale` before Receipt-required/blocking evaluation. Completion
check applies the same stale result for every capture-zero target because any
completion creates a new ledger source.

Old-target, old-Contract, and old-expectation rows remain visible only in the
bounded recent audit list and never affect that result. Any current non-
qualifying Receipt is retired by an explicit fresh target generation, not by
row update or delete.

A semantic edit of `tasks.verification` with a started target uses the existing
Contract-invalidation shape in the same Task transaction: clear typed
completion evidence and target kind/value/base, increment generation with
overflow protection, update the Task, and append the normal bounded event.
With no explicit status, review-pending moves to in-progress. Explicit active,
pause, block, or cancellation status follows ordinary transition validation;
explicit review-pending/done is rejected until a fresh target exists. The same
edit rejects caller completion-evidence options instead of applying and then
silently clearing them. Receipts and review rows remain immutable and become
historical. A same-content edit is a no-op under existing edit rules.

### Completion And Read Integration

The completion basis snapshot adds the current verification gate summary and
Receipt ID. Outside-Git preflight and locked revalidation both run the same
evaluator. Ordered validation keeps the explicit `verification_required` flag
check and target validation first, then reports the missing or blocking
Receipt, then evaluates current review findings/receipts. `task complete
--check` adds the two Receipt codes to its bounded allow-list; check never
writes or reserves a Receipt.

For specified verification, native completion proves that the unique current
Receipt is `pass/full`, then inserts a version-1 cycle with its Receipt foreign
key and identical expectation digest, the Task update, and linked completion
event in one transaction. A native completion whose verification text is empty
after trimming inserts version 1 with its exact-byte digest and a null Receipt
link; the exact empty string uses the empty-text digest. Any ownership,
target, digest, link, or gate drift rolls everything back. Existing
`verification_attestation=true` remains in the cycle, so the Receipt
strengthens rather than silently replaces the current explicit assertion.
Version-0/null/null marks only migrated cycles and the sole exact compatibility
bridge; a version-1 nonempty cycle with no valid linked Receipt is
`completion_history_inconsistent`, never an inferred legacy success.

The one Receipt command is `verification receipt add`. Its success data is
exactly `receipt`. `task show` obtains Receipt totals, exact-current
counts, gate, and newest 10 rows in the same query-only transaction as the
Task and adds the fixed `verification_evidence` object. The formatter exposes
only the specification's public allow-lists; no separate list/show/import/
export command or pagination is added.

Recent rows replace the old top-level label with the five-key versioned
subject union. The parent read model adds `current_verification_subject`, null
without a capture-v1 verification criterion. Subject-zero done cycles retain
their exact v17 link/label rules; reopen clears current capture and requires a
fresh target.

The read model emits the exact types and nulls fixed by the specification.
Empty expectation is not required and is satisfied. A nonempty active Task
reports target-required, Receipt-required, Receipt-blocking, or satisfied in
that order. A done Task backed by a matching version-0 cycle reports the
explicit legacy exemption even when its target is absent, including the sole
bridge-created cycle. A version-1 done cycle is validated against its linked
Receipt before projection. Task-show failure data includes a null
`verification_evidence`; text Task show remains unchanged. The Receipt write
alone has the fixed three-line text projection.

The initial completion-history JSON remains unchanged. The current done Task
retains its target and Receipt, so `task show.verification_evidence` exposes
the current qualifying basis; the cycle target tuple also identifies it.
Reopen advances the target and makes it historical. A later history projection
would require separate evidence and approval rather than silently changing
Viewer snapshot v4.

### Schema, Viewer, Packaging, And Tests

Migration 17 `verification_receipts` creates the Receipt table, indexes, triggers,
the three completion-cycle basis columns and insert/link guards, and the schema-
history row. Existing cycles receive only version 0/null-digest/null-link as a
structural legacy discriminator. It never parses verification text or events
to infer runs or create Receipt evidence. Reentry validates the exact objects,
columns, constraints, immutable triggers, ownership/link relations, and
absence of invented Receipt rows. Migration 18 adds the capture/provenance/
subject tables and columns described above, preserves exact v17 projections,
and creates no historical evidence. Migration 19 adds Bundle/link/snapshot and
projection state plus cycle version/null fields without inventing a historical
Bundle. Old binaries reject v19 normally; setup
remains the sole migrator. The established reopen bridge remains the only
post-migration writer of the exact version-0/null/null legacy shape.

Viewer source compatibility accepts v5-v19, while snapshot v4 fields and UI
remain unchanged. Receipt writes are Viewer-ineligible. The existing bounded
batch completion-history
loader performs bounded joins for selected version-1 cycle Receipt links and
v18+ subject/provenance/manifest/Reference relations plus the v19 Bundle
discriminator, validates them, and
discards every ledger field before snapshot formatting. There is no Receipt dataset,
snapshot field, panel, filter, or detail, and no per-Task Receipt query. This
compatibility update must land with the schema bump so explicit setup and later
unrelated Viewer maintenance cannot fail merely because the canonical state
migrated.

Focused tests reuse one matrix owner for result/coverage/current-binding gate
cases and existing migration/completion/Task-show helpers. They cover all
source schemas v1-v19, rollback/reentry, no legacy synthesis, exact target and
Contract invalidation, semantic verification edit, failure-generation reset,
unique per-generation ownership, version-0 legacy exemption, version-1
Receipt-link enforcement, the sole post-v17 legacy bridge, cycle-target
reconstruction, concurrent target/edit drift and expected-generation
rejection, read-only no-write, privacy rejection, byte/count bounds,
Evidence/backup maintenance, Viewer v19 compatibility including valid/corrupt
link and Bundle-discriminator batch validation, parser/help/
output, and unchanged unrelated projections. Test
facts and command inventories must remain derived from their existing owners
rather than copied into CI or multiple test modules.

## Task Contracts, Checkpoints, Handoffs, And Effort

### Immutable Task Contract Revisions

Schema v8 gives Tasks a current revision pointer and adds append-only
`task_contract_revisions`. Revision 0 has no row and projects empty fields.
Each positive row stores normalized scope, acceptance, optional constraints,
stable authority reference, change reason, and timestamp. Repository reads
require the pointer to reference the latest same-project/same-task revision;
the TG-M21.4D shared boundary enforces this before Contract projection or
Task-backed lifecycle use.
Scope and acceptance are each capped at 4,000 characters, constraints at
2,000, authority reference at 500, and change reason at 1,000.

Supplying any Contract option supplies the group and requires scope and
acceptance. Initial Contract recording is allowed:

- on Task add only for ready, in-progress, blocked, or review-pending; or
- for an existing revision-0 Task only in the exact ready/blocked to
  in-progress activation, with empty completion/review state and no companion
  mutation.

Initial change reason is empty. Later revisions are Contract-only edits while
ready, in-progress, paused, blocked, or review-pending and require a semantic
scope/acceptance/constraints change, bounded non-empty reason, and stable
authority reference. Done must reopen and cancelled rejects Contract input.
Authority can name a revisioned governing file/decision or exact
`user_instruction:<task-id>:<next-revision>`; raw prompt text and current Task
output are not authority.

Normalization converts CRLF/CR to LF and strips outer whitespace while
preserving internal text. Omitted later constraints retain the current value;
explicit empty removes them. Exact semantic replay returns the current
revision without write even if authority/reason labels differ. A real change
allocates the next revision under the writer, updates the pointer, resets all
completion evidence, clears/advances any started review target, moves
review-pending to in-progress, updates time, and appends a content-free
`contract_revised` event atomically. Old Contracts and review history remain.

Stored Contract projection has one narrow legacy M19.7 seam: only
`constraints_text` is validated through the bounded legacy reader and returned
unchanged. Normal Contract input never selects that reader. When later
constraints are omitted, the established carry-forward rule may copy those
already-validated bytes into the new immutable revision; this preserves
lineage and supplies neither caller-input acceptance nor authority.

Concurrent identical input records once and replays; different valid input
serializes into successive revisions. Current-or-next user-instruction
placeholders are rebound to the locked allocation. A lost-response retry with
the older placeholder may replay; an unrelated placeholder cannot authorize a
new semantic change.

### Typed Checkpoints

`task checkpoint` requires bounded summary and next action and accepts at most
eight bounded unresolved risks. Limits are 1,024 UTF-8 bytes for summary and
next action, 512 per risk, 4,096 aggregate risks, and 6,144 for caller input.
One append-only row stores those fields, Task/project, current Contract
revision, and time. The same transaction adds fixed event
`checkpoint_recorded` / `Checkpoint recorded` without content and does not
change `tasks.updated_at`.

Exact replay of the latest same-Contract checkpoint is no-write. Done Tasks
reject it. Current/show expose only the latest checkpoint; Viewer excludes
checkpoint content. A checkpoint is optional and changes no Task status,
scope, selection, review, evidence, or completion gate.

Only stored checkpoint `summary` projection may use the bounded M19.7 reader
for the former numeric `dispatch_authorization` JSON field. It returns the
original canonical stored summary and writes nothing. New summaries and all
other checkpoint fields use the ordinary privacy path.

### Local Handoff Outbox

Schema v7 owns `handoff_records` with source Task/current Contract revision,
canonical idempotency key, optional explicit occurrence ID, bounded
summary/rationale, state, adapter/delivery metadata, internal claim lease,
bounded receiver receipt, and withdrawal data. Public states are:

```text
pending_handoff
handed_off
handoff_withdrawn_by_user
```

The state matrix requires pending without terminal timestamps, handed-off with
acknowledgement time and no withdrawal, and withdrawn with user reason/time,
no receiver receipt, and zero delivery attempts. Claim tokens are never
public. Every stored free-form field and matrix is revalidated before output;
corrupt/private stored content returns a fixed internal error rather than
redaction.

Canonical compact JSON over project, source Task, source Contract revision,
normalized summary/rationale, and occurrence ID is SHA-256 hashed as the
unique idempotency key. Omission canonicalizes to empty; an explicit occurrence
must come from explicit user instruction or deterministic external identity.
Summary and rationale are each capped at 1,000 characters and occurrence ID at
200; an explicitly empty or invalid occurrence is rejected rather than treated
as omission.
Exact replay returns the row and writes nothing. The local transaction commits
before any possible delivery; success reports durable only after commit.
Handoff never changes source Task state, acceptance, timestamps, events, or
selection.

List defaults to pending, oldest-first, limit 20/max 100, with exact
`total_matching` and rows in one snapshot. Terminal states require explicit
filter. Show uses the full public allow-list, while Task show exposes only
per-state counts. Withdraw requires pending, zero attempts, no claim ever, and
a sanitized user reason; it is a single immediate transaction.

The shipped product has no receiver and no public sync command.
`adapter_enabled=false`; pending records remain durable and rediscoverable.
The schema reserves a local versioned idempotent claim/delivery state machine,
but a concrete Issue adapter remains blocked until a separately approved
local intake contract exists. Task Skill never performs Issue triage,
priority, lifecycle, network/GitHub access, arbitrary code loading, shell
execution, or Issue-database access.

Any later sink must use stable `handoff_id` as receiver idempotency key and
return only accepted/retryable/permanent plus a bounded receipt. Claims use
compare-and-swap and expiry; an ever-claimed row is never withdrawable.
Retry stages are fixed 60-second then 300-second waits, then exhausted, with
permanent error terminal for delivery attempts. This reserved state does not
add a normal Task-loop call until a real adapter is approved.

### Optional Effort Advisory

`effort.py` reads only strict optional
`<skill>/config/effort-advisory.json`. Version 1 accepts exactly profile
`informational-v1`, explicit enabled, and thresholds for five fixed metrics.
Missing or valid disabled configuration is off; invalid present configuration
is disabled with a bounded continuation diagnostic. There is no generic
configuration store, inheritance, environment override, writer, or configured
command runner.

Thresholds are nonnegative JSON integers for only changed files, changed
lines, changed modules, Contract revisions, and handoffs, and exceed only when
the measurement is strictly greater. An invalid profile uses the fixed
continuation warning; a threshold result uses at most one warning with code
`effort_advisory_threshold_exceeded`, key
`effort_advisory.threshold_exceeded.v1`, and fixed non-content message.

When enabled, the first transition to in-progress may best-effort capture
canonical Git basis, cleanliness, timestamp, project/subject activity
generations, and whether another task was active. Git observation is
argument-vector, optional-lock-disabled, no-lazy-fetch, fsmonitor-disabled,
submodule-ignored, no-external-diff/text-conversion, and read-only. Capture
failure stores no partial basis and never blocks start.

Metrics are changed files, changed lines, modules, Contract revision count,
and source-task handoff count. Attribution is unknown for non-Git, dirty or
uncertain endpoints, incomplete line coverage, or activity evidence of
overlap. Untracked/binary content makes line coverage unknown instead of
guessed. With a basis, pre/post DB observations are separately coherent around
Git; the post-read refresh detects overlap without keeping a transaction open.
Strict-off databases do no advisory bookkeeping.

The advisory never writes acknowledgement, asks a question, chooses a
handoff, changes status, expands scope/acceptance, or blocks completion.
`task show` mechanically exposes enablement. The Skill calls `task effort`
once at the existing verification/review boundary only when enabled.

## Approved TG-M16 Reduced Loop Discipline Trial Design

The existing Effort result chooses:

```text
valid enabled profile AND a nonempty ordered exceeded list
  => suggested_action=reconcile_scope
otherwise
  => suggested_action=continue
```

The same value appears in the one existing threshold warning. Attribution and
unknown reasons remain evidence only: unknown without an exceeded threshold
continues; an exceeded threshold with unknown attribution reconciles. This
adds no metric, profile field, database write, stored acknowledgement, routing
framework, or Task/handoff/review operation.

Reconciliation is session-local guidance, not persisted state.
`references/reconciliation.md` is loaded only after `reconcile_scope` or a
repeated test/review failure. After two materially equivalent failed repairs,
a third equivalent execution is prohibited without new evidence. Renaming a
wrapper, command, directory, Task, or execution unit is not new evidence; a
diagnostic is new only when its result can materially change the causal
hypothesis, authorized repair, or expected result. A fresh session resets the
comparison and relies on durable Task/event/review/handoff state.

Tests are never weakened merely to obtain PASS. A failing test or Effort
signal is evidence, not authority. A test may change only when current
authority establishes it is wrong; a Task Contract or acceptance change still
needs later explicit authority.

Scope reconciliation reuses the existing three-way classifier: work stays in
the current Task only when accepted scope and current authority cover it;
other discoveries go to local handoff; a blocker is used only after safe
authorized repair for the affected Task/lane is exhausted. Paused remains an
explicit temporary interruption, and unrelated ready lanes continue.
Remaining user decisions are batched.

Review remediation starts from the current blocking receipt/finding. A
meaningful fix sets a fresh target and obtains a fresh current-generation
result. A result that remains blocking counts as one unsuccessful cycle;
completion still requires fresh qualifying PASS receipts. After two
materially equivalent unsuccessful cycles without new evidence, no third
equivalent cycle runs; the same bounded blocker/decision path applies.

No M16 setup stage, Task seed, policy version, instruction-chain inspection,
target `AGENTS.md` mutation, persisted counter, alternate state machine, or
normal-loop call exists.

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
whole batch with the TG-M21.4C/TG-M21.4D validator before computing Task
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

They are independent same-process bounded work, never a thread, daemon, timer, queue, scheduler, service, sleep, or retry loop. Each uses its own zero-wait one-byte OS lock; lock-file existence is not ownership. Read, error, replay, no-op, configuration-only setup, doctor, Effort, and maintenance metadata writes do not invoke it.

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

## Static Viewer

### Snapshot And Publication

The Viewer is a replaceable projection, never authority:

```text
committed Viewer-relevant event
  -> source generation
  -> post-commit coordinator
  -> zero-wait Viewer lock
  -> one compatible query-only snapshot
  -> snapshot v4 + captured generation
  -> base64 UTF-8 JSON in bundled template
  -> atomic task-viewer.html replacement
  -> short rendered-generation update
```

Setup invokes the same canonical renderer directly. There is no public Viewer
command, output choice, browser launch, server, or browser-to-SQLite path.

The repository selects complete rows for all project Tasks in list order and
validates them as one source-schema-aware TG-M21.4C/TG-M21.4D batch before any
Task, review, or history projection. Exact v18/v19 snapshot validation first
validates the complete Task batch as part of the full Evidence Ledger, then the
same-transaction list-order query consumes the private proof above instead of
repeating scalar, privacy, and Contract validation. Sources v5-v17 and the
ordinary Viewer repository entry still validate the selected batch directly.
Validation failure occurs before rendering or replacement and preserves the
last-good HTML. It then selects at most 10 newest events per Task by time/rowid,
review evidence through the shared gate read model, and bounded completion
history. Snapshot v4 contains snapshot/source schema versions, generated time,
project ID/display only, counts, and explicit Task/event/evidence/history allow-
lists. It excludes repository/database paths, tool events, handoffs,
checkpoints, maintenance state, internal generation, event-cycle links,
environment, and raw review material.

Source schemas v5-v14 synthesize empty/incomplete completion history;
v15-v19 use stored cycles, reading completion histories in batches of at most
500 Task IDs. Sources v17-v19 validate version-1 cycle Receipt links; v19 also
validates and discards Bundle linkage. Every snapshot reports its
actual source schema and selects all
project Tasks; 500 Tasks is the accepted performance fixture rather than a
selection cap. The rendered artifact is capped at 64 MiB.

Snapshot JSON is deterministic UTF-8 and base64-encoded before insertion.
The template has exactly one snapshot placeholder and one decimal refresh
interval placeholder. Stored data reaches the DOM only through `textContent`
or text nodes, never HTML/eval/URL/style/event sinks.

The canonical output and lock remain below fixed state. Resolution rejects
reparse parents, containment changes, DB aliases, and linked/nonregular
destinations. Rendering writes a unique sibling temporary, flushes/closes it,
and `os.replace`s only after complete success; failure retains last-good HTML.

Schema v13 `viewer_maintenance_state` holds nonnegative source generation,
nullable rendered generation not above source, and fixed
`succeeded|deferred|failed` outcome/time. New/migrated state starts source 0
and rendered null. Under the lock, publication captures generation and rows in
one snapshot, closes SQLite, renders/replaces, then conditionally records the
captured generation without lowering a newer value. One recheck permits one
follow-up; later churn remains due.

### Presentation Profile

The only presentation policy is optional:

```text
<physical-skill>/config/viewer.json
```

Absence is valid and disables reload with interval 0. Taskgov never creates or
edits it. A present regular physical UTF-8 file is capped at 16,384 bytes and
must contain exactly:

```json
{
  "schema_version": 1,
  "profile": "visibility-refresh-v1",
  "refresh_interval_seconds": 30
}
```

The interval is a JSON integer 5-3,600; booleans, floats,
duplicate/unknown/missing keys, malformed/trailing JSON, links/reparse paths,
devices, directories, replacement races, or uninspectable metadata fail
closed with one sanitized error. The loader uses no-follow where available and
checks descriptor/path identity before and after the bounded read. Doctor does
not inspect this file.

One publication attempt loads the value once and reuses it for both possible
renders. Setup preview treats an invalid present profile as Viewer
repair-required but writes nothing; write setup and routine publication retain
last-good output and use existing incomplete/warning semantics. Missing or
changed profile/template bytes make setup repair the canonical page.

### Browser Application And Reload

The page provides a compact header, all-status summary, search/status/kind/
lane/priority/tag/terminal filters, responsive Task table, detail/events, and
empty state. Terminal Tasks are hidden by default but filterable. Native
controls, visible focus, labels, non-color status cues, bounded radii, and
wrapping support keyboard and narrow-screen use.

Reload scheduling activates only after successful fatal-UTF-8 decode and
render, with a valid nonzero interval and `file:` protocol. One reconciliation
function owns one timeout:

1. clear the prior handle;
2. stop if disabled, already requested, non-file, or hidden;
3. compute elapsed from page-load `performance.now()`;
4. request exactly one same-document reload once elapsed reaches interval; or
5. schedule only the monotonic remainder.

Timeout and visibility-change callbacks reuse that function. Hidden pages own
no timer. There is no interval timer, polling, wall-clock interval
calculation, fetch/XHR, storage, worker, database access, retry, or message
channel. Browser throttling may make reload late, never early.

Immediately before that automatic reload only, the page may make a one-shot
History API handoff with exact ordered keys:

```text
owner, schema_version, captured_at_ms, status, kind, lane, priority, tag,
terminal, selected_task_id, scroll_x, scroll_y, focus_id
```

Owner is `taskgov-viewer-auto-reload`, schema is 1, and compact UTF-8 JSON is at
most 4,096 bytes. Only allowed filter values, visible selected Task ID,
nonnegative finite scroll coordinates, and one of eight fixed control IDs may
be saved. Search text, Task/snapshot content, arbitrary selectors, URLs/paths,
dynamic-row focus, selection ranges, and nested UI state are prohibited. No
selection means no envelope and reload still proceeds.

Capture time is a nonnegative safe integer; status, kind, and priority are
empty or current enums; lane and tag are each at most 1,024 UTF-8 bytes;
selected Task ID is nonempty and at most 128 code points; coordinates are
finite integers from 0 through 2,147,483,647; and focus is empty or exactly
`search-filter`, `status-filter`, `kind-filter`, `lane-filter`,
`priority-filter`, `tag-filter`, `terminal-filter`, or `reset-filters`.

Save never overwrites a non-null non-owned state and calls only
`history.replaceState(candidate, "")` without URL. Failure never prevents
reload. On a file load, every owned state is cleared before snapshot decode,
even if malformed, stale, non-reload, or decode-fatal. Restore requires
successful clear, navigation type reload, exact keys/types/bounds/current
options, visible selection, current owner/version, and age 0-300,000 ms.
Invalid or failed restore returns filters, selection, focus, and scroll to
defaults.

On an enabled file page or one starting with owned state, manual
`history.scrollRestoration` must be set and read back before UI restoration.
If unsupported, state save/restore is disabled but owned state is still
best-effort cleared and reload continues. History state is browser-managed and
may survive session restoration; it is bounded and one-shot, not described as
memory-only.

The page never uses `pushState`, URL/query/fragment state, cookies, Web
Storage, IndexedDB, Cache API, service workers, cross-tab messaging, or manual
reload capture. It logs no state, bytes, validation reason, exception, URL, or
path. Saving/restoring must change neither `history.length` nor
`location.href`.

### Browser Security

The exact CSP is:

```text
default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; media-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; manifest-src 'none'; base-uri 'none'; form-action 'none'
```

Inline script/style is limited to the fixed single-file application;
`unsafe-eval` is absent. Stored values cannot create markup, script, style,
event handlers, URLs, or resource loads. The browser has no external URL,
telemetry, automatic launch, network API, or database-write code.

## Privacy, Safety, And Failure Boundaries

Every free-form input uses the common deny-by-default privacy guard. Fields
with defined contracts also have code-point/UTF-8 bounds; the existing `lane`
and `blocked_reason` fields are privacy-validated but have no numeric character
cap. `lane` is trimmed; `blocked_reason` is retained as supplied after
string/privacy and state validation. The guard rejects likely credentials and raw dumps,
including authorization/Bearer headers, private-key blocks, password/token/API
key assignments, Python traceback headers, raw stdout/stderr headings, and
repeated raw Git diffs.

The ordinary matcher receives caller text unchanged. It rejects both
`dispatch_authorization=<value>` and the JSON key
`"dispatch_authorization":<value>`, including numeric values; no generic
allow-list or mode exists. Neutral `operation_sequence=<positive canonical
integer>` passes without preprocessing and represents correlation or
idempotency evidence only. External authority remains an independent current
user/Contract decision.

The singular legacy M19.7 stored-text helper creates a privacy-only guard view:
it substitutes bounded lowercase positive-canonical-integer equality and
numeric JSON counter forms with a fixed non-secret sentinel, runs the complete
ordinary detector set, and returns the original text. Call sites are limited
to stored Contract constraints and stored checkpoint summary projection.
Completion history deliberately uses only the ordinary matcher. The helper
does not rewrite a row, broaden a schema or writer, or authorize a Task,
dispatch, Git, network, or other external mutation. Compound credential/token
content remains visible after substitution and fails closed.

Stored or emitted data excludes secrets, cookies, provider bodies,
authorization material, raw stdout/stderr, stack traces, environment dumps,
full prompts/conversations, private reasoning, raw reviews, large diffs,
unsafe paths, and OS/SQLite/Git exception detail. Review, Contract, handoff,
checkpoint, completion, and history projections use explicit allow-lists and
revalidate stored text before output.

Git subprocesses use fixed argument vectors, no shell, bounded timeout, safe
environment, disabled optional locks/lazy fetching, and no target-project
write. Taskgov never creates a commit, branch, PR, Issue, tag, Release, or
network request as product behavior. TG-M19 release operations are repository
release work performed only under their separate approvals, not new Taskgov
commands.

Read-only commands do not create SQLite sidecars, locks, directories, state,
Viewer output, Git changes, or target files. Atomic file publication and
last-good preservation protect generated artifacts. The threat model covers
ordinary misunderstanding, over-implementation, local races, crashes, and
accidental path changes; it does not add signing, hostile-kernel protection, or
general adversarial TOCTOU defense.

## Published Release Artifact And Compatibility Design

The v0.10.0 publication is an external projection of one reviewed repository
commit, not a product runtime subsystem. The exact accepted commit, remote
`main`, and unpeeled lightweight tag `v0.10.0` are
`a9b80ce177a6dead10d51a070b76ff01f7af0294`. GitHub Release `362617903`
has prerelease visibility. Runtime code does not derive current behavior from
a branch, tag, Release, local candidate directory, or historical Task evidence.

The release archive has exactly one `task-governance-tool/` root and is built
from the accepted commit with the package subtree as the sole pathspec. The
package `release-manifest.json` is the inventory and digest boundary for
packaged core files. Root and packaged `LICENSE` bytes are the same official
unmodified Apache-2.0 text, and the package license is manifest-covered.
Generated state, SQLite files and sidecars, backups, locks, Viewer output,
target configuration, source-repository tests and fixtures, root references,
caches, logs, secrets, and scratch output remain outside the artifact.

The published archive and checksum identities are fixed:

```text
archive              task-governance-tool-0.10.0.zip
archive sha256       99fc2345fd036091349c47f7379eee25b8b3b4c8873c0f74aaceac323bb82a03
checksum             task-governance-tool-0.10.0.zip.sha256
checksum sha256      9cdc99bd26cc4887bd88ef2ec638659224f0a0d8f567edb12a3800d59a8b6764
release notes        docs/releases/v0.10.0.md
notes sha256         aaa118a3fbbb261ec6a24f7a80f50f161e606a86857f99e17f957f34ba044a03
candidate CI         run 30561916953, attempt 1
remote-main CI       run 30565181070, attempt 1
```

Release staging, GitHub workflow selection, push readback, and Release upload
are orchestration outside the CLI architecture. They add no taskgov command,
schema table, runtime module, background process, network client, credential
store, or target-project mutation path. A later release may reuse the durable
principles—exact reviewed tree, manifest-complete package, deterministic local
checks, explicit authority for each external mutation, and readback after an
ambiguous write—but must define and approve its own exact identity and
evidence. Completed v0.10.0 approval objects and gate checkpoint schemas never
authorize a future operation.

The release boundary is immutable by policy. A defect creates a reviewed
forward-fix candidate and new version. Routine repair does not force-update
`main`, rewrite history, move or replace a published tag, replace an existing
asset, or delete a Release to hide disagreement.

The accepted upgrade rehearsal treated package files, database, and managed
artifacts as one compatibility point. It created legacy schema-v2 state with
the exact legacy package, preserved that state while installing v0.10.0,
allowed current `setup` to back up and migrate through schema v16, and proved
restart, identity/record preservation, quick check, foreign keys, and Viewer
publication in isolation. The paired rollback restored the matching legacy
package, database, and managed artifacts before running legacy code. Old code
against v16, reverse migration, mixed generations, arbitrary `--db` state,
and a source checkout used as state rollback remain unsupported.

The detailed M19 history switch, candidate staging, evidence schemas, remote
state machine, and just-in-time activation rules were one-time execution
design. The exact publication-commit form is indexed by
[the historical documentation index](history/README.md); no active product or
release guarantee depends on that historical copy.

## Accepted But Inactive TG-M20S.3 Activation Design

TG-M20S.3, Task `tg_task_286129dbca4d25ab`, freezes the instruction-layer
design for the accepted decomposition contract in `docs/specification.md`.
Nothing in this section is wired into the current package. A later separately
approved activation changes Skill guidance and package references while the
deterministic CLI, repository interfaces, schema, Viewer, and command inventory
remain unchanged.

### Instruction-Layer Classifier

The future Skill treats one explicit user message as one session-local event
and classifies it as either `registration` or `mid_task_scope_addition` before
considering decomposition. An in-scope discovery, test failure, Effort result,
cross-module failure, inferred dependency, or model preference does not create
either event.

For that event the agent forms a bounded worksheet containing only:

- the total authorized outcome and unchanged permission boundary;
- candidate outcome/acceptance partitions already supported by authority;
- `acceptance_independent`, `verification_independent`,
  `commit_independent`, and `completion_independent`, each as
  `yes|no|unknown` with a concrete reason;
- the existing `kind`, `lane`, `order`, and review tier capable of expressing
  the proposed sequence; and
- any explicit user placement direction.

The worksheet is transient reasoning, not a database record, JSON contract,
Task field, or prompt-log artifact. Aggregate independence is `yes` only when
all four fields are `yes`, `no` when any field is `no`, and `unknown` otherwise.
A known `no` therefore dominates any simultaneous unknown. The scope-
conservation check is separate: the candidate union must equal the authorized
outcome exactly, with no overlap, omission, new permission, or inferred work.

The classifier performs one pass. It cannot classify the result again,
subdivide a proposed successor, invent a parent, or use size/effort metrics as
predicate evidence. Candidate count cannot exceed the one-to-one, explicitly
accepted outcomes in the initiating authority; an outcome cannot be expanded
into candidates by file, step, test, or implementation detail. This makes the
number of potential writes finite without an arbitrary repository-wide cap.

### Registration Adapter

For explicit taskization, the classifier returns one of
`register_bounded_set`, `register_single`, or `no_write`:

1. `register_bounded_set` requires aggregate `yes` for every candidate, proven
   conservation, representable order, and unchanged permissions. The existing
   `task add` leaf is invoked once per candidate; the initiating request is the
   approval for the finite set and no per-Task confirmation is inserted.
2. `register_single` is the negative and unknown fallback only when one exact
   Contract can truthfully preserve the whole authorized outcome and the user
   did not mandate incompatible separate boundaries. It invokes `task add`
   once and does not ask a decomposition-only question.
3. `no_write` applies when even the whole Contract is not authorized or
   expressible, or when the user mandated exact separate Tasks but a boundary
   is `no` or `unknown`. The agent reports the missing fact or conflict without
   inventing, silently merging, or partially redefining Tasks.

If a repeated existing `task add` sequence fails after a strict subset was
written, the agent stops the pass, inspects the exact registered set through
existing reads, and reports or durably preserves the still-authorized
remainder. It adds only unambiguously missing candidates after the ordinary
failure is resolved; it never blindly retries, duplicates a Task, expands the
partition, or starts a recursive decomposition pass.

Registration changes governance state only. It grants no implementation,
target-project, Git, network, or external-system permission.

### Mid-Task Adapter And State Effects

For an explicit addition to an `in_progress` or `review_pending` Task, the
future Skill first chooses the scope disposition, then its continuation state:

1. recognize a semantic no-op as `keep-current`;
2. honor an explicit keep-current placement as `revise-current` when the
   addition is new and can be incorporated safely;
3. with aggregate `yes` and proven conservation/order, either register the one
   successor when the same message explicitly approves separate registration,
   or emit one write-free `propose-successor`;
4. with aggregate `no` or `unknown`, forbid splitting and use
   `revise-current` when one exact safe Contract can hold the addition; and
5. otherwise use `handoff` as the bounded scope-preservation disposition;
   after it, continue for a nonblocking addition or overlay `pause` or `block`
   under the existing temporary or acceptance-preventing rules and only after
   safe authorized work is exhausted.

The proposal contains the exact proposed Contract, review tier, lane/order,
permission boundary, and current-versus-successor partition. A proposal does
not call taskgov. While it is unresolved, safe work within the unchanged
current Contract continues. If the session or current Task would end first,
the existing `handoff record` path stores only its at-most-1,000-character
sanitized summary covering every added outcome and an at-most-1,000-character
pending-decision rationale. It is deliberately not an
encoding of the full proposed Contract, review tier, lane/order, partition, or
event identity. A resumed agent surfaces the unresolved Handoff but neither
reconstructs nor registers a successor from it; missing exact fields require
current explicit authority. Rejection, paraphrase, clarification, or a direct
answer about that proposal does not reset the one-proposal allowance; only
materially changed user authority for scope, order, or permission creates a
new event. No persisted event ID, proposal counter, or new state is added.

An approved successor uses one existing `task add`. A successor that must
follow the current Task uses the next available order in its sequential lane;
`optional` is used only when the outcome is genuinely independent. An
unrepresentable relationship fails decomposition instead of creating an
implicit dependency. An explicit instruction to keep the work current uses the
existing Contract-only `task edit` path. Its current revision, target,
receipts, findings, completion evidence, and `review_pending -> in_progress`
effects remain exactly those of the existing semantic-revision contract.

`keep-current` and a pending proposal perform no Contract write. Their current
target and evidence remain valid only for unchanged target material; ordinary
target replacement and fresh review still apply after later material changes.
Handoff preserves but does not expand acceptance or satisfy the total user
request. If an addition cannot safely enter a Contract but prevents acceptance,
the Handoff write occurs first and the applicable pause/block status write
second. Pause and block never replace the scope disposition or create a Task.
When `revise-current` must be followed by a pause or blocker, the Contract-only
edit is performed first and the separate status edit second, preserving
existing CLI validation. The normal current/next/execution/review/completion
green path adds no governance call; only the two explicit event paths can
invoke the already-public writes described above.

### Atomic Future Synchronization Boundary

A later separately approved Tier 2 activation must change these surfaces in
one reviewed revision:

- add only the concise trigger gate and disposition rules to
  `task-governance-tool/SKILL.md`;
- place the full predicates, decision tables, ordering examples, restart
  preservation rule, and negative/unknown cases in
  `task-governance-tool/references/task_workflow.md`;
- update `task-governance-tool/release-manifest.json` for the changed package
  digests and apply the repository's then-current version/release rules;
- switch the inactive markers and implementation-facing routing in
  `docs/specification.md` and `docs/design.md`, and synchronize the approved
  static execution contract in `plan.md`; and
- update `tests/test_skill_self_containment.py`,
  `tests/test_m14_integrated_acceptance.py`, and
  `tests/test_document_history.py`, adding a focused test module only if those
  owning suites cannot express the behavioral cases without duplication.

`task-governance-tool/agents/openai.yaml` is in the synchronization review set
but is expected to remain byte-identical because its current registration and
scope-preservation metadata already covers the two triggers. Scripts,
migrations, repositories, CLI parsing/output, Viewer code/template, and public
command leaves are outside the write set and must be proven unchanged. The
activation must be proposed and approved separately; TG-M20S.3 registers no
implementation Task.

### Neutral Forward-Test Boundary

Activation acceptance uses fresh, minimal-context agents that receive the
candidate Skill and neutral workloads, not the expected branch or M20S study
result. A separate evaluator checks both the response and resulting Task DB.
The fixed matrix includes:

- registration: an eligible multi-outcome partition, a large but atomically
  coupled outcome, an unknown verification/commit boundary, and user-mandated
  separate boundaries that are not independently valid; plus an injected
  failure after one successful `task add`, followed by exact-set inspection,
  explicit or durable remainder preservation, and retry of only the
  unambiguously missing candidate with no duplicate or repartition;
- mid-Task positive paths: already-covered scope, explicit keep-current,
  independently eligible addition without placement, and an explicit separate-
  Task instruction;
- mid-Task fallbacks: concrete coupling, incomplete evidence, nonblocking
  preservation, revise-then-pause, revise-then-block, a temporary no-safe-
  progress wait, and an acceptance-preventing authority or safety failure; and
- invariants: one proposal per event, no Task write before approval, no
  recursive or size-only split, exact scope conservation, correct Contract and
  review invalidation/preservation, unchanged current command leaves and schema,
  fresh review when changed material escapes an unchanged Contract's prior
  target, Handoff/resume in a fresh session without reconstructing or emitting
  a second proposal even when valid proposed Contract fields exceed Handoff
  bounds, and no invocation from discovery, test failure, Effort, or cross-
  module failure alone.

Positive, negative, and unknown cases use parallel wording and equal available
authority so the prompt does not reveal the expected result. A valid result
must match the specification branch and actual stored effects; self-reported
intent alone is insufficient. Current behavior is not forward-tested against
these expectations because activation has not occurred.

## Validation And Test Design

The suite is standard-library-first, offline, and isolated. It must not mutate
a real consuming project or Git state. Tests cover:

- all 21 parser leaves, removed commands/options, help, text/JSON/error/compact
  envelopes, and byte limits;
- missing/old/too-new/invalid state with no creation or sidecars;
- every v1-v19 migration, rollback, idempotency, required-object marker,
  realistic preservation fixture, quick check, and foreign keys;
- task validation, ordering, pause/block/current/next, done/reopen,
  completion evidence, every review tier/target/receipt/finding, Contract,
  checkpoint, handoff, and Effort route;
- the TG-M21.4C shared stored-Task fault matrix once in a focused validator
  module, plus representative public/lifecycle/doctor/Viewer route canaries,
  selected-batch query bounds, no-write failure, and last-good publication in
  a separate consumer-boundary module;
- the TG-M21.4D Contract-pointer matrix in one focused relation module and its
  selected-batch single-query, pre-v8 no-query, recovery set-fatal, valid-state,
  lifecycle, doctor/setup, and last-good Viewer canaries in one separate
  consumer-boundary module rather than duplicating every command fixture;
- Git option safety, canonical commit resolution, snapshot/index/tree binding,
  no mutation, and no lazy network;
- concurrent readers/writers, short writer ownership, stable busy/WAL errors,
  and injected rollback points;
- UUID identity, legacy identity, fixed/legacy resolver inventories,
  same-binding publication, recovery, relocation token/replay/expiry, staged
  no-clobber publication, cleanup resume, and preservation of unrelated files;
- backup publication/reconciliation/retention/recovery and every crash
  boundary;
- Viewer v4 sources 5-19, completion-history bounds, version-1 Receipt-link
  and v19 Bundle-discriminator validation, 500-ID history batching,
  the accepted 500-Task performance fixture, 64-MiB artifact cap,
  generation/last-good behavior, strict config, timer/visibility, one-shot
  History state, CSP, text-only DOM, and absence of storage/network APIs;
- package self-containment, manifest integrity, project-scoped/self-host
  layouts, ignore rules, Windows Python 3.12/3.14, and junction rejection;
- M16 fresh-session behavioral fixtures plus the current ten-call default flow
  and mechanically enabled eleven-call flow; and
- release archive reproducibility, license/manifest/archive inclusion,
  legacy upgrade/paired rollback, exact workflow identity, and sanitized
  release evidence.

Repository release consistency uses the same offline read-only checker from
focused unit fixtures and CI. Negative fixtures independently cover a missing
manifest or declared file, extra packaged core, digest mismatch, license or
manifest-license mismatch, invalid Skill/agent/manifest metadata, parser-to-
document drift, and tracked generated artifacts. The checker does not replace
installed-CLI, no-write, physical-isolation, migration, upgrade/rollback, or
release-acceptance behavior tests.

The deterministic repository runner assigns every discovered test module to
exactly one base lane:

- `fast` covers parser, validation, state transitions, pure helpers, and small
  temporary repositories;
- `integration` covers setup, recovery, relocation publication, backup,
  Viewer, concurrency, consumer layouts, and normal migration; and
- `release` covers legacy migration/recovery, upgrade and paired rollback,
  performance, package/license, active documents, workflow policy, and
  integrated release acceptance.

Every run first validates the complete manifest. A base lane is an ordered
filter of standard discovery, while `all` runs the original discovered suite
in its original order. New `test*.py` modules therefore fail closed until
classified; new methods in an owned module inherit that module's lane.

`tools/test_lanes.py` also owns one closed mandatory-native set containing
exactly
`test_m241a_lpac_portability.RunnerLpacPortabilityNativeTests.test_real_lpac_portability_matrix_and_cleanup`.
Discovery must resolve that
stable ID exactly once in the `integration` owner and therefore in `all`.
After execution, its presence in `unittest`'s skipped results makes the lane
non-PASS even when `wasSuccessful()` is otherwise true. Missing, renamed,
duplicate, or non-`integration` assignment is a policy failure; a lane that
does not select it is unaffected, and unrelated optional SKIPs are not
promoted to failures. The private, import-inactive implementation seam is
`_verification_runner_lpac_win32.py`; the direct native fixture and its focused
pure/fault coverage do not import or activate the broader TG-M24.2 provider or
process implementation.

The normal control used by the portability matrix is always suspended, never
resumed, and contained in its own Job. Both children use the same stdio
configuration and four-attribute shape, but distinct OS handles and distinct
Jobs; the sole attribute-value difference is the fixed ALL-APPLICATION-
PACKAGES-policy `DWORD`, exactly `0` for the normal control and exactly opt-out
`1` for LPAC, so a parent opt-out cannot leak into the control through
attribute omission. The host's real selector route must be non-SKIP; a
fault-injected integration test forces the opposite route without being
labeled native selector evidence, and `WIN://NOALLAPPPKG` is never a decision
input. Each public-`AccessCheck` descriptor has SYSTEM owner/group and exactly
two `FILE_READ_DATA` (`0x00000001`) allow ACEs, coordinator user first and the
selected AAP or exact Package SID second, with no extra ACE. The three required
outcomes are normal+AAP allow, LPAC+AAP deny, and LPAC+exact-Package allow. The
portability test calls the seam directly and proves both separate Jobs/tokens,
no-resume paths, and cleanup without calling `run_process_steps` or depending
on the full process/registry matrix. That full matrix is a current TG-M24.2
completion gate but is not yet in the repository mandatory-native set.
TG-M24.2 must integrate the accepted private LPAC seam, keep registry
`0x80070002` and every other registry failure fail-closed, and add the full
process/registry stable ID with non-SKIP enforcement before acceptance. Until
those verified completion changes land, no broader Runner process/registry
implementation is active.

CI obtains one compact include matrix from this same policy. Pull requests run
all three base lanes on Python 3.12 and `fast` on 3.14. Pushes to `main` run
all three base lanes independently on both versions. Manual
`workflow_dispatch` runs monolithic `all` on both versions, and an
`always()` aggregate job fails unless policy validation and the full matrix
succeed. The workflow trigger remains limited to pull requests, pushes to
`main`, and manual dispatch; a candidate gate cannot be replaced by a skipped
dependency job.

Tier-2 changes use two independent exact-target reviews. Tests hard-fail count,
byte, subprocess, attempt, and render limits; performance budgets never justify
loosening a deterministic bound or adding asynchronous architecture.

## Deferred Boundaries

Deferred work includes profile authoring, verification-command execution,
external Issue delivery, dependency graphs, task import, pagination/search,
stale detection, parent/child/checklist execution units, manual backup/restore/
export, generic browser-state persistence, live server, browser launch,
network synchronization, and update checking.

Any extension, including the accepted but inactive TG-M20S.3 design, must
preserve local-first operation, current privacy and target-project safety,
explicit authority for mutation, narrow repository boundaries, and concise
Skill guidance. It requires synchronized specification, design, plan, tests,
and review rather than reuse of a historical design capture.
