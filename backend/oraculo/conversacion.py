"""conversacion.py — Oráculo conversacional con validación por pasos.

Diseño (2026-11-18): el flujo NO lo decide el LLM. El motor anterior le dejaba
elegir qué capa llamar y, cuando el modelo contestaba con una frase en vez de
una tool_call, el ciclo se cortaba y esa frase se volvía el "veredicto"
("Voy a iniciar la cadena de reconciliación", 0 candidatos, INCIERTO).

Aquí el estado vive en el servidor y avanza por fases:

    nombre → desambiguar → confirmar → profundizar → cerrado

Reglas de producto (pedidas por el usuario):
  * Se validan los datos de identidad antes de tocar las bases: nombre →
    CURP → fecha de nacimiento → entidad.
  * Un dato queda firme si el padrón lo devuelve idéntico; si hay homónimos o
    variantes, la conversación se detiene y pregunta.
  * Nada de consultas de pago (RENAPO/Singula). Sólo capas gratuitas: padrón,
    variantes y las 29 bases federadas por RFC + CFE.
  * El hilo se guarda por usuario: al volver se retoma con su estado.

Cada turno devuelve una lista de bloques que el frontend pinta tal cual:
texto, pregunta, candidatos, datos, hallazgo, cierre, error.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
import uuid
from pathlib import Path

BASES = Path(__file__).resolve().parents[2] / "bases"
PADRON = BASES / "padron_v1.duckdb"
CHAT_DB = BASES / "oraculo_chat.db"

# 32 entidades, para reconocer "en Oaxaca" dentro del texto libre
ENTIDADES = {
    1: "Aguascalientes", 2: "Baja California", 3: "Baja California Sur",
    4: "Campeche", 5: "Coahuila", 6: "Colima", 7: "Chiapas", 8: "Chihuahua",
    9: "Ciudad de México", 10: "Durango", 11: "Guanajuato", 12: "Guerrero",
    13: "Hidalgo", 14: "Jalisco", 15: "Estado de México", 16: "Michoacán",
    17: "Morelos", 18: "Nayarit", 19: "Nuevo León", 20: "Oaxaca", 21: "Puebla",
    22: "Querétaro", 23: "Quintana Roo", 24: "San Luis Potosí", 25: "Sinaloa",
    26: "Sonora", 27: "Tabasco", 28: "Tamaulipas", 29: "Tlaxcala",
    30: "Veracruz", 31: "Yucatán", 32: "Zacatecas",
}
_ALIAS_EDO = {
    "CDMX": 9, "DF": 9, "DISTRITO FEDERAL": 9, "EDOMEX": 15, "EDO MEX": 15,
    "MEXICO": 15, "NUEVO LEON": 19, "NL": 19, "QROO": 23, "SLP": 24,
    "BC": 2, "BCS": 3, "VER": 30, "JAL": 14, "GTO": 11, "PUE": 21, "YUC": 31,
}
PARTICULAS = {"DE", "DEL", "LA", "LAS", "LOS", "SAN", "SANTA", "Y", "VDA", "VIUDA"}

CURP_RE = re.compile(r"\b([A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d)\b")
FECHA_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
FECHA_DMA = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
FECHA_LARGA = re.compile(
    r"\b(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|setiembre|octubre|noviembre|diciembre)\s+(?:de\s+|del\s+)?(\d{4})\b",
    re.IGNORECASE)
MESES = {m: i + 1 for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"])}

# comparados contra _norm() del texto, que va en mayúsculas sin acentos
AFIRMATIVO = {"SI", "S", "CLARO", "CORRECTO", "ES", "ESE", "ESA", "EL ES",
              "ELLA ES", "SI ES", "AFIRMATIVO", "EXACTO", "ASI ES"}
NEGATIVO = {"NO", "N", "NEL", "NEGATIVO", "NO ES", "OTRO", "OTRA", "NINGUNO"}


# ─────────────────────────── utilidades de texto ───────────────────────────

def _sin_acentos(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", _sin_acentos(s or "").upper()).strip()


def _limpio(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _titulo(s: str) -> str:
    """'SEBASTIAN VERNIS MORA' → 'Sebastian Vernis Mora' (sin gritar)."""
    return " ".join(w.capitalize() if len(w) > 2 else w for w in _limpio(s).lower().split())


def _entidad_de(texto: str):
    """Devuelve (clave, nombre) si el texto menciona un estado."""
    t = " " + _norm(texto) + " "
    for clave, nombre in ENTIDADES.items():
        if " " + _norm(nombre) + " " in t:
            return clave, nombre
    for alias, clave in _ALIAS_EDO.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", t):
            return clave, ENTIDADES[clave]
    return None, None


def _fecha_de(texto: str):
    """Devuelve 'YYYY-MM-DD' si hay fecha reconocible."""
    m = FECHA_ISO.search(texto)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = FECHA_DMA.search(texto)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = FECHA_LARGA.search(texto)
    if m:
        mes = MESES.get(_sin_acentos(m.group(2).lower()), 0)
        if mes:
            return f"{m.group(3)}-{mes:02d}-{int(m.group(1)):02d}"
    # "14 de julio de 1998" → ya cubierto; "julio 1998" no: ambiguo, se ignora
    return None


def _fecha_legible(iso: str) -> str:
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    try:
        a, m, d = iso.split("-")
        return f"{int(d)} de {meses[int(m) - 1]} de {a}"
    except Exception:
        return iso or "—"


def _edad(iso: str) -> str:
    try:
        a, m, d = (int(x) for x in iso.split("-"))
        hoy = time.localtime()
        e = hoy.tm_year - a - ((hoy.tm_mon, hoy.tm_mday) < (m, d))
        return f"{e} años"
    except Exception:
        return ""


def _candidato_valido(curp: str) -> bool:
    """CURP de 18 con estructura correcta (no valida el dígito verificador)."""
    return bool(re.fullmatch(r"[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d", curp or ""))


# ─────────────────────────── persistencia del hilo ───────────────────────────

def _con():
    con = sqlite3.connect(str(CHAT_DB), timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _init():
    con = _con()
    try:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS conversaciones (
                id TEXT PRIMARY KEY,
                user_id INTEGER, tenant_id INTEGER,
                titulo TEXT, estado_json TEXT,
                created_at REAL, updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS mensajes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conv_id TEXT, rol TEXT, bloques_json TEXT, created_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON mensajes(conv_id, id);
            CREATE INDEX IF NOT EXISTS idx_conv_user ON conversaciones(user_id, updated_at DESC);
        """)
        con.commit()
    finally:
        con.close()


