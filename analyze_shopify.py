"""
Serene AI · Shopify analyzer
Transforma el snapshot bruto en métricas accionables para el dashboard.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from typing import Any


def _float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def analyze_shopify_snapshot(snap: dict, meta_spend: float = 0) -> dict:
    """
    Calcula:
    - revenue total, AOV, orders count
    - top SKUs (revenue + units)
    - customer cohorts (new vs returning)
    - abandoned checkout impact
    - inventory health (Tendencia)
    - real ROAS si meta_spend > 0
    """
    orders = snap.get("orders", []) or []
    abandoned = snap.get("abandoned_checkouts", []) or []
    tendencia = snap.get("tendencia_inventory", []) or []

    # ─── Revenue ────────────────────────────────────────────
    total_revenue = 0.0
    subtotal_revenue = 0.0
    total_discounts = 0.0
    currency = "COP"
    for o in orders:
        total_revenue += _float((o.get("totalPriceSet", {}).get("shopMoney") or {}).get("amount"))
        subtotal_revenue += _float((o.get("subtotalPriceSet", {}).get("shopMoney") or {}).get("amount"))
        total_discounts += _float((o.get("totalDiscountsSet", {}).get("shopMoney") or {}).get("amount"))
        cur = (o.get("totalPriceSet", {}).get("shopMoney") or {}).get("currencyCode")
        if cur:
            currency = cur

    orders_count = len(orders)
    aov = (total_revenue / orders_count) if orders_count else 0
    discount_rate = (total_discounts / subtotal_revenue * 100) if subtotal_revenue else 0

    # ─── Top SKUs ───────────────────────────────────────────
    sku_units = Counter()
    sku_revenue = defaultdict(float)
    sku_titles = {}
    for o in orders:
        for li in (o.get("lineItems", {}).get("nodes") or []):
            sku = li.get("sku") or "?"
            qty = int(li.get("quantity") or 0)
            unit_price = _float((li.get("originalUnitPriceSet", {}).get("shopMoney") or {}).get("amount"))
            sku_units[sku] += qty
            sku_revenue[sku] += unit_price * qty
            if sku not in sku_titles:
                sku_titles[sku] = li.get("title") or sku

    top_skus = []
    for sku, units in sku_units.most_common(20):
        top_skus.append({
            "sku": sku,
            "title": sku_titles.get(sku, sku),
            "units": units,
            "revenue": sku_revenue[sku],
        })

    # ─── Customer cohorts ───────────────────────────────────
    new_customers = 0
    returning_customers = 0
    unknown_customers = 0
    for o in orders:
        cust = o.get("customer") or {}
        n = cust.get("numberOfOrders")
        if n is None:
            unknown_customers += 1
        elif int(n) <= 1:
            new_customers += 1
        else:
            returning_customers += 1

    total_known_cust = new_customers + returning_customers
    new_pct = (new_customers / total_known_cust * 100) if total_known_cust else 0
    returning_pct = (returning_customers / total_known_cust * 100) if total_known_cust else 0

    # ─── Geographic distribution (by city, normalized) ──────
    city_orders = Counter()
    for o in orders:
        addr = o.get("shippingAddress") or {}
        city = (addr.get("city") or "?").strip()
        if city and city != "?":
            # Normalize: lowercase, strip accents for matching, keep canonical form
            key = city.lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
            canonical_map = {
                "bogota": "Bogotá", "bogotá": "Bogotá", "bogota d.c.": "Bogotá", "bogotá d.c.": "Bogotá",
                "medellin": "Medellín", "medellín": "Medellín",
                "cali": "Cali", "cartagena": "Cartagena",
                "barranquilla": "Barranquilla", "bucaramanga": "Bucaramanga",
                "pereira": "Pereira", "manizales": "Manizales",
                "santa marta": "Santa Marta", "cucuta": "Cúcuta", "cúcuta": "Cúcuta",
                "ibague": "Ibagué", "ibagué": "Ibagué",
                "villavicencio": "Villavicencio", "armenia": "Armenia",
            }
            canonical = canonical_map.get(key, city.title())
            city_orders[canonical] += 1
    top_cities = [{"city": c, "orders": n, "share": n / orders_count * 100 if orders_count else 0}
                  for c, n in city_orders.most_common(10)]

    # ─── Abandoned ───────────────────────────────────────────
    abandoned_value = 0.0
    for a in abandoned:
        abandoned_value += _float((a.get("totalPriceSet", {}).get("shopMoney") or {}).get("amount"))

    # ─── Tendencia inventory health ─────────────────────────
    tendencia_tracked = 0
    tendencia_in_stock = 0
    tendencia_oos = 0
    tendencia_no_tracking = 0
    for p in tendencia:
        variants = (p.get("variants", {}).get("nodes") or [])
        any_tracked = False
        any_in_stock = False
        for v in variants:
            inv_item = v.get("inventoryItem") or {}
            if inv_item.get("tracked"):
                any_tracked = True
                if (v.get("inventoryQuantity") or 0) > 0:
                    any_in_stock = True
        if any_tracked:
            tendencia_tracked += 1
            if any_in_stock:
                tendencia_in_stock += 1
            else:
                tendencia_oos += 1
        else:
            tendencia_no_tracking += 1

    # ─── Real ROAS ──────────────────────────────────────────
    real_roas = (total_revenue / meta_spend) if meta_spend > 0 else None

    # ─── Insights AI ────────────────────────────────────────
    insights = []
    if tendencia_no_tracking > 100:
        insights.append({
            "type": "SHOPIFY_INVENTORY_TRACKING",
            "severity": "warning",
            "title": "Inventory tracking deshabilitado en SKUs Tendencia",
            "body": f"{tendencia_no_tracking}/{len(tendencia)} productos Tendencia tienen tracked=false. Meta no puede determinar availability correctamente — el feed marca todo como out_of_stock.",
            "action": "Activar tracking con policy=CONTINUE (Task #15 en backlog)",
        })
    if abandoned_value > total_revenue * 0.3:
        insights.append({
            "type": "SHOPIFY_ABANDONED_HIGH",
            "severity": "warning",
            "title": "Abandonos altos vs revenue",
            "body": f"Valor abandonado ${abandoned_value:,.0f} = {abandoned_value/total_revenue*100:.0f}% del revenue. Hay dinero en la puerta.",
            "action": "Setup abandoned cart email/SMS flow + revisar friction en checkout",
        })
    if new_pct > 80:
        insights.append({
            "type": "SHOPIFY_NEW_HEAVY",
            "severity": "info",
            "title": "Mayoría compradores nuevos",
            "body": f"{new_pct:.0f}% son nuevos (1ra compra). Repeat purchase rate baja — oportunidad de retention.",
            "action": "Activar email post-compra + retention campaign en M4",
        })
    if real_roas is not None and real_roas < 1.5:
        insights.append({
            "type": "SHOPIFY_ROAS_LOW",
            "severity": "critical",
            "title": "ROAS real bajo",
            "body": f"ROAS real (Shopify revenue / Meta spend) = {real_roas:.2f}x. Por debajo de break-even típico.",
            "action": "Revisar campaigns con score<50 + pausar las que sangran",
        })

    return {
        "currency": currency,
        "orders_count": orders_count,
        "revenue": total_revenue,
        "subtotal": subtotal_revenue,
        "discounts": total_discounts,
        "discount_rate": discount_rate,
        "aov": aov,
        "top_skus": top_skus,
        "cohorts": {
            "new": new_customers,
            "returning": returning_customers,
            "unknown": unknown_customers,
            "new_pct": new_pct,
            "returning_pct": returning_pct,
        },
        "top_cities": top_cities,
        "abandoned": {
            "count": len(abandoned),
            "value_lost": abandoned_value,
            "value_lost_pct_revenue": (abandoned_value / total_revenue * 100) if total_revenue else 0,
        },
        "tendencia": {
            "total": len(tendencia),
            "tracked": tendencia_tracked,
            "in_stock": tendencia_in_stock,
            "out_of_stock": tendencia_oos,
            "no_tracking": tendencia_no_tracking,
        },
        "real_roas": real_roas,
        "insights": insights,
    }
