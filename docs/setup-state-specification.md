# Setup And State Operation Specification

This document owns Doctor and effective-ignore preflight, recovery candidate
validity, stable project identity and relocation, and setup/backup/maintenance
behavior delegated by the [product specification](specification.md).
Implementation structure belongs in the
[Setup and state operation design](setup-state-design.md).
Shared [CLI/output](specification.md#public-cli-and-output-contract),
[package and state layout](specification.md#package-runtime-and-generated-state),
[privacy and stable errors](specification.md#privacy-safety-and-stable-errors),
[SQLite operation](specification.md#sqlite-migration-and-concurrency),
and [current persistence](specification.md#current-schema-v22-persistence-contract)
remain with their existing owners.
[Stored Task validation](task-operation-specification.md#stored-task-read-and-privacy-contract),
[Evidence publication](evidence-specification.md#canonical-evidence-bundle-and-index-formats),
and [Viewer behavior](viewer-specification.md) are directly coupled contracts,
not additional setup authority. The [authority index](authority.md) routes each
responsibility.

<a id="doctor-contract"></a>

## Doctor Contract

`doctor` is the sole diagnostic. It is inherently read-only, emits
`command="doctor"`, and always includes
`data.suggested_action="continue"`. It never fixes, initializes, migrates,
backs up, renders, acquires a maintenance lock, runs project tests, or changes
state. It is not a setup or normal-loop prerequisite.

Doctor data contains only `suggested_action`, `setup_eligible`, and
`components`. Component keys are exactly `package`, `project_state`,
`task_summary`, `handoff_delivery`, and `maintenance`. Package inspection is
one independent bounded filesystem observation. When state is readable, all
project-backed components come from one lock-respecting read transaction; an
unavailable project-backed component is exactly `{"code":"unavailable"}`.
Doctor validates the complete project Task batch before returning Task-derived
counts. A stored Task fault makes `project_state.code="unreadable"`, every
other project-backed component `{"code":"unavailable"}`, and
`setup_eligible=false`, with the fixed `project_state_unreadable` error.

- `package` is the bounded manifest-integrity projection.
- `project_state` contains only `code`, `schema_version`, and
  `required_schema_version`.
- `task_summary` contains `code` and exact counts for `active`, `blocked`,
  `done`, `next_actionable`, `paused`, and `review_pending`.
- `handoff_delivery` contains `code`, `handoff_pending`, `adapter_enabled`,
  and `delivery_due`.
- `maintenance` contains `code`, `opted_in`, `backup`, `evidence`, and `viewer`.

The package projection contains exactly `package_name`, `package_version`,
`release_origin`, `manifest_version`, `status`, `changed_core_count`,
`changed_core_paths`, `changed_core_paths_truncated`, `unknown_reasons`, and
`suggested_action="continue"`. Status is `clean`, `modified`, or `unknown`.
It compares the physical package with the co-located strict v1 release
manifest: declared core paths are normalized relative POSIX paths with
`sha256:<lowercase-digest>`, and added files outside root `config/`,
`adapters/`, generated `state/`, and bytecode/cache are core modifications.
The manifest excludes itself. Absolute/traversal/duplicate/case-colliding/
excluded/malformed paths, identity/version mismatch, incomplete inspection, or
unsafe objects produce unknown without reading outside the package. Output is
at most 20 sorted relative changed paths and never includes absolute paths,
content, expected/actual hashes, link targets, or exceptions. `release_origin`
is a declaration, not a signature; replacing both manifest and core can evade
this local drift check.

The backup object has exactly `code`, `due`, `interval_minutes`,
`generations`, `last_success_at`, and `last_outcome`. The Viewer object has
exactly `code`, `due`, `source_generation`, `rendered_generation`,
`last_success_at`, and `last_outcome`. Each outcome contains only `code`
(`none`, `succeeded`, `deferred`, or `failed`) and `occurred_at`; not-yet-owned
values are null.
The Evidence object has exactly `code`, `due`, `source_generation`, `published_generation`, `last_success_at`, and `last_outcome`; doctor reports these stored facts only and never repairs or reads generated JSON.

Readable/current state is exit 0, `ok=true`, and setup-eligible when package,
runtime, install, ignore, identity, and state preconditions all pass. Missing
state and supported older schema are successful diagnostics with respectively
`setup_required` and `migration_required` warnings and remain setup-eligible.
Package `modified` or `unknown` is a successful warning but not setup-eligible.
Relocation-required state is a successful warning with
project-state `relocation_required`, `setup_eligible=true` when all other
preconditions pass, and no write.

Fatal rows use exit 2 and these component/error mappings:

| Condition | Project/package code | Error |
|---|---|---|
| unreadable/invalid state | `unreadable` | `project_state_unreadable` |
| foreign identity | `foreign` | `project_mismatch` |
| newer schema | `newer` | `schema_too_new` |
| SQLite busy/locked | `busy` | `database_busy` |
| WAL header/sidecar | `unsupported_journal` | `unsupported_journal_mode` |
| linked/competing/unsupported layout | `invalid_layout` | `unsupported_install_layout` |
| invalid/missing project | `invalid_project` | `invalid_project_root` |
| omitted repo at a package root | `invalid_project` | `project_scope_required` |
| unsupported Python | `unsupported_runtime` | `unsupported_python` |
| ignore not effective | `ignore_required` | `state_ignore_required` |
| invalid state ownership | `invalid_state_path` | `state_path_invalid` |

Doctor and setup share this first-applicable preflight precedence:
`unsupported_python`, `unsupported_install_layout`, `project_scope_required`,
`invalid_project_root`, `state_path_invalid`,
`package_core_modified|package_status_unknown`, `state_ignore_required`,
`unsupported_journal_mode`, `database_busy`, `project_state_unreadable`,
`project_mismatch`, `schema_too_new`, then
`migration_required|setup_required|ready`. Package drift is a doctor warning
but a setup error. A physical non-Git target skips ignore enforcement. Lower
precedence never replaces the process result, though the independent package
warning may accompany a later project error.

Fixed sanitized messages are:

| Code | Message |
|---|---|
| `unsupported_python` | `Python 3.12 or newer is required` |
| `unsupported_install_layout` | `stateful use requires one supported physical project-scoped package layout` |
| `project_scope_required` | `explicit --repo is required from the package directory` |
| `invalid_project_root` | `project root must be an existing directory` |
| `state_path_invalid` | `project state path is not valid for this package layout` |
| `package_core_modified` | `packaged core files differ from the release manifest` |
| `package_status_unknown` | `package integrity could not be verified` |
| `state_ignore_required` | `project-local state must be ignored before setup` |
| `unsupported_journal_mode` | `task database uses unsupported WAL journal mode` |
| `database_busy` | `task database is busy; run the command again later` |
| `project_state_unreadable` | `project state could not be read safely` |
| `project_mismatch` | `task database belongs to a different project` |
| `schema_too_new` | `task database schema is newer than this taskgov version` |
| `migration_required` | `task database requires setup migration` |
| `setup_required` | `project state is not set up` |

Maintenance codes are `not_opted_in` before setup and, once enabled,
`current`, `due`, `deferred`, or `failed` for current runtime state.
Readable maintenance never becomes an envelope warning/error. Deferred means
zero-wait lock contention; failed means bounded artifact failure; both remain
due.

<a id="effective-git-ignore-preflight"></a>

## Effective Git-Ignore Preflight

For setup, preview, and doctor only, inspect the governed target and parents to
the nearest existing/link-like `.git` marker. Inspection failure fails closed
as `state_ignore_required`. No marker means physical non-Git and no subprocess.
With a marker, invoke exactly one shell-free, sanitized, two-second-bounded
equivalent of:

```text
git check-ignore --quiet --no-index -- <canonical-state-directory/>
```

The one target-relative operand is
`.agents/skills/task-governance-tool/state/` or self-host
`task-governance-tool/state/`, with forward slashes and trailing slash.
Exit 0 alone proves effective ignore. Exit 1, timeout, launch failure, or other
result fails closed without output or write. Git decides parent rules,
gitfiles, submodules, and negation; taskgov does not parse `.gitignore`, test
trackedness, edit ignore files, cache, retry, or expose path/pattern/error
detail. Later setup structural revalidation does not repeat this subprocess.

<a id="recovery-candidate-validity-contract"></a>

## Recovery Candidate Validity Contract

Every recognized managed recovery candidate first passes physical-file, SQLite,
schema/history/object, quick/foreign-key, project identity, binding lineage,
filename/embedded metadata, generation repository, retention, and set-envelope
validation. Only after the whole set passes may exact stored Task verification
be classified against that candidate's source schema, with privacy checked
before capacity and without exposing or rewriting the value.

Each candidate's own immutable pre-publication repository snapshot is part of
that structural validation. Across the complete set, each generation ID maps
to exactly one complete metadata tuple in every filename, embedded row, and
maintenance pointer where it appears. For schema v11 and later, its pointer must equal
its latest embedded row, its own artifact is the one file-only generation in
the physical prefix visible at publication, and any bounded row-only history
is strictly older. Schema v10 has no generation rows and may point only to an
older mechanical generation: when a retained physical predecessor exists it
must equal that predecessor's complete metadata, and only the first retained
candidate may point to an older generation whose ID is absent from the complete
physical candidate set. Candidate schemas need not be monotonic after an older
fallback is recovered and backed up for migration.
Every present SQLite value whose recovery metadata contract is `INTEGER`,
including generation-row retention and maintenance backup policy/pointer
integers, is checked at the shared storage-reader boundary before conversion
or range validation. Only the Python value produced for SQLite storage class
`INTEGER` is accepted; `REAL`, `TEXT`, `BLOB`, Boolean-like non-SQLite input,
or another storage class is structural failure. It is set-fatal as
`project_state_unreadable`, including with a present primary or an older valid
candidate, and cannot reach private normalization or canonical publication.
A candidate-derived `-journal`, `-wal`, or `-shm` entry, including a
filesystem case alias, is set-fatal even when empty. The stored Task validator
examines all rows for malformed storage values before returning a
candidate-local result; among local results, privacy has precedence over
capacity.

| Observation | Classification and selection | Setup result |
|---|---|---|
| Structurally coherent, same-project candidate at the current recovery binding whose stored Task verification passes its source-schema rules | eligible; select the newest `(published_at, generation_id)` | continue recovery |
| The same safe candidate whose stored Task verification alone fails privacy or source-schema capacity | candidate-local rejection; retain and observe the file but exclude it from selection | recover the newest older eligible candidate, or `setup_restore_failed` when none remains |
| Corrupt/unreadable SQLite, unsafe file or sidecar, unsupported/newer/incomplete schema, failed quick/FK check, foreign identity, binding/lineage divergence, metadata/repository/retention/structure inconsistency, duplicate or overflow | set-fatal; never skip it to reach another candidate | the existing specific journal/busy/newer result where applicable, otherwise `project_state_unreadable` |
| Any candidate addition, removal, replacement, stamp/order/content classification change, selected-candidate change, or canonical database/journal appearance after planning | post-plan recovery/restore/publication drift; fail closed and never reselect under the established plan | `setup_restore_failed` before restore or canonical publication |

The mechanically newest candidate remains the structural head for lineage and
generation-envelope validation even when it is candidate-local rejected.
Selection additionally requires an exact identity scheme, binding generation,
canonical path hash, and complete binding lineage match with that head; a
structurally valid older lineage-prefix artifact may remain in the inventory
but is never a fallback across a binding generation.
Initial discovery and lock-held revalidation retain every eligible and rejected
candidate plus its physical identity; their complete inventory,
classification, and selected candidate must match. The established complete
inventory remains bound through source copy, repository normalization, and the
last validation immediately before canonical publication; a later rescan may
only confirm it, never replace or narrow it. Recovery of an older candidate
keeps every structurally coherent generation in the normal row/file/pointer
envelope; rejection changes selection only and never excuses a missing or
mismatched generation relation. Classification and recovery do not rewrite a
rejected artifact, although later already-authorized retention may prune
managed generations normally. A present fixed primary remains authoritative:
ordinary consumers do not fall back to a backup, while setup's deep inspection
still scans every candidate and applies set-fatal hardening. A local
privacy/capacity rejection does not invalidate an authoritative primary, but a
malformed stored Task value does.

The private SQLite copy must still match the selected candidate's project,
binding, embedded generation rows, maintenance pointer, source schema, and Task
classification before repository normalization. Fixed recovery re-runs the
same deep resolver at restore entry and immediately before no-replace canonical
publication, and revalidates the normalized private copy's required schema
objects and exact normalized repository state. Legacy backup-only recovery
applies the same observation to the source and copied stage and re-runs the
full source resolution immediately before publishing the private stage.
Any shallow inventory refresh precedes, and never follows, the final deep
source-set comparison.

`restore_managed_backup` returns only after the no-replace canonical link has
succeeded. That return is the durable setup boundary: setup records
`database_restore` immediately, before comparing the returned source schema or
performing its post-link setup-state inspection. A later schema mismatch,
inspection failure, or other restore-stage failure remains
`setup_restore_failed` and returns `database_restore` in the exact durable
`completed_writes` prefix. A failure before the link/return reports no restore
write. Retry recomputes from the canonical state and the restore temporary is
cleaned in either case; no later failure authorizes reselection or another
canonical publication.

The current classifier admits 500 for schema v17 and 1,000 for schema v18-v22,
rejecting the next character in each source schema. It does not generalize
local rejection to another field
or to structural, identity, lineage, metadata, or TOCTOU failure.

<a id="stable-project-identity-and-relocation"></a>

## Stable Project Identity And Relocation

### Identity, Binding, Resolver, And Source Selection

Schema v14 has exactly two schemes:

- migrated v1-v13 databases retain their byte-identical legacy project ID as
  `legacy_path_v1`;
- fresh production setup creates one random UUIDv4 ID
  `tg_project_<32-lowercase-hex>` as `uuid_v1`.

No Task, event, Contract, handoff, review, completion, maintenance, or Viewer
record ID is rewritten. Missing state has top-level null project ID until
write setup creates and persists the UUID once.

Binding is separate: positive generation, 64-lowercase-hex canonical-path
SHA-256, and bounded display name. `canonical_path_v1` resolves without
requiring existence, platform-normalizes absolute spelling, UTF-8 encodes, and
hashes. Display uses the root basename, replaces controls/line separators with
U+FFFD, falls back to `project`, and is at most 200 code points. Neither is
identity.

`project_meta` plus append-only binding history records ID, generation,
old/new hashes, display, UTC time, fixed reason (`legacy_migration`,
`fresh_setup`, `confirmed_relocation`), and optional SHA-256 of the accepted
token. Generation 1 has no prior hash/token digest. Raw paths and raw tokens
are never stored.

One shared resolver owns every production database/backup/Evidence/Viewer/lock target.
It derives only current hash/display from repo, then reads stored identity and
binding. Every business writer revalidates ID/hash/generation under its short
lock. An authoritative fixed primary is validated without gating ordinary
business reads on backups or Viewer. Setup alone uses deep artifact validation.

With no fixed primary, the resolver inspects only direct physical children of
`state/projects`, at most 64, without traversing unknown content. Unknown
direct children, unsafe canonical paths, links/reparse points, multiple
candidates/identities, newer/corrupt/foreign state, or validation ambiguity
fail closed and never fall through to fresh initialization.

Source precedence is:

1. existing fixed primary, even when invalid (never replaced);
2. valid fixed managed-backup recovery with coherent identity/binding lineage;
3. exactly one valid legacy physical candidate;
4. fresh initialization only when no durable/candidate-shaped residue exists.

A legacy candidate is one physical directory named exactly by its stored legacy
project ID, with a regular primary or missing primary plus valid managed
backup, schema v1-v13 or the bounded v14 transition state, contiguous history,
one project row, quick/foreign-key success, rollback-journal compatibility,
and safe recognized artifacts. Unrelated entries inside that sole directory
are not traversed or copied and remain after migration.

Recognized legacy crash temporaries are at most one physical bounded file per
class:

```text
database directory  .taskgov-restore-[a-z0-9_]{8}.tmp
backups directory   .taskgov-backup-[a-z0-9_]{8}.tmp
Viewer directory    .task-viewer-[a-z0-9_]{8}.tmp
```

Each is no larger than source database size plus 16,777,216 bytes. Multiple
matches, wrong grammar, or oversized files are unrelated and preserved. Only
canonical backup/Viewer locks are recognized for retirement; they are not
copied as durable state.

Managed generations are normally at most 20 plus one in-flight identity:
at most 21 artifacts, rows, and union identities. Schema v11+ permits only the
existing one file-only or one missing-file-row crash relation (never both with
a present primary), and the missing-primary selected newest file plus zero
through 20 older missing rows accepted by recovery normalization. v10 uses its
pointer rules; v1-v9 use newest-artifact retention. Any second inconsistency,
overflow, divergent lineage, or reconciler rejection fails closed. Discovery
does not repair the legacy source; only a private copy is reconciled.

Same-binding legacy state migrates without a token. A primary-backed differing
binding is a relocation candidate. Moved backup-only legacy/fixed recovery is
`project_state_unreadable`; relocation is never inferred from backup material.
After a confirmed fixed rebind, old-binding backups remain retained but cannot
recover a missing primary until a new-binding backup succeeds.

Before setup, normal DB-backed leaves report `db_not_initialized` when no
source exists, `migration_required` for one same-binding legacy source,
`project_relocation_required` for a primary-backed moved legacy source, and
the existing unreadable/mismatch/newer/journal/busy result for invalid source
state. They perform no publication, recovery, cleanup, initialization, or
migration.

### Relocation Preview And Token

A path mismatch proves neither move, copy, nor fork. Normal commands return
`project_relocation_required` without write. Doctor returns a successful
continuation warning and setup eligibility. Write setup without token also
fails. Only `setup --read-only` returns successful
`status="relocation_preview"`, exact future plan, and opaque token. Combining
token with read-only is parser-level `invalid_option_combination`.

The Skill presents the plan, stops for explicit current approval, then may pass
that exact token. Preview is not approval; expired/stale token requires a new
preview and approval. Direct user invocation with a token is explicit intent.

Token format is:

```text
tgr1.<unpadded-base64url-canonical-json>.<64-lowercase-hex-checksum>
```

Checksum is SHA-256 of ASCII `tgr1.<payload>`. Canonical duplicate-free UTF-8
JSON has exactly sorted keys `binding_generation`, `expires_at`,
`identity_scheme`, `issued_at`, `new_path_hash`, `old_path_hash`,
`project_id`, `source_layout`, `source_schema_version`, and `v`. Version is 1;
source layout is `fixed_current_v1` or `legacy_projects_v1`; hashes are 64
lowercase hex; generation positive; schema supported. Serialization uses
sorted keys, `(",", ":")`, unpadded base64url. Expiry is exactly 900 seconds
after issue, acceptance is `issued_at <= now < expires_at`, total token at most
2,048 ASCII bytes, and checksum comparison constant-time.

The token binds stored ID/scheme/generation/hash, proposed hash, layout, source
schema, and time, not business content. Confirmation revalidates all fields,
package/install/ignore, source inventory/integrity, destination, and current
root under locks.

After common preflight, precedence is invalid structure/checksum, already-used
digest, expired, stale context, then not-required. Fixed codes/messages are:

| Code | Message |
|---|---|
| `project_relocation_required` | `project state is bound to a different project location; run setup --read-only` |
| `relocation_token_invalid` | `relocation confirmation is invalid` |
| `relocation_token_expired` | `relocation confirmation has expired; run setup --read-only again` |
| `relocation_token_stale` | `project relocation state changed; run setup --read-only again` |
| `relocation_token_used` | `relocation confirmation has already been used` |
| `relocation_not_required` | `project relocation is not required` |

Service errors exit 2; rejected token/hash/path/count/name/exception is never
echoed. Successful replay always returns used, even after expiry.

### Setup Relocation Output And Durable Publication

Setup always includes `data.relocation` with exactly `required`,
`source_layout`, `identity_scheme`, `binding_generation`,
`confirmation_token`, and `expires_at`. Token/expiry are non-null only on a
successful relocation preview. Fresh preview has null top-level ID and null
identity/generation/token/expiry.

Relocation failure has status null, required schema 22, empty warnings, one
error, null token/expiry, and no rejected value. A no-token mismatch preserves
the read-only future `planned_writes`; invalid/expired/stale/used/not-required
token rows have empty write arrays and mechanically observed bounded context.
Earlier common-preflight errors retain precedence.

The complete setup write vocabulary is `database_restore`,
`database_initialize`, `migration_backup`, `database_migrate`,
`maintenance_configure`, `legacy_state_publish`, `project_binding_update`,
`evidence_projection_publish`, `viewer_publish`, and `legacy_state_cleanup`. Durable order is:

1. one source prefix: empty, restore, legacy publish, restore plus legacy
   publish, or initialize;
2. migration backup then migration;
3. maintenance configuration;
4. binding update only for confirmed mismatch;
5. Evidence projection publication;
6. Viewer publication;
7. legacy cleanup only after fixed database, binding, maintenance, Evidence, and Viewer.

Legacy work occurs in one private contained stage. Pre-publication staging is
not reported completed. Atomic no-clobber publication makes its staged prefix
durable together. Binding CAS appends generation and increments Viewer source
generation in one short transaction; overflow/missing Viewer state/mismatch
rolls back without consuming the token.

Private stage names are:

```text
state/.current-stage-<32-lowercase-hex>
state/.current-stage-<same-32-lowercase-hex>.owner
```

The exclusively and durably created physical owner precedes the directory. It
is canonical ASCII JSON at most 2,048 bytes with exactly `v=1`, matching
`stage_id`, validated `project_id`, and `inventory_fingerprint`, sorted keys,
ASCII escaping, compact separators, and no path. The stage allows only physical
`backups`/`viewer` directories and at most 32 regular files from the fixed
database/journal, up to 21 managed backups, canonical locks, Viewer HTML, and
one recognized temporary per class; each file is at most source database plus
16,777,216 bytes.

Write setup may remove only one fully owner-validated residue, enumerating
explicit allowed files then proven-empty directories. Read-only setup never
removes it. A stage without owner, invalid/mismatched owner, multiple pairs,
unsafe/unknown/oversized content fails `setup_incomplete` with no deletion.

Before publication, setup stores canonical cleanup inventory JSON:

```json
{"entries":[{"kind":"file","name":"relative/posix/name","sha256":"64hex","size":0}],"v":1}
```

It has 1-32 unique UTF-8-byte-sorted allowed entries, compact sorted-key ASCII
JSON at most 16,384 bytes, plus exact SHA-256. It lists only recognized
present source artifacts. Cleanup derives a fixed retirement directory from
the full SHA-256 of project ID, moves each inventoried file without replacement
after kind/size/hash validation, then deletes verified retirement entries.
Retry continues only the persisted subset. Both source/destination present,
changed content, unrecorded retirement content, or collision stops; unrelated
legacy files remain. After all recorded entries are absent, setup clears
pending inventory metadata atomically. Normal business commands remain usable
while valid fixed cleanup is pending.

Preview creates no directory, lock, sidecar, temporary, backup, Evidence, Viewer, Git, or
target change. Actual publication holds one fail-fast package transition lock
before the backup lock, uses SQLite backup API without a source writer during
copy, and releases writers before Viewer/cleanup. Failures before fixed
publication remove only proven owned staging; failures after publication keep
fixed state authoritative and legacy state intact for resumable cleanup.

<a id="setup-recovery-evidence-backup-and-viewer-maintenance"></a>

## Setup, Recovery, Evidence, Backup, And Viewer Maintenance

### Setup Contract

`setup` is explicit, noninteractive, idempotent, and the sole initializer,
migrator, one-way maintenance opt-in, relocation flow, fixed/legacy recovery,
and canonical Evidence/Viewer repair action. Preflight validates runtime, one physical
layout, project, state ownership, package integrity, Git ignore when applicable,
journal, schema, identity, binding, and artifacts.

When canonical state is absent, setup checks only the canonical managed backup
directory and applies the recovery-candidate validity matrix. It chooses the newest
eligible same-project, current-binding generation by publication time then ID;
only a Task-verification capacity/privacy rejection may expose an older
eligible generation. Existing unreadable canonical state is never overwritten.
If only locally rejected candidates remain, setup fails `setup_restore_failed`
rather than initializing empty.
A whole-set structural, identity, binding, lineage, metadata, repository,
retention, sidecar, or set-envelope fault never exposes an older candidate and
retains its specific resolver result where applicable, otherwise
`project_state_unreadable`. After an eligible candidate and immutable plan are
established, later drift or restore/publication failure remains the separate
`setup_restore_failed` boundary.

Recovery copies the selected artifact via SQLite backup API into a fresh
sibling temporary database, normalizes schema-appropriate generation/pointer
metadata, validates and flushes it, then atomically no-clobber publishes only
while canonical state and rollback journal remain absent. A lexical orphan
canonical `-journal`, changed candidate, or lock contention fails closed.
Older recovered schema then receives a new migration backup and migration.
Fresh initialization uses the same artifact lock and rechecks absence of state,
journal, and newly appeared recovery candidates.

Schema v10 maintenance state has immutable non-null `enabled_at` once
configured, backup interval/generation policy, latest managed success/outcome,
and internal `applied_backup_generations` retention. Fresh/migrated setup
defaults are 30 minutes and 3 generations. On configured state, omitted
options preserve locked stored
values; explicit valid options change only supplied fields; equal values are a
no-op. Invalid range is `invalid_backup_policy` before write. Configuration
does not change applied retention or itself trigger backup/Viewer; lower
retention applies only after the next successful backup publication.

Setup output data is exactly `status`, `planned_writes`, `completed_writes`,
`schema_from`, `schema_to`, `maintenance_enabled`,
`backup_interval_minutes`, `backup_generations`, `evidence_status`,
`viewer_status`, and `relocation`. `schema_to` is always 22. `schema_from` is safely observed source
schema, selected recovery schema, or null. Policy values are effective
requested/stored values, not persistence claims. `maintenance_enabled`,
Evidence status, and Viewer status describe durable post-command state.

Evidence and Viewer status are each `not_present`, `current`, `published`, or `repair_required`.
Successful preview is `setup_preview`; completed writes use `setup_complete`;
current/equal state is `already_setup`; relocation preview is
`relocation_preview`. Preview reports the full ordered plan and empty completed
array. Failures report the durable ordered completed prefix. Common preflight
and invalid policy failures have status null, empty arrays, null observed
scalars except required schema; later failures retain safe/effective values.

Core setup outcomes are:

| Stage/result | Stable error |
|---|---|
| invalid policy | `invalid_backup_policy` |
| structurally coherent recovery set with every current-binding candidate locally rejected only for Task-verification privacy/capacity | `setup_restore_failed` |
| post-plan recovery selection/copy/normalization/publication failure | `setup_restore_failed` |
| recovery-set structural/set-fatal defect | the existing specific resolver error where applicable, otherwise `project_state_unreadable` |
| migration backup failure | `setup_backup_failed` |
| initialization failure | `setup_initialization_failed` |
| migration failure after backup | `setup_migration_failed` |
| configuration, Evidence, Viewer, cleanup, or other later partial failure | `setup_incomplete` |

The fixed messages for invalid policy, restore, backup, initialization,
migration, and later partial failure are respectively
`backup policy is outside the supported range`,
`managed backup could not be restored`,
`setup backup could not be completed`,
`project state could not be initialized`,
`project state could not be migrated`, and
`setup completed only partially; rerun setup`. A resolver-originated recovery
failure retains that resolver error's stable message, including
`project state could not be read safely` for `project_state_unreadable`.
Setup success has empty warnings and errors; failure has empty warnings and
exactly one error. Rerun recomputes from durable state; a prior migration
backup never substitutes for the new attempt's required backup.

The shared backup primitive uses SQLite backup API, validates project identity,
schema/history, regular physical paths, `quick_check`, and foreign keys,
closes/flushed temporary state, and atomically publishes. Setup holds its
zero-wait artifact lock through backup publication/reconciliation and the
corresponding migration commit, but never a SQLite writer while copying.
Preview creates neither lock nor artifact.

### Same-Process Maintenance

Maintenance is permanently enabled by successful setup; there is no disable
surface. Every successful state-changed business mutation commits and closes
SQLite before bounded same-process maintenance. Any such mutation may retry a
due Evidence projection; only completion-cycle insertion advances its source
generation. Viewer refresh runs second only for Viewer-relevant mutations, and
due backup runs third. A changed handoff advances neither projection but may
retry already-due Evidence before backup. Read-only, failed, replayed, no-op,
setup configuration, and maintenance-internal operations trigger nothing.
The Runner path retains exactly the one existing target-set maintenance opportunity
only for a successful or fallback command. A post-T1 Runner error invokes no
maintenance; T1 has already advanced the ordinary due state, so the next
normal maintenance opportunity or explicit setup repair catches up. Internal
Runner intent, cleanup, observation, Reference, and link writes do not add
another coordinator call or advance Evidence or Viewer generation.

Taskgov starts no detached process, child process, thread, timer, watcher,
service, queue, daemon, scheduler, or network operation. Each artifact uses a
canonical regular one-byte OS advisory lock with zero wait; process termination
releases ownership, so leftover regular lock files are harmless and never
deleted by age. Unsafe lock path, contention, or failure preserves primary
result and last-good artifact, leaves due state, and emits at most the fixed
continuation warning:

| Code | Message |
|---|---|
| `evidence_projection_deferred` | `Evidence projection refresh was deferred; task result is unchanged` |
| `evidence_projection_failed` | `Evidence projection refresh did not complete; task result is unchanged` |
| `viewer_refresh_deferred` | `Viewer refresh was deferred; task result is unchanged` |
| `viewer_refresh_failed` | `Viewer refresh did not complete; task result is unchanged` |
| `backup_deferred` | `managed backup was deferred; task result is unchanged` |
| `backup_failed` | `managed backup did not complete; task result is unchanged` |

Backup is due on the first eligible mutation with no managed success, then
after the configured interval. At most one attempt occurs per eligible
mutation. Failure remains due. Successful atomic publication records its
immutable generation ID, publication time, and 1-20
`publication_retention`, then prunes recognized older generations to the
applied value. Unknown/linked/foreign artifacts are never imported or deleted.

Every v11+ backup attempt first reconciles bounded crash residue under the same
lock: import one valid file missing a row; remove a row whose file is invalid/
missing without deleting an untrusted path; update latest/applied retention;
and finish file-before-row pruning oldest first. It will not publish while
inconsistency remains. Publish then insert-row/update-pointer in one short
transaction, followed by pruning. Recovery handles process termination after
file publish, row commit, or between prune file/row. Residue cannot exceed the
valid retained set plus one in-flight generation.

For v10, a successfully published migration copy updates latest identity/time/
outcome/applied retention before migration and is reconciled on retry. For
v1-v9, each successful pre-migration copy precedes pruning under its immutable
stage retention; a successful migration creates v10 state pointing to it.
Migration v11 discovers all canonical valid retained same-project artifacts,
seeds one row each, includes the current copy, and prunes to that copy's
applied retention.

Viewer maintenance compares source/render generation, renders once and at most
one follow-up per mutation, prevents older-over-newer publication, and leaves
remaining churn due. Setup rerun is the only explicit force/repair.

The performance fixture is 12 Tasks/191 events and 500 Tasks/5,000 events,
payloads 80/512/256 UTF-8 bytes, with eight note writes at injected minutes
0, 1, 5, 29, 30, 31, 59, and 60. Due backups are at 0, 30, 60; Viewer is
eligible on all eight. Backup-only and Viewer-only run overhead versus disabled
is at most 10 seconds, combined Viewer-plus-backup overhead is at most 12
seconds, and each command-position median is below 5 seconds on Windows CI.
Attempt, render, call, byte, and zero-wait limits are hard; timing cannot waive
them.

Windows timing qualification uses one complete non-qualifying warm-up followed
by six measured rounds from fresh copies of the same fixed fixture. The
three-mode qualifier uses every mode permutation once; the two-mode qualifier
uses both orders equally. Every overhead remains paired with the disabled
total from that same round. The mode-specific total budget applies to the
median of the six paired enabled-minus-disabled totals: 10 seconds for
backup-only and Viewer-only, and 12 seconds for combined Viewer-plus-backup.
The strict 5-second budget applies to the median of the six
observations at each mode and write position and requires that median to remain
below 5 seconds; it is not averaged across commands. Diagnostics are bounded to
the fixture and mode, the six paired
overheads, eight command medians, and the maximum raw measured observation.
Functional, count, byte, attempt, render, and call assertions remain separate
hard failures and are never decided by the timing statistic.

Repository pull-request and `main`-push CI validate the complete discovered
inventory and base-lane ownership, then defer exactly the two closed wall-clock
qualification test identities. They continue to execute the separate fixed-
fixture functional and capacity assertions. The timing tests execute and block
only in the explicit manual `workflow_dispatch` release-candidate route, whose
Python 3.12 and 3.14 `all` jobs each retain the unchanged standard-discovery
suite. This is event-aware test selection, not a skip, retry, threshold change,
or fourth lane.
