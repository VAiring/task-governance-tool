# task-governance-tool MVP Specification

Status: TG-M18.0 through TG-M18.4 are complete. The synchronized repository
release candidate is v0.10.0/schema v16 with Viewer snapshot v4 accepting
sources 5-16, atomic native completion capture, and the bounded public history
read projection. The completed TG-M17 identity/storage behavior and TG-M16.4
reduced-loop behavioral acceptance are retained. M19.0 remains blocked until
the exact M18.4 completion SHA is frozen. TG-M12.3 Issue adapter remains
blocked on a future intake contract.

This document defines the first product contract for `task-governance-tool`.
It supersedes `plan.md` for MVP product behavior. `docs/implementation-roadmap.md`
governs implementation order and execution-unit boundaries. `plan.md` remains
the decision log and open-issue holding area.

All release- or milestone-specific sections before TG-M14 are historical
lineage for those releases, including headings labeled `Implemented` or
`Approved`. Their durable state-transition, review, and privacy rules continue
only where TG-M14 does not supersede them. Sections labeled `Historical`
preserve additional pre-M14 contract detail for migration and compatibility
review. The implemented TG-M14 and later extension sections together are the
current public command, setup, diagnostic, maintenance, and output contract.

## Product Goal

`task-governance-tool` helps Codex and the user run project work without loading
large task-status Markdown files into context. The MVP replaces the practical
functions of a large `TASK_STATUS.md`: register tasks, inspect current work,
select next actionable work, record blockers, and keep concise local task
history.

The tool must stay local-first, project-doc-respecting, and non-authoritative.
Target project governing documents remain the source of truth for project
decisions.

## Historical MVP Scope Through v0.7.0

The original MVP included the following surfaces. TG-M14 supersedes this
inventory with the implemented 20-leaf public surface below.

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

## Historical Non-Goals For MVP

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

Stateful governed-project use supports only a physical project-scoped copy.
User-wide, symlink, and junction installs are not public operating modes because
they can blur state ownership across projects. Source-checkout development and
tests remain separate from installing the Skill to govern another project.
M14.2 formalizes only the bounded self-host source-tree exception needed
for this repository's own Task history; it does not broaden ordinary install
guidance.

Project-scoped setup is a distinct, explicit step after installation. The
installer or agent must ensure that the canonical package `state/` directory
is effectively ignored for a Git-managed target, may preview with
`taskgov setup --read-only`, and runs
`taskgov setup --repo <target-project>` only with explicit intent to initialize
or migrate that project's local state. `setup` is the sole initializer and
migrator. Building the source skill package must not create a database,
because the target project identity is not known at package-build time.

The skill package should contain only files needed by Codex to use the skill:

