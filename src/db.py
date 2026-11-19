"""Conexion a PostgreSQL/PostGIS/pgRouting (contenedor del docker-compose.yml) y utilidades."""
import os

import psycopg2

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_DIR = os.path.join(RAIZ, "sql")
DATA_DIR = os.path.join(RAIZ, "data")
SALIDA_DIR = os.path.join(RAIZ, "salida")

PARAMS = dict(
    host=os.environ.get("PGHOST", "127.0.0.1"),
    port=int(os.environ.get("PGPORT", "5433")),
    user=os.environ.get("PGUSER", "tarea2"),
    password=os.environ.get("PGPASSWORD", "tarea2"),
    dbname=os.environ.get("PGDATABASE", "resiliencia"),
)


def conectar():
    return psycopg2.connect(**PARAMS)


def ejecutar_archivo_sql(con, nombre):
    """Ejecuta sql/<nombre> completo (un solo script, en una transaccion)."""
    ruta = os.path.join(SQL_DIR, nombre)
    with open(ruta, encoding="utf-8") as f:
        sql = f.read()
    with con.cursor() as cur:
        cur.execute(sql)
    con.commit()
    return ruta


def consultar(con, sql, params=None):
    with con.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()
