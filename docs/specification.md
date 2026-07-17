# task-governance-tool MVP Specification

Status: formal MVP specification baseline.

This document defines the first product contract for `task-governance-tool`.
It supersedes `plan.md` for MVP product behavior. `docs/implementation-roadmap.md`
governs implementation order and execution-unit boundaries. `plan.md` remains
the decision log and open-issue holding area.

## Product Goal

`task-governance-tool` helps Codex and the user run project work without loading
large task-status Markdown files into context. The MVP replaces the practical
functions of a large `TASK_STATUS.md`: register tasks, inspect current work,
select next actionable work, record blockers, and keep concise local task
history.

The tool must stay local-first, project-doc-respecting, and non-authoritative.
Target project governing documents remain the source of truth for project
decisions.

## MVP Scope

The MVP includes:

- A Codex skill named `task-governance-tool`.
- A Python stdlib-first CLI named `taskgov`.
- A SQLite state store scoped to one governed project per installed skill copy
  by default.
- Task registration and inspection commands:
  - `taskgov db init`
  - `taskgov db status`
  - `taskgov task add`
  - `taskgov task list`
  - `taskgov task next`
  - `taskgov task show`
  - `taskgov task edit`
- JSON output for Codex and concise text output for humans.
- Project-scoped skill-local generated runtime state under the installed skill
  folder.
- Offline operation by default.

## Non-Goals For MVP

The MVP does not include:

- Importing tasks from Markdown or planning documents.
- Draft/approval workflows such as `task approve`.
- A standalone dependency graph or `task depend`.
- Persistent project profile authoring or `profile register`.
- Verification-run recording beyond short task fields.
- Review request generation.
- Creating Git commits, branches, PRs, issue comments, or other target-project
  mutation. A later completion-evidence extension may require and record an
  existing commit identifier, but `taskgov` must not create commits by default.
- Live dashboards, services, network sync, or cloud workflows. The approved
  post-MVP static Task Viewer extension below is not a live dashboard or
  service.
- Raw command-output retention.

## Skill Package Requirements

The installable skill folder name and `SKILL.md` frontmatter `name` must be
`task-governance-tool`.

The MVP is intended for project-scoped installation. Install a separate copy of
the skill into each governed project that needs task tracking:

```text
<target-project>/.agents/skills/task-governance-tool/
```

User-wide installation is not recommended for normal MVP use because it can
blur task state across projects. Use a user-wide install only for explicit local
experimentation, and prefer an explicit `--db` path if doing so.

The skill package should contain only files needed by Codex to use the skill:

```text
task-governance-tool/
  SKILL.md
  agents/
    openai.yaml
  scripts/
    taskgov.py
    task_governance_tool/
  references/
    task_workflow.md
    cli_contracts.md
```

The skill package may create generated local runtime state after installation:

```text
task-governance-tool/
  state/
    projects/
      <project-id>/
        taskgov.sqlite
```

`state/` is not part of the static skill package and must not be committed or
exported.

Runtime code required by `scripts/taskgov.py` must be included inside the skill
package so the installed skill is self-contained.

After the approved static Task Viewer extension is implemented, the package
must additionally include:

```text
task-governance-tool/
  assets/
    task-viewer.template.html
  scripts/
    task_governance_tool/
      viewer.py
```

The bundled template is static runtime input. Generated `task-viewer.html`
snapshots remain under `state/` and are not part of the package.

## Data Location

Default database path:

```text
<installed-skill-root>/state/projects/<project-id>/taskgov.sqlite
```

With the recommended project-scoped install, this resolves under:

```text
<target-project>/.agents/skills/task-governance-tool/state/projects/<project-id>/taskgov.sqlite
```

`<project-id>` must be deterministic from the canonical target project path. It
should use a sanitized target project directory basename plus a short stable
SHA-256 hash of the canonical path, for example:

```text
kurakoma-a1b2c3d4e5f6
```

The CLI must support `--db` to override the default path.

## Task Model

The MVP task record includes:

- `task_id`: stable ID such as `tg_task_...`.
- `project_id`: deterministic project ID.
- `title`: required short title.
- `description`: optional detail.
- `kind`: `sequential` or `optional`.
- `lane`: optional grouping string; normally required for ordered sequential
  work.
- `lane_order`: integer order within a lane.
- `priority`: `low`, `normal`, `high`, or `urgent`.
- `status`: `ready`, `in_progress`, `blocked`, `review_pending`, `done`, or
  `cancelled`.
- `blocked_reason`: required when status is `blocked`.
- `review_tier`: integer `0`, `1`, or `2`.
- `verification`: short verification expectation or command label.
- `tags`: comma-separated labels.
- `created_at`, `updated_at`, and optional `completed_at` timestamps.

The MVP may store task notes and state changes in a concise task event history.

## Required Post-MVP Extension: Completion Commit Gate

The next required extension after the MVP must make task completion auditable
without adding a heavy material-tracking schema.
It supersedes the current lightweight `task edit --status done` behavior once
implemented.

A task may be marked `done` only after all of these gates are satisfied:

- Required verification for the task has passed or has an explicit documented
  user-approved exception.
- Required sub-agent review has completed under the same tiered review rules
  used by this project: Tier 2 requires two independent review passes when
  review tooling is available; Tier 1 requires one independent review or the
  documented fallback when tooling is unavailable; Tier 0 may skip review only
  for purely mechanical changes.
- If Tier 2 review tooling is unavailable, the strongest feasible documented
  self-review must be run and the user must explicitly approve treating that
  fallback as completion evidence before the task can be marked `done`.
- No valid high or medium review finding remains unresolved.
- The commit gate has passed according to the task's commit requirement fields.

Managed materials are source-controlled files or user-approved durable assets
whose final state should be traceable after task completion. Generated local
runtime state, caches, logs, temporary files, SQLite databases, and ignored
scratch artifacts are not managed materials unless a governing document or user
explicitly says they are.

The database must keep commit evidence intentionally simple. Store commit state
directly on the task row:

- `completion_commit_required`: boolean-like value, default true.
- `completion_commit_hash`: concise commit hash or unique revision identifier,
  empty by default.

Completion commit rules:

- If `completion_commit_required=true`, `completion_commit_hash` is required
  before the task can be marked `done`.
- If no managed materials changed, the user or agent must explicitly set
  `completion_commit_required=false`; then `completion_commit_hash` must stay
  empty. This is the explicit commit-not-required decision.
- `completion_commit_required=false` with a non-empty
  `completion_commit_hash` is invalid.
- A Git commit hash is preferred for Git projects. For non-Git managed
  materials, a user-approved unique revision ID may be stored in
  `completion_commit_hash`.
- The hash or revision ID must be unique enough to identify the final material
  state in the target project's durable history.

The database does not store changed material paths in this simplified design.
To trace changed materials for a completed historical task, read the task's
`completion_commit_hash` and inspect the target project's version-control
history, for example:

```powershell
git show --name-only <completion_commit_hash>
```

Valid review evidence by tier:

- Tier 2: `passed` with at least two independent reviewers, or
  `unavailable_fallback` only when review tooling is unavailable, the strongest
  feasible documented self-review was completed, and the user explicitly
  approved the fallback.
- Tier 1: `passed` with at least one independent reviewer, or
  `unavailable_fallback` with a documented self-review when review tooling is
  unavailable.
- Tier 0: `not_required` is valid only for purely mechanical changes with no
  behavior, schema, API, privacy, setup, persistence, or documentation contract
  risk.

This simplified extension does not add separate verification or review tables.
The `done` transition must still require explicit command-time confirmation
that required verification and review gates have passed or have an approved
fallback. The CLI may record those confirmations as concise task events, but it
must not store full transcripts or raw command output.

Completion transition interface:

- `task edit --status done` must require `--verification-complete`.
- `task edit --status done` must require `--review-complete`.
- `--verification-complete` means required verification passed or a documented
  user-approved exception exists.
