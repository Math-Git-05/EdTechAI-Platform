from __future__ import annotations

import re
from typing import Any


SCORE_FIELDS: dict[str, str] = {
    "logical_reasoning_score": "Razonamiento logico",
    "problem_resolution_score": "Resolucion de problemas",
    "detail_attention_score": "Atencion al detalle",
    "creativity_score": "Creatividad",
    "tech_ability_score": "Habilidad tecnica",
}

TRACK_BY_SCORE_FIELD: dict[str, str] = {
    "logical_reasoning_score": "Tecnico en Informatica",
    "problem_resolution_score": "Tecnico en Administracion",
    "detail_attention_score": "Tecnico en Enfermeria",
    "creativity_score": "Tecnico en Comercio",
    "tech_ability_score": "Tecnico en Informatica",
}

TRACK_DESCRIPTIONS: dict[str, str] = {
    "Tecnico en Informatica": "Perfil orientado a analisis, logica y herramientas tecnologicas.",
    "Tecnico en Administracion": "Perfil orientado a toma de decisiones, organizacion y resolucion practica.",
    "Tecnico en Enfermeria": "Perfil orientado a precision, cuidado y seguimiento detallado de procesos.",
    "Tecnico en Comercio": "Perfil orientado a creatividad, comunicacion y dinamica comercial.",
}

TRACK_STYLE_BY_FIELD: dict[str, str] = {
    "logical_reasoning_score": "inf",
    "problem_resolution_score": "adm",
    "detail_attention_score": "enf",
    "creativity_score": "com",
    "tech_ability_score": "inf",
}


def safe_score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("%", "").replace(",", ".")
        if not cleaned:
            return None
        if "/" in cleaned:
            cleaned = cleaned.split("/", 1)[0].strip()
        try:
            return float(cleaned)
        except ValueError:
            match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
            if not match:
                return None
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def compute_average(scores: dict[str, Any]) -> float | None:
    values = [safe_score(v) for v in scores.values()]
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 2)


def score_map_from_evaluacion(evaluacion) -> dict[str, float | None]:
    return {field: safe_score(getattr(evaluacion, field, None)) for field in SCORE_FIELDS}


def score_cards_from_evaluacion(evaluacion) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    scores = score_map_from_evaluacion(evaluacion)
    for field, label in SCORE_FIELDS.items():
        value = scores.get(field)
        cards.append(
            {
                "field": field,
                "label": label,
                "value": value,
                "value_text": "-" if value is None else f"{value:.2f}",
                "bar": 0 if value is None else max(0, min(100, int(round(value)))),
                "style": TRACK_STYLE_BY_FIELD.get(field, "inf"),
            }
        )
    return cards


def best_track_from_evaluacion(evaluacion) -> tuple[str, str]:
    scores = score_map_from_evaluacion(evaluacion)
    available = [(field, value) for field, value in scores.items() if value is not None]
    if not available:
        default_track = "Tecnico en Informatica"
        return default_track, TRACK_DESCRIPTIONS[default_track]

    available.sort(key=lambda item: item[1], reverse=True)
    top_field = available[0][0]
    track = TRACK_BY_SCORE_FIELD.get(top_field, "Tecnico en Informatica")
    return track, TRACK_DESCRIPTIONS.get(track, "")


def has_any_score(evaluacion) -> bool:
    scores = score_map_from_evaluacion(evaluacion)
    return any(value is not None for value in scores.values())


def top_score_cards(evaluacion, limit: int = 3) -> list[dict[str, Any]]:
    cards = score_cards_from_evaluacion(evaluacion)
    present = [card for card in cards if card["value"] is not None]
    present.sort(key=lambda card: card["value"], reverse=True)
    return present[: max(0, limit)]
