#!/usr/bin/env python3
"""Local usage snapshot backend for Codex HUD."""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CODEX_HOME = Path.home() / ".codex"
READ_CHUNK_BYTES = 2_000_000
MAX_TOKEN_COUNT_SCAN_BYTES = 20_000_000
STALE_AFTER_SECONDS = 120
CODEX_USAGE_ENDPOINT = "https://chatgpt.com/backend-api/codex/usage"
CODEX_USAGE_TIMEOUT_SECONDS = 8
CARD_WIDTH = 38
CARD_GAP = 1
MIN_CARD_WIDTH = 24
TOKEN_SUMMARY_DAYS = 30
TOKEN_PRICE_USD_PER_1M = {
    "gpt-5.5": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
}


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class RateWindow:
    used_percent: float | None = None
    window_minutes: int | None = None
    resets_at: int | None = None


@dataclass
class Snapshot:
    primary: RateWindow
    secondary: RateWindow
    info: dict[str, Any] | None
    token_summary: dict[str, Any] | None
    plan_type: str | None
    limit_id: str | None
    limit_reached: str | None
    updated_at: dt.datetime
    source_updated_at: dt.datetime | None
    available: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex CLI usage snapshot backend")
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    parser.add_argument("--session", type=Path, help="Optional rollout JSONL session file to read limits from")
    parser.add_argument("--interval", type=float, default=1.0, help="Compatibility flag; ignored by the snapshot backend")
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
    used = used_percent_from_dict(data)
    return RateWindow(
        used_percent=used,
        window_minutes=int(data["window_minutes"]) if data.get("window_minutes") is not None else None,
        resets_at=int(data["resets_at"]) if data.get("resets_at") is not None else None,
    )


def used_percent_from_dict(data: dict[str, Any]) -> float | None:
    used = data.get("used_percent")
    if used is not None:
        return float(used)
    remaining = data.get("remaining_percent")
    if remaining is not None:
        return 100.0 - float(remaining)
    return None


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
    latest_payload: dict[str, Any] | None = None
    fallback_limits: dict[str, Any] | None = None
    for line in reversed(chunk.splitlines()):
        if '"type":"token_count"' not in line and '"type": "token_count"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") or {}
        if payload.get("type") != "token_count":
            continue
        if latest_payload is None:
            latest_payload = payload
            latest_payload["_codex_hud_source_updated_at"] = event_timestamp(event) or file_mtime(session_path)
            if rate_limits_have_windows(latest_payload.get("rate_limits") or {}):
                return latest_payload
            continue
        limits = payload.get("rate_limits") or {}
        if rate_limits_have_windows(limits):
            fallback_limits = limits
            break
    if latest_payload is None:
        return None
    if fallback_limits:
        latest_payload["rate_limits"] = merge_rate_limits(latest_payload.get("rate_limits") or {}, fallback_limits)
    return latest_payload


def rate_limits_have_windows(limits: dict[str, Any]) -> bool:
    return window_has_usage(limits.get("primary")) and window_has_usage(limits.get("secondary"))


def window_has_usage(window: dict[str, Any] | None) -> bool:
    if not isinstance(window, dict):
        return False
    return window.get("used_percent") is not None or window.get("remaining_percent") is not None


