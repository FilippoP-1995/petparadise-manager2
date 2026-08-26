from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from database import get_session
from domain.errors import InvalidTransitionError, NotFoundError, ValidationDomainError
from models.user import User
from repositories.cremation_cycle_repository import AnimalCycleRepository, CremationCycleRepository
from schemas.cremation_cycle import (
    AssignAnimalRequest,
    CremationCycleCreate,
    CremationCycleRead,
    CremationCycleUpdate,
    CycleAnimalRead,
    RemoveAnimalRequest,
    RevertCycleRequest,
)
from services import cremation_cycle_service

router = APIRouter(prefix="/api/cremation-cycles", tags=["cremation-cycles"])


@router.get("/eligible-animals", response_model=list[CycleAnimalRead])
async def list_eligible_animals(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    return await AnimalCycleRepository(db).list_unassigned_eligible(limit=limit)


def _domain_error_to_http(exc: Exception):
    if isinstance(exc, ValidationDomainError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, InvalidTransitionError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("", response_model=list[CremationCycleRead])
async def list_cycles(
    status_filter: str | None = Query(default=None, alias="status"),
    cycle_date: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    repo = CremationCycleRepository(db)
    return await repo.list_active(status=status_filter, cycle_date=cycle_date, limit=limit, offset=offset)


@router.get("/{cycle_id}", response_model=CremationCycleRead)
async def get_cycle(cycle_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    repo = CremationCycleRepository(db)
    cycle = await repo.get_by_id(cycle_id)
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ciclo non trovato")
    return cycle


@router.post("", response_model=CremationCycleRead, status_code=status.HTTP_201_CREATED)
async def create_cycle(
    payload: CremationCycleCreate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    return await cremation_cycle_service.create_cycle(db, payload, actor_user_id=user.id)


@router.put("/{cycle_id}", response_model=CremationCycleRead)
async def update_cycle(
    cycle_id: int,
    payload: CremationCycleUpdate,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return await cremation_cycle_service.update_cycle(db, cycle_id, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.delete("/{cycle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cycle(cycle_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        await cremation_cycle_service.delete_cycle(db, cycle_id, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{cycle_id}/assign-animal", response_model=CremationCycleRead)
async def assign_animal(
    cycle_id: int, payload: AssignAnimalRequest, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await cremation_cycle_service.assign_animal(db, cycle_id, payload.animal_id, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{cycle_id}/remove-animal", response_model=CremationCycleRead)
async def remove_animal(
    cycle_id: int, payload: RemoveAnimalRequest, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await cremation_cycle_service.remove_animal(db, cycle_id, payload.animal_id, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{cycle_id}/complete", response_model=CremationCycleRead)
async def complete_cycle(cycle_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        return await cremation_cycle_service.complete_cycle(db, cycle_id, actor_user_id=user.id)
    except (ValidationDomainError, InvalidTransitionError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{cycle_id}/revert", response_model=CremationCycleRead)
async def revert_cycle(
    cycle_id: int, payload: RevertCycleRequest, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await cremation_cycle_service.revert_cycle(db, cycle_id, payload.reason, actor_user_id=user.id)
    except (ValidationDomainError, InvalidTransitionError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc
