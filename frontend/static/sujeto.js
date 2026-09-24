/* === sujeto.js — Refactored 2026-09 === */

/* ==== API URL ==== */
const API_URL = (() => {
  const h = location.hostname;
  if (location.protocol === "file:" || h === "" || h === "localhost" || h === "127.0.0.1")
    return "http://localhost:8765";
  return `${location.protocol}//${location.host}`;
})();

/* ==== State ==== */
const params = new URLSearchParams(location.search);
const CURP = params.get("curp");
const HINT_MODE = !CURP && (params.get("hint_nombre") || params.get("hint_paterno")
                            || params.get("hint_rfc") || params.get("hint_nss"));
const HINT_SOURCE = params.get("source") || "desconocida";
const HINTS = {
  nombre: params.get("hint_nombre") || "", paterno: params.get("hint_paterno") || "",
  materno: params.get("hint_materno") || "", fecnac: params.get("hint_fecnac") || "",
  cp: params.get("hint_cp") || "", calle: params.get("hint_calle") || "",
  colonia: params.get("hint_colonia") || "", rfc: params.get("hint_rfc") || "",
  nss: params.get("hint_nss") || "",
};

const state = {
  sujeto: null, tlaloc_curp: null, tlaloc_rfc: null,
  checkid: null, social_media: null, validations: [], results: {}, singula: {},
};
let _dossierGlobal = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);

function safeRender(name, fn) {
  try { fn(); console.log(`[KYC] ${name} OK`); }
  catch (e) { console.error(`[KYC] ${name} ERROR:`, e); setStatus("error", `${name} falló: ${e.message}`); }
}
async function safeRenderAsync(name, fn) {
  try { await fn(); console.log(`[KYC] ${name} OK`); }
  catch (e) { console.error(`[KYC] ${name} ERROR:`, e); setStatus("error", `${name} falló: ${e.message}`); }
}
function setStatus(kind, msg) {
  const el = $("status");
  el.className = "status-bar " + kind;
  el.innerHTML = (kind === "running" ? '<span class="spinner"></span>' : '') + `<span>${esc(msg)}</span>`;
}
function kvRow(k, v) {
  return `<div class="k">${esc(k)}</div><div class="v">${esc(v || '—')}</div>`;
}
function _setBadgeManual(id, txt, cls) {
  const b = $(id);
  if (b) { b.textContent = txt; b.className = "badge " + (cls || ""); }
}
function _authHeaders() {
  const t = localStorage.getItem("session_token");
  return t ? {"Authorization": `Bearer ${t}`} : {};
}
function _authHeadersJson() {
  return {"Content-Type": "application/json", ..._authHeaders()};
}

/* ==== Tab Navigation ==== */
function switchTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tabName));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.id === "tab-" + tabName));
}

/* ==== Auth check ==== */
async function checkAuth() {
  try {
    const me = await fetch(`${API_URL}/api/auth/me`, {headers: _authHeaders()}).then(r => r.json());
    if (!me.ok) { location.href = "/login.html"; return false; }
  } catch (e) {
    setStatus("error", "No se pudo conectar al backend: " + e.message);
    return false;
  }
  return true;
}

/* ==== Cache helpers (Singula) ==== */
function _cacheAge(ts) {
  if (!ts || typeof ts !== "number") return null;
  const s = (Date.now() / 1000) - ts;
  if (s < 0) return null;
  if (s < 3600) return `hace ${Math.round(s / 60)}m`;
  if (s < 86400) return `hace ${Math.round(s / 3600)}h`;
  return `hace ${Math.round(s / 86400)}d`;
}
function _singulaCacheFor(vId) {
  const v = (state.singula || {}).validations || {};
  const fc = v.from_cache || {};
  const idToKey = {"singula-judicial":"judicial","singula-blacklist":"blacklist",
    "singula-email-lookup":"email_lookup","singula-intel-basic":"intel_basic","singula-intel-premium":"intel_premium"};
  const key = idToKey[vId];
  if (!key) return null;
  const entry = (v || {})[key];
  if (!entry || typeof entry !== "object") return null;
  return {cached: !!fc[key], ageStr: !!fc[key] ? _cacheAge(entry.cached_at) : null,
          error: entry.error || null, skipped: entry.skipped || null};
}

