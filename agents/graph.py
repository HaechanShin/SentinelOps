"""LangGraph multi-agent pipeline for SentinelOps.

Graph flow:
  sentiment_node → alert_node → (context_node if alerts) → drafting_node → notify_node

context_node uses Claude tool_use by default and a JSON tool planner for local providers.
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
from agents.llm_client import complete_text, is_anthropic_provider, loads_json_object
from agents.sentiment_agent import process_unanalyzed_posts
from config import settings
from constants import DEFAULT_ISSUE_TAG, ISSUE_TAGS, LEGACY_TAG_ALIASES
from db.engine import AsyncSessionLocal
from db.models import Alert, PipelineRun
from mcp_server.server import (
    get_alert_history,
    get_official_responses,
    get_patch_notes,
    get_response_effectiveness,
    get_sentiment_trend,
    get_similar_issues,
    get_top_complaints,
)

logger = structlog.get_logger()

CONTEXT_TOOLS = [
    {
        "name": "get_similar_issues",
        "description": (
            "Search for similar past community issues by keyword matching. "
            "Use when you need to find how the community reacted to similar problems before."
        ),
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
        "description": (
            "Get recent patch notes for the monitored game. Use when the alert might relate "
            "to a recent update, bug fix, or new feature."
        ),
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
        "description": (
            "Get past approved official responses for a specific issue type. "
            "Use to maintain consistent messaging tone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_tag": {
                    "type": "string",
                    "description": (
                        "Issue tag: anti-cheat, server-stability, optimization, "
                        "game-balance, new-content, matchmaking, bugs, monetization, general"
                    ),
                },
            },
            "required": ["issue_tag"],
        },
    },
    {
        "name": "get_sentiment_trend",
        "description": (
            "Get hourly sentiment averages over a recent window. Use to understand whether "
            "this alert is part of an ongoing trend or a sudden spike."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Hours of trend data to retrieve (default 24)",
                    "default": 24,
                },
            },
        },
    },
    {
        "name": "get_alert_history",
        "description": (
            "Get recent alerts to check whether this issue is recurring. Useful to decide "
            "if a response should escalate or reference past similar incidents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Hours of alert history (default 168 = 7 days)",
                    "default": 168,
                },
                "alert_type": {
                    "type": "string",
                    "description": "Optional filter: sentiment_drop or keyword_spike",
                },
            },
        },
    },
    {
        "name": "get_top_complaints",
        "description": (
            "Get the top complaint topics with example posts in a recent window. "
            "Use to understand what players are most frustrated about right now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "default": 24},
                "top_k": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "get_response_effectiveness",
        "description": (
            "Check how community sentiment shifted after the last approved response for this "
            "issue tag. Use to learn whether prior messaging worked before drafting a new one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_tag": {"type": "string"},
                "window_days": {"type": "integer", "default": 3},
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
    "get_sentiment_trend": lambda args: get_sentiment_trend(args.get("hours", 24)),
    "get_alert_history": lambda args: get_alert_history(
        args.get("hours", 168), args.get("alert_type"), None
    ),
    "get_top_complaints": lambda args: get_top_complaints(
        args.get("hours", 24), args.get("top_k", 5), args.get("max_examples_per_tag", 2)
    ),
    "get_response_effectiveness": lambda args: get_response_effectiveness(
        args["issue_tag"], args.get("window_days", 3)
    ),
}

CONTEXT_KEY_MAP = {
    "get_similar_issues": "similar_issues",
    "get_patch_notes": "patch_notes",
    "get_official_responses": "official_responses",
    "get_sentiment_trend": "sentiment_trend",
    "get_alert_history": "alert_history",
    "get_top_complaints": "top_complaints",
    "get_response_effectiveness": "response_effectiveness",
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


def _describe_alert(alert: dict) -> str:
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

    return alert_desc


async def _gather_context_with_claude_tool_use(alert: dict) -> dict:
    """Use Claude tool_use to decide which MCP tools to call for this alert."""
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        max_retries=settings.anthropic_max_retries,
    )

    messages = [
        {
            "role": "user",
            "content": (
                "You are gathering context for a game community alert response. "
                "Given the alert below, call the tools you need to gather relevant context. "
                "Call at least one tool.\n\n"
                f"{_describe_alert(alert)}"
            ),
        }
    ]

    context = {}

    for _ in range(5):
        response = await client.messages.create(
            model=settings.anthropic_model,
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


def _representative_issue_tag(alert: dict) -> str:
    representative = alert.get("trigger_data", {}).get("representative_posts", [])
    for post in representative:
        for tag in post.get("issue_tags") or []:
            normalized = LEGACY_TAG_ALIASES.get(str(tag).strip().lower(), str(tag).strip().lower())
            if normalized in ISSUE_TAGS:
                return normalized
    return DEFAULT_ISSUE_TAG


def _alert_search_description(alert: dict) -> str:
    trigger = alert.get("trigger_data", {})
    parts = [str(alert.get("alert_type") or "community issue")]
    if trigger.get("keyword"):
        parts.append(str(trigger["keyword"]))
    for post in trigger.get("representative_posts", [])[:3]:
        if post.get("content"):
            parts.append(str(post["content"])[:160])
    return " ".join(parts)


def _fallback_context_tool_plan(alert: dict) -> list[dict[str, Any]]:
    issue_tag = _representative_issue_tag(alert)
    return [
        {
            "name": "get_similar_issues",
            "arguments": {
                "issue_description": _alert_search_description(alert),
                "top_k": 5,
            },
        },
        {
            "name": "get_official_responses",
            "arguments": {"issue_tag": issue_tag},
        },
        {"name": "get_patch_notes", "arguments": {"recent": 3}},
        {"name": "get_top_complaints", "arguments": {"hours": 24, "top_k": 5}},
        {
            "name": "get_response_effectiveness",
            "arguments": {"issue_tag": issue_tag, "window_days": 3},
        },
    ]


def _coerce_positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(maximum, parsed))


def _normalize_tool_args(name: str, args: dict[str, Any], alert: dict) -> dict[str, Any]:
    if name == "get_similar_issues":
        description = str(args.get("issue_description") or "").strip()
        if not description:
            description = _alert_search_description(alert)
        return {
            "issue_description": description[:500],
            "top_k": _coerce_positive_int(args.get("top_k"), 5, 10),
        }

    if name == "get_patch_notes":
        return {"recent": _coerce_positive_int(args.get("recent"), 3, 5)}

    if name == "get_official_responses":
        raw_tag = str(args.get("issue_tag") or _representative_issue_tag(alert)).strip().lower()
        issue_tag = LEGACY_TAG_ALIASES.get(raw_tag, raw_tag)
        if issue_tag not in ISSUE_TAGS:
            issue_tag = DEFAULT_ISSUE_TAG
        return {"issue_tag": issue_tag}

    if name == "get_sentiment_trend":
        return {"hours": _coerce_positive_int(args.get("hours"), 24, 168)}

    if name == "get_alert_history":
        result: dict[str, Any] = {
            "hours": _coerce_positive_int(args.get("hours"), 168, 720),
        }
        alert_type = str(args.get("alert_type") or "").strip().lower()
        if alert_type in {"sentiment_drop", "keyword_spike"}:
            result["alert_type"] = alert_type
        return result

    if name == "get_top_complaints":
        return {
            "hours": _coerce_positive_int(args.get("hours"), 24, 168),
            "top_k": _coerce_positive_int(args.get("top_k"), 5, 10),
        }

    if name == "get_response_effectiveness":
        raw_tag = str(args.get("issue_tag") or _representative_issue_tag(alert)).strip().lower()
        issue_tag = LEGACY_TAG_ALIASES.get(raw_tag, raw_tag)
        if issue_tag not in ISSUE_TAGS:
            issue_tag = DEFAULT_ISSUE_TAG
        return {
            "issue_tag": issue_tag,
            "window_days": _coerce_positive_int(args.get("window_days"), 3, 14),
        }

    return args


def _normalize_tool_plan(plan: dict[str, Any], alert: dict) -> list[dict[str, Any]]:
    raw_calls = plan.get("tool_calls") or plan.get("tools") or []
    if isinstance(raw_calls, dict):
        raw_calls = [raw_calls]

    normalized = []
    seen = set()
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or call.get("tool") or "").strip()
        if name not in TOOL_EXECUTORS:
            continue

        args = call.get("arguments") or call.get("input") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}

        clean_args = _normalize_tool_args(name, args, alert)
        dedupe_key = (name, json.dumps(clean_args, sort_keys=True, default=str))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append({"name": name, "arguments": clean_args})

    return normalized[:5] or _fallback_context_tool_plan(alert)


async def _plan_context_tools(alert: dict) -> list[dict[str, Any]]:
    prompt = f"""You are gathering context for a game community alert response.
