import json
from datetime import datetime, timezone

import anthropic
import structlog
from prometheus_client import Counter, Histogram
from sqlalchemy import select, update

from config import settings
from db.engine import AsyncSessionLocal
from db.models import Post

logger = structlog.get_logger()

SENTIMENT_REQUESTS = Counter(
    "sentiment_analysis_total", "Total sentiment analysis requests"
)
SENTIMENT_LATENCY = Histogram(
    "sentiment_analysis_seconds", "Sentiment analysis latency"
)

SYSTEM_PROMPT = """You are a sentiment analysis expert for the PUBG gaming community.
Analyze the given community post and return a JSON object with:
1. "sentiment": a float from -1.0 (very negative) to 1.0 (very positive)
2. "issue_tags": a list of relevant tags from: ["bug", "server", "cheat", "update", "ban", "performance", "matchmaking", "content", "praise", "suggestion", "question"]

Consider gaming community context:
- Server complaints, lag, disconnects → negative + "server"
- Cheater reports → negative + "cheat"
- Bug reports → negative/neutral + "bug"
- Praise for updates → positive + "update" or "praise"
- Balance complaints → negative + "update"
- Performance issues → negative + "performance"

Return ONLY valid JSON, no other text."""


async def analyze_sentiment(content: str) -> dict:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    with SENTIMENT_LATENCY.time():
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content[:2000]}],
        )

    SENTIMENT_REQUESTS.inc()
    text = response.content[0].text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    result = json.loads(text)
    return {
        "sentiment": max(-1.0, min(1.0, float(result["sentiment"]))),
        "issue_tags": result.get("issue_tags", []),
    }


async def process_unanalyzed_posts(batch_size: int = 20) -> list[dict]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Post)
            .where(Post.analyzed_at.is_(None))
            .order_by(Post.created_at.desc())
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        posts = result.scalars().all()

    if not posts:
        logger.info("no_unanalyzed_posts")
        return []

    results = []
    for post in posts:
        try:
            analysis = await analyze_sentiment(post.content)

            async with AsyncSessionLocal() as session:
                stmt = (
                    update(Post)
                    .where(Post.id == post.id)
                    .values(
                        sentiment=analysis["sentiment"],
                        issue_tags=analysis["issue_tags"],
                        analyzed_at=datetime.now(timezone.utc),
                    )
                )
                await session.execute(stmt)
                await session.commit()

            results.append(
                {
                    "post_id": str(post.id),
                    "external_id": post.external_id,
                    "sentiment": analysis["sentiment"],
                    "issue_tags": analysis["issue_tags"],
                }
            )
            logger.info(
                "post_analyzed",
                post_id=str(post.id),
                sentiment=analysis["sentiment"],
                tags=analysis["issue_tags"],
            )
        except Exception:
            logger.exception("sentiment_analysis_failed", post_id=str(post.id))

    logger.info("batch_analysis_complete", analyzed=len(results), total=len(posts))
    return results
