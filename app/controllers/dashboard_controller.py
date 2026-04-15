from flask import Blueprint, redirect, url_for
from flask_login import current_user, login_required

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    """Dashboard principal: siempre redirige segun rol."""
    if current_user.role == "admin":
        return redirect(url_for("admin.index"))
    if current_user.role == "profesor":
        return redirect(url_for("profesor.index"))
    return redirect(url_for("estudiante.index"))


@dashboard_bp.route("/estudiante")
@login_required
def estudiante():
    return redirect(url_for("estudiante.index"))


@dashboard_bp.route("/profesor")
@login_required
def profesor():
    return redirect(url_for("profesor.index"))


@dashboard_bp.route("/admin")
@login_required
def admin():
    return redirect(url_for("admin.index"))
