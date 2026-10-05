# Review Wait And Parent Resumption

## Purpose And Authority

This approved bounded replacement ends the parent turn while independent
reviews continue. A ten-minute scheduled check resumes the same parent. If all
reviews end while at least 90 seconds remain until the confirmed check, its
single timer is shortened to a one-minute interval. The parent stops the timer
on resumption and handles the actual results. One minute is a schedule setting,
not a delivery-time guarantee. This accepted alternative supersedes the earlier
requirement to stop the timer and independently notify the idle parent.

Durable behavior belongs to
[host-owned review waiting](../review-completion-specification.md#host-owned-review-waiting),
structure to the corresponding
[design](../review-completion-design.md#host-owned-review-waiting), and agent
execution to the package's
[review workflow](../../task-governance-tool/references/task_workflow.md#wait-across-parent-turns).
[plan.md](../../plan.md#review-completion-state-and-viewer) routes this bounded
acceptance owner. Live Task state and verification evidence belong only in the
public CLI. An edited instruction is not evidence that its host integration works.

## Approved Scope And Execution

Implement and verify one coherent replacement across the host procedure, Skill
reference, relevant review and instruction-layer owners, and package metadata.
Use existing public host scheduling and subagent facilities; do not add a
taskgov command, schema, daemon, private scheduler database dependency, host
trust change, or shared setting. Non-review waiting remains unchanged.

The parent prepares the existing independent review requests and retains their
Packet, original paths and registration command. One executing supervisor
dispatches those requests unchanged, retains the actual returned handles and
observes completion. Reuse an existing suitable supervisor where available.
Otherwise a bounded host subagent is necessary because ordinary child completion
does not execute the all-ended decision in an idle parent. This adds coordination
work; it is not an extra independent review or a promise of token savings.

The supervisor alone owns timer mutations through explicit stop acknowledgement.
It receives the parent's check, rearm and cancel controls through existing
subagent controls. Its session-local association retains the Task, Contract,
target, actual parent/supervisor/reviewer handles, timer identity and destination,
fixed prompt, arm, confirmed due time and shortening/closed state. These are not
new taskgov business identifiers or a persistent ledger. Review interpretation,
original transport, result registration and Task writes remain with their
existing owners.

1. Dispatch the authorized review set and arm one check ten minutes in the
   future. Retain an actual next-run value when exposed, or select and verify a
   supported fixed wall-clock first occurrence with its date, UTC instant and
   host zone. A timer's original update time does not identify its next run after
   execution or retry. Never infer an unknown due time. End the parent turn only
   after successful registration/readback and a still-future matching occurrence.
2. Observe native completion events within actual tool and execution limits.
   All-ended means every dispatched reviewer is terminal, regardless of OK, NG,
   failure or missing originals. Partial or intermediate messages do not qualify.
   At all-ended, compare the confirmed future due time with the current time.
   At least 90 seconds permits one ACTIVE update of that same timer to a
   one-minute interval; less than 90 seconds leaves it unchanged. Keep its ID,
   destination, name and prompt. Read back the result. Do not repeat shortening,
   activate a paused timer, or change a timer with a stale association/zone/basis.
3. On the matching parent wake, have that same supervisor pause and read back
   before acknowledging the check or terminal stop. Healthy unfinished work may
   receive one explicitly authorized next ten-minute arm. Require the current
   temporary check acknowledgement and no later all-ended/problem/cancel latch.
   Confirm the new arm before the parent ends again. All-ended, a handling
   problem or cancellation closes the association after PAUSED readback.

A fixed wall-clock rule must match a supported public schedule shape. Its first
occurrence must be unambiguous in the current host zone and remain future at
registration acknowledgement. Count one does not replace explicit cleanup:
the host may calculate another occurrence after dispatch. After the retained
occurrence arrives, do not reinterpret it as tomorrow or infer an internal retry.
A host lacking the needed schedule/readback or parent-wake capability remains an
explicit integration limitation, not a reason to claim pending-turn waiting
satisfies this replacement.

## Races, Recovery And Permission

Process received parent control before a new mutation. Serialize writes and
reconcile unknown responses before retrying. If cancellation arrives during
shortening, finish or reconcile that operation, then pause and confirm PAUSED
before acknowledging cancellation. Old arms, duplicate wakes or a stale healthy
decision cannot reopen a closed wait. For a healthy unshortened arm, a wake before
its confirmed due time cannot authorize a new ten-minute arm. Once all-ended is
latched, however, the first matching timer wake closes the wait even when
shortening makes it earlier than the original ten-minute occurrence.

Ordinary questions and status requests alone retain a healthy wait. Stopping a
timer does not itself cancel reviewers or change Task status. Preserve the
original association if another Task becomes active or ownership, Contract or
target changes. A wake establishes neither PASS, original delivery, registration,
ownership nor completion. Missing originals use existing transport recovery.

The supervisor stays available until it has returned a confirmed stop. If it or
an acknowledgement is lost, establish the actual writer and outstanding-operation
state before transferring timer ownership. Unknown cleanup remains unknown;
do not create competing writers, duplicate reviewers or blind update retries.

Keep authorization consistent across dispatch and scheduled instructions. No
handle overrides host permission review. A denial is evidence that an operation
failed, not proof that human authorization was absent or the feature impossible.
Do not switch sender, API or context to circumvent it. The accepted scheduled
alternative does not establish that an earlier direct-notification denial was
resolved. Arbitrary cross-chat messages, real-use configuration changes and
notification-turn usage accounting remain outside this Task.

## Verification And Completion

Confirm the concrete host path: reviewers continue after the parent turn ends,
the selected timer can resume that parent, the all-ended observer can shorten
the same ACTIVE timer, and the designated writer confirms PAUSED on resumption.
Exercise the ten-minute healthy check/rearm path and the one-minute alternative.
Do not turn fixture success into an unobserved real-host guarantee.

| Scenario | Required outcome |
|---|---|
| All ended with 90 seconds exactly or more remaining | Shorten the same ACTIVE timer once. |
| All ended with less than 90 seconds remaining | Preserve the nearer scheduled check. |
| Partial completion, intermediate message or ordinary question | Retain the wait without false completion or duplicate dispatch. |
| NG, failed review or missing original at all-ended | Apply the same timing decision; preserve actual-result/recovery gates. |
| Duplicate completion, old control or stale Contract/target/owner | No duplicate shortening, rearm, registration or completion. |
| Cancellation during shortening, or all-ended before a stale rearm | Serialized stop wins; a closed association stays closed. |
| Shortened wake before the original due time | Stop and close the all-ended wait; do not reject the early wake. |
| Expired, missing, ambiguous or changed-zone due basis | Do not guess a later occurrence or claim a verified threshold decision. |
| Healthy current check | Stop/read back, explicitly rearm once for ten minutes, confirm before ending. |
| Lost writer, unknown update or failed stop | Reconcile ownership and actual state; report unresolved cleanup honestly. |

Run the document-contract checker, applicable package/reference checks and
focused instruction/host verification. Require two independent Tier 2 reviews
without blocking findings and the existing evidence/commit/completion gates.
Performance A/B measurement and unrelated full-test repetition are not required.

This owner remains active until the bounded replacement is complete and its
durable behavior is synchronized into the existing owners, or the user
explicitly supersedes it. Retirement requires its separately authorized
documentation transition. It introduces no qualification exercise for ordinary
product Tasks.
