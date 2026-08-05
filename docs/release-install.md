# Release Candidate And Published Install Record

This note owns both the current local package candidate and the immutable
published artifact identity. Version 0.12.0 is an unpublished local candidate:
no candidate commit, push, tag, archive, checksum, GitHub Release, or
publication is fixed, claimed, or authorized. The published v0.10.0 artifact
and its exact commit, tag, Release, archive, checksum, and Release body remain
unchanged. This record also does not authorize installing or overwriting a
Skill in any project.

## Current Candidate Identity

| Item | Value |
|---|---|
| Package version | `0.12.0` |
| SQLite schema | v19 |
| Viewer snapshot | v4, accepting source schemas v5-v19 (v5 through v19) |
| Public command leaves | 21 |
| Supported runtime | Python 3.12 or newer on Windows |
| Verified platform | Windows |
| Candidate commit / `main` | not fixed; unpublished local candidate |
| Tag | none; unpublished local candidate |
| GitHub Release | none; unpublished local candidate |
| Remote/repository | `origin`, `VAiring/task-governance-tool` |
| Archive | not produced |
| Checksum | not produced |
| Archive root | `task-governance-tool/` |
| Release title | not assigned |
| Release notes | `docs/releases/v0.12.0.md` candidate note |
| Workflow/name/job | `.github/workflows/ci.yml`, `CI`, `test` |
| CI Python matrix | 3.12 and 3.14 |

Linux and macOS are not claimed as supported for this candidate.

## Immutable Published v0.10.0 Identity

Version 0.10.0 remains published from exact accepted commit
`a9b80ce177a6dead10d51a070b76ff01f7af0294`; remote `main` and the lightweight
tag `v0.10.0` resolve to that commit. GitHub Release `362617903` has prerelease
visibility. Its archive is `task-governance-tool-0.10.0.zip`, its checksum is
`task-governance-tool-0.10.0.zip.sha256`, its title is
`task-governance-tool v0.10.0`, and its exact canonical body remains
`docs/releases/v0.10.0.md`. The accepted archive root is
`task-governance-tool/`. No v0.12.0 candidate work changes or supersedes these
facts.

## License Boundary

Original copyrightable material owned by Omoronine in Git-tracked files is
licensed under the Apache License, Version 2.0 (`Apache-2.0`). Copyright
holder: Omoronine.
The repository root and installable package carry byte-identical official
unmodified `LICENSE` files, and the package copy is release-required and
manifest-covered.

Root `references/`, `research.md`, untracked or ignored files, generated state
and Viewer output, target configuration, caches, logs, secrets, scratch files,
and separately licensed or unowned third-party material are outside this
boundary. The current audit found no concrete attribution duty, so neither the
repository nor the package contains a `NOTICE`.

## Published Artifact

The reviewed source repository is published under lightweight tag `v0.10.0`
with a separate installable archive whose root is the repository's
`task-governance-tool/` directory.

The installable package contains its `LICENSE`, the co-located release
manifest, `SKILL.md`, display metadata, bundled Viewer template, CLI entry point
and runtime modules, and the three one-level Skill references. The manifest is
the exact inventory and digest authority for packaged core files.

The archive must exclude:

- generated `state/`, Viewer HTML, managed backups, SQLite databases, and
  SQLite sidecars;
- root `references/`, `research.md`, tests, fixtures, development-only
  documents, and local scratch output;
- caches, logs, temporary files, environment files, secrets, and editor files.

The canonical accepted archive recipe identifier is `git-archive-v1`:

```text
git archive --format=zip --output=<staging-file> <RC_SHA> -- task-governance-tool
```

The checksum bytes are the lowercase 64-hex archive SHA-256, two ASCII spaces,
the archive basename, and one LF. M19.6 executed this exact recipe twice with
one recorded Git executable and version and produced byte-identical output.
The accepted archive SHA-256 is
`99fc2345fd036091349c47f7379eee25b8b3b4c8873c0f74aaceac323bb82a03`;
the checksum-file SHA-256 is
`9cdc99bd26cc4887bd88ef2ec638659224f0a0d8f567edb12a3800d59a8b6764`.
No tracked archive builder or product command was added.

