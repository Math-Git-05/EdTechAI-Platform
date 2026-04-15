from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from html import escape

from flask import current_app


@dataclass(frozen=True)
class EmailTemplatePayload:
    subject: str
    html: str
    text: str
    sender: str | None = None
    reply_to: str | None = None


def _safe_display_name(name: str | None) -> str:
    value = (name or "").strip()
    return value or "estudiante"


def _safe_brand_name() -> str:
    return (current_app.config.get("MAIL_BRAND_NAME") or "EdTech AI").strip() or "EdTech AI"


def _sender_noreply() -> str | None:
    return current_app.config.get("MAIL_FROM_NOREPLY") or current_app.config.get("MAIL_FROM")


def _sender_support() -> str | None:
    explicit_support = _extract_email_address(current_app.config.get("MAIL_FROM_SUPPORT"))
    if explicit_support:
        return explicit_support
    return _default_support_email()


def _default_support_email() -> str:
    from_candidates = [
        current_app.config.get("MAIL_FROM_SUPPORT"),
        current_app.config.get("MAIL_REPLY_TO_SUPPORT"),
        current_app.config.get("MAIL_FROM_NOREPLY"),
        current_app.config.get("MAIL_FROM"),
    ]
    for raw in from_candidates:
        address = _extract_email_address(raw)
        if "@" not in address:
            continue
        _, domain = address.split("@", 1)
        domain = domain.strip().lower()
        if domain:
            return f"support@{domain}"
    return "support@edtech.local"


def _reply_to_support() -> str | None:
    explicit_reply_to = _extract_email_address(current_app.config.get("MAIL_REPLY_TO_SUPPORT"))
    if explicit_reply_to:
        return explicit_reply_to

    explicit_support = _extract_email_address(_sender_support())
    if explicit_support:
        return explicit_support

    return _default_support_email()


def _extract_email_address(raw_value: str | None) -> str:
    _, address = parseaddr((raw_value or "").strip())
    if address:
        return address
    return (raw_value or "").strip()


def _support_contact() -> str:
    return _extract_email_address(_reply_to_support()) or _default_support_email()


def _accent_color(kind: str) -> str:
    return {
        "success": "#059669",
        "warning": "#b45309",
        "results": "#1d4ed8",
        "security": "#1e3a8a",
    }.get(kind, "#1d4ed8")


