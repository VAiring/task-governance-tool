# Static Task Viewer Specification

This document owns the current Viewer product behavior delegated by the
[product specification](specification.md#static-task-viewer). Implementation
structure belongs in the [Viewer design](viewer-design.md).
The shared [setup](specification.md#setup-contract),
[maintenance](specification.md#same-process-maintenance),
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

Snapshot v4 accepts source schemas v5-v22. One query-only transaction validates
schema/project/binding, reads generation, validates the complete source-aware
Task batch through the stored-row and Contract-relationship boundary, and
assembles rows; rendering and
publication occur after close. A stored Task fault produces no snapshot or
replacement and therefore preserves the last-good Viewer.

It contains version/UTC `generated_at`, project ID/display, source schema,
seven status counts, explicit Task allow-list, newest at most 10 sanitized
events/review receipts/findings, and the exact completion-history projection. Sources
v5-v14 synthesize zero cycles with `legacy_history_incomplete=true`; v15-v22
read stored history in query batches of at most 500 Task IDs. For sources
v17-v22, the batch reader validates version-1 completion-cycle Verification
Receipt links; v18+ additionally validates subject, provenance, manifest, and
Reference relations, while v19-v22 validate and discard the Bundle
discriminator. Sources v20-v22 additionally validate the Bundle-v2
verification basis and Runner graph appropriate to the source schema; v21 and v22
validate the complete tagged union. These reads discard every Runner field without
exposing it.
It discards every joined ledger field. The Viewer
selects all project Tasks; 500 Tasks is the accepted performance fixture, not a
selection cap. The HTML artifact is at most 64 MiB.

It excludes paths, maintenance, checkpoints, Task Contract prose, handoff/tool
state, environment, raw evidence, logs, prompts, and secrets. Deterministic
UTF-8 JSON is base64-embedded into a bundled template; stored values use
text-only DOM APIs.

Schema v13+ increments source generation on Viewer-relevant Task, checkpoint,
or review events, not handoff-only/read/failure/replay/no-op/config/
maintenance. The Viewer stage skips before opt-in, takes zero-wait lock,
renders only when due, records rendered generation shortly, rechecks once, and
never publishes an older capture over newer. Doctor reports stored facts only;
browser reload never observes SQLite directly.
The owning state is `viewer_maintenance_state`; its source generation is
advanced in the same business transaction as the corresponding event.

The offline `file://` UI shows project/time/status totals, search and
status/kind/lane/priority/tag filters, deterministic Task order, terminal
history, details, review/completion/history, and recent events. It is responsive
and keyboard/label/focus/contrast accessible and has no write controls,
network/API/analytics/telemetry/server/direct SQLite/watcher/launch/storage.

## Optional Visibility-Aware Reload

Only physical `<skill>/config/viewer.json` controls reload. Taskgov never
creates/edits/migrates it. Absence means decimal interval 0 and no browser
timer. A present file is physical regular non-link/non-reparse strict UTF-8
JSON at most 16,384 bytes with exactly:

```json
{"schema_version":1,"profile":"visibility-refresh-v1","refresh_interval_seconds":30}
```

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
owned. Hidden pages own no timer. On visible change/timeout, reload is requested
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

The schema-1 object has exactly owner, schema_version, captured_at_ms, status,
kind, lane, priority, tag, terminal, selected_task_id, scroll_x, scroll_y, and
focus_id. Canonical serialization and readback are at most 4,096 UTF-8 bytes.
Time is nonnegative safe integer; status/kind/priority use current enums or
empty; lane/tag are at most 1,024 UTF-8 bytes and must exist in new options;
terminal is Boolean; selected ID is nonempty and at most 128 code points;
scroll is finite 0-2,147,483,647. Focus is empty or one of
`search-filter`, `status-filter`, `kind-filter`, `lane-filter`,
`priority-filter`, `tag-filter`, `terminal-filter`, `reset-filters`.

Search text, business/snapshot content, option arrays, URL/query/fragment,
path, arbitrary selector, caret/text selection, dynamic-row focus, or nested
scroll is prohibited. Cookies, Web Storage, IndexedDB, Cache API, service
workers, and network are prohibited.

At eligible load, read state at most once before snapshot decode. Owned state
is immediately cleared with `replaceState(null, "")` even when invalid,
stale, non-reload, or later decode fails. Clear failure disables restore.
Non-file state is untouched. `history.scrollRestoration` must exist, accept
`manual`, and read back manual before save/restore; otherwise M15.6 is disabled
but reload continues. State-read failure does not skip the manual-mode attempt.

Restore only on navigation type reload, after successful clear, with exact
keys/types/bounds, age 0-300,000 ms, current options, visible selected Task,
and existing fixed focus. Defaults are explicitly applied first. Valid restore
applies filters, one render/selection, focus without caret, then document
scroll. Any failure consumes state and resets defaults plus `(0,0)`. If scroll
fails after focus, best-effort blur only that focused fixed control, then reset.
No state/error detail reaches UI, console, snapshot, or taskgov output.

History state is browser-managed and may survive session restore; it is not
memory-only. One-shot ownership, five-minute age, size cap, and clear-before-
restore are the privacy boundary. `pushState`, URL arguments, URL/history-length
changes, cross-tab sync, and manual-reload capture are prohibited. Interrupted
navigation may leave one bounded envelope for the next qualifying reload.
