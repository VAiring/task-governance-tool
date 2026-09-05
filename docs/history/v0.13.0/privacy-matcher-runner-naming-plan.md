> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves the completed Privacy matcher corrections, manual
> diagnostic quotation policy and implementation, and Runner naming execution
> narrative, not current product behavior, implementation authority, live Task
> state, or evidence. Internal words such as current, approved, accepted,
> active, or implemented describe only the captured revision. This file cannot
> fill a current authority gap or satisfy a current gate.

# Privacy Matcher And Runner Naming Execution Plan Capture

- Source path: `plan.md`
- Source commit: `9669adf5a4519ef6dce116dbeb57d35e51df026b`
- Capture unit: `TG-RMAP.6`
- Current replacements:
  [privacy and stable-error contract](../../specification.md#privacy-safety-and-stable-errors),
  [privacy and failure-boundary design](../../design.md#privacy-safety-and-failure-boundaries),
  [trusted-local Runner contract](../../specification.md#trusted-local-verification-runner),
  [Runner parent-service design](../../design.md#runner-parent-service-and-audit-graph),
  [Task privacy implementation](../../../task-governance-tool/scripts/task_governance_tool/tasks.py),
  [independent Evidence privacy oracle](../../../tests/evidence_reader_oracle.py),
  [Runner parent-service implementation](../../../task-governance-tool/scripts/task_governance_tool/verification_runner_service.py),
  [privacy regressions](../../../tests/test_task_validation.py), and
  [Runner service regressions](../../../tests/test_m242_runner_service.py).
  Use the public CLI for live Task state and evidence.
- Capture purpose: preserve the complete pre-retirement TG-PMC.1 through
  TG-PMC.4 policy and implementation narrative, TG-RNC.1 execution contract,
  finite boundaries, and one-time verification gates from the committed source.

The exact source section begins below and excludes only the separator blank
before the next plan anchor.

````markdown
<a id="privacy-matcher-corrections-and-runner-naming"></a>

### Approved Follow-Up — Privacy Matcher Corrections And Runner Naming

The user approved registration of the bounded follow-up below. These are
execution contracts, not a live status record. Current
privacy behavior remains owned by the specification's "Privacy, Safety, And
Stable Errors" and the design's "Privacy, Safety, And Failure Boundaries".
Registration does not start implementation or authorize Git operations,
setup/migration, live Runner execution, configuration publication, or release.

Retain Evidence JSON automatic projection and its external-consumer purpose,
Review provenance, Viewer reload state, Handoff delivery reservations for a
future Issue Skill, active Runner cleanup/gates and historical compatibility,
Setup/relocation/recovery, and Effort Advisory. None is a cleanup target here.
Do not extend this work into a schema change, platform port, common security
framework, new setting, LLM classifier, normal-loop call, or routine approval.

TG-PMC.1, TG-PMC.2, and TG-PMC.4 form lane `TG-PMC` at orders 10, 20, and 30;
each later implementation preserves the earlier accepted cases. TG-PMC.3 and
TG-RNC.1 are independently selectable optional-kind Tasks, not predecessors or
final integration gates.
Each implementation unit includes its own affected documentation, tests, and
package-manifest synchronization; do not postpone those to another unit.
Use synthetic examples and isolated test state, not real private content or
the live Task database, for regression tests. Existing valid stored content,
sealed Bundle bytes, and historical evidence must remain readable without
rewriting them. Verification Receipt fields and automatic Runner stream
retention remain unchanged throughout.

Each privacy unit is Tier 2 and requires two independent reviews. TG-RNC.1 is
Tier 1 and requires one independent review for the narrow name change and its
package-identity impact. Use focused checks attributable to each unit, plus
the document/release contract and diff checks where affected. Update and check
the test-lane inventory only if its membership changes. This follow-up adds
no repeated full-suite, performance, platform, or exhaustive adversarial gate;
the repository's existing release/CI policy remains separate and unchanged.

<a id="tg-pmc-1"></a>

#### TG-PMC.1 — Nonsecret Numeric Metadata

Input: the current shared privacy guard and independent Evidence test oracle.
Scope/output: correct false rejection of the three numeric metadata keys
`max_tokens`, `token_count`, and `password_length`. Define their exact numeric
syntax in the affected current privacy documentation, and update the matcher,
directly coupled tests, independent oracle policy, and package manifest in the
same unit. Representative accepted values are `max_tokens=4096`,
`token_count=1024`, and `password_length=12`.
Acceptance: these examples survive caller validation, stored readback, and a
representative completion-to-Evidence/oracle round trip. Existing credential
and dump regressions still reject; a recognized metadata span does not hide a
credential elsewhere in the input. Do not allow arbitrary similarly suffixed
keys or arbitrary nonnumeric values. Ambiguous token assignments and status
words are not made safe merely because the surrounding sentence describes a
fix. Do not globally weaken the common guard or add per-project exceptions.
Verification: focused `test_task_validation` coverage and the relevant existing
Evidence round-trip/oracle tests, including mixed allowed-and-rejected input;
affected document/release contract checks and `git diff --check`.

<a id="tg-pmc-2"></a>

#### TG-PMC.2 — Fully Redacted Credential Examples

Input: the matcher and accepted numeric cases from TG-PMC.1.
Scope/output: allow a complete literal `<redacted>` placeholder in a documented
credential-example form, including `Authorization: Bearer <redacted>`, while
continuing to inspect all remaining text. Update the current privacy docs,
shared guard, directly coupled tests, independent oracle policy, and manifest
together. This is recognition of already-redacted text, not an automatic
redactor or an invitation to supply a real credential.
Acceptance: the documented fully redacted example passes validation, readback,
and a representative Evidence/oracle round trip. Partial redaction, content
appended to the credential value, and a separate detectable credential remain
rejected. An arbitrary angle-bracket word, status word, or introductory verb
does not grant a whole-input bypass. TG-PMC.1 cases and existing unrelated
privacy/legacy-read behavior remain valid.
Verification: focused placeholder-boundary and mixed-content tests alongside
the existing Task validation and relevant Evidence round-trip/oracle tests;
affected document/release contract checks and `git diff --check`.

<a id="tg-pmc-3"></a>

#### TG-PMC.3 — Short Diagnostic Quotation Policy

Input: the approved direction to admit limited manually entered diagnostic
quotations instead of rejecting solely on a stream heading. The exact allowed
fields, length/line boundary, and quotation format have not been decided.
Scope/output: produce one small policy recommendation in this pending section,
with concrete allowed/rejected examples, those unresolved choices, and the
exact current documentation/validator consumers that would need alignment.
Use `Investigate failure: stderr: permission denied` as a representative
useful case. Explain the benefit and residual disclosure risk. Any proposed
new cap must be justified against existing field limits and concrete needs,
not added merely to make the policy stricter.
Acceptance: the recommendation distinguishes manual short quotations from
automatic Runner capture and Verification Receipts; retains rejection of
detected credentials, log blocks, stack traces, environment dumps and large
diffs; and requires no new setting, classifier or routine approval. List the
remaining user decision explicitly. The Task is complete when that bounded
proposal is documented and reviewed; it need not wait for implementation.
This is design-only: do not activate changed privacy rules or create an
implementation Task automatically. A later explicit registration event based
on the accepted policy owns implementation. TG-PMC.1, TG-PMC.2 and TG-RNC.1
do not wait for this decision.
Verification: check the examples and affected-field inventory against current
validators; document-contract check and `git diff --check`; two independent
reviews of scope, tradeoffs and achievable acceptance, not a new test harness.

##### Active Contract — Manual Diagnostic Quotations

TG-PMC.4 activates the user-approved two-field, single-line manual diagnostic
quotation policy. Current behavior is owned by the specification's
[Privacy, Safety, And Stable Errors](docs/specification.md#privacy-safety-and-stable-errors)
and the design's
[Privacy, Safety, And Failure Boundaries](docs/design.md#privacy-safety-and-failure-boundaries).
Those owners define the exact form, privacy checks, consumer boundaries, and
non-expansion rules; the execution contract below preserves this Task's scope,
acceptance matrix, and gates without duplicating the active product contract.

<a id="tg-pmc-4"></a>

#### TG-PMC.4 — Manual Diagnostic Quotation Implementation

Input: the accepted manual diagnostic quotation policy above and the completed
TG-PMC.1/TG-PMC.2 privacy corrections. Scope/output: activate the exact
two-field, one-line form in the current privacy sections of the specification
and design, `tasks.py`'s common caller/stored validation, the existing
authority-snapshot and Review Packet consumer paths, and the independent
Evidence reader policy. Add focused caller, stored-read,
completion-to-Evidence, Review Packet, and independent-reader tests, and
synchronize affected package manifest digests and the existing test-lane
inventory if membership changes.
Do not create a schema migration, a new configuration or mode, an automatic
redactor, a semantic classifier, or a shared implementation dependency between
the production matcher and independent test oracle.

Acceptance: the two accepted examples pass unchanged as Task `title` and
`description` through caller validation, stored readback, Review Packet where
applicable, and a representative completion-to-Evidence/oracle round trip. No
other field gains this quotation exception: every field that currently treats
raw-output headings strictly continues to reject the same text, while fields
outside that strict set retain their existing behavior. Bare headings, multiple
headings, and other malformed candidates receive no new quotation exception;
pre-existing field-specific behavior, including the benign-title allowance,
remains unchanged. Empty context or quotation, line boundaries, trailing line
breaks, detected credentials, stack frames, log blocks, environment dumps, and
raw/large diffs remain rejected in the governed title/description form. The full
original field and extracted body are both checked, with no recursive quotation
exception.
Existing numeric metadata,
fully redacted Bearer, ordinary multiline description, strict raw-output, and
legacy-read cases remain valid. Current valid stored bytes and sealed Evidence
bytes are not rewritten. Public CLI/JSON shape, error codes/messages, field
limits, Verification Receipts, Runner capture, review/completion gates, and
target-project mutation behavior are unchanged.

Verification: run focused Task/privacy, stored consumer, Review Packet,
completion-to-Evidence, and independent reader/oracle tests containing the
complete accepted/rejected matrix; run the document contract, release contract,
test-lane check when membership changes, and `git diff --check`. Use synthetic
content and isolated test state. Do not add a full-suite, platform matrix,
adversarial campaign, performance gate, live Runner, external CI dispatch, or
new test framework to this unit. This privacy behavior change is Tier 2 and
requires two independent exact-target reviews with no blocking findings.

<a id="tg-rnc-1"></a>

#### TG-RNC.1 — Optional Runner Internal Entrypoint Name

Input: the existing optional Runner service entrypoint and its CLI/test callers.
Scope/output: rename `set_review_target_with_shadow_runner` to
`set_review_target_with_optional_runner` in the service, its export list, CLI
call site and directly referring tests; synchronize the package manifest.
Acceptance: callers use the new name with unchanged public CLI, JSON, schema,
gate behavior and persistence. Preserve historical migration names, sandbox
tables/fields, digest domains/keys and old fixtures; do not chase every
occurrence of "shadow" or "sandbox". Package-byte identity changes continue to
make prior Runner qualification stale by the existing rules while keeping old
evidence readable; do not bypass that check or re-run the live project here.
Verification: exact-symbol reference check, focused tests in the existing
Runner Plan CLI/service/gate/acceptance modules and the existing identity-change
regression; release/document contract checks and `git diff --check`. Do not
add a new native-process or sandbox qualification matrix for a name change.
````
