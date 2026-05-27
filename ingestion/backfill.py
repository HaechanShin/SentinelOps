"""
Backfill historical Steam reviews.

Usage:
  docker compose exec app python -m ingestion.backfill --days 30
  docker compose exec app python -m ingestion.backfill --days 7 --analyze
"""

import argparse
import asyncio
from datetime import datetime, timezone, timedelta

import httpx
import structlog
from sqlalchemy.dialects.postgresql import insert

from config import settings
from db.engine import AsyncSessionLocal
from db.models import Post
from agents.sentiment_agent import process_unanalyzed_posts

logger = structlog.get_logger()

STEAM_REVIEWS_URL = "https://store.steampowered.com/appreviews/{app_id}"


async def backfill_reviews(days: int, analyze: bool):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cursor = "*"
    total_collected = 0
    total_stored = 0
    page = 0

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    logger.info("backfill_start", days=days, cutoff=cutoff.isoformat(), analyze=analyze)

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            page += 1
            params = {
                "json": "1",
                "filter": "recent",
                "language": "all",
                "num_per_page": 100,
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

            reviews = []
            reached_cutoff = False
            for review in batch:
                created = datetime.fromtimestamp(
                    review["timestamp_created"], tz=timezone.utc
                )
                if created < cutoff:
                    reached_cutoff = True
                    break
                reviews.append({
                    "source": "steam",
                    "external_id": f"steam_{review['recommendationid']}",
                    "title": None,
                    "content": review["review"],
                    "author": review["author"].get("steamid", "unknown"),
                    "url": f"https://store.steampowered.com/app/{settings.steam_app_id}",
                    "recommended": review.get("voted_up", None),
                    "created_at": created,
                })

            stored = 0
            if reviews:
                async with AsyncSessionLocal() as session:
                    for r in reviews:
                        stmt = insert(Post).values(**r).on_conflict_do_nothing(
                            index_elements=["external_id"]
                        )
                        result = await session.execute(stmt)
                        if result.rowcount > 0:
                            stored += 1
                    await session.commit()

            total_collected += len(reviews)
            total_stored += stored

            logger.info(
                "backfill_page",
                page=page,
                collected=len(reviews),
                stored=stored,
                total_collected=total_collected,
                total_stored=total_stored,
            )

            if reached_cutoff:
                break

            next_cursor = data.get("cursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    logger.info(
        "backfill_collection_done",
        total_collected=total_collected,
        total_stored=total_stored,
        pages=page,
    )

    if analyze and total_stored > 0:
        logger.info("backfill_analysis_start", posts=total_stored)
        analyzed = 0
        while True:
            batch = await process_unanalyzed_posts(batch_size=20)
            if not batch:
                break
            analyzed += len(batch)
            logger.info("backfill_analysis_progress", analyzed=analyzed)
        logger.info("backfill_analysis_done", total_analyzed=analyzed)

    logger.info("backfill_complete")


def main():
    parser = argparse.ArgumentParser(description="Backfill historical Steam reviews")
    parser.add_argument("--days", type=int, required=True, help="Number of days to backfill")
    parser.add_argument("--analyze", action="store_true", help="Run AI analysis on collected reviews")
    args = parser.parse_args()

    asyncio.run(backfill_reviews(days=args.days, analyze=args.analyze))


if __name__ == "__main__":
    main()
