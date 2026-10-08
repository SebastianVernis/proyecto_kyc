#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""barrido_universal.py — barrido de un identificador sobre TODO el corpus ./bases.

Generaliza el pipeline que se usó a mano en las investigaciones 55 4055 3398 y
56 1377 2366 (scripts/tel_*/sweep_tel.py, fase*.py) a cualquier identificador:

    teléfono · RFC · CURP · NSS · número de servicio CFE · placa · CP/dirección · nombre

Estrategia (2 pasos, con caché):

  1. INVENTARIO  — abre cada base (DuckDB y SQLite), lista tablas, describe
     columnas y cuenta filas. Se guarda en JSON y se reutiliza.
  2. BÚSQUEDA     — para cada identificador arma el predicado correcto según el
     TIPO de columna real (no según un esquema supuesto, que es lo que rompe:
     `consec` VARCHAR, `telefono` con padding, RFC de 13 vs 10, etc.).

Principios que este script respeta a propósito:

  * NUNCA deduce el esquema: lee `describe`/`PRAGMA table_info` y decide.
  * NUNCA escribe: todas las conexiones son read_only.
  * NO deduplica por nombre de archivo. Los pares `*_master` / `*_v1` de este
    corpus son twins de contenido, pero `santander_v1..v7` NO lo son pese a
    compartir stem. Por eso reporta cada base con su nombre real y solo avisa
    (TWINS_CONOCIDOS) para que el conteo no se lea doble.
  * Normaliza teléfonos/RFC a dígitos antes de comparar, y prueba variantes
    (con y sin LADA, RFC con y sin homoclave).
  * Imprime avance por base: este usuario empuja fuerte contra los barridos
    largos y silenciosos.

Uso:

    # inventario (rápido, metadata) y búsqueda por teléfono
    python3 scripts/barrido_universal.py --tel 5540553398 5613772366

    python3 scripts/barrido_universal.py --curp CAGC610211MDFSTR04 --json out.json
    python3 scripts/barrido_universal.py --rfc CAGC610211000
    python3 scripts/barrido_universal.py --nombre "CASTRO GUTIERREZ"
    python3 scripts/barrido_universal.py --placa ABC1234 --num-servicio 98100607204
    python3 scripts/barrido_universal.py --nss 12345678901
    python3 scripts/barrido_universal.py --reusar-inventario        # no recuenta filas

    # acotar
    --solo santander,telcel      solo esas bases (substring del nombre)
    --excluir covid,empleadores  salta esas bases
    --limite 50                  filas a conservar por hit (default 50)
    --timeout 120                segundos por consulta (default 120)
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

try:
    import duckdb
except ImportError:
    sys.exit("falta duckdb: /mnt/disco2/projects/kyc/.venv/bin/python scripts/barrido_universal.py ...")

BASES = Path("/mnt/disco2/projects/kyc/proyecto_kyc/bases")
CACHE_DEFAULT = Path("/mnt/disco2/projects/kyc/proyecto_kyc/scripts/inventario_bases.json")

# Bases de infraestructura del propio sistema: no son corpus de personas.
EXCLUIR_FIJAS = {
    "auth.db", "oraculo_audit.db", "oraculo_mem_1.db", "singula_cache.db",
}

# Twins de contenido verificados (mismo n de filas y mismas tablas, 2026-10-08).
# Se reportan igual, pero el resumen lo advierte para no leer el conteo doble.
TWINS_CONOCIDOS = [
    ("att_v1.duckdb", "att.duckdb"),
    ("repuve_v1.duckdb", "repuve.duckdb"),
    ("empleadores_v1.duckdb", "empleadores.duckdb"),
    ("telcel_v2.duckdb", "telcel1_master.duckdb"),
    ("telcel_v3.duckdb", "telcel_master_v2.duckdb"),
    ("telcel_v4.duckdb", "telcel_mexico_master.duckdb"),
    ("covid_v1.duckdb", "covid23_master.duckdb"),
    ("banorte_v1.duckdb", "banorte_master.duckdb"),
    ("hsbc_v1.duckdb", "hsbc_master.duckdb"),
    ("citibanamex_v1.duckdb", "citibanamex_master.duckdb"),
    ("bancomer_v1.duckdb", "bancomer_master.duckdb"),
    ("bancoppel_v1.duckdb", "bancoppel_master.duckdb"),
    ("amex_v1.duckdb", "amex_master.duckdb"),
    ("clavijero_v1.duckdb", "clavijero_master.duckdb"),
    ("docentes_v1.duckdb", "docentes_master.duckdb"),
    ("hospital_angeles_v1.duckdb", "hospital_angeles_master.duckdb"),
]

