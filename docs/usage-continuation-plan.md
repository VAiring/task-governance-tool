# Usage Continuation Execution Plan

This is the approved execution boundary for the existing Usage continuation
Task. Live progress and evidence belong only in the public CLI. Durable
behavior remains owned by the lifecycle sections in
[Setup specification](setup-state-specification.md#optional-lifecycle-usage-collection)
and [Setup design](setup-state-design.md#lifecycle-usage-hook), and by
[numerical persistence](database-specification.md#numerical-usage-store) and
[reconciliation tracking](database-design.md#usage-reconciliation-tracking).
The design below is conditional until the implementation, verification,
independent reviews and current-owner synchronization pass together.

## Outcome And Boundary

For finite stable obtainable input, progress-capable existing work units and
an available execution service, one admitted Hook must leave responsibility
for known remaining work with that service. No later Hook, user command or LLM
turn is needed. Hook return and numerical catch-up are separate facts.

Reuse the already configured project MCP service's start/close lifetime, with
a separate Usage execution owner and project-scoped OS lease. Do not reuse the
backup observer's lossy request consumption or review-wait ownership. The
service must retain/recover unacknowledged work across process termination.
Existing numerical cursors, dirty queues and publication positions remain the
source of committed work; a wake request is never proof that work completed.
No new daemon installation, host timer, network call or automatic MCP restart
is part of this Task.

The intake records only admitted structural work intent, not Hook bodies,
transcripts, arbitrary paths, environment values or numerical observations.
Each slice re-admits the project, collection policy, current source roots and
state. Use the existing physical resolver and collection authorization.
Concurrent or superseding requests cannot be acknowledged by an older round.
Work ownership spans actual execution, including a still-running shutdown;
closing the observer must not release a live worker's lease prematurely.

Keep the Hook's current absolute budget and neutral protocol. Continuation
slices have their own bounded execution opportunities after Hook return,
with cooperative yielding and a bounded delay on unavailable/no-progress work.
They do not extend an individual Hook or keep its caller waiting. Maintain
128-unit/250 ms pages and the 50 ms deadline margin, exact numerical and
attribution algorithms, fresh publication guards and last-good preservation.
Input waiting, partial tails, unavailable files and unavailable infrastructure
are not successful catch-up. Finishing a finite audit sweep never claims that
earlier bytes have remained unchanged or that future usage is already known.

## Ordered Stages In One Task

1. **Execution ownership and restartable intake.** Define the admitted request,
   recovery and acknowledgment boundary, fixed runtime artifacts, policy/runtime
   checks, project lease, bounded shutdown and no-progress outcome. Implement
   and test them in isolation before service integration.
2. **Collection and service integration.** Reuse existing source admission,
   queue/CAS and reconciliation/publication transactions. Retain fair progress
   for collection, pending reviewer admission, inventory, prefix audit and
   immutable publication. Establish caught-up status from actual completed work,
   not queue rotation, silence or a successful Hook exit. Integrate with the
   service lifetime without changing review or backup ownership.
3. **Cross-boundary verification and adoption.** Compare finite-input results
   with full replay. Test interruption, restart, duplicate owners/requests,
   concurrent Core updates, handoff failures, unavailable infrastructure and
   non-progressing inputs. Synchronize current owners and distribution assets,
   then verify the actual host's after-return continuation and obtain two
   independent reviews of the same fixed target before commit/completion.

These are sequential stages of the existing Task, not newly registered Tasks.
The separately registered eight-phase batching work is independent scope and
must not be mixed into this change. No live-state or verification ledger is
maintained here.

## Writes, Permissions And Gates

Expected source writes are Usage lifecycle/continuation modules, the relevant
repository and fixed resolver boundaries, the MCP service entry, focused tests,
the four current owners above, directly affected package guidance, distribution
manifest and test/SQL inventories. Authority routing registers this plan.
Other contributors' working changes must be preserved.

Core Task state, sealed Evidence, review-wait ownership, numerical meaning and
the existing page policy are unchanged. Huge indivisible unit splitting,
timeout extension, cross-Hook validation caches and the separate eight-phase
batching proposal are excluded. Existing work units that never commit within
an available slice are explicitly outside the finite-progress guarantee.

No live configuration, trust, installation, migration or repair is authorized
by this plan alone. If the implementation requires one, first prepare the exact
preview and obtain the existing explicit Setup/configuration authorization.
An unavailable or old resident service is not a successful host test.

Verification includes existing Usage/service regressions, focused restart and
concurrency tests, whole-replay equality, Core/sealed Evidence preservation,
document/distribution checks and observed after-Hook host execution. Report
platform and host limitations. Tier 2 requires two independent GPT-6 Astra
high reviews with no unresolved blocking findings. Use the existing automatic
completion route when its verified prerequisites hold.

## Retirement

Retain this execution owner until the dependent Task is done or explicitly
superseded, durable rules have current owners, and all remaining decisions and
gates have an active home. Retirement requires a separately authorized atomic
history/index transition under AGENTS.md; Task completion alone does not
authorize deleting this plan.
