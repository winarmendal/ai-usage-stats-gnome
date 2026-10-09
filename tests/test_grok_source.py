from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HELPER_DIR = Path(__file__).resolve().parents[1] / "helper"
sys.path.insert(0, str(HELPER_DIR))

import codex_stats_helper as helper

NOW = "2026-09-15T20:00:00+07:00"


def turn(ended_at: str, total: int, turn_number: int = 1) -> dict:
    # One `turns[]` entry as Grok writes it: nanosecond endedAt + per-turn totals.
    return {
        "turnNumber": turn_number,
        "endedAt": ended_at,
        "inputTokens": max(0, total - 1),
        "outputTokens": 1,
        "cachedReadTokens": 0,
        "cacheCreationTokens": 0,
        "reasoningTokens": 0,
        "totalTokens": total,
        "modelCalls": 1,
        "costUsdTicks": 0,
        "turnCount": 1,
        "primaryModelId": "grok-4.6-build",
    }


def usage_file(turns: list, session_total: int | None = None, extra: dict | None = None) -> dict:
    total = session_total if session_total is not None else sum(
        int(item.get("totalTokens", 0)) for item in turns if isinstance(item, dict)
    )
    payload: dict = {
        "sessionId": "01a0a04f-d3ca-7033-af72-5c3cf2fd2c4f",
        "updatedAt": "2026-09-15T03:31:28.235871341+00:00",
        "session": {
            "inputTokens": total,
            "outputTokens": 0,
            "cachedReadTokens": 0,
            "cacheCreationTokens": 0,
            "reasoningTokens": 0,
            "totalTokens": total,
            "modelCalls": 1,
            "costUsdTicks": 0,
            "turnCount": len(turns),
            "primaryModelId": "grok-4.6-build",
        },
        "turns": turns,
    }
    if extra:
        payload.update(extra)
    return payload


def billing_line(ts: str, percent: float, period_end: str, extra_config: dict | None = None) -> str:
    config: dict = {
        "creditUsagePercent": percent,
        "currentPeriod": {
            "type": "USAGE_PERIOD_TYPE_WEEKLY",
            "start": "2026-09-08T16:01:55.200423+00:00",
            "end": period_end,
        },
        "onDemandCap": {"val": 0},
        "onDemandUsed": {"val": 0},
        "prepaidBalance": {"val": 0},
        "isUnifiedBillingUser": True,
        "billingPeriodStart": "2026-09-08T16:01:55.200423+00:00",
        "billingPeriodEnd": period_end,
        "historyLen": 0,
    }
    if extra_config:
        config.update(extra_config)
    return json.dumps(
        {
            "ts": ts,
            "src": "shell",
            "pid": 98684,
            "ver": "1.0.30",
            "lvl": "info",
            "msg": helper.GROK_BILLING_MSG,
            "ctx": {"config": config, "onDemandEnabled": None, "subscriptionTier": "SuperGrok"},
        }
    )


