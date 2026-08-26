"""doc06 'PRATICA - struttura completa V2' + 'Relazione Ritiro -> Pratica'.

Regole pure di dominio (nessuna dipendenza da FastAPI/DB), verificate su
dati gia' caricati - non fanno query, il service layer si occupa di
caricare cio' che serve prima di chiamare queste funzioni."""

from domain.errors import ValidationDomainError

VALID_SERVICE_TYPES = {"Da decidere", "Cremazione singola", "Cremazione collettiva"}

# doc06 'Relazione Ritiro -> Pratica': Percorso A (da Ritiro) e Percorso B
# (diretto, Collaboratore/Consegna in sede) sono le UNICHE due modalita' di
# creazione ammesse. Il dominio Ritiro (calendar_events) non e' ancora
# costruito in V2 in questa fase - quindi solo il Percorso B e' raggiungibile
# dall'API in questo momento. Non e' un'invenzione: e' esattamente cio' che
# doc06 descrive come legittimo per la creazione diretta, nient'altro.
DIRECT_CREATION_ORIGINS = {"Collaboratore", "Consegna in sede"}


def ensure_valid_service_type(service_type: str) -> None:
    if service_type not in VALID_SERVICE_TYPES:
        raise ValidationDomainError(
            f"service_type non valido: '{service_type}'. Valori ammessi: {sorted(VALID_SERVICE_TYPES)}."
        )


def ensure_direct_creation_origin(request_origin: str) -> None:
    """doc06 'Percorso B (diretto)': solo Collaboratore/Consegna in sede
    possono nascere senza un Ritiro di origine. Le pratiche 'Privato'/
    'Veterinario' nascono dal Percorso A (Ritiro -> Pratica, non ancora
    disponibile in questa fase - vedi report di fine dominio)."""
    if request_origin not in DIRECT_CREATION_ORIGINS:
        raise ValidationDomainError(
            f"Creazione diretta non ammessa per request_origin='{request_origin}'. "
            f"Ammessi solo: {sorted(DIRECT_CREATION_ORIGINS)} (Percorso B). "
            "Le pratiche con altra origine nascono da un Ritiro (Percorso A, dominio non ancora disponibile)."
        )


def effective_total_cents(practice) -> int:
    """doc06 Addendum D: se presente, l'override manuale e' il totale
    ufficiale (mai una sovrascrittura silenziosa del calcolo automatico -
    entrambi restano sempre disponibili, ma questo e' 'il' totale quando
    serve un solo numero, es. per la riconciliazione doc06 Addendum P).
    Riusato dal dominio Riconsegna (domain/delivery/rules.py) per lo stesso
    identico motivo per cui e' gia' usato altrove - non ricalcolato in
    parallelo con una propria formula."""
    if practice.computed_total_override_cents is not None:
        return practice.computed_total_override_cents
    return sum(li.amount_cents for li in practice.line_items)


def build_owner_snapshot(client) -> dict:
    """doc06 Addendum A: scattato UNA VOLTA alla creazione, mai piu'
    riscritto. Usa i campi realmente presenti sul dominio Clienti V2 (vedi
    schemas/client.py) - non le 16 colonne owner_* di V1, che sono un
    dettaglio del layout della PRATICA in V1, non dello schema clients V2."""
    return {
        "first_name": client.first_name,
        "last_name": client.last_name,
        "company_name": client.company_name,
        "phone": client.phone,
        "phone_2": client.phone_2,
        "email": client.email,
        "tax_code": client.tax_code,
        "vat_number": client.vat_number,
        "street": client.street,
        "city": client.city,
        "province": client.province,
        "zip": client.zip,
        "address": client.address,
        "notes": client.notes,
    }
