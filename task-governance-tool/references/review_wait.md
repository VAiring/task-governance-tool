# Review Wait And Same-Parent Resumption

For authorized independent reviews across parent turns only. The optional
service owns waiting; the parent keeps judgment and Task decisions. Integrated
preparation delegates fixed registration/commit/completion, manual preparation
does not. Non-review waits are unchanged; no resident coordinating LLM is needed.

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

Reuse the complete available `review_wait_wait` definition. Only a missing,
incomplete or changed definition, or recovery needing another control, calls for
discovery in the connected review-wait MCP namespace. Use its actual returned
name and schema; no routine lookup or confirmation is needed.

1. Dispatch the unchanged independent-review requests through the authorized
   subagent facility. Retain the original Task/Packet, actual returned handles,
   original-result paths and selected continuation command. Never duplicate a dispatch
   whose outcome is unknown.
2. Call `review_wait_wait(task_id, reviewer_ids)` once with that original Task
   and the actual returned `/root/...` handles (canonical UUIDs also work).
   Keep prepared slot order and bind before any extra message to the reviewers.
   Use the returned handles directly; do not ask reviewers for UUIDs or manually
   transcribe them for this normal wait.
   Supply no automation/probe IDs, prompt, schedule or destination.
3. Delegate waiting and end this turn on
   `ok=true,status=waiting,parent_may_end=true` only.
   The service owns waiting and same-parent notification. Readiness is not Task
   completion or successful answer collection. Preserve any
   `answer_collection.unavailable` failure: `binding_mismatch` with reason
   `initial_binding_interacted` means the host observed additional contact before
   initial binding. Other binding failures do not establish that cause.
   Failed calls use the linked failure procedure for ordinary turn
   ending after reporting a limitation.
4. Report a received integrated body directly when it matches the original
   Task/target generation, has `status=completed`, successful stages, actual
   Task done and sufficient reporting facts, without unresolved contradiction
   or action. No extra guide read, routine query or replay is needed. Disclose
   Findings (also low/resolved), warnings, limitations and `not_recorded`; these
   alone do not exclude success. Labels, correlation, PASS or `reviews_ended`
   cannot establish completion or unseen content. For all other results use
   [Continue after reviews](task_workflow.md#continue-after-reviews).
   No individual ACK, reservation management or `wait-ended` call is needed.

Other permitted work or retained notifications do not cancel a managed wait;
the session slot still prevents starting a second Task while the current Task
awaits review. For overlapping retained notifications, see
[retained wait notifications](#retained-wait-notifications).

At a scheduled ten-minute check, handle actual review results normally. If the
same reviewers are healthy but unfinished, repeat only the same wait request.
`status=reviews_ended,parent_may_end=false` means continue result processing.
Do not redispatch reviewers merely to wait again, choose reservation phases or
manually create another heartbeat.

Exceptional same-basis supplementation/reassessment uses the
[explicit continuation](task_workflow.md#same-basis-review-continuation), then
waits only for the returned selected actual reviewers. Retained qualified
judgments do not depend on their old chat turns. Follow its pre-send arm
instruction for newly issued version-two collected reviews. Their dedicated
reviewer may advance to a later answer while tracking remains current; waiting
finishes for that reviewer only when the first valid completed answer is frozen.
An invalid-format response is not a completed review result. Older exact-turn
occurrences retain their unexpected-turn invalidation. A failure notice reports an
invalidated wait and a safe next action, never PASS or Task completion. Its
continuation/occurrence identity belongs to that outcome; an old notice or
receiving turn cannot acknowledge the new one. Unknown send/cleanup prevents
replacement and resend.

## Retained Wait Notifications

Other permitted work, such as read-only Task inspection, or a retained Task's
notification may advance this same parent's turn without cancelling existing
managed waits. The combined session slot still prevents starting a second Task
in that session while its current Task awaits review. Each wait remains bound
to its original Task and reviewers; the service defers while the parent is busy
and coordinates its sends. It finds the first actual receiving turn through
bounded public history even if newer turns already exist. No parent-side history
correlation or extra call is needed. Apply the [normal resumption step](#normal-wait)
to the original Task's actual report; a notification is not completion evidence.

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
Old-wait reasons distinguish `review_continuation_effect_unknown` (timer/send
effects unconfirmed), `review_continuation_worker_exit_unknown` (actual worker
exit unconfirmed), and `review_continuation_cleanup_required` (known request
still needs cleanup). Preserve these distinctions and any previously confirmed
operation; a later failed proof is not settled. None authorizes replay or a new
wait. Diagnosing the reason does not establish operational recovery.
When supplied, `diagnostic.host_failure` names the fixed host operation, its
communication reason and its own helper-process termination result. Report
these separately: confirmed termination does not repair failed communication,
and unresolved termination does not erase the original failure. These facts
do not establish notification-worker exit or any mutation/receipt outcome.
When present, `read_failure` identifies parent state, reviewer identity or
notification receipt, the count of complete validated pages, and a fixed next
action. An unread page or incomplete JSON never establishes the missing
conclusion; a failed fresh parent read supplies no current idle evidence.
Frame/total/queue overflow and partial EOF remain distinct from cleanup failure.
Restore the failed prerequisite before repeating the same read. Receipt failure
preserves known registration, commit and Task completion: do not resend, reopen
or complete again. Requested inspection may expose the current worker's first
parent-state or receipt-observation failure as transient `read_diagnostic`;
absence after restart does not recover its cause. A semantic response rejection
retains observed communication/termination facts; it never invents missing ones.
That projection requires the exact observed attempt, binding, probe and owned
worker. Unknown or mismatched observation suppresses it; a correctly bound
stopped worker may retain its transient diagnosis.

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

Use `review_wait_inspect(task_id)` for requested diagnosis or a failure. A
[generated collected-review request](task_workflow.md#collected-review-answers)
also includes one best-effort call in that reviewer's own context before review;
it adds no parent preflight, retry or stop on unavailable connection preparation.
Use `review_wait_stop(task_id)` for explicit cancellation or known-state recovery.
They need no saved automation/probe IDs and add no normal completion gate.
Discover the applicable exact name in the same connected namespace when needed;
an already available complete definition needs no repeat lookup.
Inspection is read-only. Stop cannot recall a dispatched message or retry an
unknown effect. Restart is inspection-only; no active flag alone proves a live
worker. On a fresh target generation, normal waiting internally closes an exact
older same-project/binding/parent/Task wait only when worker exit and timer/send
settlement are proven. A missing receipt alone does not block it or permit a
resend. No extra inspect/stop is needed for that settled case. Other changed
bases or reviewer turns, missing exit proof and unknown effects retain recovery
of the old wait before a new association.
Inspect/stop and return notifications share a timestamped observation of the
original Task/attempt/probe/worker. Keep request phase, worker
`running|stopped|unknown`, timer/send/receipt, runtime and known work separate.
Only an exact live owned thread proves running; actual joined exit or matching
saved joined-exit evidence proves stopped, including after restart. Missing,
old, unreadable or conflicting evidence stays unknown; cleanup success, timer
deletion and Task done do not prove exit. Stop success survives later observation
failure; unknown alone needs no re-stop. Report direct versus saved confirmation,
persistence failure and observed scope; other children and verification process
exit stay unknown. Old notifications are send-time snapshots, not current state.
Only notification text explains remaining receipt observation: accepted/unknown
send, at most 120 seconds within the original twenty-minute deadline, ending on
confirmation or existing observation/stop/deadline conditions. This does not
confirm future exit or its own send/receipt. No routine extra inspect/ACK/stop,
exit notice or LLM turn is needed.
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
