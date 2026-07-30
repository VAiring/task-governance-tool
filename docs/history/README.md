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

## v0.10.0 Roadmap, Plan, And Forward-Evidence Lineage

### `docs/implementation-roadmap.md`

- Immutable history:
  [v0.10.0/implementation-roadmap.md](v0.10.0/implementation-roadmap.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve the implementation roadmap before its active
  milestone-history consolidation.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

### `plan.md`

- Immutable history: [v0.10.0/plan.md](v0.10.0/plan.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve the decision log and execution-status lineage
  before its current-decision and open-issue consolidation.
- Current replacement: [plan.md](../../plan.md)

### `docs/forward-tests/completion-commit-flow.md`

- Immutable history:
  [v0.10.0/forward-tests/completion-commit-flow.md](v0.10.0/forward-tests/completion-commit-flow.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded forward-test evidence for the completion
  commit flow.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

### `docs/forward-tests/static-task-viewer.md`

- Immutable history:
  [v0.10.0/forward-tests/static-task-viewer.md](v0.10.0/forward-tests/static-task-viewer.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded forward-test evidence for the static
  Task Viewer.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

### `docs/forward-tests/tg-m11-git-snapshot-completion.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m11-git-snapshot-completion.md](v0.10.0/forward-tests/tg-m11-git-snapshot-completion.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M11 Git-snapshot completion
  forward-test evidence.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

### `docs/forward-tests/tg-m12-local-handoff.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m12-local-handoff.md](v0.10.0/forward-tests/tg-m12-local-handoff.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M12 local-handoff forward-test
  evidence.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

### `docs/forward-tests/tg-m12-task-contract.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m12-task-contract.md](v0.10.0/forward-tests/tg-m12-task-contract.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M12 Task Contract forward-test
  evidence.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

### `docs/forward-tests/tg-m16-loop-discipline.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m16-loop-discipline.md](v0.10.0/forward-tests/tg-m16-loop-discipline.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M16 loop-discipline forward-test
  evidence.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

### `docs/forward-tests/tg-m18-completion-history.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m18-completion-history.md](v0.10.0/forward-tests/tg-m18-completion-history.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M18 completion-history forward-test
  evidence.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

### `docs/forward-tests/tg-m8-resume-and-completion.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m8-resume-and-completion.md](v0.10.0/forward-tests/tg-m8-resume-and-completion.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M8 resume-and-completion forward-test
  evidence.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

## v0.10.0 Publication-State Lineage

The following captures preserve the exact active bodies at published commit
`a9b80ce177a6dead10d51a070b76ff01f7af0294` before post-release
reconciliation. Each file adds only its non-authority banner before those
bytes. The completed release-stage execution wording remains historical even
when an older index sentence describes it in future tense.

### Publication capture of `docs/specification.md`

- Immutable history:
  [v0.10.0/release-publication/specification.md](v0.10.0/release-publication/specification.md)
- Capture commit: `a9b80ce177a6dead10d51a070b76ff01f7af0294`
- Capture purpose: preserve the full product specification and one-time M19
  publication contract before post-release reduction.
- Current replacement: [docs/specification.md](../specification.md)

### Publication capture of `docs/design.md`

- Immutable history:
  [v0.10.0/release-publication/design.md](v0.10.0/release-publication/design.md)
- Capture commit: `a9b80ce177a6dead10d51a070b76ff01f7af0294`
- Capture purpose: preserve the full design and one-time M19 release
  orchestration before post-release reduction.
- Current replacement: [docs/design.md](../design.md)

### Publication capture of `docs/implementation-roadmap.md`

- Immutable history:
  [v0.10.0/release-publication/implementation-roadmap.md](v0.10.0/release-publication/implementation-roadmap.md)
- Capture commit: `a9b80ce177a6dead10d51a070b76ff01f7af0294`
- Capture purpose: preserve completed M19 execution contracts, approvals,
  verification gates, and pre-reconciliation status.
- Current replacement:
  [docs/implementation-roadmap.md](../implementation-roadmap.md)

### Publication capture of `plan.md`

- Immutable history:
  [v0.10.0/release-publication/plan.md](v0.10.0/release-publication/plan.md)
- Capture commit: `a9b80ce177a6dead10d51a070b76ff01f7af0294`
- Capture purpose: preserve pre-publication decisions, blockers, and the
  superseded volatile handoff mirror.
- Current replacement: [plan.md](../../plan.md)

### Publication capture of `docs/release-install.md`

- Immutable history:
  [v0.10.0/release-publication/release-install.md](v0.10.0/release-publication/release-install.md)
- Capture commit: `a9b80ce177a6dead10d51a070b76ff01f7af0294`
- Capture purpose: preserve the exact pre-publication checklist and artifact
  decision before conversion to the published record.
- Current replacement: [docs/release-install.md](../release-install.md)
