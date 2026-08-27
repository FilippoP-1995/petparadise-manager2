from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, require_role
from database import get_session
from domain.errors import InvalidTransitionError, NotFoundError, ValidationDomainError
from models.user import User, UserRole
from repositories.practice_repository import PracticeRepository
from schemas.practice import (
    CorrectionRequest,
    OverrideTotalRequest,
    PracticeCreate,
    PracticeRead,
    PracticeUpdate,
    TransitionRequest,
    TrashRequest,
)
from services import practice_service

router = APIRouter(prefix="/api/practices", tags=["practices"])


def _domain_error_to_http(exc: Exception):
    if isinstance(exc, ValidationDomainError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, InvalidTransitionError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("", response_model=list[PracticeRead])
async def list_practices(
    q: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    repo = PracticeRepository(db)
    practices = await repo.list_active(search=q, status=status_filter, limit=limit, offset=offset)
    return [PracticeRead.from_practice(p) for p in practices]


@router.get("/{practice_id}", response_model=PracticeRead)
async def get_practice(practice_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    repo = PracticeRepository(db)
    practice = await repo.get_by_id(practice_id)
    if practice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pratica non trovata")
    return PracticeRead.from_practice(practice)


@router.post("", response_model=PracticeRead, status_code=status.HTTP_201_CREATED)
async def create_practice(
    payload: PracticeCreate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        practice = await practice_service.create_practice(db, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.put("/{practice_id}", response_model=PracticeRead)
async def update_practice(
    practice_id: int,
    payload: PracticeUpdate,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        practice = await practice_service.update_practice(db, practice_id, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.post("/{practice_id}/transition", response_model=PracticeRead)
async def transition_practice(
    practice_id: int,
    payload: TransitionRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """doc14 §1 - workflow normale: Operator o Admin, nessun motivo."""
    try:
        practice = await practice_service.transition_practice_state(db, practice_id, payload, actor_user_id=user.id)
    except (ValidationDomainError, InvalidTransitionError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.post("/{practice_id}/correct-state", response_model=PracticeRead)
async def correct_practice_state(
    practice_id: int,
    payload: CorrectionRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.admin)),
):
    """doc14 §1 - correzione eccezionale: SOLO Admin, motivo obbligatorio."""
    try:
        practice = await practice_service.correct_practice_state(db, practice_id, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.post("/{practice_id}/trash", response_model=PracticeRead)
async def trash_practice(
    practice_id: int,
    payload: TrashRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        practice = await practice_service.trash_practice(db, practice_id, payload.reason, actor_user_id=user.id)
    except NotFoundError as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.post("/{practice_id}/restore", response_model=PracticeRead)
async def restore_practice(
    practice_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        practice = await practice_service.restore_practice(db, practice_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.post("/{practice_id}/override-total", response_model=PracticeRead)
async def override_total(
    practice_id: int,
    payload: OverrideTotalRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.admin)),
):
    """Release hardening: correzione manuale del totale - solo Admin, stessa
    barriera di correct-state (decisione esplicita del gate di rilascio)."""
    try:
        practice = await practice_service.set_total_override(db, practice_id, payload, actor_user_id=user.id)
    except NotFoundError as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.post("/{practice_id}/clear-total-override", response_model=PracticeRead)
async def clear_total_override(
    practice_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(require_role(UserRole.admin))
):
    """Release hardening: ripristino del calcolo automatico - solo Admin,
    stessa barriera dell'override che annulla."""
    try:
        practice = await practice_service.clear_total_override(db, practice_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.post("/{practice_id}/notify-owner", response_model=PracticeRead)
async def notify_owner(
    practice_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        practice = await practice_service.mark_owner_notified(db, practice_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)


@router.post("/{practice_id}/mark-collaborator-billed", response_model=PracticeRead)
async def mark_collaborator_billed(
    practice_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        practice = await practice_service.mark_collaborator_billed(db, practice_id, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc
    return PracticeRead.from_practice(practice)
