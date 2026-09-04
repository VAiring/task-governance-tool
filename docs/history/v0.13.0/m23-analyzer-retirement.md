# M23 Analyzer Retirement Capture

> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves retired Analyzer contracts, not supported product
> behavior or authority. Historical wording such as current or implemented does
> not activate any runtime, gate, or permission.

- Source commit: `5c58f1038a5f7df46b393ec5fc5cf454fa38eb36`
- Capture unit: `TG-M23R.10`
- Current replacements: [specification](../../specification.md),
  [design](../../design.md), and [plan](../../../plan.md).
  Use the public CLI for live Task state and evidence.
- Capture purpose: preserve the exact current Analyzer sections immediately
  before their atomic runtime and active-document retirement. Only the original
  section bytes inside each fenced block are captured; runtime code is not
  archived here.

## Source: `docs/specification.md:3169-3492`

````markdown
## Derived-Evidence Analyzer

The Analyzer is an offline-first, non-authoritative consumer of one validated
Evidence index entry and, for native state, its sealed Bundle. It uses only the
ignored `<canonical-package-state>/analysis/` tree: immutable
`outbox/tg_analysis_job_<16-lower-hex>.json`, atomically replaced
`status/tg_analysis_job_<16-lower-hex>.json`, and paired
`reports/tg_analysis_report_<16-lower-hex>.json` and
`rendered/tg_analysis_report_<16-lower-hex>.md`. The report pair is provisional
only while a complete running intent exists and immutable after publication.
There is no Analyzer index or SQLite table. The Analyzer changes no Task,
Contract, Evidence Reference, criterion link, Bundle, gate, CLI, Skill, setup,
doctor, maintenance, or Viewer state.

### Descriptor, Packet, Status, And Replay

Descriptor v1 has exactly
`analysis_job_id,descriptor_version,source_kind,source_key,source_basis,recipe,
recipe_digest,descriptor_digest`; version is `1` and source kind is
`native_bundle|legacy_index_entry`. `source_basis` has exactly
`project_id,projection_generation,index_digest,entry`, where `entry` has exactly
`task_id,completion_cycle_id,cycle_ordinal,bundle_state,bundle_id,bundle_file,
bundle_digest,file_digest,sealed_at`. Native values reproduce one validated
index entry and Bundle. Legacy state is `legacy_unknown` and its last five entry
values are null; no Bundle lookup or Receipt/provenance-shaped value is added.

Let `C(v)` be the accepted Bundle/index canonical JSON without LF; durable bytes
are `C(v)||LF`. Let `H(d,b)` be raw SHA-256 over NUL-free ASCII `d`, NUL, and
`b`; `S(h)` be `sha256:` plus 64 lowercase hex; and `I(p,h)` be prefix `p` plus
the first 16 hex. Digests within canonical JSON stay full 71-byte ASCII, named
digests use `S` unless null, and no implicit framing exists.

Native source identity is exactly
`project_id,task_id,completion_cycle_id,cycle_ordinal,bundle_id,bundle_digest,
file_digest`; legacy identity is exactly
`project_id,task_id,completion_cycle_id,cycle_ordinal,bundle_state`.
`source_key=S(H("taskgov-analysis-source-v1",C(identity)))`. Recipe is exactly
`producer_version,report_schema_version,renderer_version,prompt_schema_version,
inference_mode,declared_model_id`; mode is `offline|codex_optional`, offline
model is null, and any relevant byte/behavior change increments the applicable
positive version. `recipe_digest=S(H("taskgov-analysis-recipe-v1",C(recipe)))`.
`analysis_job_id=I("tg_analysis_job_",H("taskgov-analysis-job-v1",
ASCII(source_key)||NUL||ASCII(recipe_digest)))`; `descriptor_digest` covers the
exact descriptor without that digest under `taskgov-analysis-descriptor-v1`.
The fixed legacy vectors are:

- `C(identity)={"bundle_state":"legacy_unknown","completion_cycle_id":"c","cycle_ordinal":1,"project_id":"p","task_id":"t"}` produces
  `source_key=sha256:43de9c707c10c49ab1b3bc939975b058bbf9b79dfbd495324ecd5e2135581fbf`;
- `C(recipe)={"declared_model_id":null,"inference_mode":"offline","producer_version":1,"prompt_schema_version":1,"renderer_version":1,"report_schema_version":1}` produces
  `recipe_digest=sha256:8ac0a31a34894d0d759b7844b8f0d8b6999520374f34a73b45a2a4cff7b29f3d`.

After independent source validation the ID is derived. An absent ID is created
exclusively. An existing descriptor replays the original basis only when source
key, recipe digest, project ID, and all nine entry fields match; it never binds
a later index. Generation/index-digest drift alone neither rewrites nor
collides. Any other content under the same derived ID is a collision and is
rejected. `published|failed|cancelled` replays its immutable outcome with no
attempt; only a changed source identity or recipe creates a new job.

