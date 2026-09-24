# Argos OSINT KYC Platform — Design System

Sistema extraído del código existente (`frontend/css/global.css`, `frontend/static/sujeto.css`, `frontend/buscar.html`, `frontend/static/sujeto.js`). Este archivo es la fuente de verdad para toda decisión visual. Ningún componente se escribe sin consultarlo.

## 1. Atmosphere & Identity

Un centro de mando de investigación OSINT. Denso en información pero tranquilo: paneles oscuros separados por bordes finos y tonalidades cercanas, con un fondo atmosférico profundo (radial + grano) que da la sensación de una consola de operaciones nocturna. La firma visual es el **contraste entre la superficie quieta (`--panel`) y el acento eléctrico (`--accent` azul, `--accent2` menta)** usado solo donde hay datos o acción — nunca decorativamente.

## 2. Color

Solo modo oscuro. No hay variante clara.

### Palette

| Role | Token | Value | Usage |
|------|-------|-------|-------|
| Surface/base | `--bg` | `#0f1115` | Fondo de página (sobre `argos-bg`) |
| Surface/panel | `--panel` | `#1a1d24` | Cards, navbar, modales |
| Surface/elevated | `--panel2` | `#232730` | Inputs, badges, stat cards, hover de filas |
| Border/default | `--border` | `#2c313c` | Bordes de panel, inputs, divisores |
| Text/primary | `--text` | `#e6e8ec` | Cuerpo, títulos |
| Text/secondary | `--muted` | `#8a93a6` | Labels, captions, hint text |
| Accent/primary | `--accent` | `#4f8cff` | CTAs, links, focus, acento azul |
| Accent/secondary | `--accent2` | `#6ee7b7` | Éxito, datos destacados, acento menta |
| Status/success | mapped to `--accent2` | `#6ee7b7` | Confirmaciones, pasos OK |
| Status/warning | `--warn` | `#f59e0b` | Precauciones, parcial |
| Status/error | `--error` | `#ef4444` | Errores, destructivo |
| Status/highlight | `--highlight` | `#facc15` | Resaltado puntual (CFDI, RFC) |

### Badge tint ramp (translúcido sobre panel)

Derivado de cada acento con alfa fijo — patrón repetido en `global.css`:

| Variant | Background | Border | Text |
|---------|-----------|--------|------|
| success | `rgba(110,231,183,.12)` | `rgba(110,231,183,.25)` | `--accent2` |
| warning | `rgba(245,158,11,.12)` | `rgba(245,158,11,.25)` | `--warn` |
| danger | `rgba(239,68,68,.12)` | `rgba(239,68,68,.25)` | `--error` |
| info | `rgba(79,140,255,.12)` | `rgba(79,140,255,.25)` | `--accent` |
| highlight | `rgba(250,204,21,.12)` | `rgba(250,204,21,.25)` | `--highlight` |
| neutral | `--panel2` | `--border` | `--muted` |

### Rules

- La jerarquía de superficie es **borders + tonal-shift suave** (`--panel` vs `--panel2` vs `--bg`); sombras solo en overlays elevados (modal, toast, dropdown).
- `--accent` y `--accent2` son **solo para interacción o datos significativos** (éxito, dato validado). Nunca rellenos grandes ni decoración.
- El texto sobre un acento sólido usa `#fff` sobre `--accent`/`--error` y `#000`/`#1a1d24` sobre `--accent2`/`--warn`/`--highlight` (los acentos brillantes rechazan blanco por contraste).
- No introducir hex fuera de esta tabla. Para un rol semántico nuevo, extender la tabla primero.

## 3. Typography

Solo una familia de sistema; escala compacta de consola (el contenido es denso).

### Font Stack

- **Primary:** `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif` (`--font`)
- **Mono:** `'SF Mono', 'Fira Code', 'Cascadia Code', monospace` (`--font-mono`) — identificadores (CURP, RFC, NSS), tiempos, números de crédito

### Scale (fuente: `global.css` + `sujeto.css`)

| Level | Size | Weight | Letter | Usage |
|-------|------|--------|--------|-------|
| H1 | 18px / 1.28rem | 600 | normal | Título de página (header de sujeto) |
| H2 | 16px / 1.15rem | 600 | normal | Título de modal, header de card principal |
| H3 | 14px / 1rem | 600 | `0.05em` uppercase | Título de sección dentro de card, label de paso |
| Body | 14px / 1rem | 400 | normal | Texto base |
| Body/sm | 13px / 0.93rem | 400 | normal | KV rows, tabla, detail de paso |
| Caption | 12px / 0.86rem | 400 | normal | Metadata, tiempo ms, hint |
| Overline | 11px / 0.79rem | 600 | `0.05em` uppercase | Section labels, stat-card label |

