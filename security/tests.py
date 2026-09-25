import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import URLResolver
from rest_framework.test import APITestCase

import inventario.api_urls
import treasury.api_urls
from treasury.models import CorteCaja

from .models import Area
from .permissions import AREA_INVENTARIO, AREA_TESORERIA
from .testing import crear_usuario_con_area


def _rutas_concretas(modulo_urls, prefijo):
    """
    Todas las rutas de un módulo de URLs (vistas sueltas + las del router) con
    sus parámetros rellenados, para poder pedirlas con el cliente de pruebas.
    Se omiten las variantes `.json`/`.api` y la raíz navegable del router.
    """
    patrones = []
    for patron in modulo_urls.urlpatterns:
        if isinstance(patron, URLResolver):
            patrones.extend(str(sub.pattern) for sub in patron.url_patterns)
        else:
            patrones.append(str(patron.pattern))

    rutas = []
    for patron in patrones:
        if "format" in patron:
            continue
        ruta = patron.lstrip("^").rstrip("$")
        ruta = re.sub(r"\(\?P<\w+>[^)]*\)", "1", ruta)  # (?P<pk>[^/.]+) del router
        ruta = re.sub(r"<(?:\w+:)?\w+>", "1", ruta)  # <int:arqueo_id> de path()
        if ruta:
            rutas.append(prefijo + ruta)
    return sorted(set(rutas))


RUTAS_TESORERIA = _rutas_concretas(treasury.api_urls, "/api/treasury/")
RUTAS_INVENTARIO = _rutas_concretas(inventario.api_urls, "/api/inventario/")


class AreasBaseMigracionTests(TestCase):
    def test_la_migracion_crea_las_areas_de_las_que_depende_el_ruteo(self):
        codigos = set(Area.objects.values_list("codigo", flat=True))
        self.assertTrue({"TES", "INV", "ADMIN"} <= codigos)


class PermisosPorAreaApiTests(APITestCase):
    """
    Recorre TODAS las rutas de cada módulo: si alguien agrega un endpoint sin
    la permission class del área, este test lo detecta.
    """

    def test_se_encontraron_rutas_que_revisar(self):
        # Cordura del recorrido: si el parseo de patrones se rompe, los demás
        # tests pasarían en vacío.
        self.assertGreater(len(RUTAS_TESORERIA), 20)
        self.assertGreater(len(RUTAS_INVENTARIO), 20)

    def _assert_todas_prohibidas(self, rutas, usuario):
        self.client.force_authenticate(user=usuario)
        for ruta in rutas:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 403)

    def test_usuario_sin_areas_no_entra_a_ningun_endpoint(self):
        usuario = crear_usuario_con_area("sin_area")
        self._assert_todas_prohibidas(RUTAS_TESORERIA + RUTAS_INVENTARIO, usuario)

    def test_inventario_no_entra_a_tesoreria(self):
        self._assert_todas_prohibidas(RUTAS_TESORERIA, crear_usuario_con_area("almacen", AREA_INVENTARIO))

    def test_tesoreria_no_entra_a_inventario(self):
        self._assert_todas_prohibidas(RUTAS_INVENTARIO, crear_usuario_con_area("cajero", AREA_TESORERIA))

    def test_anonimo_recibe_401(self):
        for ruta in (RUTAS_TESORERIA[0], RUTAS_INVENTARIO[0]):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 401)

    def test_cada_area_entra_a_su_modulo(self):
        self.client.force_authenticate(user=crear_usuario_con_area("cajero", AREA_TESORERIA))
        self.assertEqual(self.client.get("/api/treasury/divisas/").status_code, 200)
        self.assertEqual(self.client.get("/api/treasury/reportes/movimientos/").status_code, 400)  # pasa el permiso; falta el rango

        self.client.force_authenticate(user=crear_usuario_con_area("almacen", AREA_INVENTARIO))
        self.assertEqual(self.client.get("/api/inventario/camaras/").status_code, 200)
        self.assertEqual(self.client.get("/api/inventario/existencias/").status_code, 200)

    def test_area_desactivada_no_da_acceso_ni_aparece_en_me(self):
        usuario = crear_usuario_con_area("cajero", AREA_TESORERIA)
        Area.objects.filter(codigo=AREA_TESORERIA).update(activo=False)
        self.client.force_authenticate(user=usuario)

        self.assertEqual(self.client.get("/api/treasury/divisas/").status_code, 403)
        self.assertEqual(self.client.get("/api/auth/me/").data["areas"], [])

    def test_superusuario_entra_a_ambos_modulos_sin_areas(self):
        admin = get_user_model().objects.create_superuser(username="admin", password="x")
        self.client.force_authenticate(user=admin)
        self.assertEqual(self.client.get("/api/treasury/divisas/").status_code, 200)
        self.assertEqual(self.client.get("/api/inventario/camaras/").status_code, 200)


class RegistroPublicoCerradoTests(APITestCase):
    def test_registro_ya_no_existe(self):
        payload = {"username": "intruso", "password": "Clave-segura-123", "password2": "Clave-segura-123"}
        self.assertEqual(self.client.post("/api/auth/registro/", payload, format="json").status_code, 404)
        self.assertFalse(get_user_model().objects.filter(username="intruso").exists())


class PantallasLegadasCoreTests(TestCase):
    RUTAS = ["/", "/apertura/", "/movimiento/", "/movimientos/", "/cierre/", "/divisas/"]

    def test_sin_sesion_redirige_al_login(self):
        for ruta in self.RUTAS:
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(ruta)
                self.assertEqual(respuesta.status_code, 302)
                self.assertTrue(respuesta["Location"].startswith("/login/?next="))

    def test_post_anonimo_no_abre_corte(self):
        self.client.post("/apertura/", {"fecha": "2026-09-25", "responsable_apertura": "x"})
        self.assertFalse(CorteCaja.objects.exists())

    def test_con_sesion_pero_sin_tesoreria_da_403(self):
        self.client.force_login(crear_usuario_con_area("almacen", AREA_INVENTARIO))
        for ruta in self.RUTAS:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 403)

    def test_con_tesoreria_entra(self):
        self.client.force_login(crear_usuario_con_area("cajero", AREA_TESORERIA))
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/divisas/").status_code, 200)

    def test_login_legado_inicia_sesion_y_redirige(self):
        crear_usuario_con_area("cajero", AREA_TESORERIA, password="Clave-segura-123")
        self.assertEqual(self.client.get("/login/").status_code, 200)
        respuesta = self.client.post("/login/", {"username": "cajero", "password": "Clave-segura-123"})
        self.assertRedirects(respuesta, "/")
