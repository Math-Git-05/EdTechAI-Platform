from datetime import datetime
import json

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, func, inspect, or_, text
from werkzeug.security import generate_password_hash

from app import db
from app.models.academic_score import AcademicScore
from app.models.calificacion import Calificacion
from app.models.comentario_edit_request import ComentarioEditRequest
from app.models.evaluacion import Evaluacion
from app.models.profile_edit_request import ProfileEditRequest
from app.models.student_interest import StudentInterest
from app.models.student_profile import StudentProfile
from app.models.teacher_profile import TeacherProfile
from app.models.user import User
from app.services.profile_service import (
    ACADEMIC_STATUS,
    DEFAULT_SCHOOL_ID,
    GENDERS,
    LABOR_STATUS,
    calculate_age,
    ensure_unique_student_id,
    generate_student_id,
    is_valid_student_id,
    normalize_student_id,
)
from app.services.form_response_service import upsert_form_response_from_tally
from app.services.academic_score_service import (
    recalculate_academic_scores_for_student,
    recalculate_all_academic_scores,
)
from app.services.evaluacion_service import best_track_from_evaluacion, score_cards_from_evaluacion
from app.services.prediction_persistence_service import upsert_ai_prediction_for_evaluacion
from app.services.recommendation_engine_service import build_recommendation_for_student
from app.services.student_data_sync_service import delete_student_data_if_exists, sync_student_data_if_exists
from app.services.student_interest_service import sync_student_interest_from_profile
from app.services.tally_service import (
    apply_sheet_scores_to_evaluacion,
    apply_submission_to_evaluacion,
    fetch_scores_from_submissions_sheet,
    fetch_submission_from_tally,
)

admin_bp = Blueprint("admin", __name__)

ROLES_VALIDOS = {"estudiante", "profesor", "admin"}
GENDER_OPTIONS = [
    ("masculino", "Masculino"),
    ("femenino", "Femenino"),
    ("otro", "Otro"),
    ("prefiero_no_decir", "Prefiero no decir"),
]
ACADEMIC_STATUS_OPTIONS = [
    ("activo", "Activo"),
    ("no_actual", "No actual"),
    ("egresado", "Egresado"),
    ("tecnico", "Paso a tecnico"),
]
LABOR_STATUS_OPTIONS = [
    ("activo", "Activo"),
    ("inactivo", "Inactivo"),
]
SECCIONES_VALIDAS = {"A", "B", "C", "D", "E"}
PERIODOS_CALIFICACION = [
    ("periodo_1", "1er periodo"),
    ("periodo_2", "2do periodo"),
    ("periodo_3", "3er periodo"),
    ("periodo_4", "4to periodo"),
]
ASIGNATURAS_PREDEFINIDAS = [
    "Espanol",
    "Lenguas extranjeras",
    "Matematicas",
    "Ciencias sociales",
    "Ciencias naturales",
]


def _check_admin_access():
    if current_user.role != "admin":
        flash("Solo administradores pueden acceder a este modulo.", "warning")
        return False
    return True


def _root_admin_email() -> str:
    return (current_app.config.get("ROOT_ADMIN_EMAIL") or "").strip().lower()


def _is_root_admin_user(usuario: User | None) -> bool:
    if not usuario:
        return False
    root_email = _root_admin_email()
    return bool(root_email and (usuario.email or "").strip().lower() == root_email)


def _visible_users_query():
    root_email = _root_admin_email()
    query = User.query
    if root_email:
        query = query.filter(func.lower(User.email) != root_email)
    return query


def _redirect_back(default_endpoint: str):
    referer = (request.referrer or "").strip()
    if referer:
        return redirect(referer)
    return redirect(url_for(default_endpoint))


def _is_delete_confirmed() -> bool:
    return (request.form.get("confirm_token") or "").strip().upper() == "ELIMINAR"


def _remove_student_shadow_data(*, user_id: int, student_id: str | None) -> None:
    normalized_student_id = (student_id or "").strip()
    if normalized_student_id:
        StudentInterest.query.filter_by(student_id=normalized_student_id).delete(synchronize_session=False)
    delete_student_data_if_exists(user_id=user_id, student_id=normalized_student_id or None)


def _delete_form_responses_for_student(*, user_id: int, student_id: str | None = None) -> None:
    inspector = inspect(db.engine)
    if "form_responses" not in set(inspector.get_table_names()):
        return

    columns = {column["name"] for column in inspector.get_columns("form_responses")}
    if "estudiante_id" not in columns and "matricula_estudiante" not in columns:
        return

    normalized_student_id = (student_id or "").strip()
    filters: list[str] = []
    params: dict[str, object] = {}

    if "estudiante_id" in columns:
        filters.append("estudiante_id = :estudiante_id")
        params["estudiante_id"] = int(user_id)
    if normalized_student_id and "matricula_estudiante" in columns:
        filters.append("matricula_estudiante = :matricula_estudiante")
        params["matricula_estudiante"] = normalized_student_id

    if not filters:
        return

    where_clause = " OR ".join(filters)
    db.session.execute(text(f"DELETE FROM form_responses WHERE {where_clause}"), params)


def _delete_ai_predictions_for_student(*, user_id: int) -> None:
    inspector = inspect(db.engine)
    if "ai_predictions" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("ai_predictions")}
    if "student_id" not in columns:
        return
    db.session.execute(
        text("DELETE FROM ai_predictions WHERE student_id = :student_id"),
        {"student_id": int(user_id)},
    )


def _delete_teacher_observations_rows(*, student_id: int | None = None, teacher_id: int | None = None) -> None:
    inspector = inspect(db.engine)
    if "teacher_observations" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("teacher_observations")}

    filters: list[str] = []
    params: dict[str, object] = {}
    if student_id is not None and "student_id" in columns:
        filters.append("student_id = :student_id")
        params["student_id"] = int(student_id)
    if teacher_id is not None and "teacher_id" in columns:
        filters.append("teacher_id = :teacher_id")
        params["teacher_id"] = int(teacher_id)
    if not filters:
        return

    where_clause = " OR ".join(filters)
    db.session.execute(text(f"DELETE FROM teacher_observations WHERE {where_clause}"), params)


def _delete_academic_profile_for_user(usuario: User) -> tuple[bool, list[str]]:
    removed_sections: list[str] = []

    if usuario.student_profile:
        student_id = usuario.student_profile.student_id
        _remove_student_shadow_data(user_id=usuario.id, student_id=student_id)
        _delete_ai_predictions_for_student(user_id=usuario.id)
        _delete_teacher_observations_rows(student_id=usuario.id)
        db.session.delete(usuario.student_profile)
        removed_sections.append("perfil estudiante")

        # Datos academicos ligados al perfil del estudiante.
        AcademicScore.query.filter_by(estudiante_id=usuario.id).delete(synchronize_session=False)
        Calificacion.query.filter_by(estudiante_id=usuario.id).delete(synchronize_session=False)
        removed_sections.append("scores y calificaciones")

        usuario.seccion = None
        db.session.add(usuario)

    if usuario.teacher_profile:
        db.session.delete(usuario.teacher_profile)
        removed_sections.append("perfil profesor")

    if removed_sections:
        ProfileEditRequest.query.filter_by(user_id=usuario.id).delete(synchronize_session=False)
        removed_sections.append("solicitudes de perfil")

    return bool(removed_sections), removed_sections


def _disable_user_access(usuario: User) -> None:
    if usuario.role == "profesor":
        User.query.filter_by(profesor_id=usuario.id).update({"profesor_id": None}, synchronize_session=False)
        Evaluacion.query.filter_by(profesor_comentario_id=usuario.id).update(
            {"profesor_comentario_id": None},
            synchronize_session=False,
        )

    usuario.profesor_id = None
    usuario.seccion = None
    usuario.activo = False
    usuario.email_verificado = False
    usuario.email_verificado_at = None
    usuario.ultimo_login = None

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    usuario.email = f"deleted+{usuario.id}+{timestamp}@edtech.local"
    usuario.password = generate_password_hash(f"deleted::{usuario.id}::{timestamp}")
    db.session.add(usuario)


def _delete_user_hard(usuario: User) -> None:
    student_profile = StudentProfile.query.filter_by(user_id=usuario.id).first()
    student_id = student_profile.student_id if student_profile else None
    _remove_student_shadow_data(user_id=usuario.id, student_id=student_id)
    _delete_form_responses_for_student(user_id=usuario.id, student_id=student_id)
    _delete_ai_predictions_for_student(user_id=usuario.id)
    _delete_teacher_observations_rows(student_id=usuario.id, teacher_id=usuario.id)

    # Limpieza de referencias cruzadas entre usuarios.
    User.query.filter(User.profesor_id == usuario.id).update({"profesor_id": None}, synchronize_session=False)
    Evaluacion.query.filter(Evaluacion.profesor_comentario_id == usuario.id).update(
        {"profesor_comentario_id": None},
        synchronize_session=False,
    )

    # Primero liberar referencias opcionales y luego eliminar filas dependientes.
    ProfileEditRequest.query.filter_by(reviewed_by=usuario.id).update(
        {"reviewed_by": None},
        synchronize_session=False,
    )
    ComentarioEditRequest.query.filter_by(reviewed_by=usuario.id).update(
        {"reviewed_by": None},
        synchronize_session=False,
    )

    ComentarioEditRequest.query.filter(
        or_(
            ComentarioEditRequest.profesor_id == usuario.id,
            ComentarioEditRequest.estudiante_id == usuario.id,
        )
    ).delete(synchronize_session=False)
    ProfileEditRequest.query.filter_by(user_id=usuario.id).delete(synchronize_session=False)

    Evaluacion.query.filter_by(estudiante_id=usuario.id).delete(synchronize_session=False)
    AcademicScore.query.filter_by(estudiante_id=usuario.id).delete(synchronize_session=False)
    Calificacion.query.filter_by(estudiante_id=usuario.id).delete(synchronize_session=False)

    StudentProfile.query.filter_by(user_id=usuario.id).delete(synchronize_session=False)
    TeacherProfile.query.filter_by(user_id=usuario.id).delete(synchronize_session=False)
    User.query.filter_by(id=usuario.id).delete(synchronize_session=False)


