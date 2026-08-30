> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> - Source path: `docs/execution-contracts/tg-m23-process-safety.md`
> - Source commit: `cd08834d023ac5967e2ae18004aff7b41277ee99`
> - Current replacements: [docs/authority.md](../../../authority.md),
>   [docs/specification.md](../../../specification.md),
>   [docs/design.md](../../../design.md), and
>   [plan.md](../../../../plan.md). Use the public CLI for live Task state and
>   evidence.
> - Capture unit: `TG-DOC.3`
>
> The exact source blob begins below.

# TG-M23 Windows Process Safety And Atomic Publication Contract

<a id="tg-m23-process-safety"></a>

> [!IMPORTANT]
> DELEGATED FORMAL AUTHORITY: sole owner of routed TG-M23 Windows
> process containment, private temp, and atomic publication/recovery. TG-M23.1-.3
> offline/mock scope is accepted; no unit is current. It owns no Analyzer
> acceptance, network/live model, public CLI/Skill, schema, gate, or Task
> mutation.

The [TG-M23 core owner/router](tg-m23-derived-evidence.md#tg-m23-1) alone owns sequence/Task bounds, descriptor/packet/status/report/provenance/citation semantics, outcome codes, activation, permissions, and gates; [specification](../specification.md)=behavior, [design](../design.md)=structure, Task DB=live state/evidence. This document creates no second owner.

## Parent Route

Only the [TG-M23 core owner/router](tg-m23-derived-evidence.md#tg-m23-1) reaches this owner; a direct read/link neither bypasses the parent unit nor activates behavior.

## Delegated Interface And Scope

The core passes one bounded attempt input `(analysis_job_id,N,packet_digest,stdin_bytes,argv,E,cancel)` and the exact validated report/Markdown bytes and digests when publication is requested. This owner returns only a bounded adapter outcome, duration, a sealed result or null, `tree_quiescent`, and `publish_ready`. It cannot add or reinterpret report content, evidence meaning, status fields, digests, retry eligibility, or a caller-visible launch.

`C` is the controller, `B` the fixed private one-shot broker, `T` the target, and `J` the per-attempt Job. `S` is `output-schema.json`, `O` is `output.json`, `I` is the sealed input mapping, `Q` is the sealed result mapping, and `Vb/Vc` are the broker/controller synchronization events. Pagefile-backed IPC means no taskgov file; it does not claim absence from OS page, hibernation, or dump storage.

This owner applies only to the ignored `<canonical-package-state>/analysis/` tree and the fixed core namespaces. SQLite, `storage.py`, schema, setup, doctor, maintenance, public CLI, Skill, Task loop, network, and live provider use remain outside TG-M23.

## Lease, Private Tree, And Quarantine

`taskgov-analysis.lock` is no-follow/noninheritable; byte 0 is held by `LockFileEx(...,LOCKFILE_EXCLUSIVE_LOCK|LOCKFILE_FAIL_IMMEDIATELY)` before root inspection, counting, input, or status open, through terminal publication/quarantine. Busy/uncertain returns deferred `interrupted` with no creation, inventory, or durable read/write. Parallel/read-only sessions are unsupported. Final release is one `UnlockFileEx` then `CloseHandle`.

Under lease C creates and holds one absent-before-open `tmp/.taskgov-analysis-<8-lower-alnum>/` root per worker count or publication-eligible no-adapter result; pre-existence fails before count/publication and identity/bytes/token/old zero never proves freshness. Adapter leaf order is `S,O,report.json,report.md`: B delete-on-close-holds S/O and C holds reports. For a core-authorized no-adapter result, C creates only TH-held reports; S/O never exist, held enumeration proves exact `{report.json,report.md}` and S/O absence, and C's ledger proves no B/T/J/restricted-token/mapping/event/pipe/stdio/worker handle creation. Those identity/membership/never-created facts are the core no-adapter-tree proof. Adapter success proves S/O absence; failed tree proof closes S/O unread, claims no immediate absence, and follows quarantine below.

No-follow preflight requires the exact contained non-reparse directory identity and at most 32 entries. Existing roots are quarantine: never traverse, read, change, delete, claim, or reuse them. Entry 33, an unexpected type/name/identity, escape, overflow, or inspection error stops. Only the current attempt root may become `tree_quiescent`; while live it is B-owned. More than 100,000 regular files in the durable analysis directory fails closed, with no partial index or SQLite record.

Every retry uses a fresh N, root, J, B, I, Q, Vb/Vc, S, O, T, pipes, and workers. Run-wide C DACL/thread freeze may be established once before attempt 1 and is revalidated before each B receives I. No attempt reuses a path, handle, process, Job, mapping, event, pipe, output, timestamp, or modification time from its predecessor.

## File Handles, Freshness, And Transient Output

Aliases `(access,share,disposition,flags)`: `RD=FILE_READ_DATA`, `RA=FILE_READ_ATTRIBUTES`, `WD=FILE_WRITE_DATA`, `DL=FILE_LIST_DIRECTORY`, `DA=FILE_ADD_FILE|FILE_ADD_SUBDIRECTORY`, `CR=READ_CONTROL`, `SY=SYNCHRONIZE`, `SR=FILE_SHARE_READ`, `SW=FILE_SHARE_WRITE`, `SD=FILE_SHARE_DELETE`, `OI=FILE_OPEN_IF`, `X=FILE_FLAG_OPEN_REPARSE_POINT`, `K=FILE_FLAG_BACKUP_SEMANTICS`, `D=FILE_ATTRIBUTE_TEMPORARY|FILE_FLAG_DELETE_ON_CLOSE|X`.

Tuples: `OP=(DELETE|RD|RA|SY,SW|SD,CREATE_NEW,D)`, `OC=(WD|SY,SR|SD,OPEN_EXISTING,X)`, `SP=(DELETE|RD|WD|RA|SY,SR|SD,CREATE_NEW,D)`, `SC=(RD|RA|SY,SR|SW|SD,OPEN_EXISTING,X)`, `TH=(CR|DELETE|RD|WD|RA|SY,0,CREATE_NEW,X)`, `RH=(CR|DELETE|RD|RA|SY,0,OPEN_EXISTING,X)`, `S0=(CR|DL|DA|RA|SY,SW,OI,K|X)` (access `0x00120087`, share `0x2`), `R0=(CR|DELETE|DL|DA|RA|SY,0,OPEN_EXISTING,K|X)`, `DP=(DL|DA|RA|SY,SW,OPEN_EXISTING,K|X)`. S0 exists solely for the exclusive lease owner's atomic status CAS; it is acquired after lease and held through that lease's status session. SW authorizes no second/parallel/read-only session; directory DELETE/share-delete, root/outbox/lease R0, RH, and RC remain unchanged. `DF=FileDispositionInfo.DeleteFile`; `AR=FILE_RENAME_INFORMATION(TRUE,held-S0,exact-basename)` and `NR=FILE_RENAME_INFORMATION(FALSE,held-DP,exact-basename)` each run once on held TH via typed `NtSetInformationFile(...,FileRenameInformation)`. B owns O/S through OP/SP; T gets only OC/SC. Opens are no-follow/identity-bound; live tuples are never reclaimed.

`CU`=exact current primary `TokenUser` SID; `RC=WinRestrictedCodeSid=S-1-5-12`, `OW=OWNER RIGHTS=S-1-3-4`, `FT=FILE_TRAVERSE`, `WC=WRITE_DAC`, `WO=WRITE_OWNER`. Root/report-temp SDs=protected/noninheriting/canonical; no default/NULL/generic/other ACE. Root exact DACL=`DENY OW(WC|WO);ALLOW CU(R0.access);ALLOW RC(FT|RA)`; report-temp DACL=`DENY OW(WC|WO);ALLOW CU(TH.access)`, no RC ACE. O exact DACL=`DENY OW(WC|WO);ALLOW CU(OP.access|OC.access);ALLOW RC(OC.access)`; S/stdio retain exact-alias CU/RC dual allows plus `DENY OW(WC|WO)`. T cannot list/add/reopen reports, rename/delete/change security, or reach DP/durable data.

B flushes and revalidates S before C atomically records N and `running`; a crash consumes N, never a timestamp. M23.2 O is one closed-code immutable fixture. The held fresh O plus `(analysis_job_id,N,packet_digest)` binds the only candidate result. Partial, replaced, late, wrong-attempt, wrong-packet, or post-read-changed output is `invalid_output`. Rejected bytes, stdout/stderr prefixes, prompts, packet bytes, and provider bodies are discarded before terminal state and never enter report, durable state, logs, or quarantine.

## Optional Adapter Process Boundary

<a id="optional-adapter-process-boundary"></a>

The core's exact argv runs with an absolute `lpApplicationName`, a digest-bound immutable isolated runtime/copy, a fresh empty non-Git cwd, and a fresh private `CODEX_HOME`. Child environment E is a non-null double-NUL-terminated Unicode block with exactly ordered `CODEX_HOME,PATH,PATHEXT,SystemRoot,TEMP,TMP`, no duplicates, `=drive` entries, or other keys. Saved authentication, API-key variables, inherited provider/network configuration, console, site, plugin, user config, rules, ambient cwd, or ambient DLL lookup are never reused. B performs no semantic parse and only bounded opaque copy.

The stdin writer sends only the core's exact bounded frame. Stdout and stderr are concurrently drained into separate 65,536-byte in-memory prefixes and then discarded; neither is report input. S is strict and private. Live/provider mode is `policy_blocked` without separate containment/broker authority, an immutable runtime/home, absence of credentials and parent handles, and the core's separate model/data/cost authority.

## Restricted Tokens And Parent/Desktop Containment

`SDC=DELETE|READ_CONTROL|WRITE_DAC|WRITE_OWNER`; `PR=PROCESS_ALL_ACCESS`; `TR=THREAD_ALL_ACCESS`; `WR=WINSTA_ALL_ACCESS|SDC`; `DR=DESKTOP_ALL_ACCESS|SDC`; and `BP=SeChangeNotifyPrivilege|SeAssignPrimaryTokenPrivilege|SeIncreaseQuotaPrivilege`. A valid PR/TR bit is a documented access right in the repository's minimum supported modern Windows SDK; an SDK/bit-set mismatch is pre-count `policy_blocked`. `P(U)` means U receives `ACCESS_DENIED` for each valid PR/TR bit, their union, and `MAXIMUM_ALLOWED` on C and every C thread.

C creates separate sibling primary tokens from C-original. `TT=CreateRestrictedToken(flags=0,DeletePrivileges=all-except-{SeChangeNotifyPrivilege},SidsToRestrict=[(RC,0)])` must have the exact singleton `TokenPrivileges`, exact `TokenRestrictedSids=[RC]`, restricted state, no `WRITE_RESTRICTED`, and inert `OWNER RIGHTS`. `BT=CreateRestrictedToken(C-original;flags=0;delete=all-except-BP;restrict=[])` must have exact BP, no restricted SIDs, and every other privilege permanently deleted and unaddable, including debug, ownership, backup, restore, and DACL bypass.

Before B, C holds its required self handles and a sole probe-thread handle. C process and every current C-thread DACL deny the current user and `OWNER RIGHTS` all PR/TR; `P(duplicate-user)` must pass. C closes the probe and creates no later thread. C's BT creation handle has exactly `TOKEN_ASSIGN_PRIMARY|TOKEN_DUPLICATE|TOKEN_QUERY`. Broker environment BE is a non-null double-NUL-terminated Unicode block with exactly ordered `SystemRoot,TEMP,TMP`; TEMP/TMP name the fresh current root, and no duplicate, `=drive`, PATH, profile, config, proxy, credential, or other key exists.

B is atomically launched suspended with `CreateProcessAsUserW(BT)`: absolute digest-bound immutable broker `lpApplicationName`; fixed argument bytes; `lpCurrentDirectory` equal to the fresh current root; BE; `bInheritHandles=TRUE`; exact flags `EXTENDED_STARTUPINFO_PRESENT|CREATE_SUSPENDED|CREATE_UNICODE_ENVIRONMENT|CREATE_NO_WINDOW`; and `STARTUPINFOEXW` with no `STARTF_USESTDHANDLES` and exactly two attributes, `JOB_LIST=[J]` plus `HANDLE_LIST=[IB,QB,VbB,VcB,TTB]`. IB/QB/VbB/VcB/TTB are fresh inheritable duplicates made only for this call with exact rights `SECTION_MAP_READ`, `SECTION_MAP_WRITE`, `EVENT_MODIFY_STATE`, `SYNCHRONIZE`, and `TOKEN_ASSIGN_PRIMARY|TOKEN_DUPLICATE|TOKEN_QUERY`; every original and every nonlisted handle is noninheritable and excluded. Attribute-list construction, listed-handle inheritance, Job membership, and process/thread-handle noninheritance must be proved before exactly one successful B `ResumeThread`; C then closes B's primary-thread handle and every parent-side inheritable duplicate. Any create, attribute, inheritance, membership, or resume uncertainty enters the abnormal Job path without an ambient-inheritance fallback. B receives no J, lease, C process/thread, destination-parent, durable-file, stdio, console, or ambient handle. Before reading I, B's actual token must reprove exact BP and `P(B)`; deleted privileges remain unaddable.

Before USER/GDI, a window, hook, worker, or T exists, B creates a fresh noninheritable explicit-SD station and desktop in this order: `CreateWindowStationW(NULL,CWF_CREATE_ONLY,...)`, `SetProcessWindowStation`, `CreateDesktopW(L"default",...,0,...,explicit-SD)`, `SetThreadDesktop`. `GetUserObjectInformationW` supplies the exact returned `station\\default` name. The private station/desktop dual-allow the current user and RC exact WR/DR; C's original station/desktop and all C/B process/thread/IPC/J/lease/DP/durable objects allow no RC.

C sets and queries exactly `JOB_OBJECT_UILIMIT_{HANDLES,READCLIPBOARD,WRITECLIPBOARD,SYSTEMPARAMETERS,DISPLAYSETTINGS,GLOBALATOMS,DESKTOP,EXITWINDOWS}` before T. B then creates its fixed workers and freezes thread and USER state. No B thread subsequently creates or receives a window, hook, clipboard, DDE, COM-STA, message-queue, or other desktop-bound IPC channel; the target desktop carries no broker receiver. TT must receive `ACCESS_DENIED`, per bit, union, and `MAXIMUM_ALLOWED`, for PR/TR on C, B, and every C/B thread, and WR/DR on C's original station/desktop.

T is created only by `CreateProcessAsUserW(TT)` with the returned `station\\default`, E, and exact stdio3 inheritance through `STARTF_USESTDHANDLES`, `HANDLE_LIST`, and `bInheritHandles=TRUE`. Flags are exactly `EXTENDED_STARTUPINFO_PRESENT|CREATE_SUSPENDED|CREATE_UNICODE_ENVIRONMENT|CREATE_NO_WINDOW`. J membership is non-breakaway and proved before exactly one successful `ResumeThread`; T receives no B/J/IPC/token/lease/durable handle and cannot open its parent B through the user SID, OWNER RIGHTS, RC, a desktop receiver, or an inherited handle.

## Job, IPC, Worker Ownership, And Termination

For each N, `J=CreateJobObjectW(...,NULL)` is fresh and unnamed. C is sole owner of its noninheritable kill-on-close/no-breakaway handle; no named or foreign Job is opened. I and Q are unnamed nonexecutable pagefile mappings: I is C-sealed and B has `SECTION_MAP_READ`; Q is B-write/C-read and contains only `version,state,analysis_job_id,N,packet_digest,length,digest,bytes`. Vb is B-modify/C-sync and Vc the inverse; T receives none. Events are synchronization, never child-tree proof.

N is 1..2. Monotonic budgets in milliseconds are attempt/all/proof/final=`120000/240000/5000/1000`; wall-clock time is untrusted. Retry requires proved cleanup plus `unavailable|launch_failed|timeout|invalid_output`, occurs only from N=1, and is forbidden after cancel. Uncertainty before recording N is `policy_blocked`.

Owners are C=`lease,J,B-process,B-primary-thread-until-resume,BT-creation-handle,TT-creation-copy,I,Q,Vb/Vc,IB/QB/VbB/VcB/TTB-until-launch-proof`; B=`station/desktop,TTB,T-process/thread,pipes,worker-thread-handles,S/O,Q-write`; T=`stdio3`. C has no I/O worker and B has no J, lease, destination-parent, or C handle. C closes its BT creation handle and inheritable duplicates after the proved B launch, its B primary-thread handle after resume, and its TT creation copy only after B confirms exact TTB; B closes TTB after T creation and closes every T/thread/pipe/worker handle in the ordered proof below.

B owns exactly one bounded stdin writer and one bounded drain worker for each of stdout and stderr. Each worker holds only its required pipe end, bounded input/prefix buffer, and its own thread state; it cannot write Q or access S/O. B owns all three thread handles. On normal T signal, B closes the stdin source as required, closes the broker pipe ends in protocol order, and joins all three workers within the 5,000-ms proof budget. A worker timeout, orphaned pipe end, incomplete EOF, or unjoined thread is an abnormal worker hang: B writes no terminal Q and C immediately uses the abnormal Job path. No worker may outlive B, a successful Q, or a controller return.

Success order is: T signals; B joins all workers and closes every T handle; two `QueryInformationJobObject(NULL)` reads prove `ActiveProcesses==1` and PID exactly `[B]`; B held-reads and caps O; B seals bound length/digest/opaque bytes into Q; B deletes and proves S/O absent; B signals terminal and exits. C accepts only `Q-terminal-valid ∧ B-signaled ∧ J.ActiveProcesses==0 ∧ stable zero/Q reread`, then validates Q. B exit, an event, a process handle signal, or one zero observation alone proves nothing.

Timeout, cancel, B crash, worker hang, partial Q, or any abnormal child state first latches the outcome, then calls `TerminateJobObject(J)` before waiting; no thread is terminated. C must prove B signaled and stable Job zero before mapping or reading Q, retrying, removing the root, unlocking, or returning. If that proof cannot be obtained, all those actions remain forbidden: the current tree is quarantine and C fail-fast holds the lease and sole J, with kill-on-close only as process-termination fallback.

Only a proved abnormal path may leave Q unread, prove S/O absent, remove the current root, close the N-specific B/I/Q/events/duplicate ownership set, and close that attempt's J. If N=1 remains retry-eligible, C retains the same lease continuously without `UnlockFileEx` or close while it creates the wholly fresh N+1 ownership set above. Otherwise J closure precedes the one final lease unlock/close and controller return. `tree_quiescent` means the required Job/process/worker/handle/file absence proof and ordered finalization all succeeded; otherwise result digests remain null and no path or object is reused.

## Atomic Publication And Recovery

<a id="atomic-publication-and-recovery"></a>

Before creating report temps or intent, C acquires and holds both noninheritable DPs and validates canonical identity, containment, and same-volume placement. Failure changes no destination, status, or R3, keeps core state `running`, and returns deferred `interrupted`. C creates both temps through TH, sets DF true before writing bytes and never uses `FILE_FLAG_DELETE_ON_CLOSE`, writes and flushes, held-rereads, and validates exact canonical bytes, privacy, bindings, digests, and caps.

`publish_ready` requires valid report/Markdown temps, both held DPs, the complete all-nonnull R3 intent bound to those bytes, no other private leaf, and either adapter `tree_quiescent` with S/O closed/absent or the no-adapter-tree proof with S/O never created. Intent order is JSON then Markdown. Promotion sets DF false and uses same-TH atomic NR through DP `RootDirectory` and exact basename; replace, copy, pre-delete, reopen, or path fallback is forbidden. An absent final completes; its held handle rechecks parent, name, identity, length, bytes, and digest. C removes the empty current root and proves absence, atomically records `published`, closes file handles, then DPs, then releases the lease. All handles remain held until their ordered close.

On failure, an unpromoted temp remains DF true through last close and original-identity absence proof. A promoted same TH is reset DF true, closed, and proved original-identity absent or foreign-replaced. The current root is removed only after its absence proof. Only after every matching rollback proof may core atomically publish null R3 `failed/publication_failed`, close DPs, and unlock. Uncertainty preserves the `running` intent/root and returns deferred `interrupted`; it never terminalizes early. An ambiguous status write is reread under lease, and rollback proceeds only if state is still `running`.

A crash between clearing DF and rename may leave only complete, intent-bound, privacy/cap-valid canonical report/Markdown. It is quarantine, never raw O, provider, prompt, packet, stdout/stderr, or rejected bytes. Recovery holds both DPs and reads only descriptor, source, complete intent, and exact final destinations; it never reads, traverses, deletes, or reuses O or quarantine and never increments worker/adapter counters.

Missing means absent; present opens RH. Acquisition, type, identity, length, or cap uncertainty changes nothing, preserves `running`, and returns deferred `interrupted`. C memory-reads capped files, discards mismatch bytes/hash/path/OS detail, and rechecks held identity/length plus canonical JSON/bindings/report digest or rerendered exact Markdown/render digest. Two valid files use held publication and status reread. Otherwise matching handles are set DF true, closed, and proved original-identity absent or foreign-replaced; foreign or mismatched files remain untouched and unexposed.

Null-R3 `failed/publication_failed` or collision requires every matching rollback proof; any failed proof preserves intent. Parse-equivalent, reordered, reframed, or digest-only mismatch maps through the core to `report_invalid` with no adapter retry. Publication/recovery never weakens the core's privacy, cap, exact-byte, attempt-binding, status, or report contracts.
