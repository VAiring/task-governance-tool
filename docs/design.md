# task-governance-tool Current Implementation Design

Status: the published product is v0.10.0 with SQLite schema v16 and Viewer
snapshot v4 accepting source schemas v5-v16. The accepted release commit,
remote `main`, and lightweight tag `v0.10.0` are
`a9b80ce177a6dead10d51a070b76ff01f7af0294`; GitHub Release `362617903` has
prerelease visibility. TG-M19.0 through TG-M19.10, including TG-M19.6A and
TG-M19.6B, are complete. This post-release revision is the consolidated active
implementation authority. TG-M16.4 behavioral acceptance remains part of the
published baseline.

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

## TG-M20 Repository-Only Observation Design

This section defines a planned one-time repository-development study. TG-M20.1
records the contract only; it does not implement collection. The design is not
a taskgov output contract, product telemetry, a public command, a SQLite
extension, a Viewer field, a Skill trigger, or a normal-loop call.

The product baseline is exact commit
`43c91d5987b0c35c66f834789aea782e98dcaff7`. The completed TG-M20.1 commit
becomes the observation-authority revision, but never replaces or modifies
that product baseline. Every prospective trial uses a clean isolated
materialization of the baseline. The canonical project Task database,
uncommitted or ignored local material, temporary M20 notes, and prior trial
output are outside the trial subject.

### Frozen Collection Inventory And Adjudication

The study launches each listed attempt exactly once. The prose below fixes
scenario semantics without retaining the subject's delivered prompt text.

| Unit | Scenario ID | Arm(s) | Planned attempts |
|---|---|---|---:|
| M20.2 | `gov_tier1_commitless` | `harness` | 1 |
| M20.2 | `gov_tier2_snapshot` | `harness` | 1 |
| M20.2 | `gov_pause_resume` | `harness` | 1 |
| M20.2 | `gov_handoff_continue` | `harness` | 1 |
| M20.2 | `gov_reopen_rereview` | `harness` | 1 |
| M20.2 | `m19_preparation_reconstruction` | `retrospective` | 1 |
| M20.2 | `m19_publication_reconstruction` | `retrospective` | 1 |
| M20.2 | `m19_postrelease_reconstruction` | `retrospective` | 1 |
| M20.3 | `vp_cli_contract` | `baseline` | 1 |
| M20.3 | `vp_state_transition` | `baseline` | 1 |
| M20.3 | `vp_release_contract` | `baseline` | 1 |
| M20.4 | `sp_multi_outcome_intake` | `broad`, `bounded` | 2 |
| M20.4 | `sp_in_scope_discovery` | `broad`, `bounded` | 2 |
| M20.4 | `sp_user_expansion` | `broad`, `bounded` | 2 |
| M20.4 | `sp_cross_module_failure` | `broad`, `bounded` | 2 |
| M20.4 | `sp_handoff_control` | `broad` | 1 |

Every M20.2 harness and M20.3/M20.4 fresh-agent `trial_id` is exactly
`<scenario_id>.<arm>.01`. Every record for the three retrospective scenarios
has `trial_id=null`; a formatted retrospective trial ID is invalid. The
fresh-agent cohort code is `fresh_baseline_v1` for every M20.3/M20.4 attempt.
Channel record keys are exactly `cli`, `state_pair`,
`verification_measurement`, `split_measurement`, the reconstruction metric
code, or `attestation` as applicable.

Record cardinality is fixed. Each M20.2 harness attempt emits one `cli` and
one `state_pair` record; each retrospective scenario emits one record for each
of the 12 listed reconstruction metrics. Each M20.3 attempt emits one
`state_pair`, one `verification_measurement`, and one `attestation`. Each
non-control M20.4 arm emits one `split_measurement` and one `attestation`; the
Handoff control arm additionally emits one `state_pair`. An excluded
source-level envelope replaces its intended record and never adds another.

The M20.2 scenarios cover respectively a Tier-1 commit-not-required path, a
Tier-2 Git-snapshot/commit path, pause and resume, local Handoff while accepted
work continues, and complete/reopen/re-review/recomplete. They are deterministic
CLI fixtures, not LLM subjects.

The three retrospective cohorts are also fixed: preparation is TG-M19.0
through TG-M19.5; publication is TG-M19.6A, TG-M19.6B, TG-M19.6, and
TG-M19.7 through TG-M19.10; post-release is TG-M19.11 through TG-M19.14.
Their exact Task IDs and completion revisions are the active roadmap's concise
completion-index rows. No other M19 Task enters an aggregate.

