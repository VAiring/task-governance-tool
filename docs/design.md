# task-governance-tool Current Implementation Design

Status: the published product is v0.10.0 with SQLite schema v16 and Viewer
snapshot v4 accepting source schemas v5-v16. The accepted release commit,
remote `main`, and lightweight tag `v0.10.0` are
`a9b80ce177a6dead10d51a070b76ff01f7af0294`; GitHub Release `362617903` has
prerelease visibility. TG-M19.0 through TG-M19.10, including TG-M19.6A and
TG-M19.6B, are complete. This post-release revision is the consolidated active
implementation authority. TG-M16.4 behavioral acceptance remains part of the
published baseline. TG-M20.1 through TG-M20.5 are complete. TG-M21.1 is
complete at `fc2e0870ad9bf70830a082df168ad1992e07b51d`; its inactive design
below does not alter current runtime or package behavior. The approved TG-M20S
successor observation now interrupts before TG-M21.1A. Registered TG-M21
follow-up units remain inactive behind that study and their own gates.

This document is the current implementation design for the behavior specified
in `docs/specification.md`. Historical design captures under `docs/history/`
are non-authoritative and are never needed to implement, operate, migrate, or
review the supported product.

## Design Summary

`task-governance-tool` is a small local-first Codex Skill and deterministic
Python CLI. The Skill supplies concise agent routing; the CLI owns structured
state transitions; SQLite owns local helper state; and the generated Viewer is
a read-only projection. Governing project documents, not SQLite or the Skill,
remain authority for project decisions.

The implementation deliberately avoids a general issue tracker or workflow
engine:

- normal operation is offline and uses no external model call;
- inspection does not create, migrate, recover, or repair state;
- target-project or Git mutation requires separate explicit authority;
- durable text uses its defined per-field bounds and is privacy-validated;
- external work never runs while a SQLite writer is held;
- state and artifacts have one physical project-scoped owner; and
- historical evidence cannot satisfy a current completion or review gate.

## Source And Package Layout

The source repository contains governing documents, tests, fixtures, and one
self-contained installable package:

```text
AGENTS.md
docs/
  specification.md
  design.md
  implementation-roadmap.md
  history/
plan.md
tools/
  release_contract.py
  test_lanes.py
task-governance-tool/
  release-manifest.json
  SKILL.md
  agents/openai.yaml
  assets/task-viewer.template.html
  references/
    task_workflow.md
    cli_contracts.md
    reconciliation.md
  scripts/
    taskgov.py
    task_governance_tool/
tests/
fixtures/
```

Normal stateful use supports exactly one physical package at:

```text
<governed-project>/.agents/skills/task-governance-tool
```

User-wide, symlink, junction, and other reparse-point installs are unsupported.
The source repository has one development-only self-host exception: an
explicit `--repo` may use the physical package at
`<repo>/task-governance-tool` when the five governing source documents and the
fixed package entry/manifest files identify that source shape and no competing
project-scoped install exists. It uses the same package-local state resolver;
it is not a second state mode or install recommendation.

The supported runtime is Python 3.12 or newer on Windows. CI verifies exactly
Python 3.12 and 3.14; no Linux or macOS support claim is inferred.

The package is self-contained. `scripts/taskgov.py` disables bytecode creation
before package imports. Release archives contain the package only, including
its bundled HTML template and manifest, and exclude source-repository tests,
fixtures, root documents, local configuration, generated state, caches, and
logs.

## Runtime Module Boundaries

The implementation keeps these narrow ownership boundaries:

- `cli.py` parses the public surface, resolves lexical root options, dispatches
  services, and formats bounded JSON or text.
- `compact.py` owns compact task projections and final byte caps.
- `project_scope.py` validates the governed root, physical package layout,
  self-host exception, containment, and effective Git-ignore preflight.
- `state_paths.py` defines fixed state names and containment rules.
- `state_resolver.py` is the sole production resolver for fixed state, bounded
  legacy discovery, identity, binding, recovery observations, and artifact
  targets.
- `state_transition.py` owns private staging, no-clobber publication, and
  bounded legacy cleanup.
- `storage.py` owns SQLite connections, migrations, validation, repositories,
  and transaction-scoped queries. Feature modules do not open raw SQLite
  connections.
- `tasks.py`, `ordering.py`, and `selection.py` own task validation, lifecycle,
  current/list projections, the shared sequential predecessor predicate, and
  next-task selection.
- `completion.py`, `completion_workflow.py`, and `git_snapshot.py` own typed
  completion evidence, read-only Git observations, completion planning, and
  review-to-commit snapshot binding.
- `completion_history_projection.py` owns the bounded public cycle projection;
  `storage.py` alone inserts immutable cycles.
- `reviews.py` owns review target, receipt, finding, and deterministic gate
  evaluation; `review_packet.py` owns bounded read-only review context.
- `contracts.py` owns immutable Task Contract revisions and invalidation.
- `checkpoints.py` owns optional append-only checkpoints.
- `handoffs.py` owns the local handoff outbox; selection never depends on
  handoff or adapter state.
- `effort.py` owns the optional deterministic Effort Advisory and no task
  transition.
- `setup.py` orchestrates explicit initialization, migration, recovery,
  relocation, maintenance opt-in, and direct Viewer repair.
- `doctor.py` combines read-only package, scope, and project observations.
- `self_status.py` is the bounded package-integrity inspector used internally
  by doctor; there is no public `self` command.
- `backup.py`, `artifact_lock.py`, and `maintenance.py` own managed SQLite
  copies, one-byte artifact locks, policy state, and post-commit coordination.
- `viewer.py`, `viewer_config.py`, and `viewer_maintenance.py` own compatible
  snapshot reads, strict presentation configuration, safe HTML rendering, and
  generation-based publication.

The bundled template is a complete offline HTML/CSS/JavaScript application. It
has no external dependency, server, database connection, or network API.

The root `tools/release_contract.py` is repository-only verification tooling,
not an installable package module or public CLI leaf. It disables bytecode
generation before importing package code, derives parser leaves and release
versions from their owning runtime modules, delegates packaged-core inspection
to the runtime manifest inspector, and reads the tracked inventory with one
bounded shell-free `git ls-files -z`. Its deterministic findings cover
manifest/package drift, release metadata, license, active command inventories,
CI wiring, and generated-artifact exclusions without creating state or
changing a target project.

The root `tools/test_lanes.py` is the single repository-only owner for the
module-level `fast`, `integration`, and `release` test manifest and the CI
event/Python/lane matrix. It discovers with the same `unittest` start,
top-level directory, and pattern as the prior full command, rejects loader
errors, duplicate IDs, duplicate ownership, unassigned modules, and stale
manifest modules, and preserves discovery order when filtering. `all` is a
meta-lane over the unchanged standard suite, not a fourth maintained list.
The runner disables bytecode generation, performs no network or repository
write, and is not an installable package module or public CLI leaf.

## Public CLI And Serialization

### Command Surface

The parser exposes exactly 20 command leaves:

```text
setup
doctor
task add
task list
task next
task current
task effort
task show
task edit
task complete
task checkpoint
handoff record
handoff list
handoff show
handoff withdraw
review prepare
review target set
review receipt add
review finding add
review finding resolve
```

There are no public `db`, `self`, `web`, export, repair, maintenance, backup,
restore, relocation, or adapter commands. Public `--db` is rejected. Internal
path injection remains available only to repositories, services, and tests.
`--repo` defaults to the current directory because a governed project need not
be a Git repository; runtime never silently re-roots it to a Git worktree.
Invocation while the current directory is either supported package root
requires explicit `--repo`, preventing the package from becoming the governed
project accidentally. Setup alone accepts the bounded backup interval,
retention, and relocation-confirmation options; normal Skill routing supplies
backup defaults and passes a token only after the explicit preview/approval
flow.

Root preprocessing recognizes lexical `--json`, removed commands, and removed
or unknown root options before package, project, Git, or state resolution. A
rejected token or option value is never echoed. Argparse contains no
compatibility subparsers.

`setup` is the only initializer, migrator, recovery/relocation confirmer,
maintenance opt-in, and direct Viewer repair surface. `setup --read-only`
builds a no-write plan. `doctor` is the sole diagnostic, is inherently
read-only, never repairs anything, and is not a normal Task-loop prerequisite.

### Output Boundary

JSON uses the stable envelope:

```json
{
  "ok": true,
  "command": "task.show",
  "project_id": "tg_project_...",
  "data": {},
  "warnings": [],
  "errors": []
}
```

No public envelope contains `db_path` or another internal path. Errors use
stable codes and sanitized fixed messages. Text output is concise and
operational and likewise exposes no database, backup, Viewer, staging, or
package path.

Exit status is 0 for success, 1 for parser/input validation, and 2 for
database, migration, project-state, or service failure unless a command's
fixed contract narrows it.

Default read models and compact projections use explicit allow-lists. They
never serialize a raw SQLite row. Compact current and next results are capped
at 24,576 and 16,384 UTF-8 bytes. Completion check is capped at 8,192 bytes,
checkpoint caller input at 6,144 bytes, and a complete Review Packet at 32,768
bytes. The final JSON boundary also bounds lexical parse errors and replaces an
otherwise unbounded message with a fixed omission message.

Inspection leaves are no-create and no-write. A missing database,
migration-required source, unsupported journal, invalid identity/binding, or
busy database returns its stable error and the command's fixed empty data
shape. Successful business writes describe the record they committed; only
after commit may bounded maintenance warnings be appended.

## Canonical State And SQLite Boundary

### Fixed State Resolver

One physical package owns:

```text
state root       <physical-skill>/state
transition lock  <state-root>/taskgov-state.lock
fixed root       <state-root>/current
database         <fixed-root>/taskgov.sqlite
managed backups  <fixed-root>/backups
Viewer           <fixed-root>/viewer/task-viewer.html
```

The resolver returns canonical paths, the in-memory governed root/hash/display
observation, stored identity/binding when available, source schema, layout
state (`missing`, `fixed_current_v1`, or `legacy_projects_v1`), binding state
(`unbound`, `matching`, or `relocation_required`), and an optional deep
setup-only recovery/legacy observation. None of its raw paths or path hashes
crosses a formatter.

Fixed-primary normal consumers validate only the authoritative primary and
derive canonical artifact targets. Setup, recovery, transition, and legacy
discovery use the deep bounded inventory projection. The caller selects the
projection mechanically; it is not a CLI or LLM choice. Every DB-backed leaf
uses the same resolver observation. Feature code may not reconstruct a
project ID or state path.

### Journal And Connection Rules

Live operational access supports rollback-journal SQLite only. Before any
existing-database read, write, or migration, `storage.py`:

1. rejects lexical `<db>-wal` or `<db>-shm` entries;
2. reads only the fixed SQLite header;
3. rejects a valid header whose read or write version indicates persistent
   WAL; and
4. leaves rollback `-journal` files to SQLite's normal locking/recovery rules.

The check does not open SQLite, checkpoint, change journal mode, delete a
sidecar, or disclose a path/header/OS error. WAL maps to
`unsupported_journal_mode`; unreadable headers use a sanitized internal error.
A short, invalid, or unknown header is not mislabeled as WAL and proceeds to
normal schema validation.

Read connections use:

```text
file:<absolute-uri>?mode=ro
PRAGMA query_only=ON
BEGIN
```

They never use `immutable=1`. Schema history, required objects, project
identity/binding, and command rows are read from the same transaction.
`task show` combines task, events, review evidence, handoff summary, Contract,
checkpoint, and completion history in one snapshot. Handoff list reads count
and rows together. Viewer capture reads generation, tasks, events, and history
in one compatible-schema transaction. `task next` deliberately uses one
transaction for status/paused advisory and another for candidates; cross-phase
staleness is advisory only, while each phase remains internally coherent.

