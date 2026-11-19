# Resiliencia de la fibra óptica chilena ante el terremoto de Maule (Mw 8,8)

Tarea 2 individual, Algoritmos de Ruteo y Redes Resilientes. El repositorio carga a una base de datos (PostgreSQL + PostGIS + pgRouting) la
infraestructura de fibra óptica del estudio *Estudio y Recomendaciones sobre la Resiliencia de la infraestructura de la Internet chilena*
(NIC Chile Research Labs, Universidad de Chile, CORFO y Subtel, 2018), la convierte en una topología de red, calcula el ATTR en operación,
modela la falla de cada enlace por el terremoto del 27-02-2010 y calcula el ATTR después del sismo. Un mapa Leaflet muestra la red, el
daño estimado y los ATTR.

## Cómo ejecutarlo

```bash
docker compose up -d            # base de datos en 127.0.0.1:5433 (usuario/clave/base: tarea2 / tarea2 / resiliencia)
pip install -r requirements.txt
python main.py todo             # carga, ATTR antes y después, y abre el mapa (salida/mapa.html)
```

Comandos sueltos: `python main.py cargar` · `python main.py probabilidades` (regenera `sql/main.sql`) · `python main.py attr` · `python main.py mapa`.
Opciones: `--muestras N` (escenarios Monte Carlo, 5.000 por defecto) y `--no-abrir`.
Todos los datos ya vienen en `data/`, así que funciona sin internet (el mapa carga las teselas de OpenStreetMap y Leaflet desde la red).
Se usa pgRouting 3.7 (`pgr_createTopology` queda obsoleta en la 3.8 y se elimina en la 4.0).

## Qué hace cada punto del enunciado

| Punto | Dónde |
|---|---|
| 1. Infraestructura del estudio | `data/transparencia/Respuesta_T5610/fibra_optica_detectada.shp`: shapefile oficial del proyecto Yafün que Subtel entregó por Transparencia (solicitud AN002T0005610), publicado por el ayudante del curso → `src/shp.py` (lector propio), `src/cargar_infra.py`, `sql/01_esquema_infra.sql`. Con `--fuente internet_archive` se usa la copia del mapa de Subtel del Internet Archive (`data/yafun/`), solo para comparar. |
| 2. Nodos clave | `src/nodos_clave.py`, `src/descargar_nodos.py`, `sql/02_nodos_clave.sql` |
| 3. Topología con `pgr_createTopology` | `sql/03_topologia.sql` |
| 4. ATTR en operación | `src/attr.py` (`calcular_attr` sin fallas) |
| 5. Sismo 8,8 del USGS en GeoJSON | `data/usgs/maule_2010_usgs.geojson` → `src/sismo.py`, `sql/04_sismo.sql` |
| 6. Modelo probabilístico de falla | `src/falla.py` (cadena de relaciones de la literatura, abajo) |
| 7. Script de probabilidades por enlace | `src/falla.py` (`calcular_probabilidades`) y `python main.py probabilidades` |
| 8. `main.sql` | `sql/main.sql` (tabla `falla_enlace` y vista `falla_tramo`) |
| 9. Nuevo ATTR | `python main.py attr` → `salida/attr_post_sismo.json` y tabla `attr_resultado` |
| 10. Repositorio con `main.py` y Leaflet | este repositorio; `src/mapa.py` genera `salida/mapa.html` |

## Decisiones y supuestos (cada uno con su fuente)

**Nodos clave (marco proveedor–consumidor del estudio, sección 5.1).** *Generan*: centros de datos (PeeringDB, 29 tras unir los que están a menos de ~1,1 km).
*Intercambian*: IXP (PeeringDB, 6 ciudades) y estaciones de amarre de cables submarinos (submarinecablemap.com, 19). *Reciben*: las 345 comunas (DPA 2023;
el estudio de 2018 usa 346). **[Supuesto]** la clasificación en esos tres roles es nuestra. Una comuna se une a la red si hay fibra dentro de su territorio
(criterio del estudio); los demás nodos, si hay un vértice de fibra a 20 km o menos **[Supuesto]**. Los nodos sin fibra cuentan como aislados.

