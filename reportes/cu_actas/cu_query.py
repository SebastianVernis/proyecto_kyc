#!/usr/bin/env python3
"""Consulta Única Actas - Batch query script."""
import os, sys, json, time, base64, urllib.request, urllib.error

API_KEY = "24a1a22c0b3fb973c8369b6af1020eb2746503734985177f"
BASE = "https://api.consultaunica.mx/v3/actas"
OUTDIR = "/home/sebastianvernis/proyectos/kyc/proyecto_kyc/reportes/cu_actas"

QUERIES = [
    # Hermanas - matrimonios
    ("JIGT550413MJCMDR11", "matrimonio", "MARIA_Teresa_JIGT550413_matrimonio"),
    ("JIGS660310MJCMDS06", "matrimonio", "SUSANA_JIGS660310_matrimonio"),
    ("JIGA741005MJCMDD07", "matrimonio", "ADRIANA_GUADALUPE_JIGA741005_matrimonio"),
    ("JIGF880830MJCMDR00", "matrimonio", "MARIA_FERNANDA_JIGF880830_matrimonio"),
    ("JIGJ881021MJCMDS04", "matrimonio", "MARIA_JESUS_JIGJ881021_matrimonio"),
    ("JIGJ911002MJCMDS02", "matrimonio", "JESSICA_DENIS_JIGJ911002_matrimonio"),
    ("JIGA931028MJCMDN05", "matrimonio", "ANA_KAREN_JIGA931028_matrimonio"),
    ("JIGV990111MJCMDN19", "matrimonio", "VIANEY_GUADALUPE_JIGV990111_matrimonio"),
    ("JIGK000221MJCMDRA8", "matrimonio", "KAREN_LIZBETH_JIGK000221_matrimonio"),
    # Juan Arturo Garza Gonzalez
    ("GAGJ710907HTSRNN07", "nacimiento", "JUAN_ARTURO_GAGJ710907_nacimiento"),
    ("GAGJ710907HTSRNN07", "matrimonio", "JUAN_ARTURO_GAGJ710907_matrimonio"),
]

def api_post(body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": body}, e.code

def api_get(url):
    req = urllib.request.Request(url)
    req.add_header("x-api-key", API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": body}, e.code

def poll_until_done(uuid, max_wait=300):
    url = f"{BASE}/{uuid}"
    start = time.time()
    while time.time() - start < max_wait:
        resp, code = api_get(url)
        if code != 200:
            return resp, code
        status = resp.get("status", "")
        if status == "completed":
            return resp, 200
        elif status == "failed":
            return resp, 200
        time.sleep(5)
    return {"error": "timeout"}, 408

results = []
for curp, acta_type, label in QUERIES:
    print(f"\n{'='*60}")
    print(f"Querying: {label} (CURP: {curp}, type: {acta_type})")
    resp, code = api_post({"curp": curp, "actaType": acta_type, "conFolio": False})
    
    if code == 503:
        print(f"  503 - Service unavailable. Skipping all remaining.")
        results.append({"label": label, "status": "503_unavailable"})
        break
    elif code == 429:
        print(f"  429 - Rate limited. Waiting 60s...")
        time.sleep(60)
        resp, code = api_post({"curp": curp, "actaType": acta_type, "conFolio": False})
    
    if code not in (200, 201):
        print(f"  Error {code}: {resp}")
        results.append({"label": label, "status": f"error_{code}", "detail": resp})
        continue
    
    uuid = resp.get("uuid") or resp.get("id")
    status = resp.get("status", "")
    
    if status in ("completed",) or (not uuid and status):
        # Already done or inline result
        if resp.get("pdfBase64"):
            pdf_path = os.path.join(OUTDIR, f"{label}.pdf")
            with open(pdf_path, "wb") as f:
                f.write(base64.b64decode(resp["pdfBase64"]))
            print(f"  SAVED PDF: {pdf_path}")
            results.append({"label": label, "status": "saved", "path": pdf_path})
        else:
            print(f"  Completed but no PDF. Response keys: {list(resp.keys())}")
            results.append({"label": label, "status": "completed_no_pdf", "keys": list(resp.keys())})
        continue
    
    if not uuid:
        print(f"  No UUID returned. Response: {resp}")
        results.append({"label": label, "status": "no_uuid", "response": resp})
        continue
    
    print(f"  UUID: {uuid}, status: {status}. Polling...")
    final, fcode = poll_until_done(uuid)
    
    if final.get("status") == "completed" and final.get("pdfBase64"):
        pdf_path = os.path.join(OUTDIR, f"{label}.pdf")
        with open(pdf_path, "wb") as f:
            f.write(base64.b64decode(final["pdfBase64"]))
        print(f"  SAVED PDF: {pdf_path}")
        results.append({"label": label, "status": "saved", "path": pdf_path})
    elif final.get("status") == "failed":
        print(f"  FAILED: {final.get('error', final.get('message', 'unknown'))}")
        results.append({"label": label, "status": "failed", "detail": final})
    else:
        print(f"  Final: status={final.get('status')}, code={fcode}")
        results.append({"label": label, "status": f"poll_{fcode}", "detail": final})

print(f"\n{'='*60}")
print("SUMMARY:")
for r in results:
    print(f"  {r['label']}: {r['status']}")
print(f"\nTotal: {len(results)} queries")
