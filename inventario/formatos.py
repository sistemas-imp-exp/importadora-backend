"""
Formato de kilos y precios por kilo en reportes (PDF y Excel).

Kilos se guardan con 3 decimales y precios por kilo con 4; se muestran con al
menos 2 y sin ceros de más: 100 → "100.00", 9.072 → "9.072", 98.7654 → "$98.7654".
Los importes en pesos siguen en centavos (cada reporte los formatea).
"""

FORMATO_KG = "#,##0.00#"
FORMATO_PRECIO_KG = '"$"#,##0.00##'


def _minimo_dos(texto):
    entero, _, fraccion = texto.partition(".")
    return f"{entero}.{fraccion.rstrip('0').ljust(2, '0')}"


def kg(valor):
    return _minimo_dos(f"{valor:,.3f}")


def precio_kg(valor):
    return "$" + _minimo_dos(f"{valor:,.4f}")