```text
task-governance-tool/
  release-manifest.json
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

The public CLI derives this canonical path and rejects `--db`. Explicit
database-path injection remains internal to storage/service constructors and
tests only.

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
passes. An independent reviewer returns the actual verdict and findings; the
trusted caller that requested the review, normally the parent Codex or
orchestrator, records a concise sanitized receipt and any findings as an
attestation of that returned result. taskgov authenticates neither the caller
nor the reviewer and does not prove reviewer identity or independence.
`--review-complete` remains a command-time compatibility confirmation, but
cannot substitute for missing structured evidence.

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

## Implemented Post-MVP Extension: TG-M9 Paused Work Visibility

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

## Implemented Post-MVP Extension: TG-M11 Completion Integrity Corrections

TG-M11 incorporates the accepted parts of the CanonWeave operational
hardening feedback without making completed tasks permanently immutable or
increasing the normal Tier 2 review path beyond two LLM judgments. The contract
and its bounded implementation are part of the v0.3.0/schema-v6 baseline.

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

TG-M11 is advertised by `SKILL.md`, README, release guidance, and
installed-skill references only as part of the synchronized v0.3.0 release
after its acceptance gates pass. Implementation provides an explicit,
repeatable schema migration and preserves the sanitized 12-task/191-event
fixture, nine historical completion hashes, every existing review
receipt/finding, and project identity.

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
It was approved as a staged extension from the v0.3.0/schema-v6 baseline.
Each separately approved implementation unit changes and advertises only the
behavior that passes that unit's gates; TG-M12.1 advanced the implemented
baseline to v0.4.0/schema v7 without pre-advertising later units.

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

The CLI options are `--contract-scope`, `--contract-acceptance`,
`--contract-constraints`, `--contract-authority-ref`, and
`--contract-change-reason`. Supplying any Contract option supplies the group;
an explicit partial group is rejected rather than silently discarded. The CLI
copies that information during `task add`, or during an explicit
revision-0 transition from `ready` or `blocked` to `in_progress`. On add, the
resulting status must be `ready`, `in_progress`, `blocked`, or
`review_pending`. On edit, the Contract group may be combined only with that
status transition and requires empty completion evidence, target, and target
generation. It cannot be combined with notes, metadata, review-tier,
completion-evidence, or gate-confirmation changes. When no Contract option is
supplied, the task remains at revision `0`; the agent must not ask for missing
fields or infer that a task is "long enough" to need them. If the caller
explicitly starts a Contract group but omits scope or acceptance, the command
fails with `invalid_argument`.

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

Contract content is canonicalized by converting CRLF or CR to LF and removing
outer whitespace; internal text is otherwise preserved. Initial omitted
constraints are empty. On a later edit, omitted constraints preserve the
current value, while an explicitly supplied empty value removes them.

Supplying scope, acceptance, and constraints that canonically equal the current
Contract is an exact replay regardless of omitted, repeated, or re-labeled
authority/change metadata. Any supplied metadata is still privacy- and
size-validated before replay is accepted. A supplied `user_instruction`
reference must still name the same task and a positive revision, but an older
revision placeholder does not block an otherwise exact replay.
It returns `recorded=false` with the current revision and performs no task,
evidence, target, timestamp, or event write. A semantic revision requires at
least one of those three Contract-content fields to change and then requires
both authority reference and change reason. A crash retry or authority re-label
therefore cannot create a new Contract generation or force another review.

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
An exact replay returns `event=null` and `changed_fields=[]`. The fixed
`task show` projection contains revision, scope, acceptance, constraints,
authority reference, change reason, and creation time; revision zero uses
empty strings and `created_at=null`. The task object itself never contains the
current pointer.

The service validates the exact
`user_instruction:<task-id>:<revision>` form mechanically. Other sanitized
governing-file or roadmap identifiers remain caller-provided provenance; the
workflow rule that current-task outputs cannot self-authorize scope expansion
remains a documented authority boundary rather than a general provenance
engine. Concurrent same-content writes become one record plus one replay.
Without an expected-revision option, different valid semantic inputs serialize
as successive immutable revisions. If concurrent callers formed the exact
current-or-next `user_instruction` placeholder before locking, the service
binds it deterministically to the revision allocated by the locked write;
semantic changes reject other revision numbers, while exact replay accepts an
older same-task positive placeholder. Pointer or companion-input races return
`contract_write_conflict`.

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
recurrence. Omitting `--occurrence-id` uses the canonical empty value.
Supplying it explicitly with an empty, non-string, or over-200-character value
returns `handoff_occurrence_invalid`; privacy rejection remains
`privacy_rejected`.

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
- withdraw: `handoff` and
  `changed_fields=["state","withdraw_reason","withdrawn_at"]`; and
- sync: bounded `claimed`, `accepted`, `pending`, and `failed` counts.

`handoff list` defaults to `pending_handoff` and oldest-first
`created_at, handoff_id` order. Terminal records appear only with an explicit
state filter. The default limit is 20 and maximum is 100; paging remains
deferred. Count and rows come from one read snapshot. Its compact rows contain
only `handoff_id`, `source_task_id`, `source_contract_revision`, `summary`,
`state`, `created_at`, and `updated_at`. Record/show/withdraw may return the
full sanitized public record, but never the internal `claim_token`. Every
public projection revalidates stored privacy limits and the cross-field state
matrix rather than emitting corrupt or private stored text.

New stable error codes are `handoff_not_persisted`,
`handoff_not_withdrawable`, `handoff_occurrence_invalid`,
`contract_activation_forbidden`, `contract_authority_required`,
and `contract_write_conflict`. Canonically unchanged Contract input is a
successful no-op, not an error. `handoff_delivery_pending` is a warning code,
never a completion error.

`task show` gains only a compact sibling `handoff_summary` with exact
`pending_handoff`, `handed_off`, and `handoff_withdrawn_by_user` counts. Full
records remain behind the handoff commands. Handoff command errors use fixed
empty data shapes. Database readiness failures retain
`db_not_initialized`, `migration_required`, or `project_mismatch`;
`handoff_not_persisted` applies only after readiness validation when the local
record transaction cannot be committed.

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

Effort Advisory is a default-off, project-scoped profile feature. The only
configuration source is the installed Skill's fixed
`config/effort-advisory.json`; `taskgov` never creates or edits it. The
version-1 profile requires `profile="informational-v1"` and an explicit
boolean `enabled`. Its optional threshold allow-list is exactly
`changed_files`, `changed_lines`, `changed_modules`, `contract_revisions`, and
`handoffs`. Values are non-negative integers and exceed only on
`measurement > threshold`. Unknown fields, duplicate keys, unsupported
metrics, or invalid values disable the profile with a bounded diagnostic.

The public inspection command is
`taskgov task effort <task-id>`. It deterministically reports those five
metrics when their coverage is available. Git file/line/module counts use the
captured basis and current read-only endpoint; Contract revision and recorded
source-task handoff counts come from existing structured Task DB fields.
Generated fixture size, retry inference, configured test execution, and generic
risk profiles are deferred rather than guessed or added to the initial
implementation.

Its implemented contract through TG-M15.6 is informational only:

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
reasons, and stable warning key. Schema v9 adds only empty basis/activity
metadata and a zero project activity generation to support this conservative
attribution; migration does not rewrite existing task history.

An absent or valid disabled profile preserves all pre-advisory Task output and
performs no Git work. A project that has never captured
an advisory basis also performs no advisory-state bookkeeping. If a basis was
captured during an earlier enabled period, disabling the profile stops new
basis capture but retains only project/subject activity counters; this avoids
silently losing overlap evidence if the same profile is enabled later. An
invalid present profile adds only a bounded continuation diagnostic. The
mandatory default JSON `task show` response exposes the deterministic
enablement flag so the Skill can run one `task effort` observation at the
existing verification/review boundary rather than after every command.

For implementation lineage, M14.1 added that boolean to `task show`, M14.2
removed public `db status`, and M14.7 published the mechanically routed Skill
flow from `task show`. The approved TG-M16.1 override is defined in the
TG-M16 section below; TG-M16.0 does not change this implemented runtime.

### Consuming-Project Modification Boundary

Consuming-project behavior belongs in documented configuration or an approved
adapter. Core Task Skill improvements belong in an upstream Issue or pull
request. If a consuming project modifies core package files, it must make the
installed version, origin, and local difference visible.

TG-M12.O2 adds the offline read-only command `taskgov self status`. It compares
the installed core package with the co-located version-1
`release-manifest.json` and reports:

- installed `package_name` and `package_version`;
- manifest-declared `release_origin` and `manifest_version`;
- `status` as `clean`, `modified`, or `unknown`;
- exact `changed_core_count` and at most 20 sorted relative
  `changed_core_paths` after a complete comparison;
- stable `unknown_reasons`; and
- fixed `suggested_action=continue`.

The manifest contains a sorted map from portable package-relative core paths
to `sha256:<lowercase digest>`. It excludes itself to avoid recursive hashing.
Only root `config/`, `adapters/`, generated `state/`, and Python bytecode/cache
entries are non-core. An added file outside those boundaries is a core
modification. Manifest paths are strict relative POSIX paths; absolute,
traversal, duplicate, case-colliding, excluded, or malformed entries make the
result `unknown` without reading outside the package.

Missing, invalid, unsupported, identity-mismatched, version-mismatched, or
incompletely inspected packages return `ok=true`, `status=unknown`, and
`suggested_action=continue`. Confirmed missing, changed, non-regular, or
unexpected core entries return `modified`. Changed paths are bounded and never
include absolute paths, file content, expected or actual hashes, link targets,
or operating-system exception text.

`release_origin` is a sanitized declaration from the co-located manifest, not
a signature or authenticity proof. Simultaneously replacing core files and the
manifest can evade this local-drift check. The command does not use SQLite,
inspect target-project Git, contact GitHub or another network service, write a
cache, update, repair, download, install, create an Issue/PR/handoff, or stop
task work. It is an explicit package-inspection/setup surface and is not added
to the minimum task-consumption loop. The once-daily GitHub update-check
proposal remains separately deferred.

### Compatibility, Judgment Budget, And Acceptance

Migration order is fixed:

1. TG-M11 schema-neutral corrections use schema-v5 fields only.
2. TG-M11 adds schema version 6 for Git snapshot base revisions.
3. TG-M12 adds schema version 7 for the handoff outbox.
4. TG-M12 adds schema version 8 for Task Contract revisions.
5. TG-M12.O1 adds schema version 9 for optional Effort Advisory basis/activity
   metadata.

Old binaries must reject newer schemas with `migration_required` or an
equivalent explicit newer-schema error; they must never downgrade or write a
newer database. Operators must update the installed Skill before `db init` and
retain the normal local backup/rollback discipline.

Static Viewer snapshot version 3 maps to source schema versions 5 through 13.
`source_schema_version` contains the actual database version. Contract,
outbox, Effort Advisory, checkpoint, and maintenance metadata remain excluded
from the Viewer task allow-list. Snapshot-v3 compatibility must be tested at
every intermediate schema; no public Viewer export command or read-only export
mode remains.

The decision budget is:

- legacy or simple revision-0 task: zero additional LLM judgments;
- Contract activation: zero judgments because it copies only explicit input;
- Task-versus-handoff classification: the one judgment already inherent in
  scope handling, with no second Issue-presence or continuation judgment;
- adapter delivery and due retry: zero judgments;
- Effort Advisory through TG-M15.6: zero judgments and zero stop decisions;
  TG-M16 retains zero green-path judgments and permits one bounded
  session-local reconciliation episode only for a valid exceeded observation;
  and
- Local Package Self-Status: zero judgments and zero stop decisions; and
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
- migrations v5-to-v6-to-v7-to-v8-to-v9 and v8-to-v9 preserve tasks,
  191-event fixture history, nine completion hashes, review evidence, handoffs,
  Contract pointers/revisions, and project identity with rollback, quick, and
  foreign-key checks;
- Viewer snapshot v3 remains safe and unchanged at schemas 5 through 9;
- disabled Effort Advisory leaves Task output and Git reads unchanged; without
  an existing basis it also leaves advisory state unchanged, while after a
  prior basis it may advance only hidden activity counters; enabled threshold
  or unknown results through TG-M15.6 always continue and never mutate
  Task/handoff/acceptance/review state; TG-M16.1 changes only an exceeded
  result's action while preserving that no-mutation boundary;
- package self-status is deterministic and read-only for clean, modified,
  missing/invalid-manifest, excluded-directory, and installed-copy cases;
  every result continues and changes no package, SQLite, Git, or target state;
- privacy, compact envelopes, read-only behavior, project separation,
  concurrency, and no-target-project-mutation behavior do not regress; and
- the implemented Skill remains concise and advertises each surface only after
  its own implementation and acceptance pass.

Semantic duplicate/recurrence resolution, retention/archive, paging, multiple
Issue receivers, Issue import/sync/priority/triage, automatic Task creation,
advanced risk or fixture analysis, child/checklist tasks, signed evidence, and
daily GitHub update checking remain separate Issue candidates.

## Approved Post-MVP Extension: TG-M13 Operational Release Hardening

TG-M13 corrects operational consistency, lock duration, and distribution
guidance discovered during the independent v0.7.0 review. It adds no Task
lifecycle, Issue lifecycle, workflow-engine behavior, or normal-path LLM
judgment. The units are sequential:

1. TG-M13.1 makes live SQLite reads transactionally coherent, fixes the
   operational journal-mode contract, and maps read-side lock contention.
2. TG-M13.2 moves Git and Effort preflight outside short SQLite write
   transactions and extends the sanitized lock-contention error to writes.
3. TG-M13.3 synchronizes project-scoped installation, runtime, CI, Viewer, and
   privacy compatibility guidance.
4. TG-M13.4 performs integrated local acceptance and, only when separately
   authorized, records final-commit GitHub CI evidence.

### Operational SQLite Read And Journal Contract

The live task-governance-tool database supports SQLite rollback-journal mode
only. Before any operational SQLite read or write, the tool must inspect the
database header and adjacent WAL sidecar names without opening a mutable
connection. A persistent WAL header or an existing `-wal` or `-shm` sidecar
returns `unsupported_journal_mode` with the exact sanitized message
`task database uses unsupported WAL journal mode`. The tool must not open the
database, create a sidecar, checkpoint, delete a sidecar, or convert journal
mode on that path. Rollback-journal `-journal` files are not rejected merely
for existing; SQLite locking and recovery rules decide whether the requested
read can proceed.

Every live operational read must remove `immutable=1`. It opens the database
with `mode=ro`, enables `PRAGMA query_only=ON`, and begins one explicit read
transaction before validating schema, project identity, and the rows that form
one internally coherent response. Related counts and rows, task/event/review/
Contract projections, and handoff count/list projections must come from that
same response transaction. Each read therefore returns one committed-consistent
response or a structured concurrency error; it must never report uncommitted,
rolled-back, or internally mixed state.

In TG-M13.1, residual read-side `SQLITE_BUSY` or `SQLITE_LOCKED` after the
normal driver wait returns exit code 2, code `database_busy`, and the exact
message `task database is busy; run the command again later`. TG-M13.2 extends
that same mapping to write-side contention.

`task next` retains only its already documented advisory freshness boundary:
the paused/status inspection and candidate selection may be two committed read
transactions, so a commit between them may make the warning and candidates
briefly differ. Each half must remain internally coherent. This is not a
cross-command snapshot or blanket linearizability guarantee.

`task effort` is the other explicit phased inspection when the enabled advisory
has a stored basis. It reads the task, Contract count, handoff count, and stored
basis in one transaction and closes that transaction before Git observation. It
then refreshes activity generations in a second validated transaction. Each
phase is internally coherent; the generation comparison is the deliberate
bridge across them. Without a stored basis there is no generation comparison,
so the command does not add a valueless second database read. A post-Git
busy/locked read returns `database_busy`, and a newly detected WAL state returns
`unsupported_journal_mode`; neither is converted into a successful advisory.
Other bounded observation failures may retain the existing
`activity_generation_uncertain` advisory behavior.

### Short Write Transactions And Stable Contention

Potentially slow Git commit resolution, Git snapshot capture/comparison,
completion verification, and Effort observation must finish before
`BEGIN IMMEDIATE`. After acquiring the short write transaction, the service
must reread and revalidate schema/project identity plus the relevant task,
review generation, target/base, Task Contract revision, and completion basis.
Stale governance state returns the existing applicable domain conflict and
performs no partial write.

For optional Effort basis capture, preflight records the starting project and
subject activity generations plus whether another task is active, then observes
Git outside the write transaction. Under the short lock, the transition is
reread and the generations are compared before a basis is stored. Any
generation regression or impossible delta discards the best-effort basis.
Activity attributable to another task during capture, or another task active
before or after capture, sets `other_active_at_capture`; it must never be
reported later as an exclusive task window.

A `git_snapshot` is a stable observation of canonical HEAD and the stage-0
index at capture time. It neither acquires nor promises a persistent Git index
lock. A later index change is handled through the existing completion binding
and fresh-target rules.

Residual write-side SQLite `SQLITE_BUSY` or `SQLITE_LOCKED` after the normal
driver wait uses the same exit code 2, `database_busy`, and exact sanitized
message introduced for reads in TG-M13.1.
The envelope gains no `retryable` or `suggested_action` field, and the tool
does not increase the busy timeout or add sleep, backoff, or generic retries.
The existing handoff-record retry of one complete fresh transaction remains
the sole bounded exception. A failed write leaves no row, event, receipt, Git
change, or target-project change.

### Distribution And Compatibility Boundary

Normal governed-project use is documented only for a physical project-scoped
copy at:

```text
<target-project>/.agents/skills/task-governance-tool
```

When a command is launched from the target-project root, omitted `--repo`
continues to mean that whole directory, whether or not it is a Git repository.
When launched from inside the installed Skill directory, project commands must
pass the target-project path explicitly with `--repo`. Symlink and Windows
junction installs are unsupported for stateful use because code location and
project-local state ownership must not silently diverge. User-wide
`$CODEX_HOME/skills`, `%USERPROFILE%\.codex\skills`, and user-wide
`.agents/skills` operating paths are not part of the public governed-project
workflow.

Project identity remains derived from the canonical absolute target path, so
moving the project changes its default identity and can require explicit state
recovery. TG-M13 does not add a relocation command, project UUID, runtime
`--repo` requirement, or Git-repository existence requirement.

Target-project ignore guidance must name only the canonical Skill state
directory; the recommended target-local rule is
`/.agents/skills/task-governance-tool/state/`, while an effective enclosing
rule is also valid. Broad repository-wide `*.sqlite`, `*.sqlite3`, or `*.db`
guidance is not acceptable. Release-archive artifact exclusions remain a
separate packaging concern.

The documented minimum runtime is Python 3.12. Windows CI must exercise exact
Python 3.12 and 3.14 entries, including unsupported-junction detection. Windows
is the CI-verified platform; Linux and macOS remain unverified without a
support claim. Viewer snapshot v3 continues to accept source schemas 5 through
9, including automated schema-v8 coverage. No schema or Viewer snapshot version
changes in TG-M13.

### TG-M13 Acceptance And Judgment Budget

Acceptance requires automated proof that:

- rollback-journal cache spill and concurrent writers cannot produce an
  uncommitted, partial, or internally mixed successful inspection;
- persistent WAL header, WAL sidecar, and SHM sidecar paths fail before
  operational access without new sidecars or conversion;
- `db status`, task list/next/current/show/effort, handoff list/show, and Viewer
  projections preserve their compact success/error shapes and no-write rules;
- deliberately delayed Git or Effort preflight does not prevent an unrelated
  task or handoff write from completing;
- post-lock stale state is rejected atomically and residual lock contention
  maps only to `database_busy`;
- project-scoped install, ignore, relocation-limit, Python, Windows CI,
  junction, privacy recovery, and Viewer schema-compatibility contracts are
  synchronized and tested; and
- the final revision passes the full offline suite, integrity checks, package
  self-status, two independent Tier 2 reviews, and any separately authorized
  final-commit CI gate.

These corrections add zero normal-path LLM judgments, questions, retry choices,
or stop decisions. A deterministic SQLite error may fail the affected command,
but the Skill does not ask the model to choose a recovery policy. External CI
dispatch, push, PR creation, or publication remains outside local task
authority and requires explicit user authorization.

## Implemented Post-MVP Extension: TG-M14 Daily UX And Local Continuity

TG-M14 is implemented as the current v0.8.0/schema-v13 public contract. TG-M14.0
fixed the contract before implementation; TG-M14.1 through TG-M14.6 introduced
the bounded runtime slices, and TG-M14.7 synchronized the active Skill,
metadata, help, release guidance, manifest, workflow, and integrated
acceptance.

### Public Surface And Removed Invocation Contract

The completed M14 public surface contains exactly these 20 leaves:

1. `taskgov setup`
2. `taskgov doctor`
3. `taskgov task add`
4. `taskgov task list`
5. `taskgov task next`
6. `taskgov task current`
7. `taskgov task effort`
8. `taskgov task show`
9. `taskgov task edit`
10. `taskgov task complete`
11. `taskgov task checkpoint`
12. `taskgov handoff record`
13. `taskgov handoff list`
14. `taskgov handoff show`
15. `taskgov handoff withdraw`
16. `taskgov review prepare`
17. `taskgov review target set`
18. `taskgov review receipt add`
19. `taskgov review finding add`
20. `taskgov review finding resolve`

`task complete --check` is a read-only mode of the same leaf. Applicable
commands retain `--repo`, `--json`, `--read-only`, and root `--version`.
`setup` alone also accepts `--backup-interval-minutes` in the inclusive range
1-1,440 and `--backup-generations` in the inclusive range 1-20. The normal
Skill setup supplies neither option. Completed M14 removes public `self`, `db`,
`web`, `--db`, raw database/backup/Viewer path fields, custom Viewer output,
compatibility aliases, and any replacement storage, database, Viewer, export,
repair, maintenance, disable, or admin command.

Removed root commands and any unknown root command fail before package, project,
Git, or SQLite resolution. They use exit 2. With lexical `--json`, stdout is the
compact envelope below and stderr is empty:

```json
{
  "ok": false,
  "command": "parse",
  "project_id": null,
  "data": {},
  "warnings": [],
  "errors": [
    {"code": "invalid_command", "message": "command is not available"}
  ]
}
```

Any public `--db` occurrence fails at the same pre-resolution boundary with
code `invalid_option` and exact message `option is not available`. Its following
value is never echoed. Without lexical `--json`, stdout is empty and stderr is
exactly `taskgov: command is not available` or
`taskgov: option is not available`, followed by one newline. No compatibility
handler, alias, state lookup, or write is permitted. Lexical `--db` takes
precedence over an unknown or removed command; otherwise command validation
precedes every command-specific option check.

All completed-M14 command envelopes contain only `ok`, `command`, `project_id`,
`data`, `warnings`, and `errors`. `project_id` remains the existing sanitized
identity when safely resolved. Public output never contains `db_path`,
`backup_path`, `viewer_path`, another raw storage path, or a rejected CLI value.

### Compact Selection And Completion

Only `task current` and `task next` accept `--compact`. Default JSON and text
payloads remain compatible. Compact JSON uses the normal envelope and these
allow-lists:

- current data: `tasks`, `total_matching`, `returned_count`, `limit`,
  `statuses`, `truncated`;
- current task: `task_id`, `title`, `status`, `kind`, `lane`, `lane_order`,
  `priority`, `review_tier`, `blocked_reason`, `pause_reason`, `latest_event`,
  `suggested_next_action`;
- compact latest event: `event_type`, `summary`, `created_at`,
  `summary_truncated`;
- next data: `tasks`, `total_matching`, `returned_count`, `limit`,
  `truncated`;
- next task: `task_id`, `title`, `kind`, `lane`, `lane_order`, `priority`,
  `review_tier`, `tags`, and `suggested_next_action`.

Compact current is at most 24,576 UTF-8 bytes and compact next is at most
16,384 UTF-8 bytes, including the envelope. Event summary is bounded to 256
UTF-8 bytes at a valid code-point boundary. Rows are retained only in the
existing deterministic order; if another complete row would exceed the cap it
and all later rows are omitted and `truncated=true`. Compact mode never changes
selection, state, the database query's coherent transaction, or default output.
`--compact` requires `--json`; without it the command fails before state access
with exit 2, code `invalid_option_combination`, and exact message
`--compact requires --json`. No second compact text formatter exists.
Compact selection intentionally omits Contract and checkpoint content; the
fixed post-selection `task show` call below obtains both without inflating every
candidate row.

M14.1 retains the normal envelope keys while M14.2 owns their global removal.
Every compact JSON and JSON completion-check handler exit passes through one
final bounded formatter. Independently, every argparse rejection that requests
JSON lexically before `--`, including an argparse-supported `--json`
abbreviation, passes through the same formatter at the smallest 8,192-byte
cap. It therefore needs no second task-leaf or root-option parser and remains
within all three bounded-command caps. The formatter keeps diagnostic identity
values when the exact rowless envelope fits. If those values would break the
applicable hard cap, it sets `db_path` to `null` and then, only if still
necessary, `project_id` to `null`; identity values are never partially
truncated. If an error envelope still cannot fit because its diagnostic
message contains a rejected path, option, or other unbounded value, the
formatter preserves the command, data, exit code, and first safe error code but
replaces that message with
`diagnostic details omitted to satisfy the bounded output limit`. Success data
and error codes otherwise retain their command semantics.

`task complete --check` accepts the same proposed evidence and confirmation
inputs as thin `task complete`, invokes exactly the current completion
validator, writes nothing, and emits at most 8,192 UTF-8 bytes. It captures one
short coherent database basis, closes that transaction before any required Git
observation, then uses a second short coherent read to revalidate task,
Contract, target generation/fields, receipts/findings, and evidence basis
before returning. No SQLite transaction is held during Git. A changed basis
returns not-ready with first code `completion_check_stale`. Its data allow-list
is `task_id`, `ready`, `status`,
`blocking_codes`, `contract_revision`, `review_target_generation`,
`completion_evidence_kind`, and `suggested_action`. Blocking codes preserve the
validator's existing order: task/state and sequential ordering, evidence input
and Git binding, verification confirmation, current review target/receipts/
findings, then final snapshot binding. `blocking_codes` contains either no code
or the one first stable code that the write path would return. Check and write
call one shared fail-fast pure preflight; the check does not invent an
all-findings collector or a second state machine. A check is never an
authorization token; the write revalidates everything.

Only these user-correctable completion/gate codes may appear in
`blocking_codes`: `invalid_status_transition`,
`sequential_predecessor_incomplete`, `verification_required`,
`review_required`, `completion_evidence_conflict`,
`external_revision_approval_required`, `commit_required`,
`git_commit_not_found_or_ambiguous`, `invalid_review_evidence`,
`review_target_required`, `review_target_mismatch`,
`review_finding_unresolved`, `review_changes_requested`,
`review_receipts_insufficient`, and `completion_check_stale`. Parse/privacy
validation, `not_found`, project identity, schema/journal/busy/storage, and
internal errors remain normal `ok=false` command errors and never appear as
readiness data.

The check text form is exactly three LF-terminated lines:
`Task <task_id>: ready|not ready`, `Blocking: none|<comma-separated codes>`,
and `Suggested action: <bounded suggested_action>`. It contains no evidence
value, path, Contract text, or review content.

Thin `task complete` shares the existing transition and accepts only the
completion subset currently supported by `task edit`: task ID, verification
and review confirmations, and one valid `git_commit`, `external_revision`, or
`commit_not_required` evidence form. Legacy `task edit --status done` remains
compatible. Neither check nor thin completion creates a second completion state
machine.

Thin completion emits `command=task.complete`. Its data keys are exactly the
existing edit result keys `task`, `changed_fields`, and `event`; it adds no
completion wrapper. Text uses the existing edit summary fields with the first
line changed to `Task completed: <task_id>`.

M14.1 moves the existing Effort Advisory routing signal off removed
`db status` and onto the already mandatory default JSON `task show` response.
Its data gains exactly one machine-routing field,
`effort_advisory_enabled: true|false`. A valid enabled profile returns `true`;
an absent or valid disabled profile returns `false`; and an invalid profile
returns `false` plus the existing `effort_advisory_profile_invalid`
continuation warning. The field performs no Git work and text `task show`
remains unchanged. The Skill mechanically invokes one existing `task effort`
call at the established verification/review boundary only when this boolean is
true. It does not ask an LLM whether the advisory applies.

The deterministic Skill call graph is:

- one compact `task current` call to rediscover work;
- when it returns an `in_progress` or `review_pending` row, resume the first
  such row in returned order; otherwise make one compact `task next` call.
  Returned `paused` and `blocked` rows remain rediscovered but do not suppress
  unrelated ready selection;
- one `task show` call for the resumed or selected task so its complete current
  Contract, latest checkpoint, and Effort Advisory routing flag are always
  read;
- one task edit to start the selected task;
- only for a deterministically enabled Effort Advisory profile, one existing
  `task effort` observation at the verification/review boundary;
- one review target set call after the exact material is ready;
- one `review prepare` call instead of separate task, Contract, target, and Git
  context reads;
- one receipt write per actual receipt; and
- one thin complete call.

A default-off no-finding Tier 2 path therefore has at most nine governance
subprocess calls; a profile-enabled path has at most ten. Both exclude real
progress updates and the two independent review model decisions.
`task complete --check`, `doctor`, and `task checkpoint` are absent from the
default success path.

### Doctor Contract

`doctor` is the sole diagnostic, is inherently read-only, emits
`command=doctor`, and always includes `data.suggested_action="continue"`.
It has no fix mode and never initializes, migrates, backs up, renders, locks a
maintenance artifact, runs project tests, or changes target state. Only when a
Git administrative marker exists at the governed target or an ancestor may
doctor run the one TG-M15.4 effective-ignore inspection; it performs no Task,
review, or completion Git observation. Doctor is not a prerequisite for setup
or normal Task work.

The component ownership is exclusive:

- `package`: package integrity only;
- `project_state`: setup, format, identity, and readability readiness;
- `task_summary`: active, blocked, done, next-actionable, paused, and
  review-pending task counts;
- `handoff_delivery`: pending handoffs, adapter enablement, and delivery/due
  state;
- `maintenance`: backup and Viewer opt-in, due state, and latest bounded
  sanitized outcomes.

Package inspection is an independent bounded filesystem observation. When
project state is readable, every other component is assembled from one
lock-respecting SQLite read transaction. No atomicity is claimed between the
package observation and SQLite snapshot. Unavailable project-backed components
contain only `{"code":"unavailable"}`.

Doctor data contains only `suggested_action`, `setup_eligible`, and
`components`. `setup_eligible` is the logical conjunction of every package,
runtime, install, ignore, project, and state precondition; it is never a
project-state-only claim. Component keys are exactly `package`,
`project_state`, `task_summary`, `handoff_delivery`, and `maintenance`.
`package` reuses the existing bounded package-integrity projection.
`project_state` owns every non-package readiness/preflight code and contains
only `code`, `schema_version`, and `required_schema_version`. A readable
`task_summary` contains `code` plus
counts for `active`, `blocked`, `done`,
`next_actionable`, `paused`, and `review_pending`. A readable
`handoff_delivery` contains `code`, `handoff_pending`, `adapter_enabled`, and
`delivery_due`. A readable `maintenance` contains `code`, `opted_in`, one
bounded `backup` object, and one bounded `viewer` object. The backup object
has exactly `code`, `due`, `interval_minutes`, `generations`,
`last_success_at`, and `last_outcome`. The Viewer object has exactly `code`,
`due`, `source_generation`, `rendered_generation`, `last_success_at`, and
`last_outcome`. Each `last_outcome` contains only `code` (`none`, `succeeded`,
`deferred`, or `failed`) and `occurred_at`; it never contains a message or
exception. A not-yet-owned value is `null`, not omitted. An unavailable
project-backed component is only `{"code":"unavailable"}`.

Doctor process results are fixed as follows:

| Package/project row | Component code | Exit / `ok` | Warning or error | `setup_eligible` |
|---|---|---|---|---|
| clean + current readable state | `clean` / `ready` | 0 / true | none | true |
| modified package | `modified` | 0 / true | `package_core_modified` warning | false |
| unknown package inspection | `unknown` | 0 / true | `package_status_unknown` warning | false |
| missing state | `setup_required` | 0 / true | `setup_required` warning | true |
| supported older schema | `migration_required` | 0 / true | `migration_required` warning | true |
| unreadable or invalid state | `unreadable` | 2 / false | `project_state_unreadable` error | false |
| foreign project identity | `foreign` | 2 / false | `project_mismatch` error | false |
| newer schema | `newer` | 2 / false | `schema_too_new` error | false |
| SQLite busy/locked | `busy` | 2 / false | `database_busy` error | false |
| WAL header/sidecar state | `unsupported_journal` | 2 / false | `unsupported_journal_mode` error | false |
| linked/unsupported/colliding layout | project_state `invalid_layout` | 2 / false | `unsupported_install_layout` error | false |
| invalid/missing project directory | `invalid_project` | 2 / false | `invalid_project_root` error | false |
| omitted repo at either package root | `invalid_project` | 2 / false | `project_scope_required` error | false |
| unsupported Python | `unsupported_runtime` | 2 / false | `unsupported_python` error | false |
| missing state ignore protection | `ignore_required` | 2 / false | `state_ignore_required` error | false |
| invalid canonical state ownership | `invalid_state_path` | 2 / false | `state_path_invalid` error | false |

Here, a supported older schema means contiguous declared history with every
object and per-project row required through that version and no recognized
marker from a later migration. A missing required object/row or later marker
is unreadable/invalid state; setup must not advertise or begin migration for
it.

Fatal project errors take process precedence but do not suppress a bounded
package warning. Layout validity never changes the independently observed
package-integrity code: for example, a clean source package with a competing
install is package `clean` plus project_state `invalid_layout`; an
uninspectable linked package may independently be package `unknown` plus the
same project-state error. No row exposes a path or OS/SQLite exception. M14.2
implements
the envelope, package/project/task/handoff rows, and base maintenance rows
`not_opted_in` or `enabled`, including only whether a setup-owned backup copy
and Viewer publish succeeded. M14.3 preserves that setup-copy baseline and adds
backup due calculation, routine generations, and routine fixed outcomes. M14.6
adds Viewer source/render generation, due, and latest fixed outcome while
preserving backup fields. M14.7 accepts the combined table.

Maintenance row staging is exact:

| Stage/object | `code` values | Other fixed behavior |
|---|---|---|
| M14.2 maintenance | `not_opted_in`, `enabled` | interval/retention are exposed only when enabled |
| M14.2 backup | `not_opted_in`, `setup_copy_succeeded`, `configured` | a partial migration row can expose setup-copy success before opt-in; enabled state exposes policy plus setup-copy `last_success_at`/`last_outcome=succeeded`; `due` remains null |
| M14.2 Viewer | `not_opted_in`, `published`, `repair_required` | reflects setup publication only; no generation calculation |
| M14.3 backup | `not_opted_in`, `current`, `due`, `deferred`, `failed` | exposes `due`, policy, last success, and one fixed sanitized latest outcome |
| M14.6 Viewer | `not_opted_in`, `current`, `due`, `deferred`, `failed` | exposes `due`, source/render generations, last success, and one fixed sanitized latest outcome |

For the same object, a later stage's code set replaces the earlier provisional
labels while preserving the established keys and base facts; stage code sets
are not cumulative aliases.

`deferred` means zero-wait artifact-lock contention and remains due; `failed`
means a bounded artifact operation failed and remains due. `current` means not
due. Doctor never converts these historical maintenance codes into an envelope
warning or error: every readable maintenance combination has exit 0,
`ok=true`, and `suggested_action=continue`. Only an independently fatal
project-state row makes maintenance `unavailable` and the command fail.

Doctor and setup share this fixed first-applicable preflight precedence:
`unsupported_python`, `unsupported_install_layout`, `project_scope_required`,
`invalid_project_root`, `state_path_invalid`, package
`package_core_modified|package_status_unknown`, `state_ignore_required`,
`unsupported_journal_mode`, `database_busy`, `project_state_unreadable`,
`project_mismatch`, `schema_too_new`, then
`migration_required|setup_required|ready`. Package drift/unknown is an exit-0
doctor warning but makes top-level `setup_eligible=false`; the same code is a
fatal setup error. Ignore protection participates only for a Git-managed
project, so a physical non-Git project remains eligible. A lower-precedence
condition never replaces the process error, although the independent bounded
package warning may still accompany a later project-state error.

Preflight/doctor codes use these fixed sanitized messages:

| Code | Message |
|---|---|
| `unsupported_python` | `Python 3.12 or newer is required` |
| `unsupported_install_layout` | `stateful use requires one supported physical project-scoped package layout` |
| `project_scope_required` | `explicit --repo is required from the package directory` |
| `invalid_project_root` | `project root must be an existing directory` |
| `state_path_invalid` | `project state path is not valid for this package layout` |
| `package_core_modified` | `packaged core files differ from the release manifest` |
| `package_status_unknown` | `package integrity could not be verified` |
| `state_ignore_required` | `project-local state must be ignored before setup` |
| `unsupported_journal_mode` | `task database uses unsupported WAL journal mode` |
| `database_busy` | `task database is busy; run the command again later` |
| `project_state_unreadable` | `project state could not be read safely` |
| `project_mismatch` | `task database belongs to a different project` |
| `schema_too_new` | `task database schema is newer than this taskgov version` |
| `migration_required` | `task database requires setup migration` |
| `setup_required` | `project state is not set up` |

### Enclosing Git Ignore Preflight

TG-M15.4 changes only how the existing `state_ignore_required` preflight
recognizes Git management and effective ignore rules. It does not change the
governed project root, project identity, package layout, canonical state path,
preflight precedence, public error, or setup/doctor envelope.

The canonical explicit target remains the governed root even when it is a
subdirectory of a larger Git worktree. `--repo`, omitted-repo behavior,
project-ID derivation, state ownership, setup writes, Viewer publication, and
completion Git validation continue to use that target rather than an enclosing
worktree root.

Ignore inspection is deterministic:

1. Inspect the canonical target and each parent through the filesystem root,
   stopping at the nearest existing or link-like `.git` administrative marker.
   An error while inspecting a marker or advancing the parent chain fails
   closed as `state_ignore_required` without a subprocess or write.
2. If no marker exists, classify the physical target as non-Git for this
   preflight, accept it without launching an ignore subprocess, and preserve
   existing non-Git setup/doctor behavior.
3. If a marker exists, invoke exactly one shell-free Git process from the
   governed target with the fixed equivalent of
   `git check-ignore --quiet --no-index -- <canonical-state-directory/>`.
   The single target-relative operand is
   `.agents/skills/task-governance-tool/state/` for the ordinary layout or
   `task-governance-tool/state/` for self-host, uses forward slashes and the
   trailing slash for directory semantics, and follows `--` as one argument.
4. Exit 0 alone proves the effective ignore result. Exit 1, any other exit,
   the two-second timeout, launch failure, or other subprocess failure maps to
   the existing `state_ignore_required` result and performs no write.

The process uses the existing sanitized Git environment, disables optional
locks, lazy fetch, replacement objects, terminal prompts, and filesystem
monitor integration, supplies no stdin, and retains or emits no stdout,
stderr, path, pattern source, or OS exception. Parent-worktree rules,
target-local rules, negation, linked-worktree/gitfile layouts, and submodule
boundaries are therefore decided by Git's effective ignore semantics rather
than by parsing one `.gitignore` file. A superproject rule does not stand in
for a submodule-local effective rule.

Each `setup`, `setup --read-only`, or `doctor` invocation runs at most this one
ignore process. Setup's later stage revalidations repeat structural,
package-integrity, project, and state-ownership checks without repeating
ignore inspection. Ordinary Task, handoff, and review commands run no added
marker scan or ignore process.

This preflight does not inspect whether an artifact is already tracked, prove
that an ignore rule is committed or portable, add a sentinel filename, edit
`.gitignore`, authenticate Git, retry, cache, or create a reusable Git command
framework. Perfect ignore-rule TOCTOU detection across later setup stages is
also outside the local non-adversarial threat model. It adds no Task-loop call,
LLM judgment, question, or normal stop; only setup/doctor for a Git-candidate
target may return the already defined deterministic error.

### Setup Contract

`setup` is explicit, noninteractive, idempotent, and limited to one supported
physical project-scoped package layout. Ordinary governed projects use exactly
`<repo>/.agents/skills/task-governance-tool`. The bounded development-only
self-host layout uses exactly `<repo>/task-governance-tool` and is accepted only
when:

- the caller supplied `--repo` explicitly;
- package and repository paths are physical, canonical, non-linked directories;
- regular source files `AGENTS.md`, `docs/specification.md`, `docs/design.md`,
  `docs/implementation-roadmap.md`, and `plan.md` exist under that same repo;
- the package's `SKILL.md`, `release-manifest.json`, and `scripts/taskgov.py`
  pass the normal package boundary and integrity checks; and
- no competing `<repo>/.agents/skills/task-governance-tool` package exists.

The exception reuses the canonical state already under the source package. It
does not copy or relocate a database, create a second state mode, relax
user-wide/link rejection, or become consuming-project install guidance.
`setup` remains the sole public initializer, migrator, one-way
local-maintenance opt-in, and canonical Viewer repair action. Non-Git ordinary
project directories remain valid. Invocation from either package root without
explicit `--repo` is rejected for setup and every other project-scoped command;
root `--version` remains project-free.

Preflight validates the project directory, physical install boundary, Python
3.12+, canonical state ownership, package integrity, ignore protection, and
project state before a write. When the canonical database is missing, setup
first inspects only this project's canonical managed-backup directory. If a
valid same-project generation exists, setup selects the newest valid generation
by publication time then generation ID and plans recovery instead of empty
initialization. The write order is: preflight; recovery or fresh
initialization; when migrating, one validated backup; migration; one-way
opt-in/configuration; canonical Viewer publication. `setup --read-only`
returns the same planned write set and performs none of those writes.

Schema v10 stores `backup_interval_minutes` and `backup_generations` in the
single project-maintenance row beside the one-way opt-in. Initialization or
migration creates a partial row with nullable `enabled_at` and policy fields;
successful migration may already store its setup-copy success metadata.
Configuration atomically fills the policy and changes `enabled_at` from null to
one immutable non-null timestamp. Doctor reports `setup_required` while it is
null, and setup resumes at configuration without losing the setup-copy
baseline. Fresh setup and migration use defaults 30 and 3 when options are
omitted. On an already
configured project, an omitted option preserves its stored value; an explicitly
provided option changes only that field. Values outside 1-1,440 minutes or 1-20
generations fail before any write with `invalid_backup_policy`. The standard
Skill workflow never selects or supplies non-default values; they require
explicit caller intent. The configuration transaction rereads the current row
under its short SQLite writer lock and resolves each omitted field from that
locked row, so a stale preflight plan cannot revert a concurrently completed
explicit setting. A configuration-only setup does not itself render or attempt
a routine backup. The next eligible business mutation uses the new values, and
reduced retention is enforced only after the next successful backup
publication.
The same row has nullable internal `applied_backup_generations`, constrained to
the same 1-20 range. Configuration does not change it. Each managed-backup
stage resolves one immutable 1-20 `publication_retention` before copying:
setup uses its validated explicit value or default 3 when the source policy is
partial/unconfigured, setup uses the stored value observed before any later
`maintenance_configure` when the source is configured, and routine work uses
the stored value observed for that attempt. A successfully published
generation records this value as applied in the same metadata transaction.
Post-publication pruning and restart reconciliation use the applied value. This
distinguishes an interrupted prune from a newly reduced policy without a second
configuration store or a user-visible option.
No second JSON/TOML configuration file is created; SQLite keeps policy and
maintenance state atomic while the public CLI and Viewer continue to hide the
storage path.

Setup data contains only `status`, `planned_writes`, `completed_writes`,
`schema_from`, `schema_to`, `maintenance_enabled`,
`backup_interval_minutes`, `backup_generations`, and `viewer_status`.
Write lists use only `database_restore`, `database_initialize`, `migration_backup`,
`database_migrate`, `maintenance_configure`, and `viewer_publish`, in execution
order. `viewer_status` is one of `not_present`, `current`, `published`, or
`repair_required`. Explicit values equal to stored policy are a no-write replay,
including under `--read-only`.

Those scalar fields have one meaning on every row:

- `schema_from` is the safely observed canonical schema before the command,
  the selected managed-backup schema when recovery is planned, or `null` when
  neither initialized state nor a recovery candidate exists. `schema_to` is
  always the owning runtime's required schema version.
- `maintenance_enabled` and `viewer_status` describe durable state after the
  command returns. Preview therefore reports current state, not planned state.
  `published` means this invocation successfully published the canonical
  Viewer; an already-current Viewer is `current`; a missing Viewer is
  `not_present`; and a present invalid/stale Viewer is `repair_required`.
- the two policy fields are the effective valid requested/stored values for the
  plan. They do not assert persistence; `completed_writes` records whether
  configuration became durable.
- every `ok=false` row has `status=null`. On a shared-preflight failure or
  `invalid_backup_policy`, `schema_from`, `maintenance_enabled`, both policy
  fields, and `viewer_status` are `null`, `schema_to` remains the required
  version, and both write arrays are empty. On a later stage failure,
  `schema_from` and the effective policy remain populated while
  `maintenance_enabled`, `viewer_status`, and `completed_writes` report the
  durable ordered prefix left by that invocation.

| Setup row | `planned_writes` | `completed_writes` | Exit / `ok` / `status` or error |
|---|---|---|---|
| fresh preview | `[database_initialize, maintenance_configure, viewer_publish]` | `[]` | 0 / true / `setup_preview` |
| fresh success | `[database_initialize, maintenance_configure, viewer_publish]` | `[database_initialize, maintenance_configure, viewer_publish]` | 0 / true / `setup_complete` |
| current-schema recovery preview | `[database_restore, viewer_publish]` | `[]` | 0 / true / `setup_preview` |
| current-schema recovery success | `[database_restore, viewer_publish]` | `[database_restore, viewer_publish]` | 0 / true / `setup_complete` |
| older-schema recovery preview | `[database_restore, migration_backup, database_migrate, maintenance_configure?, viewer_publish]` | `[]` | 0 / true / `setup_preview` |
| older-schema recovery success | `[database_restore, migration_backup, database_migrate, maintenance_configure?, viewer_publish]` | same ordered list | 0 / true / `setup_complete` |
| managed recovery material but no valid same-project candidate | `[]` | `[]` | 2 / false / `setup_restore_failed` |
| recovery publication failure | recovery plan | `[]` | 2 / false / `setup_restore_failed` |
| current healthy, options omitted or equal | `[]` | `[]` | 0 / true / `already_setup` |
| policy-change preview | `[maintenance_configure]` | `[]` | 0 / true / `setup_preview` |
| policy-change success | `[maintenance_configure]` | `[maintenance_configure]` | 0 / true / `setup_complete` |
| invalid policy | `[]` | `[]` | 2 / false / `invalid_backup_policy` |
| unconfigured or policy-change migration preview | `[migration_backup, database_migrate, maintenance_configure, viewer_publish]` | `[]` | 0 / true / `setup_preview` |
| unconfigured or policy-change migration success | `[migration_backup, database_migrate, maintenance_configure, viewer_publish]` | `[migration_backup, database_migrate, maintenance_configure, viewer_publish]` | 0 / true / `setup_complete` |
| unconfigured or policy-change migration-backup failure | `[migration_backup, database_migrate, maintenance_configure, viewer_publish]` | `[]` | 2 / false / `setup_backup_failed` |
| configured unchanged migration preview | `[migration_backup, database_migrate, viewer_publish]` | `[]` | 0 / true / `setup_preview` |
| configured unchanged migration success | `[migration_backup, database_migrate, viewer_publish]` | `[migration_backup, database_migrate, viewer_publish]` | 0 / true / `setup_complete` |
| configured unchanged migration-backup failure | `[migration_backup, database_migrate, viewer_publish]` | `[]` | 2 / false / `setup_backup_failed` |
| configured unchanged migration failure after backup | `[migration_backup, database_migrate, viewer_publish]` | `[migration_backup]` | 2 / false / `setup_migration_failed` |
| configured unchanged migrated Viewer failure | `[migration_backup, database_migrate, viewer_publish]` | `[migration_backup, database_migrate]` | 2 / false / `setup_incomplete` |
| initialization failure | `[database_initialize, maintenance_configure, viewer_publish]` | `[]` | 2 / false / `setup_initialization_failed` |
| unconfigured or policy-change migration failure after backup | `[migration_backup, database_migrate, maintenance_configure, viewer_publish]` | `[migration_backup]` | 2 / false / `setup_migration_failed` |
| fresh configuration failure | `[database_initialize, maintenance_configure, viewer_publish]` | `[database_initialize]` | 2 / false / `setup_incomplete` |
| migrated configuration failure | `[migration_backup, database_migrate, maintenance_configure, viewer_publish]` | `[migration_backup, database_migrate]` | 2 / false / `setup_incomplete` |
| fresh Viewer failure | `[database_initialize, maintenance_configure, viewer_publish]` | `[database_initialize, maintenance_configure]` | 2 / false / `setup_incomplete` |
| migrated Viewer failure | `[migration_backup, database_migrate, maintenance_configure, viewer_publish]` | `[migration_backup, database_migrate, maintenance_configure]` | 2 / false / `setup_incomplete` |
| current Viewer repair success | `[viewer_publish]` | `[viewer_publish]` | 0 / true / `setup_complete` |
| current Viewer repair failure | `[viewer_publish]` | `[]` | 2 / false / `setup_incomplete` |
| missing configuration and Viewer recovery | `[maintenance_configure, viewer_publish]` | `[maintenance_configure, viewer_publish]` | 0 / true / `setup_complete` |
| any preflight failure | `[]` | `[]` | 2 / false / first code from the shared precedence |

If a policy change and Viewer repair are both due, their exact plan is
`[maintenance_configure, viewer_publish]`; success or Viewer failure follows the
corresponding ordered-prefix rule above. A pre-v10 source is unconfigured.
For a configured v10+ source, omitted/equal policy uses the configured-unchanged
rows and never reports `maintenance_configure`; an actual explicit change uses
the policy-change rows. Every success has empty warnings and errors. Every
failure has empty warnings and exactly one error. Fixed messages
for setup-owned errors are: `backup policy is outside the supported range`,
`managed backup could not be restored`, `setup backup could not be completed`,
`project state could not be initialized`, `project state could not be migrated`,
and `setup completed only partially; rerun setup`, corresponding to
`invalid_backup_policy`, `setup_restore_failed`, `setup_backup_failed`,
`setup_initialization_failed`, `setup_migration_failed`, and
`setup_incomplete`. Rerun recomputes the plan and begins with
the first incomplete durable stage; a prior migration backup is evidence, not a
reusable result, so a later migration retry performs a new backup attempt.

The validated backup primitive is implemented once in M14.2 and reused by
M14.3. It uses the SQLite backup API, validates project identity, supported
format, `quick_check`, and foreign keys, closes the temporary database, and
atomically publishes one managed generation. Migration cannot begin if it
fails. Write-mode setup holds the shared zero-wait backup artifact lock from
before reconciliation/publication until the corresponding migration
transaction commits; it never holds a SQLite writer lock while copying.
Contention fails the setup backup stage without migrating. M14.3 reuses the
same lock primitive for routine backup work. Preview never creates a lock or
backup.

Setup-owned recovery adds no public command, path option, prompt, or automatic
action outside explicit setup. It never overwrites an existing canonical
database, including an unreadable one. It reuses the managed filename,
same-project, schema-history, `quick_check`, foreign-key, regular-file,
non-link, and identity checks. Invalid, foreign, linked, and unrecognized
artifacts are never changed or deleted. A newer invalid artifact does not hide
an older valid generation; selection is the newest valid generation. If
canonical managed names exist but no valid same-project candidate remains,
setup fails closed rather than creating empty state.

Recovery copies the selected artifact through the SQLite backup API into a
fresh sibling temporary database. For schema v11 and newer, it normalizes the
temporary database's generation rows to the currently validated managed
artifacts and points maintenance state at the selected generation, because a
managed copy necessarily predates recording its own generation row. Schema v10
updates its setup-copy pointer; earlier schemas carry no managed-generation
metadata. The temporary database is revalidated, flushed, and published with
an atomic no-clobber operation only while the canonical database remains
absent. A lexical rollback-journal entry for the missing canonical database is
unsafe residue: missing-database preflight rejects it before either fresh
initialization or candidate selection, and recovery start plus final
publication revalidate it. Every case fails closed without reading, deleting,
or changing that journal. The shared zero-wait backup lock covers selection
revalidation, recovery, and any
immediately required migration backup/migration. A changed candidate, orphan
canonical rollback journal, or lock contention returns
`setup_restore_failed` without creating or replacing canonical state.
Supported older recovered schemas then follow the existing migration-backup
and migration contract. Successful and partial results report durable
recovered maintenance state, while preview reports that canonical maintenance
is not yet enabled.

Fresh initialization uses that same artifact lock and immediately rechecks
that the canonical database is still absent, no rollback journal exists, and
no valid managed recovery candidate has appeared since preflight. A newly
available candidate returns `setup_restore_failed`; rerunning setup then plans
recovery instead of making empty state.

### Maintenance Bounds

Maintenance is enabled once by successful setup and has no disable surface in
M14. Every successful backup-eligible business mutation commits and closes its
SQLite connection before same-process maintenance; Viewer publication is then
gated to the Viewer-relevant subset. Read-only, failed, replayed, no-op, and
maintenance-internal operations trigger nothing. Taskgov itself starts no
detached process, child process, thread, timer, watcher, service, queue, daemon,
scheduler, or network operation. TG-M15.5's optional timer lives only inside an
already opened generated `file://` page and performs no taskgov process work.

