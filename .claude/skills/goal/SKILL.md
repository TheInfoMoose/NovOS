---
name: goal
description: Use when the user types /goal STEP <ID> (or "Proceed/Execute STEP <ID>") to execute one bounded NovOS plan step from STEPPED_IMPLEMENTATION_PLAN.md through PRE_GATE → read → mutate → POST_GATE, then halt.
disable-model-invocation: true
argument-hint: STEP <ID>
---

# /goal STEP <ID> — Single Bounded Step Execution

Source rules: CLAUDE.md §4 (step scope lock, 4-phase gates, single-step halt) and §5 (EXECUTE mode); provenance in the CLAUDE.md Parity Table.
`step-lock.sh` records the step in `.claude/state/active-step`; `guard-edit.sh` then blocks any path outside that step's `ALLOWED_FILES`.

## Protocol (in order, no skipping)
1. **Verify state.** Run `git status`. Locate `### STEP <ID>` and its roadmap row in `STEPPED_IMPLEMENTATION_PLAN.md`. If the row is COMPLETED or the mutations are already present, report that and halt; never repeat work.
2. **PRE_GATE.** Run the step's `[PRE_GATE]` command exactly. Non-zero exit → report the missing dependency and the output, then halt.
3. **PARALLEL_READ.** Read only the step's `ALLOWED_FILES` and `READ_ONLY_REFS`, batched in one parallel call. No exploratory repo-wide searches when paths are given.
4. **ATOMIC_MUTATION.** Apply the `[MUTATION_SPEC]` deterministically with Edit/Write. Preserve unrelated logic and comments. Create new files with Write (it makes parent directories); do not `mkdir` paths outside ALLOWED_FILES.
5. **POST_GATE.** Run the step's `[POST_GATE]` command exactly. If it fails, fix within ALLOWED_FILES and rerun; if the fix needs out-of-scope files, report and halt.
6. **Mark COMPLETED.** Set the step's roadmap row status to `**COMPLETED**` in `STEPPED_IMPLEMENTATION_PLAN.md`.
7. **Report and halt.** Output the POST_GATE command and exit code (plus relevant output), files changed, anything left unverified. Stop.

## Hard rules
- **Upstream defect outside scope:** report `file:line` and the defect, then halt. Do not edit it.
- **Never chain steps.** One step per turn; do not start STEP N+1.
- **Barriers:** no suppressions, faked packages, or dropped/downgraded/disabled entries without explicit user approval (`guard-diff.sh` enforces; annotate genuine exceptions with `# novos-allow: <reason>`). Never edit `.nebulous/golden-snapshot.toml`, `approvals.toml`, `manifest.toml`.
- **Privilege:** no sudo, no destructive disk/bootloader commands, no `--from-stage`. Builds and VM tests are the user's: print the exact command and stop.
- Do not commit unless the user asks.

## Reporting
Concise and factual: gate commands, exit codes, changed files. No filler.
