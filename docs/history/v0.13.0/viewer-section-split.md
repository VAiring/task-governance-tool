# Viewer Section Split Capture

> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves only the Viewer sections before their document split.
> Words such as current, approved, or implemented describe the captured revision,
> not current authority. This history cannot fill an active-contract gap,
> satisfy a current gate, or authorize a removed behavior.

- Source commit: `5d0c41203ee550728ac4f2a0d1f8fdb055b13a65`
- Source sections: `docs/specification.md#static-task-viewer` and
  `docs/design.md#static-viewer`
- Capture unit: `TG-SPLIT.1`
- Active replacements:
  [Viewer specification](../../viewer-specification.md) and
  [Viewer design](../../viewer-design.md).
  [Repository authority](../../authority.md) routes the unchanged common owners.
  Use the public CLI for live Task state and evidence.

## Captured Product Section

Source path: `docs/specification.md`

````markdown
## Static Task Viewer

The Viewer is a generated self-contained projection, never an authority.
Setup owns initial publication/repair and post-commit maintenance owns bounded
refresh. There is no Viewer command, custom output, browser launch, or model
decision. Canonical path protection rejects linked/non-regular/escaping/
database-alias targets. Same-directory temporary publication is flushed and
atomically replaces; failure preserves last good.

### Snapshot v4

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

### Optional Visibility-Aware Reload

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

### One-Shot Automatic-Reload UI State

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

````

## Captured Design Section

Source path: `docs/design.md`

````markdown
## Static Viewer

### Snapshot And Publication

The Viewer is a replaceable projection, never authority:

```text
committed Viewer-relevant event
  -> source generation
  -> post-commit coordinator
  -> zero-wait Viewer lock
  -> one compatible query-only snapshot
  -> snapshot v4 + captured generation
  -> base64 UTF-8 JSON in bundled template
  -> atomic task-viewer.html replacement
  -> short rendered-generation update
```

Setup invokes the same canonical renderer directly. There is no public Viewer
command, output choice, browser launch, server, or browser-to-SQLite path.

The repository selects complete rows for all project Tasks in list order and
validates them as one source-schema-aware stored-Task/Contract batch before any
Task, review, or history projection. Exact v18-v22 snapshot validation first
validates the complete Task batch as part of the full Evidence Ledger, then the
same-transaction list-order query consumes the private proof above instead of
repeating scalar, privacy, and Contract validation. Sources v5-v17 and the
ordinary Viewer repository entry still validate the selected batch directly.
Validation failure occurs before rendering or replacement and preserves the
last-good HTML. It then selects at most 10 newest events per Task by time/rowid,
review evidence through the shared gate read model, and bounded completion
history. Snapshot v4 contains snapshot/source schema versions, generated time,
project ID/display only, counts, and explicit Task/event/evidence/history allow-
lists. It excludes repository/database paths, tool events, handoffs,
checkpoints, maintenance state, internal generation, event-cycle links,
environment, and raw review material.

Source schemas v5-v14 synthesize empty/incomplete completion history;
v15-v22 use stored cycles, reading completion histories in batches of at most
500 Task IDs. Sources v17-v22 validate version-1 cycle Receipt links; v19-v22
also validate and discard Bundle linkage, and v20-v22 validate the source-
appropriate closed verification basis and Runner graph. Sources v21 and v22 validate
the complete tagged union before discarding every Runner field. Every snapshot
reports its actual source schema and selects all
project Tasks; 500 Tasks is the accepted performance fixture rather than a
selection cap. The rendered artifact is capped at 64 MiB.

Snapshot JSON is deterministic UTF-8 and base64-encoded before insertion.
The template has exactly one snapshot placeholder and one decimal refresh
interval placeholder. Stored data reaches the DOM only through `textContent`
or text nodes, never HTML/eval/URL/style/event sinks.

The canonical output and lock remain below fixed state. Resolution rejects
reparse parents, containment changes, DB aliases, and linked/nonregular
destinations. Rendering writes a unique sibling temporary, flushes/closes it,
and `os.replace`s only after complete success; failure retains last-good HTML.

Schema v13 `viewer_maintenance_state` holds nonnegative source generation,
nullable rendered generation not above source, and fixed
`succeeded|deferred|failed` outcome/time. New/migrated state starts source 0
and rendered null. Under the lock, publication captures generation and rows in
one snapshot, closes SQLite, renders/replaces, then conditionally records the
captured generation without lowering a newer value. One recheck permits one
follow-up; later churn remains due.

### Presentation Profile

The only presentation policy is optional:

