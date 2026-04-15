from __future__ import annotations

from app import db
from app.models.student_interest import StudentInterest
from app.services.profile_service import normalize_student_id


def _safe_interest(value) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, round(parsed, 2)))


def _has_legacy_parent_student_row(student_id: str) -> bool:
    # El parent legacy students_data/student_data deja de ser requerido como fuente canonica.
    # student_interests se mantiene independiente y toma los datos desde student_profiles.
    return True


def upsert_student_interest(
    *,
    student_id: str,
    interest_technology,
    interest_design,
    interest_business,
    interest_health,
) -> StudentInterest | None:
    normalized_student_id = normalize_student_id(student_id or "")
    if not normalized_student_id:
        return None

    row = StudentInterest.query.filter_by(student_id=normalized_student_id).first()
    if not row:
        row = StudentInterest(student_id=normalized_student_id)

    row.interest_technology = _safe_interest(interest_technology)
    row.interest_design = _safe_interest(interest_design)
    row.interest_business = _safe_interest(interest_business)
    row.interest_health = _safe_interest(interest_health)
    db.session.add(row)
    return row


def sync_student_interest_from_profile(student_profile) -> StudentInterest | None:
    if not student_profile:
        return None
    normalized_student_id = normalize_student_id(getattr(student_profile, "student_id", "") or "")
    if not normalized_student_id:
        return None
    if not _has_legacy_parent_student_row(normalized_student_id):
        return None
    return upsert_student_interest(
        student_id=normalized_student_id,
        interest_technology=getattr(student_profile, "interest_technology", 0),
        interest_design=getattr(student_profile, "interest_design", 0),
        interest_business=getattr(student_profile, "interest_business", 0),
        interest_health=getattr(student_profile, "interest_health", 0),
    )
