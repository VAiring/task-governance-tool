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
and [current persistence](database-specification.md#current-schema-v22-persistence-contract)
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
backs up, renders, acquires artifact-publication ownership, runs project tests,
or changes state. It may probe an already existing backup-runtime lock without
creating or modifying it, releasing an acquired probe immediately. It is not
a setup or normal-loop prerequisite.

Doctor data contains only `suggested_action`, `setup_eligible`, and
`components`. Component keys are exactly `package`, `project_state`,
`task_summary`, `handoff_delivery`, and `maintenance`. Package inspection is
one independent bounded filesystem observation. When state is readable, all
project-backed database values come from one lock-respecting read transaction;
the backup-runtime observation has its own explicit observation time and is
not claimed to be atomic with that database snapshot. An
unavailable project-backed component is exactly `{"code":"unavailable"}`.
Doctor validates the complete project Task batch before returning Task-derived
counts. Within one current-schema read, Setup and Doctor may share the exact
validator's complete Task batch and completed row checks privately. All global
relationships and privacy checks remain required; reuse never spans a snapshot,
connection or Setup stage and never admits caller-supplied validation claims.
Standalone entries retain complete validation. A stored Task fault makes `project_state.code="unreadable"`, every
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
`generations`, `last_success_at`, `last_outcome`, and `runtime`. The Viewer object has
exactly `code`, `due`, `source_generation`, `rendered_generation`,
`last_success_at`, and `last_outcome`. Each outcome contains only `code`
(`none`, `succeeded`, `deferred`, or `failed`) and `occurred_at`; not-yet-owned
values are null.
Backup `runtime` contains exactly `state`, `observed_at`, `attempt_id`,
`captured_at`, `published_at`, and `generation_id`. Its state is `idle`,
`starting`, `running`, `interrupted`, `succeeded`, `failed`, `deferred`, or
`unknown`. The attempt/generation identities and times are null when not
known. Capture time describes when the latest attempt's source snapshot was
established, not the time that every later Task mutation became protected.
Publication time is reported only for confirmed publication and metadata
success. An earlier Setup backup can have a known core success without a
runtime capture time. Runtime failure does not erase that core success. These
six fields describe the worker, not a published opportunity or service
availability. Publishing a request never changes them to started or completed.
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
| project root cannot be inspected or normalized | `project_uninspectable` | `project_root_uninspectable` |
| omitted repo at a package root | `invalid_project` | `project_scope_required` |
| unsupported Python | `unsupported_runtime` | `unsupported_python` |
| ignore not effective | `ignore_required` | `state_ignore_required` |
| invalid state ownership | `invalid_state_path` | `state_path_invalid` |

Doctor and setup share this first-applicable preflight precedence:
`unsupported_python`, `unsupported_install_layout`, `project_scope_required`,
`invalid_project_root|project_root_uninspectable`, `state_path_invalid`,
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
| `project_root_uninspectable` | `project root could not be inspected safely; check access permissions and execution context` |
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

Project-root inspection distinguishes a missing path, a non-directory component,
or an invalid path value (`invalid_project_root`) from an inability to inspect
or normalize it (`project_root_uninspectable`). Windows `ERROR_INVALID_NAME`
(123) is positive evidence of an invalid path value; a generic `EINVAL` alone
is not. `project_root_uninspectable` includes access
denial and other OS/normalization failures; it does not establish absence or
claim that permissions are the only possible cause. This shared structural
preflight applies to ordinary commands as well as setup and doctor. Both
failures return exit 2, null project identity and no project-state read or
write. Doctor reports unavailable Task/handoff/maintenance components and
`setup_eligible=false`. Existing physical-install/link rejection and higher
preflight precedence remain in force. Neither result authorizes automatic
elevation, ACL changes, setup, or an extra doctor call on a successful path.
Messages never include an OS exception, path, or environment value.

Strict path observations prefer the platform's standard resolver. On Windows
only, its access-denied error (5) may use one bounded, read-only native
alternative: the normalized NT target must belong to the observed direct
volume root of the input drive, its reconstructed ordinary DOS path must
reopen to the same normalized target, and that drive root must remain the same
on a second observation. The result must also agree with the existing
`canonical_path_v1` nonstrict spelling. An uncertain mapping, unsupported
namespace, unsafe suffix, native failure, or mismatch preserves the original
fixed failure classification. A lexical spelling alone never establishes
success. Standard-success paths are not subject to these fallback restrictions.
This internal observation is shared by root validation and strict state
containment; it neither relaxes metadata/directory/ancestor-link/reparse checks
nor replaces the Packet's public CLI live validation. It adds no CLI retry,
argument, doctor step, permission change, or stored native diagnostic data.

Maintenance codes are `not_opted_in` before setup and, once enabled,
`current`, `due`, `deferred`, or `failed` for Evidence and Viewer runtime state.
Backup uses the same `not_opted_in`, `current`, `due`, `deferred`, and `failed`
codes: disabled policy is `not_opted_in`; otherwise an observed latest runtime
`failed` or `deferred` takes precedence over the policy's `due` or `current`.
The persisted `last_outcome` remains a separate historical fact. `runtime`
describes the latest asynchronous attempt and current ownership observation. `due` alone
does not establish a scheduled or running worker. A saved running phase with
confirmed absent ownership is `interrupted`; unsafe or inconclusive ownership
remains `unknown`. `last_success_at` and `last_outcome` remain persisted backup
facts, not evidence that the latest Task completion was captured.
Readable maintenance never becomes an envelope warning/error. Evidence/Viewer
deferred means zero-wait lock contention; failed means bounded artifact failure;
both remain due. Backup failed/deferred attempts remain eligible for recovery
on the next changed mutation, independently of the interval's `due` value.

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

The one target-relative operand is `.taskgov/` for both an ordinary package
and the self-host exception, with forward slashes and trailing slash.
Exit 0 alone proves effective ignore. Exit 1, timeout, launch failure, or other
result fails closed without output or write. Git decides parent rules,
gitfiles, submodules, and negation; taskgov does not parse `.gitignore`, test
trackedness, edit ignore files, cache, retry, or expose path/pattern/error
detail. Later setup structural revalidation does not repeat this subprocess.

<a id="project-state-separation"></a>

## Project State Separation

Generated state has one owner at `<governed-project>/.taskgov/`; the physical
package and supported `config/` files remain in place. Changing this storage
location alone preserves project identity, binding generation/history, schema,
Task and evidence IDs, maintenance policy and validated artifacts. It is not
project relocation. Actual binding changes still require the existing explicit
confirmation flow. Historical evidence remains historical.

Only explicit `setup` initializes or changes the layout. `setup --read-only`
previews it without directories, identities, locks or temporaries. Ordinary
commands and `doctor` never copy, fence, repair or activate state. Before the
first cutover, stop Taskgov writers and enabled Runner processes, update the
one physical package, then run setup in that offline maintenance window. A
new transition lock cannot establish that every old executable has stopped.
Taskgov does not kill processes, relax host protection or claim online cutover.

The separately opted-in [development diagnostics](specification.md#common-debug-logger)
directory is a physical non-state sibling under `.taskgov/`. Its presence does
not require setup or affect transition/recovery selection. State separation
preserves it without traversing, copying or deleting its diagnostic contents;
it supplies no evidence for state admission or repair.

Before upgrading, finish any incomplete predecessor-layout migration with the
compatible installed pre-separation version's `setup`. This prerequisite applies
only to validated `legacy_cleanup_pending` state or recognized owned
`.current-stage-*` residue, including an owner-only interrupted stage. New
separation preview and write setup preserve these inputs and return
`setup_incomplete`; they neither execute the old deletion intent nor remove the
owned stage. A healthy old layout needs no extra setup. Malformed or unowned
residue retains its existing refusal, not permission for cleanup. This is
separate from a new separation's private/fenced retry below: never run an older
runtime against an activated new layout or use it to repair new private data.

Selection is deterministic:

- No state or candidate-shaped residue at either location permits fresh setup.
- Valid package-local state requires migration, with the existing source
  identity/schema/binding and deep artifact checks. Invalid present source,
  ambiguous legacy state and backup-set faults retain their existing failures.
- A private or fenced transition is setup-only. Normal calls cannot use its
  candidate, including after directory publication but before activation.
- Activated new state requires its matching old-path retirement marker.
  A missing marker is a setup repair condition; an unrelated present old
  primary or mismatched marker is a conflict, never overwritten as repair.
  Repair validates old-location material against the recorded transition,
  both in preview and under the existing locks before publication. Only
  unchanged recognized retirement material is allowed; another legacy
  candidate or unexplained migration residue is preserved and stops repair.
  A formerly hidden old candidate is not authorized by fixed-source precedence.
- Two usable databases, unexplained residue or mismatched identities stop
  without merging, selecting a timestamp, deleting material or initializing.
- After activation, a missing primary uses only new-root managed recovery.
  An invalid present primary remains authoritative and blocking. Retained old
  material is never a fallback or another active database.

Setup retains one complete inventoried source set outside managed-backup
pruning and prepares a private candidate below `.taskgov/.state-separation/`.
SQLite files are snapshotted with the backup API, not copied while open.
Existing source schema/recovery normalization runs only on the private copy.
All durable business records and valid managed backups and projections remain
preserved. The bounded `backups/validation` domain, including its fixed pending
publication file when present, is inventoried, copied and sealed with those
backups. Its bytes are part of source/candidate continuity, not a new recovery
authority. Unknown or unsafe entries inside this recognized domain are refused;
retry cannot drop or replace its records to make a sealed candidate acceptable.
Runner graph validation is unchanged: pending intents or actual
attempt/quarantine trees stop cutover; migration never runs cleanup or invents
cleanup success. A completed/restart-cleaned Runner graph already requires
tree absence, and its rows and valid empty physical layout are preserved.

After revalidation, setup atomically replaces only the validated old fixed
primary with a prepared non-SQLite retirement marker; there is no rename-away
gap. When the primary was absent, publication is no-replace. Fresh setup also
publishes this marker. The complete candidate is then published no-replace at
the new `current/`, and the transition record is activated last. Source
retention, candidate validation and matching marker are prerequisites, not
additional LLM choices. The public durable write labels are
`state_layout_retire`, `state_layout_publish`, `state_layout_activate`, in that
order. Private candidate work is not reported as a change to the live DB.
When a retry supplies changed backup-policy options for an already sealed
candidate, setup preserves that exact candidate through activation, then applies
the supplied fields through ordinary maintenance configuration. Preview and
write results append `maintenance_configure` after the three layout stages;
omitted or equal fields do not add a configuration write. Configuration failure
retains the completed activation prefix and is resumed by ordinary setup.

Retry checks both recorded phase and actual objects. A matching marker can
prove fencing completed before phase persistence; a matching published
candidate can prove publication completed before activation. A sealed fresh
transition may resume through the empty old `current/` created during marker
preparation, after validating absence of old source material in preview and
under locks; ordinary empty-destination refusal remains unchanged. Changed source,
unowned/changed residue, mismatched marker or competing publication is preserved
and rejected. A nonempty preparation interrupted before its digests were sealed
is also preserved and reports `setup_incomplete`, rather than deleting or
accepting uncertain private bytes. Phase-boundary retry does not claim automatic
reconstruction at every interior preparation step. Process interruption is supported at these boundaries; this is
not a cross-location power-loss durability or arbitrary concurrent-old-process
guarantee. The qualified old executables are v0.10.0
`a9b80ce177a6dead10d51a070b76ff01f7af0294` and baseline
`c997fb65d58c598dac20f430498edf58b612fe32`. Blocking v0.1 executable reuse is
not supported; schema-v2 source-data migration remains supported separately.
Sealing does not waive supplied relocation-confirmation validation for an
unchanged binding. An already confirmed and rebound candidate may still resume
without another token; its recorded binding and confirmation digest are checked.

Keep the old exclusion and marker during package replacement. Retained source
has no automatic expiry, pruning or deletion. After new writes, it is stale;
any return to it is an explicitly selected
[paired rollback](release-install.md#release-upgrade-and-paired-rollback), not
error recovery. No reverse-schema migration, row merge, network operation or
new normal-loop command is introduced. State in the writable workspace is not
tamper-proof; existing structural, identity and evidence validation remains.

<a id="recovery-candidate-validity-contract"></a>

## Recovery Candidate Validity Contract

Every recognized managed recovery candidate first passes physical-file, SQLite,
schema/history/object, quick/foreign-key, project identity, binding lineage,
filename/embedded metadata, generation repository, retention, and set-envelope
validation. Internal content checks may use the exact
[immutable backup validation record](#immutable-backup-validation-records);
physical, binding, inventory and selection checks remain current. Only after
the whole set passes may exact stored Task verification
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
ordinary consumers do not fall back to a backup. Healthy fixed-primary Setup
checks the physical backup inventory and its filename/primary-ledger envelope,
without opening or hashing unrelated old backup contents. An old artifact's
content defect therefore does not block that Setup merely by existing. A backup
needed for migration protection, reconciliation, legacy transfer or recovery is
checked under that operation's content contract. Unsafe physical paths, sidecars
and inconsistent current generation metadata remain errors.

The private SQLite copy must still match the selected candidate's project,
binding, embedded generation rows, maintenance pointer, source schema, and Task
classification before repository normalization. Fixed recovery refreshes the
same complete inventory at restore entry and immediately before no-replace canonical
publication, reusing content certification only for unchanged immutable inputs,
and revalidates the normalized private copy's required schema
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

The current classifier admits 500 for schema v17 and 1,000 for schema v18-v34,
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
hashes. Relative input is made absolute before nonstrict resolution. After
structural preflight, ordinary consumers use its inspected absolute project
root for state selection and binding observation, rather than resolving the
original relative argument again. Omitted repo, equivalent relative input and
absolute input select the same existing state and binding; this neither waives
physical-root checks nor authorizes relocation or stored-hash repair.
Display uses the root basename, replaces controls/line separators with
U+FFFD, falls back to `project`, and is at most 200 code points. Neither is
identity.

The strict Windows alternative above does not change existence-independent
`canonical_path_v1`, rewrite a stored ID/hash/generation or binding history,
or authorize relocation. It verifies consistency with the existing spelling;
it does not claim general drive-alias uniqueness. Unverified fallback forms
fail closed, while standard-success forms retain their existing behavior.

`project_meta` plus append-only binding history records ID, generation,
old/new hashes, display, UTC time, fixed reason (`legacy_migration`,
`fresh_setup`, `confirmed_relocation`), and optional SHA-256 of the accepted
token. Generation 1 has no prior hash/token digest. Raw paths and raw tokens
are never stored.

One shared resolver owns every production database/backup/Evidence/Viewer/lock target.
It derives only current hash/display from repo, then reads stored identity and
binding. Every business writer revalidates ID/hash/generation under its short
lock. An authoritative fixed primary is validated without gating ordinary
business reads on backups or Viewer. Setup selects physical or content inspection
according to the artifact's role; recovery and old-layout transfer require the
complete relevant inventory.

Within a package-local migration source, with no fixed primary or eligible
fixed recovery, the resolver inspects only direct physical children of
`state/projects`, at most 64, without traversing unknown content. Unknown
direct children, unsafe canonical paths, links/reparse points, multiple
candidates/identities, newer/corrupt/foreign state, or validation ambiguity
fail closed and never fall through to fresh initialization.

Within that pre-activation source, precedence is:

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
lowercase hex; generation positive. Fixed-current sources support schemas
v1-v34; legacy-project sources remain capped at v14. Newer source schemas are
rejected. Serialization uses
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

Relocation failure has status null, required schema 34, empty warnings, one
error, null token/expiry, and no rejected value. A no-token mismatch preserves
the read-only future `planned_writes`; invalid/expired/stale/used/not-required
token rows have empty write arrays and mechanically observed bounded context.
Earlier common-preflight errors retain precedence.

The setup write vocabulary includes `database_restore`,
`database_initialize`, `migration_backup`, `database_migrate`,
`maintenance_configure`, `legacy_state_publish`, `project_binding_update`,
`evidence_projection_publish`, `viewer_publish`, and `legacy_state_cleanup`,
plus the three separation stages defined above. For already activated state,
the existing ordinary setup durable order is:

1. one source prefix: empty, restore, legacy publish, restore plus legacy
   publish, or initialize;
2. migration backup then migration;
3. maintenance configuration;
4. binding update only for confirmed mismatch;
5. Evidence projection publication;
6. Viewer publication;
7. legacy cleanup only after fixed database, binding, maintenance, Evidence, and Viewer.

New package-local fixed/legacy sources instead use the retained-source
[separation procedure](#project-state-separation), not the predecessor
delete-after-publication procedure below. Private preparation is not reported
as a live change. Binding CAS appends generation and increments Viewer source
generation in one short transaction; overflow/missing Viewer state/mismatch
rolls back without consuming the token.

Predecessor stage/cleanup metadata remains a recognized compatibility input.
Its bounded stage names are:

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

The predecessor cleanup primitive may remove only one fully owner-validated residue, enumerating
explicit allowed files then proven-empty directories. Read-only setup never
removes it. A stage without owner, invalid/mismatched owner, multiple pairs,
unsafe/unknown/oversized content fails `setup_incomplete` with no deletion.

Its persisted cleanup inventory is canonical JSON:

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
pending inventory metadata atomically. In the predecessor runtime, normal
business commands remain usable while valid fixed cleanup is pending; its
setup must complete that cleanup before separation.

New separation neither creates this delete-intent inventory nor retires the
original source through it. It preserves source material under its distinct
record and domain inventories, without relaxing the predecessor limits.
Preview creates no directory, lock, sidecar, temporary, backup, Evidence,
Viewer, Git or target change. Activated-state relocation uses the existing
short binding transaction; separation uses the explicit offline lock order
and retained-source rules above.

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
`viewer_status`, `relocation`, `usage`, `usage_hooks`, and `optional_features`.
`schema_to` is always 34. `schema_from` is safely observed source
schema, selected recovery schema, or null. Policy values are effective
requested/stored values, not persistence claims. `maintenance_enabled`,
Evidence status, and Viewer status describe durable post-command state.

`usage` independently contains `status`, `schema_to=10`, `planned_writes`,
`completed_writes` and `error`. Status is `not_attempted` after core failure,
`pending_core_setup` when preview cannot yet admit the future core binding,
`not_present` for an admitted missing-store preview, `initialized` after
publication, `migration_required` for exact schema 1 through 9 in preview, `migrated`
after the atomic schema-1-through-9-to-10 transaction, `current` after validation, or `unavailable`.
Write stages are `usage_initialize` and `usage_migrate`; completed writes are empty in preview. `error` is null
except the fixed `usage_unavailable` for numerical failure. Core `ok`, errors,
status and write lists remain about core setup: numerical failure does not
roll back valid core state or require re-running a completed migration.
An explicit later setup can initialize a still-absent numerical store; corrupt,
foreign or incompatible files are preserved, never overwritten or rebound.
After successful core and numerical preparation, explicit setup prepares the
project-local hook definition when collection is selected ON or a legacy
definition already exists. It never trusts or invokes hooks.

`usage_hooks` independently contains exactly `status`, `planned_writes`,
`completed_writes`, `trust`, `next_action`, and `error`. Status is
`not_attempted` after a core or numerical failure, `preparation_required` when
preview finds changes, `prepared` after publication, `current` for an unchanged
definition, `not_requested` for an unselected absent definition, `disabled`
for an explicit saved/requested OFF, or `unavailable` for a configuration/read/publication failure.
The only write stage is `usage_hooks_prepare`; preview completes no writes.
Fresh/relocation preview may plan definitions without assuming the future
numerical binding. `trust` is always `unknown`: prepared is not collection enabled.
`next_action` is `review_and_trust_hooks` for a prepared/current/planned definition,
`review_hook_configuration` for unavailable, otherwise null. `error` is null
or fixed `usage_hooks_unavailable`; no raw configuration or paths are returned.
Text output carries the same user trust/review guidance. Hook failure preserves
successful core/numerical setup, its success envelope and core write lists;
it does not block ordinary Task work. A later explicit setup retries preparation.

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

Explicitly opted-in [common diagnostics](specification.md#common-debug-logger)
observe write Setup and private-copy connections only after existing admission.
Preview and doctor remain no-write. Diagnostic failure cannot alter a Setup
stage, enablement choice, error or durable completed prefix.

The initial source connection is closed immediately after SQLite backup
finishes, before independent validation of the private copy. Copy preparation
does not retain that live reader. Initial-source, copied-database and fresh
pre-publication-source validation remain separate and complete; the private copy
is inspected once after its SQLite connection closes and receives its own
content certificate. The fresh
source stays open through replacement under the artifact lock. Private-stage
copy and generation restore use the same initial-source release boundary while
preserving their existing identity/recovery observations and publication rules.

### Optional Feature Selection

After core setup, the noninteractive CLI accepts any subset of
`--usage-collection on|off`, `--verification-runner on|off`,
`--effort-advisory on|off`, `--viewer-reload on|off`, `--review-wait on|off`,
`--review-answer-collection on|off`, and `--review-answer-correction on|off`. These explicit choices
authorize only their configuration and choice-record writes below. Omission
preserves existing choices/configuration; it is not OFF and never enables an
absent feature. Setup without choices offers unresolved items together. The
LLM uses the user's answer once; deferral or missing answers do not block core
setup or ordinary Tasks, and no normal-loop question or call is added.

The physical package's `config/setup-features.json` is strict UTF-8 JSON,
at most 16,384 bytes, with exactly `schema_version=1` and `choices`, an object
whose only optional keys are `usage_collection`, `verification_runner`,
`effort_advisory`, `viewer_reload`, `review_wait`, `review_answer_collection`,
`review_answer_correction`, each a Boolean. Missing keys are undecided.
Both ON and OFF persist; valid existing feature configs also count as resolved
without copying or rewriting them merely to remember a choice. This local
file is not core DB state, host trust, or Task authority; preserve it with the
other supported settings under the [installation contract](release-install.md#supported-installation).
Exclude package `config/` from source/release artifacts.

`optional_features` contains `features`, `offer`, and
`viewer_default_interval_seconds=30`. `features` has those seven fixed names.
Each row contains exactly `requested` (Boolean or null), `selection`
(`on|off|undecided|unknown`), `selection_source` (`saved|existing|none|unknown`),
`effective` (`on|off|unknown`), `status`
(`not_attempted|observed|preview|applied|unchanged|unavailable`), and `error`
(null, `feature_choices_unavailable`, or `feature_configuration_unavailable`).
`offer` lists undecided, safely inspected items without a supplied choice,
in the feature order above. Invalid/uninspectable settings are unknown and
need inspection, not an inferred ON/OFF answer. A saved choice is intent:
direct user configuration changes can make `selection` differ from `effective`.
Text output reports the same choice, effective configuration, outcome, and offer.

After successful core setup, if answer collection is requested ON or was already
selected ON, `optional_features` additionally contains exactly
`answer_collection_setup`. Its closed fields are `status`
(`disabled|current|prepared|preparation_required|connection_required|unavailable`),
`planned_writes`, `completed_writes`, `connection`
(`configured|missing|disabled|unknown`), `trust="unknown"`, and `error`
(null or `review_answer_setup_unavailable`). Ordered write identifiers are only
`review_answer_hook_prepare` and `review_answer_tool_allowlist_prepare`;
completed writes are the durable prefix and preview completes none. Configured
means an existing enabled server declaration, not a connected or trusted host.
A missing or disabled declaration reports `connection_required`; preparation of
the local choice or hook never silently activates a server. This diagnostic is
absent when the answer feature is not involved or core setup failed.

The application boundary is per feature, not a multi-file transaction. A
failure may leave an applied config without a saved choice or a saved choice
with unavailable prerequisites. Return `unavailable` with the safely readable
selection/effective values; never claim total success or retry completed core
stages. Other selected features may proceed. Core `ok`, write lists and schema
result are unchanged by optional failures. Preview writes nothing, reports
requested intent separately from observed effective configuration, and records
no new choice. Same-choice replay preserves bytes when no repair is necessary.

- Usage ON saves the local collector switch and prepares definitions; host
  trust and actual delivery remain unobserved. OFF prevents subsequent
  collector invocations before source reads/numerical writes, even if hooks
  remain trusted. Keep all existing numerical history and unrelated hooks;
  do not change host trust or stop in-flight processes. Legacy hooks without
  the new switch retain prior behavior. Fresh setup creates no hooks until ON.
- Runner changes only `trusted_local`, preserving Plan version/ID and all
  entries/limits. ON with no Plan creates a v1 `taskgov-local-plan` with empty
  entries using the existing local-only publisher. Existing Git ignore/index
  and physical-source requirements still apply; failure is non-gating here.
  OFF with no Plan writes only the choice. No Task or verification is invented;
  a missing Task entry remains manual verification, while malformed/stale/
  ambiguous Plans retain existing rejection. Only the existing target-setting
  boundary launches Runner. Task entry authoring remains separate.
- Effort toggles `enabled` in the existing profile, preserving thresholds.
  Newly enabled profiles have an empty threshold map; no thresholds are
  invented and there is no new gate or required tuning.
- Viewer toggles optional Boolean `enabled` in its existing profile, retaining
  the interval (new ON uses the offered 30 seconds). An omitted `enabled` is
  legacy ON. OFF resolves to interval zero, not interval deletion. Changed
  selection republishes the canonical Viewer through its existing Setup
  publisher; a publication failure remains an unavailable optional outcome.
  Already open pages adopt the published setting on their next reload; no
  browser launch or external control is implied.
- Review wait changes only its local Boolean choice; effective ON means the
  local wait policy (including compatible prepare/start) is enabled, not that
  the host is configured, connected, trusted or authorized to send. Omitted/malformed policy is disabled
  at runtime. Setup creates no operational store, reservation or MCP config and
  launches no process. The separately reviewed project MCP configuration uses
  the packaged entry point described in [review waiting](review-completion-specification.md#host-owned-review-waiting).
  OFF preserves state and permits original-parent inspection/cleanup/ack;
  running workers stop at their next fresh basis check with bounded cleanup.
- Answer collection ON saves its separate local choice and prepares its
  owned `SubagentStop` MCP handler in the project's physical `.codex/hooks.json`.
  The handler addresses the existing connected `taskgov_review_wait` server's
  `review_answer_collect` tool. Its typed `event` input interpolates only
  `hook_event_name`, `session_id`, `agent_id`, `turn_id`, and `stop_hook_active`;
  it forwards no body or transcript path. The status marker is
  `taskgov: collect structured review answer`, with a 30-second hook timeout.
  Preserve unrelated hooks and metadata. A conflicting owned handler or inline
  definition is unavailable, not silently replaced or duplicated. Same-choice
  replay preserves an already matching definition. If the existing physical
  `.codex/config.toml` has a `taskgov_review_wait` server with an explicit
  `enabled_tools` list, this selection also appends only `review_answer_collect`
  when absent. An omitted allowlist already admits tools and needs no edit.
  Preserve every other parsed setting and text outside that array. Malformed,
  conflicting disabled-tool, or unsupported table spellings are unavailable;
  never remove a deny rule or rewrite the whole configuration. Missing server
  configuration requires a separately reviewed connection setup: do not invent
  command, arguments, public-wrapper path, timezone, environment, trust, or
  enablement. OFF leaves existing tool admission intact. Feature effective ON
  requires the saved choice, matching hook and physically admitted tool; live
  connection, trust and host acceptance remain separate. Setup never connects a
  server, trusts or executes the hook, launches a reviewer, or certifies host
  capabilities. OFF preserves definitions and durable evidence while preventing
  future automatic collection; missing or malformed choice is disabled.
  Issued request protocol remains immutable across restart or choice changes.
- Answer correction ON saves only its separate opt-in and requires collection
  ON to be effective. It grants no message permission or observation owner.
  An unavailable exact follow-up-turn adapter returns the fixed unavailable
  outcome without sending. Normal valid answers remain usable. The bounded
  [answer collection contract](review-completion-specification.md#collected-review-answers)
  owns dispatch, original retention, recovery and existing completion gates.

### Optional Lifecycle Usage Collection

The bundled `scripts/usage_hook.py` is a neutral host adapter, not a public
Task command or a Skill trigger. Selected-ON or existing-hook setup prepares only the governed
project's physical `.codex/hooks.json` (creating `.codex` if absent); the user's
separate Codex review/trust action enables invocation. Setup preserves unrelated
hooks, group options, events and top-level metadata, adding or updating only its
own four command handlers. Equal definitions preserve the original file bytes.
Generated handlers use the reserved `statusMessage="taskgov: collect numerical usage"`
to identify ownership; duplicate owned handlers are collapsed. The exact old
documented ordinary-install recipe can be adopted; other unmarked
`usage_hook.py` invocations or project-inline TOML usage hooks require manual
configuration review instead of adding a second collector. Literal script-name
recognition is case-insensitive on Windows. Unrelated inline hooks are
preserved. Setup reads but never modifies project `config.toml`.
Malformed/duplicate-key JSON, nonphysical paths, configurations over 1 MiB,
ambiguous usage handlers and write failures remain unavailable and preserve the
original config. Publication uses a flushed sibling temporary, rechecks the
observed original, and replaces only the validated config; an absent destination
is published without clobber. A failure may leave a newly created empty `.codex`
directory, but not a partial definition. No host/user-wide definitions, trust
store, feature flag or managed policy is changed or scanned. Definitions in
other configuration layers remain user-managed, not deduplicated by setup.

The generated invocation uses the setup interpreter, physical package entrypoint
and `--repo` for that project. Windows uses a fixed UTF-16LE encoded PowerShell
invocation to preserve special path characters through the caller shell; this
does not expand configurable command behavior. Timeouts are 30 seconds for the
first three events and 3 for SessionEnd, host bounds rather than performance gates.
Setup neither proves host event delivery nor reads/detects trust; changed
definitions may need renewed user trust. Ordinary Task work needs no extra
command, model message, context injection or continuation request.

Supported event inputs are `SessionStart`, `Stop`, `SubagentStop` and
best-effort `SessionEnd`. The process must run from the governed project root;
the payload's absolute `cwd` must match it, and the physical package and current
state must pass the existing resolver. SubagentStop selects `agent_id` and
`agent_transcript_path`, never the parent's session or transcript. Every normal
return is exit0 with only `{}` and LF, including unavailable usage or invalid
input. No event changes Task ownership, state, gates, sealed Evidence or Viewer.

The adapter accepts no arguments for ordinary installs, or exactly
`--repo <project-root>`. That operand must resolve lexically to the process cwd;
it does not change cwd or select another project's state. Only explicit
`--repo` enables the existing development-only source-tree exception, with all
its physical-layout, required-file and no-competing-install checks intact.
Malformed arguments or a mismatched operand yield the same neutral response
without collection. The payload cwd must still match independently.

The invoking SessionStart can register itself as unassigned. Committed owner
acquisitions and core actual-reviewer bindings register their identities without
another Task-side numerical write. Successful bound original-review save also
best-effort registers its own caller in the existing numerical registry, so a
reviewer's stop can collect before parent submission; attribution still requires
the committed core Receipt. Reviewer read/material and ordinary inspection
remain read-only. Save registration failure never rejects or repeats the save.
The conditional review-wait decision helper similarly best-effort registers its
actual sender; its structurally validated single-turn marker is governed by
[wait attribution](database-specification.md#review-wait-decision-attribution).
Registration is not permission, Task ownership or review evidence.

Each event catches up only these registered project sessions. Within the separate
numerical store, it retains observed locations as root kind, current root-path
fingerprint and a bounded relative path, bound to the project and session. A
location is a candidate, not a validated source or numerical attribution. It
may be retained before the file or header exists. Later events, including a new
process, try finite fair pages directly before best-effort directory discovery.
Every use repeats physical/header/project/thread validation. Changed root
bindings cannot reuse old paths; old observations and missing-source gaps remain.

An unregistered collected-answer reviewer may leave a separate pending location
only when the parent's exact current native assignment proves the reviewer and
selected slot. Parent-child relationships alone grant nothing. This metadata-only
capture does not read the transcript or register its participant. A later exact
Receipt/source binding permits promotion; another reviewer's Receipt does not.
Confirmed cancellation or incompatible native basis revokes the pending hint.
An unavailable check or changed operational slot on the same native basis keeps
it unknown, since a retained original can still be registered later. The bounded
pending pool has no age-based expiry; a full pool refuses new candidates without
evicting existing ones or claiming coverage. Its lifetime ends through confirmed
revocation or promotion. Legacy review saves retain their existing registration
path; no speculative pending admission is added for legacy reviewers.

Discovery uses
exact registered IDs to locate standard Codex log filename candidates in
`CODEX_HOME/sessions` (year/month/day) and flat `archived_sessions`, or the
default user `.codex` location. Both the legacy session-UUID suffix and the
session-UUID plus `_` and segment-UUID suffix are candidates; the segment ID is
never used as the session identity. Coexisting files are collected through the
same response-identity deduplication and cursor rules. It does not open unrelated
headers, follow directory links/junctions or discover descendants from parenthood. The hook's
path is only a hint within those roots. Every candidate must pass existing
physical/header/project/thread validation before numerical source registration.
Unknown layouts remain unavailable; no host index, private-body search or
all-project header scan is a fallback.

Persisted complete records flow through the existing atomic cursor/observation
writer and immutable usage publisher. Delayed endpoint records, completed Tasks
and previous participants are retried at later events. Interrupted/concurrent
work remains replayable; stale batches do not rerun a Task command. Missing or
invalid sources preserve observed totals with gaps. Existing schema10 is required:
events never initialize, migrate, repair or rebind a database.
Saved partial tails and unread backlog remain visible as their existing fixed
gaps in Task usage and published snapshots until later collection clears the
source pending state. Hook silence alone never clears those gaps.

Collection reserves bounded work for previous participants, old segments and
prefix audits as well as the invoking participant. Newly observed candidates
join the fair queue without resetting previously queued work. An exact standard
session basename may also be checked in the current flat archive; this adds no
directory search and preserves separate source cursors and response deduplication.
Reading the archive never removes a missing-source gap on the old location.
Directory inventory and
source attempts resume across events; new traffic cannot continually select
only the newest source. After the oldest candidate, the invoking participant
may receive bounded additional ingest slices when the preceding committed slice
reports more records and advances its complete-record or oversized-record drain
position. Its exact host hint takes priority within that allocation; other
discovered segments retain fair turns. Partial tails, replay, errors or no
forward progress do not trigger immediate repetition. The allocation uses the
existing event budget and does not discover extra paths or infer attribution.
An idle reentry of a source whose oversized unfinished line has drained to EOF
continues to report `partial_tail` without claiming ingest progress or another
immediate priority slice.
Later cycles catch moved directory entries. Unobserved old logs, missed segments
and arbitrary renames retain best-effort discovery; this is not a complete
discovery guarantee and adds no mandatory manual recovery command. Work and
database-lock budgets yield neutral pending/unavailable results, preserving
committed progress and last-good publication. Prefix integrity is explicitly
[deferred](database-specification.md#numerical-usage-store); immutable snapshot
validation and file comparison follow the explicitly deferred
[rotating audit](database-specification.md#immutable-usage-evidence).
Completed audit steps resume on later events against the same index basis;
`evidence_verification_deferred` never claims current integrity. Internal budgets do
not assert every environment finishes within the host timeout. The cooperative
budget begins at collection entry and includes state admission; a later stage
does not reset it. Operation-local core admission retains physical/schema/binding
checks. The numerical change consumer validates affected ownership, cycle and
actual-reviewer relations, preserves pending progress and expands their old/new
ranges under the [reconciliation contract](database-design.md#usage-reconciliation-tracking).
Final index replacement still requires fresh admitted reads and matching
transaction-bound generations, with no pending reconciliation work. Prior
classification alone cannot certify the fresh basis.

Stop, quiet files and elapsed time never establish final coverage. Disabled or
untrusted hooks perform no collection. With no later event, late usage stays
pending; starting/resuming an ordinary session in that same project is the
recovery trigger, not merely restarting the app. SessionEnd is supplemental,
not guaranteed cleanup. Host-specific delivery and transcript support must be
observed separately; mocked tests do not certify Desktop, CLI or all OSes.
See the packaged [installation procedure](../task-governance-tool/references/usage_hooks.md).

<a id="same-process-maintenance"></a>

### Post-Commit Maintenance

The [incremental Viewer contract](viewer-specification.md#conditional-incremental-projection)
governs current Viewer capture/publication only. It grants no live Setup,
configuration or recovery permission and does not change Evidence or backup.

Maintenance is permanently enabled by successful setup; there is no disable
surface. Every successful state-changed business mutation commits and closes
SQLite before bounded same-process Evidence and Viewer maintenance. Any such
mutation may retry a due Evidence projection; only completion-cycle insertion
advances its source generation. Viewer refresh runs second only for
Viewer-relevant mutations. A changed handoff advances neither projection but
may retry already-due Evidence. Read-only, failed, replayed, no-op,
setup configuration, and maintenance-internal operations trigger nothing.
Eligibility checks validate structural state and the consumed maintenance
metadata without traversing all Task histories when disabled, current or not
due. Routine Evidence validates unpublished Bundle closure and its acknowledged
membership under the [publication contract](evidence-specification.md#canonical-evidence-bundle-and-index-formats).
Explicit Setup establishes or repairs that disposable baseline with complete
source validation, including after DB replacement or format change. Viewer uses its
selected-Task capture, checked display cache and fresh structural effect guards;
asynchronous backup validates its consistent private copy as specified below.
Metadata observed before a lock never authorizes publication.
The Runner path retains exactly the one existing target-set maintenance opportunity
only for a successful or fallback command. A post-T1 Runner error invokes no
maintenance; T1 has already advanced the ordinary due state, so the next
normal maintenance opportunity or explicit setup repair catches up. Internal
Runner intent, cleanup, observation, Reference, and link writes do not add
another coordinator call or advance Evidence or Viewer generation.

Evidence and Viewer remain same-process work, without a thread, timer, watcher,
service, queue, daemon, scheduler, or network operation. Each projection uses a
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
| `backup_request_deferred` | `Backup request was deferred; task result is unchanged` |
| `backup_request_failed` | `Backup request could not be published; task result is unchanged` |

Backup warnings describe only the bounded request-publication result, not
service acceptance, worker startup or a future backup outcome. An absent or
incompatible service produces a warning only when opted-in backup is due or
needs recovery. Unreadable eligibility may return the bounded failure warning.
A confirmed current backup needs no service check or warning; an already
running backup is a normal observation.

#### Asynchronous Backup

The same eligible changed mutation may publish one bounded asynchronous-backup
opportunity after Evidence and Viewer maintenance. The existing configured interval and
last successful publication determine whether a backup is due; absence of a
successful backup is due. An interrupted, failed or deferred attempt also
permits bounded recovery even before that interval expires. If reconciliation
confirms a current backup, recovery ends without taking another copy or
continually restarting a worker. Task, handoff, review and Verification Receipt
mutations, including completion and Runner's one target-set opportunity, use
this same entry. Read-only, failed, replayed, no-op, configuration-only Setup
and maintenance-internal operations never publish an opportunity. No extra LLM command,
polling step or manual completion action is required.

The caller reads only narrow backup-eligibility policy and, when needed,
publishes one fixed local opportunity for the already configured project MCP
service. Publication means requested only; it proves neither acceptance,
startup nor successful backup. The caller does not launch a process, wait for
service consumption or completion, copy the database, audit Task histories or
reconcile/prune generations. An absent or incompatible service defers the
request without changing business success. There is no CLI-spawn or
synchronous-copy fallback, automatic service start, configuration write or
additional LLM operation.

The service owns a separate opportunity observer independently of review-wait
policy and individual review workers. Every ten seconds it observes the one
fixed opportunity file; unchanged or absent input opens no core database and
performs no package-content hashing. Only a fresh, bounded, project-bound
opportunity may enter fresh backup admission and child startup. Compatible
presence uses the version-one opportunity ABI and core schema version; actual
due startup also verifies the service's loaded/deployed runtime identity.
One current request identity is consumed at most once, including a failed or
unknown launch. This is coalescing, not a queue, delivery guarantee or replay
history. Service startup and ownership takeover baseline the existing request
without launching it. A subsequent eligible changed mutation is the recovery
opportunity; elapsed time or service restart alone never replays old work.

Service startup obtains its core target, then immediately after exclusive
ownership acquisition reads current schema/binding and enabled policy again,
including when acquisition follows standby. The target must still match
exactly, and that transaction closes before presence activation. Failed
readmission or presence activation after ownership acquisition ends the
observer until service restart. Standby retries only OS-lease contention;
it adds no periodic DB retry to normal idle observation.

After an authorized relocation or restore, well-formed, physically safe service
and opportunity records for the same durable project may retain a different
binding. Only a new service with freshly confirmed current core target and
exclusive ownership treats them as old-request baseline and publishes current
presence. Their
saved generation, whether higher or lower, is not binding authority. A fresh
request may replace that same-project old opportunity only for a live service
matching the exact current binding, owner and compatibility. Dispatch and all
backup guards still require the current exact basis; no old request executes.
Foreign-project, malformed or unsafe records are preserved and rejected. This
changes no core binding, worker state, backup artifact or Setup permission.

One project has at most one active backup worker. Further updates while it
runs add no worker or queued work. A service-owned worker can continue after
the requesting CLI exits and ends after one attempt. Backup completion does
not stop the MCP service; review-wait completion, cancellation and timers do
not stop the backup. Service or host shutdown may interrupt it, preserving the
last normal generation for recovery on a later eligible mutation. The observer
does not create a new host timer or turn a clock tick into a backup schedule.

Launch and running ownership use OS locks and an attempt identity. A saved
phase, PID, request publication, lock-file existence or elapsed time alone is not proof that a
worker is alive or stopped. Startup failure and abnormal termination preserve
the last normal backup. The next eligible changed mutation can recover an
interrupted attempt and try again without a separate user operation. An
uncertain owner remains unknown rather than being replaced unsafely.
Runtime-state publication and reading coordinate through a short file lock,
without retaining a database transaction or process wait. Diagnosis neither
creates this lock nor waits for it. A transient unavailable state is not proof
that another attempt replaced the worker; service-owned startup may observe again only within
its original deadline. A confirmed different attempt still rejects the old one.
If the service's cleanup of a newly launched child cannot confirm its termination, a bounded
attempt-specific barrier prevents another launch until matching terminal
evidence and released ownership establish its end.

The worker captures one consistent rollback-journal SQLite copy while holding
the live source only for the bounded control read and copy. Schema, integrity,
foreign-key, complete relationship and privacy validation run on that private
copy after closing the source. Fresh live checks before publication and short
metadata writes validate database-file identity, project/binding/schema and
the consumed policy/generation basis; they do not repeat the full historical
audit. The exact boundary is owned by
[asynchronous backup admission](database-specification.md#asynchronous-backup-admission).

Ordinary updates after capture remain valid and do not force another copy or
an unbounded catch-up loop. They belong to a later due backup. Capture time,
publication success and Task completion are separate facts: successful Task
completion does not assert that its final state is already in a backup, and
backup failure never reverses the committed Task result. Explicit Setup's
pre-migration protection, recovery copies and complete-source admission remain
unchanged. The routine worker does not replace those synchronous protections.

The retained backup primitive's successful atomic publication records its
immutable generation ID, publication time, and 1-20
`publication_retention`, then prunes recognized older generations to the
applied value. Unknown/linked/foreign artifacts are never imported or deleted.

Failed/deferred outcome recording validates structural state and consumed
maintenance metadata, without auditing Task or review histories. Its short
writer compares the observed success generation, success time, applied
retention, prior outcome and complete managed-generation inventory before
updating. Prune-only recovery and equal-count generation or metadata changes
invalidate an earlier observation even when the latest success is unchanged;
timestamps alone never authorize
a stale result to overwrite a newer success or recovery. A failure after this
attempt's own publication or reconciliation remains due. Failure to record the
outcome preserves the original operation result and last-good artifact.
The observation also pins the database file instance: restoring an identical
success tuple into a replacement database cannot revive an old failed/deferred
write. This continuity comparison is not a content-validation shortcut.

Every v11+ backup attempt first reconciles bounded crash residue under the same
lock: import one valid file missing a row; remove a row whose file is invalid/
missing without deleting an untrusted path; update latest/applied retention;
and finish file-before-row pruning oldest first. Valid physical artifacts must
have unique generation IDs before any reconciliation metadata change or pruning.
Duplicate IDs are rejected without discarding existing rows or success metadata.
It will not publish while
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

#### Immutable Backup Validation Records

The fixed `backups/validation/` directory contains at most 21 derived records,
one per immutable managed generation, plus one bounded publication scratch
file. Each record is at most 4,096 bytes. It remains available when the primary
database is lost, and is never embedded in the file whose SHA-256 it records.
No new core schema, public option, service, timer or normal Skill operation is
introduced. Records are not Task Evidence or generation ownership.

A full record certifies the final closed copy's schema/history/objects,
quick/FK checks, domain rows and relations, privacy and recovery classification.
It binds format and validation-rule versions, exact mode/scope, source schema,
project and full generation tuple, byte count and SHA-256. A canonical envelope
checksum detects record corruption; it is not authentication against a writer
who can replace both database and record. Existing local filesystem trust and
physical safety remain necessary. Structural-only records never certify full
recovery eligibility.

Only successful complete private-copy inspection may create a full record.
Content fingerprints bracket that inspection. A final publication fingerprint
must still match the same physical file instance; the source database and the
SQLite-produced copy are never assumed byte-identical. Validation failure or
drift publishes no success record for the changed content.

Under the existing backup lock, publication writes the validated record through
one fixed `.pending.json`, then publishes the immutable backup file, commits its
generation/pointer, and performs existing retention. A record without its exact
artifact is orphaned derived state, not restoration or pruning authority.
The next authorized backup may remove a valid same-project orphan record only;
it never removes an artifact or changes generation rows on that basis. A file
published before its generation commit remains the existing bounded file-only
reconciliation case and must pass content and current relationship checks.
Record publication failure leaves the last normal generation intact. Scratch
cleanup touches only the fixed bounded physical file, under artifact ownership.
Malformed, foreign or unsafe final records are preserved and rejected when
needed, never silently replaced. Pruning a separately authorized generation
also removes its matching derived record; interrupted cleanup is bounded orphan
state.

A needed candidate is fingerprinted once per coherent content observation.
The fingerprint and metadata observation are bound to the same SQLite read
snapshot, with the hash descriptor closed only after the SQLite reader. A
record read before admission is confirmed again before its result is used.
An exact valid record permits skipping only its certified internal audit.
Current physical paths and file identity, sidecars, thin schema/project/binding
metadata, filename/generation tuple, inventory relationships, selected source
and effect-boundary continuity are still checked. A same-byte replacement after
planning remains drift. Records never authorize reselection under a fixed plan.
Fresh effect-boundary observations may require a new hash; a redundant second
classification within the same observation does not. Size/mtime alone are not
content evidence.
Published record bytes and physical identity must still match the prepared
record at admission. Async file/metadata publication cannot turn later record
corruption, disappearance or replacement into success. Before deleting older
generations, retention confirms the surviving recovery artifacts again; failed
confirmation preserves the old normal files and does not advertise success.
That confirmation retains the admitted digest, project/binding, repository
observation and physical identity even when no persistent record exists. A
different internally valid database cannot replace the admitted contents.
File-only generation import and v10 pointer recovery require this continuity
even when retention will delete no file.
Reconciliation also validates complete candidate lineage, embedded generation
prefixes and pointers against current primary metadata before importing or
pruning. The surviving set must retain an eligible generation at its structural
head's binding; an eligible artifact at an older binding is not its fallback.

Missing records and valid records with obsolete rules or insufficient scope
fall back to actual inspection of that needed artifact. Read-only discovery,
preview and diagnosis never create or refresh records. Hash, project, generation
or schema mismatch, malformed records and failed inspection cannot be re-enrolled
as known-good. Old backups are not rewritten or migrated en masse.
Explicit full content audits perform the audit even with a matching record.
Normal healthy-primary Setup has no old-backup content/hash dependency at all.

Restore may reuse certified source contents, but separately checks destination
compatibility, the actual copied state before normalization, and the normalized
or migrated state before publication. Modified private copies never inherit the
original's content certificate. Recovery selection, error precedence, last-good
retention, no-replace publication and read-only/no-repair boundaries remain.

Recovery planning, including backup-only transfer from an old layout, reads its
policy from the selected, content-confirmed backup snapshot. It preserves fresh
binding, maintenance and source-continuity checks without unconditionally
repeating that source's certified internal audit. Planning and preview write
no records; actual copied and normalized outputs retain independent validation.

#### Projection And Performance Checks

Viewer maintenance compares source/render generation, renders once and at most
one follow-up per mutation, prevents older-over-newer publication, and leaves
remaining churn due. Setup rerun is the only explicit force/repair.

The performance fixture is 12 Tasks/191 events and 500 Tasks/5,000 events,
payloads 80/512/256 UTF-8 bytes, with eight note writes at injected minutes
0, 1, 5, 29, 30, 31, 59, and 60. The fixture retains its `backup-only` and
`combined` mode names for CI test identity. Ordinary writes perform no
synchronous backup copy or validation, including when interval policy is due. The
`viewer-only` fixture has a historical backup; Viewer is eligible on all eight
writes. Enabled-backup-policy and Viewer-only overhead versus disabled is at
most 10 seconds, Viewer with due backup policy overhead is at most 15 seconds,
and each command-position median is below 5 seconds on Windows CI. Absence of
synchronous backup work, render, call, byte, and zero-wait limits are hard;
timing cannot waive them. Separate isolated worker tests control startup and
capture/publication barriers, verify one active worker, and require ordinary
updates to complete during private-copy validation. They measure CLI return,
service observation, live reader/writer lifetime, and SQL/connection counts
independently of the worker's total elapsed time. Request publication, service
consumption and worker completion are separate assertions. Idle ten-second
observations open no database and hash no package contents. Absent/incompatible
service, restart baselining and ownership takeover retain the same non-replay
boundary. Only due or recovery opportunities can start a worker; updates while
it runs add no launch or queued request.

Windows timing qualification uses one complete non-qualifying warm-up followed
by six measured rounds from fresh copies of the same fixed fixture. The
three-mode qualifier uses every mode permutation once; the two-mode qualifier
uses both orders equally. Every overhead remains paired with the disabled
total from that same round. The mode-specific total budget applies to the
median of the six paired enabled-minus-disabled totals: 10 seconds for
`backup-only` (enabled policy without synchronous backup) and Viewer-only, and
15 seconds for `combined` (Viewer with due backup policy).
The strict 5-second budget applies to the median of the six
observations at each mode and write position and requires that median to remain
below 5 seconds; it is not averaged across commands. Diagnostics are bounded to
the fixture and mode, the six paired
overheads, eight command medians, and the maximum raw measured observation.
Functional, count, byte, no-synchronous-backup, render, and call assertions remain separate
hard failures and are never decided by the timing statistic.

Repository pull-request and `main`-push CI validate the complete discovered
inventory and base-lane ownership, then defer exactly the two closed wall-clock
qualification test identities. They continue to execute the separate fixed-
fixture functional and capacity assertions. The timing tests execute and block
only in the explicit manual `workflow_dispatch` release-candidate route, whose
Python 3.12 and 3.14 `all` jobs each retain the unchanged standard-discovery
suite. This is event-aware test selection, not a skip, retry, threshold change,
or fourth lane.
