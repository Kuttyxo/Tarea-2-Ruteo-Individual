"""Lector mínimo de shapefiles de polilíneas (tipo 3) y de su tabla .dbf, sin dependencias externas.

Sigue la especificación técnica de ESRI (1998): cabecera de 100 bytes, registros con cabecera big-endian
(número y largo en palabras de 16 bits) y contenido little-endian (tipo, caja, partes y puntos).
"""
import os
import struct


def leer_dbf(ruta):
    """Devuelve la lista de registros de la tabla .dbf como diccionarios {campo: texto}."""
    with open(ruta, "rb") as f:
        d = f.read()
    n_reg, largo_cab, largo_reg = struct.unpack("<IHH", d[4:12])
    campos, pos = [], 32
    while d[pos] != 0x0D:
        nombre = d[pos:pos + 11].split(b"\x00")[0].decode("latin-1")
        campos.append((nombre, d[pos + 16]))
        pos += 32
    registros = []
    for k in range(n_reg):
        r = d[largo_cab + k * largo_reg: largo_cab + (k + 1) * largo_reg]
        off, fila = 1, {}
        for nombre, ancho in campos:
            fila[nombre] = r[off:off + ancho].decode("latin-1").strip()
            off += ancho
        registros.append(fila)
    return registros


def leer_polilineas(ruta_shp):
    """Devuelve [(atributos, [parte1, parte2, ...]), ...] con cada parte como lista de (lon, lat)."""
    with open(ruta_shp, "rb") as f:
        d = f.read()
    if struct.unpack(">i", d[0:4])[0] != 9994:
        raise ValueError(f"{ruta_shp} no es un shapefile")
    tipo = struct.unpack("<i", d[32:36])[0]
    if tipo not in (3, 13, 23):
        raise ValueError(f"{ruta_shp}: tipo de geometría {tipo}, se esperaba polilínea (3)")
    geoms, pos = [], 100
    while pos + 8 <= len(d):
        _, largo = struct.unpack(">ii", d[pos:pos + 8])
        c = d[pos + 8: pos + 8 + 2 * largo]
        pos += 8 + 2 * largo
        if struct.unpack("<i", c[:4])[0] == 0:          # geometría nula
            geoms.append([])
            continue
        n_partes, n_puntos = struct.unpack("<ii", c[36:44])
        inicios = list(struct.unpack(f"<{n_partes}i", c[44:44 + 4 * n_partes])) + [n_puntos]
        off = 44 + 4 * n_partes
        puntos = [struct.unpack("<2d", c[off + 16 * i: off + 16 * i + 16]) for i in range(n_puntos)]
        geoms.append([puntos[inicios[k]:inicios[k + 1]] for k in range(n_partes)])
    ruta_dbf = os.path.splitext(ruta_shp)[0] + ".dbf"
    atributos = leer_dbf(ruta_dbf) if os.path.exists(ruta_dbf) else [{} for _ in geoms]
    if len(atributos) != len(geoms):
        raise ValueError(f"{ruta_shp}: {len(geoms)} geometrías y {len(atributos)} filas en el .dbf")
    return list(zip(atributos, geoms))
