"""Send a fake alert to Slack to verify integration."""
import asyncio
import uuid

from config import settings
from slack_app.handlers.alert_handler import send_alert
from slack_sdk.web.async_client import AsyncWebClient


async def main():
    if not settings.slack_bot_token or settings.slack_bot_token.startswith("xoxb-xxxx"):
        print("SLACK_BOT_TOKEN not configured in .env")
        return

    client = AsyncWebClient(token=settings.slack_bot_token)
    alert_id = str(uuid.uuid4())

    fake_alert = {
        "id": alert_id,
        "alert_type": "sentiment_drop",
        "severity": "high",
        "trigger_data": {
            "drop": 0.45,
            "recent_avg": -0.32,
            "earlier_avg": 0.13,
            "recent_count": 15,
            "representative_posts": [
                {
                    "content": "Servers are completely broken after the latest update. Getting disconnected every other match.",
                    "sentiment": -0.8,
                    "url": "https://store.steampowered.com/app/578080",
                    "issue_tags": ["server-stability"],
                },
                {
                    "content": "Cheaters are back in full force, ran into 3 aimbotters in ranked today.",
                    "sentiment": -0.9,
                    "url": "https://store.steampowered.com/app/578080",
                    "issue_tags": ["anti-cheat"],
                },
            ],
        },
    }

    fake_drafts = [
        {
            "id": str(uuid.uuid4()),
            "alert_id": alert_id,
            "tone": "official",
            "content": "We are aware of the connectivity issues some players are experiencing following the latest update. Our team is actively investigating and working on a fix. We will provide an update as soon as possible.",
            "status": "pending",
        },
        {
            "id": str(uuid.uuid4()),
            "alert_id": alert_id,
            "tone": "empathetic",
            "content": "We hear you — disconnects mid-match are incredibly frustrating, especially in ranked. This is our top priority right now, and we're working to resolve it. Thank you for your patience.",
            "status": "pending",
        },
        {
            "id": str(uuid.uuid4()),
            "alert_id": alert_id,
            "tone": "concise",
            "content": "We're investigating the post-update connectivity issues. A hotfix is in progress.",
            "status": "pending",
        },
    ]

    print(f"Sending test alert to {settings.slack_alert_channel}...")
    ts = await send_alert(client, fake_alert, drafts=fake_drafts)

    if ts:
        print(f"Sent! Message timestamp: {ts}")
    else:
        print("Failed to send. Check logs and bot permissions.")


if __name__ == "__main__":
    asyncio.run(main())
