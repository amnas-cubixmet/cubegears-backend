"""Stock directory, item editor, low-stock filtering and tenant safety UI checks."""
from decimal import Decimal

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company
from apps.inventory.models import StockItem


class StockManagementUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="stock-ui-admin@example.com",
            name="Stock UI Admin",
            password="Stock-secure-test-2026!",
        )
        cls.alpha = Company.objects.create(name="Alpha Workshop", slug="stock-ui-alpha")
        cls.beta = Company.objects.create(name="Beta Workshop", slug="stock-ui-beta")
        cls.healthy = StockItem.objects.create(
            company=cls.alpha, name="Oil Filter", sku="OIL-100", status="Active",
            on_hand=Decimal("15.00"), reserved=Decimal("2.00"),
            minimum_stock=Decimal("3.00"), selling_price=Decimal("400.00"),
        )
        cls.low = StockItem.objects.create(
            company=cls.alpha, name="Brake Pad", sku="BRAKE-200", status="Active",
            on_hand=Decimal("5.00"), reserved=Decimal("2.00"),
            minimum_stock=Decimal("4.00"), selling_price=Decimal("990.00"),
        )
        cls.out = StockItem.objects.create(
            company=cls.alpha, name="Wiper Blade", sku="WIPER-300", status="Inactive",
            on_hand=Decimal("2.00"), reserved=Decimal("2.00"),
            minimum_stock=Decimal("3.00"), selling_price=Decimal("250.00"),
        )
        cls.other = StockItem.objects.create(
            company=cls.beta, name="Spark Plug", sku="SPARK-400", status="Active",
            on_hand=Decimal("6.00"), reserved=Decimal("0.00"),
            minimum_stock=Decimal("1.00"), selling_price=Decimal("150.00"),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_stock_directory_displays_real_kpis_and_quantities(self):
        response = self.client.get(reverse("panel:stock"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock &amp; Inventory")
        self.assertContains(response, "Low Stock Items")
        self.assertContains(response, "Out of Stock")
        self.assertContains(response, 'class="user-stat-value"', count=4)
        self.assertContains(response, "stock-ui.css")
        self.assertContains(response, "Oil Filter")
        self.assertContains(response, "Brake Pad")
        self.assertContains(response, "Wiper Blade")
        self.assertContains(response, "Spark Plug")
        self.assertContains(response, "13.00")
        self.assertIsNotNone(finders.find("control_panel/stock-ui.css"))
        self.assertEqual(response.context["total_items"], 4)
        self.assertEqual(response.context["low_stock_count"], 2)
        self.assertEqual(response.context["out_of_stock_count"], 1)
        self.assertEqual(response.context["stock_companies"], 2)

    def test_search_company_status_and_stock_level_filters_combine(self):
        url = reverse("panel:stock")
        result = self.client.get(url, {
            "q": "Brake", "company": str(self.alpha.pk),
            "status": "Active", "level": "low",
        })
        self.assertEqual(result.context["rows"].paginator.count, 1)
        self.assertContains(result, "Brake Pad")
        self.assertNotContains(result, "Wiper Blade")
        self.assertEqual(result.context["total_items"], 4)

        out = self.client.get(url, {"level": "out"})
        self.assertEqual(out.context["rows"].paginator.count, 1)
        self.assertContains(out, "Wiper Blade")

        healthy = self.client.get(url, {"level": "healthy"})
        self.assertEqual(healthy.context["rows"].paginator.count, 2)
        self.assertContains(healthy, "Oil Filter")
        self.assertContains(healthy, "Spark Plug")

        invalid = self.client.get(url, {
            "status": "unknown", "level": "invalid",
        })
        self.assertEqual(invalid.context["rows"].paginator.count, 4)
        self.assertEqual(invalid.context["status"], "")
        self.assertEqual(invalid.context["level"], "")

    def test_add_edit_form_keeps_all_original_stock_fields(self):
        create = self.client.get(reverse("panel:stock-create"))
        self.assertTemplateUsed(create, "control_panel/stock_form.html")
        self.assertContains(create, "Item Information")
        self.assertContains(create, "Prices &amp; Tax")
        self.assertContains(create, "Quantities &amp; Reorder Levels")
        self.assertContains(create, "Stock movement note")
        self.assertContains(create, 'name="csrfmiddlewaretoken"')
        for name in (
            "company", "branch", "name", "sku", "barcode", "category",
            "brand", "compatible_vehicle", "unit", "cost_price",
            "selling_price", "on_hand", "reserved", "minimum_stock",
            "reorder_level", "rack", "supplier", "tax", "hsn_code", "status",
        ):
            self.assertContains(create, f'name="{name}"')
        edit = self.client.get(reverse("panel:stock-edit", args=[self.healthy.pk]))
        self.assertTemplateUsed(edit, "control_panel/stock_form.html")
        self.assertContains(edit, "Save Changes")

    def test_stock_create_and_edit_preserve_recorded_quantities(self):
        payload = {
            "company": str(self.alpha.pk), "name": "Air Filter",
            "sku": "AIR-500", "unit": "Piece",
            "compatible_vehicle": "Universal", "cost_price": "90.00",
            "selling_price": "150.00", "on_hand": "10.00",
            "reserved": "3.00", "minimum_stock": "4.00",
            "reorder_level": "5.00", "tax": "0.00", "status": "Active",
        }
        self.assertRedirects(
            self.client.post(reverse("panel:stock-create"), payload),
            reverse("panel:stock"),
        )
        item = StockItem.objects.get(company=self.alpha, sku="AIR-500")
        self.assertEqual(item.available_quantity, Decimal("7.00"))
        payload["on_hand"] = "12.00"
        self.assertRedirects(
            self.client.post(reverse("panel:stock-edit", args=[item.pk]), payload),
            reverse("panel:stock"),
        )
        item.refresh_from_db()
        self.assertEqual(item.available_quantity, Decimal("9.00"))

    def test_cross_company_category_assignment_cannot_succeed(self):
        from apps.inventory.models import StockCategory

        other_category = StockCategory.objects.create(company=self.beta, name="Foreign")
        result = self.client.post(reverse("panel:stock-create"), {
            "company": str(self.alpha.pk),
            "name": "Mismatched Part", "sku": "BAD-600",
            "category": str(other_category.pk), "unit": "Piece",
            "compatible_vehicle": "Universal", "cost_price": "0",
            "selling_price": "30", "on_hand": "1",
            "reserved": "0", "minimum_stock": "0",
            "reorder_level": "0", "tax": "0", "status": "Active",
        })
        self.assertEqual(result.status_code, 200)
        self.assertFalse(StockItem.objects.filter(sku="BAD-600").exists())
        self.assertTrue(result.context["form"].errors)

    def test_stock_detail_shows_available_quantity_and_login_required(self):
        detail = self.client.get(reverse("panel:stock-detail", args=[self.low.pk]))
        self.assertContains(detail, 'class="panel-card stock-profile-header"')
        self.assertContains(detail, "Brake Pad")
        self.assertContains(detail, "3.00")
        self.assertContains(detail, "Low Stock")
        self.client.logout()
        self.assertEqual(self.client.get(reverse("panel:stock")).status_code, 302)
        self.assertEqual(self.client.get(reverse("panel:stock-create")).status_code, 302)
        self.assertEqual(self.client.get(reverse("panel:stock-detail", args=[self.low.pk])).status_code, 302)

    def test_other_resource_form_template_is_unchanged(self):
        self.assertTemplateUsed(
            self.client.get(reverse("panel:invoice-create")), "control_panel/invoice_form.html"
        )
