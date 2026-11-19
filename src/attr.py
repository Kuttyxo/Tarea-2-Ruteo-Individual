"""Punto 4 y 9: ATTR (Average Two-Terminal Reliability) de la infraestructura.

Definicion usada (paper base del curso: Boettcher et al. 2018, IEEE Latin America Transactions 16(9), ec. 8 y 9):
    ATTR = (1 / C(N,2)) * sum, sobre todos los pares {i, j} de nodos clave, de P(i y j estan conectados)
es decir, la fraccion esperada de pares de nodos clave que pueden comunicarse. Con la red en operacion (sin fallas) la
probabilidad de cada par es 0 o 1 y el ATTR es simplemente la fraccion de pares conectados. Despues del terremoto,
cada arista falla con su probabilidad p_i (independientes) y el ATTR se estima por Monte Carlo.

Un nodo clave que no se pudo unir a la red (ver nodos_clave.unir_nodos_a_red) esta aislado: no esta conectado con nadie.
Tambien se informa el ATTR "todos-terminales" (probabilidad de que TODOS los nodos clave unidos a la red queden en una sola componente).
"""
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .db import consultar

ROLES = ("genera", "intercambia", "recibe")


def cargar_red(con):
    filas = consultar(con, "SELECT id, source, target FROM fibra_red WHERE NOT bucle ORDER BY id")
    ids = np.array([f[0] for f in filas], dtype=np.int64)
    src = np.array([f[1] for f in filas], dtype=np.int64)
    dst = np.array([f[2] for f in filas], dtype=np.int64)
    n_vertices = int(consultar(con, "SELECT max(id) FROM fibra_red_vertices_pgr")[0][0]) + 1
    return ids, src, dst, n_vertices


def cargar_nodos(con, umbral_m=None):
    """Devuelve (ids, roles, vertice) con vertice = -1 si el nodo no esta unido a la red."""
    # comunas: se usa la regla de union de la base; el resto: distancia al vertice <= umbral (por defecto el de la base)
    cond = ("r.conectado" if umbral_m is None
            else f"(CASE WHEN n.rol = 'recibe' THEN r.conectado ELSE r.dist_m <= {float(umbral_m)} END)")
    filas = consultar(con, f"""SELECT n.id, n.rol, CASE WHEN {cond} THEN r.vertice_id ELSE -1 END
                               FROM nodos_clave n JOIN nodo_red r ON r.nodo_id = n.id ORDER BY n.id""")
    ids = np.array([f[0] for f in filas])
    roles = np.array([f[1] for f in filas])
    vertice = np.array([-1 if f[2] is None else f[2] for f in filas], dtype=np.int64)
    return ids, roles, vertice


