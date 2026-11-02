# Integrity Validation Transition Inventory

## Role And Discovery Boundary

This is the coverage ledger for the conditional
[integrity validation placement and transition design](integrity-validation-plan.md).
That plan owns placement, implementation units, protection, activation and
retirement. This inventory records definitions, call families, current guarantees,
dispositions, owners and regression anchors; it neither activates a change nor
certifies live database state. Live Task state and results remain in the public CLI.

Family identifiers retain their transition scope. The call-edge dispositions
below describe the supported consumers, not a queue of additional implementation
work. Symbols are the locators; the original approximate line numbers are only
discovery aids. Verification results and reviewed revisions belong to the live
Task, not this inventory. The cross-consumer closure section reconciles direct
SQL, wrapper defaults and retained audit/compatibility callers.

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
| C-02 physical/FK ordinary entry | A/B | `state_resolver._inspect_database` conditionally calls `storage._physical_integrity_ok(..., quick_check/foreign_key_check)` | Current ordinary primary admission omits these sweeps; old-schema, doctor, Setup and recovery retain them | **Moved to explicit audit**, N-C03 / IV-L; ordinary entry uses N-C01/N-C02/N-C04 / IV-S. No public bypass option. |
| C-03 physical check implementation and lexical sharing | A/B | `storage._physical_integrity_ok`, `_reuse_read_integrity`, `_schema20_integrity_checks`; decorators on `state_resolver._inspect_database` and backup phase owners | Separate quick/full/FK results; query-only connection plus savepoint, write-count and schema-cookie lifetime; direct SQL `PRAGMA <check>` | **Keep/share at audit owner**, N-C03 / IV-L. The helper is not deleted merely because normal entry stops calling it. Preserve distinctions between physical and FK failures and between quick and full checks. |
| C-04 schema history/version | C | `storage.current_schema_version`, `_validated_migration_versions`, `missing_migration_versions`; resolver `_inspect_database` then `_validate_current_schema_contract`; repeated capability reads in `tasks`, `task_ownership`, `contracts`, `task_show_projection`, `stored_task_validation` | Schema version, exact every-marker name/type/time/gaps, supported source; repeated in one read and again at separate writer entry | **Share only within established same read state**, N-C02 / IV-S; feed already observed version/capability into immediate Task consumers in IV-T. Do not turn `MAX(version)` into proof of valid marker history. Preserve independent initial/migration/restore acceptance and fresh writer boundary. |
| C-05 exact owned schema and attachments | C | `storage._validate_current_schema_contract` -> `_Schema33OwnedValidation` -> `schema_evidence_reference_index.validate_owned_contract` (v37); predecessors `schema_viewer_finding_index` (v36), `schema_runner_lookup_indexes` (v35), `schema_viewer_projection` (v34), `schema_own_review_indexes` (v33) and Evidence schema helpers; `required_schema_objects_missing`, `newer_schema_markers_present`, `schema_objects_inconsistent_with_version` for older sources | Exact owned DDL fingerprint, required columns/FKs, forbidden attached/index/trigger/rebuild residue. `_Schema33OwnedValidation` exists only for one synchronous admission and checks connection, transaction state and schema cookie | **Share/replace duplicate composition**, N-C02 / IV-S, preserving required structural acceptance. Current one-call sharing is already implemented, so do not reimplement it as new work. Broader reuse requires an explicit lifetime and invalidation design, never process-wide mtime/size trust. Older migration/reentry validators remain independent. |
| C-06 current project identity/binding | C/D | `state_resolver._read_bound_project` / `_inspect_database`; `storage.validate_current_database_binding`, `_admit_current_database`, `_validate_current_task_read_database`; `project_binding_repository.read_project_binding_state`, `_read_project_binding_snapshot`, `_validate_target_binding` | One durable project identity plus current path hash/generation and binding relations; resolver value is compared again by current reader/writer | **Keep current identity and fresh effect checks; share repeated same-snapshot facts**, N-C04 / IV-S. No rebind or restoration inferred from a file stamp. Full binding lineage/Setup-specific policy remains owned by Setup and must be mapped there if changed. |
| C-07 common project control metadata | D | `storage._validate_current_project_rows` -> maintenance/Viewer/Evidence controls; cycle `COUNT(*)` only with `full_audit` | Typed consumed controls on ordinary admission; all-cycle generation equality on explicit full admission | **Replaced ordinary blanket composition**, N-C04 / IV-S; full counterpart retained / IV-L. Evidence publication separately checks its complete thin membership and generation; Viewer/backup retain their own controls. |
| C-08 admission wrappers and global-domain fanout | C/D | `storage.validate_current_database_structure`, `validate_current_database`, `_admit_current_database`, `_validate_current_schema_structure`, `connect_initialized[_readonly]`, `connect_initialized_task_readonly`, `begin_initialized_write`; `cli.context_read_connection`, `cli.handle_command`, `cli_handoff` | Global default invokes Runner/ownership/note/review validation; explicit operation-local paths already avoid unrelated histories. Reader and writer entry are independent states | **Replaced ordinary routing/composition**, IV-S/IV-T and the domain adapters below. Setup, doctor, recovery and full consumers retain explicit full admission; Runner Plan actions use T-09's complete selected closure. Fresh writer Task/owner/generation checks remain N-T02. |
| T-01 complete selected Task row | D | `stored_task_validation.validate_stored_task_rows`, `_validate_stored_task_row`, `_validate_stored_completion_evidence`, `_validate_stored_review_target`, stored scalar/privacy helpers; `validate_current_stored_task_rows`, `fetch_validated_current_task_row` | Exact SQLite types, IDs, privacy/capacity, states and cross-field matrices on selected full rows; no normalization or partial result; own short read only when caller has no transaction | **Keep target validation / share results**, N-T01 / IV-T. A mutable Task row is not hash-substitutable. Its current fields remain freshly read after transaction or mutation boundary. Recovery's verification-only exception stays confined to its owner. |
| T-02 selected Contract-pointer composition | B/D | `stored_task_validation._validate_stored_contract_relationships`; `validate_stored_task_rows` -> one selected-ID query; `contracts.read_current_contract` / `contract_content._validate_stored_contract`; `_SelectedTaskFacts.contract` | All returned same-Task revision keys/types/ownership and exact latest pointer, then current Contract content; single ID-set SQL including wrong-storage-class aliases, no global Contract sweep | **Keep/share**, N-T01/N-T03 / IV-T. Existing shared current Contract row avoids second latest aggregate. Standalone current Contract reads retain their own pointer validation. Do not replace mutable pointer validation by immutable Contract hash. |
| T-03 selected ownership, mutation authorization and slot | B/D | `task_ownership.validate_basis`, `read_bases`, `_locked`, `_require_mutation_basis`, `capture_mutation_basis`, `_free_slot`, `transition`, `initialize_task`; `tasks.read_mutation_basis`, `read_task_write_state` | Selected Task/ownership/execution same-project relations; caller authority and exact generation; combined executing/review-pending session slot; writer serialization | **Keep/share only read facts**, N-T01/N-T02 / IV-T. Preserve fresh writer comparison, combined-slot predicate and SQL guards; no reuse of display ownership flags. Global `task_ownership.validate_storage_rows` is an audit consumer, not normal selected admission. |
| T-04 selected row/fact handoff and detail | D | `_validated_task_facts`, `_SelectedTaskFacts.require_current`; `task_show_projection.retain_validated_task_read`, `_check_retained_task_read`, `show_task`; `cli.read_task_detail` / `handle_task_context` | Same connection, actual transaction/savepoint, exact Task/project, write-count and scope lifetime. Context selected row and checkpoint can be reused only in retained read; marker-2 intentionally rereads after physical observation | **Keep existing sharing and extend only documented duplicates**, N-T04 / IV-T. Commit/rollback/connection replacement/mutation invalidates sharing. Normal Usage access removal remains existing Task 956d, not part of this change. |
| T-05 list/current/next selection and projection | D | `tasks.list_tasks`, `list_current_tasks`; `selection.select_next_tasks`, `count_next_tasks`, `next_task_readiness_sql`; `task_ownership.project_tasks`; `ordering.lane_ordering_cte` | Validate selected complete rows before filtering/projection; lane readiness and exact count are actual selection work, not unrelated semantic audits | **Kept selection predicates; shared selected validation**, N-T01/N-T04 / IV-T. List/current/next pass admitted ownership to projection; no second ownership read is required in that unchanged selection snapshot. |
| T-06 ordinary mutation and completion freshness | B/D | `tasks._add_prepared_task`, `edit_task`, `reopen_done_task`, `capture_completion_basis`, `validate_completion_plan_basis`, `validate_proposed_done_ordering`, `validate_done_transition_inputs`; `contracts._activate_revision_zero`, `_later_revision`, `edit_contract`; `ordering` guards | Ingress then external preflight, fresh locked Task/Contract/owner/status/order/target basis, atomic events and rollback; completion semantic token and owner generation compared before sealing | **Keep N-T02**, IV-T for ordinary composition, IV-R/the existing Evidence publication Task for domain gate and seal internals. No cross-writer reuse of preflight. `done` immutability, reopen cycle and same-lane successor/predecessor checks survive. |
| T-07 Contract/checkpoint/handoff stored content | B/D | `contract_content._validate_stored_contract`; `checkpoints._row_to_checkpoint`, `record_checkpoint`, `read_latest_checkpoint`; `handoffs._validate_stored_handoff`, `record_handoff`, `list_handoffs`, `show_handoff`, `withdraw_handoff`, `handoff_summary_for_task`; `cli_handoff` writers | Target content/privacy, exact Contract revision, idempotent replay, Handoff state matrix and caller permission in fresh writer; count and rows in one snapshot | **Keep/share**, N-T03 / IV-T. Checkpoint/Handoff record already use `read_task_write_state` shared facts, withdraw already captures owner once. Replay cannot bypass selected validation. No new evidence hash required for these mutable/current relationships. |
| T-08 notes/events and Effort | D | `tasks.row_to_event`, `validated_event_note`, `fetch_task_event_rows`, `create_task_event`; `effort.build_effort_advisory`, `_fresh_activity_generations`, transition activity helpers | Consumed event/note privacy and operation fields; Effort selected Task and fresh activity-generation checks. Global retained-note validation exists in storage's global branch | **Keep targeted content/current-generation checks**, N-T03 / IV-T; retain global audit only in IV-L. Activity scans required to compute the advisory are not automatically removable integrity checks. Usage Hook/evidence algorithms remain IV-U and the existing Usage reconciliation Task. |

