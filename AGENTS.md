# AGENTS.md

## Purpose

This workspace lives at `C:\WorkSpace\task-governance-tool`.

It is intended to develop `task-governance-tool`: a reusable, local-first Codex
skill/tooling project for high-discipline task execution across projects. The
product may include a concise Codex skill, deterministic helper scripts, a
SQLite-backed local state store, and project profile detection for governance
rules, verification gates, review gates, and task history. The previous working
project name/path `task-governance` is superseded by `task-governance-tool`.

This project is separate from `C:\WorkSpace\KuraKoma`. Files copied from
KuraKoma or other projects are references only and are not authority for this
workspace.

## Current Project Shape

- The intended repository root is `C:\WorkSpace\task-governance-tool`.
- The project is at requirements/planning bootstrap. Do not assume a package
  layout, CLI, tests, or skill directory already exists.
- The workspace is Git-managed, initialized on the `main` branch. Continue to
  verify Git state before workflow steps because clones or copied workspaces may
  differ.
- Initial root-level documents:
  - `AGENTS.md`
  - `plan.md`
  - root `references/` for copied external reference material only
- The copied KuraKoma task status file, if present, is a reference example for
  quality bar, roadmap granularity, and review discipline. It is not this
  project's current task status.
- Future installable skill package references, such as
  `task-governance-tool/references/` inside a skill folder, are distinct from
  root copied references. Skill package references are governed by `SKILL.md`;
  root copied references are examples only.

## Environment Rules

- The workspace is Windows-based. Prefer PowerShell-native commands and Windows
  path conventions.
- For Japanese text files, set PowerShell output encoding before reading when
  needed:
  `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`.
- Prefer `rg` / `rg --files` for search. Fall back only when unavailable.
- Use `apply_patch` for manual file edits.
- Do not use destructive git or filesystem commands unless the user explicitly
  asks for them.
- Do not assume Node.js, Python packages, or Codex skill helper scripts are
  installed unless the workspace or governing docs show they are available.
- Do not run `pip install`, `git clone`, network downloads, or external setup as
  a side effect of ordinary planning, inspection, or skill use.

## Source Of Truth

Before design or implementation work, read and follow:

1. `AGENTS.md`
2. `docs/specification.md`
3. `docs/design.md`
4. `docs/implementation-roadmap.md`
5. `plan.md`
6. directly coupled code, config, schemas, tests, examples, and fixtures
7. files under root `references/` as reference only

If these documents conflict:

- Follow `AGENTS.md` for agent behavior, safety, and workflow discipline.
- For product behavior, prefer `docs/specification.md`.
- For implementation structure, prefer `docs/design.md`.
- For implementation order, execution-unit boundaries, verification gates, and
  review gates, prefer `docs/implementation-roadmap.md`.
- Use `plan.md` for roadmap candidates, decisions not yet promoted to formal
  docs, and open issues.
- If a required product decision is missing, record it as an open issue in
  `plan.md` or ask the user before hard-coding behavior.

## Reread Rule

- Re-read `AGENTS.md` at the start of each new task.
- Re-read `AGENTS.md` at the start of every milestone and execution unit before
  planning, editing, verification, or review.
- Re-read `docs/specification.md`, `docs/design.md`,
  `docs/implementation-roadmap.md`, and `plan.md` before
  implementation-affecting decisions.
- If remembered context and current documents differ, follow the current
  documents.

## Product Baseline

`task-governance-tool` should help Codex and the user run complex projects with
clear task boundaries, repeatable checks, and auditable progress.

The expected product direction is:

- a Codex skill named something like `task-governance-tool` or
  `task-governance`
- a small deterministic CLI/script layer for detection, status, task planning,
  verification recording, and review-template generation
- a local SQLite database for cache, indexes, project profiles, execution
  history, and verification summaries
- project-specific profiles that point to governing docs and verification
  commands without copying those docs into the database as authority
- JSON/text outputs that Codex can use reliably in later turns

