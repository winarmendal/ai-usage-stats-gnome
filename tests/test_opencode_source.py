from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HELPER_DIR = Path(__file__).resolve().parents[1] / "helper"
sys.path.insert(0, str(HELPER_DIR))

import codex_stats_helper as helper

NOW = "2026-09-15T20:00:00+07:00"
TODAY_MORNING = "2026-09-15T10:00:00+07:00"
YESTERDAY = "2026-09-14T10:00:00+07:00"


def ms(iso: str) -> int:
    return int(helper.parse_now(iso).timestamp() * 1000)


def assistant_data(
    created_iso: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    extra: dict | None = None,
) -> dict:
    created = ms(created_iso)
    data: dict = {
        "parentID": "msg_parent",
        "role": "assistant",
        "mode": "build",
        "agent": "build",
        "path": {"cwd": "/home/win/proj", "root": "/home/win/proj"},
        "cost": 0,
        # `tokens.total` is deliberately wrong here: the helper must sum the
        # components, exactly like the Claude adapter does.
        "tokens": {
            "total": 0,
            "input": input_tokens,
            "output": output_tokens,
            "reasoning": reasoning,
            "cache": {"write": cache_write, "read": cache_read},
        },
        "modelID": "muse-spark-1.3",
        "providerID": "opencode",
        "time": {"created": created, "completed": created + 1000},
        "finish": "stop",
    }
    if extra:
        data.update(extra)
    return data


def user_data(created_iso: str, text: str) -> dict:
    created = ms(created_iso)
    return {"role": "user", "time": {"created": created}, "summary": text}