M20.3 gives a fresh subject one normal localized change in each of three
fixture classes: CLI/output contract with an existing checker, Task
state-transition behavior with existing state fixtures, and release metadata
consistency with the existing release checker. Every trial requires the
target-change/revert sensitivity check and the fixed reduced fields below.

M20.4 fixes four paired cases: multiple independently completable intake
outcomes; in-scope independently completable work discovered during execution;
explicit user expansion; and repeated-failure cross-module expansion. In each
`bounded` arm the same work is separately authorized at intake. The unpaired
control is truly out of scope and must use Handoff.

A trial is excluded before semantic scoring when its baseline differs, context
is inherited, state is not isolated, another trial's artifact is visible, the
paired model/tool/permission cohort differs, mid-trial coaching occurs, or the
required reduced record fails schema/privacy validation. A valid protocol with
a capped required measurement is partial. Exclusion and mechanically
determined partiality are locked before the parent applies the outcome rubric.
An observer record becomes partial only when the frozen rubric yields
an `unknown` required field with `observer_uncertain`, a successfully observed
common-count source cannot provide its count and uses `not_observable`, or a
mechanically counted common field exceeds its fixed cap with `cap_exceeded`;
it is never excluded or rerun because its semantic outcome is unfavorable.

There is no rerun, replacement subject, agent reuse, adaptive scenario,
optional arm, or early stop in M20.2-M20.4. A failed or excluded attempt remains
in its denominator. Unit tests may repeat synthetic/injected inputs but are not
study attempts. Insufficient eligible attempts produce `observe_more` at
M20.5; any additional subject belongs to a separately approved bounded
observation unit.

### Reduced Observation Envelope And Identity

One reduced observation is canonical compact sorted-key UTF-8 JSON with exact
top-level keys:

```text
schema, contract_id, contract_revision, baseline_revision,
authority_revision, observation_id, scenario_id, trial_id, record_key, unit,
evidence_class, channel, eligibility, unknown_reasons, unknowns, payload
```

The fixed values and bounds are:

- `schema="m20-operational-observation-v1"`;
- `contract_id="TG-M20-OPERATIONAL-BASELINE"` and
  `contract_revision=1`;
- `baseline_revision` is the exact 40-hex product baseline above;
- `authority_revision` is the exact 40-hex TG-M20.1 completion commit;
- `scenario_id`, non-null `trial_id`, and `record_key` are
  non-identifying lowercase ASCII `[a-z0-9._-]` strings of 1-64 characters;
  `trial_id` is null only for a retrospective record;
- `unit` is `M20.2`, `M20.3`, or `M20.4`;
- `evidence_class` is `machine_observed`,
  `historically_reconstructed`, or `observer_attested`;
- `machine_observed` permits `cli_invocation`, `state_projection`, and
  `trial_measurement`; `historically_reconstructed` permits only
  `task_git_reconstruction`; `observer_attested` permits only
  `fresh_agent_trial`; and
- `eligibility` is `eligible`, `partial`, or `excluded`. A channel that
  is not applicable emits no record.

Observation identity uses ASCII bytes exactly:

```text
"m20-observation-v1\0"
+ contract_id + "\0"
+ decimal contract_revision + "\0"
+ baseline_revision + "\0"
+ authority_revision + "\0"
+ unit + "\0"
+ scenario_id + "\0"
+ (trial_id or "") + "\0"
+ evidence_class + "\0"
+ channel + "\0"
+ record_key + "\0"
```

`observation_id` is `m20obs_` plus the complete 64-lowercase-hex SHA-256
of that preimage; it is therefore exactly 71 ASCII characters. No suffix,
random value, or truncation is allowed. A corpus may contain each ID once.
Any duplicate ID, whether bytes agree or disagree, invalidates the corpus as
`source_drift`; collection never silently deduplicates or resolves a
collision.

`unknown_reasons` is the sorted unique union of every per-field reason and
has at most nine values from `not_observable`, `not_reconstructable`,
`source_missing`, `source_drift`, `parse_failed`, `timeout`,
`cap_exceeded`, `observer_uncertain`, and `contaminated`. `unknowns`
is a field-name-sorted array of at most 128 objects with exact keys `field`
and `reasons`; field is a payload-relative lowercase dotted path matching
`[a-z][a-z0-9_.]{0,95}`, and reasons is a nonempty sorted unique subset of
the same enum. Each field occurs exactly once. Array elements use a zero-based
decimal component, for example `operations.0.stdout_bytes` or
`data.episodes.0.files_before`. A semantically known null such as a timed-out
operation's `exit_code` has no unknown entry.

