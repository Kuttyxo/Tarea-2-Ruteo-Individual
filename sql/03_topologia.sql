-- Punto 3: convertir la infraestructura (polilineas de fibra) en una topologia de red con pgRouting.
--
-- Pasos:
--  3.1 se descartan las polilineas de largo 0 (trazos degenerados de la capa original);
--  3.2 pgr_nodeNetwork parte las polilineas en sus intersecciones (donde una linea termina en medio de otra),
--      porque sin ese paso no habria conexion entre ellas;
--  3.3 cada tramo entre dos vertices consecutivos pasa a ser una arista (igual que el estudio, seccion 6.1:
--      "cada uno de estos puntos fue convertido en un nodo del grafo, y cada conexion en un arco");
--  3.4 pgr_createTopology asigna source/target (crea la tabla fibra_red_vertices_pgr) agrupando los
--      puntos que quedan a menos de 0,0001 grados (~11 m);
--  3.5 pgr_analyzeGraph revisa la red (extremos sin salida, intersecciones, aristas aisladas).

DROP TABLE IF EXISTS fibra_red CASCADE;
DROP TABLE IF EXISTS fibra_red_vertices_pgr CASCADE;
DROP TABLE IF EXISTS fibra_limpia CASCADE;
DROP TABLE IF EXISTS fibra_limpia_noded CASCADE;

-- 3.1
CREATE TABLE fibra_limpia AS
SELECT gid AS id, geom
FROM fibra_raw
WHERE ST_Length(geom::geography) > 0;

-- 3.2 (en pgRouting 3.7 la salida es la tabla fibra_limpia_noded con id, old_id, sub_id, source, target, geom)
SELECT pgr_nodeNetwork('fibra_limpia', 0.0001, 'id', 'geom');

-- 3.3
CREATE TABLE fibra_red AS
SELECT row_number() OVER (ORDER BY n.id, s.path[1])::integer AS id,
       n.old_id                                           AS linea_id,   -- polilinea original (fibra_raw.gid)
       n.id                                               AS tramo_id,   -- tramo entre intersecciones
       s.path[1]                                          AS orden,      -- posicion dentro del tramo
       s.geom::geometry(LineString, 4326)                 AS geom,
       ST_Length(s.geom::geography)                       AS largo_m,
       NULL::integer                                      AS source,
       NULL::integer                                      AS target
FROM fibra_limpia_noded n
CROSS JOIN LATERAL ST_DumpSegments(n.geom) AS s;

ALTER TABLE fibra_red ADD PRIMARY KEY (id);
CREATE INDEX fibra_red_geom_gix ON fibra_red USING gist (geom);

-- 3.4
SELECT pgr_createTopology('fibra_red', 0.0001, 'geom', 'id', 'source', 'target');

-- aristas que quedaron con source = target (dos vertices a menos de 11 m): son bucles sin efecto en la conectividad
ALTER TABLE fibra_red ADD COLUMN bucle boolean;
UPDATE fibra_red SET bucle = (source = target);

CREATE INDEX fibra_red_source_ix ON fibra_red (source);
CREATE INDEX fibra_red_target_ix ON fibra_red (target);

-- 3.5
SELECT pgr_analyzeGraph('fibra_red', 0.0001, 'geom', 'id', 'source', 'target');