Packet v1 has exactly
`packet_version,analysis_job_id,source_kind,source_basis,source`; version is 1.
Native source is the exact independently validated Bundle envelope; legacy
source is null and carries content only through `source_basis`. Native packet
size is capped at 16,842,752 bytes and legacy at 16,384; overflow is
`packet_too_large` and never truncates. Packet bytes remain memory-only, are
framed directly to stdin, and are discarded after the attempt; `packet.json`
is forbidden and only its digest may persist.

`prompt_bytes` is fixed, nonempty, versioned, BOM-free UTF-8/LF with one final
LF and no NUL. With `packet_bytes=C(packet)` and shortest positive ASCII-decimal
lengths P/Q, stdin is exactly:

```text
ASCII("taskgov-analysis-stdin-v1")||LF||ASCII("prompt-length:")||P||LF||
ASCII("packet-length:")||Q||LF||LF||prompt_bytes||packet_bytes||LF
```

The frame is capped at 262,144 bytes; overflow is `input_too_large` without
launch. `prompt_digest=S(H("taskgov-analysis-prompt-v1",prompt_bytes))`;
identical `packet_digest` and report `input_digest` are
`S(H("taskgov-analysis-packet-v1",packet_bytes))`; and
`accepted_output_digest=S(H("taskgov-analysis-output-v1",strict canonical
output bytes))`, with no LF in any canonical input. A prompt/frame byte change
increments `prompt_schema_version`.

Status has exactly
`analysis_job_id,state,worker_attempt_count,adapter_attempt_count,inference_state,
fixed_code,duration_ms,packet_digest,accepted_output_digest,report_id,
report_digest,render_digest`. State is
`pending|running|published|failed|cancelled`; inference state is
`disabled|policy_blocked|pending|running|succeeded|input_too_large|unavailable|
launch_failed|timeout|output_too_large|invalid_output|failed|cancelled`.
Counters are integers 0..2; duration is 0..600,000 and no greater than
`300000 * worker_attempt_count`. Fixed code is null except failed
`source_invalid|packet_too_large|report_invalid|publication_failed|interrupted`
or cancelled `cancelled`. The three report fields are all null or all non-null.

For the status matrix below, `PS` is exactly
`policy_blocked|input_too_large|succeeded|unavailable|launch_failed|timeout|
output_too_large|invalid_output|failed`.

Offline uses adapter count 0, inference `disabled`, and null output digest.
Optional execution uses adapter count 0 for `pending|policy_blocked|
input_too_large`, 1..2 for `running|succeeded|unavailable|launch_failed|
timeout|output_too_large|invalid_output|failed`, and 0..2 for `cancelled`.
Output digest is non-null exactly for `succeeded`; optional `running` and all
members of `PS` require a packet, while pending may omit
one. The exact no-adapter results are `offline/disabled`,
`codex_optional/policy_blocked`, and `codex_optional/input_too_large`; no other
result may claim the no-adapter-tree proof or consume no adapter attempt.

Pending has zero worker/duration and null code, digests, and report tuple; its
offline/optional inference is `disabled|pending`. Every other state has worker
count 1..2. Running has null code and required packet; offline inference is
`disabled`, optional inference is `pending|running` or a member of `PS`, output
follows the succeeded-only rule, and the report tuple is either
all null or an all-nonnull intent made only after report/render validation and
the required tree-quiescent/no-tree cleanup proof. Published requires packet,
an all-nonnull report tuple, null code, and inference `disabled` or a member of
`PS`. Failed source/packet has no packet/output/report and inference
`disabled|pending`; failed report/publication has packet, no report, and
inference `disabled` or a member of `PS`. An `interrupted` failure has
`disabled|pending` without packet, `disabled` or a non-succeeded member of `PS`
with packet but no output, and `succeeded` with packet and output. The
optional adapter-count-zero/inference-`failed` combination is valid only for
the exact pre-call reclaim terminal described below. Cancelled has no report,
fixed code `cancelled`, optional inference `cancelled` with adapter count 0..2
or offline `disabled` with adapter count 0, and a packet exactly when source
validation completed. Other terminal states have no rerun, timestamp, or raw
error field.

Status replacement is atomic. Pending-to-running increments the worker count.
The no-wait lease enforces the 100,000-file cap and identifier order and selects
at most one pending/reclaimable job. A complete intent receives bounded,
counter-neutral recovery. A no-intent running row with worker count below 2
first uses a counter-only compare-and-swap; the optional exact pre-call reclaim
terminal is running worker 2, adapter 0, inference pending, packet set, null
output/report/code to failed worker 2, adapter 0, inference failed, fixed code
`interrupted`, with every other field unchanged. Reclaim does not launch,
increment the adapter, or touch output/quarantine. A worker attempt is capped at
300,000 ms; expiry is `failed/interrupted`; adapter count increments before
lookup/call and never exceeds 2.
Descriptor publication precedes pending status. Under the lease, a valid
descriptor with an absent exact status leaf exclusively creates pending once;
reports are neither scanned nor used as replay input. A present status requires
exact pairing. Invalid present descriptor or status structure stays unchanged.

### Report, Claims, And Citations

