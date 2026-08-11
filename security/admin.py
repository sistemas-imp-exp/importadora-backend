from django.contrib import admin

from .models import Area, UsuarioArea


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "activo")
    list_filter = ("activo",)
    search_fields = ("codigo", "nombre")


@admin.register(UsuarioArea)
class UsuarioAreaAdmin(admin.ModelAdmin):
    list_display = ("usuario", "area")
    search_fields = ("usuario__username", "area__nombre", "area__codigo")