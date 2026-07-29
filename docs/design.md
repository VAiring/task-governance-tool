# task-governance-tool MVP Design

Status: the published baseline remains v0.9.0/schema v14 with Viewer snapshot
v3 and completed TG-M17 identity/storage behavior. TG-M18.0 and TG-M18.1 are
complete: the non-public staging runtime is package v0.9.0, schema v15, and
Viewer snapshot v3 accepting sources 5-15, without native completion capture
or public history. TG-M18.2 through TG-M18.4 remain sequential work targeting
v0.10.0/schema v16/Viewer snapshot v4. The completed TG-M16.4 reduced-loop
behavioral acceptance is retained. TG-M12.3 Issue adapter remains blocked.

This document describes the initial implementation design for the MVP specified
in `docs/specification.md`.

All release- or milestone-specific sections before TG-M14 are historical
implementation lineage, including headings labeled `Implemented` or
`Approved`. Their durable state-transition, review, and privacy design remains
applicable only where TG-M14 does not supersede it. Sections labeled
`Historical` preserve additional pre-M14 implementation detail. The
implemented TG-M14 section and later component designs are the current v0.8.0
lineage; implemented TG-M17 design is the current v0.9.0
identity/storage authority when those older boundaries differ. The approved
TG-M18 section is implemented through M18.1 and remains future implementation
authority for its sequential M18.2 through M18.4 units.

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
  release-manifest.json
  SKILL.md
  agents/
    openai.yaml
  assets/
    task-viewer.template.html
  scripts/
    taskgov.py
    task_governance_tool/
      __init__.py
      artifact_lock.py
      backup.py
      checkpoints.py
      cli.py
      compact.py
      completion_workflow.py
      doctor.py
      maintenance.py
      project_scope.py
      review_packet.py
      setup.py
      storage.py
      tasks.py
      ordering.py
      selection.py
      completion.py
      reviews.py
      git_snapshot.py
      handoffs.py
      contracts.py
      effort.py
      self_status.py
      viewer.py
      viewer_maintenance.py
  references/
    task_workflow.md
    cli_contracts.md
    reconciliation.md
tests/
fixtures/
  task-status-mvp/
  task-status-migration-v2/
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

User-wide, symbolic-link, and junction installation is unsupported for
stateful use. Each governed project uses one physical project-scoped copy.

## Skill Package Design

`SKILL.md` should stay concise. It should:

- Trigger on task planning and state, current/next selection, blockers and
  pauses, local handoff, bounded review preparation, evidence-gated
  completion, optional checkpoints, setup, and read-only diagnosis.
- Instruct Codex to read target project governing docs separately.
- Instruct Codex to use `taskgov task next` and `taskgov task show` instead of
  loading large task-status files.
- Point to one-level references only when details are needed.
- Avoid advertising deferred behavior, including verification-run recording,
  external Issue delivery, persistent profile authoring, dependency graphs,
  Git mutation, browser automation, or target-project mutation.

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
- Resolve `--repo` and output mode while rejecting removed public `--db`
  lexically.
- Call task and storage services.
- Format JSON/text output.

`task-governance-tool/scripts/task_governance_tool/storage.py`

- Resolve default database paths.
- Open SQLite connections.
- Apply schema migrations.
- Provide repository helpers.
- Enforce project metadata checks for the canonical public path and internal
  test/service path injection.
- Avoid leaking raw `sqlite3` calls into feature modules.

`task-governance-tool/scripts/task_governance_tool/tasks.py`

- Validate task fields.
- Create, update, fetch, and list tasks.
- Append task event rows for meaningful changes.
- Own the bounded current-task projection and its optional current-status
  filter after TG-M9.

`task-governance-tool/scripts/task_governance_tool/selection.py`

- Implement `task next` readiness rules.
- Consume the shared sequential predecessor predicate from `ordering.py`.
- Keep paused visibility advisory-only; paused rows never become next-task
  candidates.

`task-governance-tool/scripts/task_governance_tool/ordering.py`

- Own the one predecessor-completeness query used by both `task next` and
  direct status transitions.
- Treat only `done` and `cancelled` predecessors as complete.

`task-governance-tool/scripts/task_governance_tool/completion.py`

- Validate explicit completion evidence kinds.
- Resolve Git commit evidence read-only and return a canonical full object ID.
- Enforce completion evidence without creating commits or changing Git state.

`task-governance-tool/scripts/task_governance_tool/reviews.py`

- Create and read sanitized review targets, receipts, and findings through
  storage repository helpers.
- Evaluate tier-specific review gates deterministically.
- Store no raw review transcript or private reasoning.

`task-governance-tool/scripts/task_governance_tool/viewer.py`

- Assemble the versioned static-viewer snapshot from repository results.
- Load and validate the bundled viewer template.
- Encode snapshot JSON for safe HTML embedding.
- Render and atomically write the output file.
- Contain no raw SQLite queries; task and event reads remain in the task
  repository boundary.

`task-governance-tool/scripts/task_governance_tool/self_status.py`

- Provide the internal package-integrity inspector used by `doctor`; it has no
  public `self` command.
- Strictly parse the co-located versioned release manifest.
- Enumerate only the installed Skill package and compare regular core files by
  streaming SHA-256.
- Exclude only declared root configuration, adapter, generated-state, and
  Python cache boundaries.
- Return bounded relative paths and stable status/reason values without
  reading a database, Git repository, network service, or target project.

`task-governance-tool/assets/task-viewer.template.html`

- Contain the complete offline HTML, CSS, and JavaScript application.
- Use one unique snapshot placeholder populated by `viewer.py`.
- Depend on no external files, packages, fonts, CDNs, or network calls.

`tests/`

- Exercise CLI contracts, storage migrations, repository behavior, and next-task
  selection.

## Historical Public Database Path Resolution Through v0.7.0

This section records the former public `--db` override. The implemented TG-M14
CLI and envelope boundary below removes that option and derives the only public
database location from the supported physical package and governed project.

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

## Historical Command Flow Through v0.7.0

This section preserves the former `db init`/`db status` command flow. Current
creation and migration is owned only by `setup`; current inspection is owned by
the read-only `doctor` and task/handoff reads described in TG-M14.

`db init` is the only create/migrate command. It should follow this flow:

1. Parse arguments.
2. Resolve repo and database path.
3. If `--read-only` is present, reject the command before creating,
   migrating, or writing.
4. Validate that existing migration history is contiguous, then initialize or
   apply only the next supported ordered migration.
5. Revalidate migration history, required schema objects, and project identity
   before reporting success.
6. If history has a gap, return `migration_required` without mutation and
   require a valid backup or explicit migration-history inspection; do not
   infer or stamp a missing version.
7. Emit JSON or concise text and return a stable exit code.

All other write commands, including `task add`, `task edit`, and TG-M8 review
commands, must use this flow:

1. Parse arguments and reject `--read-only`.
2. Resolve repo and database path without creating parents or a file.
3. Open an existing database and verify project identity and current schema.
4. Return `db_not_initialized` or `migration_required` without mutation when
   applicable.
5. Validate inputs and current-state transition gates.
6. Perform the repository operation in one transaction.
7. Emit JSON or concise text and return a stable exit code.

Write commands must clearly say what they recorded in text mode and in JSON
payloads.

Database inspection commands are `db status`, `task list`, `task next`,
`task current`, `task effort`, `task show`, `handoff list`, and
`handoff show`. They must not create, migrate, or write to the database by
default. A missing database should produce `db_not_initialized`; a database
requiring migration should produce `migration_required`; `db status` should
report those states without changing the database. TG-M13's operational
journal preflight and response-coherent read boundary below apply to every one
of these database-backed inspections.

M14.6 removes the public `web`/custom-output surface. After setup opt-in, the
post-commit coordinator reads one compatible SQLite snapshot and writes only
the canonical generated Viewer; setup owns the direct initial/repair path.
Neither path writes task state while rendering.

## Historical JSON Envelope Through v0.7.0

The old envelope below included `db_path`. Current v0.8.0 public serialization
uses the implemented TG-M14 envelope and omits every raw storage path.

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
  "message": "status must be one of: ready, in_progress, paused, blocked, review_pending, done, cancelled"
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

The implemented post-MVP completion extension added schema version 2 and kept
commit evidence directly on the `tasks` row. This section describes the
historical v2 design; TG-M8 below supersedes its generic revision and
boolean-only review behavior for new transitions. The version-control system
remains responsible for listing changed materials.

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

## Implemented TG-M8 Governance Hardening Design

This section supersedes the initialization, completion-evidence, and
boolean-only review details above for new behavior. Migrations preserve the
earlier schema and data rather than rewriting historical claims.

### Project-Scoped Setup

Package creation remains side-effect free. After the user approves installation
to `<repo>/.agents/skills/task-governance-tool`, the setup workflow is:

1. verify the installed `state/` path is ignored;
2. run `taskgov db status --repo <repo>`;
3. run `taskgov db init --repo <repo>` explicitly when initialization or a
   supported migration is required; and
4. run `db status` again to confirm the current schema and project identity.

No task command may substitute for step 3.

### Schema Version 3: Paused State

Schema version 3 rebuilds `tasks` transactionally because the existing status
`CHECK` constraint must admit `paused`. It adds:

```sql
pause_reason TEXT NOT NULL DEFAULT ''
```

The rebuilt table must include `paused` in the status constraint and:

```sql
CHECK (
  (status = 'paused' AND pause_reason != '') OR
  (status != 'paused' AND pause_reason = '')
)
```

All version-2 rows copy unchanged with `pause_reason=''`. Because
`task_events` references the rebuilt parent table, the migration must use this
SQLite-safe boundary (or an equivalently tested strategy):

1. confirm no transaction is active, then disable foreign-key enforcement;
2. begin the migration transaction, create the version-3 table, copy every
   task, replace the old table, and recreate indexes;
3. compare task/event IDs and counts and run `PRAGMA foreign_key_check` before
   commit;
4. commit only when all checks pass; otherwise roll back to the usable version-2
   database; and
5. re-enable foreign-key enforcement in a `finally` path and verify it reports
   enabled before returning the connection.

`PRAGMA foreign_keys` must not be toggled inside a transaction. Failure-injection
tests must prove rollback preserves the original data and that enforcement is
restored for both success and failure paths.

`ordering.py` exposes one repository query answering whether a given sequential
task has an earlier same-project, same-lane row whose status is not `done` or
`cancelled`. `selection.py` excludes such ready tasks. `tasks.py` calls the
same predicate before transitions into `in_progress`, `review_pending`, or
`done` and returns `sequential_predecessor_incomplete` on failure. A paused
predecessor is therefore incomplete without special-case duplication.

Task add applies the predicate to a proposed sequential row whose initial
status is `in_progress` or `review_pending`; initial `done` and `paused` are
rejected before storage. Task edit validates the complete resulting row, not only the fields
named on the command. If kind, lane, order, or status changes, the same write
transaction validates every `in_progress`, `review_pending`, and `done` row in
both affected old/new lanes. This rejects inserting or moving an incomplete
task ahead of work that has already crossed the sequential gate. The
repository uses an immediate write transaction and performs validation
immediately before the write so concurrent writers serialize instead of
bypassing it.

No transition override is introduced in TG-M8. Administrative overrides need
a separate contract and audit design before implementation.
Unsupported pause/resume source states fail with `invalid_status_transition`;
initial paused registration fails with `initial_paused_forbidden`.

### `task current` Read Model

The repository query selects only `in_progress`, `review_pending`, `paused`,
and `blocked`, with default limit `20`. It left-joins or separately fetches at
most one latest event per task using deterministic order
`created_at DESC, rowid DESC`, matching `task show` and the Viewer when multiple
events share the same second. The internal rowid is a tie-breaker only and is
not exposed in JSON.

Command value is `task.current`; its payload is:

```json
{
  "tasks": [
    {
      "task_id": "tg_task_example",
      "status": "in_progress",
      "latest_event": {},
      "suggested_next_action": "continue implementation"
    }
  ],
  "count": 1,
  "limit": 20,
  "statuses": ["in_progress", "review_pending", "paused", "blocked"]
}
```

Suggested actions are deterministic status mappings, optionally including the
sanitized pause or blocker reason:

- `in_progress`: continue the task and inspect its latest event;
- `review_pending`: complete the required review gate;
- `paused`: review the pause reason and resume explicitly when safe;
- `blocked`: resolve or reassess the blocker.

The command does not inspect the working tree, calculate staleness, or write a
checkpoint. A transition into `paused` is valid only from `in_progress` or
`review_pending`; the normal exit from `paused` is `in_progress`. Leaving
`paused` clears `pause_reason`; the transition event retains only the concise
sanitized historical reason.

### Schema Version 4: Completion Evidence

Schema version 4 adds these task columns while retaining
`completion_commit_required` and `completion_commit_hash` as compatibility
projections:

```sql
completion_evidence_kind TEXT NOT NULL DEFAULT 'none' CHECK (
  completion_evidence_kind IN (
    'none',
    'git_commit',
    'external_revision',
    'commit_not_required',
    'legacy_unverified'
  )
),
completion_evidence_revision TEXT NOT NULL DEFAULT '',
completion_evidence_reason TEXT NOT NULL DEFAULT '',
external_revision_approved INTEGER NOT NULL DEFAULT 0 CHECK (
  external_revision_approved IN (0, 1)
)
```

Version-2 historical rows map without validation or data loss:

- required with a non-empty hash becomes `legacy_unverified`; the original
  hash remains unchanged and is copied to `completion_evidence_revision`;
- not required with an empty hash becomes `commit_not_required`;
- required with an empty hash becomes `none`; and
- any otherwise inconsistent legacy combination becomes `legacy_unverified`
  with both original fields retained for audit and a migration warning.

Historical `done` rows remain done. `legacy_unverified` is read-only historical
evidence and cannot satisfy a new done transition after the task is reopened.
New writes enforce this complete cross-field matrix and synchronize the legacy
projection:

- `none`: revision/reason empty, approval `0`, required `1`, hash empty;
- `git_commit`: canonical full revision non-empty, reason empty, approval `0`,
  required `1`, hash equal to revision;
- `external_revision`: revision and sanitized reason non-empty, approval `1`,
  required `1`, hash equal to revision;
- `commit_not_required`: revision/reason empty, approval `0`, required `0`,
  hash empty; and
- `legacy_unverified`: migration-only; revision mirrors the preserved old hash,
  reason/approval remain empty/`0`, and the original required/hash pair is not
  rewritten.

Changing an evidence kind clears every field not permitted by the destination
row before applying its new values. Conflicting or stale reason, approval,
revision, required, or hash fields fail with `completion_evidence_conflict`
rather than being silently retained. `legacy_unverified` cannot be selected by
a normal CLI write.

Completion CLI contract:

- `--completion-commit-hash <revision>` is the compatibility spelling for
  evidence kind `git_commit`;
- `--completion-evidence-kind git_commit --completion-revision <revision>` is
  the explicit Git spelling;
- `--completion-evidence-kind external_revision --completion-revision <id>
  --completion-evidence-reason <summary>` records external evidence;
- external evidence always requires `--external-revision-approved` and stores
  an audit event; in a Git project the acknowledgement explicitly selects the
  external durable source instead of Git history; and
- `--commit-not-required` maps to `commit_not_required`.

Mutually conflicting evidence options fail before storage. A task must not be
created initially as done, regardless of supplied evidence.

Git resolution uses `subprocess.run` with an argument vector and no shell,
equivalent to:

```text
git -C <canonical-repo> rev-parse --verify --end-of-options <revision>^{commit}
```

The revision-plus-peel expression is one argument in the subprocess argument
vector. Reject empty or leading-hyphen revisions before process execution;
`--end-of-options` is still required as defense in depth. The implementation
must reject failure, multiple output lines, non-hex output, or output that is
not one canonical full object ID. This makes an ambiguous short name fail and
stores the resolved full ID. Tests include option-shaped input and snapshot
`HEAD`, refs, index/worktree status, and database bytes or logical counts before
and after read-only inspection to prove no mutation.

### Schema Version 5: Review Evidence

Schema version 5 adds the current target to `tasks`:

```sql
review_target_kind TEXT NOT NULL DEFAULT '',
review_target_value TEXT NOT NULL DEFAULT '',
review_target_generation INTEGER NOT NULL DEFAULT 0 CHECK (
  review_target_generation >= 0
)
```

It adds two normalized evidence tables:

```sql
CREATE TABLE review_receipts (
  review_receipt_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  reviewer_key TEXT NOT NULL,
  receipt_kind TEXT NOT NULL CHECK (receipt_kind IN (
    'independent', 'self_review_fallback', 'not_required'
  )),
  verdict TEXT NOT NULL CHECK (verdict IN (
    'pass', 'changes_requested', 'not_required'
  )),
  target_kind TEXT NOT NULL,
  target_value TEXT NOT NULL,
  target_generation INTEGER NOT NULL CHECK (target_generation > 0),
  summary TEXT NOT NULL DEFAULT '',
  user_approved INTEGER NOT NULL DEFAULT 0 CHECK (user_approved IN (0, 1)),
  created_at TEXT NOT NULL,
  UNIQUE (task_id, target_generation, reviewer_key),
  CHECK (reviewer_key != ''),
  CHECK (target_kind IN ('git_commit', 'diff_fingerprint', 'external_revision')),
  CHECK (target_value != ''),
  CHECK (
    (receipt_kind = 'independent' AND verdict IN ('pass', 'changes_requested')
      AND user_approved = 0) OR
    (receipt_kind = 'self_review_fallback'
      AND verdict IN ('pass', 'changes_requested')) OR
    (receipt_kind = 'not_required' AND verdict = 'not_required'
      AND user_approved = 0 AND summary != '')
  ),
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE review_findings (
  review_finding_id TEXT PRIMARY KEY,
  review_receipt_id TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
  status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
  summary TEXT NOT NULL,
  resolution_summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  FOREIGN KEY (review_receipt_id) REFERENCES review_receipts(review_receipt_id)
);
```

Indexes cover `(task_id, target_generation, target_kind, target_value,
verdict)`, reviewer distinctness queries, and open findings by task and
severity. Receipts are append-only and each reviewer key may appear on at most
one receipt per task target generation. A correction or re-review requires
setting the review target again to create a new generation; same-generation
PASS-to-changes-requested or changes-requested-to-PASS replacement fails with
`review_receipt_already_recorded`. Finding resolution updates only status,
sanitized resolution summary, and timestamp while preserving the original
finding.

Allowed review target kinds are `git_commit`, `diff_fingerprint`, and
`external_revision`. A Git target is resolved and stored canonically through
the same read-only validation service used by completion evidence. A diff
fingerprint must use canonical `sha256:<64 lowercase hex characters>` form;
the tool validates but does not invent the fingerprint. CLI commands are:

- `review target set <task-id> --kind <kind> --revision <value>`;
- `review receipt add <task-id> --reviewer <key> --kind <kind>
  --verdict <verdict> [--summary <summary>] [--user-approved]`;
- `review finding add <task-id> --receipt-id <id> --severity <severity>
  --summary <summary>`; and
- `review finding resolve <finding-id> --resolution <summary>`.

Receipt creation copies the task's current target and generation; callers
cannot silently attach a receipt to a different target. Every `review target
set` increments the task generation and appends an audit event, even when kind
and value equal a historical target. Old receipts remain historical but the
completion query counts only receipts whose target kind, value, and generation
equal the current task target.

The service validates receipt combinations as one unit:

- `independent` accepts `pass` or `changes_requested`, never user approval;
- `self_review_fallback` requires a concise summary; Tier 1 `pass` requires
  approval `0`, while Tier 2 `pass` requires explicit approval `1`;
- `not_required` is Tier 0 only, requires verdict `not_required`, a concise
  mechanical-change rationale, and approval `0`; and
- `changes_requested` requires a concise summary and never satisfies a review
  gate.

Finding creation first loads the task in the current project and the referenced
receipt through `(project_id, task_id, review_receipt_id)`, rejects a historical
target generation, and derives the stored relationship solely from the loaded
receipt. The finding table intentionally does not duplicate caller-supplied
task/project keys. Cross-task or cross-project receipt use fails with
`review_receipt_mismatch`.

The gate query counts distinct reviewer keys for independent `pass` receipts,
applies Tier 0/1/2 fallback rules from the specification, and separately checks
for open high or medium findings across all task receipts. Resolving a high or
medium finding does not erase its freshness requirement: the task's current
target generation must be greater than that finding receipt's generation, and
the counted PASS receipts must belong to the newer current generation. Thus
resolve-without-target-reset remains blocked; target reset plus fresh passes can
succeed. Reviewer-key distinctness is a deterministic minimum check, not
cryptographic identity proof.

The independent reviewer produces the verdict and findings. The trusted caller
that requested the review, normally the parent Codex or orchestrator, writes
the sanitized receipt/finding rows as an attestation of that result. No
reviewer authentication, signature, process/session binding, or identity proof
is inferred from that write or from the reviewer key.

`task show` reuses the gate query and returns an additive `review_evidence`
object after schema version 5:

```json
{
  "target": {"kind": "git_commit", "value": "...", "generation": 3},
  "gate": {
    "review_tier": 2,
    "required_independent_passes": 2,
    "qualifying_independent_passes": 2,
    "fallback_kind": null,
    "satisfied": true
  },
  "counts": {
    "receipts_total": 4,
    "receipts_current_generation": 2,
    "open_high": 0,
    "open_medium": 0,
    "open_low": 1
  },
  "blocking_findings": [],
  "recent_receipts": [],
  "recent_findings": []
}
```