Write services perform caller validation and all Git or other external
observation before opening the writer. They then:

1. run journal preflight;
2. acquire `BEGIN IMMEDIATE`;
3. revalidate schema, project ID, binding hash/generation, task status,
   ordering, Contract revision, review target/generation, and other
   operation-specific basis;
4. persist business rows and events in one savepoint/transaction; and
5. commit and close before formatting or maintenance.

`updated_at` alone is never a concurrency token. No external process, sleep,
backoff, copy, render, model call, or configured command runs while the writer
is held. Residual SQLite busy/locked results map after the normal driver wait to
`database_busy`, exit 2, with no raw SQLite text or retry question. Handoff
record may retry its whole fresh local transaction once; no general automatic
retry exists.

### Migration Sequence

`schema_migrations` is contiguous, named, and validated with required objects
and known later-version markers. Only explicit setup invokes migration. Other
commands never create parents, create a database, stamp missing history, or
perform a reverse migration. Older binaries reject newer schemas.

Current sequential migrations are:

| Version | Name / owned state |
| --- | --- |
| 1 | initial task, event, tool-event, and project metadata schema |
| 2 | compatibility completion-commit requirement/hash projection |
| 3 | `paused` status and exact pause-reason matrix |
| 4 | typed completion evidence |
| 5 | review target, receipt, and finding evidence |
| 6 | Git-snapshot base and receipt target expansion |
| 7 | local handoff outbox |
| 8 | immutable Task Contract revisions and current pointer |
| 9 | Effort Advisory activity generations and bases |
| 10 | one-way project maintenance and backup/Viewer outcome state |
| 11 | managed backup generation inventory |
| 12 | append-only typed checkpoints |
| 13 | Viewer source/render generation state |
| 14 | stable identity and append-only path-binding history |
| 15 | immutable completion-cycle history and internal event linkage |
| 16 | marker-only native completion-capture activation |

Every migration is ordered, idempotent on reentry, transactional, and
rollback-tested. Reentry validates rather than synthesizing missing data.
Table rebuilds preserve foreign keys and IDs and restore foreign-key
enforcement in a `finally` path. Migration validation uses
`PRAGMA quick_check`, `PRAGMA foreign_key_check`, exact row/object
preservation, and the sanitized realistic 12-task/191-event fixture with nine
historical completion hashes and representative review, Contract, handoff,
checkpoint, maintenance, identity, and completion traces.

The fixed-state setup migrator accepts complete source schemas v1-v15 and
treats v16 as current. Legacy `state/projects` discovery is intentionally
narrower: v1-v13 plus the explicit schema-v14 legacy-layout transition.
Viewer compatibility is independent and accepts source schemas v5-v16.
Incomplete history, a missing required object/row, a later marker, too-new
state, unsupported layout, foreign identity, or corrupt integrity fails closed.

## Stable Project Identity, Binding, And Relocation

### Schema V14 Identity

Fresh setup creates `project_id=tg_project_<uuid4 hex>`,
`identity_scheme=uuid_v1`, binding generation 1, and reason `fresh_setup`.
Migrated databases keep their legacy sanitized-name-plus-12-hex
`legacy_path_v1` ID. Durable business rows never change project ID.

`project_meta.canonical_path_hash` is the current binding, not identity.
Schema v14 also stores:

- `binding_generation >= 1`;
- reason `legacy_migration`, `fresh_setup`, or `confirmed_relocation`;
- canonical binding timestamp;
- bounded sanitized display name; and
- the exact cleanup state triple: either pending `0` with null inventory/hash,
  or pending `1` with canonical inventory and its lowercase SHA-256.

`project_path_binding_history` is append-only and keyed by project plus
generation. Generation 1 has no previous hash; each later entry's previous
hash equals the prior current hash and has reason `confirmed_relocation` plus a
token digest. Current metadata must equal the maximum history row. Triggers
prevent identity/creation changes, project deletion, history update/delete,
and invalid cleanup triples. Validation checks consecutive lineage,
scheme-specific IDs, lowercase 64-hex hashes, canonical timestamps, bounded
display text, and canonical cleanup inventory.

The binding repository compares identity, expected generation, and old hash
inside `BEGIN IMMEDIATE`, rejects a same-hash change and signed-64-bit
generation overflow, inserts generation `N+1`, updates the current binding,
and increments Viewer source generation in the same transaction. A missing or
overflowing Viewer row rolls the whole binding change back. The token digest,
not token text, is retained in history.

### Bounded Legacy And Recovery Discovery

Legacy inspection runs only when fixed primary and higher-precedence fixed
recovery are absent. It scans only direct entries under `state/projects`,
consumes at most 64, and rejects an unknown entry, a 65th entry, link/reparse
component, escape, invalid candidate, or multiple candidates without creating
or writing anything.

The candidate validator checks:

- exact legacy directory/database names and one project row;
- contiguous schema and required objects without later markers;
- operational journal mode, quick check, and foreign keys;
- identity/scheme, directory equality, and v14 binding lineage;
- every recognized managed backup as the same identity/scheme with a lineage
  prefix of the mechanically newest source;
- coherent schema-specific backup pointer/rows/files, with at most 21
  retained-plus-in-flight generation identities;
- canonical database, backup, Viewer, lock, and recognized temporary paths; and
- at most one bounded regular temporary for each exact restore, backup, and
  Viewer temporary-name grammar.

Other contained names are opaque user material and are preserved. A fixed
primary always wins even when invalid; the resolver never falls back around
it. A fixed missing-primary recovery selects the newest valid generation and
requires its binding head to match the current governed root. Earlier
generations may be exact lineage prefixes. A moved backup-only legacy source,
foreign/divergent identity, corrupt primary, or unsupported journal is never a
relocation fallback.

Same-binding legacy primary or backup-only state is setup-publishable to fixed
state. Normal commands report migration-required until that atomic
publication. A primary-backed moved source is read-only
`project_relocation_required`; a moved fixed primary is likewise usable only
through explicit relocation confirmation.

### Relocation Token

`relocation.py` emits an opaque, non-secret token:

```text
tgr1.<unpadded-base64url-canonical-json>.<sha256>
```

Its exact payload binds version 1, project ID/scheme, current binding
generation, distinct old/new 64-hex hashes, source layout, source schema, issue
time, and expiry exactly 900 seconds later. A fixed-current source may be
schema v1-v16; `legacy_projects_v1` is capped at the explicit v14 transition,
while ordinary legacy discovery remains v1-v13 plus that v14 shape. Canonical JSON uses
sorted keys, compact separators, UTF-8, and `ensure_ascii=True`; the checksum
is SHA-256 over ASCII `tgr1.<payload>` and is compared with
`hmac.compare_digest`. The whole token is capped at 2,048 ASCII bytes.
Padding, noncanonical encoding, duplicate/missing/extra keys, bad types,
ranges, or times are rejected.

Preview fixes the clock and is no-write. A valid token requires
`issued_at <= now < expires_at`, but handling first checks structural
validity/checksum and successful-token digest reuse. A successfully applied
token therefore remains `relocation_token_used` after expiry. A changed
source, identity, generation, binding, or governed-root observation is stale;
a matching context is not relocation-required. Rejected token values never
appear in output.

`setup --read-only` may emit the fixed six-key relocation preview containing
the token and expiry but no path/hash. Agents must present that preview and
wait for explicit current approval; they never auto-confirm.
`--read-only --confirm-relocation` is rejected before scope inspection.
Write-mode mismatch without the exact token remains no-write.

### Staged Publication And Cleanup

Write-mode setup acquires locks in this order:

1. zero-wait package state-transition lock;
2. relevant source/fixed managed-backup artifact lock; and
3. short private or canonical SQLite transactions.

It never holds a SQLite writer while copying, rendering, invoking Git,
publishing a directory, or cleaning legacy files. Normal business commands do
not acquire the transition lock; canonical rebind instead uses SQLite
compare-and-swap.

Legacy publication and confirmed moved-legacy publication share one bounded
primitive:

1. Revalidate scope, ignore, destination absence, journal, schema, integrity,
   identity/binding, artifact inventory, and optional token under the
   transition lock.
2. Create exclusive, durably flushed paired
   `state/.current-stage-<32hex>.owner` and
   `state/.current-stage-<32hex>` entries. The compact owner record is at most
   2,048 ASCII bytes and binds version, stage ID, project ID, and source
   inventory fingerprint without a path.
3. Admit only database/optional rollback journal, up to 21 managed backups,
   canonical artifact locks, Viewer HTML, and at most one recognized bounded
   temporary per class; at most 32 regular files are staged, each bounded by
   source database size plus 16 MiB. Unsafe, unknown, duplicate, oversized, or
   unowned residue is preserved and stops.
4. Copy the selected database through SQLite backup and copy only validated
   managed/last-good artifacts. Reconcile backup state inside the private
   stage, apply required migrations and maintenance configuration, apply a
   confirmed binding change, persist cleanup intent, and render/validate the
   current Viewer there.
5. Build canonical inventory JSON
   `{"entries":[...],"v":1}` from 1-32 unique recognized source files. Each
   entry contains only kind, relative POSIX name, lowercase SHA-256, and size;
   sorting is by UTF-8 name bytes, canonical bytes are at most 16,384, and the
   fingerprint is their SHA-256.
6. Validate the complete staged database, history, artifacts, Viewer,
   sidecar absence, and binding.
7. Rename the complete directory no-replace to absent `state/current`, then
   remove the external owner marker. A concurrent destination is never
   overwritten.

A pre-publication crash leaves the source authoritative and only a fully
validated owned residue eligible for bounded setup cleanup. A
post-publication crash leaves fixed state authoritative and legacy cleanup
pending. A port without equivalent no-replace directory semantics must fail
closed.

Pending legacy cleanup derives its retirement directory as
`state/.legacy-cleanup-<sha256(project-id)>`. The persisted canonical
inventory, never directory enumeration, is the allow-list. Each recorded file
must be absent or match kind, size, and SHA-256 at exactly one old/retirement
location. Cleanup first no-replace moves remaining old files, then deletes
verified retirement files one by one and only proven-empty owned directories.
Unexpected or changed content stops; unrelated old content remains. One short
transaction clears cleanup state after the recorded set and retirement
directory are absent. Token-free setup resumes a valid pending cleanup.

Fixed-state relocation performs only the binding transaction, then publishes
the Viewer by its generation contract. A crash or Viewer failure after commit
leaves relocation durable and Viewer due; token-free setup repairs it. It does
not manufacture an immediate backup, so loss of the primary before later
managed publication is an explicit bounded recovery window.

## Task State And Selection

### Task Model And Events

Tasks have stable random `tg_task_...` IDs, project ownership, bounded title
and description, kind (`sequential` or `optional`), lane/order, priority
(`low`, `normal`, `high`, `urgent`), status, blocker/pause reasons, review tier,
verification text, tags, timestamps, completion evidence, current review
target, current Contract pointer, Effort activity, and completion-history
coverage. IDs never encode a path.

Statuses are:

```text
ready
in_progress
paused
blocked
review_pending
done
cancelled
```

Blocked requires a reason. Paused requires a pause reason exactly while paused
and may be entered only from in-progress or review-pending; its normal exit is
in-progress and clears the current reason. Initial paused and initial done are
forbidden. `completed_at` is set only on done and cleared on reopen.

