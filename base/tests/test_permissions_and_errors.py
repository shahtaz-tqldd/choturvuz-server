from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase

User = get_user_model()


class FileUploadPermissionTests(APITestCase):
    def test_anonymous_upload_is_rejected_with_envelope(self):
        response = self.client.post(
            reverse("file-upload"),
            {"file": SimpleUploadedFile("x.txt", b"hello")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["status"], 401)
        self.assertIn("message", response.data)
        self.assertIn("errors", response.data)

    def test_customer_upload_is_rejected(self):
        user = User.objects.create_user(email="c@example.com", password="StrongPass123!")
        self.client.force_authenticate(user)
        response = self.client.post(
            reverse("file-upload"),
            {"file": SimpleUploadedFile("x.txt", b"hello")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 403)


class ErrorEnvelopeTests(APITestCase):
    def test_validation_errors_are_enveloped(self):
        response = self.client.post(reverse("client-order-create"), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data["success"])
        self.assertIn("customer_name", response.data["errors"])
        self.assertTrue(response.data["message"])


class AnnouncementPermissionTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com", password="StrongPass123!", is_staff=True
        )
        self.client.force_authenticate(self.staff)
        self.url = reverse("store-config-update")

    def test_staff_can_update_announcement(self):
        response = self.client.patch(
            self.url,
            {"announcement": {"notify_banner_enabled": True, "notify_banner_text": "Eid sale"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["announcement"]["notify_banner_text"], "Eid sale")

    def test_staff_cannot_update_other_sections(self):
        response = self.client.patch(
            self.url,
            {"commerce": {"delivery_fee_inside_dhaka": "1.00"}},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_superuser_can_update_commerce_thresholds(self):
        admin = User.objects.create_superuser(email="root@example.com", password="StrongPass123!")
        self.client.force_authenticate(admin)
        response = self.client.patch(
            self.url,
            {"commerce": {"free_delivery_threshold": "5000.00", "low_stock_threshold": 5, "allow_cod": False}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        commerce = response.data["data"]["commerce"]
        self.assertEqual(commerce["low_stock_threshold"], 5)
        self.assertFalse(commerce["allow_cod"])