Receipt/finding arrays are sanitized, ordered by `created_at DESC, rowid DESC`,
and bounded to 10 each. The projection includes no transcript or reasoning and
performs no writes or LLM calls. The snapshot-version-3 builder reuses this read
model rather than reimplementing review gate logic. Before a target is set, the
projection uses an empty target with generation `0`, zero qualifying receipts,
and `satisfied=false` for tiers that require review.

Review and finding summaries, reviewer keys, target values, external evidence,
pause reasons, and resolution summaries all pass the same deny-by-default
privacy validator used by task notes. Raw review text, private prompts,
reasoning, diffs, logs, and stack traces are never accepted.

Migration gives every existing task an empty target and generation `0` and
creates no synthetic receipt or finding. Historical done rows remain done; if
one is reopened, a later done transition must set a target and satisfy the new
review gate.

### Migration And Compatibility Verification

The v2-to-current path is explicit, ordered, idempotent, and transactionally
repeatable through `db init`. A synthetic sanitized migration fixture must
match the operational shape without copying private project text: 12 tasks,
191 events, nine completed task hashes, active and ready tasks, and
representative tool events. Tests compare task IDs, event IDs and counts,
statuses, completion hashes, and project metadata before and after migration,
then run `PRAGMA quick_check` and `PRAGMA foreign_key_check`.

Snapshot version 3 reuses the bounded `task show` review-evidence projection.
It does not expose raw reviews, reasoning, or an unbounded ledger.

The compact envelope retains its existing top-level keys. Existing commands
retain their command names. Minimal all-status static-viewer and snapshot
support for `paused` shipped with schema version 3 so the viewer did not
regress. The final integration unit added completion/review evidence after
those contracts stabilized.

## Implemented TG-M9 Paused Work Visibility Design

TG-M9 adds three read-only projections over the existing schema-v5 `status`
column. It requires no schema version change, task migration, event backfill,
or viewer snapshot change. Implementation must not begin until its execution
units receive separate approval.

### Paused Count Repository Query

`storage.empty_counts()` gains the additive `paused` key so success and every
database-readiness error return one stable count shape. `count_tasks()` fills
that key with an exact project-scoped `COUNT(*) WHERE status='paused'` while
leaving the existing `active` predicate unchanged. `status_data()` therefore
needs no new top-level field; `status_text()` adds a labeled paused value.

The count query runs through the existing inspection connection and project
identity check. It must not open a write connection, start migration, append an
event, or create SQLite sidecars. No task content is needed to compute or
display the count.

### Advisory Warning Assembly

`task next` keeps its existing repository candidate query, inspection
connection boundary, and stable `data` payload. After TG-M9.1,
`inspect_database()` already returns the exact unfiltered paused count from its
successful current-schema, project-matching read. TG-M9.2 reuses that
`StatusResult` value when assembling the warning; it does not add a third query
or change the candidate connection to the viewer-specific snapshot helper.

When the count is positive, the handler constructs exactly one warning:

```json
{
  "code": "paused_tasks_present",
  "message": "3 paused tasks exist; run taskgov task current --status paused"
}
```

The numeric value is substituted deterministically. The warning remains in the
existing top-level `warnings` array; `data.tasks`, `data.count`, `data.limit`,
and `data.selection_rules` are byte-for-byte compatible for equivalent input
state. The text renderer appends the equivalent warning after the candidate
list. Warning generation neither invokes an LLM nor changes the exit code.

Database-readiness, argument-validation, and internal-error paths retain empty
warnings. This avoids presenting a paused count that was not obtained from a
successful current-schema, project-matching read. The warning serializer must
use only the integer count and fixed command text, never any stored task field.

The status inspection and candidate selection remain successive read-only
observations, as they are before TG-M9. A concurrent writer can commit between
them, so the advisory count may be stale by the time candidates are emitted.
This is acceptable for a recall warning and must be documented rather than
overstated as a linearizable snapshot. Reusing `connect_snapshot_readonly()`
would import the Viewer-specific persistent-WAL rejection and transaction
contract into `task next`; TG-M9 did not authorize that compatibility change.
TG-M13.1 later standardizes the rollback-journal preflight and coherent
transaction on all operational reads while preserving this two-transaction
advisory boundary.

### Filtered Current-Task Read Model

The `task current` parser adds optional `--status`. `tasks.py` validates it
against `CURRENT_STATUSES` and parameterizes the status predicate; it must not
interpolate caller text into SQL. With no filter, the current SQL, status rank,
priority rank, newest-update order, task-ID tie-break, default limit `20`, and
maximum limit `100` remain unchanged.

With a valid filter, the repository applies one status predicate and returns
the selected one-element tuple in `CurrentTaskResult.statuses`. The existing
latest-event subquery and `current_suggested_next_action()` are reused, so
paused results retain their sanitized pause reason, most recent event, and
resume guidance without duplicating a read model. `task_current_empty_data()`
must accept the effective valid filter for stable missing/migration/project
error payloads. Unsupported current statuses return `invalid_status` and make
no database or Git change.

The command remains bounded. No cursor, offset, `has_more`, or total-matching
field is added in TG-M9. `db status.counts.paused` is the exact population
signal; the existing `--limit` controls the resume-rich subset. This makes
possible truncation visible without coupling the current command to the later
pagination design.

### Compatibility And Guidance Boundary

TG-M9.1 changes only the storage count projection and `db status` text.
TG-M9.2 adds the current-task query/parser and the `task next` warning together,
then synchronizes `SKILL.md`, the one-level workflow/CLI references, README,
and release guidance after those behaviors pass acceptance. This ordering
ensures the warning never advertises a command that is not implemented in the
same verified slice. Unimplemented behavior must not be advertised by the
installable Skill during TG-M9.0 or TG-M9.1.

The following remain separate future designs: current/list/event pagination,
stale detection, checkpoints, parent/child or checklist execution units, and a
networked GitHub update check with once-daily caching. TG-M9 introduces no
network, Git write, target-project mutation, or additional LLM judgment.

## Implemented TG-M11 Completion Integrity Design

TG-M11 is the implemented completion-integrity layer culminating in schema
version 6. It keeps the task/review/storage module boundaries and adds the
smallest state needed to bind a reviewed staged Git tree to a later completion
commit. The lifecycle and input corrections that do not need schema version 6
were implemented and tested before the snapshot migration.

### Done Write Guard And Reopen Transaction

`tasks.py` owns a shared done-state guard used by task edits and every mutable
review service. A syntactically valid command first loads the owning task. If
its status is `done`, the service accepts only the exact reopen shape:

- resulting status `in_progress`;
- one non-empty sanitized `reopen_reason`; and
- no other task field, note, completion option, or gate confirmation.

All other valid write requests return `done_task_requires_reopen`. Existing
parser-required review payloads remain parser-required; TG-M11 does not move
general CLI syntax validation into services merely to prioritize the done
error for malformed commands.

The reopen transaction runs in the existing task-edit savepoint and sets:

```text
status = in_progress
completed_at = NULL
blocked_reason = ''
pause_reason = ''
completion_evidence_kind = none
completion_evidence_revision = ''
completion_evidence_reason = ''
external_revision_approved = 0
completion_commit_required = 1
completion_commit_hash = ''
review_target_kind = ''
review_target_value = ''
review_target_generation = previous_generation + 1
```

TG-M11.2 runs against schema version 5 and therefore touches only the three
review-target fields that exist there. TG-M11.3 extends the same shared reopen
update to clear `review_target_base_revision` after schema version 6 adds that
column. No schema-neutral unit may query or write a future column.

The implementation must check signed 64-bit generation overflow before the
update. It then runs the existing affected-lane ordering guard against the
resulting row. Reopening an earlier sequential task is rejected when it would
place incomplete work before an already active, review-pending, or done
successor. The tool never reopens or rewrites successor tasks automatically.

On success, one `task_reopened` event records the sanitized reason and previous
completion evidence kind/revision. Earlier events, receipts, and findings are
not changed or deleted. Clearing the target while advancing the generation
makes the existing review gate fail until a new target and fresh receipts are
recorded. Clearing typed completion evidence makes the later done transition
require new evidence. SQLite rollback covers the task row and event together.

Review target, receipt, finding-add, and finding-resolution services call the
same guard after locating a valid owner and before normal payload processing or
storage. No structured review mutation is permitted while the task remains
done.

Before a task edit or reopen writes, the service acquires the SQLite writer
lock and rereads the owner row. A task that became `done` is rejected with
`done_task_requires_reopen`; another relevant concurrent task-row change fails
without update and requires a retry. A concurrent review-target change during
completion is instead evaluated by the locked binding and review-gate reread,
so it cannot reuse the earlier target or produce a second completion event.

### Review-Tier Change Guard

`review_tier_change_reason` is a transient task-edit input validated by the
existing privacy and length boundary and stored only in the resulting event.
An increase follows normal edit behavior. A decrease is accepted only when:

```text
existing status and resulting status are in
  ready | in_progress | paused | blocked
review_target_generation == 0
review_target_kind == ''
review_target_value == ''
```

This is the complete schema-v5 predicate. After schema version 6, the guard
also requires `review_target_base_revision == ''`. The extension is introduced
in the migration/snapshot unit rather than referenced by the earlier
schema-neutral unit.

The decrease cannot share a command with completion evidence,
`--verification-complete`, `--review-complete`, or a transition to
`review_pending`/`done`. It emits `review_tier_changed` with the old/new tier
and sanitized reason. No task-added event rename, review-start event latch, or
legacy event backfill is used. Generation `> 0` is the durable structured fact
that review has started and permanently forbids a downgrade.

### Review Gate Correction

`read_review_evidence()` adds a current-generation query for verdict
`changes_requested`, matching project, task, target kind, target value, and
target generation in the schema-v5 unit. The additive count is returned as
`counts.changes_requested_current_generation`. TG-M11.3 extends the same query
to match target base revision after schema version 6 adds it.

Gate satisfaction additionally requires the count to be zero. Enforcement
checks this condition after target presence and blocking findings but before
generic receipt sufficiency so the stable error is
`review_changes_requested`. Resetting the target creates a newer generation;
old changes-requested receipts remain visible history but no longer block.

### Done-Time Git Revalidation

The completion service exposes one canonical read-only Git commit resolver.
The done gate invokes it again for stored `git_commit` completion evidence and
for a `git_commit` review target, even when each value was resolved earlier.
The resolver output must equal the stored canonical full ID. Failures occur
before the savepoint is released and therefore append no success event or task
change.

External revision equality and commit-not-required checks remain local
database comparisons. None of these paths invokes a shell, hook, network
operation, or Git write command.

### Schema Version 6: Git Snapshot Targets

Schema version 6 extends the current target and copied receipt target:

```sql
ALTER TABLE tasks
  ADD COLUMN review_target_base_revision TEXT NOT NULL DEFAULT '';

-- Logical new receipt column; SQLite migration rebuilds the constrained table.
target_base_revision TEXT NOT NULL DEFAULT ''
```

`review_receipts` is rebuilt transactionally so its target-kind constraint
accepts `git_snapshot` in addition to `git_commit`, `diff_fingerprint`, and
`external_revision`. Existing receipt IDs and every finding foreign-key target
are preserved. Existing task targets and receipts receive an empty base
revision. Application validation enforces:

- `git_snapshot`: non-empty canonical Git base, canonical
  `sha256:<64 lowercase hex>` target value;
- every other kind: empty target base revision.

Receipt uniqueness remains `(task_id, target_generation, reviewer_key)`.
Receipt creation copies kind, value, base revision, and generation from the
task. All current-target queries compare all four target components.
The migration also upgrades the shared reopen and review-tier guards to clear
or validate the new base field atomically.

Migration is explicit through `db init`, idempotent, and rollback-safe. The
v2-to-v6 acceptance path preserves the existing 12-task/191-event fixture,
nine historical completion hashes, schema-v5 receipts/findings, IDs, counts,
and project identity, followed by quick and foreign-key checks.

### Canonical Git Snapshot

`review target set --kind git_snapshot` accepts no caller revision. The review
service uses argument-vector, no-shell Git reads to resolve `HEAD^{commit}` and
obtain the index with `git ls-files --stage -z`. It consumes bytes rather than
decoded path text, rejects an unborn `HEAD`, rejects any stage other than zero,
rejects zero-object intent-to-add and sparse-directory entries, and normalizes
entries by raw repository path bytes. Git subprocesses disable optional locks
and lazy object fetching so capture cannot refresh the index or contact a
promisor remote.

The version-1 manifest is:

```text
"taskgov-git-snapshot-v1\0"
<base full object id> "\0"
for each index entry sorted by raw path bytes:
  <mode> "\0" <object id> "\0" <raw path bytes> "\0"
```

The stored target value is `sha256:` plus the lowercase SHA-256 digest of these
bytes. The stored base revision is the resolved `HEAD` commit. The target-set
operation advances generation and writes only the task-governance SQLite
database/event. It does not inspect or record unstaged or untracked content.

For completion binding, the Git service resolves the proposed completion
commit, reads its parent list and recursive full tree as bytes, and normalizes
tree leaves to the same mode/object/path entry form. The candidate manifest
uses the commit's sole parent as base. Binding succeeds only when:

- the commit has exactly one parent;
- that parent equals `review_target_base_revision`; and
- the candidate fingerprint equals `review_target_value`.

Root and merge commits are unsupported in the first version. Added, removed,
changed, renamed, mode-changed, or hook-altered committed content necessarily
changes the normalized manifest or base and fails
`review_target_mismatch`. Commit author, timestamp, and message are not part of
the reviewed material fingerprint.

`git_commit` targets retain exact commit-ID equality. `external_revision`
targets retain exact value equality. `commit_not_required` continues to use a
canonical `diff_fingerprint`. A `git_snapshot` target may close only with
`git_commit` completion evidence.

### Lane And Integer Normalization

One `validate_lane()` boundary trims values for task add/edit/list/next
before defaulting or querying. One integer validator accepts only SQLite
signed 64-bit values. `next_lane_order()` detects maximum-value overflow before
addition. Task insertion/edit and every filter therefore use the same
canonical lane string without changing stored historical rows.

### TG-M11 Documentation Boundary

The v0.3.0 release synchronizes `SKILL.md`, installed-skill references, README,
release/install guidance, version metadata, and product code only after the
code, migration, privacy, no-write, and forward-flow acceptance gates pass.
Viewer snapshot version 3 remains unchanged and omits the internal snapshot
base even though `review target set` and `task show` expose it for the owning
task.

## Approved TG-M12 Scope Control And Local Handoff Design

TG-M12 is layered after TG-M11 schema version 6. It introduces one durable
outbox before Task Contract storage so an out-of-scope discovery can be
preserved without depending on a future Issue Skill. The later Contract layer
then reduces scope reinterpretation without altering legacy task rows.

The implementation remains split by responsibility:

- `tasks.py` owns task lifecycle and the compact task read models;
- `handoffs.py` owns handoff validation, local persistence, idempotency,
  bounded listing, claims, withdrawal, and delivery state;
- `contracts.py` owns Contract validation, immutable revision writes, and
  lifecycle invalidation;
- `storage.py` owns migrations and repository connection boundaries;
- a later `effort.py` may own the optional informational calculation; and
- `self_status.py` owns package-manifest integrity inspection and has no Task,
  SQLite, Git, network, or repair dependency.

Neither handoff nor Contract logic is folded into selection. `task next`
continues to read task rows only.

### Scope Classification Boundary

The workflow supplies one already-required semantic classification: safely
actionable under current authority, blocked by a named unmet condition, or
outside both. The CLI does not try to infer this from prose. Deterministic
consequences remove later branches:

```text
within current authority and safely resolvable -> current Task
unresolvable unmet acceptance condition        -> task blocker
everything else                               -> handoff record, then continue
```

Safety is evaluated separately. A credible risk adds notification; an unsafe
continuation adds the existing blocker to only the affected task/lane.
Handoff state, adapter state, or an effort warning never changes readiness or
completion.

### Schema Version 7: Local Handoff Outbox

Schema version 7 adds one Task-DB-owned delivery table:

```sql
CREATE TABLE handoff_records (
  handoff_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  source_task_id TEXT NOT NULL,
  source_contract_revision INTEGER NOT NULL DEFAULT 0
    CHECK (source_contract_revision >= 0),
  idempotency_key TEXT NOT NULL,
  occurrence_id TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL,
  rationale TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL CHECK (state IN (
    'pending_handoff',
    'handed_off',
    'handoff_withdrawn_by_user'
  )),
  adapter_key TEXT NOT NULL DEFAULT '',
  adapter_version TEXT NOT NULL DEFAULT '',
  delivery_attempts INTEGER NOT NULL DEFAULT 0
    CHECK (delivery_attempts >= 0),
  last_delivery_code TEXT NOT NULL DEFAULT '',
  next_attempt_at TEXT,
  claim_token TEXT NOT NULL DEFAULT '',
  claim_expires_at TEXT,
  receiver_receipt TEXT NOT NULL DEFAULT '',
  withdraw_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  handed_off_at TEXT,
  withdrawn_at TEXT,
  UNIQUE (project_id, idempotency_key),
  FOREIGN KEY (source_task_id) REFERENCES tasks(task_id)
);

CREATE INDEX idx_handoff_project_state_created
  ON handoff_records(project_id, state, created_at, handoff_id);
CREATE INDEX idx_handoff_project_source
  ON handoff_records(project_id, source_task_id, created_at, handoff_id);
CREATE INDEX idx_handoff_due_claim
  ON handoff_records(project_id, state, claim_expires_at);
```

Application validation additionally requires the source task to belong to the
current project and enforces the cross-field state matrix:

- pending has no handed-off or withdrawn timestamp;
- handed-off has an acknowledgement timestamp and no withdrawal data; and
- withdrawn has a sanitized reason and timestamp, no receiver receipt, and
  `delivery_attempts=0`.

The claim token is internal and is never emitted. `receiver_receipt`,
`last_delivery_code`, adapter key/version, summary, rationale, occurrence ID,
and withdrawal reason use bounded privacy validation. No raw adapter response
is retained.

The canonical idempotency input is versioned deterministic JSON containing
project ID, source task ID, source Contract revision, normalized
summary/rationale, and an explicit occurrence ID when supplied. Schema-v7
records capture revision `0`; after schema v8, record reads and captures the
task's current pointer without copying Contract text. The SHA-256 digest is
stored as `idempotency_key`. Without an occurrence ID, an exact replay returns
the existing row. A caller may supply a distinct stable occurrence ID only from
an explicit user instruction or deterministic external occurrence identity.
There is no fuzzy duplicate or recurrence algorithm. CLI omission is `None`
and canonicalizes to an empty occurrence value; explicit invalid occurrence
input maps to `handoff_occurrence_invalid`, allowing omission to remain
distinct from an invalid explicit value.

`handoff record` uses the immediate transaction already acquired by the
initialized write connection to validate the source task, insert or fetch the
exact existing record, and commit before any delivery attempt. Hash replay
also compares all canonical identity fields; collision or corrupt mismatch is
an internal error, never a replay. The repository returns created/replayed
state, but the CLI assembles `durable=true` only after commit succeeds. The
command's success envelope includes the resulting handoff and:

```json
{
  "local_record": {
    "durable": true,
    "created": true,
    "replayed": false,
    "handoff_id": "tg_handoff_..."
  }
}
```

Exact replay sets `created=false` and `replayed=true`. A local commit failure
returns `handoff_not_persisted`, may include one existing stable database cause,
and never falls through to delivery.

Delivery happens after the local commit. An absent or disabled sink leaves the
record pending without warning churn; only an enabled sink failure updates
bounded attempt metadata and returns `handoff_delivery_pending`. It never
updates the source task, `task_events`, or task timestamps. Local retry policy
is at most one retry for a transient SQLite busy/locked result. Privacy
rejection may be retried once only with a newly supplied sanitized abstraction;
the storage layer does not silently redact and claim equivalence.

`handoff list` defaults to state `pending_handoff`, oldest-first
`created_at ASC, handoff_id ASC` order, limit 20, and maximum 100. State and
source-task filters are optional; terminal states appear only when explicitly
selected. It returns `count` plus exact `total_matching`.
Both values and the bounded rows are read under one SQLite snapshot. Compact
list rows contain only `handoff_id`, `source_task_id`,
`source_contract_revision`, `summary`, `state`, `created_at`, and
`updated_at`; show/record/withdraw use a separate full public allow-list that
never includes `claim_token`. Every projection revalidates all stored
free-form fields and the state matrix, returning a fixed internal error rather
than redacting or exposing corrupt/private stored content.
`db status.counts.handoff_pending` remains the exact project population signal.
Paging is deferred; after old pending rows are delivered, withdrawn, or
otherwise filtered, later rows move into the bounded window. `handoff show`
returns one full sanitized row. `task show.handoff_summary` contains only exact
per-state counts for that source task.

`handoff withdraw` uses an immediate transaction and succeeds only when state is
pending, `delivery_attempts=0`, and no claim was ever acquired. It requires one
sanitized user-provided reason. Handed-off, withdrawn, claimed, attempted, or
expired-claim records fail `handoff_not_withdrawable` without mutation.
Success reports changed fields `state`, `withdraw_reason`, and `withdrawn_at`.

### Claim, Delivery, And Sync State Machine

One pending delivery worker claims rows in deterministic
`created_at, handoff_id` order. In one immediate transaction it writes a random
claim token and bounded UTC expiry, clears `next_attempt_at`, and increments
`delivery_attempts` only when the row is pending and due, unclaimed or expired.
`delivery_attempts` counts receiver calls for observability; it is not the
retryable-negative stage. Expired claims can be reclaimed only by sync, never
withdrawn. The sink receives the stable `handoff_id` as its required
idempotency key.

After a receiver response:

