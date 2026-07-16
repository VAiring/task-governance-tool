# task-governance-tool MVP Design

Status: formal MVP design baseline.

This document describes the initial implementation design for the MVP specified
in `docs/specification.md`.

## Design Summary

The MVP is a small local-first Codex skill plus deterministic Python CLI. The
skill tells Codex when and how to use the tool. The CLI owns all structured task
state operations. A project-scoped installed skill copy owns one governed
project's default task state. The approved post-MVP Task Viewer extension adds
a generated, self-contained HTML snapshot without introducing a server or a
second state store.

The design intentionally avoids a general project-management system. It
implements the minimum state and query behavior needed to replace large
`TASK_STATUS.md` files.

## Repository Layout

The source repository should contain both the installable skill package and
normal development files:

```text
AGENTS.md
plan.md
docs/
  specification.md
  design.md
task-governance-tool/
  SKILL.md
  agents/
    openai.yaml
  assets/
    task-viewer.template.html
  scripts/
    taskgov.py
    task_governance_tool/
      __init__.py
      cli.py
      storage.py
      tasks.py
      selection.py
      viewer.py
  references/
    task_workflow.md
    cli_contracts.md
tests/
fixtures/
  task-status-mvp/
```

Generated local runtime state is created under the installed skill folder and is
ignored by Git:

```text
task-governance-tool/state/projects/<project-id>/taskgov.sqlite
```

When installed for normal use, the skill folder should live under the governed
project's repo-scoped skill directory:

```text
<target-project>/.agents/skills/task-governance-tool/
```

User-wide installation is discouraged for the MVP because this tool is meant to
replace one project's task-status file with one project-local task database.

## Skill Package Design

`SKILL.md` should stay concise. It should:

- Trigger on task planning, task status inspection, next-task selection,
  blocker handling, and MVP task-state updates.
- Instruct Codex to read target project governing docs separately.
- Instruct Codex to use `taskgov task next` and `taskgov task show` instead of
  loading large task-status files.
- Point to one-level references only when details are needed.
- Avoid advertising deferred behavior, including verification-run recording,
  review request generation, persistent profile authoring, dependency graphs,
  Git integration, or target-project mutation, until those features are
  implemented and documented.

`references/task_workflow.md` should describe the workflow for selecting,
starting, blocking, and completing tasks.

`references/cli_contracts.md` should describe command examples and JSON output
shapes.

`scripts/taskgov.py` should be the executable entry point usable from inside the
skill package. Runtime modules live under
`task-governance-tool/scripts/task_governance_tool/` so installed-package
behavior is self-contained. Tests may add `task-governance-tool/scripts/` to
`sys.path` when importing those modules.

## Python Module Boundaries

`task-governance-tool/scripts/task_governance_tool/cli.py`

- Parse command-line arguments.
- Resolve `--repo`, `--db`, and output mode.
- Call task and storage services.
- Format JSON/text output.

`task-governance-tool/scripts/task_governance_tool/storage.py`

- Resolve default database paths.
- Open SQLite connections.
- Apply schema migrations.
- Provide repository helpers.
- Enforce project metadata checks so an explicit `--db` path is not silently
  reused for a different target project.
- Avoid leaking raw `sqlite3` calls into feature modules.

`task-governance-tool/scripts/task_governance_tool/tasks.py`

- Validate task fields.
- Create, update, fetch, and list tasks.
- Append task event rows for meaningful changes.

`task-governance-tool/scripts/task_governance_tool/selection.py`

- Implement `task next` readiness rules.
- Keep sequential lane logic separate from generic list filtering.

`task-governance-tool/scripts/task_governance_tool/viewer.py`

- Assemble the versioned static-viewer snapshot from repository results.
- Load and validate the bundled viewer template.
- Encode snapshot JSON for safe HTML embedding.
- Render and atomically write the output file.
- Contain no raw SQLite queries; task and event reads remain in the task
  repository boundary.

`task-governance-tool/assets/task-viewer.template.html`