/* ==== Render candidate table (shared by all manual searches) ==== */
function _renderCandidateTable(candidates, containerId, cols, emptyMsg) {
  const div = $(containerId);
  if (!div) return;
  if (!candidates || candidates.length === 0) {
    div.innerHTML = `<div class="sj-empty">${emptyMsg || 'Sin resultados.'}</div>`;
    return;
  }
  let html = `<table class="sj-table"><thead>
    <tr class="sj-table-header">
    <th class="sj-table th" style="width:24px"><input type="checkbox" onchange="(function(cb){var r=cb.closest('table').querySelectorAll('tbody input[type=checkbox]');r.forEach(function(x){x.checked=cb.checked;});})(this)"></th>
    <th class="sj-table th">Score</th>`;
  for (const c of cols) html += `<th class="sj-table th">${esc(c.label)}</th>`;
  html += `</tr></thead><tbody>`;
  for (const c of candidates) {
    const sc = (c._score || 0).toFixed(3);
    const color = c._score >= 0.7 ? '#16a34a' : c._score >= 0.4 ? '#f59e0b' : '#94a3b8';
    html += `<tr class="sj-table tr">`;
    html += `<td class="sj-table td"><input type="checkbox" name="manual-match" data-id="${esc(c._base + ':' + (c.rfc || c.numero_servicio || c.id_issste || ''))}"></td>`;
    html += `<td class="sj-table td score-cell" style="color:${color}">${sc}</td>`;
    for (const cdef of cols) {
      const v = cdef.get ? cdef.get(c) : (c[cdef.key] || '');
      html += `<td class="sj-table td detail-cell"><small>${esc(String(v).substring(0, 80))}</small></td>`;
    }
    html += `</tr>`;
  }
  html += `</tbody></table>`;
  html += `<div class="sj-table export-row">
    ☑ Marca los checkboxes de los candidatos válidos.
    <button onclick="exportarSeleccionManual('${containerId}')" class="sj-export-btn">📥 Exportar selección</button></div>`;
  div.innerHTML = html;
}
function exportarSeleccionManual(containerId) {
  const div = $(containerId);
  if (!div) return;
  const checked = div.querySelectorAll('tbody input[type=checkbox]:checked');
  if (!checked.length) { alert('No has marcado ningún candidato.'); return; }
  let lines = ['id_base_rfc_curp\tdatos_candidato'];
  for (const cb of checked) {
    const cells = Array.from(cb.closest('tr').querySelectorAll('td')).slice(2);
    lines.push(cb.dataset.id + '\t' + cells.map(td => td.innerText.trim()).join(' | '));
  }
  const blob = new Blob([lines.join('\n')], {type: 'text/plain'});
  const a = Object.assign(document.createElement('a'), {href: URL.createObjectURL(blob), download: 'manual_matches_' + Date.now() + '.tsv'});
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

/* ==== MAIN: loadSujeto ==== */
async function loadSujeto() {
  if (!(await checkAuth())) return;
  if (!CURP) {
    if (HINT_MODE) { renderHintMode(); return; }
    setStatus("error", "Falta parámetro ?curp=... (o ?hint_nombre=...)"); return;
  }
  setStatus("running", `Cargando sujeto ${CURP}...`);
  try {
    const r = await fetch(`${API_URL}/api/perfil/crear`, {
      method: "POST",
      headers: _authHeadersJson(),
      body: JSON.stringify({ curp: CURP }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text().catch(()=>"")).slice(0,200)}`);
    const data = await r.json();
    if (data.error) throw new Error(data.error);

    const perfil = data.perfil || {};
    const padron = perfil.padron_data || {};
    const checkidData = perfil.checkid_data || null;

    const sujeto = {
      curp: perfil.curp || padron.curp || CURP,
      rfc: perfil.rfc || padron.rfc || "",
      nss: perfil.nss || (checkidData && checkidData.nss) || "",
      nombre: padron.nombre || "",
      paterno: padron.paterno || "",
      materno: padron.materno || "",
      nombre_completo: [padron.nombre, padron.paterno, padron.materno].filter(Boolean).join(" ") || "",
      fecnac: padron.fecnac || "",
      sexo: padron.sexo || "",
      calle: padron.calle || "",
      ext: padron.ext || "",
      interior: padron.interior || "",
      colonia: padron.colonia || "",
      cp: padron.cp || "",
      estado: padron.estado || "",
      municipio: padron.municipio || "",
      seccion: padron.seccion || "",
      mza: padron.mza || "",
      folio: padron.folio || "",
      anio_reg: padron.anio_reg || "",
    };

    const checkid = checkidData ? {
      exitoso: true,
      rfc: checkidData.rfc || "",
      nss: checkidData.nss || "",
      razon_social: checkidData.razon_social || "",
      email: checkidData.email || "",
      codigo_postal: checkidData.codigo_postal || "",
      regimen_fiscal: checkidData.regimen_fiscal || "",
      estado_69_69b: checkidData.estado_69_69b || null,
    } : null;

    Object.assign(state, {
      sujeto, tlaloc_curp: null, tlaloc_rfc: null,
      checkid, social_media: null,
      validations: [], singula: {},
      perfil_completo: perfil,
      perfil_pasos: data.pasos || [],
      perfil_estado: data.estado || "desconocido",
      perfil_creditos: data.metadata?.creditos || {},
    });

    $("sujeto-titulo").textContent = sujeto.nombre_completo || "Sin nombre";
    $("sujeto-curp").textContent = CURP;
    document.title = `${sujeto.nombre_completo || CURP} — Investigación`;

    $("sujeto-fuente-badge").style.display = "none";
    $("fallback-banner").style.display = "none";

    const btnI = $("btn-issste");
    if (btnI) {
      const p = new URLSearchParams({auto: "1"});
      if (sujeto.paterno) p.set("paterno", sujeto.paterno);
      if (sujeto.materno) p.set("materno", sujeto.materno);
      if (sujeto.nombre) p.set("nombre", sujeto.nombre);
      btnI.href = `issste.html?${p}`;
    }

    safeRender("renderPadron", () => renderPadron(sujeto));
    await safeRenderAsync("renderSepomex", () => renderSepomex(sujeto));
    safeRender("loadMap", () => loadMap(sujeto));
    safeRender("renderRfc", () => renderRfc({rfc: perfil.rfc ? {rfc: perfil.rfc, source: "perfil_completo", match: true} : null}));
    safeRender("renderCheckId", () => renderCheckId(checkid));
    safeRender("renderSocialMedia", () => renderSocialMedia(null));
    safeRender("renderTlalocCurp", () => renderTlalocCurp(null));
    safeRender("renderTlalocRfc", () => renderTlalocRfc(null, sujeto.rfc));

    const sujetoRfc = sujeto.rfc;
    const sujetoNss = sujeto.nss || null;
    loadBasesExternas(sujetoRfc, sujetoNss, sujeto);

    buscarTodasLasBases(sujeto, checkid);

    fetchInteligenciaIntegral(true).catch(err => {
      console.error("[KYC] inteligencia_integral auto error:", err);
    });

    renderValidations();
    const creditos = data.metadata?.creditos || {};
    setStatus("ok",
      `✓ ${data.estado || "?"} · RFC=${sujeto.rfc||"—"} · CheckID: ${checkid?.exitoso?"✓":"—"} · ${creditos.total||0} créditos`);
  } catch (e) {
    setStatus("error", `Error cargando sujeto: ${e.message}`);
  }
}

/* ==== Render: Padrón ==== */
function renderPadron(s) {
  if (!s) return;
  const fields = [
    ["RFC", s.rfc || "—", s.rfc ? "rfc-highlight" : "empty"],
    ["CURP", s.curp], ["Nombre", s.nombre], ["Paterno", s.paterno],
    ["Materno", s.materno], ["F. nacimiento", s.fecnac], ["Sexo", s.sexo],
    ["Estado (e)", s.estado], ["Municipio (m)", s.municipio],
    ["Sección (s)", s.seccion], ["Manzana", s.mza], ["Calle", s.calle],
    ["Número ext.", s.ext], ["Número int.", s.interior], ["Colonia", s.colonia],
    ["CP", s.cp], ["Folio", s.folio], ["Año registro", s.anio_reg],
  ];
  $("kv-padron").innerHTML = fields.map(([k, v, cls]) => {
    const c = v === null || v === undefined || v === "" ? "empty" : (cls || "");
    return `<div class="k">${k}</div><div class="v ${c}">${esc(v || "—")}</div>`;
  }).join("");
  prefillManualInputs(s);
}

function prefillManualInputs(s) {
  if (!s) return;
  const nc = [s.nombre, s.paterno, s.materno].filter(Boolean).join(' ').trim().toUpperCase();
  function _set(id, val) { const el = $(id); if (el && val) el.value = String(val).trim(); }
  // Bancos (unificado)
  _set("banco-q-nombre-completo", nc); _set("banco-q-rfc", s.rfc);
  _set("banco-q-curp", s.curp); _set("banco-q-paterno", s.paterno);
  _set("banco-q-materno", s.materno); _set("banco-q-nombre", s.nombre);
  _set("banco-q-cp", s.cp);
  // Green border on prefilled
  document.querySelectorAll('.manual-form-grid input').forEach(el => {
    if (el.value) { el.style.borderColor = "rgba(34,197,94,0.5)"; el.title = "Pre-rellenado (editable)"; }
  });
}

/* ==== Render: RFC ==== */
function renderRfc(data) {
  if (!data.rfc) {
    $("kv-rfc").innerHTML = '<div class="v empty">Sin respuesta de búsqueda SAT</div>';
    $("rfc-source-badge").textContent = "sin registro SAT";
    $("rfc-source-badge").className = "badge warn";
    return;
  }
  const r = data.rfc;
  $("rfc-source-badge").textContent = r.source || "CheckID SAT";
  $("rfc-source-badge").className = "badge";
  const local = r.local || {}, sat = r.sat || {};
  const matchBadge = r.match === true ? '<span class="badge sj-match-badge">✓ coincide</span>'
    : r.match === false ? '<span class="badge warn">⚠ no coincide</span>' : '<span class="badge">—</span>';
  const rfcP = r.rfc || "", completo = rfcP.length >= 13;
  const rfcD = completo ? rfcP : (local.rfc_10 || rfcP || "—");
  const alerta = !completo ? `<div class="sj-alert sj-alert-grid">
    ⚠ <strong>Sin registro en la búsqueda sobre la base del SAT en tiempo real.</strong>
    RFC mostrado: primeros 10 caracteres calculados localmente (sin homoclave).
    ${r.error ? `<div class="mt-1">Detalle: ${esc(r.error)}</div>` : ""}</div>` : "";
  const rows = [
    ["RFC", rfcD, rfcD ? "rfc-highlight" : "empty"],
    ["Estado", completo ? "✓ Completo" : "⚠ Sin registro SAT"],
    ["Match local vs SAT", matchBadge, null, true],
    ["RFC 10 chars (local)", local.rfc_10], ["RFC SAT", sat.rfc],
    ["Homoclave", sat.homoclave || "—"], ["DV (SAT)", sat.dv || "—"],
    ["Fuente", r.source],
  ];
  $("kv-rfc").innerHTML = alerta + rows.map(([k, v, cls, isHtml]) => {
    const isCode = !isHtml && ["RFC","RFC 10 chars (local)","RFC SAT","Homoclave","DV (SAT)"].includes(k) && typeof v === "string";
    const c = !v || v === "—" ? "empty" : (cls || "");
    return `<div class="k">${k}</div><div class="v ${c}">${isHtml ? v : (isCode ? "<code>"+esc(v)+"</code>" : esc(v))}</div>`;
  }).join("");
}

/* ==== Render: CheckID ==== */
function renderCheckId(c) {
  const el = $("checkid-badge"), kv = $("kv-checkid");
  if (!c) { el.textContent = "no ejecutado"; el.className = "badge warn"; kv.innerHTML = '<div class="v empty">Sin respuesta</div>'; return; }
  el.textContent = c.exitoso ? "✓ éxito" : "⚠ falló";
  el.className = c.exitoso ? "badge" : "badge warn";
  const e69 = c.estado_69_69b;
  const rows = [
    ["Exitoso", c.exitoso ? "✓ Sí" : "✗ No"],
    ["RFC", c.rfc, c.rfc ? "rfc-highlight" : "empty"],
    ["Razón social", c.razon_social], ["NSS", c.nss],
    ["Email fiscal", c.email ? `<a href="mailto:${esc(c.email)}" target="_blank">${esc(c.email)}</a>` : "—"],
    ["Código postal", c.codigo_postal], ["Régimen fiscal", c.regimen_fiscal],
    ["69/69B", e69?.con_problema === true ? "⚠ Sí" : (e69?.con_problema === false ? "✓ No" : "—")],
    ["Error", c.error],
  ];
  kv.innerHTML = rows.map(([k, v, cls]) => {
    const c2 = v === null || v === undefined || v === "" ? "empty" : (cls || "");
    const isCode = ["RFC","NSS","Código postal"].includes(k) && typeof v === "string";
    return `<div class="k">${k}</div><div class="v ${c2}">${isCode ? "<code>"+esc(v)+"</code>" : v}</div>`;
  }).join("");
}

/* ==== Render: Social Media ==== */
function renderSocialMedia(sm) {
  const badge = $("social-media-badge"), kv = $("kv-social-media");
  if (!sm || sm.error) {
    badge.textContent = sm?.error ? "⚠ error" : "sin datos"; badge.className = "badge warn";
    kv.innerHTML = '<div class="v empty">' + esc(sm?.error || "Sin resultados.") + '</div>'; return;
  }
  const perfiles = sm.perfiles || [];
  if (!perfiles.length) {
    badge.textContent = "0 perfiles"; badge.className = "badge warn";
    kv.innerHTML = `<div class="v empty">No se encontraron coincidencias (${sm.total_encontrados||0} candidatos).</div>`; return;
  }
  badge.textContent = "✓ " + perfiles.length + (perfiles.length > 1 ? " perfiles" : " perfil");
  badge.className = "badge";
  kv.innerHTML = perfiles.map(p => {
    const platform = p.platform || p._search_name || "";
    const name = p.displayName || p.name || p.fullName || "—";
    const url = p.url || p.profileUrl || "";
    const bio = (p.bio || p.description || "").slice(0, 120);
    const followers = p.followers || "";
    return `<div class="sj-social-card">
      <div class="sj-social-header">
        <span class="sj-social-name">${esc(platform)}${p.verified?" ✓":""}</span>
        ${followers ? `<span class="text-xs text-muted">${esc(String(followers))} seguidores</span>` : ""}
      </div>
      <div class="sj-social-handle">${esc(name)}</div>
      ${bio ? `<div class="sj-social-bio">${esc(bio)}</div>` : ""}
      ${url ? `<div class="text-xs"><a href="${esc(url)}" target="_blank" class="sj-soc-link">🔗 ${esc(url)}</a></div>` : ""}
    </div>`;
  }).join("");
}

/* ==== Render: Tlaloc ==== */
function _checkTlalocVisibility() {
  const curpCard = $("tlaloc-curp-card"), rfcCard = $("tlaloc-rfc-card"), title = $("tlaloc-section-title");
  if (title && curpCard?.style.display === "none" && rfcCard?.style.display === "none") title.style.display = "none";
}
function renderTlalocCurp(t) {
  const el = $("tlaloc-source-badge");
  const card = $("tlaloc-curp-card");
  if (!t || t.error) { if (card) card.style.display = "none"; _checkTlalocVisibility(); return; }
  el.textContent = t.valid ? "✓ válido" : "✗ no encontrado";
  el.className = t.valid ? "badge" : "badge error";
  $("kv-tlaloc").innerHTML = [
    ["Status", t.status], ["Nombres", t.nombres], ["Apellido paterno", t.primerApellido],
    ["Apellido materno", t.segundoApellido], ["Sexo", t.sexo],
    ["Fecha de nacimiento", t.fechaNacimiento], ["Nacionalidad", t.nacionalidad],
    ["Entidad", t.entidad],
    ["Doc. probatorio", t.docProbatorio ? (t.docProbatorio.entidadRegistro + " / " + t.docProbatorio.municipioRegistro) : null],
  ].map(([k, v]) => `<div class="k">${k}</div><div class="v ${!v?"empty":""}">${esc(v||"—")}</div>`).join("");
}

function renderTlalocRfc(t, rfc) {
  const el = $("tlaloc-rfc-badge");
  const card = $("tlaloc-rfc-card");
  if (!rfc || !t || t.error) { if (card) card.style.display = "none"; _checkTlalocVisibility(); return; }
  el.textContent = t.valid ? "✓ válido en SAT" : "✗ no encontrado";
  el.className = t.valid ? "badge" : "badge error";
  $("kv-tlaloc-rfc").innerHTML = [
    ["RFC", rfc], ["Valid", t.valid ? "✓ Sí" : "✗ No"],
    ["Acepta CFDI", t.accept_cfdi ? "✓ Sí" : "✗ No"], ["Mensaje del SAT", t.reason],
  ].map(([k, v]) => `<div class="k">${k}</div><div class="v ${!v?"empty":""}">${esc(v||"—")}</div>`).join("");
}

/* ==== Render: SEPOMEX ==== */
async function renderSepomex(s) {
  if (!s) return;
  const cp = (s.cp || "").toString().slice(0, 5);
  const colonia = (s.colonia || "").toUpperCase();
  const kv = $("kv-sepomex"), badge = $("sepomex-badge");
  if (!cp || cp === "00000") {
    kv.innerHTML = '<div class="k">CP</div><div class="v empty">No hay CP en padrón</div>';
    badge.textContent = "sin CP"; badge.style.background = "var(--muted)"; return;
  }
  try {
    const r = await fetch(`${API_URL}/api/sepomex/validate`, {
      method: "POST", headers: _authHeadersJson(),
      body: JSON.stringify({cp, colonia, estado: s.estado_nombre||"", municipio: s.municipio_nombre||""}),
    });
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    const ok = "✓", ko = "✗";
    const fields = [
      ["CP", data.cp],
      ["Estado (SEPOMEX)", `${data.coincidencias.estado?ok:ko} ${esc(data.estado_oficial||"—")}${s.estado_nombre?` <span class="text-muted text-xs">(padrón: ${esc(s.estado_nombre)})</span>`:""}`],
      ["Municipio", `${data.coincidencias.municipio?ok:ko} ${esc(data.municipio_oficial||"—")}`],
      ["Colonia (padrón)", esc(colonia||"—")],
      ["Colonia oficial", data.colonia_oficial ? `${ok} ${esc(data.colonia_oficial)}` : (colonia ? `${ko} no existe` : "—")],
      ["Score", `${data.score}/100`], ["Veredicto", data.verdict],
    ];
    if (data.colonias_oficiales?.length) {
      fields.push(["Colonias oficiales CP", data.colonias_oficiales.slice(0,15).map(c => `${esc(c.nombre)}`).join(", ")]);
    }
    kv.innerHTML = fields.map(([k, v]) => `<div class="k">${k}</div><div class="v ${v?.startsWith("✗")?"empty":""}">${v}</div>`).join("");
    badge.textContent = data.score >= 70 ? "✓ verificado" : data.score >= 40 ? "⚠ parcial" : "✗ no coincide";
    badge.style.background = data.score >= 70 ? "var(--accent2)" : data.score >= 40 ? "var(--warn)" : "var(--error)";
  } catch (e) {
    kv.innerHTML = `<div class="k">Error</div><div class="v empty">${esc(e.message)}</div>`;
    badge.textContent = "error"; badge.style.background = "var(--error)";
  }
}

/* ==== Bases externas (lazy) ==== */
async function loadBasesExternas(rfc, nss, sujeto) {
  const s = sujeto || state.sujeto || {};
  const p = new URLSearchParams();
  if (CURP) p.set("curp", CURP);
  if (rfc) p.set("rfc", rfc);
  if (nss) p.set("nss", nss);
  for (const k of ["nombre","paterno","materno","fecnac"]) if (s[k]) p.set(k, s[k]);
  try {
    const r = await fetch(`${API_URL}/api/v1/sujeto/enriquecido?${p}`, {headers: _authHeaders()});
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    renderBasesExternas(await r.json());
  } catch (e) {
    _setBadgeManual("bases-externas-badge", "error", "err");
    const el = $("kv-bases-externas");
    if (el) el.innerHTML = `<div class="v data-err">Error: ${esc(String(e))}</div>`;
  }
}

function renderBasesExternas(d) {
  const total = d.total_registros || 0;
  const badge = $("bases-externas-badge"), totEl = $("bases-externas-total");
  if (totEl) totEl.textContent = total.toLocaleString() + " registros";
  if (badge) { badge.textContent = total > 0 ? "✓ cargado" : "sin coincidencias"; badge.className = total > 0 ? "badge" : "badge warn"; }
  const xwalk = d.xwalk || {};
  const summaryEl = $("kv-bases-externas");
  if (summaryEl) summaryEl.innerHTML = [
    kvRow("CURP", d.curp||""), kvRow("RFC usado", (xwalk.rfcs||[]).join(", ")||"—"),
    kvRow("NSS", d.nss||"—"), kvRow("Total filas", (d.total_registros||0).toLocaleString()),
  ].join("");
  renderBaseCard("att","🏠 ATT",d.att,["nombres","telefono_fijo","celular","direccion","colonia","municipio","estado"]);
  renderBaseCard("emp","🏢 Empleadores",d.empleadores,["razon_social","num_empleados","dom_calle","dom_municipio","dom_entidad","dom_cp"]);
  renderBaseCard("repuve","🚗 REPUVE",d.repuve,["placa","no_serie","marca","modelo","color","tipo","uso","propietario"]);
  renderBaseCard("telcel","📱 Telcel",d.telcel,["telefono","plan_actual","marca","modelo","estado_linea","titular_nombre1"]);
  renderBaseCard("imss-a","💼 IMSS Asegurados",d.imss_asegurado,["registro_patron","empresa_nombre","empresa_giro","empresa_cp","sueldo"]);
  renderBaseCard("imss-s","🩺 IMSS Segmentación",d.imss_salud,["edad","genero","ooad","unidad_medica"]);
  const det = $("bases-externas-detalle");
  if (det) det.innerHTML = renderBasesDetalle(d);
}

function renderBaseCard(prefix, label, b, previewFields) {
  const badgeEl = $(prefix+"-badge"), kvEl = $("kv-"+prefix);
  if (!b || !badgeEl || !kvEl) return;
  const count = b.count || 0, total = b.total != null ? b.total : count, error = b.error;
  badgeEl.textContent = error ? "error" : total === 0 ? "0" : total.toLocaleString();
  badgeEl.className = error ? "badge err" : "badge";
  if (error) { kvEl.innerHTML = `<div class="v data-err">⚠️ ${esc(error)}</div>`; return; }
  if (total === 0) { kvEl.innerHTML = '<div class="v empty">— sin registros —</div>'; return; }
  const rows = (b.rows||[]).slice(0,3), html = [];
  for (const r of rows) {
    html.push('<div class="text-xs text-muted mt-1">— registro —</div>');
    for (const f of previewFields) { const v = r[f]; if (v != null && v !== "") html.push(kvRow(f.replace(/_/g," "), String(v).slice(0,80))); }
  }
  kvEl.innerHTML = html.join("");
}

function renderBasesDetalle(d) {
  const blocks = [];
  for (const [k, label] of [["att","ATT"],["empleadores","Empleadores"],["repuve","REPUVE"],["telcel","Telcel"],["imss_asegurado","IMSS Aseg."],["imss_salud","IMSS Salud"]]) {
    const b = d[k] || {}; if (!b.total && !b.error) continue;
    blocks.push(`<h4 class="sj-h4">${label} — ${b.total||0} registros</h4>`);
    if (b.rows?.length) blocks.push(`<details><summary>Ver ${b.rows.length} filas</summary><pre class="sj-pre-block">${esc(JSON.stringify(b.rows,null,2))}</pre></details>`);
  }
  return blocks.length ? blocks.join("") : '<div class="sj-nodata">Sin datos.</div>';
}

/* ==== Manual search: unified for all entities ==== */
// Column definitions per entity
const _COLS = {
  banco: [{key:'rfc',label:'RFC'},{key:'titular_completo',label:'Titular'},{key:'cuenta',label:'Cuenta'},{key:'telefono',label:'Tel'},{key:'titular_domicilio',label:'Domicilio'},{key:'titular_colonia',label:'Colonia'},{key:'titular_cp',label:'CP'}],
};

async function buscarManualBanco() {
  const entidad = $('banco-select')?.value;
  if (!entidad) return;
  const pref = 'banco-';
  const p = new URLSearchParams();
  const mappings = [['nombre-completo','nombre_completo'],['rfc','rfc'],['curp','curp'],['paterno','paterno'],['materno','materno'],['nombre','nombre'],['cp','cp'],['telefono','telefono']];
  for (const [idKey, paramKey] of mappings) {
    const v = $(pref+'q-'+idKey)?.value.trim(); if (v) p.set(paramKey, v);
  }
  if (![...p.keys()].length) { _setBadgeManual('manual-banco-badge','falta input','err'); return; }
  p.set('limit', '100');
  const badge = $('manual-banco-badge'), resultsId = 'manual-banco-results';
  badge.textContent = `buscando en ${entidad}...`; badge.className = 'badge';
  try {
    const r = await fetch(`${API_URL}/api/v1/${entidad}/buscar?${p}`, {headers: _authHeaders()});
    const data = await r.json();
    if (data.error) { badge.textContent = `error: ${data.error.substring(0,40)}`; badge.className = 'badge err'; }
    else { badge.textContent = `${data.count||0} candidatos`; badge.className = data.count > 0 ? 'badge' : 'badge warn'; }
    _renderCandidateTable(data.candidates||[], resultsId, _COLS.banco, `Sin resultados en ${entidad}.`);
  } catch (e) { badge.textContent = 'error'; badge.className = 'badge err'; }
}

/* ==== Auto-búsqueda en todas las bases ==== */
// 2026-09: ejecuta búsqueda automática en cada base usando los parámetros
// correctos según el schema real de cada una. Se llama al cargar el sujeto.
async function buscarTodasLasBases(s, checkid) {
  if (!s) return;
  const rfc = s.rfc || "";
  const curp = s.curp || "";
  const nombre = s.nombre || "";
  const paterno = s.paterno || "";
  const materno = s.materno || "";
  const nc = [nombre, paterno, materno].filter(Boolean).join(" ");
  const calle = s.calle || "";
  const numero = s.ext || "";
  const colonia = s.colonia || "";
  const cp = (s.cp || "").toString().slice(0, 5);
  const nss = (checkid?.nss) || (s.nss) || "";

  // Definir búsquedas: cada una con su endpoint, params y columnas de resultado
  const busquedas = [
    // === Inteligencia ===
    {
      id: "auto-cfe", label: "⚡ CFE", icon: "⚡",
      endpoint: `/api/v1/cfe/buscar_avanzado?${new URLSearchParams({calle, numero, colonia, cp, limit:"50"})}`,
      cols: [{key:"titular",label:"Titular"},{key:"direccion",label:"Dirección"},{key:"colonia",label:"Colonia"},{key:"cp",label:"CP"},{key:"numero_servicio",label:"# Servicio"}],
      condition: calle || colonia || cp,
    },
    {
      id: "auto-att", label: "🏠 ATT", icon: "🏠",
      endpoint: `/api/v1/att/buscar_avanzado?${new URLSearchParams({paterno, materno, nombres:nombre, limit:"50"})}`,
      cols: [{key:"paterno",label:"Paterno"},{key:"materno",label:"Materno"},{key:"nombres",label:"Nombres"},{key:"telefono_fijo",label:"Tel fijo"},{key:"celular",label:"Celular"},{key:"direccion",label:"Dirección"},{key:"colonia",label:"Colonia"},{key:"estado",label:"Estado"}],
      condition: paterno || materno || nombre,
    },
    {
      id: "auto-telcel", label: "📱 Telcel", icon: "📱",
      endpoint: `/api/v1/telcel/buscar_avanzado?${new URLSearchParams({rfc, paterno, materno, nombre, limit:"50"})}`,
      cols: [{key:"rfc",label:"RFC"},{key:"nombre1",label:"Nombre"},{key:"nombre2",label:"Apellidos"},{key:"telefono",label:"Teléfono"},{key:"plan",label:"Plan"},{key:"domicilio",label:"Domicilio"},{key:"colonia",label:"Colonia"},{key:"cp",label:"CP"}],
      condition: rfc || paterno || nombre,
    },
    {
      id: "auto-issste", label: "🏛️ ISSSTE", icon: "🏛️",
      endpoint: `/api/v1/issste/buscar_avanzado?${new URLSearchParams({paterno, materno, nombre, limit:"50"})}`,
      cols: [{key:"paterno",label:"Paterno"},{key:"materno",label:"Materno"},{key:"nombres",label:"Nombres"},{key:"cargo",label:"Cargo"},{key:"sexo",label:"Sexo"},{key:"sueldo",label:"Sueldo"},{key:"ramo_id",label:"Ramo"},{key:"entidad_id",label:"Entidad"}],
      condition: paterno || materno || nombre,
    },
    {
      id: "auto-repuve", label: "🚗 REPUVE", icon: "🚗",
      endpoint: `/api/v1/repuve/buscar?${new URLSearchParams({rfc, nombre:nc, limit:"50"})}`,
      cols: [{key:"placa",label:"Placa"},{key:"no_serie",label:"Serie"},{key:"marca",label:"Marca"},{key:"modelo",label:"Modelo"},{key:"color",label:"Color"},{key:"propietario",label:"Propietario"},{key:"rfc",label:"RFC"}],
      condition: rfc || nc,
      rowsKey: "rows",
    },
    // === Bancarios (todos comparten schema main.personas) ===
    ...["santander","hsbc","banorte","bancomer","citibanamex","bancoppel","amex","clavijero"].map(bank => ({
      id: `auto-${bank}`, label: `🏦 ${bank.charAt(0).toUpperCase()+bank.slice(1)}`, icon: "🏦",
      endpoint: `/api/v1/${bank}/buscar?${new URLSearchParams({nombre_completo:nc, rfc, curp, limit:"50"})}`,
      cols: [{key:"rfc",label:"RFC"},{key:"titular_completo",label:"Titular"},{key:"cuenta",label:"Cuenta"},{key:"telefono",label:"Tel"},{key:"titular_domicilio",label:"Domicilio"},{key:"titular_colonia",label:"Colonia"},{key:"titular_cp",label:"CP"}],
      condition: nc || rfc || curp,
    })),
  ];

  // Filtrar solo las que tienen condición válida
  const activas = busquedas.filter(b => b.condition);
  if (!activas.length) return;

  // Actualizar badge global
  const badge = $("auto-search-badge");
  if (badge) { badge.textContent = `${activas.length} bases...`; badge.className = "badge"; }

  // Ejecutar todas en paralelo (con límite de concurrencia)
  const CONCURRENT_LIMIT = 6;
  let completadas = 0;
  const results = {};

  async function runBusqueda(b) {
    try {
      const r = await fetch(`${API_URL}${b.endpoint}`, {headers: _authHeaders()});
      const data = await r.json();
      results[b.id] = {data, config: b};
      completadas++;
      if (badge) badge.textContent = `${completadas}/${activas.length}`;
      renderAutoSearchCard(b, data);
    } catch (e) {
      completadas++;
      renderAutoSearchCard(b, {error: e.message, candidates: [], count: 0});
    }
  }

  // Ejecutar con límite de concurrencia
  const queue = [...activas];
  const workers = [];
  for (let i = 0; i < Math.min(CONCURRENT_LIMIT, queue.length); i++) {
    workers.push((async () => {
      while (queue.length) {
        const b = queue.shift();
        await runBusqueda(b);
      }
    })());
  }
  await Promise.all(workers);

  if (badge) {
    const totalMatches = Object.values(results).reduce((a, r) => a + (r.data?.count || 0), 0);
    badge.textContent = totalMatches > 0 ? `✓ ${totalMatches} coincidencias` : "0 coincidencias";
    badge.className = totalMatches > 0 ? "badge" : "badge warn";
  }
}

function renderAutoSearchCard(b, data) {
  const container = $("auto-search-results");
  if (!container) return;
  // Eliminar placeholder si existe
  const placeholder = $("auto-search-placeholder");
  if (placeholder) placeholder.remove();

  const card = document.createElement("div");
  card.className = "card";
  card.style.padding = "12px";
  const count = data.count || 0;
  const error = data.error;
  const badgeCls = error ? "badge err" : count > 0 ? "badge" : "badge warn";
  const badgeTxt = error ? "error" : `${count}`;

  let bodyHtml = "";
  if (error) {
    bodyHtml = `<div class="sj-error-text">${esc(error)}</div>`;
  } else if (count === 0) {
    bodyHtml = '<div class="sj-muted-text">Sin resultados</div>';
  } else {
    const candidates = (data[b.rowsKey || "candidates"] || data.candidates || data.rows || []).slice(0, 10);
    bodyHtml = `<table class="sj-modal-table"><thead><tr>`;
    bodyHtml += `<th class="sj-modal-table th">Score</th>`;
    for (const c of b.cols) bodyHtml += `<th class="sj-modal-table th">${esc(c.label)}</th>`;
    bodyHtml += `</tr></thead><tbody>`;
    for (const c of candidates) {
      const sc = c._score != null ? (c._score || 0).toFixed(2) : "—";
      const color = (c._score||0) >= 0.7 ? '#16a34a' : (c._score||0) >= 0.4 ? '#f59e0b' : '#94a3b8';
      bodyHtml += `<tr class="sj-modal-table tr">`;
      bodyHtml += `<td class="sj-modal-table td score-cell" style="color:${color}">${sc}</td>`;
      for (const col of b.cols) {
        const v = c[col.key] || '';
        bodyHtml += `<td class="sj-modal-table td">${esc(String(v).substring(0, 60))}</td>`;
      }
      bodyHtml += `</tr>`;
    }
    bodyHtml += `</tbody></table>`;
    if (count > 10) bodyHtml += `<div class="sj-more-hint">... y ${count - 10} más</div>`;
  }

  card.innerHTML = `<div class="sj-card-header">
    <span class="sj-card-title">${b.icon} ${esc(b.label.replace(b.icon+' ',''))}</span>
    <span class="badge ${badgeCls}">${badgeTxt}</span>
  </div>${bodyHtml}`;
  container.appendChild(card);
}

/* ==== Inteligencia Integral ==== */
async function fetchInteligenciaIntegral(auto=false) {
  const badge = $("mapeo-integral-auto-badge"), kv = $("kv-mapeo-integral-auto"), det = $("mapeo-integral-auto-detalle");
  if (badge) { badge.textContent = auto ? "cargando..." : "ejecutando..."; badge.className = "badge"; }
  let url = `${API_URL}/api/v1/sujeto/inteligencia_completa?`;
  if (CURP) url += `curp=${encodeURIComponent(CURP)}&`;
  const s = state.sujeto;
  if (s) { for (const k of ['rfc','nombre','paterno','materno']) if (s[k]) url += `${k}=${encodeURIComponent(s[k])}&`; }
  const r = await fetch(url, {headers: _authHeaders()});
  const data = await r.json();
  if (!r.ok || data.error) throw new Error(data.error || `HTTP ${r.status}`);
  _dossierGlobal = data;
  renderMapeoIntegralAuto(data);
  return data;
}

function renderMapeoIntegralAuto(data) {
  const badge = $("mapeo-integral-auto-badge"), kv = $("kv-mapeo-integral-auto"), det = $("mapeo-integral-auto-detalle");
  if (!badge || !kv || !det) return;
  const mapa = data.mapeo_domicilio_cfe || {}, servicios = mapa.servicios || [];
  const sc = ((data.dossier_sujetos||[]).find(x => x.id === "SUJETO_CENTRAL") || {}).hallazgos || {};
  const banca = sc.banca || [];
  badge.textContent = servicios.length || banca.length ? "cargado" : "sin hallazgos";
  badge.className = servicios.length || banca.length ? "badge" : "badge warn";
  kv.innerHTML = [
    kvRow("Domicilio padrón", mapa.domicilio_padron||"—"),
    kvRow("Servicios CFE", String(mapa.total_servicios||0)),
    kvRow("Cuentas bancarias", String(banca.length||0)),
    kvRow("Familiares consanguíneos", String(data.total_familiares_consanguineos||0)),
    kvRow("Convivientes", String(data.total_convivientes_inmueble||0)),
    kvRow("Vecinos", String(data.total_vecinos_misma_calle||0)),
  ].join("");

  const partes = [];
  // Dictamen
  const dc = data.dictamen_conclusivo || {};
  if (dc.estatus_identidad || dc.dinamica_relacional) {
    const nc = (dc.nivel_riesgo_kyc||"").toUpperCase().includes("BAJO") ? "var(--ok)" : "var(--warn)";
    partes.push(`<div class="sj-soc-card">
      <div class="sj-soc-header">📊 Dictamen</div>
      <div><b>Identidad:</b> ${esc(dc.estatus_identidad||'—')}</div>
      <div><b>Riesgo KYC:</b> <span style="color:${nc};font-weight:bold">${esc(dc.nivel_riesgo_kyc||'—')}</span></div>
      <div><b>Fiscal:</b> ${esc(dc.alertas_fiscales_69_69b||'—')}</div>
      <div><b>Dinámica:</b> ${esc(dc.dinamica_relacional||'—')}</div></div>`);
  }
  // CFE
  if (servicios.length) {
    partes.push('<h4 class="sj-h4-accent">⚡ CFE en el domicilio</h4>');
    for (const s of servicios) {
      const esMatch = s.tipo_relacion_titular !== "sin_match" && s.titular_nombre_relacionado;
      const score = s.score_match_titular != null ? Number(s.score_match_titular).toFixed(2) : "—";
      const ia = s.validacion_ia;
      const iaHtml = ia?.ia_disponible && ia.narrativa ? `<div class="sj-soc-card" style="margin-top:6px;padding:6px 8px"><strong>🤖 IA:</strong> ${esc(ia.narrativa)}</div>` : "";
      partes.push(`<div class="sj-soc-card">
        <div><strong>Servicio:</strong> ${esc(s.numero_servicio||"—")}</div>
        <div><strong>Titular:</strong> ${esc(s.titular||"—")}</div>
        <div><strong>Relación:</strong> <strong style="color:${esMatch?"var(--ok)":"var(--muted)"}">${esc(s.tipo_relacion_titular||"sin_match")}</strong> (score ${score})${esMatch?" — "+esc(s.titular_nombre_relacionado):""}</div>
        ${iaHtml}</div>`);
    }
  }
  // Banca
  if (banca.length) {
    partes.push('<h4 class="sj-h4-accent">🏦 Huella bancaria</h4>');
    for (const b of banca.slice(0,8)) {
      partes.push(`<div class="sj-soc-card">
        <div><strong>${esc(b.banco_o_fuente||"—")}</strong> — Cuenta: ${esc(b.cuenta||"—")}</div>
        <div class="sj-hallazgo-item">Tel: ${esc(b.telefono||"—")} · Dom: ${esc(b.domicilio||"—")}</div></div>`);
    }
  }
  // Matches de alta confianza
  const serviciosCfe = (data.mapeo_domicilio_cfe||{}).servicios||[];
  const sujetosCentral = ((data.dossier_sujetos||[]).find(x=>x.id==='SUJETO_CENTRAL')||{}).identidad||{};
  const subjCalle=(sujetosCentral.calle||"").toUpperCase(), subjExt=(sujetosCentral.ext||"").toString(), subjCp=(sujetosCentral.cp||"").toString();
  const curpsHC = new Set();
  for (const s of serviciosCfe) if ((s.score_match_titular||0)>=0.80 && s.titular_curp_relacionado) curpsHC.add(s.titular_curp_relacionado);
  const mismoDom = (ident) => {
    if (!ident||!subjCalle||!subjCp) return false;
    if ((ident.cp||"").toString()!==subjCp) return false;
    const c=(ident.calle||"").toUpperCase();
    if (c!==subjCalle && !c.includes(subjCalle) && !subjCalle.includes(c)) return false;
    return String(ident.ext||"").replace(/\.0$/,"").trim()===subjExt.replace(/\.0$/,"").trim();
  };
  const todos = data.dossier_sujetos||[];
  const filtrados = todos.filter(s => {
    if (s.id==='SUJETO_CENTRAL') return false;
    const curp = (s.identidad||{}).curp||"";
    if (curp && curpsHC.has(curp)) return true;
    if (s.id.startsWith('FAM_') && mismoDom(s.identidad)) return true;
    if (s.id.startsWith('CONV_') && mismoDom(s.identidad)) return true;
    return false;
  });
  if (filtrados.length) {
    partes.push(`<h4 class="sj-h4-accent">🧬 Alta confianza (${filtrados.length}/${todos.length})</h4>`);
    for (const s of filtrados.slice(0,10)) {
      const ident = s.identidad||{};
      partes.push(`<div class="sj-hint-candidate">
        <strong>${esc(ident.nombre_completo||ident.nombre||"—")}</strong>
        ${ident.curp?`<span class="text-muted monospace text-xs">${esc(ident.curp)}</span>`:""}
        <div class="sj-hallazgo-item">${esc(ident.calle||"")} #${esc(ident.ext||"")}, ${esc(ident.colonia||"")}, CP ${esc(ident.cp||"")}</div>
        ${s.dictamen_analitico?`<div class="mt-1 italic">${esc(s.dictamen_analitico.slice(0,200))}</div>`:""}</div>`);
    }
  }
  det.innerHTML = partes.length ? partes.join("") : '<div class="sj-nodata">Sin hallazgos automáticos.</div>';
}

/* ==== Validaciones Singula/Apify ==== */
function renderValidations() {
  const grid = $("validations-grid");
  if (!state.validations.length) { grid.innerHTML = '<div class="sj-loading-center sj-nodata">No hay validaciones disponibles</div>'; return; }
  const cacheBadge = (vId) => {
    const m = _singulaCacheFor(vId); if (!m) return "";
    if (m.error) return `<div class="cache-badge cache-err">⚠ ${esc(m.error).slice(0,60)}</div>`;
    if (m.skipped) return `<div class="cache-badge cache-skip">⏭ ${esc(m.skipped)}</div>`;
    if (m.cached && m.ageStr) return `<div class="cache-badge cache-fresh">✓ cache ${esc(m.ageStr)}</div>`;
    return '<div class="cache-badge cache-paid">💰 cobrará al ejecutar</div>';
  };
  grid.innerHTML = state.validations.map(v => `
    <div class="validation-card" id="vcard-${v.id}">
      <h4>${esc(v.name)} <span class="provider-tag ${v.provider}">${v.provider}</span></h4>
      <div class="desc">${esc(v.description)}</div>
      ${v.provider === "singula" ? cacheBadge(v.id) : ""}
      <div class="input-row"><input type="text" id="vq-${v.id}" value="${esc(v.default_query)}" placeholder="query">
        <button class="btn secondary sj-ver-btn" onclick="openModal('${v.id}')">⚙️</button></div>
      <button class="run-btn" onclick="runValidation('${v.id}')">▶ Ejecutar</button>
      <div class="result" id="vresult-${v.id}" style="display:none"></div>
    </div>`).join("");
}

let currentValidation = null;
function openModal(vId) {
  currentValidation = state.validations.find(v => v.id === vId);
  if (!currentValidation) return;
  $("modal-title").textContent = currentValidation.name;
  $("modal-body").innerHTML = `
    <p class="sj-modal-desc">${esc(currentValidation.description)}</p>
    <label class="sj-modal-label">Query:</label>
    <input type="text" id="modal-query" value="${esc($("vq-"+vId).value)}" class="sj-modal-input">
    <div id="modal-result" class="mt-4"></div>`;
  $("modal-run-btn").onclick = () => runValidationInModal(vId);
  $("modal").classList.add("open");
  document.body.style.overflow = "hidden";
}
function closeModal() { $("modal").classList.remove("open"); document.body.style.overflow = ""; currentValidation = null; }

async function runValidation(vId) {
  const v = state.validations.find(x => x.id === vId); if (!v) return;
  const query = $("vq-"+vId).value.trim(); if (!query) { alert("Query vacío"); return; }
  const card = $("vcard-"+vId), btn = card.querySelector(".run-btn"), result = $("vresult-"+vId);
  btn.disabled = true; btn.textContent = "⏳ Ejecutando..."; card.className = "validation-card busy";
  result.style.display = "block"; result.className = "result"; result.textContent = "Ejecutando...";
  const ctrl = new AbortController(); const tid = setTimeout(()=>ctrl.abort(), 300000);
  try {
    const r = await fetch(`${API_URL}/api/validar`, {method:"POST", signal:ctrl.signal, headers:_authHeadersJson(),
      body: JSON.stringify({validation_id:vId, curp:CURP, query, sujeto:state.sujeto, rfc:state.sujeto?.rfc})});
    clearTimeout(tid); if (!r.ok) throw new Error((await r.json().catch(()=>({}))).error || `HTTP ${r.status}`);
    state.results[vId] = await r.json(); card.className = "validation-card done";
    btn.textContent = "✓ Ejecutar de nuevo"; result.className = "result success";
    result.innerHTML = renderValidationResult(state.results[vId], vId);
  } catch (e) { card.className = "validation-card error"; btn.textContent = "✗ Reintentar"; result.className = "result error"; result.textContent = `Error: ${e.name==="AbortError"?"Timeout (5 min).":e.message}`; }
  finally { btn.disabled = false; clearTimeout(tid); }
}

async function runValidationInModal(vId) {
  const query = $("modal-query").value.trim(); if (!query) { alert("Query vacío"); return; }
  $("vq-"+vId).value = query;
  $("modal-run-btn").disabled = true; $("modal-run-btn").textContent = "⏳ Ejecutando...";
  $("modal-result").innerHTML = '<div class="sj-loading-center"><span class="spinner-big"></span></div>';
  const ctrl = new AbortController(); const tid = setTimeout(()=>ctrl.abort(), 300000);
  try {
    const r = await fetch(`${API_URL}/api/validar`, {method:"POST", signal:ctrl.signal, headers:_authHeadersJson(),
      body: JSON.stringify({validation_id:vId, curp:CURP, query, sujeto:state.sujeto, rfc:state.sujeto?.rfc})});
    clearTimeout(tid); if (!r.ok) throw new Error((await r.json().catch(()=>({}))).error || `HTTP ${r.status}`);
    state.results[vId] = await r.json();
    $("modal-result").innerHTML = renderValidationResult(state.results[vId], vId);
    const card = $("vcard-"+vId); card.className = "validation-card done";
    $("vresult-"+vId).style.display = "block"; $("vresult-"+vId).className = "result success";
    $("vresult-"+vId).innerHTML = renderValidationResult(state.results[vId], vId);
  } catch (e) { $("modal-result").innerHTML = `<div class="sj-error-text">Error: ${esc(e.name==="AbortError"?"Timeout.":e.message)}</div>`; }
  finally { $("modal-run-btn").disabled = false; $("modal-run-btn").textContent = "Ejecutar"; clearTimeout(tid); }
}

function renderValidationResult(data, vId) {
  const res = data?.result;
  if (res?.sin_coincidencias_determinantes || Array.isArray(res?.perfiles)) {
    const perfiles = res.perfiles || [];
    if (res.sin_coincidencias_determinantes) {
      return `<div class="sj-result-card">
        <div class="sj-hint-error">⚠ Sin coincidencias determinantes</div>
        <div class="sj-muted-text">${esc(res.mensaje||"Ningún perfil alcanzó el umbral.")}</div></div>` +
        perfiles.map(p => renderProfileCard(p, false)).join("");
    }
    return `<div class="sj-result-ok">
      <div class="sj-result-ok-text">✓ ${esc(res.mensaje||`${perfiles.length} perfiles`)}</div></div>` +
      perfiles.map(p => renderProfileCard(p, true)).join("");
  }
  return `<pre class="sj-pre-json">${esc(JSON.stringify(data,null,2))}</pre>`;
}

function renderProfileCard(p, det) {
  const sc = p._score?.score ?? 0, color = sc>=80?"#4caf50":sc>=60?"#ff9800":"#f44336";
  const url = p.url || p.profileUrl || "";
  const name = p.full_name || p.name || "—", username = p.username || "—";
  const bio = (p.biography || p.description || "").slice(0,160);
  const extra = [];
  if (p.public_email||p.email) extra.push(`📧 ${esc(p.public_email||p.email)}`);
  if (p.public_phone||p.phone) extra.push(`📞 ${esc(p.public_phone||p.phone)}`);
  if (p.followersCount||p.user_follower_count) extra.push(`👥 ${p.followersCount||p.user_follower_count}`);
  return `<div class="sj-soc-card" style="border-left:4px solid ${color}">
    <div class="sj-soc-header">
      <div class="sj-card-title">${esc((p.plataforma||"").toUpperCase())} · ${esc(username)}</div>
      <div style="background:${color};color:#000;font-weight:700;font-size:12px;padding:2px 8px;border-radius:12px">${sc}</div></div>
    <div class="sj-social-handle">${esc(name)}</div>
    ${bio?`<div class="sj-social-bio">${esc(bio)}</div>`:""}
    ${extra.length?`<div class="sj-soc-extra">${extra.map(e=>`<span class="sj-soc-extra-tag">${e}</span>`).join("")}</div>`:""}
    ${url?`<div class="sj-soc-url"><a href="${esc(url)}" target="_blank" class="sj-soc-link">🔗 ${esc(url)}</a></div>`:""}
  </div>`;
}

/* ==== Reporte AI ==== */
document.addEventListener("DOMContentLoaded", () => {
  $("btn-reporte-ai")?.addEventListener("click", async () => {
    const btn = $("btn-reporte-ai"); btn.disabled = true; btn.textContent = "⏳ Generando...";
    const cont = $("reporte-result");
    cont.innerHTML = '<div class="sj-loading-center"><span class="spinner-big"></span><div class="sj-loading-label">Generando reporte AI...</div></div>';
    const ctrl = new AbortController(); const tid = setTimeout(()=>ctrl.abort(), 300000);
    try {
      const r = await fetch(`${API_URL}/api/report/generate`, {method:"POST", signal:ctrl.signal, headers:_authHeadersJson(),
        body: JSON.stringify({sujeto:state.sujeto, enrichment:{results:state.results,tlaloc_curp:state.tlaloc_curp,tlaloc_rfc:state.tlaloc_rfc,checkid:state.checkid,social_media:state.social_media}, style:"formal", format:"html"})});
      clearTimeout(tid); if (!r.ok) throw new Error((await r.json().catch(()=>({}))).error || `HTTP ${r.status}`);
      cont.innerHTML = `<iframe id="reporte-iframe" class="sj-frame" srcdoc='${(await r.text()).replace(/'/g,"&#39;")}'></iframe>`;
      $("btn-imprimir-reporte").style.display = "inline-block";
    } catch (e) { cont.innerHTML = `<div class="sj-error-text">Error: ${esc(e.name==="AbortError"?"Timeout.":e.message)}</div>`; $("btn-imprimir-reporte").style.display = "none"; }
    finally { btn.disabled = false; btn.textContent = "📄 Generar Reporte AI"; clearTimeout(tid); }
  });
  $("btn-imprimir-reporte")?.addEventListener("click", () => {
    const iframe = $("reporte-iframe"); if (!iframe?.contentWindow) { alert("Primero genera el reporte."); return; }
    try { iframe.contentWindow.focus(); iframe.contentWindow.print(); } catch(e) { alert("Error: "+e.message); }
  });
});