class OpenCodeSourceTests(unittest.TestCase):
    # --- fixtures --------------------------------------------------------

    def create_v1(self, db: Path, rows: list[tuple[str, str, dict]], parts: list[str] | None = None) -> None:
        """OpenCode 1.x layout: one `message` table, role lives inside `data`."""
        db.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db)
        connection.execute(
            "CREATE TABLE message (id text PRIMARY KEY, session_id text NOT NULL, "
            "time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE part (id text PRIMARY KEY, message_id text NOT NULL, type text, text text)"
        )
        for index, text in enumerate(parts or []):
            connection.execute("INSERT INTO part VALUES (?,?,?,?)", (f"prt_{index}", "msg_1", "text", text))
        self.insert_v1(connection, rows)
        connection.commit()
        connection.close()

    def insert_v1(self, connection: sqlite3.Connection, rows: list[tuple[str, str, dict]]) -> None:
        for row_id, updated_iso, data in rows:
            updated = ms(updated_iso)
            created = int(data.get("time", {}).get("created") or updated)
            connection.execute(
                "INSERT OR REPLACE INTO message VALUES (?,?,?,?,?)",
                (row_id, "ses_1", created, updated, json.dumps(data)),
            )

    def create_v2(self, db: Path, rows: list[tuple[str, str, str, dict]]) -> None:
        """OpenCode 2.x layout: `session_message` with an explicit `type` column."""
        db.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db)
        connection.execute(
            "CREATE TABLE session_message (id text PRIMARY KEY, session_id text NOT NULL, type text NOT NULL, "
            "time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL, seq integer NOT NULL)"
        )
        for seq, (row_id, row_type, updated_iso, data) in enumerate(rows):
            updated = ms(updated_iso)
            created = int(data.get("time", {}).get("created") or updated)
            connection.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                (row_id, "ses_1", row_type, created, updated, json.dumps(data), seq),
            )
        connection.commit()
        connection.close()

    def build(self, root: Path, cache: Path, now: str = NOW, use_cache: bool = True) -> dict:
        return helper.build_payload(root, cache, use_cache, helper.parse_now(now), provider="opencode")

    def read_cache(self, cache: Path) -> dict:
        return json.loads(cache.read_text(encoding="utf-8"))

    # --- token bucketing -------------------------------------------------

    def test_v1_message_table_totals_and_user_rows_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            self.create_v1(
                root / "opencode.db",
                [
                    (
                        "msg_a",
                        TODAY_MORNING,
                        assistant_data(TODAY_MORNING, input_tokens=443, output_tokens=70, reasoning=193, cache_read=97905),
                    ),
                    ("msg_user", TODAY_MORNING, user_data(TODAY_MORNING, "hello")),
                ],
            )
            payload = self.build(root, cache)
            # 443 + 70 + 193 + 97905 (cache.write 0)
            self.assertEqual(payload["today"]["total_tokens"], 98611)
            self.assertEqual(payload["today"]["hourly"][10], 98611)
            self.assertTrue(payload["status"]["ok"])
            self.assertEqual(payload["status"]["opencode_db_status"], "ok")
            self.assertEqual(payload["status"]["opencode_rows_cached"], 1)
            # OpenCode keeps no rate-limit data on disk.
            self.assertIsNone(payload["limits"]["primary"]["used_percent"])
            self.assertIsNone(payload["limits"]["secondary"]["used_percent"])

    def test_v2_session_message_table_in_prod_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            self.create_v2(
                root / "opencode-prod.db",
                [
                    ("msg_a", "assistant", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=10, output_tokens=5)),
                    ("msg_switch", "model-switched", TODAY_MORNING, {"time": {"created": ms(TODAY_MORNING)}}),
                ],
            )
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 15)
            self.assertEqual(payload["status"]["opencode_db_status"], "ok")
            self.assertEqual(self.read_cache(cache)["db"], str(root / "opencode-prod.db"))

    def test_both_message_tables_in_one_database_are_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            db = root / "opencode.db"
            self.create_v1(db, [("msg_v1", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=100))])
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE session_message (id text PRIMARY KEY, session_id text NOT NULL, type text NOT NULL, "
                "time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL, seq integer NOT NULL)"
            )
            connection.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                (
                    "msg_v2",
                    "ses_1",
                    "assistant",
                    ms(TODAY_MORNING),
                    ms(TODAY_MORNING),
                    json.dumps(assistant_data(TODAY_MORNING, input_tokens=7)),
                    0,
                ),
            )
            connection.commit()
            connection.close()
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 107)

    def test_newest_database_candidate_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            stale = root / "opencode.db"
            fresh = root / "opencode-prod.db"
            self.create_v1(stale, [("msg_old", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=999))])
            self.create_v2(
                fresh,
                [("msg_new", "assistant", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=42))],
            )
            os.utime(stale, (1_700_000_000, 1_700_000_000))
            os.utime(fresh, (1_800_000_000, 1_800_000_000))
            self.assertEqual(helper.resolve_opencode_db(root), fresh)
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 42)

    def test_rows_without_tokens_or_created_time_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            no_tokens = assistant_data(TODAY_MORNING, input_tokens=5)
            no_tokens.pop("tokens")
            no_time = assistant_data(TODAY_MORNING, input_tokens=9)
            no_time.pop("time")
            self.create_v1(
                root / "opencode.db",
                [
                    ("msg_ok", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=11)),
                    ("msg_no_tokens", TODAY_MORNING, no_tokens),
                    ("msg_no_time", TODAY_MORNING, no_time),
                ],
            )
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 11)

    # --- streaming / incremental cache -----------------------------------

    def test_streaming_rewrite_keeps_last_write_and_advances_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            db = root / "opencode.db"
            self.create_v1(db, [("msg_a", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=10))])
            first = self.build(root, cache)
            self.assertEqual(first["today"]["total_tokens"], 10)
            self.assertEqual(self.read_cache(cache)["hwm"], ms(TODAY_MORNING))

            # OpenCode rewrites the same row as the response streams in.
            connection = sqlite3.connect(db)
            self.insert_v1(
                connection,
                [("msg_a", "2026-09-15T10:00:05+07:00", assistant_data(TODAY_MORNING, input_tokens=10, output_tokens=90))],
            )
            connection.commit()
            connection.close()

            second = self.build(root, cache)
            self.assertEqual(second["today"]["total_tokens"], 100)
            self.assertEqual(self.read_cache(cache)["hwm"], ms("2026-09-15T10:00:05+07:00"))
            self.assertEqual(second["status"]["opencode_rows_cached"], 1)

    def test_incremental_scan_only_fetches_rows_at_or_after_the_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            db = root / "opencode.db"
            self.create_v1(
                db,
                [
                    ("msg_1", YESTERDAY, assistant_data(YESTERDAY, input_tokens=1)),
                    ("msg_2", "2026-09-14T11:00:00+07:00", assistant_data(YESTERDAY, input_tokens=2)),
                ],
            )
            first = self.build(root, cache)
            self.assertEqual(first["status"]["opencode_rows_scanned"], 2)

            connection = sqlite3.connect(db)
            self.insert_v1(connection, [("msg_3", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=4))])
            connection.commit()
            connection.close()

            second = self.build(root, cache)
            # The watermark row itself is re-read (>=), so rows written in the same
            # millisecond are never skipped; msg_1 is not touched again.
            self.assertEqual(second["status"]["opencode_rows_scanned"], 2)
            self.assertEqual(second["status"]["opencode_rows_cached"], 3)
            self.assertEqual(second["today"]["total_tokens"], 4)

    def test_deleted_rows_are_retained_as_a_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            db = root / "opencode.db"
            self.create_v1(db, [("msg_a", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=555))])
            self.assertEqual(self.build(root, cache)["today"]["total_tokens"], 555)

            connection = sqlite3.connect(db)
            connection.execute("DELETE FROM message")
            connection.commit()
            connection.close()

            second = self.build(root, cache)
            self.assertEqual(second["today"]["total_tokens"], 555)
            self.assertEqual(second["status"]["opencode_rows_cached"], 1)

    def test_watermark_resets_when_the_database_path_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            old = root / "opencode.db"
            self.create_v1(old, [("msg_old", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=20))])
            self.build(root, cache)
            self.assertEqual(self.read_cache(cache)["hwm"], ms(TODAY_MORNING))

            # A 2.x upgrade introduces a second database whose watermark is unrelated.
            new = root / "opencode-prod.db"
            self.create_v2(
                new,
                [("msg_new", "assistant", YESTERDAY, assistant_data(YESTERDAY, input_tokens=30))],
            )
            os.utime(old, (1_700_000_000, 1_700_000_000))
            os.utime(new, (1_800_000_000, 1_800_000_000))

            second = self.build(root, cache)
            stored = self.read_cache(cache)
            self.assertEqual(stored["db"], str(new))
            self.assertEqual(stored["hwm"], ms(YESTERDAY))
            # The full rescan found msg_new even though its time_updated predates
            # the old database's watermark; msg_old stays in the ledger.
            self.assertEqual(second["status"]["opencode_rows_scanned"], 1)
            self.assertEqual(second["status"]["opencode_rows_cached"], 2)
            self.assertEqual(second["today"]["total_tokens"], 20)

    def test_no_cache_rescans_everything_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            self.create_v1(
                root / "opencode.db",
                [
                    ("msg_1", YESTERDAY, assistant_data(YESTERDAY, input_tokens=1)),
                    ("msg_2", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=2)),
                ],
            )
            payload = self.build(root, cache, use_cache=False)
            self.assertEqual(payload["status"]["opencode_rows_scanned"], 2)
            self.assertEqual(payload["today"]["total_tokens"], 2)
            self.assertFalse(cache.exists())

    # --- fork collapse ---------------------------------------------------

    def test_forked_duplicate_rows_collapse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            data = assistant_data(TODAY_MORNING, input_tokens=100, output_tokens=25)
            self.create_v1(
                root / "opencode.db",
                [("msg_original", TODAY_MORNING, data), ("msg_forked_copy", TODAY_MORNING, dict(data))],
            )
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 125)

            original = helper.OPENCODE_COLLAPSE_DUPLICATE_ROWS
            helper.OPENCODE_COLLAPSE_DUPLICATE_ROWS = False
            try:
                uncollapsed = self.build(root, Path(tmp) / "cache2.json")
            finally:
                helper.OPENCODE_COLLAPSE_DUPLICATE_ROWS = original
            self.assertEqual(uncollapsed["today"]["total_tokens"], 250)

    # --- database availability -------------------------------------------

    def test_missing_database_reports_not_ok_but_still_serves_the_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            db = root / "opencode.db"
            self.create_v1(db, [("msg_a", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=333))])
            self.build(root, cache)

            db.unlink()
            payload = self.build(root, cache)
            self.assertFalse(payload["status"]["ok"])
            self.assertIn("OpenCode database not found", payload["status"]["message"])
            self.assertEqual(payload["status"]["opencode_db_status"], "missing")
            self.assertEqual(payload["today"]["total_tokens"], 333)

    def test_busy_database_serves_cached_rows_without_advancing_the_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            db = root / "opencode.db"
            self.create_v1(db, [("msg_a", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=77))])
            self.build(root, cache)
            watermark = self.read_cache(cache)["hwm"]

            holder = sqlite3.connect(db, isolation_level=None)
            holder.execute("BEGIN EXCLUSIVE")
            try:
                payload = self.build(root, cache)
            finally:
                holder.execute("ROLLBACK")
                holder.close()

            self.assertEqual(payload["status"]["opencode_db_status"], "locked")
            self.assertEqual(payload["status"]["message"], "OpenCode database busy; showing cached usage")
            self.assertTrue(payload["status"]["ok"])
            self.assertEqual(payload["today"]["total_tokens"], 77)
            self.assertEqual(self.read_cache(cache)["hwm"], watermark)

    def test_schema_error_mid_scan_is_reported_not_disguised_as_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            db = root / "opencode.db"
            self.create_v1(db, [("msg_a", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=88))])
            # A future OpenCode schema without `time_updated` raises mid-scan,
            # after the `message` table has already been read in full.
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE session_message (id text PRIMARY KEY, session_id text NOT NULL, "
                "type text NOT NULL, time_created integer NOT NULL, data text NOT NULL)"
            )
            connection.commit()
            connection.close()

            payload = self.build(root, cache)
            self.assertEqual(payload["status"]["opencode_db_status"], "error")
            self.assertFalse(payload["status"]["ok"])
            self.assertIn("OpenCode database unreadable", payload["status"]["message"])
            # Rows read before the failure still count, and the watermark stays
            # put so the unscanned table is retried on the next refresh.
            self.assertEqual(payload["today"]["total_tokens"], 88)
            self.assertEqual(self.read_cache(cache)["hwm"], 0)

    # --- privacy ---------------------------------------------------------

    def test_privacy_message_text_never_leaves_the_database(self) -> None:
        part_secret = "OPENCODE-PART-TEXT-SECRET"
        user_secret = "OPENCODE-USER-PROMPT-SECRET"
        error_secret = "OPENCODE-ASSISTANT-ERROR-SECRET"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            self.create_v1(
                root / "opencode.db",
                [
                    (
                        "msg_a",
                        TODAY_MORNING,
                        assistant_data(
                            TODAY_MORNING,
                            input_tokens=60,
                            output_tokens=40,
                            extra={"error": {"name": "Err", "message": error_secret}, "summary": error_secret},
                        ),
                    ),
                    ("msg_user", TODAY_MORNING, user_data(TODAY_MORNING, user_secret)),
                ],
                parts=[part_secret],
            )
            payload = self.build(root, cache)
            rendered = json.dumps(payload)
            cached = cache.read_text(encoding="utf-8")
            for secret in (part_secret, user_secret, error_secret):
                self.assertNotIn(secret, rendered)
                self.assertNotIn(secret, cached)
            # The ledger stores only [timestamp, total] per message id.
            self.assertNotIn('"data"', cached)
            self.assertNotIn("modelID", cached)
            self.assertEqual(payload["today"]["total_tokens"], 100)

    def test_part_table_is_never_queried(self) -> None:
        # A database whose `part` table is unreadable must still aggregate: proof
        # the helper never touches it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            cache = Path(tmp) / "cache.json"
            db = root / "opencode.db"
            self.create_v1(db, [("msg_a", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=8))])
            connection = sqlite3.connect(db)
            connection.execute("DROP TABLE part")
            connection.commit()
            connection.close()
            payload = self.build(root, cache)
            self.assertEqual(payload["today"]["total_tokens"], 8)
            self.assertEqual(payload["status"]["opencode_db_status"], "ok")

    # --- CLI wiring ------------------------------------------------------

    def test_main_uses_opencode_default_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode-home"
            self.create_v1(
                root / "opencode.db",
                [("msg_a", TODAY_MORNING, assistant_data(TODAY_MORNING, input_tokens=90, output_tokens=10))],
            )
            cache = Path(tmp) / "cache-opencode.json"
            argv = [
                "codex_stats_helper.py", "--json", "--provider", "opencode",
                "--cache-file", str(cache), "--now", NOW,
            ]
            original_argv = sys.argv
            original_roots = helper.PROVIDER_DEFAULT_ROOTS
            helper.PROVIDER_DEFAULT_ROOTS = dict(original_roots, opencode=root)
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
            self.assertEqual(payload["today"]["total_tokens"], 100)
            self.assertEqual(payload["status"]["opencode_db_status"], "ok")


if __name__ == "__main__":
    unittest.main()