### Rules

- Texto de cuerpo nunca bajo 13px; captions/metadata a 12px; overline a 11px siempre uppercase + tracking.
- Identificadores y números de crédito siempre en `--font-mono`.
- `letter-spacing` solo en overlines/labels (0.04–0.05em), nunca en cuerpo.

## 4. Spacing & Layout

Base de **4px**. Los valores repetidos en el código actual son px directos; se tokenizan por intención:

| Token | Value | Usage |
|-------|-------|-------|
| `--space-1` | 4px | gap icono-label, padding chip |
| `--space-2` | 8px | gap entre items de lista, padding compacto |
| `--space-3` | 12px | padding de paso, gap estándar |
| `--space-4` | 16px | padding de card, modal-body |
| `--space-5` | 20px | padding header de modal |
| `--space-6` | 24px | main padding, separación de secciones |

### Layout

- Max content width: `1100px` (`main` en sujeto), `1200px` (`.container` global), `720px` (`.container-sm`).
- Border radius: `--radius: 6px` (inputs, botones), `--radius-lg: 10px` (cards, modales), `--radius-pill: 999px` (badges).
- Breakpoints: sm 480 / md 640–768 / lg 1024.

### Rules

- Los componentes de flujo usan los tokens `--space-*` y `--radius-*`; no introducir px arbitrarios nuevos.
- Mechanic sizes (`100%`, `1fr`, `minmax()`, `clamp()`) se quedan raw — no son tokens.

## 5. Components

(Extraídos del código existente; los nuevos del flujo orgánico se marcan **[nuevo]**.)

### Button (`.btn`)

- **Structure:** `<button class="btn btn-primary">`
- **Variants:** default (panel2), `-primary` (accent), `-secondary` (transparente/borde), `-danger` (error), `-ghost`; tamaños `-sm`, `-lg`, `-block`, `-icon`
- **Spacing:** padding `0.55rem 1.2rem` (≈ space-3 vertical, space-5 horizontal), gap `0.5rem`
- **States:** default · hover (bg → border) · active (`scale(0.97)`) · focus-visible · disabled (`opacity .45`)
- **Motion:** `all var(--transition)` (150ms); active press en `transform`
- **Accessibility:** target ≥36px alto

> **Consolidación pendiente:** `sujeto.css` tiene una variante glossy (gradiente + inner shadow + glow) paralela. Se conserva en su pantalla; la variante plana de `global.css` es la estándar del sistema. Unificar = decisión explícita posterior.

### Badge (`.badge`)

- **Structure:** `<span class="badge badge-success">`
- **Variants:** neutral · `-success` · `-warning` · `-danger` · `-info` · `-highlight` (tint ramp §2)
- **Spacing:** `0.2rem 0.6rem`, radius `--radius-pill`
- **States:** estático (sin hover); uppercase 0.75rem
- **Accessibility:** contraste AA sobre `--panel`

### Card (`.card` / `.stat-card` **[nuevo]**)

- **Structure:** `.card > .card-header + .card-body + .card-footer`
- **Variants:** default (panel + border); `.stat-card` = celda de métrica con label overline + valor grande
- **Spacing:** padding `--space-5`/`--space-4`
- **States:** default; stat-card no interactivo
- **Layout:** stack interna; stat-cards en `cluster` (flex gap `--space-3`)

### Step List (`.step-list` / `.step-item` **[nuevo]**)

- **Structure:** `.step-list > .step-item(.ok|.warn|.error|.skip)*`
- **Anatomy:** icono emoji (20px centrado) · label (flex-1, text) · tiempo (`--font-mono` caption muted) · estado (✓/✗/♻/— coloreado) · detalle opcional (caption muted)
- **Variants de estado:** `.ok` (accent2) · `.warn` (warn) · `.error` (error) · `.skip`/reutilizado (muted + ♻)
- **Spacing:** padding `--space-2` `--space-3`, separator `border-bottom` `--border`
- **States:** sin hover; el estado lo dicta el dato
- **Accessibility:** cada fila comunica estado por icono + color, no solo color

### Source Chip (`.source-chip` **[nuevo]**)

- **Structure:** `<span class="source-chip">` con `✓ NOMBRE (n)`
- **Variants:** default (tinte accent2 translúcido, chip pill pequeño)
- **Spacing:** `0.15rem 0.5rem`, radius `--radius-pill`, cluster wrap gap `--space-1`
- **States:** estático

### KV Row (`.kv-row` **[nuevo]**)

- **Structure:** `<div class="kv-row"><span class="kv-label">RFC</span><span class="kv-val">…</span></div>`
- **Spacing:** gap `--space-2`, label min-width `64px`
- **Typography:** label caption muted uppercase; valor `--font-mono` text
- **Empty state:** `.kv-row .kv-val:empty` no aplica — renderizado solo si hay valor (condicional en JS)

