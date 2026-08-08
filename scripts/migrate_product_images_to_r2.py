from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.product import ProductImage
from app.utils.image_upload import _extract_r2_key, _get_r2_client, _upload_product_image_to_r2


def _read_image_bytes(image_url: str) -> bytes | None:
    normalized = str(image_url or "").strip()
    if not normalized or normalized.startswith("http://") or normalized.startswith("https://"):
        return None

    relative = normalized.lstrip("/")
    file_path = Path(relative)
    if not file_path.is_absolute():
        file_path = Path.cwd() / relative
    if not file_path.exists() or not file_path.is_file():
        return None
    return file_path.read_bytes()


def main() -> None:
    if not settings.r2_enabled:
        raise SystemExit("R2 is not configured. Set R2_* env vars before running this migration.")

    client = _get_r2_client()
    if client is None:
        raise SystemExit("Failed to initialize the R2 client.")

    db = SessionLocal()
    migrated = 0
    skipped = 0
    try:
        images = db.query(ProductImage).order_by(ProductImage.id.asc()).all()
        for image in images:
            if _extract_r2_key(image.image_url):
                skipped += 1
                continue

            image_bytes = _read_image_bytes(image.image_url)
            if image_bytes is None:
                skipped += 1
                continue

            remote_url = _upload_product_image_to_r2(image_bytes)
            if not remote_url:
                skipped += 1
                continue

            image.image_url = remote_url
            migrated += 1

        db.commit()
    finally:
        db.close()

    print(f"migrated={migrated} skipped={skipped}")


if __name__ == "__main__":
    main()