Creating an archive is side-effect free. It does not initialize, migrate, copy,
or inspect a target project's local state.

## Supported Installation

Install one physical package copy per governed project at exactly:

```text
<target-project>\.agents\skills\task-governance-tool
```

No other ordinary stateful layout is supported. Before installation or update,
show the complete destination and obtain explicit user approval. Do not
overwrite an existing package without a separate update decision, and do not
delete its generated project-local `state/` while replacing packaged core
files.

For a Git-managed target project, ensure the canonical Skill state directory
is effectively ignored. This narrow target-local rule is recommended:

```gitignore
/.agents/skills/task-governance-tool/state/
```

An enclosing worktree rule for the same directory is also accepted. Do not
recommend repository-wide extension globs. The one directory rule contains
this Skill's database, sidecars, backups, locks, generated Evidence JSON, and Viewer without
hiding unrelated project fixtures or assets. Non-Git governed directories are
valid and do not require an ignore file.

From the target-project root, preview setup before the explicit write:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --read-only --json
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

When running from inside the installed Skill directory, supply the target
project explicitly:

```powershell
python scripts/taskgov.py --repo <target-project> setup --json
```

Omitting `--repo` selects the current directory and never re-roots it to an
enclosing Git worktree. The target directory must exist, but it need not be a
Git repository.

## Setup And Upgrade Contract

`setup` is the only public initializer and migrator. It also performs the
one-way opt-in to bounded local maintenance and directly publishes or repairs
canonical Evidence JSON and Viewer. It is explicit, noninteractive, idempotent, and limited
to the supported physical project-scoped package.

Setup reports `schema_to=19` and `evidence_status` as `not_present`, `current`,
`published`, or `repair_required`. Its ordered write vocabulary includes
`evidence_projection_publish` after maintenance/binding and before
`viewer_publish`; read-only preview plans it without writing.

Version 0.12.0 retains the fixed package-local `state/current/` layout. Fresh
write-mode setup creates one UUIDv4-backed immutable project identity and
stores the mutable governed-directory binding separately. Explicit setup
publishes supported schema-v1-through-v13 legacy state into the fixed layout
without changing any existing project, Task, event, Contract, handoff, review,
completion, or maintenance identity. Same-binding migration is mechanical and
adds no user choice.

If the fixed canonical DB is missing, setup first validates fixed-layout
managed generations. If no fixed source exists, the shared resolver may select
exactly one eligible legacy-layout source. A same-binding legacy primary or
legacy backup-only source is staged and published into the fixed layout;
backup-only recovery performs `database_restore` inside that private stage
before `legacy_state_publish` and never recreates the old legacy primary.
Setup then continues normal
migration/configuration/Viewer repair without overwriting an existing
canonical DB. Invalid, foreign, linked, unrecognized, and ambiguous artifacts
are unchanged. If no valid matching managed candidate exists, setup fails
instead of creating empty task state. An orphan rollback journal for a missing
fixed primary also fails closed and remains untouched. A moved legacy
backup-only source is not a relocation candidate and fails no-write as
`project_state_unreadable`. There is no public recovery command or recovery
path option.

If the stored binding differs from the current governed directory, neither a
normal command nor `doctor` rebinding state is permitted. A write setup without
confirmation fails no-write with `project_relocation_required`. Run the
read-only preview:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --read-only --json
```

A successful `relocation_preview` reports the ordered future writes and a
bounded, expiring confirmation token without changing either source or
destination. Token issuance is not approval. Present that plan and wait for
explicit approval from the current user; only then submit the exact token:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --confirm-relocation <exact-token> --json
```

Never infer that a mismatch proves a move rather than a copy or fork, and
never auto-confirm. Invalid, expired, stale, or already-used tokens fail
closed; expired or stale context requires a fresh preview and fresh user
approval.
Successful relocation preserves the immutable project identity while updating
the mutable binding and its history, together with the applicable ordered
fixed-layout, migration, maintenance, and Viewer stages. A token-free replay
after completion is `already_setup`; replaying the consumed token is rejected.

Fresh setup and migration default to:

- backup interval: 30 minutes after the last successful managed backup;
- retained managed generations: 3.

