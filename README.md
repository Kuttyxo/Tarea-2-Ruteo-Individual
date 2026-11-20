# Resiliencia de la fibra óptica chilena ante el terremoto de Maule (Mw 8,8)

Tarea 2 individual, Algoritmos de Ruteo y Redes Resilientes. El repositorio carga a una base de datos (PostgreSQL + PostGIS + pgRouting) la
infraestructura de fibra óptica del estudio *Estudio y Recomendaciones sobre la Resiliencia de la infraestructura de la Internet chilena*
(NIC Chile Research Labs, Universidad de Chile, CORFO y Subtel, 2018), la convierte en una topología de red, calcula el ATTR en operación,
modela la falla de cada enlace por el terremoto del 27-02-2010 y calcula el ATTR después del sismo. Un mapa Leaflet muestra la red, el
daño estimado y los ATTR.

## Cómo ejecutarlo (un solo comando)

Requisitos: Docker (Docker Desktop en Windows o macOS) abierto, y Python 3 con las librerías de `requirements.txt` (probado con Python 3.13.5).

```bash
git clone <url-del-repositorio> && cd <carpeta-del-repositorio>
pip install -r requirements.txt     # una sola vez
python main.py todo                 # TODO el flujo
```

`python main.py todo` hace, en orden:

0. Si la base no responde, la levanta con `docker compose up -d` (archivo `docker-compose.yml`: PostgreSQL 16 + PostGIS 3.5 + pgRouting 3.7.3, en 127.0.0.1:5433; usuario, clave y base: `tarea2`, `tarea2`, `resiliencia`).
1. Carga la fibra (`sql/01_esquema_infra.sql`, tabla `fibra_raw`).
2. Arma la topología con `pgr_createTopology` (`sql/03_topologia.sql`, tablas `fibra_red` y `fibra_red_vertices_pgr`). La columna `fibra_red.p_falla` empieza en 0.
3. Carga los nodos clave y los une a la red (`sql/02_nodos_clave.sql`, `src/nodos_clave.py`).
4. Carga el sismo desde el GeoJSON del USGS (`sql/04_sismo.sql`, tabla `sismo`).
5. Calcula la probabilidad de falla de cada enlace dentro de la base (`sql/05_probabilidades.sql`): la distancia sale de `ST_Distance` sobre `geography`, en metros, y el resultado se escribe en `fibra_red.p_falla`.
6. Calcula el ATTR antes y después (`src/attr.py`) y guarda `salida/attr_operacion.json`, `salida/attr_post_sismo.json` y la tabla `attr_resultado`.
7. Escribe `sql/main.sql`, el volcado completo de la base.
8. Genera el mapa `salida/mapa.html` y lo abre en el navegador.

Todo corre sin internet: los datos ya vienen en `data/`. Solo el mapa carga Leaflet y las teselas de OpenStreetMap desde la red. Tarda unos 2 minutos.

Comandos sueltos: `python main.py cargar | probabilidades | attr | exportar | mapa | verificar`.
Opciones:

- `--muestras N`: escenarios Monte Carlo (5.000 por defecto).
- `--no-abrir`: no abre el navegador.
- `--fuente internet_archive`: usa la capa antigua, solo para comparar.
- `--desde-main-sql`: carga la base desde `sql/main.sql` en vez de construirla desde los datos.
- `--motor pgrouting`: calcula las componentes de cada escenario con `pgr_connectedComponents` dentro de la base. Da el mismo resultado que SciPy y tarda unos 6 minutos en vez de 30 segundos.

`python main.py verificar` resuelve la red en operación y los primeros 20 escenarios del sismo con SciPy y con `pgr_connectedComponents`, y comprueba que dan lo mismo.

### Valores que debe entregar

| Resultado | Valor |
|---|---|
| Fibra cargada | 1.915 polilíneas, 10.312,8 km |
| Topología | 22.705 aristas, 22.673 vértices, 123 componentes conexas |
| Nodos clave | 660 (29 generan; 286 intercambian: 6 IXP, 19 amarres y 261 nodos troncales; 345 reciben); 600 unidos a la red |
| Cortes esperados por el sismo | 262,2 (P por enlace entre 0 y 0,436) |
| ATTR en operación | 0,0567 |
| ATTR después del sismo | 0,0084 (−0,0483; −85,1 %), IC 95 % ± 0,00004, 5.000 escenarios, semilla 2010 |

## `main.sql` en una base vacía

