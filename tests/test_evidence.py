from fmp_mcp_research.evidence import (
    assess_transcript_completeness,
    build_transcript_payload,
    filter_by_period,
    has_real_explicit_truncation_marker,
    normalize_transcript_dates,
    prioritize_sec_filings,
    split_transcript_sections,
    validate_evidence_payload,
)


def test_no_false_truncation_marker_from_regex():
    text = "Prepared remarks. Operator: our first question comes from Jane. This concludes today's conference."
    assert has_real_explicit_truncation_marker(text) is False
    assessment = assess_transcript_completeness([{"content": text}])
    assert assessment["explicit_truncation_marker_detected"] is False


def test_detects_real_truncation_marker():
    assert has_real_explicit_truncation_marker("Management said revenue improved. [truncated]") is True


def test_false_qna_intro_is_not_split_as_real_qna():
    text = "Prepared remarks. A question-and-answer session will follow after the presentation. Revenue grew."
    sections = split_transcript_sections(text)
    assert sections["qna"] == ""
    assert sections["section_detection_warning"] in {
        "qna_mentioned_but_no_reliable_qna_start",
        "qna_start_likely_false_positive",
    }


def test_build_transcript_payload_supports_qna_section():
    qna = "Operator: our first question comes from Jane. " + "Question answer management. " * 300 + "This concludes today's conference."
    raw = [{"content": "Prepared remarks. " + qna}]
    payload = build_transcript_payload(
        symbol="TEST", year=2026, quarter=1, raw=raw, section="qna", max_chars=10_000
    )
    assert payload["section"] == "qna"
    assert payload["qna"].startswith("Operator")
    assert payload["content_truncated_by_tool"] is False


def test_build_transcript_payload_recommends_section_fetches_when_full_truncated():
    raw = [{"content": "Prepared remarks. " + "word " * 2000}]
    payload = build_transcript_payload(
        symbol="TEST", year=2026, quarter=1, raw=raw, section="full", max_chars=100
    )
    assert payload["content_truncated_by_tool"] is True
    args = [action["arguments"] for action in payload["recommended_next_actions"]]
    assert {"symbol": "TEST", "year": 2026, "quarter": 1, "section": "prepared_remarks"} in args
    assert {"symbol": "TEST", "year": 2026, "quarter": 1, "section": "qna"} in args


def test_normalize_transcript_dates_filters_and_sorts():
    raw = [
        {"year": 2024, "quarter": 4},
        {"year": 2026, "quarter": 1},
        {"fiscalYear": "2025", "fiscalQuarter": "Q4"},
    ]
    result = normalize_transcript_dates(raw, min_year=2025, max_items=2)
    assert [x["period_label"] for x in result] == ["Q1 2026", "Q4 2025"]


def test_filter_by_period_matches_quarters():
    periods = [{"year": 2026, "quarter": 1}]
    rows = [{"calendarYear": "2026", "period": "Q1"}, {"calendarYear": "2025", "period": "Q4"}]
    assert filter_by_period(rows, periods) == [rows[0]]


def test_prioritize_sec_filings_identifies_earnings_release_candidates():
    filings = [
        {"formType": "8-K", "title": "Earnings Results Exhibit 99.1"},
        {"formType": "Form 4", "title": "Insider transaction"},
        {"formType": "10-Q", "title": "Quarterly report"},
    ]
    result = prioritize_sec_filings(filings)
    assert len(result["relevant_filings_for_report"]) == 2
    assert len(result["earnings_release_candidates"]) == 1


def test_validate_evidence_payload_blocks_unread_sources():
    payload = {
        "evidence_pack_version": "0.3.0",
        "selected_periods": [{"period_label": "Q1 2026"}],
        "source_audit_template": [
            {
                "period_label": "Q1 2026",
                "full_call_text_read": "no",
                "qna_reviewed": "no",
                "official_release_reviewed": "no",
            }
        ],
        "scoring_readiness": {"blocking_items": []},
        "recommended_next_actions": [],
    }
    result = validate_evidence_payload(payload)
    assert result["allowed"] is False
    assert "full_call_text_not_read:Q1 2026" in result["blocking_items"]
