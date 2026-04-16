import json
import csv
from datetime import datetime
from io import StringIO

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db
from app.models.calificacion import Calificacion
from app.models.comentario_edit_request import ComentarioEditRequest
from app.models.evaluacion import Evaluacion
from app.models.grade_change_request import GradeChangeRequest
from app.models.profile_edit_request import ProfileEditRequest
from app.models.teacher_profile import TeacherProfile
from app.models.user import User
from app.services.profile_service import DEFAULT_SCHOOL_ID, LABOR_STATUS, normalize_student_id
from app.services.settings_service import get_bool_setting
from app.services.audit_log_service import log_audit_event
from app.services.teacher_observation_service import upsert_teacher_observation

profesor_bp = Blueprint('profesor', __name__)

PERIODOS_CALIFICACION = [
    ("periodo_1", "1er período"),
    ("periodo_2", "2do período"),
    ("periodo_3", "3er período"),
    ("periodo_4", "4to período"),
]
ASIGNATURAS_CALIFICACION = [
    "Español",
    "Lenguas Extranjeras",
    "Matemáticas",
    "Ciencias Sociales",
    "Ciencias Naturales",
    "Informática",
    "Enfermería",
    "Comercio",
    "Mercadeo",
    "Administración Pública Tributaria",
]


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


def _mask_identifier(value: str | None) -> str:
    token = (value or "").strip()
    if not token:
        return "-"
    if len(token) <= 4:
        return "***"
    return f"{token[:2]}***{token[-2:]}"


def _csv_response(*, filename: str, fieldnames: list[str], rows: list[dict[str, object]]) -> Response:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    response = Response(buffer.getvalue(), mimetype="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    return response


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


def _submit_grade_request_for_student(*, estudiante: User) -> tuple[bool, str]:
    asignatura = (request.form.get("asignatura") or "").strip()
    periodo = (request.form.get("periodo") or "").strip() or None
    anio_raw = (request.form.get("anio") or "").strip()
    valor_raw = (request.form.get("valor") or "").strip()
    observaciones = (request.form.get("observaciones") or "").strip() or None

    if asignatura not in set(ASIGNATURAS_CALIFICACION):
        return False, "Selecciona una asignatura válida."
    if periodo and periodo not in {item[0] for item in PERIODOS_CALIFICACION}:
        return False, "Selecciona un período válido."

    try:
        valor = float(valor_raw)
    except (TypeError, ValueError):
        return False, "La calificación debe ser numérica."
    if valor < 0 or valor > 100:
        return False, "La calificación debe estar entre 0 y 100."

    anio = None
    if anio_raw:
        try:
            anio = int(anio_raw)
        except ValueError:
            return False, "El año de la calificación es inválido."
        if anio < 1990 or anio > 2100:
            return False, "El año de la calificación está fuera de rango."

    existing_grade = (
        Calificacion.query.filter_by(
            estudiante_id=estudiante.id,
            asignatura=asignatura,
            periodo=periodo,
            anio=anio,
        )
        .order_by(Calificacion.fecha_actualizacion.desc())
        .first()
    )

    pending = (
        GradeChangeRequest.query.filter_by(
            profesor_id=current_user.id,
            estudiante_id=estudiante.id,
            asignatura=asignatura,
            periodo=periodo,
            anio=anio,
            status="pendiente",
        )
        .order_by(GradeChangeRequest.requested_at.desc())
        .first()
    )
    if pending:
        pending.valor = valor
        pending.observaciones = observaciones
        pending.calificacion_id = existing_grade.id if existing_grade else None
        pending.requested_at = db.func.current_timestamp()
        db.session.add(pending)
        db.session.commit()
        request_id = pending.id
        message = "Solicitud de calificación pendiente actualizada."
    else:
        new_request = GradeChangeRequest(
            profesor_id=current_user.id,
            estudiante_id=estudiante.id,
            calificacion_id=existing_grade.id if existing_grade else None,
            asignatura=asignatura,
            periodo=periodo,
            anio=anio,
            valor=valor,
            observaciones=observaciones,
            status="pendiente",
        )
        db.session.add(new_request)
        db.session.flush()
        request_id = new_request.id
        db.session.commit()
        message = "Solicitud de calificación enviada al administrador."

    log_audit_event(
        action="profesor.grade_request.created",
        actor_user_id=current_user.id,
        target_type="grade_request",
        target_id=request_id,
        metadata={
            "estudiante_id": estudiante.id,
            "asignatura": asignatura,
            "periodo": periodo,
            "anio": anio,
            "valor": valor,
        },
    )
    db.session.commit()
    return True, message

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
    allow_rne_view = get_bool_setting("allow_professor_view_student_rne", default=True)
    allow_professor_export_csv = get_bool_setting("allow_professor_export_csv", default=False)
    pending_grade_requests = (
        GradeChangeRequest.query.filter_by(profesor_id=current_user.id, status="pendiente")
        .order_by(GradeChangeRequest.requested_at.desc())
        .limit(80)
        .all()
    )
    student_email_display: dict[int, str] = {}
    student_rne_display: dict[int, str] = {}
    for est in estudiantes:
        student_email_display[est.id] = est.email if allow_email_view else _mask_email(est.email)
        student_rne = est.student_profile.student_id if est.student_profile else ""
        student_rne_display[est.id] = student_rne if (allow_rne_view and student_rne) else _mask_identifier(student_rne)

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
        student_rne_display=student_rne_display,
        allow_professor_view_student_email=allow_email_view,
        allow_professor_view_student_rne=allow_rne_view,
        secciones_disponibles=secciones_disponibles,
        evaluaciones_total=evaluaciones_total,
        score_averages=score_averages,
        comentarios_por_estudiante=comentarios_por_estudiante,
        evaluaciones_por_estudiante=evaluaciones_por_estudiante,
        allow_professor_export_csv=allow_professor_export_csv,
        pending_grade_requests=pending_grade_requests,
        periodos_calificacion=PERIODOS_CALIFICACION,
        asignaturas_calificacion=ASIGNATURAS_CALIFICACION,
        year_options=[datetime.utcnow().year - 1, datetime.utcnow().year, datetime.utcnow().year + 1],
    )


