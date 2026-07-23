# task-governance-tool MVP Specification

Status: formal implemented baseline through TG-M8 governance hardening. The
TG-M9 paused-work visibility contract is approved for implementation planning;
its CLI and Skill behavior is not yet implemented. The TG-M11
completion-integrity correction contract is approved for planning and task
registration, but its schema, CLI, and Skill behavior remain unimplemented
pending separate approval. The TG-M12 scope-control and local-handoff contract
is likewise approved for planning and task registration only; v0.2.0 behavior
remains the implemented release baseline.

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
  - `taskgov task current`
  - `taskgov task show`
  - `taskgov task edit`
  - `taskgov review target set`
  - `taskgov review receipt add`
  - `taskgov review finding add`
  - `taskgov review finding resolve`
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
  mutation. The tool may inspect Git read-only to validate an existing commit, but
  `taskgov` must not create or change Git state.
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

Project-scoped setup is a distinct, explicit step after installation. The
installer or agent must verify that generated `state/` is ignored, inspect with
`taskgov db status`, and then run `taskgov db init --repo <target-project>` with
user approval. Building the source skill package must not create a database,
because the target project identity is not known at package-build time.

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

The implemented static Task Viewer package additionally includes:

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
- `status`: `ready`, `in_progress`, `paused`, `blocked`, `review_pending`,
  `done`, or `cancelled`.
- `blocked_reason`: required when status is `blocked`.
- `pause_reason`: required when status is `paused`.
- `review_tier`: integer `0`, `1`, or `2`.
- `verification`: short verification expectation or command label.
- `tags`: comma-separated labels.
- `created_at`, `updated_at`, and optional `completed_at` timestamps.

The MVP may store task notes and state changes in a concise task event history.

## Implemented Post-MVP Extension: Completion Commit Gate

This extension made task completion auditable without adding a heavy
material-tracking schema. Its generic revision and boolean-only review details
are historical baseline behavior and are superseded for new transitions by the
implemented TG-M8 contract below.

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

## Implemented Post-MVP Extension: TG-M8 Governance Hardening

TG-M8 supersedes the simplified completion-evidence and boolean-only review
parts of the completion commit extension. It preserves the local-first SQLite
ledger, compact JSON envelope, project identity, privacy boundary, and
read-only target-project policy.

### Explicit Initialization

`taskgov db init` is the only command allowed to create or migrate a database.
All task write commands must open an already initialized database at the
current schema version. A missing database must produce
`db_not_initialized` without creating a file or parent state directory. An
older supported database must produce `migration_required` without applying a
migration. `--read-only` must continue to reject every write path before any
database or Git change.

### Initial Done Prohibition

`taskgov task add --status done` is prohibited. It must fail with
`initial_done_forbidden` before a task or event is stored. Historical import or
migration, if later required, must use a separately approved explicit command;
normal task registration must not double as an import bypass.

Initial `task add --status paused` is also prohibited with
`initial_paused_forbidden`; pausing is a transition for work already in
progress or review.

### Paused Work And Sequential Transition Enforcement

`paused` represents active work intentionally put on hold. It is distinct from
`blocked`, which means progress depends on a named blocking condition.

- Moving a task to `paused` requires a concise sanitized `pause_reason`.
- Only `in_progress` or `review_pending` work may move directly to `paused`.
- Normal resumption moves `paused` back to `in_progress`.
- `paused` tasks are excluded from `task next` and included in `task current`.
- A `paused` sequential predecessor remains incomplete and blocks later tasks
  in the same lane.
- A sequential task may enter `in_progress`, `review_pending`, or `done` only
  when every earlier task in its lane is `done` or `cancelled`.
- The rule applies to initial `task add` status as well as `task edit`.
  Initial `done` remains prohibited separately.
- An add or edit that changes kind, lane, order, or status must validate the
  complete resulting row and every affected old/new lane. It must reject an
  insertion or reorder that would place an incomplete predecessor before an
  already `in_progress`, `review_pending`, or `done` task.
- A failed transition must return `sequential_predecessor_incomplete` and must
  not change the task or append a success event.
- `task next` and task transitions must use the same predecessor-completeness
  implementation. TG-M8 does not add a general dependency graph or an
  administrative override.

### Current Task Rediscovery

TG-M8 adds the read-only command `taskgov task current`. It returns tasks in
`in_progress`, `review_pending`, `paused`, or `blocked`, including task fields,
the latest event when present, `updated_at`, and a deterministic suggested next
action. The default limit is `20`.

Default ordering is status (`in_progress`, `review_pending`, `paused`, then
`blocked`), priority (`urgent`, `high`, `normal`, `low`), newest `updated_at`,
and `task_id` for deterministic ties. `task next` remains ready-task selection;
it is not changed into a resume command.

Stale-age calculation, persistent checkpoints, and event-history pagination
are deferred. The command must not claim that `updated_at` proves repository
working-tree freshness.

### Completion Evidence Kinds And Git Validation

Every new TG-M8 completion transition must use one explicit evidence kind:

- `git_commit`: an existing commit in the target Git repository.
- `external_revision`: an existing durable revision outside the target Git
  history, with a concise sanitized reason and explicit acknowledgement.
- `commit_not_required`: an explicit statement that no managed material
  changed.

For `git_commit`, the CLI must verify the supplied revision as a commit using a
read-only Git command. A unique abbreviated hash may be accepted, but missing,
ambiguous, or non-commit revisions must fail with
`git_commit_not_found_or_ambiguous`. The canonical full object ID must be
stored. Validation must not change `HEAD`, the index, the worktree, refs, or
configuration.

`external_revision` must never be inferred from an arbitrary string. It
requires an explicit evidence kind, revision value, reason,
`--external-revision-approved`, and audit event. In a Git project the
acknowledgement specifically confirms that the external system, rather than the
available Git history, is the approved durable source of the completed
materials. Missing acknowledgement returns
`external_revision_approval_required`. `commit_not_required` stores no revision
value.

