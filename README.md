# FMP MCP Research Server

Remote MCP server for ChatGPT or another LLM that exposes read-only Financial Modeling Prep tools for professional buy-side earnings-call research workflows.

The server does **not** generate final investment recommendations. It builds evidence manifests, returns transcript completeness metadata, finds filings/tables, and helps an analyst or LLM decide whether a scorecard is supported by the evidence.

## Version 0.3.2 changes

- Added explicit MCP `ToolAnnotations` for every exposed action: `readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true`, and `openWorldHint=false`.
- Added human-readable tool titles and “Use this when...” descriptions so ChatGPT can classify the actions safely.
- Added stricter public input validation for ticker symbols and ISO date ranges.
- Added a contract test that fails if any tool is missing safe read-only annotations.
- Removed public `include_transcript_text` and `max_transcript_chars` inputs from `fmp_build_research_evidence_pack`.
- Kept evidence packs as manifests only: transcript text is not embedded; the required transcript fetches appear in `recommended_next_actions`.
- Removed public `section` and `max_chars` inputs from `fmp_get_earnings_call_transcript`.
- Made `fmp_get_earnings_call_transcript` return the complete transcript supplied by FMP for the requested symbol/year/quarter.
- Updated transcript-related recommended actions so they pass only `symbol`, `year`, and `quarter`.
- Bumped package, contract, and evidence-pack versions to `0.3.1`.

## Tools exposed

| Tool | Purpose |
|---|---|
| `fmp_get_company_profile` | Get sector, industry, market cap and descriptive metadata. |
| `fmp_list_transcript_dates` | Discover available earnings-call transcript periods. |
| `fmp_get_earnings_call_transcript` | Fetch the complete transcript for a selected earnings-call period. |
| `fmp_get_statement_tables` | Fetch income statement, balance sheet, cash flow, key metrics, ratios and growth. |
| `fmp_search_sec_filings` | Find and prioritize 8-K/6-K earnings releases plus 10-Q/10-K evidence. |
| `fmp_get_earnings_calendar` | Confirm earnings dates and EPS actual/estimate context. |
| `fmp_build_research_evidence_pack` | Build selected periods, evidence manifest, audit template and next actions. |
| `fmp_validate_research_evidence` | Mechanically validate an evidence-pack payload. |
| `research_report_contract` | Return report sections, audit fields and score dimensions. |

## Recommended workflow

1. Call `research_report_contract(sector="healthcare_technology")` or the relevant sector.
2. Call `fmp_build_research_evidence_pack(symbol="PSNL", min_year=2025, requested_calls=2)`.
3. Read `selected_periods`, `evidence_manifest`, `scoring_readiness` and `recommended_next_actions`.
4. For every selected period, call `fmp_get_earnings_call_transcript(symbol, year, quarter)`.
5. Mark `full_call_text_read=yes` and `qna_reviewed=yes` only after actually reading the returned transcript.
6. Use `fmp_search_sec_filings` to locate official 8-K/6-K releases and 10-Q/10-K filings.
7. Use `fmp_get_statement_tables(period="annual")` to review Income Statement, Balance Sheet and Cash Flow Statement for the latest completed fiscal year.
8. Use `fmp_get_statement_tables(period="quarter")` to review Income Statement, Balance Sheet and Cash Flow Statement for every selected quarter.
9. Use key metrics, ratios and growth tables as supporting context, not as a substitute for primary statements.
10. Complete both `source_audit_template` and `financial_statement_audit_template` before producing a scorecard.

## OpenAI-friendly tool design

- Evidence packs do not return bulk transcript text.
- Transcript text is fetched only through the dedicated transcript tool.
- The transcript tool schema avoids large-content controls such as user-provided maximum character counts.
- Recommended transcript actions use a small, fixed argument shape: `symbol`, `year`, and `quarter`.
- Count-style inputs use bounded defaults and server-side clamps to avoid oversized tool calls.
- The server remains read-only and does not expose tools that generate final investment recommendations.
- Every exposed MCP tool declares safety annotations so ChatGPT should not conservatively classify it as write/destructive when metadata is refreshed.

## Local development

```bash
cp .env.example .env
# edit .env and set FMP_API_KEY
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
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

## Security guardrails

- All MCP tools are annotated as read-only, non-destructive, idempotent, and non-open-world. The server does make server-side requests to FMP for public market data, but it does not publish content, write to external systems, modify user accounts, trade, send notifications, or mutate any data.
- Keep `FMP_API_KEY` server-side only.
- Do not expose write tools.
- Do not add a tool that generates final investment recommendations.
- Log tool name, symbol, quarter and source coverage status; do not log API keys.
- Add rate limiting and an auth layer if used by multiple analysts or exposed outside a trusted network.
