# FMP MCP Research Server

Remote MCP server for ChatGPT that exposes read-only FMP tools to build a strict buy-side earnings-call evidence pack before scoring a catalyst report.

## Why this design

The report process requires the agent to read the actual full earnings-call transcript including Q&A, separately review earnings releases and financial tables, document missing evidence, complete a quarter-by-quarter audit, and only then score. This MCP server therefore exposes small, auditable tools instead of a single black-box "make report" tool.

## Tools exposed

| Tool | Purpose |
|---|---|
| `fmp_get_company_profile` | Subsector, company description, market data, sector/industry context. |
| `fmp_list_transcript_dates` | Select the two most recent transcripts from 2025 or later. |
| `fmp_get_earnings_call_transcript` | Fetch full transcript for a specific quarter/year; the agent must read prepared remarks and Q&A. |
| `fmp_get_statement_tables` | Fetch quarterly income statement, balance sheet, cash flow, key metrics, ratios and growth. |
| `fmp_search_sec_filings` | Find official filings, especially 8-K/10-Q/10-K fallback evidence. |
| `fmp_get_earnings_calendar` | Confirm earnings dates and EPS actual/estimate context. |
| `fmp_build_research_evidence_pack` | Bundle selected periods, transcripts, financial tables, SEC filing list and audit templates. |
| `research_report_contract` | Return required report sections, scoring fields, source-audit fields and Pharma overlay diagnostics. |

## FMP endpoints used

Base URL: `https://financialmodelingprep.com/stable`

- `/profile?symbol=...`
- `/earning-call-transcript-dates?symbol=...`
- `/earning-call-transcript?symbol=...&year=...&quarter=...`
- `/earning-call-transcript-latest`
- `/earnings-calendar?symbol=...&from=...&to=...`
- `/income-statement?symbol=...&period=quarter&limit=...`
- `/balance-sheet-statement?symbol=...&period=quarter&limit=...`
- `/cash-flow-statement?symbol=...&period=quarter&limit=...`
- `/key-metrics?symbol=...&period=quarter&limit=...`
- `/ratios?symbol=...&period=quarter&limit=...`
- `/financial-growth?symbol=...&period=quarter&limit=...`
- `/sec-filings-search/symbol?symbol=...&from=...&to=...`
- `/sec-filings-8k?from=...&to=...`

## Local development

```bash
cp .env.example .env
# edit .env and set FMP_API_KEY
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m fmp_mcp_research.server
```

The MCP endpoint is usually available at:

```text
http://localhost:8000/mcp
```

## Docker

```bash
cp .env.example .env
# edit .env and set FMP_API_KEY
docker compose up --build
```

## Deployment recommendation

For ChatGPT, deploy as a remote HTTPS MCP server. Local is best only for development. For production, use Cloud Run or Fly.io for a simple container deployment; use AWS ECS/Fargate if your firm already standardizes on AWS and wants VPC/private networking.

Recommended default: **Google Cloud Run**

- Container-native and low ops burden.
- Scales to zero for cost control.
- Easy environment variables and secret management.
- Public HTTPS endpoint works with ChatGPT remote MCP setup.

## Cloud Run deploy sketch

```bash
gcloud run deploy fmp-mcp-research \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars FMP_API_KEY=YOUR_KEY
```

For a production workspace, replace `--allow-unauthenticated` with an OAuth/API-gateway layer or Cloud Run IAM where supported by your MCP client path.

## ChatGPT connection

Use the public HTTPS URL plus `/mcp`, for example:

```text
https://your-service-url.run.app/mcp
```

In ChatGPT app/developer settings, add the custom MCP app/server and approve the read-only tools.

## Security guardrails

- Keep FMP_API_KEY server-side only.
- Do not expose write tools.
- Do not add a tool that generates final investment recommendations.
- Log tool name, symbol, quarter and source coverage status; do not log full API keys or secrets.
- Add rate limiting if used by multiple analysts.

## Typical report workflow

1. `research_report_contract(sector="pharma")`
2. `fmp_build_research_evidence_pack(symbol="AONC", min_year=2025, requested_calls=2)`
3. Read each transcript payload, including Q&A.
4. Review statement tables in the evidence pack.
5. Use `fmp_search_sec_filings` to locate 8-K/10-Q/10-K fallback sources and official release exhibits.
6. Complete the source audit and quarter-by-quarter audit.
7. Only after the audit, create scorecard and adjusted score.
