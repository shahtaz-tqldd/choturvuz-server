from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APITestCase

from catalog.models import Product
from orders.models import Order
from reviews.models import Review
from reviews.utils.choices import ReviewSource, ReviewStatus

User = get_user_model()


def review_payload(**overrides):
    payload = {
        "customer_name": "Tanvir Hasan",
        "customer_title": "CSE '26, DU",
        "customer_email": "tanvir@example.com",
        "rating": 5,
        "title": "Great jerseys",
        "comment": "Quality was great and delivery was on time.",
    }
    payload.update(overrides)
    return payload


class CustomerReviewTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("client-review-list")

    def test_submitted_review_waits_for_approval(self):
        response = self.client.post(self.url, review_payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["data"]["status"], "PENDING")

        review = Review.objects.get()
        self.assertEqual(review.source, ReviewSource.CUSTOMER)
        # Not on the site until approved.
        self.assertEqual(self.client.get(self.url).data["data"], [])

    def test_public_list_shows_only_approved_without_contact_details(self):
        Review.objects.create(customer_name="A", rating=5, comment="Approved one", status="APPROVED")
        Review.objects.create(customer_name="B", rating=1, comment="Hidden one", status="HIDDEN")
        Review.objects.create(customer_name="C", rating=3, comment="Pending one")

        data = self.client.get(self.url).data["data"]
        self.assertEqual([r["customer_name"] for r in data], ["A"])
        self.assertNotIn("customer_email", data[0])
        self.assertNotIn("customer_phone", data[0])
        self.assertNotIn("status", data[0])

    def test_summary_and_filters(self):
        for rating, featured in ((5, True), (5, False), (4, False), (2, False)):
            Review.objects.create(
                customer_name="X", rating=rating, comment="ok ok ok", status="APPROVED",
                is_featured=featured,
            )
        Review.objects.create(customer_name="X", rating=1, comment="pending", status="PENDING")

        summary = self.client.get(reverse("client-review-summary")).data["data"]
        self.assertEqual(summary["count"], 4)
        self.assertEqual(summary["average_rating"], 4.0)
        self.assertEqual(summary["distribution"], {"5": 2, "4": 1, "3": 0, "2": 1, "1": 0})

        self.assertEqual(len(self.client.get(self.url, {"featured": "true"}).data["data"]), 1)
        self.assertEqual(len(self.client.get(self.url, {"min_rating": "4"}).data["data"]), 3)
        ordered = self.client.get(self.url, {"ordering": "lowest"}).data["data"]
        self.assertEqual(ordered[0]["rating"], 2)

    def test_validation(self):
        self.assertEqual(
            self.client.post(self.url, review_payload(rating=6), format="json").status_code, 400
        )
        self.assertEqual(
            self.client.post(self.url, review_payload(comment=""), format="json").status_code, 400
        )

    def test_optional_product_reference(self):
        product = Product.objects.create(name="Tee", status="ACTIVE", base_price=Decimal("1"))
        response = self.client.post(
            self.url, review_payload(product_id=str(product.pk)), format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        review = Review.objects.get()
        self.assertEqual(review.product, product)
        review.status = ReviewStatus.APPROVED
        review.save()

        data = self.client.get(self.url, {"product": product.slug}).data["data"]
        self.assertEqual(data[0]["product"]["slug"], product.slug)

    def test_order_number_marks_verified_purchase_once(self):
        order = Order.objects.create(
            order_number="ARS-261118-ABC123", customer_name="T", customer_phone="+8801711000000",
            customer_email="tanvir@example.com", address_line="a", city="Dhaka",
        )
        bad = self.client.post(
            self.url,
            review_payload(order_number=order.order_number, customer_email="other@example.com"),
            format="json",
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("order_number", bad.data["errors"])

        ok = self.client.post(
            self.url, review_payload(order_number=order.order_number.lower()), format="json"
        )
        self.assertEqual(ok.status_code, 201, ok.data)
        self.assertTrue(ok.data["data"]["is_verified_purchase"])

        again = self.client.post(
            self.url, review_payload(order_number=order.order_number), format="json"
        )
        self.assertEqual(again.status_code, 400)


class AdminReviewTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="mod@example.com", password="StrongPass123!", is_staff=True
        )
        self.client.force_authenticate(self.admin)
        self.list_url = reverse("admin-review-list")

    def test_staff_review_is_published_immediately(self):
        response = self.client.post(self.list_url, review_payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data["data"]
        self.assertEqual(data["status"], "APPROVED")
        self.assertEqual(data["source"], "STAFF")
        self.assertIsNotNone(data["published_at"])
        self.assertEqual(data["moderated_by"]["email"], "mod@example.com")

        response = self.client.post(
            self.list_url, review_payload(status="PENDING"), format="json"
        )
        self.assertEqual(response.data["data"]["status"], "PENDING")
        self.assertIsNone(response.data["data"]["published_at"])

    def test_approve_hide_feature_and_edit(self):
        review = Review.objects.create(customer_name="A", rating=4, comment="pending review")
        url = reverse("admin-review-detail", args=[review.pk])

        response = self.client.patch(
            url, {"status": "APPROVED", "is_featured": True, "comment": "Edited text"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        review.refresh_from_db()
        self.assertEqual(review.status, ReviewStatus.APPROVED)
        self.assertTrue(review.is_featured)
        self.assertEqual(review.comment, "Edited text")
        first_published = review.published_at

        self.client.patch(url, {"status": "HIDDEN"}, format="json")
        self.client.patch(url, {"status": "APPROVED"}, format="json")
        review.refresh_from_db()
        self.assertEqual(review.published_at, first_published)

        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertFalse(Review.objects.exists())

    def test_list_filters_counts_and_bulk(self):
        pending = [
            Review.objects.create(customer_name=f"P{i}", rating=5, comment="pending review")
            for i in range(3)
        ]
        Review.objects.create(customer_name="H", rating=1, comment="spam", status="HIDDEN")

        response = self.client.get(self.list_url, {"status": "PENDING"})
        self.assertEqual(response.data["meta"]["count"], 3)
        self.assertEqual(
            response.data["meta"]["status_counts"], {"PENDING": 3, "APPROVED": 0, "HIDDEN": 1}
        )
        self.assertIn("customer_email", response.data["data"][0])

        bulk = reverse("admin-review-bulk")
        ids = [str(review.pk) for review in pending[:2]]
        response = self.client.post(bulk, {"ids": ids, "action": "approve"}, format="json")
        self.assertEqual(response.data["data"]["affected"], 2)
        self.assertEqual(Review.objects.filter(status="APPROVED").count(), 2)

        self.client.post(bulk, {"ids": ids, "action": "feature"}, format="json")
        self.assertEqual(Review.objects.filter(is_featured=True).count(), 2)

        self.client.post(bulk, {"ids": ids, "action": "delete"}, format="json")
        self.assertEqual(Review.objects.count(), 2)

    def test_admin_endpoints_need_staff(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.list_url).status_code, 401)
