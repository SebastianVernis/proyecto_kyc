#!/usr/bin/env python3
import json, time, base64, os, sys, urllib.request, urllib.error

API_KEY = "24a1a22c0b3fb973c8369b6af1020eb2746503734985177f"
BASE = "https://api.consultaunica.mx/v3/actas"
OUT_DIR = "/mnt/disco2/projects/kyc/proyecto_kyc/reportes/cu_actas"

def api_post(body):
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), headers={"x-api-key": API_KEY, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read())

def api_get(path):
    req = urllib.request.Request(f"{BASE}/{path}", headers={"x-api-key": API_KEY}, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

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

# Health check
try:
    status, body = api_post({"curp":"TEST","actaType":"nacimiento","conFolio":False})
    print(f"Health check: HTTP {status}, body={json.dumps(body)[:100]}")
except urllib.error.HTTPError as e:
    print(f"Health check: HTTP {e.code}")
    if e.code == 503:
        print("API DOWN (503). Aborting.")
        sys.exit(0)
except Exception as e:
    print(f"Health check ERROR: {e}")
    sys.exit(1)

# Submit all
jobs = []
for curp, acta_type, label in queries:
    try:
        status, body = api_post({"curp": curp, "actaType": acta_type, "conFolio": False})
        d = body.get("data", body)
        uuid = d.get("uuid")
        if not uuid:
            print(f"  {label}: no uuid in response (HTTP {status}): {json.dumps(body)[:200]}")
            jobs.append((label, None, "NO_UUID"))
            continue
        print(f"  {label}: submitted -> {uuid}")
        jobs.append((label, uuid, "SUBMITTED"))
    except urllib.error.HTTPError as e:
        print(f"  {label}: HTTP {e.code}")
        jobs.append((label, None, f"HTTP_{e.code}"))
    except Exception as e:
        print(f"  {label}: ERROR {e}")
        jobs.append((label, None, "ERROR"))

# Poll
print("\n--- Polling ---")
for i, (label, uuid, st) in enumerate(jobs):
    if st != "SUBMITTED":
        continue
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            data = api_get(uuid)
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
                    jobs[i] = (label, uuid, "NO_PDF")
                    print(f"  {label}: completed, no pdf field")
                break
            elif s == "failed":
                err = rec.get("error") or rec.get("message") or json.dumps(rec)[:200]
                jobs[i] = (label, uuid, f"FAILED: {err}")
                print(f"  {label}: FAILED - {err}")
                break
            time.sleep(5)
        except Exception as e:
            jobs[i] = (label, uuid, f"POLL_ERR: {e}")
            print(f"  {label}: poll error {e}")
            break
    else:
        jobs[i] = (label, uuid, "TIMEOUT")
        print(f"  {label}: TIMEOUT 120s")

print("\n=== FINAL SUMMARY ===")
for label, uuid, st in jobs:
    print(f"  {label}: {st}")
