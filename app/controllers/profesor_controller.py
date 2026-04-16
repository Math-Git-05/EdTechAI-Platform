import json

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db
from app.models.comentario_edit_request import ComentarioEditRequest
from app.models.evaluacion import Evaluacion
from app.models.profile_edit_request import ProfileEditRequest
from app.models.teacher_profile import TeacherProfile
from app.models.user import User
from app.services.profile_service import DEFAULT_SCHOOL_ID, LABOR_STATUS, normalize_student_id
from app.services.settings_service import get_bool_setting
from app.services.teacher_observation_service import upsert_teacher_observation

profesor_bp = Blueprint('profesor', __name__)


def _mask_email(email: str | None) -> str:
    value = (email or "").strip()
    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    local = local.strip()
    domain = domain.strip()
    if len(local) <= 2:
        local_masked = local[:1] + "***"
    else:
        local_masked = local[:2] + "***"
    return f"{local_masked}@{domain}"


def _sync_teacher_observation_safe(*, student_id: int, teacher_id: int, comment: str, observed_at) -> bool:
    try:
        upsert_teacher_observation(
            student_id=student_id,
            teacher_id=teacher_id,
            observation_text=comment,
            observed_at=observed_at,
        )
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False

@profesor_bp.route('/')
@login_required
def index():
    """Dashboard del profesor"""
    if current_user.role != "profesor":
        flash("Este panel esta disponible solo para profesores.", "warning")
        return redirect(url_for("dashboard.index"))

    estudiantes = (
        User.query.filter_by(role="estudiante", profesor_id=current_user.id)
        .order_by(User.nombre.asc(), User.apellido.asc())
        .all()
    )
    allow_email_view = get_bool_setting("allow_professor_view_student_email", default=False)
    student_email_display: dict[int, str] = {}
    for est in estudiantes:
        student_email_display[est.id] = est.email if allow_email_view else _mask_email(est.email)

    secciones_disponibles = sorted(
        {
            (est.seccion or "").strip().upper()
            for est in estudiantes
            if (est.seccion or "").strip()
        }
    )
    estudiantes_ids = [est.id for est in estudiantes]
    evaluaciones_total = 0
    comentarios_por_estudiante = {}
    evaluaciones_por_estudiante = {}
    score_averages = {
        "logical_reasoning_score": None,
        "problem_resolution_score": None,
        "detail_attention_score": None,
        "creativity_score": None,
        "tech_ability_score": None,
        "average_score": None,
    }
    if estudiantes_ids:
        evaluaciones_total = (
            db.session.query(func.count(Evaluacion.id))
            .filter(Evaluacion.estudiante_id.in_(estudiantes_ids))
            .scalar()
            or 0
        )
        avg_row = (
            db.session.query(
                func.avg(Evaluacion.logical_reasoning_score),
                func.avg(Evaluacion.problem_resolution_score),
                func.avg(Evaluacion.detail_attention_score),
                func.avg(Evaluacion.creativity_score),
                func.avg(Evaluacion.tech_ability_score),
                func.avg(Evaluacion.average_score),
            )
            .filter(Evaluacion.origen == "tally", Evaluacion.estudiante_id.in_(estudiantes_ids))
            .first()
        )
        if avg_row:
            score_averages = {
                "logical_reasoning_score": round(avg_row[0], 2) if avg_row[0] is not None else None,
                "problem_resolution_score": round(avg_row[1], 2) if avg_row[1] is not None else None,
                "detail_attention_score": round(avg_row[2], 2) if avg_row[2] is not None else None,
                "creativity_score": round(avg_row[3], 2) if avg_row[3] is not None else None,
                "tech_ability_score": round(avg_row[4], 2) if avg_row[4] is not None else None,
                "average_score": round(avg_row[5], 2) if avg_row[5] is not None else None,
            }
        comentarios_rows = (
            Evaluacion.query.with_entities(Evaluacion.estudiante_id, Evaluacion.comentario_profesor)
            .filter(
                Evaluacion.estudiante_id.in_(estudiantes_ids),
                Evaluacion.profesor_comentario_id == current_user.id,
            )
            .all()
        )
        commented_ids = {
            row.estudiante_id
            for row in comentarios_rows
            if row.comentario_profesor and str(row.comentario_profesor).strip()
        }
        comentarios_por_estudiante = {
            estudiante_id: (estudiante_id in commented_ids) for estudiante_id in estudiantes_ids
        }
        evaluaciones_count_rows = (
            db.session.query(Evaluacion.estudiante_id, func.count(Evaluacion.id))
            .filter(Evaluacion.estudiante_id.in_(estudiantes_ids))
            .group_by(Evaluacion.estudiante_id)
            .all()
        )
        evaluaciones_por_estudiante = {row[0]: int(row[1] or 0) for row in evaluaciones_count_rows}
    return render_template(
        "dashboard/profesor.html",
        estudiantes=estudiantes,
        student_email_display=student_email_display,
        allow_professor_view_student_email=allow_email_view,
        secciones_disponibles=secciones_disponibles,
        evaluaciones_total=evaluaciones_total,
        score_averages=score_averages,
        comentarios_por_estudiante=comentarios_por_estudiante,
        evaluaciones_por_estudiante=evaluaciones_por_estudiante,
    )