/* ==== Modal close ==== */
document.addEventListener("DOMContentLoaded", () => {
  $("modal")?.addEventListener("click", e => { if (e.target === $("modal")) closeModal(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });
});

/* ==== Mapa Leaflet ==== */
let _mapInstance = null;
function buildDireccion(s) {
  const partes = [];
  const calle = (s.calle||"").trim(), ext = (s.ext||"").toString().trim(), interior = (s.interior||"").toString().trim();
  if (calle) { partes.push(`${calle} ${ext||"S/N"}`); if (interior) partes.push(`Int. ${interior}`); }
  for (const k of ['colonia','municipio_nombre','estado_nombre']) { const v=(s[k]||"").trim(); if (v) partes.push(v); }
  const cp = (s.cp||"").toString().slice(0,5); if (cp && cp!=="00000") partes.push(cp);
  return partes.join(", ");
}
async function loadMap(s) {
  const cp = (s.cp||"").toString().slice(0,5), container = $("map-container");
  if (!cp || cp==="00000") { container.style.display = "none"; return; }
  const direccion = buildDireccion(s);
  let geo = null;
  try { const r = await fetch(`${API_URL}/api/geo/geocode/${cp}?q=${encodeURIComponent(direccion)}`, {headers:_authHeaders()}); if (r.ok) { const d = await r.json(); if (!d.error) geo = d; } } catch(e){}
  if (!geo) try { const r = await fetch(`${API_URL}/api/geo/geocode/${cp}`, {headers:_authHeaders()}); if (r.ok) { const d = await r.json(); if (!d.error) geo = d; } } catch(e){}
  if (!geo) { container.style.display = "none"; return; }
  container.style.display = "block";
  $("map-direccion-label").textContent = direccion || `CP ${cp}`;
  if (_mapInstance) { _mapInstance.remove(); _mapInstance = null; }
  _mapInstance = L.map("map").setView([geo.lat, geo.lon], 16);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {attribution:'&copy; OSM', maxZoom:19}).addTo(_mapInstance);
  L.marker([geo.lat, geo.lon]).addTo(_mapInstance).bindPopup(`<b>📍 ${esc(direccion)}</b><br><small>CP ${cp}</small>`).openPopup();
  if (geo.boundingbox?.length === 4) try { _mapInstance.fitBounds([[geo.boundingbox[0],geo.boundingbox[2]],[geo.boundingbox[1],geo.boundingbox[3]]],{padding:[20,20]}); } catch(e){}
}

