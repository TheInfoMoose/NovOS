---
trigger: always_on
description: "Enforces strict per-step file scope locks, pre/post dependency gates, and Argon execution efficiency."
---

# Argon Atomic Step Scope & Gate Protocol

## 1. Strict Step Scope Containment (`SCOPE_LOCK`)
* **Explicit File Allowlist (`ALLOWED_FILES`):** When executing a stepped prompt, the agent may ONLY create, edit, or delete files explicitly listed in that prompt's `ALLOWED_FILES` block.
* **Hard Prohibition on Out-of-Scope Edits (`FORBIDDEN_SCOPE`):** Never modify files belonging to earlier or later steps, even if an adjacent improvement is noticed. If an upstream file has a defect that blocks the current step, report the exact file and line defect and **halt** rather than editing out-of-scope files.
* **Single-Step Execution (`HALT_AFTER_STEP`):** Execute at most **one** atomic step per user turn. Never automatically roll into Step `N+1` after completing Step `N`.

## 2. Mandatory Dependency Gate Protocol (`PRE_GATE` -> `MUTATE` -> `POST_GATE`)
Every stepped execution turn must follow a strict 4-phase state machine:
1. **`PRE_GATE` (Upstream Dependency Check):** Run the step's pre-condition command first to verify all required inputs, files, and symbols from prior steps exist. If `PRE_GATE` exits non-zero, halt immediately and report the missing dependency.
2. **`PARALLEL_READ`:** Read only the files in `ALLOWED_FILES` (plus explicitly named read-only reference files) in a single parallel tool call batch. Do not perform exploratory repo-wide searches when paths are already specified.
3. **`ATOMIC_MUTATION`:** Apply deterministic edits using `write_file` or `edit_file`. Preserve all unrelated logic and comments.
4. **`POST_GATE` & `HALT`:** Run the step's deterministic verification command (`bash -n`, `python3 -m py_compile`, YAML/RON validation, or `verify-deps.sh`). Once `POST_GATE` exits `0`, output a concise status summary and **halt immediately**.

## 3. Argon Prompt Format & Planning Standard
Whenever operating in Planning Mode (`/plan`) or generating stepped outputs:
1. **Always Persist Steps to Repository `.md` Immediately:** Write/update all steps and full Argon prompts directly in the repository's plan file (e.g., `STEPPED_IMPLEMENTATION_PLAN.md`) **during the `/plan` turn itself** (while leaving application code/scripts untouched until approval). Never defer updating the repository `.md` plan file until after approval, and include the same complete Argon prompts in the `implementation_plan.md` artifact.
2. **Maximum Atomic Granularity:** Break tasks down into small, single-concern atomic steps (typically 1–3 closely coupled files per step) so each step isolates one defect or subsystem for maximum agent focus and zero regression risk.
3. **Single Deterministic Slash Command per Step (Never "A or B"):** Every step must specify **exactly one** recommended slash command—never write `/goal or /boost`. Choose deterministically:
   * Use `/goal` for deterministic, well-bounded file mutations with a fast static `POST_GATE` check.
   * Use `/boost` for complex multi-file cross-subsystem wiring, deep verification, or multi-perspective validation.
4. **Mandatory Complete Argon Prompt Block per Step:** Never summarize steps as prose or bullet lists without their full Argon prompt block. Every step must include a fenced block with all six sections:
   * `[STEP_ID]`: Unique atomic identifier and title (e.g., `STEP 3A: ...`).
   * `[SCOPE_LOCK]`: Exact `ALLOWED_FILES` (Read/Write), `READ_ONLY_REFS`, and `FORBIDDEN_SCOPE`.
   * `[PRE_GATE]`: Exact shell command verifying upstream prerequisites.
   * `[MUTATION_SPEC]`: Numbered, deterministic technical specification of changes per file.
   * `[POST_GATE]`: Exact shell command that must exit `0` to prove completion.
   * `[HALT_INVARIANT]`: Explicit instruction to stop immediately after `[POST_GATE]` passes.

## 4. Communication & Context Window Discipline
* **Zero Filler:** Do not emit exclamations, conversational pleasantries, or expressive commentary.
* **Concise Technical Reporting:** Keep status updates and gate reports minimal, factual, and directly tied to file mutations, command exit codes, or blocking defects.
* **Relevant Detail Only:** Provide extended detail only when diagnosing failures or explaining non-obvious technical constraints.
