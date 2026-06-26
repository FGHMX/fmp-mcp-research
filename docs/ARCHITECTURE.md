# Architecture

## Components

```text
ChatGPT / MCP client
        |
        | HTTPS Streamable HTTP MCP
        v
FMP MCP Research Server
        |
        | Server-side FMP_API_KEY
        v
Financial Modeling Prep API
```

## Data flow for the AONC-style report

1. Agent asks for report contract.
2. Agent requests an evidence pack for the ticker.
3. Server selects the two most recent transcript periods from 2025 or later.
4. Server fetches transcripts, tables and SEC filing index.
5. Agent reads the returned content and performs the audit.
6. Agent scores only after all required evidence flags are completed.

## Why not a single `generate_report` tool?

The report specification requires explicit proof of source review. If the MCP server returns a finished report, it becomes harder to prove the agent actually read the transcript/Q&A and official financial tables. This design makes source coverage observable.

## Cloud options

| Option | Best for | Notes |
|---|---|---|
| Google Cloud Run | Default recommendation | Lowest ops, HTTPS by default, container deploy. |
| Fly.io | Fast small deployments | Good for prototypes; simpler than AWS. |
| AWS ECS/Fargate | Enterprise AWS shops | More setup, better if VPC, WAF, CloudWatch, IAM standards are already in place. |
| Local + Secure MCP Tunnel | Development/private testing | ChatGPT does not connect directly to local MCP servers; use a secure tunnel when supported. |

## Tool naming convention

Use prefix `fmp_` for data-access tools and `research_` for process/contract tools.

- Verb first: `get`, `list`, `search`, `build`.
- Avoid ambiguous names like `analyze_company` or `make_report`.
- Make every tool read-only.

## Production hardening

- Add authentication in front of the MCP endpoint.
- Add IP allowlists where possible.
- Add structured logs for source audit fields.
- Add cache with short TTL for transcripts and financial statements.
- Add retry/backoff and rate-limit handling.
- Add fallback scraper only for approved transcript sites if FMP transcript is absent.
