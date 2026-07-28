# task-governance-tool MVP Implementation Roadmap

Status: implementation units through the TG-M16.1 runtime slice are complete
at v0.8.0/schema v13 with Viewer snapshot v3. TG-M16.2 and TG-M16.4 are
approved and pending; TG-M16.3 is cancelled. TG-M12.3 remains blocked on a
future Issue Skill intake contract.
The default-browser launch follow-up remains requirements-only pending design
and roadmap approval.

Completed unit descriptions before TG-M14 are explicitly historical execution
lineage. Their legacy commands, options, and intermediate release surfaces do
not instruct current use; the implemented TG-M14 section and current
specification/design supersede them where they differ.

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
skill-local runtime state. Project-scoped install guidance must not require
tests to mutate a real target project.

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
- `GOVERNANCE-HARDENING`: implemented TG-M8 database, transition, evidence, and
  resume-surface changes.
- `PAUSED-VISIBILITY`: implemented TG-M9 additive counts, warnings, and bounded
  paused-work retrieval.
- `COMPLETION-INTEGRITY`: implemented TG-M11 lifecycle, review, and read-only
  Git binding corrections.
- `SCOPE-CONTROL`: approved TG-M12 local handoff and optional Task Contract
  capability; implementation is authorized after TG-M11 except the Issue
  adapter.

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
  `<installed-skill-root>/state/projects/<project-id>/taskgov.sqlite`. For
  normal use, `<installed-skill-root>` is the target project's
  `.agents/skills/task-governance-tool` directory.
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
- Do not install or overwrite a project or user skill directory without
  explicit user approval and destination path.
- Document that the MVP's recommended installation target is
  `<target-project>/.agents/skills/task-governance-tool`, and that user-wide
  installation is discouraged for normal governed-project use.
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
- live dashboard or service UI; the static non-server viewer is scoped only by
  TG-M7 below
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

## Approved Post-MVP Extension: TG-M7 Static Task Viewer

TG-M7 adds a user-facing, non-server Task Viewer as a generated single-file
HTML snapshot. It does not add a live dashboard, HTTP server, browser-to-SQLite
access, or browser-side mutation. Implementation was approved after the TG-M7.0
review, and all TG-M7 execution units are complete. The unit definitions below
remain the approved execution record.

All TG-M7 units are sequential in lane `VIEWER`. This feature has a compact
contract-to-renderer-to-CLI-to-release dependency chain; unrelated optional
tasks in other lanes remain selectable if this lane blocks.

### TG-M7.0 Static Task Viewer Design And Task Baseline

Kind: sequential
Lane: `VIEWER`
Depends on: TG-M6.4
Review tier: Tier 2

Write scope:

- `AGENTS.md`
- `docs/specification.md`
- `docs/design.md`
- `docs/implementation-roadmap.md`
- `plan.md`
- task-governance-tool SQLite task records

Implementation notes:

- Define the static snapshot boundary, CLI name, output path, embedded data
  contract, UI scope, privacy/security rules, and packaging behavior.
- Register bounded implementation tasks in SQLite.
- Keep TG-M7.1 blocked with an explicit user-approval reason until the reviewed
  roadmap is approved; later lane tasks remain ready but non-actionable behind
  that blocked sequential task.
- Do not update `SKILL.md`, `README.md`, or release guidance to advertise an
  unimplemented command.
- Do not implement application or CLI code in this unit.

Verification gate:

- Documentation consistency check across governing docs.
- Inspect registered TG-M7 tasks with public `taskgov` commands.
- Full offline unittest suite remains green.
- `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issues.
- The user can approve or revise the implementation units from the documented
  contract and task list.

### TG-M7.1 Viewer Snapshot Read Model

Kind: sequential
Lane: `VIEWER`
Depends on: TG-M7.0 and user approval of TG-M7 implementation
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/tasks.py`
- `task-governance-tool/scripts/task_governance_tool/storage.py`
- new `task-governance-tool/scripts/task_governance_tool/viewer.py`
- focused snapshot/repository tests
- governing docs only for necessary clarifications

Implementation notes:

- Add a dedicated repository query for every task with all `task show` fields
  and at most 10 recent events per task.
- Preserve existing `task list` and `task show` JSON contracts.
- Define snapshot version 1, one generation timestamp, project-safe metadata,
  source schema version, status counts, and deterministic task order.
- Exclude canonical repo path, DB path, tool events, and raw private runtime
  data.
- Require read-only SQLite access and no schema migration.
- Use a dedicated non-immutable, query-only SQLite read transaction and
  revalidate schema/project identity inside the snapshot transaction.

Verification gate:

- All statuses and completion commit fields project correctly.
- Events are bounded and deterministically ordered.
- Snapshot counts and timestamps are consistent.
- Existing task command contract tests and full offline suite pass.
- Tests prove snapshot reads create no task/tool events or DB writes.
- Normal reads create no SQLite sidecars; active WAL is rejected; a concurrent
  writer yields a consistent snapshot or structured failure.

Completion criteria:

- Snapshot version 1 is deterministic, privacy-bounded, and independently
  usable by the renderer.

### TG-M7.2 Bundled Static Viewer And Renderer

Kind: sequential
Lane: `VIEWER`
Depends on: TG-M7.1
Review tier: Tier 2

Write scope:

- `task-governance-tool/assets/task-viewer.template.html`
- `task-governance-tool/scripts/task_governance_tool/viewer.py`
- renderer, template-security, and UI behavior tests

Implementation notes:

- Build one responsive vanilla HTML/CSS/JavaScript application with no runtime
  dependencies or external assets.
- Encode snapshot JSON as base64 UTF-8 and replace exactly one fixed template
  placeholder.
- Render all stored task values through text-only DOM APIs.
- Add status totals, active/terminal visibility, text search, status/kind/lane/
  priority/tag filters, deterministic list order, task details, completion
  commit state, and recent events.
- Display project identity and generated timestamp without implying live data.
- Add a restrictive content security policy and no network/storage APIs.
- Use the exact documented CSP, including the explicitly accepted inline
  script/style policy for the fixed single-file template.

Verification gate:

- Template placeholder validation and base64 round-trip tests.
- HTML-shaped task text remains inert and visible as text.
- Static inspection finds no external URL, fetch/XHR, WebSocket, service
  worker, cookie, or local-storage behavior.
- Tests assert the required CSP directives and reject prohibited task-content
  DOM sinks such as `innerHTML`, `insertAdjacentHTML`, `eval`, `Function`,
  inline event attributes, and task-derived URL attributes.
- A `file://` negative test proves script/event-handler-shaped task text is
  inert and no network request is made.
- Browser smoke check through `file://` covers filters, selection, empty state,
  and task details.
- Desktop and mobile screenshots show no blank view, overlap, clipped controls,
  or unreadable content.

Completion criteria:

- The rendered single file is useful offline and safe for sanitized task text.

### TG-M7.3 `web export` CLI And Output Safety

Kind: sequential
Lane: `VIEWER`
Depends on: TG-M7.2
Review tier: Tier 2

Write scope:

- `task-governance-tool/scripts/task_governance_tool/cli.py`
- `task-governance-tool/scripts/task_governance_tool/storage.py`
- `task-governance-tool/scripts/task_governance_tool/viewer.py`
- CLI, path, atomic-write, and installed-copy tests
- `task-governance-tool/references/cli_contracts.md` after behavior exists

Implementation notes:

- Add `web export`, stable command name `web.export`, and `--output`.
- Resolve the default viewer independently of explicit `--db`:
  `<skill-root>/state/projects/<project-id>/viewer/task-viewer.html`.
- Implement explicit-output validation, default-only parent creation, atomic
  replacement, and cleanup on failure.
- Implement `--read-only` as a no-file-write preview.
- Emit the documented JSON/text fields and error codes.
- Never write SQLite, open a browser, or mutate target project source/Git state;
  explicit output inside the target project is accepted only under the
  installed skill's generated `state/` directory.

Verification gate:

- CLI help, success/error JSON envelopes, and concise text output.
- Default and explicit output-path behavior.
- Rejection of explicit target-project destinations outside installed-skill
  generated `state/`.
- Existing file replacement and simulated write-failure cleanup.
- `--read-only` creates no directories, temp files, output files, or DB rows.
- Missing DB, migration-required, and project-mismatch behavior.
- Isolated project-scoped skill copy exports without external imports.

Completion criteria:

- Repeated export safely regenerates one self-contained viewer with no server or
  database mutation.

### TG-M7.4 Skill Guidance, Packaging, And Acceptance

Kind: sequential
Lane: `VIEWER`
Depends on: TG-M7.3
Review tier: Tier 2

Write scope:

- `task-governance-tool/SKILL.md`
- `task-governance-tool/agents/openai.yaml`
- `task-governance-tool/references/task_workflow.md`
- `task-governance-tool/references/cli_contracts.md`
- `README.md`
- `docs/release-install.md`
- `.github/workflows/ci.yml`
- packaging/self-containment tests and forward-test notes

Implementation notes:

- Advertise static task viewing only after the command is implemented.
- Explain snapshot freshness, default location, explicit-output approval, and
  the absence of browser edit/live-refresh behavior.
- Include `assets/task-viewer.template.html` in the installable release unit.
- Keep generated viewer snapshots under `state/` and out of source/release
  artifacts.
- Forward-test a realistic request to export and inspect task state using a
  fresh sub-agent context.

Verification gate:

- Skill metadata/reference self-check and isolated installed-skill export.
- Full offline test suite and CI artifact guard.
- Representative browser checks at desktop and mobile viewports.
- Forward-test confirms a fresh agent can select the correct command and
  explain snapshot freshness without inventing a server or write capability.
- `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issues.
- The static viewer satisfies all acceptance criteria in
  `docs/specification.md` and its generated output remains untracked.

## Implemented Post-MVP Extension: TG-M8 Governance Hardening

TG-M8 converts the current record-and-guidance behavior into deterministic
enforcement at the highest-risk boundaries while preserving the simple lane
model. The execution units are sequential so each schema and CLI contract is
migratable and reviewable before the next depends on it. No TG-M8 unit may
create commits or otherwise mutate a governed target project's Git state.

### TG-M8.0 Hardening Contract And Task Baseline

Kind: sequential
Lane: `GOVERNANCE-HARDENING`
Depends on: TG-M7.4
Review tier: Tier 2

Intended outcome:

- Reconcile the external operational feedback with current code, tests, and
  governing documents.
- Approve only initial-done prohibition, explicit initialization, paused work,
  shared sequential transition enforcement, `task current`, structured review
  evidence, and typed completion evidence with read-only Git validation.
- Register TG-M8 work in the project-local task database without beginning
  implementation.

Write scope:

- `docs/specification.md`
- `docs/design.md`
- `docs/implementation-roadmap.md`
- `plan.md`
- ignored project-local SQLite task state

Verification gate:

- Documentation consistency and deferred-scope checks.
- Confirm implementation and Skill discovery metadata are unchanged.
- Full offline unittest suite.
- `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified documentation commit is recorded on the TG-M8.0 task.

### TG-M8.1 Explicit Initialization And Initial-Done Prohibition

Kind: sequential
Lane: `GOVERNANCE-HARDENING`
Depends on: TG-M8.0
Review tier: Tier 2

Intended outcome:

- Make `db init` the sole database create/migrate path.
- Reject `task add --status done` before any write.

Write scope:

- CLI and storage open-mode boundaries
- task-add validation
- focused initialization, migration-required, read-only, and add tests
- implementation-facing CLI contract references

