# TG-M23 Derived Evidence Accepted Execution Contract

<a id="tg-m23-derived-evidence"></a>

> [!IMPORTANT]
> MIXED FORMAL AUTHORITY: TG-M23.1, BOUNDED OFFLINE/MOCK TG-M23.2, AND
> TG-M23.3 OFFLINE/MOCK INTEGRATED ACCEPTANCE ARE ACCEPTED PREDECESSORS; NO
> TG-M23 UNIT IS CURRENT. Load this document
> only when the current Task Contract or [authority index](../authority.md)
> routes to TG-M23. Network/live Analyzer acceptance remains outside scope;
> SQLite, `storage.py`,
> public CLI/Skill, network/live-model action, gate mutation, and Task mutation remain
> outside scope.

[Specification](../specification.md)=behavior; [design](../design.md)=structure; this document is the sole TG-M23 unit owner/router for sequence, Task boundaries, descriptor, packet, status, report, provenance, citation, activation order, permissions, and gates; [process safety](tg-m23-process-safety.md#tg-m23-process-safety)=the sole delegated owner of Windows containment, private temporary storage, and atomic publication/recovery mechanics; [plan.md](../../plan.md)=other static/cross-sequence; Task DB=live state/evidence. The delegated owner does not own unit state or core data semantics, and this router does not restate its physical safety mechanics.

## Sequence Boundary

TG-M23 is sequential Tier 2 work in lane `TG-M23-DERIVED-EVIDENCE`:

| Unit/order | Task | Dependency |
|---|---|---|
| TG-M23.1 / 10 | `tg_task_722ac8a308a23d1c` | accepted TG-M22.4 |
| TG-M23.2 / 20 | `tg_task_d5511d2ca7db93dc` | accepted TG-M23.1 |
| TG-M23.3 / 30 | `tg_task_0ada32d2b4f9759d` | accepted TG-M23.2 |

## Process Safety Route

All TG-M23 Windows process isolation, restricted-token, Job/handle/pipe, timeout/cancel, private-temp/quarantine, and atomic-publication/recovery mechanics are owned only by the [TG-M23 process-safety contract](tg-m23-process-safety.md#tg-m23-process-safety). The route is normative whenever those mechanics are in scope; it does not independently activate a unit.

<a id="tg-m23-1"></a>

## TG-M23.1 Design

### Future Local Layout And Ownership

M23.2 may use only ignored `<canonical-package-state>/analysis/`: immutable `outbox/tg_analysis_job_<16-lower-hex>.json`; atomically replaced `status/tg_analysis_job_<16-lower-hex>.json`; and `reports/tg_analysis_report_<16-lower-hex>.json` plus `rendered/tg_analysis_report_<16-lower-hex>.md`, provisional only while a complete running intent exists and immutable only after publication. There is no analyzer index or SQLite table. The exact lease, 100,000-file preflight, private `tmp/` tree, quarantine, freshness, cleanup, and durable publication rules are owned only by the [TG-M23 process-safety contract](tg-m23-process-safety.md#tg-m23-process-safety).

Modules: `analysis_contracts.py`=schema/canonical JSON/digest/ID/limit; `evidence_consumer.py`=independent M22 index/Bundle validator, importing neither `storage.py` nor `evidence_projection.py`; `analysis_packet.py`=ephemeral packet; `analysis_outbox.py`=lock/descriptor/status/replay/publication.
`analysis_validator.py`=validator; `analysis_renderer.py`=JSON→UTF-8/LF Markdown; `codex_analysis_adapter.py`=subprocess/credential boundary plus the fixed private one-shot broker specified by the delegated process-safety owner; `analysis_worker.py`=internal/test `run_once(...)`; no caller-visible launch.
`state_paths.py`/`state_resolver.py`=fixed contained paths only; `storage.py`/schema/public CLI/Skill/setup/doctor/maintenance/Task loop unchanged.

### Descriptor, Packet, And Replay Contract

Descriptor v1 exact/no-extra keys=`analysis_job_id,descriptor_version,source_kind,source_key,source_basis,recipe,recipe_digest,descriptor_digest`; `descriptor_version=1`, `source_kind=native_bundle|legacy_index_entry`.
`source_basis` exact keys=`project_id,projection_generation,index_digest,entry`; entry exact keys=`task_id,completion_cycle_id,cycle_ordinal,bundle_state,bundle_id,bundle_file,bundle_digest,file_digest,sealed_at`.
Native values reproduce one validated index entry and sealed Bundle. Legacy state=`legacy_unknown`, its last five entry values null; no Bundle lookup or Receipt/provenance-shaped value is added.

`C(v)`=accepted M22 Bundle/index canonical JSON with every value/rejection/serialization/no-normalization rule, no LF; durable=`C(v)||LF`. `H(d,b)` is raw SHA-256 over NUL-free ASCII `d`||NUL||`b`; `S(h)`=ASCII `sha256:`+64 lowercase hex; `I(p,h)`=ASCII `p`+first 16 hex. Digests inside `C` stay full 71-byte ASCII; named digests use `S` unless null; no implicit framing.
Source identity is an exact no-extra-key object copied from validated basis: native `project_id,task_id,completion_cycle_id,cycle_ordinal,bundle_id,bundle_digest,file_digest`; legacy `project_id,task_id,completion_cycle_id,cycle_ordinal,bundle_state`. `source_key=S(H("taskgov-analysis-source-v1",C(identity)))`.
`recipe` is exactly `producer_version,report_schema_version,renderer_version,prompt_schema_version,inference_mode,declared_model_id`; mode `offline|codex_optional`, offline model null. Relevant byte/behavior change increments the positive version. `recipe_digest=S(H("taskgov-analysis-recipe-v1",C(recipe)))`.
Vector: `C(identity)`=`{"bundle_state":"legacy_unknown","completion_cycle_id":"c","cycle_ordinal":1,"project_id":"p","task_id":"t"}` gives `source_key=sha256:43de9c707c10c49ab1b3bc939975b058bbf9b79dfbd495324ecd5e2135581fbf`; `C(recipe)`=`{"declared_model_id":null,"inference_mode":"offline","producer_version":1,"prompt_schema_version":1,"renderer_version":1,"report_schema_version":1}` gives `recipe_digest=sha256:8ac0a31a34894d0d759b7844b8f0d8b6999520374f34a73b45a2a4cff7b29f3d`.
`analysis_job_id=I("tg_analysis_job_",H("taskgov-analysis-job-v1",ASCII(source_key)||NUL||ASCII(recipe_digest)))`. `descriptor_digest=S(H("taskgov-analysis-descriptor-v1",C(the exact descriptor object without descriptor_digest)))`.

After source validation derive ID. Absent ID is exclusive-create; an existing descriptor replays original basis iff source key, recipe digest, project ID, and all nine entry fields match, never a later index. Other same-ID content collides; generation/index-digest drift alone neither rewrites nor collides. `published|failed|cancelled` replay immutable outcome without attempt; only changed source identity/recipe creates a job.

Packet v1 is exact no-extra-key `packet_version,analysis_job_id,source_kind,source_basis,source`; `packet_version=1`. Native `source` is the exact independently validated Bundle envelope; legacy `source=null`, with content only in `source_basis`.
Packet caps: native 16,842,752 bytes, legacy 16,384; excess=`packet_too_large`, never truncation. Packet bytes are memory-only, framed directly to stdin, discarded after the attempt; `packet.json` is forbidden and only the digest may persist.
`prompt_bytes`=fixed/nonempty/versioned BOM-free UTF-8/LF with one final LF/no NUL; `packet_bytes=C(packet)`; P/Q=shortest positive ASCII-decimal lengths. Stdin exactly=`ASCII("taskgov-analysis-stdin-v1")||LF||ASCII("prompt-length:")||P||LF||ASCII("packet-length:")||Q||LF||LF||prompt_bytes||packet_bytes||LF`; no other bytes/normalization/NUL; cap=262,144, excess=`input_too_large`, no truncation/launch. `prompt_digest=S(H("taskgov-analysis-prompt-v1",prompt_bytes))`; identical `packet_digest`/report `input_digest=S(H("taskgov-analysis-packet-v1",packet_bytes))`; `accepted_output_digest=S(H("taskgov-analysis-output-v1",strict canonical output bytes))`, no LF. Prompt/frame byte change increments `prompt_schema_version`.

Status keys=`analysis_job_id,state,worker_attempt_count,adapter_attempt_count,inference_state,fixed_code,duration_ms,packet_digest,accepted_output_digest,report_id,report_digest,render_digest`; state∈`pending|running|published|failed|cancelled`; inference∈`disabled|policy_blocked|pending|running|succeeded|input_too_large|unavailable|launch_failed|timeout|output_too_large|invalid_output|failed|cancelled`; R3=`report_id,report_digest,render_digest`. Counters=integers 0..2; integer duration=0..600,000≤`300000 * worker_attempt_count`. Code=null except failed∈`source_invalid|packet_too_large|report_invalid|publication_failed|interrupted`, cancelled=`cancelled`.

PS=`policy_blocked|input_too_large|succeeded|unavailable|launch_failed|timeout|output_too_large|invalid_output|failed`. Offline: adapter=0, inference=`disabled`, output=null. Optional adapter=0 for `pending|policy_blocked|input_too_large`, 1..2 for `running|succeeded|unavailable|launch_failed|timeout|output_too_large|invalid_output|failed`, cancelled=0..2. Output is non-null exactly for `succeeded`; optional `running|PS` requires packet; `pending` may omit it. `mode/inference` no-adapter results are exactly `offline/disabled|codex_optional/policy_blocked|codex_optional/input_too_large`; no other result uses the delegated no-adapter-tree proof or consumes an adapter attempt.

Matrix—`pending`: worker/duration=0; code/digests/R3=null; inference offline/optional=`disabled|pending`. All others have worker=1..2. `running`: null code; inference offline=`disabled`, optional=`pending|running|PS`; packet required; output per above; R3 is all-null or all-nonnull intent after report/render validation plus required tree-quiescent/no-tree cleanup proof. `published`: packet+R3, null code, inference=`disabled|PS`. Failed source/packet: packet/output/R3 null, inference=`disabled|pending`; failed report/publication: packet, R3 null, inference=`disabled|PS`. `interrupted`: no packet→`disabled|pending`; packet/no output→`disabled|non-succeeded PS`; packet/output→`succeeded`. Optional adapter=0/inference=`failed` is valid only as the exact pre-call reclaim terminal: failed/interrupted, worker=2, packet set, output/R3 null. `cancelled`: R3 null/fixed code; optional=`cancelled`/adapter 0..2 or offline=`disabled`/0; packet iff validated. Other R3 null; terminals have no rerun/timestamp/raw error.

Status atomic; pending→running increments worker. Locked no-wait `run_once` enforces 100,000 files and ID order; selects ≤1 pending/reclaimable. Complete intent gets bounded counter-neutral recovery. No-intent running with worker below 2 first uses a counter-only running→running CAS: worker alone changes and duration is unchanged. The optional pre-call terminal CAS is exactly running worker=2/adapter=0/inference=`pending`/packet set/output+R3 null/code null → failed worker=2/adapter=0/inference=`failed`/code=`interrupted`, with every other field unchanged. Reclaim never increments adapter/launches/touches O/quarantine. Worker≤300,000 ms; expiry=`failed/interrupted`; adapter increments pre lookup/call, ≤2. Descriptor precedes pending. Under lease, a valid descriptor with absent exact status leaf exclusively creates pending once; reports are neither scanned nor replay input. Present status requires exact pairing; invalid type/parse/digest/binding stays unchanged.

### Report And Citation Contract

Let A=`structural_facts|trusted_caller_declarations|llm_derived|omissions|uncertainties|declared_code_occurrences|citations`; FK=`bundle|task|contract|target|authority_snapshot|criterion|criterion_link|artifact_manifest|artifact_entry|evidence_reference|verification_receipt|review_receipt|review_provenance|finding_snapshot|completion_evidence|omission`; DK=`reviewer_class|model_state|declared_model_id|skill_state|declared_skill_id|declared_skill_version|profile|lens|context_relation|method`.
Report v1 is exact canonical no-extra-key JSON: envelope=`report_schema_version,report_digest,payload`; payload order=`report_id,analysis_job_id,source_kind,source_key,recipe_digest,inference_state,structural_facts,trusted_caller_declarations,legacy_absence,llm_derived,omissions,uncertainties,declared_code_occurrences,citations,reproducibility`. The four evidence classes never substitute for one another; rendering is a pure projection, not evidence.

Fact=`fact_kind,value,citation_ids`, kind∈FK, value=cited scalar/null/empty-array/atomic row, never inferred. Declaration=`declaration_kind,value,citation_ids`, kind∈DK, value=cited v1 scalar/null/one ordered code, never summary/reviewer-key text. Native `legacy_absence=null`; legacy exact keys=`state=legacy_unknown,receipt_detail=unavailable,provenance_detail=unavailable,citation_id`.

Omission/uncertainty keys=`code,citation_ids`; omission∈cited M22 code or `claim_capacity_exceeded|render_capacity_exceeded|legacy_detail_unavailable|inference_unavailable`; uncertainty∈`insufficient_basis|conflicting_basis|legacy_absence`. Derived keys=`tag,non_authoritative,text,citation_ids,uncertainty`; occurrence keys=`kind,code,bundle_id,review_receipt_id,review_provenance_id,citation_ids`. Caps: facts/declarations=16,384, citations/occurrences=65,536, omissions/uncertainties=4,096; each non-citation has 1..8 valid citation IDs.

Each A array sorts by unsigned lexicographic canonical-JSON element bytes (shorter prefix first) and rejects duplicates. Each `citation_ids` is unique unsigned-ASCII ascending. Fact array value may only be `[]`; copied M22-row arrays retain M22 order. Adapter `claims` use element-byte order; `source_refs` sort by kind `legacy_basis,native_pointer`, then null-before-string unsigned-UTF-8 pointer; duplicates fail.

`reproducibility` exact keys=`producer_version,declared_model_id,prompt_schema_version,prompt_digest,input_digest,accepted_output_digest,report_schema_version,renderer_version`; offline model/prompt/output digests=null; values are declared/deterministic, never authenticated actor/model identity.

For native input, `analysis_validator.py` independently derives a required-pointer multiset exactly equal to source facts/declarations/occurrences/omissions; subsets fail. Envelope `format_version|bundle_digest`+payload metadata→`bundle`; `task|contract|target|authority_snapshot` map identically; plural collections→singular FK kinds. Scalar/null/empty collection=one item; criterion/link/artifact entry/Evidence Reference/finding snapshot=one exact-row fact; nonempty Bundle omissions=source omissions. Everything mandatory+cited; projection/report overflow=`report_invalid`, never omission.

Native v1 provenance: ID/version/assurance/producer class+version/digest=mandatory `review_provenance` facts; each reviewer/model/Skill scalar including nullable IDs/version/context=declaration; ordered profile/lens/method code=element-pointer declaration+occurrence. Empty collection→one `review_provenance` fact `[]`, no occurrence. Null provenance only for `not_required`: one fact, no Receipt declaration/occurrence. Expected pointers/values/kinds/IDs/cardinalities derive only from Bundle.

Omissions=canonical source+recomputed-runtime union: `legacy_detail_unavailable` iff legacy; `inference_unavailable` iff optional publication≠`succeeded`; capacity code iff suffix removal. Source items remain; runtime codes cite Bundle root/legacy basis. Uncertainties=deduplicated legacy `legacy_absence` plus retained claims' non-`none` code/citations; native offline none. Extra/missing/substitution=`report_invalid`.

Native citation exact keys=`citation_id,citation_kind,source_key,bundle_id,bundle_digest,file_digest,json_pointer,entity_id,entity_digest`; RFC 6901 pointer resolves in Bundle; kind∈FK. Entity pair is ID+digest for `authority_snapshot|criterion|artifact_manifest|artifact_entry|evidence_reference|review_provenance|finding_snapshot`, ID+null for `task|criterion_link|verification_receipt|review_receipt|completion_evidence`, null+null for `bundle|contract|target|omission`. Artifact entry=manifest pair+ordinal; empty collection/absent verification=null-identity `bundle`; null `not_required` provenance=Receipt identity; Receipt/completion also cite Evidence Reference.

Legacy exact: `structural_facts`,`trusted_caller_declarations`,`declared_code_occurrences`=`[]`; `citations=[L]`. L keys=`citation_id,citation_kind=legacy_index_entry,source_key,project_id,projection_generation,index_digest,task_id,completion_cycle_id,cycle_ordinal`, copied from basis. `legacy_absence.citation_id` and all legacy `citation_ids`=`L.citation_id`; no other fact/declaration/occurrence/citation or Receipt/provenance/v0/Bundle identity/value. Citation ID=`I("tg_analysis_citation_",H("taskgov-analysis-citation-v1",C(citation without citation_id)))`; same-ID/different-bytes fails. Native source/Bundle/file=descriptor/packet; citation source key=descriptor; IDs resolve once; items bind only by `citation_ids`.

Derived: tag=`llm_derived/batch_analyzer/1`, `non_authoritative=true`, UTF-8 text≤1,000 bytes, 1..8 citations, uncertainty∈`none|insufficient_basis|conflicting_basis|legacy_absence`, count≤2,048. Occurrence: kind∈`profile|lens|method`, code, exact Bundle/Review Receipt/provenance IDs. Cross-Receipt/Bundle repetition is descriptive; in-Receipt duplicate invalid. Grouping never authenticates identity, proves competence/independence, scores quality/diversity, upgrades assurance, fills unknown/null/legacy, or changes a gate.

Adapter output=exact canonical no-extra-key `output_schema_version,analysis_job_id,source_key,recipe_digest,claims`; version=1, descriptor bindings, 0..2,048 claims. Claim exact keys=`text,source_refs,uncertainty`: nonempty privacy-guarded UTF-8 text at most 1,000 bytes; derived uncertainty enum; 1..8 unique `{kind,json_pointer}` refs. Native: kind=`native_pointer`, nonempty required-projection RFC 6901 pointer, uncertainty not `legacy_absence`; legacy: exactly one required `{kind:legacy_basis,json_pointer:null}`. Fixed ordering above; empty claims valid.

Validator independently maps refs to exact citation IDs and creates only fixed-tag/non-authoritative items; adapter text supplies no ID/tag/authority/fact/declaration. Output digest covers the validated sorted envelope. Over 65,536 bytes=`output_too_large`; privacy/binding/order/duplicate/schema/reference/native-legacy failure=`invalid_output`; both have null digest, enter no report/durable state, and use transient disposal below.

Packet ID/kind/basis byte-match descriptor; `payload.analysis_job_id`=descriptor/status ID. Report ID/kind/source key/recipe digest match descriptor; complete running-intent/published status IDs/digests match report/render. Reproducibility versions/model match recipe; input digest=packet/status; accepted-output digest/inference state=status. Before intent, binding mismatch atomically publishes terminal `failed/report_invalid`, null report fields, no report/render destination.

Report JSON/Markdown caps are 16,777,216/8,388,608 bytes.
From sorted claims keep the longest report-fitting prefix; add `claim_capacity_exceeded` iff a suffix is removed; then keep the longest report+Markdown-fitting prefix and add `render_capacity_exceeded` iff more are removed. Partial claims/source items fail; no fitting skeleton=`report_invalid`. `report_id=I("tg_analysis_report_",H("taskgov-analysis-report-id-v1",ASCII(source_key)||NUL||ASCII(recipe_digest)||NUL||ASCII(inference_state)||NUL||ASCII(accepted_output_digest or "offline-null")))`; `report_digest=S(H("taskgov-analysis-report-v1",C(payload)))`; `render_digest=S(SHA-256(exact Markdown bytes))`. A complete intent additionally requires the delegated `publish_ready` proof; no report or Markdown becomes visible without its atomic publication proof. Raw chat/prompts/reasoning/logs/diffs/environment/secrets/session/provider bodies/unrestricted content never persist.

Markdown v1=`T||LF||LF||join(B1..B10,LF||LF)||LF`; `T=ASCII("# Task Governance Analysis Report v1")`; `B=UTF8("## "||NAME)||LF||LF||ASCII("    ")||C(VALUE)`. Exact NAME→VALUE order: `Identity`→object `report_schema_version,report_digest,report_id,analysis_job_id,source_kind,source_key,recipe_digest,inference_state`; `Structural Facts`→`structural_facts`; `Trusted Caller Declarations`→`trusted_caller_declarations`; `Legacy Absence`→`legacy_absence`; `LLM Derived`→`llm_derived`; `Omissions`→`omissions`; `Uncertainties`→`uncertainties`; `Declared Code Occurrences`→`declared_code_occurrences`; `Citations`→`citations`; `Reproducibility`→`reproducibility`. BOM-free UTF-8/LF; no tab/fence/HTML/link/prose/wrap/normalization/Markdown escape or data outside four-space canonical JSON; only JSON escaping. Layout/escaping change increments `renderer_version`.

Renderer input is the canonical validated report plus LF. The delegated [process-safety publication contract](tg-m23-process-safety.md#atomic-publication-and-recovery) solely defines destination-parent validation, held temporary files, ordered atomic rename, rollback, crash quarantine, and counter-neutral recovery. Its `publish_ready` proof must bind the exact report and Markdown bytes/digests above before the all-nonnull R3 intent; uncertainty preserves `running` and returns deferred `interrupted`. Recovery never creates an adapter attempt, and parse-equivalent/reordered/reframed/digest-only mismatch is `report_invalid` with no retry.

### Offline And Optional Codex Protocol

Offline publishes the deterministic report and Markdown above with zero model calls. Optional logical shell-free argv is exactly `codex exec --ephemeral
--sandbox read-only --ignore-user-config --ignore-rules --skip-git-repo-check
--model <exact-approved-id> --output-schema <private-schema> -o
<private-output> -`. Output schema is strict `additionalProperties=false`. Adapter output is accepted only through the schema, binding, ordering, size, and privacy rules above; stdout/stderr are never report inputs.

The [process-safety owner](tg-m23-process-safety.md#optional-adapter-process-boundary) solely defines private cwd/home/environment, credentials, runtime identity, Windows token/Job/desktop/handle/pipe containment, attempt freshness, timeout/cancel/child-tree termination, transient output disposal, and bounded broker-worker joins. Its proof maps only to the existing inference states and fixed codes above; it cannot add report content, evidence meaning, retries, or a caller-visible launch.

No public CLI/Skill/credential/provider/network activates; M23.2 uses one closed-code immutable credential-free mock output fixture. Missing/launch/post-count/cap/nonzero-empty/expiry/cancel/invalid outcomes map only to `unavailable|launch_failed|interrupted|output_too_large|failed|timeout|cancelled|invalid_output`. No provider token plus spend ceiling means zero live calls/cost, `codex_optional=policy_blocked`, acceptance=`not_applicable`, and no stored inference. Live launch requires separate one-shot-data/exact-model/proven-isolation/non-tool-readable-broker authority plus independent pre-call ceiling proof. Mocks cover retry, timeout, cancel, worker join, child-tree proof, and output freshness/binding.

Completion=document/exact-file/diff checks, current full Verification Receipt, two independent Tier 2 reviews, no unresolved High/Medium.

<a id="tg-m23-2"></a>

## TG-M23.2 Activation

Task `tg_task_d5511d2ca7db93dc`: source/outbox→validator/report/renderer→mock adapter/worker; scope=paths/disk-consumer/core/internal `run_once`/private fixed mock broker/offline publication; no SQLite/`storage.py`/public CLI/Skill/maintenance/Task-loop/daemon/caller-visible launch/network/live; evidence/assurance/Task/gates inert.

Completion=deterministic dedupe; provenance/citation/status/replay/failure/privacy/no-fabrication/scoring/assurance-upgrade; one real-Windows two-process full-session race starts two `run_once`s together, pauses the nondeterministic winner after lease/before selection, proves the loser busy before inventory with no durable read/write and state unchanged until winner release, then exactly one worker increment/publication; focused/full offline/package+exact-diff, current Receipt, two clean Tier 2 reviews; credentials/payment need separate cost/data authority.

<a id="tg-m23-3"></a>

## TG-M23.3 Integrated Acceptance

Task `tg_task_0ada32d2b4f9759d` accepts multi-source native/legacy queue/restart/replay/failure; offline/mock; v1/null/legacy; repeated cross-Receipt/Bundle codes; ID/version/digest/pointer traces; legacy cycle/index only; classes/gates separate/inert.

Accepted shape=one reviewed authority activation; one focused multi-source integration module reusing shared fixture/oracle; bounded M23.1 corrections only; accepted-state synchronization in the intended final snapshot; exact target, focused/package/performance preflight, full verification and current Receipt, two later clean Tier 2 reviews, commit, and completion.

Required=offline/mock focused/full; privacy; package/release; exact-diff/integration; current Receipt before final reviews. Live=`not_applicable`; later exact credential/data/model/provider-ceiling authority creates a fresh target. SQLite/`storage.py`/schema/public CLI/Skill/network/live call/gate or Task mutation remain outside scope. Stop before M24.
