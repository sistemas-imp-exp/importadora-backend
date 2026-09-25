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
    ArqueoCaja,
    CorteCaja,
    Denominacion,
    Divisa,
    Empleado,
    MovimientoArchivo,
    MovimientoTesoreria,
    NominaDetalle,
    NominaSemanal,
    Puesto,
    Rancho,
)
from importadora.exception_handler import custom_exception_handler
from security.permissions import AREA_TESORERIA
from security.testing import crear_usuario_con_area

User = get_user_model()


class CorteCajaModelTests(TestCase):
    def setUp(self):
        self.responsable = User.objects.create_user(username='responsable', password='clave12345')
        self.responsable_2 = User.objects.create_user(username='responsable2', password='clave12345')
        self.responsable_cierre = User.objects.create_user(username='responsable_cierre', password='clave12345')
        Divisa.objects.create(codigo='MXN', nombre='Peso mexicano', simbolo='$')

    def test_no_se_puede_abrir_corte_sin_divisas_activas(self):
        Divisa.objects.all().delete()

        with self.assertRaisesMessage(
            ValidationError, 'Debes registrar al menos una divisa activa antes de abrir un corte de caja.'
        ):
            CorteCaja.abrir_nuevo_corte(
                fecha=timezone.localdate(),
                responsable_apertura=self.responsable,
                observaciones='Apertura sin divisas',
            )

    def test_no_new_cut_if_open_cut_exists(self):
        fecha = timezone.localdate()
        CorteCaja.abrir_nuevo_corte(
            fecha=fecha,
            responsable_apertura=self.responsable,
            observaciones='Apertura inicial',
        )

        with self.assertRaisesMessage(ValidationError, 'Debe cerrar el corte de caja abierto antes de abrir uno nuevo.'):
            CorteCaja.abrir_nuevo_corte(
                fecha=fecha + timedelta(days=1),
                responsable_apertura=self.responsable_2,
                observaciones='Apertura siguiente',
            )

    def test_can_open_new_cut_after_closing_previous(self):
        fecha = timezone.localdate()
        corte = CorteCaja.abrir_nuevo_corte(
            fecha=fecha,
            responsable_apertura=self.responsable,
            observaciones='Apertura inicial',
        )
        corte.close(responsable_cierre=self.responsable_cierre, fecha_cierre=timezone.now())

        siguiente_fecha = fecha + timedelta(days=1)
        nuevo_corte = CorteCaja.abrir_nuevo_corte(
            fecha=siguiente_fecha,
            responsable_apertura=self.responsable_2,
            observaciones='Apertura siguiente',
        )

        self.assertEqual(nuevo_corte.fecha, siguiente_fecha)
        self.assertFalse(nuevo_corte.cerrado)


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

        self.corte = CorteCaja.abrir_nuevo_corte(
            fecha=timezone.localdate(),
            responsable_apertura=self.usuario,
        )

    def _crear_movimiento(self, folio='001', tipo='I', cantidad='100.00'):
        payload = {
            'folio': folio,
            'tipo': tipo,
            'autorizo': 'Jefe de caja',
            'beneficiario': 'Proveedor X',
            'concepto': 'Prueba',
            'divisas': [{'divisa_id': self.mxn.id, 'cantidad': cantidad}],
        }
        respuesta = self.client.post('/api/treasury/movimientos/', payload, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        return respuesta.data

    def test_editar_movimiento_actualiza_saldo_y_marca_editado(self):
        movimiento = self._crear_movimiento(cantidad='100.00')

        payload_editado = {
            'folio': movimiento['folio'],
            'tipo': 'I',
            'autorizo': 'Jefe de caja',
            'beneficiario': 'Proveedor X',
            'concepto': 'Prueba corregida',
            'divisas': [{'divisa_id': self.mxn.id, 'cantidad': '150.00'}],
        }
        respuesta = self.client.put(f"/api/treasury/movimientos/{movimiento['id']}/", payload_editado, format='json')

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertTrue(respuesta.data['editado'])

        saldo = self.corte.saldos.get(divisa=self.mxn)
        self.assertEqual(saldo.saldo_final, Decimal('150.00'))

    def test_no_se_puede_editar_movimiento_de_corte_cerrado(self):
        movimiento = self._crear_movimiento()
        self.corte.close(responsable_cierre=self.usuario, fecha_cierre=timezone.now())

        payload_editado = {
            'folio': movimiento['folio'],
            'tipo': 'I',
            'autorizo': 'Jefe de caja',
            'beneficiario': 'Proveedor X',
            'concepto': 'Intento de edición',
            'divisas': [{'divisa_id': self.mxn.id, 'cantidad': '200.00'}],
        }
        respuesta = self.client.put(f"/api/treasury/movimientos/{movimiento['id']}/", payload_editado, format='json')

        self.assertEqual(respuesta.status_code, 400)

    def test_cancelar_movimiento_revierte_saldo_y_requiere_motivo(self):
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
        self.assertEqual(respuesta.data['motivo_cancelacion'], 'Folio duplicado por error de captura')

        saldo = self.corte.saldos.get(divisa=self.mxn)
        self.assertEqual(saldo.saldo_final, Decimal('0.00'))

    def test_folio_duplicado_da_mensaje_amigable(self):
        self._crear_movimiento(folio='DUP-1')

        payload = {
            'folio': 'DUP-1',
            'tipo': 'I',
            'autorizo': 'Jefe de caja',
            'beneficiario': 'Otro',
            'concepto': 'Prueba',
            'divisas': [{'divisa_id': self.mxn.id, 'cantidad': '10.00'}],
        }
        respuesta = self.client.post('/api/treasury/movimientos/', payload, format='json')

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.data['folio'][0], 'Ya existe un movimiento registrado con este folio.')

    def test_sugerencias_devuelve_coincidencias_mas_usadas_primero(self):
        self._crear_movimiento(folio='S-1')
        self._crear_movimiento(folio='S-2')
        payload_otro = {
            'folio': 'S-3',
            'tipo': 'I',
            'autorizo': 'Jefe de caja',
            'beneficiario': 'Proveedor Y',
            'concepto': 'Prueba',
            'divisas': [{'divisa_id': self.mxn.id, 'cantidad': '10.00'}],
        }
        self.client.post('/api/treasury/movimientos/', payload_otro, format='json')

        respuesta = self.client.get('/api/treasury/movimientos/sugerencias/', {'campo': 'beneficiario', 'q': 'proveedor'})

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data, ['Proveedor X', 'Proveedor Y'])

    def test_sugerencias_rechaza_campo_invalido(self):
        respuesta = self.client.get('/api/treasury/movimientos/sugerencias/', {'campo': 'corte_id'})

        self.assertEqual(respuesta.status_code, 400)


