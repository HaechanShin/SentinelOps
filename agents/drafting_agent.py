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

SYSTEM_PROMPT = """You are a community manager for PUBG (PlayerUnknown's Battlegrounds) by KRAFTON.
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

    tones = [
        ("official", "Write in a formal, official corporate communication style."),
        ("empathetic", "Write in a warm, empathetic tone that acknowledges player frustration."),
        ("concise", "Write a brief, to-the-point response. Maximum 2 sentences."),
    ]

    drafts = []
    for tone_name, tone_instruction in tones:
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

        DRAFTS_GENERATED.inc()

        drafts.append({"content": draft_content, "tone": tone_name})

    stored_drafts = await _store_drafts(alert_data.get("id"), drafts)
    logger.info("drafts_generated", count=len(stored_drafts), alert_id=alert_data.get("id"))
    return stored_drafts


async def evaluate_draft(draft_content: str, issue_context: str) -> dict:
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

    try:
        scores = loads_json_object(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("eval_score_parse_failed", raw=text[:200])
        return {"relevance": 0.5, "tone": 0.5, "accuracy": 0.5, "actionability": 0.5}

    for key in ("relevance", "tone", "accuracy", "actionability"):
        if key in scores:
            scores[key] = max(0.0, min(1.0, float(scores[key])))

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