try:
    _init()
except Exception as _e:  # noqa: BLE001
    print(f"[oraculo.chat] no se pudo inicializar {CHAT_DB}: {_e}", flush=True)


def _estado_vacio() -> dict:
    return {
        "fase": "nombre",
        "slots": {},           # nombre/curp/fecnac/entidad -> {valor, fuente, estado}
        "padron": None,        # registro elegido del padrón
        "candidatos": [],      # filas candidatas mientras se desambigua
        "hallazgos": [],       # resultados de las capas gratuitas
        "score": 0.0,
    }


def nueva(user_id: int, tenant_id: int) -> dict:
    cid = uuid.uuid4().hex[:12]
    ahora = time.time()
    st = _estado_vacio()
    con = _con()
    try:
        con.execute(
            "INSERT INTO conversaciones (id, user_id, tenant_id, titulo, estado_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (cid, user_id, tenant_id, "", json.dumps(st, ensure_ascii=False), ahora, ahora))
        con.commit()
    finally:
        con.close()
    _guardar_mensaje(cid, "bot", _bienvenida())
    return cargar(cid, user_id)


def cargar(conv_id: str, user_id: int):
    con = _con()
    try:
        r = con.execute("SELECT * FROM conversaciones WHERE id=? AND user_id=?",
                        (conv_id, user_id)).fetchone()
    finally:
        con.close()
    if not r:
        return None
    conv = dict(r)
    conv["estado"] = json.loads(conv.pop("estado_json") or "{}")
    return conv


def ultima(user_id: int):
    con = _con()
    try:
        r = con.execute("SELECT id FROM conversaciones WHERE user_id=? ORDER BY updated_at DESC LIMIT 1",
                        (user_id,)).fetchone()
    finally:
        con.close()
    return cargar(r["id"], user_id) if r else None


def listar(user_id: int, limite: int = 20) -> list:
    con = _con()
    try:
        rows = con.execute(
            "SELECT id, titulo, updated_at, estado_json FROM conversaciones"
            " WHERE user_id=? ORDER BY updated_at DESC LIMIT ?", (user_id, limite)).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        st = json.loads(r["estado_json"] or "{}")
        out.append({
            "id": r["id"],
            "titulo": r["titulo"] or "sin nombre todavía",
            "fase": st.get("fase"),
            "actualizada": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["updated_at"])),
        })
    return out


