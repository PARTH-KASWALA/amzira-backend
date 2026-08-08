import os
import uuid
from io import BytesIO
from urllib.parse import urlparse

from fastapi import UploadFile, HTTPException
from PIL import Image
from pydantic import ValidationError
from app.core.config import settings
from app.schemas.image import ImageUploadValidation
import logging

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
FORMAT_TO_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def _get_r2_client():
    if not settings.r2_enabled:
        return None
    try:
        import boto3  # type: ignore
    except Exception as exc:
        logger.warning("boto3_unavailable_for_r2", exc_info=exc)
        return None

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def _build_r2_object_url(key: str) -> str:
    return f"{settings.R2_PUBLIC_URL.rstrip('/')}/{key}"


def _upload_product_image_to_r2(data: bytes) -> str | None:
    client = _get_r2_client()
    if client is None:
        return None

    output = BytesIO()
    with Image.open(BytesIO(data)) as img:
        if img.mode in {"RGBA", "P"}:
            img = img.convert("RGB")
        img.save(output, format="WEBP", quality=85, optimize=True)
    output.seek(0)

    key = f"products/{uuid.uuid4().hex}.webp"
    client.upload_fileobj(
        output,
        settings.R2_BUCKET_NAME,
        key,
        ExtraArgs={
            "ContentType": "image/webp",
            "CacheControl": "public, max-age=31536000",
        },
    )
    return _build_r2_object_url(key)


def _extract_r2_key(image_path: str) -> str | None:
    if not settings.r2_enabled:
        return None
    public_url = settings.R2_PUBLIC_URL.rstrip("/")
    if not image_path.startswith(public_url):
        return None
    parsed = urlparse(image_path)
    key = parsed.path.lstrip("/")
    return key or None


def validate_image_upload(file: UploadFile) -> tuple[bytes, str]:
    """Validate image upload and return raw bytes plus normalized extension."""
    filename = getattr(file, "filename", "") or ""
    max_size = settings.MAX_UPLOAD_SIZE
    file.file.seek(0)
    try:
        data = file.file.read(max_size + 1)
        try:
            validated = ImageUploadValidation.model_validate(
                {
                    "filename": filename,
                    "content_type": getattr(file, "content_type", None),
                    "data": data,
                    "max_size": max_size,
                    "allowed_extensions": set(settings.ALLOWED_EXTENSIONS),
                }
            )
        except ValidationError as exc:
            error_message = exc.errors()[0].get("msg", "Invalid image file")
            raise HTTPException(status_code=400, detail=error_message) from exc

        detected_mime = ""
        try:
            import magic  # type: ignore
            detected_mime = magic.from_buffer(validated.data, mime=True)
        except Exception:
            # Fallback for environments without libmagic; keep strict MIME allowlist.
            with Image.open(BytesIO(validated.data)) as image:
                detected_mime = FORMAT_TO_MIME.get((image.format or "").upper(), "")

        if detected_mime not in ALLOWED_IMAGE_MIME_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image MIME type")
        return validated.data, validated.detected_extension or ""
    finally:
        file.file.seek(0)


def save_product_image(file: UploadFile) -> str:
    """Save uploaded image safely and return public file path.

    Validations:
    - Check extension and MIME type
    - Enforce max upload size from settings
    - Validate actual image headers using Pillow
    - Never trust client filename
    """
    data, file_extension = validate_image_upload(file)

    try:
        img = Image.open(BytesIO(data))
        img.verify()  # will raise if broken
        width, height = img.size
        # Prevent decompression bomb by limiting pixel count
        if width * height > 50_000_000:
            raise HTTPException(status_code=400, detail="Image too large")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Invalid image upload: %s", e)
        raise HTTPException(status_code=400, detail="Invalid image file")

    remote_url = _upload_product_image_to_r2(data)
    if remote_url:
        return remote_url

    # Fallback for local/dev environments.
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

    # Save file with safe unique name
    unique_filename = f"{uuid.uuid4()}.{file_extension}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)
    with open(file_path, 'wb') as out:
        out.write(data)

    # Optionally optimize/resize
    try:
        optimize_image(file_path)
    except Exception:
        logger.exception("Image optimization failed for %s", file_path)

    return f"/{file_path}"


def optimize_image(file_path: str, max_width: int = 1200, quality: int = 85):
    """Resize and compress image using Pillow."""
    img = Image.open(file_path)
    if img.width > max_width:
        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.LANCZOS)
    if img.mode == 'RGBA':
        img = img.convert('RGB')
    img.save(file_path, 'JPEG', quality=quality, optimize=True)


def delete_product_image(image_path: str):
    """Delete image from R2 or local filesystem."""
    try:
        r2_key = _extract_r2_key(image_path)
        if r2_key:
            client = _get_r2_client()
            if client is not None:
                client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=r2_key)
            return
        path = image_path.lstrip('/')
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        logger.exception("Error deleting image: %s", image_path)
