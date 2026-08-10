# task-governance-tool

`task-governance-tool` is a local-first Codex Skill and stdlib Python CLI for
keeping long-running work resumable, reviewable, and bounded. It stores local
task state without replacing the target project's `AGENTS.md`, specifications,
design documents, tests, or current user decisions.

Release `0.12.0` uses SQLite schema v19 and Viewer snapshot v4 with source
schemas 5 through 19 as the current unpublished local candidate contract. It
has not been pushed, tagged, or published. The immutable published release
remains v0.10.0 at its recorded commit, tag, and GitHub prerelease.

## License

Original copyrightable material owned by Omoronine in Git-tracked files is
licensed under the Apache License, Version 2.0 (`Apache-2.0`). Copyright
holder: Omoronine.
The root [LICENSE](LICENSE) and installable
[`task-governance-tool/LICENSE`](task-governance-tool/LICENSE) contain the
same official unmodified license text.

This boundary excludes root `references/`, `research.md`, untracked or ignored
files, generated local state and Viewer output, target configuration, caches,
logs, secrets, scratch files, and any separately licensed or unowned
third-party material. The current tracked and shipped material has no
identified attribution duty requiring a `NOTICE`, so no `NOTICE` is included.

## Install

Python 3.12 or newer on Windows is the supported runtime. Windows is the
CI-verified platform; Linux and macOS are not claimed as supported.

Install one physical copy of the installable `task-governance-tool/` folder for
each governed project at exactly:

```text
<target-project>\.agents\skills\task-governance-tool
```

Only this project-scoped physical layout is supported for ordinary use. Show
the exact destination and obtain approval before installing or replacing it.
The release artifact and package creation process never initialize project
state.

For a Git-managed target, ensure the canonical Skill state directory is
effectively ignored before setup. This narrow target-local rule is recommended:

```gitignore
/.agents/skills/task-governance-tool/state/
```

The rule covers only generated state owned by this Skill. An enclosing
worktree rule for the same directory is also accepted. A non-Git directory is
also a valid governed project and needs no Git ignore check.

From the target-project root, preview and then perform setup:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --read-only --json
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

`setup` is the only initializer and migrator. It also performs the one-way
opt-in to project-local maintenance and publishes or repairs the canonical
Evidence JSON and offline Viewer. It is noninteractive and idempotent. If the canonical DB is
missing while a valid fixed-layout managed generation remains, setup recovers
the newest valid same-project generation before normal migration and Viewer
repair. It also supports one unambiguous same-binding legacy backup-only
source: recovery occurs in the private fixed-layout stage and never recreates
the old legacy primary. A moved legacy backup-only source is not a relocation
candidate and fails no-write as `project_state_unreadable`. It does not add a
recovery command or accept a recovery path.

The 0.12.0 candidate retains one immutable project identity in the fixed
package-local `state/current/` layout and keeps the governed-directory binding
separately.
Fresh setup creates a UUID-backed identity. Explicit setup mechanically moves
a supported schema-v1-through-v13 legacy database to the fixed layout when its
stored binding still matches the current project.

Existing fixed state migrates transactionally through append-only completion
cycle storage in schema v15 and marker-only native-capture activation in schema
v16. Schema v17 adds immutable Verification Receipts and an explicit
completion-cycle verification basis. Schema v18 adds capture-versioned
authority/criterion ledger bindings, tool-owned verification subjects,
versioned Review provenance, and 1,000-character durable/read verification
capacity. Explicit public Task add/edit admission is also 1,000 characters.
Migrated capture-version-0 targets remain read-only lineage and require a fresh
target before new evidence-source writes. Legacy gaps remain marked incomplete
rather than being inferred.

Schema v19 adds immutable criterion links and Finding snapshots, seals one
version-1 Bundle with each native completion, and maintains deterministic
Evidence JSON at fixed `state/current/evidence/index.json` and
`state/current/evidence/bundles/<completion-evidence-bundle-id>.json` paths,
with its lock at `state/current/evidence/taskgov-evidence.lock`. Pre-v19 cycles remain
index-only `legacy_unknown`. SQLite stays canonical and JSON is never imported.
This activates no Runner, Analyzer, network/model invocation, Viewer Evidence
surface, new public leaf, or new normal-loop call.

