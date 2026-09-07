# task-governance-tool Current Product Specification

Status: The immutable published product remains v0.10.0/schema v16/Viewer v4
sources v5-v16/20 leaves; its identity is fixed in `docs/release-install.md`.
The current unpublished candidate is v0.13.0 with SQLite schema v22, Viewer
snapshot v4 accepting source schemas v5-v22, and 21 public command leaves.
Its supported behavior includes tool-owned Verification Receipt subjects,
versioned Review provenance, immutable Evidence References and completion
Bundles, deterministic Evidence JSON, and the explicitly opted-in trusted-local
verification Runner with a closed manual fallback. Schema v20 remains a
supported migration source and
audit-only Runner lineage; only fresh gate-eligible evidence under the unchanged
schema-v21 protocol retained by schema v22 may
satisfy the Runner branch. M25 Select-Split-Merge-Register is active only as
Skill instruction-layer guidance. Completed execution narrative belongs only in
indexed history, and
the Task database owns live state and evidence.

This document specifies supported product behavior. The concise
[authority index](authority.md) routes implementation structure to
`docs/design.md`, durable agent behavior to root `AGENTS.md`, current decisions,
open issues, gateways, and non-delegated static contracts to root `plan.md`.
The project-local Task database, inspected
through the public CLI, owns live Task state and evidence. Indexed files under
[`docs/history/`](history/README.md) are non-authoritative lineage only.
Required current behavior never depends on historical text.

## Documentation Authority And History

`docs/authority.md` is the repository-visible registry and selective-read
router. Root `AGENTS.md` plus that index are the mandatory start set; the live
Task Contract selects the applicable current owners and sections. No execution
or documentation-governance sequence is currently active or conditional;
completed sequence detail is indexed non-authoritative history. History is
indexed by `docs/history/README.md`.
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
defined below. One explicit `task edit --runner-plan-action` invocation may
also create or replace only the canonical ignored package-local Runner Plan
under the current authoring contract.

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
| `task.edit` | `task`, `changed_fields`, `event`, plus `contract_write` only for Contract input and `runner_plan_update` only when a Runner Plan action was supplied |
| `task.complete` | `task`, `changed_fields`, `event` |
| `handoff.record` | `handoff`, `local_record` |
| `handoff.list` | `handoffs`, `count`, `total_matching`, `limit`, `states` |
| `handoff.show` | `handoff` |
| `handoff.withdraw` | `handoff`, `changed_fields` |
| `review.target.set` | `task`, `changed_fields`, `event`, `verification_route`, `blocking_code` |
| `review.receipt.add` | `receipt`, `event` |
| `review.finding.add` | `finding`, `event` |
| `review.finding.resolve` | `finding`, `event` |
| `verification.receipt.add` | `receipt` |

`task.show` failure keeps both `completion_history=null` and
`verification_evidence=null` in its bounded empty data.
`review.target.set` adds its two routing keys only on success. Its failure data
remains exactly `task=null`, `changed_fields=[]`, and `event=null`.
Revision-zero Contract output is exactly revision 0; empty scope, acceptance,
constraints, `authority_ref`, and `change_reason`; and null `created_at`.
`local_record` contains exactly `durable`, `created`, `replayed`, and
`handoff_id`.

### Task Selection And Read Commands

