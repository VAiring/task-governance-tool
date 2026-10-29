# Integrity Validation Transition Inventory

## Role And Discovery Boundary

This is the coverage ledger for the conditional
[integrity validation placement and transition design](integrity-validation-plan.md).
That plan owns placement, implementation units, protection, activation and
retirement. This inventory records definitions, call families, current guarantees,
dispositions, owners and regression anchors; it neither activates a change nor
certifies live database state. Live Task state and results remain in the public CLI.

Discovery was performed on 2026-10-29 against the shared working tree, including
observed HEAD `6cc69cfcfb20dddda7b45f3c92df775b9bc67c33` and existing uncommitted
changes. This date/revision is informational, not authority or a frozen review
target. Symbols are the durable locators; approximate line numbers are only
discovery aids. Every implementation unit rechecks its current callsites.

Current behavior remains owned by [database persistence](database-specification.md)
and [database design](database-design.md), [Task operations](task-operation-specification.md)
and [Task design](task-operation-design.md), [Review/completion](review-completion-specification.md)
and [Review design](review-completion-design.md), [Setup/state](setup-state-specification.md)
and [Setup design](setup-state-design.md), [Evidence](evidence-specification.md)
and [Evidence design](evidence-design.md), and [Viewer](viewer-specification.md)
and [Viewer design](viewer-design.md).
Runner call contexts retain [Plan authoring](runner-plan-authoring-specification.md)
and [execution](runner-execution-specification.md), with their paired designs.

Code paths are relative to
`task-governance-tool/scripts/task_governance_tool/`; tests use repository-relative
paths. A = physical integrity, B = reference integrity, C = schema/format contract,
D = domain semantics/provenance, E = sealed-content identity. Physical containment,
journal policy, transaction/lock lifetime and connection identity are retained
controls, not an additional whole-database audit class.

The C/T, U, O and L sections distinguish core/Task, numerical Usage, operational
review stores and lifecycle/Evidence/Viewer consumers. Shared definitions have
one implementation responsibility; separate rows show distinct call contexts,
particularly ordinary entry versus explicit acceptance. These are coverage rows,
not counts of defects, SQL statements or independent implementation changes.
Domain Review/Finding/Receipt rows use the separate R namespace.

## Responsibility And Cross-Owner Rules

- IV-S owns common structural admission, coherent schema/history sharing and
  removal of ordinary full A/B entry scans. It also owns the blanket cycle-count
  versus Evidence-generation check in common entry (C-07).
- IV-T owns ordinary Task/Handoff composition and selected-value reuse. IV-R owns
  Review and operational stores. IV-U owns numerical structural admission sharing.
  IV-V owns Viewer targeted guards. IV-L owns lifecycle audit composition.
  IV-P owns explicit protection; IV-X proves cross-consumer closure.
- Existing `tg_task_a5f10d38b4deac04` solely owns normal Evidence capture,
  publication and outcome changes (L-15 through L-18). Common entry and full
  lifecycle integration do not silently become additions to that Task.
- Existing `tg_task_469a9a022b76f9e7` solely owns changed-target Usage
  reconciliation, relation batching and current publication basis (U-03 through
  U-05). IV-U must not duplicate its attribution/invalidation algorithm.
- Preserve existing `tg_task_956dfaa96df08a57` normal Usage omission,
  `tg_task_7044a15b7de5aa3a` live-file/POSIX lock safety and
  `tg_task_0697e3afb197fac4` collected Receipt/Finding writer consistency.
  These are scope references, not completion assertions.
- Keep/share means the existing required guarantee remains. Replace/move identifies
  a retired call edge and its new bounded or audit responsibility; it never means
  deleting all uses of the shared validator. No family authorizes a persistent
  validation certificate, new daemon, public bypass or implicit repair.

The new-responsibility labels below are local documentation identifiers:
N-C01 connection controls; N-C02 structural/schema/history admission;
N-C03 explicit full physical/FK audit; N-C04 current project/binding/control
facts; N-T01 complete selected Task composition; N-T02 fresh locked mutation
prerequisites; N-T03 selected stored content; N-T04 immediate projection reuse.
They do not introduce runtime records or another Task registry.

## Core Admission And Ordinary Task Families

