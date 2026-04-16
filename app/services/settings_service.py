from __future__ import annotations

from app import db
from app.models.system_setting import SystemSetting


def get_setting(key: str, default: str | None = None) -> str | None:
    setting = SystemSetting.query.filter_by(key=key).first()
    if not setting:
        return default
    return setting.value_text


def get_bool_setting(key: str, default: bool = False) -> bool:
    value = get_setting(key, None)
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "on", "yes", "si"}


def set_setting(key: str, value: str | bool | int | None) -> None:
    setting = SystemSetting.query.filter_by(key=key).first()
    if setting is None:
        setting = SystemSetting(key=key)
    setting.value_text = "" if value is None else str(value)
    db.session.add(setting)
