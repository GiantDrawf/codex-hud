---
name: codex-hud
description: Use when the user wants to view, explain, or troubleshoot remaining Codex CLI subscription limits with the local Codex HUD plugin.
---

# Codex HUD

This plugin provides a local terminal dashboard for remaining Codex subscription limits.

## Commands

- Live Ink TUI:
  `~/plugins/codex-hud/scripts/codex-hud`
- One-time snapshot:
  `~/plugins/codex-hud/scripts/codex-hud --once`
- Compact status line:
  `~/plugins/codex-hud/scripts/codex-hud --status-line --once --no-clear`
- JSON output:
  `~/plugins/codex-hud/scripts/codex-hud --once --json`

## What It Reads

- `~/.codex/sessions/**/rollout-*.jsonl`: native `token_count.rate_limits` events.
- `~/.codex/logs_2.sqlite`: recent local `codex.rate_limits` websocket events.
- `~/.codex/state_5.sqlite`: fallback source for locating the latest rollout file.

Live rendering is handled by Ink. Data collection remains in the Python backend exposed via `codex_hud.py --once --json`.

## Field Meanings

- `5 小时使用限额`: Codex's primary rate-limit window, shown as used and remaining percentages in the Ink TUI.
- `每周使用限额`: Codex's secondary rolling limit window, shown as used and remaining percentages in the Ink TUI.
- `重置时间`: local reset time reported by Codex telemetry.

## Limitations

Codex does not currently expose the same statusline integration that Claude Code does. This HUD runs as a separate Ink TUI and uses local Codex telemetry files.