### Core And Task Entry Coverage

T-09 (B/C/D; IV-T) covers `verification_runner_plan_edit._read_task_preflight`,
`_edit_without_action`, `_edit_plan_only`, `_edit_combined` and `_edit_on_connection`.
Ordinary edits and explicit config-only or combined Plan actions choose
operation-local admission. Their former global-default connection/writer edges
are replaced by complete selected Task/Contract/owner/criterion validation in the fresh
writer; preflight is never writer authority. Preserve pure prospective Plan
construction, rollback, writer closure before physical publication and the
committed-Task/unconfirmed-Plan outcome. Preserve bounded publisher path/file
and byte-identity checks. No new reservation or long filesystem-spanning writer.
`tests/test_m242_runner_plan_edit.py` covers both writer paths.
`test_plan_actions_revalidate_consumed_task_after_capture` verifies rejection
when a consumed field changes after preflight.
`test_plan_actions_ignore_unconsumed_note_history_and_keep_writer_local`
checks unrelated note history and the scope of the writer.

Normal public leaves covered through adapters are Task
add/list/current/next/context/show/effort/checkpoint/edit/complete and Handoff
record/list/show/withdraw. Task edit includes Contract-only, state, compatibility
done and reopen branches. Explicit Runner Plan actions use T-09's targeted
replacement; an ambiguous default must not switch other consumers accidentally.

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

