import html
import re
from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy.dialects.postgresql import insert

from config import settings
from db.engine import AsyncSessionLocal
from db.models import PatchNote

logger = structlog.get_logger()

STEAM_NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"

PATCH_PATTERN = re.compile(
    r"\b(update|patch\s*note|hotfix|maintenance)\b", re.IGNORECASE
)
VERSION_PATTERN = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _strip_markup(text: str) -> str:
    text = re.sub(r"\{STEAM_CLAN_IMAGE\}[^\s]*", "", text)
    text = re.sub(r"\[img\][^\[]*\[/img\]", "", text)
    text = re.sub(r"\[/?[^\]]+\]", "", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_version(title: str, gid: str) -> str:
    match = VERSION_PATTERN.search(title)
    if match:
        return match.group(1)
    return f"news-{gid}"


async def collect_patch_notes(count: int = 50) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        params = {
            "appid": settings.steam_app_id,
            "count": count,
            "maxlength": 0,
            "feeds": "steam_community_announcements",
            "format": "json",
        }
        resp = await client.get(STEAM_NEWS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    news_items = data.get("appnews", {}).get("newsitems", [])

    patch_notes = []
    for item in news_items:
        title = item.get("title", "")
        if not PATCH_PATTERN.search(title):
            continue

        gid = str(item.get("gid", ""))
        patch_notes.append({
            "gid": gid,
            "version": _extract_version(title, gid),
            "title": title,
            "content": _strip_markup(item.get("contents", "")),
            "published_at": datetime.fromtimestamp(
                item.get("date", 0), tz=timezone.utc
            ),
        })

    logger.info(
        "patch_notes_collected",
        count=len(patch_notes),
        total_news=len(news_items),
    )
    return patch_notes


async def store_patch_notes(notes: list[dict]) -> int:
    if not notes:
        return 0

    stored = 0
    async with AsyncSessionLocal() as session:
        for note in notes:
            stmt = (
                insert(PatchNote)
                .values(**note)
                .on_conflict_do_nothing(index_elements=["gid"])
            )
            result = await session.execute(stmt)
            if result.rowcount > 0:
                stored += 1
        await session.commit()

    logger.info("patch_notes_stored", new_count=stored, total_count=len(notes))
    return stored


async def run_news_collection() -> dict:
    notes = await collect_patch_notes()
    stored = await store_patch_notes(notes)
    return {"collected": len(notes), "stored": stored}
