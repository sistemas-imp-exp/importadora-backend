from .base import *

DEBUG = False

# PostgreSQL. Las variables llevan prefijo DB_ porque nombres genéricos como
# USER o NAME pueden venir ya definidos en el entorno del sistema, y esos ganan
# sobre el .env (django-environ no los sobrescribe).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
    }
}
