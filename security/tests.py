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


class SoloLecturaApiTests(APITestCase):
    """
    Un área asignada en solo lectura deja consultar y descargar (GET) todo el
    módulo, pero ninguna escritura. Recorre todas las rutas, igual que
    PermisosPorAreaApiTests, para que un endpoint nuevo no se escape.
    """

    # Auditoría de entradas es de superusuario (EsSuperusuario), no del área.
    RUTAS_LECTURA = [r for r in RUTAS_INVENTARIO if "auditoria" not in r]

    def setUp(self):
        self.lector = crear_usuario_con_area("lector", AREA_INVENTARIO, solo_lectura=True)
        self.client.force_authenticate(user=self.lector)

    def test_puede_consultar_y_descargar_todo_el_modulo(self):
        for ruta in self.RUTAS_LECTURA:
            with self.subTest(ruta=ruta):
                self.assertNotEqual(self.client.get(ruta).status_code, 403)

    def test_ninguna_escritura_esta_permitida(self):
        for ruta in RUTAS_INVENTARIO:
            for metodo in ("post", "put", "patch", "delete"):
                with self.subTest(ruta=ruta, metodo=metodo):
                    self.assertEqual(getattr(self.client, metodo)(ruta, {}, format="json").status_code, 403)

    def test_el_mensaje_explica_que_es_solo_lectura(self):
        respuesta = self.client.post("/api/inventario/camaras/", {"nombre": "NUEVA", "tipo": "propia"}, format="json")
        self.assertEqual(respuesta.status_code, 403)
        self.assertIn("solo lectura", str(respuesta.data["detail"]))

    def test_quitar_solo_lectura_devuelve_la_escritura(self):
        self.lector.areas.update(solo_lectura=False)
        respuesta = self.client.post("/api/inventario/camaras/", {"nombre": "NUEVA", "tipo": "propia"}, format="json")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)

    def test_me_informa_las_areas_de_solo_lectura(self):
        datos = self.client.get("/api/auth/me/").data
        self.assertEqual(datos["areas"], [AREA_INVENTARIO])
        self.assertEqual(datos["areas_solo_lectura"], [AREA_INVENTARIO])


class AsignarSoloLecturaApiTests(APITestCase):
    def setUp(self):
        admin = get_user_model().objects.create_superuser(username="admin", password="x")
        self.client.force_authenticate(user=admin)

    def _crear(self, **extra):
        payload = {"username": "nuevo", "password": "Clave-segura-123", "areas": [AREA_INVENTARIO], **extra}
        return self.client.post("/api/auth/usuarios/", payload, format="json")

    def test_superusuario_asigna_un_area_en_solo_lectura(self):
        respuesta = self._crear(areas_solo_lectura=[AREA_INVENTARIO])

        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data["areas_solo_lectura"], [AREA_INVENTARIO])
        usuario = get_user_model().objects.get(username="nuevo")
        self.assertTrue(usuario.areas.get().solo_lectura)

        # Editar sin mandar la lista la deja igual; mandarla vacía devuelve la escritura.
        self.client.patch(f"/api/auth/usuarios/{usuario.id}/", {"first_name": "X"}, format="json")
        self.assertTrue(usuario.areas.get().solo_lectura)
        self.client.patch(f"/api/auth/usuarios/{usuario.id}/", {"areas_solo_lectura": []}, format="json")
        self.assertFalse(usuario.areas.get().solo_lectura)

    def test_solo_lectura_en_area_no_asignada_se_rechaza(self):
        respuesta = self._crear(areas_solo_lectura=[AREA_TESORERIA])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("areas_solo_lectura", respuesta.data)


class PantallasLegadasSoloLecturaTests(TestCase):
    def test_con_tesoreria_en_solo_lectura_ve_pero_no_abre_corte(self):
        self.client.force_login(crear_usuario_con_area("cajero", AREA_TESORERIA, solo_lectura=True))
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.post("/apertura/", {"fecha": "2026-09-25"}).status_code, 403)
        self.assertFalse(CorteCaja.objects.exists())
