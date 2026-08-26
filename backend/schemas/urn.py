from datetime import datetime

from pydantic import BaseModel, ConfigDict

from models.urn import UrnCategory


class UrnCreate(BaseModel):
    category: UrnCategory = UrnCategory.urna
    name: str
    material: str | None = None
    price_cents: int = 0
    quantity: int = 0
    low_stock_threshold: int = 3
    notes: str | None = None


class UrnUpdate(UrnCreate):
    pass


class UrnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: UrnCategory
    name: str
    material: str | None
    internal_code: str
    price_cents: int
    quantity: int
    low_stock_threshold: int
    notes: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class UrnMovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    urn_id: int
    practice_id: int | None
    user_id: int | None
    movement_type: str
    quantity_delta: int
    old_quantity: int
    new_quantity: int
    note: str | None
    created_at: datetime
