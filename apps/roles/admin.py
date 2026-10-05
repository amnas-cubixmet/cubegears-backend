from django.contrib import admin
from .models import Role

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "company", "is_system", "is_active")
    list_filter = ("is_system", "is_active")
    search_fields = ("name", "code")
