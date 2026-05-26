from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PostOut(BaseModel):
    id: UUID
    source: str
    external_id: str
    title: str | None
    content: str
    author: str | None
    url: str | None
    recommended: bool | None
    sentiment: float | None
    issue_tags: list[str] | None
    created_at: datetime
    analyzed_at: datetime | None

    model_config = {"from_attributes": True}


class AlertOut(BaseModel):
    id: UUID
    alert_type: str
    severity: str
    trigger_data: dict | None
    related_post_ids: list[str] | None
    slack_ts: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertUpdateIn(BaseModel):
    status: str = Field(pattern="^(open|acknowledged|resolved)$")


class DraftOut(BaseModel):
    id: UUID
    alert_id: UUID | None
    content: str
    tone: str | None
    status: str
    feedback: str | None
    eval_scores: dict | None
    created_at: datetime
    reviewed_at: datetime | None

    model_config = {"from_attributes": True}


class DraftReviewIn(BaseModel):
    status: str = Field(pattern="^(approved|rejected|edited)$")
    feedback: str | None = None


class SentimentTrendPoint(BaseModel):
    hour: datetime
    avg_sentiment: float
    post_count: int


class DashboardSummary(BaseModel):
    total_posts: int
    average_sentiment: float | None
    posts_by_source: dict[str, int]
    alerts_open: int
    alerts_total_24h: int
    drafts_pending: int
    approval_rate: float | None


class PipelineRunResult(BaseModel):
    sentiments_analyzed: int
    alerts_triggered: int
    drafts_generated: int
