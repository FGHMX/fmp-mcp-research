from pathlib import Path
import re

root = Path.cwd()

def replace_in_file(path, replacements):
    p = root / path
    text = p.read_text()
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        p.write_text(text)
        print(f"updated {path}")
    else:
        print(f"no direct changes in {path}")

# 1. Remove tenacity from pyproject
replace_in_file("pyproject.toml", [
    ('  "tenacity>=8.2.3",\n', ""),
    ('  "tenacity>=8.2.3"\n', ""),
])

# 2. Remove tenacity retry wrapper from FMP client
replace_in_file("src/fmp_mcp_research/fmp_client.py", [
    ("from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential\n", ""),
    ("""    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError, FMPError)),
        reraise=True,
    )
""", ""),
])

# 3. Remove tenacity retry wrapper from SEC client
replace_in_file("src/fmp_mcp_research/sec_client.py", [
    ("from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential\n", ""),
    ("""    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError, SECError)),
        reraise=True,
    )
""", ""),
])

# 4. Remove retry constant and retry_suggestion fields from evidence.py
evidence_path = root / "src/fmp_mcp_research/evidence.py"
text = evidence_path.read_text()

text = re.sub(
    r'\nOPENAI_RETRY_SUGGESTION = \(\n'
    r'    "If the host rejects or drops this tool call, it may be useful to retry the same call "\n'
    r'    "up to 3 total attempts before treating the source as unavailable\."\n'
    r'\)\n',
    "\n",
    text,
)

text = re.sub(
    r'\n\s*"retry_suggestion": OPENAI_RETRY_SUGGESTION,',
    "",
    text,
)

evidence_path.write_text(text)
print("updated src/fmp_mcp_research/evidence.py")

# 5. Update server.py annotations, descriptions, and notes
server_path = root / "src/fmp_mcp_research/server.py"
text = server_path.read_text()

text = text.replace("    OPENAI_RETRY_SUGGESTION,\n", "")

text = text.replace(
"""READ_ONLY_SAFE = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
""",
"""READ_ONLY_EXTERNAL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

READ_ONLY_LOCAL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
""",
)

external_tools = [
    "fmp_get_company_profile",
    "fmp_list_transcript_dates",
    "fmp_get_earnings_call_prepared_remarks",
    "fmp_get_earnings_call_q_and_a",
    "fmp_get_statement_tables",
    "fmp_search_sec_filings",
    "get_earnings_release_json",
    "fmp_get_earnings_calendar",
    "fmp_build_research_evidence_pack",
    "fmp_build_research_pack",
]

local_tools = [
    "fmp_validate_research_evidence",
    "research_report_contract",
]

# Replace all remaining READ_ONLY_SAFE with external first.
text = text.replace("annotations=READ_ONLY_SAFE", "annotations=READ_ONLY_EXTERNAL")

# Then specifically set local tools to READ_ONLY_LOCAL.
for tool_name in local_tools:
    pattern = (
        r'(@mcp\.tool\(\n'
        r'(?:.|\n)*?'
        r'annotations=)READ_ONLY_EXTERNAL'
        r'(,\n\)\nasync def ' + re.escape(tool_name) + r'\()'
    )
    text = re.sub(pattern, r'\1READ_ONLY_LOCAL\2', text)

# Remove retry_suggestion entries in server.py
text = re.sub(
    r'\n\s*"retry_suggestion": OPENAI_RETRY_SUGGESTION,',
    "",
    text,
)

# Remove payload retry_suggestion assignment
text = text.replace('    payload["retry_suggestion"] = OPENAI_RETRY_SUGGESTION\n', "")

# Replace notes that append OPENAI_RETRY_SUGGESTION
text = text.replace(
'''    payload["note"] = (
        "This tool returns only the earnings-call start/prepared remarks. The paired Q&A tool can provide additional context for the same period. "
        f"{OPENAI_RETRY_SUGGESTION}"
    )
''',
'''    payload["note"] = (
        "This tool returns only the earnings-call start/prepared remarks. "
        "The paired Q&A tool can provide additional context for the same period."
    )
''',
)

text = text.replace(
'''    payload["note"] = (
        "This tool returns only the earnings-call Q&A. The paired prepared-remarks tool can provide additional context for the same period. "
        f"{OPENAI_RETRY_SUGGESTION}"
    )
''',
'''    payload["note"] = (
        "This tool returns only the earnings-call Q&A. "
        "The paired prepared-remarks tool can provide additional context for the same period."
    )
''',
)

# Simplify sensitive descriptions if old versions exist
text = text.replace(
'''    description=(
        "Use this for read-only FMP earnings-call workflows when the user needs the start "
        "of a selected earnings call / prepared remarks without Q&A. The paired Q&A tool can add context "
        "for the same symbol, year, and quarter. If the host rejects or drops the call, a retry may be useful."
    ),
''',
'''    description="Reads prepared remarks from one FMP earnings call.",
''',
)

text = text.replace(
'''    description=(
        "Use this for read-only FMP earnings-call workflows when the user needs only the Q&A "
        "portion of a selected earnings call. The paired prepared-remarks tool can add context "
        "for the same symbol, year, and quarter. If the host rejects or drops the call, a retry may be useful."
    ),
''',
'''    description="Reads the Q&A section from one FMP earnings call.",
''',
)

text = text.replace(
'''    description=(
        "Use this when the user wants official SEC EDGAR earnings-release context for one "
        "selected fiscal quarter. Fetches the likely 8-K/6-K earnings-release exhibit from "
        "SEC EDGAR and converts it into LLM-friendly JSON with text blocks and parsed tables. "
        "Raw HTML is never returned and tables are always included. If the host rejects or drops "
        "the call, a retry may be useful. "
        "Read-only; does not submit, publish, trade, or mutate data."
    ),
''',
'''    description="Reads one public SEC earnings release and returns text and tables as JSON.",
''',
)

server_path.write_text(text)
print("updated src/fmp_mcp_research/server.py")

# 6. Update tests if present
test_evidence = root / "tests/test_evidence.py"
if test_evidence.exists():
    text = test_evidence.read_text()
    text = text.replace("    OPENAI_RETRY_SUGGESTION,\n", "")
    text = text.replace("from fmp_mcp_research.evidence import (\n    OPENAI_RETRY_SUGGESTION,\n", "from fmp_mcp_research.evidence import (\n")
    text = text.replace('    assert actions[0]["retry_suggestion"] == OPENAI_RETRY_SUGGESTION\n', "")
    text = text.replace(
        '    assert set(actions[0]) == {"tool", "arguments", "reason", "retry_suggestion", "suggested_scope", "period_label"}\n',
        '    assert set(actions[0]) == {"tool", "arguments", "reason", "suggested_scope", "period_label"}\n',
    )
    test_evidence.write_text(text)
    print("updated tests/test_evidence.py")

test_contract = root / "tests/test_server_tool_contract.py"
if test_contract.exists():
    text = test_contract.read_text()
    text = text.replace(
'''        assert annotations["openWorldHint"] is False, tool.name
''',
'''        if tool.name in {"fmp_validate_research_evidence", "research_report_contract"}:
            assert annotations["openWorldHint"] is False, tool.name
        else:
            assert annotations["openWorldHint"] is True, tool.name
''',
    )
    test_contract.write_text(text)
    print("updated tests/test_server_tool_contract.py")

print("done")
