from __future__ import annotations

CONTRACT_VERSION = "0.3.4"

SUGGESTED_REPORT_OUTPUT_SECTIONS = [
    "company_snapshot",
    "selected_periods",
    "source_context",
    "earnings_call_summary_by_period",
    "financial_tables_review",
    "official_filings_context",
    "catalysts_and_risks",
    "limitations",
]

SUGGESTED_SOURCE_CONTEXT_FIELDS = [
    "transcript_period_selected",
    "prepared_remarks_available",
    "qna_available",
    "official_release_candidate_found",
    "financial_tables_matched_to_period",
    "income_statement_available",
    "balance_sheet_available",
    "cash_flow_statement_available",
    "latest_completed_fiscal_year_available",
]

PHARMA_LENSES = [
    "clinical_trial_progress",
    "regulatory_timeline_clarity",
    "commercial_launch_quality",
    "cash_runway_and_financing_risk",
]

HEALTHCARE_TECH_LENSES = [
    "implementation_velocity",
    "client_retention_and_expansion",
    "gross_margin_scalability",
    "ai_or_platform_differentiation",
]


def build_report_contract(sector: str = "healthcare_technology") -> dict[str, object]:
    if sector == "pharma":
        overlay_name = "pharma"
        lenses = PHARMA_LENSES
    elif sector == "healthcare_technology":
        overlay_name = "healthcare_technology"
        lenses = HEALTHCARE_TECH_LENSES
    else:
        overlay_name = "none"
        lenses = []

    return {
        "contract_version": CONTRACT_VERSION,
        "mode": "informational_suggestions_only",
        "suggested_sections": SUGGESTED_REPORT_OUTPUT_SECTIONS,
        "suggested_source_context_fields": SUGGESTED_SOURCE_CONTEXT_FIELDS,
        "sector_overlay": overlay_name,
        "sector_lens_suggestions": lenses,
        "workflow_suggestions": {
            "evidence_pack_is_context_provider": True,
            "transcript_tools": [
                "fmp_get_earnings_call_prepared_remarks",
                "fmp_get_earnings_call_q_and_a",
            ],
            "statement_tables_tool": "fmp_get_statement_tables",
            "sec_earnings_release_tool": "get_earnings_release",
        },
        "note": "The MCP returns information and suggestions only; the analyst or LLM decides how to use them.",
    }
