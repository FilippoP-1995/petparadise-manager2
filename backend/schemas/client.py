from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ClientCreate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    company_name: str | None = None
    phone: str | None = None
    phone_2: str | None = None
    email: str | None = None
    tax_code: str | None = None
    vat_number: str | None = None
    street: str | None = None
    city: str | None = None
    province: str | None = None
    zip: str | None = None
    address: str | None = None
    notes: str | None = None


class ClientUpdate(ClientCreate):
    pass


class ClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str | None
    last_name: str | None
    company_name: str | None
    phone: str | None
    phone_2: str | None
    email: str | None
    tax_code: str | None
    vat_number: str | None
    street: str | None
    city: str | None
    province: str | None
    zip: str | None
    address: str | None
    notes: str | None
    active: bool
    created_at: datetime
    updated_at: datetime
