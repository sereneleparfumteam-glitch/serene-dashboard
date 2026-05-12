"""
Serene AI · Recomendaciones de próximas publicaciones

Combina:
- Top SKUs vendidos (Shopify) cross-ref top ads (Meta post IDs)
- Posts con buen engagement pero poco spend (oportunidad escalar)
- SKUs vendidos sin ad activo (gap en feed)
- Patrones de co-compra (de [[project_serene_catalog_intelligence]])

Sin LLM externo en Fase 1 — solo algorithmic gap detection.
Fase 2 (futuro): pytrends + Meta Ad Library + Claude synthesis.
"""
from __future__ import annotations
from typing import Any


# Avatares M4 (de project_serene_avatars + memoria catalog)
AVATAR_HINTS = {
    "S832": ("Hombre de Nicho", "🎩"),
    "S032": ("Hombre de Nicho", "🎩"),
    "S830": ("Hombre de Nicho", "🎩"),
    "S030": ("Depredador Social", "🐺"),
    "S034": ("Depredador Social", "🐺"),
    "S838": ("Mujer Aspiracional", "💎"),
    "S234": ("Mujer Aspiracional", "💎"),
    "S230": ("Mujer Aspiracional", "💎"),
    "Hypnosis": ("Coleccionista", "🧪"),
    "Kit": ("Comprador de Regalo", "🎁"),
}


def _classify_avatar(sku: str, title: str) -> tuple[str, str]:
    """Retorna (avatar, emoji) basado en SKU o título."""
    sku_upper = (sku or "").upper()
    title_lower = (title or "").lower()
    for prefix, (avatar, emoji) in AVATAR_HINTS.items():
        if sku_upper.startswith(prefix.upper()) or prefix.lower() in title_lower:
            return avatar, emoji
    if "kit" in title_lower or sku_upper.startswith("KIT"):
        return "Comprador de Regalo", "🎁"
    if "feromonas" in title_lower or "fe-" in sku_upper:
        return "Cross-sell", "💜"
    if "crema" in title_lower or sku_upper.startswith("C"):
        return "Retention", "🤍"
    return "General", "✨"


