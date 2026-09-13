# CLI Contracts

Use the contents to read the section for the current command, input, or error,
including its directly linked requirements. Unrelated operations and audit-only
detail are not prerequisites for normal Task work.

## Contents

- [Invocation And Public Inventory](#invocation-and-public-inventory)
- [Envelope And Read/Write Boundary](#envelope-and-readwrite-boundary)
- [Execution Access](#execution-access)
- [`setup`](#setup)
- [`doctor`](#doctor)
- [Task Commands](#task-commands)
  - [`task add`](#task-add)
  - [`task list`](#task-list)
  - [`task next`](#task-next)
  - [`task current`](#task-current)
  - [`task context`](#task-context)
  - [`task effort`](#task-effort)
  - [`task show`](#task-show)
    - [Task Read Conditions](#task-read-conditions)
  - [Historical investigation (`--audit`)](#task-audit-detail)
  - [`task checkpoint`](#task-checkpoint)
  - [`task edit`](#task-edit)
  - [Runner Plan actions](#runner-plan-actions)
    - [Runner Plan example and OS limits](#runner-plan-example-and-os-limits)
  - [`task complete`](#task-complete)
- [Verification Receipt](#verification-receipt)
- [Local Handoff Commands](#local-handoff-commands)
- [Review Commands](#review-commands)
  - [`review prepare`](#review-prepare)
  - [Review Evidence](#review-evidence)
    - [Set the exact target](#review-target)
    - [Receipt input and provenance](#review-provenance)
    - [Finding creation and resolution](#finding-resolution)
    - [Structured Finding Resolutions](#structured-finding-resolutions)
  - [Structured Review Results](#structured-review-results)
- [Receipt Output For Integration Or Audit](#receipt-output-for-integration-or-audit)
- [Internal Continuity Boundary](#internal-continuity-boundary)
- [Errors And Privacy](#errors-and-privacy)

## Invocation And Public Inventory

For normal governed-project use, install one physical project-scoped copy.
Unless a section states otherwise, every example below runs from the governed
project root; `<target-project>` is that same root, not the Skill directory.
Replace angle-bracket placeholders before execution:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py <command> [options]
```

Only when the working directory is the installed Skill directory instead, use
the shorter script path and pass the governed project explicitly:

```powershell
python scripts/taskgov.py <command> --repo <target-project> [options]
```

From the target-project root on Linux/macOS, use `python3`, for example:

```sh
python3 .agents/skills/task-governance-tool/scripts/taskgov.py setup --read-only --json
```

Omitting `--repo` means the current directory and never re-roots it to an
enclosing Git worktree. A non-Git directory is valid. Stateful use supports
one physical project-scoped package only; user-wide, symbolic-link, and
Windows junction layouts are unsupported. Python 3.12 or later is required.
Ordinary Task use supports Windows, Linux, and macOS. The explicit trusted-local
Runner supports the same three platforms under their OS-specific limits;
verification without Runner opt-in remains manual. There is no OS-selection command.

The complete public command inventory is exactly these 23 leaves:

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
22. `task context`
23. `review result add`

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

`task context` takes no Task ID. Task-specific commands such as `task show` and
`task edit` take the selected ID, normally
`data.selected.task.task_id` from `task context`. Use returned values, not the
illustrative IDs in JSON examples.

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
CLI `--json` is UTF-8 plus LF, without indentation/separator spaces or
non-ASCII escapes. Use returned IDs as opaque identities; do not reconstruct
them from a project path.

Successful `task edit`, `task complete`, and `review target set` acknowledge
the write without repeating unchanged `description` or `verification` in
`data.task`. A changed field, including a cleared empty string, is returned
when named in `changed_fields`. Keep unchanged prose from the working context;
do not replace it with this acknowledgement or add a routine read. Other
operation-specific fields, errors, warnings, and human text are unchanged.
Registration/context, show/audit, and Review Packet retain their full forms.

Inherently read-only commands are `doctor`, task `list`, `next`, `current`, `context`,
`effort`, and `show`, `task complete --check`, handoff `list` and `show`, and
`review prepare`. `setup --read-only` is a no-write preview.

Write commands other than `setup` require current initialized state. They
never initialize or migrate implicitly. `setup` is the only initializer and
migrator. `--read-only` rejects a write form before a business write.

Contention returns `database_busy`; unsupported WAL state returns
`unsupported_journal_mode`, without raw database or operating-system detail.
Business writes revalidate their current basis and save atomically. Exceptions
with a durable completed prefix are documented under [setup](#setup) and
[Runner Plan actions](#runner-plan-actions). Maintenance warnings never undo a successful
business write; see [Internal Continuity Boundary](#internal-continuity-boundary).

## Execution Access

Use this section for a known host restriction or access failure, not a normal
preflight. Reading Skill explanations needs read access to the package files;
read-only taskgov inspection also reads its governed project and canonical
state. Ordinary authorized Task/evidence updates need write access under the
physical package's canonical `state/`, including SQLite transaction files and
bounded post-commit artifacts. Read access alone does not authorize those writes.

Codex's default workspace-write policy protects an existing `.agents` directory
recursively as read-only, even inside a writable workspace. Thus an installed
package's `state/` can require host approval although the project is writable.
This is a host condition, not taskgov's permission policy or evidence that
every installation is blocked. Use the current host's effective restrictions
and valid existing grants; the Skill itself grants no execution permission.

When a known restriction affects the intended authorized write, use the host's
formal scoped approval mechanism for that operation without first provoking a
known-denied write. Reuse an existing grant only while it covers the same access
and remains valid. Do not request administrator or unrestricted access by
default, add a per-call permission check/question, or change ACLs, sandbox
settings, or state location. If required approval is unavailable or denied,
report the affected operation and required access; do not bypass the restriction.
Continue unrelated authorized work where possible.

An `internal_error` alone does not identify a permission failure. Follow
[the existing error guidance](#errors-and-privacy); do not diagnose its cause
from that code alone. If a write response is lost or its committed outcome is
unknown, inspect actual saved state through the relevant public read before
deciding whether any retry is needed; never blindly resubmit. A successful
write with a [maintenance warning](#internal-continuity-boundary) stays successful.

This guidance adds no write probe, normal-loop doctor, new approval gate, or
new error classification. Setup, installation, Git, external operations and
[Runner Plan publication](#runner-plan-actions) retain their separate explicit
permission boundaries; ordinary Task-write approval does not authorize them.

<a id="setup"></a>

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

When the canonical database is absent, setup automatically prefers eligible
fixed-layout managed recovery, then at most one eligible legacy source. It
does not ask the LLM to choose a backup or path. Same-binding legacy primary or
legacy backup-only state can be recovered; a moved legacy backup-only source
is not a relocation candidate and fails no-write as `project_state_unreadable`.
An existing database is never overwritten, even when unreadable. Invalid,
foreign, linked, unrecognized, or ambiguous artifacts remain unchanged.

Only stored Task-verification privacy/capacity rejection may select an older
eligible same-binding backup. If none remains, setup returns
`setup_restore_failed` with `managed backup could not be restored`, never empty
initialization. Structural recovery faults retain their specific error where
applicable, otherwise `project_state_unreadable`; this includes a journal
beside a missing primary. Changed recovery material or a restore/publication
failure after planning returns `setup_restore_failed`. Do not remove failed
sources or attempt manual database repair as part of this flow.

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
unexpired `data.relocation.confirmation_token` from the preview; its expiry is
`data.relocation.expires_at`. It never infers move/copy/fork semantics or auto-confirms.
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
the durable ordered prefix. Inspect `data.completed_writes` before retrying;
`setup_incomplete` calls for rerunning setup, which recomputes from durable
state rather than repeating an assumed failed stage.

Setup is noninteractive and idempotent. It does not create a second
configuration file, disable continuity after opt-in, contact a network, mutate
Git, or modify target source. For a Git-candidate target, only its single
bounded effective-ignore preflight may inspect Git.

<a id="doctor"></a>

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

Invalid stored Task data or its current Contract relationship makes
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

Invalid stored Task or current Contract data returns exit 2, code
`project_state_unreadable`, and message
`project state could not be read safely`. The command keeps its existing empty
data shape and emits no warning, partial Task projection, rejected content, or
write. Do not treat this as an empty selection or repair stored values manually.

<a id="task-add"></a>

### `task add`

Register one explicit task:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task add --repo <target-project> --title "Update docs" --kind optional --priority normal --json
```

Options are `--title`, `--description`, `--kind`, `--lane`, `--order`,
`--priority`, `--status`, `--blocked-reason`, `--review-tier`,
`--verification`, `--tags`, and the Contract group
`--contract-scope`, `--contract-acceptance`, `--contract-constraints`,
`--contract-authority-ref`, and `--contract-change-reason`.

Kinds are `sequential|optional`; priorities are
`low|normal|high|urgent`; review tiers are `0|1|2`. An initial Contract
requires both scope and acceptance and never gets inferred from missing input.
Success data contains `task`, `event`, and `context_preparation`, plus
`contract_write={"recorded":true,"revision":1}` when a Contract was recorded.
`data.task.task_id` identifies the registered Task; it need not be the selected work.

Initial `done` returns `initial_done_forbidden`; specifically,
`task add --status done` never stores a task or event. Initial `paused` returns
`initial_paused_forbidden`. Initial `blocked` requires `--blocked-reason`.
Sequential adds preserve the same predecessor rule used for selection and
transitions.

Explicit `task add --verification` and `task edit --verification` are capped
at 1,000 characters, with privacy checked before length. A 1,001-character
value is rejected without a write. Omitting `--verification` from `task edit`
preserves the existing value; other edits do not require copying it.

For a finalized multiple-Task set, use `task add --from-stdin --json` once with
this UTF-8 JSON shape (no BOM; at most 262,144 bytes and 1 through 64 items):

```json
{"version":1,"common":{"review_tier":1,"verification":"Focused document checks","contract":{"constraints":"Docs only","authority_ref":"docs/decision.md#approved"}},"tasks":[{"title":"Clarify setup","contract":{"scope":"Setup examples","acceptance":"Examples match setup help"}},{"title":"Clarify diagnosis","contract":{"scope":"Diagnosis examples","acceptance":"Examples are read-only"}}]}
```

Top-level keys are exactly `version`, `common`, and `tasks`. Each item requires
its own `title` and `contract` (null for revision zero, or required `scope` and
`acceptance`, optional `constraints` and `authority_ref`). Common Contract values
may contain only `constraints` and `authority_ref`; they never activate null.
Common/per-item Task fields are `description`, `kind`, `lane`, `lane_order`,
`priority`, `status`, `blocked_reason`, `review_tier`, `verification`, and `tags`.
Each effective review tier must be explicitly supplied; other omitted fields
use single-add defaults. Individual values override common ones, including empty
text. Integer fields are JSON integers (lane_order may be null); other scalars
are strings. All supplied values, even unused common values, retain existing
privacy/bounds checks. No unknown/duplicate keys or automatic scope inference.
Do not combine this mode with individual Task/Contract flags; read-only rejects
it without consuming stdin.

Success data contains `tasks=[{"input_index":0,"task":{},"event":{},"contract_write":{"recorded":true,"revision":1}}]`
and one `context_preparation`,
where Task/event are the existing projections and `contract_write` is absent
for null Contracts. The response maps every input in order; no per-Task follow-up
confirmation is required. Match `data.tasks[].input_index` to the input and use
that item's `task.task_id` for later operations. All entries commit together or all roll back;
handled batch input/storage errors return `tasks=[]`. Parse/read-only errors
retain their ordinary envelope. Correct and resubmit only after confirmed
rollback. A lost response requires inspecting existing Tasks before any retry;
never blindly replay or remove successes. This is not an idempotent importer.
Windows PowerShell producers must send UTF-8 without BOM, not locale-encoded
text. This option creates no input file, adapter, extra normal-loop call, or
authority beyond explicit registration.

For single and batch success, `context_preparation` has exactly `status`,
`context`, and `errors`. `status=ready` carries the complete ordinary
[`task context`](#task-context) data with `errors=[]`; its component warnings
appear once in the outer warnings. Use `context.selected.task.task_id` for
selected work, not the registered ID by assumption. This replaces the immediate
separate context read, not any implementation permission or gate. Batch items
do not duplicate context. The read follows commit with ordinary validation and
selection; it is not atomic with registration or later maintenance.

`status=failed` has `context=null` and sanitized `errors`, with no partial read
or component warnings. Outer `ok=true` and exit zero still mean all registration
results committed. Address the read failure and retry only `task context`; do
not add the Tasks again. Registration failures do not prepare context. A lost
outer response remains uncertain: inspect actual state before any registration
retry, as above. No additional normal-path command or user choice is introduced.

<a id="task-list"></a>

### `task list`

Return compact filtered rows:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task list --repo <target-project> --status ready --limit 20 --json
```

Filters are `--status`, `--kind`, `--lane`, `--priority`, `--tag`,
`--limit`, and `--include-done`. Data keys are `tasks`, `count`, and `limit`.

<a id="task-next"></a>

### `task next`

Return ready candidates:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task next --repo <target-project> --limit 5 --compact --json
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

<a id="task-current"></a>

### `task current`

Rediscover started or held work:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task current --repo <target-project> --compact --json
python .agents/skills/task-governance-tool/scripts/taskgov.py task current --repo <target-project> --status paused --json
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

<a id="task-context"></a>

### `task context`

Use one fixed read for ordinary Task start or resume:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task context --repo <target-project> --json
```

Only common options are accepted; there is no Task ID, filter, display-mode,
or automatic start option. The tool resumes active/review-pending work first,
otherwise selects ready work using its existing order. Selection precedes
display omission, so a Task absent from the compact lists may still be selected.
Use the returned selection; do not reconstruct or re-rank it.

Success data is exactly `selection`, `current`, `next`, and `selected`.
`selection` is `current`, `next`, or `none`. `current` is compact-current data;
`next` is compact-next data only when fallback ran, otherwise null. `selected`
is the complete [normal Task detail](#task-show), or null when no candidate exists.
It includes complete Contract, latest checkpoint, current blockers/gates, and
`effort_advisory_enabled`. Use that detail directly without another
current/next/show call. Held work remains recalled; successful component
warnings are retained once.

For `selection=current|next`, use `data.selected.task.task_id` as `<task-id>` in
the next Task-specific command. `selection=next` does not start the Task; start
it with `task edit <task-id> --status in_progress --json`. No ID is appended to
the `task context` call itself.

Any read failure returns its sanitized error with no partial working context;
it never skips the failure to select another Task. `ok=true` with
`selection=none` is successful absence, distinct from `ok=false`. Text gives
the selection, existing selected-Task detail, held-work recall, and warnings.
No state, evidence, or gate is changed. The public operation reuses the
existing selection and gate rules.

<a id="task-effort"></a>

### `task effort`

Read one optional informational observation. At the verification/review boundary,
the normal flow calls this only when
`task context` returned `data.selected.effort_advisory_enabled=true`; take
`<task-id>` from that response's `data.selected.task.task_id`:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task effort --repo <target-project> <task-id> --read-only --json
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

<a id="task-show"></a>

### `task show`

Read one specified Task's fixed normal working context. Use `<task-id>` from
`data.selected.task.task_id` in `task context`, or a Task row returned by an
explicit `task list/current/next` inspection:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

For explicit historical investigation only, use [the audit mode](#task-audit-detail).
`task context.selected` always uses normal detail.

Use the returned working context directly. These fields have the same meaning
under `task context`'s `data.selected`; no follow-up read is needed:

| Field under `data` | Use |
|---|---|
| `task` | Task ID, status, verification expectation, review tier, current target/generation, and typed completion evidence. |
| `contract` | Complete current scope, acceptance, constraints, authority reference, and revision. Revision `0` means no activated Contract, not a failed read; use the existing [Contract procedure](task_workflow.md#task-contract) when applicable. |
| `latest_checkpoint` | The latest [continuation checkpoint](#task-checkpoint), or null when none exists. |
| `events` | Latest activity and retained notes/transition reasons. Do not discard continuation information merely because it is old, from an earlier generation, or followed by a newer checkpoint. |
| `review_evidence` | Current gate/counts, `current_receipts`, and `current_findings`. |
| `verification_evidence` | Current gate/counts, `current_receipt`, and tool-generated `current_verification_subject`. |
| `handoff_summary` | Counts for `pending_handoff`, `handed_off`, and `handoff_withdrawn_by_user`; these do not expand Task scope. |
| `completion_history` | `total` and `legacy_history_incomplete`, not current completion evidence. |
| `effort_advisory_enabled` | Whether the normal loop uses the optional Effort observation. Invalid configuration returns false with a continuation warning. |
| `suggested_next_action` | The tool's state-based next-action hint, not new authority. |

Use `review_evidence.gate.satisfied` and its required/qualifying independent
pass counts, not the number of recent reviews, to read review readiness.
`current_receipts` match the complete current target and generation.
`current_findings` includes all open Findings, including older generations and
low severity, plus resolved high/medium Findings awaiting fresh review.
Their `blocking_reason` is `unresolved`, `fresh_review_required`, or null when
nonblocking. Keep these distinctions; see [Finding repair](task_workflow.md#repair-findings)
when blocked. A newer generation never clears an unresolved Finding by itself.

Use `verification_evidence.gate.required`, `satisfied`, and `blocking_code`
for verification readiness. `current_receipt` is null or the exact-current
Receipt's ID, result, duration, coverage, and recording time. Its absence or
zero Receipt counts do not imply failure: a qualifying Runner pass needs no
Receipt. Do not reconstruct the gate from internal markers or historical rows.
For a returned blocking code or read failure, use [Task Read Conditions](#task-read-conditions).

#### Task Read Conditions

For `review_target_required`, prepare the exact material and
[set its target](task_workflow.md#set-the-review-target). `evidence_basis_stale`
means the retained basis cannot authorize new evidence or completion; use a
fresh target, never attach an earlier run to it. For
`verification_receipt_required` or `verification_receipt_blocking`, follow the
[Verification Receipt conditions](#verification-receipt), including fresh-target
requirements after failed, timed-out, or partial verification. A blocked Runner
cannot be overridden by a manual Receipt. For new target operations, use their
returned route as specified by the [normal loop](task_workflow.md#bounded-operating-loop).

No read changes evidence or gates. Both normal and audit reads validate hidden
history too; omitting its display is not permission to ignore invalid state.
On `ok=false`, use the sanitized error rather than empty/null data as a Task or
gate result. `project_state_unreadable`, `invalid_verification_evidence`, or
`completion_history_inconsistent` can indicate invalid retained content;
follow [read-failure diagnosis](#errors-and-privacy), not an audit bypass or
another candidate. Neither mode exposes raw reviews or private reasoning.

<a id="task-audit-detail"></a>

### Historical Investigation (`--audit`)

Use this mode only for explicit Receipt/provenance or completion-history
investigation. It is bounded detail, not an exhaustive export or an additional
normal-loop read. Use the same `<task-id>` as the [normal show operation](#task-show)
and apply the shared [Task Read Conditions](#task-read-conditions):

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task show --repo <target-project> <task-id> --audit --json
```

Audit does not change saved Evidence, validation, or current gates. Its
`review_evidence` retains the target, full gate/counts, blocking Findings, and
bounded recent Receipt/provenance and Finding rows; events retain the newest ten.
Audit `completion_history` retains exactly:

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
gate. Audit text reports bounded counts and the latest cycle's non-content
fields; normal text omits saved-cycle detail.

Stored public completion-evidence and review-target text is strictly
privacy-revalidated before projection. Completion history has no
[legacy counter compatibility exception](#errors-and-privacy); rejected or corrupt stored text returns
`completion_history_inconsistent` without exposing the value. Audit `task show`
and Viewer use the same bounded history projection; normal show validates it
before omitting its detail.

Audit `verification_evidence` has exactly `expectation`, `contract_revision`,
`source_revision`, `current_verification_subject`, `gate`, `counts`, and
`recent_receipts`. `source_revision`
is null without a target; otherwise it contains exactly `kind`, `value`,
nullable `base_revision`, and positive `generation`. Gate has exactly
`required`, `satisfied`, nullable `blocking_code`, and nullable
`qualifying_receipt_id`. Counts has exactly `receipts_total`,
`receipts_exact_current`, `qualifying_exact_current`, and
`blocking_exact_current`. At most ten newest-first rows use the fixed public
Receipt fields and never expose the internal expectation digest. The same gate
and subject types/null rules apply in normal mode. Text does not summarize
Receipt state. Invalid stored Receipt evidence returns the
sanitized `invalid_verification_evidence` failure.
For the returned subject or provenance compatibility fields, use
[Receipt output detail](#receipt-output-for-integration-or-audit).

<a id="task-checkpoint"></a>

### `task checkpoint`

Record one optional typed continuation boundary for
`data.selected.task.task_id` from `task context`:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task checkpoint --repo <target-project> <task-id> --summary "Completed slice" --next-action "Run verification" --unresolved-risk "Review remains" --json
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
Only an already-stored checkpoint summary may use the bounded legacy
numeric `dispatch_authorization` JSON reader. It returns the original summary
unchanged and authorizes no write or external operation. New checkpoint input
and every other checkpoint field use strict normal validation.

<a id="task-edit"></a>

### `task edit`

Update task state or metadata for `data.selected.task.task_id` from
`task context` (unlike `task context`, this command requires the ID):

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for user decision" --json
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
`contract_write` for Contract operations.

Task Contract activation is allowed only on an exact revision-zero
`ready|blocked -> in_progress` transition. Later semantic revisions are
Contract-only, require explicit later authority and a reason, invalidate
current completion/review eligibility, and use immutable successive revisions.
Canonically unchanged input is a write-free replay.
Omitted later constraints retain the byte-identical, already-validated prior
value, including [bounded legacy counter forms](#errors-and-privacy); explicit constraints use strict
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
completion. Read [Runner Plan Actions](#runner-plan-actions) only for an explicit Runner Plan action or
`runner_plan_action_required`, not for ordinary metadata/state edits.

<a id="runner-plan-actions"></a>

### Runner Plan Actions

Use only for an explicit Plan action or `runner_plan_action_required`.
Use the Task ID from [task edit](#task-edit); its shared Task/Contract edit
conditions also apply to combined changes. The [example and OS limits](#runner-plan-example-and-os-limits) are part
of this authoring guidance, not ordinary Task-edit prerequisites.

An action-bearing success adds
exactly `runner_plan_update={"action":<action>,"status":<status>}`, where status
is `updated|unchanged|unconfirmed`. Without an action, JSON, text, warnings, and
errors retain the existing shape.

`--runner-plan-action` is an explicit opt-in to author only the canonical
ignored physical package file `config/verification-runner.json`; it adds no
command leaf and never launches the Runner or sets a review target. The actions
are closed:

| Action | Standard input | Plan effect |
|---|---|---|
| `replace` | one document | Upsert the addressed Task entry from a strict versioned `RunnerPlanDraft`; this is the only initial-set action. |
| `rebind` | not read | Require one addressed entry, preserve its steps and position, and bind it to the exact current or future Task basis. |
| `detach` | not read | Remove every addressed Task entry while preserving all unrelated entries and order. |
| `disable` | not read | Set only global `trusted_local=false`; preserve Plan ID, entries, and order. |

The `replace` stdin document contains exactly `version=1|2` and `steps`, is capped
at 65,536 UTF-8 bytes by one read of at most 65,537 bytes, and contains one
through 16 exact Step objects matching that version. V1 retains required
`memory_mib` and `process_limit`; v2 replaces them with `windows_limits`, either
the object containing both bounded integers or null. These limits apply only
to Windows; Windows requires them before launch. An explicit v2 replace
upgrades a v1 Plan, preserving unrelated entry values and bases; other actions
preserve the version and no action downgrades v2. Task ID, Contract revision,
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

#### Runner Plan Example And OS Limits

After ordinary physical installation, setup, and ignore protection, this
optional v2 draft works on Windows, Linux, and macOS for a nonterminal Task
with a current Contract and verification criterion. Adapt the entrypoint and
limits to the approved verification; the script and its required files must
exist in the exact Git target. Save this JSON as `runner-plan-draft.json`:

```json
{
  "version": 2,
  "steps": [
    {
      "step_id": "focused",
      "mode": "script",
      "entrypoint": "tests/test_focused.py",
      "argv": [],
      "cwd": ".",
      "timeout_seconds": 60,
      "cpu_seconds": 60,
      "windows_limits": {"memory_mib": 256, "process_limit": 4},
      "output_byte_limit": 1048576
    }
  ]
}
```

From the target-project root, explicitly publish the addressed Task's entry:

```powershell
Get-Content -Raw -Encoding utf8 .\runner-plan-draft.json |
  python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --runner-plan-action replace --json
```

On Linux/macOS, submit the same file:

```sh
python3 .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --runner-plan-action replace --json < runner-plan-draft.json
```

The first absent-file `replace` is the explicit trusted-local opt-in and creates
the canonical ignored Plan with `trusted_local=true`. It neither launches the
Runner nor sets a target. Execution remains part of the existing
`review target set` operation; this optional authoring step adds nothing to the
normal Task loop and does not discover commands or infer verification coverage.

Windows retains Job-based aggregate user CPU, memory, and simultaneous-process
limits. Linux/macOS instead enforce per-process CPU and wall timeout for the
whole execution, then stop and clean up the ordinary managed process group;
intentional daemon escape is outside the guarantee. Memory and simultaneous-
process limits are unsupported there and do not require future implementation.
The Windows pair above is ignored on Linux/macOS; `windows_limits=null` is also
valid there but prevents launch on Windows. Null is not an applied or unlimited
limit. POSIX memory and cumulative-process measurements are unmeasured (null,
not zero). Unavailable auxiliary measurements alone do not prevent PASS, while
execution failure or uncertain managed-process cleanup still blocks completion.

The Plan cannot select another interpreter or use PATH lookup. Arguments
remain literal, no shell is used for Runner execution,
and verification runs only in private exact Git material with no copy-back or
raw-output retention. These are trusted-code reliability guarantees, not
hostile-code or network isolation.

<a id="task-complete"></a>

### `task complete`

Complete the selected Task after its current gates pass. `<task-id>` is
`data.selected.task.task_id` from `task context`; `<hash>` is the exact
completion commit created under the project's separately authorized Git
workflow, not an ID or fingerprint from a Task response:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --completion-evidence-kind git_commit --completion-revision <hash> --json
```

Only when an explicit no-write readiness preview is useful, run the same
proposal with `--check --read-only`; it is not a normal-loop prerequisite:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --completion-evidence-kind git_commit --completion-revision <hash> --check --read-only --json
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
python .agents/skills/task-governance-tool/scripts/taskgov.py verification receipt add --repo <target-project> <task-id> --result pass --duration-ms <milliseconds> --scope-coverage full --expected-target-generation <generation> --json
```

The normal trigger is `data.verification_route=receipt_required` in the
successful `review target set` response. Take `<task-id>` from its
`data.task.task_id` and `<generation>` from `data.task.review_target_generation`.
Use the actual run's result, measured duration and confirmed coverage; a PASS
alone never establishes full coverage. Other routes are handled in
[target selection](#review-target), without another `task show`.

The four options are required unless `--from-stdin` supplies the fixed
[structured result](#structured-verification-result). `result` is `pass`, `fail`, or `timeout`;
`scope-coverage` is `full` or `partial`; duration is a nonnegative signed-
64-bit millisecond value; and expected generation is the positive generation
returned by target set. `--command-label` is not accepted and no caller subject
option replaces it. Taskgov binds the Receipt to the current Contract and exact
target and generates the subject, Receipt ID, and recording time; do not supply
or reconstruct them.

Receipt recording is allowed only for `in_progress` or `review_pending`,
requires verification that is nonempty after trimming and a current marker-`0`
target or exact-current closed no-launch `m21_fallback`, writes no Task event or
timestamp, and permits one immutable row per target generation. A marker-`2`
qualifying Runner pass, pending basis, stale basis, cleanup-only basis, or any
other structurally valid terminal result rejects Receipt add with
`evidence_basis_stale`. A retry after `fail`, `timeout`, or `partial` requires
an explicitly fresh target.
Taskgov does not execute a command for this manual verification branch, infer coverage,
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

On registration success, `data.receipt` is the recorded result and its
`verification_receipt_id` is the generated ID. `ok=true` means registration,
not verification PASS. `data.review_preparation` has `status`, `packet`, and
`errors`. Pass/full automatically returns `ready` with the existing Packet;
fail/timeout/partial returns `blocked`, null Packet and the existing blocking
code without preparing. Post-commit preparation failure returns `failed`, null
Packet and sanitized errors, while preserving the Receipt and outer warnings.
Give a ready Packet directly to reviewers; do not prepare it again.

For preparation-only failure, retry only the read-only operation against the
same saved Receipt:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review prepare <task-id> --verification-receipt-id <recorded-id> --read-only --json
```

Both Packet reads validate that Receipt's exact current Task, Contract and full
target basis. Stale evidence cannot be rebound; it requires fresh targeting
and verification. If the response was lost or timed out, inspect recorded
state first to learn whether registration actually committed. Do not infer
rollback or blindly replay registration; the same generation accepts one
immutable Receipt only. No review, completion, or external verification is
automatically launched. Receiptless/Runner-pass routes do not register one.
Only for output integration or historical subject
inspection, read [Receipt output detail](#receipt-output-for-integration-or-audit).

`--read-only` and every failed call perform no business or maintenance write.
There is no Receipt list, show, import, export, Runner command, or Viewer panel.
For explicit Receipt inspection use [task show](#task-show), not a new command.

<a id="structured-verification-result"></a>

### Structured Verification Result

When an external verifier already emits this fixed format, send its stdout
unchanged to `verification receipt add <task-id> --from-stdin --json`. The
registration replaces the four individual result options; it does not add a
normal-loop call, launch verification, or require the LLM to read/convert the
result. Keep other verification tools on the existing manual attestation path.

```json
{"version":1,"task_id":"tg_task_0123456789abcdef","result":"pass","duration_ms":1250,"scope_coverage":"full","expected_target_generation":1}
```

All six fields are required, with no additional keys. Input is one UTF-8 JSON
object, at most 4,096 bytes, without BOM or duplicate keys. Version is integer
1; duration and generation are exact JSON integers (not strings, floats or
Booleans) with the existing bounds. Other fields use existing string, enum
and privacy checks. Invalid shape/encoding is `invalid_verification_evidence`;
a different Task ID is `verification_basis_stale`. Mixed individual options
and `--from-stdin` are `invalid_option_combination`; read-only rejects before
consuming stdin. Nothing is recorded on rejection.

Supply Task ID and generation to the external verifier as the already-selected
run context before execution. `full` must be explicitly justified against the
entire Task verification expectation, never inferred from success. Subject,
Contract, criterion, source tuple, Receipt ID and recording time are still
derived by the existing writer, not repeated in the document. This remains a
caller attestation, not authenticated process evidence. Normal Receipt output,
one-per-generation, Runner eligibility, completion gates and maintenance are
unchanged; no raw document or stream is stored. A fresh run after failure,
timeout or partial coverage requires a fresh target first.

For shell-independent byte transport, a caller can connect an already approved
producer's binary stdout directly to the consumer's stdin, or pass those same
bytes using `subprocess.run(..., input=producer.stdout)`. Do not turn arbitrary
test output into this format by asking the LLM to transcribe it. No producer
installation, configuration or command execution is authorized by this input mode.

## Local Handoff Commands

<a id="handoff-record"></a>

### `handoff record`

Record one sanitized out-of-scope discovery. `<source-task-id>` is
`data.selected.task.task_id` from `task context`, the Task where it was found:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff record --repo <target-project> <source-task-id> --summary "Concise discovery" --rationale "Outside acceptance" --json
```

`--summary` is required and capped at 1,000 characters. `--rationale` is
optional and capped at 1,000. Optional `--occurrence-id` is capped at 200 and
must already come from a stable user/deterministic source.

Data keys are `handoff` and `local_record`. `local_record` contains exactly
`durable`, `created`, `replayed`, and `handoff_id`. Exact canonical replay
returns the same row with `created=false`, `replayed=true`, and no update.
Use `data.local_record.handoff_id` for a later explicit show or withdrawal.

The row is local `pending_handoff` only. It captures the source task and
current Contract revision but does not change task state, acceptance,
selection, events, timestamps, or completion. The command performs no Issue
adapter detection, delivery, semantic triage, or priority assignment.

Local busy persistence receives at most one fresh-transaction retry. Final
failure returns `handoff_not_persisted` with `handoff=null` and a
non-durable `local_record`.

<a id="handoff-list"></a>

### `handoff list`

List bounded records:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff list --repo <target-project> --json
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff list --repo <target-project> --state handed_off --state handoff_withdrawn_by_user --json
```

Options are repeatable `--state`, `--source-task-id`, and `--limit` (default
20, maximum 100). Default state is `pending_handoff`; rows sort oldest first.
Data keys are `handoffs`, `count`, exact `total_matching`, `limit`, and
`states`. Paging is not implemented.

<a id="handoff-show"></a>

### `handoff show`

Show one sanitized full record, using `data.local_record.handoff_id` from
recording or the selected `data.handoffs[].handoff_id` from listing:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff show --repo <target-project> <handoff-id> --json
```

Data contains only `handoff`. Stored content is privacy-revalidated before
emission.

<a id="handoff-withdraw"></a>

### `handoff withdraw`

Withdraw an undelivered pending row only on explicit user direction. Use its
`handoff_id` from the record/list/show response, not the source Task ID:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff withdraw --repo <target-project> <handoff-id> --reason "Handled outside Task Skill" --json
```

Reason is required and capped at 1,000 characters. A terminal, claimed, or
attempted row returns `handoff_not_withdrawable`. Withdrawal changes no source
task or task event.

## Review Commands

<a id="review-prepare"></a>

### `review prepare`

Prepare one bounded read-only reviewer packet. Qualifying Receipt registration
and Receiptless target setting already return this Packet, so this command is
for explicit preparation/retry, not the normal success loop. Use `<task-id>` from the
successful target-set response's `data.task.task_id`, after handling its
verification route:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review prepare --repo <target-project> <task-id> --read-only --json
```

Supported target kinds are `git_snapshot`, `git_commit`, `diff_fingerprint`,
and `external_revision`. After target-set preparation failure, copy its
`data.review_preparation.binding` into `--expected-binding <binding>`. This
checks the same saved Task/Contract/full-target context before and after
preparation. It accepts `sha256:` plus 64 lowercase hex digits and rejects
combination with `--verification-receipt-id` as `invalid_review_evidence`.
Drift is `review_packet_stale`, not permission to follow a newer target.
The retry is read-only: no target setting, generation advance, Runner launch,
Receipt registration, or maintenance. Never repeat target setting to recover
only a Packet.

If the target response was lost, inspect `task show <task-id> --audit --json`
first. Confirm its saved Task, Contract, and full target match the intended operation, then use
`data.review_evidence.preparation_binding` for a preparation-only retry. A null
binding means no target; uncertain or changed state requires resolution, not
blind replay or rebinding. This current comparison value is not gate evidence;
normal show/context has no added field or call.

Optional `--verification-receipt-id <id>` binds a
Packet-only retry to that exact-current pass/full Receipt in both reads;
mismatch uses `verification_basis_stale`, and non-pass/full uses
`verification_receipt_blocking`. The Receipt ID uses its existing fixed syntax.
Omitting it retains standalone preparation, which is not proof of verification
eligibility. The command takes no caller focus, reviewer, import,
receipt-file, or output-destination argument. It launches no reviewer, imports
no result, writes no packet, and changes neither local task state nor target
Git.

Success data keys are exactly:

```text
task, contract, review_target, changed_paths_available, changed_paths,
changed_paths_total, changed_paths_truncated, review_focus, required_output,
receipt_command
```

Changed paths are strict relative UTF-8 project paths, sorted bytewise, and
bounded to 100 rows, 240 UTF-8 bytes per row, and 16,384 aggregate path bytes.
The complete text or JSON stdout is capped at 32,768 bytes. A truncated path
list is not the complete review scope; inspect the exact material bound to the
returned target.

The fixed `required_output` requests a version-1 structured result with one
reviewer's Receipt, severity-ordered Findings, and actual provenance. Bounded
summaries include exact file/line references, remaining risks and recommended
changes without raw review reasoning. `receipt_command` names `review result add`;
preparation itself neither records results nor launches a reviewer or model.

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

<a id="review-target"></a>

#### Target Selection And Binding

Set or replace the target for `data.selected.task.task_id` from `task context`.
For pre-commit review, stage the intended files and use the snapshot form:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_snapshot --json
```

For review of an existing commit, use its exact revision instead:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_commit --revision <revision> --json
```

`git_commit`, `diff_fingerprint`, and `external_revision` require
`--revision`; `git_snapshot` rejects it. Every set advances the target
generation. Git commits are resolved read-only and stored canonically. A diff
fingerprint is `sha256:` plus 64 lowercase hexadecimal characters.

At schema v21 or v22, this same target-set operation may use the explicitly opted-in
trusted-local Runner route. It adds no argument or public Runner command. JSON
success data is `task`, `changed_fields`, `event`, `verification_route`,
`blocking_code`, and `review_preparation`; failure
data remains exactly the prior three-key empty shape. `verification_route` is
exactly `not_required`, `receipt_required`, `runner_pass`, or `blocked`.
`blocking_code` is null for the first three and is
`verification_receipt_blocking` for a stored nonqualifying Runner terminal.
The route describes the target and Runner result committed by this invocation;
use it directly, without another `task show`. Retain `data.task.task_id` and
`data.task.review_target_generation` for Receipt registration. `receipt_required`
means run the governed verification and record its aggregate result;
`not_required` and `runner_pass` automatically prepare a Packet after the save
and existing Runner work, without a Verification Receipt. `review_preparation`
has `status`, `binding`, `packet`, and `errors`: `ready` carries the Packet;
`failed` carries sanitized errors and retains the binding for read-only retry.
Preparation failure keeps outer `ok=true`, exit zero, and the saved target.
For preparation-only retry or a lost response, follow [bound Packet recovery](#review-prepare).
For `receipt_required`/`blocked`, status is `not_applicable`, binding and Packet
are null, errors empty, and no preparation is attempted. Text appends the
status and Packet or failure/retry detail. Warnings and one existing maintenance
pass remain outer-operation concerns. Use the Packet only when `ready`; the
Packet component retains its existing bounds. `blocked` requires its non-null code and stops this completion path;
a missing or inconsistent route/code pair also stops closed.

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

<a id="review-provenance"></a>

#### Receipt Input And Provenance

Use [Structured Review Results](#structured-review-results) when the actual
received results have its version-1 shape and matching Task/Contract/target;
combine their Receipt arrays without rewriting declarations. A single result
in that shape can use the same command; reviewer count is not the selector.
Use the existing single-Receipt form for a review received outside that format
when its actual verdict, required provenance and exact reviewed basis are
available. Record any actual Findings with [Finding creation](#finding-resolution).
Do not infer absent declarations, rebind an old result, drop Findings, or split
a rejected batch to evade validation. Obtain missing/corrected information
from the review source. An uncertain registration response requires inspecting
saved evidence before any retry; neither form makes committed replay idempotent.
Use the Task ID in the
actual Review Packet's `task.task_id`, and the reviewer's actual declaration
(Packet is `data.review_preparation.packet` after registration/target setting or `data` after
standalone preparation):

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <stable-reviewer-key> --kind independent --verdict pass --summary "No blocking findings" --reviewer-class human --model-state not_applicable --skill-state not_applicable --context-relation external_context --review-profile general --review-lens correctness --review-method review_packet_inspection --json
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

`context_relation` is a provenance declaration, not an independence verdict.
Do not infer reviewer independence or a `fresh_context` requirement from its
value alone; apply the project's actual review requirements.

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

Single-Receipt success returns its ID at `data.receipt.review_receipt_id`.
Taskgov generates the output provenance metadata; supply only the caller
declarations above. For interpreting returned metadata or legacy absence, use
[Receipt output detail](#receipt-output-for-integration-or-audit) only when that
integration or audit is needed. Provenance never proves identity, actual
model/Skill execution, competence, independence, diversity, quality, or truth.

The independent reviewer returns the verdict and findings. The trusted
parent/orchestrator records their concise sanitized receipt/finding rows as an
attestation. Taskgov deterministically evaluates qualifying PASS receipts and
changes-requested receipts only for the current review target and generation.
Any unresolved high or medium finding from any recorded generation of that
Task continues to block the gate. Distinct reviewer keys prove distinct stored
strings only; they do not prove distinct people, LLMs, machines, independent
processes, independence, authenticated provenance, or summary truth.

<a id="finding-resolution"></a>

#### Finding Creation And Resolution

Use `task.task_id` from the actual Packet object returned by registration/target setting or
standalone preparation.
`<receipt-id>` is `data.receipt.review_receipt_id` from single Receipt creation,
or the corresponding `data.receipts[].receipt.review_receipt_id` from grouped
results. `<finding-id>` is the selected `review_finding_id` returned by Finding
creation, grouped results, or `task show`'s `data.review_evidence.current_findings`:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review finding add --repo <target-project> <task-id> --receipt-id <receipt-id> --severity high --summary "Concise finding" --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review finding resolve --repo <target-project> <finding-id> --resolution "Concise resolution" --json
```

Severity is `high`, `medium`, or `low`. Open high/medium findings block
completion. After resolving a blocking finding and changing material, set a
new target and obtain fresh qualifying receipts.

<a id="structured-finding-resolutions"></a>

#### Structured Finding Resolutions

For several confirmed resolutions, send one UTF-8 JSON document to
`review finding resolve --from-stdin --json`:

```json
{"version":1,"task_id":"tg_task_0123456789abcdef","resolutions":[{"finding_ids":["tg_review_finding_0123456789abcdef"],"resolution":"Individual correction verified"},{"finding_ids":["tg_review_finding_123456789abcdef0","tg_review_finding_23456789abcdef01"],"resolution":"Shared correction verified for these two findings"}]}
```

Use `data.task.task_id` from `task show` and the selected
`data.review_evidence.current_findings[].review_finding_id` values, or the same
fields under `data.selected` in `task context`. Previously returned Finding
creation/result IDs may also be used; no extra confirmation read is required.

Every shown key is required, with no extras or duplicate JSON keys. UTF-8 must
have no BOM and fit 262,144 bytes. Version is exactly integer 1. Each group has
at least one ID; the total is 1 through 64, with no normalized duplicates.
IDs and reasons are strings using existing normalization/privacy and
128/1,000-character limits respectively. No target or Contract echo is needed:
existing older-generation and capture-v0 Findings remain resolvable.
Task and project ownership, done prohibition, and existing-resolution
immutability still apply. The LLM selects only fixes it has actually confirmed;
identical reasons are shared only by explicitly listing those IDs together.

Do not combine stdin mode with a positional ID or `--resolution`; it returns
`invalid_option_combination` before state access. Read-only never consumes
stdin. Success data is `{"findings":[{"finding":{},"event":{}}]}` with the
existing projections in flattened input order; each Finding ID identifies its
input. Batch failure returns `findings=[]`, except ordinary parse rejection.
All selected resolutions commit together or all roll back, with one maintenance
pass. No extra per-ID confirmation is needed after a successful response.
After confirmed rollback, correct and resubmit the explicit selection. For a
lost response, inspect existing state and preserve completed resolutions;
resubmit only IDs proven still open, never blindly replay or overwrite.
Malformed shape uses `invalid_review_evidence`; missing/foreign-project IDs use
`not_found`, different-Task IDs use `invalid_review_evidence`, and other existing
privacy/value/storage errors keep their codes. Unselected Findings and original
content are unchanged. A blocking resolution still needs a newer target and
fresh qualifying review; this mode never infers resolution from PASS.

### Structured Review Results

Register the reviewers' concise structured results together, using UTF-8 JSON
stdin with `review result add <task-id> --json`. There is no input-file argument
or output destination. For example, an already assembled JSON string can be
piped in PowerShell with UTF-8 encoding:

```powershell
$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$resultJson | python .agents/skills/task-governance-tool/scripts/taskgov.py review result add <task-id> --json
```

The document has this exact shape (replace the illustrative identity and
target with those returned in the actual Review Packet):

```json
{
  "version": 1,
  "task_id": "tg_task_0123456789abcdef",
  "contract_revision": 1,
  "review_target": {
    "kind": "external_revision", "value": "approved-revision-1",
    "base_revision": "", "generation": 1
  },
  "receipts": [{
    "reviewer": "reviewer-a", "kind": "independent", "verdict": "pass",
    "summary": "No blocking findings",
    "provenance": {
      "reviewer_class": "human", "model_state": "not_applicable",
      "declared_model_id": null, "skill_state": "not_applicable",
      "declared_skill_id": null, "declared_skill_version": null,
      "review_profiles": ["general"], "review_lenses": ["correctness"],
      "context_relation": "external_context",
      "method_codes": ["review_packet_inspection"]
    },
    "findings": []
  }]
}
```

Use the actual Packet object: `data.review_preparation.packet` after qualifying
registration/target setting or `data` after standalone preparation. Copy its `task.task_id`
into both the command's `<task-id>` and document `task_id`, `contract.revision`
into `contract_revision`, and complete `review_target` into `review_target`.
Combine only reviewers' `receipts` arrays with identical envelope values;
preserve returned verdicts and provenance rather than filling or retyping them.

All displayed keys are required and no other keys are accepted. Each Finding
contains exactly `severity` and `summary`. Put exact project-relative file/line
references and recommended changes in its bounded summary, not extra fields.
Use the [Receipt/provenance enums, bounds and matrix](#review-provenance) and
[Finding rules](#finding-resolution). `provenance` contains exactly those ten caller declaration fields, with
explicit nullable identifiers and arrays; use null only for `not_required`.
Do not invent model/Skill identity, context, methods, or independence.

The whole input is limited to 262,144 UTF-8 bytes, 1–8 Receipts, and 64 Findings
total. Version must be integer 1; Contract revision is a nonnegative signed-64-bit
integer and generation a positive one. Exact JSON types are enforced (booleans
are not integers). Duplicate/unknown/missing keys, non-finite numbers, invalid
Unicode and exceeded limits fail `invalid_review_evidence`. Existing privacy
checks inspect every typed declaration before enum, text-limit, duplicate-reviewer
or provenance-matrix checks and never echo rejected content.

Task ID, Contract revision and the complete target tuple must match the current
Task under the writer lock; stale or mismatched input fails `review_target_mismatch`
without rebinding. Existing missing-target, done-Task and capture-version-0
errors remain. Duplicate normalized reviewer keys within the batch, previously
registered reviewers and committed replay fail `review_receipt_already_recorded`.
Duplicate normalized `(severity, summary)` pairs within one Receipt fail
`invalid_review_evidence`; separate reviewers may report the same Finding.

JSON cannot supply user approval. Only when the existing Tier-2 self-review PASS
fallback has explicit current user approval, add repeatable
`--user-approved-reviewer <key>` for the matching reviewer. Duplicate, unmatched,
ineligible or missing required approval fails `invalid_review_evidence`.

All entries are new and save atomically with their existing individual
provenance, References and events. Success data is exactly
`{receipts: [{receipt, event, findings: [{finding, event}]}]}` in input order.
Use `data.receipts[].receipt.review_receipt_id` for the returned Receipt IDs
and `data.receipts[].findings[].finding.review_finding_id` for Finding IDs;
no follow-up read is needed to obtain them.
Each nested object uses the existing public single-item projection. Failure
data is `{receipts: []}` with no saved prefix, including after a late failure.
Text success reports only Receipt/Finding counts. `--read-only` rejects before
stdin is consumed. Successful registration runs existing post-commit maintenance
once; maintenance warnings do not undo the committed evidence.

The tool stores no result document or batch ledger, launches no reviewer,
merges no judgment, resolves no Finding, and does not complete the Task.
Distinct reviewer keys remain caller-attested strings, not proof of identity
or independence. The existing single-item commands remain available.

## Receipt Output For Integration Or Audit

Read this section only to consume generated Receipt fields in an integration
or interpret explicit [audit detail](#task-audit-detail). It is not an input
template or a prerequisite for registering verification/reviews. Normal
registration uses the input and result-handling sections above; the tool owns
the output IDs, timestamps, subject binding, and provenance metadata.

### Verification Receipt Output

Successful registration returns `data.receipt` with exactly:

```text
verification_receipt_id, project_id, task_id, contract_revision,
verification_subject, result, duration_ms, scope_coverage, source_revision,
created_at
```

`source_revision` is the bound target's `kind`, `value`, nullable `base_revision`,
and `generation`. `verification_subject` has exactly `basis_version`, `kind`,
`authority_snapshot_id`, `verification_criterion_id`, and `legacy_caller_label`.
A native v1 Receipt has basis 1, kind `task_verification_criterion`, both
non-null IDs, and a null legacy label. A migrated pre-v18 Receipt has basis 0,
kind `legacy_caller_label`, both IDs null, and the preserved label. The audit
Receipt rows use the same union. These are tool-owned bindings, not caller
identity authentication or permission to reuse historical evidence.

Successful text starts with this unchanged Receipt prefix:

```text
Verification receipt recorded: <verification_receipt_id>
Result: <result>  Coverage: <scope_coverage>
Source: <kind>/generation <generation>
```

It is followed by `Review preparation: <ready|blocked|failed>` and the Packet
text or sanitized code/message lines. JSON additionally returns
`data.review_preparation` as described under [Verification Receipt](#verification-receipt).
The Packet component keeps the standalone 32,768-byte envelope check; the
combined output also includes the bounded Receipt/status and is not subject
to a new combined 32,768-byte cap.

### Review Provenance Output

Each public Review Receipt contains `review_provenance`. Native independent
or self-review Receipts have a v1 object with exactly:

```text
review_provenance_id, provenance_version, reviewer_class, model_state,
declared_model_id, skill_state, declared_skill_id, declared_skill_version,
review_profiles, review_lenses, context_relation, method_codes,
assurance_class, producer_class, producer_version, digest
```

Native assurance/producer/version is `bound_attestation/trusted_caller/1`.
Migrated pre-v18 independent/self-review Receipts use the same keys as v0:
null ID/digest and semantic fields/collections, with
`legacy_unknown/legacy_migration/1`. This records missing historical provenance,
not explicit v1 `unknown` or empty code sets. `not_required` instead has null
provenance and no provenance row. These cases do not change the parent
Receipt's caller-attested meaning or prove identity, actual model/Skill use,
independence, or review truth. Only current evidence can satisfy current gates.

## Internal Continuity Boundary

Read this section for a continuity warning or an explicit setup/Viewer
diagnosis, not as a normal-loop action. Successful `setup` enables bounded
same-process maintenance; there is no disable, export, custom-output, or repair
command. Taskgov starts no background worker, scheduler, browser, or network
operation. SQLite stays canonical; generated Evidence JSON and Viewer files
are projections, never inputs or current completion evidence.

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

Use [doctor](#doctor) only for explicit read-only diagnosis and [setup](#setup)
for authorized repair. A stored Task fault preserves the last-good Viewer;
setup preflight fails no-write with `project_state_unreadable`, whereas a
failure confined to its later Viewer stage is `setup_incomplete` and reports
the durable completed prefix.

The optional physical `config/viewer.json` is browser-presentation policy, not
a CLI or normal-loop choice. Taskgov never creates it; absence means no refresh
timer. A valid schema-1 `visibility-refresh-v1` profile applies its 5-3,600 second
interval on the next Viewer publication. Invalid policy preserves routine Task
success and the last-good Viewer with `viewer_refresh_failed`; actual setup uses
`setup_incomplete`. Preview remains successful and no-write, reporting
`viewer_status="repair_required"` and planned `viewer_publish`. Doctor does not
inspect this policy; it cannot confirm its validity.

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

Correct an observed command/argument mismatch against its command section.
`internal_error` alone does not establish a syntax error or an instruction
defect: the cause may be in the environment or internal processing. Report the
sanitized failure and keep the cause unconfirmed until relevant read-only
diagnosis establishes it; do not guess new options or blindly retry writes.

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
The sole legacy reader is confined to already-stored bounded lowercase
`dispatch_authorization` positive-canonical-integer equality and numeric JSON
counter forms in Contract constraints and checkpoint summaries. It preserves their original text, performs
no write, and leaves compound credentials or tokens rejected.
