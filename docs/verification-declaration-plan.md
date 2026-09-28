# Explicit Verification Declaration Correction

## Approved Boundary

This is the bounded execution owner for Task `tg_task_ca05b87cbc6e1f72`,
approved by the user on 2026-09-28. It does not replace the product or design
owners routed by [authority.md](authority.md). Live status and evidence remain
in the public Task CLI.

The outcome is to distinguish unspecified verification, required verification,
and an explicit not-required declaration with a short reason. Registration,
including revision-zero Tasks, remains possible before the declaration is
known; a new completion does not. Neither acceptance prose nor old blank
fields supplies an inferred declaration. Existing manual Receipt and qualifying
Runner paths remain available. Past completion records are not rewritten.

## One Bounded Execution Unit

This correction is one optional unit, independent of the RG-3 lane. Its internal
steps are sequential, not new Tasks or a new workflow:

1. Synchronize the directly affected Task, verification/completion, persistence,
   and shared Runner contracts; implement declaration input, storage/read,
   target invalidation and completion gating together. Extend existing add/edit
   and batch input; add no normal-loop command or natural-language parser.
2. Verify the corrected omission case and valid routes in isolated databases,
   including old unfinished/done/reopened Tasks, preservation and migration
   reentry, single/batch inheritance/overrides, stale evidence, transaction
   rollback and privacy. Run coupled CLI/JSON, document and release checks.
3. Bind verification to the final target, obtain two independent Tier 2 reviews,
   resolve blocking findings and create one scoped local commit on
   `codex/review-result-template`. Record completion only after current gates
   really pass and the public CLI supports the approved live state.

Expected writes are limited to those contracts, coupled runtime/schema,
focused tests, short Skill guidance/examples and release metadata. A schema
change uses explicit setup only. Tests use disposable databases/projects, not
the live database or existing measurement environments. No rollback of done
Tasks, RG-3 change, benchmark, dependency installation, ACL/host change,
network, push, CI dispatch or Main integration is authorized. Existing unrelated
working-tree changes are preserved.

## Completion And Retirement

Completion requires the Task's whole verification expectation, two qualifying
independent reviews of the exact target, and the local commit. The review tier
is 2. If live setup migration becomes necessary for governance closeout, stop
at that boundary for separate user approval; do not simulate completion or
weaken the migration boundary.

This document is active until the Task is done or explicitly superseded and
the durable behavior is fully owned by the corresponding specifications and
designs. Its later retirement follows AGENTS.md's authorized history/routing
transition; finishing this correction alone does not perform that transition.
