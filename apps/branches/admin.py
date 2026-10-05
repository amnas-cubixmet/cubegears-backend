from django.contrib import admin
from .models import Branch

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "code", "city", "is_head_office", "is_active")
    list_filter = ("company", "is_head_office", "is_active")
    search_fields = ("name", "code", "city")
