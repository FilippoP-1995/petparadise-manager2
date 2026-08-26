from datetime import datetime, timedelta, timezone

import pytest

from domain.errors import NotFoundError, ValidationDomainError
from models.calendar_event import DeliveryType
from schemas.calendar_event import DeliveryCreate, DeliveryUpdate, LinkDeliveryToPracticeRequest
from schemas.practice import LineItemInput, PracticeCreate
from services import delivery_service, practice_service


def _start_end():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return start, start + timedelta(hours=1)


def _delivery_data(**overrides):
    start, end = _start_end()
    base = dict(start_at=start, end_at=end, delivery_type=DeliveryType.sede_aziendale)
    base.update(overrides)
    return DeliveryCreate(**base)


async def _create_practice_with_total(db_session, admin_user, sample_client, sample_location, amount_cents):
    return await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            line_items=[LineItemInput(category="cremazione", description="Cremazione", amount_cents=amount_cents)],
        ),
        actor_user_id=admin_user.id,
    )


async def test_create_delivery_in_sede_requires_location(db_session, admin_user):
    with pytest.raises(ValidationDomainError):
        await delivery_service.create_delivery(
            db_session, _delivery_data(delivery_type=DeliveryType.sede_aziendale, delivery_location_id=None), actor_user_id=admin_user.id
        )


async def test_create_delivery_in_sede_succeeds_with_location(db_session, admin_user, sample_location):
    delivery = await delivery_service.create_delivery(
        db_session,
        _delivery_data(delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id),
        actor_user_id=admin_user.id,
    )
    assert delivery.delivery_type == DeliveryType.sede_aziendale
    assert delivery.delivery_location_id == sample_location.id


async def test_create_delivery_fuori_sede_domicilio_requires_zone(db_session, admin_user, sample_zone):
    with pytest.raises(ValidationDomainError):
        await delivery_service.create_delivery(
            db_session, _delivery_data(delivery_type=DeliveryType.domicilio, delivery_zone_id=None), actor_user_id=admin_user.id
        )
    delivery = await delivery_service.create_delivery(
        db_session, _delivery_data(delivery_type=DeliveryType.domicilio, delivery_zone_id=sample_zone.id), actor_user_id=admin_user.id
    )
    assert delivery.delivery_zone_id == sample_zone.id


async def test_create_delivery_with_preliminary_payment(db_session, admin_user, sample_location):
    delivery = await delivery_service.create_delivery(
        db_session,
        _delivery_data(
            delivery_type=DeliveryType.sede_aziendale,
            delivery_location_id=sample_location.id,
            preliminary_payment_status="Da saldare",
            preliminary_payment_amount=5000,
        ),
        actor_user_id=admin_user.id,
    )
    assert delivery.preliminary_payment_status == "Da saldare"
    assert delivery.preliminary_payment_amount == 5000


async def test_update_delivery_before_link_can_change_preliminary_payment(db_session, admin_user, sample_location):
    delivery = await delivery_service.create_delivery(
        db_session,
        _delivery_data(
            delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id, preliminary_payment_amount=5000
        ),
        actor_user_id=admin_user.id,
    )
    start, end = _start_end()
    updated = await delivery_service.update_delivery(
        db_session,
        delivery.id,
        DeliveryUpdate(
            start_at=start,
            end_at=end,
            delivery_type=DeliveryType.sede_aziendale,
            delivery_location_id=sample_location.id,
            preliminary_payment_amount=7000,
        ),
        actor_user_id=admin_user.id,
    )
    assert updated.preliminary_payment_amount == 7000


async def test_link_delivery_to_practice_without_mismatch(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 12000)
    delivery = await delivery_service.create_delivery(
        db_session,
        _delivery_data(delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id, preliminary_payment_amount=12000),
        actor_user_id=admin_user.id,
    )
    linked = await delivery_service.link_delivery_to_practice(
        db_session, delivery.id, LinkDeliveryToPracticeRequest(practice_id=practice.id), actor_user_id=admin_user.id
    )
    assert linked.linked_practice_id == practice.id


async def test_link_delivery_to_practice_rejects_mismatch_without_confirmation(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 12000)
    delivery = await delivery_service.create_delivery(
        db_session,
        _delivery_data(delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id, preliminary_payment_amount=9000),
        actor_user_id=admin_user.id,
    )
    with pytest.raises(ValidationDomainError):
        await delivery_service.link_delivery_to_practice(
            db_session, delivery.id, LinkDeliveryToPracticeRequest(practice_id=practice.id), actor_user_id=admin_user.id
        )


async def test_link_delivery_to_practice_accepts_mismatch_with_explicit_confirmation(
    db_session, admin_user, sample_client, sample_location
):
    """doc06 Addendum P: 'mai un collegamento silenzioso che fa sparire la
    discrepanza' - con conferma esplicita il collegamento avviene, la
    divergenza resta comunque tracciata in audit (verificato sotto)."""
    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 12000)
    delivery = await delivery_service.create_delivery(
        db_session,
        _delivery_data(delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id, preliminary_payment_amount=9000),
        actor_user_id=admin_user.id,
    )
    linked = await delivery_service.link_delivery_to_practice(
        db_session,
        delivery.id,
        LinkDeliveryToPracticeRequest(practice_id=practice.id, confirm_despite_mismatch=True),
        actor_user_id=admin_user.id,
    )
    assert linked.linked_practice_id == practice.id

    from sqlalchemy import select

    from models.audit_log import AuditLog

    row = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.entity_type == "calendar_event", AuditLog.action == "linked_to_practice")
        )
    ).scalar_one()
    assert row.reason is not None


async def test_update_delivery_after_link_cannot_change_preliminary_payment(db_session, admin_user, sample_client, sample_location):
    """doc06 Addendum P: 'congelati... mai piu' scritti dall'interfaccia' -
    mai una correzione silenziosa."""
    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 12000)
    delivery = await delivery_service.create_delivery(
        db_session,
        _delivery_data(delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id, preliminary_payment_amount=12000),
        actor_user_id=admin_user.id,
    )
    await delivery_service.link_delivery_to_practice(
        db_session, delivery.id, LinkDeliveryToPracticeRequest(practice_id=practice.id), actor_user_id=admin_user.id
    )

    start, end = _start_end()
    with pytest.raises(ValidationDomainError):
        await delivery_service.update_delivery(
            db_session,
            delivery.id,
            DeliveryUpdate(
                start_at=start,
                end_at=end,
                delivery_type=DeliveryType.sede_aziendale,
                delivery_location_id=sample_location.id,
                preliminary_payment_amount=1,
            ),
            actor_user_id=admin_user.id,
        )


async def test_trash_and_restore_delivery(db_session, admin_user, sample_location):
    from repositories.calendar_event_repository import CalendarEventRepository

    repo = CalendarEventRepository(db_session)
    delivery = await delivery_service.create_delivery(
        db_session, _delivery_data(delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id), actor_user_id=admin_user.id
    )
    trashed = await delivery_service.trash_delivery(db_session, delivery.id, actor_user_id=admin_user.id)
    assert trashed.deleted_at is not None
    assert await repo.get_by_id(delivery.id) is None

    restored = await delivery_service.restore_delivery(db_session, delivery.id, actor_user_id=admin_user.id)
    assert restored.deleted_at is None
