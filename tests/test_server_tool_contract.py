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


def test_get_earnings_call_transcript_complete_transcript_inputs():
    args = _async_function_args("fmp_get_earnings_call_transcript")
    assert args == ["symbol", "year", "quarter"]
    assert "section" not in args
    assert "max_chars" not in args


def test_all_mcp_tools_have_safe_read_only_annotations():
    from fmp_mcp_research.server import mcp

    tools = mcp._tool_manager.list_tools()
    assert tools
    for tool in tools:
        assert tool.title, f"{tool.name} is missing a human-readable title"
        assert tool.description, f"{tool.name} is missing a description"
        assert tool.annotations is not None, f"{tool.name} is missing MCP safety annotations"
        annotations = tool.annotations.model_dump(by_alias=True, exclude_none=True)
        assert annotations["readOnlyHint"] is True, tool.name
        assert annotations["destructiveHint"] is False, tool.name
        assert annotations["idempotentHint"] is True, tool.name
        assert annotations["openWorldHint"] is False, tool.name
