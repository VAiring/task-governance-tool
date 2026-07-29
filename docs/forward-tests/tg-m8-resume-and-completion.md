# TG-M8 Resume And Completion Forward Test

Date: 2026-07-22 JST

Historical execution record: commands reflect the tested release on this date
and are superseded by the current v0.10.0 active guidance.

## Scope

Validate from a project-scoped temporary Skill copy that a new usage session
can explicitly initialize state, rediscover and pause/resume work, and complete
a Tier 2 task only with valid structured review and Git evidence. The test used
only temporary local files and no network access.

## Commands Exercised

The independent tester used the installed Skill guidance and CLI help to run:

```powershell
python scripts/taskgov.py db status --repo <temp-git-project> --json
python scripts/taskgov.py db init --repo <temp-git-project> --json
python scripts/taskgov.py task add --repo <temp-git-project> --title "Forward Tier 2 task" --review-tier 2 --json
python scripts/taskgov.py task edit --repo <temp-git-project> <task-id> --status in_progress --json
python scripts/taskgov.py task current --repo <temp-git-project> --json
python scripts/taskgov.py task edit --repo <temp-git-project> <task-id> --status paused --pause-reason "Safe handoff" --json
python scripts/taskgov.py task edit --repo <temp-git-project> <task-id> --status in_progress --json
python scripts/taskgov.py review target set --repo <temp-git-project> <task-id> --kind git_commit --revision <commit> --json
python scripts/taskgov.py review receipt add --repo <temp-git-project> <task-id> --reviewer <reviewer-a> --kind independent --verdict pass --summary "No blocking findings" --json
python scripts/taskgov.py review receipt add --repo <temp-git-project> <task-id> --reviewer <reviewer-b> --kind independent --verdict pass --summary "No blocking findings" --json
python scripts/taskgov.py task edit --repo <temp-git-project> <task-id> --status done --verification-complete --review-complete --completion-commit-hash <commit> --json
python scripts/taskgov.py task show --repo <temp-git-project> <task-id> --json
```

## Result

PASS. No High or Medium issue was found.

- Initial `db status` returned `db_not_initialized` without creating state;
  explicit `db init` created schema v5.
- `task current` rediscovered `in_progress` and `paused` work. Resume cleared
  the current pause reason while retaining the bounded transition event.
- The review target and completion evidence stored the same canonical full Git
  commit ID.
- Two distinct independent PASS receipts for target generation 1 satisfied the
  Tier 2 gate; open high/medium finding counts were zero.
- The done transition succeeded only after verification, review, and typed Git
  evidence were supplied.
- `task show` returned typed completion evidence, a satisfied `2/2` review gate,
  and bounded receipt/event history.

## Read-Only Invariance

Before and after `db status --read-only`, `task current --read-only`, `task show
--read-only`, and `web export --read-only`, the tester compared the database
SHA-256, size, last-write time, Git HEAD, and Git status. All were unchanged.
No SQLite sidecar or Viewer HTML was created; the Viewer preview reported
snapshot version 3 and `written=false`.

The temporary project was removed after verification. The test receipts were
synthetic lifecycle evidence and do not replace the two independent reviews of
the TG-M8.6 implementation itself.
