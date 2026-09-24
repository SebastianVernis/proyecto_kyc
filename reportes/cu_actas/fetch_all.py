#!/usr/bin/env python3
"""Fetch CU Actas with proper rate limiting (2s delay between requests)."""
import json, time, base64, os, requests

API_KEY = "24a1a22c0b3fb973c8369b6af1020eb2746503734985177f"
OUT_DIR = "/home/sebastianvernis/proyectos/kyc/proyecto_kyc/reportes/cu_actas"
HEADERS_JSON = {"Content-Type": "application/json", "x-api-key": API_KEY}
BASE = "https://api.consultaunica.mx/v3/actas"

queries = [
    ("JIGT550413MJCMDR11", "matrimonio", "Maria_Teresa_Jimenez"),
    ("JIGS660310MJCMDS06", "matrimonio", "Susana_Jimenez"),
    ("JIGA741005MJCMDD07", "matrimonio", "Adriana_Guadalupe_Jimenez"),
    ("JIGF880830MJCMDR00", "matrimonio", "Maria_Fernanda_Jimenez"),
    ("JIGJ881021MJCMDS04", "matrimonio", "Maria_Jesus_Jimenez"),
    ("JIGJ911002MJCMDS02", "matrimonio", "Jessica_Denis_Jimenez"),
    ("JIGA931028MJCMDN05", "matrimonio", "Ana_Karen_Jimenez"),
    ("JIGV990111MJCMDN19", "matrimonio", "Vianey_Guadalupe_Jimenez"),
    ("JIGK000221MJCMDRA8", "matrimonio", "Karen_Lizbeth_Jimenez"),
    ("GAGJ710907HTSRNN07", "nacimiento", "Juan_Arturo_Garza_Gonzalez_NAC"),
    ("GAGJ710907HTSRNN07", "matrimonio", "Juan_Arturo_Garza_Gonzalez_MAT"),
]

def post_query(curp, acta_type, label):
    """POST with retry on 429/503, backoff up to 30s."""
    for attempt in range(5):
        body = {"curp": curp, "actaType": acta_type, "conFolio": False}
        r = requests.post(BASE, headers=HEADERS_JSON, json=body, timeout=15)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 429:
            wait = min(5 * (attempt + 1), 30)
            print(f"  429 on {label}, waiting {wait}s...")
            time.sleep(wait)
        elif r.status_code == 503:
            wait = min(5 * (attempt + 1), 30)
            print(f"  503 on {label}, waiting {wait}s...")
            time.sleep(wait)
        else:
            print(f"✗ {label}: HTTP {r.status_code} - {r.text[:200]}")
            return None
    print(f"✗ {label}: gave up after 5 attempts")
    return None

# Phase 1: Submit all
pending = []
for curp, acta_type, label in queries:
    time.sleep(3)  # rate limit
    data = post_query(curp, acta_type, label)
    if data:
        uuid = data.get("uuid") or data.get("data", {}).get("uuid")
        status = data.get("status") or data.get("data", {}).get("status")
        print(f"✓ {label}: uuid={uuid} status={status}")
        if status == "completed":
            pdf_b64 = data.get("pdfBase64") or data.get("data", {}).get("pdfBase64")
            if pdf_b64:
                path = f"{OUT_DIR}/{label}.pdf"
                with open(path, "wb") as f:
                    f.write(base64.b64decode(pdf_b64))
                print(f"  → Saved {path}")
        else:
            pending.append((uuid, status, label, curp, acta_type))

# Phase 2: Poll pending
if pending:
    print(f"\n--- Polling {len(pending)} pending ---")
    for attempt in range(30):
        time.sleep(30)
        still_pending = False
        for i, (uuid, status, label, curp, acta_type) in enumerate(pending):
            if status in ("completed", "failed"):
                continue
            try:
                r = requests.get(f"{BASE}/{uuid}", headers={"x-api-key": API_KEY}, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    ns = data.get("status") or data.get("data", {}).get("status")
                    if ns == "completed":
                        pdf_b64 = data.get("pdfBase64") or data.get("data", {}).get("pdfBase64")
                        if pdf_b64:
                            path = f"{OUT_DIR}/{label}.pdf"
                            with open(path, "wb") as f:
                                f.write(base64.b64decode(pdf_b64))
                            print(f"✓ {label}: completed → {path}")
                        pending[i] = (uuid, "completed", label, curp, acta_type)
                    elif ns == "failed":
                        err = data.get("error") or data.get("data", {}).get("error") or "unknown"
                        print(f"✗ {label}: FAILED - {err}")
                        pending[i] = (uuid, "failed", label, curp, acta_type)
                    else:
                        still_pending = True
                elif r.status_code == 429:
                    time.sleep(10)
                    still_pending = True
                else:
                    still_pending = True
            except Exception as e:
                still_pending = True
        if not still_pending:
            print("\nAll done!")
            break
    else:
        print("\nTimeout")

# Final summary
print("\n=== FINAL SUMMARY ===")
for label in [q[2] for q in queries]:
    path = f"{OUT_DIR}/{label}.pdf"
    if os.path.exists(path):
        sz = os.path.getsize(path)
        print(f"  ✓ {label} — PDF {sz} bytes")
    else:
        print(f"  ✗ {label} — no PDF")
