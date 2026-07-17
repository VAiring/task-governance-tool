# Static Task Viewer Forward Test

Date: 2026-07-17

## Scope

Validate that a fresh agent can use the project skill to answer a realistic
request for an offline browser-readable task view without inventing a server,
live refresh, browser editing, or unsupported CLI aliases.

The user-equivalent request asked the agent to create an offline HTML view of
current tasks and explain whether later task updates appear automatically or
can be edited in the browser. The agent was told not to execute commands or
modify files, only to provide the exact command and behavior.

## Iteration

The first fresh-context attempt understood the static/read-only behavior but
invented `taskgov viewer export --project-root`. That attempt failed.

`SKILL.md` and `references/task_workflow.md` were tightened to state the exact
bundled invocation and that no `viewer`, `--project-root`, or guaranteed global
`taskgov` alias exists.

A second attempt exposed an evaluation-harness issue: the local skill item was
not available to the agent, so it did not read the revised skill. This result
was not treated as product evidence. The final attempt used the skill-creator
recommended prompt form with the local skill folder path in a fresh context.

## Final Result

PASS. The fresh agent selected:

```powershell
python scripts/taskgov.py web export --repo "C:\WorkSpace\task-governance-tool" --json
```

It also selected the correct no-write preview:

```powershell
python scripts/taskgov.py web export --repo "C:\WorkSpace\task-governance-tool" --read-only --json
```

The response correctly explained all of these points:

- the default file is under
  `state/projects/<project-id>/viewer/task-viewer.html`
- the page is a static timestamped snapshot and must be explicitly regenerated
- browser task editing and browser-side SQLite access are unavailable
- no local server or network connection is required
- the CLI does not open a browser automatically or write SQLite during export

The final forward-test agent executed no commands and created no artifacts.

## Browser Acceptance Evidence

The current project database exported successfully to the ignored default
viewer path with snapshot version 1, 9 tasks, and 38 bounded events. The viewer
template did not change after the earlier generated-file browser acceptance,
where the user opened the `file://` artifact and reported no visible issue
after the requested UI checklist.

Automated navigation to the current `file://` artifact was rejected by the
browser-control URL policy. No alternate browser-control workaround was used.
Current static renderer tests continue to cover the desktop and narrow-screen
media rules, security policy, text-only rendering, controls, and layout markers.
The generated file remains ignored and outside release artifacts.