- `--review-complete` means the required review gate passed, or a valid
  documented fallback/not-required decision exists for the task's review tier.
- `--completion-commit-hash <hash>` records the commit hash or unique revision
  ID used when `completion_commit_required=true`.
- `--commit-not-required` explicitly sets `completion_commit_required=false`
  for a task with no managed material changes.

The extension must not store raw diffs, raw command output, full review
transcripts, full prompts, secrets, stack traces, environment dumps, or large
logs by default.

When this extension is implemented, `task edit --status done` must reject
completion with structured errors if required verification, review, or commit
state is missing or inconsistent. The error codes should include
`verification_required`, `review_required`, `commit_required`, and
`completion_commit_conflict` unless a later approved CLI contract chooses
narrower names.

## Approved Post-MVP Extension: Static Task Viewer

The tool must provide a user-facing, non-server task viewer after the core task
and completion flows are stable. The application name is `Task Viewer` and the
CLI entry point is:

```powershell
taskgov web export --repo <target-project> [--output <html-path>]
```

This extension is a generated static snapshot, not a live dashboard. The CLI
reads the existing SQLite database in read-only mode, embeds a bounded task
snapshot into one self-contained HTML file, and exits. Opening the file must not
require Python, a local HTTP server, a browser extension, a network connection,
or direct browser access to SQLite.

### Export Behavior

`web export` must:

- require an initialized, current-schema database and preserve the existing
  `db_not_initialized`, `migration_required`, and `project_mismatch` behavior
- open SQLite read-only and write no database rows, task events, tool events, or
  migrations
- reject active WAL sidecars and persistent WAL journal mode before the viewer
  snapshot connection so export and `--read-only` create no SQLite sidecars
- export every task status, including `done` and `cancelled`, while allowing
  the browser UI to hide terminal tasks by default
- include all task fields exposed by `task show`, including
  `completion_commit_required` and `completion_commit_hash`
- include at most the 10 most recent task events per task, newest first
- preserve current `task list`, `task show`, and other CLI JSON contracts
- replace the selected HTML file atomically so a failed render does not leave a
  partial viewer
- make snapshot age visible through a UTC `generated_at` timestamp in the
  rendered application

The default output path is generated skill-local runtime state:

```text
<installed-skill-root>/state/projects/<project-id>/viewer/task-viewer.html
```

The default viewer directory may be created by `web export`. An explicit
`--output` path is a separate file-write destination and requires explicit user
approval when Codex invokes it. Its parent directory must already exist; the
command must not create arbitrary explicit-output parent directories. The path
must end in `.html` or `.htm`. For either default or explicit output, an
existing directory, symbolic link, or non-regular-file destination must be
rejected.

An explicit output that resolves inside the canonical target project must stay
under the installed skill's generated `state/` directory. Other explicit
destinations inside the target project must be rejected with
`output_path_invalid`, even when their parent exists. A user-approved explicit
destination outside the target project remains allowed.

An explicit `--db` changes only the SQLite source. It must not move the default
HTML output away from the installed skill's generated state directory.

Generating or regenerating either default or explicit output requires explicit
user intent in the current task. A request only to inspect or summarize task
state does not authorize an HTML write. Codex may use `--read-only` to preview
the resolved output and counts before that intent is granted.

`--read-only` acts as a dry preview for this command. It must read and validate
the database and template, report the resolved output path and snapshot counts,
set `written=false`, and create or replace no HTML or directories.

The command must not open a browser automatically. Browser opening remains a
separate user action.

### Snapshot Contract

The embedded snapshot is an internal, versioned data contract with this
top-level shape:

```json
{
  "snapshot_version": 1,
  "generated_at": "2026-01-01T00:00:00Z",
  "project": {
    "project_id": "example-a1b2c3d4e5f6",
    "display_name": "example"
  },
  "source_schema_version": 2,
  "counts": {
    "total": 0,
    "ready": 0,
    "in_progress": 0,
    "blocked": 0,
    "review_pending": 0,
    "done": 0,
    "cancelled": 0
  },
  "tasks": []
}
```

