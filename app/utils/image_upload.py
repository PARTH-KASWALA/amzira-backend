import os
import uuid
from io import BytesIO
from fastapi import UploadFile, HTTPException
from PIL import Image
from app.core.config import settings
import imghdr
import logging

logger = logging.getLogger(__name__)


ALLOWED_MIME = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'webp': 'image/webp'
}


def save_product_image(file: UploadFile) -> str:
    """Save uploaded image safely and return public file path.

    Validations:
    - Check extension and MIME type
    - Enforce max upload size from settings
    - Validate actual image headers using Pillow
    - Never trust client filename
    """
    # Validate extension
    filename = getattr(file, 'filename', '') or ''
    file_extension = filename.split('.')[-1].lower() if '.' in filename else ''
    if file_extension not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid file extension")

    # Validate content type header if provided
    content_type = getattr(file, 'content_type', '')
    expected_mime = ALLOWED_MIME.get(file_extension)
    if content_type and expected_mime and content_type != expected_mime:
        raise HTTPException(status_code=400, detail="MIME type does not match file extension")

    # Read up to max size + 1 to enforce limit
    max_size = settings.MAX_UPLOAD_SIZE
    data = file.file.read(max_size + 1)
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > max_size:
        raise HTTPException(status_code=400, detail="File too large")

    # Verify image header using imghdr and Pillow
    detected = imghdr.what(None, h=data)
    if detected is None or detected not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid image file")

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

    # Create directory
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
    """Delete image file from filesystem"""
    try:
        path = image_path.lstrip('/')
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        logger.exception("Error deleting image: %s", image_path)





