"""doc14 §2 (Ritiro - pickup_status). A differenza della Pratica, qui non
esiste un livello 'correzione eccezionale' distinto dal workflow: doc14 non
ne definisce uno per il Ritiro, quindi non ne viene inventato uno - le
uniche transizioni valide sono quelle del grafo dichiarato qui, per
Operator o Admin indistintamente (nessuna transizione riservata
all'Admin), MAI un bypass. 'annullato' e' terminale per costruzione: il
suo insieme di transizioni uscenti e' vuoto."""

from domain.errors import InvalidTransitionError
from models.calendar_event import PickupStatus

ALLOWED_TRANSITIONS: dict[PickupStatus, frozenset[PickupStatus]] = {
    PickupStatus.da_confermare: frozenset({PickupStatus.da_ritirare, PickupStatus.annullato}),
    PickupStatus.da_ritirare: frozenset({PickupStatus.ritirato, PickupStatus.annullato}),
    PickupStatus.ritirato: frozenset({PickupStatus.annullato}),
    PickupStatus.annullato: frozenset(),
}


def validate_transition(current_status: PickupStatus, target_status: PickupStatus) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed:
        reason = (
            "'annullato' e' uno stato terminale, nessuna transizione in uscita e' mai permessa"
            if current_status == PickupStatus.annullato
            else f"transizione non valida da '{current_status.value}' a '{target_status.value}'"
        )
        raise InvalidTransitionError(f"Ritiro: {reason}.")
