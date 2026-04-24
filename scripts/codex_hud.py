#!/usr/bin/env python3
"""Live local usage HUD for Codex CLI."""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import signal
import shutil
import sqlite3
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CODEX_HOME = Path.home() / ".codex"
READ_CHUNK_BYTES = 2_000_000
MAX_TOKEN_COUNT_SCAN_BYTES = 20_000_000
STALE_AFTER_SECONDS = 120


@dataclass
class RateWindow:
    used_percent: float | None = None
    window_minutes: int | None = None
    resets_at: int | None = None


@dataclass
class Snapshot:
    primary: RateWindow
    secondary: RateWindow
    plan_type: str | None
    limit_id: str | None
    limit_reached: str | None
    updated_at: dt.datetime
    source_updated_at: dt.datetime | None
    available: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex CLI live usage HUD")
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    parser.add_argument("--session", type=Path, help="Optional rollout JSONL session file to read limits from")
    parser.add_argument("--interval", type=float, default=1.0, help="Refresh interval in seconds")
    parser.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a terminal HUD")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument("--status-line", action="store_true", help="Print compact one-line status output")
    parser.add_argument("--tmux-line", action="store_true", help="Deprecated alias for --status-line")
    parser.add_argument("--no-clear", action="store_true", help="Compatibility flag for status-line integrations")
    return parser.parse_args()


def newest_session(codex_home: Path) -> Path | None:
    pattern = str(codex_home / "sessions" / "**" / "rollout-*.jsonl")
    paths = [Path(path) for path in glob.glob(pattern, recursive=True)]
    existing_paths = [path for path in paths if path.exists()]
    if existing_paths:
        return max(existing_paths, key=lambda path: path.stat().st_mtime)
    return newest_session_from_state(codex_home)


def newest_session_from_state(codex_home: Path) -> Path | None:
    db_path = codex_home / "state_5.sqlite"
    if not db_path.exists():
        return None
    query = """
        SELECT rollout_path
        FROM threads
        WHERE rollout_path IS NOT NULL AND rollout_path != ''
        ORDER BY updated_at DESC
        LIMIT 20
    """
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.2) as conn:
            rows = conn.execute(query).fetchall()
    except sqlite3.Error:
        return None
    for (rollout_path,) in rows:
        path = Path(rollout_path).expanduser()
        if path.exists():
            return path
    return None


def rate_window_from_dict(data: dict[str, Any] | None) -> RateWindow:
    data = data or {}
    used = data.get("used_percent")
    return RateWindow(
        used_percent=float(used) if used is not None else None,
        window_minutes=int(data["window_minutes"]) if data.get("window_minutes") is not None else None,
        resets_at=int(data["resets_at"]) if data.get("resets_at") is not None else None,
    )


def read_latest_token_count(session_path: Path | None) -> dict[str, Any] | None:
    if not session_path or not session_path.exists():
        return None
    try:
        with session_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            scanned = 0
            while scanned < min(size, MAX_TOKEN_COUNT_SCAN_BYTES):
                read_size = min(READ_CHUNK_BYTES, size - scanned)
                offset = max(0, size - scanned - read_size)
                handle.seek(offset)
                chunk = handle.read(read_size).decode("utf-8", errors="replace")
                token_count = parse_latest_token_count_chunk(chunk, session_path)
                if token_count:
                    return token_count
                scanned += read_size
    except OSError:
        return None
    return None


def parse_latest_token_count_chunk(chunk: str, session_path: Path) -> dict[str, Any] | None:
    for line in reversed(chunk.splitlines()):
        if '"type":"token_count"' not in line and '"type": "token_count"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") or {}
        if payload.get("type") == "token_count":
            payload["_codex_hud_source_updated_at"] = event_timestamp(event) or file_mtime(session_path)
            return payload
    return None


def event_timestamp(event: dict[str, Any]) -> dt.datetime | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone()


def file_mtime(path: Path) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return None


def read_latest_token_count_anywhere(codex_home: Path, session_path: Path | None = None) -> dict[str, Any] | None:
    if session_path:
        return read_latest_token_count(session_path)

    pattern = str(codex_home / "sessions" / "**" / "rollout-*.jsonl")
    paths = [Path(path) for path in glob.glob(pattern, recursive=True)]
    existing_paths = [path for path in paths if path.exists()]
    existing_paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    for path in existing_paths[:20]:
        token_count = read_latest_token_count(path)
        if token_count and token_count.get("rate_limits"):
            return token_count
    state_path = newest_session_from_state(codex_home)
    return read_latest_token_count(state_path)