def generate_smart_opportunities(shopify_data: dict, post_ids: list[dict],
                                  campaigns: list[dict], summary: dict) -> list[dict]:
    """
    Detecta gaps accionables en la data actual. Cada opportunity tiene:
    - hook: copy sugerido
    - why: justificación basada en data
    - confidence: 0-100 basado en señales
    - funnel: prospecting / retention / retargeting
    - avatar: target M4
    - action: qué hacer concretamente
    """
    opportunities = []

    if not shopify_data:
        return opportunities

    top_skus = shopify_data.get("top_skus", [])
    if not top_skus:
        return opportunities

    # ──────────────────────────────────────────────────────
    # Opportunity Type 1: Top-selling SKU sin ad post correspondiente
    # ──────────────────────────────────────────────────────
    post_id_titles = " ".join(
        [str(p.get("ad_names", [""])[0]).lower() for p in (post_ids or [])]
    )

    for sku_info in top_skus[:10]:
        sku = sku_info.get("sku", "")
        title = sku_info.get("title", "")
        units = sku_info.get("units", 0)
        revenue = sku_info.get("revenue", 0)

        if not sku or sku == "?":
            continue

        # Heuristic: SKU vendido pero no aparece en post_id_titles
        sku_in_ads = sku.lower() in post_id_titles or title.lower()[:20] in post_id_titles
        if not sku_in_ads and units >= 10:
            avatar, emoji = _classify_avatar(sku, title)
            opportunities.append({
                "hook": f"Anuncio dedicado para {title[:40]} — {units} vendidos sin ad propio",
                "why": f"Vendiste {units} unidades de {sku} (${revenue/1_000_000:.1f}M) pero no aparece en tus ads activos. Cross-sell orgánico, demanda comprobada.",
                "confidence": min(95, 60 + units // 3),
                "funnel": "Prospecting",
                "avatar": avatar,
                "emoji": emoji,
                "sku": sku,
                "action": f"Crear creative dedicado para {sku} · headline '{title[:30]}' · audience LAL compradores",
                "category": "hidden_winner",
            })

    # ──────────────────────────────────────────────────────
    # Opportunity Type 2: Post con buen engagement, poco spend
    # ──────────────────────────────────────────────────────
    for p in (post_ids or [])[:15]:
        purchases = p.get("purchases", 0)
        spend = float(p.get("spend", 0) or 0)
        engagement = p.get("engagement", 0) or p.get("comments", 0) + p.get("likes", 0) + p.get("shares", 0)

        # Underspend: tiene purchases pero spend bajo
        avg_spend = float(summary.get("spend", 0)) / max(len(post_ids or [1]), 1)
        if purchases >= 3 and 0 < spend < avg_spend * 0.5:
            cpa = (spend / purchases) if purchases else 0
            ad_name = (p.get("ad_names") or ["?"])[0]
            opportunities.append({
                "hook": f"Escalá presupuesto en post {ad_name[:40]}",
                "why": f"{purchases} compras con solo ${spend:,.0f} spend (CPA ${cpa:,.0f}). Underspent vs promedio de la cuenta (${avg_spend:,.0f}).",
                "confidence": min(90, 60 + purchases * 5),
                "funnel": "Scale existing",
                "avatar": "Múltiples",
                "emoji": "📈",
                "sku": None,
                "action": f"Duplicar budget en ad set padre · post_id …{(p.get('post_id') or '')[-12:]}",
                "category": "scale_winner",
            })

    # ──────────────────────────────────────────────────────
    # Opportunity Type 3: Abandoned cart recovery angle
    # ──────────────────────────────────────────────────────
    abandoned = shopify_data.get("abandoned", {})
    if abandoned.get("count", 0) > 100 and abandoned.get("value_lost", 0) > 50_000_000:
        opportunities.append({
            "hook": f"Campaña retargeting carritos abandonados — ${abandoned['value_lost']/1_000_000:.0f}M en juego",
            "why": f"{abandoned['count']} carritos abandonados con valor ${abandoned['value_lost']/1_000_000:.0f}M COP en 7 días. Audience ya caliente, intent confirmado.",
            "confidence": 92,
            "funnel": "Retargeting",
            "avatar": "Múltiples",
            "emoji": "🛒",
            "sku": None,
            "action": "Setup DPA retargeting · ATC 14d audience + sweetener Feromonas FE-1 ($20K)",
            "category": "abandoned_recovery",
        })

    # ──────────────────────────────────────────────────────
    # Opportunity Type 4: Retention para new buyers
    # ──────────────────────────────────────────────────────
    cohorts = shopify_data.get("cohorts", {})
    if cohorts.get("new_pct", 0) > 55:
        new_count = cohorts.get("new", 0)
        opportunities.append({
            "hook": "Email post-compra + retention loop para nuevos clientes",
            "why": f"{cohorts['new_pct']:.0f}% son compradores nuevos ({new_count} en 7d). Tasa de recompra baja — cada nuevo cliente vale 3-5x con retention.",
            "confidence": 88,
            "funnel": "Retention",
            "avatar": "Comprador de Regalo + Coleccionista",
            "emoji": "💌",
            "sku": None,
            "action": "Email D+7 con crema matching + Email D+30 con kit complementario · audience custom Purchase 7d/30d",
            "category": "retention_setup",
        })

    # ──────────────────────────────────────────────────────
    # Opportunity Type 5: City-specific scaling
    # ──────────────────────────────────────────────────────
    top_cities = shopify_data.get("top_cities", [])
    if len(top_cities) >= 3:
        top3 = top_cities[:3]
        top3_share = sum(c["share"] for c in top3)
        if top3_share > 25:
            cities_label = ", ".join([c["city"] for c in top3])
            opportunities.append({
                "hook": f"Ad set geo-targeted: {cities_label}",
                "why": f"{top3_share:.0f}% del revenue viene de {cities_label}. Ad sets geo-segmentados con creatives locales pueden bajar CPA 30-50%.",
                "confidence": 75,
                "funnel": "Prospecting",
                "avatar": "Hombre de Nicho + Mujer Aspiracional",
                "emoji": "🗺",
                "sku": None,
                "action": f"Duplicar campañas top y filtrar geo={cities_label}. Crear thumbnail con landmark local (ej. Monserrate para Bogotá).",
                "category": "geo_scale",
            })

    # ──────────────────────────────────────────────────────
    # Opportunity Type 6: Tendencia inventory blocker
    # ──────────────────────────────────────────────────────
    tendencia = shopify_data.get("tendencia", {})
    if tendencia.get("no_tracking", 0) > 100:
        opportunities.append({
            "hook": "DPA bloqueado por inventory tracking en SKUs Tendencia",
            "why": f"{tendencia['no_tracking']}/{tendencia['total']} productos Tendencia con tracked=false. Meta no puede determinar stock → DPA marca todo out_of_stock.",
            "confidence": 99,
            "funnel": "Catalog setup",
            "avatar": "Todos",
            "emoji": "📦",
            "sku": None,
            "action": "Activar tracking con policy=CONTINUE → qty=9999 → tracked=true (Task #15)",
            "category": "blocker_fix",
        })

    # Sort by confidence desc
    opportunities.sort(key=lambda o: -o["confidence"])
    return opportunities[:10]  # top 10