Verification gate:

- Missing-DB task writes do not create a file or parent state directory.
- Old-schema task writes do not migrate or modify the database.
- Initial done fails with `initial_done_forbidden` and stores no task/event.
- Existing explicit `db init`, project identity, and read-only tests pass.
- Full offline unittest suite and `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation commit is recorded on the task.

### TG-M8.2 Paused State And Shared Sequential Guard

Kind: sequential
Lane: `GOVERNANCE-HARDENING`
Depends on: TG-M8.1
Review tier: Tier 2

Intended outcome:

- Add schema version 3 with `paused` and required `pause_reason`.
- Use one predecessor predicate for next-task selection and transitions into
  `in_progress`, `review_pending`, and `done`.

Write scope:

- schema version 3 and migration repository code
- task domain/state transitions
- shared ordering module and selection integration
- minimal Task Viewer snapshot, status filter, count, and display support for
  `paused`
- focused schema, transition, privacy, and concurrency tests

Verification gate:

- Paused/resume and blocker semantics are distinct and tested.
- A paused or otherwise incomplete predecessor blocks later same-lane direct
  transitions but not unrelated optional or other-lane work.
- Initial active/review registration, combined metadata/status edits, and
  insertion/reordering before already active/review/done work cannot bypass the
  same invariant, including under concurrent writers.
- Schema migration preserves all version-2 rows and passes SQLite integrity and
  foreign-key checks.
- Static Viewer export remains all-status and safely renders paused tasks and
  pause reasons as snapshot version 2 without changing its read-only or offline
  boundary.
- Injected migration failure leaves the version-2 database usable and restores
  `PRAGMA foreign_keys=ON` on both success and failure paths.
- Full offline unittest suite and `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation commit is recorded on the task.

### TG-M8.3 Current Task Rediscovery

Kind: sequential
Lane: `GOVERNANCE-HARDENING`
Depends on: TG-M8.2
Review tier: Tier 2

Intended outcome:

- Add read-only `task current` for `in_progress`, `review_pending`, `paused`,
  and `blocked` work while keeping `task next` ready-only.

Write scope:

- repository current-task read model
- CLI parser, JSON/text output, and help
- focused ordering, latest-event, limit, error, and no-write tests

Verification gate:

- JSON and text output match the documented contract and deterministic order.
- Same-second latest-event selection agrees with `task show` through the shared
  `created_at DESC, rowid DESC` tie rule.
- Missing/migration/project mismatch errors do not mutate the database.
- Database and Git state remain unchanged during successful inspection.
- Full offline unittest suite and `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation commit is recorded on the task.

### TG-M8.4 Typed Completion Evidence And Git Validation

Kind: sequential
Lane: `GOVERNANCE-HARDENING`
Depends on: TG-M8.3
Review tier: Tier 2

Intended outcome:

- Add schema version 4 with explicit `git_commit`, `external_revision`,
  `commit_not_required`, and historical `legacy_unverified` evidence.
- Validate Git commits read-only and store the canonical full object ID.

Write scope:

- schema version 4 and migration repository code
- completion evidence service and CLI options
- task output/viewer compatibility fields as required for this slice
- temporary-Git-repository and migration tests

Verification gate:

- Missing, ambiguous, and non-commit revisions fail; valid short hashes resolve
  canonically.
- Leading-hyphen/option-shaped revisions are rejected and Git invocation uses
  an explicit end-of-options boundary.
- External revisions always require reason and explicit approval; Git projects
  additionally make the non-Git durable-source choice explicit in the audit.
- Every completion evidence kind enforces its full field/legacy-projection
  matrix, clears stale fields on kind changes, and rejects conflicting options.
- Git `HEAD`, refs, index, worktree, and config are unchanged by validation.
- Version-2 completed hashes are byte-for-byte retained and labeled historical
  without retroactive failure.
- Full offline unittest suite and `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation commit is recorded on the task.

### TG-M8.5 Structured Review Receipts And Findings

Kind: sequential
Lane: `GOVERNANCE-HARDENING`
Depends on: TG-M8.4
Review tier: Tier 2

Intended outcome:

- Add schema version 5 review targets, sanitized receipts, and findings.
- Enforce tier-specific same-target receipt counts and unresolved-finding gates
  without storing review transcripts or reasoning.

Write scope:

- schema version 5 and review repository/service code
- review target, receipt, and finding CLI commands
- done-transition review gate integration
- additive bounded `task show.review_evidence` read model
- focused tier, freshness, fallback, privacy, and concurrency tests

Verification gate:

- Tier 2 requires two distinct independent PASS receipts for the same current
  target; Tier 1 and Tier 0/fallback paths match the specification.
- A target change invalidates old receipts for gate purposes without deleting
  history.
- Open high or medium findings block completion until resolved.
- Resolving high/medium findings without a newer target generation remains
  blocked; a newer target and fresh receipts can satisfy the gate.
- Receipt kind/verdict/approval/tier/rationale combinations, one-receipt-per-
  reviewer-generation, and cross-task/project receipt ownership are enforced.
- PASS-then-changes-requested in one reviewer generation is rejected and
  re-review uses a new generation.
- `task show` reports current target, deterministic gate state, bounded
  receipts/findings, and blocking counts without database or Git mutation.
- Raw logs, diffs, stack traces, secrets, transcripts, and private prompts are
  rejected in every new free-form field.
- Full offline unittest suite and `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation commit is recorded on the task.

### TG-M8.6 Migration, Skill, Viewer, And Acceptance Sync

Kind: sequential
Lane: `GOVERNANCE-HARDENING`
Depends on: TG-M8.5
Review tier: Tier 2

Intended outcome:

- Prove the complete v2-to-v5 migration and synchronize implemented CLI,
  viewer, workflow, Skill references, README, and release package.

Write scope:

- synthetic sanitized 12-task/191-event migration fixture and acceptance tests
- Task Viewer snapshot version 3 reuses the bounded `task show` review-evidence
  projection and adds completion evidence UI compatibility; paused snapshot
  version 2 support is already required by TG-M8.2
- `SKILL.md`, `agents/openai.yaml`, one-level references, README, and release
  guidance only for behavior now implemented
- final governing-document implementation status

Verification gate:

- Migration preserves task/event IDs and counts, nine historical completion
  hashes, statuses, and project identity; quick and foreign-key checks pass.
- All minimum TG-M8 acceptance criteria in `docs/specification.md` pass,
  including concurrent update, privacy, project separation, and no-write tests.
- Installed-skill self-containment, metadata/reference self-check, viewer
  security tests, full offline suite, and `git diff --check` pass.
- A realistic forward test shows a fresh Codex session can use `task current`,
  pause/resume safely, and complete only with valid evidence.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation commit is recorded on the task.
- Verification receipts, stale detection, checkpoints, event pagination, and
  parent/child/checklist features remain explicitly deferred.

## Implemented Post-MVP Extension: TG-M9 Paused Work Visibility

TG-M9 makes an unbounded population of paused work visible without turning
`task next` into a resume command or adding a workflow engine. The milestone is
schema-neutral and was delivered as a reviewed contract baseline, a count-only
slice, and a filter/warning/guidance slice after separate user approval.

### TG-M9.0 Paused Visibility Contract And Task Baseline

Kind: sequential
Lane: `PAUSED-VISIBILITY`
Depends on: TG-M8.6
Review tier: Tier 2

Intended outcome:

- Define the minimal paused-work visibility contract from operational
  feedback without changing existing CLI or Skill behavior.
- Approve an exact paused count, an advisory `task next` warning, and a bounded
  `task current --status paused` view.
- Register TG-M9 implementation tasks while keeping implementation blocked for
  separate user approval.

Write scope:

- `docs/specification.md`
- `docs/design.md`
- `docs/implementation-roadmap.md`
- `plan.md`
- ignored project-local SQLite task state

Verification gate:

- Governing-document and deferred-scope consistency checks.
- Inspect TG-M9 tasks through public `taskgov` commands and confirm TG-M9.1 is
  blocked for approval while TG-M9.2 remains ordered behind it.
- Confirm application code, schema, Skill package, README, and release guidance
  are unchanged.
- Full offline unittest suite and `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified documentation commit is recorded on TG-M9.0.
- Pagination and GitHub update checking remain explicitly deferred.

### TG-M9.1 Paused Count Projection

Kind: sequential
Lane: `PAUSED-VISIBILITY`
Depends on: TG-M9.0 and separate user approval of TG-M9 implementation
Review tier: Tier 2

Intended outcome:

- Add exact project-scoped paused counts to `db status`.

Write scope:

- `task-governance-tool/scripts/task_governance_tool/storage.py`
- `task-governance-tool/scripts/task_governance_tool/cli.py`
- focused DB-status, error-shape, and read-only tests
- governing docs only for necessary contract corrections

Implementation notes:

- Do not change the SQLite schema or migration version.
- Add `counts.paused` while retaining paused rows in `counts.active`.
- Preserve a zero-LLM-decision, read-only path.

Verification gate:

- Exact positive/zero paused counts, including a count greater than the current
  result limit.
- Existing `active`, `blocked`, `review_pending`, `done`, and
  `next_actionable` values retain their meanings.
- Missing/migration/project errors retain the additive empty count shape.
- Database/event/sidecar/Git no-write assertions and the full offline suite
  pass.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation commit is recorded on the task.

### TG-M9.2 Current Filter, Next Warning, And Guidance Sync

Kind: sequential
Lane: `PAUSED-VISIBILITY`
Depends on: TG-M9.1
Review tier: Tier 2

Intended outcome:

- Add a bounded current-work status filter, including the paused-only
  resume-rich view, and add its `task next` warning in the same verified slice.
- Synchronize Skill, workflow, CLI, user, and release guidance only after the
  behavior is implemented and verified.

Write scope:

- `task-governance-tool/scripts/task_governance_tool/tasks.py`
- `task-governance-tool/scripts/task_governance_tool/cli.py`
- focused current-task, next-warning, privacy, and CLI contract tests
- `task-governance-tool/SKILL.md`
- `task-governance-tool/agents/openai.yaml` only if display metadata needs
  synchronization
- `task-governance-tool/references/task_workflow.md`
- `task-governance-tool/references/cli_contracts.md`
- `README.md` and `docs/release-install.md`
- forward-test note and governing-doc status updates

Implementation notes:

- Accept only `in_progress`, `review_pending`, `paused`, or `blocked` for
  `task current --status`.
- Preserve the unfiltered TG-M8 query, ordering, payload, default limit `20`,
  and maximum `100`.
- Reuse the current latest-event and suggested-action projection; do not add a
  second paused-task read model.
- Return the effective selected list in `data.statuses` and reject unsupported
  values with `invalid_status` without mutation.
- Build `paused_tasks_present` only after successful current-schema and
  project-identity validation, reusing the exact paused count already returned
  by the status inspection. Keep `task.next.data` unchanged.
- Treat status inspection and candidate selection as successive advisory reads,
  not a linearizable snapshot. Do not replace the existing task inspection
  connection with the Viewer-specific snapshot/WAL policy in this milestone;
  TG-M13.1 later applies one shared rollback-journal/coherent-read policy while
  preserving the two-read advisory boundary.
- Include only the integer count and fixed `taskgov task current --status
  paused` suggestion; never serialize paused-task text into the warning.
- Describe the result as bounded. Do not add or advertise pagination,
  stale-age detection, checkpoints, automatic resume, or GitHub checks.

Verification gate:

