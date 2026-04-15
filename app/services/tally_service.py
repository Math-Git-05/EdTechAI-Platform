from __future__ import annotations

import json
import re
import unicodedata
import csv
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app

from app.services.evaluacion_service import compute_average, safe_score
from app.services.profile_service import normalize_student_id


_SCORE_MATCHERS: dict[str, tuple[str, ...]] = {
    "logical_reasoning_score": (
        "logical reasoning score",
        "logical reasoning",
        "razonamiento logico",
    ),
    "problem_resolution_score": (
        "problem resolution score",
        "problem solution score",
        "problem solving score",
        "problem-solving score",
        "problem resolution",
        "resolucion de problemas",
    ),
    "detail_attention_score": (
        "detail attention score",
        "attention to detail score",
        "attention detail score",
        "detail attention",
        "atencion al detalle",
    ),
    "creativity_score": (
        "creativity score",
        "creativity",
        "creatividad",
    ),
    "tech_ability_score": (
        "tech ability score",
        "teach ability score",
        "teachability score",
        "touch ability score",
        "touchability score",
        "technology ability score",
        "tech ability",
        "habilidad tecnica",
        "aptitud tecnica",
    ),
}


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _coerce_answer_value(answer: Any) -> Any:
    if isinstance(answer, dict):
        if "value" in answer:
            return answer.get("value")
        if "raw" in answer:
            return answer.get("raw")
    return answer


def _match_score_field(title_or_key: Any) -> str | None:
    normalized = _normalize_text(title_or_key)
    if not normalized:
        return None
    for field, aliases in _SCORE_MATCHERS.items():
        for alias in aliases:
            if alias in normalized:
                return field
    return None


def _is_matricula_field(title_or_key: Any) -> bool:
    normalized = _normalize_text(title_or_key)
    if not normalized:
        return False
    return "matricula" in normalized or normalized == "rne" or "student id" in normalized


def parse_tally_datetime(value: Any):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def extract_submission_values(payload: dict[str, Any]) -> dict[str, Any]:
    scores: dict[str, float | None] = {
        "logical_reasoning_score": None,
        "problem_resolution_score": None,
        "detail_attention_score": None,
        "creativity_score": None,
        "tech_ability_score": None,
    }

    matricula_estudiante: str | None = None
    fields = payload.get("fields")
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            title = field.get("title") or field.get("label") or field.get("id")
            answer = _coerce_answer_value(field.get("answer"))

            score_key = _match_score_field(title)
            if score_key:
                parsed = safe_score(answer)
                if parsed is not None:
                    scores[score_key] = parsed

            if _is_matricula_field(title):
                normalized_id = normalize_student_id(str(answer or ""))
                if normalized_id:
                    matricula_estudiante = normalized_id

    answers = payload.get("answers")
    if isinstance(answers, list):
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            title = answer.get("question") or answer.get("label") or answer.get("key")
            value = _coerce_answer_value(answer.get("answer") or answer.get("value"))
            score_key = _match_score_field(title)
            if score_key and scores.get(score_key) is None:
                parsed = safe_score(value)
                if parsed is not None:
                    scores[score_key] = parsed
            if matricula_estudiante is None and _is_matricula_field(title):
                normalized_id = normalize_student_id(str(value or ""))
                if normalized_id:
                    matricula_estudiante = normalized_id

    calculated = payload.get("calculated")
    if isinstance(calculated, dict):
        for key, value in calculated.items():
            score_key = _match_score_field(key)
            if score_key and scores.get(score_key) is None:
                parsed = safe_score(value)
                if parsed is not None:
                    scores[score_key] = parsed

    # Fallback: buscar campos de score directamente en la raiz.
    for key, value in payload.items():
        score_key = _match_score_field(key)
        if score_key and scores.get(score_key) is None:
            parsed = safe_score(value)
            if parsed is not None:
                scores[score_key] = parsed

        if matricula_estudiante is None and _is_matricula_field(key):
            normalized_id = normalize_student_id(str(value or ""))
            if normalized_id:
                matricula_estudiante = normalized_id

    average_score = compute_average(scores)
    return {
        **scores,
        "average_score": average_score,
        "matricula_estudiante": matricula_estudiante,
    }


