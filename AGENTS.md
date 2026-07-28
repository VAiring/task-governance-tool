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
- The project is implemented through TG-M15.6 at v0.8.0/schema v13 with Viewer
  snapshot v3. The
  installable package, CLI, migrations, tests, and formal documents already
  exist; inspect them before changing an established contract.
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
- Installable skill package references, such as
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
  verification recording, and bounded review-packet generation
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
- Advertise only implemented triggers: task planning and state, current/next
  selection, blocker and pause handling, local handoff, bounded Review Packet
  preparation, evidence-gated completion, optional checkpoints, setup, and
  read-only diagnosis.
- Do not advertise deferred behavior such as verification-run recording,
  external Issue delivery, cross-project profiles, automatic browser launch,
  browser mutation control, or network synchronization. TG-M15.5's optional
  same-file reload is presentation behavior inside an already opened generated
  Viewer, not a Skill trigger or normal-loop action.
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
- Ordinary governed-project stateful use supports only one physical
  project-scoped copy under the target project's
  `.agents/skills/task-governance-tool` directory. The task-governance-tool
  repository itself has one bounded development-only self-host exception: an
  explicit `--repo` may use a physical package exactly at
  `<repo>/task-governance-tool` when the governing source documents and package
  manifest identify that source-tree layout and no competing project-scoped
  install exists. It reuses the source package's existing canonical state; it
  is not install guidance, relocation, or a second state mode. User-wide,
  symbolic-link, and Windows junction layouts remain unsupported.

## Architecture Rules

- Keep skill instructions, CLI commands, SQLite storage, project profile logic,
  and output formatting as separate boundaries.
- SQLite access must go through a storage/repository layer. Do not scatter raw
  `sqlite3` calls through feature code.
- The CLI must support dry-run or read-only behavior for inspection commands.
- Any command that writes to the task-governance-tool database must say what it
  will record. Any command that writes to a target project must require explicit
  user intent and should offer a dry-run first.
- Completed TG-M14 setup opt-in authorizes only the canonical bounded
  post-commit Viewer maintenance and idempotent setup repair defined below.
  There is no public Viewer/export command or custom output. Inspection
  commands, including `doctor`, never authorize a Viewer write.
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

## Implemented M14 Boundary

- M14 is implemented at v0.8.0/schema v13. M14.0 fixed the formal contracts;
  M14.1-M14.6 implemented bounded slices, and M14.7 synchronized the active
  parser, help, Skill, README, release package, manifest, workflow, and tests.
- The completed M14 public surface is exactly 20 command leaves: `setup`,
  `doctor`, task `add/list/next/current/effort/show/edit/complete/checkpoint`,
  handoff `record/list/show/withdraw`, and review `prepare`, `target set`,
  `receipt add`, `finding add`, and `finding resolve`.
- The completed M14 surface removes public `self`, `db`, `web`, `--db`, raw
  storage paths, compatibility aliases, and replacement storage, Viewer,
  export, repair, maintenance, disable, or admin commands. Internal path
  injection remains an implementation and test boundary, not an LLM choice.
- `doctor` is the sole diagnostic. It is inherently read-only, never
  fixes or prepares state, is not a normal Task-loop prerequisite, and keeps
  recognized advisory results at `suggested_action=continue`.
- `setup` is the sole public initializer, migrator, one-way local
  maintenance opt-in, and canonical Viewer repair action. It is explicit,
  noninteractive, idempotent, and limited to a physical project-scoped Skill.
- Backup and Viewer publication maintenance is bounded same-process
  post-commit work, never a daemon, thread, timer, detached process, queue,
  scheduler, or service. Setup stores the project-local backup policy with
  defaults of 30 minutes after the last successful managed backup and 3
  retained generations. Only explicit setup options may change those values;
  the normal Skill workflow supplies no policy choice. A failed attempt
  remains due for the next eligible successful mutation.
- M14 adds no mandatory doctor call, LLM judgment, question, or routine stop.
  Checkpoints are optional at genuine continuation boundaries; Review Packet
  generation replaces separate review-context reads. The already mandatory
  `task show` mechanically exposes whether the existing Effort Advisory is
  enabled; the default-off flow is bounded to 9 governance calls and an enabled
  profile to 10, with no LLM choice.
- Every M14.1-M14.6 unit that changes manifest-covered core files must refresh
  integrity inventory/hashes in the same reviewed revision. M14.7 alone owns
  final release metadata/version and active publication synchronization.

## Approved M15.5 Boundary

- TG-M15.5 adds only opt-in reload behavior to the already generated static
  Viewer. It adds no public command, setup option, normal Task-loop call,
  SQLite field, Viewer snapshot field, LLM judgment, user-return stop,
  automatic browser launch, watcher, service, network use, or direct browser
  database access.
- The sole presentation policy location is the physical project-scoped
  package's `config/viewer.json`. Taskgov never creates or edits this optional
  target-project file. When the file is absent, generated HTML disables
  automatic refresh and creates no browser refresh timer.
