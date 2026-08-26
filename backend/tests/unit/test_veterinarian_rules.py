import pytest

from domain.errors import ValidationDomainError
from domain.veterinarian.rules import HoursInput, ensure_identifiable, ensure_valid_hours


def test_clinic_name_is_sufficient():
    ensure_identifiable("Ambulatorio Rossi", None)


def test_doctor_name_is_sufficient():
    ensure_identifiable(None, "Dott. Bianchi")


def test_no_name_at_all_is_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_identifiable(None, None)


def test_valid_hours_pass():
    ensure_valid_hours([HoursInput(day_of_week=0, closed=False), HoursInput(day_of_week=6, closed=True)])


def test_out_of_range_day_is_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_valid_hours([HoursInput(day_of_week=7, closed=False)])


def test_negative_day_is_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_valid_hours([HoursInput(day_of_week=-1, closed=False)])


def test_duplicate_day_is_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_valid_hours([HoursInput(day_of_week=2, closed=False), HoursInput(day_of_week=2, closed=True)])
