import pytest

from domain.errors import ValidationDomainError
from domain.pickup.rules import ensure_pickup_fields_consistent
from models.practice import PickupType


def test_sede_aziendale_requires_location():
    with pytest.raises(ValidationDomainError):
        ensure_pickup_fields_consistent(
            PickupType.sede_aziendale,
            pickup_location_id=None,
            pickup_zone_id=None,
            veterinarian_id=None,
            collaborator_id=None,
            pickup_contact_name=None,
        )
    ensure_pickup_fields_consistent(
        PickupType.sede_aziendale,
        pickup_location_id=1,
        pickup_zone_id=None,
        veterinarian_id=None,
        collaborator_id=None,
        pickup_contact_name=None,
    )


def test_domicilio_requires_zone():
    with pytest.raises(ValidationDomainError):
        ensure_pickup_fields_consistent(
            PickupType.domicilio,
            pickup_location_id=None,
            pickup_zone_id=None,
            veterinarian_id=None,
            collaborator_id=None,
            pickup_contact_name=None,
        )


def test_veterinario_requires_veterinarian_id():
    with pytest.raises(ValidationDomainError):
        ensure_pickup_fields_consistent(
            PickupType.veterinario,
            pickup_location_id=None,
            pickup_zone_id=None,
            veterinarian_id=None,
            collaborator_id=None,
            pickup_contact_name=None,
        )


def test_collaboratore_requires_collaborator_id():
    with pytest.raises(ValidationDomainError):
        ensure_pickup_fields_consistent(
            PickupType.collaboratore,
            pickup_location_id=None,
            pickup_zone_id=None,
            veterinarian_id=None,
            collaborator_id=None,
            pickup_contact_name=None,
        )


def test_altro_requires_contact_name():
    with pytest.raises(ValidationDomainError):
        ensure_pickup_fields_consistent(
            PickupType.altro,
            pickup_location_id=None,
            pickup_zone_id=None,
            veterinarian_id=None,
            collaborator_id=None,
            pickup_contact_name="   ",
        )
    ensure_pickup_fields_consistent(
        PickupType.altro,
        pickup_location_id=None,
        pickup_zone_id=None,
        veterinarian_id=None,
        collaborator_id=None,
        pickup_contact_name="Zia del cliente",
    )
