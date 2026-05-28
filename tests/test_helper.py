from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))

import codex_stats_helper as helper


def token_event(timestamp: str, total: int, primary: float = 25.0, secondary: float = 40.0) -> dict:
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
                "primary": {"used_percent": primary, "window_minutes": 300, "resets_at": 1779998881},
                "secondary": {"used_percent": secondary, "window_minutes": 10080, "resets_at": 1780182909},
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

    def test_daily_hourly_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir()
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                root / "one.jsonl",
                [
                    token_event("2026-05-27T23:50:00+07:00", 999),
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


if __name__ == "__main__":
    unittest.main()

