from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from database import get_session
from domain.errors import InvalidTransitionError, NotFoundError, ValidationDomainError
from models.calendar_event import CalendarEventType
from models.user import User
from repositories.calendar_event_repository import CalendarEventRepository
from schemas.calendar_event import (
    CancelPickupAndTrashPracticeRequest,
    CancelPickupRequest,
    CreatePracticeFromPickupRequest,
    PickupCreate,
    PickupRead,
    PickupUpdate,
    TransitionPickupRequest,
)
from schemas.practice import PracticeRead
from services import pickup_service

router = APIRouter(prefix="/api/pickups", tags=["pickups"])


def _domain_error_to_http(exc: Exception):
    if isinstance(exc, ValidationDomainError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, InvalidTransitionError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("", response_model=list[PickupRead])
async def list_pickups(
    q: str | None = Query(default=None),
    pickup_status: str | None = Query(default=None, alias="status"),
    date_from: datetime | None = Query(default=None, description="Filtra i ritiri con inizio >= a questo istante"),
    date_to: datetime | None = Query(default=None, description="Filtra i ritiri con inizio < a questo istante"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    repo = CalendarEventRepository(db)
    return await repo.list_active(
        event_type=CalendarEventType.ritiro,
        search=q,
        pickup_status=pickup_status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/{pickup_id}", response_model=PickupRead)
async def get_pickup(pickup_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    repo = CalendarEventRepository(db)
    pickup = await repo.get_by_id(pickup_id)
    if pickup is None or pickup.event_type != CalendarEventType.ritiro:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ritiro non trovato")
    return pickup


@router.post("", response_model=PickupRead, status_code=status.HTTP_201_CREATED)
async def create_pickup(
    payload: PickupCreate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await pickup_service.create_pickup(db, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.put("/{pickup_id}", response_model=PickupRead)
async def update_pickup(
    pickup_id: int, payload: PickupUpdate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await pickup_service.update_pickup(db, pickup_id, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{pickup_id}/transition", response_model=PickupRead)
async def transition_pickup(
    pickup_id: int,
    payload: TransitionPickupRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """doc14 §2 - Operator o Admin indistintamente."""
    try:
        return await pickup_service.transition_pickup(db, pickup_id, payload.target_status, actor_user_id=user.id)
    except (ValidationDomainError, InvalidTransitionError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{pickup_id}/cancel", response_model=PickupRead)
async def cancel_pickup(
    pickup_id: int,
    payload: CancelPickupRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Azione A (sezione 6): annullamento normale, la pratica collegata
    (se esiste) non viene toccata - il campo linked_practice_id nella
    risposta permette al frontend di mostrare l'avviso."""
    try:
        return await pickup_service.cancel_pickup(db, pickup_id, payload.reason, actor_user_id=user.id)
    except (ValidationDomainError, InvalidTransitionError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{pickup_id}/cancel-and-trash-practice", response_model=PickupRead)
async def cancel_pickup_and_trash_practice(
    pickup_id: int,
    payload: CancelPickupAndTrashPracticeRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Azione B (sezione 6): distinta da A, conferma esplicita separata
    (motivo obbligatorio) - Operator o Admin, non riservata all'Admin
    (sezione 7)."""
    try:
        return await pickup_service.cancel_pickup_and_trash_practice(db, pickup_id, payload.reason, actor_user_id=user.id)
    except (ValidationDomainError, InvalidTransitionError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{pickup_id}/create-practice", response_model=PracticeRead, status_code=status.HTTP_201_CREATED)
async def create_practice_from_pickup(
    pickup_id: int,
    payload: CreatePracticeFromPickupRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Percorso A - vedi services/pickup_service.py e
    services/practice_service.py per il dettaglio del lock/transazione."""
    try:
        practice = await pickup_service.create_practice_from_pickup_action(db, pickup_id, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.post("/{pickup_id}/trash", response_model=PickupRead)
async def trash_pickup(pickup_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        return await pickup_service.trash_pickup(db, pickup_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{pickup_id}/restore", response_model=PickupRead)
async def restore_pickup(pickup_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        return await pickup_service.restore_pickup(db, pickup_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise _domain_error_to_http(exc) from exc
