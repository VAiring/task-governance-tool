# Formal Usage phase-page validation

This is a measurement and validation record, not governing authority. The
current contract is [numerical usage storage](../database-specification.md#numerical-usage-store)
and [reconciliation tracking](../database-design.md#usage-reconciliation-tracking).
The November 9 prototype and its original observations are retained unchanged
as local-only reference material, outside the repository's tracked artifacts.
They are not evidence for this implementation.

## Candidate and comparison method

The baseline is commit `1c4058ab4964eaa50f923a58726940017103b3ca`, including the
subsequent Usage continuation implementation. The product change extends the
existing phase-page executor to operations, participants, event locations,
pending sources, uncertainty rules, interval metadata, diagnostics and
execution summaries. It retains the first standalone commit, 128-unit limit,
250 ms optional-start allowance and 50 ms deadline margin. No schema, settings,
Hook budget, single-unit algorithm or response-page boundary changes.

The offline [comparison tool](../../tools/measure_usage_phase_pages.py) loads
the complete baseline runtime file or the candidate runtime file. It first
checks that every other package Python file is byte-identical. It does not
load or layer the experimental `BatchExtra` implementation. Both versions use
the same closed fixture databases, identities and paths, restoring the Usage
file in place between runs. The Core bytes must remain identical. Every result
index must match exactly, then the independent full attribution oracle checks
component contents, numerical totals, unknown and unassigned response sets.

Runtime SHA-256 values:

- Before: `1f0692b74d4b9676f45c0a5bf2661793acf63c19fd1140288da1e9b5b4e2c622`
- After: `4771f148feba2bf4b46fdb059494a1e58195bb1b38c85f32aa626bbafd12f76a`

Environment: Windows, CPython 3.14.3, local C: storage. Each numerical case has
two uninstrumented samples per version in before/after/after/before order,
followed by a separate instrumented comparison. SQL figures below count SQLite
trace callbacks, not Python SQL API calls. Connection counts include both Core
and Usage connections. Schema counts are entries to the Core schema validator.
Observer time is not used to claim time improvement. Page count can vary with
machine load because the time allowance is cooperative.

## Observations

| Synthetic input | Before seconds (2 runs) | After seconds (2 runs) | Connections before → after | Core schema entries before → after |
|---|---|---|---|---|
| 16 pause/resume cycles, 32 appended turns | 10.620, 11.278 | 3.046, 3.188 | 372 → 88 | 186 → 44 |
| 1 Task, 128 appended turns | 11.155, 10.134 | 4.062, 3.566 | 356 → 100 | 178 → 50 |
| 13 Tasks, 5 sources | 9.914, 10.881 | 4.258, 4.429 | 368 → 138 | 184 → 69 |
| Same 128-turn history, unchanged | 0.249, 0.147 | 0.229, 0.125 | 8 → 8 | 4 → 4 |
| Same history, one appended response | 1.001, 0.901 | 0.882, 0.813 | 32 → 32 | 16 → 16 |

| Synthetic input | Usage SQL trace callbacks before → after | Core SQL trace callbacks before → after |
|---|---|---|
| Pause/resume | 22,386 → 19,098 | 23,090 → 5,482 |
| 128 appended turns | 28,344 → 26,423 | 22,098 → 6,226 |
| 13 Tasks / 5 sources | 19,254 → 16,476 | 22,854 → 8,594 |
| Unchanged | 157 → 157 | 500 → 500 |
| One appended response | 1,269 → 1,269 | 1,988 → 1,988 |

All five indices and oracle comparisons passed. The three backlog cases use
62.5–76.3% fewer connections; their two-sample mean times are about 58–72%
lower. Unchanged and one-append cases add no connections, schema admissions or
SQL trace callbacks. Their small timing differences are not a general speedup
claim. Raw bounded results are in [comparison.jsonl](usage-phase-pages-2026-11-12/comparison.jsonl).

## Hook and regression coverage

An isolated physical package is initialized through public Setup and Task
operations. Synthetic SessionStart payloads enter `usage_lifecycle.main`;
production continuation intake and publication run normally. Initial,
unchanged and one-response-append conditions all publish `current`. The
canonical index and all snapshot files are byte-identical between runtimes;
Core and sealed completion Evidence are unchanged. Operational continuation
state is also equal except its deliberately fresh request identifier, whose
closed format is independently validated.

The [Hook observations](usage-phase-pages-2026-11-12/hook.jsonl) are equality
checks, not performance qualification: they are single samples and were run
alongside regression tests. No host trust or live Hook setting was modified.

The formal [phase-page regression](../../tests/test_usage_reconciliation_phase_pages.py)
covers all eight phases with real Task/source/transition inputs, a standalone
first commit, failure after actual mutations in a later page, exact derived-row
and queue rollback, repository reopen, a new first standalone commit and full
replay equality. It also covers empty queues, FIFO boundaries at 0/1/127/128/129/258
units, time/deadline stops, upstream requeue, a concurrent Core writer between
pages and rejection by the independent final publication guard. Obsolete-key
fixtures exercise page sizes; populated real inputs exercise semantic effects.
Existing reconciliation suites retain pause/resume, duplicate/unknown response,
restored epoch, reviewer participation, snapshot lineage and response-page tests.

Initial harness corrections are retained in the local validation logs: the new
obsolete event fixture initially omitted required task/order columns; the Hook
observer initially watched the pre-continuation caller rather than the actual
shared evidence service; comparing all Usage-directory JSON also included the
new random operational request ID. Diagnosis isolated that sole difference to
`runtime/continuation.json.request`. These corrections do not relax published
JSON equality or change product behavior. The original prototype was untouched.

## Similar-path review and limits

Both entry functions were inspected together with operation/participant
selectors, uncertainty/pending rules, interval metadata, contribution replacement
and execution summaries. Those units enqueue downstream work under the same
writer. Each following page reopens Core admission and revisits higher-priority
queues; no phase-level validated state survives its transaction. The existing
turn and response pages retain their own unit semantics and limits. Baseline,
source ingestion, interval reconstruction, ranges, snapshot construction and
immutable-history auditing are deliberately unchanged.

A single indivisible unit and its commit can exceed 250 ms. This change does
not split that unit or promise a maximum Core writer delay. Measurements do not
establish Linux/macOS performance, live backlog completion or an improvement
for every host input. Actual-host observations must identify the loaded runtime
separately; old logs or synthetic results cannot certify the updated resident
service. No live state migration, configuration change or environment setup is
part of this validation.

At the local deployment observation (2026-11-11 19:52 UTC), the MCP service
responded but reported loaded/deployed code `different`, with both schema
ceilings still 37. Its loaded code ID was
`sha256:7464925684624f9342a04c8544f1ad9cedf8f155d691a31a3ed48facf53f8f88`.
The latest existing common-debug-log write was 2026-11-11 19:43 UTC, before
this runtime was applied. Those logs cannot establish the new implementation's
live performance. Reloading the same service is required before using the new
resident implementation; no new Setup or configuration change is required.

## Reproduction

Prepare a source-only checkout of the baseline above and this candidate, with
Python 3.12 or later and existing repository prerequisites. From the candidate:

```powershell
python -B tools/measure_usage_phase_pages.py --baseline-root <baseline-copy> --output <unused-results.jsonl> --temp-root <isolated-temp>
python -B tools/document_contract.py --repo .
python -B tools/test_lanes.py --check
```

For tests, add `task-governance-tool/scripts` and the repository root to the
Python import path, use an isolated temporary directory and the repository's
documented fixture caller, then run `tests.test_usage_reconciliation*` and the
installed lifecycle tests. `--hook-only` runs just the three Hook comparisons.
The tool refuses to overwrite its results. Its fixture cleanup and restoration
operate only inside its explicitly selected temporary area, never live state.
