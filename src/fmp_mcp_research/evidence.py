from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from .fmp_client import FMPClient
from .report_contract import REQUIRED_SOURCE_FLAGS, REPORT_OUTPUT_SECTIONS

TranscriptSection = Literal["full", "prepared_remarks", "qna", "metadata"]

QA_MARKERS = re.compile(
    r"(question-and-answer|question and answer|questions and answers|q&a|operator instructions|we will now begin the question|first question)",
    re.I,
)
OPERATOR_MARKER = re.compile(r"\boperator\b\s*:|operator instructions", re.I)
CLOSING_MARKERS = re.compile(
    r"(this concludes|conference has now concluded|you may now disconnect|thank you for joining|thank you for your participation|end of (today'?s )?conference)",
    re.I,
)
PREPARED_MARKERS = re.compile(
    r"(prepared remarks|opening remarks|presentation|management discussion|turn the call over)",
    re.I,
)
TRUNCATION_MARKERS = re.compile(
    r"(\.\.\.|\[truncated\]|<truncated>|content truncated|output truncated|continued in next chunk)",
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
    """Return the longest transcript-like text field from one FMP transcript item."""
    candidates: list[str] = []
    for key in ("content", "transcript", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    return max(candidates, key=len) if candidates else ""


def combine_transcript_text(items: list[dict[str, Any]]) -> str:
    texts = [extract_transcript_text(item) for item in items]
    return "\n\n".join(text for text in texts if text)


def transcript_has_qna(transcript_item: dict[str, Any]) -> bool:
    return bool(QA_MARKERS.search(extract_transcript_text(transcript_item)))


def split_transcript_sections(full_text: str) -> dict[str, Any]:
    """Heuristically split a transcript into prepared remarks and Q&A."""
    match = QA_MARKERS.search(full_text)
    if not full_text:
        return {"prepared_remarks": "", "qna": "", "qna_start_offset": None}
    if not match:
        return {"prepared_remarks": full_text, "qna": "", "qna_start_offset": None}
    return {
        "prepared_remarks": full_text[: match.start()].strip(),
        "qna": full_text[match.start() :].strip(),
        "qna_start_offset": match.start(),
    }


def chunk_text(text: str, chunk_size_chars: int = CHUNK_SIZE_CHARS) -> list[dict[str, Any]]:
    chunks = []
    if not text:
        return chunks
    for index, start in enumerate(range(0, len(text), chunk_size_chars), start=1):
        end = min(start + chunk_size_chars, len(text))
        chunks.append(
            {
                "chunk_index": index,
                "total_chunks": None,
                "start_char": start,
                "end_char": end,
                "is_final_chunk": end >= len(text),
                "content": text[start:end],
            }
        )
    total = len(chunks)
    for chunk in chunks:
        chunk["total_chunks"] = total
    return chunks


def assess_transcript_completeness(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Mechanical checks that help the LLM distinguish source absence from truncation."""
    full_text = combine_transcript_text(items)
    sections = split_transcript_sections(full_text)
    qna = sections["qna"]
    prepared = sections["prepared_remarks"]
    word_count = len(full_text.split())
    char_count = len(full_text)
    qna_word_count = len(qna.split())
    prepared_word_count = len(prepared.split())

    has_text = char_count > 0
    qna_detected = bool(QA_MARKERS.search(full_text))
    operator_intro_detected = bool(OPERATOR_MARKER.search(full_text))
    operator_qna_start_detected = bool(QA_MARKERS.search(qna)) if qna else False
    operator_close_detected = bool(CLOSING_MARKERS.search(full_text))
    explicit_truncation_marker_detected = bool(TRUNCATION_MARKERS.search(full_text))
    prepared_remarks_available = bool(prepared.strip())

    # Heuristic counts. They are not perfect, but they are explicit and auditable.
    analyst_question_markers = len(QUESTION_LINE.findall(qna)) if qna else 0
    management_answer_markers = len(ANSWER_LINE.findall(qna)) if qna else 0

    truncation_reasons: list[str] = []
    if not has_text:
        truncation_reasons.append("empty_transcript_payload")
    if has_text and word_count < MIN_FULL_CALL_WORDS:
        truncation_reasons.append("too_short_for_typical_full_earnings_call")
    if has_text and not qna_detected:
        truncation_reasons.append("qna_start_not_detected")
    if qna_detected and qna_word_count < 250:
        truncation_reasons.append("qna_detected_but_too_short")
    if has_text and not operator_close_detected:
        truncation_reasons.append("operator_close_not_detected")
    if explicit_truncation_marker_detected:
        truncation_reasons.append("explicit_truncation_marker_detected")

    likely_complete = has_text and not truncation_reasons
    qna_likely_complete = qna_detected and qna_word_count >= 250 and operator_close_detected

    return {
        "has_text": has_text,
        "returned_character_count": char_count,
        "total_character_count": char_count,
        "word_count": word_count,
        "prepared_remarks_available": prepared_remarks_available,
        "prepared_remarks_word_count": prepared_word_count,
        "prepared_remarks_complete": prepared_remarks_available and word_count >= MIN_FULL_CALL_WORDS,
        "qna_available": qna_detected,
        "qna_word_count": qna_word_count,
        "qna_complete": qna_likely_complete,
        "full_transcript_complete": likely_complete,
        "operator_intro_detected": operator_intro_detected,
        "operator_qna_start_detected": operator_qna_start_detected,
        "operator_close_detected": operator_close_detected,
        "explicit_truncation_marker_detected": explicit_truncation_marker_detected,
        "likely_truncated_or_incomplete": not likely_complete,
        "truncation_reasons": truncation_reasons,
        "qna_validation": {
            "analyst_question_markers_detected": analyst_question_markers,
            "management_answer_markers_detected": management_answer_markers,
            "qna_likely_complete": qna_likely_complete,
        },
    }


def normalize_transcript_dates(raw_dates: Any, min_year: int = 2025, max_items: int = 2) -> list[dict[str, Any]]:
    dates = _safe_list(raw_dates)
    normalized: list[dict[str, Any]] = []
    for item in dates:
        year_i = _safe_int(item.get("year") or item.get("fiscalYear"))
        q_i = _safe_int(item.get("quarter") or item.get("fiscalQuarter"))
        if year_i is None or q_i is None:
            continue
        if year_i >= min_year:
            normalized.append(
                {
                    "year": year_i,
                    "quarter": q_i,
                    "period_label": f"Q{q_i} {year_i}",
                    "call_date": item.get("date") or item.get("callDate") or item.get("fillingDate"),
                    "transcript_available": True,
                    "source": "FMP earning-call-transcript-dates",
                    "recommended_fetch_tool": "fmp_get_earnings_call_transcript",
                    "raw": item,
                }
            )
    normalized.sort(key=lambda x: (x["year"], x["quarter"], str(x.get("call_date") or "")), reverse=True)
    return normalized[:max_items]


def filter_by_period(rows: Any, periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = {(p["year"], f"Q{p['quarter']}") for p in periods}
    output = []
    for row in _safe_list(rows):
        fiscal_year = row.get("calendarYear") or row.get("year") or row.get("fiscalYear")
        period = row.get("period") or row.get("quarter")
        year_i = _safe_int(fiscal_year)
        period_s = str(period).upper() if period is not None else ""
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
        "audit_note": (
            "Exact rows matched to selected earnings-call periods."
            if matched
            else "No exact period match; agent must not treat fallback/latest rows as reviewed for selected periods unless manually verified."
        ),
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
    symbol: str,
    year: int,
    quarter: int,
    raw: Any,
    section: TranscriptSection = "full",
    include_full_text: bool = True,
    max_chars: int = DEFAULT_TRANSCRIPT_CHAR_BUDGET,
    chunk_size_chars: int = CHUNK_SIZE_CHARS,
) -> dict[str, Any]:
    items = _safe_list(raw)
    full_text = combine_transcript_text(items)
    sections = split_transcript_sections(full_text)
    completeness = assess_transcript_completeness(items)

    if section == "prepared_remarks":
        selected_text = sections["prepared_remarks"]
    elif section == "qna":
        selected_text = sections["qna"]
    elif section == "metadata":
        selected_text = ""
    else:
        selected_text = full_text

    content_truncated_by_tool = include_full_text and len(selected_text) > max_chars
    returned_text = selected_text[:max_chars] if content_truncated_by_tool else selected_text
    chunks = chunk_text(selected_text, chunk_size_chars=chunk_size_chars) if selected_text else []

    transcript_available = bool(items and full_text)
    source_complete = bool(completeness["full_transcript_complete"])
    returned_complete = bool(include_full_text and not content_truncated_by_tool and selected_text)
    qna_included = section in {"full", "qna"} and bool(sections["qna"]) and not (section == "full" and content_truncated_by_tool and len(returned_text) < (sections["qna_start_offset"] or 10**12))

    status = "complete" if source_complete and returned_complete else "incomplete"
    if not transcript_available:
        status = "missing"

    record = {
        "symbol": symbol.upper(),
        "year": year,
        "quarter": quarter,
        "period_label": f"Q{quarter} {year}",
        "source_name": "FMP earning-call-transcript",
        "call_exists": True,
        "transcript_available": transcript_available,
        "transcript_status": status,
        "section_returned": section,
        "full_transcript_included_in_payload": section == "full" and returned_complete,
        "included_content_is_excerpt": bool(selected_text) and not returned_complete,
        "content_truncated_by_tool": content_truncated_by_tool,
        "content_truncated_by_pack": content_truncated_by_tool,
        "returned_character_count": len(returned_text),
        "total_character_count": len(selected_text),
        "full_source_character_count": len(full_text),
        "qna_detected_in_source": completeness["qna_available"],
        "qna_included_in_payload": qna_included,
        "qna_complete": completeness["qna_complete"],
        "prepared_remarks_available": completeness["prepared_remarks_available"],
        "prepared_remarks_complete": completeness["prepared_remarks_complete"],
        "operator_qna_start_detected": completeness["operator_qna_start_detected"],
        "operator_close_detected": completeness["operator_close_detected"],
        "completeness": completeness,
        "sections_metadata": {
            "prepared_remarks_character_count": len(sections["prepared_remarks"]),
            "qna_character_count": len(sections["qna"]),
            "qna_start_offset": sections["qna_start_offset"],
        },
        "chunks_manifest": [
            {k: v for k, v in chunk.items() if k != "content"}
            for chunk in chunks
        ],
        "text": returned_text if include_full_text and section != "metadata" else None,
        "sections": None,
        "recommended_followup_tool": "fmp_get_earnings_call_transcript" if status != "complete" or content_truncated_by_tool else None,
        "must_call_dedicated_transcript_tool": status != "complete" or content_truncated_by_tool,
        "score_allowed_from_current_payload": status == "complete" and qna_included,
        "next_best_action": None,
        "warning": None,
    }

    if section == "full" and include_full_text and not content_truncated_by_tool:
        record["sections"] = {"prepared_remarks": sections["prepared_remarks"], "qna": sections["qna"]}

    if record["must_call_dedicated_transcript_tool"]:
        record["next_best_action"] = {
            "tool": "fmp_get_earnings_call_transcript",
            "arguments": {"symbol": symbol.upper(), "year": year, "quarter": quarter, "section": "full"},
            "reason": "Transcript is missing, incomplete, or not fully included in the current payload.",
        }
        record["warning"] = (
            "Transcript evidence is not certified as fully returned in this payload. "
            "Do not mark full call or Q&A as reviewed until the dedicated transcript tool returns complete prepared remarks and Q&A."
        )

    return record


async def fetch_transcript_with_quality_retries(
    client: FMPClient,
    symbol: str,
    year: int,
    quarter: int,
    semantic_attempts: int = 3,
) -> dict[str, Any]:
    attempts = []
    best: dict[str, Any] | None = None
    for attempt_number in range(1, max(1, semantic_attempts) + 1):
        raw = await client.transcript(symbol, year, quarter)
        items = _safe_list(raw)
        completeness = assess_transcript_completeness(items)
        attempt = {
            "attempt_number": attempt_number,
            "success": completeness["full_transcript_complete"],
            "word_count": completeness["word_count"],
            "qna_complete": completeness["qna_complete"],
            "operator_close_detected": completeness["operator_close_detected"],
            "likely_truncated_or_incomplete": completeness["likely_truncated_or_incomplete"],
            "truncation_reasons": completeness["truncation_reasons"],
            "raw": raw,
            "items": items,
            "completeness": completeness,
        }
        attempts.append(attempt)
        if best is None or attempt["word_count"] > best["word_count"]:
            best = attempt
        if completeness["full_transcript_complete"]:
            break
    return {"attempts": attempts, "best_attempt": best}


async def build_evidence_pack(
    symbol: str,
    min_year: int = 2025,
    requested_calls: int = 2,
    strict_report_workflow: bool = True,
    include_transcript_text: bool = False,
    max_transcript_chars: int = 24_000,
    transcript_semantic_attempts: int = 3,
) -> dict[str, Any]:
    client = FMPClient()
    symbol = symbol.upper()
    requested_calls = max(1, requested_calls)

    profile = await client.profile(symbol)
    raw_dates = await client.transcript_dates(symbol)
    periods = normalize_transcript_dates(raw_dates, min_year=min_year, max_items=requested_calls)

    blocking_items: list[dict[str, Any]] = []
    recommended_next_actions: list[dict[str, Any]] = []
    evidence_manifest: list[dict[str, Any]] = []
    transcripts_by_period: list[dict[str, Any]] = []

    if not periods:
        issue = {
            "issue": "no_earnings_call_dates_found_at_or_after_min_year",
            "min_year": min_year,
            "reason": "No transcript periods were discovered for the requested min_year threshold.",
        }
        blocking_items.append(issue)
        recommended_next_actions.append(
            {
                "tool": "fmp_list_transcript_dates",
                "arguments": {"symbol": symbol, "min_year": min_year - 1, "limit": requested_calls},
                "reason": "Find the latest available earnings-call transcript periods by widening the year filter.",
            }
        )

    for p in periods:
        fetched = await fetch_transcript_with_quality_retries(
            client=client,
            symbol=symbol,
            year=p["year"],
            quarter=p["quarter"],
            semantic_attempts=transcript_semantic_attempts,
        )
        best = fetched["best_attempt"]
        raw = best["raw"] if best else []
        payload = build_transcript_payload(
            symbol=symbol,
            year=p["year"],
            quarter=p["quarter"],
            raw=raw,
            section="full",
            include_full_text=include_transcript_text,
            max_chars=max_transcript_chars,
        )
        payload["discovery"] = p
        payload["semantic_fetch_attempts"] = [
            {k: v for k, v in attempt.items() if k not in {"raw", "items", "completeness"}}
            for attempt in fetched["attempts"]
        ]
        payload["raw_items"] = _safe_list(raw) if include_transcript_text and not payload["content_truncated_by_tool"] else None
        transcripts_by_period.append(payload)

        requires_followup = payload["must_call_dedicated_transcript_tool"] or not payload["qna_complete"]
        evidence_manifest.append(
            {
                "document_type": "earnings_call_transcript",
                "symbol": symbol,
                "period": payload["period_label"],
                "available": payload["transcript_available"],
                "included_in_payload": include_transcript_text,
                "complete_in_payload": payload["full_transcript_included_in_payload"],
                "source_complete": payload["completeness"]["full_transcript_complete"],
                "qna_detected_in_source": payload["qna_detected_in_source"],
                "qna_included_in_payload": payload["qna_included_in_payload"],
                "requires_followup_fetch": requires_followup,
                "recommended_tool": "fmp_get_earnings_call_transcript" if requires_followup else None,
            }
        )
        if requires_followup:
            action = {
                "tool": "fmp_get_earnings_call_transcript",
                "arguments": {"symbol": symbol, "year": p["year"], "quarter": p["quarter"], "section": "full"},
                "reason": f"{payload['period_label']} transcript/Q&A is not certified as complete in the current evidence pack payload.",
            }
            recommended_next_actions.append(action)
            blocking_items.append(
                {
                    "period": payload["period_label"],
                    "issue": "earnings_call_transcript_or_qna_not_certified_complete_in_current_payload",
                    "required_action": action,
                }
            )

    income = await client.income_statement(symbol, period="quarter", limit=12)
    balance = await client.balance_sheet(symbol, period="quarter", limit=12)
    cashflow = await client.cash_flow(symbol, period="quarter", limit=12)
    key_metrics = await client.key_metrics(symbol, period="quarter", limit=12)
    ratios = await client.ratios(symbol, period="quarter", limit=12)
    growth = await client.financial_growth(symbol, period="quarter", limit=12)

    tables = {
        "income_statement": financial_table_record("income_statement", income, periods, allow_fallback=not strict_report_workflow),
        "balance_sheet": financial_table_record("balance_sheet", balance, periods, allow_fallback=not strict_report_workflow),
        "cash_flow_statement": financial_table_record("cash_flow_statement", cashflow, periods, allow_fallback=not strict_report_workflow),
        "key_metrics": financial_table_record("key_metrics", key_metrics, periods, allow_fallback=not strict_report_workflow),
        "ratios": financial_table_record("ratios", ratios, periods, allow_fallback=not strict_report_workflow),
        "financial_growth": financial_table_record("financial_growth", growth, periods, allow_fallback=not strict_report_workflow),
    }
    for name, record in tables.items():
        evidence_manifest.append(
            {
                "document_type": "financial_table",
                "table_name": name,
                "symbol": symbol,
                "periods_requested": [p["period_label"] for p in periods],
                "available": bool(record["matched_rows"] or record["fallback_rows"]),
                "included_in_payload": True,
                "complete_in_payload": bool(record["matched_rows"]),
                "requires_followup_fetch": not bool(record["matched_rows"]),
                "match_status": record["match_status"],
            }
        )
        if not record["matched_rows"]:
            blocking_items.append({"issue": "financial_table_no_exact_period_match", "table_name": name})

    current_year = date.today().year
    sec = await client.sec_filings(symbol, from_date=f"{min_year}-01-01", to_date=f"{current_year}-12-31", limit=100)
    sec_prioritized = prioritize_sec_filings(sec)
    for p in periods:
        evidence_manifest.append(
            {
                "document_type": "official_earnings_release_or_filing",
                "symbol": symbol,
                "period": p["period_label"],
                "available": bool(sec_prioritized["earnings_release_candidates"] or sec_prioritized["relevant_filings_for_report"]),
                "included_in_payload": True,
                "complete_in_payload": False,
                "requires_followup_fetch": True,
                "recommended_tool": "fmp_search_sec_filings",
                "audit_note": "MCP can identify candidate filings; the LLM/agent must open/read the actual release or 8-K exhibit before marking reviewed.",
            }
        )
        recommended_next_actions.append(
            {
                "tool": "fmp_search_sec_filings",
                "arguments": {"symbol": symbol, "from_date": f"{min_year}-01-01", "to_date": f"{current_year}-12-31", "limit": 100},
                "reason": f"Open/read the official earnings release or 8-K/6-K exhibit for {p['period_label']} before scoring.",
            }
        )

    quarter_audit = []
    for p in periods:
        period_label = p["period_label"]
        tp = next((x for x in transcripts_by_period if x["year"] == p["year"] and x["quarter"] == p["quarter"]), None)
        quarter_audit.append(
            {
                "quarter": period_label,
                "earnings_call_exists": "yes" if tp and tp["call_exists"] else "no",
                "transcript_available": "yes" if tp and tp["transcript_available"] else "no",
                "full_call_text_returned_to_agent": "yes" if tp and tp["full_transcript_included_in_payload"] else "no",
                "full_call_text_read": "unknown_agent_must_confirm_after_dedicated_fetch",
                "qna_detected_in_source": "yes" if tp and tp["qna_detected_in_source"] else "no",
                "qna_returned_to_agent": "yes" if tp and tp["qna_included_in_payload"] else "no",
                "qna_reviewed": "unknown_agent_must_confirm_after_dedicated_fetch",
                "earnings_release_available": "yes_candidate" if sec_prioritized["earnings_release_candidates"] else "unknown_or_requires_ir_edgar_fallback",
                "earnings_release_reviewed": "unknown_agent_must_confirm",
                "financial_tables_available": "yes" if any(tables[name]["matched_rows"] for name in tables) else "no_exact_period_match",
                "financial_tables_reviewed": "unknown_agent_must_confirm",
                "main_topic": "agent_to_extract_from_full_call",
                "main_risk": "agent_to_extract_from_full_call_qna_and_release",
                "confidence_impact": "blocking_until_full_transcript_qna_release_and_financial_tables_are_reviewed",
            }
        )

    ready_to_score = False
    if not strict_report_workflow:
        ready_to_score = len(blocking_items) == 0

    return {
        "symbol": symbol,
        "profile": profile,
        "requested_policy": {
            "min_year": min_year,
            "requested_calls": requested_calls,
            "strict_report_workflow": strict_report_workflow,
            "include_transcript_text": include_transcript_text,
            "max_transcript_chars": max_transcript_chars,
            "transcript_semantic_attempts": transcript_semantic_attempts,
            "canonical_full_transcript_tool": "fmp_get_earnings_call_transcript",
        },
        "selected_periods": periods,
        "transcript_dates_raw": raw_dates,
        "transcripts": transcripts_by_period,
        "financial_tables": tables,
        "sec_filings": sec_prioritized,
        "evidence_manifest": evidence_manifest,
        "source_audit_template": {flag: "agent_to_complete_after_actual_source_reading" for flag in REQUIRED_SOURCE_FLAGS},
        "quarter_by_quarter_coverage_audit_template": quarter_audit,
        "scoring_readiness": {
            "ready_to_score": ready_to_score,
            "score_allowed_now": ready_to_score,
            "blocking_items": blocking_items,
            "recommended_next_actions": recommended_next_actions,
            "guardrail": (
                "This evidence pack identifies and audits required sources but does not certify agent review. "
                "For strict report workflows, call fmp_get_earnings_call_transcript for every selected period and read prepared remarks plus Q&A before scoring."
            ),
        },
        "process_checklist": {
            "selected_two_recent_calls_from_min_year": len(periods) == requested_calls,
            "dedicated_transcript_fetch_required_for_each_selected_period": True,
            "earnings_release_review_required": True,
            "financial_tables_review_required": True,
            "score_allowed_now": ready_to_score,
        },
        "report_required_sections": REPORT_OUTPUT_SECTIONS,
        "important_warning": (
            "WARNING: fmp_build_research_evidence_pack is an orchestrator/manifest tool, not proof that full transcripts were read. "
            "If transcript text is omitted or truncated here, do not treat it as missing in the source. Use fmp_get_earnings_call_transcript."
        ),
    }


def validate_evidence_payload(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    """Offline validator for an evidence-pack payload. It does not infer agent reading."""
    manifest = evidence_pack.get("evidence_manifest", [])
    blocking = []
    for item in manifest:
        if item.get("requires_followup_fetch"):
            blocking.append(item)
    return {
        "symbol": evidence_pack.get("symbol"),
        "ready_to_score": not blocking and evidence_pack.get("scoring_readiness", {}).get("ready_to_score", False),
        "blocking_items": blocking,
        "recommended_next_actions": evidence_pack.get("scoring_readiness", {}).get("recommended_next_actions", []),
    }
