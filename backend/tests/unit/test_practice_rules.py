import pytest

from domain.errors import ValidationDomainError
from domain.practice.rules import build_owner_snapshot, ensure_direct_creation_origin, ensure_valid_service_type


def test_valid_service_types_accepted():
    for value in ("Da decidere", "Cremazione singola", "Cremazione collettiva"):
        ensure_valid_service_type(value)  # non deve sollevare


def test_invalid_service_type_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_valid_service_type("Cremazione extra-lusso")


@pytest.mark.parametrize("origin", ["Collaboratore", "Consegna in sede"])
def test_direct_creation_allowed_for_percorso_b_origins(origin):
    ensure_direct_creation_origin(origin)  # non deve sollevare


@pytest.mark.parametrize("origin", ["Privato", "Veterinario"])
def test_direct_creation_rejected_for_percorso_a_origins(origin):
    """doc06 'Relazione Ritiro -> Pratica': Privato/Veterinario nascono dal
    Percorso A (da Ritiro), non disponibile in questa fase - non devono
    poter essere create direttamente finche' quel dominio non esiste."""
    with pytest.raises(ValidationDomainError):
        ensure_direct_creation_origin(origin)


class _FakeClient:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_owner_snapshot_captures_all_expected_fields():
    client = _FakeClient(
        first_name="Anna",
        last_name="Verdi",
        company_name=None,
        phone="333",
        phone_2=None,
        email="a@b.it",
        tax_code=None,
        vat_number=None,
        street="Via Roma 1",
        city="Livorno",
        province="LI",
        zip="57100",
        address=None,
        notes="cliente storico",
    )
    snapshot = build_owner_snapshot(client)
    assert snapshot == {
        "first_name": "Anna",
        "last_name": "Verdi",
        "company_name": None,
        "phone": "333",
        "phone_2": None,
        "email": "a@b.it",
        "tax_code": None,
        "vat_number": None,
        "street": "Via Roma 1",
        "city": "Livorno",
        "province": "LI",
        "zip": "57100",
        "address": None,
        "notes": "cliente storico",
    }