The policy is stored with project state in SQLite; no JSON, TOML, or other
second configuration file is created. Explicit setup options may select
1-1,440 minutes and 1-20 generations:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --backup-interval-minutes 60 --backup-generations 5 --json
```

Omitted options preserve already configured values. A policy-only replay with
omitted or equal values performs no write. Once enabled, maintenance has no
public disable surface.

Before migration, setup creates one validated managed backup while holding the
shared fail-fast artifact lock. Migration from supported older schemas is
transactional, ordered, idempotent, and never a downgrade. It preserves task
and event identity, current completion/review records, completion-cycle
history, handoffs, Contracts, effort metadata, and checkpoints from every
supported source. An older runtime rejects a newer schema. Schema v14 adds
stable identity, binding, relocation history, and bounded legacy-cleanup
metadata. Schema v15 adds append-only completion-cycle storage and conservative
partial rows only for then-current done Tasks; schema v16 is the marker-only
activation boundary for atomic native capture. Schema v17 adds immutable
Verification Receipts and an explicit completion-cycle verification basis.
Schema v18 adds capture-versioned authority snapshots, whole-field criteria,
evidence references, Git artifact manifests, tool-owned verification subjects,
and versioned Review provenance. It raises durable/read verification capacity
to 1,000 characters; explicit public Task add/edit admission is also 1,000.
Schema v19 adds immutable completion Bundles, criterion links/Finding snapshots,
cycle evidence-basis linkage, and fixed Evidence JSON projection state.
Migration from v1-v18 and schema-v19 activation/reentry is ordered and
repeatable. A migrated capture-version-0 target remains read-only lineage and
must be replaced with a fresh capture-version-1 target before a new Receipt,
Finding, or completion source can be recorded.

Installation alone and ordinary task commands never migrate. After replacing
packaged core files while preserving local state, run `setup`; it performs any
required migration and Evidence/Viewer repair. A failed migration backup prevents the
migration.

## Release Upgrade And Paired Rollback

The immutable v0.10.0 release acceptance rehearsed the transition from the
exact legacy v0.1.0/schema-v2 baseline to v0.10.0/schema v16. The current
v0.12.0 candidate must separately rehearse that isolated baseline through
schema v19, including schema-v17 Receipt/completion preservation, subject and
provenance migration, capture-v0/fresh retargeting, 500/1,000 capacity,
Bundle/projection recovery, backup recovery, and no-partial-write behavior before it can become a release.
The v0.11.0 candidate note remains immutable lineage and its rehearsal cannot
satisfy this current gate. Neither rehearsal selects or mutates
user-wide, linked, junction, or custom-`--db` state.

Rollback restores one matched pre-migration package, database, and managed
artifact set as a single compatibility point, then proves that the legacy
package can read that restored state. Running old code against schema v19,
reverse-migrating in place, mixing generations, or treating a Git checkout
alone as rollback is unsupported. After cutover, a defect is handled by a
forward fix and new candidate/version, not a force update, history rewrite,
retag, or asset replacement.

## Doctor Contract

`doctor` is the sole diagnostic:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py doctor --json
```

It is inherently read-only and always reports
`suggested_action="continue"` for recognized advisory conditions. It observes
package integrity separately, then—when state is readable—uses one
lock-respecting SQLite read transaction for project, task, handoff, backup,
Evidence, and Viewer status. The fixed Evidence object reports only `code`,
`due`, source/published generations, last success, and last outcome.

Doctor never initializes, migrates, backs up, renders, repairs, acquires a
maintenance lock, runs project verification, or changes the target project.
For a Git-candidate target, only its single bounded effective-ignore preflight
may inspect Git. Doctor is optional and is not a setup or normal-loop
prerequisite.

## Runtime Maintenance

After setup opt-in, every eligible successful state mutation closes its SQLite
write before the fixed same-process maintenance coordinator runs:

1. refresh fixed Evidence JSON when its source generation is ahead;
2. refresh the canonical Viewer when its source generation is ahead; then
3. attempt at most one backup when the stored interval is due.

