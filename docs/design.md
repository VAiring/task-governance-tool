# task-governance-tool Current Implementation Design

Status: the immutable published product remains v0.10.0 with SQLite schema
v16, Viewer snapshot v4 accepting source schemas v5-v16, and 20 public command
leaves. The accepted release commit, remote `main`, and lightweight tag
`v0.10.0` are
`a9b80ce177a6dead10d51a070b76ff01f7af0294`; GitHub Release `362617903` has
prerelease visibility. The current unpublished local candidate is v0.11.0 with
SQLite schema v17, Viewer snapshot v4 accepting source schemas v5-v17, and 21
public command leaves; it activates the bounded TG-M21 Verification Receipt
design below without claiming a tag, remote commit, or published Release.
TG-M21.4 now freezes an accepted but inactive schema-v18 correction from
caller-authored labels to a taskgov-owned verification subject. TG-M21.4A
also freezes the schema-v18 verification-capacity compatibility correction
below. TG-M21.4B implements the current v17 recovery-candidate classifier and
TOCTOU reconciliation without activating schema v18 or public-limit changes.
TG-M16.4
behavioral acceptance remains part of the published baseline. The TG-M20S
successor observation reached its frozen `proceed_to_design` result and
no-rerun retirement boundary. TG-M20S.3 now freezes an accepted but inactive
Tier 2 decomposition design; it changes no current runtime, package, or agent
behavior. TG-M22.1 now freezes the accepted but inactive Evidence Ledger,
Completion Evidence Bundle, and deterministic JSON design below; it changes no
current runtime, schema, package, generated artifact, or agent behavior. The
approved M21 and M22 execution contracts remain governed by their own
dependency and synchronization gates; the Task database owns their live state.

This document is the current implementation design for the behavior specified
in `docs/specification.md`. Historical design captures under `docs/history/`
are non-authoritative and are never needed to implement, operate, migrate, or
review the supported product.

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
  specification.md
  design.md
  history/
plan.md
tools/
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
`<repo>/task-governance-tool` when the four governing source documents and the
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
  current/list projections, the shared sequential predecessor predicate, and
  next-task selection.
- `completion.py`, `completion_workflow.py`, and `git_snapshot.py` own typed
  completion evidence, read-only Git observations, completion planning, and
  review-to-commit snapshot binding.
- `completion_history_projection.py` owns the bounded public cycle projection;
  `storage.py` alone inserts immutable cycles.
- `verification_receipts.py` owns caller Receipt validation, exact-current
  classification, completion-gate evaluation, and the bounded Task-show read
  model; `storage.py` alone owns Receipt persistence, migration structure, and
  the deterministic command-label predicate shared by public input and
  stored-row validation.
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

Every migration is ordered, idempotent on reentry, transactional, and
rollback-tested. Reentry validates rather than synthesizing missing data.
Table rebuilds preserve foreign keys and IDs and restore foreign-key
enforcement in a `finally` path. Migration validation uses
`PRAGMA quick_check`, `PRAGMA foreign_key_check`, exact row/object
preservation, and the sanitized realistic 12-task/191-event fixture with nine
historical completion hashes and representative review, Contract, handoff,
checkpoint, maintenance, identity, and completion traces.

The fixed-state setup migrator accepts complete source schemas v1-v16 and
treats v17 as current. Legacy `state/projects` discovery is intentionally
narrower: v1-v13 plus the explicit schema-v14 legacy-layout transition.
Viewer compatibility is independent and accepts source schemas v5-v17.
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
- canonical database, backup, Viewer, lock, and recognized temporary paths; and
- at most one bounded regular temporary for each exact restore, backup, and
  Viewer temporary-name grammar.

Recovery classification is deliberately two-phase. The resolver opens every
recognized candidate and completes the physical, SQLite, schema, identity,
lineage, metadata, and whole-set checks above against the mechanically newest
structural head. It then calls the storage-owned exact Task-verification
validator with that candidate's source schema. The validator returns normally
or raises a sanitized internal privacy/capacity rejection; every other storage
failure remains structural and set-fatal. The current implementation accepts
at most 500 characters for supported schema v17 and earlier. M22.2 changes the
same stored validator for schema v18 rather than adding a recovery-only limit.

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