| Old family | Class | Concrete definitions and call edges | Existing guarantee, scope and read state | Disposition / new responsibility / unit |
|---|---|---|---|---|
| C-01 connection controls | Controls; B enforcement | `sqlite_connection.configure_connection`, `connect_existing`, `connect_readonly`, `validate_operational_journal_state`; `storage.connect_initialized`, `_connect_validated_readonly`, `begin_initialized_write`; `tasks.begin_task_write` | `foreign_keys=ON`, rollback-journal admission, no silent creation, query-only `BEGIN`, fresh `BEGIN IMMEDIATE`; controls are not whole-store semantic audits | **Keep**, N-C01 / IV-S. Do not remove FK enforcement, read transaction or writer lock as part of legacy-check retirement. Normal rollback-journal recovery is not an automatic full-audit trigger. |
| C-02 physical/FK ordinary entry | A/B | `state_resolver._inspect_database` at 1397, calls `storage._physical_integrity_ok(..., quick_check/foreign_key_check)` at 1461-1462 even with `operation_local=True` | Full DB physical/reference inspection in the resolver's query-only snapshot; called before normal Task repositories | **Move ordinary-path full checks** to N-C03 / IV-L and replace ordinary entry with N-C01/N-C02/N-C04 in IV-S. Keep physical/FK checks for explicit acceptance/audit consumers. No generic boolean exposed to callers to bypass integrity. |
| C-03 physical check implementation and lexical sharing | A/B | `storage._physical_integrity_ok`, `_reuse_read_integrity`, `_schema20_integrity_checks`; decorator on `state_resolver._inspect_database(doctor_only=True)` | Separate quick/full/FK results; query-only connection plus savepoint and write-count lifetime; direct SQL `PRAGMA <check>` | **Keep/share at audit owner**, N-C03 / IV-L. The helper is not deleted merely because normal entry stops calling it. Preserve distinctions between physical and FK failures and between quick and full checks. |
| C-04 schema history/version | C | `storage.current_schema_version`, `_validated_migration_versions`, `missing_migration_versions`; resolver `_inspect_database` then `_validate_current_schema_contract`; repeated capability reads in `tasks`, `task_ownership`, `contracts`, `task_show_projection`, `stored_task_validation` | Schema version, exact every-marker name/type/time/gaps, supported source; repeated in one read and again at separate writer entry | **Share only within established same read state**, N-C02 / IV-S; feed already observed version/capability into immediate Task consumers in IV-T. Do not turn `MAX(version)` into proof of valid marker history. Preserve independent initial/migration/restore acceptance and fresh writer boundary. |
| C-05 exact owned schema and attachments | C | `storage._validate_current_schema_contract` -> `_Schema33OwnedValidation` -> `schema_viewer_projection.validate_owned_contract` (v34), predecessor `schema_own_review_indexes.validate_owned_contract` (v33), Evidence schema helpers; `required_schema_objects_missing`, `newer_schema_markers_present`, `schema_objects_inconsistent_with_version` for older sources | Exact owned DDL fingerprint, required columns/FKs, forbidden attached/index/trigger/rebuild residue. `_Schema33OwnedValidation` currently exists only for one synchronous admission and checks connection, transaction state and schema cookie | **Share/replace duplicate composition**, N-C02 / IV-S, preserving required structural acceptance. Current one-call sharing is already implemented, so do not reimplement it as new work. Broader reuse requires an explicit lifetime and invalidation design, never process-wide mtime/size trust. Older migration/reentry validators remain independent. |
| C-06 current project identity/binding | C/D | `state_resolver._read_bound_project` / `_inspect_database`; `storage.validate_current_database_binding`, `_admit_current_database`, `_validate_current_task_read_database`; `project_binding_repository.read_project_binding_state`, `_read_project_binding_snapshot`, `_validate_target_binding` | One durable project identity plus current path hash/generation and binding relations; resolver value is compared again by current reader/writer | **Keep current identity and fresh effect checks; share repeated same-snapshot facts**, N-C04 / IV-S. No rebind or restoration inferred from a file stamp. Full binding lineage/Setup-specific policy remains owned by Setup and must be mapped there if changed. |
| C-07 common project control metadata | D | `storage._validate_current_project_rows` -> `read_project_maintenance`, `read_viewer_maintenance`, `read_evidence_projection_state`, project-wide `COUNT(*) FROM task_completion_cycles` (12236-12240); reached by both global and local current admission | Maintenance/projection metadata and source-generation equality against all cycles, even when an ordinary operation does not consume Evidence history | **Replace ordinary blanket composition with consumed controls**, N-C04 / IV-S. IV-S owns removal of this blanket equality check from common ordinary entry and retains its full-audit counterpart through IV-L. The existing Evidence publication Task owns its targeted publication-generation checks; it does not inherit the common-entry change. Viewer/backup control readers are separate domain owners. |
| C-08 admission wrappers and global-domain fanout | C/D | `storage.validate_current_database_structure`, `validate_current_database`, `_admit_current_database`, `_validate_current_schema_structure`, `connect_initialized[_readonly]`, `connect_initialized_task_readonly`, `begin_initialized_write`; `cli.context_read_connection`, `cli.handle_command`, `cli_handoff` | Global default invokes Runner/ownership/note/review validation; explicit operation-local paths already avoid unrelated histories. Reader and writer entry are independent states | **Replace shared routing/composition**, IV-S, then IV-T and other adapters adopt it. No blanket global-to-local replacement for Setup, doctor, recovery, Runner Plan actions or full consumers. Fresh writer Task/owner/generation checks remain N-T02. |
| T-01 complete selected Task row | D | `stored_task_validation.validate_stored_task_rows`, `_validate_stored_task_row`, `_validate_stored_completion_evidence`, `_validate_stored_review_target`, stored scalar/privacy helpers; `validate_current_stored_task_rows`, `fetch_validated_current_task_row` | Exact SQLite types, IDs, privacy/capacity, states and cross-field matrices on selected full rows; no normalization or partial result; own short read only when caller has no transaction | **Keep target validation / share results**, N-T01 / IV-T. A mutable Task row is not hash-substitutable. Its current fields remain freshly read after transaction or mutation boundary. Recovery's verification-only exception stays confined to its owner. |
| T-02 selected Contract-pointer composition | B/D | `stored_task_validation._validate_stored_contract_relationships`; `validate_stored_task_rows` -> one selected-ID query; `contracts.read_current_contract` / `contract_content._validate_stored_contract`; `_SelectedTaskFacts.contract` | All returned same-Task revision keys/types/ownership and exact latest pointer, then current Contract content; single ID-set SQL including wrong-storage-class aliases, no global Contract sweep | **Keep/share**, N-T01/N-T03 / IV-T. Existing shared current Contract row avoids second latest aggregate. Standalone current Contract reads retain their own pointer validation. Do not replace mutable pointer validation by immutable Contract hash. |
| T-03 selected ownership, mutation authorization and slot | B/D | `task_ownership.validate_basis`, `read_bases`, `_locked`, `_require_mutation_basis`, `capture_mutation_basis`, `_free_slot`, `transition`, `initialize_task`; `tasks.read_mutation_basis`, `read_task_write_state` | Selected Task/ownership/execution same-project relations; caller authority and exact generation; combined executing/review-pending session slot; writer serialization | **Keep/share only read facts**, N-T01/N-T02 / IV-T. Preserve fresh writer comparison, combined-slot predicate and SQL guards; no reuse of display ownership flags. Global `task_ownership.validate_storage_rows` is an audit consumer, not normal selected admission. |
| T-04 selected row/fact handoff and detail | D | `_validated_task_facts`, `_SelectedTaskFacts.require_current`; `task_show_projection.retain_validated_task_read`, `_check_retained_task_read`, `show_task`; `cli.read_task_detail` / `handle_task_context` | Same connection, actual transaction/savepoint, exact Task/project, write-count and scope lifetime. Context selected row and checkpoint can be reused only in retained read; marker-2 intentionally rereads after physical observation | **Keep existing sharing and extend only documented duplicates**, N-T04 / IV-T. Commit/rollback/connection replacement/mutation invalidates sharing. Normal Usage access removal remains existing Task 956d, not part of this change. |
| T-05 list/current/next selection and projection | D | `tasks.list_tasks`, `list_current_tasks`; `selection.select_next_tasks`, `count_next_tasks`, `next_task_readiness_sql`; `task_ownership.project_tasks`; `ordering.lane_ordering_cte` | Validate selected complete rows before filtering/projection; lane readiness and exact count are actual selection work, not unrelated semantic audits | **Keep selection predicates; share selected validation**, N-T01/N-T04 / IV-T. Concrete remaining duplicate: `select_next_tasks` 149-154 validates ownership through `validate_current_stored_task_rows` then `project_tasks` rereads ownership without `_validated`; list/current already pass validated facts. Remove only that redundant read. |
| T-06 ordinary mutation and completion freshness | B/D | `tasks._add_prepared_task`, `edit_task`, `reopen_done_task`, `capture_completion_basis`, `validate_completion_plan_basis`, `validate_proposed_done_ordering`, `validate_done_transition_inputs`; `contracts._activate_revision_zero`, `_later_revision`, `edit_contract`; `ordering` guards | Ingress then external preflight, fresh locked Task/Contract/owner/status/order/target basis, atomic events and rollback; completion semantic token and owner generation compared before sealing | **Keep N-T02**, IV-T for ordinary composition, IV-R/the existing Evidence publication Task for domain gate and seal internals. No cross-writer reuse of preflight. `done` immutability, reopen cycle and same-lane successor/predecessor checks survive. |
| T-07 Contract/checkpoint/handoff stored content | B/D | `contract_content._validate_stored_contract`; `checkpoints._row_to_checkpoint`, `record_checkpoint`, `read_latest_checkpoint`; `handoffs._validate_stored_handoff`, `record_handoff`, `list_handoffs`, `show_handoff`, `withdraw_handoff`, `handoff_summary_for_task`; `cli_handoff` writers | Target content/privacy, exact Contract revision, idempotent replay, Handoff state matrix and caller permission in fresh writer; count and rows in one snapshot | **Keep/share**, N-T03 / IV-T. Checkpoint/Handoff record already use `read_task_write_state` shared facts, withdraw already captures owner once. Replay cannot bypass selected validation. No new evidence hash required for these mutable/current relationships. |
| T-08 notes/events and Effort | D | `tasks.row_to_event`, `validated_event_note`, `fetch_task_event_rows`, `create_task_event`; `effort.build_effort_advisory`, `_fresh_activity_generations`, transition activity helpers | Consumed event/note privacy and operation fields; Effort selected Task and fresh activity-generation checks. Global retained-note validation exists in storage's global branch | **Keep targeted content/current-generation checks**, N-T03 / IV-T; retain global audit only in IV-L. Activity scans required to compute the advisory are not automatically removable integrity checks. Usage Hook/evidence algorithms remain IV-U and the existing Usage reconciliation Task. |

