import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import structlog
from prometheus_client import Counter as PromCounter
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from config import settings
from constants import ACTIVE_ALERT_STATUSES
from db.engine import AsyncSessionLocal
from db.models import Alert, Post

logger = structlog.get_logger()

ALERTS_TRIGGERED = PromCounter("alerts_triggered_total", "Total alerts triggered", ["type"])


async def get_sentiment_window(hours: int = 24) -> dict:
    """Compare the most recent `hours` window to the equally-sized window before it."""
    now = datetime.now(timezone.utc)
    recent_start = now - timedelta(hours=hours)
    earlier_start = recent_start - timedelta(hours=hours)

    async with AsyncSessionLocal() as session:
        recent_stmt = (
            select(func.avg(Post.sentiment), func.count(Post.id))
            .where(Post.analyzed_at.is_not(None))
            .where(Post.created_at >= recent_start)
            .where(Post.created_at < now)
        )
        recent_result = await session.execute(recent_stmt)
        recent_avg, recent_count = recent_result.one()

        earlier_stmt = (
            select(func.avg(Post.sentiment), func.count(Post.id))
            .where(Post.analyzed_at.is_not(None))
            .where(Post.created_at >= earlier_start)
            .where(Post.created_at < recent_start)
        )
        earlier_result = await session.execute(earlier_stmt)
        earlier_avg, earlier_count = earlier_result.one()

    return {
        "recent_avg": float(recent_avg) if recent_avg else 0.0,
        "recent_count": recent_count or 0,
        "earlier_avg": float(earlier_avg) if earlier_avg else 0.0,
        "earlier_count": earlier_count or 0,
        "window_hours": hours,
    }


async def get_keyword_frequencies(hours: int = 24) -> dict:
    now = datetime.now(timezone.utc)
    recent_start = now - timedelta(hours=hours)
    earlier_start = recent_start - timedelta(hours=hours)

    async with AsyncSessionLocal() as session:
        recent_stmt = (
            select(Post.issue_tags)
            .where(Post.analyzed_at.is_not(None))
            .where(Post.issue_tags.is_not(None))
            .where(Post.created_at >= recent_start)
            .where(Post.created_at < now)
        )
        recent_result = await session.execute(recent_stmt)
        recent_tags = Counter()
        for (tags,) in recent_result:
            if tags:
                recent_tags.update(tags)

        earlier_stmt = (
            select(Post.issue_tags)
            .where(Post.analyzed_at.is_not(None))
            .where(Post.issue_tags.is_not(None))
            .where(Post.created_at >= earlier_start)
            .where(Post.created_at < recent_start)
        )
        earlier_result = await session.execute(earlier_stmt)
        earlier_tags = Counter()
        for (tags,) in earlier_result:
            if tags:
                earlier_tags.update(tags)

    return {"recent": dict(recent_tags), "earlier": dict(earlier_tags)}


async def get_representative_posts(
    issue_tags: list[str], hours: int, limit: int = 3
) -> list[dict]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=hours)

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Post)
            .where(Post.analyzed_at.is_not(None))
            .where(Post.created_at >= window_start)
            .where(Post.issue_tags.overlap(issue_tags))
            .order_by(Post.sentiment.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        posts = result.scalars().all()

    return [
        {
            "id": str(p.id),
            "external_id": p.external_id,
            "content": p.content[:200],
            "sentiment": p.sentiment,
            "url": p.url,
            "issue_tags": p.issue_tags,
        }
        for p in posts
    ]


async def detect_alerts() -> list[dict]:
    alerts = []
    window_hours = settings.alert_window_hours
    min_sample = settings.alert_min_sample_size

    sentiment_data = await get_sentiment_window(window_hours)
    keyword_data = await get_keyword_frequencies(window_hours)

    if (
        sentiment_data["earlier_count"] >= min_sample
        and sentiment_data["recent_count"] >= min_sample
    ):
        drop = sentiment_data["earlier_avg"] - sentiment_data["recent_avg"]
        if drop >= settings.sentiment_drop_threshold:
            severity = "high" if drop >= 0.4 else "medium" if drop >= 0.2 else "low"
            representative = await get_representative_posts(
                ["server-stability", "bugs", "anti-cheat"], hours=window_hours
            )

            alert = {
                "alert_type": "sentiment_drop",
                "severity": severity,
                "trigger_data": {
                    "drop": round(drop, 3),
                    "recent_avg": round(sentiment_data["recent_avg"], 3),
                    "earlier_avg": round(sentiment_data["earlier_avg"], 3),
                    "recent_count": sentiment_data["recent_count"],
                    "window_hours": window_hours,
                    "representative_posts": representative,
                },
            }
            alerts.append(alert)
            ALERTS_TRIGGERED.labels(type="sentiment_drop").inc()
            logger.warning(
                "sentiment_drop_detected",
                drop=round(drop, 3),
                severity=severity,
                window_hours=window_hours,
            )

    for tag, recent_count in keyword_data["recent"].items():
        earlier_count = keyword_data["earlier"].get(tag, 0)
        if earlier_count > 0 and recent_count >= earlier_count * settings.spike_multiplier:
            if recent_count >= min_sample:
                representative = await get_representative_posts([tag], hours=window_hours)
                alert = {
                    "alert_type": "keyword_spike",
                    "severity": "high" if recent_count >= earlier_count * 3 else "medium",
                    "trigger_data": {
                        "keyword": tag,
                        "recent_count": recent_count,
                        "earlier_count": earlier_count,
                        "multiplier": round(recent_count / max(earlier_count, 1), 2),
                        "window_hours": window_hours,
                        "representative_posts": representative,
                    },
                }
                alerts.append(alert)
                ALERTS_TRIGGERED.labels(type="keyword_spike").inc()
                logger.warning(
                    "keyword_spike_detected",
                    keyword=tag,
                    multiplier=round(recent_count / max(earlier_count, 1), 2),
                )

    stored_alerts = []
    cooldown_since = datetime.now(timezone.utc) - timedelta(hours=settings.alert_cooldown_hours)

    async with AsyncSessionLocal() as session:
        for alert_data in alerts:
            fingerprint_type = alert_data["alert_type"]
            fingerprint_tag = alert_data["trigger_data"].get("keyword", "sentiment_drop")

            existing_stmt = (
                select(func.count(Alert.id))
                .where(Alert.alert_type == fingerprint_type)
                .where(Alert.status.in_(ACTIVE_ALERT_STATUSES))
                .where(Alert.created_at >= cooldown_since)
            )
            existing_count = (await session.execute(existing_stmt)).scalar()
            if existing_count and existing_count > 0:
                logger.info(
                    "alert_deduplicated",
                    alert_type=fingerprint_type,
                    tag=fingerprint_tag,
                )
                continue

            alert_id = uuid.uuid4()
            related_ids = [
                p["external_id"] for p in alert_data["trigger_data"].get("representative_posts", [])
            ]
            stmt = insert(Alert).values(
                id=alert_id,
                alert_type=alert_data["alert_type"],
                severity=alert_data["severity"],
                trigger_data=alert_data["trigger_data"],
                related_post_ids=related_ids,
            )
            await session.execute(stmt)
            alert_data["id"] = str(alert_id)
            stored_alerts.append(alert_data)
        await session.commit()

    logger.info("alert_detection_complete", alerts_count=len(stored_alerts))
    return stored_alerts
