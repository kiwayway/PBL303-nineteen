from importlib import import_module

routes = import_module(".routes", __name__)
bp = routes.bp
from . import google  # noqa: F401  (mendaftarkan route Google ke blueprint)

__all__ = ["bp"]
