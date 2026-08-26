from datetime import date, time

import pytest

from domain.errors import NotFoundError, ValidationDomainError
from models.cremation_cycle import CremationCycleStatus
from models.practice import PracticeStatus
from schemas.cremation_cycle import CremationCycleCreate, CremationCycleUpdate
from schemas.practice import AnimalInput, PracticeCreate, TransitionRequest
from services import cremation_cycle_service, practice_service


def _cycle_data(**overrides):
    base = dict(cycle_date=date(2026, 9, 10), planned_start=time(9, 0), planned_end=time(10, 30))
    base.update(overrides)
    return CremationCycleCreate(**base)


async def _create_eligible_practice(db_session, admin_user, sample_client, sample_location, animal_names, *, advance_to_in_programma=True):
    practice = await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            animals=[AnimalInput(name=n) for n in animal_names],
        ),
        actor_user_id=admin_user.id,
    )
    if advance_to_in_programma:
        practice = await practice_service.transition_practice_state(
            db_session, practice.id, TransitionRequest(target_status=PracticeStatus.in_programma), actor_user_id=admin_user.id
        )
    return practice


async def test_create_cycle_starts_pianificato_with_zero_animals(db_session, admin_user):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    assert cycle.status == CremationCycleStatus.pianificato
    assert cycle.animals == []


async def test_assign_first_animal_moves_to_in_attesa(db_session, admin_user, sample_client, sample_location):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    animal_id = practice.animals[0].id

    updated = await cremation_cycle_service.assign_animal(db_session, cycle.id, animal_id, actor_user_id=admin_user.id)
    assert updated.status == CremationCycleStatus.in_attesa
    assert [a.id for a in updated.animals] == [animal_id]


async def test_assign_third_animal_rejected_capacity(db_session, admin_user, sample_client, sample_location):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["A", "B", "C"])
    a, b, c = [an.id for an in practice.animals]

    await cremation_cycle_service.assign_animal(db_session, cycle.id, a, actor_user_id=admin_user.id)
    await cremation_cycle_service.assign_animal(db_session, cycle.id, b, actor_user_id=admin_user.id)
    with pytest.raises(ValidationDomainError):
        await cremation_cycle_service.assign_animal(db_session, cycle.id, c, actor_user_id=admin_user.id)


async def test_remove_last_animal_reverts_to_pianificato(db_session, admin_user, sample_client, sample_location):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    animal_id = practice.animals[0].id
    await cremation_cycle_service.assign_animal(db_session, cycle.id, animal_id, actor_user_id=admin_user.id)

    updated = await cremation_cycle_service.remove_animal(db_session, cycle.id, animal_id, actor_user_id=admin_user.id)
    assert updated.status == CremationCycleStatus.pianificato
    assert updated.animals == []