The existing `--completion-commit-hash` option remains a Git-only compatibility
alias for `git_commit`; it no longer accepts a generic revision string.
Existing schema-v2 rows and their original hashes must be retained. Migration
may label historical evidence `legacy_unverified`; it must not rewrite or
retroactively invalidate completed tasks.

### Structured Review Evidence

TG-M8 stores sanitized review receipts and findings rather than full review
text or private reasoning. Each task has a current review target identified by
`git_commit`, `diff_fingerprint`, or `external_revision` and its value. Git
targets are verified and canonicalized like completion Git evidence. Every
target-set operation also advances a monotonic target generation, including
when the kind/value returns to an earlier value.
Each receipt records a stable reviewer key, receipt kind, verdict, the exact
target, a concise summary, and timestamps. Each finding records severity,
resolution status, a concise summary, and resolution metadata.

Completion rules are:

- Tier 2 requires two `PASS` receipts from distinct independent reviewer keys
  for the same current review target.
- Tier 1 requires one independent `PASS` receipt for the current target.
- Tier 2 tooling-unavailable fallback requires a documented self-review
  `PASS` receipt and explicit user approval recorded as structured evidence.
- Tier 1 tooling-unavailable fallback requires a documented self-review
  `PASS` receipt.
- Tier 0 may use a `not_required` receipt only with a concise mechanical-change
  rationale.
- Any unresolved `high` or `medium` finding blocks completion.
- Changing or re-setting the current review target advances its generation and
  prevents every older-generation receipt from satisfying the gate, even for
  an A-to-B-to-A target sequence.
- Resolving a high or medium finding does not by itself restore completion
  eligibility. The current target generation must be greater than the
  generation of the receipt that produced the finding, and fresh required PASS
  receipts must target that newer generation. Minor record-only edits and low
  findings that do not change the target do not trigger review calls.

Reviewer keys provide mechanically checkable distinctness, not identity
proof. The agent remains responsible for using genuinely independent review
passes. `--review-complete` remains a command-time compatibility confirmation,
but cannot substitute for missing structured evidence.

The normal successful Tier 2 path therefore uses two LLM review decisions.
Recording or validating receipts, reviewer distinctness, target equality,
findings, and Git commits is deterministic and adds no LLM decision. One
finding/fix cycle normally adds two fresh final-review decisions, for four
review decisions in total; each additional meaningful fix cycle adds two.

### Deferred Feedback

TG-M8 does not add verification receipts, stale-active detection, persistent
handoff checkpoints, event-history pagination, or parent/child execution
units. Large tasks may continue to use concise task events and bounded roadmap
units. A checklist or child-task feature should be reconsidered only after
operational evidence from `paused` and `task current` shows that the simple lane
model is insufficient.

## Approved Post-MVP Extension: TG-M9 Paused Work Visibility

TG-M9 addresses the narrower risk that intentionally paused work can disappear
from the normal ready-task flow. It adds deterministic visibility to existing
read surfaces without changing paused-state semantics, sequential readiness,
or the local-first and no-target-mutation boundaries.

### Paused Count

After TG-M9 is implemented, `taskgov db status` must expose an exact
`counts.paused` value for the current project. `counts.active` remains backward
compatible: it continues to include `ready`, `in_progress`, `paused`,
`blocked`, and `review_pending`. The separate paused count is additive and must
not change any existing total.

The concise text form must label the paused count explicitly. Missing,
migration-required, and project-mismatch responses must retain the normal
command-specific empty count object, including `paused: 0`, without creating,
migrating, or modifying the database.

### Ready Selection Warning

`taskgov task next` remains a ready-task selector. Its candidate query,
filters, ordering, `data` payload, and treatment of sequential lanes do not
change. When the current project has one or more paused tasks, a successful
`task next` response must add one structured warning with code
`paused_tasks_present`. Its deterministic message must include the paused-task
count and suggest:

```text
taskgov task current --status paused
```

The text form must present the same advisory warning. The warning must not add
paused tasks to the result, suppress otherwise ready work, change the success
exit code, or write a task event, tool event, database byte, or Git state. No
paused warning is emitted when the count is zero or when `task next` itself
fails database or argument validation. The warning contains only the count and
command suggestion, not task titles, pause reasons, or event text.

The warning count is the exact result of the successful database-status
inspection that precedes candidate selection. It is an advisory observation,
not a cross-command lock: a concurrent writer may change task state immediately
after that read. TG-M9 does not change the existing `task next` connection or
WAL/sidecar policy merely to make the warning linearizable.

### Current-Status Filter

`taskgov task current` gains an optional `--status` filter. Accepted values are
the existing current-work statuses `in_progress`, `review_pending`, `paused`,
and `blocked`. Omitting the filter preserves the TG-M8 query, output,
ordering, default limit `20`, and maximum limit `100` unchanged.

`taskgov task current --status paused` returns only paused tasks using the
existing paused ordering, latest-event projection, pause reason, and
deterministic suggested next action. In JSON, `data.statuses` contains the
effective selected status list, so a paused-only response reports
`["paused"]`. An unsupported status such as `ready`, `done`, or `cancelled`
must fail with `invalid_status` and no database or Git mutation.

This is a bounded resume-rich view, not an unbounded ledger. Operators can
compare the returned page count with `db status`'s exact paused count and raise
the existing limit when useful. Cursor pagination for `task current`, richer
`task list` retrieval, and event-history pagination remain deferred; TG-M9 must
not advertise exhaustive retrieval beyond the existing limit.

### Compatibility, Cost, And Non-Goals

TG-M9 requires no SQLite schema migration and makes no data model change. It
adds one count key, may add one warning on successful `task next`, and narrows
the existing `task.current.data.statuses` list only when the caller explicitly
requests a filter. The compact top-level JSON envelope and all existing command
names remain unchanged.