def build_snapshot(args: argparse.Namespace, previous: Snapshot | None = None) -> Snapshot:
    codex_home = args.codex_home.expanduser()
    session_path = args.session.expanduser() if args.session else None
    token_count = read_latest_token_count_anywhere(codex_home, session_path)
    limits = (token_count or {}).get("rate_limits") or {}
    if not limits and previous:
        return Snapshot(
            primary=previous.primary,
            secondary=previous.secondary,
            plan_type=previous.plan_type,
            limit_id=previous.limit_id,
            limit_reached=previous.limit_reached,
            updated_at=dt.datetime.now(),
            source_updated_at=previous.source_updated_at,
            available=False,
        )
    return Snapshot(
        primary=rate_window_from_dict(limits.get("primary")),
        secondary=rate_window_from_dict(limits.get("secondary")),
        plan_type=limits.get("plan_type"),
        limit_id=limits.get("limit_id"),
        limit_reached=limits.get("rate_limit_reached_type"),
        updated_at=dt.datetime.now(),
        source_updated_at=token_count.get("_codex_hud_source_updated_at") if limits else None,
        available=bool(limits),
    )


def bar(value: float | None, width: int = 18) -> str:
    if value is None:
        return "[" + "-" * width + "]"
    filled = max(0, min(width, round(width * value / 100)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def colorize(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    codes = {"dim": "2", "green": "32", "yellow": "33", "red": "31", "cyan": "36", "bold": "1"}
    code = codes.get(color)
    if not code:
        return text
    return f"\033[{code}m{text}\033[0m"


def render(snapshot: Snapshot, use_color: bool) -> str:
    width = shutil.get_terminal_size((88, 24)).columns
    lines = []
    title = "Codex HUD"
    updated = snapshot.updated_at.strftime("%Y-%m-%d %H:%M:%S")
    lines.append(colorize(f"{title}  Usage Remaining", "bold", use_color))
    if snapshot.source_updated_at:
        source_text = snapshot.source_updated_at.strftime("%Y-%m-%d %H:%M:%S")
        stale_seconds = max(0, int((snapshot.updated_at - snapshot.source_updated_at.replace(tzinfo=None)).total_seconds()))
        if stale_seconds > STALE_AFTER_SECONDS:
            stale_text = format_duration(stale_seconds)
            lines.append(colorize(f"Updated {updated}  |  Source {source_text}  |  stale for {stale_text}", "yellow", use_color))
        else:
            lines.append(colorize(f"Updated {updated}  |  Source {source_text}", "dim", use_color))
    else:
        lines.append(colorize(f"Updated {updated}  |  waiting for Codex limit telemetry", "yellow", use_color))

    lines.append("")
    cards = [
        limit_card("5 小时使用限额", "滚动窗口", snapshot.primary, weekly=False, use_color=use_color),
        limit_card("每周使用限额", "订阅周期", snapshot.secondary, weekly=True, use_color=use_color),
    ]
    cards_width = max(display_width(strip_ansi(line)) for card in cards for line in card)
    side_by_side = width >= cards_width * 2 + 2
    lines.extend(render_cards(cards, side_by_side=side_by_side))
    lines.append("")
    plan = snapshot.plan_type or "-"
    limit = snapshot.limit_id or "-"
    reached = snapshot.limit_reached or "no"
    lines.append(colorize(f"Plan: {plan} | limit: {limit} | reached: {reached}", "dim", use_color))

    return "\n".join(lines)


def render_status_line(snapshot: Snapshot) -> str:
    primary_remaining = remaining_text(snapshot.primary)
    secondary_remaining = remaining_text(snapshot.secondary)
    primary_reset = reset_datetime_text(snapshot.primary.resets_at, include_date=reset_is_not_today(snapshot.primary.resets_at))
    secondary_reset = reset_datetime_text(snapshot.secondary.resets_at, include_date=True)
    return (
        f"HUD • 5h {primary_remaining} left reset {primary_reset} "
        f"• weekly {secondary_remaining} left reset {secondary_reset}"
    )


def remaining_text(window: RateWindow) -> str:
    if window.used_percent is None:
        return "?"
    remaining = max(0.0, min(100.0, 100.0 - window.used_percent))
    return f"{remaining:.0f}%"


def limit_card(label: str, subtitle: str, window: RateWindow, weekly: bool, use_color: bool) -> list[str]:
    card_width = 38
    inner_width = card_width - 4
    if window.used_percent is None:
        percent_text = "未知"
        bar_text = bar(None, 22)
        reset = "-"
        color = "yellow"
    else:
        remaining = max(0.0, min(100.0, 100.0 - window.used_percent))
        percent_text = f"{remaining:.0f}%"
        bar_text = quota_bar(remaining, 22)
        reset = reset_datetime_text(window.resets_at, weekly)
        color = remaining_color(remaining)
    usage_text = "剩余额度"
    raw_lines = [
        f"┌{'─' * (card_width - 2)}┐",
        framed_line(label, inner_width),
        framed_line(subtitle, inner_width),
        framed_line("", inner_width),
        framed_line(f"{percent_text} {usage_text}", inner_width),
        framed_line(bar_text, inner_width),
        framed_line(f"重置时间：{reset}", inner_width),
        f"└{'─' * (card_width - 2)}┘",
    ]
    colored = []
    for index, line in enumerate(raw_lines):
        if index == 1:
            colored.append(colorize(line, "bold", use_color))
        elif index == 4:
            colored.append(colorize(line, color, use_color))
        else:
            colored.append(line)
    return colored


def render_cards(cards: list[list[str]], side_by_side: bool) -> list[str]:
    if not side_by_side:
        return cards[0] + [""] + cards[1]
    return [f"{left}  {right}" for left, right in zip(cards[0], cards[1])]


def framed_line(text: str, width: int) -> str:
    return f"│ {pad_display(text, width)} │"


def pad_display(text: str, width: int) -> str:
    text = strip_ansi(text)
    display = display_width(text)
    if display >= width:
        return truncate_display(text, width)
    return text + " " * (width - display)


def truncate_display(text: str, width: int) -> str:
    result = []
    used = 0
    for char in text:
        char_width = display_width(char)
        if used + char_width > width:
            break
        result.append(char)
        used += char_width
    return "".join(result) + " " * max(0, width - used)


def display_width(text: str) -> int:
    text = strip_ansi(text)
    total = 0
    for char in text:
        codepoint = ord(char)
        if unicodedata.combining(char) or unicodedata.category(char) in {"Cf", "Cc"}:
            continue
        if codepoint >= 0x1F000:
            total += 2
            continue
        total += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return total


def strip_ansi(text: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", text)


def quota_bar(remaining: float, width: int) -> str:
    filled = max(0, min(width, round(width * remaining / 100)))
    return "[" + "■" * filled + "·" * (width - filled) + "]"


def remaining_color(remaining: float) -> str:
    if remaining >= 30:
        return "green"
    if remaining >= 10:
        return "yellow"
    return "red"


def reset_datetime_text(ts: int | None, include_date: bool) -> str:
    if not ts:
        return "-"
    value = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone()
    if include_date:
        return f"{value.year}年{value.month}月{value.day}日 {value:%H:%M}"
    return value.strftime("%H:%M")


def reset_is_not_today(ts: int | None) -> bool:
    if not ts:
        return False
    value = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone()
    return value.date() != dt.datetime.now().astimezone().date()


def format_duration(seconds: int) -> str:
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, rem_minutes = divmod(minutes, 60)
    return f"{hours}h {rem_minutes}m"


def snapshot_to_json(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "primary": {
            **snapshot.primary.__dict__,
            "remaining_percent": None
            if snapshot.primary.used_percent is None
            else max(0.0, min(100.0, 100.0 - snapshot.primary.used_percent)),
        },
        "secondary": {
            **snapshot.secondary.__dict__,
            "remaining_percent": None
            if snapshot.secondary.used_percent is None
            else max(0.0, min(100.0, 100.0 - snapshot.secondary.used_percent)),
        },
        "plan_type": snapshot.plan_type,
        "limit_id": snapshot.limit_id,
        "limit_reached": snapshot.limit_reached,
        "available": snapshot.available,
        "updated_at": snapshot.updated_at.isoformat(),
        "source_updated_at": snapshot.source_updated_at.isoformat() if snapshot.source_updated_at else None,
    }


def main() -> int:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    args = parse_args()
    use_color = not args.no_color and sys.stdout.isatty()
    previous_snapshot = None
    while True:
        snapshot = build_snapshot(args, previous_snapshot)
        if snapshot.primary.used_percent is not None or snapshot.secondary.used_percent is not None:
            previous_snapshot = snapshot
        if args.json:
            print(json.dumps(snapshot_to_json(snapshot), ensure_ascii=False, indent=2))
        elif args.status_line or args.tmux_line:
            print(render_status_line(snapshot))
        else:
            if not args.once:
                print("\033[2J\033[H", end="")
            print(render(snapshot, use_color))
        if args.once:
            return 0
        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
