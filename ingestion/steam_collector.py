from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy.dialects.postgresql import insert

from config import settings
from db.engine import AsyncSessionLocal
from db.models import Post

logger = structlog.get_logger()

STEAM_REVIEWS_URL = "https://store.steampowered.com/appreviews/{app_id}"


async def collect_steam_reviews(count: int = 50) -> list[dict]:
    params = {
        "json": "1",
        "filter": "recent",
        "language": "english",
        "num_per_page": count,
        "purchase_type": "all",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            STEAM_REVIEWS_URL.format(app_id=settings.steam_app_id), params=params
        )
        resp.raise_for_status()
        data = resp.json()

    reviews = []
    for review in data.get("reviews", []):
        review_data = {
            "source": "steam",
            "external_id": f"steam_{review['recommendationid']}",
            "title": None,
            "content": review["review"],
            "author": review["author"].get("steamid", "unknown"),
            "url": f"https://store.steampowered.com/app/{settings.steam_app_id}",
            "created_at": datetime.fromtimestamp(
                review["timestamp_created"], tz=timezone.utc
            ),
        }
        reviews.append(review_data)

    logger.info(
        "steam_reviews_collected",
        count=len(reviews),
        app_id=settings.steam_app_id,
    )
    return reviews


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