- A present profile is strict, UTF-8, regular, non-link/non-reparse,
  size-bounded to 16,384 bytes, and contains exactly schema version 1, profile
  `visibility-refresh-v1`, and an integer interval from 5 through 3,600
  seconds. Invalid presentation policy never changes a committed business
  result or the last good Viewer.
- Taskgov loads the profile once per Viewer publication attempt. Setup preview
  reports an invalid present profile as Viewer repair work without writing;
  actual setup uses its existing incomplete result. Routine publication uses
  the existing sanitized Viewer-failure warning. Doctor does not inspect this
  optional presentation policy.
- The rendered page may schedule at most one browser timeout only after
  snapshot decode and initial render succeed, only under `file:`, and only
  while visible. It uses monotonic elapsed time, requests at most one
  same-document reload per loaded page, and schedules no work after
  decode/render failure. Browser throttling may make refresh late but never
  early.
- M15.5 does not persist filter, selection, focus, or scroll state. That is the
  separate TG-M15.6 slice.

## Approved M15.6 Boundary

- TG-M15.6 adds one privacy-bounded, one-shot History API handoff only for the
  M15.5 automatic `file:` reload. It adds no public command, configuration,
  normal Task-loop call, LLM judgment, user-return stop, SQLite field, Viewer
  snapshot field, URL state, history entry, network use, or CSP relaxation.
- Immediately before the existing automatic reload, the Viewer may call
  `history.replaceState(state, "")` with no URL argument. It never overwrites
  or clears a non-null state that it does not own. Save failure never prevents
  the reload.
- The exact schema-1 envelope is capped at 4,096 UTF-8 bytes and contains only
  its fixed owner/time fields, status/kind/lane/priority/tag/terminal filters,
  selected Task ID, nonnegative document scroll coordinates, and a focus ID
  from the fixed Viewer-control allow-list. Search text, task content, snapshot
  content, arbitrary selectors, dynamic task-row focus, URLs/paths, and nested
  panel or text-selection state are prohibited.
- On an eligible `file:` load, the Viewer recognizes its exact owner and clears
  owned state before snapshot decoding, including invalid, non-reload, and
  fatal-decode cases. Only a reload navigation may then restore an exact state
  whose age, keys, types, bounds, current options, visible selection, and fixed
  focus target validate. If clearing fails, it restores nothing. Non-`file:`
  and non-owned state are untouched. If a later focus or scroll operation
  fails, it best-effort blurs only a fixed control that this restore just
  focused, then returns filters, selection, and scroll to their defaults.
- On an auto-refresh-enabled `file:` page, or one loading an outstanding owned
  state, the Viewer sets
  `history.scrollRestoration` to `manual` before UI restoration so browser
  reload scroll does not compete with the bounded envelope. If that capability
  is unavailable, envelope save/restore is disabled but M15.5 reload continues.
  A History-state read exception also disables envelope save/restore, but does
  not skip the manual-mode attempt on an enabled `file:` page.
- History state is browser-managed and may be session-restored; it is not
  described as memory-only. Cookies, Web Storage, IndexedDB, Cache API,
  service workers, `pushState`, URL/query/fragment state, cross-tab sync, and
  manual-reload capture remain prohibited. Interrupted automatic navigation may
  leave one bounded envelope for a later qualifying reload to consume.

## SQLite And State Rules

- The runtime uses only the canonical state path under the supported physical
  package. Public `--db` is removed; storage/repository constructors and tests
  retain explicit path injection.
- Because the MVP skill is installed per governed project, generated local
  state belongs under the physical project-scoped package's `state/`
  directory. The completed M14 CLI does not expose an alternate state path.
- Generated state under a project-scoped install must be ignored or otherwise
  kept out of source commits before `setup` or other write commands are used.
- The canonical static Viewer produced by explicit setup and opted-in bounded
  post-commit maintenance lives under that ignored project-specific `state/`
  directory. It is a local projection of SQLite state, not an additional
  source of truth, and must remain out of source commits and release artifacts.
- The optional `config/viewer.json` presentation policy is separate from
  SQLite backup policy and generated `state/`. Its absence is a valid
  disabled state; taskgov never creates it as a setup side effect.
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
  destination. Ordinary writes use only the canonical generated database, and
  setup or bounded post-commit maintenance may publish only the canonical
  Viewer under the supported package's ignored `state/` directory. Inspection
  alone never authorizes either write.
- A governed target remains the project-identity and state-ownership root when
  it is nested inside an enclosing Git worktree. When the target or an ancestor
  has a Git administrative marker, setup and doctor must use one bounded,
  shell-free effective-ignore check for the canonical package `state/`
  directory. Only an ignored result is accepted; an unignored, timed-out, or
  uninspectable result fails closed with the existing sanitized
  `state_ignore_required` contract and no target-project write. A physical
  target with no Git marker in its ancestor chain remains a valid non-Git
  project without invoking the ignore subprocess.
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