`sql/main.sql` (≈ 9,5 MB) trae la estructura y todos los datos: infraestructura (`fibra_raw`), topología (`fibra_red` con `source`, `target` y `p_falla`, y la tabla de vértices `fibra_red_vertices_pgr`), nodos clave (`nodos_clave`, `nodo_red`), sismo (`sismo`), modelo de falla (`youngs1997_coef`, `parametros_falla`, función `youngs1997`, vista `falla_tramo`) y los ATTR (`attr_resultado`).
Para cargarlo en una base vacía (sirve en cualquier terminal, con la base levantada):

```bash
docker exec tarea2_resiliencia_db createdb -U tarea2 vacia
docker cp sql/main.sql tarea2_resiliencia_db:/tmp/main.sql
docker exec tarea2_resiliencia_db psql -U tarea2 -d vacia -v ON_ERROR_STOP=1 -q -f /tmp/main.sql
```

## Qué hace cada punto del enunciado

| Punto | Dónde |
|---|---|
| 1. Infraestructura del estudio | `data/transparencia/Respuesta_T5610/fibra_optica_detectada.shp`: shapefile oficial del proyecto Yafün que Subtel entregó por Transparencia (solicitud AN002T0005610), publicado por el ayudante del curso. Lo usan `src/shp.py` (lector propio), `src/cargar_infra.py` y `sql/01_esquema_infra.sql`. Con `--fuente internet_archive` se usa la copia del mapa de Subtel del Internet Archive (`data/yafun/`), solo para comparar. |
| 2. Nodos clave | `src/nodos_clave.py`, `src/descargar_nodos.py`, `sql/02_nodos_clave.sql` |
| 3. Topología con `pgr_createTopology` | `sql/03_topologia.sql` |
| 4. ATTR en operación | `src/attr.py` (`calcular_attr` sin fallas) |
| 5. Sismo 8,8 del USGS en GeoJSON | `data/usgs/maule_2010_usgs.geojson`, cargado por `src/sismo.py` y `sql/04_sismo.sql` |
| 6. Modelo probabilístico de falla | `sql/05_probabilidades.sql` (cadena de la literatura, abajo); la misma cadena en numpy está en `src/falla.py`, para comprobar y para las sensibilidades |
| 7. Script de probabilidades por enlace | `sql/05_probabilidades.sql` (`python main.py probabilidades`), que actualiza `fibra_red.p_falla` |
| 8. `main.sql` | `sql/main.sql`, generado por `src/exportar.py` |
| 9. Nuevo ATTR | `python main.py attr`, que escribe `salida/attr_post_sismo.json` y la tabla `attr_resultado` |
| 10. Repositorio con `main.py` y Leaflet | este repositorio; `src/mapa.py` genera `salida/mapa.html` |

## Librerías y para qué se usa cada una

| Librería / programa | Versión probada | Para qué se usa |
|---|---|---|
| PostgreSQL | 16 | Base de datos donde vive todo: datos, topología, modelo y resultados. |
| PostGIS | 3.5.2 | Geometrías: largo de cada tramo (`ST_Length` sobre `geography`), distancia al epicentro (`ST_Distance` sobre `geography`, en metros), puntos de los sub-tramos (`ST_LineInterpolatePoint`), unión de nodos con la red. |
| pgRouting | 3.7.3 | `pgr_nodeNetwork` (corta las líneas en sus cruces), `pgr_createTopology` (crea `source`, `target` y la tabla de vértices), `pgr_analyzeGraph` (revisa la red) y `pgr_connectedComponents` (cuenta las componentes). Se usa la 3.7 porque `pgr_createTopology` queda obsoleta en la 3.8. |
| psycopg2-binary | 2.9.13 | Conecta Python con PostgreSQL. |
| numpy | 2.1.3 | Arreglos: sorteo de los escenarios Monte Carlo y la cadena del modelo en Python (comprobación del SQL y sensibilidades). |
| SciPy | 1.15.3 | `scipy.sparse.csgraph.connected_components`: componentes conexas en cada uno de los 5.000 escenarios Monte Carlo (~5 ms por escenario, frente a ~60 ms de `pgr_connectedComponents`). `python main.py verificar` muestra que da lo mismo que pgRouting, y `--motor pgrouting` hace todo el Monte Carlo dentro de la base. |
| Leaflet | 1.9.4 (CDN cdnjs) | Mapa web interactivo (`salida/mapa.html`). |
| Docker / Docker Compose | 28.0 / 2.34 | Levanta la base con la imagen `pgrouting/pgrouting:16-3.5-3.7.3`. |

