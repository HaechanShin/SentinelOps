"""LangGraph multi-agent pipeline for SentinelOps.

Graph flow:
  sentiment_node → alert_node → (context_node if alerts) → drafting_node → notify_node

context_node uses Claude tool_use to decide which MCP tools to call.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

import anthropic
import structlog
from langgraph.graph import END, StateGraph
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.postgresql import insert

from agents.alert_agent import detect_alerts
from agents.drafting_agent import evaluate_draft, generate_drafts, store_eval_scores
from agents.sentiment_agent import process_unanalyzed_posts
from config import settings
from db.engine import AsyncSessionLocal
from db.models import Alert, PipelineRun
from mcp_server.server import get_official_responses, get_patch_notes, get_similar_issues

logger = structlog.get_logger()

CONTEXT_TOOLS = [
    {
        "name": "get_similar_issues",
        "description": "Search for similar past community issues by keyword matching. Use when you need to find how the community reacted to similar problems before.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_description": {
                    "type": "string",
                    "description": "Keywords or description of the issue to search for",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 5)",
                    "default": 5,
                },
            },
            "required": ["issue_description"],
        },
    },
    {
        "name": "get_patch_notes",
        "description": "Get recent PUBG patch notes. Use when the alert might relate to a recent update, bug fix, or new feature.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recent": {
                    "type": "integer",
                    "description": "Number of recent patch notes (default 3)",
                    "default": 3,
                },
            },
        },
    },
    {
        "name": "get_official_responses",
        "description": "Get past approved official responses for a specific issue type. Use to maintain consistent messaging tone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_tag": {
                    "type": "string",
                    "description": "Issue tag: anti-cheat, server-stability, optimization, game-balance, new-content, matchmaking, bugs, monetization, general",
                },
            },
            "required": ["issue_tag"],
        },
    },
]

TOOL_EXECUTORS = {
    "get_similar_issues": lambda args: get_similar_issues(
        args["issue_description"], args.get("top_k", 5)
    ),
    "get_patch_notes": lambda args: get_patch_notes(args.get("recent", 3)),
    "get_official_responses": lambda args: get_official_responses(args["issue_tag"]),
}

CONTEXT_KEY_MAP = {
    "get_similar_issues": "similar_issues",
    "get_patch_notes": "patch_notes",
    "get_official_responses": "official_responses",
}


class PipelineState(TypedDict, total=False):
    sentiment_results: list[dict]
    alerts: list[dict]
    context: dict
    drafts: list[dict]
    evaluations: list[dict]
    notifications: list[dict]
    error: str | None


async def sentiment_node(state: PipelineState) -> PipelineState:
    logger.info("pipeline_sentiment_start")
    results = await process_unanalyzed_posts(batch_size=20)
    return {"sentiment_results": results}


async def alert_node(state: PipelineState) -> PipelineState:
    logger.info("pipeline_alert_start")
    alerts = await detect_alerts()
    return {"alerts": alerts}


def should_draft(state: PipelineState) -> str:
    if state.get("alerts"):
        return "draft"
    return "end"


async def _gather_context_for_alert(alert: dict) -> dict:
    """Use Claude tool_use to decide which MCP tools to call for this alert."""
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key, max_retries=3
    )

    trigger = alert.get("trigger_data", {})
    representative = trigger.get("representative_posts", [])

    alert_desc = f"Alert type: {alert.get('alert_type')}, Severity: {alert.get('severity')}\n"
    if alert.get("alert_type") == "sentiment_drop":
        alert_desc += (
            f"Sentiment dropped by {trigger.get('drop', 'N/A')} "
            f"(from {trigger.get('earlier_avg', 'N/A')} to {trigger.get('recent_avg', 'N/A')})\n"
        )
    elif alert.get("alert_type") == "keyword_spike":
        alert_desc += (
            f"Keyword '{trigger.get('keyword')}' spiked "
            f"{trigger.get('multiplier')}x ({trigger.get('recent_count')} mentions)\n"
        )

    if representative:
        alert_desc += "Representative community posts:\n"
        for p in representative[:3]:
            alert_desc += f"- {p.get('content', '')[:200]}\n"

    messages = [
        {
            "role": "user",
            "content": (
                "You are gathering context for a PUBG community alert response. "
                "Given the alert below, call the tools you need to gather relevant context. "
                "Call at least one tool.\n\n"
                f"{alert_desc}"
            ),
        }
    ]

    context = {}

    for _ in range(3):
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            tools=CONTEXT_TOOLS,
            messages=messages,
        )

        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_blocks:
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in tool_blocks:
            executor = TOOL_EXECUTORS.get(block.name)
            if executor:
                try:
                    result = await executor(block.input)
                    ctx_key = CONTEXT_KEY_MAP[block.name]
                    if ctx_key == "official_responses":
                        context.setdefault(ctx_key, []).extend(result)
                    else:
                        context[ctx_key] = result
                except Exception:
                    result = {"error": f"Tool execution failed for {block.name}"}
                    logger.exception("tool_execution_failed", tool=block.name)
            else:
                result = {"error": f"Unknown tool: {block.name}"}

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str)[:5000],
                }
            )

        messages.append({"role": "user", "content": tool_results})

        if response.stop_reason == "end_turn":
            break

    return context


async def context_node(state: PipelineState) -> PipelineState:
    logger.info("pipeline_context_start")
    all_contexts = {}

    for alert in state.get("alerts", []):
        try:
            context = await _gather_context_for_alert(alert)
            all_contexts[alert.get("id")] = context
            logger.info(
                "context_collected",
                alert_id=alert.get("id"),
                similar=len(context.get("similar_issues", [])),
                patches=len(context.get("patch_notes", [])),
                responses=len(context.get("official_responses", [])),
                tools_used=list(context.keys()),
            )
        except Exception:
            logger.exception("context_collection_failed", alert_id=alert.get("id"))
            all_contexts[alert.get("id")] = {}

    return {"context": all_contexts}


async def drafting_node(state: PipelineState) -> PipelineState:
    logger.info("pipeline_drafting_start", alert_count=len(state.get("alerts", [])))
    all_drafts = []
    all_evaluations = []
    all_contexts = state.get("context", {})

    for alert in state.get("alerts", []):
        alert_context = all_contexts.get(alert.get("id"))
        drafts = await generate_drafts(alert, context=alert_context)
        all_drafts.extend(drafts)

        for draft in drafts:
            try:
                issue_ctx = _alert_to_context_str(alert)
                scores = await evaluate_draft(draft["content"], issue_ctx)
                all_evaluations.append(
                    {"draft_id": draft["id"], "scores": scores}
                )
                await store_eval_scores(draft["id"], scores)
            except Exception:
                logger.exception("evaluation_failed", draft_id=draft["id"])

    return {"drafts": all_drafts, "evaluations": all_evaluations}


async def notify_node(state: PipelineState) -> PipelineState:
    logger.info(
        "pipeline_notify_start",
        alerts=len(state.get("alerts", [])),
        drafts=len(state.get("drafts", [])),
    )

    notifications = []

    if not settings.slack_bot_token or settings.slack_bot_token.startswith("xoxb-xxxx"):
        logger.warning("slack_not_configured", msg="Skipping Slack notifications")
        for alert in state.get("alerts", []):
            notifications.append(
                {"alert_id": alert.get("id"), "status": "skipped_no_slack"}
            )
        return {"notifications": notifications}

    from slack_app.handlers.alert_handler import send_alert
    from slack_sdk.web.async_client import AsyncWebClient

    client = AsyncWebClient(token=settings.slack_bot_token)

    for alert in state.get("alerts", []):
        alert_drafts = [
            d for d in state.get("drafts", []) if d.get("alert_id") == alert.get("id")
        ]

        try:
            ts = await send_alert(client, alert, drafts=alert_drafts or None)

            if ts:
                async with AsyncSessionLocal() as session:
                    stmt = (
                        sql_update(Alert)
                        .where(Alert.id == alert.get("id"))
                        .values(slack_ts=ts)
                    )
                    await session.execute(stmt)
                    await session.commit()

            notifications.append(
                {
                    "alert_id": alert.get("id"),
                    "alert_type": alert.get("alert_type"),
                    "severity": alert.get("severity"),
                    "draft_count": len(alert_drafts),
                    "slack_ts": ts,
                    "status": "sent" if ts else "send_failed",
                }
            )
            logger.info("slack_alert_sent", alert_id=alert.get("id"), ts=ts)
        except Exception:
            logger.exception("slack_notification_failed", alert_id=alert.get("id"))
            notifications.append(
                {"alert_id": alert.get("id"), "status": "error"}
            )

    return {"notifications": notifications}


def _alert_to_context_str(alert: dict) -> str:
    trigger = alert.get("trigger_data", {})
    parts = [f"Type: {alert.get('alert_type')}, Severity: {alert.get('severity')}"]
    for post in trigger.get("representative_posts", [])[:2]:
        parts.append(f"Post: {post.get('content', '')[:100]}")
    return " | ".join(parts)


def build_graph() -> StateGraph:
    graph = StateGraph(PipelineState)

    graph.add_node("sentiment", sentiment_node)
    graph.add_node("alert", alert_node)
    graph.add_node("context", context_node)
    graph.add_node("drafting", drafting_node)
    graph.add_node("notify", notify_node)

    graph.set_entry_point("sentiment")
    graph.add_edge("sentiment", "alert")
    graph.add_conditional_edges("alert", should_draft, {"draft": "context", "end": END})
    graph.add_edge("context", "drafting")
    graph.add_edge("drafting", "notify")
    graph.add_edge("notify", END)

    return graph


pipeline = build_graph().compile()


async def run_pipeline() -> dict[str, Any]:
    run_id = uuid.uuid4()
    logger.info("pipeline_run_start", run_id=str(run_id))

    async with AsyncSessionLocal() as session:
        stmt = insert(PipelineRun).values(id=run_id, status="running")
        await session.execute(stmt)
        await session.commit()

    try:
        result = await pipeline.ainvoke({})

        async with AsyncSessionLocal() as session:
            stmt = (
                sql_update(PipelineRun)
                .where(PipelineRun.id == run_id)
                .values(
                    status="completed",
                    completed_at=datetime.now(timezone.utc),
                    posts_analyzed=len(result.get("sentiment_results", [])),
                    alerts_triggered=len(result.get("alerts", [])),
                    drafts_generated=len(result.get("drafts", [])),
                )
            )
            await session.execute(stmt)
            await session.commit()

        logger.info(
            "pipeline_run_complete",
            run_id=str(run_id),
            sentiments=len(result.get("sentiment_results", [])),
            alerts=len(result.get("alerts", [])),
            drafts=len(result.get("drafts", [])),
            evaluations=len(result.get("evaluations", [])),
        )
        return result

    except Exception as e:
        async with AsyncSessionLocal() as session:
            stmt = (
                sql_update(PipelineRun)
                .where(PipelineRun.id == run_id)
                .values(
                    status="failed",
                    completed_at=datetime.now(timezone.utc),
                    error_message=str(e)[:500],
                )
            )
            await session.execute(stmt)
            await session.commit()

        logger.exception("pipeline_run_failed", run_id=str(run_id))
        raise