Task events are append-only concise audit summaries with stable random IDs.
The internal completion-cycle link added in schema v15 is never in
`PUBLIC_EVENT_FIELDS`; all event-return paths construct the six-field
allow-list explicitly. Latest-event ordering is
`created_at DESC, rowid DESC`. Tool events remain bounded operational records,
not raw logs.

### Sequential Ordering

One repository predicate determines whether an earlier same-project,
same-lane sequential row is incomplete. Only done and cancelled predecessors
are complete. `task next` and direct transitions to in-progress,
review-pending, or done use that predicate. Task add and edits that change
kind/lane/order/status validate every already-active, review-pending, and done
row in both affected lanes inside the serialized write, preventing
registration or reordering ahead of active successors. There is no override.

Lane input is trimmed and validated once. Sequential omission chooses the
deterministic default lane and next order; all integers fit SQLite signed
64-bit and next-order overflow fails before addition. The unique
project/lane/order index enforces final storage uniqueness.

Next-task order is priority (`urgent`, `high`, `normal`, `low`), lane,
lane-order with nulls last, creation time, then task ID. Default limit is 5.
Paused, blocked, active, review-pending, done, and cancelled rows are not next
candidates. A positive paused population adds one fixed count-only advisory
without changing candidate data or exit status.

`task list` uses the same priority, canonical-lane, nulls-last lane-order,
creation-time, and task-ID order. Its default limit is 20 and maximum is 100.

`task current` selects in-progress, review-pending, paused, and blocked rows,
optionally one valid status, with default limit 20 and maximum 100. It reuses
the latest event/checkpoint and deterministic fixed next-action mapping. It
does not calculate staleness or write a checkpoint. List/current/next remain
bounded and have no pagination cursor.

### Done Immutability And Reopen

Every task/review mutation loads the owner and applies the shared done guard.
A done Task accepts only the exact reopen edit:

- resulting status `in_progress`;
- one non-empty sanitized reopen reason; and
- no other task, note, completion, review, or Contract input.

The reopen writer checks review-generation overflow and sequential ordering,
clears current completion evidence and review target/base, advances review
generation, clears completion time and hold reasons, and appends
`task_reopened`. It preserves prior events, receipts, findings, Contract
revisions, and completion cycles. Schema-v16 reopen additionally validates and
links the latest saved cycle as described below. All other done writes return
`done_task_requires_reopen`.

A review-tier increase is a normal edit. A decrease needs one sanitized reason
and is permitted only while ready, in-progress, paused, or blocked, before any
review target ever existed: generation 0 and empty kind/value/base. It cannot
share completion input or a transition to review-pending/done. Generation
greater than zero permanently proves structured review started.

## Completion Evidence And Review

### Typed Completion Evidence

Schema v4 retains the old required/hash columns only as synchronized
compatibility projections. Current evidence kinds and matrices are:

- `none`: empty revision/reason, approval 0, required 1, empty hash;
- `git_commit`: canonical full commit ID as revision/hash, empty reason,
  approval 0, required 1;
- `external_revision`: bounded revision and reason, explicit approval 1,
  required 1, hash equal to revision;
- `commit_not_required`: empty revision/reason/hash, approval 0, required 0;
- `legacy_unverified`: migration-only partial history retaining the old hash;
  no normal write may choose it.

Changing kind clears fields not valid for the destination. Conflicting stale
fields return `completion_evidence_conflict`. External evidence always needs
the explicit acknowledgement, including in a Git project. `legacy_unverified`
cannot satisfy a new done after reopen.

Git commit resolution uses argument-vector, no-shell reads equivalent to:

```text
git -C <repo> rev-parse --verify --end-of-options <revision>^{commit}
```

Empty/option-shaped input, multiple output lines, non-hex output, ambiguity,
failure, or a noncanonical full ID is rejected. Optional locks and lazy object
fetching are disabled; no Git read may invoke a hook, network fetch, or write.
Stored Git completion and Git review targets are re-resolved under the
done-time plan and compared with the locked stored values.

Thin `task complete`, compatibility `task edit --status done`, and
`task complete --check` share one completion request and ordered validator.
Both write paths require explicit verification/review confirmations, typed
evidence, sequential eligibility, exact current target, a satisfied fresh
review gate, and no blocking receipt/finding. Check is read-only and is not an
authorization token: it captures one coherent basis, closes SQLite for Git,
then performs a second coherent basis read. Drift yields
`completion_check_stale`; no readiness row or receipt is stored. Its bounded
projection returns only Task ID, ready/status, the first ordered blocking code,
Contract revision, target generation, proposed evidence kind, and fixed next
action.

### Review Target And Git Snapshot

The current review identity is the exact tuple:

```text
kind, value, base_revision, generation
```

Kinds are `git_commit`, `diff_fingerprint`, `external_revision`, and
`git_snapshot`. A diff fingerprint is canonical
`sha256:<64 lowercase hex>`. Setting any target advances generation even when
kind/value repeat, and old receipts become audit-only. Receipt creation copies
the complete tuple; callers cannot attach one to another target.

`git_snapshot` takes no caller revision. The Git service reads a stable
`HEAD^{commit}` and stage-0 `git ls-files --stage -z` index before/after
checks, consuming raw path bytes. It rejects unborn HEAD, nonzero stages,
intent-to-add zero objects, sparse directory entries, and capture instability.
The version-1 digest input is:

```text
"taskgov-git-snapshot-v1\0"
<base full object id> "\0"
for mode/object/path entries sorted by raw path bytes:
  <mode> "\0" <object id> "\0" <raw path bytes> "\0"
```

The target value is its SHA-256 fingerprint and base is HEAD. It excludes
unstaged and untracked content.

At completion, the proposed commit must have exactly one parent equal to the
stored base. Its recursive tree leaves are normalized to the same
mode/object/path form and must reproduce the fingerprint. Root and merge
commits are unsupported for snapshot binding. Hook-altered, added, removed,
renamed, mode-changed, or otherwise changed content fails
`review_target_mismatch`. A snapshot target may close only with Git-commit
completion evidence. A Git-commit target requires the identical canonical
Git completion commit, an external target requires the identical approved
external revision, and commit-not-required requires a canonical
diff-fingerprint target.

### Receipts, Findings, And Gate

Receipts are append-only and unique per Task, target generation, and bounded
caller-supplied reviewer key. Kinds are:

- `independent`: pass or changes-requested, never user-approved;
- `self_review_fallback`: summarized; a Tier-2 pass requires explicit user
  approval, a Tier-1 pass does not;
- `not_required`: Tier 0 only, verdict not-required, mechanical rationale, no
  approval.

A changes-requested receipt requires a summary and never satisfies the gate.
Replacing a same-generation reviewer result is forbidden; re-review sets a
fresh target generation.

Findings belong to a loaded same-project/same-task receipt and are high,
medium, or low. Resolution preserves the original row and adds only bounded
resolution text/time. Open high or medium findings across all task receipts
block. A resolved high/medium finding still requires a target generation newer
than its receipt and fresh passes. Any current-generation changes-requested
receipt blocks. Low findings are nonblocking.

The gate deterministically requires:

- Tier 0: one current not-required receipt;
- Tier 1: one distinct current independent PASS, or its valid fallback;
- Tier 2: two distinct current independent PASS reviewer keys, or one valid
  explicitly approved fallback when independent review tooling is unavailable;
- no current changes-requested receipt;
- no open high/medium finding; and
- no resolved high/medium finding at or after the current target generation.

Independent PASS rows are ordered by reviewer key then receipt ID and take
precedence over fallback. Tier 0/fallback selection is by receipt ID. Reviewer
key distinctness proves only different stored strings, not identity,
independence, provenance, expertise, target inspection, or authentication. The
trusted caller/orchestrator is responsible for truthful attestation.

### Review Packet

`review prepare` reads Task, Contract, target, and review counts in one
transaction, closes it for bounded Git observation, then reopens a short read
transaction to compare project/task identity, Contract revision, and the whole
target tuple. A change returns `review_packet_stale`.

For a snapshot it recaptures the exact base/index context. For a Git commit it
lists first-parent changes, using the empty tree for a root. Fingerprint and
external targets perform no Git read and state that exact caller-provided
material must be bound to the target before PASS. At most 10 shell-free Git
processes, 100 bytewise-sorted relative paths, 240 UTF-8 bytes per path, and
16,384 aggregate path bytes are allowed. Safe overflow is marked truncated;
an unsafe path or a packet above 32,768 bytes fails with no partial packet.

Task, Contract, target, changed paths, five fixed review-focus rows, required
output, and the existing receipt argv shape are allow-listed. The builder
does not launch a reviewer, execute/import a receipt, store a packet, or
include a diff, transcript, prompt, stdout/stderr, secret, or absolute path.

## Completion Cycle History

### Schema V15 And V16 Activation

Schema v15 adds immutable `task_completion_cycles`, Task coverage
`legacy_unknown|complete`, nullable internal `task_events.completion_cycle_id`,
parent/foreign-key support indexes, and triggers that prevent:

- every cycle update or deletion;
- changing Task coverage after insert; and
- changing an event's cycle link from null or one saved ID.

Cycle IDs have prefix `tg_completion_cycle_` plus 16 lowercase hex characters.
Ordinals are unique per project/Task, start at 1, and reject signed-64-bit
overflow. The row stores:

- origin `native_done|legacy_current_done` and completeness
  `complete|partial`;
- completion and record times;
- Contract revision, review tier, and verification expectation/attestation;
- the complete six-field completion evidence projection;
- the complete four-field review target;
- gate basis version and deterministic counts/basis kind; and
- up to two exact qualifying receipt IDs protected by composite foreign keys
  to the same Task and target tuple.

Native rows must be complete, have completion time, attestation true,
non-empty target, gate-basis version 1, zero blockers, and a valid Tier basis.
Legacy rows are partial, have null attestation, gate-basis version 0, unknown
basis, null counts/receipt slots, and retain an honest legacy completion
projection. Structural `CHECK`s enforce the matrices; repository validation
also enforces canonical hashes/fingerprints/text/timestamps and exact receipt
kind/verdict/approval/order.

Migration 15:

1. fingerprints all prior business tables and counts;
2. creates columns, table, indexes, and immutability triggers in one immediate
   foreign-key-enabled transaction;
3. reads current done Tasks in binary Task-ID order;
4. inserts one ordinal-1 `legacy_current_done` partial row per current done
   Task using one migration timestamp and no inferred gate claim;
5. leaves every old event link null and every Task coverage
   `legacy_unknown`; and
6. proves preservation, quick check, and foreign keys before recording
   `completion_cycle_history`.

Reentry validates current structure/matrices without rerunning migration-time
cardinality assumptions.

Marker migration 16 requires complete v15 and all Tasks still
`legacy_unknown`. For each current done Task it compares the newest cycle with
completion time, Contract/review/verification values, all evidence and target
fields, and whether a reopen already linked it. It inserts the next partial
legacy cycle only when absent, different, or already linked; an exact unlinked
match is retained. It then records
`completion_cycle_capture_activation`. The marker adds no schema object.
Reentry validates and never reconciles twice. Schema-v16 Task creation
explicitly writes coverage `complete`; the column default remains
`legacy_unknown` for schema-v15 safety.

### Native Done And Reopen Transactions

Both `task complete` and compatibility `task edit --status done` perform Git
preflight outside SQLite and converge on one locked native capture:

1. validate schema v16, identity/binding, optimistic Task basis, Contract,
   sequential ordering, evidence, and target;
2. reread receipts/findings, evaluate the current gate, and select its
   deterministic basis;
3. choose canonical completion time and next ordinal;
4. insert one immutable complete `native_done` cycle;
5. update the current Task to done with identical evidence;
6. rerun lane invariants;
7. insert the existing completion event with the internal cycle link; and
8. record existing Effort/Viewer-generation effects and commit.

