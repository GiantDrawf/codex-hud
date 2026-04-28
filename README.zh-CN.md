# Codex HUD

[English](./README.md) | 简体中文

Codex HUD 是一个本地终端用量面板，用来查看 Codex CLI 订阅限额的剩余额度。

它的交互形式参考了 `claude-hud`，但实现方式不同：当前版本不会修改 Codex CLI，也不会请求官网接口，只读取本机 Codex CLI 已经写入的本地 telemetry 文件。

实时面板使用 [Ink](https://github.com/vadimdemedes/ink) 渲染。读取本地 telemetry 的逻辑仍然由 Python 后端负责，并通过 `codex_hud.py --once --json` 输出快照。

## 展示内容

HUD 只展示两类订阅限额：

- `5 小时使用限额`：Codex telemetry 中的 primary rate-limit window。
- `每周使用限额`：Codex telemetry 中的 secondary rate-limit window。

实时面板同时显示“已用百分比”和“剩余百分比”；单行状态栏保持只显示剩余量的紧凑格式。

示例：

```text
Codex HUD  Usage Remaining
Updated 2026-04-24 15:08:31  |  Source 2026-04-24 15:08:31

┌────────────────────────────────────┐  ┌────────────────────────────────────┐
│ 5 小时使用限额                     │  │ 每周使用限额                       │
│ 滚动窗口                           │  │ 订阅周期                           │
│                                    │  │                                    │
│ 已用：22%                          │  │ 已用：17%                          │
│ 剩余：78%                          │  │ 剩余：83%                          │
│ [■■■■■■■■■■■■■■■■■·····]           │  │ [■■■■■■■■■■■■■■■■■■····]           │
│ 重置时间：19:34                    │  │ 重置时间：2026年4月29日 23:53      │
└────────────────────────────────────┘  └────────────────────────────────────┘
```

终端宽度足够时，两张卡片会并排展示；宽度不足时会自动降级为上下两行。

## 使用方式

实时查看：

```bash
codex-hud
```

实时模式会启动 Ink TUI。按 `q`、`Esc` 或 `Ctrl-C` 退出。

如果没有配置 alias，也可以直接运行：

```bash
~/plugins/codex-hud/scripts/codex-hud
```

只输出一次：

```bash
codex-hud --once
```

输出 JSON：

```bash
codex-hud --once --json
```

输出单行状态栏格式：

```bash
codex-hud --status-line --once --no-clear
```

当源 telemetry 超过 2 分钟没有更新时，单行状态栏会带上 `stale` 标记。

## 安装 alias

推荐在 `~/.zshrc` 或 `~/.bashrc` 中添加：

```bash
alias codex-hud="$HOME/plugins/codex-hud/scripts/codex-hud"
```

当前 shell 立即生效：

```bash
source ~/.zshrc
```

如果通过 zip/tar 安装后丢失了可执行位，执行：

```bash
chmod +x ~/plugins/codex-hud/scripts/codex-hud ~/plugins/codex-hud/scripts/codex_hud.py
```

## 数据来源

Codex HUD 读取以下本地文件：

- `~/.codex/sessions/**/rollout-*.jsonl`
- `~/.codex/logs_2.sqlite`
- `~/.codex/state_5.sqlite`

主要数据来自本地 `codex.rate_limits` 和 `token_count.rate_limits` telemetry 事件。`state_5.sqlite` 只用于在必要时定位最新的 rollout 文件。

## 实时性说明

这个 HUD 不是直接轮询官网 analytics 页面。

它显示的是本机任意 Codex CLI 会话最近一次写入本地 telemetry 的账号级限额快照。因此：

- 本机没有 Codex CLI 会话产生新的模型响应时，HUD 可能不会变化。
- 官网 analytics 页面可能比本地 HUD 更早看到服务端最新统计。
- 如果你在网页版、其他设备或其他 Codex 账号中消耗额度，本地 HUD 通常要等本机 Codex CLI 下次收到 telemetry 后才会同步。
- 实时模式会保留最后一次有效数据，避免临时读取失败时清空界面。
- 如果最后一次源数据超过 2 分钟没有更新，标题区域会标记为 stale。

`Updated` 是 HUD 本身渲染的时间，`Source` 是本地 Codex telemetry 快照的时间。如果本机没有运行 Codex CLI，`Updated` 会继续变化，但 `Source` 会停在最后一次本地 telemetry；此时百分比是历史本地快照，不是服务端实时余额。

## 安全性

Codex HUD 不读取或上传 API key、cookie、authorization header 等账号凭据。

它只读取 Codex CLI 本地 telemetry 文件，并在本机终端渲染结果；不会发起网络请求。

运行时 npm 依赖（`ink` 和 `react`）只用于终端渲染。

## 限制

当前官方 Codex CLI 没有公开与 Claude Code `statusLine` 等价的插件接口，所以这个项目默认以独立终端窗口方式运行。

如果使用第三方 patched Codex，并支持 `status_line_command`，可以将命令配置为：

```toml
[tui]
status_line_command = "/path/to/codex-hud --status-line --once --no-clear"
```

未打补丁的官方 Codex CLI 会忽略该配置。
