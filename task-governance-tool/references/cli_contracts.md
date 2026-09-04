# CLI Contracts

Use this reference when exact public commands, arguments, JSON fields, bounds,
or error behavior matter.

The current v0.13.0 package uses task schema v22 and offline snapshot v4 with
source schemas v5 through v22. The published v0.10.0 release remains the
immutable schema-v16 predecessor.

Schema v19 sealed native Bundle v1. Schema v20 preserves existing v1 bytes and
digests, seals native Bundle v2 with a derived verification basis and null
Runner observation, and maintains fixed Evidence index v2 automatically. It
adds no export/projection command, Runner, Analyzer, Viewer Evidence surface,
network/model invocation, public leaf, or normal-loop call.

Schema v21 keeps those public surfaces unchanged and uses the existing Bundle
v2 tagged union. When `verification_basis.kind` is `caller_attestation` or
`not_required`, the root `runner_observation` is null; when the kind is
`runner_observation`, that root field contains the qualifying exact Runner
observation. It adds no public command, argument, Skill trigger, or Viewer
surface. Gate integration adds only `verification_route` and `blocking_code` to
the existing target-set JSON success data. Bundle/Evidence serialization adds no
normal-loop call, and no post-target `task show` is added.

Current schema v22 retains that protocol and removes only retired Analyzer
reservations from the shared Evidence schema and current enums. Explicit
setup migrates supported sources through 22; exact-v22 reentry validates
without migration. New native Bundles use source 22/format 2. Retained
source-19/20/21 Bundle bytes and digests are unchanged, while the refreshed
format-2 index reports its actual source schema 22.

## Contents