- Every accepted filter, paused-only fields/guidance, deterministic order,
  default unfiltered compatibility, limit validation, and invalid-status errors
  are covered.
- Warning is present exactly once at positive count, absent at zero and on
  failed commands, with equivalent JSON/text guidance and documented advisory
  freshness under concurrent writers.
- Equivalent next candidates, ordering, filters, selection rules, data payload,
  exit code, and other-lane behavior remain unchanged when paused rows exist.
- Missing/migration/project mismatch and `--read-only` inspections create no
  database, event, sidecar, target-project, or Git changes.
- CLI help, compact envelope, Skill metadata/reference self-check, isolated
  installed-copy smoke test, and full offline suite pass.
- A fresh-context forward flow can follow the warning to the paused-only view
  without claiming exhaustive pagination or freshness.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- All TG-M9 acceptance criteria in `docs/specification.md` pass.
- The verified implementation/guidance commit is recorded on the task.

## Implemented Post-MVP Extension: TG-M11 Completion Integrity Corrections

TG-M11 closes the audited completion/review gaps found by a consuming project
while preserving explicit reopen, review-before-commit ordering, and the normal
two-judgment Tier 2 path. TG-M11 uses lane `COMPLETION-INTEGRITY`. The contract
and all bounded implementation units are complete.

The formal milestone uses TG-M11 to avoid reusing the title of a cancelled
provisional TG-M10 checklist task retained in SQLite history.

### TG-M11.0 Completion Integrity Contract And Task Baseline

Kind: sequential
Lane: `COMPLETION-INTEGRITY`
Depends on: TG-M8.6
Review tier: Tier 2

Intended outcome:

- Record the accepted operational corrections without adopting permanent done
  immutability or the event-latch tier-downgrade design.
- Define explicit reopen, current-generation changes-requested blocking,
  done-time Git revalidation, normalized ordering inputs, and review-before-
  commit `git_snapshot` binding.
- Register bounded implementation tasks without changing product code, schema,
  Skill behavior, README, or release guidance.

Write scope:

- `docs/specification.md`
- `docs/design.md`
- `docs/implementation-roadmap.md`
- `plan.md`
- ignored project-local SQLite task state

Verification gate:

- Governing-document consistency and explicit deferred-implementation checks.
- Inspect the registered TG-M11 tasks and confirm TG-M11.1 is blocked for
  separate implementation approval.
- Confirm application code, schema, Skill package, README, version metadata,
  and release guidance are unchanged.