El resto es biblioteca estándar de Python: `struct` para leer el shapefile, `urllib` para las descargas y `json`.

## Decisiones y supuestos (cada uno con su fuente)

**Nodos clave (marco proveedor–consumidor del estudio, sección 5.1).** *Generan*: centros de datos (PeeringDB; quedan 29 tras unir los que están a menos de ~1,1 km).
*Intercambian*: IXP (PeeringDB, 6 ciudades), estaciones de amarre de cables submarinos (submarinecablemap.com, 19) y **nodos troncales** (261).
Un nodo troncal es un vértice donde se juntan 3 o más tramos de fibra (grado `cnt` ≥ 3 según `pgr_analyzeGraph`): una bifurcación de la red de transporte, donde
se junta o se reparte el tráfico de varias rutas. El Estudio llama troncales a los enlaces que interconectan los equipos de los proveedores (pág. 8) y habla de una
columna vertebral de fibra que conecta las ciudades (pág. 9). *Reciben*: las 345 comunas (DPA 2023; el estudio de 2018 usa 346). **[Supuesto]** La
clasificación en esos tres roles y el criterio de los nodos troncales son nuestros. Una comuna se une a la red si hay fibra dentro de su territorio (criterio del
estudio). Un nodo troncal ya está en la red. Los demás nodos se unen si hay un vértice de fibra a 20 km o menos **[Supuesto]**. Los nodos sin fibra cuentan
como aislados. La lista completa, con rol, fuente, ubicación y vértice de la topología, se escribe en `salida/nodos_clave.csv`.

**Topología.** Cada tramo entre vértices consecutivos es una arista, como en la sección 6.1 del estudio. Primero, `pgr_nodeNetwork` parte las líneas en sus
intersecciones. Después, `pgr_createTopology` (tolerancia 0,0001° ≈ 11 m) asigna `source` y `target`. Resultado: 22.705 aristas, 22.673 vértices y 123 componentes
conexas. La fibra *detectada* no es una red nacional continua; el estudio dice lo mismo: la red actual no es conexa.

**Limitaciones de los datos.** El shapefile oficial de fibra trae 10.313 km (el informe del estudio, Cuadro 4.1, dice 10.342 km). Los ~28.000 km que cita el
estudio son la suma de las 4 capas: fibra, postación, caminos y ferry. Aquí se usa **solo la fibra detectada**, porque las otras capas son corredores sin fibra que el estudio usa para
proponer una red nueva (sección 6). **No se agregó ningún enlace sintético**: toda arista sale de una polilínea de la capa de fibra.

**ATTR.** Average Two-Terminal Reliability: fracción esperada de pares de nodos clave que pueden comunicarse. Es la definición del paper base del curso: Boettcher, Prieto, Restrepo y Pezoa (2018), IEEE Latin America Transactions 16(9), ec. 8 y 9, «fiabilidad promedio de dos terminales»: ATTR(k) = C(n,2)⁻¹ Σ Z_ij, promediado sobre los escenarios de falla. En operación, cada par está conectado o no. Después del sismo, cada arista falla de forma independiente con su probabilidad `p_falla`. El valor
es el promedio de 5.000 escenarios (Monte Carlo, semilla fija).

El paper suma sobre todos sus escenarios porque tiene pocos. Aquí hay 2^22.585 escenarios, y el cálculo exacto de la fiabilidad de una red es #P-completo (Provan y Ball, 1983). Con 5.000 escenarios, el IC 95 % del ATTR es ±0,00004 (0,4 %). La tabla `attr_nodo` guarda, para cada nodo clave, la fracción de los otros nodos clave que alcanza antes y después del sismo; su promedio es el ATTR.

**Modelo de falla (desde el epicentro del GeoJSON del USGS), en `sql/05_probabilidades.sql`.**
1. Cada arista se divide en sub-tramos de 2 km como máximo. La distancia al epicentro del punto medio de cada sub-tramo se calcula con `ST_Distance` sobre `geography` (geodésica en el elipsoide WGS84, en metros).
   Se descartó una sola proyección UTM: con UTM 19S (EPSG:32719), el error llega a 274 km en Isla de Pascua y a 1,2 km en el continente.
