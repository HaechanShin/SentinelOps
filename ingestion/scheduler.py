import asyncio

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agents.graph import run_pipeline
from config import settings
from ingestion.news_collector import run_news_collection
from ingestion.steam_collector import run_steam_collection

logger = structlog.get_logger()


async def collection_cycle():
    logger.info("collection_cycle_start")
    total_stored = 0

    try:
        steam_result = await run_steam_collection()
        total_stored += steam_result["stored"]
        logger.info("steam_collection_done", **steam_result)
    except Exception:
        logger.exception("steam_collection_error")

    try:
        news_result = await run_news_collection()
        logger.info("news_collection_done", **news_result)
    except Exception:
        logger.exception("news_collection_error")

    if total_stored > 0:
        try:
            await run_pipeline()
        except Exception:
            logger.exception("pipeline_error")


def main():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    scheduler = AsyncIOScheduler(event_loop=loop)
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
    loop.create_task(collection_cycle())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        scheduler.shutdown()
        logger.info("scheduler_stopped")


if __name__ == "__main__":
    main()