Any failure rolls all business rows back. Concurrent completions serialize; a
loser creates no second cycle. Read-only completion check inserts nothing.

Reopen locks the done Task, loads the highest cycle and linked reopen state,
and compares that cycle with the entire current completion projection. When
coverage is `legacy_unknown` and no cycle exists, it may insert the exact
ordinal-1 partial compatibility bridge. Complete coverage without a cycle,
any existing-cycle mismatch, or an already linked reopen returns
`completion_history_inconsistent`. It then clears current gates, preserves
coverage and cycles, inserts `task_reopened` linked to the validated cycle, and
commits. It never revalidates historical Git material, uses historical
receipts as current eligibility, changes a cycle, or creates a bridge when a
cycle already exists. A later done uses fresh gates and the next ordinal.

### Public History Projection

`task show` reads total, incomplete-legacy aggregate, and newest-first rows in
the same snapshot as all other Task data. The exact wrapper is:

```json
{
  "total": 2,
  "returned_count": 2,
  "truncated": false,
  "legacy_history_incomplete": false,
  "cycles": []
}
```

`legacy_history_incomplete` is computed over all durable rows, not the bounded
return window. It is true when Task coverage is not complete, any saved cycle
is partial, or any `task_reopened` event has a null internal cycle link. A
fresh schema-v16 Task with no cycle is complete history and false.

Each cycle and each candidate complete wrapper is measured using compact,
sorted-key, non-ASCII-preserving UTF-8 JSON. A cycle is at most 8,192 bytes;
the wrapper is at most 10 rows and 32,768 bytes. Collection stops at the first
non-fitting newest-first row. Version-0 basis uses JSON nulls and no receipt
IDs; version 1 uses integers and the stored one/two IDs without null
placeholders. Text prints only counts, flags, and newest non-content fields.

The formatter revalidates stored completion revision, evidence reason,
completion-hash, target value, and target-base text through the ordinary
privacy matcher before building a public cycle. It has no legacy M19.7
projection. A rejection maps to the fixed
`completion_history_inconsistent` error and never exposes the offending field
or value; `task show` and Viewer use this same formatter.

Viewer batch history is grouped/windowed for at most 500 selected Tasks and 10
cycles each, not one query per cycle. Snapshot v4 uses the identical wrapper.
Source schemas v5-v14 synthesize empty/incomplete history; v15-v16 use stored
rows. Neither public events nor Viewer disclose internal event-cycle IDs.

The only new stable error is
`completion_history_inconsistent: stored completion history is inconsistent`.
It exposes no IDs, counts, values, hashes, SQL, or paths.

## Approved But Inactive TG-M21 Verification Receipt Design

This design is an acceptance target for later separately approved units. It is
not wired into schema v16, the 20-leaf parser, completion, Viewer, or Skill.
Current code and package guidance remain authoritative until the atomic M21
activation gate completes.

### Ownership And Data Model

A future `verification_receipts.py` module owns Receipt input validation,
exact-current classification, gate evaluation, and the bounded public read
model. `storage.py` alone owns schema, migration, append/read queries, and
Receipt uniqueness. `tasks.py` and `completion_workflow.py` consume the gate
result; neither opens raw SQLite or interprets verification prose.
`cli.py` owns only parser/dispatch/formatting for the one proposed write leaf.

Schema v17 adds an immutable `verification_receipts` table with:

```text
verification_receipt_id project_id task_id contract_revision
verification_expectation_digest command_label result duration_ms
scope_coverage target_kind target_value target_base_revision
target_generation created_at
```

IDs use the `tg_verification_receipt_<16-lowercase-hex>` grammar. Result is
`pass|fail|timeout`; coverage is `full|partial`; duration is a nonnegative
signed-64-bit millisecond integer. The label is nonempty, privacy-validated,
and at most 200 characters. Target fields reuse the existing four-way matrix.
Composite indexes support project/Task/current Contract/expectation/target
gate reads without one query per Receipt. Update/delete triggers preserve the
append-only boundary. A unique project/Task/target-generation key permits one
aggregate Receipt and makes retry require an explicit fresh target.

The same migration adds three internal fields to `task_completion_cycles`:

```text
verification_basis_version verification_expectation_digest
verification_receipt_id
```

Existing rows receive version 0 with null digest and null Receipt link. New
native cycle insertion is constrained to version 1 and stores the same domain-
separated digest of its exact Task verification text, including empty text.
Version 1 requires a linked Receipt for a nonempty stored verification
expectation and requires a null link for an empty expectation. The Receipt ID
is a nullable foreign key; repository and projection validation additionally
prove identical ownership, Contract, stored digest, target tuple, and
`pass/full` qualification. Existing cycle immutability covers all three
fields. A migration-owned insert guard permits version 0/null/null only for the
existing sole `legacy_current_done` partial reopen bridge when a legacy-
unknown done Task has no cycle; every normal native insert requires version 1.
None of the internal fields is added to the public completion-history
projection.

The expectation digest is:

```text
sha256("taskgov-verification-expectation-v1\0" + exact stored UTF-8 verification)
```

It binds eligibility without storing another copy of verification prose. The
public projection never emits the digest. The complete target tuple is copied
from the locked Task; no caller target field exists. `created_at` and ID are
allocated under the writer. Structural validation reuses the existing target,
timestamp, privacy, signed-integer, and ownership validators.

The activated Skill sequence is exact material, existing target set with its
returned generation retained, external verification, Receipt add with that
expected generation, review prepare/record, then completion. This moves target
setting before verification and adds exactly one normal-path call, making the
inactive target bound 10 or 11 with Effort Advisory. No current Skill
instruction changes before the atomic activation unit.

### Receipt Write And Freshness

The write service first validates the four Receipt facts plus positive
`expected_target_generation` without opening a
writer, then starts one short immediate transaction and rereads schema,
identity/binding, Task, Contract pointer, exact verification text, and target
tuple. It permits only in-progress or review-pending Tasks with specified
verification and a current target. Expected generation must equal the locked
target generation; drift returns `verification_basis_stale` without storage.
It computes the digest, appends the row without changing the Task or adding an
event, commits, and only then invokes backup-eligible, Viewer-ineligible post-
commit maintenance.

The caller's add invocation attests that the external run exercised the copied
target; taskgov does not prove that claim. The caller supplies no command body,
source revision, Contract revision, timestamp, ID, exit code, output,
exception, or arbitrary result document. A failed or timeout row records
evidence only and performs no Task/status/Contract/target/review/completion/
Handoff mutation. A second call in the same generation returns
`verification_receipt_already_recorded`; the caller can inspect `task show`,
then explicitly set a fresh target before another run.

Parser and common state errors retain their existing owners. The Receipt
service owns one ordered validator matching the specification: normalized
caller fields, Task existence, done/disallowed status, nonempty expectation,
stored target structure/presence, expected generation, then uniqueness. It is
used before and after the writer lock so a concurrent status, Contract,
expectation, or target change cannot select a different semantic ordering or
bind the row to new material. CLI formatting owns the exact three-line success
text and adds no synthetic Task event.

One shared evaluator classifies a row as exact-current only when project,
Task, Contract revision, expectation digest, and every target field equal the
coherent current basis. The gate computes, in order:

```text
specified expectation + no exact-current Receipt
  => verification_receipt_required
specified expectation + Receipt other than pass/full
  => verification_receipt_blocking
specified expectation + pass/full
  => satisfied
unspecified expectation
  => receipt gate not required; existing attestation remains required
```

Old-target, old-Contract, and old-expectation rows remain visible only in the
bounded recent audit list and never affect that result. Any current non-
qualifying Receipt is retired by an explicit fresh target generation, not by
row update or delete.

A semantic edit of `tasks.verification` with a started target uses the existing
Contract-invalidation shape in the same Task transaction: clear typed
completion evidence and target kind/value/base, increment generation with
overflow protection, move review-pending to in-progress, update the Task, and
append the normal bounded event. Receipts and review rows remain immutable and
become historical. A same-content edit is a no-op under existing edit rules.

### Completion And Read Integration

The completion basis snapshot adds the current verification gate summary and
Receipt ID. Outside-Git preflight and locked revalidation both run the same
evaluator. Ordered validation keeps the explicit `verification_required` flag
check and target validation first, then reports the missing or blocking
Receipt, then evaluates current review findings/receipts. `task complete
--check` adds the two Receipt codes to its bounded allow-list; check never
writes or reserves a Receipt.

For specified verification, native completion proves that the unique current
Receipt is `pass/full`, then inserts a version-1 cycle with its Receipt foreign
key and identical expectation digest, the Task update, and linked completion
event in one transaction. A native completion with empty verification inserts
version 1 with the empty-text digest and a null Receipt link. Any ownership,
target, digest, link, or gate drift rolls everything back. Existing
`verification_attestation=true` remains in the cycle, so the Receipt
strengthens rather than silently replaces the current explicit assertion.
Version-0/null/null marks only migrated cycles and the sole exact compatibility
bridge; a version-1 nonempty cycle with no valid linked Receipt is
`completion_history_inconsistent`, never an inferred legacy success.

The one future command is `verification receipt add`. Its success data is
exactly `receipt`. `task show` obtains Receipt totals, exact-current
counts, gate, and newest 10 rows in the same query-only transaction as the
Task and adds the fixed `verification_evidence` object. The formatter exposes
only the specification's public allow-lists; no separate list/show/import/
export command or pagination is added.

The read model emits the exact types and nulls fixed by the specification.
Empty expectation is not required and is satisfied. A nonempty active Task
reports target-required, Receipt-required, Receipt-blocking, or satisfied in
that order. A done Task backed by a matching version-0 cycle reports the
explicit legacy exemption even when its target is absent, including the sole
bridge-created cycle. A version-1 done cycle is validated against its linked
Receipt before projection. Task-show failure data includes a null
`verification_evidence`; text Task show remains unchanged. The Receipt write
alone has the fixed three-line text projection.

The initial completion-history JSON remains unchanged. The current done Task
retains its target and Receipt, so `task show.verification_evidence` exposes
the current qualifying basis; the cycle target tuple also identifies it.
Reopen advances the target and makes it historical. A later history projection
would require separate evidence and approval rather than silently changing
Viewer snapshot v4.

### Schema, Viewer, Packaging, And Tests

Migration `verification_receipts` creates the Receipt table, indexes, triggers,
the three completion-cycle basis columns and insert/link guards, and the schema-
history row. Existing cycles receive only version 0/null-digest/null-link as a
structural legacy discriminator. It never parses verification text or events
to infer runs or create Receipt evidence. Reentry validates the exact objects,
columns, constraints, immutable triggers, ownership/link relations, and
absence of invented Receipt rows. Old binaries reject v17 normally; setup
remains the sole migrator. The established reopen bridge remains the only
post-migration writer of the exact version-0/null/null legacy shape.

Viewer source compatibility expands from v5-v16 to v5-v17 in the atomic
activation unit, but snapshot v4 fields and UI remain unchanged. Receipt
writes are Viewer-ineligible. The existing bounded batch completion-history
loader performs one internal join for selected version-1 cycle Receipt links,
validates only ownership/digest/target/qualification, and discards every
Receipt field before snapshot formatting. There is no Receipt dataset,
snapshot field, panel, filter, or detail, and no per-Task Receipt query. This
compatibility update must land with the schema bump so explicit setup and later
unrelated Viewer maintenance cannot fail merely because the canonical state
migrated.

