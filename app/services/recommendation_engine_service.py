from __future__ import annotations

import unicodedata

from app.models.calificacion import Calificacion
from app.models.evaluacion import Evaluacion
from app.models.student_interest import StudentInterest
from app.services.academic_score_service import latest_academic_score_for_student, map_subject_to_group
from app.services.evaluacion_service import safe_score
from app.services.feedback_analysis_service import analyze_teacher_feedback


TRACKS = {
    "inf": {
        "name": "Tecnico en Informatica",
        "description": "Orientado a logica, tecnologia y solucion tecnica de problemas.",
    },
    "enf": {
        "name": "Tecnico en Enfermeria",
        "description": "Orientado a cuidado, precision y seguimiento de procesos de salud.",
    },
    "adm": {
        "name": "Tecnico en Administracion",
        "description": "Orientado a gestion, toma de decisiones y organizacion.",
    },
    "com": {
        "name": "Tecnico en Comercio",
        "description": "Orientado a comunicacion, creatividad y dinamica comercial.",
    },
}

BASE_COMPONENT_WEIGHTS = {
    "aptitudes": 35.0,
    "interests": 20.0,
    "academics": 20.0,
    "feedback": 20.0,
}

APTITUDE_TRACK_WEIGHTS = {
    "inf": {
        "logical_reasoning_score": 0.45,
        "tech_ability_score": 0.35,
        "problem_resolution_score": 0.20,
    },
    "enf": {
        "detail_attention_score": 0.50,
        "problem_resolution_score": 0.25,
        "logical_reasoning_score": 0.15,
        "creativity_score": 0.10,
    },
    "adm": {
        "problem_resolution_score": 0.45,
        "logical_reasoning_score": 0.25,
        "detail_attention_score": 0.15,
        "creativity_score": 0.15,
    },
    "com": {
        "creativity_score": 0.50,
        "problem_resolution_score": 0.20,
        "logical_reasoning_score": 0.15,
        "tech_ability_score": 0.15,
    },
}

INTEREST_TRACK_WEIGHTS = {
    "inf": {"technology": 0.70, "design": 0.30},
    "enf": {"health": 0.80, "business": 0.10, "design": 0.10},
    "adm": {"business": 0.70, "technology": 0.15, "design": 0.15},
    "com": {"business": 0.45, "design": 0.45, "technology": 0.10},
}

ACADEMIC_TRACK_WEIGHTS = {
    "inf": {"math": 0.50, "science": 0.35, "language": 0.15},
    "enf": {"science": 0.50, "language": 0.35, "math": 0.15},
    "adm": {"language": 0.45, "math": 0.35, "science": 0.20},
    "com": {"language": 0.55, "science": 0.25, "math": 0.20},
}

FEEDBACK_TRACK_WEIGHTS = {
    "inf": {
        "Pensamiento Analitico": 0.35,
        "Habilidad Tecnica / Practica": 0.30,
        "Iniciativa y Autonomia": 0.20,
        "Atencion al Detalle": 0.15,
    },
    "enf": {
        "Responsabilidad": 0.30,
        "Atencion al Detalle": 0.30,
        "Actitud y Valores": 0.20,
        "Colaboracion": 0.20,
    },
    "adm": {
        "Liderazgo": 0.30,
        "Comunicacion": 0.25,
        "Responsabilidad": 0.25,
        "Iniciativa y Autonomia": 0.20,
    },
    "com": {
        "Comunicacion": 0.35,
        "Creatividad e Innovacion": 0.30,
        "Participacion": 0.20,
        "Liderazgo": 0.15,
    },
}


def _clamp(value) -> float | None:
    parsed = safe_score(value)
    if parsed is None:
        return None
    return max(0.0, min(100.0, parsed))


