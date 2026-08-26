import pytest

from domain.errors import InvalidTransitionError
from domain.pickup.state_machine import validate_transition
from models.calendar_event import PickupStatus


@pytest.mark.parametrize(
    "current,target",
    [
        (PickupStatus.da_confermare, PickupStatus.da_ritirare),
        (PickupStatus.da_confermare, PickupStatus.annullato),
        (PickupStatus.da_ritirare, PickupStatus.ritirato),
        (PickupStatus.da_ritirare, PickupStatus.annullato),
        (PickupStatus.ritirato, PickupStatus.annullato),
    ],
)
def test_declared_transitions_are_valid(current, target):
    validate_transition(current, target)  # non deve sollevare


@pytest.mark.parametrize(
    "current,target",
    [
        (PickupStatus.da_confermare, PickupStatus.ritirato),  # salta da_ritirare
        (PickupStatus.da_ritirare, PickupStatus.da_confermare),  # regressione
        (PickupStatus.ritirato, PickupStatus.da_confermare),  # regressione
        (PickupStatus.ritirato, PickupStatus.da_ritirare),  # regressione
    ],
)
def test_non_declared_transitions_are_rejected(current, target):
    with pytest.raises(InvalidTransitionError):
        validate_transition(current, target)


@pytest.mark.parametrize("target", list(PickupStatus))
def test_annullato_is_terminal_no_exit_transition_ever(target):
    """Sezione 6: 'annullato' non puo' essere riaperto, non puo' tornare a
    uno stato precedente, non puo' essere riutilizzato come se fosse
    attivo - verificato per OGNI possibile stato di destinazione, incluso
    se stesso."""
    with pytest.raises(InvalidTransitionError):
        validate_transition(PickupStatus.annullato, target)