- accepted acknowledgement updates to `handed_off` only with
  `WHERE state='pending_handoff' AND claim_token=?`;
- retryable or permanent adapter failure clears the matching claim, retains
  pending state, and stores only a stable error code, attempt count, and
  deterministic next-attempt time; and
- a mismatched or expired token cannot acknowledge or withdraw the row.

If the process stops after receiver acceptance but before the local
acknowledgement, the lease expires. Retrying the same `handoff_id` must cause
the receiver to return the same accepted item, after which the compare-and-swap
can finish. This prevents duplicate external items and the impossible
"withdrawn locally after delivery" state without adding more user-visible
states, because every ever-claimed row is permanently ineligible for local
withdrawal.

Retry version 1 is fixed rather than configured. The stable
`last_delivery_code` is the retryable-result stage machine:

- empty or non-retry stage plus the first retryable negative response becomes
  `retryable_wait_1` and sets `next_attempt_at=now+60s`;
- `retryable_wait_1` plus the second retryable negative response becomes
  `retryable_wait_2` and sets `next_attempt_at=now+300s`;
- `retryable_wait_2` plus the third retryable negative response becomes
  `retry_exhausted` and sets `next_attempt_at=NULL`;
- any permanent negative response becomes `permanent_error` and
  `next_attempt_at=NULL`; and
- an expired non-empty claim is an uncertain acknowledgement and remains due
  for idempotent reconciliation without changing `last_delivery_code`,
  regardless of the normal negative-response cap.

Adapter version change does not reset permanent/exhausted metadata. A future
manual requeue or receiver cancellation contract requires separate approval.

`db status` assembles:

```json
{
  "counts": {
    "handoff_pending": 3
  },
  "handoff_delivery": {
    "adapter_enabled": false,
    "sync_due": false
  }
}
```

The fields are additive to the existing command-specific payload.
`sync_due` is true only when an enabled adapter exists and at least one pending
record:

- has an expired claim; or
- has never been claimed (`delivery_attempts=0`, empty claim, and
  `next_attempt_at IS NULL`); or
- is unclaimed with non-null `next_attempt_at <= now`;

and is neither permanent nor exhausted. This makes schema-v7 pending records
immediately drainable when a later adapter is enabled without rewriting them.
An active claim, disabled adapter, permanent error, or exhausted retry is not
due. The workflow calls `handoff sync --due` once at a session or
execution-unit boundary when both booleans permit it. The command performs one
bounded batch, does not loop until empty, and never reads Issue lifecycle back
into Task state.

### Deferred Local Issue Adapter

The base v7 implementation ships with no receiver and
`adapter_enabled=false`. `handoff record`, list/show/withdraw, exact pending
counts, and delivery-status fields remain fully useful before an Issue Skill
exists. Claim acquisition and `handoff sync` are implemented only with the
later versioned local adapter, so the base Skill contains no dead delivery
workflow.

A later integration unit may inject one `HandoffSink` implementation:

```text
deliver(
  contract_version,
  handoff_id,
  sanitized_summary,
  sanitized_rationale,
  source_reference = (
    project_id,
    source_task_id,
    source_contract_revision
  )
) -> accepted | retryable_error | permanent_error
```

The sink contract must be local and versioned. It is enabled only by explicit
project-scoped user configuration. Before implementation, `AGENTS.md`,
specification safety rules, Skill workflow, and CLI references must be revised
together to permit only this boundary. The Task package does not dynamically
load arbitrary project code, invoke a shell or URL, contact a network or
GitHub, or directly open/initialize/migrate/write an Issue database. The
concrete transport and configuration are blocked until the Issue Skill defines
its intake contract.

Receiver acceptance may return one bounded opaque receipt. Task Skill stores
that delivery receipt only; it does not store Issue state, priority, triage,
resolution, or resulting Task IDs. A future inbound `source_issue_id` may be
added only with the Issue-to-Task conversion contract, not as part of schema
version 7 or 8.

### Schema Version 8: Task Contract Revisions

Schema version 8 adds one current pointer to `tasks` and one immutable table:

```sql
ALTER TABLE tasks
  ADD COLUMN current_contract_revision INTEGER NOT NULL DEFAULT 0
    CHECK (current_contract_revision >= 0);

CREATE TABLE task_contract_revisions (
  contract_revision_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision > 0),
  scope TEXT NOT NULL,
  acceptance TEXT NOT NULL,
  constraints_text TEXT NOT NULL DEFAULT '',
  authority_ref TEXT NOT NULL DEFAULT '',
  change_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE (task_id, revision),
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE INDEX idx_contract_project_task_revision
  ON task_contract_revisions(project_id, task_id, revision);
```

The current pointer is checked in the repository against an existing revision
for the same project/task. Revision `0` intentionally has no table row. Existing
tasks migrate to `0`, so all legacy JSON and lifecycle behavior remains
unchanged.

Contract input uses `--contract-scope`, `--contract-acceptance`,
`--contract-constraints`, `--contract-authority-ref`, and
`--contract-change-reason`. Supplying any option supplies the optional group;
both scope and acceptance are then required. The activation matrix is:

- `task add`: allow the group only with resulting status `ready`,
  `in_progress`, `blocked`, or `review_pending`;
- revision-0 `task edit`: allow only a `ready|blocked -> in_progress`
  transition with empty completion evidence, target, and generation, and no
  other caller mutation;
- revision-0 tasks outside those boundaries remain revision 0;
- revision-N later edit: allow only while `ready`, `in_progress`, `paused`,
  `blocked`, or `review_pending`, as a Contract-only caller change;
- `done` requires reopen first and `cancelled` rejects Contract input.

Constraints and an initial stable authority reference are optional. Initial
copy is allowed only when the caller supplies explicit authority-derived
values; the CLI never prompts or infers them. A raw user prompt is not an
authority reference. Initial `task add` uses the normal `task_added` event.
Activation on the allowed start transition appends one `contract_recorded`
event in the same transaction.

Later revisions require both a non-empty sanitized change reason and a
non-empty stable sanitized authority reference. A governing-file path with a
known revision/hash, roadmap-decision ID, or
`user_instruction:<task-id>:<next-revision>` may be used; raw instruction text
may not. The user-instruction form is generated without another user question
when the current instruction explicitly changes the Contract.

Before allocating a revision, `contracts.py` normalizes CRLF/CR to LF, strips
outer whitespace, and otherwise preserves internal text. Initial omitted
constraints are empty; later omitted constraints preserve the current value,
while an explicit empty value removes them. It compares scope, acceptance, and
constraints with the current row. If all three match, it returns
`recorded=false` with the current revision and performs no write, regardless
of omitted, repeated, or re-labeled authority/change metadata. Supplied
metadata is still privacy/limit validated. A supplied `user_instruction`
reference must use the exact same-task positive-revision syntax, but replay
does not require that placeholder to remain current. A semantic revision
requires at least one of those three fields to change and only then requires
non-empty authority and change reason plus current-or-next revision binding.

For a semantic change, in one savepoint `contracts.py`:

1. loads and validates the current task and done-state guard;
2. checks signed-64-bit revision and review-generation overflow;
3. inserts the next immutable Contract row;
4. updates `current_contract_revision`;
5. resets the complete schema-v4 completion-evidence matrix;
6. if target is empty and generation is `0`, keeps generation `0`; otherwise
   clears target kind/value/base and advances generation;
7. changes `review_pending` to `in_progress`, if applicable;
8. updates task `updated_at`; and
9. appends `contract_revised`.

Failure rolls back every row. Existing Contract revisions, review receipts,
findings, and task history remain. The event stores revision number, sanitized
authority reference, and reason, not the full Contract text.

Only successful responses whose command supplied Contract input gain an
additive sibling:

```json
{
  "contract_write": {
    "recorded": true,
    "revision": 1
  }
}
```

Exact replay reports `recorded=false` and the task's current revision. Commands
without Contract input retain their existing payload exactly.
`task.show.data.contract` returns the current revision and sanitized fields or
revision `0`; list/current/next and Viewer task shapes remain unchanged.
The fixed revision-zero projection uses empty `scope`, `acceptance`,
`constraints`, `authority_ref`, and `change_reason`, plus `created_at=null`.
Exact replay returns the normal edit shape with `changed_fields=[]` and
`event=null`; recorded edits return `contract_recorded` or
`contract_revised`. Add continues to return `task_added`.

`contract_activation_forbidden` covers invalid status/activation boundaries,
`contract_authority_required` covers missing or invalid semantic authority,
and `contract_write_conflict` covers forbidden companion input or a pointer/
write race. Partial groups, missing semantic change reasons, and initial change
reasons use `invalid_argument`. The current CLI deliberately has no expected-
revision option: concurrent identical input records once and replays once,
while different valid semantic input is serialized into successive immutable
revisions. A current-or-next `user_instruction` placeholder formed before the
write lock is rebound deterministically to the revision allocated by the
locked write. A lost-response retry with its older placeholder remains a
write-free exact replay; an actual semantic change with an unrelated revision
placeholder is rejected. Repository reads verify that the pointer is the latest
revision for the same project/task.

`user_instruction:<task-id>:<revision>` is validated exactly. Other stable
governing-file or roadmap identifiers are sanitized opaque references. Their
semantic provenance and the prohibition on current-task-output
self-authorization stay in the Skill workflow; schema version 8 does not add a
general authority or signature engine.

### Optional Effort Advisory Design

Effort Advisory is implemented only under the fixed default-off
`config/effort-advisory.json` profile in the installed Skill. There is no
generic profile repository, inheritance, environment override, configuration
write CLI, or configured command runner. The strict version-1 JSON loader
accepts only the `informational-v1` id, explicit `enabled`, and a threshold
subset for five fixed metrics. Missing or valid disabled configuration is
strictly off; invalid present configuration is disabled with a bounded
continuation diagnostic.

The public read command is `taskgov task effort <task-id>` with stable envelope
command `task.effort`. Through TG-M15.6 it returns measurements, thresholds,
basis, coverage, attribution, unknown reasons, one stable warning key, and
fixed `suggested_action=continue`. `db status` historically gained an
enablement projection only for an enabled or invalid present profile; the
current `task show` route preserves the absent/disabled shape. TG-M16.1's
approved deterministic action override is specified below and is not
implemented by TG-M16.0.

When enabled, first entry to `in_progress` may best-effort store a basis HEAD,
endpoint cleanliness, capture timestamp, and activity generation in Task DB.
This is an authorized side effect of that existing write, not a read-only
inspection. Capture failure records no partial basis and never blocks start.
Subsequent Git observation uses argument-vector, optional-lock-disabled,
no-lazy-fetch reads only. It disables `core.fsmonitor`, ignores submodules,
and validates the stored basis as a full object ID before constructing any Git
argument. Invalid stored evidence is neither passed to Git nor emitted.

Attribution is conservative. It is `unknown` for non-Git repositories, dirty or
uncertain basis/observation endpoints, missing coverage, or activity-generation
evidence showing possible overlap after basis capture. Schema v9 adds
`project_meta.effort_activity_generation`,
`task_effort_activity(task_id, project_id, generation)`, and one
`task_effort_bases` row per task. A project counter and a subject counter let
the observation subtract the subject task's own active-state transitions while
still detecting another task that starts and finishes before observation.
`other_active_at_capture` covers an overlap already present at basis time.
When a stored basis exists, the command reads current activity generations from
a fresh coherent read transaction after Git observation, rather than reusing
the pre-observation snapshot. Without a stored basis, there is no generation
comparison and no second database read. Non-lock/non-journal refresh failure is
`activity_generation_uncertain`; M13 read-side `database_busy` and
`unsupported_journal_mode` remain command errors.
These counters are maintained only after explicit enablement or while an
existing basis needs continued attribution; strict-off databases do no
advisory bookkeeping.

Initial metrics are limited to Git changed files/lines/modules, current
Contract revision count, and recorded source-task handoff count. The Git diff
uses no rename, external-diff, or text-conversion helpers; untracked or binary
content makes line coverage unavailable rather than guessed. Generated fixture
bytes, structured retry counts, configured tests, and advanced risk analysis
remain deferred.

The command never writes an acknowledgement, asks a question, chooses a
handoff, changes status, expands acceptance, or blocks completion. Repeated
observations may repeat the same stable warning. More precise attribution,
persistent dispositions, and automatic enforcement require a separate future
contract. The Skill invokes the command once at the existing
verification/review boundary when `db status` reports it enabled; it does not
invoke it after every command.

### Local Package Self-Status

`self status` is a package-local inspection handler. `cli.py` resolves only the
installed Skill root from its own entry point and calls
`inspect_local_package()`. It deliberately does not resolve caller `--repo` or
`--db`, open SQLite, invoke Git, load an adapter, or contact a network.
The common options remain accepted for CLI compatibility; `--read-only` is
redundant.

The root `release-manifest.json` has this fixed version-1 shape:

```json
{
  "manifest_version": 1,
  "package_name": "task-governance-tool",
  "package_version": "0.7.0",
  "release_origin": "github:VAiring/task-governance-tool",
  "core_files": {
    "SKILL.md": "sha256:<64-lowercase-hex>"
  }
}
```

The loader rejects duplicate or unknown top-level keys, unsupported versions,
malformed identity/origin/version/hash values, more than 512 core entries,
non-portable paths, traversal, absolute/backslash paths, case-fold collisions,
and entries under excluded boundaries. The manifest is capped at 256 KiB.
The manifest excludes itself to avoid a recursive digest.

Core enumeration is deterministic and does not follow symbolic links or
Windows junctions. The only excluded package regions are root `config/`,
`adapters/`, generated `state/`, any `__pycache__/`, and `*.pyc`. A regular
file outside those regions that is not listed in the manifest is modified
core. Missing, digest-mismatched, link-like, or other non-regular listed
entries are also modifications. Expected core files are hashed in streaming
chunks with per-file, total-byte, and entry-count limits; a read race, unsafe
path, permission failure, or exceeded bound yields `unknown` rather than a
guess. The executable entry point disables bytecode generation before runtime
imports so the inspection command itself creates no Python cache.

After a complete comparison, changed paths are sorted, counted exactly, and
truncated to 20 output values. Output contains no absolute package path,
content, digest, symlink target, or operating-system exception detail. Missing
or invalid manifest, unsupported manifest, package identity/version mismatch,
or incomplete inspection is a successful `unknown` advisory. Modified and
unknown results add a fixed warning, but every result returns
`suggested_action=continue`, exit code 0, and no Task/Issue/handoff action.

The manifest-declared release origin is provenance display, not authentication.
Because the manifest is co-located and unsigned, simultaneous modification of
core and manifest can evade detection. Signing, upstream comparison, GitHub
checking, repair, update, download, install, persistence, and automatic
Issue/PR creation are excluded. Package inspection is an explicit setup or
diagnostic surface; it is not inserted into the minimum Task loop.
The diagnostic covers stable local drift and ordinary file-read races; an
adversarial directory swap-and-restore between observations is outside its
non-authenticating threat model.

### Migration, Viewer, And Old-Binary Compatibility

Migrations run only through explicit `db init` in strict
v5-to-v6-to-v7-to-v8-to-v9 order. Each unit is idempotent and rollback-safe.
The migration fixture retains the existing 12 tasks, 191 events, nine
completion hashes, review evidence, project identity, and all new
preceding-version records. Specific rollback tests cover claim/outbox
preservation, v7 handoffs surviving v8, atomic Contract pointer/revision
writes, and v8-to-v9 Effort Advisory metadata creation with an empty disabled
baseline.

An older binary compares the stored schema before a write and rejects any newer
version. It does not apply a reverse migration or overwrite version metadata.
Release guidance must require updating the installed package before migrating
and retaining a recoverable local database copy when crossing schema versions.

Current Viewer snapshot mapping is version 3 for source schemas 5-13.
Historical version 1 covered schema 2 and version 2 covered schemas 3-4.

Normal Task, handoff, and routine Viewer-maintenance commands continue to
require the runtime's exact current schema. Setup inspection and
snapshot-compatibility reads use a separate query-only validator that accepts
snapshot-v3-compatible schemas 5 through the runtime's current schema,
requires contiguous migration history and only the tables/columns it reads,
and still enforces project identity. This preserves old-snapshot reads during
setup migration without allowing writes through an old schema.

Every snapshot carries its actual `source_schema_version`. The snapshot-v3
allow-list remains stable from schema 5 through 13 and excludes handoff,
Contract, Effort, checkpoint, and maintenance-internal rows. Compatibility
tests cover each source schema and prove identical privacy, no-sidecar, and
task-shape behavior.

### TG-M12 Guidance Boundary

When TG-M12 was approved, only governing documents, `plan.md`, and ignored
local task state described it; product behavior remained at v0.3.0/schema
version 6. Each gated implementation unit then synchronizes only its delivered
surface, beginning with TG-M12.1 at v0.4.0/schema v7.

Each implementation unit synchronizes only the CLI and internal contracts it
has actually delivered. The concise Skill workflow adds deterministic Contract
copying and handoff behavior only after their acceptance gates. Effort Advisory
and package self-status are now documented only at their bounded optional
surfaces; the Issue adapter remains unadvertised until its integration unit
passes.

## Approved TG-M13 Operational Release Hardening Design

TG-M13 is a bounded correction layer over schema version 9 and Viewer snapshot
version 3. It does not introduce another repository abstraction, lock manager,
workflow engine, schema migration, or LLM decision point.

### Operational Journal Preflight

`storage.py` owns one shared, read-only operational journal preflight. For an
existing SQLite file it:

1. rejects an adjacent `<db>-wal` or `<db>-shm` entry before opening SQLite;
2. reads only the fixed SQLite header needed to validate the normal file
   signature and the read/write-version bytes;
3. rejects persistent WAL when either header version byte is `2`;
4. accepts rollback-journal header version `1`; and
5. maps rejection to this exact error without including the path, header
   bytes, sidecar metadata, or operating-system exception detail:

```json
{
  "code": "unsupported_journal_mode",
  "message": "task database uses unsupported WAL journal mode"
}
```

The check is applied before all live operational SQLite reads and writes,
including existing-database migration. An absent database with no adjacent
WAL/SHM entry remains the normal `db_not_initialized` or `db init` create path;
an orphan WAL/SHM entry is preserved and rejected as unsupported state. The
helper never opens SQLite, changes `journal_mode`, checkpoints, deletes a
sidecar, or treats a rollback-journal `-journal` file as unsupported. A race
after preflight is handled by SQLite locking or by the response transaction;
TG-M13 does not attempt adversarial TOCTOU prevention.

Only a valid SQLite signature with either header journal-version byte equal to
`2`, or an existing WAL/SHM sidecar, is classified as unsupported WAL. A short
header, non-SQLite signature, or journal-version byte outside `{1, 2}` is not
misreported as WAL; the later SQLite/schema validation returns its existing
sanitized migration/internal error. Failure to read an existing header returns
`internal_error` with `could not inspect database journal mode`. No header
bytes or operating-system detail are emitted.

### Coherent Read Connection Boundary

The live read helper opens:

```text
file:<absolute-db-uri>?mode=ro
```

without `immutable=1`, applies the existing connection configuration, enables
`PRAGMA query_only=ON`, and executes explicit `BEGIN`. Schema-history,
required-object, and project-identity checks then run on that same connection
before any command-specific query. The caller owns that transaction until the
complete response projection has been assembled and closes it without a write.

The repository boundary is response-oriented:

- `db status` reads readiness, identity, all counts, and next-actionable count
  in one transaction when the database is usable;
- task list/current read their selected rows in the same transaction that
  validated readiness;
- task show reads its task, events, review evidence, handoff summary, and
  Contract from one transaction;
- handoff list reads `total_matching` and bounded rows together, and handoff
  show reads and validates its row in the same transaction;
- when a stored Effort basis exists, each pre-Git and post-Git database
  observation is independently coherent; and
- Viewer maintenance reuses the same journal preflight and its dedicated
  compatible-schema snapshot transaction.

`task next` intentionally retains two committed transactions: its status/
paused-warning inspection and its candidate query. A concurrent commit between
them is advisory inter-read staleness already accepted by TG-M9. Neither
transaction may contain internally mixed rows.

When the enabled advisory has a stored basis, `task effort` has two explicit
database phases around Git observation. The first transaction reads the task,
stored basis, Contract revision count, and handoff count, then closes. The
second validated transaction refreshes activity generations. Without a stored
basis, there is no generation comparison and no second database read. A
busy/locked refresh or newly detected WAL state is a command error, while other
bounded refresh failures retain the existing `activity_generation_uncertain`
result.

The old pattern of `inspect_database()` on one connection followed by related
rows on an immutable connection is removed. Storage may expose a context or
repository helper that returns the already validated connection, but feature
modules must not create raw SQLite connections. Missing, migration-required,
and project-mismatch paths remain no-create/no-sidecar inspections.

Rollback-journal contention may make a read wait for the normal Python SQLite
timeout. A successful result is one committed snapshot. M13.1 maps read-side
`SQLITE_BUSY` or `SQLITE_LOCKED` to the stable database contention error below;
raw SQLite text is never emitted. M13.2 extends the identical mapping to
writes.

### Short Write And External Preflight Boundary

M13.2 replaces the former command-wide immediate transaction with this flow:

1. parse and validate caller input without opening a write transaction;
2. resolve the database/repository target and run operational journal
   preflight;
3. perform required Git commit resolution, Git snapshot capture/comparison,
   completion verification, or Effort observation read-only;
4. retain the minimum canonical observations needed for persistence;
5. open the existing database and acquire `BEGIN IMMEDIATE`;
6. revalidate schema history and project identity in that transaction;
7. reread the owning task and every relevant concurrency component;
8. reject a stale component with its existing domain conflict;
9. persist the state row and audit event atomically; and
10. commit immediately before formatting the response.