Reason applicability is exact. `not_reconstructable` is historical-only and
`observer_uncertain` is observer-only. `timeout` is machine-only;
`cap_exceeded` applies to a machine field or a mechanically counted observer
common field. `not_observable` may describe a machine or observer field that
the frozen source boundary does not expose. `source_missing`, `source_drift`,
`parse_failed`, and `contaminated` invalidate every evidence class. A
source-level failure uses the single path `payload`; a field-level reason uses
the most specific leaf path. The 128-entry bound covers the largest valid
shape, the 96 nullable numeric leaves of eight split-pressure episodes, as
well as the 64 independently capped CLI stream counters and 30 state leaves.
An attempted shape beyond a declared channel maximum is not grouped or
truncated: it yields only a sanitized excluded record with
`unknowns=[{"field":"payload","reasons":["parse_failed"]}]`.

An eligible record has empty `unknown_reasons` and `unknowns` and every
required measurement in its channel-specific complete form. A partial record
has at least one declared nullable/unknown field, a matching `unknowns`
entry, and the exact union in `unknown_reasons`. An excluded record has at
least one reason that invalidates the trial protocol or record
(`source_missing`, `source_drift`, `parse_failed`, or `contaminated`)
and no field may be used in a decision. Unknown and null never mean zero.
Only a source-level excluded record may use `payload=null`, paired with the
single `payload` unknown entry; every other record uses its complete
channel-specific payload shape.

One record is at most 32,768 UTF-8 bytes and one unit's complete reduced corpus
is at most 262,144 bytes. Unless narrowed below, a count is a JSON integer from
0 through 2,147,483,647; a product revision/generation/count may use 0 through
9,223,372,036,854,775,807. JSON booleans are used only where declared. Floats
are forbidden. Every named key is always present; only fields explicitly
declared nullable may be null. Arrays preserve declared order; set-like arrays
are unique and ASCII-byte sorted.

For the unit cap, all candidate observation objects are sorted by ASCII
`observation_id` and serialized as one compact sorted-key UTF-8 JSON array with
no BOM or trailing newline. The array bytes are the sole measured corpus.
The fixed maximum record counts are 46 for M20.2, 9 for M20.3, and 19 for
M20.4. When the array exceeds 262,144 bytes, no record is truncated, dropped,
or selected: the candidate array is discarded and replaced by one small
non-evidence failure object with exact keys `schema`, `unit`, `reason`,
`record_count`, and `candidate_bytes`. Its schema is
`m20-operational-corpus-failure-v1`, unit is the owning unit code, reason is
`cap_exceeded`, and the final two values are nonnegative integers. That unit
contributes no eligible bundle and therefore
forces `observe_more` for every dependent decision. When within the cap, the
exact array is the only retained unit corpus.

### Machine-Observed Channels

`cli_invocation.payload` has exactly `operations`. Only the five M20.2
harness scenarios use it. Each has 1-32 operations, so the study maximum is
160. Every operation has exactly:

```text
ordinal, phase, command_leaf, duration_ms, duration_capped, result,
exit_code, warning_codes, error_codes, stdout_bytes, stderr_bytes
```

Ordinal is contiguous from 1. Phase is `setup`, `rediscover`, `select`,
`activate`, `execute`, `verify`, `review`, `complete`, or
`diagnose`. Command leaf is one of the 20 current leaves owned by the active
specification and uses dotted repository spelling. Duration is monotonic whole
milliseconds from 0 through 300,000. Result is `success`, `input_error`,
`service_error`, or `timeout`. A valid parsed response maps `success` to raw
envelope `ok=true`, `exit_code=0`, and an empty error-code array;
`input_error` to `ok=false`, `exit_code=1`, and at least one error code; and
`service_error` to `ok=false`, `exit_code=2`, and at least one error code. The
raw `ok` value is validated but not retained. Timeout requires
`duration_ms=300000`,
`duration_capped=true`, `exit_code=null`, and empty warning/error-code arrays
because no response body is trusted. Every other result requires
`duration_ms` from 0 through 299,999 and `duration_capped=false`. A parsed
result/`ok`/exit/error disagreement or any other exit code is excluded with
`parse_failed`; it is never coerced. Warning/error arrays each contain at most
16 unique ASCII-byte-sorted stable codes matching
`[a-z][a-z0-9_]{0,63}`. Stream byte counts are integers capped at 1,048,576.
A larger stderr stores the cap and makes the record partial with
`cap_exceeded`. A larger stdout does the same only when the complete JSON
envelope ends within the retained prefix and every discarded byte is JSON
whitespace. Otherwise the bounded reader cannot validate the envelope and the
attempt is excluded with `parse_failed`; it never trusts a truncated object.
Bodies are parsed only in bounded memory and discarded immediately.

