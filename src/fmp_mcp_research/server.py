from __future__ import annotations

import os
from typing import Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .evidence import (
    TranscriptSection,
    build_evidence_pack,
    build_transcript_payload,
    normalize_transcript_dates,
    prioritize_sec_filings,
    validate_evidence_payload,
)
from .fmp_client import FMPClient
from .report_contract import build_report_contract

load_dotenv()

mcp = FastMCP(
    "FMP Buy-Side Research",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000")),
)

TRANSCRIPT_TOOL_MAX_CHARS = int(os.getenv("TRANSCRIPT_TOOL_MAX_CHARS", "200000"))


@mcp.tool()
async def fmp_get_company_profile(symbol: str) -> dict[str, Any]:
    """Get company profile, sector, industry, market cap and descriptive metadata from FMP."""
    return {"symbol": symbol.upper(), "data": await FMPClient().profile(symbol)}


@mcp.tool()
async def fmp_list_transcript_dates(
    symbol: str, min_year: int = 2025, limit: int = 2
) -> dict[str, Any]:
    """List available FMP earnings-call transcript periods."""
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
                "arguments_template": {
                    "symbol": symbol.upper(),
                    "year": "",
                    "quarter": "",
                    "section": "full",
                },
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


@mcp.tool()
async def fmp_get_earnings_call_transcript(
    symbol: str,
    year: int,
    quarter: int,
    section: TranscriptSection = "full",
    max_chars: int = TRANSCRIPT_TOOL_MAX_CHARS,
) -> dict[str, Any]:
    """Fetch one earnings-call transcript, optionally returning full, prepared remarks, Q&A, or metadata."""
    data = await FMPClient().transcript(symbol=symbol, year=year, quarter=quarter)
    payload = build_transcript_payload(
        symbol=symbol,
        year=year,
        quarter=quarter,
        raw=data,
        section=section,
        include_full_text=section != "metadata",
        max_chars=min(max_chars, TRANSCRIPT_TOOL_MAX_CHARS),
    )
    payload["raw_data"] = data if not payload["content_truncated_by_tool"] and section == "metadata" else None
    payload["audit_note"] = (
        "The transcript tool returns the requested section with mechanical completeness metadata. "
        "Mark full_call_text_read and qna_reviewed yes only after actually reading the returned text. "
        "If content_truncated_by_tool is true, use section='prepared_remarks' and section='qna' separately."
    )
    return payload


@mcp.tool()
async def fmp_get_statement_tables(
    symbol: str, period: Literal["quarter", "annual"] = "quarter", limit: int = 8
) -> dict[str, Any]:
    """Fetch income statement, balance sheet, cash flow, key metrics, ratios and growth tables."""
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
        "audit_note": "These are FMP financial tables. They do not replace official earnings releases or filings when the report requires them.",
    }


@mcp.tool()
async def fmp_search_sec_filings(
    symbol: str,
    from_date: str = "2025-01-01",
    to_date: str | None = None,
    limit: int = 100,
    prioritize_for_report: bool = True,
) -> dict[str, Any]:
    """Search SEC filings and optionally prioritize earnings releases plus 10-Q/10-K report evidence."""
    raw = await FMPClient().sec_filings(symbol, from_date=from_date, to_date=to_date, limit=limit)
    return {
        "symbol": symbol.upper(),
        "source_name": "FMP sec-filings-search/symbol",
        "prioritized": prioritize_sec_filings(raw) if prioritize_for_report else None,
        "data": raw,
        "audit_note": "The MCP identifies candidate filings; the agent must open and read the actual filing before marking it reviewed.",
    }


@mcp.tool()
async def fmp_get_earnings_calendar(
    symbol: str | None = None, from_date: str | None = None, to_date: str | None = None
) -> dict[str, Any]:
    """Fetch earnings calendar data, including announcement dates and EPS actual/estimate when available."""
    return {
        "symbol": symbol.upper() if symbol else None,
        "data": await FMPClient().earnings_calendar(
            symbol=symbol, from_date=from_date, to_date=to_date
        ),
    }


@mcp.tool()
async def fmp_build_research_evidence_pack(
    symbol: str,
    min_year: int = 2025,
    requested_calls: int = 2,
    strict_report_workflow: bool = True,
    include_transcript_text: bool = False,
    max_transcript_chars: int = 24_000,
) -> dict[str, Any]:
    """Build a report evidence manifest with selected periods, source status, tables, filings and next actions."""
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
    """Validate an evidence-pack payload mechanically and return blocking items / next actions."""
    return validate_evidence_payload(evidence_pack)


@mcp.tool()
async def research_report_contract(
    sector: Literal["pharma", "healthcare_technology", "general"] = "healthcare_technology",
) -> dict[str, object]:
    """Return required report sections, source-audit fields, score dimensions and sector overlays."""
    return build_report_contract(sector)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
