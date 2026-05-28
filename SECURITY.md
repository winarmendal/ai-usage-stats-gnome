# Security Policy

## Supported Versions

The current public version targets GNOME Shell 50.

## Reporting A Vulnerability

Open a GitHub security advisory or private issue with:

- affected version or commit
- operating system and GNOME Shell version
- clear reproduction steps
- whether local Codex logs, cache files, or extension preferences are involved

## Privacy-Sensitive Areas

Codex Stats must not parse or display:

- prompts or assistant messages
- source file contents
- browser cookies
- API keys or bearer tokens
- ChatGPT/OpenAI network API responses

The helper should only use `event_msg` / `token_count` metadata from local Codex JSONL logs.

