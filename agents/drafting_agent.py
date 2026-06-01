import json
import uuid

import structlog
from prometheus_client import Counter, Histogram
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from agents.llm_client import complete_text, loads_json_object
from db.engine import AsyncSessionLocal
from db.models import Draft

logger = structlog.get_logger()

DRAFTS_GENERATED = Counter("drafts_generated_total", "Total drafts generated")
DRAFT_LATENCY = Histogram("draft_generation_seconds", "Draft generation latency")

SYSTEM_PROMPT = """You are a community manager for a video game distributed on Steam.
Your task is to draft professional responses to community issues.

Guidelines:
- Be empathetic and acknowledge player frustrations
- Provide clear, actionable information when possible
- Maintain a professional yet approachable tone
- Never make promises about specific timelines unless confirmed
- Reference relevant patch notes or known issues when applicable
- Keep responses concise (2-4 sentences for concise, 3-5 for others)

You will be given context about the issue including:
- The alert details and representative posts
- Similar past issues and responses
- Recent patch notes

Generate a response draft in the specified tone."""


async def generate_drafts(
    alert_data: dict,
    context: dict | None = None,
) -> list[dict]:
    context_text = _build_context(alert_data, context)

    if not context_text.strip():
        logger.warning(
            "drafting_skipped_empty_context",
            alert_id=alert_data.get("id"),
            alert_type=alert_data.get("alert_type"),
        )
        return []

    tones = [
        ("official", "Write in a formal, official corporate communication style."),
        ("empathetic", "Write in a warm, empathetic tone that acknowledges player frustration."),
        ("concise", "Write a brief, to-the-point response. Maximum 2 sentences."),
    ]

    drafts = []
    for tone_name, tone_instruction in tones:
        try:
            with DRAFT_LATENCY.time():
                draft_content = await complete_text(
                    system=SYSTEM_PROMPT,
                    user=f"""Issue Context:
{context_text}

Tone: {tone_instruction}

Write a community response draft for this issue.
Return ONLY the response text, no JSON or formatting.""",
                    max_tokens=512,
                    temperature=0.4,
                )
        except Exception:
            logger.exception(
                "draft_generation_failed",
                alert_id=alert_data.get("id"),
                tone=tone_name,
            )
            continue

        if not draft_content or not draft_content.strip():
            logger.warning(
                "draft_generation_empty",
                alert_id=alert_data.get("id"),
                tone=tone_name,
            )
            continue

        DRAFTS_GENERATED.inc()
        drafts.append({"content": draft_content.strip(), "tone": tone_name})

    if not drafts:
        logger.warning("no_drafts_generated", alert_id=alert_data.get("id"))
        return []

    stored_drafts = await _store_drafts(alert_data.get("id"), drafts)
    logger.info("drafts_generated", count=len(stored_drafts), alert_id=alert_data.get("id"))
    return stored_drafts


DEFAULT_EVAL_SCORES = {"relevance": 0.0, "tone": 0.0, "accuracy": 0.0, "actionability": 0.0}