- Full offline unittest suite and `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified documentation commit is recorded on TG-M11.0.
- Additional external feedback may revise the approved contract before
  TG-M11.1 is unblocked.

### TG-M11.1 Deterministic Gate And Input Corrections

Kind: sequential
Lane: `COMPLETION-INTEGRITY`
Depends on: TG-M11.0 and separate user approval of TG-M11 implementation
Review tier: Tier 2

Intended outcome:

- Block current-generation `changes_requested` receipts.
- Revalidate stored Git evidence at every done transition.
- Normalize lanes and enforce signed-64-bit explicit/automatic lane orders.

Write scope:

- completion, review, task, selection, and CLI service boundaries
- additive `task show` review-evidence count and stable error mapping
- focused gate, Git no-write, lane, overflow, concurrency, and JSON tests
- governing docs only for necessary contract corrections

Implementation notes:

- Add no SQLite schema or event-latch change in this unit.
- Count only exact current-target-generation `changes_requested` receipts.
- Reuse the canonical no-shell Git resolver and re-run it immediately before
  done completion.
- Use shared lane/order validators in add/edit/list/next.
- Do not change Skill or user/release guidance yet.

Verification gate:

- Two PASS receipts plus any current `changes_requested` still fail with
  `review_changes_requested`; a newer target plus fresh PASS succeeds.
- Deleted/unresolvable stored Git evidence fails without SQLite or Git change.
- Whitespace-shadow lane and integer overflow cases return compact structured
  errors without traceback.
- Existing selection, project identity, privacy, read-only, and full offline
  tests pass.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation commit is recorded on the task.

### TG-M11.2 Done Reopen And Review-Tier Boundary

Kind: sequential
Lane: `COMPLETION-INTEGRITY`
Depends on: TG-M11.1
Review tier: Tier 2

Intended outcome:

- Lock done tasks against every write except one explicit atomic reopen.
- Allow review-tier downgrade only before the first structured review target.

Write scope:

- task edit CLI options and lifecycle service
- shared done-owner guard for task and review writes
- tier-change validation and sanitized audit events
- focused lifecycle, ordering, privacy, concurrency, and rollback tests

Implementation notes:

- Require exact `done -> in_progress` plus `--reopen-reason` and no combined
  mutation.
- Clear current completion evidence and review target, advance generation, and
  preserve all historical events/receipts/findings.
- Apply the shared sequential guard after constructing the reopened row; never
  mutate successors automatically.
- Keep review payloads parser-required. Malformed CLI syntax need not be
  replaced by a done-owner error.
- Use generation `0`, empty target, safe current/resulting status, and one
  sanitized reason for downgrades. Do not rename task-added events or add an
  event-history latch.
- Use schema-v5 fields only in this unit. Do not reference
  `review_target_base_revision`; TG-M11.3 extends the same guards after adding
  that field.

Verification gate:

- Every valid non-reopen task/review write against done fails
  `done_task_requires_reopen` without bytes, rows, or events changing.
- Reopen is atomic, preserves history, invalidates prior completion/review
  eligibility, and requires fresh gates on re-completion.
- Reopen ordering conflicts fail without cascading task updates.
- Downgrades succeed only before target generation 1 and never after review or
  reopen; upgrades retain normal behavior.
- Privacy, compact JSON/text errors, concurrent writers, and full suite pass.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation commit is recorded on the task.

### TG-M11.3 Schema Version 6 And Git Snapshot Service

Kind: sequential
Lane: `COMPLETION-INTEGRITY`
Depends on: TG-M11.2
Review tier: Tier 2

Intended outcome:

- Add schema version 6 target-base storage and internal `git_snapshot`
  capture/comparison primitives.
- Keep the public target surface unchanged until completion binding can ship in
  the same later unit.

Write scope:

- schema/migration and repository layer
- completion/review Git snapshot service
- target/receipt read and write models
- schema-v6 Viewer compatibility and unchanged snapshot allow-list tests
- migration, byte-path, Git topology, privacy, and no-mutation tests

Implementation notes:

- Add `review_target_base_revision` to tasks and
  `target_base_revision` to receipts.
- Rebuild the constrained receipt table transactionally to add
  `git_snapshot`, preserving receipt IDs and finding references.
- Extend the reopen and review-tier guards to clear or validate the new target
  base field only after the migration succeeds.
- Parse `git ls-files --stage -z` and recursive tree output as bytes.
- Fingerprint the versioned base/mode/object/path manifest.
- Reject unborn HEAD, unmerged index, root completion, and merge completion in
  the first version.
- Keep `git_snapshot` unavailable through the public review-target CLI and omit
  its new target-base output until TG-M11.4 activates target creation and done
  binding together.
- Never run `git write-tree`, hooks, checkout, commit, reset, add, or another
  target-project mutation.

Verification gate:

- v2-to-v6 and v5-to-v6 migrations are repeatable and rollback-safe and retain
  12 tasks, 191 events, nine historical hashes, all review evidence, and
  project identity.
- Snapshot capture changes only the authorized SQLite target/event rows and
  leaves Git refs, object inventory, index, worktree, config, and hooks
  unchanged.
- Equivalent index/commit trees match; wrong base, added/removed/changed/
  renamed/mode-changed/hook-changed content and merges fail deterministically.
- Raw and unusual Git path bytes are canonicalized without storing paths.
- Normal and read-only Viewer export work at schema version 6, report actual
  source schema 6, preserve snapshot version 3, and expose no target-base data.
- Quick check, foreign-key check, concurrency, privacy, and full suite pass.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified schema/service commit is recorded on the task.

### TG-M11.4 Completion Binding, Skill Sync, And Acceptance

Kind: sequential
Lane: `COMPLETION-INTEGRITY`
Depends on: TG-M11.3
Review tier: Tier 2

Intended outcome:

- Activate the public `git_snapshot` review target only together with exact
  completion-to-review binding for Git, snapshot, external, and
  commit-not-required paths.
- Synchronize implemented CLI, Skill, workflow, README, release/version, and
  Viewer-compatible read models only after acceptance passes.

Write scope:

- review-target activation, done-gate binding integration, and stable CLI
  contracts
- `task show`, Viewer snapshot/read-model compatibility as required
- `task-governance-tool/SKILL.md`
- `task-governance-tool/agents/openai.yaml` only if display metadata changes
- one-level Skill workflow and CLI references
- README, release/install guidance, version metadata, forward-test note, and
  governing status text
- focused binding and full TG-M11 acceptance tests

Implementation notes:

- Enable public `git_snapshot` target creation and its target-base projection
  in the same change that enables its done-gate binding; no released
  intermediate unit may create an uncompletable target.
- Accept identical Git commit target/evidence or a single-parent commit that
  matches the current `git_snapshot` base and fingerprint.
- Preserve exact external revision matching and diff-target
  commit-not-required semantics.
- Keep the normal Tier 2 path at two judgments; deterministic binding must not
  trigger another LLM review.
- Do not advertise root/merge snapshot support or another deferred feature.

Verification gate:

- Every TG-M11 acceptance criterion in `docs/specification.md` passes.
- Installed-skill self-containment, metadata/reference consistency, compact
  JSON/text contracts, Viewer compatibility, privacy, read-only, project
  separation, migration, and full offline tests pass.
- A realistic forward flow reviews a staged snapshot twice, creates the commit
  through the project workflow, and completes without extra review; a changed
  commit fails binding.
- `git diff --check` and the appropriate version/release checks pass.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified implementation/guidance commit is recorded on the task.
- Remaining external feedback is either incorporated into a separately
  approved unit or explicitly deferred with rationale.

## Approved Post-MVP Extension: TG-M12 Scope Control And Local Handoff

TG-M12 provides one deterministic escape path for out-of-scope discoveries and
one optional authority-copied Task Contract. It preserves acceptance-driven
completion, lane-local stopping, and the existing simple loop. The base units
do not depend on an Issue Skill. TG-M12 uses lane `SCOPE-CONTROL`; optional
advisory units require their own later approval and never block that lane.

### TG-M12.0 Scope-Control Contract And Task Baseline

Kind: sequential
Lane: `SCOPE-CONTROL`
Depends on: TG-M8.6; coordinated with the TG-M11.0 feedback closeout
Review tier: Tier 2

Intended outcome:

- Classify the operational feedback into Task core, optional advisory, future
  Issue integration, and explicitly deferred work.
- Define continue-first scope handling, deterministic Contract activation,
  durable local handoff, pending rediscovery, adapter boundaries, migration
  order, and zero-additional-judgment guarantees.
- Correct TG-M11's schema-neutral/schema-v6 ordering description.
- Register bounded future units without changing product code or advertised
  Skill behavior.

Write scope:

- `docs/specification.md`
- `docs/design.md`
- `docs/implementation-roadmap.md`
- `plan.md`
- ignored project-local SQLite task state

Verification gate:

- Governing-document consistency, judgment-budget, stop-condition, and
  responsibility-boundary checks.
- Inspect registered TG-M12 tasks and confirm TG-M12.1 is blocked for separate
  implementation approval.
- Confirm code, schema, `SKILL.md`, installed references, README, release
  metadata, Viewer code, and version remain unchanged.
- Full offline unittest suite and `git diff --check`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue,
  including no v0.2.0 downgrade or hidden LLM-decision increase.
- The verified documentation commit is recorded on TG-M12.0.

### TG-M12.1 Schema Version 7 And Local Handoff Commands

Kind: sequential
Lane: `SCOPE-CONTROL`
Depends on: TG-M11.4 and separate user approval of TG-M12 implementation
Review tier: Tier 2
Status: complete at v0.4.0/schema v7

Intended outcome:

- Add a durable Task-DB-local handoff outbox that works without Issue Skill.
- Add stable local record/list/show/withdraw surfaces, exact pending counts,
  source-task summary, and exact-replay idempotency.

Write scope:

- schema version 7 migration and handoff repository/service
- handoff CLI parser, compact JSON/text envelopes, and error mapping
- additive `db status` and `task show` projections
- `task-governance-tool/SKILL.md`, one-level workflow/CLI references, and
  README/release guidance for the implemented local-only handoff
- synchronized version `0.4.0` metadata for the public command/schema addition
- focused migration, privacy, idempotency, concurrency, and no-task-mutation
  tests
- governing docs only for necessary contract corrections

Implementation notes:

- Store only the three approved states and bounded delivery bookkeeping.
- Commit locally before any receiver attempt; return `ok=true` only after that
  commit.
- Exact payload replay returns one row. A distinct occurrence requires a stable
  explicit occurrence ID; do not implement semantic duplicate detection.
- Store `source_contract_revision=0` in v7 and include it in the idempotency
  identity so v8 can later capture the current pointer without rewriting rows.
- Keep the adapter disabled and `sync_due=false` in this unit.
- Do not append task events or update source-task timestamps.
- Default list to pending-only, oldest-first, return exact `total_matching`,
  and use `db status.counts.handoff_pending` as the project population signal;
  do not add paging.
- Permit withdraw only before any claim/delivery attempt. Claim/sync code does
  not ship in this unit.
- Fix all new field limits, stable command/data/error/warning shapes, and the
  `durable/created/replayed/handoff_id` local write receipt.
- Do not change Task selection or completion gates.

Verification gate:

- Missing/migration/read-only/project-mismatch paths create no row, file,
  sidecar, task event, task timestamp, or target-project change.
- Local commit failure is an error and never appears durable; successful exact
  replay is idempotent. Recovery and the single execution-unit stop are
  explicit and do not mutate Task status automatically.
- Pending-to-withdrawn is user-explicit and race-safe in the adapter-disabled
  state.
- Privacy rejection covers every stored free-form field and no Issue
  priority/lifecycle/resulting-task field exists.
- v5-to-v6-to-v7 and v6-to-v7 migrations preserve the 12-task/191-event
  fixture, nine completion hashes, review evidence, project identity, and pass
  rollback, quick, and foreign-key checks.
- Existing current/next/done, compact envelopes, Viewer snapshot v3, and full
  offline tests pass.
- A fresh-context forward flow records an out-of-scope discovery, continues,
  completes the source task with a pending handoff, and rediscovers the oldest
  pending row and exact count without an Issue Skill or extra question.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified schema/CLI/Skill commit is recorded on the task.

### TG-M12.2 Schema Version 8 Task Contract

Kind: sequential
Lane: `SCOPE-CONTROL`
Depends on: TG-M12.1
Review tier: Tier 2
Status: complete at v0.5.0/schema v8

Intended outcome:

- Add an optional immutable Task Contract that copies explicit authority and
  reduces scope reinterpretation without adding a task-start question.
- Invalidate stale completion/review eligibility only when semantic authority
  changes after review has started.

Write scope:

- schema version 8 migration and Contract repository/service
- optional task add/edit inputs and conditional additive write receipts
- lifecycle invalidation and additive `task show.contract`
- Skill/workflow/CLI guidance for deterministic activation and revision rules
- focused authority, migration, lifecycle, JSON, privacy, concurrency, and
  legacy-compatibility tests

Implementation notes:

- Keep purpose in title/description and existing acceptance authority for
  revision-0 tasks.
- Enforce the activation matrix: approved statuses on add, or revision-0
  `ready|blocked -> in_progress` with empty evidence/target/generation and no
  other caller mutation. Other revision-0 tasks stay revision 0.
- Treat later revisions as Contract-only caller edits in approved active
  statuses; done requires reopen and cancelled rejects.
- Permit later revision only from explicit user or later independent authority;
  current-task document edits cannot self-authorize expansion.
- Require an actual scope, acceptance, or constraints change. Treat canonical
  equality as `recorded=false` regardless of authority/reason replay and
  perform no evidence, target, status, timestamp, or event write.
- Use the documented five `--contract-*` options. Normalize only line endings
  and outer whitespace; preserve omitted later constraints and treat explicit
  empty constraints as removal. Partial groups and initial change reasons
  fail instead of being silently ignored.
- Permit same-content replay without repeated authority/reason metadata after
  validating any supplied text and same-task positive user-instruction syntax.
  An older placeholder does not block exact replay. Require both metadata
  fields and current-or-next binding only for semantic change. Do not add an
  expected-revision option in this unit; different valid semantic inputs
  serialize as immutable revisions, with a current-or-next user-instruction
  placeholder rebound to the locked allocated revision.
- Clear completion evidence on a semantic revision. Keep review generation 0
  when no review target has ever existed; otherwise clear target/base, advance
  generation, and return review-pending to in-progress atomically.
- Return `contract_write` only when Contract input was supplied and display the
  current Contract only under task show.
- Capture the task's current Contract revision in new handoff identities while
  preserving existing schema-v7 revision-0 rows.
- Keep Contract/outbox fields out of list/current/next and Viewer.

Verification gate:

- Revision-0 tasks and ordinary add/edit payloads keep existing behavior and
  shape.
- The complete activation/status/combination matrix is covered.
- First copy and later immutable revisions obey authority rules without raw
  prompt storage.
- Exact replay and authority-only re-label cannot force a revision or review.
- Revision failure rolls back pointer, row, task status/evidence/target,
  timestamp, and event together.
- Pre-review revision keeps generation 0; post-review semantic revision
  requires fresh review and completion evidence.
- v7 handoffs and all v5 fixture data survive v8 migration and rollback checks.
- Viewer snapshot v3 emits actual source schema 8 with unchanged allow-list in
  normal and read-only export.
- Skill self-containment, privacy, compact envelopes, concurrency, and full
  offline tests pass.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified schema/Contract/guidance commit is recorded on the task.

### TG-M12.3 Versioned Issue Adapter, Claims, And Due Sync

Kind: sequential
Lane: `SCOPE-CONTROL`
Depends on: TG-M12.2, a separately approved Issue Skill intake contract, and
explicit user approval of the integration boundary
Review tier: Tier 2
Initial status: blocked

Intended outcome:

- Deliver existing and new pending handoffs through one explicitly enabled,
  versioned local intake boundary.
- Add claim/lease, fixed bounded retry, crash reconciliation, and due sync only
  when a real receiver contract makes those paths usable.

Write scope:

- concrete local adapter and explicit project-scoped configuration
- handoff claim, acknowledgement, fixed retry, and sync services
- `handoff sync --due` and delivery-status projection
- adapter version negotiation and sanitized acknowledgement handling
- `AGENTS.md`, specification safety boundary, `SKILL.md`, one-level references,
  README, and release guidance synchronized in the same unit
- fake/real local-contract compatibility, failure, permission, privacy, crash,
  concurrency, and integration tests

Implementation notes:

- Do not start until the Issue intake contract and exact transport exist.
- Never open/init/migrate/edit the Issue DB directly and never use a shell,
  URL, network, GitHub, or arbitrary dynamic project code.
- Keep the same `handoff record` command; the agent never branches on receiver
  presence.
- Acquire one expiring claim and atomically increment attempts. Once claimed,
  a row can never be locally withdrawn, including after expiry.
- Use matching-token compare-and-swap for acknowledgement and stable
  `handoff_id` for receiver idempotency.
- Implement the fixed 60-second, 300-second, then exhausted retry contract;
  advance fixed `last_delivery_code` stages only on retryable negative
  responses rather than using the broader delivery-attempt count.
  Permanent/exhausted rows remain pending but not due, while expired uncertain
  claims remain due for acknowledgement reconciliation without advancing the
  retry stage.
- `sync --due` runs one bounded batch at a boundary, never a loop-to-empty.
- Absent/disabled adapters emit no warning. Enabled delivery failure emits only
  `handoff_delivery_pending` with fixed continue guidance.
- Store only receiver acceptance receipt; no priority, triage, resolution,
  resulting Task ID, import, or reverse synchronization.

Verification gate:

- Absent, installed-disabled, enabled-success, enabled-retryable,
  enabled-permanent, exhausted, crash-after-receive, expired-claim, and
  existing-pending-drain scenarios all use the same agent workflow.
- A delayed worker after lease expiry can never create local-withdrawn plus
  receiver-accepted state.
- Concurrent sync/ack uses one receiver item and deterministic local state.
- Exact due calculation, attempt increment, retry times/cap, permanent behavior,
  and no adapter-version reset are covered.
- Permission and version mismatches fail closed to pending without Issue or
  Task data corruption.
- Governing write boundaries, privacy, idempotency, zero extra LLM decisions,
  no-network/no-shell, full offline suite, and installed-copy tests pass.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified adapter/delivery/guidance commit is recorded on the task.

### TG-M12.O1 Informational Effort Advisory

Kind: optional
Lane: `SCOPE-ADVISORY`
Depends on: TG-M12.2, a separately approved default-off risk profile
Review tier: Tier 2
Status: complete at v0.6.0/schema v9

Intended outcome:

- Expose deterministic scale observations without adding an LLM disposition,
  user question, status transition, or completion gate.

Write scope:

- optional basis metadata and read-only repository/Git observation service
- risk-profile configuration and informational JSON/text output
- focused attribution/unknown, dirty/multi-active, metric, privacy, no-write,
  and zero-stop tests
- Skill guidance only after the default-off behavior is verified

Implementation notes:

- Always return `suggested_action=continue`.
- Never auto-handoff, ask, pause, block, fail a task because of an advisory
  result, or change acceptance. Ordinary database readiness, journal, and
  contention errors remain command errors.
- Basis capture is best-effort on the existing first in-progress write and
  cannot block start.
- Return attribution unknown for non-Git, dirty/uncertain endpoints, missing
  coverage, or possible active-task overlap.
- Do not persist acknowledgement or an LLM choice; repeated stable warnings are
  acceptable.
- The approved initial profile is the fixed installed-Skill
  `config/effort-advisory.json`, absent/disabled by default. There is no generic
  profile repository or configuration-write CLI.
- The initial metric allow-list is Git changed files/lines/modules, current
  Contract revision count, and source-task handoff count. Fixture sizing,
  retry inference, configured test execution, and generic risk analysis remain
  deferred.
- Schema v9 stores only optional basis and project/subject activity-generation
  metadata. Viewer snapshot v3 accepts source schemas 5-9 without exposing it.
- The Skill calls the read-only advisory once at an existing
  verification/review boundary only when `db status` reports it enabled.

Verification gate:

- Deterministic metrics and unknown reasons are reproducible without target Git
  or source mutation.
- No threshold alone changes Task or handoff state or creates a user-decision
  stop.
- Disabled profiles leave the simple loop and task output unchanged.
- Full offline suite and Skill compatibility checks pass.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified optional-feature commit is recorded on the task.

### TG-M12.O2 Local Package Self-Status

Kind: optional
Lane: `SCOPE-ADVISORY`
Depends on: TG-M12.0 and separate user approval
Review tier: Tier 2
Status: complete at v0.7.0/schema v9

Intended outcome:

- Report installed version and local core modification through a release
  manifest without update checking or automatic repair.

Write scope:

- versioned release manifest and read-only `self status`
- package/config/adapter boundary tests
- Skill and release guidance only after the behavior is implemented
- synchronized version `0.7.0` metadata; schema v9 and Viewer snapshot v3 stay
  unchanged

Implementation notes:

- Report only clean, modified, or unknown with bounded changed core paths.
- Exclude generated state, declared configuration, and adapter files.
- Do not contact GitHub, download, install, repair, or block task work.
- Treat manifest-declared origin as visible provenance, not authenticity.
- Keep the command outside the minimum Task loop; all results use the fixed
  `suggested_action=continue` and add no LLM decision.

Verification gate:

- Clean, modified, missing-manifest, ignored-state, and configured-adapter cases
  are deterministic and read-only.
- Installed-copy packaging, privacy, compact output, and full offline tests
  pass.
- The checked-in artifact and manifest agree exactly, and inspection creates no
  Python cache, database, Git, target-project, or network state.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified optional-feature commit is recorded on the task.

## Approved Post-MVP Extension: TG-M13 Operational Release Hardening

TG-M13 incorporates the accepted independent-review corrections without adding
Task or Issue lifecycle. The units use sequential lane `REVIEW-HARDENING`.
Current status and immutable Task Contract revisions are maintained in the
project-local Task database.

### TG-M13.1 Live SQLite Read Consistency

Kind: sequential
Lane: `REVIEW-HARDENING`
Depends on: TG-M12.O2
Review tier: Tier 2

Intended outcome:

- Replace unsafe immutable live-database reads with lock-respecting,
  response-coherent read transactions.
- Make rollback-journal mode the only supported operational database mode and
  reject persistent WAL before access without mutation.
- Promote the complete M13.1 through M13.4 contract into governing documents
  before the first runtime edit.

Write scope:

- `docs/specification.md`
- `docs/design.md`
- `docs/implementation-roadmap.md`
- `plan.md`
- `task-governance-tool/references/cli_contracts.md`
- `task-governance-tool/release-manifest.json` for mechanical digest sync only
- `task-governance-tool/scripts/task_governance_tool/storage.py`
- directly coupled read repositories/handlers in `tasks.py`, `handoffs.py`,
  `effort.py`, `viewer.py`, and `cli.py`
- focused live-read, rollback-journal, WAL-rejection, response-coherence, and
  no-write tests

Implementation notes:

- First pass the docs-only consistency sub-gate for all four M13 units; do not
  use Task DB contract text as the only authority.
- Remove `immutable=1` from every live operational database connection.
- Open `mode=ro`, enable `query_only`, and begin one explicit transaction before
  schema/project validation and related response queries.
- Reuse one connection for each internally coherent response. `task next` may
  retain only the documented committed inter-read staleness between status/
  paused-warning inspection and candidate selection.
- When the enabled advisory has a stored basis, split `task effort` into one
  validated pre-Git transaction and one validated post-Git
  activity-generation transaction; hold no read transaction across Git.
  Without a stored basis, omit the valueless second DB read. Busy/locked
  refresh and WAL state are command errors, while other bounded refresh
  failures remain attribution-unknown.
- Reject a persistent WAL header or existing WAL/SHM sidecar with
  `unsupported_journal_mode` and exact message
  `task database uses unsupported WAL journal mode` before operational reads or
  writes. Do not open, checkpoint, delete, or convert that database.
- Allow rollback-journal files to follow SQLite locking/recovery behavior.
- Map residual read-side `SQLITE_BUSY`/`SQLITE_LOCKED` to exit code 2,
  `database_busy`, and exact message
  `task database is busy; run the command again later`. M13.2 owns the
  write-side extension.
- Do not change schema, selection rules, compact payloads, normal Task
  judgments, or target-project state.

Verification gate:

- Cross-document consistency proves specification, design, roadmap, and CLI
  reference agree on M13 ordering, transaction boundaries, journal policy,
  compatibility, gates, and excluded scope before runtime edits.
- A rollback-journal writer with cache spill cannot make any inspection return
  uncommitted, rolled-back, partial, or internally mixed state; the read either
  returns one committed snapshot or the exact read-side `database_busy` error.
- Persistent-WAL header, WAL-sidecar, and SHM-sidecar fixtures fail before
  operational access and create no sidecars or conversions.
- `db status`, task list/next/current/show/effort, handoff list/show, and Viewer
  tests cover success, readiness errors, coherent related rows/counts, compact
  envelopes, privacy, and no-write behavior.
- Full offline unittest suite and `git diff --check` pass. Mechanically
  synchronize the release-manifest digest for changed packaged core so package
  `self status --read-only --json` returns `clean`; M13.3 still owns public
  release/install guidance.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified docs/runtime revision and completion evidence are recorded on
  the task.

### TG-M13.2 Short SQLite Write Transactions

Kind: sequential
Lane: `REVIEW-HARDENING`
Depends on: TG-M13.1
Review tier: Tier 2

Intended outcome:

- Finish slow Git, completion, and Effort preflight before acquiring the
  SQLite immediate write transaction.
- Revalidate the operation-specific governance basis after the lock and return
  stable sanitized contention errors.

Write scope:

- directly governing specification/design/CLI corrections if implementation
  discovery requires them
- storage and repository write-transaction helpers
- `task-governance-tool/release-manifest.json` for mechanical digest sync only
- task edit/completion, review target, Git snapshot, Effort, handoff, and event
  persistence paths
- deterministic delayed-preflight, stale-state, rollback, and contention tests

Implementation notes:

- Resolve Git commits, capture/compare Git snapshots, verify completion
  bindings, and perform Effort preflight outside `BEGIN IMMEDIATE`.
- Treat `git_snapshot` as a stable observed HEAD/stage-0 index capture, not a
  Git index lock or future-stability promise.
- After the short write lock, reread schema/project identity and every relevant
  task status/order, review target/base/generation, Contract revision, and
  completion-basis component. Do not use `updated_at` alone.
- For Effort basis capture, retain starting project/subject generations and
  other-active state around the out-of-lock Git observation. Under the lock,
  discard impossible/regressed deltas and mark `other_active_at_capture` when
  non-subject activity occurred or another task was active before/after; store
  the locked post-transition generations.
- Extend M13.1's exact `database_busy` code/message to residual write-side
  `SQLITE_BUSY` and `SQLITE_LOCKED`.
- Add no `retryable` or `suggested_action` field, timeout increase, sleep,
  backoff, generic retry, or retry question. Preserve only handoff record's
  existing one fresh-transaction retry.
- Keep every successful row/event write exactly once and all existing review/
  completion gates unchanged.

Verification gate:

- Deliberately delayed Git commit resolution, snapshot capture/comparison, and
  Effort preflight permit an unrelated task or handoff write to complete.
- A relevant state change between preflight and lock returns the applicable
  existing domain conflict and stores no partial task, target, event, receipt,
  Contract, handoff, or completion state.
- Residual busy/locked failures use exactly `database_busy`, exit code 2,
  command-specific empty data, and no raw SQLite/path/timeout detail.
- Uncontended task, review, completion, Contract, handoff, and Effort flows
  preserve behavior and event counts.
- SQLite `quick_check`, `foreign_key_check`, focused tests, full offline suite,
  and diff check pass. Mechanically synchronize the release-manifest digest
  for changed packaged core so package self-status returns `clean`.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified short-transaction revision and completion evidence are recorded
  on the task.

### TG-M13.3 Release And Compatibility Hardening

Kind: sequential
Lane: `REVIEW-HARDENING`
Depends on: TG-M13.2
Review tier: Tier 2

Intended outcome:

- Correct installation, ignore, runtime, platform, Viewer, and privacy guidance
  without adding another state mode or command.
- Prove the documented project-scoped package is self-contained on the
  supported Windows Python range.

Write scope:

- root README and `.gitignore` guidance
- `docs/release-install.md` and narrowly coupled governing corrections
- packaged `SKILL.md`, workflow/CLI references, metadata, release manifest, and
  release/package guards
- `.github/workflows/ci.yml`
- install, junction, Python-version, command-inventory, Viewer schema-v8, and
  packaged privacy-workflow tests

Implementation notes:

- Document governed-project use only at a physical
  `<target-project>/.agents/skills/task-governance-tool` copy.
- Primary examples run from `<target-project>` through the relative Skill
  script. Examples that start in the Skill folder require explicit
  `--repo <target-project>`.
- Remove `$CODEX_HOME/skills`, `%USERPROFILE%\.codex\skills`, and user-wide
  `.agents/skills` operating paths from public guidance and CI.
- Mark symlink and Windows junction installs unsupported. Do not implement
  lexical state-root recovery or another install mode.
- Retain optional `--repo` because non-Git governed directories are valid; do
  not re-root to a Git root or add a Git-repository existence guard. TG-M15.4
  later permits only its ancestor-marker scan for ignore preflight.
- Recommend the narrow target-local Skill `state/` rule without requiring that
  exact pattern when effective Git ignore semantics protect the same path.
  Keep release-archive database exclusions separate.
- Explain canonical-path identity relocation limits without adding project
  UUID, relocation command, or automatic recovery.
- Document Python 3.12+ and run exact Windows 3.12/3.14 CI matrix entries.
  Linux/macOS remain unverified.
- Keep implemented command inventory synchronized, add automated Viewer
  schema-v8 coverage, and permit at most one sanitized handoff abstraction
  retry after privacy rejection.

Verification gate:

- Documentation and command-inventory consistency checks find no user-wide
  governed-project operating path or broad target-project database glob.
- Physical installed-copy smoke tests pass; symlink/junction stateful use is
  rejected or diagnosed deterministically without state mutation.
- Exact Windows Python 3.12 and 3.14 CI configuration and minimum-version
  guidance agree.
- Viewer snapshot v3 covers source schemas 5 through 9, including schema 8.
- The packaged handoff privacy recovery never stores or emits the rejected raw
  content and attempts at most one sanitized abstraction.
- Skill self-check, full offline suite, package self-status, artifact guards,
  and diff check pass.

Completion criteria:

- Two independent Tier 2 reviews find no blocking high or medium issue.
- The verified release/compatibility revision and completion evidence are
  recorded on the task.

### TG-M13.4 Integrated Regression And Release Acceptance

Kind: sequential
Lane: `REVIEW-HARDENING`
Depends on: TG-M13.3
Review tier: Tier 2

Intended outcome:

- Verify M13.1 through M13.3 together against realistic local concurrency,
  migration, Viewer, privacy, package, and no-mutation scenarios.
- Record external final-commit CI evidence only after explicit authorization.

Write scope:

- integration/forward fixtures and acceptance documentation
- only narrow corrections required by an M13 acceptance failure
- Task DB review/completion evidence
- no push, PR, workflow dispatch, or publication without explicit user
  authorization

Implementation notes:

- Do not add another feature or broaden scope during acceptance. Record
  nonblocking discoveries as local handoffs.
- Review one final revision twice. Any meaningful correction requires a new
  target and two fresh PASS receipts.
- Local acceptance may complete independently of the external release gate.
  Missing authorization blocks only CI dispatch/push/PR/publication evidence.

Verification gate:

- Full offline unittest suite has only documented skips.
- Explicit rollback-journal cache-spill and delayed Git/Effort concurrent-writer
  scenarios pass.
- Migration preservation, Viewer schemas 5-9, privacy, compact JSON, package
  manifest, project-scoped installation, Windows CI configuration, and DB/Git/
  target-project no-mutation checks pass.
- `self status --read-only --json`, `git diff --check`, and clean-scope Git
  inspection pass.
- Two independent Tier 2 reviews for the same final revision pass with no open
  high/medium finding.
- Before merge/publication, the final commit has successful GitHub CI through
  a PR or explicitly authorized workflow dispatch.

Completion criteria:

- All local gates and final-revision review evidence are recorded.
- The external release gate is recorded when authorized; its absence does not
  authorize an external action or invalidate completed local verification.

## Roadmap Completion History Through TG-M13

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

TG-M8.0 through TG-M8.6 were consumed in order after separate explicit user
approval. Completion requires the migration/acceptance gate above and never
authorizes the deferred features listed in TG-M8.6.

TG-M9.0 recorded the contract and task baseline. TG-M9.1 and TG-M9.2 were
consumed after explicit implementation approval. That approval did not
authorize current/list/event pagination, stale detection, checkpoints,
parent/child task structure, default-browser launch, or networked GitHub update
checking.

TG-M11.0 recorded the completion-integrity contract and task baseline, and
TG-M11.1 through TG-M11.4 implemented the v0.3.0/schema-v6 release. That
approval did not authorize pagination, stale detection, checkpoints,
parent/child task structure, default-browser launch, network access, or Git
writes by task-governance-tool.

TG-M12.0 records the scope-control/local-handoff contract and task baseline.
The current task-consumption request consumed TG-M12.1, TG-M12.2, TG-M12.O1,
and TG-M12.O2. TG-M12.3 remains blocked until an Issue Skill intake contract,
governing permission update, and the integration boundary are separately
approved. None of these approvals authorizes semantic Issue triage, paging,
child tasks, signed evidence, external Issue import/lifecycle sync, package
repair/update, or daily GitHub update checking.

TG-M13.1 through TG-M13.4 were approved as one sequential release-hardening
lane. This approval covers only the documented SQLite consistency,
short-transaction, project-scoped distribution, compatibility, and acceptance
corrections. It does not authorize a new schema, command, workflow engine,
Git write, external CI dispatch, PR, push, or publication. External release
actions still require explicit user authorization.

## Implemented Post-MVP Extension: TG-M14 Daily UX And Local Continuity

TG-M14 is sequential in lane `REVIEW-HARDENING`. All units are Tier 2. The
complete behavioral contract, constants, result tables, schema sequence, and
excluded scope are in the planned TG-M14 specification and design sections.
M14.1-M14.6 refresh integrity-only release-manifest inventory/hashes whenever
they change covered core files, in the same reviewed revision. M14.7 owns final
release metadata/version and active publication synchronization.
M14.2 also implements the approved development-only source-tree self-host
layout. From that point onward, this repository invokes every project-scoped
command with explicit `--repo`; before a unit raises the required schema, its
current package integrity is refreshed and `setup` migrates the existing
package-local Task DB in place. No install copy or DB transfer is part of this
lane.

### TG-M14.0 Daily UX And Local Maintenance Contract Baseline

Depends on: TG-M13.4

Intended outcome:

- Promote the approved 20-leaf surface, setup/doctor behavior, daily-flow
  bounds, and foreground maintenance design into formal planned authority.
- Resolve every implementation-facing value and ownership boundary before
  changing runtime behavior.

Write scope:

- `AGENTS.md`
- `docs/specification.md`
- `docs/design.md`
- `docs/implementation-roadmap.md`
- `plan.md`
- M14 Task DB Contracts/evidence only

Implementation notes:

- Keep every new surface explicitly planned.
- Do not change parser/help, active Skill, README usage, release files,
  manifest, runtime tests/code, schema, or target state.
- Record the user-approved 30-minute/3-generation defaults and the explicit
  setup-only bounded project-local configuration contract.
- Fix removed-command errors, doctor and setup result tables, compact/
  checkpoint/packet fields and bytes, call graph, lock/attempt/render bounds,
  fixture/write sequence, benchmark, schema sequence, and task ownership.
- Withdraw only pending handoffs already promoted into authority; retain manual
  backup and reopen-history candidates.

Verification gate:

- Cross-document tests or bounded scripts prove all constants, rows, ownership,
  staging, judgment budgets, and deferred items agree.
- The active 19-leaf parser/help, Skill, README, release, manifest, runtime
  tests, version 0.7.0, and schema v9 remain byte-for-byte outside this task's
  formal write scope.
- `git diff --check` passes.

Completion:

- Two independent reviews of one exact revision find no High/Medium issue.

### TG-M14.1 Completion Readiness And Compact Task Projection

Depends on: TG-M14.0

Write scope:

- compact current/next formatter and CLI option;
- shared completion request/validator, `task complete --check`, and thin
  completion command;
- deterministic `task show` Effort Advisory enablement routing field;
- directly coupled executable help, implementation contracts, and tests;
- final Skill routing contract tests, but no active publication surfaces.

Verification:

- exact field allow-lists and 24,576/16,384/8,192-byte caps;
- default output compatibility, short-read/Git/short-revalidation no-write
  check with no SQLite lock during Git, and stale check non-authority;
- all current completion gates and legacy task-edit completion;
- JSON-only compact mode, mandatory post-selection `task show`, and fixed
  maximum nine-call default-off/ten-call Effort-enabled governance graph;
- invalid/disabled/enabled Effort profile routing with no Git work or LLM
  choice in `task show`;
- integrity manifest refresh and `self status=clean`;
- full suite and two Tier 2 reviews.

Schema remains v9.

### TG-M14.2 Unified Setup And Read-Only Doctor

Depends on: TG-M14.1

Write scope:

- direct setup orchestration and preview;
- bounded explicit-repo source-tree self-host layout for this repository,
  without relocation or a competing install;
- doctor envelope/base rows and one coherent project read;
- removal of public self/db/`--db` parsing and raw path envelopes;
- schema-v10 one-way maintenance opt-in;
- schema-v10 bounded interval/retention with setup-only configuration;
- one validated SQLite-backup primitive;
- one shared zero-wait artifact lock held from setup backup through migration,
  without holding a SQLite writer lock while copying;
- setup initialization/migration and canonical Viewer repair;
- directly coupled help/docs/tests, not active Skill/release publication.

Verification:

- every setup and doctor base row from the specification;
- physical project scope, non-Git success, Skill-root omitted-repo rejection,
  source-package omitted-repo/collision rejection, package/ignore/Python/link
  boundaries, and no target mutation;
- layout failures are project_state `invalid_layout` while package integrity
  remains an independent clean/modified/unknown observation;
- preview no-write; migration backup precedes mutation; partial configuration
  and Viewer recovery are idempotent; every success, preview, preflight error,
  and partial failure obeys the exact setup scalar/null semantics;
- setup copies carry managed generation identity, and repeated failed
  migrations remain bounded by the effective explicit/default retention even
  before a v11 generation table exists;
- concurrent setup cannot prune the generation bound to an in-flight migration
  or revert explicitly configured policy through omitted stale values;
- v10 `applied_backup_generations` is null when no managed publication exists,
  while v1-v9 migration records the setup plan's explicit/default
  `publication_retention` beside the copy identity;
- doctor does no setup, migration, backup, render, lock, or repair; TG-M15.4
  later permits only one bounded effective-ignore Git process during scope
  preflight for a Git-candidate target;
- v1-v9 to v10 migrations preserve the realistic fixture, and Viewer snapshot
  v3 reads source schemas 5-10;
- the current source-tree Task DB migrates in place to v10 with explicit
  `--repo` and retains every M14 task/Contract/event;
- integrity manifest refresh keeps setup preflight and doctor package status
  `clean`, while public `self` returns `invalid_command`;
- full suite and two Tier 2 reviews.

### TG-M14.3 Rate-Limited Rotating SQLite Backups

Depends on: TG-M14.2

Write scope:

- schema-v11 managed-backup generation table reusing v10 policy/outcome state;
- reuse of the M14.2 primitive and opt-in;
- stored-policy due calculation with 1,800-second/three-generation defaults,
  reuse of the crash-releasing M14.2 OS lock, and bounded warnings;
- doctor backup rows and backup-only performance fixtures.

Verification:

- first due, offsets `[0,1,5,29,30,31,59,60]`, failure-stays-due, publish-then-
  prune, crash artifacts, concurrency, lock contention, and no SQLite write
  lock during copying;
- v11 deterministically seeds every retained canonical valid same-project setup
  generation (including G0 and failed-migration retry copies), rejects a
  missing v10 latest-identity match, and keeps total recognized artifacts
  within applied retention after migration and routine rotation; a lower
  configured value remains pending until a later successful publication
  freezes and applies it;
- routine v11 reconciliation tests termination after file publish, after
  row/pointer commit, and between file-before-row pruning; orphan import,
  missing/invalid target handling, no-new-publish on incomplete repair, and the
  prior-valid-set-plus-one bound are exact;
- the shared v11+ reconciler runs before both routine and setup migration
  publication; setup failure returns `setup_backup_failed`, performs no
  migration, and a lower configured retention remains unapplied until a
  successful managed publication records it;
- existing-v10 migration publishes, commits latest identity/time/outcome, then
  prunes while retaining that identity before v11 seed; restart reconciliation
  and v1-v9 row creation follow the exact specification order;
- partial/unconfigured v10 resolves migration `publication_retention` from the
  validated explicit setup value or default 3, while configured v10 freezes
  its stored pre-configuration value; canonical file metadata restores that
  value on file-only import;
- policy/configuration interleaving proves that a publication keeps its frozen
  value and a later policy transaction remains pending until the next
  successful managed publication;
- repeated setup migration failure followed by success is tested with
  retention lower than the retry count;
- explicit non-default policy/range/preservation cases and the exact eight
  task-edit note sequence;
- configured v10 migration with omitted/equal policy performs no
  `maintenance_configure`; actual policy change and every ordered-prefix
  failure use the exact alternate setup rows;
- no trigger for read-only/failure/replay/no-op/internal maintenance;
- handoff record/withdraw and every other backup-eligible write commit and
  close SQLite before copying even when not Viewer-relevant;
- one attempt per eligible mutation and no Viewer work in this unit's
  benchmark;
- small/large disabled versus backup-only `+10s` budget;
- migration backup regression, Viewer snapshot v3 source schemas 5-11,
  source self-host DB migration to v11, privacy, integrity manifest/doctor
  package clean plus public-self rejection, full suite, and two reviews.

### TG-M14.4 Optional Typed Continuation Checkpoints

Depends on: TG-M14.3

Write scope:

- schema-v12 append-only checkpoint table/repository;
- `task checkpoint` with 1,024/1,024/512/4,096/6,144-byte bounds;
- exact replay, content-free event, latest current/show projection;
- setup-owned migration and Viewer source-schema compatibility.

Verification:

- privacy, done lock, replay, atomic row/event, Contract revision, no task
  `updated_at`, fixed `checkpoint_recorded` event type/summary, no selection/
  gate change, no automatic checkpoint;
- configured v11 migration preserves omitted/equal backup policy with no
  configuration write and exact alternate setup rows;
- v11-to-v12 setup retries reconcile termination after migration-backup file
  publish, row/applied-retention commit, and file-before-row pruning without
  publishing another generation until repair completes;
- 12-task/191-event/completion trace preservation;
- source self-host DB migration from v11 to v12 with all M14 evidence retained;
- Viewer v3 reads source schemas 5-12 without checkpoint publication;
- integrity manifest refresh, doctor package `clean`, and public-self rejection;
- full suite and two reviews.

### TG-M14.5 Bounded Read-Only Review Packet

Depends on: TG-M14.4

Write scope:

- `review prepare` for all four existing review-target kinds;
- one coherent governance read, bounded shell-free Git observation where
  applicable, and one short basis revalidation read;
- fixed text/JSON packet, focus, and receipt shape;
- executable help/docs/tests and final Skill workflow contract only.

Verification:

- 100 paths, 240 bytes/path, 16,384 aggregate path bytes, 32,768 packet bytes,
  and at most ten Git subprocesses;
- safe truncation, unsafe-path error, revision-zero Contract,
  stale/foreign/missing target, non-Git path-unavailable output, privacy, and
  zero writes, including exact fixed missing/stale/path/size error messages;
- no reviewer launch or result/receipt import;
- integrity manifest refresh, doctor package `clean`, and public-self rejection;
- full suite and two reviews.

Schema remains v12.

### TG-M14.6 Synchronous Default Viewer Maintenance

Depends on: TG-M14.5

Write scope:

- schema-v13 Viewer business/render generations and bounded outcome state;
- reuse of setup opt-in, existing Viewer snapshot/renderer/path/atomic writer;
- zero-wait per-Viewer lock, initial render plus one follow-up;
- doctor Viewer rows while preserving backup rows;
- removal of public web/custom-output parsing/help;
- replacement of the pre-M14 explicit-export-only formal Viewer design;
- Viewer-only and Viewer-then-due-backup performance tests.

Verification:

- no pre-opt-in render or disable path;
- exact backup-eligible versus Viewer-relevant mutation lists; setup uses its
  direct render stage; commit/close before maintenance;
- configured v12 migration preserves omitted/equal backup policy with no
  configuration write and exact alternate setup rows;
- v12-to-v13 setup retries reconcile termination after migration-backup file
  publish, row/applied-retention commit, and file-before-row pruning without
  publishing another generation until repair completes;
- older-over-newer prevention, remaining churn due, last-good preservation,
  independent backup/Viewer failures, and fixed warnings;
- no trigger on read/failure/replay/no-op/internal writes;
- at most two renders and zero lock wait;
- Viewer-only and combined small/large `+10s` budget;
- exact eight task-edit note sequence in every comparison;
- snapshot v3 source schemas 5-13, privacy, integrity manifest/doctor package
  clean plus public-self rejection, source self-host DB migration to v13, full
  suite, two reviews.

### TG-M14.7 Integrated Usability And Continuity Acceptance

Depends on: TG-M14.6

Write scope:

- final active Skill, display metadata, README, release, manifest, workflow, and
  root/group help synchronization, including final release metadata/version
  over already current integrity hashes;
- integrated setup-to-completion fixtures and only narrow acceptance fixes.

Verification:

- exact 20 leaves; removed self/db/web/`--db` and replacement commands use the
  fixed no-write parse failure; public envelopes have no storage path;
- all doctor/setup staged rows combine exactly;
- configured v10-v13 migrations preserve omitted/equal policy without a
  configuration write and keep setup G0 inside managed retention;
- partial v10 null-policy migration, explicit/default publication retention,
  file-only recovery, and later policy-change non-retroactivity are exact;
- realistic setup, compact selection, task work, checkpoint, Review Packet,
  review evidence, completion, backup, Viewer, migration, concurrency, privacy,
  and no-target-mutation flow;
- standard nine-call default-off and ten-call Effort-enabled graphs, every hard
  byte/attempt/render/lock/configuration value, and repeated final combined
  benchmark;
- setup and routine share v11+ backup reconciliation across publish,
  row/applied-retention commit, and file-before-row prune boundaries; policy
  shrink waits for a successful managed publication;
- current default compatibility except explicitly removed surfaces/envelope
  path;
- final manifest/package metadata, doctor package `clean`, and public `self`
  `invalid_command`;
- explicit-repo source self-host tasks remain usable at v13 while ordinary
  install guidance remains `.agents/skills` only;
- full suite and two PASS reviews with no High/Medium issue.

No unit adds background execution, network, generic workflow/diagnostic
architecture, external Issue/Git write, browser launch, standalone restore/export,
relocation, search/pagination, or a mandatory LLM decision or stop.

## Approved Pre-Publication Review Corrections: TG-M15

TG-M15.1 through TG-M15.3 are the completed three-unit sequential lane approved
from the independent v0.8.0 review follow-up. TG-M15.4 is the completed,
separately approved optional correction for enclosing-Git ignore verification.
TG-M15.5 is the approved opt-in Viewer reload unit; TG-M15.6 follows it as a
separate browser-state unit. These units keep version 0.8.0, schema v13, Viewer
snapshot v3, and the 20-leaf public surface. TG-M15.1, TG-M15.2, TG-M15.4,
TG-M15.5, and TG-M15.6 are Tier 2 contract changes; the exact-SHA TG-M15.3 gate
is Tier 0 because it is a deterministic external observation with no source,
workflow, schema, state-contract, or target-project mutation. No unit
authorizes a PR, merge, tag, release, or publication.

### TG-M15.1 Setup-Owned Managed-Backup Recovery

Depends on: TG-M14.7

Intended outcome:

- Prevent explicit setup from silently creating empty task state when the
  canonical database is missing but a valid managed backup remains.
- Recover without adding a public backup, restore, storage, or admin command.

Write scope:

- setup/backup orchestration and directly coupled tests;
- current setup, release, Skill, and CLI-contract documentation;
- formal setup and backup contracts plus manifest digest synchronization.

Verification:

- read-only preview performs no write and exposes only the existing setup data
  keys plus the new `database_restore` write token;
- newest-valid same-project selection, invalid-newer fallback, zero-valid
  fail-closed behavior, orphan rollback-journal preservation, no-clobber
  publication, candidate/lock races, and temp cleanup;
- current schema and supported v9-v12 recovery through the normal migration
  path, including configured policy preservation;
- task/event/Contract/completion/review evidence and v11+ generation-pointer
  preservation using the realistic migration fixture;
- foreign, invalid, linked, and unrecognized artifacts remain unchanged;
- no command, schema, Viewer snapshot, network, target-Git, judgment, or normal
  stop expansion;
- focused and full offline tests, package doctor clean, diff check, and two
  exact-revision Tier 2 PASS reviews.

### TG-M15.2 Exact Review-Target Inspection Guidance

Depends on: TG-M15.1

Intended outcome:

- Make the existing Review Packet tell an independent reviewer how to inspect
  the exact stored target instead of ambient worktree content.
- Clarify that a trusted orchestrator records the reviewer's actual result as
  an attestation and that reviewer keys prove distinct strings, not identity.

Write scope:

- target-kind-specific fixed Review Packet focus guidance;
- formal review contract, active Skill/references, README/release guidance,
  tests, and manifest digests.

Verification:

- `git_snapshot` points only to the stage-0 index against its stored base;
  `git_commit` points only to the canonical commit and first-parent/root diff;
  non-Git kinds prohibit PASS without exact externally bound material;
- no raw diff, reviewer launch, result import, signature, authentication,
  receipt-file, new command, extra Git subprocess, or routine LLM branch;
- existing packet keys, 32,768-byte cap, no-write/stale/privacy behavior,
  20 leaves, and nine/ten-call graphs remain;
- focused and full offline tests, Skill/package validation, diff check, and two
  exact-revision Tier 2 PASS reviews.

### TG-M15.3 Exact-SHA GitHub Actions Release Gate

Depends on: TG-M15.2

Intended outcome:

- Dispatch the existing CI workflow after the final feature tip is committed
  and pushed, then mechanically bind the run to that exact full commit SHA.

Write scope:

- bounded Task DB evidence only; no repository or workflow edit is planned.

Verification:

- local `HEAD` equals the pushed upstream feature tip before dispatch;
- the run event is `workflow_dispatch`, `headSha` equals that exact commit, and
  the Windows Python 3.12/3.14 matrix jobs both succeed;
- the Task note stores only the full SHA, run ID/URL, event, branch, and fixed
  conclusions, never raw logs;
- authentication or CI failure leaves the task incomplete and does not
  authorize a bypass or unrelated correction.

### TG-M15.4 Enclosing Git Ignore Verification

Kind: optional
Lane: `TG-M15-GIT-SAFETY`
Review tier: Tier 2
Depends on: completed TG-M15.1 through TG-M15.3 source baseline
Status: implemented

Intended outcome:

- Treat a governed target nested inside an enclosing Git worktree as
  Git-managed for the existing ignore preflight.
- Verify the canonical package state directory through Git's effective ignore
  semantics without re-rooting the governed project or changing non-Git
  support.

Write scope:

- `AGENTS.md`, specification, design, roadmap, and plan contract updates first;
- `project_scope.py` ignore inspection, `setup.py` repeat-check suppression,
  and directly coupled setup/doctor tests;
- user-facing setup/doctor guidance that describes ignore preparation,
  governed-root selection, or Git observation;
- required package manifest digests.

Ordering:

- Phase A synchronizes all five formal sources and directly coupled guidance
  before runtime edits begin.
- Phase B starts only after the Phase-A contract has no unresolved behavioral
  choice. This is an implementation ordering gate, not an additional review
  stop; the required Tier 2 gate remains two PASS reviews of the exact final
  revision.

Verification:

- no-marker physical targets remain valid without an ignore subprocess;
- nested target-local and parent rules, negation, linked worktree/gitfile, and
  submodule-local versus superproject-only rules follow effective Git results;
- only `check-ignore --quiet --no-index` exit 0 accepts the target; not ignored,
  timeout, launch failure, and other errors return the existing sanitized
  `state_ignore_required` result before writes;
- the explicit target still determines `--repo`, project identity, database,
  backup, and Viewer locations;
- subprocess execution is shell-free, two-second bounded, sanitized,
  stdin/output-free, and complete before SQLite work;
- setup, setup preview, and doctor each launch at most one ignore subprocess;
  their later setup revalidations and all ordinary Task/handoff/review commands
  launch none;
- no tracked-artifact detector, sentinel, `.gitignore` edit, new code/message,
  Git retry/cache/authentication, perfect ignore-rule TOCTOU defense, schema,
  Viewer, command, JSON, Task-loop call, LLM decision, or user-return stop;
- focused tests, full offline suite, package doctor and manifest checks,
  `git diff --check`, exact-SHA Windows Python 3.12/3.14 Actions, and two
  exact-final-revision Tier 2 PASS reviews.

### TG-M15.5 Visibility-Aware Viewer Auto-Refresh

Kind: sequential
Lane: `TG-M15-VIEWER`
Review tier: Tier 2
Depends on: completed TG-M15.4 source baseline
Status: implemented

Intended outcome:

- Let an already opened canonical static Viewer observe newly published local
  snapshots through a bounded browser reload without adding a live service,
  watcher, automatic launch, or normal Task-loop action.
- Keep automatic refresh explicitly opt-in: no canonical Viewer profile means
  no timer and manual browser refresh only.

Write scope:

- Phase A updates `AGENTS.md`, specification, design, roadmap, plan, and
  directly coupled release/user guidance before runtime changes;
- Phase B adds only a dedicated bounded Viewer-profile loader, one renderer
  interval placeholder, setup/publication integration, one visibility-aware
  template scheduler, focused tests, package-manifest/CI inventory updates,
  and forward-test evidence;
- the optional user file is exactly
  `<installed-skill-root>/config/viewer.json`, is never created or changed by
  taskgov, and is not a shipped release-manifest entry.

Ordering:

- Phase B starts only after Phase A fixes absence, schema/range/size, unsafe
  path, setup preview/failure, routine last-good, file-only, monotonic timing,
  and browser-state boundaries without contradiction.
- M15.6 cannot start until M15.5 is complete. M15.5 adds no persistence for
  filters, selection, focus, or scroll.

Verification:

- missing profile resolves to embedded interval `0`, no timer, and no
  automatic reload; a present profile accepts only exact schema 1/profile
  `visibility-refresh-v1` and integer 5-3,600 seconds;
- the 16,384-byte cap, strict UTF-8/JSON fields, duplicate/unknown/missing/
  bool/float/range failures, link/reparse/non-file/replacement failures, and
  sanitized output are deterministic;
- one profile observation is reused across both bounded renders, the template
  has exactly one snapshot and one decimal interval placeholder, and setup
  detects template/interval drift;
- invalid config produces a successful no-write setup repair preview, existing
  `setup_incomplete` for actual setup, and existing
  `viewer_refresh_failed` for routine post-commit work while preserving the
  last-good Viewer and primary mutation;
- scheduling begins only after fatal UTF-8 decode and initial render, runs only
  under `file:`, owns at most one timeout while visible, uses
  `performance.now()` and the remaining duration, and requests at most one
  same-document reload per loaded page;
- exact CSP, no-network, no-storage, text-only rendering, 20 command leaves,
  compact JSON, version 0.8.0, schema v13, Viewer snapshot v3, and nine/ten-call
  normal-loop bounds remain unchanged;
- focused and full offline tests, Skill/package validation, real `file://`
  browser forward test, `git diff --check`, exact-SHA Windows Python
  3.12/3.14 Actions, and two exact-final-revision Tier 2 PASS reviews.

