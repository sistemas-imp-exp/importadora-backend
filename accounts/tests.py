from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from security.testing import crear_usuario_con_area

from .models import EventoAcceso, Perfil

User = get_user_model()
PASSWORD = "Camaron-2026!"
COOKIE = settings.AUTH_COOKIE_NAME


class BaseSesion(APITestCase):
    def setUp(self):
        self.usuario = crear_usuario_con_area("cartera1", "CAR", password=PASSWORD)

    def _login(self, username="cartera1", password=PASSWORD):
        return self.client.post("/api/auth/login/", {"username": username, "password": password}, format="json")

    def _refresh(self):
        return self.client.post("/api/auth/refresh/")

    def _bearer(self, access):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


class LoginTests(BaseSesion):
    def test_el_refresh_va_en_cookie_httponly_y_no_en_el_cuerpo(self):
        r = self._login()
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("refresh", r.data)
        self.assertIn("access", r.data)
        cookie = r.cookies[COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(cookie["path"], "/api/auth/")
        self.assertTrue(EventoAcceso.objects.filter(evento=EventoAcceso.INGRESO, usuario=self.usuario).exists())

    def test_mismo_mensaje_para_usuario_inexistente_inactivo_o_mala_contrasena(self):
        inactivo = crear_usuario_con_area("baja", "CAR", password=PASSWORD)
        inactivo.is_active = False
        inactivo.save()
        mensajes = {
            self._login(password="otra").data["detail"],
            self._login(username="noexiste").data["detail"],
            self._login(username="baja").data["detail"],
        }
        self.assertEqual(mensajes, {"Usuario o contraseña incorrectos."})

    def test_bloqueo_tras_cinco_intentos_y_desbloqueo_por_superusuario(self):
        for _ in range(5):
            self.assertEqual(self._login(password="mal").status_code, 400)
        r = self._login()  # aun con la contraseña correcta
        self.assertEqual(r.status_code, 429)
        self.assertIn("Intenta de nuevo en", r.data["detail"])
        self.assertTrue(EventoAcceso.objects.filter(evento=EventoAcceso.BLOQUEADO).exists())

        admin = User.objects.create_superuser("admin", password=PASSWORD)
        self.client.force_authenticate(user=admin)
        self.assertTrue(self.client.get(f"/api/auth/usuarios/{self.usuario.id}/").data["bloqueado"])
        self.client.post(f"/api/auth/usuarios/{self.usuario.id}/desbloquear/")
        self.client.force_authenticate(user=None)
        self.assertEqual(self._login().status_code, 200)

    def test_el_bloqueo_vence_a_los_15_minutos(self):
        for _ in range(5):
            self._login(password="mal")
        EventoAcceso.objects.update(fecha=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self._login().status_code, 200)

    def test_un_ingreso_correcto_reinicia_el_conteo(self):
        for _ in range(4):
            self._login(password="mal")
        self.assertEqual(self._login().status_code, 200)
        for _ in range(4):
            self._login(password="mal")
        self.assertEqual(self._login().status_code, 200)


class RefreshYLogoutTests(BaseSesion):
    def test_refresh_con_cookie_rota_y_anula_el_anterior(self):
        self._login()
        anterior = self.client.cookies[COOKIE].value
        r = self._refresh()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["user"]["username"], "cartera1")
        self.assertNotEqual(self.client.cookies[COOKIE].value, anterior)
        self.assertEqual(BlacklistedToken.objects.count(), 1)

    def test_sin_cookie_no_hay_sesion(self):
        self.assertEqual(self._refresh().status_code, 401)

    def test_reuso_dentro_del_margen_se_acepta(self):
        self._login()
        anterior = self.client.cookies[COOKIE].value
        self._refresh()
        self.client.cookies[COOKIE] = anterior  # otra pestaña con la cookie de hace un instante
        self.assertEqual(self._refresh().status_code, 200)

    def test_reuso_fuera_del_margen_cierra_todas_las_sesiones(self):
        self._login()
        anterior = self.client.cookies[COOKIE].value
        r = self._refresh()
        vigente = r.data["access"]
        BlacklistedToken.objects.update(blacklisted_at=timezone.now() - timedelta(minutes=5))
        self.client.cookies[COOKIE] = anterior
        r = self._refresh()
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.data["code"], "sesion_revocada")
        self._bearer(vigente)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)
        self.assertTrue(EventoAcceso.objects.filter(evento=EventoAcceso.REUSO_TOKEN).exists())

    def test_logout_anula_el_refresh_y_borra_la_cookie(self):
        self._login()
        cookie = self.client.cookies[COOKIE].value
        r = self.client.post("/api/auth/logout/")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.cookies[COOKIE].value, "")
        self.client.cookies[COOKIE] = cookie
        BlacklistedToken.objects.update(blacklisted_at=timezone.now() - timedelta(minutes=5))
        self.assertEqual(self._refresh().status_code, 401)
        self.assertTrue(EventoAcceso.objects.filter(evento=EventoAcceso.SALIDA, usuario=self.usuario).exists())

    def test_logout_por_inactividad_queda_en_bitacora(self):
        self._login()
        self.client.post("/api/auth/logout/", {"motivo": "inactividad"}, format="json")
        self.assertTrue(EventoAcceso.objects.filter(evento=EventoAcceso.INACTIVIDAD).exists())