`state_projection.payload` has exactly `before` and `after`, preventing
separate phase identities. Each state object has exactly:

```text
task_status, contract_revision, review_generation, receipts_current,
qualifying_passes, changes_requested_current, findings_open_high,
findings_open_medium, findings_open_low, handoffs_pending,
handoffs_delivered, handoffs_withdrawn, completion_cycles,
verification_attestation, verification_detail
```

Status uses the current Task enum. Revisions, generations, and counts are
nonnegative signed-64-bit integers. Each state is derived from exactly one
successful `task show --json` response. The mapping is exact:
`task_status=data.task.status`;
`contract_revision=data.contract.revision`;
`review_generation=data.task.review_target_generation`;
`receipts_current=data.review_evidence.counts.receipts_current_generation`;
`qualifying_passes=data.review_evidence.gate.qualifying_independent_passes`;
`changes_requested_current=data.review_evidence.counts.changes_requested_current_generation`;
the three finding counts map to the corresponding `open_high`, `open_medium`,
and `open_low`; the three Handoff counts map respectively to
`pending_handoff`, `handed_off`, and `handoff_withdrawn_by_user`; and
`completion_cycles=data.completion_history.total`.
`verification_attestation` is the newest completion cycle's public attestation
only when `task_status="done"` and a cycle exists; a reopened or otherwise
non-done Task uses null so historical evidence cannot satisfy current state.

`verification_detail` is `absent` when that valid public response contains no
structured current-verification object with command label, result, source
revision, duration, or scope coverage. The exact baseline output has no such
object, so `present` is not a valid M20 value. A failed, truncated, or
schema-invalid read yields `unknown`, a matching unknown entry, and cannot be
eligible. An eligible state object permits null attestation but requires
`verification_detail="absent"`; any other unavailable value is null and makes
the record partial with its dotted field path. No private database fallback is
allowed. No Task or Contract prose, event/checkpoint text, review body, path,
or Viewer material is retained.

`trial_measurement.payload` has exact keys `measurement_kind` and `data`.
For `measurement_kind="verification_proportionality"`, data has exactly:

```text
product_files, test_files, product_lines, test_lines, test_cases,
contract_owner_fanout, inventory_owner_fanout, maintenance_fanout,
duplicate_contract_locations, fixture_copy_groups,
verification_escalation, target_change_result, verification_steps
```

The first ten fields are counts derived once from the frozen scenario reducer
manifest and the isolated final materialization. A changed file differs from
the exact baseline Git tree by mode or bytes, including a new or deleted
regular file. Test files are changed paths matching `tests/test_*.py`; all
other managed changed files are product files. Lines are added plus deleted
text lines from the no-renames baseline comparison. Binary, symlink, oversize,
or unreadable input is never guessed: binary or symlink line/case fields use
`not_observable`, an oversize field uses `cap_exceeded`, and an unreadable
required source is excluded with `source_missing`. `test_cases` is the union
of baseline and final Python `test_*` function/method AST nodes whose source
span intersects an old- or new-side changed line. The occurrence key is the
canonical slash-separated repository-relative path, dot-qualified enclosing
class/function name, and zero-based ordinal among same-qualified-name nodes
ordered by `(lineno, col_offset)`. The baseline/final union deduplicates one
logical occurrence with the same key; a rename or move creates distinct old
and new keys and therefore counts two.

M20.2 freezes the reducer-manifest schema and its deterministic construction
and validation. Each ephemeral fresh-trial control bundle materializes safe
logical owner-slot codes, exact repository-relative selectors, LF-normalized
UTF-8 contract and inventory byte probes, fixture-probe AST fingerprints,
verification-label-to-kind mappings, and one reversible target-change
mutation. The raw selector material is trial control data and is not copied
into reduced evidence.
`contract_owner_fanout`, `inventory_owner_fanout`, and `maintenance_fanout`
are respectively the number of distinct manifest owner slots whose exact
contract-probe occurrence intersects a changed hunk, whose exact
inventory-probe occurrence intersects a changed hunk, or whose selector maps
any changed file. For each contract probe let `B` and `F` be the sets of
distinct managed UTF-8 text files containing it in the baseline and final
trees. `duplicate_contract_locations` is the largest `|F|` among probes for
which `|F|>=2` and `|F|>|B|`, or zero when no probe qualifies. Existing or
merely relocated baseline duplication with no net increase therefore
contributes zero.
`fixture_copy_groups` is the number of manifest fixture fingerprints that
have at least two distinct final test-case occurrences and a larger final than
baseline occurrence count. A fingerprint is SHA-256 of UTF-8
`ast.dump(node, annotate_fields=True, include_attributes=False)` for the
manifest-selected fixture node. No fuzzy matching, semantic inference, or
unlisted selector contributes to a count.

