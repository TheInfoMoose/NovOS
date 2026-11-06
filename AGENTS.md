# NovOS — Agent Governance Pointer

NovOS is a Debian sid OS/ISO build (`scripts/build-novos.sh`, Stages 1–6).

Claude Code governance lives in [`CLAUDE.md`](CLAUDE.md) and `.claude/` (hooks, settings, skills). The old `.agents/` tree is retired.

Hard stops (details in `CLAUDE.md` §1–§6):
- Never run `sudo`/root commands or destructive disk/boot/firmware operations; output the command and halt.
- Fresh Stage 1–6 builds only; never `--from-stage`. The user runs host builds and VM tests; logs are in `logs/`.
- Edit only the active step's `ALLOWED_FILES`; PRE_GATE → mutate → POST_GATE; one step per turn, then halt.
- Never edit `.nebulous/golden-snapshot.toml`, `approvals.toml`, `manifest.toml`.
- No suppressions, faked packages, or dropped/downgraded entries without user approval.

Where each former rule now lives: [`CLAUDE.md` → Parity Table](CLAUDE.md#parity-table).
