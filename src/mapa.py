"""Punto 10: mapa Leaflet con la infraestructura, el dano del terremoto (probabilidad de falla por enlace) y los ATTR."""
import json
import math
import os

import numpy as np

from . import falla as F
from .db import SALIDA_DIR, consultar

HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Resiliencia de la fibra óptica chilena ante el terremoto de Maule 2010</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css">
<style>
 html,body{height:100%;margin:0;font-family:system-ui,Segoe UI,Arial,sans-serif}
 #map{height:100%}
 .panel{background:#fff;padding:10px 12px;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.35);font-size:12.5px;line-height:1.35;max-width:330px}
 .panel h4{margin:0 0 6px;font-size:13.5px}
 .panel table{border-collapse:collapse;width:100%}
 .panel td,.panel th{padding:2px 4px;text-align:right}.panel td:first-child,.panel th:first-child{text-align:left}
 .panel th{border-bottom:1px solid #999}
 .sw{display:inline-block;width:22px;height:5px;margin-right:6px;vertical-align:middle}
 .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle;border:1px solid #333}
 .neg{color:#b71c1c;font-weight:600}
</style></head><body><div id="map"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<script>
const DATA = __DATA__;
const map = L.map('map').setView([-35.5, -71.5], 6);
const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18, attribution: '&copy; OpenStreetMap'}).addTo(map);

function color(p){ return p<0.02?'#1a9850':p<0.05?'#91cf60':p<0.10?'#fee08b':p<0.20?'#fc8d59':p<0.40?'#d73027':'#7f0000'; }
function pct(x){ return (100*x).toFixed(x<0.01?2:1)+' %'; }

// 1) infraestructura en operacion (gris) y 2) probabilidad de falla por enlace (color)
const operacion = L.geoJSON(DATA.tramos, {style:{color:'#555',weight:1.6,opacity:.85}});
const danio = L.geoJSON(DATA.tramos, {
  style: f => ({color: color(f.properties.p), weight: 2 + 4*Math.min(1, f.properties.p/0.4), opacity: .95}),
  onEachFeature: (f, l) => l.bindPopup(
    '<b>Enlace (tramo) '+f.properties.id+'</b><br>Largo: '+f.properties.km+' km<br><b>Probabilidad de falla: '+pct(f.properties.p)+
    '</b><br>PGA mediana: '+f.properties.pga.toFixed(2)+' g<br>PGV mediana: '+f.properties.pgv.toFixed(1)+' cm/s<br>Distancia al hipocentro: '+f.properties.d.toFixed(0)+' km')
}).addTo(map);

// 3) zonas de intensidad estimadas (anillos de PGA del modelo) y epicentro
const anillos = L.layerGroup();
DATA.anillos.forEach(a => L.circle([DATA.sismo.lat, DATA.sismo.lon], {radius: a.radio_km*1000, color: a.color, weight: 1.2, fill: true, fillOpacity: 0.06, interactive: false})
  .addTo(anillos).bindTooltip('PGA ≥ '+a.pga+' g (≈ '+a.radio_km+' km del epicentro)', {sticky:true}));
const epi = L.marker([DATA.sismo.lat, DATA.sismo.lon]).bindPopup('<b>Epicentro</b><br>'+DATA.sismo.titulo+'<br>Mw '+DATA.sismo.mag+', profundidad '+DATA.sismo.prof+' km<br>'+DATA.sismo.id).addTo(map);
anillos.addTo(map);

// 4) nodos clave
const colores = {genera:'#1565c0', intercambia:'#6a1b9a', recibe:'#9e9e9e'};
const nodos = L.layerGroup();
DATA.nodos.forEach(n => L.circleMarker([n.lat, n.lon], {radius: n.rol==='recibe'?3:6, color:'#222', weight: n.conectado?1:2, dashArray: n.conectado?null:'2 2', fillColor: colores[n.rol], fillOpacity: n.conectado?0.9:0.25})
  .bindPopup('<b>'+n.nombre+'</b><br>Rol: '+n.rol+' ('+n.tipo+')<br>'+(n.conectado?'unido a la red ('+n.dist_km.toFixed(1)+' km al vértice)':'<i>sin fibra detectada (aislado)</i>')).addTo(nodos));
nodos.addTo(map);

L.control.layers({'OpenStreetMap': osm}, {
  'Probabilidad de falla por enlace': danio, 'Fibra en operación (gris)': operacion,
  'Anillos de intensidad (PGA del modelo)': anillos, 'Nodos clave': nodos}, {collapsed:false}).addTo(map);

// leyenda + ATTR
const info = L.control({position:'bottomleft'});
info.onAdd = function(){
  const d = L.DomUtil.create('div','panel');
  const a = DATA.attr_antes, p = DATA.attr_despues;
  const fila = (n,k) => '<tr><td>'+n+'</td><td>'+(100*a[k]).toFixed(2)+' %</td><td>'+(100*p[k]).toFixed(2)+' %</td><td class="neg">'+(100*(p[k]-a[k])).toFixed(2)+' pts</td></tr>';
  d.innerHTML = '<h4>ATTR (Average Two-Terminal Reliability)</h4><table><tr><th></th><th>antes</th><th>después</th><th>cambio</th></tr>'+
    fila('Todos los nodos clave','ATTR_total')+fila('Usuarios–proveedores','ATTR_genera-recibe')+fila('Usuarios–intercambio','ATTR_intercambia-recibe')+
    '</table><div style="margin-top:4px;color:#555">'+DATA.attr_nota+'</div>'+
    '<h4 style="margin-top:8px">Probabilidad de falla del enlace</h4>'+
    [['< 2 %','#1a9850'],['2–5 %','#91cf60'],['5–10 %','#fee08b'],['10–20 %','#fc8d59'],['20–40 %','#d73027'],['≥ 40 %','#7f0000']].map(x=>'<span class="sw" style="background:'+x[1]+'"></span>'+x[0]).join('<br>')+
    '<div style="margin-top:4px;color:#555">Todos los enlaces son fibra detectada: no hay enlaces sintéticos.</div>'+
    '<h4 style="margin-top:8px">Nodos clave</h4><span class="dot" style="background:#1565c0"></span>generan (centros de datos)<br><span class="dot" style="background:#6a1b9a"></span>intercambian (IXP, amarres de cables)<br><span class="dot" style="background:#9e9e9e"></span>reciben (comunas); borde punteado = sin fibra';
  L.DomEvent.disableClickPropagation(d); return d; };
info.addTo(map);
</script></body></html>
"""


def _radio_para_pga(pga_objetivo, mag, prof, sitio):
    """Distancia hipocentral (km) a la que la PGA mediana del modelo baja a pga_objetivo (biseccion)."""
    lo, hi = 1.0, 3000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        pga = float(F.movimiento_suelo(np.array(mid), mag, prof, sitio)[0])
        lo, hi = (mid, hi) if pga > pga_objetivo else (lo, mid)
    return math.sqrt(max((lo + hi) / 2, prof) ** 2 - prof ** 2)   # a distancia sobre el suelo (epicentral)


def construir_datos(con):
    """Todo sale de la base: tramos y probabilidades de la vista falla_tramo (sobre fibra_red.p_falla), nodos y sismo."""
    s = F.datos_sismo(con)
    sitio = consultar(con, "SELECT sitio FROM parametros_falla")[0][0]
    tramos = []
    filas = consultar(con, """SELECT tramo_id, ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.0003), 5), p_falla,
                                     round(largo_km::numeric, 2), pga_g, pgv_cm_s, dist_hipocentral_km
                              FROM falla_tramo ORDER BY tramo_id""")
    for i, g, p, km, pga, pgv, d in filas:
        tramos.append({"type": "Feature", "geometry": json.loads(g),
                       "properties": {"id": i, "p": round(float(p), 5), "km": float(km), "pga": float(pga), "pgv": float(pgv), "d": float(d)}})
    nodos = [{"nombre": n, "rol": r, "tipo": t, "lon": float(x), "lat": float(y), "conectado": bool(c), "dist_km": float(d or 0) / 1000}
             for n, r, t, x, y, c, d in consultar(con, """SELECT n.nombre, n.rol, n.tipo, ST_X(n.geom), ST_Y(n.geom), r.conectado, r.dist_m
                                                           FROM nodos_clave n JOIN nodo_red r ON r.nodo_id = n.id""")]
    anillos = []
    for pga, color in ((0.30, "#b71c1c"), (0.20, "#e65100"), (0.10, "#f9a825"), (0.05, "#558b2f")):
        anillos.append({"pga": pga, "radio_km": round(_radio_para_pga(pga, s["mag"], s["prof"], sitio)), "color": color})
    return {"tramos": {"type": "FeatureCollection", "features": tramos}, "nodos": nodos, "anillos": anillos,
            "sismo": {"lat": s["lat"], "lon": s["lon"], "mag": s["mag"], "prof": s["prof"], "id": s["id"], "titulo": "2010 Maule, Chile Earthquake (USGS)"}}


def generar_mapa(con, attr_antes, attr_despues, ruta=None):
    datos = construir_datos(con)
    datos["attr_antes"] = {k: v for k, v in attr_antes.items() if k.startswith("ATTR_") and not k.endswith("ic95")}
    datos["attr_despues"] = {k: v for k, v in attr_despues.items() if k.startswith("ATTR_") and not k.endswith("ic95")}
    fuente_fibra = consultar(con, "SELECT string_agg(DISTINCT fuente, ',') FROM fibra_raw")[0][0]
    fuente = {"transparencia": "shapefile oficial de Subtel (Transparencia)", "internet_archive": "copia del Internet Archive"}.get(fuente_fibra, fuente_fibra)
    datos["attr_nota"] = (f"Fibra: proyecto Yafün, {fuente}. {attr_despues['muestras']:,} escenarios de falla (Monte Carlo). "
                          f"{attr_antes['n_nodos_unidos_a_la_red']} de {attr_antes['n_nodos_clave']} nodos clave tienen fibra detectada.")
    ruta = ruta or os.path.join(SALIDA_DIR, "mapa.html")
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write(HTML.replace("__DATA__", json.dumps(datos, ensure_ascii=False, separators=(",", ":"))))
    return ruta