### TG-M15.6 One-Shot Viewer UI State Restore

Kind: sequential
Lane: `TG-M15-VIEWER`
Review tier: Tier 2
Depends on: completed TG-M15.5
Status: complete

Intended outcome:

- Preserve only the useful, non-content Viewer UI subset across an M15.5
  automatic local-file reload.
- Consume the state once, without adding durable preferences, URL state,
  browser/database synchronization, a command, or a model decision.

Write scope:

- Phase A updates `AGENTS.md`, specification, design, roadmap, and plan before
  runtime work, replacing blanket browser-persistence wording with the one
  exact History API exception;
- Phase B changes only the bundled Viewer template, focused static/browser
  tests, directly coupled Viewer/release guidance and forward-test evidence,
  and required package-manifest digests;
- no Python runtime module, configuration, command parser/handler, SQLite
  object, snapshot field, migration, CSP value, or version metadata.

Ordering:

- Phase B starts only after Phase A fixes the exact envelope, ownership,
  non-owned-state behavior, save/consume/clear order, byte and age limits,
  field validation, navigation requirement, privacy exclusions, fallback,
  focus/selection/scroll order, and browser-managed lifetime.
- One final exact-revision Tier 2 gate covers the resulting privacy and browser
  behavior; Phase A review is an implementation-order check, not an added
  user-return stop.

