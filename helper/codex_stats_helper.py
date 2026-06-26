#!/usr/bin/env python
"""Local Codex usage aggregator for the Codex Stats GNOME extension."""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_LOG_ROOT = Path.home() / ".codex" / "sessions"
DEFAULT_CACHE_FILE = Path.home() / ".cache" / "codex-stats" / "cache.json"
DEFAULT_LIVE_LOG_DB = Path.home() / ".codex" / "logs_2.sqlite"
DEFAULT_CODEX_BIN = ""
DEFAULT_LIMIT_WINDOWS = {"primary": 300, "secondary": 10080}

DEFAULT_CLAUDE_LOG_ROOT = Path.home() / ".claude" / "projects"
DEFAULT_CLAUDE_LIMITS_FILE = Path.home() / ".cache" / "codex-stats" / "claude-limits.json"
DEFAULT_CLAUDE_HUD_CACHE_DIR = Path.home() / ".claude" / "hud" / "cache"
CLAUDE_LIMIT_WINDOWS = {"primary": 300, "secondary": 10080}
CLAUDE_LIMIT_KEYS = {"primary": "five_hour", "secondary": "seven_day"}
CLAUDE_ONLINE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_ONLINE_BETA = "oauth-2025-04-20"
CLAUDE_ONLINE_KEYS = {
    "primary": "five_hour",
    "secondary": "seven_day",
    "sonnet_weekly": "seven_day_sonnet",
    "opus_weekly": "seven_day_opus",
}
CLAUDE_ONLINE_WINDOWS = {"primary": 300, "secondary": 10080, "sonnet_weekly": 10080, "opus_weekly": 10080}
CLAUDE_ONLINE_TTL_SECONDS = 60
CLAUDE_ONLINE_BACKOFF_SECONDS = 300
CLAUDE_ONLINE_ERROR_TTL_SECONDS = 45
CLAUDE_ONLINE_TIMEOUT_SECONDS = 10
DEFAULT_CLAUDE_ONLINE_CACHE_FILE = Path.home() / ".cache" / "codex-stats" / "claude-online.json"
LIVE_LIMIT_EVENT_TYPE = "codex.rate_limits"
LIVE_LIMIT_ROW_LIMIT = 100
LIVE_LIMIT_MAX_AGE_SECONDS = 24 * 60 * 60
LIVE_LIMIT_NEWER_TOLERANCE_SECONDS = 2
ACCOUNT_LIMIT_TIMEOUT_SECONDS = 5.0
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class TokenEvent:
    ts: float
    total_tokens: int
    session_ts: float = 0.0
    primary_used: float | None = None
    primary_window: int | None = None
    primary_resets_at: float | None = None
    secondary_used: float | None = None
    secondary_window: int | None = None
    secondary_resets_at: float | None = None


@dataclass(frozen=True)
class LimitSnapshot:
    ts: float
    session_ts: float
    used_percent: float | None
    window_minutes: int | None
    resets_at: float | None
    source: str = "jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate local Codex token usage")
    parser.add_argument("--json", action="store_true", help="print JSON output")
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex", help="usage provider to aggregate")
    parser.add_argument("--log-root", default=None, help="session log directory (provider default when omitted)")
    parser.add_argument("--cache-file", default=None, help="cache JSON path (provider default when omitted)")
    parser.add_argument("--limits-file", default="", help="Claude statusLine rate-limit capture JSON path")
    parser.add_argument("--claude-online", action="store_true", help="fetch live Claude limits from the Anthropic usage API (opt-in network call)")
    parser.add_argument("--claude-credentials-file", default="", help="Claude OAuth credentials path for --claude-online")
    parser.add_argument("--claude-online-cache", default="", help="throttle cache path for --claude-online")
    parser.add_argument("--live-log-db", default=str(DEFAULT_LIVE_LOG_DB), help="Codex live log SQLite path")
    parser.add_argument("--codex-bin", default=DEFAULT_CODEX_BIN, help="Codex CLI path for realtime account limits")
    parser.add_argument("--no-cache", action="store_true", help="disable cache reads and writes")
    parser.add_argument("--no-live-limits", action="store_true", help="disable live rate-limit reads from Codex logs")
    parser.add_argument("--no-account-limits", action="store_true", help="disable realtime account rate-limit reads from Codex CLI")
    parser.add_argument("--now", default="", help="override current time as ISO-8601, for tests")
    return parser.parse_args()


def parse_datetime(value: str, local_tz: timezone) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(local_tz)


def parse_now(value: str) -> datetime:
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    if not value:
        return datetime.now(local_tz)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"Invalid --now value: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed


def iter_log_files(log_root: Path) -> list[Path]:
    if not log_root.exists():
        return []
    return sorted(path for path in log_root.rglob("*.jsonl") if path.is_file())


def parse_session_timestamp(path: Path, local_tz: timezone) -> float:
    prefix = "rollout-"
    stamp_length = len("2026-05-29T11-01-36")
    name = path.name
    if not name.startswith(prefix):
        return 0.0

    stamp = name[len(prefix) : len(prefix) + stamp_length]
    try:
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%S")
    except ValueError:
        return 0.0
    return parsed.replace(tzinfo=local_tz).timestamp()


def load_cache(cache_file: Path) -> dict[str, Any]:
    try:
        with cache_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "files": {}}
    if payload.get("schema_version") != SCHEMA_VERSION:
        return {"schema_version": SCHEMA_VERSION, "files": {}}
    if not isinstance(payload.get("files"), dict):
        return {"schema_version": SCHEMA_VERSION, "files": {}}
    return payload


def save_cache(cache_file: Path, cache: dict[str, Any]) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = cache_file.with_suffix(cache_file.suffix + ".tmp")
        with tmp_file.open("w", encoding="utf-8") as handle:
            json.dump(cache, handle, separators=(",", ":"))
        os.replace(tmp_file, cache_file)
    except OSError:
        pass