def _normalize_choice(value: str, allowed: set[str], fallback: str) -> str:
    candidate = (value or "").strip().lower()
    if candidate in allowed:
        return candidate
    return fallback


def _sync_ai_prediction_safe(evaluacion: Evaluacion) -> bool:
    estudiante = User.query.get(evaluacion.estudiante_id)
    if not estudiante:
        return False
    try:
        recommendation = build_recommendation_for_student(student=estudiante, evaluacion=evaluacion)
        upsert_ai_prediction_for_evaluacion(
            student_id=estudiante.id,
            evaluacion_id=evaluacion.id,
            recommendation=recommendation,
        )
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "No se pudo sincronizar ai_predictions para evaluacion_id=%s estudiante_id=%s",
            getattr(evaluacion, "id", None),
            getattr(estudiante, "id", None),
        )
        return False


def _parse_optional_interest(value):
    if value in {None, ""}:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, round(parsed, 2)))


def _parse_birth_date(raw_date: str):
    value = (raw_date or "").strip()
    if not value:
        return None, "Debes indicar la fecha de nacimiento."
    try:
        return datetime.strptime(value, "%Y-%m-%d").date(), None
    except ValueError:
        return None, "Formato de fecha de nacimiento invalido."


def _load_request_payload(solicitud: ProfileEditRequest) -> dict:
    try:
        payload = json.loads(solicitud.request_payload_json or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return payload


def _apply_profile_request_changes(solicitud: ProfileEditRequest) -> None:
    payload = _load_request_payload(solicitud)
    usuario = User.query.get(solicitud.user_id)
    if not usuario:
        raise ValueError("No se encontro el usuario de la solicitud.")

    nombre = (payload.get("nombre") or "").strip()
    apellido = (payload.get("apellido") or "").strip()
    if not nombre or not apellido:
        raise ValueError("La solicitud no tiene nombre y apellido validos.")

    usuario.nombre = nombre
    usuario.apellido = apellido
    db.session.add(usuario)

    role = (payload.get("role") or usuario.role or "").strip().lower()
    if role == "estudiante":
        student_payload = payload.get("student_profile") if isinstance(payload.get("student_profile"), dict) else {}
        student_id = normalize_student_id(student_payload.get("student_id") or "")
        segundo_apellido = (student_payload.get("segundo_apellido") or "").strip()
        grado_nivel = (student_payload.get("grado_nivel") or "").strip()
        genero = _normalize_choice(student_payload.get("genero"), GENDERS, "prefiero_no_decir")
        school_id = (
            (student_payload.get("school_id") or DEFAULT_SCHOOL_ID).strip().upper()
            or DEFAULT_SCHOOL_ID
        )
        academic_status = _normalize_choice(
            student_payload.get("academic_status"),
            ACADEMIC_STATUS,
            "activo",
        )

        if not student_id or not is_valid_student_id(student_id):
            raise ValueError("RNE/Matricula invalido en la solicitud.")
        if not segundo_apellido or not grado_nivel:
            raise ValueError("La solicitud del estudiante esta incompleta.")

        nacimiento_raw = str(student_payload.get("fecha_nacimiento") or "").strip()
        try:
            fecha_nacimiento = datetime.strptime(nacimiento_raw, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("Fecha de nacimiento invalida en solicitud.") from exc

        enrollment_year = student_payload.get("enrollment_year")
        if enrollment_year in {"", None}:
            enrollment_year = None
        else:
            try:
                enrollment_year = int(enrollment_year)
            except (TypeError, ValueError) as exc:
                raise ValueError("Ano de ingreso invalido en solicitud.") from exc
            current_year = datetime.utcnow().year + 1
            if enrollment_year < 1990 or enrollment_year > current_year:
                raise ValueError("Ano de ingreso fuera de rango en solicitud.")

        clash = StudentProfile.query.filter_by(student_id=student_id).first()
        if clash and clash.user_id != usuario.id:
            raise ValueError("El RNE/Matricula solicitado ya existe en otro usuario.")

        student_profile = StudentProfile.query.filter_by(user_id=usuario.id).first()
        if not student_profile:
            student_profile = StudentProfile(user_id=usuario.id)

        student_profile.student_id = student_id
        student_profile.segundo_apellido = segundo_apellido
        student_profile.genero = genero
        student_profile.fecha_nacimiento = fecha_nacimiento
        student_profile.edad = calculate_age(fecha_nacimiento)
        student_profile.grado_nivel = grado_nivel
        student_profile.school_id = school_id
        student_profile.enrollment_year = enrollment_year
        student_profile.academic_status = academic_status

        interest_technology = _parse_optional_interest(student_payload.get("interest_technology"))
        interest_design = _parse_optional_interest(student_payload.get("interest_design"))
        interest_business = _parse_optional_interest(student_payload.get("interest_business"))
        interest_health = _parse_optional_interest(student_payload.get("interest_health"))
        if interest_technology is not None:
            student_profile.interest_technology = interest_technology
        if interest_design is not None:
            student_profile.interest_design = interest_design
        if interest_business is not None:
            student_profile.interest_business = interest_business
        if interest_health is not None:
            student_profile.interest_health = interest_health

        db.session.add(student_profile)
        sync_student_data_if_exists(usuario, student_profile)
        sync_student_interest_from_profile(student_profile)
    elif role == "profesor":
        teacher_payload = payload.get("teacher_profile") if isinstance(payload.get("teacher_profile"), dict) else {}
        employee_id = normalize_student_id(teacher_payload.get("employee_id") or "") or None
        if employee_id:
            conflict = TeacherProfile.query.filter_by(employee_id=employee_id).first()
            if conflict and conflict.user_id != usuario.id:
                raise ValueError("El codigo de empleado ya existe en otro perfil.")

        teacher_profile = TeacherProfile.query.filter_by(user_id=usuario.id).first()
        if not teacher_profile:
            teacher_profile = TeacherProfile(user_id=usuario.id)

        teacher_profile.employee_id = employee_id
        teacher_profile.especialidad = (teacher_payload.get("especialidad") or "").strip() or None
        teacher_profile.departamento = (teacher_payload.get("departamento") or "").strip() or None
        teacher_profile.telefono = (teacher_payload.get("telefono") or "").strip() or None
        teacher_profile.school_id = (
            (teacher_payload.get("school_id") or DEFAULT_SCHOOL_ID).strip().upper()
            or DEFAULT_SCHOOL_ID
        )
        teacher_profile.labor_status = _normalize_choice(
            teacher_payload.get("labor_status"), LABOR_STATUS, "activo"
        )
        db.session.add(teacher_profile)


def _review_profile_request(solicitud: ProfileEditRequest, action: str, note: str | None = None) -> None:
    if solicitud.status != "pendiente":
        raise ValueError("La solicitud ya fue revisada.")

    action = (action or "").strip().lower()
    if action not in {"aprobar", "rechazar"}:
        raise ValueError("Accion invalida para solicitud.")

    if action == "aprobar":
        _apply_profile_request_changes(solicitud)
        solicitud.status = "aprobada"
    else:
        solicitud.status = "rechazada"

    solicitud.admin_note = (note or "").strip() or None
    solicitud.reviewed_by = current_user.id
    solicitud.reviewed_at = db.func.current_timestamp()
    db.session.add(solicitud)


def _dashboard_context():
    users_q = _visible_users_query()
    total_users = users_q.count()
    estudiantes_count = users_q.filter_by(role="estudiante").count()
    profesores_count = users_q.filter_by(role="profesor").count()
    admins_count = users_q.filter_by(role="admin").count()
    estudiantes_asignados_count = (
        users_q.filter_by(role="estudiante").filter(User.profesor_id.isnot(None)).count()
    )
    estudiantes_sin_asignar_count = (
        users_q.filter_by(role="estudiante").filter(User.profesor_id.is_(None)).count()
    )
    evaluaciones_count = Evaluacion.query.count()
    evaluaciones_tally_count = Evaluacion.query.filter_by(origen="tally").count()

    avg_row = (
        db.session.query(
            func.avg(Evaluacion.logical_reasoning_score),
            func.avg(Evaluacion.problem_resolution_score),
            func.avg(Evaluacion.detail_attention_score),
            func.avg(Evaluacion.creativity_score),
            func.avg(Evaluacion.tech_ability_score),
            func.avg(Evaluacion.average_score),
        )
        .filter(Evaluacion.origen == "tally")
        .first()
    )
    score_averages = {
        "logical_reasoning_score": round(avg_row[0], 2) if avg_row and avg_row[0] is not None else None,
        "problem_resolution_score": round(avg_row[1], 2) if avg_row and avg_row[1] is not None else None,
        "detail_attention_score": round(avg_row[2], 2) if avg_row and avg_row[2] is not None else None,
        "creativity_score": round(avg_row[3], 2) if avg_row and avg_row[3] is not None else None,
        "tech_ability_score": round(avg_row[4], 2) if avg_row and avg_row[4] is not None else None,
        "average_score": round(avg_row[5], 2) if avg_row and avg_row[5] is not None else None,
    }

    usuarios = users_q.order_by(User.fecha_creacion.desc()).all()
    profesores = (
        users_q.filter_by(role="profesor").order_by(User.nombre.asc(), User.apellido.asc()).all()
    )
    estudiantes = (
        users_q.filter_by(role="estudiante")
        .order_by(User.nombre.asc(), User.apellido.asc())
        .all()
    )
    calificaciones_recientes = (
        Calificacion.query.order_by(Calificacion.fecha_creacion.desc()).limit(60).all()
    )
    estudiantes_por_id = {est.id: est for est in estudiantes}
    try:
        promedios_calificaciones_raw = (
            AcademicScore.query.order_by(AcademicScore.anio.desc(), AcademicScore.fecha_actualizacion.desc())
            .limit(160)
            .all()
        )
        if not promedios_calificaciones_raw and calificaciones_recientes:
            recalculate_all_academic_scores()
            db.session.commit()
            promedios_calificaciones_raw = (
                AcademicScore.query.order_by(AcademicScore.anio.desc(), AcademicScore.fecha_actualizacion.desc())
                .limit(160)
                .all()
            )
    except Exception:
        db.session.rollback()
        promedios_calificaciones_raw = []

    promedios_calificaciones = []
    for row in promedios_calificaciones_raw:
        estudiante = estudiantes_por_id.get(row.estudiante_id)
        if not estudiante:
            continue
        promedios_calificaciones.append(
            {
                "estudiante": estudiante,
                "math_average": row.math_average,
                "language_average": row.language_average,
                "science_average": row.science_average,
                "overall_average": row.overall_average,
                "anio": row.anio,
                "cantidad": int(row.period_count or 0),
            }
        )

    current_year = datetime.utcnow().year
    year_options = [current_year - 1, current_year, current_year + 1]
    solicitudes_perfil = (
        ProfileEditRequest.query.filter_by(status="pendiente")
        .order_by(ProfileEditRequest.requested_at.asc())
        .limit(100)
        .all()
    )
    solicitudes_comentario = (
        ComentarioEditRequest.query.filter_by(status="pendiente")
        .order_by(ComentarioEditRequest.requested_at.asc())
        .limit(100)
        .all()
    )
    solicitudes_cuenta_verificacion = (
        User.query.filter(
            User.role.in_(["estudiante", "profesor"]),
            User.email_verificado == False,
        )
        .order_by(User.fecha_creacion.asc())
        .limit(120)
        .all()
    )
    solicitudes_cuenta_aprobacion = (
        User.query.filter(
            User.role.in_(["estudiante", "profesor"]),
            User.email_verificado == True,
            User.activo == False,
        )
        .order_by(User.fecha_creacion.asc())
        .limit(120)
        .all()
    )
    for solicitud in solicitudes_perfil:
        solicitud.parsed_payload = _load_request_payload(solicitud)

    return {
        "total_users": total_users,
        "estudiantes_count": estudiantes_count,
        "profesores_count": profesores_count,
        "admins_count": admins_count,
        "estudiantes_asignados_count": estudiantes_asignados_count,
        "estudiantes_sin_asignar_count": estudiantes_sin_asignar_count,
        "evaluaciones_count": evaluaciones_count,
        "evaluaciones_tally_count": evaluaciones_tally_count,
        "score_averages": score_averages,
        "usuarios": usuarios,
        "profesores": profesores,
        "estudiantes": estudiantes,
        "calificaciones_recientes": calificaciones_recientes,
        "promedios_calificaciones": promedios_calificaciones,
        "periodos_calificacion": PERIODOS_CALIFICACION,
        "periodo_labels": {value: label for value, label in PERIODOS_CALIFICACION},
        "asignaturas_predefinidas": ASIGNATURAS_PREDEFINIDAS,
        "year_options": year_options,
        "solicitudes_perfil": solicitudes_perfil,
        "solicitudes_comentario": solicitudes_comentario,
        "solicitudes_cuenta_verificacion": solicitudes_cuenta_verificacion,
        "solicitudes_cuenta_aprobacion": solicitudes_cuenta_aprobacion,
        "solicitudes_cuenta_verificacion_count": len(solicitudes_cuenta_verificacion),
        "solicitudes_cuenta_aprobacion_count": len(solicitudes_cuenta_aprobacion),
        "submissions_sheet_url": current_app.config.get("TALLY_SUBMISSIONS_SHEET_URL"),
    }


@admin_bp.route("/")
@login_required
def index():
    """Home del dashboard administrativo."""
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))
    return render_template("dashboard/admin_home.html", admin_active="home", **_dashboard_context())


