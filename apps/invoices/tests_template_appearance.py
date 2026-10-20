"""Appearance validation, tenant boundaries and frozen invoice template resolution."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError

from apps.companies.models import Company
from apps.invoices.template_settings import (
    STYLE_DEFAULTS, effective_template_for_invoice, get_template_settings,
    template_for_document, validated_settings,
)
from apps.saas.models import CompanySetting


class InvoiceTemplateValidationTests(SimpleTestCase):
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
