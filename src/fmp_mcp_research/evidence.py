from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

TranscriptSection = Literal["full", "prepared_remarks", "qna", "metadata"]

QA_START_MARKERS = re.compile(
    r"(question-and-answer session\s*$|questions and answers\s*$|q&a session\s*$|"
    r"operator:\s*(we will now begin|we will now open|at this time we will conduct|we are now ready to begin).*question|"
    r"operator:\s*our first question|our first question comes from|first question comes from|"
    r"we take the first question|we take the next question|we take the last question)",
    re.I | re.M,
)
QA_MENTION_MARKERS = re.compile(r"(question-and-answer|question and answer|questions and answers|q&a)", re.I)
FALSE_QA_INTRO_MARKERS = re.compile(
    r"(question-and-answer session will follow|q&a session will follow|questions? and answers? session will follow)",
    re.I,
)
OPERATOR_MARKER = re.compile(r"\boperator\b\s*:|operator instructions", re.I)
CLOSING_MARKERS = re.compile(
    r"(this concludes|conference has now concluded|you may now disconnect|thank you for joining|"
    r"thank you for your participation|end of (today'?s )?conference)",
    re.I,
)
TRUNCATION_MARKERS = re.compile(
    r"(\[truncated\]|\(truncated\)|\.\.\.\s*$|content truncated|output truncated|"
    r"continued in next chunk|continued on next page|transcript ends|audio ends|call ends abruptly)",
    re.I,
)
QUESTION_LINE = re.compile(r"\b(question|analyst|operator)\b", re.I)
ANSWER_LINE = re.compile(r"\b(answer|ceo|cfo|chief|president|officer|management)\b", re.I)

DEFAULT_TRANSCRIPT_CHAR_BUDGET = 120_000
MIN_FULL_CALL_WORDS = 2_500
CHUNK_SIZE_CHARS = 24_000


def _safe_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _safe_int(value: Any) -> int | None:
    try:
        return int(str(value).upper().replace("FY", "").replace("Q", ""))
    except Exception:
        return None


def extract_transcript_text(item: dict[str, Any]) -> str:
    candidates = []
    for key in ("content", "transcript", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    return max(candidates, key=len) if candidates else ""


def combine_transcript_text(items: list[dict[str, Any]]) -> str:
    return "\n\n".join(text for item in items if (text := extract_transcript_text(item)))


def split_transcript_sections(full_text: str) -> dict[str, Any]:
    if not full_text:
        return {"prepared_remarks": "", "qna": "", "qna_start_offset": None, "section_detection_warning": None}
    match = QA_START_MARKERS.search(full_text)
    if not match:
        warning = "qna_mentioned_but_no_reliable_qna_start" if QA_MENTION_MARKERS.search(full_text) else None
        return {"prepared_remarks": full_text, "qna": "", "qna_start_offset": None, "section_detection_warning": warning}
    nearby = full_text[max(0, match.start() - 80) : match.end() + 120]
    if FALSE_QA_INTRO_MARKERS.search(nearby):
        return {"prepared_remarks": full_text, "qna": "", "qna_start_offset": None, "section_detection_warning": "qna_start_likely_false_positive"}
    return {
        "prepared_remarks": full_text[: match.start()].strip(),
        "qna": full_text[match.start() :].strip(),
        "qna_start_offset": match.start(),
        "section_detection_warning": None,
    }


def chunk_text(text: str, chunk_size_chars: int = CHUNK_SIZE_CHARS) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(text), chunk_size_chars), start=1):
        end = min(start + chunk_size_chars, len(text))
        chunks.append({"chunk_index": index, "total_chunks": None, "start_char": start, "end_char": end, "is_final_chunk": end >= len(text), "content": text[start:end]})
    total = len(chunks)
    for chunk in chunks:
        chunk["total_chunks"] = total
    return chunks


def transcript_ends_mid_sentence(full_text: str) -> bool:
    if not full_text:
        return False
    tail = full_text.strip()[-300:].strip()
    if not tail:
        return False
    unfinished = (
        "let me now take you through",
        "let me take you through",
        "i will now take you through",
        "i'll now take you through",
        "turn the call",
        "turn it over",
        "with that,",
        "moving to",
        "starting with",
    )
    lower_tail = tail.lower().rstrip(".!?\"'”’)]}")
    if any(lower_tail.endswith(x) for x in unfinished):
        return True
    return not bool(re.search(r'[.!?]"?$|disconnect your lines\.?$|conclude.*call\.?$', tail, re.I))


