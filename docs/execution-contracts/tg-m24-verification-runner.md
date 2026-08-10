# TG-M24 Verification Runner Mixed Execution Contract

<a id="tg-m24-verification-runner"></a>

> [!IMPORTANT]
> MIXED CURRENT AND CONDITIONAL FORMAL AUTHORITY. TG-M24.1 and its bounded
> TG-M24.1A correction are accepted predecessors, current shadow-Runner
> implementation authority belongs to TG-M24.2, and TG-M24.3 and TG-M24.4 are
> inactive. No TG-M24 runtime is active before TG-M24.2 completion. Loading
> this document activates no Runner, command
> execution, schema, CLI, Skill behavior, completion gate, network use,
> credential use, or target mutation.

The active [specification](../specification.md) and [design](../design.md) own
supported behavior and implementation structure. This document is the sole
detailed owner of the TG-M24 units' purpose, scope, order, dependencies,
permission boundaries, and gates. Root [plan.md](../../plan.md) owns the
cross-sequence gateway, current decisions, open issues, and static contracts
not delegated here. The Task database owns live state and evidence.

## Sequence Boundary

TG-M24 is sequential Tier 2 work in lane `TG-M24-VERIFICATION-RUNNER`:

| Unit/order | Task | Dependency | Purpose, permission boundary, and completion gate |
|---|---|---|---|
| TG-M24.1 / 10 | `tg_task_29aa63124900ad95` | accepted TG-DOC.2 | Accepted design predecessor: freeze project-owned verification plans, shell-free argv, exact Task/Contract/expectation/target binding, environment/network/mutation/resource policy, sanitized Runner-observed evidence, shadow-to-gate staging, and the M21 fallback. Activate no Runner, schema, CLI, Skill, gate, network, credential, or target mutation. Require exact documentation checks and diff, a current Receipt, and two independent Tier 2 reviews. |
| TG-M24.1A / 15 | `tg_task_56e212c793a42272` | accepted TG-M24.1 | Accepted bounded correction: retain exact fixed-DWORD class-46 proof where supported and add only the exact error-87 public-`AccessCheck` semantic route with a never-resumed normal-AppContainer control in a separate Job. Give both children the same four attributes and vary only the AAP-policy DWORD (`1` for LPAC, `0` for normal control). Preserve fail-closed no-resume and cleanup guarantees, make the one mandatory native portability test's SKIP non-PASS, and activate no Runner, schema, CLI, Skill, gate, network, credential, or external-project behavior. Require document/release, focused/native/full offline, exact-diff checks, a current Receipt, and two independent Tier 2 reviews. |
| TG-M24.2 / 20 | `tg_task_fafad7bc62df7576` | accepted TG-M24.1A | Current authority: implement only the approved bounded Runner and append-only evidence in shadow mode; existing M21 and completion gates remain unchanged. Execute only an explicit current project-owned plan, and require separate exact authority for any live external-project run. Integrate the accepted private LPAC seam, fix the fail-closed registry `0x80070002` boundary without accepting any registry failure as success, and add the full process/registry stable ID to the mandatory-native set with non-SKIP enforcement before acceptance. Require migration, safety, package, focused/full offline checks, exact diff, a current Receipt, and two Tier 2 reviews. |
| TG-M24.3 / 30 | `tg_task_dc015144091f8e60` | accepted TG-M24.2 | Inactive: make one qualifying exact-current complete-plan Runner result an explicit versioned completion basis while retaining the M21 caller-attested Receipt for unsupported, manual, visual, external, or unavailable-Runner cases. Analyzer output, arbitrary commands, and new normal-loop LLM leaves gain no gate authority. Require full offline, package/release consistency, exact diff, a current Receipt, and two Tier 2 reviews. |
| TG-M24.4 / 40 | `tg_task_f81f2d126f033a59` | accepted TG-M24.3 | Inactive: accept Runner safety, provenance, the completion gate, M21 fallback, Evidence Bundle/JSON, Analyzer coexistence, legacy/history, and realistic supported/unsupported flows, with only the bounded M24.1A portability correction to the accepted M24.1 design. Runs outside approved fixtures or this repository require separate exact project authority. Require focused/full and authorized forward checks, exact diff, a current Receipt, and two Tier 2 reviews. |

The sequence never lets an LLM select project tests, synthesize argv, approve a
plan, choose a fallback, or invoke a Runner leaf. Only a current project-owned
plan inside the exact review target may authorize bounded shell-free argv.
Runner observation and the M21 caller-attested `pass/full` Receipt remain
different evidence classes. M23 analysis never gains gate authority. A normal
supported flow removes the Receipt action; it does not replace it with another
main-LLM command or decision.

<a id="tg-m24-1"></a>

## TG-M24.1 Accepted Verification Runner Design

Task `tg_task_29aa63124900ad95` is the accepted documentation/design
predecessor. The design below, as boundedly corrected by accepted TG-M24.1A,
is exact authority for current TG-M24.2 and inactive TG-M24.3/TG-M24.4, but
activates none of their runtime behavior. The current
product remains schema v19,
Evidence Bundle/JSON v1, Viewer snapshot v4 with source schemas v5-v19, the
existing public CLI and Skill, and the M21 caller-attested verification gate.

### Fixed Product And Version Boundary

TG-M24.2 owns the unpublished v0.13.0/schema-v20 transition, but the candidate
remains v0.12.0/schema v19 until TG-M24.2 completes. Schema v20 owns only the
shadow Runner foundation, append-only
observations, Runner Evidence References, Bundle/JSON v2, and compatibility
work needed to expose gate-ineligible status. It must continue to write
verification-basis version 1 completion cycles and require the M21 Receipt.

TG-M24.3 may migrate schema v20 to schema v21 and activate verification-basis
version 2. Schema v21 changes only the selected verification-basis evaluator,
completion-cycle linkage, public projections, and Skill/package synchronization
needed for the Runner-or-M21 gate. Bundle/JSON remain version 2. TG-M24.4 adds
no planned schema or product version. The immutable published v0.10.0 identity
and every historical schema-v19-or-earlier cycle, Bundle, JSON file, Reference,
link, digest, Receipt, and assurance meaning remain unchanged.

### Project-Owned Plan Authority

The sole optional plan path is
`<physical-skill>/config/verification-runner.json`. Taskgov never creates,
edits, repairs, or infers this file. For an ordinary project-scoped install the
file is inside `.agents/skills/task-governance-tool`; the bounded self-host
exception uses `<repo>/task-governance-tool`. The package path must be a regular
non-reparse descendant of the governed root, and the plan used for execution
must be the blob at that same relative path in the exact review target. An
ambient worktree copy is never plan authority.

Plan v1 is strict UTF-8 JSON without BOM, duplicate keys, floats, non-integer
numbers, or unknown keys. Its semantic digest is SHA-256 over
`taskgov-verification-plan-v1\0` followed by compact, sorted-key, canonical
UTF-8 JSON and is stored as `sha256:` plus lowercase hex. The exact Git object
ID and labeled raw-blob SHA-256 are also bound so
semantically equal replacement bytes are still observable. The file is at most
64 KiB and has exactly:

```text
schema_version = 1
profile = "verification-runner-v1"
enabled = true|false
plan_id = lowercase ASCII [a-z0-9][a-z0-9._-]{0,63}
plan_version = positive integer <= 2147483647
entries = ordered array of at most 64 route entries
```

Each route entry has exactly `task_id`, `contract_revision`,
`verification_expectation_digest`, `verification_criterion_digest`, `route`,
and `coverage`. `coverage` is exactly `complete`. A route is either:

- `runner`, with one extra `steps` array and no fallback reason; or
- `m21_fallback`, with one extra `fallback_reason` from `manual`, `visual`,
  `external`, or `unsupported_toolchain`, and no steps.

Exactly one entry may have the current Task ID. A matching entry must also
equal the current positive Contract revision, existing
`taskgov-verification-expectation-v1` digest, and current verification
criterion digest. The expectation digest is exactly 64 lowercase hex; the
criterion digest is `sha256:` plus 64 lowercase hex. Duplicate Task entries, a
present stale entry, malformed
content, or any digest/Contract mismatch is `plan_invalid` or `basis_drift` and
fails closed. If the Task has no verification criterion, target-set does not
resolve or execute Runner state and completion remains `not_required` regardless
of plan content. An absent file, `enabled=false`,
or no entry for the current Task is an explicit no-launch M21 fallback rather
than implicit command discovery.

A `runner` entry contains 1 through 16 ordered steps. Each step has exactly:
`step_id`, `executable_id`, `mode`, `entrypoint`, `argv`, `cwd`,
`timeout_seconds`, `cpu_seconds`, `memory_mib`, and `process_limit`.

- `step_id` uses the plan-ID grammar and is unique within the entry.
- `executable_id` is only `taskgov_python`. PATH/PATHEXT lookup, caller paths,
  file associations, `.cmd`, `.bat`, PowerShell, command processors, and
  response files are unsupported.
- `mode` is `script` or `module`. A script entrypoint is 1 through 32 `/`-
  separated ASCII components and at most 512 bytes. Every component matches
  `[A-Za-z0-9_][A-Za-z0-9._-]{0,127}`, is neither `.` nor `..`, and the final
  component ends in lowercase `.py`. A module entrypoint is at most 512 ASCII
  bytes and matches
  `[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,15}`.
  Script resolution uses no-follow handles below the materialized target;
  module resolution uses only that target on the isolated import path.
- `argv` is an ordered array of at most 64 literal strings. Each string is at
  most 4,096 UTF-8 bytes, contains no NUL or control character, and is never
  expanded as a glob, environment reference, response file, or shell text.
  The final Windows command line is at most 24,576 UTF-16 code units under one
  tested quoting algorithm.
- `cwd` is `.` or 1 through 32 `/`-separated components using the script-
  component grammar above, is at most 512 ASCII bytes, and resolves through
  no-follow handles to a directory below the materialized private target.
- `timeout_seconds` and `cpu_seconds` are integers from 1 through 900;
  `memory_mib` is 64 through 2,048; and `process_limit` is 1 through 32. The
  sum of step timeouts is at most 1,800 seconds.