### Core And Task Entry Coverage

T-09 (B/C/D; IV-T) covers `verification_runner_plan_edit._read_task_preflight`,
`_edit_without_action`, `_edit_plan_only`, `_edit_combined` and `_edit_on_connection`.
Ordinary edits already choose operation-local admission. Config-only and combined
Plan actions still enter global-default connection/writer admission. Replace those
edges with complete selected Task/Contract/owner/criterion validation in the fresh
writer; preflight is never writer authority. Preserve pure prospective Plan
construction, rollback, writer closure before physical publication and the
committed-Task/unconfirmed-Plan outcome. Preserve bounded publisher path/file
and byte-identity checks. No new reservation or long filesystem-spanning writer.
Extend `tests/test_m242_runner_plan_edit.py` and ordinary admission tests: selected
mutation races reject, unrelated retained-note growth/corruption is outside that
operation, and both writer paths remain targeted. Reconcile its old
`test_combined_plan_action_revalidates_global_state_after_capture` premise with
the new scope; retain the race test using consumed corrupt fields.

Normal public leaves covered through adapters are Task
add/list/current/next/context/show/effort/checkpoint/edit/complete and Handoff
record/list/show/withdraw. Task edit includes Contract-only, state, compatibility
done and reopen branches. Explicit Runner Plan actions retain current admission
until T-09's targeted replacement; an ambiguous default must not switch them
accidentally.

C-03 identifies the shared physical-check implementation and its lexical reuse
mechanism. L-12 identifies its lifecycle consumers. It is one shared helper,
with ordinary retirement covered by C-02 and retained audit use covered by L-12.
Similarly, C-05 owns structural composition while L-13/L-14 retain supported
migration acceptance; C-08's domain fanout is implemented by the assigned domain
units rather than by a second generic validator.

Direct SQL categories examined include physical quick/full/FK PRAGMAs,
enforcement/control `foreign_keys`, `query_only`, `schema_version`,
`table_info`/`table_xinfo` and `sqlite_master`, migration-history SELECTs,
selected Contract/ownership relations, projection/cycle-count consistency and
fresh lane/session predicates. `PRAGMA` is not itself a deletion category.
Global ownership/note/Runner/Review/Evidence callees inside common admission
are explicitly domain/audit edges, not unclassified ordinary Task validators.

### Core And Task Regression Anchors

- `tests/test_schema_admission_reuse.py`: one owned fingerprint per current
  admission, standalone helpers independent, writer fresh after preflight, failed
  and successful result expiration, connection/DDL drift and source reentry.
- `tests/test_migration_history_validation.py`: every-marker names/times/types,
  gaps and supported source acceptance, Setup/doctor/copy/recovery routes. Keep
  exact acceptance while changing only where repeated history reads occur.
- `tests/test_operation_admission.py`: public Task and Handoff leaves avoid
  unrelated semantic history; consumed corruption rejects; writer ownership
  drift; list/current/checkpoint/handoff ownership read once; selected-facts
  rejection on another Task/connection/write/transaction/closed scope.
- `tests/test_m214c_stored_task_validation.py` and
  `tests/test_m214d_contract_pointer_validation.py`: raw storage-type/privacy/
  cross-field matrix and selected-batch relation queries; structural recovery
  errors remain set-fatal.
- `tests/test_m214d_snapshot_boundaries.py` and `tests/test_task_context.py`:
  another connection's Contract commit cannot mix a row and its relationship;
  retained Task/checkpoint identity and marker-2 transaction replacement.
- `tests/test_task_ownership.py`, `tests/test_sequential_transitions.py`,
  `tests/test_selection.py`: stale owner, two concurrent starts, combined slot,
  rollback, predecessor and reopen ordering, malformed selection rows.
- `tests/test_task_contracts.py`, `tests/test_checkpoints.py`,
  `tests/test_handoffs.py`: immutable Contract revisions, source/current pointer,
  privacy, replay/no-write, concurrent writes and exact current-owner checks.

Add isolated fixtures that compare each affected public leaf at small and large
unrelated-Task/history sizes, counting both statements and inspected rows (or
VM work) rather than merely asserting helper names. Ordinary leaves must issue
no full-DB physical/FK audit after the conditional switch, while explicit
acceptance/audit still detects its scoped full-store faults. Selected corruption
must still fail before output or write. Cover next-selection ownership sharing
without altering count, limit, lane or caller semantics. Restore/DDL/schema/
transaction replacement must invalidate the new result lifetime. Preserve fresh
writer gates after preflight, not just the read-only happy path.

## Review Domain And Completion Families

These core-domain families are owned by IV-R. They share C-04/C-05's structural
basis but do not share mutable authorization across a write or host/Git effect.
O rows below cover the separate operational journals; L-15 through L-19 cover
Evidence publication and retained sealing responsibilities.

