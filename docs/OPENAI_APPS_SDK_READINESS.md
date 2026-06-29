# OpenAI Apps SDK / MCP Safety Readiness

This repository is designed as a read-only MCP server for ChatGPT developer mode and Apps SDK-compatible clients.

## Tool safety annotations

Every exposed tool is registered with:

```json
{
  "readOnlyHint": true,
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false
}
```

Rationale:

- The server only retrieves or computes research information.
- No tool creates, updates, deletes, trades, submits filings, sends messages, creates calendar events, or publishes content.
- Calls to FMP are server-side lookups for public market data using bounded, non-sensitive inputs such as ticker symbols, fiscal periods, and date ranges.
- The FMP API key remains server-side and is never exposed as a tool input.

## ChatGPT metadata refresh

After deployment, refresh the connector metadata in ChatGPT:

1. Deploy the updated server.
2. Open ChatGPT Settings -> Connectors -> Developer mode.
3. Select the MCP server.
4. Click Refresh.
5. Confirm the actions no longer display conservative WRITE/DESTRUCTIVE labels.

## Pre-submission checks

- Run `pytest` before packaging or deploying.
- Confirm every tool descriptor includes non-null annotations.
- Keep tool inputs minimal, bounded, and directly related to public financial research.
- Do not add tools that write, trade, publish, or send notifications without changing annotations and adding explicit user confirmation flows.
- Keep logs free of API keys, secrets, and unnecessary transcript text.
- Add production rate limiting and authentication before exposing this server beyond a trusted environment.
