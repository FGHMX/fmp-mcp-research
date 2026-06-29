from fmp_mcp_research.evidence import (
    assess_transcript_completeness,
    build_transcript_payload,
    earnings_release_review_actions,
    filter_by_period,
    financial_statement_review_actions,
    has_real_explicit_truncation_marker,
    latest_completed_fiscal_year,
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


def test_build_transcript_payload_recommends_complete_refetch_when_full_truncated():
    raw = [{"content": "Prepared remarks. " + "word " * 2000}]
    payload = build_transcript_payload(
        symbol="TEST", year=2026, quarter=1, raw=raw, section="full", max_chars=100
    )
    assert payload["content_truncated_by_tool"] is True
    args = [action["arguments"] for action in payload["recommended_next_actions"]]
    assert {"symbol": "TEST", "year": 2026, "quarter": 1} in args
    assert all("section" not in item and "max_chars" not in item for item in args)


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
        "evidence_pack_version": "0.3.3",
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


def test_latest_completed_fiscal_year_infers_prior_year_for_q1():
    periods = [{"year": 2026, "quarter": 1, "period_label": "Q1 2026"}]
    assert latest_completed_fiscal_year(periods) == 2025



def test_earnings_release_review_actions_require_sec_release_for_each_period():
    periods = [
        {"year": 2026, "quarter": 1, "period_label": "Q1 2026", "call_date": "2026-01-31"},
        {"year": 2025, "quarter": 4, "period_label": "Q4 2025"},
    ]
    actions = earnings_release_review_actions("aapl", periods)
    assert [action["tool"] for action in actions] == [
        "get_earnings_release_json",
        "get_earnings_release_json",
    ]
    assert actions[0]["arguments"] == {
        "symbol": "AAPL",
        "fiscalYear": 2026,
        "fiscalQuarter": 1,
        "filingDate": "2026-01-31",
        "includeHtml": False,
        "includeTables": True,
    }
    assert actions[1]["arguments"]["filingDate"] == "2025-10-01"
    assert all(action["required_for_scoring"] is True for action in actions)

def test_financial_statement_review_actions_use_existing_statement_tables_tool():
    periods = [
        {"year": 2026, "quarter": 1, "period_label": "Q1 2026"},
        {"year": 2025, "quarter": 4, "period_label": "Q4 2025"},
    ]
    actions = financial_statement_review_actions("RLMD", periods)
    assert [action["tool"] for action in actions] == [
        "fmp_get_statement_tables",
        "fmp_get_statement_tables",
    ]
    assert actions[0]["arguments"]["period"] == "annual"
    assert actions[0]["fiscal_year_to_review"] == 2025
    assert actions[1]["arguments"]["period"] == "quarter"
    assert actions[1]["periods_to_review"] == ["Q1 2026", "Q4 2025"]
    assert actions[1]["statements_to_review"] == [
        "income_statement",
        "balance_sheet",
        "cash_flow_statement",
    ]


def test_validate_evidence_payload_blocks_unreviewed_financial_statements():
    payload = {
        "evidence_pack_version": "0.3.3",
        "selected_periods": [{"period_label": "Q1 2026"}],
        "source_audit_template": [
            {
                "period_label": "Q1 2026",
                "full_call_text_read": "yes",
                "qna_reviewed": "yes",
                "official_release_reviewed": "yes",
                "financial_tables_reviewed": "yes",
            }
        ],
        "financial_statement_audit_template": [
            {
                "period_label": "FY2025",
                "income_statement_reviewed": "no",
                "balance_sheet_reviewed": "yes",
                "cash_flow_statement_reviewed": "yes",
            }
        ],
        "scoring_readiness": {"blocking_items": []},
        "recommended_next_actions": [],
    }
    result = validate_evidence_payload(payload)
    assert result["allowed"] is False
    assert "income_statement_not_reviewed:FY2025" in result["blocking_items"]
