"""doc15 decisione #11 (chiusa): il limite di 2 riflette la capacita'
fisica reale del forno - un vincolo fisico si applica a corpi fisici
(animali), non a righe amministrative. Regola di dominio, enforcement
backend obbligatorio, mai aggirabile via API diretta."""

from domain.errors import ValidationDomainError

MAX_ANIMALS_PER_CYCLE = 2

# doc03 FACT (comportamento gia' verificato in V1, tradotto a granularita'
# animale): solo pratiche 'Cremazione singola' partecipano al sistema
# cicli (le collettive sono escluse), e una pratica gia' 'consegnato' (o
# oltre) non deve piu' poter essere toccata dal ciclo.
ELIGIBLE_SERVICE_TYPE = "Cremazione singola"
INELIGIBLE_PRACTICE_STATUSES = {"consegnato", "smaltito"}


def ensure_capacity_available(current_animal_count: int) -> None:
    if current_animal_count >= MAX_ANIMALS_PER_CYCLE:
        raise ValidationDomainError(f"Il ciclo contiene gia' {MAX_ANIMALS_PER_CYCLE} animali (limite massimo).")


def ensure_animal_eligible(*, practice_service_type: str, practice_status: str) -> None:
    if practice_service_type != ELIGIBLE_SERVICE_TYPE:
        raise ValidationDomainError(
            f"Solo animali di pratiche con service_type='{ELIGIBLE_SERVICE_TYPE}' possono essere assegnati a un ciclo."
        )
    if practice_status in INELIGIBLE_PRACTICE_STATUSES:
        raise ValidationDomainError(f"La pratica e' gia' '{practice_status}': l'animale non e' piu' assegnabile a un ciclo.")


def ensure_not_locked_in_completed_cycle(current_cycle_status: str | None) -> None:
    """Gate Animali<->Cicli (round 2): 'DOPO il completamento il
    collegamento deve essere storico e non liberamente riassegnabile -
    eventuali correzioni passano esclusivamente dal percorso di
    correzione gia' definito nella macchina a stati'. Qui si blocca il
    tentativo di riassegnazione diretta; lo sblocco passa dal ripristino
    esplicito del vecchio ciclo (state_machine.validate_revert)."""
    if current_cycle_status == "completato":
        raise ValidationDomainError(
            "Questo animale e' gia' assegnato a un ciclo completato: il collegamento e' storico. "
            "Per correggerlo, ripristina prima il ciclo completato (azione di correzione, con motivo)."
        )
