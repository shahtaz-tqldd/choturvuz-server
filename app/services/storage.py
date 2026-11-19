"""Cloudinary media storage.

Every upload gets an explicit, folder-prefixed public id (for example
``choturvuz/images/products/abc123``) so the same code works with both fixed
and dynamic Cloudinary folder modes. Records store the returned
``secure_url``; the public id and resource type are recovered from that URL
when an asset has to be deleted.
"""

import logging
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

logger = logging.getLogger("app")

CLOUDINARY_HOST = "res.cloudinary.com"
RESOURCE_TYPES = ("image", "video", "raw")

# Formats Cloudinary stores as "image" assets (it can preview and transform
# them). Anything else is uploaded as a "raw" file.
IMAGE_FORMATS = {
    "jpg", "jpeg", "png", "webp", "gif", "bmp", "svg", "tif", "tiff",
    "heic", "heif", "avif", "pdf", "ai", "eps", "psd",
}


class CloudinaryStorage:
    """Thin wrapper over the Cloudinary SDK so it can be mocked in tests."""

    def __init__(self, uploader=None, url_builder=None):
        if uploader is None or url_builder is None:
            self._configure()
        if uploader is None:
            import cloudinary.uploader

            uploader = cloudinary.uploader
        if url_builder is None:
            import cloudinary.utils

            url_builder = cloudinary.utils.cloudinary_url
        self.uploader = uploader
        self.url_builder = url_builder

    @staticmethod
    def _configure():
        import cloudinary

        if not (
            settings.CLOUDINARY_CLOUD_NAME
            and settings.CLOUDINARY_API_KEY
            and settings.CLOUDINARY_API_SECRET
        ):
            raise ImproperlyConfigured(
                "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY and "
                "CLOUDINARY_API_SECRET (or CLOUDINARY_URL) are required."
            )
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )

    def upload(self, file_obj, *, public_id, resource_type="image", **options):
        try:
            file_obj.seek(0)
        except (AttributeError, OSError):
            pass
        return self.uploader.upload(
            file_obj,
            public_id=public_id,
            resource_type=resource_type,
            overwrite=False,
            **options,
        )

    def delete(self, public_id, *, resource_type="image", delivery_type="upload"):
        return self.uploader.destroy(
            public_id,
            resource_type=resource_type,
            type=delivery_type,
            invalidate=True,
        )

    def delivery_url(self, public_id, *, resource_type="image", download_file_name=None):
        options = {"resource_type": resource_type, "type": "upload", "secure": True}
        if download_file_name:
            safe_name = _safe_object_name(Path(download_file_name).stem)
            options["flags"] = f"attachment:{safe_name}" if safe_name else "attachment"
        url, _ = self.url_builder(public_id, **options)
        return url


def resource_type_for(file_name):
    return "image" if _extension(file_name) in IMAGE_FORMATS else "raw"


def upload_image(file_obj, folder=None, public_id=None, *, storage=None):
    """Upload an image (downscaled to the configured max size) and return its URL."""

    object_name = _safe_object_name(public_id or uuid4().hex)
    full_public_id = _join(folder or settings.CLOUDINARY_IMAGES_FOLDER, object_name)
    max_side = settings.CLOUDINARY_IMAGE_MAX_DIMENSION
    result = (storage or CloudinaryStorage()).upload(
        file_obj,
        public_id=full_public_id,
        resource_type="image",
        transformation=[{"width": max_side, "height": max_side, "crop": "limit"}],
    )
    return {
        "url": result["secure_url"],
        "key": result["public_id"],
        "public_id": result["public_id"],
        "resource_type": result.get("resource_type", "image"),
    }


