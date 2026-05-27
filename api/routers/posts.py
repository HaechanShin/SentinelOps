from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import PostOut, SentimentTrendPoint
from db.engine import get_session
from db.models import Post

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("", response_model=list[PostOut])
async def list_posts(
    source: str | None = None,
    analyzed: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Post)

    if source:
        stmt = stmt.where(Post.source == source)
    if analyzed is True:
        stmt = stmt.where(Post.analyzed_at.is_not(None))
    elif analyzed is False:
        stmt = stmt.where(Post.analyzed_at.is_(None))

    stmt = stmt.order_by(Post.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/sentiment/trend")
async def sentiment_trend(
    hours: int = Query(default=24, ge=1, le=168),
    session: AsyncSession = Depends(get_session),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    stmt = (
        select(
            func.date_trunc("hour", Post.created_at).label("hour"),
            func.avg(Post.sentiment).label("avg_sentiment"),
            func.count(Post.id).label("post_count"),
        )
        .where(Post.analyzed_at.is_not(None))
        .where(Post.created_at >= since)
        .group_by(text("1"))
        .order_by(text("1"))
    )
    result = await session.execute(stmt)

    return [
        {
            "hour": row[0].isoformat() if row[0] else None,
            "avg_sentiment": round(float(row[1]), 3) if row[1] else None,
            "post_count": row[2],
        }
        for row in result
    ]


@router.get("/tags/distribution")
async def tag_distribution(
    hours: int = Query(default=24, ge=1, le=168),
    session: AsyncSession = Depends(get_session),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    stmt = select(Post.issue_tags).where(
        Post.analyzed_at.is_not(None),
        Post.issue_tags.is_not(None),
        Post.created_at >= since,
    )
    result = await session.execute(stmt)

    tag_counts: dict[str, int] = {}
    for (tags,) in result:
        if tags:
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
    return [{"tag": tag, "count": count} for tag, count in sorted_tags]
