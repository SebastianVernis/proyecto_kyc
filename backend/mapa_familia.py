"""mapa_familia.py — Mapa interactivo autocontenido (offline) del universo
relacional de un expediente KYC.

Toma la salida de `inteligencia_completa.ejecutar_inteligencia_completa()` y
produce un HTML de una sola pieza que abre con doble clic desde `file://`
sin red: Leaflet va embebido, los mosaicos (tiles) van embebidos en base64
como WebP y las direcciones se geocodifican una sola vez, guardándose en una
caché JSON en disco para que las corridas siguientes no vuelvan a pedirlas.

Nodos que dibuja (según la categoría del dossier):
    sujeto       — domicilio(s) del sujeto raíz
    familiar     — núcleo consanguíneo (coincidencia patronímica biparental)
    conviviente  — empadronados en el mismo predio
    vecino       — empadronados en la misma vialidad y CP
    apellido     — personas con apellido compartido
    cfe          — titulares de suministro CFE en el predio declarado

El CSV del mismo dataset sale junto al HTML, para que el mapa sea trazable
y no sólo decorativo.

Uso programático:

    from mapa_familia import generar_mapa_familia
    out = generar_mapa_familia(inteligencia_data, curp="XXXX...",
                               workdir=Path("/bases/_dossiers/j1"),
                               progress=print)
    out["html"], out["csv"], out["stats"]

Reglas de convivencia con OSM (servidor público de mosaicos):
  * Un solo User-Agent identificable.
  * ~2 req/s como máximo, y los mosaicos ya descargados nunca se repiten:
    la caché es un directorio plano compartido entre corridas.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ── Rutas ────────────────────────────────────────────────────────────────────
# Los assets de Leaflet se copian a backend/assets/leaflet/ y viajan dentro de
# la imagen (Dockerfile hace COPY backend/ /app/), así que la resolución es
# trivial: siempre junto a este módulo.
ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "leaflet"

USER_AGENT = "proyecto-kyc-mapa-familia/1.0 (trazabilidad OSINT; contacto: sebastianvernis)"

# bbox de México: Nominatim devuelve basura (direcciones de EUA) si la calle
# tiene nombre USA; fuera de esta caja el resultado se descarta.
MX_LAT_MIN, MX_LAT_MAX = 14.0, 33.5
MX_LON_MIN, MX_LON_MAX = -119.0, -86.5

# ── Catálogo de tipos de nodo (orden = orden de los chips en la leyenda) ────
KINDS = [
    {"kind": "sujeto",      "label": "Sujeto (domicilio declarado)",  "color": "#e11d48", "radio": 12},
    {"kind": "familiar",    "label": "Familia (núcleo consanguíneo)",  "color": "#7c3aed", "radio": 10},
    {"kind": "conviviente", "label": "Conviviente del predio",          "color": "#0891b2", "radio": 9},
    {"kind": "cfe",         "label": "Titular de suministro CFE",        "color": "#eab308", "radio": 8},
    {"kind": "apellido",    "label": "Apellido compartido",             "color": "#db2777", "radio": 7},
    {"kind": "vecino",      "label": "Vecino de la misma calle",        "color": "#ea580c", "radio": 7},
]

# categorías del dossier → kind del mapa
_CATEGORIA_A_KIND = {
    "sujeto principal": "sujeto",
    "núcleo consanguíneo": "familiar",
    "nucleo consanguineo": "familiar",
    "conviviente domiciliario": "conviviente",
    "titular suministro cfe": "cfe",
    "apellido compartido": "apellido",
    "vecino misma calle": "vecino",
}


# ══════════════════════════════════════════════════════════════════════════════
# Construcción de nodos
# ══════════════════════════════════════════════════════════════════════════════

def _s(v) -> str:
    return str(v if v is not None else "").strip()


def _limpiar_ext(v) -> str:
    """El padrón guarda el número exterior como VARCHAR con decimal ('607.0')
    o como texto ('LT 1', '-1.0'). Se normaliza sólo para MOSTRAR; el dato
    crudo se conserva en la evidencia."""
    t = _s(v)
    if not t:
        return ""
    if re.fullmatch(r"-?\d+\.0+", t):
        return t.split(".")[0]
    return t


def _limpiar_int(v) -> str:
    t = _s(v)
    if not t:
        return ""
    if re.fullmatch(r"-?\d+\.0+", t):
        return t.split(".")[0]
    return t


def _cp5(v) -> str:
    t = re.sub(r"[^0-9]", "", _s(v))
    return t[:5] if len(t) >= 5 else t


def _nombre(idt: dict) -> str:
    n = _s(idt.get("nombre_completo"))
    if n:
        return n
    partes = [_s(idt.get("nombre")), _s(idt.get("paterno")), _s(idt.get("materno"))]
    return " ".join(p for p in partes if p).strip() or "(sin nombre)"


def construir_nodos(data: dict, *, incluir_vecinos: bool = True,
                    incluir_apellido: bool = True,
                    limite_vecinos: int = 250,
                    limite_total: int = 800) -> tuple[list[dict], dict]:
    """Traduce el dossier de inteligencia a nodos mapeables.

    Devuelve (nodos, resumen_nodos) donde resumen_nodos cuenta por categoría
    ANTES de aplicar el límite, para poder reportar lo que se recortó.
    """
    dossier = data.get("dossier_sujetos") or []
    nodos: list[dict] = []
    resumen = {"dossier": len(dossier), "por_kind": {}, "omitidos": 0}

    # ── 1. Servicios CFE del predio: se indexan por número de servicio para
    # enriquecer después las fichas CFE con su titular y su dirección exacta.
    servicios = {}
    for s in (data.get("servicios_cfe_inmueble") or []):
        num = _s(s.get("numero_servicio"))
        if num:
            servicios[num] = s

    for x in dossier:
        cat = _s(x.get("categoria")).lower()
        kind = _CATEGORIA_A_KIND.get(cat, "vecino")
        if kind == "vecino" and not incluir_vecinos:
            resumen["omitidos"] += 1
            continue
        if kind == "apellido" and not incluir_apellido:
            resumen["omitidos"] += 1
            continue

        idt = x.get("identidad") or {}
        curp = _s(idt.get("curp")).upper()
        calle = _s(idt.get("calle"))
        ext = _limpiar_ext(idt.get("ext"))
        int_ = _limpiar_int(idt.get("int") or idt.get("interior"))
        colonia = _s(idt.get("colonia"))
        cp = _cp5(idt.get("cp"))

        # Los titulares CFE del dossier no siempre traen el domicilio partido
        # en columnas: en ese caso el backend volcó "AV MORENA 806 DEP 1 CP
        # 03020" entero dentro de `calle`. Se intenta separar el número y el CP
        # del blob para no mandar basura al geocodificador.
        if kind == "cfe" and not ext and calle:
            m = re.match(r"^(.*?)[,\s]+(?:No\.?\s*)?(\d{1,5}[A-Z]?)\b(.*)$", calle, re.IGNORECASE)
            if m:
                calle, ext, resto = m.group(1).strip(), m.group(2), m.group(3)
                mc = re.search(r"CP\s*\.?\s*(\d{5})", resto, re.IGNORECASE)
                if mc and not cp:
                    cp = mc.group(1)
                mi = re.search(r"\bD?EP\.?\s*([A-Z0-9\-]+)", resto, re.IGNORECASE)
                if mi and not int_:
                    int_ = mi.group(1)

        hallazgos = x.get("hallazgos") or {}
        extra = {}
        for etiqueta, clave in (
            ("Empleo (IMSS)", "empleo_imss"),
            ("Salud (IMSS)", "salud_imss"),
            ("Telefonía", "telefonia"),
            ("Banca", "banca"),
        ):
            regs = hallazgos.get(clave) or []
            if regs:
                extra[etiqueta] = str(len(regs)) + " registro(s)"

        nodo = {
            "id": _s(x.get("id")) or f"N{len(nodos) + 1}",
            "kind": kind,
            "nombre": _nombre(idt),
            "curp": curp,
            "fecnac": _s(idt.get("fecnac")),
            "calle": calle,
            "ext": ext,
            "int_": int_,
            "colonia": colonia,
            "cp": cp,
            "municipio": "",
            "estado": "",
            "fuente": _s(idt.get("fuente")) or "padron_ine",
            "evidencia": _s(x.get("dictamen_analitico")) or _s(x.get("relacion_origen")),
            "relacion": _s(x.get("relacion_origen")),
            "servicio_cfe": _s(idt.get("servicio_cfe")),
            "extra": extra,
            "lat": None,
            "lon": None,
            "precision": "",
            "geo_query": "",
            "geo_display": "",
        }
        # El titular CFE trae el número de servicio: se completa con la
        # dirección y la zona que reportó CFE para esa cuenta.
        srv = servicios.get(nodo["servicio_cfe"])
        if srv:
            nodo["extra"]["CFE · titular"] = _s(srv.get("titular"))
            nodo["extra"]["CFE · zona"] = _s(srv.get("zona"))
            nodo["extra"]["CFE · agencia"] = _s(srv.get("agencia"))
            nodo["extra"]["CFE · coincidencia de domicilio"] = (
                "sí" if srv.get("coincidencia_domicilio") else "no")
            if not nodo["cp"]:
                nodo["cp"] = _cp5(srv.get("cp"))
            if not nodo["colonia"]:
                nodo["colonia"] = _s(srv.get("colonia"))

        nodos.append(nodo)
        resumen["por_kind"][kind] = resumen["por_kind"].get(kind, 0) + 1

    # ── 2. Tope: los vecinos son el grueso y en un expediente grande pueden
    # ser miles. Se recortan por kind conservando siempre el sujeto y su
    # núcleo, y se informa cuántos quedaron fuera.
    if len(nodos) > limite_total:
        prioridad = {"sujeto": 0, "familiar": 1, "conviviente": 2, "cfe": 3,
                     "apellido": 4, "vecino": 5}
        nodos.sort(key=lambda n: prioridad.get(n["kind"], 9))
        resumen["omitidos"] += len(nodos) - limite_total
        nodos = nodos[:limite_total]
        # el corte se hizo antes de contar bien: se re-cuenta sobre lo que queda
        resumen["por_kind"] = {}
        for n in nodos:
            resumen["por_kind"][n["kind"]] = resumen["por_kind"].get(n["kind"], 0) + 1

    if incluir_vecinos and limite_vecinos and resumen["por_kind"].get("vecino", 0) > limite_vecinos:
        vecinos_ok = 0
        filtrados = []
        for n in nodos:
            if n["kind"] == "vecino":
                vecinos_ok += 1
                if vecinos_ok > limite_vecinos:
                    resumen["omitidos"] += 1
                    continue
            filtrados.append(n)
        nodos = filtrados
        resumen["por_kind"]["vecino"] = sum(1 for n in nodos if n["kind"] == "vecino")

    return nodos, resumen


def _clave_direccion(n: dict) -> tuple:
    """Deduplica por dirección normalizada: los 97 sujetos del caso real
    colapsan a ~73 direcciones, y sin deduplicar se geocodifica 30% de más."""
    calle = re.sub(r"[^A-Z0-9]", "", n["calle"].upper())
    return (calle, n["ext"].upper(), n["colonia"].upper(), n["cp"])


# ══════════════════════════════════════════════════════════════════════════════
# Geocodificación (Nominatim local primero, público después, con caché)
# ══════════════════════════════════════════════════════════════════════════════

def _cargar_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _guardar_cache(path: Path, cache: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _nominatim(base: str, params: dict, timeout: int = 20):
    url = base.rstrip("/") + "/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "es"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _probar_local(base: str) -> bool:
    """El Nominatim local del proyecto vive en :8088; el host puede tenerlo
    apagado. Se prueba UNA vez por corrida: si no contesta, toda la corrida va
    contra el público (con su límite de 1 req/s) en vez de pagar el timeout en
    cada dirección."""
    if not base:
        return False
    try:
        _nominatim(base, {"q": "Mexico", "format": "jsonv2", "limit": "1"}, timeout=4)
        return True
    except Exception:
        return False


def geocodificar(nodos: list[dict], cache_path: Path, *, progress=None,
                 dormir_publico: float = 1.1) -> dict:
    """Rellena lat/lon/colonia/CP en los nodos.

    Estrategia por dirección (una sola vez por dirección distinta):
      1. `calle ext, colonia, cp, municipio, estado, Mexico`  → precisión alta
      2. `colonia, cp, estado, Mexico`                        → precisión media
      3. `cp, Mexico`                                         → precisión baja
    Sólo se acepta un resultado dentro del bbox de México.
    """
    cache = _cargar_cache(cache_path)
    local_url = os.environ.get("NOMINATIM_LOCAL_URL", "http://127.0.0.1:8088")
    public_url = "https://nominatim.openstreetmap.org"
    usar_local = _probar_local(local_url)
    base = local_url if usar_local else public_url
    dormir = 0.0 if usar_local else dormir_publico

    stats = {"direcciones": 0, "desde_cache": 0, "geocodificadas": 0,
             "sin_geo": 0, "fallos": 0, "nominatim": "local" if usar_local else "publico"}

    vistas: dict = {}
    pendientes = []
    for n in nodos:
        k = _clave_direccion(n)
        if k in vistas:
            continue
        vistas[k] = None
        pendientes.append(n)
    stats["direcciones"] = len(pendientes)

    total = len(pendientes)
    for i, n in enumerate(pendientes, 1):
        k = _clave_direccion(n)
        ck = "|".join(k)
        hit = cache.get(ck)
        if hit is None:
            partes = [p for p in (
                f"{n['calle']} {n['ext']}".strip(), n["colonia"],
                n["municipio"], n["estado"]) if p]
            intentos = []
            if n["cp"]:
                intentos.append(("direccion+colonia+cp",
                                 ", ".join(partes + [n["cp"], "Mexico"]) if partes else f"{n['cp']}, Mexico"))
            elif partes:
                intentos.append(("direccion+colonia", ", ".join(partes + ["Mexico"])))
            if n["colonia"] or n["cp"]:
                intentos.append(("colonia+cp",
                                 ", ".join([p for p in (n["colonia"], n["cp"], "Mexico") if p])))
            if n["cp"]:
                intentos.append(("cp", f"{n['cp']}, Mexico"))

            hit = None
            for precision, q in intentos:
                try:
                    res = _nominatim(base, {"q": q,
                                            "format": "jsonv2" if usar_local else "json",
                                            "limit": "5"})
                    for r in (res if isinstance(res, list) else []):
                        try:
                            lat, lon = float(r["lat"]), float(r["lon"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if MX_LAT_MIN <= lat <= MX_LAT_MAX and MX_LON_MIN <= lon <= MX_LON_MAX:
                            hit = {"lat": lat, "lon": lon,
                                   "display": _s(r.get("display_name")),
                                   "tipo": _s(r.get("type") or r.get("class")),
                                   "precision": precision, "query": q}
                            break
                except Exception:
                    stats["fallos"] += 1
                if hit:
                    break
                if dormir:
                    time.sleep(dormir)
            cache[ck] = hit or {"lat": None, "lon": None, "display": "", "tipo": "",
                                "precision": "sin_geo", "query": (intentos[0][1] if intentos else "")}
            _guardar_cache(cache_path, cache)
            if hit:
                stats["geocodificadas"] += 1
            else:
                stats["sin_geo"] += 1
            if dormir:
                time.sleep(dormir)
        else:
            stats["desde_cache"] += 1
            if not hit.get("lat"):
                stats["sin_geo"] += 1

        vistas[k] = hit
        if progress and total:
            progress(int(100 * i / total),
                     f"Geocodificando direcciones ({i}/{total})")

    for n in nodos:
        hit = vistas.get(_clave_direccion(n)) or {}
        n["lat"] = hit.get("lat")
        n["lon"] = hit.get("lon")
        n["precision"] = hit.get("precision") or "sin_geo"
        n["geo_query"] = hit.get("query") or ""
        n["geo_display"] = hit.get("display") or ""
        n["geo_tipo"] = hit.get("tipo") or ""

    return stats


def resolver_municipio_estado(nodos: list[dict], geo_db: Path | None = None) -> int:
    """Rellena municipio/estado desde el SEPOMEX local (bases/geo.db) por CP.
    Es offline y exacto; cuando no hay CP se deja vacío en vez de inventarlo."""
    if geo_db is None:
        geo_db = Path(os.environ.get("BASES_DIR", "")) / "geo.db"
    try:
        import sqlite3
        con = sqlite3.connect(str(geo_db))
    except Exception:
        return 0
    n_ok = 0
    cache: dict[str, tuple] = {}
    try:
        for n in nodos:
            cp = n["cp"]
            if not cp:
                continue
            if cp not in cache:
                try:
                    row = con.execute(
                        "SELECT municipio, estado FROM cp WHERE cp = ? LIMIT 1",
                        (cp,)).fetchone()
                except Exception:
                    row = None
                cache[cp] = row or ("", "")
            mun, edo = cache[cp]
            if mun:
                n["municipio"] = str(mun)
                n["estado"] = str(edo)
                n_ok += 1
    finally:
        con.close()
    return n_ok


# ══════════════════════════════════════════════════════════════════════════════
# Mosaicos (tiles)
# ══════════════════════════════════════════════════════════════════════════════

def _deg2tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def _tiles_bbox(bbox, z: int) -> set:
    s, w, n_, e = bbox
    x1, y1 = _deg2tile(n_, w, z)
    x2, y2 = _deg2tile(s, e, z)
    fuera = set()
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            fuera.add((z, x, y))
    return fuera


def elegir_tiles(nodos: list[dict], *, presupuesto: int = 1600,
                 anillo: int = 1) -> tuple[set, list[dict]]:
    """Elige los mosaicos a embeber dentro de un presupuesto.

    Contexto nacional → metropolitano → cada punto, en ese orden, y sólo se
    añade un nivel si todavía cabe en el presupuesto: así un expediente con
    familia en cinco estados no dispara la descarga a decenas de miles de
    mosaicos, y uno concentrado en una colonia sí llega a nivel de calle.
    """
    pts = [(n["lat"], n["lon"]) for n in nodos if n.get("lat")]
    if not pts:
        return set(), []
    lats = [a for a, _ in pts]
    lons = [b for _, b in pts]
    detalle = []

    def caja(pad):
        return (min(lats) - pad, min(lons) - pad, max(lats) + pad, max(lons) + pad)

    want: set = set()
    # 1) contexto: se sube de zoom mientras el encuadre siga siendo barato
    for z in range(4, 12):
        t = _tiles_bbox(caja(0.6), z)
        if len(want) + len(t) > presupuesto:
            break
        want |= t
        detalle.append({"nivel": f"contexto z{z}", "tiles": len(t)})
    # 2) metropolitano
    for z in range(12, 15):
        t = _tiles_bbox(caja(0.02), z)
        if len(want) + len(t) > presupuesto:
            break
        want |= t
        detalle.append({"nivel": f"metro z{z}", "tiles": len(t)})
    # 3) cada punto: calle (z15) y portal (z16) + anillo, si cabe.
    #    El anillo importa: al hacer zoom sobre un marcador el visor pide los
    #    mosaicos VECINOS; sin ellos la vista se llena de huecos en cuanto se
    #    pierde la red, que es justo lo que este archivo existe para evitar.
    for lat, lon in pts:
        for z in (15, 16):
            if len(want) >= presupuesto:
                break
            x, y = _deg2tile(lat, lon, z)
            want.add((z, x, y))
    if anillo:
        for z in (14, 15, 16):
            for lat, lon in pts:
                if len(want) >= presupuesto:
                    break
                x, y = _deg2tile(lat, lon, z)
                for dx in range(-anillo, anillo + 1):
                    for dy in range(-anillo, anillo + 1):
                        want.add((z, x + dx, y + dy))
    return want, detalle


def descargar_tiles(tiles: set, cache_dir: Path, *, progress=None,
                     pausa: float = 0.5, reintentos: int = 2) -> dict:
    """Descarga a disco los mosaicos que falten. La caché es un directorio
    plano `z_x_y.png` compartido entre corridas: lo ya bajado no se repite."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    stats = {"pedidos": len(tiles), "en_cache": 0, "descargados": 0,
             "fallidos": 0, "fuera_de_rango": 0}
    pendientes = []
    for (z, x, y) in sorted(tiles):
        if (cache_dir / f"{z}_{x}_{y}.png").exists():
            stats["en_cache"] += 1
        elif z > 19:
            stats["fuera_de_rango"] += 1
        else:
            pendientes.append((z, x, y))

    for i, (z, x, y) in enumerate(pendientes, 1):
        dest = cache_dir / f"{z}_{x}_{y}.png"
        url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        for intento in range(reintentos + 1):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": USER_AGENT,
                                  "Referer": "https://proyecto-kyc.local/"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = r.read()
                if data[:4] == b"\x89PNG":
                    dest.write_bytes(data)
                    stats["descargados"] += 1
                else:
                    stats["fallidos"] += 1
                break
            except Exception:
                if intento == reintentos:
                    stats["fallidos"] += 1
                else:
                    time.sleep(1.5 * (intento + 1))
        if progress and (i % 25 == 0 or i == len(pendientes)):
            progress(int(100 * i / max(1, len(pendientes))),
                     f"Descargando mosaicos ({i}/{len(pendientes)})")
        time.sleep(pausa)
    return stats