def has_real_explicit_truncation_marker(full_text: str) -> bool:
    if not full_text:
        return False
    return bool(TRUNCATION_MARKERS.search(full_text))


def assess_transcript_completeness(items: list[dict[str, Any]]) -> dict[str, Any]:
    full_text = combine_transcript_text(items)
    sections = split_transcript_sections(full_text)
    qna = sections["qna"]
    prepared = sections["prepared_remarks"]
    word_count = len(full_text.split())
    char_count = len(full_text)
    qna_word_count = len(qna.split())
    has_text = char_count > 0
    qna_detected = bool(QA_START_MARKERS.search(full_text))
    operator_close_detected = bool(CLOSING_MARKERS.search(full_text))
    explicit_truncation_marker_detected = has_real_explicit_truncation_marker(full_text)
    ends_mid_sentence = transcript_ends_mid_sentence(full_text)
    qna_mention_detected = bool(QA_MENTION_MARKERS.search(full_text))

    warnings: list[str] = []
    if not has_text:
        warnings.append("empty_transcript_payload")
    if has_text and word_count < MIN_FULL_CALL_WORDS:
        warnings.append("too_short_for_typical_full_earnings_call")
    if has_text and not qna_detected and qna_mention_detected:
        warnings.append("qna_mentioned_but_reliable_qna_start_not_detected")
    elif has_text and not qna_detected:
        warnings.append("qna_start_not_detected")
    if sections.get("section_detection_warning"):
        warnings.append(sections["section_detection_warning"])
    if qna_detected and qna_word_count < 250:
        warnings.append("qna_detected_but_too_short")
    if has_text and not operator_close_detected:
        warnings.append("operator_close_not_detected")
    if explicit_truncation_marker_detected:
        warnings.append("explicit_truncation_marker_detected")
    if ends_mid_sentence:
        warnings.append("ends_mid_sentence")

    strong_incomplete = (
        not has_text
        or explicit_truncation_marker_detected
        or ends_mid_sentence
        or (word_count < MIN_FULL_CALL_WORDS and not operator_close_detected and not qna_detected)
    )
    complete_with_warnings = has_text and not strong_incomplete

    return {
        "has_text": has_text,
        "returned_character_count": char_count,
        "total_character_count": char_count,
        "word_count": word_count,
        "prepared_remarks_available": bool(prepared.strip()),
        "prepared_remarks_word_count": len(prepared.split()),
        "qna_available": qna_detected,
        "qna_word_count": qna_word_count,
        "qna_complete": qna_detected and qna_word_count >= 250 and operator_close_detected,
        "full_transcript_complete": complete_with_warnings and operator_close_detected,
        "transcript_quality_status": "complete" if complete_with_warnings and operator_close_detected else ("usable_with_warnings" if complete_with_warnings else "incomplete"),
        "operator_intro_detected": bool(OPERATOR_MARKER.search(full_text)),
        "operator_qna_start_detected": bool(QA_START_MARKERS.search(qna)) if qna else False,
        "operator_close_detected": operator_close_detected,
        "explicit_truncation_marker_detected": explicit_truncation_marker_detected,
        "ends_mid_sentence": ends_mid_sentence,
        "section_detection_warning": sections.get("section_detection_warning"),
        "qna_mention_detected": qna_mention_detected,
        "likely_truncated_or_incomplete": not complete_with_warnings,
        "quality_warnings": warnings,
        "truncation_reasons": warnings,
        "qna_validation": {
            "analyst_question_markers_detected": len(QUESTION_LINE.findall(qna)) if qna else 0,
            "management_answer_markers_detected": len(ANSWER_LINE.findall(qna)) if qna else 0,
            "qna_likely_complete": qna_detected and qna_word_count >= 250 and operator_close_detected,
        },
    }


def normalize_transcript_dates(raw_dates: Any, min_year: int = 2025, max_items: int = 2) -> list[dict[str, Any]]:
    normalized = []
    for item in _safe_list(raw_dates):
        year_i = _safe_int(item.get("year") or item.get("fiscalYear"))
        q_i = _safe_int(item.get("quarter") or item.get("fiscalQuarter"))
        if year_i is None or q_i is None or year_i < min_year:
            continue
        normalized.append({
            "year": year_i,
            "quarter": q_i,
            "period_label": f"Q{q_i} {year_i}",
            "call_date": item.get("date") or item.get("callDate") or item.get("fillingDate"),
            "transcript_available": True,
            "source": "FMP earning-call-transcript-dates",
            "recommended_fetch_tool": "fmp_get_earnings_call_transcript",
            "raw": item,
        })
    normalized.sort(key=lambda x: (x["year"], x["quarter"], str(x.get("call_date") or "")), reverse=True)
    return normalized[:max_items]


