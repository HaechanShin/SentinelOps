"""LangGraph multi-agent pipeline for SentinelOps.

Graph flow:
  ingest_node → sentiment_node → alert_node → (drafting_node if alerts) → notify_node
"""

from __future__ import annotations

from typing import Any, TypedDict

import structlog
from langgraph.graph import END, StateGraph

from agents.alert_agent import detect_alerts
from agents.drafting_agent import evaluate_draft, generate_drafts
from agents.sentiment_agent import process_unanalyzed_posts

logger = structlog.get_logger()


class PipelineState(TypedDict, total=False):
    sentiment_results: list[dict]
    alerts: list[dict]
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


async def drafting_node(state: PipelineState) -> PipelineState:
    logger.info("pipeline_drafting_start", alert_count=len(state.get("alerts", [])))
    all_drafts = []
    all_evaluations = []

    for alert in state.get("alerts", []):
        drafts = await generate_drafts(alert)
        all_drafts.extend(drafts)

        for draft in drafts:
            try:
                issue_ctx = _alert_to_context_str(alert)
                scores = await evaluate_draft(draft["content"], issue_ctx)
                all_evaluations.append(
                    {"draft_id": draft["id"], "scores": scores}
                )
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
    for alert in state.get("alerts", []):
        alert_drafts = [
            d for d in state.get("drafts", []) if d.get("alert_id") == alert.get("id")
        ]
        notifications.append(
            {
                "alert_id": alert.get("id"),
                "alert_type": alert.get("alert_type"),
                "severity": alert.get("severity"),
                "draft_count": len(alert_drafts),
                "status": "pending_notification",
            }
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
    graph.add_node("drafting", drafting_node)
    graph.add_node("notify", notify_node)

    graph.set_entry_point("sentiment")
    graph.add_edge("sentiment", "alert")
    graph.add_conditional_edges("alert", should_draft, {"draft": "drafting", "end": END})
    graph.add_edge("drafting", "notify")
    graph.add_edge("notify", END)

    return graph


pipeline = build_graph().compile()


async def run_pipeline() -> dict[str, Any]:
    logger.info("pipeline_run_start")
    result = await pipeline.ainvoke({})
    logger.info(
        "pipeline_run_complete",
        sentiments=len(result.get("sentiment_results", [])),
        alerts=len(result.get("alerts", [])),
        drafts=len(result.get("drafts", [])),
    )
    return result
