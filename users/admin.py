from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from users.models.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "full_name", "is_active", "is_blocked", "is_staff", "date_joined")
    list_filter = ("is_active", "is_blocked", "is_staff", "is_superuser")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("-date_joined",)
    readonly_fields = ("token_refresh_date", "date_joined", "last_login")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Status flags", {"fields": ("is_blocked", "token_refresh_date")}),
    )
