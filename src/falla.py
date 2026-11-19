"""Puntos 6, 7 y 8: modelo probabilistico de falla de los enlaces a partir del epicentro del sismo.

Cadena de calculo (cada eslabon tiene su fuente; ver README.md y la bitacora):

 1. Distancia  R = sqrt(d_epicentro^2 + H^2)        d_epicentro: distancia en la esfera (haversine) desde el epicentro del
                                                      GeoJSON del USGS al punto medio del tramo; H: profundidad del USGS (22,9 km).
 2. Movimiento del suelo: GMPE de interfaz de subduccion de Youngs, Chiou, Silva y Humphrey (1997), Seismological Research
    Letters 68(1):58-73, tabla 2:
        ln y = A1 + A2*M + C1 + C2*(A3-M)^3 + C3*ln(R + A4*exp(A5*M)) + A6*H        (y en g, mediana)
    para la aceleracion peak (PGA) y para la aceleracion espectral a 1 s, Sa(1.0).
 3. Velocidad peak:  PGV[cm/s] = (386.09 / (2*pi*1.65)) * 2.54 * Sa(1.0)[g] = 94,6 * Sa(1.0)   (Newmark y Hall, 1982, como lo
    implementa USGS ShakeLib, shakelib.conversions.imt.newmark_hall_1982).
 4. Tasa de reparaciones por km (infraestructura lineal, ALA 2001 / HAZUS):  RR = K * 0,00187 * PGV[in/s] por cada 1.000 pies
    -> en unidades SI: RR[1/km] = K * 0,00187 * (1000/304,8) / 2,54 * PGV[cm/s] = 0,002415 * K * PGV[cm/s]
 5. Probabilidad de falla del tramo (proceso de Poisson de cortes a lo largo del cable):
        P = 1 - exp( - sum_k RR_k * l_k )          (k: sub-tramos de a lo mas ~2 km; l_k en km)

[Supuestos, ver README]: punto-fuente en el epicentro (R no es la distancia a la ruptura); suelo tipo "soil" (Vs30 < 760 m/s);
la tasa de reparaciones de tuberias enterradas se usa como aproximacion para la fibra (K = 1); mediana sin variabilidad.
"""
import json
import math
import os

import numpy as np

from .db import DATA_DIR, SALIDA_DIR, SQL_DIR, consultar

DIR_USGS = os.path.join(DATA_DIR, "usgs")

# --- Youngs et al. (1997), interfaz de subduccion. Valores de la tabla 2 (p. 67), verificados contra la implementacion
# de OpenQuake (openquake.hazardlib.gsim.youngs_1997.YoungsEtAl1997SInter, constantes CONSTS y COEFFS_ROCK / COEFFS_SOIL).
YOUNGS = {
    "rock": {"A1": 0.2418, "A2": 1.414, "A3": 10.0, "A4": 1.7818, "A5": 0.554, "A6": 0.00607,
             "pga": {"C1": 0.0, "C2": 0.0, "C3": -2.552}, "sa1": {"C1": -1.736, "C2": -0.0064, "C3": -2.234}},
    "soil": {"A1": -0.6687, "A2": 1.438, "A3": 10.0, "A4": 1.097, "A5": 0.617, "A6": 0.00648,
             "pga": {"C1": 0.0, "C2": 0.0, "C3": -2.329}, "sa1": {"C1": -2.870, "C2": -0.0114, "C3": -1.785}},
}
NEWMARK_HALL_CM_S_POR_G = 386.09 / (2 * math.pi * 1.65) * 2.54      # = 94,6 cm/s por g de Sa(1.0)
ALA_COEF = 0.00187                                                   # RR por 1000 pies, PGV en in/s (ALA 2001)
RR_POR_KM_POR_CM_S = ALA_COEF * (1000 / 304.8) / 2.54                # = 0,002415 reparaciones/km por cada cm/s de PGV
PASO_MAX_KM = 2.0                                                    # largo maximo de cada sub-tramo al integrar

