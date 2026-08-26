from sqlalchemy.ext.asyncio import AsyncSession

from domain.article.rules import ensure_orderable
from models.article import ArticleOrder
from repositories.article_repository import ArticleOrderRepository, ArticleRepository
from repositories.audit_repository import AuditRepository
from schemas.article import ArticleOrderRead

ENTITY_TYPE = "article"


async def order_article(db: AsyncSession, article_id: int, *, actor_user_id: int) -> ArticleOrder:
    """FACT V1 (order_article): registra solo la richiesta interna - nessuna
    email, nessun fornitore (fuori scope, vedi models/article.py)."""
    articles = ArticleRepository(db)
    orders = ArticleOrderRepository(db)
    audit = AuditRepository(db)

    article = await articles.get_by_id(article_id)
    ensure_orderable(article)

    order = ArticleOrder(article_id=article_id, ordered_by=actor_user_id)
    orders.add(order)
    await db.flush()

    audit.record(entity_type=ENTITY_TYPE, entity_id=article_id, action="ordered", user_id=actor_user_id)

    await db.commit()
    await db.refresh(order)
    return order


async def list_recent_orders(db: AsyncSession, *, limit: int = 10) -> list[ArticleOrderRead]:
    rows = await ArticleOrderRepository(db).list_recent_with_names(limit=limit)
    return [
        ArticleOrderRead(
            id=order.id,
            article_id=order.article_id,
            article_name=article_name,
            ordered_by=order.ordered_by,
            ordered_by_name=display_name,
            created_at=order.created_at,
        )
        for order, article_name, display_name in rows
    ]
