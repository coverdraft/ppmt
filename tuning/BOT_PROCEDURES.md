# PPMT Tuning Bot — Operating Procedures

This file documents how the tuning bot (and any future agent) collaborates with this repo. It is the source of truth for "how do I do another round?".

## Repo Access

- **Repo**: https://github.com/coverdraft/ppmt
- **Bot identity**: configured locally via `~/.git-credentials` (credential.helper=store)
- **Token**: stored at `~/.git-credentials` with `chmod 600` (only readable by the bot user). NEVER commit this file.
- **Identity**: `Z User <z@container>` (set via `git config --global user.{name,email}`)

If the token expires or is revoked:
1. User generates a new PAT at https://github.com/settings/tokens (scope: `repo`)
2. User pastes it to the bot
3. Bot updates `~/.git-credentials` (overwrite, do not append)
4. Bot tests with `git ls-remote origin HEAD`

## Directory Layout

```
tuning/
├── README.md                          ← index + workflow (rarely changes)
├── ROADMAP.md                         ← backlog → in-progress → done/rolled-back
├── TRACEABILITY.md                    ← comparison table + per-round sections
├── v9_snapshot_analysis.md            ← diagnostic of snapshot v9
├── v9_to_v10_tuning_patch.yaml        ← explicit parameter changes
├── v10_snapshot_analysis.md           ← (added when v10 snapshot arrives)
├── v10_to_v11_tuning_patch.yaml       ← (added based on v10 analysis)
├── ...
└── BOT_PROCEDURES.md                  ← this file
```

## Standard Round Workflow

### Step 1 — Receive snapshot

User uploads a snapshot export (typically a `.txt` file containing a Markdown header + JSON payload). The snapshot format is `ppmt-export-vN` with `_exported_at` ISO timestamp.

### Step 2 — Analyze

1. Parse the snapshot JSON (engine health, capital, strategies, trades, patterns, ML, signals, risk, tokens, loop_health).
2. Identify issues (PF<1, win_rate<40%, idle strategies, ML stuck in BOOTSTRAP, etc.).
3. Cross-check Monte Carlo verdict against actual metrics (flag false positives).
4. Write `v{N}_snapshot_analysis.md` with the diagnostic.

### Step 3 — Propose patch

1. Copy `v{N-1}_to_v{N}_tuning_patch.yaml` as a template.
2. Update `baseline_metrics` with vN numbers.
3. Add `CHANGE_*` blocks. Each change MUST have:
   - `id`: unique identifier
   - `path`: dotted config path (informational)
   - `before`: current value
   - `after`: new value
   - `rationale`: 2-4 sentences explaining the why
   - `rollback_to`: usually == `before`
   - `expected_effect`: qualitative + quantitative range
4. Document `lift_conditions` for any temporary caps.
5. Document `rollback_procedure` at the bottom.

### Step 4 — Commit & push

```bash
cd /home/z/my-project/repos/ppmt
git pull --rebase origin main                # always pull first
git add tuning/ worklog.md
git commit -m "tuning: v{N}→v{N+1} patch — <short summary>"
git push origin main
```

### Step 5 — Update worklog

Append to `worklog.md` (do NOT overwrite) a section with:
```markdown
---
## YYYY-MM-DD — Tuning Round vN → vN+1

**Agent**: tuning-bot
**Trigger**: <one line>
**Snapshot**: ppmt-export-vN
<...summary, patch list, next steps...>
```

### Step 6 — Update TRACEABILITY.md

Add a row to the comparison table at the top with the new round, and a per-round section below with the metrics template (to be filled post-run).

### Step 7 — Update ROADMAP.md

- Move the round from `In Progress` → `Done` or `Rolled Back` (after results arrive).
- Pull the next card from `Backlog` into `In Progress`.

## Safety Rules

1. **Never commit the GitHub PAT**. It lives in `~/.git-credentials` only.
2. **Never push directly to `main` without local commit** — always commit first, then push.
3. **Always `git pull --rebase` before push** — avoids conflicts with parallel work.
4. **Never modify code outside `tuning/`** without explicit user request. The bot's scope is tuning patches, not source code.
5. **Every patch must be reversible** — `rollback_to` field is mandatory.
6. **No patch is marked `APPLIED` until the user confirms it was loaded into the runtime**. The bot's commit only documents the intent.
7. **If the user asks for an emergency rollback**: set `status: ROLLED_BACK` in the patch YAML, restore `rollback_to` values in the runtime, document in `TRACEABILITY.md`.

## File Naming Conventions

- Snapshot analysis: `v{N}_snapshot_analysis.md` (e.g. `v9_snapshot_analysis.md`)
- Patch: `v{N}_to_v{N+1}_tuning_patch.yaml`
- One snapshot → one analysis → one patch (1:1:1)

## Commit Message Conventions

Format: `tuning: v{N}→v{N+1} patch — <short summary>`

Examples:
- `tuning: v9→v10 patch — TP/SL rebalance, Strategy C cap, confidence threshold raise`
- `tuning: v10→v11 patch — Strategy A revival based on rejection logs`
- `tuning: v11→v12 patch — exposure lift after ML exits BOOTSTRAP`

For non-patch commits (roadmap updates, procedure docs):
- `tuning: update ROADMAP — v10 marked DONE, v11 in progress`
- `tuning: add BOT_PROCEDURES.md`

## When the User Uploads a New Snapshot

1. Read the snapshot file from `/home/z/my-project/upload/`.
2. Identify `_version` in the JSON (e.g. `ppmt-export-v10`).
3. Run the standard round workflow (Steps 2-7 above).
4. In the chat response: brief summary of analysis + the patch pushed + next steps for the user.

## Emergency Contacts

- If the runtime engine shows critical issues (MaxDD > 5%, daily_loss > 4%, L10+ streak): recommend immediate manual rollback using the `rollback_to` values in the latest patch YAML.
- If the PAT is leaked: user must revoke at https://github.com/settings/tokens and generate a new one.
