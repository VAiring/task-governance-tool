# task-governance-tool Current Product Specification

Status: The published product is v0.10.0, SQLite schema v16, and Viewer
snapshot v4 accepting source schemas v5-v16. The accepted release commit,
remote `main`, and lightweight tag `v0.10.0` are
`a9b80ce177a6dead10d51a070b76ff01f7af0294`; GitHub Release `362617903` is
published with prerelease visibility. TG-M19.0 through TG-M19.10, including
the TG-M19.6A and TG-M19.6B corrections, are complete. This post-release
reconciliation is the active current-product authority. TG-M20.1 through
TG-M20.5 are complete. TG-M21.1 is complete at
`fc2e0870ad9bf70830a082df168ad1992e07b51d`; its explicitly inactive
acceptance contract below changes no current v0.10.0 behavior. The approved
TG-M20S successor observation interrupts before TG-M21.1A. TG-M21.1A through
TG-M21.3 are approved and registered, but remain inactive behind that bounded
study and their own dependency and synchronization gates.

This document specifies supported product behavior. `docs/design.md` owns
implementation structure, `docs/implementation-roadmap.md` owns execution
order and gates, root `AGENTS.md` owns durable agent behavior, and `plan.md`
holds current decisions and open issues. Indexed files under
[`docs/history/`](history/README.md) are non-authoritative lineage only.
Required current behavior never depends on historical text.

## Documentation Authority And History

The active governing set is exactly root `AGENTS.md`, this specification,
`docs/design.md`, `docs/implementation-roadmap.md`, and root `plan.md` in the
repository authority order. History is indexed by `docs/history/README.md`.
Each captured body is immutable after its capture commit; later history work
may append a new file and index entry but never revise an archived body.

Every archive begins with a conspicuous non-authority banner, names its source
path and exact source commit, links to the active replacement, and warns that
internal words such as current/approved/implemented describe only the captured
revision. History may support lineage, migration review, rationale, and
evidence discovery, but never fills an active-contract gap, satisfies a current
gate, or revives a removed command, path, install layout, or workflow.

An authority-layout transition prepares and indexes history before reducing an
active file, then lands history, active replacements, links, and routing in one
exact reviewed commit. No intermediate commit may expose a missing target, two
plausible active authorities, or a current document that depends on history.

## Product Boundary

`task-governance-tool` is a local-first Codex Skill and deterministic Python
CLI for bounded task registration, rediscovery, selection, state transition,
local handoff, review evidence, completion evidence, and concise history. It
reduces repeated loading of large task-status documents; it does not replace a
project's `AGENTS.md`, requirements, design, tests, or decision log.

Task Skill owns:

- the Task purpose, scope, acceptance, constraints, current state, next work,
  blockers, and optional continuation checkpoint;
- the select, execute, verify, review, update, and complete loop;
- stopping at satisfied acceptance or at an existing blocker, unavailable
  dependency, required decision, or unsafe affected lane; and
- immediate durable local handoff of discoveries outside current acceptance.

It does not own Issue priority, triage, duplicate resolution, Issue lifecycle,
external ticket synchronization, resulting-Task creation, project-specific
test strategy, threat-model management, reviewer authentication, signatures,
or a general workflow/audit engine.

The tool is offline by default. It never creates or changes target-project
source, Git commits, branches, refs, tags, PRs, Issues, external services, or
network state. Read-only Git subprocesses are permitted only for the exact
validation and review operations defined below. Explicit setup may create the
canonical ignored package-local state and generated Viewer; successful
business mutations may perform the opted-in bounded same-process maintenance
defined below.

## Package, Runtime, And Generated State

The installable folder and `SKILL.md` name are `task-governance-tool`.
Ordinary stateful use supports exactly one physical project-scoped package:

```text
<target-project>/.agents/skills/task-governance-tool/
```

User-wide, symlink, junction, reparse-point, and competing project-scoped
installations are unsupported. This repository alone has a development
self-host exception at `<repo>/task-governance-tool`: it requires explicit
`--repo`, a physical package and repository, all five governing files, a valid
manifest/package boundary, and no competing ordinary install. It reuses the
source package's canonical state and is not install guidance for consumers.

Python 3.12 or newer is required. Windows is the CI-verified platform; exact
CI runtimes are Python 3.12 and 3.14. Linux and macOS are unverified.

The static package includes its manifest, Skill metadata and instructions,
runtime modules, one-level references, and bundled Viewer template. Generated
state and target-local presentation configuration are not release-package
content. The fixed generated targets are:

```text
<physical-skill>/state/current/taskgov.sqlite
<physical-skill>/state/current/backups/
<physical-skill>/state/current/viewer/task-viewer.html
```

The package `state/` directory is the generated-state and Git-ignore boundary.
The recommended target-local rule is exactly:

```gitignore
/.agents/skills/task-governance-tool/state/
```

An effective enclosing rule is also valid. Broad `*.sqlite`, `*.sqlite3`, or
`*.db` guidance is prohibited. Public alternate database, backup, Viewer,
state, export, and output paths do not exist; explicit path injection is an
internal test/service seam only.

## Public CLI And Output Contract

### Command Inventory

The public CLI has exactly 20 command leaves:

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

`task complete --check` is a mode of the same leaf. Applicable commands retain
`--repo`, `--json`, and `--read-only`; root `--version` is project-free.
Omitted `--repo` means the current directory, including a physical non-Git
directory. Invocation from either supported package root requires explicit
`--repo`.

`setup` alone also accepts `--backup-interval-minutes` from 1 through 1,440,
`--backup-generations` from 1 through 20, and
`--confirm-relocation <token>`. The normal Skill flow supplies neither backup
option and supplies a relocation token only after the explicit approval flow.

Public `self`, `db`, `web`, `--db`, custom output, compatibility aliases, and
replacement storage, Viewer, export, repair, maintenance, disable, or admin
commands are removed. Unknown/removed root commands fail before package,
project, Git, or SQLite resolution with exit 2 and `invalid_command`. A lexical
`--db` takes precedence and fails with `invalid_option` and exact message
`option is not available`; its following value is never echoed. Without
lexical JSON, stdout is empty and stderr is exactly
`taskgov: command is not available\n` or
`taskgov: option is not available\n`. With lexical `--json`, stderr is empty
and stdout is the normal compact envelope with `command="parse"`, null project
identity, empty data/warnings, and one sanitized error.

### JSON, Text, Limits, And Exit Status

Every JSON result is one compact object with exactly:

```text
ok, command, project_id, data, warnings, errors
```

`project_id` is the safely resolved stored identity or null. Public output
never contains `db_path`, `backup_path`, `viewer_path`, another raw storage
path, a rejected token/value, raw remote URL, or credential. UTC timestamps
use canonical ISO-8601 strings. Human text remains concise and cannot replace
the JSON contract.

Exit status is 0 for success, 1 for parser/validation usage errors, and 2 for
database, migration, project-state, or tool-service errors unless a command
section fixes a narrower result. Inspection leaves are inherently read-only;
`--read-only` rejects every write before database, artifact, Git, or target
change.

Every JSON parser rejection requested lexically before `--`, including a
supported abbreviation of `--json`, uses the bounded formatter with an
8,192-byte cap. The formatter preserves the envelope, command, data, first
safe error code, and exit status. If needed it nulls legacy diagnostic
identity fields atomically and replaces an unsafe/unbounded message with
`diagnostic details omitted to satisfy the bounded output limit`; it never
partially truncates an identity.

Supported nested-command or option parse failures use exit 1,
`invalid_argument`, and `arguments are invalid`. Incompatible supported
options use `invalid_option_combination` with a bounded fixed message.
Specifically, `setup --read-only --confirm-relocation <token>` fails before
project/state resolution with exact message
`--confirm-relocation cannot be used with --read-only`.

Current success-data projections are:

| Command | Data keys |
|---|---|
| `task.add` | `task`, `event`, plus `contract_write` only when Contract input was supplied |
| `task.list` | `tasks`, `count`, `limit` |
| default `task.next` | `tasks`, `count`, `limit`, `selection_rules` |
| default `task.current` | `tasks`, `count`, `limit`, `statuses` |
| `task.effort` enabled | `task_id`, `enabled`, `profile`, `measurements`, `thresholds`, `exceeded`, `basis`, `observation`, `coverage`, `attribution`, `unknown_reasons`, `warning_key`, `suggested_action` |
| `task.show` | exactly `task`, `events`, `suggested_next_action`, `review_evidence`, `handoff_summary`, `contract`, `latest_checkpoint`, `effort_advisory_enabled`, `completion_history` |
| `task.edit` | `task`, `changed_fields`, `event`, plus `contract_write` only for Contract input |
| `task.complete` | `task`, `changed_fields`, `event` |
| `handoff.record` | `handoff`, `local_record` |
| `handoff.list` | `handoffs`, `count`, `total_matching`, `limit`, `states` |
| `handoff.show` | `handoff` |
| `handoff.withdraw` | `handoff`, `changed_fields` |
| `review.target.set` | `task`, `changed_fields`, `event` |
| `review.receipt.add` | `receipt`, `event` |
| `review.finding.add` | `finding`, `event` |
| `review.finding.resolve` | `finding`, `event` |

`task.show` failure keeps `completion_history=null` in its bounded empty data.
Revision-zero Contract output is exactly revision 0; empty scope, acceptance,
constraints, `authority_ref`, and `change_reason`; and null `created_at`.
`local_record` contains exactly `durable`, `created`, `replayed`, and
`handoff_id`.

### Task Selection And Read Commands

Task statuses are `ready`, `in_progress`, `paused`, `blocked`,
`review_pending`, `done`, and `cancelled`; priorities are `low`, `normal`,
`high`, and `urgent`; kinds are `sequential` and `optional`; review tiers are
0, 1, and 2.

`task add` requires a title and accepts description, kind, lane/order,
priority, initial status, blocker reason, review tier, verification, tags, and
the optional complete Task Contract group. Defaults are optional kind, normal
priority, ready status, and Tier 1. A sequential Task may receive a
deterministic default lane and append order; output exposes the stored values.
Initial blocked requires a reason. Initial done and paused fail respectively
with `initial_done_forbidden` and `initial_paused_forbidden`.
The exact editable Task arguments are title, description, kind, lane, order,
priority, status, blocked/pause reason, review tier, verification, tags, note,
reopen reason, review-tier-change reason, typed/legacy completion evidence and
confirmations, and the Contract group. A write response states the fields and
event it recorded.
The corresponding exceptional options are exactly `--reopen-reason`,
`--review-tier-change-reason`, `--completion-evidence-kind`,
`--completion-revision`, `--completion-evidence-reason`,
`--external-revision-approved`, `--completion-commit-hash`,
`--commit-not-required`, `--verification-complete`, and
`--review-complete`.

`task list` is a compact bounded read with status, kind, lane, priority, tag,
limit, and include-done filters. Its default limit is 20 and maximum is 100;
order is priority urgent/high/normal/low, canonical lane, lane order with nulls
last, creation time, then Task ID. `task next` returns only ready optional
Tasks and ready sequential Tasks whose earlier same-lane Tasks are done or
cancelled. Its filters are kind, lane, priority, and limit; default limit is 5.
Order is priority urgent/high/normal/low, canonical lane, lane order with nulls
last, creation time, then Task ID. Paused, active, blocked, review-pending,
done, and cancelled Tasks are excluded.

When paused work exists, successful `task next` adds exactly one
`paused_tasks_present` warning with the exact paused count and suggestion
`taskgov task current --status paused`. It does not change candidates, data,
exit status, or state. Zero paused Tasks or a failing next command emits no
such warning. The count and candidate selection may be separate committed read
transactions; each is coherent, but no cross-transaction linearizability is
claimed.

`task current` returns only `in_progress`, `review_pending`, `paused`, and
`blocked`, with the latest event, reasons, update time, and deterministic next
action. Default limit is 20 and maximum is 100. Order is status in that listed
order, priority urgent/high/normal/low, newest `updated_at`, then Task ID.
Optional `--status` accepts only one current-work status; JSON reports the
effective `statuses`. It is bounded rediscovery, not stale-age, working-tree
freshness, exhaustive history, or pagination.

