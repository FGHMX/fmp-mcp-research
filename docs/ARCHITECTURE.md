# Architecture

The MCP server is a read-only research context provider. It retrieves company data, transcript sections, statement tables, SEC filing candidates and SEC earnings-release JSON, then returns structured information and suggested next actions.

## Current logic

The MCP no longer enforces gates, source restrictions or workflow blockers. It provides:

- selected transcript periods
- transcript availability context
- financial statement table summaries
- annual statement table summaries
- context notes
- recommended next actions

The analyst or LLM decides how to use the returned information.

## Filing discovery

SEC filing candidates are available through `fmp_search_sec_filings`. The evidence-pack tool does not embed `sec_filings` inside `evidence_manifest`.

## Report structure

`research_report_contract` returns suggested sections, suggested source-context fields and sector lenses. These are suggestions, not requirements.
