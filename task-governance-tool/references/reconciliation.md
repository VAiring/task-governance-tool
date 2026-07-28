# Reconciliation And Test Repair

Load this reference only when one `task effort` result returns
`data.suggested_action=reconcile_scope`, or when a test or review failure
recurs after an attempted repair. Do not load it on the successful green path.
One Effort result is one non-blocking episode regardless of how many metrics
were exceeded; loading this reference adds no taskgov command, question, or
automatic stop.

## Run One Bounded Episode

1. **Evidence and authority.** Read the current failure or advisory, Task
   Contract and governing authority, and current-generation review evidence.
   A failure or Effort signal is evidence, never authority.
2. **Material equivalence.** Compare the causal hypothesis, authorized repair,
   and expected result with repairs already attempted in this live session.
   Changing only command spelling, runner wrapper, working directory, Task
   label, or execution-unit label is not new evidence.
3. **Useful diagnostics.** Run a safe diagnostic only when its result can
   materially change the hypothesis, authorized repair, or expected result.
   A useful diagnostic is not a repair attempt; repeating an uninformative
   diagnostic does not justify another equivalent repair.
4. **Attempt boundary.** Count a failed repair only after corrective action is
   followed by failed verification or review. The triggering failure is not
   itself a repair attempt. Without new evidence, after two materially
   equivalent failed repairs, do not execute a third equivalent repair. A
   genuinely different evidence-backed repair remains allowed.
5. **Test integrity.** Never weaken a test merely to obtain PASS. Change a
   wrong test only when current governing authority establishes the expected
   behavior. A Task Contract or acceptance change still requires later
   explicit authority.
6. **Scope classification.** Keep the repair in the current Task only when it
   is within accepted scope and current authority permits it. This includes
   acceptance-required work and regressions introduced by the current Task. A
   failing test alone establishes neither condition. Record an unmet
   acceptance blocker only after safe authorized work for the affected Task or
   lane is exhausted; otherwise hand off the out-of-scope discovery
   immediately. Reserve `paused` for an explicit temporary interruption.
7. **Review remediation.** A current-generation `changes_requested` receipt or
   unresolved high or medium finding blocks completion immediately. A
   remediation cycle requires a meaningful fix, applicable finding
   resolution, a fresh review target, and a fresh current-generation review
   result. A result that remains blocking counts as one unsuccessful cycle;
   completion still requires fresh qualifying PASS receipts. Historical
   receipts, PASS receipts, and low findings do not independently add a stop.
   Without new evidence, after two materially equivalent unsuccessful
   remediation cycles, do not execute a third equivalent cycle; use the
   existing bounded decision or blocker path.
8. **Continue safely.** Continue unrelated safe ready lanes. After safe
   independent work, return all remaining user decisions in one bounded batch
   rather than stopping once per discovery.

Keep attempt comparison session-local. Do not store it in SQLite, Task events,
checkpoints, or another counter or latch, and do not reconstruct it after
compaction. A fresh session resets the comparison and relies on durable Task,
handoff, finding, and receipt state for rediscovery.

## Bounded Examples

- Two materially equivalent corrective repairs are each followed by the same
  failed verification. Changing the test command or working directory is not
  new evidence; do not run a third equivalent repair.
- A safe diagnostic reveals a different dependency or input condition that
  changes the causal hypothesis. Use that new evidence to evaluate a genuinely
  different authorized repair.
- A failure is a regression introduced by the current Task and current
  authority permits the fix. Keep it in the Task. An unrelated discovery that
  does not meet the blocker condition goes to local handoff while unrelated
  ready lanes continue.