`verification_steps` contains the subject-selected commands in occurrence
order, excluding reducer, privacy-validation, and target-change sensitivity
commands. It has 0-16 items with exact keys `ordinal`, `kind`, `duration_ms`,
`duration_capped`, and `result`; ordinal is contiguous, kind comes only from
the frozen label mapping as `focused`, `lane`, `all`, or `other`, and result is
`success`, `failure`, or `timeout`. Exit zero is success, any completed
nonzero exit is failure, and reaching the cap is timeout. Success/failure uses
0-299,999 and false; timeout uses 300,000 and true.
From the ordered executed steps, `verification_escalation` is
`repeated_all` when at least two have kind `all`; otherwise `all_first` when
the first executed step is `all`; otherwise `proportional` when at least one
step exists. An empty sequence makes it `unknown` and the record partial with
`not_observable` for `data.verification_escalation`; the observed
`data.verification_steps=[]` itself has no unknown entry. Missing or invalid step
provenance also makes it `unknown`.

The frozen sensitivity command first passes on the accepted trial result and
then runs once against the manifest's reversible target mutation.
`target_change_result` is `detected` only when the first run passes and the
mutated run fails, `not_detected` only when both pass, `not_run` when the
frozen check was not attempted, and `unknown` for every other outcome. The
mutation is reverted before reduction. `not_run` makes the record partial with
`not_observable` for `data.target_change_result`; `unknown` has the applicable
machine reason. An eligible M20.3 record requires
target change to be `detected` or `not_detected` and escalation other than
`unknown`. In a partial/excluded record, any unavailable count or
`verification_steps` may be null, and an unavailable enum is `unknown`; every
such field has a matching `unknowns` entry.

For `measurement_kind="split_pressure"`, data has exactly `episodes`, with
1-8 items in occurrence order. Each has exact keys:

```text
episode_id, files_before, files_after, modules_before, modules_after,
lines_before, lines_after, contract_revision_before, contract_revision_after,
review_generation_before, review_generation_after, governance_cycles,
review_cycles
```

Episode ID is a 1-64-character lowercase ASCII code fixed by the scenario
manifest. A paired broad/bounded scenario uses identical IDs one-for-one in
the same order; an ID mismatch is `source_drift`. File/module/line and cycle
fields are nonnegative signed-32-bit integers; revisions and generations are
nonnegative signed-64-bit integers. Within every episode,
`contract_revision_after>=contract_revision_before` and
`review_generation_after>=review_generation_before`; violation is a conflict,
not a favorable delta. A partial record may use null only for an unavailable
numeric field and must name it in `unknowns`. An absent/invalid episode list
excludes the record with `parse_failed`.

The scenario manifest fixes each episode's arm-local logical `task_slot` and
half-open start/end boundary codes before either arm runs. Intervals are
non-overlapping and occur in the stored episode order. At a boundary, files
are the count of managed files whose mode or bytes differ from baseline;
modules are the count of distinct manifest owner slots selected by those
files; and lines are the total added-plus-deleted LF-normalized text lines
against baseline. Contract revision and review generation come from the same
boundary's valid public read for that `task_slot`.
`governance_cycles` is the number of successful taskgov write invocations
bound to that slot inside the interval. `review_cycles` is the cardinality of
distinct `(task_slot, positive_review_generation)` pairs first established in
the interval. Any overlap, unmapped Task, or cross-slot attribution makes the
affected field `source_drift`, preventing double counting when a bounded arm
uses multiple Tasks with the same generation number.

The machine-readable owner of those safe codes is the tracked
`fixtures/m20/m20.4-episode-plan-v1.json` supplement. It binds its exact
unit/scenario/arm/trial inventory, baseline, authority revision, and base
M20.2 protocol SHA-256 without changing the receipt-pinned collection
protocol. Both its raw and canonical SHA-256 digests are fixed by the root
harness. It contains only task-slot, boundary, and episode codes; it contains
no request, selector, prompt, path, or other shared raw control material.
Validation requires every slot and boundary to be referenced, each interval
to have `start < end`, stored intervals not to overlap, episode IDs to be
unique, paired arms to have identical ordered episode IDs, and the Handoff
control to have one episode. Reduced measurement and attestation ID sequences
must equal the selected plan. The control-bundle validator also receives the
expected unit/scenario/arm/trial and rejects any inventory mismatch or
cross-trial bundle substitution.