- Contain the complete offline HTML, CSS, and JavaScript application.
- Use one unique snapshot placeholder populated by `viewer.py`.
- Depend on no external files, packages, fonts, CDNs, or network calls.

`tests/`

- Exercise CLI contracts, storage migrations, repository behavior, and next-task
  selection.

## Database Path Resolution

Inputs:

- `--db`: explicit override.
- `--repo`: target project root; default current directory.
- installed skill root: inferred from `scripts/taskgov.py` location for the
  self-contained skill package. In normal use, this is the target project's
  `.agents/skills/task-governance-tool` directory.

Resolution:

1. Canonicalize `--repo`.
2. Compute `project_id` from the canonical repo path.
3. If `--db` is provided, use that path.
4. Otherwise use:

```text
<installed-skill-root>/state/projects/<project-id>/taskgov.sqlite
```

With the recommended project-scoped install, the default database path stays
under the target project's installed skill copy:

```text
<target-project>/.agents/skills/task-governance-tool/state/projects/<project-id>/taskgov.sqlite
```

Project ID algorithm:

1. Take the target project directory basename.
2. Lowercase it.
3. Replace non `[a-z0-9-]` characters with `-`.
4. Trim repeated or leading/trailing hyphens.
5. Normalize the canonical project path before hashing. On Windows, normalize
   case and handle drive-letter and UNC path forms consistently.
6. Hash the canonical project path with SHA-256.
7. Append the first 12 hex characters.

Example:

```text
kurakoma-a1b2c3d4e5f6
```

The implementation should create parent directories only for the selected
database path, not for arbitrary target project paths.

When opening an existing database, compare stored `project_meta.project_id`
with the computed project ID. If they differ, fail with `project_mismatch`
unless a later documented override is added.

## SQLite Schema

The MVP schema should be small and migration-managed from the start.

```sql
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE project_meta (
  project_id TEXT PRIMARY KEY,
  canonical_path_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE tasks (
  task_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('sequential', 'optional')),
  lane TEXT NOT NULL DEFAULT '',
  lane_order INTEGER,
  priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
  status TEXT NOT NULL CHECK (status IN (
    'ready',
    'in_progress',
    'blocked',
    'review_pending',
    'done',
    'cancelled'
  )),
  blocked_reason TEXT NOT NULL DEFAULT '',
  review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
  verification TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  CHECK (status != 'blocked' OR blocked_reason != ''),
  CHECK (kind != 'sequential' OR lane != ''),
  CHECK (kind != 'sequential' OR lane_order IS NOT NULL)
);

CREATE TABLE task_events (
  task_event_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE tool_events (
  tool_event_id TEXT PRIMARY KEY,
  project_id TEXT,
  command TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

Recommended indexes:

```sql
CREATE INDEX idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX idx_tasks_project_kind ON tasks(project_id, kind);
CREATE INDEX idx_tasks_project_lane_order ON tasks(project_id, lane, lane_order);
CREATE UNIQUE INDEX idx_tasks_project_lane_order_unique
  ON tasks(project_id, lane, lane_order)
  WHERE kind = 'sequential';
