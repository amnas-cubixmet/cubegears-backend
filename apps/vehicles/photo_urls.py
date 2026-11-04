from django.urls import path
from .photo_views import (
    VehiclePhotoCollectionView, VehiclePhotoDetailView,
    StaffPhotoFileView, PublicPhotoFileView,
)

urlpatterns = [
    path("vehicles/<uuid:vehicle_id>/photos", VehiclePhotoCollectionView.as_view(), name="vehicle-photo-list"),
    path("jobs/<uuid:job_id>/inspection-photos", VehiclePhotoCollectionView.as_view(), name="job-inspection-photo-list"),
    path("vehicle-photos/<uuid:photo_id>", VehiclePhotoDetailView.as_view(), name="vehicle-photo-update"),
    path("vehicle-photos/<uuid:photo_id>/file", StaffPhotoFileView.as_view(), name="vehicle-photo-file"),
    path("c/<str:token>/photos/<uuid:photo_id>", PublicPhotoFileView.as_view(), name="customer-portal-photo-file"),
]
