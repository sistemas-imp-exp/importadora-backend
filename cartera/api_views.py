from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from inventario.models import Cliente, Empresa
from security.permissions import AreaCartera
from treasury.models import MovimientoTesoreria

from .api_serializers import (
    AjusteClienteSerializer,
    ClienteCarteraSerializer,
    EmpresaSerializer,
    IdentificarPagoSerializer,
    IngresoTesoreriaSerializer,
    PagoClienteSerializer,
    SaldoInicialSerializer,
    TraspasoSerializer,
    VerificarVentaSerializer,
    ventas_agrupadas,
)
from .avisos import avisar_diferencias
from .estado_cuenta import estado_de_cuenta, saldos_de_clientes
from .models import AjusteCliente, PagoCliente, SaldoInicialCliente, VerificacionVenta
from .ventas import PENDIENTE, SIN_PRECIO, estado, filtro_empresa, huella, importe, lineas_venta
from .reportes import (
    construir_excel_estado_cuenta,
    construir_excel_pagos,
    construir_excel_saldos,
    construir_pdf_estado_cuenta,
    construir_pdf_pagos,
    construir_pdf_saldos,
)
from .saldos import fecha_corte, por_identificar, saldos_por_cliente, ultimos_pagos

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _verdadero(valor):
    return (valor or '').lower() in ('1', 'true', 'si', 'sí')


def _dinero(valor):
    return f'{valor:.2f}'


def _descarga(contenido, tipo, nombre):
    respuesta = HttpResponse(contenido, content_type=tipo)
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre}"'
    return respuesta


def _excel(libro, nombre):
    respuesta = HttpResponse(content_type=XLSX)
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre}"'
    libro.save(respuesta)
    return respuesta


class EmpresaCarteraViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Empresas del grupo (columnas de saldo de Cartera)."""
    permission_classes = [AreaCartera]
    queryset = Empresa.objects.order_by('nombre')
    serializer_class = EmpresaSerializer
    pagination_class = None


class ClienteCarteraViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Clientes (catálogo de Inventario) con datos de cobranza y saldo por
    empresa. Sin borrado: se desactivan.
    """
    permission_classes = [AreaCartera]
    serializer_class = ClienteCarteraSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = Cliente.objects.select_related('cartera').order_by('nombre')
        texto = (self.request.query_params.get('q') or '').strip()
        if texto:
            queryset = queryset.filter(nombre__icontains=texto)
        activo = self.request.query_params.get('activo')
        if activo is not None:
            queryset = queryset.filter(activo=_verdadero(activo))
        return queryset

    def _con_saldos(self, clientes, datos):
        """Agrega a cada cliente su saldo por empresa, el conjunto y el último pago."""
        ids = [c.id for c in clientes]
        saldos = saldos_por_cliente(clientes=ids)
        ultimos = ultimos_pagos(clientes=ids)
        empresas = list(Empresa.objects.values_list('id', flat=True))
        # Líneas de venta por revisar contra la nota (pendientes y con diferencia) por cliente.
        por_revisar = {}
        for linea in lineas_venta(clientes=ids):
            clave = estado(linea)[0]
            conteo = por_revisar.setdefault(linea.salida.cliente_id, {PENDIENTE: 0, VerificacionVenta.DIFERENCIA: 0, SIN_PRECIO: 0})
            if clave in conteo:
                conteo[clave] += 1
        for cliente, fila in zip(clientes, datos):
            conteo = por_revisar.get(cliente.id, {})
            fila['ventas_por_verificar'] = conteo.get(PENDIENTE, 0)
            fila['ventas_con_diferencia'] = conteo.get(VerificacionVenta.DIFERENCIA, 0) + conteo.get(SIN_PRECIO, 0)
            por_empresa = {str(e): _dinero(saldos[cliente.id][e]) for e in empresas}
            fila['saldos'] = por_empresa
            fila['saldo_conjunto'] = _dinero(sum(saldos[cliente.id][e] for e in empresas))
            ultimo = ultimos.get(cliente.id)
            perfil = getattr(cliente, 'cartera', None)
            if ultimo is None and perfil is not None and perfil.pago_previo_fecha:
                ultimo = (perfil.pago_previo_fecha, perfil.pago_previo_monto)
            fila['ultimo_pago'] = (
                {'fecha': ultimo[0].isoformat(), 'monto': _dinero(ultimo[1]) if ultimo[1] is not None else None}
                if ultimo else None
            )
        return datos

    def list(self, request, *args, **kwargs):
        clientes = list(self.get_queryset())
        datos = self._con_saldos(clientes, self.get_serializer(clientes, many=True).data)
        if _verdadero(request.query_params.get('con_saldo')):
            datos = [d for d in datos if any(v != '0.00' for v in d['saldos'].values())]
        return Response(datos)

    def retrieve(self, request, *args, **kwargs):
        cliente = self.get_object()
        return Response(self._con_saldos([cliente], [self.get_serializer(cliente).data])[0])

    def _empresa_filtro(self):
        empresa_id = self.request.query_params.get('empresa')
        return Empresa.objects.filter(pk=empresa_id).first() if empresa_id else None

    @action(detail=True, methods=['get'], url_path='estado-cuenta')
    def estado_cuenta(self, request, pk=None):
        cliente = self.get_object()
        renglones = estado_de_cuenta(cliente, self._empresa_filtro())
        return Response([
            {
                'fecha': r.fecha.isoformat(), 'tipo': r.tipo, 'empresa': r.empresa, 'referencia': r.referencia,
                'concepto': r.concepto, 'cargo': _dinero(r.cargo), 'abono': _dinero(r.abono),
                'saldo': _dinero(r.saldo) if r.saldo is not None else None,
                'cancelado': r.cancelado, 'motivo_cancelacion': r.motivo_cancelacion, 'id': r.id,
                'detalle': [
                    {**d, 'kilos': _dinero(d['kilos']),
                     'precio_kilo': _dinero(d['precio_kilo']) if d['precio_kilo'] is not None else None,
                     'importe': _dinero(d['importe']) if d['importe'] is not None else None}
                    for d in r.detalle
                ],
            }
            for r in renglones
        ])

    @action(detail=True, methods=['get'])
    def ventas(self, request, pk=None):
        """Salidas de Inventario a este cliente, con sus líneas y verificación (filtros: estado, empresa, desde, hasta)."""
        cliente = self.get_object()
        params = request.query_params
        lineas = lineas_venta(clientes=[cliente.id])
        desde = parse_date(params.get('desde') or '')
        if desde:
            lineas = lineas.filter(salida__fecha__gte=desde)
        hasta = parse_date(params.get('hasta') or '')
        if hasta:
            lineas = lineas.filter(salida__fecha__lte=hasta)
        if params.get('empresa'):
            lineas = lineas.filter(filtro_empresa(params['empresa']))
        filtro_estado = params.get('estado')
        if filtro_estado:
            lineas = [linea for linea in lineas if estado(linea)[0] == filtro_estado]
        return Response(ventas_agrupadas(lineas))

    @action(detail=True, methods=['get'], url_path='estado-cuenta/pdf')
    def estado_cuenta_pdf(self, request, pk=None):
        cliente = self.get_object()
        empresa = self._empresa_filtro()
        saldos = saldos_por_cliente(clientes=[cliente.id])[cliente.id]
        empresas = [empresa] if empresa else list(Empresa.objects.order_by('nombre'))
        resumen = [(e.nombre, saldos[e.id]) for e in empresas]
        if not empresa:
            resumen.append(('Conjunto', sum(saldos[e.id] for e in empresas)))
        buffer = construir_pdf_estado_cuenta(
            cliente, getattr(cliente, 'cartera', None), estado_de_cuenta(cliente, empresa), resumen, empresa,
        )
        return _descarga(buffer.getvalue(), 'application/pdf', f'estado_cuenta_cliente_{cliente.id}.pdf')

    @action(detail=True, methods=['get'], url_path='estado-cuenta/excel')
    def estado_cuenta_excel(self, request, pk=None):
        cliente = self.get_object()
        libro = construir_excel_estado_cuenta(cliente, estado_de_cuenta(cliente, self._empresa_filtro()))
        return _excel(libro, f'estado_cuenta_cliente_{cliente.id}.xlsx')


class MovimientoCarteraViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Base de pagos y ajustes: filtros comunes y cancelación (no se borran)."""
    permission_classes = [AreaCartera]
    pagination_class = None
    modelo = None

    def get_queryset(self):
        queryset = self.modelo.objects.select_related(
            'cliente', 'empresa', 'usuario', 'editado_por', 'usuario_cancelacion',
        ).order_by('-fecha', '-creado')
        params = self.request.query_params
        for campo in ('cliente', 'empresa'):
            if params.get(campo):
                queryset = queryset.filter(**{f'{campo}_id': params[campo]})
        desde = parse_date(params.get('desde') or '')
        if desde:
            queryset = queryset.filter(fecha__gte=desde)
        hasta = parse_date(params.get('hasta') or '')
        if hasta:
            queryset = queryset.filter(fecha__lte=hasta)
        estado = params.get('estado')
        if estado == 'activo':
            queryset = queryset.filter(cancelado=False)
        elif estado == 'cancelado':
            queryset = queryset.filter(cancelado=True)
        return queryset

    def _cancelar(self, movimientos, motivo):
        try:
            with transaction.atomic():
                for movimiento in movimientos:
                    movimiento.marcar_cancelado(self.request.user, motivo)
                    movimiento.save()
        except DjangoValidationError as error:
            raise DRFValidationError(error.messages)

    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        movimiento = self.get_object()
        self._cancelar([movimiento], request.data.get('motivo', ''))
        return Response(self.get_serializer(movimiento).data)


class PagoClienteViewSet(MovimientoCarteraViewSet):
    modelo = PagoCliente
    serializer_class = PagoClienteSerializer

    def get_queryset(self):
        queryset = super().get_queryset().select_related('ingreso_tesoreria')
        tipo = self.request.query_params.get('tipo')
        if tipo:
            queryset = queryset.filter(tipo=tipo)
        identificar = self.request.query_params.get('por_identificar')
        if identificar is not None:
            queryset = queryset.filter(cliente__isnull=_verdadero(identificar))
        texto = (self.request.query_params.get('q') or '').strip()
        if texto:
            queryset = queryset.filter(
                Q(cliente__nombre__icontains=texto) | Q(referencia__icontains=texto) | Q(notas__icontains=texto)
            )
        return queryset

    def _filtros_texto(self):
        """Los filtros aplicados, en texto, para el encabezado del PDF y el pie del Excel."""
        params = self.request.query_params
        textos = []
        desde, hasta = parse_date(params.get('desde') or ''), parse_date(params.get('hasta') or '')
        if desde or hasta:
            textos.append(f"Periodo: {desde.strftime('%d/%m/%Y') if desde else 'inicio'} al "
                          f"{hasta.strftime('%d/%m/%Y') if hasta else 'hoy'}")
        if params.get('por_identificar') is not None:
            textos.append('Pagos por identificar' if _verdadero(params['por_identificar']) else 'Pagos identificados')
        if params.get('tipo'):
            textos.append(f"Tipo: {dict(PagoCliente.TIPO_CHOICES).get(params['tipo'], params['tipo'])}")
        if params.get('empresa'):
            empresa = Empresa.objects.filter(pk=params['empresa']).first()
            textos.append(f"Empresa: {empresa.nombre if empresa else params['empresa']}")
        if params.get('estado') in ('activo', 'cancelado'):
            textos.append('Solo activos' if params['estado'] == 'activo' else 'Solo cancelados')
        if (params.get('q') or '').strip():
            textos.append(f"Búsqueda: {params['q'].strip()}")
        return textos

    @action(detail=False, methods=['get'])
    def pdf(self, request):
        """Los pagos de la pantalla (mismos filtros) en PDF."""
        contenido = construir_pdf_pagos(list(self.get_queryset()), self._filtros_texto()).getvalue()
        return _descarga(contenido, 'application/pdf', 'pagos_clientes.pdf')

    @action(detail=False, methods=['get'])
    def excel(self, request):
        """Los pagos de la pantalla (mismos filtros) en Excel."""
        return _excel(construir_excel_pagos(list(self.get_queryset()), self._filtros_texto()), 'pagos_clientes.xlsx')

    @action(detail=True, methods=['post'])
    def identificar(self, request, pk=None):
        """Asigna el cliente a un pago por identificar: desde ahí baja su saldo con la fecha del pago."""
        pago = self.get_object()
        if pago.cancelado:
            raise DRFValidationError('El pago está cancelado.')
        if pago.cliente_id is not None:
            raise DRFValidationError('Este pago ya está identificado.')
        serializer = IdentificarPagoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pago.cliente = serializer.validated_data['cliente']
        pago.identificado_por = request.user
        pago.identificado_en = timezone.now()
        pago.save()
        return Response(self.get_serializer(pago).data)


class AjusteClienteViewSet(MovimientoCarteraViewSet):
    modelo = AjusteCliente
    serializer_class = AjusteClienteSerializer

    def get_queryset(self):
        queryset = super().get_queryset().select_related('contraparte__empresa')
        texto = (self.request.query_params.get('q') or '').strip()
        if texto:
            queryset = queryset.filter(Q(cliente__nombre__icontains=texto) | Q(concepto__icontains=texto))
        return queryset

    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        # Un traspaso se cancela completo: las dos empresas a la vez.
        ajuste = self.get_object()
        movimientos = [ajuste] + ([ajuste.contraparte] if ajuste.contraparte_id else [])
        self._cancelar(movimientos, request.data.get('motivo', ''))
        ajuste.refresh_from_db()
        return Response(self.get_serializer(ajuste).data)

    @action(detail=False, methods=['post'])
    def traspaso(self, request):
        serializer = TraspasoSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        abono, cargo = serializer.save()
        return Response(self.get_serializer([abono, cargo], many=True).data, status=status.HTTP_201_CREATED)


class VerificarVentasView(APIView):
    """
    Cartera revisa líneas de salida contra la nota en papel: verificadas, con
    diferencia (Inventario ve el comentario y corrige la salida) o de vuelta a
    pendientes. Solo líneas de venta (salida a cliente posterior al corte).
    """
    permission_classes = [AreaCartera]

    def post(self, request):
        serializer = VerificarVentaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data
        lineas = list(lineas_venta().filter(pk__in=datos['lineas']))
        if len(lineas) != len(set(datos['lineas'])):
            raise DRFValidationError('Alguna línea no es una venta a cliente posterior al corte.')
        if datos['estado'] != 'pendiente' and any(importe(linea) is None for linea in lineas):
            raise DRFValidationError('Hay líneas sin precio: Inventario debe capturarlo antes de verificarlas.')
        with transaction.atomic():
            for linea in lineas:
                if datos['estado'] == 'pendiente':
                    VerificacionVenta.objects.filter(salida_detalle=linea).delete()
                    continue
                VerificacionVenta.objects.update_or_create(
                    salida_detalle=linea,
                    defaults={'estado': datos['estado'], 'comentario': datos['comentario'],
                              'huella': huella(linea), 'usuario': request.user},
                )
            if datos['estado'] == VerificacionVenta.DIFERENCIA:
                avisar_diferencias(lineas, datos['comentario'], request.user)
        return Response({'lineas': len(lineas), 'estado': datos['estado']})


class SaldoInicialViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Saldos de arranque (se cargan con `manage.py cargar_cartera_inicial`)."""
    permission_classes = [AreaCartera]
    serializer_class = SaldoInicialSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = SaldoInicialCliente.objects.select_related('cliente', 'empresa').order_by('cliente__nombre')
        if self.request.query_params.get('cliente'):
            queryset = queryset.filter(cliente_id=self.request.query_params['cliente'])
        return queryset


