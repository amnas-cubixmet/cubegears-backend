"""Compatibility import for established inventory/stock procurement endpoints.

The canonical accounting implementation now lives at apps.suppliers.finance_api.
Legacy URLs and imports intentionally resolve to the SAME view classes.
"""
from apps.suppliers.finance_api import *  # noqa: F401,F403
