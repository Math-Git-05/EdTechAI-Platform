import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    *,
    sender: str | None = None,
    reply_to: str | None = None,
) -> bool:
    smtp_user = current_app.config.get("MAIL_USERNAME")
    # Solo limpiamos extremos; no alteramos espacios internos del password.
    smtp_pass = (current_app.config.get("MAIL_PASSWORD") or "").strip()
    smtp_server = current_app.config.get("MAIL_SERVER")
    smtp_port = current_app.config.get("MAIL_PORT", 587)
    use_tls = current_app.config.get("MAIL_USE_TLS", True)
    use_ssl = current_app.config.get("MAIL_USE_SSL", False)
    default_sender = current_app.config.get("MAIL_FROM", smtp_user)
    resolved_sender = sender or default_sender
    resolved_reply_to = reply_to or current_app.config.get("MAIL_REPLY_TO_SUPPORT")

    if resolved_sender and "@" in resolved_sender and "<" not in resolved_sender:
        brand_name = current_app.config.get("MAIL_BRAND_NAME", "EdTech AI")
        resolved_sender = formataddr((brand_name, resolved_sender))

    if not smtp_user or not smtp_pass:
        current_app.config["LAST_EMAIL_ERROR"] = "MAIL_USERNAME/MAIL_PASSWORD no configurados"
        current_app.logger.warning(
            "MAIL_USERNAME/MAIL_PASSWORD no configurados. No se envio correo a %s", to_email
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = resolved_sender
    msg["To"] = to_email
    if resolved_reply_to:
        msg["Reply-To"] = resolved_reply_to
    msg.set_content(text_body or _strip_html(html_body))
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(smtp_server, smtp_port, timeout=20) as server:
            if use_tls and not use_ssl:
                server.starttls(context=ssl.create_default_context())
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        current_app.config["LAST_EMAIL_ERROR"] = None
        return True
    except Exception as exc:
        current_app.config["LAST_EMAIL_ERROR"] = str(exc)
        current_app.logger.exception("Error enviando email a %s: %s", to_email, exc)
        return False
