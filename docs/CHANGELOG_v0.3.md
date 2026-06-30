# Changelog v0.3

## v0.3.4 suggestion-only MCP logic

- Removed readiness gates from the evidence-pack output.
- Removed mandatory direct-review policy output from the evidence-pack tool.
- Removed required source flags and strict templates from the evidence-pack output.
- Removed embedded `sec_filings` from `evidence_manifest`; filing discovery remains available through `fmp_search_sec_filings`.
- Kept `recommended_next_actions`, but changed the wording and fields to suggestions rather than requirements or blockers.
- Updated `research_report_contract` to return suggested report structure and sector lenses.
- Updated validation to return informational notes and next actions instead of blocking items or allowed/disallowed status.
