from flask import Blueprint, current_app, flash, jsonify, redirect, request, render_template, url_for
from flask_login import current_user, login_required

from app import db
from app.models.evaluacion import Evaluacion
from app.services.email_service import send_email
from app.services.email_template_service import build_evaluation_results_email
from app.services.evaluacion_service import (
    best_track_from_evaluacion,
    has_any_score,
    score_cards_from_evaluacion,
    top_score_cards,
)
from app.services.form_response_service import upsert_form_response_from_tally
from app.services.prediction_persistence_service import upsert_ai_prediction_for_evaluacion
from app.services.recommendation_engine_service import build_recommendation_for_student
from app.services.tally_service import (
    apply_sheet_scores_to_evaluacion,
    apply_submission_to_evaluacion,
    extract_submission_values,
    fetch_scores_from_submissions_sheet,
    fetch_submission_from_tally,
)

formulario_bp = Blueprint('formulario', __name__, url_prefix='/formulario')


def _evaluacion_tally_completada(estudiante_id: int):
    return (
        Evaluacion.query.filter_by(estudiante_id=estudiante_id, origen="tally")
        .order_by(Evaluacion.fecha_creacion.desc())
        .first()
    )


def _sync_ai_prediction_safe(*, student, evaluacion) -> str | None:
    try:
        recommendation = build_recommendation_for_student(student=student, evaluacion=evaluacion)
        upsert_ai_prediction_for_evaluacion(
            student_id=student.id,
            evaluacion_id=evaluacion.id,
            recommendation=recommendation,
        )
        db.session.commit()
        return None
    except Exception:
        db.session.rollback()
        return "No se pudo sincronizar ai_predictions para esta evaluacion."


def _build_external_url(endpoint: str, **kwargs) -> str:
    base = current_app.config.get("APP_BASE_URL", "").rstrip("/")
    relative = url_for(endpoint, **kwargs)
    if base:
        return f"{base}{relative}"
    return url_for(endpoint, _external=True, **kwargs)


def _send_result_email_safe(*, student, evaluacion) -> None:
    try:
        recommendation = build_recommendation_for_student(student=student, evaluacion=evaluacion)
        payload = build_evaluation_results_email(
            user_name=student.nombre,
            dashboard_url=_build_external_url("resultado.index"),
            recommendation=recommendation,
        )
        send_email(
            student.email,
            payload.subject,
            payload.html,
            payload.text,
            sender=payload.sender,
            reply_to=payload.reply_to,
        )
    except Exception:
        current_app.logger.exception(
            "No se pudo enviar correo de resultados para estudiante_id=%s",
            getattr(student, "id", None),
        )


@formulario_bp.route('/')
@login_required
def index():
    if current_user.role != "estudiante":
        flash("Solo estudiantes pueden completar el formulario de aptitudes.", "warning")
        return redirect(url_for("dashboard.index"))
    evaluacion_existente = _evaluacion_tally_completada(current_user.id)
    return render_template(
        "formulario.html",
        evaluacion_bloqueada=bool(evaluacion_existente),
        evaluacion_existente=evaluacion_existente,
        tally_form_id=current_app.config.get("TALLY_FORM_ID", "kdAYAM"),
    )

@formulario_bp.route('/resultado/<int:resultado_id>')
@login_required
def resultado(resultado_id):
    if current_user.role != "estudiante":
        flash("Solo estudiantes pueden ver resultados de evaluacion.", "warning")
        return redirect(url_for("dashboard.index"))
    evaluacion_existente = _evaluacion_tally_completada(current_user.id)
    if not evaluacion_existente:
        flash("Debes completar la evaluacion antes de ver resultados.", "warning")
        return redirect(url_for("formulario.index"))
    recommendation = build_recommendation_for_student(student=current_user, evaluacion=evaluacion_existente)
    primary = recommendation.get("primary_recommendation")
    if primary:
        recommended_track = primary["track_name"]
        recommended_description = primary["track_description"]
        recommended_style = primary["style"]
    else:
        recommended_track, recommended_description = best_track_from_evaluacion(evaluacion_existente)
        recommended_style = "inf"
    cards = score_cards_from_evaluacion(evaluacion_existente)
    top_cards = top_score_cards(evaluacion_existente, limit=3)
    return render_template(
        "resultado.html",
        user=current_user,
        evaluacion=evaluacion_existente,
        score_cards=cards,
        top_score_cards=top_cards,
        recommended_style=recommended_style if primary else (top_cards[0]["style"] if top_cards else "inf"),
        recommended_track=recommended_track,
        recommended_description=recommended_description,
        has_score_data=has_any_score(evaluacion_existente) or bool(recommendation.get("weights_used")),
        recommendation=recommendation,
    )


