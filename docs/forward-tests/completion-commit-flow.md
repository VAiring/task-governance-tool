# Completion Commit Flow Forward Test

Date: 2026-07-07 JST

Historical execution record: commands reflect the tested release on this date
and are superseded by the current v0.8.0 active guidance.

Scope: representative local dry run for TG-M6.4 using only a temporary SQLite
database and a non-created synthetic target project path.

Commands exercised:

```powershell
python task-governance-tool\scripts\taskgov.py db init --repo <synthetic-target> --db <temp-db> --json
python task-governance-tool\scripts\taskgov.py task add --repo <synthetic-target> --db <temp-db> --title "Forward completion flow" --review-tier 2 --verification "forward verification" --json
python task-governance-tool\scripts\taskgov.py task edit --repo <synthetic-target> --db <temp-db> <task-id> --status review_pending --json
python task-governance-tool\scripts\taskgov.py task edit --repo <synthetic-target> --db <temp-db> <task-id> --status done --verification-complete --review-complete --json
python task-governance-tool\scripts\taskgov.py task edit --repo <synthetic-target> --db <temp-db> <task-id> --status done --verification-complete --review-complete --commit-not-required --json
python task-governance-tool\scripts\taskgov.py task show --repo <synthetic-target> --db <temp-db> <task-id> --json
```

Observed result:

- database initialization succeeded
- task moved to `review_pending`
- done transition without commit evidence failed with `commit_required`
- done transition with `--commit-not-required` succeeded
- `task show` reported `completion_commit_required=0` and an empty
  `completion_commit_hash`
- the synthetic target project path was not created

This confirms the documented no-managed-materials completion flow works without
target-project mutation. The committed-materials path is covered by CLI tests
using `--completion-commit-hash`.