CREATE INDEX idx_task_events_task_created ON task_events(task_id, created_at);
```

The MVP should store tags as a comma-separated string. A normalized tag table is
deferred.

## Command Flow

Write commands are `db init`, `task add`, and `task edit`. They should follow
this flow:

1. Parse arguments.
2. Resolve repo and database path.
3. If `--read-only` is present, reject the command before creating,
   migrating, or writing.
4. Initialize or migrate the database when needed.
5. Validate inputs.
6. Execute repository operation.
7. Emit JSON or concise text.
8. Return a stable exit code.

Write commands must clearly say what they recorded in text mode and in JSON
payloads.

Inspection commands are `db status`, `task list`, `task next`, and `task show`.
They must not create, migrate, or write to the database by default. A missing
database should produce `db_not_initialized`; a database requiring migration
should produce `migration_required`; `db status` should report those states
without changing the database.

The post-MVP `web export` command is a hybrid export command: it reads the
database through the inspection path but, only after explicit user intent,
writes one generated HTML snapshot. Its `--read-only` mode is the no-write
inspection/preview path. It follows the dedicated output rules in the Static
Task Viewer Design section and never writes the database.

## JSON Envelope

Required JSON shape:

```json
{
  "ok": true,
  "command": "task.next",
  "project_id": "example-a1b2c3d4e5f6",
  "db_path": "C:/.../taskgov.sqlite",
  "data": {},
  "warnings": [],
  "errors": []
}
```

The `command` value and each command-specific `data` payload must match the
stable names and required payloads in `docs/specification.md`.

Errors should use stable codes:

```json
{
  "code": "invalid_status",
  "message": "status must be one of: ready, in_progress, blocked, review_pending, done, cancelled"
}
```

## Task ID And Event ID Generation

IDs should be stable-format random IDs. Use `secrets.token_hex` or UUIDs from
the Python standard library.

Recommended prefixes:

- `tg_task_`
- `tg_event_`
- `tg_tool_event_`

IDs must not encode private path details.

## Completion Commit Extension Design

The post-MVP completion extension should add schema version 2 and keep commit
evidence directly on the `tasks` row. This intentionally avoids separate commit
or artifact tables. The database records which commit or durable revision closes
the task; the version-control system remains responsible for listing changed
materials.

Recommended `tasks` columns:

```sql
completion_commit_required INTEGER NOT NULL DEFAULT 1 CHECK (completion_commit_required IN (0, 1)),
completion_commit_hash TEXT NOT NULL DEFAULT ''
```

Recommended index:

```sql
CREATE INDEX idx_tasks_project_completion_commit
  ON tasks(project_id, completion_commit_hash)
  WHERE completion_commit_hash != '';
```

Migration notes:

- Existing tasks should migrate with `completion_commit_required=1` and
  `completion_commit_hash=''`.
- Cross-column completion rules may be enforced in repository validation rather
  than SQLite `CHECK` constraints if SQLite migration limitations make table
  rebuilds undesirable.

Implementation rules:

- `completion_commit_required` defaults to true for every task.
- If managed materials changed, `completion_commit_required` remains true and
  `completion_commit_hash` must be set before the task can become `done`.
- If no managed materials changed, the user or agent must explicitly set
  `completion_commit_required=false`; `completion_commit_hash` must remain
  empty.
- `completion_commit_required=false` with a non-empty
  `completion_commit_hash` is invalid.
- Validate `completion_commit_hash` as concise sanitized text. It may be a Git
  commit hash or, for non-Git durable materials, a user-approved unique revision
  ID.
- Do not store changed material paths, raw diffs, full prompts, review
  transcripts, raw logs, stack traces, or secrets in completion commit fields.
- To trace changed materials for a completed task, read
  `completion_commit_hash` from the task and inspect the target project's
  version-control history, such as `git show --name-only <hash>`.
- `task edit --status done` must check the commit requirement before setting
  `completed_at` once schema version 2 is active.
- `task edit --status done` must also require explicit command-time
  confirmation that required verification and review gates have passed or have
  an approved fallback. Store at most concise task event summaries for those
  confirmations; do not add structured review or verification tables in this
  simplified design.
- Use `--verification-complete` and `--review-complete` as the command-time
  confirmation flags for `task edit --status done`.
- Use `--completion-commit-hash <hash>` to populate
  `completion_commit_hash`.
- Use `--commit-not-required` to set `completion_commit_required=false` for
  tasks that changed no managed materials.
- Missing verification confirmation should produce `verification_required`.
- Missing review confirmation should produce `review_required`.
- Missing required commit hash should produce `commit_required`.
- A non-empty hash when `completion_commit_required=false` should produce
  `completion_commit_conflict`.
- The tool may advise that a commit is required, but it must not create commits
  or mutate target projects without an explicit future command and updated
  target-project safety rules.
- Required verification and review gates still apply through the execution
  workflow and skill guidance; this simplified design does not add structured review or verification tables.

## Static Task Viewer Design

The Task Viewer is a projection of current SQLite state, never an independent
authority. The implementation flow is:

```text
SQLite (read-only)
  -> task/event repository query
  -> versioned snapshot object
  -> base64-encoded UTF-8 JSON
  -> bundled HTML template
  -> atomic task-viewer.html replacement
  -> browser file:// view
