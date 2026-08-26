import pytest

from domain.errors import NotFoundError, ValidationDomainError
from schemas.company_location import CompanyLocationCreate, CompanyLocationUpdate
from services import company_location_service


async def test_create_location(db_session, admin_user):
    location = await company_location_service.create_location(
        db_session, CompanyLocationCreate(name="Firenze", has_cremation_plant=False), actor_user_id=admin_user.id
    )
    assert location.name == "Firenze"
    assert location.active is True
    assert location.created_by == admin_user.id


async def test_create_location_rejects_empty_name(db_session, admin_user):
    with pytest.raises(ValidationDomainError):
        await company_location_service.create_location(
            db_session, CompanyLocationCreate(name="   "), actor_user_id=admin_user.id
        )


async def test_update_location(db_session, admin_user):
    location = await company_location_service.create_location(
        db_session, CompanyLocationCreate(name="Firenze"), actor_user_id=admin_user.id
    )
    updated = await company_location_service.update_location(
        db_session, location.id, CompanyLocationUpdate(name="Firenze Nord", has_cremation_plant=True), actor_user_id=admin_user.id
    )
    assert updated.name == "Firenze Nord"
    assert updated.has_cremation_plant is True
    assert updated.updated_by == admin_user.id


async def test_update_unknown_location_raises_not_found(db_session, admin_user):
    with pytest.raises(NotFoundError):
        await company_location_service.update_location(
            db_session, 999999, CompanyLocationUpdate(name="X"), actor_user_id=admin_user.id
        )


async def test_deactivate_location(db_session, admin_user):
    location = await company_location_service.create_location(
        db_session, CompanyLocationCreate(name="Firenze"), actor_user_id=admin_user.id
    )
    deactivated = await company_location_service.deactivate_location(db_session, location.id, actor_user_id=admin_user.id)
    assert deactivated.active is False


async def test_deactivate_already_inactive_raises_not_found(db_session, admin_user):
    location = await company_location_service.create_location(
        db_session, CompanyLocationCreate(name="Firenze"), actor_user_id=admin_user.id
    )
    await company_location_service.deactivate_location(db_session, location.id, actor_user_id=admin_user.id)
    with pytest.raises(NotFoundError):
        await company_location_service.deactivate_location(db_session, location.id, actor_user_id=admin_user.id)


async def test_audit_records_created_and_field_changed(db_session, admin_user):
    location = await company_location_service.create_location(
        db_session, CompanyLocationCreate(name="Firenze"), actor_user_id=admin_user.id
    )
    await company_location_service.update_location(
        db_session, location.id, CompanyLocationUpdate(name="Firenze Nord"), actor_user_id=admin_user.id
    )

    from sqlalchemy import select

    from models.audit_log import AuditLog

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.entity_type == "company_location", AuditLog.entity_id == location.id)
        )
    ).scalars().all()
    actions = {r.action for r in rows}
    assert "created" in actions
    assert "field_changed" in actions