def _format_score(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _render_shell(
    *,
    eyebrow: str,
    title: str,
    subtitle: str,
    paragraphs: list[str],
    button_label: str | None = None,
    button_url: str | None = None,
    link_label: str | None = None,
    link_url: str | None = None,
    bullet_points: list[str] | None = None,
    info_rows: list[tuple[str, str]] | None = None,
    notice: str | None = None,
    accent_kind: str = "default",
) -> str:
    accent = _accent_color(accent_kind)
    brand = _safe_brand_name()

    paragraphs_html = "".join(
        f'<p style="margin:0 0 14px 0;font-size:15px;line-height:1.65;color:#334155;">{escape(paragraph)}</p>'
        for paragraph in paragraphs
    )

    bullets_html = ""
    if bullet_points:
        items = "".join(
            f'<li style="margin:0 0 8px 0;font-size:14px;line-height:1.55;color:#334155;">{escape(item)}</li>'
            for item in bullet_points
        )
        bullets_html = f"""
        <div style="margin:20px 0 0 0;padding:16px 18px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;">
          <ul style="margin:0;padding-left:20px;">{items}</ul>
        </div>
        """

    info_html = ""
    if info_rows:
        rows = "".join(
            f"""
            <tr>
              <td style="padding:9px 0;border-bottom:1px solid #e2e8f0;color:#64748b;font-size:13px;">{escape(label)}</td>
              <td style="padding:9px 0;border-bottom:1px solid #e2e8f0;color:#0f172a;font-size:13px;font-weight:600;text-align:right;">{escape(value)}</td>
            </tr>
            """
            for label, value in info_rows
        )
        info_html = f"""
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:20px 0 0 0;border-collapse:collapse;">
          {rows}
        </table>
        """

    notice_html = ""
    if notice:
        notice_html = f"""
        <div style="margin:18px 0 0 0;padding:13px 14px;border:1px solid #fde68a;background:#fffbeb;border-radius:10px;font-size:13px;color:#92400e;line-height:1.55;">
          {escape(notice)}
        </div>
        """

    button_html = ""
    if button_label and button_url:
        button_html = f"""
        <div style="margin:24px 0 8px 0;">
          <a href="{escape(button_url)}" style="display:inline-block;background:{accent};color:#ffffff;text-decoration:none;font-weight:700;font-size:14px;padding:12px 22px;border-radius:999px;">{escape(button_label)}</a>
        </div>
        """

    link_html = ""
    if link_label and link_url:
        link_html = f"""
        <p style="margin:8px 0 0 0;font-size:13px;line-height:1.55;color:#475569;">
          {escape(link_label)}:
          <a href="{escape(link_url)}" style="color:{accent};word-break:break-all;text-decoration:none;">{escape(link_url)}</a>
        </p>
        """

    year = datetime.utcnow().year
    support_email = _support_contact()

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:#eef2ff;font-family:Arial,'Segoe UI',sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eef2ff;padding:24px 8px;">
    <tr>
      <td align="center">
        <table role="presentation" width="620" cellspacing="0" cellpadding="0" style="max-width:620px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #dbe2f0;">
          <tr>
            <td style="padding:26px 28px;background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 100%);">
              <p style="margin:0 0 8px 0;color:#bfdbfe;font-size:12px;letter-spacing:.08em;text-transform:uppercase;font-weight:700;">{escape(eyebrow)}</p>
              <h1 style="margin:0;color:#ffffff;font-size:28px;line-height:1.2;">{escape(title)}</h1>
              <p style="margin:12px 0 0 0;color:#dbeafe;font-size:14px;line-height:1.55;">{escape(subtitle)}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:30px 28px 22px 28px;">
              {paragraphs_html}
              {button_html}
              {link_html}
              {info_html}
              {bullets_html}
              {notice_html}
            </td>
          </tr>
          <tr>
            <td style="padding:16px 28px 24px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;">
              <p style="margin:0 0 6px 0;font-size:12px;color:#64748b;">{escape(brand)} · Soporte: {escape(support_email)}</p>
              <p style="margin:0;font-size:12px;color:#94a3b8;">© {year} {escape(brand)}. Mensaje automático, por favor no respondas directamente a este correo.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def build_registration_welcome_email(*, user_name: str) -> EmailTemplatePayload:
    safe_name = _safe_display_name(user_name)
    subject = "Te damos la bienvenida a EdTech AI"
    html = _render_shell(
        eyebrow="Registro recibido",
        title="Bienvenida a EdTech AI",
        subtitle="Tu cuenta fue creada correctamente y está en proceso de activación.",
        paragraphs=[
            f"Hola {safe_name}, gracias por registrarte en la plataforma.",
            "Para proteger tu cuenta, te enviaremos un correo de verificación desde soporte.",
            "Después de verificar el correo, tu acceso quedará en revisión administrativa antes de habilitar el inicio de sesión.",
        ],
        bullet_points=[
            "Verifica tu correo desde el enlace de activación.",
            "Espera la aprobación del administrador.",
            "Luego podrás iniciar sesión y completar tu evaluación vocacional.",
        ],
        accent_kind="success",
    )
    text = (
        f"Hola {safe_name},\n\n"
        "Tu cuenta en EdTech AI fue creada correctamente.\n"
        "Recibirás un correo de verificación y, al confirmarlo, quedará pendiente de aprobación administrativa.\n\n"
        "Gracias por registrarte."
    )
    return EmailTemplatePayload(
        subject=subject,
        html=html,
        text=text,
        sender=_sender_noreply(),
        reply_to=_reply_to_support(),
    )


def build_verification_email(*, user_name: str, verify_url: str) -> EmailTemplatePayload:
    safe_name = _safe_display_name(user_name)
    subject = "Verifica tu correo para activar tu cuenta"
    html = _render_shell(
        eyebrow="Verificación de correo",
        title="Activa tu cuenta",
        subtitle="Confirma que este correo te pertenece para completar el proceso de acceso.",
        paragraphs=[
            f"Hola {safe_name}, ya casi estamos listos.",
            "Para activar tu cuenta en EdTech AI, confirma tu correo haciendo clic en el botón.",
        ],
        button_label="Verificar mi correo",
        button_url=verify_url,
        link_label="Si el botón no abre, copia este enlace",
        link_url=verify_url,
        notice="Este enlace expira en 24 horas por seguridad.",
        accent_kind="security",
    )
    text = (
        f"Hola {safe_name},\n\n"
        "Para verificar tu correo en EdTech AI, abre este enlace:\n"
        f"{verify_url}\n\n"
        "El enlace expira en 24 horas."
    )
    return EmailTemplatePayload(
        subject=subject,
        html=html,
        text=text,
        sender=_sender_support(),
        reply_to=_reply_to_support(),
    )


def build_post_verification_welcome_email(*, user_name: str, is_active: bool) -> EmailTemplatePayload:
    safe_name = _safe_display_name(user_name)
    subject = "Correo verificado: seguimos con tu activación"
    subtitle = (
        "Tu correo ya fue validado y tu cuenta está lista para usar."
        if is_active
        else "Tu correo ya fue validado y ahora tu cuenta está en revisión administrativa."
    )
    paragraphs = [f"Excelente, {safe_name}. Ya verificaste tu correo correctamente."]
    if is_active:
        paragraphs.append("Tu cuenta está activa. Ya puedes iniciar sesión y continuar tu evaluación.")
    else:
        paragraphs.append(
            "Tu correo está confirmado. El siguiente paso es la aprobación de un administrador para habilitar el acceso."
        )

    html = _render_shell(
        eyebrow="Verificación completada",
        title="Correo confirmado",
        subtitle=subtitle,
        paragraphs=paragraphs,
        accent_kind="success",
    )
    text = (
        f"Hola {safe_name},\n\n"
        "Tu correo fue verificado correctamente.\n"
        + (
            "Tu cuenta ya está activa y puedes iniciar sesión."
            if is_active
            else "Tu cuenta quedó pendiente de aprobación administrativa."
        )
    )
    return EmailTemplatePayload(
        subject=subject,
        html=html,
        text=text,
        sender=_sender_noreply(),
        reply_to=_reply_to_support(),
    )


def build_reset_password_email(*, user_name: str, reset_url: str) -> EmailTemplatePayload:
    safe_name = _safe_display_name(user_name)
    subject = "Solicitud de recuperación de contraseña"
    html = _render_shell(
        eyebrow="Seguridad de cuenta",
        title="Restablece tu contraseña",
        subtitle="Recibimos una solicitud para cambiar la contraseña de tu cuenta.",
        paragraphs=[
            f"Hola {safe_name}, si solicitaste este cambio, usa el botón para continuar.",
            "Si no hiciste esta solicitud, puedes ignorar este mensaje sin riesgo.",
        ],
        button_label="Cambiar contraseña",
        button_url=reset_url,
        link_label="Enlace alternativo",
        link_url=reset_url,
        notice="Por seguridad, este enlace expira en 1 hora.",
        accent_kind="warning",
    )
    text = (
        f"Hola {safe_name},\n\n"
        "Usa este enlace para restablecer tu contraseña:\n"
        f"{reset_url}\n\n"
        "Si no solicitaste este cambio, ignora este mensaje.\n"
        "El enlace expira en 1 hora."
    )
    return EmailTemplatePayload(
        subject=subject,
        html=html,
        text=text,
        sender=_sender_support(),
        reply_to=_reply_to_support(),
    )


def build_password_changed_email(*, user_name: str) -> EmailTemplatePayload:
    safe_name = _safe_display_name(user_name)
    subject = "Contraseña actualizada correctamente"
    html = _render_shell(
        eyebrow="Seguridad confirmada",
        title="Tu contraseña fue actualizada",
        subtitle="El cambio se aplicó con éxito y tu cuenta está protegida.",
        paragraphs=[
            f"Hola {safe_name}, tu contraseña fue cambiada correctamente.",
            "Si no reconoces este cambio, contacta soporte de inmediato para proteger tu cuenta.",
        ],
        accent_kind="success",
    )
    text = (
        f"Hola {safe_name},\n\n"
        "Tu contraseña en EdTech AI fue actualizada correctamente.\n"
        "Si no reconoces este cambio, contacta soporte inmediatamente."
    )
    return EmailTemplatePayload(
        subject=subject,
        html=html,
        text=text,
        sender=_sender_support(),
        reply_to=_reply_to_support(),
    )


def build_evaluation_results_email(
    *,
    user_name: str,
    dashboard_url: str,
    recommendation: dict | None = None,
) -> EmailTemplatePayload:
    safe_name = _safe_display_name(user_name)
    recommendation = recommendation or {}
    primary = recommendation.get("primary_recommendation") or {}
    top = recommendation.get("top_recommendations") or []

    track_name = primary.get("track_name") or "Ruta técnica recomendada"
    affinity = primary.get("affinity_level") or "En análisis"
    score = _format_score(primary.get("score"))
    guidance = recommendation.get("guidance") or "Ingresa al panel para ver el desglose completo de resultados."

    rows = [
        ("Recomendación principal", str(track_name)),
        ("Nivel de afinidad", str(affinity)),
        ("Puntaje global", f"{score}/100"),
    ]
    ranking_lines = [f"{row.get('track_name', 'Ruta')}: {_format_score(row.get('score'))}/100" for row in top[:3]]

    html = _render_shell(
        eyebrow="Resultados disponibles",
        title="Tu informe vocacional está listo",
        subtitle="Tu evaluación fue procesada y ya tienes una recomendación inicial.",
        paragraphs=[
            f"Hola {safe_name}, ya puedes revisar tus resultados.",
            guidance,
        ],
        button_label="Ver mis resultados",
        button_url=dashboard_url,
        link_label="Acceso directo al panel",
        link_url=dashboard_url,
        info_rows=rows,
        bullet_points=ranking_lines or None,
        accent_kind="results",
    )
    text = (
        f"Hola {safe_name},\n\n"
        "Tu informe vocacional ya está disponible.\n"
        f"Recomendación principal: {track_name}\n"
        f"Nivel de afinidad: {affinity}\n"
        f"Puntaje global: {score}/100\n\n"
        f"Ver resultados: {dashboard_url}"
    )
    return EmailTemplatePayload(
        subject="Tus resultados vocacionales están disponibles",
        html=html,
        text=text,
        sender=_sender_noreply(),
        reply_to=_reply_to_support(),
    )
