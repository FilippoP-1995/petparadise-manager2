from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import SESSION_COOKIE_NAME, get_current_user
from auth.sessions import delete_session
from config import settings
from database import get_session
from domain.errors import RateLimitedError, ValidationDomainError
from models.user import User, UserRole
from services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    user_id: int
    display_name: str
    role: UserRole


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    try:
        session = await auth_service.login(
            db,
            username=payload.username,
            password=payload.password,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except RateLimitedError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.id,
        httponly=True,
        samesite="lax",
        secure=settings.environment != "development",
        max_age=None,
    )
    user = await db.get(User, session.user_id)
    return LoginResponse(user_id=user.id, display_name=user.display_name, role=user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    """Release hardening: invalida la sessione lato server (non solo il
    cookie) - un riutilizzo del token dopo logout deve essere rifiutato
    come una sessione qualsiasi scaduta/inesistente."""
    if session_token is not None:
        await delete_session(db, session_token)
    response.delete_cookie(SESSION_COOKIE_NAME)


@router.get("/me", response_model=LoginResponse)
async def me(user: User = Depends(get_current_user)):
    return LoginResponse(user_id=user.id, display_name=user.display_name, role=user.role)
