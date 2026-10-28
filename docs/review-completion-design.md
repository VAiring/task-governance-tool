# Review And Completion Implementation Design

This document owns typed completion, review target/receipt/finding gates,
Review Packets, completion-cycle capture and reopen, and manual Verification
Receipt integration delegated by the
[implementation design](design.md#completion-evidence-and-review).
Product behavior belongs in the
[Review and completion specification](review-completion-specification.md).
[Evidence construction and formats](evidence-design.md) and
[ordinary Task state, Contracts, and Checkpoints](task-operation-design.md)
retain their separate owners. Shared [runtime ownership](design.md#runtime-module-boundaries),
[CLI/serialization](design.md#public-cli-and-serialization),
[connection/transaction rules](design.md#journal-and-connection-rules),
[migration](database-design.md#migration-sequence),
[current persistence](database-design.md#schema22-reservation-cleanup-design),
[the schema-v21/v22 Runner gate protocol](database-design.md#schema21-runner-gate-basis-design),
[post-commit coordination](setup-state-design.md#post-commit-coordinator),
[privacy/failure rules](design.md#privacy-safety-and-failure-boundaries), and
[global test design](design.md#validation-and-test-design) remain with the owners
routed by the [authority index](authority.md).

<a id="typed-completion-evidence"></a>

## Typed Completion Evidence

Documentation status: **削除予定（計測後判断・実装維持）** for the legacy
`task edit --completion-commit-hash` input. The implementation still accepts
its Git-only hash and synchronizes the compatibility projection. Skill guidance
instead uses typed `--completion-evidence-kind git_commit --completion-revision`
with `task complete` for completion. Only usage guidance is removed now;
execution measurement and any later implementation removal require separate
decisions. This note is not a runtime flag, a removal authorization, or a change
to stored evidence, help, errors, or gates.

Schema v4 retains the old required/hash columns only as synchronized
compatibility projections. Current evidence kinds and matrices are:

- `none`: empty revision/reason, approval 0, required 1, empty hash;
- `git_commit`: canonical full commit ID as revision/hash, empty reason,
  approval 0, required 1;
- `external_revision`: bounded revision and reason, explicit approval 1,
  required 1, hash equal to revision;
- `commit_not_required`: empty revision/reason/hash, approval 0, required 0;
- `legacy_unverified`: migration-only partial history retaining the old hash;
  no normal write may choose it.

Changing kind clears fields not valid for the destination. Conflicting stale
fields return `completion_evidence_conflict`. External evidence always needs
the explicit acknowledgement, including in a Git project. `legacy_unverified`
cannot satisfy a new done after reopen.

Git commit resolution uses argument-vector, no-shell reads equivalent to:

```text
git -C <repo> rev-parse --verify --end-of-options <revision>^{commit}
```

Empty/option-shaped input, multiple output lines, non-hex output, ambiguity,
failure, or a noncanonical full ID is rejected. Optional locks and lazy object
fetching are disabled; no Git read may invoke a hook, network fetch, or write.
Stored Git completion and Git review targets are re-resolved under the
done-time plan and compared with the locked stored values.

Thin `task complete`, compatibility `task edit --status done`, and
`task complete --check` share one completion request and ordered validator.
Both write paths require explicit verification/review confirmations, typed
evidence, sequential eligibility, exact current target, the qualifying manual or
Runner basis chosen by the sole selector, a satisfied fresh review gate, and no
blocking receipt/finding. The manual arm uses a qualifying current Receipt;
the Runner-pass arm uses its qualifying observation with a null Receipt link.
Check is read-only and is not an
authorization token: it captures one coherent basis, closes SQLite for Git,
then performs a second coherent basis read. Drift yields
`completion_check_stale`; no readiness row or receipt is stored. Its bounded
projection returns only Task ID, ready/status, the first ordered blocking code,
Contract revision, target generation, proposed evidence kind, and fixed next
action. Marker `0` retains those two check reads and the manual write path;
only live marker `2` adds the Runner selection and selected-basis recapture.

All completion connection phases pass the completing Task ID to the storage
layer's [scoped review admission](database-design.md#conditional-review-continuation-storage),
including the writer opened by compatibility edit-to-done. The locked edit
transition repeats that same scope after `BEGIN IMMEDIATE`, before recapturing
the completion basis. Sequential status predicates, own relevant history and
cross-Task rejection remain; unrelated Tasks' reviews are not revalidated.

<a id="review-target-and-git-snapshot"></a>

## Review Target And Git Snapshot

The [record authorization adapter](task-operation-design.md#ordinary-record-authorization-adapter)
restricts optional host-preapproved calls before dispatch and propagates that
restriction through ordinary handoff preparation. It grants no integrated
finalization, review-wait or Git publication authority.

Caller-side success-only composition of Git staging and target capture, or Git
commit/full-ID retrieval and completion, uses the existing commands unchanged.
It introduces no runtime wrapper or transaction spanning Git and SQLite. A
saved target or successful commit is retained on downstream failure; uncertain
responses are reconciled through existing public state before tail-only retry.

The current review identity is the exact tuple:

```text
kind, value, base_revision, generation
```

Kinds are `git_commit`, `diff_fingerprint`, `external_revision`, and
`git_snapshot`. A diff fingerprint is canonical
`sha256:<64 lowercase hex>`. Setting any target advances generation even when
kind/value repeat, and old receipts become audit-only. Receipt creation copies
the complete tuple; callers cannot attach one to another target.

`git_snapshot` takes no caller revision. The Git service reads a stable
`HEAD^{commit}` and stage-0 `git ls-files --stage -z` index before/after
checks, consuming raw path bytes. It rejects unborn HEAD, nonzero stages,
intent-to-add zero objects, sparse directory entries, and capture instability.
The version-1 digest input is:

```text
"taskgov-git-snapshot-v1\0"
<base full object id> "\0"
for mode/object/path entries sorted by raw path bytes:
  <mode> "\0" <object id> "\0" <raw path bytes> "\0"
```

The target value is its SHA-256 fingerprint and base is HEAD. It excludes
unstaged and untracked content.

The existing `git -C <governed-project>` fixes subtree scope without rerooting
the project or adding a caller coordinate. Index entries use `ls-files`'s
project-relative default. Recursive tree reads omit `--full-tree` so that
Git applies the same subtree and path origin. Raw `diff-index` and `diff-tree`
observations explicitly use `--relative`, excluding outside changes and
stripping the enclosing prefix. Capture and completion retain the same digest
format and enclosing full base/parent IDs; only the governed subtree's leaves
contribute. Root-project observations keep their existing whole-tree behavior.

Target setting first performs bounded Git observation outside SQLite. A short
writer then rereads Task, Contract, current authority snapshot/criteria, and
expected generation and atomically inserts one capture-version-1 target,
manifest, manifest Evidence Reference, and event. Git targets compare complete
tree/index leaves or commit/first-parent leaves; root commits use the read-only
empty-tree identity. `artifact_manifest.py` validates safe UTF-8 POSIX paths,
full mode/object IDs, unique exact renames, fixed byte ordering and ordinals,
10,000 entries, 16 MiB canonical bytes, object presence, and pre/post stability.
Opaque targets insert zero entries and the fixed omission. The writer
revalidates the observation binding but never runs Git.

Migration leaves old targets at capture version 0 with null snapshot,
criterion, and manifest IDs. Source-producing Receipt/Finding/completion paths
check this after target structure and expected-generation equality but before
uniqueness or gate evaluation and return `evidence_basis_stale`. Preparation
and existing-Finding resolution remain read-only with respect to the ledger.

At completion, the proposed commit must have exactly one parent equal to the
stored base. Its recursive tree leaves are normalized to the same
mode/object/path form and must reproduce the fingerprint. `stream_tree_entries`
validates every leaf and feeds the version-one hash incrementally, without a
whole-listing or whole-manifest buffer. Its record, ordering, object-format,
scope and subprocess cleanup checks are shared with target observation; a total
listing above the bounded byte-capture limit does not invalidate an otherwise
valid tree. Root and merge commits are unsupported for snapshot binding.
Hook-altered, added, removed,
renamed, mode-changed, or otherwise changed content fails
`review_target_mismatch`. A snapshot target may close only with Git-commit
completion evidence. A Git-commit target requires the identical canonical
Git completion commit, an external target requires the identical approved
external revision, and commit-not-required requires a canonical
diff-fingerprint target.

Pre-commit Runner reads continue to recapture the stage-zero index. Completion
passes its already resolved full commit ID into the same selector; for a stored
snapshot, the selector verifies the exact parent and tree fingerprint and
recomputes the stored snapshot material digest from that immutable commit tree,
without inferring target material from ambient HEAD or the post-commit index.

<a id="receipts-findings-and-gate"></a>

## Receipts, Findings, And Gate

Receipts are append-only and unique per Task, target generation, and bounded
caller-supplied reviewer key. Kinds are:

- `independent`: pass or changes-requested, and v2 collected `unable_to_review`, never user-approved;
- `self_review_fallback`: summarized; a Tier-2 pass requires explicit user
  approval, a Tier-1 pass does not;
- `not_required`: Tier 0 only, verdict not-required, mechanical rationale, no
  approval.

A changes-requested receipt requires a summary and never satisfies the gate.
Replacing a same-generation reviewer result is forbidden; re-review sets a
fresh target generation.

Findings belong to a loaded same-project/same-task receipt and are high,
medium, or low. Resolution preserves the original row and adds only bounded
resolution text/time. Open high or medium findings across all task receipts
block. A resolved high/medium finding still requires a target generation newer
than its receipt and fresh passes. Any current-generation changes-requested
or effective collected unable receipt blocks. Low findings are nonblocking.

The gate deterministically requires:

- Tier 0: one current not-required receipt;
- Tier 1: one distinct current independent PASS, or its valid fallback;
- Tier 2: two distinct current independent PASS reviewer keys, or one valid
  explicitly approved fallback when independent review tooling is unavailable;
- no current changes-requested or effective unable receipt;
- no open high/medium finding; and
- no resolved high/medium finding at or after the current target generation.

Independent PASS rows are ordered by reviewer key then receipt ID and take
precedence over fallback. Tier 0/fallback selection is by receipt ID. Reviewer
key distinctness proves only different stored strings, not identity,
independence, provenance, expertise, target inspection, or authentication. The
trusted caller/orchestrator is responsible for truthful attestation.

Normal Task detail obtains current Receipt summaries and every operational
Finding through a show-only collector in the existing fully validated inventory
stream. It neither filters the recent-ten projection nor repeats validation
queries. Existing readers and gate consumers retain their default bounded
projection; the final normal show projection removes duplicated target/tier
fields and emits each operational Finding once. Audit retains the prior shape.

### Structured Finding Resolution

`finding_resolutions.py` validates the closed grouped stdin shape and expands
only explicit ID/reason groups into existing normalized scalar inputs. It owns
no SQL, selection, judgment, target capture, or automatic retry.
`cli.py` rejects incompatible input modes before state resolution, consumes
bounded binary stdin only after read-only rejection, and retains its initialized
connection, outer transaction, sanitized failure, and post-commit maintenance.
`cli_text.py` formats the flattened results using existing single-Finding text.

`reviews.py::resolve_review_findings` validates the flat inputs, locks and
rereads the declared Task using the existing writer boundary, and checks each
Finding's same-project/same-Task ownership on that writer. It delegates each
resolution to `resolve_review_finding` with a private batch context, retaining stored-ledger checks,
done/replay rejection, original content, timestamps, events, and revalidation.
An outer batch savepoint rolls back a successful prefix on any failure, even
when a repository caller catches it. No per-item commit or partial-success
result is emitted. Existing single resolution callers retain the
[operation-scoped admission](review-completion-specification.md#operation-scoped-review-admission)
boundary. The service deliberately does not use current-capture or
Receipt-creation freshness checks: historical Finding resolution remains valid.
No schema, new durable batch record, alternative gate, or new normal-loop step
is introduced.

`review_material_repository.py` lends validated immutable authority snapshots,
criterion links and artifact manifests only within a lexical scope on the same
connection, transaction, project and Task. Its private savepoint guard and schema
cookie reject a transaction restart or schema change; scope exit clears the
borrowed material. These scopes nest lexically and never commit the writer.
Each item still reads and validates current Task/Contract/ownership and compares
the original locked basis, plus its own Receipt, Finding, Reference, status and
resolution relations. The batch ends with complete Task validation. Standalone
writers retain their separate preflight and locked admission, with no borrowed
material crossing that boundary. No gate or other mutable projection is cached.
The same batch materials reach `append_resolution_locked` when a reassessment
Receipt is supplied, for both open and already resolved Findings. Successor
ownership, each source/Reference, resolution digest and duplicate-link rejection
remain individual checks. Continuation judgment and resolution-history readers
also borrow immutable materials across their selected-ID chunks; the graph,
ancestry and mutable resolution state are still validated independently.

### Structured Result Registration

The version-one decoder/save path described here remains the legacy arm.
New opted-in requests use [collected answer collection](#collected-answer-collection)
through the same public writer and shared gate. Material retrieval and complete
Packet validation remain common; their result instructions follow the immutable
saved protocol rather than the installed package's current default.

`review_results.py` owns the closed version-1 JSON decoder, exact type and
capacity checks, input-only approval-key matching, and batch orchestration.
It reuses Receipt/provenance/Finding normalization and the existing individual
writers; it owns no SQL, schema, batch ledger, or replacement gate.

Result-input diagnostics are a dedicated `ReviewEvidenceError` subtype emitted
from those existing checks. A closed set of schema paths and fixed reasons
supplies its field/message; no general path parser or original-position ledger
is introduced. `[]` deliberately leaves document/element position unspecified.
Element checks name an array element; legacy multi-field Receipt errors name
the Receipt group instead of treating their field label as a unique cause.
The provenance validator can attach field/reason metadata while preserving its
legacy exception code/message/field; only result normalization consumes that
metadata. The result CLI and handoff helper expose the dedicated diagnostic,
not arbitrary exception fields or contents. Shape/privacy/semantic ordering,
accepted values, normalization and transaction boundaries are unchanged.

The CLI reads bounded binary stdin and decodes before opening the initialized
connection. The decoder accepts the existing document or an array of 1–8
complete documents within the same whole-input byte cap. It validates each
document before comparing exact identity/target values, concatenates only
`receipts` in input order, and validates the combined capacity. It returns the
same single-document shape to the unchanged normalizer and writer. It neither
reads paths nor interprets reviewer messages or merges judgments.
All entries are normalized before acquiring the writer. The
service then uses the existing writer-lock/revalidation boundary and compares
the submitted Task, Contract revision and complete target against the locked
Task before any insert. A concurrent tier or authority change cannot reuse
pre-lock normalization. Each Receipt and its Findings use the same connection,
including their individual References, provenance and events.
Legacy and collected continuation adapters pass one scoped, fully validated
history into publication preparation and judgment selection before inserts.
The adapter closes that scope before returning to the service's write phase;
no cached history crosses registration, transaction or effect boundaries.

Both legacy and collected-result registration lend the fixed materials above
to the Findings of each Receipt. The outer locked admission and final gate stay
fresh. The selected Receipt/Finding/Reference inventory in `read_review_evidence`
uses the same immutable-material scope across its validation chunks, sharing one
read transaction when it owns the connection's read boundary. Show, gate and
report consumers retain their own complete source checks and output selection;
this does not cache continuation judgments or completion admission.

After the inserts, the service captures `read_review_evidence` on that same
connection and validated locked Task, then calls `first_review_gate_error`.
It does not reproduce predicates, restrict blockers to the recent window,
enforce this observation as a write gate, or perform Git/external I/O. The
response selects compact Receipt/Finding fields from the actual saved results,
keeps their input order and complete new Finding bodies, and carries the locked
Task/Contract/target basis once in `review_gate`. Explicit omission metadata
distinguishes presentation from the unchanged storage/audit projections.

The CLI owns the one outer transaction: insert or gate-read exceptions escape
it before conversion to sanitized failure output. There are no per-entry commits or
successful-prefix returns. Commit and connection close precede formatting and
one changed/Viewer-relevant maintenance result. Legacy single-item callers and
operation-scoped state admission retain their existing contracts. Replay detection uses
the existing Task/generation/reviewer uniqueness, without a new durable ID.
For this command only, a post-commit text rendering exception retains success
data with a fixed display warning. Successful emission includes stdout flush
inside the same guard. An emission/flush exception returns a fixed
saved-outcome stderr diagnostic and exit 2 without another stdout attempt or
replay. The failed stdout descriptor is redirected to the OS null sink where
available to prevent a second write during interpreter finalization; custom
embedded streams without a descriptor retain caller-owned lifecycle. Neither
path reports rollback of already committed rows. Unavailable
output still requires existing public-state recovery, not a new receipt ledger.

`scripts/review_handoff.py` delegates to `review_handoff.py` for caller-owned
original-file transport. It has no storage connection: save reuses the pure
result decoder/normalizer before exclusive creation and after physical readback;
submit preserves raw bytes, adds only array framing, and sends them once to
the sibling public CLI through binary stdin. No LLM-built collector, alternate
writer or producer-specific adapter is required. The registration transaction
and current-state revalidation stay here and are not replaced by saved-file checks.
`review_handoff_originals.submission` owns the complete parent-side confirmation from retained paths;
no separately retrieved child acknowledgement or model-visible original is a
prerequisite. Its Packet/original/sidecar validation and final byte comparisons
finish before the one registration dispatch. `main` tracks that boundary only
in memory: helper-local submit errors carry `registration_status=not_started`
before dispatch and `unknown` from dispatch onward. A missing input before
dispatch has the fixed `handoff_input_missing` code. The child still owns its
atomic outcome, complete decision response and post-commit output failures;
its stdout/exit pass through without capture, reinterpretation or replay.
This introduces no persisted confirmation record, alternate writer or mode.
`review_handoff_transport.py` owns the shared physical-file operations and pure
complete-Packet/result validation consumed by prepare, read, save, submit,
continuation, finalization, material delivery and optional workspaces. Its named
internal interface separates structural `relative_path` from ignored physical
`checked_path`, bounded reads from exclusive writes, and parent observations
from replacement checks. Stored-constraints compatibility and the existing
result decoder/normalizer remain unchanged. It owns neither live admission nor
registration; consumers import identity, domain errors and privacy validation
from their actual owners rather than through the CLI entry. The entry retains
argument parsing, admitted save orchestration and the one public CLI dispatch.

The save orchestrator normalizes only known operational `RepositoryError`
failures, retaining a call-local distinction between admission not reached and
the admitted save body entered. The boundary includes context-manager exit,
which may fail after original and sidecar publication. Its private transport
error carries the conservative save observation to `main`; the public helper
emits fixed codes, never repository messages. Shared-helper repository failures
also receive a fixed unavailable error while submit retains its dispatch state.
Repository leases, retry policy and exclusive file ownership remain unchanged.

`review_handoff_preparation.py` owns the same helper's explicit `prepare`
operation: three fixed sibling CLI invocations, bounded binary response capture,
exit/envelope and Packet consistency checks, creation of the named unused
ignored directory chain and complete Packet, and exact save/submit instructions.
For a ready integrated preparation, the same response construction replaces
`submit_command` with `finalization` and `finalization_command` only after intent
creation succeeds. Manual response fields and reviewer requests are unchanged;
an intent-creation failure still returns no ready handoff. No new argument,
command, validator, persistence field or finalizer effect is introduced.
It reuses transport path/read/write checks and the existing structured Receipt
input decoder; it owns no SQL, authority inference or arbitrary-command runner.
Source mutation outcome, sanitized warnings, and transport outcome are separate.
After target dispatch, a valid failure envelope is still persistence-unknown
unless it is the CLI's pre-dispatch `parse` rejection. The helper retains the
source exit/error and adds only a fixed state-inspection warning; it does not
deduce T1 or cleanup commits from error codes or change Runner transactions.
Receipt rejection and read-only recovery keep their existing classification.
Non-ready, malformed or uncertain responses produce no reviewer requests;
post-write failure retains residue. Capture starts before the source call and
does not add a normal query, log file, retry or reviewer-launch operation.
For every missing directory, `os.name == "nt"` selects default `Path.mkdir()`
to inherit the parent ACL; POSIX retains `mode=0o700`. No existing permissions
are rewritten, and no SID policy, native ACL adapter or caller option is added.

The same module owns read-only saved-Packet display: existing transport reads
and complete preparation validation precede `review_packet.py`'s pure
independent-role projection. The read parser requires repo and Packet paths;
optional `--material-details` selects the conditional retrieval explanation,
never a role. Arguments, tier and slots do not establish actual independence.
Generated ordinary review
requests supply that read command instead of raw-file reading and contain the
independent assignment and first-read failure boundary. Each row has only `result_path` and `request`;
the request embeds both fixed read/save invocations and remains directly
forwardable without separate command keys or parent reconstruction.
`_requests` places that stable entry before the bound read/save command sections,
in that order. The paired read's independent `result_instructions` supplies
whole-target judgment, routes to material/verification guidance, result
declarations, and fixed-save/acknowledgement/failure/permission rules. The full
Packet's direct-transport explanation receives no shared-file procedure.
Material read guidance owns
collection selection, exact mappings, reuse and conditional recovery; collection
output explains its tables and incomplete-delivery recovery without repeating
result-format rules. Host-native quoting, UTF-8 data carriers and the
existing invocation arguments remain unchanged. Focused tests compare the
common opening across cases and retain native command/Packet/result bindings;
this layout is not an observation of host prompt caching.
`_review_material` reuses artifact observers and the complete entry builder.
Normal read and `--material-details` emit collection/changes/inventory commands;
neither traverses an unchanged inventory. Only explicit inventory retrieval
streams the dependency tree, excludes changed paths and counts omitted entries.

`review_delivery.py` owns UTF-8 serialization, text allowance, two-layer JSON
transport measurement and range formatting. Its Git reader uses existing safe
Git timeout/cleanup, incrementally validates full-stream UTF-8/NUL and hashes all
bytes, retaining only the requested window plus a code-point boundary margin.
It emits nothing before validation. The full-stream hash rejects mixed resumed
ranges, including a diff changed by configuration. The specification owns limits.

`review_read_delivery.py` bounds the first-read envelope. Small displays remain
one response. Overflow routes to separately validated context, material and
verification sections, without mutating the Packet. Stable content hashes bind
their ranges; verification observation time is outside its section digest.
Workspace guidance remains one bounded response so the complete caller template
is available before invocation. Reading allocates no handle; construction in the
execution invocation retains the exact cleanup command. Paged context is
original JSON text, never a summary.

`review_material_reads.py` owns fixed individual/list operations. Full object
IDs and path validation remain closed. Dependency/directory use
`<revision>:./<path>` under the governed project; empty directory selects that
project root. Changed and explicit unchanged lists preserve whole entries,
stable offsets, Packet digest and exact target. Unchanged lists also apply
their array byte/count caps before the total transport cap. Batch validates
bounded full-ID stdin and emits bounded bodies with per-ID outcomes and recovery.
Records-only admission accepts only the corresponding fixed read arguments.
No configurable Git runner, output path or permission change is introduced.

Collection retains path/side/revision/mode and absence rules. It validates bounded
selected-path JSON, shares `_observe_material`, and resolves unchanged paths
through the project-scoped tree stream. Changed old/new paths suppress base
lookup. Its combined sequence is changed entries followed by deduplicated
dependencies; continuation binds the normalized selection digest and Packet.
At most 20 complete mappings enter a page, then the page shrinks against actual
serialized transport size. Final Packet bytes are rechecked.

`_Bodies` accounts unique emitted body and patch text separately. Exact complete
patch equality can reuse a response-local patch even after its allowance is
exhausted. Blob IDs identify bodies; patch pairs cache outcomes. An overlarge
body does not block a bounded patch. Whole-file additions/deletions reference
their body and operation. If one mapping's text groups fit separately but not
together, its selectors remain and text is marked range-required. Successful
siblings remain usable; partial text is never labeled as a complete body.

Immutable mapping supports reuse across governing-rule reading and review only
for fully delivered matching content. Generated guidance starts from every
changed entry and complete diff, then needed before/after context, affected
dependencies and governing sections under the project's mandatory reading rules.
It does not require complete before/after file bodies uniformly; whole-file
add/delete diffs still require their referenced body, and missing required
information still prevents PASS. This changes guidance only: collection keeps
its existing bounded bodies, independent patch allowance and explicit partial
status, while full-stream validation/hashing and individual recovery remain.
Missing required ranges, discovered dependencies and actual reread boundaries
remain obligations. Generated guidance covers both
execution and outer-return budgets, one response per call and recovery. There
is no content persistence, raw-output cache, reading ledger, relevance inference
or new public Task query. Save/wait/submit/completion are unchanged.
The child inherits no Git overrides and cannot lazily fetch missing material.
Generated host-specific literal-substitution instructions and fully bound shell
arguments preserve quotes and shell punctuation, including PowerShell's five
single-quote delimiter characters, without changing the parent's environment.
One fixed public `review prepare` capture then compares saved Task/Contract,
target and path metadata and rechecks original Packet bytes before display.
That read has no direct storage access or mutation; no reviewer check/show,
new file or role classifier is added. Opaque targets explicitly require supplied
material/binding, not inferred Git content. Existing observation limits and
sanitized failure codes apply without partial material. `review_results.py` renders the applicable independent
format branch from the existing vocabulary owners; it does not filter prose
with patterns or change the decoder/normalizer. The full result explanation
retains the existing vocabulary, limits, approval and fallback conditions for
direct consumers and alternative review paths.

The first read also projects `verification_evidence` from that validated current
public Packet and fixed `verification_guidance` from `review_verification.py`.
Saved evidence is shape-checked but never trusted in place of the current read;
old Packets without it remain accepted. Existing identity/Contract/target/path
comparison precedes projection, without another CLI capture. A changed summary
or observation time alone does not rewrite the saved Packet or invalidate a
review target. The independent procedure points to the supplied summary and its limits,
without a normal parent-transcription or reviewer-query step.

The transport module owns bounded physical reads, explicit ignored-path checks,
exclusive creation and retained failed-write residue. Standard-library file
operations and the existing safe Git environment suffice; no native adapter or
state-path resolver mode is introduced. Identity, type, links, size and mtime
are compared across lstat/fstat; ctime is compared only within the same API
because Windows reports different ctime meanings through those APIs. The
complete Packet's identity/template consistency and tier supply context for
existing result validation, not a second live Task admission decision.
`tests/test_review_handoff.py` exercises original-file transport;
`test_review_handoff_preparation.py` starts before actual installed source CLI
invocation and exercises ready/non-ready and injected capture/file failures.
Both cover portable filesystem behavior; `test_review_handoff_windows.py`
owns the native PowerShell transport case outside POSIX platform selection. Existing
result registration tests retain writer atomicity and concurrent-state checks.

<a id="collected-answer-collection"></a>

### Collected Answer Collection

This structure implements the current
[answer protocol](review-completion-specification.md#collected-review-answers)
and [host boundary](review-completion-specification.md#collected-answer-host-boundary).
The [database value schemas](database-design.md#collected-answer-value-schemas)
own the exact closed basis/binding/source/metadata/capture types; the
[Evidence contract](evidence-specification.md#collected-answer-evidence) owns
path-free v2 provenance and frozen public graph validation. Conditional design
material retains Task acceptance only and supplies no missing runtime rule.

`review_answer_host._dispatch_activity` is shared by single and multi initial
binding. It validates activity completeness, IDs, unique dispatch and child
identity before distinguishing `initial_binding_interacted` from other
`binding_mismatch` reasons. Only a positively observed same-child interaction
selects that fixed reason, with null field. The shared failure validator admits
only that class/field combination. Existing parent collection and managed-wait
failure forwarding retain it without new host calls or timer changes. The wait
preflight keeps this initial rejection ahead of subsequent unbound-source
failures, including the all-reviewers-ended collection path. Parent
preparation, MCP wait description and Skill guidance use returned handles before
extra contact; workerless collection retains its separate UUID-only contract.

`review_answer.py` owns pure bounded UTF-8/JSON decoding and exact original/
payload byte ranges. `DecodedAnswer` retains complete bytes, all five sanitized
fields, offsets/length and separate original/payload digests. `AnswerError`
carries only a closed failure object and a digest only after valid shape and
privacy admission. Unknown keys, rejected bodies and arbitrary exceptions never
enter diagnostics or operational records. `review_answer_values.py` reuses the
continuation execution basis, canonical codec and privacy policy, returning
detached closed values. `review_answer_provenance.py` builds and validates v2
after the actual Receipt ID is known; requested assignment and observed sources
remain separate, and actual model/Skill/methods stay unknown.

Within one transport decode or collection readback, private value helpers pass
the freshly decoded answer through complete OriginalRef/Metadata checks instead
of parsing its bytes again. Readback retains the physical confirmation read;
publication retains its fresh validation immediately before writing. Recovery
of saved publication validates its newly read bytes once. These values never
cross a writer or physical-read admission boundary as proof.

The provenance builder validates metadata and payload once, freshly parses
the supplied `DecodedAnswer.raw`, and compares all decoded fields and exact
metadata byte facts. Its private matching parser reuses only that same call's
validated payload when the parsed value is equal; different content is fully
validated and then rejected as a binding mismatch. The builder admits the
complete generated structure and size, checks privacy for every other field,
and uses the standalone validator's provenance semantics and context seal.
Public provenance validation always validates its own payload. The existing
payload-dictionary construction arm remains distinct and makes no physical-raw
assertion. No mutable `DecodedAnswer` payload is trusted from a previous call.

`review_answer_preparation.py` selects the opt-in only for admitted current-schema
preparations with known execution and a nonzero review tier. Preparation-record
version two adds per-slot `collection_bases`; version one remains exact and
omits that member. Each basis binds complete Packet bytes and the exact request
UTF-8 bytes, including the independence attestation. The request asks only for
the five-field final answer and contains no reviewer save/submit command.
The complete Packet's material/template representation stays unchanged; the
admitted read projects result instructions for the saved protocol.

`answer_request(read_command, task_id)` prefixes the generated request with one
best-effort reviewer-owned `review_wait_inspect` call using the validated Task
ID. `_requests` supplies that ID for both initial and continuation generation.
The instruction belongs only to the generated request, outside the reusable
result instructions/read view, so material rereads do not repeat it. Initial
publication and continuation request hashing include it before dispatch. No
generator executes the call, rewrites an issued request or sends a second child
message. Missing tools or call failures leave the review and existing parent
recovery enabled; no new connection result, binding or completion gate is stored.

`review_answer_continuation_preparation.py` freezes a separate basis/store for
each new continuation and includes the exact action/reason suffix in its request
digest. `RequestRound.answer_protocol` is an immutable discriminator:
`review-answer-v1` is encoded explicitly; default `legacy` is omitted to preserve
the exact old canonical payload. Decode rejects alternative encodings and
unknown protocol values. Transition/request copies preserve the tag. The
collection store is initialized before advancing the native/request transition
to prepared readiness. Before that advancement, the transition separately pins
the ordered digests of each complete CollectionBasis. Recovery compares every
operational source slot to its issued initial preparation basis or continuation
pin before binding, publication, readback, capture or registration. It never
regenerates an issued request or uses the operational basis as its own proof.
A tagged ready round with missing pins or missing/corrupt state fails closed
without filling pins from the journal or falling back to legacy. An opted-out new round cannot mix newly selected legacy results with
outstanding collected originals. No locator scans directories or invents a
missing preparation.

`review_answer_dedication.py` owns the closed legacy migration declaration and
the shared eligibility used by continuation issuance, readiness and arming.
`RequestRound.legacy_migration` is an optional versioned value frozen with the
transition fence and copied unchanged into the request round. Absence is omitted
from canonical encoding, preserving every old payload. A new explicit migration
records the selected sessions and their future reviewer keys, with each origin
pointing to that fence. Later rounds carry only their selected declarations and
validate the same origin in the bounded same-preparation history. Existing
registered predecessor keys must agree; selected saved-but-unregistered
originals retain the prior registration requirement. Requested launch attributes
are unknown, never inferred from old provenance.

The declaration is separate from the collection binding's terminal-turn
boundary. Legacy arming admits its saved declaration without opening an initial
native store; native preparations retain their original issuance-proof path.
Same-round rearm checks the existing binding before any host observation. A new
round reuses dedication but observes a new pre-send terminal turn. Existing
service admission still revalidates native basis and current selection before
binding and collection. No core schema, physical operational schema, original
metadata or Evidence format changes; no old record receives a declaration on
replay or read.

`review_answer_handoff.service_for` gives fresh collection call-local admission
that reloads the current preparation and native/issued continuation at each
existing effect boundary. This shared check covers parent, wait and hook
collection, including a successor selected during host observation. An unchanged
initial reviewer remains eligible only under the existing unselected-slot rule.
Exact retained-source reads, capture and registration select their separate
admission explicitly; historical reporting retains its read-only basis. Neither
path makes an obsolete selected request eligible for new collection.

`review_answer_runtime.sources_for_reviewers` resolves current selected requests
and never-selected initial reviewers through one shared preparation, native
selection-history and exact-binding check. Parent/wait collection and event
lookup use that resolver, including after restart. Its call-local `SourceGroup`
keeps each service's source slots separate from positions in the requested
reviewer list; collection responses map only to those request positions. It
admits ordered subsets of bound slots without changing initial full-dispatch
admission or registration ordering. The source service's existing fresh-basis
checks still run at each effect boundary. Retained-source reads keep their
separate admission and cannot supply a fresh-source fallback.

For reviewer-origin hooks, `_CollectionAdmission.check_hook` obtains one fresh,
admitted query-only native snapshot per existing admission boundary. It
checks the active Task, complete execution basis and native continuation history,
then closes that connection before operational-store or Packet reads. It also
rechecks the canonical collection path, complete captured preparation, current
issued source, opt-in and exact Packet bytes. `reviewer_hook_admission` pins the
basis, binding and active attempt. Each fresh
[resolver snapshot](setup-state-design.md#fixed-state-resolver) uses structural
operation-local admission, then validates that active Task's complete required
review storage before basis/history reads. No global validation proof or
database-content hash is used for this path. Source lookup, ordinary parent/wait,
retained-source admission and finalization use the same original-Task boundary.
Mutable target and operational checks still run at every effect boundary;
no native rows, open connection or permission survives between observations.

New initial and continuation issuance uses `CollectionBasis.version=2` to
select dedicated-reviewer tracking. The answer format remains `review-answer-v1`;
older basis versions retain their original decoding and behavior. A tracking
binding holds the actual dedicated reviewer and an observation boundary rather
than claiming a new parent dispatch-to-turn relation. Initial binding retains
strict child creation proof and an inclusive first-turn boundary. Explicit
parent `review_answer_collect action=arm` establishes a continuation's exclusive
latest-terminal-turn boundary before the caller sends the additional request.
Arming an existing slot never moves that boundary. Issued digests and original
source metadata are not rewritten during runtime upgrade or restart.
Before reusing the initial dedicated assignment, arming compares its complete
source basis with that original slot's immutable preparation issuance pin.
This historical assignment check is separate from fresh-source eligibility;
selecting a continuation does not invalidate the original dedication proof.

`review_answer_collection_repository.py` stores at most eight contiguous slots
per preparation/continuation in the resolver-derived operational database.
Every read/write validates the exact schema and closed record. Each record is
at most 32,768 canonical UTF-8 bytes. Version one retains at most two ordinal
attempts and one correction intent. Version two retains one eventual adopted
attempt plus bounded tracking/failure state, publication reference and monotone
registration state; rejected candidates never grow a history array.
It contains no answer body, prompt, provider envelope or growing observation
array. Call-local validation never becomes a cross-operation cache.
`review_answer_collection.py` owns the effect state machine. It latches exact
source/digest conflicts, preserves the first original and each attempt's digest,
reserves each persistence/send intent before its effect and reconciles only that exact intent.
Save and registration unknowns remain distinct. A format failure with admitted
privacy may retain its digest; unestablished/rejected privacy cannot.
Recovery and readback raise the repository-validated attempt failure with its closed field
and reason after checking unresolved publication, before the generic incomplete
branch. Parent submission and finalizer reporting consume that same readback.
Explicit recovery does not reconcile a publication with a stored attempt failure;
it retains both the unresolved effect and conflict evidence without I/O replay.

In tracking mode host observation runs outside the source lease. Under that
lease the service validates the candidate's complete bytes/privacy and completed
terminal state, rechecks current admission, and atomically freezes the first
valid source identity/digest with its publication intent. Invalid candidates
remain unadopted; a valid negative or unable answer freezes just as PASS does.
Once frozen, no latest-answer observation may replace it. Saved originals and
unresolved publication intents retain their existing
readback/reconciliation rules. Explicit reassessment uses a fresh continuation
slot and never resets the predecessor. No automatic follow-up sender is added.

Fresh tracking admission checks active Task/owner, complete native basis,
current preparation, current issued selection and the existing exact reviewed
Git snapshot where applicable. It repeats after host I/O and before publication
and registration. A proven material mismatch during collection permanently
invalidates that slot; read-only inspection rejects it without writing.
Temporary transport errors do not invalidate the slot or create a replacement
assignment. A replacement preparation acquires the predecessor's finalization
lease and relevant
collection leases before switching the current pointer. Registration already
retains those source leases through core commit and rechecks active native
eligibility under the writer. This closes a new-preparation race without
acquiring an earlier preparation lease beneath finalization or changing native
schema.
Finalization keeps its existing exact Git checks and publication locks; these
checks do not claim an atomic snapshot of unrelated mutable workspace files.

Publication uses only the prepared ignored physical original path and shared
`review_handoff_transport.py` exclusive-create/flush/readback checks. The service
commits the intent/reference first, revalidates basis/privacy before the file
effect, and commits saved only after exact readback and renewed identity.
Post-create uncertainty retains residue and unknown state. Only the explicit
parent arm passes publication-reconciliation intent through the runtime to
`CollectionService.recover`; hook/wait calls preserve `save_unknown`. The service
reuses `reconcile_publication` to validate the saved intent, current basis and
exact physical bytes before recording saved. It performs no host fetch, file
creation or send; absent or conflicting residue remains unknown. Registration repeats
all original/Packet/basis comparisons; a save acknowledgement is insufficient.
Operational collection leases span that final admission and the short core
writer. No host or file I/O runs inside the core SQLite transaction, and no
earlier preparation/request/finalization lease is acquired beneath collection.

`review_answer_transport.py` decodes the explicit collected discriminator before
core access, enforcing canonical base64, the complete framing cap, prepared slot
order, full raw/range/digest equality and every answer field. It produces typed
collected/retained-legacy items rather than manufacturing a v1 result document.
Internal answer failures retain their closed class, field and reason through
collection admission. The CLI and handoff boundaries map these to the existing
error code, optional field and fixed message; no rejected content or arbitrary
exception is forwarded. The shared outer stdin reader still rejects oversized
or undecodable input before protocol selection without guessing a discriminator.
`review_collected_results.py` and `review_answer_repository.py` compose the
existing locked public writer: current ownership/Contract/target, exact source
and all Findings are revalidated and committed with v2 provenance, source and
dispatch inventory atomically. The legacy decoder and approval paths are not
broadened. `reviews.py` derives the effective unable count through the same
evaluator used by manual completion, finalization and display; `review_unable`
blocks regardless of other PASS results.

`review_answer_originals.py` and `review_answer_handoff.py` read complete
collected originals alongside retained legacy sidecars. New selected slots come
first, followed by the frozen captured subset in actual session order. A v2
submission appends uncaptured, never-selected initial originals in session
order. The shared transport decodes their explicit source-use references once;
manual submission, finalization and locked registration consume those typed
roles. Existing capture ranks remain actual dispatch slots, and old envelopes
retain their exact semantics. Captured collected originals have an exact typed
capture; late initial sources instead retain initial judgment identity and are
adopted by the existing atomic source/Receipt/dispatch/consumer registration.
Native capture admission receives
unchanged source metadata under held collection leases, checks settled source
effects/old cleanup and records both the physical metadata seal and deterministic
path-free projection atomically. Further pending supersession transfers source
metadata unchanged, keeps historical captures and preserves required slots.
Source-context judgment identity is never rewritten as the consumer context.
One source store can contain both captured and late initial originals.
Registration acquires its lease once, validates exact readback for every item,
then checks each role independently: native capture equality for captured
sources, and unchanged initial issuance plus never-selected history for late
initials. The native writer repeats the current selection/history and absence
checks before registering that complete batch. No composition, inspect or
collection call gains a native capture/registration write.
The public Evidence Reader recomputes its own projection digest and graph
relations; it never reads private transport files or claims to validate their
physical seal. Historical finalizer reporting uses an already admitted saved
preparation and exact transition without requiring a current live Task basis.

`review_answer_registration.py` and `review_answer_finalization.py` reuse the
same complete typed submission and public registration boundary. They reserve
the exact submission digest before dispatch and preserve a possible effect
without replay. The registration wrapper marks a known commit immediately after
the core transaction exits successfully, before connection or admission cleanup.
Later confirmation or cleanup exceptions retain the committed response and the
existing sanitized acknowledgement warning. Exceptions before that boundary
retain their failure semantics; response construction alone never proves commit.
`review_answer_audit.py` validates the paired audit selectors;
the repository fetches the complete bounded dispatch inventory and checks each
Receipt/source/digest relation, including retained legacy entries. This exact
lookup is separate from recent-Receipt display and returns recorded, absent,
conflict or unknown. A complete matching inventory proves the existing
registration; snapshot absence alone cannot settle its effects. Normal success
adds no audit call or new completion gate.

The collected branch performs its reviewer guard and fresh Task match before
reserving outer registration dispatch. Recovery of an existing reservation with
an exact absent inventory uses `RegistrationAdmission.prove_not_dispatched`
only for a complete all-collected submission. Under the existing finalization
lease and every admitted source lease, it requires complete source coverage
and exact `not_started` registration tuples with null digest and Receipt ID.
The existing admission revalidates current preparation, original bytes, source
identity and retained history. It neither rewrites an intent nor infers rollback.
Release source leases before invoking the existing public writer to avoid
recursive acquisition; that writer repeats all-source admission and the
`not_started` check before marking any dispatch. A competing writer therefore
cannot turn the observation into duplicate registration. Mixed legacy,
incomplete or changed sources and conflict/unknown inventory remain unresolved;
already-dispatched sources require the distinct proof below.

`SettledRegistrationRecovery(preparation_id, submission_digest)` in
`review_answer_transport.py` is a frozen internal request, not an effect token.
The retained finalizer passes it as `registration_recovery` through
`Core.register`, `cli.handle_review_command`, `review_answer_registration.register`
and the existing `add_collected_review_results` composition. Exact selectors
and a non-null typed finalization basis are required. No new public flag or
alternate registration path is introduced.

The registration wrapper acquires every exact source lease and freshly admits
the complete all-collected batch. Each source must be dispatched/unknown with
the exact submission digest and null Receipt ID. It holds these leases through
the normal synchronous connection, writer transaction, close and confirmation;
it does not redispatch or reset source intent. Lock order remains finalization,
sorted collection leases, then core writer. File and host work stays outside
the core transaction.

After the writer lock and current-basis checks, the repository rejects any exact
dispatch header/entry, preparation/submission source residue, or source with an
exact submitted reviewer/turn/message identity, including conflicting digests.
Before continuation or capture adoption, recovery also rejects any existing
Receipt in the submitted captures or a continuation no longer prepared.
Historical judgments outside this submitted batch retain their normal meaning.
Only complete absence under these held locks permits the same atomic writer.
This establishes settled current-state effects, not historical non-commit
across arbitrary restoration; known identity/binding discontinuity still fails
admission. Commit acknowledgement and later-confirmation failure use the
existing committed-response rule above.

For unresolved collected reservations, `review_answer_finalization` adds the
[closed registration observation](review-completion-specification.md#exact-collected-registration-inspection)
to the existing `finalize --check` report. The ordinary check remains unlocked;
this additional diagnostic alone enters the already existing finalization lease,
recomposes and matches the saved submission/current basis, then uses
`RegistrationAdmission.registration_states` under all admitted source leases.
The public `Core.collection_registration` audit is read while those leases remain
held. Only validated complete source/native projections survive successful lease
cleanup. No initial record, lock creation, guard, confirmation, reset or writer
is invoked, and no observation is persisted or reused as an effect token.
Legacy-only and non-dispatching checks keep their existing output shape.

`review_answer_host.py` uses only bounded public `read_thread` observations.
The following exact-turn recovery behavior is retained for version-one bases.
Initial admission reads the exact parent's dispatch turn and a dedicated new
child's sole own turn, rejecting additional pages, interaction/reuse and known
or unresolved competing sends. Both initial binding entries share
`_dispatch_activity`: exactly one start remains required, while one distinct
complete completion notice for the same child is permitted. Other activities
and reused IDs still reject; the notice never supplies a turn or terminal state.
Recovery uses one fixed two-turn page and the
saved turn/message IDs; commentary, tool output, newest-turn inference and
private transcript/API fallbacks are ineligible. Observed final-item completeness
does not certify inaccessible provider omissions. The adapter currently has no
proven correction/follow-up addressability: `send_correction` returns not-sent,
and reused-session continuation without an admitted turn remains unavailable.
It must not relax initial admission to make these paths appear supported.
Recovery-only construction omits a parent dispatch turn; both fresh-binding
operations reject that form before host observation rather than substituting
the reviewer's executor turn for a parent turn.

Version-two observations use the same fixed bounded public page to select the
latest own turn beyond the saved boundary. An in-progress turn does not fall
back to an older completed PASS. Only its unique complete final item from a
completed turn is eligible. The adapter reports actual source IDs and version
two observation operations; it does not infer a request ID, dispatch identity
or chronological first answer from time. The source boundary and first-admitted
answer rule replace the unavailable strict follow-up binding guarantee.

The host distinguishes a validated `inProgress` turn with null error using
the internal `AnswerTurnInProgress` signal, carrying only the actual reviewer
and turn IDs. It is not an answer observation. Existing page/turn validation
and explicit final-item omission checks precede the signal. Generic incomplete
failures retain their type; the signal is not inferred from a failure string.
The collection service accepts this signal as pending only for the existing
Hook-only `require_terminal=False` call on a watching version-two record.
It rechecks current basis and the unchanged saved assignment under the existing
source lease after host I/O. Invalidated or changed selections still reject.
Concurrent publication requires exact saved readback or retains save-unknown.
An unchanged pending record is not written; previous failure facts remain.
Ordinary parent/worker recovery retains its existing incomplete result until
the valid completed answer can be frozen.

The Hook adapter returns a closed pending status/reason to the runtime and
`project_server` for both genuine parent and reviewer callers.
`review_wait_server._call` maps only that exact successful Hook summary to
the fixed supported `systemMessage` JSON described by the
[host boundary](review-completion-specification.md#collected-answer-host-boundary).
Other Hook successes retain neutral JSON, and failures retain their diagnostics.
No answer body, event identity or unknown provider text is rendered. This
presentation path adds no host read or lifecycle action.

`review_answer_runtime.py` and the dedicated bounded answer-hook bridge feed the
same collector from admitted hook/wait or explicit parent recovery. The hook
neither registers nor commits/completes, and numerical usage collection remains
separate. Its internal recovery/readback call alone permits a valid published
answer whose independently observed terminal state is still pending. It skips
an additional terminal lookup and retains every source, byte, basis, privacy and
unknown-effect check. Parent/wait recovery, ordinary readback and registration
keep the completed-terminal default; no terminal value is manufactured for the
neutral hook result. This pending-publication exception is version-one only;
version-two tracking waits for observed completion before adopting a response.
`project_server` exposes the project-owned `review_answer_collect`
with two exact argument arms: the typed five-field hook identity event or the
original Task plus ordered actual reviewer UUIDs, optionally `action=arm` only
for explicit pre-send continuation admission. Genuine MCP executor metadata
is checked independently. The parent arm binds only a fresh same-current-parent-
turn started dispatch or reuses an immutable saved binding, then performs bounded
missing-source recovery or exact saved-publication reconciliation. It creates
no wait, timer, message or registration.
Hook dispatch selects an already bound slot from parent/reviewer hints and
ignores the event's turn as a replacement for the saved child turn. This is
the explicit workerless entry; normal wait already binds/collects, so no
additional successful-path parent poll is introduced. Workerless recovery uses
the same saved basis and source checks. One
version-one correction intent is possible only under the source-format/live-observer rules;
it consumes the opportunity before any possible send and never creates a second
send after failure or uncertainty. Existing wait deadlines and parent resumption
handle unresolved work; add no timer, resident process or scan. Setup/host trust
and real-host validation are separate activation requirements, not consequences
of module installation or mock tests.

Managed waiting recognizes version-two tracking explicitly. A later turn from
the same dedicated reviewer does not itself terminate the wait as a mismatch;
the existing collection callback decides whether a valid answer was frozen.
Reviewer readiness and finalization use that frozen completed source, not an
incidental latest completed turn or a format-invalid response. Legacy waits
retain exact-turn semantics. Preparation uses the same tracking boundary as
the worker even when a later reviewer turn is already running. Optional initial
collection that has not established an assignment leaves ordinary exact-turn
waiting available after current-basis validation; invalidated or mismatched
assignments still fail closed. This adds no worker, timer or independent poll.
Managed worker callbacks retain the resolved prepared-slot reviewer order from
preflight and recheck its membership against the controller. The controller's
UUID-sorted occurrence identity does not replace collection's slot ordering.

A reviewer-origin hook matches the genuine executor session to the unique saved
slot under current-basis admission. `hook_executor_turn_id` tolerates only the
typed absence of executor-turn metadata. A supplied invalid, embedded-only or
conflicting turn is still rejected. For version one, a supplied valid reviewer
turn must match the saved active attempt. `ReviewerHookAdmission` pins the saved
basis and binding, plus the version-one attempt ordinal and turn, for the call.
Version two obtains the candidate turn only from the bounded public observation;
the executor turn is not an answer selector. Its admission and host-read guards
repeat these checks before effects and after helper initialization; a newly
selected request cannot redirect an in-flight read or publication. A parent-origin hook
without executor-turn metadata likewise uses saved-source recovery only. Fresh
parent dispatch admission still requires its genuine current turn.
Its dedicated read-only host reuses the existing bounded public relay with
unchanged executor metadata, permitting only the fixed two-turn `read_thread`
arguments for that reviewer. The shared parent host retains its rejection of a
parent listed as its own reviewer. Transport configuration is shared, not caller
authority; no parent metadata is manufactured and no wait, send, registration
or publication-reconciliation capability is added to the reviewer route.

Focused tests cover exact fenced/raw Unicode byte ranges, shape/privacy order,
closed values, per-attempt digest and conflict handling, publication interruption,
mixed transport, atomic unable registration/gates, exact historical audit,
protocol persistence and two supersessions with source-preserving capture.
The independent Reader tests disable product validators and reject recomputed
corrupt nested source, ancestry, correction, Receipt and capture relations.
Legacy codecs/sidecars, old request bytes and sealed Evidence remain exact;
reviewer `save` retirement requires proven drainage of supported old requests.

#### Rejected Answer Replacement Composition

The exceptional continuation composer owns rejected-answer replacement under
the existing ordered leases. A dedicated retirement reader yields bounded
structural descriptors, not a decoded rejected payload. It compares the saved
original reference and exact bytes, applies the former generic privacy and
shape/bound checks, and requires a current storage-field privacy rejection.
Normal collection, transport, provenance and registration continue using the
strict decoder.

The transition fence retains the complete retiring inventory and exact selected
rejection fields. It grants no write permission by itself: the continuation
repository repeats complete absence under its ordinary core writer and admits
the successor with retained healthy captures in the same transaction. Source
leases exclude concurrent registration until commit and connection close.
Every later registration compares the consumer to the fence, preventing an old
writer from using retained-source admission to revive a rejected batch.

Healthy sources retain old registration tuples in a bounded append-only list
when the successor reserves its distinct dispatch. Existing tuples are not
rewritten or reset. The finalization repository preserves a dispatching old
round verbatim while appending the exactly admitted replacement round; this
exception never applies to unknown commit/completion effects. Resume derives
all authority from current native state and the exact saved fence, not a prior
read-only observation or a caller-supplied absence claim.

### Optional Reviewer Verification Workspace

`review_workspace.py` owns optional caller-invoked restoration and cleanup,
separate from review transport and Runner execution. The helper parser admits
`workspace prepare|cleanup`; ordinary records-only policy rejects them before
filesystem access. The default Git read adds only a short `status=optional`
workspace notice routing to its existing `recovery_command`. It does not inspect
the temporary selector or allocate an ID. The same `--material-details` read
passes `details=True` to `workspace_guidance`, which returns `template_ready`,
`caller_language=python`, literal `caller_code` and instructions. Neither read
selects a parent or allocates a handle. `review_workspace_caller.py` builds the
fixed Python caller template and supplies `Workspace`, a local lifecycle object.
Both views omit workspace guidance for records-only and opaque
targets; neither creates anything or changes normal read/collect/save steps.
`validate_live_packet` shares the
existing complete saved/current public-Packet comparison between read and the
two preparation boundaries; it adds no direct storage access.

The template runs in one host invocation. Its caller supplies the already
selected existing argv/timeout and evidence-based normal-return process-end
condition; empty argv fails before selection. `Workspace` construction selects
the OS temporary parent once and allocates its UUID/location binding and literal
cleanup command before creation. The caller prints that command first, calls
`prepare()` once and runs ordinary `subprocess.run` only after ready. The helper
normalizes construction-time interruption across identity allocation, parent
selection, binding and cleanup-command formatting to `review_workspace_interrupted`
with `selection/unavailable` and unknown cleanup state. It publishes no partial
arguments or cleanup command and performs no preparation, verification, cleanup
or reselection after that interruption. It does not accept/execute argv,
callbacks or commands. In `finally`, the template
calls `cleanup(processes_ended=...)`; only literal True permits the existing
owned cleanup. Normal return can establish that value only under the caller's
known all-processes-joined condition. Timeout/interruption/launch error leaves
False; direct-child termination never proves descendant exit. Unconfirmed cleanup
returns `review_workspace_caller_refused` without filesystem access. Repeated
prepare on one object is refused even after failure. No automatic retry,
reselection, persistent handle or process supervision is introduced. A later
explicit recovery uses the retained exact cleanup command after inspection and
all-process-end confirmation. Whole-call/output loss remains an unknown outcome.

Workspace preparation binds raw Packet SHA-256 and a preallocated UUID hex
handle. It rejects nested Git coordinates, reuses `verification_runner_git`
observation, preflight and materialization, and derives an exclusive OS-temp
root outside Git ancestors. A new POSIX root uses mode 0700; Windows inherits
the physical temporary parent's ACL. Existing permissions are untouched.
Temporary location selection is read-only: first configured TMPDIR/TEMP/TMP,
otherwise LOCALAPPDATA/Temp on Windows or /tmp on POSIX. An absolute selector
is resolved strictly once to its existing physical path, admitting normal OS
aliases such as macOS /var without using them for writes. The entire resulting
physical ancestor chain is checked before retaining its path and identity.
Missing/looping/relative selectors or unsafe physical destinations fail; no
write probe or current-directory fallback occurs. This selection exception
does not follow links in Packet/source paths, materialized entries or cleanup.
New commands retain `--workspace-location`: at most 8192 ASCII characters of
canonical unpadded URL-safe base64 encoding of sorted compact ASCII JSON.
Its closed version-one object has `version`, `parent`, `parent_identity`
(`device`, `inode` nonnegative integers), `repo_sha256`, `packet_sha256` and
`workspace_id`. Version is integer one, not boolean. The parent is the exact
absolute normalized physical path admitted in the execution invocation (or by a
previously issued pair). Decoding rejects
noncanonical encoding, extra/duplicate fields, mixed legacy arguments and
binding mismatch before effects. This descriptor retains context; it does not
authenticate a caller or grant arbitrary filesystem authority.
The operation never resolves a new selector or follows aliases from the saved
path. It revalidates the physical chain and parent identity, including after
material preflight and throughout cleanup. A different current temporary
environment is irrelevant. Missing/unreadable/replaced original parents fail
without fallback. The legacy digest-only command keeps its existing selector
comparison; no inferred path, stored locator, automatic upgrade or search is added.
After exact materialization, POSIX 100755 entries receive mode 0700 through
identity-checked no-follow descriptors in this new copy only. Git administrative
path components are rejected before restoration.
Only `target/` receives exact blobs. A sibling bounded 4096-byte owner document
holds closed structural binding and physical root identity; no command or raw
execution output is retained. It is temporary cleanup ownership, not a new
Task record, execution identity or security boundary.

Cleanup uses `state_paths` physical-file/directory primitives after a full
no-follow inventory, capped at 60,000 entries, depth 66 and 30 seconds. It
revalidates identities at each removal and keeps the marker until target
files/directories are gone. It never calls recursive deletion. Failure removes
only a root created in that invocation when ownership remains provable;
explicit cleanup compares the retained Packet and marker independently of
later live Task transitions. Fixed diagnostic codes and absent/retained/unknown state
do not quote filesystem exceptions or rejected identifiers (an invalid handle
is null). Retained state starts immediately after mkdir succeeds, before the
physical identity check; a root without proven identity is never auto-cleaned.
Cleanup starts unknown until it can inspect the exact root; an early Packet,
location or access failure does not assert existing residue. Prepare starts
not_started. Confirmed root absence remains a successful absent result.
not_started does not establish absence/retry safety: a mkdir race may leave a
root created by someone else. `review_workspace_diagnostics.py` maps fixed stage
boundaries and typed exceptions (or one direct OS cause retained by HandoffError
or StatePathError) to the closed stage/reason pairs in the specification. It does
not parse exception strings or expose paths, raw CLI/verification output or
rejected values. `live_before` and `live_after` preserve incomplete public JSON
as `public_response_incomplete`; restoration and identity remain separate.
The original error's diagnostic survives ordinary best-effort cleanup failure;
cleanup outcome is reported separately. Interruption inside that recovery is
caught at the nested boundary: interrupted code, cleanup stage, retained state,
and no second cleanup attempt. Old location/digest CLI formats and no-fallback behavior
remain unchanged. Missing parents are never recreated or selected again.
`operate` is also the direct caller's sanitization boundary: it catches the known
input/domain, OS and subprocess failures formerly handled by the CLI wrapper,
without exposing exception text or command arguments. KeyboardInterrupt returns
a fixed interrupted code with retained/unknown state and skips automatic cleanup.
`validate_live_packet` normalizes known envelope/Packet structural validation
errors to `handoff_response_invalid`, including nested context errors. It validates
structure before comparing Task identity, preserving a valid different identity
as `review_target_mismatch` and a valid public error as its original code. Other
saved-Packet readers keep their existing validation; no malformed response is
repaired or accepted.
Portable fixtures supply their own physical temp parent and separately exercise
OS aliases, changed selections and unsafe destinations. Original save/submit and finalization are
unchanged. Tests cover exact index/commit restoration, the representative six
checks, ambient exclusion, binding drift, unavailable/incomplete material,
failure/timeout cleanup, lost output, quoted paths and records-only rejection.
Additional cases cover read-versus-execution lifetime, missing/inaccessible or
replaced parents, creation denial/race, incomplete public replies, post-create
identity failure, live processes, refusal without effects and explicit recovery.
Normal-permission host qualification compares actual separate tool invocations
with the same-invocation template; elevated tests or subprocesses within one
shell are not a substitute for that host-lifetime observation.

### Integrated Review Finalization

#### Conditional Continuation Composition

Continuation requires its admitted schema, immutable preparation and exact
settled-effect proof; it grants no migration or host activation. The core values and relations are owned by
[continuation storage](database-design.md#conditional-review-continuation-storage).
`review_handoff_continuation.py` composes the helper operation using existing
Packet/file validators and public registration; it owns no SQL.
`review_preparation_repository.py` owns a resolver-derived operational locator,
`preparation-<sha256(parent UUID:Task ID)>-<target generation>.sqlite`.
Its schema one has immutable `preparations(preparation_id,record_json)` and a
single `current(preparation_id)` FK. The closed record is at most 32,768 UTF-8
bytes: `version=1`, ID, complete core continuation basis, manual/integrated
route, exact Packet path/digest, one through eight result paths, nullable matching
finalization-store identity and creation time. Only fully published ready
preparation advances the pointer. Existing ignored physical-path validation and
the canonical resolver apply; older records remain immutable. Missing legacy
locator never authorizes recursive discovery or invented binding.

The legacy preparation record's exact keys are `version`, `preparation_id`, `basis`,
`route`, `packet_path`, `packet_digest`, `result_paths`, `finalization_store`,
and `created_at`. JSON is closed canonical sorted ASCII JSON; byte size includes
escaping. Version two additionally stores the immutable per-slot `collection_bases`
defined by [collected preparation](#collected-answer-collection); legacy bytes
remain unchanged. The nullable finalization identity is the resolver-owned basename,
not a caller path. It is required for integrated and forbidden for manual.
The repository's singleton `current` points only to an immutable saved record;
update/delete triggers protect `preparations`. First publication revalidates
core and physical Packet under the same preparation lease before creating the
ready record. Failed later publication atomically retains the previous pointer.
Schema 28's explicit ready preparation can publish this forward-compatible
structural locator while keeping its initial result/gate route unchanged. That
write activates no continuation, core migration, worker, or Git effect; it allows
an actually admitted preparation to survive a later explicit upgrade. Legacy
unknown caller/execution retains no locator and no inferred binding.

`review_handoff_scope.py` owns structural project resolution and sanitized error
codes shared by preparation, continuation and finalization. It grants no route,
ownership or effect admission. `review_handoff_originals.py` owns complete-batch
submission confirmation, physical original reading, source inventory and
composition using `review_handoff_transport.py` for shared path, file and pure
Packet/result checks. Structural preparation/finalization/request records use
only its lexical path validation, without adding filesystem or ignore queries.
Neither orchestrator imports the other or the CLI entry for these common operations.

`continuation_transition_repository.py` owns a separate schema-one intent at
`continuation-<sha256(parent UUID:Task ID)>-<target generation>.sqlite`. It bridges
core pending admission to settled old-worker cleanup without changing a live
version-one request or finalization store. Read/restart performs no cleanup,
join, activation or notification. `continuation_cleanup_repository.py` reads
the exact old request/controller/direct association: closed request plus known
timer/send outcome and bound `thread_join` proof are required when a worker
could have started. Missing marker/reference or timer deletion alone fails
closed. Only known pre-start creation admission without any direct marker
permits the no-worker case; unknown creation or send never does.

The closed transition/round record has `preparation_id`, `continuation_id`,
`continuation_sequence`, full core `basis_digest`, original `wait_basis_digest`,
`selection_digest`, `expected_attempt`, nullable `expected_request_digest`,
nullable `old_automation_id` and `old_request_phase`, `phase`, nullable
`worker_stop_identity`, planned `packet_path`/`result_paths`, and nullable
`packet_digest`. A collected round additionally carries immutable
`answer_protocol="review-answer-v1"` and, once fixed during settled preparation,
`collection_basis_digests`: one through eight ordered `sha256:` digests of the
complete per-slot CollectionBasis, matching the result-path count. The pin tuple
is written once after collection initialization and before prepared readiness,
and is preserved by every phase and request-journal copy. An exact repeated pin
is idempotent; a changed pin or first pin on a ready round is rejected. Empty
pins are omitted so legacy and older unpinned tagged payloads remain byte-exact;
older unpinned ready collected rounds remain readable history but cannot admit
collection or registration. Legacy encoding also omits its default protocol
tag. Pins remain inside the existing record bound and add no SQL schema or
public operation. See [collection preparation](#collected-answer-collection).
The original request digest covers its immutable identity including captured
pairs and automation, excluding only its monotone cleanup phase. Phases advance
one step through `fenced`, `settled`, `prepared`, `published`. Destination names
are bound from the fence before any creation, never changed or selected again
after a crash. During settled preparation the exact intended Packet digest is
saved before exclusive file creation; it is immutable once present. This binds
the actual observation bytes, including their verification observation time,
without regenerating a later observation on retry. Prepared additionally proves
complete physical readback. A retained matching file reconciles interrupted
publication; an intent without provable saved bytes remains unknown.
The fence reserves the tool-generated continuation ID, sequence and selection
digest before short core admission. A fence without its core row can therefore
reconcile that same explicit operation after rechecking the predecessor and
expected request. A legacy core-only intermediate without a provable destination
remains unknown; it cannot be rebound to a supplied directory.
Residue after interrupted creation stays unavailable until exact physical
reconciliation, never triggers creation at another location. Non-pre-start settlement
requires the existing same-worker join checksum. Each record is at most 32,768
UTF-8 bytes of canonical sorted ASCII JSON. No result body or host metadata is
stored. The core continuation owns action/reason/selected identities.

Both the transition store and schema-two request journal use
`rounds(ordinal,preparation_id,sequence,continuation_id,payload)` with generated
positive contiguous ordinal, unique continuation ID, unique preparation/sequence
and sequence one through sixty-four per preparation root.
The last per-Task row is applicable only when its wait-basis digest matches the
current binding. Historical admission validates the old native record against
its own digest/selection/preparation and a strictly newer current generation,
keeping project/path/binding/parent identity fixed. `ManagedWait` rechecks that
record and exact journal row under request admission. It uses the shared cleanup
reader on the latest closed request, including bound timer/send and actual join
proof, before monotonically consuming an obsolete prepared round. In this
managed generation transition it passes the exact available direct owner to
`observe_latest_cleanup`; the reader rechecks controller/request/probe/worker
identity and exit, accepting direct confirmation even when saving failed.
Without an owner it still requires valid saved join proof. Explicit continuation
preparation and same-basis renewal retain their existing durable-proof gates.
This is not reviewer completion or judgment reuse. Subsequent ordinary attempts retain the
published history but carry no old continuation ID. Read-only inspection does
not consume a round; unknown effects or identity disagreement still fail closed.
Explicit continuation preparation also admits a latest closed request from a
strictly older target before creating a new fence, or while reconciling its exact
saved fence. The historical basis reader validates the old Manifest/authority
and actual execution/owner transition; continued requests additionally require
their native round and selection. The composer proves the exact request,
controller/direct binding and joined cleanup, then passes an invocation-local
proof to cleanup and request installation. Both compare the old immutable request
digest and basis independently of the new round's current basis. Installation
also matches the saved join checksum under its writer. No serialized field,
caller permission or historical judgment reuse is added. Same-generation drift,
future targets, unknown effects and unready native history remain rejected.
When the admitted request journal has sequence zero and no request, the composer
may consume an older unused prepared round only after the same native
digest/selection/preparation, strictly older generation and fixed identity
checks. The exact empty journal establishes that no wait occurrence exists;
it creates no worker-stop proof and cannot bypass a recorded attempt's cleanup.
This applies both before a new fence and during exact saved-fence recovery.
`current_round(singleton,ordinal)` explicitly selects the appended current row;
earlier rows must be published and cannot be overwritten. Different explicit
preparation roots keep independent continuation sequences in the same Task
journal. Existing database-size and wait-attempt bounds remain in force.
Schema-one transition history uses `review-continuation-transition-v1`;
schema-two request history uses `review-wait-request-v2`. Exact schema inspection
and complete relevant rows, including older rounds, are validated on reads.

Schema-two request attempts retain the exact old five-field JSON when no
continuation is bound. New attempts additionally contain the exact continuation
ID/sequence and at most eight selected pairs. Only an admitted prepared round
permits same-parent-turn replacement. Appending its creating attempt and
publishing its round happen in one short transaction. Already-ended selected
reviews publish the round without a new attempt or timer. Conversion requires
explicit settled preparation, validates every old attempt as closed and keeps
all old JSON bytes. New schema and current pointer are privately validated in
the write transaction and published together at commit; reads never convert.

A published continuation round may bind successive scheduled wait occurrences.
The new occurrence must follow that same round's latest closed attempt in a
different actual parent turn, retaining the exact basis and selected reviewer
pairs. Managed admission proves the previous worker's joined exit and settled
timer/send before append; request admission independently compares the immutable
identity and requires closed cleanup. Repeated attempts for one continuation
must be contiguous, with unchanged pairs and distinct adjacent parent turns.
The round is not republished or appended, and its original expected-attempt
token continues to describe only its first occurrence admission. Same-turn
duplicates and rounds published without an earlier occurrence cannot create
a renewal. All attempts still count toward the existing per-Task capacity.
The request repository uses a one-second SQLite busy timeout so a worker's
short occurrence-fence read can settle while admission records `started`.
Only SQLite lock acquisition waits; no transaction, host operation or failed
commit is replayed, and the request OS writer lease remains nonblocking.

Operational request and integrated finalization journals gain schema two only
through explicit settled continuation. Conversion retains exact version-one
JSON/observations as the first round, validates original basis and known effects,
and atomically publishes a private validated store under admission. Reads never
convert. New request attempts bind continuation/sequence; same-parent-turn
replacement requires that exact pending core continuation. Old pairs never
change. Capacity remains sixty-four rounds, eight slots per round, with existing
wait-attempt and loaded-association bounds unchanged.

Finalization retains one fixed root and appended rounds. Root Packet/basis/branch,
candidate, commit and completion effects never reset. Each round retains its
continuation, exact originals/result inventory, registration, reviewer
observations and blocker. Only the current pointer advances. Previous successes
and terminal observations stay historical. An incidental turn mismatch can be
retired only by validated required judgments; actual failed/interrupted/unknown
review requires a selected explicit continuation and matching new judgment.
Manual locators create no finalization journal or Git stage.
For historical all-completed mismatch, the finalizer requires a validated
current-basis head for every observed actor rather than requiring an invented
failure actor. Empty/unknown observations keep the blocker. Verdict, required
independent-count and Finding checks remain separate and authoritative.
Current-round qualification also verifies exact registered original bindings and
the satisfied native gate before each affected effect boundary. It preserves
the blocker and permits saved-result precedence without manufacturing a round.
Reports offer the existing same-basis continuation only for unchanged material/conditions
and preserve the fresh-target and unknown-effect recovery boundaries.

Schema-two finalization has a closed effect-root `intent` and
`rounds(sequence,continuation_id,payload)` with one `current_round` pointer.
Round zero retains the exact legacy intent JSON; sequences one through sixty-four
retain actual continuation batches. The effect root retains basis, original
Packet/digest/result paths, branch, candidate, commit and completion phases.
Current-round reads compose those root effects without resetting earlier
registration, reviewer observations or blockers. New round fields are
`continuation_id`, `continuation_sequence`, `preparation_id`, `active_sessions`,
`retained_receipt_ids` and `original_locations`. Each original location is the
exact Packet/result pair; original session/digest pairs and Receipt IDs bind the
complete newly registered inventory, which may exceed the selected active slots.
The shared `review_handoff_originals.compose_inventory` reads supplied and retained
originals for both manual submit and finalization. It owns no SQL, lease or effect
permission. Finalization reads structural preparation and
transition records without taking their leases beneath its own admission;
native registration revalidates the pending round in its writer. Reports use
the frozen original locations after registration, including after ownership is
released. Known registration is never replayed to rebuild a report. Fresh
effect-boundary checks reject an obsolete continuation even when successor
judgments now satisfy the quality gate.

Common nested admission order is preparation, transition intent, request, same-parent dispatch,
direct controller, finalization, then short core writer, skipping unused locks.
Workers use only dispatch/controller/finalization/core in that order and never
acquire preparation/request while holding them. The helper holds dispatch and,
for an integrated route, the existing finalization lease while reserving the
fence and admitting core intent. It only observes cleanup; when cancellation or
join is required it returns the existing stop/reconcile action and releases its
leases. The stop service performs those effects outside the helper's admissions.
Host calls and join never span a core writer. Explicit reconciliation acquires
the same order, compares the transition and old round, and rechecks core basis.
Waiting captures and rechecks actual selected turns under its existing admission.
Its preflight and request-admitted creation boundary also compare the latest
transition fence with the exact request round. A saved successor fence blocks
obsolete timer creation even before native admission or round installation;
the existing start/worker checks independently protect activation and later
effects. Helper readiness accepts the latest validated occurrence of that same
published round without publishing reviewer requests again.
Old workers check the fence before Git, timer activation
or send. Crash at any boundary leaves a pending recoverable transition; reads
and restart launch nothing. Reconciliation preserves known success; conflicting
selection or unknown effect cannot publish or replay.

Continuation preparation has named boundaries for operation/predecessor admission,
settled cleanup, Packet creation or comparison, integrated-round append, core
preparation and request installation. Pending supersession and registered
workerless completion retain distinct conditions but share old-round cleanup
and publication. The composer carries a typed `ContinuationScope` for the
admitted project/root and `ContinuationLeases` borrowed from one lexical
`_admission_scope`. That scope alone acquires and releases preparation,
transition, request, dispatch and optional finalization admissions in the order
above, including partial-acquisition failure. An `IntegratedAdmission` pairs
the already authorized journal with its lease; the manual route has none.
`AdmissionSnapshot` keeps the observed native history and prior request together.
The entry advances through `AdmittedContinuation`, `SettledContinuation`,
`PacketPreparation` and `ReadyContinuation`; stages neither acquire earlier
leases nor extend their lifetime. `FailureProgress` separately retains only
acknowledged records and the active diagnostic stage. It preserves failure
identity even if a later check raises before returning a stage result; lost
acknowledgements still require observing the saved state on the next invocation.
These internal values are not persisted, permission tokens or replacements for
fresh readiness checks. A retry reads each saved stage and writes only a missing local
stage. Exact physical Packet bytes at the persisted path can reconcile an
interruption after creation; missing or changed published Packets, partial
residue and unprovable content remain unavailable without deletion or overwrite.
Normal execution and retries use one readiness predicate: current preparation,
latest non-superseded core continuation, complete basis and selection, matching
physical Packet/digest, current request round and any required integrated round
must all agree. An obsolete successful operation is history, not executable
readiness. Known successful stages are not repeated, and retries return no new
reviewer dispatch instructions. A fenced or settled transition with a null
Packet digest cannot have exposed executable requests. Reconciliation of that
pre-publication boundary returns the first selected requests after known cleanup
settles; any Packet-bound or later transition retains no-redispatch behavior,
including an uncertain readiness acknowledgement.
For an already installed current occurrence, readiness validates its actual
request/controller/direct binding and rejects in-flight or unknown send/timer
effects. A healthy current active worker may remain active; only replacement
requires settled cleanup and worker exit. The finalizer also reads the saved
transition fence before effects, so a fence whose core admission was interrupted
already excludes the older finalization round.

The shared effective evaluator serves Receipt/gate reads, Viewer, manual done,
finalization and locked cycle selection. Completion compares the continuation
revision in its writer. Qualified saved judgments precede new activity checks.
The finalizer separates full result inventory from selected active pairs; a
worker watches only those pairs and rejects unexpected changes within a round.
When native registration or the finalizer's saved-result predicate qualifies
reuse, managed admission keeps the exact current occurrence's validated pairs
for duplicate/cleanup comparison and requires their session set to match the
requested reviewers. It makes no fresh child read and does not project those
saved turns as current activity. A later parent turn still performs known
cleanup before returning review end; same-turn readiness and unknown-effect
guards retain their existing meaning.

Locked cycle selection borrows one typed `EffectiveReviewSnapshot` for its
immediate gate projection. This lexical read scope is confined to the same
connection, unchanged transaction and full validated Task projection; it expires
on exit and is never a persisted completion token. A private SAVEPOINT proves
the original transaction even after a write-free COMMIT or ROLLBACK followed by
BEGIN; total changes and the schema cookie must also remain unchanged. The
consumer checks and refreshes this outer guard before opening its inner
`ReviewMaterials` SAVEPOINT, then passes the resolved view directly into that
immediate read. Refreshing an outer guard while an inner scope is open would
release the inner SAVEPOINT and is prohibited. Normal and exceptional exits
release the private guard; cleanup failure never hides the original exception.
Other reads and later writer
or effect boundaries validate current state independently. Resolution links use
one task-local native predecessor index and validated ancestor sets for complete
chain and effective-head reachability checks. Audit limits do not bound those
chains. Bundle construction and the independent reader keep their separate
semantic validation.

Notification identity adds nullable continuation ID and positive round sequence
to existing Task/target/probe identity. Success and safe failure share one send
state under dispatch admission. Failure notice requires a fresh known idle
original parent, unchanged basis/owner and known safe timer cleanup; otherwise
only diagnosis is available. Unknown send blocks replacement; known old send is
historical and never replayed. Receipt must match this occurrence. Deadlines,
frame bounds and numerical attribution retain their existing contracts.

Managed waiting validates a prepared operational round against one admitted
native continuation snapshot, including its selection/full basis digests, then
captures only that exact selected set's current pairs. After request admission
is reacquired, it compares the saved round and renews native admission before
publishing an attempt. Already-ended selected reviews publish the round without
an attempt. The request's current bound continuation plus the operational
transition fences obsolete workers before activation, finalization or sending.
The finalizer and worker consult qualified registered judgments before unrelated
fresh child activity; they never fabricate a completed child observation.
Old execution-failure observations remain immutable. Automatic progression can
retire a named old failure only with that same actor's actual registered judgment
in a later explicit round; an unbound old unknown remains unavailable.

`ManagedDirectProbe._on_failure` uses the existing same-parent dispatch and wait
admissions. It can report a fixed invalidation only while that probe is waiting,
its exact basis/occurrence is current, its parent is freshly idle, and timer
deletion is known. It neither retries busy-parent delivery nor settles unknown
creation, cleanup or send. Failure and success use the same monotone
waiting/dispatching/accepted-or-rejected-or-unknown decision. The failure reason
survives settlement. Its version-two receipt header binds probe, actual parent,
Task/target and nullable continuation/sequence; it asserts no PASS or completion.
Unsupported/unsafe failure delivery retains existing suppression and diagnosis.

The [conditional integrated behavior](review-completion-specification.md#integrated-review-finalization)
is implemented in focused orchestration, operational repository, Git adapter and
delivery responsibilities. The existing manual helper remains independent.
The retained `finalize` invocation is both workerless explicit continuation and
conditional recovery; it is not restricted to failed worker operations.
Consumer guidance normally selects integrated preparation for authorized
supported snapshots and delegates unfinished reviews to the enabled, available
and authorized wait service, ending the parent turn on confirmed readiness.
Workerless continuation covers already-ended reviews, waiting OFF/unavailable
and explicit parent-managed waiting; it is not the normal preference-based
substitute for the service. Existing manual/direct transports retain their
conditions and required permissions. This sequencing uses the existing helper
commands and runtime; no automatic route selector or fallback is introduced.
Integrated invocation carries the caller's project-authorized intent, not an
approval boolean. Current typed caller identity and structural generation checks
remain mandatory; no parent ID is inferred from prose or numerical records.

`review_finalization_git.py` owns only the fixed local Git exception. It reuses
`capture_git_snapshot`, the canonical entry/fingerprint decoder and
`verify_git_snapshot_commit`. A stable stage-zero index yields immutable trees
through fixed `mktree -z` operations and one `commit-tree` candidate. Tree paths
are raw Git bytes; nested directories are constructed bottom-up. Candidate
construction uses `stream_index_fingerprint`'s validated entry callback to
populate that directory structure and compares its complete fingerprint before
creating any objects. It retains the directory metadata needed for tree
construction, without an additional whole raw listing or flat entry list.
Per-record and subprocess limits remain unchanged, as do changed-manifest caps.
Candidate creation leaves refs, the index and the working tree untouched. The orchestrator
persists its full ID before attempting publication. Fixed argument vectors,
sanitized environment, bounded time/output, no stderr retention, no lazy fetch
and disabled fsmonitor/signing prevent extra execution. The generic Task title
is not inserted into a shell or a commit-message argument.

Publication obtains an exclusive physical index lock without overwriting foreign
residue. A fixed `update-ref --stdin --no-deref` transaction prepares its old-ID
branch update; Git owns the branch and implicit HEAD locks. Before committing the
prepared transaction, the adapter requires the physical HEAD lock and rechecks
the symbolic HEAD, basis and gates. Unsupported ref backends retain the manual
route. EOF aborts an uncommitted transaction; the child is always reaped. The
resulting topology/tree must match the original snapshot. Only the adapter's own
unchanged index lock is removed by the adapter. Failure after dispatch is not
claimed as rollback: the saved candidate and actual refs determine recovery.
An explicit retry may reuse only that candidate after observing the original
base/index, with the same old-ID comparison, never a second commit.
The adapter rejects detached HEAD and nested project-relative snapshots;
the manual route preserves project-required hooks, signing and those layouts.
Core connections are closed during Git work; operational writer leases do not
hold SQLite transactions across subprocesses.

`review_finalization.py` composes the existing typed CLI registration/completion
handlers and their bounded maintenance, with the genuine parent caller identity.
Its Core detail reader shares Task/Contract, selected review, verification,
ownership, checkpoint and note validation with public show, but omits numerical
usage and public text/Advisory assembly. Generation discovery, fresh effect
checks, completed/check recovery and notification checks all use that internal
reader; no value is reused across snapshots. Exact collected-registration
inspection separately validates the selected Task and complete review storage,
then reads only the exact preparation/submission inventory in that snapshot.
It does not assemble report detail. Public show/context and post-registration
context retain their numerical usage projection. Registration/completion writers
and locked rechecks remain unchanged.
`review_finalization_basis.py` carries the immutable retained project, execution,
owner generation, Contract, target and manifest constraints through those handlers.
Native admission checks the retained project binding. Batch registration checks
the retained Task/owner under its existing writer lock before inserting any
Receipt; the CompletionPlan carries the same constraints into the native writer.
Its locked completion-basis validation also repeats the integrated all-severity
Finding check. These constraints are internal parameters, not public arguments,
and ordinary manual registration/completion keeps its existing conditions.
`review_finalization_repository.py` owns the operational journal and read-only
reconciliation against immutable core review/session bindings and all registered
Findings. `review_continuation_repository.read_original_sources` shares the
query-only source interpretation used by publication preparation, registration
and this reconciliation. It returns current selections plus validated legacy
and collected captures with their immutable source continuation IDs, including
null initial sources. It does not call writer admission or authorize effects.
`registered_originals` restricts candidate Receipts to the retained project,
Task and complete target, then compares session/original/execution bindings and
source judgment IDs. Recovery uses that same comparison after registration;
same-body predecessor judgments cannot substitute for the expected source.
The finalizer's `_require_registration` reuses that query-only comparison with
the journal's exact ordered Receipt IDs and freshly reads the saved originals.
It runs on successful-registration reentry before any completed/check shortcut,
before candidate creation, inside each publication revalidation and immediately
before native completion dispatch. Saved-result and notification admissions use
the same check. Each boundary reads anew; journal success and unrelated current
PASS counts are never a substitute. Reporting an unavailable exact registration
also marks the result attention-required without erasing earlier effects or
changing the actual Task state. No registration replay, state repair or new
persisted field is introduced; native writer and all-severity gates remain.
Existing locked basis, selection, capture and expected-head checks stay in the
registration writer. The canonical resolver's `review_finalization_store` names one
`finalize-<sha256(parent UUID:Task ID)>-<target generation>.sqlite`. Its exact schema
one contains `schema_history` and one canonical bounded JSON `intent`; it is not
part of core backup or schema migration. A physical writer lease spans effects,
with short transactions only. It retains the closed wait basis, Packet path/digest,
1–8 result paths, existing branch, original session/digest pairs, Receipt IDs,
candidate ID and monotone registration/commit/completion intent/success phases.
It also retains at most eight exact reviewer/turn/status observations and one
closed reviewer blocking code. Admission, ordinary wait reads, dispatch preflights
and finalization guards retain each terminal or unavailable observation before
another read or handoff. Observations accumulate by reviewer identity; an existing
turn cannot change and a failed/unknown observation cannot be replaced. Partial
observations may grow to the complete set, whose identities must belong to the
validated original batch. Healthy unfinished children add no failure. An initial
unavailable read without a captured turn stores only the existing unknown blocker.
Preparation uses the same bound admission reader and captured pairs as the first
preflight, retaining each reread before another child read or worker startup.
The scheduled preflight compares previously captured pairs and records failure
before cleanup or `reviews_ended`; it does not require a worker to reach sending.
Closed manual requests without an integrated intent release their old pairs for
explicit new association; active requests and integrated intents retain them.
The worker refreshes these observations at each effect boundary;
failed/interrupted, unavailable or changed turns latch a blocker for this intent.
`ManagedDirectProbe` supplies only its guard to `Finalizer.execute`.
Each guard check uses `_read_reviewer`'s one fresh `ChildTurn` for both the
status/thread-status readiness decision and the immediate observation callback.
There is no preceding batch observer, cross-boundary cache or delayed batch
save. Busy-parent retries and later registration, commit and completion guards
still read anew; the existing native writer checks remain independent.
The finalization guard uses its existing journal lease for those callbacks;
earlier wait reads take their own short journal lease without nested acquisition.
Parent-turn changes, parent-read failures and cancellation do not create reviewer
failure evidence. A subsequent absent observation or completed status cannot erase
that blocker. Recovery and read-only reports retain its reason and require fresh
evidence under the [continuation composition rules](#conditional-continuation-composition)
for unchanged material; only that explicit route may qualify retained judgments
or retire a named old failure through the actual selected reviewer's successor
judgment. Changed material or conditions require a new target generation/intent.
Complete saved originals can still be registered as one batch; an unresolved
blocker prevents automatic Git/Task completion.
No earlier intent missing these closed fields is silently upgraded or repaired.
No prose, result bodies, raw Git output or variable notification is journaled.
The report labels a retained completion check `observation=before_completion`
and omits that check's obsolete action hint; current Task/stage observations and
the root next action retain their own meaning. After the existing
`registered_originals` reconciliation succeeds,
`project_original_summaries` uses their ordered immutable Receipt bindings and
exact reviewer/kind/verdict/summary equality to emit same-response JSON Pointers
with explicit Receipt IDs. It copies only the affected output dictionaries;
original files, stored rows, provenance, Findings and mismatch/unavailable
branches are unchanged. No extra database/host read or new journal field is
introduced. `ManagedHost` transports this same report and explains the pointer
and observation meanings without changing delivery or completion gates.
Unknown registration/completion acknowledgements are settled by observed immutable
bindings and exact native completion state. A new target cannot overwrite an intent.

The repository's `reported_review_evidence` separately constructs reporting
Receipt roles from the observed original IDs and frozen retained IDs. For a
continuation it reuses the validated native same-basis judgment graph and walks
only registered predecessor edges from those roots. Pending selections cannot
introduce history. Receipt selection, complete Finding validation and projection
share one read transaction; Receipt IDs and Finding IDs are validated in separate
batches using the existing validator capacity, so each Finding batch's implied
parent union also remains bounded. IDs are deduplicated and relationships are
returned as `reported_receipts`. Other Task/target evidence is rejected. A failed history
read returns null collections and `reported_reviews_and_findings` unavailable,
without discarding independently observed original registration or replaying it.
The bounded Receipt validator returns the exact provenance from its already
validated native Reference sources. Reporting projects only each selected
Receipt's saved checks and limitations, preserving empty arrays; legacy
versions explicitly report unrecorded null arrays. This borrows facts from the
same read transaction, without another provenance lookup/validation, a global
history read, persistent cache or any Evidence/Bundle format change. Finding
batches retain their validation and discard this optional return value.
The Receipt and Finding batches share only immutable manifest/authority
validation through `review_material_repository` for that same read transaction.
Source, Reference, status and continuation graph checks remain independent.
The original-summary projection never consumes this expanded reporting set.
`recorded_work.recent_notes` filters already validated normal Task events by
`note_added` or non-null `note_body`; it does not parse summaries, widen history,
revalidate by weaker rules, or synthesize missing legacy bodies. The same report
is used by execution, completed `--check`, and existing bounded notification
assembly. Overflow retains its explicit incomplete-report behavior.

### Host-Owned Review Waiting

The packaged `scripts/review_wait_server.py` runs `project_server` with only
repository, public bundled MCP wrapper, Codex home and confirmed timezone.
Its nine closed wait controls include the normal `wait`, optional `inspect/stop`
and six compatible per-reservation controls. Discovery and startup create no
review-wait or answer-collection effect.
The six compatibility descriptions label the earlier association/recovery use
and identify the normal alternative without hiding controls or changing schemas.
The Skill's normal-wait entry routes tool discovery to the exact published name
within the connected server namespace, reusing an already available definition.
Conditional recovery names its applicable controls. This is caller guidance;
the nine-entry catalogue, tool loading, schemas and dispatch remain unchanged.
`managed_wait.ManagedWait` owns single-call admission, creation, preparation,
start and healthy repeated waiting; the source compatibility entry keeps its
explicit injected paths and old catalogue.

The service entry separately starts the
[backup opportunity observer](setup-state-design.md#asynchronous-backup-worker).
It is independent of review-wait policy, reviewer workers and reservations;
constructing a session, discovering tools or calling a wait control does not
start it. Service startup/takeover establishes bounded ownership and baselines
old requests without launching them. This adds no public tool, executor input,
review timer or review completion gate.

`CanonicalStatePaths.review_wait_root`, `review_wait_store` and
`review_wait_request_store` and `review_wait_dispatch_store` own all paths. Reservation filenames hash the actual
returned ID. A request filename is `request-<sha256(parent UUID:Task ID)>.sqlite`
under the same operational root. Physical scope, canonical binding and enabled
policy are checked before mutation; current Task ownership is validated before
directory creation. This subtree remains outside core backup/recovery inventory,
like numerical state, and is never restored into active workers.

The dispatch namespace is `dispatch-<sha256(parent UUID)>.sqlite`; only its
one-byte `.lock` sibling is created, with no SQLite payload or schema. The
request repository's `ParentDispatchRepository` reuses physical/no-link and
nonblocking OS lease admission. Initial creation flushes a private one-byte
sibling and publishes it with the existing no-replace primitive, so competing
workers never see a partially written lock. A losing publisher removes only
its own validated temporary and admits the existing complete lock. Contention defers the worker within its original
deadline; no host effect or transaction is retried. The lease spans fresh checks,
deletion, finalization and one send across same-project/same-parent workers,
without serializing unrelated parents or claiming atomic host compare-and-send.

`request_repository` reuses the wait repository's physical/no-link admission,
bounded SQLite connection and OS writer lease. It uses a single physical
observation for an optional rollback-journal sibling,
which SQLite may remove on concurrent commit. Missing that optional sibling is
allowed; required files, nonphysical entries and other inspection errors still
fail closed. This adds no transaction or effect retry. Its separate schema version one
has exact `schema_history` and `attempts` tables. Each bounded canonical JSON row
retains basis digest (excluding generated wait ID), actual original parent turn,
sorted reviewer UUID/turn pairs, returned automation ID or null, and phase
`creating|created|prepared|started|failed|closing|closed|unknown`.
At most 64 sequential rows preserve prior attempts. Only the latest row advances;
a new attempt requires closed cleanup and a different actual parent turn.
Creation intent precedes the host call. Transitions use short transactions
inside the request writer lease; host effects occur outside transactions.
No raw SQL belongs in orchestration and no core schema migration is introduced.

`managed_host.ManagedHost` inherits the public adapter's genuine metadata,
per-operation correlation, transport and fixed physical config admission.
It creates only a PAUSED same-parent heartbeat through public
`automation_update` with explicit `destination=thread` and `targetThreadId`,
a generated name, fixed Task recovery prompt and validated rule. It requires
the structured creation ID/status receipt, then verifies the physical config.
Unknown creation remains durable and cannot be retried or replaced. Fresh
`ReviewWaitSession` preparation verifies the same captured reviewers and basis
before the existing version-two direct start. Duplicate same-turn calls return
readiness only from the same live owned worker and confirmed active state.

`ManagedDirectProbe` adds integrated finalization before sending when an exact
prepared intent exists, and post-send observation for both routes.
Ordinary polling follows the
[observation cycle](review-completion-specification.md#review-wait-observation-cycle):
host/control facts are observed on a ten-second cadence, separately from
core-consuming result collection and completion decisions. An exact host-ended
turn is not derived from a frozen collected answer. Each handled reviewer change
is scoped to the admitted preparation/request/turn, so one collected reviewer
does not keep core admission, all-reviewer tracking or finalizer creation active
while another reviewer runs. The existing valid saved-result path remains
available. Saved-result readiness is established before collection-source admission,
including initial observer construction and readmission. Control observation
remains independent of that optional collection setup, so a legitimately
completed Task with released ownership retains its guarded notification route.
All-ended and parent-idle conditions admit pending-result observation;
only unresolved targets are checked again at the bounded cadence. Parent busy
state returns to light observation. No observation snapshot certifies a later
effect or survives as a reusable validation proof.
The direct record is observed once at the cycle boundary. Intervening host reads
use local stop/clock checks; journal, collection and dispatch effect boundaries
still obtain fresh cancellation state. No direct snapshot survives into a later
cycle or lends effect authority.
The worker's Task-local control observer follows the
[database boundary](database-specification.md#operation-local-admission): unchanged
physical hints open no core connection; changed hints compare only the original
Task's controls before requesting existing full admission. The control observer's
initial changed result is not consumed without admission: baseline capture is
followed by fresh preparation, detecting drift since the first admission.
Request and continuation occurrence reads use their validated observation paths
only in ordinary polling; native SQLite busy defers the tick, while effect
guards retain ordinary reads and invalid identity/state rejection. Pending collected
answers use a bounded host-only candidate probe. Its non-persisted comparison
state follows the separate current collection policy: OFF prevents body probes
and cannot consume pending progress. Re-enablement discards the old comparison
and progress hints and establishes fresh admitted tracking, including when only
one reviewer ended. A policy change between probe and collector cannot strand
that same candidate. Durable frozen and unknown-effect state is preserved.
The initial observer uses the policy value actually read during source
admission; an earlier separate policy sample cannot label an OFF admission ON.
Unresolved tracked reviewers retain bounded candidate probing at the normal
cadence even while a peer runs. Only candidate changes enter collection; a
handled host terminal state cannot suppress later answer changes or consume an
unprocessed candidate. Frozen results do not trigger a new body read.
The comparison key includes completeness/source/terminal and raw-body SHA-256, so same-ID
content or completeness changes remain observable. It keeps no body and has no
role in acceptance, privacy admission, stored `observed_digest`, or effect
authority. Wait/basis replacement discards it. A private purpose-specific
`PublicAnswerHost` adapter shares the probe's key calculation for its single
latest-answer override: the fresh collector's actual successful or unavailable
read becomes the last hint, including when it reads a different rejected body.
It retains no body and performs no general delegation or provider caching.
New candidates invoke the existing fresh collector; a complete candidate
interrupted by native `writer_busy` may request re-observation through an
internal callback without changing public output.
Only the next fresh operational snapshot's watching/no-publication/not-started
registration state clears that exact candidate hint. An observation busy keeps
the bounded request for the next poll; generic exceptions, mismatched candidates
and unknown publication/registration do not authorize replay. Current
policy/basis/unknown-effect guards still apply.
The inherited worker retains intent-before-effect, one deletion/send attempt,
parent/reviewer freshness, due cutoff and twenty-minute outer deadline. After
accepted/unknown send settlement, it observes the original parent for up to
120 seconds within that outer deadline, never sending again.
Managed parent freshness means the original parent identity and current idle
state; another Task's newer turn does not invalidate its bound wait. Busy reads
defer deletion/sending; after confirmed deletion the same worker may wait for
idle without deleting again, within the original outer deadline. Integrated
effect guards wait for idle under that bound, without retrying finalization.
A prepared result is retained transiently if the parent becomes busy again.
Before resumed sending, read-only finalization checking rebuilds that report
against its exact retained intent and current evidence. Reopen or target/owner
drift produces a bound failure report with prior successful stages preserved;
registration, commit and completion are never repeated to refresh a notification.
Known target/ownership drift during saved-judgment lookup restores observation
of the original bound reviewers so the existing finalizer can report the mismatch.
It does not relax native effect guards or the occurrence fence; unknown reads
and continuation conflicts remain failures rather than reusable judgments.
Read-only checking does not rerun business gates; an earlier known attempt's
blocking reason remains unless the check supplies a newer failure or confirms
completion. A successful read alone never erases an unresolved attempt failure.
The host adapter permits report replacement only before sending and under the
same complete notification/Task/target identity. Its local prompt lock freezes
the body before the single transport attempt, including unknown/rejected outcomes;
later receipt comparison cannot observe a rewritten message.
Legacy experiments keep their original-turn rule.
The managed failure route uses `_failure_parent` after the ACTIVE heartbeat
read and again after notification observation: it refreshes cancellation/clock/
deadline, parent identity/idleness and the existing `_prepared` policy/basis
admission. It does not require the invalidated reviewer observation to qualify
as ended. The due cutoff is checked before the heartbeat read and immediately
before saving deletion intent. A deleted timer bypasses only that new-sequence
cutoff, never the fresh send guard or outer deadline. Existing failure
suppression, known-ACTIVE cleanup and uncertain-effect exclusions remain;
there is no new retry, worker or timer state transition.
When scheduled-check preflight and an existing worker retain reviewer facts at
the same time, the finalization observation writer may wait at most one second
for its OS lease. Only lock admission repeats; transactions and effects do not.
The operational finalization repository uses a one-second SQLite busy
timeout so concurrent preflight reads and short observation commits can settle.
No transaction or external stage is replayed. The request repository has the
separately described short occurrence-read timeout. The direct repository also
uses the bounded native wait described below; other wait stores keep
their existing admission policy.

Immediately before the durable send intent the worker captures the fresh idle
parent turn as this attempt's transient receipt fence. After send settlement,
`ManagedHost.observe_receipt` requests newest-first public `read_thread` pages
with one turn each, at most sixteen pages per scan and one shared host timeout
clipped to the receipt deadline. It follows bounded nonrepeating cursors, rejects
duplicate turns, and must reach the fence. It chooses the oldest matching turn
strictly after that fence; missing history/bounds never choose a partial candidate.
Later B turns therefore cannot hide A's first received notification. It validates
the structured incoming
`codex_app.send_message_to_thread` delegation event for its outgoing notification.
For integrated results, a small leading identity contains protocol version,
probe, original parent, Task and the complete retained target basis before the
variable result. This is ordinary prompt content, not invented host metadata.
Untruncated reads require the exact envelope/body. Truncated reads require the
same-parent envelope and complete leading identity, an exact visible UTF-16
prefix of the sent message at the requested cap, and the public host's integer
`originalChars` equal to the expected envelope length above that cap. The host's
observed `output.text`, `truncated` and `originalChars` shape owns this branch;
an incompatible or incomplete shape fails closed. Split surrogate pairs are
compared as UTF-16 units. This establishes correlation only: the unseen suffix
is not verified by its length or by a sender-controlled identity/digest.
Only that observed UUID updates the existing monotonic acknowledgement field.
Fence/cursors/bodies stay transient. No worker restarts after process loss. Old turns, unrelated inputs, plain echoes and unknown
reads cannot set receipt. The legacy direct worker and payload versions remain
unchanged and still support their separately selected manual acknowledgement.

After confirmed deletion, integrated finalization receives a guard that rechecks
the exact idle parent, reviewer turns, cancellation and deadline before effects.
Its factory requires enabled policy and a matching canonical intent. Current
basis drift permits only bounded failure reporting to the original parent, and
legitimate native completion is recognized by the retained execution, generation
increment, released ownership and exact saved commit. A report is held transiently
by `ManagedHost` and is sent without trimming to a history limit. Receipt reads
request the public host's maximum 20,000 UTF-16 code units for integrated reports.
Complete source composition and notification correlation are explicitly separate
in `delivery.source_body` and `delivery.receipt_scope`; no runtime field certifies
the unseen suffix. The leading identity is limited to 2,000 UTF-16 units and must
fit completely inside the returned prefix. Legacy fixed review-only notices
retain their 4,096-unit exact whole-event comparison. Managed review-only notices
with a wait observation use the public 20,000-unit read bound and still require
exact whole-event comparison; without an integrated leading target identity,
truncation never establishes receipt correlation.

The fixed notification suffix and MCP description expose the Skill's compact
direct-report condition: an actually received matching Task/target generation,
`status=completed`, successful stages, actual Task done and sufficient reporting
facts without unresolved contradiction or action. They preserve disclosure of
all Findings (also low/resolved), warnings, limitations and `not_recorded` work;
these alone do not exclude direct reporting. Labels and correlation never certify
unseen results. Other outcomes link conditionally to Continue After Reviews.
This guidance applies to the visible body, including the incomplete-delivery
notice below, without adding a runtime classifier or changing any existing gate.

The shared public adapter encodes the actual tool-call frame for effect-free
admission against the existing local relay's 262,144-byte bound. This includes
JSON framing, UTF-8 text and genuine executor metadata; it is not a guessed host
send limit. Only exceeding that bound replaces the full report with an explicit
incomplete-delivery failure notice, retaining identity/outcomes, listing every
omitted field, naming `local_relay_frame_limit` and directing the retained
`finalization_command --check` for exceptional recovery. That notice must itself
fit the same frame bound. Invalid text fails before this size-only branch.
Unknown send results remain unknown with no replay or split messages. Bodies,
prefixes and frame checks stay transient; no storage schema or normal call changes.
Only an actually observed matching receipt invokes best-effort numerical attribution
to the original execution; accepted/unknown sending alone never does so.

The normal scheduled-check call uses the same Task/reviewer request. It
revalidates the same basis and actual pairs, joins the previous worker, and
rechecks both timer and send outcomes after that join; a deleted timer never
settles an unknown send. It admits only settled outcomes. The inherited direct cancellation
records and confirms a known-ACTIVE pause. The request journal then saves closing
intent before the public host deletes the known PAUSED timer and confirms its
exact deletion receipt/config absence. Its phase becomes closed only afterward.
A healthy unfinished set then gets one new internal attempt; an ended set returns
`reviews_ended`. Unknown prior effects reject both replacement and competing
cleanup. Optional inspect reads existing state only; explicit stop shares the
same cleanup path and works after OFF. Restart never resumes a worker.

For a changed wait basis, `observe_generation_cleanup` in
`continuation_cleanup_repository` validates the exact latest sequence/request
under the already-held request lease, then the controller/direct/request identity,
including original parent turn and reservation parent. The saved target must be
strictly older, with unchanged project/path binding/generation, parent and Task.
It shares settled timer/send and worker-exit checks with continuation cleanup;
`worker_observation.observe_worker` admits an exact joined local thread or the
matching saved join proof, and rejects conflicting live evidence. The reader
creates no session or effects. Only this positive proof permits `ManagedWait`
to invoke its existing `_cleanup` for the older generation and continue under
the same lease. The new basis is checked again before appending. A known paused
timer is deleted through existing cleanup; already-deleted timers and settled
sends are not repeated. A missing receipt is not a new gate. An unknown result,
unfinished closing or missing exit proof preserves the old request for recovery.
Short request transactions retain the old attempt while closing it and creating
the next; a crash after closed but before append is recoverable from the same
proof, whereas an uncertain create intent remains non-replayable. No schema,
extra caller operation, periodic cleanup or new worker lifecycle is introduced.

The existing handle resolver pages only the admitted parent's structured
dispatch/interact records, within sixteen pages and one host deadline. Matching
active first/final turn, nearest dispatch, ambiguity/duplicate/cycle checks and
strict child-turn capture are unchanged. Diagnostic codes are fixed; no path
handles or host bodies reach durable state.

`PublicMcpHost.read_child` uses a dedicated fresh single-target public
`read_thread` lookup and `parse_read_thread`, without a history cursor.
It retains only validated `ChildTurn` identities and both observed states.
`read_parent` keeps its bounded public `read_thread` route and exact parent-ID
validation. Existing callers keep every freshness/terminal check,
including active chats whose latest saved turn is already completed. History
is also read by handle resolution and notification receipt traversal; known
UUIDs skip resolution entirely.

Handoff preparation returns `review_wait={status,task_id,wait_tool,guide}`,
where the fixed values are `review_wait_wait` and `references/review_wait.md#normal-wait`.
It reads policy only, never starts waiting or changes read-only CLI effects.
Reviewer requests, saved originals and registration remain unchanged. The ready
response omits `wait_ended_command`; the legacy helper's separately documented
file/stdin invocation remains available for retained supervisor contexts.
Installed guidance replaces create/prepare/start/ACK duties with one normal
wait call and existing result processing after resumption. Non-review waiting
and actual parent/reviewer numerical attribution are unaffected.
Already-ended reviews proceed to result processing using existing observations,
without a new wait or status query. If service prerequisites are unavailable,
the retained integrated continuation remains available after review end;
unknown host effects still require the existing recovery and never imply that
registration, Git or completion should be repeated through a different route.
The catalogue describes wait effects/readiness and links to the wait protocol
and workflow's Continue After Reviews owner. It does not duplicate the result
processing procedure. That owner branches on the selected preparation route and
observed stage outcomes; unknown effects remain distinct from stages not run.
Missing originals produce an incomplete report with all-or-nothing registration.
Healthy scheduled checks and conditional recovery retain their existing controls.

The normal project's failure boundary adds the bounded diagnostic owned by
[failure diagnosis and recovery](review-completion-specification.md#review-wait-failure-diagnosis-and-recovery).
`failure_diagnostics` owns the fixed vocabulary, size bound and transient
call-local projection; it reuses the existing typed bootstrap/host classifiers.
`project_server` separates a valid disabled policy from policy-read failure,
retains fixed project-admission reasons, and invokes that projection before its
unknown fallback. It never derives a diagnostic
code from arbitrary exception text. The fixed cleanup reasons produced by
`continuation_cleanup_repository` cross `DiagnosticError`, result capture and
the final failure projection without collapsing to `unavailable`. Both latest-
cleanup proof consumers in `managed_wait` guard the supplemental read and proof
with `reading_retained`, so a read exception cannot reuse earlier observations.
They also invalidate earlier retained-state observations when the proof is not
settled, as generation-change admission does. Separately confirmed cleanup facts
remain available even when the new retained-state observation fails.
This changes call-local diagnosis only; journal writes and host-effect ordering
remain unchanged. Successful readiness and the nine control
inputs remain unchanged.

At a rejected answer-hook caller mismatch only, `project_server`
compares the admitted executor thread with the event's reviewer hint and sets
the call-local `hook_caller_relation`. `failure_diagnostics` emits only its two
fixed values at that exact operation/error/reason boundary, never arbitrary
values or a relation supplied by a result object. This records no UUID or raw
metadata and proves no stored binding. Unrelated callers are rejected before
slot lookup; reviewer candidates require the separate saved-slot admission
above. Rejection performs no host access or collection write. Successful hook
output and parent controls are unchanged. A new call starts without the optional
comparison.

At an answer hook's executor-turn admission failure, `review_wait_host` supplies
one closed context classification on its typed error. `project_server` copies
that classification to the call-local diagnostics before propagating the original
failure. `failure_diagnostics` emits it only at the exact operation-stage
`review_wait_unavailable`/`executor_context_required` boundary. The hook-only
adapter consumes `turn_missing` as absence for the saved-source recovery rule
above, without manufacturing executor metadata. Invalid and embedded-only turns
remain rejected; conflicting valid aliases still produce `caller_mismatch`.
The shared turn parser and fresh parent-dispatch rule retain their strict
requirements. This diagnostic is not persisted and carries no raw metadata or
rejected value.

Managed orchestration carries call-local evidence of local writes and host
mutation dispatch separately from admitted retained effects. A failure before
state inspection cannot certify absence. Preparation/start diagnostics survive
the cleanup path, including fixed nested direct-state activation reasons.
Authoritative rereads, supplemental direct/controller reads and journal writes
invalidate earlier absence/settled observations. Supplemental reads restore the
prior observation only on success; a session that returns a read failure instead
of raising also leaves the observation invalid. The fixed `timer_cleanup_unknown`
result separately confirms an unresolved timer read or saved unknown phase and
retains `present_unresolved`; it never certifies settlement. Failed request-lease
admission or exit invalidates observations made before that failure, including
a distinct release failure while a body exception unwinds. Propagating the same
body exception preserves that body's diagnostics. A possible local
intent without a fresh observation selects
existing-state inspection. Unconfirmed cleanup selects conservative recovery
without overwriting the original failure boundary. The journal and direct-state owners
retain intent-before-effect and all replay/competing-cleanup exclusions. These
diagnostic projections do not settle, rewrite or migrate durable state.

Both answer-collection arms and normal wait's synchronous binding/collection
attach a temporary repository observer to the call's diagnostics.
It marks possible local writes before the writer boundary
and projects retained effects from complete validated inventories only after
successful read or transaction exit. A publication intent precedes original
file creation, so an uncertain publication is already covered by that write
observation. Failed fresh reads and lease exits invalidate current state;
call-local knowledge of an unresolved effect still requires inspection until
that same inventory is successfully observed settled. The admitted wait-record
projection and collection inventories are combined without allowing settlement
of either to erase the other's unresolved effects. Failed reads remain
uninspected. The observer is restored on exit and is not shared with a worker
or saved in operational records.
Normal wait's successful OFF, absent-preparation or legacy selection leaves its
prior observation intact; an explicit parent collection with no service instead
fails with uninspected collection state.

`_ChildSession.close` retains its combined success boolean for the development
relay while exposing transient `termination_confirmed` and a fixed
`close_response_reason`. Reader failure flags and queued extra replies reject
communication independently of process exit, joined readers and closed pipes.
`PublicMcpHost._request` preserves the first exchange failure, checks late
responses, and carries safe operation/reason/termination facts through typed
host errors. Valid tool rejection and malformed content are identified before
cleanup, so cleanup failure cannot obscure them. `_json_call` identifies inner
JSON failure after confirmed cleanup.
Read/timer operations require nonempty content before cleanup; direct sending
still permits an empty successful content list as transport acceptance only.
Unknown mutation wrappers preserve both effect uncertainty and these facts.
Bootstrap and direct-start failure results
forward the closed optional object to `CallDiagnostics`, which retains it
through later timer cleanup. Facts are call-local only; old saved worker reasons
remain readable without fabricating historical exchange/termination observations.

`runtime_identity` owns source-only identity for the review-wait server entry.
It reuses the source-import loader to capture the startup inventory of Python
sources immediately under `scripts`, `task_governance_tool` and
`review_wait_runtime`, including lazy/helper sources. Loaded identity requires
the captured import bytes to agree and the startup entry source to compile to
the code object actually executing that entry. This establishes implementation
equivalence, not recovery of the interpreter's original entry source bytes;
missing or mismatching entry evidence, uncaptured or mixed imports leave
comparison unknown. Inventory is bounded to 512 files, 4 MiB per
file and 16 MiB total; unavailable or over-bound material yields unknown.
Explicit inspect compares those bytes with current physical package sources,
and reads only the literal schema value from deployed `storage.py` syntax.
It publishes one loaded digest and a comparison enum, never an expected/actual
hash pair, source text or filesystem path. It makes no Task DB access, external
call, migration, process launch or worker recovery. The loaded schema ceiling is
retained independently from deployed source inspection. Schema-only disagreement
does not prove changed code, and a lower deployed ceiling cannot authorize a
reload against newer state.

The Skill's failure procedure consumes the bounded diagnostic and reports state,
continuation, necessary action and resume condition together. Readiness permits
delegating waiting before turn end; a failure instead permits ordinary turn end
after reporting limitations, without automatic-resumption or completion claims.
Unavailable MCP inspection is not a new recovery prerequisite. The retained
Continue After Reviews route still owns completed-review processing and preserves
successful stages. These changes add no normal call, polling, automatic restart
or alternate route around unknown effects.

Focused tests cover the single call, fixed destination, failures and unknowns at
effect boundaries, duplicates, restart/OFF/cancel, healthy scheduled rewait,
same-parent event observation without ACK and unchanged legacy paths. Diagnostic
tests cover stage propagation, policy OFF versus read failure, call-local versus
retained effects, cleanup uncertainty, disclosure/size bounds and runtime source
comparison without state writes. Packaged-reference retrieval checks the failure
procedure and retained continuation. Physical
installation and handoff, document/release/Skill checks and two independent
Tier 2 reviews precede the bounded real-host validation. Accepted sending and
offline fixtures never establish actual delivery.

<a id="external-timer-deletion-evidence"></a>

#### Conditional External Timer Deletion Reconciliation

This section implements the design responsibility of the
[conditional recovery boundary](review-completion-specification.md#conditional-external-timer-deletion-reconciliation),
not a production transition. Runtime, operational schemas, tools and installed
Skill instructions remain unchanged. It owns the evidence prerequisite and
handoff to a separately authorized implementation; adoption must synchronize
the current owners and remove this conditional label only after its gates.

##### Available Evidence And Its Limits

The existing `PublicMcpHost.delete_heartbeat` confirms its own single deletion
only after fresh physical configuration admission and equality with the
captured `HeartbeatSnapshot`, a matching structured public deletion receipt,
and exact config absence under a healthy physical ancestor. The receipt binds
`automationId`, `mode=delete`, `deleteStatus=deleted` and its kind/name/rule
snapshot. Parent, prompt identity, creation/update timestamps and expected
revision come from that call's fresh pre-effect admission; the receipt alone
does not contain all those facts. This is evidence for the existing in-call
protocol, not a reusable proof of a different actor's earlier deletion.

The public `automation_update` definition offers current view and mutations;
it does not specify a typed read-only deletion tombstone or an immutable
instance/reuse guarantee. The inspected bundled `codex-app-tools` wrapper
(26.1023.50603) forwards host-owned tools and does not define that missing
semantic contract locally. This does not establish that every host version
lacks such a facility; it establishes that this design cannot rely on one.
`PublicMcpHost._call` intentionally maps tool-level error bodies to the same
`host_tool_error` boundary. Text saying not-found or permission-denied supplies
no typed deletion proof. Timeout, malformed output and lost results remain
separate failed observations, never absence.

A retained public `read_thread` observation of the motivating deletion exposed
the operation, arguments and `status=completed`, but no result/receipt body.
That marker shows a completed tool-call record, not successful deletion of the
saved occurrence. No receipt was reconstructed from it. Raw chat content and
private host data are not published or retained as operational proof. The
offline tests exercise actual adapter admission and state transitions; their
simulated receipts establish behavior under supplied inputs, not availability
of a real external-deletion API.

| Evidence | Permitted conclusion |
|---|---|
| Fresh admitted MCP-owned delete, exact receipt and physical absence | Existing deletion confirmation for that one call. Saved confirmation remains subject to binding and exit/send checks. |
| Config or directory absent; view not-found; empty list | Current access supplied no admitted live config. No external deletion conclusion. |
| Tool error, timeout, malformed/partial output, lost delete result | Observation/effect remains unknown under its current boundary; no replay. |
| Old receipt body or completed call metadata without its original identity admission | Insufficient to reconcile the saved occurrence, even if the config is now absent. |
| Same name/rule with another creation revision, parent, Task, attempt or probe | Conflicting identity; refuse adoption and leave the replacement untouched. |
| Hypothetical host-attested deletion of a non-reused instance, bound to the saved occurrence | Candidate proof only after its public semantics, retention and real-host behavior are established. It is not currently available authority. |

An exact recreation of all observable configuration fields is also not ruled
out by a name, digest or timestamp. The candidate needs a host-supported
non-reused instance identity or an equivalent documented history guarantee;
adding a local checksum cannot manufacture it. Public proof availability,
instance identity and retention across restart are unresolved prerequisites,
not implementation details to guess.

##### Candidate Stop Reconciliation And Crash Boundaries

If those prerequisites become available, keep the existing explicit stop entry
and request/wait writer ownership. First admit the exact latest request and
project/binding/parent/Task identity; join the exact owned worker, or use its
valid saved join evidence. Re-read direct/controller state after join, retaining
the independent send and timer conclusions. A live, missing or conflicting
worker observation, a changed attempt, or a pending/unknown effect refuses
reconciliation before any proposed success write.

For only the admitted known ACTIVE/PAUSED external-removal case, acquire the
public read-only proof once within the existing bounded recovery call. The
adapter would validate its host source, complete identity and documented
freshness semantics; prose or caller-supplied proof is not an input. Compare the
proof with the exact saved request/controller/direct binding and current host
instance state. The local lease cannot serialize host UI activity, so the host
identity guarantee must make a later replacement distinguishable without ever
mutating it. Failure is a limitation, not an automatic retry loop.

After successful observation, persist a bounded typed confirmation atomically
with the timer's reconciled outcome in the existing operational repository.
Its schema would need a versioned source kind, immutable host instance/proof
reference and observation time bound to the original automation, attempt,
probe, controller digest and worker identity. Retain only validated structural
facts, never raw host bodies, prompts, paths, credentials or chat history.
The precise fields and limits depend on the proven public source and require
review before implementation; they are not a new schema in this design.
Old records without such evidence remain readable but cannot acquire it by
read repair. Local structural checksums preserve binding, not authentication
against arbitrary local tampering.

Advance the same request to closed only after its exact timer, send and worker
facts settle. Stop does not start another wait, send a message or run business
finalization. A later authorized ordinary wait freshly checks the current Task,
target, reviewer selection and settled old occurrence under its existing lease.
It cannot reuse old judgments across changed targets. Known original saves,
registration, commit and completion are reported from their existing evidence,
never rerun for recovery. Current normal polling and parent inputs stay unchanged.

| Interruption or race | Required disposition |
|---|---|
| External delete happened but result was lost, or only config absence remains | Keep unknown; no second delete or fabricated confirmation. |
| Process ends before public proof is durably saved | No recovered fact. A later explicit recovery may read a retained authoritative proof; if unavailable, remain unknown. No mutation is replayed. |
| Proof/outcome transaction rolls back or its result is lost | Read the exact operational record on recovery. A complete valid confirmation is reusable; absent/partial/conflicting data is not success. Do not blindly rewrite. |
| Confirmation saved but request closure not saved | Revalidate bound proof and independent exit/send facts under the request lease, then finish only the local closure. Do not repeat a host effect. |
| Closure saved but next occurrence not created | Existing normal admission decides a fresh occurrence. It never reopens the old timer. An uncertain new create intent still blocks replay. |
| Concurrent stop/new wait, changed attempt or bound state | Existing OS writer admission serializes local changes; losers retain refusal. A proof for an older attempt cannot close the newer one. |
| Host replacement during observation | Instance mismatch or unproved identity stays unknown; never pause/delete the replacement. A reused name is not continuity. |
| Late old notification/receipt after closure or a later generation | It refers only to the old occurrence; no new ACK, settlement, judgment reuse, registration, commit or completion follows from it. |
| Saved worker exit conflicts with a live exact worker, or send is unknown | Preserve independent uncertainty, even with a valid deletion proof. No new wait. |

##### Implementation Handoff And Validation Gate

Existing-source inspection and isolated experiments establish the refusal gap
and the compatibility of the current ownership boundaries. They do **not**
establish a supported external proof source or a safe production recovery path.
The preferred integration point is stop reconciliation, conditional on that
source. Neither absence-only recovery nor importing a parent's copied receipt
is selected as a fallback. There is no production implementation to activate
from this design alone.

If a supported source is demonstrated, a separately registered implementation
would change `review_wait_host` for a closed read-only evidence adapter,
`review_wait_direct_repository` for versioned structural confirmation and an
atomic local transition, `DirectProbe`/`ManagedWait._cleanup` for the admitted
stop branch, and `continuation_cleanup_repository` for exact proof consumption.
Keep runtime orchestration free of raw SQL. Sync this specification/design and
only the applicable Skill failure guidance; add no new normal control, parent
timer-ID handoff, background service, core/usage migration or generic timer API.
The persistence version and compatibility tests must be decided with the actual
proof format, not preallocated here.

The isolated test basis is `test_review_wait_host.ExternalDeletionEvidenceTests`
and `ManagedFlowTests.test_external_removal_blocks_stop_and_fresh_generation_after_restart`:
missing config and old receipt, fixed tool/transport failures, same-name revision
or parent changes, stopped worker with active saved timer, restart, new target,
writer contention and late old receipt. Existing `DeleteHeartbeatTests`,
`ContinuationCleanupTests`, managed worker-observation and direct-wake tests
cover the positive owned-delete control, lost result, mismatched Task/attempt/
probe, missing/conflicting worker exit, send unknown, persistence failure and
restart. These are reproducible evidence of the current contract, not a stub
implementation of the conditional proof source.

Before later activation, obtain separate approval for a disposable real-host
fixture, its one external deletion and bounded stop/restart observations.
Record sanitized actual public proof shape/meaning and host version; exercise
wrong instance/same name, permissions/transport loss, result loss and retained
proof after restart without touching a real pending review. Demonstrate no
second delete/pause/send, correct local crash recovery, refusal of unknown send
or missing exit, one freshly admitted later wait, and no added normal-path poll.
Offline regression, document/package checks and two independent Tier 2 reviews
must precede that activation. A successful design review or fixture cannot
replace this host prerequisite or authorize its test mutations.

#### Accepted Review Wait Query Design

This approved design implements the [query-routing contract](review-completion-specification.md#accepted-review-wait-query-routing)
under its activation conditions. It adds no public MCP operation, caller choice,
stored transcript, or normal-path polling step. The existing adapter boundaries
remain responsible for genuine metadata and validated identities.

`PublicMcpHost.read_child` and `read_parent` each use a dedicated fresh public
`read_thread` call for their admitted reviewer or original parent:
`hostId=local`, `turnLimit=1`, `includeOutputs=false`,
`maxOutputCharsPerItem=1`, with no history cursor. Each validates its exact target,
latest turn and chat/turn states and returns only the existing `ChildTurn` facts.
They never probe self-wait first, infer idle from failed observation, change
executor identity, or substitute another chat. Item bodies have no role in this
state decision. The small per-item cap does not bound the number of items or
the size of the complete encoded response.

The host's caller-turn cancellation of `wait_threads` makes that operation
unsuitable for the canonical worker's post-parent-turn reviewer state reads.
`_read_status` uses the existing state parser and fixed transport limits;
managed, direct and bootstrap callers keep their observation points, cadence,
exact-turn and terminal-state guards. The separate cursor-based development
`wait_children` operation remains a compatibility path, outside this worker.
Reviewer-state read failure retains existing host failure/safety handling;
it adds no public diagnostic purpose or automatic retry.

Four internal purpose-specific read paths own their minimal validated outputs:

- Parent state owns only the current-state snapshot above and existing fresh
  pre-effect checks. It neither searches receipt history nor resolves handles.
- Reviewer state owns only the admitted reviewer's current state. It never
  borrows parent state, handle history or a prior terminal observation.
- `resolve_reviewer_ids` owns structured dispatch/interact records, nearest
  dispatch ownership, alias reuse/conflicts and the matching first/final parent
  turn. Known UUIDs bypass mapping. Preserve the existing bounded scan and deadline.
- `ManagedHost.observe_receipt` owns the original notification/Task/target identity,
  exact structured event, pre-send fence and first matching subsequent turn.
  Preserve the existing bounded traversal and distinction between correlation
  and complete-body observation. A candidate before reaching the required fence
  cannot establish receipt.

These paths may use the public history response transiently but do not return
raw history to the caller/LLM or save it in Task state or diagnostics. Extracting
fields after receipt does not bypass the transport's byte admission. Use a larger
finite buffer for read-only responses rather than a streaming JSON extractor.
The adopted `MAX_READ_RESPONSE_BYTES` is 4,194,304 bytes for `read_thread` and
`wait_threads` responses only, including the outer frame's newline. Nested JSON
uses the same byte ceiling, so admitted outer responses face no old 256 KiB inner
cap. `_ChildSession` admits at most 8,388,608 stdout bytes for that whole owned
exchange; stderr stays independently capped at 1,048,576 bytes and is discarded.
The queue still admits at most 16 entries, including EOF markers. Pending data, queued bytes and JSON
allocations are finite under those frame/count/stream bounds; this is a bounded
buffer design, not constant-space streaming or a claim about empirical rarity.
Initialization remains capped at 262,144 bytes even in a read exchange. All
other operation responses, requests and notification-send frames retain their
262,144-byte limit and 1,048,576-byte per-stream drain ceiling. This does not alter
the development-only view relay or public incoming server admission.

The existing per-call host deadline (default twenty seconds), sixteen-page scan
with one shared deadline, receipt's 120-second observation and twenty-minute
outer deadline remain. Nested parsing is checked against the remaining call
deadline before returning a result. Cleanup retains its one-second wait/join
budgets and exact owned-process/reader/pipe checks. The first fixed pipe failure
is retained under a lock independently from reaping and extra-frame checks.

`ThreadPage` carries only a validated `ChildTurn`, next cursor and has-more flag.
The dispatch reader returns that projection and bounded selected path/UUID sets;
the receipt reader returns it and a validated event-match boolean. Each scan
retains only these complete validated projections, up to sixteen pages plus the
dispatch's final fresh recheck, and discards raw items after extraction. No
partial page or receipt candidate before the required fence becomes a conclusion.
Neither current-state reader has a stale-snapshot fallback.

`HostAdapterError` carries an optional closed purpose failure. Bootstrap/direct
failure projection and `CallDiagnostics` preserve it through later timer cleanup.
`DirectProbe` offers a no-op failure-retention hook at the worker's failure
boundary; `ManagedDirectProbe` captures the first closed parent-state or receipt
read failure under its observation lock, bound to its owned probe/worker tuple.
It survives stopping observation; explicit inspection may project it as `read_diagnostic` for that
same owned worker only. No durable format changes or historical cause fabrication
are introduced. Inspection reuses the existing validated observation's exact
attempt/request/controller/direct identity and direct-thread ownership proof,
then matches the failure's captured probe/worker tuple against that owned object.
Unknown observation or changed identity suppresses the optional projection; a
joined, correctly bound stopped worker remains eligible. No second state read or
new worker-observation field is required. The shared semantic read boundary runs
validation only after `_json_call` returns, which requires confirmed helper,
reader and pipe cleanup. Rejected state/page/selected-record semantics retain
those facts with `host_response_invalid`. Transport errors bypass semantic
validation unchanged, so an unresolved cleanup cannot erase the original cause
or be upgraded to confirmed termination. Generic exceptions and unissued reads
do not acquire exchange facts from prior validated pages.
Accepted sending, native commit and Task done remain unchanged;
the failed receipt read does not resend or invoke finalization again.

If a later response exceeds its bound, retain prior fully received and validated
pages or independent facts. Do not parse a truncated response as a complete page.
Do not conclude a handle mapping if unseen records can still change it, or receipt
if its required fence is missing. Prior parent snapshots are not current evidence
for a later failed fresh guard. Report only fixed, safe facts about the failed
purpose, unavailable conclusion and retained successful stages; keep communication
failure separate from helper-process/reader termination. Successful cleanup does
not repair a failed read. No identical oversized-read loop, split resend, or replay
of an unknown mutation follows from the failure.

Validation covers the real-host self-wait rejection and existing parent read,
reviewer snapshots, active-chat/old-completed-turn conflicts, identity mismatch,
known-UUID bypass, alias conflicts and missing receipt fences. The capacity change
also covers all four dedicated purposes at the old and adopted bounds, outer and
inner JSON, UTF-8/escaping, cut records, preserved complete earlier results and
independent communication/cleanup failures. Retain tests for confirmed commit/Task
completion with unknown receipt, no resend and no added normal-path LLM operation.

#### Worker Observation And Exit Evidence

Optional [common diagnostics](logger-design.md#connection-inventory) use one
MCP request or bounded worker scope, never the resident service lifetime.
The configured project passes a fresh scope factory to direct/legacy workers.
Each reuses its existing basis admission; termination diagnostics reuse the exact
admitted owned worker/probe, separately reporting the saved worker/binding check.
Inspection and explicit checks stay no-write.
Operational connections retain their native isolation and timeout settings.
The common [wait checkpoint contract](logger-design.md#wait-checkpoints) owns
typed events, important-event budgets and intermediate persistence outside all
tracked connections. Managed and legacy direct workers use the same projection;
service EOF/cancellation cleanup is a separate event, never worker-exit proof.
The existing deadlines, effect guards, sends, timers and public responses remain
unchanged, including when diagnostic output fails or is disabled.

`worker_observation` owns the shared version-one projection. Managed inspection
reads the latest request sequence and exact controller/direct siblings, checks
Task/parent, basis digest, automation, original parent turn and reviewer pairs,
and consults only an already loaded matching session for direct observation.
Missing/unreadable/mismatched evidence stays unknown. It creates no session,
lease file, worker, receipt, timer or read repair. Stop appends observation after
the existing operation/cleanup boundary, preserving success if that read fails.

`DirectRecord` extends canonical bounded JSON with optional `worker_id`,
`worker_stopped_at`, `worker_stop_basis` and `worker_stop_identity`. The last is
a structural checksum of probe/worker/binding, not authentication. Old records omit these fields with
unchanged encoding; physical schema one and payload versions one/two remain.
New version-two probes allocate a worker UUID before start, not an exit claim.
The repository validates canonical UUID/time, stopped time no earlier than
creation and sole `thread_join` basis. `confirm_worker_stopped` compares probe,
worker and binding under the existing short OS lease/transaction, retaining the
first confirmation. Other updates preserve evidence. No core/usage schema,
backup, Setup, path or permission change is introduced.

After successful start, one daemon join observer belongs to that exact worker
and ends after join or original duration plus existing join allowance. It has no
host/Task/Git effects, monitoring/recovery loop, worker restart or resident
supervisor role. It saves only after `Thread.ident` establishes actual start,
join returns and `is_alive` is false. Explicit stop confirms after its bounded
join through the same repository operation. Target/finally never saves exit.
Save/observer failure is separate from direct exit and triggers no retry;
process loss before confirmation leaves no proof. Inspect can join an already
ended thread with zero timeout but never saves. Legacy version-one experiments
retain their original payload and do not start a termination observer.

The worker retains only transient last bound reviewer observations under a local
lock. Before send intent ManagedDirectProbe builds the same read-only snapshot.
ManagedHost checks Task/parent/probe and deep-freezes it before one-shot transport.
Both raised and returned-unavailable observation paths preserve the worker's
already-bound probe/automation identity without inferring unread state or retrying
a failed read. Fresh reviewer, parent, basis, cancellation and original deadline
guards follow observation work before durable send intent.
Integrated reports retain every result plus `wait_observation`; review-only
notices add one labelled JSON line. Exceptional local-frame fallback retains
observation and lists omitted original fields. Only notification suffixes
explain remaining receipt observation without asserting own acceptance/receipt
or future exit. Runtime reads remain source-only; a notification worker cannot
prove service availability. Receipt correlation compares the exact frozen body.
No extra normal call, ACK, exit notification or completion gate is added.

Focused fixtures cover running/direct exit/saved exit after restart, unstarted
and legacy records, binding/ownership conflicts, failed saving, process loss,
concurrent confirmation, read-only inspection, stop outcome separation, fixed
notifications/unknown sends and existing complete delivery results.

#### Development Review Wait Relay

`tools/review_wait_mcp_relay.py` owns the source-only
[connectivity relay](review-completion-specification.md#development-review-wait-relay).
It uses Python's standard library and speaks bounded newline-delimited UTF-8
MCP JSON-RPC on stdin/stdout. The outer server exposes only the fixed view tool;
it accepts no arbitrary downstream command, tool, mode, reservation or identity
through tool arguments. Initialization and discovery do not access the host.
Discovery, ping and the initialized notification accept standard object `_meta`
without echoing or retaining it; discovery accepts an absent or null cursor
and rejects continuation tokens because this catalogue has only one page.
Invalid control parameters return a parameter error. Discovery metadata never
supplies missing identity to a later tool call, whose fixed restrictions remain
independent.

For each admitted call it starts the unmodified installed `server.mjs` with
the absolute Node executable from the inherited `CODEX_MCP_NODE_PATH`, using
an argument array and no shell. The server path and reservation ID are fixed
startup inputs. It inherits the host-provided environment without injecting
caller metadata. The downstream handshake advertises no elicitation or
sampling capability; an unsupported server request fails closed. The original
request `_meta` is forwarded as an object without rewriting or synthesizing
any field. Only `automation_update` with the fixed `mode=view` arguments is
issued. No host-private pipe protocol or database is implemented by the relay;
the existing bundled MCP entrypoint owns its transport to the host.

One call owns one child process, its bounded I/O workers and cleanup. A deadline
limits initialization and response receipt. Cleanup closes stdin, waits, and
terminates/kills and reaps only the owned direct child when needed. A cleanup
failure prevents success. The direct Node launch avoids a shell intermediary
whose termination could orphan Node. The relay persists no state and launches
no independent daemon; the outer MCP server lives only with its stdio session.

`tests/test_review_wait_mcp_relay.py` exercises the protocol against isolated
fake peers, including exact metadata and fixed-operation forwarding, malformed
and error responses, disclosure resistance, resource bounds and process cleanup.
These tests never invoke the real bundled entrypoint or mutate a reservation.
The companion configuration and Hook examples in `tools/` are inert templates;
preparing them changes no `.codex` configuration or host trust. Configure the
relay as a separate project MCP server and pass the named inherited variables
through `env_vars`, never copied environment values. After user-reviewed
activation, a real executor call and a real SubagentStop result are separate
integration checks. Hooks require an already-connected outer MCP server; this
relay does not change that host lifecycle contract.

<a id="review-wait-basis"></a>

#### Read-Only Review Wait Basis

`review_wait_basis.py` implements the public helper's scope/resolver admission
and output composition. `review_wait_basis_repository.py` owns its coherent
query-only Task, owner, project binding and immutable Evidence reads. The retained
resolver connection is closed after the snapshot; no numerical module, state
initialization, maintenance or host process participates. The source-only
compatibility entry `tools/review_wait_basis.py` invokes that fixed public helper and validates its
bounded closed output into the original wait's current `WaitBinding`.

This separates structural freshness from executor identity. The current stored
parent is compared with genuine request metadata by the service; it is never
inserted into missing metadata. The basis includes ownership generation so a
same-parent reacquisition cannot reactivate an older wait's authority.

<a id="development-review-wait-controller"></a>

#### Development Event-Driven Review Wait

The [source-only replacement boundary](review-completion-specification.md#development-review-wait-controller)
separates three responsibilities. `tools/review_wait_controller.py` owns pure
decisions and its closed snapshot codec; `tools/review_wait_repository.py` owns
the explicitly initialized development store, revision check and writer lease;
`tools/review_wait_host.py` owns bounded public-tool transport and structural
readback. `tools/review_wait_runtime.py` composes explicit parent controls and
one bounded observation with those components. It admits the original current
Task basis and exact parent/timer/reviewer context, persists every intent before
an external write, and never replays a pending intent loaded from storage.
`tools/review_wait_observer.py` owns the non-daemon worker and bounded public
long waits; `tools/review_wait_service.py` owns each parent request's admission,
appointment calculation and worker handover; `tools/review_wait_server.py` owns
the closed stdio protocol and explicit development bootstrap. These source
paths are compatibility aliases to the shared packaged runtime. The old source
catalogue and injected scratch-state entry remain separate from the canonical
project entry; Setup and numerical collection never launch them.

The controller consumes supplied aware instants rather than reading a clock.
An immutable intent identifies one operation on one reservation and arm. Save
the intent before calling the host; settle only the matching operation using
an exact observed result and established completion of the earlier operation.
Readback alone after a timeout does not establish that completion. Missing or changed readback cannot clear uncertainty
or authorize another ACTIVE update. Cancellation remains latched while a
shortening is unresolved; reconcile that operation before issuing PAUSED.
Fresh healthy rearming requires the checked arm and no later terminal latch.
Snapshot restoration validates the closed schema and state relationships.
Appointment validation uses standard-library timezone rules, explicit UTC
support and fold roundtrips to reject ambiguous or unavailable named zones.
The chosen occurrence and daily RRULE must agree at whole-second precision;
the later 90-second comparison retains the actual decision time's precision.
Composition rechecks the original Task basis before saving and dispatching an
ACTIVE intent. Stop-only cleanup can still address the original matching wait
after a Task change. A confirmed activation that misses its occurrence is
closed and paused for both initial arm and rearm.

The repository's physical path is injected for explicit development tests and
is supplied by the shared resolver for the canonical project entry.
It owns schema version/history, bounded serialization and short SQLite
transactions. Its exclusive writer lease spans claim, external operation and
settlement; no external host call runs inside the SQLite write transaction.
Failed or interrupted callers leave their saved pending intent available for
readback, not an automatic retry. An absent/corrupt store is never silently
recreated. Production path admission, Setup opt-in and state retention are
integration responsibilities, not permissions granted by this constructor.

The host adapter reuses the relay's bounded direct-child MCP transport with
the original executor identity. It admits public `read_thread`/`wait_threads`
structural observations and fixed `automation_update` operations. The explicitly
selected direct experiment below additionally owns one fixed same-parent send. Thread
loading state is not turn completion; returned child and turn IDs must match
the admitted dispatched pair. Runtime admits completed, failed or interrupted
turns only with `idle`, `notLoaded` or `systemError`, matching the direct
experiment's reviewer-end rule; `active` and nonterminal turns remain excluded.
Bodies, summaries and tool transcripts are
discarded. SubagentStop is a candidate signal, never terminal proof.

The source entry point can initialize that one scratch store only through its
explicit prepare operation after fresh original-Task basis and public PAUSED
reservation observation. Its actual reviewer IDs are expanded to current turn
IDs by the public host reader. Reopening a prepared store never starts an
observer, initializes a replacement or replays a pending operation. Configuration
examples remain inert; they are not Setup or host trust mutations.

The entry point assigns a closed bootstrap stage before each boundary and
maps only known internal exception classes and explicitly allowlisted codes
to a fixed diagnostic. It never serializes exception text or a rejected value.
Host admission checks object and JSON-text embedded turn metadata without
filling missing primary executor identity. At the one downstream dispatch
boundary, a transient copy removes the wrapper's six recognized call-ID aliases
and nested `call.id`, preserving all other fields and the original in-memory
metadata. The unmodified public wrapper then generates its existing random
correlation ID for that distinct host operation. This prevents the host's
thread/turn/call-ID duplicate guard from conflating different operations within
one outer request. The adapter adds no error-triggered retry, mutation replay,
sender substitution or persisted metadata. The one-operation connectivity relay
retains its unchanged forwarding contract.
The host adapter retains a separate allowlisted boundary reason while preserving
its general failure code. Only an exact internal host error with a known reason
can refine bootstrap diagnostics. Cleanup uncertainty prevents success while
preserving an earlier fixed communication failure; no error message, provider body or inferred permission decision
is exposed or used to authorize a retry.
The shared relay response reader assigns closed reasons before rejecting a
deadline, EOF, I/O/limit fault, malformed response, unsupported inbound method,
or matching JSON-RPC error envelope. It classifies only standard numeric RPC
codes; arbitrary codes collapse to a generic reason without retaining the
message or data. The host refines response-stage failures only for the exact
internal relay error and an allowlisted reason. Initialization keeps its boundary;
termination is reported independently, and the standalone relay's fixed Hook failure is unchanged.

The automation view response is presentation-only. The documented exact
automation TOML can verify identity, rule, status and parent binding, but it
contains no next-run time or timezone. The adapter therefore requires an
explicit confirmed timezone and never derives a deadline from `updated_at`.
Immutable schedule fields are preserved on bounded same-reservation updates;
creation and arbitrary downstream calls are not exposed. `from_environment`
admits the configured zone against a fresh bounded query to the inherited Node
runtime's Intl API on each view. A changed zone fails closed; a change after
dispatch preserves unknown operation state. Actual host timezone and
parent-process-lifetime equivalence require separate real-host verification.
The explicit `for_pause` read bypasses timezone admission only for cleanup and
marks its structural snapshot `cleanup_only`. The retained zone is comparison
context, not a current timing claim. The adapter refuses arm/shorten with such
a snapshot; pause still compares immutable configuration and confirms PAUSED.
Runtime uses this read for check, cancellation and expired-arm cleanup, and for
reconciling an already closed wait or pending pause without claiming readiness.
An OS lock coordinates this controller's writers, not an unrelated host UI
edit; conflicting readback is a recovery condition.

The observer waits outside the repository writer lease, in batches of at most
eight actual children with a shared bounded round budget and cursors. The host
parser accepts optional `cursorReset` only as a boolean and still requires the
bounded nonempty cursor. The observer replaces each child's saved cursor with
the returned value for its next wait, including after a reset; no reset flag or
cursor is persisted as terminal evidence. Other unknown poll fields remain
invalid. The public optional `wake.turnId` must be canonical and match the
corresponding requested chat's validated latest turn; it cannot substitute for
the complete poll set. Returned
terminal candidates trigger runtime's exact current-turn reads. Unconfirmed
terminal candidates remain eligible for rechecking under the same bounded
pacing until the exact observation agrees. Cancellation is checked between
those reads and before an ACTIVE dispatch; an already
dispatched operation still settles or remains pending. Session EOF stops only
the worker. Timed-out joins return explicit unknown cleanup and cannot authorize
a replacement observer. Parent controls share this lifetime owner; an ignored
stale control restarts observation if its admitted current arm remains ready.
The response separates timer readiness from `wait_ready`, which also requires
a running healthy observer for unfinished reviews, and reports bounded observer
state/reason. Lost observation cannot return a qualified ready acknowledgement.

Focused tests cover the 90-second boundary, terminal/turn admission, pre-arm
completion, duplicate/stale events, stop/rearm races, unknown results, restart
and competing writers, bounded parsing and disclosure resistance. These tests
do not establish idle-parent runtime survival, scheduler delivery, activation
or Task completion. The installed workflow and legacy usage readers are
switched only with the complete verified integration.

#### Development Same-Parent Direct Wake

The [experimental direct wake](review-completion-specification.md#development-direct-review-wake)
uses `tools/review_wait_direct.py` for control admission, a bounded session worker
and separate delivery acknowledgement. `tools/review_wait_direct_repository.py`
owns the single exclusive, versioned SQLite record at the fixed sibling
`<development-store filename>.direct.sqlite`; it does not access the core Task
database. Original basis comes through the existing public reader. Source
aliases and the canonical project entry share this packaged implementation;
only the canonical route admits the explicit Setup policy.

Start and send serialize with the prepared wait's writer lease. The timer runtime
checks for the experiment marker inside that same lease before an ACTIVE intent;
the server also rejects arm/rearm when a marker exists. A malformed marker or
broken link fails closed. Each record is one-shot and cannot be overwritten to
retry. Short transactions retain intent before the host effect; host calls run
outside SQLite transactions. Metadata stays only in the current worker's memory.
Serialized reads and writes of the experiment record share its own lease with
bounded lock-acquisition waiting, so status inspection cannot obstruct a sender's
commit. Ordinary direct-record connections, including initialization, serialized
reads, intent/settlement, receipt, cancellation and exit-evidence writes, use a
one-second native SQLite busy timeout. This permits a single transaction's
commit to wait for a short concurrent observation without replaying SQL or an
effect; prolonged contention closes and rolls back the transaction with the
existing error. The separate `read_observation` path uses no OS lease and opens a
query-only snapshot with zero SQLite busy timeout. Only native `SQLITE_BUSY`
is classified as deferred observation; the connection closes before that outcome
reaches orchestration. Corruption, I/O faults and `SQLITE_LOCKED` retain failure
semantics. Deferral retries no transaction or host effect and never settles a
send, receipt or cancellation. Physical identity, schema and record validation
still apply to the operational snapshot. Inspection-only instances never cancel
another session's worker when they close; explicit admitted cancellation remains
separate from the owning worker's EOF cleanup.

The host adapter reads only its own parent for the original-turn check and sends
only a fixed message to that parent. The public generic MCP result has no assumed
delivery receipt schema: validate transport and cleanup for acceptance, discard
provider content, and require the new parent turn's explicit acknowledgement for
receipt. That acknowledgement is monotonic and separate from send settlement.
EOF stops the owned worker; restart is inspection-only. Tests use isolated fake
hosts and scratch stores for terminal, freshness, cancellation, lost-response,
duplicate-start, restart and acknowledgement races; they never send real messages.

The deletion variant uses the same worker and fixed sibling repository with a
closed version-two payload: the version-one fields plus `version:2`,
`timer_phase`, `timer_rule`, `timer_updated_at` and `timer_due_at`. The immutable
chosen rule is the validated ten-minute appointment. Its due instant is ten
minutes after creation; the worker deadline is twenty minutes after creation.
Version one retains its original ten-minute deadline. Version two's worker
continues observation when a due-time check declines a new deletion sequence,
instead of returning through cleanup. A deletion intent saved before due may
settle and send once within the outer deadline after fresh post-delete checks.
The update timestamp binds subsequent effects to the confirmed host revision.
The physical SQLite schema remains version one; old payloads retain their exact
canonical bytes and are not migrated. Version two is explicitly tagged and
cannot be decoded as a version-one record. No host prompt or metadata is retained.

Timer phases advance from arming to active or unknown, active to deleting or
pausing, deleting to deleted or unknown, and pausing to paused or unknown.
Retain a pending phase across process loss; never infer its outcome or replay it.
Send status advances independently and cannot become dispatching until deleted.
An unknown timer outcome blocks sending and competing cleanup. All timer effects
hold the prepared wait lease, use short separate intent/settlement transactions,
and preserve concurrent cancellation. Existing generic timer mutations are
rejected for a version-two marker, including malformed markers. Only admitted
direct cancellation may attempt known-ACTIVE cleanup after restart.

The host adapter validates fresh reservation identity and uses only public
`automation_update` delete. It requires the structured receipt with matching
automation ID, mode, deleted status and current kind/name/rule snapshot, then
checks the exact documented config path for absence with physical ancestor
admission. A missing ancestor outside the reservation or inaccessible metadata
is unknown. No raw scheduler database or private endpoint participates. Tests
cover deletion-before-send, malformed/missing receipts, remaining configuration,
post-delete cancellation/basis changes, unknown activation/cleanup, EOF and
restart without replay, and old payload compatibility.

#### Review Wait Usage Marker

Normal `review_handoff_preparation.py` output does not generate a
`wait_ended_command` or append one to independent-review requests. Retained
legacy supervisors use the separately documented `review_handoff.py`
conditional `wait-ended` operation with mutually exclusive file/UTF-8 stdin
Packet input. Existing physical input and complete-Packet validators are reused;
stdin adds no file write and preserves the direct transport route.

`review_wait.py` composes actual caller capture, the existing structural project
scope/resolver admission, one query-only core snapshot and the repository-owned
`capture_wait_metadata` check. It emits only the closed versioned metadata marker
after validation. A best-effort lifecycle registry call remembers the actual
sender without opening logs, changing core state, or becoming a gate. It does
not inspect the scheduler, create a review Receipt, capture a caller-supplied
turn ID, or infer the actual all-ended event. SQL and immutable anchor replay
belong to [numerical wait attribution](database-design.md#review-wait-decision-structure).

The host envelope supplies the sender's actual turn to the collector; text
messages and echoed output in another thread are not a substitute. No all-ended
verdict, timer action or notification body is retained. Installed helper tests
cover exact/stale basis, both transport forms, identity, unavailable numerical
state and unchanged core bytes; numerical tests cover duplicate/late/reopen and
restore behavior. These prove local correlation, not host notification timing.

<a id="review-packet"></a>

<a id="conditional-core-reviewer-binding-structure"></a>

### Core Reviewer Binding Structure

The [schema-25 contract](review-completion-specification.md#conditional-reviewer-session-binding)
is active after explicit setup migration. `review_session_transport.py`
owns pure exact-byte/digest and closed-envelope validation, with no filesystem,
environment or database access. Handoff save obtains the existing typed caller
identity once; submit retains it instead of capturing the submitting parent's
identity. Packet creation includes the execution in its existing stability
comparison. No new LLM-entered fields or template judgments are added.

`review_session_repository.py` owns the immutable core relation and its exact
Receipt/Reference/execution joins. `reviews.py` and `review_results.py` use a
typed bound-reviewer argument only for Receipt/Finding registration; all other
calls retain the ordinary owner guard. Preflight observations are rechecked
under the existing short writer. Alias detection and insertion serialize in
that writer; global and selected Receipt validation consume the same core
relation, never numerical state. Source execution belongs to the Receipt's
project/Task; historical bindings remain valid after ownership/target advance.

Migration and its consumer integration are owned by the
[database design](database-design.md#conditional-reviewer-binding-migration).
The focused repository, transport, migration and installed-entrypoint tests
cover rollback, concurrent aliases, parent forwarding, direct reviewers,
stale/malformed bindings, old unbound input and numerical-store isolation.

`usage_review_attribution.py` projects the existing successful read display,
bound-save `review_session` metadata and direct registration acknowledgement
into numerical boundaries. It neither changes core permissions nor reads the
review judgment. The numerical repository revalidates committed core bindings
and the exact Task/Contract/target/execution before using those boundaries.
An unresolved boundary remains partial numerical coverage; it is never a
reason to resubmit a Receipt or downgrade its core binding.

After successful bound original save, `usage_lifecycle.register_reviewer`
best-effort records only the actual caller in the existing numerical registry.
It admits the same project through the resolver, never migrates or reads logs,
and suppresses numerical failure without changing the save acknowledgement.
Read/material remain read-only. Later core bindings can recover registration.

## Review Packet

`review_packet_binding.py` owns only a pure, transient SHA-256 binding of
validated public Task values plus immutable Contract revision. It uses sorted,
compact ASCII JSON with the `taskgov-review-packet-binding-v1` NUL-terminated
domain prefix. `reviews.py` computes it from the existing locked post-write
row; the Runner service carries it unchanged to the CLI. No new DB read,
schema, persisted token, or admission rule is added to capture it.

After target/Runner processing, the CLI invokes the existing Packet handler
only for `not_required`/`runner_pass`, with a fresh connection and that binding.
`review_packet.py` compares it against the full validated Task and Contract
revision in both existing reads, including before initial Git observation.
The optional `--expected-binding` retry uses precisely the same checks.
`task_show_projection.py` derives the same value only in the explicit audit
projection for lost-response investigation. It is not gate evidence.
Post-save preparation failures remain nested partial success, with the
original mutation outcome and one outer maintenance pass. Normal context,
manual Receipt binding, Packet limits, and final gates remain unchanged.

`review prepare` reads Task, Contract, target, and review counts in one
transaction, closes it for bounded Git observation, then reopens a short read
transaction to compare project/task identity, Contract revision, and the whole
target tuple. A change returns `review_packet_stale`.

For a snapshot it recaptures the exact base/index context. For a Git commit it
lists first-parent changes, using the empty tree for a root. Fingerprint and
external targets perform no Git read and state that exact caller-provided
material must be bound to the target before PASS. This target-path observation
allows at most 10 shell-free Git processes, 100 bytewise-sorted relative paths, 240 UTF-8 bytes per path, and
16,384 aggregate path bytes are allowed. Safe overflow is marked truncated;
an unsafe path or a packet above 32,768 bytes fails with no partial packet.

Task, Contract, target, changed paths, five fixed review-focus rows, required
output, result template/instructions, and the batch-registration argv shape are
allow-listed. `review_results.py` owns pure rendering helpers beside its existing
closed decoder: the template copies only validated Packet identity and leaves
all Receipt/provenance claims and collections null. Provenance field names,
enums and identifier grammars come from the existing definitions; explanatory
combination rules do not implement another validator. `review_packet.py` adds
these projections only after its existing revalidation and renders the same
template as compact ASCII JSON in text output. The unchanged CLI Packet-size
boundary includes both fields. Template/instruction rendering adds no DB/Git
read or write. `required_output` is the short route to the template and its
instructions; `review_result_instructions` owns exact binding, actual and unknown
provenance, every Finding, reporting detail and privacy. Both full and independent
explanations retain their applicable requirements without a new format lookup.
Earlier complete saved Packets and their explanatory rows remain accepted by the
unchanged validator and preserved by the independent projection. The builder
does not launch a reviewer, execute/import a receipt, store a packet, or
include a diff, transcript, prompt, stdout/stderr, secret, or absolute path.

The Contract projection includes `authority_ref` from the already-read and
validated current Contract, unchanged and without resolving it. Text emits
`Authority reference` after constraints; independent JSON retains the same
field. The shared pure handoff validator accepts only the existing Contract
shape or that shape plus a privacy-checked, bounded string `authority_ref`.
For saved old Packets, reviewer read compares the existing fields against the
current projection without its new reference field; it never inserts a value
into the saved Packet or display. A supplied reference is compared exactly.
Contract revision and full target revalidation, result templates, original
bytes, output limits and registration checks remain unchanged.

Its separate pure independent display preserves all Packet material/template
and required-output fields, omits the parent receipt command and selects only
the applicable result explanation. It never mutates the original Packet or
fills claims. Helper tests exercise complete-before-display validation, original
byte and Finding conservation, stale registration rejection and read/save
failure paths; retrieval tests check the reviewer route without importing the
parent operation subtree. Generated request prose supplies the role, fixed
commands and first-read failure boundary; the paired read output supplies the
common review/save procedure and exact material retrieval with its exceptions.
Their shared explanations can be consolidated while the transient
material display selects normal or conditional detail. Saved Packet/result
shapes, immutable selectors and review/save/registration checks stay unchanged.
Local bytes/call observations are not token savings.

`review_verification.py` owns the bounded reviewer summary and its pure optional
saved-Packet validator. It reuses `read_verification_evidence` and its exact-current
Receipt collector, excluding audit rows and review judgments. Done-only history
uses the existing validated completion-cycle reader. The Packet's initial read
retains the internal Task solely to select a live marker-2 Runner basis outside
SQLite using the existing no-launch selector. Its final read revalidates that
selection and obtains the summary on the same connection as Task/Contract/target;
the existing complete preparation binding is also compared between reads, and
drift rejection precedes output. The conditional Runner selector retains its own
bounded physical checks in addition to the Packet's target-path observation;
it neither shares nor expands that observer's ten-process budget.
With an exact Receipt ID, the final read passes one connection- and Task-basis-
bound validated Receipt snapshot to both ID checking and summary projection.
The collection and row/Reference validation run once there. A different
connection or Task basis cannot reuse the transient value; the initial read,
standalone consumers and later write gates collect afresh.
The summary includes the read time,
source kind and existing normal verification fields, under the unchanged Packet
byte cap. Public text appends compact summary JSON. No verification execution,
gate, schema, evidence persistence, extra public call or reviewer method is added.
The handoff validator accepts legacy omission and otherwise validates the closed
summary, timestamps, subject/Receipt identifiers, field types and relations.
Save and registration retain their own unchanged live-evidence boundaries.

<a id="completion-cycle-history"></a>

## Completion Cycle History

### Schema V15 Through V20 Activation

Schema v15 adds immutable `task_completion_cycles`, Task coverage
`legacy_unknown|complete`, nullable internal `task_events.completion_cycle_id`,
parent/foreign-key support indexes, and triggers that prevent:

- every cycle update or deletion;
- changing Task coverage after insert; and
- changing an event's cycle link from null or one saved ID.

Cycle IDs have prefix `tg_completion_cycle_` plus 16 lowercase hex characters.
Ordinals are unique per project/Task, start at 1, and reject signed-64-bit
overflow. The row stores:

- origin `native_done|legacy_current_done` and completeness
  `complete|partial`;
- completion and record times;
- Contract revision, review tier, and verification expectation/attestation;
- the complete six-field completion evidence projection;
- the complete four-field review target;
- gate basis version and deterministic counts/basis kind; and
- up to two exact qualifying receipt IDs protected by composite foreign keys
  to the same Task and target tuple.

Schema v17 additionally stores internal verification basis version,
expectation digest, and nullable Verification Receipt link. Every pre-existing
cycle migrates as verification-basis version 0 with null digest/link. Every
new native cycle uses version 1 and stores the domain-separated digest of its
exact verification expectation. Through schema v20, and for the schema-v21/v22
`caller_attestation` arm, a trimmed-nonempty expectation links the qualifying
pass/full Receipt. Schema-v21/v22 `not_required` and `runner_observation` cycles
instead retain a null Receipt link and satisfy the current closed tagged union;
migration does not rewrite legacy whitespace-only values.
The sole partial `legacy_current_done` reopen bridge remains the only
post-v17 version-0/null/null insert.

Post-v17 native rows must be complete, have completion time, attestation true,
non-empty target, gate-basis version 1, zero blockers, and a valid Tier basis.
Legacy rows are partial, have null attestation, gate-basis version 0, unknown
basis, null counts/receipt slots, and retain an honest legacy completion
projection. Structural `CHECK`s enforce the matrices; repository validation
also enforces canonical hashes/fingerprints/text/timestamps and exact receipt
kind/verdict/approval/order.

Schema v19 independently adds cycle evidence-basis version and nullable Bundle ID. Existing cycles become version 0/null; every schema-v19 native cycle is version 1 with one immutable same-transaction Bundle, while the sole partial legacy bridge remains version 0/null. Schema v20 adds the nullable cycle verification-basis kind and Runner-observation pointer; every new native cycle remains evidence-basis version 1, derives the closed verification-basis kind, and has a null Runner pointer. Public completion history adds no Bundle or Runner field.

Migration 15:

1. fingerprints all prior business tables and counts;
2. creates columns, table, indexes, and immutability triggers in one immediate
   foreign-key-enabled transaction;
3. reads current done Tasks in binary Task-ID order;
4. inserts one ordinal-1 `legacy_current_done` partial row per current done
   Task using one migration timestamp and no inferred gate claim;
5. leaves every old event link null and every Task coverage
   `legacy_unknown`; and
6. proves preservation, quick check, and foreign keys before recording
   `completion_cycle_history`.

Reentry validates current structure/matrices without rerunning migration-time
cardinality assumptions.

Marker migration 16 requires complete v15 and all Tasks still
`legacy_unknown`. For each current done Task it compares the newest cycle with
completion time, Contract/review/verification values, all evidence and target
fields, and whether a reopen already linked it. It inserts the next partial
legacy cycle only when absent, different, or already linked; an exact unlinked
match is retained. It then records
`completion_cycle_capture_activation`. The marker adds no schema object.
Reentry validates and never reconciles twice. Schema-v16 Task creation
explicitly writes coverage `complete`; the column default remains
`legacy_unknown` for schema-v15 safety.

### Native Done And Reopen Transactions

Documentation status: **削除予定（計測後判断・実装維持）** for
`task edit --status done`. It remains accepted, but Skill usage guidance routes
completion to `task complete` with typed evidence. It is not an identical alias:
the edit path retains its permitted combined-edit behavior and response/event
contract, while the thin command accepts no non-completion edit. Shared validators
and native capture remain intact. Runtime messages that still direct callers to
`task edit --status done` (including verification/review confirmation and missing
evidence errors in `tasks.py`) remain unchanged and are a pending cleanup item.
Measurement and implementation deletion are later separate decisions; this
documentation marker authorizes neither and adds no completion prerequisite.

Both `task complete` and compatibility `task edit --status done` perform Git
preflight outside SQLite and converge on one locked native capture:

Within `tasks.py`, `_seal_native_completion_locked` accepts the caller-owned
connection, project and Task identity, proposed-done Task, completion time,
and already-selected Runner basis. It seals the current gates, References,
Bundle and cycle, returning the cycle ID without opening or committing a
transaction. `edit_task` retains the outer savepoint, ordering checks, Task
update, lane recheck, event and rollback; `completion_workflow` retains
preflight orchestration outside the writer.

`task_ownership.py` supplies the captured ownership basis and same-writer
generation check. The completion basis carries it separately from quality
evidence. Both completion paths require the owner/completion owner; `--check`
uses the same rule without acquiring a slot. Runner target admission checks
ownership before T1, but committed execution cleanup and terminal audit never
depend on later caller ownership. Packet preparation bindings omit the
caller-relative ownership projection; ownership freshness is checked separately.

1. validate schema v34, identity/binding, optimistic ownership/Task/authority/target
   capture basis, Contract, sequential ordering, and evidence;
2. reread Verification Receipts and review receipts/findings, evaluate the
   current verification and review gates, and select their deterministic
   bases;
3. choose canonical completion time and next ordinal;
4. insert links, Finding snapshots, one immutable current Bundle row carrying the
   selected caller-attestation, not-required, or Runner-observation basis under
   the [versioned Evidence contract](evidence-specification.md#collected-answer-evidence), and
   one complete verification/subject/evidence-basis-v1 `native_done` cycle;
5. link the current execution to that cycle, release ownership and update the
   current Task to done with identical evidence;
6. rerun lane invariants;
7. insert the existing completion event with the internal cycle link; and
8. advance Evidence and Viewer source generations and commit.

Any failure rolls all business rows back. Concurrent completions serialize; a
loser creates no second cycle. Read-only completion check inserts nothing.

Reopen locks the done Task, loads the highest cycle and linked reopen state,
and compares that cycle with the entire current completion projection. When
coverage is `legacy_unknown` and no cycle exists, it may insert the exact
ordinal-1 partial compatibility bridge. Complete coverage without a cycle,
any existing-cycle mismatch, or an already linked reopen returns
`completion_history_inconsistent`. It then clears current gates, preserves
coverage and cycles, inserts `task_reopened` linked to the validated cycle, and
advances Evidence generation only when it inserts the legacy bridge. It never revalidates historical Git material, uses historical
receipts as current eligibility, changes a cycle, or creates a bridge when a
cycle already exists. It acquires a new execution and checks the session's active
slot in the same writer; failure rolls back the reopen as a whole. A later done
uses fresh gates and the next ordinal.

For the sole missing-cycle bridge, `reopen_done_task` passes its internal
`operation_local` admission choice through `insert_completion_cycle_locked`
to `_persist_completion_cycle_locked`. The local arm validates the persisted
cycle's selected ledger relationships; the partial v0/null Bundle basis does
not become a native gate claim. Cycle matrices, exact projection/row identity,
next ordinal, generation update, later event and ownership checks remain in
their existing transaction. Global legacy callers keep the default whole-store
Bundle audit. The direct caller inventory is native insertion (already local),
migration 15 backfill and migration 16 reconciliation (legacy insertion before
the Bundle columns exist, followed by their global migration checks), and the
ordinary reopen bridge. Recovery validates the complete candidate rather than
creating a separate cycle-insertion bypass.
The latest-cycle reader also validates that selected native cycle's Bundle
history before reopen compares its projection. This closes the same consumed
cycle boundary without inspecting another Task's Bundles; legacy v0 has no
Bundle to validate. Existing single-Task history reads use the same validator.

### Public History Projection

The [incremental Viewer design](viewer-design.md#incremental-cache-and-publication)
uses its selected repository to validate all own Bundle/history relations in
bounded chunks before retaining the newest display window. It does not borrow
the old batch reader's upstream global-admission assumption. Native completion,
Evidence and ordinary history readers retain their own validation and never
consume the display cache.

`completion_history_repository.py` owns latest-cycle, single-Task and batch
history retrieval and metadata queries. It uses the caller's connection and
the existing storage-owned cycle/Receipt checks and shared Evidence validation
repository. The latter owns Bundle validation/acquisition; shared types and
completion writes remain in storage.
The single-Task reader retains its selected Bundle-history validation, while
the retained full-Viewer batch reader retains its upstream global-snapshot validation
assumption. These paths do not acquire a connection or start a transaction.

`task show` reads total, incomplete-legacy aggregate, and newest-first rows in
the same snapshot as all other Task data, regardless of display mode. Normal
presentation keeps only total and legacy incompleteness after validation;
`--audit` exposes the previous exact wrapper:

```json
{
  "total": 2,
  "returned_count": 2,
  "truncated": false,
  "legacy_history_incomplete": false,
  "cycles": []
}
```

`legacy_history_incomplete` is computed over all durable rows, not the bounded
return window. It is true when Task coverage is not complete, any saved cycle
is partial, or any `task_reopened` event has a null internal cycle link. A
fresh schema-v16-or-later Task with no cycle is complete history and false.

Each cycle and each candidate complete wrapper is measured using compact,
sorted-key, non-ASCII-preserving UTF-8 JSON. A cycle is at most 8,192 bytes;
the wrapper is at most 10 rows and 32,768 bytes. Collection stops at the first
non-fitting newest-first row. Version-0 basis uses JSON nulls and no receipt
IDs; versions 1 and 2 use integers and the stored one/two IDs without null
placeholders. Text prints only counts, flags, and newest non-content fields.
Version two projects the already validated frozen continuation gate; it does not
select fresh historical heads or requalify audit evidence as current evidence.

The formatter revalidates stored completion revision, evidence reason,
completion-hash, target value, and target-base text through the ordinary
privacy matcher before building a public cycle. It does not use the legacy
`dispatch_authorization` counter projection for stored Contract constraints
and checkpoint summaries. A rejection maps to the fixed
`completion_history_inconsistent` error and never exposes the offending field
or value; `task show` and Viewer use this same formatter.

The full-capture Viewer batch history is grouped/windowed for at most 500
selected Tasks and their newest 10 cycles each, not one query per cycle.
Its batch admission may compare current verification because each supplied
Task window includes its actual latest cycle. The incremental reader instead
streams every selected Task's own cycle in bounded chunks: historical Receipt
links are validated per chunk, and current verification is compared once with
the actual latest cycle after the stream, only for a done Task. A historical
chunk's last cycle is never treated as current. Both use the same historical
Receipt validator and latest-cycle comparison. Snapshot v4 uses the identical wrapper.
Source schemas v5-v14 synthesize empty/incomplete history; v15-v22 use stored
rows. Sources v17-v22 validate linked Receipt ownership, basis, target, and
pass/full qualification; v19-v22 also validate and discard Bundle linkage, and
v20-v22 additionally validate the source-appropriate closed verification basis
and Runner pointer.
Neither public events nor Viewer disclose internal event-cycle
or Verification Receipt IDs.

The only new stable error is
`completion_history_inconsistent: stored completion history is inconsistent`.
It exposes no IDs, counts, values, hashes, SQL, or paths.

<a id="current-schema-v22-manual-receipt-arm-and-bundle-integration"></a>

<a id="schema-v21-manual-receipt-arm-and-bundle-integration"></a>

## Current Schema-v22 Manual Receipt Arm And Bundle Integration

This section defines the manual Verification Receipt arm and its integration
with the sole three-branch selector under
[Schema-v21 Runner Gate-Basis Design](database-design.md#schema21-runner-gate-basis-design),
retained by [current schema v22](database-design.md#schema22-reservation-cleanup-design). It
does not rewrite the immutable v0.10.0 artifact or claim a later published
artifact identity. Schema, parser, completion, Viewer compatibility, Skill
guidance, package inventory, and tests form one supported boundary.

### Ownership And Data Model

`verification_receipts.py` owns Receipt input validation,
exact-current classification, manual-Receipt-arm evaluation, and the bounded public read
model. The single service selector combines that arm with the closed Runner
basis; callers do not choose a branch. `verification_receipt_repository.py`
owns stored-row validation and Receipt snapshot/append queries on the caller's
connection. `storage.py` retains schema, migration, full database admission,
and the schema-enforced Receipt uniqueness boundary. `tasks.py` and
`completion_workflow.py` consume the selected gate result; neither opens raw
SQLite or interprets verification prose.
`cli.py` owns stdin transport/dispatch/formatting for the one Receipt write leaf;
`cli_parser.py` owns its two mutually exclusive input modes.
`verification_results.py` decodes the bounded fixed JSON declaration into the
existing four Receipt inputs and verifies its Task ID against the command.

Schema v17 added an immutable `verification_receipts` table with:

```text
verification_receipt_id project_id task_id contract_revision
verification_expectation_digest command_label result duration_ms
scope_coverage target_kind target_value target_base_revision
target_generation created_at
```

IDs use the `tg_verification_receipt_<16-lowercase-hex>` grammar. Result is
`pass|fail|timeout`; coverage is `full|partial`; duration is a nonnegative
signed-64-bit millisecond integer. Legacy labels are nonempty,
privacy-validated, and at most 200 characters; native v18 rows store only the
internal `taskgov-owned-verification-subject-v1` value in the retained column.
Target fields reuse the existing four-way matrix.
Composite indexes support project/Task/current Contract/expectation/target
gate reads without one query per Receipt. Update/delete triggers preserve the
append-only boundary. A unique project/Task/target-generation key permits one
aggregate Receipt and makes retry require an explicit fresh target.

The same migration adds three internal fields to `task_completion_cycles`:

```text
verification_basis_version verification_expectation_digest
verification_receipt_id
```

Existing rows receive version 0 with null digest and null Receipt link. New
native cycle insertion is constrained to version 1 and stores the same domain-
separated digest of its exact Task verification text, including empty text and
preserved whitespace. The `caller_attestation` arm requires a linked Receipt;
the `not_required` and `runner_observation` arms require a null Receipt link and
are constrained by the shared schema-v21/v22 verification-basis union. The Receipt ID
is a nullable foreign key; repository and projection validation additionally
prove identical ownership, Contract, stored digest, target tuple, and
`pass/full` qualification. Existing cycle immutability covers all three
fields. A migration-owned insert guard permits version 0/null/null only for the
existing sole `legacy_current_done` partial reopen bridge when a legacy-
unknown done Task has no cycle; every normal native insert requires version 1.
None of the internal fields is added to the public completion-history
projection.

Migration 18 adds subject-basis version and nullable authority-snapshot and
verification-criterion IDs to both Receipts and cycles without rebuilding or
updating old rows. Existing rows read as subject version 0/null/null. A native
Receipt and nonempty native cycle use version 1 with matching capture IDs;
trimmed-empty verification uses version 1/null/null and no Receipt. Triggers
and readers enforce same-project/Task snapshot-to-criterion membership and the
exact target binding.

The expectation digest is:

```text
sha256("taskgov-verification-expectation-v1\0" + exact stored UTF-8 verification)
```

Its stored representation is the lowercase 64-hex digest. It binds eligibility
without storing another copy of verification prose. The public projection
never emits the digest. The complete target tuple is copied
from the locked Task; no caller target field exists. `created_at` and ID are
allocated under the writer. Structural validation reuses the existing target,
timestamp, privacy, signed-integer, and ownership validators.

The activated Skill sequence is exact material, existing target set with its
returned generation and closed route retained, review prepare/record, then
completion. Only `verification_route=receipt_required` makes a marker-`0` or
exact closed no-launch fallback branch additionally run external verification
and add a Receipt with that expected generation. `not_required` and
`runner_pass` do not; `blocked` stops with its returned existing blocking code.
Target setting remains before either verification branch. The current
[normal-loop call budget](review-completion-specification.md#receipt-meaning-and-record)
is unchanged by the Receipt input mode.

### Receipt Write And Freshness

The write service first validates result, duration, coverage, and positive
`expected_target_generation` without opening a writer, then starts
one short immediate transaction and rereads schema,
identity/binding, Task, Contract pointer, exact verification text, and target
tuple. It permits only in-progress or review-pending Tasks with specified
verification and a current target. Expected generation must equal the locked
target generation; drift returns `verification_basis_stale` without storage.
Capture version must then be 1 or the write returns `evidence_basis_stale`.
It derives the subject from the locked snapshot/criterion, computes the digest,
appends the Receipt and Evidence Reference without changing the Task or adding
an event, commits, and only then invokes post-commit maintenance. The Receipt
write may retry due Evidence and offers the ordinary asynchronous backup
opportunity; it remains Viewer-ineligible.

The caller's add invocation attests that the external run exercised the copied
target; taskgov does not prove that claim. The caller supplies no command body,
source revision, Contract revision, timestamp, Receipt ID, exit code, raw output,
exception, or arbitrary result document. The optional fixed stdin declaration
is defined by the [structured result owner](review-completion-specification.md#structured-verification-result).
The CLI rejects mixed/incomplete modes before state resolution, then rejects
read-only before reading at most the byte limit plus one from binary stdin.
The decoder rejects duplicate keys, non-integer numbers and non-UTF-8 input,
validates the closed shape and all typed strings' privacy, and reuses existing
Task-ID and Receipt-field normalizers. A document Task mismatch is stale evidence.
No writer is opened until decoding succeeds; the returned four values enter
the unchanged `add_verification_receipt` service. This keeps generation/Contract
invalidation, operation-scoped admission, physical Runner selection outside the writer,
locked revalidation, persistence, rollback and post-commit policy identical.
No producer adapter, result ledger or coverage inference is introduced.
A failed or timeout row records
evidence only and performs no Task/status/Contract/target/review/completion/
Handoff mutation. A second call in the same generation returns
`verification_receipt_already_recorded`; the caller can inspect `task show`,
then explicitly set a fresh target before another run.

Parser and common state errors retain their existing owners. The Receipt
service owns one ordered validator matching the specification: normalized
caller fields, Task existence, done/disallowed status, nonempty expectation,
stored target structure/presence, expected generation, capture version, then
the current schema-v22 Runner selector, then uniqueness. It is
used before and after the writer lock so a concurrent status, Contract,
expectation, or target change cannot select a different semantic ordering or
bind the row to new material. CLI formatting retains the three-line Receipt
prefix and appends preparation status and Packet/errors; no synthetic Task
event is added.

No label or subject argument exists. Version-aware stored validation applies
the legacy command-label predicate only to subject-zero rows and requires only
the fixed internal value for subject-one rows; public projection emits the
versioned subject union and never that internal value.

One shared Receipt evaluator classifies a row as exact-current only when project,
Task, Contract revision, expectation digest, and every target field equal the
coherent current basis. The sole completion selector delegates to this evaluator
only for marker `0` or the exact closed no-launch fallback; the qualifying Runner
branch and all other marker-`2` states use the [closed selector matrix](design.md#closed-gate-tags-and-parent-guards). The
manual arm computes, in order:

```text
specified expectation + no exact-current Receipt
  => verification_receipt_required
specified expectation + Receipt other than pass/full
  => verification_receipt_blocking
specified expectation + pass/full
  => satisfied
empty expectation + explicit nonempty not-required reason
  => receipt gate not required; existing attestation remains required
empty expectation + no not-required reason
  => verification_requirement_unspecified; required=true, satisfied=false
```

A nonempty capture-version-zero target instead reports
`evidence_basis_stale` before Receipt-required/blocking evaluation. Completion
check applies the same stale result for every capture-zero target because any
completion creates a new ledger source.

Old-target, old-Contract, and old-expectation rows remain visible only in the
bounded recent audit list and never affect that result. Any current non-
qualifying Receipt is retired by an explicit fresh target generation, not by
row update or delete.

A semantic edit of `tasks.verification` with a started target uses the existing
Contract-invalidation shape in the same Task transaction: clear typed
completion evidence and target kind/value/base, increment generation with
overflow protection, update the Task, and append the normal bounded event.
With no explicit status, review-pending moves to in-progress. Explicit active,
pause, block, or cancellation status follows ordinary transition validation;
explicit review-pending/done is rejected until a fresh target exists. The same
edit rejects caller completion-evidence options instead of applying and then
silently clearing them. Receipts and review rows remain immutable and become
historical. A same-content edit is a no-op under existing edit rules.

### Completion And Read Integration

The completion basis snapshot adds the current selected verification gate
summary and its nullable Receipt or Runner-observation basis. Outside-Git
preflight and locked revalidation both run the same sole selector. Ordered
validation keeps the explicit `verification_required` flag check and target
validation first, then applies the selected Runner/manual gate, then evaluates
current review findings/receipts. `task complete
--check` adds the two Receipt codes to its bounded allow-list; check never
writes or reserves a Receipt.

For specified verification, the manual arm proves that the unique current
Receipt is `pass/full` and inserts its Receipt foreign key; the qualifying
Runner arm proves the exact selected observation and inserts its Runner pointer
with a null Receipt link. Both then insert the version-1 cycle with the identical
expectation digest, Task update, and linked completion event in one transaction.
A native completion whose verification text is empty
after trimming requires an explicit nonempty not-required reason, then inserts
version 1 with its exact-byte digest and a null Receipt link; the exact empty
string uses the empty-text digest. Schema 23 stores the reason in Task/Packet
and immutable cycle, not in the Bundle or authority snapshot. The pure gate
returns `verification_requirement_unspecified` for an undeclared live Task;
the completion-check allow-list and actual writer share that rejection.
Historical done reads retain legacy NULL declarations without weakening a new
completion; non-NULL cycle reasons must match the Task. Any ownership,
target, digest, link, or gate drift rolls everything back. Existing
`verification_attestation=true` remains in the cycle, so the Receipt
strengthens rather than silently replaces the current explicit assertion.
Version-0/null/null marks only migrated cycles and the sole exact compatibility
bridge. A version-1 nonempty cycle whose stored tagged arm has neither its valid
linked Receipt nor its valid qualifying Runner observation is
`completion_history_inconsistent`, never an inferred legacy success.

The one Receipt command is `verification receipt add`. Its CLI handler commits
the existing Receipt transaction before invoking the existing Packet handler
for pass/full, with a fresh read connection and the recorded Receipt ID.
Success data is `receipt` plus `review_preparation`; registration errors remain
failures, while post-commit preparation errors are explicit partial success.
The original mutation outcome and one outer maintenance pass are preserved.
`verification_receipts.py` owns exact-current Receipt-ID binding using its
existing snapshot validator. `review_packet.py` applies it in both existing
reads, including readonly `--verification-receipt-id` retries. No generic
workflow engine, new ledger, or completion selector
is introduced. Packet-only retries cannot write or change the recorded basis.
Only the final Packet read with an ID shares its validated Receipt set with
`review_verification.py`; the first read and completion revalidation remain
independent snapshots. The shared value is neither cached nor persisted.
`task show` obtains Receipt totals, exact-current
counts, gate, and newest 10 rows in the same query-only transaction as the
Task. A show-only collector retains the validated exact-current row for normal
presentation; filtering recent rows cannot establish that exact-current row.
The pure normal projection removes repeated expectation, Contract revision and
target, retaining the subject, gate, current counts, and concise current result.
`--audit` preserves the previous detailed `verification_evidence` object. Both
use the same validation and public allow-lists; no separate Receipt command,
pagination, storage mutation, or gate branch is added.

Ordinary Task state resolution uses structural operation-local admission;
other consumers retain their separately declared admission scope. After that
boundary, the `task show` handler validates the selected Task's review storage
in its own snapshot, reads only that Task for
Runner routing and builds the complete show projection once. Marker `0` and
done history use that same Task-local connection; only live marker `2` releases
SQLite for physical selection and opens the final projection connection. No
branch adds a second global Runner-graph validation.

Recent rows replace the old top-level label with the five-key versioned
subject union. The parent read model adds `current_verification_subject`, null
without a capture-v1 verification criterion. Subject-zero done cycles retain
their exact v17 link/label rules; reopen clears current capture and requires a
fresh target.

The read model emits the exact types and nulls fixed by the specification.
Empty marker-`0` expectation is satisfied only for an explicit waiver or
validated prior done history. An unspecified active Task is required and
unsatisfied with `verification_requirement_unspecified`; reopening old blank
history does not supply a waiver. A nonempty
active Task first reports target-required or stale basis; its selected manual
arm then reports Receipt-required, Receipt-blocking, or satisfied, while its
qualifying Runner arm reports satisfied with a null Receipt ID. A done Task
backed by a matching version-0 cycle reports the
explicit legacy exemption even when its target is absent, including the sole
bridge-created cycle. A version-1 done cycle is validated against its stored
manual or Runner branch before projection. Task-show failure data includes a null
`verification_evidence`; audit text Task show remains unchanged. The Receipt write
retains its three-line prefix, followed by preparation status and Packet or
sanitized errors under the public connection contract.

The audit completion-history JSON remains unchanged. The current done Task
retains its target and Receipt, so normal `task show.verification_evidence` exposes
the current qualifying basis; the cycle target tuple also identifies it.
Reopen advances the target and makes it historical. A later history projection
would require separate evidence and approval rather than silently changing
Viewer snapshot v4.

### Schema, Viewer, Packaging, And Tests

Migration 17 `verification_receipts` creates the Receipt table, indexes, triggers,
the three completion-cycle basis columns and insert/link guards, and the schema-
history row. Existing cycles receive only version 0/null-digest/null-link as a
structural legacy discriminator. It never parses verification text or events
to infer runs or create Receipt evidence. Reentry validates the exact objects,
columns, constraints, immutable triggers, ownership/link relations, and
absence of invented Receipt rows. Migration 18 adds the capture/provenance/
subject tables and columns described in
[the Evidence foundation](evidence-design.md#schema-v18-capture-and-subject-foundation), preserves exact v17 projections,
and creates no historical evidence. Migration 19 adds Bundle/link/snapshot and
projection state plus cycle version/null fields without inventing a historical
Bundle. Migration 20 adds the audit-only Runner-storage objects and Bundle-v2
tagged union without inventing Runner or historical evidence. Migration 21
widens only the frozen gate-basis discriminators without promoting audit
history. Migration 22 removes retired Evidence reservations and adds the
source-22/v2 Bundle arms while preserving sealed history. Old binaries reject newer schemas normally; setup remains the sole
public migrator. The established reopen bridge remains the only
post-migration writer of the exact version-0/null/null legacy shape.

Viewer source compatibility accepts v5-v22, while snapshot v4 fields and UI
remain unchanged. Receipt writes are Viewer-ineligible. The existing bounded
batch completion-history
loader performs bounded joins for selected version-1 cycle Receipt links and
v18+ subject/provenance/manifest/Reference relations plus the v19-v22 Bundle
discriminator and source-aware Runner basis, validates them, and
discards every ledger field before snapshot formatting. There is no Receipt dataset,
snapshot field, panel, filter, or detail, and no per-Task Receipt query. This
compatibility update must land with the schema bump so explicit setup and later
unrelated Viewer maintenance cannot fail merely because the canonical state
migrated.

Focused tests reuse one matrix owner for result/coverage/current-binding gate
cases and existing migration/completion/Task-show helpers. They cover all
source schemas v1-v22, rollback/reentry, no legacy synthesis, exact target and
Contract invalidation, semantic verification edit, failure-generation reset,
unique per-generation ownership, version-0 legacy exemption, version-1
Receipt-link enforcement, the sole post-v17 legacy bridge, cycle-target
reconstruction, concurrent target/edit drift and expected-generation
rejection, read-only no-write, privacy rejection, byte/count bounds,
Evidence/backup maintenance, Viewer v22 compatibility including valid/corrupt
link, Bundle-discriminator, source-appropriate verification-basis, and Runner-
graph batch validation, parser/help/
output, and unchanged unrelated projections. Test
facts and command inventories must remain derived from their existing owners
rather than copied into CI or multiple test modules.
