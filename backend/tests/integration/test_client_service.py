import pytest
from sqlalchemy import select

from domain.errors import NotFoundError, ValidationDomainError
from models.audit_log import AuditLog
from schemas.client import ClientCreate, ClientUpdate
from services import client_service


async def test_create_client_persists_and_writes_audit_in_same_transaction(db_session, admin_user):
    client = await client_service.create_client(
        db_session, ClientCreate(first_name="Mario", last_name="Rossi", phone="333123456"), actor_user_id=admin_user.id
    )

    assert client.id is not None
    assert client.active is True

    audit_rows = (
        await db_session.execute(select(AuditLog).where(AuditLog.entity_type == "client", AuditLog.entity_id == client.id))
    ).scalars().all()
    assert len(audit_rows) == 1
    assert audit_rows[0].action == "created"
    assert audit_rows[0].user_id == admin_user.id


async def test_create_client_without_identifying_field_is_rejected(db_session, admin_user):
    with pytest.raises(ValidationDomainError):
        await client_service.create_client(db_session, ClientCreate(phone="333"), actor_user_id=admin_user.id)


async def test_update_client_records_only_changed_fields_in_audit(db_session, admin_user):
    client = await client_service.create_client(
        db_session, ClientCreate(first_name="Mario", last_name="Rossi", city="Livorno"), actor_user_id=admin_user.id
    )

    updated = await client_service.update_client(
        db_session,
        client.id,
        ClientUpdate(first_name="Mario", last_name="Rossi", city="Empoli"),
        actor_user_id=admin_user.id,
    )

    assert updated.city == "Empoli"
    audit_rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "client", AuditLog.entity_id == client.id, AuditLog.action == "field_changed"
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1
    assert audit_rows[0].field_name == "city"
    assert audit_rows[0].old_value == "Livorno"
    assert audit_rows[0].new_value == "Empoli"


async def test_update_nonexistent_client_raises_not_found(db_session, admin_user):
    with pytest.raises(NotFoundError):
        await client_service.update_client(
            db_session, 999_999, ClientUpdate(first_name="X", last_name="Y"), actor_user_id=admin_user.id
        )


async def test_deactivate_client_hides_it_from_active_listing(db_session, admin_user):
    from repositories.client_repository import ClientRepository

    client = await client_service.create_client(
        db_session, ClientCreate(first_name="Anna", last_name="Verdi"), actor_user_id=admin_user.id
    )
    await client_service.deactivate_client(db_session, client.id, actor_user_id=admin_user.id)

    repo = ClientRepository(db_session)
    active_clients = await repo.list_active(search=None, limit=50, offset=0)
    assert client.id not in [c.id for c in active_clients]


async def test_deactivate_already_inactive_client_raises_not_found(db_session, admin_user):
    client = await client_service.create_client(
        db_session, ClientCreate(first_name="Luca", last_name="Neri"), actor_user_id=admin_user.id
    )
    await client_service.deactivate_client(db_session, client.id, actor_user_id=admin_user.id)

    with pytest.raises(NotFoundError):
        await client_service.deactivate_client(db_session, client.id, actor_user_id=admin_user.id)