### Ordinary Task Cutover Disposition

T-01 through T-04 and T-06 through T-08 retain their existing selected-row,
Contract, ownership, checkpoint, Handoff and event validation. Their private
selected-facts borrow now rejects DDL through the schema cookie as well as
connection, transaction, mutation and identity changes. No mutable gate result
is shared across a writer or physical-effect boundary.

T-05 `select_next_tasks` now lends the same `_validated_task_facts` result to
`project_tasks`, matching list/current. It removes the second ownership batch,
not the complete selected-row checks, exact count, lane predicates or ordering.

T-09 explicitly selects operation-local admission in the public CLI resolver,
config-only connection/writer and combined connection/Task writer. The existing
fresh selected Task/Contract/criterion/owner checks replace blanket history
admission; pure candidate construction, closed writer before publication and
committed-Task/unconfirmed-Plan behavior remain. Global helper defaults are not
changed. The earlier global-state race test now corrupts a consumed Task field;
separate tests retain unconsumed-note isolation and config-only VM-work scaling.

### Core And Task Regression Anchors

The common-entry cutover has these concrete caller dispositions. Domain
composition is covered by the R/U/O/L families and the cross-consumer closure
below. Defaults remain full admission, not an inference from a command label.

| Family | Common-entry replacement and retained edges |
|---|---|
| C-01 | `sqlite_connection` core openers retain FK enforcement, journal preflight and native transactions. `AdmissionConnection` adds only lexical epoch observation; diagnostic and POSIX file-lifetime owners remain separate. |
| C-02/C-03 | `_resolve_fixed` may pass explicit operation-local mode into `_inspect_database`; only current ordinary primary entry omits quick/FK scans. `_resolve_legacy`, `resolve_retained_source_snapshot`, `_classify_recovery_backups`, `recovery_database_matches_observation`, backup validation, old schemas, doctor and Setup keep full physical/FK checks. `_physical_integrity_ok`, `_reuse_read_integrity` and `_schema20_integrity_checks` remain audit helpers. |
| C-04/C-05 | `current_schema_version`, `_validated_migration_versions` and `_validate_current_schema_contract` share immutable successful facts only within `admission_scope`. Exact all-marker, owned-definition, column and FK checks remain. Direct standalone and unknown/native injected connections validate fresh. `_Schema33OwnedValidation` remains the narrower ledger/Bundle composition, not a new global certificate. |
| C-06 | `_read_project_binding_snapshot` shares its frozen state/lineage inside the same scope. `_inspect_database`, `_admit_current_database` and Task-read admission reuse that fact; `_validate_target_binding` still compares each actual target. Independent backup binding checks and later effect/writer snapshots remain fresh. |
| C-07 | `_validate_current_project_rows` keeps control validation. Explicit local structure/admission and Task-read callers omit cycle COUNT; global default retains it. Resolver doctor/recovery explicitly retain full project-history equality even when their domain audit is composed later. Evidence publication's selected generation checks are unchanged. |
| C-08 | Scope owners are `_inspect_database`, `validate_current_database_structure`, `_admit_current_database`, `_validate_current_task_read_database` and standalone schema validation. They do not cache Runner, review, ownership, note or Task verdicts. CLI retained-read and later consumer composition establish fresh scopes. Runner Plan actions use T-09's complete selected closure; review composition uses R-01 through R-10. Retained full consumers are classified below, not pending conversions. |

Removing C-02 exposes one consumed relation formerly supplied by the global
FK scan: Handoff list/show must batch-check returned source Task/project
identities. Record retains selected Task/canonical replay checks, and withdraw
retains selected ownership. This replacement does not inspect unconsumed
outbox rows or move general Handoff composition out of IV-T.