def embeber_tiles(tiles: set, cache_dir: Path, *, progreso=None) -> tuple[dict, int]:
    """Lee los PNG de la caché, los reconvierte a WebP (≈45% menos peso) y los
    devuelve en base64. Se usa PIL porque el WebP de un PNG de OSM baja de
    ~14 KB a ~8 KB, y con miles de mosaicos eso decide si el HTML es manejable."""
    try:
        from PIL import Image
    except Exception:
        Image = None

    out, peso = {}, 0
    for i, (z, x, y) in enumerate(sorted(tiles), 1):
        p = cache_dir / f"{z}_{x}_{y}.png"
        if not p.exists():
            continue
        raw = p.read_bytes()
        if Image is not None:
            try:
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                buf = io.BytesIO()
                im.save(buf, "WEBP", quality=80, method=6)
                raw = buf.getvalue()
                mime = "webp"
            except Exception:
                mime = "png"
        else:
            mime = "png"
        out[f"{z}_{x}_{y}"] = base64.b64encode(raw).decode()
        peso += len(raw)
        if progreso and (i % 250 == 0):
            progreso(f"empaquetando mosaicos ({i})")
    return out, peso


# ══════════════════════════════════════════════════════════════════════════════
# CSV — el mismo dataset del mapa, para que sea trazable y no sólo visual
# ══════════════════════════════════════════════════════════════════════════════

