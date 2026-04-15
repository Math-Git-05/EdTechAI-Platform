from datetime import datetime
import json

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models.evaluacion import Evaluacion
from app.models.profile_edit_request import ProfileEditRequest
from app.models.student_profile import StudentProfile
from app.services.evaluacion_service import score_cards_from_evaluacion
from app.services.recommendation_engine_service import build_recommendation_for_student
from app.services.profile_service import (
    DEFAULT_SCHOOL_ID,
    GENDERS,
    calculate_age,
    is_valid_student_id,
    normalize_student_id,
)

estudiante_bp = Blueprint("estudiante", __name__)


def _ensure_student_access():
    if current_user.role != "estudiante":
        flash("Este panel esta disponible solo para estudiantes.", "warning")
        return False
    return True


def _parse_birth_date(raw_date: str):
    raw = (raw_date or "").strip()
    if not raw:
        return None, "Debes indicar la fecha de nacimiento."
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), None
    except ValueError:
        return None, "Formato de fecha de nacimiento invalido."


@estudiante_bp.route("/")
@login_required
def index():
    """Dashboard del estudiante."""
    if not _ensure_student_access():
        return redirect(url_for("dashboard.index"))

    evaluacion = (
        Evaluacion.query.filter_by(estudiante_id=current_user.id, origen="tally")
        .order_by(Evaluacion.fecha_creacion.desc())
        .first()
    )
    evaluacion_completada = evaluacion is not None
    recommendation_summary = None
    if evaluacion:
        recommendation = build_recommendation_for_student(student=current_user, evaluacion=evaluacion)
        primary = recommendation.get("primary_recommendation")
        if primary:
            recommendation_summary = {
                "track_name": primary.get("track_name"),
                "affinity_level": primary.get("affinity_level"),
                "score": primary.get("score"),
                "guidance": recommendation.get("guidance"),
            }
    pending_request = (
        ProfileEditRequest.query.filter_by(user_id=current_user.id, status="pendiente")
        .order_by(ProfileEditRequest.requested_at.desc())
        .first()
    )

    return render_template(
        "dashboard/estudiante.html",
        user=current_user,
        evaluacion_completada=evaluacion_completada,
        evaluacion=evaluacion,
        score_cards=(score_cards_from_evaluacion(evaluacion) if evaluacion else []),
        recommendation_summary=recommendation_summary,
        pending_profile_request=pending_request,
    )


@estudiante_bp.route("/perfil", methods=["GET", "POST"])
@login_required
def perfil():
    if not _ensure_student_access():
        return redirect(url_for("dashboard.index"))

    profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        flash("No se encontro el perfil academico del estudiante.", "warning")
        return redirect(url_for("estudiante.index"))

    pending_request = (
        ProfileEditRequest.query.filter_by(user_id=current_user.id, status="pendiente")
        .order_by(ProfileEditRequest.requested_at.desc())
        .first()
    )
    request_history = (
        ProfileEditRequest.query.filter_by(user_id=current_user.id)
        .order_by(ProfileEditRequest.requested_at.desc())
        .limit(10)
        .all()
    )

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        apellido = (request.form.get("apellido") or "").strip()
        segundo_apellido = (request.form.get("segundo_apellido") or "").strip()
        genero = (request.form.get("genero") or "").strip().lower()
        grado_nivel = (request.form.get("grado_nivel") or "").strip()
        enrollment_raw = (request.form.get("enrollment_year") or "").strip()
        student_id = normalize_student_id(request.form.get("student_id") or profile.student_id)

        if not nombre or not apellido or not segundo_apellido or not grado_nivel:
            flash("Nombre, apellido, segundo apellido y grado/nivel son obligatorios.", "warning")
            return redirect(url_for("estudiante.perfil"))

        if genero not in GENDERS:
            genero = profile.genero or "prefiero_no_decir"

        fecha_nacimiento, fecha_error = _parse_birth_date(request.form.get("fecha_nacimiento"))
        if fecha_error:
            flash(fecha_error, "warning")
            return redirect(url_for("estudiante.perfil"))

        if not student_id or not is_valid_student_id(student_id):
            flash("El RNE/Matricula debe tener entre 10 y 24 caracteres alfanumericos.", "warning")
            return redirect(url_for("estudiante.perfil"))

        existing = StudentProfile.query.filter_by(student_id=student_id).first()
        if existing and existing.user_id != current_user.id:
            flash("El RNE/Matricula ya esta en uso por otro estudiante.", "warning")
            return redirect(url_for("estudiante.perfil"))

        enrollment_year = None
        if enrollment_raw:
            try:
                enrollment_year = int(enrollment_raw)
            except ValueError:
                flash("El ano de ingreso debe ser numerico.", "warning")
                return redirect(url_for("estudiante.perfil"))
            current_year = datetime.utcnow().year + 1
            if enrollment_year < 1990 or enrollment_year > current_year:
                flash(f"El ano de ingreso debe estar entre 1990 y {current_year}.", "warning")
                return redirect(url_for("estudiante.perfil"))

        payload = {
            "role": "estudiante",
            "nombre": nombre,
            "apellido": apellido,
            "student_profile": {
                "student_id": student_id,
                "segundo_apellido": segundo_apellido,
                "genero": genero,
                "fecha_nacimiento": fecha_nacimiento.isoformat(),
                "edad": calculate_age(fecha_nacimiento),
                "grado_nivel": grado_nivel,
                "school_id": profile.school_id or DEFAULT_SCHOOL_ID,
                "enrollment_year": enrollment_year,
                "academic_status": profile.academic_status or "activo",
            },
        }

        if pending_request:
            pending_request.request_payload_json = json.dumps(payload, ensure_ascii=False)
            pending_request.admin_note = None
            pending_request.reviewed_at = None
            pending_request.reviewed_by = None
            pending_request.requested_at = db.func.current_timestamp()
            pending_request.status = "pendiente"
            db.session.add(pending_request)
            flash("Tu solicitud pendiente fue actualizada y reenviada al administrador.", "info")
        else:
            pending_request = ProfileEditRequest(
                user_id=current_user.id,
                status="pendiente",
                request_payload_json=json.dumps(payload, ensure_ascii=False),
            )
            db.session.add(pending_request)
            flash("Solicitud de edicion enviada. Queda pendiente de aprobacion administrativa.", "success")

        db.session.commit()
        return redirect(url_for("estudiante.perfil"))

    payload_preview = None
    if pending_request and pending_request.request_payload_json:
        try:
            payload_preview = json.loads(pending_request.request_payload_json)
        except Exception:
            payload_preview = None

    return render_template(
        "dashboard/estudiante_perfil.html",
        user=current_user,
        profile=profile,
        pending_request=pending_request,
        request_history=request_history,
        payload_preview=payload_preview,
    )
