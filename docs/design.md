# task-governance-tool MVP Design

Status: formal MVP design baseline.

This document describes the initial implementation design for the MVP specified
in `docs/specification.md`.

## Design Summary

The MVP is a small local-first Codex skill plus deterministic Python CLI. The
skill tells Codex when and how to use the tool. The CLI owns all structured task
state operations. SQLite stores one target project's task state per database.

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
  scripts/
    taskgov.py
    task_governance_tool/
      __init__.py
      cli.py
      storage.py
      tasks.py
      selection.py
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

`tests/`

- Exercise CLI contracts, storage migrations, repository behavior, and next-task
  selection.

## Database Path Resolution

Inputs:

- `--db`: explicit override.
- `--repo`: target project root; default current directory.
- installed skill root: inferred from `scripts/taskgov.py` location for the
  self-contained skill package.

Resolution:

1. Canonicalize `--repo`.
2. Compute `project_id` from the canonical repo path.
3. If `--db` is provided, use that path.
4. Otherwise use:

```text
<installed-skill-root>/state/projects/<project-id>/taskgov.sqlite
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
- `tg_verification_`
- `tg_review_`
- `tg_commit_`
- `tg_artifact_`

IDs must not encode private path details.

## Completion Evidence Extension Design

The post-MVP completion-evidence extension should add schema version 2 and keep
completion evidence separate from the MVP `tasks` row. This preserves the task
record as compact current state while allowing audit queries across reviews,
commits, and managed materials.

Recommended schema additions:

```sql
CREATE TABLE task_verification_records (
  verification_record_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  verification_status TEXT NOT NULL CHECK (verification_status IN (
    'not_required',
    'passed',
    'failed',
    'user_approved_exception'
  )),
  verification_label TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  verified_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE task_review_records (
  review_record_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
  review_status TEXT NOT NULL CHECK (review_status IN (
    'not_required',
    'passed',
    'blocked',
    'unavailable_fallback'
  )),
  reviewer_count INTEGER NOT NULL DEFAULT 0,
  blocking_findings_count INTEGER NOT NULL DEFAULT 0,
  fallback_approved INTEGER NOT NULL DEFAULT 0 CHECK (fallback_approved IN (0, 1)),
  summary TEXT NOT NULL DEFAULT '',
  completed_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE task_commit_records (
  commit_record_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  commit_system TEXT NOT NULL,
  commit_identifier TEXT NOT NULL,
  commit_ref TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  committed_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (project_id, commit_identifier)
);

CREATE TABLE task_commit_links (
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  commit_record_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (task_id, commit_record_id),
  FOREIGN KEY (task_id) REFERENCES tasks(task_id),
  FOREIGN KEY (commit_record_id) REFERENCES task_commit_records(commit_record_id)
);

CREATE TABLE task_artifact_changes (
  artifact_change_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  commit_record_id TEXT,
  artifact_path TEXT NOT NULL,
  artifact_kind TEXT NOT NULL DEFAULT 'file',
  change_type TEXT NOT NULL DEFAULT 'modified',
  content_hash TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(task_id),
  FOREIGN KEY (commit_record_id) REFERENCES task_commit_records(commit_record_id),
  FOREIGN KEY (task_id, commit_record_id)
    REFERENCES task_commit_links(task_id, commit_record_id)
);
```

Recommended indexes:

```sql
CREATE INDEX idx_verification_records_task ON task_verification_records(task_id, created_at);
CREATE INDEX idx_review_records_task ON task_review_records(task_id, created_at);
CREATE INDEX idx_commit_links_task ON task_commit_links(task_id, created_at);
CREATE INDEX idx_commit_links_commit ON task_commit_links(commit_record_id);
CREATE INDEX idx_artifact_changes_path ON task_artifact_changes(project_id, artifact_path);
CREATE INDEX idx_artifact_changes_task ON task_artifact_changes(task_id, created_at);
```

Implementation rules:

- Record review and commit evidence through explicit CLI commands or explicit
  `task edit` options; do not infer evidence from chat history.
- Record verification evidence explicitly. A user-approved verification
  exception is valid evidence only when the user approval is recorded as compact
  sanitized metadata.
- Treat Tier 2 review evidence as valid only when `review_status='passed'` and
  `reviewer_count >= 2`, or when `review_status='unavailable_fallback'`,
  `fallback_approved=1`, review tooling is unavailable, and the summary records
  the documented self-review and user approval.
- Treat Tier 1 review evidence as valid when `review_status='passed'` and
  `reviewer_count >= 1`, or when `review_status='unavailable_fallback'` records
  the documented self-review used because review tooling was unavailable.
- Treat Tier 0 review evidence as valid with `review_status='not_required'`
  only for purely mechanical changes.
- `blocking_findings_count` must be zero before a task can become `done`.
- Treat a Git commit hash as the preferred `commit_identifier`. Accept a
  user-provided unique revision ID for non-Git managed materials.
- Validate `verification_label`, `commit_identifier`, `commit_ref`, summaries,
  and artifact paths as concise sanitized evidence fields.
- Normalize file `artifact_path` values as project-relative paths using forward
  slashes. Store absolute paths only for explicitly approved managed materials
  outside the target project.
- Store only paths, IDs, timestamps, short summaries, and optional content
  hashes. Do not store raw diffs, full review transcripts, full prompts, raw
  logs, stack traces, or secrets.
- `task edit --status done` must check completion evidence before setting
  `completed_at` once schema version 2 is active.
- Missing verification evidence should produce `verification_required`.
- Missing review evidence should produce `review_required`; missing commit or
  managed-material revision evidence should produce `commit_required`; mixed or
  incomplete evidence should produce `completion_evidence_required`.
- The tool may advise that a commit is required, but it must not create commits
  or mutate target projects without an explicit future command and updated
  target-project safety rules.
- `task show` should include compact completion evidence when present.
- A future query should support tracing from an artifact path to the tasks and
  commit/revision IDs that changed it.
- A commit/revision record may link to multiple tasks; avoid requiring one
  commit per task.

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
- No test mutates a real target project.

## Packaging And Release Design

The source repository is the development and review surface. The installable
skill folder is the user-facing distribution unit.

Release artifacts should include the installable skill folder only:

```text
task-governance-tool/
  SKILL.md
  agents/
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

## Future Extension Points

The design leaves room for:

- Project profile detection.
- Verification recording.
- Review-template generation.
- Dependency graphs.
- Git advisory integration.
- Richer export/import.

These extensions must not change MVP privacy or target-project mutation
semantics without updating `docs/specification.md` and this design.