Each task contains the `task show` task fields plus an `events` array. The
snapshot must not add the canonical repository path or database path. Stored
task text remains subject to the existing write-time privacy checks.

The snapshot JSON must be encoded before insertion into the HTML so stored task
text cannot terminate a script element or inject markup. Browser rendering
must place stored text through text-only DOM APIs rather than interpreting it as
HTML.

### CLI Output Contract

The stable command name is `web.export`. A successful JSON result uses the
normal envelope and includes:

```json
{
  "output_path": "C:/.../task-viewer.html",
  "written": true,
  "replaced": false,
  "task_count": 0,
  "event_count": 0,
  "generated_at": "2026-01-01T00:00:00Z",
  "snapshot_version": 1
}
```

`written` must be false for `--read-only`. Text output must state the resolved
path, task count, generation timestamp, and whether the file was written or
only previewed.

`event_count` is the number of bounded recent-event rows actually embedded
across all exported tasks.

After command and output-path resolution, error results must preserve this
command-specific `data` shape with `written=false`, `replaced=false`, zero
counts, `generated_at=null`, and the resolved `output_path` when available. If
output-path resolution itself fails, `output_path` is null. The error result
retains `snapshot_version=1` after command resolution.

New user-correctable error codes are:

- `output_path_invalid`
- `output_parent_missing`

An operating-system write failure uses `output_write_failed` and exit code 2.
An absent or malformed bundled template is an `internal_error`.

### Viewer Experience

The viewer must be a read-only operational interface. It must provide:

- project identity and snapshot generation time
- status totals for all six task statuses
- text search across task ID, title, description, lane, tags, and commit hash
- status, kind, lane, priority, and tag filters
- a default view that emphasizes active tasks while keeping terminal history
  available
- deterministic task ordering consistent with `task list`
- a task detail view for description, blocker, verification, review tier,
  completion commit state, timestamps, tags, and recent events
- responsive desktop and mobile layouts, keyboard access, visible focus,
  associated form labels, and readable contrast

The viewer must not provide task-edit, completion, commit, or database-write
controls. It must not imply live refresh; the displayed `generated_at` value is
the freshness boundary.

### Viewer Safety And Non-Goals

The generated HTML must use bundled inline HTML, CSS, and JavaScript only. It
must not use a CDN, external font, analytics, telemetry, fetch/XHR, WebSocket,
service worker, cookie, or local-storage persistence. A restrictive content
security policy must disable network connections and unrelated resource loads
while permitting the bundled inline application code. The exact accepted
policy and the reason for its inline-script/style exception are defined in
`docs/design.md` and must be browser-tested through `file://`.

This extension does not include:

- direct SQLite access from browser JavaScript or a file picker
- live database watching or automatic regeneration
- task registration or editing in the browser
- a local or remote HTTP server
- sharing, synchronization, authentication, or multi-user coordination
- browser launch without an explicit user request

### Viewer Acceptance Criteria

The extension is acceptable when:

- `web export` and `web export --read-only` satisfy their JSON and write-safety
  contracts against temporary databases
- the generated file opens through `file://` and remains useful with networking
  unavailable
- all statuses, completion commit fields, and bounded recent events render
  correctly
- stored HTML-shaped text is rendered as text and cannot execute
- default and explicit output-path rules are covered by tests
- existing CLI contracts and the full offline test suite remain green
- desktop and mobile browser checks show no blank view, overlap, clipped
  controls, or unreadable task content
- the installable skill remains self-contained and release artifacts include
  the required viewer asset but exclude generated viewer snapshots

### Approved Follow-Up Requirement: Default-Browser Launch

After an explicit user request to display the Task Viewer, the tool must be
able to open a generated Task Viewer HTML file directly in the operating
system's configured default browser.

