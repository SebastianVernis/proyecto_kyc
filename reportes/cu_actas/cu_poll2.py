#!/usr/bin/env python3
import json, time, base64, os, sys, urllib.request, urllib.error

API_KEY = "24a1a22c0b3fb973c8369b6af1020eb2746503734985177f"
BASE = "https://api.consultaunica.mx/v3/actas"
OUT_DIR = "/home/sebastianvernis/proyectos/kyc/proyecto_kyc/reportes/cu_actas"

def api_post(body):
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), headers={"x-api-key": API_KEY, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")[:500]
        return e.code, body_text

def api_get(path):
    req = urllib.request.Request(f"{BASE}/{path}", headers={"x-api-key": API_KEY}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")[:500]
        return e.code, body_text

# Health check with full body
print("=== HEALTH CHECK ===")
code, body = api_post({"curp":"TEST","actaType":"nacimiento","conFolio":False})
print(f"HTTP {code}: {json.dumps(body) if isinstance(body, dict) else body}")

# If 503 or 429, wait and retry
if code in (503, 429):
    print(f"Service returning {code}. Waiting 60s and retrying...")
    time.sleep(60)
    code, body = api_post({"curp":"TEST","actaType":"nacimiento","conFolio":False})
    print(f"Retry: HTTP {code}: {json.dumps(body) if isinstance(body, dict) else body}")
    
    if code in (503, 429):
        print(f"Still {code}. Waiting another 60s...")
        time.sleep(60)
        code, body = api_post({"curp":"TEST","actaType":"nacimiento","conFolio":False})
        print(f"Retry2: HTTP {code}: {json.dumps(body) if isinstance(body, dict) else body}")

if code == 503:
    print("API DOWN. Aborting.")
    sys.exit(0)

# Queries - submit with delays
queries = [
    ("JIGT550413MJCMDR11", "matrimonio", "MARIA_TERESA_matrimonio"),
    ("JIGS660310MJCMDS06", "matrimonio", "SUSANA_matrimonio"),
    ("JIGA741005MJCMDD07", "matrimonio", "ADRIANA_GUADALUPE_matrimonio"),
    ("JIGF880830MJCMDR00", "matrimonio", "MARIA_FERNANDA_matrimonio"),
    ("JIGJ881021MJCMDS04", "matrimonio", "MARIA_JESUS_matrimonio"),
    ("JIGJ911002MJCMDS02", "matrimonio", "JESSICA_DENIS_matrimonio"),
    ("JIGA931028MJCMDN05", "matrimonio", "ANA_KAREN_matrimonio"),
    ("JIGV990111MJCMDN19", "matrimonio", "VIANEY_GUADALUPE_matrimonio"),
    ("JIGK000221MJCMDRA8", "matrimonio", "KAREN_LIZBETH_matrimonio"),
    ("GAGJ710907HTSRNN07", "nacimiento", "JUAN_ARTURO_nacimiento"),
    ("GAGJ710907HTSRNN07", "matrimonio", "JUAN_ARTURO_matrimonio"),
]

jobs = []
for curp, acta_type, label in queries:
    code, body = api_post({"curp": curp, "actaType": acta_type, "conFolio": False})
    if code == 429:
        print(f"  {label}: 429 rate limited. Waiting 15s...")
        time.sleep(15)
        code, body = api_post({"curp": curp, "actaType": acta_type, "conFolio": False})
    if code == 429:
        print(f"  {label}: still 429. Waiting 30s...")
        time.sleep(30)
        code, body = api_post({"curp": curp, "actaType": acta_type, "conFolio": False})
    
    if isinstance(body, dict):
        d = body.get("data", body)
        uuid = d.get("uuid")
        if code == 200 and uuid:
            print(f"  {label}: OK -> {uuid}")
            jobs.append((label, uuid, "SUBMITTED"))
        elif code == 409:
            # Conflict - maybe already exists, check for uuid
            uuid = d.get("uuid")
            if uuid:
                print(f"  {label}: 409 but has uuid -> {uuid}")
                jobs.append((label, uuid, "SUBMITTED"))
            else:
                print(f"  {label}: 409 no uuid: {json.dumps(d)[:200]}")
                jobs.append((label, None, f"409: {json.dumps(d)[:100]}"))
        else:
            print(f"  {label}: HTTP {code}: {json.dumps(d)[:200]}")
            jobs.append((label, None, f"HTTP_{code}"))
    else:
        print(f"  {label}: HTTP {code}: {body[:200]}")
        jobs.append((label, None, f"HTTP_{code}"))
    
    time.sleep(2)  # rate limit spacing

# Poll
print("\n--- Polling ---")
for i, (label, uuid, st) in enumerate(jobs):
    if st != "SUBMITTED":
        continue
    deadline = time.time() + 120
    while time.time() < deadline:
        code, data = api_get(uuid)
        if isinstance(data, dict):
            rec = data.get("data", data)
            s = rec.get("status", "")
            if s == "completed":
                pdf = rec.get("pdfBase64") or rec.get("pdf_base64") or rec.get("pdf")
                if pdf:
                    path = os.path.join(OUT_DIR, f"{label}.pdf")
                    with open(path, "wb") as f:
                        f.write(base64.b64decode(pdf))
                    jobs[i] = (label, uuid, f"OK -> {path}")
                    print(f"  {label}: OK -> {path}")
                else:
                    # Try all keys
                    print(f"  {label}: completed, keys={list(rec.keys())[:15]}")
                    jobs[i] = (label, uuid, "NO_PDF")
                break
            elif s == "failed":
                err = rec.get("error") or rec.get("message") or json.dumps(rec)[:300]
                jobs[i] = (label, uuid, f"FAILED: {err}")
                print(f"  {label}: FAILED - {err}")
                break
            elif s == "processing" or s == "pending":
                pass  # keep polling
        time.sleep(8)
    else:
        jobs[i] = (label, uuid, "TIMEOUT")
        print(f"  {label}: TIMEOUT 120s")

print("\n=== FINAL SUMMARY ===")
for label, uuid, st in jobs:
    print(f"  {label}: {st}")