Report v1 is exact canonical no-extra-key JSON with envelope
`report_schema_version,report_digest,payload`. Payload has exactly, in this
order, `report_id,analysis_job_id,source_kind,source_key,recipe_digest,
inference_state,structural_facts,trusted_caller_declarations,legacy_absence,
llm_derived,omissions,uncertainties,declared_code_occurrences,citations,
reproducibility`. Structural facts, trusted declarations, derived claims, and
omissions/uncertainties never substitute for each other; rendering is a pure
projection, not evidence.

Fact kinds are exactly
`bundle|task|contract|target|authority_snapshot|criterion|criterion_link|
artifact_manifest|artifact_entry|evidence_reference|verification_receipt|
review_receipt|review_provenance|finding_snapshot|completion_evidence|omission`.
Declaration kinds are exactly
`reviewer_class|model_state|declared_model_id|skill_state|declared_skill_id|
declared_skill_version|profile|lens|context_relation|method`. A Fact is
`fact_kind,value,citation_ids` and holds only a cited scalar, null, empty array,
or atomic source row. A Declaration is
`declaration_kind,value,citation_ids` and holds only a cited v1 scalar, null, or
one ordered code. Native `legacy_absence` is null; legacy absence has exactly
`state=legacy_unknown,receipt_detail=unavailable,
provenance_detail=unavailable,citation_id`.

Omission and uncertainty have `code,citation_ids`. Analyzer-only omissions are
`claim_capacity_exceeded|render_capacity_exceeded|legacy_detail_unavailable|
inference_unavailable`; uncertainties are
`insufficient_basis|conflicting_basis|legacy_absence`. A derived claim has
`tag,non_authoritative,text,citation_ids,uncertainty`, where uncertainty is
exactly `none|insufficient_basis|conflicting_basis|legacy_absence`; an occurrence has
`kind,code,bundle_id,review_receipt_id,review_provenance_id,citation_ids`, where
kind is exactly `profile|lens|method`.
Facts/declarations are capped at 16,384, citations/occurrences at 65,536,
omissions/uncertainties at 4,096, and each non-citation has 1..8 valid citation
IDs. Arrays sort by unsigned lexicographic canonical-JSON element bytes,
shorter prefix first, and reject duplicates; citation IDs are unique ascending
ASCII. Copied source-row arrays retain their source order.

`reproducibility` has exactly
`producer_version,declared_model_id,prompt_schema_version,prompt_digest,
input_digest,accepted_output_digest,report_schema_version,renderer_version`.
Offline model/prompt/output digests are null. These are deterministic or
declared values, not authenticated model or actor identity.

For native input the validator independently derives a required-pointer
multiset exactly equal to all source facts, declarations, occurrences, and
omissions; subsets fail. Bundle envelope and payload metadata map to `bundle`;
Task, Contract, target, and authority snapshot map identically; plural
collections map to singular kinds; each criterion, link, artifact entry,
Reference, Finding snapshot, scalar, null, or empty collection is represented
exactly once. Native v1 Review provenance makes ID/version/assurance/producer/
digest mandatory facts, each scalar a declaration, and each profile/lens/method
code a declaration plus occurrence. An empty provenance collection produces
one `review_provenance` fact with value `[]` and no occurrence. Null provenance
is allowed only for `not_required` and produces one fact with no Receipt
declaration or occurrence. Every pointer, value, kind, identity, and
cardinality derives only from the Bundle; projection or report overflow is
`report_invalid`, never an omission.

Report omissions are the canonical union of source omissions and recomputed
runtime omissions. `legacy_detail_unavailable` exists exactly for legacy
input, `inference_unavailable` exactly when optional publication does not end
in `succeeded`, and a capacity code exists exactly when its corresponding
suffix is removed. Source omissions remain present; runtime omissions cite the
Bundle root or legacy basis. Uncertainties are the deduplicated legacy
`legacy_absence` plus non-`none` uncertainties from retained claims; native
offline analysis has none. An extra, missing, or substituted item is
`report_invalid`.

Native citations have exactly
`citation_id,citation_kind,source_key,bundle_id,bundle_digest,file_digest,
json_pointer,entity_id,entity_digest`. The RFC 6901 pointer resolves inside the
Bundle. Identity/digest pairs use ID+digest exactly for
`authority_snapshot|criterion|artifact_manifest|artifact_entry|
evidence_reference|review_provenance|finding_snapshot`; Task, criterion link,
Receipt, and completion evidence use ID+null; Bundle, Contract, target, and
omission use null+null. An artifact entry uses
its manifest pair plus ordinal; an empty collection or absent verification uses
a null-identity Bundle citation; null `not_required` provenance uses the Review
Receipt identity; and Receipt/completion facts also cite their Evidence
Reference. Legacy output has empty facts/declarations/occurrences and one
`legacy_index_entry` citation `L` with exactly
`citation_id,citation_kind,source_key,project_id,projection_generation,
index_digest,task_id,completion_cycle_id,cycle_ordinal`, copied from the
validated basis, and `citation_kind=legacy_index_entry`. Its
`legacy_absence.citation_id` and every legacy `citation_ids` value equal
`L.citation_id`; it exposes no other citation, Receipt, provenance, v0, Bundle,
or inferred value. For native input, the source, Bundle, and file bindings equal
the descriptor and packet, the citation source key equals the descriptor source
key, each ID resolves exactly once, and report items bind only through
`citation_ids`. Citation IDs
are `I("tg_analysis_citation_",H("taskgov-analysis-citation-v1",C(citation
without citation_id)))`; same ID with different bytes fails.

