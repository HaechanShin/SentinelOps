import structlog
from slack_sdk.web.async_client import AsyncWebClient

from config import settings

logger = structlog.get_logger()


def _severity_emoji(severity: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")


def _build_context_summary_block(context: dict | None) -> dict | None:
    if not context:
        return None

    lines: list[str] = []

    trend = context.get("sentiment_trend") or []
    if trend:
        recent = trend[-1]
        avg = recent.get("avg_sentiment")
        count = recent.get("post_count")
        lines.append(f"• Latest hour sentiment: *{avg}* (n={count})")

    history = context.get("alert_history") or []
    if history:
        lines.append(f"• Prior alerts in window: *{len(history)}*")

    complaints = (context.get("top_complaints") or {}).get("complaints") or []
    if complaints:
        top = ", ".join(
            f"{c.get('issue_tag')}({c.get('count')})" for c in complaints[:3]
        )
        lines.append(f"• Top complaint topics: {top}")

    similar = context.get("similar_issues") or []
    if similar:
        lines.append(f"• Similar past issues found: *{len(similar)}*")

    responses = context.get("official_responses") or []
    if responses:
        lines.append(f"• Approved past responses referenced: *{len(responses)}*")

    effectiveness = context.get("response_effectiveness")
    if effectiveness and effectiveness.get("verdict") not in (None, "no_official_response"):
        verdict = effectiveness.get("verdict")
        shift = effectiveness.get("sentiment_shift")
        lines.append(f"• Prior response shift for this tag: *{shift}* → _{verdict}_")

    patches = context.get("patch_notes") or []
    if patches:
        titles = "; ".join((p.get("title") or "")[:60] for p in patches[:2])
        lines.append(f"• Recent patch notes: {titles}")

    if not lines:
        return None

    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*MCP context summary:*\n" + "\n".join(lines)},
    }


def build_alert_blocks(
    alert: dict,
    drafts: list[dict] | None = None,
    context: dict | None = None,
) -> list[dict]:
    trigger = alert.get("trigger_data", {})
    severity = alert.get("severity", "unknown")
    alert_type = alert.get("alert_type", "unknown")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🚨 Community Alert — {alert_type.replace('_', ' ').title()}",
            },
        },
        {"type": "divider"},
    ]

    if alert_type == "sentiment_drop":
        fields = [
            f"*Severity:* {_severity_emoji(severity)} {severity.upper()}",
            f"*Sentiment Drop:* {trigger.get('drop', 'N/A')}",
            f"*Current Avg:* {trigger.get('recent_avg', 'N/A')}",
            f"*Previous Avg:* {trigger.get('earlier_avg', 'N/A')}",
        ]
    elif alert_type == "keyword_spike":
        fields = [
            f"*Severity:* {_severity_emoji(severity)} {severity.upper()}",
            f"*Keyword:* `{trigger.get('keyword', 'N/A')}`",
            f"*Increase:* {trigger.get('multiplier', 'N/A')}x",
            f"*Recent Count:* {trigger.get('recent_count', 'N/A')}",
        ]
    else:
        fields = [f"*Severity:* {_severity_emoji(severity)} {severity.upper()}"]

    blocks.append(
        {
            "type": "section",
            "fields": [{"type": "mrkdwn", "text": f} for f in fields],
        }
    )

    representative = trigger.get("representative_posts", [])
    if representative:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Representative Posts:*",
                },
            }
        )
        for post in representative[:3]:
            content = post.get("content", "")[:150]
            url = post.get("url", "")
            sentiment = post.get("sentiment", "N/A")
            text = f"• ({sentiment}) {content}"
            if url:
                text += f" <{url}|[link]>"
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": text}}
            )

    context_block = _build_context_summary_block(context)
    if context_block:
        blocks.append({"type": "divider"})
        blocks.append(context_block)

    if drafts:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{len(drafts)} response draft(s) available*",
                },
            }
        )
        for draft in drafts[:3]:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*[{draft.get('tone', 'unknown').title()}]*\n{draft['content'][:300]}",
                    },
                }
            )
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ Approve"},
                            "style": "primary",
                            "action_id": f"approve_draft_{draft['id']}",
                            "value": draft["id"],
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✏️ Edit"},
                            "action_id": f"edit_draft_{draft['id']}",
                            "value": draft["id"],
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "❌ Reject"},
                            "style": "danger",
                            "action_id": f"reject_draft_{draft['id']}",
                            "value": draft["id"],
                        },
                    ],
                }
            )

    if not drafts:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "💬 Generate Response"},
                        "style": "primary",
                        "action_id": f"generate_draft_{alert.get('id', '')}",
                        "value": alert.get("id", ""),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🔇 Dismiss"},
                        "action_id": f"dismiss_alert_{alert.get('id', '')}",
                        "value": alert.get("id", ""),
                    },
                ],
            }
        )

    return blocks


async def send_alert(
    client: AsyncWebClient,
    alert: dict,
    drafts: list[dict] | None = None,
    context: dict | None = None,
) -> str | None:
    blocks = build_alert_blocks(alert, drafts, context=context)

    try:
        result = await client.chat_postMessage(
            channel=settings.slack_alert_channel,
            text=f"🚨 Community Alert: {alert.get('alert_type', 'unknown')}",
            blocks=blocks,
        )
        ts = result.get("ts")
        logger.info("slack_alert_sent", alert_id=alert.get("id"), ts=ts)
        return ts
    except Exception:
        logger.exception("slack_alert_send_failed", alert_id=alert.get("id"))
        return None