# ── clasificación de columnas ────────────────────────────────────────────────
# Ojo: en las bases bancarias `numero`/`interior`/`num_ext` son del DOMICILIO,
# no teléfonos. `numero_servicio` es de CFE. Se excluyen explícitamente.
NO_TELEFONO = re.compile(
    r"^(numero|num|interior|exterior|ext|int|numero_empleados|num_empleados|"
    r"numero_medidor|numero_servicio|num_servicio|id|id_registro|cuenta)$", re.I)

RE_TEL = re.compile(r"(tel|celular|m[oó]vil|phone|whats)", re.I)
RE_RFC = re.compile(r"^rfc", re.I)
RE_CURP = re.compile(r"curp", re.I)
RE_NSS = re.compile(r"(^nss|seguridad_social)", re.I)
RE_NSERV = re.compile(r"(numero_servicio|num_servicio|^servicio$|no_servicio)", re.I)
RE_PLACA = re.compile(r"placa", re.I)
RE_NOMBRE_P = re.compile(r"^(nombre|paterno|materno|nombres|pat|may|apellido|"
                         r"nom_prop|nombre1|nombre2|nom)$", re.I)
# Columnas que suenan a nombre pero no lo son (empresa, vialidad, patrón).
NO_NOMBRE = re.compile(r"(razon|raz[oó]n_social|empresa|patron|comercial|vialidad|"
                       r"calle|colonia|ciudad|estado|municipio|contacto\.)", re.I)
RE_CP = re.compile(r"^(cp|codigo_postal|cp5)$", re.I)
RE_DIR = re.compile(r"(calle|colonia|domicilio|direccion|direcci[oó]n)", re.I)


def es_telefono(col: str) -> bool:
    return bool(RE_TEL.search(col)) and not NO_TELEFONO.match(col)


def es_nombre(col: str) -> bool:
    return bool(RE_NOMBRE_P.match(col)) and not NO_NOMBRE.search(col)


# ── inventario ──────────────────────────────────────────────────────────────

def _es_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(16).startswith(b"SQLite format 3")
    except OSError:
        return False