The fixed bootstrap invokes neither a shell nor `python -m`. Before changing
directory, it resolves a script entrypoint to its verified absolute no-follow
path below the private target; it then changes to verified `cwd`, sets
`sys.argv` to `[entrypoint, *argv]`, and uses `runpy.run_path(<resolved-path>)`
for `script` or `runpy.run_module(..., run_name="__main__", alter_sys=False)`
for `module`. Its import path contains only the private
target and private standard-library mirror. A normal return or `SystemExit(0)`
is step pass; `SystemExit` with any other value, an uncaught exception, or a
nonzero bootstrap result is `step_nonzero`. No exception or exit value is
retained. One implementation of the documented Windows backslash/quote inverse
of `CommandLineToArgvW` owns argv serialization; round-trip fixtures cover the
empty string, whitespace, quote, and trailing-backslash cases.

The plan cannot supply environment keys, network policy, mutation roots,
sandbox provider, interpreter path, retry count, output limit, credential,
secret, or gate disposition. Project code can still perform behavior taskgov
cannot semantically understand; the plan author, not taskgov or an LLM, owns
test selection and the truth of `coverage=complete`.

### Exact Target And Materialization

Runner v1 supports only current `git_snapshot` and `git_commit` review targets.
`diff_fingerprint`, `external_revision`, a non-Git target, or a Git format the
materializer cannot reproduce is a no-launch M21 fallback with a closed reason.
The stored target value alone never authorizes ambient-worktree execution.

For `git_snapshot`, `review target set` must retain in process the same stable
stage-zero index inventory used to compute the target fingerprint. For
`git_commit`, it must obtain the exact recursive tree inventory for the bound
commit. Git calls use fixed argv, `shell=False`, bounded streams and timeouts,
the existing safe Git environment, no hook, filter, checkout, worktree, or
network operation, and pre/post target recapture. Object materialization uses
only exact object IDs through `git cat-file`; taskgov never asks Git to check
out or transform content.

The private target accepts at most 10,000 entries, a 16 MiB encoded inventory,
a 32 MiB blob, and 512 MiB total blob bytes. Only modes `100644` and `100755`
are supported. Symlinks,
gitlinks/submodules, unmerged or intent-to-add entries, sparse placeholders,
case-fold or Unicode-unsafe collisions, Windows reserved names, trailing dot
or space, alternate streams, device/drive/UNC paths, reparse points, object
size/digest disagreement, and any inventory or target drift reject execution.
Unstaged and untracked ambient files are never copied or visible as target
material. A target that is intrinsically unsupported may use M21; corruption,
drift, or mismatch of a supposedly supported target is blocking.

Materialization occurs under a fresh taskgov-owned private directory below
the ignored package state, never in the governed project. No `.git` directory
is exposed. The materialized input manifest is domain-separated and must equal
the bound target before launch. Its exact canonical object is
`{format_version:1,entries:[...]}` with entries
`{path,mode,object_id,size_bytes,sha256}` in unsigned UTF-8 path order; the
stored `target_material_digest` is `sha256:` plus SHA-256 over
`taskgov-verification-materialization-v1\0` and the M22 canonical JSON bytes.
Taskgov makes the private target/runtime and precreated empty scratch tree
immutable read-execute input. Runner v1 grants the child no filesystem or
registry write boundary, including no write to its disposable AppContainer
profile. It proves the governed-root denials specified below but makes no
machine-global no-write claim. After every Job has stopped, owned effects are
destroyed or quarantined before the sole terminal record is published. Taskgov
never copies results back to the governed project.

Here and only here, `<fixed-root>` means the canonical no-follow
`<physical-skill>/state/current` returned by the existing shared fixed-state
resolver described in `docs/design.md#fixed-state-resolver`; no caller,
feature module, environment value, or CLI option may reconstruct or override
it. The state resolver owns exactly
`<fixed-root>/verification-runner/taskgov-verification-runner.lock`,
`<fixed-root>/verification-runner/attempts/<attempt-id>/{target,scratch,runtime}`,
and `<fixed-root>/verification-runner/quarantine/<attempt-id>`. No caller path
exists. Resolution rejects reparse points, aliases, unsafe ownership, unexpected
children, or containment drift. Setup may reconcile only DB-named attempt IDs;
an unknown entry makes repair fail closed and is never recursively removed.
Attempt/quarantine directories are excluded from SQLite backup, Evidence,
Viewer, release/package manifests, diagnostics, and every JSON/text projection.
Managed backup, restore, and rollback fail closed while any attempt lacks
`attempt_cleanup_succeeded`; a restored backup therefore contains only
terminal, already-cleaned Runner attempts and never authorizes local profile or
directory deletion.
At most one cleanup-pending attempt may exist; its presence blocks another
attempt, so quarantine cannot accumulate across launches. Prelaunch owned bytes
remain within the 512 MiB target plus 512 MiB runtime bounds. Before process
creation, the one derived profile folder must fit 16 MiB total logical bytes
and 16,384 entries; overflow is
`no_launch/not_run/m21_fallback/sandbox_unavailable`. After suspended creation,
the combined profile-folder plus profile-registry value bytes and entries must
still fit those same caps; overflow is
`launched/sandbox_violation/runner/sandbox_boundary_violation`. Before the sole resume, taskgov seals the scratch,
profile folder, and profile registry root read-only and proves with the final
child token that every create, write, rename, delete, security, and ownership
right is denied. The hard child disk-write budget is therefore zero bytes.
Missing required APIs or permissions detected before process creation is
no-launch `sandbox_unavailable`; failure of the final suspended-child proof is
blocking and the child is terminated without resume. Post-Job inventory or
descriptor growth is a sandbox violation. These bytes are never ingested as
evidence/output, and cleanup
failure remains a privacy/safety blocker requiring repeated setup/target-set
cleanup or external remediation.

### Windows Sandbox And Process Policy

The only qualifying provider is `windows_appcontainer_profile_v1` on the
supported Windows/CPython platform. A mock provider may test orchestration but
always has gate-eligibility version zero. Non-Windows, missing required APIs,
an incompatible parent Job, unavailable private runtime, or an unproved
provider capability is a proven no-launch fallback. There is no restricted-
token, clean-environment, Job-only, ordinary `subprocess`, shell, container, or
user-approved weaker bypass.

The provider's exact local-OS mutation is one disposable AppContainer profile
per attempt. After the immutable attempt row exists, it calls
`CreateAppContainerProfile` with the deterministic moniker
`OpenAI.TaskGov.Runner.<attempt-id-16-hex>`, no capability SIDs, and fixed
display text `Task Governance Verification Runner` and description
`Disposable taskgov verification attempt`. A pre-existing moniker returns
`profile_collision`, is never reused, and is never deleted. A successful create
must be followed immediately by the append-only `profile_created` event below;
failure to durably publish that event forbids launch and first attempts an
immediate delete. If absence cannot then be proven, ownership remains uncertain
and recovery follows the no-delete branch.

After creation, taskgov derives the exact SID and obtains the sole profile
folder through `GetAppContainerFolderPath`; any alias, reparse point, unexpected
owner, extra root, or size/count overflow blocks before resume. It preserves
coordinator cleanup rights while replacing the profile/scratch DACLs with the
fixed read-only child policy. After the first child is created suspended,
taskgov validates its primary token and creates the least-rights impersonation
duplicate described below. Through the accepted
`_verification_runner_lpac_win32.py` private seam, it uses that duplicate only
to impersonate long enough for
`GetAppContainerRegistryLocation(READ_CONTROL|WRITE_DAC)` to return the current
profile-root locator. The HRESULT must be `S_OK` and the locator non-null; it
then immediately reverts. Under the coordinator token it calls
`RegOpenKeyExW(locator, NULL, 0, KEY_READ|WRITE_DAC)`, requires success plus a
non-null, non-alias handle to the same key, and closes the locator before using
the reopened handle to enumerate, seal, reopen, and recheck the root and every
existing descendant. After this handoff the LPAC duplicate is used only for
the explicit `AccessCheck` calls below. Any non-`S_OK` locator result,
including HRESULT `0x80070002`, any `S_OK`-plus-null result, any
`RegOpenKeyExW` failure, null or alias handle, or close failure is fail-closed
and never success evidence. A revert failure gets one
`SetThreadToken(NULL, NULL)` recovery attempt, still produces
`sandbox_cleanup_failed`, and forbids resume; failure of that recovery is
fail-stop. Failure at any point terminates the never-resumed Job, follows owned
cleanup, and cannot fall back. No registry path string is derived or persisted.

Recovery calls `DeleteAppContainerProfile` only when the same attempt has a durable
`profile_created` event. An attempt with neither created nor collision event
first derives the SID/name: absence is safe cleanup, while presence is
`sandbox_profile_ownership_uncertain`, remains untouched, blocks every new
launch, and needs external removal before setup can prove absence. A created
moniker is permanently reserved to that attempt; an idempotent successful/not-
found delete appends `profile_deleted`. The product never enumerates or adopts
a profile and never deletes a collision/uncertain name. No path edits a
firewall rule, loopback exemption, credential, or target-project file. M24.1
activates none of this bounded mutation.

The SID receives read/execute access to the immutable private target,
per-attempt CPython runtime, and precreated empty scratch directories. The
disposable AppContainer profile folder and registry root are also read-only
after their bounded OS initialization. No write ACE is added to the original
governed material or canonical SQLite/Evidence/Viewer/backup state.

