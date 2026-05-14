# Codex HUD

English | [简体中文](./README.zh-CN.md)

Codex HUD is a local terminal dashboard for remaining Codex CLI subscription limits.

It is inspired by `claude-hud`, but it does not patch Codex CLI and does not call the ChatGPT analytics API. It reads local telemetry files written by Codex CLI.

Live mode is rendered with [Ink](https://github.com/vadimdemedes/ink), a React renderer for command-line apps. The local telemetry reader remains in Python and is exposed through `codex_hud.py --once --json`.

## What It Shows

Codex HUD focuses on two subscription limits:

- `5 小时使用限额`: Codex telemetry's primary rate-limit window.
- `每周使用限额`: Codex telemetry's secondary rolling limit window.

It shows both used and remaining percentages. Live mode also summarizes local
token usage for today, yesterday, the current weekly limit window, the past 7
days, and the past 30 days, based on `token_count` events in local rollout
files. Status-line mode keeps the compact remaining-only format.

Example:

```text
Codex HUD | updated 13:12:35 | source 13:12:28

┌────────────────────────────────────┐ ┌────────────────────────────────────┐
│ 5 小时使用限额                     │ │ 每周使用限额                       │
│ 滚动窗口                           │ │ 订阅周期                           │
│                                    │ │                                    │
│ 已用：49%                          │ │ 已用：13%                          │
│ 剩余：51%                          │ │ 剩余：87%                          │
│ [■■■■■■■■■■■■■■■■················] │ │ [■■■■■■■■■■■■■■■■■■■■■■■■■■■■····] │
│ 重置时间：14:29                    │ │ 重置时间：2026年5月20日 10:22      │
└────────────────────────────────────┘ └────────────────────────────────────┘

Token 汇总
                                   input              output               total                cost
今日                          14,806,394              48,232          14,854,626              $11.52
昨日                           8,692,362              35,687           8,728,049               $8.06
本周限额                      23,434,729              83,693          23,518,422              $19.41
近 7 天                       72,304,663             225,938          72,530,601              $56.78
近 30 天                     296,161,983             782,514         296,944,497             $215.95
```

The two limit cards render side by side and shrink to fit narrower terminals.

The cost column is an estimate in USD. It uses known OpenAI API token prices for
supported models and separates cached input from regular input.

## Usage

Watch live:

```bash
codex-hud
```

Live mode opens the Ink TUI. It watches `scripts/ink_hud.mjs` and
`scripts/codex_hud.py`, then restarts the UI automatically when either file
changes. Press `q`, `Esc`, or `Ctrl-C` to exit.

Disable source watching when needed:

```bash
codex-hud --no-watch
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

When the source telemetry is older than 2 minutes, status-line output includes a `stale` marker.

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
- `~/.codex/logs_2.sqlite`
- `~/.codex/state_5.sqlite`

The primary data comes from local `codex.rate_limits` and `token_count.rate_limits` telemetry events. `state_5.sqlite` is used only as a fallback to locate the latest rollout file.

Token summaries are computed from per-session `total_token_usage` deltas, so
duplicate telemetry events are not counted twice. Cost estimates use the
published OpenAI API per-token prices for known models and account for cached
input separately. Tokens from models without a known price are included in token
totals but excluded from the cost column.

## Freshness

This HUD does not poll the official analytics page.

It shows the latest account-level limit snapshot that any local Codex CLI session wrote to telemetry. As a result:

- If no local Codex CLI session has produced a new model response, the HUD may not change.
- The official analytics page may show server-side updates earlier than this local HUD.
- Usage from the web app, another device, or another Codex account may appear only after a local Codex CLI session receives new telemetry.
- Live mode keeps the last valid snapshot if a refresh temporarily cannot read telemetry.
- If the last source snapshot is older than 2 minutes, the header marks it as stale.

`Updated` is the time the HUD rendered. `Source` is the timestamp of the local Codex telemetry snapshot. If Codex CLI is not running locally, `Updated` can keep changing while `Source` stays fixed; in that case the percentages are historical local telemetry, not a live server-side balance.

## Safety

Codex HUD does not read or upload API keys, cookies, authorization headers, or other account credentials.

It only reads local Codex CLI telemetry files and renders results in your terminal. It makes no network requests.

The runtime npm dependencies (`ink` and `react`) are used only for terminal rendering.

## Limitations

Official Codex CLI does not currently expose a plugin API equivalent to Claude Code's `statusLine`, so this project runs as a standalone terminal HUD by default.

If you use a third-party patched Codex that supports `status_line_command`, you can configure:

```toml
[tui]
status_line_command = "/path/to/codex-hud --status-line --once --no-clear"
```

Unpatched official Codex CLI ignores this setting.
