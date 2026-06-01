import asyncio

import structlog
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agents.graph import run_alert_pipeline, run_sentiment_pipeline
from agents.sentiment_agent import process_unanalyzed_posts
from config import settings
from ingestion.news_collector import run_news_collection
from ingestion.steam_collector import run_steam_collection

logger = structlog.get_logger()


async def collection_cycle():
    """Hourly: collect Steam reviews + patch notes, then run sentiment analysis.

    Sentiment analysis runs whenever there is *any* unanalyzed post — not only
    when the current cycle collected new ones. This matters after a backfill
    leaves a large pool of unanalyzed posts behind.
    """
    logger.info("collection_cycle_start")

    try:
        steam_result = await run_steam_collection()
        logger.info("steam_collection_done", **steam_result)
    except Exception:
        logger.exception("steam_collection_error")

    try:
        news_result = await run_news_collection()
        logger.info("news_collection_done", **news_result)
    except Exception:
        logger.exception("news_collection_error")

    try:
        await run_sentiment_pipeline()
    except Exception:
        logger.exception("sentiment_pipeline_error")


async def daily_alert_cycle():
    """Daily: evaluate alerts on the last 24h vs prior 24h, draft + notify."""
    logger.info("daily_alert_cycle_start")
    try:
        await run_alert_pipeline()
    except Exception:
        logger.exception("alert_pipeline_error")


def _on_job_event(event):
    if event.exception:
        logger.error(
            "scheduler_job_error",
            job_id=event.job_id,
            exception=str(event.exception),
        )
    else:
        logger.warning("scheduler_job_missed", job_id=event.job_id)


async def amain():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    scheduler.add_job(
        collection_cycle,
        "interval",
        seconds=settings.polling_interval_seconds,
        id="collection_cycle",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        daily_alert_cycle,
        CronTrigger(hour=settings.daily_alert_hour_utc, minute=0, timezone="UTC"),
        id="daily_alert_cycle",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    logger.info(
        "scheduler_starting",
        collection_interval_seconds=settings.polling_interval_seconds,
        daily_alert_hour_utc=settings.daily_alert_hour_utc,
    )

    scheduler.start()

    try:
        await collection_cycle()
    except Exception:
        logger.exception("initial_collection_error")

    # Keep the event loop alive so the scheduler can keep firing.
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
