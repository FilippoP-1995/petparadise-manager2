from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ArticleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    active: bool


class ArticleOrderRead(BaseModel):
    """Vista arricchita (nome articolo + nome operatore), come la lista
    'Ultime richieste' di V1 (articles_page) - costruita esplicitamente
    dal service, non un semplice from_attributes su ArticleOrder."""

    id: int
    article_id: int
    article_name: str
    ordered_by: int
    ordered_by_name: str
    created_at: datetime
