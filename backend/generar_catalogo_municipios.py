"""
generar_catalogo_municipios.py — Catálogo código INE (e, m) -> nombre de municipio
(pendiente #2 del handoff).

No existe catálogo público directo de claves INE de municipio (no coinciden con
las INEGI), así que se deriva del propio padrón: cada fila trae (e, m, cp) y
SEPOMEX da cp -> municipio. El nombre dominante por (e, m) con share >= 70% y
soporte >= 20 filas se acepta como el nombre del municipio.

Salida: bases/catalogo_municipios_ine.json  {"<e>|<m>": "NOMBRE", ...}

Uso:
    python generar_catalogo_municipios.py
"""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path
import duckdb

_BASES_DIR = Path(__file__).resolve().parent.parent / "bases"
PADRON_DB = str(_BASES_DIR / "padron_v1.duckdb")
SEPOMEX_DB = str(_BASES_DIR / "sepomex.db")
SALIDA = _BASES_DIR / "catalogo_municipios_ine.json"

MIN_SHARE = 0.70
MIN_SOPORTE = 20


def main():
    t0 = time.time()
    # cp -> municipio dominante según SEPOMEX (un cp casi nunca cruza municipio)
    sq = sqlite3.connect(SEPOMEX_DB)
    cp_mun = sq.execute("""
        SELECT cp, municipio FROM (
            SELECT cp, municipio, count(*) n,
                   row_number() OVER (PARTITION BY cp ORDER BY count(*) DESC) rk
            FROM cp GROUP BY cp, municipio
        ) WHERE rk = 1
    """).fetchall()
    sq.close()

    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='8GB'; SET threads=6; SET preserve_insertion_order=false;")
    con.execute(f"ATTACH '{PADRON_DB}' AS p (READ_ONLY)")
    con.execute("CREATE TEMP TABLE cp_mun (cp VARCHAR, mun VARCHAR)")
    con.executemany("INSERT INTO cp_mun VALUES (?, ?)", cp_mun)
    print(f"[1/2] sepomex: {len(cp_mun):,} cps con municipio", flush=True)

    # (e, m) -> municipio dominante vía el cp de cada fila del padrón
    rows = con.execute(f"""
        WITH em_cp AS (
            SELECT try_cast(e AS INT) AS e, try_cast(m AS INT) AS m,
                   lpad(trim(cp), 5, '0') AS cp, count(*) AS n
            FROM p.padron
            WHERE e IS NOT NULL AND m IS NOT NULL
              AND cp IS NOT NULL AND trim(cp) <> ''
            GROUP BY 1, 2, 3
        ),
        em_mun AS (
            SELECT e, m, cm.mun, sum(n) AS n
            FROM em_cp JOIN cp_mun cm USING (cp)
            WHERE e IS NOT NULL AND m IS NOT NULL
            GROUP BY 1, 2, 3
        )
        SELECT e, m, arg_max(mun, n) AS mun_top, max(n) AS n_top, sum(n) AS n_tot,
               max(n)::DOUBLE / sum(n) AS share
        FROM em_mun GROUP BY e, m
    """).fetchall()

    catalogo, dudosos = {}, 0
    for e, m, mun, n_top, n_tot, share in rows:
        if share >= MIN_SHARE and n_top >= MIN_SOPORTE:
            catalogo[f"{e}|{m}"] = mun.strip().upper()
        else:
            dudosos += 1
    SALIDA.write_text(json.dumps(catalogo, ensure_ascii=False, indent=0,
                                 sort_keys=True), encoding="utf-8")
    print(f"[2/2] pares (e,m) vistos: {len(rows):,} | resueltos: {len(catalogo):,} "
          f"| dudosos: {dudosos:,} | {time.time()-t0:,.0f}s -> {SALIDA}")


if __name__ == "__main__":
    main()
