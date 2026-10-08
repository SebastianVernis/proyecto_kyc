#!/usr/bin/env python3
"""Inventario: tablas + columnas tipo telefono por base. Metadata only, rapido."""
import duckdb, os, json, re, glob

B = "/mnt/disco2/projects/kyc/proyecto_kyc/bases/"
TEL_PAT = re.compile(r"(tel|phone|celular|movil|whats)", re.I)
DOC_PAT = re.compile(r"(tel|phone|celular|movil|whats|rfc|curp|nss|nombre|apellid|paterno|materno|domicilio|calle|colonia|cp|nss|cuenta|correo|mail|email)", re.I)

out = {}
files = sorted(glob.glob(B + "*.duckdb") + glob.glob(B + "*.db"))
for f in files:
    n = os.path.basename(f)
    if n in ("auth.db", "oraculo_audit.db", "oraculo_mem_1.db", "singula_cache.db"):
        continue
    try:
        c = duckdb.connect(f, read_only=True)
        tabs = [r[0] for r in c.execute("show tables").fetchall()]
        info = []
        for t in tabs:
            try:
                cols = [r[0] for r in c.execute(f'describe "{t}"').fetchall()]
            except Exception as e:
                cols = [f"ERR:{e}"]
            try:
                cnt = c.execute(f'select count(*) from "{t}"').fetchone()[0]
            except Exception as e:
                cnt = -1
            info.append({"tabla": t, "filas": cnt,
                         "telcols": [x for x in cols if TEL_PAT.search(x)],
                         "doccols": [x for x in cols if DOC_PAT.search(x)]})
        c.close()
        out[n] = info
    except Exception as e:
        out[n] = [{"error": f"{type(e).__name__}: {e}"}]

json.dump(out, open("/mnt/disco2/projects/kyc/proyecto_kyc/scripts/inv_tel.json", "w"), ensure_ascii=False, indent=1)
for k, v in out.items():
    for t in v:
        if "error" in t:
            print(f"{k}: ERROR {t['error']}")
        else:
            print(f"{k:32s} {t['tabla']:22s} filas={t['filas']:>12} tel={t['telcols']}")
