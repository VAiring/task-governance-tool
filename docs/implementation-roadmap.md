# task-governance-tool MVP Implementation Roadmap

Status: formal MVP implementation roadmap baseline.

This document turns `docs/specification.md` and `docs/design.md` into
implementation-sized execution units. It is the preferred roadmap for building
the MVP after user approval.

## Use Of This Roadmap

Before starting each execution unit, reread `AGENTS.md`,
`docs/specification.md`, `docs/design.md`, this roadmap, and `plan.md`.

For every execution unit, declare:

- intended outcome
- write scope
- verification gate
- review tier

At every execution-unit boundary, update `plan.md` or a later task-status
artifact until SQLite-backed task status exists.

After implementation, run the listed verification gate. For Tier 2 units, run
two independent review passes when review tooling is available. Commit only
after required verification passes and review has no blocking high or medium
findings.

Implementation units may modify this repository's source, docs, tests, and
fixtures within their declared write scopes. Runtime behavior and tests must not
mutate target project files. Test execution may write only to
task-governance-tool test databases, temporary directories, or generated
skill-local runtime state.

## Skill Validation Fallback

If Codex skill helper scripts are unavailable, use this documented self-check:

- skill folder name matches `SKILL.md` `name`
- `SKILL.md` frontmatter contains only `name` and `description`
- description includes only MVP-supported trigger contexts
- all referenced one-level reference files exist
- `agents/openai.yaml` matches skill metadata if present
- `scripts/taskgov.py --help` runs from the skill folder
- runtime imports do not depend on modules outside
  `task-governance-tool/scripts/`

## Implementation Lanes

Sequential lanes:

- `CORE`: repository shape, CLI entry point, shared command infrastructure.
- `DB`: database path resolution, schema, migrations, repository behavior.
- `TASK`: task registration, inspection, mutation, and event history.
- `NEXT`: next-task selection behavior.
- `SKILL`: installable skill instructions and bundled references.
- `HARDEN`: final fixture, packaging, and forward-test hardening.

Optional units may be consumed whenever their prerequisites are met. A blocker
in one sequential lane should not stop ready optional units in another lane.

## Milestone TG-M1: Skill, CLI, And DB Skeleton

Goal: create a self-contained installable skill folder, CLI skeleton, database
path resolution, explicit initialization, read-only status inspection, and
baseline tests.

### TG-M1.1 Repository And Test Harness

Kind: sequential
Lane: `CORE`
Depends on: none
Review tier: Tier 1

Write scope:

- `task-governance-tool/scripts/taskgov.py`
- `task-governance-tool/scripts/task_governance_tool/__init__.py`
- `task-governance-tool/scripts/task_governance_tool/cli.py`
- `tests/`

Implementation notes:

- Use Python standard library only.
- Make `scripts/taskgov.py --help` work from the skill folder.
- Tests should import runtime modules by adding
  `task-governance-tool/scripts/` to `sys.path`.
- Do not create the database in this unit.

Verification gate:

- CLI help exits successfully.
- Unit test harness runs without network access.

Completion criteria:

- The entry point and import layout match `docs/design.md`.
- No target project files are modified by tests.

### TG-M1.2 JSON/Text Output And Error Envelope

Kind: sequential
Lane: `CORE`
Depends on: TG-M1.1
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/cli.py`
- focused CLI tests

Implementation notes:

- Implement the stable JSON envelope:
  `ok`, `command`, `project_id`, `db_path`, `data`, `warnings`, `errors`.
- Implement exit code mapping:
  `0` success, `1` validation/user-correctable error, `2` database/migration or
  unexpected tool error.
- Use stable command names from `docs/specification.md`.
- Text output must be concise and operational.

Verification gate:

- JSON envelope tests for success and validation error examples.
- Help text tests for common options:
  `--repo`, `--db`, `--json`, `--read-only`.
- Argument parsing test proves `--read-only` reaches command handlers.

Completion criteria:

- Command output contracts are deterministic enough for later task commands.

### TG-M1.3 Project ID And DB Path Resolution

Kind: sequential
Lane: `DB`
Depends on: TG-M1.1
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/storage.py`
- path-resolution tests

Implementation notes:

- Resolve `--repo` to a canonical project path.
- Normalize Windows paths before hashing. Handle drive-letter and UNC forms
  consistently.
- Compute `project_id` as sanitized basename plus first 12 SHA-256 hex
  characters of the normalized canonical path.
- Resolve default DB path to:
  `<installed-skill-root>/state/projects/<project-id>/taskgov.sqlite`.
