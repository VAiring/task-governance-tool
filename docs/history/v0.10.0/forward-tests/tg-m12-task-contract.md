> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY — DO NOT USE AS CURRENT VERIFICATION EVIDENCE OR PRODUCT CONTRACT.**
> This file preserves the former docs/forward-tests/tg-m12-task-contract.md from exact source
> commit cbf75372617e90ca0b54746ae27f24a4e67cb292. Internal words such as
> “current”, “approved”, and “implemented” describe that historical source
> only. Current replacement is [docs/implementation-roadmap.md](../../../implementation-roadmap.md).

---

# TG-M12.2 Task Contract Forward Test

Date: 2026-07-26

Runtime: task-governance-tool v0.5.0, schema v8

Context: fresh sub-agent with no inherited task discussion

Result: PASS

## Scenario

The tester read `AGENTS.md`, `SKILL.md`, and only the needed one-level
references, then used an isolated temporary repository and explicit SQLite
path. It did not edit or stage the source repository and did not use the
network.

The representative flow:

1. initialized a new schema-v8 database;
2. added an `in_progress`, Tier 1 task with explicit scope, acceptance, and
   constraints copied into Contract revision 1;
3. inspected the current Contract;
4. replayed canonically identical content without authority or reason;
5. set a review target, added one independent PASS receipt, and recorded
   completion evidence;
6. changed acceptance under an explicit
   `user_instruction:<task-id>:2` authority and concise reason;
7. recorded an out-of-scope handoff; and
8. checked database integrity and privacy rejection.

## Observations

- Initialization applied migrations 1 through 8.
- Initial Contract input returned `recorded=true`, `revision=1`, and required
  no additional user question.
- Exact replay returned `changed_fields=[]`, `event=null`, and
  `recorded=false`; revision, event history, and `updated_at` were unchanged.
- The semantic change created revision 2, cleared completion evidence and the
  current review target, advanced review generation from 1 to 2, preserved the
  historical receipt, and made the fresh Tier 1 review gate unsatisfied.
- The handoff captured `source_contract_revision=2` without changing task
  history or timestamp.
- A secret-shaped Contract value returned `privacy_rejected` and was absent
  from stored rows.
- `PRAGMA quick_check` returned `ok`; `PRAGMA foreign_key_check` returned no
  violations.
- Source-repository Git status was unchanged before and after the flow.

## Judgment Count

- Normal Tier 1 independent review judgments: 1.
- Additional Task Contract judgments: 0.
- Additional Task Contract user questions: 0.

Replay classification, authority/revision syntax validation, evidence and
review invalidation, gate evaluation, and handoff revision capture were
deterministic.

## Coverage Boundary

This forward flow did not independently exercise concurrent semantic writes,
busy/crash retry, legacy migration, or done/reopen. Those paths are covered by
the focused and full offline regression suites. Initial
`user_instruction:<task-id>:1` cannot be formed during `task add` because the
ID does not yet exist; the initial authority reference is therefore optional,
and an already stable roadmap or governing-document reference may be used
when available.
