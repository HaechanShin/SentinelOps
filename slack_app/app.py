import asyncio

import structlog
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from config import settings
from slack_app.handlers.approval_handler import register_handlers

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()

app = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)

register_handlers(app)


@app.event("app_mention")
async def handle_mention(event, say):
    text = event.get("text", "").lower()

    if "status" in text or "summary" in text:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/api/v1/dashboard/summary")
            data = resp.json()

        await say(
            f"📊 *SentinelOps Status*\n"
            f"• Posts (24h): {data.get('total_posts', 0)}\n"
            f"• Avg Sentiment: {data.get('average_sentiment', 'N/A')}\n"
            f"• Open Alerts: {data.get('alerts_open', 0)}\n"
            f"• Pending Drafts: {data.get('drafts_pending', 0)}\n"
            f"• Approval Rate: {data.get('approval_rate', 'N/A')}"
        )
    else:
        await say(
            "👋 I'm SentinelOps! I monitor the PUBG community.\n"
            "Try mentioning me with `status` or `summary` for a report."
        )


@app.command("/sentinelops")
async def handle_command(ack, respond, command):
    await ack()
    subcommand = command.get("text", "").strip().lower()

    if subcommand == "status":
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/api/v1/dashboard/summary")
            data = resp.json()
        await respond(
            f"📊 *SentinelOps Dashboard*\n"
            f"Posts: {data.get('total_posts', 0)} | "
            f"Sentiment: {data.get('average_sentiment', 'N/A')} | "
            f"Alerts: {data.get('alerts_open', 0)} open"
        )
    elif subcommand == "run":
        await respond("🔄 Triggering pipeline run...")
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post("http://localhost:8000/api/v1/pipeline/run")
            data = resp.json()
        await respond(
            f"✅ Pipeline complete: {data.get('sentiments_analyzed', 0)} analyzed, "
            f"{data.get('alerts_triggered', 0)} alerts, "
            f"{data.get('drafts_generated', 0)} drafts"
        )
    else:
        await respond(
            "Available commands:\n"
            "• `/sentinelops status` — View dashboard summary\n"
            "• `/sentinelops run` — Trigger pipeline manually"
        )


def main():
    logger.info("slack_bot_starting")
    handler = AsyncSocketModeHandler(app)
    asyncio.get_event_loop().run_until_complete(handler.start_async())


if __name__ == "__main__":
    main()