@formulario_bp.route("/marcar-completada", methods=["POST"])
@login_required
def marcar_completada():
    if current_user.role != "estudiante":
        return jsonify({"ok": False, "error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    raw_tally_payload = payload.get("tally_payload")
    if not isinstance(raw_tally_payload, dict):
        raw_tally_payload = None

    referencia_externa = (
        (payload.get("submission_id") or "")
        or (raw_tally_payload.get("id") if raw_tally_payload else "")
        or (raw_tally_payload.get("submissionId") if raw_tally_payload else "")
    )
    referencia_externa = str(referencia_externa or "").strip() or None
    fallback_matricula = (
        current_user.student_profile.student_id
        if getattr(current_user, "student_profile", None)
        else None
    )

    evaluacion_existente = _evaluacion_tally_completada(current_user.id)
    if evaluacion_existente:
        # Idempotencia: si ya existe evaluacion, la enriquecemos con info faltante.
        if referencia_externa and not evaluacion_existente.referencia_externa:
            evaluacion_existente.referencia_externa = referencia_externa
        if fallback_matricula and not evaluacion_existente.matricula_estudiante:
            evaluacion_existente.matricula_estudiante = fallback_matricula

        submission_payload = raw_tally_payload
        tally_fetch_error = None
        reference_for_fetch = referencia_externa or evaluacion_existente.referencia_externa
        if not submission_payload and reference_for_fetch:
            submission_payload, tally_fetch_error = fetch_submission_from_tally(
                submission_id=reference_for_fetch,
                form_id=payload.get("form_id"),
            )

        if submission_payload:
            apply_submission_to_evaluacion(
                evaluacion_existente,
                submission_payload,
                fallback_matricula=fallback_matricula,
            )

        if evaluacion_existente.average_score is None:
            sheet_scores = fetch_scores_from_submissions_sheet(
                submission_id=reference_for_fetch,
                matricula_estudiante=evaluacion_existente.matricula_estudiante or fallback_matricula,
            )
            if sheet_scores:
                apply_sheet_scores_to_evaluacion(evaluacion_existente, sheet_scores)

        upsert_form_response_from_tally(
            estudiante_id=current_user.id,
            evaluacion_id=evaluacion_existente.id,
            submission_payload=submission_payload,
            fallback_submission_id=reference_for_fetch,
            fallback_form_id=payload.get("form_id"),
            fallback_matricula=evaluacion_existente.matricula_estudiante or fallback_matricula,
        )

        db.session.add(evaluacion_existente)
        db.session.commit()
        response_payload = {
            "ok": True,
            "already_completed": True,
            "evaluacion_id": evaluacion_existente.id,
        }
        prediction_warning = _sync_ai_prediction_safe(student=current_user, evaluacion=evaluacion_existente)
        if tally_fetch_error:
            response_payload["tally_sync_warning"] = tally_fetch_error
        if prediction_warning:
            response_payload["prediction_sync_warning"] = prediction_warning
        return jsonify(response_payload)

    if referencia_externa:
        duplicate_submission = Evaluacion.query.filter_by(
            origen="tally",
            referencia_externa=referencia_externa,
        ).first()
        if duplicate_submission:
            if duplicate_submission.estudiante_id == current_user.id:
                return jsonify(
                    {
                        "ok": True,
                        "already_completed": True,
                        "evaluacion_id": duplicate_submission.id,
                    }
                )
            return jsonify(
                {
                    "ok": False,
                    "error": "duplicate_submission_id",
                    "message": "Este response ID ya fue registrado por otro estudiante.",
                }
            ), 409

    submission_payload = raw_tally_payload
    tally_fetch_error = None
    if not submission_payload and referencia_externa:
        submission_payload, tally_fetch_error = fetch_submission_from_tally(
            submission_id=referencia_externa,
            form_id=payload.get("form_id"),
        )

    detected_matricula = None
    if submission_payload:
        detected_matricula = extract_submission_values(submission_payload).get("matricula_estudiante")
    matricula_for_uniqueness = detected_matricula or fallback_matricula
    if matricula_for_uniqueness:
        duplicate_matricula = (
            Evaluacion.query.filter_by(origen="tally", matricula_estudiante=matricula_for_uniqueness)
            .filter(Evaluacion.estudiante_id != current_user.id)
            .first()
        )
        if duplicate_matricula:
            return jsonify(
                {
                    "ok": False,
                    "error": "duplicate_matricula",
                    "message": "Esta matricula ya tiene una evaluacion registrada en otra cuenta.",
                }
            ), 409

    evaluacion = Evaluacion(
        estudiante_id=current_user.id,
        estado="completada",
        origen="tally",
        referencia_externa=referencia_externa,
    )

    if submission_payload:
        apply_submission_to_evaluacion(
            evaluacion,
            submission_payload,
            fallback_matricula=fallback_matricula,
        )
    elif fallback_matricula:
        evaluacion.matricula_estudiante = fallback_matricula

    if evaluacion.average_score is None:
        sheet_scores = fetch_scores_from_submissions_sheet(
            submission_id=referencia_externa,
            matricula_estudiante=fallback_matricula,
        )
        if sheet_scores:
            apply_sheet_scores_to_evaluacion(evaluacion, sheet_scores)

    db.session.add(evaluacion)
    db.session.flush()
    upsert_form_response_from_tally(
        estudiante_id=current_user.id,
        evaluacion_id=evaluacion.id,
        submission_payload=submission_payload,
        fallback_submission_id=referencia_externa,
        fallback_form_id=payload.get("form_id"),
        fallback_matricula=evaluacion.matricula_estudiante or fallback_matricula,
    )
    db.session.commit()

    response_payload = {"ok": True, "evaluacion_id": evaluacion.id}
    prediction_warning = _sync_ai_prediction_safe(student=current_user, evaluacion=evaluacion)
    _send_result_email_safe(student=current_user, evaluacion=evaluacion)
    if tally_fetch_error:
        response_payload["tally_sync_warning"] = tally_fetch_error
    if prediction_warning:
        response_payload["prediction_sync_warning"] = prediction_warning
    return jsonify(response_payload)