Verification:

- only the due M15.5 `file:` reload attempts
  `history.replaceState(envelope, "")`, never a URL argument, new history
  entry, retry, or manual-reload save;
- non-null non-Viewer state is neither overwritten nor cleared; every owned
  state is cleared before possible restoration, and clear failure restores
  nothing;
- the exact 13-key schema-1 envelope stays at or below 4,096 UTF-8 bytes and
  excludes search/task/evidence/snapshot/option/URL/path/selector/selection/
  nested-scroll content;
- reload navigation, age 0-300,000 ms, exact keys/types/bounds, current
  lane/tag, visible selected task, and fixed focus target are all required;
- filters and selected Task ID precede one render that restores selection and
  detail, followed by fixed focus and document scroll; invalid or unavailable
  state explicitly uses default filters/selection and `(0, 0)` scroll, and a
  later reload defaults unless it creates a new envelope; if scroll fails
  after fixed focus succeeds, fallback best-effort blurs only that just-focused
  control before default rerender and scroll;
- save/clear errors do not block reload or alter the M15.5 timer; URL and
  history length, one-timeout/one-reload, visibility, fatal-decode, exact CSP,
  no-network, and no-storage invariants remain; manual scroll-restoration
  requires property-presence plus set/readback success, and unavailable/
  failure fallback is deterministic; a History-state read exception disables
  envelope capability but does not skip that manual-mode attempt on an enabled
  `file:` page;
