# Codex HUD

English | [简体中文](./README.zh-CN.md)

Codex HUD is a local terminal dashboard for remaining Codex CLI subscription limits.

It is inspired by `claude-hud`, but it does not patch Codex CLI and does not call the ChatGPT analytics API. It reads local telemetry files written by Codex CLI.

## What It Shows

Codex HUD focuses on two subscription limits:

- `5 小时使用限额`: Codex telemetry's primary rate-limit window.
- `每周使用限额`: Codex telemetry's secondary rolling limit window.

It shows remaining percentage, not used percentage.

Example:

```text
Codex HUD  Usage Remaining
Updated 2026-04-24 15:08:31  |  Source 2026-04-24 15:08:31

┌────────────────────────────────────┐  ┌────────────────────────────────────┐
│ 5 小时使用限额                     │  │ 每周使用限额                       │
│ 滚动窗口                           │  │ 订阅周期                           │
│                                    │  │                                    │
│ 78% 剩余额度                       │  │ 83% 剩余额度                       │
│ [■■■■■■■■■■■■■■■■■·····]           │  │ [■■■■■■■■■■■■■■■■■■····]           │
│ 重置时间：19:34                    │  │ 重置时间：2026年4月29日 23:53      │
└────────────────────────────────────┘  └────────────────────────────────────┘
```

Cards render side by side when the terminal is wide enough, and fall back to stacked layout on narrow terminals.

## Usage

Watch live:

```bash
codex-hud
```

If the alias is not installed, run the wrapper directly:

```bash
~/plugins/codex-hud/scripts/codex-hud
```

Run once:

```bash
codex-hud --once
```

Print JSON:

```bash
codex-hud --once --json
```

Print compact status-line output:

```bash
codex-hud --status-line --once --no-clear
```

## Install Alias

Add this to `~/.zshrc` or `~/.bashrc`:

```bash
alias codex-hud="$HOME/plugins/codex-hud/scripts/codex-hud"
```

Apply it in the current shell:

```bash
source ~/.zshrc
```

If the executable bit is lost after installing from a zip/tar archive, restore it:

```bash
chmod +x ~/plugins/codex-hud/scripts/codex-hud ~/plugins/codex-hud/scripts/codex_hud.py
```

## Data Sources

Codex HUD reads:

- `~/.codex/sessions/**/rollout-*.jsonl`
- `~/.codex/state_5.sqlite`

The primary data comes from `token_count.rate_limits` events in rollout JSONL files. `state_5.sqlite` is used only as a fallback to locate the latest rollout file.

## Freshness

This HUD does not poll the official analytics page.

It shows the latest limit snapshot that Codex CLI wrote to local telemetry. As a result:

- If the current Codex CLI session has not produced a new model response, the HUD may not change.
- The official analytics page may show server-side updates earlier than this local HUD.
- Usage from the web app, another device, or another Codex session may appear only after this Codex CLI receives new telemetry.
- Watch mode keeps the last valid snapshot if a refresh temporarily cannot read telemetry.
- If the last source snapshot is older than 2 minutes, the header marks it as stale.

## Safety

Codex HUD does not read or upload API keys, cookies, authorization headers, or other account credentials.

It only reads local Codex CLI telemetry files and renders results in your terminal. It makes no network requests.

## Limitations

Official Codex CLI does not currently expose a plugin API equivalent to Claude Code's `statusLine`, so this project runs as a standalone terminal HUD by default.

If you use a third-party patched Codex that supports `status_line_command`, you can configure:

```toml
[tui]
status_line_command = "/path/to/codex-hud --status-line --once --no-clear"
```

Unpatched official Codex CLI ignores this setting.
