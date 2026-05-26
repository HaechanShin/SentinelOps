from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from agents.graph import run_pipeline
from api.routers import alerts, drafts, posts
from config import settings

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("sentinelops_starting")
    yield
    logger.info("sentinelops_stopping")


app = FastAPI(
    title="SentinelOps",
    description="PUBG Community AI Ops System — Real-time sentiment monitoring and response drafting",
    version="1.0.0",
    lifespan=lifespan,
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(posts.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")
app.include_router(drafts.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "sentinelops"}


@app.post("/api/v1/pipeline/run")
async def trigger_pipeline():
    result = await run_pipeline()
    return {
        "sentiments_analyzed": len(result.get("sentiment_results", [])),
        "alerts_triggered": len(result.get("alerts", [])),
        "drafts_generated": len(result.get("drafts", [])),
    }


@app.get("/api/v1/dashboard/summary")
async def dashboard_summary():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from db.engine import AsyncSessionLocal
    from db.models import Alert, Draft, Post

    since = datetime.now(timezone.utc) - timedelta(hours=24)

    async with AsyncSessionLocal() as session:
        post_stmt = (
            select(func.count(Post.id), func.avg(Post.sentiment))
            .where(Post.analyzed_at.is_not(None))
            .where(Post.created_at >= since)
        )
        post_result = await session.execute(post_stmt)
        total_posts, avg_sentiment = post_result.one()

        source_stmt = (
            select(Post.source, func.count(Post.id))
            .where(Post.created_at >= since)
            .group_by(Post.source)
        )
        source_result = await session.execute(source_stmt)
        by_source = {row[0]: row[1] for row in source_result}

        open_alerts = (
            await session.execute(
                select(func.count(Alert.id)).where(Alert.status == "open")
            )
        ).scalar()

        total_alerts = (
            await session.execute(
                select(func.count(Alert.id)).where(Alert.created_at >= since)
            )
        ).scalar()

        pending_drafts = (
            await session.execute(
                select(func.count(Draft.id)).where(Draft.status == "pending")
            )
        ).scalar()

        reviewed = (
            await session.execute(
                select(func.count(Draft.id)).where(Draft.status != "pending")
            )
        ).scalar()
        approved = (
            await session.execute(
                select(func.count(Draft.id)).where(Draft.status == "approved")
            )
        ).scalar()

    return {
        "total_posts": total_posts or 0,
        "average_sentiment": round(float(avg_sentiment), 3) if avg_sentiment else None,
        "posts_by_source": by_source,
        "alerts_open": open_alerts or 0,
        "alerts_total_24h": total_alerts or 0,
        "drafts_pending": pending_drafts or 0,
        "approval_rate": round(approved / reviewed, 3) if reviewed > 0 else None,
    }