The recovery-specific backup scan uses the same storage validator, excludes
only its privacy/capacity rejection from selection, and converts corrupt,
foreign, unsafe, duplicate, overflow, or otherwise structurally invalid
recognized material to the restore-failure boundary. Recovery normalization
keeps every structurally coherent artifact in the ordinary generation
row/file/pointer envelope even when the copied candidate is older; local
rejection never hides or legalizes a missing or mismatched relation. It does
not rewrite rejected artifacts, while later ordinary retention keeps its
existing authority over managed generations.

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
schema v1-v16; `legacy_projects_v1` is capped at the explicit v14 transition,
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

### Schema V15, V16, And V17 Activation

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

1. validate schema v17, identity/binding, optimistic Task basis, Contract,
   sequential ordering, evidence, and target;
2. reread Verification Receipts and review receipts/findings, evaluate the
   current verification and review gates, and select their deterministic
   bases;
3. choose canonical completion time and next ordinal;
4. insert one immutable complete verification-basis-v1 `native_done` cycle,
   including the exact expectation digest and required Receipt link;
5. update the current Task to done with identical evidence;
6. rerun lane invariants;
7. insert the existing completion event with the internal cycle link; and
8. record existing Effort/Viewer-generation effects and commit.

Any failure rolls all business rows back. Concurrent completions serialize; a
loser creates no second cycle. Read-only completion check inserts nothing.

Reopen locks the done Task, loads the highest cycle and linked reopen state,
and compares that cycle with the entire current completion projection. When
coverage is `legacy_unknown` and no cycle exists, it may insert the exact
ordinal-1 partial compatibility bridge. Complete coverage without a cycle,
any existing-cycle mismatch, or an already linked reopen returns
`completion_history_inconsistent`. It then clears current gates, preserves
coverage and cycles, inserts `task_reopened` linked to the validated cycle, and
commits. It never revalidates historical Git material, uses historical
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
Source schemas v5-v14 synthesize empty/incomplete history; v15-v17 use stored
rows. Source v17 validates linked Receipt ownership, basis, target, and
pass/full qualification in the bounded history batch before discarding every
Receipt field. Neither public events nor Viewer disclose internal event-cycle
or Verification Receipt IDs.

The only new stable error is
`completion_history_inconsistent: stored completion history is inconsistent`.
It exposes no IDs, counts, values, hashes, SQL, or paths.

## Current TG-M21 Verification Receipt Design

This section defines the current post-publication schema-v17 implementation
boundary. It does not rewrite the immutable v0.10.0 artifact or claim a later
published artifact identity. Schema, parser, completion, Viewer compatibility,
Skill guidance, package inventory, and tests activate atomically; TG-M21.3
accepts that exact target without adding a new surface.

### Ownership And Data Model

`verification_receipts.py` owns Receipt input validation,
exact-current classification, gate evaluation, and the bounded public read
model. `storage.py` alone owns schema, migration, append/read queries, and
Receipt uniqueness. `tasks.py` and `completion_workflow.py` consume the gate
result; neither opens raw SQLite or interprets verification prose.
`cli.py` owns only parser/dispatch/formatting for the one Receipt write leaf.

Schema v17 adds an immutable `verification_receipts` table with:

```text
verification_receipt_id project_id task_id contract_revision
verification_expectation_digest command_label result duration_ms
scope_coverage target_kind target_value target_base_revision
target_generation created_at
```

IDs use the `tg_verification_receipt_<16-lowercase-hex>` grammar. Result is
`pass|fail|timeout`; coverage is `full|partial`; duration is a nonnegative
signed-64-bit millisecond integer. The label is nonempty, privacy-validated,
and at most 200 characters. Target fields reuse the existing four-way matrix.
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

The write service first validates the four caller-supplied Receipt facts plus
positive `expected_target_generation` without opening a writer, then starts
one short immediate transaction and rereads schema,
identity/binding, Task, Contract pointer, exact verification text, and target
tuple. It permits only in-progress or review-pending Tasks with specified
verification and a current target. Expected generation must equal the locked
target generation; drift returns `verification_basis_stale` without storage.
It computes the digest, appends the row without changing the Task or adding an
event, commits, and only then invokes backup-eligible, Viewer-ineligible post-
commit maintenance.

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
stored target structure/presence, expected generation, then uniqueness. It is
used before and after the writer lock so a concurrent status, Contract,
expectation, or target change cannot select a different semantic ordering or
bind the row to new material. CLI formatting owns the exact three-line success
text and adds no synthetic Task event.