```

No browser-to-database connection exists. Regenerating the HTML with
`taskgov web export` is the only refresh mechanism.

### Command And Module Flow

`cli.py` adds a `web` command group with an `export` subcommand. The stable
command name is `web.export`. It accepts the existing common options plus
`--output`:

```powershell
python scripts/taskgov.py web export --repo <target-project> --json
python scripts/taskgov.py web export --repo <target-project> --output <html-path> --json
python scripts/taskgov.py web export --repo <target-project> --read-only --json
```

The handler must:

1. Resolve project identity, database path, and output path.
2. Inspect database readiness without migration or mutation.
3. Load all viewer task rows and bounded events through repository helpers in a
   dedicated read-only SQLite snapshot transaction.
4. Assemble and validate snapshot version 1.
5. Render the bundled template in memory.
6. For `--read-only`, report the planned result without creating directories or
   files.
7. Otherwise, create only the default generated viewer directory when needed
   and atomically replace the selected regular HTML file.
8. Emit the normal JSON envelope or concise text output.

The handler must not append `task_events` or `tool_events`. Exporting is a file
write but not a task-state mutation. Skill guidance must invoke the writing mode
only after the current user request explicitly asks to create or regenerate the
viewer. A task-state inspection request alone permits only inspection commands
or the `--read-only` preview.

### Repository Read Model

`tasks.py` remains the task/event repository boundary. Add a dedicated viewer
read helper rather than changing `list_tasks` or `show_task` behavior. The
helper must:

- select every task for the current `project_id`
- serialize all fields in `TASK_SHOW_FIELDS`
- order tasks with the existing `task list` priority/lane/order/time/ID rules
- fetch at most 10 events per task, ordered by `created_at DESC, rowid DESC` to
  match `task show` tie behavior
- return plain dictionaries to `viewer.py`

This extension requires no SQLite schema migration. It must not broaden the
existing `task.list` JSON task shape, whose compatibility is independent from
the viewer snapshot.

### Snapshot Assembly

`viewer.py` owns `SNAPSHOT_VERSION = 1` and constructs:

- `snapshot_version`
- one UTC `generated_at` value shared by CLI output and embedded data
- project ID and display name only
- current database schema version
- total and per-status counts
- ordered tasks with bounded `events`

Do not embed the canonical repository path, database path, environment data, or
tool events. The CLI envelope may display the local database and output paths,
but the portable HTML snapshot must not include them.

Serialize with deterministic JSON settings, UTF-8 encode the bytes, and base64
encode those bytes before template insertion. The template must contain exactly
one fixed placeholder. Rendering fails with `internal_error` if the template is
missing, unreadable, or has zero or multiple placeholders.

The browser decodes the base64 payload with standard browser APIs. Task content
must be assigned with `textContent` or equivalent text-node APIs. Do not pass
stored values to `innerHTML`, `insertAdjacentHTML`, `eval`, `Function`, URL
attributes, inline event attributes, or CSS declarations.

### Output Path And Atomic Write

Default output path resolution mirrors the database's project state directory:

```text
<installed-skill-root>/state/projects/<project-id>/viewer/task-viewer.html
```

Add a storage helper that derives this path from the installed skill root and
project ID. Do not derive it by string replacement on the database filename,
because an explicit `--db` override must not move the default viewer away from
skill-local generated state.

For an explicit `--output`:

- resolve the path to an absolute path without requiring it to exist
- require an `.html` or `.htm` suffix
- require the parent directory to exist
- reject a destination that is a directory, symbolic link, or existing
  non-regular file
- reject a destination inside the canonical target project unless it is under
  the installed skill's generated `state/` directory
- treat the user-approved path as the complete file-write scope

Apply the same existing-destination type rejection to the default output path.

For the default output, create only its `viewer` parent directory. For both
default and explicit output, write the rendered bytes to a unique temporary
file in the destination directory, flush and close it, then use `os.replace`
for atomic replacement. Clean up the temporary file after failures. Report
whether an existing regular output was replaced.

`--read-only` performs path, database, snapshot, and template validation in
memory but creates no output parent, temporary file, or final HTML.

### Browser Application

The template is a quiet operational interface, not a marketing page. Its main
regions are:

- compact header with `Task Viewer`, project display name, project ID, and
  generated timestamp
- stable status summary for all six statuses
- filter toolbar with text search, status, kind, lane, priority, tag, terminal
  task visibility, and reset command
- dense task table on desktop and a stable stacked row layout on narrow screens
- unframed detail region or modal for the selected task and its recent events
- explicit empty-result state

The browser defaults to active tasks and keeps done/cancelled tasks available
through the terminal-task control. Filtering and sorting are client-side and
ephemeral. Do not use cookies, local storage, IndexedDB, or URL query state.

Use neutral surfaces plus distinct status colors; do not rely on color alone.
Keep card radii at 8 px or less, avoid nested cards, retain visible keyboard
focus, associate labels with controls, and ensure long IDs, titles, commit
hashes, and descriptions wrap without overlap. Use native controls and
semantic table/detail markup where practical.

### Read-Only Snapshot Transaction

Do not use the existing immutable inspection connection for the viewer data
read. Add a dedicated `connect_snapshot_readonly` storage helper that opens the
SQLite URI with `mode=ro` but without `immutable=1`, enables
`PRAGMA query_only=ON`, and starts an explicit read transaction. Revalidate
schema version and project identity inside that transaction before querying
tasks and events. This gives the export one SQLite-consistent point-in-time view
when another session commits concurrently.

Preserve the existing preflight rejection for active WAL sidecars. Tests must
prove a normal snapshot read creates no WAL/SHM/journal files, an already-active
WAL state fails without stale output, and a concurrent writer either yields a
consistent snapshot or a structured tool error. This does not add cross-session
ownership or live synchronization; the generated timestamp still describes
export time, not an exclusive database revision.

### Browser Security Boundary

The generated page must include this exact meta content security policy:

```text
default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; media-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; manifest-src 'none'; base-uri 'none'; form-action 'none'
```

`'unsafe-inline'` is accepted only for the fixed bundled application script and
style needed by a single-file `file://` page. It does not permit eval because
`'unsafe-eval'` is absent. Stored task data is base64 text in a non-executable
data element and is inserted into the DOM only through text APIs; no task value
can create another inline script, style, event attribute, URL, or markup node.
Static tests must assert the exact policy and prohibited sinks, and a browser
negative test must render script/event-handler-shaped task text without setting
an execution sentinel or making a network request.

