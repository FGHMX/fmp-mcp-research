from __future__ import annotations

CONTRACT_VERSION = "0.3.1"

REPORT_OUTPUT_SECTIONS = [
    "company_snapshot",
    "selected_periods",
    "source_coverage_audit",
    "earnings_call_summary_by_period",
    "financial_tables_review",
    "official_filings_review",
    "catalyst_scorecard",
    "blocking_items_and_limitations",
]

REQUIRED_SOURCE_FLAGS = [
    "transcript_period_selected",
    "full_call_text_returned",
    "full_call_text_read",
    "prepared_remarks_reviewed",
    "qna_available",
    "qna_reviewed",
    "official_release_candidate_found",
    "official_release_reviewed",
    "financial_tables_matched_to_period",
    "financial_tables_reviewed",
    "income_statement_reviewed",
    "balance_sheet_reviewed",
    "cash_flow_statement_reviewed",
    "latest_completed_fiscal_year_reviewed",
    "scorecard_allowed",
]

CORE_SCORE_DIMENSIONS = [
    "revenue_growth_quality",
    "margin_and_cash_flow_progression",
    "guidance_revision_or_reiteration",
    "pipeline_or_product_momentum",
    "management_tone_and_execution",
    "balance_sheet_and_liquidity",
]

SECONDARY_SCORE_DIMENSIONS = [
    "analyst_qna_concerns",
    "competitive_positioning",
    "regulatory_or_reimbursement_risk",
    "customer_adoption_or_retention",
    "near_term_catalyst_density",
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
        "strictness": "professional_not_overly_strict",
        "required_sections": REPORT_OUTPUT_SECTIONS,
        "required_source_audit_fields": REQUIRED_SOURCE_FLAGS,
        "core_score_dimensions": CORE_SCORE_DIMENSIONS,
        "secondary_score_dimensions": SECONDARY_SCORE_DIMENSIONS,
        "sector_overlay": overlay_name,
        "sector_lens_scores_diagnostic_only": lenses,
        "workflow_contract": {
            "evidence_pack_is_orchestrator_not_final_review": True,
            "canonical_transcript_fetch_tool": "fmp_get_earnings_call_transcript",
            "canonical_statement_tables_tool": "fmp_get_statement_tables",
            "must_fetch_transcript_for_each_selected_period": True,
            "must_read_prepared_remarks_and_qna_before_scoring": True,
            "must_review_official_release_and_financial_tables_separately": True,
            "must_review_income_statement_balance_sheet_and_cash_flow": True,
            "must_review_latest_completed_fiscal_year_and_selected_quarters": True,
            "qna_split_uncertain_is_warning_not_automatic_blocker": True,
        },
        "scoring_guardrail": (
            "Do not produce a scorecard until the coverage audit confirms the required "
            "sources have been returned and reviewed. If evidence is partial, disclose the limitation."
        ),
    }