Choose the MCP tools that will retrieve useful context for the alert below.

Available tools:
{json.dumps(CONTEXT_TOOLS, indent=2)}

Return ONLY valid JSON in this exact shape:
{{
  "tool_calls": [
    {{
      "name": "get_similar_issues",
      "arguments": {{"issue_description": "...", "top_k": 5}}
    }}
  ]
}}

Rules:
- Call 2 to 5 tools.
- Prefer get_similar_issues for player complaints or incidents.
- Use get_official_responses when an issue tag is clear.
- Use get_patch_notes when the alert may relate to a recent update, bug fix, or balance change.
- Use get_sentiment_trend to check whether this alert is part of an ongoing slide.
- Use get_alert_history to see whether this issue has been flagged recently before.
- Use get_top_complaints when the alert is a broad sentiment drop without a clear cause.
- Use get_response_effectiveness when an issue tag is clear and you want to know if past messaging worked.

Alert:
{_describe_alert(alert)}"""

    try:
        text = await complete_text(
            system="You select data-gathering tools and return only JSON.",
            user=prompt,
            max_tokens=768,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return _normalize_tool_plan(loads_json_object(text), alert)
    except Exception:
        logger.exception("context_tool_planning_failed", alert_id=alert.get("id"))
        return _fallback_context_tool_plan(alert)


async def _gather_context_with_json_tool_plan(alert: dict) -> dict:
    context = {}
    for call in await _plan_context_tools(alert):
        name = call["name"]
        executor = TOOL_EXECUTORS[name]
        try:
            result = await executor(call["arguments"])
            ctx_key = CONTEXT_KEY_MAP[name]
            if ctx_key == "official_responses":
                context.setdefault(ctx_key, []).extend(result)
            else:
                context[ctx_key] = result
        except Exception:
            logger.exception("tool_execution_failed", tool=name)

    return context


async def _gather_context_for_alert(alert: dict) -> dict:
    if is_anthropic_provider():
        return await _gather_context_with_claude_tool_use(alert)
    return await _gather_context_with_json_tool_plan(alert)


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
                all_evaluations.append({"draft_id": draft["id"], "scores": scores})
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
            notifications.append({"alert_id": alert.get("id"), "status": "skipped_no_slack"})
        return {"notifications": notifications}

    from slack_sdk.web.async_client import AsyncWebClient

    from slack_app.handlers.alert_handler import send_alert

    client = AsyncWebClient(token=settings.slack_bot_token)
    all_contexts = state.get("context", {})

    for alert in state.get("alerts", []):
        alert_drafts = [d for d in state.get("drafts", []) if d.get("alert_id") == alert.get("id")]
        alert_context = all_contexts.get(alert.get("id"))

        try:
            ts = await send_alert(
                client, alert, drafts=alert_drafts or None, context=alert_context
            )

            if ts:
                async with AsyncSessionLocal() as session:
                    stmt = sql_update(Alert).where(Alert.id == alert.get("id")).values(slack_ts=ts)
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
            notifications.append({"alert_id": alert.get("id"), "status": "error"})

    return {"notifications": notifications}


def _alert_to_context_str(alert: dict) -> str:
    trigger = alert.get("trigger_data", {})
    parts = [f"Type: {alert.get('alert_type')}, Severity: {alert.get('severity')}"]
    for post in trigger.get("representative_posts", [])[:2]:
        parts.append(f"Post: {post.get('content', '')[:100]}")
    return " | ".join(parts)


def build_sentiment_graph() -> StateGraph:
    """Hourly pipeline: sentiment analysis only. Alerts are evaluated daily."""
    graph = StateGraph(PipelineState)
    graph.add_node("sentiment", sentiment_node)
    graph.set_entry_point("sentiment")
    graph.add_edge("sentiment", END)
    return graph


def build_alert_graph() -> StateGraph:
    """Daily pipeline: alert detection → context → drafting → notify."""
    graph = StateGraph(PipelineState)

    graph.add_node("alert", alert_node)
    graph.add_node("context", context_node)
    graph.add_node("drafting", drafting_node)
    graph.add_node("notify", notify_node)

    graph.set_entry_point("alert")
    graph.add_conditional_edges("alert", should_draft, {"draft": "context", "end": END})
    graph.add_edge("context", "drafting")
    graph.add_edge("drafting", "notify")
    graph.add_edge("notify", END)

    return graph


sentiment_pipeline = build_sentiment_graph().compile()
alert_pipeline = build_alert_graph().compile()


async def _run_pipeline(pipeline_obj, run_label: str) -> dict[str, Any]:
    run_id = uuid.uuid4()
    logger.info("pipeline_run_start", run_id=str(run_id), label=run_label)

    async with AsyncSessionLocal() as session:
        stmt = insert(PipelineRun).values(id=run_id, status="running")
        await session.execute(stmt)
        await session.commit()

    try:
        result = await pipeline_obj.ainvoke({})

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
            label=run_label,
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

        logger.exception("pipeline_run_failed", run_id=str(run_id), label=run_label)
        raise


async def run_sentiment_pipeline() -> dict[str, Any]:
    """Hourly: collect + analyze sentiment (no alerting)."""
    return await _run_pipeline(sentiment_pipeline, "sentiment")


async def run_alert_pipeline() -> dict[str, Any]:
    """Daily: detect alerts on the recent 24h vs prior 24h, draft + notify."""
    return await _run_pipeline(alert_pipeline, "alert")


# Backward-compat shim — older callers (manual triggers, tests) may import run_pipeline.
async def run_pipeline() -> dict[str, Any]:
    sentiment_result = await run_sentiment_pipeline()
    alert_result = await run_alert_pipeline()
    return {**sentiment_result, **alert_result}
