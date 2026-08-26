import pytest

from domain.client.rules import ensure_identifiable
from domain.errors import ValidationDomainError


def test_person_name_is_sufficient():
    ensure_identifiable("Mario", "Rossi", None)  # non deve sollevare


def test_company_name_is_sufficient():
    ensure_identifiable(None, None, "Clinica Veterinaria SRL")  # non deve sollevare


def test_only_first_name_is_not_enough():
    with pytest.raises(ValidationDomainError):
        ensure_identifiable("Mario", None, None)


def test_only_last_name_is_not_enough():
    with pytest.raises(ValidationDomainError):
        ensure_identifiable(None, "Rossi", None)


def test_nothing_at_all_is_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_identifiable(None, None, None)


def test_blank_strings_are_treated_as_empty():
    with pytest.raises(ValidationDomainError):
        ensure_identifiable("   ", "   ", "   ")