/* ==== Inteligencia relacional modal ==== */
document.addEventListener("DOMContentLoaded", () => {
  $("btn-inteligencia-relacional")?.addEventListener("click", async () => {
    const modal = $("modal-inteligencia"), loading = $("inteligencia-loading"), content = $("inteligencia-content");
    modal.style.display = "block"; loading.style.display = "block"; content.style.display = "none";
    try {
      const data = await fetchInteligenciaIntegral(false);
      loading.style.display = "none"; content.style.display = "block";
      $("intel-kpis").innerHTML = [
        ["TOTAL AUDITADOS", data.total_sujetos_auditados||0, "#38bdf8"],
        ["FAMILIARES", data.total_familiares_consanguineos||0, "#4ade80"],
        ["CONVIVIENTES", data.total_convivientes_inmueble||0, "#facc15"],
        ["TIEMPO", (data.tiempo_ejecucion_s||0)+"s", "#a78bfa"],
      ].map(([l,v,c]) => `<div class="sj-val-card"><div class="sj-val-num" style="color:${c}">${v}</div><div class="sj-val-lbl">${l}</div></div>`).join("");
      const d = data.dictamen_conclusivo||{};
      $("intel-dictamen").innerHTML = `<div class="sj-dictamen-title">📊 Dictamen KYC:</div>
        <div>• <b>Identidad:</b> ${esc(d.estatus_identidad||"Verificada")} · <b>Riesgo:</b> <span class="sj-dictamen-risk">${esc(d.nivel_riesgo_kyc||"BAJO")}</span></div>
        <div>• <b>Fiscal:</b> ${esc(d.alertas_fiscales_69_69b||"Limpia")} · <b>Dinámica:</b> ${esc(d.dinamica_relacional||"")}</div>`;
      const cfeList = (data.mapeo_domicilio_cfe?.servicios)||data.servicios_cfe_inmueble||[];
      if (cfeList.length) {
        $("intel-cfe-box").innerHTML = `⚡ <b>CFE:</b><br/>${cfeList.map(c => `<b>${esc(c.titular)}</b> (${esc(c.numero_servicio)} · ${esc(c.direccion)})`).join('<br/>')}`;
        $("intel-cfe-box").style.display = "block";
      } else $("intel-cfe-box").style.display = "none";
      $("cnt-todos").textContent = data.total_sujetos_auditados||0;
      $("cnt-fam").textContent = (data.total_familiares_consanguineos||0)+1;
      $("cnt-conv").textContent = (data.total_convivientes_inmueble||0)+cfeList.length;
      filtrarVarios('conv');
    } catch (err) { loading.style.display = "none"; alert("Error: "+err.message); }
  });
});

