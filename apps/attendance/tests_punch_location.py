"""Workshop-radius punch location policy and safe defaults."""
from django.test import SimpleTestCase

from apps.attendance.models import AttendanceRule
from apps.attendance.punch_location import validate_punch_location
from apps.attendance.serializers import AttendanceRuleSerializer


class PunchLocationPolicyTests(SimpleTestCase):
    def rule(self, **kwargs):
        return AttendanceRule(
            location_tracking_enabled=True, location_required=True,
            punch_location_mode="workshop",
            workshop_latitude=11.25, workshop_longitude=75.8,
            punch_radius_meters=100, **kwargs,
        )

    def test_default_rule_keeps_existing_punches_working_without_location(self):
        rule = AttendanceRule()
        self.assertEqual(rule.punch_location_mode, "anywhere")
        self.assertEqual(validate_punch_location(rule, {}), {})

    def test_nearby_coordinates_are_accepted_and_cleaned(self):
        rule = self.rule()
        location = validate_punch_location(rule, {
            "latitude": 11.2501, "longitude": 75.8001,
            "accuracy": 12, "untrustedExtra": "not saved",
        })
        self.assertEqual(set(location), {"latitude", "longitude", "accuracy"})
        self.assertEqual(location["accuracy"], 12)

    def test_outside_workshop_and_poor_accuracy_are_rejected(self):
        rule = self.rule()
        with self.assertRaisesRegex(ValueError, "Outside the permitted workshop"):
            validate_punch_location(rule, {"latitude": 11.255, "longitude": 75.8, "accuracy": 10})
        with self.assertRaisesRegex(ValueError, "accuracy is too low"):
            validate_punch_location(rule, {"latitude": 11.25, "longitude": 75.8, "accuracy": 250})

    def test_invalid_or_missing_coordinates_rejected_when_required(self):
        rule = self.rule()
        for item in [{}, {"latitude": None, "longitude": 75.8},
                     {"latitude": "NaN", "longitude": 75.8},
                     {"latitude": True, "longitude": 75.8},
                     {"latitude": 95, "longitude": 75.8}]:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    validate_punch_location(rule, item)

    def test_optional_tracking_without_location_remains_non_blocking(self):
        rule = AttendanceRule(location_tracking_enabled=True)
        self.assertEqual(validate_punch_location(rule, {}), {})

    def test_serializer_requires_coordinates_for_workshop_mode(self):
        rule = AttendanceRule()
        serializer = AttendanceRuleSerializer(
            rule, data={"punchLocationMode": "workshop"}, partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("workshopLatitude", serializer.errors)

    def test_workshop_mode_always_enables_required_location(self):
        rule = AttendanceRule()
        serializer = AttendanceRuleSerializer(
            rule, data={
                "punchLocationMode": "workshop",
                "workshopLatitude": 11.25, "workshopLongitude": 75.8,
                "punchRadiusMeters": 100,
                "locationRequired": False, "locationTrackingEnabled": False,
            }, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertTrue(serializer.validated_data["location_required"])
        self.assertTrue(serializer.validated_data["location_tracking_enabled"])
