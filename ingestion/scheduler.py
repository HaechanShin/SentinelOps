import asyncio

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agents.graph import run_pipeline
from config import settings
from ingestion.reddit_collector import run_reddit_collection
from ingestion.steam_collector import run_steam_collection

logger = structlog.get_logger()


async def collection_cycle():
    logger.info("collection_cycle_start")
    try:
        reddit_result = await run_reddit_collection()
        steam_result = await run_steam_collection()

        total_stored = reddit_result["stored"] + steam_result["stored"]
        logger.info(
            "collection_cycle_complete",
            reddit=reddit_result,
            steam=steam_result,
            total_new=total_stored,
        )

        if total_stored > 0:
            await run_pipeline()

    except Exception:
        logger.exception("collection_cycle_error")


def main():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        collection_cycle,
        "interval",
        seconds=settings.polling_interval_seconds,
        id="collection_cycle",
        max_instances=1,
    )

    logger.info(
        "scheduler_starting",
        interval_seconds=settings.polling_interval_seconds,
    )

    scheduler.start()

    loop = asyncio.new_event_loop()
    loop.create_task(collection_cycle())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        scheduler.shutdown()
        logger.info("scheduler_stopped")


if __name__ == "__main__":
    main()