Every owned filesystem object's DACL is protected from inheritance and is
replaced with exactly four non-inherited allow ACEs in this order: `SYSTEM`
(`S-1-5-18`) and the current coordinator user SID each receive
`FILE_ALL_ACCESS`; `OWNER RIGHTS` (`S-1-3-4`) receives only `READ_CONTROL`; and
the exact attempt AppContainer SID receives only
`FILE_GENERIC_READ|FILE_GENERIC_EXECUTE|SYNCHRONIZE`. Its owner remains the
current coordinator user. Every owned profile-registry key uses the analogous
protected four-ACE DACL in that same SID order: `SYSTEM` and the coordinator
user receive `KEY_ALL_ACCESS`, `OWNER RIGHTS` receives only `READ_CONTROL`, and
the attempt AppContainer SID receives only `KEY_READ`; the coordinator user remains owner.
Taskgov applies and reopens this descriptor on every existing object/key, with
no `Everyone`, `Users`, all-application-packages, capability, inherited, deny,
generic-bit, or caller-supplied ACE. The `OWNER RIGHTS` ACE suppresses the owner's implicit
`WRITE_DAC`; the coordinator still qualifies through its explicit full-control
ACE, while the LPAC restricted-token pass can qualify only through the
read-only AppContainer ACE. The final child-token `AccessCheck` below is still
mandatory; failure to prove that two-pass result blocks before resume.

After suspended creation, taskgov scans every no-follow existing descendant of the
governed root, canonical state, exact attempt roots, derived profile folder,
and derived profile registry root. The scan is limited to 100,000 filesystem
entries plus 16,384 registry entries, 64 MiB of
canonical security-descriptor data, and 30 seconds; overflow/uninspectable state
in the pre-create inventory is no-launch `sandbox_unavailable`; the same failure
after suspended process creation is a launched blocking boundary failure.

Before process creation, taskgov inventories and seals every named filesystem
and registry security descriptor but makes no effective-token claim. After
atomic suspended creation it opens and validates the child primary token,
using a short-lived parent handle with exactly `TOKEN_QUERY|TOKEN_DUPLICATE`,
then creates a `SecurityImpersonation` duplicate with exactly
`TOKEN_QUERY|TOKEN_IMPERSONATE`. It uses that duplicate with
`SetThreadToken` only for the single `GetAppContainerRegistryLocation` call,
then immediately reverts before any `RegOpenKeyExW`, enumeration, descriptor
mutation, or `AccessCheck`. Thereafter it supplies the duplicate only as the
explicit token argument to every effective `AccessCheck` before the sole
resume, and closes it after those checks. For
every protected file/directory and every ancestor directory, `AccessCheck`
must deny `FILE_ADD_FILE`, `FILE_ADD_SUBDIRECTORY`, `FILE_WRITE_DATA`,
`FILE_APPEND_DATA`, `FILE_WRITE_EA`, `FILE_WRITE_ATTRIBUTES`, `DELETE`,
`FILE_DELETE_CHILD`, `WRITE_DAC`, and `WRITE_OWNER`. For the profile registry
root and all descendants it must likewise deny `KEY_SET_VALUE`,
`KEY_CREATE_SUB_KEY`, `DELETE`, `WRITE_DAC`, and `WRITE_OWNER`. It must prove
only the required target/runtime/scratch/profile read-execute access. The
canonical descriptor digest is rechecked after suspended creation before
resume and after the final Job stops; drift is blocking. This policy is named
`immutable_target_no_child_write_v1`. It does not claim that every machine
namespace lacks a pre-existing AppContainer/broker grant or that an unrelated
principal cannot race an ACL change. Public assurance says taskgov injected no
credential and proved direct AppContainer network denial plus this bounded
write-denial observation; external/brokered effects remain unproved.

`taskgov_python` is a per-attempt, digest-inventoried mirror of the running
supported AMD64 CPython runtime and standard library, excluding user/system
site packages, bytecode caches, package managers, and unrelated executables.
Its canonical inventory is
`{format_version:1,implementation:"cpython",version:[major,minor,micro],
architecture:"AMD64",entries:[...]}`; each entry is exactly
`{path,size_bytes,sha256}`, paths use the script-component grammar and `/`, and
entries sort by unsigned UTF-8 path bytes. It is bounded to 20,000 entries,
32 MiB per file, 512 MiB total, and 16 MiB canonical JSON. The stored digest is
`sha256:` plus SHA-256 over `taskgov-verification-runtime-v1\0` and those
canonical bytes. Source and mirror inventories must match before launch. The
application path is absolute, uses fixed isolated Python flags, and never uses
PATH or an ambient package configuration.

`runner_implementation_digest` separately binds the installed taskgov
implementation, including the fixed bootstrap. Taskgov first validates every
byte named by the installed `release-manifest.json` `core_files` map, rejects
unknown/missing/mismatched core paths, and canonicalizes exactly
`{core_files,manifest_version,package_name,package_version}` from that manifest.
The labeled digest is SHA-256 over
`taskgov-verification-runner-implementation-v1\0` plus those M22 canonical JSON
bytes. A manifest or any core byte change therefore invalidates the Runner
basis even when the fixed implementation-version text is unchanged.

Before process creation, taskgov creates and limits a non-breakaway Job with
kill-on-close, active-process, job-memory, per-process-memory, CPU-time, and UI
limits. One `STARTUPINFOEX` attribute list contains exactly
`PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`,
`PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY` set through a fixed
`DWORD` whose value is exactly
`PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT` (`1`),
`PROC_THREAD_ATTRIBUTE_JOB_LIST`, and `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`.
`CreateProcessW` uses that list with `EXTENDED_STARTUPINFO_PRESENT`,
`CREATE_SUSPENDED`, `CREATE_NO_WINDOW`, and `CREATE_UNICODE_ENVIRONMENT`; the
process is therefore born in the Job rather than assigned afterward. Taskgov
queries Job membership, limits, `TokenIsAppContainer=1`, the exact
AppContainer SID, the empty capability set, and the exact inherited handles.
It queries information class 46, `TokenIsLessPrivilegedAppContainer`, with one
fixed `DWORD` output buffer and a return-length pointer. A successful query
qualifies only when the returned length is exactly `sizeof(DWORD)` and the
value is exactly `1`; zero, another value, or another length fails closed.

Only a false result from that exact well-formed class-46 call with
`GetLastError()==ERROR_INVALID_PARAMETER (87)` may select the portable proof.
Here `87` is the Win32 result for unsupported information class on this exact
call shape; the same value from any other call is not a fallback signal. Every
other error or unknown result fails closed. The portable proof creates one
normal-AppContainer control with the same exact application, command line,
environment, working directory, security-capabilities SID, stdio
configuration and attribute shape, creation flags, and containment limits as
the LPAC child, while using distinct OS handles and a distinct creation-time
Job. It uses the same exact four attributes. The sole attribute-value
difference is
`PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY`: its fixed `DWORD` is
exactly `0` for the normal control and exactly
`PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT` (`1`) for the LPAC child.
Explicit zero overrides any parent opt-out inherited by omission; omission is
not a valid normal-AppContainer control. The control is created suspended, is
never resumed, never executes project bytes, and is terminated with proved
Job-zero and closed handles before the LPAC child may resume.

The provider duplicates both primary tokens only at `SecurityImpersonation`
and performs three bounded in-memory evaluations with public `AccessCheck` and
one fixed non-generic `FILE_READ_DATA` bit `0x00000001`. Every evaluated
descriptor has owner and group exactly SYSTEM (`S-1-5-18`) and a protected DACL
containing exactly two allow ACEs in canonical order: the exact coordinator
user SID for `FILE_READ_DATA`, then the selected application SID for
`FILE_READ_DATA`. There is no deny, inherited, generic, or additional ACE. For
the AAP descriptor the selected application SID is ALL APPLICATION PACKAGES
`S-1-15-2-1`: the normal AppContainer check must return access allowed and the
LPAC check must return access denied. For the Package descriptor the selected
application SID is the exact attempt Package SID: the same LPAC token check
must return access allowed. All three results are required semantic gates.
Descriptor owner/group, exact ACE count/order/masks/SIDs, generic mapping,
token type, privilege-set result, returned granted mask, and access-status
boolean are revalidated, so an API failure or malformed/ambiguous result cannot
count as denial or success.
`WIN://NOALLAPPPKG` may be emitted only as a diagnostic after the semantic
proof; its presence, absence, or value is never consulted as a selector,
branch, expected-access, or success/failure input. Existing AppContainer, SID,
zero-capability, loopback, Job, ACL, and inherited-handle proofs remain
mandatory.

The separate normal-control Job contributes no CPU, peak-memory, or process
count to the Runner step observation; its bounded wall time remains inside the
attempt duration. Any direct or portable proof failure terminates every
affected suspended child without resume, proves both Jobs empty, closes all
handles, and follows the existing owned cleanup path. Only after the complete
proof succeeds may taskgov perform the LPAC child's existing sole resume. Any
post-create `AssignProcessToJobObject` fallback is forbidden.

`STARTUPINFOEX.StartupInfo.dwFlags` includes `STARTF_USESTDHANDLES`;
`hStdInput`, `hStdOutput`, and `hStdError` are respectively the call-only
inheritable `NUL` read duplicate and the two call-only inheritable pipe-write
duplicates, and `bInheritHandles=TRUE`. Only those three duplicates appear in
the handle-list attribute. Parent originals and every nonlisted handle are
non-inheritable; the duplicates close immediately after creation proof (or any
creation failure), while parent pipe-read handles remain only in the
coordinator.

The extended-limit flags are exactly `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`,
`JOB_OBJECT_LIMIT_ACTIVE_PROCESS`, `JOB_OBJECT_LIMIT_JOB_MEMORY`,
`JOB_OBJECT_LIMIT_PROCESS_MEMORY`, and `JOB_OBJECT_LIMIT_JOB_TIME`; byte limits are
`memory_mib * 1048576`, active-process limit is `process_limit`, and per-Job
user-time limit is `cpu_seconds * 10000000` 100-nanosecond units. UI restriction
flags deny handles, read/write clipboard, system parameters, display settings,
global atoms, desktops, and exit-Windows operations. Breakaway/silent-breakaway
and completion-port delegation are not enabled.

Each ordered step receives a fresh Job and child process so its timeout, CPU,
memory, and process limits are exactly the values in that step. The private
profile/runtime/target/scratch live for the whole attempt, and a later step is
created only after the earlier Job's active-process count is zero and its pipes
are closed. `duration_ms` covers the entire ordered attempt; no per-step time,
resource value, argv, or output is persisted.

