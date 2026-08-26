class DomainError(Exception):
    """Errore di dominio - mai un'eccezione generica. Lo strato api/ lo
    traduce in una risposta HTTP appropriata, mai in un 500 non gestito."""


class ValidationDomainError(DomainError):
    """Un input viola una regola di dominio (non solo di formato - quello
    lo valida gia' Pydantic prima di arrivare qui)."""


class NotFoundError(DomainError):
    pass


class PermissionDomainError(DomainError):
    pass


class InvalidTransitionError(DomainError):
    """doc09 'Macchine a stati esplicite': un tentativo di transizione non
    presente nella tabella delle transizioni permesse - mai un 'non succede
    nulla' silenzioso."""
