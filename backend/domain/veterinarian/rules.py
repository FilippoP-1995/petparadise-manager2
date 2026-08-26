from dataclasses import dataclass

from domain.errors import ValidationDomainError


def ensure_identifiable(clinic_name: str | None, doctor_name: str | None) -> None:
    """Un veterinario deve avere almeno un nome (clinica o medico) per
    essere selezionabile in una pratica/ritiro."""
    if not (clinic_name or "").strip() and not (doctor_name or "").strip():
        raise ValidationDomainError("Il veterinario deve avere almeno il nome della clinica o del medico.")


@dataclass(frozen=True)
class HoursInput:
    day_of_week: int
    closed: bool
    morning_start: str | None = None
    morning_end: str | None = None
    afternoon_start: str | None = None
    afternoon_end: str | None = None
    notes: str | None = None


def ensure_valid_hours(hours: list[HoursInput]) -> None:
    """Vincoli di dominio sugli orari: giorno della settimana valido (0-6),
    nessun giorno duplicato per lo stesso veterinario."""
    seen_days: set[int] = set()
    for entry in hours:
        if not 0 <= entry.day_of_week <= 6:
            raise ValidationDomainError(f"Giorno della settimana non valido: {entry.day_of_week} (atteso 0-6).")
        if entry.day_of_week in seen_days:
            raise ValidationDomainError(f"Giorno della settimana duplicato: {entry.day_of_week}.")
        seen_days.add(entry.day_of_week)
