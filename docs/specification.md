# task-governance-tool Current Product Specification

Status: The immutable published product remains v0.10.0/schema v16/Viewer v4
sources v5-v16/20 leaves; its identity is fixed in `docs/release-install.md`.
The current unpublished candidate is v0.13.0 with SQLite schema v22, Viewer
snapshot v4 accepting source schemas v5-v22, and 23 public command leaves.
Its supported behavior includes tool-owned Verification Receipt subjects,
versioned Review provenance, immutable Evidence References and completion
Bundles, deterministic Evidence JSON, and the explicitly opted-in trusted-local
verification Runner with a closed manual fallback. Schema v20 remains a
supported migration source and
audit-only Runner lineage; only fresh gate-eligible evidence under the unchanged
schema-v21 protocol retained by schema v22 may
satisfy the Runner branch. Select-Split-Merge-Register is active only as
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
[validation and review operations](review-completion-specification.md#git-snapshot-and-target-binding). Explicit setup may create the
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

Python 3.12 or newer is required. Ordinary functionality supports Windows,
Linux, and macOS, with representative verification on Windows x86-64,
Ubuntu 24.04 x86-64, and macOS 15 Apple Silicon. Windows retains the Python
3.12/3.14 full-suite policy; additional-OS checks use Python 3.12.
Normal functionality includes setup, the Task lifecycle through manual
verification and completion, Evidence JSON, Viewer, backup, and their applicable
recovery paths. Real-OS checks exercise the physical package; Windows-only
mocks do not establish another OS's support.

OS-dependent operations use the shared/OS responsibility boundary in
[the implementation design](design.md#os-operation-boundary). Existing Task
decisions, SQLite transactions, Evidence construction, fixed state ownership,
and no-write inspection remain with their current owners. There is no public
OS-selection command or per-Task LLM platform choice. No-replace publication
must not silently fall back to an operation that can overwrite a destination.

Public Runner execution supports Windows, Linux, and macOS under the explicit opt-in
and OS-specific guarantees in the [current Runner contract](runner-execution-specification.md).
The initial expansion does not require all Linux distributions, Intel Macs, every
filesystem, cross-OS transfer of existing state, or identical Windows-native
guarantees on every OS. The published artifact and candidate support claims
remain governed by [the release/install record](release-install.md).

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

The public CLI has exactly 23 command leaves:

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
22. `taskgov task context`
23. `taskgov review result add`

`task complete --check` is a mode of the same leaf.
`task add --from-stdin` is an input mode governed by
[structured Task registration](task-operation-specification.md#structured-task-registration).
`review finding resolve --from-stdin` is the existing leaf's
[structured resolution input](review-completion-specification.md#structured-finding-resolutions).
`verification receipt add --from-stdin` is likewise an input mode, defined by
the [structured result contract](review-completion-specification.md#structured-verification-result).
Both Receipt input forms automatically prepare the Packet after a qualifying
registration, with explicit partial-success reporting and same-Receipt readonly
retry on `review prepare --verification-receipt-id`, as defined by
[Receipt output and preparation](review-completion-specification.md#public-and-read-projection).
This changes success output, not the leaf inventory or completion gate.
Applicable commands retain
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

CLI `--json` emits one UTF-8 object followed by LF, without indentation or
separator spaces and without ASCII-escaping non-ASCII characters. Values,
types, array order, and key ordering are unchanged: keys sort recursively
except `review.prepare`, which preserves packet insertion order. Existing
byte-based selection, omission, and rejection decisions retain the prior
pretty-printed, ASCII-escaped, CRLF sizing basis; shorter output does not admit
additional rows or diagnostics. This is CLI presentation only, not a change to
stored Evidence canonical bytes/digests, Viewer output, or ordinary text.

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
| single `task.add` | `task`, `event`, `context_preparation`, plus `contract_write` only when Contract input was supplied |
| `task.add --from-stdin` | `tasks`, one `context_preparation`; input-indexed Task/event results with optional `contract_write` |
| `task.list` | `tasks`, `count`, `limit` |
| default `task.next` | `tasks`, `count`, `limit`, `selection_rules` |
| default `task.current` | `tasks`, `count`, `limit`, `statuses` |
| `task.effort` enabled | `task_id`, `enabled`, `profile`, `measurements`, `thresholds`, `exceeded`, `basis`, `observation`, `coverage`, `attribution`, `unknown_reasons`, `warning_key`, `suggested_action` |
| `task.show` | exactly `task`, `events`, `suggested_next_action`, `review_evidence`, `handoff_summary`, `contract`, `latest_checkpoint`, `effort_advisory_enabled`, `completion_history`, `verification_evidence` |
| `task.context` | `selection`, `current`, `next`, `selected`; fixed composition of compact recall/selection and complete show data |
| `task.edit` | `task`, `changed_fields`, `event`, plus `contract_write` only for Contract input and `runner_plan_update` only when a Runner Plan action was supplied |
| `task.complete` | `task`, `changed_fields`, `event` |
| `handoff.record` | `handoff`, `local_record` |
| `handoff.list` | `handoffs`, `count`, `total_matching`, `limit`, `states` |
| `handoff.show` | `handoff` |
| `handoff.withdraw` | `handoff`, `changed_fields` |
| `review.target.set` | `task`, `changed_fields`, `event`, `verification_route`, `blocking_code`, `review_preparation` |
| `review.receipt.add` | `receipt`, `event` |
| `review.result.add` | `receipts`; each item holds `receipt`, `event`, and nested `findings` |
| `review.finding.add` | `finding`, `event` |
| single `review.finding.resolve` | `finding`, `event` |
| `review.finding.resolve --from-stdin` | `findings`; input-order Finding/event results |
| `verification.receipt.add` | `receipt`, `review_preparation` |

For `task.edit`, `task.complete`, and `review.target.set`, the successful
`task` object omits `description` and `verification` unless that field appears
in `changed_fields`. Other existing Task fields remain unchanged; target-set
preparation has the separately defined result above. Changed prose, including an explicitly cleared value,
is returned exactly as saved. This is a JSON compatibility change: consumers
must retain unchanged prose from their working context, not replace that
context with the write acknowledgement or add a routine read. Full registration,
context and Packet projections retain their full content. Audit recovery and
target-set partial success follow the [Review/completion owner](review-completion-specification.md#git-snapshot-and-target-binding). Complete
stored-row validation still precedes this presentation-only omission.

`task.show` uses a fixed normal working projection; `--audit` selects the
previous bounded historical detail. The top-level keys above are shared, while
the nested evidence/history forms belong to the
[Task read](task-operation-specification.md#task-selection-and-read-commands)
and [Review/completion](review-completion-specification.md#public-and-read-projection)
owners. `task.context.selected` always uses normal working detail. These are
display choices only; stored Evidence, validation and current gates are unchanged.

`task.show` failure keeps both `completion_history=null` and
`verification_evidence=null` in its bounded empty data.
`task.context` failure returns `selection="none"` and null `current`, `next`,
and `selected`, with no partial data or warning. Its success/absence selection
rules belong to the [Task read owner](task-operation-specification.md#task-selection-and-read-commands).
`review.target.set` adds its routing and preparation keys only on success. Its failure data
remains exactly `task=null`, `changed_fields=[]`, and `event=null`.
Revision-zero Contract output is exactly revision 0; empty scope, acceptance,
constraints, `authority_ref`, and `change_reason`; and null `created_at`.
`local_record` contains exactly `durable`, `created`, `replayed`, and
`handoff_id`.

### Task Selection And Read Commands

Current detail is owned by the [Task operation specification](task-operation-specification.md#task-selection-and-read-commands).

### Doctor Contract

[Setup and state operation specification](setup-state-specification.md#doctor-contract) owns this complete
section. Shared contracts retain their existing owners.

### Effective Git-Ignore Preflight

[Setup and state operation specification](setup-state-specification.md#effective-git-ignore-preflight) owns this complete
section. Shared contracts retain their existing owners.

## Task State, Scope, Review, And Completion

<a id="task-record-and-state-transitions"></a>
<a id="task-contract"></a>
<a id="handoff-outbox"></a>
<a id="effort-advisory"></a>

Current detail is owned by the [Task operation specification](task-operation-specification.md#task-state-scope-review-and-completion).

<a id="approved-post-mvp-extension-tg-m16-reduced-loop-discipline-trial"></a>

## Reduced Loop Discipline

Current detail is owned by the [Task operation specification](task-operation-specification.md#reduced-loop-discipline).

## Review And Completion

[Review, Verification, and completion detail](review-completion-specification.md)
has one owner; Evidence provenance and Task Checkpoint detail retain their
delegations below.

### Review Target, Receipt, And Finding Ledger

Current detail is owned by the [Review and completion specification](review-completion-specification.md#review-target-receipt-and-finding-ledger).

### Versioned Review Provenance And Bundle Boundary

Current detail is owned by the [Evidence specification](evidence-specification.md#versioned-review-provenance-and-bundle-boundary).

### Git Snapshot And Target Binding

Current detail is owned by the [Review and completion specification](review-completion-specification.md#git-snapshot-and-target-binding).

### Review Packet

Current detail is owned by the [Review and completion specification](review-completion-specification.md#review-packet).

### Completion Evidence And Commands

Current detail is owned by the [Review and completion specification](review-completion-specification.md#completion-evidence-and-commands).

### Typed Checkpoint

Current detail is owned by the [Task operation specification](task-operation-specification.md#typed-checkpoint).

## Completion Cycle History

Current detail is owned by the [Review and completion specification](review-completion-specification.md#completion-cycle-history).

<a id="current-m25-select-split-merge-register-contract"></a>

## Task Decomposition And Registration

<a id="candidate-first-split-and-one-global-merge"></a>
<a id="explicit-registration-and-contract-population"></a>
<a id="review-tier-and-design-first-rules"></a>
<a id="explicit-mid-task-scope-addition"></a>
<a id="active-instruction-layer-boundary"></a>

Current detail is owned by the [Task operation specification](task-operation-specification.md#task-decomposition-and-registration).

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

Current detail is owned by the [Evidence specification](evidence-specification.md#authority-snapshot-whole-field-criteria-and-references).

### Receipt Meaning And Record

Current detail is owned by the [Review and completion specification](review-completion-specification.md#receipt-meaning-and-record).

### Verification Receipt Eligibility And Manual Completion

Current detail is owned by the [Review and completion specification](review-completion-specification.md#verification-receipt-eligibility-and-manual-completion).

### Public And Read Projection

Current detail is owned by the [Review and completion specification](review-completion-specification.md#public-and-read-projection).

### Migration And Activation Boundary

Current detail is owned by the [Database persistence and migration specification](database-specification.md#migration-and-activation-boundary).

<a id="schema-v19-bundle-foundation-schema-v20v21-native-writer-and-evidence-json"></a>

### Schema-v19 Bundle Foundation, Schema-v20/v21/v22 Native Writer, And Evidence JSON

Current detail is owned by the [Evidence specification](evidence-specification.md#schema-v19-bundle-foundation-schema-v20v21-native-writer-and-evidence-json).

### Assurance, Evidence References, And Finding Snapshots

Current detail is owned by the [Evidence specification](evidence-specification.md#assurance-evidence-references-and-finding-snapshots).

### Canonical Evidence Bundle And Index Formats

Current detail is owned by the [Evidence specification](evidence-specification.md#canonical-evidence-bundle-and-index-formats).

## Recovery Candidate Validity Contract

[Setup and state operation specification](setup-state-specification.md#recovery-candidate-validity-contract) owns this complete
section. Shared contracts retain their existing owners.

## Stored Task Read And Privacy Contract

Current detail is owned by the [Task operation specification](task-operation-specification.md#stored-task-read-and-privacy-contract).

## Stored Contract Pointer Integrity Contract

Current detail is owned by the [Task operation specification](task-operation-specification.md#stored-contract-pointer-integrity-contract).

## SQLite, Migration, And Concurrency

### Initialization And Supported Schemas

Current detail is owned by the [Database persistence and migration specification](database-specification.md#initialization-and-supported-schemas).

### Schema-v20 Foundation And Admission

Current detail is owned by the [Database persistence and migration specification](database-specification.md#schema-v20-foundation-and-admission).

<a id="current-schema-v21-persistence-contract"></a>
<a id="current-schema-v22-persistence-contract"></a>

### Current Schema-v22 Persistence Contract

Current detail is owned by the [Database persistence and migration specification](database-specification.md#current-schema-v22-persistence-contract).

<a id="schema-v21-persistence-contract"></a>

### Schema-v21 Persistence Compatibility And Shared Runner Protocol

The following retains the exact schema-v21 migration/storage contract and the
structural Runner protocol inherited by current schema v22. Source-21 Bundle
and migration statements describe that supported predecessor, not the current
setup target or a relabelling of retained evidence. The
[v22 delta](database-specification.md#current-schema-v22-persistence-contract) owns
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
Their accounting must satisfy the captured
[Runner policy and measurement rules](runner-execution-specification.md#runner-policy-and-accounting).
This uses the existing policy field and nullable measurement columns; no
schema, tagged-union member, JSON member, or digest domain changes. The
version-`0` audit branch retains its original Windows policy and meaning.
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
identity, current-OS policy, or qualifying observation drift makes the Runner
basis non-current.
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
the two closed [target-set JSON success fields](review-completion-specification.md#git-snapshot-and-target-binding); Bundle/Evidence serialization
and projection add no normal-loop call, and no post-target `task show` is added.

The normal and audit `task show.verification_evidence` projections follow the
[Review/completion read contract](review-completion-specification.md#public-and-read-projection).
Their subject, counts, and Receipt detail remain Receipt-only; no Runner ID or
count is exposed. Display selection does not change the shared gate types. For a
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
Historical replay never compares the captured implementation identity with the
currently installed package or the captured policy with the current OS, and
authorizes no new compatibility-mode Runner completion. A
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
`task context` composes its default selection and selected detail using the
retained read. Live marker-2 detail preserves the existing Task-show physical
observation/revalidation boundary rather than claiming cross-phase atomicity.

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

<a id="identity-binding-resolver-and-source-selection"></a>
<a id="relocation-preview-and-token"></a>
<a id="setup-relocation-output-and-durable-publication"></a>

[Setup and state operation specification](setup-state-specification.md#stable-project-identity-and-relocation) owns this complete
section, including the compatibility subsection anchors above. Shared contracts retain their existing owners.

## Setup, Recovery, Evidence, Backup, And Viewer Maintenance

<a id="setup-contract"></a>
<a id="same-process-maintenance"></a>

[Setup and state operation specification](setup-state-specification.md#setup-recovery-evidence-backup-and-viewer-maintenance) owns this complete
section, including the compatibility subsection anchors above. Shared contracts retain their existing owners.

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

One read-only compatibility path preserves already-stored legacy counter text.
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

Current detail is owned by the [Evidence specification](evidence-specification.md#evidence-interpretation-and-retired-analyzer-boundary).

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

Deferred features and retired study results never change current
acceptance, add a normal-loop command, or authorize target/external mutation
until their separately approved implementation and synchronization gates
complete. Active Task decomposition guidance adds none of those capabilities.
