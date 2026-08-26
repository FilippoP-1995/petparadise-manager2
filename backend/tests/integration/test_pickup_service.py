from datetime import datetime, timedelta, timezone

import pytest

from domain.errors import NotFoundError, ValidationDomainError
from models.calendar_event import PickupStatus
from models.practice import PickupType
from repositories.calendar_event_repository import CalendarEventRepository
from repositories.practice_repository import PracticeRepository
from schemas.calendar_event import (
    CreatePracticeFromPickupRequest,
    PickupCreate,
    PickupUpdate,
)
from schemas.practice import AnimalInput
from services import pickup_service


def _start_end():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return start, start + timedelta(hours=1)


def _create_data(sample_client, **overrides):
    start, end = _start_end()
    base = dict(
        start_at=start,
        end_at=end,
        client_id=sample_client.id,
        pickup_type=PickupType.collaboratore,
        collaborator_id=None,
    )
    base.update(overrides)
    return PickupCreate(**base)


async def test_create_pickup_defaults_to_da_confermare(db_session, admin_user, sample_client):
    """FACT V1: default reale (calendar_service.py normalize_event)."""
    from models.collaborator import Collaborator

    collaborator = Collaborator(name="Collaboratore Test")
    db_session.add(collaborator)
    await db_session.flush()

    pickup = await pickup_service.create_pickup(
        db_session, _create_data(sample_client, collaborator_id=collaborator.id), actor_user_id=admin_user.id
    )
    assert pickup.pickup_status == PickupStatus.da_confermare


async def test_create_pickup_ignores_a_client_supplied_status(db_session, admin_user, sample_client):
    """Anche uno schema costruito con extra non validi non ha comunque un
    campo pickup_status - verificato strutturalmente."""
    assert "pickup_status" not in PickupCreate.model_fields


async def test_create_pickup_rejects_inconsistent_fields(db_session, admin_user, sample_client, sample_location):
    with pytest.raises(ValidationDomainError):
        await pickup_service.create_pickup(
            db_session,
            _create_data(sample_client, pickup_type=PickupType.sede_aziendale, pickup_location_id=None),
            actor_user_id=admin_user.id,
        )


async def test_create_pickup_with_domicilio_zone(db_session, admin_user, sample_client, sample_zone):
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(sample_client, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    assert pickup.pickup_type == PickupType.domicilio
    assert pickup.pickup_zone_id == sample_zone.id


async def test_create_pickup_with_multiple_animals(db_session, admin_user, sample_client, sample_zone):
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(
            sample_client,
            pickup_type=PickupType.domicilio,
            pickup_zone_id=sample_zone.id,
            animals=[AnimalInput(name="Fido"), AnimalInput(name="Micio"), AnimalInput(name="Terzo")],
        ),
        actor_user_id=admin_user.id,
    )
    assert {a.name for a in pickup.animals} == {"Fido", "Micio", "Terzo"}


async def test_update_pickup_rejected_once_annullato(db_session, admin_user, sample_client, sample_zone):
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(sample_client, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.annullato, actor_user_id=admin_user.id)

    with pytest.raises(ValidationDomainError):
        await pickup_service.update_pickup(
            db_session,
            pickup.id,
            _create_data(sample_client, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id, notes="tentativo"),
            actor_user_id=admin_user.id,
        )


async def test_cancel_pickup_does_not_touch_a_linked_practice(db_session, admin_user, sample_client, sample_location, sample_zone):
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(sample_client, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.da_ritirare, actor_user_id=admin_user.id)
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.ritirato, actor_user_id=admin_user.id)
    practice = await pickup_service.create_practice_from_pickup_action(
        db_session, pickup.id, CreatePracticeFromPickupRequest(destination_branch_id=sample_location.id), actor_user_id=admin_user.id
    )

    cancelled = await pickup_service.cancel_pickup(db_session, pickup.id, "annullato dal cliente", actor_user_id=admin_user.id)
    assert cancelled.pickup_status == PickupStatus.annullato
    assert cancelled.linked_practice_id == practice.id

    reloaded_practice = await PracticeRepository(db_session).get_by_id(practice.id)
    assert reloaded_practice is not None, "la pratica collegata non deve essere toccata dall'annullamento normale (azione A)"
    assert reloaded_practice.deleted_at is None


