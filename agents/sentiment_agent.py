import json
from datetime import datetime, timezone

import anthropic
import structlog
from prometheus_client import Counter, Histogram
from sqlalchemy import select, update

from config import settings
from constants import DEFAULT_ISSUE_TAG, ISSUE_TAG_DESCRIPTIONS, ISSUE_TAGS, LEGACY_TAG_ALIASES
from db.engine import AsyncSessionLocal
from db.models import Post

logger = structlog.get_logger()

SENTIMENT_REQUESTS = Counter(
    "sentiment_analysis_total", "Total sentiment analysis requests"
)
SENTIMENT_LATENCY = Histogram(
    "sentiment_analysis_seconds", "Sentiment analysis latency"
)

SYSTEM_PROMPT = """You are a multilingual Steam review analyst for PUBG LiveOps monitoring.

Your job: read a Steam review in ANY language, understand its meaning, and classify it.

Steam reviews come in dozens of languages (Chinese, Russian, Portuguese, Turkish, Thai, Korean, Japanese, Arabic, etc.). You MUST understand and analyze the review in its original language. Do NOT rely on keyword matching. Understand the full context and meaning of what the reviewer is saying.

Return ONLY valid JSON in this exact format:
{"sentiment": <float>, "issue_tags": ["<tag>"], "translated": "<Korean translation>"}

## sentiment (float, -1.0 to 1.0)
Analyze the reviewer's overall feeling based on what they wrote:
- -1.0 = extremely negative (rage, refund demand, calling game dead)
- -0.5 = clearly negative (frustrated, disappointed)
-  0.0 = neutral or mixed feelings
-  0.5 = clearly positive (enjoying, recommending)
-  1.0 = extremely positive (enthusiastic praise)

Factor in the Steam recommendation signal provided with the review:
- "not recommended" with neutral text → lean negative (-0.2 to -0.4)
- "recommended" with neutral text → lean positive (0.2 to 0.4)
- The actual review text takes priority if it clearly contradicts the signal.

## issue_tags (array with exactly ONE tag)
Pick the single most relevant operational category based on what the reviewer is actually discussing:
""" + "\n".join(f'- "{tag}": {desc}' for tag, desc in ISSUE_TAG_DESCRIPTIONS.items()) + """

Choose based on the meaning of the review, not surface-level keywords.
Use "general" only when the review has no specific operational topic.

## translated (string)
Translate the review into English.
- If the review is already in English, copy it as-is.
- Keep it concise — summarize if the original is very long, but preserve the key complaints or praise.

Return ONLY the JSON object. No markdown, no explanation."""


def _format_review(content: str, recommended: bool | None) -> str:
    if recommended is True:
        signal = "recommended"
    elif recommended is False:
        signal = "not recommended"
    else:
        signal = "unknown"
    return f"Steam recommendation: {signal}\n\nReview text:\n{content[:2000]}"


async def analyze_sentiment(content: str, recommended: bool | None = None) -> dict:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3)

    with SENTIMENT_LATENCY.time():
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _format_review(content, recommended)}],
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
    raw_tags = result.get("issue_tags") or result.get("issue_tag") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]

    validated_tags = []
    for tag in raw_tags:
        normalized = str(tag).strip().lower()
        normalized = LEGACY_TAG_ALIASES.get(normalized, normalized)
        if normalized in ISSUE_TAGS:
            validated_tags.append(normalized)

    return {
        "sentiment": max(-1.0, min(1.0, float(result["sentiment"]))),
        "issue_tags": [validated_tags[0]] if validated_tags else [DEFAULT_ISSUE_TAG],
        "translated": result.get("translated") or None,
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

            analysis = await analyze_sentiment(post.content, recommended=post.recommended)

            async with AsyncSessionLocal() as session:
                update_values = {
                    "sentiment": analysis["sentiment"],
                    "issue_tags": analysis["issue_tags"],
                    "analyzed_at": datetime.now(timezone.utc),
                }
                if analysis.get("translated"):
                    update_values["translated_content"] = analysis["translated"]
                stmt = (
                    update(Post)
                    .where(Post.id == post.id)
                    .values(**update_values)
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
