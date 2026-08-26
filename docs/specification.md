# task-governance-tool Current Product Specification

Status: The immutable published product remains v0.10.0/schema v16/Viewer v4 sources v5-v16/20 leaves; its identity is fixed in `docs/release-install.md`.
The current unpublished candidate is v0.12.0 with SQLite schema v20, Viewer
snapshot v4 accepting source schemas v5-v20, and 21 public command leaves. It retains the TG-M21 Receipt, the accepted TG-M22.2/TG-M21.5 capture/admission boundaries, the accepted TG-M22.3 native Bundles and fixed Evidence JSON v1, accepted TG-M22.4 integrated acceptance with strict recovery and stored-Task/Contract-pointer validation, accepted TG-M23.1 derived-evidence design, accepted bounded offline/mock TG-M23.2 implementation, accepted TG-M23.3 offline/mock integrated Analyzer acceptance, and accepted documentation-only TG-M24.1 Runner design.
TG-M20S.3 remains inactive and no TG-M23 unit is current. TG-M24.1 and
TG-M24.1A are accepted predecessors; the fixed-Candidate-C and adversarial
LPAC route formerly owned by TG-M24.1B is superseded. Current TG-M24 authority
is the ordered repair and implementation sequence for an explicitly adopted
trusted-local Runner. Accepted TG-M24.R3A supplied only the private schema-v20
migration/storage foundation. Accepted TG-M24.R3B publicly activates that
existing schema without activating Runner execution or a completion gate.
Accepted TG-M24.R4B repaired only pre-Runner core behavior and dependency
violations. Accepted TG-M24.R5 retired only the already identified fixed
OS-temp diagnostic residue. Accepted TG-M24.2A supplied explicit trusted-local
plan authority, exact target binding, and bounded private materialization.
Accepted TG-M24.2B supplied the bounded local process adapter and deterministic
cleanup without activating a public Runner or completion gate. Accepted
TG-M24.2C supplied parent-service orchestration and audit-only schema-v20
observation and Evidence capture; it cannot satisfy verification or completion.
TG-M24.2D is the sole current unit and accepts only the already-implemented
shadow slice from one fresh exact target; it activates no additional product
behavior or Runner completion gate.
Completed execution narrative is history, and the Task
database owns live state and evidence.

Accepted TG-M24.R3A prepared only a private, non-public schema-v20 storage
foundation and left the public schema constant, setup target, Viewer source
range, and schema-v19 native version-1 Bundle writer unchanged. Accepted
TG-M24.R3B activates public schema v20, the Bundle-v2 null-Runner writer, and
Evidence/Viewer/managed backup/recovery compatibility. It creates no Runner
record, Reference/link/member/projection, process launch, or gate authority,
and neither unit alone authorizes a main or canonical-state cutover.

This document specifies supported product behavior. The concise
[authority index](authority.md) routes implementation structure to
`docs/design.md`, durable agent behavior to root `AGENTS.md`, current decisions,
open issues, gateways, and non-delegated static contracts to root `plan.md`, and
exact current-or-inactive execution-unit detail to the applicable routed formal
document. The project-local Task database, inspected
through the public CLI, owns live Task state and evidence. Indexed files under
[`docs/history/`](history/README.md) are non-authoritative lineage only.
Required current behavior never depends on historical text.

## Documentation Authority And History

`docs/authority.md` is the repository-visible registry and selective-read
router. Root `AGENTS.md` plus that index are the mandatory start set; the live
Task Contract selects the applicable current and conditional owners. An
indexed execution contract may mix current and conditional units only as
explicitly routed; a conditional unit remains inactive until its dependency
and activation gates are satisfied. History is indexed by `docs/history/README.md`.
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
`--repo`, a physical package and repository, the four fixed source-shape marker
files, a valid
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
<physical-skill>/state/current/evidence/index.json
<physical-skill>/state/current/evidence/bundles/<completion-evidence-bundle-id>.json
<physical-skill>/state/current/evidence/taskgov-evidence.lock
<physical-skill>/state/current/viewer/task-viewer.html
<physical-skill>/state/current/verification-runner/
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

The public CLI has exactly 21 command leaves:

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
21. `taskgov verification receipt add`

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
| `task.show` | exactly `task`, `events`, `suggested_next_action`, `review_evidence`, `handoff_summary`, `contract`, `latest_checkpoint`, `effort_advisory_enabled`, `completion_history`, `verification_evidence` |
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
| `verification.receipt.add` | `receipt` |

`task.show` failure keeps both `completion_history=null` and
`verification_evidence=null` in its bounded empty data.
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

Every Task-loading operation applies the current TG-M21.4C stored-row contract
and TG-M21.4D Contract-relationship contract
before an allow-list projection, compact omission, derived-state use, or
write-basis use. Bounded list/current/next reads validate the complete rows in
their selected batch and do not add an unrelated full-table scan. `task show`
and Task-backed lifecycle operations validate the selected complete row before
reading or mutating dependent state. The shared failure result is defined in
the current TG-M21.4C and TG-M21.4D sections below.

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
- one `verification receipt add` call after the caller runs the complete
  governed verification against that exact target;
- one `review prepare` call instead of separate task, Contract, target, and Git
  context reads;
- one receipt write per actual receipt; and
- one thin complete call.

A default-off no-finding Tier 2 path therefore has at most ten governance
subprocess calls; a profile-enabled path has at most eleven. Both exclude real
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
Doctor validates the complete project Task batch before returning Task-derived
counts. A stored Task fault makes `project_state.code="unreadable"`, every
other project-backed component `{"code":"unavailable"}`, and
`setup_eligible=false`, with the fixed `project_state_unreadable` error.

- `package` is the bounded manifest-integrity projection.
- `project_state` contains only `code`, `schema_version`, and
  `required_schema_version`.
- `task_summary` contains `code` and exact counts for `active`, `blocked`,
  `done`, `next_actionable`, `paused`, and `review_pending`.
- `handoff_delivery` contains `code`, `handoff_pending`, `adapter_enabled`,
  and `delivery_due`.
- `maintenance` contains `code`, `opted_in`, `backup`, `evidence`, and `viewer`.

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
The Evidence object has exactly `code`, `due`, `source_generation`, `published_generation`, `last_success_at`, and `last_outcome`; doctor reports these stored facts only and never repairs or reads generated JSON.

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
For a supported schema-v18-through-v20 source, every complete loaded Task row is validated
for exact SQLite/Python storage class, bounded text/privacy, closed enums, and
all Task cross-field matrices before any field can be omitted or exposed.
Stored values are never coerced, trimmed, repaired, or rewritten by a read.

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
are explicit in the current user instruction, approved execution plan or
execution-unit set, or explicit registration input. Supplying any of
`--contract-scope`, `--contract-acceptance`, `--contract-constraints`,
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

### Current Schema-v20 Review Provenance And Bundle Boundary

New `independent` and `self_review_fallback` Receipts use the existing `review receipt add` leaf with the closed human, LLM, deterministic-tool, hybrid, and explicit-unknown matrix; conditional model and Skill identifiers; and fixed ordered profile, lens, context, and method codes. No free-form capability or caller assurance exists.
The normalized immutable v1 record and structural digest bind the exact Receipt and target. Every public Review Receipt has one `review_provenance` union: native independent/fallback rows expose v1 with `bound_attestation/trusted_caller/1`; pre-v18 rows expose version-zero absence with `legacy_unknown/legacy_migration/1`; and Tier-0 `not_required` has null provenance and no provenance row.
These states are distinct. The original Receipt remains caller-attested, and neither it nor provenance proves identity, execution, competence, independence, quality, diversity, or truth. Migration adds only zero/null discriminators and never parses reviewer keys/summaries, backfills provenance, creates an Evidence Reference for an old row, or strengthens legacy evidence.
Native v1/null rows are included in their schema-v18 Evidence Reference and in
their schema-v19 Bundle-v1 or schema-v20 Bundle-v2; migrated v0 rows have none.
Viewer snapshot v4 validates and discards provenance with no field, panel,
filter, or UI. Evidence JSON is an automatic generated projection, not a
command.

### Git Snapshot And Target Binding

`review target set --kind git_snapshot` accepts no caller revision. It reads a
canonical HEAD and stage-0 index, rejecting unborn HEAD, unmerged index,
zero-object intent-to-add, and sparse-directory entries. A canonical manifest
binds base commit, mode, object ID, and raw path bytes; its SHA-256 plus base
are stored. No Git state, hook, config, worktree, object, index, or ref changes.
Unstaged/untracked content is outside the snapshot.

Every newly set target is capture version 1 and atomically binds the current
authority snapshot, nullable acceptance and verification criterion IDs, and
one immutable artifact manifest. Git targets contain the complete bounded
HEAD-tree/index or commit/first-parent leaf difference; root commits compare
with the empty tree. Exact mode/object pairs become a rename only when unique
on both sides. Other moves remain delete/add. Safe relative POSIX paths, full
object IDs, fixed bytewise ordering, 10,000 entries, and 16 MiB canonical JSON
are hard limits. Diff-fingerprint and external targets instead receive one
zero-entry `opaque_target` manifest with `artifact_content_not_observed`. No
blob, patch, raw diff, untracked content, or caller file list is retained.
Pre-v18 targets keep their tuple as capture version 0 with null snapshot,
criterion, and manifest bindings. They are audit-only: Verification Receipt,
Review Receipt, Review Finding, and completion source creation fails
`evidence_basis_stale` / `current evidence basis must be captured again`.
Review preparation and resolution of an existing Finding remain allowed.
Setting a fresh target is the only repair; no migration or command upgrades an
old target in place.

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
eligibility, a matching current target, a qualifying current Verification
Receipt when verification is nonempty, sufficient current-generation review,
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
`review_target_required`, `evidence_basis_stale`, `review_target_mismatch`,
`verification_receipt_required`, `verification_receipt_blocking`,
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
a schema-v15 binary from writing after native capture activates. Schema v17
adds the Verification Receipt basis discriminator and link described below.

