# Optional Usage Collection Hooks

Read this only to introduce or diagnose automatic numerical collection. It is
not a normal Task-loop procedure, and usage never changes completion gates.
Observed shared-work totals are not exclusive Task costs or final billing.

## Installation And Trust

Before enabling or trusting collection, read the [collection scope](#collection-scope)
for included responses, coverage endpoints and source/privacy boundaries.
Use the existing explicit [Setup choices](cli_contracts.md#optional-setup-features)
and, only for an upgrade, [upgrade procedure](task_workflow.md#upgrade-and-recovery).
The [collection switch](#collection-selection) retains ON/OFF choices separately
from host trust. Setup prepares core state,
the numerical store, and, for selected-ON or existing hooks, the project's
`.codex/hooks.json`, preserving unrelated
hooks and avoiding duplicate taskgov handlers on repeat setup. Read
`data.usage_hooks`: `prepared`/`current` means configuration only, never enabled
collection. For `next_action=review_and_trust_hooks`, ask the user to review and
trust the project's definitions in Codex. If already trusted and unchanged,
no new trust is implied. Trust remains unconfirmed; do not detect or edit it
yourself or claim that the hooks ran. Changed definitions may need renewed trust.

`unavailable`/`review_hook_configuration` means the existing config was preserved
for inspection (for example, malformed JSON or a manual/inline usage hook that
cannot be safely adopted). Do not blindly overwrite it or retry completed Task
writes. A successful core setup with unavailable usage/hooks is not collection
readiness. `setup --read-only` previews the preparation without writing.

For handler behavior, use [the execution boundary](#hook-execution-boundary).
Only this tool's source repository uses [development self-hosting](#development-self-host).
When establishing a host-support claim, use [host evidence](#host-delivery-evidence);
configuration alone does not establish delivery.

## Hook Execution Boundary

Setup owns four handlers, marked `taskgov: collect numerical usage`, for
SessionStart, Stop, SubagentStop (30-second bounds), and SessionEnd (3 seconds).
They invoke the setup interpreter and physical `scripts/usage_hook.py` with
`--repo` pointing to the project root. Windows uses an encoded, safely quoted
PowerShell invocation so path characters survive the caller shell. Run from
that project root; no LLM-supplied identity or per-response command is needed.
This prepares only project-local definitions, not user-wide hooks, feature flags
or host trust. Other configuration layers stay user-managed. Hooks never
migrate or repair either store.

Timeouts are host execution bounds, not performance acceptance requirements.
The adapter emits only `{}` with successful exit, never model context, a
continuation request, or a block decision. It modifies only canonical numerical
state and usage projections, not Task state, original Evidence, Viewer or source.
No daemon, external service or process termination is required.

## Development Self-Host

For this tool's own development repository, setup uses the physical source
entrypoint `task-governance-tool/scripts/usage_hook.py --repo <project-root>`.
Run from that same project root; `--repo` acknowledges
the existing self-host exception, not a different working directory or state
path. Omission keeps source-tree collection disabled. Ordinary project installs
need no new argument. Copied/linked packages and competing installs remain
unsupported; do not create a second install to enable development collection.

Use the same [installation/trust](#installation-and-trust) and
[handler boundaries](#hook-execution-boundary); this exception changes neither.

## Collection Selection

Before choosing ON, read the [collection scope](#collection-scope).
Use explicit `setup --usage-collection on|off` for an approved change. OFF is
remembered in the project package's local Setup choices and prevents subsequent
collector calls before reading logs or writing usage, even with trusted hooks.
It preserves stored history, definitions and host trust. ON prepares definitions
but does not prove trust or delivery. With no saved choice, legacy configured
hooks retain prior behavior; fresh setup does not create them without ON.

## Collection Scope

After core schema-26 Setup, the caller's in-progress and review-pending states
share one held Task and continuous inclusive-turn interval. Actual responses
while waiting count, even for unrelated discussion in that session; waiting
time alone invents no usage. Explicit release or completion ends coverage.
Setup preserves older closed intervals and legacy overlapping holdings. Use
ordinary authorized state transitions to resolve those holdings, not a manual
measurement command. Usage unavailability never blocks those transitions.
SessionStart can register its own session. Committed owner acquisition, actual
Receipt binding and successful bound original-review save register participants
without LLM-entered IDs or an extra command. The collector reads only their
validated same-project logs; parenthood does not register a child. Reviewer read
remains read-only, and a saved review counts only after its core binding exists.
Only a retained legacy supervisor uses the
[wait-decision marker](cli_contracts.md#record-review-wait-decision); normal
deterministic waiting creates no supervisor usage turn. Actual parent/reviewer
usage and existing marker history remain intact. Numerical schema 5 requires
explicit setup to initialize or migrate; ordinary helpers and hooks never
upgrade old stores. Usage unavailability does not prevent review or Task
completion, and a marker does not prove collection.

Standard Codex dated `sessions` and flat `archived_sessions` beneath `CODEX_HOME`
(default user `.codex`) are supported source layouts. Paths/filenames are hints,
not identity; physical path and header validation remain necessary. Unknown
layouts, lost files or access failures stay unavailable, with observed totals
preserved. No unrelated chat headers or bodies are collected.

## Coverage And Recovery

Use the [collection scope](#collection-scope) to identify included responses,
participants, sources and coverage endpoints. If a configuration or trust change
is needed, use the explicit [collection selection](#collection-selection) or
[installation/trust](#installation-and-trust) procedure; diagnosis itself does
not authorize those writes.

Later events catch up registered participants, including completed Tasks and
late endpoint-turn records. With hooks disabled/untrusted or no subsequent
event, collection cannot catch up. Start/resume an ordinary session in that
same project to trigger recovery; merely restarting the app does not do so.
Do not redo Task completion or review registration to recover numerical data.
SessionEnd is best effort, not finalization. Stop, elapsed time and a quiet file
do not prove final coverage; current snapshots remain pending/incomplete or
conflicting where appropriate.

Normal collection reads appended records using saved safe context. Prefix
integrity is checked later in bounded resumable slices, with explicit
`prefix_verification_deferred` coverage; unchanged mtime/size is not proof.
Later events share work with old participants/segments and deferred audits.
Changed shared components are recomputed; stored snapshot/counter and published
byte validation remains intact. Budget or publication failure preserves prior
progress/output and never asks to repeat Task completion. This is not a measured
guarantee that every host invocation avoids its timeout.

To establish support not already observed for the relevant host/source, use
[host-delivery evidence](#host-delivery-evidence).

## Host Delivery Evidence

Claims about host delivery and source format require observation in an isolated
approved project. Mocked tests or CLI help alone do not prove trust/delivery in
Desktop, CLI, Windows, Linux or macOS. Do not infer unobserved host support.
This distinguishes configuration from observed support; it does not require a
new experiment for every installation or turn already covered by applicable
evidence. Per-project trust and actual collection still cannot be inferred from
another environment's result. Unobserved delivery remains unconfirmed and does
not block ordinary Task work.