| ID | Concrete family and consumers | Class / scope | Disposition, boundary and regression |
|---|---|---|---|
| R-01 | `storage._validate_review_storage_rows`, `_validate_collected_review_storage_rows`, `validate_task_review_storage`; selected detail, Packet, registration and completion admission | B/D, selected Task's Receipt/session/collected-source/judgment closure versus explicit global audit | **Keep selected closure/share its result**, IV-R. Batch same-read relationships; global orphan inspection remains audit-only. Do not prefilter a foreign row that aliases a selected Receipt on either identity arm. Preserve `tests/test_operation_admission.py` own-review/cross-owner cases. |
| R-02 | `review_repository._iter_validated_review_receipts_with_provenance`, `_validate_review_receipt_base_row`, `_validate_review_finding_base_row`, `read_review_receipt_with_provenance`; `review_session_repository.read_bindings` | B/D/E, exact selected Receipt IDs, provenance versions, original code ordering, actual reviewer identity and Finding fields | **Keep/share selected rows**, IV-R. The iterator already streams selected IDs and batches legacy provenance; retain that rather than replacing it with per-ID queries. Standalone reads admit themselves. Global no-ID calls retain their explicit audit use. Missing originals, wrong storage types/privacy and extra/unrelated provenance cannot become a successful summary. |
| R-03 | `review_answer_repository.validate_provenance_relation`, `validate_payload_findings`, `validate_storage_rows`, `read_registration`; collected Receipt registration and selected admission | B/D/E, sealed answer/payload digest, source/preparation/manifest/Receipt/Finding and dispatch-entry relations | **Share repeated original parsing within one admitted state**, IV-R; retain both identity arms, selected dispatch parents and full Finding payload consistency. A hash does not establish current target/lease/owner eligibility. Existing `tg_task_0697e3afb197fac4` owns the post-Finding-write consistency repair; do not duplicate it or reuse pre-write mutable results. Regression: collected-answer registration, original payload and dispatch corruption tests. |
| R-04 | `review_material_repository.ReviewMaterials`, `review_materials`, `FindingBatch`, `finding_batch`; `evidence_repository._validated_authority_context`, `_validate_artifact_manifest_storage`, `validate_manifest_evidence_references` | D/E, borrowed immutable authority/manifest originals; fresh mutable Reference/Task/Receipt/Finding graph | **Keep existing lexical borrowing**, IV-R. Require same connection, project/Task, schema and actual transaction epoch. Only missing immutable material is fetched. Keep final batch mutable-graph admission after writes. No successful-prefix result escapes to another batch. Extend `tests/test_review_material_reuse.py`, including restart/DDL/local-write/final-boundary cases. |
| R-05 | `review_continuation_repository.validated_continuation_history`, `read_continuations`, `read_judgments`, `validate_judgment_storage`; `review_continuation_evidence_repository.effective_review_snapshot`, `effective_for_task`, `freeze_basis_locked`, `completion_selection_locked` | B/D/E, same Task's continuation ancestry, retained originals, effective heads, explicit resolutions and frozen completion graph | **Keep/share**, IV-R. Existing guarded effective snapshot is reused; do not compute it repeatedly in the same unchanged state or replace it with latest-PASS selection. Complete own ancestry and resolved/retained Findings stay in report/seal scope. Preserve transaction/write-count invalidation and fresh locked freeze. Regression: continuation snapshot/registration/finalization suites and independent continuation Evidence oracle. |
| R-06 | `review_wait_basis_repository.read_wait_basis`, `read_review_basis`, `read_preparation_basis`, `validate_historical_wait`; review/wait/preparation/collector/finalization callers | B/D, current Task/Contract/owner/manifest/Reference and requested old occurrence relation | **Share duplicate reads inside one basis capture**, IV-R. `read_review_basis` rereads the Task after `read_wait_basis`; an admitted composite can return the already validated row. Preserve query-only transaction, current owner/generation, exact Contract/target and both current/old native relationship checks. A historical wait is never current eligibility. Each external-effect boundary rechecks; do not alter monitoring cadence or replay unknown effects. Regression: wait basis, tracking admission, continuation transition and managed wait tests. |
| R-07 | `evidence_validation_repository.validate_selected_task_receipt_evidence`, `_validate_selected_task_evidence_reference_inventory`, `_validate_selected_completion_cycle_evidence`, `_validate_selected_completion_bundle_history`, `read_native_completion_bundle_basis_locked`; `completion_workflow` and verification/Review gates | B/D/E, selected verification/Review/Reference/completion-cycle closure and immutable Bundle seal | **Keep seal and current applicability; share admitted selected inputs**, IV-R. Historical cycles never satisfy current gates; current target/Contract/execution and blocking Findings remain fresh. Preserve independent reader verification, complete relation checks and post-insert exact saved result. Whole-store projection reconstruction stays with existing Evidence work (L-15–18), not this family. Regression: Verification Receipts, completion-cycle/history and independent Evidence tests. |

Review Packet and report construction are consumers of these admitted facts,
not new authorities. Registration identity/idempotence and report coverage are
different sets: sharing must retain all Findings required by the final report,
including retained and reassessed ancestry. JSON parsing/protocol and input
privacy still apply at ingress; they are not database-wide audits to retire.
Tests must compare read-only detail/Packet, both registration protocols,
continuation and both completion routes on the same selected corrupted/valid
fixtures, with unrelated Task growth measured separately. External host effects
remain mocked in ordinary regression tests; this initiative adds no host trust.

### Runner Execution Consumers

- R-08 (B/D/E; IV-R): `verification_runner_selection.select_current_verification_runner_basis`
  reads selected graph, closes SQLite for physical observation, then rereads and
  compares a storage token. Keep these two different states; share facts only
  within either state. Preserve Plan/package/policy/material and exact successor
  commit matching; done/marker-zero is not current Runner evidence.
- R-09 (B/D/E; IV-R): `verification_runner_service._persist_ordinary_target`,
  `_persist_launch_intent`, `_persist_terminal`, `_persist_cleanup_only`,
  `_current_basis_matches` and `set_review_target_with_optional_runner` use
  operation-local entry, but `verification_runner_repository` still calls
  `_validated_verification_runner_references` globally around T1/T2/cleanup.
  Replace that fanout with the exact selected/newly sealed graph. Keep atomic
  target/intent and observation/Reference/criterion/cleanup linkage, current
  applicability, process-zero/handles-closed/output-discarded/exact-tree proof,
  and unknown-result fences. An owner change after admitted T1 must not prevent
  mandatory cleanup/audit; no restart relaunch or invented result is permitted.
- R-10 (B/D; IV-R): `_deny_unresolved_attempt` and
  `read_pending_verification_runner_cleanup` currently validate all Runner graphs
  before project-wide pending selection. Keep the actual project-wide pending
  set needed by the fixed private inventory and single Runner lock, but retire
  unrelated completed-history validation. Define candidate/anti-join/cardinality
  closure so dangling attempts, foreign/type aliases, missing or conflicting
  terminal/cleanup relations and duplicate owners cannot disappear from the
  absence proof. Full graph audit remains IV-L's explicit lifecycle consumer.

Runner regression anchors are `tests/test_m243c_runner_gate.py`,
`tests/test_m242_runner_service.py`, `tests/test_task_ownership_runner.py` and
repository graph/cardinality tests. Cover selected corruption and unrelated
terminal growth, lock-before-T1, atomic rollback, false cleanup proof, cleanup-only
restart, T2 basis race and absence-proof corruption. The OS lock spans effects;
SQLite transactions do not. These are current Runner consumers of the common
placement contract, not an additional Runner feature or a separate execution unit.

## Numerical Usage Families