Each cycle ID is `tg_completion_cycle_<16-lowercase-hex>` with a positive
signed-64-bit per-Task ordinal beginning at 1 and increasing exactly by one.
It stores ownership; origin (`native_done` or `legacy_current_done`);
completeness (`complete` or `partial`); completion/record times; Contract
revision; tier; specified/unspecified verification expectation and nullable
attestation; the exact six-field completion evidence; exact four-field review
target; and versioned accepted gate-basis counts plus up to two qualifying
review-receipt IDs. Schema v17 additionally stores internal
`verification_basis_version`, `verification_expectation_digest`, and
`verification_receipt_id` fields without changing the public cycle shape.

The migration is named `completion_cycle_history`. Internal cycle fields
include `recorded_at`, `gate_basis_version`, and `review_basis_kind`; these are
project-owned validated values and do not broaden the public allow-list.

Post-v17 native rows are complete, have non-null completion time, true verification
attestation, gate basis v1, zero changes-requested/open-high/open-medium/
fresh-review-required, verification basis v1, and a valid tier basis:

- Tier 1/2 selects enough distinct independent PASS receipts ordered by
  `reviewer_key, review_receipt_id`;
- only when insufficient, Tier 1/2 selects the first valid fallback by receipt
  ID, including Tier 2 approval;
- Tier 0 selects the first valid not-required receipt.

The stored v1 basis kind is exactly `independent_passes`,
`self_review_fallback`, or `not_required`; a v0 legacy row uses `unknown`.

Migrated legacy rows are partial, have null attestation, unknown review basis,
gate basis v0 with null counts and no review-receipt IDs, verification basis
v0 with null digest/link, and may preserve `none` or `legacy_unverified`
without strengthening it. Cycles are immutable and never satisfy a current
gate.

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

Migration 17 assigns every pre-existing cycle, including a complete
`native_done` cycle, verification basis v0 with null expectation digest and
null Verification Receipt link while preserving every existing field value
and public meaning. It synthesizes no Receipt. After migration, every new native
cycle uses verification basis v1; only the exact partial
`legacy_current_done` reopen bridge may still insert v0/null/null.

`legacy_history_incomplete` is true when coverage is not complete, any cycle is
partial, or any `task_reopened` event has null cycle link. A fresh
schema-v16-or-later Task with no cycle is complete-history and false.

Both done paths insert one complete verification-basis-v1 cycle, update Task,
create the existing linked completion event, record effort/Viewer state, and
commit atomically under one short writer after all external observation. A
nonempty verification expectation links the exact qualifying pass/full
Verification Receipt; an expectation whose trimmed text is empty stores the
digest of its exact existing bytes and a null link. The exact empty string uses
the fixed empty-text digest, while legacy whitespace-only bytes are not
rewritten. Reopen requires the current done projection to equal the latest
un-reopened cycle; it links the new reopen event and resets current state
atomically. The sole compatibility bridge may create an ordinal-1 partial
verification-basis-v0/null/null cycle for an unknown-coverage done Task with
no cycle. Other mismatch is `completion_history_inconsistent` and no write.

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

## Accepted But Inactive TG-M20S.3 Decomposition Contract

TG-M20S.3, Task `tg_task_286129dbca4d25ab`, freezes design authority for two
and only two explicit user-authority events: an instruction to register or
taskize already-authorized work, and an explicit scope addition to an
`in_progress` or `review_pending` Task. This is an acceptance boundary for a
separately approved activation unit, not current Skill or product behavior.
The supporting M20S observation covered one repository, scenario, pair, and
model/tool cohort, so it does not justify automatic self-splitting or any
broader discovery trigger.

### Independence And Boundedness

A candidate boundary has four independently assessed fields:

- `acceptance_independent=yes` means its outcome and acceptance can be stated
  and accepted without silently accepting another candidate's outcome.
- `verification_independent=yes` means bounded verification can attribute a
  pass or failure to that candidate. Reusing a command or fixture is allowed;
  sharing one indivisible acceptance result is not.
- `commit_independent=yes` means it can form a coherent reviewed revision
  without landing a partial or unaccepted part of another candidate. Touching
  the same file is not by itself a failure; an inseparable atomic change is.
- `completion_independent=yes` means that, after any predecessor expressible
  by existing lane ordering completes, the candidate can satisfy its own
  Contract, review, and evidence gates without revising another active Task or
  borrowing that Task's current evidence.

Each field is `yes`, `no`, or `unknown`. `yes` requires explicit authority and
concrete project evidence, `no` records a concrete coupling, and `unknown`
records missing or ambiguous information. Aggregate independence is `yes` only
for the single all-`yes` combination, `no` whenever any field is `no`, and
`unknown` otherwise; a concrete `no` dominates simultaneous unknowns.
Decomposition is eligible only when
every field for every candidate is `yes`, the candidates are a disjoint and
complete partition of the user's authorized outcome, their order is
representable with the existing sequential/optional lane model, and their
permissions do not exceed the original authority. File count, line count,
estimated effort, duration, risk, or a general impression that work is large
never supplies a `yes` value.

One explicit event permits only one non-recursive assessment pass. Candidate
count is bounded by, and mapped one-to-one to, the finite independently
accepted outcomes actually present in that authority; one outcome cannot be
split by file, step, test, or implementation detail unless the user already
accepted those as separate outcomes. The agent cannot invent a parent Task,
descendant Tasks, acceptance clauses, or additional work to improve the
partition.

### Explicit Registration Decision

An explicit request to register or taskize authorized work permits the
following bounded result without asking once per Task:

| Condition | Result | Write boundary |
|---|---|---|
| Every candidate satisfies all four fields, the partition is complete and non-overlapping, order is representable, and permissions are unchanged | `register-bounded-set` | Use the existing `task add` path once per candidate in the single approved pass. |
| Any field is `no` or `unknown`, or the proposed order or partition is not representable, but one truthful Contract can contain the whole authorized outcome and the user did not mandate incompatible separate boundaries | `register-single` | Use the existing `task add` path once for the complete outcome. |
| Authority is insufficient to state even one truthful Contract, or the write would expand scope or permissions | `no-write` | Do not invent a Task or acceptance. Apply the existing clarification, handoff, pause, or blocking rule only where its normal preconditions already exist. |

The taskization instruction authorizes these bounded Task-registration writes;
it does not authorize implementation, target-project mutation, external work,
or changed permissions. A failed multi-Task assessment therefore collapses to
one honest Task when possible, not to omitted scope or repeated clarification
merely to optimize decomposition.

If the user mandated exact separate Task boundaries and any boundary is `no` or
`unknown`, the result is `no-write`, not a silent merge; report the conflict and
request changed authority only when it is needed to proceed. If a bounded
multi-add fails after a strict subset was registered, stop the pass, inspect
the exact registrations through existing reads, and report or durably preserve
the authorized remainder. Retry only an unambiguously missing candidate after
the ordinary failure is resolved; never duplicate, repartition, or widen it.

### Explicit Mid-Task Scope-Addition Decision

For one explicit scope addition to an `in_progress` or `review_pending` Task,
first select one of the first four scope dispositions below, then evaluate the
continuation state. `pause` and `block` are status outcomes layered after scope
is preserved; they never substitute for placing the authorized addition. The
six outcomes therefore cover scope and continuation without treating them as
one lossy axis.

| Outcome | Exact condition | Required effect |
|---|---|---|
| `keep-current` | The stated addition is already wholly covered by the current Contract and no explicit repartition was authorized. | Make no Contract or Task write. Do not create overlapping duplicate acceptance. An unchanged Contract preserves current review evidence only while its exact target still covers the material; changed target material requires the normal fresh target and review. |
| `revise-current` | The addition is new and the user directs it to remain current, or decomposition is ineligible but the complete addition can be safely and exactly incorporated. | Use the existing semantic Contract-revision path. A `review_pending` Task returns to `in_progress`, and stale target, review, and completion evidence do not remain current. |
| `propose-successor` | The addition is outside the current Contract, all four fields are `yes`, the partition/order/permissions tests pass, and the user has not directed it to remain current. | Present one exact successor proposal and leave the current Contract, review target, receipts, findings, and completion evidence unchanged. Register nothing until approval. An explicit instruction in the same user message to register the addition separately is that approval, so no duplicate confirmation is required. |
| `handoff` | The addition is explicitly deferred or cannot safely enter the current Contract or a successor decision yet, and remains authorized work worth preserving. | Use the existing local Handoff path for its bounded sanitized summary and rationale fields, each at most 1,000 characters. They identify the unresolved outcome and pending decision; they do not expand current acceptance or prove a successor Contract. Nonblocking work may continue; an acceptance-preventing addition must then receive the applicable pause/block status and cannot be used to claim the user's total requested outcome is complete. |
| `pause` | After the required scope disposition, a temporary missing decision or external state prevents safe progress on the active/review-pending Task, while later continuation remains expected. | Use the existing pause transition and concrete pause reason; do not create a Task. |
| `block` | After the required scope disposition, missing authority, safety, dependency, or decision prevents acceptance and no safe authorized progress or narrower preservation route remains. | Use the existing blocker transition as the last resort; do not create a Task. |

An explicit placement direction controls only an otherwise valid disjoint
partition. It cannot authorize duplicated acceptance or override a `no` or
`unknown` independence result. Moving work already covered by the current
Contract into a successor requires an explicit repartition and the existing
semantic Contract-revision consequences.

When no placement direction exists, all four `yes` values select one
`propose-successor`; any `no` or `unknown` forbids a split. If the addition can
still be incorporated exactly, select `revise-current`. Otherwise use
`handoff` as the scope-preservation disposition, then select continue, `pause`,
or `block` strictly by the existing nonblocking, temporary, and acceptance-
preventing meanings. Unknown information never becomes inferred acceptance and
never triggers a decomposition-only question while safe current work can
continue.

