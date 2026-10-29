"""
Carga los resguardos pendientes del control del área (hoja "RESUMEN RESG." de
`docs/CARTERA/CONTROL DE RESGUARDO A CLIENTES 2026.xlsx`) como ventas en
resguardo. Solo lo pendiente: el historial de resguardos terminados se queda
en el Excel (sus retiros salieron de lotes que ya no existen).

- Una venta por cliente + nota + fecha; las líneas en 0 se omiten.
- Kilos = lo pendiente según el resumen. Cajas = kilos / peso por caja del
  último lote del producto (redondeado hacia abajo); el proveedor del control
  queda en las notas de la línea.
- Lo fechado hasta el corte de Cartera (30/09) ya está en el saldo inicial y
  no se vuelve a cobrar; lo de octubre sí.
- `--convertir NOTA=FOLIO:KG_VENTA`: la venta se registró como salida de
  cámara normal antes de existir el resguardo (p. ej. 2428/25A=SI9425:2000).
  La venta se carga con los kilos comprados y esa salida pasa a ser su retiro.

Por defecto solo muestra lo que haría; con --aplicar lo guarda. No repite una
venta ya cargada (mismo cliente, nota y fecha).
"""
from decimal import ROUND_DOWN, Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from cartera.models import CostoVenta, VerificacionVenta
from inventario.models import Cliente, Empresa, EntradaDetalle, Producto, Salida, SalidaDetalle
from inventario.resguardos import siguiente_folio

HOJA = 'RESUMEN RESG.'
CLIENTES = {
    'RAYMUNDO PEÑA': 'RAYMUNDO PEÑA',
    'HERAY ACERO MORENO': 'HERAY ACERO MORENO',
    'DE JOSE LUIS UYOA VAZQUEZ': 'JOSE LUIS UYOA VAZQUEZ (CHILIO)',
}
# Cómo escribe el control los productos que no son "talla tipo".
PRODUCTOS = {
    'FILETE TILAPIA DELIPEZ': ('FILETE DELIPEZ', 'FILETE'),
    'FILETE TILAPIA DE PAZ': ('FILETE DE PAZ', 'FILETE'),
    'FILETE DE PAZ': ('FILETE DE PAZ', 'FILETE'),
    'TENTACULO DE CALAMAR': ('TENTACULO', 'CALAMAR'),
}
KG = Decimal('0.001')


def _texto(valor):
    return ' '.join(str(valor).split()).upper() if valor is not None else ''


def _producto(nombre):
    talla, tipo = PRODUCTOS.get(nombre) or nombre.rsplit(' ', 1)
    producto = Producto.objects.filter(talla=talla, tipo=tipo).first()
    if producto is None:
        raise CommandError(f'No existe el producto "{nombre}" ({talla} / {tipo}) en el catálogo.')
    return producto


def leer_pendientes(ruta):
    """{(cliente, nota, fecha): [(producto, proveedor, kilos, precio)]} del resumen."""
    hoja = load_workbook(ruta, data_only=True)[HOJA]
    ventas, cliente = {}, None
    for fila in hoja.iter_rows(values_only=True):
        _, fecha, nota, talla, proveedor, kilos, precio = (tuple(fila) + (None,) * 7)[:7]
        if isinstance(fecha, str) and _texto(fecha).startswith('RESGUARDO'):
            nombre = _texto(fecha)[len('RESGUARDO '):]
            if nombre not in CLIENTES:
                raise CommandError(f'Cliente del resumen sin equivalencia: "{nombre}".')
            cliente = Cliente.objects.get(nombre=CLIENTES[nombre])
            continue
        if not hasattr(fecha, 'year') or not kilos:
            continue
        kilos = Decimal(str(kilos)).quantize(KG)
        if kilos <= 0:
            continue
        nota = _texto(nota).removeprefix('NOTA ')
        ventas.setdefault((cliente, nota, fecha.date()), []).append(
            (_producto(_texto(talla)), _texto(proveedor), kilos, Decimal(str(precio))),
        )
    return ventas


def _cajas(producto, kilos):
    lote = EntradaDetalle.objects.filter(producto=producto, peso_por_caja__gt=0).order_by('-id').first()
    return int((kilos / lote.peso_por_caja).to_integral_value(ROUND_DOWN)) if lote else 0


def _conversiones(valores):
    resultado = {}
    for valor in valores:
        try:
            nota, resto = valor.split('=')
            folio, kilos = resto.split(':')
            resultado[nota.strip().upper()] = (folio.strip().upper(), Decimal(kilos))
        except ValueError:
            raise CommandError(f'--convertir "{valor}": usa NOTA=FOLIO:KG_VENTA.')
    return resultado