COLUMNAS_CSV = [
    "categoria", "nombre", "curp", "fecnac", "calle", "numero_exterior",
    "numero_interior", "colonia", "cp", "municipio", "estado", "lat", "lon",
    "precision_geo", "consulta_nominatim", "respuesta_nominatim", "fuente",
    "servicio_cfe", "evidencia",
]


def generar_csv(nodos: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNAS_CSV)
    for n in nodos:
        w.writerow([
            n.get("kind", ""), n.get("nombre", ""), n.get("curp", ""),
            n.get("fecnac", ""), n.get("calle", ""), n.get("ext", ""),
            n.get("int_", ""), n.get("colonia", ""), n.get("cp", ""),
            n.get("municipio", ""), n.get("estado", ""),
            n.get("lat") if n.get("lat") is not None else "",
            n.get("lon") if n.get("lon") is not None else "",
            n.get("precision", ""), n.get("geo_query", ""),
            n.get("geo_display", ""), n.get("fuente", ""),
            n.get("servicio_cfe", ""), n.get("evidencia", ""),
        ])
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# HTML — plantilla de una sola pieza, sin red
# ══════════════════════════════════════════════════════════════════════════════

_PLANTILLA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>@@TITULO@@</title>
<style>
@@CSS@@

/* ── capa propia del mapa familiar (encima del CSS de Leaflet) ───────────── */
html,body{margin:0;padding:0;height:100%;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#0b1220;color:#e6ecf5}
#wrap{display:flex;height:100vh;overflow:hidden}
#side{width:410px;min-width:410px;background:#111a2e;border-right:1px solid #1f2b45;display:flex;flex-direction:column;overflow:hidden}
#side header{padding:14px 16px;border-bottom:1px solid #1f2b45}
#side h1{font-size:15px;margin:0 0 4px}
#side .sub{font-size:11.5px;color:#8ea1c0;line-height:1.45}
#side .sub code{background:#1b2740;padding:1px 4px;border-radius:3px;font-size:10.5px;color:#bcd0ef}
#dictamen{margin:10px 12px 0;padding:9px 11px;background:#132038;border:1px solid #22304e;border-radius:8px;font-size:11.5px;line-height:1.5}
#dictamen .h{font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:#7c8db0;margin-bottom:5px}
#resumen{padding:8px 14px;border-bottom:1px solid #1f2b45;font-size:11px;color:#8ea1c0;line-height:1.6}
#filtros{padding:8px 12px;border-bottom:1px solid #1f2b45;display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:11px;padding:4px 9px;border-radius:20px;border:1px solid #2b3a5c;background:#16213a;cursor:pointer;user-select:none;display:flex;align-items:center;gap:5px}
.chip.off{opacity:.35}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:none}
#lista{overflow-y:auto;flex:1;padding:8px}
.card{background:#16213a;border:1px solid #22304e;border-radius:8px;padding:9px 11px;margin-bottom:7px;cursor:pointer;transition:border-color .15s}
.card:hover{border-color:#3b82f6}
.card.sel{border-color:#e11d48;background:#1c2540}
.card .t{font-size:12.5px;font-weight:600;color:#f1f5fb;display:flex;align-items:center;gap:6px}
.card .l{font-size:11px;color:#8ea1c0;margin-top:3px;line-height:1.4}
.card .m{font-size:10.5px;color:#64748b;margin-top:4px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.badge{font-size:9.5px;padding:1px 6px;border-radius:4px;background:#22304e;color:#9db2d4;text-transform:uppercase;letter-spacing:.3px}
#map{flex:1;height:100vh;background:#0b1220}
.legend{background:rgba(17,26,46,.94);padding:8px 10px;border-radius:8px;font-size:11px;color:#dbe6f7;border:1px solid #2b3a5c}
.legend div{margin:2px 0;display:flex;align-items:center;gap:6px}
.pop{font-size:12px;line-height:1.5;max-width:380px}
.pop h3{margin:0 0 4px;font-size:13.5px}
.pop .k{font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:#64748b;margin-top:6px}
.pop .v{font-size:11.5px}
.pop code{background:#eef2ff;color:#1e3a8a;padding:1px 4px;border-radius:3px;font-size:10.5px}
a.gm{display:inline-block;margin-top:7px;font-size:11px;color:#2563eb;text-decoration:none}
.singo{color:#f59e0b}
#offline-note{position:fixed;bottom:8px;left:424px;z-index:500;background:rgba(22,33,58,.94);border:1px solid #2b3a5c;border-radius:6px;padding:5px 10px;font-size:10.5px;color:#8ea1c0}
</style>
</head>
<body>
<div id="wrap">
  <div id="side">
    <header>
      <h1>@@ENCABEZADO@@</h1>
      <div class="sub">@@SUBTITULO@@</div>
    </header>
    <div id="dictamen"><div class="h">Dictamen del expediente</div>@@DICTAMEN@@</div>
    <div id="resumen"></div>
    <div id="filtros"></div>
    <div id="lista"></div>
  </div>
  <div id="map"></div>
</div>
<div id="offline-note">✓ Mapa 100% offline — mosaicos embebidos (WebP). Abre con doble clic, sin internet.</div>
<script>
@@JS@@
</script>
<script>
const DATA = @@DATA@@;
const TILES = @@TILES@@;
const COLOR = @@COLOR@@;
const LABEL = @@LABEL@@;
const RADIO = @@RADIO@@;
const ONLINE = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';

/* La clave del mosaico usa guiones bajos ("14_3654_7299"), igual que el
   nombre del archivo. Con "/" nunca coincidiría y el mapa saldría vacío
   SIN error visible: por eso esta comprobación no se toca a la ligera. */
const CachedLayer = L.TileLayer.extend({
  createTile: function (coords, done) {
    const img = document.createElement('img');
    img.alt = ''; img.setAttribute('role', 'presentation');
    L.DomEvent.on(img, 'load', L.bind(this._tileOnLoad, this, done, img));
    L.DomEvent.on(img, 'error', L.bind(this._tileOnError, this, done, img));
    const k = coords.z + '_' + coords.x + '_' + coords.y;
    const b64 = TILES[k];
    if (b64) { img.src = 'data:image/webp;base64,' + b64; }
    else { img.src = L.Util.template(ONLINE, L.extend({s: 'abc'[Math.abs(coords.x + coords.y) % 3]}, coords)); }
    return img;
  }
});

const map = L.map('map', {zoomControl: true, preferCanvas: true}).setView([19.40, -99.15], 11);
new CachedLayer('', {maxZoom: 19, attribution: '&copy; OpenStreetMap (mosaicos embebidos)'}).addTo(map);

function ico(kind) {
  const c = COLOR[kind] || '#64748b', r = RADIO[kind] || 8;
  return (kind === 'sujeto' || kind === 'familiar')
    ? L.divIcon({className: '', iconSize: [r*2+6, r*2+6], iconAnchor: [r+3, r+3],
        html: '<div style="width:' + (r*2+6) + 'px;height:' + (r*2+6) + 'px;border-radius:50%;background:' + c + ';border:3px solid #fff;box-shadow:0 0 0 2px ' + c + ',0 2px 6px rgba(0,0,0,.5)"></div>'})
    : L.divIcon({className: '', iconSize: [r*2, r*2], iconAnchor: [r, r],
        html: '<div style="width:' + (r*2) + 'px;height:' + (r*2) + 'px;border-radius:50%;background:' + c + ';border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.5)"></div>'});
}

const capa = {}, marcadores = {};
let seleccion = null, capaActiva = true;
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}

function popup(f) {
  let extra = '';
  const e = f.extra || {};
  Object.keys(e).forEach(function (k) {
    if (e[k] === '' || e[k] == null) return;
    extra += '<div class="k">' + esc(k) + '</div><div class="v">' + esc(e[k]) + '</div>';
  });
  const gm = f.lat ? '<a class="gm" target="_blank" href="https://www.google.com/maps?q=' + f.lat + ',' + f.lon + '">Abrir en Google Maps ↗</a>' : '';
  const prec = (f.precision === 'sin_geo' || !f.precision)
    ? '<div class="k">ubicación</div><div class="v singo">Sin domicilio en el corpus (no ubicado)</div>'
    : '<div class="k">precisión de la geocodificación</div><div class="v">' + esc(f.precision) + '</div>';
  return '<div class="pop"><h3>' + esc(f.nombre) + '</h3>'
    + '<span class="badge" style="background:' + (COLOR[f.kind] || '#64748b') + ';color:#fff">' + esc(f.kindLabel) + '</span>'
    + (f.curp ? ' <span class="badge">CURP ' + esc(f.curp) + '</span>' : '')
    + '<div class="k">relación con el sujeto</div><div class="v">' + esc(f.relacion || '—') + '</div>'
    + '<div class="k">dirección</div><div class="v">' + esc(f.direccion || '—') + '</div>'
    + '<div class="k">colonia / CP</div><div class="v">' + esc(f.colonia || '—') + ' · ' + esc(f.cp || '—') + '</div>'
    + '<div class="k">municipio / estado</div><div class="v">' + esc(f.municipio || '—') + ', ' + esc(f.estado || '—') + '</div>'
    + prec
    + '<div class="k">coordenadas</div><div class="v"><code>' + esc(f.geo || '—') + '</code></div>'
    + '<div class="k">consulta enviada a Nominatim</div><div class="v">' + esc(f.geo_query || '—') + '</div>'
    + '<div class="k">lo que devolvió Nominatim</div><div class="v">' + esc(f.geo_display || '—') + '</div>'
    + extra
    + '<div class="k">fuente</div><div class="v">' + esc(f.fuente) + '</div>'
    + '<div class="k">evidencia</div><div class="v">' + esc(f.evidencia || '—') + '</div>' + gm + '</div>';
}

DATA.forEach(function (f) {
  if (!capa[f.kind]) capa[f.kind] = L.layerGroup().addTo(map);
  if (f.lat) {
    const m = L.marker([f.lat, f.lon], {icon: ico(f.kind)}).addTo(capa[f.kind]);
    m.bindPopup(popup(f), {maxHeight: 460, autoPan: true});
    m.on('click', function () { sel(f.id, false); });
    marcadores[f.id] = m;
  }
});

const legend = L.control({position: 'bottomright'});
legend.onAdd = function () {
  const d = L.DomUtil.create('div', 'legend');
  d.innerHTML = Object.keys(COLOR).map(function (k) {
    return '<div><span class="dot" style="background:' + COLOR[k] + '"></span>' + LABEL[k] + '</div>';
  }).join('');
  return d;
};
legend.addTo(map);

const filtros = document.getElementById('filtros');
Object.keys(COLOR).forEach(function (k) {
  const n = DATA.filter(function (f) { return f.kind === k; }).length;
  if (!n) return;
  const c = document.createElement('div');
  c.className = 'chip'; c.dataset.kind = k;
  c.innerHTML = '<span class="dot" style="background:' + COLOR[k] + '"></span>' + LABEL[k] + ' (' + n + ')';
  c.onclick = function () {
    c.classList.toggle('off');
    if (c.classList.contains('off')) map.removeLayer(capa[k]); else capa[k].addTo(map);
    render();
  };
  filtros.appendChild(c);
});

function activosAhora() {
  return DATA.filter(function (f) {
    const ch = document.querySelector('.chip[data-kind="' + f.kind + '"]');
    return !ch || !ch.classList.contains('off');
  });
}

function render() {
  const lista = document.getElementById('lista'); lista.innerHTML = '';
  const activos = activosAhora();
  document.getElementById('resumen').innerHTML =
    '<b>' + activos.length + '</b> de ' + DATA.length + ' nodos visibles · '
    + DATA.filter(function (f) { return f.lat; }).length + ' con coordenada · '
    + DATA.filter(function (f) { return !f.lat; }).length + ' sin domicilio en el corpus';
  activos.forEach(function (f) {
    const c = document.createElement('div');
    c.className = 'card' + (seleccion === f.id ? ' sel' : ''); c.dataset.id = f.id;
    c.innerHTML = '<div class="t"><span class="dot" style="background:' + (COLOR[f.kind] || '#64748b') + '"></span>' + esc(f.nombre) + '</div>'
      + '<div class="l">' + esc(f.direccion || 'sin domicilio') + (f.colonia ? ' · ' + esc(f.colonia) : '') + (f.cp ? ' · ' + esc(f.cp) : '') + '</div>'
      + '<div class="m"><span class="badge">' + esc(f.kindLabel) + '</span>'
      + '<span>' + (f.lat ? '<code>' + esc(f.geo) + '</code>' : '<span class="singo">sin ubicación</span>') + '</span>'
      + '<span style="color:#7c8db0">' + esc(String(f.precision || '').split('(')[0]) + '</span></div>';
    c.onclick = function () { sel(f.id, true); };
    lista.appendChild(c);
  });
}

function sel(id, centrar) {
  seleccion = id;
  const f = DATA.find(function (x) { return x.id === id; });
  render();
  if (centrar && f && f.lat) {
    map.setView([f.lat, f.lon], 16, {animate: false});
    if (marcadores[id]) marcadores[id].openPopup();
  }
  const card = document.querySelector('.card[data-id="' + id + '"]');
  if (card) card.scrollIntoView({block: 'nearest'});
}

function verSujeto() {
  const pts = DATA.filter(function (f) { return f.lat && (f.kind === 'sujeto' || f.kind === 'familiar'); })
                  .map(function (f) { return [f.lat, f.lon]; });
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.35));
  else verTodos();
}
function verTodos() {
  const pts = DATA.filter(function (f) { return f.lat; }).map(function (f) { return [f.lat, f.lon]; });
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.15));
}
const nav = L.control({position: 'topleft'});
nav.onAdd = function () {
  const d = L.DomUtil.create('div', 'legend'); d.style.marginTop = '10px';
  d.innerHTML = '<div style="cursor:pointer;font-weight:600" id="btn-princ">🎯 Núcleo del sujeto</div>'
              + '<div style="cursor:pointer;font-weight:600" id="btn-todos">🌎 Todo el universo</div>';
  L.DomEvent.disableClickPropagation(d);
  setTimeout(function () {
    const a = document.getElementById('btn-princ'), b = document.getElementById('btn-todos');
    if (a) a.addEventListener('click', verSujeto);
    if (b) b.addEventListener('click', verTodos);
  }, 0);
  return d;
};
nav.addTo(map);
render();

/* Con ?sel= NO se encuadra el conjunto: el fitBounds competiría con el zoom al
   punto pedido y acabaría ganando, dejando el zoom sin efecto. */
const _q = new URLSearchParams(location.search);
const _sp = _q.get('sel');
if (_sp) {
  const f = DATA.find(function (x) { return String(x.id).toLowerCase() === _sp.toLowerCase(); });
  if (f && f.lat) { sel(f.id, false); map.setView([f.lat, f.lon], 16, {animate: false}); if (marcadores[f.id]) marcadores[f.id].openPopup(); }
  else if ((_q.get('view') || '').toLowerCase() === 'todos') verTodos(); else verSujeto();
} else {
  if ((_q.get('view') || '').toLowerCase() === 'todos') verTodos(); else verSujeto();
}
</script>
</body>
</html>
"""


def _leer_asset(nombre: str) -> str:
    return (ASSETS_DIR / nombre).read_text(encoding="utf-8")


def render_html(nodos: list[dict], tiles_b64: dict, *, titulo: str, encabezado: str,
                subtitulo: str, dictamen: str, fecha: str) -> str:
    """Arma el HTML final. Todo va embebido: CSS, JS y mosaicos."""
    filas = []
    for n in nodos:
        dir_txt = (n.get("calle") or "").strip()
        if n.get("ext"):
            dir_txt = (dir_txt + " " + n["ext"]).strip()
        if n.get("int_"):
            dir_txt += " (int. " + n["int_"] + ")"
        geo = f"{n['lat']:.5f}, {n['lon']:.5f}" if n.get("lat") else ""
        filas.append({
            "id": n.get("id") or "",
            "kind": n.get("kind") or "vecino",
            "kindLabel": next((k["label"] for k in KINDS if k["kind"] == n.get("kind")), "Nodo"),
            "nombre": n.get("nombre") or "",
            "curp": n.get("curp") or "",
            "relacion": n.get("relacion") or "",
            "direccion": dir_txt,
            "colonia": n.get("colonia") or "",
            "cp": n.get("cp") or "",
            "municipio": n.get("municipio") or "",
            "estado": n.get("estado") or "",
            "geo": geo,
            "precision": n.get("precision") or "",
            "geo_query": n.get("geo_query") or "",
            "geo_display": n.get("geo_display") or "",
            "fuente": n.get("fuente") or "",
            "evidencia": n.get("evidencia") or "",
            "extra": n.get("extra") or {},
            "lat": n.get("lat"),
            "lon": n.get("lon"),
        })

    colores = {k["kind"]: k["color"] for k in KINDS}
    etiquetas = {k["kind"]: k["label"] for k in KINDS}
    radios = {k["kind"]: k["radio"] for k in KINDS}

    html = _PLANTILLA
    for marca, valor in (
        ("@@CSS@@", _leer_asset("leaflet.css")),
        ("@@JS@@", _leer_asset("leaflet.js")),
        ("@@DATA@@", json.dumps(filas, ensure_ascii=False)),
        ("@@TILES@@", json.dumps(tiles_b64, separators=(",", ":"))),
        ("@@COLOR@@", json.dumps(colores, ensure_ascii=False)),
        ("@@LABEL@@", json.dumps(etiquetas, ensure_ascii=False)),
        ("@@RADIO@@", json.dumps(radios, ensure_ascii=False)),
        ("@@TITULO@@", titulo),
        ("@@ENCABEZADO@@", encabezado),
        ("@@SUBTITULO@@", subtitulo),
        ("@@DICTAMEN@@", dictamen),
    ):
        html = html.replace(marca, valor)
    if "@@" in html:
        sobrantes = sorted(set(re.findall(r"@@[A-Z_]+@@", html)))
        if sobrantes:
            html = html.replace("@@", "")
    return html


# ══════════════════════════════════════════════════════════════════════════════
# Orquestador
# ══════════════════════════════════════════════════════════════════════════════

def _dictamen_html(data: dict) -> str:
    d = data.get("dictamen_conclusivo") or {}
    partes = []
    if d.get("estatus_identidad"):
        partes.append("<b>Identidad:</b> " + str(d["estatus_identidad"]))
    if d.get("nivel_riesgo_kyc"):
        partes.append("<b>Riesgo:</b> " + str(d["nivel_riesgo_kyc"]))
    if d.get("alertas_fiscales_69_69b"):
        partes.append("<b>Alertas 69/69-B:</b> " + str(d["alertas_fiscales_69_69b"]))
    if d.get("dinamica_relacional"):
        partes.append("<b>Dinámica:</b> " + str(d["dinamica_relacional"]))
    return "<br>".join(partes) or "Sin dictamen."


def generar_mapa_familia(data: dict, *, curp: str = "", rfc: str = "",
                         workdir: Path, progress=None,
                         presupuesto_tiles: int = 1600,
                         tile_cache_dir: Path | None = None,
                         limite_vecinos: int = 250,
                         limite_total: int = 800) -> dict:
    """Genera el mapa familiar autocontenido a partir del payload de inteligencia.

    `progress(mensaje, pct)` se llama con el avance (mensajes en español, aptos
    para mostrarse tal cual en el frontend).

    Devuelve dict con {html, csv, nodos, stats}.
    """
    def avisar(msg, pct=None):
        if progress:
            try:
                progress(msg, pct)
            except TypeError:
                progress(msg)

    t0 = time.time()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    if tile_cache_dir is None:
        tile_cache_dir = workdir.parent / "_tiles_cache"

    avisar("Armando los nodos del universo familiar…", 5)
    nodos, resumen = construir_nodos(data, limite_vecinos=limite_vecinos,
                                     limite_total=limite_total)

    avisar("Resolviendo municipio y estado desde SEPOMEX (offline)…", 12)
    n_sepomex = resolver_municipio_estado(nodos)

    avisar(f"Geocodificando {len(nodos)} nodos…", 18)
    cache_geo = workdir.parent / "_geocode_cache.json"
    try:
        cache_geo = Path(os.environ.get("KYC_GEOCODE_CACHE") or cache_geo)
    except Exception:
        pass
    stats_geo = geocodificar(
        nodos, cache_geo,
        progress=lambda pct, msg: avisar(msg, 18 + int(pct * 0.35)))

    avisar("Eligiendo los mosaicos necesarios…", 55)
    tiles, detalle = elegir_tiles(nodos, presupuesto=presupuesto_tiles)
    stats_tiles = descargar_tiles(
        tiles, tile_cache_dir,
        progress=lambda pct, msg: avisar(msg, 55 + int(pct * 0.35)))

    avisar("Empaquetando mosaicos en el archivo…", 92)
    tiles_b64, peso = embeber_tiles(tiles, tile_cache_dir)

    sujeto = data.get("sujeto_investigado") or curp or "Sujeto"
    titulo = f"Mapa interactivo — universo familiar de {sujeto}"
    encabezado = "🗺️ Universo familiar — " + sujeto
    partes_sub = []
    if curp:
        partes_sub.append("CURP <code>" + curp + "</code>")
    if rfc:
        partes_sub.append("RFC <code>" + rfc + "</code>")
    conteo = " · ".join(f"{k['label']}: {resumen['por_kind'].get(k['kind'], 0)}" for k in KINDS
                        if resumen["por_kind"].get(k["kind"]))
    partes_sub.append(f"{len(nodos)} nodos")
    partes_sub.append(conteo)
    if resumen["omitidos"]:
        partes_sub.append(f"{resumen['omitidos']} nodos omitidos por límite")
    subtitulo = "<br>".join(partes_sub)

    html = render_html(nodos, tiles_b64, titulo=titulo, encabezado=encabezado,
                       subtitulo=subtitulo, dictamen=_dictamen_html(data),
                       fecha=time.strftime("%Y-%m-%d %H:%M"))

    avisar("Mapa listo.", 100)
    return {
        "html": html,
        "csv": generar_csv(nodos),
        "nodos": nodos,
        "stats": {
            "sujeto": sujeto,
            "curp": curp,
            "nodos": len(nodos),
            "por_kind": resumen["por_kind"],
            "omitidos": resumen["omitidos"],
            "con_coordenada": sum(1 for n in nodos if n.get("lat")),
            "sin_domicilio": sum(1 for n in nodos if not n.get("lat")),
            "sepomex_resueltos": n_sepomex,
            "geocodificacion": stats_geo,
            "mosaicos": stats_tiles,
            "mosaicos_detalle": detalle,
            "peso_mosaicos_mb": round(peso / 1024 / 1024, 2),
            "elapsed_s": round(time.time() - t0, 1),
        },
    }