def inventariar(archivos, limite_tablas=None, contar=True, verbose=True):
    """{archivo: {motor, tamano_mb, tablas: [{tabla, filas, columnas:[...]}]}}"""
    inv = {}
    for path in archivos:
        n = path.name
        if n in EXCLUIR_FIJAS:
            continue
        t0 = time.time()
        entrada = {"motor": "sqlite" if _es_sqlite(path) else "duckdb",
                   "tamano_mb": round(path.stat().st_size / 1e6, 1),
                   "tablas": [], "error": None}
        try:
            if entrada["motor"] == "sqlite":
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                tabs = [r[0] for r in con.execute(
                    "select name from sqlite_master where type='table'").fetchall()]
                for t in tabs:
                    cols = [r[1] for r in con.execute(f'pragma table_info("{t}")').fetchall()]
                    nf = -1
                    if contar:
                        try:
                            nf = con.execute(f'select count(*) from "{t}"').fetchone()[0]
                        except Exception:
                            pass
                    entrada["tablas"].append({"tabla": t, "filas": nf, "columnas": cols})
                con.close()
            else:
                con = duckdb.connect(str(path), read_only=True)
                # duckdb_tables() ve los esquemas; show tables NO (caso ine_2018).
                tabs = con.execute(
                    "select schema_name, table_name from duckdb_tables() "
                    "order by schema_name, table_name").fetchall()
                if not tabs:  # p.ej. archivo huérfano sin tablas
                    entrada["error"] = "sin tablas en duckdb_tables()"
                for esq, t in tabs:
                    try:
                        cols = [r[0] for r in con.execute(f'describe "{esq}"."{t}"').fetchall()]
                    except Exception as e:
                        cols = [f"ERR:{str(e)[:40]}"]
                    nf = -1
                    if contar:
                        try:
                            nf = con.execute(f'select count(*) from "{esq}"."{t}"').fetchone()[0]
                        except Exception as e:
                            nf = f"ERR:{str(e)[:30]}"
                    entrada["tablas"].append(
                        {"esquema": esq, "tabla": t, "filas": nf, "columnas": cols})
                    if limite_tablas and len(entrada["tablas"]) >= limite_tablas:
                        break
                con.close()
        except Exception as e:
            entrada["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        inv[n] = entrada
        if verbose:
            nt = len(entrada["tablas"])
            filas = sum(t["filas"] for t in entrada["tablas"] if isinstance(t["filas"], int))
            flag = f"  [{entrada['error']}]" if entrada["error"] else ""
            print(f"  {n:34s} {nt:>3} tablas {filas:>13,} filas "
                  f"{time.time()-t0:5.1f}s{flag}", flush=True)
    return inv


def cargar_inventario(cache: Path, archivos, contar, verbose=True):
    if cache.exists():
        inv = json.loads(cache.read_text())
        faltan = [p for p in archivos if p.name not in inv]
        if not faltan and verbose:
            print(f"[*] inventario desde caché: {cache} ({len(inv)} bases)", flush=True)
            return inv
        if faltan and verbose:
            print(f"[*] inventario parcial en caché; faltan {len(faltan)} bases", flush=True)
        inv.update(inventariar(faltan, contar=contar, verbose=verbose))
        cache.write_text(json.dumps(inv, ensure_ascii=False, indent=1))
        return inv
    inv = inventariar(archivos, contar=contar, verbose=verbose)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(inv, ensure_ascii=False, indent=1))
    return inv


# ── construcción de predicados por identificador ─────────────────────────────

def solo_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def variantes_tel(tel: str):
    d = solo_digitos(tel)
    out = {d}
    for n in (10, 12, 13):        # 10 dígitos nacionales, +52 con/sin 1
        if len(d) >= n:
            out.add(d[-n:])
    return sorted(x for x in out if len(x) >= 7)


def predicados(consulta: dict, tabla: dict):
    """Devuelve (conds, params) para una tabla, según las columnas que existan.

    consulta: {"tipo": "tel|rfc|curp|nss|num_servicio|placa|nombre|cp|dir", "valor": ...}
    """
    cols = tabla["columnas"]
    conds, params = [], []
    tipo, valor = consulta["tipo"], consulta["valor"]

    def add(col, expr, vals):
        conds.append(expr.format(c=f'"{col}"'))
        params.extend(vals)

    for col in cols:
        cl = col.lower()
        if tipo == "tel" and es_telefono(col):
            vs = variantes_tel(valor)
            add(col, "regexp_replace(cast({c} as varchar),'[^0-9]','','g') "
                     "in (" + ",".join("?" * len(vs)) + ")", vs)
        elif tipo == "rfc" and RE_RFC.search(col):
            v = str(valor).upper().strip()
            # RFC de 10 caracteres (PF sin homoclave) → prefijo; 13 → exacto.
            if len(v) <= 10:
                add(col, "upper(trim(cast({c} as varchar))) like ?", [v + "%"])
            else:
                add(col, "upper(trim(cast({c} as varchar))) = ?", [v])
        elif tipo == "curp" and RE_CURP.search(col):
            add(col, "upper(trim(cast({c} as varchar))) = ?", [str(valor).upper().strip()])
        elif tipo == "nss" and RE_NSS.search(col):
            add(col, "regexp_replace(cast({c} as varchar),'[^0-9]','','g') = ?",
                [solo_digitos(valor)])
        elif tipo == "num_servicio" and RE_NSERV.search(col):
            add(col, "regexp_replace(cast({c} as varchar),'[^0-9]','','g') = ?",
                [solo_digitos(valor)])
        elif tipo == "placa" and RE_PLACA.search(col):
            add(col, "upper(trim(cast({c} as varchar))) = ?", [str(valor).upper().strip()])
        elif tipo == "cp" and RE_CP.search(col):
            add(col, "lpad(regexp_replace(cast({c} as varchar),'[^0-9]','','g'),5,'0') = ?",
                [solo_digitos(valor).zfill(5)])
        elif tipo == "dir" and RE_DIR.search(col):
            for tok in re.findall(r"[A-ZÑ0-9]{3,}", str(valor).upper()):
                add(col, "upper(cast({c} as varchar)) like ?", ["%" + tok + "%"])
        elif tipo == "nombre" and es_nombre(col):
            # match por palabra completa: evita BUCIO dentro de "distribuciones"
            for tok in re.findall(r"[A-ZÑ]{3,}", str(valor).upper()):
                add(col, f"regexp_matches(upper(cast({{c}} as varchar)), "
                         f"'(^|[^A-Z]){tok}')", [])
    return conds, params


