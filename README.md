# Serene · AI Performance Command Center

Engine Python que pulla datos de Meta Marketing API, los analiza con scoring 1–100,
detecta patterns (hidden winners, budget waste, frequency saturation, funnel leaks)
y genera un dashboard HTML estilo Stripe/Linear listo para subir a Drive o servir.

---

## Quick start (3 comandos)

```bash
# 1. Instalar deps
pip3 install -r requirements.txt --break-system-packages

# 2. Setear token (una sola vez)
export META_ACCESS_TOKEN="EAAxxx..."

# 3. Correr pipeline completo (default: cuenta producción, last 7 días)
./run_full.sh
```

Output: HTML generado en `output/`, subido al Drive `serene:` con nombre `Serene AI Dashboard <slug> - YYYY-MM-DD.html`.

---

## Estructura del proyecto

```
serene-dashboard/
├── config.py                 Account IDs, thresholds, paths
├── extract.py                Pulla Meta Graph API → snapshot JSON
├── analyze.py                Scoring + classifier + insights detector
├── render.py                 Jinja2 renderer
├── main.py                   Orquesta load → analyze → render → upload
├── run_full.sh               Pipeline completo (extract + main)
├── templates/
│   └── dashboard.html.j2     Template Stripe/Linear style
├── data/
│   ├── *.json                Snapshots (extracted by extract.py)
├── output/
│   └── dashboard_*.html      HTML generado
├── docs/
│   └── value_tracking_shopify.md   Doc para dev de Shopify
└── README.md                 Este archivo
```

---

## Setup

### 1. Python + dependencies

```bash
sudo apt install -y python3 python3-pip
pip3 install jinja2 requests --break-system-packages
```

### 2. rclone (para upload al Drive)

Ya viene configurado con remote `serene:` apuntando al Drive de `sereneleparfum.team@gmail.com`.

### 3. Meta Access Token

Necesitas un **System User Access Token** con permiso `ads_read` (mínimo) o `ads_management` (si quieres write futuro).

**Opción rápida — copiar el token actual del MCP:**

```bash
# El MCP meta-ads ya tiene un token válido. Para obtenerlo:
# Ve a Meta Business Manager → Configuración → Usuarios del sistema
# → Genera token con scope ads_read
export META_ACCESS_TOKEN="EAA..."
```

**Persistir entre sesiones:**

```bash
echo 'export META_ACCESS_TOKEN="EAA..."' >> ~/.bashrc
# o crear .env en el proyecto
echo 'META_ACCESS_TOKEN=EAA...' > .env
```

⚠ El token expira cada ~60 días. Renovarlo en Business Manager y actualizar la env var.

---

## Comandos comunes

### Generar dashboard de producción (last 7 días)

```bash
./run_full.sh
```

### Generar dashboard de cuenta específica + rango custom

```bash
./run_full.sh act_935968735451363 30  # cuenta dormante, last 30 días
```

### Solo extract (sin upload)

```bash
python3 extract.py act_1020250386264513 --since 2026-05-02 --until 2026-05-09
# output: data/1020250386264513_2026-05-02_to_2026-05-09.json
```

### Solo analyze + render desde un snapshot existente

```bash
python3 main.py 1020250386264513_2026-05-02_to_2026-05-09.json
# output: output/dashboard_*.html
```

### Análisis CLI rápido (sin generar HTML)

```bash
python3 analyze.py
# Imprime ranking de campañas + insights + recomendaciones
```

---

## Cuentas configuradas

| Slug | ID | Status | Notas |
|---|---|---|---|
| `serene_prod` | `act_1020250386264513` | active | Cuenta operativa M4 — "PRUEBA GABRIELA" |
| `serene_dormant` | `act_935968735451363` | dormant | "SERENE COL 2.0 CTA 2" — histórica feb 2026 |

---

## Cómo funciona el scoring 1–100

Cada campaña obtiene un score ponderado:

| Componente | Peso | Cómo se calcula |
|---|---|---|
| `cpa_relative` | 30% | CPA vs promedio cuenta. <0.5x = 100, 1.0x = 60, 2.0x = 0 |
| `ctr_relative` | 20% | CTR vs promedio cuenta. 2x = 100, 0.5x = 0 |
| `frequency_health` | 20% | 1.0–2.0 = 100, decae después de 2.5, 0 a partir de 4.0 |
| `spend_efficiency` | 15% | Purchases por $1k spend, normalizado |
| `stability` | 15% | Placeholder (necesita daily breakdown) |

### Status assignment

- **HIDDEN WINNER**: low spend (<5% del total) + CPA <50% del promedio
- **EFFICIENT**: CPA <90% del promedio
- **SCALE**: score ≥80 + bueno en otros KPIs
- **SCALE LATER**: freq >3.0 pero CPA bueno (saturado pero rentable)
- **MONITOR**: score 50–79
- **STOP**: 0 conversions con spend significativo, o freq >4.0, o score <50

### Thresholds editables

`config.py` → `THRESHOLDS = {...}`

---

## Insights detectados automáticamente

