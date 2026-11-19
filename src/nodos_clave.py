"""Punto 2: identifica los nodos clave y los une a la topologia.

Roles (marco proveedor-consumidor del estudio, seccion 5.1, tomado de Parandehgheibi et al.):
  - genera      : centros de datos (PeeringDB)                       -> donde se aloja/origina el contenido
  - intercambia : IXP (PeeringDB) y amarres de cables submarinos     -> donde el trafico se intercambia entre redes / con el mundo
  - recibe      : comunas (DPA 2023)                                 -> usuarios; el estudio usa las comunas como nodos de usuarios

Regla de union a la red:
  - comuna: se une al vertice de la red mas cercano a su punto representativo, entre los vertices que caen DENTRO de la comuna
    (el estudio considera que una comuna recibe fibra si la fibra pasa por ella: 309 de 346). Si no hay ninguno: sin fibra.
  - resto: vertice mas cercano, siempre que este a <= UMBRAL_M metros.
"""
import json
import os
import re
import unicodedata

from psycopg2.extras import execute_values

from .db import conectar, consultar, ejecutar_archivo_sql
from .descargar_nodos import DIR_NODOS, descargar_nodos

UMBRAL_M = 20_000          # [Supuesto] distancia maxima para unir centros de datos, IXP y amarres a la red (la fibra detectada es solo de caminos; ver sensibilidad)
RADIO_DEDUP_GRADOS = 0.01  # ~1,1 km: centros de datos mas cerca que esto se cuentan como uno solo


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z]+", " ", s.lower()).strip()


def _json(nombre):
    with open(os.path.join(DIR_NODOS, nombre), encoding="utf-8") as f:
        return json.load(f)


def cargar_nodos_clave(con):
    """Crea tablas comunas y nodos_clave. Devuelve un resumen por rol/tipo y la lista de avisos."""
    if not os.path.exists(os.path.join(DIR_NODOS, "comunas_dpa2023.geojson")):
        descargar_nodos()
    ejecutar_archivo_sql(con, "02_nodos_clave.sql")
    avisos = []

    # --- comunas (recibe)
    fc = _json("comunas_dpa2023.geojson")
    filas = []
    for f in fc["features"]:
        p = f["properties"]
        filas.append((str(p["cut_com"]), p["comuna"], p.get("provincia"), p.get("region"), json.dumps(f["geometry"])))
    with con.cursor() as cur:
        execute_values(cur, "INSERT INTO comunas (cut_com, comuna, provincia, region, geom) VALUES %s", filas,
                       template="(%s,%s,%s,%s, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)), 3)))")
        cur.execute("""INSERT INTO nodos_clave (nombre, rol, tipo, fuente, cut_com, geom)
                       SELECT comuna, 'recibe', 'comuna', 'DPA_COMUNAS_2023 (SENAPRED/ArcGIS Online)', cut_com,
                              ST_PointOnSurface(geom) FROM comunas ORDER BY cut_com""")
    con.commit()
    comunas = {_norm(n): c for c, n in consultar(con, "SELECT cut_com, comuna FROM comunas")}

    # --- amarres de cables submarinos (intercambia)
    filas = []
    for f in _json("cables_landing_points_chile.geojson")["features"]:
        nombre = f["properties"]["name"].replace(", Chile", "").strip()
        lon, lat = f["geometry"]["coordinates"][:2]
        filas.append((nombre, "intercambia", "amarre de cable submarino", "submarinecablemap.com", lon, lat))
    # --- IXP (intercambia): PeeringDB no trae coordenadas, se usa el punto representativo de la comuna de la ciudad
    # Varios IXP de una misma ciudad quedan en el mismo punto: se cuentan como UN nodo de intercambio por ciudad
    ix_sin_comuna = []
    ix_por_comuna = {}
    for ix in _json("peeringdb_ix_CL.json")["data"]:
        cut = comunas.get(_norm(ix.get("city")))
        if not cut:
            ix_sin_comuna.append((ix["name"], ix.get("city")))
            continue
        ix_por_comuna.setdefault(cut, []).append(ix["name"])
    with con.cursor() as cur:
        for cut, nombres in ix_por_comuna.items():
            cur.execute("""INSERT INTO nodos_clave (nombre, rol, tipo, fuente, cut_com, geom)
                           SELECT %s, 'intercambia', 'IXP', 'PeeringDB (ix, CL); posicion = punto de la comuna de la ciudad',
                                  cut_com, ST_PointOnSurface(geom) FROM comunas WHERE cut_com = %s""",
                        ("IXP " + " / ".join(sorted(nombres)), cut))
    avisos.append(f"IXP en PeeringDB (CL): {sum(len(v) for v in ix_por_comuna.values())}, agrupados en {len(ix_por_comuna)} ciudades")
    if ix_sin_comuna:
        avisos.append(f"IXP sin comuna reconocida (omitidos): {ix_sin_comuna}")
    with con.cursor() as cur:
        execute_values(cur, "INSERT INTO nodos_clave (nombre, rol, tipo, fuente, geom) VALUES %s", filas,
                       template="(%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326))")
    con.commit()

    # --- centros de datos (genera): con coordenadas de PeeringDB; si faltan, punto de la comuna de la ciudad
    sin_coord = []
    with con.cursor() as cur:
        cur.execute("CREATE TEMP TABLE fac_tmp (nombre text, ciudad text, geom geometry(Point,4326), conc boolean)")
        for fac in _json("peeringdb_fac_CL.json")["data"]:
            lat, lon = fac.get("latitude"), fac.get("longitude")
            if lat is not None and lon is not None:
                cur.execute("INSERT INTO fac_tmp VALUES (%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326), true)",
                            (fac["name"], fac.get("city"), lon, lat))
            else:
                cut = comunas.get(_norm(fac.get("city")))
                if cut:
                    cur.execute("""INSERT INTO fac_tmp SELECT %s, %s, ST_PointOnSurface(geom), false FROM comunas WHERE cut_com=%s""",
                                (fac["name"], fac.get("city"), cut))
                else:
                    sin_coord.append((fac["name"], fac.get("city")))
        # un solo centro de datos por grupo de instalaciones a menos de ~1,1 km (varias comparten edificio)
        cur.execute("""INSERT INTO nodos_clave (nombre, rol, tipo, fuente, geom)
                       SELECT nombre, 'genera', 'centro de datos',
                              CASE WHEN conc THEN 'PeeringDB (fac, CL)' ELSE 'PeeringDB (fac, CL); sin coordenadas: punto de la comuna' END, geom
                       FROM (SELECT g.*, row_number() OVER (PARTITION BY grupo ORDER BY nombre) AS k
                             FROM (SELECT *, ST_ClusterDBSCAN(geom, eps := %s, minpoints := 1) OVER () AS grupo
                                   FROM fac_tmp) g) t WHERE k = 1""", (RADIO_DEDUP_GRADOS,))
        cur.execute("SELECT count(*) FROM fac_tmp")
        total_fac = cur.fetchone()[0]
    con.commit()
    if sin_coord:
        avisos.append(f"centros de datos sin coordenadas ni comuna reconocida (omitidos): {sin_coord}")
    avisos.append(f"centros de datos en PeeringDB (CL) usados: {total_fac}")

    with con.cursor() as cur:  # comuna donde cae cada nodo que aun no la tiene
        cur.execute("""UPDATE nodos_clave n SET cut_com = c.cut_com FROM comunas c
                       WHERE n.cut_com IS NULL AND ST_Intersects(c.geom, n.geom)""")
    con.commit()
    resumen = consultar(con, "SELECT rol, tipo, count(*) FROM nodos_clave GROUP BY 1,2 ORDER BY 1,2")
    return resumen, avisos


