from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import AlertOut, AlertUpdateIn
from db.engine import get_session
from db.models import Alert

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    status: str | None = None,
    alert_type: str | None = None,
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = select(Alert).where(Alert.created_at >= since)

    if status:
        stmt = stmt.where(Alert.status == status)
    if alert_type:
        stmt = stmt.where(Alert.alert_type == alert_type)

    stmt = stmt.order_by(Alert.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(
    alert_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.patch("/{alert_id}", response_model=AlertOut)
async def update_alert(
    alert_id: UUID,
    body: AlertUpdateIn,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    stmt = update(Alert).where(Alert.id == alert_id).values(status=body.status)
    await session.execute(stmt)
    await session.commit()

    result = await session.execute(select(Alert).where(Alert.id == alert_id))
    return result.scalar_one()


@router.get("/stats/summary")
async def alert_stats(
    hours: int = Query(default=24, ge=1, le=168),
    session: AsyncSession = Depends(get_session),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    total_stmt = select(func.count(Alert.id)).where(Alert.created_at >= since)
    total = (await session.execute(total_stmt)).scalar()

    by_type_stmt = (
        select(Alert.alert_type, func.count(Alert.id))
        .where(Alert.created_at >= since)
        .group_by(Alert.alert_type)
    )
    by_type = {row[0]: row[1] for row in (await session.execute(by_type_stmt))}

    by_severity_stmt = (
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.created_at >= since)
        .group_by(Alert.severity)
    )
    by_severity = {row[0]: row[1] for row in (await session.execute(by_severity_stmt))}

    return {
        "period_hours": hours,
        "total": total,
        "by_type": by_type,
        "by_severity": by_severity,
    }
