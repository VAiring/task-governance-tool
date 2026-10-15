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
assignment through the current authority route; session alias checks remain.
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
| Publication | Exact fenced original; payload digest; exclusive paths; readback mismatch; interruption before/after create/reference commit; retained partial residue |
| Idempotency | Repeated hook and fallback same bytes; conflicting bytes for same turn; delayed initial answer after correction; restart without duplicate side effects |
| Correction | One same-reviewer request; unavailable/failed/unknown send; accepted but unbound turn; invalid resubmission; original deadline; sibling preservation; finalizer race |
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