Paused counting, warning construction, and status filtering are deterministic
SQLite/CLI operations and add zero LLM judgments. TG-M9 does not add stale-age
calculation, checkpoints, parent/child tasks, acceptance checklists, automatic
resume, network access, or GitHub update checking. A once-daily GitHub update
check remains an unapproved follow-up pending a separate network, cache,
failure, and privacy contract.

## Approved Post-MVP Extension: TG-M11 Completion Integrity Corrections

TG-M11 incorporates the accepted parts of the CanonWeave operational
hardening feedback without making completed tasks permanently immutable or
increasing the normal Tier 2 review path beyond two LLM judgments. The contract
is approved for planning and task registration only. Implementation requires a
separate user request.

TG-M11 preserves the existing local-first database, compact JSON envelope,
project identity, privacy boundary, explicit `db init`, task selection, paused
work, and no-target-project-mutation rules.

### Locked Done State And Explicit Reopen

A `done` task is locked, but not permanently immutable. The only accepted write
against a `done` task is an explicit reopen transition:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --reopen-reason "<sanitized reason>" --json
```

The reopen transition must be atomic and must:

- require a non-empty sanitized `reopen_reason`;
- reject every additional task mutation, completion confirmation, completion
  evidence option, or note in the same command;
- change status only from `done` to `in_progress`;
- clear `completed_at`, blocker state, and pause state;
- reset writable completion evidence to `none` and its legacy projection to
  commit-required with an empty hash;
- clear the current review target while advancing its generation so every
  earlier receipt remains historical and cannot satisfy a later completion;
- preserve all existing task events, review receipts, and findings;
- append one sanitized `task_reopened` event that identifies the previous
  completion evidence kind and revision without storing raw output; and
- apply the shared sequential-lane invariant to the resulting state.

Every other task edit or structured review write against a `done` task must
fail with stable error code `done_task_requires_reopen` and perform no write.
This includes metadata edits, notes, review-tier changes, target changes,
receipts, new findings, finding resolution, and completion-evidence changes.
A reopened task must satisfy fresh verification, review, and completion gates
before returning to `done`.

### Review-Tier Downgrade Boundary

A review-tier downgrade is allowed only before structured review begins.
Structured review begins when the first review target is set, represented
mechanically by `review_target_generation > 0`; status events are not used as
an indirect latch.

A downgrade must:

- occur while the task is `ready`, `in_progress`, `paused`, or `blocked`;
- require a concise sanitized `--review-tier-change-reason`;
- require `review_target_generation == 0` with an empty current target;
- reject combination with completion evidence, verification/review
  confirmations, or a transition into `review_pending` or `done`; and
- append a `review_tier_changed` event with the old tier, new tier, and reason.

Once any review target has been set, the tier may be raised but must never be
lowered, including after a reopen. No `task_added_review_unstarted` event latch
or event-history backfill is required. Invalid downgrades return
`review_tier_downgrade_forbidden` without mutation.

### Current-Generation Changes Requested

Any `changes_requested` receipt for the exact current review target generation
must block completion even when the tier's required PASS count is otherwise
satisfied. `task show.review_evidence.counts` must expose
`changes_requested_current_generation`. The completion path must return
`review_changes_requested`. Setting a newer target generation and obtaining
fresh qualifying review receipts is the only way to clear this condition;
historical receipts remain preserved.

### Done-Time Git Revalidation And Input Boundaries

Every transition to `done` must re-resolve stored Git completion evidence and
any Git-backed current review target immediately before the database update.
The canonical full commit ID must still equal the stored value. Missing,
ambiguous, non-commit, or no-longer-resolvable evidence fails with the existing
Git validation error and changes neither SQLite nor Git.

Lane values must be trimmed consistently by add, edit, list, and next
operations. Explicit and automatically assigned `lane_order` values must stay
within SQLite's signed 64-bit integer range. Whitespace-shadow lanes and
automatic order overflow must return structured validation errors without a
traceback.

These checks are deterministic and add no LLM judgment.

### Review-Before-Commit Git Snapshot Binding

TG-M11 retains the approved workflow in which implementation is verified and
reviewed before the completion commit is created. It adds review target kind
`git_snapshot` so the reviewed staged state can be bound deterministically to
the later Git commit without a second pair of LLM reviews.

Setting a `git_snapshot` target must:

- read the canonical current `HEAD` commit as the base revision;
- read only stage-0 entries from the Git index;
- reject an unborn `HEAD`, unmerged index, zero-object intent-to-add entry, or
  sparse-directory index entry;
- construct a versioned canonical manifest from base commit, file mode, object
  ID, and raw path bytes for every index entry;
- store a SHA-256 fingerprint and the canonical base commit;
- advance the normal review target generation; and
- leave Git `HEAD`, refs, objects, index, worktree, configuration, and hooks
  unchanged.

`review target set --kind git_snapshot` captures the current staged snapshot;
it does not accept a caller-supplied `--revision`. Unstaged and untracked
content is outside the snapshot and is not inspected or recorded.

At completion, `git_commit` evidence may satisfy review binding in either of
these ways:

- the current target is `git_commit` with the identical canonical commit ID; or
- the current target is `git_snapshot`, the completion commit has exactly one
  parent equal to the stored base revision, and the completion commit tree
  produces the identical canonical manifest fingerprint.

Merge commits and snapshot/parent/tree mismatches must fail with
`review_target_mismatch`. `external_revision` completion still requires the
identical external target. `commit_not_required` still requires an explicit
`diff_fingerprint` target. Existing target kinds and historical receipts remain
valid under their existing rules.

The normal successful Tier 2 path remains two independent LLM review
judgments. Fingerprint creation, parent/tree comparison, receipt counting, Git
resolution, and finding-state checks are deterministic.

### TG-M11 Compatibility And Acceptance

TG-M11 must not be advertised by `SKILL.md`, README, release guidance, or
installed-skill references until its implementation and migrations pass their
acceptance gates. Implementation must provide an explicit, repeatable schema
migration and preserve the sanitized 12-task/191-event fixture, nine historical
completion hashes, every existing review receipt/finding, and project identity.

Acceptance requires automated proof that:

- every non-reopen write against `done` is rejected without mutation;
- reopen atomically invalidates old completion/review eligibility while
  preserving history;
- a reopened task cannot complete without fresh evidence;
- the sequential invariant also governs reopen;
- review-tier downgrade is possible only at generation `0` with a reason;
- current-generation `changes_requested` blocks otherwise sufficient PASS;
- stored Git evidence is revalidated at the done transition;
- `git_snapshot` binds the reviewed staged state to one later single-parent
  commit and detects added, removed, changed, renamed, mode-changed, hook-
  changed, wrong-parent, and merge-commit cases;
- normal Tier 2 completion uses two review judgments, not four;
- lane normalization and integer boundaries cannot bypass ordering;
- privacy rejection, compact envelopes, read-only behavior, project
  separation, concurrency, and migration preservation remain intact; and
- no validation or inspection command changes SQLite or target-project Git
  state unless the command is an explicitly authorized task-database write.

## Approved Post-MVP Extension: TG-M12 Scope Control And Local Handoff

TG-M12 incorporates the accepted task-scope and effort-growth feedback without
turning Task Skill into an Issue tracker, workflow engine, or audit platform.
It is approved for planning and task registration only. Product code, schema,
CLI, Skill guidance, README, release metadata, and installed behavior remain at
the implemented v0.2.0/schema-v5 baseline until separately approved
implementation units pass their gates.

The extension has two core capabilities:

- an optional, authority-copied Task Contract that reduces repeated scope and
  acceptance reinterpretation; and
- a durable local handoff outbox that records out-of-scope discoveries before
  work continues, whether or not an Issue Skill is installed.

An informational Effort Advisory and local-package self-status are optional
later units. They must not make the minimum Task loop more complex.

### Responsibility And Continue-First Rule

Task Skill remains responsible for:

- preserving the task purpose through existing `title` and `description`;
- recording scope, acceptance, constraints, current state, next work, and
  blockers;
- running selection, execution, verification, state-update, review, and
  completion loops; and
- ending work when the current accepted authority is satisfied or reporting an
  existing blocker, unavailable dependency, or required user decision.

Task Skill does not own long-term Issue lifecycle, external ticket import,
Issue priority or triage, duplicate resolution, resulting-task creation,
threat-model management, project-specific test strategy, or a large evidence
and signature system. A future Issue Skill owns those concerns independently.

For each newly discovered defect, improvement, or request, the agent makes the
single classification already inherent in scoped work:

1. If resolving it is within the current accepted scope and can proceed under
   current authority, keep it in the current task.
2. If an unmet condition prevents current acceptance and cannot be resolved
   safely within the currently authorized work, record it as the current
   task's blocker.
3. Otherwise, durably record one handoff and continue the current task.

Quality improvement, possible future risk, or convenience alone must not add an
acceptance condition. The agent must not ask whether to enable a Contract,
whether an Issue Skill exists, or whether to continue merely because a handoff
or Effort Advisory exists.

Safety is an orthogonal modifier, not a fourth destination. First choose current
task, blocker, or handoff using the rules above. A credible data-loss or safety
risk must also be reported promptly. If continuing the affected task or lane is
unsafe, block only that task or lane and continue other safe ready work. A
global stop occurs only when all otherwise ready work is unsafe or an existing
user-decision rule already requires it.

Reaching current acceptance remains the normal completion condition. Pending
handoffs, advisory warnings, and later Issue resolution do not extend or block
the source task.

### Optional Task Contract

A Task Contract is a concise, immutable revision of explicit scope and
acceptance information already supplied by authority. It is per-task optional
and must never become a new question or a heuristic LLM decision.

Contract revision 1 is recorded only when both scope and acceptance are already
explicit in at least one of:

- the user's current instruction;
- an approved implementation roadmap; or
- explicit task-registration input.

The CLI copies that information during `task add`, or during an explicit
revision-0 transition from `ready` or `blocked` to `in_progress`. On add, the
resulting status must be `ready`, `in_progress`, `blocked`, or
`review_pending`. On edit, the Contract group may be combined only with that
status transition and requires empty completion evidence, target, and target
generation. It cannot be combined with notes, metadata, review-tier,
completion-evidence, or gate-confirmation changes. If either Contract field is
absent, the task remains at revision `0`; the agent must not ask for missing
fields or infer that a task is "long enough" to need them.

A revision-0 task that does not use one of those activation boundaries remains
revision 0. `paused`, `review_pending`, `done`, and `cancelled` existing tasks
cannot activate a first Contract. Revision `0` continues to use the user
request, governing documents, and existing task fields as authority. It never
means that acceptance is optional or automatically satisfied.

The Contract stores:

- concise `scope`;
- concise `acceptance`;
- optional `constraints`;
- an optional initial stable sanitized `authority_ref`; and
- for later revisions, a concise change reason and timestamp.

Purpose remains in existing `title` and `description`; no duplicate purpose
column is added. A user-instruction authority reference must identify a stable
decision without storing a raw prompt, full chat, or private reasoning.

Every later revision is append-only and requires a non-empty stable sanitized
`authority_ref` identifying either an explicit user instruction or a later
independent authority change, plus a change reason. This is structured
acknowledgement of authority, not a stored prompt. A governing document edited
as an output of the current task cannot authorize that same task to expand its
Contract. Agent-proposed hardening outside the current Contract is handed off
instead.

Later revision is a Contract-only edit allowed while the task is `ready`,
`in_progress`, `paused`, `blocked`, or `review_pending`. It rejects every
caller-supplied task metadata, note, status, review-tier, evidence, or gate
change in the same command. The service itself moves `review_pending` to
`in_progress` when invalidating review. A `done` task must be reopened first;
`cancelled` rejects Contract writes.

Supplying scope, acceptance, and constraints that canonically equal the current
Contract is an exact replay regardless of repeated authority/change metadata.
It returns `recorded=false` with the current revision and performs no task,
evidence, target, timestamp, or event write. A semantic revision requires at
least one of those three Contract-content fields to change. A crash retry or
authority re-label therefore cannot create a new Contract generation or force
another review.

For an explicit current user instruction with no external decision ID, the
agent may generate `user_instruction:<task-id>:<next-revision>` as the stable
authority reference without asking another question. Governing-document
authority uses a repository-relative path plus a known revision or content
hash. Neither form stores the instruction or document body.

A semantic Contract revision must atomically:

- preserve all earlier Contract revisions;
- advance the current revision;
- clear current completion evidence;
- when review has not started (`generation=0` and target empty), keep generation
  `0`; otherwise clear the current review target and advance its generation;
- move `review_pending` back to `in_progress` while leaving other allowed
  active statuses unchanged;
- update the task timestamp; and
- append one sanitized `contract_revised` event.

The task must then satisfy fresh verification, review, and completion gates.
Contract writes against `done` remain forbidden by the TG-M11 done guard until
the task is explicitly reopened. When and only when Contract input is supplied,
the write response adds a receipt containing `recorded` and the current
`revision`; ordinary add/edit payloads remain unchanged. The receipt does not
broaden the compact task object used by list/current/next.

Only `task show` gains an additive sibling `contract` projection. Contract
fields do not appear in `task list`, `task current`, `task next`, or the static
Viewer.

### Local Handoff Outbox

The same command is used whether an Issue Skill is absent, installed but
disabled, or explicitly connected:

```text
taskgov handoff record <source-task-id> ...
```

The agent never chooses a different workflow based on Issue Skill presence.
The command first commits one sanitized local record. Its only user-visible
states are:

- `pending_handoff`: durably recorded locally and awaiting or retrying
  delivery;
- `handed_off`: a configured receiver has accepted the record; this does not
  mean the Issue is resolved; and
- `handoff_withdrawn_by_user`: the user explicitly handled or withdrew the
  pending request outside Task Skill before any delivery claim.

Allowed state transitions are only
`pending_handoff -> handed_off` and
`pending_handoff -> handoff_withdrawn_by_user`. A handed-off or withdrawn
record cannot return to pending. Withdrawing records that the user no longer
wants Task Skill to deliver; it does not claim that an external Issue was
resolved. Once any delivery claim has been acquired, Task Skill can no longer
withdraw the record, even after lease expiry. Later cancellation would require
a separately designed receiver lookup/cancel contract.

The outbox stores only bounded, sanitized discovery metadata, source task
identity, the source Contract revision (or `0`), an idempotency identity, state,
delivery bookkeeping, and timestamps. It must not store Issue priority, Issue
lifecycle, semantic duplicate decisions, threat models, raw review/output,
secrets, stack traces, large diffs, or a `resulting_task_id`.

Exact replay of the same source task and canonical payload returns the existing
handoff. A distinct occurrence requires a stable explicit occurrence ID
provided by user instruction or another deterministic source. The first
version does not ask an LLM to distinguish a crash retry from a semantic
recurrence.

The local commit is the success boundary. Only a successful local commit may
return `ok=true`. Transient SQLite failure may receive one bounded retry and a
privacy-rejected payload may be replaced once with a shorter sanitized
abstraction; no unbounded retry loop is allowed. If the local record still
cannot be committed, the command returns `ok=false`, the agent immediately
reports stable `handoff_not_persisted`, and the current execution unit stops
until persistence succeeds or the user explicitly accepts the forgetting risk.
This is the one deliberate continue-first exception: it is an orchestration
stop, not an automatic Task status change. Recovery is deterministic: inspect
`db status`, obtain any required explicit initialization/migration or external
state repair, and replay the same record identity. The agent must never claim
that a failed local record was handed off.

Failure after the local commit returns success with state `pending_handoff`.
An absent or disabled receiver is the normal local-only mode and emits no
warning. A delivery attempt through an enabled receiver that fails emits the
additive warning with fixed `suggested_action=continue`. Neither case blocks
source-task completion, alters task
selection, appends a task event, or changes the source task's `updated_at`.

The approved command family is introduced only by the implementation unit that
can make each command useful:

- `handoff record`;
- `handoff list`, bounded to the existing compact-list style;
- `handoff show`;
- `handoff withdraw`; and
- `handoff sync --due`, which retries pending delivery only and never imports,
  reconciles, or mutates Issue lifecycle.

The local-outbox unit introduces record/list/show/withdraw. `handoff sync
--due` is introduced only with the separately approved Issue adapter and claim
state machine; the base Skill does not advertise a dead sync command.

Stable command names are `handoff.record`, `handoff.list`, `handoff.show`,
`handoff.withdraw`, and `handoff.sync`. Their minimum data payloads are:

- record: `handoff` and `local_record` with `durable`, `created`, `replayed`,
  and `handoff_id`;
- list: `handoffs`, returned `count`, exact `total_matching`, `limit`, and
  selected `states`;
- show: `handoff`;
- withdraw: `handoff` and `changed_fields`; and
- sync: bounded `claimed`, `accepted`, `pending`, and `failed` counts.

`handoff list` defaults to `pending_handoff` and oldest-first
`created_at, handoff_id` order. Terminal records appear only with an explicit
state filter. The default limit is 20 and maximum is 100; paging remains
deferred.

New stable error codes are `handoff_not_persisted`,
`handoff_not_withdrawable`, `handoff_occurrence_invalid`,
`contract_activation_forbidden`, `contract_authority_required`,
and `contract_write_conflict`. Canonically unchanged Contract input is a
successful no-op, not an error. `handoff_delivery_pending` is a warning code,
never a completion error.

`task show` gains only a compact sibling handoff summary. Full records remain
behind the handoff commands.

### Pending Rediscovery And Delivery

After local-outbox implementation, `db status` adds:

- exact `counts.handoff_pending`;
- `handoff_delivery.adapter_enabled`; and
- deterministic `handoff_delivery.sync_due`.

At session or execution-unit boundaries, the workflow runs exactly one
`handoff sync --due` only when both `adapter_enabled` and `sync_due` are true.
The agent does not decide whether to retry, ask the user, or enter a retry loop.
A delivery failure remains pending and non-blocking. No current/next warning is
required.

Delivery uses the stable `handoff_id` as receiver idempotency identity. Internal
claim and expiring-lease fields prevent concurrent delivery and withdrawal
races. Withdrawal succeeds only when no claim has ever been acquired.
Receiver acknowledgement changes state only through a compare-and-swap that
matches the active claim. After a crash between receiver acceptance and local
acknowledgement, the lease expires and a retry with the same `handoff_id` must
return the same receiver item before local state becomes `handed_off`.

The first adapter uses a fixed retry contract. A never-claimed record with
`delivery_attempts=0` is immediately due when an adapter is enabled, including
a record created before that adapter existed. Claim acquisition atomically
increments `delivery_attempts`, but that counter is observability only and does
not select a retry stage. Retry stage advances only on a matching-claim
retryable negative response:

- the first stores `last_delivery_code=retryable_wait_1` and is due after 60
  seconds;
- the second stores `last_delivery_code=retryable_wait_2` and is due after 300
  seconds; and
- the third stores `last_delivery_code=retry_exhausted` with no automatic due
  time.

A permanent negative response stores `permanent_error` and is also pending but
not due. An expired claim represents an uncertain acknowledgement: it does not
advance the retryable-result stage and is due for idempotent reconciliation
regardless of the normal retry cap. Active claims, permanent errors, and
exhausted negative responses make `sync_due=false`. Adapter-version changes do
not silently reset terminal retry metadata.

The Issue adapter is not part of the first outbox unit. It remains disabled by
default and may be implemented only after a versioned local Issue intake
contract exists and the governing write boundary is explicitly updated.
Enabling it requires user-approved project-scoped configuration. Task Skill may
then call only that versioned local intake boundary; it must not directly open,
initialize, migrate, or edit an Issue database, and must not invoke a shell,
URL, network service, or GitHub. Without the adapter, records simply remain
pending and the user may explicitly ask an agent to handle or withdraw one
outside Task Skill.

Handoff summary and rationale are limited to 1000 characters each; occurrence
ID to 200; adapter key/version, receiver receipt, stable delivery code, and
Contract authority reference to 500; withdrawal reason and Contract change
reason to 1000; Contract scope and acceptance to 4000 each; and Contract
constraints to 2000. All use the existing secret/raw-output/diff rejection.
Adapter source reference is only the tuple `project_id`, `source_task_id`, and
`source_contract_revision`; it contains no Contract or task prose.

### Optional Informational Effort Advisory

Effort Advisory is a later, default-off risk-profile feature. It may
deterministically report changed file/line/module counts, generated fixture
size, structured retry counts, Contract revision count, handoff count, and
configured test metrics when coverage is available.

Its initial contract is informational only:

- `suggested_action` is always `continue`;
- it never asks the user, records a handoff, changes acceptance, pauses,
  blocks, or fails a task by itself;
- it stores no acknowledgement or LLM disposition;
- repeated warnings may reuse one stable key; and
- absent or unreliable attribution is reported as `unknown`, not inferred.

Optional best-effort basis metadata may be attached to the first
`in_progress` write when the feature is enabled. Capture failure never blocks
task start. Git evaluation is read-only. Attribution is `unknown` whenever the
repository is non-Git, either endpoint is dirty or uncertain, basis coverage is
missing, or any active-task overlap occurred between basis and observation.
The advisory must expose its basis, coverage, attribution, thresholds, unknown
reasons, and stable warning key.

### Consuming-Project Modification Boundary

Consuming-project behavior belongs in documented configuration or an approved
adapter. Core Task Skill improvements belong in an upstream Issue or pull
request. If a consuming project modifies core package files, it must make the
installed version, origin, and local difference visible.

A later read-only `self status` command may compare packaged core files with a
release manifest and report `clean`, `modified`, or `unknown`. It must not
repair, update, download, contact GitHub, or stop task work. The once-daily
GitHub update-check proposal remains separately deferred.

### Compatibility, Judgment Budget, And Acceptance

Migration order is fixed:

1. TG-M11 schema-neutral corrections use schema-v5 fields only.
2. TG-M11 adds schema version 6 for Git snapshot base revisions.
3. TG-M12 adds schema version 7 for the handoff outbox.
4. TG-M12 adds schema version 8 for Task Contract revisions.

Old binaries must reject newer schemas with `migration_required` or an
equivalent explicit newer-schema error; they must never downgrade or write a
newer database. Operators must update the installed Skill before `db init` and
retain the normal local backup/rollback discipline.

Static Viewer snapshot version 3 maps to source schema versions 5, 6, 7, and 8.
`source_schema_version` contains the actual database version. Contract and
outbox fields are excluded from the Viewer task allow-list. Normal and
`--read-only` exports must be tested at every intermediate schema.

The decision budget is:

- legacy or simple revision-0 task: zero additional LLM judgments;
- Contract activation: zero judgments because it copies only explicit input;
- Task-versus-handoff classification: the one judgment already inherent in
  scope handling, with no second Issue-presence or continuation judgment;
- adapter delivery and due retry: zero judgments;
- Effort Advisory: zero judgments and zero stop decisions; and
- normal Tier 2 review: the existing two independent review judgments.

The sole new stop is failure to make an out-of-scope discovery durable after
the bounded local retry. It requires no LLM disposition, affects only the
current execution unit, and exists because continuing would reintroduce the
context-compression forgetting risk this extension is intended to remove.

Acceptance requires automated proof that:

- revision-0 tasks retain existing add/current/next/edit/done behavior;
- Contract creation and revision follow only explicit authority and preserve
  immutable history;
- canonical replay of the current Contract is a write-free no-op;
- a semantic Contract revision changes at least one content field;
- pre-review Contract change keeps generation 0, while a post-review semantic
  revision clears completion evidence and requires a fresh review generation;
- exact handoff replay creates one row, while explicit stable occurrence IDs
  can distinguish genuinely separate occurrences;
- local-record failure is never reported as durable, while delivery failure is
  durable, pending, and non-blocking;
- concurrent sync/withdraw/ack paths cannot produce a withdrawn record that was
  already accepted;
- once-claimed records cannot be withdrawn, including after lease expiry;
- pending handoffs default to deterministic oldest-first retrieval and are
  exactly counted;
- absence, disabled presence, or enabled presence of Issue Skill does not
  change the command the agent uses;
- migrations v5-to-v6-to-v7-to-v8 and v7-to-v8 preserve tasks, 191-event
  fixture history, nine completion hashes, review evidence, handoffs, Contract
  pointers/revisions, and project identity with rollback, quick, and
  foreign-key checks;
- Viewer snapshot v3 remains safe and unchanged at schemas 6, 7, and 8;
- privacy, compact envelopes, read-only behavior, project separation,
  concurrency, and no-target-project-mutation behavior do not regress; and
- the implemented Skill remains concise and advertises each surface only after
  its own implementation and acceptance pass.

Semantic duplicate/recurrence resolution, retention/archive, paging, multiple
Issue receivers, Issue import/sync/priority/triage, automatic Task creation,
advanced risk or fixture analysis, child/checklist tasks, signed evidence, and
daily GitHub update checking remain separate Issue candidates.

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
  "snapshot_version": 3,
  "generated_at": "2026-01-01T00:00:00Z",
  "project": {
    "project_id": "example-a1b2c3d4e5f6",
    "display_name": "example"
  },
  "source_schema_version": 5,
  "counts": {
    "total": 0,
    "ready": 0,
    "in_progress": 0,
    "paused": 0,
    "blocked": 0,
    "review_pending": 0,
    "done": 0,
    "cancelled": 0
  },
  "tasks": []
}
```