Focused tests reuse one matrix owner for result/coverage/current-binding gate
cases and existing migration/completion/Task-show helpers. They cover all
source schemas v1-v17, rollback/reentry, no legacy synthesis, exact target and
Contract invalidation, semantic verification edit, failure-generation reset,
unique per-generation ownership, version-0 legacy exemption, version-1
Receipt-link enforcement, the sole post-v17 legacy bridge, cycle-target
reconstruction, concurrent target/edit drift and expected-generation
rejection, read-only no-write,
privacy rejection, byte/count bounds, backup-only maintenance, Viewer v17
compatibility including valid/corrupt link batch validation, parser/help/
output, and unchanged unrelated projections. Test
facts and command inventories must remain derived from their existing owners
rather than copied into CI or multiple test modules.

### Registered Implementation Units

These units are approved and registered but remain inactive until the TG-M20S
interrupt and TG-M21.1A/TG-M21.1B authority-layout predecessors complete:

1. **TG-M21.2 atomic vertical activation** adds schema v17, immutable storage,
   repository/evaluator services, Viewer source compatibility, the write leaf,
   bounded Task-show projection, verification-edit invalidation,
   completion/check gate and versioned cycle/Receipt basis validation,
   backup-only maintenance integration, and synchronized concise Skill/
   reference/formal contracts in one exact reviewed unit. `SCHEMA_VERSION=17`
   and setup
   migration activation must not land independently from those behaviors.
2. **TG-M21.3 integrated acceptance** runs the full migration/privacy/
   concurrency/package/release checks and fresh realistic pass, failure,
   timeout, stale-evidence, and resume/complete scenarios before closing M21.

Each unit is Tier 2. Registration does not activate either unit; TG-M21.2 must
land atomically and TG-M21.3 must accept that exact target. No partial unit
activates the separate proportionality guardrail, project test strategy,
command runner, approved exception, Viewer Receipt UI, or Task decomposition.

## Task Contracts, Checkpoints, Handoffs, And Effort

### Immutable Task Contract Revisions

Schema v8 gives Tasks a current revision pointer and adds append-only
`task_contract_revisions`. Revision 0 has no row and projects empty fields.
Each positive row stores normalized scope, acceptance, optional constraints,
stable authority reference, change reason, and timestamp. Repository reads
require the pointer to reference the latest same-project/same-task revision.
Scope and acceptance are each capped at 4,000 characters, constraints at
2,000, authority reference at 500, and change reason at 1,000.

Supplying any Contract option supplies the group and requires scope and
acceptance. Initial Contract recording is allowed:

- on Task add only for ready, in-progress, blocked, or review-pending; or
- for an existing revision-0 Task only in the exact ready/blocked to
  in-progress activation, with empty completion/review state and no companion
  mutation.

Initial change reason is empty. Later revisions are Contract-only edits while
ready, in-progress, paused, blocked, or review-pending and require a semantic
scope/acceptance/constraints change, bounded non-empty reason, and stable
authority reference. Done must reopen and cancelled rejects Contract input.
Authority can name a revisioned governing file/decision or exact
`user_instruction:<task-id>:<next-revision>`; raw prompt text and current Task
output are not authority.

Normalization converts CRLF/CR to LF and strips outer whitespace while
preserving internal text. Omitted later constraints retain the current value;
explicit empty removes them. Exact semantic replay returns the current
revision without write even if authority/reason labels differ. A real change
allocates the next revision under the writer, updates the pointer, resets all
completion evidence, clears/advances any started review target, moves
review-pending to in-progress, updates time, and appends a content-free
`contract_revised` event atomically. Old Contracts and review history remain.

Stored Contract projection has one narrow legacy M19.7 seam: only
`constraints_text` is validated through the bounded legacy reader and returned
unchanged. Normal Contract input never selects that reader. When later
constraints are omitted, the established carry-forward rule may copy those
already-validated bytes into the new immutable revision; this preserves
lineage and supplies neither caller-input acceptance nor authority.

Concurrent identical input records once and replays; different valid input
serializes into successive revisions. Current-or-next user-instruction
placeholders are rebound to the locked allocation. A lost-response retry with
the older placeholder may replay; an unrelated placeholder cannot authorize a
new semantic change.

### Typed Checkpoints

`task checkpoint` requires bounded summary and next action and accepts at most
eight bounded unresolved risks. Limits are 1,024 UTF-8 bytes for summary and
next action, 512 per risk, 4,096 aggregate risks, and 6,144 for caller input.
One append-only row stores those fields, Task/project, current Contract
revision, and time. The same transaction adds fixed event
`checkpoint_recorded` / `Checkpoint recorded` without content and does not
change `tasks.updated_at`.

Exact replay of the latest same-Contract checkpoint is no-write. Done Tasks
reject it. Current/show expose only the latest checkpoint; Viewer excludes
checkpoint content. A checkpoint is optional and changes no Task status,
scope, selection, review, evidence, or completion gate.

Only stored checkpoint `summary` projection may use the bounded M19.7 reader
for the former numeric `dispatch_authorization` JSON field. It returns the
original canonical stored summary and writes nothing. New summaries and all
other checkpoint fields use the ordinary privacy path.

### Local Handoff Outbox

Schema v7 owns `handoff_records` with source Task/current Contract revision,
canonical idempotency key, optional explicit occurrence ID, bounded
summary/rationale, state, adapter/delivery metadata, internal claim lease,
bounded receiver receipt, and withdrawal data. Public states are:

```text
pending_handoff
handed_off
handoff_withdrawn_by_user
```

The state matrix requires pending without terminal timestamps, handed-off with
acknowledgement time and no withdrawal, and withdrawn with user reason/time,
no receiver receipt, and zero delivery attempts. Claim tokens are never
public. Every stored free-form field and matrix is revalidated before output;
corrupt/private stored content returns a fixed internal error rather than
redaction.

Canonical compact JSON over project, source Task, source Contract revision,
normalized summary/rationale, and occurrence ID is SHA-256 hashed as the
unique idempotency key. Omission canonicalizes to empty; an explicit occurrence
must come from explicit user instruction or deterministic external identity.
Summary and rationale are each capped at 1,000 characters and occurrence ID at
200; an explicitly empty or invalid occurrence is rejected rather than treated
as omission.
Exact replay returns the row and writes nothing. The local transaction commits
before any possible delivery; success reports durable only after commit.
Handoff never changes source Task state, acceptance, timestamps, events, or
selection.

List defaults to pending, oldest-first, limit 20/max 100, with exact
`total_matching` and rows in one snapshot. Terminal states require explicit
filter. Show uses the full public allow-list, while Task show exposes only
per-state counts. Withdraw requires pending, zero attempts, no claim ever, and
a sanitized user reason; it is a single immediate transaction.

The shipped product has no receiver and no public sync command.
`adapter_enabled=false`; pending records remain durable and rediscoverable.
The schema reserves a local versioned idempotent claim/delivery state machine,
but a concrete Issue adapter remains blocked until a separately approved
local intake contract exists. Task Skill never performs Issue triage,
priority, lifecycle, network/GitHub access, arbitrary code loading, shell
execution, or Issue-database access.

Any later sink must use stable `handoff_id` as receiver idempotency key and
return only accepted/retryable/permanent plus a bounded receipt. Claims use
compare-and-swap and expiry; an ever-claimed row is never withdrawable.
Retry stages are fixed 60-second then 300-second waits, then exhausted, with
permanent error terminal for delivery attempts. This reserved state does not
add a normal Task-loop call until a real adapter is approved.

### Optional Effort Advisory

`effort.py` reads only strict optional
`<skill>/config/effort-advisory.json`. Version 1 accepts exactly profile
`informational-v1`, explicit enabled, and thresholds for five fixed metrics.
Missing or valid disabled configuration is off; invalid present configuration
is disabled with a bounded continuation diagnostic. There is no generic
configuration store, inheritance, environment override, writer, or configured
command runner.

Thresholds are nonnegative JSON integers for only changed files, changed
lines, changed modules, Contract revisions, and handoffs, and exceed only when
the measurement is strictly greater. An invalid profile uses the fixed
continuation warning; a threshold result uses at most one warning with code
`effort_advisory_threshold_exceeded`, key
`effort_advisory.threshold_exceeded.v1`, and fixed non-content message.

When enabled, the first transition to in-progress may best-effort capture
canonical Git basis, cleanliness, timestamp, project/subject activity
generations, and whether another task was active. Git observation is
argument-vector, optional-lock-disabled, no-lazy-fetch, fsmonitor-disabled,
submodule-ignored, no-external-diff/text-conversion, and read-only. Capture
failure stores no partial basis and never blocks start.

Metrics are changed files, changed lines, modules, Contract revision count,
and source-task handoff count. Attribution is unknown for non-Git, dirty or
uncertain endpoints, incomplete line coverage, or activity evidence of
overlap. Untracked/binary content makes line coverage unknown instead of
guessed. With a basis, pre/post DB observations are separately coherent around
Git; the post-read refresh detects overlap without keeping a transaction open.
Strict-off databases do no advisory bookkeeping.

The advisory never writes acknowledgement, asks a question, chooses a
handoff, changes status, expands scope/acceptance, or blocks completion.
`task show` mechanically exposes enablement. The Skill calls `task effort`
once at the existing verification/review boundary only when enabled.

## Approved TG-M16 Reduced Loop Discipline Trial Design

The existing Effort result chooses:

```text
valid enabled profile AND a nonempty ordered exceeded list
  => suggested_action=reconcile_scope
otherwise
  => suggested_action=continue
```

The same value appears in the one existing threshold warning. Attribution and
unknown reasons remain evidence only: unknown without an exceeded threshold
continues; an exceeded threshold with unknown attribution reconciles. This
adds no metric, profile field, database write, stored acknowledgement, routing
framework, or Task/handoff/review operation.

Reconciliation is session-local guidance, not persisted state.
`references/reconciliation.md` is loaded only after `reconcile_scope` or a
repeated test/review failure. After two materially equivalent failed repairs,
a third equivalent execution is prohibited without new evidence. Renaming a
wrapper, command, directory, Task, or execution unit is not new evidence; a
diagnostic is new only when its result can materially change the causal
hypothesis, authorized repair, or expected result. A fresh session resets the
comparison and relies on durable Task/event/review/handoff state.

Tests are never weakened merely to obtain PASS. A failing test or Effort
signal is evidence, not authority. A test may change only when current
authority establishes it is wrong; a Task Contract or acceptance change still
needs later explicit authority.

Scope reconciliation reuses the existing three-way classifier: work stays in
the current Task only when accepted scope and current authority cover it;
other discoveries go to local handoff; a blocker is used only after safe
authorized repair for the affected Task/lane is exhausted. Paused remains an
explicit temporary interruption, and unrelated ready lanes continue.
Remaining user decisions are batched.

Review remediation starts from the current blocking receipt/finding. A
meaningful fix sets a fresh target and obtains a fresh current-generation
result. A result that remains blocking counts as one unsuccessful cycle;
completion still requires fresh qualifying PASS receipts. After two
materially equivalent unsuccessful cycles without new evidence, no third
equivalent cycle runs; the same bounded blocker/decision path applies.

No M16 setup stage, Task seed, policy version, instruction-chain inspection,
target `AGENTS.md` mutation, persisted counter, alternate state machine, or
normal-loop call exists.

## Setup, Doctor, Backup, And Maintenance

### Shared Scope Preflight

Setup and doctor validate a physical target directory, canonical package,
layout, state ownership, and package identity. If the target or an ancestor
has a `.git` marker, `project_scope.py` runs exactly one bounded, shell-free
effective-ignore command before SQLite:

```text
git -C <governed-target> -c core.fsmonitor=false \
  check-ignore --quiet --no-index -- <target-relative-state-directory>
```

