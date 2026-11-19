-- Punto 2: nodos clave (generan, intercambian y reciben informacion).
CREATE EXTENSION IF NOT EXISTS postgis;

DROP TABLE IF EXISTS nodo_red CASCADE;
DROP TABLE IF EXISTS nodos_clave CASCADE;
DROP TABLE IF EXISTS comunas CASCADE;

CREATE TABLE comunas (
    cut_com   text PRIMARY KEY,
    comuna    text NOT NULL,
    provincia text,
    region    text,
    geom      geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX comunas_geom_gix ON comunas USING gist (geom);

CREATE TABLE nodos_clave (
    id      serial PRIMARY KEY,
    nombre  text NOT NULL,
    rol     text NOT NULL CHECK (rol IN ('genera', 'intercambia', 'recibe')),
    tipo    text NOT NULL,          -- centro de datos | IXP | amarre de cable submarino | comuna
    fuente  text NOT NULL,
    cut_com text,                   -- comuna donde esta el nodo (si se conoce)
    geom    geometry(Point, 4326) NOT NULL
);
CREATE INDEX nodos_clave_geom_gix ON nodos_clave USING gist (geom);

-- Union de cada nodo clave con un vertice de la topologia (ver src/nodos_clave.py)
CREATE TABLE nodo_red (
    nodo_id    integer PRIMARY KEY REFERENCES nodos_clave (id) ON DELETE CASCADE,
    vertice_id integer,             -- id en fibra_red_vertices_pgr
    dist_m     double precision,    -- distancia del nodo al vertice
    conectado  boolean NOT NULL     -- true si cumple la regla de union (ver src/nodos_clave.py)
);
