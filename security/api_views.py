from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import EsSuperusuario

from .api_serializers import AreaSerializer
from .models import Area


class AreaViewSet(viewsets.ModelViewSet):
    queryset = Area.objects.all().order_by('nombre')
    serializer_class = AreaSerializer
    permission_classes = [IsAuthenticated, EsSuperusuario]