Each snapshot version uses an explicit task-field allow-list plus an `events`
array. Final TG-M8 snapshot version 3 contains the current `task show` task
fields and a bounded structured-evidence projection: current target/generation,
gate and blocking-finding counts, and at most 10 latest sanitized receipts or
findings per task. Intermediate version 2 adds pause fields but does not expose
schema-v4 evidence early. The snapshot must not add the canonical repository
path or database path. Stored task text remains subject to the existing
write-time privacy checks.

Snapshot version 1 is the implemented schema-v2/six-status baseline. TG-M8.2
introduces snapshot version 2 with `paused`, `pause_reason`, and a seven-status
count. TG-M8.6 introduces snapshot version 3 with the final typed-completion
and structured-review projection. The exporter and bundled template must
support the version produced by the installed schema; generated historical
HTML remains self-contained and needs no upgrade.

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
  "snapshot_version": 3
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
retains the schema-appropriate snapshot version after command resolution.

New user-correctable error codes are:

- `output_path_invalid`
- `output_parent_missing`

An operating-system write failure uses `output_write_failed` and exit code 2.
An absent or malformed bundled template is an `internal_error`.

### Viewer Experience

The viewer must be a read-only operational interface. It must provide:

- project identity and snapshot generation time
- status totals for all seven TG-M8 task statuses
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
- The same earlier-task predicate guards direct transitions to
  `in_progress`, `review_pending`, and `done`; `paused` is incomplete.
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