When an accepted addition must be revised into the current Contract and then
paused or blocked, perform the existing Contract-only semantic revision before
the separate status transition. The latter must not erase or replace the scope
disposition. A successor-approval wait does not pause or block a current Task
that can still progress within its unchanged Contract. An acceptance-
preventing addition that cannot yet enter a Contract is summarized in Handoff
first and then pauses or blocks the owning Task; a nonblocking Handoff never
hides a mandatory unresolved part of the user's overall request.

The one successor proposal must state its proposed Contract, review tier,
kind/lane/order, exact added scope, and what remains in the current Task. A
current-before-successor relationship uses the next available order in the
same sequential lane; `optional` is allowed only for genuinely independent
work. If existing ordering cannot express the relationship, splitting is
ineligible. Approval invokes only the existing `task add` path. Rejection or a
direction to keep the addition current selects `revise-current` when safe;
withdrawal requires explicit user direction.

At most one proposal may be produced from one explicit addition event, and it
cannot be recursively subdivided during that event. A rejection, paraphrase,
clarification, or reply about the same added outcome remains the same event;
only a materially changed user instruction for scope, order, or permission can
start a new assessment. Before the session ends, the current Task completes,
or an unresolved proposal is otherwise left behind, the existing Handoff path
must record a bounded sanitized summary of every added outcome and, in the
rationale, that a user decision is pending. Handoff is not lossless proposal
storage: it does not preserve the full proposed Contract, review tier,
lane/order, or partition and
must never be used to reconstruct or register them. A resumed agent surfaces
the unresolved Handoff without generating an alternative proposal; it requires
current explicit authority for any missing exact fields. No persisted proposal
counter or event schema is introduced. Across every branch, scope disposition
occurs before any continuation status: all authorized scope remains in the
unchanged or revised current Contract, an approved successor, or a pending
proposal/Handoff summary, with any pause/block recorded only afterward. It is
never silently discarded.

### Inactive Boundary

TG-M20S.3 changes no current `SKILL.md`, package reference, public command,
normal Task-loop call count, SQLite schema, JSON contract, Viewer field,
automatic Task creation, runtime Task splitting, parent/child model, background
LLM work, network behavior, or target-project mutation. In-scope discovery,
test-driven cross-module failure, and unrequested work remain governed by the
current keep/block/Handoff rules and cannot invoke this future decomposition
policy.

## Current Schema-v20 Verification, Ledger, And Bundle Contract

This section defines current post-publication product behavior. It does not
rewrite the immutable v0.10.0 publication record or claim a later published
artifact identity. Schema v20 retains schema-v18 capture, the 21st public
command leaf, schema-v19 completion Bundles and Evidence JSON compatibility, and
publicly activates the existing migration-20 storage foundation plus the
Bundle-v2 null-Runner writer and format-v2 Evidence index.
Receipt readiness, completion linkage, Viewer compatibility, and synchronized
Skill guidance form one supported candidate boundary.

### Authority Snapshot, Whole-Field Criteria, And References

Each Task owns a positive-generation immutable authority snapshot of exactly
its title, description, review tier, exact verification bytes, Contract
revision/state/scope/acceptance/constraints/authority reference, producer, and
domain-separated canonical digest. Task add creates generation 1. A semantic
title, description, tier, verification, or Contract change creates the next
snapshot in the same transaction; unrelated status or metadata changes do
not. Migration creates one `legacy_migration` snapshot of the exact current
stored basis and never reconstructs earlier authority. Acceptance and
verification criteria are immutable exact whole fields with
kind-specific domain-separated digests. Taskgov never splits, rewrites,
judges, or infers subcriteria. Revision-zero acceptance and trimmed-empty
verification have no criterion, while the snapshot still preserves exact
verification whitespace. Target capture binds the current snapshot and
nullable criterion IDs. Every native schema-v18 manifest, Verification Receipt, Review Receipt, Review Finding, and completion-evidence source receives one immutable Evidence
Reference in the same transaction. Its digest covers the exact source
projection, ownership, Contract/snapshot/criteria, four-field target, optional
completion cycle, and closed assurance/producer/version. Git observation is
`machine_observed/taskgov_git/1`; caller assertions are
`bound_attestation/trusted_caller/1`; external revisions are
`external_reference/external_system/1`. Callers cannot select or upgrade those
classes. Migration synthesizes no historical Reference. Schema-v19 and
schema-v20 criterion links, native Bundles, and Evidence JSON are active;
the canonical Analyzer writer remains inactive. The schema-v20 Runner writer
is active only for the TG-M24.2C audit graph defined below and remains
ineligible for every verification and completion gate.

### Receipt Meaning And Record

One Receipt represents one verification run reported by the trusted
caller after the caller ran it outside taskgov. The only material observation
facts are:

```text
result source_revision duration scope_coverage
```

Stable ownership IDs, current Contract revision, a verification-expectation
digest, and recording time are structural binding metadata, not additional
observed facts. The public Receipt has exactly:

```text
verification_receipt_id project_id task_id contract_revision
verification_subject result duration_ms scope_coverage source_revision created_at
```

`verification_receipt_id` is `tg_verification_receipt_` plus 16 lowercase hex
characters. `verification_subject` is tool-owned. Native rows use basis version
1, kind `task_verification_criterion`, and the capture's non-null authority
snapshot and verification criterion IDs with null legacy label. Migrated v17
rows use basis version 0, kind `legacy_caller_label`, null IDs, and their exact
old label. The retained storage column receives only the internal fixed value
for native rows; it is neither caller input nor public evidence. `result` is exactly `pass`, `fail`,
or `timeout`. `duration_ms` is a nonnegative signed-64-bit integer.
`scope_coverage` is exactly `full` or `partial`; `full` is the caller's claim
that this run covers the entire exact current Task `verification` text, while
`partial` is audit context and cannot satisfy completion.

`source_revision` is not caller-authored text. It is the exact locked current
review-target object with keys `kind`, `value`, `base_revision`, and
`generation`. The Receipt writer copies that tuple, the current Contract
revision, and the SHA-256 of domain-separated exact stored verification text
in one short transaction. Its stored representation is the lowercase 64-hex
SHA-256 of `b"taskgov-verification-expectation-v1\0"` followed by the exact
UTF-8 verification bytes. The digest is internal binding data and is not in
the public Receipt. ID and canonical UTC `created_at` are also tool-owned.

The normal order is: finish exact material, set the existing review target and
retain its returned generation, run the governed
verification against that material, record the Receipt with that generation
as the expected basis, then prepare and record review. This is one additional
green-path governance call: the default Tier-2 no-finding bound becomes 10, or
11 only when Effort Advisory is mechanically enabled.

Receipt recording is allowed only for an in-progress or review-pending Task
with verification text that is nonempty after trimming and a nonempty current
review target. At most
one immutable aggregate Receipt is allowed per Task target generation. It
changes no Task timestamp, event, status, Contract, target, review evidence,
completion evidence, or Handoff. A second attempt, including after `fail`,
`timeout`, or `partial`, requires explicitly setting a fresh target generation;
otherwise it fails with `verification_receipt_already_recorded` and message
`verification evidence is already recorded for the current target`.

The public `verification receipt add` command does not run the caller-attested
verification represented by that Receipt, authenticate its caller or process,
assess test quality, infer coverage, or prove the result or that the run
actually exercised the copied target. Invoking Receipt add is the caller's
attestation of those facts. It stores no command body or argument, exit code,
stdout/stderr, log, environment, exception, stack trace, prompt/chat, diff,
credential, or free-form coverage prose. The accepted audit-only TG-M24.2C
Runner is separate: it may launch an eligible trusted-local plan after explicit
opt-in, but it neither creates nor qualifies a Receipt and cannot satisfy the
M21 verification or completion gate. Approved exceptions, result-file import,
configured runners that create, import, or qualify Receipts, signatures, and
debug retention are outside this initial Receipt contract.

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
review when no status is supplied. In the same edit, an explicit
`in_progress`, `paused`, `blocked`, or `cancelled` transition follows its
normal validation after invalidation; explicit `review_pending` or `done` is
rejected because the target was cleared. Completion-evidence options in that
edit are rejected as `completion_evidence_conflict` rather than being silently
discarded. Contract revision and reopen retain their existing invalidation
behavior. None of these cases deletes historical Receipts. A failed Receipt
does not itself pause, block, revise, hand off, or otherwise mutate the Task.

The immutable completion cycle already stores the exact Task target tuple.
Schema v17 added internal `verification_basis_version`, nullable
`verification_expectation_digest`, and nullable `verification_receipt_id`
fields to each completion cycle. Existing cycles migrate as version 0 with
both nullable fields null. Every post-activation native cycle is version 1 and
stores the same domain-separated digest computed from its exact Task
verification text, including the empty string and any preserved whitespace. A
verification expectation whose trimmed text is nonempty additionally requires
a foreign-key link to the unique qualifying exact-current Receipt, while one
whose trimmed text is empty requires a null link without changing its exact-byte
digest.
Every normal post-activation native completion must insert version 1. The sole
existing reopen compatibility bridge may still insert version 0/null/null only
for its exact unknown-coverage done/no-cycle case; it remains
`legacy_current_done` and partial and cannot satisfy a current gate. No other
post-migration version-0 insert is valid. This discriminator makes an absent
link honest legacy lineage for old cycles and a fail-closed inconsistency for
a new native cycle instead of inferring validity merely from whether a Receipt
row happens to exist.

Schema v18 independently adds `verification_subject_basis_version` and
nullable subject snapshot/criterion IDs. Existing Receipts and cycles retain
subject version 0/null/null, including valid v17 verification-basis-version-1
cycles. A new Receipt and nonempty native cycle require subject version 1 and
the exact capture snapshot/verification criterion; trimmed-empty verification
uses subject version 1 with null IDs and no Receipt. No migration infers a
subject or changes an old label.