def extract_event(payload: dict[str, Any], local_tz: timezone, session_ts: float) -> TokenEvent | None:
    if payload.get("type") != "event_msg":
        return None
    event_payload = payload.get("payload")
    if not isinstance(event_payload, dict) or event_payload.get("type") != "token_count":
        return None

    timestamp = parse_datetime(str(payload.get("timestamp", "")), local_tz)
    if timestamp is None:
        return None

    info = event_payload.get("info") if isinstance(event_payload.get("info"), dict) else {}
    last_usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
    try:
        total_tokens = int(last_usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        total_tokens = 0

    rate_limits = event_payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        rate_limits = {}

    primary = rate_limits.get("primary") if isinstance(rate_limits.get("primary"), dict) else {}
    secondary = rate_limits.get("secondary") if isinstance(rate_limits.get("secondary"), dict) else {}

    return TokenEvent(
        ts=timestamp.timestamp(),
        total_tokens=max(0, total_tokens),
        session_ts=session_ts,
        primary_used=number_or_none(primary.get("used_percent")),
        primary_window=int_or_none(primary.get("window_minutes")),
        primary_resets_at=number_or_none(primary.get("resets_at")),
        secondary_used=number_or_none(secondary.get("used_percent")),
        secondary_window=int_or_none(secondary.get("window_minutes")),
        secondary_resets_at=number_or_none(secondary.get("resets_at")),
    )


def number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_file(path: Path, local_tz: timezone) -> tuple[list[TokenEvent], int]:
    events: list[TokenEvent] = []
    malformed = 0
    session_ts = parse_session_timestamp(path, local_tz)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(payload, dict):
                    continue
                event = extract_event(payload, local_tz, session_ts)
                if event is not None:
                    events.append(event)
    except OSError:
        malformed += 1
    return events, malformed


def collect_events(log_root: Path, cache_file: Path, use_cache: bool, local_tz: timezone) -> tuple[list[TokenEvent], dict[str, int]]:
    stats = {"files_scanned": 0, "files_parsed": 0, "malformed_lines": 0}
    files = iter_log_files(log_root)
    stats["files_scanned"] = len(files)

    cache = load_cache(cache_file) if use_cache else {"schema_version": SCHEMA_VERSION, "files": {}}
    cached_files = cache.setdefault("files", {})
    seen_paths: set[str] = set()
    events: list[TokenEvent] = []

    for path in files:
        key = str(path)
        seen_paths.add(key)
        try:
            stat = path.stat()
        except OSError:
            continue

        cached = cached_files.get(key) if isinstance(cached_files.get(key), dict) else None
        if (
            use_cache
            and cached
            and cached.get("mtime_ns") == stat.st_mtime_ns
            and cached.get("size") == stat.st_size
        ):
            events.extend(TokenEvent(**event) for event in cached.get("events", []))
            stats["malformed_lines"] += int(cached.get("malformed_lines", 0) or 0)
            continue

        parsed_events, malformed = parse_file(path, local_tz)
        events.extend(parsed_events)
        stats["files_parsed"] += 1
        stats["malformed_lines"] += malformed
        cached_files[key] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "malformed_lines": malformed,
            "events": [asdict(event) for event in parsed_events],
        }

    for key in list(cached_files.keys()):
        if key not in seen_paths:
            del cached_files[key]

    if use_cache:
        save_cache(cache_file, cache)

    return events, stats


def claude_usage_total(usage: dict[str, Any]) -> int:
    total = 0
    for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        value = number_or_none(usage.get(key))
        if value is not None:
            total += int(value)
    return max(0, total)


def claude_group_key(payload: dict[str, Any], message: dict[str, Any]) -> str | None:
    # One API response is written across several streaming/iteration rows that
    # all share (sessionId, requestId). requestId is absent on a few rows, so
    # fall back to message.id. The key is global across files so resumed/forked
    # sessions (same sessionId+requestId in a second file) are not double-counted.
    # Returns None when no identifier exists at all (never seen in real data); the
    # caller then keeps such a row as its own event rather than merging distinct
    # turns into an under-count.
    discriminator = payload.get("requestId")
    if discriminator is None:
        discriminator = message.get("id")
    if discriminator is None:
        return None
    return f"{payload.get('sessionId')}\x00{discriminator}"


def extract_claude_row(payload: dict[str, Any], local_tz: timezone) -> tuple[str, float, int] | None:
    if payload.get("type") != "assistant":
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    timestamp = parse_datetime(str(payload.get("timestamp", "")), local_tz)
    if timestamp is None:
        return None
    # Privacy: only the numeric usage fields and the dedup identifiers are read;
    # message.content / text is never touched.
    return claude_group_key(payload, message), timestamp.timestamp(), claude_usage_total(usage)


def parse_claude_file(path: Path, local_tz: timezone) -> tuple[list[list[Any]], int]:
    rows: list[list[Any]] = []
    malformed = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(payload, dict):
                    continue
                row = extract_claude_row(payload, local_tz)
                if row is not None:
                    rows.append([row[0], row[1], row[2]])
    except OSError:
        malformed += 1
    return rows, malformed


