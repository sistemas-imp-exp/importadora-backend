"""
Sesiones con JWT: emisión, cookie del refresh, revocación y bloqueo por intentos.

- El access token viaja en el cuerpo y el frontend lo guarda solo en memoria.
- El refresh token va en una cookie httpOnly (el código de la página no puede
  leerla), SameSite=Strict y solo en /api/auth/.
- Cada token lleva la versión de sesión del usuario (claim "ver"); subirla
  invalida al momento todos los tokens emitidos antes.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from .models import EventoAcceso, Perfil

CLAIM_VERSION = 'ver'

# Bloqueo por intentos fallidos (por nombre de usuario y por equipo).
INTENTOS_POR_USUARIO = 5
INTENTOS_POR_IP = 20
VENTANA_BLOQUEO = timedelta(minutes=15)


def perfil_de(usuario):
    perfil, _ = Perfil.objects.get_or_create(usuario=usuario)
    return perfil


def version_de(usuario_id):
    return Perfil.objects.filter(usuario_id=usuario_id).values_list('version_sesion', flat=True).first() or 0


def emitir_tokens(usuario):
    refresh = RefreshToken.for_user(usuario)
    refresh[CLAIM_VERSION] = version_de(usuario.pk)
    return refresh


def poner_cookie(response, refresh):
    response.set_cookie(
        settings.AUTH_COOKIE_NAME,
        str(refresh),
        max_age=int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()),
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite='Strict',
        path=settings.AUTH_COOKIE_PATH,
    )


def borrar_cookie(response):
    response.delete_cookie(settings.AUTH_COOKIE_NAME, path=settings.AUTH_COOKIE_PATH, samesite='Strict')


def anular_refresh(refresh):
    """Lista negra de un refresh (idempotente)."""
    try:
        refresh.blacklist()
    except Exception:  # noqa: BLE001 — ya anulado o emitido sin registro
        pass


@transaction.atomic
def revocar_sesiones(usuario):
    """Cierra todas las sesiones del usuario: sube su versión y anula sus refresh."""
    perfil = perfil_de(usuario)
    Perfil.objects.filter(pk=perfil.pk).update(version_sesion=F('version_sesion') + 1)
    pendientes = OutstandingToken.objects.filter(user=usuario, expires_at__gt=timezone.now()).exclude(
        blacklistedtoken__isnull=False,
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in pendientes], ignore_conflicts=True,
    )


def ip_cliente(request):
    """IP del equipo. Detrás de IIS (o el proxy de Vite) llega en X-Forwarded-For."""
    remota = request.META.get('REMOTE_ADDR')
    reenviada = request.META.get('HTTP_X_FORWARDED_FOR')
    # Solo se confía en el encabezado si la petición viene del proxy local.
    if reenviada and remota in ('127.0.0.1', '::1'):
        return reenviada.split(',')[0].strip() or remota
    return remota


def registrar(request, evento, usuario=None, username='', detalle=''):
    EventoAcceso.objects.create(
        evento=evento,
        usuario=usuario,
        username=(username or (usuario.username if usuario else ''))[:150],
        ip=ip_cliente(request),
        navegador=request.META.get('HTTP_USER_AGENT', '')[:300],
        detalle=detalle[:200],
    )


def _fallidos_desde_ultimo_ingreso(eventos):
    """Intentos fallidos posteriores al último ingreso o desbloqueo."""
    ultimo = eventos.filter(evento__in=[EventoAcceso.INGRESO, EventoAcceso.DESBLOQUEO]).order_by('-fecha').first()
    fallidos = eventos.filter(evento=EventoAcceso.FALLIDO)
    if ultimo:
        fallidos = fallidos.filter(fecha__gt=ultimo.fecha)
    return fallidos


def minutos_de_bloqueo(username, ip):
    """Minutos que faltan para poder intentar otra vez, o None si no hay bloqueo."""
    desde = timezone.now() - VENTANA_BLOQUEO
    recientes = EventoAcceso.objects.filter(fecha__gte=desde)
    candidatos = []
    por_usuario = _fallidos_desde_ultimo_ingreso(recientes.filter(username__iexact=username))
    if por_usuario.count() >= INTENTOS_POR_USUARIO:
        candidatos.append(por_usuario.order_by('-fecha')[INTENTOS_POR_USUARIO - 1].fecha)
    if ip:
        por_ip = recientes.filter(ip=ip, evento=EventoAcceso.FALLIDO)
        if por_ip.count() >= INTENTOS_POR_IP:
            candidatos.append(por_ip.order_by('-fecha')[INTENTOS_POR_IP - 1].fecha)
    if not candidatos:
        return None
    libera = max(candidatos) + VENTANA_BLOQUEO
    segundos = (libera - timezone.now()).total_seconds()
    return max(1, int(-(-segundos // 60)))


def esta_bloqueado(usuario):
    """Para la tabla de Usuarios: bloqueado por intentos en este momento."""
    return minutos_de_bloqueo(usuario.username, None) is not None
