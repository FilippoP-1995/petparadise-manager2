from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, require_role
from database import get_session
from domain.errors import NotFoundError, ValidationDomainError
from models.user import User, UserRole
from repositories.client_repository import ClientRepository
from schemas.client import ClientCreate, ClientRead, ClientUpdate
from services import client_service

router = APIRouter(prefix="/api/clients", tags=["clients"])


@router.get("", response_model=list[ClientRead])
async def list_clients(
    q: str | None = Query(default=None, description="Ricerca per nome, ragione sociale, telefono o email"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    repo = ClientRepository(db)
    return await repo.list_active(search=q, limit=limit, offset=offset)


@router.get("/{client_id}", response_model=ClientRead)
async def get_client(client_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    repo = ClientRepository(db)
    client = await repo.get_by_id(client_id)
    if client is None or not client.active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente non trovato")
    return client


@router.post("", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await client_service.create_client(db, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.put("/{client_id}", response_model=ClientRead)
async def update_client(
    client_id: int,
    payload: ClientUpdate,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return await client_service.update_client(db, client_id, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{client_id}/disattiva", response_model=ClientRead)
async def deactivate_client(
    client_id: int,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.admin)),
):
    try:
        return await client_service.deactivate_client(db, client_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
