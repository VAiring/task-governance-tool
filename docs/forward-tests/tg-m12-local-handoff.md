# TG-M12.1 Local Handoff Forward Test

Date: 2026-07-26 JST

Historical execution record: commands reflect the tested release on this date
and are superseded by the current v0.8.0 active guidance.

## Scope

Validate from fresh CLI processes and a temporary schema-v7 database that an
out-of-scope discovery can be recorded durably, the source Task can continue
to completion, and the pending handoff remains rediscoverable without an Issue
Skill, an extra user question, or a Task-selection side effect. The test used
only temporary local files and no network access.

## Commands Exercised

The independent tester exercised this public command surface:

```powershell
python scripts/taskgov.py db init --repo <temp-project> --db <temp-db> --json
python scripts/taskgov.py task add --repo <temp-project> --db <temp-db> --title "Finish accepted scope" --status in_progress --review-tier 0 --json
python scripts/taskgov.py handoff record --repo <temp-project> --db <temp-db> <task-id> --summary "Optional hardening belongs outside this task" --json
python scripts/taskgov.py review target set --repo <temp-project> --db <temp-db> <task-id> --kind diff_fingerprint --revision <fingerprint> --json
python scripts/taskgov.py review receipt add --repo <temp-project> --db <temp-db> <task-id> --reviewer <fixture-reviewer> --kind not_required --verdict not_required --summary "Mechanical forward-flow fixture" --json
python scripts/taskgov.py task edit --repo <temp-project> --db <temp-db> <task-id> --status done --verification-complete --review-complete --commit-not-required --json
python scripts/taskgov.py handoff list --repo <temp-project> --db <temp-db> --json
python scripts/taskgov.py handoff show --repo <temp-project> --db <temp-db> <handoff-id> --json
python scripts/taskgov.py db status --repo <temp-project> --db <temp-db> --json
python scripts/taskgov.py task show --repo <temp-project> --db <temp-db> <task-id> --json
python scripts/taskgov.py task next --repo <temp-project> --db <temp-db> --json
```

Each rediscovery command ran as a separate CLI process after source-task
completion. The temporary database was also inspected with SQLite integrity
pragmas.

## Result

PASS. No High or Medium usability or integrity issue was found.

- The complete flow was repeated after the pre-acceptance corruption,
  transactional-snapshot, and migration-history corrections.
- Explicit initialization applied migrations 1 through 7 and reported schema
  version 7.
- `handoff record` returned `durable=true`, `created=true`, and one
  `pending_handoff`.
- The source Task completed as `done` with typed
  `commit_not_required` evidence; the pending handoff did not add a completion
  gate.
- A later process rediscovered exactly one row through `handoff list` with
  `count=1` and `total_matching=1`.
- `db status.counts.handoff_pending` and
  `task show.handoff_summary.pending_handoff` both returned 1.
- `handoff show` returned the same public row without a claim token.
- All command warnings were empty. `task next` gained no handoff warning or
  selection change.
- Delivery remained explicitly inactive:
  `adapter_enabled=false` and `sync_due=false`.
- Public help exposed only `record`, `list`, `show`, and `withdraw`; no
  `handoff sync` surface was present.
- Recording the handoff appended no source-task event.
- `PRAGMA quick_check` returned `ok`; `PRAGMA foreign_key_check` returned zero
  violations; no SQLite sidecar existed before or after the list inspection.
- The focused CLI test independently confirmed that `handoff list` opens the
  non-immutable query-only snapshot reader exactly once.

## Usability Notes

The Tier 0 `not_required` review receipt and `commit_not_required` completion
evidence were synthetic lifecycle evidence for the isolated forward flow. They
do not replace the two independent Tier 2 reviews or Git completion evidence
required for TG-M12.1 itself.

The temporary database and project were removed after inspection.