The M20.4 terminal receipt and M20.5 aggregate retain the exact canonical
episode-plan digest so later synthesis cannot silently select different
boundaries. The supplement remains temporary study scaffolding and follows
the TG-M20.5 retirement lifecycle; the retained receipt preserves the digest
after removal.

For exact paired comparison, an episode's machine delta vector is ordered as
`files_after-files_before`, `modules_after-modules_before`,
`lines_after-lines_before`,
`contract_revision_after-contract_revision_before`,
`review_generation_after-review_generation_before`, `governance_cycles`, and
`review_cycles`. The first three differences may be negative; revision and
generation differences cannot be. Every comparison uses the same vector
position for the same manifest-fixed episode ID.

M20.3 permits only `verification_proportionality` with record key
`verification_measurement`; M20.4 permits only `split_pressure` with record
key `split_measurement`. Their eligible measurement and attestation records
must share the same scenario/trial ID (which encodes the arm) and
authority/baseline identity.

### Historically Reconstructed Channel

One `task_git_reconstruction` record contains one metric:
`payload` has exactly `metric`, `value`, `coverage`, and `references`;
`record_key` equals metric. Metric is one of:

```text
completion_cycles, reopens, contract_revisions, review_receipts,
changes_requested_receipts, findings_open_high, findings_open_medium,
findings_open_low, handoffs_pending, handoffs_delivered, handoffs_withdrawn,
git_wall_clock_span_ms
```

M20.2 takes one coherent read-only repository-layer transaction over the
canonical schema-v16 Task database. Before aggregation, every fixed cohort
Task must exist in the owning project, be `done`, match the Task ID and exact
completion revision in the active roadmap index, and satisfy current
Contract, completion-cycle, event, review, finding, and Handoff relational
invariants. The transaction is the sampling point for every database metric;
later state never changes a reduced value.

Database metrics use all rows and all review generations visible at that
sampling point:

- `completion_cycles` is the sum of completion-cycle row counts for the cohort
  Tasks.
- `reopens` is the count of their `task_reopened` events, after validating the
  completion-cycle/event links.
- `contract_revisions` is the sum of each Task's
  `current_contract_revision`; for every positive revision, stored revisions
  must be contiguous from 1 through that value.
- `review_receipts` is the count of all review receipts for the cohort Tasks,
  and `changes_requested_receipts` is the subset whose verdict is
  `changes_requested`.
- Each `findings_open_<severity>` metric counts findings with
  `status="open"` and that exact severity, without restricting generation.
- Each `handoffs_<state>` metric counts Handoff rows whose source Task is in
  the cohort and whose state is respectively `pending_handoff`, `handed_off`,
  or `handoff_withdrawn_by_user`.

For `git_wall_clock_span_ms`, the reducer takes the unique exact completion
commits from the roadmap index, requires each to resolve to a commit ancestor
of the product baseline, reads the commit object's integer committer timestamp
in seconds, and computes
`1000 * (maximum_timestamp - minimum_timestamp)`. A one-commit cohort is zero.
Author timestamps, filesystem times, Task/event times, and commit traversal
order are not used.

References are exact and complete: database metrics use all cohort Task IDs in
ASCII-byte order; the Git metric uses the unique completion commits in
ASCII-byte order. Each fixed cohort has at most eight references. Eligible
requires `coverage="complete"`, a nonnegative signed-64-bit integer value, and
no unknown. A source boundary that is valid but cannot reconstruct the metric
uses `coverage="partial"`, `value=null`, and `not_reconstructable` for
`value`. Missing, inconsistent, unresolvable, out-of-range, or parse-invalid
required source data excludes the record with its exact source-level reason;
it never emits a guessed partial value. Historical timestamp difference is
wall-clock span, never active labor.

Historical evidence cannot reconstruct taskgov read counts, per-call latency,
verification commands/results, active labor, user wait, reviewer invocations,
clarification turns, or exact retries. M20.2 records those limits as partial
metrics only when an allowed metric represents them; otherwise the M20.5
limitations table names the unavailable category without inventing a metric.

### Observer-Attested Channel

