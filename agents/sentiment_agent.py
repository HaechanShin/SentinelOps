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

SYSTEM_PROMPT = """You are a community issue classifier for PUBG (a battle royale game).
Analyze the given Steam review and return a JSON object with:
1. "sentiment": a float from -1.0 (very negative) to 1.0 (very positive)
2. "issue_tags": pick exactly 1 tag from ONLY these values: ["anti-cheat", "server-stability", "optimization", "game-balance", "new-content", "matchmaking", "bugs", "monetization", "general"]

Sentiment scoring guide:
- 0.7 to 1.0: clearly positive
- 0.1 to 0.6: mildly positive or mixed
- -0.1 to 0.1: neutral or unclear
- -0.6 to -0.1: mildly negative
- -1.0 to -0.7: clearly negative

Tag definitions (pick the ONE best fit):
- "anti-cheat": hackers, aimbots, wallhacks, cheater reports, anti-cheat system complaints
- "server-stability": lag, high ping, disconnects, server crashes, desync, region issues
- "optimization": FPS drops, stuttering, crashes to desktop, hardware performance, loading times
- "game-balance": weapon balance, vehicle balance, circle/zone complaints, loot distribution
- "new-content": reactions to maps, modes, skins, seasons, patches, events
- "matchmaking": queue times, skill-based matchmaking, bots in lobbies, ranking system
- "bugs": specific glitches, broken mechanics, visual/audio bugs, exploit reports
- "monetization": pricing, battle pass value, crate/skin costs, paid content complaints
- "general": overall game opinion, nostalgia, playtime milestones, recommendation without specific topic

Rules:
- Always pick exactly 1 tag, never 0 or 2+.
- Non-English reviews: classify based on recognizable keywords, tone, and context.
- Very short or single-character reviews: sentiment 0.0, tag "general".

Return ONLY valid JSON, no other text."""


async def analyze_sentiment(content: str) -> dict:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3)

    with SENTIMENT_LATENCY.time():
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content[:2000]}],
        )

    SENTIMENT_REQUESTS.inc()
    text = response.content[0].text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    start = text.index("{")
    depth = 0
    end = start
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    text = text[start:end]

    result = json.loads(text)
    tags = result.get("issue_tags", [])
    if isinstance(tags, str):
        tags = [tags]
    return {
        "sentiment": max(-1.0, min(1.0, float(result["sentiment"]))),
        "issue_tags": tags,
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
            if not post.content or not post.content.strip():
                async with AsyncSessionLocal() as session:
                    stmt = (
                        update(Post)
                        .where(Post.id == post.id)
                        .values(
                            sentiment=0.0,
                            issue_tags=["general"],
                            analyzed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.execute(stmt)
                    await session.commit()
                results.append({"post_id": str(post.id), "sentiment": 0.0, "issue_tags": ["general"]})
                continue

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
