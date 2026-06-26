REPORT_OUTPUT_SECTIONS = [
    "Executive summary",
    "Source audit and quarter-by-quarter coverage audit",
    "Comparability assessment",
    "Financial baseline + Financial Reality",
    "Reporting credibility check",
    "Earnings call roadmap",
    "Financial Alignment",
    "Catalysts / anti-catalysts",
    "Recurring Market Concerns / Next Call Risk Monitor",
    "Scorecard",
    "Final comparative table",
    "Prioritization table sorted by Adjusted Score",
]

REQUIRED_SOURCE_FLAGS = [
    "designated_mcp_transcript_used",
    "designated_mcp_qna_used",
    "mcp_retry_1_attempted",
    "mcp_retry_2_attempted",
    "internet_full_transcript_fallback_used",
    "transcript_source_name",
    "qna_source_name",
    "official_earnings_release_used",
    "official_quarter_financial_tables_used",
    "eight_k_or_six_k_used",
    "ir_or_edgar_fallback_used",
    "confidence_impact_from_missing_sources",
]

CORE_SCORE_DIMENSIONS = [
    "materiality",
    "probability_of_occurrence",
    "verifiability",
    "novelty",
    "controllability",
    "strength_of_signal_in_qna",
]

SECONDARY_SCORE_DIMENSIONS = [
    "management_communication_posture",
    "surprise_vs_market_expectations",
    "narrative_consistency",
    "timing_clarity",
    "magnitude_of_quantitative_support",
    "dependence_on_external_assumptions",
]

PHARMA_LENSES = [
    "treated_patient_demand_quality",
    "access_net_revenue_quality",
    "competitive_lifecycle_durability",
    "pipeline_label_expansion_quality",
    "portfolio_cash_conversion_quality",
]

HEALTHCARE_TECH_LENSES = [
    "monetization_quality",
    "healthcare_workflow_embeddedness",
    "stakeholder_economics_revenue_durability",
    "data_automation_product_defensibility",
    "margin_cac_cash_conversion_quality",
]

EVIDENCE_READINESS_STATES = [
    "source_not_found",
    "source_available_not_returned",
    "source_returned_partial_or_truncated",
    "source_returned_complete_agent_must_read",
    "agent_review_confirmed_outside_mcp",
]

TRANSCRIPT_COMPLETENESS_FIELDS = [
    "call_exists",
    "transcript_available",
    "full_transcript_included_in_payload",
    "included_content_is_excerpt",
    "content_truncated_by_tool",
    "qna_detected_in_source",
    "qna_included_in_payload",
    "qna_complete",
    "operator_qna_start_detected",
    "operator_close_detected",
    "must_call_dedicated_transcript_tool",
    "score_allowed_from_current_payload",
]
