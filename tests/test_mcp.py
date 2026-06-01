import pytest

from mcp_server.server import list_tools


@pytest.mark.asyncio
async def test_list_tools():
    tools = await list_tools()
    tool_names = {t.name for t in tools}
    expected_tool_names = {
        "get_similar_issues",
        "get_official_responses",
        "get_sentiment_trend",
        "get_patch_notes",
        "get_alert_history",
        "get_community_summary",
        "get_top_complaints",
        "get_response_effectiveness",
    }

    assert tool_names == expected_tool_names


@pytest.mark.asyncio
async def test_tool_schemas():
    tools = await list_tools()

    similar_tool = next(t for t in tools if t.name == "get_similar_issues")
    assert "issue_description" in similar_tool.inputSchema["properties"]
    assert "issue_description" in similar_tool.inputSchema["required"]

    responses_tool = next(t for t in tools if t.name == "get_official_responses")
    assert "issue_tag" in responses_tool.inputSchema["properties"]

    trend_tool = next(t for t in tools if t.name == "get_sentiment_trend")
    assert "hours" in trend_tool.inputSchema["properties"]

    complaints_tool = next(t for t in tools if t.name == "get_top_complaints")
    assert "hours" in complaints_tool.inputSchema["properties"]
    assert "top_k" in complaints_tool.inputSchema["properties"]

    effectiveness_tool = next(t for t in tools if t.name == "get_response_effectiveness")
    assert "issue_tag" in effectiveness_tool.inputSchema["properties"]
    assert "issue_tag" in effectiveness_tool.inputSchema["required"]