Derived items use tag `llm_derived/batch_analyzer/1`,
`non_authoritative=true`, privacy-guarded UTF-8 text up to 1,000 bytes, 1..8
citations, one closed uncertainty, and at most 2,048 items. Repetition across
Receipts/Bundles is descriptive only; grouping never authenticates identity,
proves competence/independence, scores quality/diversity, upgrades assurance,
fills unknown/null/legacy data, or changes a gate.

Adapter output has exactly
`output_schema_version,analysis_job_id,source_key,recipe_digest,claims`; version
is 1 and claims count is 0..2,048. Each claim has exactly
`text,source_refs,uncertainty`, with nonempty privacy-guarded text up to 1,000
bytes and 1..8 unique refs. Every ref has exactly `{kind,json_pointer}`. Native
refs are nonempty required-projection RFC 6901 `native_pointer` values and
cannot claim `legacy_absence`; legacy output has exactly one
`{kind:legacy_basis,json_pointer:null}` ref. Claims sort by unsigned
lexicographic canonical-JSON element bytes; source refs sort by kind
`legacy_basis,native_pointer`, then null before string and unsigned UTF-8
pointer bytes; duplicates fail. The validator maps refs to exact citation IDs
and supplies IDs, tags, and non-authority markers; adapter text cannot supply
facts or declarations. Output over 65,536 bytes is `output_too_large`; any
schema, privacy, binding, ordering, duplicate, reference, or native/legacy
violation is `invalid_output`. Rejected bytes have null digest and never enter
a report or durable state.

Packet ID, kind, and basis byte-match the descriptor, and
`payload.analysis_job_id` equals the descriptor and status ID. Report ID, kind,
source key, and recipe digest match the descriptor, while running-intent or published
status IDs and digests match the exact report and render. Reproducibility
versions/model match the recipe; input and accepted-output digests and
inference state match status. Before intent, any binding mismatch atomically
publishes terminal `failed/report_invalid` with null report fields and no
report/render destination.