This capability must preserve the static-snapshot boundary: opening the file
must not add browser-to-SQLite access, a local server, live refresh, automatic
regeneration, or browser-side task mutation. A generic task-state inspection,
an export-only request, or `web export --read-only` must not launch a browser.

This requirement does not yet approve a command name, option, interaction
flow, regeneration policy, error contract, or implementation task. Those
decisions must be added to `docs/design.md` and
`docs/implementation-roadmap.md` before implementation. Until then, the
existing `web export` contract remains generation-only and does not launch a
browser.

## Task Ordering

Task selection must support both sequence-sensitive and free-order work.

- `optional` tasks are actionable when their status is `ready`.
- `sequential` tasks are actionable when their status is `ready` and all earlier
  tasks in the same `lane` are `done` or `cancelled`.
- A blocked sequential lane must not hide ready optional tasks or ready tasks in
  other lanes.

The MVP uses `lane` plus `lane_order` instead of a general dependency graph.

## CLI Requirements

All MVP commands must support:

- `--repo`: target project root; default current directory.
- `--db`: explicit SQLite path override.
- `--json`: machine-readable output for Codex.
- `--read-only`: prohibit database creation, migration, or writes. Inspection
  commands behave as read-only even when this flag is omitted.

Inspection commands must not create or migrate databases by default. Commands
that write to the task-governance-tool database must state what they recorded in
both JSON and text output.

### `taskgov db init`

Purpose: explicitly create or migrate the current project database.

Required output:

- database path
- project ID
- whether the database was created
- migration versions applied
- final schema version

### `taskgov db status`

Purpose: show whether the current task database is usable.

This command is read-only by default. If the database does not exist or needs a
schema migration, it must report that state without creating or migrating the
database. Use `taskgov db init` to create or migrate the database.

Required output:

- database path
- schema version
- whether the database exists
- whether initialization is needed
- whether migration is needed
- project ID
- active task count
- blocked task count
- review-pending task count
- done task count
- next-actionable task count

### `taskgov task add`

Purpose: register one explicit task as current state.

Required arguments:

- `--title`

Optional arguments:

- `--description`
- `--kind`, default `optional`
- `--lane`
- `--order`
- `--priority`, default `normal`
- `--status`, default `ready`
- `--blocked-reason`, required when initial `--status` is `blocked`
- `--review-tier`, default `1` unless project rules later say otherwise
- `--verification`
- `--tags`

Adding a task is an explicit registration action. The MVP does not create draft
tasks. If a sequential task omits `--lane` or `--order`, the CLI may store a
deterministic default lane and append order; command output must include the
stored `lane` and `lane_order` so the auto-filled ordering is visible.

If `task add` sets initial `--status blocked`, it must require
`--blocked-reason`. The CLI must reject blocked task creation without a blocked
reason before any row is stored.

### `taskgov task list`

Purpose: return compact filtered task lists.

Supported filters:

- `--status`
- `--kind`
- `--lane`
- `--priority`
- `--tag`
- `--limit`, default around 20
- `--include-done`

### `taskgov task next`

Purpose: return ready work candidates without loading all task history.

Selection rules:

- Include only `status=ready`.
- Include ready `optional` tasks directly.
- Include ready `sequential` tasks only when earlier tasks in the lane are
  complete or cancelled.
- Exclude `in_progress`, `blocked`, `review_pending`, `done`, and `cancelled`.
- Supported filters are `--kind`, `--lane`, `--priority`, and `--limit`.
- Default limit is `5`.
- Priority order is `urgent`, `high`, `normal`, `low`.
- Sort by priority rank, lane, lane order with nulls last, creation time, and
  `task_id` for deterministic ties.

### `taskgov task show`

Purpose: show one task and immediate context.

Required output:

- task fields
- recent notes or events
- timestamps
- suggested next action

### `taskgov task edit`

Purpose: update task state or metadata.

Editable fields:

- `--title`
- `--description`
- `--kind`
- `--lane`
- `--order`
- `--priority`
- `--status`
- `--blocked-reason`
- `--review-tier`
- `--verification`
- `--tags`
- `--add-note`

