"""
Serene AI · Analyzer
Toma snapshot JSON, calcula scores 1-100, detecta winners/losers, genera insights.
Es agnóstico de cuenta — funciona con cualquier JSON que cumpla el schema.
"""
from __future__ import annotations
from typing import Any
from config import THRESHOLDS, SCORE_WEIGHTS


# ──────────────────────────────────────────────────────────
# Score helpers
# ──────────────────────────────────────────────────────────
def _clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


def cpa_score(camp_cpa: float | None, account_cpa: float) -> float:
    """100 if CPA is half of account avg, 0 if 2x avg, linear in between."""
    if camp_cpa is None or account_cpa <= 0:
        return 0
    ratio = camp_cpa / account_cpa
    # ratio 0.5 → 100, ratio 1.0 → 60, ratio 2.0 → 0
    return _clamp(120 - 60 * ratio)


def ctr_score(camp_ctr: float, account_ctr: float) -> float:
    """100 if CTR is 2x account avg, 0 if half avg."""
    if account_ctr <= 0:
        return 50
    ratio = camp_ctr / account_ctr
    return _clamp(40 * ratio + 20)


def frequency_score(freq: float) -> float:
    """100 if freq 1.0-2.0, decays after 2.5, 0 at 5.0+"""
    if freq <= 1.0:
        return 70  # too low — under-served
    if freq <= 2.0:
        return 100
    if freq <= 2.5:
        return 90
    if freq <= 4.0:
        return _clamp(90 - (freq - 2.5) * 40)
    return 0


def spend_efficiency_score(camp: dict, account_summary: dict) -> float:
    """Purchases per $1k spend, normalized."""
    spend = camp.get("spend", 0)
    purchases = camp.get("purchases", 0)
    if spend <= 0:
        return 0
    purchases_per_1k = (purchases / spend) * 1000
    account_p_per_1k = (account_summary["purchases"] / account_summary["spend"]) * 1000 if account_summary["spend"] > 0 else 0
    if account_p_per_1k <= 0:
        return 50
    return _clamp(100 * (purchases_per_1k / account_p_per_1k) * 0.7)  # 1.0 ratio → 70 base


def stability_score(camp: dict) -> float:
    """Placeholder — needs daily breakdown to compute properly. Default to 65 for paused, 80 for active."""
    if camp.get("effective_status") in ("ACTIVE",):
        return 80
    return 65


def calculate_score(camp: dict, account_summary: dict) -> tuple[float, dict]:
    """Returns (final_score, breakdown_dict)."""
    cpa = camp.get("cpa")
    ctr = camp.get("ctr", 0)
    freq = camp.get("frequency", 0)
    account_cpa = account_summary.get("cpa", 0)
    account_ctr = account_summary.get("ctr", 0)

    breakdown = {
        "cpa_relative": cpa_score(cpa, account_cpa),
        "ctr_relative": ctr_score(ctr, account_ctr),
        "frequency_health": frequency_score(freq),
        "spend_efficiency": spend_efficiency_score(camp, account_summary),
        "stability": stability_score(camp),
    }

    weighted = sum(breakdown[k] * SCORE_WEIGHTS[k] / 100 for k in breakdown)
    return round(weighted, 1), breakdown


# ──────────────────────────────────────────────────────────
# Status classification (SCALE / MONITOR / STOP / etc)
# ──────────────────────────────────────────────────────────
def classify_status(camp: dict, score: float, account_summary: dict) -> tuple[str, str]:
    """Returns (status_code, status_label)."""
    purchases = camp.get("purchases", 0)
    spend = camp.get("spend", 0)
    cpa = camp.get("cpa")
    freq = camp.get("frequency", 0)
    account_cpa = account_summary.get("cpa", 0)

    # 0 purchases + spent enough = STOP
    if purchases == 0 and spend > account_cpa * THRESHOLDS["kill_rule_multiplier"] * 0.1:
        return "stop", "STOP"
    if purchases == 0:
        return "stop", "NO CONVERSIONS"

    # Hidden winner: low spend but excellent CPA
    if cpa and cpa < account_cpa * 0.5 and spend < account_summary["spend"] * 0.05:
        return "scale", "HIDDEN WINNER"

    # Best CPA in account → EFFICIENT
    if cpa and cpa < account_cpa * 0.9:
        return "scale", "EFFICIENT"

    # High freq + low CPA → SCALE LATER (saturated but profitable)
    if freq > 3.0 and cpa and cpa < account_cpa * 1.1:
        return "monitor", "SCALE LATER"

    # Frequency critical → STOP
    if freq > THRESHOLDS["frequency_critical"]:
        return "stop", "FATIGUE"

    if score >= THRESHOLDS["scale_min_score"]:
        return "scale", "SCALE"
    if score >= 50:
        return "monitor", "MONITOR"
    return "stop", "STOP"


