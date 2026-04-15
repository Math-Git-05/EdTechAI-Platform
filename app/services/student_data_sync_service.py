from __future__ import annotations

from datetime import date, datetime
from typing import Any

from flask import current_app, has_app_context
from sqlalchemy import inspect, text

from app import db


def _to_date_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _resolve_student_data_table_name() -> str | None:
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if "students_data" in table_names:
        return "students_data"
    if "student_data" in table_names:
        return "student_data"
    return None


def _fallback_required_value(column_name: str, column_type: Any, user, student_profile) -> Any:
    normalized = (column_name or "").strip().lower()

    if "password" in normalized:
        return getattr(user, "password", "") or ""
    if normalized in {"first_name", "nombre"}:
        return (getattr(user, "nombre", "") or "").strip() or "SinNombre"
    if normalized in {"last_name", "apellido"}:
        return (getattr(user, "apellido", "") or "").strip() or "SinApellido"
    if normalized in {"email"}:
        return (getattr(user, "email", "") or "").strip() or f"user{getattr(user, 'id', 0)}@local"
    if normalized in {"student_id", "matricula", "rne"}:
        return (getattr(student_profile, "student_id", "") or "").strip() or None
    if normalized in {"role"}:
        return (getattr(user, "role", "") or "estudiante").strip() or "estudiante"
    if normalized in {"status", "academic_status"}:
        return (getattr(student_profile, "academic_status", "") or "activo").strip() or "activo"
    if normalized in {"school_id"}:
        return (getattr(student_profile, "school_id", "") or "").strip() or "10-03-00092"
    if normalized in {"grade_level", "grado_nivel"}:
        return (getattr(student_profile, "grado_nivel", "") or "").strip() or "N/A"
    if normalized in {"gender", "genero"}:
        return (getattr(student_profile, "genero", "") or "").strip() or "prefiero_no_decir"
    if normalized in {"date_of_birth", "fecha_nacimiento"}:
        birth_date = getattr(student_profile, "fecha_nacimiento", None)
        return _to_date_string(birth_date) if birth_date else None
    if normalized in {"active", "is_active", "activo", "enabled"}:
        return bool(getattr(user, "activo", True))
    if normalized in {"created_at", "updated_at", "fecha_creacion", "fecha_actualizacion"}:
        return datetime.utcnow()

    python_type = None
    try:
        python_type = column_type.python_type
    except Exception:
        python_type = None

    if python_type is str:
        return ""
    if python_type is int:
        return 0
    if python_type is float:
        return 0.0
    if python_type is bool:
        return False
    if python_type is date:
        return date.today()
    if python_type is datetime:
        return datetime.utcnow()
    return None


