---
name: boost
description: Use when the user types /boost STEP <ID> to execute one cross-subsystem NovOS plan step from STEPPED_IMPLEMENTATION_PLAN.md with the /goal protocol plus an independent read-only verification subagent before POST_GATE.
disable-model-invocation: true
argument-hint: STEP <ID>
---

# /boost STEP <ID> — Step Execution with Independent Verification

Source rules: CLAUDE.md §4 (step scope lock, 4-phase gates, single-step halt) and §5 (EXECUTE mode); provenance in the CLAUDE.md Parity Table.
Same scope enforcement as `/goal`: `step-lock.sh` sets `.claude/state/active-step`; `guard-edit.sh` blocks paths outside `ALLOWED_FILES`.

## Protocol (in order, no skipping)
1. **Verify state.** `git status`; find `### STEP <ID>` and its roadmap row. COMPLETED or already applied → report and halt.
2. **PRE_GATE.** Run the step's `[PRE_GATE]` exactly. Non-zero → report output and halt.
3. **PARALLEL_READ.** Read only `ALLOWED_FILES` + `READ_ONLY_REFS`, in one parallel batch.
4. **ATOMIC_MUTATION.** Apply `[MUTATION_SPEC]` with Edit/Write; preserve unrelated logic. New files via Write.
5. **INDEPENDENT VERIFICATION.** Spawn exactly one read-only verification subagent (Agent tool; it must not edit files). Give it: the step's full Argon block, the `git diff` of ALLOWED_FILES (and full content of new files), and this checklist:
   - every MUTATION_SPEC item implemented, nothing extra (over-fix);
   - no change outside ALLOWED_FILES;
   - no lazy fixes: suppressions, skipped checks, faked or force-installed packages, `#[allow(` without a `# novos-allow:` reason;
   - no removed/downgraded/disabled packages, tools or verification gates;
   - cross-subsystem effects (callers, stage order, hooks, configs) consistent.
   It returns findings as `file:line — defect`.
6. **Resolve findings.** Fix each finding within ALLOWED_FILES, or, if it needs out-of-scope changes or a user decision, record it for the report. Wait for all subagents to finish before continuing.
7. **POST_GATE.** Run the step's `[POST_GATE]` exactly. Fail → fix in scope and rerun, or report and halt.
8. **Mark COMPLETED.** Set the roadmap row to `**COMPLETED**` in `STEPPED_IMPLEMENTATION_PLAN.md`.
9. **Report and halt.** POST_GATE command and exit code, files changed, verification findings and how each was resolved, anything unresolved or unverified. Stop.

## Hard rules
- Upstream defect outside scope: report `file:line`, halt. Never edit it.
- Never chain steps; one step per turn.
- Never edit `.nebulous/golden-snapshot.toml`, `approvals.toml`, `manifest.toml`. No sudo, destructive disk/bootloader commands or `--from-stage`; builds and VM tests are the user's — print the exact command.
- Do not commit unless the user asks.

## Reporting
Concise and factual. No filler.
