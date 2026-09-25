"""
ASGI config for importadora project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

# Por defecto produccion: wsgi/asgi solo los usa el servidor (waitress bajo NSSM,
# que no define DJANGO_SETTINGS_MODULE). En desarrollo se usa manage.py -> dev.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'importadora.settings.prod')

application = get_asgi_application()
