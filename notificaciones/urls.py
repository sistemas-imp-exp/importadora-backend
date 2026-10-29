from rest_framework.routers import DefaultRouter

from .views import NotificacionViewSet

router = DefaultRouter()
router.register(r'', NotificacionViewSet, basename='notificacion')

urlpatterns = router.urls
