"""Private vehicle/job inspection photo storage and explicit visibility controls."""
from io import BytesIO
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from apps.accounts.permissions import user_has_permission
from apps.customers.portal_tokens import resolve_link
from apps.jobs.models import Job
from .models import Vehicle, VehicleInspectionPhoto


def _check_permission(request, *permissions):
    if not any(user_has_permission(request.user, permission) for permission in permissions):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("You cannot manage these photos.")


def _photo_data(p):
    return {
        "id": str(p.pk),
        "vehicleId": str(p.vehicle_id),
        "jobId": str(p.job_id) if p.job_id else None,
        "category": p.category,
        "phase": p.phase,
        "caption": p.caption,
        "notes": p.notes,
        "published": p.published,
        "capturedAt": p.captured_at,
        "fileUrl": f"/vehicle-photos/{p.pk}/file",
    }


def _image_bytes(upload):
    if not upload:
        raise ValueError("Capture a photo before uploading.")
    if upload.size is None or upload.size > 10 * 1024 * 1024 or upload.size <= 0:
        raise ValueError("Photos must be smaller than 10 MB.")
    if upload.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("Use a JPEG, PNG or WebP photo.")
    try:
        image = Image.open(BytesIO(upload.read(10 * 1024 * 1024 + 1)))
        if image.format not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError("Unsupported image format.")
        if image.width * image.height > 30_000_000:
            raise ValueError("Photo resolution is too high.")
        image.load()
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((2400, 2400))
        output = BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ValueError("The uploaded file is not a valid supported photo.") from exc


def _file_response(photo):
    if not photo.file:
        raise Http404("Photo unavailable.")
    try:
        handle = photo.file.open("rb")
    except (OSError, FileNotFoundError, ValueError):
        raise Http404("Photo unavailable.")
    result = FileResponse(handle, content_type="image/jpeg")
    result["Cache-Control"] = "private, no-store"
    result["X-Content-Type-Options"] = "nosniff"
    result["Referrer-Policy"] = "no-referrer"
    return result


class VehiclePhotoCollectionView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _get_target(self, request, vehicle_id=None, job_id=None):
        if job_id:
            job = get_object_or_404(
                Job.objects.select_related("vehicle"), id=job_id,
                company=request.user.company,
            )
            return job.vehicle, job
        return get_object_or_404(Vehicle, id=vehicle_id, company=request.user.company), None

    def get(self, request, vehicle_id=None, job_id=None):
        _check_permission(request, "vehicles.view", "jobs.view")
        vehicle, job = self._get_target(request, vehicle_id, job_id)
        photos = VehicleInspectionPhoto.objects.filter(
            company=request.user.company, vehicle=vehicle,
        )
        # Job photo listing includes images linked to this specific job and
        # historical vehicle images without duplicating files.
        if job and request.query_params.get("onlyJob") == "true":
            photos = photos.filter(job=job)
        return Response([_photo_data(p) for p in photos[:250]])

    def post(self, request, vehicle_id=None, job_id=None):
        _check_permission(request, "vehicles.edit", "vehicles.create", "jobs.edit")
        vehicle, job = self._get_target(request, vehicle_id, job_id)
        category = str(request.data.get("category") or "additional")
        phase = str(request.data.get("phase") or "before")
        caption = str(request.data.get("caption") or "").strip()
        notes = str(request.data.get("notes") or "").strip()
        if category not in dict(VehicleInspectionPhoto.CATEGORY_CHOICES):
            return Response({"message":"Invalid photo category."}, status=400)
        if phase not in dict(VehicleInspectionPhoto.PHASE_CHOICES):
            return Response({"message":"Invalid service phase."}, status=400)
        if len(caption) > 255 or len(notes) > 2000:
            return Response({"message":"Photo caption or notes are too long."}, status=400)
        # Only an explicit workshop-staff choice makes a new photo visible.
        # Older clients and vehicle-creation uploads remain private by default.
        raw_published = request.data.get("published", "false")
        if isinstance(raw_published, bool):
            published = raw_published
        elif raw_published in ("true", "false"):
            published = raw_published == "true"
        else:
            return Response({"message": "Published must be true or false."}, status=400)
        # Publishing is a visibility/approval action, not merely photo creation.
        # A create-only staff member may upload privately but not publish.
        if published:
            _check_permission(request, "vehicles.edit", "jobs.edit")
        try:
            contents = _image_bytes(request.FILES.get("image"))
        except ValueError as err:
            return Response({"message":str(err)}, status=400)
        photo = VehicleInspectionPhoto(
            company=request.user.company, branch=request.user.branch,
            vehicle=vehicle, job=job,
            category=category, phase=phase, caption=caption,
            notes=notes, published=published, captured_by=request.user,
        )
        photo.file.save("inspection.jpg", ContentFile(contents), save=False)
        try:
            photo.save()
        except Exception:
            photo.file.delete(save=False)
            raise
        return Response(_photo_data(photo), status=status.HTTP_201_CREATED)


class VehiclePhotoDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, photo_id):
        _check_permission(request, "vehicles.edit", "jobs.edit")
        photo = get_object_or_404(
            VehicleInspectionPhoto, pk=photo_id, company=request.user.company,
        )
        if set(request.data) - {"published", "caption"}:
            return Response({"message":"Only caption and customer visibility can be changed."}, status=400)
        if "published" in request.data:
            flag = request.data["published"]
            if not isinstance(flag, bool):
                return Response({"message":"Published must be a boolean."}, status=400)
            photo.published = flag
        if "caption" in request.data:
            caption = str(request.data["caption"]).strip()
            if len(caption) > 255:
                return Response({"message":"Caption is too long."}, status=400)
            photo.caption = caption
        photo.save(update_fields=["published", "caption", "updated_at"])
        return Response(_photo_data(photo))


class StaffPhotoFileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, photo_id):
        _check_permission(request, "vehicles.view", "jobs.view")
        photo = get_object_or_404(
            VehicleInspectionPhoto, pk=photo_id, company=request.user.company,
        )
        return _file_response(photo)


class PublicPhotoFileView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "customer_portal"

    def get(self, request, token, photo_id):
        access = resolve_link(token)
        if not access:
            raise Http404("Tracking link not found.")
        photo = get_object_or_404(
            VehicleInspectionPhoto,
            pk=photo_id,
            company=access.company,
            vehicle__customer=access.customer,
            published=True,
        )
        return _file_response(photo)