The reread set is operation-specific rather than a generic version token. It
includes the relevant task status and ordering basis, review target kind/value/
base/generation, current Task Contract revision, and completion-evidence basis.
`updated_at` alone is not a concurrency token. A preflight result can be
persisted only if those governance components still authorize the same write.

Optional Effort basis capture additionally carries a bounded preflight record:
starting project generation, starting subject generation, whether another task
was active, and the captured Git endpoint. After the locked transition updates
the subject activity generation, compare the locked project/subject values to
the starting values. Generation regression, negative delta, or subject delta
greater than project delta discards the best-effort basis. A project delta
greater than the subject delta means another task changed activity during Git
capture and forces `other_active_at_capture=1`; another task active in either
the starting or locked observation does the same. Store the locked
post-transition generations with the basis. This preserves conservative
attribution without keeping the write lock across Git.

Git snapshot capture continues to resolve HEAD before and after reading the
stage-0 index and rejects instability during capture. The stored target is an
observed HEAD/index snapshot, not an acquired Git index lock. An index change
after capture is permitted and is detected later by completion binding or by a
new target generation.

Task, review, Contract, completion, handoff, and Effort writes reuse narrowly
scoped repository functions or savepoints inside the short outer transaction.
No external process, configured command, sleep, backoff, or model call occurs
while `BEGIN IMMEDIATE` is held. The existing handoff-record behavior may retry
the entire fresh transaction once after local busy/locked failure; no other
write gets an automatic retry in TG-M13.

### Contention Error Mapping

After the normal SQLite driver wait, residual read-side `SQLITE_BUSY` or
`SQLITE_LOCKED` becomes the following in M13.1; M13.2 applies the same mapping
to write-side contention:

```json
{
  "code": "database_busy",
  "message": "task database is busy; run the command again later"
}
```

The error uses exit code 2 and the command's existing empty `data` projection.
Its message and error details never expose raw exception text, a path, lock
owner, timeout, or journal details; the normal top-level sanitized `db_path`
envelope field remains unchanged. The error adds no `retryable` or
`suggested_action`. The failed transaction is rolled back and emits no event
or success receipt. Busy timeout changes, sleep, backoff, generic retry policy,
and a user/LLM retry question are excluded.

### Distribution And CI Boundary

The supported stateful layout is one physical project-scoped Skill copy:

```text
<target-project>/.agents/skills/task-governance-tool
```

The primary invocation starts in `<target-project>` and calls the script by
that relative install path. A caller starting inside the Skill directory must
supply `--repo <target-project>`; otherwise `"."` correctly identifies the
Skill directory itself. Because a governed directory need not be a Git
repository, runtime code does not require `--repo` globally and never re-roots
the governed target to a Git root. Only setup/doctor ignore preflight scans the
canonical target's ancestor chain for a `.git` marker. Existing canonical
absolute-path project identity and its relocation limit remain explicit.

Symlink and Windows junction/reparse-point installs are unsupported rather than
given another state-root algorithm. Documentation and self-containment tests
must reject or clearly diagnose them without deleting or rewriting state.
User-wide Skill operating paths are removed from governed-project guidance.

The recommended target-local `.gitignore` guidance is:

```gitignore
/.agents/skills/task-governance-tool/state/
```

An effective parent rule for that same canonical directory is also accepted.
Release archive guards separately exclude generated database/viewer/runtime
artifacts and sidecars from distributable packages. They do not recommend
repository-wide database-extension globs.

The supported runtime baseline is Python 3.12+. Windows CI runs exact 3.12 and
3.14 matrix entries and covers junction rejection. No Linux or macOS support
claim is inferred. Viewer snapshot-v3 tests cover every source schema 5 through
9, including schema 8. The packaged handoff privacy workflow permits at most
one sanitized abstraction retry and never stores or forwards raw rejected
content.

### TG-M13 Staging And Review Boundary

M13.1 synchronizes this approved design, the specification, implementation
roadmap, and CLI contract before changing runtime reads. M13.2 then changes
write-transaction ownership. M13.3 synchronizes active Skill/release/CI
surfaces only after the corresponding compatibility checks pass. M13.4 performs
integrated local acceptance and two independent Tier 2 reviews against one
final revision. Push, PR, workflow dispatch, or publication remains a separate
explicitly authorized external action.

No M13 unit adds a command, schema version, Viewer snapshot version, Task gate,
Issue behavior, normal diagnostic prerequisite, LLM judgment, or routine stop.

## Implemented TG-M14 Daily UX And Local Continuity Design

This section is the current v0.8.0 implementation design. M14.0 originally
fixed it before the bounded runtime units and final publication synchronization
were consumed.

### CLI And Envelope Boundary

The final parser contains only the 20 leaves enumerated in the specification.
Root preprocessing recognizes lexical `--json`, removed/unknown root commands,
and removed `--db` before dispatching a command handler or invoking package,
project, Git, or state resolution. It emits the fixed `invalid_command` or
`invalid_option` failure and never echoes a rejected token or option value.
Argparse does not retain compatibility subparsers.

The final formatter removes `db_path` from `CommandResult` and public
serialization rather than populating it with `null`. Internal
`DatabaseTarget`, repository constructors, tests, and setup services still take
explicit paths. Public commands derive the one canonical project-scoped path
from the lexical physical Skill and `--repo`.

Compact task projections are separate allow-list formatters over the existing
coherent query results. They do not add repository queries. The formatter
applies UTF-8 code-point-safe event-summary truncation, adds rows in existing
order until the command cap, and then marks the projection truncated. Default
current/next formatters remain unchanged through M14.1; default JSON
`task show` adds only the routing field below. M14.2 owns the one global
envelope/path removal. Compact mode is JSON-only and therefore adds no
parallel compact text formatter.

M14.1 bounded modes keep the envelope shape. Compact success projection first
fits identity against the exact rowless metadata for that query result, rather
than placeholder counts, so the later complete-row prefix remains within the
cap. A final emit-boundary formatter covers compact success/errors, JSON
completion-check success/errors, and every lexical-JSON argparse rejection.
Parsed handlers select their own cap from the parsed mode. Parse failures need
no shadow argv parser: the formatter applies the smallest 8,192-byte cap to all
JSON parse errors, which is also within the larger compact caps. It preserves
`db_path` and `project_id` when the envelope fits, otherwise replaces `db_path`
and then `project_id` with null. If an error still cannot fit because its
diagnostic message embeds an unbounded rejected value, the formatter keeps the
first safe error code and replaces only the message with the fixed bounded
diagnostic-omission text. This final boundary also accounts for portable CRLF
newlines. M14.2 still owns removal of the `db_path` key from every command.

M14.1 reuses the existing Effort Advisory profile loader in default JSON
`task show` and adds only `data.effort_advisory_enabled`. It performs no Git
observation. Enabled maps to `true`; absent/disabled/invalid maps to `false`,
with the existing invalid-profile continuation warning retained. The active
Skill reads this already mandatory response and mechanically routes one
existing `task effort` call only when true; text `task show` is unchanged.

Completion check and thin completion share one `CompletionRequest` and the
existing ordered validator. Check captures a short validated basis, closes it
before Git observation, and performs a second short read to reject a changed
basis before returning the validator projection. Write preflight likewise
observes Git outside the write transaction and locked execution revalidates the
same request. No SQLite transaction is held during Git, and no check receipt or
readiness token is stored.

### Doctor Read Model

Doctor orchestration has two required observations and one conditional
preflight observation:

1. the existing bounded package inspector, which never opens SQLite;
2. only for a Git-candidate target, the single bounded effective-ignore process
   described above, completed before SQLite; and
3. at most one operational journal preflight and one coherent project read
   transaction for project state, task counts, handoff delivery, and
   maintenance state.

The formatter combines them without claiming cross-source atomicity. A project
readiness result owns exactly one `project_state` code. When it cannot supply a
coherent current-schema snapshot, the three dependent components are fixed
`unavailable` objects. Package modified/unknown remains a warning even when a
project error determines exit 2. All doctor paths are no-write, fixed
`continue`, and storage-path-free.

Layout validation belongs only to `project_state`. A clean package can
therefore coexist with `project_state.code="invalid_layout"` for a competing
install or missing self-host structure; a linked/uninspectable package may
independently be package `unknown`. The formatter never rewrites package
integrity to encode layout.

M14.2 implements the base decision rows and only setup-derived maintenance
facts. M14.3 extends the backup object in place; M14.6 extends the Viewer object
in place. Neither task adds a second component or duplicates opt-in state.
Top-level setup eligibility is computed only after applying the specification's
shared ordered preflight; it is not copied from the SQLite readiness row.
Maintenance objects always use the exact nullable key/code shape in the
specification, so staged implementations do not invent alternate component
schemas.

### Setup Service And Shared Backup Primitive

Setup is an orchestration service, not a shell composition of removed CLI
commands. It calls package/install/ignore/project preflight, the internal
storage initializer or migration service, the shared backup primitive, the
one-way opt-in repository, and the existing Viewer renderer directly.

The layout validator accepts the ordinary physical
`<repo>/.agents/skills/task-governance-tool` package and one development-only
self-host shape. The latter requires explicit `--repo`, a physical package
exactly at `<repo>/task-governance-tool`, the five fixed regular governing
source documents and three fixed package entry/manifest files listed in the
specification, and absence of a competing project-scoped install. It uses
bounded canonical filesystem checks only. Failure uses the fixed
`project_scope_required` or `unsupported_install_layout` boundary. The source
shape derives the same existing package-local canonical state path; no copy,
relocation lookup, alternate state repository, environment switch, or public
mode flag exists. The separate ignore inspector below may invoke Git only
after layout, target, and canonical state ownership are known.

### Enclosing Git Ignore Inspection

`project_scope.py` owns the TG-M15.4 ignore boundary. It does not introduce a
Git service or change `DatabaseTarget`. The existing preflight calls one
private helper with the canonical governed target and accepted layout after
canonical skill-root and state-ownership validation.

The helper first performs a bounded inclusive upward filesystem scan for an
existing or link-like `.git` marker, starting at the canonical target and
stopping at the nearest marker or filesystem root. A marker-inspection or
ancestor-traversal error fails closed without a subprocess. No marker means
non-Git success and no subprocess. A marker means Git-candidate, including an
ordinary nested worktree, linked-worktree `.git` file, or submodule `.git`
file.

For a Git-candidate the helper selects exactly one governed-target-relative
operand: `.agents/skills/task-governance-tool/state/` for ordinary layout or
`task-governance-tool/state/` for self-host. It uses forward slashes, retains
the trailing slash for directory semantics, and passes the complete operand as
one argument after `--`. It invokes one fixed process through the existing
`safe_git_command()` and `safe_git_environment()` helpers:

```python
[
    *safe_git_command(governed_target),
    "-c",
    "core.fsmonitor=false",
    "check-ignore",
    "--quiet",
    "--no-index",
    "--",
    target_relative_state_directory,
]
```

The implementation uses an argument array, `stdin=DEVNULL`,
`stdout=DEVNULL`, `stderr=DEVNULL`, `check=false`, and a two-second timeout.
Return code 0 is `True`; every other result and every `OSError` or
`SubprocessError` is `False`. Callers continue mapping `False` only to the
existing `state_ignore_required` issue, so no new error or output field exists.
The subprocess completes before any setup plan or SQLite write begins, and
doctor remains read-only.

The first `run_setup` scope inspection and the one doctor scope inspection use
`include_ignore=True`. Setup's later stage-by-stage `_revalidate_scope()`
checks use `include_ignore=False`: they retain layout, package, project, and
state-ownership validation without repeating the process. Thus setup,
`setup --read-only`, and doctor each launch at most one ignore process per
invocation, while normal Task, handoff, and review commands launch none.
Perfect ignore-rule TOCTOU detection is outside the non-adversarial local
threat model.

Git itself evaluates parent and target-local rules, negations, gitfile/linked
worktrees, submodule boundaries, `.git/info/exclude`, and configured global
excludes. The tool neither parses a `.gitignore` file nor reports which rule
matched. It does not re-root the governed target to `--show-toplevel`, and it
does not use an enclosing root for project identity, state paths, setup,
Viewer, or completion.

Focused tests use physical temporary repositories for effective-rule behavior
and mocks only for launch/timeout/environment assertions. They snapshot the
governed target and Git administrative directory before doctor and rejected
setup paths. Tests must cover a nested target with target-local and parent
rules, effective negation, linked worktree or gitfile, submodule-local versus
superproject-only rules, no-marker non-Git, nonzero/timeout/launch failures,
and the unchanged no-Git behavior of normal Task commands. No sentinel,
tracked-artifact detector, authentication, retry, cache, or generic Git
abstraction is added.

`--read-only` builds a `SetupPlan` with booleans for restore, initialize,
migrate, backup, configure, and Viewer publish plus the effective
interval/retention. It does not create directories, locks, temporary files,
SQLite connections that can recover/write, or Viewer output.
The write path revalidates preflight immediately before each irreversible
stage. A valid completed stage is never rolled back merely because a later
opt-in or Viewer stage fails; the fixed partial-result row makes rerun resume
from the first missing stage.

`SetupPlan.configure` is true for an unconfigured source or an actual explicit
policy change, never merely because migration is due. A configured v10+ source
with omitted/equal policy plans `migration_backup`, `database_migrate`, and
`viewer_publish`; an actual policy change inserts `maintenance_configure`
between migration and Viewer publication. Result arrays and partial failures
use the corresponding exact ordered prefix from the specification.

When the canonical database is absent, setup scans only its canonical
`backups` directory through the existing exact filename parser and artifact
validator. It distinguishes no managed names, at least one valid same-project
generation, and managed names with no valid same-project generation. The first
case retains fresh initialization. The second chooses the newest valid
`(published_at, generation_id)` pair mechanically. The third fails closed with
`setup_restore_failed`; invalid, foreign, linked, and unrecognized files are
not changed. A newer invalid file does not hide an older valid generation.

The recovery write revalidates the selected candidate under the existing
zero-wait backup artifact lock. It copies through `sqlite3.Connection.backup`
into a new sibling temporary database and applies no mutation to the source
artifact. For schema v11+, a recovery-only normalization removes generation
rows without a currently valid artifact, imports valid file-only generations,
and points the temporary maintenance row at the selected newest generation
without pruning artifact files. Schema v10 records the selected setup-copy
metadata; earlier schemas need no metadata repair. The temporary database then
passes the normal schema history, project identity, `quick_check`, foreign-key,
regular-file, and identity checks.

Publication uses a same-directory atomic no-clobber hard-link operation rather
than `os.replace`, so a canonical database that appears concurrently is never
overwritten. Missing-database preflight uses `lstat` to reject any lexical
`<canonical-db>-journal` entry before both fresh initialization and candidate
selection; recovery repeats the check before copying and immediately before
publication. It never opens, deletes, or changes that rollback journal,
preventing orphan residue from being applied to either a fresh or recovered
database. Temporary cleanup removes the extra link after successful
publication. The lock remains held through any required normal
migration-backup and migration. A current-schema recovery continues directly
to Viewer publication; a supported older recovery uses the existing
migration sequence. Setup rereads the recovered state before formatting any
success or later-stage failure so `maintenance_enabled` reflects durable state.
Preview may expose the selected source schema as `schema_from`, but it exposes
no path, generation identity, timestamp, hash, or exception.

Fresh initialization also takes the artifact lock, rechecks the canonical
database and rollback-journal absence, and reruns candidate selection
immediately before `initialize_database`. If a valid candidate appeared after
preflight, setup fails the stale plan with `setup_restore_failed`; the next
invocation selects that candidate normally.

The sole backup primitive:

1. opens the source through the operational rollback-journal boundary;
2. copies through `sqlite3.Connection.backup` to a fresh temporary file under
   the canonical managed backup directory;
3. validates schema support, project identity, `quick_check`, and
   `foreign_key_check`;
4. closes the temporary connection and file;
5. atomically publishes the generation under a canonical filename containing
   its internal generation identity, publication timestamp, and the validated
   1-20 `publication_retention` fixed by the caller before copying; and
6. returns that bounded identity/time metadata, never a public path.

The canonical directory is `<project-state>/backups`. The only recognized
pre-v11 basename is
`taskgov-backup-v1_<YYYYMMDDTHHMMSSZ>_<32-lowercase-hex>_r<1-20>.sqlite`;
the internal generation identity is `tg_backup_<same-32-lowercase-hex>`.
Parsing must be exact. Other names, links, non-regular files, invalid copies,
and copies for another project are neither managed nor pruned.

Setup migration calls it before beginning migration. M14.3 calls the same
primitive after a business transaction closes. Rotation is a caller policy
around this primitive, not another copy implementation. Before schema v11
exists, setup recognizes only canonical regular artifacts whose copied
database validates as the same project, orders them by publication time then
generation identity, and applies the effective explicit/default retention
after each successful publish. Repeated failed migrations therefore remain
bounded without treating an arbitrary file as managed. Retry-time recovery may
finish an interrupted pre-v10 prune using only the newest recognized
artifact's immutable filename retention; the new attempt's requested value is
not applied until its own copy publishes successfully.

Write-mode setup takes the canonical zero-wait backup artifact lock before
pre-v11 reconciliation/publication and holds it through the corresponding
migration commit. The lock is OS-held on one byte of a validated regular file,
is released by process termination, and never extends a SQLite writer
transaction across the copy. This binds the metadata passed to migration to a
generation that another setup cannot prune. M14.3 reuses the same primitive
for routine and v11+ setup backup stages.

For an existing v10 source, `migration_backup` is not complete until the
published identity/time/outcome and `applied_backup_generations` are written to
`project_maintenance` in one short transaction. Pruning must retain that
identity and occurs only after the metadata commit. On restart, a newer fully
published canonical generation is validated and reconciled to the v10 pointer
and applied retention before pruning. A v1-v9 source has no pointer; its
successful migration transaction creates v10 with the current copy metadata
and applied retention before v11 seed/import. Thus v11 never validates a stale
pointer against a set from which its target was already pruned.

The schema-v10 maintenance row stores the one-way `enabled_at` plus bounded
`backup_interval_minutes` and `backup_generations`, shared last-success/outcome,
latest managed-generation identity, and nullable internal
`applied_backup_generations`. Setup parses the only public configuration
options, applies defaults 30/3 only for an unconfigured project, preserves
omitted existing values, and atomically writes a changed policy. Equal explicit
values are a replay. The short configuration transaction rereads the current
maintenance row and resolves omitted fields there, rather than writing values
frozen by an earlier setup preflight. A policy-only update changes no applied
value, invokes neither coordinator nor pruning, and is enforced only after the
next successful managed publication records the configured value as applied.
`SetupResult` uses the exact scalar-state semantics and null rules in the
specification; previews never report planned state as durable state.

The v10 table is `project_maintenance`. Besides `project_id`, its fixed columns
are `enabled_at`, `backup_interval_minutes`, `backup_generations`,
`applied_backup_generations`, `backup_last_success_at`,
`backup_last_outcome_code`, `backup_last_outcome_at`,
`latest_backup_generation_id`, `viewer_last_success_at`,
`viewer_last_outcome_code`, and `viewer_last_outcome_at`. A database trigger
prevents a non-null `enabled_at` from changing or returning to null.

`SetupPlan` resolves immutable `publication_retention` before the backup
primitive runs. A partial/unconfigured source uses the validated explicit
setup value or default 3; a configured source uses the stored value observed
before the plan's later configuration stage. Routine work freezes the stored
value observed for that attempt. The primitive places the value in canonical
filename metadata, and metadata insertion copies that value into
`applied_backup_generations`. File-only import therefore recovers the value
from the artifact rather than consulting a policy that may have changed after
publication.

### Post-Commit Maintenance Coordinator

Successful business services return a small internal `MutationOutcome`
containing whether state changed and whether the change is Viewer-relevant.
The CLI commits and closes the business connection before passing that outcome
to a bounded coordinator. Read-only, failed, replay, no-op, and coordinator
metadata writes never call it.

When setup opt-in exists, the coordinator runs in fixed order:

1. Viewer refresh if relevant;
2. routine backup if due.

The two attempts are independent. Each opens its own validated canonical
regular lock file and takes a one-byte OS advisory lock with a zero-millisecond
wait. File existence is not ownership; the OS releases ownership on process
termination, and a leftover regular file needs no stale cleanup. Lock failure
emits one fixed continuing warning and leaves the work due. No common job
abstraction, background execution, sleep, retry loop, or durable queue is
introduced.

Backup due is a latest outcome of `deferred|failed`,
`last_success_at IS NULL`, or at least the configured interval since that
timestamp. Failed attempts do not advance success time. Every v11+ managed
backup stage, including setup migration and routine work, takes the same
artifact lock and first reconciles canonical files, generation rows, the v10
pointer, and applied retention using the exact specification order. A
published file without a row is validated/imported; an unusable or missing row
target is removed without touching an untrusted path; and an interrupted prune
deletes file before row against `applied_backup_generations`. A newly reduced
configured value is not applied by reconciliation alone. Incomplete
reconciliation prevents another publication; routine emits `backup_failed`,
while setup returns `setup_backup_failed` and performs no migration.

A successful publication writes one atomic file, then inserts its row and
updates the v10 pointer/outcome plus the artifact's immutable publication
retention in one short transaction, then prunes file-before-row. Restart
reconciliation by either routine work or setup recovers that same value from a
file-only artifact, covers termination at every boundary, and prohibits
crash-residue accumulation beyond the previously valid set plus one in-flight
generation. Defaults are 1,800 seconds and three. Injected clock and artifact
services are test seams, not public options.

