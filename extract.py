"""
Serene AI · Extractor
Pulla data de Meta Marketing API directamente con requests.
Standalone — solo necesita META_ACCESS_TOKEN en env.

Uso:
    export META_ACCESS_TOKEN="EAA..."
    python3 extract.py act_1020250386264513 --since 2026-05-02 --until 2026-05-09
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import datetime as _dt
from pathlib import Path

import requests

from config import API_BASE, ACCESS_TOKEN, DATA_DIR, ACCOUNTS

# ──────────────────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────────────────
DEFAULT_FIELDS_ACCOUNT = [
    "impressions", "clicks", "spend", "reach", "frequency",
    "ctr", "cpc", "cpm", "actions", "action_values", "purchase_roas",
]
DEFAULT_FIELDS_CAMPAIGN = [
    "campaign_id", "campaign_name", "spend", "impressions", "clicks", "reach",
    "frequency", "ctr", "actions", "action_values", "purchase_roas",
]


class MetaAPIError(Exception):
    pass


def _request(path: str, params: dict | None = None, retries: int = 3) -> dict:
    token = os.environ.get("META_ACCESS_TOKEN") or ACCESS_TOKEN
    if not token:
        raise MetaAPIError(
            "META_ACCESS_TOKEN no está configurado. Exporta el token:\n"
            "  export META_ACCESS_TOKEN='EAAxxx...'"
        )
    url = f"{API_BASE}/{path.lstrip('/')}"
    p = dict(params or {})
    p["access_token"] = token

    for attempt in range(retries):
        try:
            r = requests.get(url, params=p, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 or r.status_code >= 500:
                wait = 2 ** attempt
                print(f"⚠ HTTP {r.status_code} → retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            try:
                err = r.json().get("error", {})
                msg = err.get("message") or r.text
            except Exception:
                msg = r.text
            raise MetaAPIError(f"HTTP {r.status_code}: {msg}")
        except requests.RequestException as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"⚠ Network: {e} → retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise MetaAPIError(f"Network: {e}")
    raise MetaAPIError("All retries exhausted")


def _paginate(path: str, params: dict, max_pages: int = 20) -> list:
    out = []
    page = 0
    next_url = None
    while page < max_pages:
        if next_url:
            r = requests.get(next_url, timeout=60).json()
        else:
            r = _request(path, params)
        data = r.get("data", [])
        out.extend(data)
        next_url = r.get("paging", {}).get("next")
        if not next_url or not data:
            break
        page += 1
    return out


# ──────────────────────────────────────────────────────────
# High-level extractors
# ──────────────────────────────────────────────────────────
def get_account_info(account_id: str) -> dict:
    fields = "id,name,currency,timezone_name,account_status,balance"
    return _request(account_id, {"fields": fields})


def list_campaigns(account_id: str, status_filter: list[str] | None = None) -> list[dict]:
    fields = "id,name,objective,status,effective_status,created_time,updated_time,start_time,daily_budget,budget_remaining,lifetime_budget"
    params = {"fields": fields, "limit": 100}
    if status_filter:
        params["filtering"] = json.dumps([
            {"field": "effective_status", "operator": "IN", "value": status_filter}
        ])
    return _paginate(f"{account_id}/campaigns", params)


def get_account_insights(account_id: str, since: str, until: str,
                         fields: list[str] | None = None,
                         breakdowns: list[str] | None = None) -> dict:
    p = {
        "level": "account",
        "time_range": json.dumps({"since": since, "until": until}),
        "fields": ",".join(fields or DEFAULT_FIELDS_ACCOUNT),
    }
    if breakdowns:
        p["breakdowns"] = ",".join(breakdowns)
    r = _request(f"{account_id}/insights", p)
    data = r.get("data", [])
    return data[0] if data else {}


def get_campaign_insights(account_id: str, since: str, until: str,
                          fields: list[str] | None = None) -> list[dict]:
    p = {
        "level": "campaign",
        "time_range": json.dumps({"since": since, "until": until}),
        "fields": ",".join(fields or DEFAULT_FIELDS_CAMPAIGN),
        "limit": 100,
    }
    return _paginate(f"{account_id}/insights", p)


def get_ads_with_post_ids(account_id: str, status_filter: list[str] | None = None,
                           limit_per_page: int = 100) -> list[dict]:
    """
    List ads with their effective_object_story_id (the post_id used by the ad).
    The story_id format is `<page_id>_<post_id>`.
    """
    fields = "id,name,adset_id,campaign_id,effective_status,creative{id,effective_object_story_id,object_story_id,thumbnail_url}"
    p = {"fields": fields, "limit": limit_per_page}
    if status_filter:
        p["filtering"] = json.dumps([
            {"field": "effective_status", "operator": "IN", "value": status_filter}
        ])
    return _paginate(f"{account_id}/ads", p)


def get_audience_breakdowns(account_id: str, since: str, until: str) -> dict:
    """Pull insights account-level con varios breakdowns demográficos."""
    base_fields = ["spend", "impressions", "clicks", "ctr", "actions"]
    base_params = {
        "level": "account",
        "time_range": json.dumps({"since": since, "until": until}),
        "fields": ",".join(base_fields),
        "limit": 100,
    }

    out = {}
    for label, breakdowns in [
        ("age_gender", ["age", "gender"]),
        ("placement", ["publisher_platform", "platform_position"]),
        ("region", ["region"]),
        ("device", ["impression_device"]),
    ]:
        try:
            p = {**base_params, "breakdowns": ",".join(breakdowns)}
            r = _request(f"{account_id}/insights", p)
            out[label] = r.get("data", [])
        except MetaAPIError as e:
            print(f"   ⚠ Breakdown {label} failed: {e}", file=sys.stderr)
            out[label] = []
    return out


def get_ad_insights(account_id: str, since: str, until: str,
                    fields: list[str] | None = None) -> list[dict]:
    """Insights at ad level — heavy. Use with date range, ideally last 7-30d."""
    p = {
        "level": "ad",
        "time_range": json.dumps({"since": since, "until": until}),
        "fields": ",".join(fields or [
            "ad_id", "ad_name", "spend", "impressions", "clicks",
            "frequency", "ctr", "actions"
        ]),
        "limit": 100,
    }
    return _paginate(f"{account_id}/insights", p)


# ──────────────────────────────────────────────────────────
# Action helpers — Meta returns flat arrays, we flatten to dict
# ──────────────────────────────────────────────────────────
def actions_to_dict(actions: list[dict] | None) -> dict[str, float]:
    if not actions:
        return {}
    out = {}
    for a in actions:
        try:
            out[a["action_type"]] = float(a["value"])
        except (KeyError, ValueError, TypeError):
            pass
    return out


def get_metric(actions: dict[str, float], *types: str) -> float:
    """Return first matching metric value, 0 if none found."""
    for t in types:
        if t in actions:
            return actions[t]
    return 0


# ──────────────────────────────────────────────────────────
# Snapshot builder — outputs same schema as analyze.py expects
# ──────────────────────────────────────────────────────────
def build_post_ids_data(account_id: str, since: str, until: str) -> list[dict]:
    """Pull ads with creative.effective_object_story_id + ad-level insights → consolidate by post_id."""
    print(f"📡 Pulling ads + creatives…")
    ads = get_ads_with_post_ids(account_id, status_filter=["ACTIVE", "PAUSED"])
    print(f"   {len(ads)} ads (active+paused)")

    print(f"📡 Ad-level insights {since}→{until}…")
    ad_insights = get_ad_insights(account_id, since, until)
    print(f"   {len(ad_insights)} ads with data")

    # Index insights by ad_id
    insights_by_ad = {}
    for ai in ad_insights:
        aid = ai.get("ad_id") or ai.get("id")
        if aid:
            insights_by_ad[aid] = ai

    # Build list of ads enriched with insights + post_id parsed
    enriched = []
    for ad in ads:
        creative = ad.get("creative") or {}
        story_id = creative.get("effective_object_story_id") or creative.get("object_story_id") or ""
        # story_id format: "<page_id>_<post_id>"
        page_id, _, post_id = story_id.partition("_")
        if not post_id:
            continue  # ad without a real post-based creative

        ai = insights_by_ad.get(ad["id"], {})
        # Skip ads without any activity in period (historical/paused noise)
        if not ai:
            continue

        actions = actions_to_dict(ai.get("actions"))
        spend = float(ai.get("spend", 0) or 0)
        purchases = get_metric(actions, "purchase", "omni_purchase")

        enriched.append({
            "ad_id": ad["id"],
            "ad_name": ad.get("name", ""),
            "campaign_id": ad.get("campaign_id"),
            "adset_id": ad.get("adset_id"),
            "creative_id": creative.get("id"),
            "post_id": post_id,
            "page_id": page_id,
            "story_id": story_id,
            "thumbnail_url": creative.get("thumbnail_url"),
            "effective_status": ad.get("effective_status"),
            "spend": spend,
            "impressions": int(float(ai.get("impressions", 0) or 0)),
            "clicks": int(float(ai.get("clicks", 0) or 0)),
            "frequency": float(ai.get("frequency", 0) or 0),
            "ctr": float(ai.get("ctr", 0) or 0),
            "purchases": int(purchases),
            "atc": int(get_metric(actions, "add_to_cart", "omni_add_to_cart")),
            "ic": int(get_metric(actions, "initiate_checkout")),
            "video_views": int(get_metric(actions, "video_view")),
            "cpa": (spend / purchases) if purchases > 0 else None,
        })
    return enriched


def build_snapshot(account_id: str, since: str, until: str, include_post_ids: bool = True) -> dict:
    print(f"📡 Account info {account_id}…")
    acc_info = get_account_info(account_id)

    print(f"📡 Account insights {since}→{until}…")
    acc_insights = get_account_insights(account_id, since, until)

    print(f"📡 List campaigns…")
    campaigns_raw = list_campaigns(account_id)
    print(f"   {len(campaigns_raw)} campaigns total")

    print(f"📡 Campaign-level insights…")
    camp_insights_raw = get_campaign_insights(account_id, since, until)
    print(f"   {len(camp_insights_raw)} campaigns with data")

    ads_with_posts = []
    if include_post_ids:
        try:
            ads_with_posts = build_post_ids_data(account_id, since, until)
            print(f"   {len(ads_with_posts)} ads with post_ids")
        except MetaAPIError as e:
            print(f"   ⚠ Post IDs pull failed: {e}", file=sys.stderr)

    print(f"📡 Audience breakdowns…")
    audience_raw = get_audience_breakdowns(account_id, since, until)
    print(f"   age_gender: {len(audience_raw.get('age_gender', []))} · placement: {len(audience_raw.get('placement', []))} · region: {len(audience_raw.get('region', []))} · device: {len(audience_raw.get('device', []))}")

    # Index camp_insights by campaign_id (when present, otherwise by name)
    insights_by_id = {}
    for ci in camp_insights_raw:
        cid = ci.get("campaign_id") or ci.get("id")
        if cid:
            insights_by_id[cid] = ci

    # Account summary
    acc_actions = actions_to_dict(acc_insights.get("actions"))
    spend = float(acc_insights.get("spend", 0) or 0)
    purchases = get_metric(acc_actions, "purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase")
    cpa = (spend / purchases) if purchases > 0 else None

    summary = {
        "spend": spend,
        "impressions": int(float(acc_insights.get("impressions", 0) or 0)),
        "clicks": int(float(acc_insights.get("clicks", 0) or 0)),
        "reach": int(float(acc_insights.get("reach", 0) or 0)),
        "frequency": float(acc_insights.get("frequency", 0) or 0),
        "ctr": float(acc_insights.get("ctr", 0) or 0),
        "cpc": float(acc_insights.get("cpc", 0) or 0),
        "cpm": float(acc_insights.get("cpm", 0) or 0),
        "purchases": int(purchases),
        "purchase_value": acc_insights.get("action_values"),
        "purchase_roas": acc_insights.get("purchase_roas"),
        "atc": int(get_metric(acc_actions, "add_to_cart", "omni_add_to_cart")),
        "lpv": int(get_metric(acc_actions, "landing_page_view", "omni_landing_page_view")),
        "ic": int(get_metric(acc_actions, "initiate_checkout", "omni_initiated_checkout")),
        "add_payment_info": int(get_metric(acc_actions, "add_payment_info")),
        "video_views": int(get_metric(acc_actions, "video_view")),
        "view_content": int(get_metric(acc_actions, "view_content", "omni_view_content")),
        "page_engagement": int(get_metric(acc_actions, "page_engagement")),
        "post_engagement": int(get_metric(acc_actions, "post_engagement")),
        "link_clicks": int(get_metric(acc_actions, "link_click")),
        "messaging_started": int(get_metric(acc_actions, "onsite_conversion.messaging_conversation_started_7d")),
        "cpa": cpa,
    }
    # Funnel rates
    summary["click_to_purchase_rate"] = (purchases / summary["clicks"] * 100) if summary["clicks"] else 0
    summary["lpv_to_atc_rate"] = (summary["atc"] / summary["lpv"] * 100) if summary["lpv"] else 0
    summary["atc_to_ic_rate"] = (summary["ic"] / summary["atc"] * 100) if summary["atc"] else 0
    summary["ic_to_purchase_rate"] = (purchases / summary["ic"] * 100) if summary["ic"] else 0

    # Tracking health — heurística: si hay purchases pero action_values vacío → no value tracking
    has_value = bool(acc_insights.get("action_values"))
    tracking_health = {
        "value_tracking": has_value,
        "value_tracking_note": (
            "El pixel envía value en eventos Purchase — ROAS calculable."
            if has_value
            else "El pixel NO envía 'value' en eventos Purchase. Sin esto no se puede calcular ROAS — solo CPA. Configurar en Shopify Customer Events."
        ),
    }

    # Build campaigns — only include those with insights data in the period
    # (filter out historical/paused with 0 data to avoid noise)
    enriched_campaigns = []
    for c in campaigns_raw:
        ci = insights_by_id.get(c["id"], {})
        # Skip if no spend AND no impressions in period (helps reduce 584→active set)
        if not ci.get("spend") and not ci.get("impressions"):
            continue
        c_actions = actions_to_dict(ci.get("actions"))
        c_spend = float(ci.get("spend", 0) or 0)
        c_purchases = get_metric(c_actions, "purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase")
        c_cpa = (c_spend / c_purchases) if c_purchases > 0 else None

        enriched_campaigns.append({
            "id": c["id"],
            "name": c.get("name", ""),
            "objective": c.get("objective", ""),
            "status": c.get("status", ""),
            "effective_status": c.get("effective_status", ""),
            "daily_budget": _safe_int(c.get("daily_budget")),
            "budget_remaining": _safe_int(c.get("budget_remaining")),
            "spend": c_spend,
            "impressions": int(float(ci.get("impressions", 0) or 0)),
            "clicks": int(float(ci.get("clicks", 0) or 0)),
            "reach": int(float(ci.get("reach", 0) or 0)),
            "frequency": float(ci.get("frequency", 0) or 0),
            "ctr": float(ci.get("ctr", 0) or 0),
            "cpc": float(ci.get("cpc", 0) or 0),
            "cpm": float(ci.get("cpm", 0) or 0),
            "purchases": int(c_purchases),
            "atc": int(get_metric(c_actions, "add_to_cart", "omni_add_to_cart")),
            "lpv": int(get_metric(c_actions, "landing_page_view", "omni_landing_page_view")),
            "ic": int(get_metric(c_actions, "initiate_checkout", "omni_initiated_checkout")),
            "video_views": int(get_metric(c_actions, "video_view")),
            "cpa": c_cpa,
            "format_hint": _infer_format(c.get("name", ""), c_actions),
        })

    # Sort by spend desc
    enriched_campaigns.sort(key=lambda x: -x["spend"])

    return {
        "_meta": {
            "extracted_at": _dt.datetime.utcnow().isoformat() + "Z",
            "extracted_via": "extract.py (Meta Graph API direct)",
            "schema_version": "1.1",
        },
        "account": {
            "id": acc_info.get("id", account_id),
            "name": acc_info.get("name", ""),
            "currency": acc_info.get("currency", "USD"),
            "timezone": acc_info.get("timezone_name", ""),
            "account_status": acc_info.get("account_status", 0),
            "status_label": _status_label(acc_info.get("account_status")),
            "balance": acc_info.get("balance"),
        },
        "date_range": {
            "since": since,
            "until": until,
            "preset": "custom",
            "days": (_dt.date.fromisoformat(until) - _dt.date.fromisoformat(since)).days + 1,
        },
        "account_summary": summary,
        "tracking_health": tracking_health,
        "campaigns": enriched_campaigns,
        "ads_with_posts": ads_with_posts,
        "audience_breakdowns": audience_raw,
    }


def _safe_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _status_label(status_code) -> str:
    return {1: "active", 2: "disabled", 3: "unsettled", 7: "pending_review"}.get(status_code, "unknown")


def _infer_format(name: str, actions: dict[str, float]) -> str:
    """Heurística: si hay video_view > clicks/2 → video; si name tiene VID o V → video."""
    name_upper = name.upper()
    if any(k in name_upper for k in ["VID ", "V MARIANA", "V JEFF", "V JUANM"]):
        return "video"
    vv = actions.get("video_view", 0)
    clicks = actions.get("link_click", 1)
    if vv > clicks * 2:
        return "video"
    return "image"


# ──────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Extract Meta Ads data → snapshot JSON")
    parser.add_argument("account_id", help="Meta ad account id (e.g., act_1020250386264513)")
    parser.add_argument("--since", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--until", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--out", help="Output filename (default: <account_slug>_<date>.json)")
    args = parser.parse_args()

    snap = build_snapshot(args.account_id, args.since, args.until)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.out:
        out_path = DATA_DIR / args.out
    else:
        slug = args.account_id.replace("act_", "")
        out_path = DATA_DIR / f"{slug}_{args.since}_to_{args.until}.json"

    out_path.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✓ Snapshot saved: {out_path}")
    print(f"  {snap['account']['name']} · {len(snap['campaigns'])} campaigns · {snap['account_summary']['purchases']} purchases")


if __name__ == "__main__":
    main()