- Support explicit `--db` override.
- Create parent directories only for write commands that initialize or migrate
  the selected database.

Verification gate:

- Separate repo roots produce separate default DB paths.
- Project IDs do not include private parent path details.
- Explicit `--db` path resolution does not create files during read-only
  inspection.

Completion criteria:

- DB path behavior is stable and local-first.

### TG-M1.4 Schema Migration And `db init`

Kind: sequential
Lane: `DB`
Depends on: TG-M1.2, TG-M1.3
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/storage.py`
- `task-governance-tool/scripts/task_governance_tool/cli.py`
- migration/repository tests

Implementation notes:

- Implement schema versioning and `schema_migrations`.
- Create `project_meta`, `tasks`, `task_events`, and `tool_events`.
- Enforce schema constraints from `docs/design.md`, including:
  blocked tasks require `blocked_reason`, sequential tasks require non-empty
  `lane`, sequential tasks require non-null `lane_order`, and sequential
  `(project_id, lane, lane_order)` is unique.
- Implement `taskgov db init`.
- If `--read-only` is supplied to `db init`, reject before creating parent
  directories, creating a database, migrating, or writing.
- `db init` JSON data must include `created`, `migrations_applied`, and
  `schema_version`.
- Store project metadata and fail `project_mismatch` if an existing DB belongs
  to a different computed project ID.

Verification gate:

- Temp DB initialization test.
- Idempotent migration test.
- Schema constraint tests.
- Project mismatch test using explicit `--db`.
- `db init --read-only` does not create or modify files and returns a
  user-correctable error.

Completion criteria:

- A fresh DB can be initialized safely and repeatedly.

### TG-M1.5 Read-Only `db status`

Kind: sequential
Lane: `DB`
Depends on: TG-M1.4
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/storage.py`
- `task-governance-tool/scripts/task_governance_tool/cli.py`
- `db status` tests

Implementation notes:

- `db status` is read-only by default.
- A missing DB reports `exists=false`, `needs_init=true`, and
  `db_not_initialized` without creating files.
- A DB requiring migration reports `needs_migration=true` and
  `migration_required` without migrating.
- Existing usable DB reports counts and next-actionable count. Before `task
  next` exists, next-actionable count may use the same ready-task logic
  available at that milestone and should be tightened in TG-M3.
- Active task count means statuses `ready`, `in_progress`, `blocked`, and
  `review_pending`; it excludes `done` and `cancelled`.

Verification gate:

- Missing DB status does not create a database or parent state directory.
- Initialized DB status returns the required JSON payload.
- Text output remains concise.

Completion criteria:

- Inspection and write command behavior are clearly separated.

## Milestone TG-M2: Task Registry MVP

Goal: implement explicit task registration, filtered task inspection, task
detail display, metadata/status updates, privacy validation, and event history.

### TG-M2.1 Task Domain Validation

Kind: sequential
Lane: `TASK`
Depends on: TG-M1.4
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/tasks.py`
- validation tests

Implementation notes:

- Validate enum fields, required title, review tier integer, and status values.
- Apply size limits:
  title 200, description 4000, verification 500, tags 500, add-note 2000, event
  summary 1000 characters.
- Reject obvious secrets, raw logs, stack traces, and large raw diffs with
  `privacy_rejected`.
- Do not store raw command output.

Verification gate:

- Validation tests for each enum and size limit.
- Privacy rejection tests for the patterns in `docs/design.md`.

Completion criteria:

- All task write paths can share one validation layer.

### TG-M2.2 `task add`

Kind: sequential
Lane: `TASK`
Depends on: TG-M1.5, TG-M2.1
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/tasks.py`
- `task-governance-tool/scripts/task_governance_tool/cli.py`
- CLI/repository tests

Implementation notes:

- Implement explicit task registration only; no draft/import/approval flow.
- Default `kind=optional`, `priority=normal`, `status=ready`,
  `review_tier=1`.
- If sequential lane is omitted, store deterministic lane `default`.
- If sequential order is omitted, append after the max order in that lane.
- If initial `--status blocked` is supplied, require `--blocked-reason` and
  reject before storage when it is missing.
- If `--read-only` is supplied, reject before creating, migrating, or writing.
- JSON data must include `task` and `event`.
- Output must include stored `lane` and `lane_order` when auto-filled.

Verification gate:

