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

The MCP can verify states 1-4 mechanically. It cannot know whether the LLM truly read a document, so the audit fields use `unknown_agent_must_confirm` where appropriate.

## Tool roles

### Discovery

`fmp_list_transcript_dates` selects recent available calls from `min_year` onward and returns a recommended fetch action for each selected period.

### Canonical transcript fetch

`fmp_get_earnings_call_transcript` is the canonical source for transcript text. It returns:

- requested section: `full`, `prepared_remarks`, `qna`, or `metadata`
- completeness metadata
- Q&A metadata
- operator start / close detection
- tool-side truncation flags
- next best action if payload is incomplete or truncated

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

It does not certify that an LLM has read full transcripts.

### Validation

`fmp_validate_research_evidence` checks an evidence-pack payload for follow-up requirements. It is a mechanical validator, not an analyst judgment engine.

## Transcript completeness logic

The server uses heuristic checks:

- minimum word count for full-call plausibility
- Q&A start markers
- Q&A length
- operator close markers
- explicit truncation markers
- prepared remarks and Q&A section splitting

These checks are conservative and intentionally favor follow-up fetching when evidence is ambiguous.

## Strict report workflow behavior

When `strict_report_workflow=true`, the evidence pack avoids silent fallbacks. If financial tables do not match the selected periods exactly, the payload marks `no_exact_period_match` rather than pretending the latest rows are reviewed.

The evidence pack now separates primary statement review from secondary metrics. Income Statement, Balance Sheet and Cash Flow Statement are required for the latest completed fiscal year and every selected quarter. Key metrics, ratios and financial growth are supporting context only.

The evidence pack also sets scoring blockers unless required transcript, Q&A, release and statement-table review can be verified through the workflow.

## Why this prevents LLM errors

The prior design could return transcript flags that looked positive while the visible payload was truncated. Under a strict prompt, an LLM could incorrectly delete a company from the run or incorrectly score it.

The new design gives the LLM explicit operational states:

- transcript exists but not returned in full
- Q&A detected but not included
- source may be complete, but payload is partial
- use `fmp_get_earnings_call_transcript` next
- do not score until blocking items are cleared