def _guardar_estado(conv: dict):
    conv["estado"]["_actualizado"] = time.time()
    titulo = conv["estado"].get("slots", {}).get("nombre", {}).get("valor") or conv.get("titulo") or ""
    con = _con()
    try:
        con.execute("UPDATE conversaciones SET estado_json=?, titulo=?, updated_at=? WHERE id=?",
                    (json.dumps(conv["estado"], ensure_ascii=False), _titulo(titulo),
                     time.time(), conv["id"]))
        con.commit()
    finally:
        con.close()


def _guardar_mensaje(conv_id: str, rol: str, bloques: list):
    con = _con()
    try:
        con.execute("INSERT INTO mensajes (conv_id, rol, bloques_json, created_at) VALUES (?,?,?,?)",
                    (conv_id, rol, json.dumps(bloques, ensure_ascii=False), time.time()))
        con.commit()
    finally:
        con.close()


def historial(conv_id: str) -> list:
    con = _con()
    try:
        rows = con.execute("SELECT rol, bloques_json FROM mensajes WHERE conv_id=? ORDER BY id",
                           (conv_id,)).fetchall()
    finally:
        con.close()
    return [{"rol": r["rol"], "bloques": json.loads(r["bloques_json"] or "[]")} for r in rows]


# ─────────────────────────── bloques de respuesta ───────────────────────────

def _t(texto: str) -> dict:
    return {"tipo": "texto", "texto": texto}


def _p(texto: str, opciones=None, libre=True, placeholder="") -> dict:
    return {"tipo": "pregunta", "texto": texto, "opciones": opciones or [],
            "libre": libre, "placeholder": placeholder}


def _datos(filas: list, titulo: str = "") -> dict:
    return {"tipo": "datos", "titulo": titulo, "filas": filas}


def _hallazgo(estado: str, titulo: str, detalle: str, fuente: str = "") -> dict:
    return {"tipo": "hallazgo", "estado": estado, "titulo": titulo,
            "detalle": detalle, "fuente": fuente}


def _bienvenida() -> list:
    return [
        _t("Hola. Vamos por partes para no gastar consultas a ciegas: primero "
           "confirmo quién es, y ya con la identidad firme reviso las bases."),
        _p("¿A quién buscamos? Dame el nombre completo, como aparece en su INE, "
           "acta o credencial.",
           placeholder="Ej. Sebastián Vernis Mora"),
    ]


# ─────────────────────────── padrón ───────────────────────────

_COLS = ("curp, paterno, materno, nombre, fecnac, sexo, calle, ext, colonia, cp, e, m")


def _padron(curp=None, paterno=None, materno=None, nombre=None, fecnac=None,
            limite=20) -> list:
    import duckdb
    con = duckdb.connect(str(PADRON), read_only=True)
    try:
        where, args = [], []
        if curp:
            where.append("UPPER(curp) = ?")
            args.append(curp.upper())
        else:
            if paterno:
                where.append("UPPER(paterno) = ?")
                args.append(_norm(paterno))
            if materno:
                where.append("UPPER(materno) = ?")
                args.append(_norm(materno))
            if nombre:
                where.append("UPPER(nombre) = ?")
                args.append(_norm(nombre))
        if fecnac:
            where.append("fecnac = ?")
            args.append(fecnac)
        if not where:
            return []
        sql = f"SELECT {_COLS} FROM padron WHERE {' AND '.join(where)} ORDER BY fecnac DESC LIMIT {int(limite)}"
        cur = con.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _padron_por_apellido(paterno=None, materno=None, nombre=None, fecnac=None,
                         limite=20) -> list:
    """Variantes: LIKE por apellidos, útil cuando el orden viene cambiado."""
    import duckdb
    con = duckdb.connect(str(PADRON), read_only=True)
    try:
        where, args = [], []
        if paterno:
            where.append("UPPER(paterno) LIKE ?")
            args.append(f"%{_norm(paterno)}%")
        if materno:
            where.append("UPPER(materno) LIKE ?")
            args.append(f"%{_norm(materno)}%")
        if nombre:
            where.append("UPPER(nombre) LIKE ?")
            args.append(f"%{_norm(nombre)}%")
        if fecnac:
            where.append("fecnac = ?")
            args.append(fecnac)
        if not where:
            return []
        sql = f"SELECT {_COLS} FROM padron WHERE {' AND '.join(where)} ORDER BY fecnac DESC LIMIT {int(limite)}"
        cur = con.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _palabras(texto: str) -> list:
    return [w for w in re.split(r"[\s,]+", _norm(texto)) if w and w not in PARTICULAS]


