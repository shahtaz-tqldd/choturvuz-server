from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class UserDetailsViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="details@example.com",
            password="StrongPass123!",
            name="Details User",
        )
        self.client.force_authenticate(self.user)
        self.url = reverse("user-details")

    def test_returns_user_details(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["email"], self.user.email)
        self.assertEqual(response.data["data"]["name"], "Details User")
        self.assertNotIn("workspace_slug", response.data["data"])