When setting `--status blocked`, `--blocked-reason` is required.

`task block` and `task done` aliases are postponed. Use `task edit` in the MVP.

## JSON Output

JSON output must be stable enough for Codex to consume. Each command should emit
a top-level object with:

- `ok`: boolean.
- `command`: command name.
- `project_id`: when applicable.
- `db_path`: when applicable.
- `data`: command-specific payload.
- `errors`: list of structured errors.
- `warnings`: list of structured warnings.

Human-readable output should be concise and should not replace JSON contracts.

Command names must be stable:

- `db.init`
- `db.status`
- `task.add`
- `task.list`
- `task.next`
- `task.show`
- `task.edit`

Exit codes:

- `0`: success.
- `1`: validation or user-correctable command error.
- `2`: database, migration, or unexpected tool error.

Timestamps in JSON must use UTC ISO-8601 strings. Paths should be emitted as
normalized absolute strings for local display; IDs must not encode private path
details.

Required `data` payloads:

- `db.init`: `created`, `migrations_applied`, `schema_version`.
- `db.status`: `exists`, `needs_init`, `needs_migration`, `schema_version`,
  `counts`.
- `task.add`: `task`, `event`.
- `task.list`: `tasks`, `count`, `limit`.
- `task.next`: `tasks`, `count`, `limit`, `selection_rules`.
- `task.show`: `task`, `events`, `suggested_next_action`.
- `task.edit`: `task`, `changed_fields`, `event`.

Task objects in JSON must use the same field names as the task model. The
`review_tier` value must be an integer, not a string.

In `db.status`, `counts.active` means tasks with status `ready`,
`in_progress`, `blocked`, or `review_pending`. It excludes `done` and
`cancelled`.

Required error codes:

- `invalid_argument`
- `invalid_status`
- `invalid_kind`
- `invalid_priority`
- `invalid_review_tier`
- `blocked_reason_required`
- `privacy_rejected`
- `not_found`
- `db_not_initialized`
- `migration_required`
- `project_mismatch`
- `internal_error`

## Privacy And Safety

The MVP must:

- Write only to the task-governance-tool SQLite database by default.
- Never modify target project source files, Git state, issues, PRs, or external
  services.
- Treat project-scoped installation and generated state creation as explicit
  user-approved setup/write actions. Generated state under
  `.agents/skills/task-governance-tool/state/` must be ignored or kept out of
  commits.
- Never store API keys, tokens, cookies, authorization headers, raw provider
  bodies, full private prompts, full chat logs, large raw diffs, raw stdout, raw
  stderr, stack traces, or environment dumps.
- Treat `state/` as generated local runtime state.
- Keep root copied reference material non-authoritative.

Free-form fields are allowed only as concise sanitized task metadata. The CLI
must reject obvious secret, log, or diff content before storing user text.
Warnings may be used only for non-stored borderline content or non-blocking
normalization notes.

MVP size limits:

- `title`: 200 characters.
- `description`: 4000 characters.
- `verification`: 500 characters.
- `tags`: 500 characters.
- `--add-note`: 2000 characters.
- event `summary`: 1000 characters.

The CLI must reject obvious secrets and raw dump patterns, including bearer
tokens, authorization headers, private key blocks, `password=`, `token=`,
`api_key=`, raw stack traces, raw stdout/stderr dumps, and large raw diffs.

## Acceptance Criteria

The MVP is acceptable when:

- The skill package metadata validates or passes the documented self-check.
- `taskgov db status` inspects a project database without creating or migrating
  it.
- `taskgov db init` creates or migrates a project database safely.
- `taskgov task add/list/show/edit/next` work against a temporary database.
- `task next` correctly works around blocked sequential lanes.
- JSON outputs are tested for shape and key fields.
- Free-form privacy rejection and size-limit behavior are tested.
- No command mutates target project source files or Git state.
- Generated SQLite databases and root copied references are ignored by Git.