class ReporteMovimientosApiTests(APITestCase):
    def setUp(self):
        import openpyxl  # noqa: F401 -- falla temprano y claro si no está instalado

        self.usuario = crear_usuario_con_area('auditor', AREA_TESORERIA, password='clave12345')
        self.client.force_authenticate(user=self.usuario)

        self.mxn = Divisa.objects.create(codigo='MXN', nombre='Peso mexicano', simbolo='$')
        self.usd = Divisa.objects.create(codigo='USD', nombre='Dólar', simbolo='$')

        self.dia1 = timezone.localdate()
        self.dia2 = self.dia1 + timedelta(days=1)

        self.corte1 = CorteCaja.abrir_nuevo_corte(fecha=self._medianoche(self.dia1), responsable_apertura=self.usuario)
        self._crear_movimiento(self.corte1, folio='R-001', tipo='I', divisa=self.mxn, cantidad='1000.00', beneficiario='Cliente Mostrador')
        self._crear_movimiento(self.corte1, folio='R-002', tipo='E', divisa=self.mxn, cantidad='300.00', beneficiario='Proveedor Mariscos del Golfo')
        self.corte1.close(responsable_cierre=self.usuario, fecha_cierre=timezone.now())

        self.corte2 = CorteCaja.abrir_nuevo_corte(fecha=self._medianoche(self.dia2), responsable_apertura=self.usuario)
        mov_usd = self._crear_movimiento(self.corte2, folio='R-003', tipo='I', divisa=self.usd, cantidad='200.00', beneficiario='Cliente Mostrador')
        cancelado = self._crear_movimiento(self.corte2, folio='R-004', tipo='E', divisa=self.usd, cantidad='50.00', beneficiario='Aduana')
        cancelado.cancelar(usuario=self.usuario, motivo='Prueba de reporte')

    def _medianoche(self, fecha):
        return timezone.make_aware(timezone.datetime(fecha.year, fecha.month, fecha.day, 9, 0))

    def _crear_movimiento(self, corte, folio, tipo, divisa, cantidad, beneficiario):
        payload = {
            'folio': folio,
            'tipo': tipo,
            'autorizo': 'Jefe de caja',
            'beneficiario': beneficiario,
            'concepto': 'Prueba de reporte',
            'divisas': [{'divisa_id': divisa.id, 'cantidad': cantidad}],
        }
        # Cada corte debe estar abierto al momento de crear su movimiento;
        # por eso se llama antes de cerrar corte1 en setUp.
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

        self.corte = CorteCaja.abrir_nuevo_corte(fecha=timezone.now(), responsable_apertura=self.usuario)

    def _payload_completo(self, piezas_500=2, piezas_10=3, piezas_100_usd=1):
        return {
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

    def test_crea_arqueo_y_calcula_totales_por_divisa(self):
        respuesta = self.client.post('/api/treasury/arqueos/', self._payload_completo(), format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)

        linea_mxn = next(d for d in respuesta.data['divisas'] if d['divisa']['codigo'] == 'MXN')
        self.assertEqual(Decimal(linea_mxn['total_contado']), Decimal('1030.00'))
        self.assertEqual(Decimal(linea_mxn['resultado_esperado']), Decimal('0.00'))
        self.assertEqual(Decimal(linea_mxn['diferencia']), Decimal('1030.00'))
        self.assertEqual(linea_mxn['estado'], 'SOBRANTE')

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

    def test_sin_corte_abierto_da_error_amigable(self):
        self.corte.close(responsable_cierre=self.usuario, fecha_cierre=timezone.now())

        respuesta = self.client.post('/api/treasury/arqueos/', self._payload_completo(), format='json')

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('corte de caja abierto', str(respuesta.data))

    def test_editar_arqueo_recalcula_totales_sin_tocar_resultado_esperado(self):
        creado = self.client.post('/api/treasury/arqueos/', self._payload_completo(), format='json').data

        payload_editado = self._payload_completo(piezas_500=1, piezas_10=0, piezas_100_usd=1)
        respuesta = self.client.put(f"/api/treasury/arqueos/{creado['id']}/", payload_editado, format='json')

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        linea_mxn = next(d for d in respuesta.data['divisas'] if d['divisa']['codigo'] == 'MXN')
        self.assertEqual(Decimal(linea_mxn['total_contado']), Decimal('500.00'))
        self.assertEqual(Decimal(linea_mxn['resultado_esperado']), Decimal('0.00'))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class MovimientoArchivoApiTests(APITestCase):
    def setUp(self):
        self.usuario = crear_usuario_con_area('cajero_archivos', AREA_TESORERIA, password='clave12345')
        self.client.force_authenticate(user=self.usuario)

        self.mxn = Divisa.objects.create(codigo='MXN', nombre='Peso mexicano', simbolo='$')
        CorteCaja.abrir_nuevo_corte(fecha=timezone.localdate(), responsable_apertura=self.usuario)

        payload = {
            'folio': 'ARCH-1',
            'tipo': 'I',
            'autorizo': 'Jefe de caja',
            'beneficiario': 'Proveedor X',
            'concepto': 'Prueba',
            'divisas': [{'divisa_id': self.mxn.id, 'cantidad': '100.00'}],
        }
        respuesta = self.client.post('/api/treasury/movimientos/', payload, format='json')
        self.movimiento_id = respuesta.data['id']

    def test_sube_un_archivo_valido(self):
        archivo = SimpleUploadedFile('recibo.png', b'contenido-de-prueba', content_type='image/png')

        respuesta = self.client.post('/api/treasury/archivos-movimiento/', {
            'movimiento_id': self.movimiento_id,
            'archivo': archivo,
        }, format='multipart')

        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data['nombre_original'], 'recibo.png')
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

    def test_lista_filtrada_por_movimiento_y_borrado(self):
        archivo = SimpleUploadedFile('recibo.jpg', b'contenido', content_type='image/jpeg')
        creado = self.client.post('/api/treasury/archivos-movimiento/', {
            'movimiento_id': self.movimiento_id,
            'archivo': archivo,
        }, format='multipart').data

        listado = self.client.get('/api/treasury/archivos-movimiento/', {'movimiento': self.movimiento_id})
        self.assertEqual(listado.status_code, 200)
        self.assertEqual(len(listado.data), 1)

        borrado = self.client.delete(f"/api/treasury/archivos-movimiento/{creado['id']}/")
        self.assertEqual(borrado.status_code, 204)
        self.assertEqual(MovimientoArchivo.objects.count(), 0)


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