async def test_reassign_animal_between_two_non_completed_cycles(db_session, admin_user, sample_client, sample_location):
    cycle1 = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    cycle2 = await cremation_cycle_service.create_cycle(db_session, _cycle_data(planned_start=time(11, 0), planned_end=time(12, 30)), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    animal_id = practice.animals[0].id

    await cremation_cycle_service.assign_animal(db_session, cycle1.id, animal_id, actor_user_id=admin_user.id)
    result = await cremation_cycle_service.assign_animal(db_session, cycle2.id, animal_id, actor_user_id=admin_user.id)

    assert [a.id for a in result.animals] == [animal_id]
    from repositories.cremation_cycle_repository import CremationCycleRepository

    reloaded_cycle1 = await CremationCycleRepository(db_session).get_by_id(cycle1.id)
    assert reloaded_cycle1.animals == [], "l'animale non deve restare anche sul vecchio ciclo"
    assert reloaded_cycle1.status == CremationCycleStatus.pianificato, "il vecchio ciclo torna pianificato se svuotato"


async def test_animal_identity_stable_across_reassignment_no_duplication(db_session, admin_user, sample_client, sample_location):
    """Sezione 'impossibilita' di perdere o duplicare animali'."""
    cycle1 = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    cycle2 = await cremation_cycle_service.create_cycle(db_session, _cycle_data(planned_start=time(11, 0), planned_end=time(12, 30)), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    animal_id = practice.animals[0].id

    await cremation_cycle_service.assign_animal(db_session, cycle1.id, animal_id, actor_user_id=admin_user.id)
    await cremation_cycle_service.assign_animal(db_session, cycle2.id, animal_id, actor_user_id=admin_user.id)

    from sqlalchemy import select

    from models.animal import Animal

    all_rows = (await db_session.execute(select(Animal).where(Animal.practice_id == practice.id))).scalars().all()
    assert len(all_rows) == 1, "un solo Animal, mai duplicato"
    assert all_rows[0].id == animal_id
    assert all_rows[0].practice_id == practice.id, "il collegamento alla pratica non e' mai perso"


async def test_reassign_from_completed_cycle_is_rejected(db_session, admin_user, sample_client, sample_location):
    cycle1 = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    cycle2 = await cremation_cycle_service.create_cycle(db_session, _cycle_data(planned_start=time(11, 0), planned_end=time(12, 30)), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    animal_id = practice.animals[0].id

    await cremation_cycle_service.assign_animal(db_session, cycle1.id, animal_id, actor_user_id=admin_user.id)
    await cremation_cycle_service.complete_cycle(db_session, cycle1.id, actor_user_id=admin_user.id)

    with pytest.raises(ValidationDomainError):
        await cremation_cycle_service.assign_animal(db_session, cycle2.id, animal_id, actor_user_id=admin_user.id)


async def test_complete_cycle_transitions_practice_when_all_animals_done(db_session, admin_user, sample_client, sample_location):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    animal_id = practice.animals[0].id
    await cremation_cycle_service.assign_animal(db_session, cycle.id, animal_id, actor_user_id=admin_user.id)

    await cremation_cycle_service.complete_cycle(db_session, cycle.id, actor_user_id=admin_user.id)

    from repositories.practice_repository import PracticeRepository

    reloaded_practice = await PracticeRepository(db_session).get_by_id(practice.id)
    assert reloaded_practice.status == PracticeStatus.cremato
    assert reloaded_practice.cremation_registered is True


async def test_practice_stays_in_programma_until_all_split_animals_are_cremated(
    db_session, admin_user, sample_client, sample_location
):
    """LO SCENARIO CRITICO: Pratica con 3 animali, A+B->Ciclo1, C->Ciclo2.
    La pratica NON deve diventare 'cremato' finche' entrambi i cicli non
    sono completati."""
    cycle1 = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    cycle2 = await cremation_cycle_service.create_cycle(db_session, _cycle_data(planned_start=time(11, 0), planned_end=time(12, 30)), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["A", "B", "C"])
    a, b, c = [an.id for an in practice.animals]

    await cremation_cycle_service.assign_animal(db_session, cycle1.id, a, actor_user_id=admin_user.id)
    await cremation_cycle_service.assign_animal(db_session, cycle1.id, b, actor_user_id=admin_user.id)
    await cremation_cycle_service.assign_animal(db_session, cycle2.id, c, actor_user_id=admin_user.id)

    from repositories.practice_repository import PracticeRepository

    practice_repo = PracticeRepository(db_session)

    await cremation_cycle_service.complete_cycle(db_session, cycle1.id, actor_user_id=admin_user.id)
    still_in_programma = await practice_repo.get_by_id(practice.id)
    assert still_in_programma.status == PracticeStatus.in_programma, "Ciclo1 completo ma C non ancora cremato: la pratica resta in_programma"

    await cremation_cycle_service.complete_cycle(db_session, cycle2.id, actor_user_id=admin_user.id)
    now_cremato = await practice_repo.get_by_id(practice.id)
    assert now_cremato.status == PracticeStatus.cremato, "entrambi i cicli completi: ora la pratica e' cremato"

    # nessun animale perso/duplicato/orfano dopo tutto il flusso
    assert {an.id for an in now_cremato.animals} == {a, b, c}


async def test_revert_cycle_reverts_practice_to_in_programma(db_session, admin_user, sample_client, sample_location):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    animal_id = practice.animals[0].id
    await cremation_cycle_service.assign_animal(db_session, cycle.id, animal_id, actor_user_id=admin_user.id)
    await cremation_cycle_service.complete_cycle(db_session, cycle.id, actor_user_id=admin_user.id)

    reverted = await cremation_cycle_service.revert_cycle(db_session, cycle.id, "errore operatore", actor_user_id=admin_user.id)
    assert reverted.status == CremationCycleStatus.in_attesa
    assert reverted.completed_at is None

    from repositories.practice_repository import PracticeRepository

    reloaded_practice = await PracticeRepository(db_session).get_by_id(practice.id)
    assert reloaded_practice.status == PracticeStatus.in_programma
    assert reloaded_practice.cremation_registered is False


async def test_revert_cycle_requires_reason(db_session, admin_user, sample_client, sample_location):
    from schemas.cremation_cycle import RevertCycleRequest

    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    await cremation_cycle_service.assign_animal(db_session, cycle.id, practice.animals[0].id, actor_user_id=admin_user.id)
    await cremation_cycle_service.complete_cycle(db_session, cycle.id, actor_user_id=admin_user.id)

    # Pydantic stesso respinge un motivo vuoto (min_length=1) - verificato
    # strutturalmente, come per la correzione Pratica.
    with pytest.raises(Exception):
        RevertCycleRequest(reason="")


async def test_delete_completed_cycle_is_blocked(db_session, admin_user, sample_client, sample_location):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    await cremation_cycle_service.assign_animal(db_session, cycle.id, practice.animals[0].id, actor_user_id=admin_user.id)
    await cremation_cycle_service.complete_cycle(db_session, cycle.id, actor_user_id=admin_user.id)

    with pytest.raises(ValidationDomainError):
        await cremation_cycle_service.delete_cycle(db_session, cycle.id, actor_user_id=admin_user.id)


async def test_delete_empty_cycle_succeeds(db_session, admin_user):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    await cremation_cycle_service.delete_cycle(db_session, cycle.id, actor_user_id=admin_user.id)

    from repositories.cremation_cycle_repository import CremationCycleRepository

    assert await CremationCycleRepository(db_session).get_by_id(cycle.id) is None


async def test_only_cremazione_singola_animals_are_assignable(db_session, admin_user, sample_client, sample_location):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione collettiva",
            animals=[AnimalInput(name="Fido")],
        ),
        actor_user_id=admin_user.id,
    )
    with pytest.raises(ValidationDomainError):
        await cremation_cycle_service.assign_animal(db_session, cycle.id, practice.animals[0].id, actor_user_id=admin_user.id)


async def test_audit_records_assignment_and_completion(db_session, admin_user, sample_client, sample_location):
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await _create_eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    animal_id = practice.animals[0].id
    await cremation_cycle_service.assign_animal(db_session, cycle.id, animal_id, actor_user_id=admin_user.id)
    await cremation_cycle_service.complete_cycle(db_session, cycle.id, actor_user_id=admin_user.id)

    from sqlalchemy import select

    from models.audit_log import AuditLog

    animal_audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.entity_type == "animal", AuditLog.entity_id == animal_id))
    ).scalars().all()
    assert any(r.action == "assigned_to_cycle" for r in animal_audit)

    cycle_audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.entity_type == "cremation_cycle", AuditLog.entity_id == cycle.id))
    ).scalars().all()
    assert any(r.action == "completed" for r in cycle_audit)

    practice_audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.entity_type == "practice", AuditLog.entity_id == practice.id))
    ).scalars().all()
    side_effect_rows = [r for r in practice_audit if r.action == "state_changed" and r.new_value == "cremato"]
    assert len(side_effect_rows) == 1
    assert "automatico" in (side_effect_rows[0].reason or "")
