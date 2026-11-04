from datetime import timedelta
from io import BytesIO

from django.utils import timezone
from openpyxl import load_workbook
from rest_framework.test import APITestCase

from security.permissions import AREA_GANADO
from security.testing import crear_usuario_con_area

from .models import ACTIVO, CANCELADO, MUERTO, VENDIDO, Animal, Categoria, EdicionAnimal, Especie, Rancho


class BaseGanado(APITestCase):
    def setUp(self):
        self.usuario = crear_usuario_con_area('vaquero', AREA_GANADO)
        self.client.force_authenticate(user=self.usuario)
        self.gloria = Rancho.objects.get(nombre='LA GLORIA')
        self.trinidad = Rancho.objects.get(nombre='TRINIDAD')
        self.bovino = Especie.objects.get(nombre='BOVINO')
        self.vaca = Categoria.objects.get(especie=self.bovino, nombre='VACA')
        self.becerro = Categoria.objects.get(especie=self.bovino, nombre='BECERRO')
        self.becerra = Categoria.objects.get(especie=self.bovino, nombre='BECERRA')

    def _animal(self, categoria=None, sexo='H', arete=('', '', ''), **extra):
        return {
            'arete_1': arete[0], 'arete_2': arete[1], 'arete_3': arete[2], 'especie_id': self.bovino.pk,
            'categoria_id': (categoria or self.vaca).pk, 'sexo': sexo, **extra,
        }

    def _mover(self, **datos):
        datos.setdefault('fecha', '2026-10-01')
        return self.client.post('/api/ganado/movimientos/', datos, format='json')

    def _alta(self, animales, rancho=None, fecha='2026-10-01', motivo='COMPRA'):
        respuesta = self._mover(tipo='ALTA', motivo=motivo, fecha=fecha, rancho_id=(rancho or self.gloria).pk,
                                animales=animales)
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        return respuesta.data


class AltasTests(BaseGanado):
    def test_alta_con_arete_y_varios_sin_arete(self):
        datos = self._alta([
            self._animal(arete=('0007', '2484', '6789')),
            self._animal(self.becerro, 'M', cantidad=3),
        ])
        self.assertEqual(datos['cabezas'], 4)
        self.assertEqual(datos['resumen'], '1 VACA, 3 BECERRO')
        con_arete = Animal.objects.get(arete_3='6789')
        self.assertEqual(con_arete.arete, '0007 2484 6789')
        self.assertEqual(con_arete.rancho, self.gloria)
        self.assertEqual(con_arete.estado, ACTIVO)
        folios = sorted(Animal.objects.filter(arete_3='').values_list('folio', flat=True))
        self.assertEqual(len(folios), 3)
        self.assertTrue(all(f.startswith('GAN-') for f in folios))

    def test_arete_repetido_o_incompleto_se_rechaza(self):
        self._alta([self._animal(arete=('0007', '2484', '6789'))])
        respuesta = self._mover(tipo='ALTA', motivo='COMPRA', rancho_id=self.trinidad.pk,
                                animales=[self._animal(arete=('0007', '2484', '6789'))])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('arete', str(respuesta.data))
        respuesta = self._mover(tipo='ALTA', motivo='COMPRA', rancho_id=self.gloria.pk,
                                animales=[self._animal(arete=('0007', '', '1234'))])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('arete_3', respuesta.data)
        self.assertEqual(Animal.objects.count(), 1)  # nada a medias

    def test_con_arete_no_se_registra_por_cantidad(self):
        respuesta = self._mover(tipo='ALTA', motivo='COMPRA', rancho_id=self.gloria.pk,
                                animales=[self._animal(arete=('0007', '1', '2'), cantidad=2)])
        self.assertEqual(respuesta.status_code, 400)

    def test_categoria_de_otro_sexo_o_especie(self):
        respuesta = self._mover(tipo='ALTA', motivo='NACIMIENTO', rancho_id=self.gloria.pk,
                                animales=[self._animal(self.becerro, 'H')])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('sexo', respuesta.data)
        yegua = Categoria.objects.get(nombre='YEGUA')
        respuesta = self._mover(tipo='ALTA', motivo='NACIMIENTO', rancho_id=self.gloria.pk,
                                animales=[self._animal(yegua, 'H')])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('categoria_id', respuesta.data)

    def test_motivo_y_fecha(self):
        self.assertEqual(self._mover(tipo='ALTA', motivo='VENTA', rancho_id=self.gloria.pk,
                                     animales=[self._animal()]).status_code, 400)
        futura = (timezone.localdate() + timedelta(days=1)).isoformat()
        respuesta = self._mover(tipo='ALTA', motivo='REGALO', fecha=futura, rancho_id=self.gloria.pk,
                                animales=[self._animal()])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('fecha', respuesta.data)