function filtrarVarios(tipo) {
  for (const t of ['todos','fam','conv']) $("tab-btn-"+t).style.background = tipo===t?'#0284c7':'#334155';
  if (!_dossierGlobal) return;
  const listEl = $("intel-sujetos-list"); listEl.innerHTML = "";
  let sujetos = _dossierGlobal.dossier_sujetos||[];
  if (tipo==='fam') sujetos = sujetos.filter(s=>s.id.startsWith('FAM_')||s.id==='SUJETO_CENTRAL');
  if (tipo==='conv') sujetos = sujetos.filter(s=>s.id.startsWith('CONV_')||s.id.startsWith('CFE_')||s.id==='SUJETO_CENTRAL');
  for (const s of sujetos) {
    const ident = s.identidad||{}, h = s.hallazgos||{};
    let hallazgosHtml = "";
    if (h.empleo_imss?.length) hallazgosHtml += `<div class="sj-hallazgo-item">💼 ${esc(h.empleo_imss[0].patron)} · $${esc(h.empleo_imss[0].sueldo_base||'—')}</div>`;
    if (h.telefonia?.length) hallazgosHtml += `<div class="sj-hallazgo-item">📱 ${h.telefonia.map(t=>esc(t.telefono)).join(', ')}</div>`;
    if (h.banca?.length) hallazgosHtml += `<div class="sj-hallazgo-item">🏦 ${h.banca.map(b=>esc(b.banco_o_fuente||b.banco)).join(', ')}</div>`;
    const card = document.createElement("div");
    card.className = "sj-result-card";
    card.innerHTML = `<div class="sj-dossier-card">
      <div><span class="sj-dossier-name">${esc(ident.nombre_completo||ident.nombre)}</span>
        <span class="sj-dossier-curp">${esc(ident.curp||'ID')}</span></div>
      <span class="sj-dossier-cat">${esc(s.categoria)}</span></div>
      <div class="sj-dossier-rel">📌 ${esc(s.relacion_origen)}</div>
      ${hallazgosHtml}
      <div class="sj-dossier-dictamen"><b>Dictamen:</b> ${esc(s.dictamen_analitico)}</div>`;
    listEl.appendChild(card);
  }
}

