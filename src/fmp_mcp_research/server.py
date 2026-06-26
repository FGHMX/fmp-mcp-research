from __future__ import annotations

import os
from typing import Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .evidence import (
    TranscriptSection,
    assess_transcript_completeness,
    build_evidence_pack,
    build_transcript_payload,
    normalize_transcript_dates,
    prioritize_sec_filings,
    validate_evidence_payload,
)
from .fmp_client import FMPClient
from .report_contract import (
    CORE_SCORE_DIMENSIONS,
    HEALTHCARE_TECH_LENSES,
    PHARMA_LENSES,
    REPORT_OUTPUT_SECTIONS,
    REQUIRED_SOURCE_FLAGS,
    SECONDARY_SCORE_DIMENSIONS,
)

load_dotenv()

mcp = FastMCP(
    "FMP Buy-Side Research",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000")),
)


@mcp.tool()
async def fmp_get_company_profile(symbol: str) -> dict[str, Any]:
    """Get company profile, sector, industry, market cap and descriptive metadata from FMP."""
    return {"symbol": symbol.upper(), "data": await FMPClient().profile(symbol)}


@mcp.tool()
async def fmp_list_transcript_dates(symbol: str, min_year: int = 2025, limit: int = 2) -> dict[str, Any]:
    """List available FMP earnings-call transcript periods and recommend the canonical full transcript fetch tool."""
    raw = await FMPClient().transcript_dates(symbol)
    selected = normalize_transcript_dates(raw, min_year=min_year, max_items=limit)
    return {
        "symbol": symbol.upper(),
        "min_year": min_year,
        "requested_calls": limit,
        "available_calls": selected,
        "selected_periods": selected,
        "recommended_next_action": (
            {
                "tool": "fmp_get_earnings_call_transcript",
                "arguments_template": {"symbol": symbol.upper(), "year": "<year>", "quarter": "<quarter>", "section": "full"},
                "reason": "Fetch each selected period with the canonical transcript tool before scoring.",
            }
            if selected
            else {
                "tool": "fmp_list_transcript_dates",
                "arguments": {"symbol": symbol.upper(), "min_year": min_year - 1, "limit": limit},
                "reason": "No transcript periods found at or after min_year. Widen the year filter before concluding no EC is available.",
            }
        ),
        "raw": raw,
    }


TRANSCRIPT_TOOL_MAX_CHARS = 200_000


@mcp.tool()
async def fmp_get_earnings_call_transcript(
    symbol: str,
    year: int,
    quarter: int,
) -> dict[str, Any]:
    """Canonical fetch for one complete earnings-call transcript.

    The public MCP input intentionally exposes only symbol/year/quarter.
    The server always requests the full transcript text from FMP and applies
    a server-side character budget so the model cannot choose partial evidence.
    """

    data = await FMPClient().transcript(symbol=symbol, year=year, quarter=quarter)
    payload = build_transcript_payload(
        symbol=symbol,
        year=year,
        quarter=quarter,
        raw=data,
        section="full",
        include_full_text=True,
        max_chars=TRANSCRIPT_TOOL_MAX_CHARS,
    )
    payload["raw_data"] = data if not payload["content_truncated_by_tool"] else None
    payload["audit_note"] = (
        "The model requested only symbol/year/quarter. The server fetched the full earnings-call transcript. "
        "Mark full_call_text_read and qna_reviewed yes only after the agent actually reads the returned prepared remarks and Q&A. "
        "If content_truncated_by_tool is true, the transcript exceeds the server-side payload limit and must not be treated as fully reviewed from this payload."
    )
    return payload


@mcp.tool()
async def fmp_get_statement_tables(
    symbol: str,
    period: Literal["quarter", "annual"] = "quarter",
    limit: int = 8,
) -> dict[str, Any]:
    """Fetch income statement, balance sheet, cash flow, key metrics, ratios and growth tables from FMP."""
    client = FMPClient()
    return {
        "symbol": symbol.upper(),
        "period": period,
        "income_statement": await client.income_statement(symbol, period, limit),
        "balance_sheet": await client.balance_sheet(symbol, period, limit),
        "cash_flow_statement": await client.cash_flow(symbol, period, limit),
        "key_metrics": await client.key_metrics(symbol, period, limit),
        "ratios": await client.ratios(symbol, period, limit),
        "financial_growth": await client.financial_growth(symbol, period, limit),
        "audit_note": "These are FMP financial tables. They do not replace opening/reviewing official earnings releases or 8-K/6-K exhibits when the report requires them.",
    }