Viewer refresh runs first to honor the near-real-time observation goal; an
independent due backup attempt runs second. Each artifact lock is fail-fast
with a fixed maximum wait of 0 milliseconds. It is an OS-held advisory lock on
one byte of a canonical regular lock file, not ownership inferred from file
existence. Normal release occurs in `finally`; process termination releases the
OS lock, so a leftover regular file is harmless and is never deleted through a
stale-age heuristic. A linked, non-regular, or uninspectable lock path fails
safely. Lock contention preserves the primary result and last good artifact,
emits one sanitized continuing warning, and leaves work due.

Post-commit maintenance warnings have fixed code/message pairs:

| Code | Message |
|---|---|
| `viewer_refresh_deferred` | `Viewer refresh was deferred; task result is unchanged` |
| `viewer_refresh_failed` | `Viewer refresh did not complete; task result is unchanged` |
| `backup_deferred` | `managed backup was deferred; task result is unchanged` |
| `backup_failed` | `managed backup did not complete; task result is unchanged` |

They contain no retry, stop, or choice instruction and never change the
primary command's success.

Routine backups use the project-local policy stored by setup:

- due immediately on the first eligible mutation when no managed backup
  success exists;
- thereafter due after the configured interval, whose default is 30 minutes;
- retain the applied number of successfully published managed generations,
  initially resolved from the configured/default value and normally 3;