- Add optional and sequential tasks.
- Auto-filled lane/order tests.
- Blocked initial status requires blocked reason.
- Duplicate sequential lane order is rejected by schema/repository behavior.
- Privacy rejection prevents storage.
- `task add --read-only` does not create or modify rows.

Completion criteria:

- Tasks can be registered without reading or writing target project files.

### TG-M2.3 `task list`

Kind: sequential
Lane: `TASK`
Depends on: TG-M2.2
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/tasks.py`
- `task-governance-tool/scripts/task_governance_tool/cli.py`
- list/filter tests

Implementation notes:

- Implement filters:
  `--status`, `--kind`, `--lane`, `--priority`, `--tag`, `--limit`,
  `--include-done`.
- Default limit is around 20; use one documented default and test it.
- `task list` must be read-only and fail with `db_not_initialized` when the DB
  is missing.
- JSON data must include `tasks`, `count`, and `limit`.

Verification gate:

- Filter tests for each supported filter.
- Missing DB does not create files.

Completion criteria:

- Codex can retrieve compact task slices.

### TG-M2.4 `task show`

Kind: sequential
Lane: `TASK`
Depends on: TG-M2.2
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/tasks.py`
- `task-governance-tool/scripts/task_governance_tool/cli.py`
- show/event tests

Implementation notes:

- Return one task, recent events, timestamps, and `suggested_next_action`.
- Use `not_found` for unknown task IDs.
- Keep event history concise.
- `task show` must be read-only.

Verification gate:

- Show existing task with events.
- Unknown task returns structured error.
- Missing DB does not create files.

Completion criteria:

- One task can be inspected without loading all history.

### TG-M2.5 `task edit`

Kind: sequential
Lane: `TASK`
Depends on: TG-M2.2, TG-M2.4
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/tasks.py`
- `task-governance-tool/scripts/task_governance_tool/cli.py`
- edit/event tests

Implementation notes:

- Support editable fields from `docs/specification.md`.
- Require `--blocked-reason` when setting `status=blocked`.
- Set `completed_at` when status becomes `done`; clear it when moving back to
  an active status.
- Append concise task event rows for meaningful changes and notes.
- If `--read-only` is supplied, reject before creating, migrating, or writing.
- JSON data must include `task`, `changed_fields`, and `event`.

Verification gate:

- Status transition tests.
- Blocked reason tests.
- Add-note and event summary size-limit tests.
- Privacy rejection prevents storage.
- `task edit --read-only` does not modify rows.

Completion criteria:

- Task state can be updated without specialized alias commands.

### TG-M2.O1 Synthetic Task Fixture

Kind: optional
Lane: `HARDEN`
Depends on: TG-M2.5
Review tier: Tier 1

Write scope:

- `fixtures/task-status-mvp/`
- fixture tests if useful

Implementation notes:

- Create a small synthetic KuraKoma-style task set.
- Do not copy private raw project content.
- Include optional tasks, sequential lanes, blocked tasks, review-pending tasks,
  and completed tasks.

Verification gate:

- Fixture can seed a temp DB through public CLI or repository helpers.

Completion criteria:

- Later `task next` and forward-test work has realistic local data.

## Milestone TG-M3: Next Task Selection

Goal: implement compact next-actionable task selection that works around
blocked sequential lanes.

### TG-M3.1 Selection Service

Kind: sequential
Lane: `NEXT`
Depends on: TG-M2.3, TG-M2.5
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/selection.py`
- selection repository tests

Implementation notes:

- Include only `status=ready`.
- Optional tasks are directly actionable.
- Sequential tasks are actionable only when earlier tasks in the same lane are
  `done` or `cancelled`.
- Blocked or incomplete earlier sequential tasks block only that lane.
- Sort by priority rank (`urgent`, `high`, `normal`, `low`), lane,
  lane_order nulls last, created_at, and task_id.

Verification gate:

- Ready optional tasks are returned when one sequential lane is blocked.
- Later sequential tasks are hidden until earlier tasks are done/cancelled.
- Sorting is deterministic.

Completion criteria:

- Selection logic is isolated from generic list filtering.

### TG-M3.2 `task next`