def upload_file(file_obj, folder=None, public_id=None, *, storage=None):
    """Upload any file unchanged and return its permanent URL and public id."""

    file_name = getattr(file_obj, "name", "") or "file"
    resource_type = resource_type_for(file_name)
    object_name = _safe_object_name(public_id or uuid4().hex)
    extension = _extension(file_name)
    if resource_type == "raw" and extension and not object_name.lower().endswith(f".{extension}"):
        # Raw assets keep their extension in the public id (and URL).
        object_name = f"{object_name}.{extension}"
    full_public_id = _join(folder or settings.CLOUDINARY_FILES_FOLDER, object_name)

    result = (storage or CloudinaryStorage()).upload(
        file_obj, public_id=full_public_id, resource_type=resource_type
    )
    content_type = (
        getattr(file_obj, "content_type", None)
        or mimetypes.guess_type(file_name)[0]
        or "application/octet-stream"
    )
    return {
        "url": result["secure_url"],
        "key": result["public_id"],
        "public_id": result["public_id"],
        "resource_type": result.get("resource_type", resource_type),
        "format": result.get("format") or extension,
        "bytes": result.get("bytes"),
        "content_type": content_type,
    }


def delete_image(public_id=None, image_url=None, *, resource_type="image", storage=None):
    """Delete an asset by public id, or by one of this cloud's delivery URLs."""

    if image_url and not public_id:
        parsed = parse_url(image_url)
        if parsed is None:
            return {"result": "skipped"}
        public_id = parsed["public_id"]
        resource_type = parsed["resource_type"]
    if not public_id:
        return {"result": "skipped"}

    (storage or CloudinaryStorage()).delete(public_id, resource_type=resource_type)
    return {"result": "deleted", "key": public_id, "public_id": public_id}


def schedule_delete_image(public_id=None, image_url=None, *, resource_type="image"):
    """Delete an asset after the surrounding database transaction commits.

    Failures are logged, never raised: the database change already happened.
    """

    if image_url and not public_id:
        parsed = parse_url(image_url)
        if parsed is None:
            return {"result": "skipped"}
        public_id = parsed["public_id"]
        resource_type = parsed["resource_type"]
    if not public_id:
        return {"result": "skipped"}

    def _delete():
        try:
            delete_image(public_id=public_id, resource_type=resource_type)
        except Exception:  # noqa: BLE001 - cleanup must never break a request
            logger.exception("Could not delete Cloudinary asset %s", public_id)

    transaction.on_commit(_delete)
    return {"result": "scheduled", "key": public_id}


def parse_url(file_url):
    """Return {public_id, resource_type, delivery_type} for this cloud's URLs.

    Returns None for anything that is not a res.cloudinary.com URL of the
    configured cloud.
    """

    cloud_name = settings.CLOUDINARY_CLOUD_NAME
    if not file_url or not cloud_name:
        return None
    parsed = urlparse(str(file_url))
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    if parsed.netloc.lower() != CLOUDINARY_HOST:
        return None

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    # /<cloud>/<resource_type>/<delivery_type>/[transformations/][v123/]<public_id>
    if len(parts) < 4 or parts[0] != cloud_name or parts[1] not in RESOURCE_TYPES:
        return None
    resource_type, delivery_type, rest = parts[1], parts[2], parts[3:]

    version_index = next(
        (index for index, part in enumerate(rest) if re.fullmatch(r"v\d+", part)),
        None,
    )
    if version_index is not None:
        rest = rest[version_index + 1 :]
    if not rest:
        return None

    public_id = "/".join(rest)
    if resource_type != "raw":
        public_id = re.sub(r"\.[A-Za-z0-9]{1,10}$", "", public_id)
    if not public_id:
        return None
    return {
        "public_id": public_id,
        "resource_type": resource_type,
        "delivery_type": delivery_type,
    }


def extract_key(file_url):
    """Public id of one of this cloud's URLs, or None."""

    parsed = parse_url(file_url)
    return parsed["public_id"] if parsed else None


def is_in_folder(public_id, folder):
    folder = (folder or "").strip("/")
    return bool(public_id) and bool(folder) and public_id.startswith(f"{folder}/")


def _extension(file_name):
    suffix = Path(str(file_name or "")).suffix.lower().lstrip(".")
    return suffix if re.fullmatch(r"[a-z0-9]{1,10}", suffix) else ""


def _join(folder, object_name):
    folder = (folder or "").strip("/")
    return f"{folder}/{object_name}" if folder else object_name


def _safe_object_name(value):
    name = Path(str(value)).name
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or uuid4().hex