Current detail is owned by the [Task operation specification](task-operation-specification.md#task-selection-and-read-commands).

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

<a id="task-record-and-state-transitions"></a>
<a id="task-contract"></a>
<a id="handoff-outbox"></a>
<a id="effort-advisory"></a>

Current detail is owned by the [Task operation specification](task-operation-specification.md#task-state-scope-review-and-completion).

## Approved Post-MVP Extension: TG-M16 Reduced Loop Discipline Trial

Current detail is owned by the [Task operation specification](task-operation-specification.md#approved-post-mvp-extension-tg-m16-reduced-loop-discipline-trial).

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

### Versioned Review Provenance And Bundle Boundary

New `independent` and `self_review_fallback` Receipts use the existing
`review receipt add` leaf. Every public Review Receipt has exactly one
`review_provenance` union. A native independent/fallback Receipt exposes one v1
object with exactly these keys:

```text
review_provenance_id provenance_version reviewer_class model_state
declared_model_id skill_state declared_skill_id declared_skill_version
review_profiles review_lenses context_relation method_codes assurance_class
producer_class producer_version digest
```

The ID is `tg_review_provenance_` plus 16 lowercase hexadecimal characters;
`provenance_version` is integer `1`; assurance and producer are exactly
`bound_attestation/trusted_caller/1`; and `digest` is
`sha256:<64-lowercase-hex>`. The digest is SHA-256 over
`taskgov-review-provenance-v1\0` plus canonical sorted-key compact UTF-8 JSON
containing exactly `project_id`, `task_id`, `review_receipt_id`, `receipt_kind`,
`target`, and every v1 public field from `provenance_version` through
`producer_version`. `target` is exactly
`{kind,value,base_revision,generation,capture_version}`. The random provenance
ID and digest are excluded from that input.

A pre-v18 independent/fallback Receipt has no native row and projects the same
keys with `provenance_version=0`, null ID/digest and null v1 semantic fields and
collections, plus exactly `legacy_unknown/legacy_migration/1`. A Tier-0
`not_required` Receipt projects `review_provenance=null` and owns no provenance
row. Legacy absence, explicit v1 unknown, empty v1 code sets, and not-required
are distinct states; no migration or reader infers one from reviewer key,
summary, kind, or verdict.

The exact scalar vocabularies and fixed collection orders are:

```text
reviewer_class   human llm deterministic_tool hybrid unknown
model_state      declared not_applicable unknown
skill_state      declared not_applicable not_used unknown
context_relation same_context forked_context fresh_context external_context
                 not_applicable unknown
review_profiles  general authority_contract implementation verification
                 migration_compatibility privacy_safety release_acceptance
review_lenses    correctness contract_compliance state_completion_integrity
                 privacy target_safety verification_regression
                 migration_compatibility maintainability accessibility
                 performance release_integrity
method_codes     review_packet_inspection authority_cross_check diff_inspection
                 source_inspection test_inspection
                 verification_evidence_inspection artifact_inspection
                 runtime_observation deterministic_rule_check
```

At most four profiles, eight lenses, and eight methods are accepted. Each is a
set: duplicates are invalid, empty is valid, and storage/public projection uses
the fixed enum order regardless of option order. `context_relation` is one
required code. Declared model and Skill IDs are 1-128 ASCII bytes matching
`[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}`; declared Skill version is 1-64 ASCII
bytes matching `[A-Za-z0-9][A-Za-z0-9._+-]{0,63}`. Values are preserved
byte-for-byte after the common privacy check and are identifiers, not
free-form capability claims.

The existing leaf requires `--reviewer-class`, `--model-state`,
`--skill-state`, and `--context-relation`; permits optional
`--declared-model-id`, `--declared-skill-id`, and
`--declared-skill-version`; and permits repeatable `--review-profile`,
`--review-lens`, and `--review-method` only for independent/fallback Receipts.
No value is defaulted or inferred. Every provenance option is forbidden for
`not_required`. Type, enum, bound, duplicate, grammar, or cross-field failure
uses `invalid_review_evidence`; privacy rejection retains precedence and emits
no rejected value.

| Case | `reviewer_class` | Model state and ID | Skill state and ID/version |
|---|---|---|---|
| Human | `human` | `not_applicable`, no ID | `not_applicable`, no ID/version |
| LLM without Skill | `llm` | `declared` with ID, or `unknown` without ID | `not_used`, no ID/version |
| LLM with Skill | `llm` | `declared` with ID, or `unknown` without ID | `declared` with ID/version, or `unknown` with neither |
| Deterministic tool | `deterministic_tool` | `not_applicable`, no ID | `not_applicable`, no ID/version |
| Hybrid | `hybrid` | `declared` with ID, or `unknown` without ID | `declared` with ID/version, `not_used`, or `unknown` |
| Applicable data unavailable | `unknown` | `unknown`, no ID | `unknown`, no ID/version |
| Review not required | no provenance object | no model state | no Skill state |
| Legacy independent/fallback | v0 absence | `legacy_unknown` | `legacy_unknown` |

The matrix is exact. Human and deterministic-tool require both states
`not_applicable`; LLM and hybrid require model `declared` with its ID or
`unknown` without one; declared Skill use requires both ID and version;
`not_used` and `unknown` require both absent; reviewer class `unknown` requires
both states `unknown`. Verdict does not alter this matrix. Profile/lens/method
sets and context add no capability, applicability, or further inference.

Schema v18 stores the version discriminator and nullable provenance ID on the
Receipt plus normalized immutable `review_receipt_provenance` and
`review_receipt_provenance_codes` rows. Code rows contain only
`profile|lens|method`, a zero-based contiguous ordinal within kind, and an
allowed code; duplicate code or ordinal is invalid. Migrated
independent/fallback is `0/null`, native independent/fallback is `1/non-null`
with exactly one v1 row, and `not_required` is `0/null`. The Receipt,
provenance, and code rows commit atomically. Migration creates no provenance,
ID, digest, declaration, or Evidence Reference, and every reentry/read path
validates the exact version/kind/null/code/digest matrix.

Native v1/null provenance is included in the Review Receipt Evidence Reference
and native Bundle. Migrated v0 Receipts have neither and cannot enter a native
Bundle. Viewer snapshot v4 validates and discards provenance with no field,
panel, filter, or UI. The original Receipt assertion remains caller-attested;
neither it nor provenance proves identity, execution, competence,
independence, quality, diversity, or truth, changes a gate, or stores a person,
session, prompt, chat, reasoning, raw output, command, log, environment,
credential, or provider body.

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
On success, the same target-set service returns exactly one closed route for the
target it just stored. `verification_route` is exactly `not_required` for an
empty verification expectation, `receipt_required` for the existing marker-`0`
manual path or exact closed no-launch fallback, `runner_pass` for the exact
qualifying complete-plan Runner pass, or `blocked` for every other stored Runner
terminal. `blocking_code` is null for the first three routes and is exactly the
existing `verification_receipt_blocking` code for `blocked`. These fields expose
no Runner ID, observation, gate tuple, Receipt ID, command body, or raw result.
The text success output is unchanged.
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
eligibility, a matching current target, the qualifying current basis selected
by the sole schema-v21/v22 selector, sufficient current-generation review, and no
blocking finding/receipt. The manual arm uses the current Verification Receipt;
the Runner-pass arm uses its qualifying observation with a null Receipt link.
They use the same transition service.

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

Current detail is owned by the [Task operation specification](task-operation-specification.md#typed-checkpoint).

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
nonempty manual arm links the exact qualifying pass/full Verification Receipt;
a nonempty Runner arm stores a null Receipt link and the exact qualifying
Runner-observation pointer. An expectation whose trimmed text is empty stores
the digest of its exact existing bytes and null links. The exact empty string uses
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

## Current M25 Select-Split-Merge-Register Contract

<a id="candidate-first-split-and-one-global-merge"></a>
<a id="explicit-registration-and-contract-population"></a>
<a id="review-tier-and-design-first-rules"></a>
<a id="explicit-mid-task-scope-addition"></a>
<a id="active-instruction-layer-boundary"></a>

Current detail is owned by the [Task operation specification](task-operation-specification.md#current-m25-select-split-merge-register-contract).

<a id="current-schema-v21-verification-ledger-and-bundle-contract"></a>

## Current Schema-v22 Verification, Ledger, And Bundle Contract

This section defines current post-publication product behavior. It does not
rewrite the immutable v0.10.0 publication record or claim a later published
artifact identity. Schema v20 retains schema-v18 capture, the 21st public
command leaf, schema-v19 completion Bundles and Evidence JSON compatibility, and
publicly activates the existing migration-20 storage foundation plus the
Bundle-v2 null-Runner writer and format-v2 Evidence index.
Schema v22 retains the schema-v21 Runner basis protocol, removes retired
Analyzer reservations, and writes source-22/v2 native Bundles while retaining
source-19/20/21 sealed history unchanged.
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
the retired `derived_analysis` source is not admitted by current schema v22.
The schema-v20 Runner writer
is active only for the audit graph defined below and remains
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
characters. `verification_subject` is tool-owned and has exactly these keys:

```text
basis_version kind authority_snapshot_id verification_criterion_id
legacy_caller_label
```

Its closed compatibility matrix is:

| Durable row | Subject basis | Snapshot / criterion | Public subject |
|---|---:|---|---|
| Receipt or cycle migrated from schema v17 | `0` | both null | Receipt kind `legacy_caller_label` with the unchanged stored label; a cycle gains no inferred label or subject |
| Native Receipt | `1` | both non-null and equal to the capture-version-1 target binding | kind `task_verification_criterion`, the two IDs, and null legacy label |
| Native cycle with nonempty verification | `1` | both non-null and equal to its qualifying subject-v1 Receipt | the linked Receipt remains required and `pass/full` on the manual branch |
| Native cycle with trimmed-empty verification | `1` | both null | no Receipt or subject is invented |
| Exact partial legacy-reopen bridge | `0` | both null | its existing verification-basis-v0 behavior is unchanged |

The subject discriminator is independent of cycle
`verification_basis_version`; a valid v17 native cycle keeps verification
basis 1 while receiving subject basis 0/null/null. The retained physical
`command_label TEXT NOT NULL` column receives the unchanged caller label only
for basis-zero Receipts and the fixed internal compatibility value
`taskgov-owned-verification-subject-v1` only for basis-one Receipts. That value
is never caller input, a public label, an Evidence Reference or Bundle field,
or a digest input.

The public basis-zero subject is exactly version `0`, kind
`legacy_caller_label`, null snapshot/criterion IDs, and the unchanged label.
The basis-one subject is version `1`, kind `task_verification_criterion`, the
two non-null IDs, and null `legacy_caller_label`. A native Bundle uses a
separate four-key subject object containing only `basis_version`, `kind`,
`authority_snapshot_id`, and `verification_criterion_id`; because only basis
one is admitted, the legacy-label key is absent rather than null. Parent Task,
Contract, and target objects complete the compound identity.

`result` is exactly `pass`, `fail`,
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

For a branch that requires a Receipt, the normal order is: finish exact material,
set the existing review target, and retain its returned generation and closed
route. Only `verification_route=receipt_required` runs the governed verification
against that material and records the Receipt with that generation as the
expected basis. `not_required` and `runner_pass` proceed without that run or
Receipt; `blocked` and unexpected route/code pairs stop closed. The default
Tier-2 no-finding manual/fallback bound is ten governance calls, or eleven when
Effort Advisory is mechanically enabled; a Receiptless Runner pass is one call
lower.

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
credential, or free-form coverage prose. A gate-ineligible version-0 graph
created by the audit-only schema-v20 Runner remains separate: it neither
creates nor qualifies a Receipt and cannot satisfy the manual verification or
completion gate. Current gate-eligible version-1 Runner selection is governed
only by the shared schema-v21/v22 three-branch matrix below. Approved exceptions, result-file import,
configured runners that create, import, or qualify Receipts, signatures, and
debug retention are outside this initial Receipt contract.

### Verification Receipt Eligibility And Manual Completion

A Receipt is exact-current only when all of its project, Task, Contract
revision, verification-expectation digest, and complete source-revision tuple
equal the locked current values. All other Receipts remain append-only audit
history and never reactivate.

The current explicit `--verification-complete` assertion remains required for
every done transition. This section defines the manual Receipt arm consumed by the
schema-v21/v22 three-branch selector under Current Schema-v22 Persistence Contract.
When Task `verification` is empty on marker `0`, no Receipt is required and the
current attestation behavior is preserved. When the selector chooses the manual
arm for nonempty verification, completion additionally requires the unique
exact-current Receipt to have `result=pass` and `scope_coverage=full`. A missing
Receipt is `verification_receipt_required`; any other result/coverage
combination is `verification_receipt_blocking`. A qualifying Runner-pass arm is
Receiptless and is governed only by that later matrix. Recovery of the manual
arm requires explicitly setting a fresh target generation and recording fresh
verification; setting an identical
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
 verification text, including the empty string and any preserved whitespace.
 Through schema v20, and for the schema-v21/v22 `caller_attestation` arm, a
 verification expectation whose trimmed text is nonempty additionally requires
 a foreign-key link to the unique qualifying exact-current Receipt. The
 schema-v21/v22 `not_required` and `runner_observation` arms require a null Receipt
 link and are instead constrained by the current tagged union below.
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
and target checks, then applies the schema-v21/v22 three-branch selector before
review-receipt sufficiency. When that selector chooses the manual Receipt arm, the
missing/blocking Verification Receipt gate retains these readiness codes and
fixed messages:

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
the current schema-v22 Runner selector; and same-generation uniqueness. The fixed
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

For an expectation empty after trimming on marker `0`, the gate is
`required=false`, `satisfied=true`, with both nullable fields null. For a
nonempty expectation on a non-done Task, no target yields
`review_target_required` and a capture-version-0 target yields
`evidence_basis_stale`. The manual Receipt arm then yields Receipt-required,
Receipt-blocking, or satisfied with the qualifying Receipt ID. Marker-`2`
targets instead use the shared schema-v21/v22 matrix below: only the exact closed
no-launch fallback delegates to those manual Receipt values, while an exact qualifying
Runner pass is satisfied with a null Receipt ID. A legacy done Task whose matching
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
panel or snapshot field. The Viewer accepts source schemas through v22 while
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

<a id="schema-v19-bundle-foundation-schema-v20v21-native-writer-and-evidence-json"></a>

### Schema-v19 Bundle Foundation, Schema-v20/v21/v22 Native Writer, And Evidence JSON

Migration 19 `completion_evidence_bundles` adds immutable criterion links, Bundle membership and Finding snapshots, completion Bundles, cycle `evidence_basis_version`/bundle linkage, and Evidence projection state. Every existing cycle becomes version 0 with null bundle ID; migration creates no historical Bundle or link and projects that absence only as `legacy_unknown`.
Every native schema-v19 completion atomically inserts one version-1 Bundle with
its cycle, Task update, event, links, selected gate evidence, Finding snapshots,
and projection-generation advance. Schema v20 and the marker-zero schema-v21
baseline instead insert one version-2 Bundle whose basis is
`caller_attestation` with the qualifying Receipt for nonempty verification or
`not_required` with no Receipt for trimmed-empty verification, and its Runner
observation is null. The current schema-v22 writer retains both branches and additionally
admits the exact qualifying schema-v21-protocol `runner_observation` branch, reusing its
existing Runner Reference and criterion link as Bundle members. The sole partial
legacy reopen bridge stays version 0/null and advances only that generation. A
Bundle is complete or the completion fails before write; its canonical payload
is capped at 16 MiB.

Bundle v2 adds exactly the root `verification_basis` object and
`runner_observation` field to the v1 payload. For the caller-attestation and
not-required branches,
`verification_basis` has exactly `basis_version=1`, the derived `kind`, the
matching nullable `verification_receipt_id`, and
`runner_observation_id=null`; `runner_observation` is null. Its envelope uses
`format_version=2` and digest domain
`taskgov-completion-evidence-bundle-v2\0`. The preserved v1 envelope, payload,
domain, bytes, and digest are unchanged.

Evidence JSON is a deterministic one-way SQLite projection. Canonical sorted-key compact UTF-8 JSON uses integer-only JSON values where numeric, preserves valid Unicode without normalization, and ends each file with one LF. A schema-v20-through-v22 index uses envelope `format_version=2`, digest domain `taskgov-evidence-index-v2\0`, and adds exactly nullable `bundle_format_version` to each entry: null for `legacy_unknown`, 1 for a preserved v1 Bundle, and 2 for a native v2 Bundle. Native entries reference `bundles/<completion-evidence-bundle-id>.json`; the projection may therefore reference preserved source-19/v1 and source-20/21/v2 Bundles alongside new source-22/v2 Bundles without rewriting existing payload bytes or digests. The index reports its actual database source schema. Pre-v19 entries are `legacy_unknown` with null Bundle/file fields.
The index includes every cycle, is ordered by Task ID, ordinal, and cycle ID, and is capped at 100,000 entries and 64 MiB without truncation. Publication flushes immutable Bundle files and atomically replaces `index.json` last; SQLite remains canonical, unreferenced files are ignored, and JSON is never imported or used to repair the database.
Contention or failure preserves the last-good index and committed Task result, leaves projection due, and adds only `evidence_projection_deferred` or `evidence_projection_failed`. Setup is the sole explicit repair; doctor only reports stored projection facts. Evidence JSON exposes no Evidence command, custom path, Viewer field/UI, browser launch, server, watcher, or network action and invokes neither Analyzer nor Runner. A current schema-v22 Runner-backed Bundle projects only the already-sanitized stored observation fixed below; publication adds no normal-loop call.

### Assurance, Evidence References, And Finding Snapshots

Every Evidence Reference has exactly one assurance class and an independent
versioned producer:

```text
assurance_class = machine_observed | bound_attestation |
  deterministically_derived | external_reference | legacy_unknown
producer_class = taskgov_core | taskgov_git | trusted_caller |
  legacy_migration | external_system | verification_runner
producer_version = positive integer
```

`machine_observed` means a bounded deterministic component directly observed
the stated local process or Git-object fact, not that taskgov authenticated the
machine, environment, user, or external meaning. `bound_attestation` is a
trusted caller assertion bound to the exact Task, Contract, target, and
generation. `deterministically_derived` is a pure versioned derivation that
cannot be stronger than its sources. `external_reference` retains an external
identity without claiming its content, existence, authority, or semantics.
`legacy_unknown` is irrecoverable absent origin; migration and projection must
not fill or strengthen it. Retired `llm_derived` and `batch_analyzer` occur only
in old-schema compatibility/rejection vocabulary, not the current allow-lists.
Inference never satisfies a verification, review, completion, or release gate.
Producer values are labels, not authentication, signatures, process identity,
independence, or authority.

The v1 source dispatch is exact and uses producer version 1:

| Source kind/state | Assurance / producer | Immutable source projection | Current criterion relation |
|---|---|---|---|
| `artifact_manifest/complete_git` | `machine_observed/taskgov_git` | manifest ID/state/object format/comparison base/entry count/digest/null omission | acceptance `completion_basis`, when present |
| `artifact_manifest/opaque_target/diff_fingerprint` | `bound_attestation/trusted_caller` | manifest ID/state/target kind/digest/`artifact_content_not_observed` | acceptance `completion_basis`, when present |
| `artifact_manifest/opaque_target/external_revision` | `external_reference/external_system` | the same closed opaque projection | acceptance `completion_basis`, when present |
| `verification_receipt/recorded` | `bound_attestation/trusted_caller` | Receipt ID, subject basis and IDs, result, duration, coverage, creation time; no legacy label or compatibility value | verification `verification_attestation`, exactly once |
| `review_receipt/recorded` | `bound_attestation/trusted_caller` | Receipt ID, reviewer key, kind, verdict, summary, approval, creation time, and native v1/null provenance | acceptance `review_assessment` for each selected qualifying Receipt, when present |
| `review_finding/recorded` | `bound_attestation/trusted_caller` | Finding ID, Receipt ID, severity, original summary, creation time | acceptance `review_finding` for current-generation Findings, when present |
| `completion_evidence/git_commit` | `machine_observed/taskgov_git` | cycle ID/time and exact six-field completion evidence | acceptance `completion_basis`, when present |
| `completion_evidence/external_revision` | `external_reference/external_system` | the same closed completion projection | acceptance `completion_basis`, when present |
| `completion_evidence/commit_not_required` | `bound_attestation/trusted_caller` | the same closed completion projection | acceptance `completion_basis`, when present |
| `runner_observation/recorded` | `machine_observed/verification_runner` | the closed sanitized Runner observation projection | verification `runner_observation` only for the active gate-eligible branch |

Each `evidence_reference` stores exact project/Task/Contract ownership,
authority snapshot, nullable criteria, complete target tuple, source ID/state,
assurance/producer/version, nullable completion-cycle ID, and digest. Criteria
must equal the snapshot links; Verification Receipt references require
subject-basis one and its matching verification criterion; only completion
evidence has a cycle ID; and a Finding copies its Receipt binding. Callers
supply none of the dispatch, assurance, producer, binding, or relation.
Evidence Reference IDs are
`tg_evidence_reference_<16-lowercase-hex>`.

The reference digest is SHA-256 over
`taskgov-evidence-reference-v1\0` plus canonical JSON containing the exact
source kind/state and projection, every required/nullable binding,
assurance/producer/version, and no random reference ID or creation time. A
Finding reference excludes `status`, `resolution_summary`, and `resolved_at`;
resolution does not mutate or supersede it. A criterion link copies its source
class and producer exactly; no link, Bundle, projection, analysis, or Runner
operation transitively upgrades assurance.

Criterion links use IDs `tg_criterion_evidence_link_<16-lower-hex>` and exactly
`verification_attestation|review_assessment|review_finding|completion_basis|
runner_observation`. Construction is mechanical: when
acceptance exists, the current manifest and completion evidence receive
`completion_basis`, selected qualifying Review Receipts receive
`review_assessment`, and current-generation Findings receive `review_finding`;
when verification exists, its selected manual Receipt receives
`verification_attestation`, or the gate-eligible Runner Reference receives
`runner_observation`. Absent criteria omit their links without removing valid
Bundle members. Current schema v22 has no `derived_analysis` Reference/link
reservation or writer; old-schema DDL remains available for compatibility.

A Bundle Finding snapshot contains all current-target-generation Findings and
all earlier high/medium Findings, excludes earlier low Findings, and orders by
target generation, creation time, then Finding ID. It has exactly
`review_finding_id`, `review_receipt_id`, `target_generation`, `severity`,
`summary`, `status`, `resolution_summary`, `created_at`, `resolved_at`,
`evidence_reference_id`, `assurance_class`, `producer_class`,
`producer_version`, and `digest`. Native Findings carry their Reference and
`bound_attestation/trusted_caller/1`; pre-v18 Findings have a null Reference and
`legacy_unknown/legacy_migration/1`, create no link, and add
`historical_finding_reference_absent` once. The snapshot digest is SHA-256 over
`taskgov-completion-bundle-finding-snapshot-v1\0` plus canonical JSON of every
preceding field except `digest`.

Every native completion inserts exactly one immutable Bundle with ID
`tg_completion_evidence_bundle_<16-lowercase-hex>` in the same transaction as
its cycle, Task update, event, and source-generation advance. The complete
Bundle-v1 omission vocabulary, in order, is exactly
`acceptance_criterion_absent`, `verification_criterion_absent`,
`artifact_content_not_observed`, and
`historical_finding_reference_absent`. They respectively mean revision-zero
Contract acceptance, trimmed-empty Task verification, an opaque target, and at
least one selected pre-v18 Finding with unknown Reference provenance. Empty
Finding arrays and tier-derived Receipt counts are represented directly, not as
omissions. Pre-v19 cycle absence is represented only by an index
`legacy_unknown` entry and never by a native Bundle. Reopen validates but never
edits a prior cycle or Bundle; later completion creates a new one and historical
Bundles never satisfy a current gate.

### Canonical Evidence Bundle And Index Formats

The only generated paths are
`state/current/evidence/index.json` and
`state/current/evidence/bundles/<completion-evidence-bundle-id>.json` beneath
the ignored canonical package state. There is no public command, custom path,
import, endpoint, watcher, background worker, or Viewer Evidence UI.

Canonical JSON accepts only null, Boolean, string, integer, array, and object;
rejects floats, nonfinite values, lone surrogates, duplicate keys, and invalid
Unicode; emits shortest-decimal integers and lowercase literals; uses the short
escape for quote, backslash, and supported controls and lowercase `\u00xx` for
other JSON controls; does not escape slash or valid non-ASCII scalars; orders
object keys by Unicode code point; emits no insignificant whitespace; and never
normalizes text. Durable files are BOM-free UTF-8 with one terminal LF.

A preserved Bundle-v1 file is exactly this no-extra-key envelope plus LF:

```text
{"bundle_digest":"sha256:<64-lowercase-hex>","format_version":1,"payload":<bundle-payload>}
```

Its payload has exactly these keys and values:

| Key | Exact value |
|---|---|
| `artifact_manifest` | `{artifact_manifest_id,state,object_format,comparison_base,digest,omission_code,entries}`; nullable fields are null and entries are exact ordinal rows `{ordinal,kind,old_path,new_path,before_mode,before_object_id,after_mode,after_object_id}` |
| `authority_snapshot` | `{authority_snapshot_id,generation,digest}` |
| `bundle_id` / `bundle_version` | Bundle ID / integer `1` |
| `completion_cycle_id` / `cycle_ordinal` / `sealed_at` | cycle ID / positive integer / canonical UTC string |
| `completion_evidence` | `{kind,revision,reason,external_revision_approved,completion_commit_required,completion_commit_hash}` with flags as `0|1` |
| `contract` | `{revision,specified,scope,acceptance,constraints,authority_ref}` with Boolean `specified` |
| `criteria` | `{criterion_id,kind,text,digest}` rows ordered acceptance then verification; absent criteria have no row |
| `criterion_links` | objects `{criterion_evidence_link_id,criterion_id,evidence_reference_id,relation,assurance_class,producer_class,producer_version}` ordered by criterion kind, criterion ID, fixed relation order, Reference ID, then link ID |
| `evidence_references` | objects `{evidence_reference_id,source_kind,source_state,source_id,assurance_class,producer_class,producer_version,contract_revision,authority_snapshot_id,acceptance_criterion_id,verification_criterion_id,target_kind,target_value,target_base_revision,target_generation,completion_cycle_id,digest}` ordered by fixed source-kind order, source ID, then Reference ID; nullable bindings are null |
| `finding_snapshots` | exact snapshot rows in the Finding order above |
| `omissions` | unique strings in fixed omission order |
| `project_id` | project ID |
| `review_receipts` | selected objects `{review_receipt_id,reviewer_key,receipt_kind,verdict,summary,user_approved,created_at,review_provenance}` in qualifying gate-basis order; `user_approved` is `0|1` and provenance is v1 or null |
| `source_schema_version` | integer `19` |
| `target` | `{kind,value,base_revision,generation,capture_version}` |
| `task` | `{task_id,title,description,review_tier,verification}` |
| `verification_receipt` | null without a verification criterion; otherwise `{verification_receipt_id,verification_subject,result,duration_ms,scope_coverage,created_at}` with subject exactly `{basis_version,kind,authority_snapshot_id,verification_criterion_id}`, basis 1/kind `task_verification_criterion`, and no legacy-label key |

The fixed relation order is
`verification_attestation,review_assessment,review_finding,completion_basis,
runner_observation`; the fixed Reference source-kind order is
`artifact_manifest,verification_receipt,review_receipt,review_finding,
completion_evidence,runner_observation`. Text tie-breakers use
unsigned UTF-8 order; generations and ordinals compare numerically. The v1
Bundle digest is SHA-256 over
`taskgov-completion-evidence-bundle-v1\0` plus canonical payload bytes without
LF. The file digest hashes the complete envelope and LF.

A preserved v1 index is the exact no-extra-key envelope
`{"format_version":1,"index_digest":"sha256:<64-lowercase-hex>","payload":<index-payload>}`
plus LF. Its payload has exactly `source_schema_version=19`, `project_id`,
nonnegative `projection_generation`, `bundle_count`, `legacy_count`, and
`entries`. Each entry has exactly `task_id`, `completion_cycle_id`,
`cycle_ordinal`, `bundle_state`, `bundle_id`, `bundle_file`, `bundle_digest`,
`file_digest`, and `sealed_at`. Native entries have state `native` and five
non-null Bundle/file/seal fields; `legacy_unknown` entries have all five null.
Entries order by Task ID, cycle ordinal, then cycle ID and counts equal their
states. The index digest is SHA-256 over `taskgov-evidence-index-v1\0` plus
canonical payload bytes without LF.

Current index format v2 preserves that payload and ordering and adds exactly
nullable `bundle_format_version` to each entry: null for `legacy_unknown`, 1
for preserved Bundle v1, and 2 for Bundle v2. It uses domain
`taskgov-evidence-index-v2\0`. Bundle v2 preserves every v1 member and adds only
the closed `verification_basis` and nullable `runner_observation` roots defined
by the shared schema-v21/v22 verification-basis contract. Existing v1/v2 bytes and
digests are immutable.

Publication captures one coherent DB generation, validates all selected rows,
writes and flushes immutable Bundle files first, and atomically replaces the
index last. The index is the filesystem commit point. Consumers ignore
unreferenced files and reject wrong identity/version/project/digest. Taskgov
also compares index generation with canonical SQLite; a standalone consumer
can prove only file self-consistency and declared generation. The index covers
every cycle, has at most 100,000 entries and 67,108,864 UTF-8 bytes, and is
never truncated. A Bundle is capped at 16,777,216 bytes.

## Recovery Candidate Validity Contract

Every recognized managed recovery candidate first passes physical-file, SQLite,
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

The current classifier admits 500 for schema v17 and 1,000 for schema v18-v22,
rejecting the next character in each source schema. It does not generalize
local rejection to another field
or to structural, identity, lineage, metadata, or TOCTOU failure.

## Stored Task Read And Privacy Contract

Current detail is owned by the [Task operation specification](task-operation-specification.md#stored-task-read-and-privacy-contract).

## Stored Contract Pointer Integrity Contract

Current detail is owned by the [Task operation specification](task-operation-specification.md#stored-contract-pointer-integrity-contract).

## SQLite, Migration, And Concurrency

### Initialization And Supported Schemas

`setup` is the sole public initializer and migrator. No Task, handoff, review,
doctor, read, or write command creates/migrates a missing/old database.
Missing state is `db_not_initialized`; supported older state is
`migration_required`; a newer schema is `schema_too_new`. Old binaries reject
newer state and never downgrade/write it.

Fresh setup creates schema v22. Structurally complete contiguous source schemas
v1-v21 are setup-only migration inputs; v22 is idempotent current state.
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
| v21 | verification Runner gate-basis tags using the existing schema-v20 structures |
| v22 | retired Analyzer reservation cleanup in the existing Evidence/Bundle tables |

Each migration is transactional, idempotent, rollback-tested, validates
contiguous history and required objects/rows, preserves project/business IDs
and durable records, and passes `quick_check` and foreign keys. Acceptance
retains the realistic 12-Task/191-event fixture and historical completion/
review trace through every supported source version. No migration parses
private prose to invent structure.

### Schema-v20 Foundation And Admission

The migration implementation retains one non-public helper restricted to an
explicitly injected database path. It migrates one caller-owned disposable v19
database in
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
gate-ineligible version `0`. The migration owns only the complete physical DDL
and storage-parent integrity; plan, process, observation, cleanup, and service
admission are separate current Runner subsystem boundaries.
The Bundle table rebuild does not preserve or replay arbitrary caller DDL. A
persistent index or trigger whose name is not migration-owned but whose
`sqlite_master.tbl_name` is `completion_evidence_bundles` is unsupported
attached residue: successful migration removes it with the old v19 Bundle
table, while transaction rollback restores it. Unrelated standalone objects
not attached to the rebuilt table remain unchanged.
Marker-only, partial-owned-object, same-version owned-object drift, a known
later marker, busy/contention, integrity, or foreign-key failure is fail-closed
and leaves no partial migration.

The supported schema-v20 compatibility foundation retains the
Bundle-v2 null-Runner payload/serialization/digest writer, and schema-v20
compatibility for Evidence JSON, Viewer, and managed backup/recovery. The
schema activation itself creates no Runner resolution, attempt, sandbox event,
observation, Evidence Reference/link, Bundle member, or Runner projection.
Canonical database migration occurs only through explicit public setup.

Public admission distinguishes a complete v19 source from a hybrid before any
mutation. A database declared as v19 but containing any recognized v20-owned
table, explicit index, trigger, or column fails closed in the canonical
resolver, setup inspection/migration, Viewer, and managed backup/recovery paths.
Recognition follows SQLite's case-insensitive identifier equality: any catalog
object occupying a v20-owned table, explicit-index, or trigger name is a
collision, while a column marker is scoped to its designated parent table.
Generated columns are column markers under the same rule.
Complete-v20 migration admission accepts either empty Runner tables and
Reference/link sets or the exact audit-only Runner graph below.
It rejects a malformed, duplicate, foreign-owned, partially linked, or
gate-eligible Runner graph. Every Task Runner-basis marker remains zero and
every cycle/Bundle Runner-observation pointer remains null; native Bundle-v2
`caller_attestation` and `not_required` verification basis remains valid.
This check occurs before any database or sidecar write, migration backup,
recovery copy/publication, Viewer publication, or managed-backup write. A complete v19 source alone may invoke migration
20; a complete v20 source continues through migrations 21 and 22, and a complete
v21 source invokes migration 22. Exact v22 receives validation-only reentry. Unrelated extra
objects retain the existing policy, including deliberate removal of
unsupported unowned indexes/triggers attached to the rebuilt Bundle table and
preservation of unrelated standalone objects.

Schema v20 is an audit-only Runner foundation and never becomes a qualifying
Runner gate basis. The current schema-v22 delta below retains the schema-v21
qualifying Runner protocol with explicit manual fallback.

<a id="current-schema-v21-persistence-contract"></a>
<a id="current-schema-v22-persistence-contract"></a>

### Current Schema-v22 Persistence Contract

Schema v22 is the public schema constant and setup target. Setup reaches it
through the existing ordered migrations from complete v1-v21 sources; v20
continues through 21 and then 22 rather than returning early. Exact-v22
reentry is validation-only. Ordinary commands never migrate or repair state.

Migration 22 is exactly `evidence_reservation_cleanup`. It rebuilds only
`evidence_references`, `criterion_evidence_links`, and
`completion_evidence_bundles`, restores their owned indexes/triggers and the
coupled Evidence cycle guard, and retains all business columns and the
35-table/42-index/59-trigger inventory. Current allow-lists remove
`derived_analysis` source/relation, `llm_derived` assurance, and `batch_analyzer`
producer; `deterministically_derived` and all other valid Evidence remain.
Complete source validation rejects unexpected retired-value rows and unowned
indexes/triggers attached to any of those three rebuilt tables before rebuild;
it never deletes, converts, or invents a replacement for such rows or objects.
Unrelated objects retain the established preservation policy.

Valid business rows, IDs, relations, provenance, completion history, and sealed
source-19/v1 and source-20/21/v2 Bundle payloads/source versions/bytes/digests
are preserved. The Bundle tagged union adds source 22/format 2 with the same
three basis arms as source 21 below. New native completion obtains source 22
from its locked database basis for both the stored Bundle and its payload;
the native cycle guard requires that source. No caller chooses it. The format-2
index reports the actual container schema 22 and may change bytes/digest while
referencing unchanged old Bundles. A source-22 Bundle is not admitted in a
physical schema-21 container. No format, digest domain, or evidence assurance
is upgraded by migration or projection.

Migration follows the existing foreign-key-off/legacy-alter-on transaction,
exact row/object preservation, integrity checks, marker-last, and connection-
setting restoration pattern. It rechecks preserved rows after the marker and
full v22 validation before commit, including effects of admitted unrelated
triggers. Failure restores logical schema/data, not byte-identical SQLite
files. Exact-v22 reentry rejects unexpected attachments and migration temporary
residue without repair. Setup retains its pre-migration managed backup and
matched package/database/artifact rollback boundary; older code rejects v22.

Current Task/Receipt/completion and Runner selection retain the schema-v21
protocol below, including audit-only old graphs, no-relaunch recovery, live
implementation-drift invalidation, and self-contained done history. Setup,
doctor, backup/recovery, resolver/relocation, and Viewer admit v22 through their
existing boundaries. Recovery extends the same verification-only candidate-
local rejection rule through source 22; every other structural Task/Runner/
Bundle fault remains set-fatal. Viewer remains snapshot v4, accepts v5-v22,
validates and discards Evidence/Runner internals, and changes no public field
or UI. No CLI shape, Skill trigger/procedure/call, config, Runner runtime, gate,
or general migration framework is added.

<a id="schema-v21-persistence-contract"></a>

### Schema-v21 Persistence Compatibility And Shared Runner Protocol

The following retains the exact schema-v21 migration/storage contract and the
Runner protocol inherited unchanged by current schema v22. Source-21 Bundle
and migration statements describe that supported predecessor, not the current
setup target or a relabelling of retained evidence. The v22 delta above owns
current source identity, narrowed reservations, and consumer upper bounds.

Migration 21 is exactly `verification_runner_gate_basis`. It adds no table,
column, or index. It reuses `tasks.review_target_runner_basis_version`, the four
schema-v20 Runner tables and their `gate_eligibility_version` columns, the
existing completion-cycle and Bundle verification-basis columns, and the
existing Runner Evidence Reference and criterion-link relation. The Task marker
uses `0` for an ordinary/manual or shadow target and `2` for a target selected
for the schema-v21 Runner branch. A schema-v21 Runner resolution, attempt, and
observation graph uses one matching gate-eligibility version throughout: `0`
remains audit-only and `1` is the sole potentially qualifying version. The
physical discriminator widening, existing-table rebuilds, owned triggers, and
complete owned-object inventory are specified by the design; the migration
introduces no second storage model or parallel gate table.

A complete-v20 source is exactly the currently admitted schema-v20 state: its
owned schema and history are complete, every Task Runner marker is `0`, every
Runner row is gate-eligibility version `0`, every cycle and Bundle Runner
pointer is null, and any Runner rows form only an exact admitted audit-only
audit graph. Native v20 Bundles use only `caller_attestation` or `not_required`.
Any v21 marker, gate-eligibility version `1`, `runner_observation` completion
basis, partial owned object, malformed or foreign graph, duplicate, or unknown
later marker makes a declared v20 database a hybrid and blocks before any
database, backup, recovery, Viewer, or sidecar write.

The current schema-v20 extra-object policy remains the admission boundary.
Therefore an unowned index or trigger attached to
`completion_evidence_bundles` makes the v20 source incomplete and is rejected
before migration 21; version 21 does not provide a second deletion path for
that Bundle residue. An unowned index or trigger attached to one of the three
schema-v20 Runner tables is admitted as an unrelated extra object, retires with
that rebuilt table on successful migration, and returns on rollback without
arbitrary DDL replay. Unowned objects attached to the unreconstructed sandbox-
event table and unrelated standalone objects are preserved. Exact v21 reentry
rejects an unowned attachment to any of its four rebuilt tables.

The v20-to-v21 migration preserves every business row and stable ID and keeps
every existing Runner and Bundle relation unchanged. It does not set a Task
marker to `2`, change any gate-eligibility version from `0`, attach an existing
observation to a cycle or Bundle, synthesize a Reference or link, or rewrite an
existing Bundle payload, byte count, or digest. Schema-v20 Runner rows are
permanently audit-only; neither migration, reentry, recovery, nor a later target
may promote, copy, or reinterpret them as qualifying evidence. A fresh v21
database uses the same owned schema with empty Runner state. Same-version
reentry performs exact history, owned-schema, tagged-graph, integrity, and
foreign-key validation only, with no reconciliation or backfill.

Migration is one `BEGIN IMMEDIATE` transaction. Any rebuild, copy, history,
owned-object, graph, integrity, foreign-key, contention, or validation failure
rolls back to the complete logical v20 schema and rows with no migration-21
marker or partial v21 state; SQLite file-byte identity is not promised. After a
successful migration there is no in-place downgrade. Product rollback restores
one matched v20 package, database backup, and managed artifacts before v20 code
runs. Managed backup and recovery preserve and validate the complete source
schema and tagged graph, never include the private Runner attempt tree, never
upgrade an audit-only row, and apply migration only through explicit setup.
Schema v21 reuses the complete schema-v20 stored-Task, Contract-pointer,
privacy, and relationship rules, including the exact 1,000-code-point Task
`verification` capacity. Recovery applies the same candidate-local rule to
source schemas 18 through 21: only that field's privacy or capacity failure
rejects the candidate locally. Wrong storage class, another Task field, a
relationship fault, or a Runner/Bundle graph fault remains whole-set fatal.

Every native schema-v21 completion writes
`task_completion_cycles.verification_basis_version=1` and
`task_completion_cycles.evidence_basis_version=1`, plus one Bundle with
`source_schema_version=21` and `bundle_version=2`. The cycle and Bundle carry
the same basis kind and nullable Receipt/Runner-observation IDs, and the Bundle
JSON mirrors them with `verification_basis.basis_version=1`. Schema v21 admits
exactly these completion-basis branches:

| Branch | Current target and qualifying basis | Completion cycle and Bundle v2 |
|---|---|---|
| manual verification | Marker `0`, or marker `2` only after its exact-current gate-eligibility-version-`1` Runner result is a closed no-launch `m21_fallback`; the existing exact-current manual Receipt is `pass/full` | `kind=caller_attestation`, the qualifying Receipt ID, and null Runner-observation ID |
| verification not required | Trimmed-empty verification on a marker-`0` target | `kind=not_required`, and null Receipt and Runner-observation IDs |
| Runner verification | Marker `2` and one exact-current gate-eligibility-version-`1` observation for the complete selected plan, launched with `route=runner`, `outcome=pass`, null reason, every step completed in order, and all cleanup/privacy proofs satisfied | `kind=runner_observation`, null Receipt ID, and the qualifying Runner-observation ID |

The closed no-launch case is only the existing audit-graph terminal shape:
`route=m21_fallback`, `launch_state=no_launch`, `outcome=blocked_prelaunch`,
reason `runtime_unavailable` or `process_setup_failed`, `complete_plan=0`, and
proved process, handle, output-discard, lifecycle, and private-tree cleanup.
Either marker-`2` terminal branch requires exactly one resolution, attempt,
cleanup event, observation, Runner Evidence Reference, and
`runner_observation` verification-criterion link at the exact current target,
with their complete ownership, Contract, criterion, target, plan, material,
implementation, policy, digest, and parent bindings equal.
Marker `2` is only a closed branch discriminator; it never proves success by
itself. A pending or inconsistent graph blocks. Once the selected exact-current
Runner branch launched, every timeout, cancellation, nonzero, incomplete-plan,
cleanup/privacy failure, or other non-pass blocks that target and cannot be
overridden by a manual Receipt. Only an admitted closed no-launch result whose
route is `m21_fallback` may take the manual branch. Every other structurally
valid terminal, whether launched or no-launch, blocks and cannot be overridden
by a Receipt. Unsupported, untrusted, external, manual, or visual work never
receives marker `2` and stays on the existing manual Receipt route.

The schema-v21 storage validator recognizes the complete tagged union. Marker
`2` and a gate-eligibility-version-`1` graph may be created only while capturing
a fresh exact target through the current Runner service; no migration or
ordinary manual writer synthesizes a Runner completion basis. The manual
Receipt remains the sole gate for marker `0`. A structurally valid marker-`2`
target is never silently downgraded, consumed by a marker-`0` writer, or
rewritten. The current service never retrofits an existing marker-`0` target or
schema-v20 observation.

Contract revision, verification criterion, review-target tuple or generation,
artifact manifest, plan/selected-entry basis, target material, implementation
identity, or qualifying observation drift makes the Runner basis non-current.
The existing invalidation and reopen paths clear the current target's Runner
marker with the target and require a fresh target generation; they do not
delete or mutate an immutable Runner graph, completion cycle, Bundle, Reference,
or link. Historical and recovered observations remain history and never
reactivate by value equality.

Schema-v21 completion retains Bundle format version 2 and digest domain
`taskgov-completion-evidence-bundle-v2\0`. The two manual branches keep their
current v2 payloads. The Runner branch uses the already reserved four-field
`verification_basis` object with `basis_version=1`,
`kind=runner_observation`, null `verification_receipt_id`, and the exact
`runner_observation_id`; its existing `runner_observation` field contains only
the already defined sanitized Runner observation source projection. Bundle v1
and existing Bundle v2 bytes and digests remain immutable. Evidence Index
format/domain v2 and `bundle_format_version=2` remain unchanged. Viewer snapshot
v4 extends source validation through schema v21, validates the complete tagged
relations, and discards Runner-only data without adding a field, panel, or UI
behavior.

The Runner Bundle allow-list is exactly the existing sanitized observation
source projection used by its Evidence Reference. Raw output, argv, environment,
credentials, exception or stack text, private paths, plan bytes, target bytes,
and arbitrary provider data remain prohibited. No caller-supplied subject,
result import, Analyzer output, older observation, or public override can
satisfy this branch. Schema-v21 support adds no public command leaf, argument,
Evidence JSON member, Viewer UI, or Skill trigger. Gate integration adds only
the two closed target-set JSON success fields above; Bundle/Evidence serialization
and projection add no normal-loop call, and no post-target `task show` is added.

The existing `task show.verification_evidence` object keeps its exact keys and
types. `current_verification_subject`, all four counts, and `recent_receipts`
remain Receipt-only projections; no Runner ID or count is exposed. For a
nonempty schema-v21 verification expectation on a live, non-done Task, the
existing gate fields and completion failures use this closed matrix after the
existing missing-target and capture-version checks and before review-receipt
sufficiency:

| Current basis | Existing `gate` values | Receipt-add and completion behavior |
|---|---|---|
| marker `0` | unchanged manual Receipt matrix | unchanged |
| any live marker `2` read by the compatibility selector that does not admit Runner completion | `required=true`, `satisfied=false`, `blocking_code=evidence_basis_stale`, `qualifying_receipt_id=null` | Receipt-add and completion return existing `evidence_basis_stale` / `current evidence basis must be captured again` |
| marker `2` whose structurally valid graph is non-current because any named Contract, target, plan, implementation, policy, or related basis drifted | `required=true`, `satisfied=false`, `blocking_code=evidence_basis_stale`, `qualifying_receipt_id=null` | Receipt-add and completion return the same existing stale code/message before uniqueness or review sufficiency |
| marker `2` with an exact-current pending or cleanup-only graph | `required=true`, `satisfied=false`, `blocking_code=evidence_basis_stale`, `qualifying_receipt_id=null` | Receipt-add and completion return the same existing stale code/message |
| marker `2` with any exact-current structurally valid terminal other than the exact closed no-launch fallback or exact qualifying Runner pass | `required=true`, `satisfied=false`, `blocking_code=verification_receipt_blocking`, `qualifying_receipt_id=null` | Receipt-add returns `evidence_basis_stale`; completion returns existing `verification_receipt_blocking` / `current verification evidence does not satisfy the required result and coverage` |
| marker `2` with the exact-current closed no-launch fallback | the unchanged manual no-Receipt, blocking-Receipt, or qualifying-Receipt values | Receipt-add is allowed and completion uses the unchanged manual errors or qualifying Receipt ID |
| marker `2` with the exact-current qualifying Runner pass | `required=true`, `satisfied=true`, `blocking_code=null`, `qualifying_receipt_id=null` | Receipt-add returns `evidence_basis_stale`; completion proceeds to the existing review gate |

Because Runner branches cannot record a Receipt except after the exact closed
no-launch fallback, a qualifying Runner pass legitimately has zero Receipt
counts while `gate.satisfied=true`. Any exact-current Receipt on another
marker-`2` branch is inconsistent stored state rather than alternate evidence.
A valid done version-one cycle is resolved before the live matrix and replays
its validated stored branch, including when a compatibility reader inspects
later Runner-completion history. Caller attestation keeps the qualifying manual Receipt
projection, not-required keeps the existing empty-expectation projection, and
`runner_observation` revalidates its exact stored graph/Bundle identity and
projects `required=true`, `satisfied=true`, and both nullable gate fields null.
Historical replay never compares that captured implementation identity with the
currently installed package and authorizes no new compatibility-mode Runner completion. A
cycle/Bundle mismatch fails before gate projection.

This selector does not reorder existing argument, Task lookup/status,
expectation, target-existence, expected-generation, or retained-capture checks.
For Receipt-add it runs after those checks and before Receipt uniqueness; for
completion it runs at the existing verification-gate position before review
sufficiency. Current Runner-basis evaluation checks freshness before outcome. Failure
to establish the current installed implementation identity retains the existing
`package_core_modified` or `package_status_unknown` package-inspection result;
after a successful inspection, identity mismatch maps to
`evidence_basis_stale` before outcome mapping.
A malformed Runner/Task graph fails first through existing
`project_state_unreadable`; an invalid cycle/Bundle history relation remains
`completion_history_inconsistent`; and a malformed Receipt remains
`invalid_verification_evidence`. No new public code or message is introduced.

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
transaction leaves no row/event/receipt/Git/target change. A Runner target-set command
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

Relocation failure has status null, required schema 22, empty warnings, one
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
directory and applies the recovery-candidate validity matrix. It chooses the newest
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
`viewer_status`, and `relocation`. `schema_to` is always 22. `schema_from` is safely observed source
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
The Runner path retains exactly the one existing target-set maintenance opportunity
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
eligible on all eight. Backup-only and Viewer-only run overhead versus disabled
is at most 10 seconds, combined Viewer-plus-backup overhead is at most 12
seconds, and each command-position median is below 5 seconds on Windows CI.
Attempt, render, call, byte, and zero-wait limits are hard; timing cannot waive
them.

Windows timing qualification uses one complete non-qualifying warm-up followed
by six measured rounds from fresh copies of the same fixed fixture. The
three-mode qualifier uses every mode permutation once; the two-mode qualifier
uses both orders equally. Every overhead remains paired with the disabled
total from that same round. The mode-specific total budget applies to the
median of the six paired enabled-minus-disabled totals: 10 seconds for
backup-only and Viewer-only, and 12 seconds for combined Viewer-plus-backup.
The strict 5-second budget applies to the median of the six
observations at each mode and write position and requires that median to remain
below 5 seconds; it is not averaged across commands. Diagnostics are bounded to
the fixture and mode, the six paired
overheads, eight command medians, and the maximum raw measured observation.
Functional, count, byte, attempt, render, and call assertions remain separate
hard failures and are never decided by the timing statistic.

Repository pull-request and `main`-push CI validate the complete discovered
inventory and base-lane ownership, then defer exactly the two closed wall-clock
qualification test identities. They continue to execute the separate fixed-
fixture functional and capacity assertions. The timing tests execute and block
only in the explicit manual `workflow_dispatch` release-candidate route, whose
Python 3.12 and 3.14 `all` jobs each retain the unchanged standard-discovery
suite. This is event-aware test selection, not a skip, retry, threshold change,
or fourth lane.

## Static Task Viewer

The current Viewer behavior, snapshot, optional reload, and one-shot UI-state
contracts are owned by the [Viewer specification](viewer-specification.md).
Shared setup, maintenance, stored-read, and completion-history contracts remain
in their respective sections of this document.

## Privacy, Safety, And Stable Errors

Taskgov stores only bounded sanitized summaries, legacy command labels, exit/duration/
status/time metadata, hashes, stable IDs, and explicit structured evidence.
It never stores or emits API keys, credential/session tokens, cookies,
authorization headers, raw provider bodies, private prompts, chat transcripts,
large/raw diffs, automatically captured or free-standing raw stdout/stderr,
full logs, stack traces, environment dumps, review reasoning/bodies,
OS/SQLite exception detail, raw paths in identity metadata, expected/actual
hash pairs, or rejected values. The sole stream-text exception is the bounded,
validated manual Task quotation defined below; it is not automatic capture or
a separate output field.

A native Verification Receipt stores only the fixed internal compatibility
label, closed result, duration, coverage, tool-owned identity/time and
structural subject, and exact Contract/expectation/target binding. A v17 row
retains its sanitized caller label as explicit legacy data. Neither form stores a command body or
arguments, exit code, result body, stream, log, environment, exception,
arbitrary coverage prose, or debug-retention variant.

Current schema-v22 free-form limits not narrowed above are: title 200
characters; description 4,000; stored/read/internal verification and its
derivatives 1,000; explicit public Task add/edit verification 1,000;
tags/reviewer/target/external revision/authority ref
500; note 2,000; event/receipt/finding/resolution/pause/reopen/Contract
change reason 1,000. Secret/header/private-key/password/token/api-key,
traceback, raw stream dump, and large diff patterns are rejected with
`privacy_rejected` before storage. Rejected patterns include bearer tokens,
authorization headers, private-key blocks, `password=`, `token=`, and
`api_key=`. Public read projections revalidate stored content and cross-field
matrices and Contract-pointer relationships through the shared stored-Task
validator before omission or
exposure. Invalid stored Task content returns only
`project_state_unreadable` / `project state could not be read safely`, never
the rejected value or a caller-input error. The existing `lane` and
`blocked_reason` inputs have no numeric
character limit and still use the common privacy guard and state validation.
`lane` is canonicalized by trimming outer whitespace; `blocked_reason` is
retained as supplied after string/privacy checks.

Three nonsecret numeric metadata assignments are not credential assignments:
exact lowercase `max_tokens`, `token_count`, and `password_length`, followed by
`=` and one or more ASCII digits. Spaces or tabs may surround `=`; zero and
leading zeros are valid, with no additional numeric bound beyond the field's
existing text limit. The value ends at end-of-input, whitespace, a backtick,
comma, semicolon, or closing `)`, `]`, or `}`. For example, `max_tokens=4096`,
`token_count=1024`, and `password_length=12` are accepted unchanged by caller,
stored-text, and Evidence validation. This does not exempt prefixed/suffixed or
differently cased keys, colon assignments, quoted numeric strings, signs,
decimals, exponents, or digit-prefixed nonnumeric values. Existing JSON
nonsecret scalar handling is unchanged. All other credential and dump checks
still inspect the entire original input; recognized metadata never hides a
separate credential or makes an ambiguous token/status assignment safe.

The plain-text credential example `Authorization: Bearer <redacted>` is also
accepted unchanged. Only the complete, case-sensitive literal `<redacted>`
qualifies; the `Authorization` and `Bearer` words are case-insensitive.
Spaces/tabs may surround the colon, and at least one space/tab separates
`Bearer` from the placeholder. The header name must not be prefixed by a
letter, digit, underscore, dot or hyphen. The placeholder ends at end-of-input,
whitespace, a backtick, comma, semicolon or closing `)`, `]`, `}`; any other
immediately appended content remains part of the value and is rejected.
Other schemes, assignment forms, quoted JSON values and arbitrary placeholder
words receive no new exemption. Every other detector still checks the entire
original input, including content before and after the example. This recognizes
already-redacted text; it performs no redaction and grants no whole-input bypass.

Task `title` and `description` alone also accept one manual diagnostic
quotation in exactly one of these forms: `<context>: stderr: <quotation>` or
`<context>: stdout: <quotation>`. The delimiter, spaces, and lowercase stream
label are literal. Context and quotation each contain non-whitespace text, and
the complete value contains exactly one such delimiter. The complete value is
one physical line: `str.splitlines()` must return exactly the unchanged value,
so embedded or trailing line-boundary characters do not qualify. The existing
200- and 4,000-code-point field limits remain unchanged.

Every privacy detector still inspects the unchanged complete field. Only the
one outer stream-heading match is admitted, after which the extracted
quotation is checked again with strict raw-output handling and with this
quotation exception disabled. A credential in the context or quotation, a
stack frame visible only at the start of the extracted body, another
raw-output heading, a log or environment dump, or a raw/large diff therefore
still fails. A value with one literal delimiter but empty context or quotation,
or with an embedded or trailing line boundary, remains rejected. Bare headings,
multiple headings, and nonliteral delimiter variants receive no new exception
and retain their prior field-specific behavior, including the existing
benign-title wording allowance. Ordinary multiline descriptions and strict
rejection in all other raw-output fields are likewise unchanged.

The accepted examples are `Investigate failure: stderr: permission denied`
and `Inspect result: stdout: no matching rows`. They are retained unchanged in
Task storage and the existing completion Evidence shape. Review Packet keeps
its existing shape: its Task title may contain the accepted form, while the
validated description remains omitted. This policy adds no Runner capture,
Verification Receipt content, setting, approval step, redaction, schema,
public field, or target-project mutation.

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

## Evidence Interpretation And Retired Analyzer Boundary

Facts, caller declarations, LLM inference, and uncertainty are not
interchangeable. Derived explanations do not satisfy verification, review,
completion, or release gates and cannot upgrade the assurance of their sources.

The M23 Analyzer runtime is retired. `derived_analysis`, `llm_derived`, and
`batch_analyzer` remain only in old-schema compatibility/rejection vocabulary;
current schema v22 removes their unused reservations. Unrelated
`deterministically_derived` uses remain supported. Reservation cleanup preserves
valid durable rows, Evidence formats, canonical byte/digest rules, and Runner gates.
Existing ignored `state/current/analysis/` artifacts remain untouched and inert;
there is no cleanup, import, relocation, or repair path for them.

Independent Evidence reading/validation is retained only in repository tests.
It checks file self-consistency, declared generation, versions, identities,
canonical bytes/digests, relations, and existing bounded semantics/privacy
rules. It does not authenticate authors, prove source-code correctness, or
establish freshness against SQLite. No runtime reader, report schema, renderer,
citation-generation subsystem, or future Reporting adapter is retained. Any
later non-test reader path requires a separate product decision.

## Trusted-Local Verification Runner

<a id="eligibility-plan-and-materialization"></a>
<a id="parent-service-and-audit-graph"></a>

Current detail is owned by the [Runner execution specification](runner-execution-specification.md#trusted-local-verification-runner).

## Current Runner Plan Authoring And Control Contract

The current Runner Plan authoring/control behavior is owned by the
[Runner Plan authoring specification](runner-plan-authoring-specification.md).
The authoring detail links directly to the Runner execution and Task operation
owners. Shared maintenance rules remain in this document.

## Deferred Boundaries

The current product deliberately excludes pagination/search in CLI history,
parent/child Tasks, acceptance checklists, a public command or Skill trigger for
standalone verification-command execution, generic
result/receipt-file import, action aliases, general/manual backup or restore,
custom export, browser launch/server, durable/general browser persistence
beyond the one-shot envelope, external Issue lifecycle/sync until its intake
contract, cross-project profiles, daily network update checks, reviewer
identity/signatures/attestation, and a generic workflow engine.

Deferred features and the retired TG-M20S study result never change current
acceptance, add a normal-loop command, or authorize target/external mutation
until their separately approved implementation and synchronization gates
complete. Active M25 instruction guidance adds none of those capabilities.
