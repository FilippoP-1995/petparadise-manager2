"""Verifica comportamentale specifica per Ritiro/Riconsegna (sezione 10
della richiesta: 'Ritiro -> creazione Pratica -> collegamento' deve essere
transazionale - se una delle operazioni fallisce, non devono rimanere dati
parziali). Stessa tecnica di iniezione di fallimento gia' usata per
Clienti/Veterinari/Pratiche (vincolo NOT NULL reale su entity_type)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from models.calendar_event import CalendarEvent, CalendarEventType, PickupStatus
from models.practice import PickupType
from repositories.audit_repository import AuditRepository
from repositories.calendar_event_repository import CalendarEventRepository
from repositories.client_repository import ClientRepository
from repositories.practice_repository import PracticeRepository
from schemas.calendar_event import PickupCreate
from schemas.practice import AnimalInput, PracticeCreate
from services import practice_service
from services.practice_service import _create_practice_core


def _start_end():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return start, start + timedelta(hours=1)


async def test_failed_audit_write_rolls_back_a_pickup_state_transition(db_session, admin_user, sample_client, sample_zone):
    start, end = _start_end()
    repo = CalendarEventRepository(db_session)
    pickup = CalendarEvent(
        event_type=CalendarEventType.ritiro,
        pickup_status=PickupStatus.da_confermare,
        pickup_type=PickupType.domicilio,
        pickup_zone_id=sample_zone.id,
        client_id=sample_client.id,
        start_at=start,
        end_at=end,
        created_by=admin_user.id,
    )
    repo.add(pickup)
    await db_session.commit()
    pickup_id = pickup.id

    reloaded = await repo.get_by_id(pickup_id)
    reloaded.pickup_status = PickupStatus.da_ritirare
    AuditRepository(db_session).record(entity_type=None, entity_id=pickup_id, action="state_changed", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    final = await repo.get_by_id(pickup_id)
    assert final.pickup_status == PickupStatus.da_confermare, "lo stato non deve cambiare se l'audit della transizione fallisce"


async def test_failed_practice_creation_from_pickup_leaves_no_partial_data(
    db_session, admin_user, sample_client, sample_location, sample_zone
):
    """Replica dei passi di practice_service.create_practice_from_pickup,
    forzando un fallimento nell'ultimo passo (audit sul calendar_event) -
    verifica che NON restino: una pratica orfana, animali riassegnati, o un
    ritiro segnato come collegato."""
    start, end = _start_end()
    repo = CalendarEventRepository(db_session)
    pickup = CalendarEvent(
        event_type=CalendarEventType.ritiro,
        pickup_status=PickupStatus.ritirato,
        pickup_type=PickupType.domicilio,
        pickup_zone_id=sample_zone.id,
        client_id=sample_client.id,
        start_at=start,
        end_at=end,
        created_by=admin_user.id,
    )
    from models.animal import Animal

    pickup.animals = [Animal(name="Fido"), Animal(name="Micio")]
    repo.add(pickup)
    await db_session.commit()
    pickup_id = pickup.id

    reloaded_pickup = await repo.get_by_id_for_update(pickup_id)
    animal_ids = {a.id for a in reloaded_pickup.animals}
    client = await ClientRepository(db_session).get_by_id(sample_client.id)

    data = PracticeCreate(
        client_id=client.id,
        destination_branch_id=sample_location.id,
        request_origin="Privato",
        service_type="Da decidere",
        pickup_type=reloaded_pickup.pickup_type,
        pickup_zone_id=reloaded_pickup.pickup_zone_id,
    )
    practice = await _create_practice_core(
        db_session, data, client=client, originating_pickup_event_id=reloaded_pickup.id, actor_user_id=admin_user.id
    )
    practice_id = practice.id

    for animal in list(reloaded_pickup.animals):
        reloaded_pickup.animals.remove(animal)
        practice.animals.append(animal)
    reloaded_pickup.linked_practice_id = practice.id

    # Fallimento forzato nell'ultimo passo - stessa tecnica (NOT NULL) gia'
    # usata per Clienti/Veterinari/Pratiche.
    AuditRepository(db_session).record(
        entity_type=None, entity_id=reloaded_pickup.id, action="practice_created", user_id=admin_user.id
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    assert await PracticeRepository(db_session).get_by_id(practice_id) is None, "nessuna pratica orfana deve sopravvivere"

    final_pickup = await repo.get_by_id(pickup_id)
    assert final_pickup.linked_practice_id is None, "il ritiro non deve risultare collegato se la transazione fallisce"
    assert {a.id for a in final_pickup.animals} == animal_ids, "gli animali devono restare sul ritiro, non spariti ne' orfani"