@profesor_bp.route("/export/csv/<string:dataset>")
@login_required
def export_csv(dataset: str):
    if current_user.role != "profesor":
        flash("Solo profesores pueden exportar esta vista.", "warning")
        return redirect(url_for("dashboard.index"))

    if not get_bool_setting("allow_professor_export_csv", default=False):
        flash("La exportacion CSV para profesor esta bloqueada por administracion.", "warning")
        return redirect(url_for("profesor.index"))

    dataset = (dataset or "").strip().lower()
    now_tag = db.session.query(db.func.current_timestamp()).scalar()
    now_tag_text = now_tag.strftime("%Y%m%d_%H%M") if now_tag else "export"
    students = User.query.filter_by(role="estudiante", profesor_id=current_user.id).all()
    student_ids = [item.id for item in students]

    rows: list[dict[str, object]] = []
    headers: list[str] = []
    filename = f"profesor_{dataset}_{now_tag_text}.csv"

    if dataset == "estudiantes":
        headers = ["id", "nombre", "apellido", "email", "seccion", "rne", "activo"]
        for student in students:
            rows.append(
                {
                    "id": student.id,
                    "nombre": student.nombre,
                    "apellido": student.apellido,
                    "email": student.email,
                    "seccion": student.seccion or "",
                    "rne": student.student_profile.student_id if student.student_profile else "",
                    "activo": int(bool(student.activo)),
                }
            )
    elif dataset == "calificaciones":
        headers = ["id", "estudiante", "asignatura", "periodo", "anio", "valor", "observaciones"]
        if student_ids:
            grades = (
                Calificacion.query.filter(Calificacion.estudiante_id.in_(student_ids))
                .order_by(Calificacion.fecha_actualizacion.desc())
                .all()
            )
        else:
            grades = []
        students_map = {student.id: student for student in students}
        for grade in grades:
            student = students_map.get(grade.estudiante_id)
            rows.append(
                {
                    "id": grade.id,
                    "estudiante": f"{student.nombre} {student.apellido}" if student else grade.estudiante_id,
                    "asignatura": grade.asignatura,
                    "periodo": grade.periodo or "",
                    "anio": grade.anio or "",
                    "valor": grade.valor,
                    "observaciones": grade.observaciones or "",
                }
            )
    elif dataset == "evaluaciones":
        headers = ["evaluacion_id", "estudiante", "promedio", "origen", "fecha"]
        if student_ids:
            evals = (
                Evaluacion.query.filter(Evaluacion.estudiante_id.in_(student_ids))
                .order_by(Evaluacion.fecha_creacion.desc())
                .all()
            )
        else:
            evals = []
        students_map = {student.id: student for student in students}
        for evaluacion in evals:
            student = students_map.get(evaluacion.estudiante_id)
            rows.append(
                {
                    "evaluacion_id": evaluacion.id,
                    "estudiante": f"{student.nombre} {student.apellido}" if student else evaluacion.estudiante_id,
                    "promedio": evaluacion.average_score if evaluacion.average_score is not None else "",
                    "origen": evaluacion.origen,
                    "fecha": evaluacion.fecha_creacion.isoformat() if evaluacion.fecha_creacion else "",
                }
            )
    else:
        flash("Dataset CSV no disponible para profesor.", "warning")
        return redirect(url_for("profesor.index"))

    log_audit_event(
        action="profesor.export.csv",
        actor_user_id=current_user.id,
        target_type="csv",
        target_id=dataset,
        metadata={"rows": len(rows)},
    )
    db.session.commit()
    return _csv_response(filename=filename, fieldnames=headers, rows=rows)