Aggregate `cpu_time_ms` is computed by checked-summing every completed Job's
100-nanosecond `TotalUserTime` and flooring the sum once by division by 10,000;
`peak_job_memory_bytes` is the maximum reported `PeakJobMemoryUsed` over all Jobs, and
`total_process_count` is the checked sum of each Job's final
`JOBOBJECT_BASIC_ACCOUNTING_INFORMATION.TotalProcesses`. Overflow, a missing
query, a nonpositive completed-Job count, or an inconsistent counter is
`job_state_unproved`, never a partial metric. The coordinator revalidates the
Task/Contract/authority/criterion/target and cancellation state immediately
before every later Job. Cancellation between Jobs records `cancelled`; any
basis drift after an earlier child existed records `post_launch_drift`. Neither
case starts the next process.

Timeout and cancellation use a monotonic clock, terminate the entire Job,
drain or close every pipe, wait for active-process count zero, and never read a
success after abnormal containment. A descendant holding a pipe cannot outlive
Job termination. Failure to prove creation-time membership, limits, tree
termination, token/ACL state, profile cleanup, or handle cleanup is non-pass.

The child environment is an exact case-insensitive no-duplicate map with only
`SystemRoot`, `WINDIR`, `TEMP`, `TMP`, `HOME`, `USERPROFILE`, `LOCALAPPDATA`,
`APPDATA`, `PYTHONDONTWRITEBYTECODE`, `PYTHONNOUSERSITE`, and `PYTHONUTF8`.
The first two equal the no-follow verified Windows directory; `TEMP`/`TMP`
equal `<scratch>/tmp`, `HOME`/`USERPROFILE` equal `<scratch>/home`, and
`LOCALAPPDATA`/`APPDATA` equal `<scratch>/local` and `<scratch>/roaming`.
Those four directories are precreated, no-follow verified, and read-only to the
child. The final three
values equal string `1`. It inherits no
PATH, `PYTHONPATH`, `PYTHONHOME`, drive-current-directory entry, proxy, Git,
SSH, cloud, CI, Codex, locale override, credential, token, cookie, or caller
variable. Fixed command flags are `-I -B -X utf8`. The handle list contains
only a read handle to `NUL` for standard input and the two binary pipe write
handles for standard output/error; those pipes are drained concurrently. No
token, Job, database, directory, log, or ambient inheritable handle enters the
child.

Network policy is always `appcontainer_no_capabilities_v1`: the primary token
must be AppContainer, have zero capability SIDs, and have no loopback exemption;
taskgov verifies those facts with `GetTokenInformation` for
`TokenIsAppContainer`, `TokenAppContainerSid`, and `TokenCapabilities`, plus
`NetworkIsolationGetAppContainerConfig`; it never edits firewall or network-
isolation policy. Mutation policy is always
`immutable_target_no_child_write_v1` as bounded above. The Job supplies the
hard time, CPU, memory, and process limits. Output has a fixed 1 MiB combined
per-step observation limit. No plan can relax a boundary.

Runner contract version is integer `1`, and implementation version is fixed
text `taskgov-verification-runner/1`. `runner_policy_digest` is `sha256:` plus
SHA-256 over `taskgov-verification-runner-policy-v1\0` and canonical JSON of
exactly `{runner_contract_version:1,executable_id:"taskgov_python",max_output_bytes:
1048576,environment_profile:"clean_python_v1",network_policy:
"appcontainer_no_capabilities_v1",mutation_policy:
"immutable_target_no_child_write_v1",child_disk_write_limit_bytes:0,
timeout_clock:"monotonic",stop_on_nonpass:true}`.
`sandbox_policy_digest` uses domain
`taskgov-verification-sandbox-policy-v1\0` and exactly
`{provider_id:"windows_appcontainer_profile_v1",capabilities:[],
creation_job_assignment:"PROC_THREAD_ATTRIBUTE_JOB_LIST",profile_lifecycle:
"per_attempt",all_application_packages_policy:"opt_out",
less_privileged_appcontainer:true,
network_policy:"appcontainer_no_capabilities_v1",
mutation_policy:"immutable_target_no_child_write_v1",
child_disk_write_limit_bytes:0}`. These digests bind fixed
policy only; the plan digest independently binds every step resource limit.

AppContainer and Job enforcement prove only the specific OS boundary taskgov
successfully established. They do not prove the plan is a good test strategy,
that `coverage=complete` is substantively true, the identity or honesty of the
plan author, full machine or runtime authenticity, absence of secrets already
committed as project code, descendant intent, external-system state, or the
truth of test assertions. Public assurance text and Bundles must preserve
these limits.

### Trigger, Lifecycle, Concurrency, And Recovery

No public Runner command, daemon, scheduler, watcher, model call, or extra
Skill leaf is added. The existing `review target set` call is the only normal
trigger. With SQLite closed, it captures the bounded target inventory, reads
the plan blob from that inventory, performs side-effect-free policy/provider
preflight, and streams each bounded Git object once without writing it to
compute the exact target-material manifest/digest. In one short transaction it
revalidates Task/Contract/current-target expectations, sets the exact target,
and atomically inserts one immutable Runner resolution plus its immutable
attempt for a Runner candidate. A fallback or blocked no-launch resolution
instead appends its terminal no-launch observation and Reference/link in that
transaction and has no attempt. Only after a durable attempt exists may taskgov create its owned
directories, stream/materialize the objects a second time, copy the private
runtime, change owned ACLs, or call `CreateAppContainerProfile`; every copied
inventory must equal the precomputed seals. It performs this OS/process work
with SQLite closed and uses short transactions only for lifecycle and terminal
append-only records. A transaction failure leaves no filesystem/profile side
effect, and every side effect has a DB-named owner before creation.
The CLI returns the sanitized route and outcome with the normal target-set
response.

One no-wait package-state Runner lock prevents concurrent target-set launches.
Contention returns the sanitized `runner_busy` error before a target or Runner
row is changed; it is neither success nor an M21 fallback. A resolution has a
deterministic `idempotency_digest`: `sha256:` plus SHA-256 over
`taskgov-verification-runner-resolution-v1\0` and canonical JSON of exactly
`{project_id,task_id,contract_revision,authority_snapshot_id,
verification_criterion_id,verification_expectation_digest,
verification_criterion_digest,target_kind,target_value,target_base_revision,
target_generation,target_capture_version,artifact_manifest_id,
target_material_digest,plan_state,plan_blob_object_id,plan_raw_digest,plan_id,
plan_version,plan_semantic_digest,selected_entry_digest,coverage,step_count,
 runner_contract_version,runner_implementation_version,
 runner_implementation_digest,runner_policy_digest,
sandbox_provider,sandbox_policy_digest,runtime_digest,
gate_eligibility_version,trigger,route,reason}`.
Canonical JSON is the M22 sorted-key compact UTF-8 form; nullable values are
JSON null. The unique current Task/target-generation key permits one immutable
resolution, at most one immutable attempt, its closed lifecycle events, and one
terminal observation. All four tables reject update/delete. Resolutions
additionally have unique same-project/Task ID and idempotency-digest keys;
attempts and observations
have unique same-project/Task IDs plus the parent/generation uniqueness stated
below. No child receives a SQLite handle or canonical state path.

Normal target-set holds only the Runner lock across capture, the short
resolution/attempt transaction, OS execution, owned cleanup, and terminal publication;
it releases that lock before any Evidence, Viewer, or backup maintenance. Setup
holds the existing state-transition lock first, then the Runner lock only for
cleanup-pending attempt reconciliation, releases it, and continues the established
artifact/publication lock order. No path holds an SQLite writer while copying,
launching, waiting, terminating, deleting, or rendering.

There is no automatic same-generation retry. Pass, fail, timeout,
cancellation, resource breach, sandbox breach, child crash, output rejection,
controller interruption, or post-launch drift is terminal and a fresh review
target generation is required. `launch_state` is exactly `no_launch`,
`launch_uncertain`, or `launched`; no Boolean launch claim is stored. A direct
fallback/blocked resolution and a synchronous provider failure before
`CreateProcessW` returns a process handle are `no_launch`. A process returned
by the creation-time-Job call is `launched`, even if resume later fails. An
attempt without a terminal observation and with `profile_created` is always
`launch_uncertain`: the rows do not prove which side of process creation the
controller reached. Without `profile_created`, process creation is forbidden,
so collision, proven absence, or ownership-uncertain recovery is `no_launch`.

At the start of every lock-holding target-set or explicit `setup` repair, the
coordinator reconciles every attempt lacking an append-only
`attempt_cleanup_succeeded` event, including attempts that already have a
terminal cleanup-failure observation. While any known cleanup remains pending,
no target change or new launch is allowed. Recovery relies on Job kill-on-close
and applies the profile-ownership matrix below before any delete; it no-follow
deletes only DB-named roots.

After every normal child or prelaunch disposition is computed in memory,
taskgov first stops every Job and attempts OS cleanup with SQLite closed. If
cleanup is proved, one short transaction appends
`attempt_cleanup_succeeded`, the sole computed terminal observation, and its
Evidence Reference/link atomically. If cleanup is not proved, it appends the
sole `sandbox_cleanup_failed/blocked` observation plus its Evidence
Reference/link, but no cleanup-success event, then retains the named
quarantine. A transaction failure publishes
neither cleanup event nor terminal/Reference/link, so later recovery repeats
idempotent OS cleanup and chooses the conservative terminal below. A direct
no-attempt fallback/block publishes its observation/Reference/link atomically in the
target transaction because it owns no cleanup effect.

Recovery that proves cleanup and finds no terminal appends the cleanup event
and sole `controller_interrupted/launch_uncertain` terminal plus its Evidence
Reference/link atomically after a durable `profile_created`; before that event
it instead atomically publishes the cleanup event, exact
`blocked_prelaunch/no_launch` profile/setup terminal, and its Reference/link. Recovery that proves
cleanup after an existing `sandbox_cleanup_failed` terminal appends only the
cleanup event; the immutable blocked terminal is never replaced. Unproved
cleanup atomically appends at most one cleanup-failure terminal plus its
Reference/link, retains quarantine, and
remains pending for every later repair. Every gate-eligible Runner pass requires
the matching cleanup-success event. Either recovery terminal blocks that
generation and requires a fresh target after cleanup. Ctrl-C or target
supersession uses the same stop/cleanup/atomic-publication path. Recovery never
relaunches old argv or converts uncertainty to fallback/success.

