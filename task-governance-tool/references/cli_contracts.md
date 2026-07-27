# CLI Contracts

Use this reference when exact command arguments, JSON shapes, or error behavior
matter.

## Contents

- [Invocation](#invocation)
- [Commands](#commands)
- [`self status`](#self-status)
- [`db init`](#db-init)
- [`db status`](#db-status)
- [`task add`](#task-add)
- [`task list`](#task-list)
- [`task next`](#task-next)
- [`task current`](#task-current)
- [`task effort`](#task-effort)
- [`task show`](#task-show)
- [`task edit`](#task-edit)
- [`task complete`](#task-complete)
- [Local handoff commands](#local-handoff-commands)
- [Review evidence commands](#review-evidence-commands)
- [`web export`](#web-export)
- [Error Codes](#error-codes)

## Invocation

For normal governed-project use, install one physical project-scoped copy and
run from the target-project root:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py <command> [options]
```

The verified runtime is Windows with Python 3.12 or later. Linux and macOS are
unverified and have no support claim in this release.

For a new target project, start with `db status`. It reports missing or
outdated databases without creating files. Use `db init` only when local task
tracking should be created or migrated for that project-scoped install. A
target project may be a non-Git directory.

If a command is instead launched from inside the installed Skill directory,
pass the target project explicitly:

```powershell
python scripts/taskgov.py <command> --repo <target-project> [options]
```

Omitting `--repo` always means the current directory; it does not search for a
Git root. A physical copy at
`<target-project>/.agents/skills/task-governance-tool` is the only documented
stateful governed-project layout. Symlink, junction, and user-wide operating
paths are unsupported. Except for package-local `self status`, a linked Skill
install path returns exit code 2, `unsupported_install_layout`, and exact message
`stateful commands require a physical project-scoped skill copy` before
resolving a database or creating state.

Project identity is derived from the canonical absolute governed-directory
path. Moving or renaming that directory changes its default identity and may
make prior state appear uninitialized. This release performs no automatic
relocation and provides no relocation command or project UUID.

Common options:

- `--repo <path>`: target project root; defaults to the current directory.
- `--db <path>`: explicit SQLite database path override.
- `--json`: emit a stable JSON envelope.
- `--read-only`: reject write commands before creating, migrating, or writing.

All JSON output uses this envelope:

```json
{
  "ok": true,
  "command": "task.next",
  "project_id": "project-a1b2c3d4e5f6",
  "db_path": "C:\\path\\to\\taskgov.sqlite",
  "data": {},
  "warnings": [],
  "errors": []
}
```

Inspection commands are read-only by default: `self status`, `db status`,
`task list`, `task next`, `task current`, `task effort`, `task show`, `task
complete --check`, `handoff list`, and `handoff show`. `self status` is
package-local: it accepts the common `--repo` and `--db` spellings but does not
read or resolve either.

Database write commands are `db init`, `task add`, `task edit`, `task
complete` without `--check`, and the four `review` evidence commands, plus
`handoff record` and `handoff withdraw`. Only `db init` may create or migrate a
database. Other write commands require an already initialized database at the
current schema version; they return `db_not_initialized` or
`migration_required` without creating or migrating files otherwise. `web
export` never writes SQLite, but its normal mode writes one generated HTML file
after explicit user intent. Use `web export --read-only` for a no-file-write
preview.

### Operational database consistency

The live operational database supports rollback-journal mode only. Before an
operational read or write, taskgov rejects a persistent WAL header or adjacent
WAL/SHM sidecar with `unsupported_journal_mode` and the exact message
`task database uses unsupported WAL journal mode`, without opening,
converting, checkpointing, or deleting database state.

Every operational read uses SQLite `mode=ro` without `immutable=1`, enables
`query_only`, and begins an explicit read transaction before schema/project
validation and all related response queries. A successful response is one
committed-consistent snapshot. Rollback-journal contention may instead return
`database_busy` with the exact message
`task database is busy; run the command again later`; raw SQLite errors are
never emitted. This read-side mapping is delivered by M13.1. `task next`
retains its documented committed inter-read freshness boundary between
status/advisory inspection and candidate selection.

When the enabled advisory has a stored basis, `task effort` is intentionally
phased: one coherent transaction reads the task, Contract/handoff counts, and
stored basis, then closes before Git observation; a second validated
transaction refreshes activity generations. The generation comparison bridges
those committed observations. Without a stored basis there is no generation
comparison and no second DB read. A busy/locked post-Git refresh returns
`database_busy`, and newly detected WAL state returns
`unsupported_journal_mode`, rather than a successful unknown advisory. Other
bounded refresh failures may retain `activity_generation_uncertain`.

TG-M13.2 shortens write transactions without changing commands or payloads:
Git, completion, and Effort preflight occurs before `BEGIN IMMEDIATE`; the
relevant task/review/Contract/completion basis is reread under the short lock
before one atomic write. `git_snapshot` describes the observed HEAD and stage-0
index at capture time, not a Git index lock. M13.2 extends the same
`database_busy` code/message to residual write-side busy/locked failure, using
exit code 2, the command's existing empty `data`, and no `retryable` or
`suggested_action` field. Taskgov adds no generic automatic retry; handoff
record's existing one fresh-transaction retry is unchanged.

TG-M14.1 adds a shared completion preflight. `task complete --check` captures
one short coherent task/Contract/lane/review basis, closes SQLite before Git,
then captures a second short coherent basis. Thin `task complete` performs the
same outside-lock observation and delegates its locked write to the existing
`task edit` transition. Neither path holds a SQLite transaction during Git.

## Commands

### `self status`

Inspect the installed Skill package against its co-located release manifest:

```powershell
python scripts/taskgov.py self status --read-only --json
```

This command needs no initialized database or Git repository. The envelope has
`project_id=null` and `db_path=null`. `--read-only` is accepted but redundant.

`data`:

```json
{
  "package_name": "task-governance-tool",
  "package_version": "0.7.0",
  "release_origin": "github:VAiring/task-governance-tool",
  "manifest_version": 1,
  "status": "clean",
  "changed_core_count": 0,
  "changed_core_paths": [],
  "changed_core_paths_truncated": false,
  "unknown_reasons": [],
  "suggested_action": "continue"
}
```

`clean`, `modified`, and `unknown` all return `ok=true` and exit code 0.
Modified core adds warning `package_core_modified`; unknown adds
`package_status_unknown`. The changed-path list is sorted and limited to 20,
while `changed_core_count` is exact after a complete comparison. Missing or
invalid manifests and package-version mismatches return `unknown` with a stable
reason and no changed-path claim.

A symbolic-link or junction Skill install path returns `unknown` reason
`unsupported_install_layout`, warning `package_status_unknown`, and the same
fixed `suggested_action=continue`. This package-only diagnosis creates no state.

Root `config/`, `adapters/`, generated `state/`, `__pycache__/`, and `*.pyc`
are outside core. Output never contains package-absolute paths, file content,
digests, symlink targets, or operating-system exception text. The declared
origin is not a signature. The command performs no SQLite, Git, network,
GitHub, update, repair, download, install, Issue/PR, handoff, or Task action.

### `db init`

Create or migrate the local task database.

```powershell
python scripts/taskgov.py db init --repo <target-project> --json
```

`data`:

```json
{
  "created": true,
  "migrations_applied": [1, 2, 3, 4, 5, 6, 7, 8, 9],
  "schema_version": 9
}
```

`db init` validates contiguous migration history before and after applying an
ordered migration. A gap is not inferred or stamped as repaired: the command
returns `migration_required` without mutation and directs the operator to
restore a valid database backup or inspect the migration history.

### `db status`

Inspect database readiness without creating or migrating files.

```powershell
python scripts/taskgov.py db status --repo <target-project> --json
```

`data`:

```json
{
  "exists": true,
  "needs_init": false,
  "needs_migration": false,
  "schema_version": 9,
  "counts": {
    "active": 3,
    "paused": 1,
    "blocked": 1,
    "review_pending": 0,
    "done": 2,
    "next_actionable": 2,
    "handoff_pending": 3
  },
  "handoff_delivery": {
    "adapter_enabled": false,
    "sync_due": false
  }
}
```

`counts.active` includes `ready`, `in_progress`, `paused`, `blocked`, and
`review_pending`; it excludes terminal `done` and `cancelled` tasks.
`counts.paused` is the exact project-scoped paused population and does not
remove paused tasks from `counts.active`.
`counts.handoff_pending` is the exact project-scoped pending-handoff
population. Delivery is not implemented, so `adapter_enabled`
and `sync_due` are always `false`; pending rows produce no warning.

When a valid Effort Advisory profile is explicitly enabled, `data` also
contains:

```json
{
  "effort_advisory": {
    "enabled": true,
    "profile": "informational-v1",
    "profile_hash": "sha256:..."
  }
}
```

Absent or valid disabled profiles preserve the pre-advisory `db status` shape.
An invalid present profile adds
`effort_advisory: {"enabled": false, "configuration": "invalid"}` and one
`effort_advisory_profile_invalid` continuation warning.

### `task add`

Register one explicit task in a database previously prepared with `db init`.

```powershell
python scripts/taskgov.py task add --repo <target-project> --title "Update docs" --kind optional --priority normal --json
```

Useful options:

- `--description`
- `--kind sequential|optional`
- `--lane`
- `--order`
- `--priority low|normal|high|urgent`
- `--status ready|in_progress|blocked|review_pending|cancelled`
- `--blocked-reason`
- `--review-tier 0|1|2`
- `--verification`
- `--tags`
- optional Contract group:
  - `--contract-scope`
  - `--contract-acceptance`
  - `--contract-constraints`
  - `--contract-authority-ref`
  - `--contract-change-reason`

`data`: `task`, `event`.

The optional Task Contract group is per-task and never inferred. When any
Contract option is supplied, both scope and acceptance are required.
An initial Contract accepts optional constraints and authority reference but
rejects a change reason. It is allowed only when the resulting status is
`ready`, `in_progress`, `blocked`, or `review_pending`. Successful Contract
input adds:

```json
{
  "contract_write": {
    "recorded": true,
    "revision": 1
  }
}
```

Without Contract input, the existing `task`/`event` shape is unchanged and the
task remains at Contract revision zero. Missing Contract fields are not
prompted for or inferred.

An initial status of `done` is prohibited. Add the task in another supported
initial state, then complete it with `task edit --status done` so the normal
verification, review, and completion-evidence gates are enforced. Attempting
`task add --status done` returns `initial_done_forbidden` before any task or
event is stored.

An initial status of `paused` is also prohibited and returns
`initial_paused_forbidden`. `paused` is an intentional hold for work that has
already entered `in_progress` or `review_pending`.

For sequential tasks, initial `in_progress` or `review_pending` and any
insertion into an existing lane must preserve the shared predecessor rule.
Every earlier same-lane task must be `done` or `cancelled` before later work may
be active or under review.

### `task list`

Return compact filtered task rows.

```powershell
python scripts/taskgov.py task list --repo <target-project> --status ready --limit 20 --json
```

Filters:

- `--status`
- `--kind`
- `--lane`
- `--priority`
- `--tag`
- `--limit`
- `--include-done`

`data`: `tasks`, `count`, `limit`.

### `task next`

Return ready work candidates without loading all history.

```powershell
python scripts/taskgov.py task next --repo <target-project> --limit 5 --json
```

Filters:

- `--kind`
- `--lane`
- `--priority`
- `--limit`, default `5`

Selection rules:

- Include only `status=ready`.
- Include ready optional tasks directly.
- Include ready sequential tasks only when earlier same-lane tasks are `done` or
  `cancelled`.
- Exclude `in_progress`, `paused`, `blocked`, `review_pending`, `done`, and
  `cancelled`.

`data`: `tasks`, `count`, `limit`, `selection_rules`.

With `--compact --json`, the data keys are exactly `tasks`,
`total_matching`, `returned_count`, `limit`, and `truncated`. Each task contains
only `task_id`, `title`, `kind`, `lane`, `lane_order`, `priority`,
`review_tier`, `tags`, and `suggested_next_action`. `total_matching` is the
exact filter/readiness count before the query limit; `returned_count` is the
number retained after the byte cap. The complete JSON stdout, including the
normal envelope and warnings, is at most 16,384 UTF-8 bytes. Byte truncation
retains an existing-order complete-row prefix and sets `truncated=true`; a
query limit alone does not.

When paused work exists, a successful response adds exactly one top-level
warning without changing `data` or the success exit code:

```json
{
  "code": "paused_tasks_present",
  "message": "3 paused tasks exist; run taskgov task current --status paused"
}
```

The count comes from the immediately preceding successful status inspection
and is advisory under concurrent updates. The warning contains no task title,
pause reason, or event text. Failed commands and projects with zero paused
tasks return no such warning.

### `task current`

Rediscover work that has already started, is under review, is intentionally
paused, or is blocked. This inspection command is read-only and does not infer
working-tree freshness or staleness.

```powershell
python scripts/taskgov.py task current --repo <target-project> --limit 20 --json
python scripts/taskgov.py task current --repo <target-project> --status paused --limit 20 --json
```

`data`:

```json
{
  "tasks": [
    {
      "task_id": "tg_task_example",
      "status": "in_progress",
      "latest_event": {},
      "suggested_next_action": "continue the task and inspect its latest event"
    }
  ],
  "count": 1,
  "limit": 20,
  "statuses": ["in_progress", "review_pending", "paused", "blocked"]
}
```

Each task contains the normal public task fields plus its latest event and a
deterministic status-based suggested action. Ordering is status
(`in_progress`, `review_pending`, `paused`, `blocked`), priority, newest
`updated_at`, then `task_id`. Same-second latest events use event row order as a
private tie-breaker. `--limit` defaults to `20` and is capped at `100`.
Optional `--status` accepts only `in_progress`, `review_pending`, `paused`, or
`blocked`. A filter returns that one value in `data.statuses`; unsupported
statuses fail with `invalid_status` without mutation. This is a bounded view,
not paged history; use `db status.counts.paused` for the exact paused
population.

With `--compact --json`, the data keys are exactly `tasks`,
`total_matching`, `returned_count`, `limit`, `statuses`, and `truncated`. Each
task contains only `task_id`, `title`, `status`, `kind`, `lane`, `lane_order`,
`priority`, `review_tier`, `blocked_reason`, `pause_reason`, `latest_event`,
and `suggested_next_action`. A non-empty latest event contains only
`event_type`, `summary`, `created_at`, and `summary_truncated`; its summary is
truncated at a valid UTF-8 boundary to at most 256 bytes. The complete JSON
stdout is at most 24,576 UTF-8 bytes and retains only complete rows in existing
order.

Both compact caps account for every formatter newline as the portable CRLF
worst case. Raw stdout therefore remains within the fixed cap whether the
platform emits LF or CRLF.

The normal envelope keys remain in M14.1. Every bounded JSON handler exit
passes through the final cap. Every argparse rejection with lexical `--json`
before `--`, including an argparse-supported abbreviation, instead uses the
smallest 8,192-byte cap unconditionally; no second command parser is involved.
When diagnostic identity values would exceed the applicable cap, the formatter
sets `db_path` to `null`, then sets `project_id` to `null` only if still
required. Values are never partially truncated. If an error diagnostic still
exceeds the cap because it embeds an unbounded rejected value, the command,
data, exit code, and first safe error code remain unchanged while its message
becomes exactly `diagnostic details omitted to satisfy the bounded output
limit`. M14.2 owns global removal of the `db_path` key.

Only `task current` and `task next` accept `--compact`. It requires `--json`;
otherwise parsing fails before project/database resolution with exit 2, code
`invalid_option_combination`, and exact message `--compact requires --json`.
Default JSON and text outputs are unchanged. Compact selection omits Contract
and checkpoint content; inspect the selected task with `task show`.

### `task effort`

Read the optional informational scale observation for one task:

```powershell
python scripts/taskgov.py task effort --repo <target-project> <task-id> --json
```

The only supported profile location is
`<installed-skill-root>/config/effort-advisory.json`. The file is never
created or changed by `taskgov`. It is strict, versioned JSON:

```json
{
  "schema_version": 1,
  "profile": "informational-v1",
  "enabled": true,
  "thresholds": {
    "changed_files": 20,
    "changed_lines": 500,
    "changed_modules": 4,
    "contract_revisions": 2,
    "handoffs": 5
  }
}
```

`thresholds` is optional and may contain any subset of those five metric keys.
Values are non-negative integers; an observation exceeds a threshold only when
`value > threshold`. Unknown fields, duplicate keys, unsupported metrics, and
invalid values disable the profile. There is no profile creation command,
environment-variable override, inherited profile, arbitrary command, network
source, fixture scan, retry parser, or configured test runner.

An enabled result has this fixed `data` shape:

```json
{
  "task_id": "tg_task_example",
  "enabled": true,
  "profile": {
    "id": "informational-v1",
    "version": 1,
    "hash": "sha256:..."
  },
  "measurements": {
    "changed_files": 3,
    "changed_lines": 42,
    "changed_modules": 2,
    "contract_revisions": 1,
    "handoffs": 0
  },
  "thresholds": {
    "changed_files": 2
  },
  "exceeded": ["changed_files"],
  "basis": {
    "status": "captured",
    "revision": "0123456789abcdef0123456789abcdef01234567",
    "clean": true,
    "captured_at": "2026-07-26T00:00:00Z",
    "activity_generation": 1
  },
  "observation": {
    "revision": "89abcdef0123456789abcdef0123456789abcdef",
    "clean": true,
    "observed_at": "2026-07-26T00:10:00Z"
  },
  "coverage": {
    "changed_files": "complete",
    "changed_lines": "complete",
    "changed_modules": "complete",
    "contract_revisions": "complete",
    "handoffs": "complete"
  },
  "attribution": "exclusive_task_window",
  "unknown_reasons": [],
  "warning_key": "effort_advisory.threshold_exceeded.v1",
  "suggested_action": "continue"
}
```

Text mode emits the five measurements in that fixed order and a separate
fixed-order threshold line; unavailable values use `unknown` and no configured
thresholds use `none`.

Absent, disabled, or invalid profiles return `ok: true`, `enabled: false`, a
fixed empty projection, and `profile_disabled` or `profile_invalid`; the
inspection does not read Git and never writes advisory state. Ordinary Task
writes do no advisory bookkeeping until an enabled profile has captured a
basis. After such a basis exists, later disabled periods retain only hidden
project/subject activity counters so re-enablement cannot erase overlap
evidence; they do not capture a new basis or change any Task output. An enabled
profile may best-effort capture a basis only inside an existing transition
into `in_progress`. Capture failure never rejects the Task write and leaves no
partial basis.

Each database observation uses a fresh coherent `mode=ro`, `query_only`
transaction. Git observation uses bounded, no-shell, optional-lock-disabled
reads with `core.fsmonitor` disabled and submodules ignored. A stored basis
must be a full Git object ID before it is passed as an argument; invalid
evidence is not emitted. The command emits no paths, stderr, diffs, or raw
logs. Non-Git, dirty or unstable endpoints, missing or invalid basis/coverage,
and possible activity overlap return `ok: true`, `attribution: "unknown"`,
stable reason codes, and `suggested_action: "continue"`. Activity generation
is refreshed from a new coherent DB transaction after Git observation;
non-lock/non-journal refresh failure remains
`activity_generation_uncertain`, while M13 read-side `database_busy` and
`unsupported_journal_mode` remain command errors.
Threshold exceedance adds one stable
`effort_advisory_threshold_exceeded` warning with the same action. The command
never acknowledges, asks, hands off, pauses, blocks, changes acceptance, or
writes Task/DB/Git state. An advisory metric, threshold, or unknown result never
fails the command; ordinary database readiness, journal, and contention errors
still can. `--read-only` is accepted and has the same behavior.

### `task show`

Show one task plus recent event history and suggested next action.

```powershell
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

`data`: `task`, `events`, `suggested_next_action`, `review_evidence`,
`handoff_summary`, `contract`, `effort_advisory_enabled`.

`effort_advisory_enabled` is `true` only for a valid enabled package-local
Effort Advisory profile. An absent or valid disabled profile returns `false`
without a warning. An invalid present profile returns `false` plus the existing
`effort_advisory_profile_invalid` continuation warning. Reading this routing
field performs no Git observation, and text `task show` output is unchanged.

`task` includes typed completion evidence and the legacy commit projection:

- `completion_commit_required`
- `completion_commit_hash`
- `completion_evidence_kind`
- `completion_evidence_revision`
- `completion_evidence_reason`
- `external_revision_approved`

It also includes the current review target fields
`review_target_kind`, `review_target_value`,
`review_target_base_revision`, and `review_target_generation`. The base
revision is non-empty only for `git_snapshot`.

`review_evidence` is a bounded structured projection containing the current
target and generation, tier gate/pass/fallback state, receipt and open-finding
counts, blocking findings, and at most ten recent receipts and findings. It
never contains the snapshot base revision, raw review transcripts, or private
reasoning.

`handoff_summary` is a compact sibling, not a task field:

```json
{
  "pending_handoff": 2,
  "handed_off": 0,
  "handoff_withdrawn_by_user": 1
}
```

It contains exact per-state counts for this source task and is excluded from
task list/current/next rows and Viewer snapshots. On readiness or not-found
errors, its fixed value is `null`.

`contract` is a sibling, not a task field. Revision zero has this fixed shape:

```json
{
  "revision": 0,
  "scope": "",
  "acceptance": "",
  "constraints": "",
  "authority_ref": "",
  "change_reason": "",
  "created_at": null
}
```

A current immutable revision fills the same keys. The Contract pointer and
fields remain absent from list/current/next and Viewer task objects. On
readiness or not-found errors, `contract` is `null`.

### `task edit`

Update task state or metadata.

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for ..." --json
```

Editable options:

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
- `--reopen-reason <summary>`: concise sanitized reason for the only permitted
  write to a `done` task, an isolated reopen to `in_progress`.
- `--review-tier-change-reason <summary>`: concise sanitized reason required
  when lowering a review tier before review begins.
- `--completion-commit-hash <revision>`: Git-only compatibility alias. Verify
  an existing commit read-only and store its canonical full object ID.
- `--completion-evidence-kind <kind>`: explicitly select `git_commit`,
  `external_revision`, or `commit_not_required`.
- `--completion-revision <revision>`: revision for explicit Git or external
  evidence.
- `--completion-evidence-reason <summary>`: concise sanitized reason required
  for external evidence.
- `--external-revision-approved`: explicitly acknowledge that the external
  durable source is approved instead of target Git history when available.
- `--commit-not-required`: explicitly mark that no managed materials changed;
  compatibility alias for `commit_not_required` evidence.
- `--verification-complete`: record a concise command-time confirmation that
  required verification passed or has an approved exception.
- `--review-complete`: record a concise command-time confirmation that the
  required review gate passed or has a valid fallback.
- the optional Contract group:
  `--contract-scope`, `--contract-acceptance`, `--contract-constraints`,
  `--contract-authority-ref`, and `--contract-change-reason`.

`data`: `task`, `changed_fields`, `event`.

For a revision-zero task, Contract input is accepted only with an exact
`ready|blocked -> in_progress` transition, empty completion/review evidence,
and no other caller edit. It records revision 1 and returns
`event.event_type=contract_recorded`.

For a task with a Contract, later input is Contract-only and allowed in
`ready`, `in_progress`, `paused`, `blocked`, or `review_pending`. Scope and
acceptance remain required. Line endings and outer whitespace are normalized;
omitted later constraints preserve the current value and explicit empty
constraints remove it. Canonically equal content is a write-free replay:

```json
{
  "task": {},
  "changed_fields": [],
  "event": null,
  "contract_write": {
    "recorded": false,
    "revision": 2
  }
}
```

The shown `task` object is the normal public projection, abbreviated above.
Authority and change reason may be omitted for replay. A semantic change
requires both and returns `contract_revised`; it clears current completion
evidence, invalidates a started review target/generation, and returns
`review_pending` to `in_progress`. The exact
`user_instruction:<task-id>:<revision>` form is checked mechanically. An exact
replay accepts an older positive revision placeholder for the same task;
semantic change requires the current-or-next placeholder.
Different valid concurrent semantic inputs serialize as successive revisions;
the first version has no expected-revision option. A current-or-next
`user_instruction` placeholder formed before the write lock is rebound
deterministically to the revision allocated by that locked write. Retrying its
lost response with the original placeholder is therefore a write-free replay.

After a task reaches `done`, every otherwise-valid task and structured-review
write returns `done_task_requires_reopen`. The only exception is an isolated
`--status in_progress --reopen-reason <summary>` edit with no other field,
note, evidence, or gate option. Reopening preserves history, clears current
completion/review eligibility, and requires fresh gates before another
completion.

Raising a review tier follows the normal edit path. Lowering it requires
`--review-tier-change-reason`, target generation zero, no stored target, a safe
current/resulting status, and no review or completion companion options. Once a
target has been set, lowering the tier returns
`review_tier_downgrade_forbidden`.

Only `in_progress` or `review_pending` tasks may move to `paused`, and the
transition requires a concise sanitized `--pause-reason`. A paused task resumes
to `in_progress`, which clears the current `pause_reason`; the transition event
retains the bounded historical reason. Unsupported pause/resume paths return
`invalid_status_transition`.

Sequential transitions to `in_progress`, `review_pending`, or `done` require
all earlier same-lane tasks to be `done` or `cancelled`. The same predicate is
used by `task next`. Adds and edits that change kind, lane, order, or status
also validate both affected lanes and return
`sequential_predecessor_incomplete` without storing a success event when the
result would place incomplete work before an already advanced task.

Completion evidence spellings are mutually exclusive. Git evidence is resolved
with a shell-free, read-only `git rev-parse --verify --end-of-options
<revision>^{commit}` call. Missing, ambiguous, option-shaped, and non-commit
revisions fail; unique short hashes and annotated tags are stored as the
canonical full commit ID. External evidence always requires its explicit kind,
revision, reason, and approval. Changing evidence kind clears fields not valid
for the new kind.

`task edit --status done` requires both `--verification-complete` and
`--review-complete`. It also requires either a stored or newly supplied
valid typed evidence record. `legacy_unverified` evidence is preserved for
historical completed tasks but cannot satisfy a new done transition after a
task is reopened. A current-generation `changes_requested` receipt blocks
completion even when the required PASS receipts are present.

Complete with a Git commit:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-commit-hash <hash> --json
```

Complete with an explicitly approved external revision:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-evidence-kind external_revision --completion-revision <revision> --completion-evidence-reason "Approved external release" --external-revision-approved --json
```

Complete when no managed materials changed:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --commit-not-required --json
```

`taskgov` records and validates evidence only. It does not create commits or
mutate the target project. For Git projects, inspect changed materials from the
stored hash:

```powershell
git show --name-only <completion_commit_hash>
```

At every done transition, stored Git completion evidence and any Git review
target/base are resolved again read-only. Completion evidence must match the
current target: `git_commit` requires the identical `git_commit` target or a
valid `git_snapshot` binding; `external_revision` requires the identical
external target; and `commit_not_required` requires a `diff_fingerprint`
target. A mismatch returns `review_target_mismatch` without a success write.

### `task complete`

Check the current completion request without writing:

```powershell
python scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --commit-not-required --check --read-only --json
```

Perform the same request through the existing task-edit transition:

```powershell
python scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --commit-not-required --json
```

The thin command accepts only the task ID, `--verification-complete`,
`--review-complete`, and one typed evidence form using
`--completion-evidence-kind`, `--completion-revision`,
`--completion-evidence-reason`, `--external-revision-approved`, or the
`--commit-not-required` spelling. Legacy `task edit --status done` remains
supported. `--read-only` is valid with `--check`; it rejects the write form.

Check data keys are exactly `task_id`, `ready`, `status`, `blocking_codes`,
`contract_revision`, `review_target_generation`, `completion_evidence_kind`,
and `suggested_action`. The complete JSON stdout is at most 8,192 UTF-8 bytes.
JSON check success, errors, and recognizable parser rejections use the same
final bounded-output rule as compact output.
`blocking_codes` contains either no code or the first current write-path code.
A basis change during Git observation returns
`blocking_codes=["completion_check_stale"]`. The check stores no receipt,
readiness token, event, or evidence and never authorizes a later write.

The only readiness codes are `invalid_status_transition`,
`sequential_predecessor_incomplete`, `verification_required`,
`review_required`, `completion_evidence_conflict`,
`external_revision_approval_required`, `commit_required`,
`git_commit_not_found_or_ambiguous`, `invalid_review_evidence`,
`review_target_required`, `review_target_mismatch`,
`review_finding_unresolved`, `review_changes_requested`,
`review_receipts_insufficient`, and `completion_check_stale`. Parse/privacy,
not-found, project/schema/journal/busy/storage, and internal failures remain
normal `ok=false` command errors.

Text check output is exactly three lines: task readiness, the one blocking code
or `none`, and the bounded suggested action. Thin completion emits
`command=task.complete` and exactly the existing edit result data keys `task`,
`changed_fields`, and `event`. Its text uses the existing edit summary with
first line `Task completed: <task-id>`.

### Local handoff commands

Record one sanitized out-of-scope discovery after a single
task/blocker/handoff classification:

```powershell
python scripts/taskgov.py handoff record --repo <target-project> <source-task-id> --summary "Concise discovery" --rationale "Outside current acceptance" --json
```

Options are `--summary` (required, at most 1000 characters), `--rationale`
(at most 1000), and optional `--occurrence-id` (at most 200). Omitting the
occurrence ID means an exact canonical replay returns the existing row.
Supplying it explicitly requires a non-empty stable identity already provided
by user instruction or a deterministic source; invalid explicit values return
`handoff_occurrence_invalid`. The canonical identity includes project, source
task, the source task's current Contract revision, normalized summary
and rationale, and occurrence ID.

Successful `data`:

```json
{
  "handoff": {
    "handoff_id": "tg_handoff_...",
    "project_id": "project-a1b2c3d4e5f6",
    "source_task_id": "tg_task_...",
    "source_contract_revision": 0,
    "idempotency_key": "<sha256>",
    "occurrence_id": "",
    "summary": "Concise discovery",
    "rationale": "Outside current acceptance",
    "state": "pending_handoff",
    "adapter_key": "",
    "adapter_version": "",
    "delivery_attempts": 0,
    "last_delivery_code": "",
    "next_attempt_at": null,
    "claim_expires_at": null,
    "receiver_receipt": "",
    "withdraw_reason": "",
    "created_at": "2026-07-26T00:00:00Z",
    "updated_at": "2026-07-26T00:00:00Z",
    "handed_off_at": null,
    "withdrawn_at": null
  },
  "local_record": {
    "durable": true,
    "created": true,
    "replayed": false,
    "handoff_id": "tg_handoff_..."
  }
}
```

`claim_token` is always private. `durable=true` is assembled only after the
local commit succeeds. Exact replay returns `created=false`,
`replayed=true`, and the same row/ID without updating it. Local SQLite
busy/locked persistence receives at most one retry of the complete
transaction. Final local failure returns `handoff_not_persisted` and this
fixed non-durable data:

```json
{
  "handoff": null,
  "local_record": {
    "durable": false,
    "created": false,
    "replayed": false,
    "handoff_id": null
  }
}
```

Privacy rejection is not the SQLite persistence retry above. The packaged
workflow must not repeat, quote, log, store, or forward rejected raw input. It
may make at most one new `handoff record` attempt using a newly written concise
sanitized abstraction. A second `privacy_rejected` ends that recovery attempt
and emits only the fixed sanitized error.

List pending records oldest-first by `created_at, handoff_id`:

```powershell
python scripts/taskgov.py handoff list --repo <target-project> --limit 20 --json
python scripts/taskgov.py handoff list --repo <target-project> --state handed_off --state handoff_withdrawn_by_user --json
```

The default limit is 20 and maximum is 100. Optional `--source-task-id`
filters one source. Terminal rows appear only through explicit repeatable
`--state`. `data` contains `handoffs`, returned `count`, exact
`total_matching`, effective `limit`, and selected `states`. Each compact list
row contains only `handoff_id`, `source_task_id`,
`source_contract_revision`, `summary`, `state`, `created_at`, and
`updated_at`. Count and rows come from one read snapshot. Paging is not
implemented.

Show one full sanitized record:

```powershell
python scripts/taskgov.py handoff show --repo <target-project> <handoff-id> --json
```

`data` is `handoff`. Both list and show revalidate stored privacy limits and
the state-field matrix before emission. Corrupt/private stored content returns
`internal_error` without exposing it.

Withdraw an undelivered pending record only on explicit user direction:

```powershell
python scripts/taskgov.py handoff withdraw --repo <target-project> <handoff-id> --reason "Handled outside Task Skill" --json
```

The sanitized reason is required and limited to 1000 characters. Success
returns `handoff` and
`changed_fields=["state","withdraw_reason","withdrawn_at"]`. The conditional
write accepts only `pending_handoff` with no claim or delivery attempt;
terminal, claimed, or attempted rows return `handoff_not_withdrawable`.
Withdrawal does not append a task event or update the source task.

Error `data` is fixed by command: list returns empty `handoffs`, zero
`count`/`total_matching`/`limit`, and empty `states`; show returns
`{"handoff": null}`; withdraw returns a null `handoff` and empty
`changed_fields`. Missing, old-schema, and wrong-project databases retain
`db_not_initialized`, `migration_required`, and `project_mismatch`
respectively. `handoff_not_persisted` is reserved for failure of the local
record transaction after readiness validation.

Schema v7 implements no Issue adapter, receiver detection, claim acquisition,
delivery, or `handoff sync` command. The agent always uses `handoff record`
regardless of Issue Skill presence. A pending record never changes task
selection or completion and emits no warning.

### Review evidence commands

Set or replace the current review target:

```powershell
python scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_commit --revision <revision> --json
python scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_snapshot --json
```

Allowed target kinds are `git_commit`, `diff_fingerprint`, and
`external_revision`, which require `--revision`, plus `git_snapshot`, which
rejects `--revision`. Git commit targets are verified read-only and stored
canonically. A diff fingerprint must be `sha256:` followed by 64 lowercase
hexadecimal characters. Every set advances the monotonic target generation,
including A-to-B-to-A changes.

For `git_snapshot`, first stage exactly the intended files through the
project's Git workflow. The command captures canonical `HEAD` as the base and
fingerprints only the stage-0 index; unstaged and untracked files are excluded.
It rejects an unborn `HEAD`, unmerged/unsupported index entries, and performs
no Git mutation. The later completion commit must have exactly one parent equal
to that base and a tree matching the fingerprint. Root and merge commits are
unsupported. If candidate content or parent changes, set a new target and
obtain fresh receipts.

`data` is `task`, `changed_fields`, and `event`. For a snapshot target, the
returned task and a later `task show.task` expose the canonical
`review_target_base_revision`. Receipt rows, the `task show.review_evidence`
projection, and Task Viewer snapshots intentionally omit that base revision.

Add a sanitized receipt for the current target generation:

```powershell
python scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <stable-reviewer-key> --kind independent --verdict pass --summary "No blocking findings" --json
```

Receipt kinds are `independent`, `self_review_fallback`, and `not_required`;
verdicts are `pass`, `changes_requested`, and `not_required`. One reviewer key
may record only one receipt per target generation. Tier 1 requires one distinct
independent PASS, or a documented self-review PASS when independent tooling is
unavailable. Tier 2 requires two independent PASS receipts, unless a Tier 2
self-review PASS is explicitly user-approved because independent tooling is
unavailable. Tier 0 uses a `not_required` receipt with a rationale. The normal
Tier 2 path therefore uses two LLM review judgments; each meaningful fix/new
target requires two fresh judgments. Deterministic gate checks add none.

Record and resolve findings:

```powershell
python scripts/taskgov.py review finding add --repo <target-project> <task-id> --receipt-id <receipt-id> --severity high --summary "Concise finding" --json
python scripts/taskgov.py review finding resolve --repo <target-project> <finding-id> --resolution "Concise resolution" --json
```

Severity is `high`, `medium`, or `low`. Open high/medium findings block `done`.
After resolving a high/medium finding, completion still requires a newer target
generation and fresh qualifying receipts. Any `changes_requested` receipt for
the exact current generation also blocks completion with
`review_changes_requested`; a newer target plus fresh qualifying receipts is
required. Review write commands require an initialized current-schema database
and reject `--read-only` before writing.

### `web export`

Render one self-contained, offline Task Viewer snapshot from the initialized
SQLite database:

```powershell
python scripts/taskgov.py web export --repo <target-project> --json
```

The default output is generated skill-local state:

```text
<installed-skill-root>/state/projects/<project-id>/viewer/task-viewer.html
```

Use `--read-only` to validate the database, snapshot, template, and resolved
output path without creating a directory or file. Use `--output <path>` only
after the user explicitly approves that complete destination. Explicit parents
must already exist and the filename must end in `.html` or `.htm`. An explicit
path inside the governed project is accepted only under the installed skill's
generated `state/` directory.

`data`:

```json
{
  "output_path": "C:\\path\\to\\task-viewer.html",
  "written": true,
  "replaced": false,
  "task_count": 3,
  "event_count": 7,
  "generated_at": "2026-07-17T00:00:00Z",
  "snapshot_version": 3
}
```

Snapshot version 3 reads source schemas 5 through 9 in this release, includes
the task-show typed completion fields and the same bounded structured
review-evidence projection, but excludes `review_target_base_revision`,
`handoff_summary`, all handoff records, the Contract pointer, and all Contract
fields/revisions. The generated file is stale until
`web export` is explicitly run again. The command does not start a server,
open a browser, edit tasks, or write database events. Databases using WAL mode
are rejected before the snapshot connection so even a preview does not create
SQLite sidecar files.

After command and output resolution, every `web.export` error preserves this
fixed `data` shape. `output_path` is `null` only when output resolution itself
failed:

```json
{
  "output_path": null,
  "written": false,
  "replaced": false,
  "task_count": 0,
  "event_count": 0,
  "generated_at": null,
  "snapshot_version": 3
}
```

`output_path_invalid` and `output_parent_missing` use exit code 1.
`output_write_failed`, database readiness failures, WAL-state rejection, and
unexpected snapshot/template failures use exit code 2.

## Error Codes

Known error codes include:

- `invalid_argument`
- `invalid_status`
- `invalid_kind`
- `invalid_priority`
- `invalid_review_tier`
- `blocked_reason_required`
- `pause_reason_required`
- `verification_required`
- `review_required`
- `commit_required`
- `completion_commit_conflict`
- `completion_evidence_conflict`
- `git_commit_not_found_or_ambiguous`
- `external_revision_approval_required`
- `review_target_required`
- `review_receipts_insufficient`
- `review_finding_unresolved`
- `review_receipt_mismatch`
- `review_receipt_already_recorded`
- `invalid_review_evidence`
- `initial_done_forbidden`
- `initial_paused_forbidden`
- `invalid_status_transition`
- `sequential_predecessor_incomplete`
- `done_task_requires_reopen`
- `review_tier_downgrade_forbidden`
- `review_changes_requested`
- `review_target_mismatch`
- `handoff_not_persisted`
- `handoff_not_withdrawable`
- `handoff_occurrence_invalid`
- `contract_activation_forbidden`
- `contract_authority_required`
- `contract_write_conflict`
- `privacy_rejected`
- `not_found`
- `db_not_initialized`
- `migration_required`
- `project_mismatch`
- `unsupported_journal_mode`
- `unsupported_install_layout`
- `database_busy`
- `output_path_invalid`
- `output_parent_missing`
- `output_write_failed`
- `internal_error`

Advisory warning codes include `package_core_modified`,
`package_status_unknown`, `paused_tasks_present`,
`effort_advisory_profile_invalid`, and `effort_advisory_threshold_exceeded`.

Some commands define an explicit empty `data` shape for specific error paths.
For example, `task.next` errors return empty `tasks`, `count`, `limit`, and
`selection_rules`. Other validation errors may use the generic empty object
shape unless the command contract or tests say otherwise.