| ID | Current call family and placement | Class / scope | Disposition and destination |
|---|---|---|---|
| U-01 | `usage_repository.py:_schema` (75), `_validate` (130), `connection` (139). Every numerical connection compares all expected DDL, all migration rows and singleton project/binding metadata. Current schema 9 inherits this admission through attribution/evidence/wait/incremental/response/publication/locator/work-order repositories. | C plus current identity binding. DDL/history scope is schema objects/versions, not all Tasks. | **Share** structural admission only within the same open admitted snapshot; **owner IV-U**; IV-U, consuming IV-S's design, should separate structural proof from current project/binding checks. A fresh connection must still admit its actual store. No file-stat or process-global validity cache. Preserve exact prior-schema diagnosis and Setup-only migration. |
| U-02 | `UsageRepository.initialize` (162); `UsageAttributionRepository.initialize` (109); evidence/wait/incremental `initialize` predecessors. Six direct code sites issue paired `quick_check` and `foreign_key_check`: base 170–171; attribution current 114–115 and predecessor 125–126; evidence 107–108; wait 144–145; incremental 122–123. | A/B, explicit Setup/current-store acceptance and migration predecessor checks. These are **not** ordinary Hook/connection queries. | **Keep / share same-state work**, **IV-L**, with numerical admission integration from IV-U. Preserve actual initial acceptance, supported predecessor and resulting-schema acceptance, atomic marker-last migration, rollback, private-file creation and no-replace publication. Remove a repeated scan only when the same snapshot and same required guarantee are demonstrated; quick_check and full integrity_check are not interchangeable labels. |
| U-03 | `usage_lifecycle.py` admitted core read then `validate_usage_ownership` (130–131); `usage_locator_collection.py` (31–32); `usage_evidence_service.py` publication/refresh reads (37,113). `usage_attribution_repository.core_transitions`, `registered_participants`, `resolve_operation`; `usage_evidence_repository.core_basis`; `usage_response_repository._relation_inputs`, `_load_relations`, `_collected_review_endpoints`, `_core_basis`, `attribution`. | B/D. Core-wide owner/transition history, participation, events and exact response correspondence; repeated full basis and per-operation lookups. | **Replace** repeated full-history reconciliation with changed targets, unresolved work and dependency closure under existing `tg_task_469a9a022b76f9e7`. Batch exact ID reads and share a single admitted relation input within one capture. Missing/restored-away operation references remain unbound/unknown. No stale active owner inferred from absence of new numerical counters. |
| U-04 | `usage_evidence_repository._snapshot_read`, `_snapshot`, `_responses`, `_refresh`, `_capture_projection`, `_overlapping_snapshots`, `_adopt_projection`, `_read`; response overrides `_capture_projection`, `_project_for_refresh`, `_adopt_projection`, `read`, `_totals`. `usage_snapshot_read.py` supplies transaction-bound validated snapshots/membership. | B/D/E. Immutable snapshot document/membership/original counters and supersession/cycle links, plus mutable current eligibility. | **Keep seal/selected-object validation; share and replace global metadata selection work** under existing `tg_task_469a9a022b76f9e7`. Do not treat a snapshot hash as proof that current core references still exist or that classification is current. Preserve original response-set de-duplication, shared partition overlap, old snapshots, supersession and completion-cycle links. Reuse only in one admitted read; next capture/connection checks current basis again. |
| U-05 | `usage_publication_repository.prepare_publication`, `_index_metadata`, `_publication_state`, `publication_snapshot`, `advance_publication`, `publication_guard`; service-level fresh guard in `usage_evidence_service`. | D/E; rotating audit progress, snapshot/index bytes, mutable core/numerical publication basis and CAS. | **Keep** fresh publication decision and bounded historical audit; **replace** full basis rereading with complete change tracking under existing `tg_task_469a9a022b76f9e7`. Preserve stale-CAS rejection, restoration invalidation, interrupted progress, last-good output and no mutation on corrupt cursor. An unchanged cursor, size, mtime or inode is not a whole-store certificate. |
| U-06 | `usage_repository._physical`; `usage_locator_repository._validate_pending_binding`, `remember_locator`, `locator_page`, `begin_locator_attempt`, `_promote_locator`, `discard_pending_locator`, `known_sources`, `registered_sessions`; `usage_locator_admission._schema`, `_native`, `_current_round`, `_assignment`, `_registered`, `classify_pending`, `observe_pending`. | Physical identity controls, C, B/D for exact active collection assignment/Receipt, locator/source incarnation and queue CAS. | **Keep** fresh path/header/binding/session and exact registration checks. A saved locator is a hint, not eligibility proof. Share duplicate structural admission in one read through IV-U/IV-R where applicable; changed participant reconciliation remains `tg_task_469a9a022b76f9e7`. Raw SQLite header-reader lock correctness is existing `tg_task_7044a15b7de5aa3a`, not a new performance bypass. |
| U-07 | Usage cursor/context/source/response decoders, closed values and exact core operation lookup; independent Usage connection/Setup boundaries. | Input validation and target D; not whole-core audits. | **Keep**, owner IV-U: source-generation changes, replacement/truncation, deferred audits, unknown optional counters and typed state. Missing/busy/corrupt Usage must remain Usage-unavailable and must not block normal core completion. Do not merge numerical and core databases or imply that a core backup is a paired Usage snapshot. |

### Numerical Regression Anchors

Existing tests to retain and extend, rather than replacing their guarantees:

- `tests/test_usage_collection.py`: binding/corruption refusal; corrupt cursor; atomic initial schema failure; physical source replacement.
- `tests/test_usage_incremental.py`: all prior explicit migrations; unchanged/small append avoids reparsing; fixed audit goals detect preserved mtime; unchanged component reuse and merged union.
- `tests/test_usage_locator_repository.py`, `test_usage_work_order.py`: populated predecessor preservation, atomic migrations, corrupt path/sequence rejection and fair work-order behavior.
- `tests/test_usage_relation_inputs.py`: each relation loaded once per capture; standalone and fresh guard read again; restored-away core; cross-connection/closed-transaction input reuse rejected.
- `tests/test_usage_snapshot_read.py`: only selected originals loaded, next connection rechecks, corrupt metadata cannot hide an original.
- `tests/test_usage_publication.py`: one capture classification/current validation, bounded audit continuation, unchanged timestamps do not hide mutation, stale basis/CAS, fresh-guard source/core changes, corrupt history and original counters, deadline and last-good behavior.
- `tests/test_usage_response_repository.py`, `test_usage_wait_repository.py`, `test_usage_collected_review.py`: missing restored core event/manifest remains unknown; exact endpoint relation inputs; standalone/final guard fresh reads.
- `tests/test_usage_setup.py` and `test_usage_evidence_install.py`: corrupt/unavailable Usage does not block core admission or either completion route. Normal-output removal is already owned by `tg_task_956dfaa96df08a57` and its changed tests.

New performance acceptance belongs to the existing Usage reconciliation Task: unrelated Tasks/history increase while changed work stays fixed must not increase repeated full reconciliation; delayed operations/Receipts/source diagnostics and partition old/new closures still match the full-recompute oracle. The criterion is both query/work scope and result equivalence, not one elapsed-time threshold.

## Operational Review And Waiting Families

These are bounded resolver-owned stores distinct from core and numerical Usage.
Their local redundancy is not a core-wide audit. Direct quick/full/FK PRAGMAs
were not found in `review_wait_runtime/*repository.py`; the Preparation store
outside that directory has the FK scan in O-05. O-01's physical identity/journal
guards are controls and must not be removed with that scan.

