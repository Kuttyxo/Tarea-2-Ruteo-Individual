-- Punto 1 (carga de la infraestructura): tabla con las polilineas de fibra optica detectada
-- (proyecto Yafun, estudio NIC Chile / CORFO / SUBTEL 2018; por defecto el shapefile oficial de la respuesta de
-- Subtel por Transparencia, Respuesta_T5610).
-- Se vuelve a crear desde cero para que main.py sea repetible.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

DROP TABLE IF EXISTS fibra_raw CASCADE;
CREATE TABLE fibra_raw (
    gid        serial PRIMARY KEY,
    feature_id text,                                 -- campo "id" de la capa original
    parte      integer NOT NULL DEFAULT 1,           -- parte dentro de una polilinea de varias partes
    fuente     text NOT NULL,                        -- transparencia | internet_archive
    geom       geometry(LineString, 4326) NOT NULL
);
CREATE INDEX fibra_raw_geom_gix ON fibra_raw USING gist (geom);
