# Review Continuation Design And Execution Plan

## Authority And Activation Boundary

This is the conditional design and execution owner for the single existing Task
`tg_task_f0e30c8a9ab881e2`. The user approved concretizing the discussed scope on
2026-10-14: reuse valid judgments for unchanged material, obtain a reviewer's
reasoned reassessment or missing material when needed, and reconnect waiting
without losing history or repeating effects. Live status and evidence belong in
the public Task CLI, not in this document.

This document records the design. It does not activate the proposed operations,
migrate live state, reload MCP, authorize an external send, or start implementation.
Implementation uses the existing Task and its applicable authorization; it is
one coherent unit with three sequential internal phases below, not new Tasks.
The current owners continue to govern until the implementation, owner updates,
verification and review land together:

- [Review results and gates](review-completion-specification.md#review-target-receipt-and-finding-ledger),
  [structured results](review-completion-specification.md#structured-review-results),
  and [their design](review-completion-design.md#receipts-findings-and-gate).
- [Finalization](review-completion-specification.md#integrated-review-finalization)
  and [its journal](review-completion-design.md#integrated-review-finalization).
- [Host-owned waiting](review-completion-specification.md#host-owned-review-waiting)
  and [its design](review-completion-design.md#host-owned-review-waiting).
- [Reviewer binding](review-completion-specification.md#reviewer-session-binding),
  [Evidence](evidence-specification.md#assurance-evidence-references-and-finding-snapshots),
  [migration](database-specification.md#initialization-and-supported-schemas),
  and [ownership](task-operation-specification.md#session-ownership-and-recovery).

The normal all-PASS route gains no approval, poll, inspect, stop, ACK, reviewer
restart, target recapture or parent-side JSON reconstruction. Material changes
still use a fresh target and review. This initiative does not change numerical
usage boundaries, transport limits, host trust, public-host metadata, or the
scope of the fixed local Git operation.

## Identity And Decisions

### Unchanged Means A Verified Basis

Use the complete project/Task identity, Contract and authority/criterion binding,
target kind/value/base/generation, and applicable review requirements. Automatic
continuation additionally retains the admitted parent, execution and ownership
generation. Matching displayed text or a material hash alone is insufficient.
Changing Contract, target generation or applicable conditions invalidates reuse;
return to the existing fresh-target/review path without rebinding old Receipts.
Even A-to-B-to-A target selection does not reactivate an old generation.

A supplement may restore access to the already declared material or explain it.
It cannot silently add a new requirement or change the reviewed scope. If it
changes the basis, use the fresh-target path. Uncertain identity is unknown.
Recorded reviewer identity, provenance and originals must agree; an unbound
legacy judgment is not upgraded into a bound continuation through inference.
Existing ordinary manual gates for legacy evidence remain separately governed.

### Required Outcomes

| Current evidence | Operation and result |
|---|---|
| All required valid PASS results; other gates satisfied | Retain the original completion route: an admitted integrated intent uses finalize; manual preparation uses submit as needed and authorized manual completion. No new review or wait. |
| All valid PASS, but verification, Finding, ownership or other gate fails | Retain PASS and report that gate; no redundant review or automatic override. |
| A valid PASS; B has not supplied a judgment | Keep A; explicitly supplement and continue B only; completion uses A plus B's eventual result. |
| B has an NG on unchanged material and the parent disputes it | Give B the reason/evidence; append B's actual reassessment and explicitly resolve applicable Findings. The parent cannot declare B's verdict. |
| Material or review conditions were corrected | Capture the new target and obtain fresh review under existing rules. |
| A reviewer turn changed but the required judgments are already valid | The turn change alone does not invalidate those judgments or require a new target. Preserve the old observation in history. |
| A required judgment, binding, operation result or relevant failure cannot be resolved | Return unknown/blocked with a specific next action; never infer PASS. |

Review NG, reviewer execution failure/interruption, observation failure and an
incidental later turn are different facts. An existing valid judgment is not
made invalid merely by a later unrelated chat turn. Conversely, a completed chat
is not review PASS, and a later PASS cannot silently erase a failure affecting
the required judgment. Continuation records must explain which required result
has become available; genuine unresolved contradictions remain blocking.

## Durable Review Model

### Append Judgments And Explicit Successors

Keep Receipt IDs, original bodies/digests, provenance, session bindings and
Findings immutable. Add a positive per-reviewer judgment revision and explicit
successor links for same-basis reassessment. The physical Receipt uniqueness
changes from Task/generation/reviewer to Task/generation/reviewer/revision;
ordinary registration still rejects a duplicate unless admitted by an explicit
continuation. Removing the old unique constraint alone is not sufficient.

The core repository owns these logical records, with concrete DDL and guards
specified in the database owners before activation:

| Record | Required binding and invariant |
|---|---|
| Continuation | Tool-generated stable ID, complete basis, original preparation route and optional existing integrated intent, applicable parent/execution/owner binding, predecessor continuation, selected reviewers, action, bounded reason, creation time and monotone preparation/registration state. |
| Selected reviewer | Actual session ID, normalized reviewer key when known, supplement or reassess, expected prior Receipt ID or explicit absence, immutable prior/new result references and digests. |
| Receipt successor | Prior and successor Receipt IDs, continuation ID and revision. Same reviewer/session, Task/Contract/target; at most one successor and one predecessor; no cycles or branches. |
| Reassessment resolution | Existing Finding ID, actual successor Receipt ID, explicit resolution record and continuation ID. It preserves the original Finding and ties the exception to that precise finding. |

Use the existing stable-ID conventions and privacy/size validation. A supplement
with no previous Receipt produces revision one. A reassessment consumes the
expected current head and creates its next revision in the same core transaction
as the Receipt, provenance, session binding, Findings, References and events.
The expected-head comparison rejects stale/concurrent continuations. Timestamps
and caller-chosen reviewer aliases never choose a successor.

A same-basis reassessment is by the original actual reviewer session under the
existing trusted-caller attestation and host identity boundary. If that identity
cannot be established, this route reports unavailable; it does not treat a new
reviewer name as the original reviewer's correction. Existing reviewer/owner
separation and anti-alias checks remain. Several revisions of one reviewer count
as one reviewer, not additional independent passes.

Old material or malformed links cannot be treated as ordinary unbound input.
Validation covers the complete relevant chain even when an audit page is bounded.
Done Tasks and sealed completion history remain immutable.

### Effective Verdict And Finding Resolution

Derive the current effective Receipt per reviewer from validated successor links,
not the latest timestamp. A new NG becomes that reviewer's effective verdict;
old PASS cannot remain effective behind it. An old NG ceases to be the current
verdict only through its validated successor. It remains visible in history.

An explicit reassessment in progress suspends qualification of the selected
reviewer's old judgment until the new outcome is registered. Unselected valid
PASS remains usable. The preparation writer and finalization writer compare the
same continuation/evidence revision, so completion cannot race a new reassessment.

Findings remain independent of verdict replacement. Every unresolved Finding
retains its existing manual/integrated gate meaning. A later PASS does not
resolve any Finding automatically. For same-basis reassessment, extend explicit
Finding resolution with a typed reference to the successor Receipt: the finding
belongs to the superseded chain, the same reviewer has actually reconsidered it,
and the owner records the resolution and its reason. Only that validated link
permits satisfying the existing fresh-review requirement without a new target
generation. Unlinked historical high/medium Findings retain the current rule.
The existing resolution operation without such a link keeps its current behavior.

The extension must work for Findings already explicitly resolved before the
reassessment: add an immutable reassessment link after checking the retained
resolution, without overwriting or resolving the Finding a second time.
Links never authorize changing the review result, waiver of an unresolved
Finding, or a correction to reviewed material without a new target.

## Entry Points And Result Transport

An unjudged prepared continuation can itself need another continuation after a
known failure or incomplete result. After exact settled cleanup, explicitly
supersede that pending round in the same short core transaction that admits its
successor. Preserve the old choices, observations and originals; superseded is
not registered and invents no result. Capture exact metadata/result digests for
saved unregistered originals from unselected earlier slots. The successor's
one complete atomic batch may register those unchanged old originals under their
original continuation identity alongside the newly selected originals. Native
and independent Evidence validation retain the explicit capture/consumption
links. A saved result for a newly selected slot must first use its existing
registration boundary; in particular, a saved NG is never hidden as unjudged.

Machine Save and continuation switching share the preparation admission. Save
validates the current applicable context before exclusive creation and retains
the lease through complete result/sidecar readback. A selected obsolete context
cannot save after its successor is admitted. Unselected initial slots may still
finish under their unchanged basis. A failed partial Save remains residue and
blocks the affected transition, rather than being inferred absent or discarded.

### Explicit Exceptional Continuation

Ready shared-file preparation also records a bounded, resolver-owned operational
locator for its exact Packet, named result paths, preparation route and applicable
owner/execution/basis. It contains references and digests, never result bodies.
Publication follows complete physical Packet preparation; failed preparation
does not replace a previously ready locator. The current pointer advances only
through an explicit successful preparation, retaining earlier locations. An
existing integrated intent is selected only when it matches that preparation;
its mere presence for the Task/generation cannot promote a manual route.
Continuation derives its original transport and retained inventory from these
records and validated core bindings, not a recursive filesystem search or a new
LLM-provided material list. A legacy handoff without an admitted locator remains
usable by its original submit/finalize procedure; continuation reports the
missing binding rather than fabricating it.

Add one helper operation, `review_handoff.py continue`, for the exceptional
supplement/reassessment path. Its design input is the ordinary explicit repo,
Task ID, target generation, unused ignored physical output directory, and a
bounded versioned stdin selection. No new public MCP control is needed.

The closed version-one selection has `version` and `reviewers`; each selected
reviewer has `session_id`, `action` (`supplement` or `reassess`),
`prior_receipt_id` (null only for supplement with no Receipt), and a bounded
sanitized `reason`. There are one through eight distinct selected sessions,
within the existing whole-input cap. Tool-owned IDs, target material, judgment,
approval, timer, destination and schedule are not caller inputs. The helper
derives and locks the complete current basis and expected effective heads.

Continuation retains the original shared-file preparation route. Ordinary
`prepare` remains manual; `prepare-finalization` retains its exact already
authorized integrated intent. The helper neither creates Git/automatic-completion
authority nor converts a manual route into integrated finalization. Existing
manual-only environments and permission boundaries remain supported. Direct
complete-byte transport keeps its existing explicit path outside this helper.

Preparation creates the durable continuation before exposing new reviewer
instructions. It prepares a fresh Packet/result location with the same review
basis and a tool-owned continuation context; it never overwrites old originals.
The new Packet identifies the selected reviewers and predecessor references.
Only selected reviewers receive a new read/save request; they read the required
material and independently decide the actual result. Ordinary parent dispatch
permissions remain controlling; the helper does not send messages itself.

The reviewer's existing verdict/provenance/Findings document remains intact.
Version the machine-owned Packet context, save sidecar and submission envelope
to bind the continuation and expected predecessor. Save captures actual caller
identity as today; the parent cannot fabricate or transcribe the binding.
Readers accept old formats under their existing rules; new continuation metadata
cannot be dropped to fall back to ordinary registration. Old result bodies are
never regenerated as if written for a new round.

Retained valid originals that have not yet been registered remain marked
unregistered. The continuation inventory binds their exact old Packet, sidecar
and digest; it does not claim a Receipt exists. A saved NG must be registered
under the existing complete-original batch rules before a same-basis reassessment
can select its Receipt. If that registration cannot be completed, report the
missing original/registration boundary instead of hiding the NG as no judgment.

### Registration And Finalization

The helper returns the generated continuation ID, current basis, retained result
references, selected read/save requests and ordinary wait arguments for only
the selected reviewers. For manual preparation it returns the continuation's
generated `submit_command`; for an existing authorized integrated intent it
returns the retained `finalization_command` and no competing submit command.
The durable pending
continuation lets the existing `review_wait_wait(task_id, reviewer_ids)` validate
the allowed new set; arbitrary changed reviewer arguments grant no continuation.
The Task/current-target alone cannot select an ambiguous pending continuation.

Registration validates the complete required new-original set, plus the exact
retained inventory, before one atomic batch write. Already registered results
are matched by immutable binding and are not submitted again. Valid retained
unregistered originals join that one new batch without rewriting their bytes.
Any missing required new result leaves the batch unregistered and reports what
is missing. A registered NG remains a gate failure, not a transport failure.

Use the same core effective-evidence evaluator for manual completion, automatic
completion, Task/Viewer projection and locked completion-basis selection.
Each route consults qualified saved judgments before requiring any new reviewer
activity. When all are valid and there is no conflicting unresolved effect or
required judgment, manual continuation performs only needed registration and
leaves completion to its existing authorized manual procedure; an admitted
integrated intent uses finalize. Neither route creates an unnecessary wait.
Continuation records how obsolete turn-change observations were superseded by that verified
result basis, preserving their history rather than resetting a blocker field.
An actual unresolved failed/interrupted/unknown review remains separately reported.

Registration progress belongs to each continuation batch. For integrated work,
commit candidate, publication and Task completion belong to the one fixed finalization root.
Appending a review round never resets successful registration of earlier batches,
changes a retained candidate, or repeats a successful commit/completion. Once a
candidate exists, any eventual publication still requires the unchanged exact
tree/base and all fresh gates. Unknown effects require observed reconciliation;
no second candidate or competing finalize intent is created to bypass uncertainty.

## Wait Continuation And Delivery

### Separate Result Inventory From Active Reviewers

The selected registration/completion route uses the full effective result inventory. A worker watches only
the selected sessions whose results are still needed, with freshly captured
exact session/turn pairs. It does not require a retained A/PASS reviewer to stay
on an old chat turn while B continues. Within an active review round, unexpected
turn changes still invalidate that round's wait; they never silently retarget it.

Target generation and continuation sequence are distinct. The operational
wait journal and, when present, finalization store must preserve prior round observations/blockers, original
references and binding revisions, with one current-round pointer and root effect
state. Version that closed journal rather than editing its current singleton's
immutable reviewer pairs. Keep each row bounded; retain at most 64 continuation
rounds for a fixed preparation root, with at most eight reviewer slots per round. Reaching
capacity returns a closed error and does not evict history or widen existing
wait-attempt/loaded-association bounds. A manual continuation binds its wait
association without manufacturing a finalization intent or commit stages. Its
all-ended notification retains the existing submit/manual-processing guidance;
the worker does not start reading originals or finalizing on that route.

### Reconnect Only After Settled Cleanup

Use the pending core continuation plus an operational transition intent to
coordinate the stores; do not pretend a core transaction includes host effects.
The sequence is:

1. Reserve the explicit continuation identity, exact destination, expected old
   current round and full Task/parent/owner/basis in the replacement intent, then
   admit that identity in core. The saved fence closes competing finalization
   and same-Task replacement even if core admission is interrupted; it does not
   activate new work.
2. Request known-safe cancellation and join the old worker outside any lease or
   database transaction that worker needs. Then reread old request, controller,
   timer and send state. A missing worker reference is not proof of exit.
3. Settle known cleanup through the existing identity-checked timer protocol.
   Unknown creation/activation/pause/deletion/send prevents replacement. A deleted
   timer alone does not settle a send or a running worker.
4. Reacquire the existing request/dispatch/finalization admissions in a documented
   common order, compare the transition token and expected old round, and recheck
   core basis and actual selected reviewer turns before publishing the new round.
   Never hold a core SQLite writer across host I/O or join. Concurrent workers
   observe the transition fence before every effect.
5. If selected reviews already ended, process their results without a new timer.
   Otherwise create one fresh occurrence through the existing wait operation and
   expose readiness only after a confirmed active timer and live owned worker.

Crash/retry behavior is observation-led. A saved transition with incomplete
cleanup stays pending/unknown, never active. Repeated explicit requests with the
same operation identity reconcile existing records; they do not redispatch
reviewers or create another worker/timer. Conflicting selections fail closed.
Inspection and restart do not resume a worker. The cross-store publication and
each crash window require focused recovery tests before activation.

### One Delivery Decision For Each Settled Outcome

Preserve existing send intent, accepted/unknown outcome, receipt observation and
worker-exit distinctions. Retiring a wait does not reset its notification state.
An asynchronous invalidation may send one bound failure notice only when the
original parent, safe timer cleanup and existing sending conditions are proven.
Otherwise expose the reason/next action through the existing response/inspection.

Success and invalidation share the same per-outcome send decision under the
same-parent dispatch admission. A continuation fences the obsolete outcome so
an old worker cannot send a stale success after the new round is published.
A known sent old notice stays historical; it is not resent. A later explicitly
authorized round may report its distinct outcome, but a still-unknown old send
blocks a competing replacement/send. Scheduled/parent resume, cancellation and
finalization races cannot each claim the same send decision.

Notification identity includes the bound continuation/occurrence as well as the
existing parent/Task/target identity. Receipt must match that exact notification;
an old receiving turn cannot acknowledge a new round. Reuse public host APIs,
real metadata, the current finite deadline and frame limits. No new routine
status call, ACK, resend, split message or unbounded history read is introduced.

## Evidence, Compatibility And Synchronization

Current display separates effective verdicts, explicitly pending reassessments,
and historical judgments/Findings. It identifies retained versus new results and
the concrete missing action. Normal result projection uses the effective set;
bounded audit access exposes predecessor links without discarding history or
changing completion qualification because a display page was truncated.

Completion must capture the same effective Receipt IDs and relevant Finding
resolution/reassessment relations used by the locked gate. Add versioned Evidence
support for that continuation basis, including the selected heads and the links
needed to explain historical NG and explicit resolutions. The independent reader
must verify that graph and reject inconsistent selection. Existing immutable
Receipt/Reference digests, original data, cycles and sealed Bundle payload bytes
remain unchanged; a new format/version applies only to new completions requiring
the added basis. Do not edit old Bundle content to make it look like new evidence.

The core Receipt uniqueness and new relations require an explicit setup-only
migration after the current schema-28 baseline. Allocate its version against the
actual implementation head; update the database, setup, Evidence and package
owners together. Migration assigns revision one to existing Receipts, preserving
their IDs and assertion bytes; it invents no reassessments, session bindings or
resolutions. Ordinary read/write commands never migrate. Old binaries reject the
new schema. Rollback, reentry, backup/recovery, Viewer admission and old sealed
evidence reading retain their current guarantees.

Version the operational journal separately. An admitted old journal can be read
without writes; conversion requires the explicit continuation, validated original
basis and settled effects, preserves all old rows/facts, and publishes atomically.
Corrupt or unknown-effect journals are not repaired from caller text. A known
successful old finalization remains successful without needing conversion merely
to report it. Core upgrade does not silently activate operational workers.

Synchronize the product/design owners for review registration, Finding resolution,
gate selection, finalization, waiting, Evidence, core/operational schemas and
public projections; the helper/CLI contracts and review continuation Skill
guidance; migration/release manifests and directly coupled fixtures. Numerical
consumers must tolerate repeated judgments from one actual reviewer without
counting the same observed response twice; numerical availability remains outside
the core gate and no new usage-allocation rule is introduced here.

## Sequential Phases And Verification

### 1. Contracts And Closed Data Design

Finalize the logical records above into exact DDL/guards, closed helper/envelope/
resolution and report shapes, error vocabulary, transition/lock ordering and
versioned Evidence format in the named current owners. Keep unsupported operations
conditional until implementation is synchronized. Define the shared effective
evidence evaluator before changing any individual gate consumer. This phase
produces a coherent design for the existing Task, not standalone new features.

### 2. Storage, Registration And Safe Continuation

Implement migration and repository constraints first, then result preparation,
typed continuation registration and explicit resolution links, effective-evidence
reads and locked completion/Evidence selection, finally the journal/worker
transition and delivery integration. Each internal slice uses its coupled tests.
Do not activate a partial system whose display, manual gate, automatic gate and
Bundle select different verdicts. Expected modules include review results/session
repositories, reviews and storage gate selection, Evidence writer/reader,
review_handoff helpers, finalization repositories and managed/direct wait code.

### 3. Regression, Independent Review And Host Validation

Use temporary databases, ignored physical handoff directories, isolated Git
repositories and offline host fixtures. Cover at least:

| Group | Required contrasts |
|---|---|
| Reuse | All-PASS without new wait; other gate fails; same bytes with changed Contract/generation/conditions; valid PASS followed by incidental reviewer turn. |
| Completion route | Manual supplement and reassessment return submit and retain manual completion; integrated returns only its existing finalize command; all-PASS on each route; no implicit intent, Git authority or route switch, including manual-only environments. |
| Supplement | A retained PASS/B pending; both pending; retained original not registered; missing/changed sidecar; basis changed by supplement; B already ended at admission. |
| Reassessment | NG stays NG; NG to PASS with explicit linked Finding resolution; already-resolved Finding receives link; unlinked/unresolved Finding still blocks; PASS to NG suspends qualification; same session cannot count twice. |
| Registration | Atomic new batch with registered retained results; exact lost-response reconciliation; stale head, duplicate request, competing successors, wrong session/Task/target and invalid chains. |
| Effects | Old worker still running/missing reference; stopped worker; known timer cleanup; unknown create/arm/delete/pause/send/commit; cancellation, scheduled turn, parent busy and publication races. |
| Recovery | Crash at every transition boundary; core pending with no operational row and the reverse admitted intermediate; no double worker/timer/registration/commit/completion/send. |
| Evidence | Show/manual/automatic/Bundle choose the same heads; original NG/Findings remain traceable; old sealed bytes stable; reader rejects a substituted predecessor/head. |
| Compatibility | Setup-only migration from supported sources; failure rollback; exact reentry; old journal conversion/refusal; unchanged initial review, normal waiting, manual path and usage deduplication. |

Run affected repository/CLI/helper/finalization/managed/direct/receipt tests,
document-contract, packaged-reference, release and test-lane checks. Tier 2 needs
two independent reviews of the exact combined target with no blocking findings.
Before actual activation, establish required setup/reload permission and compatible
loaded code. Validate actual same-parent waiting across a parent turn end,
selected-reviewer continuation and new-turn notification receipt under the existing
approved host procedure. Offline PASS and accepted sending alone do not prove it.
Do not add live setup, trust/config changes or notifications under design approval.

## Completion And Retirement

The three phases are sequential within one Task; there are no optional or
independently consumed implementation lanes. Whole-Task completion requires the
accepted behavior, synchronized owners, exact-target verification, two independent
Tier 2 passes and the applicable real-host validation. Any unavailable external
prerequisite is disclosed and retained as such, not converted into evidence.

This design remains routed until the existing Task is done or explicitly
superseded, all durable rules have current owners and no live dependency needs
its anchors. Retirement requires the separately authorized reviewed history/index
transition under AGENTS.md; it is not automatic on completion. Keep live progress,
review outcomes and execution artifacts in the public Task state.
