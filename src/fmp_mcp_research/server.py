from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .evidence import (
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

Symbol = Annotated[str, Field(description="Public ticker symbol, for example ONDS.")]
FiscalYear = Annotated[int, Field(ge=1990, le=2100, description="Fiscal year.")]
FiscalQuarter = Annotated[int, Field(ge=1, le=4, description="Fiscal quarter, 1 through 4.")]
MinYear = Annotated[int, Field(ge=1990, le=2100, description="Earliest fiscal year to consider.")]
TranscriptDateLimit = Annotated[int, Field(ge=1, le=4, description="Number of transcript periods to select.")]
RequestedCalls = Annotated[int, Field(ge=1, le=4, description="Number of earnings-call periods to include in the evidence workflow.")]
StatementLimit = Annotated[int, Field(ge=1, le=12, description="Number of statement rows to request.")]
FilingLimit = Annotated[int, Field(ge=1, le=50, description="Number of SEC filing rows to request.")]


def _clamp(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


@mcp.tool()
async def fmp_get_company_profile(symbol: Symbol) -> dict[str, Any]:
    """Get company profile, sector, industry, market cap and descriptive metadata from FMP."""
    return {"symbol": symbol.upper(), "data": await FMPClient().profile(symbol)}


@mcp.tool()
async def fmp_list_transcript_dates(
    symbol: Symbol, min_year: MinYear = 2025, limit: TranscriptDateLimit = 2
) -> dict[str, Any]:
    """List available FMP earnings-call transcript periods."""
    limit = _clamp(limit, minimum=1, maximum=4)
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
                },
                "reason": "Fetch each selected period with the canonical complete-transcript tool before scoring.",
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
    symbol: Symbol,
    year: FiscalYear,
    quarter: FiscalQuarter,
) -> dict[str, Any]:
    """Fetch one complete earnings-call transcript with completeness metadata."""
    data = await FMPClient().transcript(symbol=symbol, year=year, quarter=quarter)
    payload = build_transcript_payload(
        symbol=symbol,
        year=year,
        quarter=quarter,
        raw=data,
        section="full",
        include_full_text=True,
        max_chars=None,
    )
    payload["raw_data"] = None
    payload["audit_note"] = (
        "The transcript tool returns the complete transcript text provided by FMP with mechanical completeness metadata. "
        "The public tool input intentionally does not expose section or max_chars controls. "
        "Mark full_call_text_read and qna_reviewed yes only after actually reading the returned text. "
        "If completeness warnings are present, verify the source before scoring."
    )
    return payload


@mcp.tool()
async def fmp_get_statement_tables(
    symbol: Symbol, period: Literal["quarter", "annual"] = "quarter", limit: StatementLimit = 8
) -> dict[str, Any]:
    """Fetch statement tables. Use period='annual' for latest completed fiscal year review and period='quarter' for selected-quarter review."""
    limit = _clamp(limit, minimum=1, maximum=12)
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
        "primary_statements_for_required_review": [
            "income_statement",
            "balance_sheet",
            "cash_flow_statement",
        ],
        "audit_note": (
            "Use income_statement, balance_sheet and cash_flow_statement for required financial-statement review. "
            "Use key_metrics, ratios and financial_growth as supporting context only. These FMP financial tables do not replace official earnings releases or filings when the report requires them."
        ),
    }


@mcp.tool()
async def fmp_search_sec_filings(
    symbol: Symbol,
    from_date: str = "2025-01-01",
    to_date: str | None = None,
    limit: FilingLimit = 50,
    prioritize_for_report: bool = True,
) -> dict[str, Any]:
    """Search SEC filings and optionally prioritize earnings releases plus 10-Q/10-K report evidence."""
    limit = _clamp(limit, minimum=1, maximum=50)
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
    symbol: Symbol | None = None, from_date: str | None = None, to_date: str | None = None
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
    symbol: Symbol,
    min_year: MinYear = 2025,
    requested_calls: RequestedCalls = 2,
    strict_report_workflow: bool = True,
) -> dict[str, Any]:
    """Build a report evidence manifest with selected periods, source status, tables, filings and next actions; transcript text is not embedded."""
    requested_calls = _clamp(requested_calls, minimum=1, maximum=4)
    return await build_evidence_pack(
        symbol=symbol,
        min_year=min_year,
        requested_calls=requested_calls,
        strict_report_workflow=strict_report_workflow,
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