def merge_rate_limits(current: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(fallback)
    merged.update({key: value for key, value in current.items() if value is not None})
    for key in ("primary", "secondary"):
        merged[key] = merge_window(current.get(key), fallback.get(key))
    return merged


def merge_window(current: dict[str, Any] | None, fallback: dict[str, Any] | None) -> dict[str, Any]:
    current = current or {}
    fallback = fallback or {}
    merged = dict(fallback)
    merged.update({key: value for key, value in current.items() if value is not None})
    return merged


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

    candidates = []
    account_usage = read_latest_rate_limits_from_account(codex_home)
    if account_usage and account_usage.get("rate_limits"):
        candidates.append(account_usage)

    log_token_count = read_latest_rate_limits_from_logs(codex_home)
    if log_token_count and log_token_count.get("rate_limits"):
        candidates.append(log_token_count)

    pattern = str(codex_home / "sessions" / "**" / "rollout-*.jsonl")
    paths = [Path(path) for path in glob.glob(pattern, recursive=True)]
    existing_paths = [path for path in paths if path.exists()]
    existing_paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    for path in existing_paths[:20]:
        token_count = read_latest_token_count(path)
        if token_count and token_count.get("rate_limits"):
            candidates.append(token_count)
            break

    state_path = newest_session_from_state(codex_home)
    token_count = read_latest_token_count(state_path)
    if token_count and token_count.get("rate_limits"):
        candidates.append(token_count)

    if not candidates:
        return None
    return max(candidates, key=token_count_source_ts)


def token_count_source_ts(token_count: dict[str, Any]) -> float:
    source = token_count.get("_codex_hud_source_updated_at")
    if isinstance(source, dt.datetime):
        return source.timestamp()
    return 0.0


def build_token_summary(
    codex_home: Path,
    latest_info: dict[str, Any] | None,
    weekly_window: RateWindow,
) -> dict[str, Any] | None:
    today = dt.datetime.now().astimezone().date()
    yesterday = today - dt.timedelta(days=1)
    start_date = today - dt.timedelta(days=TOKEN_SUMMARY_DAYS - 1)
    weekly_period = period_from_window(weekly_window)
    if weekly_period and weekly_period[0].date() < start_date:
        start_date = weekly_period[0].date()
    totals = {
        "today": empty_token_usage(),
        "yesterday": empty_token_usage(),
        "current_weekly_limit": empty_token_usage(),
        "last_7_days": empty_token_usage(),
        "last_30_days": empty_token_usage(),
    }
    scanned_files = 0
    event_count = 0

    pattern = str(codex_home / "sessions" / "**" / "rollout-*.jsonl")
    paths = [Path(path) for path in glob.glob(pattern, recursive=True)]
    for path in sorted(paths):
        try:
            if dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone().date() < start_date:
                continue
        except OSError:
            continue
        scanned_files += 1
        event_count += add_session_token_usage(path, start_date, today, yesterday, weekly_period, totals)

    context_window = None
    if latest_info:
        context_window = latest_info.get("model_context_window")
    if not event_count and context_window is None:
        return None
    return {
        "today": totals["today"],
        "yesterday": totals["yesterday"],
        "current_weekly_limit": totals["current_weekly_limit"],
        "last_7_days": totals["last_7_days"],
        "last_30_days": totals["last_30_days"],
        "current_weekly_limit_period": period_to_json(weekly_period),
        "plus_subscription_period": None,
        "context_window": context_window,
        "days": TOKEN_SUMMARY_DAYS,
        "scanned_files": scanned_files,
        "event_count": event_count,
    }


def empty_token_usage() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd_micros": 0,
        "unpriced_tokens": 0,
    }


def add_session_token_usage(
    path: Path,
    start_date: dt.date,
    today: dt.date,
    yesterday: dt.date,
    weekly_period: tuple[dt.datetime, dt.datetime] | None,
    totals: dict[str, dict[str, int]],
) -> int:
    previous: dict[str, int] | None = None
    current_model: str | None = None
    event_count = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                current_model = model_from_line(line) or current_model
                if '"type":"token_count"' not in line and '"type": "token_count"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload") or {}
                if payload.get("type") != "token_count":
                    continue
                info = payload.get("info") or {}
                usage = normalize_token_usage(info.get("total_token_usage"))
                if not usage:
                    continue
                timestamp = event_timestamp(event) or file_mtime(path)
                if not timestamp:
                    continue
                event_date = timestamp.date()
                delta = token_usage_delta(previous, usage)
                previous = usage
                if not delta or event_date < start_date or event_date > today:
                    continue
                add_cost_estimate(delta, current_model)
                add_token_usage(totals["last_30_days"], delta)
                if event_date >= today - dt.timedelta(days=6):
                    add_token_usage(totals["last_7_days"], delta)
                if timestamp_in_period(timestamp, weekly_period):
                    add_token_usage(totals["current_weekly_limit"], delta)
                if event_date == today:
                    add_token_usage(totals["today"], delta)
                elif event_date == yesterday:
                    add_token_usage(totals["yesterday"], delta)
                event_count += 1
    except OSError:
        return event_count
    return event_count


