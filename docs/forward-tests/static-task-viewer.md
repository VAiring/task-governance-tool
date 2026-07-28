# Static Task Viewer Forward Test

Date: 2026-07-17

Historical execution record: commands reflect the tested release on this date
and are superseded by the current v0.8.0 active guidance.

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

## TG-M15.5 Visibility-Aware Reload Evidence

Date: 2026-07-28

A fresh physical project-scoped copy was set up with an unshipped
`config/viewer.json` interval of 5 seconds. `setup` published the decimal
interval into the canonical Viewer, and a routine `task add` published the
latest snapshot. After the config file was removed, the next routine mutation
published interval sentinel `0`; the decoded snapshot contained both task
markers. This confirms that the installed package reads its own optional
profile and that an absent profile disables refresh on the next relevant
publication. The temporary project and its generated state were then removed.

Browser automation again rejected direct navigation to the generated
`file://` artifact. The safety response explicitly prohibited switching to an
alternate browser surface or indirect URL workaround, so none was attempted.
The user then performed the permitted real-browser check. They opened the
generated `file://` Viewer while its header showed
`Snapshot generated: 2026-07-28T11:11:48Z`. A task note mutation published a
new snapshot, and the still-open Viewer showed
`Snapshot generated: 2026-07-28T13:44:28Z` without a manual reload. The user
provided a screenshot and confirmed the updated header, so the visible-page
reload smoke passed.

As the strongest non-browser check, Node.js 24.14.1 extracted the exact shipped
initialization and scheduler blocks from
`assets/task-viewer.template.html` and executed them with fake monotonic time,
visibility, timeout, and reload boundaries. The harness passed all of these
cases:

- sentinel `0` and non-`file:` locations create no listener, timeout, or reload;
- a visible valid interval owns one timeout;
- hiding clears that timeout;
- becoming visible before the deadline schedules only the remainder;
- an early timeout recheck cannot reload before the deadline;
- becoming visible after the deadline requests one reload immediately; and
- later visibility events cannot create another timeout or reload request.

The harness also confirmed that fatal UTF-8 decoding rejects malformed bytes.
Focused Python tests bind the scheduler's one-timeout, one-reload, monotonic
remainder expressions and prove that initial rendering precedes scheduler
startup. Together with the user-observed `file://` refresh, this satisfies the
formal forward-test gate. The temporary profile was then removed, `setup`
republished sentinel `0`, and the repository returned to its default
refresh-disabled state.