def _combinaciones(palabras: list) -> list:
    """Órdenes posibles del nombre. Nadie dice si viene nombres primero o
    apellidos primero, así que se prueban los dos."""
    n = len(palabras)
    if n >= 3:
        return [
            {"nombre": " ".join(palabras[:-2]), "paterno": palabras[-2], "materno": palabras[-1]},
            {"nombre": " ".join(palabras[2:]), "paterno": palabras[0], "materno": palabras[1]},
        ]
    if n == 2:
        return [
            {"nombre": palabras[1], "paterno": palabras[0], "materno": None},
            {"nombre": palabras[0], "paterno": palabras[1], "materno": None},
        ]
    return []


def _buscar_personas(palabras: list, fecnac=None, edo=None, limite=20) -> list:
    """Prueba los órdenes posibles: igualdad exacta primero, LIKE como respaldo."""
    out, vistos = [], set()
    for combo in _combinaciones(palabras):
        rows = _padron(paterno=combo["paterno"], materno=combo.get("materno"),
                       nombre=combo["nombre"], fecnac=fecnac, limite=limite)
        exacto = bool(rows)
        if not rows:
            rows = _padron_por_apellido(paterno=combo["paterno"], materno=combo.get("materno"),
                                        nombre=combo["nombre"], fecnac=fecnac, limite=limite)
        for r in rows:
            c = r.get("curp")
            if c and c not in vistos:
                vistos.add(c)
                out.append(r)
        if exacto and out:
            break  # el orden exacto ya resolvió; no mezclar con el otro
    if edo:
        filtrados = [r for r in out if r.get("e") == edo]
        if filtrados:
            out = filtrados
    return out[:limite]


def _fila_datos(row: dict) -> list:
    """Convierte un registro del padrón en filas dato/valor/fuente/estado."""
    edo = ENTIDADES.get(row.get("e"), "")
    dom = " ".join(x for x in [ _limpio(row.get("calle") or ""),
                               (_limpio(row.get("ext") or "").split(".")[0]),
                               _limpio(row.get("colonia") or "") ] if x)
    if row.get("cp"):
        dom += f", C.P. {row['cp']}"
    return [
        {"campo": "Nombre", "valor": _titulo(
            f"{row.get('nombre','')} {row.get('paterno','')} {row.get('materno','')}"),
         "fuente": "Padrón", "estado": "confirmado"},
        {"campo": "CURP", "valor": row.get("curp") or "—", "fuente": "Padrón", "estado": "confirmado"},
        {"campo": "Fecha de nacimiento", "valor": f"{_fecha_legible(row.get('fecnac'))}"
            f" ({_edad(row.get('fecnac'))})", "fuente": "Padrón", "estado": "confirmado"},
        {"campo": "Sexo", "valor": "Hombre" if row.get("sexo") == "H" else "Mujer",
         "fuente": "Padrón", "estado": "confirmado"},
        {"campo": "Entidad", "valor": edo or "—", "fuente": "Padrón", "estado": "confirmado"},
        {"campo": "Domicilio", "valor": dom or "—", "fuente": "Padrón", "estado": "informado"},
    ]


# ─────────────────────────── capas gratuitas ───────────────────────────

_TOOLS_CARGADAS = False


