from __future__ import annotations

import pickle
import unicodedata
from functools import lru_cache
from pathlib import Path


MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "feedback_model_v3.pkl"


def _normalize_text(value: str) -> str:
    lowered = (value or "").strip().lower()
    no_accents = "".join(
        ch for ch in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(ch)
    )
    return " ".join(no_accents.split())


@lru_cache(maxsize=1)
def _load_feedback_payload() -> dict:
    if not MODEL_PATH.exists():
        return {}
    try:
        data = pickle.loads(MODEL_PATH.read_bytes())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def analyze_teacher_feedback(comment: str | None) -> dict:
    text = _normalize_text(comment or "")
    payload = _load_feedback_payload()
    aptitudes = payload.get("aptitudes") if isinstance(payload.get("aptitudes"), dict) else {}

    if not text or not aptitudes:
        return {
            "has_signal": False,
            "score_raw": 0.0,
            "score_0_100": 50.0,
            "aptitude_scores": {},
            "strengths": [],
            "areas_to_improve": [],
            "summary": "Aun no hay suficientes indicadores en el comentario del profesor.",
        }

    aptitude_scores: dict[str, float] = {}
    aptitude_hits: dict[str, int] = {}
    weighted_raw_total = 0.0
    weight_total = 0

    for aptitude_name, aptitude_payload in aptitudes.items():
        if not isinstance(aptitude_payload, dict):
            continue
        positives = aptitude_payload.get("positivas") if isinstance(aptitude_payload.get("positivas"), list) else []
        negatives = aptitude_payload.get("negativas") if isinstance(aptitude_payload.get("negativas"), list) else []

        pos_hits = 0
        neg_hits = 0
        for phrase in positives:
            phrase_norm = _normalize_text(str(phrase))
            if phrase_norm and phrase_norm in text:
                pos_hits += 1
        for phrase in negatives:
            phrase_norm = _normalize_text(str(phrase))
            if phrase_norm and phrase_norm in text:
                neg_hits += 1

        total_hits = pos_hits + neg_hits
        aptitude_hits[aptitude_name] = total_hits
        if total_hits == 0:
            score_raw = 0.0
        else:
            score_raw = (pos_hits - neg_hits) / total_hits
            weighted_raw_total += score_raw * total_hits
            weight_total += total_hits

        aptitude_scores[aptitude_name] = round((score_raw + 1.0) * 50.0, 2)

    if weight_total > 0:
        global_raw = weighted_raw_total / weight_total
        has_signal = True
    else:
        global_raw = 0.0
        has_signal = False
    global_score = round((global_raw + 1.0) * 50.0, 2)

    strengths = [
        item[0]
        for item in sorted(
            aptitude_scores.items(),
            key=lambda kv: (kv[1], aptitude_hits.get(kv[0], 0)),
            reverse=True,
        )
        if aptitude_hits.get(item[0], 0) > 0 and item[1] >= 55
    ][:3]
    areas = [
        item[0]
        for item in sorted(
            aptitude_scores.items(),
            key=lambda kv: (kv[1], -aptitude_hits.get(kv[0], 0)),
        )
        if aptitude_hits.get(item[0], 0) > 0 and item[1] < 50
    ][:3]

    if has_signal:
        summary = "El comentario docente aporta señales consistentes para orientar la recomendacion."
    else:
        summary = "Comentario detectado, pero sin suficientes palabras clave para una lectura fuerte."

    return {
        "has_signal": has_signal,
        "score_raw": round(global_raw, 4),
        "score_0_100": global_score,
        "aptitude_scores": aptitude_scores,
        "strengths": strengths,
        "areas_to_improve": areas,
        "summary": summary,
    }
