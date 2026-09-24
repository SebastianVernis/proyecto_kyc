#!/usr/bin/env python3
"""singula.py — Cliente Singula (KYC + huella digital completa).

Documentación: https://api.singula.mx/api
Sandbox gratis. Producción: por uso.

MODELOS CUBIERTOS:
  Identidad:  CURP, RFC, NSS
  Huella:     email-lookup, phone-lookup, intel, intel-premium
  Riesgo:     judicial, blacklist, risk

FLUJO RECOMENDADO:
  1. POST /customer     → crea customer con datameta (email, phone, address)
  2. GET  /app/rfc/customer/{id}/validate  → valida RFC
  3. GET  /app/curp/customer/{id}/validate → valida CURP
  4. GET  /app/email-lookup/customer/{id}  → email → redes sociales
  5. GET  /app/phone-lookup/customer/{id}  → teléfono → carrier, region
  6. GET  /app/judicial/customer/{id}      → antecedentes judiciales
  7. GET  /app/blacklist/customer/{id}     → listas negras (OFAC, UE, ONU, UK, PEP)
  8. GET  /app/risk/customer/{id}          → score de riesgo
  9. POST /app/intel-premium/customer/{id} → dossier narrativo + huella digital
"""

from __future__ import annotations

import os
from .base import BaseProvider, ProviderError, normalize_curp, normalize_rfc


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Deep-merge: combina dos dicts recursivamente. overlay gana.

    Para cada key:
      - si ambos son dict, merge recursivo
      - si no, overlay sobrescribe
    Las listas y valores no-dict en overlay sobrescriben base.
    """
    if not isinstance(base, dict):
        return dict(overlay) if isinstance(overlay, dict) else {}
    if not isinstance(overlay, dict):
        return dict(base)
    result = dict(base)
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class SingulaClient(BaseProvider):
    """Cliente Singula — modelo customer-centric."""

    def __init__(self, api_key: str = None, base_url: str = "https://api.singula.mx",
                 timeout: int = 30, organization_id: str = None,
                 env: str = None):
        super().__init__(name="singula", api_key=api_key, base_url=base_url, timeout=timeout)
        # environment from config or override
        self.env = env or os.getenv("SINGULA_ENV") or "sandbox"
        # organization_id se puede pasar por env o se autodescubre
        self.organization_id = (
            organization_id if organization_id is not None else os.getenv("SINGULA_ORG_ID")
        )
        # Cache local curp/custom_id → customer_id (la API de Singula no
        # provee lookup confiable por CURP, solo por prefijo de id o nombre)
        self._customer_cache: dict = {}

    def health(self) -> dict:
        try:
            r = self._post("/app/curp/validate", json={"curp": "PEPJ900101HDFRRL00"})
            return {"ok": True, "sandbox": True}
        except ProviderError as e:
            return {"ok": False, "error": str(e)}

    # ==================== CUSTOMERS ====================

    # ==================== GUARDIA DE CUSTOMER ====================

    def ensure_customer(self, customer_id: str) -> dict:
        """Valida que un customer existe y es usable antes de cualquier
        llamada a endpoints que lo requieren (email_lookup, intel_basic,
        check_judicial, etc.).

        La API de Singula puede tardar en estar lista para un customer recién
        creado, o el id puede venir de un cache obsoleto. Llamar a un endpoint
        customer-centric con un id inválido hace que se gaste un crédito
        inútil y devuelva un error críptico.

        Returns: {"ok": bool, "id": str, "error": str | None}
        """
        if not customer_id or not isinstance(customer_id, str):
            return {"ok": False, "id": None, "error": "customer_id vacío o inválido"}
        existing = self.get_customer(customer_id)
        if not isinstance(existing, dict):
            return {"ok": False, "id": customer_id, "error": f"get_customer devolvió no-dict: {existing!r}"}
        if existing.get("error"):
            return {"ok": False, "id": customer_id, "error": existing["error"]}
        real_id = existing.get("id")
        if real_id != customer_id:
            return {"ok": False, "id": customer_id,
                    "error": f"id en respuesta ({real_id}) no coincide con el solicitado ({customer_id})"}
        return {"ok": True, "id": customer_id, "error": None}

    def get_or_create_customer(self, name: str, last_name: str, mothers_last_name: str = "",
                               gender: str = "H", birth_day: str = "", birth_month: str = "",
                               birth_year: str = "", curp: str = None, rfc: str = None,
                               nss: str = None, email: str = None, phone: str = None,
                               address: str = None, city: str = None, state: str = None,
                               postal_code: str = None, env: str = None,
                               custom_id: str = None) -> dict:
        """Devuelve un customer existente o crea uno nuevo. Idempotente.

        Usa un cache local (curp/custom_id → customer_id) porque la API de
        Singula no provee un lookup confiable por CURP.

        Returns: {"id": str, "customer": dict, "created": bool}
        """
        env = env or self.env
        # 1) buscar en cache
        cache_key = (curp or custom_id or "").upper()
        if cache_key and cache_key in self._customer_cache:
            cached_id = self._customer_cache[cache_key]
            # verificar que sigue existiendo (puede haber sido purgado en sandbox)
            existing = self.get_customer(cached_id)
            if isinstance(existing, dict) and existing.get("id") == cached_id:
                return {"id": cached_id, "customer": existing, "created": False}
            # si ya no existe, limpiar cache
            self._customer_cache.pop(cache_key, None)

        # 2) intentar find_by_curp (puede no funcionar en sandbox)
        if curp:
            found = self.find_by_curp(curp, env=env)
            if isinstance(found, dict) and found.get("id"):
                self._customer_cache[curp.upper()] = found["id"]
                return {"id": found["id"], "customer": found, "created": False}

        # 2.5) FALLBACK POR BÚSQUEDA EN SINGULA (2026-08-05).
        # Singula NO tiene lookup por CURP ni por custom_id, pero
        # /customer/search?q="name last_name mothers_last_name" sí busca
        # por nombre. Para cada hit hace un GET /customer/{id} y compara
        # la CURP. Si matchea, reusamos el customer (evita crear duplicado
        # cuando el local cache se perdió: key rotada, restart, DB
        # reconstruida).
        if curp:
            anchor_custom_id = custom_id or self._build_custom_id_anchor(curp, birth_month, birth_year)
            existing = self._search_customer_by_custom_id(
                anchor_custom_id,
                env=env,
                curp=curp,
                name=name,
                last_name=last_name,
                mothers_last_name=mothers_last_name,
            )
            if existing and existing.get("id"):
                self._customer_cache[curp.upper()] = existing["id"]
                if anchor_custom_id:
                    self._customer_cache[anchor_custom_id.upper()] = existing["id"]
                return {"id": existing["id"], "customer": existing, "created": False}

        # 3) crear nuevo
        created = self.create_customer(
            name=name, last_name=last_name, mothers_last_name=mothers_last_name,
            gender=gender, birth_day=birth_day, birth_month=birth_month,
            birth_year=birth_year, curp=curp, rfc=rfc, nss=nss, env=env,
            email=email, phone=phone, address=address, city=city, state=state,
            postal_code=postal_code, custom_id=custom_id,
        )
        new_id = created.get("id") if isinstance(created, dict) else None
        if new_id and cache_key:
            self._customer_cache[cache_key] = new_id
        return {"id": new_id, "customer": created, "created": True}


    def _build_custom_id_anchor(self, curp: str, birth_month: str = "",
                                 birth_year: str = "") -> str:
        """Genera el custom_id determinístico que usamos al crear customers.

        Formato: "nexo-{CURP}-{MM}-{YYYY}". Singula lo acepta como
        `custom_id` al crear el customer, y luego /customer/search?q=<id>
        puede encontrarlo por coincidencia. Determinístico y reversible.
        """
        if not curp:
            return ""
        curp_u = curp.upper().strip()
        # Si los tenemos, únelos; si no, deja placeholders vacíos (no
        # es problema porque _search_customer_by_custom_id probará
        # tanto con como sin sufijo).
        mm = (birth_month or "").strip()
        yy = (birth_year or "").strip()
        if mm and yy:
            return f"nexo-{curp_u}-{mm}-{yy}"
        return f"nexo-{curp_u}"

    def _search_customer_by_custom_id(self, custom_id_anchor: str,
                                       env: str = None,
                                       curp: str = None,
                                       name: str = None,
                                       last_name: str = None,
                                       mothers_last_name: str = "") -> Optional[dict]:
        """Busca un customer existente en Singula por nombre + filtrado por CURP.

        La API documenta que /customer/search?q=<texto> busca por nombre
        completo (nombre + apellido paterno + apellido materno). NO busca
        por CURP ni por custom_id (verificado 2026-08-05: devolver
        {"data":[]} para ambos). Esta implementación:

          1. Compone q= como `<name> <last_name> <mothers_last_name>`.
          2. Hace GET /customer/search?q=<q>&env=<env>.
          3. Para cada hit (customer resumido), hace GET /customer/{id}
             y compara la CURP. Si coincide, devuelve el customer.
          4. Si no hay match exacto por CURP y el argumento `custom_id_anchor`
             coincide con el custom_id del customer encontrado, también sirve.

        Devuelve el dict del customer (desenvuelto) o None.

        NOTA: cada candidato incurre en 1 GET adicional (verificación de
        CURP). Para una búsqueda "limpia" con muchos candidatos esto puede
        ser lento. Pero en la práctica q=<nombre> devuelve 1-3 hits y es
        suficiente.
        """
        if not custom_id_anchor and not (name and last_name):
            return None
        env = env or self.env

        # Componer q para /customer/search
        if name and last_name:
            q_parts = [name, last_name]
            if mothers_last_name:
                q_parts.append(mothers_last_name)
            q = " ".join(p for p in q_parts if p).strip()
        else:
            # Sin nombre, intentar el anchor como búsqueda libre (no funciona
            # consistentemente, pero cubre el caso "anchor contains cus_xx")
            q = custom_id_anchor or ""

        if not q:
            return None

        try:
            r = self._get("/customer/search", params={"q": q, "env": env})
        except ProviderError:
            # 401/expirada o cualquier error de API: None silencioso
            return None
        if not isinstance(r, dict):
            return None

        data = r.get("data")
        if not isinstance(data, list) or not data:
            return None

        # Iterar hits y verificar CURP. Estrategia: el primero que matchee
        # exact-CURP gana. Esto distingue dos SAMUEL HERNANDEZ CRUZ cuando
        # uno tiene la CURP correcta (HECS921221HHGRRM05) y el otro no.
        # Si ninguno matchea CURP pero uno tiene custom_id exacto, úsalo.
        target_curp = (curp or "").upper().strip()
        match_by_curp = None
        match_by_anchor = None

        for item in data:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            if not cid:
                continue
            # Fetch completo para tener curp + custom_id
            try:
                full = self.get_customer(cid)
            except Exception:
                continue
            if not isinstance(full, dict) or not full.get("id"):
                continue

            curp_remote = (full.get("curp") or "").upper().strip()
            custom_remote = (full.get("custom_id") or "").strip()

            # 1) match exacto por CURP (preferido)
            if target_curp and curp_remote == target_curp:
                match_by_curp = full
                break  # ya tenemos el bueno
            # 2) match por custom_id anchor (defensa)
            if not match_by_anchor and custom_id_anchor and custom_remote == custom_id_anchor:
                match_by_anchor = full

        if match_by_curp:
            # Cachear ambas claves (CURP y custom_id) para futuros hits
            if target_curp:
                self._customer_cache[target_curp] = match_by_curp["id"]
            cid_remote = match_by_curp.get("custom_id")
            if cid_remote:
                self._customer_cache[cid_remote.upper()] = match_by_curp["id"]
            return match_by_curp
        if match_by_anchor:
            if target_curp:
                self._customer_cache[target_curp] = match_by_anchor["id"]
            cid_remote = match_by_anchor.get("custom_id")
            if cid_remote:
                self._customer_cache[cid_remote.upper()] = match_by_anchor["id"]
            return match_by_anchor

        return None

    def create_customer(self, name: str, last_name: str, mothers_last_name: str = "",
                        gender: str = "H", birth_day: str = "", birth_month: str = "",
                        birth_year: str = "", curp: str = None, rfc: str = None,
                        nss: str = None, env: str = None, email: str = None,
                        phone: str = None, address: str = None, city: str = None,
                        state: str = None, postal_code: str = None,
                        custom_id: str = None, company: str = None,
                        job_title: str = None, birth_place: str = "DF") -> dict:
        """POST /customer — crea un customer con datos demográficos.

        Devuelve dict con 'id' (customer_id) y 'organization_id'.
        """
        env = env or self.env
        if not self.organization_id:
            # intentar descubrirlo del primer customer
            try:
                r = self._get("/customer", params={"env": env, "page": 1})
                if isinstance(r, dict) and r.get("data"):
                    self.organization_id = r["data"][0].get("organization_id")
            except Exception:
                pass
        if not self.organization_id:
            return {"error": "no se pudo obtener organization_id"}

        datameta = {}
        if email: datameta["email"] = email
        if phone: datameta["phone"] = phone
        if address: datameta["address"] = address
        if city: datameta["city"] = city
        if state: datameta["state"] = state
        if postal_code: datameta["postal_code"] = postal_code
        if company: datameta["company"] = company
        if job_title: datameta["job_title"] = job_title

        payload = {
            "custom_id": custom_id or f"nexo-{curp or rfc or birth_day}-{birth_month}-{birth_year}",
            "type": "physical",
            "name": name,
            "last_name": last_name,
            "gender": gender,
            "birth_day": str(birth_day).zfill(2) if birth_day else "01",
            "birth_month": str(birth_month).zfill(2) if birth_month else "01",
            "birth_year": str(birth_year) if birth_year else "1980",
            "birth_place": birth_place,
            "birth_country": "MX",
            "env": env,
            "organization_id": self.organization_id,
        }
        if mothers_last_name:
            payload["mothers_last_name"] = mothers_last_name
        if curp:
            payload["curp"] = normalize_curp(curp)
        if rfc:
            payload["rfc"] = normalize_rfc(rfc)
        if nss:
            payload["nss"] = nss
        if datameta:
            payload["datameta"] = datameta

        try:
            r = self._post("/customer", json=payload)
            data = r.get("data", r)
            if isinstance(data, dict) and "id" in data:
                self.organization_id = data.get("organization_id", self.organization_id)
            return data
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_customer(self, customer_id: str) -> dict:
        """GET /customer/{id} — obtiene el customer.

        La API puede responder:
          - {"customer": {...}, "requests": ...}      (algunos endpoints)
          - {"data": {"customer": {...}, "requests": ...}}  (otros)
        Devolvemos siempre el customer plano (con id, curp, etc).
        """
        try:
            r = self._get(f"/customer/{customer_id}")
            if not isinstance(r, dict):
                return r
            # primer nivel: buscar "data" o el dict mismo
            inner = r.get("data", r) if isinstance(r.get("data"), dict) else r
            # segundo nivel: buscar "customer" dentro de inner
            if isinstance(inner.get("customer"), dict):
                return inner["customer"]
            return inner
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def update_customer(self, customer_id: str, merge_datameta: bool = True,
                        **fields) -> dict:
        """PATCH /customer/{id} — actualiza un customer existente.

        Campos aceptados: name, last_name, mothers_last_name, gender,
        birth_day, birth_month, birth_year, birth_place, birth_country,
        curp, rfc, nss, custom_id, datameta.

        Si merge_datameta=True y se pasa datameta, hace deep-merge con el
        datameta existente (preserva secciones no incluidas en fields).
        """
        try:
            if merge_datameta and "datameta" in fields and isinstance(fields["datameta"], dict):
                # leer datameta actual
                existing = self.get_customer(customer_id)
                if isinstance(existing, dict):
                    existing_dm = existing.get("datameta") or {}
                    if isinstance(existing_dm, dict):
                        fields["datameta"] = _deep_merge(existing_dm, fields["datameta"])
            r = self._patch(f"/customer/{customer_id}", json=fields)
            if not isinstance(r, dict):
                return r
            inner = r.get("data", r) if isinstance(r.get("data"), dict) else r
            if isinstance(inner.get("customer"), dict):
                return inner["customer"]
            return inner
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def search_customer(self, query: str, env: str = None) -> dict:
        """GET /customer/search?q=... — busca customers por nombre o custom_id."""
        try:
            r = self._get("/customer/search", params={"q": query, "env": env or self.env})
            return r.get("data", [])
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def find_by_curp(self, curp: str, env: str = None) -> dict:
        """Busca un customer existente por CURP."""
        try:
            r = self._get("/customer/search", params={"q": curp, "env": env or self.env})
            data = r.get("data", []) if isinstance(r, dict) else []
            for c in data:
                if c.get("curp", "").upper() == curp.upper():
                    return c
            return None
        except ProviderError:
            return None

    # ==================== VALIDACIONES BÁSICAS ====================

    def validate_curp(self, curp: str) -> dict:
        """POST /app/curp/validate — valida CURP (no requiere customer)."""
        curp_clean = normalize_curp(curp)
        if len(curp_clean) != 18:
            return {"valid": False, "curp": curp_clean, "error": "longitud inválida"}
        try:
            r = self._post("/app/curp/validate", json={"curp": curp_clean})
            data = r.get("data", r) if isinstance(r, dict) else {}
            persona = data.get("persona", {}) if isinstance(data, dict) else {}
            return {
                "valid": True,
                "status": data.get("message") if isinstance(data, dict) else None,
                "curp": persona.get("CURP", curp_clean),
                "nombres": persona.get("nombres"),
                "apellido_paterno": persona.get("apellidoPaterno"),
                "apellido_materno": persona.get("apellidoMaterno"),
                "sexo": persona.get("sexo"),
                "fecha_nacimiento": persona.get("fechaNacimiento"),
                "nacionalidad": persona.get("nacionalidad"),
                "entidad": persona.get("entidad"),
                "status_curp": persona.get("statusCurp"),
                "raw": r,
            }
        except ProviderError as e:
            return {"valid": False, "curp": curp_clean, "error": str(e), "status": e.status}

    def validate_rfc(self, rfc: str) -> dict:
        """BLOQUEADO — La validación de RFC se hace con CheckID, no con Singula.

        Llamar a Singula /app/rfc/validate duplica la consulta (CheckID ya la
        hace contra el SAT) y gasta un crédito inútil. Si necesitas validar
        un RFC, usa providers.checkid.CheckIDClient.
        """
        return {
            "valid": False,
            "rfc": rfc,
            "error": "validate_rfc() de Singula está BLOQUEADO: la validación "
                     "de RFC la hace CheckID, no Singula. Ver providers/checkid.py.",
            "blocked": True,
        }

    def get_rfc(self, name: str, last_name: str, mothers_last_name: str = "",
                birth_day: str = "", birth_month: str = "", birth_year: str = "",
                gender: str = "H", tax_regime: str = None) -> dict:
        """POST /app/rfc — OBTIENE el RFC desde datos demográficos (con homoclave real del SAT).

        Returns:
            {
                "rfc": str (RFC a 13 chars con homoclave real),
                "rfc_sin_homoclave": str (12 chars),
                "homoclave": str (2 chars),
                "dv": str (1 char),
                "raw": dict,
            }
        """
        body = {
            "name": name.strip().upper(),
            "last_name": last_name.strip().upper(),
            "mothers_last_name": mothers_last_name.strip().upper() if mothers_last_name else "",
            "gender": gender,
            "birth_day": str(birth_day).zfill(2),
            "birth_month": str(birth_month).zfill(2),
            "birth_year": str(birth_year),
        }
        if tax_regime:
            body["tax_regime"] = tax_regime
        try:
            r = self._post("/app/rfc", json=body)
            # la respuesta puede ser: dict, dict con "data", string directo
            if isinstance(r, str):
                rfc_full = r.strip()
                data = {}
            elif isinstance(r, dict):
                data = r.get("data", r)
                if isinstance(data, str):
                    rfc_full = data.strip()
                elif isinstance(data, dict):
                    rfc_full = data.get("rfc", "")
                else:
                    rfc_full = ""
            else:
                rfc_full = ""
            rfc_12 = rfc_full[:12] if len(rfc_full) >= 12 else rfc_full
            return {
                "rfc": rfc_full,
                "rfc_sin_homoclave": rfc_12,
                "homoclave": (data.get("homoclave") if isinstance(data, dict) else None) or (rfc_full[10:12] if len(rfc_full) >= 12 else ""),
                "dv": (data.get("dv") if isinstance(data, dict) else None) or (rfc_full[12:13] if len(rfc_full) >= 13 else ""),
                "raw": r,
            }
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_curp(self, name: str, last_name: str, mothers_last_name: str = "",
                gender: str = "H", birth_day: str = "", birth_month: str = "",
                birth_year: str = "", birth_place: str = "DF",
                birth_country: str = "MX") -> dict:
        """POST /app/curp — OBTIENE la CURP desde datos demográficos."""
        body = {
            "name": name.strip().upper(),
            "last_name": last_name.strip().upper(),
            "mothers_last_name": mothers_last_name.strip().upper() if mothers_last_name else "",
            "gender": gender,
            "birth_day": str(birth_day).zfill(2),
            "birth_month": str(birth_month).zfill(2),
            "birth_year": str(birth_year),
            "birth_place": birth_place,
            "birth_country": birth_country,
        }
        try:
            r = self._post("/app/curp", json=body)
            # respuesta puede ser: string directo, dict con "data", etc
            curp = None
            if isinstance(r, str):
                curp = r.strip()
            elif isinstance(r, dict):
                data = r.get("data", r)
                if isinstance(data, str):
                    curp = data.strip()
                elif isinstance(data, dict):
                    curp = data.get("curp")
            return {"curp": curp, "raw": r}
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_company_rfc(self, name: str, registration_day: str,
                        registration_month: str, registration_year: str) -> dict:
        """POST /app/rfc/company — OBTIENE el RFC de una empresa desde su nombre y fecha de registro."""
        body = {
            "name": name.strip().upper(),
            "registrationDay": str(registration_day).zfill(2),
            "registrationMonth": str(registration_month).zfill(2),
            "registrationYear": str(registration_year),
        }
        try:
            r = self._post("/app/rfc/company", json=body)
            rfc = None
            if isinstance(r, str):
                rfc = r.strip()
            elif isinstance(r, dict):
                data = r.get("data", r)
                if isinstance(data, str):
                    rfc = data.strip()
                elif isinstance(data, dict):
                    rfc = data.get("rfc")
            return {"rfc": rfc, "raw": r}
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def validate_curp_async(self, curp: str, create_customer: bool = False,
                            sync: bool = False) -> dict:
        """POST /app/curp/validate — Validar CURP (asíncrono, devuelve request_id).

        Si sync=True, espera el resultado. Si no, devuelve solo el request_id.
        """
        body = {"curp": curp.strip().upper(), "create_customer": create_customer, "sync": sync}
        try:
            r = self._post("/app/curp/validate", json=body)
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    # ==================== LOOKUPS (requieren customer_id) ====================

    def email_lookup(self, customer_id: str) -> dict:
        """GET /app/email-lookup/customer/{id} — busca redes sociales por email.

        Returns:
          {
            "email": str,
            "total_found": int,
            "platforms": [{name, exists, url}, ...],
            "total_checked": int,
          }
        """
        try:
            r = self._get(f"/app/email-lookup/customer/{customer_id}")
            data = r.get("data", r) if isinstance(r, dict) else {}
            return {
                "email": data.get("email"),
                "total_found": data.get("total_found", 0),
                "platforms": data.get("platforms", []),
                "total_checked": data.get("total_checked", 0),
                "checked_at": data.get("checked_at"),
                "raw": r,
            }
        except ProviderError as e:
            if e.status == 400 and "no email" in str(e).lower():
                return {"error": "no email registrado en el customer",
                        "platforms": [], "total_found": 0}
            return {"error": str(e), "status": e.status}

    def phone_lookup(self, customer_id: str) -> dict:
        """GET /app/phone-lookup/customer/{id} — info del teléfono.

        Returns:
          {
            "phone": str,
            "valid": bool,
            "country": str,
            "country_name": str,
            "carrier": str,
            "line_type": "mobile|landline|voip",
            "region": str,
          }
        """
        try:
            r = self._get(f"/app/phone-lookup/customer/{customer_id}")
            data = r.get("data", r) if isinstance(r, dict) else {}
            return {
                "phone": data.get("phone"),
                "valid": data.get("valid"),
                "country": data.get("country"),
                "country_name": data.get("country_name"),
                "carrier": data.get("carrier"),
                "line_type": data.get("line_type"),
                "region": data.get("region"),
                "checked_at": data.get("checked_at"),
                "raw": r,
            }
        except ProviderError as e:
            if e.status == 400 and "no phone" in str(e).lower():
                return {"error": "no phone registrado en el customer"}
            return {"error": str(e), "status": e.status}

    # ==================== LISTAS DE RIESGO ====================

    def check_blacklist(self, customer_id: str = None) -> dict:
        """GET /app/blacklist/customer/{id} — OFAC, UE, ONU, UK, PEP."""
        if not customer_id:
            return {"error": "customer_id requerido", "hit": False}
        try:
            r = self._get(f"/app/blacklist/customer/{customer_id}")
            data = r.get("data", r) if isinstance(r, dict) else {}
            return {
                "hit": (data.get("total", 0) or 0) > 0,
                "total": data.get("total", 0),
                "risk_level": data.get("risk_summary", {}).get("level"),
                "best_match": data.get("risk_summary", {}).get("best_match"),
                "raw": r,
            }
        except ProviderError as e:
            return {"error": str(e), "hit": False}

    def check_judicial(self, customer_id: str) -> dict:
        """GET /app/judicial/customer/{id} — antecedentes judiciales de persona."""
        try:
            r = self._get(f"/app/judicial/customer/{customer_id}")
            data = r.get("data", r) if isinstance(r, dict) else {}
            return {
                "status": data.get("status"),
                "total_records": data.get("total_records", 0),
                "risk_level": data.get("risk_level"),
                "summary": data.get("summary"),
                "sources_checked": data.get("sources_checked", []),
                "checked_at": data.get("checked_at"),
                "raw": r,
            }
        except ProviderError as e:
            return {"error": str(e)}

    def check_judicial_company(self, rfc: str, company_name: str = "") -> dict:
        """POST /app/judicial/company — antecedentes judiciales de empresa por RFC."""
        body = {"rfc": rfc.strip().upper()}
        if company_name:
            body["name"] = company_name.strip().upper()
        try:
            r = self._post("/app/judicial/company", json=body)
            data = r.get("data", r) if isinstance(r, dict) else {}
            return {
                "status": data.get("status"),
                "total_records": data.get("total_records", 0),
                "risk_level": data.get("risk_level"),
                "summary": data.get("summary"),
                "sources_checked": data.get("sources_checked", []),
                "checked_at": data.get("checked_at"),
                "raw": r,
            }
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def send_document_signature(self, customer_id: str, document_url: str = "",
                                email: str = "", phone: str = "") -> dict:
        """POST /app/document-signature/customer/{id} — envía documento para firma electrónica."""
        body = {}
        if document_url:
            body["documentUrl"] = document_url
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone
        try:
            r = self._post(f"/app/document-signature/customer/{customer_id}", json=body)
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_document_signature_status(self, request_id: str) -> dict:
        """GET /app/document-signature/status/{requestId} — estatus de firma."""
        try:
            return self._get(f"/app/document-signature/status/{request_id}")
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_risk(self, customer_id: str) -> dict:
        """GET /app/risk/customer/{id} — score de riesgo."""
        try:
            r = self._get(f"/app/risk/customer/{customer_id}")
            data = r.get("data", r) if isinstance(r, dict) else {}
            return {
                "score": data.get("score"),
                "risk_level": data.get("risk_level"),
                "confidence": data.get("confidence"),
                "tools_run": data.get("tools_run", []),
                "raw": r,
            }
        except ProviderError as e:
            return {"error": str(e)}

    def get_risk_signals(self, customer_id: str) -> dict:
        """GET /app/risk/customer/{id}/signals — señales detalladas."""
        try:
            r = self._get(f"/app/risk/customer/{customer_id}/signals")
            return r.get("data", r) if isinstance(r, dict) else {}
        except ProviderError as e:
            return {"error": str(e)}

    # ==================== DOSSIER (HUELLA DIGITAL) ====================

    def intel_basic(self, customer_id: str) -> dict:
        """POST /app/intel/customer/{id} — huella digital pública (basic)."""
        try:
            r = self._post(f"/app/intel/customer/{customer_id}", json={})
            data = r.get("data", r) if isinstance(r, dict) else {}
            return {
                "tier": data.get("tier"),
                "summary": data.get("summary"),
                "linkedin": data.get("linkedin"),
                "work_history": data.get("work_history", []),
                "education": data.get("education", []),
                "companies": data.get("companies", []),
                "web_results": data.get("web_results", []),
                "digital_footprint": data.get("digital_footprint"),
                "raw": r,
            }
        except ProviderError as e:
            return {"error": str(e)}

    def intel_premium(self, customer_id: str) -> dict:
        """POST /app/intel-premium/customer/{id} — dossier premium.

        Devuelve narrative + linkedin + companies + work + edu + web + digital.
        """
        try:
            r = self._post(f"/app/intel-premium/customer/{customer_id}", json={})
            data = r.get("data", r) if isinstance(r, dict) else {}
            return {
                "tier": data.get("tier"),
                "summary": data.get("summary"),
                "linkedin": data.get("linkedin"),
                "work_history": data.get("work_history", []),
                "education": data.get("education", []),
                "companies": data.get("companies", []),
                "web_results": data.get("web_results", []),
                "digital_footprint": data.get("digital_footprint"),
                "raw": r,
            }
        except ProviderError as e:
            return {"error": str(e)}

    # ==================== RFC/CURP por customer ====================

    def get_curp_for_customer(self, customer_id: str, validate: bool = True) -> dict:
        """GET /app/curp/customer/{id}[/validate] — CURP del customer."""
        path = f"/app/curp/customer/{customer_id}"
        if validate:
            path += "/validate"
        try:
            r = self._get(path)
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_rfc_for_customer(self, customer_id: str, validate: bool = True) -> dict:
        """BLOQUEADO con validate=True — La validación de RFC la hace CheckID.

        El endpoint de Singula `GET /app/rfc/customer/{id}/validate` duplica la
        consulta que ya hace CheckID contra el SAT. Por default validate=True,
        así que cualquier llamada accidental queda bloqueada con un error
        explícito. Si en el futuro se necesita leer el RFC que Singula tiene
        en su customer (sin validar contra SAT), pasar `validate=False`.
        """
        if validate:
            return {
                "error": "get_rfc_for_customer(validate=True) de Singula está "
                         "BLOQUEADO: la validación de RFC la hace CheckID. "
                         "Si sólo quieres leer el RFC del customer, pasa "
                         "validate=False.",
                "blocked": True,
                "status": 451,  # "Unavailable for legal reasons" — semánticamente honesto
            }
        # lectura no-validante del RFC persistido en el customer
        path = f"/app/rfc/customer/{customer_id}"
        try:
            r = self._get(path)
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    # ==================== HELPERS DE ALTO NIVEL ====================

    def full_digital_footprint(self, name: str, last_name: str, mothers_last_name: str = "",
                              gender: str = "H", birth_day: str = "", birth_month: str = "",
                              birth_year: str = "", curp: str = None, rfc: str = None,
                              nss: str = None, email: str = None, phone: str = None,
                              address: str = None, city: str = None, state: str = None,
                              postal_code: str = None, env: str = None,
                              custom_id: str = None) -> dict:
        """Pipeline completo: crea customer + ejecuta todos los lookups.

        Returns:
          {
            "customer": {...},
            "curp": {...},
            "rfc": {...},
            "email_lookup": {...},
            "phone_lookup": {...},
            "blacklist": {...},
            "judicial": {...},
            "risk": {...},
            "intel": {...},
            "intel_premium": {...},
            "errors": [...]
          }
        """
        result = {"errors": []}
        # 1) crear/obtener customer (idempotente).
        # 2026-08-04: get_or_create_customer reemplaza find_by_curp + create_customer.
        # Además validamos con ensure_customer antes de gastar créditos en
        # endpoints customer-centric.
        res = self.get_or_create_customer(
            name=name, last_name=last_name, mothers_last_name=mothers_last_name,
            gender=gender, birth_day=birth_day, birth_month=birth_month,
            birth_year=birth_year, curp=curp, rfc=rfc, nss=nss, env=env,
            email=email, phone=phone, address=address, city=city, state=state,
            postal_code=postal_code, custom_id=custom_id,
        )
        cust = res.get("customer") or {}
        cid = res.get("id")
        result["customer"] = cust
        result["customer_id"] = cid
        result["customer_created"] = res.get("created", False)
        if not cid:
            result["errors"].append(f"get_or_create_customer: {cust.get('error', cust)}")
            return result

        # 2) guard antes de gastar créditos
        guard = self.ensure_customer(cid)
        if not guard.get("ok"):
            result["errors"].append(f"ensure_customer: {guard.get('error')}")
            return result

        # 3) CURP (sólo lectura, NO valida contra RENAPO — eso lo hace Tlaloc)
        if curp:
            result["curp"] = self.get_curp_for_customer(cid, validate=False)
        # 4) RFC BLOQUEADO — Singula no debe validar RFC, eso lo hace CheckID.
        #    Si necesitas el RFC del customer persistido, lee directo del
        #    datameta o del store local (singula_store.get_enrichment).
        if rfc:
            result["rfc"] = {
                "blocked": True,
                "error": "Validación de RFC con Singula BLOQUEADA — usar CheckID.",
                "rfc_input": rfc,
            }
        # 4) email lookup
        if email:
            result["email_lookup"] = self.email_lookup(cid)
        # 5) phone lookup
        if phone:
            result["phone_lookup"] = self.phone_lookup(cid)
        # 6) blacklist
        result["blacklist"] = self.check_blacklist(cid)
        # 7) judicial
        result["judicial"] = self.check_judicial(cid)
        # 8) risk
        result["risk"] = self.get_risk(cid)
        # 9) intel basic
        result["intel"] = self.intel_basic(cid)
        # 10) intel premium
        result["intel_premium"] = self.intel_premium(cid)

        return result

    def create_identity_verification(self, customer_id: str, slot: str = "INE",
                                    country: str = "MX") -> dict:
        """POST /app/identity-verification/customer/{customerId}/create — crea KYC slot (INE/IFE/PASSPORT)."""
        try:
            r = self._post(f"/app/identity-verification/customer/{customer_id}/create",
                            json={"slot": slot, "country": country})
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def submit_identity_verification(self, token: str) -> dict:
        """POST /app/identity-verification/submit/{token} — submit URL de verificación."""
        try:
            r = self._post(f"/app/identity-verification/submit/{token}", json={})
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_identity_verification_status(self, request_id: str) -> dict:
        """GET /app/identity-verification/status/{requestId} — verdict + extracted data."""
        try:
            r = self._get(f"/app/identity-verification/status/{request_id}")
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_identity_verification_files(self, customer_id: str) -> dict:
        """GET /app/identity-verification/customer/{customerId}/files."""
        try:
            r = self._get(f"/app/identity-verification/customer/{customer_id}/files")
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def extract_ine(self, customer_id: str) -> dict:
        """POST /app/ine-extraction/customer/{customerId} — OCR de INE."""
        try:
            r = self._post(f"/app/ine-extraction/customer/{customer_id}", json={})
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_ine_extraction_status(self, request_id: str) -> dict:
        """GET /app/ine-extraction/status/{requestId} — OCR data + lista nominal."""
        try:
            r = self._get(f"/app/ine-extraction/status/{request_id}")
            return r
        except ProviderError as e:
            return {"error": str(e), "status": e.status}

    def get_status(self) -> dict:
        """GET /app/status — status del sistema."""
        try:
            return self._get("/app/status")
        except ProviderError as e:
            return {"error": str(e), "status": e.status}


# === helper de integración con config ===

def make_client_from_config() -> "SingulaClient | None":
    try:
        from config import config
        if not config.singula_api_key:
            return None
        return SingulaClient(
            config.singula_api_key,
            env=getattr(config, "singula_env", "production"),
            organization_id=getattr(config, "singula_org_id", None),
        )
    except Exception:
        return None