async def test_cancel_pickup_and_trash_practice_requires_a_linked_practice(db_session, admin_user, sample_client, sample_zone):
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(sample_client, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    with pytest.raises(ValidationDomainError):
        await pickup_service.cancel_pickup_and_trash_practice(db_session, pickup.id, "motivo", actor_user_id=admin_user.id)


async def test_cancel_pickup_and_trash_practice_trashes_the_linked_practice(
    db_session, admin_user, sample_client, sample_location, sample_zone
):
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(sample_client, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.da_ritirare, actor_user_id=admin_user.id)
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.ritirato, actor_user_id=admin_user.id)
    practice = await pickup_service.create_practice_from_pickup_action(
        db_session, pickup.id, CreatePracticeFromPickupRequest(destination_branch_id=sample_location.id), actor_user_id=admin_user.id
    )

    cancelled = await pickup_service.cancel_pickup_and_trash_practice(
        db_session, pickup.id, "ritiro annullato dal cliente", actor_user_id=admin_user.id
    )
    assert cancelled.pickup_status == PickupStatus.annullato

    reloaded_practice = await PracticeRepository(db_session).get_by_id(practice.id)
    assert reloaded_practice is None, "get_by_id esclude le pratiche cestinate (stesso comportamento del dominio Pratica)"

    from repositories.practice_repository import PracticeRepository as PR

    practice_with_deleted = await PR(db_session).get_by_id(practice.id, include_deleted=True)
    assert practice_with_deleted.deleted_at is not None
    assert practice_with_deleted.deleted_by == admin_user.id


async def test_create_practice_from_pickup_requires_ritirato_status(db_session, admin_user, sample_client, sample_location, sample_zone):
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(sample_client, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    with pytest.raises(ValidationDomainError):
        await pickup_service.create_practice_from_pickup_action(
            db_session, pickup.id, CreatePracticeFromPickupRequest(destination_branch_id=sample_location.id), actor_user_id=admin_user.id
        )


async def test_create_practice_from_pickup_transfers_all_animals_same_rows(
    db_session, admin_user, sample_client, sample_location, sample_zone
):
    """doc06 'stessa riga, mai una copia' - risolve il bug V1 dove solo il
    primo animale sopravviveva alla conversione."""
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(
            sample_client,
            pickup_type=PickupType.domicilio,
            pickup_zone_id=sample_zone.id,
            animals=[AnimalInput(name="Fido"), AnimalInput(name="Micio"), AnimalInput(name="Terzo")],
        ),
        actor_user_id=admin_user.id,
    )
    animal_ids_before = {a.id for a in pickup.animals}

    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.da_ritirare, actor_user_id=admin_user.id)
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.ritirato, actor_user_id=admin_user.id)
    practice = await pickup_service.create_practice_from_pickup_action(
        db_session, pickup.id, CreatePracticeFromPickupRequest(destination_branch_id=sample_location.id), actor_user_id=admin_user.id
    )

    assert practice.status.value == "ritirato"
    assert practice.originating_pickup_event_id == pickup.id
    assert {a.id for a in practice.animals} == animal_ids_before, "devono essere le STESSE righe, non copie"
    assert {a.name for a in practice.animals} == {"Fido", "Micio", "Terzo"}

    reloaded_pickup = await CalendarEventRepository(db_session).get_by_id(pickup.id)
    assert reloaded_pickup.linked_practice_id == practice.id


async def test_create_practice_from_pickup_rejects_double_creation(
    db_session, admin_user, sample_client, sample_location, sample_zone
):
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(sample_client, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.da_ritirare, actor_user_id=admin_user.id)
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.ritirato, actor_user_id=admin_user.id)
    await pickup_service.create_practice_from_pickup_action(
        db_session, pickup.id, CreatePracticeFromPickupRequest(destination_branch_id=sample_location.id), actor_user_id=admin_user.id
    )

    with pytest.raises(ValidationDomainError):
        await pickup_service.create_practice_from_pickup_action(
            db_session, pickup.id, CreatePracticeFromPickupRequest(destination_branch_id=sample_location.id), actor_user_id=admin_user.id
        )


async def test_trash_and_restore_pickup(db_session, admin_user, sample_client, sample_zone):
    repo = CalendarEventRepository(db_session)
    pickup = await pickup_service.create_pickup(
        db_session,
        _create_data(sample_client, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    trashed = await pickup_service.trash_pickup(db_session, pickup.id, actor_user_id=admin_user.id)
    assert trashed.deleted_at is not None
    assert await repo.get_by_id(pickup.id) is None

    restored = await pickup_service.restore_pickup(db_session, pickup.id, actor_user_id=admin_user.id)
    assert restored.deleted_at is None
    assert await repo.get_by_id(pickup.id) is not None
