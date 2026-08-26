import pytest

from domain.errors import InvalidTransitionError, ValidationDomainError
from domain.practice.state_machine import (
    WORKFLOW_TRANSITIONS,
    validate_correction_transition,
    validate_workflow_transition,
)
from models.practice import PracticeStatus

SINGLE = "Cremazione singola"
COLLECTIVE = "Cremazione collettiva"


@pytest.mark.parametrize("current,expected_next", list(WORKFLOW_TRANSITIONS.items()))
def test_every_declared_workflow_transition_is_valid(current, expected_next):
    # smaltito e' raggiungibile solo per service_type collettiva (doc14 §1)
    service_type = COLLECTIVE if expected_next == PracticeStatus.smaltito else SINGLE
    validate_workflow_transition(current, expected_next, service_type)  # non deve sollevare


def test_smaltito_via_workflow_rejected_for_non_collective_service_type():
    with pytest.raises(ValidationDomainError):
        validate_workflow_transition(PracticeStatus.consegnato, PracticeStatus.smaltito, SINGLE)


@pytest.mark.parametrize(
    "current,target",
    [
        (PracticeStatus.ritirato, PracticeStatus.cremato),  # salta in_programma
        (PracticeStatus.consegnato, PracticeStatus.ritirato),  # regressione
        (PracticeStatus.in_programma, PracticeStatus.in_programma),  # no-op
        (PracticeStatus.smaltito, PracticeStatus.ritirato),  # da stato terminale
    ],
)
def test_non_declared_transitions_are_rejected_as_workflow(current, target):
    with pytest.raises(InvalidTransitionError):
        validate_workflow_transition(current, target, COLLECTIVE)


def test_correction_allows_a_regression_with_reason():
    validate_correction_transition(PracticeStatus.consegnato, PracticeStatus.ritirato, SINGLE, "errore operatore")


def test_correction_allows_a_skip_with_reason():
    validate_correction_transition(PracticeStatus.ritirato, PracticeStatus.cremato, SINGLE, "dato storico migrato")


def test_correction_without_reason_is_rejected():
    with pytest.raises(ValidationDomainError):
        validate_correction_transition(PracticeStatus.consegnato, PracticeStatus.ritirato, SINGLE, None)


def test_correction_with_blank_reason_is_rejected():
    with pytest.raises(ValidationDomainError):
        validate_correction_transition(PracticeStatus.consegnato, PracticeStatus.ritirato, SINGLE, "   ")


def test_correction_to_the_same_state_is_rejected():
    with pytest.raises(ValidationDomainError):
        validate_correction_transition(PracticeStatus.ritirato, PracticeStatus.ritirato, SINGLE, "motivo qualsiasi")


def test_correction_still_enforces_domain_constraints():
    """doc14 §1: 'una correzione non e' un bypass delle regole, solo un
    bypass dell'ordine in avanti' - smaltito richiede sempre collettiva,
    anche via correzione."""
    with pytest.raises(ValidationDomainError):
        validate_correction_transition(PracticeStatus.ritirato, PracticeStatus.smaltito, SINGLE, "forzatura admin")

    validate_correction_transition(PracticeStatus.ritirato, PracticeStatus.smaltito, COLLECTIVE, "forzatura admin")
