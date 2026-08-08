# Historical Documentation Index

> [!CAUTION]
> Files indexed here are preserved implementation history, not current
> authority. Use the active [specification](../specification.md),
> [design](../design.md), root [AGENTS.md](../../AGENTS.md), and root
> [plan.md](../../plan.md) according to the repository authority order. Use
> the public CLI for live Task state and evidence.

Historical capture bodies are immutable after the capture commit. Index
entries are normally append-only; an atomic active-source retirement may
update only their `Current replacement` routing metadata while adding the
final immutable capture and retirement entry in the same revision.

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
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

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
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

### `docs/forward-tests/static-task-viewer.md`

- Immutable history:
  [v0.10.0/forward-tests/static-task-viewer.md](v0.10.0/forward-tests/static-task-viewer.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded forward-test evidence for the static
  Task Viewer.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

### `docs/forward-tests/tg-m11-git-snapshot-completion.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m11-git-snapshot-completion.md](v0.10.0/forward-tests/tg-m11-git-snapshot-completion.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M11 Git-snapshot completion
  forward-test evidence.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

### `docs/forward-tests/tg-m12-local-handoff.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m12-local-handoff.md](v0.10.0/forward-tests/tg-m12-local-handoff.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M12 local-handoff forward-test
  evidence.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

### `docs/forward-tests/tg-m12-task-contract.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m12-task-contract.md](v0.10.0/forward-tests/tg-m12-task-contract.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M12 Task Contract forward-test
  evidence.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

### `docs/forward-tests/tg-m16-loop-discipline.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m16-loop-discipline.md](v0.10.0/forward-tests/tg-m16-loop-discipline.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M16 loop-discipline forward-test
  evidence.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

### `docs/forward-tests/tg-m18-completion-history.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m18-completion-history.md](v0.10.0/forward-tests/tg-m18-completion-history.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M18 completion-history forward-test
  evidence.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

### `docs/forward-tests/tg-m8-resume-and-completion.md`

- Immutable history:
  [v0.10.0/forward-tests/tg-m8-resume-and-completion.md](v0.10.0/forward-tests/tg-m8-resume-and-completion.md)
- Capture commit: `cbf75372617e90ca0b54746ae27f24a4e67cb292`
- Capture purpose: preserve superseded TG-M8 resume-and-completion forward-test
  evidence.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

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
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

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

## v0.10.0 Post-Release Correctness Lineage

### Post-release capture of `docs/implementation-roadmap.md`

- Immutable history:
  [v0.10.0/post-release/implementation-roadmap.md](v0.10.0/post-release/implementation-roadmap.md)
- Capture commit: `43c91d5987b0c35c66f834789aea782e98dcaff7`
- Capture purpose: preserve the completed TG-M19.11 through TG-M19.14
  post-release execution sequence before TG-M20 authority activation.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

## TG-M20 Operational-Baseline Study Lineage

### TG-M20 synthesis and retired evidence boundary

- Immutable history:
  [v0.10.0/m20-operational-baseline.md](v0.10.0/m20-operational-baseline.md)
- Capture unit: `TG-M20.5`
- Capture purpose: preserve the stratified aggregate, denominators,
  exclusions, frozen-rule decisions, limitations, and bounded follow-up
  observation proposals before the reduced corpora and temporary study
  scaffolding were retired.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

## TG-M20S Task-Decomposition Study Lineage

### TG-M20S decision and retired evidence boundary

- Immutable history:
  [v0.10.0/m20s-task-decomposition.md](v0.10.0/m20s-task-decomposition.md)
- Capture unit: `TG-M20S.2`
- Capture purpose: preserve the aggregate qualification, terminal decision,
  limitations, and no-rerun retirement boundary after temporary study
  machinery and reduced evidence were retired.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

## Roadmap Retirement Lineage

The former active source path `docs/implementation-roadmap.md` is physically
retired. Older immutable captures may retain links to that former path in
their frozen banners or bodies; those links are historical tombstones, not
current routing. Every index entry above that previously pointed to the
retired path now uses the same positive authority set, and live Task state and
evidence remain available only through the public CLI.

### Final retirement capture of `docs/implementation-roadmap.md`

- Immutable history:
  [v0.10.0/roadmap-retirement/implementation-roadmap.md](v0.10.0/roadmap-retirement/implementation-roadmap.md)
- Capture commit: `af5e19545e4f5b59817c70fbc5e2763c0dbf2e1e`
- Capture purpose: preserve the exact final active roadmap body before its
  atomic physical retirement.
- Current replacements: [AGENTS.md](../../AGENTS.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), and [plan.md](../../plan.md). Use the public
  CLI for live Task state and evidence.

## v0.11.0 Pre-M22 Completed Execution Lineage

### Completed M20, M20S, and M21 execution narrative

- Immutable history:
  [v0.11.0/pre-m22-completed-execution.md](v0.11.0/pre-m22-completed-execution.md)
- Source commit: `695b240178681a072b5cbd73845dff8e31a281d6`
- Capture unit: `TG-DOC.1`
- Capture purpose: preserve completed M20/M20S observation and M21 design,
  activation, acceptance, and correction narrative retired from the normal
  read set before M22 execution.
- Current replacements: [docs/authority.md](../authority.md),
  [docs/specification.md](../specification.md),
  [docs/design.md](../design.md), [plan.md](../../plan.md), and the applicable
  [conditional execution contract index](../execution-contracts/README.md).
  Use the public CLI for live Task state and evidence. This capture is lineage
  only and cannot fill a current authority gap or satisfy a gate.

## v0.12.0 TG-M23 Authority-Split Lineage

### Pre-process-safety split capture of `docs/execution-contracts/tg-m23-derived-evidence.md`

- Immutable history:
  [v0.12.0/tg-m23-pre-process-safety-split.md](v0.12.0/tg-m23-pre-process-safety-split.md)
- Source commit: `7313483a9fd160f0ec8127b013d9f5533d2d16ab`
- Capture unit: `TG-M23.1`
- Capture purpose: preserve the exact accepted-but-inactive TG-M23 authority
  body before its current one-level core/process-safety owner split.
- Current replacements:
  [TG-M23 core owner/router](../execution-contracts/tg-m23-derived-evidence.md#tg-m23-derived-evidence)
  and
  [TG-M23 process-safety owner](../execution-contracts/tg-m23-process-safety.md#tg-m23-process-safety).
  Use the public CLI for live Task state and evidence. This capture is lineage
  only and cannot fill a current authority gap or satisfy a gate.

## v0.12.0 TG-M24 Pre-Design-Activation Lineage

### Pre-design-activation capture of `docs/execution-contracts/tg-m24-verification-runner.md`

- Immutable history:
  [v0.12.0/tg-m24-pre-design-activation.md](v0.12.0/tg-m24-pre-design-activation.md)
- Source commit: `888a77759cfc59376089b8ebdc509e748638f603`
- Capture unit: `TG-M24.1`
- Capture purpose: preserve the exact accepted-but-inactive TG-M24 authority
  body before M24.1 freezes the Runner design and the owner changes from a
  conditional-only route to mixed accepted-predecessor/inactive authority.
- Current replacement:
  [TG-M24 mixed owner](../execution-contracts/tg-m24-verification-runner.md#tg-m24-verification-runner).
  Use the public CLI for live Task state and evidence. This capture is lineage
  only and cannot fill a current authority gap or satisfy a gate.
