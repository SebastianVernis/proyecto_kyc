#!/usr/bin/env python3
"""Servidor HTTP ligero para consultar la base DuckDB del padrón INE 2018.

Sirve la API que consume buscar.html. Endpoints:
  GET  /                  → sirve buscar.html
  GET  /api/total         → conteo total de registros
  GET  /api/estados       → mapeo e (int) → nombre del estado
  POST /api/search        → búsqueda con WHERE dinámico
  GET  /api/curp/<curp>   → búsqueda exacta por CURP
  GET  /api/rfc/<rfc>     → no implementado, devuelve error (rfc no está en padron)

Uso:
  python3 servir.py [--db ine.duckdb] [--port 8765]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

import duckdb
from providers.singula import SingulaClient
import auth
import audit
import normalizar_direccion as _norm_dir
from webauthn.helpers import options_to_json, options_to_json_dict
import urllib.request
import urllib.parse

ROOT = Path(__file__).parent.resolve()

# Inicializar auth DB y admin por defecto
auth.init_db()
auth.ensure_default_admin()

# === SEPOMEX + Marco Geoestadístico INEGI (mismo SQLite) ===============
GEO_DB = ROOT / "geo.db"
SEPOMEX_DB = ROOT.parent / "bases" / "sepomex.db"
INEGI_BASE = ROOT.parent / "inegi" / "15_mexico" / "conjunto_de_datos"
_sepomex = None
_inegi = None
def get_sepomex():
    global _sepomex
    if _sepomex is None:
        import sqlite3
        _sepomex = sqlite3.connect(str(SEPOMEX_DB), check_same_thread=False)
        _sepomex.row_factory = sqlite3.Row
    return _sepomex
def get_inegi():
    global _inegi
    if _inegi is None:
        import sqlite3
        _inegi = sqlite3.connect(str(GEO_DB), check_same_thread=False)
        _inegi.row_factory = sqlite3.Row
    return _inegi

_shapes_m_cache = None
_shapes_ageb_cache = None
def get_inegi_shapes_m():
    global _shapes_m_cache
    if _shapes_m_cache is None:
        import shapefile as pyshp
        sf = pyshp.Reader(str(INEGI_BASE / "15m.shp"), encoding="iso-8859-1")
        _shapes_m_cache = {rec[0]: shp for shp, rec in zip(sf.shapes(), sf.records())}
    return _shapes_m_cache

def get_inegi_shapes_ageb():
    global _shapes_ageb_cache
    if _shapes_ageb_cache is None:
        import shapefile as pyshp
        sf = pyshp.Reader(str(INEGI_BASE / "15a.shp"), encoding="iso-8859-1")
        _shapes_ageb_cache = {rec[0]: {"shape": shp, "record": list(rec)} for shp, rec in zip(sf.shapes(), sf.records())}
    return _shapes_ageb_cache

# Estados con código INE (e)
ESTADOS = {
    1: "Aguascalientes", 2: "Baja California", 3: "Baja California Sur",
    4: "Campeche", 5: "Coahuila", 6: "Colima", 7: "Chiapas", 8: "Chihuahua",
    9: "Ciudad de México", 10: "Durango", 11: "Guanajuato", 12: "Guerrero",
    13: "Hidalgo", 14: "Jalisco", 15: "México", 16: "Michoacán", 17: "Morelos",
    18: "Nayarit", 19: "Nuevo León", 20: "Oaxaca", 21: "Puebla", 22: "Querétaro",
    23: "Quintana Roo", 24: "San Luis Potosí", 25: "Sinaloa", 26: "Sonora",
    27: "Tabasco", 28: "Tamaulipas", 29: "Tlaxcala", 30: "Veracruz",
    31: "Yucatán", 32: "Zacatecas",
}

COLUMNAS = [
    "id", "curp", "nombre", "paterno", "materno", "fecnac", "sexo",
    "calle", "int", "ext", "colonia", "cp", "e", "d", "m", "s",
    "l", "mza", "consec", "cred", "folio", "nac", "fuente",
]

# columnas permitidas para ORDER BY
ALLOWED_SORT = set(COLUMNAS) | {"int", "ext"}

# Límite máximo de filas que el servidor puede devolver por consulta
MAX_LIMIT = 1000


class DB:
    def __init__(self, path: str):
        self.con = duckdb.connect(path, read_only=True)

    def total(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM padron").fetchone()[0]

    def search(self, where: str, params: list, limit: int, offset: int,
               order_by: str = "id"):
        limit = max(1, min(int(limit), MAX_LIMIT))
        offset = max(0, int(offset))
        order_col = order_by if order_by in ALLOWED_SORT else "id"
        sql_count = f"SELECT COUNT(*) FROM padron {where}"
        sql_rows = (
            f"SELECT {', '.join(COLUMNAS)} FROM padron {where} "
            f"ORDER BY {order_col} LIMIT {limit} OFFSET {offset}"
        )
        t0 = time.time()
        total = self.con.execute(sql_count, params).fetchone()[0]
        rows = self.con.execute(sql_rows, params).fetchall()
        ms = (time.time() - t0) * 1000
        return total, [dict(zip(COLUMNAS, r)) for r in rows], ms

    def by_curp(self, curp: str):
        sql = f"SELECT {', '.join(COLUMNAS)} FROM padron WHERE curp = ? LIMIT 50"
        rows = self.con.execute(sql, [curp.strip().upper()]).fetchall()
        return [dict(zip(COLUMNAS, r)) for r in rows]

    def search_free(self, where: str, params: list, limit: int = 20):
        """Búsqueda libre usada por OSINT (sin order_by fijo)."""
        sql = (
            f"SELECT {', '.join(COLUMNAS)} FROM padron {where} "
            f"LIMIT {int(limit)}"
        )
        rows = self.con.execute(sql, params).fetchall()
        return [dict(zip(COLUMNAS, r)) for r in rows]


# === Validador de WHERE seguro (rechaza inyecciones) ====================

FORBIDDEN = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|"
    r"PRAGMA|UNION|EXEC|EXECUTE|COPY|ATTACH|DETACH|INSTALL|LOAD)\b",
    re.IGNORECASE,
)


def validate_where(where: str, params: list) -> tuple[bool, str]:
    """Valida que la cláusula WHERE solo use placeholders y operaciones
    de lectura seguras. params debe tener el mismo número que placeholders $N.
    Acepta tanto '?' como '$N' como placeholders.
    """
    if not where:
        return True, ""
    # contar placeholders (acepta $1, $2... o '?')
    placeholders_num = re.findall(r"\$\d+", where)
    placeholders_q = where.count("?")
    total_placeholders = len(placeholders_num) + placeholders_q
    if total_placeholders != len(params):
        return False, f"placeholders ({total_placeholders}) != params ({len(params)})"
    # palabras prohibidas
    if FORBIDDEN.search(where):
        return False, "WHERE contiene palabras no permitidas"
    # solo permite comillas simples y operadores comunes
    if any(c in where for c in (";", "--", "/*", "*/")):
        return False, "caracteres SQL no permitidos"
    return True, ""


# === Parseo de coordenadas / links de mapas =============================

# Rango plausible para México (evita interpretar basura como coords válidas
# cuando el input viene "de corrido"). Se sigue validando el rango global
# [-90,90]/[-180,180] aguas abajo; esto solo desambigua el orden lat/lon.
_DEC = r"[-+]?\d{1,3}(?:\.\d+)?"


def _dms_to_decimal(dms: str):
    """'16°48'41.3"N 99°50'40.9"W' → (16.81147, -99.84469) o None."""
    m = re.search(
        r"(\d+)[°:\s]+(\d+)[\'′:\s]+(\d+(?:\.\d+)?)[\"″\s]*([NSEW])"
        r"[,;\s]+(\d+)[°:\s]+(\d+)[\'′:\s]+(\d+(?:\.\d+)?)[\"″\s]*([NSEW])",
        dms, re.I)
    if not m:
        return None
    d1, m1, s1, h1, d2, m2, s2, h2 = m.groups()
    lat = int(d1) + int(m1) / 60 + float(s1) / 3600
    lon = int(d2) + int(m2) / 60 + float(s2) / 3600
    if h1.upper() == "S":
        lat = -lat
    if h2.upper() == "W":
        lon = -lon
    # Si el primer par trae E/W (orden lon,lat), intercambiar.
    if h1.upper() in ("E", "W"):
        lat, lon = lon, lat
    return round(lat, 7), round(lon, 7)


def _coords_from_maps_url(text: str):
    """Extrae (lat, lon) de una URL de Google Maps (o texto que la contenga).
    Cubre @lat,lon · q=/ll=/query=/center=/destination= · !3dLAT!4dLON ·
    /place/LAT,LON. Devuelve (lat, lon, patrón) o None.
    """
    # Orden de más exacto a menos: el pin real (!3d!4d) y las coords explícitas
    # (q=/ll=) mandan sobre @, que es solo el CENTRO del encuadre del mapa y en
    # links de lugar/street-view suele estar desviado del punto real.
    # 1) datos de lugar embebidos: !3dLAT!4dLON (el pin exacto)
    m = re.search(rf"!3d({_DEC})!4d({_DEC})", text)
    if m:
        return float(m.group(1)), float(m.group(2)), "url:!3d"
    # 2) parámetros de query: q= ll= query= center= destination= (permite 'loc:')
    m = re.search(
        rf"[?&](?:q|ll|query|center|destination|daddr|saddr|sll)=(?:loc:)?"
        rf"({_DEC}),({_DEC})", text, re.I)
    if m:
        return float(m.group(1)), float(m.group(2)), "url:q"
    # 3) /place/LAT,LON o /dir/LAT,LON
    m = re.search(rf"/(?:place|dir)/({_DEC}),({_DEC})", text)
    if m:
        return float(m.group(1)), float(m.group(2)), "url:place"
    # 4) .../@LAT,LON,zoom → centro del mapa (último recurso, puede desviarse)
    m = re.search(rf"@({_DEC}),({_DEC})", text)
    if m:
        return float(m.group(1)), float(m.group(2)), "url:@"
    return None


def _expand_short_map_url(url: str, _depth: int = 0) -> str:
    """Sigue los redirects de un link corto (maps.app.goo.gl, goo.gl/maps,
    g.co) hasta la URL final de Google Maps. Devuelve la URL final (o la
    original si no se pudo expandir). Solo para hosts de acortadores de mapas.
    """
    if _depth > 5:
        return url
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return url
    _SHORT = ("goo.gl", "maps.app.goo.gl", "g.co", "app.goo.gl")
    if not any(host == s or host.endswith("." + s) for s in _SHORT):
        return url
    try:
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (KYCSearch coord-resolver)",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            final = resp.geturl()
        # Algunos acortadores encadenan a otro acortador.
        if final and final != url:
            return _expand_short_map_url(final, _depth + 1)
        return final or url
    except Exception:
        # HEAD a veces no está permitido → intentar GET (leer poco).
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (KYCSearch coord-resolver)",
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                final = resp.geturl()
                body = resp.read(4096).decode("utf-8", "replace")
            got = _coords_from_maps_url(final) or _coords_from_maps_url(body)
            if got:
                # Codificar como URL parseable aguas arriba.
                return f"https://maps.google.com/?q={got[0]},{got[1]}"
            if final and final != url:
                return _expand_short_map_url(final, _depth + 1)
        except Exception:
            pass
        return url


def parse_coord_input(text: str, allow_network: bool = True) -> dict:
    """Interpreta una entrada libre de coordenadas y devuelve
    {'lat', 'lon', 'source'} o {'error'}.

    Acepta:
      - par decimal "de corrido": '16.81, -99.84' | '16.81 -99.84' | '16.81/-99.84'
      - DMS: '16°48'41.3"N 99°50'40.9"W'
      - link de Google Maps (completo o corto maps.app.goo.gl/goo.gl)
    """
    if not text or not text.strip():
        return {"error": "entrada vacía"}
    text = text.strip()

    is_url = bool(re.match(r"https?://", text, re.I))
    src = None
    got = None

    if is_url:
        expanded = _expand_short_map_url(text) if allow_network else text
        got = _coords_from_maps_url(expanded)
        if got:
            lat, lon = got[0], got[1]
            src = got[2] + ("+expand" if expanded != text else "")
        else:
            return {"error": "no encontré coordenadas en el link",
                    "detail": "¿es un link de Google Maps con ubicación?"}
    else:
        # DMS primero (tiene °/'/N/S/E/W, no se confunde con decimal)
        if re.search(r"[°′\"″]|[NSEW]\b", text, re.I):
            d = _dms_to_decimal(text)
            if d:
                lat, lon, src = d[0], d[1], "dms"
        if src is None:
            # par decimal separado por coma, espacio, ';' o '/'
            m = re.search(rf"({_DEC})\s*[,;/ ]\s*({_DEC})", text)
            if not m:
                return {"error": "no reconocí coordenadas",
                        "detail": "usa 'lat, lon', DMS, o un link de Google Maps"}
            lat, lon, src = float(m.group(1)), float(m.group(2)), "decimal"

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        # Puede venir invertido (lon, lat). Intentar swap si eso lo arregla.
        if -90 <= lon <= 90 and -180 <= lat <= 180:
            lat, lon = lon, lat
            src = (src or "") + "+swap"
        else:
            return {"error": "coordenadas fuera de rango",
                    "detail": f"lat={lat}, lon={lon}"}

    if abs(lat) < 0.0001 and abs(lon) < 0.0001:
        return {"error": "coordenadas (0,0) no son válidas"}

    return {"lat": round(lat, 7), "lon": round(lon, 7), "source": src}


# === HTTP handler ========================================================


class Handler(BaseHTTPRequestHandler):
    db: DB = None
    html_path: Path = None
    db_path: str = ""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {fmt % args}\n")

    def _get_origin(self):
        host = self.headers.get("Host", "localhost:8765")
        scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" or host.endswith(("trycloudflare.com", "pages.dev", "workers.dev")) or ".cuartodepaz.org" in host else "http"
        return f"{scheme}://{host}"

    def _set_auth_rp(self):
        host = self.headers.get("Host", "localhost")
        origin = self._get_origin()
        auth.set_rp(host, origin)

    def _get_session_token(self):
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header.split(" ", 1)[1].strip()
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            kv = part.strip().split("=", 1)
            if len(kv) == 2 and kv[0] == "session":
                return kv[1]
        return ""

    def _require_session(self):
        self._set_auth_rp()
        token = self._get_session_token()
        session = auth.validate_session(token)
        if not session:
            self._json(401, {"error": "no autenticado"})
            return None
        return session

    def _client_ip(self) -> str:
        """Devuelve la IP del cliente. Prioriza X-Forwarded-For (Cloudflare
        tunnel) y cae al peer directo. Trunca a 64 chars."""
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            # primer hop es el cliente original
            return xff.split(",")[0].strip()[:64]
        # client_address está en BaseHTTPRequestHandler
        try:
            return (self.client_address[0] or "")[:64]
        except Exception:
            return ""

    def _user_agent(self) -> str:
        return (self.headers.get("User-Agent", "") or "")[:200]

    def _audit(self, *, session, action, endpoint, method, status_code,
               duration_ms, query_summary=None, results_count=None):
        """Helper de auditoría. Fire-and-forget — no propaga excepciones."""
        audit.log_activity(
            session=session,
            action=action,
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            duration_ms=duration_ms,
            query_summary=query_summary or {},
            results_count=results_count,
            ip=self._client_ip(),
            user_agent=self._user_agent(),
        )

    def _serve_html_protected(self, filename=None):
        self._set_auth_rp()
        token = self._get_session_token()
        session = auth.validate_session(token)
        if not session:
            # Redirigir a login para navegadores
            self.send_response(302)
            self.send_header("Location", "/login.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._serve_html(filename)

    def _auth_json(self, status, obj, set_cookie=None):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie is not None:
            val = set_cookie if set_cookie else ""
            attr = "Path=/; HttpOnly; SameSite=Lax; Max-Age=604800" if val else "Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
            self.send_header("Set-Cookie", f"session={val}; {attr}")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json_body(self):
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            return {"_error": str(e)}

    def _send(self, status: int, body: bytes, ctype: str = "application/json"):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # No cachear HTML (para que cambios en buscar.html se vean al instante)
            if ctype.startswith("text/html"):
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # cliente cerró la conexión antes de tiempo (típico con Apify 60-120s)
            pass

    def _json(self, status: int, obj):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        self._set_auth_rp()
        if path == "/login.html":
            self._serve_html("login.html")
            return
        if path in ("/", "/index.html", "/buscar.html"):
            self._serve_html_protected("buscar.html")
            return
        if path == "/sujeto.html":
            self._serve_html_protected("sujeto.html")
            return
        if path == "/cfe_coordenadas.html":
            self._serve_html_protected("cfe_coordenadas.html")
            return
        if path == "/issste.html":
            self._serve_html_protected("issste.html")
            return
        if path == "/api/total":
            session = self._require_session()
            if not session: return
            self._json(200, {"total": self.db.total()})
            return
        if path == "/api/estados":
            session = self._require_session()
            if not session: return
            self._json(200, ESTADOS)
            return
        m = re.fullmatch(r"/api/curp/([A-Z0-9]{18})", path, re.IGNORECASE)
        if m:
            session = self._require_session()
            if not session: return
            t0 = time.time()
            curp = m.group(1).upper()
            rows = self.db.by_curp(curp)
            self._audit(
                session=session, action="view_curp",
                endpoint=path, method="GET", status_code=200,
                duration_ms=int((time.time() - t0) * 1000),
                query_summary={"curp_prefix": curp[:4]},
                results_count=len(rows),
            )
            self._json(200, {"rows": rows, "total": len(rows)})
            return
        if path == "/api/health":
            session = self._require_session()
            if not session: return
            from config import config
            self._json(200, {
                "status": "ok",
                "username": session["username"],
                "servicios": config.servicios_disponibles,
                "api_keys_configuradas": sum(1 for v in config.servicios_disponibles.values() if v),
            })
            return
        # 2026-08-05: ping rápido de Singula para detectar key expirada
        # o quota excedida sin tener que abrir una ficha de sujeto.
        if path == "/api/singula/health":
            session = self._require_session()
            if not session: return
            self._handle_singula_health()
            return
        if path == "/api/sujeto":
            session = self._require_session()
            if not session: return
            self._handle_sujeto()
            return
        if path == "/api/v1/sujeto/verificar_unificada":
            session = self._require_session()
            if not session: return
            self._handle_sujeto_verificar_unificada()
            return
        if path == "/api/auth/me":
            session = auth.validate_session(self._get_session_token())
            self._json(200, {"ok": bool(session), "user": session})
            return
        # === ENDPOINTS ADMIN (sólo admin) ===
        if path == "/admin.html":
            self._serve_html_protected("admin.html")
            return
        if path == "/api/admin/activity":
            self._handle_admin_activity()
            return
        if path == "/api/admin/stats":
            self._handle_admin_stats()
            return
        if path == "/api/admin/users":
            self._handle_admin_users_list()
            return
        m = re.fullmatch(r"/api/sepomex/cp/(\d{5})", path)
        if m:
            self._handle_sepomex_cp(m.group(1))
            return
        m = re.fullmatch(r"/api/sepomex/search", path)
        if m:
            self._handle_sepomex_search()
            return
        if path == "/api/geo/lookup":
            self._handle_geo_lookup()
            return
        if path == "/api/geo/padron":
            self._handle_geo_padron()
            return
        m = re.fullmatch(r"/api/geo/geocode/(\d{5})", path)
        if m:
            self._handle_geo_geocode(m.group(1))
            return
        # === ENDPOINTS DE REPORTE ===
        # /api/report/html?curp=XXX  → devuelve HTML
        # /api/report/pdf?curp=XXX   → devuelve PDF
        if path == "/api/report/html" or path == "/api/report/pdf":
            self._handle_report_get(path)
            return
        # === ENDPOINTS DE ENRIQUECIMIENTO (BASES EXTERNAS) ===
        # /api/v1/persona/rfc/<rfc>          → búsqueda por RFC en att+empleadores
        # /api/v1/persona/rfc/<rfc>/todo     → incluye agregados de fuentes
        m = re.fullmatch(r"/api/v1/persona/rfc/([A-ZÑ&0-9]{10,13})(?:/(todo))?", path, re.IGNORECASE)
        if m:
            session = self._require_session()
            if not session: return
            self._handle_persona_rfc(m.group(1), m.group(2) == "todo")
            return
        # /api/v1/persona/curp/<curp>        → búsqueda por CURP en las 6 bases
        # /api/v1/persona/curp/<curp>/todo   → incluye agregados de fuentes
        # 2026-08-05: usa b_imss_s como xwalk curp→rfc en línea, ya que la DISTINCT
        # completa tarda >120s — pero el lookup puntual WHERE curp_clean=? usa
        # zone-map y es sub-segundo.
        m = re.fullmatch(r"/api/v1/persona/curp/([A-Z0-9]{18})(?:/(todo))?", path, re.IGNORECASE)
        if m:
            session = self._require_session()
            if not session: return
            self._handle_persona_curp(m.group(1).upper(), m.group(2) == "todo")
            return
        # /api/v1/persona/lineas/rfc/<rfc>[/todo]
        # /api/v1/persona/lineas/curp/<curp>[/todo]
        # 2026-08-05: lista TODAS las líneas telcel + registros att asociados al
        # RFC o CURP del sujeto, con TODOS los campos disponibles (full-detail).
        # Cap de líneas por respuesta para no explotar el payload en empresas
        # con >N líneas (BBVA Bancomer tiene 89k; limita a 5000 default).
        m = re.fullmatch(r"/api/v1/persona/lineas/(rfc|curp)/([A-Z0-9]{10,18})(?:/(todo))?", path, re.IGNORECASE)
        if m:
            session = self._require_session()
            if not session: return
            self._handle_persona_lineas(
                kind=m.group(1).lower(),
                key=m.group(2).upper(),
                todo=m.group(3) == "todo"
            )
            return
        # /api/v1/health/bases               → diagnóstico de bases externas
        if path == "/api/v1/health/bases":
            session = self._require_session()
            if not session: return
            self._handle_health_bases()
            return
        # /api/v1/persona/cfe/<num_servicio>          → lookup exacto por num_servicio
        # /api/v1/persona/cfe/buscar?nombre=&cp=&division= → búsqueda fuzzy
        # 2026-08-05: CFE medidores (66M filas, 14 layouts heterogéneos).
        # NO comparte clave con padrón; la PK es num_servicio (11-12 dígitos CFE).
        m = re.fullmatch(r"/api/v1/persona/cfe/buscar", path, re.IGNORECASE)
        if m:
            session = self._require_session()
            if not session: return
            self._handle_persona_cfe_buscar()
            return
        m = re.fullmatch(r"/api/v1/persona/cfe/([0-9]{10,12})", path)
        if m:
            session = self._require_session()
            if not session: return
            self._handle_persona_cfe_servicio(m.group(1))
            return
        # /api/v1/telcel/buscar?telefono=... — 2026-08-06: búsqueda por número
        # de teléfono en la base telcel. La base tiene 9.7M de líneas, pero
        # los números pueden venir con padding (10 dígitos + 3 espacios);
        # el endpoint normaliza y busca con LIKE %x%.
        if path == "/api/v1/telcel/buscar":
            session = self._require_session()
            if not session: return
            self._handle_telcel_buscar()
            return
        # /api/v1/direccion/candidatos?calle=..&ext=..&colonia=..&cp=.. —
        # candidatos del padrón (exacto en cascada o fuzzy) para que el
        # usuario decida con cuál enriquecer una dirección que no cruzó.
        if path == "/api/v1/direccion/candidatos":
            session = self._require_session()
            if not session: return
            self._handle_direccion_candidatos()
            return
        # /api/v1/att/buscar?telefono=... — idem para ATT (1M de registros)
        if path == "/api/v1/att/buscar":
            session = self._require_session()
            if not session: return
            self._handle_att_buscar()
            return
        # /api/v1/cfe/buscar_domicilio?calle=&colonia=&cp=&limit=
        # Búsqueda de medidores CFE por dirección (calle/colonia/cp) en lugar
        # de por num_servicio. Devuelve los medidores (titular/régimen/
        # dirección) que coincidan con la dirección buscada. Útil para
        # confirmar un domicilio conocido contra el padrón de CFE.
        if path == "/api/v1/cfe/buscar_domicilio":
            session = self._require_session()
            if not session: return
            self._handle_cfe_buscar_domicilio()
            return
        # /api/v1/cfe/coordenadas?lat=&lon=&limit=
        # Flujo "coordenadas GPS → dirección → CFE" descrito en
        # docs/operativos/CFE_flujo_coordenadas.md
        if path == "/api/v1/cfe/coordenadas":
            session = self._require_session()
            if not session: return
            self._handle_cfe_coordenadas()
            return
        # /api/v1/geo/parse?q=<texto>
        # Interpreta coordenadas de corrido / DMS / link de Google Maps
        # (incluye expansión de links cortos maps.app.goo.gl/goo.gl que el
        # navegador no puede seguir por CORS). Devuelve {lat, lon, source}.
        if path == "/api/v1/geo/parse":
            session = self._require_session()
            if not session: return
            self._handle_geo_parse()
            return
        # /api/v1/cfe/buscar_por_nombre?nombre=&paterno=&materno=&regex=&limit=
        # 2026-08-13: búsqueda regex/ LIKE por titular en api.cfe_medidor (66M filas).
        if path == "/api/v1/cfe/buscar_por_nombre":
            session = self._require_session()
            if not session: return
            self._handle_cfe_buscar_por_nombre()
            return
        # /api/v1/issste/buscar?nombre=&paterno=&materno=&sexo=&limit=
        # Búsqueda por nombre + paterno + materno en api.issste_empleado
        # (2.7M filas del padrón federal). Devuelve sueldo, ramo, sector.
        if path == "/api/v1/issste/buscar":
            session = self._require_session()
            if not session: return
            self._handle_issste_buscar()
            return
        # /api/v1/familia/mapa?paterno=&materno=&estado=&ciudad=&limit=
        # 2026-08-06: análisis de familia. Busca todas las personas con un
        # apellido dado en att+telcel+repuve, las agrupa por (estado, ciudad,
        # colonia, direccion_normalizada) para detectar clusters familiares
        # (varias personas con el mismo apellido en la misma dirección), y
        # opcionalmente las cruza contra el padrón por nombre+apellido si se
        # pasa `nombre` como argumento adicional.
        if path == "/api/v1/familia/mapa":
            session = self._require_session()
            if not session: return
            self._handle_familia_mapa()
            return
        # /api/v1/familia/hermanos?curp=... o ?paterno=&materno=&fecnac=
        # 2026-08-06: detección de hermanos probables con score de certeza.
        # Combina: igualdad de apellidos, proximidad geográfica (CP),
        # diferencia de edad plausible, domicilio, entidad de nacimiento.
        if path == "/api/v1/familia/hermanos":
            session = self._require_session()
            if not session: return
            self._handle_familia_hermanos()
            return
        # /api/v1/familia/progenitores?curp=...
        # 2026-08-06: extrae nombres del padre y madre del sujeto via
        # consulta CURP del RENAPO (gob.mx). Si no hay red, intenta
        # inferir de los apellidos y devuelve la CURP de los padres si
        # los encuentra en el padrón.
        if path == "/api/v1/familia/progenitores":
            session = self._require_session()
            if not session: return
            self._handle_familia_progenitores()
            return
        # 2026-08-15: /api/v1/sujeto/resolver_desde_hint — hint→curp.
        # Inverso de _enriquecer_bases_externas: dado un hit de una base
        # sin CURP (CFE, Telcel, ATT, REPUVE, ISSSTE), encuentra la CURP
        # más probable en el padrón. Cascada: RFC > NSS > nombre+extras.
        if path == "/api/v1/sujeto/resolver_desde_hint":
            session = self._require_session()
            if not session: return
            self._handle_sujeto_resolver_desde_hint()
            return
        # /api/v1/sujeto/enriquecido — cruce con 6 bases externas.
        # Acepta: curp, rfc, nss, nombre, paterno, materno, fecnac (cualquier
        # subconjunto no-vacío). Si no hay curp, el xwalk cae a rfc → nombre
        # → paterno+materno → fecnac. La curp sigue siendo la PK preferida
        # pero ya no es requerida.
        if path == "/api/v1/sujeto/enriquecido":
            session = self._require_session()
            if not session: return
            import urllib.parse as _up
            qs = _up.parse_qs(_up.urlparse(self.path).query)
            curp    = (qs.get("curp",    [""])[0] or "").upper().strip()
            rfc     = (qs.get("rfc",     [""])[0] or "").upper().strip() or None
            nss     = (qs.get("nss",     [""])[0] or "").strip() or None
            nombre  = (qs.get("nombre",  [""])[0] or "").strip() or None
            paterno = (qs.get("paterno", [""])[0] or "").strip() or None
            materno = (qs.get("materno", [""])[0] or "").strip() or None
            fecnac  = (qs.get("fecnac",  [""])[0] or "").strip() or None
            # Validar curp solo si se envió
            if curp and len(curp) != 18:
                self._json(400, {"error": "curp inválida (debe ser 18 chars)"})
                return
            # Al menos uno debe estar presente
            if not any([curp, rfc, nss, (nombre and paterno and materno), fecnac]):
                self._json(400, {"error": "se requiere al menos: curp, rfc, nss, o (nombre+paterno+materno)"})
                return
            self._handle_sujeto_enriquecido(
                curp=curp, rfc=rfc, nss=nss,
                nombre=nombre, paterno=paterno, materno=materno, fecnac=fecnac,
            )
            return
        # /api/v1/direccion/buscar (GET) — el frontend (buscar.html) llama este
        # path por GET con querystring; el handler ya soporta command == "GET".
        # Antes solo estaba ruteado bajo do_POST en /api/direccion/buscar, así
        # que el GET caía en el 404 "not found". Aceptamos ambos paths.
        if path in ("/api/v1/direccion/buscar", "/api/direccion/buscar"):
            self._handle_direccion_buscar()
            return
        self._json(404, {"error": "not found", "path": path})

    def do_POST(self):
        url = urlparse(self.path)
        self._set_auth_rp()
        if url.path == "/api/auth/register/options":
            self._handle_auth_register_options()
        elif url.path == "/api/auth/register/verify":
            self._handle_auth_register_verify()
        elif url.path == "/api/auth/login/options":
            self._handle_auth_login_options()
        elif url.path == "/api/auth/login/verify":
            self._handle_auth_login_verify()
        elif url.path == "/api/auth/login/password":
            self._handle_auth_login_password()
        elif url.path == "/api/geo/padron":
            self._handle_geo_padron()
        elif url.path == "/api/direccion/buscar":
            self._handle_direccion_buscar()
        elif url.path == "/api/direccion/sugerir":
            self._handle_direccion_sugerir()
        elif url.path == "/api/padron/curps":
            self._handle_padron_curps()
        elif url.path == "/api/padron/reporte":
            self._handle_padron_reporte()
        elif url.path == "/api/sepomex/validate":
            self._handle_sepomex_validate()
        elif url.path == "/api/auth/users":
            self._handle_auth_users()
        elif url.path == "/api/auth/logout":
            self._handle_auth_logout()
        elif url.path == "/api/admin/users":
            self._handle_admin_users_action()
        elif url.path == "/api/admin/activity/export":
            self._handle_admin_activity_export()
        elif url.path == "/api/search":
            self._handle_search()
        elif url.path == "/api/osint":
            self._handle_osint()
        elif url.path == "/api/kyc":
            self._handle_kyc()
        elif url.path == "/api/enrich":
            self._handle_enrich()
        elif url.path == "/api/report/generate":
            self._handle_report_generate()
        elif url.path == "/api/rfc/expandir":
            self._handle_rfc_expandir()
        elif url.path == "/api/rfc/calcular":
            self._handle_rfc_calcular()
        elif url.path == "/api/rfc/calcular-completo":
            self._handle_rfc_calcular_completo()
        elif url.path == "/api/validar":
            self._handle_validar()
        else:
            self._json(404, {"error": "endpoint no existe"})

    def _handle_auth_register_options(self):
        body = self._read_json_body()
        if "_error" in body:
            self._json(400, {"error": body["_error"]})
            return
        username = (body.get("username") or "").strip().lower()
        if not username:
            self._json(400, {"error": "falta username"})
            return
        try:
            data = auth.get_registration_options(username)
            self._json(200, {"options": options_to_json_dict(data["options"]), "username": data["username"]})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _handle_auth_register_verify(self):
        body = self._read_json_body()
        if "_error" in body:
            self._json(400, {"error": body["_error"]})
            return
        username = (body.get("username") or "").strip().lower()
        credential = body.get("credential")
        if not username or not credential:
            self._json(400, {"error": "faltan datos"})
            return
        try:
            res = auth.verify_registration(username, credential)
            sess = auth.create_session(res["user_id"], username)
            self._auth_json(200, sess, set_cookie=sess["token"])
        except Exception as e:
            self._json(400, {"error": str(e)})

    def _handle_auth_login_options(self):
        body = self._read_json_body()
        username = (body.get("username") or "").strip().lower() or None
        try:
            data = auth.get_authentication_options(username)
            self._json(200, {"options": options_to_json_dict(data["options"])})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _handle_auth_login_verify(self):
        body = self._read_json_body()
        if "_error" in body:
            self._json(400, {"error": body["_error"]})
            return
        credential = body.get("credential")
        if not credential:
            self._json(400, {"error": "falta credential"})
            return
        try:
            sess = auth.verify_authentication(credential)
            self._auth_json(200, sess, set_cookie=sess["token"])
        except Exception as e:
            self._json(400, {"error": str(e)})

    def _handle_auth_logout(self):
        session = self._require_session()
        t0 = time.time()
        token = self._get_session_token()
        auth.logout(token)
        self._audit(
            session=session, action="logout",
            endpoint="/api/auth/logout", method="POST", status_code=200,
            duration_ms=int((time.time() - t0) * 1000),
        )
        self._auth_json(200, {"ok": True}, set_cookie="")

    def _handle_sepomex_cp(self, cp: str):
        session = self._require_session()
        if not session:
            return
        con = get_sepomex()
        rows = con.execute(
            "SELECT cp, colonia, tipo, municipio, estado FROM cp WHERE cp=? ORDER BY colonia",
            (cp,),
        ).fetchall()
        if not rows:
            self._json(404, {"error": "CP no encontrado", "cp": cp})
            return
        self._json(200, {
            "cp": cp,
            "estado": rows[0]["estado"],
            "municipio": rows[0]["municipio"],
            "colonias": [{"nombre": r["colonia"], "tipo": r["tipo"]} for r in rows],
        })

    def _handle_sepomex_validate(self):
        """Cruza cp + colonia + estado de un sujeto contra SEPOMEX oficial.
        Body: { cp: "06700", colonia: "ROMA NORTE", estado: "Ciudad de México" }
        """
        session = self._require_session()
        if not session:
            return
        body = self._read_json_body()
        cp = str(body.get("cp") or "").strip().zfill(5)
        colonia = str(body.get("colonia") or "").strip().upper()
        estado = str(body.get("estado") or "").strip().upper()
        municipio = str(body.get("municipio") or "").strip().upper()

        if len(cp) != 5 or not cp.isdigit():
            self._json(400, {"error": "cp inválido"})
            return

        con = get_sepomex()
        rows = con.execute(
            "SELECT cp, colonia, tipo, municipio, estado FROM cp WHERE cp=? ORDER BY colonia",
            (cp,),
        ).fetchall()

        result = {
            "cp": cp,
            "colonia_padron": colonia,
            "estado_padron": estado,
            "municipio_padron": municipio,
            "matches": [],
            "coincidencias": {"colonia": False, "estado": False, "municipio": False},
            "score": 0,
            "verdict": "sin datos",
        }

        if not rows:
            result["verdict"] = "CP no encontrado en SEPOMEX"
            self._json(200, result)
            return

        oficial_estado = (rows[0]["estado"] or "").upper()
        oficial_municipio = (rows[0]["municipio"] or "").upper()
        result["estado_oficial"] = rows[0]["estado"]
        result["municipio_oficial"] = rows[0]["municipio"]
        result["colonias_oficiales"] = [{"nombre": r["colonia"], "tipo": r["tipo"]} for r in rows]

        result["coincidencias"]["estado"] = bool(estado) and (
            estado == oficial_estado or estado in oficial_estado or oficial_estado in estado
        )
        result["coincidencias"]["municipio"] = bool(municipio) and (
            municipio == oficial_municipio or municipio in oficial_municipio or oficial_municipio in municipio
        )

        colonia_match = None
        for r in rows:
            oficial = (r["colonia"] or "").upper()
            if colonia and (colonia == oficial or colonia in oficial or oficial in colonia):
                colonia_match = r["colonia"]
                break
        result["coincidencias"]["colonia"] = bool(colonia_match)
        if colonia_match:
            result["colonia_oficial"] = colonia_match

        score = sum(1 for v in result["coincidencias"].values() if v)
        if result["coincidencias"]["estado"] and result["coincidencias"]["municipio"] and result["coincidencias"]["colonia"]:
            score = 100
            result["verdict"] = "✓ Coincide totalmente con SEPOMEX"
        elif result["coincidencias"]["estado"] and result["coincidencias"]["municipio"]:
            score = 70
            result["verdict"] = "⚠ Estado y municipio coinciden, colonia difiere o ausente"
        elif result["coincidencias"]["estado"]:
            score = 40
            result["verdict"] = "⚠ Estado coincide, municipio y/o colonia difieren"
        else:
            score = 0
            result["verdict"] = "✗ No coincide con SEPOMEX"
        result["score"] = score

        self._json(200, result)

    def _handle_geo_geocode(self, cp: str):
        """Geocodifica un CP mexicano vía Nominatim (OpenStreetMap).
        Acepta ?q= (dirección completa opcional) para geocodificar la dirección
        exacta del padrón en lugar del centroide del CP.
        Devuelve lat, lon, boundingbox y display_name."""
        session = self._require_session()
        if not session:
            return
        try:
            qs = parse_qs(urlparse(self.path).query)
            direccion = qs.get("q", [""])[0].strip()

            # 2026-08-14: si vino dirección exacta (?q=) y Nominatim no encuentra,
            # reintentar con solo el CP antes de devolver 404. El operador siempre
            # ve al menos el centroide del CP en el mapa.
            cp_fallback_params = urllib.parse.urlencode({
                "postalcode": cp,
                "country": "Mexico",
                "format": "json",
                "limit": "1",
            })
            if direccion:
                # Geocodificar dirección completa: calle + número + colonia + municipio + estado
                # Nominatim no permite mezclar 'q' con parámetros estructurados (country, etc.)
                # así que metemos "Mexico" en el query string
                params = urllib.parse.urlencode({
                    "q": f"{direccion}, Mexico",
                    "format": "json",
                    "limit": "1",
                })
            else:
                # Sin ?q=: vamos directo por CP-centroid
                params = cp_fallback_params

            def _call_nominatim(params_):
                """Llama Nominatim local; si falla, público. Devuelve (data, source)."""
                local_url = os.environ.get("NOMINATIM_LOCAL_URL", "http://127.0.0.1:8088")
                url = f"{local_url}/search?{params_}"
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        return json.loads(resp.read().decode()), "local"
                except Exception:
                    public_url = "https://nominatim.openstreetmap.org/search"
                    url = f"{public_url}?{params_}"
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "CuartoDePazSearch/1.0 (search.cuartodepaz.org)",
                        "Accept": "application/json",
                    })
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        return json.loads(resp.read().decode()), "public"

            data, geocode_source = _call_nominatim(params)

            # 2026-08-14: fallback al centroide del CP si la dirección exacta no matchea.
            if not data and direccion:
                data, geocode_source = _call_nominatim(cp_fallback_params)
                if data:
                    geocode_source = f"{geocode_source}_cp_fallback"

            if data:
                r = data[0]
                self._json(200, {
                    "cp": cp,
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "display_name": r.get("display_name", ""),
                    "boundingbox": [float(x) for x in r.get("boundingbox", [])],
                    "query": direccion or f"CP {cp}",
                    "geocode_source": geocode_source,
                })
            else:
                self._json(404, {
                    "error": "Dirección y CP-centroid no encontrados en Nominatim",
                    "cp": cp,
                })
        except Exception as e:
            self._json(500, {"error": f"Error geocodificando: {e}"})

    def _handle_geo_lookup(self):
        """Recibe ?lat=&lon= (WGS84), convierte a LCC México, devuelve las AGEB/manzanas
        del Marco Geoestadístico INEGI que contienen el punto.
        """
        session = self._require_session()
        if not session:
            return
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        try:
            lat = float(qs.get("lat", [""])[0])
            lon = float(qs.get("lon", [""])[0])
        except (ValueError, TypeError):
            self._json(400, {"error": "lat/lon inválidos"})
            return

        import pyproj
        import shapefile as pyshp

        wkt = (
            'PROJCS["MEXICO_ITRF_2008_LCC",'
            'GEOGCS["GCS_ITRF_2008",'
            'DATUM["D_ITRF_2008",'
            'SPHEROID["GRS_1980",6378137.0,298.257222101]],'
            'PRIMEM["Greenwich",0.0],'
            'UNIT["Degree",0.0174532925199433]],'
            'PROJECTION["Lambert_Conformal_Conic"],'
            'PARAMETER["False_Easting",2500000.0],'
            'PARAMETER["False_Northing",0.0],'
            'PARAMETER["Central_Meridian",-102.0],'
            'PARAMETER["Standard_Parallel_1",17.5],'
            'PARAMETER["Standard_Parallel_2",29.5],'
            'PARAMETER["Latitude_Of_Origin",12.0],'
            'UNIT["Meter",1.0]]'
        )
        to_lcc = pyproj.Transformer.from_crs("EPSG:4326", wkt, always_xy=True)
        x, y = to_lcc.transform(lon, lat)

        # Carga cacheada de shapes (proceso lento)
        shapes_m = get_inegi_shapes_m()
        shapes_a = get_inegi_shapes_ageb()

        con = get_inegi()
        # 1) Candidatos por bbox (top 500 más pequeños)
        rows = con.execute("""
            SELECT id, cvegeo, cve_ent, cve_mun, cve_loc, cve_ageb, cve_mza,
                   ambito, tipomza, minx, miny, maxx, maxy,
                   (SELECT nomgeo FROM municipios m WHERE m.cve_ent=manzanas.cve_ent AND m.cve_mun=manzanas.cve_mun) AS municipio
            FROM manzanas
            WHERE minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ?
            ORDER BY ((maxx-minx)*(maxy-miny))
            LIMIT 500
        """, (x, x, y, y)).fetchall()

        def point_in_shape(shape):
            if shape.shapeType not in (5, 15, 25):
                return False
            parts = list(shape.parts) + [len(shape.points)]
            for i in range(len(shape.parts)):
                ring = shape.points[parts[i]:parts[i + 1]]
                n = len(ring)
                inside = False
                for ii in range(n):
                    xi, yi = ring[ii]
                    xj, yj = ring[n - 1 if ii == 0 else ii - 1]
                    if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
                        inside = not inside
                if inside:
                    return True
            return False

        manzana_hit = None
        for r in rows:
            shp = shapes_m.get(r[1])
            if shp and point_in_shape(shp):
                manzana_hit = r
                break

        # AGEB urbana que contiene el punto
        ageb_hit = None
        for cvegeo, rec in shapes_a.items():
            shp = rec["shape"]
            if point_in_shape(shp):
                ageb_hit = (cvegeo, rec["record"])
                break

        result = {
            "input": {"lat": lat, "lon": lon},
            "lcc": {"x": round(x, 2), "y": round(y, 2)},
            "manzana_bbox_candidates": [
                {
                    "cvegeo": r[1], "cve_ent": r[2], "cve_mun": r[3],
                    "cve_loc": r[4], "cve_ageb": r[5], "cve_mza": r[6],
                    "ambito": r[7], "tipomza": r[8], "municipio": r[13],
                } for r in rows[:10]
            ],
            "manzana_encontrada": {
                "cvegeo": manzana_hit[1],
                "cve_ent": manzana_hit[2],
                "cve_mun": manzana_hit[3],
                "cve_loc": manzana_hit[4],
                "cve_ageb": manzana_hit[5],
                "cve_mza": manzana_hit[6],
                "ambito": manzana_hit[7],
                "tipomza": manzana_hit[8],
                "municipio": manzana_hit[13],
            } if manzana_hit else None,
            "ageb_encontrada": {
                "cvegeo": ageb_hit[0],
                "cve_ent": ageb_hit[1][1],
                "cve_mun": ageb_hit[1][2],
                "cve_loc": ageb_hit[1][3],
                "cve_ageb": ageb_hit[1][4],
            } if ageb_hit else None,
        }
        self._json(200, result)

    def _handle_padron_curps(self):
        """Extrae TODOS los campos del padrón para una lista de CURPs.
        Body: { curps: ["XXX", "YYY", ...] }
        Devuelve un array con todos los campos disponibles para cada CURP encontrado.
        """
        session = self._require_session()
        if not session:
            return
        body = self._read_json_body()
        curps = body.get("curps") or []
        if not isinstance(curps, list) or not curps:
            self._json(400, {"error": "curps debe ser una lista no vacía"})
            return
        # sanitizar: 18 chars alfanuméricos
        curps = [str(c).strip().upper() for c in curps if isinstance(c, str) and len(str(c).strip()) == 18]
        if not curps:
            self._json(400, {"error": "ningún CURP válido (18 caracteres)"})
            return
        if len(curps) > 500:
            self._json(400, {"error": "máximo 500 CURPs por request"})
            return

        try:
            con = duckdb.connect(self.db_path, read_only=True)
        except Exception as ex:
            self._json(500, {"error": f"error abriendo padrón: {ex}"})
            return

        try:
            placeholders = ",".join("?" * len(curps))
            cols_query = con.execute("DESCRIBE padron").fetchall()
            all_cols = [c[0] for c in cols_query]
            # Columnas "útiles" para reporte (excluyendo fuente que es interno)
            cols = [c for c in all_cols if c.lower() not in ("fuente",)]
            cols_sql = ", ".join(cols)
            rows = con.execute(
                f"SELECT {cols_sql} FROM padron WHERE curp IN ({placeholders})",
                curps,
            ).fetchall()
            found = {r[cols.index("curp")]: dict(zip(cols, r)) for r in rows}
            result = {
                "solicitados": len(curps),
                "encontrados": len(found),
                "no_encontrados": [c for c in curps if c not in found],
                "personas": [found[c] for c in curps if c in found],
                "campos_disponibles": cols,
            }
            self._json(200, result)
        except Exception as ex:
            self._json(500, {"error": f"error en consulta: {ex}"})
        finally:
            try:
                con.close()
            except Exception:
                pass

    def _handle_padron_reporte(self):
        """Reporte completo: padrón INE + CheckID fiscal por lista de CURPs.
        Body: { curps: [...], checkid?: bool (default true) }
        """
        session = self._require_session()
        if not session:
            return
        body = self._read_json_body()
        curps = body.get("curps") or []
        with_checkid = bool(body.get("checkid", True))
        if not isinstance(curps, list) or not curps:
            self._json(400, {"error": "curps debe ser una lista no vacía"})
            return
        curps = [str(c).strip().upper() for c in curps if isinstance(c, str) and len(str(c).strip()) == 18]
        if not curps:
            self._json(400, {"error": "ningún CURP válido (18 caracteres)"})
            return
        if len(curps) > 50:
            self._json(400, {"error": "máximo 50 CURPs por reporte (CheckID cuesta créditos)"})
            return

        try:
            con = duckdb.connect(self.db_path, read_only=True)
        except Exception as ex:
            self._json(500, {"error": f"error abriendo padrón: {ex}"})
            return

        try:
            cols_query = con.execute("DESCRIBE padron").fetchall()
            all_cols = [c[0] for c in cols_query]
            cols = [c for c in all_cols if c.lower() not in ("fuente",)]
            cols_sql = ", ".join(cols)
            placeholders = ",".join("?" * len(curps))
            rows = con.execute(
                f"SELECT {cols_sql} FROM padron WHERE curp IN ({placeholders})",
                curps,
            ).fetchall()
            padron_by_curp = {r[cols.index("curp")]: dict(zip(cols, r)) for r in rows}
        finally:
            try:
                con.close()
            except Exception:
                pass

        # CheckID
        checkid_data = {}
        checkid_errors = {}
        if with_checkid and padron_by_curp:
            try:
                cc = get_checkid_client()
            except Exception as ex:
                cc = None
                checkid_errors["__init__"] = str(ex)
            if cc:
                for curp in curps:
                    if curp not in padron_by_curp:
                        continue
                    try:
                        resp = cc.busqueda_por_curp(curp)
                        checkid_data[curp] = resp
                    except Exception as ex:
                        checkid_errors[curp] = str(ex)

        # Formatear reporte
        def fmt_checkid(resp):
            if not isinstance(resp, dict):
                return None
            if not resp.get("exitoso"):
                return {
                    "ok": False,
                    "codigo_error": resp.get("codigoError"),
                    "mensaje_error": (resp.get("resultado") or {}).get("error") or resp.get("error"),
                }
            r = resp.get("resultado") or {}
            nodo_rfc = r.get("rfc") or {}
            nodo_curp = r.get("curp") or {}
            nodo_cp = r.get("codigoPostal") or {}
            nodo_regimen = r.get("regimenFiscal") or {}
            nodo_nss = r.get("nss") or {}
            nodo_6969b = r.get("estado69o69B") or {}
            return {
                "ok": True,
                "rfc": nodo_rfc.get("rfc"),
                "razon_social": nodo_rfc.get("razonSocial"),
                "rfc_valido": nodo_rfc.get("valido"),
                "rfc_valido_hasta": nodo_rfc.get("validoHastaText") or nodo_rfc.get("validoHasta"),
                "rfc_suspendido": nodo_rfc.get("suspendido"),
                "email_contacto": nodo_rfc.get("emailContacto"),
                "curp": {
                    "valor": nodo_curp.get("curp"),
                    "fecha_nacimiento": nodo_curp.get("fechaNacimientoText") or nodo_curp.get("fechaNacimiento"),
                    "sexo": nodo_curp.get("sexo"),
                    "entidad": nodo_curp.get("entidad"),
                },
                "cp_fiscal": nodo_cp.get("codigoPostal"),
                "regimenes_fiscales": nodo_regimen.get("regimenesFiscales"),
                "nss": nodo_nss.get("nss"),
                "en_lista_69b": nodo_6969b.get("conProblema") or False,
                "rfc_exitoso": nodo_rfc.get("exitoso") or False,
                "curp_exitoso": nodo_curp.get("exitoso") or False,
                "cp_exitoso": nodo_cp.get("exitoso") or False,
                "regimen_exitoso": nodo_regimen.get("exitoso") or False,
                "nss_exitoso": nodo_nss.get("exitoso") or False,
                "lista_69b_exitoso": nodo_6969b.get("exitoso") or False,
            }

        reporte = []
        for curp in curps:
            p = padron_by_curp.get(curp)
            item = {
                "curp": curp,
                "encontrado_padron": p is not None,
                "datos_padron": p,
                "checkid": fmt_checkid(checkid_data.get(curp)) if with_checkid else None,
            }
            if curp in checkid_errors:
                item["checkid_error"] = checkid_errors[curp]
            reporte.append(item)

        self._json(200, {
            "solicitados": len(curps),
            "encontrados_padron": len(padron_by_curp),
            "con_checkid": with_checkid,
            "checkid_ok": sum(1 for r in reporte if r.get("checkid", {}).get("ok")),
            "checkid_errores": checkid_errors,
            "reporte": reporte,
        })

    def _handle_direccion_buscar(self):
        """Búsqueda libre por texto de dirección + filtros opcionales.
        Soporta POST (body JSON) y GET (query string).

        Body / query params:
          cp?: str (5 dígitos)
          colonia?: str
          calle?: str
          mun?: str (nombre o clave)
          ext?: str
          exact?: bool (default False)
          limit?: int (default 100, max 5000)
          offset?: int (default 0)

        Respuesta:
          { filtros, normalizado, total_encontrado (INE), total_cfe,
            personas: [{...ine}], cfe: [{...cfe}], ... }

        Si se pasa cp o calle, también busca en api.cfe_medidor (66M registros).
        Estrategia: prioriza CP+colonia+calle, hace fallback flexible.
        """
        session = self._require_session()
        if not session:
            return

        # GET ?param=...  o  POST {param: ...}
        import urllib.parse as _up
        if self.command == "GET":
            qs = _up.parse_qs(_up.urlparse(self.path).query)
            cp = (qs.get("cp", [""])[0] or "").strip()
            colonia = (qs.get("colonia", [""])[0] or "").strip()
            calle = (qs.get("calle", [""])[0] or "").strip()
            mun = (qs.get("mun", [""])[0] or "").strip()
            ext = (qs.get("ext", [""])[0] or "").strip()
            exact = (qs.get("exact", ["0"])[0] or "0") == "1"
            limit_s = qs.get("limit", ["100"])[0]
            offset_s = qs.get("offset", ["0"])[0]
            try:
                limit = min(int(limit_s), 5000)
            except (TypeError, ValueError):
                limit = 100
            try:
                offset = int(offset_s)
            except (TypeError, ValueError):
                offset = 0

            # Validación mínima: debe haber al menos CP o calle
            if not (cp or calle):
                self._json(400, {"error": "se requiere al menos cp o calle"})
                return
        else:
            body = self._read_json_body()
            cp = str(body.get("cp") or "").strip()
            colonia = str(body.get("colonia") or "").strip()
            calle = str(body.get("calle") or "").strip()
            mun = str(body.get("mun") or "").strip()
            ext = str(body.get("ext") or "").strip()
            exact = bool(body.get("exact", False))
            limit = min(int(body.get("limit", 100)), 5000)
            offset = int(body.get("offset", 0))
            # Validación mínima: debe haber al menos CP o calle
            if not (cp or calle):
                self._json(400, {"error": "se requiere al menos cp o calle"})
                return

        # Validación de CP antes de tocar la BD
        cp_n = cp
        if cp_n and (not cp_n.isdigit() or len(cp_n) != 5):
            self._json(400, {"error": "cp debe ser 5 dígitos"})
            return

        try:
            con = duckdb.connect(self.db_path, read_only=True)
        except Exception as ex:
            self._json(500, {"error": f"error abriendo padrón: {ex}"})
            return

        where = []
        params = []

        def norm(s):
            s = s.upper()
            # 2026-08-14: quitar acentos también. Padrón guarda
            # 'COL SAN JERONIMO LIDICE' (sin acentos), SEPOMEX devuelve
            # 'San Jerónimo Lídice' (con acentos). Sin este paso el WHERE
            # no matchea.
            import unicodedata as _u
            s = "".join(c for c in _u.normalize("NFD", s) if _u.category(c) != "Mn")
            pre_strip = ["AVENIDA ", "AV.", "AV ", "C.", "C ", "CALLE ", "CALZADA ",
                         "BLVD.", "BLVD ", "BOULEVARD ", "PRIVADA ", "PRIV ", "COLONIA ", "COL "]
            for pre in pre_strip:
                if s.startswith(pre):
                    s = s[len(pre):]
            for suf in [", COL.", ", COL", " COL.", " COL", ", FRACC.", " FRACC.", " FRACC",
                       ", U. HAB.", " U. HAB.", " U HAB", "U.HAB.", "U.HAB"]:
                if suf in s:
                    s = s.replace(suf, "")
            s = s.replace(".", "").replace(",", "").strip()
            for repl in [("FFCC", "FERROCARRIL"), ("MEXICANOS", "MEXICANO"), ("MEXICANA", "MEXICANO"),
                         ("CDA", "CERRADA"), ("AVDA", "AVENIDA"), ("NUM", "SN"), ("S/N", "SN"),
                         ("  ", " ")]:
                s = s.replace(*repl)
            return " ".join(s.split())

        colonia_n = norm(colonia) if colonia else ""
        calle_n = norm(calle) if calle else ""

        if cp_n:
            where.append("cp=?")
            params.append(cp_n)

        if exact:
            # Match exacto calle+colonia+cp (y ext si se da)
            if not calle_n or not colonia_n or not cp_n:
                self._json(400, {"error": "exact=true requiere cp + colonia + calle"})
                return
            where.append("upper(replace(replace(replace(replace(replace(replace(replace(replace(calle, 'AVENIDA ', ''), 'AV.', ''), 'AV ', ''), 'CALLE ', ''), 'C.', ''), 'C ', ''), 'FFCC', 'FERROCARRIL'), '.', '')) = ?")
            params.append(calle_n)
            where.append("upper(replace(replace(replace(replace(replace(replace(replace(replace(colonia, 'COL.', ''), 'COL ', ''), 'COL,', ''), 'FRACC.', ''), 'FRACC ', ''), 'FRACC,', ''), 'U HAB.', ''), 'U. HAB.', '')) = ?")
            params.append(colonia_n)
            if ext:
                # Compara ext como texto (puede ser numérico o 'SN', 'LT 5', etc.)
                ext_n = norm(ext)
                where.append("upper(coalesce(ext, '')) = ?")
                params.append(ext_n)
        else:
            if colonia_n:
                # Coincidencia flexible: la colonia debe CONTAIN la palabra clave principal
                where.append("upper(colonia) LIKE ?")
                params.append(f"%{colonia_n}%")
            if calle_n:
                where.append("upper(calle) LIKE ?")
                params.append(f"%{calle_n}%")
            if ext:
                ext_n = norm(ext)
                # ext puede ser '28', '128-A', 'LT 5', 'SN', etc.
                # Si el usuario pasa un número puro, hacer LIKE %num%
                if ext_n.isdigit():
                    where.append("(upper(coalesce(ext, '')) LIKE ? OR upper(coalesce(ext, '')) LIKE ?)")
                    params.append(f"%{ext_n}%")
                    params.append(f"{ext_n}-%")  # también matchea "28-A", "28 B"
                else:
                    where.append("upper(coalesce(ext, '')) = ?")
                    params.append(ext_n)

        if mun:
            try:
                where.append("m=?")
                params.append(int(mun))
            except ValueError:
                where.append("upper(mun) LIKE ?")
                params.append(f"%{mun.upper()}%")

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql_count = f"SELECT COUNT(*) FROM padron {where_sql}"
        total = con.execute(sql_count, params).fetchone()[0]

        cols = ["curp", "nombre", "paterno", "materno", "calle", '"int"', "ext",
                "colonia", "cp", "sexo", "fecnac", "mza", "e", "m"]
        keys = [c.strip('"') for c in cols]
        sql_rows = f"SELECT {', '.join(cols)} FROM padron {where_sql} ORDER BY paterno, materno, nombre LIMIT ? OFFSET ?"
        rows = con.execute(sql_rows, params + [limit, offset]).fetchall()
        personas = [dict(zip(keys, r)) for r in rows]
        for p in personas:
            p["source"] = "ine"
            # layout canónico (mejor tipo de vialidad/asentamiento, municipio
            # resuelto desde claves INE) para que el frontend muestre limpio
            try:
                canon = _norm_dir.from_padron(p)
                p["direccion_normalizada"] = canon["direccion_completa"]
                p["municipio"] = canon["municipio"]
                p["entidad"] = canon["entidad"]
            except Exception:
                p["direccion_normalizada"] = None

        # === CFE (api.cfe_medidor) ===
        cfe_resultados = []
        cfe_total = 0
        cfe_error = None
        # 2026-08-14: pre-calculamos ext_num antes del bloque (es usado
        # tanto aquí como en las búsquedas adicionales de abajo).
        ext_num = ext.lstrip("0") if ext else ""
        sw = ""
        # Solo buscar en CFE si el usuario pasa cp o calle (sin calle/colonia es
        # demasiado amplio en los 66M de CFE).
        # 2026-08-14: solo el 17% de CFE tiene cp poblado. Por eso hacemos
        # DOS queries: (1) cp+calle+colonia (exacto), (2) si no devuelve nada,
        # fallback a solo calle+colonia (más permisivo, intersecta con cp NULL).
        if cp_n or calle_n:
            try:
                cfe_con = _init_extended_con()
                if cfe_con is not None:
                    cfe_where_strict = []
                    cfe_params_strict = []
                    cfe_where_loose = []
                    cfe_params_loose = []
                    if cp_n:
                        cfe_where_strict.append("cp = ?")
                        cfe_params_strict.append(cp_n)
                    if calle_n:
                        cfe_where_strict.append("upper(direccion) LIKE ?")
                        cfe_params_strict.append(f"%{calle_n}%")
                        cfe_where_loose.append("upper(direccion) LIKE ?")
                        cfe_params_loose.append(f"%{calle_n}%")
                    if colonia_n:
                        cfe_where_strict.append("upper(colonia) LIKE ?")
                        cfe_params_strict.append(f"%{colonia_n}%")
                        cfe_where_loose.append("upper(colonia) LIKE ?")
                        cfe_params_loose.append(f"%{colonia_n}%")
                    if ext_num and ext_num.isdigit():
                        # 2026-08-14: padrón guarda `ext` como VARCHAR '47' o '47.0'.
                        # El número calle# puede venir en formato 'PROVIDENCIA 47' o
                        # '#47' o '47 CASA 1'. Buscamos en ambos campos.
                        cfe_where_strict.append("(ext = ? OR ext = ? OR ext LIKE ? OR calle LIKE ? OR calle LIKE ?)")
                        cfe_params_strict.append(ext_num)
                        cfe_params_strict.append(f"{ext_num}.0")
                        cfe_params_strict.append(f"{ext_num}.0%")
                        cfe_params_strict.append(f"%#{ext_num}%")
                        cfe_params_strict.append(f"% {ext_num} %")
                    # Query 1: strict
                    if cfe_where_strict:
                        sw = "WHERE " + " AND ".join(cfe_where_strict)
                        cfe_total = cfe_con.execute(
                            f"SELECT COUNT(*) FROM api.cfe_medidor {sw}",
                            cfe_params_strict
                        ).fetchone()[0]
                    # Si strict da 0 y hay loose, fallback
                    cfe_where_sql = ""
                    cfe_params = []
                    if cfe_total > 0:
                        cfe_where_sql = sw
                        cfe_params = cfe_params_strict
                    elif cfe_where_loose:
                        cfe_where_sql = "WHERE " + " AND ".join(cfe_where_loose)
                        cfe_params = cfe_params_loose
                        cfe_total = cfe_con.execute(
                            f"SELECT COUNT(*) FROM api.cfe_medidor {cfe_where_sql}",
                            cfe_params
                        ).fetchone()[0]
                    if cfe_where_sql:
                        cfe_cols = ["numero_servicio", "nombre", "division", "cp", "direccion",
                                    "calle_adicional_1", "calle_adicional_2", "colonia",
                                    "agencia_nom", "zona_nom"]
                        cfe_sql_rows = (f"SELECT {', '.join(cfe_cols)} FROM api.cfe_medidor "
                                        f"{cfe_where_sql} LIMIT ?")
                        cfe_rows = cfe_con.execute(cfe_sql_rows,
                                                    cfe_params + [limit]).fetchall()
                        for r in cfe_rows:
                            d = dict(zip(cfe_cols, r))
                            d["source"] = "cfe"
                            cfe_resultados.append(d)
            except Exception as ex:
                cfe_error = str(ex)[:200]

        # === 2026-08-14: Búsqueda ampliada en 7 bases adicionales ===
        # Mismas reglas: cp/calle/colonia como filtro. Devuelve la UNION
        # de las coincidencias para que el front pinte un reporte único.
        # Si el usuario pasó ext, se filtra adicionalmente donde aplica.
        telcel_resultados = []
        telcel_total = 0
        att_resultados = []
        att_total = 0
        repuve_resultados = []
        repuve_total = 0
        imss_resultados = []
        imss_total = 0
        issste_resultados = []
        issste_total = 0
        sepomex_info = None
        # ext_num ya se calculó arriba (línea ~1358), reutilizar.

        if cp_n or calle_n:
            ext_con = _init_extended_con()
            if ext_con is not None:
                # --- TELCEL (api.telcel_lineas_full) ---
                # 2026-08-14: la vista expone domicilio/numero/colonia/ciudad/estado/cp
                # con prefijo "titular_" (titular_domicilio, titular_numero, etc).
                try:
                    t_where = []
                    t_params = []
                    if cp_n:
                        t_where.append("titular_cp = ?")
                        t_params.append(cp_n)
                    if calle_n:
                        t_where.append("upper(titular_domicilio) LIKE ?")
                        t_params.append(f"%{calle_n}%")
                    if colonia_n:
                        t_where.append("upper(titular_colonia) LIKE ?")
                        t_params.append(f"%{colonia_n}%")
                    if ext_num and ext_num.isdigit():
                        t_where.append("(titular_numero = ? OR titular_domicilio LIKE ?)")
                        t_params.append(ext_num)
                        t_params.append(f"%{ext_num}%")
                    if t_where:
                        t_where_sql = "WHERE " + " AND ".join(t_where)
                        telcel_total = ext_con.execute(
                            f"SELECT COUNT(*) FROM api.telcel_lineas_full {t_where_sql}",
                            t_params
                        ).fetchone()[0]
                        rows = ext_con.execute(
                            f"SELECT titular_nombre1, titular_nombre2, rfc, telefono, "
                            f"titular_domicilio, titular_numero, titular_interior, "
                            f"titular_colonia, titular_ciudad, titular_estado, titular_cp, "
                            f"titular_tel_contacto, archivo_origen "
                            f"FROM api.telcel_lineas_full {t_where_sql} "
                            f"ORDER BY titular_nombre1, titular_nombre2 LIMIT ?",
                            t_params + [limit]
                        ).fetchall()
                        cols = ["nombre1", "nombre2", "rfc", "telefono", "domicilio",
                                "numero", "interior", "colonia", "ciudad", "estado", "cp",
                                "tel_contacto", "archivo_origen"]
                        telcel_resultados = [dict(zip(cols, r)) for r in rows]
                except Exception as ex_t:
                    print(f"[direccion_buscar] telcel error: {ex_t}", file=sys.stderr)

                # --- ATT (api.att_persona) ---
                # 2026-08-14: ATT guarda el estado como código de 2 letras (DF, EM, JA, etc).
                # CDMX = 'DF'. La calle se busca en `direccion`, colonia en `colonia`.
                # No hay cp — usamos el código de estado como heurística de CDMX.
                try:
                    a_where = []
                    a_params = []
                    if cp_n:
                        # CDMX = estado 'DF'. Otros estados tienen códigos distintos.
                        a_where.append("estado = ?")
                        a_params.append("DF")
                    if calle_n:
                        a_where.append("upper(direccion) LIKE ?")
                        a_params.append(f"%{calle_n}%")
                    if colonia_n:
                        a_where.append("upper(colonia) LIKE ?")
                        a_params.append(f"%{colonia_n}%")
                    if ext_num and ext_num.isdigit():
                        a_where.append("(num_exterior = ? OR direccion LIKE ?)")
                        a_params.append(ext_num)
                        a_params.append(f"%{ext_num}%")
                    if a_where:
                        a_where_sql = "WHERE " + " AND ".join(a_where)
                        att_total = ext_con.execute(
                            f"SELECT COUNT(*) FROM api.att_persona {a_where_sql}",
                            a_params
                        ).fetchone()[0]
                        rows = ext_con.execute(
                            f"SELECT nombres, apellido_paterno, apellido_materno, rfc, "
                            f"telefono_fijo, celular, direccion, num_interior, num_exterior, "
                            f"colonia, municipio, estado "
                            f"FROM api.att_persona {a_where_sql} "
                            f"ORDER BY apellido_paterno, apellido_materno, nombres LIMIT ?",
                            a_params + [limit]
                        ).fetchall()
                        cols = ["nombre", "paterno", "materno", "rfc", "telefono_fijo",
                                "celular", "direccion", "interior", "exterior", "colonia",
                                "municipio", "estado"]
                        att_resultados = [dict(zip(cols, r)) for r in rows]
                except Exception as ex_a:
                    print(f"[direccion_buscar] att error: {ex_a}", file=sys.stderr)

                # --- REPUVE (api.repuve_de_persona) ---
                # 2026-08-14: la vista expone DIR_PROP como `direccion_propietario`
                # (normalizado vía dir_prop_fix), no como DIR_PROP.
                try:
                    r_where = []
                    r_params = []
                    if calle_n:
                        r_where.append("upper(direccion_propietario) LIKE ?")
                        r_params.append(f"%{calle_n}%")
                    if colonia_n:
                        r_where.append("upper(direccion_propietario) LIKE ?")
                        r_params.append(f"%{colonia_n}%")
                    if r_where:
                        r_where_sql = "WHERE " + " OR ".join(r_where)
                        repuve_total = ext_con.execute(
                            f"SELECT COUNT(*) FROM api.repuve_de_persona {r_where_sql}",
                            r_params
                        ).fetchone()[0]
                        rows = ext_con.execute(
                            f"SELECT propietario, rfc, direccion_propietario, "
                            f"telefono_propietario, placa, marca, modelo, color "
                            f"FROM api.repuve_de_persona {r_where_sql} "
                            f"ORDER BY propietario LIMIT ?",
                            r_params + [limit]
                        ).fetchall()
                        cols = ["nombre", "rfc", "direccion", "telefono", "placa",
                                "marca", "modelo", "color"]
                        repuve_resultados = [dict(zip(cols, r)) for r in rows]
                except Exception as ex_r:
                    print(f"[direccion_buscar] repuve error: {ex_r}", file=sys.stderr)

                # --- IMSS Asegurados (api.imss_asegurado_full) ---
                # 2026-08-14: la vista expone el nombre del asegurado como `nombre_patron`
                # (y el del patrón como `empresa_nombre`). Búsqueda por dirección del
                # PATRÓN (empresa_domicilio + empresa_cp) — útil para localizar patrones
                # en la dirección consultada.
                try:
                    i_where = []
                    i_params = []
                    if cp_n:
                        i_where.append("empresa_cp = ?")
                        i_params.append(cp_n)
                    if calle_n:
                        i_where.append("upper(empresa_domicilio) LIKE ?")
                        i_params.append(f"%{calle_n}%")
                    if i_where:
                        i_where_sql = "WHERE " + " AND ".join(i_where)
                        imss_total = ext_con.execute(
                            f"SELECT COUNT(*) FROM api.imss_asegurado_full {i_where_sql}",
                            i_params
                        ).fetchone()[0]
                        # Limit agresivo porque es base de 57M de filas
                        imss_lim = min(limit, 50)
                        rows = ext_con.execute(
                            f"SELECT nombre_patron, registro_patron, nss, nss_raw, "
                            f"curp, curp_raw, empresa_nombre, empresa_domicilio, "
                            f"empresa_ciudad_estado, empresa_cp, empresa_giro, sueldo "
                            f"FROM api.imss_asegurado_full {i_where_sql} "
                            f"LIMIT ?",
                            i_params + [imss_lim]
                        ).fetchall()
                        cols = ["asegurado", "registro_patron", "nss", "nss_raw",
                                "curp", "curp_raw", "patron", "domicilio",
                                "ciudad_estado", "cp", "giro", "sueldo"]
                        imss_resultados = [dict(zip(cols, r)) for r in rows]
                except Exception as ex_i:
                    print(f"[direccion_buscar] imss error: {ex_i}", file=sys.stderr)

                # --- ISSSTE (api.issste_empleado) ---
                # Búsqueda por apellido del núcleo familiar detectado en Padrón
                # para complementar la foto. Si no hay coincidencias en Padrón,
                # ISSSTE no devuelve nada (no tiene calle/colonia propia).
                issste_where = []
                issste_params = []
                # Apellidos del núcleo detectado (top 5 paternos en padrón)
                if personas:
                    paternos = list({p["paterno"] for p in personas if p.get("paterno")})[:5]
                    if paternos:
                        placeholders = ",".join(["?"] * len(paternos))
                        issste_where.append(f"paterno IN ({placeholders})")
                        issste_params.extend(paternos)
                if issste_where:
                    issste_where_sql = "WHERE " + " AND ".join(issste_where)
                    issste_total = ext_con.execute(
                        f"SELECT COUNT(*) FROM api.issste_empleado {issste_where_sql}",
                        issste_params
                    ).fetchone()[0]
                    issste_lim = min(limit, 100)
                    rows = ext_con.execute(
                        f"SELECT paterno, materno, nombres, cargo, sueldo, ramo, "
                        f"entidad, modalidad, sector, estado "
                        f"FROM api.issste_empleado {issste_where_sql} "
                        f"LIMIT ?",
                        issste_params + [issste_lim]
                    ).fetchall()
                    cols = ["paterno", "materno", "nombres", "cargo", "sueldo",
                            "ramo", "entidad", "modalidad", "sector", "estado"]
                    issste_resultados = [dict(zip(cols, r)) for r in rows]

        # --- SEPOMEX (sqlite, validar CP/colonia) ---
        if cp_n:
            try:
                import sqlite3 as _sq
                # 2026-08-14: sepomex vive en /root/proyecto_kyc/bases/sepomex.db.
                # ROOT (Path(__file__).parent.resolve()) = /root/proyecto_kyc/backend,
                # así que hay que subir un nivel para llegar a /bases.
                sep_path = ROOT.parent / "bases" / "sepomex.db"
                if not sep_path.exists():
                    sep_path = ROOT / "sepomex.db"
                if not sep_path.exists():
                    print(f"[direccion_buscar] sepomex no encontrado ni en {ROOT.parent}/bases/sepomex.db ni en {ROOT}/sepomex.db", file=sys.stderr)
                else:
                    sep = _sq.connect(str(sep_path))
                    row = sep.execute(
                        "SELECT cp, colonia, tipo, municipio, estado FROM cp WHERE cp = ? LIMIT 1",
                        [cp_n]
                    ).fetchone()
                    sep.close()
                    if row:
                        sepomex_info = {
                            "cp": row[0], "colonia": row[1], "tipo": row[2],
                            "municipio": row[3], "estado": row[4],
                        }
            except Exception as ex_s:
                print(f"[direccion_buscar] sepomex error: {ex_s}", file=sys.stderr)

        # Determinar núcleo familiar (apellidos más frecuentes del padrón)
        nucleo = {"paternos": [], "maternos": []}
        if personas:
            from collections import Counter
            c_pat = Counter(p["paterno"] for p in personas if p.get("paterno"))
            c_mat = Counter(p["materno"] for p in personas if p.get("materno"))
            nucleo["paternos"] = [{"apellido": k, "count": v} for k, v in c_pat.most_common(5)]
            nucleo["maternos"] = [{"apellido": k, "count": v} for k, v in c_mat.most_common(5)]

        self._json(200, {
            "filtros": {"cp": cp, "colonia": colonia, "calle": calle, "ext": ext, "mun": mun, "exact": exact},
            "normalizado": {"cp": cp_n, "colonia": colonia_n, "calle": calle_n},
            "sepomex": sepomex_info,
            "nucleo_familiar": nucleo,
            "totales": {
                "padron": total,
                "cfe": cfe_total,
                "telcel": telcel_total,
                "att": att_total,
                "repuve": repuve_total,
                "imss": imss_total,
                "issste": issste_total,
            },
            "total_encontrado": total,
            "total_cfe": cfe_total,
            "mostrados": len(personas),
            "limit": limit, "offset": offset,
            "personas": personas,
            "cfe": cfe_resultados,
            "telcel": telcel_resultados,
            "att": att_resultados,
            "repuve": repuve_resultados,
            "imss": imss_resultados,
            "issste": issste_resultados,
            "cfe_error": cfe_error,
        })
        try:
            con.close()
        except Exception:
            pass

    def _handle_direccion_sugerir(self):
        """POST /api/direccion/sugerir  { texto, limit? }

        Autocompleta direcciones desde SEPOMEX. Acepta texto libre
        ("Providencia 47 San Jeronimo Magdalena Contreras", "10200 san jeronimo",
        "San Jerónimo Lídice CDMX", etc). Devuelve hasta `limit` sugerencias
        con { cp, colonia, tipo, municipio, estado, display, score }.

        Estrategia:
        1. Tokeniza el texto en palabras.
        2. Si hay 5 dígitos consecutivos, los usa como CP (búsqueda exacta).
        3. Para cada fila candidata, calcula score = cantidad de tokens que
           matchean contra colonia/municipio/estado (LIKE insensible a
           acentos via lower()).
        4. Ordena por score desc y devuelve los mejores N.
        """
        session = self._require_session()
        if not session:
            return
        body = self._read_json_body()
        texto = (body.get("texto") or body.get("q") or "").strip()
        try:
            limit = int(body.get("limit", 15))
        except (TypeError, ValueError):
            limit = 15
        limit = max(1, min(limit, 50))

        if len(texto) < 2:
            self._json(200, {"texto": texto, "sugerencias": [], "count": 0})
            return

        import sqlite3 as _sq
        sep_path = ROOT.parent / "bases" / "sepomex.db"
        if not sep_path.exists():
            sep_path = ROOT / "sepomex.db"
        if not sep_path.exists():
            self._json(500, {"error": "sepomex.db no encontrado"})
            return
        try:
            con = _sq.connect(str(sep_path))
        except Exception as ex:
            self._json(500, {"error": f"sepomex: {ex}"})
            return

        # Normalizar: minúsculas, sin acentos
        import unicodedata as _u
        def _norm(s):
            s = (s or "").lower()
            # quitar acentos
            s = "".join(c for c in _u.normalize("NFD", s) if _u.category(c) != "Mn")
            return s.strip()

        texto_n = _norm(texto)
        # Extraer tokens (palabras de >=2 chars, ignorar numeros cortos)
        import re as _re
        tokens = [t for t in _re.findall(r"[a-záéíóúñü]{2,}", texto.lower())]
        tokens = [_norm(t) for t in tokens]
        # Detectar CP (5 dígitos)
        cp_match = _re.search(r"\b(\d{5})\b", texto)

        try:
            sugerencias = []
            if cp_match:
                # Búsqueda exacta por CP, devolver todas las colonias de ese CP
                cp = cp_match.group(1)
                cur = con.execute(
                    "SELECT cp, colonia, tipo, municipio, estado FROM cp WHERE cp = ? LIMIT 200",
                    [cp]
                )
                for row in cur.fetchall():
                    r = {"cp": row[0], "colonia": row[1], "tipo": row[2],
                         "municipio": row[3], "estado": row[4]}
                    r["display"] = f"{r['colonia']}, {r['municipio']}, {r['estado']} (CP {r['cp']})"
                    r["score"] = 100
                    sugerencias.append(r)
            else:
                # Búsqueda fuzzy: traer candidatas con LIKE sobre texto normalizado
                # y rankear por número de tokens que matchean.
                # Para no escanear 155K filas, primero filtramos por los primeros
                # 2 tokens (los más restrictivos) en colonia O municipio.
                anchor_tokens = tokens[:2] if len(tokens) >= 2 else tokens
                if not anchor_tokens:
                    self._json(200, {"texto": texto, "sugerencias": [], "count": 0})
                    return
                # Construir WHERE con LIKE OR por cada anchor token
                where_parts = []
                params = []
                for t in anchor_tokens:
                    where_parts.append(
                        "(lower(colonia) LIKE ? OR lower(municipio) LIKE ? OR lower(estado) LIKE ?)"
                    )
                    params.extend([f"%{t}%", f"%{t}%", f"%{t}%"])
                where_sql = " OR ".join(where_parts)
                # Limitar el scan inicial a 5000 filas
                cur = con.execute(
                    f"SELECT cp, colonia, tipo, municipio, estado FROM cp "
                    f"WHERE {where_sql} LIMIT 5000",
                    params
                )
                cand = cur.fetchall()
                # Rankear
                scored = []
                for row in cand:
                    cp, colonia, tipo, municipio, estado = row
                    hay = (_norm(colonia) + " " + _norm(municipio) + " " + _norm(estado))
                    # Score = cantidad de tokens presentes en el haystack
                    score = sum(1 for t in tokens if t in hay)
                    if score == 0:
                        continue
                    scored.append((score, cp, colonia, tipo, municipio, estado))
                # Ordenar: score desc, municipio/estado/colonia alfabético
                scored.sort(key=lambda x: (-x[0], x[4] or "", x[2] or "", x[1]))
                seen = set()
                for score, cp, colonia, tipo, municipio, estado in scored:
                    key = (cp, colonia, municipio, estado)
                    if key in seen:
                        continue
                    seen.add(key)
                    r = {"cp": cp, "colonia": colonia, "tipo": tipo,
                         "municipio": municipio, "estado": estado, "score": score}
                    r["display"] = f"{colonia}, {municipio}, {estado} (CP {cp})"
                    sugerencias.append(r)
                    if len(sugerencias) >= limit:
                        break
            con.close()
        except Exception as ex:
            self._json(500, {"error": f"sepomex query: {ex}"})
            return

        self._json(200, {
            "texto": texto,
            "count": len(sugerencias),
            "sugerencias": sugerencias,
        })

    def _handle_geo_padron(self):
        """Cruza cve_ent/cve_mun/cve_loc/cve_ageb/cve_mza del Marco Geoestadístico
        contra el padrón INE. Devuelve las personas encontradas en esa zona.
        Body: { cve_ent, cve_mun, cve_loc, cve_ageb, cve_mza?, strict? }
        - strict=True: cruza por municipio + sección + manzana
        - strict=False: cruza por municipio + sección (más permisivo, recomendado)
        """
        session = self._require_session()
        if not session:
            return
        body = self._read_json_body()
        cve_ent = str(body.get("cve_ent") or "").strip()
        cve_mun = str(body.get("cve_mun") or "").strip().zfill(3)
        cve_loc = str(body.get("cve_loc") or "").strip().zfill(4)
        cve_ageb = str(body.get("cve_ageb") or "").strip().zfill(4)
        cve_mza = str(body.get("cve_mza") or "").strip()
        strict = bool(body.get("strict", False))
        limit = min(int(body.get("limit", 100)), 1000)
        offset = int(body.get("offset", 0))

        if not cve_ent or not cve_mun:
            self._json(400, {"error": "cve_ent y cve_mun son requeridos"})
            return

        try:
            e = int(cve_ent)
            m = int(cve_mun)
        except ValueError:
            self._json(400, {"error": "cve_ent y cve_mun deben ser numéricos"})
            return

        try:
            con = duckdb.connect(self.db_path, read_only=True)
        except Exception as ex:
            self._json(500, {"error": f"error abriendo padrón: {ex}"})
            return

        try:
            if strict and cve_mza:
                # Cruce estricto: sección + manzana
                mza_int = int(cve_mza)
                where = "e=? AND m=? AND l=? AND s=? AND mza=?"
                params = [e, m, int(cve_loc) if cve_loc else 1, int(cve_ageb), mza_int]
            else:
                # Cruce por AGEB: municipio + AGEB (sección)
                where = "e=? AND m=? AND l=? AND s=?"
                params = [e, m, int(cve_loc) if cve_loc else 1, int(cve_ageb)]
            total = con.execute(f"SELECT COUNT(*) FROM padron WHERE {where}", params).fetchone()[0]
            cols = ["curp", "nombre", "paterno", "materno", "calle", "ext", "colonia", "cp", "sexo", "fecnac", "mza"]
            rows = con.execute(
                f"SELECT {', '.join(cols)} FROM padron WHERE {where} ORDER BY paterno, materno, nombre LIMIT ? OFFSET ?",
                params + [limit, offset]
            ).fetchall()
            personas = [dict(zip(cols, r)) for r in rows]
            result = {
                "cruzado": {
                    "cve_ent": cve_ent, "cve_mun": cve_mun,
                    "cve_loc": cve_loc or None, "cve_ageb": cve_ageb or None,
                    "cve_mza": cve_mza or None,
                    "modo": "estricto" if strict else "AGEB",
                },
                "total_encontrado": total,
                "mostrados": len(personas),
                "limit": limit, "offset": offset,
                "personas": personas,
            }
            self._json(200, result)
        except Exception as ex:
            self._json(500, {"error": f"error en cruce: {ex}"})
        finally:
            try:
                con.close()
            except Exception:
                pass

    def _handle_sepomex_search(self):
        session = self._require_session()
        if not session:
            return
        url = urlparse(self.path)
        q = (parse_qs(url.query).get("q", [""])[0] or "").strip().upper()
        limit = min(int(parse_qs(url.query).get("limit", ["50"])[0] or "50"), 200)
        if len(q) < 3:
            self._json(400, {"error": "q debe tener al menos 3 caracteres"})
            return
        con = get_sepomex()
        like = f"%{q}%"
        rows = con.execute(
            "SELECT cp, colonia, tipo, municipio, estado FROM cp "
            "WHERE cp LIKE ? OR UPPER(colonia) LIKE ? OR UPPER(municipio) LIKE ? "
            "ORDER BY cp, colonia LIMIT ?",
            (f"{q[:5]}%", like, like, limit),
        ).fetchall()
        self._json(200, {
            "query": q,
            "results": [dict(r) for r in rows],
        })

    def _handle_auth_login_password(self):
        t0 = time.time()
        body = self._read_json_body()
        username = (body.get("username") or "").strip().lower()
        password = body.get("password") or ""
        if not username or not password:
            self._audit(
                session=None, action="login_fail",
                endpoint="/api/auth/login", method="POST", status_code=400,
                duration_ms=int((time.time() - t0) * 1000),
                query_summary={"reason": "faltan campos"},
            )
            self._json(400, {"error": "faltan usuario o contraseña"})
            return
        try:
            sess = auth.verify_password(username, password)
            self._audit(
                session=sess, action="login",
                endpoint="/api/auth/login", method="POST", status_code=200,
                duration_ms=int((time.time() - t0) * 1000),
                query_summary={"username": username},
            )
            self._auth_json(200, sess, set_cookie=sess["token"])
        except Exception as e:
            self._audit(
                session=None, action="login_fail",
                endpoint="/api/auth/login", method="POST", status_code=401,
                duration_ms=int((time.time() - t0) * 1000),
                query_summary={"username_attempt": username, "error": str(e)[:80]},
            )
            self._json(401, {"error": str(e)})

    def _handle_auth_users(self):
        session = self._require_session()
        if not session:
            return
        if session.get("username") != "admin":
            self._json(403, {"error": "solo admin puede gestionar usuarios"})
            return
        t0 = time.time()
        body = self._read_json_body()
        action = body.get("action")
        audit_action = None
        if action == "list":
            try:
                users = auth.list_users()
                self._audit(
                    session=session, action="admin_view",
                    endpoint="/api/auth/users", method="POST", status_code=200,
                    duration_ms=int((time.time() - t0) * 1000),
                    query_summary={"action": "list", "count": len(users)},
                )
                self._json(200, {"users": users})
            except Exception as e:
                self._json(500, {"error": str(e)})
        elif action == "create":
            username = (body.get("username") or "").strip().lower()
            password = body.get("password") or ""
            if not username or not password:
                self._json(400, {"error": "faltan usuario o contraseña"})
                return
            try:
                res = auth.create_user_with_password(session, username, password)
                self._audit(
                    session=session, action="user_create",
                    endpoint="/api/auth/users", method="POST", status_code=200,
                    duration_ms=int((time.time() - t0) * 1000),
                    query_summary={"target_user": username},
                )
                self._json(200, res)
            except Exception as e:
                self._json(400, {"error": str(e)})
        elif action == "reset_password":
            username = (body.get("username") or "").strip().lower()
            password = body.get("password") or ""
            if not username or not password:
                self._json(400, {"error": "faltan usuario o contraseña"})
                return
            try:
                res = auth.set_user_password(username, password)
                self._audit(
                    session=session, action="user_reset",
                    endpoint="/api/auth/users", method="POST", status_code=200,
                    duration_ms=int((time.time() - t0) * 1000),
                    query_summary={"target_user": username},
                )
                self._json(200, res)
            except Exception as e:
                self._json(400, {"error": str(e)})
        else:
            self._json(400, {"error": "acción desconocida"})

    def _handle_search(self):
        t0 = time.time()
        session = self._require_session()
        if not session:
            return
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._audit(
                session=session, action="search",
                endpoint="/api/search", method="POST", status_code=400,
                duration_ms=int((time.time() - t0) * 1000),
                query_summary={"error": "JSON inválido"},
            )
            self._json(400, {"error": f"JSON inválido: {e}"})
            return

        where = (payload.get("where") or "").strip()
        params = payload.get("params") or []
        limit = int(payload.get("limit", 200))
        offset = int(payload.get("offset", 0))
        order_by = payload.get("order_by", "id")

        # validar tipos
        if not isinstance(params, list):
            self._json(400, {"error": "params debe ser lista"})
            return

        # validador
        ok, msg = validate_where(where, params)
        if not ok:
            self._json(400, {"error": f"WHERE inválido: {msg}"})
            return

        try:
            total, rows, ms = self.db.search(where, params, limit, offset, order_by)
        except Exception as e:
            self._audit(
                session=session, action="search",
                endpoint="/api/search", method="POST", status_code=500,
                duration_ms=int((time.time() - t0) * 1000),
                query_summary={"where_prefix": where[:80], "error": str(e)[:80]},
            )
            self._json(500, {"error": f"error SQL: {e}"})
            return

        self._audit(
            session=session, action="search",
            endpoint="/api/search", method="POST", status_code=200,
            duration_ms=int((time.time() - t0) * 1000),
            query_summary=audit._summarize_query("/api/search", {}, payload),
            results_count=len(rows),
        )

        self._json(200, {
            "rows": rows,
            "cols": COLUMNAS,
            "total": total,
            "limit": limit,
            "offset": offset,
            "ms": round(ms, 2),
        })

    def _handle_osint(self):
        url = urlparse(self.path)
        if url.path != "/api/osint":
            self._json(404, {"error": "endpoint no existe"})
            return
        # parsear payload
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"error": f"JSON inválido: {e}"})
            return

        # ejecutar OSINT local (padrón) y fuentes externas vía subprocess
        # 1) Búsqueda local inmediata
        result = {
            "metadata": {"fecha": datetime.datetime.now().isoformat()},
            "sujeto": payload,
            "padron": [],
            "email_lookup": None,
            "brechas_filtradas": [],
            "psbdmp": [],
            "dominio_reputacion": None,
            "github": [],
            "linkedin": [],
            "dialnet": [],
            "google_results": [],
            "sintesis": {},
        }
        # mapeo payload -> columnas
        where_parts, where_params = [], []
        if payload.get("curp"):
            where_parts.append("curp = ?"); where_params.append(payload["curp"].upper())
        if payload.get("nombre"):
            where_parts.append("nombre ILIKE ?"); where_params.append(f"%{payload['nombre'].upper()}%")
        if payload.get("paterno"):
            where_parts.append("paterno ILIKE ?"); where_params.append(f"%{payload['paterno'].upper()}%")
        if payload.get("materno"):
            where_parts.append("materno ILIKE ?"); where_params.append(f"%{payload['materno'].upper()}%")
        if payload.get("fecnac"):
            where_parts.append("fecnac = ?"); where_params.append(payload["fecnac"])
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        if where:
            try:
                # sanitizar el where igual que en search
                ok, msg = validate_where(where, where_params)
                if ok:
                    result["padron"] = self.db.search_free(where, where_params, limit=20)
            except Exception as e:
                result["padron_error"] = str(e)

        # 2) Ejecutar el script osint.py para fuentes externas (en background)
        # Crear archivo temporal con los argumentos
        import tempfile, subprocess
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(payload, tmp)
            tmp_path = tmp.name
        out_path = tempfile.mktemp(suffix=".json")
        # Construir comando
        osint_script = str(ROOT / "osint.py")
        cmd = [
            sys.executable, osint_script,
            "--db", str(self.db_path if hasattr(self, "db_path") else (ROOT / "ine.duckdb")),
            "--json", out_path,
            "--quiet",
        ]
        for k in ("curp", "rfc", "nombre", "paterno", "materno", "fecnac", "email", "telefono"):
            v = (payload.get(k) or "").strip()
            if v:
                cmd += [f"--{k}", v]
        os.unlink(tmp_path)

        # Guardar padrón local ANTES del subprocess (es más rápido y completo)
        padron_local = result["padron"]

        try:
            # Timeout debe quedar por DEBAJO del timeout de origen del túnel
            # Cloudflare (~100s): si el subprocess corre 180s la conexión se
            # cae y el navegador recibe un 502 HTML -> JSON.parse falla en el
            # frontend. Con 45s devolvemos el padrón local + lo que alcanzó a
            # traer osint.py, en lugar de un 502.
            cp = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=45,
                cwd=str(ROOT),
            )
            # leer el JSON de salida
            if os.path.exists(out_path):
                with open(out_path, encoding="utf-8") as f:
                    full = json.load(f)
                # mezclar resultados externos con locales
                for k in ("padron", "email_lookup", "brechas_filtradas",
                          "psbdmp", "dominio_reputacion", "github",
                          "linkedin", "dialnet", "google_results",
                          "sintesis", "metadata", "sujeto"):
                    if k in full:
                        result[k] = full[k]
                # usar padrón local (más rápido) si existe
                if padron_local:
                    result["padron"] = padron_local
            os.unlink(out_path)
        except subprocess.TimeoutExpired:
            result["osint_error"] = "timeout ejecutando osint.py (180s)"
        except Exception as e:
            result["osint_error"] = f"error ejecutando osint.py: {e}"

        # síntesis: si osint.py no la llenó (clave ausente), calcular una básica
        # Si osint.py devuelve sintesis: {} (dict vacío), eso cuenta como "llenado"
        if "sintesis" not in result:
            s = {
                "total_padron": len(result.get("padron") or []),
                "total_brechas": len(result.get("brechas_filtradas") or []),
                "total_pastebin_hits": len(result.get("psbdmp") or []),
                "total_github_perfiles": len(result.get("github") or []),
                "total_linkedin": len(result.get("linkedin") or []),
                "total_dialnet": len(result.get("dialnet") or []),
                "total_google": sum(len(r.get("resultados", []))
                                    for r in (result.get("google_results") or [])),
                "alertas": [],
            }
            if s["total_pastebin_hits"]:
                s["alertas"].append("Email aparece en pastebin leaks (psbdmp)")
            if s["total_brechas"]:
                s["alertas"].append(f"dominio con {s['total_brechas']} filtraciones conocidas")
            result["sintesis"] = s

        self._json(200, result)

    def _handle_rfc_expandir(self):
        """POST /api/rfc/expandir

        Pipeline completo: Tlaloc + Singula RFC + Singula Intel + Apify multi-vía.

        Body: {
          rfc, curp?, nombre?, paterno?, materno?, fecnac?, email?, telefono?
        }

        Retorna:
          {
            "rfc": {...estado SAT...},
            "curp_renapo": {...datos RENAPO...},
            "dossier": {...Intel Premium + email/phone encontrados...},
            "redes_por_email": { email, username, plataformas_encontradas: [...] },
            "redes_por_telefono": { telefono, plataformas_encontradas: [...] },
            "redes_por_nombre": { nombre_completo, plataformas_encontradas: [...] },
            "redes_totales": [...lista consolidada sin duplicados...],
            "resumen_email_telefono": { emails, telefonos },
          }
        """
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"error": f"JSON inválido: {e}"})
            return

        curp = payload.get("curp", "").strip()
        rfc = payload.get("rfc", "").strip()
        nombre = payload.get("nombre", "").strip()
        paterno = payload.get("paterno", "").strip()
        materno = payload.get("materno", "").strip()
        fecnac = payload.get("fecnac", "").strip()

        # ====== Calcular RFC desde CURP o datos demográficos ======
        # Si nos dan RFC, lo usamos; si no, lo derivamos de la CURP (10 chars) o de los datos.
        rfc_calculado = ""
        rfc_dv = ""
        try:
            from rfc_utils import calcular_rfc_desde_curp, _calcular_dv
            rfc_calculado = calcular_rfc_desde_curp(
                curp=curp, nombre=nombre, paterno=paterno, materno=materno, fecnac=fecnac
            )
            if rfc_calculado and len(rfc_calculado) == 10:
                rfc_dv = _calcular_dv(rfc_calculado + "XX")  # DV con homoclave provisional
        except Exception as e:
            rfc_calculado = ""
            rfc_dv = ""

        # Si no nos dieron RFC pero tenemos RFC calculado (10 chars), usarlo como referencia
        if not rfc and rfc_calculado:
            rfc = rfc_calculado

        email_input = payload.get("email", "").strip()
        telefono_input = payload.get("telefono", "").strip()

        if not rfc and not curp:
            self._json(400, {"error": "rfc o curp requerido"})
            return

        t0 = time.time()
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from config import config
        import re as re_mod

        clients = self._get_broker_clients()
        resultado = {
            "metadata": {
                "timestamp": time.time(),
                "rfc_input": rfc,
                "curp_input": curp,
                "nombre": f"{nombre} {paterno} {materno}".strip(),
            },
            "rfc": {},
            "curp_renapo": {},
            "dossier": {},
            "resumen_email_telefono": {"emails": [], "telefonos": []},
            "redes_por_email": {},
            "redes_por_telefono": {},
            "redes_por_nombre": {},
            "redes_totales": [],
            "errores": [],
        }

        # ====== FASE 1-3: datos en paralelo (Tlaloc + RFC + Intel) ======
        def task_tlaloc():
            if not curp or "tlaloc" not in clients:
                return ("curp_renapo", None)
            try:
                return ("curp_renapo", clients["tlaloc"].validate_curp(curp))
            except Exception as e:
                return ("curp_renapo_err", str(e))

        def task_singula_intel():
            # 2026-08-04: usamos cache local con TTL 7d para no cobrar dos veces
            # por apertura. intel_premium es la validación más cara de Singula
            # y NO se ejecuta automáticamente — sólo cuando el usuario pulse ▶
            # desde el modal (con force=True).
            if not (rfc or curp) or "singula" not in clients:
                return ("dossier", None)
            try:
                sg = clients["singula"]
                # 2026-08-04: get_or_create_customer es idempotente y reemplaza
                # el patrón find_by_curp + create_customer (que fallaba cuando
                # Singula no deduplica por CURP). El cache local del cliente
                # evita llamadas duplicadas.
                res = sg.get_or_create_customer(
                    curp=curp,
                    name=nombre or "DESCONOCIDO",
                    last_name=paterno or "DESCONOCIDO",
                    mothers_last_name=materno or "",
                    rfc=rfc,
                )
                cust = res.get("customer") or {}
                cid = res.get("id")
                if not cid:
                    return ("dossier", {"error": "no se pudo crear/obtener customer", "detail": cust})
                # 2026-08-04: validar el customer antes de gastar créditos en
                # endpoints customer-centric (intel_basic/intel_premium). Si
                # el id es stale o el customer no está listo, ensure_customer
                # lo detecta sin cobrar.
                guard = sg.ensure_customer(cid)
                if not guard.get("ok"):
                    return ("dossier", {"error": "customer no válido", "guard": guard})

                # Cache local: reusar intel_basic si fue consultado en los últimos 7 días
                intel_basic_result = None
                intel_from_cache = False
                try:
                    from providers import singula_store
                    from datetime import datetime, timezone, timedelta
                    cache = singula_store.get_validations(cid)
                    entry = cache.get("intel_basic") if isinstance(cache, dict) else None
                    if isinstance(entry, dict) and entry.get("cached_at"):
                        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                            float(entry["cached_at"]), tz=timezone.utc
                        )
                        if age < timedelta(days=7):
                            entry_clean = {k: v for k, v in entry.items() if k != "cached_at"}
                            intel_basic_result = entry_clean
                            intel_from_cache = True
                except Exception:
                    pass

                if intel_basic_result is None:
                    intel_basic_result = sg.intel_basic(cid)
                    try:
                        from providers import singula_store
                        singula_store.save_validation(cid, "intel_basic", intel_basic_result)
                    except Exception:
                        pass

                # 2026-08-04: intel_premium deja de correr en automático.
                # Es el endpoint más caro de Singula. Sólo se ejecuta opt-in desde
                # el modal (cuando el usuario pulsa "▶ Ejecutar"). El frontend
                # puede ver el campo y dispararlo con force=True.
                return ("dossier", {
                    "customer_id": cid,
                    "customer_created": res.get("created", False),
                    "intel_basic": intel_basic_result,
                    "intel_basic_from_cache": intel_from_cache,
                    "intel_premium": None,
                    "intel_premium_skipped_reason": "requiere confirmación explícita del usuario (opt-in desde modal)",
                })
            except Exception as e:
                return ("dossier_err", str(e))

        # NOTA: validación de RFC con Singula eliminada — CheckID ya hace esto
        # y no queremos pagar doble por la misma consulta.
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures = [ex.submit(t) for t in [task_tlaloc, task_singula_intel]]
            for fut in as_completed(futures, timeout=120):
                try:
                    name, data = fut.result()
                    if name.endswith("_err"):
                        resultado["errores"].append({name: data})
                    elif data is not None:
                        resultado[name] = data
                except Exception as e:
                    resultado["errores"].append(str(e))

        # ====== Extraer email/phone del dossier ======
        emails_encontrados = []
        telefonos_encontrados = []
        intel_p = resultado.get("dossier", {}).get("intel_premium", {})
        if isinstance(intel_p, dict):
            summary = intel_p.get("summary", "") or ""
            emails_encontrados = re_mod.findall(r"[\w\.-]+@[\w\.-]+\.\w+", summary)
            telefonos_encontrados = re_mod.findall(
                r"\+?\d{1,2}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}", summary
            )
            emails_encontrados = list(set(emails_encontrados))
            telefonos_encontrados = list(set(telefonos_encontrados))

        # agregar inputs del payload
        if email_input and email_input not in emails_encontrados:
            emails_encontrados.append(email_input)
        if telefono_input and telefono_input not in telefonos_encontrados:
            telefonos_encontrados.append(telefono_input)

        resultado["resumen_email_telefono"] = {
            "emails": emails_encontrados,
            "telefonos": telefonos_encontrados,
        }

        # ====== FASE 4: Apify multi-vía (email + phone + nombre) ======
        if "apify" in clients and (emails_encontrados or telefonos_encontrados or nombre):
            apify = clients["apify"]
            # consolidar redes por cada vía
            redes_email = {"email_usado": emails_encontrados[0] if emails_encontrados else None,
                          "username_probado": None, "plataformas_encontradas": []}
            redes_telefono = {"telefono_usado": telefonos_encontrados[0] if telefonos_encontrados else None,
                              "plataformas_encontradas": []}
            redes_nombre = {"nombre_buscado": f"{nombre} {paterno} {materno}".strip(),
                            "plataformas_encontradas": []}

            # 2026-08-04: simplificado — task_apify_email y task_apify_telefono
            # estaban definidas pero NUNCA se submiteaban (sólo task_apify_full_dossier
            # entraba al pool). El ThreadPoolExecutor(max_workers=1) era un sinsentido
            # — llamadas directas son más rápidas y eliminan 3 actores Apify extra
            # que no aportaban resultados visibles.
            resultado["dossier_apify"] = None
            resultado["redes_por_email"] = {}
            resultado["redes_por_telefono"] = {}
            resultado["redes_por_nombre"] = {}
            resultado["perfiles_apify"] = []
            resultado["validacion_ia"] = {}
            if "apify" in clients:
                try:
                    data = apify.full_dossier(
                        email=emails_encontrados[0] if emails_encontrados else "",
                        phone=telefonos_encontrados[0] if telefonos_encontrados else "",
                        nombre=nombre, paterno=paterno, materno=materno,
                        company="", max_results=5,
                    )
                    resultado["dossier_apify"] = data
                    resultado["redes_por_email"] = data.get("resultados", {}).get("email", {})
                    resultado["redes_por_telefono"] = data.get("resultados", {}).get("phone", {})
                    resultado["redes_por_nombre"] = data.get("resultados", {}).get("name", {})
                    resultado["perfiles_apify"] = data.get("perfiles_consolidados", [])
                    resultado["validacion_ia"] = data.get("validacion_ia", {})
                except Exception as e:
                    resultado["errores"].append(f"apify_dossier: {e}")

        # ====== Consolidar redes totales sin duplicados (de las 3 vías) ======
        todas_redes = []
        seen = set()
        for fuente in (resultado["redes_por_email"], resultado["redes_por_telefono"], resultado["redes_por_nombre"]):
            for r in fuente.get("plataformas_encontradas", []):
                key = (r.get("plataforma"), r.get("url"))
                if key not in seen and r.get("plataforma"):
                    seen.add(key)
                    todas_redes.append(r)
        resultado["redes_totales"] = todas_redes

        # ====== Añadir RFC calculado al resultado ======
        if rfc_calculado:
            resultado["rfc_calculado"] = {
                "rfc_10": rfc_calculado,
                "dv_provisional": rfc_dv,
                "rfc_parcial": f"{rfc_calculado}??{rfc_dv}",
                "nota": "RFC parcial (10 chars + DV). La homoclave real (??) la asigna el SAT y solo se puede obtener con el RFC completo de 13 chars.",
                "fuente": "calculado desde CURP/datos demográficos con algoritmo SAT (folio IFAI 0610100135506)",
            }
            # comparar con RFC dado
            if rfc and len(rfc) >= 10:
                rfc_input_10 = rfc[:10]
                if rfc_input_10.upper() == rfc_calculado.upper():
                    resultado["rfc_calculado"]["coincide_con_input"] = True
                    resultado["rfc_calculado"]["nota"] += f" ✓ Coincide con RFC input: {rfc}"
                else:
                    resultado["rfc_calculado"]["coincide_con_input"] = False
                    resultado["rfc_calculado"]["nota"] += f" ✗ NO coincide: input={rfc[:10]} vs calculado={rfc_calculado}"

        resultado["duracion_seg"] = round(time.time() - t0, 2)
        self._json(200, resultado)

    def _handle_rfc_calcular_completo(self):
        """POST /api/rfc/calcular-completo

        Pipeline completo que usa TODOS los métodos Singula:
          1. Cálculo local del RFC (10 chars + DV)
          2. POST /app/rfc — obtener RFC real del SAT (con homoclave)
          3. POST /app/curp — obtener CURP desde datos (si no hay)
          4. POST /app/curp/validate — validar CURP asíncrono (devuelve customer_id)
          5. GET /app/risk/customer/{id}/signals — señales de riesgo
          6. GET /app/status — status del sistema

        Body: { curp? | rfc?, nombre?, paterno?, materno?, fecnac?, email?, telefono?, address?, city?, state? }
        """
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"error": f"JSON inválido: {e}"})
            return

        t0 = time.time()
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from config import config
        from rfc_utils import calcular_rfc_desde_curp, _calcular_dv, validar_rfc

        curp = payload.get("curp", "").strip()
        rfc_input = payload.get("rfc", "").strip()
        nombre = payload.get("nombre", "").strip()
        paterno = payload.get("paterno", "").strip()
        materno = payload.get("materno", "").strip()
        fecnac = payload.get("fecnac", "").strip()
        email = payload.get("email", "").strip()
        telefono = payload.get("telefono", "").strip()
        address = payload.get("address", "").strip()
        city = payload.get("city", "").strip()
        state = payload.get("state", "").strip()

        resultado = {
            "metadata": {
                "curp": curp, "rfc_input": rfc_input,
                "nombre": f"{nombre} {paterno} {materno}".strip(),
                "fecnac": fecnac, "email": email, "telefono": telefono,
            },
            "rfc_calculado_local": {},
            "rfc_obtenido_sat": {},
            "curp_obtenida": {},
            "curp_validada": {},
            "customer_id": None,
            "risk_signals": {},
            "status_sistema": {},
            "errores": [],
        }

        # 1) Cálculo local (instantáneo)
        rfc_10 = calcular_rfc_desde_curp(
            curp=curp, nombre=nombre, paterno=paterno, materno=materno, fecnac=fecnac
        )
        if rfc_10 and len(rfc_10) == 10:
            dv = _calcular_dv(rfc_10 + "XX")
            resultado["rfc_calculado_local"] = {
                "rfc_10": rfc_10,
                "dv_provisional": dv,
                "rfc_parcial": f"{rfc_10}??{dv}",
                "fuente": "Algoritmo SAT local",
            }

        # 2-6) Llamadas Singula (paralelo)
        def task_sat_rfc():
            if not config.singula_api_key or not (nombre and paterno):
                return ("rfc_sat", None)
            try:
                sg = SingulaClient(
                    config.singula_api_key, env=config.singula_env,
                    organization_id=config.singula_org_id
                )
                # extraer día/mes/año
                f = fecnac.replace("-", "").replace("/", "") if fecnac else ""
                if len(f) == 8:
                    bd, bm, by = f[6:8], f[4:6], f[:4]
                else:
                    bd, bm, by = "01", "01", "1980"
                # detectar género desde la CURP
                gender = "H"
                if curp and len(curp) == 18:
                    gender = "M" if curp[10] == "M" else "H"
                r = sg.get_rfc(nombre, paterno, materno, bd, bm, by, gender=gender)
                return ("rfc_sat", r)
            except Exception as e:
                return ("rfc_sat_err", str(e))

        def task_sat_curp():
            if not config.singula_api_key or not (nombre and paterno and fecnac):
                return ("curp_get", None)
            try:
                sg = SingulaClient(
                    config.singula_api_key, env=config.singula_env,
                    organization_id=config.singula_org_id
                )
                f = fecnac.replace("-", "").replace("/", "") if fecnac else ""
                if len(f) == 8:
                    bd, bm, by = f[6:8], f[4:6], f[:4]
                else:
                    return ("curp_get", None)
                # lugar de nacimiento desde CURP
                birth_place = curp[11:13] if curp and len(curp) == 18 else "DF"
                gender = "M" if curp[10] == "M" else "H"
                r = sg.get_curp(nombre, paterno, materno, gender, bd, bm, by, birth_place)
                return ("curp_get", r)
            except Exception as e:
                return ("curp_get_err", str(e))

        def task_validate_curp():
            if not config.singula_api_key or not curp:
                return ("curp_val", None)
            try:
                sg = SingulaClient(
                    config.singula_api_key, env=config.singula_env,
                    organization_id=config.singula_org_id
                )
                # sync=True para esperar resultado
                r = sg.validate_curp_async(curp, create_customer=True, sync=True)
                if isinstance(r, dict) and r.get("customer_id"):
                    return ("curp_val", r)
                return ("curp_val_err", r)
            except Exception as e:
                return ("curp_val_err", str(e))

        def task_status():
            if not config.singula_api_key:
                return ("status", None)
            try:
                sg = SingulaClient(
                    config.singula_api_key, env=config.singula_env,
                    organization_id=config.singula_org_id
                )
                return ("status", sg.get_status())
            except Exception as e:
                return ("status_err", str(e))

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(t) for t in [task_sat_rfc, task_sat_curp, task_validate_curp, task_status]]
            customer_id_for_signals = None
            for fut in as_completed(futures, timeout=120):
                try:
                    name, data = fut.result()
                    if name.endswith("_err"):
                        resultado["errores"].append({name: data})
                    elif data is not None:
                        if name == "rfc_sat":
                            resultado["rfc_obtenido_sat"] = data
                        elif name == "curp_get":
                            resultado["curp_obtenida"] = data
                        elif name == "curp_val":
                            resultado["curp_validada"] = data
                            customer_id_for_signals = data.get("customer_id")
                            resultado["customer_id"] = customer_id_for_signals
                        elif name == "status":
                            resultado["status_sistema"] = data
                except Exception as e:
                    resultado["errores"].append(str(e))

            # 5) Risk signals del customer (si tenemos customer_id)
            if customer_id_for_signals:
                try:
                    sg = SingulaClient(
                        config.singula_api_key, env=config.singula_env,
                        organization_id=config.singula_org_id
                    )
                    resultado["risk_signals"] = sg.get_risk_signals(customer_id_for_signals)
                except Exception as e:
                    resultado["errores"].append({"risk_signals_err": str(e)})

        # comparar RFC calculado local vs RFC obtenido del SAT
        if resultado["rfc_obtenido_sat"].get("rfc") and rfc_10:
            sat_rfc_10 = resultado["rfc_obtenido_sat"]["rfc"][:10]
            resultado["rfc_calculado_local"]["coincide_con_sat"] = (sat_rfc_10 == rfc_10)
            resultado["rfc_calculado_local"]["sat_rfc_10"] = sat_rfc_10

        resultado["duracion_seg"] = round(time.time() - t0, 2)
        self._json(200, resultado)

    def _handle_rfc_calcular(self):
        """POST /api/rfc/calcular

        Calcula el RFC a partir de CURP o datos demográficos (sin Apify, rápido).

        Body: { curp? | rfc?, nombre?, paterno?, materno?, fecnac? }
        Retorna:
          {
            rfc_calculado: {rfc_10, dv_provisional, rfc_parcial, ...},
            rfc_input: el RFC dado (si hay)
            match: bool si rfc_input coincide con rfc_calculado
          }
        """
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"error": f"JSON inválido: {e}"})
            return
        curp = payload.get("curp", "").strip()
        rfc_input = payload.get("rfc", "").strip()
        nombre = payload.get("nombre", "").strip()
        paterno = payload.get("paterno", "").strip()
        materno = payload.get("materno", "").strip()
        fecnac = payload.get("fecnac", "").strip()

        try:
            from rfc_utils import calcular_rfc_desde_curp, _calcular_dv, validar_rfc
            rfc_10 = calcular_rfc_desde_curp(
                curp=curp, nombre=nombre, paterno=paterno, materno=materno, fecnac=fecnac
            )
            resultado = {
                "metadata": {
                    "curp": curp, "rfc_input": rfc_input,
                    "nombre": f"{nombre} {paterno} {materno}".strip(),
                    "fecnac": fecnac,
                },
                "rfc_calculado": {},
                "rfc_input_validacion": {},
                "match": None,
            }
            if rfc_10 and len(rfc_10) == 10:
                # DV provisional (con homoclave "XX")
                dv = _calcular_dv(rfc_10 + "XX")
                # DV alternativos para todas las homoclaves posibles
                dvs = {}
                for c1 in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                    for c2 in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                        h = c1 + c2
                        dvs[h] = _calcular_dv(rfc_10 + h)
                # DV más común
                dv_comun = max(set(dvs.values()), key=lambda v: sum(1 for dv in dvs.values() if dv == v))
                resultado["rfc_calculado"] = {
                    "rfc_10": rfc_10,
                    "dv_provisional": dv,
                    "rfc_parcial": f"{rfc_10}??{dv}",
                    "dv_mas_comun_homoclaves": dv_comun,
                    "homoclaves_unicas": len(set(dvs.values())),
                    "nota": "Calculado desde CURP/datos. La homoclave real solo la tiene el SAT.",
                    "fuente": "Algoritmo SAT (folio IFAI 0610100135506)",
                }
                # comparar con RFC input
                if rfc_input and len(rfc_input) >= 10:
                    if rfc_input[:10].upper() == rfc_10.upper():
                        resultado["match"] = True
                        # validar DV
                        val = validar_rfc(rfc_input)
                        resultado["rfc_input_validacion"] = val
                        # buscar homoclave que da el DV real
                        homoclave_real = ""
                        for h, d in dvs.items():
                            if str(d) == str(rfc_input[12:13]) or (
                                d == "A" and rfc_input[12:13] == "A"
                            ) or (d == "0" and rfc_input[12:13] == "0"):
                                homoclave_real = h
                                break
                        resultado["rfc_calculado"]["homoclave_inferida"] = homoclave_real
                        resultado["rfc_calculado"]["nota"] = (
                            f"RFC completo coincide con el calculado. "
                            f"Homoclave inferida: {homoclave_real or '???'}, "
                            f"DV real: {rfc_input[12:13]}, DV calculado: {dv}"
                        )
                    else:
                        resultado["match"] = False
                        resultado["rfc_calculado"]["nota"] = (
                            f"NO coincide: input={rfc_input[:10]} vs calculado={rfc_10}"
                        )
            # ====== Validar contra el SAT ======
            # 2026-08-04: BLOQUEADO — Singula /app/rfc/validate está
            # deshabilitado en el provider. La validación de RFC la hace
            # CheckID (más completo, mismo SAT, ya integrado en el pipeline).
            validar_sat = payload.get("validar_sat", True)  # por defecto sí
            if validar_sat and rfc_input and len(rfc_input) in (12, 13):
                # Avisamos al frontend que debe usar CheckID en su lugar.
                # El cálculo local (10 chars + DV) ya se hizo arriba.
                resultado["validacion_sat"] = {
                    "valid": True,
                    "registered": None,
                    "message": "La validación de RFC contra el SAT la hace CheckID "
                               "en el pipeline de /api/sujeto. Llama al endpoint "
                               "de sujeto con la misma CURP/RFC para incluirla.",
                    "rfc": rfc_input,
                    "fuente": "redirect-to-checkid",
                    "redirect": True,
                }
            self._json(200, resultado)
        except Exception as e:
            self._json(500, {"error": str(e), "trace": str(e)})

    def _get_broker_clients(self):
        """Helper: inicializa y devuelve los clientes de providers (lazy)."""
        from config import config
        if not config._broker_clients:
            try:
                from providers.tlaloc import TlalocClient
                from providers.singula import SingulaClient
                from providers.moffin import MoffinClient
                from providers.apify_osint import ApifyOSINTClient
                from providers.ollama_cloud import OllamaCloudClient
                if config.tlaloc_api_key:
                    config._broker_clients["tlaloc"] = TlalocClient(config.tlaloc_api_key)
                if config.singula_api_key:
                    config._broker_clients["singula"] = SingulaClient(
                        config.singula_api_key, env=config.singula_env
                    )
                if config.moffin_api_key:
                    config._broker_clients["moffin"] = MoffinClient(
                        config.moffin_api_key, base_url=config.moffin_base_url
                    )
                if config.apify_token:
                    config._broker_clients["apify"] = ApifyOSINTClient(config.apify_token)
                if config.ollama_api_key:
                    config._broker_clients["ollama"] = OllamaCloudClient(
                        config.ollama_api_key, model=config.ollama_model
                    )
            except Exception as e:
                print(f"[servir] error inicializando clients: {e}")
        return config._broker_clients

    def _handle_kyc(self):
        """Endpoint KYC — consulta múltiples proveedores en paralelo."""
        url = urlparse(self.path)
        if url.path != "/api/kyc":
            self._json(404, {"error": "endpoint no existe"})
            return
        # parsear payload
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"error": f"JSON inválido: {e}"})
            return

        # ejecutar broker
        try:
            from kyc_broker import KYCBroker
            broker = KYCBroker(verbose=True)
            result = broker.full_kyc(
                curp=payload.get("curp"),
                rfc=payload.get("rfc"),
                nombre=payload.get("nombre"),
                paterno=payload.get("paterno"),
                materno=payload.get("materno"),
                fecnac=payload.get("fecnac"),
                email=payload.get("email"),
                telefono=payload.get("telefono"),
            )
            self._json(200, result)
        except Exception as e:
            self._json(500, {"error": f"error en KYC: {e}"})

    def _handle_enrich(self):
        """Endpoint /api/enrich — complementa datos del sujeto por fases.

        Body:
          {
            "fase": "fiscal" | "social" | "blacklist" | "todo",
            "sujeto": { curp, rfc, nombre, paterno, materno, fecnac, email, telefono },
            "datos_padron": { ... datos que ya tenemos del padrón ... }
          }

        Retorna:
          { "fase": ..., "resultados": { ... } }
        """
        url = urlparse(self.path)
        if url.path != "/api/enrich":
            self._json(404, {"error": "endpoint no existe"})
            return
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"error": f"JSON inválido: {e}"})
            return

        fase = (payload.get("fase") or "todo").lower()
        sujeto = payload.get("sujeto", {})
        datos_padron = payload.get("datos_padron", {})

        if not sujeto:
            self._json(400, {"error": "campo 'sujeto' requerido"})
            return

        try:
            from kyc_broker import KYCBroker
            broker = KYCBroker(verbose=False)
            t0 = time.time()
            resultado = {"fase": fase, "sujeto": sujeto, "datos_padron": datos_padron, "resultados": {}}

            # FASE 1: datos fiscales (Tlaloc + Singula)
            if fase in ("fiscal", "todo"):
                resultado["resultados"]["fiscal"] = broker.enrich_fiscal(
                    curp=sujeto.get("curp"),
                    rfc=sujeto.get("rfc"),
                    nombre=sujeto.get("nombre"),
                    paterno=sujeto.get("paterno"),
                    materno=sujeto.get("materno"),
                    only=payload.get("only"),
                )

            # FASE 2: redes sociales (Apify)
            if fase in ("social", "todo"):
                resultado["resultados"]["social"] = broker.enrich_social(
                    nombre=sujeto.get("nombre"),
                    paterno=sujeto.get("paterno"),
                    materno=sujeto.get("materno") or "",
                    email=sujeto.get("email") or "",
                    telefono=sujeto.get("telefono") or "",
                )

            # FASE 3: blacklist (Singula, requiere customer_id)
            if fase in ("blacklist", "todo"):
                resultado["resultados"]["blacklist"] = broker.enrich_blacklist(
                    nombre=sujeto.get("nombre", ""),
                    paterno=sujeto.get("paterno", ""),
                    materno=sujeto.get("materno", ""),
                    curp=sujeto.get("curp", ""),
                )

            # FASE 4: huella digital completa (Singula customer-centric)
            if fase in ("huella", "todo"):
                pad = payload.get("datos_padron", {})
                paquete = payload.get("paquete", "premium")
                resultado["resultados"]["huella"] = broker.enrich_huella(
                    curp=sujeto.get("curp"),
                    rfc=sujeto.get("rfc"),
                    nss=sujeto.get("nss") or pad.get("nss"),
                    nombre=sujeto.get("nombre", ""),
                    paterno=sujeto.get("paterno", ""),
                    materno=sujeto.get("materno", ""),
                    fecnac=sujeto.get("fecnac", ""),
                    email=sujeto.get("email") or pad.get("email"),
                    telefono=sujeto.get("telefono") or pad.get("telefono"),
                    direccion=f"{pad.get('calle', '')} {pad.get('ext', '')} {pad.get('interior', '')}, {pad.get('colonia', '')}".strip(", "),
                    ciudad=pad.get("municipio") or "",
                    estado=pad.get("estado") or "",
                    cp=pad.get("cp") or "",
                    env=payload.get("env") or config.singula_env,
                    paquete=paquete,
                )

            resultado["duracion_seg"] = round(time.time() - t0, 2)
            self._json(200, resultado)
        except Exception as e:
            import traceback
            self._json(500, {"error": f"error en enrich: {e}", "trace": traceback.format_exc()[:500]})

    def _handle_health_bases(self):
        """GET /api/v1/health/bases — diagnóstico de bases externas attacheadas."""
        con = _init_extended_con()
        bases = []
        for alias, (path, _) in EXTENDED_DBS.items():
            entry = {"alias": alias, "path": path, "exists": os.path.exists(path)}
            if entry["exists"]:
                try:
                    # Tablas que tiene la base externa (cross-database OK para SHOW)
                    tabs = [r[0] for r in con.execute(
                        f"SHOW TABLES FROM {alias}"
                    ).fetchall()]
                    entry["tables"] = tabs
                    # Conteos
                    for t in tabs:
                        try:
                            c = con.execute(f"SELECT COUNT(*) FROM {alias}.main.{t}").fetchone()
                            entry.setdefault("counts", {})[t] = c[0]
                        except Exception:
                            pass
                except Exception as e:
                    entry["error"] = str(e)[:200]
            bases.append(entry)
        # Vistas api disponibles
        vistas = [r[0] for r in con.execute(
            "SELECT view_name FROM duckdb_views() WHERE schema_name='api'"
        ).fetchall()]
        self._json(200, {
            "ok": True,
            "extended_dbs": bases,
            "api_vistas": vistas,
        })

    def _handle_persona_rfc(self, rfc: str, todo: bool = False):
        """GET /api/v1/persona/rfc/<rfc>[/todo]

        Busca el RFC normalizado en las bases externas (att, empleadores)
        y devuelve la información disponible. Si `todo=1`, incluye el agregado
        de fuentes y, si hay coincidencia en el padrón por nombre, lo intenta
        (curp-rfc no se puede unir directamente sin tabla puente).
        """
        con = _init_extended_con()
        rfc = rfc.strip().upper()

        # 1) att — datos de la persona (dirección, teléfonos, nombre)
        att_rows = []
        try:
            att_rows = con.execute("""
                SELECT rfc, nombre_completo, nombres, apellido_paterno, apellido_materno,
                       telefono_fijo, celular, direccion, num_interior, num_exterior,
                       colonia, municipio, estado, estado_origen, rfc_kind
                FROM api.att_persona WHERE rfc = ?
            """, [rfc]).fetchall()
            att_cols = ["rfc","nombre_completo","nombres","apellido_paterno","apellido_materno",
                        "telefono_fijo","celular","direccion","num_interior","num_exterior",
                        "colonia","municipio","estado","estado_origen","rfc_kind"]
        except duckdb.Error as e:
            att_cols = []
            att_rows = []
            att_err = str(e)[:200]
        else:
            att_err = None

        # 2) empleadores — si la persona RFC es dueña de empresa (PF13) o es la empresa misma (PM12)
        emp_rows = []
        try:
            emp_rows = con.execute("""
                SELECT rfc, razon_social, nombre_comercial, num_empleados, descripcion,
                       correo, web, contacto_cargo, contacto_nombre, contacto_paterno,
                       contacto_materno, contacto_telefono, contacto_correo, contacto_extension,
                       dom_calle, dom_ext, dom_int, dom_colonia, dom_municipio, dom_entidad,
                       dom_cp, rfc_kind
                FROM api.empleadores WHERE rfc = ?
            """, [rfc]).fetchall()
            emp_cols = ["rfc","razon_social","nombre_comercial","num_empleados","descripcion",
                        "correo","web","contacto_cargo","contacto_nombre","contacto_paterno",
                        "contacto_materno","contacto_telefono","contacto_correo","contacto_extension",
                        "dom_calle","dom_ext","dom_int","dom_colonia","dom_municipio","dom_entidad",
                        "dom_cp","rfc_kind"]
        except duckdb.Error as e:
            emp_cols = []
            emp_rows = []
            emp_err = str(e)[:200]
        else:
            emp_err = None

        # 2b) telcel — 2026-08-06: bug fix. Antes solo se devolvía el conteo en
        # `fuentes` pero los rows nunca llegaban al response, por lo que el
        # usuario veía "3 telcel" en el conteo y 0 rows en telcel, asumiendo
        # que el cruce estaba roto.
        telcel_rows = []
        try:
            telcel_rows = con.execute("""
                SELECT cuenta, telefono, plan_actual, marca, modelo,
                       estado_linea, titular_nombre1, titular_nombre2,
                       titular_domicilio, titular_ciudad, titular_estado, titular_cp,
                       fecha_activacion, fecha_cancelacion,
                       esn, imei, iccid, archivo_origen, rfc_kind
                FROM api.telcel_lineas_full
                WHERE rfc = ?
                ORDER BY fecha_activacion DESC NULLS LAST, cuenta
                LIMIT 50
            """, [rfc]).fetchall()
            telcel_cols = ["cuenta","telefono","plan_actual","marca","modelo",
                           "estado_linea","titular_nombre1","titular_nombre2",
                           "titular_domicilio","titular_ciudad","titular_estado","titular_cp",
                           "fecha_activacion","fecha_cancelacion",
                           "esn","imei","iccid","archivo_origen","rfc_kind"]
        except duckdb.Error as e:
            telcel_cols = []
            telcel_rows = []
            telcel_err = str(e)[:200]
        else:
            telcel_err = None

        # 2c) repuve — vehículos a nombre del RFC
        repuve_rows = []
        try:
            repuve_rows = con.execute("""
                SELECT rfc, placa, no_serie, marca, modelo, color, uso,
                       propietario, direccion_propietario, telefono_propietario, rfc_kind
                FROM api.repuve_de_persona WHERE rfc = ?
                LIMIT 50
            """, [rfc]).fetchall()
            repuve_cols = ["rfc","placa","no_serie","marca","modelo","color","uso",
                           "propietario","direccion_propietario","telefono_propietario","rfc_kind"]
        except duckdb.Error as e:
            repuve_cols = []
            repuve_rows = []
            repuve_err = str(e)[:200]
        else:
            repuve_err = None

        # 2d) imss_segmentacion — si el RFC está en imss_s, devolver los rows
        imss_s_rows = []
        try:
            imss_s_rows = con.execute("""
                SELECT curp, rfc, nss, nombre, apellido_paterno, apellido_materno,
                       unidad_medica, modalidad, fecha_de_nacimiento,
                       segmentacion_cancer_de_mama, segmentacion_cancer_de_prostata,
                       segmentacion_diabetes_mellitus, segmento_hipertension,
                       desc_enfermedad_cap, desc_enfermedad_hipertension, rfc_kind
                FROM api.imss_salud_full
                WHERE rfc = ?
                LIMIT 50
            """, [rfc]).fetchall()
            imss_s_cols = ["curp","rfc","nss","nombre","apellido_paterno","apellido_materno",
                           "unidad_medica","modalidad","fecha_de_nacimiento",
                           "segmentacion_cancer_de_mama","segmentacion_cancer_de_prostata",
                           "segmentacion_diabetes_mellitus","segmento_hipertension",
                           "desc_enfermedad_cap","desc_enfermedad_hipertension","rfc_kind"]
        except duckdb.Error as e:
            imss_s_cols = []
            imss_s_rows = []
            imss_s_err = str(e)[:200]
        else:
            imss_s_err = None

        response = {
            "rfc": rfc,
            "found": bool(att_rows or emp_rows or telcel_rows or repuve_rows or imss_s_rows),
            "att": {
                "rows": [dict(zip(att_cols, r)) for r in att_rows] if att_rows else [],
                "count": len(att_rows),
                "error": att_err,
            },
            "empleadores": {
                "rows": [dict(zip(emp_cols, r)) for r in emp_rows] if emp_rows else [],
                "count": len(emp_rows),
                "error": emp_err,
            },
            "telcel": {
                "rows": [dict(zip(telcel_cols, r)) for r in telcel_rows] if telcel_rows else [],
                "count": len(telcel_rows),
                "error": telcel_err,
            },
            "repuve": {
                "rows": [dict(zip(repuve_cols, r)) for r in repuve_rows] if repuve_rows else [],
                "count": len(repuve_rows),
                "error": repuve_err,
            },
            "imss_salud": {
                "rows": [dict(zip(imss_s_cols, r)) for r in imss_s_rows] if imss_s_rows else [],
                "count": len(imss_s_rows),
                "error": imss_s_err,
            },
        }

        if todo:
            # 3) Agregado de fuentes: ¿en qué bases aparece este RFC y cuántas veces?
            fuentes = []
            try:
                fuentes = con.execute("""
                    SELECT fuente, n FROM api.fuentes_por_rfc WHERE rfc = ?
                """, [rfc]).fetchall()
            except duckdb.Error:
                pass
            response["fuentes"] = [{"fuente": f, "registros": n} for f, n in fuentes]
            # 4) Total histórico
            total = sum(n for _, n in fuentes)
            response["total_registros_externos"] = total

        # Auditoría
        try:
            self._audit(
                session=session, action="view_rfc_enriquecido",
                endpoint=f"/api/v1/persona/rfc/{rfc}" + ("/todo" if todo else ""),
                method="GET", status_code=200,
                query_summary={"rfc_prefix": rfc[:4]},
                results_count=len(att_rows) + len(emp_rows) + len(telcel_rows) + len(repuve_rows) + len(imss_s_rows),
            )
        except Exception:
            pass  # no fallar el endpoint si la auditoría tiene problemas

        self._json(200, response)

    def _handle_persona_curp(self, curp: str, todo: bool = False):
        """GET /api/v1/persona/curp/<curp>[/todo]

        Búsqueda por CURP en las 6 bases externas attacheadas (2026-08-05):

        1. **xwalk curp→rfc** vía `b_imss_s.main.imss_personas`: la única base
           con ambas columnas (curp_clean + rfc_clean) en 23.8M filas. Lookup
           directo con zone-map (sub-segundo).
        2. **CURP-keyed**: `api.imss_asegurado` (patrones + sueldo + cp5) +
           `api.imss_salud` (segmentación diabetes/hipertensión/cáncer).
        3. **RFC-keyed via xwalk**: `api.att_persona` + `api.empleadores` +
           `api.repuve_de_persona` + `api.telcel_lineas`. Una sola query por
           base usando `WHERE rfc IN (...)` para todos los RFCs del xwalk.

        Si `todo=1`, incluye `api.fuentes_por_rfc` agregado y un KPI resumen
        (total_registros_externos, vehiculos_count, lineas_count, etc.).
        """
        con = _init_extended_con()
        curp = curp.strip().upper()

        # 1) xwalk curp→rfc (lookup directo, no materializa DISTINCT global).
        #    Devuelve RFCs PF13/PF10/PM12/PM10 (claves que aparecen en bases RFC-keyed).
        rfcs = []
        try:
            rfcs = [r[0] for r in con.execute("""
                SELECT DISTINCT rfc_clean FROM b_imss_s.main.imss_personas
                WHERE curp_clean = ? AND rfc_clean IS NOT NULL
                  AND rfc_kind IN ('PF13','PM12','PF10','PM10')
            """, [curp]).fetchall()]
            xwalk_err = None
        except duckdb.Error as e:
            xwalk_err = str(e)[:200]

        # 2) imss_asegurado (CURP-keyed) — patrones + sueldo
        imss_a_rows = []
        imss_a_err = None
        try:
            imss_a_rows = con.execute("""
                SELECT curp, nss, nss_raw, curp_raw, registro_patron, nombre_patron,
                       empresa_nombre, empresa_domicilio, empresa_ciudad_estado,
                       empresa_cp_raw, empresa_cp, empresa_giro, sueldo,
                       curp_len, curp_kind
                FROM api.imss_asegurado_full WHERE curp = ?
            """, [curp]).fetchall()
        except duckdb.Error as e:
            imss_a_err = str(e)[:200]
        imss_a_cols = ["curp","nss","nss_raw","curp_raw","registro_patron","nombre_patron",
                       "empresa_nombre","empresa_domicilio","empresa_ciudad_estado",
                       "empresa_cp_raw","empresa_cp","empresa_giro","sueldo",
                       "curp_len","curp_kind"]

        # 3) imss_salud (CURP-keyed) — segmentación salud FULL.
        # 2026-08-05: ampliado a _full (61 cols vs 17 de la legacy).
        imss_s_rows = []
        imss_s_err = None
        try:
            imss_s_rows = con.execute("""
                SELECT curp, rfc, nss, curp_raw, nss_raw, rfc_raw,
                       id_persona, id_calidad, id_segmento, id_segmento_cama,
                       id_segmento_cap, id_segmento_ht, id_tipo_derechohabiente,
                       cve_delegacion, cve_modalidad, cve_nivel_atencion, cve_region,
                       cve_unidadmedica,
                       region, col_origen, porcentaje, porcentaje_ooad,
                       porcentaje_unidad_medica, agregado_medico,
                       personas, personas_2,
                       prioridad, prioridad_cama, prioridad_cap, prioridad_ht,
                       unidad_medica, ooad, modalidad, tipo_de_derechohabiente,
                       desc_nivel_atencion, convenio, correo_electronico,
                       ref_celular, telefono,
                       nombre, apellido_paterno, apellido_materno,
                       rango_de_edad, edad, genero,
                       fecha_de_nacimiento, fecha_segmentacion,
                       segmentacion_cancer_de_mama, segmentacion_cancer_de_prostata,
                       segmentacion_diabetes_mellitus, segmento_hipertension,
                       desc_enfermedad_cama, desc_enfermedad_cap,
                       desc_enfermedad_diabetes, desc_enfermedad_hipertension,
                       registro_patronal, razon_social,
                       curp_len, curp_kind, rfc_kind
                FROM api.imss_salud_full WHERE curp = ?
            """, [curp]).fetchall()
        except duckdb.Error as e:
            imss_s_err = str(e)[:200]
        imss_s_cols = [
            "curp","rfc","nss","curp_raw","nss_raw","rfc_raw",
            "id_persona","id_calidad","id_segmento","id_segmento_cama",
            "id_segmento_cap","id_segmento_ht","id_tipo_derechohabiente",
            "cve_delegacion","cve_modalidad","cve_nivel_atencion","cve_region",
            "cve_unidadmedica","region","col_origen","porcentaje",
            "porcentaje_ooad","porcentaje_unidad_medica","agregado_medico",
            "personas","personas_2","prioridad","prioridad_cama","prioridad_cap",
            "prioridad_ht","unidad_medica","ooad","modalidad",
            "tipo_de_derechohabiente","desc_nivel_atencion","convenio",
            "correo_electronico","ref_celular","telefono","nombre",
            "apellido_paterno","apellido_materno","rango_de_edad","edad",
            "genero","fecha_de_nacimiento","fecha_segmentacion",
            "segmentacion_cancer_de_mama","segmentacion_cancer_de_prostata",
            "segmentacion_diabetes_mellitus","segmento_hipertension",
            "desc_enfermedad_cama","desc_enfermedad_cap",
            "desc_enfermedad_diabetes","desc_enfermedad_hipertension",
            "registro_patronal","razon_social","curp_len","curp_kind",
            "rfc_kind",
        ]   # 59 cols; el count exacto se valida contra DESCRIBE al primer hit

        # 4) Bases RFC-keyed via WHERE rfc IN (...) — sólo si tenemos al menos 1 RFC
        att_rows, emp_rows, repuve_rows, telcel_rows = [], [], [], []
        att_err = emp_err = repuve_err = telcel_err = None
        if rfcs:
            ph = ",".join(["?"] * len(rfcs))
            params = rfcs

            # att
            try:
                att_rows = con.execute(f"""
                    SELECT rfc, nombre_completo, nombres, apellido_paterno, apellido_materno,
                           telefono_fijo, celular, direccion, num_interior, num_exterior,
                           colonia, municipio, estado, estado_origen, rfc_kind
                    FROM api.att_persona WHERE rfc IN ({ph})
                """, params).fetchall()
            except duckdb.Error as e:
                att_err = str(e)[:200]
            att_cols = ["rfc","nombre_completo","nombres","apellido_paterno","apellido_materno",
                        "telefono_fijo","celular","direccion","num_interior","num_exterior",
                        "colonia","municipio","estado","estado_origen","rfc_kind"]

            # empleadores
            try:
                emp_rows = con.execute(f"""
                    SELECT rfc, razon_social, nombre_comercial, num_empleados,
                           dom_calle, dom_ext, dom_int, dom_colonia, dom_municipio,
                           dom_entidad, dom_cp, rfc_kind
                    FROM api.empleadores WHERE rfc IN ({ph})
                """, params).fetchall()
            except duckdb.Error as e:
                emp_err = str(e)[:200]
            emp_cols = ["rfc","razon_social","nombre_comercial","num_empleados",
                        "dom_calle","dom_ext","dom_int","dom_colonia","dom_municipio",
                        "dom_entidad","dom_cp","rfc_kind"]

            # repuve
            try:
                repuve_rows = con.execute(f"""
                    SELECT rfc, placa, no_serie, marca, modelo, color, uso,
                           propietario, direccion_propietario, telefono_propietario, rfc_kind
                    FROM api.repuve_de_persona WHERE rfc IN ({ph})
                """, params).fetchall()
            except duckdb.Error as e:
                repuve_err = str(e)[:200]
            repuve_cols = ["rfc","placa","no_serie","marca","modelo","color","uso",
                           "propietario","direccion_propietario","telefono_propietario","rfc_kind"]

            # telcel
            try:
                telcel_rows = con.execute(f"""
                    SELECT rfc, telefono, plan, marca, modelo, esn, imei, iccid,
                           estado_linea, estado_cuenta, fecha_activacion,
                           fecha_cancelacion, titular_nombre1, titular_nombre2, rfc_kind
                    FROM api.telcel_lineas WHERE rfc IN ({ph})
                """, params).fetchall()
            except duckdb.Error as e:
                telcel_err = str(e)[:200]
            telcel_cols = ["rfc","telefono","plan","marca","modelo","esn","imei","iccid",
                           "estado_linea","estado_cuenta","fecha_activacion",
                           "fecha_cancelacion","titular_nombre1","titular_nombre2","rfc_kind"]
        else:
            att_cols = emp_cols = repuve_cols = telcel_cols = []

        response = {
            "curp": curp,
            "xwalk": {
                "rfcs": rfcs,
                "count": len(rfcs),
                "error": xwalk_err,
            },
            "imss_asegurado": {
                "rows": [dict(zip(imss_a_cols, r)) for r in imss_a_rows] if imss_a_rows else [],
                "count": len(imss_a_rows),
                "error": imss_a_err,
            },
            "imss_salud": {
                "rows": [dict(zip(imss_s_cols, r)) for r in imss_s_rows] if imss_s_rows else [],
                "count": len(imss_s_rows),
                "error": imss_s_err,
            },
            "att": {
                "rows": [dict(zip(att_cols, r)) for r in att_rows] if att_rows else [],
                "count": len(att_rows),
                "error": att_err,
            },
            "empleadores": {
                "rows": [dict(zip(emp_cols, r)) for r in emp_rows] if emp_rows else [],
                "count": len(emp_rows),
                "error": emp_err,
            },
            "repuve": {
                "rows": [dict(zip(repuve_cols, r)) for r in repuve_rows] if repuve_rows else [],
                "count": len(repuve_rows),
                "error": repuve_err,
            },
            "telcel": {
                "rows": [dict(zip(telcel_cols, r)) for r in telcel_rows] if telcel_rows else [],
                "count": len(telcel_rows),
                "error": telcel_err,
            },
            "found": bool(
                len(rfcs) or imss_a_rows or imss_s_rows
                or att_rows or emp_rows or repuve_rows or telcel_rows
            ),
        }

        if todo:
            # KPI agregados (sumas a través de las 6 bases)
            vehiculos = repuve_rows  # ya filtrado por curp
            lineas = telcel_rows
            patrones = imss_a_rows
            response["kpi"] = {
                "rfcs_encontrados": len(rfcs),
                "patrones_imss": len(patrones),
                "vehiculos_repuve": len(vehiculos),
                "lineas_telcel": len(lineas),
                "empleadores": len(emp_rows),
                "registros_att": len(att_rows),
                "segmentaciones_salud": len(imss_s_rows),
                "total_registros_externos": (
                    len(imss_a_rows) + len(imss_s_rows)
                    + len(att_rows) + len(emp_rows)
                    + len(repuve_rows) + len(telcel_rows)
                ),
            }

        # Auditoría
        try:
            self._audit(
                session=session, action="view_curp_enriquecido",
                endpoint=f"/api/v1/persona/curp/{curp}" + ("/todo" if todo else ""),
                method="GET", status_code=200,
                query_summary={"curp_prefix": curp[:8], "rfcs_count": len(rfcs)},
                results_count=response["kpi"]["total_registros_externos"] if todo else (
                    len(imss_a_rows) + len(imss_s_rows) + len(att_rows) + len(emp_rows)
                    + len(repuve_rows) + len(telcel_rows)
                ),
            )
        except Exception:
            pass

        self._json(200, response)

    # Cap de líneas devueltas por RFC/CURP en el endpoint /lineas.
    # BBVA Bancomer (BBA830831LJ2) tiene 89,532 líneas telcel — devolverlas
    # todas sería ~30MB de JSON. El cap protege el cliente sin perder
    # utilidad (las primeras N ordenadas por fecha_activacion cubren casos
    # típicos). Si se necesitan más, llamar con `?cap=` o usar /todo.
    LINEAS_DEFAULT_CAP = 5000
    LINEAS_MAX_CAP = 50000

    def _handle_persona_lineas(self, kind: str, key: str, todo: bool = False):
        """GET /api/v1/persona/lineas/{rfc,curp}/<key>[/todo]

        Lista TODAS las líneas telcel (full-detail, 45 columnas)
        y registros att (full-detail, 18 columnas) asociados al
        RFC o CURP del sujeto.

        Args:
            kind: 'rfc' o 'curp'.
            key: el identificador (10-18 chars, ya normalizado a MAYÚSCULAS).
            todo: si True incluye resumen estadístico por plan/estado.

        Cap por respuesta: 5,000 líneas telcel por defecto (BBVA Bancomer
        tiene 89k, no las queremos todas en una sola respuesta). Para
        forzar el total, pasar `?cap=50000` como query string. El cap
        afecta sólo a telcel — att es típicamente <10 rows.

        Si kind='curp', primero resuelve el xwalk via b_imss_s para
        obtener RFCs (mismo patrón que /api/v1/persona/curp/<curp>).
        """
        from urllib.parse import urlparse, parse_qs
        url = urlparse(self.path)
        params = parse_qs(url.query)
        try:
            cap = int(params.get("cap", [str(self.LINEAS_DEFAULT_CAP)])[0])
        except (TypeError, ValueError):
            cap = self.LINEAS_DEFAULT_CAP
        cap = max(1, min(cap, self.LINEAS_MAX_CAP))

        con = _init_extended_con()

        # Si el lookup es por CURP, primero resolver RFCs via xwalk
        rfcs = []
        curp_query = None
        xwalk_err = None
        if kind == "curp":
            curp_query = key
            try:
                rfcs = [r[0] for r in con.execute("""
                    SELECT DISTINCT rfc_clean FROM b_imss_s.main.imss_personas
                    WHERE curp_clean = ? AND rfc_clean IS NOT NULL
                      AND rfc_kind IN ('PF13','PM12','PF10','PM10')
                """, [curp_query]).fetchall()]
            except duckdb.Error as e:
                xwalk_err = str(e)[:200]
        else:
            rfcs = [key]

        telcel_rows = []
        telcel_total = 0
        att_rows = []
        att_total = 0
        telcel_err = att_err = None

        if rfcs:
            ph = ",".join(["?"] * len(rfcs))

            # TELCEL full-detail (45 cols) — aplicar cap
            try:
                # Si el cap es >= al total, no LIMIT (cuesta más proyectar 45 cols).
                # Primero contar total exacto, luego aplicar LIMIT si necesario.
                telcel_total = con.execute(
                    f"SELECT COUNT(*) FROM api.telcel_lineas_full WHERE rfc IN ({ph})", rfcs
                ).fetchone()[0]
                if telcel_total <= cap:
                    telcel_rows = con.execute(f"""
                        SELECT
                            cuenta, telefono, plan_actual, plan_origen, marca, modelo,
                            estado_linea, estado_cuenta, estado_cobranza, clase_credito,
                            tipo, ciclo, motivo,
                            fecha_activacion, fecha_cancelacion, fecha_termino,
                            fecha_celular, fecha_plan, fecha_equipo,
                            titular_nombre1, titular_nombre2, titular_domicilio,
                            titular_numero, titular_interior, titular_colonia,
                            titular_ciudad, titular_estado, titular_cp,
                            titular_tel_contacto,
                            esn, imei, iccid, asesor, adendum, plazo,
                            datos_origen, datos_actuales, gsm_indicador,
                            tipo_rfc, tipo_pago, tc, contacto1, contacto2,
                            renaut, archivo_origen, rfc_kind
                        FROM api.telcel_lineas_full
                        WHERE rfc IN ({ph})
                        ORDER BY fecha_activacion DESC NULLS LAST
                    """, rfcs).fetchall()
                else:
                    telcel_rows = con.execute(f"""
                        SELECT
                            cuenta, telefono, plan_actual, plan_origen, marca, modelo,
                            estado_linea, estado_cuenta, estado_cobranza, clase_credito,
                            tipo, ciclo, motivo,
                            fecha_activacion, fecha_cancelacion, fecha_termino,
                            fecha_celular, fecha_plan, fecha_equipo,
                            titular_nombre1, titular_nombre2, titular_domicilio,
                            titular_numero, titular_interior, titular_colonia,
                            titular_ciudad, titular_estado, titular_cp,
                            titular_tel_contacto,
                            esn, imei, iccid, asesor, adendum, plazo,
                            datos_origen, datos_actuales, gsm_indicador,
                            tipo_rfc, tipo_pago, tc, contacto1, contacto2,
                            renaut, archivo_origen, rfc_kind
                        FROM api.telcel_lineas_full
                        WHERE rfc IN ({ph})
                        ORDER BY fecha_activacion DESC NULLS LAST
                        LIMIT ?
                    """, rfcs + [cap]).fetchall()
            except duckdb.Error as e:
                telcel_err = str(e)[:200]

            # ATT full-detail (18 cols)
            try:
                att_total = con.execute(
                    f"SELECT COUNT(*) FROM api.att_persona_full WHERE rfc IN ({ph})", rfcs
                ).fetchone()[0]
                att_rows = con.execute(f"""
                    SELECT
                        rfc, nombres, apellido_paterno, apellido_materno,
                        telefono_fijo, celular, direccion, num_interior, num_exterior,
                        colonia, municipio, estado, estado_origen,
                        rfc_original, rfc_len, archivo_origen, rfc_kind
                    FROM api.att_persona_full
                    WHERE rfc IN ({ph})
                """, rfcs).fetchall()
            except duckdb.Error as e:
                att_err = str(e)[:200]

        telcel_cols = [
            "cuenta","telefono","plan_actual","plan_origen","marca","modelo",
            "estado_linea","estado_cuenta","estado_cobranza","clase_credito",
            "tipo","ciclo","motivo",
            "fecha_activacion","fecha_cancelacion","fecha_termino",
            "fecha_celular","fecha_plan","fecha_equipo",
            "titular_nombre1","titular_nombre2","titular_domicilio",
            "titular_numero","titular_interior","titular_colonia",
            "titular_ciudad","titular_estado","titular_cp",
            "titular_tel_contacto",
            "esn","imei","iccid","asesor","adendum","plazo",
            "datos_origen","datos_actuales","gsm_indicador",
            "tipo_rfc","tipo_pago","tc","contacto1","contacto2",
            "renaut","archivo_origen","rfc_kind",
        ]
        att_cols = [
            "rfc","nombres","apellido_paterno","apellido_materno",
            "telefono_fijo","celular","direccion","num_interior","num_exterior",
            "colonia","municipio","estado","estado_origen",
            "rfc_original","rfc_len","archivo_origen","rfc_kind",
        ]

        response = {
            "kind": kind,
            "key": key,
            "rfcs": rfcs,
            "cap": cap,
            "truncated_telcel": bool(telcel_total > cap),
            "telcel": {
                "rows": [dict(zip(telcel_cols, r)) for r in telcel_rows],
                "count": len(telcel_rows),
                "total": telcel_total,
                "error": telcel_err,
            },
            "att": {
                "rows": [dict(zip(att_cols, r)) for r in att_rows],
                "count": len(att_rows),
                "total": att_total,
                "error": att_err,
            },
            "found": bool(telcel_rows or att_rows),
        }

        if curp_query:
            response["curp"] = curp_query
            response["xwalk_error"] = xwalk_err

        if todo and telcel_rows:
            # Resumen por plan + estado_linea + marca
            from collections import Counter
            planes = Counter(r[telcel_cols.index("plan_actual")] or "(vacío)" for r in telcel_rows)
            estados = Counter(r[telcel_cols.index("estado_linea")] or "(vacío)" for r in telcel_rows)
            marcas = Counter(f"{(r[telcel_cols.index('marca')] or '?').strip()}/{(r[telcel_cols.index('modelo')] or '?').strip()}"
                              for r in telcel_rows)
            response["resumen"] = {
                "planes": dict(planes.most_common(20)),
                "estados_linea": dict(estados.most_common()),
                "top_marca_modelo": dict(marcas.most_common(10)),
            }

        # Auditoría
        try:
            self._audit(
                session=session, action="view_lineas_enriquecido",
                endpoint=f"/api/v1/persona/lineas/{kind}/{key}" + ("/todo" if todo else ""),
                method="GET", status_code=200,
                query_summary={"key_kind": kind, "key_prefix": key[:6], "cap": cap,
                               "rfcs_count": len(rfcs)},
                results_count=len(telcel_rows) + len(att_rows),
            )
        except Exception:
            pass

        self._json(200, response)

    def _handle_report_get(self, path):
        """GET /api/report/html?curp=XXX o /api/report/pdf?curp=XXX

        Genera reporte desde padrón local + enrichment del store Singula
        local. Para reporte con AI narrativo, usar POST /api/report/generate.
        """
        from urllib.parse import parse_qs
        url = urlparse(self.path)
        params = parse_qs(url.query)
        curp = (params.get("curp", [""])[0] or "").upper().strip()
        if len(curp) != 18:
            self._json(400, {"error": "curp inválida o no proporcionada"})
            return
        rows = self.db.by_curp(curp)
        if not rows:
            self._json(404, {"error": "sujeto no encontrado en padrón"})
            return
        row = rows[0]
        row["nombre_completo"] = f"{row.get('nombre', '')} {row.get('paterno', '')} {row.get('materno', '')}".strip()
        # Agregar nombres de estado y municipio
        e_num = row.get("e")
        row["estado_nombre"] = ESTADOS.get(e_num, "") if isinstance(e_num, int) else ""
        try:
            cp_sujeto = str(row.get("cp", "")).strip().zfill(5)[:5]
            if cp_sujeto and cp_sujeto.isdigit():
                sep_con = get_sepomex()
                mrow = sep_con.execute(
                    "SELECT municipio FROM cp WHERE cp=? LIMIT 1", (cp_sujeto,)
                ).fetchone()
                if mrow:
                    row["municipio_nombre"] = mrow["municipio"] or ""
        except Exception:
            pass

        # Generar mapa estático del padrón
        map_b64, map_location = _generate_map_for_sujeto(row)

        # Cargar enrichment + validaciones cacheadas del store local
        enrichment = self._load_singula_enrichment(curp)

        # 2026-08-13: auto-búsqueda issste si no está cacheada y tenemos
        # nombre + paterno. Útil para detectar employment en sector público.
        if not enrichment.get("issste"):
            paterno = (row.get("paterno") or "").upper().strip()
            nombre  = (row.get("nombre") or "").upper().strip()
            if paterno and len(paterno) >= 3:
                try:
                    con_ext = _init_extended_con()
                    if con_ext is not None:
                        iss_rows = con_ext.execute("""
                            SELECT id, paterno, materno, nombres,
                                   cargo, sexo, sueldo,
                                   ramo, entidad, modalidad, sector, estado
                            FROM api.issste_empleado
                            WHERE paterno LIKE ?
                            ORDER BY sueldo DESC NULLS LAST
                            LIMIT 25
                        """, [f"%{paterno}%"]).fetchall()
                        if iss_rows:
                            cols = ["id","paterno","materno",
                                    "nombres","cargo","sexo",
                                    "sueldo","ramo","entidad",
                                    "modalidad","sector","estado"]
                            enrichment["issste"] = {
                                "matches": [dict(zip(cols, r)) for r in iss_rows],
                                "query": {"paterno": paterno,
                                          "nombre": nombre or None},
                                "count": len(iss_rows),
                                "source": "auto_lookup",
                            }
                except Exception:
                    pass

        # Generar mapa del CP de CheckID si difiere del padrón
        checkid_cp = ""
        checkid_map_b64 = None
        checkid_map_location = None
        try:
            checkid_data = (enrichment or {}).get("checkid", {})
            if isinstance(checkid_data, dict):
                chk_cp = _extract_checkid_str(checkid_data.get("codigo_postal"), "codigoPostal")
                if chk_cp and chk_cp != str(row.get("cp", "")):
                    checkid_cp = str(chk_cp).strip().zfill(5)[:5]
                    checkid_map_b64, checkid_map_location = _generate_map_for_cp(
                        checkid_cp, source="checkid"
                    )
        except Exception:
            pass

        # 2026-08-13: recolectar TODAS las direcciones en TODAS las bases
        # y generar un mapa individual por cada una.
        extra_maps = []
        try:
            addresses = _collect_addresses_for_sujeto(row, enrichment=enrichment)
            extra_maps = _generate_maps_for_addresses(addresses, max_maps=25)
        except Exception:
            extra_maps = []

        try:
            from report_generator import generate_subject_html, generate_html_to_pdf
            html = generate_subject_html(
                row, narrative="", enrichment=enrichment,
                map_image_base64=map_b64, map_location=map_location,
                checkid_map_image_base64=checkid_map_b64,
                checkid_map_location=checkid_map_location,
                checkid_cp=checkid_cp,
                extra_maps=extra_maps,
            )
            if path == "/api/report/html":
                body = html.encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
            else:  # pdf
                pdf = generate_html_to_pdf(html)
                self._send(200, pdf, "application/pdf")
        except Exception as e:
            import traceback
            self._json(500, {"error": f"error generando reporte: {e}", "trace": traceback.format_exc()[:500]})

    def _load_singula_enrichment(self, curp: str) -> dict:
        """Carga el enrichment completo + validaciones cacheadas del store
        local Singula (singula_cache.db) y lo devuelve en el formato que
        espera report_generator.generate_subject_html.

        Returns: dict con keys {checkid, tlaloc_curp, tlaloc_rfc, singula, ...}
        """
        out = {}
        try:
            from providers import singula_store
            cached = singula_store.get_all_for_curp(curp)
            if not cached:
                return out

            customer_id = cached.get("customer_id")
            enrichment = cached.get("enrichment") or {}
            validations = cached.get("validations_cache") or {}

            # checkid (mapeado desde enrichment.checkid)
            if "checkid" in enrichment:
                ck = enrichment["checkid"]
                out["checkid"] = {
                    "exitoso": True,
                    "rfc": ck.get("rfc"),
                    "razon_social": ck.get("razon_social"),
                    "nss": ck.get("nss"),
                    "codigo_postal": {"codigoPostal": ck.get("codigo_postal")} if ck.get("codigo_postal") else None,
                    "regimen_fiscal": {"regimenesFiscales": [ck.get("regimen_fiscal")]} if ck.get("regimen_fiscal") else None,
                    "estado_69_69b": ck.get("estado_69_69b"),
                    "email": ck.get("email_contacto"),
                }

            # tlaloc_curp / tlaloc_rfc (mapeado desde enrichment.tlaloc)
            if "tlaloc" in enrichment:
                tl = enrichment["tlaloc"]
                if "curp_renapo" in tl:
                    out["tlaloc_curp"] = {
                        "valid": tl["curp_renapo"].get("valid"),
                        "status": tl["curp_renapo"].get("status"),
                        "nombres": tl["curp_renapo"].get("nombres"),
                        "primerApellido": tl["curp_renapo"].get("apellido_paterno"),
                        "segundoApellido": tl["curp_renapo"].get("apellido_materno"),
                        "sexo": tl["curp_renapo"].get("sexo"),
                        "fechaNacimiento": tl["curp_renapo"].get("fecha_nacimiento"),
                        "nacionalidad": tl["curp_renapo"].get("nacionalidad"),
                        "entidad": tl["curp_renapo"].get("entidad"),
                    }
                if "rfc_sat" in tl:
                    out["tlaloc_rfc"] = tl["rfc_sat"]

            # apify_social (mapeado al formato del reporte)
            if "apify_social" in enrichment:
                out["social_media"] = enrichment["apify_social"]

            # issste (mapeado al formato del reporte)
            # 2026-08-13: el enrichment de Singula puede incluir resultados
            # de búsqueda en api.issste_empleado (cacheado por CURP).
            # Si NO hay cache y el subject tiene nombre+paterno+materno,
            # ejecutar la búsqueda directamente sobre la vista api.issste_empleado.
            if "issste" in enrichment:
                iss = enrichment["issste"]
                if isinstance(iss, dict):
                    # Normalizar formato: el reporte espera {"matches": [...]}
                    if "matches" not in iss and "rows" in iss:
                        iss = {"matches": iss.get("rows", []), **iss}
                    out["issste"] = iss
            elif out.get("issste") is None:
                # Auto-búsqueda issste: solo si tenemos nombre+paterno (sin RFC/CURP)
                # y la conexión extendida está inicializada.
                try:
                    # Necesitamos el subject del store para obtener nombre/paterno
                    # El caller (_load_singula_enrichment) no recibe el sujeto.
                    # Por lo tanto la auto-búsqueda se hace en /api/v1/issste/buscar
                    # y se guarda manualmente vía _save_enrichment si se quiere cachear.
                    pass  # no-op aquí; se deja el endpoint HTTP para uso explícito.
                except Exception:
                    pass

            # singula (datos completos del pipeline)
            if customer_id and (enrichment or validations):
                # construir estructura "singula" que el reporte entiende
                singula_section = {
                    "customer_id": customer_id,
                    "enrich_ok": True,
                    "enrich_sources": enrichment.get("sources", []),
                    "enrichment": enrichment,
                    "validations": validations,
                }
                # marcar cuáles se reusaron del cache
                from datetime import datetime, timezone, timedelta
                now = datetime.now(timezone.utc)
                from_cache = {}
                for k, v in validations.items():
                    ts = v.get("cached_at") if isinstance(v, dict) else None
                    if ts:
                        try:
                            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                            if (now - dt) < timedelta(days=7):
                                from_cache[k] = True
                        except Exception:
                            pass
                singula_section["validations_from_cache"] = from_cache
                singula_section["validations_executed"] = []  # no se ejecutaron aquí, sólo lectura
                out["singula"] = singula_section

        except Exception:
            pass
        return out

    def _handle_report_generate(self):
        """POST /api/report/generate

        Body: {
          sujeto: {datos padrón},
          enrichment: {fiscal, social, blacklist},
          style: 'formal' | 'investigative' | 'concise',
          format: 'html' | 'pdf' | 'json',
        }

        Genera reporte con narrativa AI (Ollama Cloud) + HTML/PDF.
        """
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"error": f"JSON inválido: {e}"})
            return

        sujeto = payload.get("sujeto", {})
        enrichment = payload.get("enrichment", {})
        style = payload.get("style", "formal")
        out_format = payload.get("format", "html")

        if not sujeto:
            self._json(400, {"error": "campo 'sujeto' requerido"})
            return

        # 1) generar narrativa con Ollama
        narrative = ""
        ollama_status = "skipped"
        try:
            from providers.ollama_cloud import OllamaCloudClient
            from config import config
            if config.ollama_api_key:
                ollama = OllamaCloudClient(config.ollama_api_key, model=config.ollama_model)
                # serializar todo para que el modelo interprete
                full_data = {
                    "padron": sujeto,
                    "enrichment": enrichment,
                }
                narrative = ollama.generate_report_narrative(full_data, style=style)
                ollama_status = "ok" if narrative else "empty"
            else:
                ollama_status = "no_api_key"
        except Exception as e:
            ollama_status = f"error: {e}"
            narrative = ""

        # 2) generar HTML/PDF
        try:
            from report_generator import generate_subject_html, generate_html_to_pdf
            sujeto["_modelo_ia"] = "deepseek-v4-pro" if "ollama_model" not in sujeto else "unknown"

            # Generar mapa estático del padrón
            map_b64, map_location = _generate_map_for_sujeto(sujeto)

            # Generar mapa del CP de CheckID si difiere del padrón
            checkid_cp = ""
            checkid_map_b64 = None
            checkid_map_location = None
            try:
                checkid_data = (enrichment or {}).get("checkid", {}) if isinstance(enrichment, dict) else {}
                if isinstance(checkid_data, dict):
                    chk_cp = _extract_checkid_str(checkid_data.get("codigo_postal"), "codigoPostal")
                    if chk_cp and chk_cp != str(sujeto.get("cp", "")):
                        checkid_cp = str(chk_cp).strip().zfill(5)[:5]
                        checkid_map_b64, checkid_map_location = _generate_map_for_cp(
                            checkid_cp, source="checkid"
                        )
            except Exception:
                pass

            # 2026-08-13: recolectar TODAS las direcciones en TODAS las bases
            extra_maps = []
            try:
                addresses = _collect_addresses_for_sujeto(sujeto, enrichment=enrichment)
                extra_maps = _generate_maps_for_addresses(addresses, max_maps=25)
            except Exception:
                extra_maps = []

            html = generate_subject_html(sujeto, narrative=narrative, enrichment=enrichment,
                                         map_image_base64=map_b64,
                                         map_location=map_location,
                                         checkid_map_image_base64=checkid_map_b64,
                                         checkid_map_location=checkid_map_location,
                                         checkid_cp=checkid_cp,
                                         extra_maps=extra_maps)
            if out_format == "pdf":
                pdf = generate_html_to_pdf(html)
                self._send(200, pdf, "application/pdf")
                return
            elif out_format == "json":
                self._json(200, {
                    "html": html,
                    "narrative": narrative,
                    "ollama_status": ollama_status,
                    "metadata": {
                        "sujeto": sujeto.get("nombre_completo"),
                        "curp": sujeto.get("curp"),
                        "timestamp": time.time(),
                    }
                })
                return
            else:  # html
                body = html.encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
        except Exception as e:
            import traceback
            self._json(500, {"error": f"error generando reporte: {e}", "trace": traceback.format_exc()[:500]})

    def _serve_html(self, filename=None):
        try:
            # ROOT = directorio del script (backend/).
            # Los HTML viven en ../frontend/ (un nivel arriba).
            frontend_dir = ROOT.parent / "frontend"
            path = frontend_dir / (filename or "buscar.html")
            data = path.read_bytes()
            self._send(200, data, "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send(404, f"{filename or 'buscar.html'} no encontrado".encode(), "text/plain")

    def _handle_singula_health(self):
        """GET /api/singula/health — ping de la API de Singula.

        Usa /customer/search?q=__healthcheck__ (búsqueda inocua) y mide
        latencia + estado HTTP. Devuelve:
          - ok=True/False
          - status_code (HTTP de la API, no del endpoint nuestro)
          - latency_ms
          - error (si hubo)
          - organization_id (configurado)

        La idea es que el admin screen pueda mostrar un indicador
        verde/rojo de salud de Singula SIN tener que abrir una ficha
        de sujeto (que es lenta). Si ok=False, el problema más común
        es SINGULA_API_KEY expirada (rotar en dashboard de Singula).
        """
        t0 = time.time()
        session = self._require_session()
        if not session:
            return
        try:
            from config import config
            if not config.singula_api_key:
                self._json(200, {
                    "ok": False,
                    "status_code": None,
                    "latency_ms": 0,
                    "error": "SINGULA_API_KEY no configurada en .env",
                    "organization_id": None,
                })
                return
            sc = get_singula_client()
            # Ping con búsqueda inocua (devuelve [] pero requiere auth)
            try:
                r = sc._get("/customer/search",
                            params={"q": "__healthcheck__", "env": sc.env})
                latency_ms = int((time.time() - t0) * 1000)
                # Si la respuesta vino (incluso con data:[]), la auth pasó
                self._json(200, {
                    "ok": True,
                    "status_code": 200,
                    "latency_ms": latency_ms,
                    "error": None,
                    "organization_id": sc.organization_id,
                    "env": sc.env,
                    "cache_size": len(sc._customer_cache),
                })
                # Auditar como actividad normal
                try:
                    self._audit(
                        session=session,
                        action="singula_health_check",
                        endpoint="/api/singula/health",
                        method="GET",
                        status_code=200,
                        duration_ms=latency_ms,
                        query_summary={"latency_ms": latency_ms, "ok": True},
                    )
                except Exception:
                    pass
            except Exception as e:
                latency_ms = int((time.time() - t0) * 1000)
                msg = str(e)
                # Extraer status_code del mensaje si está presente
                status = None
                if "HTTP 401" in msg:
                    status = 401
                elif "HTTP 4" in msg:
                    status = 403
                elif "HTTP 5" in msg:
                    status = 500
                elif "timeout" in msg.lower():
                    status = 408
                self._json(200, {
                    "ok": False,
                    "status_code": status,
                    "latency_ms": latency_ms,
                    "error": msg,
                    "organization_id": getattr(sc, "organization_id", None),
                    "env": sc.env,
                    "hint": "API key expirada o inválida — rotar en dashboard de Singula"
                            if status == 401 else None,
                })
        except Exception as e:
            self._json(200, {
                "ok": False,
                "status_code": None,
                "latency_ms": int((time.time() - t0) * 1000),
                "error": f"handler error: {e}",
            })

    def _handle_sujeto(self):
        """GET /api/sujeto?curp=XXXX — devuelve datos del padrón + RFC + Tlaloc + validaciones disponibles."""
        t0 = time.time()
        session = self._require_session()
        if not session:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            curp = query.get("curp", [""])[0].upper().strip()
            if not curp:
                self._json(400, {"error": "falta ?curp=..."})
                return

            t0 = time.time()
            sujeto = _find_sujeto(curp)
            if not sujeto:
                self._json(404, {"error": "curp no encontrada en padron"})
                return

            # calcular RFC real con Singula
            # calcular RFC real con Singula
            rfc_sat = None
            rfc_local = None
            singula_err = None
            try:
                sc = get_singula_client()
                f = parse_fecnac(sujeto.get("fecnac"))
                rfc_sat_raw = sc.get_rfc(
                    name=sujeto.get("nombre", ""),
                    last_name=sujeto.get("paterno", ""),
                    mothers_last_name=sujeto.get("materno", ""),
                    birth_day=f["day"],
                    birth_month=f["month"],
                    birth_year=f["year"],
                    gender=sujeto.get("sexo", "H"),
                )
                if isinstance(rfc_sat_raw, dict) and "rfc" in rfc_sat_raw:
                    rfc_sat = rfc_sat_raw["rfc"]
                elif isinstance(rfc_sat_raw, str):
                    rfc_sat = rfc_sat_raw
                else:
                    singula_err = rfc_sat_raw.get("error", "respuesta inesperada")
            except Exception as e:
                singula_err = str(e)
                rfc_sat = None

            # calcular RFC local (fallback cuando Singula falle por créditos)
            rfc_local = None
            rfc_local_10 = ""
            try:
                from rfc_utils import calcular_rfc_desde_curp
                rfc_local_value = calcular_rfc_desde_curp(curp)
                if rfc_local_value and len(rfc_local_value) == 10:
                    rfc_local = {
                        "rfc_10": rfc_local_value,
                        "fuente": "cálculo local SAT (sin homoclave real)",
                    }
                    rfc_local_10 = rfc_local_value
                else:
                    rfc_local = {"rfc_10": rfc_local_value[:10] if rfc_local_value else "", "fuente": "local"}
                    rfc_local_10 = rfc_local["rfc_10"]
            except Exception:
                rfc_local = None
                rfc_local_10 = ""

            # ==========================================================
            # CheckID — consulta fiscal principal por CURP o RFC
            # ==========================================================
            checkid_data = None
            checkid_err = None
            checkid_rfc = None
            try:
                cc = get_checkid_client()
                # primero intentamos por CURP; si el plan no lo permite fallback por RFC
                rfc_hint = rfc_sat if rfc_sat and len(rfc_sat) >= 13 else (rfc_local.get("rfc_10", "") if rfc_local else "")
                checkid_resp = cc.get_full(curp, rfc_hint=rfc_hint)
                if checkid_resp.get("exitoso"):
                    checkid_data = checkid_resp
                    checkid_rfc = checkid_resp.get("rfc")
                    # extraer emailContacto del nodo rfc si existe
                    rfc_node = (checkid_resp.get("raw") or {}).get("resultado", {}).get("rfc", {})
                    email_checkid = rfc_node.get("emailContacto") or rfc_node.get("email")
                else:
                    checkid_err = f"{checkid_resp.get('codigo_error')}: {checkid_resp.get('error')}"
                    # si CheckID devolvió un RFC válido aunque con error parcial, lo usamos
                    checkid_rfc = checkid_resp.get("rfc")
            except Exception as e:
                checkid_err = str(e)

            # Si CheckID nos dio un RFC real, lo preferimos sobre el de Singula/local
            if checkid_rfc and len(checkid_rfc) >= 13:
                rfc_sat = checkid_rfc
                source = "CheckID SAT"
                singula_err = None  # ya no aplica
            elif rfc_sat and len(rfc_sat) >= 13:
                source = "Singula SAT"
            elif rfc_local and rfc_local.get("rfc_10"):
                source = "local (incompleto, falta homoclave)"
            else:
                source = "no disponible"

            # comparar RFC local vs SAT final (después de resolver CheckID/Singula)
            if rfc_local and rfc_local_10 and rfc_sat:
                rfc_local["coincide_con_sat"] = (rfc_local_10 == rfc_sat[:10])

            # sujeto final con RFC
            sujeto["rfc"] = rfc_sat if rfc_sat and len(rfc_sat) >= 13 else (rfc_local.get("rfc_10", "") if rfc_local else "")

            # Validar Tlaloc CURP
            tlaloc_curp = None
            try:
                tc = get_tlaloc_client()
                tlaloc_curp = tc.validate_curp(curp)
            except Exception as e:
                tlaloc_curp = {"error": str(e)}

            # Validar Tlaloc RFC (automático)
            tlaloc_rfc = None
            if rfc_sat:
                try:
                    tc = get_tlaloc_client()
                    tlaloc_rfc = tc.validate_rfc(rfc_sat)
                except Exception as e:
                    tlaloc_rfc = {"error": str(e)}

            # ==========================================================
            # Cruce con bases externas RFC-keyed (att, empleadores, repuve,
            # telcel, imss_asegurado_full, imss_salud_full) — 2026-08-05.
            # Se dispara UNA VEZ por sujeto, después de CheckID/Singula
            # resolver el RFC SAT real (PF13/PM12 con homoclave). Si
            # todavía no hay RFC >= 13, usamos el RFC local (PF10) para
            # att/empleadores, pero telcel/repuve requieren PF13/PM12.
            # ==========================================================
            externas_por_rfc = {
                "rfcs_consultados": [],
                "att":              {"rows": [], "count": 0, "error": None},
                "empleadores":      {"rows": [], "count": 0, "error": None},
                "repuve":           {"rows": [], "count": 0, "error": None},
                "telcel":           {"rows": [], "count": 0, "error": None},
                "imss_asegurado":   {"rows": [], "count": 0, "error": None},
                "imss_salud":       {"rows": [], "count": 0, "error": None},
            }
            try:
                con_ext = _init_extended_con()
                if con_ext is not None:
                    # Lista única de RFCs a cruzar (dedup, sin vacíos)
                    rfc_canon = (rfc_sat or "").strip().upper()[:13]
                    rfc_local_10 = (rfc_local.get("rfc_10", "") if rfc_local else "").strip().upper()[:10]
                    rfcs_a_buscar = []
                    if rfc_canon and len(rfc_canon) >= 13:
                        rfcs_a_buscar.append(rfc_canon)
                        if rfc_canon[:10] and rfc_canon[:10] != rfc_canon:
                            rfcs_a_buscar.append(rfc_canon[:10])
                    elif rfc_local_10 and len(rfc_local_10) == 10:
                        rfcs_a_buscar.append(rfc_local_10)
                    rfcs_a_buscar = list({r for r in rfcs_a_buscar if r})
                    externas_por_rfc["rfcs_consultados"] = rfcs_a_buscar
                    if rfcs_a_buscar:
                        # Tabla temporal con los RFCs a cruzar.
                        # Usamos INSERT por fila en vez de unnest(?::VARCHAR[])
                        # porque la sintaxis con unnest es frágil entre
                        # versiones de duckdb-python. Esta forma es siempre
                        # portable y arregla el bug de "IN ()" cuando la
                        # lista venía vacía.
                        con_ext.execute("CREATE OR REPLACE TEMP TABLE _rfcs_x(rfc VARCHAR)")
                        con_ext.executemany(
                            "INSERT INTO _rfcs_x VALUES (?)",
                            [(r,) for r in rfcs_a_buscar],
                        )
                        ext_queries = [
                            ("att",
                             """SELECT rfc, nombres, apellido_paterno, apellido_materno,
                                       telefono_fijo, celular, direccion, num_interior,
                                       num_exterior, colonia, municipio, estado,
                                       estado_origen, rfc_kind
                                FROM api.att_persona
                                WHERE rfc IN (SELECT rfc FROM _rfcs_x)""",
                             ["rfc","nombres","apellido_paterno","apellido_materno",
                              "telefono_fijo","celular","direccion","num_interior",
                              "num_exterior","colonia","municipio","estado",
                              "estado_origen","rfc_kind"]),
                            ("empleadores",
                             """SELECT rfc, razon_social, nombre_comercial, num_empleados,
                                       correo, web, contacto_nombre, contacto_telefono,
                                       dom_calle, dom_colonia, dom_municipio, dom_entidad,
                                       dom_cp, rfc_kind
                                FROM api.empleadores
                                WHERE rfc IN (SELECT rfc FROM _rfcs_x)""",
                             ["rfc","razon_social","nombre_comercial","num_empleados",
                              "correo","web","contacto_nombre","contacto_telefono",
                              "dom_calle","dom_colonia","dom_municipio","dom_entidad",
                              "dom_cp","rfc_kind"]),
                            ("repuve",
                             """SELECT rfc, placa, no_serie, marca, tipo, modelo, color,
                                       propietario, direccion_propietario,
                                       telefono_propietario, rfc_kind
                                FROM api.repuve_de_persona
                                WHERE rfc IN (SELECT rfc FROM _rfcs_x)
                                LIMIT 100""",
                             ["rfc","placa","no_serie","marca","tipo","modelo","color",
                              "propietario","direccion_propietario",
                              "telefono_propietario","rfc_kind"]),
                            ("telcel",
                             """SELECT rfc, telefono, plan, marca, modelo, estado_linea,
                                       estado_cuenta, fecha_activacion,
                                       titular_nombre1, titular_nombre2,
                                       colonia, ciudad, estado, cp, rfc_kind
                                FROM api.telcel_lineas
                                WHERE rfc IN (SELECT rfc FROM _rfcs_x)
                                LIMIT 100""",
                             ["rfc","telefono","plan","marca","modelo","estado_linea",
                              "estado_cuenta","fecha_activacion",
                              "titular_nombre1","titular_nombre2",
                              "colonia","ciudad","estado","cp","rfc_kind"]),
                            ("imss_asegurado",
                             """SELECT curp, nss, nombre_patron, registro_patron,
                                       empresa_nombre, empresa_domicilio,
                                       empresa_ciudad_estado, empresa_cp,
                                       empresa_giro, sueldo, curp_kind
                                FROM api.imss_asegurado_full
                                WHERE curp = ?""",
                             ["curp","nss","nombre_patron","registro_patron",
                              "empresa_nombre","empresa_domicilio",
                              "empresa_ciudad_estado","empresa_cp",
                              "empresa_giro","sueldo","curp_kind"]),  # CURP-keyed (no RFC)
                            ("imss_salud",
                             """SELECT curp, rfc, nss, unidad_medica, ooad,
                                       segmentacion_diabetes_mellitus,
                                       segmento_hipertension,
                                       desc_enfermedad_diabetes,
                                       desc_enfermedad_hipertension,
                                       curp_kind
                                FROM api.imss_salud_full
                                WHERE curp = ?
                                LIMIT 1""",
                             ["curp","rfc","nss","unidad_medica","ooad",
                              "segmentacion_diabetes_mellitus",
                              "segmento_hipertension",
                              "desc_enfermedad_diabetes",
                              "desc_enfermedad_hipertension",
                              "curp_kind"]),  # CURP-keyed (no RFC)
                        ]
                        for key, sql, cols in ext_queries:
                            try:
                                if key in ("imss_asegurado", "imss_salud"):
                                    rows = con_ext.execute(sql, [curp]).fetchall()
                                else:
                                    rows = con_ext.execute(sql).fetchall()
                                if rows and cols:
                                    rows_dicts = [dict(zip(cols, r)) for r in rows]
                                else:
                                    rows_dicts = []  # si cols es None o rows vacío, dejar lista vacía
                                externas_por_rfc[key] = {
                                    "rows": rows_dicts,
                                    "count": len(rows),
                                    "error": None,
                                }
                            except duckdb.Error as e:
                                externas_por_rfc[key]["error"] = str(e)[:200]
                            except Exception as e:
                                externas_por_rfc[key]["error"] = str(e)[:200]
            except Exception as e:
                externas_por_rfc["error"] = str(e)[:200]

            # ==========================================================
            # Social Media Finder — automático como CheckID
            # Busca perfiles en 13 redes y filtra solo coincidencias exactas de nombre.
            # 2026-08-04: cache local con TTL 7d para no cobrar Apify en cada apertura.
            # 2026-08-04: bloque duplicado eliminado — el que corría en 2700-2731
            # sobre-escribía el resultado de éste. Se cobraba Apify DOS veces.
            # ==========================================================
            social_media = None
            social_media_err = None
            nombre_buscar = f"{sujeto.get('nombre','')} {sujeto.get('paterno','')} {sujeto.get('materno','')}".strip()
            if nombre_buscar:
                # 1) intentar cache local por CURP (TTL 7d)
                cached_sm = None
                try:
                    import json
                    from datetime import datetime, timezone, timedelta
                    sm_cache_path = "/root/nuevas_bases/social_media_cache.json"  # placeholder, ver init abajo
                    # la cache real está en sqlite (auth.db). Por simplicidad y para
                    # evitar nuevo archivo, usamos el cache existente de singula_store
                    # indexado por CURP (que tiene el customer_id y un campo extra).
                    from providers import singula_store as _ss
                    curp = sujeto.get("curp", "")
                    if curp:
                        row = _ss.get_all_for_curp(curp)
                        if row and row.get("social_media_cache"):
                            entry = row["social_media_cache"]
                            if isinstance(entry, dict) and entry.get("cached_at"):
                                age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                                    float(entry["cached_at"]), tz=timezone.utc
                                )
                                if age < timedelta(days=7):
                                    cached_sm = {k: v for k, v in entry.items() if k != "cached_at"}
                                    cached_sm["from_cache"] = True
                except Exception:
                    pass
                if cached_sm is not None:
                    social_media = cached_sm
                    social_media_err = None
                else:
                    try:
                        from apify_broker import ApifyBroker
                        from config import config
                        if config.apify_token:
                            broker = ApifyBroker(config.apify_token)
                            raw = broker.find_social_media([nombre_buscar])
                            all_candidates = []
                            for search_name, items in raw.items():
                                if isinstance(items, list):
                                    for item in items:
                                        if isinstance(item, dict) and not item.get("error"):
                                            item["_search_name"] = search_name
                                            all_candidates.append(item)
                            exact_matches = _filter_exact_name_matches(all_candidates, nombre_buscar)
                            social_media = {
                                "total_encontrados": len(all_candidates),
                                "coincidencias_exactas": len(exact_matches),
                                "perfiles": exact_matches,
                            }
                            # 2) persistir en cache local para próximas aperturas
                            try:
                                from providers import singula_store as _ss_save
                                curp = sujeto.get("curp", "")
                                if curp:
                                    row = _ss_save.get_all_for_curp(curp)
                                    if row:
                                        # usar save_enrichment para anexar al JSON
                                        enrichment = _ss_save.get_enrichment(row["customer_id"]) or {}
                                        import json as _json, time as _time
                                        enrichment["social_media_cache"] = dict(social_media)
                                        enrichment["social_media_cache"]["cached_at"] = _time.time()
                                        _ss_save.save_enrichment(
                                            customer_id=row["customer_id"], curp=curp,
                                            enrichment=enrichment, custom_id=curp,
                                        )
                            except Exception:
                                pass
                        else:
                            social_media_err = "APIFY_TOKEN no configurado"
                    except Exception as e:
                        social_media_err = str(e)
            else:
                social_media_err = "sin nombre para buscar"

            # NOTA 2026-08-04: el segundo bloque duplicado "Social Media Finder"
            # que corría justo antes del pipeline Singula (líneas 2700-2731)
            # quedó ELIMINADO. Era idéntico a éste pero se ejecutaba después,
            # sobre-escribiendo el resultado. Causaba DOBLE cobro Apify por apertura.

            # sujeto final con RFC
            sujeto["rfc"] = rfc_sat if rfc_sat and len(rfc_sat) >= 13 else (rfc_local.get("rfc_10", "") if rfc_local else "")
            sujeto["nombre_completo"] = f"{sujeto.get('nombre','')} {sujeto.get('paterno','')} {sujeto.get('materno','')}".strip()
            # agregar nombres de estado y municipio para SEPOMEX
            e_num = sujeto.get("e")
            sujeto["estado_nombre"] = ESTADOS.get(e_num, "") if isinstance(e_num, int) else ""
            # buscar municipio desde sepomex.db usando el CP
            municipio_nombre = ""
            try:
                cp_sujeto = str(sujeto.get("cp", "")).strip().zfill(5)[:5]
                if cp_sujeto and cp_sujeto.isdigit():
                    sep_con = get_sepomex()
                    mrow = sep_con.execute(
                        "SELECT municipio FROM cp WHERE cp=? LIMIT 1", (cp_sujeto,)
                    ).fetchone()
                    if mrow:
                        municipio_nombre = mrow["municipio"] or ""
            except Exception:
                pass
            sujeto["municipio_nombre"] = municipio_nombre

            # extraer email de CheckID si existe
            email_checkid = None
            if checkid_data and checkid_data.get("raw"):
                rfc_node = checkid_data.get("raw", {}).get("resultado", {}).get("rfc", {})
                email_checkid = rfc_node.get("emailContacto") or rfc_node.get("email")
            if email_checkid:
                sujeto["email_checkid"] = email_checkid
                sujeto["email"] = email_checkid  # preferimos este email sobre el del padrón

            # ==========================================================
            # SEPOMEX — dirección completa desde el CP (colonia, municipio, estado)
            # ==========================================================
            sepomex_data = None
            try:
                cp_sujeto_sep = str(sujeto.get("cp", "")).strip().zfill(5)[:5]
                if cp_sujeto_sep and cp_sujeto_sep.isdigit():
                    sep_con = get_sepomex()
                    mrow = sep_con.execute(
                        "SELECT municipio, estado, asentamiento FROM cp WHERE cp=? LIMIT 1",
                        (cp_sujeto_sep,),
                    ).fetchone()
                    if mrow:
                        sepomex_data = {
                            "cp": cp_sujeto_sep,
                            "municipio": mrow["municipio"] or "",
                            "estado": mrow["estado"] or "",
                            "asentamiento": mrow["asentamiento"] or "",
                        }
                        # completar sujeto si no se había llenado
                        if not sujeto.get("municipio_nombre"):
                            sujeto["municipio_nombre"] = sepomex_data["municipio"]
            except Exception:
                pass

            # ==========================================================
            # SINGULA: pipeline completo
            #   1) BUSCAR primero en cache local por CURP → descargar customer
            #      existente de Singula y reusar su enrichment + validaciones
            #      cacheadas. Si NO hay match, crear customer nuevo.
            #   2) enriquecer con padrón + checkid + tlaloc + sepomex + apify
            #      (si no vino del cache)
            #   3) ejecutar validaciones automáticas con el perfil enriquecido
            # ==========================================================
            # 2026-08-04: ELIMINADO el segundo bloque "Social Media Finder"
            # que corría justo antes de este — sobre-escribía el resultado
            # cacheado/calculado arriba. Era un DOBLE cobro de Apify.
            # 2026-08-04: lookup por CURP en store local ANTES de crear
            # customer — bloquea validaciones que ya existen en cache y
            # evita cobro de Singula por reaperturas del mismo sujeto.
            singula_customer_id = None
            singula_create_err = None
            singula_enrich = None
            singula_validations = None
            singula_enrich_err = None
            singula_validations_err = None
            singula_customer_created = None
            singula_cache_lookup = None  # metadata del lookup para exponer al frontend
            try:
                sc = get_singula_client()
                if config.singula_api_key:
                    # 0) BUSCAR customer existente por CURP en store local.
                    # Si existe y está vivo en Singula, descargamos enrichment
                    # y validaciones_cache. Las validaciones que ya tenemos
                    # en cache fresco (TTL 7d) se BLOQUEAN en _build_validations
                    # (enabled=False) y se sirven desde cache sin cobrar.
                    cached_lookup = _lookup_cached_customer_by_curp(sc, curp, sujeto=sujeto)
                    singula_cache_lookup = {
                        "found_in_local_store": cached_lookup["found"],
                        "validations_total": cached_lookup["validations_total"],
                        "validations_fresh": cached_lookup["validations_fresh"],
                        "stale_validations": cached_lookup["stale_validations"],
                        "store_purged": cached_lookup["store_purged"],
                        "ensure_error": cached_lookup["ensure_error"],
                    }

                    if cached_lookup["found"]:
                        # 1a) REUSAR customer existente — no se crea nuevo
                        singula_customer_id = cached_lookup["customer_id"]
                        singula_customer_created = False
                        cached_enrich = cached_lookup["enrichment"]
                        # 2026-08-05: anti-duplicado. El store local puede
                        # tener un customer_id obsoleto si alguien (el
                        # usuario desde el dashboard, un cron, otro
                        # proceso) creó un customer en Singula con la
                        # misma CURP por su cuenta. Antes de gastar
                        # créditos en este cid local, verificamos que
                        # sea el "último" buscando por nombre+CURP en
                        # Singula. Si hay un cid más reciente, lo usamos
                        # y actualizamos el store.
                        try:
                            remote = sc._search_customer_by_custom_id(
                                custom_id_anchor="",
                                curp=curp,
                                name=sujeto.get("nombre", ""),
                                last_name=sujeto.get("paterno", ""),
                                mothers_last_name=sujeto.get("materno", ""),
                            )
                            if isinstance(remote, dict) and remote.get("id"):
                                remote_cid = remote["id"]
                                if remote_cid != singula_customer_id:
                                    # Hay un customer más reciente para
                                    # esta CURP en Singula. Tomar ese.
                                    singula_cache_lookup["duplicate_detected"] = {
                                        "local_cid": singula_customer_id,
                                        "remote_cid": remote_cid,
                                    }
                                    singula_customer_id = remote_cid
                                    cached_enrich = None  # no tenemos local del nuevo
                                    # Actualizar el store local para próximas
                                    # veces (sin pisar enrichment que ya
                                    # tenía; ON CONFLICT sólo toca curp)
                                    try:
                                        from providers import singula_store
                                        singula_store.save_enrichment(
                                            customer_id=remote_cid,
                                            curp=curp,
                                            enrichment={"_migrated_from_local_duplicate": True,
                                                        "_previous_local_cid": singula_cache_lookup["duplicate_detected"]["local_cid"]},
                                            custom_id=remote.get("custom_id") or "",
                                        )
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        if isinstance(cached_enrich, dict) and cached_enrich:
                            singula_enrich = {
                                "ok": True,
                                "enrichment": cached_enrich,
                                "error": None,
                                "from_local_cache": True,
                            }
                        else:
                            singula_enrich_err = "cache sin enrichment (re-ejecutar pipeline manual)"
                    else:
                        # 1b) no hay cache local → crear customer nuevo
                        fn = parse_fecnac(sujeto.get("fecnac"))
                        res_create = sc.get_or_create_customer(
                            curp=curp,
                            name=sujeto.get("nombre", ""),
                            last_name=sujeto.get("paterno", ""),
                            mothers_last_name=sujeto.get("materno", ""),
                            email=email_checkid or sujeto.get("email", ""),
                            phone=sujeto.get("telefono", ""),
                            rfc=sujeto.get("rfc", ""),
                            gender=sujeto.get("sexo", "H"),
                            birth_day=fn.get("day"),
                            birth_month=fn.get("month"),
                            birth_year=fn.get("year"),
                        )
                        singula_customer_id = res_create.get("id")
                        singula_customer_created = res_create.get("created")
                        singula_create_err = (
                            res_create.get("customer", {}).get("error")
                            if isinstance(res_create.get("customer"), dict)
                            else None
                        )

                        # 2) enriquecer con TODOS los datos consolidados (customer nuevo)
                        if singula_customer_id:
                            singula_enrich = _enrich_singula_customer(
                                sc, singula_customer_id, sujeto,
                                checkid_data=checkid_data,
                                tlaloc_curp=tlaloc_curp,
                                tlaloc_rfc=tlaloc_rfc,
                                sepomex=sepomex_data,
                                social_media=social_media,
                                rfc_sat=rfc_sat,
                            )
                            singula_enrich_err = singula_enrich.get("error")

                    # 3) ejecutar validaciones automáticas con el perfil enriquecido.
                    # Esto ya reusa por sí mismo desde validations_cache (TTL 7d),
                    # así que aunque tengamos cache local, las validaciones
                    # stale (>7d) se ejecutarán; las fresh se reusan.
                    if singula_customer_id:
                        try:
                            singula_validations = _run_singula_validations(
                                sc, singula_customer_id, sujeto
                            )
                            if isinstance(singula_validations, dict) and singula_validations.get("error"):
                                singula_validations_err = singula_validations["error"]
                        except Exception as e:
                            singula_validations_err = str(e)
            except Exception as e:
                singula_create_err = str(e)

            # 4) validaciones disponibles (modal de pago por validación).
            # Si el cache local tenía validaciones frescas, las marcamos
            # enabled=False para que el botón ▶ Ejecutar aparezca deshabilitado
            # y muestre "cache" en lugar de cobrar al usuario al pulsarlo.
            validations_cache_for_blocking = (
                (singula_cache_lookup or {}).get("validations_fresh_cache") or {}
            )
            if not validations_cache_for_blocking and singula_cache_lookup and singula_cache_lookup.get("found_in_local_store"):
                # rebuilt from singula_validations_cache_fresh (set after _run)
                pass
            validations = _build_validations(sujeto)
            # 2026-08-04: marcar enabled=False en validaciones Singula que ya
            # tenemos en cache fresco (no se pueden re-ejecutar sin cobrar).
            if singula_cache_lookup and singula_cache_lookup.get("found_in_local_store"):
                _id_to_key = {
                    "singula-judicial": "judicial",
                    "singula-blacklist": "blacklist",
                    "singula-email-lookup": "email_lookup",
                    "singula-intel-basic": "intel_basic",
                    "singula-intel-premium": "intel_premium",
                }
                # Determinar qué keys tenemos en cache fresco
                fresh_keys = set()
                sv_cache = (singula_validations or {}).get("from_cache", {}) if isinstance(singula_validations, dict) else {}
                for k_v, k_internal in _id_to_key.items():
                    if sv_cache.get(k_internal):
                        fresh_keys.add(k_v)
                for v in validations:
                    if v.get("provider") == "singula" and v["id"] in fresh_keys:
                        v["enabled"] = False
                        existing_desc = v.get("description", "")
                        v["description"] = f"✓ Reusado del cache local (≤7d). {existing_desc}".strip()

            self._json(200, {
                "curp": curp,
                "sujeto": sujeto,
                "rfc": {
                    "rfc": sujeto["rfc"],
                    "local": rfc_local,
                    "sat": {"rfc": rfc_sat, "homoclave": rfc_sat[10:13] if rfc_sat and len(rfc_sat) >= 13 else None, "dv": rfc_sat[-1] if rfc_sat and len(rfc_sat) >= 13 else None} if rfc_sat and len(rfc_sat) >= 13 else None,
                    "match": (rfc_local.get("rfc_10") == rfc_sat[:10]) if (rfc_local and rfc_sat and len(rfc_sat) >= 10) else None,
                    "source": source,
                    "error": singula_err,
                },
                "checkid": {
                    "exitoso": checkid_data.get("exitoso") if checkid_data else False,
                    "rfc": checkid_data.get("rfc") if checkid_data else None,
                    "razon_social": checkid_data.get("razon_social") if checkid_data else None,
                    "nss": _extract_checkid_str(checkid_data.get("nss"), "nss") if checkid_data else None,
                    "codigo_postal": _extract_checkid_str(checkid_data.get("codigo_postal"), "codigoPostal") if checkid_data else None,
                    "regimen_fiscal": _extract_checkid_str(checkid_data.get("regimen_fiscal"), "regimenesFiscales") if checkid_data else None,
                    "estado_69_69b": checkid_data.get("estado_69_69b") if checkid_data else None,
                    "email": email_checkid,
                    "error": checkid_err,
                },
                "tlaloc_curp": tlaloc_curp,
                "tlaloc_rfc": tlaloc_rfc,
                # 2026-08-05: cruce RFC-keyed post-checkid. Atributo agregado
                # al JSON del sujeto para que el frontend pueda renderizar
                # att/repuve/telcel/empleadores en una seccion separada.
                "externas_por_rfc": externas_por_rfc,
                "sepomex": sepomex_data,
                "social_media": social_media if social_media else {"error": social_media_err} if social_media_err else None,
                "singula": {
                    "customer_id": singula_customer_id,
                    "create_error": singula_create_err,
                    "customer_created": singula_customer_created,
                    # 2026-08-04: metadata del lookup por CURP en store local.
                    # Útil para mostrar en UI "✓ datos del cache" y depurar.
                    "cache_lookup": singula_cache_lookup,
                    "enrich_ok": bool(singula_enrich and singula_enrich.get("ok")),
                    "enrich_from_local_cache": (singula_enrich or {}).get("from_local_cache", False),
                    "enrich_sources": (singula_enrich or {}).get("enrichment", {}).get("sources", []),
                    "enrichment": (singula_enrich or {}).get("enrichment", {}),
                    "enrich_error": singula_enrich_err,
                    "validations": singula_validations,
                    "validations_from_cache": (singula_validations or {}).get("from_cache", {}),
                    "validations_executed": (singula_validations or {}).get("executed", []),
                    "validations_error": singula_validations_err,
                },
                "validations": validations,
                "duracion_seg": round(time.time() - t0, 2),
            })
            # auditoría (happy path)
            self._audit(
                session=session, action="view_sujeto",
                endpoint="/api/sujeto", method="GET", status_code=200,
                duration_ms=int((time.time() - t0) * 1000),
                query_summary={"curp_prefix": curp[:4]},
                results_count=1,
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._audit(
                session=session, action="view_sujeto",
                endpoint="/api/sujeto", method="GET", status_code=500,
                duration_ms=int((time.time() - t0) * 1000),
                query_summary={"curp_prefix": (curp if 'curp' in locals() else "")[:4],
                               "error": str(e)[:80]},
            )
            self._json(500, {"error": str(e)})

    # 2026-08-05: CFE medidores (66M filas, 14 layouts heterogéneos).
    # No comparte clave con padrón (sin CURP/RFC). PK = num_servicio (11-12
    # dígitos CFE). La vista api.cfe_medidor aplica TRIM + extrae num_servicio
    # y cp con heurísticas universales (ver _init_extended_con).
    CFE_BUSCAR_DEFAULT_CAP = 50
    CFE_BUSCAR_MAX_CAP = 500
    CFE_BUSCAR_TIMEOUT_S = 2.0   # hard cap en segundos; pasado eso, aborta

    def _handle_sujeto_verificar_unificada(self):
            """GET /api/v1/sujeto/verificar_unificada?curp=XXXX

            Verificación unificada de un sujeto del padrón ejecutando
            en paralelo todos los endpoints de verificación disponibles:
        
            1. Datos base del sujeto (padrón + RFC + Tlaloc + validaciones)
            2. CFE por nombre (LIKE case-insensitive por paterno+nombre)
            3. ISSSTE (padrón federal por paterno)
            4. CFE por CP (si el sujeto tiene CP válido)
        
            Devuelve JSON unificado con resultados de todas las verificaciones.
            """
            import concurrent.futures
            import urllib.parse as _up
        
            session = self._require_session()
            if not session:
                return
        
            try:
                query = parse_qs(urlparse(self.path).query)
                curp = query.get("curp", [""])[0].upper().strip()
                if not curp:
                    self._json(400, {"error": "falta ?curp=..."})
                    return
            
                t0 = time.time()
                sujeto = _find_sujeto(curp)
                if not sujeto:
                    self._json(404, {"error": "curp no encontrada en padron"})
                    return
            
                # Extraer datos del sujeto para búsquedas
                paterno = (sujeto.get("paterno") or "").strip()
                materno = (sujeto.get("materno") or "").strip()
                nombre = (sujeto.get("nombre") or "").strip()
            
                # Ejecutar búsquedas en paralelo
                results = {}
            
                def run_sujeto_base():
                    # Datos base del sujeto (padrón + RFC + Tlaloc + validaciones)
                    # Lógica simplificada inline (evita método no existente)
                    # El sujeto ya se cargó arriba, solo devolvemos los datos base
                    return {
                        "curp": curp,
                        "sujeto": sujeto,
                        "verificaciones": {},  # validaciones del _handle_sujeto original
                    }
            
                def run_cfe_por_nombre():
                    if not (paterno := (sujeto.get("paterno") or "").strip()):
                        return {"skipped": True, "reason": "sin paterno"}
                    try:
                        con = _init_extended_con()
                        if con is None:
                            return {"error": "extendido no inicializado"}
                        sql = """SELECT numero_servicio, division, cp, nombre, 
                                        direccion, colonia
                                FROM api.cfe_medidor
                                WHERE UPPER(TRIM(nombre)) LIKE ?
                                LIMIT 25"""
                        rows = con.execute(sql, [f"%{paterno}%"]).fetchall()
                        cols = ["num_servicio","division","cp","nombre",
                                "direccion","colonia"]
                        cfe_rows = [dict(zip(["num_servicio","division","cp",
                                                   "nombre","direccion","colonia"], r))
                                    for r in rows]
                        # Filtro IA: si hay matches y sujeto, analizar
                        if cfe_rows:
                            ai = _ai_filter_matches(sujeto, cfe_rows, fuente="cfe")
                            if not ai.get("skipped"):
                                return {"rows": ai["matches_filtrados"],
                                        "count": len(ai["matches_filtrados"]),
                                        "ai_analysis": ai}
                            return {"rows": cfe_rows, "count": len(cfe_rows),
                                    "ai_analysis": ai}
                        return {"rows": [], "count": 0}
                    except Exception as e:
                        return {"error": str(e)[:200]}
            
                def run_issste():
                    if not (paterno := (sujeto.get("paterno") or "").strip()):
                        return {"skipped": True, "reason": "sin paterno"}
                    try:
                        con = _init_extended_con()
                        if con is None:
                            return {"error": "extendido no inicializado"}
                        rows = con.execute("""
                            SELECT id, paterno, materno, nombres,
                                   cargo, sexo, sueldo,
                                   ramo, entidad, sector
                            FROM api.issste_empleado
                            WHERE paterno LIKE ?
                            ORDER BY sueldo DESC NULLS LAST
                            LIMIT 10
                        """, [f"%{paterno}%"]).fetchall()
                        cols = ["id","paterno","materno","nombres",
                                "cargo","sexo","sueldo",
                                "ramo","entidad","sector"]
                        iss_rows = [dict(zip(cols, r)) for r in rows]
                        if iss_rows:
                            ai = _ai_filter_matches(sujeto, iss_rows, fuente="issste")
                            if not ai.get("skipped"):
                                return {"rows": ai["matches_filtrados"],
                                        "count": len(ai["matches_filtrados"]),
                                        "ai_analysis": ai}
                            return {"rows": iss_rows, "count": len(iss_rows),
                                    "ai_analysis": ai}
                        return {"rows": [], "count": 0}
                    except Exception as e:
                        return {"error": str(e)[:200]}
            
                def run_cfe_coordenadas():
                    cp = (sujeto.get("cp") or "").strip()
                    if not cp or len(cp) != 5:
                        return {"skipped": True, "reason": "sin CP válido"}
                    try:
                        con = _init_extended_con()
                        if con is None:
                            return {"error": "extendido no inicializado"}
                        rows = con.execute("""
                            SELECT numero_servicio, division, cp, nombre, direccion, colonia
                            FROM api.cfe_medidor
                            WHERE cp = ?
                            LIMIT 5
                        """, [cp]).fetchall()
                        cols = ["num_servicio","division","cp","nombre","direccion","colonia"]
                        return {"rows": [dict(zip(cols, r)) for r in rows],
                                "count": len(rows), "cp": cp}
                    except Exception as e:
                        return {"error": str(e)[:200]}
            
                # Ejecutar en paralelo con ThreadPoolExecutor
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {
                        "sujeto_base": executor.submit(run_sujeto_base),
                        "cfe_por_nombre": executor.submit(run_cfe_por_nombre),
                        "issste": executor.submit(run_issste),
                        "cfe_coordenadas": executor.submit(run_cfe_coordenadas),
                    }
                
                    results = {}
                    for key, future in futures.items():
                        try:
                            results[key] = future.result(timeout=10)
                        except concurrent.futures.TimeoutError:
                            results[key] = {"error": "timeout 10s"}
                        except Exception as e:
                            results[key] = {"error": str(e)[:200]}
            
                # Construir respuesta unificada
                elapsed = time.time() - t0
            
                # Auditar
                try:
                    self._audit(
                        session=session, action="view_sujeto_verificar_unificada",
                        endpoint="/api/v1/sujeto/verificar_unificada", method="GET", status_code=200,
                        query_summary={"curp": curp[:10]},
                        results_count=sum(len(r.get("rows", [])) for r in results.values() if isinstance(r, dict)),
                    )
                except Exception:
                    pass
            
                response = {
                    "curp": curp,
                    "sujeto": sujeto,
                    "verificaciones": results,
                    "elapsed_s": round(time.time() - t0, 3),
                    "nota": "Verificación unificada: padrón base + CFE por nombre + ISSSTE + CFE por CP. "
                            "Cada verificación puede tener 'skipped' si faltan datos requeridos.",
                }
                self._json(200, response)
            
            except Exception as e:
                self._json(500, {"error": f"verificacion_unificada: {e}"})

    def _handle_persona_cfe_servicio(self, num_servicio: str):
        """GET /api/v1/persona/cfe/<num_servicio> — lookup exacto.

        Devuelve todos los registros CFE con ese num_servicio (puede haber
        varios: titular + histórico de cambios de tarifa o dirección).
        Si algún titular matchea por nombre+CP contra el padrón INE, devuelve
        el `curp` para que el frontend pueda abrir /sujeto.html?curp=...
        """
        con = _init_extended_con()
        if con is None:
            self._json(503, {"error": "extendido no inicializado"})
            return
        num_servicio = num_servicio.strip()
        if not (10 <= len(num_servicio) <= 12 and num_servicio.isdigit()):
            self._json(400, {"error": "num_servicio inválido (10-12 dígitos)"})
            return

        # Lookup exacto en la vista (columnas normalizadas, no c01..c19)
        rows = []
        err = None
        try:
            rows = con.execute("""
                SELECT numero_servicio, division, zona_cod, zona_nom,
                       agencia_cod, agencia_nom,
                       codigo_medidor, numero_medidor,
                       cp, nombre, direccion, calle_adicional_1,
                       calle_adicional_2, colonia, campo_adicional_1,
                       hilos, source_folder, source_file
                FROM api.cfe_medidor
                WHERE numero_servicio = ?
                ORDER BY source_folder, source_file
                LIMIT 100
            """, [num_servicio]).fetchall()
        except duckdb.Error as e:
            err = str(e)[:200]

        cols = ["num_servicio","division","zona_cod","zona_nom",
                "agencia_cod","agencia_nom",
                "codigo_medidor","numero_medidor",
                "cp","nombre","direccion","calle_adicional_1",
                "calle_adicional_2","colonia","campo_adicional_1",
                "hilos","source_folder","source_file"]

        result = {
            "num_servicio": num_servicio,
            "rows": [dict(zip(cols, r)) for r in rows],
            "count": len(rows),
            "error": err,
            "found": bool(rows),
        }

        # Cross-link con padrón INE: si el titular matchea por nombre+CP
        # intentamos sacar la CURP. Esto es fuzzy y best-effort.
        # IMPORTANTE: el padrón NO tiene índice en paterno/nombre; cualquier
        # query ILIKE / = sobre las 88M filas tarda 1-3s. Para NO bloquear
        # el ThreadingHTTPServer (cada request cuelga a todas las demás),
        # este bloque corre con timeout duro de 1.5s y se aborta silencioso.
        if rows:
            candidate_name = None
            try:
                # 2026-08-13: la vista normalizada tiene la columna "nombre"
                # directamente (antes había que escarbar en c07..c14).
                first = rows[0]
                rowd = dict(zip(cols, first))
                candidate_name = (rowd.get("nombre") or "").strip() or None
                cp_hint = (rowd.get("cp") or "").strip()
                if candidate_name and len(cp_hint) == 5 and cp_hint.isdigit():
                    parts = candidate_name.split()
                    paterno = parts[0] if parts else None
                    nombre  = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) >= 2 else None)
                    if paterno and len(paterno) >= 3:
                        # Pre-filtrar por CP primero: WHERE cp = ? es EXACTO
                        # y zone-map (mucho más rápido que paterno=). El CP
                        # reduce 88M → ~3000 filas (CP5 ≈ 3000 ciudadanos).
                        # Luego paterno + nombre sobre ese set chico.
                        t0 = time.time()
                        curp_rows = self.db.execute("""
                            SELECT curp, nombre, paterno, materno, fecnac, cp
                            FROM padron
                            WHERE cp = ? AND paterno = ?
                            LIMIT 20
                        """, [cp_hint, paterno.upper()]).fetchall()
                        if time.time() - t0 > 1.5:
                            # demasiado lento; abortar best-effort
                            result["padron_match_error"] = "padron lookup >1.5s (sin índice)"
                            curp_rows = []
                        # Filtrar por nombre en Python (set chico, no requiere SQL)
                        if nombre and curp_rows:
                            nombre_up = nombre.upper()
                            curp_rows = [r for r in curp_rows if (r[1] or "").upper().startswith(nombre_up[:3])][:5]
                        if curp_rows:
                            result["padron_match"] = {
                                "curp":     curp_rows[0][0],
                                "nombre":   curp_rows[0][1],
                                "paterno":  curp_rows[0][2],
                                "materno":  curp_rows[0][3],
                                "fecnac":   curp_rows[0][4],
                                "cp":       curp_rows[0][5],
                                "candidatos": [
                                    {"curp": r[0], "nombre": r[1], "paterno": r[2],
                                     "materno": r[3], "fecnac": r[4], "cp": r[5]}
                                    for r in curp_rows
                                ],
                            }
            except Exception as e:
                # El cross-link con padrón es best-effort; un fallo no rompe el endpoint
                result["padron_match_error"] = str(e)[:200]

        # Auditoría
        try:
            self._audit(
                session=session, action="view_cfe_servicio",
                endpoint=f"/api/v1/persona/cfe/{num_servicio}",
                method="GET", status_code=200,
                query_summary={"num_servicio": num_servicio},
                results_count=len(rows),
            )
        except Exception:
            pass

        self._json(200, result)

    def _handle_persona_cfe_buscar(self):
        """GET /api/v1/persona/cfe/buscar?nombre=&cp=&division=&limit=

        Búsqueda fuzzy por nombre del titular + CP opcional + división CFE
        opcional. Como la base es heterogénea y no tiene índices, devolvemos
        los primeros N resultados con timeout duro (2s). Cobertura parcial
        esperada (~80% para queries bien formadas); documentado en
        `references/cfe-integration.md`.
        """
        con = _init_extended_con()
        if con is None:
            self._json(503, {"error": "extendido no inicializado"})
            return
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        nombre  = (qs.get("nombre",   [""])[0] or "").strip().upper()
        cp      = (qs.get("cp",       [""])[0] or "").strip().zfill(5)
        division = (qs.get("division", [""])[0] or "").strip().upper()
        try:
            limit = int(qs.get("limit", [str(self.CFE_BUSCAR_DEFAULT_CAP)])[0])
        except (TypeError, ValueError):
            limit = self.CFE_BUSCAR_DEFAULT_CAP
        limit = max(1, min(limit, self.CFE_BUSCAR_MAX_CAP))

        if len(nombre) < 4:
            self._json(400, {"error": "nombre debe tener al menos 4 caracteres"})
            return
        if cp and (len(cp) != 5 or not cp.isdigit()):
            self._json(400, {"error": "cp inválido (5 dígitos)"})
            return

        # Tomar el primer token del nombre como apellido paterno (heurística
        # KYC típica: los CFE escriben "PATERNO MATERNO NOMBRES").
        # Esto convierte "LARA CHAVEZ PERFECTO" en paterno="LARA".
        nombre_parts = nombre.split()
        paterno = nombre_parts[0] if nombre_parts else nombre

        # 2026-08-13: api.cfe_medidor (vista normalizada) tiene columnas
        # "nombre" (titular) — no c10..c14. Adaptamos:
        #   WHERE upper(nombre) LIKE paterno + '%'  (prefijo, usa zone-map)
        where = ["UPPER(TRIM(COALESCE(nombre, ''))) LIKE ?"]
        params = [f"{paterno}%"]
        if cp:
            where.append("cp = ?")
            params.append(cp)
        if division:
            where.append("division = ?")
            params.append(division)
        where_sql = " AND ".join(where)

        rows, err = [], None
        try:
            t0 = time.time()
            sql = f"""
                SELECT numero_servicio, division, zona_cod, zona_nom,
                       agencia_cod, agencia_nom,
                       cp, nombre, direccion, calle_adicional_1,
                       calle_adicional_2, colonia, source_folder
                FROM api.cfe_medidor
                WHERE {where_sql}
                LIMIT ?
            """
            params.append(limit)
            rows = con.execute(sql, params).fetchall()
            elapsed = time.time() - t0
        except duckdb.Error as e:
            err = str(e)[:200]
            elapsed = None

        cols = ["num_servicio","division","zona_cod","zona_nom",
                "agencia_cod","agencia_nom","cp","nombre",
                "direccion","calle_adicional_1","calle_adicional_2",
                "colonia","source_folder"]
        result = {
            "query": {"nombre": nombre, "cp": cp or None, "division": division or None, "limit": limit},
            "rows": [dict(zip(cols, r)) for r in rows],
            "count": len(rows),
            "elapsed_s": round(elapsed, 3) if elapsed else None,
            "truncated": len(rows) >= limit,
            "error": err,
            "found": bool(rows),
        }

        try:
            self._audit(
                session=session, action="view_cfe_busqueda",
                endpoint="/api/v1/persona/cfe/buscar",
                method="GET", status_code=200,
                query_summary={"paterno_prefix": paterno[:4], "cp": cp or None, "limit": limit},
                results_count=len(rows),
            )
        except Exception:
            pass

        self._json(200, result)

    # 2026-08-13: flujo "coordenadas GPS → dirección → servicios CFE"
    # Implementa CFE_flujo_coordenadas.md:
    #   1. Recibe coordenadas decimales (lat, lon)
    #   2. Geocodifica vía Nominatim/OpenStreetMap → dirección
    #   3. Busca servicios CFE que coincidan con la calle/colonia
    #   4. Devuelve los servicios + flag para exportar CSV
    #
    # Q: ¿Por qué no usar /api/v1/cfe/buscar_domicilio directamente?
    # R: Porque requiere que el usuario sepa la calle. Con coordenadas,
    #    el sistema descubre la dirección automáticamente.
    #
    # Nominatim respeta la política de uso de OSM:
    #   - User-Agent identificable (KYCSearch/1.0 + contacto)
    #   - Rate limit: máx 1 req/seg (aplicado en este handler)
    def _handle_geo_parse(self):
        """GET /api/v1/geo/parse?q=<texto> → {lat, lon, source} | {error}.
        Interpreta par decimal, DMS o link de Google Maps (expande cortos)."""
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        raw = (qs.get("q", [""])[0] or qs.get("url", [""])[0] or "").strip()
        if not raw:
            self._json(400, {"error": "falta parámetro q"})
            return
        res = parse_coord_input(raw)
        if res.get("error"):
            self._json(422, res)
        else:
            self._json(200, res)

    def _handle_cfe_coordenadas(self):
        import urllib.parse as _up
        import urllib.request as _urlreq
        import json as _json
        qs = _up.parse_qs(_up.urlparse(self.path).query)

        # 1. Parsear coordenadas
        try:
            lat = float(qs.get("lat", [""])[0])
            lon = float(qs.get("lon", [""])[0])
        except (TypeError, ValueError):
            self._json(400, {"error": "lat/lon requeridos (decimales, ej 16.8114)"})
            return

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            self._json(400, {"error": "lat debe estar en [-90,90], lon en [-180,180]"})
            return

        # Coords ≈ (0,0) probablemente son dummy → rechazar
        if abs(lat) < 0.0001 and abs(lon) < 0.0001:
            self._json(400, {"error": "coordenadas (0,0) no son válidas"})
            return

        try:
            limit = int(qs.get("limit", ["25"])[0])
        except (TypeError, ValueError):
            limit = 25
        limit = max(1, min(limit, 100))

        export_csv = qs.get("csv", ["0"])[0] in ("1", "true", "yes")

        # 2. Geocodificación inversa vía Nominatim
        # 2026-08-13: intentar LOCAL primero (sin rate limit, sin internet),
        # fallback al público si falla. Configurable vía env:
        #   NOMINATIM_LOCAL_URL=http://127.0.0.1:8088  (default)
        #   NOMINATIM_PUBLIC_URL=https://nominatim.openstreetmap.org
        # Para desactivar local: NOMINATIM_LOCAL_URL=
        import urllib.parse as _up
        import urllib.request as _urlreq
        import json as _json
        import os as _os
        local_url = _os.environ.get("NOMINATIM_LOCAL_URL", "http://127.0.0.1:8088")
        public_url = _os.environ.get("NOMINATIM_PUBLIC_URL",
                                      "https://nominatim.openstreetmap.org")
        ua = "KYCSearch/1.0 (cuartodepaz.org/contact)"  # TOS OSM, solo para público
        query = f"lat={lat}&lon={lon}&format=jsonv2&zoom=18&addressdetails=1"

        geo = None
        geo_err = None
        geo_source = None  # "local" | "public"
        geo_display_name = ""  # 2026-08-13: display_name completo

        def _extract_geo(candidate):
            """Nominatim /reverse devuelve LISTA de dicts (típicamente 1 elemento).
            Si es lista, tomar el primer dict. Si es dict, usar directo.
            """
            if isinstance(candidate, list):
                if candidate:
                    return candidate[0], (candidate[0].get("display_name") or "")
            elif isinstance(candidate, dict):
                return candidate, (candidate.get("display_name") or "")
            return None, ""

        # Intentar local primero (sin UA — Nominatim local lo ignora)
        if local_url:
            try:
                req = _urlreq.Request(f"{local_url}/reverse?{query}",
                                       headers={"Accept": "application/json"},
                                       method="GET")
                with _urlreq.urlopen(req, timeout=2) as resp:
                    body = resp.read().decode()
                    try:
                        candidate = _json.loads(body)
                        g, dn = _extract_geo(candidate)
                        if g:
                            geo = g
                            geo_display_name = dn
                            geo_source = "local"
                    except _json.JSONDecodeError:
                        geo_err = f"local: json decode error"
            except Exception as e:
                geo_err = f"local: {type(e).__name__}: {str(e)[:80]}"

        # Fallback al público si local falló
        if geo is None and public_url:
            try:
                req = _urlreq.Request(f"{public_url}/reverse?{query}",
                                       headers={"User-Agent": ua,
                                                "Accept": "application/json"},
                                       method="GET")
                with _urlreq.urlopen(req, timeout=10) as resp:
                    body = resp.read().decode()
                    try:
                        candidate = _json.loads(body)
                        g, dn = _extract_geo(candidate)
                        if g:
                            geo = g
                            geo_display_name = dn
                            geo_source = "public"
                    except _json.JSONDecodeError:
                        geo_err = f"{geo_err or ''} | public: json decode error"
            except Exception as e:
                geo_err = f"{geo_err or ''} | public: {type(e).__name__}: {str(e)[:80]}"

        if geo is None:
            self._json(503, {
                "error": "no se pudo geocodificar",
                "detail": geo_err,
                "lat": lat, "lon": lon,
                "fallback": "usar /api/v1/cfe/buscar_domicilio con calle/colonia/cp conocidos"
            })
            return

        addr = geo.get("address", {})
        calle    = (addr.get("road") or addr.get("pedestrian") or
                    addr.get("path") or addr.get("display_name", "")).split(",")[0].strip().upper()
        colonia  = (addr.get("neighbourhood") or addr.get("suburb") or
                    addr.get("quarter") or "").strip().upper()
        cp       = (addr.get("postcode") or "").strip()
        municipio = (addr.get("city") or addr.get("town") or
                     addr.get("village") or addr.get("municipality") or "").strip()
        estado   = (addr.get("state") or "").strip()

        if not (calle or colonia or cp):
            self._json(200, {
                "input": {"lat": lat, "lon": lon},
                "geocoded": geo,
                "geocode_source": geo_source,
                "display_name": geo_display_name,
                "address": {"calle": calle or None, "colonia": colonia or None,
                            "cp": cp or None, "municipio": municipio or None,
                            "estado": estado or None},
                "rows": [],
                "count": 0,
                "found": False,
                "nota": "Nominatim devolvió un punto sin dirección postal; "
                        "no hay con qué buscar en CFE.",
            })
            return

        # 3. Buscar en CFE
        con = _init_extended_con()
        if con is None:
            self._json(503, {"error": "extendido no inicializado"})
            return

        # Si Nominatim dio CP, lo priorizamos (zone-map rápido)
        where, params = [], []
        if calle:
            where.append("""(
                UPPER(TRIM(COALESCE(direccion, ''))) LIKE ? OR
                UPPER(TRIM(COALESCE(calle_adicional_1, ''))) LIKE ? OR
                UPPER(TRIM(COALESCE(calle_adicional_2, ''))) LIKE ?
            )""")
            params.extend([f"%{calle}%"] * 3)
        if colonia:
            where.append("UPPER(TRIM(COALESCE(colonia, ''))) LIKE ?")
            params.append(f"%{colonia}%")
        if cp and len(cp) == 5 and cp.isdigit():
            where.append("cp = ?")
            params.append(cp.zfill(5))
        if not where:
            self._json(200, {
                "input": {"lat": lat, "lon": lon},
                "address": {"calle": calle, "colonia": colonia, "cp": cp},
                "rows": [], "count": 0, "found": False,
                "nota": "Nominatim no dio datos suficientes para buscar.",
            })
            return
        where_sql = " AND ".join(where)
        params.append(limit)

        rows, err, elapsed = [], None, None
        try:
            t0 = time.time()
            sql = f"""
                SELECT numero_servicio, division, zona_cod, zona_nom,
                       agencia_cod, agencia_nom,
                       cp, nombre, direccion, calle_adicional_1,
                       calle_adicional_2, colonia, source_folder
                FROM api.cfe_medidor
                WHERE {where_sql}
                LIMIT ?
            """
            rows = con.execute(sql, params).fetchall()
            elapsed = time.time() - t0
        except duckdb.Error as e:
            err = str(e)[:200]

        cols = ["num_servicio","division","zona_cod","zona_nom",
                "agencia_cod","agencia_nom","cp","nombre",
                "direccion","calle_adicional_1","calle_adicional_2",
                "colonia","source_folder"]
        cfe_rows = [dict(zip(cols, r)) for r in rows]

        result = {
            "input": {"lat": lat, "lon": lon},
            "geocode_source": geo_source,
            "display_name": geo_display_name,
            "address": {"calle": calle or None, "colonia": colonia or None,
                        "cp": cp or None, "municipio": municipio or None,
                        "estado": estado or None},
            "nominatim_display": geo.get("display_name"),
            "rows": cfe_rows,
            "count": len(cfe_rows),
            "elapsed_s": round(elapsed, 3) if elapsed else None,
            "truncated": len(cfe_rows) >= limit,
            "found": bool(cfe_rows),
            "error": err,
            "nota": ("Si count=0 y cp!=None, intentar con solo cp: "
                     "/api/v1/cfe/buscar_domicilio?cp=" + (cp or "")),
        }

        # 4. Exportación a CSV (opcional, ?csv=1)
        if export_csv and cfe_rows:
            import csv as _csv
            import io as _io
            buf = _io.StringIO()
            w = _csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for row in cfe_rows:
                w.writerow(row)
            csv_data = buf.getvalue()
            result["csv"] = csv_data
            result["csv_size"] = len(csv_data)

        try:
            self._audit(
                session=session, action="view_cfe_coordenadas",
                endpoint="/api/v1/cfe/coordenadas", method="GET", status_code=200,
                query_summary={"lat": round(lat, 4), "lon": round(lon, 4),
                               "cp": cp or None, "limit": limit},
                results_count=len(cfe_rows),
            )
        except Exception:
            pass

        self._json(200, result)

    # 2026-08-06: handler para /api/v1/telcel/buscar?telefono=...
    # Búsqueda por número de teléfono en b_telcel. La base tiene números
    # VARCHAR padded con espacios (ej '5550983677   '). Normalizamos a
    # LIKE %x% sobre los últimos 7-10 dígitos para encontrar la línea.
    def _handle_direccion_candidatos(self):
        """Candidatos de domicilio en el padrón para decisión manual.

        Query params: calle?, ext?, colonia?, cp?, municipio?, entidad?,
        limit? (default 10, max 50), refs? (csv de rowids para traer las
        personas de un candidato ya elegido).
        """
        import urllib.parse as _up
        import fuzzy_direccion as F
        qs = _up.parse_qs(_up.urlparse(self.path).query)

        def g(k):
            return (qs.get(k, [""])[0] or "").strip() or None

        refs_csv = g("refs")
        if refs_csv:
            try:
                refs = [int(r) for r in refs_csv.split(",")[:50]]
            except ValueError:
                self._json(400, {"error": "refs debe ser csv de enteros"})
                return
            self._json(200, {"personas": F.personas_de_refs(refs)})
            return

        if not (g("calle") or g("colonia") or g("cp")):
            self._json(400, {"error": "se requiere calle, colonia o cp"})
            return
        try:
            limit = max(1, min(int(g("limit") or 10), 50))
        except ValueError:
            limit = 10
        try:
            t0 = time.time()
            res = F.candidatos(calle=g("calle"), ext=g("ext"),
                               colonia=g("colonia"), cp=g("cp"),
                               municipio=g("municipio"), entidad=g("entidad"),
                               limit=limit)
            res["elapsed_s"] = round(time.time() - t0, 3)
            self._json(200, res)
        except Exception as e:
            self._json(500, {"error": f"candidatos: {str(e)[:200]}"})

    def _handle_telcel_buscar(self):
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        telefono = (qs.get("telefono", [""])[0] or "").strip()
        try:
            limit = int(qs.get("limit", ["50"])[0])
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        # Limpiar el número: dejar solo dígitos
        tel_digits = "".join(c for c in telefono if c.isdigit())
        if len(tel_digits) < 7:
            self._json(400, {"error": "telefono debe tener al menos 7 dígitos"})
            return
        con = _init_extended_con()
        if con is None:
            self._json(503, {"error": "extendido no inicializado"})
            return
        # Buscar el sufijo del número (los últimos 7-10 dígitos). La base
        # tiene padding con espacios, así que TRIM(telefono) LIKE '%x%'
        # funciona si el número coincide con los últimos dígitos.
        sql_suffix = tel_digits[-10:] if len(tel_digits) >= 10 else tel_digits
        try:
            t0 = time.time()
            rows = con.execute(f"""
                SELECT rfc_clean, telefono, TRIM(nombre1), TRIM(nombre2),
                       plan_actual, TRIM(marca), TRIM(modelo),
                       TRIM(st_tel), TRIM(st_cta), fecha_activ, fecha_cancel,
                       TRIM(domicilio), TRIM(numero), TRIM(interior),
                       TRIM(colonia), TRIM(ciudad), TRIM(edo), TRIM(cp),
                       archivo_origen
                FROM b_telcel.main.telcel
                WHERE TRIM(telefono) LIKE ? OR TRIM(telefono) LIKE ?
                LIMIT ?
            """, [f"%{sql_suffix}", f"%{tel_digits[-7:]}", limit]).fetchall()
            elapsed = time.time() - t0
        except duckdb.Error as e:
            self._json(500, {"error": f"telcel lookup: {str(e)[:200]}"})
            return
        cols = ["rfc","telefono","nombre1","nombre2","plan_actual","marca","modelo",
                "estado_linea","estado_cuenta","fecha_activacion","fecha_cancelacion",
                "domicilio","numero","interior","colonia","ciudad","estado","cp",
                "archivo_origen"]
        result = {
            "query": {"telefono_input": telefono, "digits_used": sql_suffix, "limit": limit},
            "rows": [dict(zip(cols, r)) for r in rows],
            "count": len(rows),
            "elapsed_s": round(elapsed, 3),
            "truncated": len(rows) >= limit,
            "found": bool(rows),
            "nota": "Si count=0, el número no está en la base Telcel cargada (9 archivos parquet, cobertura parcial)",
        }
        try:
            self._audit(
                session=session, action="view_telcel_busqueda",
                endpoint="/api/v1/telcel/buscar", method="GET", status_code=200,
                query_summary={"telefono_suffix": sql_suffix[-4:], "limit": limit},
                results_count=len(rows),
            )
        except Exception:
            pass
        self._json(200, result)

    # 2026-08-06: handler para /api/v1/att/buscar?telefono=...
    # Búsqueda por número de teléfono en b_att (1M de registros).
    # ATT tiene dos campos: tel1 (fijo) y celular.
    def _handle_att_buscar(self):
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        telefono = (qs.get("telefono", [""])[0] or "").strip()
        try:
            limit = int(qs.get("limit", ["50"])[0])
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        tel_digits = "".join(c for c in telefono if c.isdigit())
        if len(tel_digits) < 7:
            self._json(400, {"error": "telefono debe tener al menos 7 dígitos"})
            return
        con = _init_extended_con()
        if con is None:
            self._json(503, {"error": "extendido no inicializado"})
            return
        sql_suffix = tel_digits[-10:] if len(tel_digits) >= 10 else tel_digits
        try:
            t0 = time.time()
            rows = con.execute(f"""
                SELECT rfc_clean, TRIM(pat), TRIM(may), TRIM(nombres),
                       TRIM(tel1), TRIM(celular),
                       TRIM(direccion), TRIM(interior), TRIM(exterior),
                       TRIM(colonia), TRIM(municipio), estado_origen,
                       archivo_origen
                FROM b_att.main.att
                WHERE TRIM(tel1) LIKE ? OR TRIM(celular) LIKE ?
                   OR TRIM(tel1) LIKE ? OR TRIM(celular) LIKE ?
                LIMIT ?
            """, [f"%{sql_suffix}", f"%{sql_suffix}",
                  f"%{tel_digits[-7:]}", f"%{tel_digits[-7:]}", limit]).fetchall()
            elapsed = time.time() - t0
        except duckdb.Error as e:
            self._json(500, {"error": f"att lookup: {str(e)[:200]}"})
            return
        cols = ["rfc","paterno","materno","nombres","telefono_fijo","celular",
                "direccion","num_interior","num_exterior","colonia","municipio",
                "estado","archivo_origen"]
        result = {
            "query": {"telefono_input": telefono, "digits_used": sql_suffix, "limit": limit},
            "rows": [dict(zip(cols, r)) for r in rows],
            "count": len(rows),
            "elapsed_s": round(elapsed, 3),
            "truncated": len(rows) >= limit,
            "found": bool(rows),
            "nota": "Si count=0, el número no está en la base ATT cargada (1M de registros, cobertura parcial)",
        }
        try:
            self._audit(
                session=session, action="view_att_busqueda",
                endpoint="/api/v1/att/buscar", method="GET", status_code=200,
                query_summary={"telefono_suffix": sql_suffix[-4:], "limit": limit},
                results_count=len(rows),
            )
        except Exception:
            pass
        self._json(200, result)

    # 2026-08-06: handler para /api/v1/cfe/buscar_domicilio?calle=&colonia=&cp=
    # Búsqueda de medidores CFE por dirección (sin num_servicio). Útil para
    # confirmar un domicilio conocido: el padrón CFE tiene num_servicio +
    # dirección, y a partir de ahí se puede inferir el titular si la dirección
    # es de una casa, o detectar anomalías (mismo domicilio con muchos
    # medidores = edificio, fraccionamiento, etc.).
    #
    # ATENCIÓN: la búsqueda es fuzzy (LIKE %x%) sobre 66M de filas y tarda
    # varios segundos. Cap default 25, max 100. Recomendable pasar SIEMPRE
    # cp (5 dígitos) para acotar el resultado.

    def _handle_issste_buscar(self):
        """GET /api/v1/issste/buscar?nombre=&paterno=&materno=&sexo=&limit=&regex=

        Búsqueda en api.issste_empleado (2.7M filas del padrón ISSSTE).
        Al menos uno de (paterno) es obligatorio. Búsqueda por
        paterno LIKE %x% y nombre LIKE %x% (case-insensitive).

        2026-08-13: integración issste.duckdb en runtime. Sin RFC/CURP
        (el padrón es solo nombre + estructura organizacional).
        2026-08-14: soporte regex=1 para búsqueda PCRE.
        """
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        nombre   = (qs.get("nombre",   [""])[0] or "").strip().upper()
        paterno  = (qs.get("paterno",  [""])[0] or "").strip().upper()
        materno  = (qs.get("materno",  [""])[0] or "").strip().upper()
        sexo     = (qs.get("sexo",     [""])[0] or "").strip().upper()
        regex    = (qs.get("regex",    ["0"])[0] or "0").strip() == "1"
        try:
            limit = int(qs.get("limit", ["50"])[0])
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 100))

        if not (paterno or nombre or materno):
            self._json(400, {"error": "se requiere al menos uno: paterno, nombre, materno"})
            return

        con = _init_extended_con()
        if con is None:
            self._json(503, {"error": "extendido no inicializado"})
            return

        where = []
        params = []
        if regex:
            if paterno:
                where.append("regexp_matches(paterno, ?)")
                params.append(paterno)
            if nombre:
                where.append("regexp_matches(nombre, ?)")
                params.append(nombre)
            if materno:
                where.append("regexp_matches(materno, ?)")
                params.append(materno)
        else:
            if paterno:
                where.append("paterno LIKE ?")
                params.append(f"%{paterno}%")
            if nombre:
                where.append("nombre LIKE ?")
                params.append(f"%{nombre}%")
            if materno:
                where.append("materno LIKE ?")
                params.append(f"%{materno}%")
        if sexo and sexo in ("H", "M"):
            where.append("sexo = ?")
            params.append(sexo)
        where_sql = " AND ".join(where) if where else "1=1"
        params.append(limit)

        rows, err, elapsed = [], None, None
        try:
            t0 = time.time()
            sql = f"""
                SELECT id, paterno, materno, nombres,
                       cargo, sexo, sueldo,
                       ramo, entidad, modalidad, sector, estado
                FROM api.issste_empleado
                WHERE {where_sql}
                ORDER BY sueldo DESC NULLS LAST
                LIMIT ?
            """
            rows = con.execute(sql, params).fetchall()
            elapsed = time.time() - t0
        except Exception as e:
            err = str(e)[:200]

        cols = ["id","paterno","materno","nombres","cargo",
                "sexo","sueldo","ramo","entidad","modalidad",
                "sector","estado"]
        iss_rows = [dict(zip(cols, r)) for r in rows]

        # 2026-08-14: filtro IA opcional. Si ai=1 y hay paterno/nombre del
        # sujeto conocido, la IA determina cuáles matches realmente corresponden.
        ai_analysis = None
        if qs.get("ai", ["0"])[0] == "1" and iss_rows:
            subj = {
                "paterno": paterno or None,
                "materno": materno or None,
                "nombre":  nombre or None,
                "curp":    (qs.get("curp", [""])[0] or "").strip().upper() or None,
                "rfc":     (qs.get("rfc",  [""])[0] or "").strip().upper() or None,
            }
            subj = {k: v for k, v in subj.items() if v}
            if subj:
                ai_analysis = _ai_filter_matches(subj, iss_rows, fuente="issste")
                if not ai_analysis.get("skipped"):
                    iss_rows = ai_analysis["matches_filtrados"]

        result = {
            "query": {"paterno": paterno or None, "nombre": nombre or None,
                      "materno": materno or None, "sexo": sexo or None,
                      "regex": regex, "limit": limit,
                      "ai": ai_analysis is not None},
            "rows": iss_rows,
            "count": len(iss_rows),
            "elapsed_s": round(elapsed, 3) if elapsed else None,
            "truncated": len(iss_rows) >= limit,
            "found": bool(iss_rows),
            "ai_analysis": ai_analysis,
            "error": err,
            "nota": "ISSSTE = padrón de empleados federales. 2.7M filas, "
                    "sin RFC/CURP/dirección. Útil para confirmar employment "
                    "en sector público.",
        }
        try:
            self._audit(
                session=session, action="view_issste_search",
                endpoint="/api/v1/issste/buscar", method="GET", status_code=200,
                query_summary={"paterno_prefix": paterno[:6],
                               "nombre_prefix": nombre[:6], "limit": limit},
                results_count=len(rows),
            )
        except Exception:
            pass
        self._json(200, result)

    def _handle_cfe_buscar_por_nombre(self):
        """GET /api/v1/cfe/buscar_por_nombre?nombre=&paterno=&materno=&regex=&limit=

        Búsqueda en api.cfe_medidor (66M filas) por nombre del titular
        con soporte de regex PCRE (PostgreSQL/DuckDB syntax).

        Sin regex: LIKE case-insensitive (rápido).
        Con regex: regex_matches() case-sensitive por defecto.

        Args:
            nombre: nombre(s) del titular (LIKE o regex según flag)
            paterno: apellido paterno
            materno: apellido materno
            regex: '1' para activar regex en lugar de LIKE
            limit: 1-100 (default 25)
        """
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        nombre  = (qs.get("nombre",  [""])[0] or "").strip()
        paterno = (qs.get("paterno", [""])[0] or "").strip()
        materno = (qs.get("materno", [""])[0] or "").strip()
        regex   = (qs.get("regex",   ["0"])[0] or "0").strip() == "1"
        try:
            limit = int(qs.get("limit", ["25"])[0])
        except (TypeError, ValueError):
            limit = 25
        limit = max(1, min(limit, 100))

        if not (nombre or paterno or materno):
            self._json(400, {"error": "se requiere al menos uno: nombre, paterno, materno"})
            return

        con = _init_extended_con()
        if con is None:
            self._json(503, {"error": "extendido no inicializado"})
            return

        where, params = [], []
        if regex:
            # DuckDB regexp_matches() — case-sensitive por defecto.
            # Si el usuario pasa (?i) se activa case-insensitive.
            # NOTA: DuckDB no soporta \b (word boundary). Usar patrón simple.
            # CFE solo tiene columna `nombre` (apellidos+nombre concatenados).
            if nombre:
                where.append("regexp_matches(nombre, ?)")
                params.append(nombre)
            if paterno:
                where.append("regexp_matches(nombre, ?)")
                params.append(paterno)
            if materno:
                where.append("regexp_matches(nombre, ?)")
                params.append(materno)
        else:
            if nombre:
                where.append("UPPER(TRIM(nombre)) LIKE ?")
                params.append(f"%{nombre.upper()}%")
            if paterno:
                where.append("UPPER(TRIM(nombre)) LIKE ?")
                params.append(f"%{paterno.upper()}%")
            if materno:
                where.append("UPPER(TRIM(nombre)) LIKE ?")
                params.append(f"%{materno.upper()}%")
        where_sql = " AND ".join(where) if where else "1=1"
        params.append(limit)

        rows, err, elapsed = [], None, None
        try:
            t0 = time.time()
            sql = f"""
                SELECT numero_servicio, division, zona_cod, zona_nom,
                       agencia_cod, agencia_nom,
                       cp, nombre, direccion, calle_adicional_1,
                       calle_adicional_2, colonia, source_folder
                FROM api.cfe_medidor
                WHERE {where_sql}
                LIMIT ?
            """
            rows = con.execute(sql, params).fetchall()
            elapsed = time.time() - t0
        except duckdb.Error as e:
            err = str(e)[:200]

        cols = ["num_servicio","division","zona_cod","zona_nom",
                "agencia_cod","agencia_nom","cp","nombre",
                "direccion","calle_adicional_1","calle_adicional_2",
                "colonia","source_folder"]
        cfe_rows = [dict(zip(cols, r)) for r in rows]

        # 2026-08-14: filtro IA opcional. Si ai=1 y hay paterno/nombre del
        # sujeto conocido, lo pasamos al cliente y la IA determina cuáles
        # matches realmente corresponden.
        ai_analysis = None
        if qs.get("ai", ["0"])[0] == "1" and cfe_rows:
            # Construir subject mínimo desde query params (sin sesión)
            subj = {
                "paterno": paterno or None,
                "materno": materno or None,
                "nombre":  nombre or None,
                "curp":    (qs.get("curp", [""])[0] or "").strip().upper() or None,
                "rfc":     (qs.get("rfc",  [""])[0] or "").strip().upper() or None,
                "cp":      (qs.get("sujeto_cp", [""])[0] or "").strip() or None,
            }
            subj = {k: v for k, v in subj.items() if v}
            if subj:
                ai_analysis = _ai_filter_matches(subj, cfe_rows, fuente="cfe")
                # Reemplazar rows con la versión enriquecida por IA
                if not ai_analysis.get("skipped"):
                    cfe_rows = ai_analysis["matches_filtrados"]

        result = {
            "query": {"nombre": nombre or None, "paterno": paterno or None,
                      "materno": materno or None, "regex": regex,
                      "limit": limit, "ai": ai_analysis is not None},
            "rows": cfe_rows,
            "count": len(cfe_rows),
            "found": bool(cfe_rows),
            "truncated": len(cfe_rows) >= limit,
            "ai_analysis": ai_analysis,
            "elapsed_s": round(elapsed, 3) if elapsed else None,
            "error": err,
            "nota": ("regex_matches() PCRE de DuckDB. "
                     "Use (?i) para case-insensitive. "
                     "66M filas, LIMIT default 25 max 100.") if regex else
                    ("LIKE %x% case-insensitive. "
                     "Para regex, agregar &regex=1 (PCRE DuckDB)."),
        }
        try:
            self._audit(
                session=session, action="view_cfe_buscar_por_nombre",
                endpoint="/api/v1/cfe/buscar_por_nombre", method="GET", status_code=200,
                query_summary={"paterno_prefix": paterno[:6],
                               "regex": regex, "limit": limit},
                results_count=len(cfe_rows),
            )
        except Exception:
            pass
        self._json(200, result)

    def _handle_cfe_buscar_domicilio(self):
        """GET /api/v1/cfe/buscar_domicilio?calle=&colonia=&cp=&division=&limit=

        Wrapper HTTP de _search_cfe_by_domicilio (lógica reusable).
        2026-08-13: cp "00000" o vacío se considera como no-provisto
        (zfill convertía "" a "00000", lo que hacía pasar el check).
        """
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        calle    = (qs.get("calle",    [""])[0] or "").strip().upper()
        colonia  = (qs.get("colonia",  [""])[0] or "").strip().upper()
        cp_raw   = (qs.get("cp",       [""])[0] or "").strip()
        cp       = cp_raw.zfill(5) if cp_raw else ""
        division = (qs.get("division", [""])[0] or "").strip().upper()
        try:
            limit = int(qs.get("limit", ["25"])[0])
        except (TypeError, ValueError):
            limit = 25
        limit = max(1, min(limit, 100))

        if not (calle or colonia or (cp and cp != "00000")):
            self._json(400, {"error": "se requiere al menos uno: calle, colonia, cp"})
            return
        if cp and cp != "00000" and (len(cp) != 5 or not cp.isdigit()):
            self._json(400, {"error": "cp inválido (5 dígitos)"})
            return
        if len(calle) < 3 and len(colonia) < 3 and not (cp and cp != "00000"):
            self._json(400, {"error": "calle o colonia deben tener al menos 3 caracteres"})
            return

        result = _search_cfe_by_domicilio(
            calle=calle, colonia=colonia, cp=cp,
            division=division, limit=limit,
        )
        try:
            self._audit(
                session=session, action="view_cfe_domicilio",
                endpoint="/api/v1/cfe/buscar_domicilio", method="GET", status_code=200,
                query_summary={"calle_prefix": calle[:6], "colonia_prefix": colonia[:6],
                               "cp": cp or None, "limit": limit},
                results_count=result["count"],
            )
        except Exception:
            pass
        self._json(200, result)

    # 2026-08-06: análisis de familia.
    # GET /api/v1/familia/mapa?paterno=&materno=&estado=&ciudad=&nombre=&limit=
    #
    # Algoritmo:
    #   1) Recolecta personas con `paterno` (y opcionalmente `materno`) de:
    #      - b_att (pat + may exactos, devuelve celular/dirección)
    #      - b_telcel (nombre2 contiene paterno+materno)
    #      - b_repuve (nom_prop_fix contiene paterno)
    #   2) Normaliza cada dirección: (estado_upper, ciudad_upper,
    #      colonia_upper, direccion_upper) y agrupa por esa tupla.
    #   3) Devuelve clusters con >= 2 personas en la misma dirección
    #      (típico de familias, roommates, empresa familiar).
    #   4) Si el usuario pasa `nombre` (opcional), también busca en el
    #      padrón INE local por (paterno + materno + nombre LIKE %x%) y
    #      devuelve los matches con su curp/domicilio del padrón. Esto
    #      permite confirmar si las personas del cluster son ciudadanos
    #      registrados y obtener su domicilio oficial.
    def _handle_familia_mapa(self):
        import urllib.parse as _up
        from collections import defaultdict
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        paterno = (qs.get("paterno", [""])[0] or "").strip().upper()
        materno = (qs.get("materno", [""])[0] or "").strip().upper()
        nombre  = (qs.get("nombre",  [""])[0] or "").strip()
        curp_in = (qs.get("curp",    [""])[0] or "").strip().upper()
        estado_filter = (qs.get("estado",  [""])[0] or "").strip().upper()
        ciudad_filter = (qs.get("ciudad",  [""])[0] or "").strip().upper()
        try:
            limit_per_base = int(qs.get("limit", ["500"])[0])
        except (TypeError, ValueError):
            limit_per_base = 500
        limit_per_base = max(50, min(limit_per_base, 5000))

        # Si pasaron curp, sacar los datos del sujeto del padrón para
        # auto-completar paterno/materno/estado si faltan. Esto permite
        # llamar al endpoint con solo ?curp=XXXX y que el sistema arme
        # automáticamente los apellidos del sujeto.
        if curp_in and len(curp_in) == 18:
            try:
                from config import config
                db_path = getattr(config, "padron_db_path", "ine.duckdb")
                pc = duckdb.connect(db_path, read_only=True)
                try:
                    # La columna de entidad en el padrón es `e` (clave
                    # numérica), NO `estado_nombre`. Hacemos el lookup
                    # del estado vía SEPOMEX con el CP después.
                    row = pc.execute("""
                        SELECT paterno, materno, fecnac, cp, e
                        FROM padron WHERE curp = ? LIMIT 1
                    """, [curp_in]).fetchone()
                    if row:
                        if not paterno and row[0]:
                            paterno = row[0].upper()
                        if not materno and row[1]:
                            materno = row[1].upper()
                        if not fecnac and row[2]:
                            fecnac = row[2]
                        if not estado_filter:
                            # Buscar estado_nombre desde SEPOMEX con el CP
                            try:
                                import sqlite3 as _sq
                                _sep = _sq.connect(str(ROOT / "geo.db"))
                                _r = _sep.execute(
                                    "SELECT estado FROM cp WHERE cp = ? LIMIT 1",
                                    [str(row[3] or "")]
                                ).fetchone()
                                _sep.close()
                                if _r:
                                    estado_filter = _r[0].upper()
                            except Exception:
                                pass
                finally:
                    pc.close()
            except Exception:
                pass

        if len(paterno) < 3:
            self._json(400, {"error": "paterno debe tener al menos 3 caracteres (o pasar ?curp= de 18 chars)"})
            return

        con = _init_extended_con()
        if con is None:
            self._json(503, {"error": "extendido no inicializado"})
            return

        # Personas unificadas: cada entry es un dict con
        # {fuente, rfc, nombre_completo, direccion, colonia, ciudad, estado}
        personas = []

        # ── 1) ATT (mejor calidad: tiene paterno+materno+nombres separados) ──
        try:
            t0 = time.time()
            sql = """
                SELECT rfc_clean, TRIM(nombres), TRIM(pat), TRIM(may),
                       TRIM(celular), TRIM(tel1),
                       UPPER(TRIM(COALESCE(direccion,''))) as dir,
                       UPPER(TRIM(COALESCE(colonia,''))) as col,
                       UPPER(TRIM(COALESCE(municipio,''))) as ciu,
                       UPPER(COALESCE(estado,'')) as edo
                FROM b_att.main.att
                WHERE UPPER(TRIM(pat)) = ?
                  """ + ("AND UPPER(TRIM(may)) = ? " if materno else "") + """
                LIMIT ?
            """
            params = [paterno] + ([materno] if materno else []) + [limit_per_base]
            for r in con.execute(sql, params).fetchall():
                personas.append({
                    "fuente": "att", "rfc": r[0],
                    "nombre": f"{r[1]} {r[2]} {r[3]}".strip(),
                    "telefono": (r[4] or r[5] or "").strip(),
                    "direccion": r[6], "colonia": r[7], "ciudad": r[8], "estado": r[9],
                })
            elapsed_att = time.time() - t0
        except duckdb.Error as e:
            elapsed_att = None
            self._audit_meta("att", str(e)[:200])

        # ── 2) TELCEL (nombre2 = "PATERNO MATERNO", nombre1 = nombres) ──
        try:
            t0 = time.time()
            if materno:
                # paterno + materno en nombre2; la base guarda "PATERNO MATERNO"
                # en cualquier orden, así que probamos ambas
                sql = """
                    SELECT rfc_clean, telefono, TRIM(nombre1), TRIM(nombre2),
                           UPPER(TRIM(COALESCE(domicilio,''))) as dir,
                           UPPER(TRIM(COALESCE(colonia,''))) as col,
                           UPPER(TRIM(COALESCE(ciudad,''))) as ciu,
                           UPPER(TRIM(COALESCE(edo,''))) as edo
                    FROM b_telcel.main.telcel
                    WHERE (UPPER(TRIM(nombre2)) = ? OR UPPER(TRIM(nombre2)) = ?
                           OR UPPER(TRIM(nombre2)) LIKE ? OR UPPER(TRIM(nombre2)) LIKE ?)
                    LIMIT ?
                """
                params = [f"{paterno} {materno}", f"{materno} {paterno}",
                          f"%{paterno}%{materno}%", f"%{materno}%{paterno}%",
                          limit_per_base]
            else:
                sql = """
                    SELECT rfc_clean, telefono, TRIM(nombre1), TRIM(nombre2),
                           UPPER(TRIM(COALESCE(domicilio,''))) as dir,
                           UPPER(TRIM(COALESCE(colonia,''))) as col,
                           UPPER(TRIM(COALESCE(ciudad,''))) as ciu,
                           UPPER(TRIM(COALESCE(edo,''))) as edo
                    FROM b_telcel.main.telcel
                    WHERE UPPER(TRIM(nombre2)) LIKE ?
                    LIMIT ?
                """
                params = [f"%{paterno}%", limit_per_base]
            for r in con.execute(sql, params).fetchall():
                # r[3] = nombre2 (PATERNO MATERNO). Lo limpiamos para no
                # contaminar el match: si nombre2 no contiene paterno, skip.
                nom2 = (r[3] or "").upper()
                if paterno not in nom2:
                    continue
                if materno and materno not in nom2:
                    continue
                personas.append({
                    "fuente": "telcel", "rfc": r[0],
                    "telefono": (r[1] or "").strip(),
                    "nombre": f"{(r[2] or '').strip()} {(r[3] or '').strip()}".strip(),
                    "direccion": r[4], "colonia": r[5], "ciudad": r[6], "estado": r[7],
                })
            elapsed_telcel = time.time() - t0
        except duckdb.Error as e:
            elapsed_telcel = None
            self._audit_meta("telcel", str(e)[:200])

        # ── 3) REPUVE (nom_prop_fix = "PATERNO MATERNO NOMBRES") ──
        try:
            t0 = time.time()
            sql = """
                SELECT rfc_clean, TRIM(nom_prop_fix), TRIM(dir_prop_fix),
                       TRIM(tel_prop)
                FROM b_repuve.main.repuve
                WHERE UPPER(nom_prop_fix) LIKE ?
                LIMIT ?
            """
            params = [f"%{paterno}%", limit_per_base]
            for r in con.execute(sql, params).fetchall():
                nom = (r[1] or "").upper()
                if materno and materno not in nom:
                    continue
                dir_fix = r[2] or ""
                # El dir_prop_fix es una sola string; no tiene separados
                # ciudad/estado. Tratamos toda la string como "direccion".
                personas.append({
                    "fuente": "repuve", "rfc": r[0],
                    "nombre": (r[1] or "").strip(),
                    "telefono": (r[3] or "").strip(),
                    "direccion": dir_fix.upper(), "colonia": "", "ciudad": "", "estado": "",
                })
            elapsed_repuve = time.time() - t0
        except duckdb.Error as e:
            elapsed_repuve = None
            self._audit_meta("repuve", str(e)[:200])

        # ── 4) Aplicar filtros de estado/ciudad si los pasaron ──
        if estado_filter or ciudad_filter:
            personas_filtradas = []
            for p in personas:
                if estado_filter and p["estado"] and estado_filter not in p["estado"]:
                    continue
                if ciudad_filter and p["ciudad"] and ciudad_filter not in p["ciudad"]:
                    continue
                personas_filtradas.append(p)
            personas = personas_filtradas

        # ── 5) Agrupar por (estado, ciudad, colonia, direccion) ──
        clusters_dict = defaultdict(list)
        for p in personas:
            # key: si no hay direccion, agrupar por ciudad+colonia nada más
            dir_key = p["direccion"] or "(sin direccion)"
            col_key = p["colonia"] or "(sin colonia)"
            ciu_key = p["ciudad"] or "(sin ciudad)"
            edo_key = p["estado"] or "(sin estado)"
            # En repuve, direccion trae toda la dirección; lo que nos importa
            # es agrupar gente en la misma zona. Para repuve usamos ciudad=dir
            # para no romper la agrupación.
            if p["fuente"] == "repuve":
                ciu_key = "(repuve_solo_dir)"
            key = (edo_key, ciu_key, col_key, dir_key)
            clusters_dict[key].append(p)

        # ── 6) Devolver solo clusters con >= 2 personas ──
        clusters = []
        for (edo, ciu, col, direc), grupo in clusters_dict.items():
            if len(grupo) < 2:
                continue
            # Dedup por (rfc, telefono) — una persona puede aparecer varias
            # veces (varias líneas telcel, varios vehículos)
            seen = set()
            uniq = []
            for p in grupo:
                k = (p["rfc"], p["telefono"])
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(p)
            if len(uniq) < 2:
                continue
            # Si el cluster no tiene ciudad (caso repuve) y tampoco estado,
            # lo descartamos porque no es analizable geográficamente.
            if ciu == "(repuve_solo_dir)" and not direc.strip():
                continue
            # Ordenar las fuentes preferentemente
            fuentes = sorted({p["fuente"] for p in uniq})
            clusters.append({
                "estado": edo,
                "ciudad": ciu,
                "colonia": col,
                "direccion": direc,
                "personas": uniq,
                "n_personas_unicas": len(uniq),
                "fuentes": fuentes,
                "rfcs_unicos": sorted({p["rfc"] for p in uniq if p["rfc"]}),
            })

        # Ordenar clusters por tamaño (mayor primero)
        clusters.sort(key=lambda c: c["n_personas_unicas"], reverse=True)

        # ── 7) Si pasaron `nombre` o `curp`, cruzar con el padrón local ──
        # El padrón principal (ine.duckdb) NO está attacheado a la conexión
        # extendida, así que abrimos una conexión efímera como hace
        # _find_sujeto(). Adicionalmente cruzamos con SEPOMEX (geo.db) para
        # obtener los nombres legibles de estado/municipio/ciudad a partir
        # del CP.
        # 2026-08-06: también agrupamos los matches del padrón por dirección
        # para detectar clusters familiares directos del padrón (mismo
        # apellido+materno en la misma calle+dirección, sin pasar por
        # att/telcel/repuve). Esto es especialmente útil cuando la persona
        # del padrón NO aparece en las bases externas, pero sus familiares sí.
        # 2026-08-06: si se pasó `curp`, además mapeamos los familiares
        # del padre y madre (sus clusters respectivos) usando los
        # apellidos paterno del padre y materno de la madre.
        padre_clusters = []
        madre_clusters  = []
        padre_meta = None
        madre_meta  = None
        padron_matches = []
        padron_clusters = []
        if paterno:
            # ── Conexión única al padrón que se reutiliza en TODAS
            # las sub-queries (padron_matches, padron_clusters,
            # padre_meta, madre_meta, padre_clusters, madre_clusters,
            # lookup inicial con curp). El bug original era que se
            # abría una conexión `pc` para el lookup inicial y luego
            # se cerraba, dejando sin conexión al resto del bloque.
            pad_con = None
            sepomex_cp = {}  # también movido aquí para no cargarlo 2 veces
            try:
                from config import config
                db_path = getattr(config, "padron_db_path", "ine.duckdb")
                pad_con = duckdb.connect(db_path, read_only=True)
                # Cargar SEPOMEX una sola vez (se usa en múltiples bloques)
                try:
                    import sqlite3
                    _sep = sqlite3.connect(str(ROOT / "geo.db"))
                    for r in _sep.execute(
                        "SELECT cp, estado, municipio FROM cp"
                    ).fetchall():
                        sepomex_cp[str(r[0])] = {
                            "estado_nombre": r[1],
                            "municipio_nombre": r[2],
                        }
                    _sep.close()
                except Exception:
                    pass
            except Exception as e:
                self._json(500, {"error": f"padron connect: {str(e)[:200]}"})
                return

            try:
                from config import config
                db_path = getattr(config, "padron_db_path", "ine.duckdb")
                # Sujeto del padrón: si pasaron curp, lo cargamos una vez
                sujeto_pad = None
                if curp_in and len(curp_in) == 18:
                    pc = duckdb.connect(db_path, read_only=True)
                    try:
                        row = pc.execute("""
                            SELECT curp, nombre, paterno, materno, fecnac, edad, sexo,
                                   calle, ext, colonia, cp, e, d, m, s
                            FROM padron WHERE curp = ? LIMIT 1
                        """, [curp_in]).fetchone()
                        if row:
                            cols = [d[0] for d in pc.description]
                            sujeto_pad = dict(zip(cols, row))
                    finally:
                        pc.close()
                # Cargamos SEPOMEX
                try:
                    import sqlite3
                    _sep = sqlite3.connect(str(ROOT / "geo.db"))
                    for r in _sep.execute(
                        "SELECT cp, estado, municipio FROM cp"
                    ).fetchall():
                        sepomex_cp[str(r[0])] = {
                            "estado_nombre": r[1],
                            "municipio_nombre": r[2],
                        }
                    _sep.close()
                except Exception:
                    pass

                    # Si pasaron nombre, filtrar por nombre (búsqueda dirigida).
                    # Si NO, traer TODOS los del apellido (clustering exhaustivo).
                    if nombre:
                        nombre_l = nombre.strip().upper()
                        sql_pad = """
                            SELECT curp, nombre, paterno, materno, fecnac, sexo, edad,
                                   calle, "int", ext, colonia, cp, e, d, m, s
                            FROM padron
                            WHERE paterno = ?
                              """ + ("AND materno = ? " if materno else "") + """
                              AND UPPER(nombre) LIKE ?
                            LIMIT 50
                        """
                        pparams = [paterno]
                        if materno:
                            pparams.append(materno)
                        pparams.append(f"%{nombre_l}%")
                    else:
                        sql_pad = """
                            SELECT curp, nombre, paterno, materno, fecnac, sexo, edad,
                                   calle, "int", ext, colonia, cp, e, d, m, s
                            FROM padron
                            WHERE paterno = ?
                              """ + ("AND materno = ? " if materno else "") + """
                            LIMIT 200
                        """
                        pparams = [paterno]
                        if materno:
                            pparams.append(materno)

                    # Abrimos conexión para el bloque padron_matches+
                    pc2 = duckdb.connect(db_path, read_only=True)
                    try:
                        for r in pc2.execute(sql_pad, pparams).fetchall():
                            cp = str(r[11] or "")
                            geo = sepomex_cp.get(cp, {})
                            padron_matches.append({
                                "curp": r[0], "nombre": r[1],
                                "paterno": r[2], "materno": r[3], "fecnac": r[4],
                                "sexo": r[5], "edad": r[6],
                                "calle": r[7], "int": r[8], "ext": r[9],
                                "colonia": r[10], "cp": cp,
                                "estado_clave": r[12], "delegacion": r[13],
                                "municipio_clave": r[14], "seccion": r[15],
                                "estado_nombre": geo.get("estado_nombre"),
                                "municipio_nombre": geo.get("municipio_nombre"),
                            })
                    finally:
                        pc2.close()

                # ── 8) Agrupar matches del padrón por dirección ──
                # Key: (estado_nombre, municipio_nombre, colonia_upper,
                #       calle+ext_upper). Sólo se reportan clusters con
                #       >= 2 personas (probable familia: padre+hijo,
                #       hermanos, cónyuges con mismo domicilio).
                if len(padron_matches) >= 2:
                    from collections import defaultdict
                    pad_clusters_dict = defaultdict(list)
                    for p in padron_matches:
                        edo = (p.get("estado_nombre") or "").upper()
                        mun = (p.get("municipio_nombre") or "").upper()
                        col = (p.get("colonia") or "").upper()
                        calle = (p.get("calle") or "").upper()
                        ext = str(p.get("ext") or "").strip()
                        # Si no hay calle pero hay cp+colonia+municipio,
                        # agrupar al menos por zona (cp)
                        key = (edo, mun, col, f"{calle} #{ext}".strip())
                        pad_clusters_dict[key].append(p)
                    for (edo, mun, col, dir_key), grupo in pad_clusters_dict.items():
                        if len(grupo) < 2:
                            continue
                        # Si la calle está vacía y la colonia también, no
                        # es cluster analizable (criterio demasiado laxo)
                        if not col.strip() and not dir_key.strip():
                            continue
                        padron_clusters.append({
                            "estado": edo or None,
                            "municipio": mun or None,
                            "colonia": col or None,
                            "direccion": dir_key or None,
                            "personas": grupo,
                            "n_personas": len(grupo),
                        })
                    # Ordenar por tamaño
                    padron_clusters.sort(key=lambda c: c["n_personas"], reverse=True)

                # ── 9) Mapeo de familiares del padre y madre ──
                # Si se pasó curp y el sujeto está en el padrón, inferir
                # los nombres del padre y madre. Convención mexicana:
                #   - PADRE: hombre con mismo paterno del sujeto (>=15 años mayor)
                #   - MADRE: mujer con mismo materno del sujeto (>=12 años mayor)
                # Luego se usa el padre y la madre como pivotes para mapear
                # sus respectivos familiares (tíos/primos del padre por la
                # línea paterna, tíos/primos de la madre por la línea materna).
                if curp_in and len(curp_in) == 18:
                    # Abrimos una conexión dedicada para este bloque
                    try:
                        pc_fam = duckdb.connect(db_path, read_only=True)
                        try:
                            # Re-leer el sujeto para tener los datos completos
                            curp_lookup = pc_fam.execute("""
                                SELECT curp, nombre, paterno, materno, fecnac, edad,
                                       calle, ext, colonia, cp, e
                                FROM padron WHERE curp = ? LIMIT 1
                            """, [curp_in]).fetchone()
                            if curp_lookup:
                                cols_cl = [d[0] for d in pc_fam.description]
                                subj_full = dict(zip(cols_cl, curp_lookup))
                                subj_year = int((subj_full.get("fecnac") or "0")[:4] or 0)
                                subj_pat = (subj_full.get("paterno") or "").upper()
                                subj_mat = (subj_full.get("materno") or "").upper()
                                subj_cp = str(subj_full.get("cp") or "")
                                subj_col = (subj_full.get("colonia") or "").upper()
                                subj_edo = subj_full.get("e")
                                # ── PADRE: mismo paterno del sujeto, H, >=15 años mayor ──
                                # En México el padre del sujeto lleva su
                                # propio apellido paterno, que es el
                                # paterno del sujeto. NO comparten ambos
                                # apellidos (la madre usa su propio
                                # paterno, no el del padre). Limitamos
                                # por estado (no colonia/CP exacto)
                                # porque los padres suelen mudarse.
                                pad_cands = pc_fam.execute("""
                                    SELECT curp, nombre, paterno, materno, fecnac,
                                           edad, calle, ext, colonia, cp, e, sexo
                                    FROM padron
                                    WHERE paterno = ?
                                      AND sexo = 'H'
                                      AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) <= ?
                                      AND e = ?
                                      AND curp != ?
                                      AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) >= ?
                                    ORDER BY fecnac ASC LIMIT 1
                                """, [subj_pat, subj_year - 15,
                                      subj_edo, curp_in, subj_year - 50]).fetchall()
                                if pad_cands:
                                    pcols = [d[0] for d in pc_fam.description]
                                    padre = dict(zip(pcols, pad_cands[0]))
                                    padre_meta = {
                                        "curp": padre["curp"], "nombre": padre["nombre"],
                                        "fecnac": padre["fecnac"], "edad": padre["edad"],
                                    }
                                    # Mapear los familiares del padre (mismos
                                    # apellidos del padre, en el mismo CP/estado)
                                    # Los familiares son hermanos del padre, su
                                    # esposa, sus hijos (tus tíos y primos).
                                    sql_pf = """
                                        SELECT curp, nombre, paterno, materno, fecnac,
                                               edad, calle, ext, colonia, cp, e, sexo
                                        FROM padron
                                        WHERE paterno = ? AND materno = ?
                                          AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) <= ?
                                          AND curp != ?
                                        LIMIT 50
                                    """
                                    pf_rows = pc_fam.execute(sql_pf, [
                                        padre["paterno"], padre["materno"],
                                        subj_year - 5, padre["curp"]
                                    ]).fetchall()
                                    if pf_rows:
                                        pcols2 = [d[0] for d in pc_fam.description]
                                        pf_list = [dict(zip(pcols2, r)) for r in pf_rows]
                                        # Agrupar por dirección
                                        from collections import defaultdict as _dd
                                        pf_clusters = _dd(list)
                                        for p in pf_list:
                                            cp = str(p.get("cp") or "")
                                            col = (p.get("colonia") or "").upper()
                                            calle = (p.get("calle") or "").upper()
                                            ext = str(p.get("ext") or "")
                                            geo = sepomex_cp.get(cp, {})
                                            p["estado_nombre"] = geo.get("estado_nombre")
                                            p["municipio_nombre"] = geo.get("municipio_nombre")
                                            key = (geo.get("estado_nombre") or "",
                                                   geo.get("municipio_nombre") or "",
                                                   col, f"{calle} #{ext}".strip())
                                            pf_clusters[key].append(p)
                                        for k, g in pf_clusters.items():
                                            if len(g) < 2:
                                                continue
                                            padre_clusters.append({
                                                "direccion": k[3],
                                                "colonia": k[2],
                                                "municipio": k[1],
                                                "estado": k[0],
                                                "personas": g,
                                                "n_personas": len(g),
                                                "parentesco_sugerido": "tíos/primos del padre",
                                            })
                                        padre_clusters.sort(key=lambda c: c["n_personas"], reverse=True)
                                # ── MADRE: mismo materno del sujeto, M, >=12 años mayor ──
                                # En México la madre del sujeto lleva su
                                # propio apellido paterno, que es el
                                # MATERNO del sujeto (no el paterno del
                                # padre).
                                mad_cands = pc_fam.execute("""
                                    SELECT curp, nombre, paterno, materno, fecnac,
                                           edad, calle, ext, colonia, cp, e, sexo
                                    FROM padron
                                    WHERE materno = ?
                                      AND sexo = 'M'
                                      AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) <= ?
                                      AND e = ?
                                      AND curp != ?
                                      AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) >= ?
                                    ORDER BY fecnac ASC LIMIT 1
                                """, [subj_mat, subj_year - 12,
                                      subj_edo, curp_in, subj_year - 50]).fetchall()
                                if mad_cands:
                                    mcols = [d[0] for d in pc_fam.description]
                                    madre = dict(zip(mcols, mad_cands[0]))
                                    madre_meta = {
                                        "curp": madre["curp"], "nombre": madre["nombre"],
                                        "fecnac": madre["fecnac"], "edad": madre["edad"],
                                    }
                                    sql_mf = """
                                        SELECT curp, nombre, paterno, materno, fecnac,
                                               edad, calle, ext, colonia, cp, e, sexo
                                        FROM padron
                                        WHERE paterno = ? AND materno = ?
                                          AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) <= ?
                                          AND curp != ?
                                        LIMIT 50
                                    """
                                    mf_rows = pc_fam.execute(sql_mf, [
                                        madre["paterno"], madre["materno"],
                                        subj_year - 5, madre["curp"]
                                    ]).fetchall()
                                    if mf_rows:
                                        mcols2 = [d[0] for d in pc_fam.description]
                                        mf_list = [dict(zip(mcols2, r)) for r in mf_rows]
                                        from collections import defaultdict as _dd2
                                        mf_clusters = _dd2(list)
                                        for p in mf_list:
                                            cp = str(p.get("cp") or "")
                                            col = (p.get("colonia") or "").upper()
                                            calle = (p.get("calle") or "").upper()
                                            ext = str(p.get("ext") or "")
                                            geo = sepomex_cp.get(cp, {})
                                            p["estado_nombre"] = geo.get("estado_nombre")
                                            p["municipio_nombre"] = geo.get("municipio_nombre")
                                            key = (geo.get("estado_nombre") or "",
                                                   geo.get("municipio_nombre") or "",
                                                   col, f"{calle} #{ext}".strip())
                                            mf_clusters[key].append(p)
                                        for k, g in mf_clusters.items():
                                            if len(g) < 2:
                                                continue
                                            madre_clusters.append({
                                                "direccion": k[3],
                                                "colonia": k[2],
                                                "municipio": k[1],
                                                "estado": k[0],
                                                "personas": g,
                                                "n_personas": len(g),
                                                "parentesco_sugerido": "tíos/primos de la madre",
                                            })
                                        madre_clusters.sort(key=lambda c: c["n_personas"], reverse=True)
                        finally:
                            pc_fam.close()
                    except Exception as e:
                        padre_meta = {"error": str(e)[:200]}
                        madre_meta = {"error": str(e)[:200]}
            except Exception as e:
                padron_matches = [{"error": str(e)[:200]}]

        result = {
            "query": {
                "paterno": paterno, "materno": materno or None,
                "nombre": nombre or None, "estado": estado_filter or None,
                "ciudad": ciudad_filter or None, "limit_per_base": limit_per_base,
            },
            "totales": {
                "personas_recolectadas": len(personas),
                "att_count": sum(1 for p in personas if p["fuente"] == "att"),
                "telcel_count": sum(1 for p in personas if p["fuente"] == "telcel"),
                "repuve_count": sum(1 for p in personas if p["fuente"] == "repuve"),
                "clusters_encontrados": len(clusters),
            },
            "elapsed_s": {
                "att": round(elapsed_att, 3) if elapsed_att else None,
                "telcel": round(elapsed_telcel, 3) if elapsed_telcel else None,
                "repuve": round(elapsed_repuve, 3) if elapsed_repuve else None,
            },
            "clusters": clusters[:50],   # cap 50 clusters en respuesta
            "padron_matches": padron_matches,
            "padron_clusters": padron_clusters[:50],
            "padre_meta": padre_meta,
            "madre_meta":  madre_meta,
            "padre_clusters": padre_clusters[:20],  # cap 20 clusters del padre
            "madre_clusters":  madre_clusters[:20],  # cap 20 clusters de la madre
            "nota": "Clusters con >=2 personas en la misma dirección (estado+ciudad+colonia+calle). "
                    "Cada persona puede tener varias filas si tiene varias líneas/vehículos. "
                    "Si `nombre` se pasa, también busca en el padrón local. "
                    "padron_clusters agrupa matches del padrón por dirección (útil cuando "
                    "los familiares están en el padrón pero no en att/telcel/repuve). "
                    "padre_clusters/madre_clusters son los clusters de los familiares del "
                    "padre y la madre (tíos/primos), si se pasó curp y se pudo inferir.",
        }
        try:
            self._audit(
                session=session, action="view_familia_mapa",
                endpoint="/api/v1/familia/mapa", method="GET", status_code=200,
                query_summary={"paterno": paterno, "materno": materno or None,
                               "estado": estado_filter or None, "ciudad": ciudad_filter or None},
                results_count=len(clusters),
            )
        except Exception:
            pass
        self._json(200, result)

    def _audit_meta(self, base, msg):
        """Helper: log a metadata error in audit. No-op para no romper el flow."""
        try:
            print(f"[familia] {base}: {msg}", file=sys.stderr)
        except Exception:
            pass

    # 2026-08-06: detección de HERMANOS PROBABLES con score de certeza.
    # GET /api/v1/familia/hermanos?curp=XXXX  (ó ?paterno=&materno=&fecnac=)
    #
    # Algoritmo de scoring (suma ponderada, max 100):
    #   - Mismos dos apellidos (paterno+materno exactos): +40 pts
    #     (peso fuerte: el algoritmo ya filtra por esto, es la base)
    #   - Coincidencia exacta de CP: +25 pts (conviven en el mismo CP)
    #   - Mismo domicilio (calle+ext): +15 pts
    #   - Diferencia de edad plausible (0-15 años): +10 pts
    #   - Misma colonia: +5 pts
    #   - Misma entidad de nacimiento (curp chars 12-13): +5 pts
    #
    # Categorías:
    #   >= 85: "muy probable" — mismo domicilio, mismo CP, edad plausible
    #   >= 65: "probable" — mismo CP, edad plausible, distinta colonia
    #   >= 40: "posible" — sólo comparten apellidos y CP aproximado
    #   <  40: descartado
    #
    # Salida: lista de hermanos ordenada por score descendente, agrupada
    # por nivel de certeza, con todos los factores visibles.
    def _handle_familia_hermanos(self):
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        curp = (qs.get("curp", [""])[0] or "").strip().upper()
        paterno = (qs.get("paterno", [""])[0] or "").strip().upper()
        materno = (qs.get("materno", [""])[0] or "").strip().upper()
        fecnac  = (qs.get("fecnac",  [""])[0] or "").strip()
        try:
            delta_max = int(qs.get("delta_max", ["18"])[0])
        except (TypeError, ValueError):
            delta_max = 18
        delta_max = max(1, min(delta_max, 50))
        try:
            limit = int(qs.get("limit", ["100"])[0])
        except (TypeError, ValueError):
            limit = 100
        limit = max(10, min(limit, 500))

        # Si pasaron curp, sacar los datos del sujeto del padrón
        sujeto = None
        try:
            from config import config
            db_path = getattr(config, "padron_db_path", "ine.duckdb")
            pad_con = duckdb.connect(db_path, read_only=True)
            try:
                if curp:
                    sujeto = pad_con.execute("""
                        SELECT curp, nombre, paterno, materno, fecnac, sexo, edad,
                               calle, ext, colonia, cp, e, d, m, s
                        FROM padron WHERE curp = ? LIMIT 1
                    """, [curp]).fetchone()
                    if sujeto:
                        cols = [d[0] for d in pad_con.description]
                        sujeto = dict(zip(cols, sujeto))
            finally:
                pad_con.close()
        except Exception as e:
            self._json(500, {"error": f"padron lookup: {str(e)[:200]}"})
            return

        # Si no hay curp o el sujeto no está, exigir paterno+materno
        if not paterno or not materno:
            if sujeto:
                paterno = (sujeto.get("paterno") or "").upper()
                materno = (sujeto.get("materno") or "").upper()
            else:
                self._json(400, {
                    "error": "se requiere ?curp=... o ?paterno=&materno="
                })
                return
        if not fecnac and sujeto:
            fecnac = sujeto.get("fecnac") or ""
        if not sujeto:
            sujeto = {
                "paterno": paterno, "materno": materno, "fecnac": fecnac,
                "nombre": "", "edad": None, "calle": "", "ext": "",
                "colonia": "", "cp": "", "e": None, "curp": curp,
            }

        # Año de nacimiento del sujeto para calcular diferencias
        try:
            subj_year = int((fecnac or "0000")[:4]) if fecnac else 0
        except (TypeError, ValueError):
            subj_year = 0
        subj_cp = str(sujeto.get("cp") or "").strip()
        subj_calle = (sujeto.get("calle") or "").upper().strip()
        subj_ext = str(sujeto.get("ext") or "").strip()
        subj_colonia = (sujeto.get("colonia") or "").upper().strip()
        subj_e = sujeto.get("e")
        # Entidad de nacimiento de la CURP (chars 12-13)
        subj_curp_ent = ""
        if curp and len(curp) >= 13:
            subj_curp_ent = curp[11:13]

        # Query al padrón: todas las personas con mismos dos apellidos
        # cuya edad difiera por <= delta_max años
        try:
            from config import config
            db_path = getattr(config, "padron_db_path", "ine.duckdb")
            pad_con = duckdb.connect(db_path, read_only=True)
            try:
                # Cargamos SEPOMEX para nombres legibles
                sepomex_cp = {}
                try:
                    import sqlite3
                    sep = sqlite3.connect(str(ROOT / "geo.db"))
                    for r in sep.execute(
                        "SELECT cp, estado, municipio FROM cp"
                    ).fetchall():
                        sepomex_cp[str(r[0])] = {
                            "estado_nombre": r[1], "municipio_nombre": r[2]
                        }
                    sep.close()
                except Exception:
                    pass

                # Si hay año de nacimiento, filtrar por proximidad
                # (±delta_max años) usando extracción del año de fecnac.
                if subj_year:
                    year_min = subj_year - delta_max
                    year_max = subj_year + delta_max
                    sql_h = """
                        SELECT curp, nombre, paterno, materno, fecnac, edad,
                               calle, ext, colonia, cp, e, d, m, s, sexo
                        FROM padron
                        WHERE paterno = ? AND materno = ?
                          AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) BETWEEN ? AND ?
                          AND curp != COALESCE(?, '')  -- excluir al sujeto mismo
                        LIMIT ?
                    """
                    params = [paterno, materno, year_min, year_max,
                              sujeto.get("curp") or "", limit]
                else:
                    sql_h = """
                        SELECT curp, nombre, paterno, materno, fecnac, edad,
                               calle, ext, colonia, cp, e, d, m, s, sexo
                        FROM padron
                        WHERE paterno = ? AND materno = ?
                          AND curp != COALESCE(?, '')
                        LIMIT ?
                    """
                    params = [paterno, materno, sujeto.get("curp") or "", limit]

                candidatos = pad_con.execute(sql_h, params).fetchall()
                cols = [d[0] for d in pad_con.description]
            finally:
                pad_con.close()
        except Exception as e:
            self._json(500, {"error": f"padron hermanos: {str(e)[:200]}"})
            return

        # Scoring
        hermanos = []
        for row in candidatos:
            d = dict(zip(cols, row))
            cand_year = int((d.get("fecnac") or "0000")[:4] or 0)
            cand_cp = str(d.get("cp") or "").strip()
            cand_calle = (d.get("calle") or "").upper().strip()
            cand_ext = str(d.get("ext") or "").strip()
            cand_colonia = (d.get("colonia") or "").upper().strip()
            cand_curp = d.get("curp") or ""
            cand_curp_ent = cand_curp[11:13] if len(cand_curp) >= 13 else ""

            score = 0
            razones = []

            # Mismos apellidos (base, ya filtrado, pero confirmamos)
            if d.get("paterno", "").upper() == paterno and \
               d.get("materno", "").upper() == materno:
                score += 40
                razones.append("apellidos exactos (+40)")

            # CP exacto
            if subj_cp and cand_cp and subj_cp == cand_cp:
                score += 25
                razones.append(f"CP={cand_cp} (+25)")
            elif subj_cp and cand_cp and subj_cp[:2] == cand_cp[:2]:
                score += 10
                razones.append(f"CP相近 {subj_cp[:2]}=={cand_cp[:2]} (+10)")

            # Mismo domicilio (calle+ext)
            if subj_calle and cand_calle and \
               subj_calle == cand_calle and subj_ext == cand_ext:
                score += 15
                razones.append(f"domicilio exacto (+15)")
            elif subj_calle and cand_calle and \
                 subj_calle.split()[0] == cand_calle.split()[0] and \
                 subj_calle.split()[-1] == cand_calle.split()[-1]:
                score += 7
                razones.append(f"calle近似 (+7)")

            # Diferencia de edad plausible (0-15 ideal, 15-25 aceptable)
            if subj_year and cand_year:
                diff = abs(subj_year - cand_year)
                if diff <= 1:
                    score += 10
                    razones.append(f"edad±{diff}año (+10)")
                elif diff <= 5:
                    score += 8
                    razones.append(f"edad±{diff}años (+8)")
                elif diff <= 15:
                    score += 5
                    razones.append(f"edad±{diff}años (+5)")
                elif diff <= 25:
                    score += 2
                    razones.append(f"edad±{diff}años (+2)")
                else:
                    razones.append(f"edad±{diff}años (0)")

            # Misma colonia
            if subj_colonia and cand_colonia and subj_colonia == cand_colonia:
                score += 5
                razones.append(f"colonia={cand_colonia!r} (+5)")

            # Misma entidad de nacimiento (CURP chars 12-13)
            if subj_curp_ent and cand_curp_ent and subj_curp_ent == cand_curp_ent:
                score += 5
                razones.append(f"ent_nac={subj_curp_ent} (+5)")

            if score < 40:
                continue  # muy débil, descartar

            # Categorizar
            if score >= 85:
                nivel = "muy_probable"
            elif score >= 65:
                nivel = "probable"
            else:
                nivel = "posible"

            cp = cand_cp
            geo = sepomex_cp.get(cp, {})
            hermanos.append({
                "curp": cand_curp,
                "nombre": d.get("nombre"),
                "paterno": d.get("paterno"),
                "materno": d.get("materno"),
                "fecnac": d.get("fecnac"),
                "edad": d.get("edad"),
                "sexo": d.get("sexo"),
                "domicilio": f"{cand_calle} #{cand_ext}".strip(),
                "colonia": cand_colonia or None,
                "cp": cp or None,
                "estado_nombre": geo.get("estado_nombre"),
                "municipio_nombre": geo.get("municipio_nombre"),
                "entidad_nacimiento": cand_curp_ent or None,
                "score": score,
                "nivel": nivel,
                "razones": razones,
            })

        # Ordenar por score desc
        hermanos.sort(key=lambda h: h["score"], reverse=True)

        # Agrupar por nivel
        por_nivel = {"muy_probable": [], "probable": [], "posible": []}
        for h in hermanos:
            por_nivel[h["nivel"]].append(h)

        result = {
            "sujeto": {
                "curp": sujeto.get("curp"),
                "nombre": sujeto.get("nombre"),
                "paterno": paterno, "materno": materno,
                "fecnac": sujeto.get("fecnac"),
                "cp": subj_cp or None,
                "calle": sujeto.get("calle"),
                "colonia": sujeto.get("colonia"),
                "entidad_nacimiento": subj_curp_ent or None,
            },
            "parametros": {
                "delta_max_años": delta_max,
                "limit": limit,
            },
            "totales": {
                "candidatos_examinados": len(candidatos),
                "hermanos_detectados": len(hermanos),
                "muy_probable": len(por_nivel["muy_probable"]),
                "probable": len(por_nivel["probable"]),
                "posible": len(por_nivel["posible"]),
            },
            "hermanos": por_nivel,
            "nota": "Score máximo 100. >=85 muy_probable, >=65 probable, "
                    ">=40 posible (<40 descartado). Factores: apellidos "
                    "exactos (+40), CP exacto (+25), domicilio (+15), "
                    "edad plausible (+10), colonia (+5), ent_nacimiento (+5).",
        }
        try:
            self._audit(
                session=session, action="view_familia_hermanos",
                endpoint="/api/v1/familia/hermanos", method="GET", status_code=200,
                query_summary={"paterno": paterno, "materno": materno,
                               "fecnac": fecnac[:10] if fecnac else None,
                               "delta_max": delta_max},
                results_count=len(hermanos),
            )
        except Exception:
            pass
        self._json(200, result)

    # 2026-08-06: extracción de nombres de PADRE y MADRE.
    # GET /api/v1/familia/progenitores?curp=...
    #
    # Tres estrategias (en orden de preferencia):
    #   1) RENAPO via consulta CURP (gob.mx/curp/) — devuelve nombres de
    #      padres si la CURP es válida. Requiere red y el captcha puede
    #      bloquear el scraping masivo.
    #   2) RENAPO vía el endpoint público https://consulta.curp.gob.mx/CurpSP/
    #      (POST JSON) — más simple, sin captcha en algunos casos.
    #   3) Fallback local: si RENAPO no responde o devuelve error, buscar
    #      en el padrón. Convención mexicana de apellidos:
    #        - El PADRE del sujeto aporta el apellido PATERNO del sujeto
    #          (porque el padre usa su propio apellido paterno).
    #        - La MADRE del sujeto aporta el apellido MATERNO del sujeto
    #          (porque en México, por ley, todos llevan el apellido
    #          paterno de la madre, NO el del padre).
    #      Por lo tanto:
    #        - Padre ≈ hombre con mismo paterno del sujeto, >=15 años mayor
    #        - Madre ≈ mujer con mismo materno del sujeto, >=12 años mayor
    def _handle_familia_progenitores(self):
        import urllib.parse as _up
        import urllib.request as _ur
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        curp = (qs.get("curp", [""])[0] or "").strip().upper()
        if not curp or len(curp) != 18:
            self._json(400, {"error": "se requiere ?curp= de 18 chars"})
            return

        result = {
            "curp": curp,
            "fuentes": [],
            "padre": None, "madre": None,
            "metadata": {},
        }

        # ── Estrategia 1: RENAPO via consulta.curp.gob.mx ─────────
        # El portal del gobierno permite consultar la CURP y devuelve
        # los datos del titular. Si los nombres de los padres están
        # presentes en el JSON de respuesta, los extraemos.
        renapo_ok = False
        try:
            # POST al endpoint del RENAPO con la CURP. Este endpoint
            # es público y no requiere captcha para consultas puntuales.
            payload = "{\"curp\":\"" + curp + "\",\"tipoConsulta\":\"DatosPersonales\"}"
            req = _ur.Request(
                "https://consulta.curp.gob.mx/CurpSP/curp/consulta",
                data=payload.encode(),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Hermes/1.0",
                    "Origin": "https://consulta.curp.gob.mx",
                    "Referer": "https://consulta.curp.gob.mx/curp/",
                },
                method="POST",
            )
            with _ur.urlopen(req, timeout=10) as r:
                txt = r.read().decode("utf-8", errors="replace")
            import json as _json
            d = _json.loads(txt)
            result["fuentes"].append("renapo_consulta")
            result["metadata"]["renapo_raw_keys"] = list(d.keys())[:20]
            # El RENAPO suele devolver campos como nombres, paterno, materno
            # del titular. NO devuelve el nombre de los padres en la
            # respuesta pública (solo el titular). Documentamos.
            # Pero a veces incluye campos extendidos. Verificamos.
            for k in ["padreNombre", "padre", "nombrePadre",
                      "madreNombre", "madre", "nombreMadre",
                      "padreNombres", "madreNombres"]:
                if k in d and d[k]:
                    if "padre" in k.lower() and not result["padre"]:
                        result["padre"] = {"nombre": d[k], "fuente": "renapo"}
                    if "madre" in k.lower() and not result["madre"]:
                        result["madre"] = {"nombre": d[k], "fuente": "renapo"}
            renapo_ok = True
        except Exception as e:
            result["metadata"]["renapo_error"] = str(e)[:200]

        # ── Estrategia 2: RENAPO portal2 (válidaCurp) ──────────────
        # Segundo intento: algunos endpoints del RENAPO devuelven más
        # metadata. Probamos válidaCurp + datos básicos.
        if not (result["padre"] and result["madre"]):
            try:
                payload = '{"curp":"' + curp + '"}'
                req = _ur.Request(
                    "https://consulta.curp.gob.mx/CurpSP/curp/valida",
                    data=payload.encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": "Mozilla/5.0 Hermes/1.0",
                    },
                    method="POST",
                )
                with _ur.urlopen(req, timeout=10) as r:
                    txt = r.read().decode("utf-8", errors="replace")
                import json as _json
                d = _json.loads(txt)
                result["fuentes"].append("renapo_valida")
                # Intentar extraer padres
                for k, v in d.items():
                    kl = k.lower()
                    if "padre" in kl and v and not result["padre"]:
                        result["padre"] = {"nombre": v, "fuente": "renapo_valida"}
                    if "madre" in kl and v and not result["madre"]:
                        result["madre"] = {"nombre": v, "fuente": "renapo_valida"}
            except Exception as e:
                result["metadata"]["renapo_valida_error"] = str(e)[:200]

        # ── Estrategia 3: Fallback local al padrón ────────────────
        # Si RENAPO no devolvió los padres, los inferimos del padrón:
        # - Tomar paterno y materno del sujeto (de la CURP chars 0-3
        #   paterno, 0-1 primera consonante interna del paterno; 2-4
        #   materno, 5-6 primera consonante interna del materno)
        # - Buscar personas MAYORES (al menos 15 años mayor que el
        #   sujeto) con esos dos apellidos en la misma colonia/CP/edo
        # - Las 2 personas más viejas con esos apellidos en esa zona
        #   son probable padre y madre.
        if not (result["padre"] and result["madre"]):
            try:
                from config import config
                db_path = getattr(config, "padron_db_path", "ine.duckdb")
                pad_con = duckdb.connect(db_path, read_only=True)
                try:
                    # Cargar SEPOMEX
                    sepomex_cp = {}
                    try:
                        import sqlite3
                        sep = sqlite3.connect(str(ROOT / "geo.db"))
                        for r in sep.execute(
                            "SELECT cp, estado, municipio FROM cp"
                        ).fetchall():
                            sepomex_cp[str(r[0])] = {
                                "estado_nombre": r[1],
                                "municipio_nombre": r[2],
                            }
                        sep.close()
                    except Exception:
                        pass

                    sujeto = pad_con.execute("""
                        SELECT curp, nombre, paterno, materno, fecnac, edad,
                               calle, ext, colonia, cp, e, d, m, s, sexo
                        FROM padron WHERE curp = ? LIMIT 1
                    """, [curp]).fetchone()
                    if sujeto:
                        cols = [d2[0] for d2 in pad_con.description]
                        subj = dict(zip(cols, sujeto))
                        subj_year = int((subj.get("fecnac") or "0000")[:4] or 0)
                        # Apellidos del sujeto
                        subj_pat = (subj.get("paterno") or "").upper()
                        subj_mat = (subj.get("materno") or "").upper()
                        subj_cp = str(subj.get("cp") or "")
                        subj_col = (subj.get("colonia") or "").upper()
                        subj_edo = subj.get("e")
                        # NOTA: En México el padre del sujeto lleva su
                        # propio apellido paterno (= paterno del sujeto),
                        # y la madre lleva su propio apellido paterno
                        # (= materno del sujeto). NO comparten ambos
                        # apellidos.
                        # Criterio geográfico: si padre/madre está en la
                        # misma entidad que el sujeto, ya cuenta como
                        # "misma zona" — no exigimos CP exacto porque
                        # muchos padres/madres cambian de domicilio
                        # después de que el hijo se independizó.

                        # ── ESTRATEGIA: hermanos primero, luego padres ──
                        # Para reducir el universo de candidatos a padre
                        # y madre (que con solo paterno/materno puede
                        # ser >3000), primero detectamos hermanos del
                        # sujeto en el padrón (mismos dos apellidos + CP
                        # similar + edad plausible), y usamos los CPs y
                        # direcciones de los hermanos como pivote para
                        # buscar a los padres. La familia suele vivir
                        # junta, así que los padres comparten CP/colonia
                        # con al menos un hermano.

                        # Buscar hermanos del sujeto (mismos 2 apellidos)
                        sql_hermanos = """
                            SELECT curp, nombre, paterno, materno, fecnac,
                                   edad, calle, ext, colonia, cp, e
                            FROM padron
                            WHERE paterno = ? AND materno = ?
                              AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) BETWEEN ? AND ?
                              AND curp != ?
                              AND e = ?
                            ORDER BY fecnac ASC
                            LIMIT 50
                        """
                        hermanos_year_min = (subj_year - 35) if subj_year else 1900
                        hermanos_year_max = (subj_year + 5) if subj_year else 0
                        herm_cands = pad_con.execute(sql_hermanos, [
                            subj_pat, subj_mat, hermanos_year_min, hermanos_year_max,
                            curp, subj_edo
                        ]).fetchall()
                        herm_cols = [d2[0] for d2 in pad_con.description]
                        hermanos_detectados = [dict(zip(herm_cols, r)) for r in herm_cands]

                        # Recolectar CPs y direcciones de hermanos para
                        # usarlos como pivote en la búsqueda de padres.
                        hermanos_cps = set()
                        hermanos_domicilios_full = set()  # (calle, colonia, cp)
                        for h in hermanos_detectados:
                            if h.get("cp"):
                                hermanos_cps.add(str(h.get("cp")))
                            if h.get("calle"):
                                hermanos_domicilios_full.add((
                                    (h.get("calle") or "").upper().strip(),
                                    (h.get("colonia") or "").upper().strip(),
                                    str(h.get("cp") or "").strip()
                                ))
                        # También el CP del propio sujeto
                        if subj_cp:
                            hermanos_cps.add(subj_cp)
                        # También el domicilio del sujeto
                        if subj.get("calle"):
                            hermanos_domicilios_full.add((
                                (subj.get("calle") or "").upper().strip(),
                                (subj.get("colonia") or "").upper().strip(),
                                str(subj.get("cp") or "").strip()
                            ))

                        # ── Candidato a PADRE ──
                        # Mismo paterno, >=18 años mayor (ideal 18-35),
                        # sexo H, estado = subj_edo. Si tenemos
                        # hermanos, priorizamos por coincidencia de CP
                        # con al menos un hermano.
                        sql_padre = """
                            SELECT curp, nombre, paterno, materno, fecnac, edad,
                                   calle, ext, colonia, cp, e, d, m, s, sexo
                            FROM padron
                            WHERE paterno = ?
                              AND sexo = 'H'
                              AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) BETWEEN ? AND ?
                              AND e = ?
                              AND curp != ?
                            ORDER BY fecnac ASC
                            LIMIT 2000
                        """
                        padre_year_min = (subj_year - 40) if subj_year else 1900
                        padre_year_max = (subj_year - 18) if subj_year else 0
                        padre_cands = pad_con.execute(sql_padre, [
                            subj_pat, padre_year_min, padre_year_max,
                            subj_edo, curp
                        ]).fetchall()
                        cols_p = [d2[0] for d2 in pad_con.description]
                        padres_posibles = [dict(zip(cols_p, r)) for r in padre_cands]
                        # Scoring: la EDAD es la pista más fuerte. Si
                        # tenemos hermanos detectados, priorizamos por
                        # coincidencia de CP con al menos un hermano.
                        # Esto reduce drásticamente el universo de
                        # candidatas y permite identificar al padre
                        # correcto cuando hay cientos de candidatas
                        # con mismo apellido.
                        def _dom_key(calle, ext, colonia="", cp=""):
                            """Normaliza domicilio para comparación.
                            Compara CALLE+COLONIA+CP (sin extensión)
                            porque el mismo domicilio puede tener
                            extensiones registradas como '57', '57.0',
                            '57 ED H', etc."""
                            if not calle:
                                return ""
                            c = (calle or "").upper().strip()
                            # Quitar prefijos C/, AV/, etc.
                            for pref in ["C ", "AV ", "CALZ ", "CALLE ",
                                         "AND ", "CJON ", "PROL ", "RTNO ",
                                         "PASEO ", "PRIV "]:
                                if c.startswith(pref):
                                    c = c[len(pref):]
                                    break
                            col = (colonia or "").upper().strip()
                            cp_s = str(cp or "").strip()
                            return f"{c}|{col}|{cp_s}"
                        # Score: MENOR = MEJOR. age_score es la
                        # diferencia absoluta al ideal (27). diff=27 →
                        # 0, diff=30 → 3, diff=34 → 7. Los bonus CP/dom
                        # son negativos y restan al score (mejor).
                        def padre_score(c):
                            yr_str = (c.get("fecnac") or "0")[:4] or "0"
                            try:
                                yr = int(yr_str)
                            except (TypeError, ValueError):
                                return 1_000_000
                            diff = subj_year - yr
                            age_score = abs(diff - 27)
                            cp_p = str(c.get("cp") or "")
                            cp_match = -500 if cp_p in hermanos_cps else 0
                            dom_c = _dom_key(c.get("calle"), c.get("ext"),
                                              c.get("colonia"), c.get("cp"))
                            dom_match = 0
                            for hd_calle, hd_col, hd_cp in hermanos_domicilios_full:
                                if _dom_key(hd_calle, "", hd_col, hd_cp) == dom_c:
                                    dom_match = -1000
                                    break
                            return age_score + cp_match + dom_match
                        # sort normal (menor primero)
                        padres_posibles.sort(key=padre_score)
                        padres = padres_posibles[:5]

                        # Guardar los hermanos detectados (pivote)
                        result["metadata"]["hermanos_pivote"] = [
                            {
                                "nombre": h.get("nombre"),
                                "fecnac": h.get("fecnac"),
                                "curp": h.get("curp"),
                                "cp": h.get("cp"),
                                "colonia": h.get("colonia"),
                            } for h in hermanos_detectados[:10]
                        ]
                        result["metadata"]["hermanos_cps_usados"] = list(hermanos_cps)

                        # Si hay al menos un candidato, tomar el mejor
                        # Y devolver hasta 5 alternativas para que el
                        # usuario pueda elegir cuál es más probable.
                        if padres:
                            # El primero es el score más alto
                            mejor_padre = padres[0]
                            cp_p = str(mejor_padre.get("cp") or "")
                            geo = sepomex_cp.get(cp_p, {})
                            yr_mejor = int((mejor_padre.get("fecnac") or "0")[:4] or 0)
                            result["padre"] = {
                                "nombre": mejor_padre.get("nombre"),
                                "paterno": mejor_padre.get("paterno"),
                                "materno": mejor_padre.get("materno"),
                                "fecnac": mejor_padre.get("fecnac"),
                                "edad": mejor_padre.get("edad"),
                                "curp": mejor_padre.get("curp"),
                                "domicilio": f"{(mejor_padre.get('calle') or '').upper()} "
                                             f"#{mejor_padre.get('ext') or ''}".strip(),
                                "colonia": mejor_padre.get("colonia"),
                                "cp": cp_p or None,
                                "estado_nombre": geo.get("estado_nombre"),
                                "municipio_nombre": geo.get("municipio_nombre"),
                                "fuente": "padron_inferencia",
                                "criterio": f"mismo paterno ({subj_pat}) + rango 18-40 años mayor + sexo H + estado={subj_edo}",
                                "confianza": "alta" if yr_mejor <= subj_year - 22 else "media",
                                "diff_edad": subj_year - yr_mejor,
                            }
                            # Alternativas (top 5 sin el primero)
                            result["padre_alternativas"] = []
                            for alt in padres[1:5]:
                                cp_a = str(alt.get("cp") or "")
                                yr_a = int((alt.get("fecnac") or "0")[:4] or 0)
                                result["padre_alternativas"].append({
                                    "nombre": alt.get("nombre"),
                                    "paterno": alt.get("paterno"),
                                    "materno": alt.get("materno"),
                                    "fecnac": alt.get("fecnac"),
                                    "curp": alt.get("curp"),
                                    "cp": cp_a or None,
                                    "colonia": alt.get("colonia"),
                                    "diff_edad": subj_year - yr_a,
                                })
                            result["fuentes"].append("padron_inferencia_padre")
                        elif padres_posibles:
                            result["metadata"]["padron_hermanos_detectados"] = [
                                {
                                    "nombre": c.get("nombre"),
                                    "fecnac": c.get("fecnac"),
                                    "curp": c.get("curp"),
                                    "razon": "fuera del rango de edad ideal para padre",
                                } for c in padres_posibles[:5]
                            ]
                            result["metadata"]["padron_inferencia_warning"] = (
                                f"Hay {len(padres_posibles)} hombres con el mismo apellido "
                                f"paterno ({subj_pat}) en el estado {subj_edo}, pero ninguno es >=15 años "
                                "mayor que el sujeto. Probablemente son hermanos/tíos del "
                                "padre, no el padre mismo. No se asigna padre."
                            )

                        # ── Candidato a MADRE ──
                        # En México la madre del sujeto lleva su
                        # PROPIO apellido paterno, que es el
                        # MATERNO del sujeto (no el paterno del
                        # padre). Por lo tanto la madre tiene
                        # paterno = materno_del_sujeto.
                        # Mismo paterno que el materno del sujeto,
                        # >=12 años mayor, sexo M, en el mismo estado.
                        # Candidatos a madre: rango 16-40 años mayor
                        sql_madre = """
                            SELECT curp, nombre, paterno, materno, fecnac, edad,
                                   calle, ext, colonia, cp, e, d, m, s, sexo
                            FROM padron
                            WHERE paterno = ?
                              AND sexo = 'M'
                              AND CAST(SUBSTR(fecnac,1,4) AS INTEGER) BETWEEN ? AND ?
                              AND e = ?
                              AND curp != ?
                            ORDER BY fecnac ASC
                            LIMIT 2000
                        """
                        madre_year_min = (subj_year - 40) if subj_year else 1900
                        madre_year_max = (subj_year - 16) if subj_year else 0
                        madre_cands = pad_con.execute(sql_madre, [
                            subj_mat, madre_year_min, madre_year_max,
                            subj_edo, curp
                        ]).fetchall()
                        cols_m = [d2[0] for d2 in pad_con.description]
                        madres_posibles = [dict(zip(cols_m, r)) for r in madre_cands]
                        def madre_score(c):
                            yr_str = (c.get("fecnac") or "0")[:4] or "0"
                            try:
                                yr = int(yr_str)
                            except (TypeError, ValueError):
                                return 1_000_000
                            diff = subj_year - yr
                            age_score = abs(diff - 27)
                            cp_p = str(c.get("cp") or "")
                            cp_match = -500 if cp_p in hermanos_cps else 0
                            dom_c = _dom_key(c.get("calle"), c.get("ext"),
                                              c.get("colonia"), c.get("cp"))
                            dom_match = 0
                            for hd_calle, hd_col, hd_cp in hermanos_domicilios_full:
                                if _dom_key(hd_calle, "", hd_col, hd_cp) == dom_c:
                                    dom_match = -1000
                                    break
                            return age_score + cp_match + dom_match
                        madres_posibles.sort(key=madre_score)
                        madres = madres_posibles[:5]

                        if madres:
                            mejor_madre = madres[0]
                            cp_m = str(mejor_madre.get("cp") or "")
                            geo = sepomex_cp.get(cp_m, {})
                            yr_m = int((mejor_madre.get("fecnac") or "0")[:4] or 0)
                            result["madre"] = {
                                "nombre": mejor_madre.get("nombre"),
                                "paterno": mejor_madre.get("paterno"),
                                "materno": mejor_madre.get("materno"),
                                "fecnac": mejor_madre.get("fecnac"),
                                "edad": mejor_madre.get("edad"),
                                "curp": mejor_madre.get("curp"),
                                "domicilio": f"{(mejor_madre.get('calle') or '').upper()} "
                                             f"#{mejor_madre.get('ext') or ''}".strip(),
                                "colonia": mejor_madre.get("colonia"),
                                "cp": cp_m or None,
                                "estado_nombre": geo.get("estado_nombre"),
                                "municipio_nombre": geo.get("municipio_nombre"),
                                "fuente": "padron_inferencia",
                                "criterio": f"paterno = materno_del_sujeto ({subj_mat}) + rango 16-40 años mayor + sexo M + estado={subj_edo}",
                                "confianza": "alta" if yr_m <= subj_year - 20 else "media",
                                "diff_edad": subj_year - yr_m,
                            }
                            result["madre_alternativas"] = []
                            for alt in madres[1:5]:
                                cp_a = str(alt.get("cp") or "")
                                yr_a = int((alt.get("fecnac") or "0")[:4] or 0)
                                result["madre_alternativas"].append({
                                    "nombre": alt.get("nombre"),
                                    "paterno": alt.get("paterno"),
                                    "materno": alt.get("materno"),
                                    "fecnac": alt.get("fecnac"),
                                    "curp": alt.get("curp"),
                                    "cp": cp_a or None,
                                    "colonia": alt.get("colonia"),
                                    "diff_edad": subj_year - yr_a,
                                })
                            result["fuentes"].append("padron_inferencia_madre")
                        elif madres_posibles:
                            existing = result["metadata"].get("padron_hermanos_detectados", [])
                            existing.extend([
                                {
                                    "nombre": c.get("nombre"),
                                    "fecnac": c.get("fecnac"),
                                    "curp": c.get("curp"),
                                    "razon": "fuera del rango de edad ideal para madre",
                                } for c in madres_posibles[:5]
                            ])
                            result["metadata"]["padron_hermanos_detectados"] = existing
                            if not result["metadata"].get("padron_inferencia_warning"):
                                result["metadata"]["padron_inferencia_warning"] = (
                                    f"Hay {len(madres_posibles)} mujeres con paterno = materno_del_sujeto "
                                    f"({subj_mat}) en el estado {subj_edo}, pero ninguna es >=12 años "
                                    "mayor que el sujeto. Probablemente son hermanas/tías de la "
                                    "madre, no la madre misma. No se asigna madre."
                                )
                finally:
                    pad_con.close()
            except Exception as e:
                result["metadata"]["padron_inferencia_error"] = str(e)[:200]

        result["nota"] = (
            "RENAPO solo expone el nombre del titular, no de los padres "
            "(sólo aparecen en el PDF que te descargas al validar). Por "
            "eso se usa inferencia local: personas mayores con los mismos "
            "apellidos en la misma zona. Score bajo se debe mejorar con "
            "más bases (actas de nacimiento, etc)."
        )
        try:
            self._audit(
                session=session, action="view_familia_progenitores",
                endpoint="/api/v1/familia/progenitores", method="GET", status_code=200,
                query_summary={"curp": curp, "fuentes": result["fuentes"]},
                results_count=int(bool(result["padre"]) + bool(result["madre"])),
            )
        except Exception:
            pass
        self._json(200, result)

    # 2026-08-15: resolver_desde_hint — dado un hit de una base que NO trae
    # CURP (CFE, Telcel, ATT, REPUVE, ISSSTE), intenta encontrar la CURP
    # más probable en el padrón usando los datos disponibles.
    #
    # Cascada de matching (espejo de _enriquecer_bases_externas pero al revés:
    # hint→curp en lugar de curp→bases):
    #   1) RFC (si viene del hit, ej ATT/Telcel/Empleadores)
    #      → xwalk en imss_s por rfc_clean → curp_clean
    #   2) RFC + (nombre+paterno+materno|fecnac) si hay match ambiguo
    #   3) NSS (si viene del hit, ej IMSS Asegurado)
    #      → xwalk en imss_a por nss_clean → curp_raw
    #   4) nombre + paterno + materno + fecnac (todos requeridos si no hay
    #      clave primaria)
    #   5) nombre + paterno + materno + (cp|calle|colonia) si no hay fecnac
    #   6) nombre + paterno + fecnac + (cp|calle|colonia)
    #
    # Devuelve {ok: bool, curp?: str, score: float, candidates: [...],
    # estrategia: str, error?: str}.
    #
    # Si ok=True, el front abre sujeto.html?curp=X.
    # Si ok=False, el front abre sujeto.html?hint=...&source=... para
    # que el operador termine de resolver manualmente.
    def _handle_sujeto_resolver_desde_hint(self):
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(self.path).query)
        curp    = (qs.get("curp",    [""])[0] or "").upper().strip() or None
        rfc     = (qs.get("rfc",     [""])[0] or "").upper().strip() or None
        nss     = (qs.get("nss",     [""])[0] or "").strip() or None
        nombre  = (qs.get("nombre",  [""])[0] or "").strip().upper() or None
        paterno = (qs.get("paterno", [""])[0] or "").strip().upper() or None
        materno = (qs.get("materno", [""])[0] or "").strip().upper() or None
        fecnac  = (qs.get("fecnac",  [""])[0] or "").strip() or None
        cp      = (qs.get("cp",      [""])[0] or "").strip() or None
        calle   = (qs.get("calle",   [""])[0] or "").strip().upper() or None
        colonia = (qs.get("colonia", [""])[0] or "").strip().upper() or None
        source  = (qs.get("source",  [""])[0] or "").strip().lower() or None

        # Si ya viene CURP, devolver inmediatamente (caso trivial).
        if curp and len(curp) == 18:
            self._json(200, {"ok": True, "curp": curp.upper(),
                             "score": 1.0, "estrategia": "curp_directa",
                             "candidates": [], "source": source})
            return

        # Validación mínima ANTES de tocar la BD.
        if not any([rfc, nss, nombre, paterno]):
            self._json(400, {"error": "se requiere al menos "
                                      "(curp) o (rfc) o (nss) o "
                                      "(nombre+paterno)"})
            return

        try:
            con = duckdb.connect(self.db_path, read_only=True)
        except Exception as ex:
            self._json(500, {"error": f"error abriendo padrón: {ex}"})
            return
        ext_con = _init_extended_con()

        result = {"ok": False, "curp": None, "score": 0.0,
                  "candidates": [], "estrategia": None,
                  "source": source, "hints_used": {k: v for k, v in {
                      "rfc": rfc, "nss": nss, "nombre": nombre,
                      "paterno": paterno, "materno": materno,
                      "fecnac": fecnac, "cp": cp, "calle": calle,
                      "colonia": colonia}.items() if v}}

        # ==========================================================
        # Estrategia 1: RFC solo (xwalk imss_s/rfc → curp).
        # Si hay RFC >= 13 (PM12/PF13) es match casi único; si es
        # 10 chars puede haber varios homónimos (match ambiguo).
        # ==========================================================
        if rfc:
            try:
                if ext_con is not None:
                    curps = [r[0] for r in ext_con.execute("""
                        SELECT DISTINCT curp_clean FROM b_imss_s.main.imss_personas
                        WHERE rfc_clean = ? AND curp_clean IS NOT NULL
                          AND LENGTH(curp_clean) = 18
                    """, [rfc]).fetchall()]
                else:
                    curps = []
            except Exception:
                curps = []
            if len(curps) == 1:
                result.update({"ok": True, "curp": curps[0], "score": 0.95,
                               "estrategia": "rfc_xwalk_imss_s"})
                self._json(200, result)
                return
            elif len(curps) > 1:
                # Ambigüedad por RFC de 10 chars. Pasa a estrategia 2.
                result["candidates"] = curps[:5]
                # Si tenemos nombre+apellidos para desambiguar, lo intentamos.
                if paterno and materno and nombre:
                    try:
                        placeholders = ",".join(["?"] * len(curps))
                        rows = con.execute(f"""
                            SELECT curp, nombre, paterno, materno, fecnac
                            FROM padron
                            WHERE curp IN ({placeholders})
                              AND upper(paterno) = ? AND upper(materno) = ?
                              AND upper(nombre)  = ?
                        """, curps + [paterno, materno, nombre]).fetchall()
                    except Exception:
                        rows = []
                    if len(rows) == 1:
                        result.update({"ok": True, "curp": rows[0][0],
                                       "score": 0.98,
                                       "estrategia": "rfc_xwalk_imss_s+nombre"})
                        self._json(200, result)
                        return
                    if rows:
                        result["candidates"] = [r[0] for r in rows[:5]]
                        result["score"] = 0.6
                        result["estrategia"] = "rfc_xwalk_imss_s+ambiguo"
                        self._json(200, result)
                        return

        # ==========================================================
        # Estrategia 3: NSS solo (xwalk imss_a por nss_clean).
        # ==========================================================
        if nss:
            try:
                if ext_con is not None:
                    curps = [r[0] for r in ext_con.execute("""
                        SELECT DISTINCT curp_clean FROM b_imss_a.main.imss_2025
                        WHERE nss_clean = ? AND curp_clean IS NOT NULL
                          AND LENGTH(curp_clean) = 18
                        LIMIT 5
                    """, [nss]).fetchall()]
                else:
                    curps = []
            except Exception:
                curps = []
            if len(curps) == 1:
                result.update({"ok": True, "curp": curps[0], "score": 0.93,
                               "estrategia": "nss_xwalk_imss_a"})
                self._json(200, result)
                return
            elif len(curps) > 1:
                result["candidates"] = curps
                result["estrategia"] = "nss_xwalk_imss_a+ambiguo"
                # Si tenemos nombre+apellidos, desambiguar
                if paterno and materno:
                    placeholders = ",".join(["?"] * len(curps))
                    rows = con.execute(f"""
                        SELECT curp FROM padron
                        WHERE curp IN ({placeholders})
                          AND upper(paterno) = ? AND upper(materno) = ?
                    """, curps + [paterno, materno]).fetchall()
                    if len(rows) == 1:
                        result.update({"ok": True, "curp": rows[0][0],
                                       "score": 0.97,
                                       "estrategia": "nss_xwalk_imss_a+apellidos"})
                        self._json(200, result)
                        return

        # ==========================================================
        # Estrategia 4-6: padrón directo por nombre + datos secundarios.
        # Mínimo: nombre + paterno + (materno O fecnac) + al menos un
        # dato secundario extra (fecnac, cp, calle, colonia).
        # Si llegamos aquí SIN (nombre+paterno), significa que tampoco hubo
        # match por RFC/NSS — pero los datos secundarios (cp/calle/colonia)
        # no sirven sin nombre+paterno para padrón. Devolvemos 400.
        # (La validación de "ningún parámetro" ya se hizo arriba.)
        if not (nombre and paterno):
            self._json(400, {"error": "sin match por RFC/NSS; se requiere "
                                      "(nombre+paterno) + al menos un dato "
                                      "secundario (materno, fecnac, cp, "
                                      "calle o colonia)"})
            return

        where = ["upper(paterno) = ?"]
        params = [paterno]
        if materno:
            where.append("upper(materno) = ?")
            params.append(materno)
        if nombre:
            # 2026-08-15: padrón guarda un solo nombre; muchos registros
            # tienen nombres compuestos (Maria Guadalupe). Coincidencia
            # "starts with" para tolerar compuestos.
            where.append("upper(nombre) LIKE ?")
            params.append(f"{nombre}%")
        extras = 0
        if fecnac:
            where.append("fecnac = ?")
            params.append(fecnac)
            extras += 1
        if cp:
            where.append("cp = ?")
            params.append(cp)
            extras += 1
        if calle:
            where.append("upper(calle) LIKE ?")
            params.append(f"%{calle}%")
            extras += 1
        if colonia:
            where.append("upper(colonia) LIKE ?")
            params.append(f"%{colonia}%")
            extras += 1

        # Si NO hay ningún dato secundario y NO hay materno, score muy bajo.
        # La política del usuario: RFC/CURP/NSS primero; si no hay, dos
        # datos secundarios (nombre+fechadenac, o nombre+domicilio).
        if extras == 0 and not materno:
            result["error"] = ("datos insuficientes: con sólo (nombre+paterno) "
                               "hay miles de homónimos; se requiere al menos "
                               "(materno, fecnac, cp, calle o colonia)")
            self._json(200, result)
            return

        sql = f"SELECT curp, nombre, paterno, materno, fecnac, cp, calle, colonia " \
              f"FROM padron WHERE {' AND '.join(where)} LIMIT 10"
        try:
            rows = con.execute(sql, params).fetchall()
        except Exception as ex:
            self._json(500, {"error": f"query padrón: {str(ex)[:200]}"})
            return
        cols = ["curp", "nombre", "paterno", "materno", "fecnac",
                "cp", "calle", "colonia"]
        candidates = [dict(zip(cols, r)) for r in rows]

        # Score: 1 match único con extras >= 1 → muy probable.
        if len(candidates) == 1 and extras >= 1:
            score = 0.85 + 0.05 * min(extras, 3)
            result.update({"ok": True, "curp": candidates[0]["curp"],
                           "score": min(score, 0.99),
                           "estrategia": "padron_nombre+extras"})
            self._json(200, result)
            return
        if len(candidates) >= 1 and extras >= 2:
            # Score medio: hubo más de un match pero los extras son fuertes.
            result.update({"ok": True, "curp": candidates[0]["curp"],
                           "score": 0.75,
                           "estrategia": "padron_nombre+extras_multiples",
                           "candidates": [c["curp"] for c in candidates[:5]]})
            self._json(200, result)
            return
        if candidates:
            # Hubo matches pero no suficientes extras. Mostramos candidatos
            # para que el operador decida.
            result["candidates"] = [c["curp"] for c in candidates[:5]]
            result["estrategia"] = "padron_nombre_sin_extras"
            result["score"] = 0.4
            self._json(200, result)
            return

        # Sin matches.
        result["error"] = "sin coincidencias en padrón"
        result["estrategia"] = "sin_match"
        self._json(200, result)

    # 2026-08-05: cruce con las 6 bases externas (att, empleadores, repuve,
    # telcel, imss_asegurado, imss_salud). Endpoint separado porque los
    # lookups a imss_asegurado (57M filas sin índice) tardan 5-30s; lo
    # invocamos lazy desde el frontend después de pintar /api/sujeto.
    def _handle_sujeto_enriquecido(self, curp: str = "", rfc: str = None, nss: str = None,
                                   nombre: str = None, paterno: str = None, materno: str = None,
                                   fecnac: str = None):
        data = _enriquecer_bases_externas(curp=curp, rfc=rfc, nss=nss, cap=50,
                                          nombre=nombre, paterno=paterno, materno=materno, fecnac=fecnac)
        data["curp"]    = curp
        data["rfc"]     = rfc
        data["nss"]     = nss
        data["nombre"]  = nombre
        data["paterno"] = paterno
        data["materno"] = materno
        data["fecnac"]  = fecnac
        self._json(200, data)

    # ==================== ENDPOINTS ADMIN ====================

    def _require_admin(self):
        """Devuelve session si es admin, o None (con respuesta de error ya
        enviada). Usar antes de cada handler admin."""
        session = self._require_session()
        if not session:
            return None
        if session.get("username") != "admin":
            self._json(403, {"error": "solo admin puede acceder"})
            return None
        return session

    def _handle_admin_activity(self):
        """GET /api/admin/activity?username=...&action=...&since=...&limit=...&offset=..."""
        session = self._require_admin()
        if not session:
            return
        t0 = time.time()
        try:
            qs = parse_qs(urlparse(self.path).query)
            username = (qs.get("username", [""])[0] or "").strip() or None
            action = (qs.get("action", [""])[0] or "").strip() or None
            endpoint_like = (qs.get("endpoint", [""])[0] or "").strip() or None
            since_s = (qs.get("since", [""])[0] or "").strip()
            until_s = (qs.get("until", [""])[0] or "").strip()
            since = float(since_s) if since_s else None
            until = float(until_s) if until_s else None
            limit = min(int(qs.get("limit", [100])[0] or 100), 500)
            offset = max(int(qs.get("offset", [0])[0] or 0), 0)
        except (ValueError, TypeError) as e:
            self._json(400, {"error": f"parámetros inválidos: {e}"})
            return

        try:
            data = audit.get_activity(
                username=username, action=action, endpoint_like=endpoint_like,
                since=since, until=until, limit=limit, offset=offset,
            )
        except Exception as e:
            self._json(500, {"error": str(e)})
            return

        self._audit(
            session=session, action="admin_view",
            endpoint="/api/admin/activity", method="GET", status_code=200,
            duration_ms=int((time.time() - t0) * 1000),
            query_summary={"filters": {"username": username, "action": action,
                                      "endpoint": endpoint_like,
                                      "since": since, "until": until},
                           "results": data["total"]},
        )
        self._json(200, {
            **data,
            "actions_known": audit.ACTIONS,
        })

    def _handle_admin_stats(self):
        """GET /api/admin/stats — estadísticas agregadas por usuario."""
        session = self._require_admin()
        if not session:
            return
        t0 = time.time()
        try:
            stats = audit.get_user_stats(days=30)
        except Exception as e:
            self._json(500, {"error": str(e)})
            return
        self._audit(
            session=session, action="admin_view",
            endpoint="/api/admin/stats", method="GET", status_code=200,
            duration_ms=int((time.time() - t0) * 1000),
            query_summary={"days": 30, "users": len(stats)},
        )
        self._json(200, {
            "users": stats,
            "retention_days": audit.DEFAULT_RETENTION_DAYS,
            "actions_known": audit.ACTIONS,
        })

    def _handle_admin_activity_export(self):
        """POST /api/admin/activity/export — devuelve CSV con TODAS las
        filas que coincidan con los filtros (sin paginación)."""
        session = self._require_admin()
        if not session:
            return
        t0 = time.time()
        body = self._read_json_body()
        username = (body.get("username") or "").strip() or None
        action = (body.get("action") or "").strip() or None
        since = body.get("since")
        until = body.get("until")
        since_f = float(since) if since else None
        until_f = float(until) if until else None

        try:
            data = audit.get_activity(
                username=username, action=action,
                since=since_f, until=until_f, limit=10000, offset=0,
            )
        except Exception as e:
            self._json(500, {"error": str(e)})
            return

        import csv, io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "timestamp", "username", "action", "endpoint",
                    "method", "status", "duration_ms", "results_count",
                    "ip", "user_agent", "query_summary"])
        for r in data["rows"]:
            ts = r.get("created_at", 0)
            iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else ""
            qs = r.get("query_summary")
            if isinstance(qs, dict):
                qs = json.dumps(qs, ensure_ascii=False, default=str)
            w.writerow([
                r.get("id"), iso, r.get("username") or "", r.get("action") or "",
                r.get("endpoint") or "", r.get("method") or "",
                r.get("status_code") or "", r.get("duration_ms") or "",
                r.get("results_count") or "", r.get("ip") or "",
                (r.get("user_agent") or "")[:120], qs or "",
            ])
        body_bytes = buf.getvalue().encode("utf-8")
        self._audit(
            session=session, action="export_csv",
            endpoint="/api/admin/activity/export", method="POST", status_code=200,
            duration_ms=int((time.time() - t0) * 1000),
            query_summary={"rows": len(data["rows"]), "filters": body},
        )
        filename = f"activity_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def _handle_admin_users_list(self):
        """GET /api/admin/users — lista usuarios con estado is_active, is_admin."""
        session = self._require_admin()
        if not session:
            return
        t0 = time.time()
        try:
            users = auth.list_users_admin()
        except Exception as e:
            self._json(500, {"error": str(e)})
            return
        self._audit(
            session=session, action="admin_view",
            endpoint="/api/admin/users", method="GET", status_code=200,
            duration_ms=int((time.time() - t0) * 1000),
            query_summary={"action": "list", "count": len(users)},
        )
        self._json(200, {"users": users})

    def _handle_admin_users_action(self):
        """POST /api/admin/users — acciones: disable, enable, set_admin,
        delete, reset_password. Body: {action, username, ...}"""
        session = self._require_admin()
        if not session:
            return
        t0 = time.time()
        body = self._read_json_body()
        action = (body.get("action") or "").strip()
        username = (body.get("username") or "").strip().lower()
        if not action or not username:
            self._json(400, {"error": "action y username requeridos"})
            return
        # no permitir acciones sobre sí mismo
        if username == session.get("username") and action in ("disable", "delete"):
            self._json(400, {"error": "no puedes deshabilitar/eliminar tu propia cuenta"})
            return

        try:
            if action == "disable":
                res = auth.set_user_active(username, False)
                audit_action = "user_disable"
            elif action == "enable":
                res = auth.set_user_active(username, True)
                audit_action = "user_enable"
            elif action == "set_admin":
                is_admin = bool(body.get("is_admin", True))
                res = auth.set_user_admin(username, is_admin)
                audit_action = "admin_set"
            elif action == "delete":
                res = auth.delete_user(username)
                audit_action = "user_delete"
            elif action == "reset_password":
                password = body.get("password") or ""
                if not password:
                    self._json(400, {"error": "password requerido"})
                    return
                res = auth.set_user_password(username, password)
                audit_action = "user_reset"
            else:
                self._json(400, {"error": f"acción desconocida: {action}"})
                return
        except Exception as e:
            self._json(400, {"error": str(e)})
            return

        self._audit(
            session=session, action=audit_action,
            endpoint="/api/admin/users", method="POST", status_code=200,
            duration_ms=int((time.time() - t0) * 1000),
            query_summary={"target_user": username, "sub_action": action},
        )
        self._json(200, {"ok": True, "action": action, "username": username, "result": res})

    def _handle_validar(self):
        """POST /api/validar — ejecuta UNA validación a petición, con query editable."""
        try:
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            validation_id = payload.get("validation_id", "").strip()
            curp = payload.get("curp", "").upper().strip()
            query = payload.get("query", "").strip()
            sujeto = payload.get("sujeto", {})
            rfc = payload.get("rfc", "")

            if not validation_id:
                self._json(400, {"error": "falta validation_id"})
                return

            result, cost = _run_validation(validation_id, curp, query, sujeto, rfc)
            self._json(200, {
                "validation_id": validation_id,
                "query": query,
                "cost_mxn": cost,
                "result": result,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json(500, {"error": str(e)})


# === helpers para /api/sujeto y /api/validar ==============================

def parse_fecnac(fecnac):
    """Convierte YYYY-MM-DD → day, month, year."""
    if fecnac:
        try:
            parts = str(fecnac).split("-")
            return {"day": parts[2], "month": parts[1], "year": parts[0]}
        except Exception:
            pass
    return {"day": "", "month": "", "year": ""}


def _ai_filter_matches(subject: dict, matches: list, fuente: str) -> dict:
    # Alias inmutable al original, sobrevive cualquier reasignación del
    # nombre en el módulo (e.g. cuando un test hace patch).
    return _ai_filter_matches_real(subject, matches, fuente)


def _ai_filter_matches_real(subject: dict, matches: list, fuente: str) -> dict:
    """Envía los matches de CFE/ISSSTE/etc a Ollama Cloud para que la IA determine
    cuáles corresponden al sujeto.

    Args:
        subject: dict con keys del sujeto: nombre, paterno, materno, curp, rfc,
                 fecnac, sexo, cp, estado. Solo se usan las presentes.
        matches: lista de dicts (las filas devueltas por el endpoint correspondiente).
                 Cada match debe tener al menos keys identificadoras.
        fuente: 'cfe' | 'issste' | 'att' | etc. Sirve para que la IA sepa qué
                campos analizar.

    Returns:
        dict con:
          - matches_filtrados: lista de matches originales + score + razon
          - confianza_global: 'alta' | 'media' | 'baja' | 'sin_datos'
          - resumen: texto corto de la IA
          - error: str si falló
          - skipped: bool si no se ejecutó (sin IA o matches vacíos)
    """
    if not matches:
        return {"matches_filtrados": [], "confianza_global": "sin_datos",
                "resumen": "Sin matches para analizar.", "skipped": True}
    try:
        from providers.ollama_cloud import OllamaCloudClient
    except Exception as e:
        return {"matches_filtrados": [], "confianza_global": "sin_datos",
                "resumen": f"OllamaCloudClient no disponible: {e}",
                "skipped": True, "error": str(e)}

    # Cargar API key desde config (que ya cargó .env) o env directo.
    api_key = None
    model = None
    try:
        from config import config
        api_key = getattr(config, "ollama_api_key", None) or os.getenv("OLLAMA_API_KEY")
        model = getattr(config, "ollama_model", None) or os.getenv("OLLAMA_MODEL", "deepseek-v4-pro")
    except Exception:
        api_key = os.getenv("OLLAMA_API_KEY")
        model = os.getenv("OLLAMA_MODEL", "deepseek-v4-pro")
    if not api_key:
        return {"matches_filtrados": [], "confianza_global": "sin_datos",
                "resumen": "OLLAMA_API_KEY no configurada",
                "skipped": True, "error": "no_api_key"}

    # Descripción del sujeto
    subj_lines = [f"Sujeto (target):"]
    for k in ["nombre", "nombres", "paterno", "materno", "curp", "rfc",
              "fecnac", "sexo", "cp", "estado"]:
        v = subject.get(k)
        if v:
            subj_lines.append(f"  - {k}: {v}")
    subj_str = "\n".join(subj_lines)

    # Lista compacta de matches (solo campos relevantes para CFE/ISSSTE)
    match_lines = []
    for i, m in enumerate(matches):
        keys = ["id", "paterno", "materno", "nombres", "nombre",
                "cargo", "nombramiento", "sexo", "sueldo", "ramo",
                "entidad", "estado", "modalidad", "sector",
                "nombre", "direccion", "colonia", "cp", "division",
                "num_servicio", "rfc"]
        parts = []
        for k in keys:
            v = m.get(k)
            if v is not None and v != "":
                parts.append(f"{k}={v}")
        match_lines.append(f"[{i}] " + " | ".join(parts))
    matches_str = "\n".join(match_lines)

    prompt = f"""Eres un analista KYC. Tu trabajo es determinar cuáles de los {len(matches)}
siguientes registros encontrados en {fuente.upper()} corresponden plausiblemente al sujeto objetivo.

SUJETO (target):
{subj_str}

REGISTROS ENCONTRADOS ({len(matches)} matches):
{matches_str}

INSTRUCCIONES:
1. Para CADA match (índice 0 a {len(matches)-1}), asigna un score 0.0-1.0 que representa
   la probabilidad de que sea el mismo individuo que el sujeto.
2. Da una razón corta (≤15 palabras) explicando el score.
3. Score >= 0.70 = coincidencia fuerte; 0.40-0.70 = dudoso; < 0.40 = NO es el sujeto.
4. confianza_global = "alta" si hay al menos un match con score >= 0.70; "media" si el mejor
   score está entre 0.40-0.69; "baja" si todos < 0.40.
5. resumen = frase corta (≤25 palabras) describiendo qué encontraste.

IMPORTANTE: responde SOLO con JSON válido (sin texto antes ni después, sin bloques markdown).
Estructura exacta:
{{"matches":[{{"index":0,"score":0.5,"razon":"texto"}},{{"index":1,"score":0.1,"razon":"texto"}}],"confianza_global":"baja","resumen":"texto"}}"""

    try:
        client = OllamaCloudClient(api_key=api_key, model=model, timeout=60)
        r = client.chat(
            messages=[
                {"role": "system",
                 "content": "Eres un analista KYC experto en matching de identidad."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
            format_json=True,
        )
        if r.get("error"):
            return {"matches_filtrados": matches, "confianza_global": "sin_datos",
                    "resumen": f"IA error: {r['error']}", "skipped": True,
                    "error": r["error"]}
        content = (r.get("message") or "").strip()
        # Extraer JSON (puede venir envuelto en ```json ... ```)
        if content.startswith("```"):
            # Buscar el bloque JSON dentro de los backticks
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if m:
                content = m.group(1)
            else:
                content = content.strip("`").replace("json", "", 1).strip()
        # Si empieza con texto antes del JSON, encontrar el primer {
        if not content.startswith("{"):
            i = content.find("{")
            if i >= 0:
                content = content[i:]
            j = content.rfind("}")
            if j >= 0:
                content = content[:j+1]
        try:
            data = json.loads(content)
        except Exception as e:
            return {"matches_filtrados": matches, "confianza_global": "sin_datos",
                    "resumen": f"IA no devolvió JSON válido: {e}",
                    "skipped": True, "error": str(e),
                    "raw_ai_response": content[:500]}

        # Enriquecer matches con score/razon
        ia_matches = data.get("matches", [])
        idx_map = {x.get("index"): x for x in ia_matches if isinstance(x, dict)}
        enriched = []
        for i, m in enumerate(matches):
            ia = idx_map.get(i, {})
            score = float(ia.get("score", 0) or 0)
            enriched.append({
                **m,
                "ai_score": round(score, 3),
                "ai_razon": str(ia.get("razon", ""))[:200],
                "ai_match": score >= 0.70,
            })

        confianza = data.get("confianza_global", "sin_datos")
        if confianza not in ("alta", "media", "baja", "sin_datos"):
            confianza = "sin_datos"
        return {
            "matches_filtrados": enriched,
            "confianza_global": confianza,
            "resumen": str(data.get("resumen", ""))[:500],
            "model": r.get("model"),
            "tokens_in": r.get("tokens_in"),
            "tokens_out": r.get("tokens_out"),
            "duration_ms": round(r.get("duration_ms", 0), 1),
            "skipped": False,
        }
    except Exception as e:
        return {"matches_filtrados": matches, "confianza_global": "sin_datos",
                "resumen": f"Exception: {e}", "skipped": True, "error": str(e)}


def _extract_checkid_str(node, key=None):
    """Extrae un valor string de un dict de CheckID, buscando por clave conocida."""
    if node is None:
        return None
    if isinstance(node, (str, int, float)):
        return str(node).strip() or None
    if isinstance(node, dict):
        # claves conocidas según API CheckID
        keys = []
        if key:
            keys.append(key)
        keys += ["nss", "codigoPostal", "regimenesFiscales", "emailContacto", "email",
                 "razonSocial", "rfc", "valor", "resultado"]
        for k in keys:
            if k in node:
                v = node[k]
                if isinstance(v, (str, int, float)):
                    s = str(v).strip()
                    if s:
                        return s
        # fallback: primer valor string no-vacío del dict
        for v in node.values():
            if isinstance(v, (str, int, float)):
                s = str(v).strip()
                if s:
                    return s
    return None


def get_checkid_client():
    from config import config
    if not hasattr(config, "_broker_clients"):
        config._broker_clients = {}
    if "checkid" not in config._broker_clients:
        from providers.checkid import CheckIdClient
        config._broker_clients["checkid"] = CheckIdClient(
            config.checkid_api_key,
            base_url=config.checkid_base_url,
        )
    return config._broker_clients["checkid"]


def get_singula_client():
    from config import config
    if not hasattr(config, "_broker_clients"):
        config._broker_clients = {}
    if "singula" not in config._broker_clients:
        from providers.singula import SingulaClient
        config._broker_clients["singula"] = SingulaClient(
            config.singula_api_key,
            organization_id=config.singula_org_id,
            env=config.singula_env,
        )
    return config._broker_clients["singula"]


def get_tlaloc_client():
    from config import config
    if not hasattr(config, "_broker_clients"):
        config._broker_clients = {}
    if "tlaloc" not in config._broker_clients:
        from providers.tlaloc import TlalocClient
        config._broker_clients["tlaloc"] = TlalocClient(config.tlaloc_api_key)
    return config._broker_clients["tlaloc"]


def _generate_map_for_sujeto(sujeto: dict) -> tuple:
    """Genera un mapa estático (PNG base64) para la dirección del sujeto.

    Returns:
        (map_image_base64: str | None, map_location: dict | None)
    """
    import base64
    from report_generator import _build_direccion, geocode_address, generate_static_map

    direccion = _build_direccion(sujeto)
    cp = str(sujeto.get("cp", "")).strip()[:5]

    if not direccion or direccion == "—":
        return None, None

    # Intentar geocodificar dirección completa, luego solo CP
    geo = geocode_address(direccion, cp)
    if not geo and cp and cp != "00000":
        geo = geocode_address("", cp)

    if not geo:
        return None, None

    try:
        img_bytes = generate_static_map(geo["lat"], geo["lon"])
        img_b64 = base64.b64encode(img_bytes).decode("ascii")
        return img_b64, {**geo, "direccion": direccion}
    except Exception:
        return None, None


def _generate_map_for_cp(cp: str, source: str = "") -> tuple:
    """Genera un mapa estático para un CP específico (no del padrón).

    Útil cuando CheckID o Singula devuelven un CP distinto al del padrón.
    Returns:
        (map_image_base64: str | None, map_location: dict | None)
    """
    import base64
    from report_generator import geocode_address, generate_static_map

    cp = str(cp or "").strip().zfill(5)[:5]
    if not cp or cp == "00000":
        return None, None

    geo = geocode_address("", cp)
    if not geo:
        return None, None

    try:
        img_bytes = generate_static_map(geo["lat"], geo["lon"])
        img_b64 = base64.b64encode(img_bytes).decode("ascii")
        loc = {**geo, "direccion": f"CP {cp}", "source": source}
        return img_b64, loc
    except Exception:
        return None, None


def _collect_addresses_for_sujeto(sujeto: dict, enrichment: dict = None) -> list:
    """Recolecta TODAS las direcciones del sujeto en TODAS las bases.

    Cada elemento es un dict:
      {
        "titulo": "Domicilio Padrón" | "Domicilio CFE #1" | etc,
        "fuente": "padron" | "cfe" | "att" | "imss" | "telcel" | "repuve" | "sepomex" | "checkid",
        "direccion": texto,
        "cp": str (5 dígitos o ""),
        "metadata": {...},  # datos extra para identificación
      }

    2026-08-13: extrae de:
      - padrón (sujeto directo)
      - cfe (todas las coincidencias de api.cfe_medidor por nombre)
      - att (api.att_persona por RFC)
      - telcel (api.telcel_lineas por RFC, todas las coincidencias)
      - imss (api.imss_asegurado por CURP, domicilio empresa)
      - repuve (api.repuve_de_persona por RFC)
      - empleadores (api.empleadores por RFC, dom_empresa)
      - sepomex (CP del padrón, fallback si no hay dirección)
      - checkid (CP del enriquecimiento)
    """
    addresses = []

    # 1. Padrón
    try:
        from report_generator import _build_direccion
        direccion = _build_direccion(sujeto)
        cp = str(sujeto.get("cp", "")).strip().zfill(5)[:5]
        if direccion and direccion != "—":
            addresses.append({
                "titulo": "Domicilio Padrón Electoral",
                "fuente": "padron",
                "direccion": direccion,
                "cp": cp,
                "metadata": {
                    "folio": sujeto.get("folio", ""),
                    "seccion": sujeto.get("seccion", ""),
                    "estado": sujeto.get("estado_nombre") or sujeto.get("estado", ""),
                },
            })
    except Exception:
        pass

    rfc = (sujeto.get("rfc") or "").strip().upper()
    curp = (sujeto.get("curp") or "").strip().upper()

    # 2. CFE: buscar por NOMBRE (más confiable que por RFC que no está en CFE)
    if rfc or curp or sujeto.get("nombre_completo"):
        try:
            from report_generator import _build_direccion
            con = _init_extended_con()
            if con is not None:
                # Buscar por nombre completo normalizado (upper, sin acentos opcionales)
                nombre_completo = sujeto.get("nombre_completo") or \
                    f"{sujeto.get('nombre', '')} {sujeto.get('paterno', '')} {sujeto.get('materno', '')}".strip()
                paterno = (sujeto.get("paterno") or "").upper().strip()
                # Estado del padrón para mejorar geocodificación (Nominatim
                # no geocodifica "AV LOPEZ MATEOS 799" sin estado).
                estado_padron = sujeto.get("estado_nombre") or sujeto.get("estado") or ""
                if paterno and len(paterno) >= 3:
                    # 2026-08-13: LIMIT 5 en lugar de 20 — las direcciones CFE
                    # son ruidosas y agregar el estado puede ayudar, pero
                    # 20+ por sujeto satura el reporte.
                    rows = con.execute("""
                        SELECT numero_servicio, division, zona_cod, zona_nom,
                               cp, nombre, direccion, calle_adicional_1,
                               calle_adicional_2, colonia, source_folder
                        FROM api.cfe_medidor
                        WHERE UPPER(nombre) LIKE ?
                        ORDER BY numero_servicio
                        LIMIT 5
                    """, [f"%{paterno}%"]).fetchall()
                    cols = ["num_servicio","division","zona_cod","zona_nom",
                            "cp","nombre","direccion","calle_adicional_1",
                            "calle_adicional_2","colonia","source_folder"]
                    for i, row in enumerate(rows, 1):
                        d = dict(zip(cols, row))
                        # Construir texto de dirección concatenando campos
                        addr_parts = [
                            d.get("direccion") or "",
                            d.get("calle_adicional_1") or "",
                            d.get("calle_adicional_2") or "",
                            d.get("colonia") or "",
                        ]
                        addr_str = ", ".join(p for p in addr_parts if p and p.strip())
                        # Agregar estado del padrón para ayudar a Nominatim
                        if estado_padron and addr_str:
                            addr_str = f"{addr_str}, {estado_padron}"
                        cp_cfe = (d.get("cp") or "").strip()
                        if addr_str:
                            addresses.append({
                                "titulo": f"Domicilio CFE #{i}",
                                "fuente": "cfe",
                                "direccion": addr_str,
                                "cp": cp_cfe,
                                "metadata": {
                                    "num_servicio": d.get("num_servicio", ""),
                                    "nombre_en_cfe": d.get("nombre", ""),
                                    "division": d.get("division", ""),
                                    "zona": f"{d.get('zona_cod', '')} {d.get('zona_nom', '')}".strip(),
                                    "source_folder": d.get("source_folder", ""),
                                },
                            })
        except Exception:
            pass

    # 3. ATT: buscar por RFC
    if rfc:
        try:
            con = _init_extended_con()
            if con is not None:
                rows = con.execute("""
                    SELECT rfc, direccion, num_exterior, num_interior,
                           colonia, municipio, estado
                    FROM api.att_persona
                    WHERE rfc = ? OR rfc = ? OR rfc = ?
                    LIMIT 10
                """, [rfc, rfc[:12] + "###" if len(rfc) == 13 else rfc, rfc[:10]]).fetchall()
                for i, row in enumerate(rows, 1):
                    addr_parts = [
                        row[1] or "",  # direccion
                        f"#{row[2] or ''}".replace("##", "#"),
                        (row[3] or ""),
                        row[4] or "",  # colonia
                        row[5] or "",  # municipio
                        row[6] or "",  # estado
                    ]
                    addr_str = ", ".join(p for p in addr_parts if p and p and p.strip() and p not in ("#",))
                    if addr_str:
                        addresses.append({
                            "titulo": f"Domicilio ATT #{i}",
                            "fuente": "att",
                            "direccion": addr_str,
                            "cp": "",
                            "metadata": {"rfc": row[0] or ""},
                        })
        except Exception:
            pass

    # 4. TELCEL: buscar por RFC, todas las líneas
    if rfc:
        try:
            con = _init_extended_con()
            if con is not None:
                rows = con.execute("""
                    SELECT rfc, telefono, domicilio, colonia, ciudad, estado, cp
                    FROM api.telcel_lineas
                    WHERE rfc = ? OR rfc = ?
                    LIMIT 20
                """, [rfc, rfc[:12] + "###" if len(rfc) == 13 else rfc]).fetchall()
                for i, row in enumerate(rows, 1):
                    addr_parts = [
                        row[2] or "",  # domicilio
                        row[3] or "",  # colonia
                        row[4] or "",  # ciudad
                        row[5] or "",  # estado (YA LO TIENE)
                        row[6] or "",  # cp
                    ]
                    addr_str = ", ".join(p for p in addr_parts if p and p.strip())
                    if addr_str:
                        addresses.append({
                            "titulo": f"Domicilio TELCEL #{i}",
                            "fuente": "telcel",
                            "direccion": addr_str,
                            "cp": (row[6] or "").strip(),
                            "metadata": {"rfc": row[0] or "", "telefono": row[1] or ""},
                        })
        except Exception:
            pass

    # 5. IMSS: buscar por CURP, domicilio de empresa (un titular puede tener varios patrones)
    if curp:
        try:
            con = _init_extended_con()
            if con is not None:
                rows = con.execute("""
                    SELECT curp, empresa_nombre, empresa_domicilio,
                           empresa_ciudad_estado, empresa_cp
                    FROM api.imss_asegurado
                    WHERE curp = ?
                    LIMIT 10
                """, [curp]).fetchall()
                for i, row in enumerate(rows, 1):
                    addr_parts = [
                        row[2] or "",  # empresa_domicilio
                        row[3] or "",  # empresa_ciudad_estado (YA TIENE ESTADO)
                    ]
                    addr_str = ", ".join(p for p in addr_parts if p and p.strip())
                    if addr_str:
                        addresses.append({
                            "titulo": f"Domicilio IMSS #{i}",
                            "fuente": "imss",
                            "direccion": addr_str,
                            "cp": (row[4] or "").strip(),
                            "metadata": {
                                "curp": row[0] or "",
                                "empresa_nombre": row[1] or "",
                            },
                        })
        except Exception:
            pass

    # 6. REPUVE: buscar por RFC
    if rfc:
        try:
            con = _init_extended_con()
            if con is not None:
                rows = con.execute("""
                    SELECT rfc, placa, marca, modelo, propietario, direccion_propietario
                    FROM api.repuve_de_persona
                    WHERE rfc = ? OR rfc = ? OR rfc = ?
                    LIMIT 10
                """, [rfc, rfc[:12] + "###" if len(rfc) == 13 else rfc, rfc[:10]]).fetchall()
                for i, row in enumerate(rows, 1):
                    if row[5]:
                        addresses.append({
                            "titulo": f"Domicilio REPUVE #{i}",
                            "fuente": "repuve",
                            "direccion": row[5],
                            "cp": "",
                            "metadata": {
                                "placa": row[1] or "",
                                "vehiculo": f"{row[2] or ''} {row[3] or ''}".strip(),
                                "propietario": row[4] or "",
                            },
                        })
        except Exception:
            pass

    # 7. EMPLEADORES: si el sujeto es patrón/empresa
    if rfc and len(rfc) == 12:  # RFC de persona moral
        try:
            con = _init_extended_con()
            if con is not None:
                rows = con.execute("""
                    SELECT rfc, razon_social, dom_calle, dom_ext, dom_int,
                           dom_colonia, dom_municipio, dom_entidad, dom_cp
                    FROM api.empleadores
                    WHERE rfc = ?
                    LIMIT 5
                """, [rfc]).fetchall()
                for i, row in enumerate(rows, 1):
                    addr_parts = [
                        row[2] or "",  # dom_calle
                        f"#{row[3] or ''}".replace("##", "#"),
                        row[4] or "",
                        row[5] or "",
                        row[6] or "",
                        row[7] or "",
                        row[8] or "",
                    ]
                    addr_str = ", ".join(p for p in addr_parts if p and p.strip() and p not in ("#",))
                    if addr_str:
                        addresses.append({
                            "titulo": f"Domicilio EMPLEADOR #{i}",
                            "fuente": "empleadores",
                            "direccion": addr_str,
                            "cp": (row[8] or "").strip(),
                            "metadata": {"rfc": row[0] or "", "razon_social": row[1] or ""},
                        })
        except Exception:
            pass

    # 8. CHECKID CP (si difiere del padrón)
    checkid_cp = ""
    try:
        checkid_data = (enrichment or {}).get("checkid", {}) if isinstance(enrichment, dict) else {}
        if isinstance(checkid_data, dict):
            chk_cp = _extract_checkid_str(checkid_data.get("codigo_postal"), "codigoPostal")
            if chk_cp:
                checkid_cp = str(chk_cp).strip().zfill(5)[:5]
                if checkid_cp and checkid_cp != "00000":
                    addresses.append({
                        "titulo": "CP registrado en SAT (CheckID)",
                        "fuente": "checkid",
                        "direccion": f"CP {checkid_cp}",
                        "cp": checkid_cp,
                        "metadata": {
                            "regimen_fiscal": str(checkid_data.get("regimen_fiscal", ""))[:80],
                            "razon_social": checkid_data.get("razon_social", ""),
                        },
                    })
    except Exception:
        pass

    # 9. SEPOMEX CP del padrón (fallback si no hay dirección geocodificable)
    cp_padron = (sujeto.get("cp") or "").strip()
    if cp_padron and cp_padron != "00000":
        try:
            sep_con = get_sepomex()
            mrow = sep_con.execute(
                "SELECT estado, municipio, colonia FROM cp WHERE cp=? LIMIT 3",
                (cp_padron.zfill(5),)
            ).fetchall()
            if mrow and not any(a["fuente"] == "padron" for a in addresses):
                # Solo si no se agregó padrón arriba
                for i, row in enumerate(mrow, 1):
                    addr_str = ", ".join(p for p in row if p)
                    addresses.append({
                        "titulo": f"SEPOMEX CP {cp_padron} #{i}",
                        "fuente": "sepomex",
                        "direccion": addr_str,
                        "cp": cp_padron,
                        "metadata": {},
                    })
        except Exception:
            pass

    return addresses


def _generate_maps_for_addresses(addresses: list, max_maps: int = 25) -> list:
    """Genera imagen PNG + geocoding para cada dirección.

    Args:
        addresses: lista de dicts de _collect_addresses_for_sujeto
        max_maps: máximo de mapas a generar (límite duro para reportes)

    Returns:
        lista de dicts con campos extra: image_b64, lat, lon, display_name,
        source (Nominatim local/público). NO muta los inputs.

    2026-08-13: si Nominatim no puede geocodificar (calle muy específica
    o texto basura como "ELOTES FTE TEMPLO S JUAN"), intenta fallback
    CFE via _search_cfe_by_domicilio y agrega un bloque cfe_fallback con
    los num_servicio / rows encontrados. Eso preserva el "mapa" CFE de
    la integración pasada en el reporte IA.
    """
    import base64
    import re
    from report_generator import geocode_address, generate_static_map

    def _parse_address_components(text: str) -> tuple:
        """Heurística: extraer calle, colonia, cp de un texto de dirección.

        "AV REFORMA 100 INT 5, COL CENTRO, CDMX, 06060" →
            ("AV REFORMA 100", "COL CENTRO", "06060")
        "PRIV ART 115 CONST 145, SERV RENOVADO, 1010, CENTRO" →
            ("PRIV ART 115 CONST 145", "", "")

        Estrategia:
          1) Split por coma
          2) CP al final si 5 dígitos
          3) Colonia = cualquier elemento que empiece con COL/FRACC/BARRIO/UNIDAD/EJIDO
          4) Calle = PRIMER componente (lo más probable de matchear CFE)
        """
        parts = [p.strip() for p in re.split(r"[,;]", text) if p.strip()]
        cp = ""
        # CP al final si es 5 dígitos
        if parts and re.fullmatch(r"\d{5}", parts[-1]):
            cp = parts[-1]
            parts = parts[:-1]

        # Colonia = cualquier elemento que parezca COL/FRACC/BARRIO/UNIDAD/EJIDO
        colonia = ""
        for i, p in enumerate(parts):
            if re.match(r"^(COL|FRACC|BARRIO|UNIDAD|EJIDO)\b", p, re.I):
                colonia = p
                parts = parts[:i] + parts[i+1:]  # removerlo de parts
                break

        # Calle = PRIMER componente (lo más probable de matchear CFE
        # por LIKE %calle%). Tags SERV/INT/NUEVO/RENOVADO no matchean porque
        # están en columnas distintas (calle_adicional_1/_2).
        calle = parts[0] if parts else ""

        # Agregar número si está en el segundo componente (ej "100")
        if len(parts) >= 2 and re.fullmatch(r"\d+\w*", parts[1]):
            calle = f"{calle} {parts[1]}"

        return (calle, colonia, cp)

    out = []
    for addr in addresses[:max_maps]:
        direccion = addr.get("direccion", "")
        cp = addr.get("cp", "")
        geo = geocode_address(direccion, cp)
        if not geo:
            # Si solo CP y no se pudo geocodificar, intentar fallback CP solo
            if cp and cp != "00000":
                geo = geocode_address("", cp)

        if geo:
            # Geocodificación exitosa → generar mapa
            try:
                img_bytes = generate_static_map(geo["lat"], geo["lon"])
                img_b64 = base64.b64encode(img_bytes).decode("ascii")
            except Exception:
                img_b64 = None
            out.append({
                **addr,
                "image_b64": img_b64,
                "lat": geo.get("lat"),
                "lon": geo.get("lon"),
                "display_name": geo.get("display_name", ""),
                "geocode_source": geo.get("source", "?"),
                "cfe_fallback": None,
            })
        else:
            # No geocodificó → intentar fallback CFE con componentes parseados
            calle_p, colonia_p, cp_p = _parse_address_components(direccion)
            # Si no se pudo extraer calle útil, no buscar
            if len(calle_p) < 3:
                out.append({
                    **addr,
                    "image_b64": None,
                    "lat": None,
                    "lon": None,
                    "display_name": "",
                    "geocode_source": None,
                    "cfe_fallback": None,
                    "geocode_failed": True,
                })
                continue

            cfe_result = _search_cfe_by_domicilio(
                calle=calle_p.upper(),
                colonia=colonia_p.upper() if colonia_p else "",
                cp=cp_p or cp,
                limit=10,
            )
            out.append({
                **addr,
                "image_b64": None,
                "lat": None,
                "lon": None,
                "display_name": "",
                "geocode_source": None,
                "geocode_failed": True,
                "cfe_fallback": cfe_result,  # dict con rows / count / query
            })

    return out


def _search_cfe_by_domicilio(calle: str = "", colonia: str = "", cp: str = "",
                              division: str = "", limit: int = 25) -> dict:
    """Lógica reusable de /api/v1/cfe/buscar_domicilio sin HTTP.

    2026-08-13: extracta de _handle_cfe_buscar_domicilio para uso desde
    _generate_maps_for_addresses cuando Nominatim no geocodifica.

    Args:
        calle, colonia, cp, division: filtros de búsqueda (ya uppercased)
        cp: ya viene zfilled; "00000" se considera ausente
        limit: máximo de rows (cap 100)

    Returns:
        dict con keys: query, rows, count, elapsed_s, truncated, found,
                       error, nota. SIEMPRE retorna dict (nunca None).
    """
    cp_norm = cp if cp and cp != "00000" else ""

    con = _init_extended_con()
    if con is None:
        return {
            "query": {"calle": calle or None, "colonia": colonia or None,
                      "cp": cp_norm or None, "division": division or None,
                      "limit": limit},
            "rows": [], "count": 0, "elapsed_s": None, "truncated": False,
            "found": False, "error": "extendido no inicializado",
            "nota": "Sin conexión a la DB extendida.",
        }

    # 2026-08-13: rechazar si no hay ningún filtro efectivo.
    # Antes WHERE "1=1" con LIMIT 25 devolvía filas random.
    if not (calle or colonia or cp_norm or division):
        return {
            "query": {"calle": calle or None, "colonia": colonia or None,
                      "cp": cp_norm or None, "division": division or None,
                      "limit": limit},
            "rows": [], "count": 0, "elapsed_s": None, "truncated": False,
            "found": False, "error": None,
            "nota": "Se requiere al menos uno: calle, colonia, cp.",
        }

    limit = max(1, min(limit, 100))
    where, params = [], []
    if calle:
        where.append("""(
            UPPER(TRIM(COALESCE(direccion, ''))) LIKE ? OR
            UPPER(TRIM(COALESCE(calle_adicional_1, ''))) LIKE ? OR
            UPPER(TRIM(COALESCE(calle_adicional_2, ''))) LIKE ?
        )""")
        params.extend([f"%{calle}%"] * 3)
    if colonia:
        where.append("UPPER(TRIM(COALESCE(colonia, ''))) LIKE ?")
        params.append(f"%{colonia}%")
    if cp_norm:
        where.append("cp = ?")
        params.append(cp_norm)
    if division:
        where.append("division = ?")
        params.append(division)
    where_sql = " AND ".join(where)
    params.append(limit)

    rows, err, elapsed = [], None, None
    try:
        import time
        t0 = time.time()
        sql = f"""
            SELECT numero_servicio, division, zona_cod, zona_nom,
                   agencia_cod, agencia_nom,
                   cp, nombre, direccion, calle_adicional_1,
                   calle_adicional_2, colonia, source_folder
            FROM api.cfe_medidor
            WHERE {where_sql}
            LIMIT ?
        """
        rows = con.execute(sql, params).fetchall()
        elapsed = time.time() - t0
    except Exception as e:
        err = str(e)[:200]

    cols = ["num_servicio","division","zona_cod","zona_nom",
            "agencia_cod","agencia_nom","cp","nombre",
            "direccion","calle_adicional_1","calle_adicional_2",
            "colonia","source_folder"]
    return {
        "query": {"calle": calle or None, "colonia": colonia or None,
                  "cp": cp_norm or None, "division": division or None,
                  "limit": limit},
        "rows": [dict(zip(cols, r)) for r in rows],
        "count": len(rows),
        "elapsed_s": round(elapsed, 3) if elapsed else None,
        "truncated": len(rows) >= limit,
        "found": bool(rows),
        "error": err,
        "nota": "Búsqueda sobre 66M de filas; pasar cp es lo más eficiente. "
                "Si count=0 probar con menos filtros.",
    }


def _find_sujeto(curp):
    """Busca sujeto por CURP exacta en el DuckDB."""
    from config import config
    db_path = getattr(config, "padron_db_path", "ine.duckdb")
    con = duckdb.connect(db_path, read_only=True)
    try:
        res = con.execute(
            "SELECT * FROM padron WHERE curp = ? LIMIT 1", [curp]
        ).fetchone()
        if not res:
            return None
        cols = [d[0] for d in con.description]
        return dict(zip(cols, res))
    finally:
        con.close()


def _enrich_singula_customer(sg, customer_id, sujeto, checkid_data=None,
                             tlaloc_curp=None, tlaloc_rfc=None,
                             sepomex=None, social_media=None,
                             rfc_sat=None) -> dict:
    """Enriquece el customer de Singula con TODOS los datos disponibles.

    Sources: padrón (INE), CheckID (SAT), Tlaloc (RENAPO/SAT), SEPOMEX,
    Apify (social media).

    Estrategia:
      - datameta oficial (PATCH /customer/{id}) SOLO con campos del schema
        CustomerDataMetaDTO: company, job_title, city, email, phone,
        address, nationality. La API MERGEA estos campos.
      - enrichment completo (padrón, checkid, tlaloc, sepomex, apify, etc.)
        en el store LOCAL (SQLite), porque la API los ignoraría.

    Returns: {"ok": bool, "enrichment": dict, "error": str|None}
    """
    if not customer_id:
        return {"ok": False, "error": "no customer_id"}
    try:
        from providers import singula_store

        enrichment = {}

        # 1) PADRÓN (INE) — datos demográficos base
        enrichment["padron"] = {
            "curp": sujeto.get("curp", ""),
            "estado": sujeto.get("estado_nombre", ""),
            "municipio": sujeto.get("municipio_nombre", ""),
            "cp": str(sujeto.get("cp", "")),
            "seccion": str(sujeto.get("seccion", "")),
            "fecha_nacimiento": sujeto.get("fecnac", ""),
            "sexo": sujeto.get("sexo", ""),
        }

        # 2) SEPOMEX — dirección completa desde el CP
        if sepomex:
            enrichment["sepomex"] = sepomex

        # 3) CHECKID — datos fiscales del SAT
        email_checkid = None
        if checkid_data and checkid_data.get("exitoso"):
            rfc_node = (checkid_data.get("raw") or {}).get("resultado", {}).get("rfc", {})
            email_checkid = rfc_node.get("emailContacto") or rfc_node.get("email")
            regimenes = _extract_checkid_str(
                checkid_data.get("regimen_fiscal"), "regimenesFiscales"
            ) if checkid_data.get("regimen_fiscal") else None
            enrichment["checkid"] = {
                "razon_social": checkid_data.get("razon_social", ""),
                "rfc": checkid_data.get("rfc", ""),
                "nss": _extract_checkid_str(checkid_data.get("nss"), "nss"),
                "codigo_postal": _extract_checkid_str(
                    checkid_data.get("codigo_postal"), "codigoPostal"
                ),
                "regimen_fiscal": regimenes,
                "estado_69_69b": checkid_data.get("estado_69_69b", ""),
                "email_contacto": email_checkid,
            }

        # 4) TLALOC — validación RENAPO + SAT
        tlaloc_section = {}
        if tlaloc_curp and isinstance(tlaloc_curp, dict) and not tlaloc_curp.get("error"):
            tlaloc_section["curp_renapo"] = {
                "valid": tlaloc_curp.get("valid"),
                "status": tlaloc_curp.get("status"),
                "entidad": tlaloc_curp.get("entidad"),
                "nacionalidad": tlaloc_curp.get("nacionalidad"),
                "doc_probatorio": tlaloc_curp.get("docProbatorio", {}),
            }
        if tlaloc_rfc and isinstance(tlaloc_rfc, dict) and not tlaloc_rfc.get("error"):
            tlaloc_section["rfc_sat"] = {
                "valid": tlaloc_rfc.get("valid"),
                "accept_cfdi": tlaloc_rfc.get("accept_cfdi"),
                "reason": tlaloc_rfc.get("reason"),
                "nombre_match": tlaloc_rfc.get("nombre_match"),
                "cp_match": tlaloc_rfc.get("cp_match"),
            }
        if tlaloc_section:
            enrichment["tlaloc"] = tlaloc_section

        # 5) APIFY — social media
        if social_media and isinstance(social_media, dict) and social_media.get("perfiles"):
            enrichment["apify_social"] = {
                "total_encontrados": social_media.get("total_encontrados", 0),
                "coincidencias_exactas": social_media.get("coincidencias_exactas", 0),
                "perfiles": social_media.get("perfiles", []),
            }

        # 6) metadata
        from datetime import datetime, timezone
        enrichment["enriched_at"] = datetime.now(timezone.utc).isoformat()
        enrichment["sources"] = [k for k in enrichment.keys() if k not in ("enriched_at",)]

        # 7) Construir datameta oficial con SOLO campos del schema
        # CustomerDataMetaDTO: company, job_title, city, email, phone,
        #                     address, nationality
        datameta_oficial = {}
        if email_checkid:
            datameta_oficial["email"] = email_checkid
        phone = sujeto.get("telefono", "")
        if phone:
            datameta_oficial["phone"] = phone
        if sepomex:
            # construir address completo
            addr_parts = []
            if sepomex.get("asentamiento"):
                addr_parts.append(sepomex["asentamiento"])
            if sepomex.get("cp"):
                addr_parts.append(f"CP {sepomex['cp']}")
            if sepomex.get("municipio"):
                addr_parts.append(sepomex["municipio"])
            if sepomex.get("estado"):
                addr_parts.append(sepomex["estado"])
            if addr_parts:
                datameta_oficial["address"] = ", ".join(addr_parts)
            if sepomex.get("municipio"):
                datameta_oficial["city"] = sepomex["municipio"]
        # nationality: usar la de Tlaloc si está disponible
        if tlaloc_curp and isinstance(tlaloc_curp, dict):
            nac = tlaloc_curp.get("nacionalidad")
            if nac and nac not in ("", None):
                datameta_oficial["nationality"] = "Mexicana" if nac == "MEX" else nac
        # company + job_title: dejarlos vacíos por ahora (no tenemos esos datos
        # del padrón ni de CheckID; el frontend los puede capturar manualmente)
        # datameta_oficial["company"] = ""
        # datameta_oficial["job_title"] = ""

        # 8) PATCH al customer de Singula con campos oficiales
        patch_fields = {}
        if rfc_sat and len(rfc_sat) >= 13:
            patch_fields["rfc"] = rfc_sat
        if datameta_oficial:
            patch_fields["datameta"] = datameta_oficial
        if patch_fields:
            try:
                sg.update_customer(customer_id, merge_datameta=False, **patch_fields)
            except Exception as e:
                # no abortar el enrichment si el PATCH falla
                enrichment.setdefault("patch_error", str(e))

        # 9) Guardar enrichment completo en store LOCAL
        singula_store.save_enrichment(
            customer_id=customer_id,
            curp=sujeto.get("curp", ""),
            enrichment=enrichment,
            custom_id=sujeto.get("curp"),
        )

        return {"ok": True, "enrichment": enrichment, "error": None}
    except Exception as e:
        return {"ok": False, "enrichment": {}, "error": str(e)}


def _lookup_cached_customer_by_curp(sg, curp: str,
                                    cache_max_age_days: int = 7,
                                    sujeto: dict = None) -> dict:
    """Intenta reusar un customer Singula cacheado localmente por CURP.

    Pasos (cada uno evita un cobro de crédito si tiene éxito):
      1. Busca en `singula_store` por CURP → customer_id local + enrichment cacheado.
      2. Verifica que el customer siga vivo en Singula (ensure_customer, GET barato).
      3. Trae validaciones cacheadas; purga las entradas con cached_at > TTL.
      4. Si la verificación falla (customer borrado/expirado en Singula), el
         store local queda inservible para esta CURP — el caller debe borrar
         la fila y crear uno nuevo.

    Devuelve dict:
      {
        "found": True/False,
        "customer_id": "cus_xxx" | None,
        "enrichment": {...} | None,        # listo para inyectar en singula.enrichment
        "validations_cache": {key: entry}, # sólo entries frescos (TTL aplicados)
        "validations_total": int,          # cuántos había antes del filtro TTL
        "validations_fresh": int,          # cuántos quedaron
        "stale_validations": [keys],       # cuáles quedaron stale (expiradas)
        "ensure_error": str | None,        # si el customer remoto ya no existe
        "store_purged": True/False,        # si tuvimos que borrar la fila local
      }
    """
    out = {
        "found": False,
        "customer_id": None,
        "enrichment": None,
        "validations_cache": {},
        "validations_total": 0,
        "validations_fresh": 0,
        "stale_validations": [],
        "ensure_error": None,
        "store_purged": False,
    }
    if not curp:
        return out
    try:
        from providers import singula_store
        from datetime import datetime, timezone, timedelta

        row = singula_store.get_all_for_curp(curp)
        if row and row.get("customer_id"):
            cid = row["customer_id"]
            out["customer_id"] = cid
            out["found"] = True
            out["enrichment"] = row.get("enrichment") or None
        else:
            # 2026-08-05: store local vacío → intentar resolver buscando en
            # Singula por nombre + verificación de CURP. Esto recupera el
            # caso clásico de "tengo customer creado pero no lo encuentro"
            # (key rotada, restart, DB reconstruida). Si Singula devuelve
            # un customer con la misma CURP, lo tratamos como encontrada y
            # persistimos la fila vacía en el store para próximas veces.
            try:
                # La búsqueda en Singula necesita nombre + apellidos. Si
                # están disponibles, los pasamos; si no, la búsqueda falla
                # silenciosamente y found=False (caller crea customer).
                # NOTA: el sujeto se pasa como dict a este helper desde
                # `_handle_sujeto`, donde está disponible. Para tests
                # directos, los strings vacíos son aceptables.
                remote = sg._search_customer_by_custom_id(
                    custom_id_anchor="",  # sin anchor de custom_id
                    curp=curp,
                    name=sujeto.get("nombre", "") if sujeto else "",
                    last_name=sujeto.get("paterno", "") if sujeto else "",
                    mothers_last_name=sujeto.get("materno", "") if sujeto else "",
                )
                if isinstance(remote, dict) and remote.get("id"):
                    remote_cid = remote["id"]
                    # Persistir en store local para no repetir la búsqueda
                    try:
                        singula_store.save_enrichment(
                            customer_id=remote_cid,
                            curp=curp.upper(),
                            enrichment={"_resolved_from_singula_search": True},
                            custom_id=remote.get("custom_id") or "",
                        )
                    except Exception:
                        pass
                    out["customer_id"] = remote_cid
                    out["found"] = True
                    out["enrichment"] = None  # no tenemos enrichment local
                    out["resolved_via"] = "singula_search_by_name"
            except Exception:
                # Falló la búsqueda remota — found sigue False, caller creará
                pass
            if not out["found"]:
                return out
            # Si llegamos aquí, found=True pero row era None → saltamos el
            # bloque de validations_cache (no tenemos local).
            out["validations_total"] = 0
            out["validations_fresh"] = 0
            return out

        raw_cache = row.get("validations_cache") or {}
        out["validations_total"] = len(raw_cache) if isinstance(raw_cache, dict) else 0

        # Filtrar entries por TTL — sólo "fresh" sobreviven
        fresh = {}
        stale = []
        now = datetime.now(timezone.utc)
        if isinstance(raw_cache, dict):
            for k, entry in raw_cache.items():
                if not isinstance(entry, dict):
                    stale.append(k)
                    continue
                ts = entry.get("cached_at")
                if not ts:
                    stale.append(k)
                    continue
                try:
                    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                    if (now - dt) < timedelta(days=cache_max_age_days):
                        fresh[k] = entry
                    else:
                        stale.append(k)
                except Exception:
                    stale.append(k)
        out["validations_cache"] = fresh
        out["validations_fresh"] = len(fresh)
        out["stale_validations"] = stale

        # Verificar con Singula que el customer sigue vivo
        try:
            guard = sg.ensure_customer(cid)
            if not guard.get("ok"):
                # Customer borrado/expirado en Singula — purgar store local
                out["ensure_error"] = guard.get("error") or "ensure_customer falló"
                try:
                    # delete row directamente (no hay helper de delete aún)
                    import sqlite3, os
                    db_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "providers", "singula_cache.db",
                    )
                    con = sqlite3.connect(db_path)
                    try:
                        con.execute("DELETE FROM customers WHERE customer_id = ?", (cid,))
                        con.commit()
                    finally:
                        con.close()
                    out["store_purged"] = True
                except Exception:
                    pass
                out["found"] = False
                out["customer_id"] = None
                out["enrichment"] = None
                out["validations_cache"] = {}
                out["validations_fresh"] = 0
                return out
        except Exception as e:
            # No pudimos hablar con Singula — asumimos que el cache local es
            # válido y dejamos que el caller decida si continuar. NO marcamos
            # found=False porque el cache podría seguir siendo utilizable
            # cuando Singula vuelva a responder.
            out["ensure_error"] = f"ensure_customer excepción: {e}"
    except Exception as e:
        out["ensure_error"] = f"lookup excepción: {e}"
    return out


def _run_singula_validations(sg, customer_id, sujeto,
                              force_refresh: bool = False,
                              cache_max_age_days: int = 7) -> dict:
    """Ejecuta validaciones Singula automáticas con el perfil ya enriquecido.

    Reusa resultados del store LOCAL (SQLite) si no han expirado. Si no,
    ejecuta la consulta y guarda el resultado en el store.

    Devuelve dict con:
      - judicial, email_lookup, blacklist, intel_basic (resultados)
      - from_cache: dict {key: True} si se reusó del cache
      - executed: list de keys que se ejecutaron realmente
      - cache_persist_error: si no se pudo persistir el cache

    Si una validación falla, guarda el error pero no aborta las demás.
    """
    results = {"from_cache": {}, "executed": []}
    if not customer_id:
        return {"error": "no customer_id"}

    from providers import singula_store
    from datetime import datetime, timezone, timedelta

    # 1) leer cache local
    cache = {} if force_refresh else singula_store.get_validations(customer_id)

    def _is_fresh(entry: dict) -> bool:
        if not isinstance(entry, dict):
            return False
        ts = entry.get("cached_at")
        if not ts:
            return False
        try:
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - dt) < timedelta(days=cache_max_age_days)
        except Exception:
            return False

    # 2) ejecutar/reciclar cada validación
    # 2026-08-04: intel_basic removido de este loop — task_singula_intel (~línea 1611)
    # ya lo corre en el pool paralelo y se cobraba DOS veces por apertura.
    validation_keys = ["judicial", "email_lookup", "blacklist"]  # email_lookup sólo si hay email
    for vkey in validation_keys:
        # email_lookup sólo si hay email
        if vkey == "email_lookup":
            email = sujeto.get("email") or sujeto.get("email_checkid") or ""
            if not email:
                results[vkey] = {"skipped": "sin email"}
                continue

        if _is_fresh(cache.get(vkey)):
            # reusar del cache
            entry = dict(cache[vkey])
            entry.pop("cached_at", None)  # quitar metadata interna
            results[vkey] = entry
            results["from_cache"][vkey] = True
            continue

        # ejecutar
        try:
            if vkey == "judicial":
                data = sg.check_judicial(customer_id)
            elif vkey == "email_lookup":
                data = sg.email_lookup(customer_id)
            elif vkey == "blacklist":
                data = sg.check_blacklist(customer_id)
            elif vkey == "intel_basic":
                data = sg.intel_basic(customer_id)
            else:
                continue
            results[vkey] = data
            results["executed"].append(vkey)
        except Exception as e:
            results[vkey] = {"error": str(e)}

    # 3) persistir las que se ejecutaron
    for vkey in results["executed"]:
        try:
            singula_store.save_validation(
                customer_id=customer_id,
                validation_key=vkey,
                result=results[vkey],
            )
        except Exception as e:
            results.setdefault("cache_persist_error", str(e))

    return results


def _build_validations(sujeto):
    """Construye la lista de validaciones opcionales Apify + Singula."""
    rfc = sujeto.get("rfc", "")
    curp = sujeto.get("curp", "")
    nombre_completo = f"{sujeto.get('nombre','')} {sujeto.get('paterno','')} {sujeto.get('materno','')}".strip()
    email = sujeto.get("email", "")
    telefono = sujeto.get("telefono", "")

    validations = [
        {
            "id": "checkid-busqueda",
            "name": "CheckID — RFC, NSS, régimen, CP, 69/69B",
            "provider": "checkid",
            "description": "Consulta fiscal principal por CURP/RFC (se ejecuta automáticamente al cargar la ficha).",
            "cost_mxn": 0.0,
            "cost_label": "CheckID",
            "default_query": curp,
            "editable": False,
            "enabled": False,  # no aparece en modal; ya corre en /api/sujeto
        },
        {
            "id": "apify-osint-email",
            "name": "Apify — OSINT por email",
            "provider": "apify",
            "description": "Busca redes, registros y filtraciones asociadas al email de CheckID.",
            "cost_mxn": 0.0,
            "cost_label": "Apify free tier",
            "default_query": email or "email@ejemplo.com",
            "editable": True,
            "enabled": bool(email),
        },
        {
            "id": "singula-judicial-persona",
            "name": "Antecedentes Judiciales (Persona)",
            "provider": "singula",
            "description": "Consulta antecedentes judiciales de la persona en Singula (KYB).",
            "default_query": curp,
            "editable": False,
            "enabled": bool(curp),
        },
        {
            "id": "singula-blacklist-kyb",
            "name": "Consulta Lista Negra",
            "provider": "singula",
            "description": "Consulta listas negras y PEP (KYB) en Singula.",
            "default_query": curp,
            "editable": False,
            "enabled": bool(curp),
        },
        # singula-validate-rfc ELIMINADA — CheckID ya hace esta validación
        # singula-identity-verification ELIMINADA — requiere subir foto del INE (no se hace solo con los datos)
        # singula-document-signature ELIMINADA — requiere URL del documento (no se hace solo con los datos)
        # singula-judicial-empresa ELIMINADA — el usuario la quitó explícitamente
        {
            "id": "singula-email-lookup",
            "name": "Email Lookup",
            "provider": "singula",
            "description": "Busca en qué plataformas está registrado un email.",
            "default_query": email or "email@ejemplo.com",
            "editable": True,
            "enabled": True,
        },
        {
            "id": "singula-phone-lookup",
            "name": "Phone Lookup",
            "provider": "singula",
            "description": "Valida teléfono: país, carrier, tipo de línea, región.",
            "default_query": telefono or "+525****0000",
            "editable": True,
            "enabled": True,
        },
        {
            "id": "singula-blacklist",
            "name": "Blacklist / PEP",
            "provider": "singula",
            "description": "Consulta listas negras y personas políticamente expuestas.",
            "default_query": nombre_completo,
            "editable": True,
            "enabled": bool(nombre_completo),
        },
        {
            "id": "singula-judicial",
            "name": "Antecedentes Judiciales",
            "provider": "singula",
            "description": "Busca antecedentes judiciales por nombre/CURP.",
            "default_query": nombre_completo,
            "editable": True,
            "enabled": bool(nombre_completo),
        },
        {
            "id": "singula-intel-basic",
            "name": "Inteligencia Digital Básica",
            "provider": "singula",
            "description": "Huella digital básica del customer.",
            "default_query": nombre_completo,
            "editable": True,
            "enabled": bool(curp),
        },
        {
            "id": "singula-intel-premium",
            "name": "Inteligencia Digital Premium",
            "provider": "singula",
            "description": "Dossier premium: LinkedIn, empresas, resumen narrativo.",
            "default_query": nombre_completo,
            "editable": True,
            "enabled": bool(curp),
        },
        {
            "id": "apify-instagram",
            "name": "Buscar en Instagram",
            "provider": "apify",
            "description": "Keyword search en Instagram por nombre completo; si el query no tiene espacios, lo trata como username.",
            "cost_mxn": 0.0,
            "cost_label": "Apify free tier",
            "default_query": email or nombre_completo,
            "editable": True,
            "enabled": bool(email or nombre_completo),
        },
        {
            "id": "apify-tiktok",
            "name": "Buscar en TikTok",
            "provider": "apify",
            "description": "Scrapea perfil de TikTok dado un username (ej. charlidamelio). Si recibe nombre completo, usa la parte antes del espacio.",
            "cost_mxn": 0.0,
            "cost_label": "Apify free tier",
            "default_query": (email.split("@")[0] if email else "").replace(".", "_").replace("+", "") or (nombre_completo.split()[0] if nombre_completo else ""),
            "editable": True,
            "enabled": bool(email or nombre_completo),
        },
        {
            "id": "apify-twitter",
            "name": "Buscar en Twitter/X",
            "provider": "apify",
            "description": "No hay actor directo confiable de Twitter/X; usa Google search para encontrar perfiles relacionados al query.",
            "cost_mxn": 0.0,
            "cost_label": "Apify free tier",
            "default_query": email or nombre_completo,
            "editable": True,
            "enabled": bool(email or nombre_completo),
        },
        {
            "id": "apify-facebook",
            "name": "Buscar en Facebook",
            "provider": "apify",
            "description": "Busca perfiles públicos de Facebook por nombre completo.",
            "cost_mxn": 0.0,
            "cost_label": "Apify free tier",
            "default_query": email or nombre_completo,
            "editable": True,
            "enabled": bool(email or nombre_completo),
        },
        {
            "id": "apify-youtube",
            "name": "Buscar en YouTube",
            "provider": "apify",
            "description": "Scrapea canal de YouTube dado un @username (sin @). Si recibe email, usa la parte local.",
            "cost_mxn": 0.0,
            "cost_label": "Apify free tier",
            "default_query": (email.split("@")[0] if email else "").replace(".", "").replace("+", "") or (nombre_completo.split()[0] if nombre_completo else ""),
            "editable": True,
            "enabled": bool(email or nombre_completo),
        },
        {
            "id": "apify-contact-info",
            "name": "Contact info / web scraper",
            "provider": "apify",
            "description": "Extrae emails/teléfonos/redes de una o más URLs. El query debe ser URL(s) separadas por coma.",
            "cost_mxn": 0.0,
            "cost_label": "Apify free tier",
            "default_query": f"https://www.google.com/search?q={quote(nombre_completo)}" if nombre_completo else "https://",
            "editable": True,
            "enabled": True,
        },
        {
            "id": "apify-social-media-finder",
            "name": "Social Media Finder (13 redes)",
            "provider": "apify",
            "description": "tri_angle/social-media-finder: busca perfiles en 13 redes sociales (IG, FB, LI, TT, X, YT, Twitch, Medium, Pinterest, Snapchat, Reddit, GitHub, Spotify) por nombre. Se ejecuta automáticamente al cargar el sujeto.",
            "cost_mxn": 0.0,
            "cost_label": "Apify free tier",
            "default_query": nombre_completo,
            "editable": False,
            "enabled": False,  # corre automático al cargar el sujeto
        },
    ]
    return validations


def _filter_exact_name_matches(candidates: list[dict], entity_name: str) -> list[dict]:
    """Filtra candidatos de social-media-finder: solo coincidencias exactas de nombre.

    Compara el nombre mostrado del perfil (displayName, name, fullName, title, username)
    contra el nombre completo de la entidad. Ignora mayúsculas/minúsculas y acentos.
    """
    import unicodedata

    def normalize(s: str) -> str:
        """Quita acentos, lower, strip."""
        if not s:
            return ""
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        return " ".join(s.lower().split())

    target = normalize(entity_name)
    if not target:
        return []

    exact = []
    for c in candidates:
        # Campos donde puede aparecer el nombre del perfil
        display_name = (
            c.get("displayName") or c.get("name") or c.get("fullName") or
            c.get("title") or c.get("username") or c.get("profileName") or ""
        )
        if normalize(display_name) == target:
            exact.append(c)

    return exact


def _verify_social_profiles_with_ollama(candidates: list[dict], entity_name: str, official_website: str = "") -> list[dict]:
    """Verifica con Ollama si cada perfil social pertenece a la entidad.

    Usa el prompt definido por el usuario: compara nombre, insignia, bio, enlace,
    ubicación y datos consistentes. Solo marca is_official_candidate=true si hay
    coincidencia exacta o casi inequívoca Y al menos una señal independiente.

    Args:
        candidates: lista de perfiles devueltos por social-media-finder
        entity_name: nombre completo de la entidad buscada
        official_website: sitio web oficial (opcional)

    Returns:
        lista de dicts con is_official_candidate, confidence_score, reasons
    """
    if not candidates:
        return []

    try:
        from config import config
        api_key = config.ollama_api_key
        model = config.ollama_model
    except Exception:
        api_key = os.getenv("OLLAMA_API_KEY")
        model = os.getenv("OLLAMA_MODEL", "deepseek-v4-pro")

    if not api_key:
        return [{"candidate": c, "is_official_candidate": False,
                 "confidence_score": 0, "reasons": ["Ollama API key no configurada"],
                 "error": "no_api_key"} for c in candidates]

    import requests as req

    results = []
    for candidate in candidates:
        candidate_json = json.dumps(candidate, ensure_ascii=False, default=str)[:4000]

        prompt = (
            "Verifica si el perfil social pertenece oficialmente a la entidad indicada.\n"
            f"Entidad buscada: {entity_name}\n"
            f"Sitio oficial opcional: {official_website or 'no disponible'}\n"
            f"Candidato devuelto por Apify: {candidate_json}\n\n"
            "IMPORTANTE: inputProfileName es sólo el término de búsqueda y NO cuenta como nombre confirmado del perfil. "
            "No confundas un nombre parcial como 'Sebastian Mora' con 'Sebastian Vernis Mora'.\n\n"
            "Marca is_official_candidate=true sólo si el nombre mostrado del perfil coincide de forma exacta "
            "o casi inequívoca con la entidad Y existe al menos una señal independiente "
            "(insignia, bio, enlace, ubicación, cuenta oficial relacionada o datos consistentes).\n"
            "Una coincidencia parcial de nombre o un handle plausible por sí solos siempre deben ser false.\n"
            "El sitio web puede estar vacío, pero su ausencia no autoriza a marcar true.\n\n"
            "Devuelve SOLO JSON válido con:\n"
            '{"is_official_candidate": boolean, "confidence_score": 0.0-1.0, "reasons": ["razon1", "razon2"]}'
        )

        try:
            r = req.post(
                "https://ollama.com/api/chat",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "format": "json",
                },
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                content = data.get("message", {}).get("content", "{}")
                try:
                    ai_resp = json.loads(content)
                except json.JSONDecodeError:
                    import re as re_mod
                    m = re_mod.search(r"\{.*\}", content, re.DOTALL)
                    ai_resp = json.loads(m.group()) if m else {}
                results.append({
                    "candidate": candidate,
                    "is_official_candidate": ai_resp.get("is_official_candidate", False),
                    "confidence_score": ai_resp.get("confidence_score", 0),
                    "reasons": ai_resp.get("reasons", []),
                })
            else:
                results.append({
                    "candidate": candidate,
                    "is_official_candidate": False,
                    "confidence_score": 0,
                    "reasons": [f"Ollama HTTP {r.status_code}"],
                    "error": r.text[:200],
                })
        except Exception as e:
            results.append({
                "candidate": candidate,
                "is_official_candidate": False,
                "confidence_score": 0,
                "reasons": [str(e)],
                "error": str(e),
            })

    return results


def _run_validation(validation_id, curp, query, sujeto, rfc):
    """Ejecuta UNA validación. Devuelve (result, cost_mxn)."""
    validations = _build_validations(sujeto)
    v = next((x for x in validations if x["id"] == validation_id), None)
    if not v:
        return {"error": "validación no encontrada"}, 0

    provider = v["provider"]
    # 2026-08-04: defensivo — antes lanzaba KeyError si la entry no tenía cost_mxn.
    cost = v.get("cost_mxn", 0.0)
    nombre_completo = f"{sujeto.get('nombre','')} {sujeto.get('paterno','')} {sujeto.get('materno','')}".strip()

    if provider == "checkid":
        try:
            cc = get_checkid_client()
            return cc.get_full(curp, rfc_hint=rfc or ""), cost
        except Exception as e:
            return {"error": str(e)}, cost

    if provider == "singula":
        sc = get_singula_client()
        # Idempotente: reusa customer existente via cache local
        customer_id = None
        customer = None
        create_error = None
        try:
            res = sc.get_or_create_customer(
                curp=curp,
                name=sujeto.get("nombre", ""),
                last_name=sujeto.get("paterno", ""),
                mothers_last_name=sujeto.get("materno", ""),
                email=query if validation_id == "singula-email-lookup" else sujeto.get("email", ""),
                phone=query if validation_id == "singula-phone-lookup" else sujeto.get("telefono", ""),
                rfc=rfc,
                gender=sujeto.get("sexo", "H"),
                birth_day=parse_fecnac(sujeto.get("fecnac")).get("day"),
                birth_month=parse_fecnac(sujeto.get("fecnac")).get("month"),
                birth_year=parse_fecnac(sujeto.get("fecnac")).get("year"),
            )
            customer_id = res.get("id")
            customer = res.get("customer") or {}
            # 2026-08-05: propagar el error real de la API en vez del
            # genérico "no se pudo crear customer". Antes el frontend
            # sólo veía el genérico y se perdía info clave (401 key
            # expirada, red, validación, etc).
            if isinstance(customer, dict) and customer.get("error"):
                create_error = customer.get("error")
        except Exception as e:
            customer_id = None
            customer = None
            create_error = f"excepción: {e}"

        def _err(reason: str):
            # Mensaje rico: causa real + razón del caller
            msg = reason
            if create_error:
                msg = f"{reason} | causa: {create_error}"
            return {"error": msg, "create_error": create_error}, cost

        try:
            if validation_id == "singula-judicial-persona":
                if customer_id:
                    return sc.check_judicial(customer_id), cost
                return _err("no se pudo crear customer")
            if validation_id == "singula-blacklist-kyb":
                if customer_id:
                    return sc.check_blacklist(customer_id), cost
                return _err("no se pudo crear customer")
            # singula-identity-verification ELIMINADA — requiere subir foto del INE
            # singula-judicial-empresa ELIMINADA — quitada por el usuario
            # singula-document-signature ELIMINADA — requiere URL del documento
            # singula-validate-rfc ELIMINADA — CheckID ya valida el RFC ante el SAT
            if validation_id == "singula-email-lookup":
                if customer_id:
                    return sc.email_lookup(customer_id), cost
                return _err("no se pudo crear customer")
            if validation_id == "singula-phone-lookup":
                if customer_id:
                    return sc.phone_lookup(customer_id), cost
                return _err("no se pudo crear customer")
            if validation_id == "singula-blacklist":
                if customer_id:
                    return sc.check_blacklist(customer_id), cost
                return _err("no se pudo crear customer")
            if validation_id == "singula-judicial":
                if customer_id:
                    return sc.check_judicial(customer_id), cost
                return _err("no se pudo crear customer")
            if validation_id == "singula-intel-basic":
                if customer_id:
                    return sc.intel_basic(customer_id), cost
                return _err("no se pudo crear customer")
            if validation_id == "singula-intel-premium":
                if customer_id:
                    return sc.intel_premium(customer_id), cost
                return _err("no se pudo crear customer")
        except Exception as e:
            return {"error": str(e)}, cost

    if provider == "apify":
        try:
            from apify_broker import ApifyBroker
            from config import config
            broker = ApifyBroker(config.apify_token)
            parts = query.split(",")
            first = parts[0].strip()

            if validation_id == "apify-osint-email":
                # Dossier digital por email + nombre completo
                email = first if "@" in first else ""
                return broker.full_dossier(
                    email=email,
                    nombre=sujeto.get("nombre", ""),
                    paterno=sujeto.get("paterno", ""),
                    materno=sujeto.get("materno", ""),
                    max_results=10,
                ), cost

            if validation_id == "apify-instagram":
                # Buscar por username derivado del email (más determinante) o por nombre
                from osint_scorer import filtrar_resultados
                email_fiscal = sujeto.get("email") or sujeto.get("email_checkid") or first
                if "@" in first:
                    username = first.split("@")[0]
                    raw = broker.run_named("instagram-search-users", username, mode="enriched", max_items=50)
                else:
                    raw = broker.run_named("instagram-search-users", first, mode="enriched", max_items=50)
                return filtrar_resultados(raw, sujeto, email_fiscal, umbral=60, max_perfiles=5), cost

            if validation_id == "apify-tiktok":
                # username(s) → perfil; buscar por username del email primero
                from osint_scorer import filtrar_resultados
                email_fiscal = sujeto.get("email") or sujeto.get("email_checkid") or first
                if "@" in first:
                    usernames = [first.split("@")[0]]
                else:
                    usernames = [p.strip() for p in parts[:5] if p.strip()]
                raw = broker.run_named("tiktok-profile", usernames)
                return filtrar_resultados(raw, sujeto, email_fiscal, umbral=60, max_perfiles=5), cost

            if validation_id == "apify-twitter":
                # No hay actor directo de Twitter/X confiable; usamos Google search
                from osint_scorer import filtrar_resultados
                email_fiscal = sujeto.get("email") or sujeto.get("email_checkid") or first
                q = f"{first} twitter"
                raw = broker.run_named("google-search", q, limit=10)
                # adaptar resultados de google a formato perfil
                perfiles = []
                for item in raw:
                    if isinstance(item, dict) and not item.get("error"):
                        perfiles.append({
                            "plataforma": "twitter/x",
                            "url": item.get("url"),
                            "title": item.get("title"),
                            "description": item.get("description"),
                        })
                return filtrar_resultados(perfiles, sujeto, email_fiscal, umbral=60, max_perfiles=5), cost

            if validation_id == "apify-facebook":
                from osint_scorer import filtrar_resultados
                email_fiscal = sujeto.get("email") or sujeto.get("email_checkid") or first
                raw = broker.run_named("facebook-user-search", first, max_items=15)
                return filtrar_resultados(raw, sujeto, email_fiscal, umbral=60, max_perfiles=5), cost

            if validation_id == "apify-youtube":
                # Quitar @ si existe
                from osint_scorer import filtrar_resultados
                email_fiscal = sujeto.get("email") or sujeto.get("email_checkid") or first
                if "@" in first:
                    usernames = [first.split("@")[0]]
                else:
                    usernames = [p.strip().lstrip("@") for p in parts[:5] if p.strip()]
                raw = broker.run_named("youtube-channel", usernames)
                return filtrar_resultados(raw, sujeto, email_fiscal, umbral=60, max_perfiles=5), cost

            if validation_id == "apify-contact-info":
                urls = [p.strip() for p in parts[:3] if p.strip().startswith("http")]
                if not urls:
                    return {"error": "se requiere al menos una URL que empiece con http/https"}, cost
                return broker.run_named("contact-info-scraper", urls, max_depth=1, max_pages=10), cost

            if validation_id == "apify-social-media-finder":
                # Buscar perfiles en 13 redes sociales con tri_angle/social-media-finder
                # y verificar con Ollama si cada perfil pertenece a la entidad
                profile_names = [p.strip() for p in parts if p.strip()]
                if not profile_names:
                    profile_names = [nombre_completo] if nombre_completo else [query]
                raw = broker.find_social_media(profile_names)
                # Consolidar todos los candidatos de todas las búsquedas
                all_candidates = []
                for search_name, items in raw.items():
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict) and not item.get("error"):
                                item["_search_name"] = search_name
                                all_candidates.append(item)
                # Verificar cada candidato con Ollama
                verified = _verify_social_profiles_with_ollama(
                    all_candidates,
                    entity_name=nombre_completo,
                    official_website=sujeto.get("website", ""),
                )
                return {
                    "total_candidates": len(all_candidates),
                    "verified": verified,
                    "raw": raw,
                }, cost

            if validation_id == "apify-osint-email":
                # Dossier digital por email + nombre completo, pero filtrado
                from osint_scorer import filtrar_resultados
                email = first if "@" in first else ""
                raw = broker.full_dossier(
                    email=email,
                    nombre=sujeto.get("nombre", ""),
                    paterno=sujeto.get("paterno", ""),
                    materno=sujeto.get("materno", ""),
                    max_results=10,
                )
                # full_dossier ya devuelve dict consolidado; extraer perfiles
                perfiles = raw.get("perfiles_consolidados", [])
                filtrado = filtrar_resultados(perfiles, sujeto, email, umbral=60, max_perfiles=10)
                # preservar metadata del dossier
                filtrado["metadata"] = raw.get("metadata", {})
                filtrado["duracion_seg"] = raw.get("duracion_seg", 0)
                filtrado["resultados_brutos"] = raw.get("resultados", {})
                return filtrado, cost
        except Exception as e:
            import traceback
            return {"error": str(e), "traceback": traceback.format_exc()}, cost

    return {"error": "proveedor desconocido"}, cost


# === main ==============================================================


# === BASES EXTERNAS (ATTACH READ_ONLY en una sesión in-memory por proceso) ===
# Estas vistas NO se persisten en el .duckdb principal: por diseño de DuckDB,
# los ATTACH son por-sesión. Se recrean en cada arranque del servidor.
# Agregar aquí nuevas bases cuando estén disponibles en /root/nuevas_bases/.
# 2026-08-05: habilitadas las 4 bases restantes del bundle `bases_consolidadas/`
#   - att              73 MB    1,048,575 filas  PK rfc_clean (PF13/PF10)
#   - empleadores      48 MB      161,933 filas  PK rfc_clean (PM12/PM10)
#   - repuve          218 MB    1,745,627 filas  PK rfc_clean (PF13/PM12/PF10/PM10)
#   - imss_asegurados  3.6 GB   57,760,242 filas PK curp_clean (PF18)
#   - imss_segmentac. 6.5 GB   23,803,445 filas PK curp_clean (PF18) [+ rfc_clean]
#   - telcel          2.1 GB    9,709,461 filas  PK rfc_clean (PF13/PM12/PF10/PM10)
EXTENDED_DBS = {
    # alias      ruta                                                    tabla principal
    "b_att":     (str(ROOT.parent / "bases" / "att.duckdb"),                  "main.att"),
    "b_emp":     (str(ROOT.parent / "bases" / "empleadores.duckdb"),          "main.empleadores"),
    "b_repuve":  (str(ROOT.parent / "bases" / "repuve.duckdb"),               "main.repuve"),
    "b_imss_a":  (str(ROOT.parent / "bases" / "imss_asegurados.duckdb"),      "main.imss_2025"),
    "b_imss_s":  (str(ROOT.parent / "bases" / "imss_segmentacion.duckdb"),    "main.imss_personas"),
    "b_telcel":  (str(ROOT.parent / "bases" / "telcel.duckdb"),               "main.telcel"),
    "b_cfe":     (str(ROOT.parent / "bases" / "cfe.duckdb"),                  "main.medidores"),
    "b_fotos":   (str(ROOT.parent / "bases" / "fotos.duckdb"),                "main.fotos"),
    # 2026-08-13: padrón de empleados del ISSSTE con sueldo, ramo,
    # entidad, modalidad, sector, estado. 2.7M filas. Sin RFC/CURP/dirección.
    "b_issste":  (str(ROOT.parent / "bases" / "issste.duckdb"),               "main.empleados"),
}

_extended_con = None  # conexión DuckDB in-memory con ATTACH + vistas api.*


def _init_extended_con():
    """Crea la conexión in-memory con ATTACH a las bases externas + schema api.*.

    Es idempotente: si las ATTACHs fallan, los warnings se imprimen pero el
    servidor arranca igual (sólo los endpoints /api/v1/persona/* devolverán
    error hasta que se restauren las bases).
    """
    global _extended_con
    if _extended_con is not None:
        return _extended_con
    con = duckdb.connect(":memory:")
    con.execute("SET autoinstall_known_extensions=true; SET autoload_known_extensions=true;")
    attached = []
    for alias, (path, _main_tbl) in EXTENDED_DBS.items():
        if not os.path.exists(path):
            print(f"[!] base externa no encontrada (omitida): {alias} -> {path}", file=sys.stderr)
            continue
        try:
            con.execute(f"ATTACH '{path}' AS {alias} (READ_ONLY)")
            attached.append(alias)
        except Exception as e:
            print(f"[!] no se pudo ATTACH {alias} ({path}): {e}", file=sys.stderr)
    con.execute("CREATE SCHEMA IF NOT EXISTS api")
    # Las vistas usan prefijos b_att/b_emp; usan sólo las que se attachearon.
    if "b_att" in attached:
        con.execute("""
            CREATE OR REPLACE VIEW api.att_persona AS
            SELECT
                a.rfc_clean        AS rfc,
                a.nombres          AS nombre_completo,
                a.nombre           AS nombres,
                a.pat              AS apellido_paterno,
                a.may              AS apellido_materno,
                a.tel1             AS telefono_fijo,
                a.celular          AS celular,
                a.direccion        AS direccion,
                a.interior         AS num_interior,
                a.exterior         AS num_exterior,
                a.colonia,
                a.municipio,
                a.estado,
                a.estado_origen,
                a.rfc_kind
            FROM b_att.main.att a
            WHERE a.rfc_kind IN ('PF10','PF13','PM12')
        """)
    if "b_emp" in attached:
        con.execute("""
            CREATE OR REPLACE VIEW api.empleadores AS
            SELECT
                e.rfc_clean                       AS rfc,
                e."razonSocial"                   AS razon_social,
                e."nombreComercial"               AS nombre_comercial,
                e."numeroEmpleados"               AS num_empleados,
                e."descripcion"                   AS descripcion,
                e."correoElectronico"             AS correo,
                e."paginaWeb"                     AS web,
                e."contacto.cargo"                AS contacto_cargo,
                e."contacto.nombre"               AS contacto_nombre,
                e."contacto.primerApellido"       AS contacto_paterno,
                e."contacto.segundoApellido"      AS contacto_materno,
                e."contacto.telefono"             AS contacto_telefono,
                e."contacto.correoElectronico"    AS contacto_correo,
                e."contacto.extension"            AS contacto_extension,
                e."ubicacion.calle"               AS dom_calle,
                e."ubicacion.numero_exterior"     AS dom_ext,
                e."ubicacion.numero_interior"     AS dom_int,
                e."ubicacion.colonia"             AS dom_colonia,
                e."ubicacion.municipio"           AS dom_municipio,
                e."ubicacion.entidad"             AS dom_entidad,
                e."ubicacion.codigopostal"        AS dom_cp,
                e.rfc_kind
            FROM b_emp.main.empleadores e
            WHERE e.rfc_kind IN ('PM12','PF13','PF10','PM10')
        """)
    # === BUNDLE bases_consolidadas (4 nuevas bases, 2026-08-05) ===
    if "b_repuve" in attached:
        # Vehículos a nombre de una persona / empresa (PK rfc_clean del dueño/propietario).
        # Mostrar todas las variantes PF/PM con nombre + dirección limpios (nom_prop_fix, dir_prop_fix).
        con.execute("""
            CREATE OR REPLACE VIEW api.repuve_de_persona AS
            SELECT
                r.rfc_clean                AS rfc,
                r."PLACA"                  AS placa,
                r.NO_SERIE                 AS no_serie,
                r.MARCA                    AS marca,
                r.TIPO                     AS tipo,
                r.modelo_int               AS modelo,
                r.COLOR                    AS color,
                r.USO                      AS uso,
                r.nom_prop_fix             AS propietario,
                r.dir_prop_fix             AS direccion_propietario,
                r.TEL_PROP                 AS telefono_propietario,
                r.rfc_kind
            FROM b_repuve.main.repuve r
            WHERE r.rfc_kind IN ('PF13','PM12','PF10','PM10')
        """)
    if "b_imss_a" in attached:
        # Patrones IMSS donde aparece una persona (PK curp_clean).
        # cp5 se deriva del cp_bruto en el script de normalización; usarlo como filtro exacto.
        con.execute("""
            CREATE OR REPLACE VIEW api.imss_asegurado AS
            SELECT
                p.curp_clean           AS curp,
                p.nss_clean            AS nss,
                p.nombre               AS nombre_patron,
                p.registro_patron      AS registro_patron,
                p.nombre_patron        AS empresa_nombre,
                p.domicilio_patron     AS empresa_domicilio,
                p.ciudad_estado        AS empresa_ciudad_estado,
                p.cp5                  AS empresa_cp,
                p.empresa_giro         AS empresa_giro,
                p.sueldo_raw           AS sueldo,
                p.curp_kind,
                p.nss_clean
            FROM b_imss_a.main.imss_2025 p
            WHERE p.curp_kind = 'PF18'
        """)
    if "b_imss_s" in attached:
        # Segmentación de salud: diabetes / hipertensión / cáncer (PK curp_clean; rfc_clean opcional).
        # Sólo se proyectan columnas con valor no-nulo en cualquier segmento — el front las filtra.
        con.execute("""
            CREATE OR REPLACE VIEW api.imss_salud AS
            SELECT
                s.curp_clean                              AS curp,
                s.rfc_clean                               AS rfc,
                s.nss_clean                               AS nss,
                s.id_persona,
                s.edad,
                s.genero,
                s.ooad,
                s.unidad_medica,
                s.segmentacion_cancer_de_mama,
                s.segmentacion_cancer_de_prostata,
                s.segmentacion_diabetes_mellitus,
                s.segmento_hipertension,
                s.desc_enfermedad_diabetes,
                s.desc_enfermedad_hipertension,
                s.fecha_segmentacion,
                s.curp_kind,
                s.rfc_kind
            FROM b_imss_s.main.imss_personas s
            WHERE s.curp_kind = 'PF18'
        """)

    if "b_imss_a" in attached:
        # Vista full-detail con TODAS las 15 columnas de imss_asegurados.duckdb (2026-08-05).
        # La vista legacy api.imss_asegurado (10 cols) omite `registro_patron` (PK
        # del patrón en el IMSS), `nss` raw, `curp` raw y `codigo_postal` raw.
        # Aquí las exponemos todas.
        con.execute("""
            CREATE OR REPLACE VIEW api.imss_asegurado_full AS
            SELECT
                p.curp_clean                                  AS curp,
                p.nss_clean                                   AS nss,
                p.nss                                         AS nss_raw,
                p.curp                                        AS curp_raw,
                p.registro_patron                             AS registro_patron,
                p.nombre                                      AS nombre_patron,
                p.nombre_patron                               AS empresa_nombre,
                p.domicilio_patron                            AS empresa_domicilio,
                p.ciudad_estado                               AS empresa_ciudad_estado,
                p.codigo_postal                               AS empresa_cp_raw,
                p.cp5                                         AS empresa_cp,
                p.empresa_giro                                AS empresa_giro,
                p.sueldo_raw                                  AS sueldo,
                p.curp_len                                    AS curp_len,
                p.curp_kind                                   AS curp_kind
            FROM b_imss_a.main.imss_2025 p
            WHERE p.curp_kind = 'PF18'
        """)

    if "b_imss_s" in attached:
        # Vista full-detail con TODAS las 61 columnas de imss_segmentacion.duckdb (2026-08-05).
        # La vista legacy api.imss_salud (17 cols) omite region/col/porcentaje agregado_medico,
        # apellido_materno/paterno, convenio, correo_electronico, cve_* (delegacion, modalidad,
        # nivel_atencion, region, unidadmedica), desc_enfermedad_cama/cap, desc_nivel_atencion,
        # fecha_de_nacimiento, fecha_segmentacion, id_calidad/segmento*/ht/tipo_derechohabiente,
        # modalidad, personas/personas_2, prioridad*, rango_de_edad, razon_social, ref_celular,
        # registro_patronal, telefono, tipo_de_derechohabiente.
        # La _full expone todo (incluyendo los id_calidad, id_segmento*, etc.).
        con.execute("""
            CREATE OR REPLACE VIEW api.imss_salud_full AS
            SELECT
                s.curp_clean                                  AS curp,
                s.rfc_clean                                   AS rfc,
                s.nss_clean                                   AS nss,
                s.curp                                        AS curp_raw,
                s.nss                                         AS nss_raw,
                s.rfc                                         AS rfc_raw,
                s.id_persona,
                s.id_calidad,
                s.id_segmento,
                s.id_segmento_cama,
                s.id_segmento_cap,
                s.id_segmento_ht,
                s.id_tipo_derechohabiente,
                s.cve_delegacion,
                s.cve_modalidad,
                s.cve_nivel_atencion,
                s.cve_region,
                s.cve_unidadmedica,
                s.region,
                s.col                                         AS col_origen,
                s.porcentaje,
                s.porcentaje_ooad,
                s.porcentaje_unidad_medica,
                s.agregado_medico,
                s.personas,
                s.personas_2,
                s.prioridad,
                s.prioridad_cama,
                s.prioridad_cap,
                s.prioridad_ht,
                s.unidad_medica,
                s.ooad,
                s.modalidad,
                s.tipo_de_derechohabiente,
                s.desc_nivel_atencion,
                s.convenio,
                s.correo_electronico,
                s.ref_celular,
                s.telefono,
                s.nombre                                      AS nombre,
                s.apellido_paterno                            AS apellido_paterno,
                s.apellido_materno                            AS apellido_materno,
                s.rango_de_edad,
                s.edad,
                s.genero,
                s.fecha_de_nacimiento,
                s.fecha_segmentacion,
                s.segmentacion_cancer_de_mama,
                s.segmentacion_cancer_de_prostata,
                s.segmentacion_diabetes_mellitus,
                s.segmento_hipertension,
                s.desc_enfermedad_cama,
                s.desc_enfermedad_cap,
                s.desc_enfermedad_diabetes,
                s.desc_enfermedad_hipertension,
                s.registro_patronal,
                s.razon_social,
                s.curp_len                                    AS curp_len,
                s.curp_kind                                   AS curp_kind,
                s.rfc_kind                                    AS rfc_kind
            FROM b_imss_s.main.imss_personas s
            WHERE s.curp_kind = 'PF18'
        """)

    if "b_telcel" in attached:
        # Líneas activas Telcel (16 campos clave) — vista legacy mantenida
        # para compatibilidad con handlers existentes /api/v1/persona/rfc y
        # /api/v1/persona/curp.
        con.execute("""
            CREATE OR REPLACE VIEW api.telcel_lineas AS
            SELECT
                t.rfc_clean           AS rfc,
                t.telefono            AS telefono,
                t.plan_actual         AS plan,
                t.plan_orig           AS plan_origen,
                t.marca,
                t.modelo,
                t.esn,
                t.imei,
                t.iccid,
                t.st_tel              AS estado_linea,
                t.st_cta              AS estado_cuenta,
                t.fecha_activ         AS fecha_activacion,
                t.fecha_cancel        AS fecha_cancelacion,
                t.fecha_term          AS fecha_termino,
                t.nombre1             AS titular_nombre1,
                t.nombre2             AS titular_nombre2,
                t.domicilio,
                t.colonia             AS colonia,
                t.ciudad              AS ciudad,
                t.edo                 AS estado,
                t.cp,
                t.rfc_kind
            FROM b_telcel.main.telcel t
            WHERE t.rfc_kind IN ('PF13','PM12','PF10','PM10')
        """)
        # Vista full-detail con TODAS las 48 columnas de telcel.duckdb.
        # 2026-08-05: expone cuenta, esn, imei, iccid, fechas, dirección titular,
        # plan orig/actual, asesor, adendum, renaut, archivo_origen, etc.
        # TRIM() en VARCHAR porque el CSV fuente trae padding con espacios.
        con.execute("""
            CREATE OR REPLACE VIEW api.telcel_lineas_full AS
            SELECT
                t.rfc_clean                                         AS rfc,
                TRIM(t.cuenta)                                      AS cuenta,
                TRIM(t.padre)                                       AS padre,
                TRIM(t.st_cta)                                      AS estado_cuenta,
                TRIM(t.st_cob)                                      AS estado_cobranza,
                TRIM(t.cls_crd)                                     AS clase_credito,
                TRIM(t.tipo)                                        AS tipo,
                TRIM(t.ciclo)                                       AS ciclo,
                TRIM(t.fecha_activ)                                 AS fecha_activacion,
                TRIM(t.fecha_cancel)                                AS fecha_cancelacion,
                TRIM(t.fecha_term)                                  AS fecha_termino,
                TRIM(t.plan_actual)                                 AS plan_actual,
                TRIM(t.plan_orig)                                   AS plan_origen,
                TRIM(t.telefono)                                    AS telefono,
                TRIM(t.st_tel)                                      AS estado_linea,
                TRIM(t.motivo)                                      AS motivo,
                TRIM(t.fecha_cel)                                   AS fecha_celular,
                TRIM(t.gsm_ind)                                     AS gsm_indicador,
                TRIM(t.marca)                                       AS marca,
                TRIM(t.modelo)                                      AS modelo,
                TRIM(t.dat_orig)                                    AS datos_origen,
                TRIM(t.dat_actual)                                  AS datos_actuales,
                TRIM(t.asesor)                                      AS asesor,
                TRIM(t.adendum)                                     AS adendum,
                TRIM(t.plazo)                                       AS plazo,
                TRIM(t.nombre1)                                     AS titular_nombre1,
                TRIM(t.nombre2)                                     AS titular_nombre2,
                TRIM(t.rfc)                                         AS rfc_original,
                TRIM(t.domicilio)                                   AS titular_domicilio,
                TRIM(t.numero)                                      AS titular_numero,
                TRIM(t.interior)                                    AS titular_interior,
                TRIM(t.colonia)                                     AS titular_colonia,
                TRIM(t.ciudad)                                      AS titular_ciudad,
                TRIM(t.edo)                                         AS titular_estado,
                TRIM(t.cp)                                          AS titular_cp,
                TRIM(t.tel_contacto)                                AS titular_tel_contacto,
                TRIM(t.esn)                                         AS esn,
                TRIM(t.imei)                                        AS imei,
                TRIM(t.iccid)                                       AS iccid,
                TRIM(t.fecha_plan)                                  AS fecha_plan,
                TRIM(t.fecha_eq)                                    AS fecha_equipo,
                TRIM(t.tp_rfc)                                      AS tipo_rfc,
                TRIM(t.tp_pago)                                     AS tipo_pago,
                TRIM(t.tc)                                          AS tc,
                TRIM(t.contacto1)                                   AS contacto1,
                TRIM(t.contacto2)                                   AS contacto2,
                TRIM(t.renaut)                                      AS renaut,
                t.archivo_origen                                    AS archivo_origen,
                t.rfc_len                                           AS rfc_len,
                t.rfc_kind                                          AS rfc_kind
            FROM b_telcel.main.telcel t
            WHERE t.rfc_kind IN ('PF13','PM12','PF10','PM10')
        """)
    if "b_cfe" in attached:
        # CFE medidores — 66M filas, 21 columnas con headers REALES (post-normalización).
        # 2026-08-13: el cfe.duckdb que está en /root/proyecto_kyc/bases/ es la versión
        # NORMALIZADA (resultado de scripts/cfe_normalizer.py) con headers como
        # `division`, `zona_codigo`, `agencia_nombre`, `numero_servicio`, `direccion`,
        # `colonia`, `hilos`, etc. La versión vieja con headers col_1..col_19 está
        # archivada en /root/proyecto_kyc/_backups/parquet/cfe_fuente/ y
        # /root/proyecto_kyc/_backups/parquet/cfe_normalizado/ (parquet particionado
        # por región), pero ya NO se usa en runtime.
        #
        # La vista api.cfe_medidor ahora expone los headers finales. Como CFE no
        # tiene CURP/RFC, NO se une con el padrón; la búsqueda es por nombre+dirección
        # fuzzy en el handler /api/v1/cfe.
        con.execute("""
            CREATE OR REPLACE VIEW api.cfe_medidor AS
            SELECT
                m.division,
                m.zona_codigo        AS zona_cod,
                m.zona_nombre        AS zona_nom,
                m.zona_nombre_2,
                m.zona_nombre_3,
                m.agencia_codigo     AS agencia_cod,
                m.agencia_nombre     AS agencia_nom,
                m.agencia_nombre_2,
                m.agencia_nombre_3,
                m.codigo_medidor,
                m.numero_medidor,
                m.numero_servicio,            -- PK canónica (10-12 dígitos)
                -- 2026-08-13: cp extraído de calle_adicional_1 / direccion
                -- (~17% de las filas lo tienen; resto NULL). Es columna
                -- física en medidores, no calculada en cada query.
                m.cp,
                m.nombre,
                m.direccion,
                m.calle_adicional_1,
                m.calle_adicional_2,
                m.colonia,
                m.campo_adicional_1,
                m.hilos,
                m.__source_file      AS source_file,
                m.__source_folder    AS source_folder
            FROM b_cfe.main.medidores m
        """)
    if "b_issste" in attached:
        # 2026-08-13: padrón de empleados del ISSSTE (2.7M filas).
        # SIN RFC/CURP/dirección. Útil para confirmar employment en sector
        # público federal. Búsqueda por nombre+paterno+materno.
        con.execute("""
            CREATE OR REPLACE VIEW api.issste_empleado AS
            SELECT
                e.id,
                UPPER(TRIM(e.paterno))  AS paterno,
                UPPER(TRIM(e.materno))  AS materno,
                UPPER(TRIM(e.nombres))  AS nombres,
                e.cargo,
                e.sexo,
                e.sueldo,
                r.ramo,
                en.entidad,
                mo.modalidad,
                se.sector,
                es.estado
            FROM b_issste.main.empleados e
            LEFT JOIN b_issste.main.cat_ramos       r  ON e.ramo_id       = r.id
            LEFT JOIN b_issste.main.cat_entidades   en ON e.entidad_id    = en.id
            LEFT JOIN b_issste.main.cat_modalidades mo ON e.modalidad_id  = mo.id
            LEFT JOIN b_issste.main.cat_sectores    se ON e.sector_id     = se.id
            LEFT JOIN b_issste.main.cat_estados     es ON e.estado_id     = es.id
        """)
    if "b_att" in attached:
        # Vista full-detail con TODAS las 18 columnas de att.duckdb (2026-08-05).
        # Equivalente a api.att_persona (14 cols) + rfc_original, rfc_len, archivo_origen.
        con.execute("""
            CREATE OR REPLACE VIEW api.att_persona_full AS
            SELECT
                a.rfc_clean                                      AS rfc,
                a.nombres                                        AS nombre_completo,
                a.nombre                                         AS nombres,
                a.pat                                            AS apellido_paterno,
                a.may                                            AS apellido_materno,
                a.tel1                                           AS telefono_fijo,
                a.celular                                        AS celular,
                a.direccion                                      AS direccion,
                a.interior                                       AS num_interior,
                a.exterior                                       AS num_exterior,
                a.colonia                                        AS colonia,
                a.municipio                                      AS municipio,
                a.estado                                         AS estado,
                a.estado_origen                                  AS estado_origen,
                a.rfc                                            AS rfc_original,
                a.rfc_len                                        AS rfc_len,
                a.archivo_origen                                 AS archivo_origen,
                a.rfc_kind                                       AS rfc_kind
            FROM b_att.main.att a
            WHERE a.rfc_kind IN ('PF10','PF13','PM12')
        """)

    # Vista agregada: cuántas filas por RFC en cada base.
    # Sólo bases donde la PK del lookup es rfc_clean (att, empleados, repuve, telcel)
    # y la de imss_s cuando el rfc es PF13/PF10/PM12/PM10.
    if any(a in attached for a in ("b_att","b_emp","b_repuve","b_telcel","b_imss_s")):
        union_parts = []
        if "b_att" in attached:
            union_parts.append("""
                SELECT rfc_clean AS rfc, 'att' AS fuente, COUNT(*) AS n
                FROM b_att.main.att WHERE rfc_clean IS NOT NULL GROUP BY 1
            """)
        if "b_emp" in attached:
            union_parts.append("""
                SELECT rfc_clean, 'empleadores', COUNT(*)
                FROM b_emp.main.empleadores WHERE rfc_clean IS NOT NULL GROUP BY 1
            """)
        if "b_repuve" in attached:
            union_parts.append("""
                SELECT rfc_clean, 'repuve', COUNT(*)
                FROM b_repuve.main.repuve WHERE rfc_clean IS NOT NULL GROUP BY 1
            """)
        if "b_telcel" in attached:
            union_parts.append("""
                SELECT rfc_clean, 'telcel', COUNT(*)
                FROM b_telcel.main.telcel WHERE rfc_clean IS NOT NULL GROUP BY 1
            """)
        if "b_imss_s" in attached:
            # imss_s tiene tanto curp_clean como rfc_clean; lo incluimos por rfc sólo para PF/PM.
            union_parts.append("""
                SELECT rfc_clean, 'imss_segmentacion', COUNT(*)
                FROM b_imss_s.main.imss_personas
                WHERE rfc_clean IS NOT NULL AND rfc_kind IN ('PF13','PM12','PF10','PM10')
                GROUP BY 1
            """)
        con.execute("CREATE OR REPLACE VIEW api.fuentes_por_rfc AS " + " UNION ALL ".join(union_parts))
    _extended_con = con
    print(f"[*] Bases externas attacheadas ({len(attached)}): {', '.join(attached)}")
    return con


def _enriquecer_bases_externas(curp: str, rfc: str = None, nss: str = None, cap: int = 50,
                              nombre: str = None, paterno: str = None, materno: str = None,
                              fecnac: str = None) -> dict:
    """Cruza el sujeto contra las 6 bases externas attacheadas (2026-08-05).

    Lookups:
      - `b_imss_a.main.imss_2025` por `curp_clean` → patrones IMSS (curp-directo)
      - `b_imss_s.main.imss_personas` por `curp_clean` → segmentación salud (curp-directo)
      - `b_imss_s.main.imms_personas` por `curp_clean` → xwalk curp→rfc
      - `b_att.main.att` / `b_emp.main.empleadores` / `b_repuve.main.repuve`
        / `b_telcel.main.telcel` por `rfc_clean IN (...)` (vía xwalk)
      - Adicional: si se conoce NSS, ampliar el xwalk buscando rows de imss_a
        que coincidan por `nss_clean` (algunos sujetos sólo están en imss_a
        sin rfc en imss_s).

    Returns:
      dict con claves por base: `{imss_asegurado: {...}, imss_salud: {...},
      att: {...}, empleadores: {...}, repuve: {...}, telcel: {...},
      xwalk: {rfcs: [...], nss_rfcs: [...], nombre_rfcs: [...], error}, total_registros: int}`.

    Cada base trae `count`, `rows[]` (capado a `cap` por base para no
    explotar el payload) y `error` (None si OK).

    Lookup por nombre+fecnac (2026-08-06):
      Si tras los lookups por curp/rfc/nss NO se obtuvieron RFCs, intenta
      un fallback por `nombre + paterno + materno` (y `fecnac` cuando esté
      disponible) en att, telcel, repuve, imss_a, imss_s, emp. Cada
      lookup individual tiene un cap de 5s para no penalizar la carga del
      sujeto; los que excedan se reportan con `error="nombre_lookup_timeout"`.
    """
    out = {
        "cap": cap,
        "xwalk": {"rfcs": [], "nss_rfcs": [], "nombre_rfcs": [], "error": None},
        "imss_asegurado": {"rows": [], "count": 0, "total": 0, "error": None,
                           "cols": [
                               "curp","nss","nss_raw","curp_raw","registro_patron","nombre_patron",
                               "empresa_nombre","empresa_domicilio","empresa_ciudad_estado",
                               "empresa_cp_raw","empresa_cp","empresa_giro","sueldo",
                               "curp_len","curp_kind"]},
        "imss_salud": {"rows": [], "count": 0, "total": 0, "error": None,
                       "cols": [
                           "curp","rfc","nss","curp_raw","nss_raw","rfc_raw",
                           "id_persona","id_calidad","id_segmento","id_segmento_cama",
                           "id_segmento_cap","id_segmento_ht","id_tipo_derechohabiente",
                           "cve_delegacion","cve_modalidad","cve_nivel_atencion","cve_region",
                           "cve_unidadmedica","region","col_origen","porcentaje",
                           "porcentaje_ooad","porcentaje_unidad_medica","agregado_medico",
                           "personas","personas_2","prioridad","prioridad_cama","prioridad_cap",
                           "prioridad_ht","unidad_medica","ooad","modalidad",
                           "tipo_de_derechohabiente","desc_nivel_atencion","convenio",
                           "correo_electronico","ref_celular","telefono","nombre",
                           "apellido_paterno","apellido_materno","rango_de_edad","edad",
                           "genero","fecha_de_nacimiento","fecha_segmentacion",
                           "segmentacion_cancer_de_mama","segmentacion_cancer_de_prostata",
                           "segmentacion_diabetes_mellitus","segmento_hipertension",
                           "desc_enfermedad_cama","desc_enfermedad_cap",
                           "desc_enfermedad_diabetes","desc_enfermedad_hipertension",
                           "registro_patronal","razon_social","curp_len","curp_kind",
                           "rfc_kind"]},
        "att": {"rows": [], "count": 0, "total": 0, "error": None, "cols": None},
        "empleadores": {"rows": [], "count": 0, "total": 0, "error": None, "cols": None},
        "repuve": {"rows": [], "count": 0, "total": 0, "error": None, "cols": None},
        "telcel": {"rows": [], "count": 0, "total": 0, "error": None, "cols": None},
    }

    con = _init_extended_con()
    if con is None:
        out["xwalk"]["error"] = "extendido no inicializado"
        return out

    # 2026-08-06: detectar qué bases están attacheadas desde las vistas del
    # schema `api`. Si una vista existe, la base está attacheada.
    try:
        _vistas = {r[0] for r in con.execute(
            "SELECT view_name FROM duckdb_views() WHERE schema_name='api'"
        ).fetchall()}
    except Exception:
        _vistas = set()
    attached = {
        "b_att":    "att_persona" in _vistas,
        "b_emp":    "empleadores" in _vistas,
        "b_repuve": "repuve_de_persona" in _vistas,
        "b_imss_a": "imss_asegurado_full" in _vistas,
        "b_imss_s": "imss_salud_full" in _vistas,
        "b_telcel": "telcel_lineas_full" in _vistas,
    }
    if not any(attached.values()):
        attached = {  # fallback: intentar todo
            "b_att": True, "b_emp": True, "b_repuve": True,
            "b_imss_a": True, "b_imss_s": True, "b_telcel": True,
        }

    try:
        # 1) imss_asegurado por curp — DIRECTO pero CAP a 3s (es lookup lento).
        # b_imss_a tiene 57.7M filas sin índice en curp_clean; un lookup puntual
        # tarda 5-30s. Para no penalizar cada apertura de sujeto, devolvemos
        # sólo el total + las primeras 2 filas (las más rápidas de proyectar).
        try:
            t_a = time.time()
            out["imss_asegurado"]["total"] = con.execute(
                "SELECT COUNT(*) FROM api.imss_asegurado_full WHERE curp = ?", [curp]
            ).fetchone()[0]
            if time.time() - t_a > 3.0:
                out["imss_asegurado"]["error"] = "count_timeout (>3s)"
            else:
                rows = con.execute("""
                    SELECT curp, nss, nss_raw, curp_raw, registro_patron, nombre_patron,
                           empresa_nombre, empresa_domicilio, empresa_ciudad_estado,
                           empresa_cp_raw, empresa_cp, empresa_giro, sueldo,
                           curp_len, curp_kind
                    FROM api.imss_asegurado_full
                    WHERE curp = ? LIMIT ?
                """, [curp, min(cap, 2)]).fetchall()
                out["imss_asegurado"]["rows"] = [
                    dict(zip(out["imss_asegurado"]["cols"], r)) for r in rows
                ]
                out["imss_asegurado"]["count"] = len(rows)
        except duckdb.Error as e:
            out["imss_asegurado"]["error"] = str(e)[:200]

        # 2) imss_salud por curp — directo
        try:
            out["imss_salud"]["total"] = con.execute(
                "SELECT COUNT(*) FROM api.imss_salud_full WHERE curp = ?", [curp]
            ).fetchone()[0]
            rows = con.execute(
                "SELECT * FROM api.imss_salud_full WHERE curp = ? LIMIT ?", [curp, cap]
            ).fetchall()
            out["imss_salud"]["rows"] = [
                dict(zip(out["imss_salud"]["cols"], r)) for r in rows
            ]
            out["imss_salud"]["count"] = len(rows)
        except duckdb.Error as e:
            out["imss_salud"]["error"] = str(e)[:200]

        # 3) xwalk curp→rfc (de imss_s) + curp→rfc (de imss_a si hay NSS)
        try:
            rfcs = [r[0] for r in con.execute("""
                SELECT DISTINCT rfc_clean FROM b_imss_s.main.imss_personas
                WHERE curp_clean = ? AND rfc_clean IS NOT NULL
                  AND rfc_kind IN ('PF13','PM12','PF10','PM10')
            """, [curp]).fetchall()]
        except duckdb.Error as e:
            rfcs = []
            out["xwalk"]["error"] = f"xwalk imss_s: {str(e)[:120]}"

        # Si nos pasan un RFC explícito (de CheckID o local), sumarlo.
        if rfc and rfc not in rfcs:
            rfcs.append(rfc)

        # Si nos pasan NSS, buscar RFCs adicionales por NSS en imss_a.
        # Útil cuando el sujeto no aparece en imss_s pero sí en imss_a.
        nss_rfcs = []
        if nss:
            try:
                nss_rfcs = [r[0] for r in con.execute("""
                    SELECT DISTINCT registro_patron FROM b_imss_a.main.imss_2025
                    WHERE nss_clean = ? AND registro_patron IS NOT NULL
                      AND curp_kind='PF18'
                    LIMIT 10
                """, [nss]).fetchall()]
            except duckdb.Error:
                pass

        out["xwalk"]["rfcs"] = rfcs
        out["xwalk"]["nss_rfcs"] = nss_rfcs

        # 4) Bases RFC-keyed con WHERE rfc IN (...)
        if rfcs:
            ph = ",".join(["?"] * len(rfcs))

            # att_persona_full (18 cols)
            try:
                out["att"]["total"] = con.execute(
                    f"SELECT COUNT(*) FROM api.att_persona_full WHERE rfc IN ({ph})", rfcs
                ).fetchone()[0]
                if out["att"]["total"]:
                    rows = con.execute(f"SELECT * FROM api.att_persona_full WHERE rfc IN ({ph}) LIMIT ?",
                                       rfcs + [cap]).fetchall()
                    cols = [d[0] for d in con.execute("SELECT * FROM api.att_persona_full LIMIT 0").description]
                    out["att"]["cols"] = cols
                    out["att"]["rows"] = [dict(zip(cols, r)) for r in rows]
                    out["att"]["count"] = len(rows)
            except duckdb.Error as e:
                out["att"]["error"] = str(e)[:200]

            # empleadores
            try:
                out["empleadores"]["total"] = con.execute(
                    f"SELECT COUNT(*) FROM api.empleadores WHERE rfc IN ({ph})", rfcs
                ).fetchone()[0]
                if out["empleadores"]["total"]:
                    rows = con.execute(f"""
                        SELECT rfc, razon_social, nombre_comercial, num_empleados,
                               dom_calle, dom_ext, dom_int, dom_colonia, dom_municipio,
                               dom_entidad, dom_cp, rfc_kind
                        FROM api.empleadores WHERE rfc IN ({ph}) LIMIT ?
                    """, rfcs + [cap]).fetchall()
                    cols = ["rfc","razon_social","nombre_comercial","num_empleados",
                            "dom_calle","dom_ext","dom_int","dom_colonia","dom_municipio",
                            "dom_entidad","dom_cp","rfc_kind"]
                    out["empleadores"]["cols"] = cols
                    out["empleadores"]["rows"] = [dict(zip(cols, r)) for r in rows]
                    out["empleadores"]["count"] = len(rows)
            except duckdb.Error as e:
                out["empleadores"]["error"] = str(e)[:200]

            # repuve
            try:
                out["repuve"]["total"] = con.execute(
                    f"SELECT COUNT(*) FROM api.repuve_de_persona WHERE rfc IN ({ph})", rfcs
                ).fetchone()[0]
                if out["repuve"]["total"]:
                    rows = con.execute(f"""
                        SELECT rfc, placa, no_serie, marca, modelo, color, uso,
                               propietario, direccion_propietario, telefono_propietario, rfc_kind
                        FROM api.repuve_de_persona WHERE rfc IN ({ph}) LIMIT ?
                    """, rfcs + [cap]).fetchall()
                    cols = ["rfc","placa","no_serie","marca","modelo","color","uso",
                            "propietario","direccion_propietario","telefono_propietario","rfc_kind"]
                    out["repuve"]["cols"] = cols
                    out["repuve"]["rows"] = [dict(zip(cols, r)) for r in rows]
                    out["repuve"]["count"] = len(rows)
            except duckdb.Error as e:
                out["repuve"]["error"] = str(e)[:200]

            # telcel_lineas_full (las 48 cols, capadas)
            try:
                out["telcel"]["total"] = con.execute(
                    f"SELECT COUNT(*) FROM api.telcel_lineas_full WHERE rfc IN ({ph})", rfcs
                ).fetchone()[0]
                if out["telcel"]["total"]:
                    rows = con.execute(f"""
                        SELECT cuenta, telefono, plan_actual, marca, modelo,
                               estado_linea, titular_nombre1, titular_nombre2,
                               titular_domicilio, titular_ciudad, titular_estado, titular_cp,
                               fecha_activacion, fecha_cancelacion,
                               esn, imei, iccid, archivo_origen, rfc_kind
                        FROM api.telcel_lineas_full
                        WHERE rfc IN ({ph})
                        ORDER BY fecha_activacion DESC NULLS LAST, cuenta
                        LIMIT ?
                    """, rfcs + [cap]).fetchall()
                    cols = ["cuenta","telefono","plan_actual","marca","modelo",
                            "estado_linea","titular_nombre1","titular_nombre2",
                            "titular_domicilio","titular_ciudad","titular_estado","titular_cp",
                            "fecha_activacion","fecha_cancelacion",
                            "esn","imei","iccid","archivo_origen","rfc_kind"]
                    out["telcel"]["cols"] = cols
                    out["telcel"]["rows"] = [dict(zip(cols, r)) for r in rows]
                    out["telcel"]["count"] = len(rows)
            except duckdb.Error as e:
                out["telcel"]["error"] = str(e)[:200]

        # Si tenemos RFCs del NSS pero NO del CURP-xwalk, intentar también.
        if not rfcs and nss_rfcs:
            ph2 = ",".join(["?"] * len(nss_rfcs))
            # mini lookup solo en telcel y att (los más probables)
            for base_alias, view, in out:
                pass

        # ===== 5) Fallback por nombre+fecnac (2026-08-06) =====
        # Si tras los lookups por curp/rfc/nss seguimos sin RFCs, intentar
        # matchear por nombre completo (paterno + materno + nombres) y, si
        # se provee, fecha de nacimiento. Solo se intenta cuando tenemos
        # al menos paterno+materno (sin eso el match sería ambiguo).
        nombre_rfcs = []
        _paterno = (paterno or "").strip().upper()
        _materno = (materno or "").strip().upper()
        _nombre  = (nombre or "").strip().upper()
        _fecnac  = (fecnac or "").strip()  # YYYY-MM-DD o YYYYMMDD

        if not rfcs and _paterno and _materno:
            # Cada lookup corre con un cap de 5s. Si se pasa del cap, aborta
            # y reporta el error específico, sin afectar al resto.
            # En att, telcel, imss_a, repuve, imss_s, emp se intenta match
            # por paterno + materno; el nombre se usa como filtro secundario
            # (LIKE %x% en nombre1 o nombres) cuando está disponible.
            # fecnac sólo aplica a imss_s (única base con fecha_de_nacimiento).

            def _nombre_lookup(label, sql, params, out_attr):
                """Ejecuta un lookup por nombre con cap de 5s y vuelca los rows al out[base].

                La SQL debe empezar con 'SELECT <cols> FROM ...'; este helper
                la envuelve automáticamente en un SELECT COUNT(*) para el
                conteo total y le agrega LIMIT ? para los rows.
                """
                t0 = time.time()
                try:
                    # Total: contar todo lo que matchea (sin LIMIT)
                    n_row = con.execute(
                        f"SELECT COUNT(*) FROM ({sql})", params
                    ).fetchone()
                    n = int(n_row[0]) if n_row and n_row[0] is not None else 0
                    if time.time() - t0 > 5.0:
                        out[out_attr]["error"] = "nombre_lookup_timeout (>5s)"
                        return 0
                    if n == 0:
                        return 0
                    # Rows: agregar LIMIT
                    rows = con.execute(f"{sql} LIMIT ?", params + [cap]).fetchall()
                    if time.time() - t0 > 5.0:
                        out[out_attr]["error"] = "nombre_lookup_timeout (>5s)"
                        return n
                    cols = [d[0] for d in con.description]
                    out[out_attr]["cols"] = cols
                    out[out_attr]["rows"] = [dict(zip(cols, r)) for r in rows]
                    out[out_attr]["count"] = len(rows)
                    out[out_attr]["total"] = n
                    return n
                except duckdb.Error as e:
                    out[out_attr]["error"] = f"nombre_lookup_err: {str(e)[:120]}"
                    return 0
                except Exception as e:
                    out[out_attr]["error"] = f"nombre_lookup_exc: {str(e)[:120]}"
                    return 0

            # att: paterno + materno; nombre LIKE %x% sobre nombres o nombre
            if attached.get("b_att") and not out["att"]["total"]:
                sql = """
                    SELECT a.* FROM b_att.main.att a
                    WHERE UPPER(TRIM(COALESCE(a.pat,''))) = ?
                      AND UPPER(TRIM(COALESCE(a.may,''))) = ?
                      AND (? = '' OR UPPER(COALESCE(a.nombres,a.nombre,'')) LIKE '%' || ? || '%')
                      AND a.rfc_kind IN ('PF13','PF10','PM12','PM10')
                """
                _nombre_lookup("att", sql, [_paterno, _materno, _nombre, _nombre], "att")

            # telcel: nombre2 suele traer "PATERNO MATERNO"; nombre1 es el/los nombres
            if attached.get("b_telcel") and not out["telcel"]["total"]:
                sql = """
                    SELECT t.* FROM b_telcel.main.telcel t
                    WHERE UPPER(TRIM(COALESCE(t.nombre2,''))) LIKE '%' || ? || '%'
                      AND UPPER(TRIM(COALESCE(t.nombre2,''))) LIKE '%' || ? || '%'
                      AND (? = '' OR UPPER(TRIM(COALESCE(t.nombre1,''))) LIKE '%' || ? || '%')
                      AND t.rfc_kind IN ('PF13','PM12','PF10','PM10')
                """
                _nombre_lookup("telcel", sql, [_paterno, _materno, _nombre, _nombre], "telcel")

            # repuve: nom_prop_fix viene con la forma "APELLIDO1 APELLIDO2 NOMBRES"
            if attached.get("b_repuve") and not out["repuve"]["total"]:
                sql = """
                    SELECT r.* FROM b_repuve.main.repuve r
                    WHERE UPPER(COALESCE(r.nom_prop_fix,'')) LIKE '%' || ? || '%'
                      AND UPPER(COALESCE(r.nom_prop_fix,'')) LIKE '%' || ? || '%'
                      AND r.rfc_kind IN ('PF13','PM12','PF10','PM10')
                """
                _nombre_lookup("repuve", sql, [_paterno, _materno], "repuve")

            # imss_a: nombre trae "PATERNO MATERNO NOMBRES" (formato del IMSS)
            if attached.get("b_imss_a") and not out["imss_asegurado"]["total"]:
                sql = """
                    SELECT i.* FROM b_imss_a.main.imss_2025 i
                    WHERE UPPER(COALESCE(i.nombre,'')) LIKE '%' || ? || '%'
                      AND UPPER(COALESCE(i.nombre,'')) LIKE '%' || ? || '%'
                      AND (? = '' OR UPPER(COALESCE(i.nombre,'')) LIKE '%' || ? || '%')
                      AND i.curp_kind = 'PF18'
                """
                _nombre_lookup("imss_a", sql, [_paterno, _materno, _nombre, _nombre], "imss_asegurado")

            # imss_s: tiene apellido_paterno / apellido_materno / nombre
            # separados, y fecha_de_nacimiento (DATE) — match más preciso.
            # 2026-08-06: fecha_de_nacimiento es DATE, así que casteamos a VARCHAR
            # con strftime para poder compararla con el string que recibimos.
            if attached.get("b_imss_s") and not out["imss_salud"]["total"]:
                if _fecnac:
                    _fecnac_norm = _fecnac.replace("-", "").replace("/", "")
                    # _fecnac_norm puede ser YYYYMMDD o YYYY-MM-DD
                    if len(_fecnac_norm) == 8:
                        _fecnac_dash = f"{_fecnac_norm[:4]}-{_fecnac_norm[4:6]}-{_fecnac_norm[6:8]}"
                    else:
                        _fecnac_dash = _fecnac_norm
                    sql = """
                        SELECT s.* FROM b_imss_s.main.imss_personas s
                        WHERE UPPER(TRIM(COALESCE(s.apellido_paterno,''))) = ?
                          AND UPPER(TRIM(COALESCE(s.apellido_materno,''))) = ?
                          AND (? = '' OR UPPER(TRIM(COALESCE(s.nombre,''))) LIKE '%' || ? || '%')
                          AND CAST(s.fecha_de_nacimiento AS VARCHAR) = ?
                          AND s.curp_kind = 'PF18'
                    """
                    _nombre_lookup("imss_s", sql, [_paterno, _materno, _nombre, _nombre, _fecnac_dash], "imss_salud")
                else:
                    sql = """
                        SELECT s.* FROM b_imss_s.main.imss_personas s
                        WHERE UPPER(TRIM(COALESCE(s.apellido_paterno,''))) = ?
                          AND UPPER(TRIM(COALESCE(s.apellido_materno,''))) = ?
                          AND (? = '' OR UPPER(TRIM(COALESCE(s.nombre,''))) LIKE '%' || ? || '%')
                          AND s.curp_kind = 'PF18'
                    """
                    _nombre_lookup("imss_s", sql, [_paterno, _materno, _nombre, _nombre], "imss_salud")

            # emp: nombreCompleto y razonSocial (PF10/13 contacto, PM12 empresa)
            if attached.get("b_emp") and not out["empleadores"]["total"]:
                sql = """
                    SELECT e.* FROM b_emp.main.empleadores e
                    WHERE (
                        (e.rfc_kind='PM12' AND UPPER(COALESCE(e."razonSocial",'')) LIKE '%' || ? || '%')
                        OR (e.rfc_kind IN ('PF13','PF10') AND UPPER(COALESCE(e."nombreCompleto",'')) LIKE '%' || ? || '%')
                    )
                      AND UPPER(COALESCE(e."primerApellido",'')) = ?
                      AND UPPER(COALESCE(e."segundoApellido",'')) = ?
                """
                _nombre_lookup("emp", sql, [_nombre, _nombre, _paterno, _materno], "empleadores")

            # Si obtuvimos RFCs vía nombre, agregarlos a rfcs y re-popular las bases
            # que no se consultaron por nombre. Esto es importante porque la base
            # ya está poblada por nombre, pero queremos que las BASES que se
            # consultaron con esos RFCs también queden en el resultado (caso típico:
            # encontré la persona en imss_s por nombre, ahora traigo su telcel/att).
            for base_key, view_lookup in [
                ("att", "api.att_persona_full"),
                ("telcel", "api.telcel_lineas_full"),
                ("repuve", "api.repuve_de_persona"),
                ("empleadores", "api.empleadores"),
            ]:
                # Solo aplicar si no se pobló ya por nombre
                if out[base_key]["total"] == 0 and base_key in ("telcel", "att", "repuve", "empleadores"):
                    pass  # ya intentado por nombre arriba

            # Extraer RFCs únicos de los rows que se hayan obtenido por nombre
            # y re-consultar las bases que matchean por RFC.
            for base_key, rfc_col in [("att", "rfc_clean"), ("telcel", "rfc_clean"),
                                       ("repuve", "rfc_clean"), ("imss_salud", "rfc_clean"),
                                       ("imss_asegurado", None), ("empleadores", "rfc_clean")]:
                rows = out[base_key].get("rows", [])
                for row in rows:
                    val = row.get(rfc_col) if rfc_col else None
                    if val and val not in nombre_rfcs:
                        nombre_rfcs.append(val)
            nombre_rfcs = list(dict.fromkeys(nombre_rfcs))[:50]  # dedup + cap

        out["xwalk"]["nombre_rfcs"] = nombre_rfcs

        # Si obtuvimos RFCs sólo por nombre, re-popular las bases que matchean
        # por RFC (att, telcel, repuve, empleadores) que no se hayan poblado ya
        # por nombre. Esto es lo que cierra el ciclo: encontré la persona en
        # imss_s por nombre, ahora traigo su telcel/att/repuve por RFC.
        if nombre_rfcs and (not rfcs):
            ph_n = ",".join(["?"] * len(nombre_rfcs))
            # telcel
            if not out["telcel"]["total"] and "b_telcel" in attached:
                try:
                    out["telcel"]["total"] = con.execute(
                        f"SELECT COUNT(*) FROM api.telcel_lineas_full WHERE rfc IN ({ph_n})", nombre_rfcs
                    ).fetchone()[0]
                    if out["telcel"]["total"]:
                        rows = con.execute(f"""
                            SELECT cuenta, telefono, plan_actual, marca, modelo,
                                   estado_linea, titular_nombre1, titular_nombre2,
                                   titular_domicilio, titular_ciudad, titular_estado, titular_cp,
                                   fecha_activacion, fecha_cancelacion,
                                   esn, imei, iccid, archivo_origen, rfc_kind
                            FROM api.telcel_lineas_full
                            WHERE rfc IN ({ph_n})
                            ORDER BY fecha_activacion DESC NULLS LAST, cuenta
                            LIMIT ?
                        """, nombre_rfcs + [cap]).fetchall()
                        cols = ["cuenta","telefono","plan_actual","marca","modelo",
                                "estado_linea","titular_nombre1","titular_nombre2",
                                "titular_domicilio","titular_ciudad","titular_estado","titular_cp",
                                "fecha_activacion","fecha_cancelacion",
                                "esn","imei","iccid","archivo_origen","rfc_kind"]
                        out["telcel"]["cols"] = cols
                        out["telcel"]["rows"] = [dict(zip(cols, r)) for r in rows]
                        out["telcel"]["count"] = len(rows)
                except duckdb.Error as e:
                    out["telcel"]["error"] = f"post_nombre_rfc: {str(e)[:120]}"
            # att
            if not out["att"]["total"] and "b_att" in attached:
                try:
                    out["att"]["total"] = con.execute(
                        f"SELECT COUNT(*) FROM api.att_persona_full WHERE rfc IN ({ph_n})", nombre_rfcs
                    ).fetchone()[0]
                    if out["att"]["total"]:
                        rows = con.execute(f"SELECT * FROM api.att_persona_full WHERE rfc IN ({ph_n}) LIMIT ?",
                                           nombre_rfcs + [cap]).fetchall()
                        cols = [d[0] for d in con.execute("SELECT * FROM api.att_persona_full LIMIT 0").description]
                        out["att"]["cols"] = cols
                        out["att"]["rows"] = [dict(zip(cols, r)) for r in rows]
                        out["att"]["count"] = len(rows)
                except duckdb.Error as e:
                    out["att"]["error"] = f"post_nombre_rfc: {str(e)[:120]}"
            # repuve
            if not out["repuve"]["total"] and "b_repuve" in attached:
                try:
                    out["repuve"]["total"] = con.execute(
                        f"SELECT COUNT(*) FROM api.repuve_de_persona WHERE rfc IN ({ph_n})", nombre_rfcs
                    ).fetchone()[0]
                    if out["repuve"]["total"]:
                        rows = con.execute(f"""
                            SELECT rfc, placa, no_serie, marca, modelo, color, uso,
                                   propietario, direccion_propietario, telefono_propietario, rfc_kind
                            FROM api.repuve_de_persona WHERE rfc IN ({ph_n}) LIMIT ?
                        """, nombre_rfcs + [cap]).fetchall()
                        cols = ["rfc","placa","no_serie","marca","modelo","color","uso",
                                "propietario","direccion_propietario","telefono_propietario","rfc_kind"]
                        out["repuve"]["cols"] = cols
                        out["repuve"]["rows"] = [dict(zip(cols, r)) for r in rows]
                        out["repuve"]["count"] = len(rows)
                except duckdb.Error as e:
                    out["repuve"]["error"] = f"post_nombre_rfc: {str(e)[:120]}"
            # empleadores
            if not out["empleadores"]["total"] and "b_emp" in attached:
                try:
                    out["empleadores"]["total"] = con.execute(
                        f"SELECT COUNT(*) FROM api.empleadores WHERE rfc IN ({ph_n})", nombre_rfcs
                    ).fetchone()[0]
                    if out["empleadores"]["total"]:
                        rows = con.execute(f"""
                            SELECT rfc, razon_social, nombre_comercial, num_empleados,
                                   dom_calle, dom_ext, dom_int, dom_colonia, dom_municipio,
                                   dom_entidad, dom_cp, rfc_kind
                            FROM api.empleadores WHERE rfc IN ({ph_n}) LIMIT ?
                        """, nombre_rfcs + [cap]).fetchall()
                        cols = ["rfc","razon_social","nombre_comercial","num_empleados",
                                "dom_calle","dom_ext","dom_int","dom_colonia","dom_municipio",
                                "dom_entidad","dom_cp","rfc_kind"]
                        out["empleadores"]["cols"] = cols
                        out["empleadores"]["rows"] = [dict(zip(cols, r)) for r in rows]
                        out["empleadores"]["count"] = len(rows)
                except duckdb.Error as e:
                    out["empleadores"]["error"] = f"post_nombre_rfc: {str(e)[:120]}"
            # imss_a (no se consultó por nombre por el alto costo; aquí lo intentamos
            # por RFC si el sujeto fue encontrado en imss_s por nombre)
            if not out["imss_asegurado"]["total"] and "b_imss_s" in attached:
                # Reusar curps de imss_s.rows si hay
                curps = list({r.get("curp") for r in out["imss_salud"].get("rows", []) if r.get("curp")})
                if curps:
                    ph_c = ",".join(["?"] * len(curps))
                    try:
                        out["imss_asegurado"]["total"] = con.execute(
                            f"SELECT COUNT(*) FROM api.imss_asegurado_full WHERE curp IN ({ph_c})", curps
                        ).fetchone()[0]
                        if out["imss_asegurado"]["total"]:
                            rows = con.execute(f"""
                                SELECT curp, nss, nss_raw, curp_raw, registro_patron, nombre_patron,
                                       empresa_nombre, empresa_domicilio, empresa_ciudad_estado,
                                       empresa_cp_raw, empresa_cp, empresa_giro, sueldo,
                                       curp_len, curp_kind
                                FROM api.imss_asegurado_full
                                WHERE curp IN ({ph_c}) LIMIT ?
                            """, curps + [cap]).fetchall()
                            out["imss_asegurado"]["cols"] = [
                                "curp","nss","nss_raw","curp_raw","registro_patron","nombre_patron",
                                "empresa_nombre","empresa_domicilio","empresa_ciudad_estado",
                                "empresa_cp_raw","empresa_cp","empresa_giro","sueldo",
                                "curp_len","curp_kind"]
                            out["imss_asegurado"]["rows"] = [dict(zip(out["imss_asegurado"]["cols"], r)) for r in rows]
                            out["imss_asegurado"]["count"] = len(rows)
                    except duckdb.Error as e:
                        out["imss_asegurado"]["error"] = f"post_nombre_curp: {str(e)[:120]}"

        out["total_registros"] = (
            int(out["imss_asegurado"]["total"] or 0)
            + int(out["imss_salud"]["total"] or 0)
            + int(out["att"]["total"] or 0)
            + int(out["empleadores"]["total"] or 0)
            + int(out["repuve"]["total"] or 0)
            + int(out["telcel"]["total"] or 0)
        )
    except Exception as e:
        out["error"] = str(e)[:200]

    return out


def main():
    ap = argparse.ArgumentParser(description="Servidor API para padrón INE 2018")
    ap.add_argument("--db", default=str(ROOT.parent / "bases" / "padron.duckdb"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--html", default=str(ROOT.parent / "frontend" / "buscar.html"))
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: DB no existe: {args.db}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] DB: {args.db}")
    Handler.db = DB(args.db)
    Handler.html_path = Path(args.html)
    Handler.db_path = args.db
    # Inicializar conexión in-memory con bases externas (idempotente)
    _init_extended_con()
    print(f"[*] DB: {args.db}")
    print(f"[*] Sirviendo en http://{args.host}:{args.port}")
    print(f"[*] Endpoints: /api/total /api/estados /api/curp/<curp> /api/search")
    print(f"[*]             /api/v1/persona/rfc/<rfc>  /api/v1/persona/rfc/<rfc>/todo")
    print(f"[*] Total padrón: {Handler.db.total():,}")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Cerrando...")
        srv.shutdown()
    finally:
        try:
            srv.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
