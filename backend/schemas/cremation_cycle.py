from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field

from models.cremation_cycle import CremationCycleStatus


class CremationCycleCreate(BaseModel):
    """Mai un campo status qui (stesso principio doc09 esteso a ogni
    entita' con FSM): un ciclo nasce sempre 'pianificato', con 0 animali
    (doc14 §4 grafo)."""

    cycle_date: date
    planned_start: time
    planned_end: time
    cremation_location_id: int | None = None


class CremationCycleUpdate(CremationCycleCreate):
    pass


class CycleAnimalRead(BaseModel):
    """Non lo stesso AnimalRead usato dentro Pratica: qui serve anche
    practice_id, per la visualizzazione coerente degli animali provenienti
    da pratiche con piu' animali (sezione esplicita della richiesta)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    species: str | None
    practice_id: int | None


class CremationCycleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: CremationCycleStatus
    cycle_date: date
    planned_start: time
    planned_end: time
    completed_at: datetime | None
    cremation_location_id: int | None
    sort_order: int
    created_at: datetime
    updated_at: datetime
    animals: list[CycleAnimalRead]


class AssignAnimalRequest(BaseModel):
    animal_id: int


class RemoveAnimalRequest(BaseModel):
    animal_id: int


class RevertCycleRequest(BaseModel):
    """Correzione (sezione 'ATTENZIONE ALLA SEMANTICA' della richiesta):
    motivo obbligatorio, audit dedicato - stesso rigore della correzione
    di stato Pratica."""

    reason: str = Field(min_length=1)
