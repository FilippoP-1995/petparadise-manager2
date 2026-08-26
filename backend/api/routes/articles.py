from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from database import get_session
from domain.errors import NotFoundError, ValidationDomainError
from models.user import User
from repositories.article_repository import ArticleRepository
from schemas.article import ArticleOrderRead, ArticleRead
from services import article_service

router = APIRouter(prefix="/api/articles", tags=["articles"])


@router.get("", response_model=list[ArticleRead])
async def list_articles(db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return await ArticleRepository(db).list_active()


@router.get("/orders/recent", response_model=list[ArticleOrderRead])
async def list_recent_orders(
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    return await article_service.list_recent_orders(db, limit=limit)


@router.post("/{article_id}/ordina", response_model=ArticleOrderRead, status_code=status.HTTP_201_CREATED)
async def order_article(article_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        await article_service.order_article(db, article_id, actor_user_id=user.id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    recent = await article_service.list_recent_orders(db, limit=1)
    return recent[0]
