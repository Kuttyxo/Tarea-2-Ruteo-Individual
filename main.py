"""Tarea 2 individual: resiliencia de la fibra optica chilena ante el terremoto Mw 8,8 de Maule (2010).

Uso (con la base levantada: docker compose up -d):
    python main.py cargar          # descarga lo que falte y carga a la BD: fibra, topologia, nodos clave, sismo y main.sql
    python main.py probabilidades  # recalcula las probabilidades de falla, regenera sql/main.sql y lo aplica
    python main.py attr            # ATTR antes y despues del terremoto (imprime y guarda salida/attr_*.json)
    python main.py mapa            # genera salida/mapa.html (Leaflet) y lo abre en el navegador
    python main.py todo            # todo lo anterior, en orden
Opciones: --muestras N (escenarios Monte Carlo, por defecto 5000)   --no-abrir (no abrir el navegador)
         --fuente transparencia|internet_archive (fibra; por defecto el shapefile oficial de Transparencia)
"""
import argparse
import json
import os
import sys
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import psycopg2

from src import falla as F
from src.attr import calcular_attr, guardar_resultado
from src.cargar_infra import FUENTE_POR_DEFECTO, FUENTES, cargar_fibra
from src.db import PARAMS, SALIDA_DIR, SQL_DIR, conectar, consultar, ejecutar_archivo_sql
from src.mapa import generar_mapa
from src.nodos_clave import cargar_nodos_clave, unir_nodos_a_red
from src.sismo import cargar_sismo, descargar_sismo


def esperar_bd(segundos=90):
    fin = time.time() + segundos
    while True:
        try:
            return conectar()
        except psycopg2.OperationalError as e:
            if time.time() > fin:
                sys.exit(f"No se pudo conectar a la base ({PARAMS['host']}:{PARAMS['port']}): {e}\n"
                         "¿Esta levantada? Ejecuta: docker compose up -d")
            time.sleep(2)


def paso_cargar(con, fuente=FUENTE_POR_DEFECTO):
    print(f"== 1. Infraestructura: fibra optica detectada del proyecto Yafun (estudio NIC Chile / CORFO / Subtel), fuente: {fuente}")
    n, km = cargar_fibra(con, fuente)
    print(f"   fibra_raw: {n} polilineas, {km} km")
    print("== 2. Topologia con pgRouting (pgr_nodeNetwork + pgr_createTopology + pgr_analyzeGraph)")
    ejecutar_archivo_sql(con, "03_topologia.sql")
    a, v, c = consultar(con, """SELECT (SELECT count(*) FROM fibra_red), (SELECT count(*) FROM fibra_red_vertices_pgr),
                                       (SELECT count(DISTINCT component) FROM pgr_connectedComponents(
                                         'SELECT id, source, target, largo_m AS cost, largo_m AS reverse_cost FROM fibra_red WHERE NOT bucle'))""")[0]
    print(f"   aristas: {a:,}   vertices: {v:,}   componentes conexas: {c}")
    print("== 3. Nodos clave (generan: centros de datos | intercambian: IXP y amarres de cables | reciben: comunas)")
    resumen, avisos = cargar_nodos_clave(con)
    for r in resumen:
        print("   ", r)
    for t in unir_nodos_a_red(con):
        print(f"    unidos a la red: {t[0]:<12} {t[1]:<27} {t[3]}/{t[2]}")
    print("== 4. Sismo (GeoJSON del USGS)")
    estado, _ = descargar_sismo()
    for f in cargar_sismo(con):
        print(f"   {f[0]}  Mw {f[1]}  prof. {f[5]} km  epicentro ({f[7]}, {f[6]})")
    print("== 5. Probabilidades de falla (sql/main.sql): se regenera siempre para que calce con la topologia recien creada")
    ruta = F.generar_main_sql(F.calcular_probabilidades(con), os.path.join(SQL_DIR, "main.sql"))
    with open(ruta, encoding="utf-8") as f:
        sql = f.read()
    with con.cursor() as cur:
        cur.execute(sql)
    con.commit()
    print("   falla_enlace:", consultar(con, "SELECT count(*) FROM falla_enlace")[0][0], "filas")