@admin_bp.route("/solicitudes")
@login_required
def solicitudes():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))
    return render_template(
        "dashboard/admin_solicitudes.html",
        admin_active="solicitudes",
        **_dashboard_context(),
    )


@admin_bp.route("/calificaciones")
@login_required
def calificaciones():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))
    return render_template(
        "dashboard/admin_calificaciones.html",
        admin_active="calificaciones",
        **_dashboard_context(),
    )


@admin_bp.route("/usuarios")
@login_required
def usuarios():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))
    return render_template(
        "dashboard/admin_usuarios.html",
        admin_active="usuarios",
        **_dashboard_context(),
    )


@admin_bp.route("/resultados-estudiantes")
@login_required
def resultados_estudiantes():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    estudiantes = (
        User.query.filter_by(role="estudiante")
        .order_by(User.nombre.asc(), User.apellido.asc())
        .all()
    )
    evaluaciones_tally = (
        Evaluacion.query.filter_by(origen="tally")
        .order_by(Evaluacion.estudiante_id.asc(), Evaluacion.fecha_creacion.desc(), Evaluacion.id.desc())
        .all()
    )
    latest_eval_by_student: dict[int, Evaluacion] = {}
    for item in evaluaciones_tally:
        if item.estudiante_id not in latest_eval_by_student:
            latest_eval_by_student[item.estudiante_id] = item

    rows = []
    total_con_resultado = 0
    for estudiante in estudiantes:
        evaluacion = latest_eval_by_student.get(estudiante.id)
        if evaluacion:
            total_con_resultado += 1
            recommendation = build_recommendation_for_student(student=estudiante, evaluacion=evaluacion)
            primary = recommendation.get("primary_recommendation") or {}
            tecnico = primary.get("track_name") or best_track_from_evaluacion(evaluacion)[0]
            promedio = evaluacion.average_score
            fecha_resultado = evaluacion.submitted_at or evaluacion.fecha_creacion
            score_cards = [
                {"label": card["label"], "value_text": card["value_text"]}
                for card in score_cards_from_evaluacion(evaluacion)
            ]
            guidance = recommendation.get("guidance")
        else:
            tecnico = "Sin resultado"
            promedio = None
            fecha_resultado = None
            score_cards = []
            guidance = None

        rows.append(
            {
                "estudiante": estudiante,
                "evaluacion": evaluacion,
                "results_released": bool(evaluacion and evaluacion.results_released),
                "tecnico": tecnico,
                "promedio": promedio,
                "fecha_resultado": fecha_resultado,
                "score_cards": score_cards,
                "guidance": guidance,
            }
        )

    return render_template(
        "dashboard/admin_resultados.html",
        admin_active="resultados",
        resultado_rows=rows,
        estudiantes_total=len(estudiantes),
        estudiantes_con_resultado=total_con_resultado,
        estudiantes_sin_resultado=max(0, len(estudiantes) - total_con_resultado),
        **_dashboard_context(),
    )


@admin_bp.route("/resultados-estudiantes/<int:evaluacion_id>/toggle-release", methods=["POST"])
@login_required
def toggle_resultado_release(evaluacion_id: int):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    evaluacion = Evaluacion.query.filter_by(id=evaluacion_id, origen="tally").first()
    if not evaluacion:
        flash("No se encontro la evaluacion seleccionada.", "warning")
        return redirect(url_for("admin.resultados_estudiantes"))

    release_raw = (request.form.get("release") or "").strip().lower()
    if release_raw in {"1", "true", "on", "si", "yes"}:
        should_release = True
    elif release_raw in {"0", "false", "off", "no"}:
        should_release = False
    else:
        should_release = not bool(evaluacion.results_released)

    evaluacion.results_released = should_release
    db.session.add(evaluacion)
    db.session.commit()

    if should_release:
        flash("Resultado publicado para el estudiante.", "success")
    else:
        flash("Resultado ocultado. El estudiante ya no puede verlo.", "info")
    return redirect(url_for("admin.resultados_estudiantes"))


