# Review Wait And Same-Parent Resumption

Use this only for authorized independent reviews that may outlive the parent
turn. The deterministic project MCP service owns observation and timer writes;
the parent retains review judgment, originals, registration and Task decisions.
It replaces a resident coordinating LLM. Non-review waits are unchanged.

## Setup And Connection

With explicit user selection, preview and then apply `setup --review-wait on`
using the normal scoped taskgov entry. OFF preserves all state and rejects new
preparation/start. Setup saves only local policy; it does not configure the host,
grant permission, create a reservation, launch a process or confirm connection.
Do not add a Setup call or question to ordinary Task work.

The project MCP configuration must separately run the physical installed
`scripts/review_wait_server.py` using Python 3.12+ with these fixed arguments:

```text
-B <physical-skill>/scripts/review_wait_server.py
--repo <governed-project>
--server <installed-public-codex-app-tools-server.mjs>
--codex-home <actual-Codex-home>
--timezone <confirmed-host-IANA-timezone>
```

Review the actual destination `<governed-project>/.codex/config.toml` and use
existing user authorization before editing it. Preserve unrelated configuration.
Resolve the wrapper from the installed public Codex app tools, not a guessed
cache version or downloaded replacement. Inherit `CODEX_APP_TOOLS_PIPE_PATH`,
`CODEX_MCP_NODE_PATH` and `CODEX_THREAD_ID` by name in `env_vars`; never copy their
values or fabricate metadata. If Python lacks timezone data, use already
available validated local TZif data through `PYTHONTZPATH`; do not install or
download data as a side effect. Host trust/permission remains user-controlled.
Restart only when needed to load the reviewed project configuration.

The connected service lists six `review_wait_*` controls below. Discovery is
inert, and no Task ID, reservation ID or storage path belongs in server arguments.
Loading/restarting it never resumes or retries a former worker. If the policy,
connection, timezone or required public operations are unavailable, report that
automatic resumption is unavailable and continue permitted review transport.
Do not introduce a substitute supervisor or claim fixture tests prove delivery.

## Prepare And Start

1. Dispatch the complete independent-review requests directly through the
   authorized host subagent facility. Retain each actual returned handle, the
   original Task/Packet and result paths/submit command. Never invent handles
   or launch duplicate reviewers after an uncertain dispatch.
2. Using public `automation_update`, create one **PAUSED** heartbeat for this
   same parent. Use a fixed prompt identifying the original Task and asking the
   resumed parent to inspect/close this review wait and recover actual results.
   Keep it quiet while unchanged and notify only on meaningful completion,
   failure or required user action. Retain the returned real automation ID.
   An unknown creation outcome needs inspection, not another creation.
3. Call `review_wait_prepare(task_id, automation_id, reviewer_ids)` with those
   exact returned `/root/...` handles (canonical thread UUIDs remain supported).
   The service resolves handles from this parent's structured public dispatch
   records; do not manually transcribe UUIDs or infer them from message prose.
   It reads current Task ownership, reviewer turns and the
   PAUSED reservation and records one canonical association. It does not arm
   or send. Never reuse this reservation for another preparation or Task.
4. With the user's existing authorization for this same-parent wake, call
   `review_wait_direct_delete_start(automation_id)` once. Retain its `probe_id`.
   End the parent turn only when `ok=true`, `state.status=waiting`,
   `state.timer_phase=active`, and `worker_alive=true`. It has then confirmed
   the ten-minute check. Failed/unknown start is inspected, never blindly retried.

If handle resolution is unavailable or ambiguous, preparation stops before any
association or timer effect. Inspect the actual dispatch and returned diagnostics;
do not invent a mapping or relaunch reviewers to bypass the failure. Lookup is
bounded to sixteen parent turns; a same-turn conflicting mapping fails closed.
The retained PAUSED reservation is inspected/cleaned up through the public host
tool before selecting another wait. Continue the permitted review transport if
automatic resumption is unavailable.

The worker observes exact reviewer turns and the original parent through public
reads. At all-ended and original-parent idle it deletes that reservation, confirms
the exact receipt and config absence, then rechecks freshness and sends once.
Failed/interrupted reviews also count as ended; a saved file alone does not.
Timer deletion, host send acceptance and genuine parent receipt are separate.
The host has no atomic compare-and-send promise against external UI changes.

## Resume, Stop And Recover

Use `review_wait_view(automation_id)` for original association state and
`review_wait_direct_status(automation_id)` for timer/send/receipt state. These
never start workers or replay effects. After direct delivery, call
`review_wait_direct_ack(automation_id, probe_id)` in the actual new parent turn.
It records receipt only, not PASS, an original, review registration or completion.
Then recover the retained original Task/Packet and process actual results through
the normal review gates, including missing originals and changed targets.

A new parent turn, including the ten-minute fallback, suppresses an unsent
direct wake and initiates bounded known-ACTIVE cleanup. Inspect the old wait;
if needed, call `review_wait_direct_cancel(automation_id, probe_id)` to stop and
clean up. Cancellation cannot recall a message already dispatched. Before
another healthy unfinished wait, require settled cleanup of the old association,
then explicitly create a fresh PAUSED reservation and prepare the same actual
reviewers. Do not redispatch them simply to wait again.

At the due time the worker stops starting new deletion sequences and allows a
further ten-minute fallback delivery window. A deletion already begun may settle
and send within the outer deadline after fresh checks. At twenty minutes it
expires and attempts one pause only for a known ACTIVE reservation. This is a
bounded window, not a delivery guarantee. EOF, stale basis and disabled policy
also stop observation and trigger that bounded cleanup.

Pending/unknown arm, delete, pause or send outcomes remain unknown. Do not retry,
recreate the reservation, change sender/transport, or start a competing wait
while its effect is unresolved. Restart is inspection-only; an explicit cancel
may clean up a known ACTIVE association under its writer lease. Missing/corrupt
state is not recreated. The server retains up to 64 loaded associations; a
capacity failure requires ending existing workers safely before a host restart.

No `wait-ended` helper or supervisor usage turn is produced by this workflow.
Actual parent/reviewer usage and historical legacy markers remain intact.
Originals and Task evidence keep their existing transport, freshness and
completion gates. Stopping a wait does not cancel reviewers or unrelated work.