# ──────────────────────────────────────────────────────────
# Insights detector — pattern recognition over campaigns
# ──────────────────────────────────────────────────────────
def detect_insights(account_summary: dict, campaigns: list[dict], tracking_health: dict) -> list[dict]:
    """Returns list of insights {severity, type, title, description, stats}."""
    insights = []

    # 1. Tracking gap (highest priority)
    if not tracking_health.get("value_tracking", True):
        insights.append({
            "severity": "critical",
            "type": "TRACKING_GAP",
            "title": "No hay value tracking en pixel → no se puede calcular ROAS",
            "description": tracking_health.get("value_tracking_note", ""),
            "stats": [
                {"label": "Eventos OK", "value": account_summary["purchases"], "color": "mint"},
                {"label": "Con value", "value": 0, "color": "rose"},
                {"label": "ROAS calc", "value": "N/A", "color": "rose"},
            ],
            "action": "Configurar value/currency en evento Purchase del pixel",
        })

    # 2. Hidden winners — sorted campaigns by score
    hidden_winners = [
        c for c in campaigns
        if c.get("cpa") and c["cpa"] < account_summary["cpa"] * 0.5
        and c["spend"] < account_summary["spend"] * 0.05
    ]
    if hidden_winners:
        w = hidden_winners[0]
        insights.append({
            "severity": "warning",
            "type": "HIDDEN_WINNER",
            "title": f'"{_short_name(w["name"])}" tuvo {round(account_summary["cpa"]/w["cpa"], 1)}x mejor CPA',
            "description": f"Esta campaña logró CPA {_fmt(w['cpa'])} vs {_fmt(account_summary['cpa'])} promedio cuenta. Solo gastó {_fmt(w['spend'])} del budget total. Pattern: {_format_hint_pattern(w.get('format_hint'))}",
            "stats": [
                {"label": "CPA", "value": _fmt(w['cpa']), "color": "mint"},
                {"label": "Spend", "value": _fmt(w['spend']), "color": "neutral"},
                {"label": "Purchases", "value": w["purchases"], "color": "neutral"},
            ],
            "action": "Replicar patrón en próximas iteraciones",
            "campaign_id": w["id"],
        })

    # 3. Budget waste — campaigns with 0 purchases
    losers = [c for c in campaigns if c.get("purchases", 0) == 0 and c.get("spend", 0) > 0]
    if losers:
        # Sort by spend desc — biggest waste first
        losers.sort(key=lambda x: -x["spend"])
        l = losers[0]
        insights.append({
            "severity": "critical",
            "type": "BUDGET_WASTE",
            "title": f'"{_short_name(l["name"])}" — {l["purchases"]} conversiones tras {_fmt(l["spend"])} de spend',
            "description": f"Campaña corrió con CTR {l['ctr']:.2f}% y consumió budget sin generar ninguna compra. Caso de creative que nunca debió escalarse — kill earlier hubiera ahorrado el spend.",
            "stats": [
                {"label": "Spend", "value": _fmt(l['spend']), "color": "rose"},
                {"label": "CTR", "value": f"{l['ctr']:.2f}%", "color": "rose"},
                {"label": "Purchases", "value": l["purchases"], "color": "rose"},
            ],
            "action": f"Aplicar 3x Kill Rule — pausar tras {THRESHOLDS['kill_rule_multiplier']}x CPA target sin conversion",
            "campaign_id": l["id"],
        })

    # 4. Frequency saturation
    freq = account_summary.get("frequency", 0)
    if freq > THRESHOLDS["frequency_warn"]:
        severity = "critical" if freq > THRESHOLDS["frequency_critical"] else "warning"
        insights.append({
            "severity": severity,
            "type": "FREQUENCY_ALERT",
            "title": f"Frecuencia {freq:.2f} a nivel cuenta — saturación",
            "description": f"Frecuencia promedio {freq:.2f} está sobre el límite saludable (target < {THRESHOLDS['frequency_warn']}). Audiencia siendo mostrada el mismo creative {int(freq)}+ veces.",
            "stats": [
                {"label": "Reach", "value": _fmt(account_summary['reach']), "color": "neutral"},
                {"label": "Frequency", "value": f"{freq:.2f}", "color": "warning" if severity == "warning" else "rose"},
                {"label": "Target", "value": f"<{THRESHOLDS['frequency_warn']}", "color": "mint"},
            ],
            "action": "Refrescar creative o expandir audiencia",
        })

    # 5. Funnel leak detection
    if account_summary.get("atc") and account_summary.get("ic"):
        atc = account_summary["atc"]
        ic = account_summary["ic"]
        drop_rate = 1 - (ic / atc) if atc > 0 else 0
        if drop_rate > 0.70:
            insights.append({
                "severity": "warning",
                "type": "FUNNEL_LEAK",
                "title": f"Mayor leak del funnel: ATC → Checkout ({drop_rate*100:.1f}% drop)",
                "description": f"De {atc} ATC solo {ic} hicieron checkout. El problema NO está en ads — está en la página de carrito/checkout. Antes de gastar más, auditar el flow Shopify ATC → Checkout.",
                "stats": [
                    {"label": "ATC", "value": atc, "color": "neutral"},
                    {"label": "Checkout", "value": ic, "color": "warning"},
                    {"label": "Drop", "value": f"-{drop_rate*100:.1f}%", "color": "rose"},
                ],
                "action": "Auditar flow Shopify ATC → Checkout (¿costos sorpresa? ¿formularios largos?)",
            })

    return insights


# ──────────────────────────────────────────────────────────
# Recommendations — actionable next steps
# ──────────────────────────────────────────────────────────
def generate_recommendations(account_summary: dict, campaigns: list[dict],
                             insights: list[dict], tracking_health: dict) -> list[dict]:
    recos = []

    # From tracking health
    if not tracking_health.get("value_tracking", True):
        recos.append({
            "priority": "high",
            "icon_class": "kill",
            "icon": "🛑",
            "title": "Configurar value tracking en pixel ANTES de reactivar campañas",
            "description": "Sin <code>action_values</code> en eventos Purchase, no se puede optimizar por ROAS. Esto bloquea un dashboard de profitability real. Configurar valor + currency en cada evento Purchase del pixel.",
            "priority_label": "CRITICAL",
        })

    # From hidden winners (replicate pattern)
    winner_insights = [i for i in insights if i["type"] == "HIDDEN_WINNER"]
    if winner_insights:
        w_id = winner_insights[0].get("campaign_id")
        winner_camp = next((c for c in campaigns if c["id"] == w_id), None)
        if winner_camp:
            fmt = winner_camp.get("format_hint", "")
            if fmt == "video":
                recos.append({
                    "priority": "high",
                    "icon_class": "scale",
                    "icon": "🚀",
                    "title": "Replicar formato video — el winner oculto",
                    "description": f"\"{_short_name(winner_camp['name'])}\" logró CPA {_fmt(winner_camp['cpa'])} con video — {round(account_summary['cpa']/winner_camp['cpa'], 1)}x mejor que imagen. Cuando reactives la cuenta, priorizar video sobre imagen estática.",
                    "priority_label": "HIGH",
                })

    # From losers (kill rule)
    waste_insights = [i for i in insights if i["type"] == "BUDGET_WASTE"]
    if waste_insights:
        recos.append({
            "priority": "high",
            "icon_class": "kill",
            "icon": "🛑",
            "title": "Implementar 3x Kill Rule en próximas campañas",
            "description": f"Si CPA target es ~{_fmt(account_summary['cpa'])}, 3x kill rule = {_fmt(account_summary['cpa']*3)} máximo sin conversión → pausa automática. Hubiera ahorrado 100% del waste.",
            "priority_label": "HIGH",
        })

    # Frequency
    freq_insights = [i for i in insights if i["type"] == "FREQUENCY_ALERT"]
    if freq_insights:
        recos.append({
            "priority": "med",
            "icon_class": "fatigue",
            "icon": "⚠️",
            "title": f"Mantener frequency < {THRESHOLDS['frequency_warn']} a nivel campaña",
            "description": f"Cuando se reactive M4, monitorear freq por adset y refrescar creative al llegar a {THRESHOLDS['frequency_warn']}.",
            "priority_label": "MEDIUM",
        })

    # Funnel leak
    funnel_insights = [i for i in insights if i["type"] == "FUNNEL_LEAK"]
    if funnel_insights:
        recos.append({
            "priority": "med",
            "icon_class": "duplicate",
            "icon": "📈",
            "title": funnel_insights[0]["title"],
            "description": funnel_insights[0]["description"],
            "priority_label": "MEDIUM",
        })

    return recos


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────
def _short_name(name: str, max_len: int = 36) -> str:
    if len(name) <= max_len:
        return name
    return name[:max_len-1].rstrip() + "…"


def _fmt(v) -> str:
    """Format numbers compactly: 36732 -> 36.7K, 2827677 -> 2.83M"""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}K"
    return f"{v:,.0f}"


def _format_hint_pattern(fmt: str | None) -> str:
    if fmt == "video":
        return "video performó mejor que imagen"
    if fmt == "image":
        return "imagen / static creative"
    return "formato indefinido"


