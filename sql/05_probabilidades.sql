-- Puntos 6 y 7: probabilidad de falla de cada enlace (arista de fibra_red) por el terremoto, calculada DENTRO de la base.
--
-- Cadena del modelo (cada eslabon con su fuente; ver README.md):
--  1. Cada arista se divide en sub-tramos de a lo mas 2 km; se toma el punto medio de cada sub-tramo.
--  2. Distancia al epicentro con ST_Distance sobre geography: distancia geodesica en METROS sobre el elipsoide WGS84.
--     Se usa geography y no una proyeccion UTM porque Chile mide ~4.300 km de norte a sur y una sola zona UTM
--     (p. ej. 19S, EPSG:32719) deforma las distancias lejos de su meridiano central.
--  3. Distancia hipocentral R = sqrt(d_epicentro^2 + H^2), H = profundidad del USGS (tabla sismo).
--  4. Youngs, Chiou, Silva y Humphrey (1997), SRL 68(1):58-73, tabla 2, interfaz de subduccion:
--        ln y = A1 + A2*M + C1 + C2*(A3 - M)^3 + C3*ln(R + A4*exp(A5*M)) + A6*H      (y en g, mediana)
--     para PGA y Sa(1,0 s). Coeficientes en la tabla youngs1997_coef (verificados contra OpenQuake).
--  5. PGV [cm/s] = 386,09 / (2*pi*1,65) * 2,54 * Sa(1,0) [g] = 94,6 * Sa(1,0)   (Newmark y Hall 1982, via USGS ShakeLib).
--  6. Tasa de reparaciones por km (ALA 2001, infraestructura lineal enterrada):
--        RR = K * 0,00187 * PGV[in/s] por cada 1.000 pies  =  K * 0,002415 * PGV[cm/s] por km.
--  7. Cortes esperados de la arista: lambda = suma de RR * largo de cada sub-tramo;  P = 1 - exp(-lambda)  (Poisson).
--     P queda siempre en [0, 1) y la columna fibra_red.p_falla tiene un CHECK que lo exige.
--
-- Requiere: fibra_red (03_topologia.sql) y sismo (04_sismo.sql). Se puede ejecutar varias veces.

DROP VIEW  IF EXISTS falla_tramo;
DROP TABLE IF EXISTS falla_enlace CASCADE;      -- tabla de la version anterior (las probabilidades ahora viven en fibra_red)
DROP TABLE IF EXISTS falla_subtramo CASCADE;

-- 6.1 Coeficientes de Youngs et al. (1997), tabla 2 (p. 67). rock = roca; soil = suelo (Vs30 < 760 m/s).
DROP TABLE IF EXISTS youngs1997_coef CASCADE;
CREATE TABLE youngs1997_coef (
    sitio  text NOT NULL CHECK (sitio IN ('rock', 'soil')),
    medida text NOT NULL CHECK (medida IN ('pga', 'sa1')),
    a1 double precision NOT NULL, a2 double precision NOT NULL, a3 double precision NOT NULL,
    a4 double precision NOT NULL, a5 double precision NOT NULL, a6 double precision NOT NULL,
    c1 double precision NOT NULL, c2 double precision NOT NULL, c3 double precision NOT NULL,
    PRIMARY KEY (sitio, medida)
);
INSERT INTO youngs1997_coef VALUES
    ('rock', 'pga',  0.2418, 1.414, 10.0, 1.7818, 0.554, 0.00607,  0.0,    0.0,    -2.552),
    ('rock', 'sa1',  0.2418, 1.414, 10.0, 1.7818, 0.554, 0.00607, -1.736, -0.0064, -2.234),
    ('soil', 'pga', -0.6687, 1.438, 10.0, 1.097,  0.617, 0.00648,  0.0,    0.0,    -2.329),
    ('soil', 'sa1', -0.6687, 1.438, 10.0, 1.097,  0.617, 0.00648, -2.870, -0.0114, -1.785);