The operand is exactly the ordinary
`.agents/skills/task-governance-tool/state/` or self-host
`task-governance-tool/state/`, with forward slashes and directory semantics.
The process uses safe Git environment, null stdin/stdout/stderr, and a
two-second timeout. Return 0 is accepted; every other result is
`state_ignore_required`. No marker means a valid non-Git target and no
process. The governed target is never re-rooted. Normal task/handoff/review
commands perform no ignore process.

### Doctor

Doctor combines:

1. bounded package-manifest inspection;
2. the conditional one-process ignore observation; and
3. at most one journal preflight and coherent project snapshot.

It does not claim cross-source atomicity. `project_state` owns layout and DB
readiness. When unavailable, task/handoff/maintenance details use fixed
unavailable objects. Package modified/unknown is advisory even when project
state produces exit 2. Relocation mismatch is a successful
`relocation_required` project-state advisory, never a token. Every recognized
advisory has `suggested_action=continue`; doctor creates no lock, directory,
sidecar, database, event, backup, or Viewer.

The package inspector strictly parses manifest v1, caps it at 256 KiB and 512
core entries, rejects unknown/duplicate keys, invalid identity/version/origin/
hash/path, traversal/backslashes/absolute paths, casefold collisions, and
entries in excluded regions. It enumerates physical regular core without
following links. Only root `config/`, `adapters/`, generated `state/`,
`__pycache__/`, and `*.pyc` are excluded. Files are streamed with per-file and
aggregate bounds and pre/post identity checks.

Results are `clean`, `modified`, or `unknown`; paths are bounded relative names
sorted and capped to 20. No content, expected/actual hash, absolute path, link
target, or OS error is emitted. The co-located manifest is unsigned and is a
local drift detector, not authentication. Doctor never downloads, repairs,
updates, or compares upstream state.

### Setup Plan And Stages

Read-only setup returns a deterministic plan for restore, initialization,
migration backup, migration, maintenance configuration, and Viewer publication
plus effective backup interval/retention. It creates no directory, lock,
temporary, sidecar, SQLite recovery connection, or HTML.

Write setup revalidates scope immediately before each irreversible stage. A
completed stage is not rolled back because a later stage fails; the exact
ordered partial result makes rerun resume. Initialization, migration, recovery,
relocation, maintenance opt-in, and Viewer publication use services directly,
not removed CLI subprocesses.

When canonical DB is absent, setup scans only canonical managed backups:

- no managed name => fresh initialization;
- at least one valid same-project generation => mechanically newest
  `(published_at, generation_id)` recovery;
- managed names but no valid same-project generation => fail closed.

Invalid, foreign, linked, or unrelated files are preserved. Recovery holds the
artifact lock, revalidates the candidate, copies it through SQLite backup to a
sibling temporary DB, reconciles only supported backup metadata, validates
schema/identity/quick/FK/regular-file state, and publishes no-clobber. A
lexical rollback journal beside a missing canonical DB is rejected before
selection and publication and never opened/deleted. A concurrently appearing
canonical DB is never overwritten.

A supported old recovery migrates normally; current recovery proceeds to
Viewer. Fresh initialization repeats candidate/journal absence under the lock
immediately before creation. Setup never silently substitutes initialization
for a stale recovery plan.

### Maintenance Policy And Managed Backups

Schema v10 has one `project_maintenance` row with immutable one-way
`enabled_at`, backup interval/generation policy, applied retention, shared
backup success/outcome/latest generation, and Viewer success/outcome. Setup
defaults a new policy to 30 minutes and three generations. Public bounds are
1-1,440 minutes and 1-20 generations. Omitted values preserve existing
policy; equal explicit values replay. A policy-only change does no copy/prune,
and a reduced retention becomes applied only with the next successful
publication.

The sole backup primitive:

1. opens a rollback-journal source through the operational boundary;
2. copies with `sqlite3.Connection.backup` to a fresh contained temporary;
3. validates schema support, identity/binding, quick check, and foreign keys;
4. closes it;
5. atomically publishes a canonical generation with fixed
   publication-retention metadata; and
6. returns only bounded generation/time metadata.

Managed names are exactly:

```text
taskgov-backup-v1_<YYYYMMDDTHHMMSSZ>_<32hex>_r<1-20>.sqlite
```

The generation ID is `tg_backup_<same 32hex>`. Other names and nonregular,
linked, invalid, or foreign files are never managed or pruned.

Schema v11 `managed_backup_generations` is the authoritative retention set.
Every setup/routine backup under the same zero-wait artifact lock reconciles
at most the bounded file/row crash residue: import one valid file-only
generation, remove an unusable row target without following an untrusted path,
and complete file-before-row pruning using applied retention. Publication
writes the file, then inserts its row and updates pointer/outcome/applied
retention in one short transaction, then prunes file before row. Failure leaves
the work due. Crash residue is bounded to the prior valid set plus one
in-flight generation.

Setup migration always makes the pre-migration managed backup before changing
schema and binds metadata/pointer before pruning. Rollback therefore means
restoring a matching package, database, and artifact set together; no reverse
migration exists.

### Post-Commit Coordinator

Business services return internal `MutationOutcome(changed,
viewer_relevant)`. After commit and connection close, the coordinator runs:

1. Viewer refresh when relevant; then
2. one backup attempt when due.

They are independent same-process bounded work, never a thread, daemon, timer,
queue, scheduler, service, sleep, or retry loop. Each uses its own zero-wait
one-byte OS lock. Lock-file existence is not ownership and process termination
releases the lock. Read, error, replay, no-op, configuration-only setup, doctor,
Effort, and maintenance metadata writes do not invoke it.

Backup is due when no success exists, the last outcome is deferred/failed, or
the configured interval elapsed. Failed attempts do not advance success.
Eligible mutations are Task add/edit/complete/checkpoint, handoff
record/withdraw, and review target/receipt/finding add/resolve. Handoffs are
not Viewer-relevant because the snapshot excludes the outbox.

Viewer-relevant mutations increment source generation through the
`task_events` trigger in the same transaction. Refresh compares source/rendered
generation, renders once and rechecks once, for at most two renders. Setup
initial/migration/repair uses its direct Viewer stage and does not re-enter the
coordinator.

Only fixed warnings are added after a successful primary command:

```text
viewer_refresh_deferred | viewer_refresh_failed
backup_deferred         | backup_failed
```

Messages state that the primary result is unchanged and include no path,
exception, hash, raw output, retry, stop, or model choice. Doctor observes the
latest outcome without starting work.

## Static Viewer

### Snapshot And Publication

The Viewer is a replaceable projection, never authority:

```text
committed Viewer-relevant event
  -> source generation
  -> post-commit coordinator
  -> zero-wait Viewer lock
  -> one compatible query-only snapshot
  -> snapshot v4 + captured generation
  -> base64 UTF-8 JSON in bundled template
  -> atomic task-viewer.html replacement
  -> short rendered-generation update
```

Setup invokes the same canonical renderer directly. There is no public Viewer
command, output choice, browser launch, server, or browser-to-SQLite path.

The repository selects all project Tasks in list order, at most 10 newest
events per Task by time/rowid, review evidence through the shared gate read
model, and bounded completion history. Snapshot v4 contains snapshot/source
schema versions, generated time, project ID/display only, counts, and explicit
Task/event/evidence/history allow-lists. It excludes repository/database paths,
tool events, handoffs, checkpoints, maintenance state, internal generation,
event-cycle links, environment, and raw review material.

Source schemas v5-v14 synthesize empty/incomplete completion history;
v15-v16 use stored cycles, reading completion histories in batches of at most
500 Task IDs. Every snapshot reports its actual source schema and selects all
project Tasks; 500 Tasks is the accepted performance fixture rather than a
selection cap. The rendered artifact is capped at 64 MiB.

Snapshot JSON is deterministic UTF-8 and base64-encoded before insertion.
The template has exactly one snapshot placeholder and one decimal refresh
interval placeholder. Stored data reaches the DOM only through `textContent`
or text nodes, never HTML/eval/URL/style/event sinks.

The canonical output and lock remain below fixed state. Resolution rejects
reparse parents, containment changes, DB aliases, and linked/nonregular
destinations. Rendering writes a unique sibling temporary, flushes/closes it,
and `os.replace`s only after complete success; failure retains last-good HTML.

Schema v13 `viewer_maintenance_state` holds nonnegative source generation,
nullable rendered generation not above source, and fixed
`succeeded|deferred|failed` outcome/time. New/migrated state starts source 0
and rendered null. Under the lock, publication captures generation and rows in
one snapshot, closes SQLite, renders/replaces, then conditionally records the
captured generation without lowering a newer value. One recheck permits one
follow-up; later churn remains due.

### Presentation Profile

The only presentation policy is optional:

```text
<physical-skill>/config/viewer.json
```

Absence is valid and disables reload with interval 0. Taskgov never creates or
edits it. A present regular physical UTF-8 file is capped at 16,384 bytes and
must contain exactly:

```json
{
  "schema_version": 1,
  "profile": "visibility-refresh-v1",
  "refresh_interval_seconds": 30
}
```

The interval is a JSON integer 5-3,600; booleans, floats,
duplicate/unknown/missing keys, malformed/trailing JSON, links/reparse paths,
devices, directories, replacement races, or uninspectable metadata fail
closed with one sanitized error. The loader uses no-follow where available and
checks descriptor/path identity before and after the bounded read. Doctor does
not inspect this file.

One publication attempt loads the value once and reuses it for both possible
renders. Setup preview treats an invalid present profile as Viewer
repair-required but writes nothing; write setup and routine publication retain
last-good output and use existing incomplete/warning semantics. Missing or
changed profile/template bytes make setup repair the canonical page.

### Browser Application And Reload

The page provides a compact header, all-status summary, search/status/kind/
lane/priority/tag/terminal filters, responsive Task table, detail/events, and
empty state. Terminal Tasks are hidden by default but filterable. Native
controls, visible focus, labels, non-color status cues, bounded radii, and
wrapping support keyboard and narrow-screen use.

Reload scheduling activates only after successful fatal-UTF-8 decode and
render, with a valid nonzero interval and `file:` protocol. One reconciliation
function owns one timeout:

1. clear the prior handle;
2. stop if disabled, already requested, non-file, or hidden;
3. compute elapsed from page-load `performance.now()`;
4. request exactly one same-document reload once elapsed reaches interval; or
5. schedule only the monotonic remainder.

Timeout and visibility-change callbacks reuse that function. Hidden pages own
no timer. There is no interval timer, polling, wall-clock interval
calculation, fetch/XHR, storage, worker, database access, retry, or message
channel. Browser throttling may make reload late, never early.

Immediately before that automatic reload only, the page may make a one-shot
History API handoff with exact ordered keys:

```text
owner, schema_version, captured_at_ms, status, kind, lane, priority, tag,
terminal, selected_task_id, scroll_x, scroll_y, focus_id
```

Owner is `taskgov-viewer-auto-reload`, schema is 1, and compact UTF-8 JSON is at
most 4,096 bytes. Only allowed filter values, visible selected Task ID,
nonnegative finite scroll coordinates, and one of eight fixed control IDs may
be saved. Search text, Task/snapshot content, arbitrary selectors, URLs/paths,
dynamic-row focus, selection ranges, and nested UI state are prohibited. No
selection means no envelope and reload still proceeds.

Capture time is a nonnegative safe integer; status, kind, and priority are
empty or current enums; lane and tag are each at most 1,024 UTF-8 bytes;
selected Task ID is nonempty and at most 128 code points; coordinates are
finite integers from 0 through 2,147,483,647; and focus is empty or exactly
`search-filter`, `status-filter`, `kind-filter`, `lane-filter`,
`priority-filter`, `tag-filter`, `terminal-filter`, or `reset-filters`.