- attempt at most once per eligible successful mutation;
- after failure remain due for the next eligible successful mutation; and
- prune recognized older generations only after successful atomic publish.

A setup pre-migration copy is produced by the same primitive and is a managed
generation. Every published artifact carries an internal generation identity,
publication timestamp, and 1-20 `publication_retention` in its canonical
managed filename; the copied database's project identity and the bounded
filename metadata are validated before the artifact is recognized.
Schema v10 stores the latest setup-copy identity, time, and outcome when
migration succeeds from v1-v9. When the source is already v10, the backup stage
must, in order: validate and atomically publish the new copy; update the v10
latest identity/time/outcome and the stage's `publication_retention` as
`applied_backup_generations` in one short transaction; keep that new identity
while pruning to the applied value; and only then begin schema migration. A
fully published copy left newer than the v10 pointer by process termination is
deterministically validated, including its filename retention, and reconciled
to the pointer and applied retention before pruning on the next setup run.
Metadata-update failure prevents migration and pruning.

Before a v10 row can exist, repeated failed migration attempts still apply the
effective explicit policy, or the default policy, to recognized same-project
setup copies: each successful publish precedes pruning, and the number
remaining does not exceed that stage's `publication_retention`. A successful
v1-v9 migration creates the v10 row with the current copy's
identity/time/outcome and applied retention in the migration transaction before
any v11 seeding step. A pre-v10 retry may finish pruning left incomplete by a
prior successful publication only with the newest recognized artifact's own
immutable filename retention. It never applies the new attempt's lower
retention before that attempt publishes successfully.

The v11 migration deterministically discovers every canonical, valid,
same-project managed artifact retained by setup, orders it by publication time
then generation identity, and seeds one generation row per artifact. This
includes the current migration copy and any retained earlier failed-attempt
copies; the v10 latest identity must match one seeded row when non-null.
Unrecognized, linked, invalid, or foreign files are neither imported nor
deleted. Because the current setup copy was successfully published first, the
migration then prunes recognized seeded rows/files to that copy's now-applied
`publication_retention` using the same validated file-before-row order as
routine pruning. Routine publication uses the same ordering and applied
retention set, so setup G0 cannot survive outside the applied generation count.
Backup errors never change the primary command result.

Every v11+ managed backup stage, whether setup migration or routine, holds the
same backup artifact lock and runs the same reconciler before deciding whether
to publish:

1. discover canonical regular artifacts and generation rows;
2. validate and import a fully published same-project artifact that lacks a
   row, updating the v10 latest identity/time/outcome and
   `applied_backup_generations` from that artifact's immutable
   `publication_retention` in the same short transaction;
3. remove a row whose file is missing, linked, invalid, or foreign without
   deleting an untrusted path, recompute the v10 pointer, and keep the outcome
   failed/due when artifact loss occurred; and
4. finish an interrupted prune against `applied_backup_generations` oldest
   first by deleting the validated file before deleting its row, then normalize
   the v10 pointer to the newest retained row. A lower configured policy is not
   used here until a successful publication records it as applied.

If reconciliation or pruning cannot finish, a routine command emits
`backup_failed` and remains due; setup returns `setup_backup_failed`. Neither
path publishes another generation, and setup performs no migration. Otherwise
a due routine backup or setup migration backup atomically publishes one file,
then one short transaction inserts its v11 row and updates the v10 latest
identity/time/outcome plus the artifact's immutable `publication_retention` as
`applied_backup_generations` before pruning with the same file-before-row
order. Process termination after file publish, after the row transaction, or
between file and row pruning is recovered by the next routine or setup
reconciliation. A later policy-only change never alters the retention imported
from an earlier file-only artifact. No new publication occurs while an earlier
inconsistency is unreconciled, so crash residue cannot accumulate beyond the
previously valid retained set plus one in-flight generation. Unrecognized
artifacts remain untouched.

Viewer maintenance reuses snapshot v3, the existing renderer, canonical output,
path protection, and atomic publication. A per-Viewer generation check prevents
older-over-newer publication. One initial render plus at most one follow-up is
allowed per mutation. Remaining churn stays due until the next eligible
mutation. Setup rerun is the only explicit force/repair action. Browser reload
remains manual when the optional TG-M15.5 Viewer profile is absent; a valid
profile may automate only that same local reload.

The hard performance fixture is fixed:

- small: 12 tasks and 191 events using the existing migration-acceptance data;
- large: 500 tasks and 5,000 events;
- generated title, description, and event summary payloads are respectively
  80, 512, and 256 UTF-8 bytes;
- eight successful writes at injected minute offsets
  `[0, 1, 5, 29, 30, 31, 59, 60]`;
- backup attempts are therefore due at offsets 0, 30, and 60 when all succeed;
  Viewer remains eligible on all eight relevant writes;
- each enabled eight-write run must finish within 10 seconds more than the
  matching disabled run, and each individual foreground command within
  5 seconds on the Windows CI fixture.

The eight writes are fixed `task edit <fixture-task-id> --add-note <payload-N>`
mutations against one seeded `in_progress` task, with `N` from `00` through
`07`. Each ASCII payload is deterministically padded so the stored event summary
is exactly 256 UTF-8 bytes. Every scenario starts from an identical copied
fixture and uses the injected clock above. M14.3 compares an internal
coordinator-disabled baseline with backup-only; Viewer is absent. M14.6 compares
the same disabled baseline with Viewer-only by keeping backup not due, then with
Viewer plus due backup. M14.7 repeats the disabled and final combined cases.
These are test seams, never public configuration.

Attempt, render, call, byte, and zero-wait bounds are hard assertions. Wall-clock
limits are deliberately broad; timing cannot waive a deterministic bound.
M14.3 measures backup-disabled versus backup-only. M14.6 measures
Viewer-disabled versus Viewer-only and the due-backup combination. M14.7
repeats final combined integration. A budget failure is a blocking design
finding, not authority to add background architecture.

### Typed Checkpoint

`task checkpoint <task-id>` requires `--summary` and `--next-action`, and
accepts `--unresolved-risk` at most eight times. UTF-8 limits are 1,024 bytes
for summary, 1,024 for next action, 512 per risk, 4,096 for all risks, and
6,144 for the complete caller payload.

One append-only row stores only those fields, task/project identity, source
Contract revision, and timestamp. The same transaction adds one event with
fixed type `checkpoint_recorded` and fixed summary `Checkpoint recorded`; it
never stores checkpoint content in the event or changes `tasks.updated_at`.
Exact replay against the latest checkpoint for the same Contract revision
returns that row with `replayed=true` and writes nothing. Done tasks remain
immutable.

Checkpoint use is optional, never automatic or required at pause, resume,
review, or completion, and adds one bounded content judgment only when the
caller chooses a genuine continuation boundary. It changes no status, scope,
acceptance, priority, selection, review, evidence, or completion gate.
`task current` and `task show` expose only the latest structured checkpoint.
Viewer compatibility does not require publishing its content.

The command emits `command=task.checkpoint`. Data keys are exactly
`checkpoint`, `created`, `replayed`, and `event`. The checkpoint object contains
only `checkpoint_id`, `task_id`, `contract_revision`, `summary`, `next_action`,
`unresolved_risks`, and `created_at`. A new append returns
`created=true`, `replayed=false`, and an event object containing only
`task_event_id`, `event_type="checkpoint_recorded"`, and `created_at`; exact
replay returns
`created=false`, `replayed=true`, and `event=null`. The same checkpoint object
appears under key `latest_checkpoint` in default `task current` rows and
`task show` data. Text is exactly
`Checkpoint <checkpoint_id>: recorded|replayed for task <task_id>` plus one LF.

### Review Packet

`review prepare <task-id>` is read-only stdout generation for every existing
review target kind: `git_snapshot`, `git_commit`, `diff_fingerprint`, and
`external_revision`. A missing target returns `review_target_missing` with
exact message `review target is required before preparing a review packet`.
`git_snapshot` recaptures and validates the stored snapshot. `git_commit`
resolves the canonical commit and lists first-parent changes; a root commit is
compared with the empty tree. The two non-Git target kinds emit an empty path
list with `changed_paths_available=false` and run no Git subprocess. This
target-specific behavior is internal and does not create a Skill/LLM branch.
Revision-zero tasks are supported with Contract revision 0 and empty Contract
fields.

Packet data keys are exactly `task`, `contract`, `review_target`,
`changed_paths_available`, `changed_paths`, `changed_paths_total`,
`changed_paths_truncated`, `review_focus`, `required_output`, and
`receipt_command`. `task` contains only ID, title, status, verification, and
review tier. `contract` contains only revision, scope, acceptance, and
constraints. `review_target` contains only kind, value, base revision, and
generation. It contains no raw diff, review result, receipt import,
stdout/stderr, prompt, conversation, secret, absolute path, or caller-authored
focus.

At most 100 changed paths are emitted, each at most 240 UTF-8 bytes and at most
16,384 aggregate path bytes. Safe rows are retained in bytewise order and
`changed_paths_truncated=true` records count/byte omission. An unsafe path
fails rather than being hidden with code `review_packet_path_unsafe` and exact
message `review packet contains an unsafe project path`. The complete text
stdout or complete compact JSON stdout including its envelope is at most 32,768
UTF-8 bytes; otherwise `review_packet_too_large` is returned with exact message
`review packet exceeds the supported size` and no partial packet. Git
observation uses at most 10 subprocesses. The command
does not launch a reviewer or import a result and replaces separate task,
Contract, target, and Git-context reads without adding an LLM branch.

The focus list contains the four fixed common rows, in order: Contract
compliance; state-transition and completion-gate integrity; privacy and
target-project safety; verification sufficiency and regression risk. Exactly
one fifth fixed row is selected mechanically from the target kind:

- `git_snapshot`: inspect only the matching stage-0 index against the stored
  base revision, using the cached diff and index blobs; exclude unstaged and
  untracked worktree content;
- `git_commit`: inspect the canonical target commit against its first parent,
  or the empty tree for a root commit, and read that commit's tree and blobs
  rather than ambient `HEAD` or worktree content;
- `diff_fingerprint`: do not return `PASS` unless the orchestrator supplies
  exact review material plus evidence binding it to the target value, because
  the fingerprint alone cannot retrieve content; and
- `external_revision`: do not return `PASS` unless exact external material is
  bound to the target value; taskgov does not retrieve external artifacts.

This selection adds no Git subprocess, caller-authored focus, or LLM branch.
The fixed required-output list is: verdict `PASS` or `CHANGES_REQUESTED`;
severity-ordered findings with exact file/line; remaining risks; recommended
changes. The receipt shape is the existing
`taskgov review receipt add <task_id> --reviewer <reviewer-key> --kind
independent --verdict <pass|changes_requested> --summary <sanitized-summary>
--json` argv. It tells the trusted orchestrator how to attest the reviewer's
actual returned result; it is never executed or imported by `review prepare`
and proves no identity.

Text serialization uses LF, a final newline, and the fixed section order
`Task`, `Status`, `Verification`, `Contract revision`, `Scope`, `Acceptance`,
`Constraints`, `Review target`, `Changed paths`, `Review focus`,
`Required output`, `Receipt command`. JSON uses the normal envelope and the
same ordered data fields. After Git observation, a second short coherent
read-only transaction revalidates project identity, task identity, Contract
revision, and every review-target field/generation read initially. Any change
returns `review_packet_stale` with exact message
`review context changed while preparing the packet` and no packet. No SQLite
transaction is held during Git work.

### Schema And Ownership Sequence

M14 uses narrow sequential migrations rather than pre-creating later feature
state:

- M14.1: schema v9 unchanged;
- M14.2: schema v10 adds one-way project maintenance opt-in, bounded mutable
  backup-policy values, and the shared bounded backup last-success/outcome
  fields plus latest managed-generation identity and internal applied-retention
  value used first by the setup copy and later by routine backup;
- M14.3: schema v11 adds managed backup generation rows, deterministically
  seeds all retained valid setup copies, and makes those rows the sole
  retention set; routine backup updates the v10 shared success/outcome/latest
  identity fields rather than duplicating state;
- M14.4: schema v12 adds append-only task checkpoints;
- M14.5: schema v12 unchanged;
- M14.6: schema v13 adds Viewer business/render generation and bounded outcome
  state; and
- M14.7: schema v13 unchanged.

Only setup invokes the internal migration service. Each migration is
transactional, idempotent, rollback-tested, and preserves the realistic 12-task,
191-event, completion/review trace. Older binaries reject newer schemas. Viewer
snapshot version remains v3 and expands source-schema compatibility from 5
through the current staged schema; it excludes maintenance and checkpoint fields
unless a later explicit snapshot contract adds them. M14.0 does not choose a
release number.

Each M14.1-M14.6 unit that changes a release-manifest-covered core file must
refresh that manifest's file inventory/hash in the same reviewed revision.
M14.1 verifies the still-public `self status` result is `clean`. M14.2-M14.6,
after public `self` removal, verify the same shared package inspector through
`doctor.data.components.package.status="clean"` and separately require
`taskgov self status` to fail with the fixed `invalid_command` contract.
Intermediate refreshes do not change release version/origin or publish an
active Skill. M14.7 owns the final release metadata/version decision and
publication synchronization, with the same doctor/package and removed-command
checks.

### Judgment, Privacy, And Deferred Scope

Setup adds one explicit first-use call. Doctor, compact output, backup, and
Viewer maintenance add zero normal-loop calls or judgments. Completion check is
optional and adds one call only when explicitly requested. Checkpoint adds one
bounded content judgment only when used. Review Packet removes context
acquisition calls. The existing Effort Advisory adds one mechanically routed
call only for a valid enabled profile and no LLM judgment; the default-off flow
remains at nine calls. No M14 feature adds a mandatory question, user-return
stop, Issue action, Git write, target mutation, network use, or project test
strategy.

Overview, action aliases, result or receipt-file import, verification receipts,
manual backup, standalone/manual restore, general export, relocation, browser
launch, durable or general browser-state persistence, live server, search,
pagination, Issue lifecycle, generic diagnostics, and workflow automation
remain deferred. TG-M15.6's exact one-shot History API handoff is the sole
browser-state exception.

