from __future__ import annotations

import unicodedata

from sqlalchemy import case, inspect

from app import db
from app.models.academic_score import AcademicScore
from app.models.calificacion import Calificacion


SUBJECT_MATH = "Matematicas"
SUBJECT_LANGUAGE = "Lenguas"
SUBJECT_SCIENCE = "Ciencias"


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_subject_name(value: str) -> str:
    text = _strip_accents((value or "").strip().lower())
    text = " ".join(text.split())
    return text


def map_subject_to_group(value: str) -> str | None:
    subject = normalize_subject_name(value)
    if subject in {"matematicas", "matematica"}:
        return SUBJECT_MATH
    if subject in {
        "espanol",
        "lengua espanola",
        "lenguas extranjeras",
        "ingles",
        "frances",
        "idioma extranjero",
    }:
        return SUBJECT_LANGUAGE
    if subject in {"ciencias naturales", "naturales", "ciencias sociales", "sociales"}:
        return SUBJECT_SCIENCE
    return None


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _academic_scores_schema_columns() -> dict[str, dict]:
    try:
        columns = inspect(db.engine).get_columns("academic_scores")
    except Exception:
        return {}
    return {column["name"]: column for column in columns}


def _student_profile_id(estudiante_id: int) -> str | None:
    from app.models.student_profile import StudentProfile

    with db.session.no_autoflush:
        row = (
            StudentProfile.query.filter_by(user_id=estudiante_id)
            .with_entities(StudentProfile.student_id)
            .first()
        )
    if not row:
        return None
    value = (row[0] or "").strip()
    return value or None


def _sync_student_data_for_scores(estudiante_id: int) -> None:
    from app.models.student_profile import StudentProfile
    from app.models.user import User
    from app.services.student_data_sync_service import sync_student_data_if_exists

    with db.session.no_autoflush:
        user = User.query.get(estudiante_id)
        profile = StudentProfile.query.filter_by(user_id=estudiante_id).first()
    if user and profile and (profile.student_id or "").strip():
        sync_student_data_if_exists(user, profile)


def recalculate_academic_scores_for_student(estudiante_id: int) -> int:
    with db.session.no_autoflush:
        rows = Calificacion.query.filter_by(estudiante_id=estudiante_id).all()
        existing_rows = AcademicScore.query.filter_by(estudiante_id=estudiante_id).all()
        schema_columns = _academic_scores_schema_columns()
        student_id_value = _student_profile_id(estudiante_id)

    has_student_id_column = "student_id" in schema_columns
    student_id_required = has_student_id_column and not schema_columns["student_id"].get("nullable", True)

    if has_student_id_column and student_id_value:
        _sync_student_data_for_scores(estudiante_id)

    grouped: dict[int | None, dict[str, list[float] | set[str]]] = {}
    for row in rows:
        group = map_subject_to_group(row.asignatura)
        if not group:
            continue
        if row.valor is None:
            continue
        anio_key = row.anio
        bucket = grouped.setdefault(
            anio_key,
            {
                "math": [],
                "language": [],
                "science": [],
                "periods": set(),
            },
        )
        if group == SUBJECT_MATH:
            bucket["math"].append(float(row.valor))
        elif group == SUBJECT_LANGUAGE:
            bucket["language"].append(float(row.valor))
        elif group == SUBJECT_SCIENCE:
            bucket["science"].append(float(row.valor))
        if row.periodo:
            bucket["periods"].add(str(row.periodo))

    if not grouped:
        for stale in existing_rows:
            db.session.delete(stale)
        return 0

    by_year = {item.anio: item for item in existing_rows}
    updated = 0
    for anio_key, data in grouped.items():
        record = by_year.pop(anio_key, None)
        if not record:
            if student_id_required and not student_id_value:
                # Legacy schemas may require student_id in academic_scores.
                continue
            record = AcademicScore(estudiante_id=estudiante_id, anio=anio_key)
        if has_student_id_column and student_id_value:
            record.student_id = student_id_value

        math_avg = _avg(data["math"])
        language_avg = _avg(data["language"])
        science_avg = _avg(data["science"])
        parts = [score for score in [math_avg, language_avg, science_avg] if score is not None]
        overall = round(sum(parts) / len(parts), 2) if parts else None

        record.math_average = math_avg
        record.language_average = language_avg
        record.science_average = science_avg
        record.overall_average = overall
        record.period_count = len(data["periods"])
        db.session.add(record)
        updated += 1

    for stale in by_year.values():
        db.session.delete(stale)
    return updated


def recalculate_all_academic_scores() -> int:
    student_ids = {row.estudiante_id for row in Calificacion.query.with_entities(Calificacion.estudiante_id).all()}
    student_ids.update(
        row.estudiante_id for row in AcademicScore.query.with_entities(AcademicScore.estudiante_id).all()
    )
    total_rows = 0
    for estudiante_id in student_ids:
        total_rows += recalculate_academic_scores_for_student(estudiante_id)
    return total_rows


def latest_academic_score_for_student(estudiante_id: int) -> AcademicScore | None:
    return (
        AcademicScore.query.filter_by(estudiante_id=estudiante_id)
        .order_by(case((AcademicScore.anio.is_(None), 1), else_=0), AcademicScore.anio.desc())
        .first()
    )
