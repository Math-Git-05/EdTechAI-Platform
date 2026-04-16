from datetime import datetime
import re

from flask import (
    Blueprint,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models.student_profile import StudentProfile
from app.models.teacher_profile import TeacherProfile
from app.models.user import User
from app.services.profile_service import (
    ACADEMIC_STATUS,
    DEFAULT_SCHOOL_ID,
    GENDERS,
    LABOR_STATUS,
    calculate_age,
    ensure_unique_student_id,
    generate_student_id,
    is_valid_student_id,
    normalize_student_id,
)
from app.services.email_service import send_email
from app.services.email_template_service import (
    build_password_changed_email,
    build_post_verification_welcome_email,
    build_registration_welcome_email,
    build_reset_password_email,
    build_verification_email,
)
from app.services.student_data_sync_service import sync_student_data_if_exists
from app.services.student_interest_service import sync_student_interest_from_profile
from app.services.token_service import generate_token, verify_token

auth_bp = Blueprint("auth", __name__)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _password_checks(password: str) -> dict[str, bool]:
    value = password or ""
    return {
        "length": len(value) >= 8,
        "upper": bool(re.search(r"[A-Z]", value)),
        "lower": bool(re.search(r"[a-z]", value)),
        "digit": bool(re.search(r"\d", value)),
        "special": bool(re.search(r"[^A-Za-z0-9]", value)),
    }


def _password_validation_error(password: str) -> str | None:
    checks = _password_checks(password)
    if all(checks.values()):
        return None

    missing = []
    if not checks["length"]:
        missing.append("al menos 8 caracteres")
    if not checks["upper"]:
        missing.append("una letra mayuscula")
    if not checks["lower"]:
        missing.append("una letra minuscula")
    if not checks["digit"]:
        missing.append("un numero")
    if not checks["special"]:
        missing.append("un caracter especial")

    if len(missing) == 1:
        return f"La contrasena debe incluir {missing[0]}."
    return "La contrasena debe incluir " + ", ".join(missing[:-1]) + f" y {missing[-1]}."


def _normalize_choice(value: str, allowed: set[str], fallback: str) -> str:
    candidate = (value or "").strip().lower()
    if candidate in allowed:
        return candidate
    return fallback


def _parse_birth_date(value: str):
    raw = (value or "").strip()
    if not raw:
        return None, "Debes indicar la fecha de nacimiento del estudiante."
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), None
    except ValueError:
        return None, "Formato de fecha de nacimiento invalido."


def _parse_interest_value(raw_value: str, field_label: str) -> tuple[float | None, str | None]:
    value = (raw_value or "").strip()
    if value == "":
        return 0.0, None
    try:
        parsed = float(value)
    except ValueError:
        return None, f"El interes de {field_label} debe ser numerico."
    if parsed < 0 or parsed > 100:
        return None, f"El interes de {field_label} debe estar entre 0 y 100."
    return round(parsed, 2), None


def _build_student_profile_from_form(form, nombre: str, apellido: str):
    segundo_apellido = (form.get("segundo_apellido") or "").strip()
    grado_nivel = (form.get("grado_nivel") or "").strip()
    if not segundo_apellido or not grado_nivel:
        return None, "Completa segundo apellido y grado/nivel del estudiante."

    fecha_nacimiento, birth_error = _parse_birth_date(form.get("fecha_nacimiento"))
    if birth_error:
        return None, birth_error

    enrollment_raw = (form.get("enrollment_year") or "").strip()
    enrollment_year = None
    if enrollment_raw:
        try:
            enrollment_year = int(enrollment_raw)
        except ValueError:
            return None, "El ano de ingreso debe ser numerico."

        current_year = datetime.utcnow().year + 1
        if enrollment_year < 1990 or enrollment_year > current_year:
            return None, f"El ano de ingreso debe estar entre 1990 y {current_year}."

    genero = _normalize_choice(form.get("gender"), GENDERS, "prefiero_no_decir")
    academic_status = _normalize_choice(form.get("academic_status"), ACADEMIC_STATUS, "activo")
    school_id = (form.get("school_id") or DEFAULT_SCHOOL_ID).strip().upper() or DEFAULT_SCHOOL_ID

    interest_technology, interest_error = _parse_interest_value(
        form.get("interest_technology"),
        "Technology",
    )
    if interest_error:
        return None, interest_error
    interest_design, interest_error = _parse_interest_value(
        form.get("interest_design"),
        "Design",
    )
    if interest_error:
        return None, interest_error
    interest_business, interest_error = _parse_interest_value(
        form.get("interest_business"),
        "Business",
    )
    if interest_error:
        return None, interest_error
    interest_health, interest_error = _parse_interest_value(
        form.get("interest_health"),
        "Health",
    )
    if interest_error:
        return None, interest_error

    student_id_raw = (form.get("student_id") or form.get("rne") or "").strip()
    student_id = normalize_student_id(student_id_raw)
    if student_id and not is_valid_student_id(student_id):
        return None, "El RNE/Matricula debe tener entre 10 y 24 caracteres alfanumericos."

    student_id_auto = False
    if not student_id:
        student_id = generate_student_id(nombre, apellido, segundo_apellido, fecha_nacimiento)
        student_id_auto = True

    profile_payload = {
        "student_id": student_id,
        "student_id_auto": student_id_auto,
        "segundo_apellido": segundo_apellido,
        "genero": genero,
        "fecha_nacimiento": fecha_nacimiento,
        "edad": calculate_age(fecha_nacimiento),
        "grado_nivel": grado_nivel,
        "school_id": school_id,
        "enrollment_year": enrollment_year,
        "academic_status": academic_status,
        "interest_technology": interest_technology,
        "interest_design": interest_design,
        "interest_business": interest_business,
        "interest_health": interest_health,
    }
    return profile_payload, None


