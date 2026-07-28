# CLI Contracts

Use this reference when exact public commands, arguments, JSON fields, bounds,
or error behavior matter.

Release `0.8.0` uses task schema v13 and offline snapshot v3.

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
enclosing Git worktree. A non-Git directory is valid. Stateful use supports one physical
project-scoped package only; user-wide, symbolic-link, and Windows junction
layouts are unsupported. The verified runtime is Windows with Python 3.12 or
later.

The complete public command inventory is exactly these 20 leaves:

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
  "project_id": "project-a1b2c3d4e5f6",
  "data": {},
  "warnings": [],
  "errors": []
}
```

Public output contains no local storage, backup, projection, or rejected-input
path. Error rows contain only `code` and a sanitized `message`.

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
transaction before one atomic business update.

The normal no-finding Tier 2 Skill graph is bounded to nine governance
subprocess calls when the Effort Advisory is off and ten when the mandatory
`task show` boolean enables it. No command asks an LLM to choose that branch.

## `setup`

`setup` is the sole explicit initializer, migrator, one-way continuity opt-in,
and canonical offline-projection repair action:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

Options:

- `--backup-interval-minutes <1..1440>`
- `--backup-generations <1..20>`
- `--read-only` for a no-write preview

The normal Skill flow supplies neither policy option. A first setup defaults
to 30 minutes after the last successful managed copy and three retained
generations. Once configured, omitted options preserve stored values; values
equal to stored policy are a write-free replay.

When the canonical database is absent, setup checks only canonical managed
generations for the same project. It mechanically restores the newest valid
generation, then performs any required normal migration/configuration and
Viewer publication. It never overwrites an existing database or accepts a
caller path. Invalid, foreign, linked, and unrecognized artifacts are
unchanged. Canonical managed names with no valid same-project generation fail
with `setup_restore_failed` and message
`managed backup could not be restored`; setup does not initialize empty state.
The same fixed failure applies when a rollback-journal entry remains for the
missing canonical DB; setup neither applies nor changes that journal.

`data` always has exactly:

```json
{
  "status": "setup_complete",
  "planned_writes": [
    "database_initialize",
    "maintenance_configure",
    "viewer_publish"
  ],
  "completed_writes": [
    "database_initialize",
    "maintenance_configure",
    "viewer_publish"
  ],
  "schema_from": null,
  "schema_to": 13,
  "maintenance_enabled": true,
  "backup_interval_minutes": 30,
  "backup_generations": 3,
  "viewer_status": "published"
}
```

Successful `status` is `setup_preview`, `setup_complete`, or
`already_setup`. Write-list values are limited to `database_restore`,
`database_initialize`,
`migration_backup`, `database_migrate`, `maintenance_configure`, and
`viewer_publish` in execution order. `viewer_status` is `not_present`,
`current`, `published`, or `repair_required`.

Preview reports current durable state, not planned state:
`completed_writes=[]`, and a fresh preview keeps
`maintenance_enabled=false`. A healthy replay has empty write lists. Every
error has `status=null`; preflight/policy failures use empty write lists and
null observed values except `schema_to=13`. A later-stage failure reports only
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
      "package_version": "0.8.0",
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
      "schema_version": 13,
      "required_schema_version": 13
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

Package `modified` or `unknown` is an exit-0 warning and makes
`setup_eligible=false`. Missing or migratable state is an exit-0
`setup_required` or `migration_required` warning. Invalid layout/project,
unsupported runtime/journal, busy or unreadable state, project mismatch, and a
newer schema are exit-2 errors. No row exposes a local path, digest, file
content, exception, or raw state.

## Task Commands

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
`suggested_action` is always `continue`. Threshold or attribution results
never change status, acceptance, review, completion, or target state. The
command emits no paths, stderr, diffs, raw logs, or Git writes.

### `task show`

Read one task and its bounded current context:

```powershell
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

Data keys are exactly `task`, `events`, `suggested_next_action`,
`review_evidence`, `handoff_summary`, `contract`, `latest_checkpoint`, and
`effort_advisory_enabled`.

`effort_advisory_enabled` is a deterministic boolean routing field. Invalid
profile content returns `false` plus
`effort_advisory_profile_invalid`; the field performs no Git work.

