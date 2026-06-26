from fmp_mcp_research.report_contract import CORE_SCORE_DIMENSIONS, PHARMA_LENSES, REQUIRED_SOURCE_FLAGS


def test_contract_contains_required_fields():
    assert "materiality" in CORE_SCORE_DIMENSIONS
    assert "designated_mcp_transcript_used" in REQUIRED_SOURCE_FLAGS
    assert "portfolio_cash_conversion_quality" in PHARMA_LENSES
