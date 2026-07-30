# Historical Documentation Index

> [!CAUTION]
> Files indexed here are preserved implementation history, not current
> authority. Use the active [specification](../specification.md),
> [design](../design.md), [implementation roadmap](../implementation-roadmap.md),
> root [AGENTS.md](../../AGENTS.md), and root [plan.md](../../plan.md)
> according to the repository authority order.

Historical files and their index entries are append-only after the capture
commit. A later history operation may add a new immutable file and a new index
entry, but must not revise an existing archived body or entry.

## v0.10.0 Pre-Consolidation Lineage

### `docs/specification.md`

- Immutable history:
  [v0.10.0/specification.md](v0.10.0/specification.md)
- Capture commit: `1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1`
- Capture purpose: preserve the specification before active-contract
  consolidation.
- Current replacement: [docs/specification.md](../specification.md)

### `docs/design.md`

- Immutable history: [v0.10.0/design.md](v0.10.0/design.md)
- Capture commit: `1ac8c001073b1a4cb29e9de3f0281d8ff2d9aca1`
- Capture purpose: preserve the design before active-contract consolidation.
- Current replacement: [docs/design.md](../design.md)

TG-M19.2 may append the roadmap, plan, and superseded forward-evidence lineage
to this index. It must not alter the two archived bodies above.