The digest and link are internal and do not add a completion-history field.
The linked Receipt must have the same project, Task, Contract revision,
stored expectation digest, and complete target tuple as the cycle and must be
`pass/full`.
Existing cycles and pre-v17 completions receive no synthesized Receipt and
keep their existing public meaning. Initial activation adds no new public
completion-history or Viewer Receipt projection.

Completion-check fail-fast ordering retains the existing missing-attestation
and target checks, then applies the missing/blocking Verification Receipt gate
before review-receipt sufficiency. The Receipt readiness codes and fixed
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

### Public And Read Projection

The sole Verification Receipt write leaf is public leaf number 21:

```text
taskgov verification receipt add
```

It accepts Task ID plus exactly `--result`, `--duration-ms`, `--scope-coverage`, and
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
validation is fail-fast in this exact order: `--read-only`; Task ID, result,
duration, coverage, and expected generation; Task existence; done-state
rejection; other Task status; nonempty verification expectation; structurally
valid current target; expected-generation equality; capture-version-1 basis;
and same-generation uniqueness. The fixed
service failures are:

| Condition | Code | Message |
|---|---|---|
| `--read-only` | `invalid_argument` | `verification receipt add cannot run with --read-only because it writes the database` |
| invalid result | `invalid_verification_evidence` | `result must be one of pass, fail, or timeout` |
| invalid duration | `invalid_verification_evidence` | `duration_ms must be a nonnegative signed-64-bit integer` |
| invalid coverage | `invalid_verification_evidence` | `scope_coverage must be full or partial` |
| invalid expected generation | `invalid_verification_evidence` | `expected_target_generation must be a positive signed-64-bit integer` |
| done Task | `done_task_requires_reopen` | `done task writes require an explicit reopen` |
| any other disallowed status | `invalid_status_transition` | `verification evidence may be recorded only for an in-progress or review-pending task` |
| empty Task verification | `verification_expectation_required` | `task verification must be specified before recording verification evidence` |
| missing current target | `review_target_required` | `set a current review target before recording verification evidence` |
| expected generation differs | `verification_basis_stale` | `verification target changed after the reported run` |
| retained capture version 0 | `evidence_basis_stale` | `current evidence basis must be captured again` |
| Receipt already exists | `verification_receipt_already_recorded` | `verification evidence is already recorded for the current target` |

Task ID syntax/privacy and not-found retain their existing codes. A malformed
stored target or Receipt uses
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
`contract_revision`, `source_revision`, `current_verification_subject`,
`gate`, `counts`, and
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
- `current_verification_subject` is null unless a capture-version-1 target has
  a nonempty verification criterion; otherwise it is the native subject object;
- `gate.required` and `gate.satisfied` are Booleans;
  `gate.blocking_code` is null or exactly `review_target_required`,
  `evidence_basis_stale`, `verification_receipt_required`, or
  `verification_receipt_blocking`; and
  `gate.qualifying_receipt_id` is null or one Receipt ID;
- all four count values are nonnegative integers. Exact-current, qualifying,
  and blocking counts are each zero or one; and
- `recent_receipts` is an array ordered by
  `created_at DESC, verification_receipt_id DESC`.
  Every row has exactly the public Receipt fields above, including its nested
  `source_revision`; no digest or internal cycle link is exposed.

For an expectation empty after trimming, the gate is `required=false`,
`satisfied=true`, with
both nullable fields null regardless of target state. For a nonempty
expectation on a non-done Task, no target yields `review_target_required`; a
capture-version-0 target yields `evidence_basis_stale`; a fresh target with no
Receipt yields `verification_receipt_required`; a current
non-`pass/full` row yields `verification_receipt_blocking`; and a current
`pass/full` row yields satisfied with its ID. A legacy done Task whose matching
completion cycle has basis version 0, whether migrated or created by the sole
compatibility bridge, is an explicit legacy exemption:
`required=false`, `satisfied=true`, both nullable fields null, and
`source_revision` may be null. A version-1 done cycle must obey its stored
expectation/link rule and any mismatch fails closed instead of projecting a
gate.

Successful JSON `task.show` includes this one top-level key in its exact data
contract. Failure data also contains
`verification_evidence=null`. Text `task show` remains byte-for-byte unchanged
and does not summarize Receipt state; agents use JSON for the new gate.

There is no Receipt list/show/import/export command and no Viewer Receipt
panel or snapshot field. The Viewer accepts source schemas through v20 while
retaining snapshot v4 content. Its existing
bounded batch completion-history read internally joins only the Receipt fields needed
to validate version-1 cycle and subject links plus provenance, manifests, and
References, fails closed on inconsistency, then discards them; no ledger
dataset or fact enters the snapshot. Receipt writes are not
Viewer-relevant and perform no Viewer refresh; a successful write remains
backup-eligible through the existing post-commit coordinator. Failed or read-
only calls invoke neither artifact path.

### Migration And Activation Boundary

Schema v17 migration `verification_receipts` creates
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

Migration 18 `evidence_ledger_capture` adds immutable authority snapshots,
whole-field criteria and links, normalized Review provenance, artifact
manifests and entries, Evidence References, current snapshot pointers,
capture-version target bindings, and Verification subject columns. It creates
one exact current-basis legacy snapshot per Task but no historical target
binding, manifest, Reference, provenance row, subject, Receipt, Finding, or
cycle. Reentry validates exact ownership, digests, matrices, triggers, quick
check, and foreign keys without reconciliation or backfill.

### Schema-v19 Bundle Foundation, Schema-v20 Native Writer, And Evidence JSON

Migration 19 `completion_evidence_bundles` adds immutable criterion links, Bundle membership and Finding snapshots, completion Bundles, cycle `evidence_basis_version`/bundle linkage, and Evidence projection state. Every existing cycle becomes version 0 with null bundle ID; migration creates no historical Bundle or link and projects that absence only as `legacy_unknown`.
Every native schema-v19 completion atomically inserts one version-1 Bundle with
its cycle, Task update, event, links, selected gate evidence, Finding snapshots,
and projection-generation advance. On current schema v20, the same transaction
instead inserts one version-2 Bundle. Its verification basis is
`caller_attestation` with the qualifying Receipt for nonempty verification or
`not_required` with no Receipt for trimmed-empty verification, and its Runner
observation is always null. It creates no Runner-derived Reference, criterion
link, Bundle member, or projection. The sole partial legacy reopen bridge stays
version 0/null and advances only that generation. A Bundle is complete or the
completion fails before write; its canonical payload is capped at 16 MiB.

Bundle v2 adds exactly the root `verification_basis` object and
`runner_observation` field to the v1 payload. For R3B,
`verification_basis` has exactly `basis_version=1`, the derived `kind`, the
matching nullable `verification_receipt_id`, and
`runner_observation_id=null`; `runner_observation` is null. Its envelope uses
`format_version=2` and digest domain
`taskgov-completion-evidence-bundle-v2\0`. The preserved v1 envelope, payload,
domain, bytes, and digest are unchanged.

Evidence JSON is a deterministic one-way SQLite projection. Canonical sorted-key compact UTF-8 JSON uses integer-only JSON values where numeric, preserves valid Unicode without normalization, and ends each file with one LF. A schema-v20 index uses envelope `format_version=2`, digest domain `taskgov-evidence-index-v2\0`, and adds exactly nullable `bundle_format_version` to each entry: null for `legacy_unknown`, 1 for a preserved v1 Bundle, and 2 for a native v2 Bundle. Native entries reference `bundles/<completion-evidence-bundle-id>.json`; a schema-v20 projection may therefore reference both preserved version-1 Bundles and new version-2 Bundles without rewriting existing payload bytes or digests. Pre-v19 entries are `legacy_unknown` with null Bundle/file fields.
The index includes every cycle, is ordered by Task ID, ordinal, and cycle ID, and is capped at 100,000 entries and 64 MiB without truncation. Publication flushes immutable Bundle files and atomically replaces `index.json` last; SQLite remains canonical, unreferenced files are ignored, and JSON is never imported or used to repair the database.
Contention or failure preserves the last-good index and committed Task result, leaves projection due, and adds only `evidence_projection_deferred` or `evidence_projection_failed`. Setup is the sole explicit repair; doctor only reports stored projection facts. Evidence JSON exposes no Evidence command, custom path, Viewer field/UI, browser launch, server, watcher, or network action; it invokes or projects neither Analyzer nor Runner and adds no normal-loop call.

## Current TG-M21.4B Recovery Candidate Validity Contract

TG-M21.4B, Task `tg_task_9b746fbe5fe4927f`, owns the current recovery
classification correction. Its authority reference is
`conversation_decision:2026-08-02:recovery-candidate-boundary-followup`.
Every recognized managed candidate first passes physical-file, SQLite,
schema/history/object, quick/foreign-key, project identity, binding lineage,
filename/embedded metadata, generation repository, retention, and set-envelope
validation. Only after the whole set passes may exact stored Task verification
be classified against that candidate's source schema, with privacy checked
before capacity and without exposing or rewriting the value.

Each candidate's own immutable pre-publication repository snapshot is part of
that structural validation. Across the complete set, each generation ID maps
to exactly one complete metadata tuple in every filename, embedded row, and
maintenance pointer where it appears. For schema v11 and later, its pointer must equal
its latest embedded row, its own artifact is the one file-only generation in
the physical prefix visible at publication, and any bounded row-only history
is strictly older. Schema v10 has no generation rows and may point only to an
older mechanical generation: when a retained physical predecessor exists it
must equal that predecessor's complete metadata, and only the first retained
candidate may point to an older generation whose ID is absent from the complete
physical candidate set. Candidate schemas need not be monotonic after an older
fallback is recovered and backed up for migration.
Every present SQLite value whose recovery metadata contract is `INTEGER`,
including generation-row retention and maintenance backup policy/pointer
integers, is checked at the shared storage-reader boundary before conversion
or range validation. Only the Python value produced for SQLite storage class
`INTEGER` is accepted; `REAL`, `TEXT`, `BLOB`, Boolean-like non-SQLite input,
or another storage class is structural failure. It is set-fatal as
`project_state_unreadable`, including with a present primary or an older valid
candidate, and cannot reach private normalization or canonical publication.
A candidate-derived `-journal`, `-wal`, or `-shm` entry, including a
filesystem case alias, is set-fatal even when empty. The stored Task validator
examines all rows for malformed storage values before returning a
candidate-local result; among local results, privacy has precedence over
capacity.