-- 6.2 Parametros del modelo (una sola fila). Para una sensibilidad se cambia esta fila y se vuelve a ejecutar el archivo.
DROP TABLE IF EXISTS parametros_falla CASCADE;
CREATE TABLE parametros_falla (
    id             integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    sitio          text NOT NULL DEFAULT 'soil' CHECK (sitio IN ('rock', 'soil')),  -- [Supuesto] la fibra va por valles y caminos
    k_ala          double precision NOT NULL DEFAULT 1.0,                         -- factor K de ALA (2001); 1 = valor de referencia
    paso_max_km    double precision NOT NULL DEFAULT 2.0,                         -- largo maximo de cada sub-tramo
    pgv_por_g      double precision NOT NULL DEFAULT (386.09 / (2 * pi() * 1.65) * 2.54),  -- Newmark y Hall: cm/s por g de Sa(1,0)
    rr_por_cm_s_km double precision NOT NULL DEFAULT (0.00187 * (1000 / 304.8) / 2.54)     -- ALA 2001 en unidades SI
);
INSERT INTO parametros_falla DEFAULT VALUES;

-- 6.3 Movimiento del suelo mediano (g) segun Youngs et al. (1997)
CREATE OR REPLACE FUNCTION youngs1997(r_km double precision, mag double precision, h_km double precision,
                                      p_sitio text, p_medida text)
RETURNS double precision LANGUAGE sql STABLE AS $$
    SELECT exp(c.a1 + c.a2 * mag + c.c1 + c.c2 * power(c.a3 - mag, 3)
               + c.c3 * ln(r_km + c.a4 * exp(c.a5 * mag)) + c.a6 * h_km)
    FROM youngs1997_coef c
    WHERE c.sitio = p_sitio AND c.medida = p_medida
$$;

-- 7.1 Sub-tramos de cada arista y su distancia al epicentro (ST_Distance sobre geography = metros)
CREATE TABLE falla_subtramo AS
WITH s AS (SELECT geom, magnitud AS mag, profundidad_km AS h FROM sismo ORDER BY magnitud DESC LIMIT 1),
     par AS (SELECT * FROM parametros_falla),
     sub AS (
        SELECT e.id AS arista_id, k.k AS sub_id, q.n AS n_sub, e.largo_m / 1000.0 / q.n AS largo_km,
               ST_LineInterpolatePoint(e.geom, ((k.k + 0.5) / q.n)::double precision) AS punto
        FROM fibra_red e
        CROSS JOIN par
        CROSS JOIN LATERAL (SELECT greatest(1, ceil(e.largo_m / 1000.0 / par.paso_max_km))::integer AS n) q
        CROSS JOIN LATERAL generate_series(0, q.n - 1) AS k(k)),
     d AS (
        SELECT sub.*, ST_Distance(sub.punto::geography, s.geom::geography) AS dist_epicentro_m, s.mag, s.h
        FROM sub CROSS JOIN s),
     r AS (
        SELECT d.*, sqrt((d.dist_epicentro_m / 1000.0) ^ 2 + d.h ^ 2) AS dist_hipocentral_km FROM d),
     m AS (
        SELECT r.*, par.*,
               youngs1997(r.dist_hipocentral_km, r.mag, r.h, par.sitio, 'pga') AS pga_g,
               youngs1997(r.dist_hipocentral_km, r.mag, r.h, par.sitio, 'sa1') AS sa1_g
        FROM r CROSS JOIN par)
SELECT arista_id, sub_id, n_sub, largo_km,
       ST_X(punto) AS lon, ST_Y(punto) AS lat,
       dist_epicentro_m, dist_hipocentral_km, pga_g, sa1_g,
       pgv_por_g * sa1_g                                     AS pgv_cm_s,
       k_ala * rr_por_cm_s_km * pgv_por_g * sa1_g            AS rr_por_km,
       k_ala * rr_por_cm_s_km * pgv_por_g * sa1_g * largo_km AS cortes_esperados
FROM m;
ALTER TABLE falla_subtramo ADD PRIMARY KEY (arista_id, sub_id);