## Static Task Viewer

The Task Viewer is a generated, self-contained projection of current SQLite
task state. It is maintained only after setup opt-in: setup owns explicit
initial publication and repair, while the bounded same-process post-commit
coordinator refreshes it after Viewer-relevant business mutations. There is no
public `web` command, custom output, Viewer option, or separate model decision.

The only output is:

```text
<installed-skill-root>/state/projects/<project-id>/viewer/task-viewer.html
```

An internally injected database target uses its own database state directory,
so tests and development seams cannot write the installed package's real
Viewer. The canonical parent may be created only after opt-in. Linked,
non-regular, database-alias, or escaping paths fail safely. Publication uses a
temporary regular file in the same directory, flushes it, and atomically
replaces the prior artifact. Failure preserves the last good HTML.

### Snapshot And Read Boundary

Snapshot version 3 accepts source schemas 5 through 13. One lock-respecting
query-only SQLite transaction validates schema and project identity, reads the
Viewer generation, and assembles all snapshot rows. Rendering and file
publication occur after that transaction closes.

The snapshot contains only:

- snapshot version and one UTC `generated_at`;
- project ID and display name;
- source schema version and per-status counts;
- the explicit task-field allow-list used by `task show`;
- at most ten newest sanitized events, receipts, and findings per task.

It excludes repository and database paths, maintenance generations and
outcomes, checkpoints, Contracts not already represented by the task
projection, handoff state, tool events, environment data, and raw evidence.
The deterministic UTF-8 JSON is base64 encoded before insertion into the
bundled template. Browser rendering uses text-only DOM APIs for stored values.

### Maintenance And Freshness

Schema v13 atomically increments one source generation with each
Viewer-relevant task, checkpoint, or review event. Handoff-only, read, failed,
replayed, no-op, setup-configuration-only, and maintenance-internal operations
do not increment it. The Viewer stage:

1. skips without creating a directory before setup opt-in;
2. takes the canonical one-byte OS advisory lock with zero wait;
3. rereads source/render generations and renders only when due;
4. records the published generation in a short transaction;
5. rechecks once and performs at most one follow-up render.

An older capture cannot replace a newer publication. Churn remaining after two
renders stays due for the next eligible mutation. Contention records
`deferred`; another bounded failure records `failed`; both preserve the
primary command result and last good artifact. Doctor reports only the stored
generation/outcome facts and never inspects or repairs the HTML or its optional
presentation profile. `generated_at` remains the visible boundary of the
snapshot currently rendered; browser reload never implies direct database
observation.

### Viewer Experience And Safety

The read-only `file://` application provides project identity, generation
time, all seven status totals, search and status/kind/lane/priority/tag
filters, deterministic task ordering, terminal-history visibility, task
details, review/completion evidence, and recent events. It has responsive
desktop/mobile layout, keyboard access, visible focus, associated labels, and
readable contrast. It provides no task or database write controls.

The generated page contains only bundled inline HTML, CSS, and JavaScript. It
uses no network API, external URL, analytics, telemetry, durable or general
browser persistence, server, direct browser-to-SQLite access, watcher,
automatic browser launch, or task-state mutation. TG-M15.6's only browser
history changes are one-shot replacement/clearing of the current classic state
and its bounded manual scroll-restoration mode; neither changes the URL or
history length. Its exact content security policy and prohibited DOM sinks are
defined and tested in `docs/design.md`.

### Optional Visibility-Aware Reload

TG-M15.5 adds one presentation-only profile at:

```text
<installed-skill-root>/config/viewer.json
```

Taskgov never creates, edits, migrates, or supplies a default copy of this
file. If the canonical file is absent, automatic refresh is disabled, the
rendered interval sentinel is decimal `0`, no browser refresh timeout is
created, and the user refreshes the page manually. A present profile is at
most 16,384 bytes of strict UTF-8 JSON and has exactly:

```json
{
  "schema_version": 1,
  "profile": "visibility-refresh-v1",
  "refresh_interval_seconds": 30
}
```

`refresh_interval_seconds` is a JSON integer from 5 through 3,600 inclusive;
booleans and floating-point values are invalid. Malformed JSON, invalid UTF-8,
duplicate or unknown keys, missing keys, out-of-range values, oversized
content, broken links, symbolic links, reparse points or junctions anywhere in
the config path, and non-regular final objects are invalid. Bounded descriptor
and identity checks fail closed if the file is replaced while read. Errors are
sanitized and expose no path, OS exception, content, or expected/actual
metadata.

One publication attempt observes this profile once and reuses that result for
both of its at-most-two renders. The bundled template contains exactly one
base64 snapshot placeholder and exactly one separate decimal interval
placeholder. A config change, including deletion, takes effect on the next
Viewer-relevant publication. Explicit setup also compares the currently
rendered interval/template with the current profile and plans
`viewer_publish` when they differ.

An invalid present profile makes `setup --read-only` a successful no-write
preview with `viewer_status="repair_required"` and `viewer_publish` in the
planned writes. Actual setup preserves any last-good Viewer and returns the
existing `setup_incomplete` failure. The same invalid profile during routine
post-commit publication preserves the committed primary mutation and last-good
Viewer, records the existing bounded failed outcome, and emits only
`viewer_refresh_failed` for this Viewer/config failure. The independent due
backup attempt still runs second and may emit its own existing bounded warning.
Doctor does not inspect the optional profile and adds no status, warning,
judgment, or stop for it.

After fatal UTF-8 snapshot decoding and the initial DOM render both succeed,
the page enables scheduling only when `location.protocol === "file:"` and the
rendered interval is positive. It records a monotonic load epoch with
`performance.now()` and owns at most one timeout. While
`document.visibilityState` is not `visible`, it clears and owns no timeout and
requests no reload. When the page becomes visible or the timeout fires, it
rechecks visibility and monotonic elapsed time. If at least the configured
interval has elapsed it requests at most one same-document reload for that
loaded page; otherwise it schedules only the remaining delay. Browser
throttling may make a request later but never earlier. Decode or render failure
schedules nothing.

The reload reads only the latest atomically published HTML file. It is not
real-time database observation and may retain an older snapshot until taskgov
successfully publishes another one. TG-M15.5 alone stores no filter, selection,
focus, or scroll state; TG-M15.6 defines the only bounded automatic-reload
handoff below. M15.5 adds no public command or option, normal-loop call, LLM
decision, automatic launch, network access, service worker, storage API,
background taskgov process, SQLite field, or Viewer snapshot field.

### One-Shot Automatic-Reload UI State

TG-M15.6 applies only when the M15.5 scheduler is enabled on a `file:` page and
is about to request its one automatic same-document reload. Immediately before
that existing callback invokes reload, the Viewer may attempt exactly one
`history.replaceState(envelope, "")` call with no URL argument. The reload
continues if reading current state, encoding, validation, or replacement fails.
If `history.state` is non-null and is not a Viewer-owned state, it is neither
overwritten nor cleared and no preservation is attempted. A non-array object
whose `owner` field is exactly `taskgov-viewer-auto-reload` is Viewer-owned
even when the rest of the object is invalid.

The schema-1 envelope has exactly these 13 top-level keys:

```json
{
  "owner": "taskgov-viewer-auto-reload",
  "schema_version": 1,
  "captured_at_ms": 0,
  "status": "",
  "kind": "",
  "lane": "",
  "priority": "",
  "tag": "",
  "terminal": false,
  "selected_task_id": "tg_task_example",
  "scroll_x": 0,
  "scroll_y": 0,
  "focus_id": ""
}
```

The deterministic JSON serialization must be no more than 4,096 UTF-8 bytes
both before replacement and after reading `history.state`. `captured_at_ms` is
a nonnegative safe integer from `Date.now()`. `status` is empty or one of the
seven current statuses; `kind` is empty, `sequential`, or `optional`;
`priority` is empty or one of the four current priorities. `lane` and `tag`
are UTF-8 strings of at most 1,024 bytes each and must be empty or present in
the newly loaded snapshot's corresponding options. `terminal` is Boolean.
`selected_task_id` is a nonempty string of at most 128 Unicode characters; if
the current filtered result has no selection, the Viewer skips envelope save
and still reloads. Each scroll coordinate is a finite number in the inclusive
range zero through 2,147,483,647. The fixed focus allow-list consists of the
empty no-focus sentinel plus exactly
`search-filter`, `status-filter`, `kind-filter`, `lane-filter`,
`priority-filter`, `tag-filter`, `terminal-filter`, or `reset-filters`.
Dynamic task-row or status-tile focus is captured as the empty sentinel and is
not restored.

The envelope never contains search text, task title or description, event,
receipt, finding, snapshot data, option lists, URL, query, fragment, file or
project path, arbitrary selector, caret or text selection, dynamic task-row
focus, or nested-panel scroll. It uses no cookie, `localStorage`,
`sessionStorage`, IndexedDB, Cache API, service worker, network request,
database record, or snapshot field.

At the start of a `file:` page load, before snapshot decoding, the Viewer reads
`history.state` at most once. It performs no further M15.6 work unless
automatic refresh is enabled or that value is Viewer-owned. An owned state is
cleared immediately with
`history.replaceState(null, "")`, again with no URL argument. This clear occurs
even when the state is malformed, stale, from a non-reload navigation, or the
snapshot later fails to decode. If clearing fails, nothing is restored.
If reading or clearing throws, the page continues with its normal snapshot and
default UI while the existing M15.5 scheduler remains independent. Non-`file:`
pages neither read nor change History state for M15.6.

On that same eligible `file:` load, the Viewer must successfully set
`history.scrollRestoration` to `manual` before snapshot/UI restoration. It
first requires `"scrollRestoration" in history`, then assigns `manual`, then
requires a readback equal to `manual`; this prevents an unsupported expando
property from appearing supported. The mode prevents the user agent's own
reload scroll restoration from competing with the bounded document
coordinates. If the property is unavailable, rejects `manual`, or throws,
M15.6 save and restore are disabled for that loaded page; an already owned
state is still cleared without restoration, and the M15.5 timer and reload
remain enabled.

A `history.state` read exception on an auto-refresh-enabled `file:` page does
not skip the manual scroll-mode attempt. The unreadable state disables envelope
save and restore for that page, while a successful manual-mode readback still
prevents user-agent reload scroll from competing with the normal default
`(0, 0)` fallback.

After a successful clear and snapshot option initialization, restoration
requires the current navigation entry to report type `reload`, exact keys,
owner and version, valid primitive types and bounds, a capture age from zero
through 300,000 milliseconds inclusive, current lane and tag membership, a
selected task that still exists and is visible under the candidate filters,
and any nonempty fixed focus target to exist. Initialization explicitly resets
search and every filter to the Viewer defaults before optional envelope
application, so excluded form state is not adopted from browser restoration.

Valid filters are assigned before one task-list/detail render. That render
restores selection; fixed-control focus is then restored without preserving
caret or selection, and document scroll is restored last. Invalid, stale,
oversized, unsupported, unavailable, or otherwise failed restoration renders
the normal default UI and explicitly scrolls the document to `(0, 0)`. Owned
state is consumed even when rejected, so a later reload starts from defaults
unless the M15.5 scheduler creates a new valid envelope. The reload timer
remains independent of every save or restore outcome. If fixed focus succeeds
but the following scroll operation fails, fallback best-effort blurs only that
just-restored fixed control before resetting filters and selection, rerendering
the defaults, and attempting `(0, 0)` scroll. Blur failure is contained and
does not prevent the remaining fallback.
No ignored state, serialization content, browser exception, URL/path, or
validation detail is written to the page, console, snapshot, or taskgov output.

History state is browser-managed and may survive in a session-restored history
entry; it is not guaranteed to be memory-only. TG-M15.6 therefore relies on
one-shot owner validation, the five-minute limit, the 4,096-byte cap, and
clear-before-restore rather than making a durability claim. It never calls
`pushState`, never supplies a URL argument to `replaceState`, and leaves the
current URL, classic state payload, and history length unchanged when setting
the scroll mode. Setting `scrollRestoration` updates the current entry's
user-agent scroll-restoration mode but does not add an entry or change its URL.
Manual reload never creates a new envelope. If automatic navigation is
interrupted after save, the outstanding bounded envelope may be consumed by
the next qualifying reload within five minutes.

TG-M15.6 adds no command, option, configuration choice, Task-loop call, LLM
judgment, user-return stop, automatic browser launch, database/schema field,
Viewer snapshot field or version, network behavior, or CSP relaxation.
Release v0.8.0, schema v13, Viewer snapshot v3, compact JSON, and the 20-leaf
CLI remain unchanged.

Acceptance requires schema-5-to-13 snapshot compatibility, privacy allow-list
tests, atomic/last-good path tests, zero-wait and two-render bounds, Viewer-only
and Viewer-plus-backup performance fixtures, offline `file://` usability, and
exclusion of generated HTML from source and release artifacts. TG-M15.5 also
requires strict config/path/replacement tests, absence-as-disabled behavior,
setup preview/failure and routine last-good behavior, exact two-placeholder
rendering, structural scheduling/CSP/storage/network tests, and a real
`file://` browser forward test. Package/archive checks must prove that the
optional `config/viewer.json` is absent from the release artifact while the
loader and changed runtime sources remain manifest-covered. Automatic browser
launch remains deferred.

TG-M15.6 acceptance additionally requires automatic `file:` reload save,
reload-only consume, clear-before-restore, and second-reload-default browser
tests. Tests reject wrong ownership/version/keys/types/bounds/age/navigation,
oversized encodings, missing dynamic options, invisible or missing selection,
and unavailable focus; preserve unknown non-Viewer state; exercise save and
clear failures; and prove exclusions, unchanged URL/history length, existing
timer/visibility bounds, required/manual scroll-restoration fallback, exact
CSP, no storage/network API, no console/UI disclosure, and no command, schema,
snapshot, or normal-loop change.

## Approved Post-MVP Extension: TG-M16 Reduced Loop Discipline Trial

TG-M16 is implemented as a reduced behavioral trial. TG-M16.0 fixes this formal
contract before any runtime or active-Skill change. TG-M16.1 owns only Effort
action routing, TG-M16.2 owns concise Test Repair and Scope Reconciliation
guidance, and TG-M16.4 owns final package synchronization and behavioral
acceptance. The former TG-M16.3 setup bootstrap and consuming-project
instruction-adoption unit is cancelled.

### Effort Reconciliation Signal

TG-M16.1 retains the existing profile, metrics, observation point, warning
code `effort_advisory_threshold_exceeded`, warning key
`effort_advisory.threshold_exceeded.v1`, and message
`One or more configured effort thresholds were exceeded.` It changes only the
action selected after one observation:

| Profile and observation | `data.suggested_action` | Threshold-warning action |
|---|---|---|
| absent or valid disabled | `continue` | no threshold warning |
| invalid present profile | `continue` | no `task effort` threshold warning; the existing `task show` invalid-profile warning remains `continue` |
| valid enabled, `exceeded` empty, attribution known | `continue` | no threshold warning |
| valid enabled, `exceeded` empty, attribution unknown | `continue` | no threshold warning |
| valid enabled, `exceeded` nonempty, attribution known | `reconcile_scope` | `reconcile_scope` |
| valid enabled, `exceeded` nonempty, attribution unknown | `reconcile_scope` | `reconcile_scope` |

The nonempty `exceeded` list is the sole action predicate after valid
enablement. Unknown attribution does not erase a deterministically observed
threshold exceedance, but an unknown-only observation does not invent one.
Data and warning actions must match. Any number of exceeded metrics in the
same result creates at most one session-local reconciliation episode.

`reconcile_scope` is non-blocking. The signal never asks the user, records a
handoff, changes Task status, Contract, acceptance, review tier, review or
completion evidence, pauses, blocks, or fails a Task. It does not add a second
observation, command, or judgment to the green path. The default-off
no-finding Tier 2 flow remains at nine governance calls and the enabled flow at
ten. TG-M16.2 guidance, not the deterministic Effort result, owns any later
scope classification.

### Session-Local Test Repair And Scope Reconciliation

TG-M16.2 adds concise guidance, not a persisted state machine. A reconciliation
episode first diagnoses the evidence and repair boundary. Without new
evidence, after two materially equivalent failed repair attempts the agent
must not execute a third materially equivalent retry. Changing only command
spelling, working directory, runner wrapper, Task label, or execution-unit
label is not new evidence. A safe diagnostic result is new evidence only when
it can materially change the causal hypothesis, authorized repair, or expected
outcome. Diagnostic work and a genuinely different evidence-backed repair are
not prohibited by the two-attempt bound.

Tests must not be weakened merely to obtain a pass. A test may be changed when
current governing authority shows that the test is wrong, but a Contract or
acceptance change still requires later explicit authority; neither a failure
nor an advisory signal supplies that authority.

Classification reuses the existing Responsibility And Continue-First Rule:

1. Keep work in the current Task only when resolving it is within its accepted
   scope and can proceed under current authority. This includes work required
   by current acceptance and regressions introduced by the current Task.
2. A failing test alone establishes neither accepted scope nor current
   authority. An out-of-scope discovery uses the existing immediate local
   handoff.
3. Record a blocker only after safe authorized work for the affected Task or
   lane is exhausted. Reserve `paused` for an explicit temporary interruption.
4. Continue unrelated safe ready lanes. Batch any remaining user decisions
   after that independent work rather than returning once per discovery.

Existing review gates remain authoritative. A current-generation
`changes_requested` receipt or an unresolved high or medium finding blocks
completion; historical receipts, PASS receipts, and low findings do not
independently add a stop. A meaningful fix resolves applicable findings,
advances review evidence to a fresh target, and obtains a fresh
current-generation review result. A result that remains blocking counts as one
unsuccessful remediation cycle; completion still requires fresh qualifying
PASS receipts. Without new evidence, two unsuccessful materially equivalent
review remediation cycles prohibit a third equivalent cycle. The agent then
uses one bounded existing decision or blocker path for the affected work while
unrelated safe lanes continue.