A binding mismatch is an exceptional relocation flow, not a normal Task-loop
step. Normal commands and `doctor` never rebind state. Run
`setup --read-only --json` to obtain a bounded `relocation_preview`, present
its planned writes to the user, and wait for explicit current approval. Only
then pass the exact returned token:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --confirm-relocation <exact-token> --json
```

Never infer that a mismatch means a move rather than a copy or fork, and never
auto-confirm a preview. An expired or stale token requires a fresh preview and
fresh user approval.

If invoking the CLI from inside the installed Skill directory, pass the target
directory explicitly:

```powershell
python scripts/taskgov.py --repo <target-project> setup --json
```

Omitting `--repo` means the current directory; the CLI never re-roots it to an
enclosing Git worktree. An explicit repository argument is therefore required
from the Skill directory. The governed directory itself does not need to be a
Git repository.

`doctor` is the sole diagnostic and is always read-only:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py doctor --json
```

It reports package integrity, setup readiness, compact task and handoff counts,
and bounded maintenance status. It never initializes, migrates, repairs,
renders, backs up, or runs project tests. For a Git-candidate target, only its
single bounded effective-ignore preflight may inspect Git. Doctor is optional
and is not a prerequisite for setup or normal task work.

Candidate validation rehearses the isolated transition from the exact legacy
v0.1.0/schema-v2 baseline to v0.12.0/schema v19. Paired rollback restores the
matched pre-migration package, database, and managed artifacts together; it
never runs legacy code against schema v19 or treats a Git checkout alone as
state rollback. The published v0.10.0/schema-v16 and unpublished
v0.11.0/schema-v17 rehearsal records remain immutable lineage and do not
satisfy the current candidate gate. See
[Release And Install Decision](docs/release-install.md) for the complete
boundary.

## Minimal Task Workflow

Register only work already approved by the user or an approved execution plan
or execution-unit set:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task add --title "Example task" --json
```

At a later session boundary, first rediscover current work:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task current --compact --json
```

If `task current` returns an `in_progress` or `review_pending` row, resume the
first such row in returned order. Otherwise select ready work; returned
`paused` and `blocked` rows remain visible but do not suppress unrelated ready
selection:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task next --compact --json
```

Always inspect the resumed or selected task:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task show <task-id> --json
```

Only when a ready task was selected, start it:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status in_progress --json
```

`task show` is the mandatory detailed read before work. The same JSON call
supplies bounded completion-cycle audit history, current Verification Receipt
readiness, and whether the optional Effort Advisory is enabled. Historical
cycles never satisfy a current gate. The default-off flow needs no extra LLM
choice or command. The normal no-finding Tier 2 graph remains bounded to ten
governance subprocess calls, or eleven when the existing boolean enables
`task effort`. `task current` rediscovers paused, blocked, review-pending, and
in-progress work.

When that deterministic flag is enabled, run `task effort` once at the existing
verification/review boundary. `suggested_action=continue` proceeds normally;
`suggested_action=reconcile_scope` loads
`references/reconciliation.md` for one non-blocking episode covering the whole
result, not one episode per exceeded metric. The signal itself never changes
Task state, acceptance, handoffs, or review evidence.

A failed verification or blocking review prevents only the affected Task's
completion while safe authorized repair and unrelated ready work continue.
Never weaken a test merely to obtain PASS. Without new evidence, two materially
equivalent failed repairs prohibit a third equivalent repair; wrapper, command,
or working-directory changes alone are not new evidence. Attempt comparison is
session-local and resets in a fresh session. Hand off out-of-scope discoveries,
block only after safe authorized work is exhausted, and batch any remaining
user decisions after unrelated safe work.

At a genuine continuation boundary, an optional typed checkpoint records only
the bounded summary, next action, and unresolved risks:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task checkpoint <task-id> --summary "Verified current slice" --next-action "Continue with the next acceptance item" --json
```