Only `task current` and `task next` accept `--compact`, and compact requires
`--json` or fails before state access with `invalid_option_combination` and
exact message `--compact requires --json`. Compact current is at most 24,576
UTF-8 bytes and compact next at most 16,384. Rows remain in deterministic
order; the first row that would cross the cap and all later rows are omitted
and `truncated=true`. Event summary is at most 256 UTF-8 bytes at a code-point
boundary.

Compact current data is exactly `tasks`, `total_matching`, `returned_count`,
`limit`, `statuses`, and `truncated`; each Task contains `task_id`, `title`,
`status`, `kind`, `lane`, `lane_order`, `priority`, `review_tier`,
`blocked_reason`, `pause_reason`, `latest_event`, and
`suggested_next_action`. A compact event contains `event_type`, `summary`,
`created_at`, and `summary_truncated`.

Compact next data is exactly `tasks`, `total_matching`, `returned_count`,
`limit`, and `truncated`; each Task contains `task_id`, `title`, `kind`,
`lane`, `lane_order`, `priority`, `review_tier`, `tags`, and
`suggested_next_action`.

`task show` reads one Task, bounded events, current review evidence, Contract,
handoff counts, latest checkpoint, completion history, and suggested action in
one query-only transaction. It also returns exactly one routing Boolean
`effort_advisory_enabled`; invalid advisory configuration returns false plus
the existing continuation warning. Text show does not add that flag.

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

`doctor` is the sole diagnostic. It is inherently read-only, emits
`command="doctor"`, and always includes
`data.suggested_action="continue"`. It never fixes, initializes, migrates,
backs up, renders, acquires a maintenance lock, runs project tests, or changes
state. It is not a setup or normal-loop prerequisite.

Doctor data contains only `suggested_action`, `setup_eligible`, and
`components`. Component keys are exactly `package`, `project_state`,
`task_summary`, `handoff_delivery`, and `maintenance`. Package inspection is
one independent bounded filesystem observation. When state is readable, all
project-backed components come from one lock-respecting read transaction; an
unavailable project-backed component is exactly `{"code":"unavailable"}`.

- `package` is the bounded manifest-integrity projection.
- `project_state` contains only `code`, `schema_version`, and
  `required_schema_version`.
- `task_summary` contains `code` and exact counts for `active`, `blocked`,
  `done`, `next_actionable`, `paused`, and `review_pending`.
- `handoff_delivery` contains `code`, `handoff_pending`, `adapter_enabled`,
  and `delivery_due`.
- `maintenance` contains `code`, `opted_in`, `backup`, and `viewer`.

The package projection contains exactly `package_name`, `package_version`,
`release_origin`, `manifest_version`, `status`, `changed_core_count`,
`changed_core_paths`, `changed_core_paths_truncated`, `unknown_reasons`, and
`suggested_action="continue"`. Status is `clean`, `modified`, or `unknown`.
It compares the physical package with the co-located strict v1 release
manifest: declared core paths are normalized relative POSIX paths with
`sha256:<lowercase-digest>`, and added files outside root `config/`,
`adapters/`, generated `state/`, and bytecode/cache are core modifications.
The manifest excludes itself. Absolute/traversal/duplicate/case-colliding/
excluded/malformed paths, identity/version mismatch, incomplete inspection, or
unsafe objects produce unknown without reading outside the package. Output is
at most 20 sorted relative changed paths and never includes absolute paths,
content, expected/actual hashes, link targets, or exceptions. `release_origin`
is a declaration, not a signature; replacing both manifest and core can evade
this local drift check.

The backup object has exactly `code`, `due`, `interval_minutes`,
`generations`, `last_success_at`, and `last_outcome`. The Viewer object has
exactly `code`, `due`, `source_generation`, `rendered_generation`,
`last_success_at`, and `last_outcome`. Each outcome contains only `code`
(`none`, `succeeded`, `deferred`, or `failed`) and `occurred_at`; not-yet-owned
values are null.

Readable/current state is exit 0, `ok=true`, and setup-eligible when package,
runtime, install, ignore, identity, and state preconditions all pass. Missing
state and supported older schema are successful diagnostics with respectively
`setup_required` and `migration_required` warnings and remain setup-eligible.
Package `modified` or `unknown` is a successful warning but not setup-eligible.
Relocation-required state is a successful warning with
project-state `relocation_required`, `setup_eligible=true` when all other
preconditions pass, and no write.

Fatal rows use exit 2 and these component/error mappings:

| Condition | Project/package code | Error |
|---|---|---|
| unreadable/invalid state | `unreadable` | `project_state_unreadable` |
| foreign identity | `foreign` | `project_mismatch` |
| newer schema | `newer` | `schema_too_new` |
| SQLite busy/locked | `busy` | `database_busy` |
| WAL header/sidecar | `unsupported_journal` | `unsupported_journal_mode` |
| linked/competing/unsupported layout | `invalid_layout` | `unsupported_install_layout` |
| invalid/missing project | `invalid_project` | `invalid_project_root` |
| omitted repo at a package root | `invalid_project` | `project_scope_required` |
| unsupported Python | `unsupported_runtime` | `unsupported_python` |
| ignore not effective | `ignore_required` | `state_ignore_required` |
| invalid state ownership | `invalid_state_path` | `state_path_invalid` |

Doctor and setup share this first-applicable preflight precedence:
`unsupported_python`, `unsupported_install_layout`, `project_scope_required`,
`invalid_project_root`, `state_path_invalid`,
`package_core_modified|package_status_unknown`, `state_ignore_required`,
`unsupported_journal_mode`, `database_busy`, `project_state_unreadable`,
`project_mismatch`, `schema_too_new`, then
`migration_required|setup_required|ready`. Package drift is a doctor warning
but a setup error. A physical non-Git target skips ignore enforcement. Lower
precedence never replaces the process result, though the independent package
warning may accompany a later project error.

Fixed sanitized messages are:

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

Maintenance codes are `not_opted_in` before setup and, once enabled,
`current`, `due`, `deferred`, or `failed` for current runtime state.
Readable maintenance never becomes an envelope warning/error. Deferred means
zero-wait lock contention; failed means bounded artifact failure; both remain
due.

### Effective Git-Ignore Preflight

For setup, preview, and doctor only, inspect the governed target and parents to
the nearest existing/link-like `.git` marker. Inspection failure fails closed
as `state_ignore_required`. No marker means physical non-Git and no subprocess.
With a marker, invoke exactly one shell-free, sanitized, two-second-bounded
equivalent of:

```text
git check-ignore --quiet --no-index -- <canonical-state-directory/>
```

The one target-relative operand is
`.agents/skills/task-governance-tool/state/` or self-host
`task-governance-tool/state/`, with forward slashes and trailing slash.
Exit 0 alone proves effective ignore. Exit 1, timeout, launch failure, or other
result fails closed without output or write. Git decides parent rules,
gitfiles, submodules, and negation; taskgov does not parse `.gitignore`, test
trackedness, edit ignore files, cache, retry, or expose path/pattern/error
detail. Later setup structural revalidation does not repeat this subprocess.

## Task State, Scope, Review, And Completion

### Task Record And State Transitions

The current Task projection includes stable Task/project IDs, title,
description, kind, lane/order, priority, status, blocker/pause reason, review
tier, verification, tags, timestamps, current typed completion evidence plus
its legacy compatibility projection, current review target/generation/base,
Contract pointer, and completion-history coverage. Concise events retain notes
and transitions; events are not a generic payload/log store.

Typed completion storage is exactly `completion_evidence_kind`,
`completion_evidence_revision`, `completion_evidence_reason`,
`external_revision_approved`, `completion_commit_required`, and
`completion_commit_hash`. Current target storage is
`review_target_kind`, `review_target_value`,
`review_target_base_revision`, and generation. Values and their legacy
projection must satisfy one cross-field matrix before storage or output.

Optional work is actionable when ready. Sequential work is actionable only
when every earlier Task in its lane is done or cancelled. That same predicate
guards entry into `in_progress`, `review_pending`, or `done`; paused is
incomplete. Add/edit validates the complete resulting row and both affected
lanes when kind/lane/order/status changes. It rejects inserting or moving an
incomplete predecessor before active, review-pending, or done work with
`sequential_predecessor_incomplete` and no event/write. Blocked lanes never
hide unrelated ready optional work or other ready lanes.

Blocked requires a concise reason. Paused requires a concise reason and is
reachable only from in-progress or review-pending; normal resume is paused to
in-progress. Initial paused is prohibited. Lane values are trimmed, and
explicit/automatic order must fit signed 64-bit.

A done Task is write-locked. Every write except an exact reopen fails
`done_task_requires_reopen`. Reopen alone requires a sanitized reason, changes
done to in-progress, clears completion/blocker/pause/current target, advances
review generation, preserves all history, appends `task_reopened`, applies the
lane guard, and cannot share metadata, note, evidence, confirmation, or other
mutation. Fresh verification, target, review, and completion evidence are then
required.

A review-tier downgrade is allowed only while ready, in-progress, paused, or
blocked, before structured review (`generation=0` and target empty), with a
reason, and without completion evidence, gate confirmation, or transition to
review-pending/done. Once review begins, including after reopen, tier may only
stay or rise. Invalid downgrade is `review_tier_downgrade_forbidden`.
Successful change appends `review_tier_changed` with old/new tier and reason.

### Task Contract

The optional Task Contract copies already-explicit authority and adds no
question or heuristic. Revision 1 is allowed only when scope and acceptance
are explicit in the current user instruction, approved roadmap, or explicit
registration input. Supplying any of `--contract-scope`,
`--contract-acceptance`, `--contract-constraints`,
`--contract-authority-ref`, or `--contract-change-reason` supplies the group;
partial explicit input fails `invalid_argument`.

Revision 1 may be created on add with status ready, in-progress, blocked, or
review-pending, or in one revision-0 transition from ready/blocked to
in-progress. The edit boundary additionally requires empty completion
evidence, target, and generation, and cannot share note, metadata, review-tier,
completion, or confirmation input. Paused/review-pending/done/cancelled Tasks
cannot activate revision 1. Omitted Contract input leaves revision 0 without
making acceptance optional or prompting for fields.

A Contract stores scope and acceptance (each at most 4,000 characters),
optional constraints (2,000), optional initial stable authority reference
(500), and for later revisions a reason (1,000) and timestamp. Purpose remains
in title/description. Text normalizes line endings to LF and outer whitespace;
internal content is preserved.

Later semantic revision is Contract-only, allowed while ready, in-progress,
paused, blocked, or review-pending, and requires at least one changed content
field, a nonempty stable authority reference, and reason. User authority may
use `user_instruction:<task-id>:<next-revision>`; governing authority uses a
repository-relative path plus known revision/hash. Neither stores prompt/body.
A current Task output cannot authorize expansion of that same Task.
Out-of-scope hardening is handed off.

An exact canonical content replay is a successful write-free no-op even if
authority/change labels are omitted or relabeled after their privacy/size
validation. It returns `recorded=false`, current revision, `event=null`, and
`changed_fields=[]`. Same-content concurrency produces one row plus one
replay. Different valid semantic writes serialize; pointer races fail
`contract_write_conflict`.

On later revision, omitted constraints preserve the current value and an
explicit empty value clears it. The exact
`user_instruction:<task-id>:<revision>` form is validated mechanically.
Semantic change accepts only the locked next revision placeholder; exact replay
may accept an older same-Task positive placeholder. A caller-supplied authority
label never creates a revision when Contract content did not change. Omission
may therefore carry forward byte-identical, already-validated legacy M19.7
constraints as immutable lineage; it does not validate caller-supplied legacy
vocabulary or grant new authority. Any explicitly supplied constraints use the
normal strict input guard.

A semantic revision appends immutable history, advances the pointer, clears
completion evidence, preserves generation 0 if review never began or otherwise
clears target and advances generation, moves review-pending to in-progress,
updates time, and appends `contract_revised` atomically. Fresh gates are
required. Done must reopen; cancelled rejects.

