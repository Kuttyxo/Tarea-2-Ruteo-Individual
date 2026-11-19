"""Punto 8: escribe sql/main.sql, un volcado completo que carga en una base VACIA la infraestructura, la topologia,
los nodos clave, el sismo y las probabilidades de falla.

Cargar en una base vacia (la imagen pgrouting/pgrouting trae PostGIS y pgRouting), en cualquier terminal:
    docker exec tarea2_resiliencia_db createdb -U tarea2 vacia
    docker cp sql/main.sql tarea2_resiliencia_db:/tmp/main.sql
    docker exec tarea2_resiliencia_db psql -U tarea2 -d vacia -v ON_ERROR_STOP=1 -q -f /tmp/main.sql
o desde main.py:  python main.py todo --desde-main-sql

Cada valor se escribe con su representacion de texto de PostgreSQL (la geometria como EWKB hexadecimal y los numeros con
todos sus digitos), asi que la base cargada es identica a la original. No se exportan las tablas intermedias
(fibra_limpia, fibra_limpia_noded, falla_subtramo) ni los poligonos de las comunas: se pueden regenerar con main.py.
"""
import os
from datetime import datetime

from .db import SQL_DIR, consultar

# (tabla, orden de las filas, descripcion). El orden respeta las claves foraneas.
TABLAS = [
    ("youngs1997_coef", "sitio, medida", "Coeficientes de Youngs et al. (1997), tabla 2, interfaz de subduccion"),
    ("parametros_falla", "id", "Parametros del modelo de falla (sitio, K de ALA 2001, largo de sub-tramo, conversiones)"),
    ("fibra_raw", "gid", "Infraestructura: polilineas de fibra optica detectada (proyecto Yafun, shapefile oficial de Subtel)"),
    ("fibra_red", "id", "Topologia: aristas (tramos entre vertices consecutivos) con source/target de pgr_createTopology y p_falla"),
    ("fibra_red_vertices_pgr", "id", "Topologia: tabla de vertices creada por pgr_createTopology (cnt/chk/ein/eout de pgr_analyzeGraph)"),
    ("sismo", "id", "Sismo Mw 8,8 del 27-02-2010 (GeoJSON del USGS)"),
    ("nodos_clave", "id", "Nodos clave: generan (centros de datos), intercambian (IXP, amarres de cables), reciben (comunas)"),
    ("nodo_red", "nodo_id", "Union de cada nodo clave con un vertice de la topologia"),
    ("attr_resultado", "escenario, metrica", "ATTR en operacion y despues del sismo (salida de main.py)"),
]
VISTAS = ["falla_tramo"]
FUNCIONES = ["youngs1997"]
FILAS_POR_INSERT = 1000


def _existe(con, tabla):
    return consultar(con, "SELECT to_regclass(%s) IS NOT NULL", (f"public.{tabla}",))[0][0]


def _columnas(con, tabla):
    return consultar(con, """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(d.adbin, d.adrelid),
               col_description(a.attrelid, a.attnum)
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum""", (f"public.{tabla}",))


def _crear_tabla(con, tabla, descripcion):
    cols = _columnas(con, tabla)
    defs = []
    for nombre, tipo, no_nulo, defecto, _ in cols:
        d = f"    {nombre} {tipo}"
        if defecto and not defecto.startswith("nextval("):     # las secuencias (serial) no hacen falta en una copia
            d += f" DEFAULT {defecto}"
        if no_nulo:
            d += " NOT NULL"
        defs.append(d)
    for nombre, definicion in consultar(con, """SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
                                               WHERE conrelid = %s::regclass ORDER BY contype <> 'p', conname""",
                                        (f"public.{tabla}",)):
        defs.append(f"    CONSTRAINT {nombre} {definicion}")
    sql = [f"CREATE TABLE {tabla} (\n" + ",\n".join(defs) + "\n);",
           f"COMMENT ON TABLE {tabla} IS '{descripcion}';"]
    for nombre, _, _, _, comentario in cols:
        if comentario:
            sql.append(f"COMMENT ON COLUMN {tabla}.{nombre} IS '" + comentario.replace("'", "''") + "';")
    return "\n".join(sql), [c[0] for c in cols]


def _filas(con, tabla, columnas, orden):
    expr = "'(' || concat_ws(',', " + ", ".join(f"quote_nullable({c}::text)" for c in columnas) + ") || ')'"
    return [f[0] for f in consultar(con, f"SELECT {expr} FROM {tabla} ORDER BY {orden}")]


def _indices(con, tabla):
    return [f[0] + ";" for f in consultar(con, """
        SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i
        WHERE i.indrelid = %s::regclass
          AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid)
        ORDER BY 1""", (f"public.{tabla}",))]