Kind: sequential
Lane: `NEXT`
Depends on: TG-M3.1
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/cli.py`
- `task-governance-tool/scripts/task_governance_tool/selection.py`
- CLI tests

Implementation notes:

- Support `--kind`, `--lane`, `--priority`, and `--limit`.
- Default limit is `5`.
- JSON data must include `tasks`, `count`, `limit`, and `selection_rules`.
- `task next` must be read-only.
- Update `db status` next-actionable count to use the final selection service.

Verification gate:

- CLI contract tests for filters and default limit.
- Missing DB does not create files.
- End-to-end temp DB flow: init, add, edit, next, show.

Completion criteria:

- Codex can ask for ready work without loading a large status document.

## Milestone TG-M4: Skill Package And Hardening

Goal: finish the installable skill package, validate self-containment, and
prove the MVP behavior on realistic local fixtures.

### TG-M4.1 `SKILL.md` And Skill References

Kind: sequential
Lane: `SKILL`
Depends on: TG-M1.2, TG-M3.2
Review tier: Tier 2

Write scope:

- `task-governance-tool/SKILL.md`
- `task-governance-tool/agents/openai.yaml`
- `task-governance-tool/references/task_workflow.md`
- `task-governance-tool/references/cli_contracts.md`

Implementation notes:

- `SKILL.md` frontmatter contains only `name` and `description`.
- The description advertises only MVP-supported triggers:
  task planning, task status inspection, next-task selection, blocker handling,
  and local task-state updates.
- Keep `SKILL.md` concise and use one-level references for command details.
- Do not advertise deferred verification recording, review request generation,
  persistent profiles, dependency graphs, Git integration, or target-project
  mutation.

Verification gate:

- Skill metadata validation helper if available.
- Otherwise run the self-check in this roadmap's Skill Validation Fallback
  section.
- Reference links resolve.

Completion criteria:

- The skill can be discovered and used without loading large docs by default.

### TG-M4.2 Installed Skill Self-Containment Smoke Test

Kind: sequential
Lane: `HARDEN`
Depends on: TG-M4.1
Review tier: Tier 2

Write scope:

- tests
- optional temporary packaging fixture excluded from commit

Implementation notes:

- Run `scripts/taskgov.py --help` from a copied or isolated skill folder.
- Ensure runtime imports do not depend on modules outside
  `task-governance-tool/scripts/`.
- Ensure `state/` is excluded from committed source and release artifacts.

Verification gate:

- Smoke test for isolated skill folder help.
- Git ignore check for generated `state/` and SQLite files.

Completion criteria:

- The installable skill folder is self-contained.

### TG-M4.3 Full MVP Contract Test Pass

Kind: sequential
Lane: `HARDEN`
Depends on: TG-M3.2, TG-M4.2
Review tier: Tier 2

Write scope:

- tests
- fixtures
- docs updates only if behavior clarifications are required

Implementation notes:

- Run the full offline test suite.
- Exercise text and JSON output.
- Use temp directories and temp DBs only.
- Confirm no target project mutation.

Verification gate:

- Full MVP test suite.
- Manual check of representative `db status`, `task list`, `task next`, and
  `task show` outputs.

Completion criteria:

- MVP behavior satisfies `docs/specification.md` acceptance criteria.

### TG-M4.O1 Release/Install Decision Note

Kind: optional
Lane: `HARDEN`
Depends on: TG-M4.2
Review tier: Tier 1

Write scope:

- `plan.md` or a future release note document

Implementation notes:

- Record how the skill should be published after MVP completion.
- Do not install or overwrite a user skill directory without explicit user
  approval and destination path.
- Keep generated state and root copied references out of release artifacts.

Verification gate:

- Documentation consistency check.

Completion criteria:

- Publication path is known before release packaging begins.

## Deferred Until After MVP

Do not implement these in the MVP unless `docs/specification.md`,
`docs/design.md`, and this roadmap are updated in the same task:

- Markdown task import
- `task depend` or general dependency graph
- `task approve`
- `profile register` or persistent profile authoring
- verification-run recording
- review request generation
- Git integration
- dashboard or service UI
- automatic target-project mutation
- raw command output retention

## Required Post-MVP Extension: TG-M6 Completion Commit Gate

TG-M6 is the next required product extension. It adds a simple completion
commit gate: each task records whether a commit is required and, when required,
the commit hash or unique revision ID that closes the task. Review and
verification gates continue to be governed by the task workflow and skill
instructions; this extension does not add structured review or verification
evidence tables.

### TG-M6.1 Completion Commit Columns

Kind: sequential
Lane: `COMPLETE`
Depends on: TG-M4.3
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/storage.py`
- repository helpers
- schema/migration tests
- docs updates if the schema differs from `docs/design.md`

Implementation notes:

- Add schema version 2.
- Add `completion_commit_required` to `tasks`, default true.
- Add `completion_commit_hash` to `tasks`, default empty string.
- Add an index for non-empty `completion_commit_hash` lookups.
- Do not add commit, artifact, verification, or review evidence tables in this
  simplified design.
