import pytest

from domain.delivery.rules import ensure_delivery_fields_consistent, preliminary_payment_diverges
from domain.errors import ValidationDomainError
from models.calendar_event import DeliveryType


def test_ambulatorio_requires_veterinarian():
    with pytest.raises(ValidationDomainError):
        ensure_delivery_fields_consistent(
            DeliveryType.ambulatorio,
            delivery_veterinarian_id=None,
            delivery_zone_id=None,
            delivery_location_id=None,
            delivery_address=None,
        )


def test_domicilio_requires_zone():
    with pytest.raises(ValidationDomainError):
        ensure_delivery_fields_consistent(
            DeliveryType.domicilio,
            delivery_veterinarian_id=None,
            delivery_zone_id=None,
            delivery_location_id=None,
            delivery_address=None,
        )


def test_sede_aziendale_requires_location():
    with pytest.raises(ValidationDomainError):
        ensure_delivery_fields_consistent(
            DeliveryType.sede_aziendale,
            delivery_veterinarian_id=None,
            delivery_zone_id=None,
            delivery_location_id=None,
            delivery_address=None,
        )


def test_altro_requires_address():
    with pytest.raises(ValidationDomainError):
        ensure_delivery_fields_consistent(
            DeliveryType.altro,
            delivery_veterinarian_id=None,
            delivery_zone_id=None,
            delivery_location_id=None,
            delivery_address=None,
        )


def test_preliminary_payment_diverges_only_when_amounts_differ():
    assert preliminary_payment_diverges(None, 10000) is False
    assert preliminary_payment_diverges(10000, 10000) is False
    assert preliminary_payment_diverges(9000, 10000) is True