| ID | Current call family and placement | Class / scope | Disposition and destination |
|---|---|---|---|
| O-01 | `review_wait_runtime/review_wait_repository.py:_inspect` (236), `_connection` (261), `_lease` and lease checks. Shared by wait/direct/request/transition/preparation/collection/finalization stores. Physical path, bounded database and lock file, journal/WAL restrictions, before/after identity; read BEGIN/query-only, write BEGIN IMMEDIATE; distinct observation busy behavior. | Boundary controls plus transaction consistency, not A database scan. | **Keep**, IV-R: physical admission, short transactions, writer lease/effect separation and fresh identity checks. Existing `tg_task_7044a15b7de5aa3a` owns unsafe raw-header reads on POSIX. Never remove locking as an incidental consequence of removing integrity scans; never leave a SQLite transaction open across a host effect. |
| O-02 | `ReviewWaitRepository._load` (293), `DirectRepository._load` (268), `CollectionRepository._load` (133), `PreparationRepository._load` (125), `FinalizationRepository._load` (213), `RequestRepository._load` (202), `TransitionRepository._load` (46), finalization continuation `_parts` (60). Each compares exact sqlite_schema objects, user_version and schema_history. | C, small per-store object inventory. | **Share** once per admitted snapshot in IV-R; keep exact store-kind/version admission and unexpected object rejection. No cross-store/schema-kind cache, no proof surviving closure, schema change, restart or file replacement. If a caller performs only one load, there may be nothing to remove. |
| O-03 | Wait controller `_decode`/`_encode`/`_forward`; `DirectRecord.__post_init__`, update and `confirm_worker_stopped`; collection `_decode`, `_inventory`, `_forward`. | D and bounded values. Single current controller/direct record, or 1–8 collection slots with one basis and unique actual reviewers/sessions. | **Keep**, IV-R: current record validation and monotonic effect transitions. All slots are the relevant collection closure for cross-slot uniqueness; blindly selecting one row would weaken correctness. Preserve first accepted original, unresolved send/save/registration intents, separate send/receipt/worker-stop evidence and no replay after unknown outcomes. |
| O-04 | `RequestRepository._load` loads at most 64 attempts and continuation rounds, validates exact occurrence/history/current pointer. `read_rounds` (298) calls `_load` and then calls `load_rounds` again; installation/advance paths also reread rounds in the same transaction. `TransitionRepository` checks its current round and predecessor chain before fence/bind/advance. | B/D, one original Task/wait and its ordered continuation/effect closure. | **Share**, IV-R: admitted attempt/round inventory within one snapshot; `read_rounds` duplicate is a concrete removal candidate. Keep current occurrence, reviewer/turn binding, expected sequence, predecessor settlement and unknown-effect fences. Migration from v1 must still validate admitted predecessor history before conversion. A new boundary uses a fresh read. |
| O-05 | `review_preparation_repository.PreparationRepository._load` (125–142): exact schema, **PRAGMA foreign_key_check (132)**, all preparation payload decoding, same-root set and singleton current-pointer membership. Read/publish callers invoke this on the per-target operational store. | B/D. FK scan plus every prior preparation for the same target; not all project Tasks, but grows with retained preparations. | **Replace** ordinary full FK scan/history decode with current pointer, explicitly requested preparation IDs and their necessary root/source dependency closure in the IV-R. Validate complete newly accepted preparation/root at publication; preserve immutable previous bytes, exact issued collection bases, current-pointer referential integrity and selected legacy source validation. Keep full acceptance at explicit conversion/repair/diagnosis as required. Do not quietly change a public full-history reader into current-only: split current/admission and explicit inventory routes and update their callers/tests together. |
| O-06 | `review_finalization_repository` v1 singleton and v2 `review_finalization_continuation_repository._parts`, `records`, `load`, `append`, `save_view`. v2 checks fixed effect root and at most 65 round records, exact sequence/current pointer, preparation/root/branch equality and replaced dispatch relation. | B/D. One finalization's related effect history. | **Share**, IV-R: one parsed Parts value inside a transaction; target that root and required predecessor closure if adding a narrow read. Preserve unknown registration/commit/completion fences, immutable candidate and branch root, old successful receipts and exact original bytes. `records` remains an explicit history consumer. No automatic conversion during read and no old PASS promoted to current review by checksum. |

### Operational Regression Anchors

- `tests/test_review_wait_repository.py`: exact schema/history admission, changed schema/history refusal, lease ownership and nonphysical journal checks.
- `tests/test_review_wait_observation_repository.py`: query-only/no writer lease, only native SQLITE_BUSY is deferred, closure on failure, overlapping observations allow exactly-once send/cancel/receipt writes, prolonged contention rolls back without replay; malformed state is not busy.
- `tests/test_review_wait_request_continuation.py`: explicit settled conversion keeps exact v1 bytes, current round/occurrence validation, changed reviewer pairs rejected, pointer corruption, conversion rollback, distinct preparation roots.
- `tests/test_review_wait_continuation_transition.py`: fence idempotence, unknown prior round blocks publication, corrupt prior round cannot hide behind latest row, exact packet intent retained, capacity/rollback.
- `tests/test_review_preparation_repository.py`: dangling current pointer, unknown schema/noncanonical record, changed root/conflicting identity, immutable SQL guards, failed admission/publish rollback, exact integrated/manual identity.
- `tests/test_review_finalization_continuation_repository.py`: explicit conversion, retained prior successful registration, root commit/completion exactly once; couple with finalization service tests rather than changing repository validation alone.
- `tests/test_review_answer_collection.py`, `test_review_answer_tracking_collection.py`: unique actual reviewer across slots, partial selected source, concurrent first acceptance and conflicting original behavior.

New regression acceptance: a normal current-preparation read must not decode unrelated earlier preparations; explicit old-source/history reads still validate their requested records. Same-snapshot duplicated schema/round decoding runs once, but writes or a new connection invalidate the result. Selected root, pointer, sequence and required predecessor corruption still fail; unrelated history corruption is detected on explicit history/acceptance audit rather than making every current-target operation scan all retained objects. Keep observation/workers' wait scheduling, send behavior and locking unchanged.

## Lifecycle, Evidence And Viewer Families

L-01 through L-14 cover explicit lifecycle acceptance and its shared primitives;
L-15 through L-19 cover Evidence boundaries; L-20 through L-25 cover Viewer.
Their different contexts do not duplicate ownership of the common C helpers.