- `tests/test_structural_read_admission.py`: ordinary/full/doctor/recovery modes,
  same-read history/owned/binding sharing, native cursor/script/context controls,
  transaction restart, write/DDL/binding/control invalidation, independent
  connections, fresh writer drift and VM work under unrelated-row growth.

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
| R-04 | `review_material_repository.ReviewMaterials`, `review_materials`, `FindingBatch`, `finding_batch`; `evidence_repository._validated_authority_context`, `_validate_artifact_manifest_storage`, `validate_manifest_evidence_references` | D/E, borrowed immutable authority/manifest originals; fresh mutable Reference/Task/Receipt/Finding graph | **Keep existing lexical borrowing**, IV-R. Require same connection, project/Task, schema and actual transaction epoch. Only missing immutable material is fetched. Keep final batch mutable-graph admission after writes. No successful-prefix result escapes to another batch. `tests/test_review_material_reuse.py` covers restart, DDL, local writes and the final batch boundary. |
| R-05 | `review_continuation_repository.validated_continuation_history`, `read_continuations`, `read_judgments`, `validate_judgment_storage`; `review_continuation_evidence_repository.effective_review_snapshot`, `effective_for_task`, `freeze_basis_locked`, `completion_selection_locked` | B/D/E, same Task's continuation ancestry, retained originals, effective heads, explicit resolutions and frozen completion graph | **Keep/share**, IV-R. Existing guarded effective snapshot is reused; do not compute it repeatedly in the same unchanged state or replace it with latest-PASS selection. Complete own ancestry and resolved/retained Findings stay in report/seal scope. Preserve transaction/write-count invalidation and fresh locked freeze. Regression: continuation snapshot/registration/finalization suites and independent continuation Evidence oracle. |
| R-06 | `review_wait_basis_repository.read_wait_basis`, `read_review_basis`, `read_preparation_basis`, `validate_historical_wait`; review/wait/preparation/collector/finalization callers | B/D, current Task/Contract/owner/manifest/Reference and requested old occurrence relation | **Shared composite**, IV-R. `read_review_basis` consumes the already admitted Task row in its synchronous snapshot. Current owner/generation, exact Contract/target and current/old native relations remain validated. Historical waits never establish current eligibility; each effect boundary rechecks. Regression: wait basis, tracking admission, continuation transition and managed wait tests. |
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
  operation-local entry. `verification_runner_repository` validates the exact
  selected/newly sealed graph around T1/T2/cleanup; the global graph reader
  remains an explicit audit consumer. Keep atomic
  target/intent and observation/Reference/criterion/cleanup linkage, current
  applicability, process-zero/handles-closed/output-discarded/exact-tree proof,
  and unknown-result fences. An owner change after admitted T1 must not prevent
  mandatory cleanup/audit; no restart relaunch or invented result is permitted.
- R-10 (B/D; IV-R): `_deny_unresolved_attempt` and
  `read_pending_verification_runner_cleanup` select the project-wide pending
  inventory required by the fixed private inventory and single Runner lock.
  Candidate/anti-join/cardinality checks preserve dangling attempts, foreign/type
  aliases, missing/conflicting terminal or cleanup relations and duplicate owners
  in the absence proof, without decoding unrelated terminal payloads. Full graph
  audit remains IV-L's explicit lifecycle consumer.

Runner regression anchors are `tests/test_m243c_runner_gate.py`,
`tests/test_m242_runner_service.py`, `tests/test_task_ownership_runner.py` and
repository graph/cardinality tests. Cover selected corruption and unrelated
terminal growth, lock-before-T1, atomic rollback, false cleanup proof, cleanup-only
restart, T2 basis race and absence-proof corruption. The OS lock spans effects;
SQLite transactions do not. These are current Runner consumers of the common
placement contract, not an additional Runner feature or a separate execution unit.

### Review Consumer Cutover Map

The IV-R call-edge disposition is implemented by these repository boundaries;
this map is not live Task status or a validation certificate.

| Families | Bounded responsibility and retired edge | Regression anchors |
|---|---|---|
| R-01/R-02/R-03 | Selected storage admission retains both identity arms. Its collected Receipt check and dispatch check share one call-local, write-free validation. Frozen continuation construction uses the selected Receipt iterator instead of one reader per Receipt. Original readback carries its raw-bound parse into immediate reporting/submission; framing and native writers admit independently. | `test_review_answer_reuse`, `test_review_answer_registration`, `test_review_answer_core`, `test_review_continuation_snapshot` |
| R-04/R-05 | Existing material/effective scopes retain their actual transaction epoch; both main and TEMP schema cookies invalidate them. Mutable effective evidence also retains write-count invalidation. | `test_review_material_reuse`, `test_review_continuation_snapshot`, `test_review_continuation_registration` |
| R-06 | The wait/review basis composite carries its already admitted Task row to the synchronous projection. Frozen wait guards consume one confirmed complete source inventory; the next guard and external-effect branch read anew. | `test_review_wait_guard_reuse`, `test_review_wait_basis`, `test_review_wait_polling` |
| R-07 | Native Bundle construction selects the bound authority snapshot; full authority history remains audit-only. Own historical cycles, retained/resolved Findings and post-insert Bundle assertions remain complete. | `test_m242_r3a_schema20_storage`, `test_review_answer_core`, `test_m244b_legacy_fresh_acceptance` |
| R-08/R-09/R-10 | Current selection keeps its separate pre/post-filesystem observations. Generation reads and T1/T2/cleanup use selected graph closure. Project pending selection retains structural cardinality/anti-join inventory, retiring unrelated terminal payload/authority decoding. | `test_runner_inventory_scope`, `test_m242_runner_storage`, `test_m242_runner_service`, `test_m243c_runner_gate`, `test_task_ownership_runner` |
| O-01/O-02/O-03 | Physical identity, leases, short transactions, exact schema, complete source slots and monotone effects are retained. Each composite load admits its store once; unrelated snapshots/stores never share admission. | Operational repository and managed-wait suites |
| O-04/O-06 | Request load returns already decoded rounds; finalization load returns parsed parts to same-transaction consumers. Advance rereads after writing; explicit history and migration still consume all required rounds. The finalizer factory reads root/latest from one round inventory. | `test_review_wait_request_continuation`, `test_review_finalization_continuation_repository`, `test_review_finalization_continuation` |
| O-05 | `read_current` admits the current pointer and exact record; publication also checks an explicitly reused ID. Initial acceptance, explicit `read` and old-Packet fallback retain full history/FK checks. | `test_review_preparation_repository`, `test_review_answer_runtime_selection`, `test_review_handoff_continuation` |