### Modal (`.modal-overlay` / `.modal`)

- **Structure:** `.modal-overlay > .modal > .modal-header + .modal-body + .modal-footer`
- **Spacing:** overlay padding `--space-6`; modal-body `--space-6`; header/footer `--space-5`
- **States:** open (`.open` o append al DOM); max-height `85vh` scroll propio
- **Motion:** fade en overlay (recomendado 150ms) — entrada/salida
- **Accessibility:** foco atrapado dentro, `Esc` cierra, `role="dialog"` + `aria-modal`
- **Layout:** centrado flex, max-width `560px` (global) / `640px` (sujeto); en ≤640px bottom-sheet full-width (su modal responsive)

## 6. Motion & Interaction

| Type | Duration | Easing | Usage |
|------|----------|--------|-------|
| Micro | 120–150ms | ease | hover de botón, press (`scale(0.97)`), tab color |
| Standard | 200–300ms | ease | toast-in (250ms), modal overlay fade, tab switch |
| Continuous | 600–800ms linear inf | — spinner |

### Rules

- Solo se animan `transform` y `opacity` (press en scale, toast en translateX, overlays en opacity).
- Todo elemento interactivo tiene hover + active + focus-visible.
- Spinner usa `transform: rotate`, nunca layout.
- `prefers-reduced-motion: reduce` desactiva la animación de fondo `argos-bg` y reduce transiciones no esenciales (spinner se mantiene por ser indicador de carga).

## 7. Depth & Surface

**Estrategia elegida: `mixed` (borders + tonal-shift suave + sombras solo en overlays).**

- Jerarquía base por tonalidad (`--bg` → `--panel` → `--panel2`) y borde fino `--border` — sin sombras en cards en reposo.
- Sombras reservadas a superficies elevadas sobre el contenido:
  - `--shadow: 0 2px 8px rgba(0,0,0,.35)` — dropdowns, cards elevadas
  - `--shadow-lg: 0 8px 32px rgba(0,0,0,.5)` — modales, toasts, popovers
- Overlay de modal: `rgba(0,0,0,.6)` + `backdrop-filter: blur(4px)` (global) — refuerza la capa sin nuevo color.

## 8. Accessibility Constraints & Accepted Debt

### Constraints

- WCAG 2.2 AA: contraste ≥4.5:1 cuerpo, ≥3:1 large text. El paso de estado en step-list usa color + icono (cumple no-solo-color).
- Focus visible en todo interactivo: borde `--accent` + `box-shadow 0 0 0 2px rgba(79,140,255,.15)` (patrón de inputs) extensible a botones.
- Teclado: modal atrapa foco, `Esc` cierra, tabs navegables.
- `prefers-reduced-motion` respetado (§6).
- Targets táctiles ≥36px; en mobile ≥44px (regla de `sujeto.css`).

### Accepted Debt

| Item | Location | Why accepted | Owner / Exit |
|------|----------|--------------|--------------|
| Doble sistema de `.btn` (plano global vs glossy en sujeto) | `sujeto.css` vs `global.css` | El glossy es identidad de la ficha de sujeto; migrar global a glossy o viceversa requiere revisar todas las pantallas que usan `.btn` | Unificar al extraer componentes de las pantallas restantes |
| Doble clase modal (`.modal-overlay`+`.modal` en global; `.modal-backdrop` en sujeto) | ambas hojas | Ambas funcionan; se converge en la de global (`.modal-overlay`+`.modal`, backdrop-filter) al tocar cada pantalla | Al refactorizar `sujeto.js` |
| Reglas flow (step-list, stat-card, source-chip, kv-row) duplicadas en buscar.html inline y en global.css | `global.css` + `buscar.html` | buscar.html es autocontenido y no carga global.css; centralizar requiere revisar las colisiones de `.btn`/`.card`/`.modal` | Al migrar buscar.html a <link global.css> |
| Footer del modal perfil con sticky bottom para mobile | `buscar.html` | Corregido: `position:sticky; bottom:0` en `.perfil-actions` | Completado — 2026-09-08 |
| Emojis como iconos de paso (📂📋🧮…) | flujo orgánico | Identifican las 9 fuentes de un vistazo en la consola; reemplazarlos por set SVG es trabajo de iconografía aparte | Cuando se adopte un set de iconos (Lucide) para toda la app |
| `renderMapearResult` con inline styles | `buscar.html` | Fuera del alcance actual (modal del flujo orgánico migrado); el mapeo CFE sigue con el patrón viejo | Al hacer polish de la pantalla de mapeo/búsqueda |
