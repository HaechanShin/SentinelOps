import asyncio
from datetime import datetime, timezone

import praw
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from config import settings
from db.engine import AsyncSessionLocal
from db.models import Post

logger = structlog.get_logger()

SUBREDDIT = "PUBATTLEGROUNDS"


def create_reddit_client() -> praw.Reddit:
    return praw.Reddit(
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
    )


async def collect_reddit_posts(limit: int = 50) -> list[dict]:
    reddit = create_reddit_client()
    subreddit = reddit.subreddit(SUBREDDIT)

    posts = []
    for submission in subreddit.new(limit=limit):
        post_data = {
            "source": "reddit",
            "external_id": f"reddit_{submission.id}",
            "title": submission.title,
            "content": f"{submission.title}\n\n{submission.selftext}" if submission.selftext else submission.title,
            "author": str(submission.author) if submission.author else "[deleted]",
            "url": f"https://reddit.com{submission.permalink}",
            "created_at": datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
        }
        posts.append(post_data)

    logger.info("reddit_posts_collected", count=len(posts), subreddit=SUBREDDIT)
    return posts


async def collect_reddit_comments(limit: int = 100) -> list[dict]:
    reddit = create_reddit_client()
    subreddit = reddit.subreddit(SUBREDDIT)

    comments = []
    for comment in subreddit.comments(limit=limit):
        comment_data = {
            "source": "reddit",
            "external_id": f"reddit_comment_{comment.id}",
            "title": None,
            "content": comment.body,
            "author": str(comment.author) if comment.author else "[deleted]",
            "url": f"https://reddit.com{comment.permalink}",
            "created_at": datetime.fromtimestamp(comment.created_utc, tz=timezone.utc),
        }
        comments.append(comment_data)

    logger.info("reddit_comments_collected", count=len(comments))
    return comments


async def store_posts(posts: list[dict]) -> int:
    if not posts:
        return 0

    stored = 0
    async with AsyncSessionLocal() as session:
        for post_data in posts:
            stmt = insert(Post).values(**post_data).on_conflict_do_nothing(
                index_elements=["external_id"]
            )
            result = await session.execute(stmt)
            if result.rowcount > 0:
                stored += 1
        await session.commit()

    logger.info("posts_stored", new_count=stored, total_count=len(posts))
    return stored


async def run_reddit_collection():
    posts = await collect_reddit_posts()
    comments = await collect_reddit_comments()
    all_items = posts + comments
    stored = await store_posts(all_items)
    return {"collected": len(all_items), "stored": stored}