The common publication invariant is unconditional: direct, normal, and
recovery paths insert every new terminal observation together with exactly one
Evidence Reference and criterion link in one transaction; a cleanup-success
event joins that transaction whenever cleanup has just been proved. No
terminal-only or Reference-only commit is valid.

The coordinator checks current Task, Contract, authority snapshot, criterion,
target, plan, and Runner implementation/policy/sandbox/runtime identities
before the first and every later launch. After the final Job reaches active
count zero and before cleanup, it performs the final private target/runtime,
profile-size, and ACL/registry-descriptor rehash and fixes the provisional
outcome in memory. Only then may it delete those owned roots/profile. After
cleanup and immediately before the atomic terminal transaction, it revalidates
the durable Task/Contract/authority/criterion/current-target tuple and the
installed Runner implementation/policy identity, but never tries to rehash a
deleted attempt root. Any drift before the first launch records a no-launch
blocking observation. Drift after any child creation or after the final private
seal records a non-pass terminal observation. Historical rows are retained;
only exact-current qualification changes.

### Observation, Privacy, And Assurance

Schema v20 adds immutable `verification_runner_resolutions`,
`verification_runner_attempts`, `verification_runner_sandbox_events`, and
`verification_runner_observations`. A
resolution is the exact prelaunch `runner`, `m21_fallback`, or `blocked`
decision; an attempt authorizes only one OS setup/create operation; an
observation is the terminal direct result. IDs are respectively
`tg_verification_runner_resolution_`, `tg_verification_runner_attempt_`,
`tg_verification_runner_sandbox_event_`, and
`tg_verification_runner_observation_` plus 16 lowercase hexadecimal characters.
All timestamps use the existing canonical UTC form, durations are integer
milliseconds, raw SHA-256 fields are 64 lowercase hexadecimal characters, and
labeled digests are `sha256:` plus 64 lowercase hexadecimal characters.

The resolution row has exactly these columns and no extension JSON:

| Column | SQLite type and null/closed contract |
|---|---|
| identity | `verification_runner_resolution_id TEXT PRIMARY KEY`; non-null `project_id TEXT` and `task_id TEXT` with the existing composite Task foreign key |
| authority | `contract_revision INTEGER NOT NULL > 0`; non-null `authority_snapshot_id TEXT` and `verification_criterion_id TEXT` with same-project/Task composite foreign keys and snapshot membership |
| criterion digests | non-null raw `verification_expectation_digest TEXT`; non-null labeled `verification_criterion_digest TEXT`, equal to the referenced current criterion |
| target | non-null `target_kind TEXT`, `target_value TEXT`, `target_generation INTEGER`, `target_capture_version INTEGER`, and `artifact_manifest_id TEXT`; nullable `target_base_revision TEXT`; the existing four-kind/500-byte/base matrix applies (`git_snapshot` alone has a non-null full object ID; every other kind is SQL/JSON null with no coercion), generation is positive, capture version is `1`, and the same-Task manifest must equal the target tuple |
| material | nullable labeled `target_material_digest TEXT`, non-null exactly for a Runner candidate as the side-effect-free precomputed manifest seal that the later owned materialization must equal |
| plan | non-null `plan_state TEXT`; nullable `plan_blob_object_id TEXT`, `plan_raw_digest TEXT`, `plan_id TEXT`, `plan_version INTEGER`, `plan_semantic_digest TEXT`, and `selected_entry_digest TEXT` under the matrix below |
| selected coverage | `coverage TEXT NOT NULL` is `complete|not_applicable`; `step_count INTEGER NOT NULL` is `0..16` |
| Runner basis | `runner_contract_version INTEGER NOT NULL = 1`; `runner_implementation_version TEXT NOT NULL = 'taskgov-verification-runner/1'`; non-null labeled `runner_implementation_digest TEXT` and `runner_policy_digest TEXT`; nullable `sandbox_provider TEXT`, `sandbox_policy_digest TEXT`, and `runtime_digest TEXT` |
| disposition | `gate_eligibility_version INTEGER NOT NULL` is `0|2`; `trigger TEXT NOT NULL` is `review_target_set_v1`; `route TEXT NOT NULL` is `runner|m21_fallback|blocked`; nullable `reason TEXT` uses the closed vocabulary below |
| seal | non-null labeled `idempotency_digest TEXT`; non-null `created_at TEXT` |

`plan_state` is exactly `not_addressable|absent|disabled|no_entry|runner|
fallback|invalid`. `runner|fallback|disabled|no_entry` require
all blob/object and semantic plan fields; `runner|fallback` additionally require
`selected_entry_digest`. `not_addressable|absent` make all six
plan fields null. `invalid` requires only blob object ID/raw digest and makes
the semantic four null. Object IDs are 40 or 64 lowercase hex; plan version is
positive; the three plan digests are labeled. The selected-entry digest uses
`taskgov-verification-plan-entry-v1\0` plus the canonical selected entry.
Unreadable or unsafe plan blobs roll back the target write instead of storing
a partial resolution.

A `runner` route requires `plan_state=runner`, `coverage=complete`, 1 through
16 steps, and every material/sandbox/runtime field. An explicit plan fallback
has `plan_state=fallback`, `coverage=complete`, and zero steps; every other
state has `coverage=not_applicable` and zero steps. A fallback has one fallback
reason; a block has one blocking reason. `verification_criterion_id` is absent
only when target-set creates no Runner resolution/attempt/observation; the
completion evaluator then owns the `not_required` basis. Insert triggers
rederive all matrices from the Task,
snapshot, criterion, target, manifest, and immutable parent rows.

The attempt row has exactly non-null `verification_runner_attempt_id TEXT
PRIMARY KEY`, `project_id TEXT`, `task_id TEXT`, `target_generation INTEGER`,
`gate_eligibility_version INTEGER`, `verification_runner_resolution_id TEXT`,
`target_material_digest TEXT`, `runner_implementation_digest TEXT`,
`sandbox_instance_digest TEXT`,
`attempt_digest TEXT`, and `intent_recorded_at TEXT`. Its labeled
`attempt_digest` uses `taskgov-verification-runner-attempt-v1\0` plus canonical
JSON of exactly `{gate_eligibility_version,target_material_digest,
project_id,resolution_id,runner_implementation_digest,sandbox_instance_digest,
target_generation,task_id}`.
`sandbox_instance_digest` uses
`taskgov-verification-sandbox-instance-v1\0` plus canonical JSON of exactly
`{attempt_id,profile_moniker,provider_id}`; neither clear profile value is
persisted.
Composite foreign keys cover the resolution owner/generation/eligibility/ID;
the attempt `target_material_digest` and `runner_implementation_digest` must
byte-equal their non-null resolution parent fields and are revalidated on every
read. Resolution, generation, and attempt ID
are independently unique per Task. It
stores no argv, plan body, handle, SID, path, process ID, or launch Boolean.

`verification_runner_sandbox_events` is operational cleanup authority, not
Evidence. It has exactly non-null `verification_runner_sandbox_event_id TEXT
PRIMARY KEY`, `project_id TEXT`, `task_id TEXT`, `target_generation INTEGER`,
`verification_runner_attempt_id TEXT`, `event_kind TEXT`, `event_digest TEXT`,
and `created_at TEXT`, plus nullable `terminal_observation_id TEXT`. Kind is
exactly `profile_absent|profile_created|
profile_collision|profile_deleted|attempt_cleanup_succeeded`. Its labeled
digest uses `taskgov-verification-runner-sandbox-event-v1\0` and canonical JSON
of exactly `{attempt_id,event_kind,project_id,target_generation,task_id,
terminal_observation_id}`.
Composite ownership references the attempt; `(attempt_id,event_kind)` is
unique. `profile_absent|profile_created|profile_collision` are mutually
exclusive; `profile_deleted` requires created. Every non-cleanup event makes
`terminal_observation_id` null. Cleanup success requires either absent,
collision, or created-plus-deleted, proves every DB-named root absent with no
quarantine, and makes `terminal_observation_id` non-null with a same-owner
composite foreign key declared `DEFERRABLE INITIALLY DEFERRED` to the sole
terminal. The coordinator inserts that event first and the terminal second in
one transaction, or links an already-existing cleanup-failure terminal; commit
cannot leave the event without its terminal. A gate-eligible pass additionally
requires a cleanup event linked to its own observation ID. Events have no
Evidence Reference, Bundle member,
Viewer field, backup payload beyond ordinary canonical SQLite backup, or public
path/SID/profile projection.

The observation row has exactly:

| Column | SQLite type and null/closed contract |
|---|---|
| identity | `verification_runner_observation_id TEXT PRIMARY KEY`; non-null `project_id`, `task_id`, `target_generation`, `gate_eligibility_version`, and `verification_runner_resolution_id`; nullable `verification_runner_attempt_id`; same-owner composite foreign keys; unique generation, resolution, observation ID, and non-null attempt per Task |
| Runner seal | non-null labeled `runner_implementation_digest TEXT`, byte-equal to the immutable resolution and non-null attempt when present |
| disposition | non-null `route TEXT` (`runner|m21_fallback|blocked`), `launch_state TEXT` (`no_launch|launch_uncertain|launched`), and `outcome TEXT`; nullable closed `reason TEXT` |
| coverage | `complete_plan INTEGER NOT NULL` is `0|1`; `total_step_count` and `completed_step_count` are integers `0..16`, completed not above total; nullable `failed_step_ordinal INTEGER` is `1..total` |
| timing/resources | non-null `started_at TEXT`, `finished_at TEXT`, nonnegative `duration_ms INTEGER`; nullable nonnegative `cpu_time_ms INTEGER`, `peak_job_memory_bytes INTEGER`, and `total_process_count INTEGER` |
| seal | non-null labeled `sanitized_result_digest TEXT`; non-null `created_at TEXT` |