def generar_main_sql(con, ruta=None):
    """Escribe el volcado completo. Devuelve (ruta, {tabla: filas})."""
    ruta = ruta or os.path.join(SQL_DIR, "main.sql")
    tablas = [t for t in TABLAS if _existe(con, t[0])]
    sismo = consultar(con, "SELECT id, magnitud, profundidad_km, round(ST_X(geom)::numeric, 3), round(ST_Y(geom)::numeric, 3) FROM sismo")[0]
    par = consultar(con, "SELECT sitio, k_ala, paso_max_km FROM parametros_falla")[0]
    pg = consultar(con, "SELECT current_setting('server_version'), postgis_lib_version(), pgr_version()")[0]
    partes = [f"""-- main.sql: base de datos completa de la Tarea 2 individual (Ruteo Resiliente en Redes).
-- Generado por main.py el {datetime.now():%Y-%m-%d %H:%M} en PostgreSQL {pg[0]} + PostGIS {pg[1]} + pgRouting {pg[2]}.
--
-- Contiene: infraestructura (fibra_raw), topologia (fibra_red con source/target/p_falla y fibra_red_vertices_pgr),
-- nodos clave (nodos_clave, nodo_red), sismo (sismo), modelo y probabilidades de falla (youngs1997_coef,
-- parametros_falla, funcion youngs1997, columna fibra_red.p_falla, vista falla_tramo) y los ATTR (attr_resultado).
-- Sismo: USGS {sismo[0]}, Mw {sismo[1]}, epicentro lon {sismo[3]} lat {sismo[4]}, profundidad {sismo[2]} km.
-- Modelo: Youngs et al. (1997) interfaz, sitio '{par[0]}' -> Sa(1,0) -> PGV (Newmark y Hall 1982) -> RR = K*0,00187*PGV
--   (ALA 2001), K = {par[1]} -> P = 1 - exp(-suma RR*largo), sub-tramos de <= {par[2]} km, distancia con ST_Distance(geography).
--
-- Se carga en una base VACIA con (desde la carpeta del repositorio, con la base levantada):
--   docker exec tarea2_resiliencia_db createdb -U tarea2 vacia
--   docker cp sql/main.sql tarea2_resiliencia_db:/tmp/main.sql
--   docker exec tarea2_resiliencia_db psql -U tarea2 -d vacia -v ON_ERROR_STOP=1 -q -f /tmp/main.sql
-- o con: python main.py todo --desde-main-sql   (si la base ya tiene estas tablas, se borran y se vuelven a crear).

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

BEGIN;
"""]
    for v in VISTAS:
        partes.append(f"DROP VIEW IF EXISTS {v};\n")
    for t, _, _ in reversed(tablas):
        partes.append(f"DROP TABLE IF EXISTS {t} CASCADE;\n")
    conteo = {}
    indices = []
    for t, orden, desc in tablas:
        crear, columnas = _crear_tabla(con, t, desc)
        filas = _filas(con, t, columnas, orden)
        conteo[t] = len(filas)
        partes.append(f"\n-- ===== {t}: {desc} ({len(filas):,} filas)\n{crear}\n")
        cab = f"INSERT INTO {t} ({', '.join(columnas)}) VALUES\n"
        for i in range(0, len(filas), FILAS_POR_INSERT):
            partes.append(cab + ",\n".join(filas[i:i + FILAS_POR_INSERT]) + ";\n")
        indices += _indices(con, t)
    partes.append("\n-- ===== indices\n" + "\n".join(indices) + "\n")
    partes.append("\n-- ===== funcion del modelo de falla (Youngs et al. 1997)\n")
    for fn in FUNCIONES:
        partes.append(consultar(con, "SELECT pg_get_functiondef(%s::regproc)", (fn,))[0][0].rstrip() + ";\n")
    for v in VISTAS:
        cuerpo = consultar(con, "SELECT pg_get_viewdef(%s::regclass, true)", (v,))[0][0]
        partes.append(f"\n-- ===== vista {v}\nCREATE VIEW {v} AS\n{cuerpo}\n")
    partes.append("\nCOMMIT;\nANALYZE;\n")
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(partes))
    return ruta, conteo


def huella_tablas(con):
    """md5 del contenido de cada tabla exportada (para comprobar que una base cargada desde main.sql es identica)."""
    out = {}
    for t, orden, _ in TABLAS:
        if _existe(con, t):
            out[t] = consultar(con, f"SELECT count(*), md5(coalesce(string_agg(x::text, '|' ORDER BY {orden}), '')) "
                                    f"FROM (SELECT * FROM {t}) x")[0]
    return out
