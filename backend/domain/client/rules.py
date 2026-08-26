from domain.errors import ValidationDomainError


def ensure_identifiable(first_name: str | None, last_name: str | None, company_name: str | None) -> None:
    """Un cliente deve poter essere identificato: nome+cognome oppure
    ragione sociale. Nessuna delle due valorizzata non e' un cliente
    utilizzabile (es. su un DDT, su una fattura)."""
    has_person_name = bool((first_name or "").strip()) and bool((last_name or "").strip())
    has_company_name = bool((company_name or "").strip())
    if not has_person_name and not has_company_name:
        raise ValidationDomainError(
            "Il cliente deve avere nome e cognome, oppure una ragione sociale."
        )
