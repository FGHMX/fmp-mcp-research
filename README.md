# FMP MCP Research

Read-only MCP server for FMP and SEC research context. The server behaves as an information and suggestion layer: it returns source context, selected periods, tables, filings where requested, and `recommended_next_actions` without enforcing gates, restrictions, or blocking logic.

## Tools

| Tool | Purpose |
| --- | --- |
| `fmp_get_company_profile` | Return company profile and metadata. |
| `fmp_list_transcript_dates` | Discover available earnings-call periods and suggested transcript fetch actions. |
| `fmp_get_earnings_call_prepared_remarks` | Return prepared remarks / call-start text for a selected period. |
| `fmp_get_earnings_call_q_and_a` | Return Q&A text for a selected period. |
| `fmp_get_statement_tables` | Return income statement, balance sheet, cash flow, metrics, ratios and growth tables. |
| `fmp_search_sec_filings` | Return SEC filing candidates from FMP. |
| `get_earnings_release` | Fetch a likely SEC earnings-release exhibit and return LLM-friendly Markdown only. |
| `fmp_build_research_evidence_pack` | Build selected periods, evidence manifest, context notes and recommended next actions. |
| `fmp_build_research_pack` | Compatibility alias for the evidence-pack tool. |
| `fmp_validate_research_evidence` | Return informational notes and recommended next actions from a payload. |
| `research_report_contract` | Return suggested report structure and sector lenses. |

## Evidence-pack behavior

`fmp_build_research_evidence_pack` returns:

- `evidence_pack_version`
- `symbol`
- `selected_periods`
- `latest_completed_fiscal_year`
- `evidence_manifest`
- `context_notes`
- `recommended_next_actions`

The evidence-pack output omits strict templates, policies, source flags, gates and embedded SEC filing prioritization. Use `fmp_search_sec_filings` separately when filing discovery is needed.

## Philosophy

The MCP provides information and suggestions only. It does not tell the LLM what it is allowed to conclude, does not block downstream work, and does not enforce downstream decision rules.
