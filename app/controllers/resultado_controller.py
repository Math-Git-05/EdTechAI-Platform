from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.models.evaluacion import Evaluacion
from app.services.evaluacion_service import (
    best_track_from_evaluacion,
    has_any_score,
    score_cards_from_evaluacion,
    top_score_cards,
)
from app.services.recommendation_engine_service import build_recommendation_for_student

resultado_bp = Blueprint("resultado", __name__, url_prefix="/resultado")


@resultado_bp.route("/")
@login_required
def index():
    """Pagina de resultados/espera."""
    if current_user.role != "estudiante":
        flash("Solo estudiantes pueden acceder a resultados de evaluacion.", "warning")
        return redirect(url_for("dashboard.index"))

    evaluacion = (
        Evaluacion.query.filter_by(estudiante_id=current_user.id, origen="tally")
        .order_by(Evaluacion.fecha_creacion.desc())
        .first()
    )
    if not evaluacion:
        flash("Debes completar la evaluacion antes de ver tus resultados.", "warning")
        return redirect(url_for("formulario.index"))
    if not getattr(evaluacion, "results_released", False):
        flash("Tus resultados aun estan en revision administrativa.", "info")
        return redirect(url_for("estudiante.index"))

    recommendation = build_recommendation_for_student(student=current_user, evaluacion=evaluacion)
    primary = recommendation.get("primary_recommendation")
    recommendation_reasons = []
    component_labels = {
        "aptitudes": "Aptitudes",
        "interests": "Intereses",
        "academics": "Rendimiento academico",
    }
    if primary:
        reason_values = []
        for key in ("aptitudes", "interests", "academics"):
            value = (primary.get("components") or {}).get(key)
            if value is None:
                continue
            reason_values.append((component_labels.get(key, key), float(value)))
        reason_values.sort(key=lambda item: item[1], reverse=True)
        recommendation_reasons = [{"label": label} for label, _ in reason_values]
    if primary:
        recommended_track = primary["track_name"]
        recommended_description = primary["track_description"]
        recommended_style = primary["style"]
    else:
        recommended_track, recommended_description = best_track_from_evaluacion(evaluacion)
        recommended_style = "inf"
    cards = score_cards_from_evaluacion(evaluacion)
    top_cards = top_score_cards(evaluacion, limit=3)
    return render_template(
        "resultado.html",
        user=current_user,
        evaluacion=evaluacion,
        score_cards=cards,
        top_score_cards=top_cards,
        recommended_style=recommended_style if primary else (top_cards[0]["style"] if top_cards else "inf"),
        recommended_track=recommended_track,
        recommended_description=recommended_description,
        has_score_data=has_any_score(evaluacion) or bool(recommendation.get("weights_used")),
        recommendation=recommendation,
        recommendation_reasons=recommendation_reasons,
    )
