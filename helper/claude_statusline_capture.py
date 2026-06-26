#!/usr/bin/env python
"""Capture Claude Code statusLine rate-limit metadata for the AI Usage Stats extension.

Claude Code does not persist subscription rate limits to disk; they are only
delivered in the JSON piped to a configured `statusLine` command. This wrapper
reads that JSON on stdin, writes ONLY the numeric ``rate_limits`` / ``cost`` /
``context_window`` fields (plus a ``captured_at`` timestamp) to a known file the
extension polls, then optionally chains a prior statusLine command so any
existing status line keeps rendering unchanged.

Privacy: it never reads or writes prompt, response, or transcript text — only the
whitelisted numeric metadata keys.

Usage (as a Claude Code statusLine.command):
    python3 claude_statusline_capture.py --capture <path> [-- <prior command...>]

Everything after ``--`` is the previously configured statusLine command. It is run
with the same stdin and its stdout is forwarded byte-for-byte.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

CAPTURE_KEYS = ("rate_limits", "cost", "context_window")


def build_capture(raw: bytes) -> dict:
    capture: dict = {"captured_at": time.time()}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return capture
    if not isinstance(data, dict):
        return capture
    for key in CAPTURE_KEYS:
        if key in data:
            capture[key] = data[key]
    return capture


def write_capture(path: str, capture: dict) -> None:
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(capture, handle, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass


def parse_argv(argv: list[str]) -> tuple[str, list[str]]:
    capture_path = ""
    chain: list[str] = []
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--capture":
            index += 1
            capture_path = argv[index] if index < len(argv) else ""
        elif arg == "--":
            chain = argv[index + 1:]
            break
        index += 1
    return capture_path, chain


def main(argv: list[str]) -> int:
    capture_path, chain = parse_argv(argv)
    raw = sys.stdin.buffer.read()

    if capture_path:
        write_capture(capture_path, build_capture(raw))

    if chain:
        # The outer shell already expanded and tokenized the prior command, so it
        # is exec'd as argv (the same thing the shell would have run directly).
        try:
            completed = subprocess.run(chain, input=raw)
            return completed.returncode
        except OSError:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
