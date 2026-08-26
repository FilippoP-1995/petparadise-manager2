import pytest

from domain.errors import InvalidTransitionError, ValidationDomainError
from domain.cremation_cycle.state_machine import (
    derive_status_after_count_change,
    ensure_deletable,
    validate_completion,
    validate_revert,
)
from models.cremation_cycle import CremationCycleStatus


def test_zero_animals_derives_pianificato():
    assert derive_status_after_count_change(CremationCycleStatus.pianificato, 0) == CremationCycleStatus.pianificato
    assert derive_status_after_count_change(CremationCycleStatus.in_attesa, 0) == CremationCycleStatus.pianificato


def test_one_or_more_animals_derives_in_attesa():
    assert derive_status_after_count_change(CremationCycleStatus.pianificato, 1) == CremationCycleStatus.in_attesa
    assert derive_status_after_count_change(CremationCycleStatus.in_attesa, 2) == CremationCycleStatus.in_attesa


def test_completed_cycle_never_auto_derives():
    with pytest.raises(InvalidTransitionError):
        derive_status_after_count_change(CremationCycleStatus.completato, 0)


def test_completion_requires_in_attesa():
    validate_completion(CremationCycleStatus.in_attesa)  # non deve sollevare
    with pytest.raises(InvalidTransitionError):
        validate_completion(CremationCycleStatus.pianificato)
    with pytest.raises(InvalidTransitionError):
        validate_completion(CremationCycleStatus.completato)


def test_revert_requires_completato():
    validate_revert(CremationCycleStatus.completato)  # non deve sollevare
    with pytest.raises(InvalidTransitionError):
        validate_revert(CremationCycleStatus.in_attesa)
    with pytest.raises(InvalidTransitionError):
        validate_revert(CremationCycleStatus.pianificato)


def test_completed_cycle_is_not_deletable():
    """doc14 §4: corregge il comportamento V1 (dove era permesso)."""
    with pytest.raises(ValidationDomainError):
        ensure_deletable(CremationCycleStatus.completato)
    ensure_deletable(CremationCycleStatus.pianificato)
    ensure_deletable(CremationCycleStatus.in_attesa)
