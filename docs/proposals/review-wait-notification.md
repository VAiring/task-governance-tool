# Review Wait And Completion Notification Proposal

## Status, Purpose, And Authority

This design-only proposal specifies an instruction to use one blocking wait of
up to 600 seconds after dispatching independent reviews. Function-owned review
completion notification returns that wait early. The parent checks unfinished
work after expiry and, if healthy, calls the next wait. There is no separate
ten-minute notification timer, timer registration/rearm workflow, or required
end of the parent turn between waits.

[plan.md](../../plan.md#open-issues-and-deferred-candidates) routes this proposal.
The current [review handoff](../review-completion-specification.md#caller-owned-review-handoff),
[review result registration](../review-completion-specification.md#structured-review-results),
[reviewer binding](../review-completion-specification.md#reviewer-session-binding),
[Task ownership](../task-operation-specification.md#session-ownership-and-recovery)
and [privacy](../specification.md#privacy-safety-and-stable-errors) remain
controlling. Implementation boundaries remain in
[review completion design](../review-completion-design.md#structured-result-registration).
Under the [design-first rule](../task-operation-specification.md#review-tier-and-design-first-rules),
this proposal is not an implemented feature, active Skill instruction or
implementation Task. It authorizes no host setup, trust change, service start,
live chat message or long-running experiment.

## User-Visible Sequence And Proposed Instruction

The future caller guidance is:

> After dispatching the required reviews, use the supported review wait once
> with a maximum wait of 600 seconds. Keep the parent turn pending while the
> function waits; do not repeatedly ask the LLM to check progress. Function-owned
> completion notification should return this same wait as soon as all reviews
> end, regardless of their verdicts. If the wait expires with work unfinished,
> inspect the returned status once. If healthy, call the next maximum-600-second
> wait; if a problem needs action, handle it before waiting again. Consume and
> register actual reviewer originals through the existing handoff workflow.

This instruction applies only where the selected host supports the duration
and early return under its active operating rules. Respect the tool's timeout
unit: 600 seconds is 600,000 milliseconds. This proposal invents no public wait
command and does not require a particular host/tool name.

Ten minutes is a maximum interval before checking unfinished work, not a forced
reviewer termination deadline or a reason to reject a slow review. Waiting
runs without model calls. There is no periodic chat message just to announce
progress or model-visible short polling loop hidden inside this instruction.

The complete intended review membership comes from actual dispatch results,
including failed launches as explicit outcomes. Unknown launches require
reconciliation, not blind relaunch. Observation covers completion before the
first wait and between waits. A repaired/new review round has its own current
basis; an older notification cannot complete it.

## Responsibility And Result Boundaries

| Component | Responsibility |
|---|---|
| Parent LLM | Dispatch authorized reviews, call the bounded wait, assess expiry or an actual issue, rewait when appropriate, and handle originals. |
| Host wait/notification capability | Observe the intended reviewers, wait without model generation, deliver correlated completion, return the matching wait early, and avoid duplicate effective wakeups. |
| Existing handoff helper | Prepare/read the Packet, save validated originals and bindings, and submit unchanged originals through the public CLI. |
| Taskgov core | Own Task state, ownership, verification, review registration, evidence freshness and completion gates. |

The current helper implements transport, not waiting, reviewer launching or
host notifications. Prefer an existing host wait/notification facility when it
meets this contract. Do not build a scheduler or permanent service merely to
express a 600-second wait. Exact integration choices need later implementation
authority; this design adds no persistence schema or normal-loop setup step.

Keep these dimensions distinct: lifecycle (running, ended, execution-failed,
cancelled or unknown); original availability/validation; actual verdict; and
registration/gate outcome through the public CLI. Silence is not termination,
a saved file is not registration, and a notification is not PASS.

All-ended includes execution failure and cancellation. Once every intended
reviewer is terminal, notify and return even if an original is missing or
invalid. Expose that defect for transport repair or a real review; never invent
PASS or keep waiting for a terminated reviewer. An actual problem may return
early for attention. A host returning an individual reviewer outcome early
may let the parent consume that real outcome and wait for the remainder;
ordinary commentary should not cause repeated model-visible checks.

## Notification And Wait Outcomes

Associate waiting with the existing project, Task, Contract revision, complete
review target, execution/owner generation, parent host/chat and actual reviewer
handles. Reuse host correlation instead of asking the LLM to enter session IDs.
Notifications contain structured lifecycle/result availability and original
references, plus enough event identity to recognize duplicates. They copy no
chat/review bodies, reasoning, logs or secrets, and grant no approval/ownership.

For a parent waiting on this set, function-owned completion notification and
the returned wait result are one logical outcome. Use the host's supported
tool/event path, without additionally starting another parent turn for the same
completion. A wait timeout is a tool result, not a timer-generated chat message.

| Outcome or race | Required handling |
|---|---|
| All-ended before waiting starts | Return completion immediately; no lost notification window. |
| All-ended during waiting | Notify and return the matching wait early, with actual result availability and verdicts when known. |
| Maximum wait expires | Reconcile membership first; return completion if all-ended, otherwise one unfinished status for the parent's check. |
| Healthy unfinished work | Call a new maximum-600-second wait; no timer-registration acknowledgement or mandatory turn-ending round trip. |
| Problem found | Handle it before another wait; reviewer outcomes remain available through the host result path. |
| Completion races expiry or is duplicated | Coalesce where possible; otherwise recognize the same set/event and consume it once. Never register originals twice or rewait after all-ended is known. |
| User input interrupts waiting | Honor the input, retain available results and reconcile before later waiting; no automatic continuation contrary to the user. |
| Cancellation or obsolete basis | Stop only this wait/automatic continuation, preserve reviewers and evidence, and ignore obsolete notifications for current-state writes. |

Cancelling a wait does not terminate reviewers, cancel a Task, erase results or
affect unrelated waits. Those operations retain their existing authorization.
Waiting and early return never pause a Task or transfer ownership.

The parent may keep A in review_pending, do B, then complete A. A's notification
always names A and its basis. While the parent works on B, use only supported
host delivery/queuing; do not start a concurrent parent turn, interrupt B through
a fabricated user message, or act on the default current Task. Existing public
freshness/ownership guards remain controlling.

## Interruption, Recovery, And Unsupported Hosts

Use the wait facility's elapsed-time timeout; a return ends that call. There
are no persisted ten-minute deadlines, overdue notification backlogs or timer
rearm state here. After disconnection, restart or a lost wait result, reconcile
once through existing host review handles/results before consuming completion
or waiting again. Do not assume the old call survived, restart reviewers or
replay registration. Unknown lifecycle/delivery remains unknown until resolved.

Host acceptance does not prove parent handling. Reuse existing event identity
where available. If delivery is uncertain, inspect existing status/results
instead of blindly sending another turn. Require idempotent handling, not
exactly-once delivery across independent systems.

Check both capability and active host policy. Accepting a 600,000 argument does
not prove a call remains blocked for that long. Chat delivery does not prove
an outstanding wait is released. An arbitrary shell sleep, short tool yields
followed by repeated LLM polling, or bypassing a shorter host blocking limit
is not this mode.

If the host cannot provide the bounded wait and completion-driven early return,
state the limitation and use the existing authorized review workflow. Do not
claim support, silently restore periodic timers, or add waiting/notification
availability as a completion gate. Ordinary review, unrelated work and valid
completion remain available under current rules. Numerical collection failure
also does not affect those rules.

## Host Qualification And Cost Rationale

The presently exposed collaboration.wait_agent accepts a duration including
600,000 ms, but can return for any live-agent mailbox update, not just this
review set's completion. wait_threads describes return on the first completion
or attention event and cursor-based snapshots. Neither observation establishes
an integrated all-review-ended notification and single 600-second wait for the
current Desktop. This session's shorter blocking-call policy must also be
respected; the proposal does not override it.

Qualify the selected host with an authorized isolated test: effective duration
and unit, matching completion early return, partial/attention events, user
interruption, expiry/completion race and duplicates. Use virtual time/fake
delivery for deterministic behavior and separately authorized host tests for
actual integration. No real ten-minute wait or chat delivery was verified by
this revision. An idle-chat wakeup API, daemon surviving a finished parent turn,
or new service is not a prerequisite of the blocking-wait approach.

The token rationale is fewer unnecessary model resumptions. Tool results still
lead to another model request within the same visible turn, as the
[official tool-call flow](https://developers.openai.com/api/docs/guides/function-calling#the-tool-calling-flow)
describes. A genuine single wait and a function-owned timer both wait without
generating model output; the visible turn boundary alone establishes no saving.
Avoiding timer rearm/acknowledgement/end-turn round trips may help, but actual
savings are unmeasured. Context size, request count, output and
[cache reuse](https://developers.openai.com/api/docs/guides/prompt-caching)
matter. An open turn is not a cache-retention guarantee. A token benchmark is
not a design or ordinary Task completion gate.

## Verification And Activation Boundary

Later implementation checks cover early all-PASS and mixed-verdict completion;
failed launches/terminal jobs with missing originals; unfinished at 599/600
seconds; healthy rewait and problem handling; completion before subscription
and during expiry; duplicate/obsolete events; interruption/recovery; A's
completion while doing B; and unsupported hosts. Check normal success as well
as failures, using fake lifecycle events and virtual time without real Task DB
mutation or a ten-minute sleep per scenario.

This design revision requires the document-contract checker, consistency with
linked owners and two independent specification reviews. These checks do not
establish runtime support. Activation needs a later explicit implementation
decision and coherent updates to applicable product, design and Skill owners;
this document does not activate the instruction or change current workflow.
