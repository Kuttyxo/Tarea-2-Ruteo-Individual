"""Punto 2: descarga las fuentes publicas de los nodos clave (se guardan en data/nodos/ para que el repositorio funcione sin red).

 - Comunas (receptores de informacion): capa DPA_COMUNAS_2023 (ArcGIS Online de SENAPRED), poligonos simplificados.
 - Centros de datos (generadores de informacion) y puntos de intercambio de trafico IXP (intercambio): PeeringDB, pais CL.
 - Estaciones de amarre de cables submarinos (intercambio internacional): submarinecablemap.com, solo las de Chile.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

from .db import DATA_DIR

DIR_NODOS = os.path.join(DATA_DIR, "nodos")
UA = "tarea-ruteo-resiliente/1.0 (trabajo academico)"

URL_COMUNAS = ("https://services5.arcgis.com/i7S5PSnIJAUcWvSE/arcgis/rest/services/DPA_COMUNAS_2023/FeatureServer/0/query?"
               + urllib.parse.urlencode({
                   "where": "1=1", "outFields": "cut_reg,cut_prov,cut_com,region,provincia,comuna",
                   "outSR": 4326, "maxAllowableOffset": 0.001, "f": "geojson", "resultRecordCount": 1000}))
URL_PDB_IX = "https://www.peeringdb.com/api/ix?country=CL"
URL_PDB_FAC = "https://www.peeringdb.com/api/fac?country=CL"
URL_CABLES = "https://www.submarinecablemap.com/api/v3/landing-point/landing-point-geo.json"

ARCHIVOS = {
    "comunas_dpa2023.geojson": URL_COMUNAS,
    "peeringdb_ix_CL.json": URL_PDB_IX,
    "peeringdb_fac_CL.json": URL_PDB_FAC,
    "cables_landing_points_mundo.json": URL_CABLES,
}


def _bajar(url, destino):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        datos = r.read()
    with open(destino, "wb") as f:
        f.write(datos)
    return len(datos)


def descargar_nodos(forzar=False):
    os.makedirs(DIR_NODOS, exist_ok=True)
    log = []
    for nombre, url in ARCHIVOS.items():
        destino = os.path.join(DIR_NODOS, nombre)
        if os.path.exists(destino) and not forzar:
            log.append((nombre, "ya existe", os.path.getsize(destino)))
            continue
        n = _bajar(url, destino)
        log.append((nombre, "descargado", n))
    # landings de Chile (filtro simple por el nombre "..., Chile")
    with open(os.path.join(DIR_NODOS, "cables_landing_points_mundo.json"), encoding="utf-8") as f:
        mundo = json.load(f)
    chile = {"type": "FeatureCollection",
             "features": [x for x in mundo["features"] if (x["properties"].get("name") or "").strip().endswith(", Chile")]}
    with open(os.path.join(DIR_NODOS, "cables_landing_points_chile.geojson"), "w", encoding="utf-8") as f:
        json.dump(chile, f, ensure_ascii=False)
    with open(os.path.join(DIR_NODOS, "LEEME_fuentes.txt"), "w", encoding="utf-8") as f:
        f.write(f"Descargado el {datetime.now():%Y-%m-%d %H:%M}\n")
        for nombre, url in ARCHIVOS.items():
            f.write(f"{nombre}: {url}\n")
    return log, len(chile["features"])