PARAMETROS_POR_DEFECTO = {"sitio": "soil", "K": 1.0, "paso_max_km": PASO_MAX_KM}


def _youngs(R_km, mag, prof_km, sitio, medida):
    c = YOUNGS[sitio]
    k = c[medida]
    ln_y = (c["A1"] + c["A2"] * mag + k["C1"] + k["C2"] * (c["A3"] - mag) ** 3
            + k["C3"] * np.log(R_km + c["A4"] * np.exp(c["A5"] * mag)) + c["A6"] * prof_km)
    return np.exp(ln_y)


def movimiento_suelo(R_km, mag, prof_km, sitio="soil"):
    """PGA (g), Sa(1.0) (g) y PGV (cm/s) medianos a la distancia R (km)."""
    pga = _youngs(R_km, mag, prof_km, sitio, "pga")
    sa1 = _youngs(R_km, mag, prof_km, sitio, "sa1")
    return pga, sa1, NEWMARK_HALL_CM_S_POR_G * sa1


def tasa_reparaciones_por_km(pgv_cm_s, K=1.0):
    return K * RR_POR_KM_POR_CM_S * pgv_cm_s


def haversine_km(lon1, lat1, lon2, lat2):
    r = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def leer_shakemap(variable):
    """Cuadricula del ShakeMap del USGS (CoverageJSON, resolucion media). variable: 'pga' o 'pgv'. Devuelve xs, ys, valores (g o cm/s)."""
    ruta = os.path.join(DIR_USGS, f"shakemap_coverage_{variable}_medium_res.covjson")
    with open(ruta, encoding="utf-8") as f:
        c = json.load(f)
    ax = c["domain"]["axes"]
    xs = np.linspace(ax["x"]["start"], ax["x"]["stop"], ax["x"]["num"])
    ys = np.linspace(ax["y"]["start"], ax["y"]["stop"], ax["y"]["num"])
    clave = list(c["ranges"].keys())[0]
    v = np.array([np.nan if x is None else x for x in c["ranges"][clave]["values"]], dtype=float).reshape(len(ys), len(xs))
    unidad = c["parameters"][clave]["unit"]["label"]["en"]
    v = np.exp(v)                       # el USGS publica el logaritmo natural
    if "percent" in unidad.lower():
        v = v / 100.0                   # %g -> g
    return xs, ys, v


def muestrear_cuadricula(xs, ys, v, lon, lat):
    """Valor del vecino mas cercano; NaN fuera de la cuadricula."""
    dx, dy = xs[1] - xs[0], ys[1] - ys[0]
    ix = np.rint((lon - xs[0]) / dx).astype(int)
    iy = np.rint((lat - ys[0]) / dy).astype(int)
    dentro = (ix >= 0) & (ix < len(xs)) & (iy >= 0) & (iy < len(ys))
    out = np.full(lon.shape, np.nan)
    out[dentro] = v[iy[dentro], ix[dentro]]
    return out


def cargar_aristas(con):
    filas = consultar(con, """SELECT id, linea_id, tramo_id, largo_m,
                                     ST_X(ST_StartPoint(geom)), ST_Y(ST_StartPoint(geom)),
                                     ST_X(ST_EndPoint(geom)), ST_Y(ST_EndPoint(geom))
                              FROM fibra_red ORDER BY id""")
    a = np.array(filas, dtype=float)
    return {"id": a[:, 0].astype(int), "linea_id": a[:, 1].astype(int), "tramo_id": a[:, 2].astype(int),
            "largo_m": a[:, 3], "x1": a[:, 4], "y1": a[:, 5], "x2": a[:, 6], "y2": a[:, 7]}


def datos_sismo(con):
    lon, lat, mag, prof, sid = consultar(con, "SELECT ST_X(geom), ST_Y(geom), magnitud, profundidad_km, id FROM sismo ORDER BY magnitud DESC LIMIT 1")[0]
    return {"lon": float(lon), "lat": float(lat), "mag": float(mag), "prof": float(prof), "id": sid}