/* ==== Hint mode ==== */
function renderHintMode() {
  document.title = `Resolviendo (${HINT_SOURCE}) — Investigación`;
  $("sujeto-titulo").textContent = `Creando sujeto desde ${HINT_SOURCE.toUpperCase()}`;
  $("sujeto-curp").textContent = "—"; $("sujeto-curp").style.color = "var(--warn)";
  const main = document.querySelector("main");
  const card = document.createElement("div"); card.className = "card"; card.id = "hint-resolver-card";
  card.innerHTML = `<div class="title">🧩 Resolver identidad · fuente: <span class="badge" style="background:var(--warn);color:#000">${esc(HINT_SOURCE)}</span></div>
    <p class="sj-muted-text mb-3">Confirmá o ajustá los datos y pulsá <strong>Resolver CURP</strong>.</p>
    <div class="manual-form-grid">
      <input id="hint-nombre" placeholder="Nombre" value="${esc(HINTS.nombre)}">
      <input id="hint-paterno" placeholder="Paterno" value="${esc(HINTS.paterno)}">
      <input id="hint-materno" placeholder="Materno" value="${esc(HINTS.materno)}">
      <input id="hint-fecnac" placeholder="YYYY-MM-DD" value="${esc(HINTS.fecnac)}">
      <input id="hint-rfc" placeholder="RFC" value="${esc(HINTS.rfc)}">
      <input id="hint-nss" placeholder="NSS" value="${esc(HINTS.nss)}">
      <input id="hint-cp" placeholder="CP" value="${esc(HINTS.cp)}">
      <input id="hint-calle" placeholder="Calle" value="${esc(HINTS.calle)}">
      <input id="hint-colonia" placeholder="Colonia" value="${esc(HINTS.colonia)}">
    </div>
    <div id="hint-result" class="mt-3"></div>
    <div class="flex gap-3 mt-3">
      <button class="btn" id="btn-resolver">🔍 Resolver CURP</button>
      <button class="btn secondary" id="btn-volver">← Volver</button></div>`;
  main.insertBefore(card, main.firstChild);
  $("btn-volver").addEventListener("click", () => history.length > 1 ? history.back() : location.href = "/buscar.html");
  $("btn-resolver").addEventListener("click", resolverDesdeHints);
  setStatus("running", `Modo hint — fuente: ${HINT_SOURCE}. Pulsá Resolver.`);
}

