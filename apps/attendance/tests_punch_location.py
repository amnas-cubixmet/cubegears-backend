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

    def test_selected_place_name_is_saved_as_attendance_rule_metadata(self):
        rule = AttendanceRule()
        serializer = AttendanceRuleSerializer(rule, data={
            'punchLocationMode': 'workshop',
            'workshopLocationName': 'Kozhikode Workshop, Kerala, India',
            'workshopLatitude': 11.25,
            'workshopLongitude': 75.8,
            'punchRadiusMeters': 150,
        },partial=True)
        self.assertTrue(serializer.is_valid(),serializer.errors)
        self.assertEqual(serializer.validated_data['workshop_location_name'],'Kozhikode Workshop, Kerala, India')

    def test_old_rules_without_place_name_remain_compatible(self):
        rule = AttendanceRule()
        self.assertEqual(rule.workshop_location_name,'')
        self.assertEqual(AttendanceRuleSerializer(rule).data['workshopLocationName'],'')


from django.test import TestCase
from apps.companies.models import Company
from apps.saas.models import CompanySetting
from apps.attendance.services import get_attendance_rule, onboarding_punch_policy


class OnboardingPunchRuleSyncTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Geofence Workshop", slug="geofence-workshop",
        )

    def test_existing_saved_onboarding_gps_is_used_even_without_new_setup(self):
        CompanySetting.objects.create(
            company=self.company, category="attendance",
            data={
                "workshopLatitude": "11.250000",
                "workshopLongitude": "75.800000",
                "punchRadiusMeters": 100,
            },
        )
        rule = get_attendance_rule(self.company)
        self.assertEqual(rule.punch_location_mode, "workshop")
        self.assertEqual(rule.punch_radius_meters, 100)
        self.assertTrue(rule.location_required)
        self.assertTrue(rule.location_tracking_enabled)
        with self.assertRaisesRegex(ValueError, "Outside the permitted workshop"):
            validate_punch_location(rule, {
                "latitude": 12.0, "longitude": 75.8, "accuracy": 10,
            })

    def test_existing_manager_policy_is_preserved_without_onboarding_location(self):
        rule = get_attendance_rule(self.company)
        self.assertEqual(rule.punch_location_mode, "anywhere")
        self.assertIsNone(onboarding_punch_policy(self.company))

    def test_corrupt_onboarding_gps_fails_closed_and_never_allows_anywhere(self):
        CompanySetting.objects.create(
            company=self.company, category="attendance",
            data={"workshopLatitude": "wrong", "workshopLongitude": "75.8"},
        )
        rule = get_attendance_rule(self.company)
        self.assertEqual(rule.punch_location_mode, "workshop")
        with self.assertRaises(ValueError):
            validate_punch_location(rule, {"latitude": 11.25, "longitude": 75.8})

    def test_manager_serializer_cannot_replace_saved_owner_geofence(self):
        CompanySetting.objects.create(
            company=self.company, category="attendance",
            data={
                "workshopLatitude": "11.250000",
                "workshopLongitude": "75.800000",
                "punchRadiusMeters": 90,
            },
        )
        rule = get_attendance_rule(self.company)
        serializer = AttendanceRuleSerializer(
            rule,
            data={
                "punchLocationMode": "anywhere",
                "locationRequired": False,
                "workshopLatitude": 19.0,
                "workshopLongitude": 80.0,
                "punchRadiusMeters": 500,
            },
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        changed = serializer.save()
        self.assertEqual(changed.punch_location_mode, "workshop")
        self.assertEqual(changed.workshop_latitude, 11.25)
        self.assertEqual(changed.workshop_longitude, 75.8)
        self.assertEqual(changed.punch_radius_meters, 90)
        self.assertTrue(changed.location_required)
