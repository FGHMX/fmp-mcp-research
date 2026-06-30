from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

TranscriptSection = Literal["full", "prepared_remarks", "q_and_a", "qna", "metadata"]

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

EVIDENCE_PACK_VERSION = "0.3.4"
DEFAULT_TRANSCRIPT_CHAR_BUDGET = 120_000
MIN_FULL_CALL_WORDS = 2_500
CHUNK_SIZE_CHARS = 24_000
OPENAI_RETRY_SUGGESTION = (
    "If the host rejects or drops this tool call, it may be useful to retry the same call "
    "up to 3 total attempts before treating the source as unavailable."
)
MIN_REASONABLE_QA_POSITION_RATIO = 0.12
MAX_MANAGEMENT_TO_OPERATOR_DISTANCE = 4000


@dataclass
class SplitCandidate:
    position: int
    method: str
    confidence: float
    matched_text: str
    position_ratio: float = 0.0
    adjusted_confidence: float = 0.0


def clean_transcript_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_for_search(text: str) -> str:
    replacements = {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        "…": "...",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def normalize_speaker_labels(text: str) -> str:
    return re.sub(r"(?im)^(\s*Operator)\s*[-–—]\s+", r"\1: ", text)


def find_speaker_positions(text: str, speaker: str) -> list[int]:
    pattern = re.compile(
        rf"(?:^|\n)\s*{re.escape(speaker)}\s*(?::|-)\s*",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return [m.start() for m in pattern.finditer(text)]


def find_operator_positions(text: str) -> list[int]:
    return find_speaker_positions(text, "Operator")


def find_explicit_qa_heading(text: str) -> re.Match[str] | None:
    patterns = [
        r"^\s*Question[- ]and[- ]Answer Session\s*$",
        r"^\s*Questions and Answers\s*$",
        r"^\s*Question and Answer Session\s*$",
        r"^\s*Question and Answer\s*$",
        r"^\s*Q&A Session\s*$",
        r"^\s*Q&A\s*$",
        r"^\s*Analyst Q&A\s*$",
    ]
    pattern = re.compile(
        "|".join(f"(?:{item})" for item in patterns),
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return pattern.search(text)


def find_management_qa_transition(text: str) -> re.Match[str] | None:
    patterns = [
        r"(?:with that|and with that),?\s+(?:we will|we'll|let's|let us)\s+(?:now\s+)?(?:open|begin|start|move to|move into)\s+(?:the call\s+)?(?:for|to)?\s*(?:questions|q&a)",
        r"(?:with that|and with that),?\s+(?:I will|I'll)\s+(?:now\s+)?(?:turn|hand)\s+(?:the call\s+)?(?:back\s+)?(?:over\s+)?(?:to\s+)?(?:the\s+)?operator\s+(?:for|to)\s+(?:questions|q&a)",
        r"(?:we will|we'll|let's|let us)\s+(?:now\s+)?(?:open|begin|start|move to|move into)\s+(?:the call\s+)?(?:for|to)?\s*(?:questions|q&a)",
        r"(?:we are|we're)\s+(?:now\s+)?ready\s+(?:to take|for)\s+(?:your\s+)?questions",
        r"(?:operator),?\s+(?:we are|we're)\s+ready\s+(?:to take|for)\s+(?:your\s+)?questions",
        r"(?:operator),?\s+(?:please open|please begin|please poll)\s+(?:the line|the call|the lines)\s+(?:for|to)\s+(?:questions|q&a)",
        r"(?:operator),?\s+(?:may we have|can we have|please provide)\s+(?:the first question|our first question)",
        r"(?:may we have|can we have)\s+(?:the first question|our first question),?\s+please",
        r"(?:after that|afterwards),?\s+(?:we will|we'll)\s+open\s+(?:the call\s+)?(?:to|for)\s+(?:questions|q&a)",
        r"(?:we will|we'll)\s+then\s+(?:open|begin|start)\s+(?:the call\s+)?(?:for|to)\s+(?:questions|q&a)",
        r"(?:that concludes|this concludes)\s+(?:our\s+)?(?:prepared remarks|formal remarks)",
    ]
    pattern = re.compile("|".join(f"(?:{item})" for item in patterns), flags=re.IGNORECASE)
    return pattern.search(text)


def find_operator_qa_phrase(text: str) -> re.Match[str] | None:
    patterns = [
        r"Operator\s*:\s*(?:Thank you\.?\s*)?(?:Certainly\.?\s*)?(?:Our first question|The first question|First question)",
        r"Operator\s*:\s*(?:Thank you\.?\s*)?(?:Certainly\.?\s*)?(?:We'll take|We will take|We'll go ahead and take|We will go ahead and take)\s+our\s+first\s+question",
        r"Operator\s*:\s*(?:Thank you\.?\s*)?(?:Certainly\.?\s*)?(?:And our first question|Your first question)",
        r"Operator\s*:\s*(?:Thank you\.?\s*)?(?:Certainly\.?\s*)?(?:The question comes from|Our next question comes from)",
        r"Operator\s*:\s*(?:Thank you\.?\s*)?(?:Certainly\.?\s*)?(?:We will now|We'll now|At this time, we will|At this time, we'll|We are now going to)\s+(?:begin|open|start|take)\s+(?:the )?(?:question-and-answer|question and answer|Q&A|questions)",
        r"Operator\s*:\s*(?:Thank you\.?\s*)?(?:Certainly\.?\s*)?(?:If you would like to ask a question|To ask a question)",
        r"Operator\s*:\s*(?:Thank you\.?\s*)?(?:Certainly\.?\s*)?(?:Ladies and gentlemen),?\s+(?:we will now|we'll now)\s+(?:begin|open|start)\s+(?:the )?(?:question-and-answer|Q&A|questions)",
    ]
    pattern = re.compile("|".join(f"(?:{item})" for item in patterns), flags=re.IGNORECASE)
    return pattern.search(text)


def find_first_analyst_question_like_pattern(text: str) -> re.Match[str] | None:
    patterns = [
        r"(?:Our first question|The first question|First question)\s+(?:comes|is)\s+from\s+",
        r"(?:We'll take|We will take|We'll go ahead and take|We will go ahead and take)\s+our\s+first\s+question\s+from\s+",
        r"(?:Your first question)\s+(?:comes|is)\s+from\s+",
        r"(?:Next question|Our next question)\s+(?:comes|is)\s+from\s+",
    ]
    pattern = re.compile("|".join(f"(?:{item})" for item in patterns), flags=re.IGNORECASE)
    return pattern.search(text)


def find_second_operator_fallback(text: str) -> int | None:
    operator_positions = find_operator_positions(text)
    return operator_positions[1] if len(operator_positions) >= 2 else None


def find_late_operator_fallback(text: str) -> int | None:
    operator_positions = find_operator_positions(text)
    transcript_length = len(text)
    for position in operator_positions:
        if position / max(transcript_length, 1) >= 0.25:
            return position
    return None


def build_split_candidates(text: str) -> list[SplitCandidate]:
    candidates: list[SplitCandidate] = []
    candidate_specs = [
        (find_explicit_qa_heading(text), "explicit_q_and_a_heading", 0.99),
        (find_management_qa_transition(text), "management_qa_transition", 0.96),
        (find_operator_qa_phrase(text), "operator_qa_phrase", 0.94),
        (find_first_analyst_question_like_pattern(text), "first_analyst_question_like_pattern", 0.70),
    ]
    for match, method, confidence in candidate_specs:
        if match:
            candidates.append(
                SplitCandidate(
                    position=match.start(),
                    method=method,
                    confidence=confidence,
                    matched_text=match.group(0),
                )
            )

    late_operator_position = find_late_operator_fallback(text)
    if late_operator_position is not None:
        candidates.append(
            SplitCandidate(
                position=late_operator_position,
                method="late_operator_intervention_fallback",
                confidence=0.65,
                matched_text="Late Operator intervention",
            )
        )

    second_operator_position = find_second_operator_fallback(text)
    if second_operator_position is not None:
        candidates.append(
            SplitCandidate(
                position=second_operator_position,
                method="second_operator_intervention_fallback",
                confidence=0.55,
                matched_text="Second Operator intervention",
            )
        )
    return candidates


def rank_candidates(
    candidates: list[SplitCandidate],
    text: str,
) -> tuple[list[SplitCandidate], list[str]]:
    warnings: list[str] = []
    transcript_length = len(text)
    for candidate in candidates:
        ratio = candidate.position / max(transcript_length, 1)
        candidate.position_ratio = ratio
        adjusted = candidate.confidence
        if ratio < MIN_REASONABLE_QA_POSITION_RATIO and candidate.method != "explicit_q_and_a_heading":
            adjusted -= 0.30
            warnings.append(f"qna_candidate_very_early:{candidate.method}:{ratio:.1%}")
        if "fallback" in candidate.method:
            adjusted -= 0.05
        if ratio > 0.92 and "fallback" in candidate.method:
            adjusted -= 0.20
            warnings.append(f"qna_fallback_candidate_very_late:{candidate.method}:{ratio:.1%}")
        if candidate.method == "explicit_q_and_a_heading":
            adjusted += 0.02
        if candidate.method == "management_qa_transition":
            adjusted += 0.01
        candidate.adjusted_confidence = max(0.0, min(adjusted, 1.0))
    return candidates, warnings


def choose_best_candidate(candidates: list[SplitCandidate]) -> SplitCandidate | None:
    if not candidates:
        return None
    management_candidates = [c for c in candidates if c.method == "management_qa_transition"]
    qna_start_candidates = [
        c
        for c in candidates
        if c.method
        in {
            "operator_qa_phrase",
            "late_operator_intervention_fallback",
            "second_operator_intervention_fallback",
            "first_analyst_question_like_pattern",
        }
    ]
    for management_candidate in management_candidates:
        for qna_candidate in qna_start_candidates:
            distance = qna_candidate.position - management_candidate.position
            if 0 < distance <= MAX_MANAGEMENT_TO_OPERATOR_DISTANCE:
                management_candidate.adjusted_confidence = max(
                    management_candidate.adjusted_confidence,
                    0.97,
                )
                return management_candidate
    return sorted(candidates, key=lambda c: (-c.adjusted_confidence, c.position))[0]


def split_earnings_call_into_two_blocks(transcript_text: str) -> dict[str, Any]:
    warnings: list[str] = []
    text = normalize_speaker_labels(normalize_for_search(clean_transcript_text(transcript_text)))
    if not text:
        return {
            "prepared_remarks": "",
            "qna": "",
            "qna_detected": False,
            "qna_start_offset": None,
            "split_method": None,
            "confidence": 0.0,
            "matched_text": None,
            "position_ratio": None,
            "warnings": ["empty_transcript_payload"],
            "candidates": [],
        }

    candidates = build_split_candidates(text)
    candidates, ranking_warnings = rank_candidates(candidates, text)
    warnings.extend(ranking_warnings)
    if not candidates:
        warning = "qna_mentioned_but_no_reliable_qna_start" if QA_MENTION_MARKERS.search(text) else None
        return {
            "prepared_remarks": text,
            "qna": "",
            "qna_detected": False,
            "qna_start_offset": None,
            "split_method": None,
            "confidence": 0.0,
            "matched_text": None,
            "position_ratio": None,
            "warnings": [warning] if warning else [],
            "candidates": [],
        }

    best = choose_best_candidate(candidates)
    if best is None:
        return {
            "prepared_remarks": text,
            "qna": "",
            "qna_detected": False,
            "qna_start_offset": None,
            "split_method": None,
            "confidence": 0.0,
            "matched_text": None,
            "position_ratio": None,
            "warnings": ["unable_to_choose_qna_split_candidate"],
            "candidates": [asdict(candidate) for candidate in candidates],
        }

    prepared_remarks = text[: best.position].strip()
    qna = text[best.position :].strip()
    if qna and len(qna.split()) < 250:
        warnings.append("qna_detected_but_too_short")
    if prepared_remarks and len(prepared_remarks) / max(len(text), 1) < 0.10:
        warnings.append("prepared_remarks_unusually_short")
    if best.adjusted_confidence < 0.70:
        warnings.append("low_confidence_qna_split")
    if "fallback" in best.method:
        warnings.append("qna_split_used_fallback_method")

    return {
        "prepared_remarks": prepared_remarks,
        "qna": qna,
        "qna_detected": bool(qna),
        "qna_start_offset": best.position,
        "split_method": best.method,
        "confidence": best.adjusted_confidence,
        "matched_text": best.matched_text,
        "position_ratio": best.position_ratio,
        "warnings": warnings,
        "candidates": [asdict(candidate) for candidate in candidates],
    }


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
    result = split_earnings_call_into_two_blocks(full_text)
    warnings = list(result.get("warnings") or [])
    section_detection_warning = warnings[0] if warnings else None
    return {
        "prepared_remarks": result["prepared_remarks"],
        "qna": result["qna"],
        "qna_start_offset": result["qna_start_offset"],
        "section_detection_warning": section_detection_warning,
        "qna_split_method": result["split_method"],
        "qna_split_confidence": result["confidence"],
        "qna_split_matched_text": result["matched_text"],
        "qna_split_position_ratio": result["position_ratio"],
        "qna_split_warnings": warnings,
        "qna_split_candidates": result["candidates"],
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
    qna_detected = bool(sections.get("qna_start_offset") is not None and qna.strip())
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
        "word_count": word_count,
        "prepared_remarks_available": bool(prepared.strip()),
        "prepared_remarks_word_count": len(prepared.split()),
        "qna_available": qna_detected,
        "qna_word_count": qna_word_count,
        "qna_complete": qna_detected and qna_word_count >= 250 and operator_close_detected,
        "full_transcript_complete": complete_with_warnings and operator_close_detected,
        "transcript_quality_status": "complete" if complete_with_warnings and operator_close_detected else ("usable_with_warnings" if complete_with_warnings else "incomplete"),
        "operator_intro_detected": bool(OPERATOR_MARKER.search(full_text)),
        "operator_qna_start_detected": bool(OPERATOR_MARKER.search(qna) or QA_START_MARKERS.search(qna)) if qna else False,
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
            "recommended_fetch_tools": [
                "fmp_get_earnings_call_prepared_remarks",
                "fmp_get_earnings_call_q_and_a",
            ],
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
        "note": "Exact rows matched to selected earnings-call periods." if matched else "No exact period match; fallback/latest rows are included only as context and may need manual verification.",
    }


def filter_by_fiscal_year(rows: Any, fiscal_year: int | None) -> list[dict[str, Any]]:
    if fiscal_year is None:
        return []
    output = []
    for row in _safe_list(rows):
        year_i = _safe_int(row.get("calendarYear") or row.get("year") or row.get("fiscalYear"))
        if year_i == fiscal_year:
            output.append(row)
    return output


def annual_financial_table_record(name: str, rows: Any, fiscal_year: int | None) -> dict[str, Any]:
    all_rows = _safe_list(rows)
    matched = filter_by_fiscal_year(all_rows, fiscal_year)
    return {
        "table_name": name,
        "fiscal_year": fiscal_year,
        "matched_rows": matched,
        "match_status": "exact_fiscal_year_match" if matched else "no_exact_fiscal_year_match",
        "fallback_rows": [],
        "fallback_used": False,
        "note": (
            f"Exact annual row matched for FY{fiscal_year}."
            if matched
            else "No exact annual fiscal-year match; annual statement tables may help with manual verification."
        ),
    }


def latest_completed_fiscal_year(periods: list[dict[str, Any]]) -> int | None:
    if not periods:
        return None
    latest = max(periods, key=lambda p: (int(p["year"]), int(p["quarter"])))
    year = int(latest["year"])
    quarter = int(latest["quarter"])
    return year if quarter == 4 else year - 1


def _fallback_period_anchor_date(year: int, quarter: int) -> str:
    quarter_month = {1: 1, 2: 4, 3: 7, 4: 10}.get(int(quarter), 1)
    return f"{int(year):04d}-{quarter_month:02d}-01"


def earnings_release_review_actions(symbol: str, selected_periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    symbol = symbol.upper()
    actions: list[dict[str, Any]] = []
    for period in selected_periods:
        filing_date = period.get("call_date") or _fallback_period_anchor_date(period["year"], period["quarter"])
        actions.append({
            "tool": "get_earnings_release_json",
            "arguments": {
                "symbol": symbol,
                "fiscalYear": period["year"],
                "fiscalQuarter": period["quarter"],
                "filingDate": filing_date,
            },
            "reason": (
                "Suggested source for the official SEC EDGAR earnings release for this selected quarter. "
                "The tool returns LLM-friendly text blocks and parsed tables for review."
            ),
            "retry_suggestion": OPENAI_RETRY_SUGGESTION,
            "suggested_scope": "selected_quarter_official_earnings_release",
            "period_label": period["period_label"],
        })
    return actions

def financial_statement_review_actions(symbol: str, selected_periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    symbol = symbol.upper()
    actions: list[dict[str, Any]] = []
    statements_to_review = ["income_statement", "balance_sheet", "cash_flow_statement"]
    latest_full_year = latest_completed_fiscal_year(selected_periods)

    if latest_full_year is not None:
        actions.append({
            "tool": "fmp_get_statement_tables",
            "arguments": {"symbol": symbol, "period": "annual", "limit": 3},
            "reason": (
                "Suggested statement-table context for the latest completed fiscal year "
                f"FY{latest_full_year}."
            ),
            "suggested_scope": "latest_completed_fiscal_year",
            "fiscal_year_to_review": latest_full_year,
            "statements_to_review": statements_to_review,
        })

    if selected_periods:
        actions.append({
            "tool": "fmp_get_statement_tables",
            "arguments": {"symbol": symbol, "period": "quarter", "limit": min(max(len(selected_periods), 1), 4)},
            "reason": "Suggested statement-table context for the selected quarters.",
            "suggested_scope": "selected_quarters",
            "periods_to_review": [p["period_label"] for p in selected_periods],
            "statements_to_review": statements_to_review,
        })

    return actions



def build_direct_review_policy(selected_periods: list[dict[str, Any]]) -> dict[str, Any]:
    period_labels = [str(p.get("period_label")) for p in selected_periods if p.get("period_label")]
    return {
        "purpose": "Optional source-review suggestions for analysts or LLM agents.",
        "selected_periods": period_labels,
        "suggested_sources": [
            "prepared remarks",
            "earnings-call Q&A",
            "official SEC earnings release",
            "income statement",
            "balance sheet",
            "cash flow statement",
        ],
        "note": (
            "The MCP provides discovery context and suggested next actions only. "
            "The analyst or LLM decides how to use the information."
        ),
    }

def build_source_context_template(selected_periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "period_label": p["period_label"],
            "full_call_text_read": "no",
            "prepared_remarks_reviewed": "no",
            "qna_reviewed": "no",
            "official_release_reviewed": "no",
            "financial_tables_reviewed": "no",
        }
        for p in selected_periods
    ]


def build_financial_statement_context_template(selected_periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    context_rows: list[dict[str, Any]] = []
    latest_full_year = latest_completed_fiscal_year(selected_periods)

    if latest_full_year is not None:
        context_rows.append({
            "period_label": f"FY{latest_full_year}",
            "review_scope": "latest_completed_fiscal_year",
            "income_statement_reviewed": "no",
            "balance_sheet_reviewed": "no",
            "cash_flow_statement_reviewed": "no",
        })

    for p in selected_periods:
        context_rows.append({
            "period_label": p["period_label"],
            "review_scope": "selected_quarter",
            "income_statement_reviewed": "no",
            "balance_sheet_reviewed": "no",
            "cash_flow_statement_reviewed": "no",
        })

    return context_rows


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
    relevant_limited = prioritized[:12]
    earnings_limited = earnings_like[:6]
    return {
        "relevant_filings_for_report": relevant_limited,
        "earnings_release_candidates": earnings_limited,
        "omitted_filings_count": max(0, len(rows) - len(relevant_limited)),
        "omitted_filings_note": (
            "Non-core filings such as Form 4, SC 13G, S-8, proxies, and shelf/prospectus "
            "documents are omitted from the default evidence pack unless specifically relevant."
        ),
        "note": "Prioritize 8-K/6-K earnings-release exhibits, then 10-Q/10-K. Ignore Form 4/144/13G unless specifically relevant.",
    }


def build_transcript_payload(
    *,
    symbol: str,
    year: int,
    quarter: int,
    raw: Any,
    section: TranscriptSection = "prepared_remarks",
    include_full_text: bool = True,
    max_chars: int | None = None,
) -> dict[str, Any]:
    items = _safe_list(raw)
    full_text = combine_transcript_text(items)
    sections = split_transcript_sections(full_text)
    normalized_section = "q_and_a" if section == "qna" else section
    selected_text = {
        "full": full_text,
        "prepared_remarks": sections["prepared_remarks"],
        "q_and_a": sections["qna"],
        "metadata": "",
    }[normalized_section]
    if not include_full_text:
        returned_text = ""
    elif max_chars is None:
        returned_text = selected_text
    else:
        returned_text = selected_text[:max_chars]
    assessment = assess_transcript_completeness(items)
    transcript_field = {
        "full": "transcript",
        "prepared_remarks": "prepared_remarks",
        "q_and_a": "q_and_a",
        "metadata": "transcript",
    }[normalized_section]

    payload: dict[str, Any] = {
        "symbol": symbol.upper(),
        "year": year,
        "quarter": quarter,
        "source_name": "FMP earning-call-transcript",
        "transcript_available": bool(items and full_text),
        transcript_field: returned_text,
        "completeness": assessment,
        "recommended_next_actions": transcript_next_actions(symbol.upper(), year, quarter, normalized_section, assessment),
        "related_transcript_tools": [
            "fmp_get_earnings_call_prepared_remarks",
            "fmp_get_earnings_call_q_and_a",
        ],
    }

    if normalized_section != "full":
        payload["section"] = normalized_section

    return payload


def transcript_next_actions(symbol: str, year: int, quarter: int, section: str, assessment: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    paired_tool = (
        "fmp_get_earnings_call_q_and_a"
        if section == "prepared_remarks"
        else "fmp_get_earnings_call_prepared_remarks"
        if section == "q_and_a"
        else None
    )
    if paired_tool:
        actions.append({
            "tool": paired_tool,
            "arguments": {"symbol": symbol, "year": year, "quarter": quarter},
            "reason": "Suggested paired earnings-call tool for additional transcript context.",
            "retry_suggestion": OPENAI_RETRY_SUGGESTION,
        })
    if assessment.get("transcript_quality_status") == "incomplete":
        actions.append({
            "tool": "fmp_list_transcript_dates",
            "arguments": {"symbol": symbol, "min_year": max(2000, year - 1), "limit": 4},
            "reason": "Confirm the period exists and check adjacent periods for additional context.",
        })
    return actions


async def build_evidence_pack(
    *,
    symbol: str,
    min_year: int = 2025,
    requested_calls: int = 2,
    strict_report_workflow: bool = True,
) -> dict[str, Any]:
    from .fmp_client import FMPClient

    client = FMPClient()
    symbol_upper = symbol.upper()

    transcript_dates = await client.transcript_dates(symbol)
    selected_periods = normalize_transcript_dates(transcript_dates, min_year=min_year, max_items=requested_calls)
    latest_full_year = latest_completed_fiscal_year(selected_periods)

    quarter_income, quarter_balance, quarter_cash, metrics, ratios, growth = await _fetch_financials(
        client, symbol, requested_calls, period="quarter"
    )
    annual_income, annual_balance, annual_cash, _, _, _ = await _fetch_financials(
        client, symbol, 3, period="annual"
    )
    filings = await client.sec_filings(symbol, from_date=f"{min_year}-01-01", to_date=date.today().isoformat())

    transcript_statuses = []
    for period in selected_periods:
        transcript_statuses.append({
            "year": period["year"],
            "quarter": period["quarter"],
            "period_label": period["period_label"],
            "transcript_available": True,
            "prepared_remarks_included": False,
            "q_and_a_included": False,
            "full_call_text_included": False,
            "prepared_remarks_read_by_agent": False,
            "q_and_a_read_by_agent": False,
            "full_call_text_read_by_agent": False,
            "recommended_fetch_tools": [
                "fmp_get_earnings_call_prepared_remarks",
                "fmp_get_earnings_call_q_and_a",
            ],
            "content_policy_note": (
                "Transcript text is intentionally not embedded in the evidence pack; use both "
                "earnings-call recommended actions to fetch prepared remarks and Q&A."
            ),
        })

    financial_tables = [
        financial_table_record("income_statement", quarter_income, selected_periods),
        financial_table_record("balance_sheet", quarter_balance, selected_periods),
        financial_table_record("cash_flow_statement", quarter_cash, selected_periods),
        financial_table_record("key_metrics", metrics, selected_periods),
        financial_table_record("ratios", ratios, selected_periods),
        financial_table_record("financial_growth", growth, selected_periods),
    ]
    annual_financial_tables = [
        annual_financial_table_record("income_statement", annual_income, latest_full_year),
        annual_financial_table_record("balance_sheet", annual_balance, latest_full_year),
        annual_financial_table_record("cash_flow_statement", annual_cash, latest_full_year),
    ]

    context_notes = []
    if len(selected_periods) < requested_calls:
        context_notes.append("fewer_transcript_periods_found_than_requested")

    transcript_actions = []
    for p in selected_periods:
        transcript_actions.extend([
            {
                "tool": "fmp_get_earnings_call_prepared_remarks",
                "arguments": {"symbol": symbol_upper, "year": p["year"], "quarter": p["quarter"]},
                "reason": (
                    "Suggested source for the start of the earnings call / prepared remarks for this selected quarter."
                ),
                "retry_suggestion": OPENAI_RETRY_SUGGESTION,
                "suggested_scope": "selected_quarter_prepared_remarks",
                "period_label": p["period_label"],
            },
            {
                "tool": "fmp_get_earnings_call_q_and_a",
                "arguments": {"symbol": symbol_upper, "year": p["year"], "quarter": p["quarter"]},
                "reason": (
                    "Suggested source for the earnings-call Q&A for this selected quarter."
                ),
                "retry_suggestion": OPENAI_RETRY_SUGGESTION,
                "suggested_scope": "selected_quarter_q_and_a",
                "period_label": p["period_label"],
            },
        ])
    release_actions = earnings_release_review_actions(symbol_upper, selected_periods)
    statement_actions = financial_statement_review_actions(symbol_upper, selected_periods)

    return {
        "evidence_pack_version": EVIDENCE_PACK_VERSION,
        "symbol": symbol_upper,
        "selected_periods": selected_periods,
        "latest_completed_fiscal_year": latest_full_year,
        "evidence_manifest": {
            "transcripts": transcript_statuses,
            "financial_tables": financial_tables,
            "annual_financial_tables": annual_financial_tables,
        },
        "context_notes": context_notes,
        "recommended_next_actions": transcript_actions + release_actions + statement_actions,
    }


async def _fetch_financials(client: Any, symbol: str, limit: int, period: Literal["quarter", "annual"] = "quarter") -> tuple[Any, Any, Any, Any, Any, Any]:
    return (
        await client.income_statement(symbol, period, limit),
        await client.balance_sheet(symbol, period, limit),
        await client.cash_flow(symbol, period, limit),
        await client.key_metrics(symbol, period, limit),
        await client.ratios(symbol, period, limit),
        await client.financial_growth(symbol, period, limit),
    )


def validate_evidence_payload(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    periods = evidence_pack.get("selected_periods") or []
    notes: list[str] = []
    if not periods:
        notes.append("no_selected_periods")

    return {
        "evidence_pack_version": evidence_pack.get("evidence_pack_version"),
        "status": "informational_review_only",
        "notes": sorted(set(notes)),
        "recommended_next_actions": evidence_pack.get("recommended_next_actions", []),
    }