Backup-eligible mutations are task add/edit/complete/checkpoint, handoff
record/withdraw, and review target/receipt/finding add/resolve. Viewer-relevant
mutations are the same set except handoff record/withdraw, because snapshot v3
does not project the handoff outbox. Setup initialization/migration/repair uses
its direct Viewer stage and never re-enters the post-commit coordinator. Effort
inspection, doctor, setup preview or configuration-only setup,
task/handoff/review reads, exact replay, and internal maintenance metadata
trigger neither path. Viewer generation increments atomically with each
Viewer-relevant business write. Refresh acquires the Viewer lock, compares
source and last-rendered generation, renders when behind, rechecks once, and
performs at most one follow-up. It never overwrites a newer published
generation.

Maintenance warnings use only these fixed pairs:

- `viewer_refresh_deferred`:
  `Viewer refresh was deferred; task result is unchanged`
- `viewer_refresh_failed`:
  `Viewer refresh did not complete; task result is unchanged`
- `backup_deferred`:
  `managed backup was deferred; task result is unchanged`
- `backup_failed`:
  `managed backup did not complete; task result is unchanged`

Messages contain no path, exception, expected/actual hash, raw output, retry,
stop, or model choice. The primary command stays successful. Doctor reads the
latest fixed outcome without starting work.

### Schema Sequence

Schema changes are feature-owned:

- v10 `project_maintenance`: one project row whose nullable `enabled_at` and
  policy fields represent an initialized/migrated partial setup; configuration
  fills policy and makes `enabled_at` immutable once non-null. Shared backup
  last-success/outcome/latest-generation and applied-retention fields are
  written by the pre-migration setup copy as part of the backup stage whenever
  v10 exists, or by the v1-v9 migration transaction that creates v10, and later
  by routine backup, alongside setup Viewer base facts. A policy-only change
  leaves applied retention unchanged until the next successful managed
  publication;
- v11 `managed_backup_generations`: published generation identities only. Its
  migration discovers every canonical regular same-project artifact retained
  by setup, validates it, orders by publication time and generation identity,
  inserts one row per artifact, requires the non-null v10 latest identity to
  match one row, and prunes the recognized seeded set to the current setup
  publication's applied retention. Unrecognized, linked, invalid, and foreign
  files are untouched. Due is derived from v10 policy and last success rather
  than stored twice;
- v12 `task_checkpoints`: append-only checkpoint fields, source Contract
  revision, timestamps, and a task/project foreign key;
- v13 `viewer_maintenance_state`: business generation, rendered generation,
  last success, and fixed latest outcome.

The tables use bounded text checks and project ownership. No generic
maintenance jobs table exists. Migrations are invoked only by setup, preserve
all prior rows and completion/review evidence, and use the existing migration
history discipline. Viewer snapshot v3 accepts schemas 5 through the current
stage but intentionally projects none of these internal fields or checkpoint
content.

### Checkpoint Repository

Checkpoint input is validated by the common privacy guard plus the exact byte
limits before opening a write transaction. Under the short write transaction,
the repository rereads the task, rejects done/foreign state, obtains the
current Contract revision, and compares the latest checkpoint for exact
replay. A new row and content-free `Checkpoint recorded` event are atomic.
`tasks.updated_at` is not modified; activity/event generation still records the
real state mutation for maintenance and history.

Current/show attach at most one latest checkpoint using deterministic
`created_at DESC, rowid DESC`. There is no checkpoint list repository, search,
semantic summary, or completion integration.

### Review Packet Builder

The builder reads task, Contract, target, and review counts from one validated
SQLite transaction, closes it, then performs bounded target observation. It
supports `git_snapshot` by recapturing/validating the stored snapshot and
`git_commit` by resolving the commit and listing changes against its first
parent (or the empty tree for a root commit). Merge commits use their first
parent. `diff_fingerprint` and `external_revision` use no Git observation and
emit `changed_paths_available=false`. The builder performs at most ten
shell-free Git subprocesses with the existing environment and timeout
restrictions.

Paths are decoded and validated as relative project paths, sorted bytewise, and
bounded by count, individual bytes, and aggregate bytes. Unsafe paths fail.
Safe overflow is explicit truncation. A final size check precedes text/JSON
serialization. The focus and receipt command shape are constants in code, not
free-form caller input. Four common focus rows are followed by exactly one
target-kind constant. Snapshot guidance binds review to the stored base and
stage-0 index; commit guidance binds it to the canonical commit and
first-parent/empty-tree comparison; the two non-Git rows prohibit PASS without
exact caller-supplied material bound to the target value. Selecting that row
runs no Git or model call. The builder neither launches a reviewer nor
persists a packet. After observation it opens one second short read-only
transaction and compares project/task identity, Contract revision, and all
target fields and generation with the first read. A mismatch returns
`review_packet_stale`; no SQLite transaction remains open during Git work.

The missing-target and stale-basis errors use the specification's exact
sanitized messages. Path and size failures likewise use their fixed messages;
the builder never exposes the rejected path, observed size, revision value, or
Git/OS exception.

### Fixed Test And Call Budgets

Constants are:

```text
compact current bytes     24576
compact next bytes        16384
completion check bytes     8192
checkpoint caller bytes    6144
review packet bytes       32768
review paths                 100
review path bytes             240
review aggregate path bytes 16384
review Git subprocesses        10
backup interval default sec  1800
backup interval minutes min      1
backup interval minutes max   1440
backup generations default       3
backup generations min           1
backup generations max          20
artifact lock wait ms            0
backup attempts/mutation          1
Viewer renders/mutation           2
write-sequence length              8
```

The default-off no-finding Tier 2 governance flow uses at most nine subprocess
calls and the existing Effort-Advisory-enabled flow at most ten, as defined in
the specification. The branch is a deterministic boolean route, not an LLM
judgment. Test fixtures and time offsets are also exactly those in the
specification. Tests hard-fail count, byte, and attempt violations. The broad
`enabled <= disabled + 10 seconds` and per-command five-second ceilings are
performance regressions, not permission to loosen a hard bound or introduce
async architecture.

### Staging And Publication

Every M14.1-M14.6 unit that changes a manifest-covered core file refreshes the
existing release-manifest file inventory/hash in the same exact reviewed
revision. M14.1 asserts the still-public `self status=clean`. From M14.2
onward, the same inspector is asserted through doctor package status `clean`
while public `self` is separately asserted to return `invalid_command`. This is
an integrity prerequisite for the next unit's setup; it does not change release
version/origin or publish an active Skill. M14.7 owns final release metadata,
version decision, package inventory, and publication synchronization.

- M14.0: formal planned contracts and Task DB authority only.
- M14.1: compact and completion runtime/help/tests plus final routing contract.
- M14.2: setup, doctor base, public self/db/`--db` removal, opt-in, shared
  backup primitive, setup Viewer repair, and snapshot-v3 source support through
  schema v10.
- M14.3: backup due/rotation, doctor backup rows, backup-only benchmark, and
  snapshot-v3 source support through schema v11.
- M14.4: typed checkpoint, schema v12, and snapshot-v3 source support through
  schema v12.
- M14.5: Review Packet.
- M14.6: Viewer generation/maintenance, public web/custom-output removal,
  doctor Viewer rows, and Viewer/combined benchmarks.
- M14.7: active Skill, metadata, README, release/final manifest metadata, final
  help, combined tables, and integrated acceptance.

No earlier task advertises an unimplemented active surface. No task introduces
overview, aliases, imports, restore/export, relocation, browser automation,
search/paging, Issue lifecycle, generic health checks, or workflow automation.

## Static Task Viewer Design

The Task Viewer is a projection of current SQLite state, never an independent
authority. The implementation flow is:

```text
Viewer-relevant business commit
  -> task_events generation trigger
  -> post-commit coordinator
  -> zero-wait canonical Viewer lock
  -> one SQLite read transaction
  -> task/event repository query
  -> snapshot-v3 object + captured source generation
  -> base64-encoded UTF-8 JSON
  -> bundled HTML template
  -> atomic task-viewer.html replacement
  -> short rendered-generation update
  -> browser file:// view
```

No browser-to-database connection exists. There is no public `web` parser,
custom-output resolver, Viewer maintenance command, or Viewer choice in the
normal Skill loop. Setup invokes the same canonical publisher directly for
initial publication and explicit repair; it never re-enters the coordinator.

### Repository Read Model

`tasks.py` remains the task/event repository boundary. Its dedicated Viewer
helper:

- select every task for the current `project_id`
- serialize the snapshot-v3 explicit task/evidence allow-list
- order tasks with the existing `task list` priority/lane/order/time/ID rules
- fetch at most 10 events per task, ordered by `created_at DESC, rowid DESC` to
  match `task show` tie behavior
- return plain dictionaries to `viewer.py`

This does not broaden the public `task.list` JSON task shape.

### Viewer Presentation Profile

`viewer_config.py` owns one narrow optional policy file:

```text
<installed-skill-root>/config/viewer.json
```

This is separate from backup policy in SQLite because it controls only browser
presentation. No config file or empty `config/` directory is shipped or
created by taskgov. Absence resolves to internal interval `0` and is a valid
disabled state. A present file must be no larger than 16,384 bytes and contain
exactly `schema_version=1`, `profile="visibility-refresh-v1"`, and JSON-integer
`refresh_interval_seconds` in the inclusive range 5-3,600. The parser rejects
booleans, floats, duplicate/unknown/missing fields, invalid UTF-8, malformed
JSON, and trailing alternate objects.

The loader is a dedicated function rather than a generic settings framework.
It derives the canonical path from the accepted physical Skill root, checks
every existing or link-like path component for symlink/reparse behavior,
opens the final file read-only with no-follow semantics where supported,
requires a regular file, reads at most the cap plus one byte, and compares
descriptor/path identity and bounded metadata before and after reading. A
broken link, directory, device, replacement race, or uninspectable component
fails closed through one sanitized `ViewerConfigError` that contains no path,
content, OS exception, or expected/actual metadata.

The optional runtime file is not a release-manifest entry. The loader and every
other changed package source remain manifest-covered core. The profile is read
once at the start of each direct setup or routine Viewer publication attempt;
the same resolved integer is reused by an initial and possible follow-up
render. Doctor does not call the loader. Taskgov never creates, edits,
migrates, or suggests a routine choice about this file.

### Snapshot Assembly

Snapshot version 3 accepts source schemas 5 through 13 without adding internal
maintenance or checkpoint fields. Historical snapshot versions remain
self-contained artifacts but are not produced by the current runtime.

`viewer.py` owns this mapping and constructs:

- `snapshot_version`
- one injected/current UTC `generated_at`
- project ID and display name only
- current database schema version
- total and per-status counts
- ordered tasks with bounded `events`

Do not embed the canonical repository path, database path, environment data, or
tool events. Also exclude v13 generation/outcome state and checkpoint content.

Serialize with deterministic JSON settings, UTF-8 encode the bytes, and base64
encode those bytes before template insertion. The template must contain
exactly one fixed snapshot placeholder and one distinct decimal interval
placeholder. Rendering validates the resolved interval as `0` or 5-3,600 and
fails with `internal_error` if the template is missing, unreadable, or has zero
or multiple occurrences of either placeholder.

The browser decodes the base64 payload with standard browser APIs. Task content
must be assigned with `textContent` or equivalent text-node APIs. Do not pass
stored values to `innerHTML`, `insertAdjacentHTML`, `eval`, `Function`, URL
attributes, inline event attributes, or CSS declarations.

### Canonical Path, Lock, And Atomic Write

The only output is:

```text
<installed-skill-root>/state/projects/<project-id>/viewer/task-viewer.html
```

Production derives this from the canonical database state directory; an
internally injected target therefore stays inside its own isolated state.
Before opt-in, no Viewer or lock directory is created. The resolver rejects
reparse parents, a linked or non-regular destination, a database alias, and
containment changes.

The Viewer lock is a separate canonical regular file beside the HTML. It uses
the shared narrow one-byte OS advisory-lock primitive with zero wait. File
existence is not ownership and the lock file is not deleted as stale.

Write rendered bytes to a unique temporary file in the Viewer directory, flush
and close it, then use `os.replace`. Clean the temporary file after failure.
The prior HTML remains intact until replacement succeeds.

### Generation And Publication Algorithm

Schema v13 adds one `viewer_maintenance_state` row per project:

- nonnegative `source_generation`;
- nullable nonnegative `rendered_generation` no greater than source;
- nullable last success;
- latest `succeeded|deferred|failed` outcome and time.

Fresh/migrated rows start at source zero and rendered null, forcing setup
publication without inventing a historical generation. An `AFTER INSERT`
trigger on `task_events` increments source in the same business transaction.
Generation is a monotonic change token rather than a public command count;
combined task/Contract work may insert more than one event. Handoff and
internal maintenance writes create no Viewer event.

`viewer_maintenance.py` first performs a cheap DB-only opt-in/due read. Under
the artifact lock it rereads state, then opens one query-only snapshot
transaction that captures source generation and task rows together. The
transaction closes before render or file I/O. After atomic replacement, a
short conditional write records the captured generation without lowering an
existing rendered generation. The success outcome uses publication-completion
time rather than capture time, so a completed catch-up supersedes contention
recorded while it held the lock. The service rereads once and performs at most
one follow-up render. Any later churn remains due. The resolved presentation
interval is loaded before either render and reused for both, so one publication
attempt cannot publish two policy values.

Lock contention maps to `deferred`; another bounded failure maps to `failed`.
Recording outcome metadata is best-effort so maintenance failure cannot replace
the primary command result. The post-commit coordinator runs Viewer first and
backup second with independent fixed warnings.
Outcome timestamps have second precision. An equal-time `deferred|failed`
attempt replaces the prior outcome only while source generation remains ahead
of rendered generation; an equal-time successful catch-up always supersedes
the attempt. This records an immediate due failure without allowing delayed
contention to replace a completed publication.

Setup drift inspection renders the expected template with the current resolved
interval before comparing it with the canonical output. A missing profile
therefore repairs a previously auto-refreshing page back to interval `0`, and a
changed valid profile repairs the embedded value without changing snapshot v3.
An invalid present profile is treated as Viewer repair-required during
`setup --read-only`; preview remains successful and no-write with
`viewer_publish` planned. Actual setup cannot produce valid expected bytes, so
it preserves the last-good file and returns the existing `setup_incomplete`
result. Routine publication handles the same error through existing
`viewer_refresh_failed` semantics after the primary mutation has committed.

### Browser Application

The template is a quiet operational interface, not a marketing page. Its main
regions are:

- compact header with `Task Viewer`, project display name, project ID, and
  generated timestamp
- stable status summary for every schema-supported status: six in snapshot v1
  and seven, including `paused`, in snapshot v2 and v3
- filter toolbar with text search, status, kind, lane, priority, tag, terminal
  task visibility, and reset command
- dense task table on desktop and a stable stacked row layout on narrow screens
- unframed detail region or modal for the selected task and its recent events
- explicit empty-result state

The browser defaults to active tasks and keeps done/cancelled tasks available
through the terminal-task control. Filtering and sorting are client-side and
ephemeral except for TG-M15.6's exact one-shot automatic-reload handoff below.
Do not use cookies, Web Storage, IndexedDB, Cache API, service workers, or URL
query/fragment state. TG-M15.5 itself does not preserve filters, selection,
focus, or scroll across reload.

Use neutral surfaces plus distinct status colors; do not rely on color alone.
Keep card radii at 8 px or less, avoid nested cards, retain visible keyboard
focus, associate labels with controls, and ensure long IDs, titles, commit
hashes, and descriptions wrap without overlap. Use native controls and
semantic table/detail markup where practical.

### Browser Reload Scheduler

The template embeds the validated decimal interval as data, never as executable
task content. Interval `0`, a non-`file:` protocol, or a snapshot decode/render
failure leaves the scheduler disabled. Snapshot bytes are decoded with fatal
UTF-8 semantics. Scheduling begins only after the initial render completes.

One small reconciliation function owns all timer behavior:

1. clear and null any existing timeout;
2. return when disabled, a reload was already requested, protocol is not
   `file:`, or visibility is not `visible`;
3. compute elapsed time from a page-load monotonic epoch using
   `performance.now()`;
4. when elapsed is at least the configured interval, set a one-way
   `reloadRequested` flag and request one same-document reload;
5. otherwise schedule one timeout for only the remaining duration.

The timeout callback nulls its own handle and calls the same reconciliation
function, which rechecks visibility and elapsed time. The
`visibilitychange` handler calls that function as well; entering a hidden
state therefore leaves no owned timeout. No `setInterval`, polling loop,
wall-clock `Date.now()` interval calculation, retry, fetch, XHR, Web Storage,
service worker, database access, or message channel is used. TG-M15.6 uses
`Date.now()` only to timestamp and check the five-minute one-shot envelope.
Browser throttling may delay the callback; the monotonic remainder calculation
prevents an earlier request. The reload loads only the current atomically
published HTML and is never described as a live database update.

### One-Shot Reload State Algorithm

Keep this logic inside the fixed Viewer template; do not add a Python service,
shared state module, config field, snapshot field, or reusable browser-state
framework. The state object uses these exact ordered keys:

```text
owner, schema_version, captured_at_ms, status, kind, lane, priority, tag,
terminal, selected_task_id, scroll_x, scroll_y, focus_id
```

`owner` is `taskgov-viewer-auto-reload` and `schema_version` is integer `1`.
The remaining field types, enumerations, byte/character/numeric bounds, and
the 4,096-byte deterministic JSON cap are the exact contract in
`docs/specification.md`. Lane and tag store their visible text values rather
than the template's generated numeric option values. The fixed focus map has
only the eight filter/reset element IDs listed there; empty string means no
focus. If there is no selected task, capture returns no envelope and the
existing reload still proceeds.

Use small template-local helpers with these boundaries:

1. `isViewerOwnedState` recognizes only a non-array object with the exact
   owner field. This deliberately distinguishes ownership from full validity.
2. `encodedStateSize` serializes with `JSON.stringify` and measures UTF-8 bytes
   with `TextEncoder`; any exception or size above 4,096 rejects.
3. `captureReloadState` reads only the six allowed filter values, current
   selected ID, finite nonnegative `window.scrollX/Y`, and the fixed active
   element ID. It supplies `Date.now()` only for the bounded expiry check.
4. `saveReloadState` runs only after the scheduler has decided reload is due
   and immediately before reload. It returns without writing when current
   state is non-null and non-owned, validates the candidate and byte cap, then
   calls `history.replaceState(candidate, "")` with no third argument. All
   failures are caught and never prevent reload.
5. `consumeReloadState` first checks `location.protocol === "file:"`, then
   reads current state once before snapshot decoding. Every owned state,
   including malformed, stale, non-reload, and fatal-snapshot cases, attempts
   `history.replaceState(null, "")` immediately. Only successful clearing can
   retain a local candidate for later validation. Read or clear exceptions are
   caught, retain no candidate, and continue to normal snapshot/default UI;
   non-owned state is untouched, and a non-`file:` page performs no M15.6
   History read or write.
6. After snapshot decoding and filter-option initialization, validation uses
   only the first current navigation entry from
   `performance.getEntriesByType("navigation")` and requires type `reload`,
   exact keys, current owner/version, age 0-300,000 ms, strict primitive and
   field bounds, lane/tag membership, and selection visibility under the
   candidate filters.
7. Apply a valid plan by assigning the six filters, assigning selection, and
   calling the existing `renderTasks` once. Focus the fixed target with no text
   selection, then restore document scroll last. A rejected or failed plan
   leaves or returns to the existing default filter, first-visible selection,
   focus, and scroll behavior. Retain only the fixed element just focused by
   this operation; if the following scroll throws, best-effort blur that
   element before resetting filters/selection, rerendering defaults, and
   attempting `(0, 0)` scroll. A blur exception is contained inside fallback.

A History-state getter exception sets no candidate and disables envelope
capability for that page, but it does not return before an enabled `file:` page
attempts the property-presence, set, and readback sequence for manual scroll
restoration. This keeps the default `(0, 0)` fallback deterministic without
retrying the unreadable state or enabling save.

On a `file:` page that is auto-refresh enabled or begins with an owned state,
first require `"scrollRestoration" in window.history`, then set
`history.scrollRestoration = "manual"` before snapshot/UI restoration and
verify that reading the property returns `manual`. Also set search, all select
values, and terminal visibility to their explicit defaults during filter
initialization. If manual scroll restoration cannot be established, set one
page-local M15.6 capability flag false: do not save or restore an envelope, but
still clear an already owned state and leave the M15.5 scheduler unchanged.
After the one task render, a default/rejected path explicitly calls
`window.scrollTo(0, 0)`; a valid path focuses its fixed target and then calls
`window.scrollTo(savedX, savedY)`. The scroll-mode setter updates the current
entry metadata but adds no entry and changes neither URL nor classic state.

The scheduler due branch remains one-way: set `reloadRequested`, attempt the
bounded save, then call the existing same-document reload once. Save failure
does not retry. Restore and clear do not create or alter a timer. A second
reload has defaults unless that loaded page independently reaches its own
automatic-reload deadline and saves a new envelope. A manual reload never
captures state, although a still-outstanding envelope from interrupted
automatic navigation may be consumed by a qualifying reload within five
minutes.

History state is browser-managed and may be restored with a browser session.
The implementation must not claim memory-only lifetime. It makes the state
one-shot through ownership, five-minute age, exact validation, and
clear-before-restore. Never call `pushState`; never pass a URL argument to
`replaceState`; never use cookies, local/session storage, IndexedDB, Cache API,
service workers, query/fragment state, network APIs, arbitrary selectors,
cross-tab messaging, or task/snapshot/database persistence. Static and browser
tests assert that `history.length` and `location.href` do not change.
Every helper catches browser/state failures without logging the state,
serialized bytes, exception, validation reason, URL, or path to console, DOM,
snapshot, or taskgov output.

