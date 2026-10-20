from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from database import get_session
from models.user import User
from schemas.dashboard import DashboardSummaryRead
from services.dashboard_service import get_dashboard_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryRead)
async def dashboard_summary(
    period: str = Query("oggi", pattern="^(oggi|settimana|mese)$"),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    return await get_dashboard_summary(db, period)