class IngresosTesoreriaView(APIView):
    """
    Ingresos de caja de Tesorería en pesos, activos y aún sin pago de Cartera:
    de aquí se toma el efectivo para no recapturarlo. Solo lectura.
    """
    permission_classes = [AreaCartera]

    def get(self, request):
        params = request.query_params
        usados = PagoCliente.objects.filter(cancelado=False, ingreso_tesoreria__isnull=False)
        if params.get('pago'):  # al editar un pago, su propio ingreso sigue disponible
            usados = usados.exclude(pk=params['pago'])
        queryset = (
            MovimientoTesoreria.objects.filter(tipo=MovimientoTesoreria.INGRESO, cancelado=False,
                                               divisas__divisa__codigo='MXN')
            .exclude(id__in=usados.values('ingreso_tesoreria_id'))
            .prefetch_related('divisas')
            .order_by('-fecha', '-creado')
            .distinct()
        )
        corte = fecha_corte()
        if corte:
            queryset = queryset.filter(fecha__gt=corte)
        desde = parse_date(params.get('desde') or '')
        if desde:
            queryset = queryset.filter(fecha__gte=desde)
        hasta = parse_date(params.get('hasta') or '')
        if hasta:
            queryset = queryset.filter(fecha__lte=hasta)
        texto = (params.get('q') or '').strip()
        if texto:
            queryset = queryset.filter(
                Q(folio__icontains=texto) | Q(beneficiario__icontains=texto) | Q(concepto__icontains=texto)
            )
        return Response(IngresoTesoreriaSerializer(queryset[:200], many=True).data)


