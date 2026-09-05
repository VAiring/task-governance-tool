> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves the completed M23 Analyzer-retirement and schema-
> cleanup execution plan, not current product behavior, implementation
> authority, live Task state, or evidence. Internal words such as current,
> approved, accepted, active, or implemented describe only the captured
> revision. This file cannot fill a current authority gap or satisfy a current
> gate.

# M23 Retirement And Schema Cleanup Execution Plan Capture

- Source path: `plan.md`
- Source commit: `94a2d91bbfa31b814a9045e88283d44672fbf966`
- Capture unit: `TG-RMAP.4`
- Current replacements:
  [specification Evidence contract](../../specification.md#current-schema-v22-verification-ledger-and-bundle-contract),
  [specification persistence contract](../../specification.md#current-schema-v22-persistence-contract),
  [specification retired-Analyzer boundary](../../specification.md#evidence-interpretation-and-retired-analyzer-boundary),
  [schema cleanup design](../../design.md#schema22-reservation-cleanup-design),
  [independent reader design](../../design.md#test-only-independent-evidence-reader),
  and [plan](../../../plan.md#current-evidence-bundle-json).
  Use the public CLI for live Task state and evidence.
- Capture purpose: preserve the complete pre-retirement TG-M23R.1 through
  TG-M23R.11 and TG-M23S.1 through TG-M23S.8 execution sequence, boundaries,
  acceptance, and one-time activation narrative from the committed source.

The exact source region begins below and ends before the next plan anchor.

````markdown
<a id="m23-retirement"></a>

### M23 Retirement And Evidence Test Oracle

The approved outcome is to retire the M23 Analyzer subsystem and retain only
its independent Evidence-reading/validation capability, necessary test helpers,
and the small non-authority/compatibility boundary below. The retained reader
belongs to repository tests, not the installable runtime. This section owns the
static execution contracts; the Task database owns status and evidence.
TG-M23R.10 is the atomic runtime and active-authority retirement boundary.
The preparatory contracts below retain the Analyzer only before that boundary;
the current product excludes it and retains the test-only reader. Registration
alone does not authorize implementation or Git operations.

The retained capability checks Evidence index/Bundle file self-consistency,
declared generation, versions, identities, canonical bytes/digests, relations,
and the existing bounded semantics/privacy rules. It does not authenticate
authors, prove source-code correctness, or establish freshness against SQLite.
It imports neither SQLite/storage nor the producing Evidence semantic validator.
Existing bounded filesystem primitives may be reused; independence does not
require reimplementing file I/O or Windows safety infrastructure.

The final product boundary retains the rule that facts, caller declarations,
LLM inference, and uncertainty are not interchangeable, and that derived
explanations do not satisfy verification, review, or completion gates. It does
not retain a report schema, renderer, citation-generation subsystem, or future
Reporting adapter. Any later non-test reader path is a separate product decision.

Common scope and permission boundaries for every unit:

- Preserve schema v21, the public CLI/JSON and normal Skill loop, Bundle v1/v2
  and index formats/digests, existing durable rows, and Runner gate semantics.
  Keep `derived_analysis`, `llm_derived`, and `batch_analyzer` as writer-disabled
  compatibility vocabulary; retain unrelated `deterministically_derived` uses.
  Do not create a migration, rewrite existing evidence, or require all M23
  strings to disappear.
- Leave existing ignored `state/current/analysis/` artifacts untouched and
  inert after retirement. Do not add cleanup, import, relocation, or repair.
  Package replacement continues to preserve state and the three named local
  configuration files under `docs/release-install.md`.
- No new runtime reader, CLI, config, automatic execution, completion gate,
  live model, Linux support, or M24 redesign. Do not repair M23's v2 report
  pipeline before deleting it or transplant its Win32/process/outbox foundation.
- Preparation may copy the existing reader/codec into tests and adapt only the
  Analyzer-specific wrappers. Do not rewrite its validators wholesale, add a
  generic validation framework, or expand the existing threat model. Temporary
  duplicate runtime/test code and the old support facade retire at TG-M23R.10.
- This request authorizes formal planning and ready Task registration only.
  Later execution requires user approval of the sequence. Neither registration
  nor execution approval implies Git staging/commit/push, external CI, release,
  installation into a real project, or unrelated filesystem mutation.
  Existing Git-backed review/completion and tracked-inventory checks still need
  separately authorized exact Git material; do not weaken those checks or add a
  workaround when that approval is absent.

All units are sequential in lane `TG-M23R`, at orders 10 through 110 below.
Each consumes completed predecessor outputs and its cited current owners;
same-lane ordering represents the dependencies without a new dependency model.
This deliberately serializes the two schema-consumer migrations so their shared
fixtures and final cutover inventory have one resumable order.

| Unit / order | Bounded responsibility | Review |
|---|---|---|
| TG-M23R.1 / 10 | Separate reusable Evidence fixtures | Tier 1 |
| TG-M23R.2 / 20 | Preserve the independent canonical codec in tests | Tier 1 |
| TG-M23R.3 / 30 | Extract the neutral test-only Evidence reader | Tier 1 |
| TG-M23R.4 / 40 | Preserve existing reader rejection/regression coverage | Tier 1 |
| TG-M23R.5 / 50 | Migrate schema-v20 Evidence test consumers | Tier 1 |
| TG-M23R.6 / 60 | Migrate schema-v21/Runner Evidence test consumers | Tier 1 |
| TG-M23R.7 / 70 | Exercise completion-to-JSON-to-reader integration | Tier 1 |
| TG-M23R.8 / 80 | Prepare package-replacement preservation coverage | Tier 1 |
| TG-M23R.9 / 90 | Prepare Runner implementation-identity compatibility coverage | Tier 1 |
| TG-M23R.10 / 100 | Retire runtime, packaging, and current authority atomically | Tier 2 |
| TG-M23R.11 / 110 | Run final offline integrated acceptance | Tier 2 |

<a id="tg-m23r-1"></a>

#### TG-M23R.1 — Reusable Evidence Fixtures

Scope: extract only Evidence constants, reference encoding/digest/seal helpers,
native/legacy Bundle/index builders, and ordinary tree snapshots from
`tests/m23_test_support.py` into `tests/evidence_test_support.py`. Keep Analyzer
helpers in the old support file and re-export moved names there until cutover.
Retain the existing M22 payload fixture; do not duplicate its implementation.

Acceptance: moved fixtures produce the same bytes/digests and serve both old
Analyzer tests and future reader tests. The fixture's `json.dumps` reference
encoder stays independent of the reader codec. Verification: focused fixture
parity and representative existing consumer tests; import/discovery checks for
the four M24 consumer modules named in units 5–7. No runtime edits.

<a id="tg-m23r-2"></a>

#### TG-M23R.2 — Independent Canonical Codec

Scope: copy the Evidence-needed pure canonical encoder/parser and minimum error
support from `analysis_contracts.py` into `tests/evidence_reader_codec.py`, with
focused tests. Keep the original runtime module unchanged; copy no descriptor,
recipe, job, packet, report, retry, or process contract.

Acceptance: existing literal canonical-byte vectors, duplicate-key, float,
noncanonical, and invalid-UTF-8 checks pass independently of the producer and
fixture encoder. Verification: the new focused codec tests and lane discovery.

<a id="tg-m23r-3"></a>

#### TG-M23R.3 — Neutral Test-Only Reader

Scope: copy the existing `evidence_consumer.py` read/validation core into
`tests/evidence_reader_oracle.py`, using unit 2's codec. Replace only its
Analyzer-dependent source wrapper/basis validation, remove descriptor replay,
and preserve the full selected index entry, including v2
`bundle_format_version`. Keep the runtime consumer unchanged.

Acceptance: v1 native/legacy and one existing v2 fixture can be read without
changing source files; the neutral result preserves index version, selected
entry, and exact Bundle envelope or explicit legacy absence. There is no
Analyzer, storage, producer-validator, packet, or report dependency. Existing
`state_paths` bounded read primitives remain reusable. Verification: focused
positive/read-only/binding tests, import-boundary review, and lane discovery.
This is a test helper, not a new public schema or API.

<a id="tg-m23r-4"></a>

#### TG-M23R.4 — Reader Regression Coverage

Scope: adapt the Evidence-only cases from
`tests/test_m232_evidence_consumer.py` into a neutral reader test module.
Preserve canonical/literal digest, resealed semantic tamper, relation, path,
provenance, privacy, timestamp, and no-write checks against unit 3's reader.
Do not carry over the descriptor-replay case; keep the original M23 tests until
unit 10. Correct a defect in the extracted copy only within those existing
Evidence requirements.

Acceptance: the retained cases detect the same supported-format regressions
without importing Analyzer runtime. Verification: neutral reader/codec tests
and lane discovery. No exhaustive new adversarial matrix or producer hardening.

<a id="tg-m23r-5"></a>

#### TG-M23R.5 — Schema-v20 Test Consumers

Scope: switch Evidence fixture/reader uses in
`tests/test_m242_evidence_compatibility.py` and
`tests/test_m242_r3b_schema20_activation.py` to the neutral helpers. Retain the
old descriptor-replay block and only its required imports until unit 10.

Acceptance: schema-v20 null-Runner v2, v2 index referencing unchanged v1 Bundle,
and legacy null/absence retain their existing meaning and version information.
Verification: the affected compatibility/activation cases plus neutral reader
tests and lane discovery. No migration, storage, or projection implementation
change and no new Analyzer acceptance.

<a id="tg-m23r-6"></a>

#### TG-M23R.6 — Schema-v21 And Runner Test Consumers

Scope: migrate reader/fixture uses in
`tests/test_m243b_schema21_compatibility.py` to the neutral helpers. Use existing
fixtures to cover source-v21 v2 caller-attestation, not-required, and qualifying
Runner-observation forms; preserve source-schema/index binding and existing
Runner wrong-type/identifier rejection checks.

Acceptance: the full v2 entry and sanitized Runner projection survive the
reader without schema downgrade or source mutation. Verification: affected
reader compatibility cases, the existing stored-history canaries affected by
helper changes, and lane discovery. Do not change Runner gates, storage,
recovery, or the producer to satisfy the oracle.

<a id="tg-m23r-7"></a>

#### TG-M23R.7 — Completion-To-Reader Integration

Scope: adapt `tests/test_m244b_legacy_fresh_acceptance.py` so its existing
isolated completion and Evidence publication fixtures feed the independent
reader directly. Keep the old packet-only block and its minimal imports until
unit 10; it is not the new reader's acceptance endpoint.

Acceptance: existing v19-to-v20-to-v21 history preservation and fresh v21
completion-to-Bundle/index-to-reader checks pass, including the Runner-backed
and not-required fixtures. Assert preserved version/basis and before/after
source snapshots. Verification: this focused integration module and its
directly changed helper tests. No report, explanation format, or runtime route.

<a id="tg-m23r-8"></a>

#### TG-M23R.8 — Package Replacement Preservation

Scope: reuse `tests/test_m19_legacy_upgrade_rehearsal.py` package-overlay and
physical-install helpers for a bounded replacement regression. Cover exact
manifest-owned core replacement while preserving existing database/Evidence,
an inert analysis-artifact sentinel, and the three supported config files.
Keep additions test-only and locally owned; no installer or cleanup feature.
Adapt the shared test inventory helper to the candidate manifest where its
current `git ls-files` selection would require already-deleted files, and check
its existing callers. The replacement regression itself must not require Git
staging; the release checker's existing tracked-inventory gate is unchanged.

Acceptance: the test passes before and after retirement by comparing installed
core with the candidate's actual inventory, not a fixed M23 file count. State
and config bytes remain unchanged by package replacement; obsolete nonmanifest
core is not retained. Verification: the focused replacement case and affected
helper tests, with lane discovery if a test module is added. Do not require a
new historical binary/archive, a live project upgrade, or another schema matrix.

<a id="tg-m23r-9"></a>

#### TG-M23R.9 — Runner Identity Compatibility

Scope: add only missing focused assertions to existing Runner runtime/gate and
schema-v21 history tests, reusing their disposable package/graph fixtures.
Exercise a valid changed manifest implementation identity, live-basis staleness,
and a completed historical Runner graph/Bundle retaining its captured identity.

Acceptance: package changes require the existing fresh live target basis;
historical completion still reads without rebinding to the installed digest.
No immutable observation/Bundle is rewritten or promoted. Verification: the
focused identity/live-stale/history cases. Reuse already sufficient cases rather
than duplicate them. The existing public Plan-to-`runner_pass` canary is run in
unit 11; do not add another native-process harness or change Runner behavior.

<a id="tg-m23r-10"></a>

#### TG-M23R.10 — Atomic Runtime And Authority Retirement

Scope: after units 1–9, remove `analysis_contracts.py`, `analysis_packet.py`,
`analysis_outbox.py`, `analysis_validator.py`, `analysis_renderer.py`,
`analysis_worker.py`, `codex_analysis_adapter.py`, `_analysis_windows_process.py`,
`_analysis_win32.py`, and the packaged `evidence_consumer.py`. Remove the now
superseded M23 dedicated tests/support, the two temporary M24 Analyzer blocks,
Analyzer-only `state_paths.py`/`state_resolver.py` names and fields, and their
manifest/test-lane entries. Refresh hashes for actually changed packaged files.

In the same reviewed revision, capture the retiring current Analyzer sections
with their exact source commit and non-authoritative banner into a new document
indexed by `docs/history/README.md`; do not alter old captures or archive runtime
code separately. Replace current M23 support/process/report contracts in
`docs/specification.md` and `docs/design.md` with the short Evidence
non-authority/compatibility rule and test-oracle ownership. Synchronize direct
current references in README, candidate release/install notes, plan, and
document-contract tests where required. Keep the existing authority registry;
add no new conditional owner or AGENTS contract inventory.

Acceptance: runtime removal, exact package/test inventory, history indexing,
and active-document switch form one coherent change, never separate committed
intermediate states. The retained reader tests and four M24 modules import and
pass; no live runtime consumer depends on a deleted module. Reserved vocabulary,
schema/data, supported config, and old analysis artifacts remain unchanged.
Verification: affected reader/M24/resolver/replacement/identity tests, document
and release contract checkers, test-lane check, and `git diff --check`. Tier 2
requires two independent reviews of that exact switch. Full suite belongs to
unit 11, not every preparatory Task. Fix only direct cutover defects here.

<a id="tg-m23r-11"></a>

#### TG-M23R.11 — Final Offline Acceptance

Scope: acceptance only of the exact retired candidate. Run the existing full
offline suite through `python -B tools/test_lanes.py --repo . --lane all`, plus
document/release contract, lane-inventory, and diff checks. Confirm the suite
includes the retained reader matrix, replacement/history coverage, and existing
public Plan-to-`runner_pass` Windows canary; included tests need no duplicate run.

Acceptance: all required local checks pass and two independent Tier 2 reviews
find no blocking regression or scope expansion. Report actual environment and
results without claiming GitHub CI or publication. Corrections return to their
owning unit through the existing workflow; this Task does not authorize broad
repair, weaker tests, skipped native acceptance, or new implementation. A
failure is evidence for bounded diagnosis, not authority to enlarge this plan.

Across the sequence, each Tier 1 unit needs one independent review and Tier 2
needs two. New test modules are assigned once in `tools/test_lanes.py` and the
partition is checked. Per-unit verification is focused on changed behavior;
the full suite is reserved for unit 11. No numeric file/line/time limit, repeated
whole-project audit, new mandatory worksheet, or speculative safety gate is
added to make a Task complete.

<a id="m23-schema-cleanup"></a>

### M23 Retirement Follow-Up — Schema Reservation Cleanup

This approved follow-up removes the retired Analyzer's unused vocabulary from
the current schema, not the shared Evidence tables or their valid records.
It follows TG-M23R.11 in the same `TG-M23R` sequential lane, at orders
120–190. The eight TG-M23S units below own future execution detail; the Task
database alone owns their status. Registration authorizes this plan and ready
Task records, not implementation, migration of the self-host database, Git
operations, external CI, installation, or release.

The existing TG-M23R.1–11 contracts remain unchanged. Their schema-v21 and
reserved-vocabulary preservation rule is the predecessor boundary, not a
prohibition on this separately approved follow-up. Current supported behavior
stays schema v21 until TG-M23S.7 activates the prepared schema v22 change and
synchronizes the applicable specification, design, and candidate documents.

#### Outcome And Bounded Compatibility

- Remove `derived_analysis` from the source-kind/relation CHECK allow-lists,
  `llm_derived` from assurance allow-lists, and `batch_analyzer` from producer
  allow-lists in current `evidence_references` and `criterion_evidence_links`.
  Remove corresponding unused current enum/order/dispatch reservations.
  Keep the tables, columns, valid source kinds, `deterministically_derived`,
  and the independent test-only Evidence reader.
- Preserve supported old-schema definitions and migration/read compatibility.
  Legacy DDL, rejection tests, and historical explanations may still name the
  retired values. No repository-wide zero-string test or unrelated schema,
  handoff, source-file, or document decomposition is part of this cleanup.
- Use one forward migration from complete schema v21 to v22. The physical
  rebuild includes the two Evidence tables and
  `completion_evidence_bundles`, whose source-version CHECK must admit new
  source-22/v2 Bundles. Restore their owned indexes/triggers and the coupled
  completion-cycle guard. No business column/table or Runner protocol is added.
- Preserve existing business rows, IDs, relations, provenance, completed
  history, and sealed Bundle payloads/source versions/digests. Existing Bundle
  files remain byte-identical. A regenerated index truthfully reports source
  schema 22 and may change bytes/digest while referencing those same old
  Bundles. New Bundles are source-22/format-2; index format 2, Bundle digest
  domains, Viewer snapshot 4, CLI shapes, and verification/review rules remain.
- Existing writers cannot create the retired values. Migration validates its
  actual source using the existing storage boundaries; unexpected reserved-value
  rows are rejected without deletion, conversion, or inferred replacement.
  An unowned index/trigger attached to one of the three rebuilt tables is
  rejected before rebuild rather than silently lost or replayed. Unrelated
  objects retain the existing preservation policy. No live-project census or
  manual cleanup is a prerequisite for implementing this migration.
- Reuse the established transaction, integrity/foreign-key validation,
  marker-last, rollback, and connection-setting restoration patterns. Rollback
  means logical schema/data preservation, not byte-identical SQLite files.
  Explicit setup retains the existing pre-migration backup and paired-rollback
  contract; ordinary commands never migrate or repair state.
- Preserve M24 manual/not-required/Runner branches, no-relaunch recovery,
  live implementation-drift invalidation, and self-contained historical Runner
  evidence. This is version compatibility, not a new Runner gate or safety
  framework. No Analyzer resurrection, model call, new runtime reader, CLI,
  config, normal-loop call, platform port, or general migration framework.

Preparation uses the existing explicit-connection helpers and isolated test
databases. Units 1–6 keep the public current-schema target and ordinary schema-21
behavior unchanged; they may extend narrow version-aware helpers for explicit
schema-22 tests, but introduce no rehearsal command, alternate state mode, or
parallel candidate runtime. Each packaged-code change refreshes its actual
manifest digests, and each new test module is classified once in the existing
test lanes. Tests for legacy schema 21 remain explicit old-schema tests rather
than being indiscriminately rewritten to expect 22.

All eight units are Tier 2 because they change schema, its coupled contracts or
write/gate boundaries, or final acceptance. Each requires two independent
reviews of its own target. Units 1–7 use focused checks for the changed boundary
plus applicable document/release/lane and diff checks. The existing full offline
suite runs in unit 8, not at every preparation boundary. Reuse sufficient
existing tests; add cases only for new version/rebuild behavior, not a fresh
cross-product of every legacy fault. No numeric size/time gate or repeated
whole-project audit is added.

<a id="tg-m23s-1"></a>

#### TG-M23S.1 — Schema-v22 Definitions

Input: the accepted M23 retirement and current schema-v21 definitions.
Scope: define the three v22 replacement tables, their owned indexes/triggers,
the coupled cycle guard, and exact schema-object recognition in `storage.py`.
Keep old version-specific SQL available for source admission and migration.
Output: a privately constructible, recognizable v22 physical schema with the
six reservation allow-lists narrowed and source-22/v2 Bundle admission.
Acceptance: focused temporary-database DDL tests prove the intended CHECK/FK/
index/trigger changes and rejection of the removed values, with unchanged
business columns and unchanged schema-21 definitions/public initialization.
No migration algorithm, public activation, or broad storage refactor.

<a id="tg-m23s-2"></a>

#### TG-M23S.2 — Stored Evidence And History Validation

Input: unit 1's exact schema definitions.
Scope: make existing storage-owned Evidence, Bundle, cycle, and Runner-graph
validators explicitly understand schema 22 with the same schema-21 semantic
rules and narrowed current vocabulary. Preserve old source-version validation.
Output: validation of a schema-22 database containing retained source-19/20/21
Bundles/history, including its existing storage-owned projection-basis capture,
without a public schema switch. New source-22 payload validation is unit 4.
Before unit 5 enables explicit v22 Task capabilities, storage maps physical v22
to the unchanged v21 stored-Task field/privacy capabilities; its container identity stays
22. This does not enable current Task lifecycle or Runner selection on v22.
Selected history retains the source-19/v1 null verification-basis tag as
non-Runner history; the subsequent Bundle validation still checks its sealed
source/version and complete basis without promoting it.
Acceptance: focused valid/corrupt fixture tests cover retained
manual and Runner history, source-version discrimination, reserved-value
rejection, and the existing no-promotion/no-privacy-upgrade boundary. Exact-21
guards must neither wrongly reject 22 nor silently skip its history checks.
Current Task lifecycle/Runner selection belongs to unit 5; no new gate matrix.

<a id="tg-m23s-3"></a>

#### TG-M23S.3 — Transactional Schema-v21 To v22 Migration

Input: units 1–2's physical definitions and stored-state validators.
Scope: implement the one explicit-connection migration/reentry path, rebuilding
only the three declared tables and coupled owned objects. Preserve accepted
source rows and unrelated objects, reject unexpected reserved rows or attached
unowned objects before rebuild, and record the new marker last.
Output: tested migration callable independently of public setup activation;
its populated fixtures retain source-19/20/21 Bundles, not new source-22 output.
The private connection helper retains the established foreign-key-off /
legacy-alter-on transaction pattern. It uses only `evidence_references_v21`,
`criterion_evidence_links_v21`, and `completion_evidence_bundles_v21` as
temporary table names, restores the coupled Evidence cycle guard, and compares
all existing business rows plus object SQL outside those replacements before
the marker-last validation/commit. Row preservation is checked again after the
marker, including effects of admitted unrelated triggers. Exact-v22 reentry
rejects attached unowned objects and temporary-name residue without repairing
either.
Acceptance: representative populated v21 Evidence/Runner history survives;
fresh construction reaches the same v22 owned schema; exact-v22 reentry is
validation-only; representative failures during rebuild and before commit
roll back schema/data and restore connection settings. Run quick/FK checks and
preservation assertions, including sealed Bundle payload/digest identity.
No reverse migration, data deletion/repair, live database edit, or exhaustive
fault-injection framework. Public migration dispatch remains unchanged here.

<a id="tg-m23s-4"></a>

#### TG-M23S.4 — Evidence Projection And Independent Reader Compatibility

Input: migrated v22 fixtures from unit 3 and the retained test-only reader.
Scope: extend `evidence_projection.py` and the independent test reader/support
to admit source-22/v2 Bundle/index material without a new format or domain,
including the coupled storage validation of new source-22 payloads.
Output: pure fixtures/builders generate and independently validate new
schema-22 Evidence; actual Task completion-writer integration is unit 5.
A new index can reference unchanged source-19/v1 and source-20/21/v2 Bundles.
Pure rendering admits the explicit schema-22 container while the public target
remains 21; index identity comes from that container and Bundle identity from
each sealed row. Shared stored-row validation admits source-22 Bundles only in
a physical schema-22 container, including selected-history reads, without
changing the current completion writer or adding per-Bundle schema reads.
Acceptance: focused producer-to-oracle tests cover manual/not-required/Runner
arms, truthful source versions, old Bundle bytes/digests, index refresh, and
representative version/digest rejection. The oracle does not import the
producer's semantic validator or SQLite. No report, runtime-reader route,
new publication mechanism, or requirement to preserve regenerated index bytes.

<a id="tg-m23s-5"></a>

#### TG-M23S.5 — Task Lifecycle And Runner Schema Compatibility

Input: units 2–4's schema-aware validation, migration, and Evidence encoding.
Scope: adapt existing current-state Task/verification/completion/Runner
repository paths to schema 22 and derive new Bundle source version from the
actual database. Remove exact-21 assumptions only where they represent the
same supported gate capability. Output: existing lifecycle and selector work
on explicit v22 test databases while ordinary CLI remains on v21.
Stored Task capabilities admit v22 directly, retiring unit 2's temporary
v22-to-v21 field-validation bridge while preserving the older v20 bridge.
The locked completion basis carries its validated physical schema version;
both the native JSON payload and stored Bundle use that same value. No caller
selects the version and no sealed historical Bundle is relabelled.
Acceptance: focused tests cover manual Receipt, not-required, qualifying Runner
pass, the existing fallback/blocking classifications, reopen/fresh targeting,
and retained historical Runner evidence. Reuse current gate matrices and
implementation-drift tests; do not add a native process harness or alter Plan,
process, cleanup, receipt, review, or completion semantics.

<a id="tg-m23s-6"></a>

#### TG-M23S.6 — Setup And Projection Consumer Compatibility

Input: units 1–5's explicit schema-22 database and lifecycle support.
Scope: prepare source-schema-22 validation/dispatch in setup, doctor, backup,
recovery, resolver/relocation, and Viewer helpers. Keep old source handling and
the public migration target at 21 until unit 7.
Output: isolated v22 consumer fixtures work through the existing boundaries.
The public current/newer-schema guards remain at 21. Tests initialize 21 and
privately migrate their disposable database before locally substituting only
the needed consumer schema constants to exercise the prepared 22 dispatch.
They do not replace validators or classifications, create another runtime or
state path, or claim public activation; an unmodified public entry still
rejects 22 until unit 7. Recovery retains the existing deferred Task-only
verification rejection after structural checks, and Viewer reuses its exact
validated-batch proof without changing snapshot v4 or current-artifact rules.
Acceptance: representative schema-22 backup/recovery, read-only diagnosis,
Viewer-v4, and relocation source-version tests pass with unchanged public
fields, candidate-local versus set-fatal error handling, config preservation,
and no-relaunch behavior. Reuse existing recovery and Viewer fixtures rather
than duplicating their complete matrices. No new maintenance policy, recovery
algorithm, artifact type, configuration, or live self-host setup.

<a id="tg-m23s-7"></a>

#### TG-M23S.7 — Atomic Current-Schema Activation And Residue Removal

Input: the independently verified outputs of units 1–6.
Scope: switch `SCHEMA_VERSION` and all supported setup migration/reentry
dispatch paths to 22, including the current early returns for source 20/21.
Remove remaining current-only Analyzer enum/order reservations while retaining
necessary legacy compatibility and negative tests. Synchronize the exact
affected specification/design sections, `plan.md` current decisions (not the
unchanged TG-M23R.1–11 contracts), README, CLI reference, candidate
release/install notes, manifest, and version-sensitive contract tests. Align
current-schema wording in `SKILL.md` and `references/task_workflow.md` without
changing their triggers, procedures, or normal-loop calls.
Output: one coherent current schema-22 candidate with no partial public cutover.
Acceptance: fresh setup, populated v21 upgrade, v22 no-write reentry, new
completion-to-Evidence/oracle, and Viewer/backup canaries pass; package and
document versions agree. Current definitions reject all three retired values;
valid evidence and old compatibility still work. This unit wires prepared
work and fixes direct integration defects, not a deferred migration/consumer
implementation backlog. No release, Git mutation, or real self-host migration
is authorized by this registration.

<a id="tg-m23s-8"></a>

#### TG-M23S.8 — Final Schema Cleanup Acceptance

Input: unit 7's coherent current candidate.
Scope: run `python -B tools/test_lanes.py --repo . --lane all` and the existing
document/release/lane/diff checks, including isolated package upgrade and
matched rollback coverage. Acceptance: all required local checks and two
independent reviews pass; existing Evidence/Task identities, old Bundle bytes,
the three supported configs, and historical Runner behavior are preserved.
Confirm the existing public Plan-to-`runner_pass` canary is included, without
duplicating its execution or claiming external CI/publication. Report remaining
legacy vocabulary by its compatibility owner, not as failed physical cleanup.
This is acceptance, not new implementation or permission to weaken a test;
direct defects follow the existing bounded repair/reconciliation workflow.
````