El analyzer corre 5 detectores sobre cada snapshot:

| Tipo | Trigger |
|---|---|
| `TRACKING_GAP` | `action_values` viene vacío del API → no hay value tracking |
| `HIDDEN_WINNER` | Campaña con CPA <50% del promedio + spend <5% del total |
| `BUDGET_WASTE` | Campaña con 0 purchases pero spend >0 |
| `FREQUENCY_ALERT` | Account-level frequency >2.5 |
| `FUNNEL_LEAK` | Cualquier paso del funnel pierde >70% |

Cada uno genera **recomendaciones priorizadas** (CRITICAL / HIGH / MEDIUM / LOW).

---

## Cron diario + Notificaciones

### Setup inicial

1. Copiar `.env.example` → `.env` y llenar `META_ACCESS_TOKEN` + algún backend de notificación
2. `chmod 600 .env` (lo cargamos automático en run_full.sh)

### Backends de notificación soportados (auto-detect, primero match wins)

| Backend | Env vars | Notas |
|---|---|---|
| **Telegram** (recomendado) | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Free, instant, supports markdown. Setup via @BotFather |
| **CallMeBot WhatsApp** | `CALLMEBOT_PHONE` + `CALLMEBOT_APIKEY` | Free, requires opt-in (mensaje a +34 644 38 87 54) |
| **Email SMTP** | `SMTP_HOST` + `SMTP_USER` + `SMTP_PASS` + `NOTIFY_TO` | Gmail App Password works |
| **Webhook** | `NOTIFY_WEBHOOK_URL` | Slack, Discord, custom |
| **WhatsApp fallback** | `WHATSAPP_NUMBER` | Imprime wa.me link a logs/console (manual) |

### Cron (estilo tradicional)

```bash
# Importar template
crontab cron/crontab.template

# O editar manualmente
crontab -e
# Agregar: 0 7 * * * cd /root/serene-dashboard && ./run_full.sh >> logs/cron.log 2>&1
```

### systemd (alternativa moderna, recomendado)

```bash
sudo cp cron/serene-dashboard.service /etc/systemd/system/
sudo cp cron/serene-dashboard.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now serene-dashboard.timer

# Verificar
systemctl status serene-dashboard.timer
journalctl -u serene-dashboard.service -n 50
```

### Probar notificación sin correr el pipeline

```bash
# Genera summary del último snapshot, NO envía
python3 notify.py --snapshot 1020250386264513_2026-05-02_to_2026-05-09.json --dry-run

# Envía si hay backend configurado en env vars
python3 notify.py --snapshot 1020250386264513_2026-05-02_to_2026-05-09.json --drive-link "https://..."
```

---

## Limitaciones actuales (conocidas)

| Limitación | Cómo desbloquear |
|---|---|
| No hay ROAS — solo CPA | Configurar value tracking en pixel Shopify (ver `docs/value_tracking_shopify.md`) |
| Stability score es placeholder | Pasar a usar daily breakdown (`time_increment=1`) |
| No detecta Post ID compartidos entre adsets | Implementar consolidation por `post_id` en `analyze.py` |
| No multi-cuenta combinada | Agregar función `merge_snapshots()` en `analyze.py` |
| Sin daily trends en KPI cards | Pull con `time_increment=1` y plotear sparklines reales |

---

## Troubleshooting

### `MetaAPIError: HTTP 190: Error validating access token`

El token expiró. Renovar en Business Manager y volver a exportar.

### `MetaAPIError: HTTP 100: Insufficient permissions`

El token no tiene `ads_read` para esa cuenta. Verificar Business Manager → Cuenta publicitaria → Personas asignadas.

### `rclone: command not found`

Instalar rclone y configurar el remote `serene` con OAuth Drive.

### El HTML se ve roto / sin estilos

El template usa CDN de Google Fonts. Si no hay internet, las fuentes no cargan. Funcional pero feo.

---

## Roadmap

- [ ] Value tracking habilitado → sección ROAS + profitability
- [ ] Cron + notificación WhatsApp/email
- [ ] Vistas internas (Campaigns, Post IDs, Audiences) como pages separadas
- [ ] Heatmap de performance por hora del día
- [ ] Comparativa account-vs-account inline
- [ ] Export PDF para reportes ejecutivos
- [ ] Hosting permanente en Vercel/Cloudflare Pages

---

## Stack

- **Python 3.12** + `requests` + `jinja2`
- **Meta Graph API v22.0** (insights, campaigns, ads)
- **rclone** para upload a Google Drive
- **Inter Tight + Instrument Serif + JetBrains Mono** (Google Fonts)
- **Pure SVG** para sparklines (sin libs de charts)

Sin frameworks frontend. Sin backend. Sin deps de JavaScript.

---

## Versiones

- `v1.0` (2026-05-09) — Mock data inicial
- `v2.0` (2026-05-09) — Cuenta dormante con data real
- `v3.0` (2026-05-09) — Cuenta producción + engine Python completo · **CURRENT**

---

## Contacto

Generado por Serene AI Performance · luiscala / sereneleparfum.team
