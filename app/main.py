from flask import Blueprint, render_template

from .auth.routes import current_user

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return render_template("index.html", user=current_user())


@bp.route("/health")
def health():
    return {"status": "ok"}
