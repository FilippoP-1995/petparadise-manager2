import pytest

from domain.errors import NotFoundError, ValidationDomainError
from models.article import Article
from services import article_service


async def _create_article(db_session, name="Prodotto di test", active=True):
    article = Article(name=name, active=active)
    db_session.add(article)
    await db_session.flush()
    return article


async def test_order_article_records_request_and_audit(db_session, admin_user):
    article = await _create_article(db_session)
    order = await article_service.order_article(db_session, article.id, actor_user_id=admin_user.id)
    assert order.article_id == article.id
    assert order.ordered_by == admin_user.id

    from sqlalchemy import select

    from models.audit_log import AuditLog

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.entity_type == "article", AuditLog.entity_id == article.id)
        )
    ).scalars().all()
    assert any(r.action == "ordered" for r in rows)


async def test_order_inactive_article_rejected(db_session, admin_user):
    article = await _create_article(db_session, active=False)
    with pytest.raises(ValidationDomainError):
        await article_service.order_article(db_session, article.id, actor_user_id=admin_user.id)


async def test_order_unknown_article_raises_not_found(db_session, admin_user):
    with pytest.raises(NotFoundError):
        await article_service.order_article(db_session, 999999, actor_user_id=admin_user.id)


async def test_list_recent_orders_includes_names(db_session, admin_user):
    article = await _create_article(db_session, name="Prodotto di test per lista recenti")
    await article_service.order_article(db_session, article.id, actor_user_id=admin_user.id)

    recent = await article_service.list_recent_orders(db_session, limit=1)
    assert len(recent) == 1
    assert recent[0].article_name == "Prodotto di test per lista recenti"
    assert recent[0].ordered_by_name == admin_user.display_name