Save never overwrites a non-null non-owned state and calls only
`history.replaceState(candidate, "")` without URL. Failure never prevents
reload. On a file load, every owned state is cleared before snapshot decode,
even if malformed, stale, non-reload, or decode-fatal. Restore requires
successful clear, navigation type reload, exact keys/types/bounds/current
options, visible selection, current owner/version, and age 0-300,000 ms.
Invalid or failed restore returns filters, selection, focus, and scroll to
defaults.

On an enabled file page or one starting with owned state, manual
`history.scrollRestoration` must be set and read back before UI restoration.
If unsupported, state save/restore is disabled but owned state is still
best-effort cleared and reload continues. History state is browser-managed and
may survive session restoration; it is bounded and one-shot, not described as
memory-only.

The page never uses `pushState`, URL/query/fragment state, cookies, Web
Storage, IndexedDB, Cache API, service workers, cross-tab messaging, or manual
reload capture. It logs no state, bytes, validation reason, exception, URL, or
path. Saving/restoring must change neither `history.length` nor
`location.href`.

### Browser Security

The exact CSP is:

```text
default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; media-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; manifest-src 'none'; base-uri 'none'; form-action 'none'
```

Inline script/style is limited to the fixed single-file application;
`unsafe-eval` is absent. Stored values cannot create markup, script, style,
event handlers, URLs, or resource loads. The browser has no external URL,
telemetry, automatic launch, network API, or database-write code.

## Privacy, Safety, And Failure Boundaries

Every free-form input uses the common deny-by-default privacy guard. Fields
with defined contracts also have code-point/UTF-8 bounds; the existing `lane`
and `blocked_reason` fields are privacy-validated but have no numeric character
cap. `lane` is trimmed; `blocked_reason` is retained as supplied after
string/privacy and state validation. The guard rejects likely credentials and raw dumps,
including authorization/Bearer headers, private-key blocks, password/token/API
key assignments, Python traceback headers, raw stdout/stderr headings, and
repeated raw Git diffs.

The ordinary matcher receives caller text unchanged. It rejects both
`dispatch_authorization=<value>` and the JSON key
`"dispatch_authorization":<value>`, including numeric values; no generic
allow-list or mode exists. Neutral `operation_sequence=<positive canonical
integer>` passes without preprocessing and represents correlation or
idempotency evidence only. External authority remains an independent current
user/Contract decision.

The singular legacy M19.7 stored-text helper creates a privacy-only guard view:
it substitutes bounded lowercase positive-canonical-integer equality and
numeric JSON counter forms with a fixed non-secret sentinel, runs the complete
ordinary detector set, and returns the original text. Call sites are limited
to stored Contract constraints and stored checkpoint summary projection.
Completion history deliberately uses only the ordinary matcher. The helper
does not rewrite a row, broaden a schema or writer, or authorize a Task,
dispatch, Git, network, or other external mutation. Compound credential/token
content remains visible after substitution and fails closed.

Stored or emitted data excludes secrets, cookies, provider bodies,
authorization material, raw stdout/stderr, stack traces, environment dumps,
full prompts/conversations, private reasoning, raw reviews, large diffs,
unsafe paths, and OS/SQLite/Git exception detail. Review, Contract, handoff,
checkpoint, completion, and history projections use explicit allow-lists and
revalidate stored text before output.

Git subprocesses use fixed argument vectors, no shell, bounded timeout, safe
environment, disabled optional locks/lazy fetching, and no target-project
write. Taskgov never creates a commit, branch, PR, Issue, tag, Release, or
network request as product behavior. TG-M19 release operations are repository
release work performed only under their separate approvals, not new Taskgov
commands.

Read-only commands do not create SQLite sidecars, locks, directories, state,
Viewer output, Git changes, or target files. Atomic file publication and
last-good preservation protect generated artifacts. The threat model covers
ordinary misunderstanding, over-implementation, local races, crashes, and
accidental path changes; it does not add signing, hostile-kernel protection, or
general adversarial TOCTOU defense.

## Published Release Artifact And Compatibility Design

The v0.10.0 publication is an external projection of one reviewed repository
commit, not a product runtime subsystem. The exact accepted commit, remote
`main`, and unpeeled lightweight tag `v0.10.0` are
`a9b80ce177a6dead10d51a070b76ff01f7af0294`. GitHub Release `362617903`
has prerelease visibility. Runtime code does not derive current behavior from
a branch, tag, Release, local candidate directory, or historical Task evidence.

The release archive has exactly one `task-governance-tool/` root and is built
from the accepted commit with the package subtree as the sole pathspec. The
package `release-manifest.json` is the inventory and digest boundary for
packaged core files. Root and packaged `LICENSE` bytes are the same official
unmodified Apache-2.0 text, and the package license is manifest-covered.
Generated state, SQLite files and sidecars, backups, locks, Viewer output,
target configuration, source-repository tests and fixtures, root references,
caches, logs, secrets, and scratch output remain outside the artifact.

The published archive and checksum identities are fixed:

```text
archive              task-governance-tool-0.10.0.zip
archive sha256       99fc2345fd036091349c47f7379eee25b8b3b4c8873c0f74aaceac323bb82a03
checksum             task-governance-tool-0.10.0.zip.sha256
checksum sha256      9cdc99bd26cc4887bd88ef2ec638659224f0a0d8f567edb12a3800d59a8b6764
release notes        docs/releases/v0.10.0.md
notes sha256         aaa118a3fbbb261ec6a24f7a80f50f161e606a86857f99e17f957f34ba044a03
candidate CI         run 30561916953, attempt 1
remote-main CI       run 30565181070, attempt 1
```

Release staging, GitHub workflow selection, push readback, and Release upload
are orchestration outside the CLI architecture. They add no taskgov command,
schema table, runtime module, background process, network client, credential
store, or target-project mutation path. A later release may reuse the durable
principles—exact reviewed tree, manifest-complete package, deterministic local
checks, explicit authority for each external mutation, and readback after an
ambiguous write—but must define and approve its own exact identity and
evidence. Completed v0.10.0 approval objects and gate checkpoint schemas never
authorize a future operation.

The release boundary is immutable by policy. A defect creates a reviewed
forward-fix candidate and new version. Routine repair does not force-update
`main`, rewrite history, move or replace a published tag, replace an existing
asset, or delete a Release to hide disagreement.

The accepted upgrade rehearsal treated package files, database, and managed
artifacts as one compatibility point. It created legacy schema-v2 state with
the exact legacy package, preserved that state while installing v0.10.0,
allowed current `setup` to back up and migrate through schema v16, and proved
restart, identity/record preservation, quick check, foreign keys, and Viewer
publication in isolation. The paired rollback restored the matching legacy
package, database, and managed artifacts before running legacy code. Old code
against v16, reverse migration, mixed generations, arbitrary `--db` state,
and a source checkout used as state rollback remain unsupported.

The detailed M19 history switch, candidate staging, evidence schemas, remote
state machine, and just-in-time activation rules were one-time execution
design. The exact publication-commit form is indexed by
[the historical documentation index](history/README.md); no active product or
release guarantee depends on that historical copy.

## Completed TG-M20 Study Boundary

TG-M20 was a one-time, offline repository-development study against exact
product baseline `43c91d5987b0c35c66f834789aea782e98dcaff7`. Its frozen
observation authority was completed at
`a77afbe0140fef416cceeee529e9ff2c985a8e4d`; that authority never replaced
or modified the product baseline. The reviewed stratified aggregate,
denominators, exclusions, decision basis, limitations, and follow-up
inventories are preserved only in
[non-authoritative study history](history/v0.10.0/m20-operational-baseline.md).

The recorded outcomes are:

| Candidate | Outcome |
|---|---|
| TG-M21 Verification Receipts | `proceed_to_design` |
| Skill-only proportional-verification guardrail | `observe_more` |
| Bounded user-approved Task decomposition | `observe_more` |
| Bounded successor observation for the inconclusive candidates | `proceed_to_design` |

These outcomes are decision inputs, not product behavior. A positive result
authorizes only a separately approved and reviewed design proposal. It does
not activate a Skill instruction, public command, telemetry path, SQLite
schema, Viewer projection, test-strategy engine, runtime Task split, automatic
Task creation, or parent/child Task model.

The temporary root-only collector, reconstruction and fresh-trial tools, their
dedicated tests, the study protocol and episode-plan fixtures, the ignored
memo, and all ignored reduced corpora/locks/trial remnants are retired by
TG-M20.5. No current runtime, package, Skill, test lane, or active product
contract depends on those study-only surfaces.

The three tracked `m20-collection-receipt-v1` JSON files under
`fixtures/m20/` remain only as no-rerun tombstones. Each retains the unit,
authority and baseline revisions, protocol digest, corpus digest and size,
record/eligibility counts, terminal outcome, `artifact_status="retired"`,
and the exact retirement-anchor commit. M20.4 additionally retains the
canonical episode-plan digest. A receipt is provenance for the deleted study
input, not study evidence, current verification evidence, product state, or
authority to reconstruct, rerun, or rescore the corpus.

The TG-M20S Task-decomposition successor is separately approved below. Any
other Verification Receipt design, Skill-only guardrail observation, or Task-
decomposition observation still requires new explicit user approval and a
separate bounded execution contract. Historical candidate inventories remain
non-authoritative and cannot add an attempt or change the approved successor.

## Approved Temporary TG-M20S Successor Observation Design

### Authority, Comparison Baseline, And Inventory

The observation authority is not a self-referential Git revision. It is the
immutable tuple:

```text
authority_task_id=tg_task_ddfbf721eced8c58
authority_contract_revision=1
authority_ref=conversation_decision:2026-08-01:interrupt-successor-task-decomposition-observation
```

Every protocol, episode plan, launch commitment, reduced record, corpus, and
terminal receipt binds that complete tuple. The exact comparison baseline is
`43c91d5987b0c35c66f834789aea782e98dcaff7`; its installable
`task-governance-tool/` subtree and the same subtree at TG-M21.1 completion
`fc2e0870ad9bf70830a082df168ad1992e07b51d` both resolve to tree
`529abf7ac4e4ed778b383c90b6ac5f2fedc71615`. A trial checks both the baseline
commit and package tree before launch. The equal later tree establishes
comparability only and never changes the baseline or observation authority.

The fixed order is `sp_user_expansion_alternate`,
`sp_in_scope_discovery_alternate`, then
`sp_cross_module_failure_alternate`. Each scenario has exactly `broad` and
`bounded` arms with trial IDs `<scenario_id>.<arm>.01`. Each complete pair uses
one workload digest and the same model, tool, and permission cohort. The
bounded request separately authorizes at intake the independently acceptable,
verifiable, committable, and completable work represented by the broad
request's later pressure. These are new replacement IDs; no retired M20 trial
or scenario is reopened.

TG-M20S.1 owns the protocol and lean-harness freeze. No trial may launch until
its exact final target, including the protocol and episode-plan digests, has
passed two independent Tier 2 reviews. TG-M20S.2, Task
`tg_task_e591f30d546ba69e`, owns every launch, reduction, aggregate decision,
history routing, and retirement.

### Minimal Root-Only Harness And Reduced Schemas

The temporary harness is offline, shell-free, root-repository-only, and
limited to the six fixed arms and their predefined safe checks. It may reuse
reviewed pure M20.4 concepts, but it does not restore M20.2 reconstruction,
M20.3 verification-proportionality collection, a generic observer, or any
retired corpus. It neither enters the installable package nor instruments the
product, canonical Task database, Viewer, network, or a real target project.

