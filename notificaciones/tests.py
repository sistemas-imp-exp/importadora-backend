from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from security.models import Area
from security.testing import crear_usuario_con_area

from .models import CanalArea, Notificacion
from .servicio import notificar

User = get_user_model()
URL = "/api/notificaciones/"


class NotificacionesTests(APITestCase):
    def setUp(self):
        self.cobranza = crear_usuario_con_area("cobranza", "CAR")
        self.almacen = crear_usuario_con_area("almacen", "INV")
        self.caja = crear_usuario_con_area("caja", "TES")
        self.admin = User.objects.create_superuser("admin", password="x")
        # La migración 0002 ya crea el canal; en tests las áreas pueden venir de crear_usuario_con_area.
        self.car, self.inv = Area.objects.get(codigo="CAR"), Area.objects.get(codigo="INV")
        CanalArea.objects.get_or_create(area_a=self.car, area_b=self.inv)

    def _como(self, usuario):
        self.client.force_authenticate(user=usuario)

    def _mensaje(self, origen="CAR", destino="INV", **extra):
        return self.client.post(URL, {
            "area_origen": origen, "area_destino": destino, "titulo": "Nota 512",
            "mensaje": "¿Me confirman los kilos?", **extra,
        }, format="json")

    def test_mensaje_entre_areas_con_canal(self):
        self._como(self.cobranza)
        r = self._mensaje(enlace="/inventario/salidas?busqueda=SI9510")
        self.assertEqual(r.status_code, 201)

        self._como(self.almacen)
        resumen = self.client.get(f"{URL}resumen/").data
        self.assertEqual(resumen["no_leidas"], 1)
        self.assertEqual(resumen["recientes"][0]["titulo"], "Nota 512")
        # Quien lo escribió no lo tiene como pendiente.
        self._como(self.cobranza)
        self.assertEqual(self.client.get(f"{URL}resumen/").data["no_leidas"], 0)

    def test_sin_canal_o_sin_area_no_se_puede_escribir(self):
        self._como(self.caja)
        self.assertEqual(self._mensaje(origen="TES").status_code, 400)  # TES no tiene canal
        self.assertEqual(self._mensaje().status_code, 403)  # no es de Cartera
        solo_lectura = crear_usuario_con_area("consulta", "CAR", solo_lectura=True)
        self._como(solo_lectura)
        self.assertEqual(self._mensaje().status_code, 403)

    def test_enlace_solo_rutas_internas(self):
        self._como(self.cobranza)
        self.assertEqual(self._mensaje(enlace="javascript:alert(1)").status_code, 400)
        self.assertEqual(self._mensaje(enlace="https://otro.com").status_code, 400)
        self.assertEqual(self._mensaje(enlace="//otro.com").status_code, 400)

    def test_cada_area_ve_lo_suyo_y_el_superusuario_todo(self):
        self._como(self.cobranza)
        self._mensaje()
        self._como(self.caja)
        self.assertEqual(self.client.get(URL).data["count"], 0)
        self._como(self.admin)
        self.assertEqual(self.client.get(URL).data["count"], 1)
        self.assertEqual(self.client.get(f"{URL}resumen/").data["no_leidas"], 1)
        hilo = Notificacion.objects.get().pk
        self._como(self.caja)
        self.assertEqual(self.client.get(f"{URL}{hilo}/").status_code, 404)

    def test_responder_va_al_area_contraria_y_leer_es_por_usuario(self):
        self._como(self.cobranza)
        hilo = self._mensaje().data["id"]
        self._como(self.almacen)
        detalle = self.client.get(f"{URL}{hilo}/").data
        self.assertEqual([a["codigo"] for a in detalle["responder_como"]], ["INV"])
        r = self.client.post(f"{URL}{hilo}/responder/", {"mensaje": "Ya se corrigió"}, format="json")
        self.assertEqual((r.data["area_origen"]["codigo"], r.data["area_destino"]["codigo"]), ("INV", "CAR"))
        self.assertEqual(self.client.get(f"{URL}resumen/").data["no_leidas"], 0)  # responder marca leído

        otro_almacen = crear_usuario_con_area("almacen2", "INV")
        self._como(otro_almacen)
        self.assertEqual(self.client.get(f"{URL}resumen/").data["no_leidas"], 1)  # su propio leído

        self._como(self.cobranza)
        listado = self.client.get(URL).data["results"]
        self.assertEqual((listado[0]["respuestas"], listado[0]["no_leidas"]), (1, 1))
        self.client.post(f"{URL}{hilo}/leer/")
        self.assertEqual(self.client.get(URL, {"no_leidas": "1"}).data["count"], 0)

    def test_superusuario_en_ambas_areas_elige_desde_cual_responde(self):
        self._como(self.cobranza)
        hilo = self._mensaje().data["id"]
        self._como(self.admin)
        self.assertEqual(self.client.post(f"{URL}{hilo}/responder/", {"mensaje": "x"}, format="json").status_code, 400)
        r = self.client.post(f"{URL}{hilo}/responder/", {"mensaje": "x", "area_origen": "INV"}, format="json")
        self.assertEqual(r.status_code, 201)

    def test_solo_lectura_puede_marcar_leido(self):
        self._como(self.cobranza)
        hilo = self._mensaje().data["id"]
        consulta = crear_usuario_con_area("consulta", "INV", solo_lectura=True)
        self._como(consulta)
        self.assertEqual(self.client.post(f"{URL}{hilo}/leer/").status_code, 204)
        self.assertEqual(self.client.post(f"{URL}leer-todas/").status_code, 204)

    def test_aviso_sin_canal_activo_no_se_crea(self):
        CanalArea.objects.update(activo=False)
        self.assertIsNone(notificar("CAR", "INV", "prueba", "x"))
        CanalArea.objects.update(activo=True)
        self.assertIsNotNone(notificar("CAR", "INV", "prueba", "x"))

    def test_canales_disponibles(self):
        self._como(self.cobranza)
        canales = self.client.get(f"{URL}canales/").data
        self.assertEqual([(c["origen"]["codigo"], c["destino"]["codigo"]) for c in canales], [("CAR", "INV")])
        self._como(self.caja)
        self.assertEqual(self.client.get(f"{URL}canales/").data, [])
