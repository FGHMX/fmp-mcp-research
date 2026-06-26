# FMP MCP Research Server

Remote MCP server for ChatGPT or another LLM that exposes read-only Financial Modeling Prep tools for professional buy-side earnings-call research workflows.

The server does **not** generate final investment recommendations. It builds evidence manifests, returns transcript completeness metadata, finds filings/tables, and helps an analyst or LLM decide whether a scorecard is supported by the evidence.

## Version 0.3.0 changes

- Aligned the transcript tool contract with the documented workflow: `section` and `max_chars` are now supported.
- Fixed truncation-marker detection so normal transcripts are not falsely marked as truncated.
- Added a softer `transcript_quality_status`: `complete`, `usable_with_warnings`, or `incomplete`.
- Kept Q&A split uncertainty as a warning rather than an automatic hard block.
- Added `contract_version` and `evidence_pack_version`.
- Added dev dependencies for pytest, ruff, mypy and respx.
- Added CI skeleton for test/lint.
- Reformatted the project into maintainable Python modules.
- Hardened Docker by running as a non-root user.
- Added explicit statement-table review actions for the latest completed fiscal year and selected quarters.
- Reduced default SEC filing noise in evidence packs by omitting non-core filings unless specifically relevant.

## Tools exposed

| Tool | Purpose |
|---|---|
| `fmp_get_company_profile` | Get sector, industry, market cap and descriptive metadata. |
| `fmp_list_transcript_dates` | Discover available earnings-call transcript periods. |
| `fmp_get_earnings_call_transcript` | Fetch full transcript, prepared remarks, Q&A, or metadata. |
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
4. For every selected period, call `fmp_get_earnings_call_transcript(symbol, year, quarter, section="full")`.
5. If `content_truncated_by_tool=true`, fetch `section="prepared_remarks"` and `section="qna"` separately.
6. Mark `full_call_text_read=yes` and `qna_reviewed=yes` only after actually reading the returned text.
7. Use `fmp_search_sec_filings` to locate official 8-K/6-K releases and 10-Q/10-K filings.
8. Use `fmp_get_statement_tables(period="annual")` to review Income Statement, Balance Sheet and Cash Flow Statement for the latest completed fiscal year.
9. Use `fmp_get_statement_tables(period="quarter")` to review Income Statement, Balance Sheet and Cash Flow Statement for every selected quarter.
10. Use key metrics, ratios and growth tables as supporting context, not as a substitute for primary statements.
11. Complete both `source_audit_template` and `financial_statement_audit_template` before producing a scorecard.

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

- Keep `FMP_API_KEY` server-side only.
- Do not expose write tools.
- Do not add a tool that generates final investment recommendations.
- Log tool name, symbol, quarter and source coverage status; do not log API keys.
- Add rate limiting and an auth layer if used by multiple analysts or exposed outside a trusted network.