def period_from_window(window: RateWindow) -> tuple[dt.datetime, dt.datetime] | None:
    if not window.resets_at or not window.window_minutes:
        return None
    end = dt.datetime.fromtimestamp(window.resets_at, tz=dt.timezone.utc).astimezone()
    start = end - dt.timedelta(minutes=window.window_minutes)
    return start, end


def period_to_json(period: tuple[dt.datetime, dt.datetime] | None) -> dict[str, Any] | None:
    if not period:
        return None
    start, end = period
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def timestamp_in_period(timestamp: dt.datetime, period: tuple[dt.datetime, dt.datetime] | None) -> bool:
    if not period:
        return False
    start, end = period
    return start <= timestamp < end


def model_from_line(line: str) -> str | None:
    if '"model"' not in line:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    payload = event.get("payload") or {}
    model = payload.get("model")
    return model if isinstance(model, str) else None


def normalize_token_usage(data: Any) -> dict[str, int] | None:
    if not isinstance(data, dict):
        return None
    usage = {}
    for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"):
        value = data.get(key)
        try:
            usage[key] = int(value or 0)
        except (TypeError, ValueError):
            usage[key] = 0
    if usage["total_tokens"] <= 0 and usage["input_tokens"] <= 0 and usage["output_tokens"] <= 0:
        return None
    return usage


def token_usage_delta(previous: dict[str, int] | None, current: dict[str, int]) -> dict[str, int]:
    if previous is None:
        return dict(current)
    delta = {}
    for key, value in current.items():
        previous_value = previous.get(key, 0)
        delta[key] = value - previous_value if value >= previous_value else value
    if all(value == 0 for value in delta.values()):
        return {}
    return delta


def add_token_usage(total: dict[str, int], delta: dict[str, int]) -> None:
    for key, value in delta.items():
        total[key] = total.get(key, 0) + max(0, int(value))


def add_cost_estimate(delta: dict[str, int], model: str | None) -> None:
    price = TOKEN_PRICE_USD_PER_1M.get(model or "")
    if not price:
        delta["estimated_cost_usd_micros"] = 0
        delta["unpriced_tokens"] = delta.get("total_tokens", 0)
        return
    cached_input = max(0, delta.get("cached_input_tokens", 0))
    input_tokens = max(0, delta.get("input_tokens", 0))
    uncached_input = max(0, input_tokens - cached_input)
    output_tokens = max(0, delta.get("output_tokens", 0))
    cost = (
        uncached_input * price["input"]
        + cached_input * price["cached_input"]
        + output_tokens * price["output"]
    ) / 1_000_000
    delta["estimated_cost_usd_micros"] = round(cost * 1_000_000)
    delta["unpriced_tokens"] = 0


def read_latest_rate_limits_from_logs(codex_home: Path) -> dict[str, Any] | None:
    db_path = codex_home / "logs_2.sqlite"
    if not db_path.exists():
        return None

    query = """
        SELECT ts, target, feedback_log_body
        FROM logs
        WHERE ts >= ?
          AND (
            feedback_log_body LIKE '%websocket event: {"type":"codex.rate_limits"%'
            OR feedback_log_body LIKE 'Received message {"type":"codex.rate_limits"%'
          )
        ORDER BY ts DESC, ts_nanos DESC, id DESC
        LIMIT 50
    """
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.2) as conn:
            cutoff = int(time.time()) - 24 * 60 * 60
            rows = conn.execute(query, (cutoff,)).fetchall()
    except sqlite3.Error:
        return None

    for ts, _target, body in rows:
        event = extract_rate_limits_event(body or "")
        if not event:
            continue
        rate_limits = normalize_rate_limits_event(event)
        if not rate_limits:
            continue
        return {
            "type": "token_count",
            "rate_limits": rate_limits,
            "_codex_hud_source_updated_at": dt.datetime.fromtimestamp(ts).astimezone(),
        }
    return None


