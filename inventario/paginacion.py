from rest_framework.pagination import PageNumberPagination


class PaginacionInventario(PageNumberPagination):
    """
    Paginación de los listados grandes de inventario.

    Entradas y Salidas devolvían la tabla completa (2 MB y miles de objetos
    construidos en Python por petición). El costo no estaba en la base —la
    consulta tarda milisegundos— sino en serializar y transferir todo.

    Se aplica solo en esos dos ViewSets, no globalmente: los catálogos y las
    pantallas que necesitan la lista entera siguen respondiendo un arreglo.
    """
    page_size = 25
    page_size_query_param = 'por_pagina'
    max_page_size = 200