def _weighted_mix(source: dict[str, float | None], weights: dict[str, float]) -> float | None:
    weighted_sum = 0.0
    total_weight = 0.0
    for key, weight in weights.items():
        value = source.get(key)
        if value is None:
            continue
        weighted_sum += float(value) * float(weight)
        total_weight += float(weight)
    if total_weight <= 0:
        return None
    return round(weighted_sum / total_weight, 2)


def _classify_affinity(score: float) -> str:
    if score >= 75:
        return "Alta afinidad"
    if score >= 55:
        return "Compatible"
    if score >= 35:
        return "Con desarrollo"
    return "No recomendado"


def _normalize_component_weights(available_components: list[str]) -> dict[str, float]:
    base_total = sum(BASE_COMPONENT_WEIGHTS[name] for name in available_components)
    if base_total <= 0:
        return {}
    return {
        name: (BASE_COMPONENT_WEIGHTS[name] / base_total) * 100.0 for name in available_components
    }


def _normalized_aptitude_scores(evaluacion: Evaluacion) -> dict[str, float | None]:
    return {
        "logical_reasoning_score": _clamp(getattr(evaluacion, "logical_reasoning_score", None)),
        "problem_resolution_score": _clamp(getattr(evaluacion, "problem_resolution_score", None)),
        "detail_attention_score": _clamp(getattr(evaluacion, "detail_attention_score", None)),
        "creativity_score": _clamp(getattr(evaluacion, "creativity_score", None)),
        "tech_ability_score": _clamp(getattr(evaluacion, "tech_ability_score", None)),
    }


def _interest_values_for_student(student_profile) -> dict[str, float | None]:
    if not student_profile:
        return {"technology": None, "design": None, "business": None, "health": None}

    external = StudentInterest.query.filter_by(student_id=student_profile.student_id).first()
    if external:
        return {
            "technology": _clamp(external.interest_technology),
            "design": _clamp(external.interest_design),
            "business": _clamp(external.interest_business),
            "health": _clamp(external.interest_health),
        }

    return {
        "technology": _clamp(getattr(student_profile, "interest_technology", None)),
        "design": _clamp(getattr(student_profile, "interest_design", None)),
        "business": _clamp(getattr(student_profile, "interest_business", None)),
        "health": _clamp(getattr(student_profile, "interest_health", None)),
    }


def _academic_values_for_student(estudiante_id: int) -> dict[str, float | None]:
    row = latest_academic_score_for_student(estudiante_id)
    if not row:
        math_values = []
        language_values = []
        science_values = []
        for item in Calificacion.query.filter_by(estudiante_id=estudiante_id).all():
            group = map_subject_to_group(item.asignatura)
            value = _clamp(item.valor)
            if value is None or not group:
                continue
            if group == "Matematicas":
                math_values.append(value)
            elif group == "Lenguas":
                language_values.append(value)
            elif group == "Ciencias":
                science_values.append(value)

        def _simple_avg(values: list[float]) -> float | None:
            if not values:
                return None
            return round(sum(values) / len(values), 2)

        return {
            "math": _simple_avg(math_values),
            "language": _simple_avg(language_values),
            "science": _simple_avg(science_values),
        }
    return {
        "math": _clamp(row.math_average),
        "language": _clamp(row.language_average),
        "science": _clamp(row.science_average),
    }


def _latest_teacher_comment(estudiante_id: int, evaluacion: Evaluacion) -> str | None:
    own_comment = (getattr(evaluacion, "comentario_profesor", None) or "").strip()
    if own_comment:
        return own_comment

    rows = (
        Evaluacion.query.filter_by(estudiante_id=estudiante_id)
        .filter(Evaluacion.comentario_profesor.isnot(None))
        .order_by(Evaluacion.comentario_profesor_at.desc(), Evaluacion.fecha_creacion.desc())
        .limit(20)
        .all()
    )
    for row in rows:
        text = (row.comentario_profesor or "").strip()
        if text:
            return text
    return None