def read_latest_rate_limits_from_account(codex_home: Path) -> dict[str, Any] | None:
    token = read_codex_access_token(codex_home)
    if not token:
        return None
    request = urllib.request.Request(
        CODEX_USAGE_ENDPOINT,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "codex-hud",
        },
    )
    try:
        opener = urllib.request.build_opener(NoRedirectHandler)
        with opener.open(request, timeout=CODEX_USAGE_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    rate_limits = normalize_account_usage(data)
    if not rate_limits:
        return None
    return {
        "type": "token_count",
        "rate_limits": rate_limits,
        "_codex_hud_source_updated_at": dt.datetime.now().astimezone(),
    }


def read_codex_access_token(codex_home: Path) -> str | None:
    auth_path = codex_home / "auth.json"
    try:
        with auth_path.open("r", encoding="utf-8") as handle:
            auth = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    tokens = auth.get("tokens") if isinstance(auth, dict) else None
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    return token if isinstance(token, str) and token else None


def normalize_account_usage(data: dict[str, Any]) -> dict[str, Any] | None:
    rate_limit = data.get("rate_limit") if isinstance(data, dict) else None
    if not isinstance(rate_limit, dict):
        return None
    primary = normalize_account_window(rate_limit.get("primary_window"))
    secondary = normalize_account_window(rate_limit.get("secondary_window"))
    if not primary and not secondary:
        return None
    reached_type = data.get("rate_limit_reached_type") or limit_reached_type({"rate_limits": rate_limit})
    return {
        "primary": primary,
        "secondary": secondary,
        "plan_type": data.get("plan_type"),
        "limit_id": "codex",
        "rate_limit_reached_type": reached_type,
    }


def normalize_account_window(window: Any) -> dict[str, Any]:
    if not isinstance(window, dict):
        return {}
    reset_at = window.get("reset_at")
    if reset_at is None and window.get("reset_after_seconds") is not None:
        try:
            reset_at = int(time.time()) + int(window["reset_after_seconds"])
        except (TypeError, ValueError):
            reset_at = None
    window_seconds = window.get("limit_window_seconds")
    try:
        window_minutes = int(window_seconds) // 60 if window_seconds is not None else None
    except (TypeError, ValueError):
        window_minutes = None
    return {
        "used_percent": window.get("used_percent"),
        "remaining_percent": window.get("remaining_percent"),
        "window_minutes": window_minutes,
        "resets_at": reset_at,
    }


def extract_rate_limits_event(body: str) -> dict[str, Any] | None:
    prefixes = ('websocket event: {"type":"codex.rate_limits"', 'Received message {"type":"codex.rate_limits"')
    prefix_start = -1
    for prefix in prefixes:
        prefix_start = body.find(prefix)
        if prefix_start >= 0:
            break
    if prefix_start < 0:
        return None

    marker = '{"type":"codex.rate_limits"'
    start = body.find(marker, prefix_start)
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(body[start:], start=start):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(body[start : index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def normalize_rate_limits_event(event: dict[str, Any]) -> dict[str, Any] | None:
    raw_limits = event.get("rate_limits") or {}
    limit_reached = event.get("limit_reached") or raw_limits.get("limit_reached")
    allowed = raw_limits.get("allowed")
    if not raw_limits and not limit_reached and allowed is not False:
        return None

    def normalize_window(window: dict[str, Any] | None) -> dict[str, Any]:
        window = window or {}
        used = used_percent_from_dict(window)
        return {
            "used_percent": used,
            "window_minutes": window.get("window_minutes"),
            "resets_at": window.get("resets_at") if window.get("resets_at") is not None else window.get("reset_at"),
        }

    reached_type = limit_reached_type(event, raw_limits)
    return {
        "primary": normalize_window(raw_limits.get("primary")),
        "secondary": normalize_window(raw_limits.get("secondary")),
        "plan_type": event.get("plan_type"),
        "limit_id": event.get("limit_id") or "codex",
        "rate_limit_reached_type": reached_type,
    }


def limit_reached_type(data: dict[str, Any], raw_limits: dict[str, Any] | None = None) -> str | None:
    raw_limits = raw_limits or data.get("rate_limits") or {}
    reached = (
        data.get("rate_limit_reached_type")
        or data.get("limit_reached")
        or raw_limits.get("rate_limit_reached_type")
        or raw_limits.get("limit_reached")
    )
    if not reached:
        return infer_reached_window(raw_limits) if raw_limits.get("allowed") is False else None
    if isinstance(reached, str):
        return reached
    if isinstance(reached, dict):
        for key in ("type", "window", "limit_type", "rate_limit_type", "name", "id"):
            value = reached.get(key)
            if isinstance(value, str) and value:
                return value
    if reached is True:
        return infer_reached_window(raw_limits) or "primary"
    limit_id = data.get("limit_id")
    if isinstance(limit_id, str) and limit_id:
        return limit_id
    return "codex"


def infer_reached_window(raw_limits: dict[str, Any]) -> str | None:
    windows = []
    for name in ("primary", "secondary"):
        window = raw_limits.get(name)
        if not isinstance(window, dict):
            continue
        used = used_percent_from_dict(window)
        if used is not None:
            windows.append((name, used))
    if not windows:
        return None
    return max(windows, key=lambda item: item[1])[0]


def build_snapshot(args: argparse.Namespace, previous: Snapshot | None = None) -> Snapshot:
    codex_home = args.codex_home.expanduser()
    session_path = args.session.expanduser() if args.session else None
    token_count = read_latest_token_count_anywhere(codex_home, session_path)
    limits = (token_count or {}).get("rate_limits") or {}
    if not limits and previous:
        return Snapshot(
            primary=previous.primary,
            secondary=previous.secondary,
            info=previous.info,
            token_summary=previous.token_summary,
            plan_type=previous.plan_type,
            limit_id=previous.limit_id,
            limit_reached=previous.limit_reached,
            updated_at=dt.datetime.now(),
            source_updated_at=previous.source_updated_at,
            available=False,
        )
    primary = rate_window_from_dict(limits.get("primary"))
    secondary = rate_window_from_dict(limits.get("secondary"))
    limit_reached = limits.get("rate_limit_reached_type")
    primary, secondary = apply_limit_reached(primary, secondary, limit_reached, limits.get("limit_id"))
    return Snapshot(
        primary=primary,
        secondary=secondary,
        info=token_count.get("info") if isinstance(token_count.get("info"), dict) else None,
        token_summary=build_token_summary(
            codex_home,
            token_count.get("info") if isinstance(token_count.get("info"), dict) else None,
            secondary,
        ),
        plan_type=limits.get("plan_type"),
        limit_id=limits.get("limit_id"),
        limit_reached=limit_reached,
        updated_at=dt.datetime.now(),
        source_updated_at=token_count.get("_codex_hud_source_updated_at") if limits else None,
        available=bool(limits),
    )


def apply_limit_reached(
    primary: RateWindow,
    secondary: RateWindow,
    limit_reached: str | None,
    limit_id: str | None,
) -> tuple[RateWindow, RateWindow]:
    reached_text = " ".join(part.lower() for part in (limit_reached, limit_id) if isinstance(part, str))
    if not reached_text:
        return primary, secondary
    if any(marker in reached_text for marker in ("primary", "5h", "5 hour", "5-hour", "window")):
        primary.used_percent = 100.0
    if any(marker in reached_text for marker in ("secondary", "weekly", "week", "7d")):
        secondary.used_percent = 100.0
    if primary.used_percent is None and secondary.used_percent is None:
        if primary.resets_at and not secondary.resets_at:
            primary.used_percent = 100.0
        elif secondary.resets_at and not primary.resets_at:
            secondary.used_percent = 100.0
    return primary, secondary


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
    updated = snapshot.updated_at.strftime("%H:%M:%S")
    if snapshot.source_updated_at:
        source_text = snapshot.source_updated_at.strftime("%H:%M:%S")
        stale_seconds = source_stale_seconds(snapshot)
        if stale_seconds > STALE_AFTER_SECONDS:
            stale_text = format_duration(stale_seconds)
            header = f"{title} | updated {updated} | source {source_text} | stale {stale_text}"
            lines.append(colorize(header, "yellow", use_color))
        else:
            header = f"{title} | updated {updated} | source {source_text}"
            lines.append(colorize(header, "bold", use_color))
    else:
        header = f"{title} | updated {updated} | waiting for Codex limit telemetry"
        lines.append(colorize(header, "yellow", use_color))

    lines.append("")
    card_width = card_width_for_terminal(width)
    cards = [
        limit_card("5 小时使用限额", "滚动窗口", snapshot.primary, weekly=False, use_color=use_color, card_width=card_width),
        limit_card("每周使用限额", "订阅周期", snapshot.secondary, weekly=True, use_color=use_color, card_width=card_width),
    ]
    lines.extend(render_cards(cards))
    token_lines = token_summary_lines(snapshot.token_summary, width)
    if token_lines:
        lines.append("")
        lines.extend(token_lines)
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
    freshness = status_freshness_text(snapshot)
    return (
        f"HUD{freshness} • 5h {primary_remaining} left reset {primary_reset} "
        f"• weekly {secondary_remaining} left reset {secondary_reset}"
    )


def remaining_text(window: RateWindow) -> str:
    if window.used_percent is None:
        return "?"
    remaining = max(0.0, min(100.0, 100.0 - window.used_percent))
    return f"{remaining:.0f}%"


def token_summary_lines(summary: dict[str, Any] | None, width: int) -> list[str]:
    if not summary:
        return []
    today = summary.get("today") or {}
    yesterday = summary.get("yesterday") or {}
    current_weekly_limit = summary.get("current_weekly_limit") or {}
    last_7_days = summary.get("last_7_days") or {}
    last_30_days = summary.get("last_30_days") or {}
    lines = []
    lines.append("Token 汇总")
    rows = [
        ("今日", today),
        ("昨日", yesterday),
        ("本周限额", current_weekly_limit),
        ("近 7 天", last_7_days),
        ("近 30 天", last_30_days),
    ]
    widths = even_column_widths(width, 5, gap=2)
    lines.append(token_summary_header(widths))
    for label, usage in rows:
        lines.append(token_summary_line(label, usage, widths))
    return lines


def even_column_widths(total_width: int, count: int, gap: int) -> list[int]:
    usable_width = max(count, total_width - gap * (count - 1))
    base, remainder = divmod(usable_width, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def token_summary_header(widths: list[int]) -> str:
    return (
        f"{fit_display('', widths[0], 'left')}  "
        f"{fit_display('input', widths[1], 'right')}  "
        f"{fit_display('output', widths[2], 'right')}  "
        f"{fit_display('total', widths[3], 'right')}  "
        f"{fit_display('cost', widths[4], 'right')}"
    )


def token_summary_line(label: str, usage: dict[str, Any], widths: list[int]) -> str:
    return (
        f"{fit_display(label, widths[0], 'left')}  "
        f"{fit_display(format_int(usage.get('input_tokens')), widths[1], 'right')}  "
        f"{fit_display(format_int(usage.get('output_tokens')), widths[2], 'right')}  "
        f"{fit_display(format_int(usage.get('total_tokens')), widths[3], 'right')}  "
        f"{fit_display(format_usd(usage.get('estimated_cost_usd_micros')), widths[4], 'right')}"
    )


def fit_display(text: str, width: int, align: str) -> str:
    used = display_width(text)
    if used > width:
        return truncate_display(text, width)
    padding = " " * max(0, width - used)
    return padding + text if align == "right" else text + padding


def format_usd(value: Any) -> str:
    try:
        micros = int(value or 0)
    except (TypeError, ValueError):
        return "-"
    return f"${micros / 1_000_000:.2f}"


def format_int(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def card_width_for_terminal(columns: int) -> int:
    available = (max(1, columns) - CARD_GAP) // 2
    return max(MIN_CARD_WIDTH, min(CARD_WIDTH, available))


def reset_text_for_card(timestamp: int | None, include_date: bool, width: int) -> str:
    reset = reset_datetime_text(timestamp, include_date)
    if display_width(f"重置时间：{reset}") <= width:
        return reset
    return reset_datetime_text(timestamp, include_date, compact=True)


def limit_card(label: str, subtitle: str, window: RateWindow, weekly: bool, use_color: bool, card_width: int) -> list[str]:
    inner_width = card_width - 4
    if window.used_percent is None:
        usage_line = "已用：未知"
        remaining_line = "剩余：未知"
        bar_text = bar(None, max(10, inner_width - 2))
        reset = "-"
        color = "yellow"
    else:
        used = max(0.0, min(100.0, window.used_percent))
        remaining = max(0.0, min(100.0, 100.0 - window.used_percent))
        usage_line = f"已用：{used:.0f}%"
        remaining_line = f"剩余：{remaining:.0f}%"
        bar_text = quota_bar(remaining, max(10, inner_width - 2))
        reset = reset_text_for_card(window.resets_at, weekly, inner_width)
        color = remaining_color(remaining)
    raw_lines = [
        f"┌{'─' * (card_width - 2)}┐",
        framed_line(label, inner_width),
        framed_line(subtitle, inner_width),
        framed_line("", inner_width),
        framed_line(usage_line, inner_width),
        framed_line(remaining_line, inner_width),
        framed_line(bar_text, inner_width),
        framed_line(f"重置时间：{reset}", inner_width),
        f"└{'─' * (card_width - 2)}┘",
    ]
    colored = []
    for index, line in enumerate(raw_lines):
        if index == 1:
            colored.append(colorize(line, "bold", use_color))
        elif index in {4, 5}:
            colored.append(colorize(line, color, use_color))
        else:
            colored.append(line)
    return colored


def render_cards(cards: list[list[str]]) -> list[str]:
    gap = " " * CARD_GAP
    return [f"{left}{gap}{right}" for left, right in zip(cards[0], cards[1])]


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


def reset_datetime_text(ts: int | None, include_date: bool, compact: bool = False) -> str:
    if not ts:
        return "-"
    value = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone()
    if include_date:
        if compact:
            return f"{value.month}/{value.day} {value:%H:%M}"
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


def source_stale_seconds(snapshot: Snapshot) -> int | None:
    if not snapshot.source_updated_at:
        return None
    return max(0, int((snapshot.updated_at - snapshot.source_updated_at.replace(tzinfo=None)).total_seconds()))


def is_stale(snapshot: Snapshot) -> bool:
    stale_seconds = source_stale_seconds(snapshot)
    return stale_seconds is None or stale_seconds > STALE_AFTER_SECONDS


def status_freshness_text(snapshot: Snapshot) -> str:
    stale_seconds = source_stale_seconds(snapshot)
    if stale_seconds is None:
        return " stale ?"
    if stale_seconds <= STALE_AFTER_SECONDS:
        return ""
    return f" stale {format_duration(stale_seconds)}"


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
        "info": snapshot.info,
        "token_summary": snapshot.token_summary,
        "plan_type": snapshot.plan_type,
        "limit_id": snapshot.limit_id,
        "limit_reached": snapshot.limit_reached,
        "available": snapshot.available,
        "is_stale": is_stale(snapshot),
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "source_stale_seconds": source_stale_seconds(snapshot),
        "updated_at": snapshot.updated_at.isoformat(),
        "source_updated_at": snapshot.source_updated_at.isoformat() if snapshot.source_updated_at else None,
    }


def main() -> int:
    args = parse_args()
    use_color = not args.no_color and sys.stdout.isatty()
    snapshot = build_snapshot(args)
    if args.json:
        print(json.dumps(snapshot_to_json(snapshot), ensure_ascii=False, indent=2))
    elif args.status_line or args.tmux_line:
        print(render_status_line(snapshot))
    else:
        print(render(snapshot, use_color))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
