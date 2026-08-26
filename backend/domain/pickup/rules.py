"""doc06 Addendum C (riscritto): 'niente viene mai dedotto' - ogni
pickup_type richiede il campo esplicito che lo rappresenta, mai un'
inferenza automatica da un altro campo (stesso principio gia' applicato al
circuito W/D)."""

from domain.errors import ValidationDomainError
from models.practice import PickupType


def ensure_pickup_fields_consistent(
    pickup_type: PickupType,
    *,
    pickup_location_id: int | None,
    pickup_zone_id: int | None,
    veterinarian_id: int | None,
    collaborator_id: int | None,
    pickup_contact_name: str | None,
) -> None:
    if pickup_type == PickupType.sede_aziendale and pickup_location_id is None:
        raise ValidationDomainError("pickup_type='sede_aziendale' richiede pickup_location_id.")
    if pickup_type == PickupType.domicilio and pickup_zone_id is None:
        raise ValidationDomainError("pickup_type='domicilio' richiede pickup_zone_id.")
    if pickup_type == PickupType.veterinario and veterinarian_id is None:
        raise ValidationDomainError("pickup_type='veterinario' richiede veterinarian_id.")
    if pickup_type == PickupType.collaboratore and collaborator_id is None:
        raise ValidationDomainError("pickup_type='collaboratore' richiede collaborator_id.")
    if pickup_type == PickupType.altro and not (pickup_contact_name or "").strip():
        raise ValidationDomainError("pickup_type='altro' richiede pickup_contact_name.")
