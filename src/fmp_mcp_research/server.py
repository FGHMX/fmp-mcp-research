from __future__ import annotations

import os
from typing import Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .evidence import build_evidence_pack, normalize_transcript_dates
from .fmp_client import FMPClient
from .report_contract import (
    CORE_SCORE_DIMENSIONS,
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
    """List available FMP earnings-call transcript dates and select the most recent calls at or after min_year."""
    raw = await FMPClient().transcript_dates(symbol)
    return {
        "symbol": symbol.upper(),
        "selected_periods": normalize_transcript_dates(raw, min_year=min_year, max_items=limit),
        "raw": raw,
    }


@mcp.tool()
async def fmp_get_earnings_call_transcript(symbol: str, year: int, quarter: int) -> dict[str, Any]:
    """Fetch the full FMP earnings-call transcript for a symbol/year/quarter. Agent must read prepared remarks and Q&A before scoring."""
    data = await FMPClient().transcript(symbol, year, quarter)
    return {
        "symbol": symbol.upper(),
        "year": year,
        "quarter": quarter,
        "source_name": "FMP earning-call-transcript",
        "data": data,
        "audit_note": "If Q&A text is absent or incomplete, run this tool twice more, then use an internet full-transcript fallback before scoring.",
    }


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
        "audit_note": "These are financial tables; they do not replace official earnings releases or 8-K/6-K exhibits when the report requires them.",
    }


@mcp.tool()
async def fmp_search_sec_filings(
    symbol: str,
    from_date: str = "2025-01-01",
    to_date: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Search SEC filings by symbol for 8-K, 10-Q, 10-K or other official evidence/fallback documents."""
    return {
        "symbol": symbol.upper(),
        "source_name": "FMP sec-filings-search/symbol",
        "data": await FMPClient().sec_filings(symbol, from_date=from_date, to_date=to_date, limit=limit),
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
async def fmp_build_research_evidence_pack(symbol: str, min_year: int = 2025, requested_calls: int = 2) -> dict[str, Any]:
    """Build a report-ready evidence pack: selected transcript periods, transcript payloads, financial tables, SEC filings and audit templates."""
    return await build_evidence_pack(symbol=symbol, min_year=min_year, requested_calls=requested_calls)


@mcp.tool()
async def research_report_contract(sector: Literal["pharma", "general"] = "pharma") -> dict[str, Any]:
    """Return the strict report contract: required sections, source-audit fields, score dimensions and sector overlay diagnostics."""
    return {
        "required_sections": REPORT_OUTPUT_SECTIONS,
        "required_source_audit_fields": REQUIRED_SOURCE_FLAGS,
        "core_score_dimensions": CORE_SCORE_DIMENSIONS,
        "secondary_score_dimensions": SECONDARY_SCORE_DIMENSIONS,
        "sector_overlay": "pharma" if sector == "pharma" else "none",
        "pharma_lens_scores_diagnostic_only": PHARMA_LENSES if sector == "pharma" else [],
        "scoring_guardrail": "Never produce scorecard before completing quarter-by-quarter coverage audit and actual source reading.",
    }


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
