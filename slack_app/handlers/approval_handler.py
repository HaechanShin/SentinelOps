import re
from datetime import datetime, timezone

import structlog
from slack_bolt.async_app import AsyncAck, AsyncSay
from sqlalchemy import select, update

from db.engine import AsyncSessionLocal
from db.models import Alert, Draft

logger = structlog.get_logger()


async def handle_approve(ack: AsyncAck, body: dict, say: AsyncSay):
    await ack()

    action = body["actions"][0]
    draft_id = action["value"]
    user = body["user"]["username"]

    async with AsyncSessionLocal() as session:
        stmt = (
            update(Draft)
            .where(Draft.id == draft_id)
            .values(status="approved", reviewed_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.commit()

        result = await session.execute(select(Draft).where(Draft.id == draft_id))
        draft = result.scalar_one_or_none()

    logger.info("draft_approved", draft_id=draft_id, user=user)
    await say(
        f"✅ Draft approved by @{user}\n"
        f"*Tone:* {draft.tone if draft else 'unknown'}\n"
        f"*Content:* {draft.content[:200] if draft else 'N/A'}..."
    )


async def handle_reject(ack: AsyncAck, body: dict, say: AsyncSay):
    await ack()

    action = body["actions"][0]
    draft_id = action["value"]
    user = body["user"]["username"]

    async with AsyncSessionLocal() as session:
        stmt = (
            update(Draft)
            .where(Draft.id == draft_id)
            .values(
                status="rejected",
                reviewed_at=datetime.now(timezone.utc),
                feedback=f"Rejected by {user}",
            )
        )
        await session.execute(stmt)
        await session.commit()

    logger.info("draft_rejected", draft_id=draft_id, user=user)
    await say(f"❌ Draft rejected by @{user}. Feedback will be used for improvement.")


async def handle_dismiss(ack: AsyncAck, body: dict, say: AsyncSay):
    await ack()

    action = body["actions"][0]
    alert_id = action["value"]
    user = body["user"]["username"]

    async with AsyncSessionLocal() as session:
        stmt = (
            update(Alert)
            .where(Alert.id == alert_id)
            .values(status="dismissed")
        )
        await session.execute(stmt)
        await session.commit()

    logger.info("alert_dismissed", alert_id=alert_id, user=user)
    await say(f"🔇 Alert dismissed by @{user}")


def register_handlers(app):
    @app.action(re.compile(r"^approve_draft_"))
    async def on_approve(ack, body, say):
        await handle_approve(ack, body, say)

    @app.action(re.compile(r"^reject_draft_"))
    async def on_reject(ack, body, say):
        await handle_reject(ack, body, say)

    @app.action(re.compile(r"^dismiss_alert_"))
    async def on_dismiss(ack, body, say):
        await handle_dismiss(ack, body, say)

    @app.action(re.compile(r"^edit_draft_"))
    async def on_edit(ack, body, say):
        await ack()
        draft_id = body["actions"][0]["value"]
        await say(
            f"✏️ To edit draft `{draft_id}`, "
            f"please reply with your feedback in thread."
        )

    @app.action(re.compile(r"^generate_draft_"))
    async def on_generate(ack, body, say):
        await ack()
        alert_id = body["actions"][0]["value"]
        await say(f"💬 Generating response drafts for alert `{alert_id}`...")

        from agents.drafting_agent import generate_drafts

        drafts = await generate_drafts({"id": alert_id, "alert_type": "manual"})
        for draft in drafts:
            await say(
                f"*[{draft['tone'].title()}]*\n{draft['content']}"
            )
