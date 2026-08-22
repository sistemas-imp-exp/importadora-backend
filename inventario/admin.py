from django.contrib import admin

from inventario.models import (
    Camara,
    Cliente,
    Empresa,
    Entrada,
    EntradaDetalle,
    LoteGeneral,
    MovimientoCamara,
    Producto,
    Proveedor,
    Salida,
    SalidaDetalle,
)

admin.site.register(Empresa)
admin.site.register(Camara)
admin.site.register(Proveedor)
admin.site.register(Cliente)
admin.site.register(Producto)
admin.site.register(Entrada)
admin.site.register(LoteGeneral)
admin.site.register(EntradaDetalle)
admin.site.register(Salida)
admin.site.register(SalidaDetalle)
admin.site.register(MovimientoCamara)