`fresh_agent_trial.payload` has exact common keys `cohort`, `arm`,
`workload_digest`, `control_digest`, `outcome`, `reference_opens`,
`clarification_turns`, `manual_inputs`, `governance_invocations`,
`reviewer_invocations`, `assessment_kind`, and `assessment`. Cohort and arm
are fixed non-identifying scenario codes. Both digests are exactly 64
lowercase hexadecimal SHA-256 characters.

Immediately before a fresh trial, its ephemeral control bundle is canonical
compact sorted-key UTF-8 JSON with exact keys `workload`,
`delivered_request`, `neutral_clarification`, and `reducer_manifest`.
`workload_digest` hashes the exact UTF-8 workload string and `control_digest`
hashes the complete canonical bundle bytes. The bundle is independently
reviewed before delivery. Paired arms require the same `workload_digest`;
different arm authorization may produce different `control_digest` values.
Any digest/readback mismatch is `source_drift`.

Counts are capped respectively at 256, 16, 32, 64, and 8. Outcome is
`completed`, `blocked`, `paused`, `handed_off`, `failed`, or
`inconclusive`. When an otherwise successful observer source cannot provide a
common count, the field is null and the record is partial with
`not_observable`; a missing required source instead excludes the record with
`source_missing`. A count above its cap stores the cap and makes the record
partial with `cap_exceeded`.

For `assessment_kind="verification_proportionality"`, assessment has exactly:

```text
distinct_risks, new_cases, redundant_responsibilities,
verification_fact_codes, manual_reentry_fact_codes,
responsibility_pattern_codes, reuse, instruction_fit, minimal_receipt_fit
```

The first three are counts. Fact-code arrays are unique ASCII-byte-sorted
subsets of `command_label`, `result`, `source_revision`, `duration`,
and `scope_coverage`; manual-reentry codes must be a subset of verification
fact codes. Responsibility-pattern codes are a unique sorted subset of
`duplicate_contract_assertion`, `duplicate_inventory_owner`,
`fixture_copy`, `nondetecting_regression`, and
`unbounded_verification_escalation`. Reuse is `reused`, `mixed`,
`copied`, `new_justified`, or `unknown`; each fit field is
`yes`, `no`, or `unknown`. Eligible requires no unknown enum.
The three counts and three code arrays may be null only in a partial/excluded
record with matching `unknowns`.

For `assessment_kind="split_pressure"`, assessment has exactly `episodes`,
with 1-8 items in occurrence order. Each item has exact keys:

```text
episode_id, phase, cause, current_response, acceptance_independent,
verification_independent, commit_independent, completion_independent
```

Episode IDs must match the corresponding machine-observed episode set
one-for-one and in the same order, and paired arms must therefore share the
scenario manifest's identical ordered IDs. Phase is `intake`, `implementation`,
`verification`, or `review`. Cause is `multiple_outcomes`,
`in_scope_discovery`, `user_expansion`, `repeated_failure`,
`cross_module`, or `out_of_scope_control`. Current response is
`keep_current`, `block`, or `handoff`; independence fields are `yes`,
`no`, or `unknown`. Eligible requires no unknown independence. The Handoff
control requires cause `out_of_scope_control`, response `handoff`, and no
decomposition-success classification.

M20.3 permits only `assessment_kind="verification_proportionality"` and M20.4
only `assessment_kind="split_pressure"`; both use record key `attestation`.

### Privacy, Neutrality, Isolation, And Evidence Routing

No reduced or tracked artifact retains raw argv, filesystem paths,
environment, stdout/stderr content, prompt text, chat, private reasoning,
review body, provider payload, credential, secret, Task/Contract prose, raw
diff, stack trace, or per-event wall-clock timestamp. It stores only safe IDs,
exact authority/baseline commits, command leaves, codes, enums, counts,
durations, and bounded aggregates.

Fresh subjects receive no inherited conversation, M20 memo, hypothesis,
rubric, expected answer, suspected failure, or preferred solution. They
receive only a normal request, exact baseline, isolated fixture, and ordinary
baseline authority. Paired arms use the same model/tool/permission cohort.
Agents are not reused, receive no cross-trial artifact, feedback, or mid-trial
coaching, and do not score themselves. Clarification replies use a frozen
neutral script. The context-rich parent and prior user-reported conversation
are contaminated design context and never scored evidence.

The harness is root repository-only, offline, shell-free, and limited to the
fixed isolated scenarios and predefined safe verification labels. It does not
enter the installable package, instrument product state, invoke an arbitrary
command, or mutate a real consuming project or the canonical Task database.