Report JSON and Markdown are capped at 16,777,216 and 8,388,608 bytes. From
sorted claims, retain the longest report-fitting prefix and add
`claim_capacity_exceeded` iff a suffix is removed; then retain the longest
report-plus-Markdown-fitting prefix and add `render_capacity_exceeded` iff more
are removed. Partial source items or a non-fitting skeleton are
`report_invalid`. Exact identities are
`report_id=I("tg_analysis_report_",H("taskgov-analysis-report-id-v1",
ASCII(source_key)||NUL||ASCII(recipe_digest)||NUL||ASCII(inference_state)||NUL||
ASCII(accepted_output_digest or "offline-null")))`,
`report_digest=S(H("taskgov-analysis-report-v1",C(payload)))`, and
`render_digest=S(SHA-256(exact Markdown bytes))`. A report pair becomes visible
only with the exact-byte `publish_ready` proof defined by the
[Analyzer process and publication design](design.md#derived-evidence-analyzer-process-and-publication-boundary).

Markdown v1 is `T||LF||LF||join(B1..B10,LF||LF)||LF`, where
`T=ASCII("# Task Governance Analysis Report v1")` and each block is
`UTF8("## "||NAME)||LF||LF||ASCII("    ")||C(VALUE)`. The exact name/value
order is `Identity` to the object `report_schema_version,report_digest,
report_id,analysis_job_id,source_kind,source_key,recipe_digest,inference_state`,
then `Structural Facts`, `Trusted Caller Declarations`, `Legacy Absence`,
`LLM Derived`, `Omissions`, `Uncertainties`, `Declared Code Occurrences`,
`Citations`, and `Reproducibility` to their like-named payload values. Output is
BOM-free UTF-8/LF with no tab, fence, HTML, link, prose, wrapping,
normalization, Markdown escape, or data outside those four-space canonical JSON
blocks. Only JSON escaping applies; layout/escaping changes increment
`renderer_version`.

### Offline And Optional Analysis

Offline mode publishes the deterministic report and Markdown with zero model
calls. Optional logical shell-free argv is exactly
`codex exec --ephemeral --sandbox read-only --ignore-user-config --ignore-rules
--skip-git-repo-check --model <exact-approved-id> --output-schema
<private-schema> -o <private-output> -`. Its output schema is strict
`additionalProperties=false`; stdout/stderr are never report inputs. The
process boundary uses a private cwd/home/environment, credential exclusion,
immutable runtime identity, Windows token/Job/desktop/handle containment,
attempt freshness, bounded timeout/cancel/tree termination, transient output
disposal, and bounded worker joins as specified by the active design.

No public CLI or Skill activates Analyzer execution. The implemented acceptance
surface is credential-free offline/mock operation. Missing, launch, count, cap,
nonzero-empty, expiry, cancellation, and invalid outcomes map only to
`unavailable|launch_failed|interrupted|output_too_large|failed|timeout|
cancelled|invalid_output`. Without separately approved provider credentials,
data/model authority, proven isolation, non-tool-readable broker, and a
pre-call spend ceiling, optional mode remains `policy_blocked`, makes zero live
calls, incurs zero model cost, and stores no inference.

````

## Source: `docs/design.md:2060-2380`

````markdown
<a id="derived-evidence-analyzer-process-and-publication-boundary"></a>

## Derived-Evidence Analyzer Structure

The Analyzer uses only ignored `<canonical-package-state>/analysis/` state.
`analysis_contracts.py` owns schema, canonical JSON, digests, identifiers, and
limits. `evidence_consumer.py` independently validates the Evidence index and
Bundle and imports neither `storage.py` nor `evidence_projection.py`.
`analysis_packet.py` owns the memory-only packet. `analysis_outbox.py` owns the
lease, descriptor, status, replay, and publication state. `analysis_validator.py`
owns report validation; `analysis_renderer.py` owns the pure JSON-to-UTF-8/LF
Markdown render. `codex_analysis_adapter.py` is the closed mock facade and owns
the fixed prompt, output schema, and adapter validation boundary.
`_analysis_windows_process.py` owns the closed process-safety state-machine
oracle plus a read-only native capability preflight that always returns
`policy_blocked`; `_analysis_win32.py` owns the typed Windows handle,
private-tree, and publication primitives used by that boundary.
`analysis_worker.py` exposes only internal/test `run_once(...)`; there is no
caller-visible launch. The current implementation contains no native broker,
provider, or child-process launch path and performs no live inference.
`state_paths.py` and `state_resolver.py` supply fixed contained paths only.
SQLite, `storage.py`, schema, setup, doctor, maintenance, CLI, Skill, and Task
loop are unchanged by Analyzer execution.

The durable layout contains immutable `outbox` descriptors, atomically replaced
`status` files, immutable paired `reports`/`rendered` outputs, one lease, and a
private `tmp` tree. The outbox is selected in identifier order with a
100,000-regular-file preflight. No Analyzer index or SQLite table exists.

### Controller Interface, Lease, And Private Tree

The core passes one bounded attempt input
`(analysis_job_id,N,packet_digest,stdin_bytes,argv,E,cancel)` and, when
publication is requested, exact validated report/Markdown bytes and digests.
The process boundary returns only a bounded adapter outcome, duration, a sealed
result or null, `tree_quiescent`, and `publish_ready`. It cannot add or
reinterpret report content, evidence meaning, status fields, digests, retry
eligibility, or a caller-visible launch.

The controller/broker/target rules below are the closed offline/mock
process-safety model and oracle exercised by the current implementation. They
are not an active native launch path. A future native implementation would have
to satisfy these same invariants under separate authority; the current native
preflight stops at `policy_blocked` before an adapter attempt.

Within that model, let C be the controller, B the fixed private one-shot broker,
T the target, and J the per-attempt Job. S is `output-schema.json`, O is `output.json`, I and Q
are sealed input/result pagefile mappings, and Vb/Vc are broker/controller
events. Pagefile-backed IPC creates no taskgov file; it makes no claim about OS
page, hibernation, or dump storage.

`taskgov-analysis.lock` is no-follow and noninheritable. C holds byte 0 via
`LockFileEx(...,LOCKFILE_EXCLUSIVE_LOCK|LOCKFILE_FAIL_IMMEDIATELY)` before root
inspection, counting, input, or status open and through terminal publication or
quarantine. Busy or uncertain acquisition returns deferred `interrupted` with
no creation, inventory, or durable read/write. Parallel and read-only Analyzer
sessions are unsupported. Final release is one `UnlockFileEx` then
`CloseHandle`.

Under the lease C creates and holds one absent-before-open
`tmp/.taskgov-analysis-<8-lower-alnum>/` root per worker count or
publication-eligible no-adapter result. Pre-existence fails before count or
publication; identity, bytes, token, and old zero never prove freshness.
Adapter leaf order is S, O, `report.json`, `report.md`: B holds S/O
delete-on-close and C holds the reports. A core-authorized no-adapter result
creates only held report files; S/O never exist, held enumeration proves the
exact two report leaves and S/O absence, and C's ledger proves no broker,
target, Job, restricted token, mapping, event, pipe, stdio, or worker handle was
created. Those facts are the no-adapter-tree proof. Adapter success proves S/O
absent; a failed proof closes them unread and quarantines the root.

No-follow preflight requires one exact contained non-reparse directory identity
and at most 32 entries. Existing roots are quarantine and are never traversed,
read, changed, deleted, claimed, or reused. Entry 33, an unexpected name/type/
identity, escape, overflow, or inspection failure stops. Only the current root
may become `tree_quiescent`; while live it belongs to B. Each retry uses a fresh
attempt number, root, Job, broker, mappings, events, schema/output, target,
pipes, and workers. Run-wide controller DACL/thread freeze may be established
once before attempt 1 but is revalidated before each broker receives I. No path,
handle, process, Job, mapping, event, pipe, output, timestamp, or modification
time is reused across attempts.

### Held Files, Freshness, And Security Descriptors

The exact Win32 handle aliases are:

```text
RD=FILE_READ_DATA  RA=FILE_READ_ATTRIBUTES  WD=FILE_WRITE_DATA
DL=FILE_LIST_DIRECTORY  DA=FILE_ADD_FILE|FILE_ADD_SUBDIRECTORY
CR=READ_CONTROL  SY=SYNCHRONIZE
SR=FILE_SHARE_READ  SW=FILE_SHARE_WRITE  SD=FILE_SHARE_DELETE
OI=FILE_OPEN_IF  X=FILE_FLAG_OPEN_REPARSE_POINT
K=FILE_FLAG_BACKUP_SEMANTICS
D=FILE_ATTRIBUTE_TEMPORARY|FILE_FLAG_DELETE_ON_CLOSE|X
OP=(DELETE|RD|RA|SY,SW|SD,CREATE_NEW,D)
OC=(WD|SY,SR|SD,OPEN_EXISTING,X)
SP=(DELETE|RD|WD|RA|SY,SR|SD,CREATE_NEW,D)
SC=(RD|RA|SY,SR|SW|SD,OPEN_EXISTING,X)
TH=(CR|DELETE|RD|WD|RA|SY,0,CREATE_NEW,X)
RH=(CR|DELETE|RD|RA|SY,0,OPEN_EXISTING,X)
S0=(CR|DL|DA|RA|SY,SW,OI,K|X)
R0=(CR|DELETE|DL|DA|RA|SY,0,OPEN_EXISTING,K|X)
DP=(DL|DA|RA|SY,SW,OPEN_EXISTING,K|X)
```

S0 access is exactly `0x00120087`, share `0x2`, and exists only for the lease
owner's atomic status compare-and-swap. It is acquired after the lease and held
through that status session; SW authorizes no second session. Directory DELETE/
share-delete, root/outbox/lease R0, RH, and controller handles otherwise remain
unchanged. `DF=FileDispositionInfo.DeleteFile`.
`AR=FILE_RENAME_INFORMATION(TRUE,held-S0,exact-basename)` and
`NR=FILE_RENAME_INFORMATION(FALSE,held-DP,exact-basename)` each run once on a
held TH through typed `NtSetInformationFile(...,FileRenameInformation)`. B owns
O/S through OP/SP and T gets only OC/SC. Opens are no-follow and identity-bound;
live tuples are never reclaimed.

Let CU be the exact current primary `TokenUser` SID, RC be
`WinRestrictedCodeSid=S-1-5-12`, OW be `OWNER RIGHTS=S-1-3-4`, FT be
`FILE_TRAVERSE`, and WC/WO be write-DACL/write-owner. Root and report-temp
security descriptors are protected, noninheriting, canonical, and contain no
default, null, generic, or other ACE. Root DACL is exactly
`DENY OW(WC|WO);ALLOW CU(R0.access);ALLOW RC(FT|RA)`; report-temp DACL is
`DENY OW(WC|WO);ALLOW CU(TH.access)` with no RC ACE. O uses
`DENY OW(WC|WO);ALLOW CU(OP.access|OC.access);ALLOW RC(OC.access)`; S and stdio
retain their exact CU/RC dual allows plus the OW deny. T cannot list, add,
reopen, rename, delete, or change report security or reach destination parents
or durable data.

B flushes and revalidates S before C atomically records attempt N and running.
A crash consumes N, never a timestamp. O is one closed-code immutable mock
fixture. Its held fresh handle plus `(analysis_job_id,N,packet_digest)` binds the
only candidate result. Partial, replaced, late, wrong-attempt, wrong-packet, or
post-read-changed output is `invalid_output`. Rejected bytes, stdout/stderr
prefixes, prompts, packets, and provider bodies are discarded before terminal
state and never enter reports, durable state, logs, or quarantine.

### Optional Adapter And Windows Containment

This subsection continues the closed offline/mock oracle. Present-tense process
steps describe the invariants proved by the mock state machine, not operations
performed by a native broker. The current code never calls a provider or
creates B, T, or J.

Within the oracle, the core's exact argv runs with absolute `lpApplicationName`, a digest-bound
immutable isolated runtime/copy, a fresh empty non-Git cwd, and fresh private
`CODEX_HOME`. Child environment E is a non-null double-NUL-terminated Unicode
block containing exactly ordered `CODEX_HOME,PATH,PATHEXT,SystemRoot,TEMP,TMP`,
with no duplicates, `=drive` entries, or other keys. Saved authentication,
API-key variables, provider/network config, console, site, plugin, user config,
rules, ambient cwd, and ambient DLL lookup are never reused. B performs no
semantic parse and only bounded opaque copy. The stdin writer sends only the
exact bounded frame; stdout/stderr are concurrently drained into separate
65,536-byte memory prefixes and discarded. S is strict and private. Live mode
is policy-blocked without separate containment/broker, immutable runtime/home,
credential/parent-handle absence, and model/data/cost authority.

Let `SDC=DELETE|READ_CONTROL|WRITE_DAC|WRITE_OWNER`, `PR=PROCESS_ALL_ACCESS`,
`TR=THREAD_ALL_ACCESS`, `WR=WINSTA_ALL_ACCESS|SDC`,
`DR=DESKTOP_ALL_ACCESS|SDC`, and
`BP=SeChangeNotifyPrivilege|SeAssignPrimaryTokenPrivilege|
SeIncreaseQuotaPrivilege`. A valid PR/TR bit must be documented by the minimum
supported modern Windows SDK; mismatch is pre-count `policy_blocked`. `P(U)`
means U gets `ACCESS_DENIED` for every PR/TR bit, their union, and
`MAXIMUM_ALLOWED` on C and all C threads.

C creates separate sibling primary tokens from its original token.
`TT=CreateRestrictedToken(flags=0,DeletePrivileges=all-except-
{SeChangeNotifyPrivilege},SidsToRestrict=[(RC,0)])` must have exactly that one
privilege, exact restricted SID `[RC]`, restricted state, no `WRITE_RESTRICTED`,
and inert OW. `BT=CreateRestrictedToken(C-original;flags=0;
delete=all-except-BP;restrict=[])` must have exact BP, no restricted SIDs, and
all other privileges permanently deleted and unaddable, including debug,
ownership, backup, restore, and DACL bypass.

Before B, C holds required self handles and one probe-thread handle. C process
and every current C-thread DACL deny CU and OW all PR/TR, and `P(duplicate-user)`
must pass. C closes the probe and creates no later thread. Its BT creation handle
has exactly `TOKEN_ASSIGN_PRIMARY|TOKEN_DUPLICATE|TOKEN_QUERY`. Broker
environment contains exactly ordered `SystemRoot,TEMP,TMP`, where TEMP/TMP name
the fresh root, with no PATH, profile, config, proxy, credential, duplicate,
drive, or other key.

B is atomically launched suspended with `CreateProcessAsUserW(BT)`: absolute
digest-bound immutable broker application, fixed arguments, fresh root cwd,
broker environment, `bInheritHandles=TRUE`, flags
`EXTENDED_STARTUPINFO_PRESENT|CREATE_SUSPENDED|CREATE_UNICODE_ENVIRONMENT|
CREATE_NO_WINDOW`, and `STARTUPINFOEXW` with no std-handle flag and exactly
`JOB_LIST=[J]` plus
`HANDLE_LIST=[IB,QB,VbB,VcB,TTB]`. Those are fresh inheritable duplicates only
for launch, with exact rights `SECTION_MAP_READ`, `SECTION_MAP_WRITE`,
`EVENT_MODIFY_STATE`, `SYNCHRONIZE`, and
`TOKEN_ASSIGN_PRIMARY|TOKEN_DUPLICATE|TOKEN_QUERY`; every original and unlisted
handle is noninheritable. Attribute construction, inheritance, Job membership,
and process/thread-handle noninheritance are proved before exactly one broker
resume. Any uncertainty enters the abnormal Job path with no ambient fallback.
B receives no Job, lease, controller process/thread, destination parent,
durable file, stdio, console, or ambient handle and reproves BT plus `P(B)`
before reading I.

Before USER/GDI, window, hook, worker, or T, B creates a fresh noninheritable
explicit-security station and desktop in this order:
`CreateWindowStationW(NULL,CWF_CREATE_ONLY,...)`, `SetProcessWindowStation`,
`CreateDesktopW(L"default",...,0,...,explicit-SD)`, `SetThreadDesktop`.
`GetUserObjectInformationW` provides the returned `station\default` name. The
private pair dual-allows CU and RC exact WR/DR; no original station/desktop or
controller/broker process/thread/IPC/Job/lease/destination/durable object allows
RC. C sets and queries exactly the Job UI limits for handles, clipboard,
system/display settings, global atoms, desktop, and exit-Windows. B then creates
its fixed workers and freezes thread/USER state. No later broker thread creates
or receives a window, hook, clipboard, DDE, COM-STA, message queue, or other
desktop IPC; the target desktop has no broker receiver. TT must be denied every
PR/TR form on C, B, and all their threads and WR/DR on C's original station/
desktop.

T is created only by `CreateProcessAsUserW(TT)` with returned station/desktop,
E, exact three stdio handles, `STARTF_USESTDHANDLES`, handle list, inheritance,
and exact suspended Unicode/no-window flags. Non-breakaway Job membership is
proved before exactly one target resume. T receives no broker, Job, IPC, token,
lease, or durable handle and cannot open B through CU, OW, RC, a desktop
receiver, or inherited handle.

### Job, Workers, Termination, And Retry

Each attempt creates one fresh unnamed Job. C is sole owner of its
noninheritable kill-on-close/no-breakaway handle and opens no named/foreign Job.
I/Q are unnamed nonexecutable pagefile mappings: I is controller-sealed and
broker-readable; Q is broker-write/controller-read and contains only
`version,state,analysis_job_id,N,packet_digest,length,digest,bytes`. Vb is
broker-modify/controller-sync and Vc the inverse; T receives neither. Events
provide synchronization, never child-tree proof.

Attempt N is 1..2. Monotonic attempt/all/proof/final budgets are exactly
120000/240000/5000/1000 ms; wall clock is untrusted. Retry requires proved
cleanup and one of `unavailable|launch_failed|timeout|invalid_output`, occurs
only after N=1, and is forbidden after cancel. Uncertainty before recording N
is `policy_blocked`.

C owns the lease, Job, broker process and primary-thread-until-resume, token
creation handles/copies, mappings, events, and launch duplicates. B owns its
station/desktop, target token/process/thread, pipes, worker thread handles, S/O,
and Q write. T owns only stdio. C has no I/O worker and B has no Job, lease,
destination-parent, or controller handle. Each owner closes its objects in the
specified post-proof order.

B owns exactly one bounded stdin writer and one bounded drain worker per stdout
and stderr. Each holds only its pipe end, bounded buffer, and thread state and
cannot write Q or access S/O. B owns all worker handles. On normal target signal
B closes stdin and broker pipe ends in protocol order and joins all three within
5,000 ms. Timeout, orphaned pipe, incomplete EOF, or unjoined thread is abnormal:
B writes no terminal Q and C immediately enters the abnormal Job path. No
worker survives B, a successful Q, or controller return.

Success order is: T signals; B joins workers and closes every T handle; two Job
queries prove `ActiveProcesses==1` with PID exactly `[B]`; B held-reads/caps O;
B seals bound length/digest/opaque bytes into Q; B deletes/proves S/O absent;
B signals terminal and exits. C accepts only terminal-valid Q, broker signal,
Job zero, and stable zero/Q reread, then validates Q. No single exit, event,
handle signal, or zero observation suffices.

Timeout, cancellation, broker crash, worker hang, partial Q, or abnormal child
state first latches the outcome, then calls `TerminateJobObject(J)` before
waiting; no thread is terminated. C must prove broker signal and stable Job zero
before reading Q, retrying, removing the root, unlocking, or returning. Without
that proof all remain forbidden: the current tree is quarantine and C fail-fast
holds lease and sole Job, with kill-on-close only as termination fallback.

Only a proved abnormal path may leave Q unread, prove S/O absent, remove the
root, close attempt ownership, and close J. A retry keeps the same lease while
creating an entirely fresh attempt. Otherwise J closes before the final lease
release. `tree_quiescent` means every required Job/process/worker/handle/file
absence proof and ordered finalization succeeded; otherwise result digests are
null and nothing is reused.

### Atomic Report Publication And Recovery

Before report temporaries or intent, C acquires and holds both noninheritable
destination parents and proves canonical identity, containment, and same-volume
placement. Failure changes no destination, status, or report tuple, preserves
running, and returns deferred `interrupted`. C creates both temps with TH, sets
delete disposition before writing, never uses delete-on-close, writes/flushes,
held-rereads, and validates exact canonical bytes, privacy, bindings, digests,
and caps.

`publish_ready` requires valid held report/Markdown temps, both destination
parents, a complete all-nonnull report tuple bound to those bytes, no other
private leaf, and either adapter `tree_quiescent` with S/O closed/absent or the
no-adapter proof with S/O never created. Intent order is JSON then Markdown.
Promotion clears delete disposition and performs same-handle atomic NR through
the held destination parent and exact basename. Replace, copy, pre-delete,
reopen, or path fallback is forbidden. An absent final is completed; its held
handle rechecks parent, name, identity, length, bytes, and digest. C removes and
proves absence of the empty root, atomically records published, closes files,
then destination parents, then releases the lease.

On failure an unpromoted temp stays delete-disposed through last close and
original-identity absence proof. A promoted same handle is reset delete-disposed,
closed, and proved original-identity absent or foreign-replaced. The current
root is removed only after its absence proof. Only after every matching rollback
proof may the core atomically publish null report tuple with
`failed/publication_failed`, close parents, and unlock. Uncertainty preserves
running intent/root and returns `interrupted`; ambiguous status writes are
reread under lease and rollback continues only while state remains running.

A crash between clearing disposition and rename may leave only complete,
intent-bound, privacy/cap-valid canonical report/Markdown; it is quarantine,
never raw adapter output, provider data, prompt, packet, stdout/stderr, or
rejected bytes. Recovery holds both destination parents and reads only
descriptor, source, complete intent, and exact final destinations. It never
reads, traverses, deletes, or reuses adapter output/quarantine and never changes
worker or adapter counters.

Missing means absent; present files open with RH. Any acquisition, type,
identity, length, or cap uncertainty preserves running and changes nothing.
Recovery memory-reads capped files, discards mismatch details, and rechecks held
identity/length plus canonical JSON/bindings/digest or exact rerender. Two valid
files complete publication under held handles and status reread. Otherwise only
matching handles are delete-disposed, closed, and proved absent or foreign-
replaced; foreign/mismatched files remain untouched and unexposed. A null-report
publication failure or collision requires every rollback proof; parse-equivalent,
reordered, reframed, or digest-only mismatch is `report_invalid` without retry.

````
