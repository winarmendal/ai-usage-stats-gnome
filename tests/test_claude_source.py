from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HELPER_DIR = Path(__file__).resolve().parents[1] / "helper"
sys.path.insert(0, str(HELPER_DIR))

import codex_stats_helper as helper

WRAPPER = HELPER_DIR / "claude_statusline_capture.py"


def claude_row(
    timestamp: str,
    total: int,
    session_id: str = "sess-1",
    request_id: str | None = "req-1",
    message_id: str = "msg-1",
    output_tokens: int | None = None,
    content: str | None = None,
) -> dict:
    # total is split across the four usage fields the helper sums.
    out = 1 if output_tokens is None else output_tokens
    usage = {
        "input_tokens": max(0, total - out),
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    message: dict = {"id": message_id, "role": "assistant", "usage": usage}
    if content is not None:
        message["content"] = [{"type": "text", "text": content}]
    row: dict = {
        "type": "assistant",
        "timestamp": timestamp,
        "sessionId": session_id,
        "message": message,
    }
    if request_id is not None:
        row["requestId"] = request_id
    return row


def capture_file(rate_limits: dict, captured_at: float) -> dict:
    return {"captured_at": captured_at, "rate_limits": rate_limits}


def rate_limits(five: float = 11.0, seven: float = 31.0) -> dict:
    return {
        "five_hour": {"used_percentage": five, "resets_at": 1781000000},
        "seven_day": {"used_percentage": seven, "resets_at": 1781400000},
    }


def online_response(
    five: float = 20.0,
    seven: float = 36.0,
    sonnet: float | None = 4.0,
    opus: float | None = None,
    extra: dict | None = None,
) -> dict:
    # Shape of GET https://api.anthropic.com/api/oauth/usage (utilization 0-100,
    # resets_at as ISO-8601). seven_day_sonnet / seven_day_opus are the per-model
    # weekly buckets claude.ai surfaces as "Sonnet only".
    resp: dict = {
        "five_hour": {"utilization": five, "resets_at": "2026-06-25T23:49:59.074Z"},
        "seven_day": {"utilization": seven, "resets_at": "2026-06-28T02:59:59.074Z"},
    }
    if sonnet is not None:
        resp["seven_day_sonnet"] = {"utilization": sonnet, "resets_at": "2026-06-28T03:00:00.074Z"}
    if opus is not None:
        resp["seven_day_opus"] = {"utilization": opus, "resets_at": "2026-06-28T03:00:00.074Z"}
    if extra:
        resp.update(extra)
    return resp


class _Opener:
    # Injectable stand-in for the HTTPS fetch; counts calls, never touches network.
    def __init__(self, result: tuple) -> None:
        self.result = result
        self.calls = 0
        self.token: str | None = None

    def __call__(self, token: str) -> tuple:
        self.calls += 1
        self.token = token
        return self.result


def _raise_opener(token: str) -> tuple:
    raise AssertionError("online fetch must not happen on this path")


class ClaudeSourceTests(unittest.TestCase):
    def write_jsonl(self, path: Path, rows: list[dict | str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write((row if isinstance(row, str) else json.dumps(row)) + "\n")

    def build(self, root: Path, cache: Path, now: str, limits_file: Path | None = None) -> dict:
        return helper.build_payload(
            root, cache, True, helper.parse_now(now), provider="claude", limits_file=limits_file
        )

    def test_dedup_last_wins_within_response(self) -> None:
        # One API response written across 3 streaming rows (out grows 1 -> 139),
        # plus a distinct response. Naive sum would triple-count the first.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                root / "p" / "sess.jsonl",
                [
                    claude_row("2026-06-25T10:00:00+07:00", 100, request_id="req-A", output_tokens=1),
                    claude_row("2026-06-25T10:00:01+07:00", 200, request_id="req-A", output_tokens=70),
                    claude_row("2026-06-25T10:00:02+07:00", 339, request_id="req-A", output_tokens=139),
                    claude_row("2026-06-25T11:00:00+07:00", 50, request_id="req-B"),
                ],
            )
            payload = self.build(root, cache, "2026-06-25T12:00:00+07:00")
            # req-A counts once as 339 (last==max), req-B as 50 -> 389, not 689.
            self.assertEqual(payload["today"]["total_tokens"], 389)
            self.assertEqual(payload["today"]["hourly"][10], 339)
            self.assertEqual(payload["today"]["hourly"][11], 50)

    def test_cross_file_dedup_same_request(self) -> None:
        # Same (sessionId, requestId) duplicated into a second file (resume/fork).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            cache = Path(tmp) / "cache.json"
            row = claude_row("2026-06-25T10:00:00+07:00", 500, session_id="s9", request_id="req-X")
            self.write_jsonl(root / "a.jsonl", [row])
            self.write_jsonl(root / "sub" / "b.jsonl", [row])
            payload = self.build(root, cache, "2026-06-25T12:00:00+07:00")
            self.assertEqual(payload["today"]["total_tokens"], 500)

    def test_requestid_absent_falls_back_to_message_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                root / "s.jsonl",
                [
                    claude_row("2026-06-25T10:00:00+07:00", 100, request_id=None, message_id="m1"),
                    claude_row("2026-06-25T10:00:00+07:00", 100, request_id=None, message_id="m1"),
                    claude_row("2026-06-25T10:00:00+07:00", 70, request_id=None, message_id="m2"),
                ],
            )
            payload = self.build(root, cache, "2026-06-25T12:00:00+07:00")
            # m1 deduped to 100, m2 = 70 -> 170.
            self.assertEqual(payload["today"]["total_tokens"], 170)

    def test_unkeyable_rows_are_not_merged(self) -> None:
        # Defensive: rows with no sessionId/requestId/message.id (never seen in
        # real data) must each count, not collapse into one under-counted turn.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                root / "s.jsonl",
                [
                    claude_row("2026-06-25T10:00:00+07:00", 100, session_id=None, request_id=None, message_id=None),
                    claude_row("2026-06-25T10:30:00+07:00", 200, session_id=None, request_id=None, message_id=None),
                ],
            )
            payload = self.build(root, cache, "2026-06-25T12:00:00+07:00")
            self.assertEqual(payload["today"]["total_tokens"], 300)

    def test_millisecond_z_timestamp_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            cache = Path(tmp) / "cache.json"
            # UTC 03:14:33.486Z == 10:14 in +07:00.
            self.write_jsonl(
                root / "s.jsonl",
                [claude_row("2026-06-25T03:14:33.486Z", 123, request_id="r")],
            )
            payload = self.build(root, cache, "2026-06-25T20:00:00+07:00")
            self.assertEqual(payload["today"]["total_tokens"], 123)
            self.assertEqual(payload["today"]["hourly"][10], 123)

    def test_limits_from_fresh_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            root.mkdir(parents=True)
            cache = Path(tmp) / "cache.json"
            limits = Path(tmp) / "claude-limits.json"
            now = "2026-06-25T12:00:00+07:00"
            captured_at = helper.parse_now(now).timestamp() - 120  # 2 min old
            limits.write_text(json.dumps(capture_file(rate_limits(11.0, 31.0), captured_at)))
            payload = self.build(root, cache, now, limits_file=limits)
            self.assertEqual(payload["limits"]["primary"]["label"], "5h")
            self.assertEqual(payload["limits"]["primary"]["remaining_percent"], 89.0)
            self.assertEqual(payload["limits"]["primary"]["source"], "statusline")
            self.assertEqual(payload["limits"]["secondary"]["label"], "Week")
            self.assertEqual(payload["limits"]["secondary"]["remaining_percent"], 69.0)

    def test_stale_capture_degrades_to_dashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            root.mkdir(parents=True)
            cache = Path(tmp) / "cache.json"
            limits = Path(tmp) / "claude-limits.json"
            now = "2026-06-25T12:00:00+07:00"
            captured_at = helper.parse_now(now).timestamp() - (25 * 3600)  # >24h old
            limits.write_text(json.dumps(capture_file(rate_limits(), captured_at)))
            payload = self.build(root, cache, now, limits_file=limits)
            self.assertEqual(payload["limits"]["primary"]["label"], "--")
            self.assertIsNone(payload["limits"]["primary"]["remaining_percent"])

    def test_no_capture_renders_dashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            root.mkdir(parents=True)
            cache = Path(tmp) / "cache.json"
            missing = Path(tmp) / "does-not-exist.json"
            # hud_dir empty so the fallback finds nothing.
            empty_hud = Path(tmp) / "hud"
            empty_hud.mkdir()
            snaps, _ = helper.collect_claude_limit_snapshots(missing, helper.parse_now("2026-06-25T12:00:00+07:00"), hud_dir=empty_hud)
            self.assertEqual(snaps, {})
            payload = self.build(root, cache, "2026-06-25T12:00:00+07:00", limits_file=missing)
            self.assertEqual(payload["limits"]["secondary"]["label"], "--")

    def test_hud_fallback_skips_files_without_rate_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hud = Path(tmp) / "hud"
            hud.mkdir()
            now = helper.parse_now("2026-06-25T12:00:00+07:00")
            now_ts = now.timestamp()
            # Newest file has NO rate_limits; older file has them. Older must win.
            newest = hud / "stdin.newest.json"
            older = hud / "stdin.older.json"
            newest.write_text(json.dumps({"cost": {"total_cost_usd": 1.0}}))
            older.write_text(json.dumps({"rate_limits": rate_limits(22.0, 5.0)}))
            os.utime(newest, (now_ts - 60, now_ts - 60))
            os.utime(older, (now_ts - 600, now_ts - 600))
            snaps, stats = helper.collect_claude_limit_snapshots(Path(tmp) / "none.json", now, hud_dir=hud)
            self.assertIn("primary", snaps)
            self.assertEqual(snaps["primary"].used_percent, 22.0)
            self.assertEqual(snaps["primary"].source, "statusline")
            self.assertEqual(stats["claude_limit_snapshots"], 2)

    def test_missing_vs_empty_root_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            present = Path(tmp) / "projects"
            present.mkdir()
            payload = self.build(present, cache, "2026-06-25T12:00:00+07:00")
            self.assertTrue(payload["status"]["ok"])
            self.assertEqual(payload["today"]["total_tokens"], 0)

            missing = Path(tmp) / "missing"
            payload = self.build(missing, cache, "2026-06-25T12:00:00+07:00")
            self.assertFalse(payload["status"]["ok"])
            self.assertIn("Log root not found", payload["status"]["message"])

    def test_privacy_no_transcript_text_in_output_or_cache(self) -> None:
        secret = "TOP-SECRET-PROMPT-DO-NOT-LEAK"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                root / "s.jsonl",
                [claude_row("2026-06-25T10:00:00+07:00", 100, request_id="r", content=secret)],
            )
            payload = self.build(root, cache, "2026-06-25T12:00:00+07:00")
            self.assertNotIn(secret, json.dumps(payload))
            self.assertNotIn(secret, cache.read_text())
            self.assertEqual(payload["today"]["total_tokens"], 100)

    def test_per_provider_cache_files_do_not_evict(self) -> None:
        # Separate cache files mean a codex run never evicts the claude cache.
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            cache_claude = Path(tmp) / "cache-claude.json"
            cache_codex = Path(tmp) / "cache-codex.json"
            self.write_jsonl(root := claude_root / "s.jsonl", [claude_row("2026-06-25T10:00:00+07:00", 100, request_id="r")])
            self.build(claude_root, cache_claude, "2026-06-25T12:00:00+07:00")
            self.assertTrue(cache_claude.exists())
            claude_cache_before = json.loads(cache_claude.read_text())
            self.assertTrue(claude_cache_before["files"])

            # A codex run on its own cache file.
            codex_root = Path(tmp) / "sessions"
            codex_root.mkdir()
            helper.build_payload(codex_root, cache_codex, True, helper.parse_now("2026-06-25T12:00:00+07:00"))

            # Claude cache untouched by the codex run.
            self.assertEqual(json.loads(cache_claude.read_text()), claude_cache_before)

    def test_wrapper_captures_numeric_only_and_chains_stdin_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cap = Path(tmp) / "claude-limits.json"
            stdin_payload = json.dumps(
                {
                    "rate_limits": {"five_hour": {"used_percentage": 33, "resets_at": 1}},
                    "cost": {"total_cost_usd": 2.5},
                    "context_window": {"used_percentage": 10},
                    "prompt": "SECRET-PROMPT",
                    "transcript": "SECRET-TRANSCRIPT",
                }
            )
            echo = [sys.executable, "-c", "import sys;sys.stdout.write(sys.stdin.read())"]
            proc = subprocess.run(
                [sys.executable, str(WRAPPER), "--capture", str(cap), "--", *echo],
                input=stdin_payload.encode(),
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0)
            # Chain-through forwards the same stdin byte-for-byte to the prior command's stdout.
            self.assertEqual(proc.stdout.decode(), stdin_payload)
            data = json.loads(cap.read_text())
            self.assertIn("captured_at", data)
            self.assertEqual(data["rate_limits"]["five_hour"]["used_percentage"], 33)
            self.assertEqual(data["cost"]["total_cost_usd"], 2.5)
            # Privacy: no prompt/transcript captured.
            raw = cap.read_text()
            self.assertNotIn("SECRET-PROMPT", raw)
            self.assertNotIn("SECRET-TRANSCRIPT", raw)
            self.assertNotIn("prompt", data)

    def test_wrapper_tolerates_invalid_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cap = Path(tmp) / "claude-limits.json"
            proc = subprocess.run(
                [sys.executable, str(WRAPPER), "--capture", str(cap)],
                input=b"not-json",
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0)
            data = json.loads(cap.read_text())
            self.assertIn("captured_at", data)
            self.assertNotIn("rate_limits", data)


