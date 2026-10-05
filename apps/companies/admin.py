from django.contrib import admin
from .models import Company

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "plan", "currency", "is_active")
    search_fields = ("name", "legal_name", "email", "gstin")
    prepopulated_fields = {"slug": ("name",)}
