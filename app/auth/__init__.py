from .routes import bp
from . import google  # noqa: F401  (mendaftarkan route Google ke blueprint)

__all__ = ["bp"]