| Observation | Classification and selection | Setup result |
|---|---|---|
| Structurally coherent, same-project candidate at the current recovery binding whose stored Task verification passes its source-schema rules | eligible; select the newest `(published_at, generation_id)` | continue recovery |
| The same safe candidate whose stored Task verification alone fails privacy or source-schema capacity | candidate-local rejection; retain and observe the file but exclude it from selection | recover the newest older eligible candidate, or `setup_restore_failed` when none remains |
| Corrupt/unreadable SQLite, unsafe file or sidecar, unsupported/newer/incomplete schema, failed quick/FK check, foreign identity, binding/lineage divergence, metadata/repository/retention/structure inconsistency, duplicate or overflow | set-fatal; never skip it to reach another candidate | the existing specific journal/busy/newer result where applicable, otherwise `project_state_unreadable` |
| Any candidate addition, removal, replacement, stamp/order/content classification change, selected-candidate change, or canonical database/journal appearance after planning | post-plan recovery/restore/publication drift; fail closed and never reselect under the established plan | `setup_restore_failed` before restore or canonical publication |

The mechanically newest candidate remains the structural head for lineage and
generation-envelope validation even when it is candidate-local rejected.
Selection additionally requires an exact identity scheme, binding generation,
canonical path hash, and complete binding lineage match with that head; a
structurally valid older lineage-prefix artifact may remain in the inventory
but is never a fallback across a binding generation.
Initial discovery and lock-held revalidation retain every eligible and rejected
candidate plus its physical identity; their complete inventory,
classification, and selected candidate must match. The established complete
inventory remains bound through source copy, repository normalization, and the
last validation immediately before canonical publication; a later rescan may
only confirm it, never replace or narrow it. Recovery of an older candidate
keeps every structurally coherent generation in the normal row/file/pointer
envelope; rejection changes selection only and never excuses a missing or
mismatched generation relation. Classification and recovery do not rewrite a
rejected artifact, although later already-authorized retention may prune
managed generations normally. A present fixed primary remains authoritative:
ordinary consumers do not fall back to a backup, while setup's deep inspection
still scans every candidate and applies set-fatal hardening. A local
privacy/capacity rejection does not invalidate an authoritative primary, but a
malformed stored Task value does.

The private SQLite copy must still match the selected candidate's project,
binding, embedded generation rows, maintenance pointer, source schema, and Task
classification before repository normalization. Fixed recovery re-runs the
same deep resolver at restore entry and immediately before no-replace canonical
publication, and revalidates the normalized private copy's required schema
objects and exact normalized repository state. Legacy backup-only recovery
applies the same observation to the source and copied stage and re-runs the
full source resolution immediately before publishing the private stage.
Any shallow inventory refresh precedes, and never follows, the final deep
source-set comparison.

`restore_managed_backup` returns only after the no-replace canonical link has
succeeded. That return is the durable setup boundary: setup records
`database_restore` immediately, before comparing the returned source schema or
performing its post-link setup-state inspection. A later schema mismatch,
inspection failure, or other restore-stage failure remains
`setup_restore_failed` and returns `database_restore` in the exact durable
`completed_writes` prefix. A failure before the link/return reports no restore
write. Retry recomputes from the canonical state and the restore temporary is
cleaned in either case; no later failure authorizes reselection or another
canonical publication.

The current classifier admits 500 for schema v17 and 1,000 for schema v18, v19, or v20,
rejecting the next character in each source schema. It does not generalize
local rejection to another field
or to structural, identity, lineage, metadata, or TOCTOU failure.

## Current TG-M21.4C Stored Task Read And Privacy Contract

TG-M21.4C, Task `tg_task_efa90606fed8fba0`, owns the accepted schema-v18
stored Task read hardening retained through schema v20. Its authority reference is
`conversation_decision:2026-08-03:pre-m22-qa-baseline-hardening`.
Every Task-loading operation reads the source-schema capability once and
validates each complete loaded Task row through one shared row/batch validator
before public allow-listing, compact-field omission, filtering, derived-state
use, or use as a write basis. The validator does not normalize, coerce,
truncate, repair, or rewrite stored values.

For supported schemas through v20, exact text and nullable-text storage classes, exact SQLite
integers, stable IDs/project ownership, canonical lane/order, closed
kind/priority/status/review-tier enums, canonical timestamps, bounded
free-form privacy, and the blocker, pause, completion, current-review-target,
Contract-pointer, and completion-history cross-field matrices are validated as
one row contract. Text privacy is checked before its capacity. The validator
uses source-schema capabilities rather than per-row schema introspection and
accepts only columns valid for that supported source.
Task-row fetches share the same boundary: malformed or undecodable SQLite TEXT
and other non-busy fetch/decode faults use the fixed stored-state error before
projection, while genuine SQLite busy/locked state retains `database_busy`.

Bounded `task list`, `task current`, and `task next` operations select complete
Task rows and validate only that selected batch before filtering or projection;
they add no unrelated whole-table rescan. `task show`, Review Packet basis,
checkpoint, handoff, Effort Advisory, completion/review/verification lifecycle,
and metadata-only writes validate their selected Task row before dependent
content or mutation. Doctor, Viewer capture, setup, migration/reentry, and
managed recovery load every Task row without a project filter and validate the
complete batch, including project ownership. A caller that
already holds a validated Task row passes it to dependent review/history
readers, avoiding per-Task schema introspection or duplicate Task reads.

A current stored Task fault always fails closed with exit 2, code
`project_state_unreadable`, and message
`project state could not be read safely`. A normal command returns its existing
command-specific empty data shape, no warning, no partial projection, no
rejected bytes, and no write. Doctor uses the component mapping defined above.
A routine post-commit Viewer refresh preserves the committed business result
and last-good Viewer and emits only the existing fixed
`viewer_refresh_failed` warning; setup preflight fails no-write with the fixed
stored-state error, while a failure confined to setup's later Viewer stage
remains `setup_incomplete`.

Managed recovery preserves exactly one M21.4B exception: only stored Task
`verification` privacy or source-schema capacity failure is candidate-local.
Wrong storage class, enum, cross-field matrix, another Task field's
privacy/capacity fault, or any other structural Task fault is whole-set fatal
as `project_state_unreadable`; it cannot publish a canonical database or
select an older candidate.

## Current TG-M21.4D Stored Contract Pointer Integrity Contract

TG-M21.4D, Task `tg_task_7051724dca3f1501`, owns the accepted schema-v18
Contract-pointer relationship correction retained through schema v20. Its authority reference is
`conversation_decision:2026-08-03:m21-4d-effort-observation`. After the
TG-M21.4C scalar row checks pass, the same shared validation boundary performs
exactly one bounded bulk relationship read for the loaded Task IDs when the
source schema is v8 or later and the batch is nonempty. Source schemas v1-v7
have no Contract capability and perform no relationship read.

For each loaded Task, `current_contract_revision=0` requires no related
`task_contract_revisions` row. A positive pointer requires every related row
to have exact TEXT same-project/same-Task ownership and an exact positive
SQLite INTEGER revision; the pointed revision must exist and equal the latest
raw related revision. Dangling, foreign-project, nonlatest,
revision-zero-with-row, duplicate, wrong-storage-class, malformed, or
ownership-mismatched relationship state fails before Task projection,
dependent-state use, or write. Values are not coerced through `int(...)` or a
SQLite aggregate before their storage classes are checked.

The relationship read is scoped only to the already validated selected batch.
It does not query once per Task, scan an unselected Task's Contract history, or
perform a general cross-table audit. Existing Contract-content validation
remains owned by the Contract repository after this relationship boundary.
Bounded list/current/next therefore retain their selected-row behavior, while
Doctor, Viewer, setup, migration/reentry, and managed recovery apply the check
to their existing whole-Task batch.

Every relationship fault uses the TG-M21.4C fixed exit-2
`project_state_unreadable` / `project state could not be read safely` result,
with no rejected bytes, warning, partial projection, or write. It is a
structural whole-set failure during recovery and never receives the M21.4B
verification privacy/capacity candidate-local exception. Valid revision-zero
and latest-positive states remain byte-compatible. Intentional empty review
target with positive generation and canonical stored-lane behavior are
unchanged.

## SQLite, Migration, And Concurrency

### Initialization And Supported Schemas

`setup` is the sole public initializer and migrator. No Task, handoff, review,
doctor, read, or write command creates/migrates a missing/old database.
Missing state is `db_not_initialized`; supported older state is
`migration_required`; a newer schema is `schema_too_new`. Old binaries reject
newer state and never downgrade/write it.

Fresh setup creates schema v20. Structurally complete contiguous source schemas
v1-v19 are setup-only migration inputs; v20 is idempotent current state.
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
| v17 | immutable Verification Receipts and completion-cycle verification basis |
| v18 | authority/criterion capture, Review provenance, target manifests, Evidence References, and Verification subjects |
| v19 | native completion Bundles, criterion links/Finding snapshots, and Evidence JSON projection state |
| v20 | verification Runner shadow storage and Bundle-v2 null-Runner tagged union |

Each migration is transactional, idempotent, rollback-tested, validates
contiguous history and required objects/rows, preserves project/business IDs
and durable records, and passes `quick_check` and foreign keys. Acceptance
retains the realistic 12-Task/191-event fixture and historical completion/
review trace through every supported source version. No migration parses
private prose to invent structure.

