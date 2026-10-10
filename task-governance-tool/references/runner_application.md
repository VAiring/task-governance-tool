# Applying The Existing Verification Runner

Read this only when introducing or changing an explicitly approved Runner
application. It is not a normal Task-loop prerequisite. The
[Plan actions and OS limits](cli_contracts.md#runner-plan-actions),
[Setup choices](cli_contracts.md#setup), and
[normal verification/review flow](task_workflow.md#set-the-review-target)
remain controlling. This guide adds no command, trust grant or execution gate.

## Decide Whether The Approved Checks Fit

Runner uses the CLI's fixed Python runtime, standard library and exact private
Git target. It does not select another interpreter/venv, install dependencies,
inherit the caller's Python environment, run a shell, or retain raw test output.
An entrypoint must be a Python script or module present in that target. A
stdlib module name such as `unittest` is not itself a target-owned entrypoint.
The target owns all required test files and configuration; ambient untracked
files, local secrets and working-tree edits are not inputs. Trust and execution
approval must already cover the actual checks; materialization is not a network
or hostile-code sandbox.

Keep manual verification when the specified checks require another runtime,
external packages unavailable to this runtime, ambient configuration or data,
visual judgment, shell behavior, or other unsupported execution conditions.
Record the concrete mismatch and obtain the ordinary project decision before
changing the prescribed checks or adding an adapter. Do not call a smaller or
different suite equivalent to obtain Runner PASS.

## A Target-Owned Unittest Entry

For an approved `python -B -m unittest -v` from the project root, the following
root-level `verify.py` preserves unittest's discovery entry (`module=None`):

```python
import unittest

if __name__ == "__main__":
    unittest.main(module=None)
```

Adding this file is an ordinary, separately authorized source change. Include
it in the fixed Git target. Before using it, compare
`python -B -m unittest -v` and `python -B verify.py -v` on the same exact files,
with the same Python version, project-root cwd and discovery arguments. Check
the discovered test identities/count, settings, exit outcome and intentional
failure detection. This comparison belongs to adaptation validation, not every
normal Task run. It establishes equivalence only where tests/configuration
do not depend on environment variables, site customization, external imports,
the entrypoint's argv[0], or different import/path behavior. Runner reconstructs
Python's search path from the target root and existing paths under the fixed
runtime's base. Windows uses `-I` without `-S`, so base-installed packages and
site initialization are not categorically excluded; Linux/macOS additionally
use `-S`. Do not assume identical startup/import behavior across platforms. Custom
`load_tests`, discovery roots/patterns and fixture requirements still need their
project-specific assessment; preserve explicit discovery arguments literally.

Directly running `test_counter.py` without a `unittest.main` entry executes no
tests and may exit zero. A script using `unittest.main()` instead of
`module=None` searches its own module by default. Neither is an equivalent
replacement for project discovery. Zero exit by itself proves neither coverage
nor equivalence, and Runner does not infer test sufficiency from output.

For the simple six-test, standard-library-only counter fixture validated in
this repository, use this draft after confirming those conditions:

```json
{
  "version": 2,
  "steps": [{
    "step_id": "unittest",
    "mode": "script",
    "entrypoint": "verify.py",
    "argv": ["-v"],
    "cwd": ".",
    "timeout_seconds": 60,
    "cpu_seconds": 60,
    "windows_limits": {"memory_mib": 256, "process_limit": 4},
    "output_byte_limit": 1048576
  }]
}
```

These are example bounds, not universal test settings. Windows requires its
limit pair; Linux/macOS ignore it and apply their documented per-process CPU
and wall limits. Unexecuted platforms remain unverified.

## Prepare Once, Bind Each Task, Then Use The Returned Route

1. Use an already installed physical package with ordinary Setup completed and
   canonical state/config ignored. Do not reinstall or rerun Setup for each
   Task. If global Runner ON is explicitly selected during Setup, it can create
   an empty Plan; ON alone does not supply a Task entry.
2. For the selected nonterminal Task with a current Contract and verification
   criterion, publish the approved draft using
   [the existing `replace` invocation](cli_contracts.md#runner-plan-example-and-os-limits).
   The tool fills Task/basis identities and `coverage=full`; this does not
   establish that the supplied steps actually cover the criterion. The first
   absent-file replace opts in; a present disabled Plan stays disabled until
   an explicitly authorized Setup ON selection. Neither operation launches tests.
3. Continue with the normal chosen review-preparation route. Its target-setting
   operation is the sole Runner launch: a current matching Plan runs against
   the exact fixed target and records bounded machine-observed evidence. Use
   the returned `runner_pass` route and ready complete Packet directly; do not
   run the same checks manually, retype a Receipt, or add a routine status read.
   Reviews and the chosen completion route still have their usual gates.

Absent/disabled Plan or no entry for this Task selects the existing manual
route. Perform the prescribed verification and record its actual result/full
coverage against the returned generation; use the Packet returned by successful
Receipt preparation. A stale, ambiguous or invalid Plan is a rejection, not
permission to fall back. A failed/timed-out Runner, uncertain cleanup or stale
basis cannot be overridden with a manual PASS Receipt. Diagnose the reported
condition under the existing recovery rules, fix the cause, and use a fresh
target and actual new verification when required. Missing/failed Packet
preparation is not permission to replay target-setting and launch again; follow
[preparation recovery](task_workflow.md#review-handoff-recovery).

## Maintain The Mapping And Assess The Cost

When a Contract or verification criterion changes, reassess the whole mapping.
For one exact-current enabled entry, combine the approved basis edit with
`rebind` only if its existing steps still provide full coverage; use `replace`
with an approved new draft if they change, `detach` for manual verification, or
`disable` for a deliberate project-wide switch. A target-only change does not
require or authorize a Plan update. These are existing actions, not automatic
changes or additional confirmations. If Task editing succeeds but Plan
publication is `unconfirmed`, keep the committed Task edit and complete the
reported explicit Plan repair before relying on Runner. Never replay the
entire combined edit blindly.

| Condition | Work retained or saved |
|---|---|
| Initial adoption | Installation/Setup if needed, explicit trust, equivalence assessment, any approved target entrypoint change, and Plan draft/publication cost work. |
| Each new Task | A matching Task-specific Plan entry and coverage judgment are still needed; another Task's entry does not qualify. |
| Changed criterion | Reassessment and the appropriate explicit Plan action remain necessary. |
| Prepared matching normal path | Target preparation includes execution/evidence/Packet, avoiding the separate manual test invocation and Receipt registration. |
| Unsupported or manual route | Keep the original checks and their manual result registration; no saving is assumed. |

This can help when an approved reusable entrypoint and Plan mapping are cheap
to maintain, especially across multiple target generations. For a one-off Task,
preparation may cost more than the two separate operations it removes. Operation
counts are not LLM response counts: tool calls can be batched, waits and review
judgment remain, and no response/token reduction or cache hit rate has been
measured here. No performance benchmark is added as a completion gate.