| ID | Existing definition and call family | Class / current purpose | Disposition and exact retained boundary | Implementation owner / regression |
|---|---|---|---|---|
| L-01 | `state_resolver._inspect_database` (~1397); `resolve_setup_project_state`, `_classify_recovery_backups`; `_physical_integrity_ok` calls at ~1461 | A/B/C/D; exact source-schema, identity and recovery eligibility | Keep full checks for explicit diagnosis, newly adopted/restored source and uncached needed backup. Do not promote operation-local resolution to this global mode. Matching immutable backup proof may bypass only its certified audit; physical/schema/binding/current relationships remain fresh. | IV-L + IV-S. `test_setup_recovery`, `test_doctor`, `test_backup_validation_reuse` |
| L-02 | `setup.run_setup` / setup inspection and `SetupPlan`; `backup=migrate` (~551) | C/D and phase-specific A/B; setup initialization/migration/recovery | Keep phase distinctions. Healthy-primary Setup must not open/hash unrelated old backup bodies. Configuration-only Setup is not a backup trigger. A source/copy/normalized result are different states, not interchangeable cached admissions. | IV-L. Setup/legacy recovery matrices and no-write preview |
| L-03 | `doctor` through coherent project read and exact-schema Task batch validation | A/B/C/D explicit global diagnosis | Keep complete explicitly requested diagnosis. Share exact validated Task rows within that read only. Return results; no diagnostic ledger write, artifact creation, automatic repair or prerequisite normal-loop call. | IV-L. `test_doctor`; assert no writes even for invalid DB |
| L-04 | `backup._validate_database` (~329), `_validate_publication_source` (~366), `_recovery_content_valid` (~435) | A/B/C/D; schema-specific backup/source content admission | Keep these full-audit responsibilities at lifecycle boundaries. Share admitted rows/relations only within one unchanged read and exact scope. Do not remove version-specific or privacy checks just because a generic `quick_check` passes. | IV-L. `test_backup_validation`, old-schema and recovery tests |
| L-05 | `async_backup_repository.control_snapshot`, `read_control`, `copy_snapshot` (~207), `commit_inventory`, `record_failure` | C/D local live control; copy/metadata effects | Retain current narrow live-source behavior. Copy closes its initial source before heavy validation. Inventory CAS, DB-file identity, binding, policy/generation and stale failure/success comparisons remain fresh. No new live whole-history audit. | Preserve existing async design; IV-L compatibility verification, not a new backup architecture |
| L-06 | `async_backup_repository.validate_snapshot` (~224), async capture publication | A/B/C/D on the independent consistent copy | Keep complete private-copy audit after live connection closes. Publication requires validated final bytes; later live Task updates do not invalidate the earlier captured copy or require an unlimited catch-up loop. | Preserve; IV-P may reuse primitives. `test_async_backup_capture`, repository/integration/process tests |
| L-07 | `backup_validation.artifact_snapshot`, `inspect_artifact` (~164/204); resolver proof-aware candidate reads | E plus fresh C/D metadata; optional full A/B/C/D fallback | Keep existing immutable validation reuse. Successful rule/scope/project/generation/schema/hash match skips only certified internal audit. Missing/obsolete proof audits the needed artifact; malformed/mismatching proof cannot be enrolled as good. Explicit audit ignores the reuse shortcut. | Preserve, not “future optional” retirement. `test_backup_validation_reuse` |
| L-08 | `backup_validation.prepare_copy`, `require_prepared_copy`, `publish`, `require_published_record` (~217+) | A/B/C/D then E on final closed bytes | Keep independent copy inspection, bracketing fingerprints and final publication continuity. Never assume SQLite-copy output bytes equal source bytes. Descriptor closure order must preserve POSIX SQLite locks. | Preserve + Existing `tg_task_7044a15b7de5aa3a` boundary; IV-P reuses this assurance model |
| L-09 | synchronous `backup._copy` (~1527), `publish_setup_backup`, private-stage `copy_database_snapshot` | A/B/C/D/E over initial source, private copy and fresh publication source | Keep migration protection. Source closes immediately after backup API; fresh publication guard is a new read state. Remove only duplicated same-phase same-scope traversal, not source/copy/fresh distinctions. No long metadata writer around content audits/hashing. | IV-L. `test_backup_validation`, Setup copy/lock/interruption fixtures |
| L-10 | `state_resolver._validate_primary_backup_inventory`, `_validate_candidate_repository_snapshots`, `_validate_backup_set`; `backup._discover_recovery_artifacts`, `_require_recovery_inventory`, reconciliation/pruning | C/D set and generation relationships; E content continuity when actually needed | Keep bounded inventory, unique generation IDs, lineage, pointer/retention and surviving last-good checks. Reuse one typed immutable observation inside the same phase; refresh at actual destructive/publication boundaries. No broad old-backup reinspection on healthy-primary Setup. | IV-L; bounded retention/set-fatal versus candidate-local rejection tests |
| L-11 | `backup.restore_managed_backup` (~927), `_prepare_recovered_repository` (~796), `_normalized_recovery_observation` (~887); `viewer_projection_repository.reset_restored_database` | A/B/C/D/E source adoption and transformed private output | Keep source identity/content comparison, private repository normalization, epoch rotation and output revalidation before no-clobber publication. A normalized copy does not inherit the original content proof. Never overwrite an existing unreadable live DB. | IV-L + IV-P's isolated restore adapter; `test_setup_recovery`, recovery-boundary/Viewer restore tests |
| L-12 | `storage._physical_integrity_ok`, `_schema20_integrity_checks` (~4480/4519); exact schema `validate_storage` / migration callers | A/B, plus caller C/D | Keep at required initial/migration/recovery/audit states. `quick_check`, `integrity_check`, and FK checks have different guarantees; do not equate names or eliminate a check merely by raw call count. Same-state duplicate guarantee is shareable once its caller obligation is explicit. | IV-L for lifecycle call disposition; IV-S removes normal-path calls |
| L-13 | `schema_preservation.capture/assert_preserved`; storage `_selected_table_projection_snapshot`, `_migration_preservation_snapshot`; schema migration modules | D/E preservation of old column values, row identity, objects, sequence and migration markers | Keep before-delta baseline, after-delta and after-final-marker guarantees. Those are distinct database states. No “three reads become two” rule. Reuse only within an unchanged state; streaming capture is a separate implementation choice, not required for this design. | IV-L. `test_review_migration_preservation`, stage failure/rollback and reentry tests |
| L-14 | `storage._initialize_database_with_identity`, `apply_migrations`, legacy `apply_*_migration` paths | A/B/C/D; explicit initial/upgrade admission and final state | Keep exact supported version/history and final physical/reference acceptance. Do not interpret normal-operation narrowing as permission to initialize/migrate/repair implicitly. Each committed transition retains its own acceptance; chain-final validity does not excuse an invalid earlier committed stage. | IV-L; first setup, every supported source, interruption/reentry |
| L-15 | `evidence_publication._capture` (~88) -> `storage.capture_current_evidence_projection` (~12306) -> `evidence_validation_repository._capture_evidence_projection` / source-basis validators | C/D/E; currently full admission, ledger/Bundle relation replay and all Bundle reconstruction | Replace normal capture with new/unpublished Bundle plus required references. No DB-wide audit and no reconstruction of already published immutable Bundles. Thin complete index metadata is allowed. New full baseline/repair is an explicit separate role. | Existing `tg_task_a5f10d38b4deac04` only; `test_evidence_projection_capture`, publication/integration tests |
| L-16 | `evidence_publication._generation_guard` (~371) / `_replace_index` | C/D currently global connection admission before index replacement | Replace global admission with exact project/binding/settings/captured-generation checks in fresh read state. Keep reader/atomic replacement and stale-generation rejection. Generation equality is not a blanket content-integrity proof. | Existing `tg_task_a5f10d38b4deac04` only |
| L-17 | `evidence_projection_metadata_repository.record_evidence_projection_outcome` (~325) -> default `connect_initialized` and `begin_initialized_write` | C/D currently two global admissions for narrow result write | Replace with operation-scoped structural and projection/policy row validation and conditional update. Keep monotonic published generation, unknown write outcomes and last-success semantics. | Existing `tg_task_a5f10d38b4deac04` only |
| L-18 | Evidence force/physical-status/Setup rebuild and independent source validators | A/B/C/D/E all Evidence required by explicit repair/baseline | Keep explicit audit/rebuild scope, including first publication, restored/replaced DB, schema/format changes. Rebaseline does not silently import JSON into core DB or change historical Bundle bytes. A broad audit is not the automatic fallback for every normal metadata mismatch. | Existing `tg_task_a5f10d38b4deac04` owns its baseline; IV-L owns recovery boundary integration |
| L-19 | native completion's selected-cycle / selected Bundle-history validation; test-only independent Evidence reader | D/E consumed immutable completion material | Keep the actual seal/acceptance checks. Native completion already avoids whole-store replay after insertion (`docs/evidence-design.md:111–116`). Test reader is an independent oracle; do not weaken it merely to match the optimized runtime. | Existing completion contracts; shared admission unit only for wrappers, no new duplicate Task |
| L-20 | `viewer_metadata_repository._basis` (~46), `projection_snapshot`, `_projection_writer`, `projection_replace_guard` | A/B currently DB-wide quick/FK checks on metadata reads and writers; C/D local basis | Move unrelated A/B sweeps out of normal Viewer eligibility/capture/guard/outcome. Keep fresh file identity, binding, epoch, invalidation/source/cache/HTML tuples and policy. Replacement only with a matching current basis, metadata acceptance CAS and short writer. | IV-V; unchanged/changed/failed/forced paths and concurrent source/epoch updates |
| L-21 | `viewer_task_projection_repository._review_evidence` (~24/57), `read_task_projections` | D selected Task review data; currently relies on same-snapshot quick/CHECK to make status/severity seek predicates safe | Replace that dependency together with L-20. Validate selected Task's actual consumed rows/domains so the query cannot hide an invalid selected Finding because status/severity was filtered out. Keep indexed own-Task scope; do not replace the full PRAGMA with another global orphan scan. | IV-V; invalid selected domains rejected, unrelated Tasks do not increase verification work |
| L-22 | `viewer_task_projection_repository._history` | D/E selected Task's cycles, incoming/outgoing event links, Receipt/Bundle links, latest-current compatibility | Keep current complete relevant own-history invariant, bounded chunks and both-direction ownership checks. Share validated facts within the capture. Never validate another Task's evidence merely because an invalid link points there. This is own-target work, not a whole-DB sweep. | IV-V, retaining public recent-ten display/count semantics |
| L-23 | `viewer_projection_cache.load_candidate/matches/require_same_artifact`; `viewer_maintenance._capture/_refresh` | C/D/E derived bytes and fresh applicability | Keep canonical bytes/hash/size, project/binding/epoch/file identity/format/generation comparisons. Cache checks occur when reuse is attempted, not every unchanged eligibility read. Same-attempt pure validation may be shared. Cache is never a Task/completion gate certificate. | Preserve current incremental contract; IV-V coupling tests |
| L-24 | `viewer.build_viewer_snapshot` -> `validate_snapshot_database_for_viewer`; older-source and explicit full rebuild/inspection | A/B/C/D over a deliberately whole display source | Keep source-version compatibility and intentional all-display materialization. Distinguish required all-Task output work from unrelated extra audit. New normal schema-34 path stays incremental; fallback is bounded and uses a fresh admitted source. | IV-V + IV-L for explicit repair, not another Viewer redesign |
| L-25 | legacy exported `viewer_metadata_repository.record_viewer_publication` / `record_viewer_attempt_outcome` (~325/402) | C/D global default admission around narrow metadata writes | Keep their behavior if retained, but use the same bounded metadata-write admission when invoked. Search showed definitions/re-export/diagnostic routing, not a direct current publication call; do not count these as observed normal performance without a caller. | IV-V; preserve compatibility tests, do not add a second outcome algorithm |