The implementation must contain no network API, external URL, telemetry,
automatic browser launch, or database-write code. Browser refresh reloads the
same snapshot and must not be presented as a database refresh.

### Viewer Error Mapping

- malformed/user-unsafe destination: `output_path_invalid`, exit code 1
- missing explicit parent: `output_parent_missing`, exit code 1
- output I/O or atomic replacement failure: `output_write_failed`, exit code 2
- missing/malformed template or unexpected snapshot/render failure:
  `internal_error`, exit code 2

Database readiness errors retain their existing codes and command-specific
empty data must still include the resolved output path when resolution itself
succeeded.

## Validation Rules

Task validation:

- `title` must be non-empty.
- Free-form fields must pass the size limits and privacy checks in
  `docs/specification.md`.
- `kind` must be `sequential` or `optional`.
- `priority` must be one of the MVP priorities.
- `status` must be one of the MVP statuses.
- `review_tier` must be integer `0`, `1`, or `2`.
- `blocked_reason` is required when status is `blocked`.
- `task add --status blocked` must reject before storage unless
  `--blocked-reason` is provided.
- `completed_at` is set when status becomes `done` and cleared when moving back
  to an active status.

Sequential task behavior:

- If `kind=sequential` and `lane_order` is omitted, append after the max order in
  the lane.
