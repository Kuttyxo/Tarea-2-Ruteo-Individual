"""Punto 1: carga la fibra óptica detectada del proyecto Yafün (estudio NIC Chile / CORFO / Subtel) a PostGIS.

Fuentes disponibles:
  - "transparencia" (POR DEFECTO): shapefile oficial que Subtel entregó por Transparencia (solicitud AN002T0005610,
    carpeta Respuesta_T5610), publicado por el ayudante del curso para la tarea.
  - "internet_archive": la misma capa tomada del mapa Recorrido Yafün de Subtel, copia del Internet Archive (20-05-2024).
    Se conserva solo para comparar con la primera versión del trabajo.
"""
import json
import os

from psycopg2.extras import execute_values

from .db import DATA_DIR, ejecutar_archivo_sql
from .shp import leer_polilineas

FUENTES = {
    "transparencia": os.path.join(DATA_DIR, "transparencia", "Respuesta_T5610", "fibra_optica_detectada.shp"),
    "internet_archive": os.path.join(DATA_DIR, "yafun", "Fibrapticadetectada_3.js"),
}
FUENTE_POR_DEFECTO = "transparencia"


def leer_fibra(fuente=FUENTE_POR_DEFECTO):
    """Devuelve [(id_original, [parte1, parte2, ...]), ...] con las coordenadas (lon, lat) de cada polilínea."""
    ruta = FUENTES[fuente]
    if fuente == "transparencia":
        return [(attrs.get("id"), partes) for attrs, partes in leer_polilineas(ruta)]
    with open(ruta, encoding="utf-8") as f:                    # .js de qgis2web con un FeatureCollection
        t = f.read()
    fc = json.loads(t[t.index("{"): t.rindex("}") + 1])
    salida = []
    for ft in fc["features"]:
        g = ft["geometry"]
        if g is None:
            continue
        partes = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        salida.append((ft["properties"].get("id"), [[tuple(p[:2]) for p in ls] for ls in partes]))
    return salida


def cargar_fibra(con, fuente=FUENTE_POR_DEFECTO):
    ejecutar_archivo_sql(con, "01_esquema_infra.sql")
    filas = []
    for fid, partes in leer_fibra(fuente):
        for k, coords in enumerate(partes, start=1):
            if len(coords) < 2:
                continue
            filas.append((str(fid), k, fuente, json.dumps({"type": "LineString", "coordinates": [list(c) for c in coords]})))
    with con.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO fibra_raw (feature_id, parte, fuente, geom) VALUES %s",
            filas,
            template="(%s, %s, %s, ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))",
            page_size=500,
        )
        cur.execute("SELECT count(*), round((sum(ST_Length(geom::geography)) / 1000)::numeric, 1) FROM fibra_raw")
        n, km = cur.fetchone()
    con.commit()
    return n, km
