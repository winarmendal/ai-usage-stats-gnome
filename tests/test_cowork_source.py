from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HELPER_DIR = Path(__file__).resolve().parents[1] / "helper"
sys.path.insert(0, str(HELPER_DIR))

import codex_stats_helper as helper


def cowork_row(
    timestamp: str,
    total: int,
    session_id: str | None = "cw-sess-1",
    request_id: str | None = "cw-req-1",
    message_id: str = "cw-msg-1",
    output_tokens: int | None = None,
    content: str | None = None,
) -> dict:
    # Cowork agent-mode audit.jsonl shape: snake_case ids + _audit_timestamp, no
    # top-level `timestamp`. total is split across the four usage fields summed.
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
        "_audit_timestamp": timestamp,
        "message": message,
    }
    if session_id is not None:
        row["session_id"] = session_id
    if request_id is not None:
        row["request_id"] = request_id
    return row


def code_row(
    timestamp: str,
    total: int,
    session_id: str = "code-sess-1",
    request_id: str | None = "code-req-1",
    message_id: str = "code-msg-1",
    content: str | None = None,
) -> dict:
    # Claude Code projects JSONL shape: camelCase ids + `timestamp`.
    usage = {
        "input_tokens": max(0, total - 1),
        "output_tokens": 1,
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


class CoworkSourceTests(unittest.TestCase):
    def write_jsonl(self, path: Path, rows: list[dict | str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write((row if isinstance(row, str) else json.dumps(row)) + "\n")

    def build(self, root: Path, cache: Path, now: str, cowork_root: Path | None = None) -> dict:
        return helper.build_payload(
            root, cache, True, helper.parse_now(now), provider="claude", cowork_root=cowork_root
        )

    def test_cowork_snake_case_and_audit_timestamp_parse(self) -> None:
        # AC-2: snake_case session_id/request_id + _audit_timestamp must parse and
        # bucket. UTC 03:14Z == 10:14 in +07:00.
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            claude_root.mkdir(parents=True)
            cowork_root = Path(tmp) / "cowork"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                cowork_root / "acct" / "org" / "local_a" / "audit.jsonl",
                [cowork_row("2026-06-25T03:14:00.226Z", 123, request_id="r1")],
            )
            payload = self.build(claude_root, cache, "2026-06-25T20:00:00+07:00", cowork_root=cowork_root)
            self.assertEqual(payload["today"]["total_tokens"], 123)
            self.assertEqual(payload["today"]["hourly"][10], 123)

    def test_cowork_folds_into_claude_total(self) -> None:
        # AC-5: Claude Code + Cowork merge into ONE Claude total.
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            cowork_root = Path(tmp) / "cowork"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                claude_root / "p" / "s.jsonl",
                [code_row("2026-06-25T03:00:00Z", 100, request_id="code-1")],
            )
            self.write_jsonl(
                cowork_root / "acct" / "org" / "local_a" / "audit.jsonl",
                [cowork_row("2026-06-25T03:00:00Z", 40, request_id="cw-1")],
            )
            payload = self.build(claude_root, cache, "2026-06-25T20:00:00+07:00", cowork_root=cowork_root)
            self.assertEqual(payload["today"]["total_tokens"], 140)

    def test_cowork_dedup_restated_rows_count_once(self) -> None:
        # AC-3: within a (session_id, request_id) group Cowork restates identical
        # totals across streaming rows; count once (max), not summed.
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            claude_root.mkdir(parents=True)
            cowork_root = Path(tmp) / "cowork"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                cowork_root / "s" / "audit.jsonl",
                [
                    cowork_row("2026-06-25T03:00:00Z", 116142, request_id="req-A", output_tokens=100),
                    cowork_row("2026-06-25T03:00:01Z", 116142, request_id="req-A", output_tokens=100),
                ],
            )
            payload = self.build(claude_root, cache, "2026-06-25T20:00:00+07:00", cowork_root=cowork_root)
            self.assertEqual(payload["today"]["total_tokens"], 116142)

    def test_cowork_result_line_not_counted(self) -> None:
        # AC-4: `result` lines restate per-turn usage; only `assistant` lines count.
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            claude_root.mkdir(parents=True)
            cowork_root = Path(tmp) / "cowork"
            cache = Path(tmp) / "cache.json"
            result_line = {
                "type": "result",
                "session_id": "s",
                "_audit_timestamp": "2026-06-25T03:00:00Z",
                "usage": {
                    "input_tokens": 500000,
                    "output_tokens": 500000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }
            self.write_jsonl(
                cowork_root / "s" / "audit.jsonl",
                [cowork_row("2026-06-25T03:00:00Z", 100, request_id="r1"), result_line],
            )
            payload = self.build(claude_root, cache, "2026-06-25T20:00:00+07:00", cowork_root=cowork_root)
            self.assertEqual(payload["today"]["total_tokens"], 100)

    def test_stray_nested_transcript_not_counted_or_read(self) -> None:
        # AC-6b (THE blocker guard): the Cowork root also nests full Claude Code
        # transcripts at local_x/.claude/projects/**/*.jsonl (camelCase, with prompt
        # text). Scanning *.jsonl would 2.4x-inflate the total AND open transcript
        # files. Name-scoping to audit.jsonl must exclude them entirely.
        secret = "STRAY-NESTED-TRANSCRIPT-SECRET"
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            claude_root.mkdir(parents=True)
            cowork_root = Path(tmp) / "cowork"
            cache = Path(tmp) / "cache.json"
            session_dir = cowork_root / "acct" / "org" / "local_a"
            # canonical ledger (counts)
            self.write_jsonl(session_dir / "audit.jsonl", [cowork_row("2026-06-25T03:00:00Z", 100, request_id="r1")])
            # stray nested Claude Code transcript (MUST be ignored: 999999 tokens + secret)
            self.write_jsonl(
                session_dir / ".claude" / "projects" / "p" / "y.jsonl",
                [code_row("2026-06-25T03:00:00Z", 999999, request_id="stray", content=secret)],
            )
            payload = self.build(claude_root, cache, "2026-06-25T20:00:00+07:00", cowork_root=cowork_root)
            self.assertEqual(payload["today"]["total_tokens"], 100)  # stray 999999 excluded
            self.assertNotIn(secret, json.dumps(payload))
            self.assertNotIn(secret, cache.read_text())

    def test_only_files_named_audit_jsonl_are_scanned(self) -> None:
        # A sibling *.jsonl that is not literally `audit.jsonl` must be ignored.
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            claude_root.mkdir(parents=True)
            cowork_root = Path(tmp) / "cowork"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(cowork_root / "s" / "audit.jsonl", [cowork_row("2026-06-25T03:00:00Z", 100, request_id="r1")])
            self.write_jsonl(cowork_root / "s" / "session.jsonl", [cowork_row("2026-06-25T03:00:00Z", 500, request_id="r2")])
            payload = self.build(claude_root, cache, "2026-06-25T20:00:00+07:00", cowork_root=cowork_root)
            self.assertEqual(payload["today"]["total_tokens"], 100)

    def test_cowork_privacy_no_transcript_text_in_output_or_cache(self) -> None:
        # AC-6: even a counted audit.jsonl line's content text is never emitted.
        secret = "COWORK-SECRET-PROMPT-DO-NOT-LEAK"
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            claude_root.mkdir(parents=True)
            cowork_root = Path(tmp) / "cowork"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                cowork_root / "s" / "audit.jsonl",
                [cowork_row("2026-06-25T03:00:00Z", 100, request_id="r1", content=secret)],
            )
            payload = self.build(claude_root, cache, "2026-06-25T20:00:00+07:00", cowork_root=cowork_root)
            self.assertNotIn(secret, json.dumps(payload))
            self.assertNotIn(secret, cache.read_text())
            self.assertEqual(payload["today"]["total_tokens"], 100)

    def test_absent_cowork_root_is_noop(self) -> None:
        # AC-7: no cowork_root (None) and a nonexistent root both leave the
        # Claude-Code-only total unchanged and status ok.
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            cache = Path(tmp) / "cache.json"
            self.write_jsonl(
                claude_root / "p" / "s.jsonl",
                [code_row("2026-06-25T03:00:00Z", 77, request_id="code-1")],
            )
            base = self.build(claude_root, cache, "2026-06-25T20:00:00+07:00", cowork_root=None)
            self.assertEqual(base["today"]["total_tokens"], 77)
            self.assertTrue(base["status"]["ok"])

            missing = Path(tmp) / "no-such-cowork"
            cache2 = Path(tmp) / "cache2.json"
            withmissing = self.build(claude_root, cache2, "2026-06-25T20:00:00+07:00", cowork_root=missing)
            self.assertEqual(withmissing["today"]["total_tokens"], 77)
            self.assertTrue(withmissing["status"]["ok"])

    def test_main_defaults_cowork_root_when_flag_absent(self) -> None:
        # Guards the auto-on wiring in main(): with no --cowork-log-root the helper
        # must scan DEFAULT_CLAUDE_COWORK_LOG_ROOT. A future edit dropping that
        # default would silently disable Cowork for real users while the rest of
        # the suite (which injects cowork_root directly) stayed green.
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "projects"
            self.write_jsonl(
                claude_root / "p" / "s.jsonl",
                [code_row("2026-06-25T03:00:00Z", 100, request_id="code-1")],
            )
            cowork_default = Path(tmp) / "cowork-default"
            self.write_jsonl(
                cowork_default / "acct" / "local_a" / "audit.jsonl",
                [cowork_row("2026-06-25T03:00:00Z", 40, request_id="cw-1")],
            )
            cache = Path(tmp) / "cache.json"
            argv = [
                "codex_stats_helper.py", "--json", "--provider", "claude",
                "--log-root", str(claude_root), "--cache-file", str(cache),
                "--no-cache", "--now", "2026-06-25T20:00:00+07:00",
            ]
            orig_argv = sys.argv
            orig_default = helper.DEFAULT_CLAUDE_COWORK_LOG_ROOT
            helper.DEFAULT_CLAUDE_COWORK_LOG_ROOT = cowork_default
            buf = io.StringIO()
            try:
                sys.argv = argv
                with contextlib.redirect_stdout(buf):
                    rc = helper.main()
            finally:
                sys.argv = orig_argv
                helper.DEFAULT_CLAUDE_COWORK_LOG_ROOT = orig_default
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            # Code (100) + Cowork from the patched default (40), folded via main().
            self.assertEqual(payload["today"]["total_tokens"], 140)


if __name__ == "__main__":
    unittest.main()