There is no persisted shared control master. Exact control-bundle bytes exist
only in memory or the isolated trial-local temporary area from their immediate
pre-launch review through that arm's one reduction attempt. Paired bundles are
prepared together and their workload digests are compared before either
launch; after that comparison, each arm has an independent deletion lifecycle
and retains no bytes for the other arm. A launch failure destroys the affected
bytes and remains an excluded attempt.

Before an M20.4 attempt enters `started`, its no-rerun journal state binds the
reviewed launch to exact `workload_digest`, `control_digest`,
`observer_config_digest`, `trial_root_digest`, `trial_root_parent_digest`, and
`trial_root_identity_digest` values. The root digests contain no raw path.
All nine trial roots must be distinct immediate children of the same committed
parent, which prevents cross-attempt ancestor/descendant reuse without storing
that parent path.
The baseline HEAD preflight uses the same allowlisted process environment as
measurement Git calls, so ambient `GIT_*` routing cannot redirect it.
Generic uncommitted M20.4 start is invalid; boundary capture and the one
reduction compare the live isolated root, control, and observer config to this
commitment before using them. Finalization requires the exact nine committed
root digests as absent cleanup targets, so omitting an external trial root
cannot close the collection while its raw material might remain.

M20.4 start, boundary capture, reduction, and recovery share one bounded
per-attempt lock order. Recovery acquires all nine locks before inspecting the
journal, so it fails busy instead of terminalizing an attempt whose raw source
is still being captured or reduced.

Raw trial work and its control bundle are removed immediately after the single
reduction attempt regardless of whether the candidate record passes
schema/privacy validation. On failure, the candidate reduced bytes are also
discarded and only a newly constructed sanitized excluded/failure envelope
with stable codes and no copied raw value is retained; the raw source is never
reopened for a second reduction. Schema-valid reduced records pass the same
privacy check before the no-rerun journal can persist them; final-corpus
validation is a second check, not the first privacy boundary. For a fresh
trial, only the two control digests survive in the reduced attestation.
Reduced M20.2-M20.4 records remain under ignored `dist/`
only until TG-M20.5. The project does not claim deletion from
platform/provider service logs; it guarantees only that raw material is not
retained in the repository, taskgov state, Viewer, or M20 evidence artifact.
The root-only M20 collector and reconstruction tools, their dedicated tests,
and the M20 study protocol fixtures are temporary study scaffolding retained
through TG-M20.5 and removed by that unit after synthesis. Tracked terminal
collection receipts remain afterward as no-rerun tombstones and record the
corpus retirement state; they do not become study evidence or product state.

Each tracked terminal collection receipt uses
`schema="m20-collection-receipt-v1"` and the exact common keys
`schema`, `unit`, `authority_revision`, `baseline_revision`,
`protocol_sha256`, `status`, `artifact_status`, `retirement_revision`,
`record_count`, `corpus_bytes`, `corpus_sha256`, `eligible_records`,
`partial_records`, `excluded_records`, and `outcome`. M20.4 alone adds the
exact key `episode_plan_canonical_sha256`. A closed fresh collection uses
`outcome="collection_complete"`; it records no semantic feature verdict.
While its ignored corpus exists, `artifact_status` is `retained` and
`retirement_revision` is null. TG-M20.5 changes those values to `retired` and
the exact 40-hex retirement-anchor commit only after removing the bound
corpus; a retired receipt fails closed if that corpus is still present.

Existing Task DB rows own normal execution and completion evidence. TG-M20.5
routes its reviewed aggregate and limitations to one newly indexed
non-authoritative history document, promotes only durable decisions to the
active roadmap/plan, removes the temporary memo and reduced corpus, and retires
the temporary study scaffolding above. Study evidence never satisfies a
current or historical product gate.

The design deliberately does not add an `observe` command, configuration,
telemetry, SQLite table, Viewer field, Skill behavior, Verification Receipt,
test-strategy engine, Task-splitting operation, or parent/child model. A
positive TG-M20.5 decision authorizes only a separately approved design
proposal.

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

Deferred work includes profile authoring, verification-run recording,
external Issue delivery, dependency graphs, task import, pagination/search,
stale detection, parent/child/checklist execution units, manual backup/restore/
export, generic browser-state persistence, live server, browser launch,
network synchronization, and update checking.

Any extension must preserve local-first operation, current privacy and
target-project safety, explicit authority for mutation, narrow repository
boundaries, and concise Skill guidance. It requires synchronized
specification, design, roadmap, tests, and review rather than reuse of a
historical design capture.