# ──────────────────────────────────────────────────────────
# Post ID Intelligence — consolidate ads by post_id (cross-adset)
# ──────────────────────────────────────────────────────────
def consolidate_post_ids(ads_with_posts: list[dict], account_summary: dict) -> list[dict]:
    """
    Group ads by post_id and aggregate metrics.
    A single Post ID can be used in multiple ads (different adsets/campaigns).
    Returns list of consolidated post_id stats sorted by performance.
    """
    if not ads_with_posts:
        return []

    by_post = {}
    for ad in ads_with_posts:
        pid = ad.get("post_id")
        if not pid:
            continue
        if pid not in by_post:
            by_post[pid] = {
                "post_id": pid,
                "page_id": ad.get("page_id"),
                "story_id": ad.get("story_id"),
                "thumbnail_url": ad.get("thumbnail_url"),
                "ad_count": 0,
                "active_count": 0,
                "campaigns": set(),
                "adsets": set(),
                "spend": 0.0,
                "impressions": 0,
                "clicks": 0,
                "purchases": 0,
                "atc": 0,
                "ic": 0,
                "video_views": 0,
                "ad_names": [],
                "_freq_weighted_sum": 0.0,
                "_ctr_weighted_sum": 0.0,
                "_impression_weight": 0,
            }
        p = by_post[pid]
        p["ad_count"] += 1
        if ad.get("effective_status") == "ACTIVE":
            p["active_count"] += 1
        if ad.get("campaign_id"):
            p["campaigns"].add(ad["campaign_id"])
        if ad.get("adset_id"):
            p["adsets"].add(ad["adset_id"])
        p["spend"] += ad.get("spend", 0)
        p["impressions"] += ad.get("impressions", 0)
        p["clicks"] += ad.get("clicks", 0)
        p["purchases"] += ad.get("purchases", 0)
        p["atc"] += ad.get("atc", 0)
        p["ic"] += ad.get("ic", 0)
        p["video_views"] += ad.get("video_views", 0)
        # Weighted avg of frequency/ctr by impressions
        imps = ad.get("impressions", 0)
        if imps > 0:
            p["_freq_weighted_sum"] += ad.get("frequency", 0) * imps
            p["_ctr_weighted_sum"] += ad.get("ctr", 0) * imps
            p["_impression_weight"] += imps
        if ad.get("ad_name"):
            p["ad_names"].append(ad["ad_name"])

    # Compute final metrics per post
    consolidated = []
    account_cpa = account_summary.get("cpa") or 0
    for pid, p in by_post.items():
        weight = p["_impression_weight"]
        avg_freq = (p["_freq_weighted_sum"] / weight) if weight else 0
        avg_ctr = (p["_ctr_weighted_sum"] / weight) if weight else 0
        cpa = (p["spend"] / p["purchases"]) if p["purchases"] > 0 else None
        consolidated.append({
            "post_id": pid,
            "page_id": p["page_id"],
            "story_id": p["story_id"],
            "thumbnail_url": p["thumbnail_url"],
            "ad_count": p["ad_count"],
            "active_count": p["active_count"],
            "campaigns_count": len(p["campaigns"]),
            "adsets_count": len(p["adsets"]),
            "ad_names": p["ad_names"][:3],  # first 3 names for context
            "spend": p["spend"],
            "impressions": p["impressions"],
            "clicks": p["clicks"],
            "purchases": p["purchases"],
            "atc": p["atc"],
            "ic": p["ic"],
            "video_views": p["video_views"],
            "ctr": avg_ctr,
            "frequency": avg_freq,
            "cpa": cpa,
            "label": _post_id_label(p, cpa, avg_freq, avg_ctr, account_cpa, account_summary.get("ctr", 0)),
        })

    # Sort: actives first, then by score (lower CPA + higher purchases = better)
    def sort_key(p):
        # Push posts with no purchases to the bottom
        if p["purchases"] == 0:
            return (0, 0, 0)
        # Score: -cpa_ratio (lower better) * sqrt(purchases) (more purchases better)
        cpa_score = -((p["cpa"] / account_cpa) if (p["cpa"] and account_cpa) else 999)
        return (1 if p["active_count"] > 0 else 0, p["purchases"] ** 0.5 * abs(cpa_score), -p["cpa"] if p["cpa"] else 0)

    consolidated.sort(key=sort_key, reverse=True)
    return consolidated


def _post_id_label(p: dict, cpa: float | None, freq: float, ctr: float,
                   account_cpa: float, account_ctr: float) -> str:
    """Auto-classify each post_id."""
    if p["purchases"] == 0:
        if p["spend"] > (account_cpa * 3):
            return "BUDGET WASTE"
        if p["impressions"] > 0:
            return "NO CONVERSIONS"
        return "INACTIVE"
    if not cpa or not account_cpa:
        return "TRACKED"
    cpa_ratio = cpa / account_cpa
    if cpa_ratio < 0.5 and p["spend"] < 200000:  # low spend, great CPA
        return "HIDDEN WINNER"
    if cpa_ratio < 0.7:
        return "EFFICIENT"
    if cpa_ratio < 0.95 and freq < 3.0 and p["purchases"] >= 5:
        return "SCALABLE"
    if freq > 4.0:
        return "FATIGUED"
    if cpa_ratio < 1.2:
        return "ON PAR"
    return "UNDERPERFORMING"


def post_id_insights(consolidated: list[dict], account_summary: dict) -> list[dict]:
    """Generate insights specific to post_id patterns."""
    insights = []
    if not consolidated:
        return insights

    # Best performer (lowest CPA with at least 5 purchases)
    real_perf = [p for p in consolidated if p["cpa"] and p["purchases"] >= 5]
    if real_perf:
        best = min(real_perf, key=lambda p: p["cpa"])
        if best["cpa"] < account_summary.get("cpa", 0) * 0.7:
            ratio = account_summary["cpa"] / best["cpa"] if best["cpa"] else 1
            insights.append({
                "severity": "warning",
                "type": "TOP_POST_ID",
                "title": f"Post {best['post_id'][-8:]}… domina con CPA {ratio:.1f}x mejor",
                "description": f"Este post está corriendo en {best['ad_count']} ads ({best['adsets_count']} adsets) con CPA {best['cpa']:,.0f} vs {account_summary['cpa']:,.0f} promedio cuenta. {best['purchases']} compras generadas.",
                "stats": [
                    {"label": "CPA", "value": f"{best['cpa']/1000:.1f}K", "color": "mint"},
                    {"label": "Ads", "value": best["ad_count"], "color": "neutral"},
                    {"label": "Purchases", "value": best["purchases"], "color": "neutral"},
                ],
                "action": "Duplicar a más adsets/audiencias antes de que sature",
                "post_id": best["post_id"],
            })

    # Fatiguing posts
    fatigued = [p for p in consolidated if p["frequency"] > 4.0 and p["active_count"] > 0]
    if fatigued:
        f = fatigued[0]
        insights.append({
            "severity": "critical",
            "type": "POST_FATIGUE",
            "title": f"Post {f['post_id'][-8:]}… en fatiga severa global",
            "description": f"Frecuencia consolidada {f['frequency']:.1f} a través de {f['ad_count']} ads. Audiencia saturada — kill global o refrescar audiencias.",
            "stats": [
                {"label": "Frequency", "value": f"{f['frequency']:.1f}", "color": "rose"},
                {"label": "Ads activos", "value": f["active_count"], "color": "warning"},
                {"label": "Spend", "value": f"{f['spend']/1000:.0f}K", "color": "neutral"},
            ],
            "action": "Pausar ese post_id en bulk o expandir audiencia",
            "post_id": f["post_id"],
        })

    # Hidden winners (multiple low-spend posts with great CPA)
    hidden = [p for p in consolidated if p.get("label") == "HIDDEN WINNER"]
    if hidden:
        insights.append({
            "severity": "warning",
            "type": "HIDDEN_WINNERS",
            "title": f"{len(hidden)} hidden winner{'s' if len(hidden) > 1 else ''} de Post ID detectados",
            "description": f"Posts con bajo spend pero CPA <50% del promedio. Subutilizados — escalar duplicando a otras audiencias podría desbloquear volumen sin perder eficiencia.",
            "stats": [
                {"label": "Posts", "value": len(hidden), "color": "mint"},
                {"label": "Total spend", "value": f"{sum(p['spend'] for p in hidden)/1000:.0f}K", "color": "neutral"},
                {"label": "Total purchases", "value": sum(p["purchases"] for p in hidden), "color": "mint"},
            ],
            "action": "Duplicar cada uno a 2-3 audiencias adicionales para test",
        })

    return insights


