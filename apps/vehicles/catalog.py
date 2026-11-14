"""Workshop make/model directory for Job Card creation.

Default choices are read-only examples; workshop-added choices persist in the
database and are never shared across companies.
"""
from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from apps.accounts.permissions import user_has_permission
from .models import Vehicle, VehicleCatalogEntry


DEFAULT_VEHICLE_CATALOG = {
    "Toyota": ["Innova", "Innova Crysta", "Innova Hycross", "Fortuner", "Etios",
               "Glanza", "Urban Cruiser Hyryder", "Corolla", "Camry"],
    "Maruti Suzuki": ["Alto", "Alto K10", "Swift", "Dzire", "Baleno", "Brezza",
                     "Ertiga", "Wagon R", "Celerio", "Fronx", "Grand Vitara", "Jimny"],
    "Hyundai": ["Grand i10 Nios", "i10", "i20", "Creta", "Venue", "Verna", "Aura",
                "Alcazar", "Exter", "Tucson"],
    "Tata": ["Tiago", "Tigor", "Altroz", "Punch", "Nexon", "Harrier", "Safari", "Curvv"],
    "Mahindra": ["Bolero", "Scorpio", "Scorpio N", "XUV300", "XUV3XO", "XUV500",
                 "XUV700", "Thar", "BE 6"],
    "Honda": ["City", "Amaze", "Jazz", "WR-V", "Elevate", "Civic"],
    "Kia": ["Seltos", "Sonet", "Carens", "Syros"],
    "Renault": ["Kwid", "Triber", "Kiger", "Duster"],
    "Nissan": ["Magnite", "Micra", "Sunny", "Terrano", "Kicks"],
    "Volkswagen": ["Polo", "Vento", "Virtus", "Taigun", "Tiguan"],
    "Skoda": ["Rapid", "Kushaq", "Slavia", "Octavia", "Superb"],
    "Ford": ["Figo", "Aspire", "EcoSport", "Endeavour"],
    "MG": ["Hector", "Astor", "Comet EV", "ZS EV", "Windsor EV"],
    "Suzuki": ["Swift", "Baleno", "Ertiga", "Access", "Burgman", "Gixxer"],
    "BMW": ["3 Series", "5 Series", "7 Series", "X1", "X3", "X5"],
    "Mercedes-Benz": ["A-Class", "C-Class", "E-Class", "GLA", "GLC", "GLE"],
    "Land Rover": ["Defender", "Discovery", "Range Rover Evoque", "Range Rover Sport"],
    "Royal Enfield": ["Classic 350", "Bullet 350", "Hunter 350", "Meteor 350", "Himalayan"],
    "Yamaha": ["FZ", "FZ-S", "R15", "MT-15", "Fascino", "RayZR"],
    "Bajaj": ["Pulsar", "Dominar", "Platina", "CT 100", "Avenger"],
    "TVS": ["Apache RTR", "Apache RR 310", "Jupiter", "Ntorq", "Raider"],
    "Hero": ["Splendor Plus", "Passion", "HF Deluxe", "Xtreme", "Xpulse"],
    "Honda Motorcycle": ["Activa", "Dio", "Shine", "Unicorn", "Hornet"],
}


def normalize_catalog_label(value, field, required=False):
    if not isinstance(value, str):
        raise ValidationError({field: "Enter a valid name."})
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValidationError({field: "Control characters are not allowed."})
    normalized = " ".join(value.strip().split())
    if required and not normalized:
        raise ValidationError({field: "Make is required."})
    if len(normalized) > 80:
        raise ValidationError({field: "Maximum 80 characters."})
    return normalized


def merged_vehicle_catalog(*row_groups):
    """Return unique, alphabetized makes/models with case-insensitive matching."""
    output = {}

    def add(make, model=""):
        if not isinstance(make, str) or not make.strip():
            return
        name = " ".join(make.split())
        if len(name) > 80:
            return
        key = name.casefold()
        if key not in output:
            output[key] = {"make": name, "models": {}}
        model_name = " ".join(str(model or "").split())
        if model_name and len(model_name) <= 80:
            output[key]["models"].setdefault(model_name.casefold(), model_name)

    for make, models in DEFAULT_VEHICLE_CATALOG.items():
        add(make)
        for model in models:
            add(make, model)
    for rows in row_groups:
        for make, model in rows:
            add(make, model)

    return [
        {"make": row["make"], "models": sorted(row["models"].values(), key=str.casefold)}
        for row in sorted(output.values(), key=lambda row: row["make"].casefold())
    ]


class VehicleCatalogView(APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def _guard(self, request, *, writing=False):
        codes = ("vehicles.create", "vehicles.edit", "jobs.create") if writing else (
            "vehicles.view", "jobs.view", "jobs.create",
        )
        if not request.user.company_id or not any(
            user_has_permission(request.user, code) for code in codes
        ):
            raise PermissionDenied("You do not have access to vehicle make/model options.")

    def get(self, request):
        self._guard(request)
        company = request.user.company
        saved = VehicleCatalogEntry.objects.filter(company=company).values_list("make", "model")
        used = Vehicle.objects.filter(company=company).values_list("make", "model").distinct()
        result = merged_vehicle_catalog(saved, used)
        response = Response({"makes": result})
        response["Cache-Control"] = "private, no-store"
        return response

    def post(self, request):
        self._guard(request, writing=True)
        if not isinstance(request.data, dict) or set(request.data) - {"make", "model"}:
            raise ValidationError({"message": "Provide only make and optional model."})
        make = normalize_catalog_label(request.data.get("make"), "make", required=True)
        model = normalize_catalog_label(request.data.get("model", ""), "model")
        with transaction.atomic():
            entry, created = VehicleCatalogEntry.objects.get_or_create(
                company=request.user.company,
                make_key=make.casefold(),
                model_key=model.casefold(),
                defaults={
                    "make": make,
                    "model": model,
                    "branch": getattr(request.user, "branch", None),
                },
            )
        result = Response(
            {"make": entry.make, "model": entry.model, "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
        result["Cache-Control"] = "no-store"
        return result
