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
LATEST_REVIEW_ID_KEY = "steam:{app_id}:latest_review_id"


def _latest_review_id_key() -> str:
    return LATEST_REVIEW_ID_KEY.format(app_id=settings.steam_app_id)


async def _get_redis():
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def collect_steam_reviews(
    per_page: int = 50,
    max_pages: int = 10,
    last_seen_review_id: str | None = None,
) -> tuple[list[dict], str | None]:
    cursor = "*"
    all_reviews = []
    latest_review_id = None
    reached_last_seen = False

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
                review_id = str(review["recommendationid"])
                latest_review_id = latest_review_id or review_id

                if last_seen_review_id and review_id == last_seen_review_id:
                    reached_last_seen = True
                    break

                all_reviews.append({
                    "source": "steam",
                    "external_id": f"steam_{review_id}",
                    "title": None,
                    "content": review["review"],
                    "author": review["author"].get("steamid", "unknown"),
                    "url": f"https://store.steampowered.com/app/{settings.steam_app_id}",
                    "recommended": review.get("voted_up", None),
                    "created_at": datetime.fromtimestamp(
                        review["timestamp_created"], tz=timezone.utc
                    ),
                })

            logger.info(
                "steam_reviews_page",
                page=page + 1,
                batch_size=len(batch),
                collected_count=len(all_reviews),
                reached_last_seen=reached_last_seen,
            )

            if reached_last_seen:
                break

            next_cursor = data.get("cursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    logger.info(
        "steam_reviews_collected",
        count=len(all_reviews),
        app_id=settings.steam_app_id,
        latest_review_id=latest_review_id,
        reached_last_seen=reached_last_seen,
    )
    return all_reviews, latest_review_id


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
    redis = await _get_redis()
    try:
        latest_key = _latest_review_id_key()
        last_seen_review_id = await redis.get(latest_key)
        reviews, latest_review_id = await collect_steam_reviews(
            last_seen_review_id=last_seen_review_id
        )
        stored = await store_reviews(reviews)

        if latest_review_id:
            await redis.set(latest_key, latest_review_id)
    finally:
        await redis.aclose()

    return {"collected": len(reviews), "stored": stored}