A direct fallback has null attempt, `no_launch/not_run`, and zero timing/resource
values. Here `started_at=finished_at`, `duration_ms=0`, and nullable resource
fields are null; these timestamps bound the resolver observation, not a child.
A prelaunch block has nullable attempt, `no_launch/blocked_prelaunch`, and the
same no-child timing/resource matrix. An unfinished attempt reconciles only to
`launch_uncertain/controller_interrupted/blocked` after `profile_created`, or
to the exact no-launch profile/setup block before that event; its trustworthy
timing/resource values are zero/null. A launched terminal has non-null attempt,
`launched`, canonical start/finish, monotonic duration, and resource values
when the OS reports them. Total steps are the selected Runner-entry count or
zero; completed steps include the terminal classified step. `complete_plan=1`
iff outcome is `pass`, completed equals total, and failed ordinal/reason are
null. Execution stops at the first non-pass.

A pass requires all three resource fields non-null. No-launch, uncertainty, and
cleanup-only outcomes make all three null. Other launched outcomes require all
three unless reason is `job_state_unproved`, in which case they are all null;
partial triples are invalid.

Observation `route` is the effective terminal route. It equals
`m21_fallback` for a direct fallback or a synchronously proven no-launch
provider/runtime unavailability even when the earlier candidate resolution was
`runner`; it equals `runner` only for a known launched terminal; and it equals
`blocked` for integrity failure, uncertainty, or cleanup failure.

Outcome is exactly `not_run|blocked_prelaunch|pass|fail|timeout|cancelled|
resource_exceeded|sandbox_violation|output_rejected|process_error|
controller_interrupted|post_launch_drift|sandbox_cleanup_failed`. Reason is
null for a launched terminal except where this matrix requires a code; it is
otherwise exactly one of:

```text
plan_absent plan_disabled plan_not_configured manual visual external
unsupported_toolchain unsupported_target unsupported_platform
sandbox_unavailable runtime_unavailable
plan_invalid plan_ambiguous basis_drift target_drift object_drift
policy_mismatch materialization_failed sandbox_setup_failed
profile_collision sandbox_profile_ownership_uncertain
step_nonzero timeout cancelled cpu_limit memory_limit process_limit
sandbox_boundary_violation output_limit process_create_failed
process_resume_failed process_wait_failed pipe_drain_failed job_state_unproved
controller_interrupted post_launch_drift sandbox_cleanup_failed
prelaunch_drift terminal_missing state_inconsistent
```

