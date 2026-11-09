"""Protect the brand mark in the platform control-panel templates.

The image assets are supplied separately at static/logo/logo.png; these tests
check that templates are wired to the correct static URL and no longer render
the legacy C/CG monogram in branding positions.
"""
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


class ControlPanelBrandingTests(SimpleTestCase):
    def test_control_panel_templates_share_the_uploaded_logo_and_favicon(self):
        for relative_path in (
            "templates/control_panel/base.html",
            "templates/control_panel/login.html",
            "templates/control_panel/auth_action.html",
            "templates/control_panel/magic_verify.html",
        ):
            with self.subTest(template=relative_path):
                source = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("{% static 'logo/logo.png' %}", source)
                self.assertIn('rel="icon" type="image/png"', source)

    def test_legacy_cg_tiles_are_not_rendered(self):
        base = (ROOT / "templates/control_panel/base.html").read_text(encoding="utf-8")
        login = (ROOT / "templates/control_panel/login.html").read_text(encoding="utf-8")
        auth = (ROOT / "templates/control_panel/auth_action.html").read_text(encoding="utf-8")
        self.assertNotIn('<div class="brand-mark">CG</div>', base)
        self.assertNotIn('class="auth-brand-mark" aria-hidden="true">C</span>', login)
        self.assertNotIn('<span class="art-badge">CG</span>', login)
        self.assertNotIn('<span class="auth-brand-mark">C</span>', auth)
