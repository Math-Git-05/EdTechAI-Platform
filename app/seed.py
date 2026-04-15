from werkzeug.security import generate_password_hash

from app import db
from app.models.calificacion import Calificacion
from app.models.student_profile import StudentProfile
from app.models.teacher_profile import TeacherProfile
from app.models.user import User
from app.services.profile_service import DEFAULT_SCHOOL_ID, calculate_age, ensure_unique_student_id, generate_student_id
from app.services.student_data_sync_service import sync_student_data_if_exists
from app.services.student_interest_service import sync_student_interest_from_profile
from datetime import date


TEST_USERS = [
    {
        "nombre": "Admin",
        "apellido": "Test",
        "email": "admin.test@edtech.local",
        "password": "Admin123!",
        "role": "admin",
    },
    {
        "nombre": "Profesor",
        "apellido": "Test",
        "email": "profesor.test@edtech.local",
        "password": "Profesor123!",
        "role": "profesor",
    },
    {
        "nombre": "Estudiante",
        "apellido": "Test",
        "email": "estudiante.test@edtech.local",
        "password": "Estudiante123!",
        "role": "estudiante",
    },
]


def seed_test_users() -> None:
    for payload in TEST_USERS:
        existing = User.query.filter_by(email=payload["email"]).first()
        if existing:
            existing.nombre = payload["nombre"]
            existing.apellido = payload["apellido"]
            existing.role = payload["role"]
            existing.password = generate_password_hash(payload["password"])
            existing.activo = True
            existing.email_verificado = True
            existing.email_verificado_at = db.func.current_timestamp()
            if existing.role == "estudiante" and not existing.seccion:
                existing.seccion = "A-1"
            db.session.add(existing)
            continue

        user = User(
            nombre=payload["nombre"],
            apellido=payload["apellido"],
            email=payload["email"],
            password=generate_password_hash(payload["password"]),
            role=payload["role"],
            activo=True,
            email_verificado=True,
            email_verificado_at=db.func.current_timestamp(),
            seccion="A-1" if payload["role"] == "estudiante" else None,
        )
        db.session.add(user)

    profesor = User.query.filter_by(email="profesor.test@edtech.local").first()

    if profesor and not profesor.teacher_profile:
        teacher_profile = TeacherProfile(
            user_id=profesor.id,
            employee_id=f"PROF-{profesor.id:04d}",
            especialidad="Orientacion tecnica",
            departamento="Docencia",
            telefono="",
            school_id=DEFAULT_SCHOOL_ID,
            labor_status="activo",
        )
        db.session.add(teacher_profile)

    db.session.commit()