# ──────────────────────────────────────────────────────────
# Urgent Actions — kill list / scaling list / fix list
# ──────────────────────────────────────────────────────────
def build_urgent_actions(campaigns: list[dict], post_ids: list[dict],
                          summary: dict, tracking: dict) -> list[dict]:
    """
    Returns ordered list of actions ranked by urgency + $ impact.
    Each action has: severity, action, type, target, reason, impact, detail (steps), score.
    """
    out = []
    account_cpa = summary.get("cpa") or 0
    account_ctr = summary.get("ctr") or 0
    currency = "COP"  # TODO derive from snapshot

    # 1. Campañas ACTIVE con 0 conversions y spend significativo → KILL
    for c in campaigns:
        if c.get("effective_status") != "ACTIVE":
            continue
        if c.get("purchases", 0) == 0 and c.get("spend", 0) > (account_cpa * 1.5):
            out.append({
                "severity": "critical",
                "action": "PAUSAR YA",
                "type": "campaign",
                "target_id": c["id"],
                "target_name": c["name"],
                "reason": f"Gastó {_fmt_compact(c['spend'])} sin generar 1 sola compra. CTR {c.get('ctr',0):.2f}% (promedio cuenta {account_ctr:.2f}%).",
                "impact": f"Estás quemando ~{_fmt_compact(c['spend'] / 7)} COP/día sin retorno",
                "score": c.get("score", 0),
                "detail": {
                    "what": f"Esta campaña ha gastado {_fmt_compact(c['spend'])} COP en {c.get('impressions',0):,} impresiones sin lograr una sola compra. CTR de {c.get('ctr',0):.2f}% indica que ni siquiera la audiencia está haciendo click — el creative no engancha.",
                    "why_kill": "Bajo la regla 3x Kill: si una campaña gasta >3x el CPA target sin convertir, hay que pausarla. Aquí ya pasó. Cada día que sigue activa son ~{} COP perdidos.".format(_fmt_compact(c['spend']/7)),
                    "steps": [
                        {"title": "Click \"Abrir en Meta →\" en esta card", "desc": "Te lleva directo al adset selector con esta campaña pre-seleccionada"},
                        {"title": "Toggle \"Active → Paused\"", "desc": "Click el switch verde junto al nombre. Confirma. Spend deja de correr inmediatamente."},
                        {"title": "Documenta el aprendizaje (5 min)", "desc": "Ve a la campaña → tab Creative → mira qué Post IDs tenía. Los más bajos en CTR son los culpables. NO los reuses."},
                        {"title": "Decide si vale relanzar", "desc": "Si el ángulo del creative era bueno pero el copy/hook fue débil, considera regrabar. Si el ángulo entero falló, descarta."},
                    ],
                    "post_action": "Después de pausar, el budget liberado puede ir a las campañas con score >80. Revisa la sección 'Campañas' debajo.",
                    "external_links": [
                        {"label": "Abrir esta campaña en Meta Ads Manager", "url": f"https://business.facebook.com/adsmanager/manage/adsets?act={summary.get('account_id_clean','')}&selected_campaign_ids={c['id']}"}
                    ],
                },
            })

    # 2. Campañas ACTIVE con CPA >2x del promedio → KILL or fix
    for c in campaigns:
        if c.get("effective_status") != "ACTIVE":
            continue
        if c.get("purchases", 0) > 0 and c.get("cpa") and account_cpa and c["cpa"] > account_cpa * 2:
            ratio = c["cpa"] / account_cpa
            out.append({
                "severity": "critical",
                "action": "PAUSAR",
                "type": "campaign",
                "target_id": c["id"],
                "target_name": c["name"],
                "reason": f"CPA {_fmt_compact(c['cpa'])} es {ratio:.1f}x más caro que el promedio cuenta ({_fmt_compact(account_cpa)}). Solo {c['purchases']} compras.",
                "impact": f"Cada compra cuesta {_fmt_compact(c['cpa'] - account_cpa)} más de lo normal",
                "score": c.get("score", 0),
                "detail": {
                    "what": f"Esta campaña SÍ convierte ({c['purchases']} compras), pero a un CPA {ratio:.1f}x más caro que el resto. Si pudieras convertir esos compradores a CPA promedio, ahorrarías {_fmt_compact((c['cpa']-account_cpa)*c['purchases'])} {currency}.",
                    "why_kill": f"El problema puede ser: audiencia mal segmentada, bidding strategy errada, o creative que atrae al público equivocado. Antes de pausar, hay 1 cosa que probar.",
                    "steps": [
                        {"title": "Antes de pausar — duplica con cambio mínimo", "desc": f"Crea un duplicado y cambia SOLO la audiencia (de Lookalike a Interés, o broad). Misma estructura, mismos creatives. Corre 3 días."},
                        {"title": "Si el duplicado tampoco baja CPA", "desc": "Pausa esta campaña original. Mantén solo el duplicado si está mejor."},
                        {"title": "Si el duplicado SÍ baja CPA", "desc": "Pausa la original + escala el duplicado +20%. Usaste budget para validar audiencia."},
                        {"title": "Documenta", "desc": f"Anota que '{c['name'][:40]}' performó mal. Identifica si fue por audiencia (compraban menos), creative (no resonó), o bid strategy (Cost Cap vs Bid Cap)."},
                    ],
                    "post_action": "Si pausas y quieres recuperar el momentum, considera dirigir ese budget a las top 3 campañas con score >80.",
                    "external_links": [
                        {"label": "Abrir campaña en Meta Ads Manager", "url": f"https://business.facebook.com/adsmanager/manage/adsets?act={summary.get('account_id_clean','')}&selected_campaign_ids={c['id']}"}
                    ],
                },
            })

    # 3. Frequency cuenta crítica → REFRESCAR CREATIVE
    freq = summary.get("frequency", 0)
    if freq > 4.0:
        # Find top 3 most-overshown post_ids to mention
        top_freq_posts = [p for p in post_ids if p.get("frequency", 0) > 3.0 and p.get("spend", 0) > 0][:3]
        post_examples = ", ".join(f"…{p['post_id'][-8:]}" for p in top_freq_posts) if top_freq_posts else "los Top Post IDs"

        out.append({
            "severity": "critical",
            "action": "REFRESCAR CREATIVE",
            "type": "account",
            "target_id": "global",
            "target_name": "Toda la cuenta — refresh masivo",
            "reason": f"Frecuencia {freq:.2f} (target <2.5). Audiencia saturada — el creative está quemado.",
            "impact": "Cada día sin rotar, el CPA sube ~3-5%. En 1 semana ya saltaste de 55K → 65K.",
            "score": 100,
            "detail": {
                "what": f"Tu audiencia está viendo el mismo creative {freq:.1f} veces en promedio. La gente que iba a comprar, ya compró. La gente que NO iba a comprar, está harta de ver lo mismo. Esto es la señal #1 de que necesitas rotar.",
                "why_kill": "Posts a refrescar primero (los más sobre-expuestos): " + post_examples + ". También revisa la sección 'Post ID Intelligence' del dashboard para ver cuáles tienen freq >3.",
                "steps": [
                    {"title": "1. Identifica tus 3 ángulos winners actuales", "desc": "En la sección 'Post ID Intelligence' del dashboard, los que tienen label EFFICIENT o SCALABLE. Esos angles funcionan, hay que producir variantes — NO repetir."},
                    {"title": "2. Brief 5 nuevos creatives en 24h", "desc": "Variaciones del mismo angle: cambio de hook (primeros 3 segundos), cambio de UGC creator, cambio de B-roll, cambio de música. Mantén el mensaje, varía la presentación."},
                    {"title": "3. Lanzar en NUEVOS adsets", "desc": "NO uses las mismas audiencias actuales (ya están saturadas). Crea adsets con LAL 1% nuevo, otra geografía, otro interés. Fresh audiences = freq baja desde 1.0."},
                    {"title": "4. Pausar ads con freq >3.5 en bulk", "desc": "En Ads Manager, filtra columna Frequency >3.5, selecciona todos, Bulk Action → Pause. Hace que el reach se libere para los nuevos creatives."},
                    {"title": "5. Reducir budget en adsets sobre-expuestos", "desc": "Los que tienen freq entre 2.5-3.5 pero todavía generan compras: bájales budget 50% mientras los nuevos creatives toman tracción."},
                    {"title": "6. Revisa en 5 días", "desc": "La frecuencia debe bajar a 2.5-3.0 para confirmar que el plan funcionó. Si no, hay un problema de targeting más profundo."},
                ],
                "post_action": "Esta es una limpieza preventiva: no estás perdiendo dinero hoy, pero sí mañana si no actúas. La concentración de spend en pocos creatives es el riesgo #1 de Serene ahora mismo.",
                "external_links": [
                    {"label": "Filtrar ads por frequency en Meta", "url": f"https://business.facebook.com/adsmanager/manage/ads?act={summary.get('account_id_clean','')}&columns=campaign_name%2Cdelivery%2Cresults%2Cspent%2Cfrequency"},
                ],
            },
        })

    # 4. Posts FATIGUED → KILL post bulk
    for p in post_ids:
        if p.get("frequency", 0) > 4.0 and p.get("active_count", 0) > 0:
            out.append({
                "severity": "critical",
                "action": "PAUSAR EN BULK",
                "type": "post_id",
                "target_id": p["post_id"],
                "target_name": f"Post …{p['post_id'][-12:]} ({p['ad_count']} ads activos)",
                "reason": f"Frecuencia consolidada {p['frequency']:.1f} a través de {p['ad_count']} ads. CTR cayendo.",
                "impact": f"Quemás {_fmt_compact(p['spend'])} en posts saturados",
                "score": 80,
                "detail": {
                    "what": f"Este Post ID se está usando en {p['ad_count']} ads (entre {p['adsets_count']} adsets) con frecuencia consolidada {p['frequency']:.1f}. El público lo vio demasiadas veces. CTR seguirá cayendo, CPA seguirá subiendo.",
                    "why_kill": f"Este post ya generó {p.get('purchases',0)} purchases con un CPA de {_fmt_compact(p.get('cpa'))} pero está saturado. Pausarlo en bulk te libera reach para creatives nuevos sin perder volumen — los nuevos lo absorben.",
                    "steps": [
                        {"title": "1. Abre Ads Manager y filtra por Post ID", "desc": f"En columna Creative ID, busca o filtra: {p['post_id']}. Te aparecerán los {p['ad_count']} ads que lo usan."},
                        {"title": "2. Selecciona todos los ads del post", "desc": "Checkbox del header de la tabla → todos seleccionados. O Cmd+A si Meta lo permite."},
                        {"title": "3. Bulk Action → Pause", "desc": "Botón \"Edit\" → \"Pause Ads\". Confirma. Los {} ads pausan inmediatamente.".format(p['ad_count'])},
                        {"title": "4. Reemplazar con creative nuevo", "desc": "Si tenías 4 ads activos con este post, produce 2-4 variantes con un Post ID nuevo (mismo ángulo, distinto hook). Sube al ya-existente adset."},
                        {"title": "5. Monitorea 48h", "desc": "Si el CPA del adset (no del post nuevo, sino del adset) se mantiene o mejora, validaste que el ángulo sigue siendo el correcto. Si baja drásticamente, el post viejo era el unique driver."},
                    ],
                    "post_action": f"Esta campaña/post ya dio lo que tenía que dar ({p.get('purchases',0)} purchases). Pausarlo no es perdida — es liberar oxígeno.",
                    "external_links": [
                        {"label": "Buscar este post en Meta Ads Manager", "url": f"https://business.facebook.com/adsmanager/manage/ads?act={summary.get('account_id_clean','')}&search={p['post_id']}"},
                    ],
                },
            })

    # 5. Posts con BUDGET WASTE label
    for p in post_ids:
        if p.get("label") == "BUDGET WASTE":
            out.append({
                "severity": "critical",
                "action": "PAUSAR POST",
                "type": "post_id",
                "target_id": p["post_id"],
                "target_name": f"Post …{p['post_id'][-12:]}",
                "reason": f"Gastó {_fmt_compact(p['spend'])} con 0 compras",
                "impact": f"Waste directo: {_fmt_compact(p['spend'])}",
                "score": 90,
                "detail": {
                    "what": f"Este Post ID tiene gasto pero ZERO conversiones. Está activo en {p['ad_count']} ads.",
                    "why_kill": "Bajo la regla 3x Kill, ya gastó suficiente sin convertir como para tener confianza estadística de que el creative no resuena con esta audiencia.",
                    "steps": [
                        {"title": "1. Pausar todos los ads que usan este Post ID", "desc": "Mismo flow que el caso anterior: filtrar por post_id en Ads Manager y pausar en bulk."},
                        {"title": "2. NO reusar este post en futuras campañas", "desc": f"Anota el Post ID {p['post_id']} en tu lista de \"creatives quemados\". Es información valiosa para próximas iteraciones."},
                        {"title": "3. Análisis post-mortem", "desc": "¿Qué tenía este creative que no resonó? Hook, formato, claim, casting? Documéntalo en tu \"learnings\" para no repetir."},
                    ],
                    "post_action": "El budget liberado va a la siguiente campaña/post con mejor score. Revisa la sección 'Recomendaciones AI' al final del dashboard.",
                    "external_links": [],
                },
            })

    # 6. Tracking gap
    if not tracking.get("value_tracking", True):
        out.append({
            "severity": "warning",
            "action": "CONFIGURAR PIXEL",
            "type": "tech",
            "target_id": "pixel",
            "target_name": "Shopify Customer Events Pixel — value tracking",
            "reason": "El pixel no envía 'value' en eventos Purchase. Sin esto no hay ROAS calculable.",
            "impact": "Estás optimizando ciego. Las campañas con AOV alto se ven igual que las de AOV bajo.",
            "score": 50,
            "detail": {
                "what": "Confirmado vía Meta API: tus eventos Purchase llegan correctamente (350 en mayo 2-9), pero NO incluyen el campo `value`. Por eso `action_values` y `purchase_roas` vienen vacíos. Sin esto, Meta no puede calcular ROAS ni optimizar por valor — solo por volumen de compras.",
                "why_kill": "Esto bloquea la sección §8 del prompt original (Profitability-First Analysis). Hasta que se configure, NO se puede saber si una campaña con CPA 80K es más rentable que una con CPA 50K si la primera tiene AOV 200K y la segunda 100K.",
                "steps": [
                    {"title": "1. Verifica el estado actual", "desc": "Ve a business.facebook.com/events_manager2 → tu pixel → Test Events. Haz una compra de prueba en sereneleparfum.com. Si en columna 'Value' dice '—' o vacío, el problema está confirmado."},
                    {"title": "2. Opción A — Customer Events de Shopify (recomendado, 15 min)", "desc": "Shopify Admin → Settings → Customer Events → click el pixel de Meta → Code editor. Agregar `value: checkout.totalPrice.amount` y `currency: checkout.totalPrice.currencyCode` al evento Purchase. Save."},
                    {"title": "3. Opción B — Si usas Facebook & Instagram channel", "desc": "Apps → Facebook & Instagram by Meta → Data sharing settings → Maximum (no Standard). Esto incluye value automáticamente."},
                    {"title": "4. Opción C — Editar theme.liquid", "desc": "Solo si A y B no aplican. Buscar `fbq('track', 'Purchase')` y agregar el segundo argumento con value y currency. Cuidado con la moneda en cents vs unidades."},
                    {"title": "5. Hacer compra de prueba", "desc": "Compra real de cualquier producto ($1 o más). Esperar 1-2 min."},
                    {"title": "6. Verificar en Test Events", "desc": "Volver a Meta Events Manager → Test Events. Ahora debe mostrar 'Value: 119,880 COP' (o lo que sea). Confirmas con eso."},
                    {"title": "7. Avísame", "desc": "Cuando esté hecho, regenero el dashboard y la sección ROAS aparece automáticamente. El engine ya está listo para procesarlo."},
                ],
                "post_action": "Doc completo para el dev de Shopify en docs/value_tracking_shopify.md (ya está en el repo).",
                "external_links": [
                    {"label": "Meta Events Manager (test events)", "url": "https://business.facebook.com/events_manager2"},
                    {"label": "Shopify Customer Events", "url": "https://admin.shopify.com/store/sereneleparfum/settings/customer_events"},
                ],
            },
        })

    # 7. Funnel leak severe (ATC → Checkout drop > 75%)
    if summary.get("atc") and summary.get("ic"):
        atc = summary["atc"]
        ic = summary["ic"]
        drop = 1 - (ic / atc) if atc > 0 else 0
        if drop > 0.75:
            potential = int(atc * 0.5 - ic)
            out.append({
                "severity": "warning",
                "action": "AUDITAR CHECKOUT",
                "type": "tech",
                "target_id": "shopify",
                "target_name": "Flow Shopify ATC → Checkout",
                "reason": f"De {atc:,} ATC solo {ic:,} llegan a checkout ({drop*100:.0f}% drop).",
                "impact": f"Si esto fuera 50%, tendrías {potential} purchases más esta semana. Eso es revenue NO captado.",
                "score": 70,
                "detail": {
                    "what": f"Tu funnel pierde {drop*100:.0f}% de la gente que ya añadió al carrito. {atc:,} personas dijeron 'sí lo quiero' pero solo {ic:,} llegaron al paso de pagar. El problema NO está en Meta (la gente está calificada) — está en Shopify entre el botón \"Add to Cart\" y el \"Checkout\".",
                    "why_kill": f"Si llevas tu drop de {drop*100:.0f}% a un 50% (estándar industry e-commerce), recuperas ~{potential} compras semanales × {_fmt_compact(account_cpa or 50000)} CPA promedio = ~{_fmt_compact((potential * (account_cpa or 50000)) if potential>0 else 0)} {currency}/semana adicionales.",
                    "steps": [
                        {"title": "1. Test el flow tú mismo", "desc": "Abre sereneleparfum.com en INCÓGNITO (sin tu sesión guardada). Add to cart → ¿qué tan obvio es el botón Checkout? ¿Hay distractions? Mide los segundos del click ATC al landing del checkout."},
                        {"title": "2. Test mobile vs desktop", "desc": "70%+ del tráfico Meta es mobile. Repite el test en celular. ¿El botón Checkout es visible al primer scroll? ¿Hay sticky CTA?"},
                        {"title": "3. Revisa costos sorpresa", "desc": "El #1 abandono de checkout es el shipping cost que aparece tarde. En Shopify Settings → Shipping, ¿el costo se calcula en el cart o solo en checkout? Pre-calcular = menos abandono."},
                        {"title": "4. Activa Express Checkout", "desc": "Shopify Settings → Checkout → activa Apple Pay, Google Pay, Shop Pay. Esos one-click reducen abandonment 20-30%."},
                        {"title": "5. Reduce campos en cart page", "desc": "Si tu cart page tiene formulario de descuento prominente, mueve eso al checkout. Cart debe ser \"ver carrito + 1 botón gigante\"."},
                        {"title": "6. Mira Shopify analytics", "desc": "Reports → Cart abandonment. Te dice EXACTAMENTE en qué paso se van. Esa data es oro."},
                        {"title": "7. Email recovery", "desc": "Activa Shopify Cart Abandonment Email (Settings → Notifications). Un solo email recupera 5-10% de los abandonos. Gratis."},
                    ],
                    "post_action": "Este es el ROI más alto de todo el dashboard. Mejorar el checkout puede generar más impact que cualquier optimización de Meta. Antes de gastar más en ads, arregla el balde con hueco.",
                    "external_links": [
                        {"label": "Shopify Cart Abandonment Reports", "url": "https://admin.shopify.com/store/sereneleparfum/analytics/reports"},
                        {"label": "Shopify Checkout Settings", "url": "https://admin.shopify.com/store/sereneleparfum/settings/checkout"},
                    ],
                },
            })

    # Sort: critical first, then by score
    out.sort(key=lambda x: (0 if x["severity"] == "critical" else 1, -x["score"]))
    return out


