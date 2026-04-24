---
name: codex-hud
description: Use when the user wants to view, explain, or troubleshoot remaining Codex CLI subscription limits with the local Codex HUD plugin.
---

# Codex HUD

This plugin provides a local terminal dashboard for remaining Codex subscription limits.

## Commands

- One-time snapshot:
  `python3 ~/plugins/codex-hud/scripts/codex_hud.py --once`
- Live HUD:
  `python3 ~/plugins/codex-hud/scripts/codex_hud.py`
- Compact status line:
  `python3 ~/plugins/codex-hud/scripts/codex_hud.py --status-line --once --no-clear`
- JSON output:
  `python3 ~/plugins/codex-hud/scripts/codex_hud.py --once --json`

## What It Reads

- `~/.codex/sessions/**/rollout-*.jsonl`: native `token_count.rate_limits` events.
- `~/.codex/state_5.sqlite`: fallback source for locating the latest rollout file.

## Field Meanings

- `5 小时使用限额`: remaining percentage for Codex's primary rate-limit window.
- `每周使用限额`: remaining percentage for Codex's secondary rolling limit window.
- `重置时间`: local reset time reported by Codex telemetry.

## Limitations

Codex does not currently expose the same statusline integration that Claude Code does. This HUD runs as a separate terminal watcher and uses local Codex telemetry files.