def collect_claude_events(log_root: Path, cache_file: Path, use_cache: bool, local_tz: timezone) -> tuple[list[TokenEvent], dict[str, int]]:
    stats = {"files_scanned": 0, "files_parsed": 0, "malformed_lines": 0}
    files = iter_log_files(log_root)
    stats["files_scanned"] = len(files)

    cache = load_cache(cache_file) if use_cache else {"schema_version": SCHEMA_VERSION, "files": {}}
    cached_files = cache.setdefault("files", {})
    seen_paths: set[str] = set()
    all_rows: list[list[Any]] = []

    for path in files:
        key = str(path)
        seen_paths.add(key)
        try:
            stat = path.stat()
        except OSError:
            continue

        cached = cached_files.get(key) if isinstance(cached_files.get(key), dict) else None
        if (
            use_cache
            and cached
            and cached.get("mtime_ns") == stat.st_mtime_ns
            and cached.get("size") == stat.st_size
            and isinstance(cached.get("rows"), list)
        ):
            all_rows.extend(cached["rows"])
            stats["malformed_lines"] += int(cached.get("malformed_lines", 0) or 0)
            continue

        rows, malformed = parse_claude_file(path, local_tz)
        all_rows.extend(rows)
        stats["files_parsed"] += 1
        stats["malformed_lines"] += malformed
        cached_files[key] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "malformed_lines": malformed,
            "rows": rows,
        }

    for key in list(cached_files.keys()):
        if key not in seen_paths:
            del cached_files[key]

    if use_cache:
        save_cache(cache_file, cache)

    # Global dedup per API response. last-wins == max total (the final streaming
    # row carries the complete usage); max is order-independent across files.
    best: dict[str, tuple[float, int]] = {}
    events: list[TokenEvent] = []
    for row in all_rows:
        group_key, ts, total = row[0], float(row[1]), int(row[2])
        if group_key is None:
            # No identifier to dedup on: keep it as its own event so distinct
            # unkeyable turns are never merged into an under-count.
            events.append(TokenEvent(ts=ts, total_tokens=total, session_ts=0.0))
            continue
        current = best.get(group_key)
        if current is None or total > current[1] or (total == current[1] and ts > current[0]):
            best[group_key] = (ts, total)

    events.extend(TokenEvent(ts=ts, total_tokens=total, session_ts=0.0) for ts, total in best.values())
    return events, stats


def claude_snapshots_from_rate_limits(rate_limits: Any, ts: float) -> dict[str, LimitSnapshot]:
    snapshots: dict[str, LimitSnapshot] = {}
    if not isinstance(rate_limits, dict):
        return snapshots
    for prefix, key in CLAUDE_LIMIT_KEYS.items():
        window_payload = rate_limits.get(key)
        if not isinstance(window_payload, dict):
            continue
        used = number_or_none(window_payload.get("used_percentage"))
        if used is None:
            continue
        resets_at = number_or_none(window_payload.get("resets_at"))
        snapshots[prefix] = LimitSnapshot(
            ts=ts,
            session_ts=0.0,
            used_percent=used,
            window_minutes=CLAUDE_LIMIT_WINDOWS[prefix],
            resets_at=resets_at,
            source="statusline",
        )
    return snapshots


def read_claude_capture_file(path: Path) -> tuple[Any, float] | None:
    # Returns (rate_limits, ts). ts comes from captured_at when present (the
    # wrapper writes it), else the file mtime (HUD cache fallback). A real ts is
    # mandatory: merge_live_limit_snapshot drops snapshots older than 24h.
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    rate_limits = data.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None
    ts = number_or_none(data.get("captured_at"))
    if ts is None:
        try:
            ts = path.stat().st_mtime
        except OSError:
            return None
    return rate_limits, ts


def iter_claude_hud_caches(hud_dir: Path) -> list[Path]:
    if not hud_dir.exists():
        return []
    files = [path for path in hud_dir.glob("stdin.*.json") if path.is_file()]

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(files, key=mtime, reverse=True)


def collect_claude_limit_snapshots(
    limits_file: Path | None,
    now: datetime,
    hud_dir: Path | None = None,
) -> tuple[dict[str, LimitSnapshot], dict[str, int]]:
    stats = {"claude_limit_snapshots": 0}

    # Primary source: the captured statusLine payload.
    if limits_file is not None:
        capture = read_claude_capture_file(limits_file)
        if capture is not None:
            snapshots = claude_snapshots_from_rate_limits(capture[0], capture[1])
            if snapshots:
                stats["claude_limit_snapshots"] = len(snapshots)
                return snapshots, stats

    # Fallback: newest HUD cache file that actually carries rate_limits.
    hud_dir = hud_dir if hud_dir is not None else DEFAULT_CLAUDE_HUD_CACHE_DIR
    for path in iter_claude_hud_caches(hud_dir):
        capture = read_claude_capture_file(path)
        if capture is None:
            continue
        snapshots = claude_snapshots_from_rate_limits(capture[0], capture[1])
        if snapshots:
            stats["claude_limit_snapshots"] = len(snapshots)
            return snapshots, stats

    return {}, stats


def claude_credentials_default() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    return base / ".credentials.json"


def read_claude_oauth_token(creds_file: Path) -> tuple[str, float | None] | None:
    # Reads the Claude OAuth access token for the online usage API. READ-ONLY:
    # the helper never writes this file (no token refresh / write-back), so it
    # can never corrupt Claude's auth. Returns (access_token, expires_at_ms) or
    # None. The token itself is never logged or emitted in the payload.
    try:
        with creds_file.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    creds = data.get("claudeAiOauth")
    if not isinstance(creds, dict):
        creds = data
    token = creds.get("accessToken")
    if not isinstance(token, str) or not token:
        return None
    return token, number_or_none(creds.get("expiresAt"))


