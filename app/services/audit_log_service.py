from __future__ import annotations

import json

from app import db
from app.models.audit_log import AuditLog
from app.services.settings_service import get_bool_setting


def audit_enabled() -> bool:
    return get_bool_setting("enable_audit_logs", default=True)


def log_audit_event(
    *,
    action: str,
    actor_user_id: int | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    metadata: dict | None = None,
) -> None:
    if not audit_enabled():
        return
    payload = None
    if metadata:
        try:
            payload = json.dumps(metadata, ensure_ascii=False)
        except Exception:
            payload = None
    db.session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=(action or "").strip()[:80] or "unknown",
            target_type=(target_type or "").strip()[:80] or None,
            target_id=None if target_id is None else str(target_id)[:120],
            metadata_json=payload,
        )
    )
