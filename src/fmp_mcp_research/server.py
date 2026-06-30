from __future__ import annotations

import os
import re
from datetime import date
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .evidence import (
    OPENAI_RETRY_SUGGESTION,
    build_evidence_pack,
    build_transcript_payload,
    normalize_transcript_dates,
    prioritize_sec_filings,
    validate_evidence_payload,
)
from .fmp_client import FMPClient
from .report_contract import build_report_contract
from .sec_client import SECClient

load_dotenv()

mcp = FastMCP(
    "FMP Buy-Side Research",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000")),
)

READ_ONLY_SAFE = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

SYMBOL_PATTERN = r"^[A-Za-z0-9.\\-]{1,12}$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"

Symbol = Annotated[
    str,
    Field(
        pattern=SYMBOL_PATTERN,
        min_length=1,
        max_length=12,
        description="Ticker symbol, for example ONDS or BRK.B.",
    ),
]
FiscalYear = Annotated[int, Field(ge=1990, le=2100, description="Fiscal year.")]
FiscalQuarter = Annotated[int, Field(ge=1, le=4, description="Fiscal quarter, 1 to 4.")]
MinYear = Annotated[int, Field(ge=1990, le=2100, description="Earliest fiscal year.")]
TranscriptDateLimit = Annotated[
    int, Field(ge=1, le=4, description="Number of transcript periods.")
]
RequestedCalls = Annotated[
    int,
    Field(
        ge=1,
        le=4,
        description="Number of earnings-call periods.",
    ),
]
StatementLimit = Annotated[
    int, Field(ge=1, le=4, description="Number of statement rows.")
]
FilingLimit = Annotated[
    int, Field(ge=1, le=50, description="Number of SEC filing rows.")
]
ISODateString = Annotated[
    str,
    Field(
        pattern=DATE_PATTERN,
        description="Date in YYYY-MM-DD format.",
    ),
]


def _clean_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not re.fullmatch(SYMBOL_PATTERN, value):
        raise ValueError("symbol must be 1-12 characters: letters, numbers, dot, or hyphen")
    return value