@mcp.tool()
async def fmp_search_sec_filings(
    symbol: str,
    from_date: str = "2025-01-01",
    to_date: str | None = None,
    limit: int = 100,
    prioritize_for_report: bool = True,
) -> dict[str, Any]:
    """Search SEC filings and optionally prioritize 8-K/6-K earnings releases plus 10-Q/10-K report evidence."""
    raw = await FMPClient().sec_filings(symbol, from_date=from_date, to_date=to_date, limit=limit)
    return {
        "symbol": symbol.upper(),
        "source_name": "FMP sec-filings-search/symbol",
        "prioritized": prioritize_sec_filings(raw) if prioritize_for_report else None,
        "data": raw,
        "audit_note": "The MCP can identify candidate filings, but the LLM/agent must open and read the actual earnings release or filing exhibit before marking it reviewed.",
    }


@mcp.tool()
async def fmp_get_earnings_calendar(
    symbol: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Fetch FMP earnings calendar data, including announcement dates and EPS actual/estimate when available."""
    return {
        "symbol": symbol.upper() if symbol else None,
        "data": await FMPClient().earnings_calendar(symbol=symbol, from_date=from_date, to_date=to_date),
    }


@mcp.tool()
async def fmp_build_research_evidence_pack(
    symbol: str,
    min_year: int = 2025,
    requested_calls: int = 2,
    strict_report_workflow: bool = True,
    include_transcript_text: bool = False,
    max_transcript_chars: int = 24000,
) -> dict[str, Any]:
    """Build a strict report evidence manifest with selected periods, source status, financial tables, filings and next actions.

    This tool is an orchestrator. It is intentionally conservative: it does not certify that the LLM has read full ECs.
    For strict scoring workflows, use the returned recommended_next_actions and call fmp_get_earnings_call_transcript
    for every selected period before producing a scorecard.
    """
    return await build_evidence_pack(
        symbol=symbol,
        min_year=min_year,
        requested_calls=requested_calls,
        strict_report_workflow=strict_report_workflow,
        include_transcript_text=include_transcript_text,
        max_transcript_chars=max_transcript_chars,
    )


@mcp.tool()
async def fmp_validate_research_evidence(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    """Validate an evidence-pack payload mechanically and return blocking items / next actions before scoring."""
    return validate_evidence_payload(evidence_pack)


@mcp.tool()
async def research_report_contract(sector: Literal["pharma", "healthcare_technology", "general"] = "healthcare_technology") -> dict[str, Any]:
    """Return the strict report contract: required sections, source-audit fields, score dimensions and sector overlay diagnostics."""
    if sector == "pharma":
        overlay_name = "pharma"
        lenses = PHARMA_LENSES
    elif sector == "healthcare_technology":
        overlay_name = "healthcare_technology"
        lenses = HEALTHCARE_TECH_LENSES
    else:
        overlay_name = "none"
        lenses = []
    return {
        "required_sections": REPORT_OUTPUT_SECTIONS,
        "required_source_audit_fields": REQUIRED_SOURCE_FLAGS,
        "core_score_dimensions": CORE_SCORE_DIMENSIONS,
        "secondary_score_dimensions": SECONDARY_SCORE_DIMENSIONS,
        "sector_overlay": overlay_name,
        "sector_lens_scores_diagnostic_only": lenses,
        "workflow_contract": {
            "evidence_pack_is_orchestrator_not_final_review": True,
            "canonical_transcript_fetch_tool": "fmp_get_earnings_call_transcript",
            "must_fetch_full_transcript_for_each_selected_period": True,
            "must_read_prepared_remarks_and_qna_before_scoring": True,
            "must_review_official_release_and_financial_tables_separately": True,
            "process_failure_if_scorecard_before_quarter_audit": True,
        },
        "scoring_guardrail": "Never produce scorecard before completing quarter-by-quarter coverage audit and actual source reading.",
    }


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
