"""Tarea 2 individual: resiliencia de la fibra optica chilena ante el terremoto Mw 8,8 de Maule (2010).

Un solo comando (desde la carpeta del repositorio, con Docker abierto y las dependencias de requirements.txt):
    python main.py todo            # levanta la base (docker compose), carga todo, ATTR antes y despues, main.sql y mapa
Comandos sueltos:
    python main.py cargar          # fibra, topologia, nodos clave, sismo y probabilidades de falla (sql/01..05)
    python main.py probabilidades  # vuelve a ejecutar el modelo de falla en la base (sql/05_probabilidades.sql)
    python main.py attr            # ATTR antes y despues del terremoto (imprime y guarda salida/attr_*.json)
    python main.py exportar        # escribe sql/main.sql (volcado completo de la base)
    python main.py mapa            # genera salida/mapa.html (Leaflet) y lo abre en el navegador
    python main.py verificar       # comprueba que SciPy da lo mismo que pgr_connectedComponents (operacion + 20 escenarios)
Opciones: --muestras N (escenarios Monte Carlo, por defecto 5000)   --no-abrir (no abrir el navegador)
         --motor scipy|pgrouting (componentes conexas en Python o con pgr_connectedComponents en la base; mismo resultado)
         --fuente transparencia|internet_archive (fibra; por defecto el shapefile oficial de Transparencia)
         --desde-main-sql (en vez de construir la base desde los datos, la carga desde sql/main.sql)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import psycopg2

from src import falla as F
from src.attr import (calcular_attr, guardar_por_nodo, guardar_resultado, resumen_operacion, resumen_sismo,
                      verificar_pgrouting)
from src.cargar_infra import FUENTE_POR_DEFECTO, FUENTES, cargar_fibra
from src.db import PARAMS, SALIDA_DIR, SQL_DIR, conectar, consultar, ejecutar_archivo_sql
from src.exportar import generar_main_sql
from src.mapa import generar_mapa
from src.nodos_clave import cargar_nodos_clave, unir_nodos_a_red
from src.sismo import cargar_sismo, descargar_sismo


def _intentar_conectar(segundos):
    fin = time.time() + segundos
    while True:
        try:
            return conectar()
        except psycopg2.OperationalError as e:
            if time.time() > fin:
                return e
            time.sleep(2)


def asegurar_bd():
    """Conecta a la base; si no responde, la levanta con docker compose (docker-compose.yml de este repositorio)."""
    r = _intentar_conectar(3)
    if not isinstance(r, Exception):
        return r
    if shutil.which("docker") is None:
        sys.exit(f"No se pudo conectar a la base ({PARAMS['host']}:{PARAMS['port']}) y no se encontro Docker.\n"
                 "Instala Docker y vuelve a ejecutar, o levanta la base a mano: docker compose up -d")
    print("== 0. La base no responde: se levanta con 'docker compose up -d' (docker-compose.yml de este repositorio)")
    p = subprocess.run(["docker", "compose", "-f", os.path.join(RAIZ, "docker-compose.yml"), "up", "-d"],
                       capture_output=True, text=True)
    print("   " + (p.stdout + p.stderr).strip().replace("\n", "\n   "))
    if p.returncode != 0:
        sys.exit("docker compose fallo. ¿Esta abierto Docker Desktop (o el servicio de Docker)?")
    r = _intentar_conectar(120)
    if isinstance(r, Exception):
        sys.exit(f"La base no respondio a tiempo ({PARAMS['host']}:{PARAMS['port']}): {r}")
    print(f"   base lista en {PARAMS['host']}:{PARAMS['port']} (base '{PARAMS['dbname']}')")
    return r


def _imprimir_probabilidades(con):
    r = F.resumen_sql(con)
    print(f"   {r['aristas']:,} aristas con p_falla en [{r['p_min']:.4f}, {r['p_max']:.4f}] "
          f"(fuera de [0,1]: {r['fuera_de_rango']}); cortes esperados en la red: {r['cortes_esperados']:.1f}")
    for titulo, filas in (("mas afectados", r["mas_afectados"]), ("menos afectados", r["menos_afectados"])):
        print(f"   tramos de >= 5 km {titulo}:")
        for t, km, d, pga, pgv, p in filas:
            print(f"      tramo {t:>5}: {km:>6} km, a {d:>5} km del hipocentro, PGA {pga} g, PGV {pgv} cm/s -> P = {p:.4f}")


def paso_cargar(con, fuente=FUENTE_POR_DEFECTO):
    print(f"== 1. Infraestructura: fibra optica detectada del proyecto Yafun (estudio NIC Chile / CORFO / Subtel), fuente: {fuente}")
    n, km = cargar_fibra(con, fuente)
    print(f"   fibra_raw: {n} polilineas, {km} km")
    print("== 2. Topologia con pgRouting (pgr_nodeNetwork + pgr_createTopology + pgr_analyzeGraph); p_falla = 0 por defecto")
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
    descargar_sismo()
    for f in cargar_sismo(con):
        print(f"   {f[0]}  Mw {f[1]}  prof. {f[5]} km  epicentro ({f[7]}, {f[6]})")
    paso_probabilidades(con, "5")


def paso_desde_main_sql(con):
    ruta = os.path.join(SQL_DIR, "main.sql")
    print(f"== 1-5. Se carga la base completa desde {ruta} ({os.path.getsize(ruta) / 1e6:.1f} MB)")
    with open(ruta, encoding="utf-8") as f:
        sql = f.read()
    con.autocommit = True                      # main.sql trae su propio BEGIN/COMMIT
    with con.cursor() as cur:
        cur.execute(sql)
    con.autocommit = False
    for t, n in consultar(con, """SELECT 'fibra_raw', count(*) FROM fibra_raw UNION ALL SELECT 'fibra_red', count(*) FROM fibra_red
                                  UNION ALL SELECT 'fibra_red_vertices_pgr', count(*) FROM fibra_red_vertices_pgr
                                  UNION ALL SELECT 'nodos_clave', count(*) FROM nodos_clave UNION ALL SELECT 'sismo', count(*) FROM sismo"""):
        print(f"   {t}: {n:,} filas")


def paso_probabilidades(con, numero="5"):
    print(f"== {numero}. Probabilidad de falla de cada enlace, calculada en la base (sql/05_probabilidades.sql, ST_Distance en metros)")
    F.aplicar_modelo_sql(con)
    _imprimir_probabilidades(con)


def paso_attr(con, muestras, motor="scipy"):
    print(f"== 6. ATTR antes y despues del terremoto ({muestras:,} escenarios Monte Carlo; componentes conexas con {motor})")
    antes = calcular_attr(con, motor=motor)
    despues = calcular_attr(con, p_falla=F.p_falla_dict(con), n_muestras=muestras, motor=motor)
    guardar_resultado(con, "operacion", antes)
    guardar_resultado(con, "post_sismo", despues)
    guardar_por_nodo(con, antes, despues)
    antes.pop("por_nodo"), despues.pop("por_nodo")
    os.makedirs(SALIDA_DIR, exist_ok=True)
    for nombre, d in (("attr_operacion.json", antes), ("attr_post_sismo.json", despues)):
        with open(os.path.join(SALIDA_DIR, nombre), "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"ATTR (Average Two-Terminal Reliability) sobre {antes['n_nodos_clave']} nodos clave ({antes['pares_totales']:,} pares)")
    print(f"  {'':<42} {'ANTES':>9} {'DESPUES':>9}")
    for nombre, k in (("todos los nodos clave", "total"), ("usuarios-proveedores (recibe-genera)", "genera-recibe"),
                      ("usuarios-intercambio (recibe-intercambia)", "intercambia-recibe"), ("generan-intercambian", "genera-intercambia")):
        print(f"  {nombre:<42} {antes['ATTR_' + k]:>9.4f} {despues['ATTR_' + k]:>9.4f}")
    dif = despues["ATTR_total"] - antes["ATTR_total"]
    print(f"  diferencia del ATTR total: {dif:+.4f}  ({100 * dif / antes['ATTR_total']:+.1f} %); "
          f"IC 95 % del ATTR despues: +/- {despues['ATTR_total_ic95']:.5f}")
    _imprimir_nodos(con)
    return antes, despues


def _imprimir_nodos(con):
    op = resumen_operacion(con)
    print("  En operacion (por que el ATTR es menor que 1): nodos clave por rol -> total | sin fibra (aislados) | unidos pero solos en su fragmento")
    for rol, tipo, n, sin, solos in op["por_rol"]:
        print(f"     {rol:<11} {tipo:<27} {n:>4} | {sin:>3} | {solos:>3}")
    print(f"  {len(op['grupos'])} grupos de nodos clave conectados entre si; los 5 mayores (nodos | genera, intercambia, recibe | regiones):")
    for comp, n, g, i, r, reg in op["grupos"][:5]:
        print(f"     {n:>3} | {g:>2} {i:>2} {r:>3} | {reg[:95]}")
    sis = resumen_sismo(con, 8)
    print("  Despues del sismo: nodos clave mas afectados (R = fraccion media de los otros nodos clave que alcanza):")
    for nom, rol, com, reg, sa, sd, ra, rd, c in sis["nodos"]:
        print(f"     {nom[:30]:<30} {rol:<11} {com or '':<14} {(reg or '')[:22]:<22} R {ra:.4f} -> {rd:.4f} ({100 * (rd / ra - 1):+.0f} %)")


def paso_verificar(con, n):
    print(f"== Verificacion: SciPy (usado en el Monte Carlo) frente a pgr_connectedComponents (dentro de la base), "
          f"red en operacion y primeros {n} escenarios del sismo (semilla 2010)")
    filas = verificar_pgrouting(con, F.p_falla_dict(con), n_escenarios=n)
    print(f"   {'escenario':<10} {'aristas caidas':>14} {'componentes SciPy':>18} {'pgRouting':>10} {'grupos de nodos clave':>22} "
          f"{'ATTR SciPy':>11} {'ATTR pgRouting':>15}")
    for f in filas:
        print(f"   {f['escenario']:<10} {f['aristas_caidas']:>14} {f['componentes_scipy']:>18} {f['componentes_pgr']:>10} "
              f"{'iguales' if f['particion_igual'] else 'DISTINTOS':>22} {f['attr_scipy']:>11.6f} {f['attr_pgr']:>15.6f}")
    iguales = sum(f["particion_igual"] and f["componentes_scipy"] == f["componentes_pgr"] for f in filas)
    sc = sum(f["seg_scipy"] for f in filas) / len(filas)
    pg = sum(f["seg_pgr"] for f in filas) / len(filas)
    print(f"   escenarios identicos: {iguales} de {len(filas)}")
    print(f"   tiempo medio por escenario: SciPy {1000 * sc:.0f} ms, pgr_connectedComponents {1000 * pg:.0f} ms "
          f"(5.000 escenarios: SciPy ~{5000 * sc / 60:.1f} min, pgRouting ~{5000 * pg / 60:.0f} min)")


def paso_exportar(con):
    print("== 7. main.sql: volcado completo de la base (infraestructura + topologia + nodos + sismo + probabilidades + ATTR)")
    ruta, conteo = generar_main_sql(con, os.path.join(SQL_DIR, "main.sql"))
    print(f"   {ruta} ({os.path.getsize(ruta) / 1e6:.1f} MB): " + ", ".join(f"{t} {n:,}" for t, n in conteo.items()))


def cargar_attr_guardado(con, muestras):
    ruta_a = os.path.join(SALIDA_DIR, "attr_operacion.json")
    ruta_d = os.path.join(SALIDA_DIR, "attr_post_sismo.json")
    if os.path.exists(ruta_a) and os.path.exists(ruta_d):
        with open(ruta_a, encoding="utf-8") as fa, open(ruta_d, encoding="utf-8") as fd:
            return json.load(fa), json.load(fd)
    return paso_attr(con, muestras)


def paso_mapa(con, muestras, abrir):
    print("== 8. Mapa Leaflet")
    antes, despues = cargar_attr_guardado(con, muestras)
    ruta = generar_mapa(con, antes, despues)
    print("   mapa generado:", ruta)
    if abrir:
        webbrowser.open("file:///" + ruta.replace("\\", "/"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=["cargar", "probabilidades", "attr", "exportar", "mapa", "verificar", "todo"])
    ap.add_argument("--muestras", type=int, default=5000)
    ap.add_argument("--escenarios-pgr", type=int, default=20, help="escenarios a comparar en 'verificar' (por defecto 20)")
    ap.add_argument("--fuente", choices=sorted(FUENTES), default=FUENTE_POR_DEFECTO,
                    help="fibra: shapefile oficial de Transparencia (por defecto) o la copia del Internet Archive")
    ap.add_argument("--desde-main-sql", action="store_true", help="cargar la base desde sql/main.sql en vez de construirla")
    ap.add_argument("--no-abrir", action="store_true")
    ap.add_argument("--motor", choices=["scipy", "pgrouting"], default="scipy",
                    help="componentes conexas del Monte Carlo: SciPy (rapido) o pgr_connectedComponents en la base (~6 min)")
    a = ap.parse_args()
    inicio = time.time()
    con = asegurar_bd()
    if a.comando in ("cargar", "todo"):
        if a.desde_main_sql:
            paso_desde_main_sql(con)
        else:
            paso_cargar(con, a.fuente)
    if a.comando == "probabilidades":
        paso_probabilidades(con)
    if a.comando in ("attr", "todo"):
        paso_attr(con, a.muestras, a.motor)
    if a.comando == "exportar" or (a.comando == "todo" and not a.desde_main_sql):
        paso_exportar(con)
    if a.comando in ("mapa", "todo"):
        paso_mapa(con, a.muestras, not a.no_abrir)
    if a.comando == "verificar":
        paso_verificar(con, a.escenarios_pgr)
    print(f"listo en {time.time() - inicio:.0f} s")


if __name__ == "__main__":
    main()
