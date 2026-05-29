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

Codex Stats does not call OpenAI, ChatGPT, or any remote service in v1.

## Cache

The optional cache is stored at:

```text
~/.cache/codex-stats/cache.json
```

It contains parsed token metadata and file scan metadata only.
