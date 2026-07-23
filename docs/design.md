# task-governance-tool MVP Design

Status: formal implemented design baseline through TG-M8 governance hardening.
The TG-M9 paused-work visibility and TG-M11 completion-integrity correction
designs are approved but not implemented. The TG-M12 scope-control and local-
handoff design is approved for later implementation without changing the
v0.2.0/schema-v5 release baseline.

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
      ordering.py
      selection.py
      completion.py
      reviews.py
      viewer.py
  references/
    task_workflow.md
    cli_contracts.md
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

`db init` is the only create/migrate command. It should follow this flow:

1. Parse arguments.
2. Resolve repo and database path.
3. If `--read-only` is present, reject the command before creating,
   migrating, or writing.
4. Initialize or migrate the database when needed.
5. Emit JSON or concise text.
6. Return a stable exit code.

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

Inspection commands are `db status`, `task list`, `task next`, `task current`,
and `task show`. They must not create, migrate, or write to the
database by default. A missing database should produce `db_not_initialized`; a
database requiring migration should produce `migration_required`; `db status`
should report those states without changing the database.

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
severity. Receipts are append-only and each reviewer may record only one
receipt per task target generation. A correction or re-review requires setting
the review target again to create a new generation; same-generation
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

## Approved TG-M9 Paused Work Visibility Design

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
contract into `task next`; TG-M9 does not authorize that compatibility change.

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

## Approved TG-M11 Completion Integrity Design

TG-M11 is an implementation-deferred correction layer over schema version 5.
It keeps the task/review/storage module boundaries and adds the smallest
state needed to bind a reviewed staged Git tree to a later completion commit.
The lifecycle and input corrections that do not need schema version 6 are
implemented and tested before the snapshot migration.

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

Before implementation, only governing documents, `plan.md`, and ignored local
task state describe TG-M11. `SKILL.md`, installed-skill references, README,
release/install guidance, version metadata, viewer contracts, and product code
remain at the implemented schema-v5 behavior. The final TG-M11 unit updates
those surfaces together only after code, migration, privacy, no-write, and
forward-flow acceptance passes.

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
- a later `self_status.py` may verify package-manifest integrity read-only.

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
There is no fuzzy duplicate or recurrence algorithm.

`handoff record` uses an immediate transaction to validate the source task,
insert or fetch the exact existing record, and commit before any delivery
attempt. The command's success envelope includes the resulting handoff and:

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
`db status.counts.handoff_pending` remains the exact project population signal.
Paging is deferred; after old pending rows are delivered, withdrawn, or
otherwise filtered, later rows move into the bounded window. `handoff show`
returns one full sanitized row. `task show.handoff_summary` contains only exact
per-state counts for that source task.

`handoff withdraw` uses an immediate transaction and succeeds only when state is
pending, `delivery_attempts=0`, and no claim was ever acquired. It requires one
sanitized user-provided reason. Handed-off, withdrawn, claimed, attempted, or
expired-claim records fail `handoff_not_withdrawable` without mutation.

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

Contract input is accepted as an optional group. Both scope and acceptance are
required. The activation matrix is:

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

Before allocating a revision, `contracts.py` canonically compares scope,
acceptance, and constraints with the current row. If all three match, it
returns `recorded=false` with the current revision and performs no write,
regardless of repeated or re-labeled authority/change metadata. A semantic
revision requires at least one of those three fields to change.

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

Only responses whose command supplied Contract input gain an additive sibling:

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

### Optional Effort Advisory Design

Effort Advisory is implemented only under a separate default-off risk profile.
The initial read command returns measurements, thresholds, basis, coverage,
attribution, unknown reasons, one stable warning key, and fixed
`suggested_action=continue`.

When enabled, first entry to `in_progress` may best-effort store a basis HEAD,
endpoint cleanliness, capture timestamp, and activity generation in Task DB.
This is an authorized side effect of that existing write, not a read-only
inspection. Capture failure records no partial basis and never blocks start.
Subsequent Git observation uses argument-vector, optional-lock-disabled,
no-lazy-fetch reads only.

Attribution is conservative. It is `unknown` for non-Git repositories, dirty or
uncertain basis/observation endpoints, missing coverage, or any activity-
generation change showing possible overlap after basis capture. A false claim
of exclusive Task ownership is worse than an unknown result. Initial metrics
may include Git changed files/lines/modules, generated fixture bytes,
structured Task retry counts, Contract revision count, handoff count, and
explicitly configured test metrics.

The command never writes an acknowledgement, asks a question, chooses a
handoff, changes status, expands acceptance, or blocks completion. Repeated
observations may repeat the same stable warning. More precise attribution,
persistent dispositions, and automatic enforcement require a separate future
contract.

### Local Package Self-Status