def claude_online_snapshots_from_response(response: Any, ts: float, local_tz: timezone) -> dict[str, LimitSnapshot]:
    # Maps the /api/oauth/usage response (five_hour / seven_day / seven_day_sonnet
    # / seven_day_opus, each {utilization, resets_at: ISO-8601}) onto LimitSnapshots.
    snapshots: dict[str, LimitSnapshot] = {}
    if not isinstance(response, dict):
        return snapshots
    for prefix, key in CLAUDE_ONLINE_KEYS.items():
        window_payload = response.get(key)
        if not isinstance(window_payload, dict):
            continue
        used = number_or_none(window_payload.get("utilization"))
        if used is None:
            continue
        resets_at = None
        raw_reset = window_payload.get("resets_at")
        if isinstance(raw_reset, str):
            parsed = parse_datetime(raw_reset, local_tz)
            if parsed is not None:
                resets_at = parsed.timestamp()
        else:
            resets_at = number_or_none(raw_reset)
        # "Not started" sentinel: {utilization: 0, resets_at: null} means the
        # window is inactive — this is how the API hides unused per-model (Opus/
        # Sonnet) buckets before first use. Treat it as absent (no phantom gauge).
        if resets_at is None and used == 0:
            continue
        snapshots[prefix] = LimitSnapshot(
            ts=ts,
            session_ts=0.0,
            used_percent=used,
            window_minutes=CLAUDE_ONLINE_WINDOWS[prefix],
            resets_at=resets_at,
            source="online",
        )
    return snapshots


def project_claude_online_response(response: Any) -> dict[str, Any]:
    # Privacy whitelist: keep only the numeric usage windows we render, so the
    # throttle cache on disk never holds anything but utilization + reset time.
    projected: dict[str, Any] = {}
    if not isinstance(response, dict):
        return projected
    for key in CLAUDE_ONLINE_KEYS.values():
        window_payload = response.get(key)
        if not isinstance(window_payload, dict):
            continue
        entry: dict[str, Any] = {}
        used = number_or_none(window_payload.get("utilization"))
        if used is not None:
            entry["utilization"] = used
        raw_reset = window_payload.get("resets_at")
        if isinstance(raw_reset, (str, int, float)):
            entry["resets_at"] = raw_reset
        if entry:
            projected[key] = entry
    return projected


