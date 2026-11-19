"""Punto 5: descarga el sismo M 8,8 del USGS (GeoJSON) y lo carga a la base."""
import json
import os
import urllib.request
from datetime import datetime, timezone

from .db import DATA_DIR, consultar, ejecutar_archivo_sql

DIR_USGS = os.path.join(DATA_DIR, "usgs")
UA = "tarea-ruteo-resiliente/1.0 (trabajo academico)"
# Misma consulta que hace el buscador https://earthquake.usgs.gov/earthquakes/search/ (formato GeoJSON)
URL_BUSQUEDA = ("https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
                "&starttime=2010-02-27&endtime=2010-02-28&minmagnitude=8.5")
ARCHIVO_BUSQUEDA = os.path.join(DIR_USGS, "maule_2010_usgs.geojson")
EVENTO_ID = "official20100227063411530_30"


def descargar_sismo(forzar=False):
    os.makedirs(DIR_USGS, exist_ok=True)
    if os.path.exists(ARCHIVO_BUSQUEDA) and not forzar:
        return "ya existe", os.path.getsize(ARCHIVO_BUSQUEDA)
    req = urllib.request.Request(URL_BUSQUEDA, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        datos = r.read()
    with open(ARCHIVO_BUSQUEDA, "wb") as f:
        f.write(datos)
    return "descargado", len(datos)


def cargar_sismo(con):
    """Crea la tabla sismo y carga el GeoJSON. Devuelve las filas cargadas."""
    ejecutar_archivo_sql(con, "04_sismo.sql")
    with open(ARCHIVO_BUSQUEDA, encoding="utf-8") as f:
        fc = json.load(f)
    with con.cursor() as cur:
        for ft in fc["features"]:
            p = ft["properties"]
            lon, lat, prof = ft["geometry"]["coordinates"]
            cur.execute(
                """INSERT INTO sismo (id, magnitud, tipo_magnitud, lugar, fecha_utc, profundidad_km, geom, propiedades)
                   VALUES (%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326), %s)""",
                (ft["id"], p["mag"], p.get("magType"), p.get("place"),
                 datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc), prof, lon, lat, json.dumps(p)))
    con.commit()
    return consultar(con, """SELECT id, magnitud, tipo_magnitud, lugar, fecha_utc, profundidad_km,
                                    round(ST_X(geom)::numeric,3), round(ST_Y(geom)::numeric,3) FROM sismo""")