Only `task show` exposes the full additive `contract` object: revision, scope,
acceptance, constraints, authority reference, change reason, and creation time.
Revision 0 uses empty strings and null time. Compact/list/current/next/Viewer
omit Contract text.

### Handoff Outbox

For every discovery, classify once:

1. Keep it in the Task when resolution is within accepted scope and current
   authority.
2. Block the affected Task when an unmet condition prevents acceptance and no
   safe authorized resolution remains.
3. Otherwise run the same `handoff record` command immediately and continue.

Safety is orthogonal: report credible risk promptly, block only unsafe affected
work, and continue other safe ready lanes. Pending handoffs never expand or
block source acceptance.

`handoff record` first commits one local sanitized record regardless of Issue
adapter presence. States are only `pending_handoff`, `handed_off`, and
`handoff_withdrawn_by_user`; allowed transitions are pending to either terminal
state. Withdrawal means the user handled/withdrew undelivered work, not Issue
resolution, and is forbidden after any delivery claim.

The outbox stores source Task/Contract revision, bounded summary/rationale,
optional occurrence ID, stable idempotency identity, state, bounded delivery
bookkeeping, and timestamps. It stores no Issue priority/lifecycle/triage,
semantic duplicate decision, `resulting_task_id`, threat model, raw
output/review, secret, stack trace, or diff. Public records never expose an
internal `claim_token`.

Exact source/canonical-payload replay returns the same record. A separate
occurrence requires an explicit stable ID; omission is canonical empty.
Invalid/empty explicit/over-200 occurrence ID is
`handoff_occurrence_invalid`. Summary and rationale are at most 1,000
characters.

Local commit is the success boundary. It may retry one complete fresh
transaction after transient SQLite failure and may replace rejected content
once with a shorter sanitized abstraction. Persistent failure returns
`handoff_not_persisted`, never claims durability, and stops the current unit
until persistence or explicit acceptance of forgetting risk. Delivery absence
or failure after local commit leaves pending and source work continues; enabled
delivery failure adds warning `handoff_delivery_pending` with action
`continue`.

Public leaves are record/list/show/withdraw; base Skill has no dead sync
command. List defaults to pending, oldest `created_at, handoff_id`, limit 20,
maximum 100, and returns exact `total_matching` from the same read snapshot.
Compact rows contain only `handoff_id`, `source_task_id`,
`source_contract_revision`, `summary`, `state`, `created_at`, and
`updated_at`. Full public records never expose internal claim tokens and are
revalidated before output. `task show.handoff_summary` contains exact counts
for all three states.
Successful withdrawal reports
`changed_fields=["state","withdraw_reason","withdrawn_at"]`.

Adapter delivery is not implemented and remains disabled until a separately
approved versioned local Issue intake exists. Doctor therefore reports the
local pending count with adapter/due false. Task Skill never opens an Issue
database, shells, uses a URL/network/GitHub, imports, reconciles, prioritizes,
or mutates Issue lifecycle. Any later adapter must preserve the same local
record command and source-task non-blocking boundary.

### Effort Advisory

The optional project-scoped profile exists only at
`config/effort-advisory.json`; taskgov never creates or edits it. Strict
profile v1 requires `profile="informational-v1"` and Boolean `enabled`.
Optional nonnegative integer thresholds are limited to `changed_files`,
`changed_lines`, `changed_modules`, `contract_revisions`, and `handoffs`, and
exceed only when measurement is greater than the threshold. Unknown/duplicate
keys or invalid values disable the profile with a bounded diagnostic.

`task effort <task-id>` reports those five deterministic metrics when covered.
Git measurement is read-only; Contract/handoff counts use structured DB data.
Missing coverage or dirty/uncertain/non-Git endpoints and overlapping active
work produce `unknown`, never inference. Optional basis capture on the first
in-progress write is best-effort and never blocks start. An absent/disabled
profile performs no Git work; disabling after a prior basis may retain only
hidden activity counters.

The basis captures project/subject activity generations and
`other_active_at_capture`; later attribution is exclusive only when both
endpoints and generation bridge remain reliable. A bounded observation failure
may report `activity_generation_uncertain`. These fields are advisory
bookkeeping, never Task authority or a completion gate.

## Approved Post-MVP Extension: TG-M16 Reduced Loop Discipline Trial

The nonempty deterministic `exceeded` list is the sole predicate that changes
an enabled valid result's `data.suggested_action` and matching threshold-warning
action from `continue` to `reconcile_scope`. Absent, disabled, invalid,
unknown-only, and non-exceeded observations continue. The warning remains
`effort_advisory_threshold_exceeded`, key
`effort_advisory.threshold_exceeded.v1`, and message
`One or more configured effort thresholds were exceeded.` Any number of
exceeded metrics creates at most one session-local episode.

The signal is non-blocking. It never asks the user, writes a handoff, changes
Task status, Contract, acceptance, review tier/evidence, completion evidence,
pauses, blocks, or fails a Task. It adds no second observation, command, or
green-path judgment.

Reconciliation guidance is session-local. Without new evidence, after two
materially equivalent failed repair attempts the agent must not execute a
third equivalent repair. Command spelling, working directory, wrapper, Task
label, or execution-unit label alone is not new evidence. A safe diagnostic or
genuinely different repair is new only when it can materially change the
causal hypothesis, authorized repair, or expected outcome.

Never weaken a test merely to pass. A test may change only when current
authority shows it is wrong; a Task Contract or acceptance change still needs
later explicit authority. A failing test alone proves neither scope nor
authority. Work inside accepted scope/current authority remains in the Task;
out-of-scope work is handed off; a blocker is recorded only after safe
authorized work is exhausted; paused is reserved for temporary interruption;
unrelated safe ready lanes continue and remaining decisions are batched.

Review repair still requires a fresh target and fresh current-generation
review. A result that remains blocking counts as one unsuccessful remediation
cycle. Without new evidence, two materially equivalent unsuccessful review
cycles prohibit a third equivalent cycle. Completion still requires qualifying
fresh PASS receipts; unrelated safe lanes continue.

No attempt counter, persisted latch, semantic-failure parser, automatic
Task/Contract/status/handoff mutation, mandatory checkpoint, project test
strategy, instruction-chain adoption, or workflow engine is added. TG-M16.4
synchronized this guidance and its behavioral acceptance; setup creates no
bootstrap Task and edits no consuming-project instruction.

## Review And Completion

### Review Target, Receipt, And Finding Ledger

A current target is one of:

- `git_commit`: a canonical existing commit;
- `git_snapshot`: an internally captured staged snapshot;
- `diff_fingerprint`: exact `sha256:<64-lowercase-hex>`; or
- `external_revision`: a caller-approved durable external identity.

Every target set, including identical or A-to-B-to-A values, increments a
positive signed-64-bit generation. Historical receipts never reactivate.
Target setting is forbidden on done Tasks.

`review receipt add` binds a sanitized reviewer key, independent/fallback/
not-required kind, PASS or changes-requested verdict, summary, user-approval
flag when required, and timestamps to the exact current target/generation.
One reviewer cannot replace or contradict a receipt in the same generation.
`review finding add` requires a same-project/Task receipt and severity;
resolution preserves the original finding.

Tier 2 requires two PASS receipts from distinct independent reviewer keys;
Tier 1 requires one. Tier 2 fallback requires one documented self-review PASS
plus explicit user approval; Tier 1 fallback requires the self-review PASS.
Tier 0 permits not-required only with a mechanical-change rationale. Any
current-generation changes-requested receipt, unresolved high/medium finding
from any recorded generation, or fresh-review-required condition blocks
completion. Resolving a blocking finding is insufficient: advance to a newer
target and obtain fresh receipts.

Reviewer keys prove only normalized-string distinctness. The trusted caller
attests the actual returned result; taskgov neither launches/authenticates a
reviewer nor proves person/model/process identity, independence, expertise,
provenance, or summary truth.

Public receipt kinds are exactly `independent`, `self_review_fallback`, and
`not_required`; verdicts are `pass`, `changes_requested`, and `not_required`.
Detailed rows remain in `review_receipts` and `review_findings`. Completion
cycle basis selection is independent of the existing diagnostic
`fallback_kind` projection.

`task show.review_evidence` exposes the current target/generation, tier
requirement, qualifying current-generation counts, fallback state, bounded
recent receipts/findings, and blocking counts including
`changes_requested_current_generation`. It never emits raw review content.

### Git Snapshot And Target Binding

`review target set --kind git_snapshot` accepts no caller revision. It reads a
canonical HEAD and stage-0 index, rejecting unborn HEAD, unmerged index,
zero-object intent-to-add, and sparse-directory entries. A canonical manifest
binds base commit, mode, object ID, and raw path bytes; its SHA-256 plus base
are stored. No Git state, hook, config, worktree, object, index, or ref changes.
Unstaged/untracked content is outside the snapshot.

At `git_commit` completion, review binding succeeds when the current target is
the identical canonical commit, or when a snapshot target's completion commit
has exactly one parent equal to the stored base and its commit-tree manifest
has the identical fingerprint. Merge/wrong-parent/tree mismatch is
`review_target_mismatch`. External completion requires identical external
target; commit-not-required requires a diff fingerprint.

Git commits are resolved read-only. A unique abbreviation is accepted and
stored as a canonical full object ID; missing, ambiguous, or non-commit input
is `git_commit_not_found_or_ambiguous`. Done transition re-resolves stored Git
completion and target evidence immediately before update. Git observation is
outside the SQLite writer.

### Review Packet

`review prepare <task-id>` is bounded read-only stdout generation for all four
target kinds. Missing target returns `review_target_missing` and
`review target is required before preparing a review packet`.

Snapshot targets are recaptured; commit targets resolve the commit and list
first-parent changes (root against empty tree); diff/external targets perform
no Git and report paths unavailable. Target-specific behavior is internal and
adds no Skill branch.

Data keys are exactly `task`, `contract`, `review_target`,
`changed_paths_available`, `changed_paths`, `changed_paths_total`,
`changed_paths_truncated`, `review_focus`, `required_output`, and
`receipt_command`. Task contains ID/title/status/verification/tier; Contract
contains revision/scope/acceptance/constraints; target contains kind/value/base/
generation. No diff, result, raw output, prompt, conversation, secret, absolute
path, or caller-authored focus is included.

At most 100 bytewise-ordered changed paths, 240 UTF-8 bytes each and 16,384
bytes total, are returned. Unsafe paths fail `review_packet_path_unsafe` with
`review packet contains an unsafe project path`; no path is hidden. Complete
text or JSON is at most 32,768 UTF-8 bytes or fails
`review_packet_too_large` with `review packet exceeds the supported size`.
Git observation uses at most 10 subprocesses.

Four common focus rows are Contract compliance; state/completion integrity;
privacy/target safety; and verification/regression. The fifth mechanically
states the exact snapshot, commit, supplied diff-fingerprint material, or
supplied external material boundary. Required output is verdict PASS or
CHANGES_REQUESTED, severity-ordered exact file/line findings, remaining risks,
and recommended changes. Receipt command is a non-executed attestation shape.

Text order is `Task`, `Status`, `Verification`, `Contract revision`, `Scope`,
`Acceptance`, `Constraints`, `Review target`, `Changed paths`, `Review focus`,
`Required output`, `Receipt command`, LF-terminated. After Git, a second short
read revalidates Task, Contract, and every target field/generation; drift fails
`review_packet_stale` with `review context changed while preparing the packet`.

### Completion Evidence And Commands

Every done transition supplies exactly one:

- `git_commit`: canonical existing target-project commit;
- `external_revision`: revision plus reason and
  `--external-revision-approved`; or
- `commit_not_required`: explicit assertion that no managed material changed,
  with no revision.

External revision is never inferred. Missing approval is
`external_revision_approval_required`. The compatibility
`--completion-commit-hash` is Git-only and cannot store a generic string.
Existing migrated legacy values remain `legacy_unverified`; they are not
retrospectively strengthened or invalidated.

