from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/treasury/', include('treasury.api_urls')),
    path('api/inventario/', include('inventario.api_urls')),
    path('api/cartera/', include('cartera.api_urls')),
    # Resguardos los comparten Inventario y Cartera: ruta propia con permiso de cualquiera de las dos.
    path('api/resguardos/', include('inventario.resguardos_urls')),
    path("api/auth/", include("accounts.urls")),
    path("api/security/", include("security.api_urls")),
    path("api/notificaciones/", include("notificaciones.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
