"""memory.py — Memoria persistente del oráculo por tenant.

Tablas:
  preferencias         (clave, valor) — ej. "estado_default"="OAXACA"
  aliases              (termino_canonico, sinonimos_csv) — "TOBIAS" → "TOBIAS|Tobias|Tovías"
  sujetos_investigados (curp, nombre, ultima_vez, resumen, fuentes_csv)
  feedback             (query, veredicto_user, ajustes_json) — para aprendizaje supervisado

Una BD por tenant: bases/oraculo_mem_<tenant_id>.db
"""
from __future__ import annotations
import sqlite3, time
from pathlib import Path

BASES_DIR = Path(__file__).resolve().parents[2] / "bases"


def _db_path(tenant_id: int) -> Path:
    return BASES_DIR / f"oraculo_mem_{tenant_id}.db"


def _ensure_schema(tenant_id: int):
    p = _db_path(tenant_id)
    con = sqlite3.connect(str(p))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS preferencias (
            clave TEXT PRIMARY KEY,
            valor TEXT,
            updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS aliases (
            termino_canonico TEXT PRIMARY KEY,
            sinonimos TEXT,
            updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS sujetos_investigados (
            curp TEXT PRIMARY KEY,
            nombre TEXT,
            ultima_vez REAL,
            resumen TEXT,
            fuentes TEXT
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            veredicto_user TEXT,
            ajustes TEXT,
            created_at REAL
        );
    """)
    con.commit(); con.close()


def get_preferencias(tenant_id: int) -> dict:
    _ensure_schema(tenant_id)
    con = sqlite3.connect(str(_db_path(tenant_id)))
    rows = con.execute("SELECT clave, valor FROM preferencias").fetchall()
    con.close()
    return {k: v for k, v in rows}


def set_preferencia(tenant_id: int, clave: str, valor: str):
    _ensure_schema(tenant_id)
    con = sqlite3.connect(str(_db_path(tenant_id)))
    con.execute("INSERT OR REPLACE INTO preferencias (clave, valor, updated_at) VALUES (?,?,?)",
                (clave, valor, time.time()))
    con.commit(); con.close()


def get_aliases(tenant_id: int) -> dict:
    _ensure_schema(tenant_id)
    con = sqlite3.connect(str(_db_path(tenant_id)))
    rows = con.execute("SELECT termino_canonico, sinonimos FROM aliases").fetchall()
    con.close()
    return {k: (v or "").split("|") for k, v in rows}


def set_alias(tenant_id: int, canonico: str, sinonimos: list[str]):
    _ensure_schema(tenant_id)
    con = sqlite3.connect(str(_db_path(tenant_id)))
    con.execute("INSERT OR REPLACE INTO aliases (termino_canonico, sinonimos, updated_at) VALUES (?,?,?)",
                (canonico, "|".join(sinonimos), time.time()))
    con.commit(); con.close()


def remember_sujeto(tenant_id: int, curp: str, nombre: str, resumen: str, fuentes: str = ""):
    _ensure_schema(tenant_id)
    con = sqlite3.connect(str(_db_path(tenant_id)))
    con.execute("""INSERT OR REPLACE INTO sujetos_investigados
                   (curp, nombre, ultima_vez, resumen, fuentes) VALUES (?,?,?,?,?)""",
                (curp, nombre, time.time(), resumen, fuentes))
    con.commit(); con.close()


def get_sujetos_recientes(tenant_id: int, limit: int = 10) -> list[dict]:
    _ensure_schema(tenant_id)
    con = sqlite3.connect(str(_db_path(tenant_id)))
    rows = con.execute("""SELECT curp, nombre, resumen, datetime(ultima_vez,'unixepoch') as ts
                         FROM sujetos_investigados ORDER BY ultima_vez DESC LIMIT ?""", (limit,)).fetchall()
    con.close()
    return [{"curp": r[0], "nombre": r[1], "resumen": r[2], "ultima_vez": r[3]} for r in rows]


def add_feedback(tenant_id: int, query: str, veredicto_user: str, ajustes: str = ""):
    _ensure_schema(tenant_id)
    con = sqlite3.connect(str(_db_path(tenant_id)))
    con.execute("INSERT INTO feedback (query, veredicto_user, ajustes, created_at) VALUES (?,?,?,?)",
                (query, veredicto_user, ajustes, time.time()))
    con.commit(); con.close()


def render_memory_for_prompt(tenant_id: int) -> str:
    """Genera el bloque de memoria que se inyecta al system prompt."""
    prefs = get_preferencias(tenant_id)
    aliases = get_aliases(tenant_id)
    suj = get_sujetos_recientes(tenant_id, 5)
    if not prefs and not aliases and not suj:
        return "(tenant sin memoria previa)"
    lines = []
    if prefs:
        lines.append("Preferencias:")
        for k, v in list(prefs.items())[:10]:
            lines.append(f"  - {k}: {v}")
    if aliases:
        lines.append("Aliases:")
        for k, vs in list(aliases.items())[:10]:
            lines.append(f"  - {k} ↔ {'|'.join(vs)}")
    if suj:
        lines.append("Sujetos investigados recientemente:")
        for s in suj[:5]:
            lines.append(f"  - {s['curp']} | {s['nombre']} | {s.get('ultima_vez','')}")
    return "\n".join(lines)
