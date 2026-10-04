# Review Wait Integration Decision

## Status, Purpose, And Authority

The approved implementation scope is caller guidance using the existing host
subagent wait, not a new early-return or notification feature. This replaces the
earlier proposal for qualifying a separate wait/notification integration.
No isolated environment or real ten-minute experiment is a prerequisite for
this instruction-only change. It does not assert that an untested host can
actually remain blocked for ten minutes.

Current behavior belongs to
[host-owned review waiting](../review-completion-specification.md#host-owned-review-waiting);
structure belongs to the corresponding
[design](../review-completion-design.md#host-owned-review-waiting).
[plan.md](../../plan.md#review-completion-state-and-viewer) routes this bounded
decision. Task state and evidence remain in the public CLI, not this document.

## Accepted Integration Boundary

Use the exposed subagent wait, such as `collaboration.wait_agent`, with
`timeout_ms: 600000` only when its definition and current execution rules allow
that duration. Its existing mailbox/completion notification supplies early
return; Skill code does not implement it. Respect shorter limits and disclose
the permitted fallback. Do not generalize one tool's bound to another tool.

A mailbox update need not mean all reviewers ended: it may be a partial result,
a question, an unrelated agent message or user input; timeout also returns.
The parent uses the existing dispatch handles and review context, handles real
outcomes or problems, and waits again for healthy unfinished work. Existing
original retention, registration freshness and ownership rules remain controlling.
A notification never supplies PASS, an original or authority.

The change is limited to the workflow guidance, coupled current owners and
package integrity metadata. There is no new runtime API, timer, adapter, daemon,
persistent correlation/deadline store, hook, host configuration, manually entered
identity or normal Task-loop command. Stronger host delivery guarantees are not
a hidden implementation requirement or a condition of ordinary Task completion.

## Verification Boundary

Use existing document-contract, package/Skill and reference-retrieval checks,
plus representative semantic scenarios and two independent Tier 2 reviews.
Cover supported and shorter-limit hosts, intermediate/partial/terminal outcomes,
missing originals, timeout, user input, duplicate or obsolete results, recovery
and another Task being active. These are checks of caller decisions, not a
simulated runtime offered as a product.

A real timer implementation, virtual-time state machine, host-wide qualification,
new test chats and performance A/B are unnecessary for this instruction-only
scope. Existing authorized host observations may inform the limitation, but
neither a schema-accepted timeout nor an unrelated wait tool proves effective
ten-minute behavior. Token savings remain unmeasured.
