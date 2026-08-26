from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, require_role
from database import get_session
from domain.errors import NotFoundError, ValidationDomainError
from models.user import User, UserRole
from repositories.reference_repositories import CompanyLocationRepository
from schemas.company_location import CompanyLocationCreate, CompanyLocationRead, CompanyLocationUpdate
from services import company_location_service

router = APIRouter(prefix="/api/company-locations", tags=["company-locations"])


@router.get("", response_model=list[CompanyLocationRead])
async def list_locations(db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    """Vista di gestione (include anche le sedi disattivate) - distinta da
    GET /api/references/company-locations, che resta il picker minimale
    (id+name, solo attive) gia' usato da Pratiche/Ritiri/Cicli."""
    return await CompanyLocationRepository(db).list_all()


@router.get("/{location_id}", response_model=CompanyLocationRead)
async def get_location(location_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    location = await CompanyLocationRepository(db).get_by_id(location_id)
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sede non trovata")
    return location


@router.post("", response_model=CompanyLocationRead, status_code=status.HTTP_201_CREATED)
async def create_location(
    payload: CompanyLocationCreate,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.admin)),
):
    try:
        return await company_location_service.create_location(db, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.put("/{location_id}", response_model=CompanyLocationRead)
async def update_location(
    location_id: int,
    payload: CompanyLocationUpdate,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.admin)),
):
    try:
        return await company_location_service.update_location(db, location_id, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{location_id}/disattiva", response_model=CompanyLocationRead)
async def deactivate_location(
    location_id: int,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.admin)),
):
    try:
        return await company_location_service.deactivate_location(db, location_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
