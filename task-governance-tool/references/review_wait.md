# Review Wait And Same-Parent Resumption

Use this only for authorized independent reviews that may outlive the parent
turn. The optional project service owns reservations and observation. The parent
keeps review judgment and Task decisions. Integrated preparation delegates only
its fixed registration/commit/completion; manual preparation keeps those steps
with the parent.
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

Use the already selected local policy and connected project MCP service with
its existing host/timezone/send authorization. Ordinary waiting adds no Setup
or approval step. For an explicit setup request, use [Setup and connection](#setup-and-connection).
If a prerequisite is unavailable or an operation fails, use
[failure and recovery guidance](#failures-stop-and-diagnosis); never retry an
unknown effect or infer readiness from a failed call.

Use the available `review_wait_wait` definition. If discovery is needed, search
that exact tool in the connected review-wait MCP namespace, rather than general
`wait` or `task` terms. Host-qualified names may look like
`mcp__taskgov_review_wait__review_wait_wait`; use the actual returned name and
schema, since the server registration can differ. Reuse a complete definition
already returned instead of displaying the same candidates again. Missing,
incomplete or changed definitions, or recovery needing another control, can
justify further discovery; this adds no lookup or confirmation prerequisite.

1. Dispatch the unchanged independent-review requests through the authorized
   subagent facility. Retain the original Task/Packet, actual returned handles,
   original-result paths and selected continuation command. Never duplicate a dispatch
   whose outcome is unknown.
2. Call `review_wait_wait(task_id, reviewer_ids)` once with that original Task
   and the actual returned `/root/...` handles (canonical UUIDs also work).
   The service resolves the parent-scoped structured identities and internally
   creates, prepares and starts its fixed same-parent ten-minute reservation.
   Supply no automation/probe IDs, prompt, schedule or destination.
3. End this turn only on `ok=true,status=waiting,parent_may_end=true`.
   The service then owns observation, deletion confirmation and one same-parent
   send when every exact reviewer ends and the original parent is idle.
4. On resumption or an all-ended result, follow
   [Continue after reviews](task_workflow.md#continue-after-reviews), the single
   result-processing procedure for integrated, parent-managed and direct routes.
   The worker observes the matching incoming event and records receipt; no
   individual ACK, reservation management or `wait-ended` call is needed.

Another Task's work or notification may advance this same parent's turn without
cancelling existing managed waits. Each remains bound to its original Task and
reviewers; the service defers while the parent is busy and coordinates its sends.
It finds the first actual receiving turn through bounded public history even if
newer turns already exist. No parent-side history correlation or extra call is needed.

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
Discover the applicable exact name in the same connected namespace when needed;
an already available complete definition needs no repeat lookup.
Inspection is read-only. Stop cannot recall a dispatched message or retry an
unknown effect. Restart is inspection-only; no active flag alone proves a live
worker. Changed Task basis or actual reviewer turns require recovery of the old
wait before a new association.

The six earlier per-reservation controls remain available for old associations
and explicit diagnosis: `review_wait_prepare`, `review_wait_view`,
`review_wait_direct_delete_start`, `review_wait_direct_status`,
`review_wait_direct_cancel` and `review_wait_direct_ack`. Discover the needed
control by its exact name in that namespace. They are not part
of this normal workflow and their MCP descriptions identify that compatibility
role. Only a retained legacy supervisor context uses the separate
[wait-decision marker](cli_contracts.md#record-review-wait-decision); normal
handoff responses do not generate that command. No automatic migration of experimental state occurs.
The service retains the existing 64-association bound and 64 request attempts
per parent/Task; capacity failures preserve history and never evict workers.

Timer deletion, host acceptance and actual new-turn receipt remain separate.
The worker uses bounded public observation. A truncated structured event may
establish notification correlation when the complete identity and host evidence
match; unknown reads or incomplete identity cannot. Correlation does not prove
unseen body content. Missing receipt requires no extra LLM operation or Task gate.
All original review/verification/usage/Task gates and failure recovery remain.