After TG-M9 implementation, required output also includes the exact paused task
count. This additive field does not remove paused tasks from the existing active
count.

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

This command must not create or migrate a database and must reject
initial `--status done` with `initial_done_forbidden`. Initial
`in_progress` or `review_pending` must pass the shared sequential predecessor
rule when the new task is sequential. Initial `paused` fails with
`initial_paused_forbidden`.

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
- Exclude `in_progress`, `paused`, `blocked`, `review_pending`, `done`, and
  `cancelled`.
- Supported filters are `--kind`, `--lane`, `--priority`, and `--limit`.
- Default limit is `5`.
- Priority order is `urgent`, `high`, `normal`, `low`.
- Sort by priority rank, lane, lane order with nulls last, creation time, and
  `task_id` for deterministic ties.

After TG-M9 implementation, a successful response also emits the
`paused_tasks_present` advisory warning when paused work exists. This does not
change any selection rule or `data` field.

### `taskgov task current`

Purpose: rediscover work already started, under review, paused, or blocked.

This TG-M8 inspection command is read-only and includes only `in_progress`,
`review_pending`, `paused`, and `blocked`. It returns the latest event and a
deterministic suggested next action for each task. Its default limit is `20`.
It does not perform stale-age or working-tree freshness analysis.

After TG-M9 implementation, optional `--status` accepts one of those four
current-work statuses. Omitting it preserves the existing combined view;
`--status paused` provides the bounded paused-work list used by the
`task next` warning.