class ResumenCarteraView(APIView):
    """Tablero de Inicio: por cobrar por empresa, cobrado del mes y clientes con saldo."""
    permission_classes = [AreaCartera]

    def get(self, request):
        empresas, filas = saldos_de_clientes(solo_activos=False)
        hoy = timezone.localdate()
        inicio_mes = hoy.replace(day=1)
        cobrado = PagoCliente.objects.filter(cancelado=False, fecha__gte=inicio_mes, fecha__lte=hoy)
        cobrado_por_empresa = {
            f['empresa_id']: f['total'] for f in cobrado.values('empresa_id').annotate(total=Sum('monto'))
        }
        mayores = sorted((f for f in filas if f.conjunto > 0), key=lambda f: f.conjunto, reverse=True)[:10]
        por_estado = {}
        for linea in lineas_venta():
            clave = estado(linea)[0]
            por_estado[clave] = por_estado.get(clave, 0) + 1
        return Response({
            'fecha_corte': fecha_corte().isoformat() if fecha_corte() else None,
            'empresas': [
                {
                    'id': e.id, 'nombre': e.nombre,
                    'por_cobrar': _dinero(sum((f.por_empresa[e.id] for f in filas if f.por_empresa[e.id] > 0), 0)),
                    'a_favor': _dinero(-sum((f.por_empresa[e.id] for f in filas if f.por_empresa[e.id] < 0), 0)),
                    'cobrado_mes': _dinero(cobrado_por_empresa.get(e.id, 0)),
                }
                for e in empresas
            ],
            'clientes_con_saldo': sum(1 for f in filas if f.conjunto > 0),
            'por_identificar': [
                {'empresa_id': empresa_id, 'cantidad': cantidad, 'monto': _dinero(monto)}
                for empresa_id, (cantidad, monto) in sorted(por_identificar().items())
            ],
            'ventas': {
                'pendientes': por_estado.get(PENDIENTE, 0),
                'diferencia': por_estado.get(VerificacionVenta.DIFERENCIA, 0),
                'sin_precio': por_estado.get(SIN_PRECIO, 0),
                'verificadas': por_estado.get(VerificacionVenta.VERIFICADA, 0),
            },
            'mayores_saldos': [
                {'id': f.cliente.id, 'nombre': f.cliente.nombre, 'saldo_conjunto': _dinero(f.conjunto)} for f in mayores
            ],
        })


class ReporteSaldosView(APIView):
    """Saldos de clientes como la hoja SALDO CLIENTES del área (JSON, PDF o Excel)."""
    permission_classes = [AreaCartera]
    formato = None

    def get(self, request):
        params = request.query_params
        empresas, filas = saldos_de_clientes(
            texto=params.get('q') or '', solo_con_saldo=_verdadero(params.get('con_saldo')),
            solo_activos=not _verdadero(params.get('incluir_inactivos')),
        )
        # Pagos sin cliente: renglón aparte por empresa (negativo, como en el Excel del área).
        sin_identificar = {e.id: -por_identificar().get(e.id, (0, 0))[1] for e in empresas}
        if self.formato == 'pdf':
            # ?resumido=1: hoja vertical solo con cliente y saldos.
            resumido = _verdadero(params.get('resumido'))
            nombre = 'saldos_clientes_resumido.pdf' if resumido else 'saldos_clientes.pdf'
            return _descarga(construir_pdf_saldos(empresas, filas, resumido, sin_identificar).getvalue(), 'application/pdf', nombre)
        if self.formato == 'excel':
            return _excel(construir_excel_saldos(empresas, filas, sin_identificar), 'saldos_clientes.xlsx')
        return Response({
            'empresas': EmpresaSerializer(empresas, many=True).data,
            'por_identificar': {str(e_id): _dinero(monto) for e_id, monto in sin_identificar.items()},
            'filas': [
                {
                    'cliente': {'id': f.cliente.id, 'nombre': f.cliente.nombre},
                    'saldos': {str(e.id): _dinero(f.por_empresa[e.id]) for e in empresas},
                    'saldo_conjunto': _dinero(f.conjunto),
                    'ultimo_pago': {
                        'fecha': f.ultimo_pago[0].isoformat(),
                        'monto': _dinero(f.ultimo_pago[1]) if f.ultimo_pago[1] is not None else None,
                    } if f.ultimo_pago else None,
                    'celular': f.perfil.celular if f.perfil else '',
                    'pagare': bool(f.perfil and f.perfil.pagare),
                }
                for f in filas
            ],
        })
