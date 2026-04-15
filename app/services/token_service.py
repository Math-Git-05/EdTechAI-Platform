from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask import current_app


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=current_app.config["SECRET_KEY"],
        salt=current_app.config.get("SECURITY_PASSWORD_SALT", "edtech-email-token-salt"),
    )


def generate_token(email: str, purpose: str) -> str:
    payload = {"email": email, "purpose": purpose}
    return _serializer().dumps(payload)


def verify_token(token: str, purpose: str, max_age_seconds: int):
    try:
        payload = _serializer().loads(token, max_age=max_age_seconds)
    except SignatureExpired:
        return None, "expired"
    except BadSignature:
        return None, "invalid"

    if payload.get("purpose") != purpose:
        return None, "invalid"

    return payload.get("email"), None