- ignored/invalid state, serialized content, browser exception, URL/path, and
  validation details never reach console, UI, snapshot, or taskgov output;
- focused deterministic template tests, automatic `file:` browser forward
  test, full offline suite, Skill/package doctor and manifest checks,
  `git diff --check`, exact-SHA Windows Python 3.12/3.14 Actions, and two
  exact-final-revision Tier 2 PASS reviews.

## Approved Reduced Behavioral Trial: TG-M16

TG-M16 is one sequential lane. It changes no version, schema, Viewer snapshot,
public command count, setup plan, target-project mutation authority, or normal
green-path call count. M16.0 formalizes the reduced contract before M16.1 or
M16.2 changes runtime or active Skill guidance. M16.3 is cancelled and does
not block the lane.

### TG-M16.0 Reduced Loop Discipline Contract

Kind: sequential
Lane: `TG-M16`
Lane order: 10
Review tier: Tier 2
Depends on: completed TG-M15.6
Status: complete

Intended outcome:

- Promote the approved reduced Effort reconciliation, Test Repair, Scope
  Reconciliation, and behavioral-acceptance boundaries into formal authority.
- Leave current Python, tests, CLI behavior, active Skill, setup, schema,
  Viewer, target project, and release package unchanged.

Write scope:

- `AGENTS.md`, specification, design, this roadmap, and `plan.md` only;
- exact truth table and one-episode semantics for M16.1;
- session-local two-attempt, test-integrity, scope, review-remediation,
  unrelated-lane, and batched-decision rules for M16.2;
