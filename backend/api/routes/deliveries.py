from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from database import get_session
from domain.errors import NotFoundError, ValidationDomainError
from models.calendar_event import CalendarEventType
from models.user import User
from repositories.calendar_event_repository import CalendarEventRepository
from schemas.calendar_event import DeliveryCreate, DeliveryRead, DeliveryUpdate, LinkDeliveryToPracticeRequest
from services import delivery_service

router = APIRouter(prefix="/api/deliveries", tags=["deliveries"])


def _domain_error_to_http(exc: Exception):
    if isinstance(exc, ValidationDomainError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("", response_model=list[DeliveryRead])
async def list_deliveries(
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    repo = CalendarEventRepository(db)
    return await repo.list_active(
        event_type=CalendarEventType.riconsegna, search=q, pickup_status=None, limit=limit, offset=offset
    )


@router.get("/{delivery_id}", response_model=DeliveryRead)
async def get_delivery(delivery_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    repo = CalendarEventRepository(db)
    delivery = await repo.get_by_id(delivery_id)
    if delivery is None or delivery.event_type != CalendarEventType.riconsegna:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Riconsegna non trovata")
    return delivery


@router.post("", response_model=DeliveryRead, status_code=status.HTTP_201_CREATED)
async def create_delivery(
    payload: DeliveryCreate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await delivery_service.create_delivery(db, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.put("/{delivery_id}", response_model=DeliveryRead)
async def update_delivery(
    delivery_id: int, payload: DeliveryUpdate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await delivery_service.update_delivery(db, delivery_id, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{delivery_id}/link-practice", response_model=DeliveryRead)
async def link_delivery_to_practice(
    delivery_id: int,
    payload: LinkDeliveryToPracticeRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return await delivery_service.link_delivery_to_practice(db, delivery_id, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{delivery_id}/trash", response_model=DeliveryRead)
async def trash_delivery(delivery_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        return await delivery_service.trash_delivery(db, delivery_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{delivery_id}/restore", response_model=DeliveryRead)
async def restore_delivery(delivery_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        return await delivery_service.restore_delivery(db, delivery_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise _domain_error_to_http(exc) from exc