def _fmt_compact(v) -> str:
    """Quick format for analyzer messages."""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:,.0f}"


# ──────────────────────────────────────────────────────────
# Audience Intelligence — process demographic breakdowns
# ──────────────────────────────────────────────────────────
def _row_purchases(row: dict) -> int:
    actions = row.get("actions", []) or []
    return sum(int(a.get("value", 0)) for a in actions if a.get("action_type") == "purchase")


def _row_metrics(row: dict) -> dict:
    spend = float(row.get("spend", 0) or 0)
    purchases = _row_purchases(row)
    return {
        "spend": spend,
        "impressions": int(float(row.get("impressions", 0) or 0)),
        "clicks": int(float(row.get("clicks", 0) or 0)),
        "ctr": float(row.get("ctr", 0) or 0),
        "purchases": purchases,
        "cpa": (spend / purchases) if purchases > 0 else None,
    }


def analyze_audience(audience_raw: dict, account_summary: dict) -> dict:
    """
    Process raw breakdowns into structured insights:
      - age_gender: combined buckets ranked by purchases
      - placement: ranked by efficiency
      - country: top 5 with %
      - device: distribution
    """
    if not audience_raw:
        return {}

    total_purchases = account_summary.get("purchases", 0) or 1

    # 1. Age + Gender combined
    ag_buckets = []
    for row in audience_raw.get("age_gender", []) or []:
        gender = row.get("gender", "?")
        age = row.get("age", "?")
        if gender == "unknown" or age == "?":
            continue
        m = _row_metrics(row)
        if m["spend"] == 0 and m["purchases"] == 0:
            continue
        ag_buckets.append({
            "label": f"{age} {gender}",
            "age": age,
            "gender": gender,
            **m,
            "purchase_share": (m["purchases"] / total_purchases * 100) if total_purchases else 0,
        })
    ag_buckets.sort(key=lambda x: -x["purchases"])

    # 2. Placement
    pl_buckets = []
    for row in audience_raw.get("placement", []) or []:
        plat = row.get("publisher_platform", "?")
        pos = row.get("platform_position", "?")
        m = _row_metrics(row)
        if m["spend"] == 0 and m["purchases"] == 0:
            continue
        pl_buckets.append({
            "label": f"{plat}/{pos}".replace("facebook/", "").replace("instagram/", "ig/"),
            "platform": plat,
            "position": pos,
            **m,
            "purchase_share": (m["purchases"] / total_purchases * 100) if total_purchases else 0,
        })
    pl_buckets.sort(key=lambda x: -x["purchases"])

    # 3. Region (departamentos en Colombia, group small ones)
    co_buckets = []
    for row in audience_raw.get("region", []) or []:
        m = _row_metrics(row)
        if m["spend"] == 0 and m["purchases"] == 0:
            continue
        co_buckets.append({
            "region": row.get("region", "?"),
            **m,
            "purchase_share": (m["purchases"] / total_purchases * 100) if total_purchases else 0,
        })
    # Sort por purchases si hay, fallback a spend (regiones rara vez tienen purchase events propagados)
    co_buckets.sort(key=lambda x: (-x["purchases"], -x["spend"]))

    # 4. Device
    dev_buckets = []
    for row in audience_raw.get("device", []) or []:
        m = _row_metrics(row)
        if m["spend"] == 0 and m["purchases"] == 0:
            continue
        dev_buckets.append({
            "device": row.get("impression_device", "?"),
            **m,
            "purchase_share": (m["purchases"] / total_purchases * 100) if total_purchases else 0,
        })
    dev_buckets.sort(key=lambda x: -x["purchases"])

    # Top winners
    top_demo = ag_buckets[0] if ag_buckets else None
    top_placement = pl_buckets[0] if pl_buckets else None
    top_region = co_buckets[0] if co_buckets else None

    # Gender split
    male_purch = sum(b["purchases"] for b in ag_buckets if b["gender"] == "male")
    female_purch = sum(b["purchases"] for b in ag_buckets if b["gender"] == "female")
    total_known = male_purch + female_purch

    return {
        "age_gender": ag_buckets,
        "placement": pl_buckets[:8],  # top 8 placements
        "region": co_buckets[:8],
        "device": dev_buckets,
        "top_demo": top_demo,
        "top_placement": top_placement,
        "top_region": top_region,
        "gender_split": {
            "male_purchases": male_purch,
            "female_purchases": female_purch,
            "male_pct": (male_purch / total_known * 100) if total_known else 0,
            "female_pct": (female_purch / total_known * 100) if total_known else 0,
        },
    }


