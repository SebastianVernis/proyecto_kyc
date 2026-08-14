#!/usr/bin/env python3
"""Descarga dataset SEPOMEX de Postali iterando por estado+municipio (concurrent)."""
import os, sqlite3, time, requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://postali.app/api/v1/mx"
DB_PATH = os.environ.get("SEPOMEX_DB", "/home/sebastianvernis/Descargas MEGA/BASE INE 2021/BASE INE 2018/src/sepomex.db")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "cuartodepaz-sepomex/1.0"})

def list_estados():
    r = SESSION.get(f"{BASE}/estados", timeout=15)
    r.raise_for_status()
    return [e["slug"] for e in r.json()["estados"]]

def list_municipios(estado_slug):
    r = SESSION.get(f"{BASE}/estado/{estado_slug}/municipios", timeout=20)
    r.raise_for_status()
    return [(estado_slug, m["slug"]) for m in r.json()["municipios"]]

def fetch_municipio(estado_slug, municipio_slug):
    r = SESSION.get(f"{BASE}/municipio/{estado_slug}/{municipio_slug}", timeout=30)
    if r.status_code != 200:
        return []
    data = r.json()
    estado_nombre = data.get("estado", "")
    municipio_nombre = data.get("municipio", "")
    rows = []
    for c in data.get("colonias", []):
        rows.append((c["cp"], c["nombre"], c.get("tipo"), municipio_nombre, estado_nombre))
    return rows

def main():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS cp (
            cp TEXT NOT NULL,
            colonia TEXT NOT NULL,
            tipo TEXT,
            municipio TEXT,
            estado TEXT,
            PRIMARY KEY (cp, colonia)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_cp ON cp(cp)")
    con.commit()

    t0 = time.time()
    estados = list_estados()
    print(f"estados: {len(estados)}")

    pairs = []
    for e in estados:
        try:
            pairs.extend(list_municipios(e))
        except Exception as ex:
            print(f"  WARN list_municipios({e}): {ex}")
    print(f"municipios: {len(pairs)}")

    ok = err = 0
    done = 0
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(fetch_municipio, e, m): (e, m) for e, m in pairs}
        for fut in as_completed(futures):
            e, m = futures[fut]
            done += 1
            try:
                rows = fut.result()
                for r in rows:
                    con.execute("INSERT OR IGNORE INTO cp VALUES (?,?,?,?,?)", r)
                ok += len(rows)
            except Exception as ex:
                err += 1
                if err <= 5:
                    print(f"  ERR {e}/{m}: {ex}")
            if done % 100 == 0:
                con.commit()
                elapsed = time.time() - t0
                rate = done / max(elapsed, 1)
                print(f"  [{done}/{len(pairs)}] ok_rows={ok} err={err} elapsed={elapsed:.0f}s rate={rate:.1f}/s", flush=True)
    con.commit()

    total = con.execute("SELECT COUNT(*) FROM cp").fetchone()[0]
    cps = con.execute("SELECT COUNT(DISTINCT cp) FROM cp").fetchone()[0]
    print(f"DONE total_rows={total} distinct_cp={cps} ok_rows={ok} err={err} en {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