@admin_bp.route("/asignaciones")
@login_required
def asignaciones():
    """Vista separada para asignacion masiva estudiante-profesor."""
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    profesores = (
        User.query.filter_by(role="profesor").order_by(User.nombre.asc(), User.apellido.asc()).all()
    )
    estudiantes = (
        User.query.filter_by(role="estudiante")
        .order_by(
            case((User.seccion.is_(None), 1), else_=0),
            User.seccion.asc(),
            User.nombre.asc(),
            User.apellido.asc(),
        )
        .all()
    )
    return render_template(
        "dashboard/asignaciones.html",
        profesores=profesores,
        estudiantes=estudiantes,
        admin_active="asignaciones",
    )


@admin_bp.route("/asignaciones/masiva", methods=["POST"])
@login_required
def asignacion_masiva():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    selected_ids_raw = request.form.getlist("estudiante_ids")
    if not selected_ids_raw:
        flash("Selecciona al menos un estudiante para aplicar cambios.", "warning")
        return redirect(url_for("admin.asignaciones"))

    try:
        selected_ids = [int(item) for item in selected_ids_raw]
    except ValueError:
        flash("Seleccion de estudiantes invalida.", "danger")
        return redirect(url_for("admin.asignaciones"))

    estudiantes = User.query.filter(User.id.in_(selected_ids), User.role == "estudiante").all()
    if not estudiantes:
        flash("No se encontraron estudiantes validos para actualizar.", "warning")
        return redirect(url_for("admin.asignaciones"))

    accion = (request.form.get("accion") or "asignar").strip().lower()
    profesor_id_raw = (request.form.get("profesor_id") or "").strip()
    seccion_raw = (request.form.get("seccion") or "").strip().upper()
    seccion = seccion_raw or None
    if seccion and seccion not in SECCIONES_VALIDAS:
        flash("La seccion debe estar entre A y E.", "warning")
        return redirect(url_for("admin.asignaciones"))

    profesor = None
    if accion == "asignar":
        if not profesor_id_raw:
            flash("Debes seleccionar un profesor para asignar.", "warning")
            return redirect(url_for("admin.asignaciones"))
        try:
            profesor_id = int(profesor_id_raw)
        except ValueError:
            flash("Profesor seleccionado invalido.", "danger")
            return redirect(url_for("admin.asignaciones"))
        profesor = User.query.get(profesor_id)
        if not profesor or profesor.role != "profesor":
            flash("No se encontro un profesor valido para la asignacion.", "danger")
            return redirect(url_for("admin.asignaciones"))

    for estudiante in estudiantes:
        if accion == "asignar":
            estudiante.profesor_id = profesor.id
        elif accion == "desasignar":
            estudiante.profesor_id = None
        if seccion is not None:
            estudiante.seccion = seccion
        db.session.add(estudiante)

    db.session.commit()
    if accion == "asignar":
        flash(
            f"Se asignaron {len(estudiantes)} estudiante(s) a {profesor.nombre} {profesor.apellido}.",
            "success",
        )
    else:
        flash(f"Se desasignaron {len(estudiantes)} estudiante(s).", "success")
    return redirect(url_for("admin.asignaciones"))


