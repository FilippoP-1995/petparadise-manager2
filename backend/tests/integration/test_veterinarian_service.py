import pytest

from domain.errors import ValidationDomainError
from schemas.veterinarian import VeterinarianCreate, VeterinarianHoursInput, VeterinarianUpdate
from services import veterinarian_service


async def test_create_veterinarian_with_hours(db_session, admin_user):
    vet = await veterinarian_service.create_veterinarian(
        db_session,
        VeterinarianCreate(
            clinic_name="Ambulatorio Test",
            hours=[VeterinarianHoursInput(day_of_week=0, closed=False), VeterinarianHoursInput(day_of_week=6, closed=True)],
        ),
        actor_user_id=admin_user.id,
    )

    assert vet.id is not None
    assert len(vet.hours) == 2


async def test_create_veterinarian_with_duplicate_day_is_rejected(db_session, admin_user):
    with pytest.raises(ValidationDomainError):
        await veterinarian_service.create_veterinarian(
            db_session,
            VeterinarianCreate(
                clinic_name="Ambulatorio",
                hours=[VeterinarianHoursInput(day_of_week=1, closed=False), VeterinarianHoursInput(day_of_week=1, closed=True)],
            ),
            actor_user_id=admin_user.id,
        )


async def test_update_veterinarian_replaces_hours_entirely(db_session, admin_user):
    vet = await veterinarian_service.create_veterinarian(
        db_session,
        VeterinarianCreate(clinic_name="Ambulatorio", hours=[VeterinarianHoursInput(day_of_week=0, closed=False)]),
        actor_user_id=admin_user.id,
    )

    updated = await veterinarian_service.update_veterinarian(
        db_session,
        vet.id,
        VeterinarianUpdate(
            clinic_name="Ambulatorio",
            hours=[VeterinarianHoursInput(day_of_week=2, closed=False), VeterinarianHoursInput(day_of_week=3, closed=True)],
        ),
        actor_user_id=admin_user.id,
    )

    assert {h.day_of_week for h in updated.hours} == {2, 3}