def filter_by_period(rows: Any, periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = {(p["year"], f"Q{p['quarter']}") for p in periods}
    output = []
    for row in _safe_list(rows):
        year_i = _safe_int(row.get("calendarYear") or row.get("year") or row.get("fiscalYear"))
        period_s = str(row.get("period") or row.get("quarter") or "").upper()
        if year_i is not None and (year_i, period_s) in wanted:
            output.append(row)
    return output


def financial_table_record(name: str, rows: Any, periods: list[dict[str, Any]], allow_fallback: bool = False) -> dict[str, Any]:
    all_rows = _safe_list(rows)
    matched = filter_by_period(all_rows, periods)
    fallback_rows = [] if matched or not allow_fallback else all_rows[: len(periods)]
    return {
        "table_name": name,
        "matched_rows": matched,
        "match_status": "exact_period_match" if matched else "no_exact_period_match",
        "fallback_rows": fallback_rows,
        "fallback_used": bool(fallback_rows),
        "audit_note": "Exact rows matched to selected earnings-call periods." if matched else "No exact period match; do not treat fallback/latest rows as period-reviewed unless manually verified.",
    }


def prioritize_sec_filings(filings: Any) -> dict[str, Any]:
    relevant_forms = {"8-K", "6-K", "10-Q", "10-K", "20-F", "40-F"}
    rows = _safe_list(filings)

    def form_of(row: dict[str, Any]) -> str:
        return str(row.get("formType") or row.get("type") or row.get("form") or "").upper()

    prioritized = [row for row in rows if form_of(row) in relevant_forms]
    earnings_like = [
        row
        for row in prioritized
        if form_of(row) in {"8-K", "6-K"}
        and re.search(r"earnings|results|release|exhibit|ex-99|exhibit 99", str(row), re.I)
    ]
    return {
        "relevant_filings_for_report": prioritized,
        "earnings_release_candidates": earnings_like,
        "all_filings": rows,
        "audit_note": "Prioritize 8-K/6-K earnings-release exhibits, then 10-Q/10-K. Ignore Form 4/144/13G unless specifically relevant.",
    }


def build_transcript_payload(
    *,
    symbol: str,
    year: int,
    quarter: int,
    raw: Any,
    section: TranscriptSection = "full",
    include_full_text: bool = True,
    max_chars: int = DEFAULT_TRANSCRIPT_CHAR_BUDGET,
) -> dict[str, Any]:
    items = _safe_list(raw)
    full_text = combine_transcript_text(items)
    sections = split_transcript_sections(full_text)
    selected_text = {
        "full": full_text,
        "prepared_remarks": sections["prepared_remarks"],
        "qna": sections["qna"],
        "metadata": "",
    }[section]
    content_truncated_by_tool = len(selected_text) > max_chars
    returned_text = selected_text[:max_chars] if include_full_text else ""
    assessment = assess_transcript_completeness(items)
    assessment["total_character_count"] = len(selected_text)
    assessment["returned_character_count"] = len(returned_text)
    return {
        "symbol": symbol.upper(),
        "year": year,
        "quarter": quarter,
        "section": section,
        "source_name": "FMP earning-call-transcript",
        "transcript_available": bool(items and full_text),
        "content_truncated_by_tool": content_truncated_by_tool,
        "returned_character_count": len(returned_text),
        "total_character_count": len(selected_text),
        "full_text": returned_text if section == "full" else None,
        "prepared_remarks": returned_text if section == "prepared_remarks" else (sections["prepared_remarks"][:max_chars] if section == "full" and include_full_text else None),
        "qna": returned_text if section == "qna" else (sections["qna"][:max_chars] if section == "full" and include_full_text else None),
        "chunks": chunk_text(selected_text) if content_truncated_by_tool else [],
        "completeness": assessment,
        "recommended_next_actions": transcript_next_actions(symbol.upper(), year, quarter, section, content_truncated_by_tool, assessment),
    }


def transcript_next_actions(symbol: str, year: int, quarter: int, section: str, truncated: bool, assessment: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    if truncated and section == "full":
        for next_section in ("prepared_remarks", "qna"):
            actions.append({
                "tool": "fmp_get_earnings_call_transcript",
                "arguments": {"symbol": symbol, "year": year, "quarter": quarter, "section": next_section},
                "reason": f"Fetch {next_section} separately because the full transcript exceeded the server payload budget.",
            })
    if assessment.get("transcript_quality_status") == "incomplete":
        actions.append({
            "tool": "fmp_list_transcript_dates",
            "arguments": {"symbol": symbol, "min_year": max(2000, year - 1), "limit": 4},
            "reason": "Confirm the period exists and check adjacent periods before treating the transcript as unavailable or incomplete.",
        })
    return actions


async def build_evidence_pack(
    *,
    symbol: str,
    min_year: int = 2025,
    requested_calls: int = 2,
    strict_report_workflow: bool = True,
    include_transcript_text: bool = False,
    max_transcript_chars: int = 24_000,
) -> dict[str, Any]:
    from .fmp_client import FMPClient

    client = FMPClient()
    transcript_dates = await client.transcript_dates(symbol)
    selected_periods = normalize_transcript_dates(transcript_dates, min_year=min_year, max_items=requested_calls)
    income, balance, cash, metrics, ratios, growth = await _fetch_financials(client, symbol, requested_calls)
    filings = await client.sec_filings(symbol, from_date=f"{min_year}-01-01", to_date=date.today().isoformat())

    transcript_statuses = []
    for period in selected_periods:
        transcript_statuses.append({
            "year": period["year"],
            "quarter": period["quarter"],
            "transcript_available": True,
            "full_call_text_included": include_transcript_text,
            "full_call_text_read_by_agent": False,
            "recommended_fetch_tool": "fmp_get_earnings_call_transcript",
        })

    financial_tables = [
        financial_table_record("income_statement", income, selected_periods),
        financial_table_record("balance_sheet", balance, selected_periods),
        financial_table_record("cash_flow_statement", cash, selected_periods),
        financial_table_record("key_metrics", metrics, selected_periods),
        financial_table_record("ratios", ratios, selected_periods),
        financial_table_record("financial_growth", growth, selected_periods),
    ]
    blocking_items = []
    if len(selected_periods) < requested_calls:
        blocking_items.append("fewer_transcript_periods_found_than_requested")
    if strict_report_workflow:
        blocking_items.append("agent_must_fetch_and_read_each_selected_transcript_before_scoring")
        blocking_items.append("agent_must_review_official_release_or_relevant_filing_before_scoring")

    return {
        "evidence_pack_version": "0.3.0",
        "symbol": symbol.upper(),
        "selected_periods": selected_periods,
        "evidence_manifest": {
            "transcripts": transcript_statuses,
            "financial_tables": financial_tables,
            "sec_filings": prioritize_sec_filings(filings),
        },
        "source_audit_template": [{"period_label": p["period_label"], "full_call_text_read": "no", "qna_reviewed": "no", "official_release_reviewed": "no", "financial_tables_reviewed": "no"} for p in selected_periods],
        "scoring_readiness": {
            "allowed": not strict_report_workflow and not blocking_items,
            "blocking_items": blocking_items,
            "strict_report_workflow": strict_report_workflow,
        },
        "recommended_next_actions": [
            {
                "tool": "fmp_get_earnings_call_transcript",
                "arguments": {"symbol": symbol.upper(), "year": p["year"], "quarter": p["quarter"], "section": "full"},
                "reason": "Fetch and read the full transcript before scoring.",
            }
            for p in selected_periods
        ],
    }


async def _fetch_financials(client: Any, symbol: str, limit: int) -> tuple[Any, Any, Any, Any, Any, Any]:
    period = "quarter"
    return (
        await client.income_statement(symbol, period, limit),
        await client.balance_sheet(symbol, period, limit),
        await client.cash_flow(symbol, period, limit),
        await client.key_metrics(symbol, period, limit),
        await client.ratios(symbol, period, limit),
        await client.financial_growth(symbol, period, limit),
    )


def validate_evidence_payload(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    blocking = list(evidence_pack.get("scoring_readiness", {}).get("blocking_items", []))
    periods = evidence_pack.get("selected_periods") or []
    if not periods:
        blocking.append("no_selected_periods")
    audit_rows = evidence_pack.get("source_audit_template") or []
    for row in audit_rows:
        if row.get("full_call_text_read") != "yes":
            blocking.append(f"full_call_text_not_read:{row.get('period_label')}")
        if row.get("qna_reviewed") != "yes":
            blocking.append(f"qna_not_reviewed:{row.get('period_label')}")
        if row.get("official_release_reviewed") != "yes":
            blocking.append(f"official_release_not_reviewed:{row.get('period_label')}")
    return {
        "evidence_pack_version": evidence_pack.get("evidence_pack_version"),
        "allowed": not blocking,
        "blocking_items": sorted(set(blocking)),
        "recommended_next_actions": evidence_pack.get("recommended_next_actions", []),
    }