@admin_bp.route("/asignar-profesor/<int:estudiante_id>", methods=["POST"])
@login_required
def asignar_profesor(estudiante_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    estudiante = User.query.get_or_404(estudiante_id)
    if estudiante.role != "estudiante":
        flash("Solo puedes asignar profesor a usuarios estudiante.", "warning")
        return _redirect_back("admin.asignaciones")

    profesor_id_raw = (request.form.get("profesor_id") or "").strip()
    if profesor_id_raw == "":
        estudiante.profesor_id = None
        db.session.add(estudiante)
        db.session.commit()
        flash(f"Se removio la asignacion de {estudiante.nombre} {estudiante.apellido}.", "info")
        return _redirect_back("admin.asignaciones")

    try:
        profesor_id = int(profesor_id_raw)
    except ValueError:
        flash("Profesor seleccionado invalido.", "danger")
        return _redirect_back("admin.asignaciones")

    profesor = User.query.get(profesor_id)
    if not profesor or profesor.role != "profesor":
        flash("No se encontro un profesor valido para la asignacion.", "danger")
        return _redirect_back("admin.asignaciones")

    estudiante.profesor_id = profesor.id
    db.session.add(estudiante)
    db.session.commit()
    flash(
        f"{estudiante.nombre} {estudiante.apellido} ahora esta asignado a {profesor.nombre} {profesor.apellido}.",
        "success",
    )
    return _redirect_back("admin.asignaciones")


@admin_bp.route("/usuarios/<int:user_id>/perfil", methods=["GET", "POST"])
@login_required
def editar_perfil_usuario(user_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    usuario = User.query.get_or_404(user_id)
    if _is_root_admin_user(usuario):
        flash("El administrador root esta protegido y no se edita desde este modulo.", "warning")
        return redirect(url_for("admin.usuarios"))

    if usuario.role not in {"estudiante", "profesor"}:
        flash("Solo estudiantes o profesores tienen perfil editable.", "warning")
        return redirect(url_for("admin.usuarios"))

    student_profile = StudentProfile.query.filter_by(user_id=usuario.id).first()
    teacher_profile = TeacherProfile.query.filter_by(user_id=usuario.id).first()

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        apellido = (request.form.get("apellido") or "").strip()
        if not nombre or not apellido:
            flash("Nombre y apellido son obligatorios.", "warning")
            return redirect(url_for("admin.editar_perfil_usuario", user_id=usuario.id))

        usuario.nombre = nombre
        usuario.apellido = apellido
        db.session.add(usuario)

        if usuario.role == "estudiante":
            segundo_apellido = (request.form.get("segundo_apellido") or "").strip()
            grado_nivel = (request.form.get("grado_nivel") or "").strip()
            if not segundo_apellido or not grado_nivel:
                flash("Segundo apellido y grado/nivel son obligatorios.", "warning")
                return redirect(url_for("admin.editar_perfil_usuario", user_id=usuario.id))

            fecha_nacimiento, error_fecha = _parse_birth_date(request.form.get("fecha_nacimiento"))
            if error_fecha:
                flash(error_fecha, "warning")
                return redirect(url_for("admin.editar_perfil_usuario", user_id=usuario.id))

            enrollment_year = None
            enrollment_raw = (request.form.get("enrollment_year") or "").strip()
            if enrollment_raw:
                try:
                    enrollment_year = int(enrollment_raw)
                except ValueError:
                    flash("El ano de ingreso debe ser numerico.", "warning")
                    return redirect(url_for("admin.editar_perfil_usuario", user_id=usuario.id))

                current_year = datetime.utcnow().year + 1
                if enrollment_year < 1990 or enrollment_year > current_year:
                    flash(f"El ano de ingreso debe estar entre 1990 y {current_year}.", "warning")
                    return redirect(url_for("admin.editar_perfil_usuario", user_id=usuario.id))

            genero = _normalize_choice(request.form.get("gender"), GENDERS, "prefiero_no_decir")
            academic_status = _normalize_choice(
                request.form.get("academic_status"),
                ACADEMIC_STATUS,
                "activo",
            )
            school_id = (request.form.get("school_id") or DEFAULT_SCHOOL_ID).strip().upper() or DEFAULT_SCHOOL_ID

            student_id_raw = (request.form.get("student_id") or request.form.get("rne") or "").strip()
            student_id = normalize_student_id(student_id_raw)
            student_id_auto = False
            if student_id and not is_valid_student_id(student_id):
                flash("El RNE/Matricula debe tener entre 10 y 24 caracteres alfanumericos.", "warning")
                return redirect(url_for("admin.editar_perfil_usuario", user_id=usuario.id))

            if not student_id:
                student_id = generate_student_id(nombre, apellido, segundo_apellido, fecha_nacimiento)
                student_id_auto = True

            clash = StudentProfile.query.filter_by(student_id=student_id).first()
            if clash and clash.user_id != usuario.id:
                if student_id_auto:
                    student_id = ensure_unique_student_id(student_id, current_user_id=usuario.id)
                else:
                    flash("El RNE/Matricula ya existe. Usa uno diferente.", "warning")
                    return redirect(url_for("admin.editar_perfil_usuario", user_id=usuario.id))

            if not student_profile:
                student_profile = StudentProfile(user_id=usuario.id)
            student_profile.student_id = student_id
            student_profile.segundo_apellido = segundo_apellido
            student_profile.genero = genero
            student_profile.fecha_nacimiento = fecha_nacimiento
            student_profile.edad = calculate_age(fecha_nacimiento)
            student_profile.grado_nivel = grado_nivel
            student_profile.school_id = school_id
            student_profile.enrollment_year = enrollment_year
            student_profile.academic_status = academic_status
            db.session.add(student_profile)
            sync_student_data_if_exists(usuario, student_profile)
            sync_student_interest_from_profile(student_profile)
        else:
            school_id = (
                (request.form.get("teacher_school_id") or DEFAULT_SCHOOL_ID).strip().upper()
                or DEFAULT_SCHOOL_ID
            )
            labor_status = _normalize_choice(request.form.get("labor_status"), LABOR_STATUS, "activo")
            employee_id = normalize_student_id(request.form.get("employee_id") or "") or None

            if employee_id:
                conflict = TeacherProfile.query.filter_by(employee_id=employee_id).first()
                if conflict and conflict.user_id != usuario.id:
                    flash("El codigo de empleado ya existe.", "warning")
                    return redirect(url_for("admin.editar_perfil_usuario", user_id=usuario.id))

            if not teacher_profile:
                teacher_profile = TeacherProfile(user_id=usuario.id)
            teacher_profile.employee_id = employee_id
            teacher_profile.especialidad = (request.form.get("especialidad") or "").strip() or None
            teacher_profile.departamento = (request.form.get("departamento") or "").strip() or None
            teacher_profile.telefono = (request.form.get("telefono") or "").strip() or None
            teacher_profile.school_id = school_id
            teacher_profile.labor_status = labor_status
            db.session.add(teacher_profile)

        db.session.commit()
        flash("Perfil actualizado correctamente.", "success")
        return redirect(url_for("admin.editar_perfil_usuario", user_id=usuario.id))

    return render_template(
        "dashboard/admin_profile_edit.html",
        admin_active="usuarios",
        usuario=usuario,
        student_profile=student_profile,
        teacher_profile=teacher_profile,
        gender_options=GENDER_OPTIONS,
        academic_status_options=ACADEMIC_STATUS_OPTIONS,
        labor_status_options=LABOR_STATUS_OPTIONS,
        default_school_id=DEFAULT_SCHOOL_ID,
    )


@admin_bp.route("/calificaciones/crear", methods=["POST"])
@login_required
def crear_calificacion():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    estudiante_id_raw = (request.form.get("estudiante_id") or "").strip()
    anio_raw = (request.form.get("anio") or "").strip()
    if not estudiante_id_raw:
        flash("Selecciona un estudiante antes de guardar calificaciones.", "warning")
        return redirect(url_for("admin.calificaciones"))

    try:
        estudiante_id = int(estudiante_id_raw)
    except ValueError:
        flash("Estudiante invalido.", "danger")
        return redirect(url_for("admin.calificaciones"))

    estudiante = User.query.filter_by(id=estudiante_id, role="estudiante").first()
    if not estudiante:
        flash("No se encontro un estudiante valido.", "danger")
        return redirect(url_for("admin.calificaciones"))

    anio = None
    if anio_raw:
        try:
            anio = int(anio_raw)
        except ValueError:
            flash("El ano de calificacion debe ser numerico.", "warning")
            return redirect(url_for("admin.calificaciones"))
        if anio < 1990 or anio > datetime.utcnow().year + 1:
            flash("El ano de calificacion esta fuera de rango.", "warning")
            return redirect(url_for("admin.calificaciones"))

    asignaturas = request.form.getlist("asignatura[]") or request.form.getlist("asignatura")
    cantidades = request.form.getlist("cantidad_periodos[]") or request.form.getlist("cantidad_periodos")
    valores_por_periodo = {
        1: request.form.getlist("valor_p1[]") or request.form.getlist("valor_p1"),
        2: request.form.getlist("valor_p2[]") or request.form.getlist("valor_p2"),
        3: request.form.getlist("valor_p3[]") or request.form.getlist("valor_p3"),
        4: request.form.getlist("valor_p4[]") or request.form.getlist("valor_p4"),
    }
    observaciones_por_periodo = {
        1: request.form.getlist("observacion_p1[]") or request.form.getlist("observacion_p1"),
        2: request.form.getlist("observacion_p2[]") or request.form.getlist("observacion_p2"),
        3: request.form.getlist("observacion_p3[]") or request.form.getlist("observacion_p3"),
        4: request.form.getlist("observacion_p4[]") or request.form.getlist("observacion_p4"),
    }

    max_rows = max(len(asignaturas), len(cantidades), *(len(v) for v in valores_por_periodo.values()), 1)
    periodos_validos = {item[0] for item in PERIODOS_CALIFICACION}
    asignaturas_validas = set(ASIGNATURAS_PREDEFINIDAS)

    creadas = 0
    errores = 0
    duplicadas = 0
    for idx in range(max_rows):
        asignatura = (asignaturas[idx] if idx < len(asignaturas) else "").strip()
        cantidad_raw = (cantidades[idx] if idx < len(cantidades) else "").strip()
        valores_fila = {
            p: (valores_por_periodo[p][idx] if idx < len(valores_por_periodo[p]) else "").strip()
            for p in (1, 2, 3, 4)
        }
        observaciones_fila = {
            p: (observaciones_por_periodo[p][idx] if idx < len(observaciones_por_periodo[p]) else "").strip() or None
            for p in (1, 2, 3, 4)
        }

        if not asignatura and not cantidad_raw and not any(valores_fila.values()) and not any(observaciones_fila.values()):
            continue
        if not asignatura:
            errores += 1
            continue
        if asignatura not in asignaturas_validas:
            errores += 1
            continue

        try:
            cantidad_periodos = int(cantidad_raw or "0")
        except ValueError:
            errores += 1
            continue
        if cantidad_periodos < 1 or cantidad_periodos > 4:
            errores += 1
            continue

        for period_idx in range(1, cantidad_periodos + 1):
            periodo = f"periodo_{period_idx}"
            if periodo not in periodos_validos:
                continue
            valor_raw = valores_fila.get(period_idx) or ""
            if not valor_raw:
                errores += 1
                continue
            try:
                valor = float(valor_raw)
            except ValueError:
                errores += 1
                continue
            if valor < 0 or valor > 100:
                errores += 1
                continue

            ya_existe = Calificacion.query.filter_by(
                estudiante_id=estudiante.id,
                asignatura=asignatura,
                periodo=periodo,
                anio=anio,
            ).first()
            if ya_existe:
                duplicadas += 1
                continue

            db.session.add(
                Calificacion(
                    estudiante_id=estudiante.id,
                    asignatura=asignatura,
                    valor=valor,
                    periodo=periodo,
                    anio=anio,
                    observaciones=observaciones_fila.get(period_idx),
                )
            )
            creadas += 1

    if creadas == 0:
        if duplicadas:
            flash(
                "No se registraron nuevas calificaciones porque ya existen en esos periodos. Usa Editar para modificarlas.",
                "warning",
            )
        else:
            flash("No se registraron calificaciones. Revisa los datos del lote.", "warning")
        return redirect(url_for("admin.calificaciones"))

    db.session.commit()
    recalculate_academic_scores_for_student(estudiante.id)
    db.session.commit()
    if errores or duplicadas:
        flash(
            f"Se registraron {creadas} calificacion(es). Omitidas por error: {errores}. Duplicadas bloqueadas: {duplicadas}.",
            "warning",
        )
    else:
        flash(
            f"Se registraron {creadas} calificacion(es) para {estudiante.nombre} {estudiante.apellido}.",
            "success",
        )
    return redirect(url_for("admin.calificaciones"))


@admin_bp.route("/calificaciones/<int:calificacion_id>/editar", methods=["GET", "POST"])
@login_required
def editar_calificacion(calificacion_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    calificacion = Calificacion.query.get_or_404(calificacion_id)
    estudiantes = (
        User.query.filter_by(role="estudiante")
        .order_by(User.nombre.asc(), User.apellido.asc())
        .all()
    )
    periodos_validos = {item[0] for item in PERIODOS_CALIFICACION}
    asignaturas_validas = set(ASIGNATURAS_PREDEFINIDAS)
    if calificacion.asignatura:
        asignaturas_validas.add(calificacion.asignatura)

    if request.method == "POST":
        estudiante_id_raw = (request.form.get("estudiante_id") or "").strip()
        asignatura = (request.form.get("asignatura") or "").strip()
        periodo = (request.form.get("periodo") or "").strip() or None
        valor_raw = (request.form.get("valor") or "").strip()
        anio_raw = (request.form.get("anio") or "").strip()
        observaciones = (request.form.get("observaciones") or "").strip() or None

        try:
            estudiante_id = int(estudiante_id_raw)
        except ValueError:
            flash("Estudiante invalido.", "warning")
            return redirect(url_for("admin.editar_calificacion", calificacion_id=calificacion.id))
        estudiante = User.query.filter_by(id=estudiante_id, role="estudiante").first()
        if not estudiante:
            flash("No se encontro el estudiante seleccionado.", "warning")
            return redirect(url_for("admin.editar_calificacion", calificacion_id=calificacion.id))

        if asignatura not in asignaturas_validas:
            flash("Selecciona una asignatura valida de la lista.", "warning")
            return redirect(url_for("admin.editar_calificacion", calificacion_id=calificacion.id))
        if periodo and periodo not in periodos_validos:
            flash("Selecciona un periodo valido.", "warning")
            return redirect(url_for("admin.editar_calificacion", calificacion_id=calificacion.id))
        try:
            valor = float(valor_raw)
        except ValueError:
            flash("La calificacion debe ser numerica.", "warning")
            return redirect(url_for("admin.editar_calificacion", calificacion_id=calificacion.id))
        if valor < 0 or valor > 100:
            flash("La calificacion debe estar entre 0 y 100.", "warning")
            return redirect(url_for("admin.editar_calificacion", calificacion_id=calificacion.id))

        anio = None
        if anio_raw:
            try:
                anio = int(anio_raw)
            except ValueError:
                flash("El ano de calificacion debe ser numerico.", "warning")
                return redirect(url_for("admin.editar_calificacion", calificacion_id=calificacion.id))
            if anio < 1990 or anio > datetime.utcnow().year + 1:
                flash("El ano de calificacion esta fuera de rango.", "warning")
                return redirect(url_for("admin.editar_calificacion", calificacion_id=calificacion.id))

        duplicada = (
            Calificacion.query.filter_by(
                estudiante_id=estudiante.id,
                asignatura=asignatura,
                periodo=periodo,
                anio=anio,
            )
            .filter(Calificacion.id != calificacion.id)
            .first()
        )
        if duplicada:
            flash(
                "Ya existe una calificacion para ese estudiante, asignatura, periodo y ano. Usa la existente.",
                "warning",
            )
            return redirect(url_for("admin.editar_calificacion", calificacion_id=calificacion.id))

        calificacion.estudiante_id = estudiante.id
        calificacion.asignatura = asignatura
        calificacion.periodo = periodo
        calificacion.valor = valor
        calificacion.anio = anio
        calificacion.observaciones = observaciones
        db.session.add(calificacion)
        db.session.commit()
        recalculate_academic_scores_for_student(estudiante.id)
        db.session.commit()
        flash("Calificacion actualizada correctamente.", "success")
        return redirect(url_for("admin.calificaciones"))

    asignaturas_edicion = list(ASIGNATURAS_PREDEFINIDAS)
    if calificacion.asignatura and calificacion.asignatura not in set(asignaturas_edicion):
        asignaturas_edicion.append(calificacion.asignatura)

    return render_template(
        "dashboard/admin_calificacion_edit.html",
        admin_active="calificaciones",
        calificacion=calificacion,
        estudiantes=estudiantes,
        periodos_calificacion=PERIODOS_CALIFICACION,
        asignaturas_predefinidas=asignaturas_edicion,
        year_options=[datetime.utcnow().year - 1, datetime.utcnow().year, datetime.utcnow().year + 1],
    )


@admin_bp.route("/calificaciones/recalcular-promedios", methods=["POST"])
@login_required
def recalcular_promedios_academicos():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    rows = recalculate_all_academic_scores()
    db.session.commit()
    flash(f"Promedios academicos recalculados. Registros actualizados: {rows}.", "success")
    return redirect(url_for("admin.calificaciones"))


@admin_bp.route("/submissions/sincronizar", methods=["POST"])
@login_required
def sincronizar_submission_tally():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    submission_id = (request.form.get("submission_id") or "").strip()
    estudiante_id_raw = (request.form.get("estudiante_id") or "").strip()
    if not submission_id and not estudiante_id_raw:
        flash("Indica un Submission ID o selecciona un estudiante para sincronizar.", "warning")
        return redirect(url_for("admin.calificaciones"))

    estudiante = None
    if estudiante_id_raw:
        try:
            estudiante_id = int(estudiante_id_raw)
        except ValueError:
            flash("El estudiante seleccionado es invalido.", "warning")
            return redirect(url_for("admin.calificaciones"))
        estudiante = User.query.filter_by(id=estudiante_id, role="estudiante").first()
        if not estudiante:
            flash("No se encontro el estudiante para sincronizar.", "warning")
            return redirect(url_for("admin.calificaciones"))

    submission_payload = None
    error = None
    if submission_id:
        submission_payload, error = fetch_submission_from_tally(
            submission_id=submission_id,
            form_id=request.form.get("form_id"),
        )

    resolved_submission_id = (
        str((submission_payload or {}).get("id") or submission_id).strip() or submission_id
    )
    evaluacion = None
    if resolved_submission_id:
        evaluacion = Evaluacion.query.filter_by(
            origen="tally",
            referencia_externa=resolved_submission_id,
        ).first()

    if not evaluacion and estudiante:
        evaluacion = (
            Evaluacion.query.filter_by(estudiante_id=estudiante.id, origen="tally")
            .order_by(Evaluacion.fecha_creacion.desc())
            .first()
        )

    if not evaluacion:
        if not estudiante:
            flash(
                "No existe evaluacion previa con ese submission ID. Selecciona un estudiante para crearla.",
                "warning",
            )
            return redirect(url_for("admin.calificaciones"))
        evaluacion = Evaluacion(estudiante_id=estudiante.id, estado="completada", origen="tally")

    fallback_matricula = (
        estudiante.student_profile.student_id
        if estudiante and estudiante.student_profile
        else (evaluacion.estudiante.student_profile.student_id if evaluacion.estudiante and evaluacion.estudiante.student_profile else None)
    )
    if submission_payload:
        apply_submission_to_evaluacion(
            evaluacion,
            submission_payload,
            fallback_matricula=fallback_matricula,
        )
    else:
        if fallback_matricula and not evaluacion.matricula_estudiante:
            evaluacion.matricula_estudiante = fallback_matricula

    if evaluacion.average_score is None:
        sheet_scores = fetch_scores_from_submissions_sheet(
            submission_id=resolved_submission_id,
            matricula_estudiante=fallback_matricula,
        )
        if sheet_scores:
            apply_sheet_scores_to_evaluacion(evaluacion, sheet_scores)

    db.session.add(evaluacion)
    db.session.flush()
    upsert_form_response_from_tally(
        estudiante_id=evaluacion.estudiante_id,
        evaluacion_id=evaluacion.id,
        submission_payload=submission_payload,
        fallback_submission_id=resolved_submission_id,
        fallback_form_id=request.form.get("form_id"),
        fallback_matricula=evaluacion.matricula_estudiante or fallback_matricula,
    )
    db.session.commit()
    pred_sync_ok = _sync_ai_prediction_safe(evaluacion)

    if evaluacion.average_score is None:
        flash(
            "Sincronizacion parcial. No se detectaron scores en Tally/hoja para esta submission. "
            "Verifica permisos de la hoja CSV y nombres de columnas de score.",
            "warning",
        )
    else:
        flash(
            f"Submission {resolved_submission_id or 'sin ID'} sincronizado. Promedio: {evaluacion.average_score}",
            "success",
        )
    if not pred_sync_ok:
        flash("La evaluacion se sincronizo, pero ai_predictions no pudo actualizarse.", "warning")
    return redirect(url_for("admin.calificaciones"))


@admin_bp.route("/submissions/completar-scores", methods=["POST"])
@login_required
def completar_scores_tally():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    limit_raw = (request.form.get("limit") or "300").strip()
    try:
        limit = int(limit_raw)
    except ValueError:
        limit = 300
    limit = max(1, min(limit, 1000))

    candidatas = (
        Evaluacion.query.filter_by(origen="tally")
        .order_by(Evaluacion.fecha_creacion.desc())
        .limit(limit)
        .all()
    )
    if not candidatas:
        flash("No hay evaluaciones Tally para revisar.", "info")
        return redirect(url_for("admin.calificaciones"))

    revisadas = 0
    actualizadas = 0
    sin_datos = 0
    prediction_targets: list[int] = []
    for evaluacion in candidatas:
        revisadas += 1
        before = (
            evaluacion.logical_reasoning_score,
            evaluacion.problem_resolution_score,
            evaluacion.detail_attention_score,
            evaluacion.creativity_score,
            evaluacion.tech_ability_score,
            evaluacion.average_score,
            evaluacion.referencia_externa,
            evaluacion.matricula_estudiante,
        )

        fallback_matricula = (
            evaluacion.matricula_estudiante
            or (
                evaluacion.estudiante.student_profile.student_id
                if evaluacion.estudiante and evaluacion.estudiante.student_profile
                else None
            )
        )
        referencia = (evaluacion.referencia_externa or "").strip() or None

        submission_payload = None
        if referencia:
            submission_payload, _ = fetch_submission_from_tally(
                submission_id=referencia,
                form_id=request.form.get("form_id"),
            )
        if submission_payload:
            apply_submission_to_evaluacion(
                evaluacion,
                submission_payload,
                fallback_matricula=fallback_matricula,
            )

        if evaluacion.average_score is None:
            sheet_scores = fetch_scores_from_submissions_sheet(
                submission_id=referencia,
                matricula_estudiante=fallback_matricula,
            )
            if sheet_scores:
                apply_sheet_scores_to_evaluacion(evaluacion, sheet_scores)

        upsert_form_response_from_tally(
            estudiante_id=evaluacion.estudiante_id,
            evaluacion_id=evaluacion.id,
            submission_payload=submission_payload,
            fallback_submission_id=referencia,
            fallback_form_id=request.form.get("form_id"),
            fallback_matricula=evaluacion.matricula_estudiante or fallback_matricula,
        )

        after = (
            evaluacion.logical_reasoning_score,
            evaluacion.problem_resolution_score,
            evaluacion.detail_attention_score,
            evaluacion.creativity_score,
            evaluacion.tech_ability_score,
            evaluacion.average_score,
            evaluacion.referencia_externa,
            evaluacion.matricula_estudiante,
        )
        if after != before:
            db.session.add(evaluacion)
            actualizadas += 1
            prediction_targets.append(evaluacion.id)
        elif evaluacion.average_score is None:
            sin_datos += 1
        else:
            prediction_targets.append(evaluacion.id)

    if actualizadas:
        db.session.commit()
    else:
        db.session.rollback()

    predicciones_actualizadas = 0
    for evaluacion_id in prediction_targets:
        evaluacion = Evaluacion.query.get(evaluacion_id)
        if not evaluacion:
            continue
        if _sync_ai_prediction_safe(evaluacion):
            predicciones_actualizadas += 1

    if actualizadas:
        flash(
            "Reproceso completado. "
            f"Revisadas: {revisadas}. Actualizadas con scores: {actualizadas}. "
            f"Predicciones sincronizadas: {predicciones_actualizadas}. Sin datos: {sin_datos}.",
            "success",
        )
    elif predicciones_actualizadas:
        flash(
            "Reproceso completado. "
            f"No hubo cambios en scores, pero se sincronizaron {predicciones_actualizadas} predicciones.",
            "info",
        )
    else:
        flash(
            f"Reproceso completado. Revisadas: {revisadas}. No hubo cambios. Sin datos: {sin_datos}.",
            "warning",
        )
    return redirect(url_for("admin.calificaciones"))


@admin_bp.route("/evaluaciones/limpiar-duplicados", methods=["POST"])
@login_required
def limpiar_duplicados_evaluaciones():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    evaluaciones = (
        Evaluacion.query.filter_by(origen="tally")
        .order_by(Evaluacion.fecha_creacion.desc(), Evaluacion.id.desc())
        .all()
    )
    seen_student = set()
    seen_submission = set()
    seen_matricula = set()
    to_delete = []

    for evaluacion in evaluaciones:
        student_key = evaluacion.estudiante_id
        submission_key = (evaluacion.estudiante_id, (evaluacion.referencia_externa or "").strip())
        matricula_key = (evaluacion.estudiante_id, (evaluacion.matricula_estudiante or "").strip())

        duplicate = False
        if student_key in seen_student:
            duplicate = True
        if submission_key[1] and submission_key in seen_submission:
            duplicate = True
        if matricula_key[1] and matricula_key in seen_matricula:
            duplicate = True

        if duplicate:
            to_delete.append(evaluacion)
            continue

        seen_student.add(student_key)
        if submission_key[1]:
            seen_submission.add(submission_key)
        if matricula_key[1]:
            seen_matricula.add(matricula_key)

    for evaluacion in to_delete:
        db.session.delete(evaluacion)
    db.session.commit()
    flash(f"Limpieza completada. Evaluaciones duplicadas eliminadas: {len(to_delete)}.", "success")
    return redirect(url_for("admin.calificaciones"))


@admin_bp.route("/calificaciones/<int:calificacion_id>/eliminar", methods=["POST"])
@login_required
def eliminar_calificacion(calificacion_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    calificacion = Calificacion.query.get_or_404(calificacion_id)
    estudiante_id = calificacion.estudiante_id
    db.session.delete(calificacion)
    db.session.commit()
    recalculate_academic_scores_for_student(estudiante_id)
    db.session.commit()
    flash("Calificacion eliminada.", "info")
    return redirect(url_for("admin.calificaciones"))


@admin_bp.route("/solicitudes-perfil/<int:solicitud_id>/aprobar", methods=["POST"])
@login_required
def aprobar_solicitud_perfil(solicitud_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    solicitud = ProfileEditRequest.query.get_or_404(solicitud_id)
    admin_note = (
        request.form.get("admin_note")
        or request.form.get(f"admin_note_{solicitud_id}")
        or ""
    ).strip()
    try:
        _review_profile_request(solicitud, action="aprobar", note=admin_note)
        db.session.commit()
        flash(f"Solicitud #{solicitud.id} aprobada y aplicada.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo aprobar la solicitud por un error inesperado.", "danger")
    return redirect(url_for("admin.solicitudes"))


@admin_bp.route("/solicitudes-perfil/<int:solicitud_id>/rechazar", methods=["POST"])
@login_required
def rechazar_solicitud_perfil(solicitud_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    solicitud = ProfileEditRequest.query.get_or_404(solicitud_id)
    admin_note = (
        request.form.get("admin_note")
        or request.form.get(f"admin_note_{solicitud_id}")
        or ""
    ).strip()
    try:
        _review_profile_request(solicitud, action="rechazar", note=admin_note)
        db.session.commit()
        flash(f"Solicitud #{solicitud.id} rechazada.", "info")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        flash("No se pudo rechazar la solicitud por un error inesperado.", "danger")
    return redirect(url_for("admin.solicitudes"))


@admin_bp.route("/solicitudes-perfil/masiva", methods=["POST"])
@login_required
def revisar_solicitudes_masiva():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    selected_ids_raw = request.form.getlist("solicitud_ids")
    if not selected_ids_raw:
        flash("Selecciona al menos una solicitud para la accion masiva.", "warning")
        return redirect(url_for("admin.solicitudes"))

    action = (request.form.get("accion_solicitudes") or "").strip().lower()
    note = (request.form.get("admin_note_masivo") or "").strip()
    if action not in {"aprobar", "rechazar"}:
        flash("Selecciona una accion valida para solicitudes.", "warning")
        return redirect(url_for("admin.solicitudes"))

    try:
        selected_ids = [int(item) for item in selected_ids_raw]
    except ValueError:
        flash("Seleccion invalida de solicitudes.", "danger")
        return redirect(url_for("admin.solicitudes"))

    aprobadas = 0
    rechazadas = 0
    omitidas = 0
    errores = 0

    for solicitud_id in selected_ids:
        solicitud = ProfileEditRequest.query.get(solicitud_id)
        if not solicitud:
            omitidas += 1
            continue
        try:
            _review_profile_request(solicitud, action=action, note=note)
            db.session.commit()
            if action == "aprobar":
                aprobadas += 1
            else:
                rechazadas += 1
        except ValueError:
            db.session.rollback()
            omitidas += 1
        except Exception:
            db.session.rollback()
            errores += 1

    if action == "aprobar":
        flash(
            f"Solicitudes aprobadas: {aprobadas}. Omitidas: {omitidas}. Errores: {errores}.",
            "success" if not errores else "warning",
        )
    else:
        flash(
            f"Solicitudes rechazadas: {rechazadas}. Omitidas: {omitidas}. Errores: {errores}.",
            "info" if not errores else "warning",
        )

    return redirect(url_for("admin.solicitudes"))


@admin_bp.route("/solicitudes-comentario/<int:solicitud_id>/aprobar", methods=["POST"])
@login_required
def aprobar_solicitud_comentario(solicitud_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    solicitud = ComentarioEditRequest.query.get_or_404(solicitud_id)
    if solicitud.status != "pendiente":
        flash("La solicitud de comentario ya fue revisada.", "warning")
        return redirect(url_for("admin.solicitudes"))

    admin_note = (
        request.form.get("admin_note")
        or request.form.get(f"admin_note_comment_{solicitud_id}")
        or ""
    ).strip()

    solicitud.status = "aprobada"
    solicitud.admin_note = admin_note or None
    solicitud.reviewed_by = current_user.id
    solicitud.reviewed_at = db.func.current_timestamp()
    db.session.add(solicitud)
    db.session.commit()
    flash(f"Solicitud de comentario #{solicitud.id} aprobada.", "success")
    return redirect(url_for("admin.solicitudes"))


@admin_bp.route("/solicitudes-comentario/<int:solicitud_id>/rechazar", methods=["POST"])
@login_required
def rechazar_solicitud_comentario(solicitud_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    solicitud = ComentarioEditRequest.query.get_or_404(solicitud_id)
    if solicitud.status != "pendiente":
        flash("La solicitud de comentario ya fue revisada.", "warning")
        return redirect(url_for("admin.solicitudes"))

    admin_note = (
        request.form.get("admin_note")
        or request.form.get(f"admin_note_comment_{solicitud_id}")
        or ""
    ).strip()

    solicitud.status = "rechazada"
    solicitud.admin_note = admin_note or None
    solicitud.reviewed_by = current_user.id
    solicitud.reviewed_at = db.func.current_timestamp()
    db.session.add(solicitud)
    db.session.commit()
    flash(f"Solicitud de comentario #{solicitud.id} rechazada.", "info")
    return redirect(url_for("admin.solicitudes"))


@admin_bp.route("/usuarios/<int:user_id>/rol", methods=["POST"])
@login_required
def actualizar_rol(user_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    usuario = User.query.get_or_404(user_id)
    if _is_root_admin_user(usuario):
        flash("El administrador root esta protegido y no puede cambiar de rol.", "warning")
        return redirect(url_for("admin.usuarios"))

    nuevo_rol = (request.form.get("role") or "").strip().lower()
    if nuevo_rol not in ROLES_VALIDOS:
        flash("Rol invalido.", "danger")
        return redirect(url_for("admin.usuarios"))

    if usuario.id == current_user.id and nuevo_rol != "admin":
        flash("No puedes quitarte tu propio rol de administrador.", "warning")
        return redirect(url_for("admin.usuarios"))

    rol_anterior = usuario.role
    if rol_anterior == nuevo_rol:
        flash(f"El usuario {usuario.email} ya tiene rol {nuevo_rol}.", "info")
        return redirect(url_for("admin.usuarios"))

    try:
        # Conservamos perfiles academicos al cambiar de rol para permitir reversa sin perdida de datos.
        usuario.role = nuevo_rol
        if nuevo_rol != "estudiante":
            usuario.profesor_id = None
            usuario.seccion = None

        if rol_anterior == "profesor" and nuevo_rol != "profesor":
            User.query.filter_by(profesor_id=usuario.id).update(
                {"profesor_id": None}, synchronize_session=False
            )

        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Fallo al actualizar rol user_id=%s de %s a %s",
            user_id,
            rol_anterior,
            nuevo_rol,
        )
        flash("No se pudo actualizar el rol del usuario. Intenta nuevamente.", "danger")
        return redirect(url_for("admin.usuarios"))

    flash(
        f"Rol actualizado: {usuario.nombre} {usuario.apellido} ahora es {nuevo_rol}.",
        "success",
    )
    return redirect(url_for("admin.usuarios"))


@admin_bp.route("/usuarios/<int:user_id>/estado", methods=["POST"])
@login_required
def actualizar_estado(user_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    usuario = User.query.get_or_404(user_id)
    if _is_root_admin_user(usuario):
        flash("El administrador root esta protegido y no puede desactivarse.", "warning")
        return _redirect_back("admin.usuarios")

    activar = (request.form.get("activo") or "").strip() in {"1", "true", "on", "True"}

    if usuario.id == current_user.id and not activar:
        flash("No puedes desactivar tu propia cuenta de administrador.", "warning")
        return _redirect_back("admin.usuarios")

    usuario.activo = activar
    db.session.add(usuario)
    db.session.commit()

    estado_txt = "activado" if activar else "desactivado"
    flash(
        f"Usuario {usuario.nombre} {usuario.apellido} {estado_txt} correctamente.",
        "success",
    )
    return _redirect_back("admin.usuarios")


@admin_bp.route("/usuarios/<int:user_id>/verificacion", methods=["POST"])
@login_required
def actualizar_verificacion(user_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    usuario = User.query.get_or_404(user_id)
    if _is_root_admin_user(usuario):
        flash("El administrador root esta protegido y no puede modificar su verificacion.", "warning")
        return _redirect_back("admin.usuarios")

    verificado = (request.form.get("email_verificado") or "").strip() in {
        "1",
        "true",
        "on",
        "True",
    }
    usuario.email_verificado = verificado
    if verificado and not usuario.email_verificado_at:
        usuario.email_verificado_at = db.func.current_timestamp()
    if not verificado:
        usuario.email_verificado_at = None
    db.session.add(usuario)
    db.session.commit()

    estado_txt = "verificado" if verificado else "marcado como no verificado"
    flash(f"Correo de {usuario.email} {estado_txt}.", "success")
    return _redirect_back("admin.usuarios")


@admin_bp.route("/usuarios/<int:user_id>/seccion", methods=["POST"])
@login_required
def actualizar_seccion(user_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    usuario = User.query.get_or_404(user_id)
    if usuario.role != "estudiante":
        flash("Solo estudiantes pueden tener seccion.", "warning")
        return _redirect_back("admin.asignaciones")

    seccion_raw = (request.form.get("seccion") or "").strip().upper()
    seccion = seccion_raw or None
    if seccion and seccion not in SECCIONES_VALIDAS:
        flash("La seccion debe estar entre A y E.", "warning")
        return _redirect_back("admin.asignaciones")
    usuario.seccion = seccion
    db.session.add(usuario)
    db.session.commit()
    flash(f"Seccion actualizada para {usuario.nombre} {usuario.apellido}.", "success")
    return _redirect_back("admin.asignaciones")


@admin_bp.route("/usuarios/<int:user_id>/eliminar", methods=["POST"])
@login_required
def eliminar_usuario_login(user_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    usuario = User.query.get_or_404(user_id)
    if _is_root_admin_user(usuario):
        flash("El administrador root esta protegido y no puede revocarse.", "warning")
        return _redirect_back("admin.usuarios")

    if usuario.id == current_user.id:
        flash("No puedes eliminar tu propio acceso de administrador.", "warning")
        return _redirect_back("admin.usuarios")

    if not _is_delete_confirmed():
        flash("Confirmacion invalida. No se elimino el acceso.", "warning")
        return _redirect_back("admin.usuarios")

    original_email = usuario.email
    _disable_user_access(usuario)
    db.session.commit()
    flash(
        f"Acceso eliminado para {original_email}. Se conservaron perfiles y datos academicos.",
        "success",
    )
    return _redirect_back("admin.usuarios")


@admin_bp.route("/usuarios/<int:user_id>/eliminar-perfil", methods=["POST"])
@login_required
def eliminar_perfil_academico(user_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    usuario = User.query.get_or_404(user_id)
    if _is_root_admin_user(usuario):
        flash("El administrador root esta protegido y no permite eliminar perfil.", "warning")
        return _redirect_back("admin.usuarios")

    if not _is_delete_confirmed():
        flash("Confirmacion invalida. No se elimino el perfil academico.", "warning")
        return _redirect_back("admin.usuarios")

    removed, removed_sections = _delete_academic_profile_for_user(usuario)
    if not removed:
        flash("El usuario no tiene perfil academico para eliminar.", "info")
        return _redirect_back("admin.usuarios")

    db.session.commit()
    flash(
        f"Perfil academico eliminado para {usuario.nombre} {usuario.apellido}: {', '.join(removed_sections)}.",
        "success",
    )
    return _redirect_back("admin.usuarios")


@admin_bp.route("/usuarios/<int:user_id>/eliminar-completo", methods=["POST"])
@login_required
def eliminar_usuario_completo(user_id):
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    usuario = User.query.get_or_404(user_id)
    if _is_root_admin_user(usuario):
        flash("El administrador root esta protegido y no puede eliminarse.", "warning")
        return _redirect_back("admin.usuarios")

    if usuario.id == current_user.id:
        flash("No puedes eliminar tu propia cuenta de administrador.", "warning")
        return _redirect_back("admin.usuarios")
    if usuario.role == "admin":
        flash("Por seguridad, la eliminacion completa de administradores esta bloqueada.", "warning")
        return _redirect_back("admin.usuarios")
    if not _is_delete_confirmed():
        flash("Confirmacion invalida. No se elimino el usuario.", "warning")
        return _redirect_back("admin.usuarios")

    full_name = f"{usuario.nombre} {usuario.apellido}"
    original_email = usuario.email
    _delete_user_hard(usuario)
    db.session.commit()
    flash(
        f"Usuario eliminado completamente: {full_name} ({original_email}).",
        "success",
    )
    return _redirect_back("admin.usuarios")


@admin_bp.route("/usuarios/acciones-masivas", methods=["POST"])
@login_required
def usuarios_accion_masiva():
    if not _check_admin_access():
        return redirect(url_for("dashboard.index"))

    selected_ids_raw = request.form.getlist("user_ids")
    if not selected_ids_raw:
        flash("Selecciona al menos un usuario para la accion masiva.", "warning")
        return _redirect_back("admin.usuarios")

    action = (request.form.get("accion_usuarios") or "").strip().lower()
    valid_actions = {
        "desactivar_acceso",
        "eliminar_acceso",
        "eliminar_perfil",
        "eliminar_usuario",
    }
    if action not in valid_actions:
        flash("Selecciona una accion masiva valida.", "warning")
        return _redirect_back("admin.usuarios")

    destructive_actions = {"eliminar_acceso", "eliminar_perfil", "eliminar_usuario"}
    if action in destructive_actions and not _is_delete_confirmed():
        flash("Confirmacion invalida para la accion masiva.", "warning")
        return _redirect_back("admin.usuarios")

    selected_ids: list[int] = []
    for raw_id in selected_ids_raw:
        try:
            parsed = int(raw_id)
        except (TypeError, ValueError):
            continue
        if parsed not in selected_ids:
            selected_ids.append(parsed)

    if not selected_ids:
        flash("No se detectaron usuarios validos en la seleccion.", "warning")
        return _redirect_back("admin.usuarios")

    users = User.query.filter(User.id.in_(selected_ids)).all()
    user_by_id = {user.id: user for user in users}

    processed = 0
    skipped = 0
    for user_id in selected_ids:
        usuario = user_by_id.get(user_id)
        if not usuario:
            skipped += 1
            continue
        if _is_root_admin_user(usuario):
            skipped += 1
            continue
        if usuario.id == current_user.id:
            skipped += 1
            continue
        if action == "eliminar_usuario" and usuario.role == "admin":
            skipped += 1
            continue

        if action == "desactivar_acceso":
            usuario.activo = False
            db.session.add(usuario)
        elif action == "eliminar_acceso":
            _disable_user_access(usuario)
        elif action == "eliminar_perfil":
            _delete_academic_profile_for_user(usuario)
        elif action == "eliminar_usuario":
            _delete_user_hard(usuario)
        processed += 1

    db.session.commit()
    if processed:
        flash(
            f"Accion masiva completada. Procesados: {processed}. Omitidos: {skipped}.",
            "success",
        )
    else:
        flash(
            f"No se aplicaron cambios. Omitidos: {skipped}.",
            "info",
        )
    return _redirect_back("admin.usuarios")