The product must not become:

- a hidden authority over project decisions
- a replacement for a repository's own `AGENTS.md`, specs, design docs, tests,
  or decision log
- an automatic mutator of target projects
- a network service or cloud workflow by default
- a secret store

## Skill Design Rules

- Keep the skill's `SKILL.md` concise. Put long guidance, schemas, or examples
  into one-level skill package reference files and load them only when needed.
- The skill metadata description must clearly state when the skill should
  trigger.
- For the MVP, advertise only supported task-status replacement triggers: task
  planning, task status inspection, next-task selection, blocker handling, and
  local task-state updates.
- Do not advertise deferred trigger behavior, such as verification recording,
  review request generation, or cross-project governance profile use, until that
  behavior is implemented and documented. Later versions should add those
  triggers when the corresponding features are supported.
- Prefer deterministic scripts for repeated or fragile operations:
  - detect a project profile
  - summarize current task state
  - list required governing docs
  - suggest verification gates
  - record a verification result
  - generate review request text
- The skill should be useful without network access and without external model
  calls.
- Forward-test substantial skill revisions on realistic tasks when sub-agent
  tooling is available and the user approves any risky or time-consuming
  validation.

## Skill Packaging And Validation

- Skill folder names must use lowercase letters, digits, and hyphens only, and
  should stay under 64 characters.
- The skill folder name must match the skill `name` in `SKILL.md`.
- `SKILL.md` frontmatter must contain only `name` and `description` unless a
  later approved Codex skill standard explicitly requires another field.
- The `description` must include the concrete trigger contexts for the skill,
  because this is the primary discovery surface.
- If `agents/openai.yaml` is present, keep its display metadata consistent with
  `SKILL.md`.
- Validate the skill before install/export when validation tooling is
  available. If validation tooling is unavailable, run a documented self-check
  for required files, frontmatter, naming, trigger description, and reference
  links.
- Do not install or overwrite a user or project skill directory without
  explicit user approval and a shown destination path.
- For the MVP, prefer project-scoped skill installation under a target
  project's `.agents/skills/task-governance-tool` directory. User-wide
  installation is discouraged unless the user explicitly asks for local
  experimentation outside a governed project.

## Architecture Rules

- Keep skill instructions, CLI commands, SQLite storage, project profile logic,
  and output formatting as separate boundaries.
- SQLite access must go through a storage/repository layer. Do not scatter raw
  `sqlite3` calls through feature code.
- The CLI must support dry-run or read-only behavior for inspection commands.
- Any command that writes to the task-governance-tool database must say what it
  will record. Any command that writes to a target project must require explicit
  user intent and should offer a dry-run first.
- A generated static viewer under the installed skill's ignored `state/`
  directory is permitted only when the user explicitly asks to create or
  regenerate it. Merely inspecting task state does not authorize that file
  write. The command must offer a no-write preview.
- Profile detection must emit evidence, confidence, matched governing docs,
  reference-only exclusions, and an `unknown` or `needs_user_confirmation`
  state when confidence is low.
- Low-confidence profile detection must not authorize target-project mutation
  or expensive verification. It may suggest the concrete user confirmation
  needed next.
- Store schema version and migration history from the start if SQLite is used.
- Prefer explicit JSON contracts for machine-readable command output.
- Use stable IDs for records that cross boundaries, such as `project_id`,
  `profile_id`, `task_id`, `execution_unit_id`, `verification_run_id`, and
  `review_request_id`.
- Avoid broad abstractions until the first project profile and CLI flow are
  implemented and tested.

## SQLite And State Rules

- The default task-governance-tool database path must be configurable.
- Because the MVP skill is intended to be installed per governed project, a
  reasonable default may be generated local state under the installed
  project-scoped skill folder, such as
  `.agents/skills/task-governance-tool/state/`, or an explicit user-approved
  `--db` path.
- Generated state under a project-scoped install must be ignored or otherwise
  kept out of source commits before `db init` or other write commands are used.
