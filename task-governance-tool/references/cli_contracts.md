# CLI Contracts

Use the contents to read the section for the current command, input, or error,
including its directly linked requirements. Unrelated operations and audit-only
detail are not prerequisites for normal Task work.

## Contents

- [Invocation And Public Inventory](#invocation-and-public-inventory)
- [Optional Task Preapproval](#optional-task-preapproval)
- [Envelope And Read/Write Boundary](#envelope-and-readwrite-boundary)
- [`setup`](#setup)
- [`doctor`](#doctor)
- [Task Commands](#task-commands)
  - [`task add`](#task-add)
  - [Batch Task Registration](#batch-task-registration)
  - [`task list`](#task-list)
  - [`task next`](#task-next)
  - [`task current`](#task-current)
  - [`task context`](#task-context)
  - [`task effort`](#task-effort)
  - [`task show`](#task-show)
    - [Task Read Conditions](#task-read-conditions)
  - [Historical investigation (`--audit`)](#task-audit-detail)
  - [`task checkpoint`](#task-checkpoint)
  - [`task edit`](#task-edit)
  - [Runner Plan actions](#runner-plan-actions)
    - [Runner Plan example and OS limits](#runner-plan-example-and-os-limits)
  - [`task complete`](#task-complete)
- [Verification Receipt](#verification-receipt)
- [Verification Receipt Rules](#verification-receipt-rules)
- [Structured Verification Result](#structured-verification-result)
- [Local Handoff Commands](#local-handoff-commands)
- [Review Commands](#review-commands)
  - [`review prepare`](#review-prepare)
  - [Review Evidence](#review-evidence)
    - [Set the exact target](#review-target)
    - [Receipt input and provenance](#review-provenance)
    - [Finding creation and resolution](#finding-resolution)
    - [Structured Finding Resolutions](#structured-finding-resolutions)
  - [Structured Review Results](#structured-review-results)
  - [Caller-Owned Review Handoff](#caller-owned-review-handoff)
    - [Shared path and validation boundary](#handoff-path-and-validation)
    - [Prepare](#prepare-review-handoff)
    - [Read](#read-review-packet)
    - [Save](#save-review-original)
    - [Submit](#submit-review-originals)
    - [Record Review Wait Decision](#record-review-wait-decision)
  - [Recover Review Handoff](#recover-review-handoff)
- [Receipt Output For Integration Or Audit](#receipt-output-for-integration-or-audit)
- [Internal Continuity Boundary](#internal-continuity-boundary)
- [Errors And Privacy](#errors-and-privacy)

## Invocation And Public Inventory

For normal governed-project use, install one physical project-scoped copy.
Unless a section states otherwise, every example below runs from the governed
project root; `<target-project>` is that same root, not the Skill directory.
Replace angle-bracket placeholders before execution:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py <command> [options]
```

Only when the working directory is the installed Skill directory instead, use
the shorter script path and pass the governed project explicitly:

```powershell
python scripts/taskgov.py <command> --repo <target-project> [options]
```

From the target-project root on Linux/macOS, use `python3`, for example:

```sh
python3 .agents/skills/task-governance-tool/scripts/taskgov.py setup --read-only --json
```

Omitting `--repo` means the current directory and never re-roots it to an
enclosing Git worktree. A non-Git directory is valid. Stateful use supports
one physical project-scoped package only; user-wide, symbolic-link, and
Windows junction layouts are unsupported. Python 3.12 or later is required.
Ordinary Task use supports Windows, Linux, and macOS. The explicit trusted-local
Runner supports the same three platforms under their OS-specific limits;
verification without Runner opt-in remains manual. There is no OS-selection command.

The complete public command inventory is exactly these 23 leaves:

1. `setup`
2. `doctor`
3. `task add`
4. `task list`
5. `task next`
6. `task current`
7. `task effort`
8. `task show`
9. `task edit`
10. `task complete`
11. `task checkpoint`
12. `handoff record`
13. `handoff list`
14. `handoff show`
15. `handoff withdraw`
16. `review prepare`
17. `review target set`
18. `review receipt add`
19. `review finding add`
20. `review finding resolve`
21. `verification receipt add`
22. `task context`
23. `review result add`

There are no public aliases, alternate state locations, storage-management
commands, projection-management commands, repair commands, or admin commands.
Unknown commands fail before package, project, Git, or local-state resolution.

Common options are:

- `--repo <path>`: governed project root; default current directory.
- `--json`: emit the stable JSON envelope.
- `--read-only`: prohibit creation, migration, or writes.
- root `--version`: print the package version.

Applicable common options may appear before or after command groups as shown by
`--help`.

`task context` takes no Task ID. Task-specific commands such as `task show` and
`task edit` take the selected ID, normally
`data.selected.task.task_id` from `task context`. Use returned values, not the
illustrative IDs in JSON examples.

## Optional Task Preapproval

Read this section only for explicit introduction or diagnosis of host preapproval.
Normal work reuses the already configured invocation, without a permission-guide
read, new command, approval question or per-Task authorization record.

There are two classes: already-authorized ordinary Task records, and operations
that retain their existing rules. Preapproval never authorizes unsolicited Task
registration, implementation, an existing Contract change, weaker quality gates,
or fabricated evidence. Initial Contract/quality fields for a newly authorized
Task belong to registration; revising an existing Task remains separate.

The optional first-position `--records-only` argument restricts the existing CLI
and ordinary handoff helper. Use the configured absolute Python/package/project
paths and isolated Python flags, for example after explicit host activation:

```powershell
& '<absolute-python>' '-I' '-S' '-B' '<absolute-package>/scripts/taskgov.py' '--records-only' 'task' 'context' '--repo' '<absolute-project>' '--json'
```

The same prefix replaces the ordinary invocation for registration, state/reason/
note updates, checkpoints, local Handoff records, evidence and completion records.
It adds no Task-loop operation. Only state, blocker/pause reason and note options
are admitted on `task edit`; use ordinary `task complete` for completion. Existing
ownership, sequential, exact-target, verification and review gates still apply.
All existing bounded same-process maintenance remains included. Shared-file
`prepare/read/material/save/submit` use the same restriction and generated
continuations preserve it. Existing literal stdin transports remain available.

Contract/purpose/quality/Runner Plan edits, setup/trust/configuration, Git and
external operations, approval overrides, reopen/cancel/withdraw, and integrated
`prepare-finalization`/`finalize` retain their existing authorization route.
Do not switch an already selected integrated workflow to manual just to get
preapproval. MCP approval, review-wait reservations/notifications and automatic
commit permission are unchanged. Normal `task complete` records evidence; it does
not make a Git commit.

An eligible Runner target is rejected before writes with
`runner_execution_requires_authorization`. Preserve the intended verification;
continue that same operation through its ordinary route only under existing
execution authority. It is a definite no-write result, including through
restricted `prepare target`. Other unknown failures retain ordinary recovery.
`record_operation_not_allowed` is an excluded or malformed restricted invocation,
not permission to remove the restriction and retry blindly.

For introduction, inspect the exact existing project hooks and applicable host
configuration. The write-free proposal helper emits a preserving JSON candidate:

```powershell
& '<absolute-python>' '-I' '-S' '-B' '<absolute-package>/scripts/task_preapproval.py' 'propose' '--repo' '<absolute-project>' '--python' '<absolute-python>' '--shell' 'powershell' '--existing-hooks' '<absolute-project>/.codex/hooks.json'
```

Omit `--existing-hooks` only when that file is absent. POSIX uses the same
arguments with `--shell posix` and its native literal quoting. This is a proposed
`PermissionRequest` definition, not an installer or a broad interpreter allow
rule. Show its concrete diff and destination before asking to apply it. Preserve
all other user/managed/inline/MCP settings and existing hooks. Never silently
enable hooks, trust a definition, replace configuration or weaken a host denial.

Applying `<project>/.codex/hooks.json` requires explicit approval. Codex must
support/load enabled PermissionRequest hooks and trust both the project layer
and exact definition; review/trust via the host's hook UI (`/hooks` in the CLI),
then restart/load as required. The embedded fixed bootstrap verifies policy
code before loading it, and the policy pins shipped package contents. The hook
and restricted entry points read package modules from source; existing bytecode
caches are not executed or deleted. Unlisted native/legacy import artifacts
decline to the existing approval flow. Package
changes require a fresh reviewed proposal; there is no automatic trust refresh.
The physical interpreter is trusted local infrastructure, not a command-wide
Python permission. These checks do not provide a hostile-process execution lease.

The hook admits only the configured literal invocation, exact supported
arguments and one bound project. It declines unknown syntax, substitutions,
compound commands, other scripts/projects, drift and inspection failures to the
existing approval flow. More restrictive managed rules/other denying hooks still
apply. A fixture allow response or generated example does not prove actual
Guardian reduction: report host activation and observed outcomes separately,
and leave unperformed deployment/measurement explicit. See the official
[PermissionRequest and trust contract](https://learn.chatgpt.com/docs/hooks#permissionrequest).

## Envelope And Read/Write Boundary

Every JSON result has exactly these top-level keys:

```json
{
  "ok": true,
  "command": "task.next",
  "project_id": "tg_project_550e8400e29b41d4a716446655440000",
  "data": {},
  "warnings": [],
  "errors": []
}
```

Public output contains no local storage, backup, projection, or rejected-input
path. Error rows contain only `code` and a sanitized `message`.
CLI `--json` is UTF-8 plus LF, without indentation/separator spaces or
non-ASCII escapes. Use returned IDs as opaque identities; do not reconstruct
them from a project path.

Successful `task edit`, `task complete`, and `review target set` acknowledge
the write without repeating unchanged `description`, `verification`, `kind`,
`lane`, `lane_order`, `priority`, `tags`, or `created_at` in `data.task`.
A changed field, including a cleared empty string or null, is returned
when named in `changed_fields`. Keep unchanged fields from the working context;
do not replace it with this acknowledgement or add a routine read. Other
operation-specific fields, errors, warnings, and human text are unchanged.
Registration/context, show/audit, and Review Packet retain their full forms.

Inherently read-only commands are `doctor`, task `list`, `next`, `current`, `context`,
`effort`, and `show`, `task complete --check`, handoff `list` and `show`, and
`review prepare`. `setup --read-only` is a no-write preview.

Write commands other than `setup` require current initialized state. They
never initialize or migrate implicitly. `setup` is the only initializer and
migrator. `--read-only` rejects a write form before a business write.

Ordinary Task/evidence updates write only under the governed project's
canonical `.taskgov/`, not the physical Skill or retired package-local state.
Installation, setup, and Runner Plan publication retain their own write boundaries.

Contention returns `database_busy`; unsupported WAL state returns
`unsupported_journal_mode`, without raw database or operating-system detail.
Business writes revalidate their current basis and save atomically. Exceptions
with a durable completed prefix are documented under [setup](#setup) and
[Runner Plan actions](#runner-plan-actions). Maintenance warnings never undo a successful
business write; see [Internal Continuity Boundary](#internal-continuity-boundary).

<a id="setup"></a>

## `setup`

`setup` is the sole explicit initializer, migrator, one-way continuity opt-in,
and canonical Evidence/Viewer projection repair action:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

Setup is noninteractive and idempotent. Configuration writes are limited to
the bounded project hook definitions and explicitly selected optional features
and choice record. It cannot disable continuity after opt-in, contact a network,
mutate Git, change host trust, or modify target source. Its core Git-candidate
check is one bounded effective-ignore preflight; optional Runner configuration
retains its read-only index/ignore checks. Neither launches verification or adds
a normal Task-loop operation.

Use [options](#setup-options) for invocation and [results](#setup-result-fields)
to interpret output. Read only the applicable conditional detail:
[optional choices](#optional-setup-features),
[offline upgrade/recovery](#offline-upgrade-and-recovery), or
[relocation](#relocation-preview-and-approval). For a failed or uncertain result,
use [outcome and retry rules](#setup-outcomes-and-recovery) before another write.

### Setup Options

Options:

- `--backup-interval-minutes <1..1440>`
- `--backup-generations <1..20>`
- `--confirm-relocation <token>` for one explicitly approved exceptional
  relocation
- `--read-only` for a no-write preview

The normal Skill flow supplies neither policy option nor a relocation token.
The exceptional binding-mismatch flow supplies one only after the explicit
approval boundary below. A first setup defaults to 30 minutes after the last
successful managed copy and three retained generations. Once configured,
omitted options preserve stored values; values equal to stored policy are a
write-free replay.

### Offline Upgrade And Recovery

Initial setup or upgrade to separated state uses the fixed project-root
`.taskgov/` area. Stop Taskgov writers and enabled Runner processes before
updating the package and running this explicit offline transition. Preview
creates nothing; write setup preserves the source, validates a private copy,
fences the old fixed location and activates the new state. Do not kill
processes, change ACLs, select another path or initialize around a reported
conflict. No normal Task-loop step is added. Keep package-local retirement
material during updates; it is not a second active DB or automatic fallback.

If the old layout has validated pending legacy cleanup or an owned legacy
migration stage, finish that prior migration with the compatible installed
pre-separation version's setup before upgrading. New setup preserves it and
returns `setup_incomplete`; repeatedly invoking the new version does not finish
the predecessor's work. This applies only to that unfinished old migration,
not healthy sources or new separation preparation. It authorizes no manual
deletion, package downgrade against newer data, or additional normal-loop call.

Within a pre-activation package-local source, setup automatically prefers
eligible fixed-layout managed recovery, then at most one eligible legacy source. It
does not ask the LLM to choose a backup or path. Same-binding legacy primary or
legacy backup-only state can be recovered; a moved legacy backup-only source
is not a relocation candidate and fails no-write as `project_state_unreadable`.
Recovery never overwrites an existing database, even when unreadable. Only
the explicit layout cutover replaces the validated old primary with its
retirement marker after retaining a complete source. Invalid,
foreign, linked, unrecognized, or ambiguous artifacts remain unchanged.
After activation, a missing primary uses only new-root managed recovery and
never selects the old source or retained copy.

Only stored Task-verification privacy/capacity rejection may select an older
eligible same-binding backup. If none remains, setup returns
`setup_restore_failed` with `managed backup could not be restored`, never empty
initialization. Structural recovery faults retain their specific error where
applicable, otherwise `project_state_unreadable`; this includes a journal
beside a missing primary. Changed recovery material or a restore/publication
failure after planning returns `setup_restore_failed`. Do not remove failed
sources or attempt manual database repair as part of this flow.

Package replacement preserves project-local state and requires explicit setup.
There is no public downgrade or restore command. Release rollback means
restoring one matched pre-migration package, database, and managed-artifact set
together; an older runtime against a newer schema, mixed generations, an
in-place reverse migration, or a Git checkout alone is not rollback.

### Relocation Preview And Approval

A binding mismatch never authorizes a rebind. Normal commands and `doctor`
return the bounded relocation condition without writing. Write-mode setup
without a token returns `project_relocation_required`. Only
`setup --read-only` returns successful `status="relocation_preview"` with the
future ordered write plan, an exact confirmation token, and its expiry. Token
issuance is not approval: the Skill presents the plan, waits for explicit
current user approval, and only then calls write-mode setup with that exact
unexpired `data.relocation.confirmation_token` from the preview; its expiry is
`data.relocation.expires_at`. It never infers move/copy/fork semantics or auto-confirms.
Expired or stale context requires a fresh preview and fresh user approval.

Use the token and expiry from [Setup result fields](#setup-result-fields);
the [relocation errors](#relocation-errors) distinguish rejected confirmations.

### Setup Result Fields

`data` always has exactly the fields below plus `optional_features`, described
in [optional choices](#optional-setup-features). Interpret stages, partial success
and retry using [outcome and retry rules](#setup-outcomes-and-recovery):

```json
{
  "status": "setup_complete",
  "planned_writes": [
    "state_layout_retire",
    "state_layout_publish",
    "state_layout_activate"
  ],
  "completed_writes": [
    "state_layout_retire",
    "state_layout_publish",
    "state_layout_activate"
  ],
  "schema_from": null,
  "schema_to": 27,
  "maintenance_enabled": true,
  "backup_interval_minutes": 30,
  "backup_generations": 3,
  "evidence_status": "published",
  "viewer_status": "published",
  "usage": {
    "status": "initialized",
    "schema_to": 5,
    "planned_writes": ["usage_initialize"],
    "completed_writes": ["usage_initialize"],
    "error": null
  },
  "usage_hooks": {
    "status": "not_requested",
    "planned_writes": [],
    "completed_writes": [],
    "trust": "unknown",
    "next_action": null,
    "error": null
  },
  "relocation": {
    "required": false,
    "source_layout": "fixed_current_v1",
    "identity_scheme": "uuid_v1",
    "binding_generation": 1,
    "confirmation_token": null,
    "expires_at": null
  }
}
```

### Optional Setup Features

Setup additionally accepts `--usage-collection on|off`,
`--verification-runner on|off`, `--effort-advisory on|off`,
`--viewer-reload on|off`, and `--review-wait on|off`, only for explicit user selections. Omission keeps
prior configuration/choices; OFF is remembered across later setup. New Viewer
reload uses 30 seconds and retains any existing interval. Runner ON with an
empty Plan does not run verification; Task entries are authored separately.
Effort preserves existing thresholds and creates none when enabling a new
profile. Usage OFF stops subsequent collection without erasing history or
changing host trust. Fresh setup creates no hooks until ON; legacy definitions
remain supported. Preview writes nothing and same-choice replay is idempotent.

Review wait saves only local wait policy, including compatible prepare/start;
ON does not establish host configuration, permission or connection. For explicit
configuration use [Setup and connection](review_wait.md#setup-and-connection);
ordinary use follows [normal waiting](review_wait.md#normal-wait).
OFF preserves inspection and cleanup.

`optional_features` has `features` (five entries named `usage_collection`,
`verification_runner`, `effort_advisory`, `viewer_reload`, `review_wait`), `offer` (unresolved
names), and `viewer_default_interval_seconds=30`. Each feature has `requested`
(Boolean/null), `selection=on|off|undecided|unknown`,
`selection_source=saved|existing|none|unknown`, `effective=on|off|unknown`,
`status=not_attempted|observed|preview|applied|unchanged|unavailable`, and
`error` (null, `feature_choices_unavailable`, `feature_configuration_unavailable`).
Selection is saved intent, not effective success; direct config edits may
differ. Preview shows the current effective setting, not proposed ON/OFF.
Partial failure preserves core `ok` and unrelated choices. Report unavailable
features honestly; do not rerun completed Task/core operations. User answers
authorize those feature writes without a second approval; no normal-loop call
or completion requirement is added. Host trust and actual collection remain
unknown even when usage configuration is ON.

### Setup Outcomes And Recovery

Successful `status` is `setup_preview`, `relocation_preview`,
`setup_complete`, or `already_setup`. Write-list values are limited to
`database_restore`, `legacy_state_publish`, `database_initialize`,
`migration_backup`, `database_migrate`, `maintenance_configure`,
`project_binding_update`, `evidence_projection_publish`, `viewer_publish`, `legacy_state_cleanup`,
`state_layout_retire`, `state_layout_publish`, and `state_layout_activate` in
execution order. `viewer_status` is `not_present`, `current`, `published`, or
`repair_required`.

Separation reports its three durable stages, not private candidate writes as
live DB changes. After activation, ordinary setup uses its existing stage
labels. A sealed cutover is resumable at the publication boundaries, never
permission to use an unactivated candidate. Nonempty preparation interrupted
before its digests were sealed is preserved and reports `setup_incomplete`;
retry does not automatically delete, rebuild or accept those uncertain bytes.

`evidence_status` uses the same four values. Evidence publication follows
maintenance/binding and precedes Viewer. Ordinary setup preview lists it
without writing; separation performs it privately before its three public
durable stages.

`usage` reports separate numerical-store setup, not Task-cost availability.
Its status is `not_attempted`, `pending_core_setup` (preview before core
binding is admitted), `not_present`, `initialized`, `migration_required`,
`migrated`, `current`, or `unavailable`. Writes are `usage_initialize` and
`usage_migrate` (exact schema 1, 2, 3 or 4 to 5); preview has no completed write.
`schema_to` is 5 and `error` is null or fixed `usage_unavailable`. Numerical
failure never undoes successful core setup or blocks ordinary Task work;
do not repeat Task writes to recover usage. This adds no normal-loop action,
session registration step or automatic collection.

`usage_hooks` separately reports project `.codex/hooks.json` preparation:
`not_attempted` after core/usage failure, `preparation_required` in preview,
`prepared` after a write, `current` for unchanged definitions, `not_requested`
for an unselected absent definition, `disabled` for saved/requested OFF, or `unavailable`.
Its only write label is `usage_hooks_prepare`; preview completes none. `trust`
is always `unknown`. `next_action` is `review_and_trust_hooks` for prepared,
current or planned definitions, `review_hook_configuration` for unavailable,
otherwise null. `error` is null or `usage_hooks_unavailable`. Hook failure does
not undo core/usage success; prepared does not prove trust or collection.
Follow the [user trust guidance](usage_hooks.md#installation-and-trust).

`relocation` is always present with exactly the six shown keys. `required`
is boolean. `source_layout` is null, `legacy_projects_v1`, or
`fixed_current_v1`; `identity_scheme` is null, `legacy_path_v1`, or `uuid_v1`;
`binding_generation` is a positive integer or null. The confirmation token
and expiry are non-null only in a successful relocation preview. Public output
does not expose a stored or current absolute binding path.

Preview reports current durable state, not planned state:
`completed_writes=[]`, and a fresh preview keeps
`maintenance_enabled=false`. A healthy replay has empty write lists. Every
error has `status=null`; preflight/policy failures use empty write lists and
null observed values except `schema_to=27`. A later-stage failure reports only
the durable ordered prefix. Inspect `data.completed_writes` before retrying;
`setup_incomplete` permits a retry that recomputes from durable state rather
than repeating an assumed failed stage; it does not guarantee automatic repair
of unsealed preparation. Repeated failure does not authorize deletion or an
unbounded retry loop; follow the existing reconciliation guidance.

<a id="doctor"></a>

## `doctor`

Doctor is the sole diagnostic and is inherently read-only:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py doctor --json
```

It never initializes, migrates, repairs, backs up, renders, locks an artifact,
runs project tests, or changes target state. For a Git-candidate target, only
its single bounded effective-ignore preflight may inspect Git. It is not a
prerequisite for setup or normal task work.

On a recognized binding mismatch, doctor remains successful and reports
`project_state.code="relocation_required"`, warning
`project_relocation_required`, `setup_eligible=true`, and the fixed
`suggested_action="continue"`. It never returns a relocation token or performs
the preview/confirmation step.

`data` has exactly `suggested_action`, `setup_eligible`, and `components`.
`suggested_action` is always `continue`. Component keys are exactly
`package`, `project_state`, `task_summary`, `handoff_delivery`, and
`maintenance`.

A ready result has this structure:

```json
{
  "suggested_action": "continue",
  "setup_eligible": true,
  "components": {
    "package": {
      "package_name": "task-governance-tool",
      "package_version": "0.13.0",
      "release_origin": "github:VAiring/task-governance-tool",
      "manifest_version": 1,
      "status": "clean",
      "changed_core_count": 0,
      "changed_core_paths": [],
      "changed_core_paths_truncated": false,
      "unknown_reasons": [],
      "suggested_action": "continue"
    },
    "project_state": {
      "code": "ready",
      "schema_version": 27,
      "required_schema_version": 27
    },
    "task_summary": {
      "code": "ready",
      "active": 0,
      "blocked": 0,
      "done": 0,
      "next_actionable": 0,
      "paused": 0,
      "review_pending": 0
    },
    "handoff_delivery": {
      "code": "ready",
      "handoff_pending": 0,
      "adapter_enabled": false,
      "delivery_due": false
    },
    "maintenance": {
      "code": "enabled",
      "opted_in": true,
      "backup": {
        "code": "current",
        "due": false,
        "interval_minutes": 30,
        "generations": 3,
        "last_success_at": "2026-07-27T00:00:00Z",
        "last_outcome": {
          "code": "succeeded",
          "occurred_at": "2026-07-27T00:00:00Z"
        }
      },
      "evidence": {
        "code": "current",
        "due": false,
        "source_generation": 1,
        "published_generation": 1,
        "last_success_at": "2026-07-27T00:00:00Z",
        "last_outcome": {
          "code": "succeeded",
          "occurred_at": "2026-07-27T00:00:00Z"
        }
      },
      "viewer": {
        "code": "current",
        "due": false,
        "source_generation": 1,
        "rendered_generation": 1,
        "last_success_at": "2026-07-27T00:00:00Z",
        "last_outcome": {
          "code": "succeeded",
          "occurred_at": "2026-07-27T00:00:00Z"
        }
      }
    }
  }
}
```

Unavailable project-backed components are exactly `{"code":"unavailable"}`.
Not-yet-known maintenance values are `null`, not omitted. Maintenance outcome
codes are `none`, `succeeded`, `deferred`, or `failed`; readable maintenance
states remain successful advisory data and never create a stop.
The Evidence object has exactly `code`, `due`, `source_generation`,
`published_generation`, `last_success_at`, and `last_outcome`; doctor never
opens or repairs its JSON files.

Invalid stored Task data or its current Contract relationship makes
`project_state.code="unreadable"`, all other
project-backed components `{"code":"unavailable"}`,
`setup_eligible=false`, and returns exit 2 with the fixed
`project_state_unreadable` error. It exposes no rejected value and writes
nothing.

Package `modified` or `unknown` is an exit-0 warning and makes
`setup_eligible=false`. Missing or migratable state is an exit-0
`setup_required` or `migration_required` warning. Invalid layout/project,
unsupported runtime/journal, busy or unreadable state, project mismatch, and a
newer schema are exit-2 errors. No row exposes a local path, digest, file
content, exception, or raw state.

## Task Commands

All returned Task records, including compact and write results, carry
`ownership={state,owner_session_id,completion_session_id,execution_id,generation,
is_owner,is_completion_owner}`. State is `none|owned|completion_only|unknown`;
IDs are full or null. Unknown caller/owner yields null self flags; known none
yields false. The CLI identifies its caller automatically from `CODEX_THREAD_ID`;
no session input, extra read or manual ID comparison is needed. These values are
not credentials and flags are not saved to shared Viewer/Evidence. For held or
unknown work use [explicit recovery](task_workflow.md#pause-resume-and-block).

Invalid stored Task or current Contract data returns exit 2, code
`project_state_unreadable`, and message
`project state could not be read safely`. The command keeps its existing empty
data shape and emits no warning, partial Task projection, rejected content, or
write. Do not treat this as an empty selection or repair stored values manually.

<a id="task-add"></a>

### `task add`

Register one explicit task:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task add --repo <target-project> --title "Update docs" --kind optional --priority normal --json
```

Options are `--title`, `--description`, `--kind`, `--lane`, `--order`,
`--priority`, `--status`, `--blocked-reason`, `--review-tier`,
`--verification`, `--verification-not-required <reason>`, `--tags`, and the Contract group
`--contract-scope`, `--contract-acceptance`, `--contract-constraints`,
`--contract-authority-ref`, and `--contract-change-reason`.

Kinds are `sequential|optional`; priorities are
`low|normal|high|urgent`; review tiers are `0|1|2`. An initial Contract
requires both scope and acceptance and never gets inferred from missing input.
Success data contains `task`, `event`, and `context_preparation`, plus
`contract_write={"recorded":true,"revision":1}` when a Contract was recorded.
`data.task.task_id` identifies the registered Task; it need not be the selected work.

Initial `done` returns `initial_done_forbidden`; specifically,
`task add --status done` never stores a task or event. Initial `paused` returns
`initial_paused_forbidden`. Initial `review_pending` returns `invalid_status_transition`.
Initial `in_progress` acquires the caller's single active slot.
Initial `blocked` requires `--blocked-reason`.
Sequential adds preserve the same predecessor rule used for selection and
transitions.

Explicit `task add --verification` and `task edit --verification` are capped
at 1,000 characters, with privacy checked before length. A 1,001-character
value is rejected without a write. Omitting `--verification` from `task edit`
preserves the existing value; other edits do not require copying it.

Task JSON exposes `verification_requirement=unspecified|required|not_required`
and `verification_not_required_reason`. Nonempty verification means required;
empty verification alone is unspecified and cannot complete. An explicit
sanitized, nonempty reason (at most 1,000 characters) declares not-required.
An edit of either member clears the old counterpart; both nonempty inputs are
invalid. `--verification ""` resets to unspecified; omission preserves both.
Changing the declaration invalidates the current target and evidence. Old done
history remains valid, but reopening an old blank Task does not infer a waiver.

For multiple finalized groups, use [batch registration](#batch-task-registration)
instead of these single-Task flags. Both forms use the result below.

For single and batch success, `context_preparation` has exactly `status`,
`context`, and `errors`. `status=ready` carries the complete ordinary
[`task context`](#task-context) data with `errors=[]`; its component warnings
appear once in the outer warnings. Use `context.selected.task.task_id` for
selected work, not the registered ID by assumption. This replaces the immediate
separate context read, not any implementation permission or gate. Batch items
do not duplicate context. The read follows commit with ordinary validation and
selection; it is not atomic with registration or later maintenance.

`status=failed` has `context=null` and sanitized `errors`, with no partial read
or component warnings. Outer `ok=true` and exit zero still mean all registration
results committed. Address the read failure and retry only `task context`; do
not add the Tasks again. Registration failures do not prepare context. A lost
outer response remains uncertain: inspect actual state before any registration
retry using [registration recovery](task_workflow.md#partial-add-recovery).
No additional normal-path command or user choice is introduced.

### Batch Task Registration

Apply the shared status, verification, Contract and prepared-context conditions
in [`task add`](#task-add); the input and returned ID mapping below replace
its single-Task flags and `data.task` result. Registration alone grants no
implementation or Git permission. For failed/uncertain registration, follow
[Partial-Add Recovery](task_workflow.md#partial-add-recovery), not blind replay.

For a finalized multiple-Task set, use `task add --from-stdin --json` once with
this UTF-8 JSON shape (no BOM; at most 262,144 bytes and 1 through 64 items):

```json
{"version":1,"common":{"review_tier":1,"verification":"Focused document checks","contract":{"constraints":"Docs only","authority_ref":"docs/decision.md#approved"}},"tasks":[{"title":"Clarify setup","contract":{"scope":"Setup examples","acceptance":"Examples match setup help"}},{"title":"Clarify diagnosis","contract":{"scope":"Diagnosis examples","acceptance":"Examples are read-only"}}]}
```

Top-level keys are exactly `version`, `common`, and `tasks`. Each item requires
its own `title` and `contract` (null for revision zero, or required `scope` and
`acceptance`, optional `constraints` and `authority_ref`). Common Contract values
may contain only `constraints` and `authority_ref`; they never activate null.
Common/per-item Task fields are `description`, `kind`, `lane`, `lane_order`,
`priority`, `status`, `blocked_reason`, `review_tier`, `verification`,
`verification_not_required_reason`, and `tags`. An item supplying either
declaration member replaces the common pair; otherwise it inherits the pair.
Each effective review tier must be explicitly supplied; other omitted fields
use single-add defaults. Individual values override common ones, including empty
text. Integer fields are JSON integers (lane_order may be null); other scalars
are strings. All supplied values, even unused common values, retain existing
privacy/bounds checks. No unknown/duplicate keys or automatic scope inference.
Do not combine this mode with individual Task/Contract flags; read-only rejects
it without consuming stdin.

Success data contains `tasks=[{"input_index":0,"task":{},"event":{},"contract_write":{"recorded":true,"revision":1}}]`
and one `context_preparation`,
where Task/event are the existing projections and `contract_write` is absent
for null Contracts. The response maps every input in order; no per-Task follow-up
confirmation is required. Match `data.tasks[].input_index` to the input and use
that item's `task.task_id` for later operations. All entries commit together or all roll back;
handled batch input/storage errors return `tasks=[]`. Parse/read-only errors
retain their ordinary envelope. Correct and resubmit only after confirmed
rollback. A lost response requires inspecting existing Tasks before any retry;
never blindly replay or remove successes. This is not an idempotent importer.
Windows PowerShell producers must send UTF-8 without BOM, not locale-encoded
text. This option creates no input file, adapter, extra normal-loop call, or
authority beyond explicit registration.

<a id="task-list"></a>

### `task list`

Return compact filtered rows:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task list --repo <target-project> --status ready --limit 20 --json
```

Filters are `--status`, `--kind`, `--lane`, `--priority`, `--tag`,
`--limit`, and `--include-done`. Data keys are `tasks`, `count`, and `limit`.

<a id="task-next"></a>

### `task next`

Return ready candidates:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task next --repo <target-project> --limit 5 --compact --json
```

Filters are `--kind`, `--lane`, `--priority`, and `--limit` (default 5).
Only ready optional tasks and ready sequential tasks whose earlier same-lane
predecessors are done/cancelled are actionable.

Default data keys are `tasks`, `count`, `limit`, and `selection_rules`.
Compact data keys are exactly `tasks`, `total_matching`, `returned_count`,
`limit`, and `truncated`. A compact task has only `task_id`, `title`, `kind`,
`lane`, `lane_order`, `priority`, `review_tier`, `tags`, and
`suggested_next_action`, and `ownership`.

Complete compact JSON stdout is capped at 16,384 UTF-8 bytes. Truncation keeps
only a complete-row prefix in existing order. `--compact` requires `--json`;
otherwise the command returns `invalid_option_combination`.

Existing paused work adds warning `paused_tasks_present` without changing
candidates, exit status, or data. It is an advisory recall hint only.

<a id="task-current"></a>

### `task current`

Rediscover started or held work:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task current --repo <target-project> --compact --json
python .agents/skills/task-governance-tool/scripts/taskgov.py task current --repo <target-project> --status paused --json
```

Default statuses are `in_progress`, `review_pending`, `paused`, and `blocked`.
`--status` accepts one of those values. `--limit` defaults to 20 and caps at
100.

Default data keys are `tasks`, `count`, `limit`, and `statuses`; each row
contains the normal task projection, latest event, latest checkpoint, and
deterministic suggested action. Compact data keys are exactly `tasks`,
`total_matching`, `returned_count`, `limit`, `statuses`, and `truncated`.
Compact rows contain only:

```text
task_id, title, status, kind, lane, lane_order, priority, review_tier,
blocked_reason, pause_reason, latest_event, suggested_next_action, ownership
```

A compact latest event contains only `event_type`, `summary`, `created_at`,
and `summary_truncated`, with summary capped at 256 UTF-8 bytes. Complete
compact JSON stdout is capped at 24,576 bytes and omits Contract/checkpoint
content. `--compact` requires `--json`. Follow selection with `task show`.

<a id="task-context"></a>

### `task context`

Use one fixed read for ordinary Task start or resume:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task context --repo <target-project> --json
```

Only common options are accepted; there is no Task ID, filter, display-mode,
or automatic start option. The tool resumes the caller's active/review-pending work first,
otherwise selects ready work using its existing order. Selection precedes
display omission, so a Task absent from the compact lists may still be selected.
Use the returned selection; do not reconstruct or re-rank it.

Success data is exactly `selection`, `current`, `next`, and `selected`.
`selection` is `current`, `next`, or `none`. `current` is compact-current data;
`next` is compact-next data only when fallback ran, otherwise null. `selected`
is the complete [normal Task detail](#task-show), or null when no candidate exists.
It includes complete Contract, latest checkpoint, current blockers/gates, and
`effort_advisory_enabled`. Use that detail directly without another
current/next/show call. Owner eligibility is applied before the bounded SQL limit;
explicit current/show still inspect held and other-owner Tasks. Missing identity
adds `session_identity_required` and permits only ready fallback inspection.
Successful component warnings are retained once.

For `selection=current|next`, use `data.selected.task.task_id` as `<task-id>` in
the next Task-specific command. `selection=next` does not start the Task; start
it with `task edit <task-id> --status in_progress --json`. No ID is appended to
the `task context` call itself.

Any read failure returns its sanitized error with no partial working context;
it never skips the failure to select another Task. `ok=true` with
`selection=none` is successful absence, distinct from `ok=false`. Text gives
the selection, existing selected-Task detail, caller-owned current work, and warnings.
No state, evidence, or gate is changed. The public operation reuses the
existing selection and gate rules.

<a id="task-effort"></a>

### `task effort`

Read one optional informational observation. At the verification/review boundary,
the normal flow calls this only when
`task context` returned `data.selected.effort_advisory_enabled=true`; take
`<task-id>` from that response's `data.selected.task.task_id`:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task effort --repo <target-project> <task-id> --read-only --json
```

`task effort` reads the package-local versioned profile without creating or
changing it. Only explicit [Setup selection](#optional-setup-features) may
create it or toggle `enabled`, preserving existing thresholds; a newly enabled
profile has no thresholds until explicitly configured.
Its only metrics are changed Git files, lines, modules, current Contract
revision count, and recorded source-task handoff count. An absent, disabled, or
invalid profile returns an enabled-false bounded projection and performs no
Git work.

Enabled data contains `task_id`, `enabled`, `profile`, `measurements`,
`thresholds`, `exceeded`, `basis`, `observation`, `coverage`, `attribution`,
`unknown_reasons`, `warning_key`, and `suggested_action`.
For a valid enabled profile, `suggested_action` is `reconcile_scope` exactly
when `exceeded` is nonempty, including when attribution is also unknown;
otherwise it is `continue`. The threshold warning uses the same action, and
one observation emits at most one such warning. Threshold or attribution
results never change status, acceptance, review, completion, or target state.
The command emits no paths, stderr, diffs, raw logs, or Git writes.

<a id="task-show"></a>

### `task show`

Read one specified Task's fixed normal working context. Use `<task-id>` from
`data.selected.task.task_id` in `task context`, or a Task row returned by an
explicit `task list/current/next` inspection:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

For explicit historical investigation only, use [the audit mode](#task-audit-detail).
`task context.selected` always uses normal detail.

Use the returned working context directly. These fields have the same meaning
under `task context`'s `data.selected`; no follow-up read is needed:

| Field under `data` | Use |
|---|---|
| `task` | Task ID, status, verification expectation, review tier, current target/generation, and typed completion evidence. |
| `contract` | Complete current scope, acceptance, constraints, authority reference, and revision. Revision `0` means no activated Contract, not a failed read; use the existing [Contract procedure](task_workflow.md#task-contract) when applicable. |
| `latest_checkpoint` | The latest [continuation checkpoint](#task-checkpoint), or null when none exists. |
| `events` | Latest activity and retained notes/transition reasons. Do not discard continuation information merely because it is old, from an earlier generation, or followed by a newer checkpoint. |
| `review_evidence` | Current gate/counts, `current_receipts`, and `current_findings`. |
| `verification_evidence` | Current gate/counts, `current_receipt`, and tool-generated `current_verification_subject`. |
| `handoff_summary` | Counts for `pending_handoff`, `handed_off`, and `handoff_withdrawn_by_user`; these do not expand Task scope. |
| `completion_history` | `total` and `legacy_history_incomplete`, not current completion evidence. |
| `effort_advisory_enabled` | Whether the normal loop uses the optional Effort observation. Invalid configuration returns false with a continuation warning. |
| `usage` | Last captured registered shared-work summary and cycle links, or explicit unknown. Numerical gaps never change Task gates. Optional [collection coverage and recovery](usage_hooks.md#coverage-and-recovery) explain gaps without an extra normal-loop command. |
| `suggested_next_action` | A next-action hint, not authority or proof of completion. In review-pending detail, satisfied required verification and only missing independent PASS reviews yield a remaining count and the existing result-input route; otherwise the status-based hint remains. Obtain any unperformed reviews before registering actual returned results. |

Use `review_evidence.gate.satisfied` and its required/qualifying independent
pass counts, not the number of recent reviews, to read review readiness.
`current_receipts` match the complete current target and generation.
`current_findings` includes all open Findings, including older generations and
low severity, plus resolved high/medium Findings awaiting fresh review.
Their `blocking_reason` is `unresolved`, `fresh_review_required`, or null when
nonblocking. Keep these distinctions; see [Finding repair](task_workflow.md#repair-findings)
when blocked. A newer generation never clears an unresolved Finding by itself.

Use `verification_evidence.gate.required`, `satisfied`, and `blocking_code`
for verification readiness. `current_receipt` is null or the exact-current
Receipt's ID, result, duration, coverage, and recording time. Its absence or
zero Receipt counts do not imply failure: a qualifying Runner pass needs no
Receipt. Do not reconstruct the gate from internal markers or historical rows.
For a returned blocking code or read failure, use [Task Read Conditions](#task-read-conditions).

#### Task Read Conditions

For `review_target_required`, prepare the exact material and
[set its target](task_workflow.md#set-the-review-target). `evidence_basis_stale`
means the retained basis cannot authorize new evidence or completion; use a
fresh target, never attach an earlier run to it. For
`verification_receipt_required` or `verification_receipt_blocking`, follow the
[Verification Receipt conditions](#verification-receipt-rules), including fresh-target
requirements after failed, timed-out, or partial verification. A blocked Runner
cannot be overridden by a manual Receipt. For new target operations, use their
returned route as specified by the [normal loop](task_workflow.md#bounded-operating-loop).

No read changes evidence or gates. Both normal and audit reads validate hidden
history too; omitting its display is not permission to ignore invalid state.
On `ok=false`, use the sanitized error rather than empty/null data as a Task or
gate result. `project_state_unreadable`, `invalid_verification_evidence`, or
`completion_history_inconsistent` can indicate invalid retained content;
follow [stored-state errors](#task-review-and-handoff-errors) and, for uncertain
outcomes, [failure diagnosis](#uncertain-operation-outcomes), not an audit bypass or
another candidate. Neither mode exposes raw reviews or private reasoning.

<a id="task-audit-detail"></a>

### Historical Investigation (`--audit`)

Use this mode only for explicit Receipt/provenance or completion-history
investigation. It is bounded detail, not an exhaustive export or an additional
normal-loop read. Use the same `<task-id>` as the [normal show operation](#task-show)
and apply the shared [Task Read Conditions](#task-read-conditions):

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task show --repo <target-project> <task-id> --audit --json
```

Audit does not change saved Evidence, validation, or current gates. Its
`review_evidence` retains the target, full gate/counts, blocking Findings, and
bounded recent Receipt/provenance and Finding rows; events retain the newest ten.
Audit `completion_history` retains exactly:

```text
total, returned_count, truncated, legacy_history_incomplete, cycles
```

Cycles are a newest-first complete-row prefix with at most 10 rows, 8,192
UTF-8 bytes per row, and 32,768 UTF-8 bytes for the complete five-key
component. Both limits use compact sorted-key UTF-8 JSON, and each candidate is
measured in its final wrapper with the actual counts and flags. The first
non-fitting row stops collection; older rows are not substituted. A cycle has
exactly:

```text
completion_cycle_id, saved_cycle_ordinal, origin, completeness, completed_at,
contract_revision, review_tier, verification_expectation,
verification_attestation, completion_evidence, review_target, gate_basis
```

Nested keys are exact: `completion_evidence` contains `kind`, `revision`,
`reason`, `external_revision_approved`, `completion_commit_required`, and
`completion_commit_hash`; `review_target` contains `kind`, `value`,
`base_revision`, and `generation`; `gate_basis` contains `version`, `kind`,
`required_independent_passes`, `qualifying_independent_passes`,
`changes_requested`, `open_high`, `open_medium`, `fresh_review_required`, and
`qualifying_receipt_ids`.

Gate-basis version 0 emits null counts and `qualifying_receipt_ids=[]`.
Version 1 emits integer counts and one or two slot-ordered receipt-ID strings.
`verification_attestation` is only `true` or `null`. Internal event links,
review bodies, and raw verification content never appear. Saved cycles are
audit-only and never satisfy the current verification, review, or completion
gate. Audit text reports bounded counts and the latest cycle's non-content
fields; normal text omits saved-cycle detail.

Stored public completion-evidence and review-target text is strictly
privacy-revalidated before projection. Completion history has no
[legacy counter compatibility exception](#legacy-stored-counter-compatibility); rejected or corrupt stored text returns
`completion_history_inconsistent` without exposing the value. Audit `task show`
and Viewer use the same bounded history projection; normal show validates it
before omitting its detail.

Audit `verification_evidence` has exactly `expectation`, `contract_revision`,
`source_revision`, `current_verification_subject`, `gate`, `counts`, and
`recent_receipts`. `source_revision`
is null without a target; otherwise it contains exactly `kind`, `value`,
nullable `base_revision`, and positive `generation`. Gate has exactly
`required`, `satisfied`, nullable `blocking_code`, and nullable
`qualifying_receipt_id`. Counts has exactly `receipts_total`,
`receipts_exact_current`, `qualifying_exact_current`, and
`blocking_exact_current`. At most ten newest-first rows use the fixed public
Receipt fields and never expose the internal expectation digest. The same gate
and subject types/null rules apply in normal mode. Text does not summarize
Receipt state. Invalid stored Receipt evidence returns the
sanitized `invalid_verification_evidence` failure.
For the returned subject or provenance compatibility fields, use
[Receipt output detail](#receipt-output-for-integration-or-audit).

<a id="task-checkpoint"></a>

### `task checkpoint`

Record one optional typed continuation boundary for
`data.selected.task.task_id` from `task context`:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task checkpoint --repo <target-project> <task-id> --summary "Completed slice" --next-action "Run verification" --unresolved-risk "Review remains" --json
```

`--summary` and `--next-action` are required. `--unresolved-risk` may repeat at
most eight times. UTF-8 limits are 1,024 bytes for summary, 1,024 for next
action, 512 per risk, 4,096 for all risks, and 6,144 for the complete caller
payload.

Data keys are exactly `checkpoint`, `created`, `replayed`, and `event`.
Checkpoint keys are exactly:

```text
checkpoint_id, task_id, contract_revision, summary, next_action,
unresolved_risks, created_at
```

A new append returns `created=true`, `replayed=false`, and an event containing
only `task_event_id`, `event_type="checkpoint_recorded"`, and `created_at`.
Exact replay of the latest checkpoint for the same Contract revision returns
`created=false`, `replayed=true`, `event=null`, and writes nothing.

Checkpoint use is never automatic or required. It does not change task status,
selection, gates, or `tasks.updated_at`. Done tasks remain immutable.
Only an already-stored checkpoint summary may use the bounded legacy
numeric `dispatch_authorization` JSON reader. It returns the original summary
unchanged and authorizes no write or external operation. New checkpoint input
and every other checkpoint field use strict normal validation.

<a id="task-edit"></a>

### `task edit`

Update task state or metadata for `data.selected.task.task_id` from
`task context` (unlike `task context`, this command requires the ID):

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for user decision" --json
```

Editable arguments are:

```text
--title --description --kind --lane --order --priority --status
--blocked-reason --pause-reason --review-tier --verification --tags
--add-note --reopen-reason --review-tier-change-reason
--completion-evidence-kind --completion-revision
--completion-evidence-reason --external-revision-approved
--commit-not-required --verification-complete --review-complete
--contract-scope --contract-acceptance --contract-constraints
--contract-authority-ref --contract-change-reason
--runner-plan-action replace|rebind|detach|disable
```

Success data contains `task`, `changed_fields`, and `event`, plus
`contract_write` for Contract operations and pending `usage` for successful done.

Task Contract activation is allowed only on an exact revision-zero
`ready|blocked -> in_progress` transition. Later semantic revisions are
Contract-only, require explicit later authority and a reason, invalidate
current completion/review eligibility, and use immutable successive revisions.
Canonically unchanged input is a write-free replay.
Omitted later constraints retain the byte-identical, already-validated prior
value, including [bounded legacy counter forms](#legacy-stored-counter-compatibility); explicit constraints use strict
normal validation. Carry-forward does not accept caller-supplied legacy
vocabulary or grant authority.

Only `in_progress|review_pending -> paused` is valid and requires
`--pause-reason`. Resume explicitly to `in_progress`. Sequential transitions
to active or review-pending use the same predecessor rule as
`task next`.

Lower a review tier only before any target has been set and provide
`--review-tier-change-reason`. A done task rejects every write except an
isolated `--status in_progress --reopen-reason <summary>` transition. Reopen
preserves saved completion cycles as audit history, clears current
completion/review eligibility, and requires fresh gates.

For completion, use [task complete](#task-complete) with its typed evidence,
confirmations, and current gates. Read [Runner Plan Actions](#runner-plan-actions) only for an explicit Runner Plan action or
`runner_plan_action_required`, not for ordinary metadata/state edits.

<a id="runner-plan-actions"></a>

### Runner Plan Actions

Use only for an explicit Plan action or `runner_plan_action_required`.
Use the Task ID from [task edit](#task-edit); its shared Task/Contract edit
conditions also apply to combined changes. The [example and OS limits](#runner-plan-example-and-os-limits) are part
of this authoring guidance, not ordinary Task-edit prerequisites.

An action-bearing success adds
exactly `runner_plan_update={"action":<action>,"status":<status>}`, where status
is `updated|unchanged|unconfirmed`. Without an action, JSON, text, warnings, and
errors retain the existing shape.

`--runner-plan-action` is an explicit opt-in to author only the canonical
ignored physical package file `config/verification-runner.json`; it adds no
command leaf and never launches the Runner or sets a review target. The actions
are closed:

| Action | Standard input | Plan effect |
|---|---|---|
| `replace` | one document | Upsert the addressed Task entry from a strict versioned `RunnerPlanDraft`; this is the only initial-set action. |
| `rebind` | not read | Require one addressed entry, preserve its steps and position, and bind it to the exact current or future Task basis. |
| `detach` | not read | Remove every addressed Task entry while preserving all unrelated entries and order. |
| `disable` | not read | Set only global `trusted_local=false`; preserve Plan ID, entries, and order. |

The `replace` stdin document contains exactly `version=1|2` and `steps`, is capped
at 65,536 UTF-8 bytes by one read of at most 65,537 bytes, and contains one
through 16 exact Step objects matching that version. V1 retains required
`memory_mib` and `process_limit`; v2 replaces them with `windows_limits`, either
the object containing both bounded integers or null. These limits apply only
to Windows; Windows requires them before launch. An explicit v2 replace
upgrades a v1 Plan, preserving unrelated entry values and bases; other actions
preserve the version and no action downgrades v2. Task ID, Contract revision,
verification digests, criterion digest, and `coverage=full` are derived by the
tool. No action discovers commands, infers coverage, changes setup, or
re-enables a disabled Plan.

An action may be Plan-only or accompany one actual Contract-revision or
verification-expectation change. A Plan-only success returns the current Task,
empty `changed_fields`, null `event`, no Task/Contract write, and no maintenance.
Other metadata may accompany an action only with an actual basis change.
Done, completion-evidence, reopen, verification-complete, and review-complete
modes are incompatible with every Plan action. When an enabled Plan has one
exact-current addressed entry, an actionless edit that would change its basis
fails with `runner_plan_action_required`; an absent, disabled, unreadable,
malformed, ambiguous, stale, or no-entry Plan does not block the ordinary Task
edit.

For a combined edit, the Task transaction commits and closes before Plan source
confirmation or publication. A later Plan failure never rolls the Task back:
the command returns `ok=true`, `status=unconfirmed`, and the first warning is
exactly `task_applied_runner_plan_unconfirmed` with message `Task update
completed but Runner Plan disposition is unconfirmed; apply an explicit Plan
action before relying on Runner execution`. Existing maintenance warnings
follow it. The caller must complete one explicit Plan-only repair before relying
on Runner execution. Config-only and pre-commit failures remain ordinary failed
Task-edit envelopes with no maintenance. Text success appends exactly `Runner
Plan: <action> <status>` after existing Task/Contract lines.

The additional fixed errors are `runner_plan_action_required` and
`runner_plan_entry_required` at exit 1; unsafe/malformed/ambiguous Plan source
errors and config-only `runner_plan_changed|runner_plan_update_failed` use exit
2. Draft privacy rejection remains `privacy_rejected` at exit 1. Error output
never contains draft bytes, Plan bytes, argv, paths, or publisher detail.

#### Runner Plan Example And OS Limits

For an approved existing verification command, first use the conditional
[Runner application guidance](runner_application.md#decide-whether-the-approved-checks-fit) to assess equivalent target
entrypoints, initial/per-Task preparation, updates and the returned result route.
Its unittest example is not a blanket conversion of arbitrary checks.

After ordinary physical installation, setup, and ignore protection, this
optional v2 draft works on Windows, Linux, and macOS for a nonterminal Task
with a current Contract and verification criterion. Adapt the entrypoint and
limits to the approved verification; the script and its required files must
exist in the exact Git target. Save this JSON as `runner-plan-draft.json`:

```json
{
  "version": 2,
  "steps": [
    {
      "step_id": "focused",
      "mode": "script",
      "entrypoint": "tests/test_focused.py",
      "argv": [],
      "cwd": ".",
      "timeout_seconds": 60,
      "cpu_seconds": 60,
      "windows_limits": {"memory_mib": 256, "process_limit": 4},
      "output_byte_limit": 1048576
    }
  ]
}
```

From the target-project root, explicitly publish the addressed Task's entry:

```powershell
Get-Content -Raw -Encoding utf8 .\runner-plan-draft.json |
  python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --runner-plan-action replace --json
```

On Linux/macOS, submit the same file:

```sh
python3 .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --runner-plan-action replace --json < runner-plan-draft.json
```

The first absent-file `replace` is the explicit trusted-local opt-in and creates
the canonical ignored Plan with `trusted_local=true`. It neither launches the
Runner nor sets a target. Execution remains part of the existing
`review target set` operation; this optional authoring step adds nothing to the
normal Task loop and does not discover commands or infer verification coverage.

Windows retains Job-based aggregate user CPU, memory, and simultaneous-process
limits. Linux/macOS instead enforce per-process CPU and wall timeout for the
whole execution, then stop and clean up the ordinary managed process group;
intentional daemon escape is outside the guarantee. Memory and simultaneous-
process limits are unsupported there and do not require future implementation.
The Windows pair above is ignored on Linux/macOS; `windows_limits=null` is also
valid there but prevents launch on Windows. Null is not an applied or unlimited
limit. POSIX memory and cumulative-process measurements are unmeasured (null,
not zero). Unavailable auxiliary measurements alone do not prevent PASS, while
execution failure or uncertain managed-process cleanup still blocks completion.

The Plan cannot select another interpreter or use PATH lookup. Arguments
remain literal, no shell is used for Runner execution,
and verification runs only in private exact Git material with no copy-back or
raw-output retention. These are trusted-code reliability guarantees, not
hostile-code or network isolation.

<a id="task-complete"></a>

### `task complete`

Complete the selected Task after its current gates pass. `<task-id>` is
`data.selected.task.task_id` from `task context`; `<hash>` is the exact
completion commit created under the project's separately authorized Git
workflow, not an ID or fingerprint from a Task response:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --completion-evidence-kind git_commit --completion-revision <hash> --json
```

Only when an explicit no-write readiness preview is useful, run the same
proposal with `--check --read-only`; it is not a normal-loop prerequisite:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --completion-evidence-kind git_commit --completion-revision <hash> --check --read-only --json
```

The thin command accepts only task ID, `--verification-complete`,
`--review-complete`, and one evidence form using
`--completion-evidence-kind`, `--completion-revision`,
`--completion-evidence-reason`, `--external-revision-approved`, or
`--commit-not-required`. Supported evidence kinds are `git_commit`,
`external_revision`, and `commit_not_required`.

Check data keys are exactly:

```text
task_id, ready, status, blocking_codes, contract_revision,
review_target_generation, completion_evidence_kind, suggested_action
```

Complete check JSON is capped at 8,192 UTF-8 bytes. `blocking_codes` is empty
or contains the first write-path blocking code. A changed basis during Git
observation returns `completion_check_stale`. A successful check stores no
token and never authorizes the later write.

Write success emits `command=task.complete` and data keys `task`,
`changed_fields`, `event`, and `usage={status:pending,coverage:registered_only}`.
Numerical collection/append is separate; unknown or missing usage is not a
completion failure or a reason to repeat completion.
Both check and write require a satisfying current
verification basis: an explicit waiver reason with trimmed-empty verification
on marker `0`, an exact-current
`pass/full` Verification Receipt for nonempty verification on marker `0` or the
exact closed no-launch `m21_fallback`, or an exact-current qualifying
complete-plan Runner pass with no Verification Receipt. A pending, stale, or
cleanup-only Runner basis fails `evidence_basis_stale`; every other
exact-current structurally valid terminal Runner result fails
`verification_receipt_blocking`.
Unspecified verification fails `verification_requirement_unspecified`, even
when acceptance mentions tests or `--verification-complete` is supplied.
They also require a current matching review target, qualifying review receipts,
no current changes-requested receipt, no unresolved high/medium finding, valid
typed completion evidence, sequential readiness, and exact Git/snapshot binding
when applicable.

Git evidence resolves to a canonical full commit ID. External evidence requires
an explicit reason and approval. `commit_not_required` requires a matching
`diff_fingerprint` target. This command does not create commits or mutate Git;
the separate [integrated helper](#integrated-review-finalization) owns the sole
fixed-target local commit exception.

## Verification Receipt

Use this manual form after the governed verification has run, under the
[shared Receipt rules](#verification-receipt-rules) for Task/generation sources,
eligibility, coverage, results and recovery:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py verification receipt add --repo <target-project> <task-id> --result pass --duration-ms <milliseconds> --scope-coverage full --expected-target-generation <generation> --json
```

All four result options are required. If the external verifier already emits
the fixed [structured result](#structured-verification-result), use that sibling
input route instead; do not transcribe its fields or combine the two forms.

## Verification Receipt Rules

These rules apply to both [manual input](#verification-receipt) and an already
[structured verifier result](#structured-verification-result). After setting the
exact review target, record one caller-attested aggregate result only after
running the governed verification outside taskgov on marker `0` or the
exact-current closed no-launch `m21_fallback`, for a Task with a verification
expectation that is nonempty after trimming.

The normal trigger is `data.verification_route=receipt_required` in the
successful `review target set` response. Take `<task-id>` from its
`data.task.task_id` and `<generation>` from `data.task.review_target_generation`.
Use the actual run's result, measured duration and confirmed coverage; a PASS
alone never establishes full coverage. `full` covers the entire exact current
Task verification expectation; partial runs do not combine into full coverage.
Other routes are handled in
[target selection](#review-target), without another `task show`.

`result` is `pass`, `fail`, or `timeout`;
`scope-coverage` is `full` or `partial`; duration is a nonnegative signed-
64-bit millisecond value; and expected generation is the positive generation
returned by target set. `--command-label` is not accepted and no caller subject
option replaces it. Taskgov binds the Receipt to the current Contract and exact
target and generates the subject, Receipt ID, and recording time; do not supply
or reconstruct them.

Receipt recording is allowed only for `in_progress` or `review_pending`,
requires verification that is nonempty after trimming and a current marker-`0`
target or exact-current closed no-launch `m21_fallback`, writes no Task event or
timestamp, and permits one immutable row per target generation. A marker-`2`
qualifying Runner pass, pending basis, stale basis, cleanup-only basis, or any
other structurally valid terminal result rejects Receipt add with
`evidence_basis_stale`. A retry after `fail`, `timeout`, or `partial` requires
an explicitly fresh target.
Taskgov does not execute a command for this manual verification branch, infer coverage,
authenticate the external runner, or retain a command body, arguments, exit
code, stdout/stderr, log, exception, environment, or result file.

A migrated capture-version-0 target is read-only lineage. Receipt add fails
`evidence_basis_stale` with `current evidence basis must be captured again`;
setting a fresh target creates capture version 1 and restores the write. The
old target is never upgraded in place.

A semantic `task edit --verification` after targeting clears target and old
completion evidence and advances generation. Without `--status`, a
review-pending Task returns to in-progress. Explicit in-progress, pause, block,
or cancellation follows normal transition rules; explicit review-pending/done
is rejected until a fresh target exists. Do not combine that semantic edit
with completion-evidence options; the command fails
`completion_evidence_conflict` rather than discarding them.

On registration success, `data.receipt` is the recorded result and its
`verification_receipt_id` is the generated ID. `ok=true` means registration,
not verification PASS. `data.review_preparation` has `status`, `packet`, and
`errors`. Pass/full automatically returns `ready` with the existing Packet;
fail/timeout/partial returns `blocked`, null Packet and the existing blocking
code without preparing. Post-commit preparation failure returns `failed`, null
Packet and sanitized errors, while preserving the Receipt and outer warnings.
Give a ready Packet directly to reviewers; do not prepare it again.

For preparation-only failure, retry only the read-only operation against the
same saved Receipt:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review prepare <task-id> --verification-receipt-id <recorded-id> --read-only --json
```

Both Packet reads validate that Receipt's exact current Task, Contract and full
target basis. Stale evidence cannot be rebound; it requires fresh targeting
and verification. If the response was lost or timed out, inspect recorded
state first to learn whether registration actually committed. Do not infer
rollback or blindly replay registration; the same generation accepts one
immutable Receipt only. No review, completion, or external verification is
automatically launched. Receiptless/Runner-pass routes do not register one.
Only for output integration or historical subject
inspection, read [Receipt output detail](#receipt-output-for-integration-or-audit).

`--read-only` and every failed call perform no business or maintenance write.
There is no Receipt list, show, import, export, Runner command, or Viewer panel.
For explicit Receipt inspection use [task show](#task-show), not a new command.

<a id="structured-verification-result"></a>

## Structured Verification Result

When an external verifier already emits this fixed format, send its stdout
unchanged to `verification receipt add <task-id> --from-stdin --json`. The
registration replaces the four individual result options; it does not add a
normal-loop call, launch verification, or require the LLM to read/convert the
result. Keep other verification tools on the existing manual attestation path.
Use the [shared Receipt rules](#verification-receipt-rules) for the Task ID and
generation source, field bounds, eligibility, binding, coverage and result/
uncertain-outcome recovery; the input mode changes none of them.

```json
{"version":1,"task_id":"tg_task_0123456789abcdef","result":"pass","duration_ms":1250,"scope_coverage":"full","expected_target_generation":1}
```

All six fields are required, with no additional keys. Input is one UTF-8 JSON
object, at most 4,096 bytes, without BOM or duplicate keys. Version is integer
1; duration and generation are exact JSON integers (not strings, floats or
Booleans) with the existing bounds. Other fields use existing string, enum
and privacy checks. Invalid shape/encoding is `invalid_verification_evidence`;
a different Task ID is `verification_basis_stale`. Mixed individual options
and `--from-stdin` are `invalid_option_combination`; read-only rejects before
consuming stdin. Nothing is recorded on rejection.

Supply Task ID and generation to the external verifier as the already-selected
run context before execution. `full` must be explicitly justified against the
entire Task verification expectation, never inferred from success. Subject,
Contract, criterion, source tuple, Receipt ID and recording time are still
derived by the existing writer, not repeated in the document. This remains a
caller attestation, not authenticated process evidence. Normal Receipt output,
one-per-generation, Runner eligibility, completion gates and maintenance are
unchanged; no raw document or stream is stored. A fresh run after failure,
timeout or partial coverage requires a fresh target first.

For shell-independent byte transport, a caller can connect an already approved
producer's binary stdout directly to the consumer's stdin, or pass those same
bytes using `subprocess.run(..., input=producer.stdout)`. Do not turn arbitrary
test output into this format by asking the LLM to transcribe it. No producer
installation, configuration or command execution is authorized by this input mode.

## Local Handoff Commands

<a id="handoff-record"></a>

### `handoff record`

Record one sanitized out-of-scope discovery. `<source-task-id>` is
`data.selected.task.task_id` from `task context`, the Task where it was found:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff record --repo <target-project> <source-task-id> --summary "Concise discovery" --rationale "Outside acceptance" --json
```

`--summary` is required and capped at 1,000 characters. `--rationale` is
optional and capped at 1,000. Optional `--occurrence-id` is capped at 200 and
must already come from a stable user/deterministic source.

Data keys are `handoff` and `local_record`. `local_record` contains exactly
`durable`, `created`, `replayed`, and `handoff_id`. Exact canonical replay
returns the same row with `created=false`, `replayed=true`, and no update.
Use `data.local_record.handoff_id` for a later explicit show or withdrawal.

The row is local `pending_handoff` only. It captures the source task and
current Contract revision but does not change task state, acceptance,
selection, events, timestamps, or completion. The command performs no Issue
adapter detection, delivery, semantic triage, or priority assignment.

Local busy persistence receives at most one fresh-transaction retry. Final
failure returns `handoff_not_persisted` with `handoff=null` and a
non-durable `local_record`.

<a id="handoff-list"></a>

### `handoff list`

List bounded records:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff list --repo <target-project> --json
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff list --repo <target-project> --state handed_off --state handoff_withdrawn_by_user --json
```

Options are repeatable `--state`, `--source-task-id`, and `--limit` (default
20, maximum 100). Default state is `pending_handoff`; rows sort oldest first.
Data keys are `handoffs`, `count`, exact `total_matching`, `limit`, and
`states`. Paging is not implemented.

<a id="handoff-show"></a>

### `handoff show`

Show one sanitized full record, using `data.local_record.handoff_id` from
recording or the selected `data.handoffs[].handoff_id` from listing:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff show --repo <target-project> <handoff-id> --json
```

Data contains only `handoff`. Stored content is privacy-revalidated before
emission.

<a id="handoff-withdraw"></a>

### `handoff withdraw`

Withdraw an undelivered pending row only on explicit user direction. Use its
`handoff_id` from the record/list/show response, not the source Task ID:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff withdraw --repo <target-project> <handoff-id> --reason "Handled outside Task Skill" --json
```

Reason is required and capped at 1,000 characters. A terminal, claimed, or
attempted row returns `handoff_not_withdrawable`. Withdrawal changes no source
task or task event.

## Review Commands

<a id="review-prepare"></a>

### `review prepare`

Prepare one bounded read-only reviewer packet. Qualifying Receipt registration
and Receiptless target setting already return this Packet, so this command is
for explicit preparation/retry, not the normal success loop. Use `<task-id>` from the
successful target-set response's `data.task.task_id`, after handling its
verification route:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review prepare --repo <target-project> <task-id> --read-only --json
```

Supported target kinds are `git_snapshot`, `git_commit`, `diff_fingerprint`,
and `external_revision`. After target-set preparation failure, copy its
`data.review_preparation.binding` into `--expected-binding <binding>`. This
checks the same saved Task/Contract/full-target context before and after
preparation. It accepts `sha256:` plus 64 lowercase hex digits and rejects
combination with `--verification-receipt-id` as `invalid_review_evidence`.
Drift is `review_packet_stale`, not permission to follow a newer target.
The retry is read-only: no target setting, generation advance, Runner launch,
Receipt registration, or maintenance. Never repeat target setting to recover
only a Packet.

If the target response was lost, inspect `task show <task-id> --audit --json`
first. Confirm its saved Task, Contract, and full target match the intended operation, then use
`data.review_evidence.preparation_binding` for a preparation-only retry. A null
binding means no target; uncertain or changed state requires resolution, not
blind replay or rebinding. This current comparison value is not gate evidence;
normal show/context has no added field or call.

Optional `--verification-receipt-id <id>` binds a
Packet-only retry to that exact-current pass/full Receipt in both reads;
mismatch uses `verification_basis_stale`, and non-pass/full uses
`verification_receipt_blocking`. The Receipt ID uses its existing fixed syntax.
Omitting it retains standalone preparation, which is not proof of verification
eligibility. The command takes no caller focus, reviewer, import,
receipt-file, or output-destination argument. It launches no reviewer, imports
no result, writes no packet, and changes neither local task state nor target
Git.

Success data keys are exactly:

```text
task, contract, review_target, changed_paths_available, changed_paths,
changed_paths_total, changed_paths_truncated, review_focus, required_output,
result_template, result_instructions, receipt_command
```

Changed paths are strict relative UTF-8 project paths, sorted bytewise, and
bounded to 100 rows, 240 UTF-8 bytes per row, and 16,384 aggregate path bytes.
The complete text or JSON stdout is capped at 32,768 bytes. A truncated path
list is not the complete review scope; inspect the exact material bound to the
returned target.

The Packet's `result_template` pre-fills only version 1, Task ID, Contract
revision and the complete target. Its one Receipt leaves reviewer, kind,
verdict, summary, findings and all ten provenance fields null: it is unfinished
and cannot be registered as-is. Use `result_instructions` for the existing
closed fields, codes, combinations and limits; complete the actual review
without a separate format lookup. No PASS, independence, review method,
model/Skill use or empty Findings array is inferred. The new fields count
toward the unchanged Packet cap and add no CLI call or review-method choice.
Exact artifacts and governing documents still require inspection.

`contract.authority_ref` carries the saved reference unchanged, possibly empty;
use it as a location hint only, not proof of approval or referent validity.
Do not infer paths from opaque identifiers. Shared-file transport also accepts
older complete Packets without that field, without filling it. No extra lookup
is needed solely to obtain a reference already present in the Packet.

The fixed `required_output` requests that completed version-1 result with one
reviewer's Receipt, severity-ordered Findings, and actual provenance. Bounded
summaries include exact file/line references, remaining risks and recommended
changes without raw review reasoning. `receipt_command` names `review result add`;
preparation itself neither records results nor launches a reviewer or model.

`review_focus` contains the four common fixed rows plus exactly one fixed
target-kind inspection row. `git_snapshot` binds inspection to the stage-0
index and stored base while excluding unstaged/untracked content. `git_commit`
binds it to the canonical commit and first-parent/empty-tree comparison.
`diff_fingerprint` and `external_revision` prohibit PASS unless exact material
is supplied with evidence binding it to the stored value. Selecting the row
adds no Git subprocess or caller/model choice.

Missing, changed, unsafe-path, and oversized cases return no partial packet:

- `review_target_missing`: `review target is required before preparing a review packet`
- `review_packet_stale`: `review context changed while preparing the packet`
- `review_packet_path_unsafe`: `review packet contains an unsafe project path`
- `review_packet_too_large`: `review packet exceeds the supported size`

### Review Evidence

<a id="review-target"></a>

#### Target Selection And Binding

Set or replace the target for `data.selected.task.task_id` from `task context`.
For pre-commit review, stage the intended files and use the snapshot form:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_snapshot --json
```

For review of an existing commit, use its exact revision instead:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_commit --revision <revision> --json
```

`git_commit`, `diff_fingerprint`, and `external_revision` require
`--revision`; `git_snapshot` rejects it. Every set advances the target
generation. Git commits are resolved read-only and stored canonically. A diff
fingerprint is `sha256:` plus 64 lowercase hexadecimal characters.

At schema v21 through v27, this same target-set operation may use the explicitly opted-in
trusted-local Runner route. It adds no argument or public Runner command. JSON
success data is `task`, `changed_fields`, `event`, `verification_route`,
`blocking_code`, and `review_preparation`; failure
data remains exactly the prior three-key empty shape. `verification_route` is
exactly `not_required`, `receipt_required`, `runner_pass`, or `blocked`.
`blocking_code` is null for the first three and is
`verification_receipt_blocking` for a stored nonqualifying Runner terminal.
The route describes the target and Runner result committed by this invocation;
use it directly, without another `task show`. Retain `data.task.task_id` and
`data.task.review_target_generation` for Receipt registration. `receipt_required`
means run the governed verification and record its aggregate result;
`not_required` and `runner_pass` automatically prepare a Packet after the save
and existing Runner work, without a Verification Receipt. `review_preparation`
has `status`, `binding`, `packet`, and `errors`: `ready` carries the Packet;
`failed` carries sanitized errors and retains the binding for read-only retry.
Preparation failure keeps outer `ok=true`, exit zero, and the saved target.
For preparation-only retry or a lost response, follow [bound Packet recovery](#review-prepare).
For `receipt_required`/`blocked`, status is `not_applicable`, binding and Packet
are null, errors empty, and no preparation is attempted. Text appends the
status and Packet or failure/retry detail. Warnings and one existing maintenance
pass remain outer-operation concerns. Use the Packet only when `ready`; the
Packet component retains its existing bounds. `blocked` requires its non-null code and stops this completion path;
a missing or inconsistent route/code pair also stops closed.

A target retained by schema-v18 migration with `capture_version=0` is read-only
lineage. Verification Receipt add, Review Receipt add, Review Finding add, and
both completion paths fail `evidence_basis_stale` with
`current evidence basis must be captured again`. Set a fresh target to create
capture version 1; no operation upgrades the old target in place. `review
prepare` and resolving an existing Finding remain allowed because they create
no new evidence source.

For `git_snapshot`, stage exactly intended files first. Capture observes
canonical HEAD and only the stage-0 index; unstaged and untracked files are
excluded. Completion requires a single-parent commit whose parent equals the
captured base and whose tree matches the fingerprint. A changed candidate
needs a new target and fresh receipts.

<a id="review-provenance"></a>

#### Receipt Input And Provenance

Use [Structured Review Results](#structured-review-results) when the actual
received results have its version-1 shape and matching Task/Contract/target;
combine their Receipt arrays without rewriting declarations. A single result
in that shape can use the same command; reviewer count is not the selector.
Use the existing single-Receipt form for a review received outside that format
when its actual verdict, required provenance and exact reviewed basis are
available. Record any actual Findings with [Finding creation](#finding-resolution).
Do not infer absent declarations, rebind an old result, drop Findings, or split
a rejected batch to evade validation. Obtain missing/corrected information
from the review source. An uncertain registration response requires inspecting
saved evidence before any retry; neither form makes committed replay idempotent.
Use the Task ID in the
actual Review Packet's `task.task_id`, and the reviewer's actual declaration
(Packet is `data.review_preparation.packet` after registration/target setting or `data` after
standalone preparation):

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <stable-reviewer-key> --kind independent --verdict pass --summary "No blocking findings" --reviewer-class human --model-state not_applicable --skill-state not_applicable --context-relation external_context --review-profile general --review-lens correctness --review-method review_packet_inspection --json
```

Kinds are `independent`, `self_review_fallback`, and `not_required`; verdicts
are `pass`, `changes_requested`, and `not_required`. One reviewer key may
record one receipt per target generation. Tier 2 normally requires two
distinct independent PASS receipts. Any current-generation
`changes_requested` receipt blocks completion. `--user-approved` is accepted
only where the governing review-tier fallback contract requires explicit user
approval; the normal independent path omits it.

For `independent` and `self_review_fallback`, the following provenance options
apply and no value is defaulted or inferred:

- conditionally required: `--reviewer-class`, `--model-state`,
  `--skill-state`, and `--context-relation`;
- optional only when the selected declared states require them:
  `--declared-model-id`, `--declared-skill-id`, and
  `--declared-skill-version`; and
- repeatable bounded sets: `--review-profile` (at most 4), `--review-lens` (at
  most 8), and `--review-method` (at most 8).

Every provenance option is forbidden for `not_required`. Duplicates are
invalid; empty profile/lens/method sets are valid; stored and public arrays use
the fixed enum order regardless of option order. Invalid type, enum, bound,
grammar, duplicate, or matrix combinations return
`invalid_review_evidence`; privacy rejection retains precedence.

The scalar enums are exactly:

```text
reviewer_class   human llm deterministic_tool hybrid unknown
model_state      declared not_applicable unknown
skill_state      declared not_applicable not_used unknown
context_relation same_context forked_context fresh_context external_context
                 not_applicable unknown
```

`context_relation` is a provenance declaration, not an independence verdict.
Do not infer reviewer independence or a `fresh_context` requirement from its
value alone; apply the project's actual review requirements.

The repeatable enum orders are exactly:

```text
review_profiles general authority_contract implementation verification
                migration_compatibility privacy_safety release_acceptance
review_lenses   correctness contract_compliance state_completion_integrity
                privacy target_safety verification_regression
                migration_compatibility maintainability accessibility
                performance release_integrity
method_codes    review_packet_inspection authority_cross_check diff_inspection
                source_inspection test_inspection
                verification_evidence_inspection artifact_inspection
                runtime_observation deterministic_rule_check
```

Human and deterministic-tool cases require model and Skill states
`not_applicable` and no declared IDs. LLM and hybrid cases require model
`declared` with an ID or `unknown` without one; their Skill state is `declared`
with ID and version, `not_used` without either, or `unknown` without either.
Reviewer class `unknown` requires both states `unknown` and no IDs. Declared
model/Skill IDs are 1-128 ASCII bytes matching
`[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}`; declared Skill version is 1-64 ASCII
bytes matching `[A-Za-z0-9][A-Za-z0-9._+-]{0,63}`.

Single-Receipt success returns its ID at `data.receipt.review_receipt_id`.
Taskgov generates the output provenance metadata; supply only the caller
declarations above. For interpreting returned metadata or legacy absence, use
[Receipt output detail](#receipt-output-for-integration-or-audit) only when that
integration or audit is needed. Provenance never proves identity, actual
model/Skill execution, competence, independence, diversity, quality, or truth.

The independent reviewer returns the verdict and findings. The trusted
parent/orchestrator records their concise sanitized receipt/finding rows as an
attestation. Taskgov deterministically evaluates qualifying PASS receipts and
changes-requested receipts only for the current review target and generation.
Any unresolved high or medium finding from any recorded generation of that
Task continues to block the gate. Distinct reviewer keys prove distinct stored
strings only; they do not prove distinct people, LLMs, machines, independent
processes, independence, authenticated provenance, or summary truth.

<a id="finding-resolution"></a>

#### Finding Creation And Resolution

Use `task.task_id` from the actual Packet object returned by registration/target setting or
standalone preparation.
`<receipt-id>` is `data.receipt.review_receipt_id` from single Receipt creation,
or the corresponding `data.receipts[].receipt.review_receipt_id` from grouped
results. `<finding-id>` is the selected `review_finding_id` returned by Finding
creation, grouped results, or `task show`'s `data.review_evidence.current_findings`:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review finding add --repo <target-project> <task-id> --receipt-id <receipt-id> --severity high --summary "Concise finding" --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review finding resolve --repo <target-project> <finding-id> --resolution "Concise resolution" --json
```

Severity is `high`, `medium`, or `low`. Open high/medium findings block
completion. After resolving a blocking finding and changing material, set a
new target and obtain fresh qualifying receipts.

<a id="structured-finding-resolutions"></a>

#### Structured Finding Resolutions

For several confirmed resolutions, send one UTF-8 JSON document to
`review finding resolve --from-stdin --json`:

```json
{"version":1,"task_id":"tg_task_0123456789abcdef","resolutions":[{"finding_ids":["tg_review_finding_0123456789abcdef"],"resolution":"Individual correction verified"},{"finding_ids":["tg_review_finding_123456789abcdef0","tg_review_finding_23456789abcdef01"],"resolution":"Shared correction verified for these two findings"}]}
```

Use `data.task.task_id` from `task show` and the selected
`data.review_evidence.current_findings[].review_finding_id` values, or the same
fields under `data.selected` in `task context`. Previously returned Finding
creation/result IDs may also be used; no extra confirmation read is required.

Every shown key is required, with no extras or duplicate JSON keys. UTF-8 must
have no BOM and fit 262,144 bytes. Version is exactly integer 1. Each group has
at least one ID; the total is 1 through 64, with no normalized duplicates.
IDs and reasons are strings using existing normalization/privacy and
128/1,000-character limits respectively. No target or Contract echo is needed:
existing older-generation and capture-v0 Findings remain resolvable.
Task and project ownership, done prohibition, and existing-resolution
immutability still apply. The LLM selects only fixes it has actually confirmed;
identical reasons are shared only by explicitly listing those IDs together.

Do not combine stdin mode with a positional ID or `--resolution`; it returns
`invalid_option_combination` before state access. Read-only never consumes
stdin. Success data is `{"findings":[{"finding":{},"event":{}}]}` with the
existing projections in flattened input order; each Finding ID identifies its
input. Batch failure returns `findings=[]`, except ordinary parse rejection.
All selected resolutions commit together or all roll back, with one maintenance
pass. No extra per-ID confirmation is needed after a successful response.
After confirmed rollback, correct and resubmit the explicit selection. For a
lost response, inspect existing state and preserve completed resolutions;
resubmit only IDs proven still open, never blindly replay or overwrite.
Malformed shape uses `invalid_review_evidence`; missing/foreign-project IDs use
`not_found`, different-Task IDs use `invalid_review_evidence`, and other existing
privacy/value/storage errors keep their codes. Unselected Findings and original
content are unchanged. A blocking resolution still needs a newer target and
fresh qualifying review; this mode never infers resolution from PASS.

### Structured Review Results

Register the reviewers' concise structured results together, using UTF-8 JSON
stdin with `review result add <task-id> --json`. There is no input-file argument
or output destination. Submit one complete version-1 document or an array of
complete documents, without retyping the original results. The normal shared-file
path uses [Submit Review Originals](#submit-review-originals) and its shared
path/validation and recovery rules, without LLM-built
transport code. For an existing caller that already owns its stdin transport,
the following direct PowerShell example remains compatible:

```powershell
$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
# Use the same caller-owned ignored paths assigned before the reviews.
$reviewResultPaths = @('.\review-a.json', '.\review-b.json')
$reviewResults = foreach ($reviewResultPath in $reviewResultPaths) {
  $original = Get-Content -LiteralPath $reviewResultPath -Raw -Encoding utf8 -ErrorAction Stop
  if ([string]::IsNullOrWhiteSpace($original)) { throw 'Review result is empty.' }
  $original
}
'[' + ($reviewResults -join ',') + ']' |
  python .agents/skills/task-governance-tool/scripts/taskgov.py review result add <task-id> --json
```

Normally complete the Packet's `result_template` using `result_instructions`.
The document has this exact shape (this illustrative completed example is not
evidence; do not copy its reviewer, verdict or provenance claims):

```json
{
  "version": 1,
  "task_id": "tg_task_0123456789abcdef",
  "contract_revision": 1,
  "review_target": {
    "kind": "external_revision", "value": "approved-revision-1",
    "base_revision": "", "generation": 1
  },
  "receipts": [{
    "reviewer": "reviewer-a", "kind": "independent", "verdict": "pass",
    "summary": "No blocking findings",
    "provenance": {
      "reviewer_class": "human", "model_state": "not_applicable",
      "declared_model_id": null, "skill_state": "not_applicable",
      "declared_skill_id": null, "declared_skill_version": null,
      "review_profiles": ["general"], "review_lenses": ["correctness"],
      "context_relation": "external_context",
      "method_codes": ["review_packet_inspection"]
    },
    "findings": []
  }]
}
```

Use the actual Packet object: `data.review_preparation.packet` after qualifying
registration/target setting or `data` after standalone preparation. Its template
already contains `task.task_id`, `contract.revision` and the complete
`review_target`; keep them unchanged and use its Task ID as command `<task-id>`.
The CLI validates each complete document, requires identical envelope values,
and combines only `receipts` in document order. Preserve returned verdicts and
provenance rather than filling or retyping them. Truncated displays are not
complete originals. File names, permissions, complete reads and retention are
caller-owned; this command opens no input file and adds no cleanup operation.
Use [Save Review Original](#save-review-original) when saving a new original, and
[Submit Review Originals](#submit-review-originals) for necessary saved-document confirmation and registration,
with the short save acknowledgement
without generated validation code or repeated LLM body retrieval. Neither replaces the validation below or excuses
an incomplete Packet, failed save or uncertain registration outcome.

All displayed keys are required and no other keys are accepted. Each Finding
contains exactly `severity` and `summary`. Put exact project-relative file/line
references and recommended changes in its bounded summary, not extra fields.
Use the [Receipt/provenance enums, bounds and matrix](#review-provenance) and
[Finding rules](#finding-resolution). `provenance` contains exactly those ten caller declaration fields, with
explicit nullable identifiers and arrays; use null only for `not_required`.
Do not invent model/Skill identity, context, methods, or independence.

The whole input, including framing and whitespace, is limited to 262,144 UTF-8
bytes, 1–8 Receipts, and 64 Findings total. Arrays contain 1–8 complete documents,
not nested arrays or Receipt fragments. Each document and the combined batch
retain those same limits. Version must be integer 1; Contract revision is a nonnegative signed-64-bit
integer and generation a positive one. Exact JSON types are enforced (booleans
are not integers). Duplicate/unknown/missing keys, non-finite numbers, invalid
Unicode and exceeded limits fail `invalid_review_evidence`. Existing privacy
checks inspect every typed declaration before enum, text-limit, duplicate-reviewer
or provenance-matrix checks and never echo rejected content.

Known result-input failures may add `field`, such as
`receipts[].provenance.model_state`, and a fixed reason in `message`.
The same field/reason is visible in text and helper save/submit errors.
`[]` does not identify a document or element number; inspect that known field
in the originals. Missing/type/value/duplicate or declaration-correlation
diagnostics never echo input values. Malformed JSON, duplicate/unknown keys and
other unlocatable failures retain the general error. Correct the original at
its source; this adds no normal-path call or automatic retry/correction.

After closed-shape and privacy validation, all document identity/target values
must match exactly; disagreements fail `review_target_mismatch`. Task ID,
Contract revision and the complete target tuple must also match the current
Task under the writer lock; stale or mismatched input fails `review_target_mismatch`
without rebinding. Existing missing-target, done-Task and capture-version-0
errors remain. Duplicate normalized reviewer keys within the batch, previously
registered reviewers and committed replay fail `review_receipt_already_recorded`.
Duplicate normalized `(severity, summary)` pairs within one Receipt fail
`invalid_review_evidence`; separate reviewers may report the same Finding.

JSON cannot supply user approval. Only when the existing Tier-2 self-review PASS
fallback has explicit current user approval, add repeatable
`--user-approved-reviewer <key>` for the matching reviewer. Duplicate, unmatched,
ineligible or missing required approval fails `invalid_review_evidence`.

All entries are new and save atomically with their existing individual
provenance, References and events. Success data is
`{receipts: [{receipt, findings: [{finding}]}], review_gate, omitted_details}`
in flattened input order, without truncating any new Finding.
Use `data.receipts[].receipt.review_receipt_id` for the returned Receipt IDs
and `data.receipts[].findings[].finding.review_finding_id` for Finding IDs;
no follow-up read is needed to obtain them.
Compact Receipts retain reviewer/kind/verdict/summary/approval alongside ID;
Findings retain parent Receipt ID, severity, summary and status alongside ID.
`omitted_details` explicitly lists provenance, repeated binding, events,
timestamps and resolution metadata; storage and the existing audit read retain
those details. This changes the previous full-item success response.
`review_gate` returns the existing tier/pass counts/fallback/satisfied fields,
first `blocking_code` (or null), and the observed Task/Contract/complete-target
`basis`. It includes old-generation blockers and fresh-review requirements.
It is only a review-gate observation, not verification or completion permission;
the final write still revalidates current state. Do not add a routine show/check
to confirm this response. Failure
data is `{receipts: []}` with no saved prefix, including after a late failure.
Text retains ordered Receipt/Finding IDs and summaries, review gate/basis and
an omission notice. `--read-only` rejects before
stdin is consumed. Successful registration runs existing post-commit maintenance
once; maintenance warnings do not undo the committed evidence.
Post-commit text failure retains successful JSON with
`review_result_display_failed`. Emission failure returns exit 2 with a saved-
outcome stderr diagnostic if available, never a claimed rollback. Recover
lost/incomplete output through public state before retrying; do not resubmit
saved results blindly.

The tool stores no result document or batch ledger, launches no reviewer,
merges no judgment, resolves no Finding, and does not complete the Task.
Distinct reviewer keys remain caller-attested strings, not proof of identity
or independence. The existing single-item commands remain available.

### Caller-Owned Review Handoff

The bundled `scripts/review_handoff.py` is separate from taskgov. Use `python3`
on Linux/macOS. It needs Python 3.12+ and explicit `--repo`.
Use the relevant operation below; generated read/save/submit commands already
supply their actual paths and need no routine reference lookup. Stop on failed,
missing or uncertain output and follow [handoff recovery](#recover-review-handoff),
preserving originals/residue without overwriting or blindly repeating writes.
Each operation retains the [shared path and validation boundary](#handoff-path-and-validation).

#### Handoff Path And Validation

All Packet/result paths must be untracked, Git-ignored `.json` files within the
explicit project and physical directories. Only prepare creates its explicitly
named missing ignored directory chain and `packet.json`, after a ready source
result; preflight rejects an existing destination before invoking the source.
Save/submit require existing directories. No operation changes an ignore rule,
ACL, config, state path or permission. No absolute or
traversal path, backslash, ADS, device name, link/junction/reparse ancestor,
nonregular or multiply linked file is accepted. Package/admin/state roots
`.agents`, `.codex`, `.git`, `.taskgov`, `task-governance-tool` are excluded.
Non-Git projects retain direct stdin registration; this helper cannot establish
an ignored-file boundary there. Packet input is bounded to 32,768 bytes.

Save/submit take `--packet`; they remain usable with an already obtained complete
Packet file. They do not authorize rerunning its source command.
Prepare/read/save/submit share complete-Packet validation, without filling
missing fields; legacy stored Contract constraints retain read compatibility.
For an actually approved Tier-2 self-review PASS, explicitly repeat
`--user-approved-reviewer <key>` on the applicable save and submit calls. Neither
JSON nor a saved file transfers approval. Independent reviews do not use it.

#### Prepare Review Handoff

Before the first Packet-producing call, [choose the completion route](task_workflow.md#choose-the-completion-route).
Authorized supported snapshot completion uses `prepare-finalization`; the
parent-managed route uses `prepare` in place of that operation in both examples.
Use `--directory <unused-ignored-directory>` with project-relative `/` spelling
and the Task ID selected by `task context`:

```powershell
python .agents/skills/task-governance-tool/scripts/review_handoff.py prepare-finalization --repo . --directory reviews/g1 target <task-id> --kind git_snapshot
# Only after required verification, using the generation from the prior result:
python .agents/skills/task-governance-tool/scripts/review_handoff.py prepare-finalization --repo . --directory reviews/g1 receipt <task-id> --result pass --duration-ms <milliseconds> --scope-coverage full --expected-target-generation <generation>
```

Target accepts the existing kind/revision options; Receipt also accepts the
unchanged structured verifier bytes on `--from-stdin` instead of four manual
fields. Preparation invokes that one source CLI once, before LLM display; it
does not run verification. `--reviewers` allocates 1–8 distinct result paths
(default two), not a gate or proof. `operation_status` is `not_started`,
`succeeded`, `failed` or `unknown`, separate from `handoff.status`.
`source_exit_code` reports the child exit when observed; `source` carries the
target tuple, route, blocking code and preparation binding, or the recorded
Receipt ID/result/coverage/Contract/target. Existing sanitized `warnings`
remain visible. The helper target uses the Packet's empty-string spelling for
the Receipt's null absent non-snapshot base. `ok=true`/exit 0 means ready or
not-applicable, not PASS.

Only `handoff.status=ready` supplies a saved complete `packet_path`, per-reviewer
`review_requests` (each containing only `result_path` and a self-contained
`request` with the exact read/save commands embedded). Manual `prepare` supplies
`submit_command`; integrated preparation instead supplies the
[finalization fields and command](#integrated-review-finalization), without
`submit_command`. Retain the returned route's command.
It also supplies `review_wait={status,task_id,wait_tool,guide}`. Status is
`enabled|disabled|unavailable` from local policy only; fixed tool/guide values
are `review_wait_wait` and `references/review_wait.md#normal-wait`. It does not
dispatch reviews, prepare a wait or establish host connection. Normal responses
omit `wait_ended_command`; only a retained legacy supervisor uses the separate
[wait-decision helper](#record-review-wait-decision).
Pass those requests directly; required independent artifact
and authority inspection is unchanged. A target requiring a Receipt returns
`not_applicable` without files, so its later Receipt call may use the same area.
`blocked`, `failed` or `unavailable` never supplies reviewer requests. After a
source succeeds, a Packet/file failure does not undo it. Keep the saved binding
or Receipt ID, use only the appropriate [bound recovery](#recover-review-handoff), never repeat the
write. A target failure after dispatch is `unknown`, even with an `ok=false`
response: target/Runner intent or cleanup may already be saved. Only a confirmed
pre-dispatch rejection is `failed`; do not infer unsaved state from an error code.
For unknown/lost outcomes inspect public state first. There is no automatic
retry, new ledger, reviewer launch or raw-response file. Capture is limited to
262,144 bytes in memory; malformed, incomplete or oversized output cannot be
used as a Packet. Packet/result files alone are persisted.

#### Integrated Review Finalization

For authorized supported completion, use `prepare-finalization` from the first
target call and for any required Receipt call, with the same source arguments
as `prepare`. Choose the route [before preparation](task_workflow.md#choose-the-completion-route).
There is no approval flag, second material list or extra normal preparation call.
Manual `prepare` retains its existing behavior where this operation is not
authorized or applicable, or the user explicitly chooses manual completion.
Review PASS never supplies Git permission.

The ready response includes `finalization={status:prepared,task_id,
target_generation}` and a fully quoted `finalization_command`, and omits
`submit_command`: the finalizer owns registration for this intent. It binds the
actual owner, execution, Contract, target generation, stable base/index, existing
branch, complete Packet and named original paths in canonical operational state.
The target must be `git_snapshot` at the Git root on an existing attached branch.
The adapter preserves index/worktree content and executes no commit hooks,
signing, filters or editor. Projects requiring those use the manual Git workflow.
No stage, new branch, push, arbitrary command or permission/config write occurs.

After all reviewers end, an enabled matching wait worker registers the whole
valid original batch, checks existing gates and unresolved Findings, publishes
only that fixed commit, and records native completion before notifying the same
parent. Missing originals prevent partial registration. Failed/interrupted
reviewers or any unresolved Finding stop automatic commit. Other manual
completion conditions remain unchanged.
Retained owner/target constraints are checked inside the native writers, and
Findings are checked again before publication and locked completion. A saved
failed/interrupted, unavailable or changed reviewer turn remains a blocker on
this intent across `--check` and retries; obtain fresh reviews under a new target
generation/intent instead of treating missing host information as success.

After all reviewers end, workerless explicit continuation uses the retained
command once. This is an ordinary completion path as well as the recovery entry:

```powershell
python .agents/skills/task-governance-tool/scripts/review_handoff.py finalize --repo . --task-id <task-id> --target-generation <generation>
```

The generated command includes its exact generation; omission uses the current
Task generation, never another stored intent. `--check` is read-only and returns
the complete report without creating state or replaying effects. Reports contain
Task/target, recorded work with coverage or `not_recorded`, verification, original
and registered reviews, every registered Finding/ID, gates, stage intent and
observed results, confirmed commit, missing fields, warnings and next action.
`ok` means the operation had no processing error; only `status=completed` confirms
all stages, not a successful check, registration or message delivery alone.

Use [Continue after reviews](task_workflow.md#continue-after-reviews) for result
processing and the applicable next action. Use [integrated recovery](#recover-integrated-finalization)
only for a blocker, partial success or missing/uncertain result.

#### Recover Integrated Finalization

Use only for a reported blocker, partial success, or missing/uncertain result
of an integrated intent. No notification does not establish that stages were
not run. A lost or unknown result first uses the retained `finalization_command`
with `--check` to observe the report without effects; this is not a normal
completion prerequisite. It does not itself add a new review requirement.

Partial success is preserved. Before explicit recovery, resolve the reported
blocker. The helper observes immutable registration bindings, actual publication
and native completion before continuing. It reuses the saved candidate and
requires the original base/index and current gates before publishing a still
unpublished candidate. Changed/unknown basis cannot authorize recapture or a
replacement commit. Successful completion is not repeated. Only a demonstrated
delivery constraint permits an explicitly incomplete notice naming the actual
constraint and omitted fields; never report that notice as complete delivery.
Lost or explicitly incomplete notifications use the retained command with
`--check`. History-read truncation alone does not cause omission
or require this recovery; notification correlation does not certify an unseen
body suffix. Do not resubmit originals
or resend a host message to recover missing report fields.

#### Read Current Review Wait Basis

The read-only `review_handoff.py wait-basis --repo <project> --task-id <original-task>`
returns the current structural wait basis described below.
It is a helper operation, not a new taskgov command or ordinary Skill-loop step.
It never selects another Task, captures a target, launches a reviewer, registers
usage, or operates a reservation. No Packet, caller/session, timer or database
path option is accepted. Stored ownership is an observation, not executor identity.

Success is `{ok:true,status:"review_wait_basis",basis}`. The closed `basis` has
`version:1`, `project_id`, `project_path_hash`, `project_binding_generation`,
`task_id`, `task_status`, `execution_id`, `ownership_generation`,
`parent_thread_id`, `contract_revision`, `target_kind`, `target_value`,
`target_base_revision`, `target_generation`, and `artifact_manifest_id`.
The stored owner or completion owner, complete current target, Contract,
project binding and immutable manifest/Reference association come from one
admitted query-only snapshot. No stored prose, raw path or executor metadata
is returned. Contract revision zero retains its existing no-Contract meaning.

Missing/changed state fails closed without initialization, migration or repair.
Failures use `wait_basis_invalid_arguments`, `wait_basis_inactive`,
`wait_basis_target_required` or `wait_basis_unavailable` with a fixed message;
the existing parser retains `handoff_invalid_arguments`. No partial basis is
returned. Numerical collection settings, missing usage state and numerical
failures do not affect this read. This operation does not activate a wait;
the MCP service uses it internally for the original Task's current basis.

#### Record Review Wait Decision

Only a retained legacy wait supervisor uses `wait-ended`, once in its first
all-ended/shortening-decision turn. The current
[waiting procedure](review_wait.md#normal-wait) is deterministic
and never invokes this helper or fabricates a supervisor usage turn. Normal
preparation does not generate this legacy command. For that existing supervisor
context only, use the original project's retained Packet path:

```powershell
python .agents/skills/task-governance-tool/scripts/review_handoff.py wait-ended --repo . --packet reviews/g1/packet.json
```

Alternatively, `--from-stdin` accepts the unchanged complete UTF-8 Packet and
requires no shared file. Choose exactly one input; both retain complete-Packet
validation and the 32,768-byte limit. No session/turn, timer or verdict input is
accepted. Actual caller identity and one current core read supply the original
execution and immutable target anchor. Stale/missing basis fails without a
marker or core write; it never selects another current Task.

Success is `{ok:true,status:"review_wait_ended",review_wait}`. The metadata has
`version:1`, actual `session_id`, `project_id`, `task_id`, `execution_id`,
`contract_revision`, four-field `review_target`, and `artifact_manifest_id`.
The helper best-effort registers that sender for optional usage collection.
Coverage is only that decision turn, not the whole wait or the receiving
parent's turn.
It does not initialize/migrate usage state, read logs, operate timers, save a
review or write Task evidence. Numerical failure does not invalidate a validated
marker or block ordinary review/completion. Only explicit setup upgrades an
older numerical store; missing coverage is not measured zero.

The marker is a trusted sender declaration, not all-ended, delivery, PASS,
ownership or collection proof. Parent receipt/resumption, partial or repeated
events and cleanup do not invoke it. Failed/lost output leaves coverage unknown;
continue necessary authorized wait handling without replaying timer or Task
writes. Independent reviewer requests and duties remain unchanged.

#### Read Review Packet

Assigned independent reviewers use the fixed read command embedded in the request, whose shape is
`review_handoff.py read --repo . --packet reviews/packet.json`.
This replaces the raw Packet read, not an extra query. It always returns the
independent-reviewer display after full saved-Packet validation. Removing the
role selector does not require recreating existing Packets or repeating reviews.
It retains all fields except the parent's `receipt_command` and substitutes
independent-only `result_instructions`, preserving applicable vocabulary,
relations, limits, privacy and unfinished claims. The disk Packet is unchanged.
Within this replacement read, the helper reuses existing Git target capture and
one read-only public `review prepare`, comparing saved Task/Contract/target and
path metadata. Mismatch, unavailable state or incomplete response fails without
a display; no second reviewer check/show, state write or target reset is added.
`context_check=matched_at_read` is an observation, not a future guarantee.
`review_material` lists the complete Git delta (not the Packet's bounded list),
immutable before/after object IDs and modes, comparison/dependency revisions,
and a `collect_command` bound to the saved Packet. Replace its single placeholder
with a JSON array of the dependency paths still needed (`[]` for none); all
changed before/after bodies and diffs are included automatically. No parent
selection or OID assembly is required. The returned `bodies` table supplies each
blob once, with explicit path/revision/side references and outcomes in `changes`
and `dependencies`. Whole-file add/delete diffs reference their complete body;
two-sided diffs are Git patches. Use collection for the first required AGENTS,
authority, source and test reads instead of automatically reading ambient copies
first. Read governing text before judgment and follow its routes. Reuse an
already complete body only when its immutable object identity or exact
revision/path matches the current target mapping; omit such dependencies from
collection. A filename, summary, prior review or unknown/incomplete delivery
does not establish that match. The same fixed text can serve authority reading
and review without another copy; project reread obligations still apply.
`complete`/exit zero is delivery, not review coverage or PASS.
`incomplete`/nonzero retains successful siblings but requires recovery of missing,
unavailable, binary or oversized material (1 MiB per text, 4 MiB total). Tool
truncation also requires recovery; newly discovered dependencies still need reads.
Only when individual/additional retrieval or discovery is needed, run the
returned `recovery_command`: it repeats the same validated read with optional
`--material-details` and supplies individual read/diff/directory commands and
their complete quoting and recovery guidance. Recover affected material rather
than collecting successful siblings again. Normal read retains all judgment,
format and provenance information needed for save; the detailed read is not a
normal prerequisite. Retrieval reuses
the sanitized/no-fetch Git environment in a child,
without changing your shell environment; missing objects remain unavailable.
Use listed objects for changed snapshot files,
base-commit paths for unchanged dependencies, and exact commit paths for commit
targets. Required submodule or unreadable material must be supplied before PASS.
Diff/external targets return `requires_supplied_material`; no content binding
is inferred from a saved fingerprint or substituted from Git. Existing manifest
bounds apply without a partial list. No blob, patch or additional file is saved.
Sanitized public `warnings` remain visible. Save/submit validation is unchanged.
The display is compact UTF-8 JSON plus LF, independent of the shell code page.
The generated request/output supplies the procedure without another guide read;
the [reviewer procedure](task_workflow.md#independent-reviewer) remains fallback
guidance. Different/uncertain roles retain the complete Packet
and applicable alternative instructions; never infer actual independence.

#### Save Review Original

The generated request supplies the exact save command. When shell syntax is
needed, supply only the actual completed JSON on UTF-8 stdin, not UTF-16 or BOM:

```powershell
$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
@'
<complete original version-1 result JSON>
'@ | python .agents/skills/task-governance-tool/scripts/review_handoff.py save --repo . --packet reviews/packet.json --output reviews/review-a.json
```

Save accepts one complete object document with one Receipt. Existing format,
privacy, tier, provenance and all binding validation happens before any file is
created. It writes the original bytes exclusively, flushes, reads actual saved
bytes and revalidates them and the unchanged Packet. Success is compact JSON:
`{ok:true,status:"saved",path,verdict,finding_count}`. It is only a saved-file
acknowledgement, not registration, review PASS, independence or completion proof.

Return the saved path, verdict and Finding count once, without echoing the body.
A failed/uncertain save needs [recovery](#recover-review-handoff), not overwrite.

#### Submit Review Originals

Only for parent-managed registration (`prepare`), after all dispatched reviewers
end run the retained `submit_command` once. Integrated preparation uses its
finalization command instead; do not separately submit its originals.
It owns saved-handoff confirmation; no separate save-report or original read is
required before this operation:

```powershell
python .agents/skills/task-governance-tool/scripts/review_handoff.py submit --repo . --packet reviews/packet.json reviews/review-a.json reviews/review-b.json
```

Submit accepts 1-8 paths, preserves complete original bytes, and adds only array
framing. The combined 262,144-byte / 8-Receipt / 64-Finding bounds remain. The
sibling taskgov stdin command performs its normal atomic registration and live
revalidation; stdout and exit status are its ordinary response, including every
Finding. The helper first confirms the complete Packet/original files and any
required session sidecars/digests, then rechecks their bytes before dispatch.
This observes current files rather than inferring the child or past save outcome.
Both PASS and `changes_requested` return actual verdicts, summaries, all Finding
IDs/bodies and the registered-basis `review_gate` through the same response.
Use those for judgment; the all-ended notification alone proves none of them.

### Recover Review Handoff

Only for preparation recovery, choose ONE binding form and a new unused area:

Keep the preparation operation chosen before the source write. The examples
use `prepare` for parent-managed registration; substitute `prepare-finalization`
for an integrated intent. Recovery never authorizes a different route or Git action.

```powershell
python .agents/skills/task-governance-tool/scripts/review_handoff.py prepare --repo . --directory reviews/recovery recover <task-id> --expected-binding <binding>
python .agents/skills/task-governance-tool/scripts/review_handoff.py prepare --repo . --directory reviews/recovery recover <task-id> --verification-receipt-id <recorded-id>
```

Keep the shared [path/validation boundary](#handoff-path-and-validation).
Use the saved binding or Receipt ID; never replay a successful source write.
Unknown/lost source or submission outcomes require public-state inspection first.
For corrections or alternative actual Receipt declarations, use
[workflow recovery](task_workflow.md#review-handoff-recovery).

Reads check identity and metadata before/after, with final byte rechecks before
submission. These trusted-local checks do not isolate a hostile peer with the
same permissions or guarantee future immutability. Save never overwrites, and
no operation deletes or retries. A partial write or failed confirmation
leaves its residue and returns no ready acknowledgement. A lost save response
may be confirmed by submit's file checks without another parent read or save.
Use a new unused path for a corrected original. After an unknown
registration outcome, inspect public recorded evidence, not a blind resubmit.

Helper errors emit `ok:false`, a sanitized code and fixed guidance, without raw
input/path/exception detail. Path/ignore, file-change, invalid-input, I/O and
unknown-outcome failures remain distinct from taskgov registration responses.
For parsed submit invocations, helper-local failures also carry
`registration_status=not_started|unknown`. The former means no registration
dispatch: recover the reported input; missing input is `handoff_input_missing`.
The latter begins at dispatch and requires public recorded-evidence inspection
before any retry. No valid subset is submitted after an input failure. The
registration child's ordinary response/exit passes through unchanged, including
successful registration with a blocking review gate and post-commit output loss.
File ownership, authorized path allocation and retention remain caller-owned.
Ordinary handoff recovery adds no network, Git write, review launch, alternate
gate or database writer. Integrated finalization has only its bounded exception
above and uses the existing native registration/completion writers.

## Receipt Output For Integration Or Audit

Read this section only to consume generated Receipt fields in an integration
or interpret explicit [audit detail](#task-audit-detail). It is not an input
template or a prerequisite for registering verification/reviews. Normal
registration uses the input and result-handling sections above; the tool owns
the output IDs, timestamps, subject binding, and provenance metadata.

### Verification Receipt Output

Successful registration returns `data.receipt` with exactly:

```text
verification_receipt_id, project_id, task_id, contract_revision,
verification_subject, result, duration_ms, scope_coverage, source_revision,
created_at
```

`source_revision` is the bound target's `kind`, `value`, nullable `base_revision`,
and `generation`. `verification_subject` has exactly `basis_version`, `kind`,
`authority_snapshot_id`, `verification_criterion_id`, and `legacy_caller_label`.
A native v1 Receipt has basis 1, kind `task_verification_criterion`, both
non-null IDs, and a null legacy label. A migrated pre-v18 Receipt has basis 0,
kind `legacy_caller_label`, both IDs null, and the preserved label. The audit
Receipt rows use the same union. These are tool-owned bindings, not caller
identity authentication or permission to reuse historical evidence.

Successful text starts with this unchanged Receipt prefix:

```text
Verification receipt recorded: <verification_receipt_id>
Result: <result>  Coverage: <scope_coverage>
Source: <kind>/generation <generation>
```

It is followed by `Review preparation: <ready|blocked|failed>` and the Packet
text or sanitized code/message lines. JSON additionally returns
`data.review_preparation` as described under [Receipt rules](#verification-receipt-rules).
The Packet component keeps the standalone 32,768-byte envelope check; the
combined output also includes the bounded Receipt/status and is not subject
to a new combined 32,768-byte cap.

### Review Provenance Output

Each public Review Receipt contains `review_provenance`. Native independent
or self-review Receipts have a v1 object with exactly:

```text
review_provenance_id, provenance_version, reviewer_class, model_state,
declared_model_id, skill_state, declared_skill_id, declared_skill_version,
review_profiles, review_lenses, context_relation, method_codes,
assurance_class, producer_class, producer_version, digest
```

Native assurance/producer/version is `bound_attestation/trusted_caller/1`.
Migrated pre-v18 independent/self-review Receipts use the same keys as v0:
null ID/digest and semantic fields/collections, with
`legacy_unknown/legacy_migration/1`. This records missing historical provenance,
not explicit v1 `unknown` or empty code sets. `not_required` instead has null
provenance and no provenance row. These cases do not change the parent
Receipt's caller-attested meaning or prove identity, actual model/Skill use,
independence, or review truth. Only current evidence can satisfy current gates.

## Internal Continuity Boundary

Read this section for a continuity warning or an explicit setup/Viewer
diagnosis, not as a normal-loop action. Successful `setup` enables bounded
same-process maintenance; there is no disable, export, custom-output, or repair
command. Taskgov starts no background worker, scheduler, browser, or network
operation. SQLite stays canonical; generated Evidence JSON and Viewer files
are projections, never inputs or current completion evidence.

If post-commit maintenance cannot complete, the primary business mutation
remains successful and only a bounded warning is appended:

- `evidence_projection_deferred`: `Evidence projection refresh was deferred; task result is unchanged`
- `evidence_projection_failed`: `Evidence projection refresh did not complete; task result is unchanged`
- `viewer_refresh_deferred`: `Viewer refresh was deferred; task result is unchanged`
- `viewer_refresh_failed`: `Viewer refresh did not complete; task result is unchanged`
- `backup_deferred`: `managed backup was deferred; task result is unchanged`
- `backup_failed`: `managed backup did not complete; task result is unchanged`

The operation remains due for a later eligible successful mutation. The Skill
does not ask the LLM to retry, schedule, or stop for these warnings.

Use [doctor](#doctor) only for explicit read-only diagnosis and [setup](#setup)
for authorized repair. A stored Task fault preserves the last-good Viewer;
setup preflight fails no-write with `project_state_unreadable`, whereas a
failure confined to its later Viewer stage is `setup_incomplete` and reports
the durable completed prefix.

The optional physical `config/viewer.json` is browser-presentation policy, not
a normal-loop choice. Only explicit Setup feature selection may create/toggle
it; absence or `enabled=false` means no refresh
timer. A valid schema-1 `visibility-refresh-v1` profile applies its 5-3,600 second
interval on the next Viewer publication (`enabled` defaults to true). Invalid policy preserves routine Task
success and the last-good Viewer with `viewer_refresh_failed`; actual setup uses
`setup_incomplete`. Preview remains successful and no-write, reporting
`viewer_status="repair_required"` and planned `viewer_publish`. Doctor does not
inspect this policy; it cannot confirm its validity.

## Errors And Privacy

Use the applicable input or failure section. Stored-input rules apply before
submission; an uncertain write outcome requires its own diagnosis before retry.

### Command And Argument Errors

Removed or unknown root commands return exit 2 and `invalid_command` with
message `command is not available`. Any public `--db` occurrence returns exit 2
and `invalid_option` with message `option is not available`. Other unknown
nested commands and unsupported or malformed options return exit 1 and
`invalid_argument` with message `arguments are invalid`. With lexical `--json`,
these parser failures use the normal bounded envelope, resolve no project, and
perform no read or write.

Mutually incompatible supported options return
`invalid_option_combination` with a bounded sanitized message.
Specifically, `setup --read-only --confirm-relocation <token>` fails before
project/state resolution with exit 1,
`invalid_option_combination`, and message
`--confirm-relocation cannot be used with --read-only`.

Correct an observed command/argument mismatch against its command section.

### Project Root Errors

`invalid_project_root` means the supplied root is missing, not a directory, or
an invalid path. `project_root_uninspectable` instead means root inspection or
normalization could not finish. Its fixed message is `project root could not be
inspected safely; check access permissions and execution context`. Do not infer
that the project is absent or initialize a replacement. Check the execution
context and access boundary without quoting OS exceptions or automatically
elevating or changing permissions. These errors return exit 2 with no project
state access/write. Doctor uses `project_state.code="project_uninspectable"`
for the latter and `setup_eligible=false`; a further doctor call is not required
to obtain the same diagnosis. Successful Task work still needs no doctor.

### Uncertain Operation Outcomes

`internal_error` alone does not establish a syntax error or an instruction
defect: the cause may be in the environment or internal processing. Report the
sanitized failure and keep the cause unconfirmed until relevant read-only
diagnosis establishes it; do not guess new options or blindly retry writes.
If a write response is lost or its committed outcome is unknown, inspect the
actual saved state through the relevant public read before deciding on a retry.

### Relocation Errors

Relocation setup failures use exit 2 and these fixed sanitized messages:

| Code | Message |
|---|---|
| `project_relocation_required` | `project state is bound to a different project location; run setup --read-only` |
| `relocation_token_invalid` | `relocation confirmation is invalid` |
| `relocation_token_expired` | `relocation confirmation has expired; run setup --read-only again` |
| `relocation_token_stale` | `project relocation state changed; run setup --read-only again` |
| `relocation_token_used` | `relocation confirmation has already been used` |
| `relocation_not_required` | `project relocation is not required` |

### Setup And Diagnostic Errors

Important setup/diagnostic errors include:

- `unsupported_python`
- `unsupported_install_layout`
- `project_scope_required`
- `invalid_project_root`
- `state_path_invalid`
- `state_ignore_required`
- `invalid_backup_policy`
- `setup_restore_failed`
- `setup_initialization_failed`
- `setup_backup_failed`
- `setup_migration_failed`
- `setup_incomplete`
- `project_state_unreadable`
- `project_mismatch`
- `project_relocation_required`
- `relocation_token_invalid`
- `relocation_token_expired`
- `relocation_token_stale`
- `relocation_token_used`
- `relocation_not_required`
- `schema_too_new`
- `unsupported_journal_mode`
- `database_busy`

### Task Review And Handoff Errors

Important task/review/handoff errors include:

- `invalid_argument`, `invalid_status`, `invalid_status_transition`
- `initial_done_forbidden`, `initial_paused_forbidden`
- `blocked_reason_required`, `pause_reason_required`
- `sequential_predecessor_incomplete`, `done_task_requires_reopen`
- `session_identity_required`, `session_task_in_progress`, `task_not_owned`,
  `task_ownership_changed`
- `completion_history_inconsistent`
- `contract_activation_forbidden`, `contract_authority_required`,
  `contract_write_conflict`
- `verification_required`, `review_required`, `commit_required`
- `verification_requirement_unspecified`
- `verification_expectation_required`, `verification_basis_stale`,
  `verification_receipt_required`, `verification_receipt_blocking`,
  `verification_receipt_already_recorded`, `invalid_verification_evidence`
- `completion_evidence_conflict`,
  `evidence_basis_stale`, `evidence_ledger_inconsistent`,
  `evidence_bundle_too_large`,
  `external_revision_approval_required`,
  `git_commit_not_found_or_ambiguous`
- `review_target_required`, `review_target_mismatch`,
  `review_receipts_insufficient`, `review_changes_requested`,
  `review_finding_unresolved`
- `handoff_not_persisted`, `handoff_not_withdrawable`,
  `handoff_occurrence_invalid`
- `privacy_rejected`, `not_found`, `internal_error`

`completion_history_inconsistent` uses exit 2 and the fixed sanitized message
`stored completion history is inconsistent`.
`invalid_verification_evidence` likewise fails closed with exit 2 and
`stored verification evidence is inconsistent` when stored Receipt structure
or binding is malformed.
`project_state_unreadable` uses exit 2 and fixed message
`project state could not be read safely` for any invalid current stored Task
row or selected Task's current Contract relationship. It never downgrades to
`privacy_rejected`/`invalid_argument`, returns a
partial projection, or exposes the offending value. Managed setup recovery
retains the sole narrower exception: only stored Task `verification`
privacy/capacity failure is candidate-local; all other Task faults remain
whole-set fatal, including every Contract-pointer relationship fault.

### Stored Input Privacy

Privacy validation is deny-by-default for stored free-form input. Never submit
secrets, tokens, authorization headers, raw stdout/stderr, stack traces,
environment dumps, private prompts/reasoning, full chats/reviews, or large raw
diffs. If handoff input is rejected, never repeat, quote, log, store, or
forward the rejected raw content. Make at most one new attempt using a newly
written concise sanitized abstraction.

Normal/new input rejects the equality form
`dispatch_authorization=<value>` and JSON key
`"dispatch_authorization":<value>`, including numeric values. Use
`operation_sequence=<positive canonical integer>` for future
external-operation correlation or idempotency evidence. It is not authority.

#### Legacy Stored Counter Compatibility

The sole legacy reader is confined to already-stored bounded lowercase
`dispatch_authorization` positive-canonical-integer equality and numeric JSON
counter forms in Contract constraints and checkpoint summaries. It preserves their original text, performs
no write, and leaves compound credentials or tokens rejected.
