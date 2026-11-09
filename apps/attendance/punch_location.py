"""Server-side validation for user-initiated attendance punch GPS readings.

Browser geolocation is client-reported data and cannot prove a physical presence
against a device that intentionally spoofs its coordinates. This guards common
errors and accidental out-of-radius punches, not sophisticated GPS spoofing.
"""
from math import asin, cos, isfinite, radians, sin, sqrt


def _coordinate(location, key, low, high):
    value = location.get(key)
    if isinstance(value, bool) or value is None:
        raise ValueError("A valid device location is required for attendance.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("A valid device location is required for attendance.") from exc
    if not isfinite(number) or number < low or number > high:
        raise ValueError("A valid device location is required for attendance.")
    return number


def _distance_meters(lat1, lon1, lat2, lon2):
    earth_radius = 6371000.0
    lat_delta = radians(lat2 - lat1)
    lon_delta = radians(lon2 - lon1)
    a = sin(lat_delta / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(lon_delta / 2) ** 2
    return 2 * earth_radius * asin(min(1.0, sqrt(a)))


def validate_punch_location(rule, location):
    """Return sanitized coordinates or raise before creating/updating sessions."""
    tracking = bool(rule.location_tracking_enabled or rule.location_required or
                    rule.punch_location_mode == "workshop")
    if not tracking:
        return {}

    if not location:
        if rule.location_required or rule.punch_location_mode == "workshop":
            raise ValueError("Device location is required for this attendance punch.")
        return {}

    if not isinstance(location, dict):
        raise ValueError("A valid device location is required for attendance.")

    latitude = _coordinate(location, "latitude", -90, 90)
    longitude = _coordinate(location, "longitude", -180, 180)
    cleaned = {"latitude": latitude, "longitude": longitude}
    if location.get("accuracy") is not None:
        try:
            accuracy = float(location["accuracy"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid GPS accuracy.") from exc
        if not isfinite(accuracy) or accuracy < 0:
            raise ValueError("Invalid GPS accuracy.")
        cleaned["accuracy"] = accuracy

    if rule.punch_location_mode == "workshop":
        if rule.workshop_latitude is None or rule.workshop_longitude is None:
            raise ValueError("Workshop punch location has not been configured by an administrator.")
        radius = int(rule.punch_radius_meters or 0)
        if not 20 <= radius <= 5000:
            raise ValueError("Workshop punch radius is invalid. Contact your administrator.")
        # High uncertainty could otherwise allow false acceptance at the edge
        # of the configured boundary. Ask the employee for a stronger GPS fix.
        if cleaned.get("accuracy", 0) > radius:
            raise ValueError("Location accuracy is too low. Enable precise location and retry.")
        distance = _distance_meters(
            latitude, longitude, rule.workshop_latitude, rule.workshop_longitude,
        )
        if distance > radius:
            raise ValueError("Outside the permitted workshop punch location.")

    return cleaned
