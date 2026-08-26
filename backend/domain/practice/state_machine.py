"""doc14 §1 (Pratica - practice_status) + doc09 'Macchine a stati esplicite'.

Tabella esplicita delle transizioni, mai un `if` sparso. Due percorsi
distinti, non un solo meccanismo con un flag runtime:

- workflow (transition_status): solo la prossima transizione dichiarata,
  chiamabile da Operator o Admin, nessun motivo richiesto.
- correzione (correct_status): qualunque altra transizione tra due stati
  validi dell'enum, chiamabile SOLO da Admin (verificato a livello di route
  via require_role - qui il dominio non conosce ruoli), motivo obbligatorio.

In entrambi i casi restano validi i vincoli di dominio (es. 'smaltito' solo
se service_type='Cremazione collettiva') - una correzione non e' un bypass
delle regole, solo un bypass dell'ordine "in avanti" (doc14 esplicito)."""

from domain.errors import InvalidTransitionError, ValidationDomainError
from models.practice import PracticeStatus

# Unico target per stato sorgente: il workflow e' una catena lineare, non
# un grafo con piu' uscite possibili (doc14 §1, grafo proposto).
WORKFLOW_TRANSITIONS: dict[PracticeStatus, PracticeStatus] = {
    PracticeStatus.ritirato: PracticeStatus.in_programma,
    PracticeStatus.in_programma: PracticeStatus.cremato,
    PracticeStatus.cremato: PracticeStatus.da_consegnare,
    PracticeStatus.da_consegnare: PracticeStatus.consegnato,
    PracticeStatus.consegnato: PracticeStatus.smaltito,
}

COLLECTIVE_SERVICE_TYPE = "Cremazione collettiva"


def check_domain_constraints(target_status: PracticeStatus, service_type: str) -> None:
    """Vincoli di dominio che restano validi indipendentemente dal fatto che
    la transizione sia workflow o correzione (doc14 §1, tabella 'Vincoli
    residui')."""
    if target_status == PracticeStatus.smaltito and service_type != COLLECTIVE_SERVICE_TYPE:
        raise ValidationDomainError(
            "Lo stato 'smaltito' e' ammesso solo per pratiche con service_type='Cremazione collettiva'."
        )


def validate_workflow_transition(current_status: PracticeStatus, target_status: PracticeStatus, service_type: str) -> None:
    expected_next = WORKFLOW_TRANSITIONS.get(current_status)
    if expected_next is None or expected_next != target_status:
        raise InvalidTransitionError(
            f"Transizione di workflow non valida da '{current_status.value}' a '{target_status.value}'. "
            "Se e' una correzione intenzionale, usa l'azione di correzione (solo Admin, motivo obbligatorio)."
        )
    check_domain_constraints(target_status, service_type)


def validate_correction_transition(
    current_status: PracticeStatus, target_status: PracticeStatus, service_type: str, reason: str | None
) -> None:
    if not reason or not reason.strip():
        raise ValidationDomainError("Una correzione di stato richiede un motivo obbligatorio.")
    if current_status == target_status:
        raise ValidationDomainError("Lo stato indicato coincide con quello attuale: non e' una correzione.")
    check_domain_constraints(target_status, service_type)
