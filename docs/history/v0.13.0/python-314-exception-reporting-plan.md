> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves the completed Python 3.14 exception-reporting
> execution plan, not current product behavior, implementation authority, live
> Task state, or evidence. Internal words such as current, approved, accepted,
> active, or implemented describe only the captured revision. This file cannot
> fill a current authority gap or satisfy a current gate.

# Python 3.14 Exception Reporting Execution Plan Capture

- Source path: `plan.md`
- Source commit: `aebca33e8f33e97bc341ebc51ff6763ce414e708`
- Capture unit: `TG-RMAP.5`
- Current replacements:
  [runtime and platform contract](../../specification.md#package-runtime-and-generated-state),
  [JSON and exit-status contract](../../specification.md#json-text-limits-and-exit-status),
  [privacy and stable-error contract](../../specification.md#privacy-safety-and-stable-errors),
  [runtime module design](../../design.md#runtime-module-boundaries),
  [independent Evidence reader design](../../design.md#test-only-independent-evidence-reader),
  [validation and CI design](../../design.md#validation-and-test-design),
  [test-lane policy](../../../tools/test_lanes.py),
  [development checks](../../../README.md#development-checks), and
  [Python 3.14 reporting regression](../../../tests/test_python314_exception_reporting.py).
  Use the public CLI for live Task state and evidence.
- Capture purpose: preserve the exact final completed TG-PY314.1 through
  TG-PY314.3 sequence, finite exception inventory, compatibility boundary, and
  one-time verification narrative from the committed source.

The exact source section begins below and ends before the next plan anchor.

````markdown
<a id="python-314-exception-reporting"></a>

### Approved Follow-Up — Python 3.14 Exception Reporting

The user approved task registration for the bounded exception-compatibility
correction below. Python 3.14 unittest reporting can fail while updating a
chained frozen exception's traceback. In the observed storage boundary,
`TaskValidationError` is the frozen cause; `StorageError` itself is not frozen.
The intended outcome is to report the original error and continue subsequent
tests, not to suppress an error or convert a failing test into a pass.

Keep the specification's "Package, Runtime, And Generated State", "JSON, Text,
Limits, And Exit Status", and "Privacy, Safety, And Stable Errors" unchanged.
The design's "Runtime Module Boundaries", "Test-Only Independent Evidence
Reader", and "Validation And Test Design" govern ownership and verification.
This is not a Python-version increase, privacy-policy revision, schema change,
CLI/JSON change, Runner behavior change, or exception-framework redesign.
Registration alone starts no implementation, Git operation, live Runner,
configuration publication, setup/migration, external CI, or release.

The finite scope is 15 production exception classes and two test-only exception
classes. Use ordinary traceback-compatible dataclasses, following the existing
`VerificationRunnerPlanError` precedent. Preserve class identities, inheritance,
constructor fields/defaults, field equality, string rendering, sanitized error
fields, and exception chaining. Also preserve `ArtifactLockError.contended`
and `VerificationRunnerRuntimeError.handle_cleanup_state` / `handles_closed`.
Removing frozen exception-field enforcement and its generated hash is intended;
current inspection found no hash-container or immutability dependency on these
errors. Do not introduce unsafe hashing or a new exception base to preserve an
unused property. Normal immutable data records remain frozen. Keep the Evidence
oracle/codec independent from storage and production semantic validators.

Use sequential lane `TG-PY314`, orders 10, 20, and 30. The first unit establishes
the actual-route regression and a small parameterized test pattern; later units
extend it with their own exception cases. Each unit leaves its own errors fixed
and has its own attributable tests, manifest synchronization, and one independent
Tier 1 review. Later coverage never substitutes for an earlier unit's gate.
The lane has no dependency on TG-PMC, TG-RNC, or future cleanup. Shared-file
edits must preserve those Tasks' unrelated work; no other lane is paused or
reordered by this registration. The completed RC-CI.2 is not reopened.

Common verification is direct traceback compatibility, chained-error reporting
through standard unittest APIs, continuation to a following test, and unchanged
error attributes. A synthetic inner test must still be reported as an error;
the enclosing regression asserts that behavior without leaving a failing test
in standard discovery. Avoid exact traceback-text matching, standard-library
patches, swallowed failures, and a new generic test framework. Use an injected
sanitized validation failure at the actual storage helper boundary rather than
making a short diagnostic phrase's rejection a permanent privacy requirement.

Run the focused reporting regressions on Python 3.14. Run the same small tests
on 3.12 if already available; otherwise report that local coverage limitation
and retain the existing CI policy, without downloading an interpreter or adding
a new platform gate. Put new small reporting coverage in the existing `fast`
lane so the current PR policy exercises it on 3.14. Update/check lane membership
if a new module is added. Each unit also runs focused adjacent tests, the release
contract check, and `git diff --check`; run the document checker if documents
change. No repeated full-suite, performance campaign, native-process matrix,
external CI dispatch, or live-project Runner run is added to these local gates.
The existing release-candidate aggregate CI requirement remains separate.

<a id="tg-py314-1"></a>

#### TG-PY314.1 — Task And Review Exception Compatibility

Input: current Task/review exceptions and the existing Runner Plan exception
regression as a pattern. Scope/output: fix `TaskValidationError` and
`TaskRepositoryError` in `tasks.py`, `CompletionEvidenceError` in `completion.py`,
`HandoffError` in `handoffs.py`, `ReviewPacketError` in `review_packet.py`,
`ReviewProvenanceError` in `review_provenance.py`, and `VerificationReceiptError`
in `verification_receipts.py`; synchronize affected release-manifest digests.
Add the actual `_validate_evidence_ledger_stored_privacy` chained-error regression
and the seven exception cases in a small reporting test module or existing
focused tests. No storage or privacy algorithm change is required.
Acceptance: the original sanitized storage error is reported, a following test
runs, and the seven exception classes meet the common compatibility checks.
Verification: reporting cases plus focused existing Task validation, completion,
handoff, review-packet/provenance, and Receipt error tests; common checks and
one independent Tier 1 review. Later units' still-frozen errors are not this
unit's completion condition.

<a id="tg-py314-2"></a>

#### TG-PY314.2 — Evidence And Artifact Exception Compatibility

Input: TG-PY314.1's verified reporting pattern. Scope/output: fix
`ArtifactLockError` in `artifact_lock.py`, `ArtifactManifestError` in
`artifact_manifest.py`, `EvidenceLedgerError` in `evidence_ledger.py`,
`EvidenceProjectionError` in `evidence_projection.py`, and `ViewerError` in
`viewer.py`; also fix test-only `EvidenceConsumerError` in
`tests/evidence_reader_oracle.py` and `EvidenceCodecError` in
`tests/evidence_reader_codec.py`. Extend reporting cases and synchronize the
five packaged modules' manifest digests. Do not alter file publication, locking,
Bundle validation, privacy policy, Viewer behavior, or oracle independence.
Acceptance: all seven added cases meet the common checks, including unchanged
lock-contention classification and test-reader rejection attributes.
Verification: reporting cases and focused existing artifact, Evidence, Viewer
error-path, and independent reader/codec tests; common checks and one independent
Tier 1 review. No exhaustive I/O race or recovery matrix is added.

<a id="tg-py314-3"></a>

#### TG-PY314.3 — Git And Runner Exception Compatibility

Input: the reporting coverage from TG-PY314.1 and TG-PY314.2. Scope/output: fix
`GitSnapshotError` in `git_snapshot.py`, `VerificationRunnerGitError` in
`verification_runner_git.py`, and `VerificationRunnerRuntimeError` in
`verification_runner_runtime.py`; extend reporting cases and synchronize their
manifest digests. Preserve cleanup-state fields/properties, Git observation,
Runner qualification, and normal package-identity invalidation of old evidence.
Acceptance: the three new cases pass and the small aggregate reporting test
covers the full 15-production/two-test-only set with earlier cases still passing.
The already-compatible `VerificationRunnerPlanError` regression remains valid;
normal frozen data-record assertions still pass. No broad exception or module
refactor is included. Verification: aggregate reporting cases, focused existing
Git snapshot/Runner Git/runtime error and cleanup-state tests, existing Runner
Plan traceback and relevant package-identity regressions, common checks, and
one independent Tier 1 review. Report the originating handoff's fix status but
do not withdraw it without explicit user direction.
````
