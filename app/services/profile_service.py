from __future__ import annotations

from datetime import date
import re
import unicodedata

from app.models.student_profile import StudentProfile

DEFAULT_SCHOOL_ID = "10-03-00092"
GENDERS = {"masculino", "femenino", "otro", "prefiero_no_decir"}
ACADEMIC_STATUS = {"activo", "no_actual", "egresado", "tecnico"}
LABOR_STATUS = {"activo", "inactivo"}


def _normalize_letters(value: str) -> str:
    clean = (value or "").strip()
    normalized = unicodedata.normalize("NFD", clean)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return normalized


def _first_letter(value: str) -> str:
    cleaned = _normalize_letters(value)
    for char in cleaned:
        if char.isalpha():
            return char.upper()
    return "X"


def normalize_student_id(value: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", (value or "").strip())
    return compact.upper()


def is_valid_student_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9]{10,24}", value or ""))


def calculate_age(fecha_nacimiento: date) -> int:
    today = date.today()
    years = today.year - fecha_nacimiento.year
    if (today.month, today.day) < (fecha_nacimiento.month, fecha_nacimiento.day):
        years -= 1
    return max(years, 0)


def generate_student_id(
    nombre: str,
    apellido: str,
    segundo_apellido: str,
    fecha_nacimiento: date,
) -> str:
    initials = f"{_first_letter(nombre)}{_first_letter(apellido)}{_first_letter(segundo_apellido)}"
    # Base sugerida: iniciales + fecha + sufijo secuencial base
    return f"{initials}{fecha_nacimiento.strftime('%d%m%Y')}0001"


def ensure_unique_student_id(candidate: str, current_user_id: int | None = None) -> str:
    base = normalize_student_id(candidate)[:24]
    if not base:
        base = "XXX000000000001"

    existing = StudentProfile.query.filter_by(student_id=base).first()
    if not existing or (current_user_id and existing.user_id == current_user_id):
        return base

    # Reserva ultimos 2 digitos para desambiguar de forma estable.
    prefix = base[:22]
    for index in range(1, 100):
        new_candidate = f"{prefix}{index:02d}"
        clash = StudentProfile.query.filter_by(student_id=new_candidate).first()
        if not clash or (current_user_id and clash.user_id == current_user_id):
            return new_candidate

    # Fallback extremo
    return f"{prefix}99"
