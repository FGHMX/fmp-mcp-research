# Version 0.3.0 change list

## Contract and workflow

1. Align `fmp_get_earnings_call_transcript` with README and next actions.
2. Add `section` support: `full`, `prepared_remarks`, `qna`, `metadata`.
3. Add `max_chars` support with a server-side cap.
4. Add `contract_version` to the report contract.
5. Add `evidence_pack_version` to evidence packs.
6. Treat evidence packs as manifests, not proof that the model read every source.
7. Keep Q&A split uncertainty as a warning rather than an automatic failure.

## Transcript quality

8. Fix truncation regex by removing the empty alternative.
9. Add `transcript_quality_status`: `complete`, `usable_with_warnings`, `incomplete`.
10. Preserve detailed `quality_warnings` for auditability.
11. Support separate prepared remarks and Q&A fetches when a full transcript exceeds payload budget.
12. Add transcript chunks metadata when content is too large.

## Code quality

13. Reformat long single-line modules into maintainable Python files.
14. Add dev dependencies for pytest, pytest-asyncio, respx, ruff and mypy.
15. Add ruff and mypy configuration.
16. Add more focused unit tests around transcript completeness, periods, filings and validation.
17. Add CI skeleton.

## Production readiness

18. Run Docker as non-root user.
19. Keep `.env.example` minimal and explicit.
20. Document auth/rate-limiting as production guardrails without making the local dev setup too strict.

## Required financial statement review

- Evidence packs now recommend `fmp_get_statement_tables` for annual and quarterly statement review.
- The required review scope covers Income Statement, Balance Sheet and Cash Flow Statement for the latest completed fiscal year and each selected quarter.
- Added `financial_statement_audit_template` to keep statement-level review separate from the general source audit.
- SEC filing prioritization now returns a concise default list and omits non-core filings from the evidence pack unless specifically relevant.