### Private Schema-v20 Foundation And Public Activation Boundary

Throughout TG-M24.R3A, supported product behavior remains schema v19: the
public `SCHEMA_VERSION` and setup `schema_to` remain 19, setup still accepts
v1-v18 only as migration inputs, Viewer snapshot v4 still accepts v5-v19, and
the schema-v19 native version-1 Bundle writer remains unchanged.

R3A's schema-v20 work is restricted to an explicitly injected private database
path. This is the sole non-public exception to the rule that explicit setup is
the only migrator: it migrates one caller-owned disposable v19 database in
place, at the same path, inside one `BEGIN IMMEDIATE` transaction. It performs
no copy, backup, publication, managed recovery, or canonical-state operation.
Rollback restores the logical schema and data; SQLite file-byte identity is not
claimed.

Migration 20 is exactly `verification_runner_shadow`. It preserves every
existing v19 business row and stable ID, including the canonical payload bytes
and digest of each existing version-1 Bundle. Existing Task Runner markers are
zero and existing cycle/Bundle Runner basis fields are null; the four new
immutable Runner tables and new Runner Evidence rows start empty. Schema-v20
Bundle version 2 admits only `caller_attestation` or `not_required`, and its
Runner observation pointer is always null. Any Runner observation, Reference,
or criterion link remains standalone audit history and is never a completion
cycle or Bundle basis at schema v20. Every schema-v20 Runner record is
gate-ineligible version `0`. R3A owns only the complete physical DDL and
storage-parent integrity defined by the design; 2A-2C own the future plan,
process, observation, cleanup, and repository/service admission rules.
The Bundle table rebuild does not preserve or replay arbitrary caller DDL. A
persistent index or trigger whose name is not R3A-owned but whose
`sqlite_master.tbl_name` is `completion_evidence_bundles` is unsupported
attached residue: successful migration removes it with the old v19 Bundle
table, while transaction rollback restores it. Unrelated standalone objects
not attached to the rebuilt table remain unchanged.
Marker-only, partial-owned-object, same-version owned-object drift, a known
later marker, busy/contention, integrity, or foreign-key failure is fail-closed
and leaves no partial migration.

TG-M24.R3B added no schema object or migration. It owns the accepted public
schema-v20 activation: the public schema constant and setup target, the
Bundle-v2 null-Runner payload/serialization/digest writer, and schema-v20
compatibility for the existing M22 Evidence/JSON contract, Viewer, and managed
backup/recovery. R3B creates no Runner resolution, attempt, sandbox event,
observation, Evidence Reference/link, Bundle member, or Runner projection; 2C
owns the first durable Runner mapping, write, and projection. R3A and R3B remain
separate Tasks, commits, and fresh evidence gates. R3B owns the integration
review over the exact matched commits. Only that PASS may authorize a code/main
cutover; canonical database migration remains a later explicit public setup at
a separate approval checkpoint.

Public admission distinguishes a complete v19 source from a hybrid before any
mutation. A database declared as v19 but containing any recognized v20-owned
table, explicit index, trigger, or column fails closed in the canonical
resolver, setup inspection/migration, Viewer, and managed backup/recovery paths.
Recognition follows SQLite's case-insensitive identifier equality: any catalog
object occupying a v20-owned table, explicit-index, or trigger name is a
collision, while a column marker is scoped to its designated parent table.
Generated columns are column markers under the same rule.
R3B admission initially required the four Runner tables and Runner
Reference/link sets to be empty. Current complete-v20 admission instead accepts
either that empty predecessor state or the exact TG-M24.2C audit graph below.
It rejects a malformed, duplicate, foreign-owned, partially linked, or
gate-eligible Runner graph. Every Task Runner-basis marker remains zero and
every cycle/Bundle Runner-observation pointer remains null; native Bundle-v2
`caller_attestation` and `not_required` verification basis remains valid.
This check occurs before any database or sidecar write, migration backup,
recovery copy/publication, Viewer publication, or managed-backup write. A complete v19 source alone may invoke migration
20; a complete v20 source receives validation-only reentry. Unrelated extra
objects retain the existing policy, including R3A's deliberate removal of
unsupported unowned indexes/triggers attached to the rebuilt Bundle table and
preservation of unrelated standalone objects. No migration-21 object exists.

Schema v20 is the intermediate M24.2 shadow foundation and never becomes a
qualifying Runner gate basis. Before M24.3 can activate that basis, a separate
Tier 2 contract must define schema v21; this section chooses no schema-v21
migration marker, tag, or DDL.

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
handoff record's one complete retry is the sole exception. A failed write
transaction leaves no row/event/receipt/Git/target change. A TG-M24.2C command
may nevertheless return an error after its atomic T1 or proved cleanup-only
append has already committed; those completed transactions are not rolled
back. Every Runner transaction that itself fails still leaves no partial graph.

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

One shared resolver owns every production database/backup/Evidence/Viewer/lock target.
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

Relocation failure has status null, required schema 20, empty warnings, one
error, null token/expiry, and no rejected value. A no-token mismatch preserves
the read-only future `planned_writes`; invalid/expired/stale/used/not-required
token rows have empty write arrays and mechanically observed bounded context.
Earlier common-preflight errors retain precedence.

The complete setup write vocabulary is `database_restore`,
`database_initialize`, `migration_backup`, `database_migrate`,
`maintenance_configure`, `legacy_state_publish`, `project_binding_update`,
`evidence_projection_publish`, `viewer_publish`, and `legacy_state_cleanup`. Durable order is:

1. one source prefix: empty, restore, legacy publish, restore plus legacy
   publish, or initialize;
2. migration backup then migration;
3. maintenance configuration;
4. binding update only for confirmed mismatch;
5. Evidence projection publication;
6. Viewer publication;
7. legacy cleanup only after fixed database, binding, maintenance, Evidence, and Viewer.

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

Preview creates no directory, lock, sidecar, temporary, backup, Evidence, Viewer, Git, or
target change. Actual publication holds one fail-fast package transition lock
before the backup lock, uses SQLite backup API without a source writer during
copy, and releases writers before Viewer/cleanup. Failures before fixed
publication remove only proven owned staging; failures after publication keep
fixed state authoritative and legacy state intact for resumable cleanup.

## Setup, Recovery, Evidence, Backup, And Viewer Maintenance

### Setup Contract

`setup` is explicit, noninteractive, idempotent, and the sole initializer,
migrator, one-way maintenance opt-in, relocation flow, fixed/legacy recovery,
and canonical Evidence/Viewer repair action. Preflight validates runtime, one physical
layout, project, state ownership, package integrity, Git ignore when applicable,
journal, schema, identity, binding, and artifacts.

When canonical state is absent, setup checks only the canonical managed backup
directory and applies the TG-M21.4B validity matrix. It chooses the newest
eligible same-project, current-binding generation by publication time then ID;
only a Task-verification capacity/privacy rejection may expose an older
eligible generation. Existing unreadable canonical state is never overwritten.
If only locally rejected candidates remain, setup fails `setup_restore_failed`
rather than initializing empty.
A whole-set structural, identity, binding, lineage, metadata, repository,
retention, sidecar, or set-envelope fault never exposes an older candidate and
retains its specific resolver result where applicable, otherwise
`project_state_unreadable`. After an eligible candidate and immutable plan are
established, later drift or restore/publication failure remains the separate
`setup_restore_failed` boundary.

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
`backup_interval_minutes`, `backup_generations`, `evidence_status`,
`viewer_status`, and `relocation`. `schema_to` is always 20. `schema_from` is safely observed source
schema, selected recovery schema, or null. Policy values are effective
requested/stored values, not persistence claims. `maintenance_enabled`,
Evidence status, and Viewer status describe durable post-command state.

Evidence and Viewer status are each `not_present`, `current`, `published`, or `repair_required`.
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
| structurally coherent recovery set with every current-binding candidate locally rejected only for Task-verification privacy/capacity | `setup_restore_failed` |
| post-plan recovery selection/copy/normalization/publication failure | `setup_restore_failed` |
| recovery-set structural/set-fatal defect | the existing specific resolver error where applicable, otherwise `project_state_unreadable` |
| migration backup failure | `setup_backup_failed` |
| initialization failure | `setup_initialization_failed` |
| migration failure after backup | `setup_migration_failed` |
| configuration, Evidence, Viewer, cleanup, or other later partial failure | `setup_incomplete` |

The fixed messages for invalid policy, restore, backup, initialization,
migration, and later partial failure are respectively
`backup policy is outside the supported range`,
`managed backup could not be restored`,
`setup backup could not be completed`,
`project state could not be initialized`,
`project state could not be migrated`, and
`setup completed only partially; rerun setup`. A resolver-originated recovery
failure retains that resolver error's stable message, including
`project state could not be read safely` for `project_state_unreadable`.
Setup success has empty warnings and errors; failure has empty warnings and
exactly one error. Rerun recomputes from durable state; a prior migration
backup never substitutes for the new attempt's required backup.

The shared backup primitive uses SQLite backup API, validates project identity,
schema/history, regular physical paths, `quick_check`, and foreign keys,
closes/flushed temporary state, and atomically publishes. Setup holds its
zero-wait artifact lock through backup publication/reconciliation and the
corresponding migration commit, but never a SQLite writer while copying.
Preview creates neither lock nor artifact.

### Same-Process Maintenance

Maintenance is permanently enabled by successful setup; there is no disable
surface. Every successful state-changed business mutation commits and closes
SQLite before bounded same-process maintenance. Any such mutation may retry a
due Evidence projection; only completion-cycle insertion advances its source
generation. Viewer refresh runs second only for Viewer-relevant mutations, and
due backup runs third. A changed handoff advances neither projection but may
retry already-due Evidence before backup. Read-only, failed, replayed, no-op,
setup configuration, and maintenance-internal operations trigger nothing.
TG-M24.2C retains exactly the one existing target-set maintenance opportunity
only for a successful or fallback command. A post-T1 Runner error invokes no
maintenance; T1 has already advanced the ordinary due state, so the next
normal maintenance opportunity or explicit setup repair catches up. Internal
Runner intent, cleanup, observation, Reference, and link writes do not add
another coordinator call or advance Evidence or Viewer generation.

