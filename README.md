# task-governance-tool

`task-governance-tool` is a local-first Codex Skill and stdlib Python CLI for
keeping long-running work resumable, reviewable, and bounded. It stores local
task state without replacing the target project's `AGENTS.md`, specifications,
design documents, tests, or current user decisions.

Release `0.13.0` uses SQLite schema v33 and Viewer snapshot v4 with
source schemas 5 through 33 as the current unpublished local candidate contract. No
exact final candidate commit, tag, archive, checksum, or GitHub Release identity
is fixed. An ordinary push to `main` is CI input, not release publication. The
immutable published release remains v0.10.0 at its recorded commit, tag, and
GitHub prerelease.

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

Python 3.12 or newer supports ordinary Task use on Windows, Linux, and macOS.
The representative platforms are Windows x86-64, Ubuntu 24.04 x86-64, and
macOS 15 Apple Silicon. Windows CI retains Python 3.12/3.14; the additional
platform checks use 3.12. Public Runner execution supports Windows, Linux, and
macOS under explicit opt-in. See [the support boundary](docs/release-install.md#current-candidate-identity).

Install one physical copy of the installable `task-governance-tool/` folder for
each governed project at exactly:

```text
<target-project>/.agents/skills/task-governance-tool
```

Only this project-scoped physical layout is supported for ordinary use. Show
the exact destination and obtain approval before installing or replacing it.
An approved package update preserves `config/verification-runner.json`,
`config/viewer.json`, `config/effort-advisory.json`, and
`config/setup-features.json` byte-for-byte. Release
artifacts exclude all `config/` content, and package creation never initializes
project state.

For a Git-managed target, ensure the canonical project state directory is
effectively ignored before setup. This narrow target-local rule is recommended:

```gitignore
/.taskgov/
/.agents/skills/task-governance-tool/state/
/.agents/skills/task-governance-tool/config/verification-runner.json
```

The rules cover only generated state and the explicitly authored local Runner
Plan. An enclosing worktree rule for the same paths is also accepted. A non-Git
directory is also a valid governed project and needs no Git ignore check.

From the target-project root, preview and then perform setup:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --read-only --json
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

On Linux/macOS, use the same physical package and arguments with `python3`:

```sh
python3 .agents/skills/task-governance-tool/scripts/taskgov.py setup --read-only --json
python3 .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

`setup` is the only initializer and migrator. It also performs the one-way
opt-in to project-local maintenance and publishes or repairs the canonical
Evidence JSON and offline Viewer. It is noninteractive and idempotent. If the canonical DB is
missing while a valid fixed-layout managed generation remains, setup recovers
the newest valid same-project generation before normal migration and Viewer
repair. Before layout activation, source selection also supports one
unambiguous same-binding legacy backup-only
source: recovery occurs in the private fixed-layout stage and never recreates
the old legacy primary. A moved legacy backup-only source is not a relocation
candidate and fails no-write as `project_state_unreadable`. It does not add a
recovery command or accept a recovery path. After core setup, it offers
[optional feature choices](docs/setup-state-specification.md#optional-feature-selection).
For example, after the user selects these values:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --usage-collection on --verification-runner on --effort-advisory off --viewer-reload on --json
```

Omitted choices remain unchanged/undecided; explicit OFF is remembered. New
Viewer reload uses 30 seconds; existing intervals/thresholds/Runner entries
are retained. Runner ON with no Task Plan still uses manual verification.
Check each `optional_features` outcome, not just core `ok`. Host hook trust
remains a separate user operation, and no normal Task-loop step is added.

After activation, recovery uses only new-root managed backups, never the old
package-local source or retained copy.

The candidate uses fixed `<governed-project>/.taskgov/current/` state and keeps
one immutable project identity separate from the governed-directory binding.
Upgrading from package-local state requires an explicit offline setup: stop
Taskgov writers and enabled Runner processes, update the package, preview,
then run setup. It retains a recoverable source, fences the old location and
activates the new state without changing identity merely for the path move.
Preserve old retirement material and its ignore rule during package updates.
The exact [migration/retry boundary](docs/setup-state-specification.md#project-state-separation)
does not add a normal-loop command or an arbitrary state-path option.
Fresh setup creates a UUID-backed identity. Explicit setup mechanically moves
a supported schema-v1-through-v13 legacy database to the fixed layout when its
stored binding still matches the current project.

Setup preserves stored history; it does not turn legacy gaps into current
evidence. Migrated capture-version-0 targets need a fresh target before new
evidence writes. Migrated active work with an unknown owner requires the
[pause and resume recovery procedure](task-governance-tool/references/task_workflow.md#pause-resume-and-block),
not a guessed assignment. Every new completion needs an explicit verification
declaration and current evidence, including after reopening an old Task.

For a migration investigation, use the current
[supported-schema contract](docs/database-specification.md#initialization-and-supported-schemas)
and [persistence contract](docs/database-specification.md#current-schema-v28-persistence-contract).
The [upgrade and paired rollback boundary](docs/release-install.md#release-upgrade-and-paired-rollback)
owns preservation of the matching package, state and managed artifacts.
Package updates also preserve inert old artifacts and the supported local
configuration; generated Evidence JSON is a projection, never imported state.

Optional [project-local usage hooks](task-governance-tool/references/usage_hooks.md#installation-and-trust)
collect registered participants after user review/trust in Codex. Setup never
performs host trust or claims collection was observed. Collection OFF retains
recorded history, and unavailable usage never blocks Task completion.

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
v0.1.0/schema-v2 baseline to v0.13.0/schema v31. Paired rollback restores the
matched pre-migration package, database, and managed artifacts together; it
never runs legacy code against schema v31 or treats a Git checkout alone as
state rollback. The published v0.10.0/schema-v16 and unpublished
v0.11.0/schema-v17 and v0.12.0/schema-v21 rehearsal records remain immutable
lineage and do not satisfy the current candidate gate. See
[Release And Install Decision](docs/release-install.md) for the complete
boundary.

## Minimal Task Workflow

Register only work already approved by the user or an approved execution plan
or execution-unit set:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task add --title "Example task" --json
```

At a start or resume boundary, read the selected Task and its context once:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task context --json
```

The tool resumes the caller's first `in_progress` or `review_pending` Task, otherwise
selects the first ready candidate under the existing ordering. `data.selected`
contains complete Task detail, Contract, latest checkpoint, and current gates;
use it directly without a follow-up read. Other owners and released held work
remain available through explicit current/list/show inspection, not automatic
resumption. The CLI obtains the caller ID automatically. If it is unavailable,
reads remain possible but acquisition and owner-only writes are refused.
`selection=none` means no actionable Task; `ok=false` is a read failure, not
permission to skip to another candidate. Individual current/next/show commands
remain available for explicit inspection.

Only when a ready task was selected and implementation is authorized, start it:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status in_progress --json
```

For new work already authorized for immediate implementation, use the existing
initial `--status in_progress` under the [registration and ordering rules](docs/task-operation-specification.md#explicit-registration-and-contract-population).
Use the add response's ready `data.context_preparation.context` instead of the
following context read; an already active selection needs no start edit. If
preparation failed, registration still committed: retry context, not add.
Registration, initial status, and context selection do not grant permission.

`task context` is the normal detailed read before work. The same JSON call
supplies current Verification Receipt readiness, all unresolved Findings,
continuation notes, and whether the optional Effort Advisory is enabled.
The fixed normal detail removes repeated fields and summarizes history;
`task show <task-id> --audit --json` explicitly retrieves bounded historical
Receipt/provenance and completion-cycle details when investigating history.
It is not another normal-loop call, and historical cycles never satisfy a
current gate. `review target set` returns the closed
`verification_route` and nullable `blocking_code` for the target it just stored,
so no second `task show` or LLM guess selects the manual or Runner branch. The
normal no-finding Tier 2 manual/fallback graph uses one `review result add`
and is bounded to six governance subprocess calls, or seven when the existing
boolean enables `task effort`; the Receiptless Runner-pass branch uses five or
six respectively. Explicit individual Receipt registration remains available
and uses seven/eight manual calls or six/seven Runner-pass calls. These are
operation counts, not measured total LLM token reductions. `task context`
rediscovers paused, blocked, review-pending, and in-progress work.

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

First [choose the completion route](task-governance-tool/references/task_workflow.md#choose-the-completion-route).
Authorized local `git_snapshot` work at the Git root on an existing attached
branch, without required hooks/signing, uses `prepare-finalization`. Stage only
the intended changes through the project's own Git process, then
[capture the exact review target](task-governance-tool/references/task_workflow.md#set-the-review-target)
with that preparation route. Interpret its response before proceeding.

Only for `source.verification_route=receipt_required` with null `blocking_code`,
run the exact approved project verification outside Taskgov and record its actual
result, duration and coverage. This example assumes it passed with full coverage:

```powershell
python .agents/skills/task-governance-tool/scripts/review_handoff.py prepare-finalization --repo . --directory reviews/g1 receipt <task-id> --result pass --duration-ms <milliseconds> --scope-coverage full --expected-target-generation <generation-from-target-set>
```

The `not_required` and `runner_pass` routes with null `blocking_code` skip that
manual verification/Receipt step and use the ready Packet from target setting.
The `blocked` route stops on its returned code; missing or unknown routes stop
closed too. Continue only with `handoff.status=ready`. Dispatch each returned
`handoff.review_requests[].request` to its independent reviewer and retain the
`finalization_command`. Integrated completion owns result registration, the fixed
reviewed commit, and Task completion; do not separately submit those results.
Any unresolved Finding, including low severity, stops integrated completion for
parent judgment before committing.

When reviews need waiting and the authorized service is enabled and available,
use [normal review waiting](task-governance-tool/references/review_wait.md#normal-wait)
and end the parent turn after confirmed readiness. With waiting OFF/unavailable,
an explicit parent-managed wait, or already-ended reviews, keep the integrated
route and use its retained command after the reviewers end. Failed waiting
allows ordinary turn end after reporting the limitation and resume condition;
it does not promise automatic resumption.

A received integrated body may be reported directly only when it matches the
original Task/target generation, has `status=completed`, successful stages,
actual Task done and sufficient reporting facts, without unresolved contradiction
or action. Disclose Findings (also low/resolved), warnings, limitations and
`not_recorded` work; these alone do not exclude success. Review PASS, message
acceptance or labels cannot establish completion or unseen content. Other results
use [Continue After Reviews](task-governance-tool/references/task_workflow.md#continue-after-reviews).

Unsupported targets/environments, required Git hooks/signing, missing commit
authority, or an explicit manual choice use `prepare` and its `submit_command`,
then [Manual Completion](task-governance-tool/references/task_workflow.md#manual-completion)
when authorized. Neither registration nor PASS grants Git permission.
For failed preparation use [bound handoff recovery](task-governance-tool/references/cli_contracts.md#recover-review-handoff).
Saved targets, Receipts and successful commits survive later failures. Inspect
uncertain outcomes before retrying only the remaining step; never repeat target
setting or Receipt registration merely to recover a Packet. `ok=true` on a
Receipt means it was saved, not that verification passed.

The staged snapshot excludes unstaged and untracked content. Taskgov records
only the caller's bounded verification facts; it never executes the command or
stores its body, arguments, exit code, output, logs, or environment. The
verification subject is derived from the locked capture-version-1 target; no
caller label or replacement subject input exists. Native Review Receipts carry
the declared v1 provenance fields or the explicit
[collected v2 arm](docs/evidence-specification.md#collected-answer-evidence);
migrated receipts project v0 absence, and `not_required` projects null. Provenance never upgrades the
Receipt assurance or proves reviewer identity, model/Skill execution,
competence, independence, diversity, quality, or truth. The completion commit
must have exactly one parent equal to the captured base and the same tree.
Meaningful target changes require a new target and fresh verification and
review receipts. Taskgov never stages files, creates branches, pushes, opens a
PR, or creates an Issue. Authorized integrated completion can commit only the
fixed reviewed snapshot; other completion evidence is validated read-only.

Opted-in current-schema preparations use the
[collected answer protocol](docs/review-completion-specification.md#collected-review-answers):
the reviewer returns exactly verdict, summary, findings, checks and limitations;
the collector binds and saves the complete answer under the assigned source.
The typed parent registration preserves every Finding and blocks completion
for an effective unable result. Real host admission and optional hook trust
remain separate; unsupported follow-up binding stays unavailable. Newly sealed
completion Evidence uses [Bundle 4/index 3 and graph 2](docs/evidence-specification.md#collected-answer-evidence),
preserving old sealed bytes.

The packet tells each reviewer how to inspect the exact target rather than
ambient `HEAD` or worktree content. The independent reviewer returns the
complete structured result for its saved protocol. In a shared-file workflow, the helper
captures the complete source response before display, prepares the explicit
unused ignored area, Packet and distinct result paths, and supplies exact
reviewer instructions and route-specific parent continuation before dispatch. No parent-LLM
extraction or command assembly is needed. Source mutation success is separate
from handoff failure; only Packet and original results are saved, never raw
envelopes/logs. Questions/failures may be reported as needed; a normal saved
acknowledgement is returned once without a duplicate success notification.
For a saved legacy request, each reviewer pipes their completed original JSON to the bundled
`review_handoff.py save` operation, which validates before exclusive creation,
checks the saved bytes against the Packet, and returns a short acknowledgement.
Failed or uncertain handoff is not ready; recover before registration. The
integrated finalizer (or `review_handoff.py submit` on the manual route) consumes
those originals without parent regeneration or a custom collector. Retain them until the
registration outcome is known and use all returned Findings, including low
severity, for repair. See Prepare And Record Reviews in the
[workflow reference](task-governance-tool/references/task_workflow.md#prepare-and-record-reviews) for
complete-Packet delivery and exceptional recovery. On the manual route, `submit`
also accepts a single document. It validates each document's exact
identity and combines only Receipts before the existing atomic writer. The
[package CLI reference](task-governance-tool/references/cli_contracts.md#structured-review-results),
under Structured Review Results,
defines the fixed JSON input for one Task, Contract revision, and complete
review target. Missing bindings or provenance are never inferred. Invalid,
stale, or duplicate results are rejected, and all submitted Receipts and Findings
are saved together or none are saved. Raw review transcripts are not accepted
as stored evidence. The existing `review receipt add` and `review finding add`
operations remain available for explicit individual registration, for example:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add <task-id> --reviewer <reviewer-key> --kind independent --verdict pass --summary "No blocking findings" --reviewer-class llm --model-state declared --declared-model-id <model-id> --skill-state not_used --context-relation fresh_context --review-profile general --review-lens correctness --review-method review_packet_inspection --json
```

Taskgov deterministically evaluates qualifying PASS receipts, changes-requested
receipts and effective unable judgments for the current review target and generation. Any unresolved high
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

This maintenance starts no daemon, timer, background process, queue, service,
browser, or maintenance command. Generated Evidence JSON, Viewer, and managed backups
remain runtime artifacts under the ignored project `.taskgov/` directory. Evidence
projection failure keeps the mutation successful and the last-good index,
leaves work due, and emits only its fixed warning. Viewer snapshot v4 reads
the [supported source schemas](docs/viewer-specification.md#snapshot-v4) and includes the same
bounded newest-first completion history as `task show --audit`. Sources 5-14 are shown
honestly as empty legacy-incomplete history. The Viewer contains only sanitized
task/review/audit projections and has no write controls or network dependency.
It validates schema-v18+ subject/provenance/capture bindings, the schema-v19
through schema-v31 Bundle discriminators, and the source-appropriate Bundle-v2/v3/v4
and Runner tagged graph, but adds no provenance/Bundle field, panel, filter, or
other snapshot-v4 UI surface.

Viewer auto-refresh is an explicit optional Setup choice. After the user selects
ON, preview and apply it from the target-project root:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --viewer-reload on --read-only --json
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --viewer-reload on --json
```

Use `off` for an explicit OFF choice; omitted choices remain unchanged. Setup
creates or toggles the physical package's `config/viewer.json`, retaining an
existing interval and using 30 seconds for new ON. OFF retains the interval
but disables reload. Check the per-feature outcome even when core setup succeeds.
Already open pages need a browser reload to adopt the published setting.
Ordinary Task commands do not change this configuration.

Reload affects only an already opened visible `file://` Viewer; it pauses while
search has focus or IME composition is active. It reloads the published HTML,
not SQLite, and launches no browser or network request. Automatic reload can
retain bounded search/filter state, selection, focus and scroll in the current
browser History entry. That state may survive browser session restore; its
five-minute validity limit is not a promise of physical erasure.
For custom intervals, invalid-profile behavior and exact privacy bounds, use
the current [reload policy](docs/viewer-specification.md#optional-visibility-aware-reload)
and [one-shot UI-state contract](docs/viewer-specification.md#one-shot-automatic-reload-ui-state).

## Public Commands

See the [formal command inventory](docs/specification.md#command-inventory)
for the complete public surface and the
[standalone CLI guide](task-governance-tool/references/cli_contracts.md#invocation-and-public-inventory)
for invocation and options.

Applicable commands accept `--repo`, `--json`, and `--read-only`; the root also
accepts `--version`. Storage paths and maintenance internals are not public CLI
choices. There is no separate history command or pagination; `task show --audit`
and the automatically maintained Viewer supply the bounded history projection.

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
stored Contract constraints and checkpoint summaries containing the
[bounded legacy counter forms](docs/specification.md#privacy-safety-and-stable-errors). It creates no
write or authority. Omitted Contract constraints retain already-validated
prior bytes under the existing carry-forward rule, while explicit new
constraints use the strict input guard. Completion-history public text has no
legacy exception and is strictly revalidated before `task show` or Viewer
output.

It is local-first; ordinary Task operations need no network service. Writes
stay within their authorized boundaries: canonical local Task state and
[Setup/maintenance](docs/setup-state-specification.md#setup-recovery-evidence-backup-and-viewer-maintenance),
explicit [Runner Plan authoring](#explicit-runner-plan-authoring), and the fixed
reviewed local commit under [integrated completion](docs/review-completion-specification.md#integrated-review-finalization).
Automatic verification requires the separately opted-in trusted-local Runner;
otherwise verification remains manual. Optional host review waiting retains its
separate connection and send authorization. Taskgov does not push or create
external Issues/PRs. SQLite remains helper state; governing project documents
and current user decisions remain authoritative.

### Explicit Runner Plan authoring

The [Runner application guide](task-governance-tool/references/runner_application.md)
shows how to preserve existing unittest discovery, bind each Task, update a
Plan and use execution/evidence/Packet results without manual result re-entry.
It distinguishes initial preparation cost from savings in a prepared flow.

Runner Plan authoring is optional and is not part of the normal Skill loop. It
uses the existing `task edit` command and never launches the Runner or sets a
review target. The first explicit `replace` against an absent Plan creates the
fixed local Plan with `trusted_local=true`; this is the repository's
trusted-local Runner opt-in. For that initial entry or a deliberate step
replacement, prepare a strict draft containing only `version` and one through
16 version-matched Step objects, then explicitly choose `replace`. This v2
example can be used on Windows, Linux, and macOS. Adapt the entrypoint and
limits to the approved verification; the script and its required files must
exist in the exact Git target being verified.

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

Save the JSON as `runner-plan-draft.json` and submit it from the target-project
root:

```powershell
Get-Content -Raw -Encoding utf8 .\runner-plan-draft.json |
  python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --runner-plan-action replace --json
```

On Linux/macOS, submit that same file with standard input redirection:

```sh
python3 .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --runner-plan-action replace --json < runner-plan-draft.json
```

`windows_limits` is Windows-only: Windows requires the pair before launch;
null does not mean an applied or unlimited limit. Linux/macOS do not apply the
pair, so no host-specific draft conversion is needed. They enforce per-process
CPU and wall timeout, not Windows Job memory or simultaneous-process limits.
See the [OS-specific guarantees](docs/runner-execution-specification.md#approved-os-specific-runner-guarantees)
and the self-contained example in the [package CLI reference](task-governance-tool/references/cli_contracts.md#runner-plan-example-and-os-limits).
Existing v1 drafts with flat `memory_mib` and `process_limit` remain valid.
Explicit v2 replace upgrades an existing v1
Plan while preserving unrelated entry values and bases; other actions preserve
its version. No automatic migration or additional normal-loop call is introduced.

The other actions read no stdin:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --runner-plan-action rebind --json
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --runner-plan-action detach --json
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --runner-plan-action disable --json
```

`replace`, `rebind`, and `detach` address the selected Task's entries:
`replace` upserts the supplied steps, `rebind` preserves existing steps while
moving the entry to the exact Task basis, and `detach` removes only that Task's
entries. `disable` instead sets global `trusted_local=false` while preserving
all entries; no action automatically re-enables a disabled Plan. An
action-bearing JSON success adds `runner_plan_update` with the selected action
and `updated`, `unchanged`, or `unconfirmed` status; an invocation without an
action retains its existing output. A real
Contract or verification-basis edit can carry one action in the same
invocation, for example:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo . <task-id> --verification "python -m unittest -q tests.test_focused" --runner-plan-action rebind --json
```

If that Task update succeeds but Plan publication cannot be confirmed, the Task
remains committed and the response returns
`task_applied_runner_plan_unconfirmed`. Do not rely on Runner execution until a
later explicit Plan-only `replace`, `rebind`, `detach`, or `disable` succeeds.
See
[`task-governance-tool/references/cli_contracts.md`](task-governance-tool/references/cli_contracts.md#runner-plan-actions)
for the closed input, result, and error contracts.

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

The current v0.13.0 package is an unpublished local candidate described by
[docs/releases/v0.13.0.md](docs/releases/v0.13.0.md). The immutable v0.12.0
and v0.11.0 candidate-lineage notes remain at
[docs/releases/v0.12.0.md](docs/releases/v0.12.0.md) and
[docs/releases/v0.11.0.md](docs/releases/v0.11.0.md). No exact final candidate
commit, tag, archive, checksum, or GitHub Release identity is fixed. An ordinary
push to `main` is not release publication, and no publication operation is
authorized here.

## Development Checks

On Windows, for current development and any later release candidate, run at least:

```powershell
python tools\document_contract.py --repo .
python tools\release_contract.py --repo .
python tools\test_lanes.py --repo . --check
python tools\test_lanes.py --repo . --lane all
python task-governance-tool\scripts\taskgov.py --help
python task-governance-tool\scripts\taskgov.py --version
git diff --check
```

For the representative Linux/macOS checks, use:

```sh
python3 tools/document_contract.py --repo .
python3 tools/release_contract.py --repo .
python3 tools/test_lanes.py --repo . --check
python3 tools/test_lanes.py --repo . --platform-smoke
git diff --check
```

These selected platform checks supplement, not replace, Windows full-suite
candidate qualification. They do not run the Windows-only Runner tests.

The document checker is offline and read-only. It validates this repository's
closed authority registry, route-section structure, owner/anchor/link
reachability, history provenance and non-authority, search exclusion, and
required authority headings. It does
not freeze ordinary prose, impose a repository-wide Markdown dialect, or use
hidden reduction and checker-size thresholds. Ambiguous syntax in an authority
route still fails closed instead of being guessed.

For shorter local feedback, replace `all` with `fast`, `integration`, or
`release`. The three base lanes own every standard-discovery test exactly once;
an unassigned, duplicate, or stale test module fails before execution. The
`all` lane preserves the ordered test IDs and suite from
`python -m unittest discover -s tests`.

The trusted-local Runner is explicitly opt-in. Only a selected repository that
the user already trusts may be eligible; untrusted, external, or unsupported
targets use the existing manual verification path and are never executed by
the Runner. The Runner uses fixed argv with no shell or PATH lookup, excludes
credentials from its child environment, applies the
[OS-specific process limits](docs/runner-execution-specification.md#approved-os-specific-runner-guarantees),
bounds output, retires its managed processes, and removes
its private temporary tree without retaining raw output. These are process,
cleanup, and privacy
requirements for trusted code, not a claim of hostile-code containment or
network isolation.

The retired LPAC portability fixture, Candidate C comparison, LPAC/AppContainer
policy, ETW diagnosis, and related recovery infrastructure remain physically
absent, with no tracked archive or dormant copy. They are not Runner
qualification or completion gates.

CI consumes the same repository-only policy:

| Event | Python 3.12 | Python 3.14 |
|---|---|---|
| pull request | `fast`, `integration`, `release` | `fast` |
| push to `main` | `fast`, `integration`, `release` | `fast`, `integration`, `release` |
| default manual `workflow_dispatch` | `all` | `all` |

Every event first validates the complete standard discovery inventory and
base-lane ownership. Pull-request and push `release` selections then defer only
the two closed wall-clock qualifier identities while retaining their separate
deterministic functional and capacity tests. Default manual `workflow_dispatch`
defers nothing.

The manual release-candidate aggregate requires policy validation, both
full-version Windows jobs, and both existing platform checks to succeed.
Failed or skipped dependencies cannot qualify the gate.
An `operation_sequence` value may correlate separately authorized candidate
work, but neither that value nor a successful gate authorizes workflow
dispatch, push, tag, or publication.

An additional platform job runs CLI startup, common processing, and
applicable artifact-operation checks on Ubuntu 24.04 x86-64 and macOS 15
Apple Silicon with Python 3.12. Both hosts also run selected existing physical-install,
ordinary Task completion, Evidence, Viewer, Backup, and recovery integration tests.
Both hosts additionally run the public Runner-to-completion/Evidence tests and
existing cleanup/pending/stale gate cases. These selected platform tests must
execute without SKIP; the exhaustive Windows suite still permits tests that are
inapplicable to Windows to skip.
Manual `platform_only=true` runs policy and these platform checks alone,
skipping the Windows full matrix and candidate gate. This mode is not release
qualification or a change to the published platform or Runner support boundary;
omitting the input or selecting `false` retains the full manual route.

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
- `plan.md`: current decisions, open issues, and non-delegated unfinished
  static contracts.
- `docs/release-install.md`: current candidate, immutable published artifact,
  and installation identity.
- `docs/history/README.md`: non-authoritative lineage index.

Inspect live Task state and evidence through the public CLI; project documents
do not mirror that volatile state. Normal repository searches exclude history;
load it only for an explicitly named lineage, migration, rationale-recovery, or
superseded-evidence need.