The task contains typed completion evidence and current review-target fields.
`review_evidence` is bounded to the current target/generation, tier gate,
counts, blocking findings, and recent structured receipts/findings; it omits
raw reviews and private reasoning. `handoff_summary` contains exact
`pending_handoff`, `handed_off`, and `handoff_withdrawn_by_user` counts.

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
```

Success data contains `task`, `changed_fields`, and `event`, plus
`contract_write` for Contract operations.

Task Contract activation is allowed only on an exact revision-zero
`ready|blocked -> in_progress` transition. Later semantic revisions are
Contract-only, require explicit later authority and a reason, invalidate
current completion/review eligibility, and use immutable successive revisions.
Canonically unchanged input is a write-free replay.

Only `in_progress|review_pending -> paused` is valid and requires
`--pause-reason`. Resume explicitly to `in_progress`. Sequential transitions
to active, review-pending, or done use the same predecessor rule as
`task next`.

Lower a review tier only before any target has been set and provide
`--review-tier-change-reason`. A done task rejects every write except an
isolated `--status in_progress --reopen-reason <summary>` transition. Reopen
clears current completion/review eligibility and requires fresh gates.

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
`changed_fields`, and `event`. Both check and write require verification,
current matching review target, qualifying receipts, no current
changes-requested receipt, no unresolved high/medium finding, valid typed
completion evidence, sequential readiness, and exact Git/snapshot binding when
applicable.

Git evidence resolves to a canonical full commit ID. External evidence requires
an explicit reason and approval. `commit_not_required` requires a matching
`diff_fingerprint` target. taskgov does not create commits or mutate Git.

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

For `git_snapshot`, stage exactly intended files first. Capture observes
canonical HEAD and only the stage-0 index; unstaged and untracked files are
excluded. Completion requires a single-parent commit whose parent equals the
captured base and whose tree matches the fingerprint. A changed candidate
needs a new target and fresh receipts.

Add one sanitized current-generation receipt:

```powershell
python scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <stable-reviewer-key> --kind independent --verdict pass --summary "No blocking findings" --json
```

Kinds are `independent`, `self_review_fallback`, and `not_required`; verdicts
are `pass`, `changes_requested`, and `not_required`. One reviewer key may
record one receipt per target generation. Tier 2 normally requires two
distinct independent PASS receipts. Any current-generation
`changes_requested` receipt blocks completion. `--user-approved` is accepted
only where the governing review-tier fallback contract requires explicit user
approval; the normal independent path omits it.

The independent reviewer returns the verdict and findings. The trusted
parent/orchestrator records their concise sanitized receipt/finding rows as an
attestation. taskgov validates target binding and reviewer-key distinctness,
but does not authenticate the recorder or reviewer and proves neither identity
nor independence.

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
commits and closes before same-process projection refresh and any due managed
copy. Projection refresh precedes the due-copy attempt. Work is fail-fast,
bounded to at most two renders and one copy attempt, and taskgov never uses a
daemon, thread, timer, detached process, queue, scheduler, service, or network.

The optional physical `config/viewer.json` is browser-presentation policy, not
a CLI or normal-loop choice. Taskgov never creates it; absence means the
generated page owns no refresh timer. A valid exact schema-1
`visibility-refresh-v1` profile may embed a 5-3,600 second interval on the next
Viewer publication. Invalid policy keeps routine task success and the last-good
Viewer and uses the existing `viewer_refresh_failed` warning; actual setup uses
`setup_incomplete`. `setup --read-only` remains successful and no-write,
reports `viewer_status="repair_required"`, and includes `viewer_publish` in
`planned_writes`. Doctor does not inspect this policy.

These operations add no Skill command or LLM judgment. Their artifacts and
paths are absent from public command output. Snapshot v3 reads source schemas
5 through 13 without exposing internal maintenance or checkpoint fields.

If post-commit maintenance cannot complete, the primary business mutation
remains successful and only a bounded warning is appended:

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
- `schema_too_new`
- `unsupported_journal_mode`
- `database_busy`

Important task/review/handoff errors include:

- `invalid_argument`, `invalid_status`, `invalid_status_transition`
- `initial_done_forbidden`, `initial_paused_forbidden`
- `blocked_reason_required`, `pause_reason_required`
- `sequential_predecessor_incomplete`, `done_task_requires_reopen`
- `contract_activation_forbidden`, `contract_authority_required`,
  `contract_write_conflict`
- `verification_required`, `review_required`, `commit_required`
- `completion_evidence_conflict`,
  `external_revision_approval_required`,
  `git_commit_not_found_or_ambiguous`
- `review_target_required`, `review_target_mismatch`,
  `review_receipts_insufficient`, `review_changes_requested`,
  `review_finding_unresolved`
- `handoff_not_persisted`, `handoff_not_withdrawable`,
  `handoff_occurrence_invalid`
- `privacy_rejected`, `not_found`, `internal_error`

Privacy validation is deny-by-default for stored free-form input. Never submit
secrets, tokens, authorization headers, raw stdout/stderr, stack traces,
environment dumps, private prompts/reasoning, full chats/reviews, or large raw
diffs. If handoff input is rejected, never repeat, quote, log, store, or
forward the rejected raw content. Make at most one new attempt using a newly
written concise sanitized abstraction.