- If `kind=sequential` and `lane` is omitted, use a deterministic default lane
  such as `default`.
- `task next` does not return later ready sequential tasks while earlier tasks in
  the same lane are incomplete.
- `task next` supports `--kind`, `--lane`, `--priority`, and `--limit`; default
  limit is `5`.
- `task next` sorts by priority rank (`urgent`, `high`, `normal`, `low`), lane,
  `lane_order` with nulls last, creation time, and `task_id`.

Repository and schema tests must cover duplicate sequential lane orders, null
sequential orders, blocked tasks without blocked reasons, completed timestamp
transitions, and `project_mismatch` behavior for explicit `--db` reuse.

## Free-Form Privacy Checks

The MVP should use deterministic, local-only checks before storing user text.
Reject obvious secrets and raw dumps with `privacy_rejected`. Initial rejection
patterns include:

- `Authorization:`
- `Bearer `
- `-----BEGIN `
- `password=`
- `token=`
- `api_key=`
- `Traceback (most recent call last)`
- raw stdout or stderr dump headings
- repeated `diff --git` blocks

## Text Output

Text output should be brief and operational. Examples:

```text
DB: C:\...\taskgov.sqlite
Project: kurakoma-a1b2c3d4e5f6
Ready: 4  Blocked: 1  Review pending: 0  Done: 12
Next actionable: 3
```

Avoid long explanations in CLI text output. Detailed guidance belongs in skill
references.

## Testing Strategy

Use Python standard-library tests where possible.

Required test areas:

- CLI help exits successfully.
- `db status` reports a missing temp database without initializing it.
- `db init` initializes a temp database and is idempotent.
- Default DB path creates separate paths for separate repo roots.
- Migrations are idempotent.
- `task add` validates required and enum fields.
- `task edit` requires blocked reason for blocked status.
- `task list` filters by status, kind, lane, priority, and tag.
- `task show` returns one task and recent events.
- `task next` returns ready optional tasks when a sequential lane is blocked.
- Per-command JSON envelopes contain the required payloads from
  `docs/specification.md`.
- Free-form privacy rejection and size-limit behavior are tested.
- An installed skill-folder smoke test runs `scripts/taskgov.py --help` without
  importing modules from outside the skill folder.
- No test mutates a real target project source file or Git state.

Task Viewer extension tests must additionally cover:

- all-status snapshot projection with completion commit fields and no private
  source paths
- the 10-event-per-task bound and deterministic event ordering
- base64 payload round-trip and HTML-shaped task text rendered as text
- default output path, explicit output safety, atomic replacement, and
  `--read-only` no-write behavior
- missing DB, migration-required, and project-mismatch propagation
- no database, task-event, or tool-event mutation during export
- no external resource URLs or network APIs in the bundled template
- exact CSP directive assertions and prohibited DOM sink assertions for
  `innerHTML`, `insertAdjacentHTML`, `eval`, `Function`, inline event
  attributes, and task-derived URL attributes
- isolated installed-skill export from a copied skill folder
- representative desktop and mobile `file://` browser checks

## Packaging And Release Design

The source repository is the development and review surface. The installable
skill folder is the project-scoped distribution unit.

Release artifacts should include the installable skill folder only:

```text
task-governance-tool/
  SKILL.md
  agents/
  assets/
    task-viewer.template.html
  scripts/
    taskgov.py
    task_governance_tool/
  references/
```

Release artifacts must exclude:

- `state/`
- SQLite databases
- caches
- logs
- root copied reference material
- test outputs

Generated `task-viewer.html` snapshots remain under `state/` by default and
must be excluded from release artifacts. The `assets/` template is static
runtime input and must be included after the Task Viewer implementation is
complete.

## Future Extension Points

The design leaves room for:

- Project profile detection.
- Verification recording.
- Review-template generation.
- Dependency graphs.
- Git advisory integration.
- Task import and richer exchange formats beyond the approved static viewer.

These extensions must not change MVP privacy or target-project mutation
semantics without updating `docs/specification.md` and this design.
