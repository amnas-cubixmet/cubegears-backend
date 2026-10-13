from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.vehicles.models import Vehicle
from apps.roles.models import Role
from apps.inventory.models import StockItem
from .models import Job
from .india_gst import calculate_tax, state_code


class IndianWorkshopGstTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="GST Test Workshop", slug="gst-workshop-2026",
            gstin="32ABCDE1234F1Z5", state="Kerala",
        )
        self.branch = Branch.objects.create(
            company=self.company, name="Kerala Workshop", code="KL", state="Kerala"
        )
        role = Role.objects.create(
            company=self.company, name="Manager", code="MANAGER",
            permissions=["jobs.view", "jobs.edit"],
        )
        user = User.objects.create(
            company=self.company, branch=self.branch, role=role,
            name="Manager", email="gst-manager@test.local",
        )
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Customer", phone="9876543210", state="Kerala",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch,
            customer=self.customer, registration="KL10AB1234",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch,
            customer=self.customer, vehicle=vehicle,
            job_number="JOB-GST-2026", status="Estimate Pending",
        )
        self.part = StockItem.objects.create(
            company=self.company, branch=self.branch,
            name="Brake pad", sku="BRK-2026", on_hand="2.00",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.url = f"/api/v1/jobs/{self.job.pk}/estimates/quick"

    def quote(self, place="32", *, registered=True, lines=None, discount="0"):
        return {
            "lines": lines or [
                {"description": "Brake service", "type": "Labour", "quantity": "1",
                 "unitPrice": "1000", "taxRate": "18", "hsnSac": "998714"},
                {"description": "Brake pad", "type": "Part", "quantity": "1",
                 "unitPrice": "500", "taxRate": "18", "hsnSac": "8708",
                 "catalogId": str(self.part.pk)},
            ],
            "discount": discount, "taxPercent": "0",
            "gst": {"mode": "gst" if registered else "non_gst", "placeOfSupply": place},
        }

    def test_kerala_intrastate_is_nine_plus_nine(self):
        response = self.client.post(self.url, self.quote(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        pricing = response.data["items"][-1]["_pricing"]["gst"]
        self.assertEqual(pricing["taxMode"], "cgst_sgst")
        self.assertEqual(pricing["cgst"], "135.00")
        self.assertEqual(pricing["sgst"], "135.00")
        self.assertEqual(pricing["igst"], "0")
        self.assertEqual(str(response.data["total"]), "1770.00")
        self.part.refresh_from_db()
        self.assertEqual(self.part.on_hand, Decimal("2.00"))
        self.assertEqual(self.client.get(f"/api/v1/jobs/{self.job.pk}").data["gstInfo"]["supplierState"], "32")

    def test_other_state_uses_igst(self):
        response = self.client.post(self.url, self.quote(place="33"), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        pricing = response.data["items"][-1]["_pricing"]["gst"]
        self.assertEqual(pricing["taxMode"], "igst")
        self.assertEqual(pricing["igst"], "270.00")
        self.assertEqual(pricing["cgst"], "0")
        self.assertEqual(pricing["sgst"], "0")

    def test_discount_is_allocated_before_per_line_gst(self):
        quote = self.quote(discount="150")
        quote["lines"][1]["taxRate"] = "5"  # Classified rate entered by the user.
        response = self.client.post(self.url, quote, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(str(response.data["subtotal"]), "1500.00")
        self.assertEqual(str(response.data["tax"]), "184.50")
        self.assertEqual(str(response.data["total"]), "1534.50")
        self.assertEqual(response.data["items"][0]["discountAllocation"], "100.00")
        self.assertEqual(response.data["items"][1]["discountAllocation"], "50.00")

    def test_registered_company_cannot_disable_gst_and_missing_rate_is_rejected(self):
        self.assertEqual(self.client.post(
            self.url, self.quote(registered=False), format="json",
        ).status_code, 400)
        no_rate = self.quote()
        no_rate["lines"][0].pop("taxRate")
        self.assertEqual(self.client.post(self.url, no_rate, format="json").status_code, 400)

    def test_supplier_registration_and_place_of_supply_are_required(self):
        self.assertEqual(self.client.post(
            self.url, self.quote(place="unknown"), format="json",
        ).status_code, 400)
        self.company.gstin = ""
        self.company.save(update_fields=["gstin"])
        self.assertEqual(self.client.post(
            self.url, self.quote(place="32"), format="json",
        ).status_code, 400)
        plain = self.client.post(self.url, self.quote(registered=False), format="json")
        self.assertEqual(plain.status_code, 201, plain.data)
        self.assertEqual(str(plain.data["tax"]), "0.00")

    def test_numeric_precision_and_state_parsing(self):
        self.assertEqual(state_code("32-Kerala"), "32")
        self.assertEqual(state_code("Tamil Nadu"), "33")
        self.assertEqual(state_code("KL 10"), "")
        invalid = self.quote()
        invalid["lines"][0]["taxRate"] = "NaN"
        self.assertEqual(self.client.post(self.url, invalid, format="json").status_code, 400)
        invalid["lines"][0]["taxRate"] = "18.999"
        self.assertEqual(self.client.post(self.url, invalid, format="json").status_code, 400)

    def test_discount_allocation_works_for_many_one_paisa_lines(self):
        rows = [{"total": "0.01", "taxRate": "18"} for _ in range(100)]
        result = calculate_tax(rows, Decimal("0.40"), "cgst_sgst")
        self.assertEqual(result["taxable"], Decimal("0.60"))
        self.assertEqual(sum(
            Decimal(row["discountAllocation"]) for row in result["lines"]
        ), Decimal("0.40"))

    def test_paise_split_on_one_paisa_tax_amount(self):
        rows = [{"total": "0.06", "taxRate": "18"}]
        intra = calculate_tax(rows, Decimal("0"), "cgst_sgst")
        self.assertEqual(intra["cgst"], Decimal("0.01"))
        self.assertEqual(intra["sgst"], Decimal("0.01"))
        self.assertEqual(intra["total"], Decimal("0.08"))