@profesor_bp.route("/perfil", methods=["GET", "POST"])
@login_required
def perfil():
    if current_user.role != "profesor":
        flash("Este panel esta disponible solo para profesores.", "warning")
        return redirect(url_for("dashboard.index"))

    profile = TeacherProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = TeacherProfile(user_id=current_user.id, school_id=DEFAULT_SCHOOL_ID, labor_status="activo")
        db.session.add(profile)
        db.session.commit()

    pending_request = (
        ProfileEditRequest.query.filter_by(user_id=current_user.id, status="pendiente")
        .order_by(ProfileEditRequest.requested_at.desc())
        .first()
    )

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        apellido = (request.form.get("apellido") or "").strip()
        if not nombre or not apellido:
            flash("Nombre y apellido son obligatorios.", "warning")
            return redirect(url_for("profesor.perfil"))

        employee_id = normalize_student_id(request.form.get("employee_id") or "") or None
        if employee_id:
            conflict = TeacherProfile.query.filter_by(employee_id=employee_id).first()
            if conflict and conflict.user_id != current_user.id:
                flash("El codigo de empleado ya existe en otro perfil.", "warning")
                return redirect(url_for("profesor.perfil"))

        labor_status = (request.form.get("labor_status") or "").strip().lower()
        if labor_status not in LABOR_STATUS:
            labor_status = profile.labor_status or "activo"

        payload = {
            "role": "profesor",
            "nombre": nombre,
            "apellido": apellido,
            "teacher_profile": {
                "employee_id": employee_id,
                "especialidad": (request.form.get("especialidad") or "").strip() or None,
                "departamento": (request.form.get("departamento") or "").strip() or None,
                "telefono": (request.form.get("telefono") or "").strip() or None,
                "school_id": (request.form.get("school_id") or profile.school_id or DEFAULT_SCHOOL_ID).strip().upper(),
                "labor_status": labor_status,
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
            flash("Tu solicitud pendiente fue actualizada.", "info")
        else:
            db.session.add(
                ProfileEditRequest(
                    user_id=current_user.id,
                    status="pendiente",
                    request_payload_json=json.dumps(payload, ensure_ascii=False),
                )
            )
            flash("Solicitud de edicion de perfil enviada al administrador.", "success")
        db.session.commit()
        return redirect(url_for("profesor.perfil"))

    history = (
        ProfileEditRequest.query.filter_by(user_id=current_user.id)
        .order_by(ProfileEditRequest.requested_at.desc())
        .limit(10)
        .all()
    )
    return render_template(
        "dashboard/profesor_perfil.html",
        profile=profile,
        pending_request=pending_request,
        request_history=history,
    )


@profesor_bp.route("/estudiantes/<int:estudiante_id>/comentarios", methods=["GET", "POST"])
@login_required
def comentarios_estudiante(estudiante_id):
    if current_user.role != "profesor":
        flash("Este panel esta disponible solo para profesores.", "warning")
        return redirect(url_for("dashboard.index"))

    estudiante = User.query.filter_by(
        id=estudiante_id,
        role="estudiante",
        profesor_id=current_user.id,
    ).first()
    if not estudiante:
        flash("No puedes comentar evaluaciones de ese estudiante.", "warning")
        return redirect(url_for("profesor.index"))

    allow_email_view = get_bool_setting("allow_professor_view_student_email", default=False)
    student_email_text = estudiante.email if allow_email_view else _mask_email(estudiante.email)

    if request.method == "POST":
        comentario = (request.form.get("comentario") or "").strip()
        evaluacion_id_raw = (request.form.get("evaluacion_id") or "").strip().lower()

        if not comentario:
            flash("Escribe un comentario antes de guardar.", "warning")
            return redirect(url_for("profesor.comentarios_estudiante", estudiante_id=estudiante.id))

        if evaluacion_id_raw == "new":
            evaluacion = Evaluacion(
                estudiante_id=estudiante.id,
                estado="comentada_profesor",
                origen="profesor",
            )
        else:
            try:
                evaluacion_id = int(evaluacion_id_raw)
            except ValueError:
                flash("Evaluacion seleccionada invalida.", "danger")
                return redirect(url_for("profesor.comentarios_estudiante", estudiante_id=estudiante.id))

            evaluacion = Evaluacion.query.filter_by(
                id=evaluacion_id,
                estudiante_id=estudiante.id,
            ).first()
            if not evaluacion:
                flash("No se encontro la evaluacion seleccionada.", "danger")
                return redirect(url_for("profesor.comentarios_estudiante", estudiante_id=estudiante.id))

        comentario_anterior = (evaluacion.comentario_profesor or "").strip() if evaluacion_id_raw != "new" else ""
        editar_existente = bool(comentario_anterior) and comentario_anterior != comentario
        approved_request = None
        if editar_existente:
            approved_request = (
                ComentarioEditRequest.query.filter_by(
                    profesor_id=current_user.id,
                    estudiante_id=estudiante.id,
                    status="aprobada",
                )
                .order_by(ComentarioEditRequest.reviewed_at.desc())
                .first()
            )
            if not approved_request:
                flash(
                    "Para editar un comentario existente necesitas una solicitud aprobada por administrador.",
                    "warning",
                )
                return redirect(url_for("profesor.comentarios_estudiante", estudiante_id=estudiante.id))

        evaluacion.comentario_profesor = comentario
        evaluacion.comentario_profesor_at = db.func.current_timestamp()
        evaluacion.profesor_comentario_id = current_user.id
        db.session.add(evaluacion)
        if approved_request:
            approved_request.status = "usada"
            approved_request.used_at = db.func.current_timestamp()
            db.session.add(approved_request)
        db.session.commit()
        if not _sync_teacher_observation_safe(
            student_id=estudiante.id,
            teacher_id=current_user.id,
            comment=comentario,
            observed_at=evaluacion.comentario_profesor_at,
        ):
            flash("Comentario guardado, pero no se pudo sincronizar teacher_observations.", "warning")
        flash("Comentario guardado correctamente.", "success")
        return redirect(url_for("profesor.comentarios_estudiante", estudiante_id=estudiante.id))

    evaluaciones = (
        Evaluacion.query.filter_by(estudiante_id=estudiante.id)
        .order_by(Evaluacion.fecha_creacion.desc())
        .all()
    )
    pending_edit_request = (
        ComentarioEditRequest.query.filter_by(
            profesor_id=current_user.id,
            estudiante_id=estudiante.id,
            status="pendiente",
        )
        .order_by(ComentarioEditRequest.requested_at.desc())
        .first()
    )
    approved_edit_request = (
        ComentarioEditRequest.query.filter_by(
            profesor_id=current_user.id,
            estudiante_id=estudiante.id,
            status="aprobada",
        )
        .order_by(ComentarioEditRequest.reviewed_at.desc())
        .first()
    )
    return render_template(
        "dashboard/profesor_comentarios.html",
        estudiante=estudiante,
        student_email_text=student_email_text,
        evaluaciones=evaluaciones,
        pending_edit_request=pending_edit_request,
        approved_edit_request=approved_edit_request,
    )


@profesor_bp.route("/estudiantes/<int:estudiante_id>/solicitar-edicion", methods=["POST"])
@login_required
def solicitar_edicion_comentarios(estudiante_id):
    if current_user.role != "profesor":
        flash("Este panel esta disponible solo para profesores.", "warning")
        return redirect(url_for("dashboard.index"))

    estudiante = User.query.filter_by(
        id=estudiante_id,
        role="estudiante",
        profesor_id=current_user.id,
    ).first()
    if not estudiante:
        flash("No puedes solicitar edicion para ese estudiante.", "warning")
        return redirect(url_for("profesor.index"))

    teacher_note = (request.form.get("teacher_note") or "").strip() or None
    pending = (
        ComentarioEditRequest.query.filter_by(
            profesor_id=current_user.id,
            estudiante_id=estudiante.id,
            status="pendiente",
        )
        .order_by(ComentarioEditRequest.requested_at.desc())
        .first()
    )
    approved = (
        ComentarioEditRequest.query.filter_by(
            profesor_id=current_user.id,
            estudiante_id=estudiante.id,
            status="aprobada",
        )
        .order_by(ComentarioEditRequest.reviewed_at.desc())
        .first()
    )
    if approved:
        flash("Ya tienes un permiso de edicion aprobado para este estudiante.", "info")
        return redirect(url_for("profesor.comentarios_estudiante", estudiante_id=estudiante.id))

    if pending:
        pending.teacher_note = teacher_note
        pending.requested_at = db.func.current_timestamp()
        db.session.add(pending)
        flash("Solicitud de edicion actualizada y reenviada.", "info")
    else:
        db.session.add(
            ComentarioEditRequest(
                profesor_id=current_user.id,
                estudiante_id=estudiante.id,
                status="pendiente",
                teacher_note=teacher_note,
            )
        )
        flash("Solicitud de edicion enviada al administrador.", "success")
    db.session.commit()
    return redirect(url_for("profesor.comentarios_estudiante", estudiante_id=estudiante.id))