Evidence and Viewer publication each permit one initial capture/render and at
most one follow-up. Backup rotation keeps only the applied configured generation count. All use
zero-wait OS advisory locks and bounded sanitized outcomes. Contention or
failure preserves the primary command result and leaves the maintenance stage
due for the next eligible mutation.

Taskgov starts no daemon, thread, timer, detached process, scheduler, queue,
service, browser, custom-destination operation, public maintenance command, or
separate model decision. Every changed mutation may retry due Evidence work,
but only cycle insertion advances its source generation; Handoff-only writes
do not change either projection generation. Setup publishes Evidence then Viewer directly.

Evidence JSON v1 is generated only at fixed `state/current/evidence/index.json`
and `state/current/evidence/bundles/<completion-evidence-bundle-id>.json`, with the zero-wait
lock at `state/current/evidence/taskgov-evidence.lock`. Native entries bind one
immutable Bundle; pre-v19 cycles are index-only `legacy_unknown`. The index is
published last, SQLite remains canonical, and JSON is never imported. Failure
preserves the committed mutation and last-good index with one fixed warning.

The Viewer is a self-contained, read-only `file://` projection under the
ignored package state. Snapshot v4 accepts source schemas v5-v19 and includes
the same bounded newest-first completion history as `task show`; sources v5-v14
receive an empty, legacy-incomplete history. It omits internal event links,
storage paths, maintenance internals, checkpoint content, handoffs,
Verification Receipt data, Review provenance, raw evidence, environment data,
and secrets. For sources v18+ it validates subject/provenance/capture bindings;
v19 also validates and discards Bundle linkage. It performs no network request and
provides no database or task write control.

An optional browser-only refresh profile may exist at the physical installed
package's `config/viewer.json`. Taskgov never creates or edits it, and the file
must not be shipped in a release artifact or listed in the core manifest.
Absence is valid and leaves automatic refresh disabled
with no timer. A present file must be no larger than 16,384 bytes and contain
only strict UTF-8 JSON schema 1, profile `visibility-refresh-v1`, and an integer
`refresh_interval_seconds` from 5 through 3,600. Links, reparse points,
non-regular objects, replacement races, invalid encoding/JSON, and non-exact
fields fail closed without exposing path or OS details.

The resolved setting is embedded on the next Viewer-relevant publication or
explicit setup. A valid setting schedules at most one timeout only in an
already opened visible `file://` page and requests at most one same-document
reload per loaded page. It does not launch a browser, watch SQLite, use a
network, or use Web Storage. Only immediately before that automatic reload, a
fixed at-most-4,096-byte, five-minute envelope in the current History entry may
preserve non-search filters, selected Task, fixed-control focus, and document
scroll. The reloaded page clears owned state before validation, leaves
an unrelated `history.state` payload untouched, and changes neither URL nor
history length. Five minutes is the restore acceptance limit rather than a
physical-erasure guarantee; browser-managed state may survive session restore
but an owned envelope is consumed before validation. Invalid profile preview is
no-write `repair_required`; actual setup uses
`setup_incomplete`, while routine mutations preserve their primary success and
last-good Viewer and append `viewer_refresh_failed` for this Viewer/config
failure. An independently due backup attempt and its existing warning remain
unchanged.

## Public CLI Surface

The current 0.12.0 candidate exposes exactly these 21 command leaves:

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

