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

    # 3. Country (group small ones)
    co_buckets = []
    for row in audience_raw.get("country", []) or []:
        m = _row_metrics(row)
        if m["spend"] == 0 and m["purchases"] == 0:
            continue
        co_buckets.append({
            "country": row.get("country", "?"),
            **m,
            "purchase_share": (m["purchases"] / total_purchases * 100) if total_purchases else 0,
        })
    co_buckets.sort(key=lambda x: -x["purchases"])

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
    top_country = co_buckets[0] if co_buckets else None

    # Gender split
    male_purch = sum(b["purchases"] for b in ag_buckets if b["gender"] == "male")
    female_purch = sum(b["purchases"] for b in ag_buckets if b["gender"] == "female")
    total_known = male_purch + female_purch

    return {
        "age_gender": ag_buckets,
        "placement": pl_buckets[:8],  # top 8 placements
        "country": co_buckets[:6],
        "device": dev_buckets,
        "top_demo": top_demo,
        "top_placement": top_placement,
        "top_country": top_country,
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

    recos = generate_recommendations(summary, campaigns, insights, tracking)

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
        "comparison": snapshot.get("comparison"),  # multi-account comparison if present
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