- Keep raw diffs, raw logs, full prompts, full review transcripts, and changed
  material path lists out of the database.

Verification gate:

- Migration tests from schema version 1 to 2.
- Idempotent `db init` migration tests.
- Repository tests confirming migrated tasks default to commit required with an
  empty hash.
- Repository tests for non-empty hash lookup.

### TG-M6.2 Completion Commit CLI

Kind: sequential
Lane: `COMPLETE`
Depends on: TG-M6.1
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/cli.py`
- task/completion service modules
- CLI contract tests
- `task-governance-tool/references/cli_contracts.md`

Implementation notes:

- Add explicit `task edit` options for setting the completion commit hash and
  marking the commit gate not required.
- Add `task edit --status done` confirmation options:
  `--verification-complete` and `--review-complete`.
- Add commit options: `--completion-commit-hash <hash>` and
  `--commit-not-required`.
- Prefer Git commit hashes for Git projects, but allow user-approved unique
  revision IDs for non-Git durable materials.
- Reject a non-empty commit hash when commit is marked not required.
- Keep changed material discovery outside the database; users can trace
  materials from the stored hash through Git or an equivalent VCS.
- Keep command outputs compact and JSON-contract stable.

Verification gate:

- JSON shape tests for commit-required and commit-not-required update paths.
- CLI contract tests for `--verification-complete`, `--review-complete`,
  `--completion-commit-hash`, and `--commit-not-required`.
- Privacy rejection tests for completion commit hashes or revision IDs.
- Missing database and migration-required behavior tests.

### TG-M6.3 Done Enforcement And Hash Trace

Kind: sequential
Lane: `COMPLETE`
Depends on: TG-M6.2
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/cli.py`
- task mutation service
- selection/show/list output as needed
- `task-governance-tool/references/cli_contracts.md`
- tests
- skill references

Implementation notes:

- `task edit --status done` rejects when
  `completion_commit_required=true` and `completion_commit_hash` is empty.
- `task edit --status done` accepts an explicitly not-required commit gate only
  when `completion_commit_required=false` and `completion_commit_hash` is empty.
- Review and verification gates must still pass before a task is marked `done`
  under the workflow rules. Require explicit command-time confirmation for
  those gates, but do not store detailed review or verification records.
- `task show` exposes `completion_commit_required` and
  `completion_commit_hash`.
- Document that changed materials are traced through the target project's VCS,
  for example with `git show --name-only <completion_commit_hash>`.

Verification gate:

- Done-transition blocking tests for missing verification confirmation, missing
  review confirmation, and missing required commit hash.
- Done-transition success test with review/verification confirmations and a
  required commit hash.
- Done-transition success test with review/verification confirmations and
  commit explicitly not required.
- Conflict test for `completion_commit_conflict` when commit is not required
  but a non-empty hash is supplied.
- `task show` output tests for commit requirement and hash fields.

### TG-M6.4 Skill Guidance And Forward Test

Kind: sequential
Lane: `COMPLETE`
Depends on: TG-M6.3
Review tier: Tier 2

Write scope:

- `task-governance-tool/SKILL.md`
- `task-governance-tool/agents/openai.yaml`
- `task-governance-tool/references/task_workflow.md`
- `task-governance-tool/references/cli_contracts.md`
- representative fixture or forward-test notes

Implementation notes:

- Advertise the completion commit gate only after the CLI implements it.
- Explain that task completion still requires verification, required sub-agent
  review, and either a commit hash or an explicit commit-not-required decision.
- Explain that changed materials are traced through Git or another VCS from the
  stored hash, not through separate database artifact rows.
- Preserve the rule that the tool records commit state but does not create
  commits or mutate target projects by default.

Verification gate:

- Skill metadata/self-check.
- Full offline test suite.
- Forward-test or documented representative dry run of a task completion flow.

## Roadmap Completion Criteria

The MVP implementation roadmap is complete when:

- All sequential execution units TG-M1.1 through TG-M4.3 are complete.
- Optional units needed for validation or release have either completed or been
  explicitly deferred.
- `docs/specification.md` acceptance criteria pass.
- Skill validation or documented self-check passes.
- Two independent Tier 2 review passes find no blocking high or medium issues
  when review tooling is available. If review tooling is unavailable, the
  documented AGENTS fallback review path must be followed before completion is
  accepted.
- The final implementation is committed only after review PASS.