def unir_nodos_a_red(con, umbral_m=UMBRAL_M):
    """Llena nodo_red con el vertice de la topologia al que se une cada nodo clave."""
    with con.cursor() as cur:
        cur.execute("DELETE FROM nodo_red")
        # comunas: vertices dentro de la comuna
        cur.execute("""
            INSERT INTO nodo_red (nodo_id, vertice_id, dist_m, conectado)
            SELECT n.id, v.id, ST_Distance(n.geom::geography, v.the_geom::geography), v.id IS NOT NULL
            FROM nodos_clave n
            JOIN comunas c ON c.cut_com = n.cut_com
            LEFT JOIN LATERAL (SELECT id, the_geom FROM fibra_red_vertices_pgr w
                               WHERE ST_Intersects(c.geom, w.the_geom)
                               ORDER BY w.the_geom <-> n.geom LIMIT 1) v ON true
            WHERE n.rol = 'recibe'""")
        # resto: vertice mas cercano
        cur.execute("""
            INSERT INTO nodo_red (nodo_id, vertice_id, dist_m, conectado)
            SELECT n.id, v.id, ST_Distance(n.geom::geography, v.the_geom::geography),
                   ST_Distance(n.geom::geography, v.the_geom::geography) <= %s
            FROM nodos_clave n
            CROSS JOIN LATERAL (SELECT id, the_geom FROM fibra_red_vertices_pgr w ORDER BY w.the_geom <-> n.geom LIMIT 1) v
            WHERE n.rol <> 'recibe'""", (umbral_m,))
    con.commit()
    return consultar(con, """SELECT n.rol, n.tipo, count(*) total, count(*) FILTER (WHERE r.conectado) conectados,
                                    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY r.dist_m) / 1000)::numeric, 1) mediana_km
                             FROM nodos_clave n JOIN nodo_red r ON r.nodo_id = n.id GROUP BY 1,2 ORDER BY 1,2""")