Record an out-of-scope discovery immediately without changing the current
task's acceptance criteria:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff record <task-id> --summary "Concise sanitized discovery" --json
```

The record remains a local `pending_handoff`. This release does not triage,
prioritize, synchronize, or create external Issues.

## Review And Completion

For a review-before-commit Git workflow, stage exactly the intended project
changes through the project's own Git process, capture the staged target, run
the exact project verification outside Taskgov, record its bounded attestation,
and prepare one bounded review packet:

```powershell
git add <intended-project-paths>
python .agents/skills/task-governance-tool/scripts/taskgov.py review target set <task-id> --kind git_snapshot --json
# Run the exact approved project verification here; taskgov never executes it.
python .agents/skills/task-governance-tool/scripts/taskgov.py verification receipt add <task-id> --result pass --duration-ms <milliseconds> --scope-coverage full --expected-target-generation <generation-from-target-set> --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review prepare <task-id> --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add <task-id> --reviewer <reviewer-a> --kind independent --verdict pass --summary "No blocking findings" --reviewer-class llm --model-state declared --declared-model-id <model-id> --skill-state not_used --context-relation fresh_context --review-profile general --review-lens correctness --review-method review_packet_inspection --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add <task-id> --reviewer <reviewer-b> --kind independent --verdict pass --summary "No blocking findings" --reviewer-class human --model-state not_applicable --skill-state not_applicable --context-relation external_context --review-profile general --review-lens correctness --review-method review_packet_inspection --json
git commit -m "<project-approved message>"
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete <task-id> --completion-evidence-kind git_commit --completion-revision <hash> --verification-complete --review-complete --json
```

The staged snapshot excludes unstaged and untracked content. Taskgov records
only the caller's bounded verification facts; it never executes the command or
stores its body, arguments, exit code, output, logs, or environment. The
verification subject is derived from the locked capture-version-1 target; no
caller label or replacement subject input exists. Native Review Receipts carry
the declared v1 provenance fields shown above, migrated receipts project v0
absence, and `not_required` projects null. Provenance never upgrades the
Receipt assurance or proves reviewer identity, model/Skill execution,
competence, independence, diversity, quality, or truth. The completion commit
must have exactly one parent equal to the captured base and the same tree.
Meaningful target changes require a new target and fresh verification and
review receipts. The Skill validates Git evidence read-only; it never stages,
commits, branches, pushes, opens a PR, or creates an Issue.

The packet tells each reviewer how to inspect the exact target rather than
ambient `HEAD` or worktree content. The independent reviewer returns the
verdict and findings; the trusted parent/orchestrator records their sanitized
result with the shown receipt command and existing finding commands. Taskgov
deterministically evaluates qualifying PASS receipts and changes-requested
receipts only for the current review target and generation. Any unresolved high
or medium finding from any recorded generation of that Task continues to block
the gate. Distinct reviewer keys prove distinct stored strings only; they do
not prove distinct people, LLMs, machines, independent processes,
independence, or authenticated provenance.

Tier 1 normally requires one independent PASS. Tier 2 normally requires two
distinct independent PASS receipts for the same target generation and blocks
completion while a high or medium finding remains unresolved. Use
`task complete --check` only when an explicit read-only completion check is
useful; it is not part of the normal success path.

A completed task is locked. Reopen requires an explicit reason and preserves
historical events and saved completion cycles. Those records remain audit-only;
fresh verification, target, receipts, and completion evidence are required:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status in_progress --reopen-reason "<sanitized reason>" --json
```

## Local Maintenance

Successful `setup` stores the backup policy in the project-local SQLite state.
Defaults are 30 minutes since the last successful managed backup and 3 retained
generations. Only an explicit setup call may change them:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --backup-interval-minutes 60 --backup-generations 5 --json
```

Omitted options preserve an existing policy. Supported ranges are 1-1,440
minutes and 1-20 generations. Backup policy stays in SQLite and creates no
configuration file.

After opt-in, each eligible successful state mutation closes its SQLite write
before running bounded same-process maintenance. Due Evidence projection runs
first, the canonical Viewer second when relevant, followed by at most one due backup attempt. Viewer maintenance
renders at most twice to absorb one concurrent change. Backup and Viewer
failures preserve the primary command result, keep maintenance due, and are
reported only as bounded sanitized warnings.

Taskgov starts no daemon, timer, background process, queue, service, browser,
or maintenance command. Generated Evidence JSON, Viewer, and managed backups
remain runtime artifacts under the ignored Skill `state/` directory. Evidence
projection failure keeps the mutation successful and the last-good index,
leaves work due, and emits only its fixed warning. Viewer snapshot v4 reads source schemas 5 through 19 and includes the same
bounded newest-first completion history as `task show`. Sources 5-14 are shown
honestly as empty legacy-incomplete history. The Viewer contains only sanitized
task/review/audit projections and has no write controls or network dependency.
It validates schema-v18+ subject/provenance/capture bindings and the schema-v19
Bundle discriminator but adds no provenance/Bundle field, panel, filter, or
other snapshot-v4 UI surface.

Viewer auto-refresh is a separate opt-in browser presentation policy. Taskgov
does not create or edit it. With no file at
`.agents/skills/task-governance-tool/config/viewer.json`, the generated page
creates no refresh timer and requires a normal browser reload. To opt in,
create that exact regular project-local file with strict UTF-8 JSON:

```json
{
  "schema_version": 1,
  "profile": "visibility-refresh-v1",
  "refresh_interval_seconds": 30
}
```

The interval must be an integer from 5 through 3,600 seconds. The file is
limited to 16,384 bytes; links, reparse points, extra or duplicate fields, and
invalid JSON are rejected. Run explicit `setup` to apply a changed profile
immediately, or let the next Viewer-relevant task mutation publish it. A valid
profile reloads only an already opened visible `file://` page, using at most
one browser timeout. It does not launch a browser, watch the database, contact
a network service, or use Web Storage. Immediately before that automatic
reload only, the page may place one at-most-4,096-byte, five-minute envelope in
the current History entry. It preserves status/kind/lane/priority/tag/terminal
filters, selected Task, fixed filter-control focus, and document scroll; search
text and task/snapshot content are never included. The page clears its owned
envelope before restoration and never overwrites an unrelated `history.state`
payload, changes the URL, or adds a history entry. Five minutes is the restore
acceptance limit, not a promise that browser-managed state has been physically
erased; a session-restored owned envelope is still consumed before validation.
If the profile is invalid,
`setup --read-only` reports planned Viewer repair without writing, actual setup
fails with `setup_incomplete`, and routine task mutations retain their success
plus the existing sanitized Viewer warning.

