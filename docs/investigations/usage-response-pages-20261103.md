# Response classification page experiment

This is a non-authoritative investigation artifact for
`tg_task_69e7b43a9819b4d9`. It does not change the shipped response policy.
Live Task status, review and completion evidence remain in the public CLI.
Product authority remains [numerical usage storage](../database-specification.md#numerical-usage-store)
and [reconciliation tracking](../database-design.md#usage-reconciliation-tracking).

## Question and isolated prototype

Compare response pages of **8 keys / 50 ms** with **128 keys / 250 ms**.
Both retain the first key's standalone commit and the **50 ms deadline margin**.
The time allowance stops optional *starts*; one indivisible key, admission or
commit can exceed it. It is not a transaction-duration guarantee.

The fixed product baseline is `b3b8e873e3fd995c43548f650dd4dec3962c3f28`.
It includes the earlier four-stage batching (`7a58b5c`) and component reuse
(`6e25096`); their prior measurements are not evidence for this experiment.
The runtime file SHA-256 is
`6369552595e6b922f269a4611765b38fe58cef2df6366340812319ee6818c9f3`.

The [benchmark](../../tools/benchmark_usage_response_pages.py) changes policy
only in its disposable Python process. It recompiles the existing effect
function with a single guarded expression replacement: the deadline margin
stays 50 ms when the optional-start allowance becomes 250 ms. Both policies
use this same prototype mechanism, existing single-key classifier, FIFO queue,
connection-scoped component reuse, fresh page admission, priority work checks
and final publication guard. No product source, schema, live DB, logger or host
configuration is modified. The original runtime file remains byte-identical.

## Reproduction and measurement boundaries

Use this investigation's checkout with the product baseline above, Windows
Python 3.14.3, and an existing ignored temporary directory. No dependencies,
network, real provider transcript or live project state are required.

```powershell
$env:TEMP = Join-Path (Get-Location) '.tmp-response-fixtures'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$env:PYTHONPATH = 'task-governance-tool/scripts;.'
python -B tools/benchmark_usage_response_pages.py --output .tmp-response-results.json
python -B -m unittest tests.test_usage_response_page_experiment -v
python -B tools/document_contract.py --json
python -B tools/test_lanes.py --check
```

The benchmark also accepts `--phase responses`, `contention` or `hook` for
bounded repetition. It records product and benchmark hashes and refuses an
in-flight benchmark edit. Do not run timing concurrently with another test.
Generated DBs, logs and detailed measurement JSON stay uncommitted.

- Three alternating-order timing repetitions and one separate SQL-count run
  per policy/input. Both use the same disabled fixture diagnostic logging.
  Timing probes are identical; their overhead is not subtracted.
- Response-only samples restore the same numerical DB bytes and drain an
  already-selected queue of 9, 129 or 513 keys for one active execution. Time
  includes the terminal no-response effect call, not fixture preparation or
  the subsequent oracle. It measures reclassification, not initial ingest.
- Whole-Hook samples restore the same test-created state and synthetic input
  at the same paths. A fresh process runs the actual lifecycle entry function,
  ingestion, classification, audit and JSON publication. Interpreter startup
  and imports are outside the reported entry-function time. The existing Hook
  deadline is unchanged; further events run only until publication is current.
- Whole-Hook input is one Task and one turn with 9, 129 or 513 numerical
  responses. This isolates response batching from the already implemented
  multi-turn improvements. Semantic safety cases use more varied inputs.
- SQL tracing is deliberately separate from timing. Real-clock tracing can
  change page splits, so observed statement totals are not a pure per-key
  complexity ratio. Actual page sizes accompany every count.
- `writer_hold_ms` starts at numerical validation, immediately after
  `BEGIN IMMEDIATE`, and ends after commit/connection close. It includes
  validation, Core admission and classification; it excludes the BEGIN call
  itself and any lock-acquisition wait. `writer_context_ms` includes those
  earlier costs. `writer_body_ms` excludes numerical admission and commit.
- Contention starts a real Core update inside the first key and waits until
  that writer reaches COMMIT. This synchronization consumes part of the page
  allowance. Reported COMMIT wait is a measured blocking interval, while total
  update time also includes the writer's admission/work. Both are synthetic,
  not application-wide latency bounds. No artificial sleeps or clock changes
  are used in performance measurements.

Every response/Hook sample also runs the retained independent full classifier
outside timing, compares numerical totals and diagnostics, and verifies Core
bytes are unchanged. Whole-Hook samples additionally compare all published
JSON hashes across policies at the same physical fixture.

## Results and decision

The fixed-code run completed all 54 samples (including count and contention
runs). Its benchmark SHA-256 is
`886b1a627fc41ae5d348d85604b1cbb1410ef37c505f695d8dfa46af6a7526d5`.
Times below are milliseconds, in repetition order; SQL-instrumented timings
are excluded. Every sample's independent oracle and Core-byte comparison
passed, and every Hook sample reached current publication in one event.

| Input / measurement | 8 / 50 ms: three times | 128 / 250 ms: three times |
|---|---|---|
| 9 / response-only | 202.4, 228.9, 149.1 | 210.9, 215.4, 213.5 |
| 129 / response-only | 1032.9, 1395.9, 1366.2 | 254.5, 281.5, 190.7 |
| 513 / response-only | 2735.2, 3494.6, 2870.0 | 297.1, 277.5, 369.2 |
| 9 / whole Hook | 1833.9, 1886.0, 1866.9 | 1828.3, 1813.5, 1903.4 |
| 129 / whole Hook | 2603.3, 2737.1, 2665.7 | 2784.4, 2084.1, 2106.6 |
| 513 / whole Hook | 5566.8, 5257.2, 6567.3 | 3055.0, 3022.2, 4158.0 |

At 513 keys the median response-only time falls from 2870.0 to 297.1 ms;
whole-Hook median falls from 5566.8 to 3055.0 ms. Every candidate repetition
is faster than every baseline repetition at that size. At 129 keys the
response-only benefit is consistent but one whole-Hook repetition is slower.
At 9 keys the page count is unchanged and no speedup is established.

The separate count run below gives **response-phase** counts, excluding the
terminal no-response call. Core checks count actual schema-admission calls.
The counted pages happened to fill here; timed baseline pages sometimes ended
earlier (129 keys: 17/21/19 pages; 513: 66/71/65). The candidate used 2 and 5
pages respectively in all response-only timing repetitions.

| Keys | Response SQL, baseline → candidate | Connections | Core checks / commits | Counted page sizes |
|---|---|---|---|---|
| 9 | 478 → 478 | 4 → 4 | 2 → 2 | both 1 + 8 |
| 129 | 5008 → 2638 | 34 → 4 | 17 → 2 | 1 + 16×8 → 1 + 128 |
| 513 | 19504 → 10024 | 130 → 10 | 65 → 5 | 1 + 64×8 → 1 + 4×128 |

The corresponding **whole-Hook** SQL / connections / Core checks / commits:

| Keys | Baseline | Candidate |
|---|---|---|
| 9 | 7838 / 103 / 42 / 48 | 7838 / 103 / 42 / 48 |
| 129 | 22173 / 141 / 61 / 67 | 19803 / 111 / 46 / 52 |
| 513 | 67964 / 263 / 121 / 128 | 58484 / 143 / 61 / 68 |

Maximum observed writer holds across three uninstrumented repetitions:

| Keys | Response-only baseline → candidate (ms) | Whole-Hook baseline → candidate (ms) |
|---|---|---|
| 9 | 128.3 → 103.3 | 36.9 → 35.5 |
| 129 | 175.3 → 113.3 | 45.2 → 155.1 |
| 513 | 97.8 → 87.6 | 71.3 → 72.9 |

In the three synchronized contention runs, baseline pages processed one key
and candidate pages processed 128. Core COMMIT waits were respectively
**34.1 / 25.9 / 30.3 ms** and **76.0 / 79.9 / 53.5 ms**. Total Core update
times were **119.9 / 164.4 / 122.4 ms** and **147.6 / 149.4 / 129.0 ms**.
Numerical writer holds were **119.8 / 192.2 / 145.5 ms** and
**127.9 / 124.3 / 125.3 ms**. All updates completed between pages; every next
page rejected the changed Core basis and subsequently matched full replay.

**Investigation disposition: adopt 128 keys / 250 ms optional-start allowance,
with the existing 50 ms commit margin and first-key standalone commit.**
The large-input whole-Hook reduction is reproducible, not just a SQL-count
claim. Longer contiguous Core blocking is a real cost: this experiment accepts
the additional tens of milliseconds measured here in exchange for substantially
less repeated admission/commit work. There is no new absolute latency promise.
The finite page, fresh checks, first-key progress and atomic retry boundary
remain; a failed large page can require retrying more work than an eight-key
page, but neither its keys nor prior commits are lost. The implementation is
two separate policy values plus an explicit deadline margin, not a new cache,
scheduler or recovery mechanism.

This conclusion does not itself mark either Task done. After the investigation
passes its review/completion gate, record this adopted scope and release the
existing conditional implementation Task `tg_task_0d0228eca5938c76`. That Task
must verify the actual product change and its two-review gate. The present
change contains only investigation and test materials; the shipped policy
remains 8 / 50 ms.

## Safety and interpretation

The [focused tests](../../tests/test_usage_response_page_experiment.py) exercise
0, 1, 8, 9, 127, 128, 129 and 257 queued keys, a single first-key commit,
FIFO resume, deadline margin, optional-start exhaustion, exception rollback,
real child-process termination, and later-Hook recovery to identical JSON.
They cover shared and exclusive partitions, partial/missing-order cases,
late/replaced inputs, removed diagnostics, source/Core/numerical changes,
historical successor preservation and rejection of stale final publication.
The frozen full classifier provides an independent semantic expectation.
Clock injection is restricted to deterministic boundary tests, never timing.

The initial shared-response test used an incomplete Core fixture rejected by
current admission. A paused-Task replacement fixture did not create shared
ownership. The final test uses a physical install and real public Task
completion acknowledgements; it positively requires a shared partition.
Neither failure changed the product or relaxed the shared-ownership assertion.

This is Windows/Python 3.14.3 evidence with logging disabled and synthetic
inputs, not Linux/macOS/Python 3.12 or production latency evidence. It does not
prove fairness under indefinite writer contention, an absolute 250 ms bound,
or a speedup for small input. The independent old classifier is retained test
material, not a production fallback or source of new attribution rules.

Any later implementation must keep the tested atomicity and admission boundary,
make the deadline margin explicit rather than copying the prototype, replace
prototype-based regression execution with actual product execution, and
remeasure the product implementation under the conditional Task's own gates.
