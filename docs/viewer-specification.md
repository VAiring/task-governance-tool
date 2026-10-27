# Static Task Viewer Specification

This document owns the current Viewer product behavior delegated by the
[product specification](specification.md#static-task-viewer). Implementation
structure belongs in the [Viewer design](viewer-design.md).
The shared [setup](setup-state-specification.md#setup-contract),
[maintenance](setup-state-specification.md#same-process-maintenance),
[stored-Task read](task-operation-specification.md#stored-task-read-and-privacy-contract), and
[completion-history](review-completion-specification.md#completion-cycle-history) contracts remain
at their existing owners.

The Viewer is a generated self-contained projection, never an authority.
Setup owns initial publication/repair and post-commit maintenance owns bounded
refresh. There is no Viewer command, custom output, browser launch, or model
decision. Canonical path protection rejects linked/non-regular/escaping/
database-alias targets. Same-directory temporary publication is flushed and
atomically replaces; failure preserves last good.

## Snapshot v4

The public v4 shape below is shared by the current
[incremental projection](#conditional-incremental-projection) and the retained
full-capture reader. Complete-source admission described below belongs to that
full reader for explicit inspection and compatible older sources; routine
schema-34 maintenance uses the selected-Task boundary below.

Snapshot v4 accepts source schemas v5-v34. One query-only transaction validates
schema/project/binding, reads generation, validates the complete source-aware
Task batch through the stored-row and Contract-relationship boundary, and
assembles rows; rendering and
publication occur after close. A stored Task fault produces no snapshot or
replacement and therefore preserves the last-good Viewer.
Current-source capture composes common admission with Viewer validation in
that same transaction. Global ownership, retained notes and review-session
bindings are validated once per capture;
collected-answer and judgment admission remain required. This neither narrows
the all-Task inventory nor transfers validation to a later publication guard.
Older source schemas retain independent compatible admission.

It contains version/UTC `generated_at`, project ID/display, source schema,
seven status counts, explicit Task allow-list, newest at most 10 sanitized
events/review receipts/findings, and the exact completion-history projection. Sources
v5-v14 synthesize zero cycles with `legacy_history_incomplete=true`; v15-v34
read stored history in query batches of at most 500 Task IDs. For sources
v17-v34, the batch reader validates version-1 completion-cycle Verification
Receipt links; v18+ additionally validates subject, provenance, manifest, and
Reference relations, while v19-v34 validate and discard the Bundle
discriminator. Sources v20-v34 additionally validate the Bundle
verification basis and Runner graph appropriate to the source schema; v21-v34
validate the complete tagged union. These reads discard every Runner field without
exposing it. Schema-23 declaration reasons are validated but not added to the
Viewer projection or UI.
Source 29 also validates the sealed continuation graph when Bundle format three
applies, and selects the same effective review heads as the native gates. Old
judgments and Findings remain in durable/audit evidence; pagination never
qualifies an incomplete graph or substitutes historical PASS as current PASS.
Sources 30, 31, 32, 33 and 34 additionally validate [collected v2 provenance, Bundle 4/index 3
and graph 2](evidence-specification.md#collected-answer-evidence), including
typed source/capture relations and the shared effective unable blocker.
Preserved old Bundle formats retain their exact validators. No original body,
private transport path, collection control or new Evidence panel enters snapshot
v4 or its UI.
It discards every joined ledger field. It validates schema-28 full Task notes
and their parent bindings but excludes note
bodies from its snapshot and HTML; emitted event summaries receive the same
stored type, capacity and privacy validation as other Task projections, with
their accepted text unchanged.
The Viewer selects all project Tasks; 500 Tasks is the accepted performance fixture, not a
selection cap. The HTML artifact is at most 64 MiB.

It excludes paths, maintenance, checkpoints, Task Contract prose, handoff/tool
state, environment, raw evidence, logs, prompts, and secrets. Deterministic
UTF-8 JSON is base64-embedded into a bundled template; stored values use
text-only DOM APIs.

Schema v13+ increments source generation on Viewer-relevant Task, checkpoint,
or review events, not handoff-only/read/failure/replay/no-op/config/
maintenance. The Viewer stage skips before opt-in, takes zero-wait lock,
and repeats its metadata eligibility check before the selected source capture.
Disabled/current decisions use structural and maintenance metadata admission
without auditing all Task histories. An eligible incremental capture validates
its changed Tasks and consumed relations in that same snapshot. The stage
renders only when due, records rendered generation shortly, rechecks once, and
never publishes an older capture over newer. Doctor reports stored facts only;
browser reload never observes SQLite directly.
The owning state is `viewer_maintenance_state`; its source generation is
advanced in the same business transaction as the corresponding event.
Schema 34 also records the affected Task for event-free Verification Receipt
and continuation changes in their owned transaction, according to the
[change inventory](viewer-design.md#incremental-change-inventory). This tracking
foundation supplies the revision inventory for incremental publication.

The offline `file://` UI shows project/time/status totals, search and
status/kind/lane/priority/tag filters, deterministic Task order, terminal
history, details, review/completion/history, and recent events. It is responsive
and keyboard/label/focus/contrast accessible and has no write controls,
network/API/analytics/telemetry/server/direct SQLite/watcher/launch/storage.

<a id="conditional-incremental-projection"></a>

## Incremental Projection

This current implementation concerns Viewer maintenance only. Its
[execution boundary](viewer-design.md#incremental-execution-boundaries)
retains the accepted scope and gates. No new CLI, service, timer, browser communication, configuration
permission or completion evidence is introduced.

For a source with the implemented change-tracking schema, the first publication
and a required rebuild construct every Task's sanitized display projection.
Ordinary due updates read and semantically validate only changed Tasks and the
related records needed by those Tasks. Unchanged Task projections are reused;
they are not rejoined to their historical Receipts, Bundles, originals or other
evidence. All Task display bytes may be checked and the whole HTML is still
rendered. These costs are distinct from a database-wide semantic audit.

Each selected Task retains stored-value/privacy, Contract-parent, review-target,
Receipt/Finding/provenance, continuation-head and completion-history checks for
the data consumed. Foreign or ambiguous relationships fail closed. Selection
never admits another Task's evidence merely because it is linked. The current
display allow-lists, recent-ten ordering, status counts, Task ordering and
completion-history meaning remain unchanged; no cache internals enter snapshot
v4 or HTML. The cache is never a proof for Task, review, completion or Evidence
operations. Database-wide diagnosis, migration and recovery retain their own
complete validation.

The [change inventory](viewer-design.md#incremental-change-inventory) is the
required notification boundary. Changes are recorded in the business
transaction, including changes that do not update `tasks.updated_at`.
Rollback leaves neither the change nor its notification. Repeated changes may
coalesce per Task without consuming a later change. Project-wide invalidation
is explicit; an unknown affected set is never interpreted as no change.
Unmodified source records changed outside supported writers are not guaranteed
to be detected by the next routine refresh. This is a display-cache guarantee,
not a reduction in authoritative operation gates or explicit diagnosis.

The one fixed cache is `.taskgov/current/viewer/task-projection.json`, derived
through the [shared fixed resolver](setup-state-design.md#fixed-state-resolver)
beside its canonical HTML, not reconstructed from the project path by feature
code. It is a replaceable generated artifact excluded from source/release
material. Only already
authorized Viewer maintenance/Setup may publish it under the existing physical
path and zero-wait lock rules. It stores the sanitized Task display projection
and bounded machine metadata, not original evidence, Contract prose, paths,
notes, ownership sessions or logs. Cache format/projection version, project,
binding, database incarnation and physical identity, application generation,
structure, byte capacity and SHA-256 must match the independently saved
metadata before reuse. Size or mtime alone is never integrity evidence.
The [format and publication protocol](viewer-design.md#incremental-cache-and-publication)
defines exact comparisons and the distinction between applied and published
generations. The cache and HTML each have a 64 MiB byte cap; exceeding either
fails publication without truncating Tasks or silently dropping fields.

Missing, corrupt, mismatched, incompatible or invalidated cache requires a full
display rebuild from a fresh admitted source. Unsupported or invalid source
state still fails; a cache is never a fallback around it. Formal restoration
rotates the database incarnation before publication, even when the restored
project and numeric generation equal an earlier database. Rebind, migration,
projection-version change and supported replacement invalidate reuse. Arbitrary
unsupported in-place database edits need not be detected immediately.

Disabled/current checks remain metadata-only, with bounded fixed-path presence
checks; they do not open historical evidence or hash all display bytes. Missing
artifacts are due. Cache integrity is checked when reuse is attempted or by
explicit Setup repair, not on every unchanged read. Each attempt retains the
zero-wait lock and at most two captures/renders. Stale captures cannot overwrite
a newer publication or clear later changes. Pre-replacement failure preserves
the last good HTML; a crash after a complete atomic replacement can leave the
new valid HTML with older metadata and must be reconciled conservatively on the
next opportunity. Cache acceptance atomically clears the HTML publication tuple
and its rendered-generation counter, including when the numeric source
generation is unchanged. Only a successful matching HTML publication restores
that tuple. Explicit force repair first invalidates both derived tuples under
the Viewer lock so interruption remains due. These metadata invalidations never
remove the last-good HTML file. Failure/outcome recording does not make a generation current.
Primary-command success and the existing fixed warnings remain independent.

Supported sources v5-v33 retain their full-capture compatibility path. They
cannot consume the new cache or receive tracking writes; ordinary commands do
not migrate them. Explicit Setup owns migration, restoration and rebind.
The full-capture reader remains available for those compatibility and explicit
inspection responsibilities; routine schema-34 publication uses this contract.

## Optional Visibility-Aware Reload

Only physical `<skill>/config/viewer.json` controls reload. Explicit
[Setup selection](setup-state-specification.md#optional-feature-selection)
may create/toggle it; ordinary commands never edit it. Absence means decimal interval 0 and no browser
timer. A present file is physical regular non-link/non-reparse strict UTF-8
JSON at most 16,384 bytes with exactly:

```json
{"schema_version":1,"profile":"visibility-refresh-v1","refresh_interval_seconds":30}
```

An optional Boolean `enabled` defaults to true for legacy files. False resolves
to interval zero while retaining the configured interval for later re-enabling.
Interval is an integer (not Boolean/float) 5-3,600. Duplicate/unknown/missing
keys, malformed/oversized/replaced/unsafe content is invalid with no raw
diagnostic. One publication attempt reads it once for at most two renders.
Template has exactly one base64 snapshot and one decimal interval placeholder.
Config change applies at next relevant publication; setup plans Viewer repair
when rendered template/interval differs.

Invalid present config makes preview a successful repair plan; actual setup
returns `setup_incomplete` and routine mutation emits
`viewer_refresh_failed`, preserving last-good Viewer. Backup still attempts
second. Doctor ignores this optional file.

After decode and initial render succeed, scheduling occurs only under `file:`
with positive interval. One monotonic load epoch and at most one timeout are
owned. Hidden pages, a focused search field, and active search IME composition
own no timer and do not reload, even after the deadline or while the user is
thinking without typing. Visibility, search focus/blur, and composition start/end
reconcile that same timer. Once visible, unfocused, and no longer composing,
an overdue reload happens once; before the deadline only the original remainder
is scheduled. On visible change/timeout, reload is requested
at most once per loaded page only after elapsed interval; otherwise only the
remainder is scheduled. Browser throttling may delay, never advance. Decode/
render failure schedules nothing. It reloads only latest published HTML and
adds no command, schema/snapshot field, process, service, storage, network, or
LLM choice.

## One-Shot Automatic-Reload UI State

Immediately before that one automatic reload, an eligible `file:` page may call
exactly one `history.replaceState(envelope, "")` with no URL. Failure never
prevents reload. Non-null non-owned state is untouched; any non-array object
with owner `taskgov-viewer-auto-reload` is owned even if otherwise invalid.

The schema-2 object has exactly owner, schema_version, captured_at_ms, search,
status, kind, lane, priority, tag, terminal, selected_task_id, scroll_x, scroll_y,
and focus_id. Canonical serialization and readback are at most 4,096 UTF-8 bytes.
Time is nonnegative safe integer; search is the exact input string (including
empty), bounded by the whole-envelope byte cap without truncation;
status/kind/priority use current enums or empty; lane/tag are at most 1,024
UTF-8 bytes; terminal is Boolean; selected ID is null or nonempty and at most
128 code points; scroll is finite 0-2,147,483,647. Focus is empty or one of
`search-filter`, `status-filter`, `kind-filter`, `lane-filter`,
`priority-filter`, `tag-filter`, `terminal-filter`, `reset-filters`.

Apart from the explicit search input, business/snapshot content, option arrays,
URL/query/fragment, path, arbitrary selector, caret/text selection, dynamic-row
focus, or nested scroll is prohibited. Cookies, Web Storage, IndexedDB, Cache API, service
workers, and network are prohibited.

At eligible load, read state at most once before snapshot decode. Owned state
is immediately cleared with `replaceState(null, "")` even when invalid,
stale, non-reload, or later decode fails. Clear failure disables restore.
Non-file state is untouched. `history.scrollRestoration` must exist, accept
`manual`, and read back manual before save/restore; otherwise UI-state capture
and restore are disabled but reload continues. State-read failure does not skip
the manual-mode attempt.

Restore only on navigation type reload, after successful clear, with exact
keys/types/bounds, age 0-300,000 ms, and existing fixed focus. Old schema-1
envelopes are consumed without restoration. Defaults are explicitly applied first.
Valid restore applies the exact search string and filters; a vanished lane/tag
option alone falls back to its empty filter. No selection, no matches, or a
selected Task that disappeared or no longer matches does not discard search
or other valid filters. Selection is restored only if still visible under all
filters, including search; otherwise normal rendering selects the first visible
Task or none. Valid restore uses one render/selection, focus without caret, then
document scroll. Any failure consumes state and resets defaults plus `(0,0)`. If scroll
fails after focus, best-effort blur only that focused fixed control, then reset.
No state/error diagnostic reaches UI, console, snapshot, or taskgov output.
Oversize input/envelope or unavailable/failed History storage skips capture,
without truncating the search or blocking reload; the next page uses defaults.

History state is browser-managed and may survive session restore; it is not
memory-only. One-shot ownership, five-minute age, size cap, and clear-before-
restore are the privacy boundary. `pushState`, URL arguments, URL/history-length
changes, cross-tab sync, and manual-reload capture are prohibited. Interrupted
navigation may leave one bounded envelope for the next qualifying reload.