Taskgov starts no detached process, child process, thread, timer, watcher,
service, queue, daemon, scheduler, or network operation. Each artifact uses a
canonical regular one-byte OS advisory lock with zero wait; process termination
releases ownership, so leftover regular lock files are harmless and never
deleted by age. Unsafe lock path, contention, or failure preserves primary
result and last-good artifact, leaves due state, and emits at most the fixed
continuation warning:

| Code | Message |
|---|---|
| `evidence_projection_deferred` | `Evidence projection refresh was deferred; task result is unchanged` |
| `evidence_projection_failed` | `Evidence projection refresh did not complete; task result is unchanged` |
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

Snapshot v4 accepts source schemas v5-v20. One query-only transaction validates
schema/project/binding, reads generation, validates the complete source-aware
Task batch through the TG-M21.4C row and TG-M21.4D relationship boundary, and
assembles rows; rendering and
publication occur after close. A stored Task fault produces no snapshot or
replacement and therefore preserves the last-good Viewer.

It contains version/UTC `generated_at`, project ID/display, source schema,
seven status counts, explicit Task allow-list, newest at most 10 sanitized
events/review receipts/findings, and the exact completion-history projection. Sources
v5-v14 synthesize zero cycles with `legacy_history_incomplete=true`; v15-v20
read stored history in query batches of at most 500 Task IDs. For sources
v17-v20, the batch reader validates version-1 completion-cycle Verification
Receipt links; v18+ additionally validates subject, provenance, manifest, and
Reference relations, while v19-v20 validate and discard the Bundle
discriminator. Source v20 additionally validates the Bundle-v2 verification
basis, its null Runner observation, and any standalone TG-M24.2C audit graph,
then discards every Runner field without exposing it.
It discards every joined ledger field. The Viewer
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

Taskgov stores only bounded sanitized summaries, legacy command labels, exit/duration/
status/time metadata, hashes, stable IDs, and explicit structured evidence.
It never stores or emits API keys, credential/session tokens, cookies,
authorization headers, raw provider bodies, private prompts, chat transcripts,
large/raw diffs, raw stdout/stderr, full logs, stack traces, environment dumps,
review reasoning/bodies, OS/SQLite exception detail, raw paths in identity
metadata, expected/actual hash pairs, or rejected values.

A native Verification Receipt stores only the fixed internal compatibility
label, closed result, duration, coverage, tool-owned identity/time and
structural subject, and exact Contract/expectation/target binding. A v17 row
retains its sanitized caller label as explicit legacy data. Neither form stores a command body or
arguments, exit code, result body, stream, log, environment, exception,
arbitrary coverage prose, or debug-retention variant.

Current schema-v20 free-form limits not narrowed above are: title 200
characters; description 4,000; stored/read/internal verification and its
derivatives 1,000; explicit public Task add/edit verification 1,000;
tags/reviewer/target/external revision/authority ref
500; note 2,000; event/receipt/finding/resolution/pause/reopen/Contract
change reason 1,000. Secret/header/private-key/password/token/api-key,
traceback, raw stream dump, and large diff patterns are rejected with
`privacy_rejected` before storage. Rejected patterns include bearer tokens,
authorization headers, private-key blocks, `password=`, `token=`, and
`api_key=`. Public read projections revalidate stored content and cross-field
matrices and Contract-pointer relationships through the current TG-M21.4C and
TG-M21.4D shared validator before omission or
exposure. Invalid stored Task content returns only
`project_state_unreadable` / `project state could not be read safely`, never
the rejected value or a caller-input error. The existing `lane` and
`blocked_reason` inputs have no numeric
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
project_state_unreadable
review_target_required review_target_missing review_target_mismatch
artifact_manifest_path_unsafe artifact_manifest_too_large
artifact_manifest_stale evidence_basis_stale evidence_ledger_inconsistent
evidence_bundle_too_large
review_changes_requested review_receipts_insufficient
review_finding_unresolved review_receipt_mismatch
review_receipt_already_recorded invalid_review_evidence
verification_required verification_expectation_required
verification_basis_stale verification_receipt_required
verification_receipt_blocking verification_receipt_already_recorded
invalid_verification_evidence review_required commit_required
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
six maintenance codes. Warnings never carry Task prose, secrets, raw output,
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
state and evidence remain solely in the project-local Task database and are
inspected through the public CLI. Current product, privacy, review, migration,
and artifact requirements above do not depend on historical text.

## Deferred Boundaries

### Staged TG-M24 Trusted-Local Runner Boundary

This boundary is staged. Accepted 2A covers only physical plan capture, strict
plan selection, exact target observation, and private materialization. Accepted
2B covers only the bounded runtime/process adapter and deterministic cleanup.
Accepted 2C owns parent-service orchestration plus audit-only schema-v20
observation and Evidence capture. Current 2D owns only integrated acceptance of
that shadow slice; completion-gate and release portions remain inactive behind
their owning sequential units.

The approved execution direction is an explicit-opt-in Runner for repositories
the user already trusts. Untrusted, external, unsupported, or visually verified
targets remain on the existing M21 manual verification route and are never made
eligible by convenience, fallback, or a prior run. Eligibility and execution
must bind the current Task, Contract, verification criterion, and exact target;
use fixed argv with no shell or PATH lookup; exclude credentials from the child
environment; execute only an exact private materialization; and copy no working
tree or result bytes back to the target.

TG-M24.2A implements only the dormant target-and-plan half of that boundary. It
adds no public command, normal-loop call, durable Runner row, or target process.
The sole plan is the current physical
`<physical-package>/config/verification-runner.json`. `config/` is the existing
local-only package region: the file is not generated by setup, listed in the
release manifest, or taken from the selected Git target. It is read as one
stable single-link physical regular file without following a hard-link alias,
symbolic link, or reparse. Before and
after that read, a bounded shell-free Git check must prove that no literal
case-insensitive form of the physical plan path is registered in the current
index and that the physical spelling is effectively ignored; uncertainty
blocks. A staged snapshot therefore cannot register the same Windows path
under another letter case while using it as plan authority. Its
version-one JSON has these closed member sets; an unknown, missing, duplicate,
or differently typed member is invalid:

```text
PlanV1 = version, plan_id, trusted_local, entries
EntryV1 = task_id, contract_revision, verification_expectation_digest,
  verification_criterion_digest, coverage, steps
StepV1 = step_id, mode, entrypoint, argv, cwd, timeout_seconds, cpu_seconds,
  memory_mib, process_limit, output_byte_limit
```

`version` is exactly `1`; `plan_id` and every `step_id` use the bounded Runner
identifier grammar; `trusted_local` is a JSON Boolean and is the explicit
project opt-in; and `entries` contains zero through 64 distinct exact bases.
Each entry names one Task ID, Contract revision in
`1..9223372036854775807`, the exact 64-lower-hex verification-expectation
digest, the exact labeled verification-criterion
digest, `coverage = "full"`, and one through 16 ordered steps. A step uses only
`script|module`, the matching bounded entrypoint grammar, zero through 64
literal arguments, one bounded relative working directory, the already frozen
numeric process bounds, and `output_byte_limit = 1048576`. Order supplies the
one-based ordinal. `shell` and `PATH` lookup are not plan inputs and remain
fixed false.

TG-M24.2A validates only bounds knowable from the plan: member types and byte
grammars, entry and step counts, literal-argument counts and sizes, total
timeout, and the declared resource/output values. The final quoted Windows
command line depends on the later fixed executable, bootstrap insertion, and
materialized absolute paths. Its `command_line_utf16_units <= 24576` check
therefore remains a TG-M24.2B process-request admission check and is not
approximated or pre-authorized by plan validation.

An absent config directory or plan file produces no plan source. It does not
produce empty raw bytes or a digest. The exact successful resolution matrix is:

| Condition | `plan_state` | `route` | `reason` | Plan identity | Selected entry | `coverage` / `steps` |
|---|---|---|---|---|---|---|
| no plan source | `absent` | `m21_fallback` | `plan_absent` | all raw/id/version/semantic fields null; `plan_blob_object_id` null | null | `not_applicable` / empty |
| valid plan with `trusted_local = false` | `disabled` | `m21_fallback` | `trusted_local_disabled` | raw digest, plan id, version, and semantic digest present; `plan_blob_object_id` null | null | `not_applicable` / empty |
| valid opted-in plan with no entry for the current Task | `no_match` | `m21_fallback` | `plan_entry_absent` | raw digest, plan id, version, and semantic digest present; `plan_blob_object_id` null | null | `not_applicable` / empty |
| exactly one current basis | `runner` | `runner` | null | raw digest, plan id, version, and semantic digest present; `plan_blob_object_id` null | selected-entry digest present | `full` / the selected one through 16 steps |

These are the only successful 2A plan states, routes, reasons, and nullability
relations. If an opted-in plan contains the current Task, exactly one entry
must match the current Contract revision and both current criterion digests;
stale, partial, or multiple matches block. A malformed or over-bound plan also
blocks and produces no successful resolution. The raw plan is at most 65,536
bytes;
its raw digest binds those bytes, while separately domain-separated canonical
plan and selected-entry digests bind the normalized closed semantics. The
exact review-target and target-material digest are combined with those values
only by the parent-owned resolution boundary; neither ambient plan bytes nor a
prior target can supply authority. The schema-v20 compatibility field named
`plan_blob_object_id` remains null for this physical-plan route; no selected
target object is reinterpreted as plan authority.