### Lifecycle Caller Closure

L-12/L-13 includes storage-owned earlier migrations and the source modules
`schema_task_ownership.py`, `schema_review_sessions.py`,
`schema_session_slot.py`, `schema_session_slot_reacquisition.py`,
`schema_verification_declaration.py`, `schema_task_event_notes.py`,
`schema_review_continuation.py`, `schema_review_answers.py`,
`schema_criterion_link_collector.py`, `schema_session_slot_index.py`,
`schema_own_review_indexes.py` and `schema_viewer_projection.py`.
Standalone validation and migration entry are separate contexts even when
they share a helper.

The direct FK calls in `evidence_validation_repository.py` belong to L-15/L-18
according to normal versus full lifecycle source validation. Storage's legacy
completion-history/Receipt validators and initialization/migration PRAGMAs
belong to L-12/L-14; selected consumers use common/domain admission.
Preparation's direct FK check belongs to O-05; numerical initialization PRAGMAs
belong to U-02. Logger `full_scan` labels alone cannot prove closure because
Viewer and operational direct PRAGMAs may be outside those labels.

For migration preservation, `schema_preservation.capture/assert_preserved`
and storage's preservation helpers protect old values, row identity, objects,
sequence state and markers. Before-delta, after-delta and after-final-marker
are distinct database states. Enumerate state, guarantee and scope before
sharing; generic SQLite validity cannot replace old-value preservation.
The final destination and each supported transition retain rollback/reentry
tests. Historical migration bodies are not rewritten merely to meet a count.

### Lifecycle Regression And Protection Integration

Retain the existing Setup/recovery, doctor, backup-validation/reuse,
async-backup capture/repository/integration/process, migration-preservation,
Evidence capture/publication and Viewer snapshot/cache/delta/recovery tests.
The table identifies the source families and regression suites; each unit
expands these into its exact current tests before changing the consumers.

IV-P's protected core/Usage capture and managed-generation isolated recovery
adapter are defined once in the main plan. The coverage points here are:
private copy certification (L-04/L-06/L-08), normalized restored output (L-11),
supported acceptance/preservation (L-12 through L-14), numerical acceptance
(U-01/U-02), and restored-away Usage references (U-03 through U-05).
Core and Usage are independent snapshots, not one common transaction.
Operational wait/finalization fences (O-03 through O-06) must not be revived
by restoring core state. No protection execution or live restore is recorded
by this inventory.

## Cutover Coverage And Completion Proof

This ledger uses semantic call families found through definition/call/import
inspection, direct SQL searches, adapter routing, governing sections and tests.
It is not a claim that every SQL statement or every validation-like name has
been individually catalogued. A keyword count does not establish coverage.

Before changing a unit, identify each current affected callsite within its
families and assign exactly one disposition, new responsibility, owner and
regression basis. Check reverse callers and direct SQL/wrappers as well as
named helpers. A newly reached affected edge is classified before removal;
an unrelated request that changes accepted scope is reported separately.

Each completed cutover demonstrates:

1. Every affected old edge has an explicit keep/share/replace/move/delete
   disposition; no affected edge is unclassified, unowned or left to an
   unspecified later cleanup.
2. Replacements and old-edge retirement land together. A retired ordinary
   full-audit call is not retained as a hidden fallback, default admission or
   error-path retry. An intentionally retained explicit acceptance/full-history
   route has a named owner and cannot become an accidental routine fallback.
3. Existing schema/format, stored types/privacy, current applicability,
   writer/fencing and recovery guarantees remain covered. Successful hashes
   never replace mutable owner/Contract/target/partition checks.
4. Fixed selected work is tested with unrelated Task/history growth, measuring
   statements, inspected rows or VM work, decoding/hashing bytes, connection/
   transaction counts and inclusive time. Required list/count/output work is
   reported separately rather than hidden to obtain a lower metric.
5. Corrupt selected rows and reverse identity arms still reject; same-read reuse
   works; local mutation, transaction restart, separate connection, DDL/restore
   and effect-time basis changes invalidate affected facts.
6. Required explicit diagnosis, initial/restore/migration acceptance and copy
   audits remain effective. Tests use disposable databases; none injects a fault
   into live state or writes from doctor.
7. IV-X reconciles the converted family/callsite inventory, direct SQL and reverse
   imports with all units and existing external Tasks. Obsolete helpers and
   misleading instructions are removed only when no supported consumer needs them.
   Retained historical-schema or audit helpers are explicit, not forgotten work.

No test PASS, implementation completion, backup success or Task status is
asserted by these discovery records. Those results belong to the public CLI
and the exact reviewed implementation targets.
