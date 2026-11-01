from flask import Blueprint

from ..models import Service

bp = Blueprint("catalog", __name__)


@bp.route("/jasa")
def list_services():
    services = Service.query.all()
    return ", ".join(s.title for s in services) or "Belum ada jasa"