```text
<physical-skill>/config/viewer.json
```

Absence is valid and disables reload with interval 0. Taskgov never creates or
edits it. A present regular physical UTF-8 file is capped at 16,384 bytes and
must contain exactly:

```json
{
  "schema_version": 1,
  "profile": "visibility-refresh-v1",
  "refresh_interval_seconds": 30
}
```

The interval is a JSON integer 5-3,600; booleans, floats,
duplicate/unknown/missing keys, malformed/trailing JSON, links/reparse paths,
devices, directories, replacement races, or uninspectable metadata fail
closed with one sanitized error. The loader uses no-follow where available and
checks descriptor/path identity before and after the bounded read. Doctor does
not inspect this file.

One publication attempt loads the value once and reuses it for both possible
renders. Setup preview treats an invalid present profile as Viewer
repair-required but writes nothing; write setup and routine publication retain
last-good output and use existing incomplete/warning semantics. Missing or
changed profile/template bytes make setup repair the canonical page.

### Browser Application And Reload

The page provides a compact header, all-status summary, search/status/kind/
lane/priority/tag/terminal filters, responsive Task table, detail/events, and
empty state. Terminal Tasks are hidden by default but filterable. Native
controls, visible focus, labels, non-color status cues, bounded radii, and
wrapping support keyboard and narrow-screen use.

Reload scheduling activates only after successful fatal-UTF-8 decode and
render, with a valid nonzero interval and `file:` protocol. One reconciliation
function owns one timeout:

1. clear the prior handle;
2. stop if disabled, already requested, non-file, or hidden;
3. compute elapsed from page-load `performance.now()`;
4. request exactly one same-document reload once elapsed reaches interval; or
5. schedule only the monotonic remainder.

Timeout and visibility-change callbacks reuse that function. Hidden pages own
no timer. There is no interval timer, polling, wall-clock interval
calculation, fetch/XHR, storage, worker, database access, retry, or message
channel. Browser throttling may make reload late, never early.

Immediately before that automatic reload only, the page may make a one-shot
History API handoff with exact ordered keys:

```text
owner, schema_version, captured_at_ms, status, kind, lane, priority, tag,
terminal, selected_task_id, scroll_x, scroll_y, focus_id
```

Owner is `taskgov-viewer-auto-reload`, schema is 1, and compact UTF-8 JSON is at
most 4,096 bytes. Only allowed filter values, visible selected Task ID,
nonnegative finite scroll coordinates, and one of eight fixed control IDs may
be saved. Search text, Task/snapshot content, arbitrary selectors, URLs/paths,
dynamic-row focus, selection ranges, and nested UI state are prohibited. No
selection means no envelope and reload still proceeds.

Capture time is a nonnegative safe integer; status, kind, and priority are
empty or current enums; lane and tag are each at most 1,024 UTF-8 bytes;
selected Task ID is nonempty and at most 128 code points; coordinates are
finite integers from 0 through 2,147,483,647; and focus is empty or exactly
`search-filter`, `status-filter`, `kind-filter`, `lane-filter`,
`priority-filter`, `tag-filter`, `terminal-filter`, or `reset-filters`.

Save never overwrites a non-null non-owned state and calls only
`history.replaceState(candidate, "")` without URL. Failure never prevents
reload. On a file load, every owned state is cleared before snapshot decode,
even if malformed, stale, non-reload, or decode-fatal. Restore requires
successful clear, navigation type reload, exact keys/types/bounds/current
options, visible selection, current owner/version, and age 0-300,000 ms.
Invalid or failed restore returns filters, selection, focus, and scroll to
defaults.

On an enabled file page or one starting with owned state, manual
`history.scrollRestoration` must be set and read back before UI restoration.
If unsupported, state save/restore is disabled but owned state is still
best-effort cleared and reload continues. History state is browser-managed and
may survive session restoration; it is bounded and one-shot, not described as
memory-only.

The page never uses `pushState`, URL/query/fragment state, cookies, Web
Storage, IndexedDB, Cache API, service workers, cross-tab messaging, or manual
reload capture. It logs no state, bytes, validation reason, exception, URL, or
path. Saving/restoring must change neither `history.length` nor
`location.href`.

### Browser Security

The exact CSP is:

```text
default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; media-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; manifest-src 'none'; base-uri 'none'; form-action 'none'
```

Inline script/style is limited to the fixed single-file application;
`unsafe-eval` is absent. Stored values cannot create markup, script, style,
event handlers, URLs, or resource loads. The browser has no external URL,
telemetry, automatic launch, network API, or database-write code.

````
