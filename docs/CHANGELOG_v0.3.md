# Version 0.3.x change list

## Version 0.3.1

### OpenAI-friendly tool inputs

1. Remove public `include_transcript_text` and `max_transcript_chars` inputs from `fmp_build_research_evidence_pack`.
2. Keep evidence packs as manifests only; transcript text is fetched through `recommended_next_actions`.
3. Remove public `section` and `max_chars` inputs from `fmp_get_earnings_call_transcript`.
4. Make transcript recommended actions pass only `symbol`, `year`, and `quarter`.
5. Return the complete transcript supplied by FMP from the canonical transcript tool.
6. Add bounded tool schemas and server-side clamps for count-style inputs.
7. Update package, contract, and evidence-pack versions to `0.3.1`.

## Version 0.3.0

### Contract and workflow

1. Align the evidence workflow with README and next actions.
2. Add `contract_version` to the report contract.
3. Add `evidence_pack_version` to evidence packs.
4. Treat evidence packs as manifests, not proof that the model read every source.
5. Keep Q&A split uncertainty as a warning rather than an automatic failure.

### Transcript quality

6. Fix truncation regex by removing the empty alternative.
7. Add `transcript_quality_status`: `complete`, `usable_with_warnings`, `incomplete`.
8. Preserve detailed `quality_warnings` for auditability.
9. Add transcript completeness metadata and Q&A detection metadata.

### Code quality

10. Reformat long single-line modules into maintainable Python files.
11. Add dev dependencies for pytest, pytest-asyncio, respx, ruff and mypy.
12. Add ruff and mypy configuration.
13. Add more focused unit tests around transcript completeness, periods, filings and validation.
14. Add CI skeleton.

### Production readiness

15. Run Docker as non-root user.
16. Keep `.env.example` minimal and explicit.
17. Document auth/rate-limiting as production guardrails without making the local dev setup too strict.

### Required financial statement review

18. Evidence packs recommend `fmp_get_statement_tables` for annual and quarterly statement review.
19. The required review scope covers Income Statement, Balance Sheet and Cash Flow Statement for the latest completed fiscal year and each selected quarter.
20. Added `financial_statement_audit_template` to keep statement-level review separate from the general source audit.
21. SEC filing prioritization returns a concise default list and omits non-core filings from the evidence pack unless specifically relevant.