Core finalization reads separated by registration, Git, native completion or
report/recovery boundaries remain fresh. The selected Task projection omits
usage but keeps its gates; a saved operational success never replaces the native
read. Fixed Git-candidate hashing remains outside IV-R. The pending Runner
inventory still scales with the required project-wide set; its bounded semantic
decoding is not a claim of constant total SQL work.
Selected Runner closure is measured separately by `test_runner_query_scaling`:
the actual repository SELECTs retain fixed results while unrelated Task and
own older-generation terminal graphs, References and criterion links grow.
Both reverse identity arms remain selected. Schema-35 lookup indexes and the
rowid union avoid unrelated scans; `test_runner_lookup_migration` preserves
predecessor rows/identities, old schema definitions, sealed bytes and failure
rollback. This index transition does not alter the pending-inventory guarantee.

## Numerical Usage Families

| ID | Current call family and placement | Class / scope | Disposition and destination |
|---|---|---|---|
| U-01 | `usage_repository._structure`, `_project_binding`, `_validate`, `connection`, `admitted_read`; `sqlite_admission.structural_read`/`read_epoch`. All ten numerical repository layers inherit exact DDL/history and separate singleton binding facts; reconciliation checks its revision control separately. | C plus current identity binding. One actual connection/transaction epoch, not all Tasks. | **Share**, owner IV-U: typed repository-issued borrowing handle, lexical invalidation and fresh independent/writer admission. `_direct` replaces repeated locator metadata connections with one read, ending before physical source I/O or CAS. No stat-only or process-global validity cache. Preserve exact prior-schema diagnosis and Setup-only migration. |
| U-02 | `UsageRepository.initialize` (162); `UsageAttributionRepository.initialize` (109); evidence/wait/incremental `initialize` predecessors. Six direct code sites issue paired `quick_check` and `foreign_key_check`: base 170–171; attribution current 114–115 and predecessor 125–126; evidence 107–108; wait 144–145; incremental 122–123. | A/B, explicit Setup/current-store acceptance and migration predecessor checks. These are **not** ordinary Hook/connection queries. | **Keep / share same-state work**, **IV-L**, with numerical admission integration from IV-U. Preserve actual initial acceptance, supported predecessor and resulting-schema acceptance, atomic marker-last migration, rollback, private-file creation and no-replace publication. Remove a repeated scan only when the same snapshot and same required guarantee are demonstrated; quick_check and full integrity_check are not interchangeable labels. |
| U-03 | `usage_reconciliation_core.read_task_inputs` validates selected changed ownership/cycle/Receipt inputs; `usage_reconciliation_events` resolves batched operation identities. `usage_reconciliation_runtime` persists work between fresh admitted readers; lifecycle participant discovery reuses the revision-bound structural inputs. Retained `usage_attribution_repository`/`usage_response_repository` full replay is the differential oracle. | B/D. Changed owner/transition history, participation, events and exact response correspondence; old/new dependency closure. | **Keep** selected reconciliation and fresh fences under [the current owner](database-design.md#usage-reconciliation-tracking); IV-U owns only its remaining structural admission sharing. Batch exact ID reads and share a single admitted relation input within one capture. Missing/restored-away operation references remain unbound/unknown. No stale active owner inferred from absence of new numerical counters. |
| U-04 | `usage_evidence_repository._snapshot_read`, `_snapshot`, `_responses`, `_refresh`, `_capture_projection`, `_overlapping_snapshots`, `_adopt_projection`, `_read`; response overrides `_capture_projection`, `_project_for_refresh`, `_adopt_projection`, `read`, `_totals`. `usage_snapshot_read.py` supplies transaction-bound validated snapshots/membership. | B/D/E. Immutable snapshot document/membership/original counters and supersession/cycle links, plus mutable current eligibility. | **Keep seal/selected-object validation; share and replace global metadata selection work** under existing `tg_task_469a9a022b76f9e7`. Do not treat a snapshot hash as proof that current core references still exist or that classification is current. Preserve original response-set de-duplication, shared partition overlap, old snapshots, supersession and completion-cycle links. Reuse only in one admitted read; next capture/connection checks current basis again. |
| U-05 | `usage_publication_repository.prepare_publication`, `_index_metadata`, `_publication_state`, `publication_snapshot`, `advance_publication`, `publication_guard`; service-level fresh guard in `usage_evidence_service`. | D/E; rotating audit progress, snapshot/index bytes, mutable core/numerical publication basis and CAS. | **Keep** fresh publication decision and bounded historical audit; **replace** full basis rereading with complete change tracking under existing `tg_task_469a9a022b76f9e7`. Preserve stale-CAS rejection, restoration invalidation, interrupted progress, last-good output and no mutation on corrupt cursor. An unchanged cursor, size, mtime or inode is not a whole-store certificate. |
| U-06 | `usage_repository._physical`; `usage_locator_repository._validate_pending_binding`, `remember_locator`, `locator_page`, `begin_locator_attempt`, `_promote_locator`, `discard_pending_locator`, `known_sources`, `registered_sessions`; `usage_locator_admission._schema`, `_native`, `_current_round`, `_assignment`, `_registered`, `classify_pending`, `observe_pending`. | Physical identity controls, C, B/D for exact active collection assignment/Receipt, locator/source incarnation and queue CAS. | **Keep** fresh path/header/binding/session and exact registration checks. A saved locator is a hint, not eligibility proof. Share duplicate structural admission in one read through IV-U/IV-R where applicable; changed participant reconciliation remains `tg_task_469a9a022b76f9e7`. Raw SQLite header-reader lock correctness is existing `tg_task_7044a15b7de5aa3a`, not a new performance bypass. |
| U-07 | Usage cursor/context/source/response decoders, closed values and exact core operation lookup; independent Usage connection/Setup boundaries. | Input validation and target D; not whole-core audits. | **Keep**, owner IV-U: source-generation changes, replacement/truncation, deferred audits, unknown optional counters and typed state. Missing/busy/corrupt Usage must remain Usage-unavailable and must not block normal core completion. Do not merge numerical and core databases or imply that a core backup is a paired Usage snapshot. |

### Numerical Regression Anchors

The following regression suites cover these boundaries.

- `tests/test_usage_admission_sharing.py`: all ten schemas, structural SELECT counts, typed binding, lexical/connection/transaction/local-write/DDL/source invalidation, locator composition and close-before-source-I/O, Setup audit retention and unavailable numerical output. U-06's assignment/source/queue validators and U-07's row decoders remain unchanged; Evidence and publication readers keep their existing fresh boundaries.
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
| O-04 | `RequestRepository._load` admits attempts and rounds once and returns the validated round inventory to `read_rounds` and installation/advance callers. `TransitionRepository` checks current round and predecessor chain before fence/bind/advance. | B/D, one original Task/wait and its ordered continuation/effect closure. | **Shared**, IV-R: no second `load_rounds` in the same read. Keep current occurrence, reviewer/turn binding, sequence, predecessor settlement and unknown-effect fences. V1 conversion validates predecessor history; a new boundary establishes a fresh read. |
| O-05 | `PreparationRepository._load_current` / `read_current` / `publish` admit current pointer and selected immutable root; `_load` / `read` retain FK scan and complete history | B/D. Normal current-source lookup does not decode prior preparations. Explicit old-Packet lookup and initial store acceptance retain complete root/history validation. | **Split**, IV-R. `review_answer_preparation.locate` first reads current; only an explicitly different supplied Packet selects the full-history reader. Missing/stale data fails under its existing contract, never triggers a repair or a current-path full-audit retry. |
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
| L-15 | Routine `evidence_publication` -> `evidence_incremental_repository`; explicit `_capture` -> `storage.capture_current_evidence_projection` | C/D/E; routine new Bundle closure and thin acknowledged membership; explicit baseline/repair retains full Ledger/Bundle replay | **Split** normal and full capture. Routine publication can advance a valid older baseline after an interrupted save using the physical index whose digest and generation SQLite acknowledged. Missing, invalid or foreign baselines require explicit Setup repair. Required complete index output remains. | Existing `tg_task_a5f10d38b4deac04`; `test_evidence_incremental_publication`, `test_evidence_projection_capture` |
| L-16 | `evidence_publication._generation_guard` / `_replace_index` | C/D fresh project/binding/settings/file/generation state | **Replaced** routine global admission with structural control guard; explicit full repair passes `full_audit`. Keep reader/atomic replacement and stale-basis rejection. Generation equality is not content-integrity proof. | Existing `tg_task_a5f10d38b4deac04` |
| L-17 | `evidence_projection_metadata_repository.record_evidence_projection_outcome` -> operation-local reader and writer | C/D narrow projection/policy state and expected prior outcome | **Replaced** global admission; fresh conditional writer preserves monotonic generation, unknown outcomes and last success. | Existing `tg_task_a5f10d38b4deac04` |
| L-18 | Evidence force/physical-status/Setup rebuild and independent source validators | A/B/C/D/E all Evidence required by explicit repair/baseline | Keep explicit audit/rebuild scope, including first publication, restored/replaced DB, schema/format changes. Rebaseline does not silently import JSON into core DB or change historical Bundle bytes. A broad audit is not the automatic fallback for every normal metadata mismatch. | Existing `tg_task_a5f10d38b4deac04` owns its baseline; IV-L owns recovery boundary integration |
| L-19 | native completion's selected-cycle / selected Bundle-history validation; test-only independent Evidence reader | D/E consumed immutable completion material | Keep the actual seal/acceptance checks. Native completion already avoids whole-store replay after insertion (`docs/evidence-design.md:111–116`). Test reader is an independent oracle; do not weaken it merely to match the optimized runtime. | Existing completion contracts; shared admission unit only for wrappers, no new duplicate Task |
| L-20 | `viewer_metadata_repository._basis`, `projection_snapshot`, `_projection_writer`, `projection_replace_guard` | C/D selected control basis; ordinary metadata reads/writers no longer run A/B quick/FK sweeps | Keep fresh exact structural admission, file identity, binding, epoch, invalidation/source/cache/HTML tuples and typed opt-in policy. Replacement requires a matching current basis, metadata acceptance CAS and short writer. Full A/B checks remain at lifecycle/audit owners. | IV-V; unchanged/changed/failed/forced paths and concurrent source/epoch updates |
| L-21 | `viewer_task_projection_repository._review_evidence`, `read_task_projections` | D selected Task review data, with receipt-only Finding index introduced in schema 36 | Read every selected Finding before validating status/severity and display counts; no domain filter may conceal invalid stored values. Preserve selected Reference reverse ownership and indexed receipt aliases; no unrelated global orphan scan or quick/CHECK dependency. | IV-V; invalid selected domains rejected, unrelated Tasks do not increase verification work |
| L-22 | `viewer_task_projection_repository._history` | D/E selected Task's cycles, incoming/outgoing event links, Receipt/Bundle links, latest-current compatibility | Keep complete own history, bounded chunks and both-direction ownership checks. Schema 36's Task-first cycle index exposes foreign-project own cycles for explicit rejection instead of filtering them out. Never validate another Task's evidence merely because an invalid link points there. | IV-V, retaining public recent-ten display/count semantics |
| L-23 | `viewer_projection_cache.load_candidate/matches/require_same_artifact`; `viewer_maintenance._capture/_refresh` | C/D/E derived bytes and fresh applicability | Keep canonical bytes/hash/size, project/binding/epoch/file identity/format/generation comparisons. Cache checks occur when reuse is attempted, not every unchanged eligibility read. Same-attempt pure validation may be shared. Cache is never a Task/completion gate certificate. | Preserve current incremental contract; IV-V coupling tests |
| L-24 | `viewer.build_viewer_snapshot` -> `validate_snapshot_database_for_viewer`; older-source and explicit full rebuild/inspection | A/B/C/D over a deliberately whole display source | Keep source-version compatibility and intentional all-display materialization. Distinguish required all-Task output work from unrelated extra audit. New normal schema-34 path stays incremental; fallback is bounded and uses a fresh admitted source. | IV-V + IV-L for explicit repair, not another Viewer redesign |
| L-25 | legacy exported `viewer_metadata_repository.record_viewer_publication` / `record_viewer_attempt_outcome` | C/D bounded admission around current metadata writes; pre-incremental runtimes retain their full admission | Current calls use fresh structural and selected control validation. Retained predecessor publishers cannot require schema-34 projection tables. These exports are not the current publication call path and do not establish observed normal performance without a caller. | IV-V; preserve compatibility tests, no second outcome algorithm |

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

### Lifecycle Composition Disposition

The exact IV-L composition is:

| Families | Current call edges and disposition |
|---|---|
| C-03/L-01/L-03/L-12 | `_inspect_database` and `read_setup_state` own lexical integrity scopes. Nested exact-schema validators share only the same query-only connection's successful quick/full/FK result, guarded by savepoint, write count and schema cookie. Every new observation rechecks; ordinary local admission still performs no physical audit. |
| L-04/L-06/L-09 | `_validate_publication_source` composes `_validate_database` with the exact source-schema validator. `_validate_database` uses the existing physical-check helper rather than duplicate raw PRAGMAs. Each initial source, closed private copy and fresh source guard owns a separate scope. Async live control remains narrow. |
| L-08/L-11 | `_inspect_recovery_database` composes the structural and exact recovery-content checks at the existing source/copied/normalized callsites. A writable destination remains uncached; normalized read-only output establishes its own basis. Fixed-observation recovery retains its separate resolver checks. Viewer normalization for source34 and later uses exact historical Setup admission before and under its writer, then the existing normalized-output gate. |
| L-02/L-05/L-07/L-10 | Keep Setup stage ordering, current backup control and inventory CAS, immutable content/rule/scope proofs, selection/last-good and effect-time refresh. No new source full audit, old-artifact mutation, timer or policy change. |
| L-13/L-14/U-02 | Keep initialization, each supported predecessor, post-delta, final marker and committed result as distinct states. Historical migration bodies and the six numerical initialization quick/FK sites remain unchanged: they are actual acceptance/migration boundaries, not redundant calls inside one admitted read. |

The guarded physical-check helper is not a reusable Task/domain verdict.
Direct validators outside an owning scope still run their checks. These
dispositions neither claim live Task completion nor retire the initiative.

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

## Cross-Consumer Call-Edge Closure

These routes reconcile the family ledger with callers and direct SQL. The full
admission default is deliberately retained; an explicit local adapter must not
fall back to it after a local failure. Required native FK enforcement and exact
schema/marker validation are distinct from whole-database FK/physical sweeps.

| Families | Ordinary caller composition | Retained full or compatibility consumer |
|---|---|---|
| C-01–08, T-01–09 | CLI retained reads, `tasks.begin_task_write`, Handoff adapters and Runner Plan edits use local admission and their selected predicates. Schema and binding facts expire with their read epoch; writer admission is new. | `configure_project_maintenance`, binding CAS/legacy cleanup and `viewer_projection_repository.reset_restored_database` are explicit Setup/restore writes. `connect_initialized`, `connect_initialized_readonly` and `begin_initialized_write` choose full admission by default. `_connect_validated_readonly` applies the validator its caller supplies, including the selected validator from `connect_initialized_task_readonly`. |
| R-01–10 | Review adapters bind `review_task_id`; selected Receipt/Finding/continuation/Runner relations remain complete. Required pending inventory and lane/session predicates are actual cross-Task selection, not full payload validation. | `validate_current_schema21_admitted_rows`, `_validate_review_storage_rows` without a Task, and full Runner graph readers are used by explicit audit and migration/copy admission. No ordinary failure promotes itself to one of these modes. |
| O-01–06 | Wait stores retain physical/journal/lease controls; current preparation and admitted rounds are selected or shared within their own operational read. Finalization does not reuse pre-effect eligibility. | `PreparationRepository._load` directly runs `foreign_key_check` for initial acceptance and explicit history/old-Packet lookup. It is not called by ordinary current-pointer reads. Version conversion retains its predecessor inventory. |
| U-01–07 | The numerical repository layers borrow structural facts only within their admitted connection; reconciliation consumes changed dependencies and bounded continuation work. Published immutable bytes do not establish current totals. | Direct quick/FK pairs in `usage_repository`, `usage_attribution_repository` (including its predecessor reentry), `usage_wait_repository`, `usage_evidence_repository` and `usage_incremental_repository` are initialization/migration checks. Old-format decoders and the independent full classifier remain supported compatibility/oracle consumers. |
| L-01–14 | Routine backup eligibility/control remains short and local; copying closes the live reader before private-copy certification. | Resolver explicit modes, `backup._validate_database`, schema-module migrations and storage's `apply_*_migration` / empty-history admission retain full checks. `protection_repository.copy_usage` certifies a private copy; `validate_restored_usage` admits restored Core and original numerical values. Its `usage_recovery_repository` boundary preserves restart inputs and leaves aggregate freshness pending, without draining normal reconciliation. |
| L-15–19 | Incremental Evidence uses new Bundle closure and complete thin membership. Completion insertion validates its newly saved cycle; partial legacy reopen explicitly uses the local bridge. | `_validate_evidence_ledger_rows`, `_validate_completion_evidence_bundle_rows`, `_validate_verification_receipt_rows` and `validate_completion_cycle_storage` retain their full FK/relationship work for whole-source capture, explicit acceptance, migration and recovery. The `operation_local` partial-cycle bridge prevents the retained old insertion branch becoming a routine fallback. |
| L-20–25 | Incremental Viewer uses changed Task relations, checked display cache and fresh publication/outcome guards. Current exported metadata writers select structural admission. | `viewer.build_viewer_snapshot` and predecessor publishers retain deliberate whole-display/source-version support. Full output cardinality is not an ordinary single-Task validation cost. |

The retained synchronous `backup.run_routine_backup` and its locked helper are
not ordinary-command callers. Their admitted copy/reconciliation work retains
full source checks under L-04/L-09/L-10 and the
[post-commit coordinator](setup-state-design.md#post-commit-coordinator).
Regression fixtures still exercise that primitive; the normal coordinator uses
the asynchronous opportunity path and never selects it as a fallback.

The manifest reader (`evidence_repository._read_owner_indexed_evidence_references`)
and the all-kind reader (`evidence_validation_repository._validate_selected_reference_source_chunk`)
share `evidence_repository._read_reference_source_rows`. Empty selections skip
the Reference lookup. Schema 37 seeks each selected project/kind/source key.
Schemas through 36 seek successive distinct owner keys in the Reference index
and probe each selected source under those keys, skipping intervening history
for the same non-NULL owner. Both paths expose missing Task rows for stored
TEXT and BLOB owner keys. A malformed NULL owner in a predecessor Reference
can terminate that older lookup early. Schema 37 selects by source before
checking ownership, including a NULL owner. Current ordinary commands require
the current container schema.
`verification_receipt_repository.read_verification_receipt_snapshot` uses
the companion Task/kind index for References. For manual Receipts it seeks
distinct project keys in descending order, including a final NULL-safe probe,
and looks up the selected TEXT and BLOB Task IDs under each key. See
[completion and read integration](review-completion-design.md#completion-and-read-integration).
The [source lookup contract](database-specification.md#evidence-reference-source-lookup)
and [migration design](database-design.md#evidence-reference-source-index-migration)
own these boundaries. `test_evidence_source_lookup_scaling` covers both projections,
orphan rejection on read-only and writing completion, unrelated-orphan isolation
and the cost of empty and nonempty selections. `test_evidence_source_index_migration`
and `test_m22_evidence_ledger_storage` cover migration preservation and selected
Reference integrity.

`tests/test_integrity_consumer_closure.py` measures the public CLI's read-only
completion check, its writer and post-commit publication separately. Both
histories use the same physical project path, selected Task and manual Receipt.
Each additional completed Task has its own manual Receipt. The tests require
a ready completion check with no blockers and fixed SQL and validation VM work
as unrelated history grows. The two seal inserts retain
SQLite's native deferred foreign-key checks. Their scan allowance comes from
the actual insert plans and the growth in the child tables those plans visit.
Publication measures the two complete thin-index reads individually and
requires fixed SQLite work across the rest of post-commit maintenance,
including selected Evidence and Viewer validation and publication guards.
It checks selected Bundle construction and the independent Evidence Reader,
and compares the decoded HTML snapshot with a full Viewer capture at the
published timestamp outside the measured interval. The family
regressions also cover Runner effects, the numerical reconciliation oracle,
freshness/race invalidation and isolated migration/restore.

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