- User-requested generated runtime artifacts, such as a static task viewer,
  may live under the same ignored project-specific `state/` directory. They are
  local projections of SQLite state, not an additional source of truth, and
  must remain out of source commits and release artifacts.
- The SQLite database is a helper state store, not the source of truth for a
  target project's decisions.
- Store references to governing files, command names, hashes, timestamps,
  status codes, and sanitized summaries.
- Verification command output retention is deny-by-default. Store only command
  label, sanitized command summary, exit code, duration, status, timestamps,
  and optional content hash unless the user explicitly enables debug retention.
- Raw stdout/stderr, stack traces, environment dumps, and full command logs must
  not be stored by default. Debug retention must be local-only, clearly marked,
  and covered by redaction tests before it is treated as supported behavior.
- Do not store API keys, tokens, cookies, authorization headers, raw provider
  bodies, full private prompts, full chat logs, or large raw diffs by default.
- Prefer deactivation, supersession, or history rows over hard deletion for
  durable records.
- If hard deletion is added, document what is deleted, why it is safe, and what
  history is lost.

## Target Project Safety

- Inspect target projects read-only by default.
- Do not modify a target project simply because task-governance-tool inspected
  it.
- Installing the skill into a target project's `.agents/skills` directory is a
  target-project mutation and requires explicit user approval for that
  destination. After installation, ordinary `taskgov` write commands may write
  only the generated task-governance-tool database under the installed skill
  folder or an explicit `--db` path. A documented export command may also write
  a user-requested generated artifact under the installed skill's ignored
  `state/` directory. Writing an export elsewhere requires explicit user
  approval of that destination; an inspection request alone is not approval.
- Do not create commits, branches, tags, issue comments, PRs, or file edits in a
  target project unless the user explicitly approves that concrete mutation in
  the current task. Target-project governance rules may tell
  task-governance-tool what to recommend or ask for, but they do not authorize
  mutation by themselves.
- Before running target-project verification commands, inspect the project's
  documented verification expectations and avoid commands that install
  dependencies, download datasets, contact external services, or mutate runtime
  state unless the user approves that concrete command in the current task.
- Treat copied reference material as examples. Never update reference files to
  record decisions for the current task-governance-tool project.

## Documentation Maintenance

- Keep this project documentation-driven until the first implementation roadmap
  is approved.
- Record initial ideas and open issues in `plan.md`. Use formal documents such
  as `docs/implementation-roadmap.md` once they are introduced for approved
  implementation-facing roadmaps.
- If formal requirements, specification, DB design, or implementation-task
  documents are introduced later, update this file's Source Of Truth section in
  the same task.
- Keep local-only logs, scratch outputs, caches, generated databases, virtual
  environments, and test artifacts out of public commits by default.

## Task Execution Standard

- Work in bounded, coherent slices.
- Before implementing, identify the intended outcome, write scope,
  verification gate, and review gate.
- Prefer one verified slice over broad unfinished scaffolding.
- For design-affecting work, update `plan.md` or the relevant governing doc in
  the same task.
- For code work, add or update focused tests proportional to risk.
- If verification cannot be run, state the limitation and run the strongest
  relevant check available.
- Do not silently change SQLite schema, JSON contracts, CLI behavior, skill
  trigger behavior, privacy behavior, or target-project mutation semantics
  without updating governing docs in the same task.

## Roadmap Before Substantial Implementation

- Before broad implementation, create or update an implementation roadmap in
  `plan.md` or a later formal implementation-task document.
- The roadmap should define:
  - milestones or phases
  - execution units categorized as sequential or optional
  - dependencies, ordering lanes, known blockers, and which tasks may be
    consumed in any order
  - expected changed areas
  - verification gates
  - required review gates
  - completion criteria
- Present the roadmap to the user and obtain approval before consuming a
  multi-step implementation loop.
- Narrow direct edits may proceed without a full roadmap when the user asks for
  a specific edit and it does not start broad implementation.

