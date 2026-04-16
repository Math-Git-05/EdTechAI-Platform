from flask import Blueprint, render_template

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    """Pagina de inicio."""
    return render_template("index.html")


@main_bp.route("/landing")
def landing():
    """Pagina de aterrizaje."""
    return render_template("landing.html")


@main_bp.route("/explorar-carreras")
def explorar_carreras():
    """Pagina publica para explorar carreras tecnicas."""
    return render_template("explorar_carreras.html")


@main_bp.route("/maintenance")
def maintenance():
    """Pantalla de mantenimiento para usuarios no admin."""
    return render_template("maintenance.html"), 503
