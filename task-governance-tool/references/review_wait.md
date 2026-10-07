# Review Wait And Same-Parent Resumption

Use this only for authorized independent reviews that may outlive the parent
turn. The optional project service owns reservations and observation. The parent
keeps review judgment, original results, registration and Task decisions.
Non-review waiting is unchanged; no resident coordinating LLM is needed.

## Setup And Connection

Explicit `setup --review-wait on` selects local policy only. Do not add Setup
or a new permission question to ordinary Task work. Host configuration, trust,
timezone and same-parent send authorization remain separate prerequisites.
OFF preserves existing state and permits inspection/known-state cleanup.

The project MCP configuration separately runs the physical installed
`scripts/review_wait_server.py` with Python 3.12+ and these arguments:

```text
-B <physical-skill>/scripts/review_wait_server.py
--repo <governed-project>
--server <installed-public-codex-app-tools-server.mjs>
--codex-home <actual-Codex-home>
--timezone <confirmed-host-IANA-timezone>
```

Use the actual installed public wrapper. Review the destination
`<governed-project>/.codex/config.toml` and existing authorization before editing;
preserve unrelated settings and allow the normal `review_wait_wait` tool.
Inherit `CODEX_APP_TOOLS_PIPE_PATH`, `CODEX_MCP_NODE_PATH` and `CODEX_THREAD_ID`
by name, never copy values or synthesize executor metadata. Existing validated
TZif data may be configured with `PYTHONTZPATH`; do not install/download data as
a side effect. Restart only when required to load reviewed configuration.
Discovery, inspection and restart never start or replay an old worker.

## Normal Wait

1. Dispatch the unchanged independent-review requests through the authorized
   subagent facility. Retain the original Task/Packet, actual returned handles,
   original-result paths and registration command. Never duplicate a dispatch
   whose outcome is unknown.
2. Call `review_wait_wait(task_id, reviewer_ids)` once with that original Task
   and the actual returned `/root/...` handles (canonical UUIDs also work).
   The service resolves the parent-scoped structured identities and internally
   creates, prepares and starts its fixed same-parent ten-minute reservation.
   Supply no automation/probe IDs, prompt, schedule or destination.
3. End this turn only on `ok=true,status=waiting,parent_may_end=true`.
   The service then owns observation, deletion confirmation and one same-parent
   send when every exact reviewer ends and the original parent is idle.
4. On direct resumption, recover the retained review originals and continue the
   existing review procedure. No individual ACK, routine status/view, reservation
   management or `wait-ended` call is needed. The worker independently observes
   the matching incoming event in the new parent turn and records receipt.
   The notification itself is not PASS, an original or a completion Receipt.

At a scheduled ten-minute check, handle actual review results normally. If the
same reviewers are healthy but unfinished, repeat only the same wait request.
It settles known cleanup and prepares the next reservation internally.
`status=reviews_ended,parent_may_end=false` means continue result processing.
Do not redispatch reviewers merely to wait again, choose reservation phases or
manually create another heartbeat.

## Failures, Stop And Diagnosis

A failed call or `parent_may_end=false` never grants permission to end the turn
as a ready wait. Unknown creation, activation, deletion, pause or send prevents
replay and replacement. Preserve the original wait/results and report the
specific limitation; do not infer success from an accepted host send.
An unavailable policy, connection or required public operation does not prevent
other permitted review transport and does not authorize enabling settings,
another sender or a substitute supervisor.

Use `review_wait_inspect(task_id)` only for requested diagnosis or a failure,
and `review_wait_stop(task_id)` for explicit cancellation or known-state recovery.
They need no saved automation/probe IDs and add no normal completion gate.
Inspection is read-only. Stop cannot recall a dispatched message or retry an
unknown effect. Restart is inspection-only; no active flag alone proves a live
worker. Changed Task basis or actual reviewer turns require recovery of the old
wait before a new association.

The six earlier per-reservation prepare/view/start/status/cancel/ack controls
remain available for old associations and explicit diagnosis. They are not part
of this normal workflow. No automatic migration of experimental state occurs.
The service retains the existing 64-association bound and 64 request attempts
per parent/Task; capacity failures preserve history and never evict workers.

Timer deletion, host acceptance and actual new-turn receipt remain separate.
The worker uses bounded public observation; unknown or truncated reads do not
establish receipt. Missing receipt requires no extra LLM operation or Task gate.
All original review/verification/usage/Task gates and failure recovery remain.
