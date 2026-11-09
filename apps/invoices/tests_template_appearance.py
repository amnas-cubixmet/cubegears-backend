"""Appearance validation, tenant boundaries and frozen invoice template resolution."""
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError

from apps.companies.models import Company
from apps.invoices.template_settings import (
    STYLE_DEFAULTS, effective_template_for_invoice, get_template_settings,
    template_for_document, validated_settings,
)
from apps.saas.models import CompanySetting


class InvoiceTemplateValidationTests(SimpleTestCase):
    def test_empty_appearance_uses_no_sample_field_values(self):
        for key in ("customName", "customHeader", "customFooter", "businessName", "termsText"):
            self.assertEqual(STYLE_DEFAULTS[key], "", key)

    def test_named_styles_and_bounded_text(self):
        self.assertEqual(
            validated_settings({"invoiceTemplate": "classic", "accent": "blue", "showTerms": False}),
            {"invoiceTemplate": "classic", "accent": "blue", "showTerms": False},
        )
        for invalid in (
            {"invoiceTemplate": "arbitrary"},
            {"accent": "#ff0000"},
            {"showTerms": "yes"},
            {"unrecognized": "untrusted"},
            {"customFooter": "x" * 2001},
            {"businessLogo": "https://other-site.invalid/logo.svg"},
            {"businessLogo": "data:image/svg+xml;base64,PHN2Zz4="},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                validated_settings(invalid)

    def test_logo_must_be_real_bounded_base64(self):
        with self.assertRaises(ValidationError):
            validated_settings({"businessLogo": "data:image/png;base64,not!base64"})
        import base64
        too_big = base64.b64encode(b"Q" * (250 * 1024 + 1)).decode()
        with self.assertRaises(ValidationError):
            validated_settings({"businessLogo": "data:image/png;base64," + too_big})



    def test_identity_visibility_switches_allow_optional_pan_and_website(self):
        for name in ("showGstin", "showPan", "showWebsite"):
            self.assertIn(name, STYLE_DEFAULTS)
            self.assertIs(STYLE_DEFAULTS[name], True)
            self.assertEqual(validated_settings({name: False})[name], False)
            with self.assertRaises(ValidationError):
                validated_settings({name: "false"})

    def test_logo_validation_allows_saved_onboarding_image_without_public_url(self):
        from io import BytesIO
        import base64
        from PIL import Image
        buffer = BytesIO()
        Image.new("RGB", (240, 120), "white").save(buffer, "PNG")
        value = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        self.assertEqual(validated_settings({"businessLogo": value})["businessLogo"], value)


class CompanyInvoiceTemplateTests(TestCase):
    def setUp(self):
        self.alpha = Company.objects.create(name="Alpha Workshop", slug="alpha-styles")
        self.beta = Company.objects.create(name="Beta Workshop", slug="beta-styles")

    def test_settings_are_scoped_to_company(self):
        CompanySetting.objects.create(
            company=self.alpha, category="document_templates",
            data={"invoiceTemplate": "classic", "accent": "graphite"},
        )
        self.assertEqual(get_template_settings(self.alpha)["invoiceTemplate"], "classic")
        self.assertEqual(get_template_settings(self.beta)["invoiceTemplate"], "modern")
        self.assertEqual(get_template_settings(self.beta)["accent"], "teal")

    def test_finalized_snapshot_is_frozen_from_later_company_changes(self):
        CompanySetting.objects.create(
            company=self.alpha, category="document_templates",
            data={"invoiceTemplate": "classic", "accent": "graphite"},
        )
        captured = template_for_document(self.alpha, "invoice")
        self.assertEqual(captured["selectedTemplate"], "classic")
        CompanySetting.objects.filter(company=self.alpha, category="document_templates").update(
            data={"invoiceTemplate": "minimal", "accent": "teal"},
        )
        finalized = SimpleNamespace(
            company=self.alpha, kind="invoice", template_snapshot=captured,
            finalized_at=object(),
        )
        self.assertEqual(effective_template_for_invoice(finalized)["selectedTemplate"], "classic")
        self.assertEqual(effective_template_for_invoice(finalized)["accent"], "graphite")
        draft = SimpleNamespace(company=self.alpha, kind="invoice", template_snapshot={}, finalized_at=None)
        self.assertEqual(effective_template_for_invoice(draft)["selectedTemplate"], "minimal")

    def test_old_finalized_invoice_retains_legacy_blue_theme(self):
        CompanySetting.objects.create(
            company=self.alpha, category="document_templates",
            data={"invoiceTemplate": "custom", "accent": "teal"},
        )
        legacy = SimpleNamespace(company=self.alpha, kind="invoice", template_snapshot={},
                                 finalized_at=object())
        settings = effective_template_for_invoice(legacy)
        self.assertEqual(settings["selectedTemplate"], STYLE_DEFAULTS["invoiceTemplate"])
        self.assertEqual(settings["accent"], "blue")

    def test_estimate_and_invoice_have_independent_selections(self):
        CompanySetting.objects.create(
            company=self.alpha, category="document_templates",
            data={"invoiceTemplate": "classic", "estimateTemplate": "minimal"},
        )
        self.assertEqual(template_for_document(self.alpha, "invoice")["selectedTemplate"], "classic")
        self.assertEqual(template_for_document(self.alpha, "estimate")["selectedTemplate"], "minimal")

    def test_onboarding_company_details_and_selected_bank_fill_template_and_pdf_snapshot(self):
        from decimal import Decimal
        from apps.inventory.models import WorkshopFinancialAccount
        from apps.invoices.template_settings import company_template_defaults

        self.alpha.name = "Registered Workshop"
        self.alpha.legal_name = "Registered Workshop Private Limited"
        self.alpha.gstin = "32ABCDE1234F1Z5"
        self.alpha.phone = "+919900000000"
        self.alpha.email = "workshop@example.test"
        self.alpha.address = "Main Road, Workshop District"
        self.alpha.state = "Kerala"
        self.alpha.save()
        bank = WorkshopFinancialAccount.objects.create(
            company=self.alpha, name="Business Account", kind="Bank",
            bank_name="State Bank of India", account_number="000123456789",
            ifsc="SBIN0001234", opening_balance=Decimal("1250.00"),
        )
        CompanySetting.objects.create(
            company=self.alpha, category="onboarding",
            data={"bankAccountId": str(bank.pk), "completedSteps": ["company", "bank"]},
        )
        inherited = company_template_defaults(self.alpha)
        self.assertEqual(inherited["businessName"], "Registered Workshop")
        self.assertEqual(inherited["businessLegalName"], "Registered Workshop Private Limited")
        self.assertEqual(inherited["businessGstin"], "32ABCDE1234F1Z5")
        self.assertEqual(inherited["businessPan"], "ABCDE1234F")
        self.assertEqual(inherited["businessState"], "Kerala")
        self.assertEqual(inherited["bankAccountNumber"], "000123456789")
        self.assertEqual(inherited["bankIfsc"], "SBIN0001234")
        self.assertEqual(template_for_document(self.alpha, "invoice")["bankName"], "State Bank of India")

        # Another tenant never gets the linked bank or seller information.
        other = get_template_settings(self.beta)
        self.assertEqual(other["businessName"], self.beta.name)
        self.assertEqual(other["bankAccountNumber"], "")
        self.assertEqual(other["bankIfsc"], "")

        # Explicit template overrides remain, but owners can request a clean
        # refresh from the current workshop profile without losing styling.
        CompanySetting.objects.create(
            company=self.alpha, category="document_templates",
            data={"businessName": "Custom Print Name", "invoiceTemplate": "classic"},
        )
        self.assertEqual(get_template_settings(self.alpha)["businessName"], "Custom Print Name")
        refreshed = get_template_settings(self.alpha, sync_profile=True)
        self.assertEqual(refreshed["businessName"], "Registered Workshop")
        self.assertEqual(refreshed["invoiceTemplate"], "classic")

    def test_uploaded_workshop_logo_is_available_to_templates_without_public_url(self):
        import base64
        from io import BytesIO
        from tempfile import TemporaryDirectory
        from django.core.files.base import ContentFile
        from django.test import override_settings
        from PIL import Image
        from apps.invoices.template_settings import company_logo_for_template

        buffer = BytesIO()
        Image.new("RGB", (120, 80), (32, 83, 151)).save(buffer, "PNG")
        with TemporaryDirectory() as media_dir, override_settings(MEDIA_ROOT=media_dir):
            self.alpha.logo_image.save("workshop.png", ContentFile(buffer.getvalue()), save=True)
            logo = company_logo_for_template(self.alpha)
            self.assertTrue(logo.startswith("data:image/jpeg;base64,"))
            self.assertTrue(base64.b64decode(logo.split(",", 1)[1]))
            self.assertEqual(get_template_settings(self.alpha)["businessLogo"], logo)
            self.assertFalse(CompanySetting.objects.filter(
                company=self.alpha, category="document_templates",
            ).exists())
            self.alpha.logo_image.delete(save=False)

    def test_inherited_branding_is_not_frozen_by_template_settings_put(self):
        from apps.accounts.models import User
        from apps.roles.models import Role
        from rest_framework.test import APIClient

        role = Role.objects.create(company=self.alpha, name="Owner", code="SYNC_OWNER", permissions=["*"])
        owner = User.objects.create_user(
            email="sync-owner@example.test", password="ExamplePassword123!",
            company=self.alpha, role=role, name="Template Owner",
        )
        self.alpha.owner = owner
        self.alpha.save(update_fields=["owner"])
        client = APIClient()
        client.force_authenticate(user=owner)
        url = "/api/v1/settings/document_templates"
        initial = client.get(url)
        self.assertEqual(initial.status_code, 200, initial.data)
        payload = {**initial.data, "accent": "blue", "invoiceTemplate": "classic"}
        saved = client.put(url, payload, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        row = CompanySetting.objects.get(company=self.alpha, category="document_templates")
        self.assertEqual(row.data["invoiceTemplate"], "classic")
        self.assertEqual(row.data["accent"], "blue")
        self.assertNotIn("businessName", row.data)

        self.alpha.name = "Renamed After Onboarding"
        self.alpha.save(update_fields=["name"])
        fetched = client.get(url)
        self.assertEqual(fetched.data["businessName"], "Renamed After Onboarding")
        self.assertEqual(fetched.data["invoiceTemplate"], "classic")
