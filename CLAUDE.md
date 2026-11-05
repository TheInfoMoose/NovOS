@AGENTS.md

## Claude Code notes
- `AGENTS.md` above is authoritative (fresh Stage 1–6 builds only, no sudo, step scope locks, Argon plan format). `.agents/Locks.md` and `.agents/rules/` hold the detail.
- Antigravity-specific sandbox/slash-command (`/goal`, `/boost`) wording maps here to: stay inside the step's `ALLOWED_FILES`, one step per turn, then halt and report the POST_GATE result.
- Never edit `.nebulous/golden-snapshot.toml`, `approvals.toml`, or `manifest.toml`.
- The user runs `sudo ./scripts/build-novos.sh` and VM tests; read results from `logs/`.
