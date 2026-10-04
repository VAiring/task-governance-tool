# Review Wait And Parent Chat Notification Proposal

## Status, Purpose, And Authority

This is the design-only result for the user-requested review-wait change. It
specifies a proposed optional way to wait after dispatching independent reviews:
the parent ends its turn, a deterministic host-side controller waits, and a chat
notification wakes the parent only when results or a ten-minute check need
attention. It is not an implemented feature, an active Skill instruction, an
implementation roadmap, or permission to start services or send messages.

[plan.md](../../plan.md#open-issues-and-deferred-candidates) routes this proposal.
The current [review handoff](../review-completion-specification.md#caller-owned-review-handoff),
[review result registration](../review-completion-specification.md#structured-review-results),
[reviewer binding](../review-completion-specification.md#reviewer-session-binding),
[Task ownership](../task-operation-specification.md#session-ownership-and-recovery)
and [privacy](../specification.md#privacy-safety-and-stable-errors) remain
controlling. Their implementation boundaries remain in
[review completion design](../review-completion-design.md#structured-result-registration).
The [design-first rule](../task-operation-specification.md#review-tier-and-design-first-rules)
requires a later explicit taskization/implementation decision; this proposal
does not create or start implementation Tasks.

## User-Visible Sequence

1. The parent prepares the current Packet and dispatches the intended reviewers
   through its existing authorized host. One registration of the review set
   associates those jobs with the parent chat and arms a ten-minute timer.
   Timer setup and job registration are function-owned parts of that dispatch,
   not new instructions for the LLM to construct a timer or repeatedly poll.
2. Once the controller acknowledges that it owns observation and notification,
   the parent ends its turn. No LLM remains running just to wait. If this
   handoff fails, report that automatic waiting is unavailable; the existing
   review workflow remains usable and dispatched reviewers need not be stopped.
3. If reviews are still running after ten minutes, the controller sends one
   check-due notification to the same parent chat and disarms that timer. The
   awakened parent inspects the supplied status and obtains more detail only
   if needed. If work is healthy, it explicitly continues waiting; the function
   arms one new ten-minute timer and the parent ends its turn again. If there
   is a problem, it keeps the timer stopped while handling that problem.
4. As soon as all members of the review set have ended, regardless of PASS or
   changes-requested verdicts, the controller removes all timers belonging to
   that set and sends a completion notification. It does not wait for the next
   ten-minute boundary. The parent reads and registers the original results
   through the existing handoff and handles every Finding under current rules.

Ten minutes is a check interval, never a reviewer timeout or a forced failure.
A slow but observable reviewer can continue through any number of explicitly
continued intervals. A missing response from the parent never automatically
rearms a timer or starts a stream of reminders.

## Responsibility Boundary

| Component | Responsibility |
|---|---|
| Parent LLM | Dispatch authorized independent reviews, end its turn, decide whether a check needs intervention or continued waiting, consume originals and handle Findings. |
| Host adapter | Identify the exact destination chat and reviewer jobs from structured dispatch results; receive lifecycle status; deliver a notification into that same chat with documented busy/idle behavior. |
| Wait controller | Own one review set, timer state, deduplicated events and a minimal pending-notification record; operate without model calls during waiting. |
| Existing handoff helper | Prepare/read the Packet, save validated original results and binding sidecars, and submit their unchanged bytes through the public CLI. |
| Taskgov core | Continue to own Task state, ownership, Receipt registration, evidence freshness, review and completion gates. |

The controller does not judge independence, reinterpret Findings, submit
Receipts, mark Tasks done, acquire Task ownership, or launch a replacement
reviewer. It observes lifecycle metadata instead of reading chat transcripts.
The current helper has no scheduler, reviewer launcher or host adapter. A
future orchestration layer will compose with that helper rather than turning
file existence or a chat acknowledgement into a replacement gate.

The parent can keep A in `review_pending`, execute B, and later complete A.
A notification names A explicitly and never uses a default current-Task
selection that could instead modify B. A parent turn ending is not a Task
pause or ownership transfer. Existing ownership recovery remains explicit;
neither the controller nor an adapter may impersonate the owner by changing
environment identifiers.

## Review Set And Observation Model

Registration freezes the intended member set from this dispatch. The adapter
must capture actual job identifiers, including a failed launch as an explicit
member outcome; an unknown launch outcome is reconciled without blind relaunch.
Registration and initial status reconciliation must cover jobs that finish
before event subscription. No reviewer may be silently omitted to make a set
complete. Additional reviews after repairs form a new set with its own basis.

The controller retains only the coordination data needed for this set:

- a unique wait-set ID and the caller's idempotency key for dispatch adoption;
- project ID, Task ID, Contract revision, full review-target tuple and execution
  ID, plus the observed owner generation;
- destination host and parent-chat identifiers, and exact reviewer-job handles;
- the existing Packet/result references and structured member states;
- controller revision, timer generation/deadline, notification IDs and delivery
  states, and the structured reason for stopping or reconciliation.

These are proposed internal concepts, not new public CLI or JSON fields. A
later implementation must define its exact schema and explicit local write
location before activation. Coordination storage is separate from core Task
evidence and optional token collection. Use one explicitly owned, ignored local
area and atomic updates; do not create a second authority, secret store, host
configuration, or copy of review/chat content.

Track the following dimensions separately:

| Dimension | Meaning |
|---|---|
| Job lifecycle | Running, ended, execution-failed, cancelled, or unknown. Host loss/silence is unknown, not proof of termination. |
| Original result | Not yet saved, saved and validated by the existing helper, missing after termination, invalid, or uncertain. |
| Verdict | The actual verdict from a validated original, or unavailable. Process exit and helper exit do not imply PASS. |
| Registration and gate | Observed only from existing public Taskgov results. Saved originals, ended jobs and delivered notifications do not establish either. |

`all ended` means every registered job is known to have reached a terminal
lifecycle state. It includes execution failure and cancellation as abnormal
outcomes. A missing/invalid result does not keep the wait timer alive after
termination: notify that the set ended **with missing or invalid results**, so
the parent can repair the transport or arrange an actual review. Keep all
originals and uncertain residue under current handoff rules.

## Timer And Event State Machine

Each set has one serialized state owner. Use compare-and-set or equivalent
local locking so concurrent completion, timer and continuation events cannot
create extra active timers. Timer generations make delayed callbacks inert.

| State / event | Required transition and effect |
|---|---|
| New registration | Reconcile known member states; if all ended, queue completion directly, otherwise arm deadline `now + 600 seconds`. |
| Waiting / member update | Update that member idempotently. If all ended, remove this set's timers and queue one completion event. Otherwise retain the current deadline. |
| Waiting / current deadline due | Reconcile current member states first. If all ended, take the completion path; otherwise disarm, enter `check_due`, and queue one check event. |
| Check due / parent continues | Reconcile basis and members. If still applicable and unfinished, advance timer generation and arm `now + 600 seconds`. A repeated continuation with the same operation ID returns the existing arm. |
| Check due / problem found | Enter `attention`, with no timer. Keep lifecycle observation so later all-ended results can still notify the parent. |
| Attention / problem resolved and parent continues | Reconcile the same basis and members, then arm once, or finish immediately if already all ended. |
| Any live wait / explicit cancellation or obsolete basis | Remove only this set's timers, revoke unsent events and stop automatic continuation for it. Preserve originals and evidence. |
| All ended / late timer or continuation | Ignore the obsolete event; never create another wait interval. |

An explicit cancellation of waiting does not terminate reviewer processes,
delete their results, cancel unrelated automations, or change Task status.
Those actions retain their own existing authorization. A stopped or obsolete
set cannot be reactivated by a stale notification. An explicitly requested new
wait is registered with fresh identity and current basis.

Within one live process, deadlines use monotonic elapsed time. Persist a UTC
deadline for recovery; after sleep/restart, reconcile once and emit at most one
overdue check, never one notification per missed interval. Uncertain clock or
job status requires reconciliation, not an assumed healthy automatic rearm.

## Notification, Race, And Recovery Semantics

Notifications contain a fixed event kind (`check_due`, `all_ended`, or
`attention_required`), event ID, wait-set/basis identifiers, per-member lifecycle
and result availability, and references to the original results. A verdict is
included only from a validated original. Full Findings are consumed through the
existing result path rather than recopied into a notification. No raw output,
private review body, reasoning, credentials or conversation text is retained.

An idle destination starts a new turn in the same chat. If that parent is
already active, the adapter must enqueue for that chat using a verified host
mechanism; it must not start a concurrent parent turn, interrupt unrelated B,
switch chats, or synthesize user approval. The notification identifies itself
as a tool/controller event. The LLM checks its supplied basis before acting;
the existing public write guards remain the final ownership/freshness checks.

Completion supersedes an unsent check-due event. If the check was already
accepted by the host when completion arrives, a separate completion event is
legitimate: on handling the older check, the parent reconciles and finds no need
to rearm. This design promises idempotent effects, not impossible exactly-once
delivery across two systems without a shared transaction.

The pending-notification record distinguishes pending, host-accepted, and
delivery-unknown. Host acceptance is not parent handling or Receipt
registration. Before retrying an unknown send, the adapter uses a documented
idempotency/status mechanism when available. If the host cannot resolve the
outcome, retain it as unknown and surface recovery through the local controller
status; do not blindly send new turns. A definitely rejected send may be retried
after the connection is restored using the same event ID. The parent ignores
duplicate event IDs and obsolete timer generations.

On controller restart, reacquire a single local owner, load the minimal set
record, reconnect observation and reconcile membership, current public Task
basis, and pending delivery before choosing any action. Known all-ended jobs
produce completion; an expired unfinished interval produces one check; a
future deadline retains its remaining time. Unknown jobs or ownership/target
changes suspend automatic continuation and expose a sanitized recovery reason.
They do not automatically pause/block the Task or erase valid results.

Monitor unavailability, delivery failure, missing host access, and token
collection failure are not additional review or completion gates. The existing
manual review handoff remains available. A healthy fallback needs no proof
that unrelated timers, chats or Tasks are correct.

## Host Connection Feasibility And Remaining Boundary

Official [Codex App Server documentation](https://learn.chatgpt.com/docs/app-server#start-a-turn)
describes `turn/start` for a specified `threadId`. Its tool-output form can
resume an idle thread and queue output into an active turn. This establishes
the protocol capability; it does not establish a connection from an arbitrary
local process to the user's already-running Desktop chat.
The documented connection handshake is `initialize` followed by `initialized`;
loading a thread through `thread/resume` is separate from read-only inspection.
The [background Hook contract](https://learn.chatgpt.com/docs/hooks#how-background-hooks-run)
does not start a new idle turn just because a background Hook finishes, so a
Hook alone does not provide this wake-up mechanism.

The read-only local probe used CLI `0.157.1`. Its `app-server proxy` help
describes stdio forwarding to a running App Server control socket and accepts
`--sock`. The host-side `app-server daemon version` probe failed with connection
refused (OS 10061) at the default control socket. The ordinary sandbox probe
instead used its isolated home and failed with OS 10050; that sandbox result is
not evidence about Desktop's endpoint. No daemon was started, configuration or
trust changed, or test message sent.
The current official documentation is not a verified capability declaration
for this installed CLI or Desktop backend. In particular, `toolOutput` and
active-turn queue compatibility must be checked on the selected runtime before
the adapter advertises them. The docs establish no `turn/start` idempotency-key
guarantee; the delivery-unknown branch above therefore remains necessary.

The host-provided `send_message_to_thread` tool is a potential host adapter
surface when the host exposes it to an authorized controller. Its availability
inside an LLM turn does not prove an independent Python process can call it or
survive that turn. Starting a separate App Server also does not prove it is the
runtime managing the existing Desktop chat. A thread ID alone is insufficient
to bridge these boundaries.

The selected architecture therefore uses a host adapter with explicit
capabilities: same-runtime destination resolution, lifecycle observation that
survives the parent turn, safe busy/idle delivery, and recoverable delivery
outcomes. Prefer an existing host-owned service when it provides these; do not
add a second service solely because a CLI command exists. If no supported
connection is exposed, automatic waiting remains unavailable while ordinary
review continues. This is an unverified host prerequisite, not a conclusion
that chat-triggered continuation is impossible.

Before claiming support for the current Desktop, an authorized isolated
end-to-end check must demonstrate: a parent ends its turn, reviewers continue,
the controller survives, and both an overdue check and an all-ended event reach
that exact chat and resume it with correct busy/idle handling. Use a designated
test chat and explicit send/service authority; do not test on unrelated work.
This host qualification applies to providing the feature, not completing
ordinary Tasks or this design responsibility. No actual delivery was verified
in this design investigation.

## Verification Scenarios For Implementation

Use a virtual clock, fake reviewer lifecycle source, fake notifier and isolated
local records. These tests require neither a ten-minute real sleep nor model
calls, network access, real Task DB mutation or token A/B runs. Observe timer
count/deadline, event identity, per-member/result state and side effects.

| Scenario | Required observable result |
|---|---|
| Two PASS originals, jobs end before 600 s | One all-ended event; no remaining timer; originals remain unchanged; no automatic registration/completion. |
| PASS and changes requested | Same timely all-ended path, actual distinct verdicts preserved, all Findings available through originals. |
| Job fails or ends without a valid original | All-ended notification explicitly exposes failure/missing/invalid result; no fabricated PASS. |
| Unfinished at 599 s, then 600 s | No early notification; one check at deadline; no armed timer until parent continues. |
| Healthy check and continuation | One new deadline 600 s after continuation, parent can end turn; duplicate continuation does not postpone it again. |
| Problem at check | No rearm; later terminal member events still yield completion unless waiting was explicitly cancelled. |
| Completion races deadline or continuation | Completion wins before send; an already accepted check may precede completion; late handlers cannot rearm. |
| Parent still active or executing B | Event is queued for the same chat and names A; no concurrent turn, B mutation or ownership takeover. |
| Completion before subscription / partial launch | Reconciliation accounts for every intended job, including launch failures; none is lost or silently omitted. |
| Duplicate dispatch/event / two controllers | One set for one adoption key and at most one timer owner; no duplicate effective continuation. |
| Cancel set A while set B waits | Only A timers and unsent events removed; no reviewer termination, result deletion or B timer change. |
| Restart before/after deadline, or after all ended | Reconcile once; retain remaining interval or one due check/completion; no backlog of reminders. |
| Crash around notification acceptance | Unknown delivery remains distinct; no blind turn replay; supported dedup/status recovery uses the original event ID. |
| Stale target, changed owner, unknown job or unavailable host | Suspend only automatic continuation, expose reason, retain originals; current ordinary review/completion rules remain usable. |

For this design Task, verification is the current document-contract checker,
consistency against the linked owners, source-backed connection assessment and
two independent specification reviews. The table is an executable-test plan for
later implementation, not a claim that a monitor or Desktop integration has
already passed those tests. Activation must update the applicable product,
design, privacy and Skill owners together; until then their current contracts
remain unchanged.