A later `self status` inspection compares a versioned release manifest of core
package file hashes with the installed copy. It reports package version,
manifest version, and `clean`, `modified`, or `unknown`, plus bounded changed
core paths. Configuration, generated state, and explicitly declared adapter
files are outside the core manifest.

The command is read-only and offline. It never restores files, installs an
update, changes task state, or contacts GitHub. The Skill may advise upstream
Issue or pull-request handling for a modified core but must not stop normal
work solely because of this advisory.

### Migration, Viewer, And Old-Binary Compatibility

Migrations run only through explicit `db init` in strict v5-to-v6-to-v7-to-v8
order. Each unit is idempotent and rollback-safe. The migration fixture retains
the existing 12 tasks, 191 events, nine completion hashes, review evidence,
project identity, and all new preceding-version records. Specific rollback
tests cover claim/outbox preservation, v7 handoffs surviving v8, and atomic
Contract pointer/revision writes.

An older binary compares the stored schema before a write and rejects any newer
version. It does not apply a reverse migration or overwrite version metadata.
Release guidance must require updating the installed package before migrating
and retaining a recoverable local database copy when crossing schema versions.

Viewer snapshot mapping becomes:

- snapshot version 1: schema version 2;
- snapshot version 2: schema versions 3-4; and
- snapshot version 3: schema versions 5-8.

Every snapshot carries its actual `source_schema_version`. The snapshot-v3
allow-list remains unchanged at schemas 6, 7, and 8: it excludes
`review_target_base_revision`, handoff rows, Contract pointers, and Contract
revisions. Normal and `--read-only` export tests run against each intermediate
schema and prove identical privacy, no-sidecar, and task-shape behavior.

### TG-M12 Guidance Boundary

Before implementation, only governing documents, `plan.md`, and ignored local
task state describe TG-M12. Product code, migrations, `SKILL.md`, installed
references, README, release/version metadata, and Viewer code remain at
v0.2.0/schema version 5.

Each implementation unit synchronizes only the CLI and internal contracts it
has actually delivered. The concise Skill workflow adds deterministic Contract
copying and handoff behavior only after their acceptance gates. Issue adapter,
Effort Advisory, and self-status remain unadvertised until their separate
default-off or integration units pass.

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
4. Assemble and validate the snapshot version required by the current schema.
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
- serialize an explicit allow-list for the schema-appropriate snapshot version;
  version 2 adds pause fields but intentionally does not expose schema-v4
  completion evidence until version 3
- order tasks with the existing `task list` priority/lane/order/time/ID rules
- fetch at most 10 events per task, ordered by `created_at DESC, rowid DESC` to
  match `task show` tie behavior
- return plain dictionaries to `viewer.py`

This extension requires no SQLite schema migration. It must not broaden the
existing `task.list` JSON task shape, whose compatibility is independent from
the viewer snapshot.

### Snapshot Assembly

The implemented schema-v2 baseline uses snapshot version 1. TG-M8 updates the
version mapping explicitly, and the approved TG-M11/TG-M12 compatibility rule
extends snapshot version 3 without adding new viewer fields:

- snapshot version 1: schema version 2 and the original six statuses;
- snapshot version 2: schema versions 3-4, adding `paused`, `pause_reason`, and
  seven-status counts; and
- snapshot version 3: schema versions 5-8, adding the final completion/review
  evidence projection while excluding schema-v6 target bases, schema-v7
  handoffs, and schema-v8 Contracts.

`viewer.py` owns this mapping and constructs:

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
- stable status summary for every schema-supported status: six in snapshot v1
  and seven, including `paused`, in snapshot v2 and v3
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

Preserve the existing preflight rejection for active WAL sidecars. Also inspect
the stable SQLite file-header journal bytes without opening a mutable SQLite
connection and reject persistent WAL mode before the snapshot connection. Tests
must prove a normal snapshot read creates no WAL/SHM/journal files, active or
cleanly closed WAL state fails without stale output or new sidecars, and a
concurrent writer either yields a consistent snapshot or a structured tool
error. This does not add cross-session ownership or live synchronization; the
generated timestamp still describes export time, not an exclusive database
revision.

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
  compact envelopes, and static viewer export.

TG-M9 focused tests must additionally cover:

- additive exact `paused` counts on ready and error-shaped `db status`
  responses while preserving the meaning of `active`;
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
- Verification recording and acceptance-progress receipts.
- Review-template generation.
- Dependency graphs.
- Additional Git advisory integration beyond read-only completion validation.
- Task import and richer exchange formats beyond the approved static viewer.
- Stale-active warnings, handoff paging/retention, event-history pagination,
  and parent/child or checklist execution units.
- Cursor pagination for bounded current/list results and a separately designed
  once-daily GitHub update check.

These extensions must not change MVP privacy or target-project mutation
semantics without updating `docs/specification.md` and this design.
