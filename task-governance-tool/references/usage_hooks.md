# Optional Usage Collection Hooks

Read this only to introduce or diagnose automatic numerical collection. It is
not a normal Task-loop procedure, and usage never changes completion gates.
Observed shared-work totals are not exclusive Task costs or final billing.

## Installation And Trust

Use the existing explicit setup/upgrade procedure. It prepares core state,
the numerical store, and the project's `.codex/hooks.json`, preserving unrelated
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

For this tool's own development repository, setup uses the physical source
entrypoint `task-governance-tool/scripts/usage_hook.py --repo <project-root>`.
Run from that same project root; `--repo` acknowledges
the existing self-host exception, not a different working directory or state
path. Omission keeps source-tree collection disabled. Ordinary project installs
need no new argument. Copied/linked packages and competing installs remain
unsupported; do not create a second install to enable development collection.

## Coverage And Recovery

SessionStart can register its own session. Committed owner acquisition, actual
Receipt binding and successful bound original-review save register participants
without LLM-entered IDs or an extra command. The collector reads only their
validated same-project logs; parenthood does not register a child. Reviewer read
remains read-only, and a saved review counts only after its core binding exists.

Standard Codex dated `sessions` and flat `archived_sessions` beneath `CODEX_HOME`
(default user `.codex`) are supported source layouts. Paths/filenames are hints,
not identity; physical path and header validation remain necessary. Unknown
layouts, lost files or access failures stay unavailable, with observed totals
preserved. No unrelated chat headers or bodies are collected.

Later events catch up registered participants, including completed Tasks and
late endpoint-turn records. With hooks disabled/untrusted or no subsequent
event, collection cannot catch up. Start/resume an ordinary session in that
same project to trigger recovery; merely restarting the app does not do so.
Do not redo Task completion or review registration to recover numerical data.
SessionEnd is best effort, not finalization. Stop, elapsed time and a quiet file
do not prove final coverage; current snapshots remain pending/incomplete or
conflicting where appropriate.

Host delivery and source format must be verified in an isolated approved
project. Mocked tests or CLI help alone do not prove trust/delivery in Desktop,
CLI, Windows, Linux or macOS. Do not infer unobserved host support.