Fallback uses only the first eleven codes. Prelaunch blocked uses a later
integrity/setup code. Known launched outcomes map to `step_nonzero`, `timeout`,
`cancelled`, one resource code, `sandbox_boundary_violation`, `output_limit`,
one process/pipe/Job code, or `post_launch_drift`. Cleanup failure overrides
the effective route to `blocked`. Unknown codes fail publication; they never
become fallback. Failed ordinal is non-null only for a known step-specific
non-pass (including a later step's create failure), not for first-step
no-launch, between-step cancellation, uncertainty, or cleanup-only failure.

`process_create_failed` on the first step is specifically
`no_launch/blocked_prelaunch/blocked`; after at least one earlier Job it is
`launched/process_error/runner` and identifies the next ordinal. Resume, wait,
pipe-drain, and Job-proof failures are always launched process errors because
`CreateProcessW` already returned a process born in the Job.

Output bytes are drained only to enforce the fixed limit and are immediately
discarded. No child-output digest, byte count, prefix, suffix, sample, or
classification is retained: even a digest can disclose low-entropy secret
output. Limit overflow kills the Job and records `output_rejected`. The stored
sanitized-result digest is `sha256:` plus SHA-256 over
`taskgov-verification-runner-observation-v1\0` and canonical JSON of exactly
`{attempt_id,completed_step_count,complete_plan,cpu_time_ms,duration_ms,
failed_step_ordinal,finished_at,gate_eligibility_version,launch_state,outcome,
peak_job_memory_bytes,project_id,reason,resolution_id,
runner_implementation_digest,started_at,target_generation,task_id,route,
total_process_count,total_step_count}`. Nulls are
explicit and keys use the M22 sorted-key compact UTF-8 order. Taskgov
never persists or projects argv, entrypoints, command bodies, raw output,
stream fragments, per-stream text, exit codes, exception/traceback text, logs,
environment, temporary paths, prompt/chat/reasoning, credential, secret,
session identifier, provider body, or arbitrary coverage prose. Diagnostics
expose only closed codes, bounded structural counts, stable IDs, versions, and
sanitized-result digests.

Every terminal observation receives one Evidence Reference with source kind
`runner_observation`, source state `recorded`, assurance class
`machine_observed`, producer class `verification_runner`, and producer version
`1`; its source ID is the observation ID. The Reference digest continues the
existing `taskgov-evidence-reference-v1\0` builder with the closed Runner source
 projection exactly `{observation_id,gate_eligibility_version,route,reason,
 outcome,launch_state,complete_plan,total_step_count,completed_step_count,
 failed_step_ordinal,started_at,finished_at,duration_ms,cpu_time_ms,
 peak_job_memory_bytes,total_process_count,plan_blob_object_id,plan_raw_digest,
 plan_id,plan_version,plan_semantic_digest,runner_implementation_version,
 runner_implementation_digest,runner_policy_digest,sandbox_provider,
 sandbox_policy_digest,runtime_digest,
sanitized_result_digest}`; it is not a child-output digest. A verification-
criterion link uses relation
`runner_observation`. This is
never a Verification Receipt and never uses
`bound_attestation/trusted_caller/1`. A no-launch observation proves only the
closed resolver/policy fact; a launched observation proves only what the
taskgov Runner directly observed under its recorded boundary.

### Bundle, JSON, Viewer, And Analyzer Compatibility

Every already-written Bundle-v1 file remains byte-for-byte immutable. The
fixed `index.json` is a replaceable projection commit point, not an immutable
historical artifact: the first schema-v20 publication replaces its v1 envelope
with v2 while referencing the unchanged v1 files. No v1 bundle is regenerated.

Migration 20 rebuilds `completion_evidence_bundles` under the existing verified
copy/swap pattern because its v19 CHECK is fixed to schema 19/bundle 1. Every
existing column value is copied exactly; nullable
`verification_basis_kind TEXT` and
`verification_runner_observation_id TEXT` are added. The new row matrix is
`19/1` with both fields null, or `20|21/2` with kind
`caller_attestation|runner_observation|not_required` and the same tagged-union
link matrix as the cycle. A supplemental non-selected Runner observation is
frozen only through the existing Bundle member/Reference relation. Schema-v20
writers reject source 21 and the Runner-selected kind; migration 21 need not
rewrite a Bundle row.

Bundle v2 uses exactly
`{"bundle_digest":"sha256:<64-lowercase-hex>","format_version":2,
"payload":<bundle-payload>}` plus LF. Its digest is `sha256:` plus SHA-256
over `taskgov-completion-evidence-bundle-v2\0` and canonical payload bytes
without LF. The payload retains the exact v1 key set and rules, changes only
`source_schema_version` to `20|21` and `bundle_version` to `2`, and adds exactly
`verification_basis` and `runner_observation`. No other v1 key changes shape,
except that the v2 `verification_receipt` nullability follows the tagged union
below rather than the v1 criterion-presence rule.

`verification_basis` is exactly
`{basis_version,kind,runner_observation_id,verification_receipt_id}` and obeys:

| Source schema | basis/version and kind | Receipt ID | Runner observation ID |
|---|---|---|---|
| 20 | `1/caller_attestation` | non-null | null |
| 20 | `1/not_required` | null | null |
| 21 | `2/runner_observation` | null | non-null |
| 21 | `2/caller_attestation` | non-null | null |
| 21 | `2/not_required` | null | null |

Thus XOR applies only to the two evidence-bearing kinds; `not_required` has
both links null. `verification_receipt` is the existing exact object only for
`caller_attestation`, otherwise null. A Runner ID is selectable only from a
gate-eligibility-version-2 exact-current complete-plan pass.

`runner_observation` is null or exactly:

```text
{complete_plan,completed_step_count,cpu_time_ms,duration_ms,
 failed_step_ordinal,finished_at,gate_eligibility_version,launch_state,
 observation_id,outcome,peak_job_memory_bytes,total_process_count,
 plan_blob_object_id,plan_id,plan_raw_digest,plan_semantic_digest,plan_version,
 reason,route,runner_implementation_version,runner_implementation_digest,
 runner_policy_digest,runtime_digest,
 sandbox_policy_digest,sandbox_provider,sanitized_result_digest,started_at,
 total_step_count}
```

Every numeric field is integer or null only where the storage contract permits;
IDs/digests/text use their storage grammar. Plan and sandbox/runtime fields are
null exactly when the immutable resolution did not reach that stage. Schema
v20 may include one exact-current eligibility-zero terminal observation, but
its basis Runner ID is always null. Schema v21 caller fallback may likewise
include its no-launch observation without selecting it. A selected Runner ID
must equal the object ID. Runner Reference/link membership follows the same
object and never itself selects a gate basis.

Evidence index v2 is exactly
`{"format_version":2,"index_digest":"sha256:<64-lowercase-hex>",
"payload":<index-payload>}` plus LF. Payload keys remain exactly
`{bundle_count,entries,legacy_count,project_id,projection_generation,
source_schema_version}` with source schema `20|21`. Each entry is exactly
`{bundle_digest,bundle_file,bundle_format_version,bundle_id,bundle_state,
completion_cycle_id,cycle_ordinal,file_digest,sealed_at,task_id}`. Native state
is `native`, format is integer `1|2`, and all identity/file/digest/time fields
are strings. `legacy_unknown` makes format and those five nullable identity/
file/digest/time values null. Ordering/count/size/publication rules remain v1.
`index_digest` uses domain `taskgov-evidence-index-v2\0` and canonical payload
bytes. The file digest remains SHA-256 over complete bundle-file bytes.

Bundle/index v2 validation recomputes every tagged-union source, assurance,
binding, observation/reference digest, and file/payload digest. M23 consumers
accept v1/v2, render a present Runner observation as distinct
`machine_observed/verification_runner/1`, and give it no Analyzer or
independent gate authority. Derived reports remain outside canonical evidence.

Viewer snapshot remains version 4. TG-M24.2 extends its source-schema range to
v5-v20 and TG-M24.3 to v5-v21, validates and discards the new source fields,
and adds no Runner panel, field, filter, action, or status inference. Setup,
doctor, Evidence publication, post-commit maintenance, backup, restore, and
rollback include the versioned data through their existing bounded owners and
add no public command.

### Public Projection Without A New Leaf

TG-M24.2 may add one fixed `verification_runner` object to `review target set`
and Task `show` successful JSON data. It is exactly
`{eligibility,observation_id,outcome,phase,reason,route,schema_version,
target_generation}`. `schema_version` is integer `1`; `phase` is
`shadow|gate`; route is null or `runner|m21_fallback|blocked`; eligibility is
`shadow|legacy_m21|not_evaluated|not_required|runner_eligible|m21_fallback|
blocked`; outcome is null or the complete observation outcome enum; reason is
null or the complete reason enum; observation ID is null or its stable ID; and
target generation is the current nonnegative integer (`0` means no target).
Command failures retain the existing empty/error data contract and never
project a partial object.

The public projection is the following closed matrix. `terminal.*` means the
value from the unique validated terminal observation; `resolution.*` is used
only when that terminal is absent. Generation is zero only with no target and
otherwise equals the current target generation.

| Source/state | phase | eligibility | route | outcome | reason | observation ID |
|---|---|---|---|---|---|---|
| v20, no target | `shadow` | `not_evaluated` | null | null | null | null |
| v20, target without criterion | `shadow` | `not_required` | null | null | null | null |
| v20, criterion target with terminal | `shadow` | `shadow` | `terminal.route` | `terminal.outcome` | `terminal.reason` | `terminal.id` |
| v20, criterion target with resolution but no terminal | `shadow` | `shadow` | `resolution.route` | null | `resolution.reason` | null |
| v20, criterion target with no Runner row | `shadow` | `shadow` | null | null | null | null |
| v21, no target | `gate` | `not_evaluated` | null | null | null | null |
| v21, target without criterion | `gate` | `not_required` | null | null | null | null |
| v21, marker zero | `gate` | `legacy_m21` | null | null | null | null |
| v21, marker two, exact complete Runner pass terminal | `gate` | `runner_eligible` | `terminal.route` | `terminal.outcome` | `terminal.reason` | `terminal.id` |
| v21, marker two, closed M21-fallback terminal | `gate` | `m21_fallback` | `terminal.route` | `terminal.outcome` | `terminal.reason` | `terminal.id` |
| v21, marker two, any other valid terminal | `gate` | `blocked` | `terminal.route` | `terminal.outcome` | `terminal.reason` | `terminal.id` |
| v21, marker two, resolution but no terminal | `gate` | `blocked` | `blocked` | null | `terminal_missing` | null |

Thus a terminal always wins over its resolution, and a legacy marker-zero
generation never projects historical shadow fields. A corrupt/missing
resolution, parent mismatch, unknown enum, or impossible matrix is the existing
command failure with empty/error data, not a synthesized successful object.
Schema-v20 fields remain audit-only and cannot change target-set exit status or
satisfy/block M21 verification, Review, or completion.

Text mode appends exactly one sanitized line to both successful commands:

```text
Verification runner: phase=<phase> route=<route|none> eligibility=<eligibility> outcome=<outcome|none> reason=<reason|none> observation=<id|none> generation=<integer>
```

TG-M24.3 adds exactly `selected_basis_kind` and `selected_basis_source_id` to
the existing Task-show `verification_evidence.gate` object. Kind is null or
`runner_observation|caller_attestation|not_required`; source ID is the selected
observation/Receipt ID or null for not-required/unsatisfied. Existing
`qualifying_receipt_id` is non-null only for caller attestation. No completion-
history shape changes. There is no caller flag or public Runner/cancel/retry/
status leaf. Errors never expose plan body, argv, output, runtime path,
environment, ACL, SID, profile name, or OS detail.

### Schema-v21 Gate And M21 Fallback

Existing completion-cycle verification basis 0 (legacy absence) and 1 (M21
caller Receipt/not-required) never change. Schema v20 adds nullable
`verification_basis_kind` and `verification_runner_observation_id` foundation
columns to completion cycles; every basis-0/1 row keeps both null. It also adds
`tasks.review_target_runner_basis_version INTEGER NOT NULL DEFAULT 0 CHECK
(review_target_runner_basis_version IN (0,2))`. Existing and schema-v20 targets
use zero and schema-v20 Task writers/read validators reject `2`. Schema-v20
Runner-table DDL fixes every `gate_eligibility_version` CHECK to `=0`.

Schema v21 rebuilds `task_completion_cycles` solely to expand the existing
basis CHECK from `0|1` to `0|1|2`; every old column value and both new nulls are
copied exactly, counts/projections/FKs are verified, and no old row is updated.
It likewise rebuilds the three eligibility-bearing Runner tables only to expand their eligibility
CHECK to `IN (0,2)`, copying every eligibility-zero row unchanged. New native
cycles use basis 2. Its kind matrix is exact:

| Kind | `verification_receipt_id` | `verification_runner_observation_id` |
|---|---|---|
| `runner_observation` | null | non-null |
| `caller_attestation` | non-null | null |
| `not_required` | null | null |

The XOR therefore applies only to evidence-bearing kinds. Schema-v21 target-set
for a criterion-bearing Task writes target-basis version `2` atomically with a
new resolution whose eligibility version is `2`. A Task without a verification
criterion writes marker zero and no Runner row; reopen/target clear likewise
resets the marker to zero.
Migration never advances that marker or any resolution/observation. Every
schema-v20 eligibility-zero row is permanently shadow: it may remain visible
and bundled but can never qualify or block a v21 gate. A fresh schema-v21 target
and fresh eligibility-two observation are required to gain Runner authority.
Readers reject marker-zero with an eligibility-two row and marker-two with a
missing or eligibility-zero resolution; only marker-zero/no-row-or-zero and
marker-two/exact-two are valid for a criterion-bearing target.

The evaluator first validates the current Task/Contract/authority/criterion/
target and Task target-basis marker. When verification is not specified it
selects basis-2 `not_required`. When the marker is zero, it ignores every
shadow Runner row and preserves the M21 path: the unique exact-current
`pass/full` Receipt selects basis-2 `caller_attestation`. When the marker is
two, it requires one exact eligibility-two resolution and applies:

1. A current candidate resolution and terminal observation both on `runner`
   that launched under the exact implementation/plan/policy and has its matching
   cleanup-success event may use only one exact-current, complete-plan `pass`
   observation. It selects `runner_observation` and no
   Receipt.
2. Any launched `fail`, timeout, cancellation, resource/sandbox/output error,
   process/controller error, or post-launch drift blocks completion. A M21
   Receipt cannot override it; repair requires a fresh target and fresh run.
3. A current closed observation on `m21_fallback/no_launch`, whether resolved
   directly or proven unavailable before process creation, selects the existing
   unique exact-current `pass/full` Receipt and records `caller_attestation`.
4. A `blocked`, missing terminal, ambiguous, corrupt, stale, or unknown Runner
   state blocks completion and never silently becomes M21 fallback.

`verification receipt add` remains the same public leaf. Under schema v21 it is
accepted for a marker-zero legacy-M21 target or a marker-two current
`m21_fallback` generation, and rejected for Runner-managed/blocked generation.
Review prepare and complete recompute the same selected basis; neither starts a
process or chooses a route. Plan, Task, Contract, authority, criterion, target,
Runner implementation/policy/sandbox, or runtime drift invalidates the current Runner basis
without deleting its observation.

Supported Runner use therefore removes the normal `verification receipt add`
action. The synchronized Skill branch is exact: `runner_eligible` proceeds to
review prepare without a Receipt; `m21_fallback|legacy_m21` performs the
existing Receipt action; `not_required` proceeds without one; `blocked` stops;
and `not_evaluated` follows the existing target prerequisite. Schema-v20 Skill
text remains unchanged and therefore still records M21 evidence. This is one
deterministic branch over an existing call result, not a new LLM choice, test-
selection step, or command.

Reopen preserves the historical cycle, basis version/kind/source, Bundle,
Receipt, and observation. The new active cycle needs a fresh target generation
and current evidence. Migration fabricates no plan, observation, v2 basis,
Reference, link, marker advance, eligibility upgrade, or stronger assurance.

### Bounded Unit Split And Acceptance Matrix

TG-M24.2 implements schema v20, the strict plan parser, exact Git
materializer, Runner-specific Win32/AppContainer/Job boundary, coordinator,
append-only observation and Reference/link storage, automatic target-set
trigger, shadow projections, Bundle/JSON v2, M23 consumer support, Viewer-v4
v20 compatibility, state resolver/cleanup, package candidate synchronization,
and focused/full offline tests. All gate writers still require M21 basis
version 1. Live execution is limited to bounded repository-owned test fixtures;
another real project needs separate exact authority. Mock process tests do not
substitute for real Windows containment tests.

TG-M24.3 implements schema v21, basis-version-2 storage constraints and
evaluator, Receipt admission restriction, completion/Review/Task projection,
Bundle v2 selected-basis validation, Viewer-v4 v21 compatibility, concise
Skill/reference branching, migration/reopen/history behavior, package/release
candidate synchronization, and zero-added-call tests. It must not broaden the
plan or process provider.

TG-M24.4 runs the integrated matrix and may make only corrections required by
this design. The matrix includes:

- absent, disabled, manual, visual, external, unsupported-toolchain, malformed,
  duplicate, stale-Contract, stale-expectation, and stale-criterion plans;
- exact snapshot/commit materialization, unstaged/untracked exclusion, unsafe
  path/mode/object/size rejection, and before/during/after target drift;
- real Windows pass/fail, descendant and pipe-hold, timeout, Ctrl-C,
  controller loss, two-controller exclusion, process/memory/CPU limits,
  exact total-process accounting,
  atomic creation-time Job assignment, per-attempt AppContainer profile
  create/ownership-event/delete/crash cleanup including the ambiguous-create
  no-delete branch, clean-environment sentinels, immutable target, zero-byte
  child disk-write budget across scratch/profile file and registry storage,
  governed-root and canonical-state write denial, direct network denial,
  runtime and release-manifest-bound Runner implementation identity/drift,
  handle cleanup, and no weaker-provider fallback;
- the direct-provider real suspended-child portability matrix exercising the host's actual
  fixed-DWORD class-46-success or exact error-87 semantic route without SKIP,
  including, when the host selects error 87, a never-resumed normal-
  AppContainer control in its separate Job, the identical four-attribute lists
  whose AAP-policy DWORD alone differs as normal `0` versus LPAC `1`, normal AAP
  allow, LPAC AAP deny, same-LPAC exact-Package-SID allow, each through the
  exact SYSTEM-owned/grouped two-ACE coordinator-user-plus-selected-
  application descriptor with bit `0x00000001` and no extra ACE, non-decisional
  `WIN://NOALLAPPPKG`, every failure/unknown no-resume path, and complete
  control/LPAC cleanup; a fault-injected integration test forces the opposite
  branch, and pure helpers may cover it additionally, without calling either a
  native selector result or native selector evidence; this direct proof covers
  both separate Jobs and tokens, the allow/deny/allow triple, no-resume paths,
  and cleanup without calling `run_process_steps` or depending on the Runner
  registry route;
- output flood and secret/traceback/diff-like output proving raw bytes never
  enter SQLite, Bundle, JSON, Viewer, diagnostics, backup, or logs;
- schema19-to-20-to-21 migration, idempotent setup, too-new rejection,
  rollback/backup, legacy and reopen cycles, v1/v2 Bundle/index preservation,
  M23 Analyzer coexistence, Viewer compatibility, and package inventory;
- permanent v20 shadow pass/fail inertness across migration; fresh-v21 Runner
  pass qualification, every eligibility-two non-pass block, fresh-generation
  recovery, honest legacy/current M21 fallback, Receipt override rejection,
  Review/completion preservation, and supported/fallback Skill call counts.

Offline temporary Git projects and taskgov-owned Win32 fixtures are sufficient
for realistic acceptance. Their AppContainer profile mutation is limited to
the exact disposable taskgov moniker lifecycle above. Normal/owned cleanup must
leave no profile/root residue; the injected create-before-ownership-record crash
must instead prove no foreign deletion, global launch blocking, and successful
repair after the fixture owner removes the ambiguous profile. No live external service, credential, paid model,
network destination, or unrelated external project is used without separate
exact authority. Tests never weaken an assertion or substitute caller
attestation for a required machine-observed boundary.

The current repository lane policy owns exactly one mandatory native test:
`test_m241a_lpac_portability.RunnerLpacPortabilityNativeTests.test_real_lpac_portability_matrix_and_cleanup`.
When selected by
`integration` or `all`, its `unittest` SKIP makes that lane non-PASS even when
the remaining result is otherwise successful. A missing, renamed, duplicate,
or non-`integration` assignment fails policy validation. A lane that does not
select the ID is unaffected, and unrelated optional SKIPs retain their
existing meaning. The normal-AppContainer/LPAC positive matrix therefore
completes without SKIP on a supported governed Windows lane. Its native
evidence comes from the direct private-seam proof above, independently of
`run_process_steps` and the full process/registry matrix. The latter is a
current TG-M24.2 completion gate but is not yet part of the repository
mandatory-native set. TG-M24.2 must add exactly
`test_m242_runner_process.RunnerProcessNativeTests.test_real_process_matrix_and_cleanup`
as a second closed mandatory-native ID with non-SKIP enforcement before
acceptance; until then, the standalone portability ID remains the complete
current set.

This accepted design and its accepted bounded correction activate no runtime,
process, schema, gate, CLI, Skill, network, credential, or target mutation.
Current TG-M24.2 authority permits only implementation toward its exact gates;
it does not make incomplete Runner code or the full native matrix active.

<a id="tg-m24-1a"></a>

## TG-M24.1A Accepted Windows LPAC Proof Portability Correction

Task `tg_task_56e212c793a42272` accepted only the bounded M24.1 LPAC proof
correction through one private, import-inactive package seam,
`_verification_runner_lpac_win32.py`, its direct suspended-child native fixture
and focused pure/fault tests in `test_m241a_lpac_portability`, and the one-ID
repository mandatory-native lane gate. It retains the exact class-46 proof
where supported and adds only the exact error-87 public-`AccessCheck` route
above. It does not copy or activate the broader TG-M24.2 provider, process, or
registry implementation. It changes no platform support, plan, schema,
storage, public CLI/JSON, Skill, M21 gate, M24.3 behavior, network authority,
external-project authority, diagnostic retention, or assurance class.
Registry `0x80070002` and every other registry failure remain fail-closed,
never count as success evidence, and are owned by current TG-M24.2. The
accepted correction is TG-M24.2's required dependency and permanent private
seam; it does not itself activate the broader Runner.

Completion requires document-contract and release checks; focused private-
seam, fault, and lane-policy tests; a real suspended-child matrix covering the
host's actual class-46 or exact error-87 semantic route without SKIP, a
fault-injected integration test forcing the opposite route without claiming
native selector evidence, identical four-attribute lists whose sole value
difference is AAP policy normal `0` versus LPAC `1`, both AAP sentinels, exact
Package-SID allow, exact SYSTEM owner/group and two-ACE coordinator-user-plus-
selected-application descriptors with non-generic bit `0x00000001` and no
extra ACE, no-resume failure/unknown paths, and cleanup; and proof that the
selected mandatory native test's SKIP makes its lane non-PASS. The portability
proof does not call `run_process_steps` or depend on the full process/registry
matrix. The full matrix test,
`test_m242_runner_process.RunnerProcessNativeTests.test_real_process_matrix_and_cleanup`,
is the current TG-M24.2 non-SKIP completion gate and was not in this accepted
Task's lane policy or commit. It must be added to the repository mandatory-
native set as part of TG-M24.2's verified completion. The remaining
gates are the full offline suite and exact diff; a current `pass/full`
Verification Receipt; and two independent Tier 2 PASS reviews with no
unresolved High or Medium finding.

<a id="tg-m24-2"></a>

## TG-M24.2 Current Shadow Runner And Evidence Capture Authority

Task `tg_task_fafad7bc62df7576` may implement only the schema-v20 shadow slice
assigned above. Existing M21 Receipt and completion gates remain authoritative;
a Runner resolution or outcome can neither satisfy nor block completion nor
mutate Task, Review, Receipt, or selected gate state. Live external-project
execution requires its own exact current authority.

This authority transition changes no package byte, schema, command, or active
runtime. Implementation must integrate the accepted
`_verification_runner_lpac_win32.py` seam rather than duplicate its token and
LPAC proof. A missing initial registry handoff or cleanup failure forbids the
final proof and resume. HRESULT `0x80070002` remains a hard failure to diagnose
and repair through the locator-to-coordinator handoff above, never an absence,
SKIP, fallback, or successful registry proof.

Completion requires the exact parser/materializer/AppContainer/Job/lifecycle/
privacy boundary, append-only exact-basis observations, inert Bundle/JSON v2
linkage, no shell or ambient target execution, and real Windows containment
tests, including the full
`test_m242_runner_process.RunnerProcessNativeTests.test_real_process_matrix_and_cleanup`
process/registry matrix without SKIP and activation of that exact stable ID as
the second repository mandatory-native gate;
`0x80070002` and every other registry failure fail closed and never count as
success. The remaining gate includes migration, state, Viewer compatibility,
package, focused/full
offline checks, exact diff, a current Verification Receipt, and two independent
Tier 2 reviews with no unresolved High or Medium finding.

<a id="tg-m24-3"></a>

## TG-M24.3 Inactive Gate Integration And M21 Fallback

Task `tg_task_dc015144091f8e60` may implement only the schema-v21 gate slice
assigned above after accepted TG-M24.2. One qualifying exact-current complete-
plan Runner pass may satisfy verification; every launched non-pass blocks; only
a closed no-launch fallback may use the M21 Receipt. Analyzer evidence, old
evidence, arbitrary command choice, and caller override gain no authority.

Completion requires exact basis selection, invalidation and fresh-target
recovery, immutable legacy/reopen history, Bundle/JSON/Viewer/M23 consistency,
the deterministic Skill branch with one fewer supported-flow action and no new
leaf, migration, full offline/privacy/package/release checks, exact diff, a
current Verification Receipt, and two independent Tier 2 reviews with no
unresolved High or Medium finding.

<a id="tg-m24-4"></a>

## TG-M24.4 Inactive Integrated Acceptance

Task `tg_task_f81f2d126f033a59` accepts the exact schema-v20 Runner and
schema-v21 gate across the complete matrix above. Only bounded corrections
required by accepted TG-M24.1 and its bounded M24.1A portability correction
are allowed. Realistic supported fixture tasks must
complete without a manual Receipt action; honest unsupported/manual cases must
retain it; failures and uncertainty must never become success.

Completion requires focused/full offline and authorized realistic forward
checks, exact diff, a current Verification Receipt, and two independent Tier 2
reviews with no unresolved High or Medium finding. Execution outside bounded
fixtures or this repository, publication, push, tag, Release, external service,
network destination, or credential use needs separate exact authority.

## Deferred Detail Rule

The exact design above, including the bounded TG-M24.1A portability correction,
owns the M24.2-M24.4 schema, storage, plan, process, projection, gate,
compatibility, and acceptance boundary. A correction may
clarify or repair that boundary but may not add another executable resolver,
platform/provider, plan field, public leaf, normal-loop action, output-retention
mode, Analyzer gate, external execution, fallback removal, or assurance
upgrade. Such expansion requires separate explicit authority.