All attempt counting is session-local and may reset in a fresh session; durable
Task, event, finding, and receipt records remain the only rediscovery state.
TG-M16 adds no attempt table, counter, latch, acknowledgement, semantic failure
parser, automatic Task/Contract/status/handoff mutation, mandatory checkpoint,
project-specific test strategy, or workflow engine.

### Setup, Adoption, And Acceptance Boundary

TG-M16.3 is withdrawn. Setup creates no policy or bootstrap Task, performs no
instruction-chain audit, and does not create or edit a consuming project's
`AGENTS.md` or other instructions. Setup JSON, Viewer maintenance, task
selection, schema v13, Viewer snapshot v3, release v0.8.0, and the 20 command
leaves remain unchanged.

TG-M16.4 synchronizes the concise Skill entry point, one failure-only
one-level reference, package guidance and manifest, then pressure-tests fresh
sessions for the exact Effort truth table, two-attempt Test Repair boundary,
review remediation, existing scope/blocker/handoff classification,
unrelated-lane continuation, batched decisions, and durable Task rediscovery
with session-local retry counts reset. It installs nothing, edits no consuming
project, and performs no instruction adoption. Any broader project-policy
adoption requires a later separately approved design and is not a standing
handoff from TG-M16.

## Implemented Post-MVP Extension: TG-M17 Stable Project Identity And Relocation

TG-M17 separates immutable project identity from mutable filesystem binding.
This section is the implemented release-v0.9.0/schema-v14 contract.
TG-M17.1 through TG-M17.4 were non-public staging revisions; TG-M17.5
synchronized the accepted fixed resolver and relocation behavior into the
release package.

The final TG-M17 surface remains exactly 20 command leaves. The only added
public option is `setup --confirm-relocation <token>`. Viewer snapshot remains
version 3 and accepts source schemas 5 through 14. The normal default-off Task
flow remains at no more than nine governance calls and an enabled Effort
Advisory flow at no more than ten. Setup and doctor remain outside that normal
flow.

### Identity, Binding, And Fixed State

Schema v14 supports exactly two identity schemes:

- `legacy_path_v1`: every database migrated from schema v1 through v13 keeps
  its existing `project_id` byte-for-byte;
- `uuid_v1`: a fresh production setup creates one random UUIDv4-backed
  `project_id` formatted as `tg_project_<32-lowercase-hex>`.

TG-M17 never rewrites a Task, event, Contract, handoff, review, completion,
maintenance, or Viewer-generation `project_id`. A missing fresh database has
no durable identity: doctor, normal-command errors, and `setup --read-only`
use top-level `project_id=null`; only write-mode setup creates and persists the
UUID once.

The current binding consists of a positive generation, the existing full
64-lowercase-hex canonical-path SHA-256, and a non-authoritative display name.
The hash algorithm remains the implemented `canonical_path_v1` algorithm so
legacy values compare exactly: resolve the governed root without requiring it
to exist, use the platform-normalized absolute spelling, UTF-8 encode, and
SHA-256 hash. The display name is derived only from the root basename, replaces
control and line-separator characters with U+FFFD, falls back to `project`,
and is limited to 200 Unicode code points. Neither value is project identity.

Schema v14 records the identity scheme and current binding in `project_meta`
and one append-only binding-history row for each generation. Each row stores
only the project ID, generation, prior and current hashes, bounded display
name, one fixed reason, an optional relocation-token digest, and canonical UTC
time. Reasons are exactly `legacy_migration`, `fresh_setup`, and
`confirmed_relocation`. Generation 1 has no prior hash or token digest.
Confirmed relocation stores SHA-256 of the complete accepted token, never the
token itself. Raw absolute paths are prohibited from the new project identity,
binding, cleanup, and token-history metadata. Existing sanitized Task and
business-text privacy rules are unchanged; TG-M17 does not retrospectively
redefine an ordinary governing-file reference as identity metadata.

`project_meta` also owns one internal legacy-cleanup pending bit, an optional
bounded canonical source-inventory JSON value, and its optional 64-hex
fingerprint. All three are null/false normally. Staged legacy publication sets
them before fixed state becomes canonical; successful cleanup clears them.
The inventory contains at most 32 of the exact recognized relative artifact
names defined below, is at most 16,384 ASCII/UTF-8 bytes, and contains no
absolute path or unrelated filename. These fields authorize only setup's exact
old-layout retirement, not identity or binding change.

The final canonical generated targets are fixed under the one supported
physical package:

```text
<physical-skill>/state/current/taskgov.sqlite
<physical-skill>/state/current/backups/
<physical-skill>/state/current/viewer/task-viewer.html
```

The ordinary project-scoped and bounded development self-host package rules
remain unchanged. The package `state/` directory remains the ignore operand
and generated-state boundary. Public alternate state or database paths remain
unavailable.

One shared resolver owns all production database and artifact targets. It
derives only the current root hash and bounded display name from `--repo`, then
reads the stored identity and binding from fixed state. Setup, doctor, every
Task/handoff/review leaf, backup, Viewer, maintenance, and recovery consumer
must not reconstruct an identity or canonical state path independently.
Internal explicit target injection remains test-only.

With an authoritative fixed primary, ordinary Task/handoff/review resolution
validates that primary's identity, schema, integrity, and binding but does not
open retained backups or gate business-state availability on the
non-authoritative Viewer projection. Setup uses the same resolver's
deterministic deep-validation projection before repair or mutation. Fixed
missing-primary recovery, legacy discovery, and private-stage publication also
retain full backup/Viewer/lock/artifact validation. This caller-owned
mechanical distinction adds no command, LLM choice, question, or normal-loop
stop.

### Legacy Discovery And Compatibility

When the fixed primary is absent, the shared resolver used by every DB-backed
leaf may inspect only direct children of
`<physical-skill>/state/projects`; it does not search a repository, parent
directory, user-wide install, link target, or network location. Setup and
doctor consume the richer plan/diagnostic projection; normal leaves use the
same read-only observation only to return the exact state result below.
Enumeration stops and fails closed after 64 entries. Every inspected component
must be lexically and canonically contained, physical, non-link,
non-junction/non-reparse, and of the expected regular-file or directory kind.
An unknown direct child of `state/projects` is left untouched and makes source
selection unreadable rather than permitting fresh initialization.

One legacy candidate requires all of the following:

- exactly one physical candidate directory and either one regular
  `taskgov.sqlite` or a missing primary with at least one valid managed backup;
- directory basename equal to the database's stored legacy `project_id`;
- source schema 1 through 13, or the schema-v14 legacy-layout transition state
  produced while TG-M17.1 is active before TG-M17.2;
- implicit pre-v14 or explicit `legacy_path_v1` identity;
- contiguous migration history, one project row, successful `quick_check`,
  no foreign-key violation, and supported rollback-journal state;
- valid same-project artifact metadata at every recognized managed path, with
  generation rows inside the existing reconciler's retained-set-plus-one
  in-flight envelope;
- every canonical or recognized owned path is physical and safe.

Other entries *inside* that one candidate directory are not candidate input.
The resolver does not traverse, open, copy, fingerprint, rename, or delete
them, even when their name ends in `.sqlite`; they remain in the old directory
after migration. A link or unsafe object at a canonical database, recognized
managed-backup, Viewer, lock, or exact recognized temporary path fails closed;
an unrelated entry at another name is preserved.

The only recognized legacy crash-temporary basenames are one file per class:

```text
database directory  .taskgov-restore-[a-z0-9_]{8}.tmp
backups directory   .taskgov-backup-[a-z0-9_]{8}.tmp
Viewer directory    .task-viewer-[a-z0-9_]{8}.tmp
```

Each must be a non-link regular file no larger than the source database size
plus 16,777,216 bytes. A class is recognized only when exactly one matching
basename exists. If two or more basenames match one class, every match in that
class is unrelated and preserved; none is selected by enumeration order. A
nonmatching random length/alphabet or an oversized file is likewise unrelated
and preserved, not deletable tool state. Publication preflight only classifies
these temporaries; it does not reconcile or delete them before fixed state is
durable.

The canonical backup and Viewer lock files are recognized for retirement, but
are never copied as durable state into the fixed layout. They are released
before cleanup, included in the cleanup inventory when present, and removed
only through that inventory. Other lock-like names are unrelated and
preserved.

A candidate-shaped invalid or unsafe canonical entry, more than one candidate,
multiple project identities, foreign backup metadata, or a newer schema fails
with the exact sanitized boundary below and changes nothing. It never falls
through to recovery or initialization.

Source selection is deterministic:

1. An existing fixed primary is authoritative. If it is unreadable or corrupt,
   setup never replaces it from a backup or legacy candidate.
2. With no fixed primary, recognized fixed-layout managed backups retain the
   existing missing-primary recovery precedence. Valid generations must carry
   one identity scheme and one coherent binding lineage.
3. With neither fixed primary nor usable fixed recovery source, exactly one
   valid legacy candidate may be considered.
4. Only when none of those durable sources or candidate-shaped residues exists
   may setup initialize fresh state.

A fixed backup-only recovery whose selected newest valid generation is bound
to a different current root fails closed as `project_state_unreadable`.
TG-M17 does not infer a move from backup material or extend relocation
confirmation to that third source kind. Older retained valid generations may
have earlier bindings only when their identity and scheme match and their
history, or implicit pre-v14 generation 1, is an exact prefix of the selected
generation's history. They remain retained but are not selected over the
mechanically newest valid generation.

A confirmed fixed-state rebind does not synthesize an extra backup merely to
close this lineage. Existing pre-rebind generations remain valid retained
history, but none has the new binding head until the next successful managed
backup. If the fixed primary is lost during that bounded interval, setup fails
closed as `project_state_unreadable` rather than restoring an old-binding
generation. This explicit residual preserves the existing backup schedule and
write vocabulary; it is not a reason to add a synchronous rebind backup.

Legacy discovery preserves the existing M14 crash-reconciliation envelope.
The normally retained set is at most 20 generations, but it may have exactly
one additional canonical, valid, same-project in-flight generation. Source
validation allows no more than 21 canonical same-project artifacts, no more
than 21 generation rows when that table exists, no more than 21 distinct
generation identities across their union, and only a state accepted by the
existing schema-specific reconciler or recovery normalizer. For schema v11+
with a present primary, it permits no more than one artifact lacking its
generation row or one generation row whose file is absent from an interrupted
file-before-row prune, never both. When the primary is missing and the newest
backup is the database source, that snapshot normally lacks its own later row:
the sole file-only artifact must be that mechanically selected newest backup,
and zero through 20 of its older snapshot rows may lack files already removed
by completed or interrupted retention pruning. The existing recovery
normalizer must accept every removal/import, and the artifact/row union remains
bounded to 21 identities.
For schemas v1 through v10, absence of the v11
generation table is not a file-only exception: v10 uses its existing pointer
rules and v1-v9 use their existing newest-artifact retention rules. This
permits all 21 row-backed files after a newly published
artifact lowered applied retention but the process stopped before pruning.
The in-flight generation may be file-only before its row transaction or
coherent after that transaction; it participates in newest-candidate
selection, including when the legacy primary is absent.

A second v11+ file-only artifact; a second missing-file row with a present
primary; more than 20 missing rows in the exact missing-primary relation; more
than 21 artifacts, rows, or union identities; divergent identity/binding
lineage; or any state the existing reconciler/normalizer rejects fails closed.
Discovery and source locking do not import, delete a stale row, prune, or
otherwise change the legacy source. In the private fixed stage,
missing-primary recovery normalization first removes zero through 20 stale
snapshot rows and imports the selected newest artifact in one bounded pass.
The existing version-specific reconciler then runs to bounded convergence
before any migration backup: present-primary v11+ imports one missing row,
removes one stale row, or prunes the coherent post-row set and may require at
most two passes, while v10 and pre-v10 use their pointer/prune normalization.
Only the private copy is reconciled. Every present valid source artifact,
including the selected recovery file, remains in the persisted cleanup
inventory; a row-only missing file has no filesystem entry to inventory.

A legacy candidate whose primary or newest valid backup has a stored hash
equal to the current root is a same-binding layout migration and requires no
relocation token. When its primary is missing, setup restores the newest
coherent managed backup into the private fixed-layout stage; it never recreates
the old primary. A primary-backed legacy candidate with a differing hash is a
relocation candidate and remains strictly no-write until TG-M17.3
confirmation. A moved backup-only legacy candidate fails
`project_state_unreadable` because recovery would be the unsupported third
relocation source.

All schema v1 through v13 inputs migrate sequentially and idempotently to v14,
preserving every durable record and completion trace. The migration matrix
tests each source version. Final end-to-end acceptance additionally exercises
at least one complete pre-v9 fixture, one v13 fixture, and the schema-v14
legacy-layout transition state. Viewer snapshot v3 reads sources 5 through 14;
earlier sources must complete setup migration before Viewer publication.
Migration deterministically normalizes the old non-authoritative display name
with the same control replacement, fallback, and 200-code-point bound used for
new bindings; ordinary valid names are unchanged. This is not a business-record
or identity rewrite.

### Setup Relocation Confirmation

A path mismatch proves neither move, copy, nor fork intent. Normal commands
and doctor never rebind. Normal commands return
`project_relocation_required` without a business, SQLite, artifact, Git, or
target-project write. Doctor remains successful, read-only, and fixed at
`suggested_action="continue"`; it reports project-state code
`relocation_required`, the same warning, and `setup_eligible=true` when every
other precondition passes.

Write-mode `setup` without a token also returns
`project_relocation_required` and performs no write. Only
`setup --read-only` returns a successful `status="relocation_preview"` with
the exact future plan and a confirmation token. Supplying
`--confirm-relocation` with `--read-only` is rejected before project or state
resolution as `invalid_option_combination`. Setup never prompts.

Token issuance is not user approval. In the Skill-driven flow, the agent must
present the relocation requirement and bounded plan, stop for the user's
explicit current approval, and only then pass that exact token to write setup.
It must never turn preview into confirmation autonomously. A token that expires
or becomes stale before use requires a new preview and new approval. A user who
directly invokes write setup with `--confirm-relocation` has supplied that
explicit intent outside the Skill mediation. This exceptional one-decision
boundary occurs only on a proven mismatch and adds nothing to the normal
nine/ten-call Task loop.

The token is an accidental-misapplication checksum and bounded approval
transport, not authentication, a signature, a secret, or protection from a
malicious local process. Its exact ASCII form is:

```text
tgr1.<unpadded-base64url-canonical-json>.<64-lowercase-hex-checksum>
```

The checksum is SHA-256 of the ASCII bytes `tgr1.<payload>`. The decoded JSON
uses UTF-8, rejects duplicate keys, and has exactly these sorted canonical
keys:

```text
binding_generation
expires_at
identity_scheme
issued_at
new_path_hash
old_path_hash
project_id
source_layout
source_schema_version
v
```

`v` is integer 1; `source_layout` is exactly `fixed_current_v1` or
`legacy_projects_v1`; hashes are 64 lowercase hex; the generation is positive;
and the schema is a supported observed source. JSON uses sorted keys,
`(",", ":")` separators, and no insignificant whitespace before unpadded
base64url encoding. `expires_at` is exactly 900 seconds after `issued_at`.
The token is accepted only while `issued_at <= now < expires_at`, is at most
2,048 ASCII bytes, re-encodes canonically, and passes constant-time checksum
comparison.

The token binds consent to the stored project ID, identity scheme, current
generation/hash, proposed hash, source layout, source schema, and validity
window. It deliberately does not fingerprint Task/business content:
confirmation authorizes a binding change, and the actual command migrates the
latest coherent source observed under its locks. Confirmation revalidates all
token fields, package/install/ignore state, source inventory and integrity,
destination state, and the current root hash immediately before durable work.

Malformed structure, types, checksum, time relation, or future issue time is
`relocation_token_invalid`; expiry is `relocation_token_expired`; a
well-formed token whose bound source or proposed context changed is
`relocation_token_stale`; an already successful token is
`relocation_token_used`; and a valid-context token supplied when no relocation
is required is `relocation_not_required`. A successful-token replay always
fails. A later token-free setup at the rebound path is the normal idempotent
setup or cleanup/Viewer-repair path.

After ordinary package/project/state preflight, token-result precedence is
structural/checksum validity, a digest already present in binding history,
expiry, stale context, then not-required context. Consequently, every
structurally valid successful-token replay returns `relocation_token_used` even
after its original validity window. No lower-precedence result reveals which
payload field differed.

The fixed sanitized errors and messages are:

| Code | Message |
|---|---|
| `project_relocation_required` | `project state is bound to a different project location; run setup --read-only` |
| `relocation_token_invalid` | `relocation confirmation is invalid` |
| `relocation_token_expired` | `relocation confirmation has expired; run setup --read-only again` |
| `relocation_token_stale` | `project relocation state changed; run setup --read-only again` |
| `relocation_token_used` | `relocation confirmation has already been used` |
| `relocation_not_required` | `project relocation is not required` |

These setup service errors use exit 2. Parser-level
`invalid_option_combination` uses the existing usage-error exit 1. No rejected
token, hash, path, source count, filename, SQLite/OS exception, or validation
detail is echoed.

### Setup JSON And Durable Write Order

TG-M17.3 adds one always-present `data.relocation` object to setup results with
exactly:

```text
required
source_layout
identity_scheme
binding_generation
confirmation_token
expires_at
```

`required` is boolean. Other values are bounded scalar values or `null`;
`confirmation_token` and `expires_at` are non-null only in a successful
relocation preview. No path or hash appears outside the opaque token. Fresh
preview has a null top-level project ID, a false `required`, and null identity,
generation, token, and expiry. Existing matching state reports its scheme and
generation without adding a normal-loop branch.

Relocation-related setup failures follow the existing setup scalar rules:
`status` is `null`; `schema_to` is 14; `schema_from` and the maintenance,
policy, and Viewer fields are the safely observed/effective values when
ordinary preflight established them, otherwise null as for a shared-preflight
failure. The top-level project ID is the safely established stored value or
null. Every error has `confirmation_token=null` and `expires_at=null`; no
rejected token field is reflected. The remaining values are exact:

| Setup error | `planned_writes` | `completed_writes` | `relocation.required` and context |
|---|---|---|---|
| mismatch, no token: `project_relocation_required` | the same ordered future plan `P` that read-only preview would return | `[]` | `true`; safely observed source layout, scheme, and generation |
| `relocation_token_invalid` | `[]` | `[]` | current mechanically observed mismatch boolean; safely observed context or null |
| `relocation_token_expired` | `[]` | `[]` | current mechanically observed mismatch boolean; safely observed context or null |
| `relocation_token_stale` | `[]` | `[]` | current mechanically observed mismatch boolean; safely observed context or null |
| `relocation_token_used` | `[]` | `[]` | current mechanically observed mismatch boolean; safely observed context or null |
| `relocation_not_required` | `[]` | `[]` | `false`; safely observed source layout, scheme, and generation |

Here `P` includes migration/configuration, binding, Viewer, and pending-cleanup
stages applicable to that source under the durable ordering below. All six
rows exit 2 with `ok=false`, empty warnings, and exactly their fixed error.
Supplying a token cannot convert an earlier package, project, ignore, unsafe,
busy, newer-schema, corrupt-state, or ambiguous-source result into one of
these rows; the earlier shared preflight row and its existing null/empty data
contract wins.

Once a fixed or legacy database identity has passed validation, the envelope's
top-level `project_id` is that stored ID, including relocation preview/error.
It is `null` for fresh/missing state and for an invalid source whose identity
was not safely established.

The setup write vocabulary adds exactly `legacy_state_publish`,
`project_binding_update`, and `legacy_state_cleanup` to the existing values.
For any path, `planned_writes` and `completed_writes` use this durable stage
order:

1. a source prefix of exactly one of `[]`, `[database_restore]`,
   `[legacy_state_publish]`,
   `[database_restore, legacy_state_publish]`, or
   `[database_initialize]`; the two-stage prefix is only same-binding
   legacy-layout missing-primary recovery;
2. `migration_backup` and `database_migrate` when required;
3. `maintenance_configure` when required;
4. `project_binding_update` only for confirmed mismatch;
5. `viewer_publish` when required;
6. `legacy_state_cleanup` only after fixed database, binding, maintenance, and
   regenerated Viewer state are durable.

A same-binding legacy source plans no `project_binding_update`. A fixed current
database without valid pending cleanup plans no `legacy_state_publish` or
cleanup; when its pending record is valid, setup alone plans
`legacy_state_cleanup` after any due Viewer repair. A moved legacy source plans
publication, any schema/configuration stages, binding update, Viewer
publication, then cleanup. Before fixed publication, all legacy work occurs in
one private, contained staging directory; staging writes are not reported as
completed. The staged database and recognized artifacts are validated, and
the complete `state/current` directory is published atomically without
clobbering an entry that appeared concurrently. Publication makes every
preceding staged stage durable together; the result still lists their ordered
prefix.

The private stage is owned by one separate sibling marker; its random basename
alone never authorizes deletion:

```text
state/.current-stage-<32-lowercase-hex>
state/.current-stage-<same-32-lowercase-hex>.owner
```

After source inventory validation and while holding the transition lock, setup
creates the owner file exclusively and durably before creating the directory.
The owner is canonical ASCII JSON no larger than 2,048 bytes with exactly
`v=1`, the matching `stage_id`, validated `project_id`, and
`inventory_fingerprint`; it uses sorted keys, `ensure_ascii=true`,
`(",", ":")`, and duplicate-key rejection. It contains no path and is a
physical non-link regular file. The directory may contain only physical
directories `backups` and `viewer` and at most 32 physical regular files total
from this exact
allow-list: the staged database and optional rollback journal, up to 21 exact
managed backups, canonical backup and Viewer lock files, Viewer HTML, and at
most one exact bounded temporary from each existing class. Every non-marker
file is no larger than the source database plus 16,777,216 bytes.

Write setup may remove one owner/stage residue only when the marker is
canonical and matches the directory suffix, every component is contained and
non-link/non-reparse, the allow-list/count/size rules hold, and no unknown
entry exists. It removes explicit files and then proven-empty directories; it
never recursively deletes an unvalidated tree. A valid orphan owner with no
stage is removed by itself, including after successful directory publication.
Read-only setup never removes either member and may continue to the ordinary
preview because the next write can recover it; this internal residue cleanup
does not add a public planned-write value. A valid orphan owner beside an
authoritative fixed primary does not block normal Task/handoff/review leaves.
A stage without its owner, a
mismatched/invalid owner, more than one residue pair, an unsafe component, or
an unknown/oversized entry returns `setup_incomplete` with `schema_to=14`,
all other setup scalars and project ID null, empty write arrays, false/null
relocation fields, exit 2, and no deletion in both read-only and write mode.

Failure before atomic fixed publication removes only an owner-validated
staging residue and leaves the legacy source unchanged. Failure after fixed
publication but before cleanup leaves valid fixed state authoritative and the
legacy source intact. Fixed-primary resolution does not resume general legacy
discovery; instead, only setup observes the current database's pending flag.
It derives the old directory from the stored project ID and the fixed-length
retirement directory
`state/.legacy-cleanup-<sha256(project-id-utf8)>`, using all 64 lowercase hex
characters rather than the possibly long legacy ID. A collision or entry not
authorized by the pending record fails closed. A token-free setup resumes
Viewer repair or this bounded cleanup. Token replay remains an error.

Before fixed publication, setup persists this exact canonical JSON inventory
in the staged database and stores its SHA-256 fingerprint:

```json
{"entries":[{"kind":"file","name":"relative/posix/name","sha256":"64hex","size":0}],"v":1}
```

Entries are sorted by UTF-8 bytes of `name`; object keys are sorted; JSON uses
UTF-8, `ensure_ascii=true`, and `(",", ":")`; `size` is a nonnegative byte
count. There are 1 through 32 unique entries and the complete canonical JSON
is at most 16,384 bytes. Repository validation reparses it with duplicate-key
rejection, validates every relative POSIX name against the fixed recognized
artifact grammar, requires canonical byte-for-byte re-encoding, and requires
its SHA-256 to equal the stored fingerprint. It includes only the canonical
database when present, at most 21 valid managed backups in the exact
retained-set-plus-one-in-flight envelope, last-good
Viewer, canonical backup and Viewer lock files, and at most one exact bounded
temporary in each of the three classes above. Unrelated files are excluded and
stay in the legacy directory.

Cleanup atomically moves each recognized entry without replacement from its
old relative location to the same relative location under the retirement
directory. The database inventory, not a fresh directory enumeration, is the
sole allow-list. For each inventory entry, both old/retirement locations is an
error; an old-only or retirement-only file must match its stored kind, size,
and SHA-256; and neither means that entry is already absent. Setup first moves
and verifies every still-present old entry. It begins deletion only after no
recorded old entry remains, then deletes verified retirement entries one at a
time. A retry uses the persisted inventory to continue moving or deleting only
the recorded remaining subset. New or changed content at a recorded path
fails, and an unrecorded retirement entry prevents deletion.

Cleanup removes only those recognized files and proven-empty owned
subdirectories; the old project directory remains when unrelated entries
exist. When neither old nor retirement location contains a recorded entry,
filesystem cleanup is already durable and setup clears the pending bit,
inventory, and fingerprint in one short fixed-DB transaction. This covers a
crash after the last delete but before the metadata update. A changed
recognized source, collision, or unrecorded retirement entry yields
`setup_incomplete` for setup; it does not make an otherwise valid fixed
database unreadable. Unrelated old entries are never traversed, recursively
deleted, overwritten, or treated as another candidate. Normal Task commands
remain usable from valid fixed state while cleanup is pending or its
filesystem retirement is incomplete.

The following symbols make the setup rows exact:

- `M` is `[migration_backup, database_migrate]` when the selected source
  schema is below 14, otherwise `[]`;
- `C` is `[maintenance_configure]` when maintenance is not configured or an
  explicit setup policy change is valid, otherwise `[]`;
- `L` is `[legacy_state_cleanup]` when the authoritative fixed database has a
  valid pending cleanup record, otherwise `[]`; it is always ordered after any
  required Viewer publication.
- list concatenation preserves the durable stage order above.

| Observed state and invocation | Planned writes / result |
|---|---|
| no fixed/recovery/legacy source, `--read-only` | `[database_initialize] + C + [viewer_publish]`; `setup_preview`; project ID null; no token/write |
| no source, write setup | same plan; create one UUID identity; `setup_complete` or existing stage-specific failure |
| matching fixed primary | existing restore/migrate/configure/Viewer truth table, then `L`; no relocation token or binding stage |
| fixed mismatch, `--read-only` | `M + C + [project_binding_update, viewer_publish] + L`; `relocation_preview`; token present; no write |
| fixed mismatch, write setup without token | `project_relocation_required`; no write |
| fixed mismatch, valid token | preview plan including `L`; binding CAS precedes Viewer and cleanup follows Viewer; `setup_complete` or ordered partial result |
| one same-binding legacy source, `--read-only` | `[legacy_state_publish] + M + C + [viewer_publish, legacy_state_cleanup]`; `setup_preview`; no token/write |
| one same-binding legacy source, write setup | same plan; staged no-clobber publication; no binding stage |
| same-binding legacy primary missing with valid backups, `--read-only` | `[database_restore, legacy_state_publish] + M + C + [viewer_publish, legacy_state_cleanup]`; `setup_preview`; no token/write |
| same-binding legacy primary missing with valid backups, write setup | same plan; restore only into the private fixed stage; old primary remains absent |
| one moved legacy source, `--read-only` | `[legacy_state_publish] + M + C + [project_binding_update, viewer_publish, legacy_state_cleanup]`; `relocation_preview`; token present; no write |
| one moved legacy source, write setup without token | `project_relocation_required`; neither source nor destination changes |
| one moved legacy source, valid token | preview plan; atomic staged publication followed only by cleanup; `setup_complete` or ordered partial result |
| accepted token supplied again | `relocation_token_used`; no write |
| no token after successful rebind | `already_setup`, or only the still-due `[viewer_publish]`, `[legacy_state_cleanup]`, or both in that order |
| valid fixed primary with pending cleanup whose recorded filesystem entry changed, collided, or has an unrecorded retirement peer | setup `setup_incomplete`; stored project ID and relocation scheme/generation remain available; planned writes retain the due Viewer/cleanup stages and completed writes stop before cleanup; normal Task/handoff/review leaves remain usable |
| fixed primary missing, matching fixed managed backup | existing `database_restore` truth table, then `M + C + [viewer_publish]`; no relocation token |
| fixed primary missing, moved/ambiguous fixed backup identity | `project_state_unreadable`; no restore, rebind, or initialization |
| corrupt/unreadable existing fixed primary | `project_state_unreadable`; no backup/legacy fallback |
| no fixed or legacy state | normal leaf `db_not_initialized`; doctor `setup_required`; setup follows fresh rows |
| same-binding legacy state | normal leaf `migration_required`; doctor `migration_required`; setup follows same-binding rows |
| primary-backed moved legacy state | normal leaf `project_relocation_required`; doctor relocation warning/row; setup follows relocation rows |
| more than one or more than 64 `state/projects` entries, unknown direct child, invalid history/integrity, unsafe canonical entry, or invalid persisted cleanup metadata | `project_state_unreadable`; project ID null; `planned_writes=[]`; `completed_writes=[]`; relocation fields false/null; no fallback/write |
| safely read candidate whose directory, primary, or managed backup identity differs | `project_mismatch`; project ID null; empty write arrays and false/null relocation fields; no fallback/write |
| candidate source schema above 14 | `schema_too_new`; safely read project ID or null; empty write arrays and false/null relocation fields; no fallback/write |
| candidate busy/locked or unsupported journal state | existing `database_busy` or `unsupported_journal_mode`; safely read project ID or null; empty write arrays and false/null relocation fields; no fallback/write |
| unrelated physical entry inside the sole valid legacy directory | preserve and exclude it; it neither changes the selected source result nor appears in JSON |

### Concurrency, Recovery, Privacy, And Ownership

Preview creates no directory, lock file, SQLite sidecar, temporary file,
backup, Viewer, Git change, or target-project change. Actual legacy publication
uses one fail-fast package-state transition lock before the existing backup
artifact lock. It performs source SQLite copying through the backup API, never
holds a source write transaction during filesystem copy, uses only short
private/current write transactions for migration, configuration, and binding,
and releases every SQLite writer before Viewer publication or cleanup.
Fixed-state rebinding takes the same transition lock and performs one short
`BEGIN IMMEDIATE` compare-and-swap transaction.
Setup preview and write preflight both validate an already present transition
lock as one physical regular file of zero or one byte. An invalid artifact
returns the same sanitized no-write `setup_incomplete` result in either mode.

The binding repository compares project ID, identity scheme, old hash, and
expected generation, appends generation `N+1`, and updates current binding in
one transaction. That same transaction increments
`viewer_maintenance_state.source_generation` exactly once so a crash before
Viewer publication leaves a durable due marker. Missing Viewer state, binding
generation overflow, or Viewer source-generation overflow rolls the whole
rebind back as `project_state_unreadable` without consuming the token. Stale
comparison, same-hash rebind, or invalid fields likewise rolls back without a
history row. Every business writer
revalidates the resolver's project ID, binding hash, and generation under its
existing short write transaction. Read commands use one lock-respecting
query-only transaction. Backup and Viewer publication revalidate binding
before publication and preserve last-good artifacts on stale state or failure.
No SQLite writer is held during Git, backup copying, Viewer rendering, or other
subprocess work.

Missing-primary managed-backup restore remains setup-only and no-clobber.
Unreadable or corrupt existing fixed state is never replaced. Wrong identity,
unsafe links, ambiguous sources, unsupported journal state, concurrent
destination appearance, and lock contention fail closed through existing
sanitized boundaries. No new daemon, watcher, queue, service, network call,
generic migration engine, signature system, secret store, fork inference,
normal Task gate, LLM judgment, or routine stop is added.

TG-M17 ownership is exclusive:

- M17.1 owns schema v14, v1-v13 migration, identity/binding repository
  primitives, and an injected-target-only UUID initializer. Production
  resolution and fresh UUID setup remain unchanged in that staging revision.
- M17.2 activates the one fixed resolver, fresh production `uuid_v1`, and
  same-binding legacy publication. It owns the shared staged-publication and
  bounded cleanup/resume primitive for same-binding sources, transports
  validated backup/Viewer artifacts opaquely, recognizes moved sources
  read-only, and supports the schema-v14 legacy-layout transition state. It
  adds no token or moved write.
- M17.3 owns the sole setup option, token codec, setup JSON/errors, fixed
  rebind, confirmed moved-legacy staged publication/rebind, and replay. It
  reuses the M17.2 staged-publication and cleanup/resume primitive for the
  confirmed moved source rather than adding a second implementation.
- M17.4 owns the structural audit of all 20 leaves and runtime integration for
  backup, Viewer, doctor, maintenance, restore, last-good, privacy, and
  concurrency. It adds no schema, option, resolver, or token semantics.
- M17.5 synchronizes Skill/references, help, README/release guidance, manifest,
  release version, formal status, and isolated/self-host acceptance. It adds no
  behavior and performs no push, workflow dispatch, install, or publication.
  The synchronized Skill must present relocation preview and wait for explicit
  user approval before it reuses the token; it never auto-confirms.

Each unit is Tier 2 and requires its focused and full offline verification,
`doctor`, manifest/package checks where covered files change,
`git diff --check`, and two independent PASS receipts for the exact final
target. TG-M17.1 cannot start before M17.0 completes those gates. M18 cannot
start before M17.5 is done.

## Approved Post-MVP Extension: TG-M18 Completion Cycle History

TG-M18 preserves accepted completion evidence when a Task is reopened without
allowing old evidence to satisfy a later completion. It is an audit extension
to the existing Task lifecycle, not Issue management, general event history,
verification-run storage, or a workflow engine.

The synchronized repository release candidate is v0.10.0/schema v16/Viewer
snapshot v4. The completed allocation, including its non-public staging
revisions, is:

| Stage | Package | SQLite | Viewer | Publication state |
|---|---:|---:|---:|---|
| M18.1 schema/repository | 0.9.0 | 15 | 3, source 5-15 | non-public staging |
| M18.2 native capture activation | 0.9.0 | 16 | 3, source 5-16 | non-public staging |
| M18.3 read model/Viewer | 0.9.0 | 16 | 4, source 5-16 | non-public staging |
| M18.4 synchronized release | 0.10.0 | 16 | 4, source 5-16 | release candidate |

Schema v15 is named `completion_cycle_history`. Schema v16 is the marker-only
`completion_cycle_capture_activation` migration. It adds no table or column;
it exists so a schema-v15 binary cannot write a database after native capture
and `complete` coverage have become active. A v14 binary rejects v15 or v16,
and a v15 binary rejects v16 through the existing too-new-schema boundary.

The public CLI remains exactly 20 command leaves. TG-M18 adds no command,
option, setup choice, normal-loop call, LLM judgment, question, or stopping
rule. The default-off Task loop remains at most nine governance calls and an
enabled Effort Advisory flow remains at most ten. History is supplied by the
already mandatory `task show` only after M18.3.

### Schema And Immutable Cycle Contract

Schema v15 adds exactly:

- one `tasks.completion_history_coverage` column with values
  `legacy_unknown` or `complete` and default `legacy_unknown`;
- one nullable internal `task_events.completion_cycle_id` foreign-key column;
  and
- one append-only `task_completion_cycles` table.

No second history, join, reopen, verification-receipt, or generic event-payload
table is authorized. `tasks` remains the current mutable projection.
`review_receipts` and `review_findings` remain the detailed review ledger.

Each cycle has a stable
`tg_completion_cycle_<16-lowercase-hex>` ID and one positive signed-64-bit
`saved_cycle_ordinal` unique within its project and Task. Ordinals begin at 1
and increase by exactly one. The stored fields are:

| Group | Exact stored fields and values |
|---|---|
| ownership | `completion_cycle_id`, `project_id`, `task_id`, `saved_cycle_ordinal` |
| provenance | `origin` = `native_done` or `legacy_current_done`; `completeness` = `complete` or `partial`; nullable `completed_at`; internal `recorded_at` |
| authority | nonnegative `contract_revision`; `review_tier` 0, 1, or 2 |
| verification | `verification_expectation` = `specified` or `unspecified`; nullable Boolean `verification_attestation` |
| completion evidence | `completion_evidence_kind`, `completion_evidence_revision`, `completion_evidence_reason`, `external_revision_approved`, `completion_commit_required`, and `completion_commit_hash` |
| exact target | `review_target_kind`, `review_target_value`, `review_target_base_revision`, and `review_target_generation` |
| gate basis | `gate_basis_version`, `review_basis_kind`, required and qualifying independent-PASS counts, changes-requested count, open-high count, open-medium count, fresh-review-required count, and two nullable qualifying receipt-ID slots |

The completion-evidence fields preserve the existing typed/legacy projection
and bounds: revision at most 500 characters, reason at most 1,000, and the
stored compatibility commit-hash projection at most 500. The legacy direct
`completion_commit_hash` input alias retains its existing 128-character input
limit. Native rows allow `git_commit`, `external_revision`, or
`commit_not_required` with the existing exact matrix. Partial migrated rows
additionally allow `none` with the exact empty/required matrix and
`legacy_unverified`; they never convert either into stronger evidence. Target
value and base revision are each at most 500 characters and retain the existing
target-kind matrix.

`native_done` always pairs with `complete`, a non-null completion time,
verification attestation `true`, and gate-basis version 1.
`legacy_current_done` always pairs with `partial`, verification attestation
`null`, review basis `unknown`, and gate-basis version 0. The storage type is a
nullable Boolean, but the current exact origin matrix admits only `true` for a
native row and `null` for a legacy row; stored `false` is rejected.
`verification_expectation` records only whether the locked Task's bounded
verification field was non-empty; it does not copy command output.

Gate-basis version 0 has null counts and no receipt IDs. Version 1 has
nonnegative signed-64-bit counts, exactly the tier-derived required count
(0, 1, or 2), and one of:

- `independent_passes`: Tier 1 or 2, enough independent PASS reviewers, one
  receipt ID for Tier 1 and two for Tier 2;
- `self_review_fallback`: Tier 1 or 2, insufficient independent PASS reviewers,
  one valid fallback receipt ID; or
- `not_required`: Tier 0, one valid not-required receipt ID.

Because a native cycle is written only after the current gate succeeds, its
changes-requested, open-high, open-medium, and fresh-review-required counts are
all zero. The counts remain stored so the accepted basis cannot be changed by
later review-row resolution. Receipt slots reference existing exact-target
rows; review bodies and finding summaries are not copied.

Cycles cannot be updated or deleted. Coverage cannot be heuristically upgraded.
The event link is internal and immutable after insertion. Only the event
created by an actual successful native done transition and the later native
`task_reopened` event may reference the corresponding cycle. No event type is
added.

### Migration And Honest Legacy Coverage

Migration 15 runs only after a structurally complete schema v14 and is one
transaction. It sets `legacy_unknown` for every existing Task, leaves every
existing event field and value unchanged with a null cycle link, and inserts
exactly one ordinal-1 partial cycle for each Task that is currently `done`.
Every other status receives zero cycles.

The partial cycle copies only the current done projection: completion time,
Contract revision, review tier, verification expectation, existing typed or
legacy completion fields, and current review target. Its verification and
review attestations remain unknown. The migration does not parse event
summaries, associate old receipts by inference, update an event, or synthesize
a completion that was reopened before migration. An invalid current-done
projection fails the whole migration as unreadable state rather than inventing
values.

M18.1 retains schema default `legacy_unknown` for all task additions and does
not activate production done/reopen capture or alter either path. Migration 16
is applied only by the M18.2 runtime after both native done paths and reopen
integration exist. Its one activation transaction rereads every current done
`legacy_unknown` Task in `task_id` binary order, validates the current
projection, and compares it with the latest saved cycle using the exact normal
reopen projection fields. When no cycle exists, the latest cycle does not
match, or a `task_reopened` event already links the latest cycle, activation
appends one next-ordinal `legacy_current_done` partial cycle from the current
projection. An exact un-reopened latest cycle is reused without another row.
Invalid projection, ordinal overflow, or inconsistent ownership fails the
whole activation without the marker or any partial insert. Once schema v16 is
recorded, setup reentry validates the marker, objects, and stored rows but
never reruns this reconciliation.

This bounded reconciliation is needed because schema-v15 capture-less
completion and reopen remain legal and may change the current projection after
the migration-15 backfill. It neither infers nor recreates an older completion:
only the current done projection is saved, coverage remains `legacy_unknown`,
and any unlinked reopen event continues to report incomplete history. Schema
v16 remains marker-only in schema shape: it adds no table, column, index, or
trigger, although its activation transaction may add these conservative
partial rows before inserting the marker.

From activation onward, `task add` explicitly assigns `complete`; the schema
default remains `legacy_unknown` so an incomplete writer fails
conservatively. Tasks created before activation remain `legacy_unknown`
permanently, including after a later native completion.

For every status, `legacy_history_incomplete` is true if any of these is true:

- `completion_history_coverage` is not `complete`;
- any saved cycle is `partial`; or
- the Task has an explicit `task_reopened` event whose internal cycle link is
  null.

It is false only for a fully covered Task with no partial cycle and no unlinked
reopen event. A fresh, never-completed Task created after activation therefore
has zero cycles and `legacy_history_incomplete=false`. No text parsing is used.

### Native Completion And Deterministic Review Basis

Both public done paths, `task complete` and compatibility
`task edit --status done`, use the same capture service. Existing parsing,
verification confirmation, Git validation, sequential ordering, Contract,
review-target, receipt, finding, and concurrency gates run unchanged. Git and
other subprocess work remains outside the SQLite writer.

Under one short writer transaction, the service rereads the Task, current
Contract revision, target, exact-target receipts, findings, and lane basis. It
rejects a stale preflight, computes the next ordinal without overflow, selects
the review basis, inserts one complete cycle, updates the current Task, creates
the existing completion event with the internal cycle link, records the
existing effort transition, and commits. Cycle, Task, event, effort state,
Viewer source generation, and post-commit-maintenance due state either commit
coherently or remain unchanged. Backup and Viewer work still occurs only after
the business transaction commits.

Review-basis selection is independent of the existing diagnostic
`fallback_kind` field:

1. Count distinct independent PASS reviewers for the exact project, Task,
   target kind/value/base/generation.
2. If that count meets the tier requirement, select exactly the required one
   or two receipts ordered by `reviewer_key ASC, review_receipt_id ASC`.
3. Only if independent PASS is insufficient may Tier 1 or 2 select the first
   otherwise-valid fallback ordered by `review_receipt_id ASC`, including the
   existing Tier-2 user-approval requirement.
4. Tier 0 selects the first valid not-required receipt ordered by
   `review_receipt_id ASC`.

A valid fallback is never stored when enough independent PASS receipts exist.
Historical cycles and their receipt IDs are audit-only and are never queried
to satisfy a current gate.

### Reopen, Linkage, And Fresh Gates

An exact `done` to `in_progress` reopen locks and rereads the Task and its
latest ordinal cycle. The current done projection must match that cycle's
completion time, Contract revision, review tier, derived verification
expectation, six completion-evidence fields, and four review-target fields, and
that cycle must not already have a linked reopen event. Reopen does not
revalidate Git or reinterpret review rows merely to archive values already
accepted.

The same transaction links the new `task_reopened` event to that cycle, clears
only the existing current completion and review-target fields, advances review
generation with the existing overflow guard, applies the sequential-lane
guard, records the effort transition, and commits. The saved cycle remains
unchanged.

Normal activation ensures every then-current done `legacy_unknown` Task has a
latest cycle that matches its current projection. The stored compatibility
bridge nevertheless remains exact and exclusive: if a locked done Task has
`legacy_unknown` coverage and no cycle, reopen first inserts one ordinal-1
`legacy_current_done` partial cycle from that locked projection, then links the
reopen event and performs the normal reset in the same transaction. A
`complete`-coverage Task with no cycle, an unknown-coverage Task with a
mismatching existing cycle, or caller-supplied bridge data returns
`completion_history_inconsistent` with no write. A schema-v15 Task that was not
done at activation simply receives its first native complete cycle on a later
done transition.

A later completion always creates the next ordinal and requires a new
verification confirmation, post-reopen target, current-generation review
evidence, review confirmation, and completion evidence. Reopen history never
weakens current completion eligibility.

### Bounded Task Show And Viewer Projection

M18.3 adds exactly one `completion_history` sibling to default `task show`
data. The object has exactly:

```text
total, returned_count, truncated, legacy_history_incomplete, cycles
```

Cycles are a newest-first complete-row prefix ordered by
`saved_cycle_ordinal DESC`. At most 10 are returned. Byte measurement for one
cycle and for the complete `completion_history` component uses exactly
`json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")`.
One measured cycle is limited to 8,192 bytes and the measured complete
component is limited to 32,768 bytes. For every candidate, the formatter
measures the final five-key wrapper with the actual `total`, candidate
`returned_count`, resulting `truncated`, actual
`legacy_history_incomplete`, and candidate `cycles` array. This includes
non-ASCII UTF-8 and count-digit growth in the decision. Rows are never
partially serialized or skipped to fit an older row: collection stops before
the first row that would exceed either bound. `returned_count` is the actual
array length and `truncated` is `returned_count < total`.

The public cycle allow-list is exactly:

```text
completion_cycle_id, saved_cycle_ordinal, origin, completeness, completed_at,
contract_revision, review_tier, verification_expectation,
verification_attestation, completion_evidence, review_target, gate_basis
```

`completion_evidence` contains exactly `kind`, `revision`, `reason`,
`external_revision_approved`, `completion_commit_required`, and
`completion_commit_hash`. `review_target` contains exactly `kind`, `value`,
`base_revision`, and `generation`. `gate_basis` contains exactly `version`,
`kind`, `required_independent_passes`, `qualifying_independent_passes`,
`changes_requested`, `open_high`, `open_medium`,
`fresh_review_required`, and `qualifying_receipt_ids`.

For a gate-basis version-0 cycle, all six public count fields are JSON `null`
and `qualifying_receipt_ids` is exactly `[]`. For version 1, every count is a
JSON integer and `qualifying_receipt_ids` is an ordered JSON string array made
by filtering the two stored slots in slot order; null placeholders are never
emitted. Its length is exactly 1 for Tier-0 not-required, Tier-1 independent,
or Tier-1/2 fallback basis, and exactly 2 for a Tier-2 independent basis.
Public `verification_attestation` is JSON `true` for a native cycle and JSON
`null` for a legacy partial cycle; JSON `false` is never emitted.

The six existing public event fields remain the only event projection:

```text
task_event_id, task_id, project_id, event_type, summary, created_at
```

An explicit `PUBLIC_EVENT_FIELDS` boundary applies to event reads and every
write receipt. `task_events.completion_cycle_id` never appears there.
`completion_cycle_id` is public only as the stable ID of a returned cycle.

Text `task show` reports returned/total, truncation, legacy incompleteness, and
only the latest cycle's ordinal, origin/completeness, completion time,
evidence kind, target kind/generation, and review-basis kind. It does not print
revision values, reasons, receipt IDs, or review bodies. Task show reads Task,
events, current review evidence, handoff summary, Contract, counts, and cycles
in one query-only transaction.

`task list`, `task current`, `task next`, their compact forms, `task effort`,
and Review Packet output remain byte-contract compatible. No history option or
pagination path is added.

Viewer snapshot v4 includes the same five-key, 10-row/32,768-byte history
object per Task. For source schemas 5-14 it derives zero cycles and
`legacy_history_incomplete=true`; for schemas 15-16 it uses stored coverage,
cycles, and event links. The existing 500-Task selection and 64 MiB artifact
cap remain. Snapshot v3 accepts source 15 during M18.1 and source 16 during
M18.2 but omits all history fields; only M18.3 activates snapshot v4.

The Viewer remains static, offline, text-only, and database-independent in the
browser. TG-M18 adds no additional timer and preserves M15.5's existing single
visibility-aware timer. M15.6 stores no completion-history field, and filter,
selection, focus, scroll, CSP, no-storage/no-network, atomic publication, and
last-good behavior remain unchanged.

### Errors, Privacy, Recovery, And Ownership

`completion_history_inconsistent` is the only new stable public error. It uses
exit 2 and the sanitized message `stored completion history is inconsistent`.
It covers a missing required cycle, latest-cycle/current-projection mismatch,
reused reopen link, ordinal overflow, invalid gate-basis relationship, and
cycle/event ownership conflict. It is a command error, not a new
`task complete --check` blocker code. Existing stale-plan, review, Git,
sequential, busy, journal, migration, project mismatch, and unreadable-state
errors retain their precedence and messages.

No cycle stores raw stdout/stderr, verification-run details, stack traces,
environment data, diffs, review bodies or reasoning, prompts, conversations,
path lists, secrets, tokens, or authorization headers. Existing bounded,
validated completion values are copied once; current review rows remain their
own detailed history. Reads, failed checks, failed writes, and replay create no
cycle.

Schema v15/v16 state participates in the existing setup-only migration backup,
managed backup, restore, quick-check, foreign-key, fixed-state, and last-good
rules. A fixed schema-v15 backup restored by the schema-v16 runtime is migrated
before normal writes. Legacy `state/projects` eligibility remains schemas 1-13
plus the explicit schema-v14 transition; it is not broadened to v15/v16.
Fixed-state relocation tokens and backup validators accept the current schema
through 16. No new backup, restore, alternate path, target-project mutation,
network behavior, worker, queue, watcher, daemon, or service is introduced.

TG-M18 ownership is exclusive:

- M18.1 owns schema v15, migrations from the complete M17 schema lineage,
  partial current-done backfill, storage models/repository primitives,
  immutable/public-event boundaries, and snapshot-v3 source-15 compatibility.
- M18.2 owns marker-only schema v16, activation of `complete` task additions,
  activation-time partial reconciliation, both native done paths, exact reopen
  and compatibility-bridge behavior, deterministic gate selection, and
  snapshot-v3 source-16 compatibility.
- M18.3 owns the bounded `task show` JSON/text read model and Viewer snapshot
  v4. It changes no history writer or current gate.
- M18.4 synchronizes the active Skill, references, help, README/release package,
  manifest, versions, formal status, and full lifecycle acceptance at
  v0.10.0/schema v16/Viewer v4. It adds no behavior.

Every unit is sequential Tier 2 and requires focused and full offline tests,
schema/quick/foreign-key and compatibility checks, `doctor`, package/manifest
checks where covered files change, `git diff --check`, and two independent
PASS receipts for the exact final target. Handoff
`tg_handoff_696a19cba075d56e` remains pending as rationale until the user
separately directs its disposition.

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

## Historical CLI Requirements Through v0.7.0

This section preserves the pre-M14 command-by-command baseline. It is not the
current invocation contract. The implemented TG-M14 public-surface, setup,
doctor, completion, checkpoint, and Review Packet sections above control
the v0.8.0 lineage; the implemented TG-M17 section controls the v0.9.0
identity/storage boundary. Removed commands and `--db` fail through the fixed
no-write parser contract.

All MVP commands must support:

- `--repo`: target project root; default current directory.
- `--db`: explicit SQLite path override.
- `--json`: machine-readable output for Codex.
- `--read-only`: prohibit database creation, migration, or writes. Inspection
  commands behave as read-only even when this flag is omitted.

Inspection commands must not create or migrate databases by default. Commands
that write to the task-governance-tool database must state what they recorded in
both JSON and text output.

### `taskgov self status`

Purpose: inspect installed package version and local core drift without a
database, Git repository, or network.

`--repo` and `--db` are accepted for common CLI compatibility but are not read
or resolved by this package-local command. `--read-only` is accepted and
redundant because the command is always read-only. All three statuses are
successful advisory results with exit code 0.

Required output:

- package name and installed package version
- manifest-declared release origin and manifest version, or null when unknown
- `clean`, `modified`, or `unknown`
- exact changed-core count and bounded sorted relative paths after a complete
  comparison
- stable unknown-reason list
- fixed `suggested_action=continue`

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
- `--reopen-reason`
- `--review-tier-change-reason`
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
`external_revision`, plus `--revision`; `git_snapshot` instead captures the
current staged index and rejects a caller revision. A Git target is verified
and stored as its canonical full commit ID; a diff fingerprint must be canonical
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

## Historical JSON Output Through v0.7.0

This section preserves the old envelope and command inventory for compatibility
review. Current v0.8.0 envelopes follow the implemented TG-M14 contract and
never expose `db_path`; the TG-M17 additions preserve that envelope boundary
in v0.9.0.

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

- `self.status`
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

- `self.status`: `package_name`, `package_version`, `release_origin`,
  `manifest_version`, `status`, `changed_core_count`, `changed_core_paths`,
  `changed_core_paths_truncated`, `unknown_reasons`, and `suggested_action`.
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
At schema version 6, the task object returned by `review.target.set` and the
expanded task object returned by `task.show` include
`review_target_base_revision`. Compact list/current/next task objects, review
receipts, bounded `review_evidence`, and Viewer snapshot v3 omit that internal
binding component.

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
- `done_task_requires_reopen`
- `review_tier_downgrade_forbidden`
- `privacy_rejected`
- `not_found`
- `db_not_initialized`
- `migration_required`
- `project_mismatch`
- `unsupported_journal_mode`
- `database_busy`
- `review_target_required`
- `review_changes_requested`
- `review_receipts_insufficient`
- `review_finding_unresolved`
- `review_receipt_mismatch`
- `review_receipt_already_recorded`
- `review_target_mismatch`
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

Required TG-M12.O2 advisory warning codes:

- `package_core_modified`
- `package_status_unknown`

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

## Historical MVP Acceptance Criteria

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
