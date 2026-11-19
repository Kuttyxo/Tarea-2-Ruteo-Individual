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


def _una_muestra(src, dst, vivas, n_vertices, vertice, roles):
    g = coo_matrix((np.ones(int(vivas.sum()), dtype=np.int8), (src[vivas], dst[vivas])), shape=(n_vertices, n_vertices))
    n_comp, labels = connected_components(g, directed=False)
    unidos = vertice >= 0
    lab = np.where(unidos, labels[np.where(unidos, vertice, 0)], n_comp + np.arange(len(vertice)))
    pares, total = _pares(lab, roles, n_comp + len(vertice))
    mayor = int(total.max())
    unidos_en_mayor = int(np.bincount(labels[vertice[unidos]]).max()) if unidos.any() else 0
    todos = bool(unidos.all() and len(np.unique(labels[vertice[unidos]])) == 1)
    todos_unidos = bool(len(np.unique(labels[vertice[unidos]])) == 1) if unidos.any() else False
    solos = int((total[lab] == 1).sum())          # nodos clave sin ningun otro nodo clave en su componente
    return pares, mayor, unidos_en_mayor, todos, todos_unidos, int(n_comp), solos


def calcular_attr(con, p_falla=None, n_muestras=2000, semilla=2010, umbral_m=None):
    """ATTR con la red en operacion (p_falla=None) o con probabilidades de falla por arista.

    p_falla: dict {id de fibra_red: probabilidad}. Aristas que no estan en el dict tienen p = 0.
    Devuelve un dict con las metricas.
    """
    ids, src, dst, n_vertices = cargar_red(con)
    _, roles, vertice = cargar_nodos(con, umbral_m)
    den = _denominadores(roles)
    unidos = vertice >= 0
    n_unidos = int(unidos.sum())

    if p_falla is None:
        muestras = 1
        pares, mayor, unidos_mayor, todos, todos_unidos, n_comp, solos = _una_muestra(
            src, dst, np.ones(len(ids), dtype=bool), n_vertices, vertice, roles)
        acum = {k: [v] for k, v in pares.items()}
        mayores, todos_l, todos_u_l, ncomp_l, solos_l = [mayor], [todos], [todos_unidos], [n_comp], [solos]
    else:
        p = np.array([p_falla.get(int(i), 0.0) for i in ids])
        rng = np.random.default_rng(semilla)
        acum = {k: [] for k in den}
        mayores, todos_l, todos_u_l, ncomp_l, solos_l = [], [], [], [], []
        for _ in range(n_muestras):
            vivas = rng.random(len(ids)) >= p
            pares, mayor, _, todos, todos_unidos, n_comp, solos = _una_muestra(src, dst, vivas, n_vertices, vertice, roles)
            for k, v in pares.items():
                acum[k].append(v)
            mayores.append(mayor); todos_l.append(todos); todos_u_l.append(todos_unidos); ncomp_l.append(n_comp); solos_l.append(solos)
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
    return res


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