class Command(BaseCommand):
    help = 'Carga los resguardos pendientes del control del área (modo prueba salvo --aplicar).'

    def add_arguments(self, parser):
        parser.add_argument('ruta', help='Ruta del .xlsx de control de resguardos.')
        parser.add_argument('--convertir', action='append', default=[], metavar='NOTA=FOLIO:KG_VENTA')
        parser.add_argument('--aplicar', action='store_true', help='Guarda los cambios; sin esto solo los muestra.')

    def handle(self, *args, **opciones):
        ventas = leer_pendientes(opciones['ruta'])
        conversiones = _conversiones(opciones['convertir'])
        empresa = Empresa.objects.get(nombre='IMPORTADORA')
        with transaction.atomic():
            totales = self._cargar(ventas, conversiones, empresa)
            if not opciones['aplicar']:
                transaction.set_rollback(True)
        self.stdout.write(
            f"\n{totales['ventas']} venta(s), {totales['lineas']} línea(s), {totales['kilos']:,.3f} kg pendientes; "
            f"cobro nuevo en Cartera (posterior al corte): ${totales['cobro']:,.2f}; "
            f"{totales['omitidas']} venta(s) ya cargadas."
        )
        self.stdout.write(self.style.SUCCESS('Guardado.') if opciones['aplicar'] else 'Modo prueba: no se guardó nada.')

    def _cargar(self, ventas, conversiones, empresa):
        from cartera.saldos import fecha_corte
        corte = fecha_corte()
        totales = {'ventas': 0, 'lineas': 0, 'kilos': Decimal('0'), 'cobro': Decimal('0'), 'omitidas': 0}
        for (cliente, nota, fecha), lineas in ventas.items():
            if Salida.objects.filter(es_resguardo=True, cliente=cliente, notas=nota, fecha=fecha).exists():
                totales['omitidas'] += 1
                self.stdout.write(f'· Ya cargada: {cliente.nombre} nota {nota} ({fecha}).')
                continue
            retiro = None
            if nota in conversiones:
                folio, kilos_venta = conversiones[nota]
                retiro = Salida.objects.filter(folio_de_salida=folio, cliente=cliente, notas=nota).first()
                if retiro is None or retiro.es_retiro:
                    raise CommandError(f'No hay una salida {folio} de {cliente.nombre} con nota {nota} por convertir.')
                # La venta es del día de la nota, no posterior a su primer retiro.
                fecha = min(fecha, retiro.fecha)
            venta = Salida.objects.create(
                cliente=cliente, fecha=fecha, notas=nota, es_resguardo=True, empresa=empresa,
            )
            cobra = corte is None or fecha > corte
            self.stdout.write(f'\n{cliente.nombre} · nota {nota} · {fecha:%d/%m/%Y}' + ('  [se cobra]' if cobra else ''))
            por_producto = {}
            for producto, proveedor, pendiente, precio in lineas:
                kilos = kilos_venta if retiro else pendiente
                total = (kilos * precio).quantize(Decimal('0.01'))
                cajas = _cajas(producto, kilos)
                por_producto[producto.id] = SalidaDetalle.objects.create(
                    salida=venta, producto=producto, cajas=cajas, total_kilos=kilos,
                    precio_x_kilo=precio, total_venta=total, cajas_resguardo=cajas, kilos_resguardo=kilos,
                    notas=f'Proveedor según control de resguardos: {proveedor}' if proveedor else '',
                )
                totales['lineas'] += 1
                totales['kilos'] += pendiente
                if cobra:
                    totales['cobro'] += total
                self.stdout.write(
                    f'   {producto}: {kilos:,.3f} kg ({cajas} cajas) × ${precio} = ${total:,.2f}'
                    + (f'  · pendiente según control {pendiente:,.3f} kg' if retiro else '')
                )
            if retiro:
                self._convertir(retiro, por_producto)
            totales['ventas'] += 1
        return totales

    def _convertir(self, retiro, por_producto):
        detalles = list(retiro.detalles.all())
        faltan = [str(d.producto) for d in detalles if d.producto_id not in por_producto]
        if faltan:
            raise CommandError(f'{retiro.folio_de_salida}: sin línea de venta para {", ".join(faltan)}.')
        quitadas = VerificacionVenta.objects.filter(salida_detalle__in=detalles).delete()[0]
        quitadas += CostoVenta.objects.filter(salida_detalle__in=detalles).delete()[0]
        retiro.folio_retiro = siguiente_folio()
        retiro.save(update_fields=['folio_retiro', 'modificado'])
        for d in detalles:
            d.retiro_de = por_producto[d.producto_id]
            d.precio_x_kilo = d.total_venta = None
            d.save(update_fields=['retiro_de', 'precio_x_kilo', 'total_venta', 'modificado'])
            self.stdout.write(f'   ↳ {retiro.folio_de_salida} pasa a retiro {retiro.folio_retiro}: {d.producto} {d.total_kilos:,.3f} kg')
        if quitadas:
            self.stdout.write(f'   ↳ se quitaron {quitadas} verificación(es)/costo(s) de Cartera de esa salida (ya no es venta).')