def _build_teacher_profile_from_form(form):
    school_id = (form.get("teacher_school_id") or DEFAULT_SCHOOL_ID).strip().upper() or DEFAULT_SCHOOL_ID
    labor_status = _normalize_choice(form.get("labor_status"), LABOR_STATUS, "activo")
    employee_id = normalize_student_id(form.get("employee_id") or "") or None
    payload = {
        "employee_id": employee_id,
        "especialidad": (form.get("especialidad") or "").strip() or None,
        "departamento": (form.get("departamento") or "").strip() or None,
        "telefono": (form.get("telefono") or "").strip() or None,
        "school_id": school_id,
        "labor_status": labor_status,
    }
    return payload, None


def _build_external_url(endpoint: str, **kwargs) -> str:
    base = current_app.config.get("APP_BASE_URL", "").rstrip("/")
    relative = url_for(endpoint, **kwargs)
    if base:
        return f"{base}{relative}"
    return url_for(endpoint, _external=True, **kwargs)


def _flash_debug_email_error() -> None:
    if not current_app.debug:
        return
    smtp_error = current_app.config.get("LAST_EMAIL_ERROR")
    if smtp_error:
        flash(f"Detalle SMTP: {smtp_error}", "info")


def _reserve_email_slot(user: User, event_key: str) -> bool:
    """Evita envios duplicados del mismo tipo en una ventana corta."""
    cooldown = int(current_app.config.get("AUTH_EMAIL_DEDUP_SECONDS", 90) or 90)
    cooldown = max(5, cooldown)

    now = datetime.utcnow()
    last_event = (getattr(user, "last_email_event", "") or "").strip().lower()
    last_sent_at = getattr(user, "last_email_sent_at", None)
    if (
        last_event == event_key
        and last_sent_at is not None
        and (now - last_sent_at).total_seconds() < cooldown
    ):
        current_app.logger.info(
            "Email duplicado suprimido. evento=%s user_id=%s email=%s",
            event_key,
            user.id,
            user.email,
        )
        return False

    user.last_email_event = event_key
    user.last_email_sent_at = now
    db.session.add(user)
    db.session.commit()
    return True


def _release_email_slot(user: User) -> None:
    """Libera reserva si fallo el SMTP para permitir reintento inmediato."""
    try:
        user.last_email_event = None
        user.last_email_sent_at = None
        db.session.add(user)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _send_guarded_email(*, user: User, event_key: str, payload) -> bool:
    if not _reserve_email_slot(user, event_key):
        # Tratamos como exito silencioso para no mostrar errores por duplicado al usuario.
        return True

    sent = send_email(
        user.email,
        payload.subject,
        payload.html,
        payload.text,
        sender=payload.sender,
        reply_to=payload.reply_to,
    )
    if not sent:
        _release_email_slot(user)
    return sent


def _send_verification_email(user: User) -> bool:
    verify_url = _verification_link(user)
    payload = build_verification_email(user_name=user.nombre, verify_url=verify_url)
    return _send_guarded_email(user=user, event_key="verify_email", payload=payload)


