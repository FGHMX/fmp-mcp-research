from fmp_mcp_research.report_contract import build_report_contract


def test_contract_returns_suggestions_only():
    contract = build_report_contract("healthcare_technology")
    assert contract["mode"] == "informational_suggestions_only"
    assert "suggested_sections" in contract
    assert "suggested_source_context_fields" in contract
    assert "note" in contract