class ClaudeOnlineTests(unittest.TestCase):
    def setUp(self) -> None:
        # Neutralize the real HUD fallback dir so these tests never read live
        # machine state (online is the source under test here).
        self._hud_tmp = tempfile.TemporaryDirectory()
        self._orig_hud = helper.DEFAULT_CLAUDE_HUD_CACHE_DIR
        helper.DEFAULT_CLAUDE_HUD_CACHE_DIR = Path(self._hud_tmp.name)

    def tearDown(self) -> None:
        helper.DEFAULT_CLAUDE_HUD_CACHE_DIR = self._orig_hud
        self._hud_tmp.cleanup()

    def write_creds(self, path: Path, now_ts: float, expired: bool = False) -> None:
        expires_ms = (now_ts - 10 if expired else now_ts + 3600) * 1000
        path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-XYZ", "expiresAt": expires_ms}}))

    def build_online(self, root, cache, now, creds, opener, online_cache, limits_file=None) -> dict:
        return helper.build_payload(
            root, cache, True, helper.parse_now(now), provider="claude",
            limits_file=limits_file, claude_online=True, claude_creds_file=creds,
            claude_online_cache=online_cache, claude_online_opener=opener,
        )

    def test_parse_iso_resets_and_windows(self) -> None:
        now = helper.parse_now("2026-06-26T04:00:00+07:00")
        snaps = helper.claude_online_snapshots_from_response(
            online_response(opus=2.0), now.timestamp(), now.tzinfo
        )
        self.assertEqual(snaps["primary"].window_minutes, 300)
        self.assertEqual(snaps["primary"].used_percent, 20.0)
        self.assertEqual(snaps["secondary"].window_minutes, 10080)
        self.assertEqual(snaps["sonnet_weekly"].window_minutes, 10080)
        self.assertEqual(snaps["sonnet_weekly"].used_percent, 4.0)
        self.assertEqual(snaps["opus_weekly"].used_percent, 2.0)
        self.assertEqual(snaps["primary"].source, "online")
        # ISO reset string parsed to a real epoch (23:49:59.074Z).
        self.assertAlmostEqual(snaps["primary"].resets_at, 1782431399.074, places=1)

    def test_online_not_started_sentinel_is_absent(self) -> None:
        now = helper.parse_now("2026-06-26T04:00:00+07:00")
        resp = online_response(opus=None)
        # API hides an unused per-model bucket as {utilization: 0, resets_at: null}.
        resp["seven_day_opus"] = {"utilization": 0, "resets_at": None}
        snaps = helper.claude_online_snapshots_from_response(resp, now.timestamp(), now.tzinfo)
        self.assertNotIn("opus_weekly", snaps)
        self.assertIn("sonnet_weekly", snaps)
        # But a genuine 0% with a real reset window is kept (just-reset, not hidden).
        resp["seven_day_opus"] = {"utilization": 0, "resets_at": "2026-06-28T03:00:00.074Z"}
        snaps2 = helper.claude_online_snapshots_from_response(resp, now.timestamp(), now.tzinfo)
        self.assertIn("opus_weekly", snaps2)
        self.assertEqual(snaps2["opus_weekly"].used_percent, 0.0)

    def test_online_populates_buckets_incl_per_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"; root.mkdir(parents=True)
            cache = Path(tmp) / "cache.json"
            ocache = Path(tmp) / "online.json"
            now = "2026-06-26T04:00:00+07:00"
            creds = Path(tmp) / ".credentials.json"
            self.write_creds(creds, helper.parse_now(now).timestamp())
            opener = _Opener(("ok", online_response(opus=2.0)))
            payload = self.build_online(root, cache, now, creds, opener, ocache)
            lim = payload["limits"]
            self.assertEqual(lim["primary"]["used_percent"], 20.0)
            self.assertEqual(lim["primary"]["source"], "online")
            self.assertEqual(lim["primary"]["label"], "5h")
            self.assertEqual(lim["secondary"]["used_percent"], 36.0)
            self.assertEqual(lim["sonnet_weekly"]["used_percent"], 4.0)
            self.assertEqual(lim["sonnet_weekly"]["source"], "online")
            self.assertEqual(lim["opus_weekly"]["used_percent"], 2.0)
            self.assertEqual(opener.calls, 1)
            self.assertEqual(payload["status"]["claude_online_status"], "ok")

    def test_online_overrides_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"; root.mkdir(parents=True)
            cache = Path(tmp) / "cache.json"
            ocache = Path(tmp) / "online.json"
            now = "2026-06-26T04:00:00+07:00"
            now_ts = helper.parse_now(now).timestamp()
            limits = Path(tmp) / "claude-limits.json"
            cap_rl = {
                "five_hour": {"used_percentage": 11.0, "resets_at": now_ts + 3000},
                "seven_day": {"used_percentage": 31.0, "resets_at": now_ts + 300000},
            }
            limits.write_text(json.dumps(capture_file(cap_rl, now_ts - 120)))
            creds = Path(tmp) / ".credentials.json"
            self.write_creds(creds, now_ts)
            opener = _Opener(("ok", online_response(five=20.0, seven=36.0)))
            payload = self.build_online(root, cache, now, creds, opener, ocache, limits_file=limits)
            # Live API wins over a still-fresh statusline capture.
            self.assertEqual(payload["limits"]["primary"]["used_percent"], 20.0)
            self.assertEqual(payload["limits"]["primary"]["source"], "online")

    def test_online_disabled_no_fetch_no_per_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"; root.mkdir(parents=True)
            cache = Path(tmp) / "cache.json"
            creds = Path(tmp) / ".credentials.json"
            now = "2026-06-26T04:00:00+07:00"
            self.write_creds(creds, helper.parse_now(now).timestamp())
            payload = helper.build_payload(
                root, cache, True, helper.parse_now(now), provider="claude",
                claude_online=False, claude_creds_file=creds, claude_online_opener=_raise_opener,
            )
            self.assertNotIn("sonnet_weekly", payload["limits"])
            self.assertEqual(payload["status"]["claude_online_requests"], 0)

    def test_online_token_expired_skips_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"; root.mkdir(parents=True)
            cache = Path(tmp) / "cache.json"
            ocache = Path(tmp) / "online.json"
            now = "2026-06-26T04:00:00+07:00"
            creds = Path(tmp) / ".credentials.json"
            self.write_creds(creds, helper.parse_now(now).timestamp(), expired=True)
            payload = self.build_online(root, cache, now, creds, _raise_opener, ocache)
            self.assertEqual(payload["status"]["claude_online_status"], "token-expired")
            self.assertEqual(payload["limits"]["primary"]["label"], "--")

    def test_online_throttle_serves_cache_within_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ocache = Path(tmp) / "online.json"
            creds = Path(tmp) / ".credentials.json"
            now = helper.parse_now("2026-06-26T04:00:00+07:00")
            self.write_creds(creds, now.timestamp())
            opener = _Opener(("ok", online_response()))
            helper.collect_claude_online_snapshots(creds, ocache, now, True, opener=opener)
            snaps, stats = helper.collect_claude_online_snapshots(creds, ocache, now, True, opener=opener)
            self.assertEqual(opener.calls, 1)  # second call served from the throttle cache
            self.assertEqual(stats["claude_online_status"], "cache")
            self.assertEqual(snaps["primary"].used_percent, 20.0)

    def test_online_rate_limited_serves_cache_and_backs_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ocache = Path(tmp) / "online.json"
            creds = Path(tmp) / ".credentials.json"
            now = helper.parse_now("2026-06-26T04:00:00+07:00")
            now_ts = now.timestamp()
            self.write_creds(creds, now_ts)
            # Stale-but-present cache (older than TTL) with prior data.
            ocache.write_text(json.dumps({
                "fetched_at": now_ts - 120,
                "data": helper.project_claude_online_response(online_response()),
                "backoff_until": 0,
            }))
            opener = _Opener(("rate_limited", None))
            snaps, stats = helper.collect_claude_online_snapshots(creds, ocache, now, True, opener=opener)
            self.assertEqual(stats["claude_online_status"], "rate-limited")
            self.assertEqual(snaps["primary"].used_percent, 20.0)  # served from cache
            disk = json.loads(ocache.read_text())
            self.assertGreater(disk["backoff_until"], now_ts)

    def test_online_cache_is_numeric_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ocache = Path(tmp) / "online.json"
            creds = Path(tmp) / ".credentials.json"
            now = helper.parse_now("2026-06-26T04:00:00+07:00")
            self.write_creds(creds, now.timestamp())
            junk = {"account_email": "user@example.com", "organization": "SECRET-ORG"}
            opener = _Opener(("ok", online_response(extra=junk)))
            helper.collect_claude_online_snapshots(creds, ocache, now, True, opener=opener)
            raw = ocache.read_text()
            self.assertNotIn("user@example.com", raw)
            self.assertNotIn("SECRET-ORG", raw)
            self.assertNotIn("account_email", raw)

    def test_online_never_writes_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ocache = Path(tmp) / "online.json"
            creds = Path(tmp) / ".credentials.json"
            now = helper.parse_now("2026-06-26T04:00:00+07:00")
            self.write_creds(creds, now.timestamp())
            before = creds.read_bytes()
            mtime_before = creds.stat().st_mtime_ns
            opener = _Opener(("ok", online_response()))
            helper.collect_claude_online_snapshots(creds, ocache, now, True, opener=opener)
            self.assertEqual(creds.read_bytes(), before)
            self.assertEqual(creds.stat().st_mtime_ns, mtime_before)


if __name__ == "__main__":
    unittest.main()
