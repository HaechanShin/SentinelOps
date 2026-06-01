import asyncio

import structlog

from config import settings

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()

API_BASE = settings.internal_api_url


def create_app():
    from slack_bolt.async_app import AsyncApp

    from slack_app.handlers.approval_handler import register_handlers

    bolt_app = AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
    )

    register_handlers(bolt_app)

    @bolt_app.event("app_mention")
    async def handle_mention(event, say):
        text = event.get("text", "").lower()

        if "status" in text or "summary" in text:
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{API_BASE}/api/v1/dashboard/summary")
                data = resp.json()

            await say(
                f"*SentinelOps Status*\n"
                f"- Posts (24h): {data.get('total_posts', 0)}\n"
                f"- Avg Sentiment: {data.get('average_sentiment', 'N/A')}\n"
                f"- Open Alerts: {data.get('alerts_open', 0)}\n"
                f"- Pending Drafts: {data.get('drafts_pending', 0)}\n"
                f"- Approval Rate: {data.get('approval_rate', 'N/A')}"
            )
        else:
            await say(
                "I'm SentinelOps! I monitor the game's Steam community.\n"
                "Try mentioning me with `status` or `summary` for a report."
            )

    @bolt_app.command("/sentinelops")
    async def handle_command(ack, respond, command):
        await ack()
        subcommand = command.get("text", "").strip().lower()

        if subcommand == "status":
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{API_BASE}/api/v1/dashboard/summary")
                data = resp.json()
            await respond(
                f"*SentinelOps Dashboard*\n"
                f"Posts: {data.get('total_posts', 0)} | "
                f"Sentiment: {data.get('average_sentiment', 'N/A')} | "
                f"Alerts: {data.get('alerts_open', 0)} open"
            )
        elif subcommand == "run":
            await respond("Triggering pipeline run...")
            import httpx

            headers = {}
            if settings.api_secret_key:
                headers["X-API-Key"] = settings.api_secret_key
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{API_BASE}/api/v1/pipeline/run", headers=headers
                )
                data = resp.json()
            await respond(
                f"Pipeline complete: {data.get('sentiments_analyzed', 0)} analyzed, "
                f"{data.get('alerts_triggered', 0)} alerts, "
                f"{data.get('drafts_generated', 0)} drafts"
            )
        else:
            await respond(
                "Available commands:\n"
                "- `/sentinelops status` -- View dashboard summary\n"
                "- `/sentinelops run` -- Trigger pipeline manually"
            )

    return bolt_app


async def async_main():
    bolt_app = create_app()

    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    handler = AsyncSocketModeHandler(bolt_app, app_token=settings.slack_app_token)
    await handler.start_async()


def main():
    logger.info("slack_bot_starting")

    if not settings.slack_app_token or settings.slack_app_token.startswith("xapp-xxxx"):
        logger.warning("slack_not_configured", msg="Slack tokens not configured, bot idle")
        import time
        while True:
            time.sleep(3600)

    asyncio.run(async_main())


if __name__ == "__main__":
    main()
