from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import DraftOut, DraftReviewIn
from db.engine import get_session
from db.models import Draft

router = APIRouter(prefix="/drafts", tags=["drafts"])


@router.get("", response_model=list[DraftOut])
async def list_drafts(
    status: str | None = None,
    alert_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Draft)

    if status:
        stmt = stmt.where(Draft.status == status)
    if alert_id:
        stmt = stmt.where(Draft.alert_id == alert_id)

    stmt = stmt.order_by(Draft.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/{draft_id}", response_model=DraftOut)
async def get_draft(
    draft_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Draft).where(Draft.id == draft_id))
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.post("/{draft_id}/review", response_model=DraftOut)
async def review_draft(
    draft_id: UUID,
    body: DraftReviewIn,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Draft).where(Draft.id == draft_id))
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    values = {
        "status": body.status,
        "reviewed_at": datetime.now(timezone.utc),
    }
    if body.feedback:
        values["feedback"] = body.feedback

    stmt = update(Draft).where(Draft.id == draft_id).values(**values)
    await session.execute(stmt)
    await session.commit()

    result = await session.execute(select(Draft).where(Draft.id == draft_id))
    return result.scalar_one()


@router.get("/stats/approval-rate")
async def approval_rate(
    session: AsyncSession = Depends(get_session),
):
    total_stmt = select(func.count(Draft.id)).where(Draft.status != "pending")
    total = (await session.execute(total_stmt)).scalar()

    approved_stmt = select(func.count(Draft.id)).where(Draft.status == "approved")
    approved = (await session.execute(approved_stmt)).scalar()

    rate = round(approved / total, 3) if total > 0 else None

    return {
        "total_reviewed": total,
        "approved": approved,
        "approval_rate": rate,
    }
