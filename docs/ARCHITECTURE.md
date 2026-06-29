# Architecture

## Goal

The MCP server is a read-only evidence orchestration layer for LLM-driven buy-side research reports. It is optimized for prompts that require full earnings-call review, Q&A review, official releases, financial tables, quarter-by-quarter source audits, and conservative scoring readiness.

## High-level flow

```text
LLM / ChatGPT
  ↓ MCP tool calls
FastMCP server
  ↓
FMPClient
  ↓
Financial Modeling Prep API
```

## Design principle

The MCP separates these states explicitly:

1. A source exists.
2. A source is available to fetch.
3. A source was returned in the payload.
4. A source was returned completely.
5. The LLM/agent has read the returned source.
6. Scoring is mechanically allowed.

The MCP can verify states 1-4 mechanically. It cannot know whether the LLM truly read a document, so the audit fields use explicit review flags that the analyst or agent must complete after reading the returned sources.

## Tool roles

### Discovery

`fmp_list_transcript_dates` selects recent available calls from `min_year` onward and returns recommended fetch actions with only `symbol`, `year`, and `quarter`.

### Canonical transcript fetch

`fmp_get_earnings_call_transcript` is the canonical source for transcript text. Its public input is intentionally small:

- `symbol`
- `year`
- `quarter`

The tool returns the complete transcript text supplied by FMP for that selected period plus completeness metadata, Q&A metadata, operator start / close detection and next best actions if the source appears incomplete.

### Evidence pack

`fmp_build_research_evidence_pack` is a manifest/orchestrator. It returns:

- selected transcript periods
- transcript status per period
- evidence manifest
- financial table matching status for selected quarters
- annual financial table matching status for the latest completed fiscal year
- prioritized SEC filings
- quarter-by-quarter source audit template
- financial statement audit template for annual and selected-quarter review
- scoring readiness and blocking items
- next actions

It does not embed transcript text. This keeps the evidence-pack tool friendly to OpenAI tool-call safety checks and makes the dedicated transcript tool the only place where full earnings-call text is returned. Count-style inputs are bounded and clamped server-side to reduce accidental oversized calls.

### Validation

`fmp_validate_research_evidence` checks an evidence-pack payload for follow-up requirements. It is a mechanical validator, not an analyst judgment engine.

## Transcript completeness logic

The server uses heuristic checks:

- minimum word count for full-call plausibility
- Q&A start markers
- Q&A length
- operator close markers
- explicit truncation markers
- prepared remarks and Q&A section detection for metadata

These checks are conservative and intentionally favor follow-up verification when evidence is ambiguous.

## Strict report workflow behavior

When `strict_report_workflow=true`, the evidence pack avoids silent fallbacks. If financial tables do not match the selected periods exactly, the payload marks `no_exact_period_match` rather than pretending the latest rows are reviewed.

The evidence pack separates primary statement review from secondary metrics. Income Statement, Balance Sheet and Cash Flow Statement are required for the latest completed fiscal year and every selected quarter. Key metrics, ratios and financial growth are supporting context only.

The evidence pack also sets scoring blockers unless required transcript, Q&A, release and statement-table review can be verified through the workflow.

## Why this prevents LLM errors

The evidence pack gives the LLM explicit operational states:

- transcript exists but is not embedded in the evidence pack
- use `fmp_get_earnings_call_transcript` next
- Q&A review must be confirmed after reading the full returned transcript
- official releases and statement tables must be reviewed separately
- do not score until blocking items are cleared
