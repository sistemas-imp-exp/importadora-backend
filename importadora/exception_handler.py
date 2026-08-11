from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def custom_exception_handler(exc, context):
    """
    Garantiza que cualquier error de la API regrese JSON legible para el
    usuario, nunca la página técnica de Django (aunque DEBUG esté activo):

    - Los ValidationError de Django (los que lanzan los modelos en full_clean())
      no pasan por el manejador de DRF por defecto; aquí se normalizan a 400.
    - Cualquier otro error no controlado (bugs, IntegrityError, etc.) se
      convierte en un 500 genérico en vez de dejar escapar detalles técnicos.
    """
    if isinstance(exc, DjangoValidationError):
        if hasattr(exc, "message_dict"):
            detail = {
                ("non_field_errors" if clave == NON_FIELD_ERRORS else clave): valor
                for clave, valor in exc.message_dict.items()
            }
        else:
            detail = {"non_field_errors": exc.messages}
        return Response(detail, status=status.HTTP_400_BAD_REQUEST)

    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    return Response(
        {"detail": "Ocurrió un error inesperado en el servidor. Intenta de nuevo o contacta a soporte."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