def extract_submission_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "referencia_externa": str(payload.get("id") or payload.get("submissionId") or "").strip() or None,
        "respondent_id": str(payload.get("respondentId") or "").strip() or None,
        "form_id": str(payload.get("formId") or "").strip() or None,
        "submitted_at": parse_tally_datetime(payload.get("createdAt") or payload.get("submittedAt")),
    }


def apply_submission_to_evaluacion(evaluacion, payload: dict[str, Any], fallback_matricula: str | None = None):
    metadata = extract_submission_metadata(payload)
    scores = extract_submission_values(payload)

    if metadata["referencia_externa"]:
        evaluacion.referencia_externa = metadata["referencia_externa"]
    if metadata["respondent_id"]:
        evaluacion.respondent_id = metadata["respondent_id"]
    if metadata["form_id"]:
        evaluacion.form_id = metadata["form_id"]
    if metadata["submitted_at"]:
        evaluacion.submitted_at = metadata["submitted_at"]

    for field in (
        "logical_reasoning_score",
        "problem_resolution_score",
        "detail_attention_score",
        "creativity_score",
        "tech_ability_score",
        "average_score",
    ):
        setattr(evaluacion, field, scores.get(field))

    matricula = scores.get("matricula_estudiante") or normalize_student_id(fallback_matricula or "")
    evaluacion.matricula_estudiante = matricula or None
    evaluacion.datos_json = json.dumps(payload, ensure_ascii=False)


def _sheet_to_csv_url(sheet_url: str) -> str | None:
    raw = (sheet_url or "").strip()
    if not raw:
        return None
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", raw)
    if not match:
        return None
    sheet_id = match.group(1)
    gid_match = re.search(r"[?&]gid=(\d+)", raw)
    if gid_match:
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid_match.group(1)}"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"


def _row_get_value_by_aliases(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    normalized_row = {_normalize_text(k): v for k, v in row.items()}
    for alias in aliases:
        key = _normalize_text(alias)
        if key in normalized_row:
            return normalized_row[key]
    return None


def fetch_scores_from_submissions_sheet(
    submission_id: str | None = None,
    matricula_estudiante: str | None = None,
) -> dict[str, Any] | None:
    sheet_url = current_app.config.get("TALLY_SUBMISSIONS_SHEET_URL") or ""
    csv_url = _sheet_to_csv_url(sheet_url)
    if not csv_url:
        return None

    submission_id = str(submission_id or "").strip()
    matricula_estudiante = normalize_student_id(matricula_estudiante or "")

    try:
        request = Request(csv_url, headers={"User-Agent": "edtech-platform/1.0", "Accept": "text/csv"})
        with urlopen(request, timeout=14) as response:
            raw_csv = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    reader = csv.DictReader(raw_csv.splitlines())
    best_match: dict[str, Any] | None = None
    for row in reader:
        if not isinstance(row, dict):
            continue

        row_submission_id = str(
            _row_get_value_by_aliases(
                row,
                (
                    "submission id",
                    "submission_id",
                    "submissionid",
                    "response id",
                    "response_id",
                    "responseid",
                    "id",
                ),
            )
            or ""
        ).strip()
        row_matricula = normalize_student_id(
            _row_get_value_by_aliases(
                row,
                (
                    "matricula estudiante",
                    "matricula",
                    "student id",
                    "student_id",
                    "rne",
                ),
            )
            or ""
        )

        matches_submission = bool(submission_id and row_submission_id and row_submission_id == submission_id)
        matches_matricula = bool(matricula_estudiante and row_matricula and row_matricula == matricula_estudiante)
        if not matches_submission and not matches_matricula:
            continue

        extracted = {
            "logical_reasoning_score": safe_score(
                _row_get_value_by_aliases(row, ("logical reasoning score", "logical_reasoning_score"))
            ),
            "problem_resolution_score": safe_score(
                _row_get_value_by_aliases(row, ("problem resolution score", "problem_resolution_score"))
            ),
            "detail_attention_score": safe_score(
                _row_get_value_by_aliases(row, ("detail attention score", "detail_attention_score"))
            ),
            "creativity_score": safe_score(
                _row_get_value_by_aliases(row, ("creativity score", "creativity_score"))
            ),
            "tech_ability_score": safe_score(
                _row_get_value_by_aliases(row, ("tech ability score", "tech_ability_score"))
            ),
            "matricula_estudiante": row_matricula or None,
            "referencia_externa": row_submission_id or None,
        }
        extracted["average_score"] = compute_average(
            {
                "logical_reasoning_score": extracted["logical_reasoning_score"],
                "problem_resolution_score": extracted["problem_resolution_score"],
                "detail_attention_score": extracted["detail_attention_score"],
                "creativity_score": extracted["creativity_score"],
                "tech_ability_score": extracted["tech_ability_score"],
            }
        )

        # Fallback extra: deducir columnas de score por nombre de cabecera.
        for raw_key, raw_value in row.items():
            score_key = _match_score_field(raw_key)
            if score_key and extracted.get(score_key) is None:
                parsed = safe_score(raw_value)
                if parsed is not None:
                    extracted[score_key] = parsed
        extracted["average_score"] = compute_average(
            {
                "logical_reasoning_score": extracted["logical_reasoning_score"],
                "problem_resolution_score": extracted["problem_resolution_score"],
                "detail_attention_score": extracted["detail_attention_score"],
                "creativity_score": extracted["creativity_score"],
                "tech_ability_score": extracted["tech_ability_score"],
            }
        )

        # Si vino submission_id exacto, usarlo directo. Si no, guardar el ultimo match por matricula.
        if matches_submission:
            return extracted
        best_match = extracted

    return best_match


def apply_sheet_scores_to_evaluacion(evaluacion, scores_payload: dict[str, Any]) -> None:
    if not scores_payload:
        return
    for field in (
        "logical_reasoning_score",
        "problem_resolution_score",
        "detail_attention_score",
        "creativity_score",
        "tech_ability_score",
        "average_score",
    ):
        value = scores_payload.get(field)
        if value is not None:
            setattr(evaluacion, field, value)

    referencia = (scores_payload.get("referencia_externa") or "").strip()
    if referencia and not evaluacion.referencia_externa:
        evaluacion.referencia_externa = referencia
    matricula = normalize_student_id(scores_payload.get("matricula_estudiante") or "")
    if matricula and not evaluacion.matricula_estudiante:
        evaluacion.matricula_estudiante = matricula


def _build_tally_request(url: str, api_key: str) -> Request:
    return Request(
        url=url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "edtech-platform/1.0",
        },
    )


