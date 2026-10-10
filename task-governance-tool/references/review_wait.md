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

## Runtime Upgrade And Reconnection

A connected service keeps imported runtime/schema values until it is reloaded.
Before package replacement or schema migration, settle or stop known running
waits with the compatible service's existing recovery controls. Preserve their
records and never retry unknown timer/send effects. After successful explicit
Setup, restart/reconnect the same reviewed MCP service, preserving its host
configuration and trust. CLI success alone does not prove service compatibility.

If migration already succeeded and the service is known to retain the old
runtime, reload that same reviewed service before inspecting retained
associations. `review_wait_unavailable` or `schema_too_new` alone does not prove
this condition: an old deployed package can also be incompatible. Optional
inspection reports loaded source identity, loaded/deployed schema ceilings and
`comparison=matching|different|unknown`. A code difference is evidence to assess
against the intended upgrade; a lower deployed ceiling requires package
consistency diagnosis, not blind reload. Unknown comparison is not evidence
that a restart will fix the error.

Do not repeat successful Setup, infer no previous effects, or create a
replacement timer. Restart/inspection never replays a worker; stored ACTIVE is
not live readiness. Use the [failure procedure](#failures-stop-and-diagnosis)
for each known state. This upgrade instruction adds no ordinary Task-loop call,
automatic restart or permission to change host settings/trust.

## Normal Wait

Use the already selected local policy and connected project MCP service with
its existing host/timezone/send authorization. Ordinary waiting adds no Setup
or approval step. For an explicit setup request, use [Setup and connection](#setup-and-connection).
When dispatched reviews still need waiting and these prerequisites hold, use
this service and end the parent turn after confirmed readiness. An explicit
user request for parent-managed waiting remains an exception; otherwise do not
substitute a parent-side wait by preference. This applies to both integrated
and manual completion transports. If existing dispatch/results already show
all reviewers ended, process the actual result under step 4 below; no reservation
or extra status query is needed just to establish that branch.
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
3. Delegate waiting and end this turn on
   `ok=true,status=waiting,parent_may_end=true` only.
   The service then owns observation, deletion confirmation and one same-parent
   send when every exact reviewer ends and the original parent is idle. This is
   readiness, not Task completion. For a failed call, the linked failure
   procedure distinguishes ordinary turn ending after reporting a limitation.
4. On resumption, a received integrated report whose visible body matches the
   original Task/target generation, shows `status=completed` with successful
   stages and actual Task done, and supplies the reporting facts without
   unresolved contradiction or action can be reported directly. No extra guide
   read, routine query or successful-operation replay is needed. Preserve
   Findings (including low/resolved), warnings, limitations and `not_recorded`
   facts; their presence alone does not require recovery. Labels, correlation,
   PASS or `reviews_ended` alone do not prove completion or unseen content.
   For other results, including incomplete, unknown, failed, conflicting or
   action-requiring reports, use
   [Continue after reviews](task_workflow.md#continue-after-reviews).
   The worker observes the matching incoming event and records receipt; no
   individual ACK, reservation management or `wait-ended` call is needed.

Other permitted work, such as read-only Task inspection, or a retained Task's
notification may advance this same parent's turn without cancelling existing
managed waits. The combined session slot still prevents starting a second Task
in that session while its current Task awaits review. Each wait remains bound to its original Task and
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

A failed call or `parent_may_end=false` does not confirm delegated waiting.
It does not require keeping the turn open indefinitely. Report the known state,
safe continuation, necessary action and condition for resuming together. Continue
safe authorized work when available; when recovery needs an external change or
user action, end the ordinary turn after this report. Do not promise automatic
resumption or mark the Task complete. Do not repeat the same failed operation
without a changed relevant condition or new evidence.

Normal service failures retain `ok/error` and provide a fixed `diagnostic`:
`stage/reason` identify the failure; `local_state` and `host_mutation` describe
this call; `retained_effects` describes admitted saved-state observations;
`cleanup` and `recovery` identify the applicable recovery boundary.
`turn_end=report_limitation` means the ordinary reporting branch above.
`host_mutation=not_dispatched` never proves earlier effects absent, and
`retained_effects=not_inspected` is not absence. Even a confirmed operation does
not establish review PASS, message receipt or Task completion. Unresolved
cleanup takes priority when choosing the next action without erasing the original
failure. A general host tool error is not proof of permission denial.

Use the following branch once, based on available evidence. Missing diagnostics
do not justify inventing a cause or inferring that effects were absent.

| Known condition | Safe continuation, necessary action and resume condition |
|---|---|
| Connection or tool unavailable | Use available public connection evidence. The unavailable service's own inspect is not a prerequisite. Report the unavailable operation; resume after the same reviewed service is available, without replaying uncertain effects. |
| Normal policy OFF | Respect OFF. Continue the retained review route when its prerequisites hold; do not enable settings or run Setup. |
| Configuration unreadable | Report configuration-read failure separately from OFF. Diagnose the admitted configuration responsibility without exposing values; repair requires its existing authority. |
| Runtime mismatch supported by evidence | Use the [upgrade procedure](#runtime-upgrade-and-reconnection). Reload only the intended reviewed runtime, then inspect retained effects; do not repeat successful Setup. |
| Project/root/binding or access admission failed | Use the returned bounded project reason. This is not proof of MCP disconnection. Do not auto-elevate, change ACLs, bypass admission or change binding. |
| Read failed | Keep retained effects unconfirmed where unreadable. Continue unrelated permitted work or report the concrete recovery prerequisite; retry only after relevant conditions or evidence change. |
| Creation, activation, deletion, pause, send or cleanup unresolved | Preserve the original wait and outcomes. No replay, replacement or competing cleanup; use only applicable authorized diagnosis. If uncertainty remains, report it and the resume condition, then end the ordinary turn. |
| Actual reviewers ended, with no conflicting unresolved effects | Use [Continue after reviews](task_workflow.md#continue-after-reviews) with the original route and retained command. |
| Confirmed wait readiness | Use [Normal wait](#normal-wait); the service owns waiting after the parent turn ends. |

Preserve the original Task/Packet, reviewer identities, originals and selected
continuation command. An unavailable policy, connection or required public
operation does not prevent other permitted review transport and does not
authorize enabling settings, another sender or a substitute supervisor.
For a prepared integrated intent, waiting OFF/unavailable permits the existing
workerless continuation after all reviewers end; it does not require manual
registration or a different commit route. Use
[Continue after reviews](task_workflow.md#continue-after-reviews) with the
retained command and outcomes. A failed wait or missing notification alone
does not establish that prior effects were absent; preserve known successes
and inspect unknown outcomes through the applicable recovery there. Missing
Git or send permission remains a boundary on every route.

Use `review_wait_inspect(task_id)` only for requested diagnosis or a failure,
and `review_wait_stop(task_id)` for explicit cancellation or known-state recovery.
They need no saved automation/probe IDs and add no normal completion gate.
Discover the applicable exact name in the same connected namespace when needed;
an already available complete definition needs no repeat lookup.
Inspection is read-only. Stop cannot recall a dispatched message or retry an
unknown effect. Restart is inspection-only; no active flag alone proves a live
worker. Changed Task basis or actual reviewer turns require recovery of the old
wait before a new association.
Inspect is conditional diagnosis, not a required extra call after every failure;
do not loop on it when the connection or service itself cannot answer. Runtime
identity diagnosis reads package sources only and never migrates state or starts
a worker. Keep the existing normal single wait call; add no routine doctor,
polling, status, ACK or automatic restart.

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
Connection, a tool call, Hook success, worker readiness and Task completion also
require their own evidence. The development JSON Hook relay is not a required
normal-wait recovery path and must not be re-enabled by ordinary Task work.
The worker uses bounded public observation. A truncated structured event may
establish notification correlation when the complete identity and host evidence
match; unknown reads or incomplete identity cannot. Correlation does not prove
unseen body content. Missing receipt requires no extra LLM operation or Task gate.
All original review/verification/usage/Task gates and failure recovery remain.