-- 7.2 Columnas de resultado en la tabla de aristas (p_falla ya existe con DEFAULT 0, ver 03_topologia.sql)
ALTER TABLE fibra_red ADD COLUMN IF NOT EXISTS p_falla double precision NOT NULL DEFAULT 0
    CHECK (p_falla >= 0 AND p_falla <= 1);
ALTER TABLE fibra_red
    ADD COLUMN IF NOT EXISTS dist_epicentro_m    double precision,
    ADD COLUMN IF NOT EXISTS dist_hipocentral_km double precision,
    ADD COLUMN IF NOT EXISTS pga_g               double precision,
    ADD COLUMN IF NOT EXISTS pgv_cm_s            double precision,
    ADD COLUMN IF NOT EXISTS cortes_esperados    double precision;

-- 7.3 Se actualiza la base: probabilidad de cada arista y, para mostrar, los valores en su punto medio
UPDATE fibra_red SET p_falla = 0, dist_epicentro_m = NULL, dist_hipocentral_km = NULL, pga_g = NULL, pgv_cm_s = NULL,
                     cortes_esperados = NULL;
WITH s AS (SELECT geom, magnitud AS mag, profundidad_km AS h FROM sismo ORDER BY magnitud DESC LIMIT 1),
     par AS (SELECT * FROM parametros_falla),
     lam AS (SELECT arista_id, sum(cortes_esperados) AS cortes FROM falla_subtramo GROUP BY arista_id),
     medio AS (
        SELECT e.id, ST_Distance(ST_LineInterpolatePoint(e.geom, 0.5)::geography, s.geom::geography) AS d_m, s.mag, s.h
        FROM fibra_red e CROSS JOIN s),
     medio_r AS (SELECT medio.*, sqrt((d_m / 1000.0) ^ 2 + h ^ 2) AS r_km FROM medio)
UPDATE fibra_red e
SET cortes_esperados    = lam.cortes,
    p_falla             = 1 - exp(-lam.cortes),
    dist_epicentro_m    = mr.d_m,
    dist_hipocentral_km = mr.r_km,
    pga_g               = youngs1997(mr.r_km, mr.mag, mr.h, par.sitio, 'pga'),
    pgv_cm_s            = par.pgv_por_g * youngs1997(mr.r_km, mr.mag, mr.h, par.sitio, 'sa1')
FROM lam, medio_r mr, par
WHERE lam.arista_id = e.id AND mr.id = e.id;

COMMENT ON COLUMN fibra_red.dist_epicentro_m    IS 'Distancia geodesica (ST_Distance sobre geography, metros) del punto medio de la arista al epicentro';
COMMENT ON COLUMN fibra_red.dist_hipocentral_km IS 'Distancia hipocentral del punto medio: sqrt(d_epicentro^2 + H^2), km';
COMMENT ON COLUMN fibra_red.pga_g               IS 'PGA mediana en el punto medio (Youngs et al. 1997), g';
COMMENT ON COLUMN fibra_red.pgv_cm_s            IS 'PGV mediana en el punto medio (Newmark y Hall 1982), cm/s';
COMMENT ON COLUMN fibra_red.cortes_esperados    IS 'Cortes esperados en la arista: suma de RR*largo de sus sub-tramos (ALA 2001)';

-- 7.4 Probabilidad por tramo (aristas entre dos intersecciones): falla si se corta alguna de sus aristas.
-- 1 - exp(-suma de cortes) = 1 - producto de (1 - p) de sus aristas.
CREATE VIEW falla_tramo AS
SELECT tramo_id,
       count(*)                                    AS aristas,
       sum(largo_m) / 1000                         AS largo_km,
       sum(cortes_esperados)                       AS cortes_esperados,
       1 - exp(-sum(cortes_esperados))             AS p_falla,
       avg(pga_g)                                  AS pga_g,
       avg(pgv_cm_s)                               AS pgv_cm_s,
       avg(dist_hipocentral_km)                    AS dist_hipocentral_km,
       ST_LineMerge(ST_Collect(geom ORDER BY orden)) AS geom
FROM fibra_red
GROUP BY tramo_id;
