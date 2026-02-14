from io import BytesIO
from typing import Optional, Set

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, field_validator, model_validator


IMAGE_MIME_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class ImageUploadValidation(BaseModel):
    filename: str
    content_type: Optional[str] = None
    data: bytes
    max_size: int
    allowed_extensions: Set[str]
    detected_extension: Optional[str] = None

    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def normalize_extensions(cls, value):
        return {str(ext).lower() for ext in value}

    @model_validator(mode="after")
    def validate_image(self):
        if not self.data:
            raise ValueError("Empty file")
        if len(self.data) > self.max_size:
            raise ValueError("File too large")

        filename_extension = self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""
        if filename_extension not in self.allowed_extensions:
            raise ValueError("File extension not allowed")

        try:
            with Image.open(BytesIO(self.data)) as image:
                detected = (image.format or "").lower()
        except UnidentifiedImageError:
            detected = ""

        if not detected:
            raise ValueError("Invalid file type")

        detected_extension = "jpg" if detected == "jpeg" else detected
        if detected_extension not in self.allowed_extensions:
            raise ValueError("Invalid file type")

        detected_mime = IMAGE_MIME_TYPES.get(detected_extension)
        if self.content_type and detected_mime and self.content_type.lower() != detected_mime:
            raise ValueError("Invalid image MIME type")

        if filename_extension and filename_extension != detected_extension and not (
            filename_extension in {"jpg", "jpeg"} and detected_extension in {"jpg", "jpeg"}
        ):
            raise ValueError("File extension does not match content")

        self.detected_extension = detected_extension
        return self