### `taskgov task show`

Purpose: show one task and immediate context.

Required output:

- task fields
- recent notes or events
- timestamps
- suggested next action

After schema version 5, `task show` also returns `review_evidence` with the
current target/generation, tier requirement, current-generation qualifying
receipt counts, fallback state, blocking-finding counts and up to 10 sanitized
recent receipts/findings. This is a read-only projection, not raw review text.

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
- `--pause-reason`
- `--review-tier`
- `--verification`
- `--tags`
- `--add-note`
- `--completion-evidence-kind`, `--completion-revision`, and
  `--completion-evidence-reason`
- `--external-revision-approved`
- existing `--completion-commit-hash` and `--commit-not-required`

When setting `--status blocked`, `--blocked-reason` is required.
Setting `--status paused` requires `--pause-reason`, sequential
start/review/completion transitions enforce the shared predecessor rule, and
completion enforces structured review and explicit completion evidence. The
guard evaluates the resulting kind, lane, order, and status together and checks
both affected lanes when ordering metadata changes.

`task block` and `task done` aliases are postponed. Use `task edit` in the MVP.

### TG-M8 Review Evidence Commands

`taskgov review target set <task-id>` sets the task's current review target.
It requires `--kind` with `git_commit`, `diff_fingerprint`, or
`external_revision`, plus `--revision`. A Git target is verified and stored as
its canonical full commit ID; a diff fingerprint must be canonical
`sha256:<64-lowercase-hex>`. Changing a target appends an audit event and does
not delete older receipts. Every set, including re-setting the same value,
advances the target generation so historical receipts cannot reactivate.

