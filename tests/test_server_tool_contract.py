import ast
from pathlib import Path

SERVER_PATH = Path(__file__).resolve().parents[1] / "src" / "fmp_mcp_research" / "server.py"


def _async_function_args(name: str) -> list[str]:
    tree = ast.parse(SERVER_PATH.read_text())
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return [arg.arg for arg in node.args.args]
    raise AssertionError(f"Function not found: {name}")


def test_build_research_evidence_pack_openai_friendly_inputs():
    args = _async_function_args("fmp_build_research_evidence_pack")
    assert args == ["symbol", "min_year", "requested_calls", "strict_report_workflow"]
    assert "include_transcript_text" not in args
    assert "max_transcript_chars" not in args


def test_build_research_pack_alias_openai_friendly_inputs():
    args = _async_function_args("fmp_build_research_pack")
    assert args == ["symbol", "min_year", "requested_calls", "strict_report_workflow"]


def test_earnings_call_transcript_replaced_by_two_section_tools():
    prepared_args = _async_function_args("fmp_get_earnings_call_prepared_remarks")
    qna_args = _async_function_args("fmp_get_earnings_call_q_and_a")
    assert prepared_args == ["symbol", "year", "quarter"]
    assert qna_args == ["symbol", "year", "quarter"]

    tree = ast.parse(SERVER_PATH.read_text())
    function_names = {node.name for node in tree.body if isinstance(node, ast.AsyncFunctionDef)}
    assert "fmp_get_earnings_call_transcript" not in function_names


def test_all_mcp_tools_have_safe_read_only_annotations():
    from fmp_mcp_research.server import mcp

    tools = mcp._tool_manager.list_tools()
    assert tools
    tool_names = {tool.name for tool in tools}
    assert "fmp_get_earnings_call_transcript" not in tool_names
    assert "fmp_get_earnings_calendar" not in tool_names
    assert "get_earnings_release_json" not in tool_names
    assert "get_earnings_release" in tool_names
    assert "fmp_get_earnings_call_prepared_remarks" in tool_names
    assert "fmp_get_earnings_call_q_and_a" in tool_names
    for tool in tools:
        assert tool.title, f"{tool.name} is missing a human-readable title"
        assert tool.description, f"{tool.name} is missing a description"
        assert tool.annotations is not None, f"{tool.name} is missing MCP safety annotations"
        annotations = tool.annotations.model_dump(by_alias=True, exclude_none=True)
        assert annotations["readOnlyHint"] is True, tool.name
        assert annotations["destructiveHint"] is False, tool.name
        assert annotations["idempotentHint"] is True, tool.name
        if tool.name in {"fmp_validate_research_evidence", "research_report_contract"}:
            assert annotations["openWorldHint"] is False, tool.name
        else:
            assert annotations["openWorldHint"] is True, tool.name


def test_get_earnings_release_uses_fixed_camel_case_inputs_without_html_or_table_flags():
    args = _async_function_args("get_earnings_release")
    assert args == ["symbol", "fiscalYear", "fiscalQuarter"]
    assert "includeHtml" not in args
    assert "includeTables" not in args


def test_statement_table_limit_is_capped_at_four():
    args = _async_function_args("fmp_get_statement_tables")
    assert args == ["symbol", "period", "limit"]
    text = SERVER_PATH.read_text()
    assert "le=4" in text
    assert "maximum=4" in text
    assert "le=12" not in text
    assert "maximum=12" not in text


def test_date_pattern_accepts_real_iso_date():
    import re

    from fmp_mcp_research.server import DATE_PATTERN

    assert re.fullmatch(DATE_PATTERN, "2026-04-29")
    assert re.fullmatch(DATE_PATTERN, "2025-01-01")
    assert not re.fullmatch(DATE_PATTERN, r"\d{4}-\d{2}-\d{2}")
