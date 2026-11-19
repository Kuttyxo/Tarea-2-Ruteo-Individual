-- Punto 5: el sismo (GeoJSON del USGS) cargado a la base.
CREATE EXTENSION IF NOT EXISTS postgis;

DROP TABLE IF EXISTS sismo CASCADE;
CREATE TABLE sismo (
    id             text PRIMARY KEY,          -- id del evento en el USGS
    magnitud       double precision NOT NULL, -- Mw
    tipo_magnitud  text,
    lugar          text,
    fecha_utc      timestamptz,
    profundidad_km double precision,
    geom           geometry(Point, 4326) NOT NULL,   -- epicentro
    propiedades    jsonb                      -- properties completas del GeoJSON
);
CREATE INDEX sismo_geom_gix ON sismo USING gist (geom);