## Public Commands

The 0.12.0 local candidate exposes exactly these 21 command leaves:

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

Applicable commands accept `--repo`, `--json`, and `--read-only`; the root also
accepts `--version`. Storage paths and maintenance internals are not public CLI
choices. There is no history command or option; the existing `task show` and
automatically maintained Viewer supply the bounded projection.

## Privacy And Scope

The Skill stores sanitized task metadata, compact events, completion evidence,
bounded completion-cycle audit rows, review evidence, handoffs, optional
Contracts, checkpoints, bounded maintenance facts, and exact allow-listed
Verification Receipt facts. A native Receipt contains only its closed result,
duration, full/partial coverage, ownership, tool-owned
verification subject, current Contract and target basis, and recording time.
Native schema-v18+ Receipts accept no caller label. Taskgov stores no verification
command body or arguments, exit code, stdout/stderr, stack trace, environment,
logs, exceptions, prompts, diffs, credentials, arbitrary coverage prose, or
debug variant. Native Bundles and Evidence JSON contain only the same bounded
allow-listed ledger facts and safe relative artifact identities.

New task input strictly rejects both `dispatch_authorization=<value>` and the
JSON key `"dispatch_authorization":<value>`. For future external-operation
records, use `operation_sequence=<positive canonical integer>` only as
correlation or idempotency evidence; it never grants permission to dispatch,
push, publish, or perform another external action.

One read-only compatibility seam preserves the original bytes of already
stored M19.7 Contract constraints and checkpoint summaries. It creates no
write or authority. Omitted Contract constraints retain already-validated
prior bytes under the existing carry-forward rule, while explicit new
constraints use the strict input guard. Completion-history public text has no
legacy exception and is strictly revalidated before `task show` or Viewer
output.

It is local-first and uses no network service. It does not mutate target-project
files or Git state, run project-specific verification automatically, or create
external Issues/PRs. SQLite remains helper state; governing project documents
and current user decisions remain authoritative.

## Immutable Published v0.10.0 Artifact

Version 0.10.0 is published from exact commit
`a9b80ce177a6dead10d51a070b76ff01f7af0294`; remote `main` and lightweight
tag `v0.10.0` resolve to that commit. GitHub Release `362617903` has prerelease
visibility. Its title is `task-governance-tool v0.10.0`, archive
`task-governance-tool-0.10.0.zip`, and checksum
`task-governance-tool-0.10.0.zip.sha256`. The canonical Release body is
[docs/releases/v0.10.0.md](docs/releases/v0.10.0.md). The exact
`git-archive-v1` recipe, checksum format, workflow identity, and runtime matrix
are fixed in [docs/release-install.md](docs/release-install.md).

The current v0.12.0 package is an unpublished local candidate described by
[docs/releases/v0.12.0.md](docs/releases/v0.12.0.md). The immutable v0.11.0
candidate-lineage note remains at
[docs/releases/v0.11.0.md](docs/releases/v0.11.0.md). No commit, tag, archive,
checksum, GitHub Release, push, or publication is claimed or authorized for it.

## Development Checks

For current development and any later release candidate, run at least:

```powershell
python tools\document_contract.py --repo .
python tools\release_contract.py --repo .
python tools\test_lanes.py --repo . --check
python tools\test_lanes.py --repo . --lane all
python task-governance-tool\scripts\taskgov.py --help
python task-governance-tool\scripts\taskgov.py --version
git diff --check
```