def _pares(lab, roles, n_etiquetas):
    """Pares conectados totales y por combinacion de roles, a partir de la etiqueta de componente de cada nodo."""
    cuenta = {r: np.bincount(lab[roles == r], minlength=n_etiquetas).astype(np.int64) for r in ROLES}
    total = np.bincount(lab, minlength=n_etiquetas).astype(np.int64)
    out = {"total": int((total * (total - 1) // 2).sum())}
    for i, a in enumerate(ROLES):
        out[f"{a}-{a}"] = int((cuenta[a] * (cuenta[a] - 1) // 2).sum())
        for b in ROLES[i + 1:]:
            out[f"{a}-{b}"] = int((cuenta[a] * cuenta[b]).sum())
    return out, total


def _denominadores(roles):
    n = {r: int((roles == r).sum()) for r in ROLES}
    d = {"total": len(roles) * (len(roles) - 1) // 2}
    for i, a in enumerate(ROLES):
        d[f"{a}-{a}"] = n[a] * (n[a] - 1) // 2
        for b in ROLES[i + 1:]:
            d[f"{a}-{b}"] = n[a] * n[b]
    return d


def _componentes_scipy(src, dst, vivas, n_vertices):
    """Componentes conexas con SciPy. Los ids de vertice de pgRouting parten en 1: la fila 0 de la matriz es un vertice
    que no existe y se descuenta del total."""
    g = coo_matrix((np.ones(int(vivas.sum()), dtype=np.int8), (src[vivas], dst[vivas])), shape=(n_vertices, n_vertices))
    n_comp, labels = connected_components(g, directed=False)
    return n_comp - 1, labels


def escenarios(p, n_muestras, semilla):
    """Escenarios de falla del Monte Carlo: en cada uno, la arista i sigue viva con probabilidad 1 - p_i (independientes)."""
    rng = np.random.default_rng(semilla)
    for _ in range(n_muestras):
        yield rng.random(len(p)) >= p


class MotorPgr:
    """Componentes conexas con pgr_connectedComponents dentro de la base (alternativa a SciPy, mas lenta: ~0,1 s por escenario).
    Devuelve lo mismo que _componentes_scipy: (numero de componentes, etiqueta de cada vertice)."""

    def __init__(self, con, ids, n_vertices):
        from psycopg2.extras import execute_values
        self.cur, self.ids, self.n_vertices, self._ev = con.cursor(), ids, n_vertices, execute_values
        self.cur.execute("DROP TABLE IF EXISTS pg_temp.aristas_caidas")
        self.cur.execute("CREATE TEMP TABLE aristas_caidas (id integer PRIMARY KEY)")

    def __call__(self, vivas):
        self.cur.execute("TRUNCATE aristas_caidas")
        if (~vivas).any():
            self._ev(self.cur, "INSERT INTO aristas_caidas (id) VALUES %s", [(int(i),) for i in self.ids[~vivas]], page_size=1000)
        self.cur.execute("""SELECT node, component FROM pgr_connectedComponents(
                              'SELECT id, source, target, 1 AS cost, 1 AS reverse_cost FROM fibra_red e
                               WHERE NOT bucle AND NOT EXISTS (SELECT 1 FROM aristas_caidas c WHERE c.id = e.id)')""")
        filas = np.array(self.cur.fetchall(), dtype=np.int64).reshape(-1, 2)
        etiqueta = np.arange(self.n_vertices) + self.n_vertices      # vertice sin aristas vivas: queda solo
        etiqueta[filas[:, 0]] = filas[:, 1]
        unicas, etiqueta = np.unique(etiqueta, return_inverse=True)
        return len(unicas) - 1, etiqueta                              # -1: el vertice 0 no existe


def _una_muestra(src, dst, vivas, n_vertices, vertice, roles, motor=None):
    n_comp, labels = _componentes_scipy(src, dst, vivas, n_vertices) if motor is None else motor(vivas)
    unidos = vertice >= 0
    libre = int(labels.max()) + 1                 # etiquetas nuevas para los nodos sin fibra (cada uno solo)
    lab = np.where(unidos, labels[np.where(unidos, vertice, 0)], libre + np.arange(len(vertice)))
    pares, total = _pares(lab, roles, libre + len(vertice))
    mayor = int(total.max())
    unidos_en_mayor = int(np.bincount(labels[vertice[unidos]]).max()) if unidos.any() else 0
    todos = bool(unidos.all() and len(np.unique(labels[vertice[unidos]])) == 1)
    todos_unidos = bool(len(np.unique(labels[vertice[unidos]])) == 1) if unidos.any() else False
    solos = int((total[lab] == 1).sum())          # nodos clave sin ningun otro nodo clave en su componente
    socios = total[lab] - 1                       # por nodo: cuantos otros nodos clave alcanza
    return pares, mayor, unidos_en_mayor, todos, todos_unidos, int(n_comp), solos, socios


def calcular_attr(con, p_falla=None, n_muestras=2000, semilla=2010, umbral_m=None, devolver_muestras=False, motor="scipy"):
    """ATTR con la red en operacion (p_falla=None) o con probabilidades de falla por arista.

    p_falla: dict {id de fibra_red: probabilidad}. Aristas que no estan en el dict tienen p = 0.
    motor: 'scipy' (por defecto, rapido) o 'pgrouting' (pgr_connectedComponents dentro de la base, mismos resultados).
    Devuelve un dict con las metricas (con devolver_muestras=True, tambien el ATTR de cada escenario).
    """
    ids, src, dst, n_vertices = cargar_red(con)
    fn = MotorPgr(con, ids, n_vertices) if motor == "pgrouting" else None
    nodo_ids, roles, vertice = cargar_nodos(con, umbral_m)
    den = _denominadores(roles)
    unidos = vertice >= 0
    n_unidos = int(unidos.sum())

    if p_falla is None:
        muestras = 1
        pares, mayor, unidos_mayor, todos, todos_unidos, n_comp, solos, socios = _una_muestra(
            src, dst, np.ones(len(ids), dtype=bool), n_vertices, vertice, roles, fn)
        acum = {k: [v] for k, v in pares.items()}
        mayores, todos_l, todos_u_l, ncomp_l, solos_l = [mayor], [todos], [todos_unidos], [n_comp], [solos]
        socios_sum = socios.astype(float)
    else:
        p = np.array([p_falla.get(int(i), 0.0) for i in ids])
        acum = {k: [] for k in den}
        mayores, todos_l, todos_u_l, ncomp_l, solos_l = [], [], [], [], []
        socios_sum = np.zeros(len(roles))
        for vivas in escenarios(p, n_muestras, semilla):
            pares, mayor, _, todos, todos_unidos, n_comp, solos, socios = _una_muestra(src, dst, vivas, n_vertices, vertice, roles, fn)
            for k, v in pares.items():
                acum[k].append(v)
            mayores.append(mayor); todos_l.append(todos); todos_u_l.append(todos_unidos); ncomp_l.append(n_comp); solos_l.append(solos)
            socios_sum += socios
        muestras = n_muestras

    res = {"muestras": muestras, "n_nodos_clave": int(len(roles)), "n_nodos_unidos_a_la_red": n_unidos,
           "n_aristas": int(len(ids)), "n_vertices": int(len(np.unique(np.concatenate([src, dst]))))}
    for k, lst in acum.items():
        a = np.array(lst, dtype=float) / max(den[k], 1)
        res[f"ATTR_{k}"] = float(a.mean())
        if muestras > 1:
            res[f"ATTR_{k}_ic95"] = float(1.96 * a.std(ddof=1) / np.sqrt(muestras))
    # ATTR calculado solo entre los nodos que estan unidos a la red (no penaliza a los que no tienen fibra)
    pares_tot = np.array(acum["total"], dtype=float)
    res["ATTR_solo_nodos_unidos"] = float((pares_tot / max(n_unidos * (n_unidos - 1) // 2, 1)).mean())
    res["mayor_grupo_de_nodos_clave_conectados"] = float(np.mean(mayores))
    res["prob_todos_los_nodos_unidos_conectados"] = float(np.mean(todos_u_l))
    res["componentes_de_la_red_promedio"] = float(np.mean(ncomp_l))
    res["nodos_clave_solos_en_su_componente"] = float(np.mean(solos_l))
    if muestras > 1:
        res["nodos_clave_solos_ic95"] = float(1.96 * np.std(solos_l, ddof=1) / np.sqrt(muestras))
    res["pares_totales"] = den["total"]
    if devolver_muestras:
        res["muestras_attr_total"] = [v / den["total"] for v in acum["total"]]
    if fn is not None:
        con.rollback()                            # borra la tabla temporal de aristas caidas
    res["motor_componentes"] = motor
    # Por nodo: fiabilidad promedio del nodo R_i = E[otros nodos clave que alcanza] / (N - 1).
    # El promedio de R_i sobre los N nodos es exactamente el ATTR (cada par se cuenta dos veces).
    # Va aparte (clave "por_nodo") porque no es un numero suelto: main.py la guarda en la tabla attr_nodo.
    socios_medios = socios_sum / muestras
    res["por_nodo"] = {"nodo_id": [int(i) for i in nodo_ids], "unido": [bool(u) for u in unidos],
                       "socios": [float(s) for s in socios_medios],
                       "r_nodo": [float(s) / (len(roles) - 1) for s in socios_medios]}
    return res


SQL_GRUPOS = """
    WITH comp AS (SELECT node, component FROM pgr_connectedComponents(
                    'SELECT id, source, target, largo_m AS cost, largo_m AS reverse_cost FROM fibra_red WHERE NOT bucle')),
         g AS (SELECT n.id, n.rol, n.region, c.component
               FROM nodos_clave n JOIN nodo_red r ON r.nodo_id = n.id AND r.conectado JOIN comp c ON c.node = r.vertice_id)
    SELECT component, count(*) AS nodos, count(*) FILTER (WHERE rol = 'genera') AS genera,
           count(*) FILTER (WHERE rol = 'intercambia') AS intercambia, count(*) FILTER (WHERE rol = 'recibe') AS recibe,
           string_agg(DISTINCT region, ', ') AS regiones
    FROM g GROUP BY component ORDER BY nodos DESC, component"""


def resumen_operacion(con):
    """Para interpretar el ATTR en operacion: nodos clave sin fibra y grupos de nodos clave que comparten un fragmento de la
    red (componentes conexas calculadas con pgr_connectedComponents dentro de la base)."""
    por_rol = consultar(con, """SELECT n.rol, n.tipo, count(*), count(*) FILTER (WHERE NOT a.unido),
                                       count(*) FILTER (WHERE a.unido AND a.socios_antes = 0)
                                FROM nodos_clave n JOIN attr_nodo a ON a.nodo_id = n.id GROUP BY 1, 2 ORDER BY 1, 2""")
    sin_fibra = consultar(con, """SELECT n.rol, n.nombre, n.comuna, n.region, round((r.dist_m / 1000)::numeric, 1)
                                  FROM nodos_clave n JOIN nodo_red r ON r.nodo_id = n.id
                                  WHERE NOT r.conectado AND n.rol <> 'recibe' ORDER BY n.rol, n.nombre""")
    comunas_sin_fibra = consultar(con, """SELECT n.region, count(*) FROM nodos_clave n JOIN nodo_red r ON r.nodo_id = n.id
                                          WHERE NOT r.conectado AND n.rol = 'recibe' GROUP BY 1 ORDER BY 2 DESC, 1""")
    grupos = consultar(con, SQL_GRUPOS)
    return {"por_rol": por_rol, "sin_fibra": sin_fibra, "comunas_sin_fibra": comunas_sin_fibra, "grupos": grupos}


def resumen_sismo(con, n=10):
    """Nodos clave mas afectados (mayor caida de su fiabilidad promedio R_i) y resumen por region."""
    nodos = consultar(con, """SELECT n.nombre, n.rol, n.comuna, n.region, a.socios_antes, round(a.socios_despues::numeric, 1),
                                     a.r_antes, a.r_despues, a.caida
                              FROM nodos_clave n JOIN attr_nodo a ON a.nodo_id = n.id
                              ORDER BY a.caida DESC, n.nombre LIMIT %s""", (n,))
    regiones = consultar(con, """SELECT n.region, count(*) AS nodos, count(*) FILTER (WHERE a.unido) AS unidos,
                                        avg(a.r_antes) AS r_antes, avg(a.r_despues) AS r_despues
                                 FROM nodos_clave n JOIN attr_nodo a ON a.nodo_id = n.id
                                 GROUP BY n.region ORDER BY avg(a.r_antes) - avg(a.r_despues) DESC""")
    return {"nodos": nodos, "regiones": regiones}


def _canonica(etiquetas):
    """Renumera las etiquetas por orden de aparicion: dos particiones son iguales si sus formas canonicas son iguales."""
    vistas = {}
    return np.array([vistas.setdefault(e, len(vistas)) for e in etiquetas])


def verificar_pgrouting(con, p_falla, n_escenarios=20, semilla=2010):
    """Compara SciPy con pgr_connectedComponents (dentro de la base) en la red en operacion y en los primeros n escenarios
    del Monte Carlo (los mismos que usa calcular_attr con esa semilla). Para cada escenario compara: (a) el numero de
    componentes de la red y (b) que grupos de nodos clave quedan conectados (la particion). Si (b) coincide, el ATTR de ese
    escenario es identico con los dos metodos. Devuelve una fila por escenario."""
    import time
    from psycopg2.extras import execute_values
    ids, src, dst, n_vertices = cargar_red(con)
    _, roles, vertice = cargar_nodos(con)
    assert consultar(con, "SELECT count(*) = max(id) FROM fibra_red_vertices_pgr")[0][0], "ids de vertices no consecutivos"
    unidos = vertice >= 0
    den = len(roles) * (len(roles) - 1) // 2
    p = np.array([p_falla.get(int(i), 0.0) for i in ids])
    with con.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS pg_temp.aristas_caidas")
        cur.execute("CREATE TEMP TABLE aristas_caidas (id integer PRIMARY KEY)")
    filas = []
    lista = [("operacion", np.ones(len(ids), dtype=bool))] + [(f"sismo {k + 1}", v) for k, v in enumerate(escenarios(p, n_escenarios, semilla))]
    for nombre, vivas in lista:
        # SciPy (lo que usa el Monte Carlo)
        t0 = time.perf_counter()
        n_sc, lab_v = _componentes_scipy(src, dst, vivas, n_vertices)
        en_aristas = np.unique(np.concatenate([src[vivas], dst[vivas]]))
        n_sc_aristas = len(np.unique(lab_v[en_aristas]))            # componentes entre los vertices que tienen aristas vivas
        lab_sc = np.where(unidos, lab_v[np.where(unidos, vertice, 0)], -1 - np.arange(len(vertice)))
        t_sc = time.perf_counter() - t0
        # pgRouting dentro de la base, con las mismas aristas caidas
        t0 = time.perf_counter()
        with con.cursor() as cur:
            cur.execute("TRUNCATE aristas_caidas")
            if (~vivas).any():
                execute_values(cur, "INSERT INTO aristas_caidas (id) VALUES %s", [(int(i),) for i in ids[~vivas]])
            cur.execute("""SELECT node, component FROM pgr_connectedComponents(
                             'SELECT id, source, target, 1 AS cost, 1 AS reverse_cost FROM fibra_red e
                              WHERE NOT bucle AND NOT EXISTS (SELECT 1 FROM aristas_caidas c WHERE c.id = e.id)')""")
            comp = dict(cur.fetchall())
        t_pg = time.perf_counter() - t0
        n_pg = len(set(comp.values()))
        # un vertice sin aristas vivas no aparece en pgRouting: queda solo (su propio grupo)
        lab_pg = np.array([comp.get(int(v), -int(v) - 10**7) if u else -1 - k for k, (v, u) in enumerate(zip(vertice, unidos))])
        misma = bool(np.array_equal(_canonica(lab_sc), _canonica(lab_pg)))
        pares_sc = int(sum(c * (c - 1) // 2 for c in np.unique(lab_sc, return_counts=True)[1]))
        pares_pg = int(sum(c * (c - 1) // 2 for c in np.unique(lab_pg, return_counts=True)[1]))
        filas.append({"escenario": nombre, "aristas_caidas": int((~vivas).sum()), "componentes_scipy": n_sc_aristas,
                      "componentes_pgr": n_pg, "particion_igual": misma, "attr_scipy": pares_sc / den, "attr_pgr": pares_pg / den,
                      "seg_scipy": t_sc, "seg_pgr": t_pg})
    con.rollback()
    return filas


def guardar_por_nodo(con, antes, despues):
    """Tabla attr_nodo: fiabilidad promedio de cada nodo clave antes y despues del sismo (y su caida)."""
    a, d = antes["por_nodo"], despues["por_nodo"]
    assert a["nodo_id"] == d["nodo_id"]
    with con.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS attr_nodo")
        cur.execute("""CREATE TABLE attr_nodo (
                           nodo_id        integer PRIMARY KEY REFERENCES nodos_clave (id),
                           unido          boolean NOT NULL,   -- unido a la red (si no, aislado desde el inicio)
                           socios_antes   integer NOT NULL,   -- otros nodos clave que alcanza en operacion
                           socios_despues double precision NOT NULL,  -- promedio en los escenarios del sismo
                           r_antes        double precision NOT NULL,  -- socios_antes / (N - 1)
                           r_despues      double precision NOT NULL,  -- socios_despues / (N - 1)
                           caida          double precision NOT NULL)  -- r_antes - r_despues""")
        filas = [(i, u, int(round(sa)), sd, ra, rd, ra - rd) for i, u, sa, sd, ra, rd in
                 zip(a["nodo_id"], a["unido"], a["socios"], d["socios"], a["r_nodo"], d["r_nodo"])]
        cur.executemany("INSERT INTO attr_nodo VALUES (%s,%s,%s,%s,%s,%s,%s)", filas)
        cur.execute("COMMENT ON TABLE attr_nodo IS 'Fiabilidad promedio de cada nodo clave (R_i) antes y despues del sismo; promedio de R_i = ATTR'")
    con.commit()


def guardar_resultado(con, escenario, res):
    """Guarda las metricas numericas en la tabla attr_resultado (se recrea la fila de ese escenario)."""
    with con.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS attr_resultado (
                           escenario text, metrica text, valor double precision, PRIMARY KEY (escenario, metrica))""")
        cur.execute("DELETE FROM attr_resultado WHERE escenario = %s", (escenario,))
        for k, v in res.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cur.execute("INSERT INTO attr_resultado VALUES (%s,%s,%s)", (escenario, k, float(v)))
    con.commit()