TG-M24.2A proves that composition through the existing pure
`resolution_idempotency_digest`: its closed input binds the current Task,
Contract, authority and criterion identities, review-target kind/value/base and
generation, target-material digest, and raw/semantic/selected plan digests. The
seal contains no raw plan bytes, argv, or steps and remains an in-process value
in this slice. TG-M24.2C owns parent-service orchestration, persistence, and
dispatch consumption; it does not redefine the 2A component or seal semantics.

Only `git_snapshot` and `git_commit` are addressable by this dormant slice. A
snapshot means the complete stable stage-zero index and a commit means the
complete exact commit tree. Every materialized file is read from those Git
objects, never from the ambient working tree. A same-named config file present
in target material is ordinary target data and grants no opt-in or plan
authority. Sparse entries, symlinks, submodules, unsafe or colliding Windows
paths, object loss, and target drift are rejected. An absent or unsupported
target remains on M21 fallback; stale or inconsistent material blocks.

The closed materialization bound is 10,000 regular files, 30,000 derived
directories excluding the supplied destination root, depth 64, 512 MiB total
file bytes, and the existing 240-byte portable relative-path limit. The
destination must be an already owned,
physical, empty private target directory with no reparse traversal. Creation is
exclusive, every streamed blob is verified against its Git object ID, and a
bounded post-write inventory must equal the admitted entry set exactly. No byte
is copied back. Existing shell-free, no-lazy-fetch Git plumbing may be used only
for read-only target/object observation; no plan entrypoint, verification
command, hook, or target code is launched by TG-M24.2A.

Each accepted execution must establish its Job and process limits before user
code runs, bound wall time, process count, resources, and stdout/stderr, retire
the complete process tree, close handles, and remove its privately owned
temporary tree. Raw output, command arguments, environment, credentials,
private paths, and exception bodies remain transient and are never durable
verification or review evidence. Only the existing closed outcome and bounded
structural evidence may be retained. Cleanup or privacy uncertainty is a
blocking failure.

### TG-M24.2C Audit-Only Parent Orchestration

TG-M24.2C consumes the existing `review target set` dispatch without adding a
public command, argument, success-data field, text line, Skill trigger, or
normal-loop call. The existing exact review-target transaction is called T1 in
this boundary. A fallback T1 contains only that ordinary capture. An admitted
Runner T1 atomically contains the same capture plus its resolution and attempt
intent. Process work is a later parent-service phase. After preflight and
before an admitted Runner T1, the parent acquires the zero-wait Runner lock and
retains it through pending-intent reconciliation,
T1, process/lifecycle work, and the terminal T2. Each SQLite transaction is
short and closes before filesystem or process work; no SQLite writer is held
while a target is materialized or a process runs.

Before T1, the parent resolves every definite Runner-only condition that can be
preflighted. An absent verification criterion, an
unsupported or non-addressable target, `absent`, `disabled`, or `no_match`
plan state, or another definite closed Runner-only failure before an attempt is
admitted produces no Runner resolution, attempt, event, observation,
Reference, or criterion link.
The ordinary exact target is still captured and returned normally. This fallback
does not weaken TG-M24.2A: malformed, ambiguous, stale, inconsistent, or
over-bound plan/target material still blocks with no T1 write.

Only an exact `runner` plan and target may add Runner state to T1. After
preflight, the parent validates the fixed lifecycle inventory and acquires its
one zero-wait Runner lock before any such writer. Lock contention fails with
`runner_busy`; lifecycle or inventory uncertainty fails with
`runner_state_invalid`; both return with no T1 write. Under the retained lock,
pending reconciliation precedes the same short writer that revalidates the
complete prepared basis and atomically records the ordinary target, one
resolution, and one attempt intent. There is no concurrent or crash window
containing a committed eligible target without its intent. Per Task target
generation the admitted cardinality is:

| Durable state | Resolution | Attempt | Cleanup event | Observation | Observation Reference/link |
|---|---:|---:|---:|---:|---:|
| no admitted attempt | 0 | 0 | 0 | 0 | 0 |
| pending intent | 1 | 1 | 0 | 0 | 0 |
| restart cleanup only | 1 | 1 | 1 | 0 | 0 |
| accepted terminal audit graph | 1 | 1 | 1 | 1 | exactly 1/1 |

Every column has a maximum of the shown value. An exact replay reuses the
matching immutable graph without another row; a conflicting digest, second
owner, duplicate, or cardinality overflow fails closed. Runner identifiers are
allocated by the parent from one caller token per record and do not encode a
path or result.

After intent, a terminal observation is allowed only after all three accepted
process proofs (`process_zero`, `handles_closed`, and
`raw_output_discarded`) are true and lifecycle independently proves the exact
attempt and quarantine entries absent. A returned TG-M24.2B result maps its
`outcome`, nullable `reason`, `launch_state`, step summary, duration, and
bounded accounting one-to-one. A launched result uses observation
`route=runner`; a closed no-launch result uses `route=m21_fallback`.
`complete_plan=1` only for a launched `pass` with null reason, every planned
step completed in order, and no failed ordinal; every other admitted result is
zero. No new outcome or reason code is introduced.

A definite post-intent runtime admission failure is the closed
`blocked_prelaunch/runtime_unavailable/no_launch` result. A definite private-
tree, materialization, or request-construction failure is
`blocked_prelaunch/process_setup_failed/no_launch`, but only after exact tree
absence is proved. Both use `route=m21_fallback`. `cleanup_failed`, any false
process/privacy proof, or lifecycle uncertainty is not terminal evidence and
creates no observation.

The terminal transaction inserts the observation, its one Evidence Reference,
its one `runner_observation` link to the bound verification criterion, and the
one cleanup event atomically. The Reference uses only the existing sanitized
Runner observation source projection with
`machine_observed/verification_runner/1`; it stores no raw process material.
It is standalone audit history. It is never a Verification Receipt, completion
cycle or Bundle member/basis, does not advance Evidence-projection generation,
and is not published to Evidence JSON.

Restart never relaunches a persisted attempt. Under the one Runner lock the
service may clean only the exact database-named known tree. When absence is
proved but the process result is unknown, it inserts only the cleanup event
with a null terminal-observation link. That cleanup-performing call creates no
new T1 and returns `runner_state_invalid` without maintenance because it cannot
claim the old result. The cleanup-only event closes only its original target
generation; it is not a permanent Runner block. A later independent target-set
call may admit a new generation after the complete cleanup-only predecessor,
zero actual pending attempts, and empty fixed inventory are proved. Here
`pending` means an attempt with neither an observation nor a cleanup event. A
foreign tree, more than one actual pending attempt, owner drift, or cleanup
uncertainty fails closed and leaves the intent pending for inspection. Target,
authority, or installed-implementation drift after intent similarly permits
only proved cleanup and a cleanup-only event; it creates no observation and
returns a tool-service error.

The fixed private root is
`<physical-package>/state/current/verification-runner`, with only the fixed
one-byte lock, `attempts`, and `quarantine` children. It is resolved by the
shared canonical state resolver and is created only for an admitted Runner
route; feature code never reconstructs it. The schema-v20
`runner_policy_digest` is the fixed
`verification Runner orchestration policy v1` identity
`sha256:8910c1edfd525be0def6a2c3afb65adab11e5a32e9a60ebbf898c175ffd60fa8`.
It is a structural policy label, not a security claim, and is not rederived
from the separately manifest-bound Runner implementation digest. Because the
accepted TG-M24.2B layer exposes no durable canonical runtime digest,
`runtime_digest` is always null in the resolution and sanitized Runner source
projection. The manifest-bound implementation digest and fixed policy label
are the only durable execution-identity fields in this slice.

If storage, lifecycle, or terminal atomicity becomes uncertain after T1, the
already committed target and intent remain current and the command returns a
sanitized tool-service error without claiming Runner success. A separately
completed cleanup-only event also remains when exact absence was proved before
the error. This is the sole narrow Runner exception to the ordinary no-write-
on-command-failure rule: T1 is a completed existing target-set mutation with
its atomic Runner intent, cleanup-only is completed lifecycle closure, and the
failed phase is later. No automatic relaunch, fallback success, Receipt,
review, or completion claim is synthesized.
Successful and fallback target-set output remains byte-compatible with the
existing public contract.

Backup and recovery preserve the SQLite audit graph and validate its exact
cardinality, ownership, digests, links, and gate-ineligible version. The
private attempt tree is generated scratch state and is not copied into a
managed database backup. Viewer snapshot v4 validates any graph and discards
it; Viewer UI, public Task JSON, Bundle v2, completion history, M21 Receipt and
completion semantics, schema/DDL, and Task Runner-basis markers remain
unchanged.

This boundary governs trusted code; it does not claim hostile-code containment,
network isolation, LPAC/AppContainer confinement, or zero capability. Candidate
C, Candidate B-to-C comparison, Package-SID ACL qualification, ETW diagnosis,
claim-bound transfer/recovery, supervisor or trust-root layers, and diagnostic
fault matrices are not M24 prerequisites or completion gates. Their repository
and OS-temporary residues are owned by the dedicated inventory and physical-
retirement units. TG-M24.2C adds no Runner command, schema, Skill trigger, or
completion gate. Its only dispatch is the exact trusted-local opt-in branch of
the existing target-set operation; qualifying gate authority remains inactive
until its later sequential unit is accepted and synchronized.

The current product deliberately excludes pagination/search in CLI history,
parent/child Tasks, acceptance checklists, a public command or Skill trigger for
standalone verification-command execution, generic
result/receipt-file import, action aliases, general/manual backup or restore,
custom export, browser launch/server, durable/general browser persistence
beyond the one-shot envelope, external Issue lifecycle/sync until its intake
contract, cross-project profiles, daily network update checks, reviewer
identity/signatures/attestation, and a generic workflow engine.

Deferred features, the retired TG-M20S study result, and the accepted but
inactive TG-M20S.3 design never change current acceptance, add a normal-loop
command, or authorize target/external mutation until their separately approved
implementation and synchronization gates complete.
