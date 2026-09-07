# Runner Execution Implementation Design

This document owns the trusted-local Runner module registry, target/Plan,
process, cleanup, and parent-service structure delegated by the
[implementation design](design.md#trusted-local-runner-architecture), for the
behavior in the [Runner execution specification](runner-execution-specification.md).
The [Runner Plan authoring/control design](runner-plan-authoring-design.md)
remains separate. Shared [runtime ownership](design.md#runtime-module-boundaries),
[CLI/serialization](design.md#public-cli-and-serialization),
[connection/transaction](design.md#journal-and-connection-rules),
[schema-v21 Runner gate basis](design.md#schema-v21-runner-gate-basis-design),
[current schema-v22 integration](design.md#current-schema-v22-manual-receipt-arm-and-bundle-integration),
[post-commit coordination](design.md#post-commit-coordinator),
[privacy/failure](design.md#privacy-safety-and-failure-boundaries), and
[global validation/test design](design.md#validation-and-test-design) retain
their existing owners, routed by the [authority index](authority.md).

<a id="trusted-local-runner-architecture"></a>

## Trusted-Local Runner Architecture

This is the current explicit-opt-in adapter architecture for a repository the
user already trusts. Eligibility remains deny-by-default and
binds the current Task, Contract, verification criterion, exact target, and a
project-owned fixed plan. Untrusted, external, unsupported, or visual-only
targets route to manual Verification Receipt handling without starting a process.
A launch uses an absolute executable, literal argv, `shell=false`, no
PATH lookup, a closed credential-excluding environment, and an exact private
materialization. The target working tree is never the execution root and
receives no copy-back.

The `value_model` is dependency-pure and legacy-stable. It preserves the opaque
Runner-policy seal and closed compatible record shapes and uses caller-token
identifiers. No legacy storage, Evidence provider/policy, or fixed-policy shim
remains an active consumer.

### Closed Runner-Slice Module Registry

The table is the complete Runner-slice layer registry. `Allowed Runner-layer
imports` names the only forward edges between these layer identifiers;
imports within one row are same-layer. Standard-library imports and existing
shared primitives outside this exact module set transfer no listed ownership
and may not import back into a higher Runner layer. Because `cli.py` is shared
by all public leaves, its unrelated pre-existing imports are outside the
Runner route; its Runner dispatch has only the `cli -> service` edge.

| Layer id | Exact owned modules | Sole Runner responsibility | Allowed Runner-layer imports | Forbidden responsibility or reverse edge | Current boundary |
|---|---|---|---|---|---|
| `cli` | `cli.py` | Parse and format the existing public surface and dispatch the Runner route to the parent service. | `service` | No Runner eligibility, authority, persistence, process, native, or cleanup decision; no Runner dispatch to `process_adapter` or `os_adapter`. | Direct process/native CLI branches are physically absent. |
| `service` | `verification_runner_service.py` | Parent orchestration; sole ownership of opt-in and eligibility, Task/Contract/criterion freshness, canonical repository coordination, target/plan selection, Evidence and terminal persistence, maintenance/recovery coordination, and final cleanup acceptance. | `repository`, `target_plan`, `value_model`, `runtime_identity`, `lifecycle`, `process_adapter` | No OS mechanics and no delegation of authority, business gates, terminal persistence, or cleanup acceptance to a child layer. | It is the sole business and cleanup-acceptance owner. |
| `repository` | `storage.py`, `sqlite_connection.py`, `project_binding_repository.py`, `backup_metadata_repository.py`, `schema_verification_runner.py`, `tasks.py`, `stored_task_validation.py`, `task_values.py`, `contracts.py`, `contract_content.py`, `reviews.py`, `verification_receipts.py`, `completion.py`, `evidence_ledger.py`, `evidence_projection.py`, `evidence_publication.py`, `maintenance.py` | Canonical SQLite, Task/Contract/review/completion state, Evidence, and maintenance repositories and business gates invoked only by the parent service. | `value_model` | No process launch; no import of `process_adapter` or `os_adapter`; no filesystem cleanup ownership. | Schema/Evidence compatibility stays repository-owned with no reverse edge. |
| `target_plan` | `artifact_manifest.py`, `verification_runner_git.py`, `verification_runner_plan.py` | Parent-invoked exact target observation/materialization and fixed-plan decode/validation. | `repository`, `value_model` | No CLI policy, canonical database ownership, completion decision, trusted-code or verification-command launch, terminal publication, or cleanup acceptance. | Target/plan code is read-only until parent-owned materialization. |
| `value_model` | `verification_runner.py` | Pure closed Runner identifiers, bounded codes, value validation, and domain encoding used across the boundary. | none | No I/O and no import of CLI, service, repository, persistence, target, runtime, lifecycle, process, native, or business-gate modules. | The module is dependency-pure and has no compatibility shim consumer. |
| `runtime_identity` | `verification_runner_runtime.py`, `_verification_runner_executable_win32.py`, `self_status.py` | Parent-invoked fixed executable and package-integrity observation. | `repository`, `value_model` | No process launch, canonical database ownership, business gate, terminal publication, or cleanup acceptance. | Candidate-only runtime material is physically absent. |
| `lifecycle` | `verification_runner_lifecycle.py` | Parent-requested creation, inventory, quarantine, removal, and absence proof for the one owned private attempt tree. | none | No process start, Job/stdio/handle ownership, SQLite, Evidence, business gate, terminal publication, or final cleanup acceptance. | Profile/recovery alternatives are physically absent. |
| `process_adapter` | `verification_runner_process.py` | Consume the closed request, establish the Job before trusted code, enforce process/resource/output/time bounds, drain and discard output, terminate and wait for process-tree zero, close handles, and return the closed result. | `value_model`, `os_adapter` | No canonical state or target-tree cleanup; no import of CLI, service, repository, storage, Task, Contract, review, Evidence, completion, setup, backup, maintenance, or another business gate. | Candidate/AppContainer/profile/ACL branches and business-freshness callbacks are absent. |
| `os_adapter` | `_verification_runner_win32.py` | Thin Windows Job, process, stdio, accounting, termination, wait, and handle primitives. | `value_model` | No parent policy, repository, persistence, gate, cleanup acceptance, LPAC/AppContainer/profile/ACL/ETW/registry-recovery module, or reverse import. | Only thin native primitives remain. |

The complete inter-layer edge set is therefore exactly:

```text
cli -> service
service -> repository
service -> target_plan
service -> value_model
service -> runtime_identity
service -> lifecycle
service -> process_adapter
repository -> value_model
target_plan -> repository
target_plan -> value_model
runtime_identity -> repository
runtime_identity -> value_model
process_adapter -> value_model
process_adapter -> os_adapter
os_adapter -> value_model
```

An unlisted edge is forbidden, and the graph has no cycle. In particular,
repository/persistence code never imports or launches the process or OS
adapter; the process and OS adapters never open the canonical database or
state resolver and never import a parent or business-gate module. `basis_is_current`
or an equivalent Task/Contract/criterion/target freshness callback is a
business-gate reverse edge; the service performs those checks before launch,
between bounded adapter calls where applicable, and after the returned result.

### Target-Plan Implementation

The `target_plan` registry row owns
`verification_runner_git.py` and `verification_runner_plan.py`; it does not add
or change CLI dispatch, the parent service, SQLite, Evidence, completion,
runtime identity, lifecycle, process, or native behavior. The modules may use
the existing read-only Git/artifact primitives, pure Evidence canonical JSON,
shared physical-path primitives, and `verification_runner.py` constants. They
must not import a higher Runner layer or any process or OS adapter.

`verification_runner_git.py` owns these immutable in-process values and calls:

```
RunnerTargetEntry = relative_posix_path, mode, object_id
RunnerTargetObservation = artifact, object_format, entries,
  target_material_digest
RunnerMaterialization = target, object_sizes, total_bytes,
  target_material_digest
MaterializedRunnerTarget = target_material_digest, entry_count,
  directory_count, total_bytes

observe_staged_runner_target(repo)
observe_commit_runner_target(repo, revision)
preflight_runner_material(repo, observation)
materialize_runner_target(repo, material, destination)
```

Observation reuses the established stable stage-zero index or exact commit
capture, then captures the complete ordered leaf set twice around a repeated
artifact observation. It admits only modes `100644` and `100755`, validates the
portable path and case/normalization collision set, derives all directories,
and never selects a plan from target material. The target digest is
`sha256(TARGET_MATERIAL_DOMAIN || canonical_json(target kind/value/base,
object format, ordered entries))`, where
`TARGET_MATERIAL_DOMAIN` is
`taskgov-verification-runner-target-material-v1\0`.

Preflight batch-checks every distinct target object as a blob, binds its exact
size, enforces the 10,000-file, 30,000-derived-directory (excluding the supplied
destination root), depth-64, and 512-MiB target bounds, and repeats target
capture before returning. It never reads or identifies a plan.
Materialization accepts only that preflight value and an existing
empty physical destination. It creates derived directories and regular files
exclusively, streams each blob with the established bounded Git reader,
recomputes the Git object ID including its blob header, and performs a bounded
no-follow inventory/hash comparison against the complete admitted set. A final
target recapture must still match. It never reads working-tree file content,
extracts an archive, follows a link/reparse, replaces an entry, copies back, or
launches target code. Failure leaves cleanup acceptance to the separate
`lifecycle` owner.

The parent-created canonical attempt target may be beneath the governed
repository because both the ordinary project-scoped package and the self-host
package keep their ignored generated state there. That lexical relationship
does not make the repository root the execution destination or authorize an
arbitrary repository write: materialization still accepts only the supplied
already-owned empty physical directory, rejects a destination equal to or
containing the repository, and confines every exclusive write and inventory
check to that destination. The `lifecycle` owner remains responsible for its
creation and cleanup.

`verification_runner_plan.py` owns:

```
VerificationRunnerPlanSource = raw_blob, raw_digest
VerificationRunnerPlanStep = ordinal plus normalized StepV1,
  with shell=false and path_lookup=false
VerificationRunnerPlanResolution = plan_state, route, reason,
  plan_blob_object_id, plan_raw_digest, plan_id, plan_version,
  plan_semantic_digest, selected_entry_digest, coverage, steps

capture_verification_runner_plan(repo, physical_package_root)
  -> VerificationRunnerPlanSource | None
resolve_verification_runner_plan(source,
  task_id, contract_revision, verification_expectation_digest,
  verification_criterion_digest)
```

Capture reads only `config/verification-runner.json` beneath the already
resolved physical package. An absent config directory or absent file returns
`None`, not an empty source; an alias, reparse, non-regular file, over-bound
file, link count other than one, identity change, or read uncertainty blocks.
Before and after the stable
read, one fixed shell-free Git check requires the exact repo-relative plan path
to have no literal case-insensitive current-index match and the physical
spelling to match effective ignore policy. A Git failure, timeout, unexpected
output, non-ignored result, or registered case variant blocks; the check never
writes Git state.
The file is independent of the selected Git target and stays excluded from the
release manifest. A same-named target entry is not consulted. The resolver
decodes strict UTF-8 JSON with duplicate-member, float, non-finite,
unknown-member, and type rejection. It validates the exact [Runner Plan shape](runner-execution-specification.md#eligibility-plan-and-materialization)
and only the plan-known scalar/collection bounds before selection. Final
Windows quoting, fixed executable/bootstrap insertion, materialized absolute
paths, and the `command_line_utf16_units` bound remain process-request
admission and are not evaluated by this module. Raw bytes use ordinary
labeled SHA-256. Canonical normalized plan and selected-entry values use the
domains `taskgov-verification-runner-plan-v1\0` and
`taskgov-verification-runner-plan-entry-v1\0`. `trusted_local = true` plus one
exact basis selects `plan_state=runner`, `route=runner`; false opt-in, absence,
or no current-Task entry is a closed manual fallback; a current-Task mismatch,
duplicate basis, ambiguity, or malformed input raises one sanitized bounded
plan error. Successful resolutions use exactly the [plan resolution matrix](runner-execution-specification.md#eligibility-plan-and-materialization):
`absent/m21_fallback/plan_absent`,
`disabled/m21_fallback/trusted_local_disabled`,
`no_match/m21_fallback/plan_entry_absent`, or
`runner/runner/null`. The absent case has no raw, id, version, semantic, or
selected-entry value. The two present-plan fallbacks retain raw/id/version/
semantic identity but no selected entry; every fallback has
`coverage=not_applicable` and empty steps. The runner case has those plan
identities plus a selected-entry digest, `coverage=full`, and one through 16
steps. The existing nullable `plan_blob_object_id` stays null in every case;
raw and semantic digests provide the physical-plan binding. Arguments and
private values remain in-process only and are not members of the resolution
digest or any durable projection.

The integration boundary assembles actual plan-resolution and target-
materialization outputs into the existing pure
`resolution_idempotency_digest`. That seal already binds Task/Contract,
authority/criterion, review-target kind/value/base/generation, target material,
and plan raw/semantic/selected-entry identities without including plan bytes,
argv, or steps. `target_plan` does not import or invoke the parent service;
the parent service remains the owner of orchestration, persistence, and
dispatch consumption.

### Typed Process Value Boundary

The following are logical immutable in-process records, not a public schema or
implemented transport. Their member sets are closed:

```text
RunnerProcessRequestV1 = version, attempt_id, executable,
  materialized_root, scratch_root, clean_environment, steps, cancel_signal
RunnerProcessStepV1 = ordinal, step_id, mode, entrypoint, argv, cwd,
  shell, path_lookup, timeout_seconds, cpu_seconds, memory_mib,
  process_limit, output_byte_limit
RunnerProcessResultV1 = version, attempt_id, outcome, reason, launch_state,
  failed_step_ordinal, duration_ms, cpu_time_ms, peak_job_memory_bytes,
  total_process_count, process_zero, handles_closed, raw_output_discarded,
  steps
RunnerProcessStepResultV1 = ordinal, outcome, reason, launch_state,
  cpu_time_ms, peak_job_memory_bytes, total_process_count
RunnerPrivateTreeResultV1 = attempt_id, state
```

The closed scalar, path, and collection bounds are exactly:

```text
RunnerProcessBoundsV1:
  request_version = 1
  accepted_plan_blob_utf8_bytes <= 65536
  attempt_id = ASCII /tg_verification_runner_attempt_[0-9a-f]{16}/ (47 bytes)
  identifier = ASCII /[a-z0-9][a-z0-9._-]{0,63}/ (1..64 bytes)
  result_code = ASCII /[a-z][a-z0-9_]{0,63}/ (1..64 bytes)
  absolute_path = well-formed Unicode, absolute normalized Windows path, no NUL or Unicode Cc, no "." or ".." segment, 1..4096 UTF-8 bytes and 1..4096 UTF-16 code units
  relative_path = "." or 1..32 "/"-separated ASCII /[A-Za-z0-9_][A-Za-z0-9._-]{0,127}/ components, no "." or ".." component, total 1..512 bytes
  script_entrypoint = non-dot relative_path ending in ".py"
  module_entrypoint = 1..16 "."-separated ASCII /[A-Za-z_][A-Za-z0-9_]{0,63}/ components, total 1..512 bytes
  literal_arg = well-formed Unicode with no Unicode Cc, 0..4096 UTF-8 bytes and 0..4096 UTF-16 code units
  path_ownership = executable is a parent-verified fixed absolute package-runtime identity outside materialized_root and scratch_root with no PATH lookup; materialized_root and scratch_root are distinct target and scratch children of one owned attempt root; no symlink or reparse traversal
  resolved_relative_path = every entrypoint and cwd resolves beneath materialized_root
  step_count = 1..16; argv_count_per_step = 0..64
  timeout_seconds = 1..900; total_timeout_seconds = 1..1800
  cpu_seconds = 1..900; memory_mib = 64..2048; process_limit = 1..32
  output_byte_limit = 1048576
  command_line_utf16_units <= 24576 after exact Windows quoting and fixed bootstrap insertion
  clean_environment_entry_count = 11; clean_environment_value_utf8_bytes = 1..4096
  clean_environment_keys = APPDATA, HOME, LOCALAPPDATA, PYTHONDONTWRITEBYTECODE, PYTHONNOUSERSITE, PYTHONUTF8, SystemRoot, TEMP, TMP, USERPROFILE, WINDIR
  clean_environment_paths = APPDATA=scratch_root/roaming; HOME=USERPROFILE=scratch_root/home; LOCALAPPDATA=scratch_root/local; TEMP=TMP=scratch_root/tmp; SystemRoot=WINDIR=parent-verified Windows directory
  clean_environment_literals = PYTHONDONTWRITEBYTECODE=PYTHONNOUSERSITE=PYTHONUTF8="1"
  clean_environment_block_utf16_units <= 24576 including the terminal double NUL
  result_version = 1; result_attempt_id = request.attempt_id
  result_outcome = result_code; result_reason = null or result_code
  step_result_outcome = result_code; step_result_reason = null or result_code
  launch_state = no_launch|launched; private_tree_state = absent|uncertain
  result_step_count = 0..request.step_count and 0..16; result_step_ordinals = unique, request-ordered values in 1..request.step_count
  failed_step_ordinal = null or a value in 1..request.step_count
```

The service constructs the request only after parent-owned eligibility,
freshness, target, plan, executable, materialization, and credential-exclusion
checks. No variable-length request member inherits a bound from an inactive
execution unit. `executable`, `materialized_root`, and `scratch_root` are
`absolute_path` values observed by the parent without a symlink or reparse
traversal. `executable` is the parent-verified fixed absolute package-runtime
identity, uses no `PATH` lookup, and is outside `materialized_root` and
`scratch_root`. `materialized_root` and `scratch_root` belong to exactly one
private attempt root as its distinct `target` and `scratch` children. Every
resolved `entrypoint` and `cwd` remains under `materialized_root`.

`clean_environment` is an ordered tuple containing exactly the 11
case-insensitively unique keys `APPDATA`, `HOME`, `LOCALAPPDATA`,
`PYTHONDONTWRITEBYTECODE`, `PYTHONNOUSERSITE`, `PYTHONUTF8`, `SystemRoot`,
`TEMP`, `TMP`, `USERPROFILE`, and `WINDIR`, in that order. `APPDATA` is
`scratch_root/roaming`; `HOME` and `USERPROFILE` are `scratch_root/home`;
`LOCALAPPDATA` is `scratch_root/local`; `TEMP` and `TMP` are
`scratch_root/tmp`; `SystemRoot` and `WINDIR` are the same parent-verified
Windows directory; and the three `PYTHON*` values are exactly `"1"`. It has no
additional or ambient key, and all path values satisfy `absolute_path`.

Within `runtime_identity`, `verification_runner_runtime.py` owns manifest
validation, `RunnerImplementationIdentity`, implementation digest, and the
shared runtime error and handle-cleanup state. The Windows-specific
`_verification_runner_executable_win32.py` owns `RunnerFixedExecutableLease`,
its primary/probe slots and serialized state, native declarations, path and
identity observations, rechecks, and handle release. It consumes the existing
materialized/scratch roots, yields only the verified absolute executable path,
and returns the existing cleanup state from `finalize_owner()`. The service
still constructs the owner, holds its context through the process request, and
performs final release and cleanup acceptance; no process-adapter edge changes.

The process boundary fixes the package-runtime executable source to the operating-system
image path of the current parent process. `sys.executable` is used only to
corroborate the same physical file; neither value is resolved through `PATH`,
configuration, a plan, or target material. The runtime-identity layer observes
every path component without following a symlink or reparse point, requires a
normalized absolute regular `python.exe` outside the owned target and scratch
trees, and holds a non-inheritable read handle that denies write and delete
sharing. The parent keeps that lease open from the final identity observation
through the complete process-adapter call and closes it on every exit. Failure
to establish or retain the lease is a sanitized admission failure with no
alternate executable. The process adapter consumes only the leased absolute
path in the closed request and does not import the runtime-identity layer.
Every process-adapter path observation separately rejects a symlink or reparse
point and a resolved-path or file-type mismatch. Repeated-observation equality
then compares only normalized path spelling, device ID, file ID, and full mode
(including file type); non-reparse `st_file_attributes` bits are mutable
metadata rather than physical identity and do not by themselves invalidate an
otherwise unchanged path. This exclusion never masks the separately repeated
reparse check.
Each lease serializes executable access, context state, and native close with a
private non-reentrant lock, so no two native close attempts overlap and access
cannot observe an in-progress close. Context entry is single-depth; nested
entry and direct close while a context is active fail closed, while the
matching context exit performs the one release transition. A definitive native
close failure retains ownership for a later serialized retry, whereas an
interrupted native close becomes uncertain and is never retried.

`steps` is a tuple whose ordinal is its one-based position. `step_id` satisfies
`identifier`; `mode` is exactly `script|module`; `entrypoint` satisfies the
matching entrypoint grammar; `argv` is a tuple of `literal_arg`; and `cwd`
satisfies `relative_path`. These counts, per-member sizes, and aggregate
command-line/environment-block limits are all enforced before process-adapter
entry. The request admits no other scalar or collection shape. Resource,
timeout, and output values use the exact [numeric bounds](#typed-process-value-boundary); `shell` and
`path_lookup` are exactly false. Request paths, argv, and environment are
transient and never copied into a result or durable row.
`cancel_signal` is a local in-process typed signal whose observable payload is
one Boolean; it contains no callback to SQLite, CLI policy, or a business gate,
is never persisted, and creates no transport.

For both result records, `outcome` is an adapter-local `result_code` and
`reason` is either null or an adapter-local `result_code`; `launch_state` uses
the exact [closed set](#typed-process-value-boundary). These codes are bounded sanitized structural
values, not arbitrary text. The value-model boundary owns only the closed
record-member sets, `result_code` grammar, nullability, and those member-to-
grammar bindings. The process adapter owns concrete local membership and
pairing, while the parent service owns the closed
durable/public mapping and projection. The parent service accepts no arbitrary
adapter text, remains the sole business-interpretation and persistence owner,
and persists only the mapped existing durable outcome. This freeze does not
alter the [Runner specification's existing closed durable outcome](runner-execution-specification.md#parent-service-and-audit-graph). Optional accounting
is nonnegative and bounded by the request. Result `steps` satisfies the exact
[count, uniqueness, range, and order relation](#typed-process-value-boundary), so it has at most one
sanitized result per request step. A result contains no exit code, output byte,
argv, environment value, credential, path, exception body, or arbitrary text.

The adapter-local pairings are exactly as follows; a slash joins
`outcome / reason / launch_state`, and `null` is the only absent reason:

`RunnerProcessStepPairingsV1`:

  blocked_prelaunch / runtime_unavailable|process_setup_failed|process_boundary_unproved|process_create_failed / no_launch
  pass / null / launched
  fail / step_nonzero / launched
  timeout / timeout / launched
  cancelled / cancelled / launched
  resource_exceeded / cpu_limit|memory_limit / launched
  output_rejected / output_limit / launched
  process_error / process_boundary_unproved|process_resume_failed|process_wait_failed|pipe_drain_failed|process_tree_unproved / launched
  process_error / process_create_failed / no_launch
  controller_interrupted / controller_interrupted / no_launch|launched
  cleanup_failed / process_cleanup_failed / no_launch|launched

`RunnerProcessResultPairingsV1`:

  blocked_prelaunch / runtime_unavailable|process_setup_failed|process_boundary_unproved|process_create_failed|cancelled|controller_interrupted / no_launch
  pass / null / launched
  fail / step_nonzero / launched
  timeout / timeout / launched
  cancelled / cancelled / launched
  resource_exceeded / cpu_limit|memory_limit / launched
  output_rejected / output_limit / launched
  process_error / runtime_unavailable|process_setup_failed|process_boundary_unproved|process_create_failed|process_resume_failed|process_wait_failed|pipe_drain_failed|process_tree_unproved / launched
  controller_interrupted / controller_interrupted / no_launch|launched
  cleanup_failed / process_cleanup_failed / no_launch|launched

`cpu_time_ms` is total user CPU time, `peak_job_memory_bytes` is the peak Job
memory observation, and `total_process_count` is the cumulative number of
processes created in the applicable per-step Jobs. Each is absent as one group
or is a nonnegative signed-64-bit integer. CPU and memory observations on a
successful or ordinary nonzero step do not exceed that step's request limit.
`process_limit` bounds simultaneously active processes in each Job; it is not
an invalid upper bound on the cumulative `total_process_count`. Request-level
CPU and process counts are checked sums and request-level memory is the checked
maximum. A limit or cleanup failure may omit accounting, but it never changes
the three mandatory Boolean cleanup proofs. Windows rejects creation beyond the
active-process limit, while the child may surface that rejection only as the
ordinary sanitized `step_nonzero`; the adapter adds no completion-port or notification
infrastructure solely to reclassify it as a resource result.

process_zero, handles_closed, and raw_output_discarded must all be true before
the service can accept process cleanup. The lifecycle layer separately returns
only the same safe `attempt_id` and the exact [`private_tree_state` set](#typed-process-value-boundary);
it returns no path or filesystem detail.

The lifecycle cleanup call accepts only its fixed parent-owned Runner paths
and one valid `attempt_id`. It returns `state=absent` only after a bounded
no-follow removal and a fresh proof that both the exact attempt and quarantine
entries are absent while their owning parent-directory identities remain
unchanged. Already-absent is an idempotent `absent` result. Simultaneous
attempt/quarantine entries, a foreign entry, reparse or identity change,
traversal bound or timeout, partial deletion, reappearance, or any observation
or deletion failure returns `state=uncertain`; it never authorizes an
out-of-root deletion, copy-back, alternate cleanup root, or diagnostic detail.
An invalid attempt identifier is rejected before cleanup rather than converted
to either result state.

### Cleanup Acceptance And Privacy

The process adapter is the sole owner of its Job, descendants, drains, and
handles. The lifecycle layer is the sole mechanic allowed to remove and prove
absence of the parent-owned private attempt tree. Neither is a business
acceptance owner. `verification_runner_service.py` is the single cleanup-
acceptance owner: it alone combines process-tree zero, handle closure, output
discard, and private-tree absence, blocks on any false or uncertain proof, and
alone authorizes cleanup success or terminal persistence. No recovery path,
maintenance module, repository, CLI branch, or later adapter may synthesize a
second acceptance decision.

Raw output, argv, environment, credentials, private paths, exit codes, and
exception bodies remain transient and are never stored. Cleanup or privacy
uncertainty fails closed. The [process and lifecycle records](#typed-process-value-boundary) define no serializer, file spool,
queue, pipe, socket, RPC, worker, daemon, subprocess wrapper, supervisor,
heartbeat, retry protocol, secondary state store, or second database
connection. Any later process separation requires explicit authority; this
boundary adds no IPC, schema, public CLI, Skill trigger, or second completion
gate.

This remains a reliability and privacy boundary for trusted code, not a
hostile-code sandbox or a claim of network isolation. Candidate C, B-to-C,
LPAC/AppContainer, Package-SID ACLs, ETW, registry/profile recovery, transfer
state machines, supervisors, trust-root hardening, and diagnostic fault
matrices are not qualification gates. Their code, tests, fixtures, manifests,
Candidate runtime material, and direct native seams are physically absent and
are not architecture nodes.

<a id="runner-parent-service-and-audit-graph"></a>

## Runner Parent Service And Audit Graph

`cli.py` keeps the existing `review target set` parser and formatters and calls
only `verification_runner_service.py` for that dispatch. The service consumes
one internal prepared-capture seam from `reviews.py`; the ordinary review
service delegates to the same seam, so target normalization, Git observation,
manifest construction, authority capture, event bytes, output, and maintenance
classification have one owner.

The service returns the stored target result with exactly
`verification_route` and `blocking_code` for JSON success. The text formatter
ignores those keys and remains byte-compatible; failure data remains the prior
three-key empty shape. The ordinary path derives `not_required` or
`receipt_required` from the captured verification expectation before its write.
The Runner path classifies the terminal observation after successful T2 with
one helper shared by the exact-current selector: closed no-launch fallback maps
to `receipt_required`, qualifying pass to `runner_pass`, and every other stored
terminal to `blocked` plus `verification_receipt_blocking`. No post-commit DB
reread, Runner reselection, or second public command is introduced.

The service performs target/plan/package/runtime and every deterministic
Runner-only preflight before a writer. A fallback or definite closed
pre-attempt Runner-only failure invokes one target-only T1 transaction. An
eligible Runner next validates the fixed lifecycle inventory and acquires the
zero-wait Runner lock before any T1 writer. Lock contention returns
`runner_busy`; lifecycle or inventory uncertainty returns
`runner_state_invalid`; both leave the target and Runner tables unchanged.
Under that retained lock, the service reconciles any pending intent before it
may invoke one short T1 transaction that revalidates the prepared Task,
Contract, criteria, target generation, manifest, plan, implementation, and
policy identities and atomically inserts:

```text
ordinary exact review target + artifact manifest/Reference + event
one verification_runner_resolution
one verification_runner_attempt intent
```

There is no eligible target-without-intent commit window. Conversely, no
Runner row is inserted for the fallback T1. A T1 failure rolls back every
member. The service retains the same Runner lock from pending reconciliation
through T1, materialization, process/lifecycle work, and terminal T2, but
closes each SQLite transaction before creating attempt directories,
materializing Git objects, leasing the executable, or calling the process
adapter. No SQLite writer spans filesystem or process work.

`storage.py` owns repository operations for exact same-generation graph reads,
the atomic T1 insert, cleanup-only append, and atomic terminal append. The
repository enforces at most one resolution, attempt, cleanup event, and
observation per project/Task/target generation despite the deliberately more
permissive physical indexes. Exact idempotency-digest replay returns the
existing row set; a different digest, extra row, ownership mismatch, or second
pending owner fails closed. `BEGIN IMMEDIATE` makes each same-generation
repository transaction atomic, while the retained Runner OS lock serializes
the complete reconciliation/T1/process/T2 route. A caller that cannot take
that lock performs no T1 and cannot launch a process.

The parent supplies one fresh 16-lowercase-hex token to each pure
`generate_runner_id` call. A resolution uses the target-plan seal and the
manifest-bound `RunnerImplementationIdentity`. Its
`runner_policy_digest` is the fixed
`verification Runner orchestration policy v1` label
`sha256:8910c1edfd525be0def6a2c3afb65adab11e5a32e9a60ebbf898c175ffd60fa8`.
The label is not recomputed from the manifest, target, plan, or runtime and
adds no sandbox or security claim; `runner_implementation_digest` separately
binds the strict current release-manifest identity. The process layer has
no durable canonical runtime digest, so `runtime_digest` is always null in the
resolution and Runner source projection; the implementation digest and fixed
policy label are the only durable execution identities.

For an admitted attempt, the service alone owns this sequence under the one
zero-wait Runner lock already acquired before T1:

```text
reconcile pending DB state and the fixed filesystem inventory
commit atomic T1 and close its SQLite writer
create the exact attempt target/scratch tree
materialize the admitted target and build the closed process request
hold the fixed-executable lease across the process call
consume one accepted RunnerProcessResultV1
prove process_zero + handles_closed + raw_output_discarded
prove exact attempt/quarantine absence through lifecycle cleanup
append one terminal graph, or fail closed without an observation
```

The terminal mapper copies `outcome`, `reason`, `launch_state`, ordered step
summary, duration, and optional accounting from the accepted process result without
adding a code. `launch_state=launched` maps to `route=runner`; `no_launch` maps
to `route=m21_fallback`. `complete_plan` is one only for a launched pass with
null reason, the complete planned ordinal set, every step passing, and no
failed ordinal. A definite post-intent runtime admission failure maps to
`blocked_prelaunch/runtime_unavailable/no_launch`; a definite tree,
materialization, or request-setup failure maps to
`blocked_prelaunch/process_setup_failed/no_launch` only after tree absence is
proved. `cleanup_failed` and any incomplete process/privacy/lifecycle proof
are not persistable observations.

One terminal repository transaction inserts exactly the observation, its
`attempt_cleanup_succeeded` event pointing to that observation, one
`runner_observation` Evidence Reference, and one verification-criterion link.
`evidence_ledger.py` admits this source only from the internal Runner dispatch,
derives `machine_observed/verification_runner/1`, recomputes the existing
sanitized source projection and digest, and rejects a caller-selected
assurance, producer, source, or criterion. The terminal transaction itself
creates no completion cycle or Bundle, advances neither Evidence nor Viewer
generation, and never creates a Verification Receipt. For
`gate_eligibility_version=0`, the Reference and link remain standalone audit
history and are never a Bundle member, completion basis, or Evidence JSON
source. For a schema-v21/v22 `gate_eligibility_version=1` qualifying pass, later
completion capture selects that exact stored observation, Reference, and link;
`evidence_projection.py` reuses the existing Reference and link as Bundle
members and emits only the sanitized projection in Bundle v2. The closed
no-launch fallback adds no Runner member, and every other terminal blocks
completion.

Restart reads immutable DB state before filesystem mutation. An attempt intent
without an observation is never relaunched. The service asks lifecycle to
remove only its exact known tree and, when absence is proved but the prior
process result is unknowable, appends only one cleanup event with null
`terminal_observation_id`. The cleanup-performing call then returns
`runner_state_invalid`, performs no new T1 or maintenance, and does not launch.
Storage classifies that old generation as complete `restart_cleaned`, not
pending. A later independent target-set call may create a new generation only
after the validator admits that exact cleanup-only predecessor, the pending
query returns zero attempts with neither observation nor cleanup event, and
the fixed inventory is empty. Target, authority, or installed-implementation
drift after intent takes the same cleanup-only route and then errors. A foreign
tree, simultaneous attempt/quarantine entries, more than one actual pending
attempt, unknown DB owner, root identity drift, or uncertain cleanup leaves
the attempt pending and fails closed.

Post-intent pending, basis drift, incomplete process proof, and cleanup or
terminal-state uncertainty use the existing sanitized `runner_state_invalid`
service failure; zero-wait lock contention uses `runner_busy`; a storage error
retains its existing code. No new public error code or envelope member is
added. The committed T1 target and intent survive a later service error.
Success and fallback perform exactly the existing one target-set post-commit
maintenance opportunity; a post-T1 error performs none and relies on the
already-advanced due state. Internal Runner writes add no maintenance call and
advance neither Evidence nor Viewer generation.

The shared Runner graph validator admits only the four states in the
[specification cardinality table](runner-execution-specification.md#parent-service-and-audit-graph). Every observation has exactly one matching
Reference/link and cleanup event; a cleanup-only event has a null terminal
observation; every pending intent has neither; all other combinations fail.
Schema v20 admits only `gate_eligibility_version=0`, Task marker `0`, and null
completion-cycle/Bundle Runner pointers. Schemas v21 and v22 retain that audit-only
shape and additionally admit the closed version-`1`/marker-`2` shape; only an
exact qualifying pass may bind its observation and the two pre-existing
evidence identities into completion, while fallback keeps the manual Receipt
basis and null Runner pointers. Viewer validates and discards Runner-only data.
Managed backup copies the SQLite rows but not the private Runner tree; recovery
therefore treats a restored pending intent as an unknown-result cleanup-only
case after proving physical absence. The service path adds no further schema
object, migration, public projection, Bundle format, Skill text, or manual
Receipt-gate behavior.
