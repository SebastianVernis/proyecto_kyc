#!/usr/bin/env python3
"""report_generator.py — Genera reportes HTML premium con CheckID, Tlaloc, Singula, Apify y Redes Sociales.

Estructura:
  1. Portada (RFC, nombre, fecha, logo)
  2. Resumen ejecutivo (narrativa AI)
  3. Datos personales (padrón)
  4. Mapa de ubicación (sección completa con dirección y coordenadas)
  5. Identidad oficial (Tlaloc RENAPO)
  6. Situación fiscal y laboral (CheckID SAT/IMSS)
  7. Situación fiscal adicional (Singula SAT, fallback)
  8. Redes sociales (Social Media Finder — automático, solo coincidencias exactas)
  9. Observaciones, conclusiones y fuentes

Estilos: tipografía editorial, paleta corporativa oscura, tablas premium.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any, Optional


REPORT_CSS = """
@page {
  size: letter;
  margin: 14mm 15mm 14mm 15mm;
  @bottom-center {
    content: "NEXO · Confidential · Página " counter(page) " de " counter(pages);
    font-size: 7.5pt;
    color: #9ca3af;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 0;
  color: #1a1d24; background: #fafbfc;
  font-size: 10pt; line-height: 1.45;
}
.cover {
  height: 100vh; display: flex; flex-direction: column;
  background: linear-gradient(135deg, #0f1115 0%, #1a1d24 60%, #232730 100%);
  color: #e6e8ec; padding: 36px 44px; page-break-after: always;
  position: relative;
}
.cover::after {
  content: ""; position: absolute; bottom: 0; left: 0; right: 0; height: 6px;
  background: linear-gradient(90deg, #4f8cff 0%, #6ee7b7 50%, #facc15 100%);
}
.cover .brand {
  display: flex; align-items: center; gap: 12px;
  border-bottom: 1px solid #2c313c; padding-bottom: 14px; margin-bottom: 24px;
}
.cover .brand-mark {
  width: 38px; height: 38px; border-radius: 8px;
  background: linear-gradient(135deg, #4f8cff 0%, #6ee7b7 100%);
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 17px; color: #0f1115;
}
.cover .brand-name { font-size: 15px; font-weight: 600; letter-spacing: 0.5px; }
.cover .brand-sub { font-size: 10px; color: #8a93a6; margin-top: 2px; }
.cover .doc-type {
  font-size: 10px; color: #6ee7b7; text-transform: uppercase;
  letter-spacing: 2px; margin-top: auto; margin-bottom: 6px;
}
.cover h1 {
  font-size: 32px; font-weight: 700; margin: 0 0 4px 0;
  letter-spacing: -0.5px; line-height: 1.1;
}
.cover .subject {
  font-size: 14px; color: #8a93a6; margin-bottom: 24px;
}
.cover .meta-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 14px 20px;
  background: rgba(255,255,255,0.04); padding: 16px 20px; border-radius: 10px;
  border: 1px solid #2c313c;
}
.cover .meta-grid .k {
  font-size: 9px; color: #8a93a6; text-transform: uppercase; letter-spacing: 1px;
}
.cover .meta-grid .v { font-size: 12px; color: #e6e8ec; font-weight: 500; margin-top: 2px; }
.cover .footer {
  font-size: 9px; color: #8a93a6; margin-top: 24px;
  display: flex; justify-content: space-between; align-items: center;
}
.cover .confidential {
  background: #ef4444; color: #fff; padding: 3px 8px; border-radius: 4px;
  font-size: 8px; font-weight: 600; letter-spacing: 1.5px;
}
.page {
  background: #fff; padding: 24px 34px; margin: 0;
  page-break-after: always;
  box-shadow: 0 0 0 1px #e5e7eb;
}
.page h2 {
  font-size: 20px; font-weight: 700; color: #0f1115; margin: 0 0 4px 0;
  border-bottom: 3px solid #4f8cff; padding-bottom: 8px;
  display: flex; align-items: center; gap: 10px;
}
.page h2 .badge {
  background: #4f8cff; color: #fff; padding: 2px 7px; border-radius: 4px;
  font-size: 9px; font-weight: 600; letter-spacing: 0.5px;
}
.page h2 .num {
  background: #0f1115; color: #6ee7b7; padding: 2px 7px; border-radius: 4px;
  font-size: 10px; font-weight: 700;
}
.page h3 {
  font-size: 13px; font-weight: 600; color: #1a1d24; margin: 14px 0 6px 0;
  text-transform: uppercase; letter-spacing: 0.5px;
}
.narrative {
  font-size: 11px; line-height: 1.6; color: #1a1d24;
  background: #f8fafc; padding: 16px 20px; border-left: 4px solid #4f8cff;
  border-radius: 0 8px 8px 0; margin: 10px 0;
}
.narrative p { margin: 0 0 8px 0; }
.narrative p:last-child { margin-bottom: 0; }
table {
  width: 100%; border-collapse: collapse; margin: 8px 0;
  background: #fff; border: 1px solid #e5e7eb; border-radius: 6px;
  overflow: hidden; font-size: 10pt;
}
th {
  background: #0f1115; color: #e6e8ec; padding: 8px 12px;
  text-align: left; font-weight: 600; font-size: 9pt;
  text-transform: uppercase; letter-spacing: 0.5px;
}
td { padding: 7px 12px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }
tr:nth-child(even) td { background: #fafbfc; }
tr:last-child td { border-bottom: none; }
td.k { color: #6b7280; font-weight: 500; width: 32%; }
td.v { color: #1a1d24; font-weight: 500; }
.alert {
  padding: 10px 14px; border-radius: 6px; margin: 8px 0;
  font-size: 10px; display: flex; gap: 8px; align-items: start;
}
.alert-warn { background: #fef3c7; border-left: 3px solid #f59e0b; color: #92400e; }
.alert-ok { background: #d1fae5; border-left: 3px solid #10b981; color: #065f46; }
.alert-err { background: #fee2e2; border-left: 3px solid #ef4444; color: #991b1b; }
.alert-info { background: #dbeafe; border-left: 3px solid #3b82f6; color: #1e40af; }
.cfe-fallback-table {
  width: 100%; border-collapse: collapse; margin: 8px 0 0;
  font-size: 9px;
}
.cfe-fallback-table th, .cfe-fallback-table td {
  padding: 4px 6px; border-bottom: 1px solid #e5e7eb;
  text-align: left;
}
.cfe-fallback-table th {
  background: #f3f4f6; font-weight: 600;
}
.cfe-fallback-table .hint {
  font-size: 8px; color: #6b7280; margin-top: 4px;
}
.alert strong { font-weight: 700; }
.network-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 8px 0;
}
.network-card {
  background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px;
  padding: 10px 12px; border-left: 3px solid #4f8cff;
}
.network-card .plat {
  font-size: 10px; font-weight: 700; color: #1a1d24;
  text-transform: uppercase; letter-spacing: 0.5px;
}
.network-card .url {
  font-size: 9px; color: #4f8cff; word-break: break-all; margin-top: 3px;
  text-decoration: none;
}
.network-card .name { font-size: 9px; color: #6b7280; margin-top: 2px; }
.network-card .bio { font-size: 8px; color: #9ca3af; margin-top: 2px; font-style: italic; }
.network-card .followers { font-size: 8px; color: #10b981; margin-top: 2px; font-weight: 600; }
.section-intro {
  font-size: 10px; color: #6b7280; font-style: italic;
  margin: 0 0 10px 0; line-height: 1.5;
}
.tag {
  display: inline-block; background: #e5e7eb; color: #1a1d24;
  padding: 2px 7px; border-radius: 10px; font-size: 8px;
  font-weight: 600; margin-right: 3px; text-transform: uppercase;
  letter-spacing: 0.3px;
}
.tag.fiscal { background: #fef3c7; color: #92400e; }
.tag.id { background: #dbeafe; color: #1e40af; }
.tag.social { background: #fce7f3; color: #9d174d; }
.tag.pad { background: #e0e7ff; color: #3730a3; }
.tag.checkid { background: #10b981; color: #ffffff; }
.tag.black { background: #1f2937; color: #f9fafb; }
.sources-list {
  list-style: none; padding: 0; margin: 6px 0;
  display: grid; grid-template-columns: 1fr 1fr; gap: 5px;
}
.sources-list li {
  background: #f8fafc; padding: 6px 10px; border-radius: 4px;
  font-size: 9px; color: #4b5563; border: 1px solid #f3f4f6;
}
.sources-list li strong { color: #1a1d24; }
.kpi-row {
  display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin: 10px 0;
}
.kpi {
  background: #0f1115; color: #e6e8ec; padding: 12px 14px;
  border-radius: 8px; border-left: 3px solid #6ee7b7;
}
.kpi .kpi-k {
  font-size: 8px; color: #8a93a6; text-transform: uppercase; letter-spacing: 1px;
}
.kpi .kpi-v {
  font-size: 16px; font-weight: 700; color: #6ee7b7; margin-top: 3px;
}
.kpi.amber { border-left-color: #facc15; }
.kpi.amber .kpi-v { color: #facc15; }
.kpi.red { border-left-color: #ef4444; }
.kpi.red .kpi-v { color: #ef4444; }

/* === Mapa (sección completa) === */
.map-section {
  margin: 0;
}
.map-address-block {
  background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px;
  padding: 14px 18px; margin-bottom: 12px;
}
.map-address-block .addr-line {
  font-size: 11px; color: #1a1d24; margin-bottom: 3px;
}
.map-address-block .addr-line:last-child { margin-bottom: 0; }
.map-address-block .addr-label {
  font-size: 8px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px;
  margin-right: 6px;
}
.map-address-block .addr-value {
  font-weight: 600; color: #0f1115;
}
.map-coords {
  font-size: 9px; color: #6b7280; margin-top: 6px;
  font-family: monospace;
}
.map-container {
  margin: 12px 0;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  background: #f8fafc;
}
.map-container img {
  width: 100%;
  height: auto;
  display: block;
}
.map-caption {
  font-size: 8.5px; color: #6b7280; padding: 6px 10px;
  background: #f8fafc; border-top: 1px solid #e5e7eb;
  text-align: center;
}
.map-marker-row {
  display: flex; gap: 8px; align-items: center;
  margin-bottom: 8px; font-size: 9px; color: #6b7280;
}
.map-marker-row .pin {
  width: 10px; height: 10px; border-radius: 50%;
  background: #ef4444; flex-shrink: 0;
}
.map-meta {
  list-style: none;
  padding: 8px 12px;
  margin: 8px 0;
  background: #f8fafc;
  border-left: 3px solid #6366f1;
  border-radius: 4px;
  font-size: 9.5pt;
}
.map-meta li {
  padding: 2px 0;
  color: #1f2937;
}
.source-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 8.5pt;
  font-weight: 600;
  text-transform: uppercase;
  vertical-align: middle;
  margin-left: 4px;
}
.source-local { background: #10b981; color: #fff; }
.source-public { background: #3b82f6; color: #fff; }
.fuente-tag {
  background: #6366f1;
  color: #fff;
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 8.5pt;
  margin-left: 6px;
}
.badge.fuente-padron,
.badge.fuente-cfe,
.badge.fuente-att,
.badge.fuente-telcel,
.badge.fuente-imss,
.badge.fuente-repuve,
.badge.fuente-empleadores,
.badge.fuente-sepomex,
.badge.fuente-checkid {
  background: #6366f1;
  color: #fff;
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 9pt;
  margin-right: 4px;
}
}
"""


def _esc(s: Any) -> str:
    """Escapa HTML."""
    if s is None:
        return "—"
    s = str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


def _fmt_dict(d: dict) -> str:
    """Formatea un dict anidado como tabla HTML de dos columnas."""
    if not d:
        return ""
    rows = ""
    for k, v in d.items():
        if v is None or v == "":
            continue
        val = _esc(v)
        if isinstance(v, (list, dict)):
            val = f"<pre style='font-size:9px;white-space:pre-wrap;'>{_esc(json.dumps(v, ensure_ascii=False, default=str))}</pre>"
        rows += f"<tr><td class='k'>{_esc(k)}</td><td class='v'>{val}</td></tr>"
    return f"<table>{rows}</table>" if rows else ""


def _nl2p(text: str) -> str:
    """Convierte saltos de línea en <p>."""
    if not text:
        return "<p>—</p>"
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    return "".join(f"<p>{_esc(p)}</p>" for p in paragraphs)


def _build_direccion(sujeto: dict) -> str:
    """Construye dirección legible desde datos del padrón.

    Usa el layout canónico de normalizar_direccion (tipos de vialidad y
    asentamiento expandidos, municipio resuelto desde claves INE); si el
    normalizador no produce nada, cae al armado manual original.
    """
    try:
        import normalizar_direccion as N
        canon = N.normalizar_direccion(
            calle=sujeto.get("calle"), ext=sujeto.get("ext"),
            int_=sujeto.get("interior") or sujeto.get("int"),
            colonia=sujeto.get("colonia"), cp=sujeto.get("cp"),
            municipio=sujeto.get("municipio_nombre"),
            entidad=sujeto.get("estado_nombre"),
            e=sujeto.get("e"), m=sujeto.get("m"),
        )
        if canon.get("direccion_completa"):
            return canon["direccion_completa"]
    except Exception:
        pass
    partes = []
    calle = (sujeto.get("calle") or "").strip()
    ext = str(sujeto.get("ext") or "").strip()
    interior = str(sujeto.get("interior") or "").strip()

    if calle:
        num = ext or "S/N"
        partes.append(f"{calle} {num}")
        if interior:
            partes.append(f"Int. {interior}")
    colonia = (sujeto.get("colonia") or "").strip()
    if colonia:
        partes.append(colonia)
    municipio = (sujeto.get("municipio_nombre") or "").strip()
    if municipio:
        partes.append(municipio)
    estado = (sujeto.get("estado_nombre") or "").strip()
    if estado:
        partes.append(estado)
    cp = str(sujeto.get("cp") or "").strip()
    if cp and cp != "00000":
        partes.append(f"CP {cp[:5]}")
    return ", ".join(partes) if partes else "—"


def generate_subject_html(subject_data: dict, narrative: str = "",
                          enrichment: dict = None,
                          map_image_base64: Optional[str] = None,
                          map_location: Optional[dict] = None,
                          checkid_map_image_base64: Optional[str] = None,
                          checkid_map_location: Optional[dict] = None,
                          checkid_cp: str = "",
                          extra_maps: Optional[list] = None) -> str:
    """Genera el reporte HTML completo de un sujeto.

    Args:
        subject_data: datos del padrón (nombre, curp, rfc, etc.)
        narrative: texto narrativo generado por AI
        enrichment: dict con datos de Tlaloc, Singula, Apify, CheckID, social_media
        map_image_base64: imagen PNG del mapa en base64 (data URI) — padrón
        map_location: dict con {lat, lon, display_name, direccion} — padrón
        checkid_map_image_base64: mapa del CP que devuelve CheckID
        checkid_map_location: ubicación del CP de CheckID
        checkid_cp: CP que devuelve CheckID (para encabezado de la sección)
        extra_maps: lista de mapas adicionales de _generate_maps_for_addresses,
            cada uno con {titulo, fuente, direccion, cp, image_b64, lat, lon,
            display_name, geocode_source, metadata}. Cada uno se muestra en su
            propia sub-sección.
    """
    enrichment = enrichment or {}
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    nombre = _esc(subject_data.get("nombre_completo") or
                  f"{subject_data.get('nombre', '')} {subject_data.get('paterno', '')} {subject_data.get('materno', '')}".strip())
    curp = _esc(subject_data.get("curp", "—"))
    rfc = _esc(subject_data.get("rfc", "—"))
    folio = _esc(subject_data.get("folio", "—"))
    seccion = _esc(subject_data.get("seccion", "—"))

    # Fuentes
    checkid = enrichment.get("checkid", {}) or {}
    tlaloc_curp = enrichment.get("tlaloc_curp", {}) or {}
    tlaloc_rfc = enrichment.get("tlaloc_rfc", {}) or {}
    fiscal = enrichment.get("fiscal", {}) or {}
    social = enrichment.get("social", {}) or {}
    social_media = enrichment.get("social_media", {}) or {}

    checkid_ok = bool(checkid.get("exitoso"))
    tlaloc_ok = bool(tlaloc_curp.get("valid"))
    fiscal_ok = bool(fiscal.get("datos_identidad")) or tlaloc_ok

    # Redes sociales: usar social_media (nuevo, automático) o fallback a social (viejo)
    sm_perfiles = social_media.get("perfiles", []) if isinstance(social_media, dict) else []
    sm_total = social_media.get("total_encontrados", 0)
    sm_exactas = social_media.get("coincidencias_exactas", len(sm_perfiles))
    sm_error = social_media.get("error", "") if isinstance(social_media, dict) else ""
    if not sm_perfiles and isinstance(social, dict):
        # Fallback al viejo formato
        sm_perfiles = social.get("redes", [])
        sm_total = len(sm_perfiles)
        sm_exactas = len(sm_perfiles)

    # ===== PÁGINA 1: PORTADA =====
    cover = f"""
<section class="cover">
  <div class="brand">
    <div class="brand-mark">NX</div>
    <div>
      <div class="brand-name">NEXO INTELLIGENCE</div>
      <div class="brand-sub">Plataforma de Verificación de Identidad · Padrón Nacional</div>
    </div>
  </div>
  <div class="doc-type">Reporte de Verificación</div>
  <h1>{nombre}</h1>
  <div class="subject">
    <span class="confidential">CONFIDENCIAL</span>
    &nbsp;&nbsp;ID interno: NEXO-{curp[:8] if curp != '—' else 'N/A'}-{datetime.now().strftime('%Y%m%d')}
  </div>
  <div class="meta-grid">
    <div><div class="k">CURP</div><div class="v">{curp}</div></div>
    <div><div class="k">RFC</div><div class="v">{rfc}</div></div>
    <div><div class="k">Folio Nacional</div><div class="v">{folio}</div></div>
    <div><div class="k">Sección Electoral</div><div class="v">{seccion}</div></div>
    <div><div class="k">Fecha de Consulta</div><div class="v">{fecha}</div></div>
    <div><div class="k">Tipo de Reporte</div><div class="v">Verificación Integral</div></div>
  </div>
  <div class="footer">
    <div>© {datetime.now().year} Nexo Intelligence · Todos los datos provienen de fuentes oficiales verificables</div>
  </div>
</section>
"""

    # ===== PÁGINA 2: RESUMEN EJECUTIVO =====
    checkid_kpi = ""
    if checkid_ok:
        checkid_kpi = f"""
  <tr><td><span class="tag checkid">CheckID (SAT/IMSS)</span></td><td><strong>Verificado</strong></td><td>RFC {checkid.get('rfc', '—')}, NSS {checkid.get('nss', '—')}, régimen {str(checkid.get('regimen_fiscal', '—'))[:40]}...</td></tr>"""

    sm_kpi = ""
    if sm_perfiles:
        sm_kpi = f"""
  <tr><td><span class="tag social">Social Media Finder</span></td><td><strong>{sm_exactas} perfiles</strong></td><td>{sm_exactas} coincidencias exactas de {sm_total} candidatos en 13 redes sociales</td></tr>"""
    elif sm_error:
        sm_kpi = f"""
  <tr><td><span class="tag social">Social Media Finder</span></td><td><strong>Error</strong></td><td>{_esc(sm_error)}</td></tr>"""
    else:
        sm_kpi = """
  <tr><td><span class="tag social">Social Media Finder</span></td><td><strong>Sin resultados</strong></td><td>No se encontraron coincidencias exactas de nombre</td></tr>"""

    summary_kpis = f"""
<table>
  <tr><th>Fuente</th><th>Estado</th><th>Detalle</th></tr>
  <tr><td><span class="tag pad">Padrón INE 2018</span></td><td><strong>Verificado</strong></td><td>{_esc(subject_data.get('nombre', '—'))} {_esc(subject_data.get('paterno', '—'))} {_esc(subject_data.get('materno', '—'))}</td></tr>
  <tr><td><span class="tag id">Tlaloc (RENAPO)</span></td><td>{'Verificado' if tlaloc_ok else 'No consultado'}</td><td>{'CURP validado contra RENAPO' if tlaloc_ok else 'Pendiente de validación'}</td></tr>
  {checkid_kpi}
  <tr><td><span class="tag fiscal">Singula (SAT)</span></td><td>{'Verificado' if fiscal.get('datos_fiscales', {}).get('rfc_singula') else 'No consultado'}</td><td>{'RFC validado ante el SAT' if fiscal.get('datos_fiscales', {}).get('rfc_singula') else 'Pendiente'}</td></tr>
  {sm_kpi}
</table>
"""

    narrative_html = f"""
<section class="page">
  <h2><span class="num">02</span> Resumen Ejecutivo</h2>
  <p class="section-intro">Síntesis narrativa generada con IA (modelo {_esc(subject_data.get('_modelo_ia', 'glm-5.2'))}) a partir de los datos recolectados en el padrón, fuentes oficiales (RENAPO/SAT/IMSS vía CheckID) y redes sociales.</p>
  <div class="narrative">
    {_nl2p(narrative) if narrative else '<p>El sujeto fue identificado en el padrón electoral y los datos base fueron recolectados. Para generar el resumen narrativo AI, ejecute primero las validaciones disponibles.</p>'}
  </div>
  <h3>Estado de las Fuentes</h3>
  {summary_kpis}
</section>
"""

    # ===== PÁGINA 3: DATOS DEL PADRÓN =====
    padron_fields = [
        ("Nombre", subject_data.get("nombre")),
        ("Apellido Paterno", subject_data.get("paterno")),
        ("Apellido Materno", subject_data.get("materno")),
        ("Fecha de Nacimiento", subject_data.get("fecnac")),
        ("Sexo", subject_data.get("sexo")),
        ("Estado", subject_data.get("estado_nombre") or subject_data.get("estado")),
        ("Municipio", subject_data.get("municipio_nombre") or subject_data.get("municipio")),
        ("Localidad", subject_data.get("localidad")),
        ("Sección", subject_data.get("seccion")),
        ("Manzana", subject_data.get("mza")),
        ("Calle", subject_data.get("calle")),
        ("Número Exterior", subject_data.get("ext")),
        ("Número Interior", subject_data.get("interior")),
        ("Colonia", subject_data.get("colonia")),
        ("Código Postal", subject_data.get("cp")),
        ("Folio Nacional", subject_data.get("folio")),
        ("Clave de Elector", subject_data.get("clave")),
        ("OCR", subject_data.get("ocr")),
        ("CIC", subject_data.get("cic")),
        ("Año de Registro", subject_data.get("anio_reg")),
    ]
    padron_rows = "".join(
        f"<tr><td class='k'>{_esc(k)}</td><td class='v'>{_esc(v)}</td></tr>"
        for k, v in padron_fields
    )

    padron_html = f"""
<section class="page">
  <h2><span class="num">03</span> Datos del Padrón Electoral <span class="badge">LOCAL</span></h2>
  <p class="section-intro">Datos obtenidos del padrón electoral federal (INE 2018), base nacional con 88,402,547 registros.</p>
  <table>
    <tr><th>Campo</th><th>Valor</th></tr>
    {padron_rows}
  </table>
</section>
"""

    # ===== PÁGINA 4: MAPA DE UBICACIÓN (SECCIÓN COMPLETA) =====
    direccion = _build_direccion(subject_data)
    map_html = ""
    if map_image_base64:
        map_display = map_location.get("display_name", "") if map_location else ""
        map_lat = map_location.get("lat", "") if map_location else ""
        map_lon = map_location.get("lon", "") if map_location else ""
        coords_str = f"{map_lat}, {map_lon}" if map_lat and map_lon else ""

        # Desglosar dirección en líneas
        addr_parts = direccion.split(", ")
        addr_lines = "".join(
            f'<div class="addr-line"><span class="addr-label">Dirección:</span><span class="addr-value">{_esc(p)}</span></div>'
            for p in addr_parts
        ) if addr_parts else f'<div class="addr-line"><span class="addr-value">{_esc(direccion)}</span></div>'

        map_html = f"""
<section class="page map-section">
  <h2><span class="num">04</span> Mapa de Ubicación <span class="badge">GEO</span></h2>
  <p class="section-intro">Geolocalización de la dirección registrada en el padrón electoral. Datos geocodificados vía OpenStreetMap/Nominatim.</p>

  <div class="map-address-block">
    {addr_lines}
    {f'<div class="map-coords">📍 Coordenadas: {_esc(coords_str)}</div>' if coords_str else ''}
  </div>

  <div class="map-container">
    <img src="data:image/png;base64,{map_image_base64}" alt="Mapa de ubicación" />
    <div class="map-caption">{_esc(map_display or direccion)}</div>
  </div>

  <h3>Nota Metodológica</h3>
  <p class="section-intro">La ubicación mostrada corresponde a la dirección registrada en el padrón electoral. La precisión depende de la calidad de los datos de dirección y de la geocodificación de OpenStreetMap. Pueden existir variaciones de hasta 50 metros en zonas urbanas densas o rurales con nomenclatura no estandarizada.</p>
</section>
"""
    else:
        map_html = f"""
<section class="page map-section">
  <h2><span class="num">04</span> Mapa de Ubicación <span class="badge">GEO</span></h2>
  <p class="section-intro">Geolocalización de la dirección registrada en el padrón electoral.</p>
  <div class="map-address-block">
    <div class="addr-line"><span class="addr-label">Dirección:</span><span class="addr-value">{_esc(direccion)}</span></div>
  </div>
  <div class="alert alert-info"><strong>ℹ</strong> No se pudo generar el mapa estático. La dirección puede estar incompleta o no ser geocodificable.</div>
</section>
"""

    # ===== PÁGINA 4B: MAPA CP CHECKID (si difiere del padrón) =====
    padron_cp = str(subject_data.get("cp", "")).strip().zfill(5)[:5]
    checkid_map_html = ""
    if checkid_map_image_base64 and checkid_cp and checkid_cp != padron_cp:
        chk_display = checkid_map_location.get("display_name", "") if checkid_map_location else ""
        chk_lat = checkid_map_location.get("lat", "") if checkid_map_location else ""
        chk_lon = checkid_map_location.get("lon", "") if checkid_map_location else ""
        coords_str = f"{chk_lat}, {chk_lon}" if chk_lat and chk_lon else ""
        checkid_map_html = f"""
<section class="page map-section">
  <h2><span class="num">04b</span> Mapa CP CheckID <span class="badge">CHECKID</span></h2>
  <p class="section-intro">El código postal devuelto por CheckID (SAT) difiere del registrado en el padrón electoral. Esto puede indicar un cambio de domicilio fiscal, una dirección declarada distinta para efectos fiscales, o una discrepancia entre fuentes.</p>
  <div class="map-address-block">
    <div class="addr-line"><span class="addr-label">CP Padrón:</span><span class="addr-value">{_esc(padron_cp or "—")}</span></div>
    <div class="addr-line"><span class="addr-label">CP CheckID (SAT):</span><span class="addr-value"><strong>{_esc(checkid_cp)}</strong></span></div>
    {f'<div class="map-coords">📍 Coordenadas: {_esc(coords_str)}</div>' if coords_str else ''}
  </div>
  <div class="map-container">
    <img src="data:image/png;base64,{checkid_map_image_base64}" alt="Mapa CP CheckID" />
    <div class="map-caption">{_esc(chk_display or f"CP {checkid_cp}")}</div>
  </div>
  <h3>Nota Metodológica</h3>
  <p class="section-intro">Esta ubicación corresponde al código postal devuelto por CheckID/SAT, geocodificado vía OpenStreetMap. La diferencia con el CP del padrón puede ser relevante para validación fiscal, laboral o de cumplimiento normativo.</p>
</section>
"""
    elif checkid_cp and checkid_cp == padron_cp and checkid_map_image_base64:
        # Si los CPs son iguales pero tenemos mapa, mostrar una nota
        checkid_map_html = f"""
<section class="page map-section">
  <h2><span class="num">04b</span> Verificación CP CheckID <span class="badge">CHECKID</span></h2>
  <p class="section-intro">El código postal devuelto por CheckID/SAT coincide con el del padrón electoral — sin discrepancias geográficas.</p>
  <div class="map-address-block">
    <div class="addr-line"><span class="addr-label">CP Padrón:</span><span class="addr-value">{_esc(padron_cp)}</span></div>
    <div class="addr-line"><span class="addr-label">CP CheckID:</span><span class="addr-value">{_esc(checkid_cp)}</span></div>
  </div>
  <div class="alert alert-ok"><strong>✓</strong> Coincidencia: padrón y SAT reportan el mismo CP.</div>
</section>
"""
    elif checkid_cp and not checkid_map_image_base64:
        # Tenemos CP de CheckID pero no se pudo geocodificar
        checkid_map_html = f"""
<section class="page map-section">
  <h2><span class="num">04b</span> Verificación CP CheckID <span class="badge">CHECKID</span></h2>
  <p class="section-intro">El código postal devuelto por CheckID/SAT difiere del registrado en el padrón.</p>
  <div class="map-address-block">
    <div class="addr-line"><span class="addr-label">CP Padrón:</span><span class="addr-value">{_esc(padron_cp or "—")}</span></div>
    <div class="addr-line"><span class="addr-label">CP CheckID (SAT):</span><span class="addr-value"><strong>{_esc(checkid_cp)}</strong></span></div>
  </div>
  <div class="alert alert-warn"><strong>⚠</strong> Discrepancia de CP detectada, pero no se pudo geocodificar el CP de CheckID para mostrar el mapa.</div>
</section>
"""

    # ===== PÁGINA 04c: MAPAS DE DOMICILIOS EN OTRAS BASES (2026-08-13) =====
    # Cada dirección recolectada (CFE, ATT, TELCEL, IMSS, REPUVE, empleadores, sepomex,
    # checkid CP si difiere, etc.) se muestra en su propia sub-sección con mapa.
    # Sin límite en cantidad salvo el cap del caller (default 25).
    extra_maps_html = ""
    if extra_maps:
        # Agrupar por fuente para encabezado
        fuentes = {}
        for m in extra_maps:
            fuentes.setdefault(m.get("fuente", "?"), []).append(m)

        # Resumen por fuente (un mapa cada una)
        resumen_parts = []
        for fuente, lst in fuentes.items():
            resumen_parts.append(
                f'<span class="badge fuente-{fuente}">{_esc(fuente.upper())}</span> '
                f'<strong>{len(lst)}</strong>'
            )
        resumen_html = " · ".join(resumen_parts)

        # Cada mapa individual
        maps_blocks = []
        for i, m in enumerate(extra_maps, 1):
            titulo = _esc(m.get("titulo", f"Domicilio {i}"))
            fuente = m.get("fuente", "?")
            direccion = _esc(m.get("direccion", ""))
            cp = m.get("cp", "")
            img_b64 = m.get("image_b64")
            lat = m.get("lat", "")
            lon = m.get("lon", "")
            display = _esc(m.get("display_name", "")[:120])
            geocode_src = m.get("geocode_source", "?")
            meta = m.get("metadata", {}) or {}
            coords_str = f"{lat}, {lon}" if lat and lon else ""

            # Metadata: mostrar lo más relevante por fuente
            meta_html = ""
            if meta:
                items = []
                for k, v in meta.items():
                    if v and str(v).strip():
                        items.append(f"<li><strong>{_esc(k)}:</strong> {_esc(str(v)[:80])}</li>")
                if items:
                    meta_html = "<ul class='map-meta'>" + "".join(items[:8]) + "</ul>"

            # Imagen o fallback CFE
            cfe_fallback = m.get("cfe_fallback") or {}
            cfe_fallback_html = ""
            if img_b64:
                img_html = f'<img src="data:image/png;base64,{img_b64}" alt="{titulo}" />'
            elif cfe_fallback and cfe_fallback.get("count", 0) > 0:
                # 2026-08-13: Nominatim no geocodificó pero CFE fuzzy match sí.
                # Mostrar tabla de medidores como "mapa CFE" textual.
                rows = cfe_fallback.get("rows", [])[:10]
                row_htmls = []
                for r in rows:
                    ns = _esc(r.get("num_servicio", ""))
                    nom = _esc((r.get("nombre") or "")[:40])
                    div = _esc(r.get("division", ""))
                    rcp = _esc(r.get("cp", ""))
                    zon = _esc(f"{r.get('zona_cod', '')} {r.get('zona_nom', '')}".strip())
                    col = _esc((r.get("colonia") or "")[:30])
                    row_htmls.append(
                        f"<tr><td>{ns}</td><td>{nom}</td><td>{div}</td>"
                        f"<td>{rcp}</td><td>{col}</td><td>{zon}</td></tr>"
                    )
                cfe_fallback_html = f"""
  <div class="alert alert-info">
    <strong>📋 Integración pasada (CFE por domicilio):</strong> Nominatim no
    geocodificó esta dirección, pero el padrón CFE tiene
    <strong>{cfe_fallback.get('count', 0)}</strong> medidor(es) coincidentes
    (búsqueda fuzzy en <em>api.cfe_medidor</em>).
    <table class="cfe-fallback-table">
      <thead><tr>
        <th>num_servicio</th><th>nombre</th><th>division</th>
        <th>cp</th><th>colonia</th><th>zona</th>
      </tr></thead>
      <tbody>{''.join(row_htmls)}</tbody>
    </table>
    <p class="hint">Query: calle={_esc(str(cfe_fallback.get('query', {}).get('calle', ''))[:40])},
       cp={_esc(str(cfe_fallback.get('query', {}).get('cp', '')))},
       elapsed={cfe_fallback.get('elapsed_s', '?')}s</p>
  </div>
"""
                img_html = ""  # no hay imagen PNG
            else:
                img_html = '<div class="alert alert-warn"><strong>⚠</strong> No se pudo geocodificar (Nominatim falló) ni buscar en CFE (sin calle/colonia parseable).</div>'

            src_badge = f'<span class="source-badge source-{geocode_src}">{_esc(geocode_src)}</span>' if geocode_src else ""

            maps_blocks.append(f"""
<section class="page map-section">
  <h2><span class="num">04c.{i:02d}</span> {_esc(titulo)} <span class="badge fuente-tag">{_esc(fuente.upper())}</span></h2>
  <div class="map-address-block">
    <div class="addr-line"><span class="addr-label">Dirección:</span><span class="addr-value">{direccion}</span></div>
    {f'<div class="addr-line"><span class="addr-label">CP:</span><span class="addr-value">{_esc(cp)}</span></div>' if cp else ''}
    {f'<div class="map-coords">📍 Coordenadas: {_esc(coords_str)} {src_badge}</div>' if coords_str else ''}
  </div>
  {meta_html}
  <div class="map-container">
    {img_html}
    {cfe_fallback_html}
    {f'<div class="map-caption">{display}</div>' if display else ''}
  </div>
</section>
""")

        # Encabezado general de la sección
        intro_count = f"{len(extra_maps)} mapa(s) individual(es) generado(s)"
        intro_disclaimer = (
            "Cada mapa corresponde a una dirección distinta encontrada en las bases "
            "de datos. Las direcciones pueden repetirse entre fuentes (la misma calle "
            "puede aparecer en CFE, ATT y TELCEL). Se muestran todas para auditoría."
        )

        extra_maps_html = f"""
<section class="page map-section">
  <h2><span class="num">04c</span> Mapas de Domicilios — Todas las Bases</h2>
  <p class="section-intro">{intro_count}: {resumen_html}</p>
  <p class="section-intro">{intro_disclaimer}</p>
</section>
{''.join(maps_blocks)}
"""

    # ===== PÁGINA 5: IDENTIDAD OFICIAL (TLALOC) =====
    datos_id = fiscal.get("datos_identidad", {}) if isinstance(fiscal, dict) else {}
    doc_prob = datos_id.get("doc_probatorio", {}) if isinstance(datos_id.get("doc_probatorio"), dict) else {}

    tlaloc_rows = ""
    if datos_id and not datos_id.get("error"):
        tlaloc_fields = [
            ("Nombres", datos_id.get("nombres")),
            ("Apellido Paterno", datos_id.get("apellido_paterno")),
            ("Apellido Materno", datos_id.get("apellido_materno")),
            ("Sexo", datos_id.get("sexo")),
            ("Fecha de Nacimiento", datos_id.get("fecha_nacimiento")),
            ("Nacionalidad", datos_id.get("nacionalidad")),
            ("Entidad de Registro", datos_id.get("entidad")),
            ("Clave de Entidad", datos_id.get("clave_entidad")),
            ("Estatus CURP", datos_id.get("status_curp")),
            ("Año de Registro", doc_prob.get("anioReg")),
            ("Entidad de Registro (Doc)", doc_prob.get("entidadRegistro")),
            ("Municipio de Registro", doc_prob.get("municipioRegistro")),
            ("Número de Acta", doc_prob.get("numActa")),
        ]
        tlaloc_rows = "".join(
            f"<tr><td class='k'>{_esc(k)}</td><td class='v'>{_esc(v)}</td></tr>"
            for k, v in tlaloc_fields
        )

    tlaloc_alert = ""
    if tlaloc_curp and tlaloc_curp.get("valid"):
        tlaloc_alert = '<div class="alert alert-ok"><strong>✓</strong> CURP validado correctamente contra RENAPO.</div>'
    elif tlaloc_curp and tlaloc_curp.get("error"):
        tlaloc_alert = f'<div class="alert alert-err"><strong>✗</strong> Error: {_esc(tlaloc_curp.get("error", "desconocido"))}</div>'
    else:
        tlaloc_alert = '<div class="alert alert-info"><strong>ℹ</strong> No se ha ejecutado la validación con Tlaloc.</div>'

    tlaloc_html = f"""
<section class="page">
  <h2><span class="num">05</span> Identidad Oficial (RENAPO) <span class="badge">TLALOC</span></h2>
  <p class="section-intro">Validación de identidad contra el Registro Nacional de Población (RENAPO) vía API Tlaloc.</p>
  {tlaloc_alert}
  <h3>Datos Verificados</h3>
  <table>
    <tr><th>Campo</th><th>Valor</th></tr>
    {tlaloc_rows or "<tr><td colspan='2' class='k'>Sin datos — ejecute la validación Tlaloc primero</td></tr>"}
  </table>
</section>
"""

    # ===== PÁGINA 6: SITUACIÓN FISCAL Y LABORAL (CHECKID) =====
    checkid_alert = ""
    if checkid_ok:
        checkid_alert = f'<div class="alert alert-ok"><strong>✓</strong> Datos fiscales y laborales obtenidos vía CheckID: RFC, NSS, régimen fiscal, código postal y estado en listas 69/69B.</div>'
    elif checkid.get("error"):
        checkid_alert = f'<div class="alert alert-err"><strong>✗</strong> Error CheckID: {_esc(checkid.get("error", "desconocido"))}</div>'
    else:
        checkid_alert = '<div class="alert alert-info"><strong>ℹ</strong> No se ha ejecutado la consulta con CheckID.</div>'

    checkid_rows = ""
    if checkid_ok:
        e69 = checkid.get("estado_69_69b", {}) or {}
        # estado_69_69b puede venir como string (legacy) o dict (nuevo)
        if not isinstance(e69, dict):
            e69 = {"con_problema": e69 if e69 and e69 != "OK" else None}
        con_problema = e69.get("con_problema")
        if con_problema is None:
            con_problema = e69.get("conProblema")
        checkid_fields = [
            ("RFC", checkid.get("rfc")),
            ("Razón social", checkid.get("razon_social")),
            ("NSS", checkid.get("nss")),
            ("Email fiscal", checkid.get("email")),
            ("Código postal", checkid.get("codigo_postal")),
            ("Régimen fiscal", checkid.get("regimen_fiscal")),
            ("69/69B con problema", "Sí" if con_problema else "No" if con_problema is False else "—"),
        ]
        checkid_rows = "".join(
            f"<tr><td class='k'>{_esc(k)}</td><td class='v'>{_esc(v)}</td></tr>"
            for k, v in checkid_fields
        )
        detalles = e69.get("detalles", {})
        if detalles:
            problemas = detalles.get("problemas", [])
            if problemas:
                for p in problemas:
                    checkid_rows += f"<tr><td class='k'>Problema 69/69B</td><td class='v'>{_esc(p.get('descripcion', '—'))} ({_esc(p.get('fechaActualizacion', '—'))})</td></tr>"
            sit = detalles.get("situacionContribuyente")
            sta = detalles.get("statusContribuyente")
            if sit:
                checkid_rows += f"<tr><td class='k'>Situación contribuyente</td><td class='v'>{_esc(sit)}</td></tr>"
            if sta:
                checkid_rows += f"<tr><td class='k'>Status contribuyente</td><td class='v'>{_esc(sta)}</td></tr>"

    checkid_html = f"""
<section class="page">
  <h2><span class="num">06</span> Situación Fiscal y Laboral (SAT/IMSS) <span class="badge">CHECKID</span></h2>
  <p class="section-intro">Consulta fiscal y laboral vía CheckID (Canicalabs): RFC, NSS, código postal, régimen fiscal y estatus en listas 69 y 69B del SAT.</p>
  {checkid_alert}
  <h3>Datos Fiscales y Laborales</h3>
  <table>
    <tr><th>Campo</th><th>Valor</th></tr>
    {checkid_rows or "<tr><td colspan='2' class='k'>Sin datos — ejecute la validación CheckID primero</td></tr>"}
  </table>
</section>
"""

    # ===== PÁGINA 6b: ISSSTE — Padrón de empleados del sector público federal =====
    # Búsqueda por nombre+paterno+materno en api.issste_empleado (2.7M filas).
    # Solo aplica si el subject tiene nombre + paterno + materno (no RFC/CURP).
    issste_data = enrichment.get("issste", {}) if isinstance(enrichment, dict) else {}
    issste_rows_html = ""
    issste_alert = ""
    issste_ai_html = ""
    issste_count = 0
    issste_total_sueldo = 0
    issste_avg_sueldo = 0
    if issste_data.get("matches"):
        issste_count = len(issste_data["matches"])
        for m in issste_data["matches"][:25]:
            sueldo = m.get("sueldo", 0) or 0
            issste_total_sueldo += float(sueldo)
            ai_score = m.get("ai_score")
            ai_razon = m.get("ai_razon", "")
            ai_badge = ""
            if isinstance(ai_score, (int, float)):
                color = "#16a34a" if ai_score >= 0.70 else "#f59e0b" if ai_score >= 0.40 else "#94a3b8"
                ai_badge = f' <span style="background:{color};color:#000;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:600">score={ai_score:.2f}</span>'
                if ai_razon:
                    ai_badge += f' <span style="color:var(--muted);font-size:10px;font-style:italic">{_esc(ai_razon)}</span>'
            issste_rows_html += f"""
    <tr>
      <td>{_esc(m.get('paterno', ''))} {_esc(m.get('materno', ''))}</td>
      <td>{_esc(m.get('nombres', ''))}{ai_badge}</td>
      <td>{_esc(m.get('cargo', ''))}</td>
      <td>{_esc(m.get('sexo', ''))}</td>
      <td>${float(sueldo):,.2f}</td>
      <td>{_esc((m.get('ramo') or '')[:40])}</td>
      <td>{_esc(m.get('entidad', ''))}</td>
      <td>{_esc(m.get('sector', ''))}</td>
    </tr>"""
        if issste_count > 0:
            issste_avg_sueldo = issste_total_sueldo / issste_count

        # Análisis IA si está disponible
        ai = issste_data.get("ai_analysis") or {}
        if ai and not ai.get("skipped"):
            conf = ai.get("confianza_global", "sin_datos")
            conf_color = {"alta": "#16a34a", "media": "#f59e0b",
                          "baja": "#ef4444"}.get(conf, "#94a3b8")
            resumen = ai.get("resumen", "")
            issste_ai_html = f"""
  <div class="alert alert-ai" style="border-left:4px solid {conf_color}">
    <strong>🤖 Análisis IA:</strong> <span style="background:{conf_color};color:#000;padding:2px 8px;border-radius:3px;font-size:11px">confianza: {conf.upper()}</span><br>
    <span style="font-size:13px">{_esc(resumen)}</span>
  </div>"""
            # Cambiar el alert principal para reflejar el filtrado IA
            n_match = sum(1 for m in issste_data["matches"] if m.get("ai_match"))
            issste_alert = (
                f'<div class="alert alert-ok"><strong>✓</strong> '
                f'IA identificó <strong>{n_match}</strong> de {issste_count} matches '
                f'como plausibles del sujeto ({issste_avg_sueldo:,.2f} MXN sueldo promedio).</div>'
                if n_match > 0 else
                f'<div class="alert alert-warn"><strong>⚠</strong> '
                f'IA revisó {issste_count} matches ISSSTE — ninguno parece corresponder '
                f'al sujeto ({issste_avg_sueldo:,.2f} MXN sueldo promedio).</div>'
            )
        else:
            issste_alert = (
                f'<div class="alert alert-ok"><strong>✓</strong> '
                f'Encontrado(s) {issste_count} registro(s) en padrón ISSSTE '
                f'({issste_avg_sueldo:,.2f} MXN sueldo promedio).</div>'
                if issste_count == 1 else
                f'<div class="alert alert-ok"><strong>✓</strong> '
                f'Encontrados {issste_count} registros en padrón ISSSTE '
                f'(${issste_avg_sueldo:,.2f} MXN sueldo promedio).</div>'
            )
    elif issste_data.get("error"):
        issste_alert = f'<div class="alert alert-warn"><strong>⚠</strong> {_esc(issste_data["error"])}</div>'
    else:
        issste_alert = '<div class="alert alert-info"><strong>ℹ</strong> No se ha ejecutado la búsqueda en padrón ISSSTE.</div>'

    issste_html = f"""
<section class="page">
  <h2><span class="num">06b</span> Padrón ISSSTE (Empleados Federales) <span class="badge">ISSSTE</span></h2>
  <p class="section-intro">Búsqueda en padrón del Instituto de Seguridad y Servicios Sociales de los Trabajadores del Estado (2.7M empleados). Búsqueda por nombre + paterno + materno (sin RFC/CURP).</p>
  {issste_alert}
  {issste_ai_html}
  <h3>Coincidencias</h3>
  <table>
    <tr>
      <th>Apellidos</th><th>Nombre</th><th>Cargo</th><th>Sexo</th>
      <th>Sueldo</th><th>Ramo</th><th>Entidad</th><th>Sector</th>
    </tr>
    {issste_rows_html or "<tr><td colspan='8' class='k'>Sin coincidencias en padrón ISSSTE</td></tr>"}
  </table>
</section>
"""

    # ===== PÁGINA 7: SITUACIÓN FISCAL ADICIONAL (SINGULA) =====
    # Soporta dos formatos de datos:
    #   - legacy: fiscal.datos_fiscales.rfc_singula (viejo formato)
    #   - nuevo: enrichment.singula.{customer_id, validations, enrichment}
    singula_rfc = fiscal.get("datos_fiscales", {}).get("rfc_singula", {}) if isinstance(fiscal, dict) else {}
    singula_data = enrichment.get("singula", {}) if isinstance(enrichment, dict) else {}
    singula_validations = singula_data.get("validations", {}) if isinstance(singula_data, dict) else {}
    singula_enrichment = singula_data.get("enrichment", {}) if isinstance(singula_data, dict) else {}

    singula_alert = ""
    if singula_rfc and not singula_rfc.get("error"):
        singula_alert = f'<div class="alert alert-ok"><strong>✓</strong> RFC validado: {_esc(singula_rfc.get("message", "OK"))}</div>'
    elif singula_rfc.get("error"):
        singula_alert = f'<div class="alert alert-err"><strong>✗</strong> Error: {_esc(singula_rfc.get("error", "desconocido"))}</div>'
    elif singula_data.get("enrich_ok"):
        singula_alert = f'<div class="alert alert-ok"><strong>✓</strong> Perfil Singula completo: customer {_esc(singula_data.get("customer_id", "—"))}, enriquecido con {len(singula_data.get("enrich_sources", []))} fuentes (padrón, checkid, tlaloc, sepomex, apify).</div>'
    elif singula_data.get("enrich_error"):
        singula_alert = f'<div class="alert alert-err"><strong>✗</strong> Error Singula: {_esc(singula_data.get("enrich_error", "desconocido"))}</div>'
    else:
        singula_alert = '<div class="alert alert-info"><strong>ℹ</strong> No se ha ejecutado la validación con Singula.</div>'

    singula_rows = ""
    if singula_rfc and not singula_rfc.get("error"):
        for k, v in singula_rfc.items():
            singula_rows += f"<tr><td class='k'>{_esc(k)}</td><td class='v'>{_esc(v)}</td></tr>"

    # Sección de perfil enriquecido
    singula_profile_rows = ""
    if singula_enrichment:
        for source_key, source_label in [
            ("padron", "Padrón electoral"),
            ("sepomex", "Dirección SEPOMEX"),
            ("checkid", "Datos fiscales CheckID"),
            ("tlaloc", "Validación RENAPO + SAT"),
            ("apify_social", "Redes sociales"),
        ]:
            section = singula_enrichment.get(source_key)
            if not section:
                continue
            singula_profile_rows += f"<tr><td class='k' colspan='2'><strong>{_esc(source_label)}</strong></td></tr>"
            if isinstance(section, dict):
                for k, v in section.items():
                    if isinstance(v, (str, int, float, bool)):
                        singula_profile_rows += f"<tr><td class='k'>{_esc(k)}</td><td class='v'>{_esc(v)}</td></tr>"
                    else:
                        singula_profile_rows += f"<tr><td class='k'>{_esc(k)}</td><td class='v'><pre>{_esc(json.dumps(v, default=str, ensure_ascii=False)[:500])}</pre></td></tr>"

    # Sección de validaciones
    singula_validations_html = ""
    if singula_validations and isinstance(singula_validations, dict):
        from_cache = singula_data.get("validations_from_cache", {})
        executed = singula_data.get("validations_executed", [])
        cache_persist_error = singula_data.get("validations_error", "")

        singula_validations_html = f"""
<h3>Validaciones Singula</h3>
<div class="kpi-row">
  <div class="kpi"><div class="kpi-k">Customer ID</div><div class="kpi-v" style="font-size:11pt">{_esc(singula_data.get("customer_id", "—"))}</div></div>
  <div class="kpi"><div class="kpi-k">Reusadas del cache</div><div class="kpi-v">{len([k for k, v in (from_cache or {}).items() if v])}/4</div></div>
  <div class="kpi"><div class="kpi-k">Ejecutadas nuevas</div><div class="kpi-v">{len(executed or [])}/4</div></div>
</div>
"""
        # 1) Antecedentes judiciales
        jud = singula_validations.get("judicial", {})
        if jud:
            jud_status = _esc(jud.get("status", "—"))
            jud_risk = _esc(jud.get("risk_level", "—"))
            jud_total = jud.get("total_records", 0)
            jud_summary = _esc(jud.get("summary", "—"))
            jud_sources = ", ".join(jud.get("sources_checked", []) or [])
            cache_tag = '<span class="badge" style="background:#fef3c7;color:#92400e;font-size:9pt">CACHE</span>' if from_cache.get("judicial") else ""
            jud_alert_class = "alert-ok" if jud_status == "clean" else "alert-warn" if jud_status else "alert-info"
            singula_validations_html += f"""
<h4>1. Antecedentes Judiciales {cache_tag}</h4>
<div class="alert {jud_alert_class}">
  <strong>Estatus:</strong> {jud_status} ·
  <strong>Risk level:</strong> {jud_risk} ·
  <strong>Registros:</strong> {jud_total} ·
  <strong>Fuentes:</strong> {_esc(jud_sources)}
</div>
<p class="section-intro">{jud_summary}</p>
"""

        # 2) Email lookup
        email_lookup = singula_validations.get("email_lookup", {})
        if email_lookup and not email_lookup.get("skipped"):
            cache_tag = '<span class="badge" style="background:#fef3c7;color:#92400e;font-size:9pt">CACHE</span>' if from_cache.get("email_lookup") else ""
            total_found = email_lookup.get("total_found", 0)
            total_checked = email_lookup.get("total_checked", 0)
            email_value = _esc(email_lookup.get("email", "—"))
            platforms = email_lookup.get("platforms", []) or []
            platforms_html = ""
            if platforms:
                rows = "".join(
                    f"<tr><td class='k'>{_esc(p.get('name', '?'))}</td><td class='v'>{'✓' if p.get('exists') else '✗'}</td></tr>"
                    for p in platforms
                )
                platforms_html = f"<table><tr><th>Plataforma</th><th>Existe</th></tr>{rows}</table>"
            el_alert_class = "alert-ok" if total_found > 0 else "alert-info"
            singula_validations_html += f"""
<h4>2. Email Lookup {cache_tag}</h4>
<div class="alert {el_alert_class}">
  <strong>Email:</strong> {email_value} ·
  <strong>Plataformas con cuenta:</strong> {total_found}/{total_checked}
</div>
{platforms_html}
"""
        elif email_lookup.get("skipped"):
            singula_validations_html += f"""
<h4>2. Email Lookup</h4>
<div class="alert alert-info"><strong>ℹ</strong> {_esc(email_lookup.get('skipped', 'sin email'))}</div>
"""

        # 3) Blacklist / PEP
        bl = singula_validations.get("blacklist", {})
        if bl:
            cache_tag = '<span class="badge" style="background:#fef3c7;color:#92400e;font-size:9pt">CACHE</span>' if from_cache.get("blacklist") else ""
            hit = bl.get("hit", False)
            total = bl.get("total", 0)
            risk = _esc(bl.get("risk_level", "—"))
            bl_alert_class = "alert-err" if hit else "alert-ok"
            hit_text = f"<strong>⚠ MATCH</strong> {total} coincidencias" if hit else "Sin coincidencias"
            singula_validations_html += f"""
<h4>3. Blacklist / PEP {cache_tag}</h4>
<div class="alert {bl_alert_class}">
  {hit_text} · <strong>Risk level:</strong> {risk}
</div>
"""

        # 4) Intel básico
        intel = singula_validations.get("intel_basic", {})
        if intel and not intel.get("error"):
            cache_tag = '<span class="badge" style="background:#fef3c7;color:#92400e;font-size:9pt">CACHE</span>' if from_cache.get("intel_basic") else ""
            tier = _esc(intel.get("tier", "—"))
            summary = _esc(intel.get("summary") or "Sin resumen")
            linkedin = intel.get("linkedin") or {}
            work = intel.get("work_history", []) or []
            edu = intel.get("education", []) or []
            web_results = intel.get("web_results", []) or []
            digital_footprint = _esc(intel.get("digital_footprint", "—"))

            linkedin_html = ""
            if isinstance(linkedin, dict) and linkedin.get("url"):
                linkedin_html = f"""
<div class="alert alert-info">
  <strong>LinkedIn:</strong> <a href="{_esc(linkedin.get('url', '#'))}">{_esc(linkedin.get('url', ''))}</a><br>
  {f'<strong>Título:</strong> {_esc(linkedin.get("title", ""))}' if linkedin.get("title") else ''}
  {f'<br><strong>Empresa:</strong> {_esc(linkedin.get("company", ""))}' if linkedin.get("company") else ''}
</div>
"""
            work_html = ""
            if work:
                rows = "".join(
                    f"<tr><td class='k'>{_esc(w.get('company', '—'))}</td><td class='v'>{_esc(w.get('title', '—'))}<br><small>{_esc(w.get('period', ''))}</small></td></tr>"
                    for w in work[:5]
                )
                work_html = f"<table><tr><th>Empresa</th><th>Puesto / Periodo</th></tr>{rows}</table>"

            web_html = ""
            if web_results:
                items = "".join(
                    f'<li><a href="{_esc(w.get("url", "#"))}">{_esc(w.get("title", "—"))}</a><br><small>{_esc(w.get("snippet", "")[:200])}</small></li>'
                    for w in web_results[:5]
                )
                web_html = f"<ul>{items}</ul>"

            singula_validations_html += f"""
<h4>4. Inteligencia Digital Básica {cache_tag}</h4>
<div class="alert alert-info">
  <strong>Tier:</strong> {tier} ·
  <strong>Huella digital:</strong> {digital_footprint}
</div>
{linkedin_html}
{f'<h5>Historial laboral</h5>{work_html}' if work_html else ''}
{f'<h5>Resultados web</h5>{web_html}' if web_html else ''}
<p class="section-intro">{summary}</p>
"""

    # HTML de tabla legacy de RFC (separado para evitar backslash en f-string)
    if singula_rows:
        legacy_rfc_html = f'<h3>Datos del RFC (legacy)</h3><table><tr><th>Campo</th><th>Valor</th></tr>{singula_rows}</table>'
    else:
        legacy_rfc_html = ''

    singula_html = f"""
<section class="page">
  <h2><span class="num">07</span> Validaciones Singula <span class="badge">SINGULA</span></h2>
  <p class="section-intro">Customer-centric de Singula: RFC validado, antecedentes judiciales, email lookup, blacklist/PEP e inteligencia digital básica. El perfil es creado/reusado por CURP y enriquecido automáticamente con datos del padrón electoral, CheckID, Tlaloc y SEPOMEX.</p>
  {singula_alert}
  {f'<h3>Perfil del Customer</h3><table><tr><th>Campo</th><th>Valor</th></tr>{singula_profile_rows}</table>' if singula_profile_rows else ''}
  {singula_validations_html}
  {legacy_rfc_html}
</section>
"""

    # ===== PÁGINA 8: REDES SOCIALES (SOCIAL MEDIA FINDER) =====
    sm_alert = ""
    if sm_error:
        sm_alert = f'<div class="alert alert-err"><strong>✗</strong> Error: {_esc(sm_error)}</div>'
    elif sm_perfiles:
        sm_alert = f'<div class="alert alert-ok"><strong>✓</strong> Se encontraron {sm_exactas} coincidencias exactas de nombre de {sm_total} candidatos revisados en 13 redes sociales (Instagram, Facebook, LinkedIn, TikTok, Twitter/X, YouTube, Twitch, Medium, Pinterest, Snapchat, Reddit, GitHub, Spotify).</div>'
    else:
        sm_alert = f'<div class="alert alert-info"><strong>ℹ</strong> No se encontraron coincidencias exactas de nombre. Se revisaron {sm_total} candidatos en 13 redes sociales.</div>'

    sm_kpi_row = ""
    if sm_total:
        sm_kpi_row = f"""
<div class="kpi-row">
  <div class="kpi"><div class="kpi-k">Candidatos revisados</div><div class="kpi-v">{sm_total}</div></div>
  <div class="kpi amber"><div class="kpi-k">Coincidencias exactas</div><div class="kpi-v">{sm_exactas}</div></div>
  <div class="kpi"><div class="kpi-k">Redes consultadas</div><div class="kpi-v">13</div></div>
</div>
"""

    redes_html = ""
    if sm_perfiles:
        for perfil in sm_perfiles[:20]:
            plataforma = _esc(perfil.get("platform") or perfil.get("_search_name") or "?")
            url = _esc(perfil.get("url") or perfil.get("profileUrl") or perfil.get("link") or "")
            display = _esc(perfil.get("displayName") or perfil.get("name") or perfil.get("fullName") or perfil.get("title") or "")
            username = _esc(perfil.get("username") or perfil.get("handle") or "")
            bio = _esc((perfil.get("bio") or perfil.get("description") or perfil.get("biography") or "")[:150])
            followers = perfil.get("followers") or perfil.get("followerCount") or perfil.get("followersCount") or ""
            verified = " ✓" if (perfil.get("verified") or perfil.get("isVerified")) else ""

            redes_html += f"""
<div class="network-card">
  <div class="plat">{plataforma}{verified}</div>
  {f'<a class="url" href="{url}">{url}</a>' if url else ''}
  {f'<div class="name">{display}</div>' if display else ''}
  {f'<div class="name">@{username}</div>' if username else ''}
  {f'<div class="bio">{bio}</div>' if bio else ''}
  {f'<div class="followers">{followers} seguidores</div>' if followers else ''}
</div>
"""
        redes_html = f'<div class="network-grid">{redes_html}</div>'
    else:
        redes_html = '<div class="alert alert-info"><strong>ℹ</strong> No se encontraron perfiles con coincidencia exacta de nombre. Solo se muestran perfiles cuyo nombre mostrado coincide exactamente con el nombre registrado en el padrón.</div>'

    social_html = f"""
<section class="page">
  <h2><span class="num">08</span> Presencia en Redes Sociales <span class="badge">SOCIAL MEDIA FINDER</span></h2>
  <p class="section-intro">Búsqueda automática en 13 plataformas sociales (Instagram, Facebook, LinkedIn, TikTok, Twitter/X, YouTube, Twitch, Medium, Pinterest, Snapchat, Reddit, GitHub, Spotify) vía tri_angle/social-media-finder en Apify. Solo se incluyen perfiles cuyo nombre mostrado coincide <strong>exactamente</strong> con el nombre completo registrado en el padrón electoral.</p>
  {sm_alert}
  {sm_kpi_row}
  {redes_html}
  <h3>Nota Metodológica</h3>
  <p class="section-intro">La búsqueda se realiza por nombre completo. El filtro de coincidencia exacta compara el nombre mostrado del perfil (displayName, name, fullName, title) contra el nombre del sujeto en el padrón, ignorando mayúsculas/minúsculas y acentos. Un perfil que aparece aquí tiene alta probabilidad de pertenecer a la entidad buscada, pero no constituye una verificación definitiva de identidad.</p>
</section>
"""

    # ===== PÁGINA 9: OBSERVACIONES Y CONCLUSIONES =====
    obs = []
    if tlaloc_ok and tlaloc_curp.get("nombres"):
        nombres_padron = f"{subject_data.get('nombre', '')} {subject_data.get('paterno', '')}".upper().strip()
        nombres_renapo = f"{tlaloc_curp.get('nombres', '')} {tlaloc_curp.get('primerApellido', '')}".upper().strip()
        if nombres_padron and nombres_renapo and nombres_padron != nombres_renapo:
            obs.append(f"<strong>Inconsistencia de nombre:</strong> Padrón reporta «{_esc(nombres_padron)}» pero RENAPO devuelve «{_esc(nombres_renapo)}».")

    if subject_data.get("fecnac") and tlaloc_curp.get("fechaNacimiento"):
        if str(subject_data.get("fecnac")) != str(tlaloc_curp.get("fechaNacimiento")):
            obs.append(f"<strong>Inconsistencia de fecha de nacimiento:</strong> Padrón «{_esc(subject_data.get('fecnac'))}» vs RENAPO «{_esc(tlaloc_curp.get('fechaNacimiento'))}».")

    if checkid_ok:
        e69 = checkid.get("estado_69_69b", {}) or {}
        if not isinstance(e69, dict):
            e69 = {"con_problema": e69 if e69 and e69 != "OK" else None}
        con_problema = e69.get("con_problema")
        if con_problema is None:
            con_problema = e69.get("conProblema")
        if con_problema:
            obs.append("<strong>Alerta fiscal:</strong> El sujeto aparece con problemas en listas 69/69B del SAT según CheckID.")
        if checkid.get("email") and subject_data.get("email_checkid"):
            obs.append(f"<strong>Email fiscal identificado:</strong> {_esc(checkid.get('email'))} (proviene de datos del SAT/IMSS).")

    if sm_perfiles:
        platforms_found = set()
        for p in sm_perfiles:
            plat = p.get("platform") or p.get("_search_name") or ""
            if plat:
                platforms_found.add(plat.lower())
        if platforms_found:
            obs.append(f"<strong>Redes sociales detectadas:</strong> Se encontraron {sm_exactas} perfiles con coincidencia exacta de nombre en: {', '.join(sorted(platforms_found))}.")

    obs_html = ""
    if obs:
        for o in obs:
            obs_html += f'<div class="alert alert-warn">⚠ {o}</div>'
    else:
        obs_html = '<div class="alert alert-ok"><strong>✓</strong> Sin inconsistencias detectadas entre las fuentes.</div>'

    sources_html = """
<ul class="sources-list">
  <li><strong>Padrón INE 2018</strong> — 88.4M registros locales</li>
  <li><strong>CheckID</strong> — SAT (RFC, régimen, 69/69B) + IMSS (NSS)</li>
  <li><strong>Tlaloc</strong> — RENAPO (CURP)</li>
  <li><strong>Singula</strong> — SAT (RFC validation, fallback)</li>
  <li><strong>ISSSTE</strong> — Padrón de empleados federales (2.7M)</li>
  <li><strong>Social Media Finder</strong> — tri_angle/social-media-finder vía Apify (13 redes)</li>
  <li><strong>Ollama Cloud</strong> — Análisis narrativo (glm-5.2)</li>
  <li><strong>OpenStreetMap</strong> — Geocodificación local (Nominatim México) + mapa de ubicación</li>
</ul>
"""

    conclusion_html = f"""
<section class="page">
  <h2><span class="num">09</span> Observaciones y Conclusiones</h2>
  <h3>Validación Cruzada</h3>
  {obs_html}
  <h3>Limitaciones del Reporte</h3>
  <p class="section-intro">Este reporte se basa en las fuentes consultadas al momento de su generación. Los datos pueden haber cambiado desde entonces. La información de redes sociales es OSINT público; su ausencia no implica inexistencia. Los perfiles sociales mostrados son coincidencias exactas de nombre y no constituyen verificación definitiva de identidad.</p>
  <h3>Fuentes Utilizadas</h3>
  {sources_html}
  <h3>Cláusula de Confidencialidad</h3>
  <p class="section-intro">Este documento contiene información personal protegida por la LFPDPPP. Su uso está restringido a los fines autorizados por el titular. Prohibida su reproducción total o parcial sin consentimiento escrito. La veracidad de los datos corresponde a las fuentes consultadas; este reporte no certifica la identidad del sujeto.</p>
</section>
"""

    # ===== HTML COMPLETO =====
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Reporte de Verificación — {nombre}</title>
<style>{REPORT_CSS}</style>
</head>
<body>
{cover}
{narrative_html}
{padron_html}
{map_html}
{checkid_map_html}
{extra_maps_html}
{tlaloc_html}
{checkid_html}
{issste_html}
{singula_html}
{social_html}
{conclusion_html}
</body>
</html>"""


def generate_html_to_pdf(html: str) -> bytes:
    """Convierte HTML a PDF usando weasyprint."""
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except ImportError:
        raise RuntimeError("weasyprint no instalado")
    except Exception as e:
        raise RuntimeError(f"error generando PDF: {e}")


def generate_static_map(lat: float, lon: float, width: int = 780, height: int = 320,
                        zoom: int = 16) -> bytes:
    """Genera una imagen PNG de mapa estático con marcador usando staticmap.

    Returns:
        bytes de la imagen PNG
    """
    from staticmap import StaticMap, CircleMarker
    import io

    m = StaticMap(width, height, url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png")

    # Círculo exterior blanco para visibilidad
    outer = CircleMarker((lon, lat), "white", 16)
    m.add_marker(outer)

    # Marcador rojo en la ubicación exacta
    marker = CircleMarker((lon, lat), "red", 10)
    m.add_marker(marker)

    image = m.render(zoom=zoom)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def geocode_address(direccion: str, cp: str = "") -> Optional[dict]:
    """Geocodifica una dirección mexicana vía Nominatim.

    2026-08-13: intenta Nominatim LOCAL primero (más rápido, sin rate limit),
    fallback al público si falla.

    Returns:
        dict con {lat, lon, display_name} o None
    """
    import urllib.request
    import urllib.parse
    import os

    local_url = os.environ.get("NOMINATIM_LOCAL_URL", "http://127.0.0.1:8088")
    public_url = "https://nominatim.openstreetmap.org"

    if not direccion and not cp:
        return None

    def _build_params(local: bool) -> str:
        if direccion:
            params = {
                "q": f"{direccion}, Mexico",
                "format": "jsonv2" if local else "json",
                "limit": "1",
            }
        else:
            params = {
                "postalcode": cp,
                "country": "Mexico",
                "format": "jsonv2" if local else "json",
                "limit": "1",
            }
        return urllib.parse.urlencode(params)

    def _do_request(url_base: str, params_str: str, use_ua: bool):
        url = f"{url_base}/search?{params_str}"
        headers = {"Accept": "application/json"}
        if use_ua:
            headers["User-Agent"] = "CuartoDePazSearch/1.0 (search.cuartodepaz.org)"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return data

    # 1) Intentar local primero
    if local_url:
        try:
            data = _do_request(local_url, _build_params(local=True), use_ua=False)
            if data:
                r = data[0]
                return {
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "display_name": r.get("display_name", ""),
                    "source": "local",
                }
        except Exception:
            pass

    # 2) Fallback al público
    try:
        data = _do_request(public_url, _build_params(local=False), use_ua=True)
        if data:
            r = data[0]
            return {
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "display_name": r.get("display_name", ""),
                "source": "public",
            }
    except Exception:
        pass

    return None