class BajasYTraspasosTests(BaseGanado):
    def setUp(self):
        super().setUp()
        self._alta([self._animal(arete=('0007', '2484', str(n))) for n in range(1, 4)])
        self.a1, self.a2, self.a3 = Animal.objects.order_by('id')

    def test_traspaso_y_venta(self):
        respuesta = self._mover(tipo='TRASPASO', fecha='2026-10-05', rancho_id=self.gloria.pk,
                                rancho_destino_id=self.trinidad.pk, animal_ids=[self.a1.pk, self.a2.pk])
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.a1.refresh_from_db()
        self.assertEqual(self.a1.rancho, self.trinidad)

        respuesta = self._mover(tipo='BAJA', motivo='VENTA', fecha='2026-10-06', rancho_id=self.trinidad.pk,
                                animal_ids=[self.a1.pk], contraparte='Comprador', importe='15000.00')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        respuesta = self._mover(tipo='BAJA', motivo='MUERTE', fecha='2026-10-06', rancho_id=self.gloria.pk,
                                animal_ids=[self.a3.pk], notas='Enfermedad')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        estados = dict(Animal.objects.values_list('pk', 'estado'))
        self.assertEqual(estados, {self.a1.pk: VENDIDO, self.a2.pk: ACTIVO, self.a3.pk: MUERTO})

    def test_animal_de_otro_rancho_o_dado_de_baja(self):
        respuesta = self._mover(tipo='BAJA', motivo='VENTA', rancho_id=self.trinidad.pk, animal_ids=[self.a1.pk])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('no está activo en TRINIDAD', str(respuesta.data))
        self._mover(tipo='BAJA', motivo='MUERTE', rancho_id=self.gloria.pk, animal_ids=[self.a1.pk])
        respuesta = self._mover(tipo='TRASPASO', rancho_id=self.gloria.pk, rancho_destino_id=self.trinidad.pk,
                                animal_ids=[self.a1.pk])
        self.assertEqual(respuesta.status_code, 400)

    def test_no_antes_del_ultimo_movimiento(self):
        respuesta = self._mover(tipo='BAJA', motivo='VENTA', fecha='2026-09-30', rancho_id=self.gloria.pk,
                                animal_ids=[self.a1.pk])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('posterior a esta fecha', str(respuesta.data))

    def test_traspaso_al_mismo_rancho_o_sin_destino(self):
        for destino in (self.gloria.pk, None):
            respuesta = self._mover(tipo='TRASPASO', rancho_id=self.gloria.pk, rancho_destino_id=destino,
                                    animal_ids=[self.a1.pk])
            self.assertEqual(respuesta.status_code, 400)
            self.assertIn('rancho_destino_id', respuesta.data)

    def test_cancelar_solo_el_ultimo_de_cada_animal(self):
        traspaso = self._mover(tipo='TRASPASO', fecha='2026-10-05', rancho_id=self.gloria.pk,
                               rancho_destino_id=self.trinidad.pk, animal_ids=[self.a1.pk]).data
        alta_id = Animal.objects.get(pk=self.a1.pk).movimientos.order_by('id').first().pk

        respuesta = self.client.post(f'/api/ganado/movimientos/{alta_id}/cancelar/', {'motivo': 'Error'}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('posteriores', str(respuesta.data))

        respuesta = self.client.post(f"/api/ganado/movimientos/{traspaso['id']}/cancelar/", {'motivo': 'Error'},
                                     format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.a1.refresh_from_db()
        self.assertEqual(self.a1.rancho, self.gloria)

        respuesta = self.client.post(f'/api/ganado/movimientos/{alta_id}/cancelar/', {'motivo': 'Error'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(set(Animal.objects.values_list('estado', flat=True)), {CANCELADO})
        # El arete de un alta cancelada queda libre.
        self._alta([self._animal(arete=('0007', '2484', '1'))])

    def test_editar_fecha_respeta_el_orden(self):
        traspaso = self._mover(tipo='TRASPASO', fecha='2026-10-05', rancho_id=self.gloria.pk,
                               rancho_destino_id=self.trinidad.pk, animal_ids=[self.a1.pk]).data
        url = f"/api/ganado/movimientos/{traspaso['id']}/"
        self.assertEqual(self.client.patch(url, {'fecha': '2026-09-01'}, format='json').status_code, 400)
        respuesta = self.client.patch(url, {'fecha': '2026-10-03', 'notas': 'Corrección'}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(respuesta.data['fecha'], '2026-10-03')
        self.assertEqual(respuesta.data['editado_por']['id'], self.usuario.pk)
        # Los animales no se cambian al editar.
        self.client.patch(url, {'animal_ids': [self.a2.pk]}, format='json')
        self.assertEqual(respuesta.data['cabezas'], 1)


class AnimalesTests(BaseGanado):
    def test_editar_datos_deja_bitacora_e_historial(self):
        self._alta([self._animal(self.becerra, 'H', arete=('0007', '2484', '6789'))])
        animal = Animal.objects.get()
        respuesta = self.client.patch(f'/api/ganado/animales/{animal.pk}/',
                                      {'categoria_id': Categoria.objects.get(nombre='NOVILLA').pk}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(respuesta.data['categoria']['nombre'], 'NOVILLA')
        edicion = EdicionAnimal.objects.get()
        self.assertEqual(edicion.cambios, [{'campo': 'Categoría', 'antes': 'BECERRA', 'despues': 'NOVILLA'}])

        historial = self.client.get(f'/api/ganado/animales/{animal.pk}/historial/').data
        self.assertEqual([e['clase'] for e in historial['eventos']], ['edicion', 'movimiento'])

    def test_rancho_y_estado_no_se_editan(self):
        self._alta([self._animal()])
        animal = Animal.objects.get()
        self.client.patch(f'/api/ganado/animales/{animal.pk}/', {'estado': 'MUERTO'}, format='json')
        animal.refresh_from_db()
        self.assertEqual(animal.estado, ACTIVO)
        self.assertEqual(self.client.delete(f'/api/ganado/animales/{animal.pk}/').status_code, 405)

    def test_listado_filtros_y_seleccion(self):
        self._alta([self._animal(arete=('0007', '2484', '6789')), self._animal(self.becerro, 'M', cantidad=2)])
        datos = self.client.get('/api/ganado/animales/', {'q': '6789'}).data
        self.assertEqual(datos['count'], 1)
        self.assertEqual(self.client.get('/api/ganado/animales/', {'arete': 'sin'}).data['count'], 2)
        self.assertEqual(self.client.get('/api/ganado/animales/', {'sexo': 'M'}).data['count'], 2)
        seleccion = self.client.get('/api/ganado/animales/seleccion/', {'rancho': self.gloria.pk}).data
        self.assertEqual(len(seleccion), 3)


class ReportesTests(BaseGanado):
    def setUp(self):
        super().setUp()
        self._alta([self._animal(arete=('0007', '2484', '1')), self._animal(self.becerro, 'M', cantidad=2)],
                   fecha='2026-09-20')
        self.a1, self.b1, _ = Animal.objects.order_by('id')
        self._mover(tipo='TRASPASO', fecha='2026-10-02', rancho_id=self.gloria.pk,
                    rancho_destino_id=self.trinidad.pk, animal_ids=[self.a1.pk])
        self._mover(tipo='BAJA', motivo='VENTA', fecha='2026-10-03', rancho_id=self.gloria.pk, animal_ids=[self.b1.pk])
        self._alta([self._animal(self.becerra, 'H')], rancho=self.trinidad, fecha='2026-10-04', motivo='NACIMIENTO')

    def test_inventario_a_fecha(self):
        antes = self.client.get('/api/ganado/reportes/inventario/', {'fecha': '2026-10-01'}).data
        self.assertEqual([r['rancho']['nombre'] for r in antes['ranchos']], ['LA GLORIA'])
        self.assertEqual(antes['totales']['total'], 3)

        hoy = self.client.get('/api/ganado/reportes/inventario/', {'fecha': '2026-10-04', 'animales': '1'}).data
        por_rancho = {r['rancho']['nombre']: r for r in hoy['ranchos']}
        self.assertEqual(por_rancho['LA GLORIA']['total'], 1)
        self.assertEqual(por_rancho['TRINIDAD']['filas'], [
            {'especie': 'BOVINO', 'categoria': 'VACA', 'hembras': 1, 'machos': 0, 'con_arete': 1, 'sin_arete': 0, 'total': 1},
            {'especie': 'BOVINO', 'categoria': 'BECERRA', 'hembras': 1, 'machos': 0, 'con_arete': 0, 'sin_arete': 1, 'total': 1},
        ])
        self.assertEqual(len(por_rancho['TRINIDAD']['animales']), 2)

    def test_balance_de_movimientos(self):
        datos = self.client.get('/api/ganado/reportes/movimientos/', {'desde': '2026-10-01', 'hasta': '2026-10-31'}).data
        balance = {f['rancho']['nombre']: f for f in datos['balance']}
        gloria, trinidad = balance['LA GLORIA'], balance['TRINIDAD']
        self.assertEqual((gloria['inicial'], gloria['enviados'], gloria['ventas'], gloria['final']), (3, 1, 1, 1))
        self.assertEqual((trinidad['inicial'], trinidad['recibidos'], trinidad['nacimientos'], trinidad['final']),
                         (0, 1, 1, 2))
        self.assertEqual(len(datos['movimientos']), 3)

        solo = self.client.get('/api/ganado/reportes/movimientos/', {
            'desde': '2026-10-01', 'hasta': '2026-10-31', 'rancho': self.trinidad.pk, 'tipo': 'ALTA',
        }).data
        self.assertEqual([f['rancho']['nombre'] for f in solo['balance']], ['TRINIDAD'])
        self.assertEqual(len(solo['movimientos']), 1)
        self.assertEqual(self.client.get('/api/ganado/reportes/movimientos/').status_code, 400)

    def test_descargas(self):
        for ruta, params in (
            ('/api/ganado/reportes/inventario/', {'fecha': '2026-10-04'}),
            ('/api/ganado/reportes/movimientos/', {'desde': '2026-09-01', 'hasta': '2026-10-31'}),
        ):
            pdf = self.client.get(ruta + 'pdf/', params)
            self.assertEqual(pdf.status_code, 200)
            self.assertTrue(pdf.content.startswith(b'%PDF'))
            excel = self.client.get(ruta + 'excel/', params)
            self.assertEqual(excel.status_code, 200)
            libro = load_workbook(BytesIO(excel.content))
            self.assertGreaterEqual(len(libro.sheetnames), 2)
        libro = load_workbook(BytesIO(self.client.get('/api/ganado/reportes/inventario/excel/',
                                                     {'fecha': '2026-10-04'}).content))
        hoja = libro['TRINIDAD']
        self.assertEqual([hoja.cell(row=3, column=c).value for c in range(1, 6)], ['VACA', 'Hembra', '0007', '2484', '1'])
        self.assertEqual(hoja.cell(row=4, column=5).value, 'S/A')


class CatalogosTests(BaseGanado):
    def test_categoria_repetida_y_rancho_con_movimientos(self):
        respuesta = self.client.post('/api/ganado/categorias/', {
            'especie_id': self.bovino.pk, 'nombre': 'vaca ', 'sexo': 'H',
        }, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('nombre', respuesta.data)
        nuevo = self.client.post('/api/ganado/ranchos/', {'nombre': 'los cocos'}, format='json')
        self.assertEqual(nuevo.data['nombre'], 'LOS COCOS')
        self._alta([self._animal()])
        self.assertEqual(self.client.delete(f'/api/ganado/ranchos/{self.gloria.pk}/').status_code, 400)
        ranchos = {r['nombre']: r['cabezas'] for r in self.client.get('/api/ganado/ranchos/').data}
        self.assertEqual(ranchos['LA GLORIA'], 1)

    def test_solo_lectura(self):
        lector = crear_usuario_con_area('lector', AREA_GANADO, solo_lectura=True)
        self.client.force_authenticate(user=lector)
        self.assertEqual(self.client.get('/api/ganado/animales/').status_code, 200)
        self.assertEqual(self._mover(tipo='ALTA', motivo='COMPRA', rancho_id=self.gloria.pk,
                                     animales=[self._animal()]).status_code, 403)

