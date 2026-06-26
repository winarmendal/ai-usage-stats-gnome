# Privacy

AI Usage Stats is intentionally local-first. The same privacy guarantees apply to both the Codex and Claude providers. The only time it touches the network is the explicitly opt-in **"Fetch live limits online"** switch for Claude (off by default), documented under [Online Live Limits](#online-live-limits-opt-in).

## What It Reads — Codex

The helper reads local JSONL files below the configured Codex log root, defaulting to:

```text
~/.codex/sessions
```

It only processes records shaped like:

```text
type = event_msg
payload.type = token_count
```

From those records it reads token counts, model context size, rate-limit percentages, rate-limit windows, reset timestamps, and event timestamps. It also infers the session start timestamp from the JSONL filename when choosing between conflicting rate-limit snapshots.

For fresher 5-hour and weekly percentages, the helper may also read the local Codex log database:

```text
~/.codex/logs_2.sqlite
```

From that database it accepts only structured `codex.rate_limits` events and reads numeric rate-limit metadata: used percentage, window length, and reset timestamp. It ignores free-form text and does not store log database rows in the cache.

When realtime account limits are enabled, the extension starts the local Codex CLI app-server over stdio and sends only an `account/rateLimits/read` request. The response is used only for current 5-hour and weekly percentages. This mode can be disabled in preferences.

## What It Reads — Claude

The helper reads local JSONL transcript files below the configured Claude log root, defaulting to:

```text
~/.claude/projects
```

Claude JSONL transcripts contain prompt and response text. The helper reads **only** `assistant` events and from those reads only the `message.usage` numeric fields (input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens) plus the deduplication identifiers `sessionId`, `requestId`, and `message.id`. Prompt text, assistant response text, file contents, tool call arguments, and all other fields are ignored and never stored.

For 5-hour and weekly rate-limit percentages, the helper reads the opt-in statusLine capture file, defaulting to:

```text
~/.cache/codex-stats/claude-limits.json
```

This file is written exclusively by the statusLine capture wrapper (see below) and contains only numeric `rate_limits`, `cost`, and `context_window` fields plus a `captured_at` timestamp.

## StatusLine Capture Wrapper

`helper/claude_statusline_capture.py` is an opt-in component installed from Preferences → Claude → "Install". It intercepts the Claude Code statusLine payload and writes only whitelisted numeric fields to the capture file — rate limit percentages, cost, and context window size. It does not log, store, or forward any other content from the statusLine payload. The wrapper chains any pre-existing `statusLine` command byte-for-byte. Installation and uninstall edit `~/.claude/settings.json` atomically.

## Online Live Limits (Opt-In)

By default the extension makes no network calls. The optional **"Fetch live limits online"** switch (Preferences → Claude, off by default) is the single exception. When enabled — and only for the Claude provider — the helper makes one HTTPS request to Anthropic's subscription usage endpoint:

```text
GET https://api.anthropic.com/api/oauth/usage
```

This is the same endpoint Claude Code's own `/usage` view uses. It returns live 5-hour and weekly percentages plus per-model (Sonnet/Opus) weekly buckets that the offline statusLine source cannot provide.

- **Authentication.** The request is authenticated with your existing local Claude login token, read **read-only** from `~/.claude/.credentials.json` (the file Claude Code itself maintains). The helper never writes that file, never refreshes the token, and never logs, stores, or transmits the token anywhere except as the `Authorization` header of this one request to Anthropic. If the token is absent or expired, the helper skips the request and falls back to the local statusLine source.
- **What is sent.** Only the bearer token and the fixed request above — no prompts, no token history, no machine identifiers.
- **What is received and stored.** Only numeric usage metadata (utilization percentages and reset timestamps), whitelisted before being written to a throttle cache at `~/.cache/codex-stats/claude-online.json`. Nothing else from the response is kept.
- **Throttling.** Responses are cached briefly and a rate-limited (HTTP 429) response triggers a backoff, so enabling this does not hammer the endpoint.
- **Scope.** This reads *your own* subscription usage from *your own* account. The endpoint is undocumented/internal and may change without notice; if it fails, the gauges fall back to the local statusLine source.

Leaving the switch off keeps the extension fully local-first with no network access.

## What It Does Not Read

The extension and helper do not parse or display:

- user prompts (Codex or Claude)
- assistant responses (Codex or Claude)
- source files or tool call arguments
- shell command output
- browser data
- cookies
- API keys or access tokens — *except* that the opt-in [Online Live Limits](#online-live-limits-opt-in) mode reads your local Claude login token read-only, solely to authenticate your own usage request
- remote service responses — *except* the numeric usage metadata returned to the opt-in online mode from your own Anthropic account

## Network

By default the extension and helper open no network sockets. Codex realtime account limit mode delegates to the local Codex CLI, which may refresh its own account rate-limit snapshot, and the Claude provider reads only local files. The single exception is the opt-in **"Fetch live limits online"** switch (off by default), which makes one authenticated HTTPS request to Anthropic's usage endpoint for the Claude provider — see [Online Live Limits](#online-live-limits-opt-in).

## Cache

The optional per-provider cache files are stored at:

```text
~/.cache/codex-stats/cache-codex.json
~/.cache/codex-stats/cache-claude.json
```

They contain parsed token metadata and file scan metadata only — never prompt text, response text, or file contents.

When the opt-in online mode is enabled, a throttle cache at `~/.cache/codex-stats/claude-online.json` additionally holds only the most recent numeric usage snapshot (utilization percentages and reset timestamps) and never any token or transcript content.
