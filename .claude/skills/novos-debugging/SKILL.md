---
name: novos-debugging
description: Load when diagnosing a failed NovOS build, kernel panic, boot hang, or systemd failure — reading build/serial/dmesg logs in logs/, or when asked to "troubleshoot", "locate the cause", or "review" a failure.
---

# NovOS Problem Shaping & Iterative Debugging (ReAct Loops)

Ported from `.agents/skills/Skills.md` §3.3 (AGENTS.md §3.3).

## Inputs: user-supplied logs
- The user runs host builds and VM tests and supplies results in `logs/` (e.g. `logs/qemu-artifacts/`). Read from there.
- Never re-run `sudo ./scripts/build-novos.sh` or VM tests the user already ran. If a fresh run is needed, output the exact command and halt.
- "Troubleshoot / locate the cause / review" = diagnosis only: zero file edits, no builds.

## Log & Panic Parsing
- On a build crash or VM kernel panic (e.g. `VFS: Unable to mount root fs`, missing symbol errors, systemd dependency cycles), inspect captured `dmesg` output and serial traces.
- Correlate panic call stacks against kernel symbol maps or missing driver requirements to isolate the precise defect (e.g. a driver omitted from the initramfs, an incorrect root partition UUID).
- Draft a minimal configuration diff targeting only the affected subsystem, and recompile only that subsystem; report the fix with `file:line`.

## State Management & Checkpointing
- OS builds are compute-intensive; discrete checkpoint directories (e.g. `build/base-rootfs.tar.gz`, `build/kernel-out/`) may speed iterative inner-loop investigation so a bootloader or ISO-packing mistake does not force a full kernel recompile.
- **Final verification is always fresh, Stages 1→6.** Checkpoints and partial resumes never count as verification (AGENTS.md §1).

## Context Window Protection
- Build logs reach hundreds of thousands of lines and cause premature context compaction (~135k tokens) and context dilution.
- Redirect verbose compile/packaging output to log files (`make -j$(nproc) > compile.log 2>&1`).
- Surface only relevant error windows and stack traces with targeted `grep`, `tail`, `grep -n -B5 -A20`, or a custom log-parser script. Never `cat` a full build log.

## Reporting
- Communicate in direct, non-expressive, factual statements: root cause, evidence (log path:line), proposed minimal fix. No exclamations or filler.