def paso_probabilidades(con):
    res = F.calcular_probabilidades(con)
    ruta = F.generar_main_sql(res, os.path.join(SQL_DIR, "main.sql"))
    with open(ruta, encoding="utf-8") as f:
        sql = f.read()
    with con.cursor() as cur:
        cur.execute(sql)
    con.commit()
    print(f"main.sql regenerado y aplicado: {ruta}")
    print(f"cortes esperados en la red: {res['lambda'].sum():.0f}; probabilidad mediana por arista: {sorted(res['p_falla'])[len(res['p_falla'])//2]:.4f}")


def paso_attr(con, muestras):
    antes = calcular_attr(con)
    despues = calcular_attr(con, p_falla=F.p_falla_dict(con), n_muestras=muestras)
    guardar_resultado(con, "operacion", antes)
    guardar_resultado(con, "post_sismo", despues)
    os.makedirs(SALIDA_DIR, exist_ok=True)
    for nombre, d in (("attr_operacion.json", antes), ("attr_post_sismo.json", despues)):
        with open(os.path.join(SALIDA_DIR, nombre), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"ATTR (Average Two-Terminal Reliability) sobre {antes['n_nodos_clave']} nodos clave ({antes['pares_totales']:,} pares)")
    print(f"  {'':<40} {'ANTES':>9} {'DESPUES':>9}")
    for nombre, k in (("todos los nodos clave", "total"), ("usuarios-proveedores (recibe-genera)", "genera-recibe"),
                      ("usuarios-intercambio (recibe-intercambia)", "intercambia-recibe"), ("generan-intercambian", "genera-intercambia")):
        print(f"  {nombre:<40} {antes['ATTR_' + k]:>9.4f} {despues['ATTR_' + k]:>9.4f}")
    print(f"  cambio relativo del ATTR total: {100 * (despues['ATTR_total'] / antes['ATTR_total'] - 1):+.1f} %  ({muestras:,} escenarios)")
    return antes, despues


def cargar_attr_guardado(con, muestras):
    ruta_a = os.path.join(SALIDA_DIR, "attr_operacion.json")
    ruta_d = os.path.join(SALIDA_DIR, "attr_post_sismo.json")
    if os.path.exists(ruta_a) and os.path.exists(ruta_d):
        with open(ruta_a, encoding="utf-8") as fa, open(ruta_d, encoding="utf-8") as fd:
            return json.load(fa), json.load(fd)
    return paso_attr(con, muestras)


def paso_mapa(con, muestras, abrir):
    antes, despues = cargar_attr_guardado(con, muestras)
    res = F.calcular_probabilidades(con)
    ruta = generar_mapa(con, res, antes, despues)
    print("mapa generado:", ruta)
    if abrir:
        webbrowser.open("file:///" + ruta.replace("\\", "/"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=["cargar", "probabilidades", "attr", "mapa", "todo"])
    ap.add_argument("--muestras", type=int, default=5000)
    ap.add_argument("--fuente", choices=sorted(FUENTES), default=FUENTE_POR_DEFECTO,
                    help="fibra: shapefile oficial de Transparencia (por defecto) o la copia del Internet Archive")
    ap.add_argument("--no-abrir", action="store_true")
    a = ap.parse_args()
    con = esperar_bd()
    if a.comando in ("cargar", "todo"):
        paso_cargar(con, a.fuente)
    if a.comando == "probabilidades":
        paso_probabilidades(con)
    if a.comando in ("attr", "todo"):
        paso_attr(con, a.muestras)
    if a.comando in ("mapa", "todo"):
        paso_mapa(con, a.muestras, not a.no_abrir)


if __name__ == "__main__":
    main()
