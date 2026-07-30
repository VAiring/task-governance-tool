> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY — DO NOT USE AS CURRENT VERIFICATION EVIDENCE OR PRODUCT CONTRACT.**
> This file preserves the former docs/forward-tests/tg-m18-completion-history.md from exact source
> commit cbf75372617e90ca0b54746ae27f24a4e67cb292. Internal words such as
> “current”, “approved”, and “implemented” describe that historical source
> only. Current replacement is [docs/implementation-roadmap.md](../../../implementation-roadmap.md).

---

# TG-M18 Completion-Cycle History Forward Acceptance

Date: 2026-07-30 JST

Runtime: task-governance-tool v0.10.0, schema v16, Viewer snapshot v4 with
source schemas v5-v16

Result: PASS

## Boundary

This is a sanitized repository-candidate acceptance record for TG-M18.4. It
records no raw command output, review transcript, private prompt, path list,
diff, secret, or external publication action. No push, tag, PR, workflow
dispatch, or release was performed.

## Verified Behavior

- Schema-v1-through-v14 migration, schema-v15 activation-window recovery, and
  marker-only schema-v16 reentry preserve existing task, event, review,
  identity, binding, backup, and relocation state.
- Native `git_commit`, `external_revision`, and `commit_not_required`
  completions append immutable cycles. Git snapshot and commit targets,
  independent and fallback review bases, Contract revisions, and same-second
  cycle ordering retain their exact accepted values.
- Reopen preserves earlier completion cycles as audit history while clearing
  current eligibility. Historical verification, target, receipt, and
  completion evidence never satisfies a later current gate.
- The existing `task show` response and Viewer snapshot v4 expose the same
  bounded newest-first history projection. Source schemas v5-v14 receive an
  honest empty legacy-incomplete projection; source schemas v15-v16 use stored
  cycles.
- Per-row and complete-component UTF-8 limits, exact allow-lists, internal-link
  omission, Viewer CSP/no-storage/no-network behavior, last-good publication,
  backup/recovery, physical-install, self-host, nested-Git, and relocation
  boundaries passed their offline tests.
- Public behavior remains exactly 20 command leaves with no history command or
  option. The normal Task loop remains nine governance calls with Effort
  Advisory disabled and ten when mechanically enabled. Completion history adds
  no LLM judgment, question, user-return stop, or normal-loop call.

## Checks

- The complete offline unittest suite passed.
- The focused CLI help, package self-containment, migration, activation,
  lifecycle, task-show, Viewer, review-evidence, and integrated acceptance
  tests passed.
- `taskgov --version` reported `0.10.0`.
- `doctor --read-only --json` reported package `clean`, schema v16, and the
  current Viewer projection.
- SQLite `quick_check` returned `ok`; `foreign_key_check` returned no rows.
- `git diff --check` passed.

## Remaining Boundary

This record accepts only the local v0.10.0 repository candidate. M19 owns
release authority, licensing, candidate-branch CI, main cutover, tagging, and
GitHub Release decisions.
