# Codex HUD

English | [简体中文](./README.zh-CN.md)

Codex HUD is a local terminal dashboard for remaining Codex CLI subscription limits.

It is inspired by `claude-hud`, but it does not patch Codex CLI. For account
limits it reads the same ChatGPT account usage endpoint used by Codex CLI
`/status`, then falls back to local telemetry files written by Codex CLI.

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

- `https://chatgpt.com/backend-api/codex/usage`
- `~/.codex/sessions/**/rollout-*.jsonl`
- `~/.codex/logs_2.sqlite`
- `~/.codex/state_5.sqlite`

The primary limit data comes from the ChatGPT account usage endpoint. The
request is an authenticated GET with no request body, uses the Codex CLI
`~/.codex/auth.json` access token, and disables redirects so the Authorization
header is not forwarded away from `chatgpt.com`.

Local `codex.rate_limits` and `token_count.rate_limits` telemetry events are
fallback sources. `state_5.sqlite` is used only as a fallback to locate the
latest rollout file.

Token summaries are computed from per-session `total_token_usage` deltas, so
duplicate telemetry events are not counted twice. Cost estimates use the
published OpenAI API per-token prices for known models and account for cached
input separately. Tokens from models without a known price are included in token
totals but excluded from the cost column.

## Freshness

This HUD does not poll the official analytics page.

It asks the same account usage endpoint as Codex CLI `/status`, then falls back
to the latest account-level limit snapshot that any local Codex CLI session
wrote to telemetry. As a result:

- If the account usage endpoint is unavailable, the HUD may show stale local telemetry.
- Live mode keeps the last valid snapshot if a refresh temporarily cannot read telemetry.
- If the last source snapshot is older than 2 minutes, the header marks it as stale.

`Updated` is the time the HUD rendered. `Source` is the timestamp of the account
usage response or the fallback local Codex telemetry snapshot.

## Safety

Codex HUD reads the Codex CLI ChatGPT access token from `~/.codex/auth.json`
only to authenticate the official account usage request to `chatgpt.com`.

Security review recorded on 2026-05-18: the usage request sends no local project
files, session transcripts, rollout contents, logs, cookies, API keys, or token
values in the request body. The token is placed only in the Authorization header
for `https://chatgpt.com/backend-api/codex/usage`, is not printed, is not cached
by Codex HUD, and is not forwarded through redirects.

The runtime npm dependencies (`ink` and `react`) are used only for terminal rendering.

## Limitations

Official Codex CLI does not currently expose a plugin API equivalent to Claude Code's `statusLine`, so this project runs as a standalone terminal HUD by default.

If you use a third-party patched Codex that supports `status_line_command`, you can configure:

```toml
[tui]
status_line_command = "/path/to/codex-hud --status-line --once --no-clear"
```

Unpatched official Codex CLI ignores this setting.