def _importar_tools():
    """Las tools se registran al importar sus módulos (no basta con el paquete)."""
    global _TOOLS_CARGADAS
    if _TOOLS_CARGADAS:
        return
    from .tools import (  # noqa: F401
        base_search, cfe, curp, padron, rfc, wiki_recall,
    )
    from .tools import reconciliacion  # noqa: F401
    _TOOLS_CARGADAS = True


def _ctx(conv: dict):
    from .tools import ToolContext
    return ToolContext(conv["tenant_id"], conv["user_id"], 0)


def _federadas(curp: str, conv: dict) -> dict | None:
    try:
        _importar_tools()
        from .tools import TOOL_REGISTRY
        return TOOL_REGISTRY["recon_federadas"].fn({"curp": curp}, _ctx(conv))
    except Exception as e:  # noqa: BLE001
        return {"error": f"{e}", "rows": [], "count": 0}


def _cohabitacion(row: dict, conv: dict) -> dict | None:
    cp = (row.get("cp") or "").strip()
    if len(cp) != 5:
        return None
    try:
        _importar_tools()
        from .tools import TOOL_REGISTRY
        return TOOL_REGISTRY["recon_cohabitacion"].fn({
            "cp": cp, "calle": row.get("calle") or "",
            "numero": (_limpio(row.get("ext") or "").split(".")[0]),
            "colonia": row.get("colonia") or "", "paterno": row.get("paterno") or "",
        }, _ctx(conv))
    except Exception as e:  # noqa: BLE001
        return {"error": f"{e}", "rows": [], "count": 0}


# ─────────────────────────── el turno ───────────────────────────

def turno(conv: dict, texto: str) -> tuple[list, dict]:
    """Procesa un mensaje del usuario y devuelve (bloques, estado)."""
    st = conv["estado"]
    t = _limpio(texto)
    tn = _norm(t)

    if tn in ("REINICIAR", "EMPEZAR DE NUEVO", "NUEVA BUSQUEDA", "BORRA", "LIMPIAR"):
        conv["estado"] = _estado_vacio()
        _guardar_estado(conv)
        return [_t("Listo, empezamos de cero.")] + _bienvenida(), conv["estado"]

    # Datos que el usuario pueda estar aportando en cualquier momento
    curp = None
    m = CURP_RE.search((t or "").upper())
    if m and _candidato_valido(m.group(1)):
        curp = m.group(1)
    fecnac = _fecha_de(t)
    edo_clave, edo_nombre = _entidad_de(t)

    fase = st.get("fase", "nombre")

    # ── Fase confirmar: espera sí/no ──
    if fase == "confirmar" and st.get("padron"):
        if tn in AFIRMATIVO or tn.startswith("SI"):
            return _confirmar(conv)
        if tn in NEGATIVO or tn.startswith("NO"):
            st["fase"] = "desambiguar"
            st["padron"] = None
            st["candidatos"] = []
            _guardar_estado(conv)
            return [_t("Entendido, ése no era."),
                    _p("Pásame su CURP si la tienes; si no, dime fecha de nacimiento y "
                       "estado (por ejemplo «14/07/1998, Oaxaca») y busco variantes.",
                       placeholder="CURP o fecha y estado")], st

        # Si en vez de confirmar escribe otro nombre, se toma como persona nueva:
        # descartarlo y volver a pedir el nombre sería perder lo que ya escribió.
        if len(_palabras(t)) >= 2 and not (fecnac or edo_clave or curp):
            st.update({"fase": "nombre", "padron": None, "candidatos": [],
                       "buscado": None, "slots": {}})
            _guardar_estado(conv)

    # ── Fase desambiguar: elige candidato por número o aporta datos ──
    if fase == "desambiguar" and st.get("candidatos"):
        cands = st["candidatos"]
        mnum = re.match(r"^\s*(?:el\s+|la\s+)?(\d{1,2})\s*$", tn)
        if mnum and 1 <= int(mnum.group(1)) <= len(cands):
            return _elegir(conv, int(mnum.group(1)) - 1)
        # si no elige, intenta afinar con los datos nuevos
        if not (fecnac or edo_clave or curp or len(t.split()) >= 2):
            return [_p(f"No te entendí. Dime el número del 1 al {len(cands)}, "
                       "o pásame su fecha de nacimiento o el estado donde vive.",
                       opciones=[{"label": str(i + 1), "valor": str(i + 1)}
                                 for i in range(len(cands))])], st

    # ── Identificación por CURP directa ──
    if curp and not st.get("padron"):
        rows = _padron(curp=curp)
        if rows:
            st["candidatos"] = rows
            return _elegir(conv, 0, por_curp=True)
        return [_t(f"El padrón no tiene registrada la CURP {curp}."),
                _p("¿Me confirmas el nombre completo? Con eso la busco por nombre.",
                   placeholder="Nombre completo")], st

    # ── Fase nombre / búsqueda por nombre ──
    # Si ya hay un nombre en el hilo y lo que llega son sólo fecha/estado, se
    # refina esa misma búsqueda en vez de tomarlo como un nombre nuevo.
    palabras = _palabras(t)
    if st.get("buscado") and (fecnac or edo_clave) and len(palabras) <= 2:
        palabras = _palabras(st["buscado"])
    if len(palabras) >= 2 and not st.get("padron"):
        st["buscado"] = " ".join(palabras)
        return _buscar_por_nombre(conv, palabras, fecnac, edo_clave, edo_nombre, curp)

    if fase == "profundizar" or fase == "cerrado":
        return [_t("Esa conversación ya cerró. Si quieres buscar a otra persona, "
                   "dame su nombre completo y abro una búsqueda nueva.")], st

    return [_p("Dame el nombre completo, con apellidos. Si lo prefieres, empieza "
               "por la CURP.", placeholder="Nombre completo o CURP")], st