2. Distancia hipocentral: `R = √(d_epicentro² + H²)`, con H = 22,9 km.
3. Youngs, Chiou, Silva y Humphrey (1997), *Seismological Research Letters* 68(1):58-73, tabla 2 (zona de subducción, interfaz): PGA y Sa(1 s) medianos, suelo tipo *soil*.
4. PGV = 94,6 cm/s × Sa(1 s) en g (Newmark y Hall, 1982; implementación de USGS ShakeLib).
5. Tasa de reparaciones por km de infraestructura lineal: `RR = K · 0,00187 · PGV` (ALA 2001 / HAZUS; PGV en in/s y RR por cada 1.000 pies), es decir `0,002415 · K · PGV[cm/s]` por km.
6. Probabilidad de falla de una arista: `P = 1 − exp(−Σ RR·largo)` (Poisson). Queda siempre en [0, 1), y la columna tiene un `CHECK` que lo exige.

**[Supuestos]** Punto-fuente en el epicentro: el USGS muestra una ruptura de ~500 km, por lo que el modelo subestima el movimiento a 200-450 km hacia el norte y lo sobreestima
a más de 450 km. Suelo *soil*, porque la fibra va por valles y caminos. La tasa de reparaciones de tuberías enterradas se usa como aproximación de un cable (K = 1, el valor de referencia de ALA),
sin variabilidad aleatoria. Contra el ShakeMap oficial del USGS, el modelo tiene correlación de logaritmos 0,90 (PGA y PGV) en 16.729 puntos de la red.
K y el tipo de suelo cambian mucho el ATTR después del sismo (ver la tabla de sensibilidad en la bitácora), pero no la conclusión: el ATTR cae en todas las variantes.

## Estructura

```
main.py              punto de entrada (un solo comando: python main.py todo)
docker-compose.yml   PostgreSQL 16 + PostGIS 3.5 + pgRouting 3.7.3
requirements.txt     psycopg2-binary, numpy, scipy
sql/                 01_esquema_infra, 02_nodos_clave, 03_topologia, 04_sismo, 05_probabilidades, main.sql (volcado completo)
src/                 db, shp, cargar_infra, descargar_nodos, nodos_clave, sismo, falla, attr, exportar, mapa
data/                transparencia/ (fibra oficial), yafun/ (copia del Internet Archive, solo para comparar), nodos/ (comunas, PeeringDB, cables), usgs/ (sismo y ShakeMap)
salida/              mapa.html, attr_operacion.json, attr_post_sismo.json, nodos_clave.csv
```

## Fuentes

- Paper base del curso (definición de ATTR): N. Boettcher, Y. Prieto, S. E. Restrepo y J. E. Pezoa, «Resilient Multiculture Network Design in the Presence of Exploit-Triggered Correlated Failures», IEEE Latin America Transactions 16(9):2336-2344, 2018.
- Estudio y Recomendaciones sobre la Resiliencia de la infraestructura de la Internet chilena (2018): https://www.subtel.gob.cl/wp-content/uploads/2018/12/resiliencia_uchile_corfo_subtel.pdf
- Shapefiles oficiales del proyecto Yafün: respuesta de Subtel a la solicitud de Transparencia AN002T0005610 (`Respuesta_T5610`), publicados por el ayudante del curso.
- Mapa Recorrido Yafün (Subtel, archivado; solo para comparar): https://web.archive.org/web/20240520163236/https://www.subtel.gob.cl/mapadigital/mapa_recorrido_yafun/
- USGS, M 8.8 Maule 2010 (`official20100227063411530_30`): https://earthquake.usgs.gov/earthquakes/eventpage/official20100227063411530_30
- Youngs et al. (1997), SRL 68(1):58-73. Coeficientes verificados contra `openquake.hazardlib.gsim.youngs_1997`.
- Newmark y Hall (1982) vía USGS ShakeLib: https://usgs.github.io/shakelib/shakelib.conversions.imt.newmark_hall_1982.html
- ALA (2001), *Seismic Fragility Formulations for Water Systems* (fórmula de RR citada en Mazumder et al., J. Pipeline Syst. Eng.).
- PostGIS, `ST_Distance` sobre `geography` (distancia en metros sobre el elipsoide): https://postgis.net/docs/ST_Distance.html
- J. S. Provan y M. O. Ball (1983), «The complexity of counting cuts and of computing the probability that a graph is connected», SIAM J. Comput. 12(4):777-788, doi 10.1137/0212053.
- PeeringDB (https://www.peeringdb.com), submarinecablemap.com, SENAPRED/ArcGIS Online (DPA 2023).
