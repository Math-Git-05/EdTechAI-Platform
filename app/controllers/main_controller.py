from flask import Blueprint, render_template

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    """Página de inicio"""
    return render_template('index.html')

@main_bp.route('/landing')
def landing():
    """Página de aterrizaje"""
    return render_template('landing.html')