def _buscar_por_nombre(conv, palabras, fecnac, edo_clave, edo_nombre, curp):
    st = conv["estado"]

    # si el usuario ya dio CURP, ésa manda
    if curp:
        rows = _padron(curp=curp)
        if rows:
            st["candidatos"] = rows
            return _elegir(conv, 0, por_curp=True)

    rows = _buscar_personas(palabras, fecnac=fecnac, edo=edo_clave)
    if not rows:
        return _sin_coincidencias(conv, palabras, fecnac)

    st["candidatos"] = rows
    nombre_buscado = " ".join(palabras)

    if len(rows) == 1:
        return _presentar_unico(conv, rows[0], nombre_buscado)

    bloque_c = {"tipo": "candidatos", "items": [_cand_item(r, i) for i, r in enumerate(rows[:10])],
                "titulo": f"{len(rows)} personas con ese nombre"}
    st["fase"] = "desambiguar"
    _guardar_estado(conv)
    donde = f" en {edo_nombre}" if edo_nombre else ""
    return [
        _t(f"El padrón tiene {len(rows)} personas con el nombre «{_titulo(nombre_buscado)}»{donde}. "
           f"Antes de seguir necesito saber cuál es:"),
        bloque_c,
        _p(f"Dime el número del 1 al {min(len(rows), 10)}. Si sabes su fecha de nacimiento "
           "o la ciudad donde vive, pásamela y lo reduzco.",
           opciones=[{"label": str(i + 1), "valor": str(i + 1)} for i in range(min(len(rows), 10))]),
    ], st


def _sin_coincidencias(conv, palabras, fecnac):
    """Nada con esos datos: reintenta sin filtros y, si tampoco, pide algo más."""
    st = conv["estado"]

    # variantes: se suelta la fecha (suele venir mal o ser la de otra persona)
    variantes = _buscar_personas(palabras, fecnac=None, limite=15) if fecnac else []
    if variantes:
        st["candidatos"] = variantes
        st["fase"] = "desambiguar"
        _guardar_estado(conv)
        return [
            _t("Con esa fecha no aparece nada, pero sin filtrar por fecha salen estas "
               "coincidencias con el mismo nombre:"),
            {"tipo": "candidatos", "titulo": f"{len(variantes)} posibles",
             "items": [_cand_item(r, i) for i, r in enumerate(variantes[:10])]},
            _p(f"¿Alguno es? Dime el número del 1 al {min(len(variantes), 10)}, o pásame "
               "su CURP y salgo de dudas de una vez.",
               opciones=[{"label": str(i + 1), "valor": str(i + 1)}
                         for i in range(min(len(variantes), 10))]),
        ], st

    _guardar_estado(conv)
    return [
        _t("No encontré ese nombre en el padrón (88.4 millones de registros). Puede "
           "venir con otro orden, con un apellido distinto o con un error de captura."),
        _p("¿Me pasas su CURP? Si no la tienes, dime fecha de nacimiento y estado "
           "(«14/07/1998, Oaxaca») y busco variantes.",
           placeholder="CURP, o fecha y estado"),
    ], st


