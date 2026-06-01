from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))

import codex_stats_helper as helper


def token_event(
    timestamp: str,
    total: int,
    primary: float = 25.0,
    secondary: float = 40.0,
    primary_resets_at: int = 1779998881,
    secondary_resets_at: int = 1780182909,
) -> dict:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": total - 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 10,
                    "reasoning_output_tokens": 0,
                    "total_tokens": total,
                },
                "model_context_window": 237500,
            },
            "rate_limits": {
                "primary": {"used_percent": primary, "window_minutes": 300, "resets_at": primary_resets_at},
                "secondary": {"used_percent": secondary, "window_minutes": 10080, "resets_at": secondary_resets_at},
            },
        },
    }


class HelperTests(unittest.TestCase):
    def write_jsonl(self, path: Path, rows: list[dict | str]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                if isinstance(row, str):
                    handle.write(row + "\n")
                else:
                    handle.write(json.dumps(row) + "\n")

    def build(self, root: Path, cache_file: Path, now: str, use_cache: bool = True) -> dict:
        parsed_now = helper.parse_now(now)
        return helper.build_payload(root, cache_file, use_cache, parsed_now)

    def write_live_db(self, path: Path, rows: list[tuple[float, str, str]]) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                CREATE TABLE logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    ts_nanos INTEGER NOT NULL,
                    target TEXT NOT NULL,
                    feedback_log_body TEXT
                )
                """
            )
            for ts, target, body in rows:
                seconds = int(ts)
                nanos = int((ts - seconds) * 1_000_000_000)
                connection.execute(
                    "INSERT INTO logs (ts, ts_nanos, target, feedback_log_body) VALUES (?, ?, ?, ?)",
                    (seconds, nanos, target, body),
                )
            connection.commit()
        finally:
            connection.close()

    def test_daily_hourly_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir()
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                root / "one.jsonl",
                [
                    token_event("2026-05-27T23:50:00+07:00", 999, primary=5, secondary=20),
                    token_event("2026-05-28T00:15:00+07:00", 100, primary=10, secondary=30),
                    token_event("2026-05-28T13:30:00+07:00", 250, primary=20, secondary=40),
                ],
            )

            payload = self.build(root, cache, "2026-05-28T14:00:00+07:00")

            self.assertEqual(payload["today"]["total_tokens"], 350)
            self.assertEqual(payload["today"]["hourly"][0], 100)
            self.assertEqual(payload["today"]["hourly"][13], 250)
            self.assertEqual(payload["limits"]["primary"]["label"], "5h")
            self.assertEqual(payload["limits"]["primary"]["remaining_percent"], 80.0)
            self.assertEqual(payload["limits"]["secondary"]["remaining_percent"], 60.0)

    def test_week_month_three_month_buckets_and_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir()
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                root / "mixed.jsonl",
                [
                    "not-json",
                    token_event("2026-03-31T10:00:00+07:00", 50),
                    token_event("2026-04-01T10:00:00+07:00", 100),
                    token_event("2026-05-22T10:00:00+07:00", 200),
                    token_event("2026-05-28T10:00:00+07:00", 300),
                    {"timestamp": "2026-05-28T10:00:00+07:00", "type": "event_msg", "payload": {"type": "agent_message"}},
                ],
            )

            payload = self.build(root, cache, "2026-05-28T12:00:00+07:00")

            self.assertEqual(payload["status"]["malformed_lines"], 1)
            self.assertEqual(payload["history"]["week"][0]["date"], "2026-05-22")
            self.assertEqual(payload["history"]["week"][0]["total_tokens"], 200)
            self.assertEqual(payload["history"]["week"][-1]["total_tokens"], 300)
            self.assertEqual(payload["history"]["three_months"][0]["month"], "2026-03")
            self.assertEqual(payload["history"]["three_months"][0]["total_tokens"], 50)
            self.assertEqual(payload["history"]["three_months"][1]["total_tokens"], 100)
            self.assertEqual(payload["history"]["three_months"][2]["total_tokens"], 500)

    def test_empty_and_missing_log_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir()
            cache = Path(tmp) / "cache.json"

            payload = self.build(root, cache, "2026-05-28T12:00:00+07:00")
            self.assertTrue(payload["status"]["ok"])
            self.assertEqual(payload["today"]["total_tokens"], 0)
            self.assertIsNone(payload["limits"]["primary"]["remaining_percent"])

            missing = Path(tmp) / "missing"
            payload = self.build(missing, cache, "2026-05-28T12:00:00+07:00")
            self.assertFalse(payload["status"]["ok"])
            self.assertIn("Log root not found", payload["status"]["message"])

    def test_cache_invalidates_when_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir()
            cache = Path(tmp) / "cache.json"
            log_file = root / "one.jsonl"

            self.write_jsonl(log_file, [token_event("2026-05-28T10:00:00+07:00", 100)])
            first = self.build(root, cache, "2026-05-28T12:00:00+07:00")
            self.assertEqual(first["today"]["total_tokens"], 100)

            self.write_jsonl(
                log_file,
                [
                    token_event("2026-05-28T10:00:00+07:00", 100),
                    token_event("2026-05-28T11:00:00+07:00", 75),
                ],
            )
            second = self.build(root, cache, "2026-05-28T12:00:00+07:00")
            self.assertEqual(second["today"]["total_tokens"], 175)
            self.assertGreaterEqual(second["status"]["files_parsed"], 1)

    def test_expired_rate_limit_does_not_override_current_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir()
            cache = Path(tmp) / "cache.json"

            self.write_jsonl(
                root / "mixed.jsonl",
                [
                    token_event(
                        "2026-05-29T11:20:00+07:00",
                        100,
                        primary=1,
                        primary_resets_at=1780046258,
                    ),
                    token_event(
                        "2026-05-29T11:28:00+07:00",
                        50,
                        primary=12,
                        primary_resets_at=1780028145,
                    ),
                ],
            )

            payload = self.build(root, cache, "2026-05-29T11:29:00+07:00")

            self.assertEqual(payload["limits"]["primary"]["remaining_percent"], 99.0)
            self.assertEqual(payload["limits"]["primary"]["resets_at"], "2026-05-29T16:17:38+07:00")

    def test_expired_rate_limit_rolls_forward_when_no_current_window_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir()
            cache = Path(tmp) / "cache.json"

            self.write_jsonl(
                root / "stale.jsonl",
                [
                    token_event(
                        "2026-05-29T11:28:00+07:00",
                        50,
                        primary=12,
                        primary_resets_at=1780028145,
                    ),
                ],
            )

            payload = self.build(root, cache, "2026-05-29T11:29:00+07:00")

            self.assertEqual(payload["limits"]["primary"]["remaining_percent"], 100.0)
            self.assertEqual(payload["limits"]["primary"]["resets_at"], "2026-05-29T16:15:45+07:00")

    def test_latest_current_window_usage_wins_when_sessions_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir()
            cache = Path(tmp) / "cache.json"

            self.write_jsonl(
                root / "rollout-2026-05-29T11-01-36-newer.jsonl",
                [
                    token_event(
                        "2026-05-29T11:31:37+07:00",
                        100,
                        primary=6,
                        primary_resets_at=1780046258,
                    ),
                ],
            )
            self.write_jsonl(
                root / "rollout-2026-05-26T23-50-18-older.jsonl",
                [
                    token_event(
                        "2026-05-29T11:31:56+07:00",
                        50,
                        primary=1,
                        primary_resets_at=1780046258,
                    ),
                ],
            )

            payload = self.build(root, cache, "2026-05-29T11:32:00+07:00")

            self.assertEqual(payload["limits"]["primary"]["remaining_percent"], 99.0)

    def test_live_rate_limit_metadata_overrides_stale_jsonl_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir()
            cache = Path(tmp) / "cache.json"
            live_db = Path(tmp) / "logs_2.sqlite"

            primary_reset = int(helper.parse_now("2026-05-29T16:00:00+07:00").timestamp())
            secondary_reset = int(helper.parse_now("2026-06-01T06:00:00+07:00").timestamp())
            self.write_jsonl(
                root / "one.jsonl",
                [
                    token_event(
                        "2026-05-29T11:50:00+07:00",
                        100,
                        primary=5,
                        secondary=20,
                        primary_resets_at=primary_reset,
                        secondary_resets_at=secondary_reset,
                    ),
                ],
            )

            live_event = {
                "type": "codex.rate_limits",
                "rate_limits": {
                    "primary": {"used_percent": 12, "window_minutes": 300, "reset_at": primary_reset},
                    "secondary": {"used_percent": 47, "window_minutes": 10080, "reset_at": secondary_reset},
                },
            }
            ignored_text = '{"rate_limits":{"primary":{"used_percent":99}}}'
            live_ts = helper.parse_now("2026-05-29T11:59:00+07:00").timestamp()
            self.write_live_db(
                live_db,
                [
                    (live_ts + 1, "codex_api::endpoint::responses_websocket", ignored_text),
                    (
                        live_ts,
                        "codex_api::endpoint::responses_websocket",
                        f"session_loop: parsed SSE event {json.dumps(live_event, separators=(',', ':'))}",
                    ),
                ],
            )

            payload = helper.build_payload(
                root,
                cache,
                True,
                helper.parse_now("2026-05-29T12:00:00+07:00"),
                live_db,
            )

            self.assertEqual(payload["limits"]["primary"]["remaining_percent"], 88.0)
            self.assertEqual(payload["limits"]["secondary"]["remaining_percent"], 53.0)
            self.assertEqual(payload["limits"]["primary"]["source"], "live-log")
            self.assertEqual(payload["status"]["live_limit_snapshots"], 2)

    def test_account_rate_limits_use_codex_limit_id_payload(self) -> None:
        observed = helper.parse_now("2026-05-29T12:00:00+07:00").timestamp()
        payload = {
            "rateLimits": {
                "primary": {"usedPercent": 90, "windowDurationMins": 300, "resetsAt": 1780046258},
                "secondary": {"usedPercent": 90, "windowDurationMins": 10080, "resetsAt": 1780646258},
            },
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {"usedPercent": 17, "windowDurationMins": 300, "resetsAt": 1780046258},
                    "secondary": {"usedPercent": 3, "windowDurationMins": 10080, "resetsAt": 1780646258},
                },
                "other": {
                    "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 1780046258},
                },
            },
        }

        snapshots = helper.account_snapshots_from_payload(payload, observed)

        self.assertEqual(snapshots["primary"].used_percent, 17)
        self.assertEqual(snapshots["secondary"].used_percent, 3)
        self.assertEqual(snapshots["primary"].source, "codex-account")


if __name__ == "__main__":
    unittest.main()
