import re
from collections import Counter
from datetime import datetime, timezone

import structlog
from slack_bolt.async_app import AsyncAck, AsyncSay
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from db.engine import AsyncSessionLocal
from db.models import Alert, Draft, OfficialResponse

logger = structlog.get_logger()


def _extract_primary_tag(alert: Alert) -> str | None:
    trigger = alert.trigger_data or {}
    if alert.alert_type == "keyword_spike":
        return trigger.get("keyword")
    posts = trigger.get("representative_posts", [])
    tags = []
    for p in posts:
        for t in (p.get("issue_tags") or []):
            tags.append(t)
    if tags:
        return Counter(tags).most_common(1)[0][0]
    return None


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

        if draft and draft.alert_id:
            alert_result = await session.execute(
                select(Alert).where(Alert.id == draft.alert_id)
            )
            alert = alert_result.scalar_one_or_none()
            if alert:
                issue_tag = _extract_primary_tag(alert)
                if issue_tag:
                    insert_stmt = insert(OfficialResponse).values(
                        issue_tag=issue_tag,
                        content=draft.content,
                        source=f"approved_by_{user}",
                    )
                    await session.execute(insert_stmt)
                    await session.commit()
                    logger.info(
                        "official_response_accumulated",
                        draft_id=draft_id,
                        issue_tag=issue_tag,
                    )

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

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Alert).where(Alert.id == alert_id))
            alert = result.scalar_one_or_none()

        if not alert:
            await say(f"⚠️ Alert `{alert_id}` not found.")
            return

        alert_data = {
            "id": str(alert.id),
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "trigger_data": alert.trigger_data or {},
        }
        drafts = await generate_drafts(alert_data)
        if not drafts:
            await say(
                f"⚠️ No drafts could be generated for alert `{alert_id}` — "
                "the alert has no review context to draft from."
            )
            return
        for draft in drafts:
            await say(
                f"*[{draft['tone'].title()}]*\n{draft['content']}"
            )
