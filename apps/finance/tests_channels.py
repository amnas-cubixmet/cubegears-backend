from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.finance.channels import account_channel, validate_payment_channel


class FinanceCashBankChannelTests(SimpleTestCase):
    def test_new_channels_have_only_cash_and_bank(self):
        self.assertEqual(account_channel("Cash"), "Cash")
        self.assertEqual(account_channel("Bank"), "Bank")
        self.assertEqual(account_channel("UPI"), "Bank")
        self.assertEqual(account_channel("Wallet"), "Bank")
        self.assertIsNone(account_channel("Other"))

    def test_matching_cash_bank_posting(self):
        for method, kind in (("Cash", "Cash"), ("Bank", "Bank"), ("Bank", "UPI")):
            validate_payment_channel(method, SimpleNamespace(kind=kind))

    def test_mismatched_account_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_payment_channel("Cash", SimpleNamespace(kind="Bank"))
        with self.assertRaises(ValidationError):
            validate_payment_channel("Bank", SimpleNamespace(kind="Cash"))

    def test_historical_payment_channels_remain_readable(self):
        validate_payment_channel("UPI", SimpleNamespace(kind="UPI"))
        validate_payment_channel("Bank Transfer", SimpleNamespace(kind="Bank"))