def _send_registration_welcome_email(user: User) -> bool:
    payload = build_registration_welcome_email(user_name=user.nombre)
    return _send_guarded_email(user=user, event_key="register_welcome", payload=payload)


def _send_post_verification_welcome_email(user: User) -> bool:
    payload = build_post_verification_welcome_email(user_name=user.nombre, is_active=bool(user.activo))
    return _send_guarded_email(user=user, event_key="post_verify_welcome", payload=payload)


def _send_reset_password_email(user: User) -> bool:
    reset_url = _reset_password_link(user)
    payload = build_reset_password_email(user_name=user.nombre, reset_url=reset_url)
    return _send_guarded_email(user=user, event_key="reset_password", payload=payload)


def _send_password_changed_email(user: User) -> bool:
    payload = build_password_changed_email(user_name=user.nombre)
    return _send_guarded_email(user=user, event_key="password_changed", payload=payload)


def _verification_link(user: User) -> str:
    token = generate_token(user.email, "verify_email")
    return _build_external_url("auth.verify_email", token=token)


def _reset_password_link(user: User) -> str:
    token = generate_token(user.email, "reset_password")
    return _build_external_url("auth.reset_password", token=token)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = _normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password, password):
            flash("Correo o contrasena incorrectos.", "danger")
            return redirect(url_for("auth.login"))

        if not user.email_verificado:
            sent = _send_verification_email(user)
            if sent:
                flash("Debes verificar tu correo antes de iniciar sesion. Te enviamos un enlace.", "warning")
            else:
                flash("Debes verificar tu correo. No se pudo enviar el email de verificacion.", "warning")
                _flash_debug_email_error()
                if current_app.debug:
                    flash(f"Link local de verificacion: {_verification_link(user)}", "info")
            return redirect(url_for("auth.login"))

        if not user.activo:
            flash("Tu cuenta esta pendiente de aprobacion por un administrador.", "warning")
            return redirect(url_for("auth.login"))

        user.ultimo_login = datetime.utcnow()
        db.session.add(user)
        db.session.commit()
        login_user(user)
        session["show_notification_toast"] = True
        flash(f"Bienvenido, {user.nombre}.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        apellido = (request.form.get("apellido") or "").strip()
        email = _normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        rol = (request.form.get("rol") or "estudiante").strip().lower()

        if rol not in {"estudiante", "profesor"}:
            rol = "estudiante"

        if not nombre or not apellido or not email or not password:
            flash("Completa todos los campos obligatorios.", "warning")
            return redirect(url_for("auth.register"))

        password_error = _password_validation_error(password)
        if password_error:
            flash(password_error, "warning")
            return redirect(url_for("auth.register"))

        existing = User.query.filter_by(email=email).first()
        if existing:
            if existing.email_verificado:
                flash("El correo ya esta registrado.", "warning")
                return redirect(url_for("auth.login"))

            sent = _send_verification_email(existing)
            if sent:
                flash("Ya existe una cuenta pendiente de verificacion. Revisa tu correo.", "info")
            else:
                flash("Ya existe una cuenta pendiente. No se pudo reenviar el correo.", "warning")
                _flash_debug_email_error()
            return redirect(url_for("auth.login"))

        student_profile_payload = None
        teacher_profile_payload = None
        if rol == "estudiante":
            student_profile_payload, profile_error = _build_student_profile_from_form(
                request.form,
                nombre,
                apellido,
            )
            if profile_error:
                flash(profile_error, "warning")
                return redirect(url_for("auth.register"))

            requested_student_id = student_profile_payload["student_id"]
            id_clash = StudentProfile.query.filter_by(student_id=requested_student_id).first()
            if id_clash:
                if student_profile_payload.get("student_id_auto"):
                    student_profile_payload["student_id"] = ensure_unique_student_id(requested_student_id)
                else:
                    flash("El RNE/Matricula ya existe. Usa uno diferente.", "warning")
                    return redirect(url_for("auth.register"))
        elif rol == "profesor":
            teacher_profile_payload, _ = _build_teacher_profile_from_form(request.form)
            employee_id = teacher_profile_payload.get("employee_id")
            if employee_id:
                conflict = TeacherProfile.query.filter_by(employee_id=employee_id).first()
                if conflict:
                    flash("El codigo de empleado ya esta en uso.", "warning")
                    return redirect(url_for("auth.register"))

        user = User(
            nombre=nombre,
            apellido=apellido,
            email=email,
            password=generate_password_hash(password),
            role=rol,
            email_verificado=False,
            activo=False,
        )
        db.session.add(user)
        db.session.flush()

        if rol == "estudiante" and student_profile_payload:
            profile = StudentProfile(
                user_id=user.id,
                student_id=student_profile_payload["student_id"],
                segundo_apellido=student_profile_payload["segundo_apellido"],
                genero=student_profile_payload["genero"],
                fecha_nacimiento=student_profile_payload["fecha_nacimiento"],
                edad=student_profile_payload["edad"],
                grado_nivel=student_profile_payload["grado_nivel"],
                school_id=student_profile_payload["school_id"],
                enrollment_year=student_profile_payload["enrollment_year"],
                academic_status=student_profile_payload["academic_status"],
                interest_technology=student_profile_payload["interest_technology"],
                interest_design=student_profile_payload["interest_design"],
                interest_business=student_profile_payload["interest_business"],
                interest_health=student_profile_payload["interest_health"],
            )
            db.session.add(profile)
            sync_student_data_if_exists(user, profile)
            sync_student_interest_from_profile(profile)

        if rol == "profesor" and teacher_profile_payload:
            profile = TeacherProfile(user_id=user.id, **teacher_profile_payload)
            db.session.add(profile)

        db.session.commit()

        welcome_sent = _send_registration_welcome_email(user)
        verification_sent = _send_verification_email(user)
        if verification_sent:
            flash(
                "Cuenta creada. Verifica tu correo y espera aprobacion del administrador para iniciar sesion.",
                "success",
            )
            if not welcome_sent:
                flash("No se pudo enviar el correo de bienvenida inicial.", "info")
        else:
            flash(
                "Cuenta creada y pendiente de aprobacion, pero no se pudo enviar el correo de verificacion.",
                "warning",
            )
            _flash_debug_email_error()
            if current_app.debug:
                flash(f"Link local de verificacion: {_verification_link(user)}", "info")

        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")


@auth_bp.route("/verify-email/<token>")
def verify_email(token):
    email, error = verify_token(token, "verify_email", max_age_seconds=24 * 60 * 60)
    if error:
        flash("El enlace de verificacion es invalido o expiro.", "danger")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(email=_normalize_email(email)).first()
    if not user:
        flash("No se encontro la cuenta para verificar.", "danger")
        return redirect(url_for("auth.register"))

    if user.email_verificado:
        flash("Tu correo ya estaba verificado.", "info")
        return redirect(url_for("auth.login"))

    user.email_verificado = True
    user.email_verificado_at = datetime.utcnow()
    db.session.add(user)
    db.session.commit()
    welcome_sent = _send_post_verification_welcome_email(user)
    if not welcome_sent and current_app.debug:
        _flash_debug_email_error()

    if user.activo:
        flash("Correo verificado correctamente. Ahora puedes iniciar sesion.", "success")
    else:
        flash(
            "Correo verificado. Tu cuenta esta en revision del administrador antes de habilitar el acceso.",
            "info",
        )
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = _normalize_email(request.form.get("email"))
        user = User.query.filter_by(email=email).first()

        if user and user.activo:
            sent = _send_reset_password_email(user)
            if sent:
                user.reset_requested_at = datetime.utcnow()
                db.session.add(user)
                db.session.commit()
            elif current_app.debug:
                _flash_debug_email_error()
                flash(f"Link local para reset: {_reset_password_link(user)}", "info")

        flash(
            "Si el correo existe en el sistema, te enviamos instrucciones para recuperar la contrasena.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    email, error = verify_token(token, "reset_password", max_age_seconds=60 * 60)
    if error:
        flash("El enlace para restablecer contrasena es invalido o expiro.", "danger")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.filter_by(email=_normalize_email(email)).first()
    if not user:
        flash("No se encontro la cuenta asociada al enlace.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        password_error = _password_validation_error(password)
        if password_error:
            flash(password_error, "warning")
            return redirect(url_for("auth.reset_password", token=token))

        if password != confirm_password:
            flash("Las contrasenas no coinciden.", "warning")
            return redirect(url_for("auth.reset_password", token=token))

        user.password = generate_password_hash(password)
        user.reset_requested_at = None
        db.session.add(user)
        db.session.commit()
        if not _send_password_changed_email(user) and current_app.debug:
            _flash_debug_email_error()
        flash("Contrasena actualizada correctamente. Ya puedes iniciar sesion.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token, email=user.email)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("Sesion cerrada.", "info")
    response = make_response(redirect(url_for("main.index")))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Clear-Site-Data"] = '"cache"'
    return response