Managed material is source-controlled content or a user-approved durable asset
whose final state must be traceable. Generated local state, ignored SQLite,
caches, locks, logs, and scratch files are not managed material unless current
authority explicitly says otherwise. `commit_not_required` is valid only when
no managed material changed.

Both thin `task complete` and compatibility `task edit --status done` require
verification and review confirmations, typed completion evidence, sequential
eligibility, a matching current target, sufficient current-generation review,
and no blocking finding/receipt. They use the same transition service.

Thin completion emits `command="task.complete"` and data exactly `task`,
`changed_fields`, and `event`; text starts
`Task completed: <task-id>`. It accepts no non-completion edit.

`task complete --check` accepts the same proposed evidence and confirmations,
is read-only, invokes the exact shared fail-fast validator, and is not an
authorization token. It captures one coherent basis, closes before Git, then
revalidates in a second coherent read. Drift returns not-ready with
`completion_check_stale`.

Check output is at most 8,192 UTF-8 bytes. Data is exactly `task_id`, `ready`,
`status`, `blocking_codes`, `contract_revision`,
`review_target_generation`, `completion_evidence_kind`, and
`suggested_action`. It returns only the first code in existing validation
order. Allowed readiness codes are `invalid_status_transition`,
`sequential_predecessor_incomplete`, `verification_required`,
`review_required`, `completion_evidence_conflict`,
`external_revision_approval_required`, `commit_required`,
`git_commit_not_found_or_ambiguous`, `invalid_review_evidence`,
`review_target_required`, `review_target_mismatch`,
`review_finding_unresolved`, `review_changes_requested`,
`review_receipts_insufficient`, and `completion_check_stale`. Parse/privacy,
not-found, project/schema/journal/busy/storage/internal failures remain command
errors.

Check text is exactly three LF-terminated lines:
`Task <task_id>: ready|not ready`,
`Blocking: none|<comma-separated codes>`, and
`Suggested action: <bounded suggested_action>`.

### Typed Checkpoint

`task checkpoint <task-id>` requires summary and next action and accepts up to
eight unresolved risks. UTF-8 limits are 1,024 for summary, 1,024 for next
action, 512 per risk, 4,096 combined risks, and 6,144 total caller payload.
One append-only row stores only that content, Task/project, Contract revision,
and time. The same transaction adds event type `checkpoint_recorded` with
fixed summary `Checkpoint recorded`, does not copy content into the event, and
does not update `tasks.updated_at`.

Exact replay against the latest same-Contract checkpoint is write-free with
`replayed=true`. Done is immutable. Checkpoints are optional, never automatic,
and change no status, scope, acceptance, selection, review, evidence, or gate.
`task show`/default current expose only the latest object.

The stored-summary read path alone retains bounded compatibility for the
already-recorded M19.7 numeric `dispatch_authorization` JSON field and returns
the original summary unchanged. New checkpoint input uses the normal strict
guard, and the compatibility read neither records nor authorizes an external
operation.

Command data is exactly `checkpoint`, `created`, `replayed`, and `event`.
Checkpoint keys are `checkpoint_id`, `task_id`, `contract_revision`,
`summary`, `next_action`, `unresolved_risks`, and `created_at`. New event
output contains only ID, type, and time; replay event is null. Text is
`Checkpoint <checkpoint_id>: recorded|replayed for task <task_id>\n`.

## Completion Cycle History

Schema v15 adds `tasks.completion_history_coverage` (`legacy_unknown` or
`complete`, default legacy unknown), nullable internal
`task_events.completion_cycle_id`, and append-only
`task_completion_cycles`. Schema v16 is a marker-only
`completion_cycle_capture_activation` migration: it adds no object but prevents
a schema-v15 binary from writing after native capture activates.

Each cycle ID is `tg_completion_cycle_<16-lowercase-hex>` with a positive
signed-64-bit per-Task ordinal beginning at 1 and increasing exactly by one.
It stores ownership; origin (`native_done` or `legacy_current_done`);
completeness (`complete` or `partial`); completion/record times; Contract
revision; tier; specified/unspecified verification expectation and nullable
attestation; the exact six-field completion evidence; exact four-field review
target; and versioned accepted gate-basis counts plus up to two qualifying
receipt IDs.

The migration is named `completion_cycle_history`. Internal cycle fields
include `recorded_at`, `gate_basis_version`, and `review_basis_kind`; these are
project-owned validated values and do not broaden the public allow-list.

Native rows are complete, have non-null completion time, true verification
attestation, gate basis v1, zero changes-requested/open-high/open-medium/
fresh-review-required, and a valid tier basis:

- Tier 1/2 selects enough distinct independent PASS receipts ordered by
  `reviewer_key, review_receipt_id`;
- only when insufficient, Tier 1/2 selects the first valid fallback by receipt
  ID, including Tier 2 approval;
- Tier 0 selects the first valid not-required receipt.

The stored v1 basis kind is exactly `independent_passes`,
`self_review_fallback`, or `not_required`; a v0 legacy row uses `unknown`.

Legacy rows are partial, have null attestation, unknown review basis, gate
basis v0 with null counts and no receipt IDs, and may preserve `none` or
`legacy_unverified` without strengthening it. Cycles are immutable and never
satisfy a current gate.

Migration 15 is transactional: every existing Task remains
`legacy_unknown`; existing event bytes/links remain unchanged; each currently
done Task receives exactly one ordinal-1 partial cycle copied only from its
current projection; other statuses receive none. It never parses event prose
or infers reopened cycles.

Activation to v16 rereads current done unknown-coverage Tasks in binary Task-ID
order. It reuses an exact latest un-reopened partial cycle or appends one next
partial cycle; mismatch, overflow, ownership error, or inconsistent link rolls
back the whole activation and marker. From v16, new Tasks explicitly receive
complete coverage; older Tasks remain legacy-unknown permanently.

`legacy_history_incomplete` is true when coverage is not complete, any cycle is
partial, or any `task_reopened` event has null cycle link. A fresh v16 Task with
no cycle is complete-history and false.

Both done paths insert one complete cycle, update Task, create the existing
linked completion event, record effort/Viewer state, and commit atomically
under one short writer after all external observation. Reopen requires the
current done projection to equal the latest un-reopened cycle; it links the
new reopen event and resets current state atomically. The sole compatibility
bridge may create an ordinal-1 partial cycle for an unknown-coverage done Task
with no cycle. Other mismatch is `completion_history_inconsistent` and no
write.

Default `task show` adds exactly one `completion_history` object with
`total`, `returned_count`, `truncated`, `legacy_history_incomplete`, and
`cycles`. It returns the newest complete-row prefix, maximum 10. Each cycle is
at most 8,192 bytes and the complete component at most 32,768, measured with
canonical compact UTF-8 JSON including actual wrapper counts. It never skips
or partially serializes an oversized row.

Public cycle fields are exactly `completion_cycle_id`,
`saved_cycle_ordinal`, `origin`, `completeness`, `completed_at`,
`contract_revision`, `review_tier`, `verification_expectation`,
`verification_attestation`, `completion_evidence`, `review_target`, and
`gate_basis`. Nested evidence has `kind`, `revision`, `reason`,
`external_revision_approved`, `completion_commit_required`, and
`completion_commit_hash`; target has `kind`, `value`, `base_revision`,
`generation`; gate basis has `version`, `kind`,
`required_independent_passes`, `qualifying_independent_passes`,
`changes_requested`, `open_high`, `open_medium`, `fresh_review_required`, and
`qualifying_receipt_ids`.

Gate-basis v0 emits six null counts and an empty receipt array; v1 emits integer
counts and exactly one qualifying ID for Tier-0/Tier-1/fallback or two for
Tier-2 independent basis. Attestation is true or null, never false. Public
events remain only `task_event_id`, `task_id`, `project_id`, `event_type`,
`summary`, and `created_at`; internal cycle link is never emitted.

Before a cycle is emitted, every public free-form completion-evidence and
review-target text field is revalidated with the normal strict privacy guard.
Completion history has no M19.7 compatibility exception. Private or corrupt
stored text fails with the existing sanitized
`completion_history_inconsistent` result rather than being redacted or
returned.

Text show prints history returned/total/truncation/incompleteness and only the
latest cycle's ordinal, origin/completeness, time, evidence kind, target
kind/generation, and review-basis kind. Other list/current/next/effort/packet
outputs remain unchanged and history has no option or pagination.

The only new error is exit-2 `completion_history_inconsistent` with exact
message `stored completion history is inconsistent`. It covers required-cycle
absence, projection mismatch, reused reopen link, ordinal overflow, invalid
gate relationship, and cycle/event ownership conflict; it is not a completion
check blocker code.

## Approved Temporary TG-M20S Task-Decomposition Observation Boundary

TG-M20S is a repository-development observation, not product behavior. Its
authority is Task `tg_task_ddfbf721eced8c58`, Contract revision 1, with exact
authority reference
`conversation_decision:2026-08-01:interrupt-successor-task-decomposition-observation`.
It compares against product baseline
`43c91d5987b0c35c66f834789aea782e98dcaff7` and requires the installable
package subtree to equal Git tree
`529abf7ac4e4ed778b383c90b6ac5f2fedc71615`. The matching package tree at the
later TG-M21.1 completion revision does not change the comparison baseline or
make that Git revision observation authority.

TG-M20S.1 freezes and validates the temporary protocol and minimal M20.4-only
root harness before any trial. TG-M20S.2, Task
`tg_task_e591f30d546ba69e`, may then launch only these replacement pairs, in
this order, with at most one fresh `broad` and one fresh `bounded` arm per
pair:

1. `sp_user_expansion_alternate`;
2. `sp_in_scope_discovery_alternate`; and
3. `sp_cross_module_failure_alternate`.

They replace only the three unavailable category slots from M20.4. The
reviewed `sp_multi_outcome_intake` pair and eligible Handoff control are fixed
prior decision inputs and are neither reconstructed, rescored, nor rerun. The
successor denominator therefore begins at `E=1`, `Q=1`, `U=3`, where `E` is
the eligible count among the four conceptual non-control pairs, `Q` is the
qualifying count among those eligible pairs, and `U=4-E`.

A replacement pair is eligible only when both arms have the exact frozen
authority, baseline, package tree, protocol, cohort, workload digest, and
ordered episode identities, and every required machine measurement and
observer attestation for both arms is eligible. A partial or excluded record,
identity mismatch, inherited context, cross-trial visibility, coaching,
privacy/schema failure, or unknown predicate field makes the pair unavailable;
unknown and unavailable values are never zero or favorable evidence.

An eligible pair qualifies only when every bounded-arm episode attests
`acceptance_independent`, `verification_independent`,
`commit_independent`, and `completion_independent` as `yes`, and at least one
of these exact improvements holds:

- a same-ID broad episode has at least one independence value `no` and at
  least one position of its frozen machine delta vector is greater than the
  bounded episode's corresponding position;
- the bounded sum of Contract-revision deltas is at least one lower than the
  broad sum; or
- the bounded sum of review cycles is at least one lower than the broad sum.

With the retained eligible Handoff control and no evidence conflict, the first
new eligible qualifying pair makes `Q>=2` and yields
`proceed_to_design`; collection stops after that complete pair. The negative
rule is `E>=3` and `Q+U<2`; from the inherited values it can be reached only
when all three replacements are eligible and nonqualifying, producing
`E=4,Q=1,U=0` and `no_follow_up`. Fixed-inventory exhaustion without either
rule yields `observe_more`. A positive result authorizes only a separately
approved design proposal; no result registers a design Task or activates Task
splitting.

Fresh subjects receive no parent conversation, hypothesis, rubric, expected
verdict, suspected failure, or preferred solution. Arms use isolated roots;
paired arms use the same workload and model/tool/permission cohort; an agent is
never reused. Raw requests, prompts, chats, reasoning, reviews, diffs, paths,
streams, credentials, and control bytes are never retained. Every started arm
has one reduction attempt and no rerun. TG-M20S.2 removes all raw and ephemeral
material and the temporary harness, tests, fixtures, locks, and reduced corpus,
retaining only the bounded terminal no-rerun receipt and non-authoritative
history allowed by the design.