- [Invocation And Public Inventory](#invocation-and-public-inventory)
- [Envelope And Read/Write Boundary](#envelope-and-readwrite-boundary)
- [`setup`](#setup)
- [`doctor`](#doctor)
- [Task Commands](#task-commands)
  - [`task add`](#task-add)
  - [`task list`](#task-list)
  - [`task next`](#task-next)
  - [`task current`](#task-current)
  - [`task effort`](#task-effort)
  - [`task show`](#task-show)
  - [`task checkpoint`](#task-checkpoint)
  - [`task edit`](#task-edit)
  - [`task complete`](#task-complete)
- [Verification Receipt](#verification-receipt)
- [Local Handoff Commands](#local-handoff-commands)
- [Review Commands](#review-commands)
  - [`review prepare`](#review-prepare)
  - [Review Evidence](#review-evidence)
- [Internal Continuity Boundary](#internal-continuity-boundary)
- [Errors And Privacy](#errors-and-privacy)

## Invocation And Public Inventory

For normal governed-project use, install one physical project-scoped copy and
run from the project root:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py <command> [options]
```

When launching from inside the installed Skill directory, pass the target
project explicitly:

```powershell
python scripts/taskgov.py <command> --repo <target-project> [options]
```

Omitting `--repo` means the current directory and never re-roots it to an
enclosing Git worktree. A non-Git directory is valid. Stateful use supports
one physical project-scoped package only; user-wide, symbolic-link, and
Windows junction layouts are unsupported. The verified runtime is Windows
with Python 3.12 or later.

The complete public command inventory is exactly these 21 leaves:

1. `setup`
2. `doctor`
3. `task add`
4. `task list`
5. `task next`
6. `task current`
7. `task effort`
8. `task show`
9. `task edit`
10. `task complete`
11. `task checkpoint`
12. `handoff record`
13. `handoff list`
14. `handoff show`
15. `handoff withdraw`
16. `review prepare`
17. `review target set`
18. `review receipt add`
19. `review finding add`
20. `review finding resolve`
21. `verification receipt add`

There are no public aliases, alternate state locations, storage-management
commands, projection-management commands, repair commands, or admin commands.
Unknown commands fail before package, project, Git, or local-state resolution.

Common options are:

- `--repo <path>`: governed project root; default current directory.
- `--json`: emit the stable JSON envelope.
- `--read-only`: prohibit creation, migration, or writes.
- root `--version`: print the package version.

Applicable common options may appear before or after command groups as shown by
`--help`.

## Envelope And Read/Write Boundary

Every JSON result has exactly these top-level keys:

```json
{
  "ok": true,
  "command": "task.next",
  "project_id": "tg_project_550e8400e29b41d4a716446655440000",
  "data": {},
  "warnings": [],
  "errors": []
}
```

Public output contains no local storage, backup, projection, or rejected-input
path. Error rows contain only `code` and a sanitized `message`.
Fresh setup uses a `uuid_v1` identity in the shown format. Migrated
`legacy_path_v1` IDs are preserved byte-for-byte; top-level `project_id`
always comes from stored identity and is never recomputed from the current
path.

Inherently read-only commands are `doctor`, task `list`, `next`, `current`,
`effort`, and `show`, `task complete --check`, handoff `list` and `show`, and
`review prepare`. `setup --read-only` is a no-write preview.

Write commands other than `setup` require current initialized state. They
never initialize or migrate implicitly. `setup` is the only initializer and
migrator. `--read-only` rejects a write form before a business write.

Each related read response uses one lock-respecting coherent transaction.
Rollback-journal contention maps to `database_busy`; unsupported WAL state
maps to `unsupported_journal_mode`. Raw SQLite or operating-system details are
never emitted. Git observation occurs outside SQLite transactions. Write
commands revalidate their task/Contract/review basis under a short write
transaction before one atomic business update. The explicit Runner Plan action
is the sole exception to a one-store description: a real Task basis edit commits
first, closes SQLite, and only then confirms or publishes one separately
committed canonical Plan file as described under `task edit`.

The normal no-finding Tier 2 manual/fallback Skill graph is bounded to ten
governance subprocess calls when the Effort Advisory is off and eleven when the
mandatory pre-work `task show` boolean enables it. The Receiptless Runner-pass
branch is one call lower. The target-set response itself supplies the closed
route; no second read or LLM choice is required.

## `setup`

`setup` is the sole explicit initializer, migrator, one-way continuity opt-in,
and canonical Evidence/Viewer projection repair action:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

Options:

- `--backup-interval-minutes <1..1440>`
- `--backup-generations <1..20>`
- `--confirm-relocation <token>` for one explicitly approved exceptional
  relocation
- `--read-only` for a no-write preview

The normal Skill flow supplies neither policy option nor a relocation token.
The exceptional binding-mismatch flow supplies one only after the explicit
approval boundary below. A first setup defaults to 30 minutes after the last
successful managed copy and three retained generations. Once configured,
omitted options preserve stored values; values equal to stored policy are a
write-free replay.

When the fixed canonical database is absent, the shared resolver first
validates fixed-layout managed generations. If no fixed source exists, it may
select exactly one eligible legacy-layout source under the compatibility
rules below. A same-binding legacy primary or legacy backup-only source is
staged and published into the fixed layout; backup-only recovery performs
`database_restore` inside that private fixed-layout stage before
`legacy_state_publish` and never recreates the old legacy primary. Setup then
performs any required normal
migration/configuration/Viewer publication. It never overwrites an existing
database or accepts a caller path. Invalid, foreign, linked, unrecognized, and
ambiguous artifacts are unchanged and fail closed. After the complete recovery
set passes structural validation, a set with no eligible current-binding
generation solely because every such candidate is locally rejected for stored
Task-verification privacy/capacity fails with `setup_restore_failed` and
message `managed backup could not be restored`; setup does not initialize
empty state. Candidate corruption, foreign identity, binding/lineage
divergence, metadata/repository/retention/sidecar inconsistency, and other
discovery-time structural failures retain their specific resolver result where
applicable and otherwise fail no-write as `project_state_unreadable`. A
rollback-journal entry beside a missing fixed primary is such structural
residue; setup neither applies nor changes it. Drift, copy, normalization, or
no-clobber publication failure after a candidate plan is established remains
`setup_restore_failed`. A moved legacy backup-only source is not a relocation
candidate and fails no-write as `project_state_unreadable`.

The current package stores the database in the fixed package-local
`state/current/` layout. Fresh write-mode setup creates one UUID-backed
immutable project identity; same-binding schema-v1-through-v13 legacy state is
published into the fixed layout by explicit setup without an LLM choice.
Project identity is separate from the mutable governed-directory binding.

Package replacement preserves project-local state and requires explicit setup.
There is no public downgrade or restore command. Release rollback means
restoring one matched pre-migration package, database, and managed-artifact set
together; an older runtime against a newer schema, mixed generations, an
in-place reverse migration, or a Git checkout alone is not rollback.

A binding mismatch never authorizes a rebind. Normal commands and `doctor`
return the bounded relocation condition without writing. Write-mode setup
without a token returns `project_relocation_required`. Only
`setup --read-only` returns successful `status="relocation_preview"` with the
future ordered write plan, an exact confirmation token, and its expiry. Token
issuance is not approval: the Skill presents the plan, waits for explicit
current user approval, and only then calls write-mode setup with that exact
unexpired token. It never infers move/copy/fork semantics or auto-confirms.
Expired or stale context requires a fresh preview and fresh user approval.

`data` always has exactly:

```json
{
  "status": "setup_complete",
  "planned_writes": [
    "database_initialize",
    "maintenance_configure",
    "evidence_projection_publish",
    "viewer_publish"
  ],
  "completed_writes": [
    "database_initialize",
    "maintenance_configure",
    "evidence_projection_publish",
    "viewer_publish"
  ],
  "schema_from": null,
  "schema_to": 22,
  "maintenance_enabled": true,
  "backup_interval_minutes": 30,
  "backup_generations": 3,
  "evidence_status": "published",
  "viewer_status": "published",
  "relocation": {
    "required": false,
    "source_layout": null,
    "identity_scheme": null,
    "binding_generation": null,
    "confirmation_token": null,
    "expires_at": null
  }
}
```

Successful `status` is `setup_preview`, `relocation_preview`,
`setup_complete`, or `already_setup`. Write-list values are limited to
`database_restore`, `legacy_state_publish`, `database_initialize`,
`migration_backup`, `database_migrate`, `maintenance_configure`,
`project_binding_update`, `evidence_projection_publish`, `viewer_publish`, and `legacy_state_cleanup` in
execution order. `viewer_status` is `not_present`, `current`, `published`, or
`repair_required`.

`evidence_status` uses the same four values. Evidence publication follows
maintenance/binding and precedes Viewer; preview lists it without writing.

`relocation` is always present with exactly the six shown keys. `required`
is boolean. `source_layout` is null, `legacy_projects_v1`, or
`fixed_current_v1`; `identity_scheme` is null, `legacy_path_v1`, or `uuid_v1`;
`binding_generation` is a positive integer or null. The confirmation token
and expiry are non-null only in a successful relocation preview. Public output
does not expose a stored or current absolute binding path.

Preview reports current durable state, not planned state:
`completed_writes=[]`, and a fresh preview keeps
`maintenance_enabled=false`. A healthy replay has empty write lists. Every
error has `status=null`; preflight/policy failures use empty write lists and
null observed values except `schema_to=22`. A later-stage failure reports only
the durable ordered prefix.

Setup is noninteractive and idempotent. It does not create a second
configuration file, disable continuity after opt-in, contact a network, mutate
Git, or modify target source. For a Git-candidate target, only its single
bounded effective-ignore preflight may inspect Git.

## `doctor`

Doctor is the sole diagnostic and is inherently read-only:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py doctor --json
```

It never initializes, migrates, repairs, backs up, renders, locks an artifact,
runs project tests, or changes target state. For a Git-candidate target, only
its single bounded effective-ignore preflight may inspect Git. It is not a
prerequisite for setup or normal task work.

On a recognized binding mismatch, doctor remains successful and reports
`project_state.code="relocation_required"`, warning
`project_relocation_required`, `setup_eligible=true`, and the fixed
`suggested_action="continue"`. It never returns a relocation token or performs
the preview/confirmation step.

`data` has exactly `suggested_action`, `setup_eligible`, and `components`.
`suggested_action` is always `continue`. Component keys are exactly
`package`, `project_state`, `task_summary`, `handoff_delivery`, and
`maintenance`.

A ready result has this structure:

```json
{
  "suggested_action": "continue",
  "setup_eligible": true,
  "components": {
    "package": {
      "package_name": "task-governance-tool",
      "package_version": "0.13.0",
      "release_origin": "github:VAiring/task-governance-tool",
      "manifest_version": 1,
      "status": "clean",
      "changed_core_count": 0,
      "changed_core_paths": [],
      "changed_core_paths_truncated": false,
      "unknown_reasons": [],
      "suggested_action": "continue"
    },
    "project_state": {
      "code": "ready",
      "schema_version": 22,
      "required_schema_version": 22
    },
    "task_summary": {
      "code": "ready",
      "active": 0,
      "blocked": 0,
      "done": 0,
      "next_actionable": 0,
      "paused": 0,
      "review_pending": 0
    },
    "handoff_delivery": {
      "code": "ready",
      "handoff_pending": 0,
      "adapter_enabled": false,
      "delivery_due": false
    },
    "maintenance": {
      "code": "enabled",
      "opted_in": true,
      "backup": {
        "code": "current",
        "due": false,
        "interval_minutes": 30,
        "generations": 3,
        "last_success_at": "2026-07-27T00:00:00Z",
        "last_outcome": {
          "code": "succeeded",
          "occurred_at": "2026-07-27T00:00:00Z"
        }
      },
      "evidence": {
        "code": "current",
        "due": false,
        "source_generation": 1,
        "published_generation": 1,
        "last_success_at": "2026-07-27T00:00:00Z",
        "last_outcome": {
          "code": "succeeded",
          "occurred_at": "2026-07-27T00:00:00Z"
        }
      },
      "viewer": {
        "code": "current",
        "due": false,
        "source_generation": 1,
        "rendered_generation": 1,
        "last_success_at": "2026-07-27T00:00:00Z",
        "last_outcome": {
          "code": "succeeded",
          "occurred_at": "2026-07-27T00:00:00Z"
        }
      }
    }
  }
}
```

Unavailable project-backed components are exactly `{"code":"unavailable"}`.
Not-yet-known maintenance values are `null`, not omitted. Maintenance outcome
codes are `none`, `succeeded`, `deferred`, or `failed`; readable maintenance
states remain successful advisory data and never create a stop.
The Evidence object has exactly `code`, `due`, `source_generation`,
`published_generation`, `last_success_at`, and `last_outcome`; doctor never
opens or repairs its JSON files.

Doctor validates the complete stored Task batch before returning Task-derived
counts. A malformed, wrong-storage-class, privacy-rejected, over-capacity, or
cross-field-invalid Task, or invalid current Contract relationship makes
`project_state.code="unreadable"`, all other
project-backed components `{"code":"unavailable"}`,
`setup_eligible=false`, and returns exit 2 with the fixed
`project_state_unreadable` error. It exposes no rejected value and writes
nothing.

Package `modified` or `unknown` is an exit-0 warning and makes
`setup_eligible=false`. Missing or migratable state is an exit-0
`setup_required` or `migration_required` warning. Invalid layout/project,
unsupported runtime/journal, busy or unreadable state, project mismatch, and a
newer schema are exit-2 errors. No row exposes a local path, digest, file
content, exception, or raw state.

## Task Commands

Every command that loads a Task validates its complete stored row through one
source-schema-aware boundary before allow-list projection, compact omission,
dependent-state use, or use as a write basis. Exact SQLite storage classes,
privacy/capacity, enums, and Task cross-field matrices are checked without
coercion or repair. Bounded list/current/next commands validate only their
selected complete-row batch and add no unrelated whole-table rescan.
For source schemas v8-v22, the same boundary performs one bulk relationship
read for only those selected Task IDs. Revision zero requires no Contract row;
a positive `current_contract_revision` must exist as the latest exact INTEGER
revision owned by the same project and Task. Dangling, foreign, nonlatest,
revision-zero-with-row, wrong-storage-class, or ownership-mismatched state uses
the fixed stored-state error. Source schemas v1-v7 perform no Contract read,
and no command queries Contract rows once per Task or audits unselected Task
history.
Malformed or undecodable SQLite TEXT and other non-busy Task fetch/decode
faults use the same fixed error; genuine busy/locked state remains
`database_busy`. Doctor, Viewer, setup, and recovery validate every Task row,
including stored project ownership, as one whole batch.

Any current schema-v22 stored Task fault returns exit 2, code
`project_state_unreadable`, and message
`project state could not be read safely`. The command keeps its existing empty
data shape and emits no warning, partial Task projection, rejected content, or
write. Valid rows and outputs are unchanged.

### `task add`

Register one explicit task:

```powershell
python scripts/taskgov.py task add --repo <target-project> --title "Update docs" --kind optional --priority normal --json
```

Options are `--title`, `--description`, `--kind`, `--lane`, `--order`,
`--priority`, `--status`, `--blocked-reason`, `--review-tier`,
`--verification`, `--tags`, and the Contract group
`--contract-scope`, `--contract-acceptance`, `--contract-constraints`,
`--contract-authority-ref`, and `--contract-change-reason`.

Kinds are `sequential|optional`; priorities are
`low|normal|high|urgent`; review tiers are `0|1|2`. An initial Contract
requires both scope and acceptance and never gets inferred from missing input.
Success data contains `task` and `event`, plus
`contract_write={"recorded":true,"revision":1}` when a Contract was recorded.

Initial `done` returns `initial_done_forbidden`; specifically,
`task add --status done` never stores a task or event. Initial `paused` returns
`initial_paused_forbidden`. Initial `blocked` requires `--blocked-reason`.
Sequential adds preserve the same predecessor rule used for selection and
transitions.

At schema v18 or later, explicit public `task add --verification` and
`task edit --verification` values are each capped at 1,000 characters, with the
existing privacy check before length. Durable/read and internal-derived paths
accept and preserve exact valid verification text through 1,000 characters.
Metadata, Contract, target, review, lifecycle, completion, reopen, setup,
backup, recovery, and projection paths continue to use the source-schema-aware
stored validator rather than caller-input validation; a stored value over its
source-schema limit fails closed. Explicit 1,001-character caller input is
rejected without a write.

### `task list`

Return compact filtered rows:

```powershell
python scripts/taskgov.py task list --repo <target-project> --status ready --limit 20 --json
```

Filters are `--status`, `--kind`, `--lane`, `--priority`, `--tag`,
`--limit`, and `--include-done`. Data keys are `tasks`, `count`, and `limit`.

### `task next`

Return ready candidates:

```powershell
python scripts/taskgov.py task next --repo <target-project> --limit 5 --compact --json
```

Filters are `--kind`, `--lane`, `--priority`, and `--limit` (default 5).
Only ready optional tasks and ready sequential tasks whose earlier same-lane
predecessors are done/cancelled are actionable.

Default data keys are `tasks`, `count`, `limit`, and `selection_rules`.
Compact data keys are exactly `tasks`, `total_matching`, `returned_count`,
`limit`, and `truncated`. A compact task has only `task_id`, `title`, `kind`,
`lane`, `lane_order`, `priority`, `review_tier`, `tags`, and
`suggested_next_action`.

Complete compact JSON stdout is capped at 16,384 UTF-8 bytes. Truncation keeps
only a complete-row prefix in existing order. `--compact` requires `--json`;
otherwise the command returns `invalid_option_combination`.

Existing paused work adds warning `paused_tasks_present` without changing
candidates, exit status, or data. It is an advisory recall hint only.

### `task current`

Rediscover started or held work:

```powershell
python scripts/taskgov.py task current --repo <target-project> --compact --json
python scripts/taskgov.py task current --repo <target-project> --status paused --json
```

Default statuses are `in_progress`, `review_pending`, `paused`, and `blocked`.
`--status` accepts one of those values. `--limit` defaults to 20 and caps at
100.

Default data keys are `tasks`, `count`, `limit`, and `statuses`; each row
contains the normal task projection, latest event, latest checkpoint, and
deterministic suggested action. Compact data keys are exactly `tasks`,
`total_matching`, `returned_count`, `limit`, `statuses`, and `truncated`.
Compact rows contain only:

```text
task_id, title, status, kind, lane, lane_order, priority, review_tier,
blocked_reason, pause_reason, latest_event, suggested_next_action
```

A compact latest event contains only `event_type`, `summary`, `created_at`,
and `summary_truncated`, with summary capped at 256 UTF-8 bytes. Complete
compact JSON stdout is capped at 24,576 bytes and omits Contract/checkpoint
content. `--compact` requires `--json`. Follow selection with `task show`.

### `task effort`

Read one optional informational observation:

```powershell
python scripts/taskgov.py task effort --repo <target-project> <task-id> --read-only --json
```

The package-local versioned profile is never created or changed by taskgov.
Its only metrics are changed Git files, lines, modules, current Contract
revision count, and recorded source-task handoff count. An absent, disabled, or
invalid profile returns an enabled-false bounded projection and performs no
Git work.

Enabled data contains `task_id`, `enabled`, `profile`, `measurements`,
`thresholds`, `exceeded`, `basis`, `observation`, `coverage`, `attribution`,
`unknown_reasons`, `warning_key`, and `suggested_action`.
For a valid enabled profile, `suggested_action` is `reconcile_scope` exactly
when `exceeded` is nonempty, including when attribution is also unknown;
otherwise it is `continue`. The threshold warning uses the same action, and
one observation emits at most one such warning. Threshold or attribution
results never change status, acceptance, review, completion, or target state.
The command emits no paths, stderr, diffs, raw logs, or Git writes.

### `task show`

Read one task and its bounded current context:

```powershell
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

Data keys are exactly `task`, `events`, `suggested_next_action`,
`review_evidence`, `handoff_summary`, `contract`, `latest_checkpoint`,
`effort_advisory_enabled`, `completion_history`, and `verification_evidence`.
A task-show failure uses both `completion_history=null` and
`verification_evidence=null` in its bounded empty data.

`effort_advisory_enabled` is a deterministic boolean routing field. Invalid
profile content returns `false` plus
`effort_advisory_profile_invalid`; the field performs no Git work.

The task contains typed completion evidence and current review-target fields.
`review_evidence` is bounded to the current target/generation, tier gate,
counts, blocking findings, and recent structured receipts/findings; it omits
raw reviews and private reasoning. `handoff_summary` contains exact
`pending_handoff`, `handed_off`, and `handoff_withdrawn_by_user` counts.

`verification_evidence` includes `current_verification_subject`. It is null
without a capture-version-1 nonempty verification criterion; otherwise it is
the same five-key subject-v1 object used by recent Verification Receipts. For
active/review-pending nonempty verification on a retained capture-version-0
target, its blocking code is `evidence_basis_stale` before receipt-required or
receipt-blocking evaluation. Trimmed-empty verification remains
`required=false,satisfied=true,blocking_code=null` with a null subject.

For a schema-v21 or schema-v22 live target, the existing gate shape has a closed basis matrix.
Marker `0` keeps the M21 Receipt behavior. Marker `2` with a non-current,
pending, or cleanup-only graph is stale and uses `evidence_basis_stale`; any
other exact-current terminal Runner result except the two admitted results uses
`verification_receipt_blocking`. The exact-current closed no-launch
`m21_fallback` delegates to M21. An exact-current qualifying complete-plan
Runner pass is satisfied with no Receipt, so `qualifying_receipt_id` is null
and the Receipt-only counts may all be zero.

`completion_history` has exactly:

```text
total, returned_count, truncated, legacy_history_incomplete, cycles
```

Cycles are a newest-first complete-row prefix with at most 10 rows, 8,192
UTF-8 bytes per row, and 32,768 UTF-8 bytes for the complete five-key
component. Both limits use compact sorted-key UTF-8 JSON, and each candidate is
measured in its final wrapper with the actual counts and flags. The first
non-fitting row stops collection; older rows are not substituted. A cycle has
exactly:

```text
completion_cycle_id, saved_cycle_ordinal, origin, completeness, completed_at,
contract_revision, review_tier, verification_expectation,
verification_attestation, completion_evidence, review_target, gate_basis
```

Nested keys are exact: `completion_evidence` contains `kind`, `revision`,
`reason`, `external_revision_approved`, `completion_commit_required`, and
`completion_commit_hash`; `review_target` contains `kind`, `value`,
`base_revision`, and `generation`; `gate_basis` contains `version`, `kind`,
`required_independent_passes`, `qualifying_independent_passes`,
`changes_requested`, `open_high`, `open_medium`, `fresh_review_required`, and
`qualifying_receipt_ids`.

Gate-basis version 0 emits null counts and `qualifying_receipt_ids=[]`.
Version 1 emits integer counts and one or two slot-ordered receipt-ID strings.
`verification_attestation` is only `true` or `null`. Internal event links,
review bodies, and raw verification content never appear. Saved cycles are
audit-only and never satisfy the current verification, review, or completion
gate. Text output reports only bounded counts and the latest cycle's
non-content fields.

Stored public completion-evidence and review-target text is strictly
privacy-revalidated before projection. Completion history has no M19.7
compatibility exception; rejected or corrupt stored text returns
`completion_history_inconsistent` without exposing the value. `task show` and
Viewer use the same bounded projection.

`verification_evidence` has exactly `expectation`, `contract_revision`,
`source_revision`, `current_verification_subject`, `gate`, `counts`, and
`recent_receipts`. `source_revision`
is null without a target; otherwise it contains exactly `kind`, `value`,
nullable `base_revision`, and positive `generation`. Gate has exactly
`required`, `satisfied`, nullable `blocking_code`, and nullable
`qualifying_receipt_id`. Counts has exactly `receipts_total`,
`receipts_exact_current`, `qualifying_exact_current`, and
`blocking_exact_current`. At most ten newest-first rows use the fixed public
Receipt fields and never expose the internal expectation digest. Text
`task show` is unchanged. Invalid stored Receipt evidence returns the
sanitized `invalid_verification_evidence` failure.

Revision-zero Contract data is:

```json
{
  "revision": 0,
  "scope": "",
  "acceptance": "",
  "constraints": "",
  "authority_ref": "",
  "change_reason": "",
  "created_at": null
}
```

`latest_checkpoint` is `null` or the public checkpoint object below.

### `task checkpoint`

Record one optional typed continuation boundary:

```powershell
python scripts/taskgov.py task checkpoint --repo <target-project> <task-id> --summary "Completed slice" --next-action "Run verification" --unresolved-risk "Review remains" --json
```

`--summary` and `--next-action` are required. `--unresolved-risk` may repeat at
most eight times. UTF-8 limits are 1,024 bytes for summary, 1,024 for next
action, 512 per risk, 4,096 for all risks, and 6,144 for the complete caller
payload.

Data keys are exactly `checkpoint`, `created`, `replayed`, and `event`.
Checkpoint keys are exactly:

```text
checkpoint_id, task_id, contract_revision, summary, next_action,
unresolved_risks, created_at
```

A new append returns `created=true`, `replayed=false`, and an event containing
only `task_event_id`, `event_type="checkpoint_recorded"`, and `created_at`.
Exact replay of the latest checkpoint for the same Contract revision returns
`created=false`, `replayed=true`, `event=null`, and writes nothing.

Checkpoint use is never automatic or required. It does not change task status,
selection, gates, or `tasks.updated_at`. Done tasks remain immutable.
Only an already-stored M19.7 checkpoint summary may use the bounded legacy
numeric `dispatch_authorization` JSON reader. It returns the original summary
unchanged and authorizes no write or external operation. New checkpoint input
and every other checkpoint field use strict normal validation.

<a id="task-edit"></a>

### `task edit`

Update task state or metadata:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for user decision" --json
```

Editable arguments are:

```text
--title --description --kind --lane --order --priority --status
--blocked-reason --pause-reason --review-tier --verification --tags
--add-note --reopen-reason --review-tier-change-reason
--completion-commit-hash --completion-evidence-kind --completion-revision
--completion-evidence-reason --external-revision-approved
--commit-not-required --verification-complete --review-complete
--contract-scope --contract-acceptance --contract-constraints
--contract-authority-ref --contract-change-reason
--runner-plan-action replace|rebind|detach|disable
```

Success data contains `task`, `changed_fields`, and `event`, plus
`contract_write` for Contract operations. An action-bearing success also adds
exactly `runner_plan_update={"action":<action>,"status":<status>}`, where status
is `updated|unchanged|unconfirmed`. Without an action, JSON, text, warnings, and
errors retain the existing shape.

`--runner-plan-action` is an explicit opt-in to author only the canonical
ignored physical package file `config/verification-runner.json`; it adds no
command leaf and never launches the Runner or sets a review target. The actions
are closed:

| Action | Standard input | Plan effect |
|---|---|---|
| `replace` | one document | Upsert the addressed Task entry from a strict `RunnerPlanDraftV1`; this is the only initial-set action. |
| `rebind` | not read | Require one addressed entry, preserve its steps and position, and bind it to the exact current or future Task basis. |
| `detach` | not read | Remove every addressed Task entry while preserving all unrelated entries and order. |
| `disable` | not read | Set only global `trusted_local=false`; preserve Plan ID, entries, and order. |

The `replace` stdin document contains exactly `version=1` and `steps`, is capped
at 65,536 UTF-8 bytes by one read of at most 65,537 bytes, and contains one
through 16 exact existing StepV1 objects. Task ID, Contract revision,
verification digests, criterion digest, and `coverage=full` are derived by the
tool. No action discovers commands, infers coverage, changes setup, or
re-enables a disabled Plan.

An action may be Plan-only or accompany one actual Contract-revision or
verification-expectation change. A Plan-only success returns the current Task,
empty `changed_fields`, null `event`, no Task/Contract write, and no maintenance.
Other metadata may accompany an action only with an actual basis change.
Done, completion-evidence, reopen, verification-complete, and review-complete
modes are incompatible with every Plan action. When an enabled Plan has one
exact-current addressed entry, an actionless edit that would change its basis
fails with `runner_plan_action_required`; an absent, disabled, unreadable,
malformed, ambiguous, stale, or no-entry Plan does not block the ordinary Task
edit.

For a combined edit, the Task transaction commits and closes before Plan source
confirmation or publication. A later Plan failure never rolls the Task back:
the command returns `ok=true`, `status=unconfirmed`, and the first warning is
exactly `task_applied_runner_plan_unconfirmed` with message `Task update
completed but Runner Plan disposition is unconfirmed; apply an explicit Plan
action before relying on Runner execution`. Existing maintenance warnings
follow it. The caller must complete one explicit Plan-only repair before relying
on Runner execution. Config-only and pre-commit failures remain ordinary failed
Task-edit envelopes with no maintenance. Text success appends exactly `Runner
Plan: <action> <status>` after existing Task/Contract lines.

The additional fixed errors are `runner_plan_action_required` and
`runner_plan_entry_required` at exit 1; unsafe/malformed/ambiguous Plan source
errors and config-only `runner_plan_changed|runner_plan_update_failed` use exit
2. Draft privacy rejection remains `privacy_rejected` at exit 1. Error output
never contains draft bytes, Plan bytes, argv, paths, or publisher detail.

Task Contract activation is allowed only on an exact revision-zero
`ready|blocked -> in_progress` transition. Later semantic revisions are
Contract-only, require explicit later authority and a reason, invalidate
current completion/review eligibility, and use immutable successive revisions.
Canonically unchanged input is a write-free replay.
Omitted later constraints retain the byte-identical, already-validated prior
value, including bounded M19.7 legacy lineage; explicit constraints use strict
normal validation. Carry-forward does not accept caller-supplied legacy
vocabulary or grant authority.

Only `in_progress|review_pending -> paused` is valid and requires
`--pause-reason`. Resume explicitly to `in_progress`. Sequential transitions
to active, review-pending, or done use the same predecessor rule as
`task next`.

Lower a review tier only before any target has been set and provide
`--review-tier-change-reason`. A done task rejects every write except an
isolated `--status in_progress --reopen-reason <summary>` transition. Reopen
preserves saved completion cycles as audit history, clears current
completion/review eligibility, and requires fresh gates.

The existing done transition remains accepted through `task edit` and enforces
the same validator as thin `task complete`. Prefer the thin command for normal
completion.

### `task complete`

Optionally check the proposed completion without writing:

```powershell
python scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --completion-evidence-kind git_commit --completion-revision <hash> --check --read-only --json
```

Perform completion:

```powershell
python scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --completion-evidence-kind git_commit --completion-revision <hash> --json
```

The thin command accepts only task ID, `--verification-complete`,
`--review-complete`, and one evidence form using
`--completion-evidence-kind`, `--completion-revision`,
`--completion-evidence-reason`, `--external-revision-approved`, or
`--commit-not-required`. Supported evidence kinds are `git_commit`,
`external_revision`, and `commit_not_required`.

Check data keys are exactly:

```text
task_id, ready, status, blocking_codes, contract_revision,
review_target_generation, completion_evidence_kind, suggested_action
```

Complete check JSON is capped at 8,192 UTF-8 bytes. `blocking_codes` is empty
or contains the first write-path blocking code. A changed basis during Git
observation returns `completion_check_stale`. A successful check stores no
token and never authorizes the later write.

Write success emits `command=task.complete` and data keys `task`,
`changed_fields`, and `event`. Both check and write require a satisfying current
verification basis: trimmed-empty verification on marker `0`, an exact-current
`pass/full` Verification Receipt for nonempty verification on marker `0` or the
exact closed no-launch `m21_fallback`, or an exact-current qualifying
complete-plan Runner pass with no Verification Receipt. A pending, stale, or
cleanup-only Runner basis fails `evidence_basis_stale`; every other
exact-current structurally valid terminal Runner result fails
`verification_receipt_blocking`.
They also require a current matching review target, qualifying review receipts,
no current changes-requested receipt, no unresolved high/medium finding, valid
typed completion evidence, sequential readiness, and exact Git/snapshot binding
when applicable.

Git evidence resolves to a canonical full commit ID. External evidence requires
an explicit reason and approval. `commit_not_required` requires a matching
`diff_fingerprint` target. taskgov does not create commits or mutate Git.

## Verification Receipt

After setting the exact review target, record one caller-attested aggregate
result for a Task with a verification expectation that is nonempty after
trimming only after running the governed verification outside taskgov on marker
`0` or the exact-current closed no-launch `m21_fallback`:

```powershell
python scripts/taskgov.py verification receipt add --repo <target-project> <task-id> --result pass --duration-ms <milliseconds> --scope-coverage full --expected-target-generation <generation> --json
```

The four options are required. `result` is `pass`, `fail`, or `timeout`;
`scope-coverage` is `full` or `partial`; duration is a nonnegative signed-
64-bit millisecond value; and expected generation is the positive generation
returned by target set. `--command-label` is not accepted and no caller subject
option replaces it. Taskgov derives the verification subject from the locked
capture-version-1 target's authority snapshot and whole-field verification
criterion, and copies the locked Contract, expectation digest, and complete
target tuple. It owns ID and timestamp.

Receipt recording is allowed only for `in_progress` or `review_pending`,
requires verification that is nonempty after trimming and a current marker-`0`
target or exact-current closed no-launch `m21_fallback`, writes no Task event or
timestamp, and permits one immutable row per target generation. A marker-`2`
qualifying Runner pass, pending basis, stale basis, cleanup-only basis, or any
other structurally valid terminal result rejects Receipt add with
`evidence_basis_stale`. A retry after `fail`, `timeout`, or `partial` requires
an explicitly fresh target.
Taskgov does not execute a command for this M21 branch, infer coverage,
authenticate the external runner, or retain a command body, arguments, exit
code, stdout/stderr, log, exception, environment, or result file.

A migrated capture-version-0 target is read-only lineage. Receipt add fails
`evidence_basis_stale` with `current evidence basis must be captured again`;
setting a fresh target creates capture version 1 and restores the write. The
old target is never upgraded in place.

A semantic `task edit --verification` after targeting clears target and old
completion evidence and advances generation. Without `--status`, a
review-pending Task returns to in-progress. Explicit in-progress, pause, block,
or cancellation follows normal transition rules; explicit review-pending/done
is rejected until a fresh target exists. Do not combine that semantic edit
with completion-evidence options; the command fails
`completion_evidence_conflict` rather than discarding them.

Success data is exactly `receipt`, whose fields are exactly:

```text
verification_receipt_id, project_id, task_id, contract_revision,
verification_subject, result, duration_ms, scope_coverage, source_revision,
created_at
```

`verification_subject` has exactly:

```text
basis_version, kind, authority_snapshot_id, verification_criterion_id,
legacy_caller_label
```

A native v1 Receipt uses `basis_version=1`, kind
`task_verification_criterion`, both non-null IDs, and a null legacy label. A
migrated pre-v18 Receipt uses `basis_version=0`, kind `legacy_caller_label`,
both IDs null, and the preserved label. Neither form authenticates caller
identity. `task show` recent rows use the same versioned union.

Success text is exactly:

```text
Verification receipt recorded: <verification_receipt_id>
Result: <result>  Coverage: <scope_coverage>
Source: <kind>/generation <generation>
```

The write is backup-eligible but does not advance the Evidence or Viewer
source generation. Because every changed mutation may retry already-due
maintenance, a due Evidence projection may still publish before the due
backup. `--read-only` and every failed call perform no business or maintenance
write. There is no Receipt list, show, import, export, Runner command, or Viewer
panel.

## Local Handoff Commands

### `handoff record`

Record one sanitized out-of-scope discovery:

```powershell
python scripts/taskgov.py handoff record --repo <target-project> <source-task-id> --summary "Concise discovery" --rationale "Outside acceptance" --json
```

`--summary` is required and capped at 1,000 characters. `--rationale` is
optional and capped at 1,000. Optional `--occurrence-id` is capped at 200 and
must already come from a stable user/deterministic source.

Data keys are `handoff` and `local_record`. `local_record` contains exactly
`durable`, `created`, `replayed`, and `handoff_id`. Exact canonical replay
returns the same row with `created=false`, `replayed=true`, and no update.

The row is local `pending_handoff` only. It captures the source task and
current Contract revision but does not change task state, acceptance,
selection, events, timestamps, or completion. The command performs no Issue
adapter detection, delivery, semantic triage, or priority assignment.

Local busy persistence receives at most one fresh-transaction retry. Final
failure returns `handoff_not_persisted` with `handoff=null` and a
non-durable `local_record`.

### `handoff list`

List bounded records:

```powershell
python scripts/taskgov.py handoff list --repo <target-project> --json
python scripts/taskgov.py handoff list --repo <target-project> --state handed_off --state handoff_withdrawn_by_user --json
```

Options are repeatable `--state`, `--source-task-id`, and `--limit` (default
20, maximum 100). Default state is `pending_handoff`; rows sort oldest first.
Data keys are `handoffs`, `count`, exact `total_matching`, `limit`, and
`states`. Paging is not implemented.

### `handoff show`

Show one sanitized full record:

```powershell
python scripts/taskgov.py handoff show --repo <target-project> <handoff-id> --json
```

Data contains only `handoff`. Stored content is privacy-revalidated before
emission.

### `handoff withdraw`

Withdraw an undelivered pending row only on explicit user direction:

```powershell
python scripts/taskgov.py handoff withdraw --repo <target-project> <handoff-id> --reason "Handled outside Task Skill" --json
```

Reason is required and capped at 1,000 characters. A terminal, claimed, or
attempted row returns `handoff_not_withdrawable`. Withdrawal changes no source
task or task event.

## Review Commands

### `review prepare`

Prepare one bounded read-only reviewer packet for the current target:

```powershell
python scripts/taskgov.py review prepare --repo <target-project> <task-id> --read-only --json
```

Supported target kinds are `git_snapshot`, `git_commit`, `diff_fingerprint`,
and `external_revision`. The command takes no caller focus, reviewer, import,
receipt-file, or output-destination argument. It launches no reviewer, imports
no result, writes no packet, and changes neither local task state nor target
Git.

Success data keys are exactly:

```text
task, contract, review_target, changed_paths_available, changed_paths,
changed_paths_total, changed_paths_truncated, review_focus, required_output,
receipt_command
```

The packet reads one coherent governance basis, closes SQLite for bounded
shell-free Git observation, then performs one short basis revalidation.
Changed paths are strict relative UTF-8 project paths, sorted bytewise, and
bounded to 100 rows, 240 UTF-8 bytes per row, and 16,384 aggregate path bytes.
The complete text or JSON stdout is capped at 32,768 bytes. Git observation is
capped at ten subprocesses.

The fixed `required_output` requests the verdict, severity-ordered findings,
exact file references, remaining risks, recommended changes, and the scalar and
collection provenance values required for the Receipt kind. `receipt_command`
uses the existing `review receipt add` leaf and includes provenance option
placeholders; it adds no command, import, reviewer launch, or model call.

`review_focus` contains the four common fixed rows plus exactly one fixed
target-kind inspection row. `git_snapshot` binds inspection to the stage-0
index and stored base while excluding unstaged/untracked content. `git_commit`
binds it to the canonical commit and first-parent/empty-tree comparison.
`diff_fingerprint` and `external_revision` prohibit PASS unless exact material
is supplied with evidence binding it to the stored value. Selecting the row
adds no Git subprocess or caller/model choice.

Missing, changed, unsafe-path, and oversized cases return no partial packet:

- `review_target_missing`: `review target is required before preparing a review packet`
- `review_packet_stale`: `review context changed while preparing the packet`
- `review_packet_path_unsafe`: `review packet contains an unsafe project path`
- `review_packet_too_large`: `review packet exceeds the supported size`

### Review Evidence

Set or replace the current target:

```powershell
python scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_commit --revision <revision> --json
python scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_snapshot --json
```

`git_commit`, `diff_fingerprint`, and `external_revision` require
`--revision`; `git_snapshot` rejects it. Every set advances the target
generation. Git commits are resolved read-only and stored canonically. A diff
fingerprint is `sha256:` plus 64 lowercase hexadecimal characters.

At schema v21 or v22, this same target-set operation may use the explicitly opted-in
trusted-local Runner route. It adds no argument or public Runner command. JSON
success data is exactly the prior `task`, `changed_fields`, and `event` plus
`verification_route` and `blocking_code`; text output is unchanged, and failure
data remains exactly the prior three-key empty shape. `verification_route` is
exactly `not_required`, `receipt_required`, `runner_pass`, or `blocked`.
`blocking_code` is null for the first three and is
`verification_receipt_blocking` for a stored nonqualifying Runner terminal.
The route describes the target and Runner result committed by this invocation;
the CLI performs no post-commit gate reread or Runner reselection.

A target retained by schema-v18 migration with `capture_version=0` is read-only
lineage. Verification Receipt add, Review Receipt add, Review Finding add, and
both completion paths fail `evidence_basis_stale` with
`current evidence basis must be captured again`. Set a fresh target to create
capture version 1; no operation upgrades the old target in place. `review
prepare` and resolving an existing Finding remain allowed because they create
no new evidence source.

For `git_snapshot`, stage exactly intended files first. Capture observes
canonical HEAD and only the stage-0 index; unstaged and untracked files are
excluded. Completion requires a single-parent commit whose parent equals the
captured base and whose tree matches the fingerprint. A changed candidate
needs a new target and fresh receipts.

Add one sanitized current-generation receipt:

```powershell
python scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <stable-reviewer-key> --kind independent --verdict pass --summary "No blocking findings" --reviewer-class human --model-state not_applicable --skill-state not_applicable --context-relation external_context --review-profile general --review-lens correctness --review-method review_packet_inspection --json
```

Kinds are `independent`, `self_review_fallback`, and `not_required`; verdicts
are `pass`, `changes_requested`, and `not_required`. One reviewer key may
record one receipt per target generation. Tier 2 normally requires two
distinct independent PASS receipts. Any current-generation
`changes_requested` receipt blocks completion. `--user-approved` is accepted
only where the governing review-tier fallback contract requires explicit user
approval; the normal independent path omits it.

For `independent` and `self_review_fallback`, the following provenance options
apply and no value is defaulted or inferred:

- conditionally required: `--reviewer-class`, `--model-state`,
  `--skill-state`, and `--context-relation`;
- optional only when the selected declared states require them:
  `--declared-model-id`, `--declared-skill-id`, and
  `--declared-skill-version`; and
- repeatable bounded sets: `--review-profile` (at most 4), `--review-lens` (at
  most 8), and `--review-method` (at most 8).

Every provenance option is forbidden for `not_required`. Duplicates are
invalid; empty profile/lens/method sets are valid; stored and public arrays use
the fixed enum order regardless of option order. Invalid type, enum, bound,
grammar, duplicate, or matrix combinations return
`invalid_review_evidence`; privacy rejection retains precedence.

The scalar enums are exactly:

```text
reviewer_class   human llm deterministic_tool hybrid unknown
model_state      declared not_applicable unknown
skill_state      declared not_applicable not_used unknown
context_relation same_context forked_context fresh_context external_context
                 not_applicable unknown
```

The repeatable enum orders are exactly:

```text
review_profiles general authority_contract implementation verification
                migration_compatibility privacy_safety release_acceptance
review_lenses   correctness contract_compliance state_completion_integrity
                privacy target_safety verification_regression
                migration_compatibility maintainability accessibility
                performance release_integrity
method_codes    review_packet_inspection authority_cross_check diff_inspection
                source_inspection test_inspection
                verification_evidence_inspection artifact_inspection
                runtime_observation deterministic_rule_check
```

Human and deterministic-tool cases require model and Skill states
`not_applicable` and no declared IDs. LLM and hybrid cases require model
`declared` with an ID or `unknown` without one; their Skill state is `declared`
with ID and version, `not_used` without either, or `unknown` without either.
Reviewer class `unknown` requires both states `unknown` and no IDs. Declared
model/Skill IDs are 1-128 ASCII bytes matching
`[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}`; declared Skill version is 1-64 ASCII
bytes matching `[A-Za-z0-9][A-Za-z0-9._+-]{0,63}`.

Every public Review Receipt adds exactly one `review_provenance` value. A new
independent/self-review Receipt projects v1 with exactly:

```text
review_provenance_id, provenance_version, reviewer_class, model_state,
declared_model_id, skill_state, declared_skill_id, declared_skill_version,
review_profiles, review_lenses, context_relation, method_codes,
assurance_class, producer_class, producer_version, digest
```

Native v1 assurance/producer/version is exactly
`bound_attestation/trusted_caller/1`. A migrated pre-v18 independent/self-review
Receipt projects the same keys as v0 with null ID/digest and null semantic
fields/collections, plus `legacy_unknown/legacy_migration/1`; v0 records
absence and does not infer explicit `unknown`. A `not_required` Receipt projects
null and owns no provenance row. Provenance does not change the parent
Receipt's existing `bound_attestation/trusted_caller/1` assertion and never
proves identity, actual model/Skill execution, competence, independence,
diversity, quality, or truth.

The independent reviewer returns the verdict and findings. The trusted
parent/orchestrator records their concise sanitized receipt/finding rows as an
attestation. Taskgov deterministically evaluates qualifying PASS receipts and
changes-requested receipts only for the current review target and generation.
Any unresolved high or medium finding from any recorded generation of that
Task continues to block the gate. Distinct reviewer keys prove distinct stored
strings only; they do not prove distinct people, LLMs, machines, independent
processes, independence, authenticated provenance, or summary truth.

Record and resolve structured findings:

```powershell
python scripts/taskgov.py review finding add --repo <target-project> <task-id> --receipt-id <receipt-id> --severity high --summary "Concise finding" --json
python scripts/taskgov.py review finding resolve --repo <target-project> <finding-id> --resolution "Concise resolution" --json
```

Severity is `high`, `medium`, or `low`. Open high/medium findings block
completion. After resolving a blocking finding and changing material, set a
new target and obtain fresh qualifying receipts.

## Internal Continuity Boundary

Successful `setup` enables bounded local continuity once. There is no disable
surface. After eligible successful business writes, the business transaction
commits and closes before same-process Evidence projection, Viewer refresh,
and any due managed copy, in that order. Every changed mutation may retry due
Evidence work, while only completion-cycle insertion advances its source
generation. Each projection has at most one follow-up and backup one attempt; taskgov never uses a
daemon, thread, timer, detached process, queue, scheduler, service, or network.

Evidence JSON paths are fixed at `state/current/evidence/index.json` and
`state/current/evidence/bundles/<completion-evidence-bundle-id>.json`, with the lock at
`state/current/evidence/taskgov-evidence.lock`; callers cannot select them.
A schema-v20-through-v22 index uses `format_version=2` and adds
`bundle_format_version`: null for `legacy_unknown`, 1 for a preserved Bundle
v1, and 2 for a native Bundle v2. New v22 Bundles use `format_version=2`, a
closed verification-basis union of `caller_attestation`, `not_required`, or
`runner_observation`, and a matching nullable `runner_observation` field. The
two M21 arms keep that field null; the Runner arm contains only the qualifying
sanitized observation. Retained source-19/20/21 Bundle bytes and digests are unchanged. The index
is published last, SQLite remains canonical, and JSON is never imported or
displayed by the Viewer.

The optional physical `config/viewer.json` is browser-presentation policy, not
a CLI or normal-loop choice. Taskgov never creates it; absence means the
generated page owns no refresh timer. A valid exact schema-1
`visibility-refresh-v1` profile may embed a 5-3,600 second interval on the next
Viewer publication. Invalid policy keeps routine task success and the last-good
Viewer and uses the existing `viewer_refresh_failed` warning; actual setup uses
`setup_incomplete`. `setup --read-only` remains successful and no-write,
reports `viewer_status="repair_required"`, and includes `viewer_publish` in
`planned_writes`. Doctor does not inspect this policy.

Only the resulting visible `file://` page uses the browser timer. Immediately
before its automatic reload, it may replace the current History state with one
fixed, at-most-4,096-byte, five-minute envelope containing non-search filters,
selected Task, fixed-control focus, and document scroll. Owned state is
cleared before restoration; an unrelated `history.state` payload, URL, and
history length are unchanged. Five minutes is the restore acceptance limit,
not a physical-erasure guarantee; browser-managed state may survive session
restore but an owned envelope is consumed before validation. No cookie, Web
Storage, network, snapshot field, database field,
command, or normal-loop decision is added.

These operations add no Skill command or LLM judgment. Their artifacts and
paths are absent from public command output. Snapshot v4 reads source schemas
5 through 22. Sources 5-14 receive an empty, legacy-incomplete completion
history; sources 15-22 use stored cycles. Every Task receives the same bounded
five-key projection as `task show` without exposing internal event links,
maintenance data, or checkpoint content.

For sources v18+, Viewer capture validates Review Receipt provenance and
verification-subject/capture bindings; v19-v22 also validate the Bundle
discriminator, and v20-v22 additionally validate the source-appropriate
Bundle-v2 verification basis and Runner graph. It discards those fields from snapshot v4. It
adds no provenance UI, filter, panel, snapshot key, or normal-loop behavior.

Viewer capture validates the complete source-aware Task batch before rendering
or replacement, including the source-v8+ Contract-pointer relationship. A
stored Task fault therefore publishes no partial snapshot
and preserves the last-good Viewer; routine post-commit maintenance returns the
successful business result with only `viewer_refresh_failed`. Setup preflight
instead fails no-write with `project_state_unreadable`; a failure confined to
setup's later Viewer stage remains `setup_incomplete`.

If post-commit maintenance cannot complete, the primary business mutation
remains successful and only a bounded warning is appended:

- `evidence_projection_deferred`: `Evidence projection refresh was deferred; task result is unchanged`
- `evidence_projection_failed`: `Evidence projection refresh did not complete; task result is unchanged`
- `viewer_refresh_deferred`: `Viewer refresh was deferred; task result is unchanged`
- `viewer_refresh_failed`: `Viewer refresh did not complete; task result is unchanged`
- `backup_deferred`: `managed backup was deferred; task result is unchanged`
- `backup_failed`: `managed backup did not complete; task result is unchanged`

The operation remains due for a later eligible successful mutation. The Skill
does not ask the LLM to retry, schedule, or stop for these warnings.

## Errors And Privacy

Removed or unknown root commands return exit 2 and `invalid_command` with
message `command is not available`. Any public `--db` occurrence returns exit 2
and `invalid_option` with message `option is not available`. Other unknown
nested commands and unsupported or malformed options return exit 1 and
`invalid_argument` with message `arguments are invalid`. With lexical `--json`,
these parser failures use the normal bounded envelope, resolve no project, and
perform no read or write.

Mutually incompatible supported options return
`invalid_option_combination` with a bounded sanitized message.
Specifically, `setup --read-only --confirm-relocation <token>` fails before
project/state resolution with exit 1,
`invalid_option_combination`, and message
`--confirm-relocation cannot be used with --read-only`.

Relocation setup failures use exit 2 and these fixed sanitized messages:

| Code | Message |
|---|---|
| `project_relocation_required` | `project state is bound to a different project location; run setup --read-only` |
| `relocation_token_invalid` | `relocation confirmation is invalid` |
| `relocation_token_expired` | `relocation confirmation has expired; run setup --read-only again` |
| `relocation_token_stale` | `project relocation state changed; run setup --read-only again` |
| `relocation_token_used` | `relocation confirmation has already been used` |
| `relocation_not_required` | `project relocation is not required` |

Important setup/diagnostic errors include:

- `unsupported_python`
- `unsupported_install_layout`
- `project_scope_required`
- `invalid_project_root`
- `state_path_invalid`
- `state_ignore_required`
- `invalid_backup_policy`
- `setup_restore_failed`
- `setup_initialization_failed`
- `setup_backup_failed`
- `setup_migration_failed`
- `setup_incomplete`
- `project_state_unreadable`
- `project_mismatch`
- `project_relocation_required`
- `relocation_token_invalid`
- `relocation_token_expired`
- `relocation_token_stale`
- `relocation_token_used`
- `relocation_not_required`
- `schema_too_new`
- `unsupported_journal_mode`
- `database_busy`

Important task/review/handoff errors include:

- `invalid_argument`, `invalid_status`, `invalid_status_transition`
- `initial_done_forbidden`, `initial_paused_forbidden`
- `blocked_reason_required`, `pause_reason_required`
- `sequential_predecessor_incomplete`, `done_task_requires_reopen`
- `completion_history_inconsistent`
- `contract_activation_forbidden`, `contract_authority_required`,
  `contract_write_conflict`
- `verification_required`, `review_required`, `commit_required`
- `verification_expectation_required`, `verification_basis_stale`,
  `verification_receipt_required`, `verification_receipt_blocking`,
  `verification_receipt_already_recorded`, `invalid_verification_evidence`
- `completion_evidence_conflict`,
  `evidence_basis_stale`, `evidence_ledger_inconsistent`,
  `evidence_bundle_too_large`,
  `external_revision_approval_required`,
  `git_commit_not_found_or_ambiguous`
- `review_target_required`, `review_target_mismatch`,
  `review_receipts_insufficient`, `review_changes_requested`,
  `review_finding_unresolved`
- `handoff_not_persisted`, `handoff_not_withdrawable`,
  `handoff_occurrence_invalid`
- `privacy_rejected`, `not_found`, `internal_error`

`completion_history_inconsistent` uses exit 2 and the fixed sanitized message
`stored completion history is inconsistent`.
`invalid_verification_evidence` likewise fails closed with exit 2 and
`stored verification evidence is inconsistent` when stored Receipt structure
or binding is malformed.
`project_state_unreadable` uses exit 2 and fixed message
`project state could not be read safely` for any invalid current stored Task
row or selected Task's current Contract relationship. It never downgrades to
`privacy_rejected`/`invalid_argument`, returns a
partial projection, or exposes the offending value. Managed setup recovery
retains the sole narrower exception: only stored Task `verification`
privacy/capacity failure is candidate-local; all other Task faults remain
whole-set fatal, including every Contract-pointer relationship fault.

Privacy validation is deny-by-default for stored free-form input. Never submit
secrets, tokens, authorization headers, raw stdout/stderr, stack traces,
environment dumps, private prompts/reasoning, full chats/reviews, or large raw
diffs. If handoff input is rejected, never repeat, quote, log, store, or
forward the rejected raw content. Make at most one new attempt using a newly
written concise sanitized abstraction.

Normal/new input rejects the equality form
`dispatch_authorization=<value>` and JSON key
`"dispatch_authorization":<value>`, including numeric values. Use
`operation_sequence=<positive canonical integer>` for future
external-operation correlation or idempotency evidence. It is not authority.
The sole legacy reader is confined to already-stored M19.7 Contract
constraints and checkpoint summaries, preserves their original text, performs
no write, and leaves compound credentials or tokens rejected.
