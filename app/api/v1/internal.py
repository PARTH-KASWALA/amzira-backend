"""Short-lived operational endpoints kept disabled unless explicitly armed."""

import secrets
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from app.core.cache import invalidate_product_cache
from app.core.config import settings
from app.db.session import get_db
from app.schemas.catalog_import import CatalogImportRequest
from app.services.catalog_import_service import CatalogImportValidationError, import_catalog
from app.utils.image_upload import upload_r2_object

router = APIRouter()


def _require_refresh_token(token: str | None = Header(default=None, alias="X-Catalog-Refresh-Token")) -> None:
    configured = (settings.CATALOG_REFRESH_TOKEN or "").strip()
    if not configured or not token or not secrets.compare_digest(token, configured):
        # Do not reveal whether the operational route is configured.
        raise HTTPException(status_code=404, detail="Not found")


def _validate_catalog_key(key: str) -> str:
    normalized = str(key or "").strip()
    parts = normalized.split("/")
    if (
        len(parts) != 3
        or parts[0] != "catalog-v2"
        or not parts[1]
        or not parts[2].endswith(".webp")
        or not parts[2][:-5].isdigit()
        or ".." in normalized
    ):
        raise HTTPException(status_code=400, detail="Invalid catalog media key")
    return normalized


@router.post("/catalog-media", include_in_schema=False, dependencies=[Depends(_require_refresh_token)])
async def upload_catalog_media(
    key: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload one prepared catalog image to the temporary v2 media namespace."""
    object_key = _validate_catalog_key(key)
    data = await file.read(settings.MAX_UPLOAD_SIZE + 1)
    if len(data) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Catalog media file is too large")
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid catalog image") from exc
    try:
        upload_r2_object(data, object_key, content_type="image/webp")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Catalog media storage is unavailable") from exc
    return {"key": object_key, "bytes": len(data)}


@router.post("/catalog-import", include_in_schema=False, dependencies=[Depends(_require_refresh_token)])
def import_catalog_once(payload: CatalogImportRequest, db: Session = Depends(get_db)):
    """Apply the prepared catalog atomically and invalidate its product cache entries."""
    try:
        report = import_catalog(db, payload)
    except CatalogImportValidationError as exc:
        raise HTTPException(status_code=422, detail={"message": "Catalog validation failed", "errors": exc.errors}) from exc
    if not payload.dry_run:
        invalidate_product_cache(slugs=report.get("changed_slugs"))
    return report