def calcular_probabilidades(con, sitio="soil", K=1.0, paso_max_km=PASO_MAX_KM, modelo="epicentro"):
    """Probabilidad de falla de cada arista. modelo='epicentro' (el pedido) o 'shakemap' (comparacion: PGV del ShakeMap del USGS
    dentro de su cuadricula y el modelo del epicentro fuera de ella). Devuelve un dict de arreglos."""
    e = cargar_aristas(con)
    s = datos_sismo(con)
    L_km = e["largo_m"] / 1000.0
    n = np.maximum(1, np.ceil(L_km / paso_max_km)).astype(int)         # sub-tramos por arista
    idx = np.repeat(np.arange(len(L_km)), n)                          # arista de cada sub-tramo
    k = np.arange(len(idx)) - np.repeat(np.cumsum(n) - n, n)           # posicion dentro de la arista
    t = (k + 0.5) / n[idx]
    lon = e["x1"][idx] + t * (e["x2"][idx] - e["x1"][idx])
    lat = e["y1"][idx] + t * (e["y2"][idx] - e["y1"][idx])
    d_epi = haversine_km(s["lon"], s["lat"], lon, lat)
    R = np.sqrt(d_epi ** 2 + s["prof"] ** 2)
    pga, sa1, pgv = movimiento_suelo(R, s["mag"], s["prof"], sitio)
    if modelo == "shakemap":
        xs, ys, g = leer_shakemap("pgv")
        pgv_sm = muestrear_cuadricula(xs, ys, g, lon, lat)
        pgv = np.where(np.isnan(pgv_sm), pgv, pgv_sm)
    rr = tasa_reparaciones_por_km(pgv, K)
    lam_sub = rr * (L_km[idx] / n[idx])
    lam = np.bincount(idx, weights=lam_sub, minlength=len(L_km))
    p = 1.0 - np.exp(-lam)
    # valores representativos de cada arista = los del punto medio del primer/unico sub-tramo central
    medio = np.cumsum(n) - n + n // 2
    fuente = consultar(con, "SELECT string_agg(DISTINCT fuente, ',') FROM fibra_raw")[0][0]
    return {"fuente_fibra": fuente, "id": e["id"], "linea_id": e["linea_id"], "tramo_id": e["tramo_id"], "largo_m": e["largo_m"],
            "dist_hipocentral_km": R[medio], "pga_g": pga[medio], "pgv_cm_s": pgv[medio], "rr_km": rr[medio],
            "lambda": lam, "p_falla": p, "sismo": s, "sitio": sitio, "K": K, "modelo": modelo}


def validar_contra_shakemap(res, con, max_puntos=4000, semilla=1):
    """Compara el movimiento del suelo del modelo con la cuadricula del ShakeMap (PGA en g, PGV en cm/s) en los puntos medios de las aristas."""
    e = cargar_aristas(con)
    lon = (e["x1"] + e["x2"]) / 2
    lat = (e["y1"] + e["y2"]) / 2
    xs, ys, gpga = leer_shakemap("pga")
    _, _, gpgv = leer_shakemap("pgv")
    pga_sm = muestrear_cuadricula(xs, ys, gpga, lon, lat)
    pgv_sm = muestrear_cuadricula(xs, ys, gpgv, lon, lat)
    ok = ~np.isnan(pga_sm) & ~np.isnan(pgv_sm)
    out = {"n_puntos": int(ok.sum())}
    for nombre, mod, sm in (("PGA (g)", res["pga_g"], pga_sm), ("PGV (cm/s)", res["pgv_cm_s"], pgv_sm)):
        razon = mod[ok] / sm[ok]
        out[nombre] = {"mediana_modelo": float(np.median(mod[ok])), "mediana_shakemap": float(np.median(sm[ok])),
                       "razon_mediana_modelo/shakemap": float(np.median(razon)),
                       "correlacion_log": float(np.corrcoef(np.log(mod[ok]), np.log(sm[ok]))[0, 1])}
    return out