The shared command-label predicate applies before the supported writer and
during stored-row validation. It combines the common secret/raw-log scan with
deterministic rejection of shell controls, standalone options, path-prefixed
invocations, executable/script-suffixed leading tokens, and recognized
command-runner prefixes including Windows executable forms. A row inserted
outside the supported repository boundary therefore cannot become accepted
Receipt history.

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

Migration `verification_receipts` creates the Receipt table, indexes, triggers,
the three completion-cycle basis columns and insert/link guards, and the schema-
history row. Existing cycles receive only version 0/null-digest/null-link as a
structural legacy discriminator. It never parses verification text or events
to infer runs or create Receipt evidence. Reentry validates the exact objects,
columns, constraints, immutable triggers, ownership/link relations, and
absence of invented Receipt rows. Old binaries reject v17 normally; setup
remains the sole migrator. The established reopen bridge remains the only
post-migration writer of the exact version-0/null/null legacy shape.

Viewer source compatibility accepts v5-v17, while snapshot v4 fields and UI
remain unchanged. Receipt writes are Viewer-ineligible. The existing bounded
batch completion-history
loader performs one internal join for selected version-1 cycle Receipt links,
validates only ownership/digest/target/qualification, and discards every
Receipt field before snapshot formatting. There is no Receipt dataset,
snapshot field, panel, filter, or detail, and no per-Task Receipt query. This
compatibility update must land with the schema bump so explicit setup and later
unrelated Viewer maintenance cannot fail merely because the canonical state
migrated.

Focused tests reuse one matrix owner for result/coverage/current-binding gate
cases and existing migration/completion/Task-show helpers. They cover all
source schemas v1-v17, rollback/reentry, no legacy synthesis, exact target and
Contract invalidation, semantic verification edit, failure-generation reset,
unique per-generation ownership, version-0 legacy exemption, version-1
Receipt-link enforcement, the sole post-v17 legacy bridge, cycle-target
reconstruction, concurrent target/edit drift and expected-generation
rejection, read-only no-write, privacy rejection, byte/count bounds,
backup-only maintenance, Viewer v17
compatibility including valid/corrupt link batch validation, parser/help/
output, and unchanged unrelated projections. Test
facts and command inventories must remain derived from their existing owners
rather than copied into CI or multiple test modules.

### Atomic Activation And Acceptance Units

The approved static units preserve one activation target and one exact-target
acceptance boundary; their live state remains in the Task database:

1. **TG-M21.2 atomic vertical activation** owns schema v17, immutable storage,
   repository/evaluator services, Viewer source compatibility, the write leaf,
   bounded Task-show projection, verification-edit invalidation,
   completion/check gate and versioned cycle/Receipt basis validation,
   backup-only maintenance integration, and synchronized concise Skill/
   reference/formal contracts in one exact reviewed unit. `SCHEMA_VERSION=17`
   and setup migration activation must not land independently from those
   behaviors.
2. **TG-M21.3 integrated acceptance** runs the full migration/privacy/
   concurrency/package/release checks and fresh realistic pass, failure,
   timeout, stale-evidence, and resume/complete scenarios before closing M21.

Each unit is Tier 2. The schema-v17 design is supported only as the complete
atomic TG-M21.2 boundary, and TG-M21.3 must accept that exact target. No
partial unit activates the separate proportionality guardrail, project test
strategy, command runner, approved exception, Viewer Receipt UI, or Task
decomposition.

## Accepted But Inactive TG-M21.4 Verification Subject Design

TG-M21.4 changes no current module, schema, parser, package, Skill, Viewer, or
generated artifact. It defines the schema-v18 vertical transition that
TG-M22.2 must implement before any evidence bundle exists.

### Additive Storage And Legacy Preservation

`verification_receipts.py` continues to own Receipt gate semantics and public
projection. At schema v18 it will replace caller label normalization with one
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
durable/read validation before schema v18 activates. The implementation must
not raise the current shared `TEXT_LIMITS["verification"]` value in M22.2,
because that would expose the later M21.5 public behavior prematurely. M22.2
instead introduces two explicit owners:

```text
TASK_VERIFICATION_INPUT_LIMIT = 500
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
structural failure remains set-fatal. No eligible candidate returns the
existing restore failure without a canonical database. Stored overflow or
privacy failure maps to the owning sanitized stored-context inconsistency
result, not caller `invalid_argument` or `privacy_rejected`, with no partial
business, evidence, generation, or artifact write.

M21.5 changes only `TASK_VERIFICATION_INPUT_LIMIT` from 500 to 1,000 and the
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

M22.2 completion records its exact commit and complete package-tree identity
as the later compatibility baseline. M21.5 tests prove public
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

TG-M22.2 owns this subject activation together with the schema-v18 criterion
and target foundation plus the 1,000-character durable/read boundary while
public Task add/edit remains capped at 500. TG-M21.5 follows at the same
schema/package identity and changes only that public admission to 1,000.
TG-M22.3
then owns subject-only bundle/JSON projection, and TG-M22.4 owns integrated
legacy/current acceptance. No unit adds a public leaf, normal-loop call,
Runner, analyzer, or raw retained content.

## Accepted But Inactive TG-M22 Evidence Ledger Design

TG-M22.1 plus the TG-M21.4/TG-M21.4A corrections define three later atomic
implementation slices without activating them. TG-M22.2 will establish schema
v18 subject and capture foundations plus 1,000-character durable/read
capacity, TG-M21.5 will change only public verification-text ingress at schema
v18, and TG-M22.3 will establish schema v19 completion bundles and
generated JSON. Until those units
complete, the module list, schema-v17 runtime, state resolver, setup and
maintenance shapes, Viewer source range, package manifest, Skill call graph,
and public 21-leaf parser remain exactly current.

TG-M22.2 advances the unpublished package candidate to v0.12.0 with schema
v18. TG-M21.5 retains that identity; TG-M22.3 then advances schema to
v19. This development sequence makes no published-version, tag, Release,
remote-commit, or artifact-identity claim.

### Future Module Ownership

The activation design introduces three narrow package modules:

- `evidence_ledger.py` owns assurance/producer validation, authority-basis
  canonicalization, whole-field criteria, evidence references, immutable link
  rules, bundle assembly, omission codes, and canonical public allow-lists.
- `artifact_manifest.py` owns bounded shell-free Git tree/index observation,
  exact artifact entry normalization, deterministic rename pairing, and
  canonical manifest digests. It reuses the safe process runner and stable
  snapshot primitives from `git_snapshot.py` without routing complete
  manifests through the truncated Review Packet projection.
- `evidence_projection.py` owns coherent ledger capture, canonical bundle and
  index JSON, digest validation, index-last atomic publication, generation
  comparison, last-good preservation, and repair planning.

`storage.py` remains the only SQLite owner. `tasks.py` and `contracts.py`
invoke authority capture inside their existing savepoints;
`verification_receipts.py` derives subject-v1 bindings and it and `reviews.py`
create typed evidence references inside their own existing writes; the target
service creates one manifest and subject-capable binding atomically;
the completion workflow passes a fully prepared bundle basis into the existing
native-cycle savepoint. Feature modules never open raw SQLite connections.

`state_resolver.py` and `state_paths.py` will become the sole owners of the
fixed Evidence directory, index, bundle directory, and lock. `state_transition.py`
will recognize only those generated files in a bounded setup stage.
`maintenance.py` will add an Evidence-relevant flag and stage without
conflating its generation with Viewer event generation. `setup.py` will own
explicit repair. `cli.py` will add only the approved setup/doctor fields and
warnings in M22.3; it adds no command or evidence-export parser branch.

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
does not need to reinterpret old records, while repository writers allow only
the producers activated by their current schema/feature version. A producer
class is not a user, reviewer, process, model, executable, machine, signature,
or trust proof. Derived records preserve source IDs and classes; the validator
rejects any assurance upgrade.

The current mapping is implemented as the fixed source/state dispatch table in
the specification, not caller input. A complete Git manifest reference is
`machine_observed/taskgov_git/1`; its canonical digest is a structural seal,
not a separately classed claim. An opaque diff-fingerprint manifest is
`bound_attestation/trusted_caller/1`; an opaque external-revision manifest is
`external_reference/external_system/1`. M21 Receipt and review inputs are
`bound_attestation/trusted_caller/1`. Completion evidence dispatches by its
closed kind to Git-observed, external-reference, or caller-attested exactly as
specified. Migration-only absence is legacy unknown. M23 and M24 producer
branches remain unreachable until their own schema and service activations.

### Schema V18 Capture Foundation

Migration 18 is named `evidence_ledger_capture`. It adds:

```text
authority_snapshots
contract_criteria
authority_snapshot_criteria
artifact_manifests
artifact_manifest_entries
evidence_references
```

and the minimum current-pointer, target-binding, and TG-M21.4 subject columns
needed to connect them to Tasks, Receipts, and cycles. The subject columns are
the additive three-field set defined in the preceding section; no separate
subject table or caller-authored value exists. All record IDs use their
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
TG-M21.4A schema-aware 1,000-character stored limit. Public Task add/edit input
remains independently capped at 500 until M21.5; no capture object, trigger,
migration validator, or read projection may reuse that narrower cap.

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
is `<bundle-id>.json`; no caller path exists. Resolution rejects links,
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
warning. Any later eligible business mutation may retry one due Evidence
refresh without an LLM decision. Setup directly repairs missing/stale/corrupt
projection after migration/configuration and before Viewer publication;
read-only setup reports the planned write and does nothing. Doctor reads the
stored generation/outcome only.

M22.3 will update setup data with `evidence_status`, add
`evidence_projection_publish` to the ordered setup write vocabulary, add one
fixed Evidence maintenance object to doctor, and synchronize the corresponding
stable errors/warnings. These are additive changes to existing leaves, not a
new leaf. Current M22.1 documentation does not activate them.

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
row, caller label, target, result/coverage, caller-attested class, and v17
done-cycle rules but receive no reference or bundle. A native bundle accepts
only subject basis one and serializes exactly `basis_version`, `kind`,
`authority_snapshot_id`, and `verification_criterion_id`. The schema-v18
read-model union's `legacy_caller_label` key and the internal compatibility
value are both absent; result/coverage remain caller-attested.

### Future M23 And M24 Seams

The M23 analyzer may later read one exact bundle file/digest through a bounded
core-created packet and append a separate `llm_derived` analysis revision and
criterion link. It may not update a bundle, create canonical evidence, change a
Task or gate, or read SQLite directly. M22 activates no worker, outbox, remote
model, report narrative, retry policy, or analyzer producer.

The M24 Runner may later add a runner-observation table and a new tagged
verification-basis/bundle version. A Runner can classify only its directly
observed argv-plan execution facts as machine-observed; project test selection,
environment authenticity, external effects, and old caller evidence do not
inherit that class. Shadow evidence remains gate-ineligible. Later gate
activation must retain the M21 Receipt path as an explicit fallback and never
rewrite existing cycles, bundles, links, or digests.

### Implementation And Acceptance Units

1. **TG-M22.2** owns schema v18, authority/criterion/reference repositories,
   subject-basis Receipt/cycle fields, label-free CLI/Task-show transition, Git
   manifest capture, Task/Contract/target/Receipt/Finding/completion integration
   including capture-v0 rejection and native-cycle references, the 1,000-
   character stored/read boundary with 500-character public Task ingress,
   migration,
   self-host retarget/reverification/review, Viewer-v4 v18 compatibility,
   synchronized `AGENTS.md` retention, docs/Skill/package, split focused/full
   tests, exact diff, Verification Receipt, and two Tier 2 reviews. It creates
   no bundle or Evidence JSON.
2. **TG-M21.5** owns only the public Task add/edit verification-admission
   change from 500 to 1,000 after schema v18; stored readers and DDL remain
   unchanged, with no migration, subject redesign, leaf, or normal-loop call.
3. **TG-M22.3** owns schema v19, native bundle sealing, immutable criterion
   links and finding snapshots, projection state/resolver/staging/setup/
   maintenance, subject-based index-last JSON publication, Viewer-v4 v19
   compatibility, synchronized governing/Skill/package surfaces, full tests,
   exact diff, Verification Receipt, and two Tier 2 reviews.
4. **TG-M22.4** owns realistic legacy/current, subject, 1,000-character, and
   Evidence integrated acceptance plus only bounded repairs inside the accepted
   design. It does not activate M23, M24, remote inference, Viewer Evidence UI,
   another command, or another Skill-loop call.

Every unit is Tier 2 and lands as a coherent completion commit. Schema v18,
the capacity transition, and schema v19 are not reachable until their exact
owning code, migrations where applicable, Viewer source range, package
inventory, tests, and current formal contracts agree.

## Task Contracts, Checkpoints, Handoffs, And Effort

### Immutable Task Contract Revisions

Schema v8 gives Tasks a current revision pointer and adds append-only
`task_contract_revisions`. Revision 0 has no row and projects empty fields.
Each positive row stores normalized scope, acceptance, optional constraints,
stable authority reference, change reason, and timestamp. Repository reads
require the pointer to reference the latest same-project/same-task revision.
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
sidecar, database, event, backup, or Viewer.

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
migration backup, migration, maintenance configuration, and Viewer publication
plus effective backup interval/retention. It creates no directory, lock,
temporary, sidecar, SQLite recovery connection, or HTML.

Write setup revalidates scope immediately before each irreversible stage. A
completed stage is not rolled back because a later stage fails; the exact
ordered partial result makes rerun resume. Initialization, migration, recovery,
relocation, maintenance opt-in, and Viewer publication use services directly,
not removed CLI subprocesses.

When canonical DB is absent, setup scans only canonical managed backups:

- no managed name => fresh initialization;
- at least one valid same-project generation => mechanically newest
  `(published_at, generation_id)` recovery;
- managed names but no valid same-project generation => fail closed.

Invalid, foreign, linked, or unrelated files are preserved. Recovery holds the
artifact lock, revalidates the candidate, copies it through SQLite backup to a
sibling temporary DB, reconciles only supported backup metadata, validates
schema/identity/quick/FK/regular-file state, and publishes no-clobber. A
lexical rollback journal beside a missing canonical DB is rejected before
selection and publication and never opened/deleted. A concurrently appearing
canonical DB is never overwritten.

A supported old recovery migrates normally; current recovery proceeds to
Viewer. Fresh initialization repeats candidate/journal absence under the lock
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

Business services return internal `MutationOutcome(changed,
viewer_relevant)`. After commit and connection close, the coordinator runs:

1. Viewer refresh when relevant; then
2. one backup attempt when due.

They are independent same-process bounded work, never a thread, daemon, timer,
queue, scheduler, service, sleep, or retry loop. Each uses its own zero-wait
one-byte OS lock. Lock-file existence is not ownership and process termination
releases the lock. Read, error, replay, no-op, configuration-only setup, doctor,
Effort, and maintenance metadata writes do not invoke it.

Backup is due when no success exists, the last outcome is deferred/failed, or
the configured interval elapsed. Failed attempts do not advance success.
Eligible mutations are Task add/edit/complete/checkpoint, handoff
record/withdraw, and review target/receipt/finding add/resolve. Handoffs are
not Viewer-relevant because the snapshot excludes the outbox. Verification
Receipt add is backup-eligible and Viewer-ineligible because the snapshot has
no Receipt dataset or field.

Viewer-relevant mutations increment source generation through the
`task_events` trigger in the same transaction. Refresh compares source/rendered
generation, renders once and rechecks once, for at most two renders. Setup
initial/migration/repair uses its direct Viewer stage and does not re-enter the
coordinator.

Only fixed warnings are added after a successful primary command:

```text
viewer_refresh_deferred | viewer_refresh_failed
backup_deferred         | backup_failed
```

Messages state that the primary result is unchanged and include no path,
exception, hash, raw output, retry, stop, or model choice. Doctor observes the
latest outcome without starting work.

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

The repository selects all project Tasks in list order, at most 10 newest
events per Task by time/rowid, review evidence through the shared gate read
model, and bounded completion history. Snapshot v4 contains snapshot/source
schema versions, generated time, project ID/display only, counts, and explicit
Task/event/evidence/history allow-lists. It excludes repository/database paths,
tool events, handoffs, checkpoints, maintenance state, internal generation,
event-cycle links, environment, and raw review material.

Source schemas v5-v14 synthesize empty/incomplete completion history;
v15-v17 use stored cycles, reading completion histories in batches of at most
500 Task IDs. Source v17 validates version-1 cycle Receipt links within that
batch and discards the joined Receipt fields. Every snapshot reports its
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

## Completed TG-M20 Study Boundary

TG-M20 was a one-time, offline repository-development study against exact
product baseline `43c91d5987b0c35c66f834789aea782e98dcaff7`. Its frozen
observation authority was completed at
`a77afbe0140fef416cceeee529e9ff2c985a8e4d`; that authority never replaced
or modified the product baseline. The reviewed stratified aggregate,
denominators, exclusions, decision basis, limitations, and follow-up
inventories are preserved only in
[non-authoritative study history](history/v0.10.0/m20-operational-baseline.md).

The recorded outcomes are:

| Candidate | Outcome |
|---|---|
| TG-M21 Verification Receipts | `proceed_to_design` |
| Skill-only proportional-verification guardrail | `observe_more` |
| Bounded user-approved Task decomposition | `observe_more` |
| Bounded successor observation for the inconclusive candidates | `proceed_to_design` |

These outcomes are decision inputs, not product behavior. A positive result
authorizes only a separately approved and reviewed design proposal. It does
not activate a Skill instruction, public command, telemetry path, SQLite
schema, Viewer projection, test-strategy engine, runtime Task split, automatic
Task creation, or parent/child Task model.

The temporary root-only collector, reconstruction and fresh-trial tools, their
dedicated tests, the study protocol and episode-plan fixtures, the ignored
memo, and all ignored reduced corpora/locks/trial remnants are retired by
TG-M20.5. No current runtime, package, Skill, test lane, or active product
contract depends on those study-only surfaces.

The three tracked `m20-collection-receipt-v1` JSON files under
`fixtures/m20/` remain only as no-rerun tombstones. Each retains the unit,
authority and baseline revisions, protocol digest, corpus digest and size,
record/eligibility counts, terminal outcome, `artifact_status="retired"`,
and the exact retirement-anchor commit. M20.4 additionally retains the
canonical episode-plan digest. A receipt is provenance for the deleted study
input, not study evidence, current verification evidence, product state, or
authority to reconstruct, rerun, or rescore the corpus.

The completed TG-M20S Task-decomposition successor is summarized below. Any
other Verification Receipt design, Skill-only guardrail observation, or Task-
decomposition observation still requires new explicit user approval and a
separate bounded execution contract. Historical candidate inventories remain
non-authoritative and cannot add an attempt or change the recorded result.

## Completed TG-M20S Successor Observation Boundary

The retired study was bound to Task `tg_task_ddfbf721eced8c58`, Contract
revision 1, authority reference
`conversation_decision:2026-08-01:interrupt-successor-task-decomposition-observation`,
baseline `43c91d5987b0c35c66f834789aea782e98dcaff7`, and installable package
tree `529abf7ac4e4ed778b383c90b6ac5f2fedc71615`. TG-M20S.1 froze the
root-only one-shot harness and protocol; none of that machinery entered the
installable package.

TG-M20S.2 launched only `sp_user_expansion_alternate`. Both isolated arms
were eligible. Every bounded episode had independently acceptable, verifiable,
committable, and completable scope. Its Contract-revision delta sum was zero,
compared with one for the broad arm, so the pair qualified under the frozen
predicate. The inherited `E=1,Q=1,U=3` became `E=2,Q=2,U=2`; the first
positive rule selected `proceed_to_design` and prevented launch of the two
remaining pairs.

This is narrow evidence from one repository, scenario, pair, and model/tool
cohort. Supporting counts and footprint measures do not establish population
causality, authenticated reviewer independence, or a general automatic
self-splitting rule. The result itself permitted only a separately approved
Tier 2 design proposal and did not register or activate one.

All raw work, controls, candidate records, reduced corpus, journal, roots, and
temporary harness assets are retired. The retained receipt records only the
closed corpus identity, aggregate counts, decision, and exact retirement
anchor; it cannot reproduce or rescore the study. The complete aggregate
interpretation and limitations are preserved in
[non-authoritative study history](history/v0.10.0/m20s-task-decomposition.md).

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
- every v1-v17 migration, rollback, idempotency, required-object marker,
  realistic preservation fixture, quick check, and foreign keys;
- task validation, ordering, pause/block/current/next, done/reopen,
  completion evidence, every review tier/target/receipt/finding, Contract,
  checkpoint, handoff, and Effort route;
- Git option safety, canonical commit resolution, snapshot/index/tree binding,
  no mutation, and no lazy network;
- concurrent readers/writers, short writer ownership, stable busy/WAL errors,
  and injected rollback points;
- UUID identity, legacy identity, fixed/legacy resolver inventories,
  same-binding publication, recovery, relocation token/replay/expiry, staged
  no-clobber publication, cleanup resume, and preservation of unrelated files;
- backup publication/reconciliation/retention/recovery and every crash
  boundary;
- Viewer v4 sources 5-17, completion-history bounds, version-1 Receipt-link
  validation, 500-ID history batching,
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
