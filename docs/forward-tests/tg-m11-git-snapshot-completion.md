# TG-M11 Git Snapshot Completion Forward Test

Date: 2026-07-26 JST

Historical execution record: commands reflect the tested release on this date
and are superseded by the current v0.9.0 active guidance.

## Scope

Validate from a fresh temporary Skill copy that the documented
review-before-commit path binds two Tier 2 reviews of staged Git content to one
later completion commit without another review pair. Also confirm that a
different committed tree is rejected atomically. The test used only temporary
local files and no network access.

## Commands Exercised

The independent tester followed the installed Skill guidance with this command
surface:

```powershell
python scripts/taskgov.py db status --repo <temp-git-project> --json
python scripts/taskgov.py db init --repo <temp-git-project> --json
python scripts/taskgov.py task add --repo <temp-git-project> --title "Snapshot completion" --review-tier 2 --status in_progress --json
git add -- <intended-path>
python scripts/taskgov.py review target set --repo <temp-git-project> <task-id> --kind git_snapshot --json
python scripts/taskgov.py review receipt add --repo <temp-git-project> <task-id> --reviewer <reviewer-a> --kind independent --verdict pass --summary "No blocking findings" --json
python scripts/taskgov.py review receipt add --repo <temp-git-project> <task-id> --reviewer <reviewer-b> --kind independent --verdict pass --summary "No blocking findings" --json
git commit -m "<project-approved message>"
python scripts/taskgov.py task edit --repo <temp-git-project> <task-id> --status done --verification-complete --review-complete --completion-commit-hash <commit> --json
python scripts/taskgov.py task show --repo <temp-git-project> <task-id> --json
```

The negative flow repeated target capture and the two receipt writes, then
changed the staged content before creating the candidate commit.

## Result

PASS. No High or Medium usability or integrity issue was found.

- Initial `db status` returned `db_not_initialized` without creating `state/`;
  explicit `db init` created schema v6 through migrations 1-6.
- Public `git_snapshot` target creation succeeded without `--revision` at
  generation 1.
- Two distinct independent PASS receipts satisfied the Tier 2 gate. The
  matching single-parent commit completed successfully while target generation
  and receipt count stayed unchanged, so no extra LLM review was required.
- When the committed tree differed from the reviewed staged snapshot,
  completion failed with stable error `review_target_mismatch`.
- The rejected transition left database bytes, task status, completion
  evidence, event count, receipt count, Git `HEAD`, and Git status unchanged.
- Receipt and bounded review-evidence projections omitted the snapshot base.
  No raw diff, repository path, reviewer transcript, or private reasoning was
  retained.
- `PRAGMA quick_check` returned `ok`; `PRAGMA foreign_key_check` returned zero
  violations. Immutable inspection did not change database bytes or timestamp
  and created no SQLite sidecar.

## Usability Notes

The missing-database status is intentionally a nonzero result, and a caller
uses `task show` for the expanded post-completion confirmation because
`task edit` returns a compact task projection. The temporary environment used a
dedicated nonstandard Skill-copy path because its sandbox did not permit the
normal `.agents/skills` location; this did not change CLI behavior.

The two stored receipts were synthetic lifecycle evidence for the forward
flow. They do not replace the two independent Tier 2 reviews of the TG-M11.4
implementation itself.
