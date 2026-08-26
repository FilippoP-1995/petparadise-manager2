from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, require_role
from database import get_session
from domain.errors import NotFoundError, ValidationDomainError
from models.user import User, UserRole
from repositories.veterinarian_repository import VeterinarianRepository
from schemas.veterinarian import VeterinarianCreate, VeterinarianRead, VeterinarianUpdate
from services import veterinarian_service

router = APIRouter(prefix="/api/veterinarians", tags=["veterinarians"])


@router.get("", response_model=list[VeterinarianRead])
async def list_veterinarians(
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    repo = VeterinarianRepository(db)
    return await repo.list_active(search=q, limit=limit, offset=offset)


@router.get("/{veterinarian_id}", response_model=VeterinarianRead)
async def get_veterinarian(
    veterinarian_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)
):
    repo = VeterinarianRepository(db)
    veterinarian = await repo.get_by_id(veterinarian_id)
    if veterinarian is None or not veterinarian.active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Veterinario non trovato")
    return veterinarian


@router.post("", response_model=VeterinarianRead, status_code=status.HTTP_201_CREATED)
async def create_veterinarian(
    payload: VeterinarianCreate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await veterinarian_service.create_veterinarian(db, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.put("/{veterinarian_id}", response_model=VeterinarianRead)
async def update_veterinarian(
    veterinarian_id: int,
    payload: VeterinarianUpdate,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return await veterinarian_service.update_veterinarian(db, veterinarian_id, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{veterinarian_id}/disattiva", response_model=VeterinarianRead)
async def deactivate_veterinarian(
    veterinarian_id: int,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.admin)),
):
    try:
        return await veterinarian_service.deactivate_veterinarian(db, veterinarian_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
