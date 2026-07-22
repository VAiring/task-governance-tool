# CLI Contracts

Use this reference when exact command arguments, JSON shapes, or error behavior
matter.

## Contents

- [Invocation](#invocation)
- [Commands](#commands)
- [`db init`](#db-init)
- [`db status`](#db-status)
- [`task add`](#task-add)
- [`task list`](#task-list)
- [`task next`](#task-next)
- [`task current`](#task-current)
- [`task show`](#task-show)
- [`task edit`](#task-edit)
- [Review evidence commands](#review-evidence-commands)
- [`web export`](#web-export)
- [Error Codes](#error-codes)

## Invocation

Run from the project-scoped installed skill folder, normally
`.agents/skills/task-governance-tool` inside the governed project:

```powershell
python scripts/taskgov.py <command> [options]
```

For a new target project, start with `db status`. It reports missing or
outdated databases without creating files. Use `db init` only when local task
tracking should be created or migrated for that project-scoped install. If the
skill is running from a user-wide or global install, confirm that non-standard
setup before writing state.

Common options:

- `--repo <path>`: target project root; defaults to the current directory.
- `--db <path>`: explicit SQLite database path override.
- `--json`: emit a stable JSON envelope.
- `--read-only`: reject write commands before creating, migrating, or writing.

All JSON output uses this envelope:

```json
{
  "ok": true,
  "command": "task.next",
  "project_id": "project-a1b2c3d4e5f6",
  "db_path": "C:\\path\\to\\taskgov.sqlite",
  "data": {},
  "warnings": [],
  "errors": []
}
```

Inspection commands are read-only by default: `db status`, `task list`,
`task next`, `task current`, and `task show`.

Database write commands are `db init`, `task add`, `task edit`, and the four
`review` evidence commands. Only `db init` may create or migrate a database.
Other write commands require an already initialized database at the current
schema version; they return `db_not_initialized` or `migration_required`
without creating or migrating files otherwise. `web export` never writes
SQLite, but its normal mode writes one generated HTML file after explicit user
intent. Use `web export --read-only` for a no-file-write preview.

## Commands

### `db init`

Create or migrate the local task database.

```powershell
python scripts/taskgov.py db init --repo <target-project> --json
```

`data`:

```json
{
  "created": true,
  "migrations_applied": [1, 2, 3, 4, 5],
  "schema_version": 5
}
```

### `db status`

Inspect database readiness without creating or migrating files.

```powershell
python scripts/taskgov.py db status --repo <target-project> --json
```

`data`:

```json
{
  "exists": true,
  "needs_init": false,
  "needs_migration": false,
  "schema_version": 5,
  "counts": {
    "active": 3,
    "blocked": 1,
    "review_pending": 0,
    "done": 2,
    "next_actionable": 2
  }
}
```

`counts.active` includes `ready`, `in_progress`, `paused`, `blocked`, and
`review_pending`; it excludes terminal `done` and `cancelled` tasks.

### `task add`

Register one explicit task in a database previously prepared with `db init`.

```powershell
python scripts/taskgov.py task add --repo <target-project> --title "Update docs" --kind optional --priority normal --json
```

Useful options:

- `--description`
- `--kind sequential|optional`
- `--lane`
- `--order`
- `--priority low|normal|high|urgent`
- `--status ready|in_progress|blocked|review_pending|cancelled`
- `--blocked-reason`
- `--review-tier 0|1|2`
- `--verification`
- `--tags`

`data`: `task`, `event`.

An initial status of `done` is prohibited. Add the task in another supported
initial state, then complete it with `task edit --status done` so the normal
verification, review, and completion-evidence gates are enforced. Attempting
`task add --status done` returns `initial_done_forbidden` before any task or
event is stored.

An initial status of `paused` is also prohibited and returns
`initial_paused_forbidden`. `paused` is an intentional hold for work that has
already entered `in_progress` or `review_pending`.

For sequential tasks, initial `in_progress` or `review_pending` and any
insertion into an existing lane must preserve the shared predecessor rule.
Every earlier same-lane task must be `done` or `cancelled` before later work may
be active or under review.

### `task list`

Return compact filtered task rows.

```powershell
python scripts/taskgov.py task list --repo <target-project> --status ready --limit 20 --json
```

Filters:

- `--status`
- `--kind`
- `--lane`
- `--priority`
- `--tag`
- `--limit`
- `--include-done`

`data`: `tasks`, `count`, `limit`.

### `task next`

Return ready work candidates without loading all history.

```powershell
python scripts/taskgov.py task next --repo <target-project> --limit 5 --json
```

Filters:

- `--kind`
- `--lane`
- `--priority`
- `--limit`, default `5`

Selection rules:

- Include only `status=ready`.
- Include ready optional tasks directly.
- Include ready sequential tasks only when earlier same-lane tasks are `done` or
  `cancelled`.
- Exclude `in_progress`, `paused`, `blocked`, `review_pending`, `done`, and
  `cancelled`.

`data`: `tasks`, `count`, `limit`, `selection_rules`.

### `task current`

Rediscover work that has already started, is under review, is intentionally
paused, or is blocked. This inspection command is read-only and does not infer
working-tree freshness or staleness.

```powershell
python scripts/taskgov.py task current --repo <target-project> --limit 20 --json
```

`data`:

```json
{
  "tasks": [
    {
      "task_id": "tg_task_example",
      "status": "in_progress",
      "latest_event": {},
      "suggested_next_action": "continue the task and inspect its latest event"
    }
  ],
  "count": 1,
  "limit": 20,
  "statuses": ["in_progress", "review_pending", "paused", "blocked"]
}
```

Each task contains the normal public task fields plus its latest event and a
deterministic status-based suggested action. Ordering is status
(`in_progress`, `review_pending`, `paused`, `blocked`), priority, newest
`updated_at`, then `task_id`. Same-second latest events use event row order as a
private tie-breaker. `--limit` defaults to `20` and is capped at `100`.

### `task show`

Show one task plus recent event history and suggested next action.

```powershell
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

`data`: `task`, `events`, `suggested_next_action`, `review_evidence`.

`task` includes typed completion evidence and the legacy commit projection:

- `completion_commit_required`
- `completion_commit_hash`
- `completion_evidence_kind`
- `completion_evidence_revision`
- `completion_evidence_reason`
- `external_revision_approved`

`review_evidence` is a bounded structured projection containing the current
target and generation, tier gate/pass/fallback state, receipt and open-finding
counts, `changes_requested_current_generation`, blocking findings, and at most
ten recent receipts and findings. It never contains raw review transcripts or
private reasoning.

### `task edit`

Update task state or metadata.

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for ..." --json
```

Editable options:

- `--title`
- `--description`
- `--kind`
- `--lane`
- `--order`
- `--priority`
- `--status`
- `--blocked-reason`
- `--pause-reason`
- `--review-tier`
- `--review-tier-change-reason <summary>`: sanitized rationale required only
  when lowering the review tier.
- `--verification`
- `--tags`
- `--add-note`
- `--completion-commit-hash <revision>`: Git-only compatibility alias. Verify
  an existing commit read-only and store its canonical full object ID.
- `--completion-evidence-kind <kind>`: explicitly select `git_commit`,
  `external_revision`, or `commit_not_required`.
- `--completion-revision <revision>`: revision for explicit Git or external
  evidence.
- `--completion-evidence-reason <summary>`: concise sanitized reason required
  for external evidence.
- `--external-revision-approved`: explicitly acknowledge that the external
  durable source is approved instead of target Git history when available.
- `--commit-not-required`: explicitly mark that no managed materials changed;
  compatibility alias for `commit_not_required` evidence.
- `--verification-complete`: record a concise command-time confirmation that
  required verification passed or has an approved exception.
- `--review-complete`: record a concise command-time confirmation that the
  required review gate passed or has a valid fallback.

`data`: `task`, `changed_fields`, `event`.

Only `in_progress` or `review_pending` tasks may move to `paused`, and the
transition requires a concise sanitized `--pause-reason`. A paused task resumes
to `in_progress`, which clears the current `pause_reason`; the transition event
retains the bounded historical reason. Unsupported pause/resume paths return
`invalid_status_transition`.

Sequential transitions to `in_progress`, `review_pending`, or `done` require
all earlier same-lane tasks to be `done` or `cancelled`. The same predicate is
used by `task next`. Adds and edits that change kind, lane, order, or status
also validate both affected lanes and return
`sequential_predecessor_incomplete` without storing a success event when the
result would place incomplete work before an already advanced task.
Lane values are trimmed consistently for add, edit, list, and next. Explicit
and auto-appended orders must fit SQLite's signed 64-bit integer range;
out-of-range input returns structured `invalid_argument` output without a
Python traceback.

Completion evidence spellings are mutually exclusive. Git evidence is resolved
with a shell-free, read-only `git rev-parse --verify --end-of-options
<revision>^{commit}` call. Missing, ambiguous, option-shaped, and non-commit
revisions fail; unique short hashes and annotated tags are stored as the
canonical full commit ID. External evidence always requires its explicit kind,
revision, reason, and approval. Changing evidence kind clears fields not valid
for the new kind.

At the done transition, stored Git evidence and any Git review target are
resolved again. `git_commit` completion must equal the current canonical Git
review target; `external_revision` completion must equal the current external
review target. A `diff_fingerprint` cannot close either revision-bearing kind
because schema version 5 defines no deterministic diff-to-commit/tree mapping;
retarget the final revision and obtain fresh receipts. `commit_not_required`
requires a diff target.

`task edit --status done` requires both `--verification-complete` and
`--review-complete`. It also requires either a stored or newly supplied
valid typed evidence record. `legacy_unverified` evidence is preserved for
historical completed tasks but cannot satisfy any new done transition.

Every edit to a `done` task is rejected with `task_done_immutable`, including a
status change, note, review-tier edit, confirmation, or completion-evidence
edit. Done is terminal; create a new task for follow-up work. Lowering a tier is
allowed only while both existing and resulting status are `ready`,
`in_progress`, `paused`, or `blocked`. It requires exactly one
`task_added_review_unstarted` event, no `review_started` event, an empty review
target at generation `0`, and `--review-tier-change-reason`. Missing, duplicate,
or legacy marker state fails closed; pause/resume or leaving review does not
clear the latch. A downgrade cannot be combined with completion evidence or
verification/review completion confirmations. Tier upgrades need no rationale.

Complete with a Git commit:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-commit-hash <hash> --json
```

Complete with an explicitly approved external revision:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-evidence-kind external_revision --completion-revision <revision> --completion-evidence-reason "Approved external release" --external-revision-approved --json
```

Complete when no managed materials changed:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --commit-not-required --json
```

`taskgov` records and validates evidence only. It does not create commits or
mutate the target project. For Git projects, inspect changed materials from the
stored hash:

```powershell
git show --name-only <completion_commit_hash>
```

### Review evidence commands

Set or replace the current review target:

```powershell
python scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_commit --revision <revision> --json
```

Allowed target kinds are `git_commit`, `diff_fingerprint`, and
`external_revision`. Git targets are verified read-only and stored canonically.
A diff fingerprint must be `sha256:` followed by 64 lowercase hexadecimal
characters. Every set advances the monotonic target generation, including
A-to-B-to-A changes.

Add a sanitized receipt for the current target generation:

```powershell
python scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <stable-reviewer-key> --kind independent --verdict pass --summary "No blocking findings" --json
```

Receipt kinds are `independent`, `self_review_fallback`, and `not_required`;
verdicts are `pass`, `changes_requested`, and `not_required`. One reviewer key
may record only one receipt per target generation. Tier 1 requires one distinct
independent PASS, or a documented self-review PASS when independent tooling is
unavailable. Tier 2 requires two independent PASS receipts, unless a Tier 2
self-review PASS is explicitly user-approved because independent tooling is
unavailable. Tier 0 uses a `not_required` receipt with a rationale. The normal
Tier 2 path therefore uses two LLM review judgments; each meaningful fix/new
target requires two fresh judgments. Deterministic gate checks add none.
A `changes_requested` receipt in the current generation blocks completion even
when sufficient PASS receipts exist; a newer target generation and fresh
review are required.

Record and resolve findings:

```powershell
python scripts/taskgov.py review finding add --repo <target-project> <task-id> --receipt-id <receipt-id> --severity high --summary "Concise finding" --json
python scripts/taskgov.py review finding resolve --repo <target-project> <finding-id> --resolution "Concise resolution" --json
```

Severity is `high`, `medium`, or `low`. Open high/medium findings block `done`.
After resolving a high/medium finding, completion still requires a newer target
generation and fresh qualifying receipts. Review write commands require an
initialized current-schema database and reject `--read-only` before writing.
They also reject target, receipt, finding, and resolution mutations while the
owning task is `done`, returning `task_done_immutable`.

All mutable payload flags shown above remain required by the command contract
and are labeled required in `--help`, but their presence is validated after
owner lookup. This ensures a done owner returns `task_done_immutable` even when
a payload flag is omitted. A non-done owner receives the normal command-
specific JSON/text validation envelope, with no database write.

### `web export`

Render one self-contained, offline Task Viewer snapshot from the initialized
SQLite database:

```powershell
python scripts/taskgov.py web export --repo <target-project> --json
```

The default output is generated skill-local state:

```text
<installed-skill-root>/state/projects/<project-id>/viewer/task-viewer.html
```

Use `--read-only` to validate the database, snapshot, template, and resolved
output path without creating a directory or file. Use `--output <path>` only
after the user explicitly approves that complete destination. Explicit parents
must already exist and the filename must end in `.html` or `.htm`. An explicit
path inside the governed project is accepted only under the installed skill's
generated `state/` directory.

`data`:

```json
{
  "output_path": "C:\\path\\to\\task-viewer.html",
  "written": true,
  "replaced": false,
  "task_count": 3,
  "event_count": 7,
  "generated_at": "2026-07-17T00:00:00Z",
  "snapshot_version": 3
}
```

Snapshot version 3 includes the task-show typed completion fields and the same
bounded structured review-evidence projection. The generated file is stale
until `web export` is explicitly run again. The
command does not start a server, open a browser, edit tasks, or write database
events. Databases using WAL mode are rejected before the snapshot connection so
even a preview does not create SQLite sidecar files.

After command and output resolution, every `web.export` error preserves this
fixed `data` shape. `output_path` is `null` only when output resolution itself
failed:

```json
{
  "output_path": null,
  "written": false,
  "replaced": false,
  "task_count": 0,
  "event_count": 0,
  "generated_at": null,
  "snapshot_version": 3
}
```

`output_path_invalid` and `output_parent_missing` use exit code 1.
`output_write_failed`, database readiness failures, WAL-state rejection, and
unexpected snapshot/template failures use exit code 2.

## Error Codes

Known error codes include:

- `invalid_argument`
- `invalid_status`
- `invalid_kind`
- `invalid_priority`
- `invalid_review_tier`
- `blocked_reason_required`
- `pause_reason_required`
- `verification_required`
- `review_required`
- `commit_required`
- `completion_commit_conflict`
- `completion_evidence_conflict`
- `git_commit_not_found_or_ambiguous`
- `external_revision_approval_required`
- `review_target_required`
- `review_receipts_insufficient`
- `review_finding_unresolved`
- `review_receipt_mismatch`
- `review_receipt_already_recorded`
- `review_target_mismatch`
- `invalid_review_evidence`
- `initial_done_forbidden`
- `initial_paused_forbidden`
- `task_done_immutable`
- `invalid_status_transition`
- `sequential_predecessor_incomplete`
- `privacy_rejected`
- `not_found`
- `db_not_initialized`
- `migration_required`
- `project_mismatch`
- `output_path_invalid`
- `output_parent_missing`
- `output_write_failed`
- `internal_error`

Some commands define an explicit empty `data` shape for specific error paths.
For example, `task.next` errors return empty `tasks`, `count`, `limit`, and
`selection_rules`. Other validation errors may use the generic empty object
shape unless the command contract or tests say otherwise.
