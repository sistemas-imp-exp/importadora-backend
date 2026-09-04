from .base import *

DEBUG = True

# ALLOWED_HOSTS y CORS_ALLOWED_ORIGINS vienen del .env (ver base.py). Antes se
# repetian aqui con la IP de la LAN escrita a mano, asi que cambiar de red
# obligaba a tocar codigo en tres archivos distintos.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