def sync_student_data_if_exists(user, student_profile) -> None:
    if has_app_context() and not current_app.config.get("ENABLE_STUDENT_DATA_SHADOW_SYNC", False):
        return

    table_name = _resolve_student_data_table_name()
    if not table_name:
        return

    inspector = inspect(db.engine)
    column_defs = inspector.get_columns(table_name)
    if not column_defs:
        return
    columns = {column["name"] for column in column_defs}
    columns_by_name = {column["name"]: column for column in column_defs}

    values_map: dict[str, Any] = {
        "user_id": user.id,
        "student_id": student_profile.student_id,
        "matricula": student_profile.student_id,
        "rne": student_profile.student_id,
        "first_name": user.nombre,
        "nombre": user.nombre,
        "last_name": user.apellido,
        "apellido": user.apellido,
        "email": user.email,
        "role": user.role,
        "password": user.password,
        "password_hash": user.password,
        "gender": student_profile.genero,
        "genero": student_profile.genero,
        "date_of_birth": _to_date_string(student_profile.fecha_nacimiento),
        "fecha_nacimiento": _to_date_string(student_profile.fecha_nacimiento),
        "age": student_profile.edad,
        "edad": student_profile.edad,
        "grade_level": student_profile.grado_nivel,
        "grado_nivel": student_profile.grado_nivel,
        "school_id": student_profile.school_id,
        "enrollment_year": student_profile.enrollment_year,
        "status": student_profile.academic_status,
        "academic_status": student_profile.academic_status,
        "interest_technology": getattr(student_profile, "interest_technology", None),
        "interest_design": getattr(student_profile, "interest_design", None),
        "interest_business": getattr(student_profile, "interest_business", None),
        "interest_health": getattr(student_profile, "interest_health", None),
        "active": bool(getattr(user, "activo", True)),
        "is_active": bool(getattr(user, "activo", True)),
        "activo": bool(getattr(user, "activo", True)),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "fecha_creacion": datetime.utcnow(),
        "fecha_actualizacion": datetime.utcnow(),
    }

    updatable_columns = [column["name"] for column in column_defs if column["name"] in values_map]
    if not updatable_columns:
        return

    identifier_column = None
    if "user_id" in columns:
        identifier_column = "user_id"
    elif "student_id" in columns:
        identifier_column = "student_id"
    elif "matricula" in columns:
        identifier_column = "matricula"
    elif "rne" in columns:
        identifier_column = "rne"
    if not identifier_column:
        return

    params = {column: values_map.get(column) for column in updatable_columns}
    identifier_value = values_map.get(identifier_column)
    if identifier_value in {None, ""}:
        return
    params["_identifier"] = identifier_value

    exists_stmt = text(
        f"SELECT COUNT(1) AS total FROM {table_name} WHERE {identifier_column} = :_identifier"
    )
    total = db.session.execute(exists_stmt, params).scalar() or 0

    if total:
        set_columns = []
        for column in updatable_columns:
            if column == identifier_column:
                continue
            value = params.get(column)
            col_def = columns_by_name.get(column, {})
            if value is None and not col_def.get("nullable", True):
                continue
            set_columns.append(column)
        if not set_columns:
            return
        set_clause = ", ".join(f"{column} = :{column}" for column in set_columns)
        update_stmt = text(
            f"UPDATE {table_name} SET {set_clause} WHERE {identifier_column} = :_identifier"
        )
        db.session.execute(update_stmt, params)
    else:
        insert_values: dict[str, Any] = {}
        insert_columns_list: list[str] = []
        for column in updatable_columns:
            col_def = columns_by_name.get(column, {})
            value = params.get(column)
            if value is None and not col_def.get("nullable", True):
                value = _fallback_required_value(column, col_def.get("type"), user, student_profile)
            if value is None and col_def.get("default") is not None:
                continue
            if value is None and not col_def.get("nullable", True):
                return
            insert_columns_list.append(column)
            insert_values[column] = value

        for column_def in column_defs:
            name = column_def["name"]
            if name in insert_values:
                continue
            if column_def.get("nullable", True):
                continue
            if column_def.get("default") is not None:
                continue
            if name == "id":
                continue
            if column_def.get("autoincrement"):
                continue
            if column_def.get("identity"):
                continue
            fallback = _fallback_required_value(name, column_def.get("type"), user, student_profile)
            if fallback is None:
                return
            insert_columns_list.append(name)
            insert_values[name] = fallback

        if not insert_columns_list:
            return

        insert_columns = ", ".join(insert_columns_list)
        insert_params = ", ".join(f":{column}" for column in insert_columns_list)
        insert_stmt = text(
            f"INSERT INTO {table_name} ({insert_columns}) VALUES ({insert_params})"
        )
        db.session.execute(insert_stmt, insert_values)


def delete_student_data_if_exists(*, user_id: int | None = None, student_id: str | None = None) -> None:
    table_name = _resolve_student_data_table_name()
    if not table_name:
        return

    inspector = inspect(db.engine)
    column_defs = inspector.get_columns(table_name)
    if not column_defs:
        return

    columns = {column["name"] for column in column_defs}
    filters = []
    params: dict[str, Any] = {}

    if user_id is not None and "user_id" in columns:
        filters.append("user_id = :user_id")
        params["user_id"] = int(user_id)

    normalized_student_id = (student_id or "").strip()
    if normalized_student_id:
        if "student_id" in columns:
            filters.append("student_id = :student_id")
            params["student_id"] = normalized_student_id
        if "matricula" in columns:
            filters.append("matricula = :matricula")
            params["matricula"] = normalized_student_id
        if "rne" in columns:
            filters.append("rne = :rne")
            params["rne"] = normalized_student_id

    if not filters:
        return

    where_clause = " OR ".join(filters)
    delete_stmt = text(f"DELETE FROM {table_name} WHERE {where_clause}")
    db.session.execute(delete_stmt, params)
