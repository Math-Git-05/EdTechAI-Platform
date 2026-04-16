from flask import Blueprint, render_template

from app.services.settings_service import get_datetime_setting, get_setting

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    """Página de inicio."""
    return render_template("index.html")


@main_bp.route("/landing")
def landing():
    """Página de aterrizaje."""
    return render_template("landing.html")


@main_bp.route("/explorar-carreras")
def explorar_carreras():
    """Página pública para explorar carreras técnicas."""
    return render_template("explorar_carreras.html")


@main_bp.route("/maintenance")
def maintenance():
    """Pantalla de mantenimiento para usuarios no admin."""
    maintenance_eta_dt = get_datetime_setting("maintenance_eta_text")
    maintenance_eta_text = (
        maintenance_eta_dt.strftime("%Y-%m-%d %H:%M")
        if maintenance_eta_dt
        else (get_setting("maintenance_eta_text", "") or "").strip()
    )
    return (
        render_template(
            "maintenance.html",
            maintenance_eta_text=maintenance_eta_text,
            evaluation_window_start=get_datetime_setting("evaluation_window_start"),
            evaluation_window_end=get_datetime_setting("evaluation_window_end"),
        ),
        503,
    )
