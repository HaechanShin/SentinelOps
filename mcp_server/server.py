"""MCP Server — Community Intelligence Layer for SentinelOps.

Provides tools for querying community data, sentiment trends,
official responses, and patch notes via the Model Context Protocol.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from sqlalchemy import func, select, text

from config import settings
from db.engine import AsyncSessionLocal
from db.models import Alert, Draft, OfficialResponse, PatchNote, Post

app = Server("sentinelops-community")


@app.list_tools()
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
            description="Get official response templates for a given issue tag (e.g., 'server', 'bug', 'cheat').",
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
            description="Get the most recent PUBG patch notes.",
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
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    match name:
        case "get_similar_issues":
            result = await _get_similar_issues(
                arguments["issue_description"], arguments.get("top_k", 5)
            )
        case "get_official_responses":
            result = await _get_official_responses(arguments["issue_tag"])
        case "get_sentiment_trend":
            result = await _get_sentiment_trend(arguments.get("hours", 24))
        case "get_patch_notes":
            result = await _get_patch_notes(arguments.get("recent", 3))
        case "get_alert_history":
            result = await _get_alert_history(
                arguments.get("hours", 24),
                arguments.get("alert_type"),
                arguments.get("status"),
            )
        case "get_community_summary":
            result = await _get_community_summary(arguments.get("hours", 24))
        case _:
            result = {"error": f"Unknown tool: {name}"}

    return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]


async def _get_similar_issues(description: str, top_k: int) -> list[dict]:
    keywords = description.lower().split()
    async with AsyncSessionLocal() as session:
        conditions = []
        for kw in keywords[:10]:
            conditions.append(func.lower(Post.content).contains(kw))

        stmt = (
            select(Post)
            .where(Post.analyzed_at.is_not(None))
            .order_by(Post.created_at.desc())
            .limit(top_k * 5)
        )
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


async def _get_official_responses(issue_tag: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(OfficialResponse)
            .where(OfficialResponse.issue_tag == issue_tag)
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


async def _get_sentiment_trend(hours: int) -> list[dict]:
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


async def _get_patch_notes(recent: int) -> list[dict]:
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


async def _get_alert_history(
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


async def _get_community_summary(hours: int) -> dict:
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


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