def generar_main_sql(res, ruta=None, comentario_extra=""):
    """Escribe main.sql: crea la tabla falla_enlace con la probabilidad de falla de cada arista y las vistas por tramo."""
    ruta = ruta or os.path.join(SQL_DIR, "main.sql")
    s = res["sismo"]
    cab = f"""-- main.sql: probabilidad de falla de cada enlace (arista de fibra_red) por el terremoto Mw {s['mag']} de Maule (27-02-2010).
-- Fibra: proyecto Yafun, fuente '{res.get('fuente_fibra')}' (transparencia = shapefile oficial de Subtel, Respuesta_T5610). Aristas: {len(res['id'])}.
-- Sismo: USGS {s['id']}  epicentro lon {s['lon']} lat {s['lat']}  profundidad {s['prof']} km.
-- Modelo ({res['modelo']}): Youngs et al. (1997) interfaz de subduccion, sitio '{res['sitio']}' -> Sa(1.0) -> PGV (Newmark y Hall 1982)
--   -> tasa de reparaciones RR = K*0,00187*PGV (ALA 2001 / HAZUS), K = {res['K']} -> P = 1 - exp(-sum RR*largo).
-- Requiere que ya existan las tablas de topologia (sql/03_topologia.sql). Se puede ejecutar varias veces.
{comentario_extra}
BEGIN;
DROP VIEW  IF EXISTS falla_tramo;
DROP TABLE IF EXISTS falla_enlace;
CREATE TABLE falla_enlace (
    arista_id           integer PRIMARY KEY REFERENCES fibra_red (id),
    linea_id            integer,
    tramo_id            integer,
    largo_m             double precision,
    dist_hipocentral_km double precision,
    pga_g               double precision,
    pgv_cm_s            double precision,
    rr_reparaciones_km  double precision,
    p_falla             double precision NOT NULL CHECK (p_falla >= 0 AND p_falla <= 1)
);
"""
    cols = ["id", "linea_id", "tramo_id", "largo_m", "dist_hipocentral_km", "pga_g", "pgv_cm_s", "rr_km", "p_falla"]
    partes = [cab]
    n = len(res["id"])
    for ini in range(0, n, 1000):
        filas = []
        for i in range(ini, min(ini + 1000, n)):
            filas.append("(" + ",".join([
                str(int(res["id"][i])), str(int(res["linea_id"][i])), str(int(res["tramo_id"][i])),
                f"{res['largo_m'][i]:.2f}", f"{res['dist_hipocentral_km'][i]:.2f}", f"{res['pga_g'][i]:.5f}",
                f"{res['pgv_cm_s'][i]:.3f}", f"{res['rr_km'][i]:.6f}", f"{min(max(res['p_falla'][i], 0.0), 1.0):.8f}"]) + ")")
        partes.append("INSERT INTO falla_enlace (arista_id, linea_id, tramo_id, largo_m, dist_hipocentral_km, pga_g, pgv_cm_s, rr_reparaciones_km, p_falla) VALUES\n"
                      + ",\n".join(filas) + ";\n")
    partes.append("""
-- Probabilidad de falla de cada tramo (conjunto de aristas entre intersecciones): falla si falla alguna de sus aristas
CREATE VIEW falla_tramo AS
SELECT tramo_id, count(*) AS aristas, round((sum(largo_m) / 1000)::numeric, 2) AS largo_km,
       1 - exp(sum(ln(greatest(1 - p_falla, 1e-12)))) AS p_falla
FROM falla_enlace GROUP BY tramo_id;
COMMIT;
""")
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(partes))
    return ruta


def p_falla_dict(con):
    """Lee las probabilidades desde la tabla falla_enlace (las que cargo main.sql)."""
    return {int(i): float(p) for i, p in consultar(con, "SELECT arista_id, p_falla FROM falla_enlace")}
