---
name: codex-hud
description: Use when the user wants to view, explain, or troubleshoot remaining Codex CLI subscription limits with the local Codex HUD plugin.
---

# Codex HUD

This plugin provides a local terminal dashboard for remaining Codex subscription limits.

## Commands

- One-time snapshot:
  `~/plugins/codex-hud/scripts/codex-hud --once`
- Live HUD:
  `~/plugins/codex-hud/scripts/codex-hud`
- Compact status line:
  `~/plugins/codex-hud/scripts/codex-hud --status-line --once --no-clear`
- JSON output:
  `~/plugins/codex-hud/scripts/codex-hud --once --json`

## What It Reads

- `~/.codex/sessions/**/rollout-*.jsonl`: native `token_count.rate_limits` events.
- `~/.codex/state_5.sqlite`: fallback source for locating the latest rollout file.

## Field Meanings

- `5 小时使用限额`: remaining percentage for Codex's primary rate-limit window.
- `每周使用限额`: remaining percentage for Codex's secondary rolling limit window.
- `重置时间`: local reset time reported by Codex telemetry.

## Limitations

Codex does not currently expose the same statusline integration that Claude Code does. This HUD runs as a separate terminal watcher and uses local Codex telemetry files.