`taskgov review receipt add <task-id>` stores one sanitized receipt for the
current target. It requires `--reviewer`, `--kind`, and `--verdict`; optional
`--summary` and the Tier 2 fallback `--user-approved` flag follow the structured
review rules above. Callers do not provide a separate target on this command.
The task and current target must exist, and the receipt kind, verdict, approval,
tier, and rationale combination must be valid as a unit.

`taskgov review finding add <task-id>` stores a sanitized finding and requires
`--receipt-id`, `--severity`, and `--summary`. The receipt must belong to the
same task and current project. Task and project identity are derived from the
loaded receipt rather than trusted from caller-provided values.

`taskgov review finding resolve <finding-id>` requires `--resolution` and
preserves the original finding. These commands never launch reviewers or make
LLM calls; they record and validate evidence produced by the approved workflow.

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
- `task.current`
- `task.show`
- `task.edit`
- `review.target.set`
- `review.receipt.add`
- `review.finding.add`
- `review.finding.resolve`

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
- `task.current`: `tasks`, `count`, `limit`, `statuses`.
- `task.show`: `task`, `events`, `suggested_next_action`, and
  `review_evidence` after schema version 5.
- `task.edit`: `task`, `changed_fields`, `event`.
- `review.target.set`: `task`, `changed_fields`, `event`.
- `review.receipt.add`: `receipt`, `event`.
- `review.finding.add`: `finding`, `event`.
- `review.finding.resolve`: `finding`, `event`.

