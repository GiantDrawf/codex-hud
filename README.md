# Codex HUD

English | [简体中文](./README.zh-CN.md)

A local terminal HUD for Codex CLI subscription-limit remaining usage, inspired by `claude-hud`.

Codex currently exposes useful usage data in local files rather than a Claude-style statusline API. This plugin reads the latest local `token_count.rate_limits` event from:

- `~/.codex/sessions/**/rollout-*.jsonl` for native `token_count` and `rate_limits` events.
- `~/.codex/state_5.sqlite` only as a fallback to locate the latest rollout file.

## Usage

Run once:

```bash
python3 ~/plugins/codex-hud/scripts/codex_hud.py --once
```

Watch live:

```bash
python3 ~/plugins/codex-hud/scripts/codex_hud.py
```

Use a specific session JSONL:

```bash
python3 ~/plugins/codex-hud/scripts/codex_hud.py --session ~/.codex/sessions/2026/04/24/rollout-xxx.jsonl
```

Install a shell shortcut:

```bash
alias codex-hud='python3 ~/plugins/codex-hud/scripts/codex_hud.py'
```

## Notes

- The HUD shows remaining quota, not used quota.
- `5 小时使用限额` maps to Codex's primary rate-limit window.
- `每周使用限额` maps to Codex's secondary rate-limit window.
- If a refresh temporarily cannot read local Codex telemetry, watch mode keeps showing the last valid limit snapshot.
- Token data is local telemetry from the Codex CLI; no network requests are made.
- The watch mode refreshes once per second by default and exits with `Ctrl-C`.
