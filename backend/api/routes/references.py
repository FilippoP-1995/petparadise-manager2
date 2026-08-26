"""Endpoint di sola lettura per le tabelle di riferimento richieste dal
form Pratica (doc06 'tabelle che restano concettualmente invariate') -
popolano le select della UI, nessuna logica di dominio qui."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from database import get_session
from models.user import User
from repositories.reference_repositories import (
    CalendarZoneRepository,
    CollaboratorRepository,
    CompanyLocationRepository,
    TagRepository,
    UrnRepository,
)

router = APIRouter(prefix="/api/references", tags=["references"])


class _NamedRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    label: str
    category: str | None


@router.get("/company-locations", response_model=list[_NamedRead])
async def list_company_locations(db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return await CompanyLocationRepository(db).list_active()


@router.get("/collaborators", response_model=list[_NamedRead])
async def list_collaborators(db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return await CollaboratorRepository(db).list_active()


@router.get("/urns", response_model=list[_NamedRead])
async def list_urns(db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return await UrnRepository(db).list_active()


@router.get("/calendar-zones", response_model=list[_NamedRead])
async def list_calendar_zones(db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return await CalendarZoneRepository(db).list_all()


@router.get("/tags", response_model=list[TagRead])
async def list_tags(db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return await TagRepository(db).list_all()