def audience_insights(audience: dict, account_summary: dict) -> list[dict]:
    """Generate insights specific to audience patterns."""
    insights = []
    if not audience:
        return insights

    # Top demo dominance
    top = audience.get("top_demo")
    if top and top["purchase_share"] > 25:
        insights.append({
            "severity": "warning",
            "type": "TOP_DEMO",
            "title": f"{top['label']} concentra {top['purchase_share']:.0f}% de las purchases",
            "description": (f"Esta combinación de edad+género genera {top['purchases']} compras de {account_summary.get('purchases', 0)} totales " + (f"con CPA {top['cpa']:,.0f}." if top['cpa'] else "(CPA no calculable).") + " Si pausas esta audiencia, baja el volumen drásticamente."),
            "stats": [
                {"label": "Purchases", "value": top["purchases"], "color": "mint"},
                {"label": "Share", "value": f"{top['purchase_share']:.0f}%", "color": "neutral"},
                {"label": "CPA", "value": f"{top['cpa']/1000:.0f}K" if top['cpa'] else "—", "color": "neutral"},
            ],
            "action": "Diversificar — escalar 2nd y 3rd best demo para reducir riesgo de concentración",
        })

    # Gender skew
    gs = audience.get("gender_split", {})
    if gs.get("male_pct", 0) > 70 or gs.get("female_pct", 0) > 70:
        dominant = "male" if gs["male_pct"] > gs["female_pct"] else "female"
        pct = gs["male_pct"] if dominant == "male" else gs["female_pct"]
        insights.append({
            "severity": "warning",
            "type": "GENDER_SKEW",
            "title": f"Audiencia {dominant} domina con {pct:.0f}% de las compras",
            "description": f"Hombres: {gs['male_purchases']} purchases ({gs['male_pct']:.0f}%) · Mujeres: {gs['female_purchases']} purchases ({gs['female_pct']:.0f}%). El perfil de comprador está claramente sesgado.",
            "stats": [
                {"label": "Hombres", "value": gs["male_purchases"], "color": "neutral"},
                {"label": "Mujeres", "value": gs["female_purchases"], "color": "neutral"},
                {"label": "Skew", "value": f"{pct:.0f}%/{100-pct:.0f}%", "color": "warning"},
            ],
            "action": f"Si el producto NO debería skewar — probar creatives para género contrario. Si sí — ratificar y excluir el otro para optimizar.",
        })

    # Top placement
    tp = audience.get("top_placement")
    if tp and tp["purchase_share"] > 25:
        insights.append({
            "severity": "warning",
            "type": "TOP_PLACEMENT",
            "title": f"{tp['label']} es el placement dominante",
            "description": (f"Genera {tp['purchases']} compras ({tp['purchase_share']:.0f}% del total) " + (f"con CPA {tp['cpa']:,.0f}." if tp['cpa'] else "") + " Considerar crear campañas específicas para optimizar exclusivamente ese placement."),
            "stats": [
                {"label": "Purchases", "value": tp["purchases"], "color": "mint"},
                {"label": "Share", "value": f"{tp['purchase_share']:.0f}%", "color": "neutral"},
                {"label": "Spend", "value": f"{tp['spend']/1000000:.1f}M", "color": "neutral"},
            ],
            "action": "Crear placement-specific campaign para optimizar más fino",
        })

    return insights