def _cand_item(r: dict, i: int) -> dict:
    dom = " ".join(x for x in [_limpio(r.get("calle") or ""),
                               _limpio(r.get("colonia") or "")] if x)
    return {
        "n": i + 1,
        "curp": r.get("curp"),
        "nombre": _titulo(f"{r.get('nombre','')} {r.get('paterno','')} {r.get('materno','')}"),
        "fecnac": _fecha_legible(r.get("fecnac")),
        "edad": _edad(r.get("fecnac")),
        "entidad": ENTIDADES.get(r.get("e"), ""),
        "domicilio": dom + (f", C.P. {r['cp']}" if r.get("cp") else ""),
    }


def _presentar_unico(conv, row: dict, nombre_buscado: str):
    st = conv["estado"]
    st["padron"] = row
    st["fase"] = "confirmar"
    st["slots"] = {
        "nombre": {"valor": _titulo(f"{row.get('nombre','')} {row.get('paterno','')} {row.get('materno','')}"),
                   "fuente": "padron", "estado": "pendiente"},
        "curp": {"valor": row.get("curp"), "fuente": "padron", "estado": "pendiente"},
        "fecnac": {"valor": row.get("fecnac"), "fuente": "padron", "estado": "pendiente"},
        "entidad": {"valor": ENTIDADES.get(row.get("e"), ""), "fuente": "padron", "estado": "pendiente"},
    }
    _guardar_estado(conv)
    return [
        _t(f"Con «{_titulo(nombre_buscado)}» el padrón devuelve un solo registro, "
           "así que no hay homónimos que descartar. Esto es lo que trae:"),
        _datos(_fila_datos(row), titulo="Del padrón electoral"),
        _p("¿Es esta persona?", opciones=[{"label": "Sí, es", "valor": "si"},
                                         {"label": "No, es otra", "valor": "no"}]),
    ], st


def _elegir(conv: dict, idx: int, por_curp: bool = False):
    st = conv["estado"]
    cands = st.get("candidatos") or []
    if not (0 <= idx < len(cands)):
        return [_p("Ese número no está en la lista. Dime uno de los que aparecen.")], st
    row = cands[idx]
    nombre = _titulo(f"{row.get('nombre','')} {row.get('paterno','')} {row.get('materno','')}")
    st["padron"] = row
    st["fase"] = "confirmar"
    st["slots"] = {
        "nombre": {"valor": nombre, "fuente": "padron", "estado": "pendiente"},
        "curp": {"valor": row.get("curp"), "fuente": "padron", "estado": "pendiente"},
        "fecnac": {"valor": row.get("fecnac"), "fuente": "padron", "estado": "pendiente"},
        "entidad": {"valor": ENTIDADES.get(row.get("e"), ""), "fuente": "padron", "estado": "pendiente"},
    }
    _guardar_estado(conv)
    intro = (f"Esa CURP corresponde a {nombre}." if por_curp
             else f"Anotado: {nombre}.")
    return [
        _t(intro + " Así quedan los datos del padrón:"),
        _datos(_fila_datos(row), titulo="Del padrón electoral"),
        _p("¿Confirmas que es la persona que buscas?",
           opciones=[{"label": "Sí, es", "valor": "si"}, {"label": "No, es otra", "valor": "no"}]),
    ], st