### Read-Only Snapshot Transaction

The dedicated read helper follows the shared M13 operational journal
preflight, opens the SQLite URI with `mode=ro` and no immutable flag, enables
`PRAGMA query_only=ON`, and starts an explicit read transaction. Revalidate
the Viewer-compatible schema and project identity inside that transaction
before querying generation, tasks, and events. This gives each render one
SQLite-consistent point-in-time view when another session commits concurrently.

Preserve the existing preflight rejection for active WAL sidecars. Also inspect
the stable SQLite file-header journal bytes without opening a mutable SQLite
connection and reject persistent WAL mode before the snapshot connection. Tests
must prove a normal snapshot read creates no WAL/SHM/journal files, active or
cleanly closed WAL state fails without stale output or new sidecars, and a
concurrent writer either yields a consistent snapshot or a structured tool
error. The generated timestamp describes render time, not an exclusive
database revision.

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

The browser implementation contains no network API, external URL, telemetry,
automatic browser launch, or database-write code. Its only History API writes
are TG-M15.6's bounded replacement and clearing of the current entry, without
a URL argument, plus its page-local `scrollRestoration = "manual"` setting.
Browser refresh reloads the same snapshot and is not presented as a database
refresh.

## Approved TG-M16 Reduced Loop Discipline Trial Design

TG-M16 adds no subsystem. M16.1 changes one centralized value selection inside
the existing Effort result assembly. M16.2 changes no product runtime or
persisted state; it updates agent guidance, behavioral fixtures, and directly
coupled summaries, self-checks, and manifest hashes. M16.4 synchronizes the
package and validates fresh-session behavior. M16.3's proposed setup bootstrap
and project-instruction adoption are deleted from the design.

### Deterministic Effort Action Selection

M16.1 derives one local `suggested_action` after profile validity and the
ordered `exceeded` list are known:

```text
valid profile AND enabled AND len(exceeded) > 0
    => reconcile_scope
otherwise
    => continue
```

The same value is placed in the result data and, when the existing threshold
warning is emitted, in that warning. The warning code, key, and message remain
the current fixed values. Attribution and `unknown_reasons` remain independent
evidence fields: an unknown-only result continues, while a nonempty exceeded
list plus unknown attribution reconciles. One result has at most one threshold
warning and therefore at most one reconciliation episode.

This selection stays in the existing `effort.py` boundary; it does not create
a routing framework, new profile field, metric, CLI branch, database write, or
stored acknowledgement. It is read-only and cannot call Task, Contract,
handoff, review, or completion repositories. The existing mechanically gated
single Effort call and nine/ten-call budgets are unchanged.

### Session-Local Guidance Model

M16.2 represents neither retries nor reconciliation in SQLite. The active
Skill adds one short trigger that loads
`references/reconciliation.md` only after `reconcile_scope` or repeated test
or review failure. The Skill Operating Rules and root `AGENTS.md` hold only the
durable first-failure, test-integrity, and continue-first invariants; the
one-level reference holds the bounded procedure and examples. Normal successful
work loads no extra reference and performs no extra taskgov call.

The procedure keeps a session-local comparison of failed attempts.
After two materially equivalent failed repairs, a third equivalent execution
is prohibited when no new evidence exists. Wrapper, command spelling,
working-directory, Task-label, or execution-unit-label changes do not create
new evidence by themselves. A safe diagnostic is allowed and is new evidence
only if its result can materially alter the causal hypothesis, authorized
repair, or expected result. No session-local count is serialized,
checkpointed, or reconstructed after compaction; a fresh session resets it and
relies only on durable Task, event, review, and handoff state.

Test Repair never edits tests solely to make verification green. Governing
authority may show that a test is incorrect, but only later explicit authority
may revise the Task Contract or acceptance. A failure or Effort signal is
evidence, not authority.

Scope Reconciliation calls the existing three-way classifier rather than
adding a workflow state:

- current Task only when accepted scope and current authority cover the repair,
  including acceptance-required work and regressions introduced by that Task;
- existing local handoff for other discoveries;
- existing blocker only after safe authorized work for the affected Task or
  lane is exhausted.

`paused` remains an explicit temporary interruption. Unrelated ready lanes
continue. Any decisions that remain after safe independent work are returned
in one bounded batch.

Review remediation begins with the existing blocking receipt/finding state. A
meaningful fix advances to a fresh target generation and a fresh
current-generation review result. A result that remains blocking counts as one
unsuccessful cycle; completion still requires fresh qualifying PASS receipts.
Two unsuccessful materially equivalent remediation cycles without new evidence
prohibit a third equivalent cycle and use the same bounded decision or blocker
path. There is no second review state machine or attempt record.

### Removed Bootstrap And Behavioral Acceptance

Setup gains no M16 stage, Task seed, policy version, JSON field, or target
instruction write. No component inspects a consuming project's instruction
chain for adoption and no target `AGENTS.md` is created or edited.

M16.4 validates the package from fresh sessions after M16.1 and M16.2. Pressure
fixtures cover exceeded and non-exceeded Effort routes, equivalent failure,
new diagnostic evidence, test integrity, review findings and fresh targets,
scope/handoff/blocker selection, unrelated-lane continuation, batched
decisions, and rediscovery with reset session-local retry counts. These are
repository tests and sanitized forward evidence, not persisted runtime policy.
The package remains physical and project-scoped with explicit `--repo`; no
test installs into or edits a real consuming project.

## Implemented TG-M17 Stable Identity And Relocation Design

TG-M17 is one staged storage-boundary change, not a general project registry or
workflow engine. M17.0 fixed the design; M17.1 through M17.4 were non-public
staging revisions that could temporarily combine package version 0.8.0 with
schema v14 or a legacy-layout transition database. M17.5 synchronized the
accepted full fixed resolver as release v0.9.0. Viewer snapshot stays v3 and
its source-schema validator accepts 5-14.

### Schema Version 14: Identity And Binding

Migration 14 is named `project_identity_bindings`. It runs only after complete
schema v13 history. Every supported v1-v13 database reaches it through the
existing sequential migration driver. The current `project_meta` table remains
the single current-binding row and gains these columns:

```sql
identity_scheme TEXT NOT NULL DEFAULT 'legacy_path_v1'
  CHECK (identity_scheme IN ('legacy_path_v1', 'uuid_v1')),
binding_generation INTEGER NOT NULL DEFAULT 1
  CHECK (binding_generation >= 1),
binding_reason TEXT NOT NULL DEFAULT 'legacy_migration'
  CHECK (
    binding_reason IN (
      'legacy_migration',
      'fresh_setup',
      'confirmed_relocation'
    )
  ),
binding_updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z',
legacy_cleanup_pending INTEGER NOT NULL DEFAULT 0
  CHECK (legacy_cleanup_pending IN (0, 1)),
legacy_cleanup_inventory TEXT
  CHECK (
    legacy_cleanup_inventory IS NULL
    OR (
      length(legacy_cleanup_inventory) BETWEEN 1 AND 16384
    )
  ),
legacy_cleanup_fingerprint TEXT
  CHECK (
    legacy_cleanup_fingerprint IS NULL
    OR (
      length(legacy_cleanup_fingerprint) = 64
      AND legacy_cleanup_fingerprint NOT GLOB '*[^0-9a-f]*'
    )
)
```

Migration uses seven ordered `ALTER TABLE ... ADD COLUMN` statements. The
inventory and fingerprint columns have only their own nullable bounds above.
After all seven columns exist, fixed insert/update triggers enforce the
cross-column triple: pending 0 requires null inventory and fingerprint;
pending 1 requires a non-null inventory and a valid non-null fingerprint. This
avoids pretending that a later table-level `CHECK` can be added to the existing
table.

The existing `canonical_path_hash` is the current binding hash, not identity.
Repository validation requires exactly 64 lowercase hexadecimal characters.
The existing `display_name` is non-authoritative and must pass the fixed
1-200-code-point display sanitizer. Migration leaves existing
`project_meta.created_at` and `updated_at` unchanged, deterministically
normalizes only that old display value by replacing prohibited control/line
characters, applying the `project` fallback, and truncating to 200 code
points, sets
`binding_updated_at` to the migration's one canonical UTC timestamp, and
creates this append-only table:

```sql
CREATE TABLE project_path_binding_history (
  project_id TEXT NOT NULL,
  binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
  previous_path_hash TEXT,
  canonical_path_hash TEXT NOT NULL,
  display_name TEXT NOT NULL CHECK (
    length(display_name) BETWEEN 1 AND 200
  ),
  reason TEXT NOT NULL CHECK (
    reason IN (
      'legacy_migration',
      'fresh_setup',
      'confirmed_relocation'
    )
  ),
  confirmation_token_digest TEXT,
  bound_at TEXT NOT NULL,
  PRIMARY KEY (project_id, binding_generation),
  FOREIGN KEY (project_id) REFERENCES project_meta(project_id),
  CHECK (
    length(canonical_path_hash) = 64
    AND canonical_path_hash NOT GLOB '*[^0-9a-f]*'
  ),
  CHECK (
    previous_path_hash IS NULL
    OR (
      length(previous_path_hash) = 64
      AND previous_path_hash NOT GLOB '*[^0-9a-f]*'
    )
  ),
  CHECK (
    confirmation_token_digest IS NULL
    OR (
      length(confirmation_token_digest) = 64
      AND confirmation_token_digest NOT GLOB '*[^0-9a-f]*'
    )
  ),
  CHECK (
    (binding_generation = 1 AND previous_path_hash IS NULL)
    OR
    (binding_generation > 1 AND previous_path_hash IS NOT NULL)
  ),
  CHECK (
    (
      reason = 'confirmed_relocation'
      AND binding_generation > 1
      AND confirmation_token_digest IS NOT NULL
    )
    OR
    (
      reason != 'confirmed_relocation'
      AND confirmation_token_digest IS NULL
    )
  )
);
```

The migration inserts generation 1 with reason `legacy_migration`, no previous
hash, no token digest, and the same migration timestamp for each existing
project row. A valid production database contains exactly one project row.
The migration tolerates the zero-row schema-construction interval but the
initializer must create current metadata and generation 1 in the same
transaction before publication.

SQLite triggers reject update of `project_meta.project_id`,
`identity_scheme`, or `created_at`, deletion of `project_meta`, and every
update or deletion of history. Connection validation requires:

- one project row and one history lineage with consecutive generations
  `1..N`, generation 1 having no previous hash, and each later
  `previous_path_hash` equal to the preceding generation's canonical hash;
- generation 1 reason is `legacy_migration` exactly for `legacy_path_v1` and
  `fresh_setup` exactly for `uuid_v1`; every later reason is
  `confirmed_relocation`;
- current generation equal to the maximum history generation;
- current hash, display, reason, and binding time equal to that history row;
- valid scheme-specific project ID;
- valid canonical timestamps, hashes, display, and reason.
- cleanup state is either `(0, NULL, NULL)` or
  `(1, canonical-inventory, 64-lower-hex)`, where the inventory is 1-32 exact
  unique recognized file entries, canonical JSON of at most 16,384 ASCII
  bytes, and the fingerprint is its SHA-256; it never changes identity or
  binding.

The trigger names are
`trg_project_meta_identity_immutable`,
`trg_project_meta_no_delete`,
`trg_project_meta_cleanup_insert_valid`,
`trg_project_meta_cleanup_update_valid`,
`trg_project_path_binding_history_no_update`, and
`trg_project_path_binding_history_no_delete`. Their fixed abort messages do
not contain row values.

`legacy_path_v1` accepts only the existing sanitized-basename-plus-12-hex form
already present in supported databases. `uuid_v1` accepts
`tg_project_[0-9a-f]{32}` only when the embedded hex has UUID version nibble
`4` and variant nibble `8`, `9`, `a`, or `b`; generation uses
`uuid.uuid4().hex`. No path-derived input is part of a UUID ID.

The binding repository takes `project_id`, `identity_scheme`,
`expected_generation`, `expected_old_hash`, `new_hash`, bounded new display,
reason `confirmed_relocation`, token digest, and timestamp. It validates before
`BEGIN IMMEDIATE`, rereads and compares the identity, generation, and old hash
under that transaction, rejects a same-hash change or generation
`9,223,372,036,854,775,807`, inserts generation `N+1`, and updates current
hash/generation/display/reason/binding time and `updated_at`. Exactly one
insert and one current-row update must occur. The same transaction increments
the one `viewer_maintenance_state.source_generation` row and requires it below
SQLite integer maximum. Missing Viewer state or binding/Viewer overflow maps to
the sanitized unreadable-state failure and rolls back identity, history,
generation, and token consumption together; otherwise a crash before
publication remains durably Viewer-due. Any other count mismatch rolls back as
stale. No repository method updates a durable business-row project ID.

M17.1 exposes the UUID initializer only behind an explicitly injected fixed
`DatabaseTarget` and injectable ID/clock factories. Production setup and the
old resolver do not call it. If the non-public M17.1 staging revision must
initialize through its old production path, it creates `legacy_path_v1`
generation 1 with `legacy_migration`; only M17.2 activates production
`uuid_v1`. This makes the repository's own schema-v14 legacy-layout database a
supported M17.2 transition input rather than an accidental gap.

### Single Fixed Resolver

The physical package owns these derived paths:

```text
state root       <physical-skill>/state
state lock       <state-root>/taskgov-state.lock
fixed root       <state-root>/current
database         <fixed-root>/taskgov.sqlite
managed backups  <fixed-root>/backups
Viewer           <fixed-root>/viewer/task-viewer.html
```

`project_scope.py` continues to own physical install, explicit/omitted repo,
self-host, target-directory, containment, runtime, package, and effective
ignore checks. It no longer creates identity. A single storage resolver owns
fixed paths, legacy discovery, and database identity/binding reads. Its
internal result contains:

- canonical skill/state/fixed/database/backup/Viewer paths;
- current canonical governed root in memory, its 64-hex hash, and sanitized
  display;
- stored project ID, scheme, binding generation/hash, and source schema when
  available;
- layout state `missing`, `fixed_current_v1`, or `legacy_projects_v1`;
- binding state `unbound`, `matching`, or `relocation_required`;
- a setup-only validated legacy/recovery observation.

Paths in that object never cross the public formatter. Binding hashes appear
only inside M17.3's opaque confirmation token and never as independent JSON,
text, warning, or error fields. Missing state has no project ID. All production
CLI handlers, doctor, setup, backup,
Viewer, maintenance, and recovery accept this result or the
`DatabaseTarget` constructed by it. A structural test rejects imports or calls
that use the former path-derived identity/default-path helpers outside the
resolver and explicitly injected tests.

For a fixed database, identity, schema, current binding, and history are read
inside the same lock-respecting `mode=ro`, `query_only=ON` transaction used by
the consumer. A write request carries identity, hash, and generation as its
basis and compares all three again in its existing short write transaction.
There is no separate identity lookup call in the normal Task loop.

The one resolver has two deterministic validation projections. Active
fixed-primary consumers validate only the authoritative primary and derive the
canonical artifact targets; they do not open every retained backup or validate
Viewer content on each Task/handoff/review command. Setup selects the deep
projection, and missing-primary recovery, legacy discovery, and private-stage
validation always use it. That projection validates the full bounded
backup/Viewer/lock inventory before setup repairs or publishes anything. The
selection is fixed by the caller and never exposed as an LLM or CLI choice.

### Bounded Legacy Resolver

Legacy inspection runs only when the fixed primary and higher-precedence fixed
recovery source are absent. It uses a single non-recursive scan of
`state/projects` and consumes at most 64 direct entries. The scan rejects a
65th entry, an unknown direct entry, any link/junction/reparse component,
containment escape, more than one candidate, or an invalid candidate without
creating a directory or opening a writable SQLite connection. Every DB-backed
leaf uses this same resolver observation when fixed state is absent. Normal
leaves do not build a migration plan: final M17 maps same binding to
`migration_required`, primary-backed moved binding to
`project_relocation_required`, no source to `db_not_initialized`, and the
exact invalid conditions to the specification's fixed errors.

The candidate validator:

1. validates the physical candidate directory and the exact primary location;
2. when primary exists, applies operational journal preflight and opens one
   query-only transaction; when it is absent, selects the newest valid managed
   backup by the existing `(published_at, generation_id)` order and opens only
   that artifact;
3. validates contiguous source history, every required object introduced by
   the declared schema version, absence of any known later-migration marker,
   exactly one project row, scheme and identity, current binding/history when
   v14, `quick_check`, foreign keys, and directory-name equality;
4. validates every recognized managed backup against the same identity/scheme
   and requires each older binding lineage, including implicit pre-v14
   generation 1, to be an exact prefix of the selected newest lineage; it
   accepts only a state the existing schema-specific reconciler accepts, with
   at most 21 canonical same-project artifacts and, when present, generation
   rows and at most 21 distinct union identities. Schema v11+ with a primary
   permits at most one artifact without a row or one row without a file but not
   both and requires coherent pointer/applied-retention state. With no primary,
   the sole file-only artifact must be the selected mechanically-newest backup,
   whose pre-row snapshot may have zero through 20 older rows without files
   already removed by retention pruning; the recovery normalizer must accept
   them and the union remains at most 21 identities. v10 uses its pointer rules
   and v1-v9 use newest-artifact retention without misclassifying their absent
   v11 table as file-only. The in-flight generation participates in
   newest-candidate selection, including a policy reduction that leaves 21
   row-backed files before prune;
5. validates canonical database/backup/Viewer/lock paths and classifies one
   regular, non-link temporary only when exactly one basename matches each
   exact
   `.taskgov-restore-[a-z0-9_]{8}.tmp`,
   `.taskgov-backup-[a-z0-9_]{8}.tmp`, and
   `.task-viewer-[a-z0-9_]{8}.tmp` pattern, each no larger than source DB size
   plus 16,777,216 bytes; two or more matches in one class are all unrelated,
   preserved, and never selected by enumeration order;
6. returns the stored binding comparison and recognized inventory without
   updating metadata or deleting a temporary.

Other entries inside the sole candidate are opaque user/local material. They
are neither traversed nor candidate input and remain in the legacy directory.
An unsafe object at a canonical or exact recognized owned path fails; an
unrelated file or directory at another name is preserved.

Pre-v14 is treated as implicit `legacy_path_v1`, generation 1 for planning.
Source schemas 1-13 and an M17.1-produced v14 legacy-layout database are
accepted. A v14 `uuid_v1` database in the legacy layout is invalid because no
production unit creates that combination.

An existing fixed primary always wins and is never replaced when invalid.
Fixed missing-primary recovery selects the mechanically newest valid
generation and requires its binding head to match the current root. Older
retained generations may use an exact prefix lineage; a different identity,
scheme, or divergent lineage fails. If the selected newest binding differs
from the current root, recovery is intentionally unsupported and unreadable;
the relocation token has no backup source kind.

A primary-backed same-binding legacy candidate is eligible only for
setup-owned fixed publication. A missing-primary same-binding legacy candidate
uses the selected backup as the private-stage database source and has public
source prefix `[database_restore, legacy_state_publish]`; it never restores the
old primary. A primary-backed moved candidate is returned as read-only
relocation-required state until M17.3. A moved backup-only legacy candidate is
unreadable rather than a third confirmed source.

M17.2 owns that internal state plus the one staged-publication and bounded
cleanup/resume primitive for same-binding sources. In its non-public staging
revision, same-binding layout returns the existing `migration_required` result
until setup publishes it, and moved binding maps to the existing no-write
`project_mismatch` result. M17.3 alone replaces the latter with the final
`project_relocation_required` preview/error, adds the relocation JSON, and
reuses the same publication and cleanup primitive for confirmed moved sources.

### Relocation Token Codec And Result Model

`relocation.py` owns one codec and binding-context validator; it is not a
credential service. The codec emits:

```text
tgr1.<payload>.<checksum>
```

`payload` is canonical compact sorted-key JSON encoded as unpadded base64url.
The exact keys and types are:

```text
v: integer 1
project_id: validated scheme-specific string
identity_scheme: legacy_path_v1 | uuid_v1
binding_generation: integer 1..9223372036854775806
old_path_hash: 64 lowercase hex
new_path_hash: 64 lowercase hex distinct from old
source_layout: fixed_current_v1 | legacy_projects_v1
source_schema_version: integer 1..14
issued_at: canonical UTC second
expires_at: canonical UTC second exactly issued_at + 900 seconds
```

The encoder uses UTF-8, `sort_keys=True`, `separators=(",", ":")`, and
`ensure_ascii=True`. The checksum is lowercase SHA-256 of the ASCII
`tgr1.<payload>`. The decoder caps the whole token at 2,048 ASCII bytes before
decoding, rejects padding, non-ASCII, duplicate/missing/extra keys,
noncanonical re-encoding, invalid types/ranges/times, and compares the
checksum with `hmac.compare_digest`. No signing key, stored nonce, or secret is
introduced.

The preview clock fixes both timestamps. Acceptance requires
`issued_at <= now < expires_at` with no hidden grace interval. An intervening
Task mutation does not stale the token because it confirms binding, not a data
snapshot. Actual setup always copies the latest coherent database snapshot
after revalidating identity, binding, schema, filesystem inventory, and
current-root hash.

The codec conveys no authority to the Skill. M17.5 guidance treats preview and
confirmation as two user-mediated steps: present the bounded relocation plan,
wait for explicit current approval, then submit the exact unexpired token.
Agents never auto-confirm a preview. Expired or stale context requires a fresh
preview and fresh approval; direct user CLI submission remains explicit intent.

