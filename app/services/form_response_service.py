from __future__ import annotations

import json
from typing import Any

from flask import current_app, has_app_context
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models.form_response import FormResponse
from app.services.profile_service import normalize_student_id
from app.services.tally_service import extract_submission_metadata, extract_submission_values


def _form_response_schema_ready() -> bool:
    try:
        inspector = inspect(db.engine)
        if "form_responses" not in inspector.get_table_names():
            return False
        existing = {column["name"] for column in inspector.get_columns("form_responses")}
        required = {
            "id",
            "estudiante_id",
            "evaluacion_id",
            "submission_id",
            "respondent_id",
            "form_id",
            "matricula_estudiante",
            "submitted_at",
            "payload_json",
            "source",
        }
        return required.issubset(existing)
    except Exception:
        return False


def upsert_form_response_from_tally(
    *,
    estudiante_id: int,
    evaluacion_id: int | None = None,
    submission_payload: dict[str, Any] | None = None,
    fallback_submission_id: str | None = None,
    fallback_form_id: str | None = None,
    fallback_matricula: str | None = None,
):
    if has_app_context() and not current_app.config.get("ENABLE_FORM_RESPONSE_STORAGE", False):
        return None

    if not _form_response_schema_ready():
        return None

    metadata: dict[str, Any] = {}
    submission_values: dict[str, Any] = {}
    if isinstance(submission_payload, dict):
        metadata = extract_submission_metadata(submission_payload)
        submission_values = extract_submission_values(submission_payload)

    submission_id = (
        str(metadata.get("referencia_externa") or fallback_submission_id or "").strip() or None
    )
    respondent_id = str(metadata.get("respondent_id") or "").strip() or None
    form_id = str(metadata.get("form_id") or fallback_form_id or "").strip() or None
    submitted_at = metadata.get("submitted_at")

    matricula = normalize_student_id(
        submission_values.get("matricula_estudiante") or fallback_matricula or ""
    ) or None

    row = None
    try:
        if submission_id:
            row = FormResponse.query.filter_by(submission_id=submission_id).first()
        if not row and evaluacion_id:
            row = FormResponse.query.filter_by(evaluacion_id=evaluacion_id).first()
        if not row:
            row = FormResponse(estudiante_id=estudiante_id, source="tally")

        row.estudiante_id = estudiante_id
        row.evaluacion_id = evaluacion_id
        row.submission_id = submission_id
        row.respondent_id = respondent_id
        row.form_id = form_id
        row.matricula_estudiante = matricula
        if submitted_at:
            row.submitted_at = submitted_at
        if submission_payload:
            row.payload_json = json.dumps(submission_payload, ensure_ascii=False)

        db.session.add(row)
        return row
    except SQLAlchemyError:
        # Si el esquema real no coincide aun (por ejemplo, tabla legacy sin id),
        # no bloqueamos el flujo de sincronizacion principal.
        db.session.rollback()
        return None
