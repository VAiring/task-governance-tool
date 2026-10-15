# Conditional Review Answer Collection Design

## Status, Scope, And Authority

This is the conditional design for collecting a reviewer's structured final
answer. It records the approved direction; it does not activate a hook, new
JSON input, schema migration, reviewer message, Git effect, or completion gate.
The current [review/completion specification](review-completion-specification.md)
and [implementation design](review-completion-design.md) continue to govern
runtime behavior until a separately authorized implementation is verified,
reviewed, and synchronized with its owners.

The design replaces only the reviewer's answer-saving/submission operation.
The reviewer still uses existing `read`, `material`, and optional
`workspace prepare/cleanup` operations, with their current material and
verification boundaries. The parent retains manual submission; the collector
reuses internal validation/persistence and the existing finalizer. Removing all
reviewer tools, changing material delivery, and changing numerical collection
are outside this design.

This document owns the proposed answer/collection protocol and its adoption
conditions. The [authority index](authority.md#document-authority-index) routes
it as conditional. Durable runtime rules will belong to review/completion,
Evidence, database, and Setup owners. Retire this document only after those
rules are adopted, all dependent work is complete or explicitly superseded,
and the separately authorized history/routing transition preserves outstanding
conditions. Live Task progress and evidence remain in the public CLI.

The [concrete host baseline](#concrete-host-admission-baseline) records the
current unresolved prerequisites. The [closed collection values](#closed-collection-values-and-records)
define the conditional input, persistence and evidence contract; they are not
an activation declaration.

## Outcome And Responsibility Boundaries

A reviewer reads the fixed material, performs the review, and returns one
structured final answer. It does not save a file, provide Taskgov bookkeeping,
register a Receipt, or complete the Task. Collection observes that exact answer,
preserves its original, and associates it with the fixed request. Successful
collection and terminal reviewer status are separate prerequisites; neither is
a review PASS or Task completion.

| Responsibility | Owner in the proposed implementation |
|---|---|
| Fixed material, request, destination and completion authority | Existing preparation, extended with an explicit answer protocol and collection binding |
| Exact answer extraction, JSON/semantic/privacy validation | Pure answer decoder; no host, filesystem or DB calls |
| Public host event/answer observation | Dedicated hook adapter, separate from numerical usage hooks |
| Original publication and readback | Shared physical transport primitives, invoked by the collector |
| Request/slot state, duplicate handling and correction admission | Collection repository under the canonical state resolver |
| Same-reviewer correction send and observation | Connected MCP host adapter, outside DB transactions |
| Atomic Receipt/Finding registration and current-basis checks | Existing public registration service, extended by a typed collected-result input |
| Automatic commit/completion/parent reporting | Existing finalization and review-wait orchestration |
| Manual registration | Parent through the supported submission route |

Do not grow a second finalizer, direct reviewer DB writer, full-chat collector,
general message queue, transcript parser, or alternate state root. Proposed
module names may change; these responsibility and transaction boundaries may not.

## Reviewer Answer Protocol

The request selects `review-answer-v1`; this discriminator is machine metadata,
not another reviewer-entered field. The answer is exactly one JSON object:

```json
{
  "verdict": "pass",
  "summary": "The reviewed change meets the stated requirements.",
  "findings": [],
  "checks": ["Compared the change with the governing contract and regression cases."],
  "limitations": ["No live-host verification was performed."]
}
```

All five keys are mandatory; unknown or duplicate keys are rejected. No Task ID,
Contract revision, target generation, reviewer/session ID, output path, model,
Skill version, provenance declaration, or internal transport version is requested
inside this object.

- `verdict`: exactly `pass`, `changes_requested`, or `unable_to_review`.
- `summary`: nonempty sanitized string, at most 1,000 characters.
- `findings`: zero through 64 objects, each exactly
  `{severity, summary}`, using existing `high|medium|low` severity and
  required sanitized summary of at most 1,000 characters. Preserve order and
  every Finding. Existing duplicate and normalization rules continue to apply.
- `checks`: zero through 32 nonempty sanitized strings, each at most
  1,000 characters, describing actual checks in the reviewer's own words.
- `limitations`: zero through 32 nonempty sanitized strings, each at most
  1,000 characters. An empty array is the reviewer's explicit report of no
  stated limitations, not a claim of exhaustive verification.

An empty `checks` array is not fabricated evidence of work. PASS remains the
reviewer's judgment; current completeness/gate rules still apply. The collector
does not infer a verdict from Findings, turn completion, or an empty list.
`changes_requested` and `unable_to_review` require an explanatory summary.
An unable reviewer may preserve concrete Findings already obtained; it must not
discard them to use that verdict. This outcome is valid collected evidence but
does not contribute a qualifying PASS and prevents automatic completion.
It is not converted to content NG or invalid JSON.

The first answer protocol is for an explicitly assigned independent review.
Its fixed request states in ordinary review language: returning `pass` or
`changes_requested` attests that the responder actually performed the assigned
review in an independent reviewer role, rather than as the implementer doing
self-review. If the responder cannot affirm that role or sufficient review,
it must return `unable_to_review` and explain the limitation. No extra JSON key
or Taskgov-specific declaration is added. The protocol definition and exact
request digest bind this meaning to the answer; a verdict is not interpreted
without that request. An empty `checks` array does not supply evidence of work.

Registration may map an answer to a qualifying independent judgment only when
the admitted request explicitly carries that protocol/attestation definition,
the actual response is bound to its assigned reviewer and turn, and the answer
expressly supplies `pass`. `changes_requested` carries the same role attestation
but cannot qualify as a PASS. Unknown role, missing request/attestation binding,
an unable result, or a known conflicting structural role binding never becomes
an independent PASS from the assignment label alone. Reject a conflicting
structural binding and return it for parent judgment. Do not build a free-text
role classifier: any contradiction explicitly identified by the parent or
reviewer requires correction at its source, not rewriting the answer. The
claim remains reviewer-attested, not host-proven independence. Tier-0 waivers
and approved self-review fallback retain their existing explicit routes and
approval conditions; this protocol does not silently classify or replace them.

The ordinary request asks for JSON alone. The decoder also accepts the whole
answer wrapped in exactly one `json` fenced block, with only JSON whitespace
outside it, an opening fence on its own line and a closing fence on its own
line. No other language label, mixed commentary, multiple blocks, substring
search, brace matching across prose, or repair by the parent LLM is supported.
JSON whitespace around a bare object is allowed. Reject invalid Unicode,
non-finite numbers, wrong exact types, invalid enums and excess nesting without
echoing input. Booleans never satisfy an integer field in machine metadata.
Apply the existing shape/privacy/semantic ordering to every string before
persistence. Findings, checks and limitations contain sanitized descriptions,
not logs, command bodies, prompts, hidden reasoning, secrets or transcripts.

## Bounds And Failure Classification

Keep the existing 262,144-byte bound on an individual original and on the
complete serialized registration input, including all escaping, base64,
sidecars and batch framing. Retain one through eight reviewer slots and the
combined limit of 64 Findings. These are whole-input limits, not additive
per-reviewer allowances. Exact encoded size is checked before dispatch;
having saved individual originals does not mean their combined registration
will fit. Do not split a rejected batch, drop Findings, summarize originals,
or increase an allowance silently.

The hook adapter has the same 262,144-byte whole-event input bound. If event
metadata/escaping makes an answer exceed that bound, it is an incomplete
observation eligible only for the bounded exact-answer recovery below.
No clipped prefix becomes a valid answer. A dedicated reply transport can
reuse complete originals without exposing them as model-visible hook output.

| Failure class | Meaning and action |
|---|---|
| `format_invalid` | Complete bounded answer has invalid JSON/shape/type/enum or required content; one source correction may be attempted |
| `privacy_rejected` | Input fails existing privacy policy; retain only sanitized failure metadata, never the rejected body or an echo |
| `capacity_exceeded` | Original, event, field/count, or full registration framing exceeds its bound; report capacity failure, never ask to delete Findings to fit |
| `collection_unavailable` | Missing/incomplete answer or host transport cannot establish exact complete bytes; use only the bounded recovery below |
| `binding_mismatch` | Wrong/stale project, request, Task, target, execution, reviewer or turn; never rebind |
| `save_unknown` / `registration_unknown` | A dispatched persistence effect cannot be determined; inspect that exact effect before any retry |
| `review_unable` | Well-formed `unable_to_review`; persist as its own verdict, block automatic completion |
| `review_changes_requested` | Well-formed content NG; existing repair/reassessment rules apply |

Capacity and privacy failures do not consume a format-correction request.
They remain reported failures, not automatic retry reasons. Diagnostics use
closed field paths and fixed reasons; unknown keys/values, raw bodies and
exceptions are never included. The implementation must preserve classification
through hook, MCP, CLI and parent reporting, rather than converting every
failure to invalid JSON.

## Request Binding And Host Admission

Preparation publishes the answer protocol and exact physical destination with
the existing fixed Packet. Its immutable collection basis includes project
identity and binding generation, parent session, Task, Contract revision,
execution, complete target/capture identity, preparation identity and Packet
digest. Continuation adds the exact existing continuation/selection identities.
It records the completion route and authority already admitted; a hook cannot
supply or enlarge them.

After actual dispatch, bind each allocated slot to the actual returned reviewer
session and the intended review turn using the supported host observations.
Slot labels or supplied JSON IDs do not prove that association. The supported
dispatch/observation adapter must identify a new or already-running turn
unambiguously; if it cannot, admission fails closed. Do not choose whichever
turn is latest later. A completed answer racing ahead of slot binding is not
accepted under guessed identity; after binding, only the exact recovery route
may recover it. Preparation alone launches no reviewer.

An admitted slot has one initial turn and at most one explicitly linked format
correction turn. Same session does not imply same turn or independent review.
Preserve current alias detection, owner/execution checks, all current-basis
checks, and same-basis continuation rules. A new Task generation, ownership
change, or conflicting preparation makes old arrivals historical or rejected,
never current evidence.

The actual collector caller and answer-source reviewer are distinct typed
identities. Do not call old `save` in the parent and label that parent as the
reviewer; do not change `CODEX_THREAD_ID` or trust a claimed session field in
answer JSON. The public writer verifies a collected-result binding against
the admitted preparation and host observation, under its existing short writer
lock. Ordinary caller ownership checks remain for all other operations.
The new entry is trusted-local structural binding, not remote authentication.

### Host Contract And Activation Evidence

Official [Codex Hooks](https://learn.chatgpt.com/docs/hooks#subagentstop)
documents `agent_id`, nullable `last_assistant_message`, `turn_id`,
`stop_hook_active`, and nullable transcript path. Its
[common fields](https://learn.chatgpt.com/docs/hooks#common-input-fields)
state that subagent hooks use the parent `session_id`; transcript layout is
not a stable API. A hook event or its active turn ID alone therefore does not
prove the exact review turn, complete final answer, successful terminal state,
or independent review.

The first supported host adapter must demonstrate exact parent/child/turn
binding and complete final-answer retrieval using public host surfaces.
Do not read arbitrary transcript files or private host databases to fill gaps.
Existing terminal observation remains authoritative for reviewer termination.
`stop_hook_active` is not a durable correction counter or success flag.

An MCP hook uses an already connected server and does not establish a server
connection or trust. Background hook output does not itself wake an idle
parent. Neither API availability in this assistant nor a hook configuration
file proves that the installed product can address/resume the intended native
reviewer. Same-reviewer MCP send, actual correction-turn linkage, restart
recovery, and same-parent delivery are implementation activation gates.

Do not use hook `decision=block`, a hidden continuation prompt, or an additional
timer as an unapproved substitute for the chosen MCP correction route.
A supported host lacking that route reports format failure. Only explicit Setup
may install project-local definitions, and host trust remains a separate user
action. Numerical usage hook opt-in does not opt into answer retention or sends.

## Collection, Originals, And Recovery

The hook validates a small bounded event, matches an admitted slot, and hands
off one bounded observation. It does not wait for sibling reviewers, execute
Git, complete a Task, perform a full-history scan, or send repeated requests.
The collection service owns durable short state transitions; network/host and
file I/O occur outside DB transactions. Existing repository/lease conventions
serialize admission and publication.

Keep accepted originals in the prepared ignored, untracked physical transport
directory, under exclusively allocated paths. Preserve the exact UTF-8 bytes
of the complete delivered answer, including whitespace/fence. Record a distinct
payload range/digest for the parsed JSON; never call reserialized JSON the
original. Do not retain a whole hook event, provider envelope, transcript,
prompt or command. Before publication, revalidate privacy and the full basis;
flush and read back the physical file, compare exact bytes, then atomically
record the immutable original reference/digests. Current symlink/junction,
path, identity and TOCTOU protections remain.

The collection repository uses a resolver-owned operational store associated
with the preparation, not arbitrary supplied DB paths. Keep at most eight slots,
two attempt identities per slot, one correction-send intent, exact
original/metadata digests, closed failure codes and publication/registration
references. Each closed metadata record is bounded to 32,768 UTF-8 bytes;
original bodies stay outside this journal. A slot never appends unlimited
observations, turns, diagnostics or retry history. Repeated identical
observations produce no new row/body. Do not prune evidence to free capacity.

Use explicit, separate facts for observation, durable original publication,
correction effect, terminal reviewer status and registration. `received` is
not `saved`; `saved` is not `registered`. A duplicate for the same
preparation/slot/attempt and byte digest is idempotent. A different digest for
the same immutable source turn is a conflict, not a newer answer. A delayed
initial-turn event cannot overwrite an admitted correction answer.
Cross-target or cross-preparation reuse is rejected.

Publication writes an intent before exclusive file creation. A crash after
creation but before reference commit is reconciled only against that saved
intent, exact path and full bytes/digest. Partial or conflicting residues stay
unavailable; never overwrite, delete, choose another path automatically, or
announce saved. Read-only inspection/restart does not itself perform a save,
send, migration, retry or completion.

Missing hook observations and correction silence use the existing wait deadline;
they do not reset it. Within that deadline, an admitted public API operation
may obtain the complete answer for that exact source session/turn/basis and
feed the same decoder/publication path. Never reconstruct a result from
summaries, tool output, a truncated preview or an arbitrary latest message.
If the complete answer cannot be obtained, report collection failure to the
parent. Deadline expiry does not prove a send failed or permit another send.
No new normal-path parent polling is introduced.

This deadline policy applies only when a matching live wait occurrence already
owns observation. With waiting OFF, unavailable, or explicitly parent-managed,
there is no implied deadline or background observer. Normal complete bound
answers can still be collected and manually submitted. The existing parent
continuation is the recovery owner: when it processes the ended reviews, it may
make one bounded exact-answer recovery call per missing slot and then apply the
same decoder/publication path. If that call cannot establish a complete answer,
it returns collection failure immediately to the parent; it does not schedule
polls, create a worker/timer, or wait for a nonexistent occurrence. Host calls
retain their bounded transport timeout, not a new review deadline.

Automatic format correction is admitted only when a matching live occurrence
can durably own the send, exact follow-up turn and original deadline. Without
that observation owner, do not dispatch a correction: return the format failure
to the parent with the fixed reason `correction_observer_unavailable`. If an
already-dispatched correction loses that owner, preserve its known or unknown
send effect and return control to the parent as unresolved; never retry or
declare failed merely because observation ended. Explicit later recovery uses
the retained exact identities and the existing supported recovery route.
Parent manual registration never bypasses missing, invalid or unresolved slots.

## One Format Correction

Only a complete, bounded, privacy-admissible `format_invalid` result for an
unregistered slot is eligible. The service atomically reserves one correction
intent against the exact initial answer observation and current basis before
calling MCP. The request contains the fixed schema and sanitized field/reason,
not the failed body, untrusted key names or a suggested review verdict.
Ask for the same review's complete corrected final answer; do not request new
implementation, change the target, or demand PASS.

The send effect is `not_sent`, `accepted`, `failed`, or `unknown`.
Once dispatch may have happened, its one-request budget is consumed. A
pre-dispatch admission failure also ends automatic correction for that slot;
this design has no retry loop. Only a known accepted send and exact observed
follow-up turn linkage can admit its result. Acceptance alone is not receipt,
execution, corrected-answer validity or terminal status. An unknown send stays
unknown until exact public observation resolves it; never resend merely because
the deadline expired. If correlation is unavailable, report failure/unknown
without changing the meaning of the effect.

The correction transition fences finalization while it is pending. Preserve
the initial turn record and the explicit new-turn relation; do not overwrite
the wait's original reviewer pair or globally disable turn-change detection.
The worker observes the authorized correction turn for that slot only.
Unexpected turns still invalidate an unregistered slot's active observation;
this exception is not a general permission to follow the latest turn. A later
unrelated turn does not invalidate an already registered, fully bound judgment
retained under the existing same-basis continuation contract. Other valid
answers for the unchanged basis remain retained.

Revalidate the complete corrected answer. A second format error, inability to
send, failure to bind its turn, or silence past the original deadline ends in
a review-format/collection failure with the distinct underlying cause. Never
issue a second correction. A corrected valid NG or unable result is accepted
as that verdict; formatting recovery is not content repair.

This is not judgment reassessment: no Receipt exists for a malformed initial
answer. After registration, changes to a judgment must use existing explicit
continuation/reassessment with retained history and Finding-resolution rules.
A format-correction relation cannot supersede a registered NG or manufacture a
new qualifying independent reviewer.

## Provenance And Evidence Changes

The current [v1 provenance contract](evidence-specification.md#versioned-review-provenance-and-bundle-boundary)
is a caller declaration with `bound_attestation/trusted_caller/1`. It cannot
be filled by the collector pretending the reviewer declared those fields.
Introduce a separate provenance v2 variant for collected answers; v0/v1 readers,
originals, References, Bundle payloads and digests remain unchanged.

The proposed v2 separates these sources explicitly:

| Group | Meaning |
|---|---|
| `answer_attestation` | Exact accepted answer digest and its five fields; checks/limitations are reviewer claims |
| `assignment` | Request protocol, designated review kind/profiles/lenses and launch configuration, if actually recorded; not evidence of execution |
| `host_observation` | Exact admitted session/turn relation and individually observed runtime facts, with source/version |
| `unknown` values | Facts the selected public host surface cannot establish; not inferred defaults |

Every optional runtime fact uses a closed tagged value:
`{state: observed, value: ..., source: ...}` or
`{state: unknown, value: null, source: null}`. Observed sources are versioned,
allow-listed public adapter observations, not caller labels or arbitrary prose.
An observed model value is the actual relevant reviewer-turn model; launch
configuration and the parent hook's model are not substitutes. A model change
within the reviewed work must remain represented or unknown, not silently
flattened to the last model. Skill installation is not use; absent evidence
cannot become `not_used`. Requested methods/lenses are not performed methods.
Do not parse free-text `checks` into legacy method codes. Explicit empty,
unknown, not applicable, and legacy absence remain distinguishable.

Collection provenance has its own producer/version and a structural association
assurance distinct from v1 attestation. It proves neither competence,
independence, completeness nor truth. The parent still declares the independent
assignment through the current authority route; the bound answer supplies the
explicit role attestation defined above. Assignment alone cannot supply it,
and session alias checks remain.
The new digest domain includes project/Task/Receipt/target, source identity,
original and payload digests, protocol, and all versioned semantic metadata.
No v1 digest is recalculated under v2 rules.

The implementation must define the closed v2 persisted/public shape in Evidence
and database owners, update migration/admission/reentry validation and atomically
save the new provenance with Receipt/Finding/Reference rows. Preserve exact
answer checks/limitations as sanitized structured evidence, rather than squeezing
them into a Receipt summary or losing them after registration. Bundle/index,
audit, Viewer validation and the independent Evidence Reader must understand
the new variant and `unable_to_review`. The schema number is allocated against
the then-current baseline; this document does not activate or reserve a database
migration number.

## Registration And Completion Integration

The typed collected-result transport carries the original bytes and separate
machine binding; it is not forged v1 JSON. Extend the existing public
registration service to decode this version, normalize all entries, and
revalidate the current owner/Contract/target and admitted slot under one writer.
Keep all Receipts, provenance, Findings, References and relevant registration
references atomic. Do not let the collector bypass the public service by using
storage internals.

A batch is ready only when every required selected slot has a valid published
answer and the corresponding admissible terminal turn, no correction/effect is
unresolved, all original/readback/binding checks pass, and the whole batch fits.
Apply the same rules to parent manual submit. A failed slot blocks automatic
whole-batch registration; retain successful sibling originals for explicit
recovery. Do not partially register to work around a failed slot or size bound.

Add `unable_to_review` as its own Receipt verdict in the new protocol/storage
variant. It never counts as PASS and blocks automatic completion. Existing
v1 input is not silently broadened. Carry an effective unable count and a
distinct blocking reason through the shared review-evidence/gate evaluation,
public check/display, finalizer and completion writer. Two other PASS receipts
do not erase an effective unable result; manual submission is not a completion
bypass. Resolve it only through an authorized same-basis reassessment or a fresh
target under the existing history/freshness rules, never by ignoring it or
changing it to NG. Do not add a new manual override. Unresolved Findings, changes-requested
judgments, verification, current target, commit authority and completion gates
remain controlling. A PASS with Findings does not bypass existing finalizer
parent-judgment requirements.

The finalizer reuses existing registration, candidate publication, completion
and parent-report stages. It consumes the collected originals through the shared
transport boundary instead of depending on a reviewer save acknowledgement.
If registration was dispatched but its response was lost, resolve the exact
persisted Receipt/source/digest relation through public read-only inspection
before replay; response absence is not rollback. No extra check is added to the
normal successful path.

Keep original-body retention in the authorized transport directory and
structured evidence in the canonical core/Evidence projections. Neither the hook
nor an operational journal becomes a parallel completion authority. Numerical
usage may consume an actual reviewer binding through its existing boundary;
it must not collect review bodies or control review success.

## Compatibility And Removal

The protocol is fixed at request preparation. New requests after activation use
the new answer route; requests already issued with old save instructions finish
on the old route. Resume/restart uses the saved protocol, never the installed
package's newest default. A single request is not upgraded in place.

Retain old original/sidecar readers and immutable historical Evidence. New and
legacy requests may coexist, but a result from one protocol cannot be accepted
under another by shape guessing. Existing explicit judgment continuation retains
its own basis/selection rules and must carry the source protocol for each
retained original.

Remove the reviewer-facing `save` command entry and its generated invocation,
JSON shell carrier, save-success acknowledgement and Skill instructions only
after all supported outstanding old requests are known settled. A missing or
unknown old-request inventory is not proof of drainage. Upgrade/admission must
preserve the old route or defer removal rather than strand a running review.
Do not delete old originals, result readers or audit compatibility as cleanup.

Keep `read`, `material`, `workspace prepare/cleanup`, parent `submit`,
existing finalization commands and shared original-persistence/validation
functions. Update read's answer instructions for the selected request protocol;
do not alter its material boundaries. No reviewer command is replaced with a
new command that performs the same answer-saving job.

## Verification And Adoption Gates

This design Task checks the documented contract and routing and requires two
independent Tier 2 reviews of its exact material. Those checks do not establish
real-host support. The separately authorized implementation must update the
owning specifications/designs, JSON/error contracts, database/admission/Evidence
readers, helper guidance, Setup selection, and package tests together.

| Area | Required regression/acceptance evidence |
|---|---|
| Decoder | Bare JSON and sole json fence; Japanese/quotes/newlines; all five fields; duplicate/unknown keys; invalid Unicode/numbers/types; privacy precedence |
| Limits | Original/event/escaped envelope/batch limits independently; 1–8 slots and combined 64 Findings; no truncation, partial registration or demand to remove Findings |
| Binding | Parent differs from reviewer; wrong session/turn/project/Task/Contract/execution/target; alias; answer-before-binding race; stale generation |
| Role attestation | Explicit independent pass/NG under the fixed request; unknown role becomes unable; missing attestation or conflicting structural role never becomes qualifying PASS from assignment; fallback approval remains separate |
| Publication | Exact fenced original; payload digest; exclusive paths; readback mismatch; interruption before/after create/reference commit; retained partial residue |
| Idempotency | Repeated hook and fallback same bytes; conflicting bytes for same turn; delayed initial answer after correction; restart without duplicate side effects |
| Correction | One same-reviewer request; unavailable/failed/unknown send; accepted but unbound turn; invalid resubmission; original deadline; sibling preservation; finalizer race |
| Workerless paths | Waiting OFF/unavailable/parent-managed accepts normal complete answers; one exact missing-answer recovery call then parent failure; no observer means no automatic correction send; lost owner preserves unresolved accepted/unknown send |
| Outcomes | PASS, content NG, unable, format, privacy, capacity and collection failure remain distinct; two PASS plus an effective unable blocks the shared gate, manual completion and automatic completion until legitimate supersession |
| Provenance | Actual vs requested model/Skill/method; unknown vs explicit empty; no v1 fabrication; mixed v0/v1/v2 history and independent Reader validation |
| Registration | Whole-batch rollback; concurrent target/owner/Contract change; lost post-commit output; exact public reconciliation before retry |
| Compatibility | Old issued request completes; new request omits save; restart keeps protocol; unresolved legacy drainage defers removal; retained original/reassessment rules |
| Host | Exact reviewer-turn binding and complete answer; direct MCP correction to native reviewer; durable correction turn; trusted hook absence; parent idle wake and restart |
| Permissions | No setup/trust/Git authority from hook; no full transcripts, new timers, arbitrary paths or numerical-hook coupling |

The host gate must name the actual Codex version, adapter, launch settings,
verified public fields, original/correction-turn evidence and failure behavior.
Mocks alone cannot satisfy it. Exact answer completeness and source binding are
mandatory: if either is unsupported, do not activate the new route on that
host; retain the existing supported route and report the capability limitation.
Correction addressability is a separately reported capability. A host that
supports exact collection but cannot send/bind a correction can collect normal
answers; a malformed answer follows the agreed format-failure path to the
parent. Test that unavailable-correction path before activation. Neither case
permits weaker identity checks or an invented transcript fallback.

Before implementation begins, resolve the concrete supported host adapter and
complete the closed v2 provenance/transport schema against that baseline.
Those technical activation details remain explicit implementation prerequisites;
the approved product decisions (five answer fields, one correction, unknown
preservation, manual parent path, and answer-command-only removal) are fixed.
Any change to those decisions requires new authority. No implementation Tasks,
runtime changes, or further phase backlog are created by this document.

## Concrete Host Admission Baseline

The inspected baseline is Windows Codex Desktop package `26.1007.9446.0`,
bundled `codex-app-tools` directory version `0.1.6`, and its `server.mjs` SHA-256
`064893183a9cc09953a1f69ed82dfb4299b4f7e1653a3507259deeb5abbb9617`.
The separately installed command reports `codex-cli 0.160.0`; this is not proof
of the Desktop's embedded runtime version. These are inspection identities,
not supported-version claims. A package directory version is also distinct
from the server's self-reported MCP version (`0.1.0` in that source).

The candidate adapter is `codex-app-tools-review-answer/1`, using the connected
public `read_thread`/`wait_threads` surfaces and the dedicated SubagentStop
event. Its current admission is **unavailable**. No host version is certified
for this answer route by this document. The following distinctions prevent
the subsequent implementation from treating a read probe as a passed gate:

| Surface | Established observation or documented contract | Missing required evidence |
|---|---|---|
| SubagentStop | Official fields identify an agent and an active turn and optionally carry its latest assistant message; common `session_id` belongs to the parent | The event does not document that its turn is the assigned child's review turn, that its message is the complete final answer, or that the child successfully terminated |
| `read_thread` | The public input selects a thread and bounded recent turns/cursor, with `includeOutputs` and `maxOutputCharsPerItem`; a read of one known ended reviewer returned schema 1, exact thread/turn IDs, completed status and an `agentMessage.text` string | It has no exact-turn input or documented final-answer completeness contract; the observed short string does not prove large-answer fidelity or dispatch-to-child-turn correlation |
| `wait_threads` | Existing adapter validates returned child/turn identity and terminal state | A latest-turn observation is not the original dispatch binding or complete answer |
| Native agent dispatch | Actual returned handles and parent dispatch activity can establish the child; current wait adapter already rejects ambiguous aliases | The available dispatch return does not provide an admitted initial child-turn ID. A later newest turn cannot supply it by inference |
| `send_message_to_thread` | Public tool accepts an authorized destination and prompt | Its schema does not promise native subagent addressability or the resulting exact correction-turn ID; this was not tested by a send |
| App Server | Official `agentMessage` and `item/completed` describe finalized items and optional `phase`; public `turn/start` is a separate surface | Access from this connected product to that same Desktop-owned reviewer, exact source recovery and lifecycle continuity has not been established; launching another server is not a substitute |

Sources: [Hooks common fields](https://learn.chatgpt.com/docs/hooks#common-input-fields),
[SubagentStop](https://learn.chatgpt.com/docs/hooks#subagentstop), and
[App Server items](https://learn.chatgpt.com/docs/app-server#items) and
[turn start](https://learn.chatgpt.com/docs/app-server#start-a-turn).
The read-only probe retained no transcript, tool output or answer body in the
repository. It observed an existing reviewer only; no new review, correction,
hook, timer, trust or configuration was created. The installed server source
is a dynamic public-tool relay, not an answer-specific implementation or a
contract for the Desktop's hidden implementation.

Before enabling or starting the dependent integration, certify the adapter's
two mandatory capabilities: exact dispatch-to-parent/child/turn binding, and
complete final-answer retrieval. They remain unresolved on this baseline.
Do not implement a guessed latest-turn fallback while awaiting that evidence.
A future public adapter may satisfy them, but selecting a different host
surface requires revisiting this baseline and its acceptance evidence first.
Correction send/binding is a separate optional capability: its absence returns
`correction_observer_unavailable` or `correction_transport_unavailable` as
appropriate and does not disable otherwise certified normal collection.

### Public Adapter Boundary

The following is the closed internal contract required of that adapter, not a
claim that today's host tools implement it. No new public host method is
invented. All operations use the existing bounded transport timeout, at most
one exact recovery call for a missing slot, and the original wait deadline
when present. They neither traverse history to find a plausible answer nor
retain provider envelopes.

- Dispatch binding input is `{basis_digest, slot, dispatch_id}`; success is
  `{parent_session_id, reviewer_session_id, initial_turn_id, source}`. The
  public observation must bind the saved dispatch to all three identities.
  A missing or ambiguous relation returns `collection_unavailable`; knowing
  the parent or child alone cannot manufacture a successful result.
- Answer observation input is `{basis_digest, slot, attempt,
  reviewer_session_id, turn_id, expected_message_id}`. Session and turn must
  already match the admitted dispatch or correction binding. The expected
  message ID is null when no final item was observed, including lost-hook,
  answer-before-binding and workerless recovery. In that case this one bounded
  exact-turn operation must identify exactly one complete final item and return
  its ID; zero or ambiguous final items returns `collection_unavailable`.
  A non-null expected ID additionally requires exact equality. No latest-turn
  selection, history traversal or preliminary message-ID lookup is permitted.
  Success is `{source_identity, answer_bytes, complete:true, terminal,
  answer_source, terminal_source}`. `source_identity` is exactly
  `{reviewer_session_id, turn_id, message_id}`, with non-null message ID;
  `terminal` is `in_progress|completed|failed|interrupted|unknown`.
  `answer_source.operation` is `answer_observation`; terminal source is null
  exactly for unknown, otherwise its operation is `terminal_observation`.
  Both observations bind the returned session/turn; answer observation does
  not infer terminal state. Only an
  actual complete final-answer item may provide `answer_bytes`; a summary,
  tool response, concatenated commentary or clipped prefix never does.
  Publication and terminal observation remain separate; registration requires
  `completed`. A changed source or multiple ambiguous final items fails closed.
- Terminal-only observation input is `{basis_digest, slot, attempt,
  reviewer_session_id, turn_id}` and success is `{reviewer_session_id, turn_id,
  terminal, terminal_source}`, with the same terminal/source rules. It observes
  the admitted turn without retrieving an answer or selecting a newer turn.
- Correction dispatch input is `{basis_digest, slot, initial_turn_id,
  correction_intent_id, diagnostic}` plus the fixed protocol request. Diagnostic
  uses the closed failure object below. Success
  is `{effect:accepted, correction_turn_id, source}` only if the same public
  operation establishes the exact new turn. Other results are
  `{effect:not_sent|failed|unknown, correction_turn_id:null, source:null}`.
  Accepted transport without a proven turn returns
  `{effect:accepted,correction_turn_id:null,source:null}` and cannot admit an
  answer until an exact observation resolves it.
  This is a pending effect, not permission to send again.

`source` is `{adapter:"codex-app-tools-review-answer", version:1,
operation:"dispatch_binding"|"answer_observation"|"terminal_observation"|
"correction_binding"}`. It is assigned by the certified adapter, never supplied
by the reviewer or copied from hook `model`. These source labels describe
structural observations, not authentication. There is currently no admitted
producer for them. The API must drop unrelated message and reasoning content
before returning its closed result. An oversized or incomplete provider result
returns `collection_unavailable`; it never reaches the JSON decoder as a
complete malformed answer.

Adapter failures return exactly `{error:{class,field,reason}}` using the failure
object below; a successful operation never includes `error`. `answer_bytes`
is an internal byte string, not a JSON numeric array; a public transport uses
the canonical base64 field defined below. The operation schemas do not expose
the underlying provider response.

## Closed Collection Values And Records

These are conditional semantic storage/public schemas. They do not allocate a
core migration number, add a public command, or activate a new writer. Every
listed key is mandatory, including explicitly nullable keys; every object is
closed. Arrays preserve order except where the current owner specifies a set.
All integers are exact signed-64-bit JSON/SQLite integers, never Booleans or
floats. `positive` means 1 through 2^63-1; `nonnegative` includes zero. UUIDs
use the existing canonical session/turn UUID validator. Tool IDs retain their
existing prefix plus sixteen lowercase hex grammar; a new correction intent
uses `tg_review_correction_` with that suffix. Host dispatch/message IDs are
1-256 ASCII characters matching `[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}`.
Digests below are `sha256:` followed by 64 lowercase hex characters.

Reuse the current canonical UTF-8 JSON codec and privacy policy. No BOM,
non-finite number, float, lone surrogate, duplicate or unknown key is accepted.
Envelope nesting is at most 16 containers; answer nesting is at most three
(object, findings array, Finding object). Enforce bounded input and structural
types first, scan every typed string for privacy next, then enforce enums,
text/count bounds, duplicate semantics and binding. A structurally malformed
answer has no retained raw body; privacy rejection stores only a fixed reason.
No failed body, unknown field name or exception is echoed. Counts/lengths use
Unicode scalar values after valid UTF-8 decoding, not UTF-16 units.

### Preparation And Slot Binding

`CollectionBasis` has exactly these fields:

```text
version=1, preparation_id, packet_digest, request_digest,
protocol="review-answer-v1", attestation="assigned-independent-review-v1",
route="manual"|"integrated", continuation, execution_basis
```

`execution_basis` is exactly the closed version-one basis in
[continuation storage](database-design.md#conditional-review-continuation-storage),
with its existing field types, nullable criteria and 16,384-byte bound. This
reuses a value schema, not a requirement to create a continuation. Require a
known execution, ownership generation and capture-version-one current target;
the target's manifest and authority/criterion IDs must match the saved Packet.
`preparation_id` identifies the actual existing preparation. `continuation` is
null for an ordinary request, otherwise exactly `{continuation_id, sequence,
selection_digest}`, with sequence 1..64 and the existing admitted selection.
`packet_digest` hashes the unchanged complete Packet bytes; `request_digest`
hashes the exact UTF-8 request delivered to that reviewer, including the
protocol's independence attestation. Where requests differ by slot, a separate
CollectionBasis is captured per slot under the same preparation; all other
basis fields must match. Canonical basis bytes are capped at 32,768.

An immutable `SlotBinding` is exactly `{basis_digest, slot, reviewer_key,
dispatch_id, parent_session_id, reviewer_session_id, initial_turn_id,
binding_source, assignment}`. `slot` is 1..8, unique and contiguous for the
prepared slots. `reviewer_key` uses the existing normalized nonempty 500-character
bound; keys and actual reviewer sessions must be distinct. It cannot be a
label inferred from answer prose. The parent/execution must match the basis,
the child must differ from the parent, and the exact initial turn must come
from admitted dispatch observation. A slot remains unbound until all fields
are known; an answer-before-binding event is not retained for speculative
matching. After binding, only the exact recovery operation may obtain it.

`assignment` is exactly `{kind:"independent", review_profiles, review_lenses,
requested_model, requested_effort, requested_skill, context_relation}`.
Profiles/lenses use existing fixed-order sets and limits. The other fields
are explicitly recorded launch requests or null when absent; model uses the
existing 128-byte identifier grammar, effort is one of the actual supported
launch values `none|minimal|low|medium|high|xhigh|max|ultra`, Skill is
`{id,version}` using existing identifier/version bounds, and context uses the
existing context-relation vocabulary. Null is unknown, not an empty request.
None of these values becomes an observed fact or performed method.

### Attempts, Originals And Effect State

The operational record is exactly `{version:1, basis, binding, attempts,
correction, registration}`. `binding` is null until admitted. `attempts` has
at most two entries, in ordinal order, exactly `{ordinal, turn_id,
source_identity, observation, observed_digest, terminal, terminal_source,
publication, failure}`:

- `ordinal` is 0 (initial) or 1 (format correction); 1 requires the one saved
  correction intent. `turn_id` is null only before its exact binding.
- `source_identity` is null or the complete three-field identity above;
  its turn/session must match the admitted attempt. `observation` is
  `missing|complete|unavailable|conflict`. Only `complete` supplies an original
  candidate. For either ordinal, `observed_digest` is the exact complete UTF-8
  answer's SHA-256 digest once its identity, byte bound and privacy admissibility
  are established. Retain it even when semantic/format validation fails and
  publication and correction are null, including invalid ordinal-one answers.
  Each attempt's `publication.original.digest`, when present, must equal that
  same attempt's `observed_digest`. `correction.initial_digest` must equal only
  `attempts[0].observed_digest`; it is never compared for equality with ordinal
  one's digest. A corrected answer may and normally will have different bytes.
  A subsequent equal digest for that source is idempotent; a different digest latches
  conflict without replacing the first digest, source or original.
  This comparison precedes any repeat correction/publication admission.
  Missing/incomplete observations and rejected or unestablished privacy have
  null digest and cannot be called identical by comparing nulls. For a complete
  bound answer whose syntax/shape prevents the existing privacy policy from
  establishing admissibility, retain only its source and sanitized terminal
  failure, never its body/hash or a correction intent. Later arrivals for that
  failed attempt return its failure without new effects; explicit parent
  recovery is required. This fail-closed case is not silent duplicate success.
- `terminal` is the adapter terminal enum. Unknown is never treated as ended;
  failed/interrupted are distinct from successfully completed. `terminal_source`
  is null exactly for unknown; otherwise it is an admitted source with operation
  `terminal_observation` for that same bound source turn.
- `publication` is null before an intent, otherwise exactly
  `{state:"intent"|"saved"|"unknown", original}`. `OriginalRef` is exactly
  `{path, byte_length, digest, payload_start, payload_length, payload_digest}`.
  Path is a prepared repository-relative ignored physical path using the
  existing transport grammar/limit, never supplied by the answer. Byte length
  is 1..262144; offsets are nonnegative, payload length positive and its end
  cannot exceed the original. Both endpoints must be UTF-8 scalar boundaries.
- `failure` is null or `{class, field, reason}`. Class is from the failure table
  above; field is null or a closed answer path (`verdict`, `summary`, `findings`,
  `findings[].severity`, `findings[].summary`, `checks`, `checks[]`, `limitations`,
  `limitations[]`). Reason is `shape|type|choice|unicode|duplicate|bound|privacy|
  missing|incomplete|identity|conflict|effect_unknown|observer_unavailable|
  transport_unavailable`. Class/reason must agree with the actual failed stage.

`correction` is null or exactly `{intent_id, initial_source_identity,
initial_digest, effect, correction_turn_id, binding_source, failure}`. Digest binds the
privacy-admissible complete initial answer without retaining its invalid body.
Effect uses `not_sent|accepted|failed|unknown`; turn is null until proven, and
non-null requires accepted and a source with operation `correction_binding`;
binding_source is null while turn is null. A recorded intent consumes the one automatic
correction opportunity, including a definite pre-dispatch failure; no new intent
or second send is created. The ordinal-one result never overwrites ordinal zero.

`registration` is exactly `{state:"not_started"|"dispatched"|"registered"|
"unknown", submission_digest, receipt_id}`. Digest is null only before
dispatch; Receipt ID is non-null only for a publicly confirmed registration.
State cannot revert to not_started after a possible dispatch. Inspection can
resolve an unknown exact effect, not retry it. Matching public Receipt source
and submission digests prove registered; absence is not by itself rollback.

The complete operational metadata record, including the basis and binding,
must fit 32,768 UTF-8 bytes before any effect. It stores no answer fields or
body. Slot/attempt limits cannot be evaded by new record IDs. Each preparation
has at most eight bindings and sixteen attempts, with no growing observation
array. A nullable publication reference retains known residue without deletion.
Core evidence below stores answer fields separately; its larger allowance does
not enlarge this metadata bound.

For bare JSON, the payload range excludes only surrounding JSON whitespace.
For a sole `json` fence, it excludes fence lines and surrounding whitespace;
LF and CRLF fence lines are accepted, with no indentation or suffix on either
fence. The range is the exact JSON object's bytes, never a reserialization.
The original is the UTF-8 encoding of the complete delivered Unicode answer,
not a claim to preserve inaccessible provider wire encoding. No trimming or
Unicode normalization occurs in either original or payload digests.

Publication first commits the intent/reference metadata, then revalidates the
full current basis and privacy, exclusively creates/flushes/reads the exact
file using `review_handoff_transport`, and finally commits `saved` after
revalidating identity and intent. A pre-file failure is not saved. A post-create
uncertain effect is unknown with residue; reconcile only the exact saved intent
and bytes. At submission, readback, Packet and reference comparison run again
for every required original before dispatch to the public writer. These are
the two distinct confirmation boundaries; a save acknowledgement cannot replace
the second. No file or host I/O runs under a core SQLite writer.

### Collected Transport And Provenance V2

Use a new format discriminator, not the existing continuation sidecar's integer
`version=2`. The stdin transport is exactly:

```text
{format:"taskgov-collected-review-results-v1", preparation_id,
 continuation, items, retained_receipt_ids, retained_originals}
```

`continuation` is the nullable CollectionBasis continuation value. Items are in
prepared slot order, one for every required unregistered slot, including
retained unregistered originals. A collected item is exactly
`{kind:"collected",slot,original_base64,metadata}`. A retained legacy item is
exactly `{kind:"legacy",slot,source_packet_digest,original_base64,binding}`,
where binding is the unchanged closed version-one or version-two sidecar from
the [existing transport](review-completion-specification.md#reviewer-session-binding)
and [continuation transport](review-completion-specification.md#continuation-transport-and-failure-contract).
The helper verifies that source Packet and sidecar before framing; the writer
matches its admitted continuation capture and never trusts the digest alone.
Legacy is allowed only for a retained unregistered original, not a newly
assigned slot under the new protocol. `retained_receipt_ids` retains the
existing continuation-envelope shape, ordering and validation.
`retained_originals` uses the closed union below. Both are empty without a
continuation. Registered retained judgments supply their existing IDs, not
new input Receipts.
`metadata` is exactly `{basis, binding, attempt, source_identity, original,
answer_source, terminal:"completed", terminal_source, correction_binding}`.
Attempt is the accepted ordinal. The source identity is the exact admitted
answer identity, not merely the initial turn in SlotBinding. Answer and terminal
sources have the corresponding operations above. `correction_binding` is null
for ordinal zero; ordinal one requires exactly `{intent_id, initial_turn_id,
correction_turn_id, source}` matching the one accepted, bound correction and
its source identity, with operation `correction_binding`. These machine facts
are captured from the admitted operational record, never supplied by answer
JSON. The other values use the closed definitions above. Canonical padded
base64 contains exactly one original, with no whitespace or alternate alphabet.
The public decoder checks size before decoding, complete original and payload
hashes/ranges, all five answer fields, current slot publication/terminal facts
and admitted sources; metadata alone grants nothing. Canonical framing has no
original path supplied by the reviewer and cannot override its prepared path.

One through eight items, at most 64 combined Findings and the entire serialized
262,144-byte stdin bound include all base64/metadata/escaping. Existing retained
originals retain their own versioned sidecars and unchanged bytes under the
explicit legacy arm. A mixed-protocol continuation reuses existing typed
composition and exact old Packet/sidecar references; it cannot reinterpret a
legacy answer as a collected payload or manufacture v2 provenance for it.
The existing whole-batch count/size limit includes retained unregistered items.
No public registration accepts a partial selected set.

`retained_originals` contains at most eight entries, sorted by reviewer session
ID, with no duplicate session across either arm. A legacy entry is the exact
existing four-key capture, without a new tag or changed hash domain. A collected
entry is exactly `{kind:"collected", consumer_continuation_id, consumer_slot,
source_preparation_id, source_continuation_id, source_slot, session_id,
source_basis_digest, original_digest, metadata_digest}`. Continuation IDs use
the existing grammar; source continuation alone may be null for an unchanged
initial request. Slots are 1..8; other IDs/digests use the definitions above.
The outer item's slot is the consumer slot; its unchanged metadata retains
the source slot and source basis. They are equal for newly selected items.
No source field is rewritten to look like a newly dispatched review.

The collected metadata seal is SHA-256 over
`taskgov-collected-review-metadata-v1\0` plus canonical JSON of the complete
closed metadata object above, including the original path and exact bound
correction/terminal facts. Native capture stores the complete canonical
metadata (at most 32,768 bytes) with that seal and the capture fields, under
the consumer continuation/session key. Its result Receipt link is null until
atomic registration, then immutable. Original bytes stay in their source
transport file; the capture contains no answer body. Legacy capture storage,
`continued_metadata_digest` and `bound_metadata_digest` remain unchanged and
must never validate the collected arm by reconstructing a legacy sidecar.

Capture admission requires a valid published source with completed exact turn,
no unresolved source correction/save/registration effect, known settled old
wait cleanup, and the existing explicit continuation/supersession authority.
Source preparation must equal the consumer's root preparation. A non-null
source continuation must be a superseded ancestor of that consumer; a null
source continuation must be the same preparation's still-valid initial slot.
All execution-basis fields, route and protocol remain equal; only the captured
request/Packet/continuation/selection identities and source slot retain their
old values. The source session must occupy exactly one required consumer slot,
must not be selected anew, and cannot already have a registered result being
silently replaced. The source metadata seal, basis digest, original digest,
source Packet and core capture must all match. If the consumer is superseded
again before registration, transfer a capture to its descendant without losing
the original source identity/metadata; preserve the earlier capture as history.
Every envelope capture must consume exactly one supplied original and every
old-context collected original must have its exact admitted capture. A missing,
extra, aliased, cross-basis or differently sealed relation rejects the whole
batch. The physical metadata seal is native-only. New continuation Evidence
uses the distinct path-free projection below; the independent Reader must not
claim to recompute the native seal without its private input. Preserved legacy
capture rows, public projections, hash domains and sealed graphs are unchanged.

For a collected capture, define `source_metadata` by taking exactly the closed
collected metadata object above and removing only `original.path`. It retains
the complete basis, binding, accepted attempt/source identity, all other
OriginalRef fields, answer/terminal sources and nullable correction binding.
No path placeholder, path hash, original body or provider envelope is added.
Define `capture_identity` as exactly the collected envelope capture fields
above except `metadata_digest`. The new frozen public capture row is exactly
`{capture_identity, source_metadata, evidence_digest, result_receipt_id}`.
The nullable Receipt link retains the native capture's actual value. The entire
row is bounded to 32,768 canonical UTF-8 bytes before capture admission.
Public capture rows order by consumer continuation ID and session ID, using
the existing unsigned UTF-8 ordering. The outer Bundle's existing whole-input
bound remains controlling; an oversized projection rejects the operation.

`evidence_digest` is SHA-256 over
`taskgov-collected-review-capture-evidence-v1\0` plus canonical JSON of exactly
`{capture_identity,source_metadata}`. It excludes the subsequently published
Receipt link, whose one-time publication and graph relation are validated
separately. At native capture creation, derive this projection from the same
validated metadata used for the physical seal and store both seals/projection
atomically. Source metadata and both seals are immutable for that consumer;
transfer to a new consumer derives a new capture identity/evidence digest while
preserving the original source metadata. Sealing and native reentry compare
the frozen projection with that exact deterministic native projection. They
do not accept independently supplied public metadata or an opaque seal as proof.

The independent Reader uses only the frozen graph. It recomputes the public
evidence digest and source basis digest, validates every nested closed type and
cross-field binding, and enforces the source/consumer ancestry, same complete
execution basis, slot/session uniqueness and one-to-one consumption rules above.
The source metadata's basis preparation/continuation IDs and binding slot/session
must equal capture_identity's corresponding source fields, preserving null
initial continuation; its original digest must equal the captured original digest.
Correction ordinal one requires the exact
initial/accepted-turn relation; ordinal zero requires null correction binding.
For each consumed capture, compare all overlapping fields with the linked v2
Receipt's provenance: request/protocol/attestation, assignment, original/payload
metadata, source basis, preparation/slot/attempt, parent/reviewer/turn/message
and observation sources. Require the matching existing session binding and
original digest, the registered consumer and its exact Receipt relation. Every
old-context collected Receipt requires one consumed capture; null links are
allowed only in superseded historical consumers. No pending consumer enters a
sealed graph. Recomputing a digest cannot excuse inconsistent ancestry, source,
correction or Receipt relations. These checks establish declared graph
consistency, not host truth or inaccessible answer/Packet bytes. The Reader
neither reads transport files nor validates the private physical seal. The new
versioned Evidence graph must carry this closed arm; existing graph versions
are never broadened or rewritten in place.

Persisted/public v2 `review_provenance` is exactly:

```text
{review_provenance_id, provenance_version:2,
 answer_attestation:{protocol,attestation,request_digest,original,payload},
 assignment, host_observation, assurance_class:"bound_attestation",
 producer_class:"taskgov_answer_collector", producer_version:1, digest}
```

The ID retains the existing provenance grammar. `payload` is exactly the five
decoded answer fields, preserving all sanitized strings and array order,
including explicit empty checks/limitations. Core Receipt/Finding summaries
use current normalization; keep the raw sanitized payload strings separately
and verify their normalization equals those rows. Duplicate normalized Findings
remain invalid; no Finding is dropped. The `original` in durable provenance is
the OriginalRef **without `path`**, so private transport paths never enter
Evidence. `protocol`, `attestation`, request digest and assignment equal the
saved request. Unable carries the request's attestation definition but makes
no completed-review/independence assertion. PASS/NG supplies the explicit
reviewer attestation only under the fixed request; a conflicting structural
role rejects registration. There is no free-text role classifier.

`host_observation` is exactly `{basis_digest, preparation_id, slot, attempt,
parent_session_id, reviewer_session_id, turn_id, message_id, binding_source,
answer_source, terminal_source, model, skill, methods}`. Exact required
identities and three sources must be known; unknown mandatory binding cannot
be persisted as an eligible result. Optional runtime facts `model`, `skill`,
and `methods` are exactly `{state:"unknown",value:null,source:null}` for this
adapter version: no inspected public surface establishes their actual use
throughout the review. Future observed variants require a reviewed versioned
source allow-list and value schema first, not arbitrary strings in v2. An
explicit empty answer array remains in payload; legacy absence remains v0;
not-applicable remains a legacy applicable route, not an inferred runtime fact.
Requested model/Skill and free-text checks cannot fill these unknowns.

The provenance object has the existing 262,144-byte individual-original cap as
its own upper bound; the full Bundle cap remains separately controlling.
Digest uses `taskgov-review-provenance-v2\0` plus canonical JSON of exactly
`{project_id,task_id,review_receipt_id,receipt_kind,target,provenance}`. Target
has the current five fields including capture version; `provenance` contains
every v2 public field except its random ID and digest. Basis digest uses
`taskgov-review-answer-basis-v1\0` plus the complete CollectionBasis. Submission
digest is SHA-256 of the exact dispatched transport bytes. Original/payload,
Packet and request digests hash their exact bytes with no domain prefix.
Never reinterpret existing unprefixed sidecar hashes or v1 domain digests.

### Public Registration Reconciliation

Lost-output recovery uses a conditional extension of existing `task show
<task-id> --audit --json`, with paired options `--collection-preparation <id>`
and `--collection-submission <digest>`. These options are not available before
adoption. They require each other and `--audit`, use the same admitted project,
and accept only the preparation grammar and exact prefixed SHA-256 digest.
They neither read answer files nor invoke registration, a host, migration or
maintenance. Ordinary show/audit without the pair retains its existing shape.

With the pair, add exactly `collection_registration` to existing data. It is
`{status, preparation_id, submission_digest, current_basis, entries, reason}`,
at most 32,768 canonical UTF-8 bytes. Status is
`recorded|absent|conflict|unknown`; current_basis is Boolean for recorded and
null otherwise. Reason is null for recorded/absent or exactly
`source_mismatch|partial_inventory|inventory_unavailable|capacity` for the
corresponding conflict/unknown. Entries is empty except for recorded, when it
contains the complete 1..8 dispatch entries in submitted order. Each entry is
exactly `{slot, protocol, receipt_id, project_id, task_id, contract_revision,
target, source_preparation_id, source_continuation_id, reviewer_session_id,
turn_id, message_id, basis_digest, original_digest, payload_digest,
submission_digest}`. Target is the existing five-field target including capture
version. Collected entries use protocol `review-answer-v1` and all known source
fields. Legacy entries use `legacy-review-result-v1`, retain their actual
nullable preparation/continuation binding, and have null turn/message/basis/
payload digests; they never acquire invented collected provenance. Slot refers
to the consuming dispatch, not a rewritten original slot.

The public repository reads the exact durable dispatch inventory and immutable
source/Receipt relations in one consistent core snapshot, independent of the
bounded recent-Receipt display. Record that bounded inventory and every typed
entry's submission digest atomically with registration, including legacy items
in a mixed batch; this adds a relation, not a modification to legacy assertion
bytes/digests. The decoder/repository derives the entries from validated originals,
never from caller claims about a saved result. All expected entries and exact
digests matching yields recorded, even if later Task/target state changed;
current_basis then says false and does not authorize current completion.
No exact committed inventory yields absent, which is only absence in that read
snapshot, not proof that a dispatched writer rolled back. A known inventory
with mismatched or incomplete source relations yields conflict; unreadable or
over-capacity inventory yields unknown without a prefix. Neither permits retry.
Resolve registration_unknown to registered only after recorded matches the
retained exact operation, complete inventory and all source digests. A caller
must separately establish non-commit/settled effects before any other recovery;
an absent read alone never resets dispatched/unknown to not_started.

The normal successful registration response remains sufficient; this lookup is
an explicit failure-recovery operation, not a new normal-loop prerequisite.
It reports structural registration facts, never review truth, verification or
Task completion. The review/completion public-read owner, Task audit projection,
and database source-relation owner must adopt this exact extension together.

### Core Persistence, Gates And Adoption Owners

The next explicit Setup migration must add a separate immutable v2 provenance
relation keyed one-to-one by Receipt ID, with the complete bounded canonical v2
JSON and digest, plus a unique collection source relation
`(preparation_id,slot,attempt,reviewer_session_id,turn_id,message_id)` linking
Receipt, basis digest, original digest, payload digest and submission digest.
The original bytes remain only in authorized ignored transport files. Retain
v1 normalized rows untouched; Receipt version 2 requires exactly one v2 row,
version 1 exactly its existing v1 row, version 0 none. All relations and new
Findings/References commit under the existing public writer in one transaction.
Read admission/reentry validates exact version/kind/source/target/digest matrices
and rejects orphan, duplicate or mismatched relations as inconsistent Evidence.
Physical DDL, migration numbering and exported Bundle version are assigned
against the implementation's current baseline, not by this conditional spec.

`unable_to_review` is admitted only for v2 independent Receipts. All shared
review consumers derive `unable_current_generation` from validated effective
heads, with distinct blocking code `review_unable`; two other PASS results do
not hide it. In gate priority it follows current changes-requested and unresolved
blocking Findings, before insufficient PASS count. Manual and automatic
completion call the same predicate. A valid unable may register with Findings;
it can only cease blocking through legitimate new-target history or existing
same-reviewer reassessment. No new override or implicit Finding resolution exists.

| Current owner / coupled implementation | Required adoption change and acceptance |
|---|---|
| [Structured registration](review-completion-specification.md#structured-review-results), `review_results.py`, `review_session_transport.py`, `reviews.py` | Closed collected arm and exact source lookup; atomic all-or-none receipt/provenance/Finding/reference registration; valid unable plus two PASS still blocks every completion path |
| [Original transport](review-completion-design.md#structured-result-registration), `review_handoff_transport.py`, `review_handoff_originals.py`, preparation/finalization repositories | Shared physical checks and two confirmations; immutable request protocol; initial/correction attempts kept distinct; existing finalizer consumes exact originals without a second finalizer |
| [Evidence provenance](evidence-specification.md#versioned-review-provenance-and-bundle-boundary), `review_provenance.py`, ledger/projection and [independent Reader](evidence-design.md#test-only-independent-evidence-reader) | Explicit v2 dispatch and digest, complete checks/limitations and all Findings; references/bundles carry new assurance/producer without upgrading attestation; old v0/v1/v2/v3 Bundle bytes/digests remain exact |
| [Persistence](database-specification.md#current-schema-v28-persistence-contract), [migration design](database-design.md#migration-sequence), schema/admission/recovery/Viewer validators | Transactional predecessor migration, rollback/reentry, exact old rows and sealed bytes preserved; no synthesized provenance or actual-use claims; independent Reader rejects recomputed corrupt graphs, not only bad hashes |
| [Setup choice](setup-state-specification.md#optional-feature-selection), hook definitions and review-wait host adapter | Separately selected answer retention/correction permission and host trust; numerical hook choice cannot enable it; unresolved host capability keeps route unavailable; no ordinary-command config writes |
| [Current wait](review-completion-specification.md#host-owned-review-waiting) and continuation composition | Bind each correction to one slot and original deadline, retain siblings; no-worker normal collection/manual registration; one exact recovery then failure, no automatic correction without its observer; unknown effects never replayed |
| Skill/helper guidance and release manifest | Five-field final answer only for admitted new requests; old answer-save route remains until known drainage; read/material/workspace and parent submit retained; package and document checks agree |

Extend the acceptance table above with exact-boundary cases: each required key
missing/extra, each integer replaced by bool/float, 32,768/262,144-byte edges,
Unicode scalar versus byte offsets, canonical/noncanonical base64, altered
original/fence/payload/request/source digest, reused child under another turn,
accepted correction without turn, save interrupted at both commit boundaries,
registered-source replay with lost output, and v2 evidence mixed with immutable
legacy history. Specifically cover lost-hook and workerless recovery beginning
with null expected_message_id, wrong known message ID, zero/multiple final
items, terminal-only observation, unpublished invalid initial/corrected answers
with identical/conflicting digests across restart, an invalid initial digest
`D0` followed by a valid corrected digest `D1 != D0`, and privacy-unknown failure
without retained bytes/hash or automatic effects. Carry a collected original
through two pending-round supersessions and a mixed legacy batch; tamper its
metadata seal, source/consumer slot, ancestor, correction and completed-turn
facts independently. Export a retained corrected original without its transport
path or physical seal, validate the public projection with no transport-file
access, and reject inconsistent correction, ancestry or Receipt/source relations
even after an attacker recomputes the public capture and outer Bundle digests.
Reconcile a lost registration response outside the recent
Receipt window, after target change, and with absent/partial/conflicting source
relations; none may trigger a blind replay.
Restart must recover the exact protocol/source/effect state;
it cannot manufacture missing bindings. Actual host certification additionally
needs a known dispatch-to-initial-turn relation, large/escaped/Japanese complete
answers, a lost-hook exact recovery, unrelated newer turns, optional correction
addressability or its explicit unavailable path, idle parent delivery and
restart. Record actual Desktop/runtime/plugin versions and launch settings for
each observation; mock tests satisfy only the deterministic contract.

The documentation prerequisite can report the negative host-admission result
honestly. Its completion does not satisfy the dependent integration's start
gate: that gate remains closed until the two mandatory capabilities above are
established through the same supported public adapter and reviewed contract.

