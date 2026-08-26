"""Verifica comportamentale, specifica per le transizioni di stato della
Pratica (non una copia del test gia' fatto per Clienti/Veterinari): una
transizione di stato e il relativo audit_log devono vivere nella stessa
transazione (doc09), quindi un fallimento nella meta' 'audit' deve
impedire che lo stato della pratica risulti comunque cambiato."""

import pytest
from sqlalchemy.exc import IntegrityError

from models.animal import Animal
from models.practice import Practice, PracticeStatus
from repositories.audit_repository import AuditRepository
from repositories.practice_repository import PracticeRepository


async def _create_bare_practice(db_session, sample_client, sample_location) -> Practice:
    repo = PracticeRepository(db_session)
    practice = Practice(
        practice_number=await repo.next_practice_number(service_type="Cremazione singola", request_origin="Collaboratore"),
        status=PracticeStatus.ritirato,
        client_id=sample_client.id,
        destination_branch_id=sample_location.id,
        request_origin="Collaboratore",
        service_type="Cremazione singola",
        created_by=None,
    )
    repo.add(practice)
    await db_session.flush()
    return practice


async def test_failed_audit_write_rolls_back_a_state_transition(db_session, admin_user, sample_client, sample_location):
    practice = await _create_bare_practice(db_session, sample_client, sample_location)
    await db_session.commit()
    practice_id = practice.id

    repo = PracticeRepository(db_session)
    audit = AuditRepository(db_session)

    reloaded = await repo.get_by_id(practice_id)
    reloaded.status = PracticeStatus.in_programma
    # entity_type NOT NULL - simula un fallimento nella meta' 'audit' di
    # una transizione di stato.
    audit.record(entity_type=None, entity_id=practice_id, action="state_changed", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    final = await repo.get_by_id(practice_id)
    assert final.status == PracticeStatus.ritirato, (
        "lo stato della pratica non deve mai risultare cambiato se l'audit della transizione fallisce"
    )


async def test_failed_audit_write_rolls_back_animals_added_during_an_update(
    db_session, admin_user, sample_client, sample_location
):
    """Caso specifico Pratica: l'update sostituisce la lista animali in
    cascata (delete-orphan + insert) nella stessa transazione dell'audit -
    un fallimento dell'audit non deve lasciare animali orfani scritti."""
    practice = await _create_bare_practice(db_session, sample_client, sample_location)
    await db_session.commit()
    practice_id = practice.id

    repo = PracticeRepository(db_session)
    audit = AuditRepository(db_session)

    reloaded = await repo.get_by_id(practice_id)
    reloaded.animals = [Animal(name="Fido", practice_id=practice_id)]
    audit.record(entity_type=None, entity_id=practice_id, action="field_changed", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    final = await repo.get_by_id(practice_id)
    assert final.animals == [], "nessun animale deve sopravvivere se l'audit della stessa transazione fallisce"
