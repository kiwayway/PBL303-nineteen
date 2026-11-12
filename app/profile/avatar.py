"""Validasi dan penyimpanan foto profil (pola sama dengan category_images)."""
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from flask import current_app
from werkzeug.datastructures import FileStorage

ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
AVATAR_SIZE = 512
PREFIX = "uploads/avatars/"


def _static_dir():
    return Path(current_app.root_path) / "static"


def _folder():
    return _static_dir() / "uploads" / "avatars"


def save_avatar(upload: FileStorage) -> str:
    """Decode lalu encode ulang gambar (JPG/PNG/WebP), jadikan persegi 512px."""
    raw = upload.stream.read(MAX_AVATAR_BYTES + 1)
    if not raw:
        raise ValueError("Pilih foto terlebih dahulu.")
    if len(raw) > MAX_AVATAR_BYTES:
        raise ValueError("Ukuran foto melebihi batas 2 MB.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            if fmt not in ALLOWED_FORMATS:
                raise ValueError("Format foto harus JPG, PNG, atau WebP.")
            if probe.width * probe.height > MAX_IMAGE_PIXELS:
                raise ValueError("Dimensi foto terlalu besar.")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            if fmt == "JPEG" and image.mode == "RGBA":
                image = image.convert("RGB")
            image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE))
            out = io.BytesIO()
            opts = {"optimize": True}
            if fmt == "JPEG":
                opts["quality"] = 88
            image.save(out, format=fmt, **opts)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ValueError("File tidak dapat dibaca sebagai foto yang valid.") from None

    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ALLOWED_FORMATS[fmt]}"
    (folder / name).write_bytes(out.getvalue())
    return PREFIX + name


def delete_avatar(path):
    """Hapus hanya file hasil upload avatar, tidak pernah path sembarang."""
    if not path or not path.startswith(PREFIX):
        return
    root = _folder().resolve()
    target = (_static_dir() / path).resolve()
    if target.parent == root and target.is_file():
        target.unlink()