TG-M20S adds no Skill instruction, public command, SQLite object, Viewer
field, telemetry, automatic Task creation, runtime split, parent/child Task,
network behavior, or target-project mutation. It cannot satisfy current
verification, review, completion, or release gates.

## Approved But Inactive TG-M21 Verification Receipt Contract

This section is an approved implementation acceptance boundary, not current
v0.10.0 behavior. The registered TG-M21.2 and TG-M21.3 units remain inactive
until the TG-M20S interrupt, authority-layout predecessors, and their own
synchronization gates complete. Until then, the product remains schema v16
with 20 command leaves, no Verification Receipt table or output, the existing
`--verification-complete` gate, and the current Skill call order. Nothing in
this section is invocable or advertised by the current package.

### Receipt Meaning And Record

One future Receipt represents one verification run reported by the trusted
caller after the caller ran it outside taskgov. The only material observation
facts are:

```text
command_label result source_revision duration scope_coverage
```

Stable ownership IDs, current Contract revision, a verification-expectation
digest, and recording time are structural binding metadata, not additional
observed facts. The proposed public Receipt has exactly:

```text
verification_receipt_id project_id task_id contract_revision command_label
result duration_ms scope_coverage source_revision created_at
```

`verification_receipt_id` is `tg_verification_receipt_` plus 16 lowercase hex
characters. `command_label` is a sanitized nonempty label of at most 200
characters, not a command line or argument list. `result` is exactly `pass`,
`fail`, or `timeout`. `duration_ms` is a nonnegative signed-64-bit integer.
`scope_coverage` is exactly `full` or `partial`; `full` is the caller's claim
that this run covers the entire exact current Task `verification` text, while
`partial` is audit context and cannot satisfy completion.

`source_revision` is not caller-authored text. It is the exact locked current
review-target object with keys `kind`, `value`, `base_revision`, and
`generation`. The Receipt writer copies that tuple, the current Contract
revision, and the SHA-256 of domain-separated exact stored verification text
in one short transaction. The digest is internal binding data and is not in
the public Receipt. ID and canonical UTC `created_at` are also tool-owned.

After activation, the normal order becomes: finish exact material, set the
existing review target and retain its returned generation, run the governed
verification against that material, record the Receipt with that generation
as the expected basis, then prepare and record review. This is one additional
green-path governance call: the default Tier-2 no-finding bound becomes 10, or
11 only when Effort Advisory is mechanically enabled. The current Skill order
and nine/ten-call bounds remain unchanged until the activation unit
synchronizes them.

Receipt recording is allowed only for an in-progress or review-pending Task
with nonempty verification text and a nonempty current review target. At most
one immutable aggregate Receipt is allowed per Task target generation. It
changes no Task timestamp, event, status, Contract, target, review evidence,
completion evidence, or Handoff. A second attempt, including after `fail`,
`timeout`, or `partial`, requires explicitly setting a fresh target generation;
otherwise it fails with `verification_receipt_already_recorded` and message
`verification evidence is already recorded for the current target`.

Taskgov does not run the verification, resolve a command label to executable
content, authenticate the caller or process, assess test quality, infer
coverage, or prove the result or that the run actually exercised the copied
target. Invoking Receipt add is the caller's attestation of those facts. It
stores no command body or argument, exit code, stdout/stderr, log, environment,
exception, stack trace, prompt/chat, diff, credential, or free-form coverage
prose. Approved exceptions, result-file import, configured runners,
signatures, and debug retention are outside this initial contract.

### Current Eligibility And Completion Gate

A Receipt is exact-current only when all of its project, Task, Contract
revision, verification-expectation digest, and complete source-revision tuple
equal the locked current values. All other Receipts remain append-only audit
history and never reactivate.

The current explicit `--verification-complete` assertion remains required for
every done transition. When Task `verification` is empty, no Receipt is
required and the current attestation behavior is preserved. When it is
nonempty, completion additionally requires the unique exact-current Receipt to
have `result=pass` and `scope_coverage=full`. A missing Receipt is
`verification_receipt_required`; any other result/coverage combination is
`verification_receipt_blocking`. Recovery requires explicitly setting a fresh
target generation and recording fresh verification; setting an identical
target already advances generation under the current target contract. Partial
coverage never aggregates mechanically because taskgov owns no project test
strategy.

A semantic Task `verification` edit after review targeting begins clears the
current target and completion evidence, advances target generation, moves
review-pending back to in-progress, and requires fresh verification and
review. Contract revision and reopen retain their existing invalidation
behavior. None of these cases deletes historical Receipts. A failed Receipt
does not itself pause, block, revise, hand off, or otherwise mutate the Task.

The immutable completion cycle already stores the exact Task target tuple.
Schema v17 also adds internal `verification_basis_version`, nullable
`verification_expectation_digest`, and nullable `verification_receipt_id`
fields to each completion cycle. Existing cycles migrate as version 0 with
both nullable fields null. Every post-activation native cycle is version 1 and
stores the same domain-separated digest computed from its exact Task
verification text, including the empty string. A nonempty verification
expectation additionally requires a foreign-key link to the unique qualifying
exact-current Receipt, while an empty expectation requires a null link.
Every normal post-activation native completion must insert version 1. The sole
existing reopen compatibility bridge may still insert version 0/null/null only
for its exact unknown-coverage done/no-cycle case; it remains
`legacy_current_done` and partial and cannot satisfy a current gate. No other
post-migration version-0 insert is valid. This discriminator makes an absent
link honest legacy lineage for old cycles and a fail-closed inconsistency for
a new native cycle instead of inferring validity merely from whether a Receipt
row happens to exist.

The digest and link are internal and do not add a completion-history field.
The linked Receipt must have the same project, Task, Contract revision,
stored expectation digest, and complete target tuple as the cycle and must be
`pass/full`.
Existing cycles and pre-v17 completions receive no synthesized Receipt and
keep their existing public meaning. Initial activation adds no new public
completion-history or Viewer Receipt projection.

Completion-check fail-fast ordering retains the existing missing-attestation
and target checks, then applies the missing/blocking Verification Receipt gate
before review-receipt sufficiency. The proposed new readiness codes and fixed
messages are:

| Code | Message |
|---|---|
| `verification_receipt_required` | `current verification evidence is required` |
| `verification_receipt_blocking` | `current verification evidence does not satisfy the required result and coverage` |

Invalid stored Receipt structure or binding fails closed with
`invalid_verification_evidence` and message
`stored verification evidence is inconsistent`; no unsafe value is returned.
An invalid completion-cycle basis version or link fails through the existing
`completion_history_inconsistent` contract before a success projection.

### Proposed Public And Read Projection

After activation, the sole new public leaf is number 21:

```text
taskgov verification receipt add
```

It accepts Task ID plus exactly `--command-label`, `--result`,
`--duration-ms`, `--scope-coverage`, and
`--expected-target-generation`, together with applicable common `--repo`,
`--json`, and `--read-only`. Expected generation is a positive integer copied
from the target-set result and is only an optimistic concurrency guard; it is
not a second source revision. The writer compares it under the same lock as
Contract, verification expectation, and target capture. Mismatch fails with
`verification_basis_stale` and message
`verification target changed after the reported run`, with no row or
maintenance. The command accepts no caller source revision, Contract revision,
timestamp, command body, result body, or arbitrary file. Successful data is
exactly `receipt`; read-only rejects before any database or maintenance write.

After applicable common CLI, project, and state preflight, Receipt-add
validation is fail-fast in this exact order: `--read-only`; Task ID and the
five caller values in command-label, result, duration, coverage, expected-
generation order; Task existence; done-state rejection; other Task status;
nonempty verification expectation; structurally valid current target;
expected-generation equality; and same-generation uniqueness. The fixed
service failures are:

| Condition | Code | Message |
|---|---|---|
| `--read-only` | `invalid_argument` | `verification receipt add cannot run with --read-only because it writes the database` |
| invalid command label | `invalid_verification_evidence` | `command_label must be a non-empty sanitized label of at most 200 characters` |
| invalid result | `invalid_verification_evidence` | `result must be one of pass, fail, or timeout` |
| invalid duration | `invalid_verification_evidence` | `duration_ms must be a nonnegative signed-64-bit integer` |
| invalid coverage | `invalid_verification_evidence` | `scope_coverage must be full or partial` |
| invalid expected generation | `invalid_verification_evidence` | `expected_target_generation must be a positive signed-64-bit integer` |
| done Task | `done_task_requires_reopen` | `done task writes require an explicit reopen` |
| any other disallowed status | `invalid_status_transition` | `verification evidence may be recorded only for an in-progress or review-pending task` |
| empty Task verification | `verification_expectation_required` | `task verification must be specified before recording verification evidence` |
| missing current target | `review_target_required` | `set a current review target before recording verification evidence` |
| expected generation differs | `verification_basis_stale` | `verification target changed after the reported run` |
| Receipt already exists | `verification_receipt_already_recorded` | `verification evidence is already recorded for the current target` |

Task ID syntax/privacy and not-found retain their existing codes. Command-label
privacy rejection retains the common `privacy_rejected` contract and occurs at
that field's position. A malformed stored target or Receipt uses
`invalid_verification_evidence`, not a missing-target code. Concurrency is
rechecked under the writer lock in the same semantic order; no failed call
publishes backup or Viewer maintenance.

Successful text is exactly three LF-separated lines with no event line:

```text
Verification receipt recorded: <verification_receipt_id>
Result: <result>  Coverage: <scope_coverage>
Source: <kind>/generation <generation>
```

`task show` alone adds `verification_evidence` with exactly `expectation`,
`contract_revision`, `source_revision`, `gate`, `counts`, and
`recent_receipts`. Gate contains `required`, `satisfied`, `blocking_code`, and
`qualifying_receipt_id`. Counts contains `receipts_total`,
`receipts_exact_current`, `qualifying_exact_current`, and
`blocking_exact_current`. Recent receipts are newest-first, at most 10, and
use the public Receipt allow-list. The existing Task target remains the source
of the current source-revision object. Other list/current/next/compact/review
packet projections remain unchanged.

The projection types and null rules are fixed:

- `expectation` is the exact existing Task verification string;
  `contract_revision` is a nonnegative integer;
- `source_revision` is null when no current target exists. Otherwise it has
  exactly string `kind`, string `value`, nullable string `base_revision`, and
  positive integer `generation`; base is a full Git object ID only for
  `git_snapshot` and null for every other kind;
- `gate.required` and `gate.satisfied` are Booleans;
  `gate.blocking_code` is null or exactly `review_target_required`,
  `verification_receipt_required`, or `verification_receipt_blocking`; and
  `gate.qualifying_receipt_id` is null or one Receipt ID;
- all four count values are nonnegative integers. Exact-current, qualifying,
  and blocking counts are each zero or one; and
- `recent_receipts` is an array ordered by
  `created_at DESC, verification_receipt_id DESC`.
  Every row has exactly the public Receipt fields above, including its nested
  `source_revision`; no digest or internal cycle link is exposed.

For an empty expectation, the gate is `required=false`, `satisfied=true`, with
both nullable fields null regardless of target state. For a nonempty
expectation on a non-done Task, no target yields `review_target_required`; a
target with no Receipt yields `verification_receipt_required`; a current
non-`pass/full` row yields `verification_receipt_blocking`; and a current
`pass/full` row yields satisfied with its ID. A legacy done Task whose matching
completion cycle has basis version 0, whether migrated or created by the sole
compatibility bridge, is an explicit legacy exemption:
`required=false`, `satisfied=true`, both nullable fields null, and
`source_revision` may be null. A version-1 done cycle must obey its stored
expectation/link rule and any mismatch fails closed instead of projecting a
gate.

After activation, successful JSON `task.show` appends this one top-level key to
its current exact data contract. Failure data also contains
`verification_evidence=null`. Text `task show` remains byte-for-byte unchanged
and does not summarize Receipt state; agents use JSON for the new gate.

