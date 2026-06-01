"""MCP Server — Community Intelligence Layer for SentinelOps.

Provides tools for querying community data, sentiment trends,
official responses, and patch notes via the Model Context Protocol.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import structlog
import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from sqlalchemy import func, or_, select, text
from starlette.applications import Starlette
from starlette.routing import Mount

from constants import LEGACY_TAG_ALIASES
from db.engine import AsyncSessionLocal
from db.models import Alert, Draft, OfficialResponse, PatchNote, Post

logger = structlog.get_logger()

mcp_app = Server("sentinelops-community")


@mcp_app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_similar_issues",
            description="Search for similar community issues using keyword matching. Returns the most relevant past posts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_description": {
                        "type": "string",
                        "description": "Description of the issue to search for",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return",
                        "default": 5,
                    },
                },
                "required": ["issue_description"],
            },
        ),
        Tool(
            name="get_official_responses",
            description="Get official response templates for a given issue tag (e.g., 'server-stability', 'bugs', 'anti-cheat', 'optimization', 'game-balance', 'new-content', 'matchmaking', 'monetization', 'general').",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_tag": {
                        "type": "string",
                        "description": "Issue tag to search responses for",
                    },
                },
                "required": ["issue_tag"],
            },
        ),
        Tool(
            name="get_sentiment_trend",
            description="Get sentiment trend data over a specified time period. Returns hourly averages.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "Number of hours to look back",
                        "default": 24,
                    },
                },
            },
        ),
        Tool(
            name="get_patch_notes",
            description="Get the most recent patch notes for the monitored game.",
            inputSchema={
                "type": "object",
                "properties": {
                    "recent": {
                        "type": "integer",
                        "description": "Number of recent patch notes to return",
                        "default": 3,
                    },
                },
            },
        ),
        Tool(
            name="get_alert_history",
            description="Get recent alert history with optional filtering by type and status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "Hours to look back",
                        "default": 24,
                    },
                    "alert_type": {
                        "type": "string",
                        "description": "Filter by alert type: 'sentiment_drop' or 'keyword_spike'",
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter by status: 'open', 'acknowledged', 'resolved'",
                    },
                },
            },
        ),
        Tool(
            name="get_community_summary",
            description="Get a summary of community activity including post counts, average sentiment, and top issues.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "Hours to summarize",
                        "default": 24,
                    },
                },
            },
        ),
        Tool(
            name="get_top_complaints",
            description=(
                "Get the top complaint topics in a recent window. Returns negative-sentiment "
                "posts grouped by issue tag with example excerpts. Use to scope what players "
                "are most frustrated about right now."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "Hours to look back",
                        "default": 24,
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of complaint topics to return",
                        "default": 5,
                    },
                    "max_examples_per_tag": {
                        "type": "integer",
                        "description": "Max example posts per topic",
                        "default": 2,
                    },
                },
            },
        ),
        Tool(
            name="get_response_effectiveness",
            description=(
                "Measure community sentiment shift after an issue tag received an approved "
                "official response. Compares the sentiment for that tag in the N days before "
                "and after the most recent approval."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_tag": {
                        "type": "string",
                        "description": "Issue tag to evaluate",
                    },
                    "window_days": {
                        "type": "integer",
                        "description": "Days before/after the approval to compare",
                        "default": 3,
                    },
                },
                "required": ["issue_tag"],
            },
        ),
    ]


@mcp_app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    match name:
        case "get_similar_issues":
            result = await get_similar_issues(
                arguments["issue_description"], arguments.get("top_k", 5)
            )
        case "get_official_responses":
            result = await get_official_responses(arguments["issue_tag"])
        case "get_sentiment_trend":
            result = await get_sentiment_trend(arguments.get("hours", 24))
        case "get_patch_notes":
            result = await get_patch_notes(arguments.get("recent", 3))
        case "get_alert_history":
            result = await get_alert_history(
                arguments.get("hours", 24),
                arguments.get("alert_type"),
                arguments.get("status"),
            )
        case "get_community_summary":
            result = await get_community_summary(arguments.get("hours", 24))
        case "get_top_complaints":
            result = await get_top_complaints(
                arguments.get("hours", 24),
                arguments.get("top_k", 5),
                arguments.get("max_examples_per_tag", 2),
            )
        case "get_response_effectiveness":
            result = await get_response_effectiveness(
                arguments["issue_tag"],
                arguments.get("window_days", 3),
            )
        case _:
            result = {"error": f"Unknown tool: {name}"}

    return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]


async def get_similar_issues(description: str, top_k: int) -> list[dict]:
    keywords = description.lower().split()
    async with AsyncSessionLocal() as session:
        conditions = [func.lower(Post.content).contains(kw) for kw in keywords[:10]]

        stmt = (
            select(Post)
            .where(Post.analyzed_at.is_not(None))
        )
        if conditions:
            stmt = stmt.where(or_(*conditions))
        stmt = stmt.order_by(Post.created_at.desc()).limit(top_k * 10)

        result = await session.execute(stmt)
        posts = result.scalars().all()

    scored = []
    for post in posts:
        content_lower = post.content.lower()
        score = sum(1 for kw in keywords if kw in content_lower)
        if score > 0:
            scored.append((score, post))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        {
            "id": str(post.id),
            "source": post.source,
            "content": post.content[:300],
            "sentiment": post.sentiment,
            "issue_tags": post.issue_tags,
            "created_at": post.created_at.isoformat() if post.created_at else None,
            "relevance_score": score,
        }
        for score, post in scored[:top_k]
    ]


async def get_official_responses(issue_tag: str) -> list[dict]:
    normalized_tag = LEGACY_TAG_ALIASES.get(issue_tag.strip().lower(), issue_tag.strip().lower())
    legacy_aliases = [k for k, v in LEGACY_TAG_ALIASES.items() if v == normalized_tag]
    search_tags = list({normalized_tag} | set(legacy_aliases))

    async with AsyncSessionLocal() as session:
        stmt = (
            select(OfficialResponse)
            .where(OfficialResponse.issue_tag.in_(search_tags))
            .order_by(OfficialResponse.created_at.desc())
        )
        result = await session.execute(stmt)
        responses = result.scalars().all()

    return [
        {
            "id": str(r.id),
            "issue_tag": r.issue_tag,
            "content": r.content,
            "source": r.source,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in responses
    ]


async def get_sentiment_trend(hours: int) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with AsyncSessionLocal() as session:
        stmt = text("""
            SELECT
                date_trunc('hour', created_at) AS hour,
                AVG(sentiment) AS avg_sentiment,
                COUNT(*) AS post_count,
                array_agg(DISTINCT unnest_tag) AS tags
            FROM posts,
                 LATERAL unnest(issue_tags) AS unnest_tag
            WHERE analyzed_at IS NOT NULL AND created_at >= :since
            GROUP BY date_trunc('hour', created_at)
            ORDER BY hour
        """)
        result = await session.execute(stmt, {"since": since})
        rows = result.fetchall()

    return [
        {
            "hour": row[0].isoformat() if row[0] else None,
            "avg_sentiment": round(float(row[1]), 3) if row[1] else None,
            "post_count": row[2],
            "top_tags": row[3][:5] if row[3] else [],
        }
        for row in rows
    ]


async def get_patch_notes(recent: int) -> list[dict]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(PatchNote)
            .order_by(PatchNote.published_at.desc())
            .limit(recent)
        )
        result = await session.execute(stmt)
        notes = result.scalars().all()

    return [
        {
            "id": str(n.id),
            "version": n.version,
            "title": n.title,
            "content": n.content,
            "published_at": n.published_at.isoformat() if n.published_at else None,
        }
        for n in notes
    ]


async def get_alert_history(
    hours: int, alert_type: str | None, status: str | None
) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with AsyncSessionLocal() as session:
        stmt = select(Alert).where(Alert.created_at >= since)
        if alert_type:
            stmt = stmt.where(Alert.alert_type == alert_type)
        if status:
            stmt = stmt.where(Alert.status == status)
        stmt = stmt.order_by(Alert.created_at.desc())

        result = await session.execute(stmt)
        alerts = result.scalars().all()

    return [
        {
            "id": str(a.id),
            "alert_type": a.alert_type,
            "severity": a.severity,
            "trigger_data": a.trigger_data,
            "status": a.status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in alerts
    ]


async def get_community_summary(hours: int) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with AsyncSessionLocal() as session:
        count_stmt = (
            select(func.count(Post.id), func.avg(Post.sentiment))
            .where(Post.created_at >= since)
            .where(Post.analyzed_at.is_not(None))
        )
        count_result = await session.execute(count_stmt)
        total, avg_sent = count_result.one()

        source_stmt = (
            select(Post.source, func.count(Post.id))
            .where(Post.created_at >= since)
            .group_by(Post.source)
        )
        source_result = await session.execute(source_stmt)
        by_source = {row[0]: row[1] for row in source_result}

        alert_stmt = (
            select(func.count(Alert.id))
            .where(Alert.created_at >= since)
        )
        alert_result = await session.execute(alert_stmt)
        alert_count = alert_result.scalar()

    return {
        "period_hours": hours,
        "total_posts": total or 0,
        "average_sentiment": round(float(avg_sent), 3) if avg_sent else None,
        "posts_by_source": by_source,
        "alerts_triggered": alert_count or 0,
    }


async def get_top_complaints(
    hours: int, top_k: int, max_examples_per_tag: int
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Post)
            .where(Post.analyzed_at.is_not(None))
            .where(Post.created_at >= since)
            .where(Post.sentiment < 0)
            .where(Post.issue_tags.is_not(None))
            .order_by(Post.sentiment.asc())
        )
        result = await session.execute(stmt)
        posts = result.scalars().all()

    by_tag: dict[str, dict] = {}
    for post in posts:
        for tag in post.issue_tags or []:
            entry = by_tag.setdefault(
                tag,
                {"issue_tag": tag, "count": 0, "sentiment_sum": 0.0, "examples": []},
            )
            entry["count"] += 1
            entry["sentiment_sum"] += float(post.sentiment or 0.0)
            if len(entry["examples"]) < max_examples_per_tag:
                entry["examples"].append(
                    {
                        "id": str(post.id),
                        "content": (post.content or "")[:200],
                        "sentiment": post.sentiment,
                        "created_at": post.created_at.isoformat() if post.created_at else None,
                    }
                )

    ranked = sorted(by_tag.values(), key=lambda e: e["count"], reverse=True)[:top_k]
    for entry in ranked:
        entry["avg_sentiment"] = (
            round(entry["sentiment_sum"] / entry["count"], 3) if entry["count"] else None
        )
        entry.pop("sentiment_sum", None)

    return {
        "period_hours": hours,
        "total_negative_posts": sum(e["count"] for e in ranked),
        "complaints": ranked,
    }


async def get_response_effectiveness(issue_tag: str, window_days: int) -> dict:
    normalized_tag = LEGACY_TAG_ALIASES.get(issue_tag.strip().lower(), issue_tag.strip().lower())

    async with AsyncSessionLocal() as session:
        resp_stmt = (
            select(OfficialResponse)
            .where(OfficialResponse.issue_tag == normalized_tag)
            .order_by(OfficialResponse.created_at.desc())
            .limit(1)
        )
        latest = (await session.execute(resp_stmt)).scalar_one_or_none()

        if not latest:
            return {
                "issue_tag": normalized_tag,
                "status": "no_official_response",
                "message": "No approved official response found for this tag yet.",
            }

        anchor = latest.created_at
        before_start = anchor - timedelta(days=window_days)
        after_end = anchor + timedelta(days=window_days)

        async def _avg(start, end):
            stmt = (
                select(func.avg(Post.sentiment), func.count(Post.id))
                .where(Post.analyzed_at.is_not(None))
                .where(Post.issue_tags.is_not(None))
                .where(Post.issue_tags.overlap([normalized_tag]))
                .where(Post.created_at >= start)
                .where(Post.created_at < end)
            )
            avg, count = (await session.execute(stmt)).one()
            return float(avg) if avg is not None else None, count or 0

        before_avg, before_count = await _avg(before_start, anchor)
        after_avg, after_count = await _avg(anchor, after_end)

    shift = None
    if before_avg is not None and after_avg is not None:
        shift = round(after_avg - before_avg, 3)

    return {
        "issue_tag": normalized_tag,
        "approval_at": anchor.isoformat() if anchor else None,
        "window_days": window_days,
        "before": {
            "avg_sentiment": round(before_avg, 3) if before_avg is not None else None,
            "post_count": before_count,
        },
        "after": {
            "avg_sentiment": round(after_avg, 3) if after_avg is not None else None,
            "post_count": after_count,
        },
        "sentiment_shift": shift,
        "verdict": (
            "improved" if shift is not None and shift > 0.05
            else "degraded" if shift is not None and shift < -0.05
            else "neutral" if shift is not None
            else "insufficient_data"
        ),
    }


sse = SseServerTransport("/messages/")


async def handle_sse(scope, receive, send):
    try:
        async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
            await mcp_app.run(
                read_stream, write_stream, mcp_app.create_initialization_options()
            )
    except Exception:
        logger.exception("mcp_sse_connection_error")


_inner = Starlette(
    routes=[
        Mount("/messages/", app=sse.handle_post_message),
    ]
)


async def starlette_app(scope, receive, send):
    if scope["type"] == "http" and scope.get("path", "").rstrip("/") == "/sse":
        await handle_sse(scope, receive, send)
    else:
        await _inner(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(starlette_app, host="0.0.0.0", port=8001)
