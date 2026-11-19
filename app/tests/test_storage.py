from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from app.services.storage import (
    CloudinaryStorage,
    delete_image,
    extract_key,
    parse_url,
    upload_file,
    upload_image,
)
from base.signals import delete_public_asset_with_record

CLOUD = "https://res.cloudinary.com/demo"


def fake_upload(file_obj, *, public_id, resource_type, **options):
    extension = "" if resource_type == "raw" else ".png"
    return {
        "public_id": public_id,
        "resource_type": resource_type,
        "secure_url": f"{CLOUD}/{resource_type}/upload/v1700000000/{public_id}{extension}",
        "format": "png" if resource_type == "image" else None,
        "bytes": 123,
    }


@override_settings(
    CLOUDINARY_CLOUD_NAME="demo",
    CLOUDINARY_ROOT_FOLDER="choturvuz",
    CLOUDINARY_IMAGES_FOLDER="choturvuz/images",
    CLOUDINARY_FILES_FOLDER="choturvuz/files",
    CLOUDINARY_IMAGE_MAX_DIMENSION=2400,
)
class CloudinaryStorageTests(SimpleTestCase):
    def setUp(self):
        self.uploader = Mock()
        self.uploader.upload.side_effect = fake_upload
        self.storage = CloudinaryStorage(uploader=self.uploader, url_builder=Mock())

    def test_image_upload_uses_folder_prefixed_public_id_and_size_limit(self):
        image = SimpleUploadedFile("logo.png", b"fake", content_type="image/png")

        result = upload_image(
            image, folder="choturvuz/images/products", public_id="product photo",
            storage=self.storage,
        )

        self.assertEqual(result["key"], "choturvuz/images/products/product-photo")
        self.assertEqual(
            result["url"],
            f"{CLOUD}/image/upload/v1700000000/choturvuz/images/products/product-photo.png",
        )
        kwargs = self.uploader.upload.call_args.kwargs
        self.assertEqual(kwargs["resource_type"], "image")
        self.assertFalse(kwargs["overwrite"])
        self.assertEqual(
            kwargs["transformation"], [{"width": 2400, "height": 2400, "crop": "limit"}]
        )

    def test_pdf_is_an_image_asset_and_zip_is_raw_with_extension(self):
        pdf = SimpleUploadedFile("brief.pdf", b"%PDF", content_type="application/pdf")
        result = upload_file(pdf, folder="choturvuz/files", public_id="a1", storage=self.storage)
        self.assertEqual(result["resource_type"], "image")
        self.assertEqual(result["public_id"], "choturvuz/files/a1")
        self.assertEqual(result["content_type"], "application/pdf")

        archive = SimpleUploadedFile("pack.zip", b"PK", content_type="application/zip")
        result = upload_file(archive, folder="choturvuz/files", public_id="a2", storage=self.storage)
        self.assertEqual(result["resource_type"], "raw")
        self.assertEqual(result["public_id"], "choturvuz/files/a2.zip")

    def test_parse_url_handles_versions_transformations_and_raw(self):
        self.assertEqual(
            parse_url(f"{CLOUD}/image/upload/v1712/choturvuz/images/users/a%20b.webp"),
            {
                "public_id": "choturvuz/images/users/a b",
                "resource_type": "image",
                "delivery_type": "upload",
            },
        )
        self.assertEqual(
            extract_key(f"{CLOUD}/image/upload/c_fill,w_300/v1712/choturvuz/x/y.jpg"),
            "choturvuz/x/y",
        )
        self.assertEqual(
            parse_url(f"{CLOUD}/raw/upload/v1/choturvuz/files/pack.zip")["public_id"],
            "choturvuz/files/pack.zip",
        )
        # Other clouds and hosts are never ours.
        self.assertIsNone(parse_url("https://res.cloudinary.com/other/image/upload/v1/a.png"))
        self.assertIsNone(parse_url("https://assets.example.com/image/upload/v1/a.png"))

    def test_delete_by_url_uses_parsed_public_id_and_resource_type(self):
        result = delete_image(
            image_url=f"{CLOUD}/raw/upload/v1/choturvuz/files/pack.zip",
            storage=self.storage,
        )

        self.assertEqual(result["result"], "deleted")
        self.uploader.destroy.assert_called_once_with(
            "choturvuz/files/pack.zip", resource_type="raw", type="upload", invalidate=True
        )

    def test_delete_skips_foreign_urls(self):
        result = delete_image(image_url="https://example.com/a.png", storage=self.storage)
        self.assertEqual(result["result"], "skipped")
        self.uploader.destroy.assert_not_called()

    @patch("base.signals.schedule_delete_image")
    def test_record_delete_signal_schedules_its_public_asset(self, schedule_delete):
        sender = SimpleNamespace(_meta=SimpleNamespace(label_lower="base.storeconfig"))
        instance = SimpleNamespace(logo_url=f"{CLOUD}/image/upload/v1/choturvuz/logo.webp")

        delete_public_asset_with_record(sender, instance)

        schedule_delete.assert_called_once_with(image_url=instance.logo_url)