Task objects in JSON must use the same field names as the task model. The
`review_tier` value must be an integer, not a string.

In `db.status`, `counts.active` means tasks with status `ready`,
`in_progress`, `paused`, `blocked`, or `review_pending`. It
excludes `done` and `cancelled`.

After TG-M9 implementation, `db.status.counts.paused` is an additive exact
project count. Existing meanings of `active`, `blocked`, `review_pending`,
`done`, and `next_actionable` are unchanged.

Required error codes:

- `invalid_argument`
- `invalid_status`
- `invalid_kind`
- `invalid_priority`
- `invalid_review_tier`
- `blocked_reason_required`
- `pause_reason_required`
- `initial_done_forbidden`
- `initial_paused_forbidden`
- `invalid_status_transition`
- `sequential_predecessor_incomplete`
- `privacy_rejected`
- `not_found`
- `db_not_initialized`
- `migration_required`
- `project_mismatch`
- `review_target_required`
- `review_receipts_insufficient`
- `review_finding_unresolved`
- `review_receipt_mismatch`
- `review_receipt_already_recorded`
- `invalid_review_evidence`
- `verification_required`
- `review_required`
- `commit_required`
- `completion_commit_conflict`
- `completion_evidence_conflict`
- `git_commit_not_found_or_ambiguous`
- `external_revision_approval_required`
- `internal_error`

Required TG-M9 warning code:

- `paused_tasks_present`

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
- review target value, external revision value, and reviewer key: 500
  characters each.
- pause reason, review receipt summary, review finding summary, and finding
  resolution summary: 1000 characters each.

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

TG-M8 is acceptable only when automated tests additionally prove:

- initial `task add --status done` fails without storing a task or event;
- initial `task add --status paused` fails, and invalid pause/resume source
  transitions return a stable error without mutation;
- a later sequential task cannot be started, reviewed, or completed while an
  earlier same-lane task is incomplete, including when it is paused;
- task registration and combined kind/lane/order/status edits cannot insert or
  reorder an incomplete predecessor ahead of active, review-pending, or done
  same-lane work;
- `task current` rediscovers active, review-pending, paused, and blocked work
  without writing the database or Git state;
- Tier 2 completion requires two distinct `PASS` receipts for the same current
  target and fails with any unresolved high or medium finding;
- resolving a high or medium finding without advancing the target generation
  remains blocked; a newer target plus fresh required PASS receipts succeeds;
- one reviewer cannot replace or contradict a receipt in the same target
  generation; re-review requires a new target generation;
- `task show` exposes the current review gate and bounded sanitized evidence so
  a new session can diagnose completion readiness without a write or LLM call;
- nonexistent or ambiguous Git commit evidence is rejected and a valid short
  hash is stored canonically;
- missing or old-schema databases are not created or migrated by task writes;
- schema migrations retain a realistic fixture with 12 tasks, 191 events, and
  all historical completion hashes;
- privacy rejection covers all new free-form fields;
- concurrent updates retain SQLite integrity; and
- the existing project-identity, `task next`, compact JSON-envelope, viewer,
  and `--read-only` contracts do not regress.

TG-M9 is acceptable only when automated tests additionally prove:

- `db status.counts.paused` is exact, including when more paused tasks exist
  than `task current` can return in one bounded response, while
  `counts.active` retains its existing meaning;
- successful `task next` emits no paused warning at count zero and exactly one
  `paused_tasks_present` warning at a positive count with the count and
  suggested command;
- paused warnings do not alter ready candidates, selection rules, filters,
  ordering, success exit status, or JSON `data`;
- `task current --status paused` returns only paused tasks with latest event,
  pause reason, deterministic suggested action, and `statuses=["paused"]`;
- unfiltered `task current` preserves its TG-M8 behavior, and unsupported
  current-status filters fail with `invalid_status` without mutation;
- successful and failing `db status`, `task next`, and `task current` reads do
  not change database contents, SQLite sidecars, task/tool events, or Git
  state;
- project identity separation and missing/migration-required behavior remain
  unchanged; and
- warnings expose no task title, pause reason, event text, secret, raw output,
  prompt, stack trace, or diff.