**Topología.** Cada tramo entre vértices consecutivos es una arista (como en la sección 6.1 del estudio); antes, `pgr_nodeNetwork` parte las líneas en sus
intersecciones y después `pgr_createTopology` (tolerancia 0,0001° ≈ 11 m) asigna `source`/`target`. Resultado: 22.705 aristas, 22.673 vértices y 123 componentes
conexas: la fibra *detectada* no es una red nacional continua (el estudio dice lo mismo: la red actual no es conexa).

**ATTR.** Average Two-Terminal Reliability: fracción esperada de pares de nodos clave que pueden comunicarse. Es la definición del paper base del curso: Boettcher, Prieto, Restrepo y Pezoa (2018), IEEE Latin America Transactions 16(9), ec. 8 y 9, «fiabilidad promedio de dos terminales», ATTR(k) = C(n,2)⁻¹ Σ Z_ij, promediado sobre los escenarios de falla. En operación, cada par está conectado o no. Después del sismo, cada arista falla de forma independiente con su probabilidad y el valor
es el promedio de 5.000 escenarios (Monte Carlo, semilla fija).

**Modelo de falla (desde el epicentro del GeoJSON del USGS).**
1. Distancia hipocentral `R = √(d_epicentro² + H²)`, con H = 22,9 km.
2. Youngs, Chiou, Silva y Humphrey (1997), *Seismological Research Letters* 68(1):58-73, tabla 2 (zona de subducción, interfaz): PGA y Sa(1 s) medianos, suelo tipo *soil*.
3. PGV = 94,6 cm/s × Sa(1 s) en g (Newmark y Hall, 1982; implementación de USGS ShakeLib).
4. Tasa de reparaciones por km de infraestructura lineal: `RR = K · 0,00187 · PGV` (ALA 2001 / HAZUS; PGV en in/s y RR por cada 1.000 pies), es decir `0,002415 · K · PGV[cm/s]` por km.
5. Probabilidad de falla de un tramo: `P = 1 − exp(−Σ RR·largo)` (Poisson), integrando sub-tramos de a lo más 2 km.

**[Supuestos]** punto-fuente en el epicentro (el USGS muestra una ruptura de ~500 km, por lo que el modelo subestima el movimiento a 200-450 km hacia el norte y lo sobreestima
a más de 450 km); suelo *soil* (la fibra va por valles y caminos); la tasa de reparaciones de tuberías enterradas se usa como aproximación de un cable (K = 1, el valor de referencia de ALA),
sin variabilidad aleatoria. Contra el ShakeMap oficial del USGS, el modelo tiene correlación de logaritmos 0,90 en 16.729 puntos de la red y razón de medianas de PGV 1,08.
K y el tipo de suelo cambian mucho el ATTR después del sismo (ver la tabla de sensibilidad en la bitácora), pero no la conclusión: el ATTR cae en todas las variantes.

## Estructura

```
main.py              punto de entrada
docker-compose.yml   PostgreSQL 16 + PostGIS 3.5 + pgRouting 3.7.3
sql/                 01_esquema_infra, 02_nodos_clave, 03_topologia, 04_sismo, main.sql (probabilidades)
src/                 db, cargar_infra, descargar_nodos, nodos_clave, sismo, falla, attr, mapa
data/                transparencia/ (fibra oficial), yafun/ (copia del Internet Archive, solo para comparar), nodos/ (comunas, PeeringDB, cables), usgs/ (sismo y ShakeMap)
salida/              mapa.html, attr_operacion.json, attr_post_sismo.json
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
- PeeringDB (https://www.peeringdb.com), submarinecablemap.com, SENAPRED/ArcGIS Online (DPA 2023).
