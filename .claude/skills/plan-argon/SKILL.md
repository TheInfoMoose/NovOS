---
name: plan-argon
description: Use when the user types /plan-argon or /plan, or asks to "create a plan" for NovOS work. Writes atomic Argon steps (full six-section blocks, one slash command each) into STEPPED_IMPLEMENTATION_PLAN.md and halts for approval without touching code.
disable-model-invocation: true
---

# /plan-argon — Argon Stepped Plan Authoring

Source rules: CLAUDE.md §4 (step scope lock, planning invariants) and §5 (PLAN mode); provenance in the CLAUDE.md Parity Table.

## Mode
- `step-lock.sh` puts the session in PLAN mode on `/plan-argon` or a prompt starting with `/plan`: only `STEPPED_IMPLEMENTATION_PLAN.md` is editable. Do not edit code, scripts, configs or other docs this turn.
- Under Claude there is no separate `implementation_plan.md` artifact. The repository plan file is the single record.

## Procedure
1. Check state first: `git status`, the existing roadmap table and step sections in `STEPPED_IMPLEMENTATION_PLAN.md`. Never re-plan steps already PLANNED or COMPLETED; extend or amend instead.
2. Read only what is needed to scope the work (targeted reads/greps, no repo-wide sweeps when paths are known).
3. Decompose into atomic, single-concern steps: **1–3 closely coupled files per step**. Split anything larger.
4. Write the steps into `STEPPED_IMPLEMENTATION_PLAN.md` **in this same turn**:
   - Add one row per step to the roadmap table: `| **STEP <ID>** | PLANNED | \`/goal\` or \`/boost\` | <title> | <ALLOWED_FILES> |`.
   - Add one section per step: `### STEP <ID>: <title>`, a `* **Recommended Command:**` line, then the fenced Argon block below.
5. Add an **Open Questions** list for anything only the user can decide (architecture forks, approvals for drops/downgrades, privileged actions).
6. Halt. Report the new step IDs, their commands and the open questions. Wait for approval before any `/goal` or `/boost`.

## Required block per step (all six sections, never prose-only)
```text
[STEP_ID]
STEP <ID>: <one-line title>

[SCOPE_LOCK]
ALLOWED_FILES:
  - <path>            # exact paths; step-lock parses this list
READ_ONLY_REFS:
  - <path or doc §>
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
<one shell command verifying upstream prerequisites; non-zero = halt>

[MUTATION_SPEC]
1. <file>: <deterministic, numbered change>

[POST_GATE]
<one shell command that exits 0 only when the step is done: bash -n, py_compile, grep checks, cargo gates, verify-deps.sh>

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

## Choosing the command (exactly one per step; never "A or B")
- `/goal`: bounded deterministic edits in 1–3 files with a fast static POST_GATE.
- `/boost`: cross-subsystem wiring, behavior-sensitive changes (build stages, installer, bootloader, hooks/guards), or anything where a static gate cannot prove correctness and an independent verification subagent is warranted.

## Constraints to encode in steps
- Fresh Stage 1–6 builds only; never plan `--from-stage` runs. Privileged builds and VM tests are user steps: write the exact command for the user, do not make the agent run them.
- Never schedule edits to `.nebulous/golden-snapshot.toml`, `approvals.toml` or `manifest.toml`.
- No step may rely on suppressions or faked/dropped/downgraded packages without explicit user approval (guard-diff barrier).

## Reporting
Concise and factual: steps added, roadmap rows, open questions. No filler.