def _feedback_track_scores(comment: str | None) -> tuple[dict[str, float | None], dict]:
    analysis = analyze_teacher_feedback(comment)
    if not (comment or "").strip():
        return {track_key: None for track_key in TRACKS}, analysis

    aptitude_scores = {}
    for key, value in analysis.get("aptitude_scores", {}).items():
        normalized_key = "".join(
            ch for ch in unicodedata.normalize("NFKD", str(key)) if not unicodedata.combining(ch)
        )
        aptitude_scores[normalized_key] = _clamp(value)
    scores: dict[str, float | None] = {}
    for track_key, weights in FEEDBACK_TRACK_WEIGHTS.items():
        scores[track_key] = _weighted_mix(aptitude_scores, weights)
        if scores[track_key] is None:
            fallback = analysis.get("score_0_100")
            scores[track_key] = _clamp(fallback)
    return scores, analysis


def build_recommendation_for_student(*, student, evaluacion: Evaluacion) -> dict:
    aptitude_values = _normalized_aptitude_scores(evaluacion)
    aptitude_component = {
        key: _weighted_mix(aptitude_values, weights)
        for key, weights in APTITUDE_TRACK_WEIGHTS.items()
    }

    interest_values = _interest_values_for_student(getattr(student, "student_profile", None))
    interest_component = {
        key: _weighted_mix(interest_values, weights)
        for key, weights in INTEREST_TRACK_WEIGHTS.items()
    }

    academic_values = _academic_values_for_student(student.id)
    academic_component = {
        key: _weighted_mix(academic_values, weights)
        for key, weights in ACADEMIC_TRACK_WEIGHTS.items()
    }

    teacher_comment = _latest_teacher_comment(student.id, evaluacion)
    feedback_component, feedback_analysis = _feedback_track_scores(teacher_comment)

    components_by_name = {
        "aptitudes": aptitude_component,
        "interests": interest_component,
        "academics": academic_component,
        "feedback": feedback_component,
    }

    available = []
    for name, scores in components_by_name.items():
        if any(scores.get(track) is not None for track in TRACKS):
            available.append(name)
    weights = _normalize_component_weights(available)

    ranking = []
    for track_key, track_meta in TRACKS.items():
        contribution_breakdown = {}
        total = 0.0
        for component_name, component_scores in components_by_name.items():
            value = component_scores.get(track_key)
            contribution_breakdown[component_name] = value
            if value is None or component_name not in weights:
                continue
            total += value * (weights[component_name] / 100.0)

        total_score = round(total, 2) if weights else 0.0
        ranking.append(
            {
                "track_key": track_key,
                "track_name": track_meta["name"],
                "track_description": track_meta["description"],
                "style": track_key,
                "score": total_score,
                "affinity_level": _classify_affinity(total_score),
                "components": contribution_breakdown,
            }
        )

    ranking.sort(key=lambda row: row["score"], reverse=True)
    top_recommendations = ranking[:3]
    primary = top_recommendations[0] if top_recommendations else None

    if primary:
        if primary["score"] >= 75:
            guidance = "Tu perfil muestra una afinidad alta para esta ruta tecnica."
        elif primary["score"] >= 55:
            guidance = "Esta ruta es compatible contigo y puede fortalecerse con practica guiada."
        elif primary["score"] >= 35:
            guidance = "Esta ruta es posible con acompanamiento y plan de mejora."
        else:
            guidance = "Esta ruta no es la mas fuerte hoy, pero puedes desarrollarla progresivamente."
    else:
        guidance = "Aun no hay suficientes datos para recomendar una ruta con confianza."

    return {
        "weights_used": weights,
        "inputs": {
            "aptitudes": aptitude_values,
            "interests": interest_values,
            "academics": academic_values,
            "teacher_comment": teacher_comment,
        },
        "feedback_analysis": feedback_analysis,
        "ranking": ranking,
        "top_recommendations": top_recommendations,
        "primary_recommendation": primary,
        "guidance": guidance,
    }