The document checker is offline and read-only. It validates this repository's
closed semantic authority registry, route-scoped syntax, owner/anchor/link
reachability, execution-unit identity/order/dependency, history provenance and
non-authority, search exclusion, and explicit bounded-read controls. It does
not freeze ordinary prose, impose a repository-wide Markdown dialect, or use
hidden reduction and checker-size thresholds. Ambiguous syntax in an authority
route still fails closed instead of being guessed.

For shorter local feedback, replace `all` with `fast`, `integration`, or
`release`. The three base lanes own every standard-discovery test exactly once;
an unassigned, duplicate, or stale test module fails before execution. The
`all` lane preserves the ordered test IDs and suite from
`python -m unittest discover -s tests`.

The current closed mandatory-native set contains exactly one stable ID,
`test_m241a_lpac_portability.RunnerLpacPortabilityNativeTests.test_real_lpac_portability_matrix_and_cleanup`.
It belongs to `integration`
and therefore `all`; if selected and reported as skipped, that lane is
non-PASS. A lane that does not select it and unrelated optional SKIPs retain
their existing behavior. The portability test is a direct private-seam proof.
The distinct full process/registry matrix is a current TG-M24.2 completion
gate, but is not yet part of the repository mandatory-native set. TG-M24.2
must add its stable ID with non-SKIP enforcement before acceptance; until
then, the accepted standalone portability ID remains the complete current set.

CI consumes the same repository-only policy:

| Event | Python 3.12 | Python 3.14 |
|---|---|---|
| pull request | `fast`, `integration`, `release` | `fast` |
| push to `main` | `fast`, `integration`, `release` | `fast`, `integration`, `release` |
| manual `workflow_dispatch` | `all` | `all` |

The manual matrix is the complete release-candidate gate; its aggregate
job fails unless policy validation and both full-version jobs succeed.
An `operation_sequence` value may correlate separately authorized candidate
work, but neither that value nor a successful gate authorizes workflow
dispatch, push, tag, or publication.

The repository-only release checker is offline and read-only. It derives the
CLI leaves and runtime release versions from their owning Python modules, uses
the release manifest as the package inventory, and checks metadata,
documentation, license, CI, and tracked generated-artifact consistency. It is
not an installable `taskgov` command. The suite separately retains isolated
physical-install, no-write, migration, upgrade, and paired-rollback behavior
coverage.

## Project Docs

- `AGENTS.md`: durable agent behavior, safety, and workflow gates.
- `docs/authority.md`: concise authority index and selective-read routing.
- `docs/specification.md`: product contract.
- `docs/design.md`: implementation design and boundaries.
- `plan.md`: current decisions, open issues, cross-sequence gateways, and
  non-delegated static contracts. Its
  `plan.md#tg-doc-sequence` route owns accepted post-M23 TG-DOC.2 and inactive
  post-M24 TG-DOC.3; accepted TG-DOC.2 is the predecessor of M24.1.
- `docs/execution-contracts/`: indexed mixed current/conditional authority. M22
  routes through
  `docs/execution-contracts/tg-m22-evidence-ledger.md#tg-m22-sequence`;
  TG-M22.1A, TG-M22.2, TG-M21.5, TG-M22.3, TG-M22.4, TG-M23.1, bounded
  offline/mock TG-M23.2, and offline/mock TG-M23.3 integrated Analyzer
  acceptance, TG-DOC.2, documentation-only TG-M24.1, and the bounded
  TG-M24.1A LPAC-proof correction are accepted predecessors; current
  shadow-Runner implementation authority belongs to TG-M24.2, while TG-M24.3
  and TG-M24.4 remain inactive. Until TG-M24.2 completes, the candidate stays
  v0.12.0/schema v19 and no Runner, schema, CLI, Skill, or gate is active.
  Network/live
  Analyzer acceptance still requires separate authority. SQLite, `storage.py`,
  public CLI or Skill calls, network/live-model actions, gate mutation, and
  Task mutation remain outside the accepted TG-M23 scope.
  Its sole unit owner/router delegates only the Windows process/private-temp/
  publication seam to
  `docs/execution-contracts/tg-m23-process-safety.md#tg-m23-process-safety`.
- `docs/release-install.md`: current candidate, immutable published artifact,
  and installation identity.
- `docs/history/README.md`: non-authoritative lineage index.

Inspect live Task state and evidence through the public CLI; project documents
do not mirror that volatile state. Normal repository searches exclude history;
load it only for an explicitly named lineage, migration, rationale-recovery, or
superseded-evidence need.
