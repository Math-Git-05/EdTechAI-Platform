from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text

from app import db


TRACK_ALIASES = {
    "inf": ("informatica", "tecnico en informatica"),
    "enf": ("enfermeria", "tecnico en enfermeria"),
    "adm": ("administracion", "tecnico en administracion"),
    "com": ("comercio", "tecnico en comercio"),
}


def _normalize_label(value: Any) -> str:
    return str(value or "").strip().lower()


def _resolve_track_key(track_name: Any) -> str | None:
    normalized = _normalize_label(track_name)
    if not normalized:
        return None
    for key, aliases in TRACK_ALIASES.items():
        if normalized == key:
            return key
        for alias in aliases:
            if alias in normalized:
                return key
    return None


def _table_columns(table_name: str) -> set[str]:
    inspector = inspect(db.engine)
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _probability_payload_from_recommendation(recommendation: dict[str, Any] | None) -> tuple[str | None, dict[str, float]]:
    ranking = (recommendation or {}).get("ranking") or []
    raw_scores = {"inf": 0.0, "enf": 0.0, "adm": 0.0, "com": 0.0}
    recommended_track_name = None

    if ranking:
        recommended_track_name = ranking[0].get("track_name")

    for row in ranking:
        key = _resolve_track_key(row.get("track_key") or row.get("track_name"))
        if not key:
            continue
        try:
            raw_scores[key] = max(0.0, float(row.get("score") or 0.0))
        except (TypeError, ValueError):
            raw_scores[key] = 0.0

    total = sum(raw_scores.values())
    if total > 0:
        probabilities = {key: round((value / total) * 100.0, 4) for key, value in raw_scores.items()}
    else:
        probabilities = raw_scores

    return recommended_track_name, probabilities


def upsert_ai_prediction_for_evaluacion(
    *,
    student_id: int,
    evaluacion_id: int | None,
    recommendation: dict[str, Any] | None,
    model_version: str = "recommendation_engine_v1",
) -> None:
    columns = _table_columns("ai_predictions")
    if not columns:
        return

    recommended_career, probs = _probability_payload_from_recommendation(recommendation)

    payload: dict[str, Any] = {}
    if "student_id" in columns:
        payload["student_id"] = int(student_id)
    if "response_id" in columns:
        payload["response_id"] = int(evaluacion_id) if evaluacion_id is not None else None
    if "recommended_career" in columns:
        payload["recommended_career"] = recommended_career
    if "prob_informatica" in columns:
        payload["prob_informatica"] = probs.get("inf", 0.0)
    if "prob_enfermeria" in columns:
        payload["prob_enfermeria"] = probs.get("enf", 0.0)
    if "prob_administracion" in columns:
        payload["prob_administracion"] = probs.get("adm", 0.0)
    if "prob_comercio" in columns:
        payload["prob_comercio"] = probs.get("com", 0.0)
    if "model_version" in columns:
        payload["model_version"] = model_version
    include_predicted_at = "predicted_at" in columns

    if not payload:
        return

    where_clause = None
    where_params: dict[str, Any] = {}
    if evaluacion_id is not None and "response_id" in columns:
        where_clause = "response_id = :where_response_id"
        where_params["where_response_id"] = int(evaluacion_id)
    elif "student_id" in columns:
        where_clause = "student_id = :where_student_id"
        where_params["where_student_id"] = int(student_id)

    if where_clause:
        exists = db.session.execute(
            text(f"SELECT COUNT(1) AS total FROM ai_predictions WHERE {where_clause}"),
            where_params,
        ).scalar() or 0
    else:
        exists = 0

    if int(exists) > 0 and where_clause:
        set_cols = [column for column in payload.keys() if column not in {"student_id", "response_id"}]
        set_sql_parts = [f"{column} = :{column}" for column in set_cols]
        if include_predicted_at:
            set_sql_parts.append("predicted_at = CURRENT_TIMESTAMP")
        if not set_sql_parts:
            return
        update_sql = ", ".join(set_sql_parts)
        params = {column: payload[column] for column in set_cols}
        params.update(where_params)
        db.session.execute(
            text(f"UPDATE ai_predictions SET {update_sql} WHERE {where_clause}"),
            params,
        )
        return

    insert_cols = [column for column in payload.keys() if payload.get(column) is not None]
    insert_vals = [f":{column}" for column in insert_cols]
    if include_predicted_at:
        insert_cols.append("predicted_at")
        insert_vals.append("CURRENT_TIMESTAMP")
    if not insert_cols:
        return

    insert_sql_cols = ", ".join(insert_cols)
    insert_sql_vals = ", ".join(insert_vals)
    insert_params = {column: payload[column] for column in payload.keys() if column in insert_cols}
    db.session.execute(
        text(f"INSERT INTO ai_predictions ({insert_sql_cols}) VALUES ({insert_sql_vals})"),
        insert_params,
    )
