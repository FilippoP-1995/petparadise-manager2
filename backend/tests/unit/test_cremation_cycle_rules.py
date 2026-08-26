import pytest

from domain.cremation_cycle.rules import (
    ensure_animal_eligible,
    ensure_capacity_available,
    ensure_not_locked_in_completed_cycle,
)
from domain.errors import ValidationDomainError


def test_capacity_available_under_two():
    ensure_capacity_available(0)
    ensure_capacity_available(1)  # non deve sollevare


def test_capacity_rejected_at_two():
    """doc15 decisione #11: limite fisico del forno, 2 animali."""
    with pytest.raises(ValidationDomainError):
        ensure_capacity_available(2)
    with pytest.raises(ValidationDomainError):
        ensure_capacity_available(3)


def test_only_cremazione_singola_is_eligible():
    ensure_animal_eligible(practice_service_type="Cremazione singola", practice_status="ritirato")
    with pytest.raises(ValidationDomainError):
        ensure_animal_eligible(practice_service_type="Cremazione collettiva", practice_status="ritirato")
    with pytest.raises(ValidationDomainError):
        ensure_animal_eligible(practice_service_type="Da decidere", practice_status="ritirato")


@pytest.mark.parametrize("status", ["consegnato", "smaltito"])
def test_practices_past_consegnato_are_not_eligible(status):
    with pytest.raises(ValidationDomainError):
        ensure_animal_eligible(practice_service_type="Cremazione singola", practice_status=status)


def test_not_locked_when_no_current_cycle_or_not_completed():
    ensure_not_locked_in_completed_cycle(None)
    ensure_not_locked_in_completed_cycle("pianificato")
    ensure_not_locked_in_completed_cycle("in_attesa")  # nessuno deve sollevare


def test_locked_when_current_cycle_is_completed():
    """Gate Animali<->Cicli (round 2): il collegamento a un ciclo
    completato e' storico, non riassegnabile direttamente."""
    with pytest.raises(ValidationDomainError):
        ensure_not_locked_in_completed_cycle("completato")