The schema-v18+ Receipt writes remain those existing leaves. Verification has
no caller label or replacement subject input; Taskgov derives the subject from
the locked capture-version-1 target:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py verification receipt add <task-id> --result pass --duration-ms <milliseconds> --scope-coverage full --expected-target-generation <generation> --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add <task-id> --reviewer <reviewer-key> --kind independent --verdict pass --summary "No blocking findings" --reviewer-class llm --model-state declared --declared-model-id <model-id> --skill-state not_used --context-relation fresh_context --review-profile general --review-lens correctness --review-method review_packet_inspection --json
```

For independent/self-review Receipts, reviewer class, model state, Skill state,
and context relation are conditionally required; declared identifiers are
optional only when their states require them, while profile, lens, and method
flags are repeatable bounded sets. Native Receipts project v1 provenance,
migrated pre-v18 Receipts project v0 absence, and `not_required` projects null.
These attestations never authenticate reviewer identity or prove actual
model/Skill execution, competence, independence, diversity, quality, or truth.

Applicable leaves retain `--repo`, `--json`, and `--read-only`; the root retains
`--version`. Storage and generated-artifact paths are internal implementation
details, not public CLI choices. Unknown or removed commands/options fail at
the parser boundary before package, project, Git, or SQLite resolution.
There is no history command or option; the mandatory `task show` read and
automatically maintained Viewer supply the bounded audit projection.

The normal no-finding Tier 2 Task flow uses at most ten governance subprocess
calls with the default-off Effort Advisory, or eleven when an enabled valid
profile mechanically adds `task effort`. Doctor, checkpoint, and completion
check are optional and absent from the standard success path.

An enabled `task effort` result returns `suggested_action=continue` or the
single `suggested_action=reconcile_scope` route. The latter loads
`references/reconciliation.md` for one non-blocking episode covering the whole
result. Initial failure
handling remains in the Skill itself:
failed verification or blocking review prevents affected completion but not
safe authorized repair or unrelated ready work, and tests are never weakened
merely to obtain PASS. Without new evidence, two materially equivalent failed
repairs or review-remediation cycles prohibit a third equivalent cycle. The
comparison is session-local, creates no SQLite counter or new command, and
resets in a fresh session; durable Task, handoff, finding, and receipt state
remains available through normal rediscovery.

For review before the completion commit, stage exactly the intended files,
set the target with `review target set <task-id> --kind git_snapshot`, run the
project's exact verification outside Taskgov, record one bounded
`verification receipt add` attestation for that target generation, and use the
single packet from `review prepare`. Unstaged and untracked files remain
outside that target. Taskgov never executes or stores the verification command
or its arguments. The packet adds one deterministic target-kind instruction so
reviewers inspect the exact stored index, commit, fingerprint-bound material,
or external revision rather than ambient content. The independent reviewer
returns the result; the trusted parent/orchestrator records its sanitized
receipt/findings. Taskgov deterministically evaluates
qualifying PASS receipts and changes-requested receipts only for the current
review target and generation. Any unresolved high or medium finding from any
recorded generation of that Task continues to block the gate. Distinct
reviewer keys prove distinct stored strings only; they do not prove distinct
people, LLMs, machines, independent processes, independence, or authenticated
provenance.

## Safety And Privacy

`taskgov` may write only canonical generated Skill state after setup and the
explicit task-state operation requested by the caller. It does not edit target
project source, stage or write Git, open a browser, create an Issue or PR, or
use the network.

Default retention excludes raw stdout/stderr, stack traces, environment dumps,
full prompts or conversations, authorization material, raw provider bodies,
large diffs, raw review transcripts, and secrets. A Verification Receipt stores
only its result, duration, full/partial scope coverage, ownership, tool-owned
verification subject, current Contract/target basis, and recording time. A
migrated subject-v0 Receipt preserves its old caller label only as legacy
read-only lineage; a new subject-v1 Receipt accepts none. It
never stores the verification command body or arguments, exit code, output,
logs, exception, prompt, diff, credential, arbitrary coverage prose, or debug
variant. Review provenance retains only the closed scalar/code declarations,
bounded declared identifiers, binding metadata, assurance/producer values, and
digest. It does not upgrade the parent Receipt's assurance. The Viewer and
diagnostic envelopes expose bounded allow-listed
projections only. Completion cycles copy only already accepted bounded fields
and receipt IDs and never satisfy a current gate.

Normal and new input rejects both `dispatch_authorization=<value>` and the JSON
key `"dispatch_authorization":<value>`. The only compatibility is a read-only,
original-text projection for already-stored M19.7 Contract constraints and
checkpoint summaries. Omitted Contract constraints may retain those
already-validated bytes under the existing carry-forward contract; explicit
input cannot use the compatibility reader. Completion-history public text has
no exception and is strictly revalidated before `task show` or Viewer output.
None of these read boundaries changes SQLite schema or authorizes a write.

SQLite is helper state, not authority over project decisions. The target
project's governing documents and current user decisions remain authoritative.

## Published Acceptance And Candidate Checks

The v0.10.0 acceptance boundary is complete:

- M14 through M19.10 acceptance and required reviews passed on their recorded
  exact targets, including the M19.6A and M19.6B corrections.
- Candidate CI run `30561916953`, attempt 1, and remote-main CI run
  `30565181070`, attempt 1, succeeded for Python 3.12 and 3.14 at
  `a9b80ce177a6dead10d51a070b76ff01f7af0294`.
- Root/package `LICENSE` files match the official Apache-2.0 bytes; package
  `LICENSE` is manifest-covered and no unsupported `NOTICE` exists.
- The accepted archive is manifest-complete and excludes generated state,
  SQLite files and sidecars, Viewer output, backups, locks, root references,
  logs, caches, configuration, and scratch output.
- Default-branch, tag, and archive isolated-install smokes passed. The Release
  body is the exact bytes of `docs/releases/v0.10.0.md`, SHA-256
  `aaa118a3fbbb261ec6a24f7a80f50f161e606a86857f99e17f957f34ba044a03`.

For the current 0.12.0 candidate, first run the repository-only, offline,
read-only checker with `python tools/release_contract.py --repo .`. It derives
the parser leaves and package/schema/Viewer versions from owning Python code,
uses the release manifest as the exact packaged-core inventory, and checks
license, metadata, active command inventories, CI wiring, and tracked
generated-artifact exclusions. Validate the exact test partition with
`python tools/test_lanes.py --repo . --check`, then run
`python tools/test_lanes.py --repo . --lane all`, isolated physical install
and upgrade/rollback acceptance, and `git diff --check` against that
candidate's own exact identity.

The current CI policy parallelizes all three base lanes on pushes to `main`
for Python 3.12 and 3.14. Pull requests run the complete partition once on
3.12 plus `fast` on 3.14. A release candidate requires an exact-ref
manual `workflow_dispatch`: its matrix is monolithic `all` on both Python
versions and the explicit `Full release-candidate gate` must succeed. The
checker and lane runner are repository tooling, not installable `taskgov`
commands or target-project writes. A prior v0.10.0 result, Task receipt,
approval object, or historical gate never satisfies a future candidate.

Pushing, dispatching external CI, changing `main`, creating or changing a tag,
or publishing a Release always requires separate exact current authorization.
The completed v0.10.0 approvals authorize no later operation.
Future intent/evidence may use
`operation_sequence=<positive canonical integer>` as neutral correlation or
idempotency metadata, but the sequence is not an approval token and never
supplies that authorization.

## Current Candidate Summary

Version 0.12.0 is an unpublished local candidate. Schema v19 retains the
schema-v18 capture ledger, subjects/provenance, retargeting, and 1,000-character
capacity, then activates native Bundles and fixed Evidence JSON v1. Pre-v19
cycles remain index-only `legacy_unknown`; setup repairs, doctor observes, and
post-commit order is Evidence, Viewer, then backup. Viewer snapshot v4 accepts
source schemas v5-v19 while exposing no Evidence UI. No Runner, Analyzer,
network/model invocation, command leaf, or normal-loop call is activated. The
public inventory is exactly 21 leaves, and the normal no-finding
Tier 2 flow remains bounded to ten calls, or eleven with the enabled Effort
Advisory. Nothing in this candidate records a publishable commit, creates a tag
or archive, dispatches CI, pushes, or publishes a Release.

## Immutable Published v0.10.0 Summary

Version 0.10.0 is published as GitHub prerelease `362617903` at exact commit
and lightweight tag target
`a9b80ce177a6dead10d51a070b76ff01f7af0294`. It builds on the stable identity,
mutable binding, explicit relocation, and fixed `state/current/` layout from
0.9.0. Schema v16 preserves each accepted completion as append-only audit
history across reopen while requiring fresh current verification, target,
review, and completion evidence. Viewer snapshot v4 accepts source schemas
v5-v16 and displays the same bounded history already returned by `task show`.
The public inventory remains exactly 20 leaves with no history command or
option; the nine/ten-call normal flow, privacy boundaries, offline operation,
and target-project/Git no-mutation remain unchanged.
