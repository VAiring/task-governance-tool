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
- The installable package, CLI, migrations, tests, and formal documents already
  exist; inspect them before changing an established contract.
- Current release/runtime state, the concise completion index, and approved or
  pending execution units are maintained in
  `docs/implementation-roadmap.md`. Current decisions, open issues, and future
  candidates belong in `plan.md`; completed milestone narratives and
  superseded evidence are indexed as non-authoritative lineage by
  `docs/history/README.md`. Consult those owning documents instead of mirroring
  volatile status here.
- The workspace is Git-managed. The checked-out committed lineage determines
  which revision of the active governing documents applies locally; a default
  or legacy branch name is not authority by itself. Continue to verify Git
  state and ancestry before workflow steps because clones or copied workspaces
  may differ.
- Root `references/` and copied task-status material are external examples only;
  they are not authority or current project status.
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
- Use `plan.md` for current roadmap candidates, decisions not yet promoted to
  formal docs, and open issues. It is not the execution-status or historical
  milestone log.
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
  browser mutation control, or network synchronization. Optional same-file
  reload inside an already opened generated Viewer is presentation behavior,
  not a Skill trigger or normal-loop action.
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
- Explicit setup opt-in authorizes only the canonical bounded post-commit
  Viewer maintenance and idempotent setup repair defined in the current
  specification and design. There is no public Viewer/export command or custom
  output. Inspection commands, including `doctor`, never authorize a Viewer
  write.
- Profile detection must emit evidence, confidence, matched governing docs,
  reference-only exclusions, and an `unknown` or `needs_user_confirmation`
  state when confidence is low.
- Low-confidence profile detection must not authorize target-project mutation
  or expensive verification. It may suggest the concrete user confirmation
  needed next.
- Store SQLite schema version and migration history.
- Prefer explicit JSON contracts for machine-readable command output.
- Use stable IDs for records that cross boundaries, such as `project_id`,
  `profile_id`, `task_id`, `execution_unit_id`, `verification_run_id`, and
  `review_request_id`.
- Keep a stored project identity separate from its mutable filesystem binding.
  A path change never changes durable business-record IDs, and only the
  explicit setup confirmation flow defined by the current specification may
  advance a binding. Normal commands and `doctor` never infer or perform a
  rebind.
- Avoid abstractions broader than the implemented and tested profile and CLI
  flows.

## Product Contract Routing And Durable Agent Guardrails

- Do not duplicate release- or milestone-specific product contracts, status,
  command inventories, schemas, constants, truth tables, acceptance matrices,
  or history in this file. Product behavior belongs in `docs/specification.md`,
  implementation structure in `docs/design.md`, active execution order/status
  and the concise completion index in `docs/implementation-roadmap.md`,
  decisions or open issues in `plan.md`, and immutable non-authoritative
  lineage behind `docs/history/README.md`.
- Treat the current CLI, storage, setup/doctor, maintenance, Viewer, and output
  contracts as established. Read their exact current formal sections and
  directly coupled code and tests before changing them; do not reconstruct them
  from milestone summaries.
- Use only the current implemented Skill workflow and public CLI. Do not invent
  removed or administrative commands, compatibility aliases, or raw storage
  paths. `doctor` is read-only and not a normal Task-loop prerequisite;
  initialization, migration, maintenance opt-in, and Viewer repair require
  explicit `setup`.
- Optional same-file Viewer reload and its one-shot UI-state handoff are
  presentation-only. They authorize no Skill trigger, normal Task-loop call,
  automatic browser launch, network action, or target-project/config write.
- Approved but not-yet-implemented agent guidance is an acceptance boundary,
  not an active normal-loop instruction. Follow the current Skill until the
  owning execution unit and synchronization gate are complete; a runtime
  advisory does not itself expand authority or activate later guidance.
- Historical and audit projections never satisfy a current verification,
  review, or completion gate. Use current Task state and evidence bound to the
  current review-target generation; preserve older evidence only as history.
- `docs/history/README.md` is the sole historical-document index. Every
  document routed through it is non-authoritative even when its preserved
  wording says `current`, `approved`, or `implemented`. Every contract required
  for supported current behavior must remain in the active governing
  documents; history may preserve lineage, rationale, and old evidence but
  never fills a gap in current authority.

## SQLite And State Rules

- The runtime uses only the canonical state path under the supported physical
  package. Public `--db` is removed; storage/repository constructors and tests
  retain explicit path injection.
- Because the MVP skill is installed per governed project, generated local
  state belongs under the physical project-scoped package's `state/`
  directory. The public CLI does not expose an alternate state path.
- One shared storage resolver owns the canonical package-local database,
  backup, and Viewer targets. Feature code must not reconstruct a project ID
  or state path, and approved future layout contracts remain inactive until
  their owning resolver-activation unit is complete.
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
- A changed project path is not evidence of move, copy, or fork intent.
  Inspection may report a sanitized binding mismatch, but no path, hash,
  identity, database, artifact, or target-project write is authorized until
  the user invokes the exact explicit setup confirmation flow.
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

- Keep this file limited to durable agent behavior, safety, authority routing,
  and workflow gates. Put product behavior, implementation detail, execution
  status, and decision history in their owning documents.
- Reference governing sections instead of copying their detailed contracts
  here. Do not add milestone histories, command inventories, constants, schemas,
  truth tables, or acceptance matrices to this file.
- Prefer replacement or relocation over additive growth. Any temporary
  milestone note must identify its owner and retirement condition and must be
  removed or reduced to a durable invariant when that condition is met.
- An authority-layout transition must add and index its historical destination
  before switching affected active documents and routing, and the complete
  switch must land in one reviewed revision. Do not commit an intermediate
  state with mixed authority, an ambiguous active document, or a dangling
  governing link.
- Update this file only when durable agent behavior, safety, authority routing,
  or workflow discipline changes. Product changes still update their owning
  formal documents and directly coupled artifacts in the same task.
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
- A failed verification or current blocking review result prevents completion
  of the affected unit; it does not by itself stop safe authorized diagnosis,
  repair, or unrelated ready lanes. Never weaken a test merely to obtain PASS.
  Change a wrong test only under current governing authority; changing a Task
  Contract or acceptance still requires later explicit authority.
- When `task effort` returns `reconcile_scope`, or a test or review failure
  recurs after an attempted repair, read
  `task-governance-tool/references/reconciliation.md` before another materially
  equivalent repair. Apply the referenced session-local invariants without
  adding a normal-path command, persisted counter, or unrelated-lane stop.
- Stop only when all approved units are complete; when no safe authorized
  approved work remains because affected repair paths are exhausted, remaining
  work is blocked or dependent, a required user decision is missing, or an
  external state change is needed; or when the user changes scope or asks to
  pause.
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
