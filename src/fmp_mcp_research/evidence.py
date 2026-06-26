from __future__ import annotations

import re
from datetime import date
from typing import Any

from .fmp_client import FMPClient
from .report_contract import REQUIRED_SOURCE_FLAGS, REPORT_OUTPUT_SECTIONS

QA_MARKERS = re.compile(r"(question-and-answer|questions? and answers?|q&a|operator\s*:)", re.I)


def _safe_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def transcript_has_qna(transcript_item: dict[str, Any]) -> bool:
    text = " ".join(str(transcript_item.get(k, "")) for k in ("content", "transcript", "text"))
    return bool(QA_MARKERS.search(text))


def normalize_transcript_dates(raw_dates: Any, min_year: int = 2025, max_items: int = 2) -> list[dict[str, Any]]:
    dates = _safe_list(raw_dates)
    normalized: list[dict[str, Any]] = []
    for item in dates:
        year = item.get("year") or item.get("fiscalYear")
        quarter = item.get("quarter") or item.get("fiscalQuarter")
        try:
            year_i = int(str(year).replace("FY", ""))
            q_i = int(str(quarter).replace("Q", ""))
        except Exception:
            continue
        if year_i >= min_year:
            normalized.append({"year": year_i, "quarter": q_i, "raw": item})
    normalized.sort(key=lambda x: (x["year"], x["quarter"]), reverse=True)
    return normalized[:max_items]


def filter_by_period(rows: Any, periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = {(p["year"], f"Q{p['quarter']}") for p in periods}
    output = []
    for row in _safe_list(rows):
        fiscal_year = row.get("calendarYear") or row.get("year") or row.get("fiscalYear")
        period = row.get("period") or row.get("quarter")
        try:
            key = (int(str(fiscal_year).replace("FY", "")), str(period).upper())
        except Exception:
            continue
        if key in wanted:
            output.append(row)
    return output


async def build_evidence_pack(symbol: str, min_year: int = 2025, requested_calls: int = 2) -> dict[str, Any]:
    client = FMPClient()
    symbol = symbol.upper()
    profile = await client.profile(symbol)

    raw_dates = await client.transcript_dates(symbol)
    periods = normalize_transcript_dates(raw_dates, min_year=min_year, max_items=requested_calls)

    transcript_attempts = []
    transcripts_by_period = []
    for p in periods:
        # Designated MCP attempt + two explicit retries are done by the client's retry policy.
        # We also record retry intent for source-audit traceability.
        raw = await client.transcript(symbol, p["year"], p["quarter"])
        items = _safe_list(raw)
        has_text = any((i.get("content") or i.get("transcript") or i.get("text")) for i in items)
        has_qna = any(transcript_has_qna(i) for i in items)
        transcripts_by_period.append({**p, "items": items, "full_call_text_read_candidate": has_text, "qna_detected_candidate": has_qna})
        transcript_attempts.append({"year": p["year"], "quarter": p["quarter"], "attempts": 3, "success": has_text})

    income = await client.income_statement(symbol, period="quarter", limit=12)
    balance = await client.balance_sheet(symbol, period="quarter", limit=12)
    cashflow = await client.cash_flow(symbol, period="quarter", limit=12)
    key_metrics = await client.key_metrics(symbol, period="quarter", limit=12)
    ratios = await client.ratios(symbol, period="quarter", limit=12)
    growth = await client.financial_growth(symbol, period="quarter", limit=12)

    current_year = date.today().year
    sec = await client.sec_filings(symbol, from_date=f"{min_year}-01-01", to_date=f"{current_year}-12-31", limit=100)

    quarter_audit = []
    for p in periods:
        tp = next((x for x in transcripts_by_period if x["year"] == p["year"] and x["quarter"] == p["quarter"]), None)
        has_transcript = bool(tp and tp["items"])
        has_qna = bool(tp and tp["qna_detected_candidate"])
        period_label = f"Q{p['quarter']} {p['year']}"
        quarter_audit.append({
            "quarter": period_label,
            "earnings_call_exists": "yes" if has_transcript else "no",
            "full_call_text_read": "yes_candidate_requires_agent_reading" if has_transcript else "no",
            "qna_reviewed": "yes_candidate_requires_agent_reading" if has_qna else "no",
            "earnings_release_reviewed": "requires_8k_or_ir_review",
            "financial_tables_reviewed": "yes_from_fmp_statement_tables" if filter_by_period(income, [p]) else "no",
            "main_topic": "agent_to_extract_from_full_call",
            "main_risk": "agent_to_extract_from_full_call_and_release",
        })

    return {
        "symbol": symbol,
        "profile": profile,
        "selected_periods": periods,
        "transcript_dates_raw": raw_dates,
        "transcripts": transcripts_by_period,
        "financial_tables": {
            "income_statement": filter_by_period(income, periods) or _safe_list(income)[:requested_calls],
            "balance_sheet": filter_by_period(balance, periods) or _safe_list(balance)[:requested_calls],
            "cash_flow_statement": filter_by_period(cashflow, periods) or _safe_list(cashflow)[:requested_calls],
            "key_metrics": filter_by_period(key_metrics, periods) or _safe_list(key_metrics)[:requested_calls],
            "ratios": filter_by_period(ratios, periods) or _safe_list(ratios)[:requested_calls],
            "financial_growth": filter_by_period(growth, periods) or _safe_list(growth)[:requested_calls],
        },
        "sec_filings": _safe_list(sec),
        "source_audit_template": {flag: "agent_to_complete_after_reading" for flag in REQUIRED_SOURCE_FLAGS},
        "quarter_by_quarter_coverage_audit_template": quarter_audit,
        "report_required_sections": REPORT_OUTPUT_SECTIONS,
        "important_instruction": "Do not score until the agent has actually read full transcript text including Q&A, official release/8-K or IR release, and financial tables for every requested quarter.",
    }
