from .base import *

DEBUG = False

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "192.168.1.128",
    "tu-dominio.com",
]

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
