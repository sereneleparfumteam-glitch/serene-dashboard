# Serene AI Dashboard · Roadmap Pro

**Última actualización:** 2026-05-12 (sprint completado, ver "Estado" abajo)
**Objetivo:** Convertir el dashboard de "vista Meta Ads" a "command center 360° de Serene" — Meta + Shopify + Google Ads + trazabilidad + recomendaciones de contenido.

## Estado final del sprint (2026-05-12)

| Pack | Estado | Notas |
|---|---|---|
| **G** Ciudades/Regiones | ✅ COMPLETED | Geo breakdown por departamento (region), 34 regiones, top winners Distrito Especial + Antioquia |
| **A** Shopify integration | ✅ COMPLETED | Revenue $108.6M/7d, ROAS real 8.15x, 654 órdenes, top SKUs, ciudades, cohorts, abandoned, inventory |
| **B** Google Ads | 🚫 BLOCKED | Refresh token OAuth revoked. Acción usuario abajo. |
| **C** Activity Feed | ✅ COMPLETED (Meta+Shopify) | 427 cambios humanos, 10 actores (Lidis, Gabriela), filtros JS, flags severity. Google se agrega cuando se desbloquee Pack B. |
| **F** Smart Opportunities | ✅ COMPLETED (Fase 1) | 10 oportunidades algorítmicas cross-data. Fase 2 (LLM+trends) pendiente. |
| **D** UI polish | ✅ COMPLETED | Topbar chips (revenue/ROAS/activity), historical archive (foundation Pack E) |
| **E** Forecasting+Anomaly+Digest | ✅ INFRA COMPLETED | history.py listo (z-score + linear forecast). Activa automático a los 7 días. Digest enhanced YA con Shopify+ROAS+top-opp. |

## Acciones requeridas del usuario para 100%

### 1. Setear GitHub secrets (5 min)
Para que el cron diario incluya Shopify automáticamente:
- Ir a https://github.com/sereneleparfumteam-glitch/serene-dashboard/settings/secrets/actions
- Agregar 2 secrets (los valores te los paso por chat privado):
  - `SHOPIFY_CLIENT_ID`
  - `SHOPIFY_CLIENT_SECRET`
- (Opcional) `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` si querés el digest diario en Telegram

Las credenciales están en `~/.claude.json` bajo `mcpServers.shopify.args` (clientId/clientSecret).

### 2. Regenerar Google Ads OAuth refresh token (10 min)
Para activar Pack B (Google Ads) + parte Google de Pack C (change_event):
1. `cd /root/workspaces/serene-ads && ls credentials/` — ver dónde está el flow OAuth
2. Probablemente ejecutar: `./venv/bin/python -m google_ads.scripts.refresh_token` (script de regen)
3. O regenerar manualmente desde Google Cloud Console: https://console.cloud.google.com/apis/credentials → OAuth 2.0 Client → revoke old + create new
4. Actualizar el token en `/root/workspaces/serene-ads/credentials/google-ads.yaml`
5. Reiniciar el MCP server serene-ads-prod
6. Decirme "Google listo" → arranco extract_google.py

---

## Estado actual (baseline)

Hoy el dashboard solo consume **Meta Marketing API** (`extract.py` único request layer). Secciones existentes:
- KPI hero (spend/CTR/ROAS/CPA)
- Multi-account comparison
- Funnel de conversión
- Insights AI auto-detectados
- Campañas ranked por score
- Audience Intelligence (demographics + placement + **geo por país**)
- Post ID Intelligence
- Recomendaciones AI
- Urgent Actions (kill list / fix list)

**Pipeline:** `extract.py` → snapshot JSON → `analyze.py` → `render.py` (Jinja2) → `output/dashboard_*.html` → cron copia a `public/index.html` → Vercel deploy diario.

**Bloqueante conocido:** pixel value tracking gap → ROAS Meta API no es real. Solo se resuelve cuando metamos Shopify (Pack A) y calculemos ROAS = Shopify revenue / Meta+Google spend.

---

