# Setup And State Operation Section Split Capture

> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves only the Setup, state-operation, identity, recovery,
> and maintenance sections before their document split. Words such as current,
> approved, or implemented describe the captured revision, not current authority.
> This history cannot fill an active-contract gap, satisfy a current gate, or
> authorize a removed behavior.

- Source commit: `a5fac6b0533c0f7cfc3c46cee76344ff440d2711`
- Source paths: `docs/specification.md` and `docs/design.md`; the exact
  section boundaries are identified with each captured block below.
- Capture unit: `TG-MOD.15`
- Active replacements:
  [Setup and state specification](../../setup-state-specification.md)
  and [Setup and state design](../../setup-state-design.md).
  [Repository authority](../../authority.md) routes the unchanged common owners.
  Use the public CLI for live Task state and evidence.

## Captured Section 1: Doctor Contract

Source path: `docs/specification.md`

Source range: `### Doctor Contract` through immediately before `### Effective Git-Ignore Preflight`.

````markdown
### Doctor Contract

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

````

## Captured Section 2: Effective Git-Ignore Preflight

Source path: `docs/specification.md`

Source range: `### Effective Git-Ignore Preflight` through immediately before `## Task State, Scope, Review, And Completion`.

````markdown
### Effective Git-Ignore Preflight

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

````

## Captured Section 3: Recovery Candidate Validity Contract

Source path: `docs/specification.md`

Source range: `## Recovery Candidate Validity Contract` through immediately before `## Stored Task Read And Privacy Contract`.

````markdown
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

````

## Captured Section 4: Stable Project Identity And Relocation

Source path: `docs/specification.md`

Source range: `## Stable Project Identity And Relocation` through immediately before `## Setup, Recovery, Evidence, Backup, And Viewer Maintenance`.

````markdown
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

````

## Captured Section 5: Setup, Recovery, Evidence, Backup, And Viewer Maintenance

Source path: `docs/specification.md`

Source range: `## Setup, Recovery, Evidence, Backup, And Viewer Maintenance` through immediately before `## Static Task Viewer`.

````markdown
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

````

## Captured Section 6: Fixed State Resolver

Source path: `docs/design.md`

Source range: `### Fixed State Resolver` through immediately before `### Journal And Connection Rules`.

````markdown
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

````

## Captured Section 7: Stable Project Identity, Binding, And Relocation

Source path: `docs/design.md`

Source range: `## Stable Project Identity, Binding, And Relocation` through immediately before `## Task State And Selection`.

````markdown
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

````

## Captured Section 8: Setup, Doctor, Backup, And Maintenance

Source path: `docs/design.md`

Source range: `## Setup, Doctor, Backup, And Maintenance` through immediately before `## Static Viewer`.

````markdown
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

````