VALORES_IGNORADOS = (None, "", "0", "0000000000000000", "None", "nan")


def limpiar_fila(cols, fila):
    d = {}
    for k, v in zip(cols, fila):
        s = None if v is None else str(v)
        if s is not None and s.strip() in ("",):
            s = None
        d[k] = s
    return {k: v for k, v in d.items() if v not in VALORES_IGNORADOS}


# ── búsqueda ─────────────────────────────────────────────────────────────────

def buscar(consultas, inv, bases_dir: Path, limite=50, timeout=120,
           verbose=True, solo=None, excluir=None):
    hits = []
    for n, meta in inv.items():
        if solo and not any(s in n for s in solo):
            continue
        if excluir and any(s in n for s in excluir):
            continue
        if meta.get("error"):
            continue
        p = bases_dir / n
        if not p.exists():
            continue
        motor = meta["motor"]
        try:
            con = (sqlite3.connect(f"file:{p}?mode=ro", uri=True) if motor == "sqlite"
                   else duckdb.connect(str(p), read_only=True))
        except Exception as e:
            print(f"  [ERR abrir] {n}: {type(e).__name__} {str(e)[:70]}", flush=True)
            continue
        try:
            con.execute(f"set statement_timeout='{timeout}s'") if motor == "duckdb" else None
        except Exception:
            pass
        t_base = time.time()
        for tabla in meta["tablas"]:
            esq = tabla.get("esquema")
            ref = f'"{esq}"."{tabla["tabla"]}"' if esq else f'"{tabla["tabla"]}"'
            cols = tabla["columnas"]
            for q in consultas:
                conds, params = predicados(q, tabla)
                if not conds:
                    continue
                sql = f'select * from {ref} where ' + " or ".join(conds)
                t0 = time.time()
                try:
                    cur = con.execute(sql, params)
                    filas = cur.fetchall()
                    nombres = [d[0] for d in cur.description]
                except Exception as e:
                    print(f"  [ERR] {n} {tabla['tabla']} {q['tipo']}={q['valor']}: "
                          f"{type(e).__name__} {str(e)[:80]}", flush=True)
                    continue
                el = round(time.time() - t0, 2)
                if not filas:
                    continue
                print(f"  [HIT] {n:32s} {tabla['tabla']:18s} {q['tipo']}={q['valor']:<18s} "
                      f"n={len(filas):<5} {el}s", flush=True)
                hits.append({
                    "base": n, "esquema": esq, "tabla": tabla["tabla"],
                    "identificador": q, "n": len(filas), "segundos": el,
                    "columnas": nombres,
                    "filas": [[None if v is None else str(v) for v in f]
                              for f in filas[:limite]],
                })
                if verbose:
                    for f in filas[:4]:
                        print("        ", limpiar_fila(nombres, f), flush=True)
        con.close()
        if verbose:
            print(f"  -- {n} recorrida en {time.time()-t_base:.1f}s", flush=True)
    return hits