def claude_online_http_fetch(token: str) -> tuple[str, Any]:
    # Single HTTPS GET to the fixed Anthropic usage endpoint. Host is hardcoded
    # (no SSRF surface). Returns ("ok", data) | ("rate_limited", None) | ("error", None).
    request = urllib.request.Request(
        CLAUDE_ONLINE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": CLAUDE_ONLINE_BETA,
            "Content-Type": "application/json",
            "User-Agent": "codex-stats-gnome",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=CLAUDE_ONLINE_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return "error", None
            body = response.read().decode("utf-8")
        return "ok", json.loads(body)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            return "rate_limited", None
        return "error", None
    except (urllib.error.URLError, OSError, ValueError):
        return "error", None


def load_online_cache(cache_file: Path) -> dict[str, Any]:
    try:
        with cache_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_online_cache(cache_file: Path, payload: dict[str, Any]) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = cache_file.with_suffix(cache_file.suffix + ".tmp")
        with tmp_file.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        os.replace(tmp_file, cache_file)
    except OSError:
        pass


def collect_claude_online_snapshots(
    creds_file: Path,
    cache_file: Path,
    now: datetime,
    use_cache: bool,
    opener: Any = None,
) -> tuple[dict[str, LimitSnapshot], dict[str, int]]:
    stats = {"claude_online_requests": 0, "claude_online_snapshots": 0, "claude_online_status": ""}
    local_tz = now.tzinfo or timezone.utc
    now_ts = now.timestamp()
    fetch = opener or claude_online_http_fetch

    cache = load_online_cache(cache_file) if use_cache else {}
    cached_data = cache.get("data") if isinstance(cache.get("data"), dict) else None
    fetched_at = number_or_none(cache.get("fetched_at")) or 0.0
    backoff_until = number_or_none(cache.get("backoff_until")) or 0.0

    def served(status: str) -> tuple[dict[str, LimitSnapshot], dict[str, int]]:
        stats["claude_online_status"] = status
        if cached_data is not None:
            snaps = claude_online_snapshots_from_response(cached_data, fetched_at, local_tz)
            stats["claude_online_snapshots"] = len(snaps)
            return snaps, stats
        return {}, stats

    # Fresh cache within TTL: avoid hammering the endpoint across refreshes.
    if cached_data is not None and 0.0 <= now_ts - fetched_at < CLAUDE_ONLINE_TTL_SECONDS:
        return served("cache")

    # Honor an active 429 backoff window.
    if now_ts < backoff_until:
        return served("backoff")

    token_info = read_claude_oauth_token(creds_file)
    if token_info is None:
        return served("no-credentials")
    token, expires_at = token_info
    if expires_at is not None and expires_at <= now_ts * 1000:
        # Token expired; Claude Code refreshes it on next use. Never refresh here.
        return served("token-expired")

    stats["claude_online_requests"] = 1
    status, data = fetch(token)

    if status == "ok" and isinstance(data, dict):
        projected = project_claude_online_response(data)
        if use_cache:
            save_online_cache(cache_file, {"fetched_at": now_ts, "data": projected, "backoff_until": 0.0})
        snaps = claude_online_snapshots_from_response(projected, now_ts, local_tz)
        stats["claude_online_status"] = "ok"
        stats["claude_online_snapshots"] = len(snaps)
        return snaps, stats

    if status == "rate_limited":
        if use_cache:
            updated = dict(cache)
            updated["backoff_until"] = now_ts + CLAUDE_ONLINE_BACKOFF_SECONDS
            save_online_cache(cache_file, updated)
        return served("rate-limited")

    if use_cache:
        updated = dict(cache)
        updated["backoff_until"] = now_ts + CLAUDE_ONLINE_ERROR_TTL_SECONDS
        save_online_cache(cache_file, updated)
    return served("error")


def extract_balanced_json_object(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def iter_live_rate_limit_events(text: str) -> list[dict[str, Any]]:
    if LIVE_LIMIT_EVENT_TYPE not in text or "rate_limits" not in text:
        return []

    events: list[dict[str, Any]] = []
    marker_start = 0
    while True:
        marker_index = text.find(LIVE_LIMIT_EVENT_TYPE, marker_start)
        if marker_index == -1:
            break

        search_floor = max(0, marker_index - 12000)
        start = text.rfind("{", search_floor, marker_index)
        attempts = 0
        while start != -1 and attempts < 48:
            attempts += 1
            candidate = extract_balanced_json_object(text, start)
            if candidate and marker_index < start + len(candidate) and "rate_limits" in candidate:
                try:
                    decoded = json.loads(candidate)
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, dict) and decoded.get("type") == LIVE_LIMIT_EVENT_TYPE:
                    events.append(decoded)
                    break
            start = text.rfind("{", search_floor, start)

        marker_start = marker_index + len(LIVE_LIMIT_EVENT_TYPE)

    return events


def live_snapshot_from_limit(prefix: str, payload: dict[str, Any], ts: float) -> LimitSnapshot | None:
    used = number_or_none(payload.get("used_percent"))
    if used is None:
        return None

    window = int_or_none(payload.get("window_minutes")) or DEFAULT_LIMIT_WINDOWS.get(prefix)
    resets_at = number_or_none(payload.get("reset_at"))
    if resets_at is None:
        resets_at = number_or_none(payload.get("resets_at"))
    if resets_at is None:
        reset_after = number_or_none(payload.get("reset_after_seconds"))
        if reset_after is not None:
            resets_at = ts + reset_after

    return LimitSnapshot(
        ts=ts,
        session_ts=0.0,
        used_percent=used,
        window_minutes=window,
        resets_at=resets_at,
        source="live-log",
    )


def collect_live_limit_snapshots(live_log_db: Path | None, now: datetime) -> tuple[dict[str, LimitSnapshot], dict[str, int]]:
    stats = {"live_limit_rows": 0, "live_limit_events": 0, "live_limit_snapshots": 0}
    if live_log_db is None or not live_log_db.exists():
        return {}, stats

    now_ts = now.timestamp()
    snapshots: dict[str, LimitSnapshot] = {}

    try:
        connection = sqlite3.connect(f"file:{live_log_db}?mode=ro", uri=True, timeout=0.05)
    except sqlite3.Error:
        return {}, stats

    try:
        connection.execute("PRAGMA query_only = true")
        rows = connection.execute(
            """
            SELECT ts, ts_nanos, target, feedback_log_body
            FROM logs
            WHERE ts >= ?
              AND ts <= ?
              AND target = ?
              AND feedback_log_body IS NOT NULL
              AND feedback_log_body LIKE ?
            ORDER BY ts DESC, ts_nanos DESC, id DESC
            LIMIT ?
            """,
            (
                int(now_ts - LIVE_LIMIT_MAX_AGE_SECONDS),
                int(now_ts + 60),
                "codex_api::endpoint::responses_websocket",
                f"%{LIVE_LIMIT_EVENT_TYPE}%",
                LIVE_LIMIT_ROW_LIMIT,
            ),
        )

        for ts, ts_nanos, _target, body in rows:
            event_ts = float(ts) + (float(ts_nanos or 0) / 1_000_000_000)
            if event_ts > now_ts + 60:
                continue
            if now_ts - event_ts > LIVE_LIMIT_MAX_AGE_SECONDS:
                break
            if not isinstance(body, str) or LIVE_LIMIT_EVENT_TYPE not in body:
                continue

            stats["live_limit_rows"] += 1
            for event in iter_live_rate_limit_events(body):
                rate_limits = event.get("rate_limits")
                if not isinstance(rate_limits, dict):
                    continue

                stats["live_limit_events"] += 1
                for prefix in ("primary", "secondary"):
                    payload = rate_limits.get(prefix)
                    if not isinstance(payload, dict):
                        continue
                    snapshot = live_snapshot_from_limit(prefix, payload, event_ts)
                    if snapshot is None:
                        continue
                    existing = snapshots.get(prefix)
                    if existing is None or snapshot.ts > existing.ts:
                        snapshots[prefix] = snapshot

            if "primary" in snapshots and "secondary" in snapshots:
                break
    except sqlite3.Error:
        return {}, stats
    finally:
        connection.close()

    stats["live_limit_snapshots"] = len(snapshots)
    return snapshots, stats


def resolve_codex_bin(value: str | None) -> str | None:
    if value:
        path = Path(value).expanduser()
        if path.exists() and os.access(path, os.X_OK):
            return str(path)

    found = shutil.which("codex")
    if found:
        return found

    common_path = Path.home() / ".npm-global" / "bin" / "codex"
    if common_path.exists() and os.access(common_path, os.X_OK):
        return str(common_path)

    return None


def account_window_snapshot(prefix: str, payload: dict[str, Any], observed_ts: float) -> LimitSnapshot | None:
    used = number_or_none(payload.get("usedPercent"))
    if used is None:
        used = number_or_none(payload.get("used_percent"))
    if used is None:
        return None

    window = int_or_none(payload.get("windowDurationMins"))
    if window is None:
        window = int_or_none(payload.get("window_minutes"))
    if window is None:
        window = DEFAULT_LIMIT_WINDOWS.get(prefix)

    resets_at = number_or_none(payload.get("resetsAt"))
    if resets_at is None:
        resets_at = number_or_none(payload.get("resets_at"))

    return LimitSnapshot(
        ts=observed_ts,
        session_ts=0.0,
        used_percent=used,
        window_minutes=window,
        resets_at=resets_at,
        source="codex-account",
    )


def account_snapshots_from_payload(payload: dict[str, Any], observed_ts: float) -> dict[str, LimitSnapshot]:
    rate_limits = payload.get("rateLimits")
    by_limit_id = payload.get("rateLimitsByLimitId")
    if isinstance(by_limit_id, dict) and isinstance(by_limit_id.get("codex"), dict):
        rate_limits = by_limit_id["codex"]
    if not isinstance(rate_limits, dict):
        return {}

    snapshots: dict[str, LimitSnapshot] = {}
    for prefix in ("primary", "secondary"):
        window = rate_limits.get(prefix)
        if not isinstance(window, dict):
            continue
        snapshot = account_window_snapshot(prefix, window, observed_ts)
        if snapshot is not None:
            snapshots[prefix] = snapshot
    return snapshots


def collect_account_limit_snapshots(
    codex_bin: str | None,
    now: datetime,
    timeout_seconds: float = ACCOUNT_LIMIT_TIMEOUT_SECONDS,
) -> tuple[dict[str, LimitSnapshot], dict[str, int]]:
    stats = {"account_limit_requests": 0, "account_limit_snapshots": 0}
    resolved = resolve_codex_bin(codex_bin)
    if not resolved:
        return {}, stats

    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "codex-stats", "title": "Codex Stats", "version": "0.0.0"},
            "capabilities": {
                "experimentalApi": True,
                "requestAttestation": False,
                "optOutNotificationMethods": [],
            },
        },
    }
    read_limits = {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": None}

    try:
        proc = subprocess.Popen(
            [resolved, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return {}, stats

    snapshots: dict[str, LimitSnapshot] = {}
    try:
        if proc.stdin is None or proc.stdout is None:
            return {}, stats

        for message in (initialize, read_limits):
            proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        proc.stdin.flush()
        stats["account_limit_requests"] = 1

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            ready, _, _ = select.select([proc.stdout], [], [], remaining)
            if not ready:
                break

            line = proc.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != 2 or not isinstance(message.get("result"), dict):
                continue

            snapshots = account_snapshots_from_payload(message["result"], now.timestamp())
            stats["account_limit_snapshots"] = len(snapshots)
            break
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()

    return snapshots, stats


def start_of_month(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def add_months(value: datetime, delta: int) -> datetime:
    month_index = (value.year * 12 + value.month - 1) + delta
    year = month_index // 12
    month = month_index % 12 + 1
    return value.replace(year=year, month=month, day=1)


def fmt_month(value: datetime) -> str:
    return value.strftime("%b")


def bucket_label_for_window(window_minutes: int | None) -> str:
    if window_minutes == 300:
        return "5h"
    if window_minutes == 10080:
        return "Week"
    if not window_minutes:
        return "--"
    if window_minutes < 60:
        return f"{window_minutes}m"
    if window_minutes < 1440:
        hours = round(window_minutes / 60)
        return f"{hours}h"
    days = round(window_minutes / 1440)
    return f"{days}d"


def limit_snapshot(prefix: str, event: TokenEvent) -> LimitSnapshot:
    return LimitSnapshot(
        ts=event.ts,
        session_ts=event.session_ts,
        used_percent=getattr(event, f"{prefix}_used"),
        window_minutes=getattr(event, f"{prefix}_window"),
        resets_at=getattr(event, f"{prefix}_resets_at"),
    )


def roll_reset_forward(resets_at: float | None, window_minutes: int | None, now_ts: float) -> float | None:
    if resets_at is None or window_minutes is None or window_minutes <= 0:
        return resets_at
    if resets_at > now_ts:
        return resets_at

    window_seconds = window_minutes * 60
    missed_windows = int((now_ts - resets_at) // window_seconds) + 1
    return resets_at + missed_windows * window_seconds


def reset_generation(snapshot: LimitSnapshot) -> int:
    if snapshot.resets_at is None or snapshot.window_minutes is None or snapshot.window_minutes <= 0:
        return 0
    return int(snapshot.resets_at // (snapshot.window_minutes * 60))


def limit_sort_key(snapshot: LimitSnapshot) -> tuple[int, float, float, float, float]:
    used = snapshot.used_percent if snapshot.used_percent is not None else -1.0
    return (
        reset_generation(snapshot),
        snapshot.ts,
        snapshot.session_ts,
        used,
        snapshot.resets_at or 0.0,
    )


def select_limit_snapshot(events: list[TokenEvent], prefix: str, now: datetime) -> LimitSnapshot | None:
    now_ts = now.timestamp()
    active: list[LimitSnapshot] = []
    expired: list[LimitSnapshot] = []

    for event in events:
        if event.ts > now_ts:
            continue

        snapshot = limit_snapshot(prefix, event)
        if snapshot.used_percent is None:
            continue

        if snapshot.resets_at is not None and snapshot.resets_at <= now_ts:
            expired.append(snapshot)
            continue

        active.append(snapshot)

    if active:
        return max(active, key=limit_sort_key)

    if not expired:
        return None

    latest_expired = max(expired, key=limit_sort_key)
    rolled_reset = roll_reset_forward(latest_expired.resets_at, latest_expired.window_minutes, now_ts)
    return LimitSnapshot(
        ts=latest_expired.ts,
        session_ts=latest_expired.session_ts,
        used_percent=0.0,
        window_minutes=latest_expired.window_minutes,
        resets_at=rolled_reset,
    )


def merge_live_limit_snapshot(
    prefix: str,
    jsonl_snapshot: LimitSnapshot | None,
    live_snapshot: LimitSnapshot | None,
    now: datetime,
) -> LimitSnapshot | None:
    if live_snapshot is None:
        return jsonl_snapshot

    now_ts = now.timestamp()
    if live_snapshot.ts > now_ts + 60:
        return jsonl_snapshot
    if now_ts - live_snapshot.ts > LIVE_LIMIT_MAX_AGE_SECONDS:
        return jsonl_snapshot
    if jsonl_snapshot is not None and live_snapshot.ts + LIVE_LIMIT_NEWER_TOLERANCE_SECONDS < jsonl_snapshot.ts:
        return jsonl_snapshot

    window = live_snapshot.window_minutes
    if window is None and jsonl_snapshot is not None:
        window = jsonl_snapshot.window_minutes
    if window is None:
        window = DEFAULT_LIMIT_WINDOWS.get(prefix)

    resets_at = live_snapshot.resets_at
    if resets_at is None and jsonl_snapshot is not None:
        resets_at = jsonl_snapshot.resets_at
    resets_at = roll_reset_forward(resets_at, window, now_ts)

    session_ts = jsonl_snapshot.session_ts if jsonl_snapshot is not None else live_snapshot.session_ts
    return LimitSnapshot(
        ts=live_snapshot.ts,
        session_ts=session_ts,
        used_percent=live_snapshot.used_percent,
        window_minutes=window,
        resets_at=resets_at,
        source=live_snapshot.source,
    )


def limit_payload(snapshot: LimitSnapshot | None, local_tz: timezone) -> dict[str, Any]:
    if snapshot is None:
        return {
            "label": "--",
            "remaining_percent": None,
            "used_percent": None,
            "resets_at": None,
            "observed_at": None,
            "source": None,
        }

    used = snapshot.used_percent
    window = snapshot.window_minutes
    resets_at = snapshot.resets_at
    if used is None:
        return {
            "label": bucket_label_for_window(window),
            "remaining_percent": None,
            "used_percent": None,
            "resets_at": None,
            "observed_at": None,
            "source": snapshot.source,
        }

    used = max(0.0, min(100.0, used))
    reset_iso = None
    if resets_at:
        reset_iso = datetime.fromtimestamp(resets_at, tz=local_tz).isoformat()
    observed_iso = datetime.fromtimestamp(snapshot.ts, tz=local_tz).isoformat() if snapshot.ts else None

    return {
        "label": bucket_label_for_window(window),
        "remaining_percent": round(max(0.0, 100.0 - used), 1),
        "used_percent": round(used, 1),
        "resets_at": reset_iso,
        "observed_at": observed_iso,
        "source": snapshot.source,
    }


def aggregate(
    events: list[TokenEvent],
    now: datetime,
    stats: dict[str, int],
    log_root: Path,
    live_limits: dict[str, LimitSnapshot] | None = None,
) -> dict[str, Any]:
    local_tz = now.tzinfo or timezone.utc
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start_date = (now.date() - timedelta(days=6))
    month_start = start_of_month(now)
    three_month_start = add_months(month_start, -2)

    hourly = [0 for _ in range(24)]
    week_buckets = {week_start_date + timedelta(days=i): 0 for i in range(7)}
    month_days = [month_start.date() + timedelta(days=i) for i in range((now.date() - month_start.date()).days + 1)]
    month_buckets = {day: 0 for day in month_days}
    three_month_buckets: dict[str, int] = {}
    for i in range(3):
        month = add_months(three_month_start, i)
        three_month_buckets[month.strftime("%Y-%m")] = 0

    limit_events: list[TokenEvent] = []

    for event in events:
        event_dt = datetime.fromtimestamp(event.ts, tz=local_tz)
        if event_dt > now:
            continue

        limit_events.append(event)

        if day_start <= event_dt <= now:
            hourly[event_dt.hour] += event.total_tokens

        event_date = event_dt.date()
        if event_date in week_buckets:
            week_buckets[event_date] += event.total_tokens
        if event_date in month_buckets:
            month_buckets[event_date] += event.total_tokens

        if three_month_start <= event_dt <= now:
            key = event_dt.strftime("%Y-%m")
            if key in three_month_buckets:
                three_month_buckets[key] += event.total_tokens

    message = ""
    ok = log_root.exists()
    if not ok:
        message = f"Log root not found: {log_root}"
    elif stats["malformed_lines"]:
        message = f"Skipped {stats['malformed_lines']} malformed JSONL line(s)"

    primary_snapshot = select_limit_snapshot(limit_events, "primary", now)
    secondary_snapshot = select_limit_snapshot(limit_events, "secondary", now)
    live_limits = live_limits or {}
    primary_snapshot = merge_live_limit_snapshot("primary", primary_snapshot, live_limits.get("primary"), now)
    secondary_snapshot = merge_live_limit_snapshot("secondary", secondary_snapshot, live_limits.get("secondary"), now)

    limits = {
        "primary": limit_payload(primary_snapshot, local_tz),
        "secondary": limit_payload(secondary_snapshot, local_tz),
    }
    # Per-model weekly buckets only exist when the Claude online source supplies
    # them; absent keys are skipped so the Codex/offline contract is unchanged.
    for extra_key in ("sonnet_weekly", "opus_weekly"):
        extra_snapshot = merge_live_limit_snapshot(extra_key, None, live_limits.get(extra_key), now)
        if extra_snapshot is not None:
            limits[extra_key] = limit_payload(extra_snapshot, local_tz)

    return {
        "generated_at": now.isoformat(),
        "status": {
            "ok": ok,
            "message": message,
            "files_scanned": stats["files_scanned"],
            "files_parsed": stats["files_parsed"],
            "malformed_lines": stats["malformed_lines"],
            "live_limit_rows": stats.get("live_limit_rows", 0),
            "live_limit_events": stats.get("live_limit_events", 0),
            "live_limit_snapshots": stats.get("live_limit_snapshots", 0),
            "account_limit_requests": stats.get("account_limit_requests", 0),
            "account_limit_snapshots": stats.get("account_limit_snapshots", 0),
            "claude_online_requests": stats.get("claude_online_requests", 0),
            "claude_online_snapshots": stats.get("claude_online_snapshots", 0),
            "claude_online_status": stats.get("claude_online_status", ""),
        },
        "today": {
            "total_tokens": sum(hourly),
            "hourly": hourly,
        },
        "limits": limits,
        "history": {
            "week": [
                {"date": day.isoformat(), "label": day.strftime("%a"), "total_tokens": tokens}
                for day, tokens in week_buckets.items()
            ],
            "month": [
                {"date": day.isoformat(), "label": str(day.day), "total_tokens": tokens}
                for day, tokens in month_buckets.items()
            ],
            "three_months": [
                {
                    "month": key,
                    "label": fmt_month(datetime.strptime(key, "%Y-%m").replace(tzinfo=local_tz)),
                    "total_tokens": tokens,
                }
                for key, tokens in three_month_buckets.items()
            ],
        },
    }


def codex_source(
    log_root: Path,
    cache_file: Path,
    use_cache: bool,
    now: datetime,
    local_tz: timezone,
    live_log_db: Path | None,
    use_account_limits: bool,
    codex_bin: str | None,
) -> tuple[list[TokenEvent], dict[str, int], dict[str, LimitSnapshot]]:
    events, stats = collect_events(log_root, cache_file, use_cache, local_tz)
    live_limits, live_stats = collect_live_limit_snapshots(live_log_db, now)
    stats.update(live_stats)
    if use_account_limits:
        account_limits, account_stats = collect_account_limit_snapshots(codex_bin, now)
        stats.update(account_stats)
        live_limits.update(account_limits)
    return events, stats, live_limits


def claude_source(
    log_root: Path,
    cache_file: Path,
    use_cache: bool,
    now: datetime,
    local_tz: timezone,
    limits_file: Path | None,
    online_enabled: bool = False,
    creds_file: Path | None = None,
    online_cache: Path | None = None,
    online_opener: Any = None,
) -> tuple[list[TokenEvent], dict[str, int], dict[str, LimitSnapshot]]:
    events, stats = collect_claude_events(log_root, cache_file, use_cache, local_tz)
    live_limits, limit_stats = collect_claude_limit_snapshots(limits_file, now)
    stats.update(limit_stats)
    if online_enabled and creds_file is not None:
        cache_path = online_cache if online_cache is not None else DEFAULT_CLAUDE_ONLINE_CACHE_FILE
        # The online cache is a rate-limit/throttle buffer, not the JSONL parse
        # cache that --no-cache controls: it serves the last-known live limits
        # across the endpoint's 429 backoff and avoids hammering Anthropic on
        # every refresh. Without it the per-model "Sonnet" gauge blanks on every
        # throttled fetch. So keep it on even when token-history caching is off.
        # It stores only numeric utilization + reset timestamps.
        online_limits, online_stats = collect_claude_online_snapshots(
            creds_file, cache_path, now, True, opener=online_opener
        )
        stats.update(online_stats)
        # Online (live API) wins for 5h/Week and adds the per-model buckets.
        live_limits.update(online_limits)
    return events, stats, live_limits


def build_payload(
    log_root: Path,
    cache_file: Path,
    use_cache: bool,
    now: datetime,
    live_log_db: Path | None = None,
    use_account_limits: bool = False,
    codex_bin: str | None = None,
    provider: str = "codex",
    limits_file: Path | None = None,
    claude_online: bool = False,
    claude_creds_file: Path | None = None,
    claude_online_cache: Path | None = None,
    claude_online_opener: Any = None,
) -> dict[str, Any]:
    local_tz = now.tzinfo or timezone.utc
    if provider == "claude":
        events, stats, live_limits = claude_source(
            log_root,
            cache_file,
            use_cache,
            now,
            local_tz,
            limits_file,
            online_enabled=claude_online,
            creds_file=claude_creds_file,
            online_cache=claude_online_cache,
            online_opener=claude_online_opener,
        )
    else:
        events, stats, live_limits = codex_source(
            log_root, cache_file, use_cache, now, local_tz, live_log_db, use_account_limits, codex_bin
        )
    return aggregate(events, now, stats, log_root, live_limits)


def main() -> int:
    args = parse_args()
    now = parse_now(args.now)
    provider = args.provider

    if args.log_root is not None:
        log_root = Path(args.log_root).expanduser()
    else:
        log_root = DEFAULT_CLAUDE_LOG_ROOT if provider == "claude" else DEFAULT_LOG_ROOT

    if args.cache_file is not None:
        cache_file = Path(args.cache_file).expanduser()
    elif provider == "claude":
        cache_file = DEFAULT_CACHE_FILE.with_name("cache-claude.json")
    else:
        cache_file = DEFAULT_CACHE_FILE

    if provider == "claude":
        limits_file = Path(args.limits_file).expanduser() if args.limits_file else DEFAULT_CLAUDE_LIMITS_FILE
        creds_file = (
            Path(args.claude_credentials_file).expanduser()
            if args.claude_credentials_file
            else claude_credentials_default()
        )
        online_cache = (
            Path(args.claude_online_cache).expanduser()
            if args.claude_online_cache
            else DEFAULT_CLAUDE_ONLINE_CACHE_FILE
        )
        payload = build_payload(
            log_root,
            cache_file,
            not args.no_cache,
            now,
            provider="claude",
            limits_file=limits_file,
            claude_online=args.claude_online,
            claude_creds_file=creds_file,
            claude_online_cache=online_cache,
        )
    else:
        live_log_db = None if args.no_live_limits else Path(args.live_log_db).expanduser()
        use_account_limits = not args.no_live_limits and not args.no_account_limits
        payload = build_payload(
            log_root,
            cache_file,
            not args.no_cache,
            now,
            live_log_db,
            use_account_limits,
            args.codex_bin,
        )

    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