def _confirmar(conv: dict):
    """El usuario confirmó: los datos del padrón quedan firmes y corren las capas gratis."""
    st = conv["estado"]
    row = st.get("padron") or {}
    for k in st.get("slots", {}):
        st["slots"][k]["estado"] = "confirmado"
    st["fase"] = "profundizar"

    bloques = [_t("Perfecto. Quedan firmes nombre, CURP, fecha de nacimiento y entidad. "
                  "Ahora reviso las bases que no cuestan nada.")]

    curp = (row.get("curp") or "").upper()
    rfc10 = curp[:10]
    score = 0.55
    hallazgos = []

    # Capa federadas
    fed = _federadas(curp, conv) if curp else None
    if fed and not fed.get("error"):
        n = fed.get("count") or 0
        bases = fed.get("bases_con_hit") or 0
        if n:
            nombres_bases = sorted({r.get("__entidad") or r.get("__base") or "?"
                                    for r in fed.get("rows", [])})[:6]
            detalle = (f"{n} coincidencia(s) en {bases} de las 29 bases: "
                       f"{', '.join(str(x) for x in nombres_bases)}.")
            bloques.append(_hallazgo("ok", f"Bases federadas · RFC {rfc10}", detalle))
            score += min(0.10 * bases, 0.25)
        else:
            detalle = (f"Ninguna de las 29 bases devolvió el RFC {rfc10}. En alguien sin "
                       "crédito ni línea telefónica a su nombre es lo normal; no "
                       "invalida lo del padrón.")
            bloques.append(_hallazgo("vacio", f"Bases federadas · RFC {rfc10}", detalle))
        hallazgos.append({**bloques[-1], "fuente": f"RFC {rfc10}"})
    elif fed and fed.get("error"):
        bloques.append(_hallazgo("error", "Bases federadas", f"No se pudieron consultar: {fed['error']}"))

    # Capa cohabitación (CFE)
    coh = _cohabitacion(row, conv)
    if coh and not coh.get("error"):
        total = coh.get("count") or 0
        if total:
            dom = f"C.P. {row.get('cp')}"
            if coh.get("titular_es_sujeto"):
                detalle = (f"En {dom} el titular del medidor coincide con el apellido del sujeto; "
                           f"{total} medidor(es) en la dirección.")
                score += 0.10
            else:
                detalle = (f"En {dom} hay {total} medidor(es), pero el titular no es el sujeto: "
                           "el domicilio del padrón no está a su nombre en CFE.")
            if coh.get("familiares_detectados"):
                detalle += f" {len(coh['familiares_detectados'])} medidor(es) con el mismo apellido."
                score += 0.05
            bloques.append(_hallazgo("ok" if coh.get("titular_es_sujeto") else "warn",
                                     f"CFE · domicilio {dom}", detalle))
            hallazgos.append({**bloques[-1], "fuente": f"CFE {dom}"})

    score = round(min(score, 0.95), 2)
    nivel = "ALTO" if score >= 0.8 else "MEDIO" if score >= 0.55 else "BAJO"
    st["score"] = score
    st["hallazgos"] = hallazgos
    st["fase"] = "cerrado"

    # Cierre conversacional
    nombre = st["slots"]["nombre"]["valor"]
    edo = st["slots"]["entidad"]["valor"]
    nac = _fecha_legible(row.get("fecnac"))
    con_hits = [h for h in hallazgos if h["estado"] == "ok"]
    sin_hits = [h for h in hallazgos if h["estado"] in ("vacio", "warn")]

    parrafo = (f"{nombre} quedó identificado con el registro del padrón: CURP "
               f"{row.get('curp')}, nacido el {nac} en {edo or 'México'}. ")
    if con_hits:
        fuentes = "; ".join(h["titulo"] for h in con_hits)
        parrafo += f"Además hay evidencia que lo corrobora en {fuentes}. "
    if sin_hits:
        faltantes = ", ".join(h["titulo"].split("·")[0].strip() for h in sin_hits)
        parrafo += f"No encontré nada en {faltantes}, lo cual es esperable y no contradice la identificación. "
    genero = {"ALTO": "alta", "MEDIO": "media", "BAJO": "baja"}[nivel]
    parrafo += f"Confianza {genero} ({int(score * 100)}%)."

    bloques += [
        _t(parrafo),
        _datos(_fila_datos(row), titulo="Datos confirmados"),
        {"tipo": "cierre", "texto": "¿Lo guardo en el expediente del sujeto?",
         "curp": row.get("curp"), "score": score, "nivel": nivel},
        _p("Si quieres revisar otra persona, dime su nombre y empezamos de nuevo.",
           opciones=[], libre=True, placeholder="Otro nombre, o «reiniciar»"),
    ]
    _guardar_estado(conv)
    return bloques, st
