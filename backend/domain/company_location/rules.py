from domain.errors import ValidationDomainError


def ensure_name_valid(name: str) -> None:
    """Una sede senza nome non e' selezionabile in nessun form (Pratica,
    Ritiro, Riconsegna, Ciclo di cremazione) che la referenzia."""
    if not (name or "").strip():
        raise ValidationDomainError("Il nome della sede e' obbligatorio.")