## Task Consumption Loop Rule

- When the user approves an implementation roadmap or execution-unit set,
  continue through ready approved execution units by default.
- Sequential tasks may block their own lane or dependency chain, but they should
  not stop unrelated ready optional tasks or ready tasks in other lanes.
- Stop only when all approved units are complete, a verification gate fails, a
  valid high/medium review finding blocks completion, no approved ready work
  remains because of blockers or dependencies, a required user decision is
  missing, an external state change is needed, or the user changes scope or asks
  to pause.
- At every execution-unit boundary, re-read the governing docs required by the
  Reread Rule, declare intended outcome, write scope, verification gate, and
  review tier, then update `plan.md` or a later task-status artifact until
  SQLite-backed task status exists.
- Completion of one routine execution unit is not itself a stop condition while
  approved work remains.

## Review Standard

- Use tiered review gates.
- Every execution unit/task must declare its review tier before work begins.
- An execution unit/task is complete only after required verification passes and
  the required review gate has no blocking findings.
- `Tier 2` changes require two independent review passes when review tooling is
  available. This applies to SQLite schema/migrations, JSON contracts, CLI
  write behavior, target-project mutation behavior, privacy/logging behavior,
  skill trigger behavior, roadmap/milestone acceptance, and documentation that
  changes implementation-facing rules.
- If Tier 2 review tooling is unavailable, do not silently downgrade the gate.
  Run the strongest feasible documented self-review and ask the user before
  treating the execution unit as complete or explicitly blocked.
- `Tier 1` changes require at least one independent review or documented
  self-review when sub-agent tooling is unavailable. This applies to narrow
  scaffold changes, low-risk docs updates, fixtures, and localized tests that
  do not change contracts or product behavior.
- `Tier 0` changes may skip independent review only for purely mechanical edits
  with no behavior, schema, API, privacy, setup, persistence, or documentation
  contract risk. State the skip rationale in the closeout.
- Review findings with valid high or medium severity block completion until
  fixed or explicitly marked blocked.

### Review Request Template

Use this structure for independent reviews:

- `Task:` what was supposed to change
- `Scope:` changed files or planned write scope
- `Governing docs:` exact docs or sections to use as authority
- `Artifacts:` diff, files, or relevant excerpts
- `Verification already run:` commands or checks completed
- `Review focus:` specific concerns to judge
- `Required output:` verdict, severity-ordered findings, exact file references,
  remaining risks, and recommended changes

## Testing And Verification Rules

- Verify the narrowest meaningful slice for the current task.
- For docs-only work, verify internal consistency and agreement with `AGENTS.md`
  and `plan.md`.
- For CLI work, verify help text, dry-run/read-only behavior, JSON output shape,
  error behavior, and write behavior when applicable.
- For SQLite work, verify migrations, repository behavior, schema versioning,
  idempotent initialization, and realistic temp-database scenarios.
- For skill work, validate skill metadata and run representative task examples.
- Tests must run without network access and without real target-project
  mutation by default.

## Git Rules

- Verify whether `C:\WorkSpace\task-governance-tool` is under git control and
  inspect status before using git workflow steps.
- Do not initialize a Git repository unless the user asks.
- Do not revert user changes unless explicitly requested.
- Keep commits scoped to one coherent, verified execution unit/task.
- Do not commit generated SQLite databases, caches, logs, virtual environments,
  local secrets, copied private reference material, or generated task artifacts
  by default.

## Default Bias

When multiple options are acceptable, bias toward:

1. project docs as source of truth over hidden tool state
2. read-only inspection before mutation
3. explicit JSON contracts over ad hoc text parsing
4. SQLite repository boundaries over scattered database calls
5. local/mock-testable behavior over API-dependent behavior
6. configurable paths over hard-coded personal paths
7. sanitized summaries over raw prompts, raw diffs, or secrets
8. narrow verified slices over broad scaffolding
9. concise skill instructions with progressive disclosure
10. updating governing docs together with behavior changes
