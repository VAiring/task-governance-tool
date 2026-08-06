# Current And Conditional Execution Contract Index

> [!IMPORTANT]
> MIXED CURRENT AND CONDITIONAL FORMAL AUTHORITY. TG-M22.1A, TG-M22.2,
> TG-M21.5, TG-M22.3, and TG-M22.4 are accepted predecessors. TG-M23.1 is
> current design-only authority; TG-M23.2, TG-M23.3, and TG-M24 remain
> accepted but inactive. Load files only when the current
> Task Contract or the
> [repository authority index](../authority.md) selects their exact path and
> ASCII anchor. Their presence, indexing, or reading activates no behavior.

The active [specification](../specification.md) owns supported product behavior,
and the active [design](../design.md) owns current implementation structure.
Each indexed sequence file is the sole detailed execution owner/router for its
named units' purpose, scope, order, dependency, permission, and gates, whether
a named unit is current, accepted, or inactive. A router may delegate one
explicit non-overlapping detail seam to a directly indexed sub-owner; the
sub-owner owns no unit state or parent semantics. Root
[plan.md](../../plan.md) owns current decisions, open issues, cross-sequence
gateways, and non-delegated static contracts. Its concise M24 table is an
intentional non-owning repository-visible index whose Task identity, order,
dependency, purpose, permission, and gate cells are kept exactly equal to the
canonical conditional table by the document checker. The Task database owns
live state and evidence. These files must not mirror status, blockers, targets,
reviews, receipts, or completion history.

## Indexed Contracts

- [TG-M22 Evidence Ledger](tg-m22-evidence-ledger.md#tg-m22-sequence)
  owns accepted TG-M22.1A/TG-M22.2/TG-M21.5/TG-M22.3/TG-M22.4 predecessor
  detail; its supported schema-v19 behavior remains active through the
  specification and design.
- [TG-M23 Derived Evidence](tg-m23-derived-evidence.md#tg-m23-derived-evidence)
  is the sole unit owner/router for current TG-M23.1 design-only detail and
  accepted inactive TG-M23.2/TG-M23.3 detail.
- [TG-M23 Process Safety](tg-m23-process-safety.md#tg-m23-process-safety)
  is the sole delegated owner of the routed Windows containment, private-temp,
  and atomic publication/recovery seam. It owns no unit state, core schema, or
  activation. No Analyzer runtime, worker, model/network path, public CLI or
  Skill call, gate, or Task mutation is active.
- [TG-M24 Verification Runner](tg-m24-verification-runner.md#tg-m24-verification-runner)
  owns accepted inactive Runner design, shadow, gate-integration, and
  acceptance detail for the M24 sequence, which remains inactive.

## Routing Rules

- Use only explicit lowercase ASCII anchors. Existing anchor identifiers are
  stable routing names and must not be silently renamed.
- An indexed document supplies execution detail only for its named current or
  inactive units. It does not replace product behavior in the specification or
  implementation structure in the design.
- A design unit may freeze exact runtime detail inside its approved scope; this
  index and later-unit summaries do not decide that detail early.
- A completed sequence is folded into subsystem-oriented current authority or
  routed to indexed non-authoritative history by a separately approved atomic
  documentation unit. History never supplies missing active authority.
