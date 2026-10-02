import tempfile
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    Denominacion,
    Divisa,
    Empleado,
    MovimientoArchivo,
    MovimientoDivisa,
    MovimientoTesoreria,
    NominaDetalle,
    NominaSemanal,
    Puesto,
    Rancho,
)
from .saldos import historial, resumen_dia, saldos_al
from importadora.exception_handler import custom_exception_handler
from security.permissions import AREA_TESORERIA
from security.testing import crear_usuario_con_area

User = get_user_model()

HOY = timezone.localdate()


def crear_movimiento(fecha, tipo, divisa, cantidad, usuario, folio, cancelado=False):
    movimiento = MovimientoTesoreria.objects.create(
        fecha=fecha, folio=folio, tipo=tipo, autorizo='Jefe', beneficiario='B', concepto='C', usuario=usuario,
    )
    MovimientoDivisa.objects.create(movimiento=movimiento, divisa=divisa, cantidad=Decimal(cantidad))
    if cancelado:
        movimiento.cancelar(usuario=usuario, motivo='Prueba')
    return movimiento


class SaldosPorFechaTests(TestCase):
    """El saldo de un día se calcula: todos los ingresos - egresos hasta ese día, desde siempre."""

    def setUp(self):
        self.usuario = User.objects.create_user(username='cajero', password='x')
        self.mxn = Divisa.objects.create(codigo='MXN', nombre='Peso', simbolo='$')
        self.d1 = HOY - timedelta(days=10)
        crear_movimiento(self.d1, 'I', self.mxn, '1000', self.usuario, 'SI-1')  # saldo inicial

    def test_movimiento_con_fecha_atrasada_recalcula_los_dias_siguientes(self):
        crear_movimiento(self.d1 + timedelta(days=5), 'E', self.mxn, '300', self.usuario, 'E-1')
        self.assertEqual(saldos_al(HOY)[self.mxn.id], Decimal('700'))

        # Se captura hoy una hoja del día 2 que no se había pasado.
        crear_movimiento(self.d1 + timedelta(days=2), 'I', self.mxn, '500', self.usuario, 'I-1')

        self.assertEqual(saldos_al(self.d1 + timedelta(days=2))[self.mxn.id], Decimal('1500'))
        self.assertEqual(saldos_al(HOY)[self.mxn.id], Decimal('1200'))

    def test_los_cancelados_no_cuentan(self):
        crear_movimiento(self.d1, 'I', self.mxn, '999', self.usuario, 'I-1', cancelado=True)
        self.assertEqual(saldos_al(HOY)[self.mxn.id], Decimal('1000'))

    def test_un_movimiento_muy_antiguo_cuenta_en_todos_los_saldos_posteriores(self):
        antiguo = HOY - timedelta(days=400)
        crear_movimiento(antiguo, 'I', self.mxn, '500', self.usuario, 'I-1')

        self.assertEqual(saldos_al(antiguo - timedelta(days=1)), {})
        self.assertEqual(saldos_al(HOY)[self.mxn.id], Decimal('1500'))
        dias = {d['fecha']: d for d in historial(self.d1, HOY)}
        self.assertEqual(dias[self.d1]['saldos'][self.mxn.id], Decimal('1500'))

    def test_resumen_del_dia_parte_del_cierre_del_dia_anterior(self):
        dia = self.d1 + timedelta(days=4)
        crear_movimiento(self.d1, 'I', self.mxn, '100', self.usuario, 'I-0')
        crear_movimiento(dia, 'I', self.mxn, '50', self.usuario, 'I-1')
        crear_movimiento(dia, 'E', self.mxn, '20', self.usuario, 'E-1')

        fila = resumen_dia(dia)[0]
        self.assertEqual((fila['saldo_inicial'], fila['ingresos'], fila['egresos'], fila['saldo_final']),
                         (Decimal('1100'), Decimal('50'), Decimal('20'), Decimal('1130')))
        # El primer día con movimientos parte de cero.
        primer_dia = resumen_dia(self.d1)[0]
        self.assertEqual((primer_dia['saldo_inicial'], primer_dia['ingresos']), (Decimal('0'), Decimal('1100')))

    def test_historial_marca_los_dias_en_negativo_sin_bloquear(self):
        crear_movimiento(self.d1 + timedelta(days=1), 'E', self.mxn, '1500', self.usuario, 'E-1')
        crear_movimiento(self.d1 + timedelta(days=2), 'I', self.mxn, '800', self.usuario, 'I-1')

        dias = {d['fecha']: d for d in historial(self.d1, HOY)}
        self.assertFalse(dias[self.d1]['negativo'])
        self.assertTrue(dias[self.d1 + timedelta(days=1)]['negativo'])
        self.assertEqual(dias[self.d1 + timedelta(days=1)]['saldos'][self.mxn.id], Decimal('-500'))
        self.assertFalse(dias[self.d1 + timedelta(days=2)]['negativo'])

    def test_sin_movimientos_no_hay_saldos(self):
        MovimientoTesoreria.objects.all().delete()
        self.assertEqual(saldos_al(HOY), {})

    def test_no_se_aceptan_fechas_futuras(self):
        with self.assertRaises(ValidationError):
            crear_movimiento(HOY + timedelta(days=1), 'I', self.mxn, '1', self.usuario, 'F')