async def evaluate_draft(draft_content: str, issue_context: str) -> dict:
    if not draft_content or not draft_content.strip():
        logger.warning("eval_skipped_empty_draft")
        return dict(DEFAULT_EVAL_SCORES)

    if not issue_context or not issue_context.strip():
        logger.warning("eval_skipped_empty_context")
        return dict(DEFAULT_EVAL_SCORES)

    try:
        text = await complete_text(
            system="""You are an evaluation judge for community response drafts.
Rate the following draft on these criteria (0.0 to 1.0):
1. relevance: How well does the response address the specific issue?
2. tone: Is the tone appropriate for a gaming community manager?
3. accuracy: Are there any factual errors or misleading statements?
4. actionability: Does the response provide clear next steps or information?

Return ONLY a JSON object with these four scores.""",
            user=f"Issue: {issue_context}\n\nDraft Response: {draft_content}",
            max_tokens=256,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
    except Exception:
        logger.exception("eval_request_failed")
        return {"relevance": 0.5, "tone": 0.5, "accuracy": 0.5, "actionability": 0.5}

    try:
        scores = loads_json_object(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("eval_score_parse_failed", raw=(text or "")[:200])
        return {"relevance": 0.5, "tone": 0.5, "accuracy": 0.5, "actionability": 0.5}

    for key in ("relevance", "tone", "accuracy", "actionability"):
        if key in scores:
            try:
                scores[key] = max(0.0, min(1.0, float(scores[key])))
            except (TypeError, ValueError):
                scores[key] = 0.5

    return scores


async def store_eval_scores(draft_id: str, scores: dict):
    async with AsyncSessionLocal() as session:
        stmt = update(Draft).where(Draft.id == draft_id).values(eval_scores=scores)
        await session.execute(stmt)
        await session.commit()
    logger.info("eval_scores_stored", draft_id=draft_id, scores=scores)


def _build_context(alert_data: dict, context: dict | None) -> str:
    parts = []

    trigger = alert_data.get("trigger_data", {})
    if alert_data.get("alert_type") == "sentiment_drop":
        parts.append(
            f"Alert: Sentiment drop of {trigger.get('drop', 'N/A')} detected. "
            f"Current average: {trigger.get('recent_avg', 'N/A')}"
        )
    elif alert_data.get("alert_type") == "keyword_spike":
        parts.append(
            f"Alert: Spike in '{trigger.get('keyword', 'N/A')}' mentions "
            f"({trigger.get('multiplier', 'N/A')}x increase)"
        )

    representative = trigger.get("representative_posts", [])
    if representative:
        parts.append("\nRepresentative community posts:")
        for p in representative[:3]:
            parts.append(f"- {p.get('content', '')[:150]}")

    if context:
        if context.get("similar_issues"):
            parts.append("\nSimilar past community issues:")
            for issue in context["similar_issues"][:3]:
                sentiment = issue.get("sentiment", "N/A")
                tags = ", ".join(issue.get("issue_tags") or [])
                content = issue.get("content", "")[:200]
                parts.append(f"- [{sentiment}] ({tags}) {content}")

        if context.get("patch_notes"):
            parts.append("\nRecent patch notes:")
            for note in context["patch_notes"][:2]:
                title = note.get("title", "")
                content = note.get("content", "")[:300]
                parts.append(f"- {title}: {content}")

        if context.get("official_responses"):
            parts.append("\nPast approved responses for similar issues:")
            for resp in context["official_responses"][:3]:
                tag = resp.get("issue_tag", "")
                content = resp.get("content", "")[:200]
                parts.append(f"- [{tag}] {content}")

        trend = context.get("sentiment_trend") or []
        if trend:
            parts.append("\nRecent hourly sentiment trend (oldest → newest):")
            for row in trend[-6:]:
                hour = row.get("hour", "")
                avg = row.get("avg_sentiment")
                count = row.get("post_count")
                parts.append(f"- {hour}: avg={avg}, n={count}")

        history = context.get("alert_history") or []
        if history:
            parts.append("\nRecent prior alerts for context:")
            for past in history[:3]:
                parts.append(
                    f"- [{past.get('alert_type')}] severity={past.get('severity')} at {past.get('created_at')}"
                )

        complaints = (context.get("top_complaints") or {}).get("complaints") or []
        if complaints:
            parts.append("\nTop complaint topics in the window:")
            for c in complaints[:5]:
                parts.append(
                    f"- {c.get('issue_tag')}: {c.get('count')} posts, avg sentiment {c.get('avg_sentiment')}"
                )

        effectiveness = context.get("response_effectiveness")
        if effectiveness and effectiveness.get("verdict") not in (None, "no_official_response"):
            parts.append(
                "\nPrior response effectiveness — "
                f"tag={effectiveness.get('issue_tag')}, "
                f"shift={effectiveness.get('sentiment_shift')}, "
                f"verdict={effectiveness.get('verdict')}"
            )

    return "\n".join(parts)


async def _store_drafts(alert_id: str | None, drafts: list[dict]) -> list[dict]:
    stored = []
    async with AsyncSessionLocal() as session:
        for draft in drafts:
            draft_id = uuid.uuid4()
            stmt = insert(Draft).values(
                id=draft_id,
                alert_id=alert_id if alert_id else None,
                content=draft["content"],
                tone=draft["tone"],
                status="pending",
            )
            await session.execute(stmt)
            stored.append(
                {
                    "id": str(draft_id),
                    "alert_id": str(alert_id) if alert_id else None,
                    "content": draft["content"],
                    "tone": draft["tone"],
                    "status": "pending",
                }
            )
        await session.commit()

    return stored
