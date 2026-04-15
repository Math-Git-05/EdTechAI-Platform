from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text

from app import db


def _table_columns(table_name: str) -> set[str]:
    inspector = inspect(db.engine)
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upsert_teacher_observation(
    *,
    student_id: int,
    teacher_id: int | None,
    observation_text: str | None,
    observed_at: Any = None,
) -> None:
    columns = _table_columns("teacher_observations")
    if not columns:
        return

    comment = (observation_text or "").strip()
    if not comment:
        return
    if "student_id" not in columns:
        return

    payload: dict[str, Any] = {"student_id": int(student_id)}
    if "teacher_id" in columns:
        payload["teacher_id"] = int(teacher_id) if teacher_id is not None else None
    if "teacher_comment" in columns:
        payload["teacher_comment"] = comment
    if "observation" in columns:
        payload["observation"] = comment
    if "observed_at" in columns:
        payload["observed_at"] = observed_at or db.func.current_timestamp()

    exists = db.session.execute(
        text("SELECT COUNT(1) AS total FROM teacher_observations WHERE student_id = :student_id"),
        {"student_id": int(student_id)},
    ).scalar() or 0

    if int(exists) > 0:
        update_cols = [column for column in payload.keys() if column != "student_id"]
        if not update_cols:
            return
        update_clause = ", ".join(f"{column} = :{column}" for column in update_cols)
        params = {column: payload[column] for column in update_cols}
        params["student_id"] = int(student_id)
        db.session.execute(
            text(f"UPDATE teacher_observations SET {update_clause} WHERE student_id = :student_id"),
            params,
        )
        return

    insert_cols = list(payload.keys())
    insert_sql_cols = ", ".join(insert_cols)
    insert_sql_vals = ", ".join(f":{column}" for column in insert_cols)
    db.session.execute(
        text(f"INSERT INTO teacher_observations ({insert_sql_cols}) VALUES ({insert_sql_vals})"),
        payload,
    )
