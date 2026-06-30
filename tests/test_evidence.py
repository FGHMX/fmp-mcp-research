from fmp_mcp_research.evidence import (
    earnings_release_review_actions,
    financial_statement_review_actions,
    validate_evidence_payload,
)


def test_validate_evidence_payload_is_informational():
    result = validate_evidence_payload({"evidence_pack_version": "0.3.4", "selected_periods": []})
    assert result["status"] == "informational_review_only"
    assert result["notes"] == ["no_selected_periods"]
    assert set(result) == {"evidence_pack_version", "status", "notes", "recommended_next_actions"}


def test_release_actions_are_suggestions_only():
    actions = earnings_release_review_actions("ONDS", [{"year": 2026, "quarter": 1, "period_label": "Q1 2026"}])
    assert actions[0]["tool"] == "get_earnings_release_json"
    assert set(actions[0]) == {"tool", "arguments", "reason", "suggested_scope", "period_label"}


def test_statement_actions_are_suggestions_only():
    actions = financial_statement_review_actions("ONDS", [{"year": 2026, "quarter": 1, "period_label": "Q1 2026"}])
    assert actions
    assert all("suggested_scope" in action for action in actions)
