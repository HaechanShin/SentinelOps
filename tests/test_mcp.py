import pytest

from mcp_server.server import app, list_tools


@pytest.mark.asyncio
async def test_list_tools():
    tools = await list_tools()
    tool_names = [t.name for t in tools]

    assert "get_similar_issues" in tool_names
    assert "get_official_responses" in tool_names
    assert "get_sentiment_trend" in tool_names
    assert "get_patch_notes" in tool_names
    assert "get_alert_history" in tool_names
    assert "get_community_summary" in tool_names
    assert len(tools) == 6


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
