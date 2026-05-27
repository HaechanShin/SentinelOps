from datetime import datetime, timezone

import httpx
import redis.asyncio as aioredis
import structlog
from sqlalchemy.dialects.postgresql import insert

from config import settings
from db.engine import AsyncSessionLocal
from db.models import Post

logger = structlog.get_logger()

STEAM_REVIEWS_URL = "https://store.steampowered.com/appreviews/{app_id}"
CURSOR_KEY = "steam:review_cursor"


async def _get_redis():
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def collect_steam_reviews(per_page: int = 50, max_pages: int = 10) -> list[dict]:
    redis = await _get_redis()
    cursor = await redis.get(CURSOR_KEY) or "*"
    all_reviews = []

    async with httpx.AsyncClient(timeout=30) as client:
        for page in range(max_pages):
            params = {
                "json": "1",
                "filter": "recent",
                "language": "all",
                "num_per_page": per_page,
                "purchase_type": "all",
                "cursor": cursor,
            }
            resp = await client.get(
                STEAM_REVIEWS_URL.format(app_id=settings.steam_app_id), params=params
            )
            resp.raise_for_status()
            data = resp.json()

            batch = data.get("reviews", [])
            if not batch:
                break

            for review in batch:
                all_reviews.append({
                    "source": "steam",
                    "external_id": f"steam_{review['recommendationid']}",
                    "title": None,
                    "content": review["review"],
                    "author": review["author"].get("steamid", "unknown"),
                    "url": f"https://store.steampowered.com/app/{settings.steam_app_id}",
                    "recommended": review.get("voted_up", None),
                    "created_at": datetime.fromtimestamp(
                        review["timestamp_created"], tz=timezone.utc
                    ),
                })

            next_cursor = data.get("cursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

            logger.info(
                "steam_reviews_page",
                page=page + 1,
                batch_size=len(batch),
                cursor=next_cursor,
            )

    if cursor != "*":
        await redis.set(CURSOR_KEY, cursor)

    logger.info(
        "steam_reviews_collected",
        count=len(all_reviews),
        app_id=settings.steam_app_id,
    )
    await redis.aclose()
    return all_reviews


async def store_reviews(reviews: list[dict]) -> int:
    if not reviews:
        return 0

    stored = 0
    async with AsyncSessionLocal() as session:
        for review_data in reviews:
            stmt = insert(Post).values(**review_data).on_conflict_do_nothing(
                index_elements=["external_id"]
            )
            result = await session.execute(stmt)
            if result.rowcount > 0:
                stored += 1
        await session.commit()

    logger.info("steam_reviews_stored", new_count=stored, total_count=len(reviews))
    return stored


async def run_steam_collection():
    reviews = await collect_steam_reviews()
    stored = await store_reviews(reviews)
    return {"collected": len(reviews), "stored": stored}