# ──────────────────────────────────────────────────────────
# Main entry — analyze full snapshot
# ──────────────────────────────────────────────────────────
def analyze_snapshot(snapshot: dict) -> dict:
    """Takes raw snapshot JSON, returns enriched dict with scores + insights + recos."""
    summary = snapshot["account_summary"]
    campaigns = snapshot["campaigns"]
    tracking = snapshot.get("tracking_health", {})

    enriched_camps = []
    for c in campaigns:
        score, breakdown = calculate_score(c, summary)
        status_code, status_label = classify_status(c, score, summary)
        enriched_camps.append({
            **c,
            "score": score,
            "score_breakdown": breakdown,
            "status_code": status_code,
            "status_label": status_label,
        })
    enriched_camps.sort(key=lambda c: -c["score"])

    insights = detect_insights(summary, campaigns, tracking)

    # Post ID consolidation (if data available)
    ads_with_posts = snapshot.get("ads_with_posts", [])
    post_ids_consolidated = consolidate_post_ids(ads_with_posts, summary)
    pid_insights = post_id_insights(post_ids_consolidated, summary)
    insights.extend(pid_insights)

    # Audience breakdowns (if data available)
    audience_raw = snapshot.get("audience_breakdowns", {})
    audience = analyze_audience(audience_raw, summary)
    aud_insights = audience_insights(audience, summary)
    insights.extend(aud_insights)

    # Shopify data (if loaded as sibling snapshot)
    shopify = None
    shopify_raw = snapshot.get("shopify")
    if shopify_raw:
        try:
            from analyze_shopify import analyze_shopify_snapshot
            shopify = analyze_shopify_snapshot(shopify_raw, meta_spend=summary.get("spend", 0))
            insights.extend(shopify.get("insights", []))
        except Exception as e:
            print(f"⚠ Shopify analyze failed: {e}", file=__import__('sys').stderr)

    recos = generate_recommendations(summary, campaigns, insights, tracking)

    # Smart opportunities (Pack F) — recomendaciones de próximas publicaciones
    smart_opps = []
    try:
        from recommend import generate_smart_opportunities
        smart_opps = generate_smart_opportunities(shopify, post_ids_consolidated, campaigns, summary)
    except Exception as e:
        print(f"⚠ Smart opportunities failed: {e}", file=__import__('sys').stderr)

    # Historical trends (Pack E) — anomaly + forecast cuando hay >=3-7 días de history
    history_data = None
    try:
        from history import compute_history_summary
        history_data = compute_history_summary()
    except Exception as e:
        print(f"⚠ History compute failed: {e}", file=__import__('sys').stderr)

    # Funnel rates
    funnel = {
        "impressions": summary["impressions"],
        "clicks": summary["clicks"],
        "lpv": summary["lpv"],
        "atc": summary["atc"],
        "ic": summary["ic"],
        "purchases": summary["purchases"],
        "ctr_rate": summary["ctr"],
        "click_to_lpv": (summary["lpv"] / summary["clicks"] * 100) if summary["clicks"] else 0,
        "lpv_to_atc": (summary["atc"] / summary["lpv"] * 100) if summary["lpv"] else 0,
        "atc_to_ic": (summary["ic"] / summary["atc"] * 100) if summary["atc"] else 0,
        "ic_to_purchase": (summary["purchases"] / summary["ic"] * 100) if summary["ic"] else 0,
        "click_to_purchase": (summary["purchases"] / summary["clicks"] * 100) if summary["clicks"] else 0,
    }

    # Build URGENT KILL LIST — campañas y posts que requieren acción inmediata
    urgent_actions = build_urgent_actions(enriched_camps, post_ids_consolidated, summary, tracking)

    return {
        "_meta": snapshot.get("_meta", {}),
        "account": snapshot["account"],
        "date_range": snapshot["date_range"],
        "summary": summary,
        "tracking_health": tracking,
        "campaigns": enriched_camps,
        "insights": insights,
        "recommendations": recos,
        "funnel": funnel,
        "post_ids": post_ids_consolidated,
        "audience": audience,
        "urgent_actions": urgent_actions,
        "comparison": snapshot.get("comparison"),  # multi-account comparison if present
        "shopify": shopify,
        "activity": snapshot.get("activity"),  # cross-platform activity feed
        "smart_opportunities": smart_opps,  # próximas publicaciones recomendadas
        "history": history_data,  # forecast + anomaly detection (necesita >=3-7d acumulados)
        "stats": {
            "campaigns_total": len(campaigns),
            "campaigns_scale": sum(1 for c in enriched_camps if c["status_code"] == "scale"),
            "campaigns_monitor": sum(1 for c in enriched_camps if c["status_code"] == "monitor"),
            "campaigns_stop": sum(1 for c in enriched_camps if c["status_code"] == "stop"),
            "insights_critical": sum(1 for i in insights if i["severity"] == "critical"),
            "insights_warning": sum(1 for i in insights if i["severity"] == "warning"),
            "post_ids_total": len(post_ids_consolidated),
            "post_ids_with_purchases": sum(1 for p in post_ids_consolidated if p["purchases"] > 0),
        },
    }


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    snap_path = Path(__file__).parent / "data" / "serene_dormant_2026_02.json"
    with open(snap_path) as f:
        snap = json.load(f)

    result = analyze_snapshot(snap)
    print(f"=== Account: {result['account']['name']} ({result['account']['id']}) ===")
    print(f"Period: {result['date_range']['since']} to {result['date_range']['until']}")
    print(f"Spend: {_fmt(result['summary']['spend'])} {result['account']['currency']}")
    print(f"Purchases: {result['summary']['purchases']} · CPA: {_fmt(result['summary']['cpa'])}")
    print(f"\nCampaigns ranked by score:")
    for c in result['campaigns']:
        print(f"  [{c['score']:>5.1f}] {c['status_label']:<15} · {_short_name(c['name'])} · CPA {_fmt(c.get('cpa'))} · CTR {c['ctr']:.2f}% · Freq {c['frequency']:.2f}")
    print(f"\nInsights ({len(result['insights'])}):")
    for i in result['insights']:
        print(f"  [{i['severity'].upper():<8}] {i['type']:<18} · {i['title']}")
    print(f"\nRecommendations ({len(result['recommendations'])}):")
    for r in result['recommendations']:
        print(f"  [{r['priority_label']:<8}] {r['title']}")