class SaldoInicialTests(APITestCase):
    """El saldo inicial (sin fecha) se suma a todo saldo calculado; solo el superusuario lo modifica."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username='admin', password='x')
        self.cajero = crear_usuario_con_area('cajero', AREA_TESORERIA)
        self.mxn = Divisa.objects.create(codigo='MXN', nombre='Peso', simbolo='$')
        self.usd = Divisa.objects.create(codigo='USD', nombre='Dólar', simbolo='$')

    def _guardar(self, usuario, saldos):
        self.client.force_authenticate(user=usuario)
        return self.client.post('/api/treasury/saldos-iniciales/', {'saldos': saldos}, format='json')

    def test_el_superusuario_lo_guarda_y_se_suma_a_todos_los_saldos(self):
        crear_movimiento(HOY - timedelta(days=900), 'E', self.mxn, '200', self.cajero, 'E-1')
        respuesta = self._guardar(self.admin, [{'divisa_id': self.mxn.id, 'monto': '1000.00'}])

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        mxn = next(f for f in respuesta.data if f['divisa']['codigo'] == 'MXN')
        self.assertEqual((mxn['monto'], mxn['editado_por']['username']), ('1000.00', 'admin'))
        self.assertEqual(saldos_al(HOY - timedelta(days=1000))[self.mxn.id], Decimal('1000'))
        self.assertEqual(saldos_al(HOY)[self.mxn.id], Decimal('800'))
        self.assertEqual(resumen_dia(HOY - timedelta(days=900))[0]['saldo_inicial'], Decimal('1000'))

    def test_volver_a_guardar_actualiza_el_monto(self):
        self._guardar(self.admin, [{'divisa_id': self.mxn.id, 'monto': '1000.00'}])
        self._guardar(self.admin, [{'divisa_id': self.mxn.id, 'monto': '1500.00'}])
        self.assertEqual(saldos_al(HOY)[self.mxn.id], Decimal('1500'))

    def test_tesoreria_lo_consulta_pero_no_lo_modifica(self):
        self.client.force_authenticate(user=self.cajero)
        listado = self.client.get('/api/treasury/saldos-iniciales/')
        self.assertEqual(listado.status_code, 200)
        self.assertEqual([f['monto'] for f in listado.data], ['0.00', '0.00'])

        respuesta = self._guardar(self.cajero, [{'divisa_id': self.mxn.id, 'monto': '1.00'}])
        self.assertEqual(respuesta.status_code, 403)

    def test_rechaza_montos_negativos_y_divisas_repetidas(self):
        negativo = self._guardar(self.admin, [{'divisa_id': self.mxn.id, 'monto': '-1.00'}])
        repetida = self._guardar(self.admin, [
            {'divisa_id': self.mxn.id, 'monto': '1.00'}, {'divisa_id': self.mxn.id, 'monto': '2.00'},
        ])
        self.assertEqual((negativo.status_code, repetida.status_code), (400, 400))


class CustomExceptionHandlerTests(TestCase):
    def test_django_validation_error_se_convierte_en_400_con_json(self):
        respuesta = custom_exception_handler(ValidationError('Mensaje amigable de negocio.'), {})

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('non_field_errors', respuesta.data)
        self.assertEqual(respuesta.data['non_field_errors'], ['Mensaje amigable de negocio.'])

    def test_excepcion_no_controlada_nunca_expone_detalle_tecnico(self):
        respuesta = custom_exception_handler(KeyError('boom'), {})

        self.assertEqual(respuesta.status_code, 500)
        self.assertNotIn('boom', respuesta.data['detail'])


class MovimientoTesoreriaApiTests(APITestCase):
    def setUp(self):
        self.usuario = crear_usuario_con_area('cajero', AREA_TESORERIA, password='clave12345')
        self.client.force_authenticate(user=self.usuario)
        self.mxn = Divisa.objects.create(codigo='MXN', nombre='Peso mexicano', simbolo='$')

    def _payload(self, folio='001', tipo='I', cantidad='100.00', fecha=None, **extra):
        return {
            'fecha': (fecha or HOY).isoformat(),
            'folio': folio,
            'tipo': tipo,
            'autorizo': 'Jefe de caja',
            'beneficiario': 'Proveedor X',
            'concepto': 'Prueba',
            'divisas': [{'divisa_id': self.mxn.id, 'cantidad': cantidad}],
            **extra,
        }

    def _crear_movimiento(self, **kwargs):
        respuesta = self.client.post('/api/treasury/movimientos/', self._payload(**kwargs), format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        return respuesta.data

    def test_se_captura_con_fecha_atrasada_y_queda_a_nombre_del_usuario_de_la_sesion(self):
        otro = User.objects.create_user(username='otro', password='x')
        hace_una_semana = HOY - timedelta(days=7)
        creado = self._crear_movimiento(fecha=hace_una_semana, usuario=otro.id)

        self.assertEqual(creado['fecha'], hace_una_semana.isoformat())
        self.assertEqual(creado['usuario']['username'], 'cajero')  # se ignora el usuario del payload

    def test_egreso_que_deja_la_caja_en_negativo_se_guarda(self):
        self._crear_movimiento(tipo='E', cantidad='500.00')
        self.assertEqual(saldos_al(HOY)[self.mxn.id], Decimal('-500.00'))

    def test_fecha_futura_se_rechaza_y_una_antigua_se_acepta(self):
        futura = self.client.post('/api/treasury/movimientos/', self._payload(fecha=HOY + timedelta(days=1)), format='json')
        self.assertEqual(futura.status_code, 400)
        self._crear_movimiento(folio='002', fecha=HOY - timedelta(days=800))

    def test_editar_registra_quien_y_cuando_y_recalcula_el_saldo(self):
        movimiento = self._crear_movimiento(cantidad='100.00')
        editor = crear_usuario_con_area('editor', AREA_TESORERIA)
        self.client.force_authenticate(user=editor)

        respuesta = self.client.put(
            f"/api/treasury/movimientos/{movimiento['id']}/",
            self._payload(folio=movimiento['folio'], cantidad='150.00', fecha=HOY - timedelta(days=2)),
            format='json',
        )

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertTrue(respuesta.data['editado'])
        self.assertEqual(respuesta.data['editado_por']['username'], 'editor')
        self.assertIsNotNone(respuesta.data['editado_en'])
        self.assertEqual(respuesta.data['usuario']['username'], 'cajero')  # el autor no cambia
        self.assertEqual(saldos_al(HOY)[self.mxn.id], Decimal('150.00'))

    def test_cancelar_revierte_saldo_requiere_motivo_y_registra_al_usuario(self):
        movimiento = self._crear_movimiento(cantidad='100.00')

        sin_motivo = self.client.post(f"/api/treasury/movimientos/{movimiento['id']}/cancelar/", {}, format='json')
        self.assertEqual(sin_motivo.status_code, 400)

        respuesta = self.client.post(
            f"/api/treasury/movimientos/{movimiento['id']}/cancelar/",
            {'motivo': 'Folio duplicado por error de captura'},
            format='json',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertTrue(respuesta.data['cancelado'])
        self.assertEqual(respuesta.data['usuario_cancelacion']['username'], 'cajero')
        self.assertEqual(saldos_al(HOY).get(self.mxn.id, Decimal('0')), Decimal('0.00'))

    def test_folio_duplicado_da_mensaje_amigable(self):
        self._crear_movimiento(folio='DUP-1')
        respuesta = self.client.post('/api/treasury/movimientos/', self._payload(folio='DUP-1'), format='json')

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.data['folio'][0], 'Ya existe un movimiento registrado con este folio.')

    def test_filtra_por_fecha(self):
        self._crear_movimiento(folio='A', fecha=HOY - timedelta(days=3))
        self._crear_movimiento(folio='B')
        respuesta = self.client.get('/api/treasury/movimientos/', {'fecha': HOY.isoformat()})
        self.assertEqual([m['folio'] for m in respuesta.data], ['B'])

    def test_sugerencias_devuelve_coincidencias_mas_usadas_primero(self):
        self._crear_movimiento(folio='S-1')
        self._crear_movimiento(folio='S-2')
        self._crear_movimiento(folio='S-3', beneficiario='Proveedor Y')

        respuesta = self.client.get('/api/treasury/movimientos/sugerencias/', {'campo': 'beneficiario', 'q': 'proveedor'})

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data, ['Proveedor X', 'Proveedor Y'])

    def test_sugerencias_rechaza_campo_invalido(self):
        respuesta = self.client.get('/api/treasury/movimientos/sugerencias/', {'campo': 'folio'})
        self.assertEqual(respuesta.status_code, 400)


class CajaDiariaApiTests(APITestCase):
    def setUp(self):
        self.usuario = crear_usuario_con_area('cajero', AREA_TESORERIA)
        self.client.force_authenticate(user=self.usuario)
        self.mxn = Divisa.objects.create(codigo='MXN', nombre='Peso', simbolo='$')
        self.usd = Divisa.objects.create(codigo='USD', nombre='Dólar', simbolo='$')
        self.inicio = HOY - timedelta(days=5)

    def test_caja_del_dia_devuelve_saldos_y_movimientos(self):
        crear_movimiento(self.inicio, 'I', self.mxn, '1000', self.usuario, 'SI-1')
        crear_movimiento(self.inicio + timedelta(days=1), 'I', self.mxn, '200', self.usuario, 'I-1')
        crear_movimiento(HOY, 'E', self.mxn, '1500', self.usuario, 'E-1')

        respuesta = self.client.get('/api/treasury/caja/dia/', {'fecha': HOY.isoformat()})

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        mxn = next(s for s in respuesta.data['saldos'] if s['divisa']['codigo'] == 'MXN')
        self.assertEqual((mxn['saldo_inicial'], mxn['egresos'], mxn['saldo_final']), ('1200.00', '1500.00', '-300.00'))
        self.assertTrue(mxn['negativo'])
        self.assertTrue(respuesta.data['negativo'])
        self.assertEqual([m['folio'] for m in respuesta.data['movimientos']], ['E-1'])

    def test_historial_lista_los_dias_con_actividad_del_mas_reciente_al_mas_antiguo(self):
        crear_movimiento(self.inicio, 'I', self.mxn, '1000', self.usuario, 'SI-1')
        crear_movimiento(self.inicio + timedelta(days=2), 'I', self.mxn, '10', self.usuario, 'I-1')

        respuesta = self.client.get('/api/treasury/caja/historial/', {'desde': self.inicio.isoformat()})

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual([d['fecha'] for d in respuesta.data],
                         [(self.inicio + timedelta(days=2)).isoformat(), self.inicio.isoformat()])
        self.assertEqual(respuesta.data[1]['saldos'][0]['saldo_final'], '1000.00')

    def test_sin_movimientos_la_caja_del_dia_viene_en_cero(self):
        respuesta = self.client.get('/api/treasury/caja/dia/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual([s['saldo_final'] for s in respuesta.data['saldos']], ['0.00', '0.00'])
        self.assertEqual(respuesta.data['movimientos'], [])


class ReporteMovimientosApiTests(APITestCase):
    def setUp(self):
        import openpyxl  # noqa: F401 -- falla temprano y claro si no está instalado

        self.usuario = crear_usuario_con_area('auditor', AREA_TESORERIA, password='clave12345')
        self.client.force_authenticate(user=self.usuario)

        self.mxn = Divisa.objects.create(codigo='MXN', nombre='Peso mexicano', simbolo='$')
        self.usd = Divisa.objects.create(codigo='USD', nombre='Dólar', simbolo='$')

        self.dia2 = HOY
        self.dia1 = HOY - timedelta(days=1)
        self._crear_movimiento(self.dia1, folio='R-001', tipo='I', divisa=self.mxn, cantidad='1000.00', beneficiario='Cliente Mostrador')
        self._crear_movimiento(self.dia1, folio='R-002', tipo='E', divisa=self.mxn, cantidad='300.00', beneficiario='Proveedor Mariscos del Golfo')
        self._crear_movimiento(self.dia2, folio='R-003', tipo='I', divisa=self.usd, cantidad='200.00', beneficiario='Cliente Mostrador')
        cancelado = self._crear_movimiento(self.dia2, folio='R-004', tipo='E', divisa=self.usd, cantidad='50.00', beneficiario='Aduana')
        cancelado.cancelar(usuario=self.usuario, motivo='Prueba de reporte')

    def _crear_movimiento(self, fecha, folio, tipo, divisa, cantidad, beneficiario):
        payload = {
            'fecha': fecha.isoformat(),
            'folio': folio,
            'tipo': tipo,
            'autorizo': 'Jefe de caja',
            'beneficiario': beneficiario,
            'concepto': 'Prueba de reporte',
            'divisas': [{'divisa_id': divisa.id, 'cantidad': cantidad}],
        }
        respuesta = self.client.post('/api/treasury/movimientos/', payload, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        return MovimientoTesoreria.objects.get(pk=respuesta.data['id'])

    def test_requiere_rango_de_fechas(self):
        respuesta = self.client.get('/api/treasury/reportes/movimientos/')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('fecha', respuesta.data['detail'])

    def test_rango_invertido_da_mensaje_amigable(self):
        respuesta = self.client.get('/api/treasury/reportes/movimientos/', {
            'fecha_inicio': self.dia2.isoformat(),
            'fecha_fin': self.dia1.isoformat(),
        })
        self.assertEqual(respuesta.status_code, 400)

    def test_resumen_agrupa_por_divisa_y_excluye_cancelados_de_los_montos(self):
        respuesta = self.client.get('/api/treasury/reportes/movimientos/', {
            'fecha_inicio': self.dia1.isoformat(),
            'fecha_fin': self.dia2.isoformat(),
        })

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(respuesta.data['total'], 4)

        resumen = {fila['divisa']['codigo']: fila for fila in respuesta.data['resumen']}
        self.assertEqual(resumen['MXN']['ingresos'], '1000.00')
        self.assertEqual(resumen['MXN']['egresos'], '300.00')
        self.assertEqual(resumen['MXN']['neto'], '700.00')

        # El egreso cancelado (50.00 USD) no debe contar en egresos ni en activos.
        self.assertEqual(resumen['USD']['ingresos'], '200.00')
        self.assertEqual(resumen['USD']['egresos'], '0.00')
        self.assertEqual(resumen['USD']['movimientos_activos'], 1)
        self.assertEqual(resumen['USD']['movimientos_cancelados'], 1)

    def test_filtro_por_rango_de_fechas_acota_resultados(self):
        respuesta = self.client.get('/api/treasury/reportes/movimientos/', {
            'fecha_inicio': self.dia1.isoformat(),
            'fecha_fin': self.dia1.isoformat(),
        })
        self.assertEqual(respuesta.data['total'], 2)

    def test_filtro_por_beneficiario(self):
        respuesta = self.client.get('/api/treasury/reportes/movimientos/', {
            'fecha_inicio': self.dia1.isoformat(),
            'fecha_fin': self.dia2.isoformat(),
            'beneficiario': 'mariscos',
        })
        self.assertEqual(respuesta.data['total'], 1)
        self.assertEqual(respuesta.data['muestra'][0]['folio'], 'R-002')

    def test_filtro_por_divisa_y_estado(self):
        respuesta = self.client.get('/api/treasury/reportes/movimientos/', {
            'fecha_inicio': self.dia1.isoformat(),
            'fecha_fin': self.dia2.isoformat(),
            'divisa': self.usd.id,
            'estado': 'cancelados',
        })
        self.assertEqual(respuesta.data['total'], 1)
        self.assertEqual(respuesta.data['muestra'][0]['folio'], 'R-004')
        self.assertTrue(respuesta.data['muestra'][0]['cancelado'])

    def test_excel_se_genera_y_contiene_las_dos_hojas(self):
        import io

        from openpyxl import load_workbook

        respuesta = self.client.get('/api/treasury/reportes/movimientos/excel/', {
            'fecha_inicio': self.dia1.isoformat(),
            'fecha_fin': self.dia2.isoformat(),
        })

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('attachment', respuesta['Content-Disposition'])

        libro = load_workbook(io.BytesIO(respuesta.content))
        self.assertEqual(libro.sheetnames, ['Detalle', 'Resumen por divisa'])

        hoja_detalle = libro['Detalle']
        # encabezado + 4 movimientos (incluye el cancelado, marcado como tal)
        self.assertEqual(hoja_detalle.max_row, 5)
        self.assertEqual(hoja_detalle.cell(row=1, column=1).value, 'Folio')

        hoja_resumen = libro['Resumen por divisa']
        self.assertEqual(hoja_resumen.cell(row=1, column=1).value, 'Divisa')

    def test_pdf_se_genera_con_los_filtros_aplicados(self):
        import reportlab  # noqa: F401 -- falla temprano y claro si no está instalado

        respuesta = self.client.get('/api/treasury/reportes/movimientos/pdf/', {
            'fecha_inicio': self.dia1.isoformat(),
            'fecha_fin': self.dia2.isoformat(),
            'tipo': 'I',
        })

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertIn('attachment', respuesta['Content-Disposition'])
        # Cabecera %PDF- confirma que es un PDF válido, no solo el content-type.
        self.assertTrue(respuesta.content.startswith(b'%PDF-'))
        self.assertGreater(len(respuesta.content), 500)

    def test_pdf_responde_mensaje_amigable_si_falta_el_rango_de_fechas(self):
        respuesta = self.client.get('/api/treasury/reportes/movimientos/pdf/')

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('fecha', respuesta.data['detail'])


class ArqueoCajaApiTests(APITestCase):
    def setUp(self):
        self.usuario = crear_usuario_con_area('auditor_arqueo', AREA_TESORERIA, password='clave12345')
        self.client.force_authenticate(user=self.usuario)

        self.mxn = Divisa.objects.create(codigo='MXN', nombre='Peso mexicano', simbolo='$')
        self.usd = Divisa.objects.create(codigo='USD', nombre='Dólar', simbolo='$')

        self.billete_500 = Denominacion.objects.create(divisa=self.mxn, valor=Decimal('500'), tipo=Denominacion.BILLETE)
        self.moneda_10 = Denominacion.objects.create(divisa=self.mxn, valor=Decimal('10'), tipo=Denominacion.MONEDA)
        self.billete_100_usd = Denominacion.objects.create(divisa=self.usd, valor=Decimal('100'), tipo=Denominacion.BILLETE)

        self.inicio = HOY - timedelta(days=3)
        crear_movimiento(self.inicio, 'I', self.mxn, '1000', self.usuario, 'SI-1')

    def _payload_completo(self, piezas_500=2, piezas_10=3, piezas_100_usd=1, fecha=None):
        return {
            'fecha': (fecha or HOY).isoformat(),
            'hora_inicio': timezone.now().isoformat(),
            'observaciones': 'Arqueo de prueba',
            'divisas': [
                {
                    'divisa_id': self.mxn.id,
                    'conteos': [
                        {'denominacion_id': self.billete_500.id, 'piezas': piezas_500},
                        {'denominacion_id': self.moneda_10.id, 'piezas': piezas_10},
                    ],
                },
                {
                    'divisa_id': self.usd.id,
                    'conteos': [
                        {'denominacion_id': self.billete_100_usd.id, 'piezas': piezas_100_usd},
                    ],
                },
            ],
        }

    def test_crea_arqueo_contra_el_saldo_calculado_del_dia(self):
        crear_movimiento(HOY, 'I', self.mxn, '30', self.usuario, 'I-1')

        respuesta = self.client.post('/api/treasury/arqueos/', self._payload_completo(), format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)

        linea_mxn = next(d for d in respuesta.data['divisas'] if d['divisa']['codigo'] == 'MXN')
        self.assertEqual(Decimal(linea_mxn['total_contado']), Decimal('1030.00'))
        self.assertEqual(Decimal(linea_mxn['resultado_esperado']), Decimal('1030.00'))
        self.assertEqual(linea_mxn['estado'], 'EXACTO')
        self.assertEqual(respuesta.data['usuario']['username'], 'auditor_arqueo')

    def test_varios_arqueos_el_mismo_dia_y_en_fechas_pasadas(self):
        primero = self.client.post('/api/treasury/arqueos/', self._payload_completo(), format='json')
        segundo = self.client.post('/api/treasury/arqueos/', self._payload_completo(piezas_500=1), format='json')
        pasado = self.client.post('/api/treasury/arqueos/', self._payload_completo(fecha=self.inicio), format='json')

        self.assertEqual((primero.status_code, segundo.status_code, pasado.status_code), (201, 201, 201))
        self.assertEqual(len(self.client.get('/api/treasury/arqueos/', {'fecha': HOY.isoformat()}).data), 2)

    def test_el_esperado_es_una_foto_del_momento_del_conteo(self):
        creado = self.client.post('/api/treasury/arqueos/', self._payload_completo(), format='json').data
        crear_movimiento(HOY, 'I', self.mxn, '500', self.usuario, 'TARDE')  # hoja capturada después

        linea = self.client.get(f"/api/treasury/arqueos/{creado['id']}/").data['divisas'][0]
        self.assertEqual(Decimal(linea['resultado_esperado']), Decimal('1000.00'))

    def test_falta_contar_una_divisa_activa_da_error_amigable(self):
        payload = self._payload_completo()
        payload['divisas'].pop()

        respuesta = self.client.post('/api/treasury/arqueos/', payload, format='json')

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('USD', str(respuesta.data))

    def test_denominacion_no_corresponde_a_la_divisa(self):
        payload = self._payload_completo()
        payload['divisas'][0]['conteos'][0]['denominacion_id'] = self.billete_100_usd.id

        respuesta = self.client.post('/api/treasury/arqueos/', payload, format='json')

        self.assertEqual(respuesta.status_code, 400)

    def test_arqueo_antes_del_primer_movimiento_espera_cero(self):
        respuesta = self.client.post(
            '/api/treasury/arqueos/', self._payload_completo(fecha=self.inicio - timedelta(days=1)), format='json',
        )
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        linea_mxn = next(d for d in respuesta.data['divisas'] if d['divisa']['codigo'] == 'MXN')
        self.assertEqual(Decimal(linea_mxn['resultado_esperado']), Decimal('0.00'))

    def test_editar_arqueo_recalcula_totales_y_registra_al_editor(self):
        creado = self.client.post('/api/treasury/arqueos/', self._payload_completo(), format='json').data
        editor = crear_usuario_con_area('supervisor', AREA_TESORERIA)
        self.client.force_authenticate(user=editor)

        payload_editado = self._payload_completo(piezas_500=1, piezas_10=0, piezas_100_usd=1)
        respuesta = self.client.put(f"/api/treasury/arqueos/{creado['id']}/", payload_editado, format='json')

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        linea_mxn = next(d for d in respuesta.data['divisas'] if d['divisa']['codigo'] == 'MXN')
        self.assertEqual(Decimal(linea_mxn['total_contado']), Decimal('500.00'))
        self.assertEqual(respuesta.data['usuario']['username'], 'auditor_arqueo')
        self.assertEqual(respuesta.data['editado_por']['username'], 'supervisor')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class MovimientoArchivoApiTests(APITestCase):
    def setUp(self):
        self.usuario = crear_usuario_con_area('cajero_archivos', AREA_TESORERIA, password='clave12345')
        self.client.force_authenticate(user=self.usuario)

        self.mxn = Divisa.objects.create(codigo='MXN', nombre='Peso mexicano', simbolo='$')
        self.movimiento_id = crear_movimiento(HOY, 'I', self.mxn, '100', self.usuario, 'ARCH-1').id

    def test_sube_un_archivo_valido(self):
        archivo = SimpleUploadedFile('recibo.png', b'contenido-de-prueba', content_type='image/png')

        respuesta = self.client.post('/api/treasury/archivos-movimiento/', {
            'movimiento_id': self.movimiento_id,
            'archivo': archivo,
        }, format='multipart')

        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data['nombre_original'], 'recibo.png')
        self.assertEqual(respuesta.data['subido_por']['username'], 'cajero_archivos')
        self.assertIn('url', respuesta.data)

    def test_rechaza_extension_no_permitida(self):
        archivo = SimpleUploadedFile('script.exe', b'contenido', content_type='application/octet-stream')

        respuesta = self.client.post('/api/treasury/archivos-movimiento/', {
            'movimiento_id': self.movimiento_id,
            'archivo': archivo,
        }, format='multipart')

        self.assertEqual(respuesta.status_code, 400)

    def test_rechaza_archivo_mas_grande_que_el_limite(self):
        contenido_grande = b'a' * (MovimientoArchivo.TAMANO_MAXIMO_BYTES + 1)
        archivo = SimpleUploadedFile('grande.pdf', contenido_grande, content_type='application/pdf')

        respuesta = self.client.post('/api/treasury/archivos-movimiento/', {
            'movimiento_id': self.movimiento_id,
            'archivo': archivo,
        }, format='multipart')

        self.assertEqual(respuesta.status_code, 400)

    def test_borrar_deja_rastro_de_quien_y_cuando_y_lo_oculta(self):
        archivo = SimpleUploadedFile('recibo.jpg', b'contenido', content_type='image/jpeg')
        creado = self.client.post('/api/treasury/archivos-movimiento/', {
            'movimiento_id': self.movimiento_id,
            'archivo': archivo,
        }, format='multipart').data

        listado = self.client.get('/api/treasury/archivos-movimiento/', {'movimiento': self.movimiento_id})
        self.assertEqual(len(listado.data), 1)

        borrado = self.client.delete(f"/api/treasury/archivos-movimiento/{creado['id']}/")
        self.assertEqual(borrado.status_code, 204)

        registro = MovimientoArchivo.objects.get(pk=creado['id'])
        self.assertTrue(registro.eliminado)
        self.assertEqual(registro.eliminado_por, self.usuario)
        self.assertIsNotNone(registro.eliminado_en)
        self.assertEqual(self.client.get('/api/treasury/archivos-movimiento/', {'movimiento': self.movimiento_id}).data, [])
        movimiento = self.client.get(f'/api/treasury/movimientos/{self.movimiento_id}/').data
        self.assertEqual(movimiento['archivos_count'], 0)


class NominaDetalleModelTests(TestCase):
    def setUp(self):
        self.rancho = Rancho.objects.create(nombre='Rancho El Camarón')
        self.puesto = Puesto.objects.create(nombre='Cosechador')
        self.empleado = Empleado.objects.create(
            rancho=self.rancho,
            puesto=self.puesto,
            nombre='Juan Pérez',
            salario_diario=Decimal('200.00'),
            numero_cuenta='1234567890',
        )
        self.usuario = crear_usuario_con_area('nominero', AREA_TESORERIA, password='clave12345')
        self.nomina = NominaSemanal.objects.create(
            fecha_inicio=timezone.localdate(),
            fecha_fin=timezone.localdate() + timedelta(days=6),
            creado_por=self.usuario,
        )

    def test_calcula_totales_y_hace_snapshot_del_salario(self):
        detalle = NominaDetalle.objects.create(
            nomina=self.nomina,
            empleado=self.empleado,
            dias_trabajados=Decimal('5'),
            salario_diario=self.empleado.salario_diario,
            descuento=Decimal('100.00'),
        )

        self.assertEqual(detalle.total_bruto, Decimal('1000.00'))
        self.assertEqual(detalle.total_neto, Decimal('900.00'))

        # Un cambio posterior al salario del empleado no debe alterar la nómina ya capturada.
        self.empleado.salario_diario = Decimal('300.00')
        self.empleado.save()
        detalle.refresh_from_db()
        self.assertEqual(detalle.salario_diario, Decimal('200.00'))

    def test_rechaza_descuento_mayor_al_total_bruto(self):
        detalle = NominaDetalle(
            nomina=self.nomina,
            empleado=self.empleado,
            dias_trabajados=Decimal('5'),
            salario_diario=self.empleado.salario_diario,
            descuento=Decimal('1500.00'),
        )
        with self.assertRaisesMessage(
            ValidationError, 'El descuento no puede ser mayor al total bruto del empleado.'
        ):
            detalle.full_clean()

    def test_rechaza_dias_trabajados_fuera_de_rango(self):
        detalle = NominaDetalle(
            nomina=self.nomina,
            empleado=self.empleado,
            dias_trabajados=Decimal('8'),
            salario_diario=self.empleado.salario_diario,
        )
        with self.assertRaisesMessage(ValidationError, 'Los días trabajados deben estar entre 0 y 7.'):
            detalle.full_clean()


class NominaSemanalApiTests(APITestCase):
    def setUp(self):
        self.usuario = crear_usuario_con_area('nominero_api', AREA_TESORERIA, password='clave12345')
        self.client.force_authenticate(user=self.usuario)

        self.rancho_1 = Rancho.objects.create(nombre='Rancho Norte')
        self.rancho_2 = Rancho.objects.create(nombre='Rancho Sur')
        self.puesto = Puesto.objects.create(nombre='Vigilante')

        self.empleado_1 = Empleado.objects.create(
            rancho=self.rancho_1, puesto=self.puesto, nombre='Ana López', salario_diario=Decimal('250.00'),
        )
        self.empleado_2 = Empleado.objects.create(
            rancho=self.rancho_2, puesto=self.puesto, nombre='Luis Ramírez', salario_diario=Decimal('180.00'),
        )

    def _payload(self):
        return {
            'fecha_inicio': timezone.localdate().isoformat(),
            'fecha_fin': (timezone.localdate() + timedelta(days=6)).isoformat(),
            'detalles': [
                {'empleado_id': self.empleado_1.id, 'dias_trabajados': '6', 'salario_diario': '250.00', 'descuento': '50.00'},
                {'empleado_id': self.empleado_2.id, 'dias_trabajados': '7', 'salario_diario': '180.00', 'descuento': '0'},
            ],
        }

    def test_crea_nomina_y_calcula_totales_generales(self):
        respuesta = self.client.post('/api/treasury/nominas/', self._payload(), format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)

        # 6*250 - 50 = 1450; 7*180 - 0 = 1260; total_neto = 2710
        self.assertEqual(Decimal(respuesta.data['total_bruto']), Decimal('2760.00'))
        self.assertEqual(Decimal(respuesta.data['total_neto']), Decimal('2710.00'))

    def test_no_permite_editar_una_nomina_cerrada(self):
        respuesta = self.client.post('/api/treasury/nominas/', self._payload(), format='json')
        nomina_id = respuesta.data['id']

        respuesta_cierre = self.client.post(f'/api/treasury/nominas/{nomina_id}/cerrar/')
        self.assertEqual(respuesta_cierre.status_code, 200, respuesta_cierre.data)
        self.assertTrue(respuesta_cierre.data['cerrada'])

        respuesta_edicion = self.client.put(
            f'/api/treasury/nominas/{nomina_id}/', self._payload(), format='json'
        )
        self.assertEqual(respuesta_edicion.status_code, 400)
