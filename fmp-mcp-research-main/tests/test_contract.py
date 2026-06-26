from fmp_mcp_research.report_contract import build_report_contract


def test_contract_has_version_and_core_fields():
    contract = build_report_contract("healthcare_technology")
    assert contract["contract_version"] == "0.3.0"
    assert "source_coverage_audit" in contract["required_sections"]
    assert "full_call_text_read" in contract["required_source_audit_fields"]
    assert contract["sector_overlay"] == "healthcare_technology"
