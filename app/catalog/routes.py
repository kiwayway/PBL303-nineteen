from flask import Blueprint, render_template

from ..auth.routes import current_user
from ..models import Service

bp = Blueprint("catalog", __name__)


@bp.route("/jasa")
def list_services():
    services = Service.query.all()
    return render_template("catalog/list.html", services=services, user=current_user())