There is no Receipt list/show/import/export command and no Viewer Receipt
panel or snapshot field in the initial activation. The Viewer must only accept
source schema v17 while retaining snapshot v4 content. Its existing bounded
batch completion-history read internally joins only the Receipt fields needed
to validate version-1 cycle links, fails closed on inconsistency, then discards
them; no Receipt dataset or fact enters the snapshot. Receipt writes are not
Viewer-relevant and perform no Viewer refresh; a successful write remains
backup-eligible through the existing post-commit coordinator. Failed or read-
only calls invoke neither artifact path.

### Migration And Activation Boundary

The proposed schema-v17 migration is named `verification_receipts`. It creates
one append-only Receipt table and adds the three internal completion-cycle
basis fields through the storage/repository layer. Receipt ownership, target,
uniqueness, link, and qualifying relationships are validated in SQLite and
again on read. Existing cycle rows receive only the version-0/null-digest/
null-link legacy discriminator; the migration synthesizes no Receipt from Task
verification prose, events, `verification_attestation`, completion cycles, M20
observations, command history, or review receipts. Existing done Tasks and
cycles therefore remain honest legacy attestation history. The insert guard
also preserves the pre-existing sole compatibility bridge's exact
`legacy_current_done` partial version-0/null/null shape while rejecting every
other new version-0 cycle.

TG-M21.2 implementation must synchronize specification, design, roadmap, CLI help and
JSON/text contracts, completion check, Skill/reference guidance, schema and
migration validation, Viewer source compatibility, package manifest, release
checker, and focused offline tests in the approved activation unit. It must
retain the present 20-leaf and schema-v16 behavior until that activation unit
completes. The proportional-verification guardrail remains `observe_more`.
The separately approved TG-M20S Task-decomposition observation is an interrupt
predecessor, not M21 content, and activates no decomposition behavior.

## SQLite, Migration, And Concurrency

### Initialization And Supported Schemas

`setup` is the sole public initializer and migrator. No Task, handoff, review,
doctor, read, or write command creates/migrates a missing/old database.
Missing state is `db_not_initialized`; supported older state is
`migration_required`; a newer schema is `schema_too_new`. Old binaries reject
newer state and never downgrade/write it.

Fresh setup creates schema v16. Structurally complete contiguous source schemas
v1-v15 are setup-only migration inputs; v16 is idempotent current state.
Schema sequence is:

| Version | Durable addition |
|---:|---|
| v1-v4 | original Task/event/project and completion-evidence lineage |
| v5 | structured review target, receipt, and finding state |
| v6 | Git-snapshot base revision |
| v7 | local handoff outbox |
| v8 | immutable Task Contract revisions |
| v9 | optional Effort basis/activity metadata |
| v10 | maintenance opt-in, policy, latest backup/outcome/applied retention |
| v11 | managed backup generation ledger |
| v12 | append-only checkpoints |
| v13 | Viewer source/render generation and outcomes |
| v14 | stable identity, binding/history, and cleanup metadata |
| v15 | completion-cycle history |
| v16 | marker-only native capture activation |

Each migration is transactional, idempotent, rollback-tested, validates
contiguous history and required objects/rows, preserves project/business IDs
and durable records, and passes `quick_check` and foreign keys. Acceptance
retains the realistic 12-Task/191-event fixture and historical completion/
review trace through every supported source version. No migration parses
private prose to invent structure.

### Operational Read/Write Boundary

Operational databases support rollback-journal mode only. Before opening, the
tool inspects the header and adjacent `-wal`/`-shm`; persistent WAL state fails
`unsupported_journal_mode` with
`task database uses unsupported WAL journal mode` before connection, sidecar,
checkpoint, deletion, or conversion. Existing rollback `-journal` is left to
SQLite locking/recovery.

Every read uses `mode=ro`, `PRAGMA query_only=ON`, and one explicit transaction
covering schema, project identity/binding, and all rows/counts in a response.
`immutable=1` is prohibited. Reads return one committed-consistent result or a
structured concurrency error.

`task next` has the documented two-read advisory boundary. Enabled
`task effort` reads Task/Contract/handoff/basis once, closes before Git, then
uses a second validated generation read only when a stored basis exists. The
generation comparison bridges phases. Post-Git busy or newly observed WAL
fails; other bounded observation uncertainty may remain an advisory.

Git resolution, snapshot capture/comparison, completion validation, and Effort
observation finish before `BEGIN IMMEDIATE`. Under the short writer, services
reread schema, identity/binding, Task, lane, Contract, target generation/base,
review rows, and completion basis and reject stale state atomically. No SQLite
writer is held during Git, backup copy, Viewer rendering, cleanup, or another
subprocess.

Residual `SQLITE_BUSY`/`SQLITE_LOCKED` after the normal driver wait is exit 2
`database_busy` with
`task database is busy; run the command again later` for reads and writes. The
tool adds no retryable flag, longer timeout, sleep, backoff, or generic retry;
handoff record's one complete retry is the sole exception. Failed writes leave
no row/event/receipt/Git/target change.

## Stable Project Identity And Relocation

### Identity, Binding, Resolver, And Source Selection

Schema v14 has exactly two schemes:

- migrated v1-v13 databases retain their byte-identical legacy project ID as
  `legacy_path_v1`;
- fresh production setup creates one random UUIDv4 ID
  `tg_project_<32-lowercase-hex>` as `uuid_v1`.

No Task, event, Contract, handoff, review, completion, maintenance, or Viewer
record ID is rewritten. Missing state has top-level null project ID until
write setup creates and persists the UUID once.

Binding is separate: positive generation, 64-lowercase-hex canonical-path
SHA-256, and bounded display name. `canonical_path_v1` resolves without
requiring existence, platform-normalizes absolute spelling, UTF-8 encodes, and
hashes. Display uses the root basename, replaces controls/line separators with
U+FFFD, falls back to `project`, and is at most 200 code points. Neither is
identity.

`project_meta` plus append-only binding history records ID, generation,
old/new hashes, display, UTC time, fixed reason (`legacy_migration`,
`fresh_setup`, `confirmed_relocation`), and optional SHA-256 of the accepted
token. Generation 1 has no prior hash/token digest. Raw paths and raw tokens
are never stored.

One shared resolver owns every production database/backup/Viewer/lock target.
It derives only current hash/display from repo, then reads stored identity and
binding. Every business writer revalidates ID/hash/generation under its short
lock. An authoritative fixed primary is validated without gating ordinary
business reads on backups or Viewer. Setup alone uses deep artifact validation.

With no fixed primary, the resolver inspects only direct physical children of
`state/projects`, at most 64, without traversing unknown content. Unknown
direct children, unsafe canonical paths, links/reparse points, multiple
candidates/identities, newer/corrupt/foreign state, or validation ambiguity
fail closed and never fall through to fresh initialization.

Source precedence is:

1. existing fixed primary, even when invalid (never replaced);
2. valid fixed managed-backup recovery with coherent identity/binding lineage;
3. exactly one valid legacy physical candidate;
4. fresh initialization only when no durable/candidate-shaped residue exists.

A legacy candidate is one physical directory named exactly by its stored legacy
project ID, with a regular primary or missing primary plus valid managed
backup, schema v1-v13 or the bounded v14 transition state, contiguous history,
one project row, quick/foreign-key success, rollback-journal compatibility,
and safe recognized artifacts. Unrelated entries inside that sole directory
are not traversed or copied and remain after migration.

Recognized legacy crash temporaries are at most one physical bounded file per
class:

```text
database directory  .taskgov-restore-[a-z0-9_]{8}.tmp
backups directory   .taskgov-backup-[a-z0-9_]{8}.tmp
Viewer directory    .task-viewer-[a-z0-9_]{8}.tmp
```

Each is no larger than source database size plus 16,777,216 bytes. Multiple
matches, wrong grammar, or oversized files are unrelated and preserved. Only
canonical backup/Viewer locks are recognized for retirement; they are not
copied as durable state.

Managed generations are normally at most 20 plus one in-flight identity:
at most 21 artifacts, rows, and union identities. Schema v11+ permits only the
existing one file-only or one missing-file-row crash relation (never both with
a present primary), and the missing-primary selected newest file plus zero
through 20 older missing rows accepted by recovery normalization. v10 uses its
pointer rules; v1-v9 use newest-artifact retention. Any second inconsistency,
overflow, divergent lineage, or reconciler rejection fails closed. Discovery
does not repair the legacy source; only a private copy is reconciled.

Same-binding legacy state migrates without a token. A primary-backed differing
binding is a relocation candidate. Moved backup-only legacy/fixed recovery is
`project_state_unreadable`; relocation is never inferred from backup material.
After a confirmed fixed rebind, old-binding backups remain retained but cannot
recover a missing primary until a new-binding backup succeeds.

Before setup, normal DB-backed leaves report `db_not_initialized` when no
source exists, `migration_required` for one same-binding legacy source,
`project_relocation_required` for a primary-backed moved legacy source, and
the existing unreadable/mismatch/newer/journal/busy result for invalid source
state. They perform no publication, recovery, cleanup, initialization, or
migration.

### Relocation Preview And Token

A path mismatch proves neither move, copy, nor fork. Normal commands return
`project_relocation_required` without write. Doctor returns a successful
continuation warning and setup eligibility. Write setup without token also
fails. Only `setup --read-only` returns successful
`status="relocation_preview"`, exact future plan, and opaque token. Combining
token with read-only is parser-level `invalid_option_combination`.

The Skill presents the plan, stops for explicit current approval, then may pass
that exact token. Preview is not approval; expired/stale token requires a new
preview and approval. Direct user invocation with a token is explicit intent.

Token format is:

```text
tgr1.<unpadded-base64url-canonical-json>.<64-lowercase-hex-checksum>
```

Checksum is SHA-256 of ASCII `tgr1.<payload>`. Canonical duplicate-free UTF-8
JSON has exactly sorted keys `binding_generation`, `expires_at`,
`identity_scheme`, `issued_at`, `new_path_hash`, `old_path_hash`,
`project_id`, `source_layout`, `source_schema_version`, and `v`. Version is 1;
source layout is `fixed_current_v1` or `legacy_projects_v1`; hashes are 64
lowercase hex; generation positive; schema supported. Serialization uses
sorted keys, `(",", ":")`, unpadded base64url. Expiry is exactly 900 seconds
after issue, acceptance is `issued_at <= now < expires_at`, total token at most
2,048 ASCII bytes, and checksum comparison constant-time.

The token binds stored ID/scheme/generation/hash, proposed hash, layout, source
schema, and time, not business content. Confirmation revalidates all fields,
package/install/ignore, source inventory/integrity, destination, and current
root under locks.

After common preflight, precedence is invalid structure/checksum, already-used
digest, expired, stale context, then not-required. Fixed codes/messages are:

| Code | Message |
|---|---|
| `project_relocation_required` | `project state is bound to a different project location; run setup --read-only` |
| `relocation_token_invalid` | `relocation confirmation is invalid` |
| `relocation_token_expired` | `relocation confirmation has expired; run setup --read-only again` |
| `relocation_token_stale` | `project relocation state changed; run setup --read-only again` |
| `relocation_token_used` | `relocation confirmation has already been used` |
| `relocation_not_required` | `project relocation is not required` |

Service errors exit 2; rejected token/hash/path/count/name/exception is never
echoed. Successful replay always returns used, even after expiry.

### Setup Relocation Output And Durable Publication

Setup always includes `data.relocation` with exactly `required`,
`source_layout`, `identity_scheme`, `binding_generation`,
`confirmation_token`, and `expires_at`. Token/expiry are non-null only on a
successful relocation preview. Fresh preview has null top-level ID and null
identity/generation/token/expiry.

Relocation failure has status null, required schema 16, empty warnings, one
error, null token/expiry, and no rejected value. A no-token mismatch preserves
the read-only future `planned_writes`; invalid/expired/stale/used/not-required
token rows have empty write arrays and mechanically observed bounded context.
Earlier common-preflight errors retain precedence.

The complete setup write vocabulary is `database_restore`,
`database_initialize`, `migration_backup`, `database_migrate`,
`maintenance_configure`, `legacy_state_publish`, `project_binding_update`,
`viewer_publish`, and `legacy_state_cleanup`. Durable order is:

1. one source prefix: empty, restore, legacy publish, restore plus legacy
   publish, or initialize;
2. migration backup then migration;
3. maintenance configuration;
4. binding update only for confirmed mismatch;
5. Viewer publication;
6. legacy cleanup only after fixed database, binding, maintenance, and Viewer.

Legacy work occurs in one private contained stage. Pre-publication staging is
not reported completed. Atomic no-clobber publication makes its staged prefix
durable together. Binding CAS appends generation and increments Viewer source
generation in one short transaction; overflow/missing Viewer state/mismatch
rolls back without consuming the token.

Private stage names are:

```text
state/.current-stage-<32-lowercase-hex>
state/.current-stage-<same-32-lowercase-hex>.owner
```

The exclusively and durably created physical owner precedes the directory. It
is canonical ASCII JSON at most 2,048 bytes with exactly `v=1`, matching
`stage_id`, validated `project_id`, and `inventory_fingerprint`, sorted keys,
ASCII escaping, compact separators, and no path. The stage allows only physical
`backups`/`viewer` directories and at most 32 regular files from the fixed
database/journal, up to 21 managed backups, canonical locks, Viewer HTML, and
one recognized temporary per class; each file is at most source database plus
16,777,216 bytes.

Write setup may remove only one fully owner-validated residue, enumerating
explicit allowed files then proven-empty directories. Read-only setup never
removes it. A stage without owner, invalid/mismatched owner, multiple pairs,
unsafe/unknown/oversized content fails `setup_incomplete` with no deletion.

Before publication, setup stores canonical cleanup inventory JSON:

```json
{"entries":[{"kind":"file","name":"relative/posix/name","sha256":"64hex","size":0}],"v":1}
```

It has 1-32 unique UTF-8-byte-sorted allowed entries, compact sorted-key ASCII
JSON at most 16,384 bytes, plus exact SHA-256. It lists only recognized
present source artifacts. Cleanup derives a fixed retirement directory from
the full SHA-256 of project ID, moves each inventoried file without replacement
after kind/size/hash validation, then deletes verified retirement entries.
Retry continues only the persisted subset. Both source/destination present,
changed content, unrecorded retirement content, or collision stops; unrelated
legacy files remain. After all recorded entries are absent, setup clears
pending inventory metadata atomically. Normal business commands remain usable
while valid fixed cleanup is pending.

Preview creates no directory, lock, sidecar, temporary, backup, Viewer, Git, or
target change. Actual publication holds one fail-fast package transition lock
before the backup lock, uses SQLite backup API without a source writer during
copy, and releases writers before Viewer/cleanup. Failures before fixed
publication remove only proven owned staging; failures after publication keep
fixed state authoritative and legacy state intact for resumable cleanup.

## Setup, Recovery, Backup, And Viewer Maintenance

### Setup Contract

`setup` is explicit, noninteractive, idempotent, and the sole initializer,
migrator, one-way maintenance opt-in, relocation flow, fixed/legacy recovery,
and canonical Viewer repair action. Preflight validates runtime, one physical
layout, project, state ownership, package integrity, Git ignore when applicable,
journal, schema, identity, binding, and artifacts.

When canonical state is absent, setup checks only the canonical managed backup
directory. It chooses the newest valid same-project generation by publication
time then ID; invalid/newer artifacts do not hide an older valid one. Existing
unreadable canonical state is never overwritten. If recognized material exists
but no valid same-project candidate remains, setup fails
`setup_restore_failed` rather than initializing empty.

Recovery copies the selected artifact via SQLite backup API into a fresh
sibling temporary database, normalizes schema-appropriate generation/pointer
metadata, validates and flushes it, then atomically no-clobber publishes only
while canonical state and rollback journal remain absent. A lexical orphan
canonical `-journal`, changed candidate, or lock contention fails closed.
Older recovered schema then receives a new migration backup and migration.
Fresh initialization uses the same artifact lock and rechecks absence of state,
journal, and newly appeared recovery candidates.

Schema v10 maintenance state has immutable non-null `enabled_at` once
configured, backup interval/generation policy, latest managed success/outcome,
and internal `applied_backup_generations` retention. Fresh/migrated setup
defaults are 30 minutes and 3 generations. On configured state, omitted
options preserve locked stored
values; explicit valid options change only supplied fields; equal values are a
no-op. Invalid range is `invalid_backup_policy` before write. Configuration
does not change applied retention or itself trigger backup/Viewer; lower
retention applies only after the next successful backup publication.

Setup output data is exactly `status`, `planned_writes`, `completed_writes`,
`schema_from`, `schema_to`, `maintenance_enabled`,
`backup_interval_minutes`, `backup_generations`, `viewer_status`, and
`relocation`. `schema_to` is always 16. `schema_from` is safely observed source
schema, selected recovery schema, or null. Policy values are effective
requested/stored values, not persistence claims. `maintenance_enabled` and
Viewer status describe durable post-command state.

Viewer status is `not_present`, `current`, `published`, or `repair_required`.
Successful preview is `setup_preview`; completed writes use `setup_complete`;
current/equal state is `already_setup`; relocation preview is
`relocation_preview`. Preview reports the full ordered plan and empty completed
array. Failures report the durable ordered completed prefix. Common preflight
and invalid policy failures have status null, empty arrays, null observed
scalars except required schema; later failures retain safe/effective values.

Core setup outcomes are:

| Stage/result | Stable error |
|---|---|
| invalid policy | `invalid_backup_policy` |
| recognized recovery without valid candidate/publication failure | `setup_restore_failed` |
| migration backup failure | `setup_backup_failed` |
| initialization failure | `setup_initialization_failed` |
| migration failure after backup | `setup_migration_failed` |
| configuration, Viewer, cleanup, or other later partial failure | `setup_incomplete` |

Messages are respectively `backup policy is outside the supported range`,
`managed backup could not be restored`,
`setup backup could not be completed`,
`project state could not be initialized`,
`project state could not be migrated`, and
`setup completed only partially; rerun setup`. Setup success has empty warnings
and errors; failure has empty warnings and exactly one error. Rerun recomputes
from durable state; a prior migration backup never substitutes for the new
attempt's required backup.

The shared backup primitive uses SQLite backup API, validates project identity,
schema/history, regular physical paths, `quick_check`, and foreign keys,
closes/flushed temporary state, and atomically publishes. Setup holds its
zero-wait artifact lock through backup publication/reconciliation and the
corresponding migration commit, but never a SQLite writer while copying.
Preview creates neither lock nor artifact.

### Same-Process Maintenance

Maintenance is permanently enabled by successful setup; there is no disable
surface. Every successful backup-eligible business mutation commits and closes
SQLite before bounded same-process maintenance. Viewer refresh runs first for
Viewer-relevant mutations; due backup runs second. Read-only, failed, replayed,
no-op, handoff-only where inapplicable, setup configuration, and
maintenance-internal operations trigger nothing.

Taskgov starts no detached process, child process, thread, timer, watcher,
service, queue, daemon, scheduler, or network operation. Each artifact uses a
canonical regular one-byte OS advisory lock with zero wait; process termination
releases ownership, so leftover regular lock files are harmless and never
deleted by age. Unsafe lock path, contention, or failure preserves primary
result and last-good artifact, leaves due state, and emits at most the fixed
continuation warning:

| Code | Message |
|---|---|
| `viewer_refresh_deferred` | `Viewer refresh was deferred; task result is unchanged` |
| `viewer_refresh_failed` | `Viewer refresh did not complete; task result is unchanged` |
| `backup_deferred` | `managed backup was deferred; task result is unchanged` |
| `backup_failed` | `managed backup did not complete; task result is unchanged` |

Backup is due on the first eligible mutation with no managed success, then
after the configured interval. At most one attempt occurs per eligible
mutation. Failure remains due. Successful atomic publication records its
immutable generation ID, publication time, and 1-20
`publication_retention`, then prunes recognized older generations to the
applied value. Unknown/linked/foreign artifacts are never imported or deleted.

Every v11+ backup attempt first reconciles bounded crash residue under the same
lock: import one valid file missing a row; remove a row whose file is invalid/
missing without deleting an untrusted path; update latest/applied retention;
and finish file-before-row pruning oldest first. It will not publish while
inconsistency remains. Publish then insert-row/update-pointer in one short
transaction, followed by pruning. Recovery handles process termination after
file publish, row commit, or between prune file/row. Residue cannot exceed the
valid retained set plus one in-flight generation.

For v10, a successfully published migration copy updates latest identity/time/
outcome/applied retention before migration and is reconciled on retry. For
v1-v9, each successful pre-migration copy precedes pruning under its immutable
stage retention; a successful migration creates v10 state pointing to it.
Migration v11 discovers all canonical valid retained same-project artifacts,
seeds one row each, includes the current copy, and prunes to that copy's
applied retention.

Viewer maintenance compares source/render generation, renders once and at most
one follow-up per mutation, prevents older-over-newer publication, and leaves
remaining churn due. Setup rerun is the only explicit force/repair.

The performance fixture is 12 Tasks/191 events and 500 Tasks/5,000 events,
payloads 80/512/256 UTF-8 bytes, with eight note writes at injected minutes
0, 1, 5, 29, 30, 31, 59, and 60. Due backups are at 0, 30, 60; Viewer is
eligible on all eight. Enabled run overhead versus disabled is at most 10
seconds and each foreground command at most 5 seconds on Windows CI. Attempt,
render, call, byte, and zero-wait limits are hard; timing cannot waive them.

## Static Task Viewer

The Viewer is a generated self-contained projection, never an authority.
Setup owns initial publication/repair and post-commit maintenance owns bounded
refresh. There is no Viewer command, custom output, browser launch, or model
decision. Canonical path protection rejects linked/non-regular/escaping/
database-alias targets. Same-directory temporary publication is flushed and
atomically replaces; failure preserves last good.

### Snapshot v4

Snapshot v4 accepts source schemas v5-v16. One query-only transaction validates
schema/project/binding, reads generation, and assembles rows; rendering and
publication occur after close.

It contains version/UTC `generated_at`, project ID/display, source schema,
seven status counts, explicit Task allow-list, newest at most 10 sanitized
events/receipts/findings, and the exact completion-history projection. Sources
v5-v14 synthesize zero cycles with `legacy_history_incomplete=true`; v15-v16
read stored history in query batches of at most 500 Task IDs. The Viewer
selects all project Tasks; 500 Tasks is the accepted performance fixture, not a
selection cap. The HTML artifact is at most 64 MiB.

It excludes paths, maintenance, checkpoints, Task Contract prose, handoff/tool
state, environment, raw evidence, logs, prompts, and secrets. Deterministic
UTF-8 JSON is base64-embedded into a bundled template; stored values use
text-only DOM APIs.

Schema v13+ increments source generation on Viewer-relevant Task, checkpoint,
or review events, not handoff-only/read/failure/replay/no-op/config/
maintenance. The Viewer stage skips before opt-in, takes zero-wait lock,
renders only when due, records rendered generation shortly, rechecks once, and
never publishes an older capture over newer. Doctor reports stored facts only;
browser reload never observes SQLite directly.
The owning state is `viewer_maintenance_state`; its source generation is
advanced in the same business transaction as the corresponding event.

The offline `file://` UI shows project/time/status totals, search and
status/kind/lane/priority/tag filters, deterministic Task order, terminal
history, details, review/completion/history, and recent events. It is responsive
and keyboard/label/focus/contrast accessible and has no write controls,
network/API/analytics/telemetry/server/direct SQLite/watcher/launch/storage.

### Optional Visibility-Aware Reload

Only physical `<skill>/config/viewer.json` controls reload. Taskgov never
creates/edits/migrates it. Absence means decimal interval 0 and no browser
timer. A present file is physical regular non-link/non-reparse strict UTF-8
JSON at most 16,384 bytes with exactly:

```json
{"schema_version":1,"profile":"visibility-refresh-v1","refresh_interval_seconds":30}
```

