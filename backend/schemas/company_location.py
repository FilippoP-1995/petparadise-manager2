from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CompanyLocationCreate(BaseModel):
    name: str
    has_cremation_plant: bool = False


class CompanyLocationUpdate(CompanyLocationCreate):
    pass


class CompanyLocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    has_cremation_plant: bool
    active: bool
    created_at: datetime
    updated_at: datetime
