from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.article import Article, ArticleOrder
from models.user import User


class ArticleRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, article_id: int) -> Article | None:
        return await self._session.get(Article, article_id)

    async def list_active(self) -> list[Article]:
        stmt = select(Article).where(Article.active.is_(True)).order_by(Article.name)
        return list((await self._session.execute(stmt)).scalars().all())


class ArticleOrderRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    def add(self, order: ArticleOrder) -> None:
        self._session.add(order)

    async def list_recent_with_names(self, *, limit: int = 10) -> list[tuple[ArticleOrder, str, str]]:
        """Vista arricchita (nome articolo + nome operatore) - stessa join
        gia' fatta da V1 (articles_page)."""
        stmt = (
            select(ArticleOrder, Article.name, User.display_name)
            .join(Article, Article.id == ArticleOrder.article_id)
            .join(User, User.id == ArticleOrder.ordered_by)
            .order_by(ArticleOrder.created_at.desc(), ArticleOrder.id.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).all())