One reduced record is canonical compact sorted-key UTF-8 JSON with exactly:

```text
schema, authority_task_id, authority_contract_revision, authority_ref,
baseline_revision, package_tree, protocol_sha256, episode_plan_sha256,
observation_id, scenario_id, arm, trial_id, record_key, evidence_class,
eligibility, unknown_reasons, unknowns, payload
```

`schema` is `m20s-decomposition-observation-v1`. `record_key` is
`split_measurement` or `attestation`; the matching evidence class is
`machine_observed` or `observer_attested`. `eligibility` is `eligible`,
`partial`, or `excluded`. IDs are lowercase ASCII codes of at most 64
characters, the two digests and package tree are complete lowercase SHA-256 or
Git-tree hex as applicable, and `observation_id` is `m20sobs_` plus the full
SHA-256 of the domain-separated authority, baseline, package-tree, protocol,
episode-plan, scenario, arm, trial, and record-key tuple. IDs are unique and
never deduplicated.

Each arm emits one record of each kind. A source-level failure emits a newly
constructed sanitized excluded envelope for each intended record; it does not
copy rejected input. The complete corpus therefore has at most 12 records.
Protocol and episode-plan fixtures are each capped at 32,768 UTF-8 bytes; each
record at 16,384; the journal and canonical sorted-array corpus at 65,536; and
the terminal receipt at 4,096. Exceeding a bound fails closed without
truncation or selection. `unknown_reasons` has at most eight values and is the
sorted unique union of bounded per-field `unknowns`, using only
`not_observable`, `source_missing`, `source_drift`, `parse_failed`, `timeout`,
`cap_exceeded`, `observer_uncertain`, or `contaminated`. Unknown and null never
mean zero. A partial record has only declared nullable fields and exact
unknown entries; `source_missing`, `source_drift`, `parse_failed`, or
`contaminated` excludes the record and makes all of its payload unusable for a
decision.

`split_measurement.payload` contains only `episodes`, with 1-8 items in the
episode-plan order and these exact keys:

```text
episode_id, files_before, files_after, modules_before, modules_after,
lines_before, lines_after, contract_revision_before, contract_revision_after,
review_generation_before, review_generation_after, governance_cycles,
review_cycles
```

The episode plan fixes each task slot and half-open measurement boundary.
Revision and generation deltas cannot be negative. For same-ID comparison the
machine vector is, in order, file, module, line, Contract-revision, review-
generation, governance-cycle, and review-cycle delta.

`attestation.payload` has exactly `cohort`, `workload_digest`,
`control_digest`, `outcome`, `reference_opens`, `clarification_turns`,
`manual_inputs`, `governance_invocations`, `reviewer_invocations`, and
`episodes`. The five supporting counts are capped at 256, 16, 32, 64, and 8
respectively and never satisfy a decision predicate. Each 1-8 item episode has
exactly:

```text
episode_id, phase, cause, current_response, acceptance_independent,
verification_independent, commit_independent, completion_independent
```

Phase is `intake`, `implementation`, `verification`, or `review`; cause is
`in_scope_discovery`, `user_expansion`, `repeated_failure`, or
`cross_module`; response is `keep_current`, `block`, or `handoff`; and each
independence value is `yes`, `no`, or `unknown`. An eligible attestation has no
unknown independence value and its episode IDs exactly equal the paired
measurement and opposite arm in the same order.

### Eligibility, Qualification, And Decision

A trial is excluded before semantic scoring when its authority, baseline,
package tree, protocol, episode plan, workload/control commitment, isolation,
or required schema/privacy boundary differs; context is inherited; another
trial's artifact is visible; the paired cohort differs; mid-trial coaching
occurs; or a required source is missing or invalid. A bounded declared unknown
makes a record partial. Exclusion and partiality are fixed before applying the
outcome rubric, and an unfavorable outcome is never a reason to relabel or
rerun an arm.

A pair is eligible only when all four required records are eligible and both
arms have identical frozen identity, workload digest, cohort, and ordered
episode IDs. An eligible pair qualifies only when every bounded episode has
all four independence fields `yes` and at least one condition holds:

1. a same-ID broad episode has an independence field `no` and at least one
   machine-vector position greater than the bounded position;
2. `sum(bounded contract_revision delta) <= sum(broad contract_revision
   delta) - 1`; or
3. `sum(bounded review_cycles) <= sum(broad review_cycles) - 1`.

The active decision authority carries forward, without reconstruction or
rescoring, the reviewed eligible and qualifying `sp_multi_outcome_intake` pair
and eligible M20.4 Handoff control. Initially `E=1`, `Q=1`, and `U=3`; after
each complete replacement pair, `E=1+eligible replacements`,
`Q=1+qualifying replacements`, and `U=4-E`. With no conflict and the retained
Handoff control:

- `Q>=2` is positive `proceed_to_design` and stops before another pair;
- `E>=3 && Q+U<2` is negative `no_follow_up`; from the initial values it
  requires all three replacements to be eligible and nonqualifying, yielding
  `E=4,Q=1,U=0`; and
- exhaustion without either rule is `observe_more`.

An arm already started reaches exactly one terminal eligible, partial, or
excluded outcome. A pair is evaluated only after both arms terminalize. An
unlaunched arm after a positive stop is not a failed attempt. Timing, size,
line, invocation, and count values are supporting context only.

### Neutrality, No-Rerun, Receipt, And Retirement

Fresh agents receive no parent conversation, temporary memo, hypothesis,
rubric, expected verdict, suspected failure, preferred solution, earlier arm
output, or mid-trial feedback. Paired control bundles are prepared together,
share an exact workload digest, and receive independent pre-launch review.
Their exact bytes exist only in memory or an isolated trial-local temporary
area. Before an arm enters `started`, a no-rerun journal binds its workload,
control, observer-config, and sanitized trial-root digests. Every started arm
gets one reduction attempt; success, failure, timeout, privacy rejection, or
cleanup failure never authorizes a rerun or replacement.

No reduced or tracked artifact retains raw paths, requests, prompts, chats,
reasoning, review bodies, provider payloads, credentials, Task/Contract prose,
diffs, streams, logs, stack traces, or environment. Raw work, control bytes,
and candidate reduced bytes are destroyed immediately after the one reduction
attempt; a rejected candidate is replaced only by a new safe excluded
envelope.

The sole retained tombstone is canonical
`m20s-decomposition-collection-receipt-v1` JSON with exactly `schema`, `unit`,
`authority_task_id`, `authority_contract_revision`, `authority_ref`,
`baseline_revision`, `package_tree`, `protocol_sha256`,
`episode_plan_sha256`, `status`, `artifact_status`, `retirement_revision`,
`attempted_pairs`, `attempted_arms`, `record_count`, `corpus_bytes`,
`corpus_sha256`, `eligible_pairs`, `qualifying_pairs`, `unavailable_pairs`,
and `decision`. Decision is `proceed_to_design`,
`no_follow_up`, or `observe_more`. It records no raw material and is not enough
to reconstruct or rerun a trial.

TG-M20S.2 routes the reviewed result and limitations to active decision
authority and one indexed non-authoritative history capture, then removes the
temporary protocol, episode plan, harness, tests, fixtures, controls, corpus,
locks, journals, and trial remnants. Only after that removal may the receipt
be `artifact_status="retired"` with its exact retirement anchor. A positive
decision authorizes only a separately approved Tier 2 design Task. No outcome
changes current Skill, CLI, schema, Viewer, completion, Task-creation, or Task-
splitting behavior.

## Validation And Test Design

The suite is standard-library-first, offline, and isolated. It must not mutate
a real consuming project or Git state. Tests cover:

- all 20 parser leaves, removed commands/options, help, text/JSON/error/compact
  envelopes, and byte limits;
- missing/old/too-new/invalid state with no creation or sidecars;
- every v1-v16 migration, rollback, idempotency, required-object marker,
  realistic preservation fixture, quick check, and foreign keys;
- task validation, ordering, pause/block/current/next, done/reopen,
  completion evidence, every review tier/target/receipt/finding, Contract,
  checkpoint, handoff, and Effort route;
- Git option safety, canonical commit resolution, snapshot/index/tree binding,
  no mutation, and no lazy network;
- concurrent readers/writers, short writer ownership, stable busy/WAL errors,
  and injected rollback points;
- UUID identity, legacy identity, fixed/legacy resolver inventories,
  same-binding publication, recovery, relocation token/replay/expiry, staged
  no-clobber publication, cleanup resume, and preservation of unrelated files;
- backup publication/reconciliation/retention/recovery and every crash
  boundary;
- Viewer v4 sources 5-16, completion-history bounds, 500-ID history batching,
  the accepted 500-Task performance fixture, 64-MiB artifact cap,
  generation/last-good behavior, strict config, timer/visibility, one-shot
  History state, CSP, text-only DOM, and absence of storage/network APIs;
- package self-containment, manifest integrity, project-scoped/self-host
  layouts, ignore rules, Windows Python 3.12/3.14, and junction rejection;
- M16 fresh-session behavioral fixtures, nine-call default flow and
  mechanically enabled ten-call flow; and
- release archive reproducibility, license/manifest/archive inclusion,
  legacy upgrade/paired rollback, exact workflow identity, and sanitized
  release evidence.

Repository release consistency uses the same offline read-only checker from
focused unit fixtures and CI. Negative fixtures independently cover a missing
manifest or declared file, extra packaged core, digest mismatch, license or
manifest-license mismatch, invalid Skill/agent/manifest metadata, parser-to-
document drift, and tracked generated artifacts. The checker does not replace
installed-CLI, no-write, physical-isolation, migration, upgrade/rollback, or
release-acceptance behavior tests.

The deterministic repository runner assigns every discovered test module to
exactly one base lane:

- `fast` covers parser, validation, state transitions, pure helpers, and small
  temporary repositories;
- `integration` covers setup, recovery, relocation publication, backup,
  Viewer, concurrency, consumer layouts, and normal migration; and
- `release` covers legacy migration/recovery, upgrade and paired rollback,
  performance, package/license, active documents, workflow policy, and
  integrated release acceptance.

Every run first validates the complete manifest. A base lane is an ordered
filter of standard discovery, while `all` runs the original discovered suite
in its original order. New `test*.py` modules therefore fail closed until
classified; new methods in an owned module inherit that module's lane.

CI obtains one compact include matrix from this same policy. Pull requests run
all three base lanes on Python 3.12 and `fast` on 3.14. Pushes to `main` run
all three base lanes independently on both versions. Manual
`workflow_dispatch` runs monolithic `all` on both versions, and an
`always()` aggregate job fails unless policy validation and the full matrix
succeed. The workflow trigger remains limited to pull requests, pushes to
`main`, and manual dispatch; a candidate gate cannot be replaced by a skipped
dependency job.

Tier-2 changes use two independent exact-target reviews. Tests hard-fail count,
byte, subprocess, attempt, and render limits; performance budgets never justify
loosening a deterministic bound or adding asynchronous architecture.

## Deferred Boundaries

Deferred work includes profile authoring, active verification-run recording,
external Issue delivery, dependency graphs, task import, pagination/search,
stale detection, parent/child/checklist execution units, manual backup/restore/
export, generic browser-state persistence, live server, browser launch,
network synchronization, and update checking.

Any extension, including the inactive M21 design, must preserve local-first
operation, current privacy and target-project safety, explicit authority for
mutation, narrow repository boundaries, and concise Skill guidance. It
requires synchronized specification, design, roadmap, tests, and review rather
than reuse of a historical design capture.