@profesor_bp.route("/calificaciones/solicitar", methods=["POST"])
@login_required
def solicitar_calificacion_panel():
    if current_user.role != "profesor":
        flash("Este panel está disponible solo para profesores.", "warning")
        return redirect(url_for("dashboard.index"))

    estudiante_id_raw = (request.form.get("estudiante_id") or "").strip()
    try:
        estudiante_id = int(estudiante_id_raw)
    except (TypeError, ValueError):
        flash("Selecciona un estudiante válido para la solicitud.", "warning")
        return redirect(url_for("profesor.index"))

    estudiante = User.query.filter_by(
        id=estudiante_id,
        role="estudiante",
        profesor_id=current_user.id,
    ).first()
    if not estudiante:
        flash("No puedes solicitar calificaciones para ese estudiante.", "warning")
        return redirect(url_for("profesor.index"))

    ok, message = _submit_grade_request_for_student(estudiante=estudiante)
    flash(message, "success" if ok else "warning")
    return redirect(url_for("profesor.index"))


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
    allow_rne_view = get_bool_setting("allow_professor_view_student_rne", default=True)
    student_email_text = estudiante.email if allow_email_view else _mask_email(estudiante.email)
    student_rne_raw = estudiante.student_profile.student_id if estudiante.student_profile else ""
    student_rne_text = student_rne_raw if (allow_rne_view and student_rne_raw) else _mask_identifier(student_rne_raw)

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
        log_audit_event(
            action="profesor.comment.saved",
            actor_user_id=current_user.id,
            target_type="evaluacion",
            target_id=evaluacion.id,
            metadata={"estudiante_id": estudiante.id, "edit_mode": bool(editar_existente)},
        )
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
    grade_request_history = (
        GradeChangeRequest.query.filter_by(
            profesor_id=current_user.id,
            estudiante_id=estudiante.id,
        )
        .order_by(GradeChangeRequest.requested_at.desc())
        .limit(20)
        .all()
    )
    existing_grades = (
        Calificacion.query.filter_by(estudiante_id=estudiante.id)
        .order_by(Calificacion.fecha_actualizacion.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "dashboard/profesor_comentarios.html",
        estudiante=estudiante,
        student_email_text=student_email_text,
        student_rne_text=student_rne_text,
        allow_professor_view_student_rne=allow_rne_view,
        evaluaciones=evaluaciones,
        pending_edit_request=pending_edit_request,
        approved_edit_request=approved_edit_request,
        grade_request_history=grade_request_history,
        existing_grades=existing_grades,
        year_now=datetime.utcnow().year,
        periodos_calificacion=PERIODOS_CALIFICACION,
        asignaturas_calificacion=ASIGNATURAS_CALIFICACION,
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
    log_audit_event(
        action="profesor.comment_edit_request.sent",
        actor_user_id=current_user.id,
        target_type="estudiante",
        target_id=estudiante.id,
        metadata={"has_note": bool(teacher_note)},
    )
    db.session.commit()
    return redirect(url_for("profesor.comentarios_estudiante", estudiante_id=estudiante.id))


@profesor_bp.route("/estudiantes/<int:estudiante_id>/solicitar-calificacion", methods=["POST"])
@login_required
def solicitar_calificacion(estudiante_id):
    if current_user.role != "profesor":
        flash("Este panel está disponible solo para profesores.", "warning")
        return redirect(url_for("dashboard.index"))

    estudiante = User.query.filter_by(
        id=estudiante_id,
        role="estudiante",
        profesor_id=current_user.id,
    ).first()
    if not estudiante:
        flash("No puedes solicitar calificaciones para ese estudiante.", "warning")
        return redirect(url_for("profesor.index"))
    ok, message = _submit_grade_request_for_student(estudiante=estudiante)
    flash(message, "success" if ok else "warning")
    return redirect(url_for("profesor.comentarios_estudiante", estudiante_id=estudiante.id))