On success, the binding history stores SHA-256 of the entire ASCII token.
After package/project/state preflight, handling order is: structural and
checksum validation, current-history digest lookup, expiry, source/binding/root
staleness, then currently-not-required context. Digest lookup therefore makes
every structurally valid successful-token replay `relocation_token_used` even
after the original expiry. A well-formed token that does not describe the
current source/binding/root is stale. A token for a currently matching but
never-applied context is not required. Rejected values are never copied into
errors or logs.

M17.3 extends `SetupServiceResult.data` with one fixed `relocation` object.
The formatter always emits its six specification keys and never emits a path
or hash. `status="relocation_preview"` is used only for a successful
read-only mismatch preview. The token and expiry are non-null only there.
Parser validation rejects `--read-only --confirm-relocation` before scope
inspection. Actual mismatch without a token is the same no-write
`project_relocation_required` result.

### Staged Publication, Rebind, And Cleanup

Write-mode setup obtains locks in this order:

1. fail-fast package state-transition lock at `state/taskgov-state.lock`;
2. the source/fixed managed-backup artifact lock when a backup inventory or
   migration backup participates;
3. short private or current SQLite transactions.

It never holds a SQLite writer while copying a source database, rendering or
publishing a Viewer, invoking Git, deleting a legacy artifact, or waiting for
another lock. Read-only preview creates none of these locks or artifacts.
Both setup modes inspect an existing transition-lock artifact through the
shared zero/one-byte lock validator before planning or writing; malformed
state returns the same sanitized `setup_incomplete` result without changing
the file.
Normal business commands do not take the state-transition lock; fixed rebind
therefore uses SQLite compare-and-swap, while legacy state is not an active
normal-command target after M17.2.

For legacy publication, the source backup lock remains held through staged
snapshot/artifact copy and atomic fixed publication. Setup then releases that
artifact lock while retaining the state-transition lock before it moves the
now-unlocked legacy lock file during cleanup. Current-version normal commands
never use the legacy root, so no SQLite or artifact lock is held while cleanup
moves files.

Same-binding and confirmed moved-legacy publication share one primitive:

1. Under the transition lock, repeat project/package/ignore/containment,
   destination-absence, candidate-count, journal, schema, integrity,
   identity/binding, artifact, and optional token checks, then form and validate
   the exact source cleanup inventory and fingerprint.
2. Create one random, contained
   `state/.current-stage-<32-lowercase-hex>.owner` marker by exclusive,
   durably flushed creation, then create its paired
   `state/.current-stage-<same-32-lowercase-hex>` directory no-replace. The
   canonical marker has exactly `v`, matching `stage_id`, validated
   `project_id`, and source `inventory_fingerprint`, is at most 2,048 ASCII
   bytes, uses sorted compact `ensure_ascii` JSON with duplicate-key rejection,
   and contains no path. Write-mode setup may remove one prior residue
   only after marker, suffix, containment, physical-kind, allow-list, count,
   and size validation. The stage allows only `backups` and `viewer`
   directories and at most 32 regular files: database/optional rollback
   journal, up to 21 exact managed backups, both canonical artifact locks,
   Viewer HTML, and at most one exact bounded temporary per existing class.
   Files are at most source DB size plus 16,777,216 bytes. Cleanup names each
   file and removes only proven-empty directories, never an unvalidated
   recursive tree. An owner without a stage is removable alone. Read-only
   reports the ordinary preview for one valid recoverable residue but changes
   nothing. A stage without its owner, invalid/mismatched owner, second pair,
   unknown/oversized entry, or unsafe component is the specification's exact
   pre-plan `setup_incomplete` row and is preserved.
3. Copy the primary or selected same-binding recovery backup to the private
   stage through `sqlite3.Connection.backup`; copy only validated retained
   managed backups, the one allowed valid in-flight generation, and last-good
   Viewer artifacts. In the private stage only, run the existing
   version-specific reconciler to bounded convergence before any
   migration-backup publication. Missing-primary v11+ recovery normalization
   first removes zero through 20 stale snapshot rows and imports the selected
   file-only artifact in one bounded pass; present-primary v11+ imports one
   file-only generation, removes one missing-file row, or prunes the coherent
   post-row set in at most two passes. v10/pre-v10 use the current
   pointer/prune normalization. Merely
   classify backup and Viewer lock/temp state in the source. No source entry is
   deleted, renamed, truncated, or reconciled before fixed publication.
4. Persist the already formed version-1 inventory from recognized source files
   only. Each entry
   is `{"kind":"file","name":<relative POSIX name>,"sha256":<64hex>,
   "size":<nonnegative bytes>}`. Sort entries by UTF-8 name bytes, place them in
   `{"entries":[...],"v":1}`, serialize UTF-8 with `ensure_ascii=True`,
   sorted object keys, separators `(",", ":")`, require 1-32 unique exact
   recognized names and at most 16,384 bytes, and hash those bytes with
   SHA-256. Unrelated entries are excluded. In the private database, apply
   required sequential migrations, maintenance configuration, and, for
   confirmed movement, the one binding compare-and-swap. Set cleanup pending
   plus the canonical inventory text and fingerprint. Generate and validate
   the current Viewer from that staged database. All public planned stages
   remain uncompleted because the stage is not canonical.
5. Validate the complete staged database, history, backup metadata/files,
   Viewer, absence of unsafe canonical paths/sidecars, and expected binding.
6. Atomically publish the entire staged directory to absent `state/current`
   with a no-replace directory rename, leaving the sibling owner outside
   `current`, then remove that owner. An entry that appears concurrently
   aborts publication and is never replaced. A crash after rename leaves fixed
   state authoritative and only the validated orphan owner removable. After
   this point the ordered source, migration/configuration, binding, and Viewer
   stages are durably complete together.
7. Retire and delete only the recorded recognized legacy files as specified
   below; leave every unrelated entry at its original legacy path.

The primary supported runtime is Windows, where the directory rename fails if
the destination exists. Any port that cannot provide equivalent no-replace
semantics must fail safely rather than substitute a replacing rename.

A crash or failure before step 6 leaves the source authoritative and only an
owned private residue eligible for bounded setup cleanup. A failure after step
6 leaves fixed state authoritative. If Viewer publication was part of the
stage it is already durable; recognized legacy cleanup may remain. General
legacy discovery stays disabled while fixed primary exists. Setup alone reads
the pending bit and derives:

```text
old root       state/projects/<project-id>
retirement     state/.legacy-cleanup-<sha256(project-id UTF-8)>
```

The retirement component uses the full 64 lowercase hex digest and therefore
does not inherit legacy-ID length. Repository validation first reparses the
persisted inventory with duplicate-key rejection, validates its exact
recognized grammar and canonical bytes, and compares its SHA-256 with the
fingerprint. That persisted inventory, never directory enumeration, is the
allow-list. For each recorded relative file, both old/retirement locations
fails; an old-only or retirement-only file must match its recorded kind, size,
and SHA-256; neither is already absent. Cleanup creates the retirement
directory no-replace when needed and first atomically moves every still-present
old file to its mirrored relative location without replacement. Only after no
recorded old file remains does it delete verified retirement files one by one.
A retry reconstructs the exact remaining subset from the inventory and the two
locations. It deletes only recorded files and proven-empty owned
subdirectories; any unrecorded retirement entry fails, and unrelated old
entries remain untouched.

When no recorded file and no retirement directory remains, including a crash
after final deletion but before DB update, one short current transaction
clears pending/inventory/fingerprint. New or changed content at a recorded
path, retirement collision, or an unrecorded retirement entry returns
`setup_incomplete` only from setup; valid fixed-state normal leaves remain
usable. Invalid persisted cleanup JSON or digest is database integrity failure.
Token replay still returns used; token-free setup resumes.

An existing fixed mismatch does no copy or layout publication. Under the state
lock, setup revalidates the token and fixed database, performs the short
binding transaction, which atomically advances Viewer source generation,
closes it, then publishes the Viewer through the existing generation/last-good
path. Binding or Viewer-generation overflow rolls the transaction back and
does not consume the token. A crash or Viewer failure after commit leaves the
durable generation due, preserves the prior Viewer, and is repaired by a later
token-free setup; a reported failure returns `setup_incomplete`. When that
fixed database already carries a valid legacy-cleanup pending record, preview
and valid confirmation append cleanup after Viewer publication. A mismatch
without confirmation remains wholly no-write and does not clean legacy state;
the successful confirmation or its later token-free repair performs that due
cleanup.

### Consumer Integration And Tests

M17.4 does not add another resolver or lock hierarchy and does not move
production entry-path activation out of M17.2. It structurally audits all 20
public leaves, corrects only discovered bypass/integration defects, and
finishes backup, Viewer, doctor, maintenance, recovery, setup, and
effective-ignore consumer integration through the shared target. Doctor maps a valid
mismatch to successful `project_state.code="relocation_required"`, the fixed
warning, `setup_eligible=true` when otherwise eligible, and unavailable
project-backed detail without issuing a token. Same-binding legacy layout is
`migration_required`; unsafe/ambiguous state is unreadable. Doctor remains one
read transaction and always suggests `continue`.

A structurally complete supported older schema is `migration_required`.
Missing migration history, an object or per-project row required by the
declared version, or a known marker introduced after the declared version is
invalid state and therefore `project_state_unreadable`; setup must not
advertise or begin migration for that state.

Routine writes compare the target's project ID, binding hash, and generation
under their short transaction. Post-commit Viewer and backup work receive that
same basis and re-resolve before publication; stale basis yields the existing
bounded failure/deferred behavior and never writes an old-binding artifact.
Missing fixed primary may use coherent fixed-layout recovery; missing legacy
primary may use coherent same-binding legacy recovery into the private fixed
stage. The newest valid generation is selected mechanically. Earlier retained
generations remain valid only as same-identity/scheme lineage prefixes and do
not override the newest head. Corrupt existing primary, foreign/divergent
identity, unsupported journal, and moved backup-only state never fall back.
Fixed rebind deliberately creates no immediate managed generation. Until
ordinary maintenance next publishes one with the new binding head, loss of the
fixed primary is unreadable even though older prefix generations remain
retained. This is a documented bounded recovery window, not an implicit backup
stage or relocation fallback.

Focused tests cover:

- v1-v13 to v14 migration, rollback/idempotency, preserved IDs/records, and
  immutable/current-history constraints;
- injected UUID initialization and absence of production UUID activation in
  M17.1;
- fixed resolver separation, fresh UUID setup, same-binding sources 1-13 and
  transition v14, same-binding legacy backup-only recovery, moved no-write
  discovery, primary-present/primary-missing retained-plus-one pre-row and
  post-row reconciliation and two-in-flight refusal, 0/1/multiple/65-entry
  and unsafe canonical inventories, and preservation of unrelated contained
  files;
- exact token codec/time/error/replay/stale/current-context behavior and no
  rejected-value disclosure;
- staged no-clobber publication, each injected failure boundary, source
  preservation, versioned inventory fingerprint, long legacy IDs,
  old/retirement/mixed/neither cleanup resume, token-free repair, fixed rebind
  Viewer-due marking, retained binding-lineage backups, and concurrent
  setup/write;
- all-leaf structural resolver use, doctor zero-write, backup restore/rotation,
  Viewer v3 source 5-14 and last-good behavior, M15.5/M15.6 preservation, and
  no raw path;
- unchanged 20 leaves, one setup option, compact envelopes, selection, Effort,
  privacy, nine/ten-call guidance, and target-project/Git no-mutation.

M17.5 reuses these focused suites instead of creating a second giant fixture.
Its forward matrix adds one full pre-v9 flow, one v13 flow, one transition-v14
self-host flow, fresh UUID, same-binding migration, moved preview/confirm,
fixed rebind, explicit user-approval Skill routing, replay, token-free rerun,
corrupt/unsafe refusal, package self-check, and two exact-final-target Tier 2
reviews.

## Approved TG-M18 Completion Cycle History Design

This design implements the approved completion-history contract without adding
a public workflow step. M18.1 through M18.3 are non-public staging revisions;
M18.4 is the sole version/package synchronization point.

### Version And Activation Design

The allocation is final:

- M18.1: package 0.9.0, schema v15 `completion_cycle_history`, Viewer snapshot
  v3 accepting sources 5-15;
- M18.2: package 0.9.0, marker-only schema v16
  `completion_cycle_capture_activation`, Viewer v3 accepting sources 5-16;
- M18.3: package 0.9.0, schema v16, Viewer snapshot v4 accepting sources 5-16;
- M18.4: release 0.10.0, schema v16, Viewer snapshot v4.

The marker migration separates repository availability from writer activation.
All schema-v15 task inserts use the column default `legacy_unknown`. Schema v16
is recorded only after the runtime contains capture in both done paths, reopen
linkage, and explicit `complete` assignment for new Tasks. A schema-v15 binary
therefore rejects the v16 database before it can perform a capture-less write.

### Schema V15

Migration 15 adds one table, two columns, four supporting indexes, and four
immutability triggers. The normative DDL shape is:

```sql
ALTER TABLE tasks
  ADD COLUMN completion_history_coverage TEXT NOT NULL
    DEFAULT 'legacy_unknown'
    CHECK (completion_history_coverage IN ('legacy_unknown', 'complete'));

CREATE UNIQUE INDEX idx_tasks_project_task_identity
  ON tasks(project_id, task_id);

CREATE UNIQUE INDEX idx_review_receipts_completion_cycle_reference
  ON review_receipts(
    project_id, task_id, target_kind, target_value,
    target_base_revision, target_generation, review_receipt_id
  );

CREATE TABLE task_completion_cycles (
  completion_cycle_id TEXT PRIMARY KEY
    CHECK (
      length(completion_cycle_id) = 36
      AND substr(completion_cycle_id, 1, 20) = 'tg_completion_cycle_'
      AND substr(completion_cycle_id, 21) NOT GLOB '*[^0-9a-f]*'
    ),
  project_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  saved_cycle_ordinal INTEGER NOT NULL
    CHECK (saved_cycle_ordinal >= 1),

  origin TEXT NOT NULL
    CHECK (origin IN ('native_done', 'legacy_current_done')),
  completeness TEXT NOT NULL
    CHECK (completeness IN ('complete', 'partial')),
  completed_at TEXT,
  recorded_at TEXT NOT NULL,

  contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
  review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
  verification_expectation TEXT NOT NULL
    CHECK (verification_expectation IN ('specified', 'unspecified')),
  verification_attestation INTEGER
    CHECK (
      verification_attestation IS NULL
      OR verification_attestation IN (0, 1)
    ),

  completion_evidence_kind TEXT NOT NULL
    CHECK (completion_evidence_kind IN (
      'none', 'git_commit', 'external_revision',
      'commit_not_required', 'legacy_unverified'
    )),
  completion_evidence_revision TEXT NOT NULL
    CHECK (length(completion_evidence_revision) <= 500),
  completion_evidence_reason TEXT NOT NULL
    CHECK (length(completion_evidence_reason) <= 1000),
  external_revision_approved INTEGER NOT NULL
    CHECK (external_revision_approved IN (0, 1)),
  completion_commit_required INTEGER NOT NULL
    CHECK (completion_commit_required IN (0, 1)),
  completion_commit_hash TEXT NOT NULL
    CHECK (length(completion_commit_hash) <= 500),

  review_target_kind TEXT NOT NULL
    CHECK (review_target_kind IN (
      '', 'git_commit', 'diff_fingerprint',
      'external_revision', 'git_snapshot'
    )),
  review_target_value TEXT NOT NULL
    CHECK (length(review_target_value) <= 500),
  review_target_base_revision TEXT NOT NULL
    CHECK (length(review_target_base_revision) <= 500),
  review_target_generation INTEGER NOT NULL
    CHECK (review_target_generation >= 0),

  gate_basis_version INTEGER NOT NULL
    CHECK (gate_basis_version IN (0, 1)),
  review_basis_kind TEXT NOT NULL
    CHECK (review_basis_kind IN (
      'unknown', 'independent_passes',
      'self_review_fallback', 'not_required'
    )),
  required_independent_passes INTEGER
    CHECK (
      required_independent_passes IS NULL
      OR required_independent_passes BETWEEN 0 AND 2
    ),
  qualifying_independent_passes INTEGER
    CHECK (
      qualifying_independent_passes IS NULL
      OR qualifying_independent_passes >= 0
    ),
  changes_requested_count INTEGER
    CHECK (changes_requested_count IS NULL OR changes_requested_count >= 0),
  open_high_count INTEGER
    CHECK (open_high_count IS NULL OR open_high_count >= 0),
  open_medium_count INTEGER
    CHECK (open_medium_count IS NULL OR open_medium_count >= 0),
  fresh_review_required_count INTEGER
    CHECK (
      fresh_review_required_count IS NULL
      OR fresh_review_required_count >= 0
    ),
  qualifying_receipt_id_1 TEXT,
  qualifying_receipt_id_2 TEXT,

  FOREIGN KEY (project_id, task_id)
    REFERENCES tasks(project_id, task_id),
  FOREIGN KEY (
    project_id, task_id, review_target_kind, review_target_value,
    review_target_base_revision, review_target_generation,
    qualifying_receipt_id_1
  ) REFERENCES review_receipts(
    project_id, task_id, target_kind, target_value,
    target_base_revision, target_generation, review_receipt_id
  ),
  FOREIGN KEY (
    project_id, task_id, review_target_kind, review_target_value,
    review_target_base_revision, review_target_generation,
    qualifying_receipt_id_2
  ) REFERENCES review_receipts(
    project_id, task_id, target_kind, target_value,
    target_base_revision, target_generation, review_receipt_id
  ),

  CHECK (
    (review_target_kind = ''
      AND review_target_value = ''
      AND review_target_base_revision = ''
      AND review_target_generation = 0)
    OR
    (review_target_kind = 'git_snapshot'
      AND review_target_value != ''
      AND review_target_base_revision != ''
      AND review_target_generation > 0)
    OR
    (review_target_kind IN (
        'git_commit', 'diff_fingerprint', 'external_revision'
      )
      AND review_target_value != ''
      AND review_target_base_revision = ''
      AND review_target_generation > 0)
  ),
  CHECK (
    (completion_evidence_kind = 'none'
      AND completeness = 'partial'
      AND completion_evidence_revision = ''
      AND completion_evidence_reason = ''
      AND external_revision_approved = 0
      AND completion_commit_required = 1
      AND completion_commit_hash = '')
    OR
    (completion_evidence_kind = 'git_commit'
      AND completion_evidence_revision != ''
      AND completion_evidence_reason = ''
      AND external_revision_approved = 0
      AND completion_commit_required = 1
      AND completion_commit_hash = completion_evidence_revision)
    OR
    (completion_evidence_kind = 'external_revision'
      AND completion_evidence_revision != ''
      AND completion_evidence_reason != ''
      AND external_revision_approved = 1
      AND completion_commit_required = 1
      AND completion_commit_hash = completion_evidence_revision)
    OR
    (completion_evidence_kind = 'commit_not_required'
      AND completion_evidence_revision = ''
      AND completion_evidence_reason = ''
      AND external_revision_approved = 0
      AND completion_commit_required = 0
      AND completion_commit_hash = '')
    OR
    (completion_evidence_kind = 'legacy_unverified'
      AND completeness = 'partial'
      AND completion_evidence_revision != ''
      AND completion_evidence_reason = ''
      AND external_revision_approved = 0
      AND completion_commit_hash = completion_evidence_revision)
  ),
  CHECK (
    (origin = 'native_done'
      AND completeness = 'complete'
      AND completed_at IS NOT NULL
      AND verification_attestation = 1
      AND review_target_kind != ''
      AND gate_basis_version = 1)
    OR
    (origin = 'legacy_current_done'
      AND completeness = 'partial'
      AND verification_attestation IS NULL
      AND gate_basis_version = 0)
  ),
  CHECK (
    (gate_basis_version = 0
      AND review_basis_kind = 'unknown'
      AND required_independent_passes IS NULL
      AND qualifying_independent_passes IS NULL
      AND changes_requested_count IS NULL
      AND open_high_count IS NULL
      AND open_medium_count IS NULL
      AND fresh_review_required_count IS NULL
      AND qualifying_receipt_id_1 IS NULL
      AND qualifying_receipt_id_2 IS NULL)
    OR
    (gate_basis_version = 1
      AND required_independent_passes =
        CASE review_tier WHEN 0 THEN 0 WHEN 1 THEN 1 ELSE 2 END
      AND qualifying_independent_passes IS NOT NULL
      AND changes_requested_count = 0
      AND open_high_count = 0
      AND open_medium_count = 0
      AND fresh_review_required_count = 0
      AND (
        (review_basis_kind = 'independent_passes'
          AND review_tier IN (1, 2)
          AND qualifying_independent_passes >= required_independent_passes
          AND qualifying_receipt_id_1 IS NOT NULL
          AND (
            (review_tier = 1 AND qualifying_receipt_id_2 IS NULL)
            OR
            (review_tier = 2 AND qualifying_receipt_id_2 IS NOT NULL)
          ))
        OR
        (review_basis_kind = 'self_review_fallback'
          AND review_tier IN (1, 2)
          AND qualifying_independent_passes < required_independent_passes
          AND qualifying_receipt_id_1 IS NOT NULL
          AND qualifying_receipt_id_2 IS NULL)
        OR
        (review_basis_kind = 'not_required'
          AND review_tier = 0
          AND qualifying_receipt_id_1 IS NOT NULL
          AND qualifying_receipt_id_2 IS NULL)
      ))
  )
);

CREATE UNIQUE INDEX idx_task_completion_cycles_task_ordinal
  ON task_completion_cycles(project_id, task_id, saved_cycle_ordinal);

ALTER TABLE task_events
  ADD COLUMN completion_cycle_id TEXT
    REFERENCES task_completion_cycles(completion_cycle_id);

CREATE INDEX idx_task_events_completion_cycle
  ON task_events(completion_cycle_id)
  WHERE completion_cycle_id IS NOT NULL;

CREATE TRIGGER trg_task_completion_cycles_no_update
BEFORE UPDATE ON task_completion_cycles
BEGIN
  SELECT RAISE(ABORT, 'immutable_completion_cycle');
END;

CREATE TRIGGER trg_task_completion_cycles_no_delete
BEFORE DELETE ON task_completion_cycles
BEGIN
  SELECT RAISE(ABORT, 'immutable_completion_cycle');
END;

CREATE TRIGGER trg_tasks_completion_history_coverage_immutable
BEFORE UPDATE OF completion_history_coverage ON tasks
WHEN NEW.completion_history_coverage IS NOT OLD.completion_history_coverage
BEGIN
  SELECT RAISE(ABORT, 'immutable_completion_history_coverage');
END;

CREATE TRIGGER trg_task_events_completion_cycle_link_immutable
BEFORE UPDATE OF completion_cycle_id ON task_events
WHEN NEW.completion_cycle_id IS NOT OLD.completion_cycle_id
BEGIN
  SELECT RAISE(ABORT, 'immutable_completion_cycle_link');
END;
```

