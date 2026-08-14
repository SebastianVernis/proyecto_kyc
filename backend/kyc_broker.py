#!/usr/bin/env python3
"""kyc_broker.py — orquestador KYC que combina todos los providers.

Une Padrón local + Tlaloc + Singula + Moffin + Kiban + Apify + Buró + Círculo.

Uso:
  from kyc_broker import KYCBroker
  broker = KYCBroker()
  result = broker.full_kyc(
      curp="...",
      nombre="...",
      paterno="...",
      materno="...",
      fecnac="...",
      rfc="...",
      email="...",
  )
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from config import config
from providers import (
    TlalocClient, SingulaClient, MoffinClient, KibanClient,
    ApifyOSINTClient, BuroClient, CirculoCreditoClient,
    ProviderError, normalize_curp, normalize_rfc,
)


class KYCBroker:
    """Orquestador KYC que combina múltiples proveedores."""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.clients: dict[str, Any] = {}
        self._init_clients()

    def _init_clients(self):
        """Inicializa solo los clientes que tienen API key."""
        if config.tlaloc_api_key:
            try:
                self.clients["tlaloc"] = TlalocClient(config.tlaloc_api_key)
                self._log("✓ Tlaloc inicializado")
            except Exception as e:
                self._log(f"✗ Tlaloc: {e}")
        if config.singula_api_key:
            try:
                self.clients["singula"] = SingulaClient(config.singula_api_key)
                self._log("✓ Singula inicializado")
            except Exception as e:
                self._log(f"✗ Singula: {e}")
        if config.moffin_api_key:
            try:
                self.clients["moffin"] = MoffinClient(
                    config.moffin_api_key,
                    base_url=config.moffin_base_url,
                )
                self._log("✓ Moffin inicializado")
            except Exception as e:
                self._log(f"✗ Moffin: {e}")
        if config.kiban_api_key:
            try:
                self.clients["kiban"] = KibanClient(config.kiban_api_key)
                self._log("✓ Kiban inicializado")
            except Exception as e:
                self._log(f"✗ Kiban: {e}")
        if config.apify_token:
            try:
                self.clients["apify"] = ApifyOSINTClient(config.apify_token)
                self._log("✓ Apify inicializado")
            except Exception as e:
                self._log(f"✗ Apify: {e}")
        if config.buro_api_key and config.buro_username:
            try:
                self.clients["buro"] = BuroClient(
                    api_key=config.buro_api_key,
                    username=config.buro_username,
                    password=config.buro_password,
                )
                self._log("✓ Buró inicializado")
            except Exception as e:
                self._log(f"✗ Buró: {e}")
        if config.cdc_api_key and config.cdc_username:
            try:
                self.clients["cdc"] = CirculoCreditoClient(
                    api_key=config.cdc_api_key,
                    username=config.cdc_username,
                    password=config.cdc_password,
                    cert_path=config.cdc_cert_path,
                    cert_password=config.cdc_cert_password,
                )
                self._log("✓ Círculo de Crédito inicializado")
            except Exception as e:
                self._log(f"✗ Círculo: {e}")

    def _log(self, msg: str):
        if self.verbose:
            print(f"[kyc] {msg}", flush=True)

    def health_check(self) -> dict:
        """Verifica qué proveedores responden."""
        results = {}
        for name, client in self.clients.items():
            try:
                results[name] = client.health()
            except Exception as e:
                results[name] = {"ok": False, "error": str(e)}
        return results

    def enrich_fiscal(self, curp: str = None, rfc: str = None,
                      nombre: str = None, paterno: str = None,
                      materno: str = None, only: str = None) -> dict:
        """Fase 1: complementar datos fiscales con Tlaloc + Singula.

        Args:
            only: None (ambos) | "tlaloc" | "singula"

        No re-valida el CURP (eso ya está en padrón), pero trae:
          - Datos RENAPO detallados (Tlaloc): nombres, fecnac, doc probatorio
          - Validación RFC + estatus fiscal (Singula)
        """
        result = {"fase": "fiscal", "datos_fiscales": {}, "datos_identidad": {}}
        if only is None:
            only = "all"

        # 1) Tlaloc: datos RENAPO detallados
        if curp and "tlaloc" in self.clients and only in ("tlaloc", "all"):
            try:
                tlaloc = self.clients["tlaloc"].validate_curp(curp)
                if isinstance(tlaloc, dict) and tlaloc.get("valid"):
                    result["datos_identidad"] = {
                        "fuente": "tlaloc_renapo",
                        "nombres": tlaloc.get("nombres"),
                        "apellido_paterno": tlaloc.get("primerApellido"),
                        "apellido_materno": tlaloc.get("segundoApellido"),
                        "sexo": tlaloc.get("sexo"),
                        "fecha_nacimiento": tlaloc.get("fechaNacimiento"),
                        "nacionalidad": tlaloc.get("nacionalidad"),
                        "entidad": tlaloc.get("entidad"),
                        "clave_entidad": tlaloc.get("claveEntidad"),
                        "doc_probatorio": tlaloc.get("docProbatorio"),
                        "status_curp": tlaloc.get("status"),
                    }
            except Exception as e:
                result["datos_identidad"] = {"error": str(e)}

        # 2) Singula: validación RFC
        # ELIMINADO: CheckID ya valida el RFC ante el SAT; pagar doble no tiene sentido.
        # if rfc and "singula" in self.clients and only in ("singula", "all"):
        #     try:
        #         rfc_resp = self.clients["singula"].validate_rfc(rfc)
        #         if isinstance(rfc_resp, dict):
        #             result["datos_fiscales"]["rfc_singula"] = {
        #                 "valid": rfc_resp.get("valid"),
        #                 "registered": rfc_resp.get("registered"),
        #                 "status": rfc_resp.get("status"),
        #                 "message": rfc_resp.get("message"),
        #             }
        #     except Exception as e:
        #         result["datos_fiscales"]["rfc_singula"] = {"error": str(e)}

        return result

    def enrich_social(self, nombre: str = None, paterno: str = None,
                      materno: str = "", email: str = "",
                      telefono: str = "") -> dict:
        """Fase 2: búsqueda de redes sociales con Apify.

        Usa el nombre + variantes de username + email.
        """
        result = {"fase": "social", "redes": [], "apify_metadata": {}}
        if "apify" not in self.clients:
            result["error"] = "Apify no configurado"
            return result
        try:
            apify_res = self.clients["apify"].full_osint(
                nombre=nombre or "", paterno=paterno or "", materno=materno or "",
                email=email, telefono=telefono, max_results=5,
            )
            sint = apify_res.get("sintesis", {})
            result["apify_metadata"] = {
                "total_perfiles_byname": sint.get("total_perfiles_byname", 0),
                "total_perfiles_byusername": sint.get("total_perfiles_byusername", 0),
            }
            result["redes"] = sint.get("redes_encontradas", [])
            result["by_name"] = apify_res.get("by_name", {})
            result["by_username"] = apify_res.get("by_username", {})
        except Exception as e:
            result["error"] = str(e)
        return result

    def enrich_blacklist(self, nombre: str = "", paterno: str = "",
                        materno: str = "", curp: str = "") -> dict:
        """Fase 3: listas negras (OFAC, PEP, UE, ONU, UK).

        Solo funciona si hay customer_id de Singula o Kiban configurado.
        """
        result = {"fase": "blacklist", "listas": {}, "hit": False}
        # Singula blacklist requiere customer_id
        if "singula" in self.clients and curp:
            try:
                sg = self.clients["singula"]
                # 2026-08-04: get_or_create_customer es idempotente. Reemplaza
                # el patrón find_by_curp directo (que podía devolver None o
                # un customer stale sin id).
                res = sg.get_or_create_customer(curp=curp)
                cid = res.get("id")
                if cid:
                    # Validar el customer antes de gastar créditos en
                    # check_blacklist / check_judicial.
                    guard = sg.ensure_customer(cid)
                    if guard.get("ok"):
                        bl = sg.check_blacklist(cid)
                        result["listas"]["singula"] = bl
                        if isinstance(bl, dict) and bl.get("hit"):
                            result["hit"] = True
                        jd = sg.check_judicial(cid)
                        result["judicial"] = jd
                    else:
                        result["listas"]["singula"] = {"error": "customer no válido", "guard": guard}
            except Exception as e:
                result["listas"]["singula"] = {"error": str(e)}
        return result

    def enrich_huella(self, curp: str = None, rfc: str = None, nss: str = None,
                      nombre: str = "", paterno: str = "", materno: str = "",
                      fecnac: str = "", email: str = "", telefono: str = "",
                      direccion: str = "", ciudad: str = "", estado: str = "",
                      cp: str = "", env: str = None,
                      paquete: str = "premium") -> dict:
        """Fase 4: huella digital completa (Singula customer-centric).

        Paquetes disponibles:
          - "basico"   $13 MXN: CURP + RFC + email_lookup + phone_lookup
          - "completo" $13 MXN: basico + blacklist + judicial + risk
          - "premium"  $31 MXN: completo + intel_basic + intel_premium

        Returns:
          {
            "fase": "huella",
            "paquete": "premium",
            "costo_mxn": 31.00,
            "errores": [...],
            "customer": {...},
            "customer_id": "cus_xxx",
            "curp": {...}, "rfc": {...},
            "email_lookup": {...}, "phone_lookup": {...},
            "blacklist": {...}, "judicial": {...}, "risk": {...},
            "intel": {...}, "intel_premium": {...},
            "endpoints_ejecutados": [...],
          }
        """
        from config import config

        # paquetes
        paquetes = {
            "basico":   ["curp", "rfc", "email_lookup", "phone_lookup"],
            "completo": ["curp", "rfc", "email_lookup", "phone_lookup",
                         "blacklist", "judicial", "risk"],
            "premium":  ["curp", "rfc", "email_lookup", "phone_lookup",
                         "blacklist", "judicial", "risk", "intel", "intel_premium"],
        }
        if paquete not in paquetes:
            paquete = "premium"
        endpoints = paquetes[paquete]
        costo = config.singula_paquetes.get(paquete, {}).get("costo_mxn", 0)

        result = {
            "fase": "huella",
            "paquete": paquete,
            "costo_mxn": costo,
            "errores": [],
            "endpoints_ejecutados": [],
        }
        if "singula" not in self.clients:
            result["errores"].append("Singula no configurado")
            return result

        singula = self.clients["singula"]

        # parsear fecha
        try:
            if fecnac and len(fecnac) >= 10:
                yyyy, mm, dd = fecnac[:10].split("-")
                birth_day, birth_month, birth_year = dd, mm, yyyy
            else:
                birth_day, birth_month, birth_year = "01", "01", "1980"
        except Exception:
            birth_day, birth_month, birth_year = "01", "01", "1980"

        # crear customer (o reusar) — 2026-08-04: usar get_or_create_customer
        # (idempotente, cache local, evita llamadas duplicadas a la API).
        try:
            res = singula.get_or_create_customer(
                name=nombre or "DESCONOCIDO",
                last_name=paterno or "DESCONOCIDO",
                mothers_last_name=materno or "",
                gender="H",
                birth_day=birth_day, birth_month=birth_month, birth_year=birth_year,
                curp=curp, rfc=rfc, nss=nss, env=env,
                email=email, phone=telefono,
                address=direccion, city=ciudad, state=estado, postal_code=cp,
                custom_id=f"nexo-{curp or rfc or 'sinid'}",
            )
            cust = res.get("customer") or {}
            cid = res.get("id")
            if not cid:
                result["errores"].append(f"get_or_create_customer: {cust.get('error', cust)}")
                return result
            result["customer"] = cust
            result["customer_id"] = cid
            result["customer_created"] = res.get("created", False)
        except Exception as e:
            result["errores"].append(f"get_or_create_customer: {e}")
            return result

        # 2026-08-04: validar el customer antes de gastar créditos en
        # endpoints customer-centric. Si el id no es válido, abortamos el
        # pipeline de huella sin cobrar nada más.
        guard = singula.ensure_customer(cid)
        if not guard.get("ok"):
            result["errores"].append(f"ensure_customer: {guard.get('error')}")
            return result

        # ejecutar endpoints en paralelo
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # 2026-08-04: 'rfc' quitado de los tasks. Singula no debe validar RFC
        # (eso lo hace CheckID). get_rfc_for_customer está bloqueado con
        # validate=True en el provider.
        tasks = {
            "curp": (singula.get_curp_for_customer, [cid, False]),  # sólo lectura
            "email_lookup": (singula.email_lookup, [cid]),
            "phone_lookup": (singula.phone_lookup, [cid]),
            "blacklist": (singula.check_blacklist, [cid]),
            "judicial": (singula.check_judicial, [cid]),
            "risk": (singula.get_risk, [cid]),
            "intel": (singula.intel_basic, [cid]),
            "intel_premium": (singula.intel_premium, [cid]),
        }
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {name: ex.submit(fn, *args) for name, (fn, args) in tasks.items()
                       if name in endpoints}
            for name, fut in futures.items():
                try:
                    result[name] = fut.result(timeout=60)
                    result["endpoints_ejecutados"].append(name)
                except Exception as e:
                    result[name] = {"error": str(e)}

        return result

    def full_kyc(
        self,
        curp: str = None,
        nombre: str = None,
        paterno: str = None,
        materno: str = None,
        fecnac: str = None,
        rfc: str = None,
        email: str = None,
        telefono: str = None,
    ) -> dict:
        """Ejecuta KYC completo en paralelo con todos los proveedores activos.

        Returns:
            dict con resultados de cada proveedor + síntesis.
        """
        curp_clean = normalize_curp(curp) if curp else None
        rfc_clean = normalize_rfc(rfc) if rfc else None
        t0 = time.time()

        result = {
            "metadata": {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "sujeto": {
                    "curp": curp_clean, "rfc": rfc_clean,
                    "nombre": nombre, "paterno": paterno, "materno": materno,
                    "fecnac": fecnac, "email": email, "telefono": telefono,
                },
                "proveedores_usados": list(self.clients.keys()),
            },
            "resultados": {},
        }

        # definir tareas
        def task_tlaloc():
            if "tlaloc" not in self.clients or not curp_clean:
                return ("tlaloc", None)
            try:
                return ("tlaloc", self.clients["tlaloc"].validate_curp(curp_clean))
            except Exception as e:
                return ("tlaloc", {"error": str(e)})

        def task_singula():
            if "singula" not in self.clients:
                return ("singula", None)
            try:
                if curp_clean:
                    r = self.clients["singula"].validate_curp(curp_clean)
                else:
                    # ELIMINADO: validate_rfc ya no se usa — CheckID valida el RFC
                    return ("singula", None)
                # blacklist + score si hay nombre
                if nombre:
                    r["blacklist"] = self.clients["singula"].check_blacklist(
                        curp=curp_clean, rfc=rfc_clean, nombre=f"{nombre} {paterno or ''} {materno or ''}".strip()
                    )
                return ("singula", r)
            except Exception as e:
                return ("singula", {"error": str(e)})

        def task_moffin():
            if "moffin" not in self.clients or not curp_clean:
                return ("moffin", None)
            try:
                return ("moffin", self.clients["moffin"].query_renapo_curp(curp_clean))
            except Exception as e:
                return ("moffin", {"error": str(e)})

        def task_kiban():
            if "kiban" not in self.clients or not curp_clean:
                return ("kiban", None)
            try:
                r = {"curp_validate": self.clients["kiban"].validate_curp(curp_clean)}
                # PEP/OFAC
                if nombre:
                    r["pep_ofac"] = self.clients["kiban"].check_pep_ofac(
                        nombre=nombre, apellido_paterno=paterno or "",
                        apellido_materno=materno or "", fecha_nac=fecnac,
                    )
                return ("kiban", r)
            except Exception as e:
                return ("kiban", {"error": str(e)})

        def task_apify():
            if "apify" not in self.clients:
                return ("apify", None)
            try:
                return ("apify", self.clients["apify"].full_osint(
                    nombre=nombre or "", paterno=paterno or "", materno=materno or "",
                    email=email, telefono=telefono, max_results=10,
                ))
            except Exception as e:
                return ("apify", {"error": str(e)})

        def task_buro():
            if "buro" not in self.clients:
                return ("buro", None)
            try:
                if not (nombre and paterno and fecnac):
                    return ("buro", {"skipped": "datos insuficientes"})
                return ("buro", self.clients["buro"].consulta_reporte(
                    nombre=nombre, paterno=paterno, materno=materno or "",
                    fecnac=fecnac, rfc=rfc_clean, curp=curp_clean,
                ))
            except Exception as e:
                return ("buro", {"error": str(e)})

        def task_cdc():
            if "cdc" not in self.clients:
                return ("cdc", None)
            try:
                if not (nombre and paterno and fecnac):
                    return ("cdc", {"skipped": "datos insuficientes"})
                persona = {
                    "PrimerNombre": nombre,
                    "ApellidoPaterno": paterno,
                    "ApellidoMaterno": materno or "",
                    "FechaNacimiento": fecnac,
                    "Domicilio": {
                        "Direccion": "",
                        "ColoniaPoblacion": "",
                        "DelegacionMunicipio": "",
                        "Ciudad": "",
                        "Estado": "",
                        "Cp": "",
                    },
                }
                if rfc_clean: persona["Rfc"] = rfc_clean
                if curp_clean: persona["Curp"] = curp_clean
                return ("cdc", self.clients["cdc"].consulta_reporte("rcc_fico", persona))
            except Exception as e:
                return ("cdc", {"error": str(e)})

        tasks = [task_tlaloc, task_singula, task_moffin, task_kiban,
                 task_apify, task_buro, task_cdc]
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(t) for t in tasks]
            for fut in as_completed(futures, timeout=300):
                try:
                    name, data = fut.result()
                    result["resultados"][name] = data
                    self._log(f"  {name}: {'OK' if data and 'error' not in str(data)[:200] else 'con error'}")
                except Exception as e:
                    self._log(f"  task error: {e}")

        # síntesis
        result["sintesis"] = self._sintetizar(result["resultados"])
        result["metadata"]["duracion_seg"] = round(time.time() - t0, 2)
        return result

    def _sintetizar(self, resultados: dict) -> dict:
        """Genera alertas y métricas a partir de los resultados."""
        s = {
            "proveedores_ok": [],
            "proveedores_error": [],
            "alertas": [],
        }
        for name, data in resultados.items():
            if not data:
                continue
            if isinstance(data, dict) and "error" in data:
                s["proveedores_error"].append(name)
            else:
                s["proveedores_ok"].append(name)
        # alertas
        singula = resultados.get("singula", {})
        if isinstance(singula, dict):
            bl = singula.get("blacklist", {})
            if isinstance(bl, dict) and bl.get("hit") or bl.get("matches"):
                s["alertas"].append("singula: sujeto en lista negra")
        kiban = resultados.get("kiban", {})
        if isinstance(kiban, dict):
            pep = kiban.get("pep_ofac", {})
            if isinstance(pep, dict) and (pep.get("matches") or pep.get("pep_found")):
                s["alertas"].append("kiban: sujeto en lista PEP/OFAC")
        return s


# === CLI ===

def main():
    import argparse
    ap = argparse.ArgumentParser(description="KYC broker — consulta múltiples APIs")
    ap.add_argument("--curp")
    ap.add_argument("--rfc")
    ap.add_argument("--nombre")
    ap.add_argument("--paterno")
    ap.add_argument("--materno")
    ap.add_argument("--fecnac", help="DD/MM/YYYY")
    ap.add_argument("--email")
    ap.add_argument("--telefono")
    ap.add_argument("--json", help="Guardar en archivo")
    ap.add_argument("--health", action="store_true", help="Solo health check")
    args = ap.parse_args()

    broker = KYCBroker(verbose=True)

    if args.health:
        import json
        print(json.dumps(broker.health_check(), indent=2, default=str))
        return

    result = broker.full_kyc(
        curp=args.curp, rfc=args.rfc,
        nombre=args.nombre, paterno=args.paterno, materno=args.materno,
        fecnac=args.fecnac, email=args.email, telefono=args.telefono,
    )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str, ensure_ascii=False)
        print(f"Guardado en {args.json}")
    else:
        import json
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
