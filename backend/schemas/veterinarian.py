from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field


class VeterinarianHoursInput(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    closed: bool = False
    morning_start: time | None = None
    morning_end: time | None = None
    afternoon_start: time | None = None
    afternoon_end: time | None = None
    notes: str | None = None


class VeterinarianHoursRead(VeterinarianHoursInput):
    model_config = ConfigDict(from_attributes=True)

    id: int


class VeterinarianCreate(BaseModel):
    clinic_name: str | None = None
    doctor_name: str | None = None
    short_name: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    notes: str | None = None
    service_duration_minutes: int | None = None
    hours: list[VeterinarianHoursInput] = Field(default_factory=list)


class VeterinarianUpdate(VeterinarianCreate):
    pass


class VeterinarianRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_name: str | None
    doctor_name: str | None
    short_name: str | None
    phone: str | None
    address: str | None
    city: str | None
    notes: str | None
    active: bool
    service_duration_minutes: int | None
    created_at: datetime
    updated_at: datetime
    hours: list[VeterinarianHoursRead]