class GrokSourceTests(unittest.TestCase):
    def write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def write_lines(self, path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")

    def session_dir(self, root: Path, name: str = "sess-1") -> Path:
        return root / "sessions" / "%2Fhome%2Fwin%2Fproj" / name

    def build(self, root: Path, cache: Path, now: str = NOW, use_cache: bool = True) -> dict:
        return helper.build_payload(root, cache, use_cache, helper.parse_now(now), provider="grok")

    # --- token bucketing -------------------------------------------------

    def test_turns_are_counted_and_session_totals_ignored(self) -> None:
        # `session` inherits history through resume/fork; only `turns[]` may count.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            self.write_json(
                self.session_dir(root) / "usage.json",
                usage_file(
                    [
                        turn("2026-09-15T03:00:00.082965175+00:00", 200, 1),
                        turn("2026-09-15T03:05:00.082965175+00:00", 100, 2),
                    ],
                    session_total=999999,
                ),
            )
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 300)
            # 03:00Z == 10:00 in +07:00
            self.assertEqual(payload["today"]["hourly"][10], 300)
            self.assertTrue(payload["status"]["ok"])

    def test_forked_session_replay_counts_once(self) -> None:
        # Forking copies the parent's turns verbatim into a new session directory.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            shared = turn("2026-09-15T03:00:00.082965175+00:00", 500, 1)
            self.write_json(self.session_dir(root, "parent") / "usage.json", usage_file([shared]))
            self.write_json(
                self.session_dir(root, "fork") / "usage.json",
                usage_file([shared, turn("2026-09-15T04:00:00.082965175+00:00", 70, 2)]),
            )
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 570)

    def test_malformed_and_zero_turns_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            no_ended_at = turn("2026-09-15T03:10:00.000000000+00:00", 42, 2)
            no_ended_at.pop("endedAt")
            self.write_json(
                self.session_dir(root) / "usage.json",
                usage_file(
                    [
                        turn("2026-09-15T03:00:00.082965175+00:00", 200, 1),
                        no_ended_at,
                        turn("2026-09-15T03:20:00.000000000+00:00", 0, 3),
                        "not-a-dict",
                    ]
                ),
            )
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 200)

    def test_unparseable_usage_file_is_counted_malformed_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            good = self.session_dir(root, "good") / "usage.json"
            self.write_json(good, usage_file([turn("2026-09-15T03:00:00.000000000+00:00", 90, 1)]))
            broken = self.session_dir(root, "broken") / "usage.json"
            broken.parent.mkdir(parents=True, exist_ok=True)
            broken.write_text("{not json", encoding="utf-8")
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 90)
            self.assertTrue(payload["status"]["ok"])
            self.assertEqual(payload["status"]["malformed_lines"], 1)

    def test_nanosecond_and_offset_timestamps_parse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            self.write_json(
                self.session_dir(root) / "usage.json",
                usage_file(
                    [
                        turn("2026-09-15T03:00:00.082965175+00:00", 10, 1),
                        turn("2026-09-15T04:00:00.5Z", 20, 2),
                        turn("2026-09-15T12:00:00+07:00", 30, 3),
                    ]
                ),
            )
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 60)

    # --- privacy ---------------------------------------------------------

    def test_privacy_siblings_and_log_text_never_read_or_cached(self) -> None:
        # usage.json's neighbours hold prompt/response text and must never be
        # opened; an extra key inside usage.json and a non-billing log line must
        # never surface either.
        sibling_secret = "GROK-CHAT-HISTORY-SECRET"
        inline_secret = "GROK-USAGE-EXTRA-KEY-SECRET"
        log_secret = "GROK-TOOL-OUTPUT-SECRET"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            session = self.session_dir(root)
            self.write_json(
                session / "usage.json",
                usage_file(
                    [turn("2026-09-15T03:00:00.082965175+00:00", 123, 1)],
                    extra={"promptPreview": inline_secret},
                ),
            )
            self.write_lines(
                session / "chat_history.jsonl",
                [json.dumps({"role": "user", "content": sibling_secret})],
            )
            self.write_lines(session / "updates.jsonl", [json.dumps({"text": sibling_secret})])
            (session / "system_prompt.txt").write_text(sibling_secret, encoding="utf-8")
            self.write_lines(session / "terminal" / "out.jsonl", [json.dumps({"out": sibling_secret})])
            self.write_lines(
                root / "logs" / "unified.jsonl",
                [
                    json.dumps({"ts": "2026-09-15T03:00:00.000Z", "msg": "tool: output", "ctx": {"out": log_secret}}),
                    billing_line("2026-09-15T03:10:00.100Z", 71.0, "2026-09-22T16:01:55.200423+00:00"),
                ],
            )

            payload = self.build(root, cache)
            rendered = json.dumps(payload)
            cached = cache.read_text(encoding="utf-8")
            for secret in (sibling_secret, inline_secret, log_secret):
                self.assertNotIn(secret, rendered)
                self.assertNotIn(secret, cached)
            self.assertEqual(payload["today"]["total_tokens"], 123)
            self.assertEqual(payload["limits"]["secondary"]["used_percent"], 71.0)
            # Nothing from the log is cached: the cache holds only the usage.json
            # file ledger, no billing fields and no limit source marker.
            for leaked in ("creditUsagePercent", "currentPeriod", "subscriptionTier", helper.GROK_LIMIT_SOURCE):
                self.assertNotIn(leaked, cached)

    def test_only_files_named_usage_json_are_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            session = self.session_dir(root)
            self.write_json(session / "usage.json", usage_file([turn("2026-09-15T03:00:00.000000000+00:00", 100, 1)]))
            self.write_json(
                session / "usage.json.bak",
                usage_file([turn("2026-09-15T03:00:00.000000000+00:00", 500, 9)]),
            )
            self.write_json(
                session / "session_usage.json",
                usage_file([turn("2026-09-15T04:00:00.000000000+00:00", 700, 9)]),
            )
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 100)
            self.assertEqual(payload["status"]["files_scanned"], 1)

    # --- weekly credit limit ---------------------------------------------

    def test_weekly_limit_newest_line_wins_and_primary_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            (root / "sessions").mkdir(parents=True)
            self.write_lines(
                root / "logs" / "unified.jsonl",
                [
                    billing_line("2026-09-15T02:00:00.000Z", 30.0, "2026-09-22T16:01:55.200423+00:00"),
                    billing_line("2026-09-15T05:00:00.000Z", 71.0, "2026-09-22T16:01:55.200423+00:00"),
                ],
            )
            payload = self.build(root, cache)
            secondary = payload["limits"]["secondary"]
            self.assertEqual(secondary["used_percent"], 71.0)
            self.assertEqual(secondary["remaining_percent"], 29.0)
            self.assertEqual(secondary["label"], "Week")
            self.assertEqual(secondary["source"], helper.GROK_LIMIT_SOURCE)
            self.assertTrue(secondary["resets_at"].startswith("2026-09-22"))
            # Grok has no 5-hour window at all.
            self.assertIsNone(payload["limits"]["primary"]["used_percent"])
            self.assertEqual(payload["limits"]["primary"]["label"], "--")
            self.assertEqual(payload["status"]["grok_limit_snapshots"], 1)

    def test_stale_billing_line_older_than_a_day_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            (root / "sessions").mkdir(parents=True)
            self.write_lines(
                root / "logs" / "unified.jsonl",
                [billing_line("2026-09-13T05:00:00.000Z", 44.0, "2026-09-22T16:01:55.200423+00:00")],
            )
            payload = self.build(root, cache)
            self.assertIsNone(payload["limits"]["secondary"]["used_percent"])
            self.assertEqual(payload["status"]["grok_limit_snapshots"], 0)

    def test_expired_period_reports_zero_used_and_rolls_reset_forward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            (root / "sessions").mkdir(parents=True)
            self.write_lines(
                root / "logs" / "unified.jsonl",
                # Written an hour ago, but its weekly period ended before `now`.
                [billing_line("2026-09-15T05:00:00.000Z", 88.0, "2026-09-15T06:00:00.000000+00:00")],
            )
            payload = self.build(root, cache)
            secondary = payload["limits"]["secondary"]
            self.assertEqual(secondary["used_percent"], 0.0)
            self.assertEqual(secondary["label"], "Week")
            self.assertGreater(secondary["resets_at"], payload["generated_at"])

    def test_malformed_billing_line_is_skipped_for_the_previous_good_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            (root / "sessions").mkdir(parents=True)
            self.write_lines(
                root / "logs" / "unified.jsonl",
                [
                    billing_line("2026-09-15T02:00:00.000Z", 30.0, "2026-09-22T16:01:55.200423+00:00"),
                    '{"ts":"2026-09-15T05:00:00.000Z","msg":"' + helper.GROK_BILLING_MSG + '","ctx":{',
                ],
            )
            payload = self.build(root, cache)
            self.assertEqual(payload["limits"]["secondary"]["used_percent"], 30.0)

    def test_billing_line_without_percent_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            (root / "sessions").mkdir(parents=True)
            line = json.loads(billing_line("2026-09-15T05:00:00.000Z", 71.0, "2026-09-22T16:01:55.200423+00:00"))
            line["ctx"]["config"].pop("creditUsagePercent")
            self.write_lines(root / "logs" / "unified.jsonl", [json.dumps(line)])
            payload = self.build(root, cache)
            self.assertIsNone(payload["limits"]["secondary"]["used_percent"])

    def test_missing_log_file_still_reports_tokens_and_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            self.write_json(self.session_dir(root) / "usage.json", usage_file([turn("2026-09-15T03:00:00.000000000+00:00", 77, 1)]))
            payload = self.build(root, cache)
            self.assertTrue(payload["status"]["ok"])
            self.assertEqual(payload["today"]["total_tokens"], 77)
            self.assertEqual(payload["limits"]["secondary"]["label"], "--")

    # --- tail-bounded log read -------------------------------------------

    def test_read_tail_lines_is_bounded_and_drops_partial_first_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unified.jsonl"
            path.write_text("aaaa\nbbbb\ncccc\ndddd\n", encoding="utf-8")
            self.assertEqual(helper.read_tail_lines(path, 4096), ["aaaa", "bbbb", "cccc", "dddd"])
            # The last 12 bytes start mid-"bbbb"; that partial record is dropped
            # rather than handed to json.loads.
            self.assertEqual(helper.read_tail_lines(path, 12), ["cccc", "dddd"])
            # A window that holds only one (possibly truncated) record yields
            # nothing rather than risk decoding a partial line.
            self.assertEqual(helper.read_tail_lines(path, 5), [])
            self.assertEqual(helper.read_tail_lines(Path(tmp) / "absent.jsonl", 4096), [])

    def test_billing_line_outside_the_tail_window_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            (root / "sessions").mkdir(parents=True)
            padding = json.dumps({"ts": "2026-09-15T05:00:00.000Z", "msg": "noise", "ctx": {"pad": "x" * 400}})
            self.write_lines(
                root / "logs" / "unified.jsonl",
                [billing_line("2026-09-15T02:00:00.000Z", 30.0, "2026-09-22T16:01:55.200423+00:00")]
                + [padding] * 20,
            )
            original = helper.GROK_LOG_TAIL_BYTES
            helper.GROK_LOG_TAIL_BYTES = 1024
            try:
                payload = self.build(root, cache)
            finally:
                helper.GROK_LOG_TAIL_BYTES = original
            self.assertIsNone(payload["limits"]["secondary"]["used_percent"])

            # The same log read in full does find it.
            payload_full = self.build(root, Path(tmp) / "cache2.json")
            self.assertEqual(payload_full["limits"]["secondary"]["used_percent"], 30.0)

    # --- cache ledger ----------------------------------------------------

    def test_deleted_session_history_is_retained_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok"
            cache = Path(tmp) / "cache.json"
            session = self.session_dir(root)
            self.write_json(session / "usage.json", usage_file([turn("2026-09-15T03:00:00.000000000+00:00", 4242, 1)]))
            first = self.build(root, cache)
            self.assertEqual(first["today"]["total_tokens"], 4242)

            shutil.rmtree(session)
            second = self.build(root, cache)
            self.assertEqual(second["today"]["total_tokens"], 4242)
            self.assertEqual(second["status"]["files_scanned"], 0)
            self.assertEqual(second["status"]["files_retained"], 1)

    def test_missing_root_reports_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "no-grok-here"
            cache = Path(tmp) / "cache.json"
            payload = self.build(root, cache)
            self.assertFalse(payload["status"]["ok"])
            self.assertIn("Log root not found", payload["status"]["message"])
            self.assertEqual(payload["today"]["total_tokens"], 0)
            self.assertEqual(payload["limits"]["secondary"]["label"], "--")

    # --- CLI wiring ------------------------------------------------------

    def test_main_uses_grok_default_root_and_cache_name(self) -> None:
        # Guards the main() wiring: `--provider grok` with no --log-root must read
        # PROVIDER_DEFAULT_ROOTS["grok"].
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "grok-home"
            self.write_json(self.session_dir(root) / "usage.json", usage_file([turn("2026-09-15T03:00:00.000000000+00:00", 321, 1)]))
            self.write_lines(
                root / "logs" / "unified.jsonl",
                [billing_line("2026-09-15T05:00:00.000Z", 12.5, "2026-09-22T16:01:55.200423+00:00")],
            )
            cache = Path(tmp) / "cache-grok.json"
            argv = [
                "codex_stats_helper.py", "--json", "--provider", "grok",
                "--cache-file", str(cache), "--now", NOW,
            ]
            original_argv = sys.argv
            original_roots = helper.PROVIDER_DEFAULT_ROOTS
            helper.PROVIDER_DEFAULT_ROOTS = dict(original_roots, grok=root)
            buffer = io.StringIO()
            try:
                sys.argv = argv
                with contextlib.redirect_stdout(buffer):
                    rc = helper.main()
            finally:
                sys.argv = original_argv
                helper.PROVIDER_DEFAULT_ROOTS = original_roots
            self.assertEqual(rc, 0)
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["today"]["total_tokens"], 321)
            self.assertEqual(payload["limits"]["secondary"]["used_percent"], 12.5)


if __name__ == "__main__":
    unittest.main()
