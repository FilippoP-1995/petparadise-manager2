from sqlalchemy.ext.asyncio import AsyncSession

from models.audit_log import AuditLog


class AuditRepository:
    """Unico punto di scrittura per audit_log - mai un INSERT diretto
    sparso nei service (doc06 'Audit trail - unificato')."""

    def __init__(self, session: AsyncSession):
        self._session = session

    def record(
        self,
        *,
        entity_type: str,
        entity_id: int,
        action: str,
        user_id: int | None,
        field_name: str | None = None,
        old_value: str | None = None,
        new_value: str | None = None,
        reason: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            user_id=user_id,
        )
        self._session.add(entry)
        return entry