class RevocacionTests(BaseSesion):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("admin", password=PASSWORD)

    def _sesion(self):
        access = self._login().data["access"]
        self._bearer(access)
        return access

    def _como_admin(self, metodo, url, datos=None):
        from rest_framework.test import APIClient
        cliente = APIClient()
        cliente.force_authenticate(user=self.admin)
        return getattr(cliente, metodo)(url, datos or {}, format="json")

    def test_cerrar_sesiones_invalida_el_access_al_momento(self):
        self._sesion()
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)
        self._como_admin("post", f"/api/auth/usuarios/{self.usuario.id}/cerrar-sesiones/")
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)
        self.assertEqual(self._refresh().status_code, 401)

    def test_desactivar_cierra_sus_sesiones(self):
        self._sesion()
        self._como_admin("patch", f"/api/auth/usuarios/{self.usuario.id}/", {"is_active": False})
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_cambiar_mi_contrasena_cierra_las_otras_y_deja_esta(self):
        otra = self._login().data["access"]
        self._sesion()
        r = self.client.post("/api/auth/me/password/", {
            "password_actual": PASSWORD, "password_nueva": "Langosta-2027!", "password_nueva2": "Langosta-2027!",
        }, format="json")
        self.assertEqual(r.status_code, 200)
        self._bearer(r.data["access"])
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)
        self._bearer(otra)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_contrasena_provisional_obliga_a_cambiarla(self):
        r = self._como_admin("patch", f"/api/auth/usuarios/{self.usuario.id}/", {"password": "Provisional-99"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["debe_cambiar_password"])
        access = self._login(password="Provisional-99").data["access"]
        self._bearer(access)
        self.assertTrue(self.client.get("/api/auth/me/").data["debe_cambiar_password"])
        r = self.client.get("/api/cartera/clientes/")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data["code"], "cambio_password_requerido")

        r = self.client.post("/api/auth/me/password/", {
            "password_actual": "Provisional-99", "password_nueva": "Langosta-2027!", "password_nueva2": "Langosta-2027!",
        }, format="json")
        self._bearer(r.data["access"])
        self.assertFalse(r.data["user"]["debe_cambiar_password"])
        self.assertEqual(self.client.get("/api/cartera/clientes/").status_code, 200)

    def test_usuario_nuevo_empieza_con_contrasena_provisional(self):
        r = self._como_admin("post", "/api/auth/usuarios/", {
            "username": "nuevo", "password": "Provisional-99", "areas": ["CAR"],
        })
        self.assertEqual(r.status_code, 201)
        self.assertTrue(Perfil.objects.get(usuario__username="nuevo").debe_cambiar_password)

    def test_minimo_diez_caracteres(self):
        r = self._como_admin("post", "/api/auth/usuarios/", {"username": "corto", "password": "Ab-12345"})
        self.assertEqual(r.status_code, 400)


class BitacoraTests(BaseSesion):
    def test_solo_superusuario(self):
        self._login(password="mal")
        self.client.force_authenticate(user=self.usuario)
        self.assertEqual(self.client.get("/api/auth/bitacora/").status_code, 403)
        admin = User.objects.create_superuser("admin", password=PASSWORD)
        self.client.force_authenticate(user=admin)
        r = self.client.get("/api/auth/bitacora/", {"evento": "fallido"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["username"], "cartera1")

    @override_settings(AUTH_COOKIE_SECURE=True)
    def test_cookie_secure_configurable(self):
        self.assertTrue(self._login().cookies[COOKIE]["secure"])
