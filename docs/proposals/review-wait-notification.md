# Review Wait And Parent Resumption

## Purpose And Authority

The approved replacement ends the parent turn while independent reviews run.
Keep one ten-minute check during the wait. When every actual reviewer has ended,
delete that same reservation, confirm deletion, then invoke one public MCP send
to resume the same idle parent. This user-approved direction supersedes the
90-second threshold and one-minute shortening acceptance for the replacement.
It does not assert a delivery-time guarantee or activate the installed workflow.

Durable behavior belongs to
[host-owned review waiting](../review-completion-specification.md#host-owned-review-waiting)
and the [development direct-wake contract](../review-completion-specification.md#development-direct-review-wake);
structure belongs to [Review/completion design](../review-completion-design.md#host-owned-review-waiting).
[plan.md](../../plan.md#review-completion-state-and-viewer) routes this bounded
execution owner. Live Task state and evidence belong only in the public CLI.

## Approved Scope And Execution

This same Task replaces the resident coordination LLM with deterministic
observation and retained operation state. Actual dispatched reviewer/turn pairs,
the original Task, Contract, target, execution owner, parent and reservation
remain bound throughout. A hook or saved original alone is not a terminal
observation. Review interpretation, original transport, result registration,
usage evidence and Task completion retain their existing gates.

The user approves these ordered units and their persistence in this owner:

1. Update the Task Contract and coupled behavior/design owners. Extend the
   source-only controller, storage, public-host adapter and controls to arm one
   ten-minute check, observe actual reviews and the idle original parent,
   delete/confirm the reservation, then send once. Preserve old experiment
   records and the separately selected PAUSED-only probe.
2. Verify the focused normal, failure, interruption and restart paths, document
   contracts and coupled package artifacts. Obtain two independent Tier 2
   review passes with no blocking findings before real-host activation.
3. Use a fresh explicitly associated experiment reservation and scratch store.
   Observe the real parent-ended path, confirmed deletion, one send and a new
   genuine parent-turn acknowledgement. Distinguish host acceptance from receipt.
   A required host restart is an external boundary, not proof of success.

The user additionally approves persistence and execution of these production
integration units in the same Task:

4. Package the verified runtime behind the shared canonical state resolver.
   Admit fresh, individually bound waits without per-Task server configuration;
   preserve previous experiment records and reject replay or overlapping use
   of the same reservation. Update the coupled behavior/design owners. Verify
   physical package installation, state admission and multi-wait isolation.
5. Connect explicit Setup selection, review handoff and installed Skill guidance
   to that packaged service. Keep host configuration and trust explicit, retain
   offline operation when unavailable, and remove the resident coordinating
   LLM from the enabled workflow. Preserve actual parent/reviewer usage and
   legacy evidence. Verify Setup preview/idempotence/failure boundaries and
   representative handoff operation.
6. Run the coupled regression and document/package checks, obtain two
   independent Tier 2 reviews with no blocking findings, then verify the normal
   canonical route with a fresh real reservation and genuine new-turn receipt.
   A required host restart remains an external boundary. Update live evidence
   through the public CLI; preserve all existing completion gates.

All six units are sequential and Tier 2. Units 4-6 depend on the source
experiment; the installed procedure changes only with the implemented and
reviewed integration. These units authorize only the stated runtime, resolver,
Setup/configuration, handoff, Skill, tests and coupled governing-document scope,
not unrelated follow-up work, shared settings, host trust, or another project's
installation. No optional lane is introduced. A deterministic event must not
fabricate a supervisor usage turn. Earlier timer fixtures remain regression
evidence for existing controls, not acceptance of the deletion path.

## Races, Recovery And Permission

One serialized writer owns activation, deletion, cleanup and sending. Persist
each intent before its effect. Fresh identity admission and an exact public
delete receipt plus confirmed absence of the documented configuration are
required before sending. An error or missing view response is not deletion
confirmation. Retain deletion separately from the send result and receipt.

Check current basis, exact reviewer turns, original idle parent, cancellation
and deadline before deletion and again before sending. A later parent turn
suppresses the send. UI changes outside the local lease remain a host race;
do not claim atomic host compare-and-send. Failed/interrupted reviews qualify
as ended but do not satisfy review acceptance or original delivery.

Unknown activation, deletion, cleanup or send outcomes stay unknown. Never
automatically retry, recreate a deleted reservation, rearm this one-shot
association, change sender/transport, or resume its worker after restart.
Cancellation, expiry, stale basis and owning-session EOF stop further work;
known active reservations receive at most one pause attempt for cleanup.
Unknown pending mutations must not receive a competing pause. Explicit cleanup
can be attempted only with an admitted current writer and known outcome.

The ten-minute check remains a fallback while reviewers run. A new parent turn
stops this experiment and triggers bounded known-state cleanup; unfinished
review handling and a later explicitly prepared wait are separate operations.
It does not silently launch duplicate reviewers or extend the current record.
The deletion variant retains the fallback for a further ten-minute delivery
window after its due time, with no new deletion sequence after due. A sequence
already durably started before due can finish within that outer deadline.
The twenty-minute maximum prevents indefinite observation and repeated future
occurrences without cancelling the fallback at its nominal delivery time.

Use public host facilities and genuine executor context only. No private
scheduler database, trust change, shared setting, arbitrary cross-chat send,
permission bypass or notification-turn usage accounting is authorized here.
The user separately authorized this same-parent send and the test configuration;
host permission review still applies. Preserve a denial as evidence of failure.

## Verification And Completion

| Scenario | Required outcome |
|---|---|
| Exact all-ended set and original idle parent | Delete the same ACTIVE check, confirm absence, send once. |
| Partial review, intermediate message or active original parent | Keep the current check; no premature deletion or send. |
| NG, failed/interrupted review or missing original | Same wake decision; preserve actual-result/recovery gates. |
| Delete receipt malformed, rejected, lost or config still present | Retain unknown result; no send, retry or competing mutation. |
| Cancellation or changed basis/parent after deletion | Preserve deleted fact; suppress send. |
| Cancellation, deadline, failure or EOF before deletion | Stop worker; pause once only when the ACTIVE outcome is known. |
| Duplicate start, restart or unsettled intent | Inspect only; no replay, resurrection or second send. |
| Successful send without new-turn acknowledgement | Accepted only; receipt remains unverified. |
| New genuine same-parent acknowledgement | Preserve receipt even if send settlement was unknown. |

Run the document-contract checker and focused offline tests without real host
effects, then the bounded authorized real-host experiment. Require two
independent Tier 2 reviews and the existing evidence/commit/completion gates.
Fixture success alone does not establish host resumption. Performance A/B
measurement and unrelated full-test repetition are not required.

This owner remains active until the replacement is complete and its durable
behavior is synchronized, or the user explicitly supersedes it. Retirement
requires its separately authorized documentation transition.