def _extract_submission_object(api_payload: Any, requested_submission_id: str | None = None):
    if isinstance(api_payload, dict):
        if isinstance(api_payload.get("submission"), dict):
            return api_payload["submission"]
        if isinstance(api_payload.get("data"), dict):
            data_obj = api_payload["data"]
            if isinstance(data_obj.get("submission"), dict):
                return data_obj["submission"]
            if "id" in data_obj:
                return data_obj
        if isinstance(api_payload.get("data"), list):
            for item in api_payload["data"]:
                if not isinstance(item, dict):
                    continue
                if requested_submission_id and str(item.get("id") or "").strip() == requested_submission_id:
                    return item
            if api_payload["data"]:
                first = api_payload["data"][0]
                if isinstance(first, dict):
                    return first
        if "id" in api_payload:
            return api_payload
    return None


def fetch_submission_from_tally(
    submission_id: str | None,
    form_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    submission_id = str(submission_id or "").strip()
    if not submission_id:
        return None, "missing_submission_id"

    api_key = (current_app.config.get("TALLY_API_KEY") or "").strip()
    if not api_key:
        return None, "missing_api_key"

    form_id = str(form_id or current_app.config.get("TALLY_FORM_ID") or "").strip()
    candidates: list[str] = []
    if form_id:
        candidates.append(f"https://api.tally.so/forms/{form_id}/submissions/{submission_id}")
        candidates.append(f"https://api.tally.so/forms/{form_id}/submissions?limit=10")
    candidates.append(f"https://api.tally.so/submissions/{submission_id}")

    last_error: str | None = None
    for url in candidates:
        try:
            req = _build_tally_request(url, api_key)
            with urlopen(req, timeout=12) as response:
                raw = response.read().decode("utf-8")
                parsed = json.loads(raw)
                submission = _extract_submission_object(parsed, submission_id)
                if submission:
                    return submission, None
                last_error = "submission_not_found_in_response"
        except HTTPError as exc:
            last_error = f"http_{exc.code}"
        except URLError:
            last_error = "network_error"
        except TimeoutError:
            last_error = "timeout_error"
        except json.JSONDecodeError:
            last_error = "invalid_json"
        except Exception:
            last_error = "unexpected_error"

    return None, last_error or "unknown_error"
