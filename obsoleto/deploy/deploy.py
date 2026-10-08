#!/usr/bin/env python3
"""
deploy.py — Prepara y despliega el backend KYC + frontend Cloudflare Pages.

Modos:
  python deploy.py --server-only     → inicia cloudflared tunnel apuntando a 127.0.0.1:8765
  python deploy.py --pages-only    → deploya solo el frontend a Cloudflare Pages
  python deploy.py --full          → arranca backend + tunnel + deploy Pages
  python deploy.py --status        → muestra estado de servicios

Requiere:
  - cloudflared instalado
  - wrangler@3 (npm/npx)
  - venv en /root/ine_server/.venv (externo al proyecto)
  - .env con API keys
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
BUILD_DIR = ROOT / "cuartodepazsearch-build"
PUBLIC_DIR = BUILD_DIR / "public"
# venv externo (no en el repo). Ver AGENTS.md §1.
VENV_PYTHON = Path(os.environ.get("KYC_VENV", "/root/ine_server/.venv/bin/python"))
SERVER_SCRIPT = ROOT / "servir.py"
PORT = 8765
TUNNEL_PID_FILE = BUILD_DIR / ".tunnel.pid"
SERVER_PID_FILE = BUILD_DIR / ".server.pid"


def _run(cmd, cwd=None, background=False, capture=True):
    print(f"$ {' '.join(str(c) for c in cmd)}")
    if background:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return p
    kwargs = {"cwd": cwd}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    return subprocess.run(cmd, **kwargs)


def sync_public():
    """Copia buscar.html y sujeto.html a public/."""
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "buscar.html", PUBLIC_DIR / "index.html")
    shutil.copy(ROOT / "sujeto.html", PUBLIC_DIR / "sujeto.html")
    print("✓ Frontend sincronizado en public/")


def start_server():
    """Arranca el backend Python en background."""
    if SERVER_PID_FILE.exists():
        old = SERVER_PID_FILE.read_text().strip()
        try:
            os.kill(int(old), 0)
            print(f"  Server ya activo (PID {old})")
            return
        except (OSError, ValueError):
            pass
    p = _run([str(VENV_PYTHON), str(SERVER_SCRIPT), "--port", str(PORT)],
             cwd=ROOT, background=True)
    SERVER_PID_FILE.write_text(str(p.pid))
    print(f"✓ Backend arrancado PID {p.pid} en http://127.0.0.1:{PORT}")
    # esperar a que responda health
    for i in range(15):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1)
            print("  Health check OK")
            return
        except Exception:
            time.sleep(0.5)
    print("  ⚠ Backend no responde aún")


def stop_server():
    """Detiene el backend."""
    if not SERVER_PID_FILE.exists():
        print("  No hay server PID registrado")
        return
    pid = SERVER_PID_FILE.read_text().strip()
    try:
        os.kill(int(pid), 15)
        print(f"✓ Server PID {pid} detenido")
    except Exception as e:
        print(f"  Error deteniendo server: {e}")
    SERVER_PID_FILE.unlink(missing_ok=True)


def start_tunnel():
    """Arranca cloudflared quick tunnel."""
    if TUNNEL_PID_FILE.exists():
        old = TUNNEL_PID_FILE.read_text().strip()
        try:
            os.kill(int(old), 0)
            print(f"  Tunnel ya activo (PID {old})")
            return None
        except (OSError, ValueError):
            pass
    log_file = BUILD_DIR / ".tunnel.log"
    # cloudflared quick tunnel
    p = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{PORT}"],
        cwd=BUILD_DIR,
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
    )
    TUNNEL_PID_FILE.write_text(str(p.pid))
    print(f"✓ Tunnel arrancado PID {p.pid}; log: {log_file}")
    # leer URL del log
    url = None
    for i in range(30):
        if log_file.exists():
            txt = log_file.read_text()
            for line in txt.splitlines():
                if "trycloudflare.com" in line and "https://" in line:
                    # extraer URL
                    import re
                    m = re.search(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com", line)
                    if m:
                        url = m.group(0)
                        break
            if url:
                break
        time.sleep(1)
    if url:
        print(f"  URL pública: {url}")
        # guardarla para el frontend
        (BUILD_DIR / ".tunnel.url").write_text(url)
    else:
        print("  ⚠ No se pudo capturar URL del tunnel aún. Revisa el log.")
    return url


def stop_tunnel():
    if not TUNNEL_PID_FILE.exists():
        print("  No hay tunnel PID registrado")
        return
    pid = TUNNEL_PID_FILE.read_text().strip()
    try:
        os.kill(int(pid), 15)
        print(f"✓ Tunnel PID {pid} detenido")
    except Exception as e:
        print(f"  Error deteniendo tunnel: {e}")
    TUNNEL_PID_FILE.unlink(missing_ok=True)


def deploy_pages():
    """Deploya frontend a Cloudflare Pages."""
    sync_public()
    print("\nDeployando a Cloudflare Pages...")
    r = _run(["npx", "--yes", "wrangler@3", "pages", "deploy", "public",
              "--project-name=cuartodepazsearch"], cwd=BUILD_DIR, capture=True)
    print(r.stdout or "")
    if r.returncode != 0:
        print(r.stderr or "")
        print("❌ Deploy falló. Si no has hecho login:")
        print("   npx --yes wrangler@3 login")
        print("   o exporta CLOUDFLARE_API_TOKEN")
    else:
        print("✓ Deploy Pages exitoso")


def status():
    server_pid = SERVER_PID_FILE.read_text().strip() if SERVER_PID_FILE.exists() else None
    tunnel_pid = TUNNEL_PID_FILE.read_text().strip() if TUNNEL_PID_FILE.exists() else None
    tunnel_url = (BUILD_DIR / ".tunnel.url").read_text().strip() if (BUILD_DIR / ".tunnel.url").exists() else None

    def alive(pid):
        try:
            os.kill(int(pid), 0)
            return "activo"
        except Exception:
            return "muerto"

    print(json.dumps({
        "server": {"pid": server_pid, "estado": alive(server_pid) if server_pid else "no registrado"},
        "tunnel": {"pid": tunnel_pid, "estado": alive(tunnel_pid) if tunnel_pid else "no registrado", "url": tunnel_url},
        "frontend_build": str(PUBLIC_DIR),
        "local_api": f"http://127.0.0.1:{PORT}",
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Deploy script cuartodepazsearch")
    parser.add_argument("--server-only", action="store_true")
    parser.add_argument("--pages-only", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()

    if args.status:
        status()
    elif args.stop:
        stop_tunnel()
        stop_server()
    elif args.server_only:
        sync_public()
        start_server()
        start_tunnel()
        status()
    elif args.pages_only:
        deploy_pages()
    elif args.full:
        sync_public()
        start_server()
        start_tunnel()
        deploy_pages()
        status()
    else:
        print("Uso: python deploy.py --server-only | --pages-only | --full | --status | --stop")
        sys.exit(1)


if __name__ == "__main__":
    main()
