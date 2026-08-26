"""doc14 §4 (Ciclo di cremazione) + Gate Animali<->Cicli (round 2). Due
tipi di transizione, non uno:

1. AUTOMATICHE (pianificato<->in_attesa): mai un'azione diretta
   dell'operatore, sempre una conseguenza deterministica del conteggio di
   animali effettivamente assegnati - derive_status_after_count_change().
2. ESPLICITE (in_attesa->completato, completato->in_attesa 'correzione'):
   azioni dirette, validate qui."""

from domain.errors import InvalidTransitionError, ValidationDomainError
from models.cremation_cycle import CremationCycleStatus


def derive_status_after_count_change(current_status: CremationCycleStatus, animal_count: int) -> CremationCycleStatus:
    if current_status == CremationCycleStatus.completato:
        raise InvalidTransitionError(
            "Un ciclo completato non cambia stato automaticamente in base agli animali assegnati "
            "- serve prima un ripristino esplicito (correzione)."
        )
    return CremationCycleStatus.pianificato if animal_count == 0 else CremationCycleStatus.in_attesa


def validate_completion(current_status: CremationCycleStatus) -> None:
    if current_status != CremationCycleStatus.in_attesa:
        raise InvalidTransitionError(
            f"Un ciclo puo' essere completato solo da 'in_attesa' (stato attuale: '{current_status.value}')."
        )


def validate_revert(current_status: CremationCycleStatus) -> None:
    if current_status != CremationCycleStatus.completato:
        raise InvalidTransitionError(
            f"Il ripristino e' ammesso solo da 'completato' (stato attuale: '{current_status.value}')."
        )


def ensure_deletable(status: CremationCycleStatus) -> None:
    """doc14 §4: 'Eliminazione del ciclo mentre completato - VIETATA per
    costruzione' (corregge il comportamento V1, dove era permessa)."""
    if status == CremationCycleStatus.completato:
        raise ValidationDomainError(
            "Un ciclo completato non puo' essere eliminato - ripristinalo esplicitamente prima, se necessario."
        )