def _validate_iso_date(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date") from exc
    return value


def _validate_date_range(
    from_date: str | None, to_date: str | None
) -> tuple[str | None, str | None]:
    clean_from = _validate_iso_date(from_date, field_name="from_date")
    clean_to = _validate_iso_date(to_date, field_name="to_date")
    if clean_from and clean_to and date.fromisoformat(clean_from) > date.fromisoformat(clean_to):
        raise ValueError("from_date must be earlier than or equal to to_date")
    return clean_from, clean_to


def _clamp(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


@mcp.tool(
    title="Get company profile",
    description="Reads public company profile data from FMP.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_get_company_profile(symbol: Symbol) -> dict[str, Any]:
    """Get company profile, sector, industry, market cap and descriptive metadata from FMP."""
    clean_symbol = _clean_symbol(symbol)
    return {"symbol": clean_symbol, "data": await FMPClient().profile(clean_symbol)}


@mcp.tool(
    title="List transcript dates",
    description="Lists available FMP earnings-call periods for a ticker.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_list_transcript_dates(
    symbol: Symbol, min_year: MinYear = 2025, limit: TranscriptDateLimit = 2
) -> dict[str, Any]:
    """List available FMP earnings-call transcript periods."""
    clean_symbol = _clean_symbol(symbol)
    limit = _clamp(limit, minimum=1, maximum=4)
    raw = await FMPClient().transcript_dates(clean_symbol)
    selected = normalize_transcript_dates(raw, min_year=min_year, max_items=limit)
    return {
        "symbol": clean_symbol,
        "min_year": min_year,
        "requested_calls": limit,
        "available_calls": selected,
        "selected_periods": selected,
        "recommended_next_actions": (
            [
                {
                    "tool": "fmp_get_earnings_call_prepared_remarks",
                    "arguments_template": {
                        "symbol": clean_symbol,
                        "year": "",
                        "quarter": "",
                    },
                    "reason": "Suggested source for the start/prepared remarks for each selected period.",
                    "retry_suggestion": OPENAI_RETRY_SUGGESTION,
                },
                {
                    "tool": "fmp_get_earnings_call_q_and_a",
                    "arguments_template": {
                        "symbol": clean_symbol,
                        "year": "",
                        "quarter": "",
                    },
                    "reason": "Suggested source for the Q&A for each selected period.",
                    "retry_suggestion": OPENAI_RETRY_SUGGESTION,
                },
            ]
            if selected
            else [
                {
                    "tool": "fmp_list_transcript_dates",
                    "arguments": {"symbol": clean_symbol, "min_year": min_year - 1, "limit": limit},
                    "reason": "No transcript periods found at or after min_year. You may widen the year filter for additional context.",
                }
            ]
        ),
        "raw": raw,
    }


@mcp.tool(
    title="Get earnings-call prepared remarks",
    description="Reads prepared remarks from one FMP earnings call.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_get_earnings_call_prepared_remarks(
    symbol: Symbol,
    year: FiscalYear,
    quarter: FiscalQuarter,
) -> dict[str, Any]:
    """Fetch the start/prepared remarks portion of one earnings-call transcript."""
    clean_symbol = _clean_symbol(symbol)
    data = await FMPClient().transcript(symbol=clean_symbol, year=year, quarter=quarter)
    payload = build_transcript_payload(
        symbol=clean_symbol,
        year=year,
        quarter=quarter,
        raw=data,
        section="prepared_remarks",
        include_full_text=True,
        max_chars=None,
    )
    payload["raw_data"] = None
    payload["note"] = (
        "This tool returns only the earnings-call start/prepared remarks. The paired Q&A tool can provide additional context for the same period. "
        f"{OPENAI_RETRY_SUGGESTION}"
    )
    return payload


@mcp.tool(
    title="Get earnings-call Q&A",
    description="Reads the Q&A section from one FMP earnings call.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_get_earnings_call_q_and_a(
    symbol: Symbol,
    year: FiscalYear,
    quarter: FiscalQuarter,
) -> dict[str, Any]:
    """Fetch the Q&A portion of one earnings-call transcript."""
    clean_symbol = _clean_symbol(symbol)
    data = await FMPClient().transcript(symbol=clean_symbol, year=year, quarter=quarter)
    payload = build_transcript_payload(
        symbol=clean_symbol,
        year=year,
        quarter=quarter,
        raw=data,
        section="q_and_a",
        include_full_text=True,
        max_chars=None,
    )
    payload["raw_data"] = None
    payload["note"] = (
        "This tool returns only the earnings-call Q&A. The paired prepared-remarks tool can provide additional context for the same period. "
        f"{OPENAI_RETRY_SUGGESTION}"
    )
    return payload


@mcp.tool(
    title="Get financial statement tables",
    description="Reads FMP financial statement tables for a ticker.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_get_statement_tables(
    symbol: Symbol, period: Literal["quarter", "annual"] = "quarter", limit: StatementLimit = 4
) -> dict[str, Any]:
    """Fetch statement tables. Use period='annual' for latest completed fiscal year review and period='quarter' for selected-quarter review."""
    clean_symbol = _clean_symbol(symbol)
    limit = _clamp(limit, minimum=1, maximum=4)
    client = FMPClient()
    return {
        "symbol": clean_symbol,
        "period": period,
        "income_statement": await client.income_statement(clean_symbol, period, limit),
        "balance_sheet": await client.balance_sheet(clean_symbol, period, limit),
        "cash_flow_statement": await client.cash_flow(clean_symbol, period, limit),
        "key_metrics": await client.key_metrics(clean_symbol, period, limit),
        "ratios": await client.ratios(clean_symbol, period, limit),
        "financial_growth": await client.financial_growth(clean_symbol, period, limit),
        "primary_statement_tables": [
            "income_statement",
            "balance_sheet",
            "cash_flow_statement",
        ],
        "note": (
            "Income statement, balance sheet and cash flow statement are primary statement tables. "
            "Key metrics, ratios and financial growth are supporting context."
        ),
    }


@mcp.tool(
    title="Search SEC filings",
    description="Lists SEC filing candidates from FMP.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_search_sec_filings(
    symbol: Symbol,
    from_date: ISODateString = "2025-01-01",
    to_date: ISODateString | None = None,
    limit: FilingLimit = 50,
    prioritize_for_report: bool = True,
) -> dict[str, Any]:
    """Search SEC filings and optionally prioritize earnings releases plus 10-Q/10-K report evidence."""
    clean_symbol = _clean_symbol(symbol)
    clean_from, clean_to = _validate_date_range(from_date, to_date)
    limit = _clamp(limit, minimum=1, maximum=50)
    raw = await FMPClient().sec_filings(
        clean_symbol, from_date=clean_from or "2025-01-01", to_date=clean_to, limit=limit
    )
    return {
        "symbol": clean_symbol,
        "source_name": "FMP sec-filings-search/symbol",
        "prioritized": prioritize_sec_filings(raw) if prioritize_for_report else None,
        "data": raw,
        "note": "The MCP identifies candidate filings and returns the raw filing-search data for context.",
    }


@mcp.tool(
    title="Get SEC earnings release JSON",
    description="Reads one public SEC earnings release and returns text and tables as JSON.",
    annotations=READ_ONLY_SAFE,
)
async def get_earnings_release_json(
    symbol: Symbol,
    fiscalYear: FiscalYear,
    fiscalQuarter: FiscalQuarter,
    filingDate: ISODateString,
) -> dict[str, Any]:
    """Fetch an official SEC EDGAR earnings release and convert it to LLM-friendly JSON."""
    clean_symbol = _clean_symbol(symbol)
    clean_filing_date = _validate_iso_date(filingDate, field_name="filingDate")
    if clean_filing_date is None:
        raise ValueError("filingDate is required")
    payload = await SECClient().get_earnings_release_json(
        symbol=clean_symbol,
        fiscal_year=fiscalYear,
        fiscal_quarter=fiscalQuarter,
        filing_date=clean_filing_date,
    )
    payload["retry_suggestion"] = OPENAI_RETRY_SUGGESTION
    return payload


@mcp.tool(
    title="Get earnings calendar",
    description="Reads FMP earnings calendar entries.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_get_earnings_calendar(
    symbol: Symbol | None = None,
    from_date: ISODateString | None = None,
    to_date: ISODateString | None = None,
) -> dict[str, Any]:
    """Fetch earnings calendar data, including announcement dates and EPS actual/estimate when available."""
    clean_symbol = _clean_symbol(symbol) if symbol else None
    clean_from, clean_to = _validate_date_range(from_date, to_date)
    return {
        "symbol": clean_symbol,
        "data": await FMPClient().earnings_calendar(
            symbol=clean_symbol, from_date=clean_from, to_date=clean_to
        ),
    }


@mcp.tool(
    title="Build research evidence pack",
    description="Builds a compact research evidence summary for a ticker.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_build_research_evidence_pack(
    symbol: Symbol,
    min_year: MinYear = 2025,
    requested_calls: RequestedCalls = 2,
    strict_report_workflow: bool = False,
) -> dict[str, Any]:
    """Build a report evidence manifest with selected periods, source status, tables, filings and next actions; transcript text is not embedded."""
    clean_symbol = _clean_symbol(symbol)
    requested_calls = _clamp(requested_calls, minimum=1, maximum=4)
    return await build_evidence_pack(
        symbol=clean_symbol,
        min_year=min_year,
        requested_calls=requested_calls,
        strict_report_workflow=strict_report_workflow,
    )


@mcp.tool(
    title="Build research pack",
    description="Short alias for the research evidence summary tool.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_build_research_pack(
    symbol: Symbol,
    min_year: MinYear = 2025,
    requested_calls: RequestedCalls = 2,
    strict_report_workflow: bool = False,
) -> dict[str, Any]:
    """Build a report evidence manifest through the shorter fmp_build_research_pack tool name."""
    clean_symbol = _clean_symbol(symbol)
    requested_calls = _clamp(requested_calls, minimum=1, maximum=4)
    return await build_evidence_pack(
        symbol=clean_symbol,
        min_year=min_year,
        requested_calls=requested_calls,
        strict_report_workflow=strict_report_workflow,
    )


@mcp.tool(
    title="Validate research evidence",
    description="Checks a research evidence payload and returns notes.",
    annotations=READ_ONLY_SAFE,
)
async def fmp_validate_research_evidence(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    """Review an evidence-pack payload informationally and return notes / next actions."""
    return validate_evidence_payload(evidence_pack)


@mcp.tool(
    title="Get research report contract",
    description="Returns suggested research report sections.",
    annotations=READ_ONLY_SAFE,
)
async def research_report_contract(
    sector: Literal["pharma", "healthcare_technology", "general"] = "healthcare_technology",
) -> dict[str, object]:
    """Return suggested report sections and sector overlays."""
    return build_report_contract(sector)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