def resumen(consultas, hits, inv):
    print("\n" + "=" * 78)
    print("RESUMEN DEL BARRIDO")
    print("=" * 78)
    por_id = {}
    for h in hits:
        k = f"{h['identificador']['tipo']}={h['identificador']['valor']}"
        por_id.setdefault(k, []).append(h)
    for q in consultas:
        k = f"{q['tipo']}={q['valor']}"
        hs = por_id.get(k, [])
        total = sum(h["n"] for h in hs)
        print(f"\n{k}: {len(hs)} tabla(s) con coincidencia, {total} fila(s)")
        for h in sorted(hs, key=lambda x: -x["n"]):
            print(f"   {h['base']:32s} {h['tabla']:18s} n={h['n']}")
        if not hs:
            print("   (sin coincidencias)")
    print("\nTWINS de contenido (mismo corpus; no sumar dos veces):")
    for a, b in TWINS_CONOCIDOS:
        if a in inv or b in inv:
            print(f"   {a}  ==  {b}")
    print("\nBases excluidas / con problemas:")
    for n, meta in inv.items():
        if meta.get("error"):
            print(f"   {n}: {meta['error']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tel", nargs="+")
    ap.add_argument("--rfc", nargs="+")
    ap.add_argument("--curp", nargs="+")
    ap.add_argument("--nss", nargs="+")
    ap.add_argument("--num-servicio", dest="num_servicio", nargs="+")
    ap.add_argument("--placa", nargs="+")
    ap.add_argument("--cp", nargs="+")
    ap.add_argument("--dir", nargs="+")
    ap.add_argument("--nombre", nargs="+")
    ap.add_argument("--solo", help="substrings separados por coma")
    ap.add_argument("--excluir", help="substrings separados por coma")
    ap.add_argument("--limite", type=int, default=50)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--json")
    ap.add_argument("--cache", default=str(CACHE_DEFAULT))
    ap.add_argument("--reusar-inventario", action="store_true",
                    help="usa el JSON de inventario sin recontar filas")
    ap.add_argument("--solo-inventario", action="store_true")
    args = ap.parse_args()

    consultas = []
    for tipo in ("tel", "rfc", "curp", "nss", "num_servicio", "placa", "cp", "dir", "nombre"):
        for v in (getattr(args, tipo) or []):
            consultas.append({"tipo": tipo, "valor": v})
    if not consultas and not args.solo_inventario:
        ap.error("indica al menos un identificador (--tel/--rfc/--curp/--nss/...)")

    archivos = [Path(p) for p in
                sorted(glob.glob(str(BASES / "*.duckdb")) + glob.glob(str(BASES / "*.db")))]
    print(f"[*] corpus: {len(archivos)} archivos en {BASES}", flush=True)
    cache = Path(args.cache)
    inv = cargar_inventario(cache, archivos, contar=not args.reusar_inventario)
    print(f"[*] inventario: {sum(len(m['tablas']) for m in inv.values())} tablas en "
          f"{len(inv)} bases", flush=True)
    if args.solo_inventario:
        return

    t0 = time.time()
    print(f"\n[*] barriendo {len(consultas)} identificador(es)", flush=True)
    hits = buscar(consultas, inv, BASES, limite=args.limite, timeout=args.timeout,
                  solo=args.solo.split(",") if args.solo else None,
                  excluir=args.excluir.split(",") if args.excluir else None)
    print(f"\n[*] barrido terminado en {time.time()-t0:.1f}s", flush=True)
    resumen(consultas, hits, inv)

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"consultas": consultas, "hits": hits, "inventario": inv},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n-> {args.json}")


if __name__ == "__main__":
    main()
