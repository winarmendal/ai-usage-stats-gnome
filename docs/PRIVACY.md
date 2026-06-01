# Privacy

Codex Stats is intentionally local-first.

## What It Reads

The helper reads local JSONL files below the configured log root, defaulting to:

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

From that database it accepts only structured `codex.rate_limits` events and reads numeric rate-limit metadata: used percentage, window length, and reset timestamp. It ignores free-form text and does not store log database rows in the Codex Stats cache.

When realtime account limits are enabled, Codex Stats starts the local Codex CLI app-server over stdio and sends only an `account/rateLimits/read` request. The response is used only for current 5-hour and weekly percentages. This mode can be disabled in preferences.

## What It Does Not Read

Codex Stats does not parse or display:

- user prompts
- assistant responses
- source files
- shell command output
- browser data
- cookies
- API keys or access tokens
- ChatGPT/OpenAI API responses

## Network

Codex Stats does not call OpenAI, ChatGPT, or any remote service directly. Realtime account limit mode delegates to the local Codex CLI, which may refresh its own account rate-limit snapshot.

## Cache

The optional cache is stored at:

```text
~/.cache/codex-stats/cache.json
```

It contains parsed token metadata and file scan metadata only.
