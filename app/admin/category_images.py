"""Secure category image validation, normalization, and storage."""
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from flask import current_app
from werkzeug.datastructures import FileStorage

ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_BACKGROUND_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


def save_category_image(upload: FileStorage, kind: str) -> str:
    """Decode and re-encode an allowed raster image; never trust client MIME/name."""
    if kind not in {"logo", "background"}:
        raise ValueError("Jenis gambar tidak valid.")
    limit = MAX_LOGO_BYTES if kind == "logo" else MAX_BACKGROUND_BYTES
    raw = upload.stream.read(limit + 1)
    if not raw:
        raise ValueError("Pilih gambar terlebih dahulu.")
    if len(raw) > limit:
        raise ValueError(f"Ukuran {kind} melebihi batas {limit // (1024 * 1024)} MB.")

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            image_format = probe.format
            if image_format not in ALLOWED_FORMATS:
                raise ValueError("Format gambar harus JPG, PNG, atau WebP. SVG tidak diizinkan.")
            if probe.width * probe.height > MAX_IMAGE_PIXELS:
                raise ValueError("Dimensi gambar terlalu besar.")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            # Re-encode decoded pixels, stripping metadata and any appended payload.
            extension = ALLOWED_FORMATS[image_format]
            if image_format == "JPEG" and image.mode == "RGBA":
                image = image.convert("RGB")
            output = io.BytesIO()
            save_options = {"optimize": True}
            if image_format == "JPEG":
                save_options["quality"] = 88
            image.save(output, format=image_format, **save_options)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("File tidak dapat dibaca sebagai gambar yang valid.") from None

    folder = Path(current_app.root_path) / "static" / "uploads" / "categories"
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{extension}"
    destination = folder / filename
    destination.write_bytes(output.getvalue())
    return f"uploads/categories/{filename}"


def delete_category_image(path: str | None) -> None:
    """Delete only generated category uploads, never arbitrary filesystem paths."""
    if not path or not path.startswith("uploads/categories/"):
        return
    root = (Path(current_app.root_path) / "static" / "uploads" / "categories").resolve()
    target = (Path(current_app.root_path) / "static" / path).resolve()
    if target.parent == root and target.is_file():
        target.unlink()
