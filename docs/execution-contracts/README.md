# Current And Conditional Execution Contract Index

> [!IMPORTANT]
> MIXED CURRENT AND CONDITIONAL FORMAL AUTHORITY. TG-M22.2 is current. Within
> M22, only TG-M21.5, TG-M22.3, and TG-M22.4 are accepted but inactive; TG-M23
> and TG-M24 remain accepted but inactive. Load files only when the current
> Task Contract or the
> [repository authority index](../authority.md) selects their exact path and
> ASCII anchor. Their presence, indexing, or reading activates no behavior.

The active [specification](../specification.md) owns supported product behavior,
and the active [design](../design.md) owns current implementation structure.
Each indexed file is the sole detailed execution owner for its named units'
purpose, scope, order, dependency, permission, and gates, whether a named unit
is current or inactive. Root
[plan.md](../../plan.md) owns current decisions, open issues, cross-sequence
gateways, and non-delegated static contracts. Its concise M24 table is an
intentional non-owning repository-visible index whose Task identity, order,
dependency, purpose, permission, and gate cells are kept exactly equal to the
canonical conditional table by the document checker. The Task database owns
live state and evidence. These files must not mirror status, blockers, targets,
reviews, receipts, or completion history.

## Indexed Contracts

- [TG-M22 Evidence Ledger](tg-m22-evidence-ledger.md#tg-m22-sequence)
  owns accepted TG-M22.1A prerequisite detail and current TG-M22.2 execution
  and acceptance detail. Only TG-M21.5, TG-M22.3, and TG-M22.4 are accepted but
  inactive within M22.
- [TG-M23 Derived Evidence](tg-m23-derived-evidence.md#tg-m23-derived-evidence)
  owns accepted inactive local analysis/reporting detail for the M23 sequence,
  which remains inactive.
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