The required-object inventory includes the table, all four named indexes,
`trg_task_completion_cycles_no_update`,
`trg_task_completion_cycles_no_delete`,
`trg_tasks_completion_history_coverage_immutable`, and
`trg_task_events_completion_cycle_link_immutable`. The first two abort every
cycle update/delete. The coverage trigger aborts a value change after insert.
The event-link trigger aborts an update from null or a saved ID to a different
value. M18 inserts links only with new events; it never retrofits an old event.

SQLite checks enforce structural matrices. Repository validation additionally
applies the existing canonical full-Git-hash, fingerprint, external-text,
timestamp, and signed-64-bit validators. A receipt-slot insert must reread the
referenced row and confirm the required kind, verdict, approval, reviewer
ordering, and exact target before the composite foreign key is allowed to
persist.

### Migration 15 And Marker Migration 16

`apply_completion_cycle_history_migration()` requires complete schema v14 and
no active transaction. Its exact order is:

1. Record task, event, Contract, checkpoint, handoff, review, maintenance,
   backup, identity, and binding identity/count fingerprints for verification.
2. Start `BEGIN IMMEDIATE` with foreign keys enabled.
3. Add coverage and the two parent-key indexes.
4. Create `task_completion_cycles`, its ordinal index, and immutable-cycle
   triggers.
5. Add the nullable event link, its index, and the coverage/link triggers.
6. Read current done Tasks by `task_id COLLATE BINARY ASC`. Validate their
   current completion projection with the existing legacy-allowed matrix.
7. Insert one generated ordinal-1 `legacy_current_done`/`partial` row per
   current done Task. Copy completion time, Contract revision, review tier,
   `specified` versus `unspecified`, all six completion fields, and all four
   target fields. Use one migration timestamp as `recorded_at`; use null
   attestation, gate-basis version 0, null counts, and null receipt slots.
8. Assert every Task still has `legacy_unknown`, current done count equals
   cycle count, every non-done Task has zero cycles, every old event link is
   null, and all recorded identity/count fingerprints are unchanged.
9. Insert migration row `(15, 'completion_cycle_history', applied_at)`, run
   quick and foreign-key checks, and commit.

Any failure rolls back DDL, backfill, and migration history. Reentry at v15
validates required columns/objects, foreign keys, coverage values, and each
stored cycle's matrix/ownership without reapplying migration-time
status/cardinality assertions or inserting a row. This matters because a
legitimate M18.1 capture-less completion may be done with no cycle and a
staging reopen may leave a migrated partial cycle unmatched by the current
Task. No old event text is parsed or changed.

`apply_completion_cycle_capture_activation_migration()` requires structurally
complete v15 and confirms that every Task still has `legacy_unknown`. It then:

1. starts one `BEGIN IMMEDIATE` transaction;
2. reads current done Tasks by `task_id COLLATE BINARY ASC` and validates each
   current completion projection;
3. loads each Task's latest ordinal cycle and compares completion time,
   Contract revision, review tier, derived verification expectation, all six
   completion fields, and all four target fields, and checks whether any
   `task_reopened` event already links that cycle;
4. when no cycle exists, that exact projection differs, or a reopen event
   already links the latest cycle, inserts one next-ordinal
   `legacy_current_done`/`partial` row copied from the current projection with
   the activation timestamp, null attestation, gate-basis version 0, null
   counts, and null receipt slots;
5. leaves an exact matching and un-reopened latest cycle unchanged;
6. inserts `(16, 'completion_cycle_capture_activation', applied_at)`, validates
   required objects, row matrices, identity/ownership, quick check, and foreign
   keys, then commits.

Any invalid current projection, ordinal overflow, duplicate, or validation
failure rolls back both the partial rows and marker. Existing unlinked reopen
events are not edited or inferred.
Schema v16 is marker-only structurally; it adds no schema object, but activation
may conservatively reconcile the current done projections created during the
capture-less v15 window. Schema-v16 `add_task()` explicitly writes `complete`;
the database default stays `legacy_unknown`. Reentry with marker 16 already
present validates its exact name, the v15 objects, foreign keys, coverage, and
stored cycle/link matrices; it never repeats reconciliation or inserts a row.

All setup paths apply the ordinary pre-migration backup before v14-to-v15/v16
work. Existing fixed managed backups may contain v14, v15, or v16. A restored
v15 fixed backup is migrated to v16 before normal writes. Legacy-layout source
discovery remains limited to schemas 1-13 plus the explicit schema-v14
transition.

### Repository Boundaries

The storage layer adds narrow immutable models for `CompletionCycle`,
`CompletionGateBasis`, and `CompletionHistory`. It exposes only:

- `select_completion_gate_basis_locked()` for current exact-target rows;
- `insert_completion_cycle_locked()` from service-constructed validated data;
- `read_latest_completion_cycle()` for reopen/current-projection matching;
- `read_completion_history()` for one Task; and
- one bounded batch history reader for the Viewer.

No public or generic API accepts a caller-supplied cycle, ordinal, gate basis,
receipt list, activation-reconciliation input, or compatibility-bridge
payload. IDs use the established
cryptographic-token generator with prefix `tg_completion_cycle`. Ordinal
allocation reads the locked Task's maximum and rejects
`9,223,372,036,854,775,807` before addition.

`PUBLIC_EVENT_FIELDS` is exactly:

```python
(
    "task_event_id",
    "task_id",
    "project_id",
    "event_type",
    "summary",
    "created_at",
)
```

Every event-return path constructs from that allow-list. `SELECT *` may remain
internal, but no `dict(row)` from a row containing the link crosses a service
or formatter boundary.

### Gate-Basis Capture

Under the same writer that commits completion, the selector uses exact
`project_id`, `task_id`, target kind/value/base/generation and computes:

- distinct independent PASS reviewer count;
- current-target `changes_requested` receipt count;
- open high and medium finding counts across that Task;
- resolved high/medium findings whose receipt generation is greater than or
  equal to the current target generation; and
- deterministic qualifying receipt IDs.

Tier 1/2 independent PASS rows order by
`reviewer_key COLLATE BINARY ASC, review_receipt_id COLLATE BINARY ASC`.
When the distinct count meets the tier requirement, the selector retains only
the first one or two rows and never considers a fallback. Otherwise it selects
the first valid fallback by `review_receipt_id COLLATE BINARY ASC`, with the
existing Tier-2 approval rule. Tier 0 selects the first valid not-required row
by that same receipt-ID order. The selector rejects duplicate reviewer/basis
anomalies rather than normalizing them.

### Native Done Transaction

Both done paths produce one existing `CompletionPlan`. Git resolution and
snapshot validation finish outside the writer. The exact writer/savepoint
order is:

1. `BEGIN IMMEDIATE` through the existing initialized writer and revalidate
   schema v16, project identity/binding, Task optimistic basis, Contract
   revision, sequential order, evidence, and target.
2. Reread receipts/findings, run the existing gate, and build the gate basis
   with independent-over-fallback precedence.
3. Choose the canonical completion time and allocate the next ordinal.
4. Insert one `native_done` complete cycle from only that locked basis.
5. Update the current Task to done with the same completion/evidence values.
6. Run the existing affected-lane invariant and reread the Task.
7. Insert the existing successful completion event with the internal cycle ID.
8. Record the existing Effort transition and Viewer source-generation business
   mutation, release the savepoint, and commit the outer writer.

The event type and sanitized summary remain the existing task-edit result; no
new event type is introduced. A failure at any numbered step rolls back cycle,
Task, event, Effort, and Viewer-generation changes. Backup/Viewer publication
starts only after commit and preserves its existing last-good/failure contract.
Concurrent completions serialize; one commits and the loser receives the
existing done/concurrent-state error without another cycle.

Read-only `task complete --check` performs no insert. It may diagnose current
gates but `completion_history_inconsistent` remains a command/state error,
not a new blocker item in the bounded check payload.

### Reopen Transaction

The reopen savepoint order is:

1. Lock and reread the exact done Task and coverage.
2. Load the highest ordinal cycle and any linked reopen event.
3. When a latest cycle exists, compare completion time, Contract revision,
   review tier, derived verification expectation, all six completion fields,
   and all four target fields, and determine whether a reopen event already
   links it.
4. If no cycle exists and coverage is `legacy_unknown`, validate the locked
   current projection and insert the exact ordinal-1 partial compatibility
   bridge. Reject `complete` coverage with no cycle, any existing-cycle/current
   projection mismatch, or an already-linked reopen as
   `completion_history_inconsistent`. Normal activation makes the no-cycle
   bridge unreachable for its then-current done Tasks, but the stored exact
   compatibility path remains fail-closed for any other unknown-coverage
   state.
5. Compute the next review generation, update the Task to `in_progress`,
   clear current completion/target values, and keep coverage unchanged.
6. Run the existing sequential-lane invariant.
7. Insert the existing `task_reopened` event linked to the validated latest or
   newly bridged cycle.
8. Record the existing Effort/Viewer-generation transition, release the
   savepoint, and commit.

Reopen does not query historical receipts as eligibility, revalidate the
accepted Git object, alter a cycle, or synthesize a bridge when any cycle
already exists. The next done transition uses `MAX(ordinal)+1` and all fresh
current gates.

### Bounded Read Model And Viewer

`read_completion_history()` performs counts, the incomplete-legacy aggregate,
and rows in the same query-only transaction as the rest of task show.
`legacy_history_incomplete` uses all cycles/events, not only returned rows.
Rows order by ordinal descending.

The formatter constructs the exact five-key object:

```json
{
  "total": 2,
  "returned_count": 2,
  "truncated": false,
  "legacy_history_incomplete": false,
  "cycles": []
}
```

Each public cycle is first validated against the exact nested allow-list in the
specification. The exact measurement serializer for both a cycle and the
complete wrapper is
`json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")`.
A cycle may be appended only if that serialization is at most 8,192 bytes.
Beginning with an empty wrapper, rows are considered newest first. For each
candidate the formatter constructs and measures the final five-key wrapper
using the transaction's actual total and legacy flag, the candidate array and
returned count, and `truncated = returned_count < total`. It accepts the row
only while both the 10-row and 32,768-byte complete-wrapper limits hold. The
first non-fitting row stops collection; older rows are not substituted. This
candidate-final measurement covers non-ASCII UTF-8 and integer digit growth.

The public gate-basis projection maps version-0 nullable counts to JSON `null`
and both receipt slots to `[]`. Version 1 maps counts to JSON integers and
filters the two slots in stored order into a string array with no null
placeholder: one element for Tier-0 not-required, Tier-1 independent, or
Tier-1/2 fallback, and two for Tier-2 independent. Attestation maps to JSON
`true` for native cycles and `null` for legacy partial cycles; `false` is
rejected before formatting.

Text output is one bounded summary containing returned/total, truncation,
legacy flag, and the newest cycle's non-content fields. It never prints
revision/reason/receipt values.

The Viewer batch reader accepts at most the existing 500 selected Tasks and
uses windowed/grouped queries to fetch at most 10 cycles per Task plus exact
totals/incomplete aggregates; it does not issue one query per cycle. Snapshot
v4 includes the identical five-key object. Source schemas 5-14 synthesize only
an empty/incomplete projection; v15/v16 use stored rows. Snapshot v3 accepts
v15/v16 during its staging owners but omits coverage, cycles, and internal event
links.

Snapshot v4 keeps the existing 64 MiB artifact bound, atomic replacement,
generation comparison, last-good behavior, CSP, text-only DOM construction,
M15.5 single visibility-aware timer, and M15.6 allow-listed one-shot History
state. The browser never reads SQLite and saves no completion history.

### Failures, Concurrency, Privacy, And Verification

The sole new stable error is:

```text
code: completion_history_inconsistent
exit: 2
message: stored completion history is inconsistent
```

It includes no IDs, values, counts, paths, SQL, exception details, or hashes.
Migration structural failures remain the existing sanitized setup/unreadable
errors. Busy/journal, stale completion plan, Git, review, lane, and project
binding errors retain their current precedence.

No SQLite writer is held during Git, backup copying, Viewer rendering,
subprocesses, or browser work. Cycle insertion occurs only in the already short
business writer. The one-table/two-column design adds no service, worker,
timer, queue, watcher, network, or target-project mutation.

Cycles contain only bounded values already accepted into the current Task and
stable receipt IDs. They exclude verification-run records, raw command output,
review/finding bodies, diffs, prompts, reasoning, transcripts, environment
data, paths, secrets, tokens, and authorization material.

M18 tests must cover:

- v1-v14 through v15/v16 migration, exact current-done backfill, all other
  statuses, historical done-`none`, a 500-character external revision,
  M18.1-window Task creation/completion/reopen followed by idempotent setup,
  rollback, quick/FK checks, and unchanged
  12-Task/191-event/completion/review traces;
- every evidence kind, target kind, tier, fallback, independent-plus-fallback
  precedence, Contract revision, same-second ordering, and both done paths;
- exact activation reconciliation for matching, absent, and mismatching latest
  cycles; the exclusive unknown/no-cycle reopen bridge and complete/no-cycle
  refusal; done A/reopen/done B ordinals and event links; fresh gates;
  mismatch/reuse/overflow/concurrency and injected failures;
- exact JSON/text allow-lists and byte/count bounds, event-link non-disclosure,
  single-transaction reads, list/current/next/compact/Review Packet stability;
- Viewer v3 staging compatibility, v4 sources 5-16, 500-Task/64-MiB behavior,
  last-good publication, M15.5 reload, M15.6 restore, CSP, no storage/network;
- fixed backup/restore, v15-to-v16 recovery, corruption refusal, M17 identity/
  binding/relocation, self-host and installed layouts; and
- exactly 20 command leaves, nine/ten normal calls, full offline suite,
  `doctor`, manifest/package integrity, `git diff --check`, and two exact-target
  Tier 2 reviews per unit.

## Validation Rules

Task validation:

- `title` must be non-empty.
- Free-form fields must pass the size limits and privacy checks in
  `docs/specification.md`.
- `kind` must be `sequential` or `optional`.
- `priority` must be one of the MVP priorities.
- `status` must be one of the current schema statuses; TG-M8 adds `paused`.
- `review_tier` must be integer `0`, `1`, or `2`.
- `blocked_reason` is required when status is `blocked`.
- `task add --status blocked` must reject before storage unless
  `--blocked-reason` is provided.
- `task add --status done` must reject with `initial_done_forbidden` before
  storage.
- `pause_reason` is required and non-empty exactly while the task is `paused`,
  and is cleared when leaving `paused`; concise history belongs in task events.
- `completed_at` is set when status becomes `done` and cleared when moving back
  to an active status.

Sequential task behavior:

- If `kind=sequential` and `lane_order` is omitted, append after the max order in
  the lane.
- If `kind=sequential` and `lane` is omitted, use a deterministic default lane
  such as `default`.
- `task next` does not return later ready sequential tasks while earlier tasks in
  the same lane are incomplete.
- Direct transitions to `in_progress`, `review_pending`, or `done` use the same
  predecessor predicate as `task next`.
- Sequential add and combined kind/lane/order/status edits validate the
  resulting affected lanes so registration or reordering cannot create an
  already-active out-of-order state.
- `task next` supports `--kind`, `--lane`, `--priority`, and `--limit`; default
  limit is `5`.
- `task next` sorts by priority rank (`urgent`, `high`, `normal`, `low`), lane,
  `lane_order` with nulls last, creation time, and `task_id`.

Repository and schema tests must cover duplicate sequential lane orders, null
sequential orders, blocked tasks without blocked reasons, completed timestamp
transitions, and `project_mismatch` behavior for internally injected database
targets. Public `--db` remains unavailable.

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
Doctor: ready
Task <task-id>: ready
Blocking: none
Suggested action: run task complete with the same evidence and confirmations
```

Text output never exposes a database, backup, Viewer, or other internal state
path. Avoid long explanations in CLI text output. Detailed guidance belongs in
skill references.

## Testing Strategy

Use Python standard-library tests where possible.

Required test areas:

- CLI help exits successfully.
- `doctor` reports missing project state without initializing it.
- `setup --read-only` is no-write; `setup` initializes or migrates isolated
  project state and is idempotent.
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

TG-M8 focused tests must additionally cover:

- task writes against missing and old-schema databases leave paths and bytes
  unchanged and return `db_not_initialized` or `migration_required`;
- initial done rejection and every completion-gate path;
- paused transitions, required pause reason, resumption, and active counts;
- shared sequential predecessor behavior for next/start/review/done;
- registration, reordering, combined-edit, and concurrent-write attempts that
  would create an out-of-order active/review/done row;
- deterministic `task current` ordering, latest-event selection, limit, JSON
  shape, and zero database/Git mutation;
- Git commit existence, annotated-tag peeling, unique short hash
  canonicalization, ambiguity/missing rejection, and unchanged Git state;
- explicit external-revision acknowledgement in Git projects;
- Tier 0/1/2 receipts, reviewer distinctness, target equality, fallback
  approval, target changes, and unresolved/resolved finding gates;
- privacy rejection and field limits for every new free-form field;
- concurrent writers and readers under SQLite transaction/timeout behavior;
- sequential v2-to-v3-to-v4-to-v5 migration, idempotent rerun, rollback on
  failure, and the 12-task/191-event preservation fixture; and
- regression coverage for project identity, read-only behavior, task next,
  compact envelopes, and canonical Viewer maintenance.

TG-M9 focused tests must additionally cover:

- exact paused counts in current doctor task-summary rows while preserving the
  meaning of `active`;
- zero/positive paused warning behavior and identical next-task candidate data
  with and without a paused population;
- warning reuse of the successful status-inspection count, its documented
  advisory freshness under concurrent writers, and no event, sidecar,
  database, or Git mutation;
- warning privacy: only a count and fixed command text may be serialized;
- unfiltered current-task compatibility and each accepted `--status` value,
  especially the paused-only latest-event and suggested-action projection;
- invalid current status, limit, missing database, migration-required, and
  project-mismatch errors without writes; and
- installed-Skill self-containment, CLI help, compact JSON envelopes, and the
  full offline regression suite after guidance synchronization.

Task Viewer tests must additionally cover:

- all-status snapshot projection with completion commit fields and no private
  source paths
- the 10-event-per-task bound and deterministic event ordering
- base64 payload round-trip and HTML-shaped task text rendered as text
- canonical output containment, pre-opt-in no-write behavior, atomic
  replacement, and last-good preservation
- missing DB, migration-required, and project-mismatch propagation
- exact generation triggers, zero-wait contention, at-most-two renders, and
  no generation from read/replay/no-op/handoff/internal writes
- no external resource URLs or network APIs in the bundled template
- exact CSP directive assertions and prohibited DOM sink assertions for
  `innerHTML`, `insertAdjacentHTML`, `eval`, `Function`, inline event
  attributes, and task-derived URL attributes
- absent-profile interval `0`, strict profile schema/range/size/UTF-8 checks,
  path link/reparse/replacement rejection, and one profile load per publication
- exactly one snapshot plus one interval placeholder; setup no-write
  repair preview, setup last-good failure, routine sanitized warning, and
  interval/template drift repair
- structural scheduler assertions for file-only, fatal decode, monotonic
  remainder, hidden-state timer clearing, one timeout, and one reload request,
  while preserving the exact CSP and absence of network/storage APIs
- isolated canonical publication from an injected/copied skill state
- representative desktop and mobile `file://` browser checks, including a
  real visibility-aware auto-reload forward test with a valid profile and a
  no-reload check when the profile is absent

## Packaging And Release Design

The source repository is the development and review surface. The installable
skill folder is the project-scoped distribution unit.

Release artifacts should include the installable skill folder only:

```text
task-governance-tool/
  release-manifest.json
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
- Verification recording and acceptance-progress receipts.
- Review generation beyond TG-M14's bounded read-only Review Packet.
- Dependency graphs.
- Additional Git advisory integration beyond read-only completion validation.
- Task import and richer exchange formats beyond the approved static viewer.
- Stale-active warnings, handoff paging/retention, event-history pagination,
  and parent/child or checklist execution units.
- Cursor pagination for bounded current/list results and a separately designed
  once-daily GitHub update check.

These extensions must not change MVP privacy or target-project mutation
semantics without updating `docs/specification.md` and this design.
