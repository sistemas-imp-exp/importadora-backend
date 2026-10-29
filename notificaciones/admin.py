from django.contrib import admin

from .models import CanalArea


@admin.register(CanalArea)
class CanalAreaAdmin(admin.ModelAdmin):
    list_display = ['area_a', 'area_b', 'activo']
    list_filter = ['activo']