- explicit cancellation of M16.3 and bounded M16.4 package/behavior scope.

Verification:

- the five authorities distinguish the approved target contract from the
  still-M15.6 runtime without contradiction;
- no bootstrap, instruction adoption, durable counter/latch, command, schema,
  Viewer, setup, target mutation, network, or background behavior is added;
- full offline suite, package doctor, `git diff --check`, and two independent
  exact-target Tier 2 PASS reviews.

### TG-M16.1 Effort Advisory Reconciliation Routing

Kind: sequential
Lane: `TG-M16`
Lane order: 20
Review tier: Tier 2
Depends on: completed TG-M16.0
Status: complete

Intended outcome:

- Return `reconcile_scope` when and only when a valid enabled Effort
  observation has a nonempty `exceeded` list.
- Keep all other paths at `continue` and keep the signal non-blocking and
  read-only.

Write scope:

- centralized Effort action selection and directly coupled data/warning output;
- table-driven focused tests and synchronized implementation-facing docs;
- manifest digest updates only for changed covered package files.

Verification:

- absent, disabled, invalid, below-threshold, and unknown-only paths continue;
  exceeded and exceeded-plus-unknown paths reconcile;
- data and existing threshold-warning actions match when that warning is
  emitted; warning code/key/message are stable; any number of exceeded metrics
  creates one episode;
- no Task, Contract, handoff, review, completion, Git, or target write; no new
  command, profile field, metric, call, question, or stop;
- focused and full offline tests, package doctor and self-check,
  `git diff --check`, and two exact-target Tier 2 PASS reviews.

### TG-M16.2 Reduced Test Repair And Scope Guidance

Kind: sequential
Lane: `TG-M16`
Lane order: 30
Review tier: Tier 2
Depends on: completed TG-M16.1
Status: approved

Intended outcome:

- Route `reconcile_scope` and repeated test or review failure into one concise
  session-local diagnostic procedure.
- Prevent equivalent retry loops and test weakening without adding a durable
  workflow state.

Write scope:

- short durable root `AGENTS.md` invariants;
- concise Skill trigger and one failure-only
  `references/reconciliation.md` reference;
- directly coupled formal summaries, behavior fixtures, self-checks, and
  manifest hashes.

Verification:

- no third materially equivalent repair after two failures without new
  evidence; superficial relabeling is not new evidence and a materially useful
  safe diagnostic remains allowed;
- no test weakening merely for PASS and no Contract/acceptance change without
  explicit authority;
- current Task ownership requires accepted scope and current authority;
  failure alone is insufficient; existing blocker, handoff, paused, lane, and
  review gates are reused;
- blocking review evidence requires a meaningful fix, fresh target, and fresh
  receipts; two equivalent failed remediation cycles bound further repair;
- unrelated safe lanes continue and remaining decisions are batched;
- no runtime counter, latch, command, schema, automatic mutation, mandatory
  checkpoint, or target instruction edit;
- focused behavior checks, full offline suite, package doctor and self-check,
  `git diff --check`, and two exact-target Tier 2 PASS reviews.

### TG-M16.3 Atomic Versioned Bootstrap Policy Ensure

Kind: sequential
Lane: `TG-M16`
Lane order: 40
Review tier: Tier 2
Status: cancelled

The proposed setup-owned policy Task, instruction-chain inspection, and
consuming-project instruction adoption are withdrawn. Setup, schema v13,
Viewer snapshot v3, the 20 command leaves, and target-project mutation
authority remain unchanged. This cancelled row does not block M16.4.

### TG-M16.4 Package Synchronization And Behavioral Acceptance

Kind: sequential
Lane: `TG-M16`
Lane order: 50
Review tier: Tier 2
Depends on: completed TG-M16.1 and TG-M16.2; cancelled TG-M16.3
Status: approved

Intended outcome:

- Synchronize the reduced Effort and reconciliation guidance across the active
  Skill, one-level references, README, release guidance, formal status, tests,
  forward evidence, and release manifest.
- Demonstrate fresh-session behavior without bootstrap or project-instruction
  adoption.

Verification:

- fresh-session pressure covers exceeded Effort, two equivalent failures
  without test weakening, distinct diagnostic evidence, blocking review with
  fresh target/receipts, scope/blocker/handoff classification, unrelated-lane
  continuation, batched decisions, and durable state rediscovery with reset
  session-local retry counts;
- setup creates no Task and performs no instruction audit or target edit;
- explicit `--repo` and physical project-scoped safety remain documented;
- v0.8.0, schema v13, Viewer v3, 20 command leaves, nine/ten-call bounds,
  privacy, offline operation, and target-mutation boundaries remain unchanged;
- package self-check, full offline suite, `git diff --check`, and two
  exact-target Tier 2 PASS reviews.

## Roadmap Completion Criteria

The currently approved roadmap is complete when:

- all approved sequential units through TG-M14.7 and approved TG-M15 units
  through TG-M15.6 are complete;
- TG-M16.0, TG-M16.1, TG-M16.2, and TG-M16.4 are complete, with TG-M16.3
  remaining cancelled;
- every unit's documented verification and review gate has passed for its exact
  final revision;
- no valid High or Medium review finding remains unresolved;
- deferred items remain outside the active product contract; and
- publication, push, PR, or external CI actions occur only after separate
  explicit user authorization.