Interval is an integer (not Boolean/float) 5-3,600. Duplicate/unknown/missing
keys, malformed/oversized/replaced/unsafe content is invalid with no raw
diagnostic. One publication attempt reads it once for at most two renders.
Template has exactly one base64 snapshot and one decimal interval placeholder.
Config change applies at next relevant publication; setup plans Viewer repair
when rendered template/interval differs.

Invalid present config makes preview a successful repair plan; actual setup
returns `setup_incomplete` and routine mutation emits
`viewer_refresh_failed`, preserving last-good Viewer. Backup still attempts
second. Doctor ignores this optional file.

After decode and initial render succeed, scheduling occurs only under `file:`
with positive interval. One monotonic load epoch and at most one timeout are
owned. Hidden pages own no timer. On visible change/timeout, reload is requested
at most once per loaded page only after elapsed interval; otherwise only the
remainder is scheduled. Browser throttling may delay, never advance. Decode/
render failure schedules nothing. It reloads only latest published HTML and
adds no command, schema/snapshot field, process, service, storage, network, or
LLM choice.

### One-Shot Automatic-Reload UI State

Immediately before that one automatic reload, an eligible `file:` page may call
exactly one `history.replaceState(envelope, "")` with no URL. Failure never
prevents reload. Non-null non-owned state is untouched; any non-array object
with owner `taskgov-viewer-auto-reload` is owned even if otherwise invalid.

The schema-1 object has exactly owner, schema_version, captured_at_ms, status,
kind, lane, priority, tag, terminal, selected_task_id, scroll_x, scroll_y, and
focus_id. Canonical serialization and readback are at most 4,096 UTF-8 bytes.
Time is nonnegative safe integer; status/kind/priority use current enums or
empty; lane/tag are at most 1,024 UTF-8 bytes and must exist in new options;
terminal is Boolean; selected ID is nonempty and at most 128 code points;
scroll is finite 0-2,147,483,647. Focus is empty or one of
`search-filter`, `status-filter`, `kind-filter`, `lane-filter`,
`priority-filter`, `tag-filter`, `terminal-filter`, `reset-filters`.

Search text, business/snapshot content, option arrays, URL/query/fragment,
path, arbitrary selector, caret/text selection, dynamic-row focus, or nested
scroll is prohibited. Cookies, Web Storage, IndexedDB, Cache API, service
workers, and network are prohibited.

At eligible load, read state at most once before snapshot decode. Owned state
is immediately cleared with `replaceState(null, "")` even when invalid,
stale, non-reload, or later decode fails. Clear failure disables restore.
Non-file state is untouched. `history.scrollRestoration` must exist, accept
`manual`, and read back manual before save/restore; otherwise M15.6 is disabled
but reload continues. State-read failure does not skip the manual-mode attempt.

Restore only on navigation type reload, after successful clear, with exact
keys/types/bounds, age 0-300,000 ms, current options, visible selected Task,
and existing fixed focus. Defaults are explicitly applied first. Valid restore
applies filters, one render/selection, focus without caret, then document
scroll. Any failure consumes state and resets defaults plus `(0,0)`. If scroll
fails after focus, best-effort blur only that focused fixed control, then reset.
No state/error detail reaches UI, console, snapshot, or taskgov output.

History state is browser-managed and may survive session restore; it is not
memory-only. One-shot ownership, five-minute age, size cap, and clear-before-
restore are the privacy boundary. `pushState`, URL arguments, URL/history-length
changes, cross-tab sync, and manual-reload capture are prohibited. Interrupted
navigation may leave one bounded envelope for the next qualifying reload.

## Privacy, Safety, And Stable Errors

Taskgov stores only bounded sanitized summaries, command labels, exit/duration/
status/time metadata, hashes, stable IDs, and explicit structured evidence.
It never stores or emits API keys, credential/session tokens, cookies,
authorization headers, raw provider bodies, private prompts, chat transcripts,
large/raw diffs, raw stdout/stderr, full logs, stack traces, environment dumps,
review reasoning/bodies, OS/SQLite exception detail, raw paths in identity
metadata, expected/actual hash pairs, or rejected values.

Free-form limits not narrowed above are: title 200 characters; description
4,000; verification 500; tags/reviewer/target/external revision/authority ref
500; note 2,000; event/receipt/finding/resolution/pause/reopen/Contract
change reason 1,000. Secret/header/private-key/password/token/api-key,
traceback, raw stream dump, and large diff patterns are rejected with
`privacy_rejected` before storage. Rejected patterns include bearer tokens,
authorization headers, private-key blocks, `password=`, `token=`, and
`api_key=`. Public read projections revalidate stored content and cross-field
matrices. The existing `lane` and `blocked_reason` inputs have no numeric
character limit and still use the common privacy guard and state validation.
`lane` is canonicalized by trimming outer whitespace; `blocked_reason` is
retained as supplied after string/privacy checks.

Normal and new caller input has no release- or project-specific privacy
exception. Both the lowercase equality form
`dispatch_authorization=<value>` and a JSON
`"dispatch_authorization":<value>` key are rejected, including positive
numeric values. Future external-operation records use the neutral
`operation_sequence=<positive canonical integer>` vocabulary. That value is
only correlation or idempotency evidence: it neither contains nor grants
authority, and the exact current approval for any external operation remains
separate.

One read-only compatibility path preserves exact already-stored M19.7 text.
Only stored Contract constraints and stored checkpoint summary reads may
replace the bounded lowercase `dispatch_authorization` equality or numeric
JSON counter with a non-secret sentinel while running every other privacy
detector, then return the original stored text unchanged. The path performs no
rewrite, schema change, Task write, dispatch, or other external operation.
Stored Contract fields other than constraints, checkpoint fields other than
summary, completion history, and all caller input use the normal strict guard.
Prefixed, nested, differently cased, noncanonical, credential-valued, or
compound credential/token forms remain rejected in every path.

Stable domain codes include:

```text
invalid_argument invalid_option invalid_option_combination invalid_command
invalid_status invalid_kind invalid_priority invalid_review_tier
blocked_reason_required pause_reason_required initial_done_forbidden
initial_paused_forbidden invalid_status_transition
sequential_predecessor_incomplete done_task_requires_reopen
review_tier_downgrade_forbidden privacy_rejected not_found
db_not_initialized migration_required schema_too_new project_mismatch
project_relocation_required unsupported_journal_mode database_busy
review_target_required review_target_missing review_target_mismatch
review_changes_requested review_receipts_insufficient
review_finding_unresolved review_receipt_mismatch
review_receipt_already_recorded invalid_review_evidence
verification_required review_required commit_required
completion_commit_conflict completion_evidence_conflict
git_commit_not_found_or_ambiguous external_revision_approval_required
completion_check_stale completion_history_inconsistent
handoff_not_persisted handoff_not_withdrawable handoff_occurrence_invalid
contract_activation_forbidden contract_authority_required
contract_write_conflict review_packet_path_unsafe review_packet_too_large
review_packet_stale invalid_backup_policy setup_restore_failed
setup_backup_failed setup_initialization_failed setup_migration_failed
setup_incomplete unsupported_python unsupported_install_layout
project_scope_required invalid_project_root state_path_invalid
state_ignore_required package_core_modified package_status_unknown
relocation_token_invalid relocation_token_expired relocation_token_stale
relocation_token_used relocation_not_required internal_error
```

Warnings are bounded, sanitized, and non-authoritative. Required warning codes
include `paused_tasks_present`, `handoff_delivery_pending`,
`effort_advisory_profile_invalid`,
`effort_advisory_threshold_exceeded`, package/setup advisory codes, and the
four maintenance codes. Warnings never carry Task prose, secrets, raw output,
diffs, paths, or exception text.

## Published v0.10.0 Release Record

Version 0.10.0 is published from the exact accepted commit
`a9b80ce177a6dead10d51a070b76ff01f7af0294`. Remote `main` and the
unpeeled lightweight tag `v0.10.0` resolve to that commit. GitHub Release
`362617903` is the canonical publication record and has prerelease
visibility. A mutable branch name, remote-tracking reference, generated state,
or an older completion cycle is not release authority.

The published identity is:

| Item | Published value |
|---|---|
| Package | `0.10.0` |
| SQLite | v16 |
| Viewer | snapshot v4, source schemas v5-v16 |
| Public command leaves | 20 |
| Runtime/CI | Windows Python 3.12+, CI 3.12 and 3.14 |
| Commit / `main` / tag | `a9b80ce177a6dead10d51a070b76ff01f7af0294` |
| Tag | lightweight `v0.10.0` |
| Remote/repository | `origin`, `VAiring/task-governance-tool` |
| GitHub Release | `362617903`, prerelease |
| Archive | `task-governance-tool-0.10.0.zip` |
| Archive SHA-256 | `99fc2345fd036091349c47f7379eee25b8b3b4c8873c0f74aaceac323bb82a03` |
| Checksum file | `task-governance-tool-0.10.0.zip.sha256` |
| Checksum-file SHA-256 | `9cdc99bd26cc4887bd88ef2ec638659224f0a0d8f567edb12a3800d59a8b6764` |
| Release notes | `docs/releases/v0.10.0.md` |
| Release-notes SHA-256 | `aaa118a3fbbb261ec6a24f7a80f50f161e606a86857f99e17f957f34ba044a03` |
| Candidate CI | run `30561916953`, attempt 1, Python 3.12/3.14 success |
| Remote-main CI | run `30565181070`, attempt 1, Python 3.12/3.14 success |

The archive contains exactly the committed installable package subtree under
one `task-governance-tool/` root. The package release manifest owns its
packaged-core inventory and digests. Generated state, SQLite files and
sidecars, backups, locks, Viewer output, target configuration, root
`references/`, tests, fixtures, caches, logs, secrets, and scratch files are
not artifact content. The canonical recipe and checksum format remain defined
by [the release and install record](release-install.md).

Original copyrightable material owned by Omoronine in the reviewed tracked and
shipped scope is licensed under Apache-2.0. Root and package `LICENSE` files
contain byte-identical official unmodified text, the package copy is
manifest-covered, and no concrete `NOTICE` duty was identified. Root
`references/`, `research.md`, untracked or ignored material, generated
state, and separately licensed or unowned third-party material remain outside
that grant.

The accepted tag, Release metadata, notes, archive, and checksum are immutable
by project policy. A defect is handled by a reviewed forward-fix candidate and
new version; routine recovery never force-updates `main`, rewrites history,
moves or replaces the tag, replaces published assets, or deletes the Release
to hide a mismatch. Any future Git or network mutation still requires exact
current user authority for that operation. Product inspection and normal Task
commands grant no release authority.

The v0.10.0 acceptance rehearsed an isolated transition from the exact legacy
v0.1.0/schema-v2 package and state to the unchanged v0.10.0/schema-v16
package. Supported rollback restores a matched legacy package, database, and
managed artifacts as one compatibility point before legacy code runs. Running
old code against schema v16, in-place downgrade, mixed generations, and Git
checkout alone are unsupported.

Completed release-gate Contracts, checkpoint schemas, approval objects,
candidate staging mechanics, and ordered mutation evidence are one-time
execution lineage, not active normal-loop product behavior. Their exact
publication-commit forms are indexed by
[the historical documentation index](history/README.md). Final completion
evidence remains in the project-local Task database and the active roadmap's
concise completion index. Current product, privacy, review, migration, and
artifact requirements above do not depend on historical text.

## Deferred Boundaries

The current product deliberately excludes pagination/search in CLI history,
parent/child Tasks, acceptance checklists, active verification-run receipts,
generic
result/receipt-file import, action aliases, general/manual backup or restore,
custom export, browser launch/server, durable/general browser persistence
beyond the one-shot envelope, external Issue lifecycle/sync until its intake
contract, cross-project profiles, daily network update checks, reviewer
identity/signatures/attestation, and a generic workflow engine.

Deferred features, the temporary TG-M20S observation, and the inactive TG-M21
implementation never change current
acceptance, add a normal-loop command, or authorize target/external mutation
until their separately approved implementation and synchronization gates
complete.
