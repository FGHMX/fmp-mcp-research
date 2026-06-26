from fmp_mcp_research.evidence import (
    assess_transcript_completeness,
    build_transcript_payload,
    normalize_transcript_dates,
)
from fmp_mcp_research.report_contract import (
    CORE_SCORE_DIMENSIONS,
    HEALTHCARE_TECH_LENSES,
    PHARMA_LENSES,
    REQUIRED_SOURCE_FLAGS,
    TRANSCRIPT_COMPLETENESS_FIELDS,
)


def test_contract_contains_required_fields():
    assert "materiality" in CORE_SCORE_DIMENSIONS
    assert "designated_mcp_transcript_used" in REQUIRED_SOURCE_FLAGS
    assert "portfolio_cash_conversion_quality" in PHARMA_LENSES
    assert "monetization_quality" in HEALTHCARE_TECH_LENSES
    assert "content_truncated_by_tool" in TRANSCRIPT_COMPLETENESS_FIELDS


def test_normalize_transcript_dates_selects_recent_min_year():
    raw = [
        {"year": 2024, "quarter": 4},
        {"year": 2025, "quarter": 1},
        {"year": 2026, "quarter": 1, "date": "2026-05-01"},
        {"fiscalYear": "2025", "fiscalQuarter": "Q4"},
    ]
    periods = normalize_transcript_dates(raw, min_year=2025, max_items=2)
    assert [(p["year"], p["quarter"]) for p in periods] == [(2026, 1), (2025, 4)]
    assert periods[0]["recommended_fetch_tool"] == "fmp_get_earnings_call_transcript"


def test_assess_transcript_flags_missing_qna_and_close():
    payload = [{"content": "Prepared remarks only. Revenue improved. Let me now take you through the numbers."}]
    assessment = assess_transcript_completeness(payload)
    assert assessment["has_text"] is True
    assert assessment["qna_available"] is False
    assert assessment["full_transcript_complete"] is False
    assert "qna_start_not_detected" in assessment["truncation_reasons"]
    assert "operator_close_not_detected" in assessment["truncation_reasons"]


def test_build_transcript_payload_marks_tool_truncation():
    long_qna = "Question-and-answer session. Operator Instructions. " + ("Analyst question. CEO answer. " * 300) + "This concludes today's conference."
    raw = [{"content": "Prepared remarks. " + ("Business update. " * 300) + long_qna}]
    payload = build_transcript_payload("SGRY", 2025, 4, raw, include_full_text=True, max_chars=200)
    assert payload["content_truncated_by_tool"] is True
    assert payload["full_transcript_included_in_payload"] is False
    assert payload["must_call_dedicated_transcript_tool"] is True
    assert payload["next_best_action"]["tool"] == "fmp_get_earnings_call_transcript"
