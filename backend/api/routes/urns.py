from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from database import get_session
from domain.errors import NotFoundError, ValidationDomainError
from models.urn import UrnCategory
from models.user import User
from repositories.urn_repository import UrnCatalogRepository, UrnMovementRepository
from schemas.urn import UrnCreate, UrnMovementRead, UrnRead, UrnUpdate
from services import urn_service

router = APIRouter(prefix="/api/urns", tags=["urns"])


@router.get("", response_model=list[UrnRead])
async def list_urns(
    category: UrnCategory | None = Query(default=None),
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    return await UrnCatalogRepository(db).list_all(category=category, active_only=active_only)


@router.get("/{urn_id}", response_model=UrnRead)
async def get_urn(urn_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    urn = await UrnCatalogRepository(db).get_by_id(urn_id)
    if urn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Urna non trovata")
    return urn


@router.get("/{urn_id}/movements", response_model=list[UrnMovementRead])
async def list_urn_movements(urn_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return await UrnMovementRepository(db).list_for_urn(urn_id)


@router.post("", response_model=UrnRead, status_code=status.HTTP_201_CREATED)
async def create_urn(payload: UrnCreate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        return await urn_service.create_urn(db, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.put("/{urn_id}", response_model=UrnRead)
async def update_urn(
    urn_id: int, payload: UrnUpdate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await urn_service.update_urn(db, urn_id, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{urn_id}/disattiva", response_model=UrnRead)
async def deactivate_urn(urn_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        return await urn_service.deactivate_urn(db, urn_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
