# Optional Usage Collection Hooks

Read this only to introduce or diagnose automatic numerical collection. It is
not a normal Task-loop procedure, and usage never changes completion gates.
Observed shared-work totals are not exclusive Task costs or final billing.

## Installation And Trust

First use the existing explicit setup/upgrade procedure so both core state and
the numerical store are current. A successful core setup with unavailable usage
does not mean collection is ready. Hooks never migrate or repair either store.

Ask for approval of the exact project's `.codex/hooks.json` and show the actual
commands before writing. The user must review/trust the definitions in Codex.
Do not overwrite an existing hook configuration, install user-wide hooks, edit
trust state or enable this as an ordinary setup side effect. If another hook is
already configured, agree the narrow merge before editing it.

For a physical package at `.agents/skills/task-governance-tool`, the following
definition runs from the governed project root. Resolve the Python interpreter
available on that host before approval; substitute its safely quoted absolute
path if `python`/`python3` is unavailable. This is a host hook, not another public
taskgov command or an instruction to the LLM to run it after every response.

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 -B .agents/skills/task-governance-tool/scripts/usage_hook.py", "commandWindows": "python -B .agents/skills/task-governance-tool/scripts/usage_hook.py", "timeout": 30}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "python3 -B .agents/skills/task-governance-tool/scripts/usage_hook.py", "commandWindows": "python -B .agents/skills/task-governance-tool/scripts/usage_hook.py", "timeout": 30}]}],
    "SubagentStop": [{"hooks": [{"type": "command", "command": "python3 -B .agents/skills/task-governance-tool/scripts/usage_hook.py", "commandWindows": "python -B .agents/skills/task-governance-tool/scripts/usage_hook.py", "timeout": 30}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "python3 -B .agents/skills/task-governance-tool/scripts/usage_hook.py", "commandWindows": "python -B .agents/skills/task-governance-tool/scripts/usage_hook.py", "timeout": 3}]}]
  }
}
```

Timeouts are host execution bounds, not performance acceptance requirements.
The adapter emits only `{}` with successful exit, never model context, a
continuation request, or a block decision. It modifies only canonical numerical
state and usage projections, not Task state, original Evidence, Viewer or source.
No daemon, external service or process termination is required.

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
