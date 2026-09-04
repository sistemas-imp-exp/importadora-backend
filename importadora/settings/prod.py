from .base import *

DEBUG = False

# ALLOWED_HOSTS y CORS_ALLOWED_ORIGINS vienen del .env (ver base.py). Tenerlos
# aqui escritos a mano significaba que el host de produccion vivia en el
# repositorio, y seguia teniendo el "tu-dominio.com" de la plantilla.

DATABASES = {
    "default": {
        "ENGINE": "mssql",
        "NAME": env("NAME"),
        "USER": env("USER"),
        "PASSWORD": env("PASSWORD"),
        "HOST": env("HOST", default="localhost"),
        "PORT": env("PORT", default="1433"),
        "OPTIONS": {
            "driver": env("DRIVER"),
            "extra_params": "Encrypt=no;TrustServerCertificate=yes",
        },
    }
}