## Pack G · Quick win: ciudades en vez de países (~30 min)

Cambiar geo breakdown del `country` actual a `region` o `city` en Meta API.

**Archivo a tocar:** `extract.py:get_audience_breakdowns` — cambiar param `breakdowns=country` → `breakdowns=region` (o `city`). Region en Meta = departamento/estado (más útil que país para Colombia donde el 100% del tráfico es local). City es más granular pero más ruido.

**Decisión:** ir con **region** (≈departamentos de Colombia). Más legible y accionable que ciudades individuales que tendrán low volume.

**Template:** `dashboard.html.j2` sección "Country" → renombrar a "Región" + cambiar columna label.

---

## Pack A · Shopify integration (~3-4 h)

Datos a pullar via Shopify GraphQL Admin API (token vía `[[reference_shopify_admin_token]]`):

- **Revenue / AOV / orders count** (last 7d, 30d, MoM delta)
- **Top SKUs vendidos** (cross-ref con [[project_serene_catalog_intelligence]] — destacar si son los top esperados o si emergió un winner nuevo)
- **Customer cohorts**: new vs returning, repeat purchase rate, días entre compras
- **Abandoned checkouts**: cantidad, valor total perdido, top razones (si están en metafields)
- **Inventory health**: SKUs sin stock, SKUs Tendencia con tracked=false (referencia Task #15)
- **ROAS real cross-platform**: `(Shopify revenue) / (Meta spend + Google spend)` — calculado en analyze.py

**Archivos a crear/tocar:**
- `extract_shopify.py` (nuevo) — GraphQL queries para orders, products, customers
- `analyze.py` — agregar funciones `analyze_shopify_data()`, `calculate_real_roas()`
- `templates/dashboard.html.j2` — nueva sección "Shopify · Revenue" entre Funnel y Campaigns
- `extract.py` — añadir orchestrator que llame ambos y construya snapshot unificado

**Bonus:** order timeline (eventos por orden) feed la trazabilidad de Pack C.

---

## Pack B · Google Ads integration (~3-4 h)

MCP ya disponible: `serene-ads-prod` (customer 4027382494). Datos:

- **Spend, CTR, conversions, conversion value, ROAS**
- **Top keywords ganadores** vs **search terms perdedores** (waste analysis)
- **Quality Score promedio** + distribución
- **Impression share lost** (lost to rank vs lost to budget — accionable distinto)
- **PMax breakdown** si hay campañas activas
- **Auction Insights** — competidores que aparecen + overlap rate
- **Change history** vía GAQL `change_status` (alimenta Pack C)

**Archivos:**
- `extract_google.py` (nuevo) — usa el MCP serene-ads-prod (read-only, sin CONFIRMO)
- `analyze.py` — `analyze_google_data()`
- `templates/dashboard.html.j2` — sección "Google Ads · Performance"
- Cross-platform attribution: si Meta y Google compiten por el mismo usuario, mostrar overlap

**OJO scope:** developer token de Serene ya está aprobado a Basic Access (a diferencia de Colcofy que está pending). Writes están disponibles pero NO se hacen desde el dashboard (read-only).

---

## Pack C · Trazabilidad / Activity Feed (~4-5 h)

**Lo más pedido por el user.** Timeline cross-platform de quién hizo qué cambio, cuándo, en qué plataforma.

**Fuentes:**
- **Meta**: `GET /act_{id}/activities?fields=event_type,event_time,object_id,object_name,actor_id,actor_name,extra_data` — retorna últimos N días de cambios. Incluye `actor_name` (quién lo hizo). Cubre: pause/resume, budget change, audience edit, creative edit.
- **Google Ads**: GAQL query sobre `change_event` resource — campos `change_date_time`, `user_email`, `change_resource_type`, `old_resource`, `new_resource`. Ultra granular hasta el campo exacto.
- **Shopify**: REST `/admin/api/2024-10/events.json?since_id=…` — eventos sobre orders, products, customers. Tiene `user_id` y `subject_type`.

**UI:**
```
ACTIVITY FEED · last 24h · 47 changes

[Filter: All ▼] [Platform: All ▼] [User: All ▼]

🟠 Meta Ads   2026-05-12 14:32 · Daniel Beltrán
   Pausó campaña "DPA · Retargeting · VC 14d · BORRADOR"
   Spend previo: $0 lifetime · Status: ACTIVE → PAUSED

🟠 Meta Ads   2026-05-12 13:15 · Gabriela
   Aumentó budget DPA · ATC 14d $50,000 → $100,000 COP/día
   Delta: +100% · Justificación auto-detectada: no

🔵 Google Ads 2026-05-12 11:00 · daniel.serene@gmail.com
   Agregó keyword "perfume hombre nicho" · match=BROAD · bid=$8.50 USD
   Ad group: Hombres Nicho · Campaign: SLP Search · Colombia

🟢 Shopify    2026-05-12 09:45 · Sistema (API)
   Cambió precio "Tom Ford Oud Wood (S832)" $189,000 → $179,000 COP
   App caller: Manual (admin UI)
```

**Archivos:**
- `extract_activity.py` (nuevo) — fetch unificado de los 3 logs
- `analyze.py` — `analyze_activity()` que normaliza al schema común y aplica heuristics (detectar cambios que pueden romper performance: budget +200%, audience swap, pausar campaña con buen score)
- `templates/dashboard.html.j2` — nueva sección con filtros JS client-side

**Heuristics auto-flagging:**
- 🚨 Budget aumentó >100% sin justificación documentada
- 🚨 Campaign paused with high score (>70)
- ⚠️ Price drop in top SKU + active campaign with that product
- ⚠️ Negative keyword added to high-converting search term

---

## Pack F · Próximas publicaciones recomendadas (~5-6 h)

**Lo nuevo del user.** Sistema que sugiere qué publicar/hacer la próxima semana (ads + orgánico).

**Fuentes de señal (no es Google Search literal — son varias APIs):**

1. **Winners propios (Meta + Shopify):**
   - Top 5 post IDs con mejor ROAS/CTR/engagement de Meta
   - Top 5 SKUs vendidos cross-ref con qué campañas los promueven
   - Pattern detection: angles, hooks, formatos que repiten en los winners

2. **Trending externo:**
   - **Google Trends** (free, via `pytrends` library) — términos en alza sobre perfumes en Colombia
   - **Google Keyword Planner** (via Ads API, free) — search volume + competition de queries relacionadas
   - Google Trends puede mostrar "rising queries" y "breakout terms" (>5000% spike)

3. **Competitor spy:**
   - **Meta Ad Library API** (free, public) — ads activos de competidores (Encanto, Macondo, Tienda Stork). Saber qué hooks usan + formatos.
   - Si está habilitado, **TikTok Creative Center** o **TikTok Ad Library** — viral patterns.

4. **AI synthesis:**
   - Claude (LLM call) recibe: winners propios + trending + competidores + avatares M4 → genera 5-10 recomendaciones con:
     - Hook copy (3 variantes)
     - Visual concept (descripción para Higgsfield/banana)
     - Avatar target (de los 5 M4)
     - Funnel stage (prospecting / retargeting / retention)
     - Justificación basada en data ("Tom Ford Oud Wood es el #1 SKU pero solo aparece en 2 ads activos → angle 'descubrí el #1 más pedido'")

**Por qué NO Google Search directo:**
- No tiene API pública gratis
- Lo que vos querías saber (qué buscan/quieren) se cubre mejor con Google Trends + Keyword Planner
- Para "qué está hablando la gente" sirven más Reddit search + X/Twitter API (paga)

**UI:**
```
PRÓXIMAS PUBLICACIONES · 8 recomendaciones · regenerated daily

[1] 🎯 Hook: "El Oud que vendí 34 veces este mes — y casi nadie lo conoce"
    Visual: Bottle close-up con luz dramática warm
    Avatar: 2 · Hombre de Nicho
    Funnel: Prospecting
    Why: S832 es #1 vendido pero solo en 2 ads activos · spend $0
    Confidence: 87%
```

**Archivos:**
- `extract_trends.py` (nuevo) — pytrends + keyword planner via Google Ads MCP
- `extract_competitors.py` (nuevo) — Meta Ad Library scrape
- `recommend.py` (nuevo) — LLM call con prompt template
- `templates/dashboard.html.j2` — sección "Próximas publicaciones"

---

## Pack D · UI Pro polish (~2 h)

- **Sparkline charts** mini en KPI cards (tendencia 7 días)
- **Day-over-day deltas** con flechas ↑↓ + % cambio
- **Real-time alerts badges** (campañas que cayeron >30% vs ayer)
- **Sticky topbar** con quick filters (last 24h / 7d / 30d toggles)
- **Skeleton loading** states (para cuando el JSON viene de fetch async, no inline)
- **Print mode optimizado** (ya existe parcial)

---

## Pack E · Lo MUY pro (~6+ h)

- **Forecasting** próximos 7 días — regresión lineal sobre spend/revenue, banda de confianza
- **Anomaly detection** — z-score sobre métricas diarias. Auto-alerta cuando una campaña sale del rango histórico ±2σ
- **Unified attribution** — si una compra de Shopify tiene UTM con click previo de Meta Y Google, dividir el crédito 50/50 (modelo lineal) o weighted por tiempo. Probarlo con last-30d.
- **Daily digest** auto a Telegram/Email — top 3 cosas que pasaron + top 3 cosas que hay que hacer hoy

---

## Orden de ejecución sugerido

```
1. Pack G (cities)              · 30 min  · standalone
2. Pack B (Google Ads)          · 3-4h    · independiente
3. Pack A (Shopify)             · 3-4h    · independiente, paralelo con B
4. Pack C (Activity Feed)       · 4-5h    · requiere A + B (las 3 fuentes)
5. Pack F (Recommendations)     · 5-6h    · requiere A + B + winners propios
6. Pack D (UI polish)           · 2h      · cuando hay data densa
7. Pack E (Advanced)            · 6h+     · cuando todo lo de arriba existe
```

**Total estimado:** 24-28 horas de trabajo neto, divisible en 4-6 sesiones.

---

## Trampas a evitar

1. **Pixel value tracking gap** sigue activo — cuando metamos Shopify, calcular ROAS desde Shopify revenue, NO desde Meta `purchase_roas`. Ver [[reference_serene_pixel_tracking_gap]].
2. **Google Ads writes**: dashboard es READ-ONLY. Si en el futuro queremos que recomiende cambios + auto-aplicarlos, eso requiere CONFIRMO del user por cada write (regla CLAUDE.md).
3. **Shopify API rate limits**: GraphQL Admin tiene cost-per-query. No pullar 90 días de orders en un solo request, paginar.
4. **Meta `act_xxx/activities` retention**: solo 90 días. Almacenar localmente en `data/activity_log_*.json` para histórico más largo.
5. **MCP serene-ads-prod**: solo customer 4027382494 (Industrias Serene SAS). Nunca tocar 4012064923 (MCC).
6. **Tareas paralelas en curso**: DPA Meta setup está pausado esperando catalog_id (Task #16) y inventory tracking flow (Task #15). NO mezclar trabajo. Roadmap dashboard es proyecto independiente.

---

## Cómo retomar próxima sesión

**Trigger phrases:** "vamos con el dashboard pro", "Pack X", "continuemos con el roadmap del dashboard", "trazabilidad", "ciudades"

**Antes de empezar a codear cualquier pack:**
1. Leer este PLAN.md (en `/root/serene-dashboard/PLAN.md`)
2. Leer [[project_serene_ai_dashboard]] para estado técnico del engine
3. Verificar token Meta válido: `tr '\0' '\n' < /proc/$(pgrep -f meta-ads | head -1)/environ | grep META_ACCESS_TOKEN`
4. `cd /root/serene-dashboard && git pull origin main` (puede haber commits del bot diario)