async function resolverDesdeHints() {
  const get = id => $(id).value.trim();
  const qs = new URLSearchParams();
  for (const [k,id] of [["nombre","hint-nombre"],["paterno","hint-paterno"],["materno","hint-materno"],["fecnac","hint-fecnac"],["rfc","hint-rfc"],["nss","hint-nss"],["cp","hint-cp"],["calle","hint-calle"],["colonia","hint-colonia"]]) { const v=get(id); if(v) qs.set(k,v); }
  qs.set("source", HINT_SOURCE);
  if (![...qs.keys()].some(k => k!=="source")) { $("hint-result").innerHTML = '<div class="sj-error-text">Necesitás al menos un dato.</div>'; return; }
  const btn = $("btn-resolver"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Resolviendo...';
  $("hint-result").innerHTML = '<div class="sj-loading-sm"><span class="spinner"></span> Buscando...</div>';
  try {
    const r = await fetch(`${API_URL}/api/v1/sujeto/resolver_desde_hint?${qs}`, {headers: _authHeaders()});
    const data = await r.json();
    if (data.ok && data.curp) {
      $("hint-result").innerHTML = `<div class="sj-hint-match">✓ Match: ${esc(data.estrategia)} (score ${data.score.toFixed(2)}) — abriendo...</div>`;
      setTimeout(() => location.href = `/sujeto.html?curp=${encodeURIComponent(data.curp)}`, 800); return;
    }
    if (data.candidates?.length) {
      $("hint-result").innerHTML = `<div class="sj-hint-candidates">⚠ ${data.candidates.length} candidatos:</div>` +
        data.candidates.map(c => `<div class="sj-hint-candidate"><code class="text-accent2">${esc(c)}</code><button class="btn accent2 sj-ver-btn" onclick="location.href='/sujeto.html?curp=${encodeURIComponent(c)}'">Abrir</button></div>`).join("");
    } else $("hint-result").innerHTML = `<div class="sj-error-text">✗ Sin match${data.error?" — "+esc(data.error):""}. Ajustá y reintentá.</div>`;
  } catch (e) { $("hint-result").innerHTML = `<div class="sj-error-text">Error: ${esc(e.message)}</div>`; }
  finally { btn.disabled = false; btn.innerHTML = '🔍 Resolver CURP'; }
}

/* ==== Bootstrap ==== */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".tab-btn").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  switchTab("identidad");
});
loadSujeto();
