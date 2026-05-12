"""
Serene AI · Activity Feed extractor
Pulla cambios cross-platform con actor (quién, cuándo, qué cambió).

Fuentes:
- Meta: GET /act_xxx/activities (con actor_name, event_type, extra_data)
- Shopify: GET /admin/api/.../events.json (con verb, message, user)
- Google Ads: GAQL change_event (PENDING — refresh token revoked 2026-05-12)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import datetime as _dt
from typing import Any
import requests


META_API_VERSION = "v21.0"
SHOPIFY_API_VERSION = "2024-10"


def fetch_meta_activities(account_id: str, since: str, until: str,
                          access_token: str, limit: int = 500) -> list[dict]:
    """Activity log de Meta. Retorna last 500 events desde since.

    Esquema retornado: event_type, event_time, object_id, object_name,
    actor_id, actor_name, extra_data (old/new), translated_event_type.
    """
    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    url = f"https://graph.facebook.com/{META_API_VERSION}/{account_id}/activities"
    params = {
        "fields": "event_type,event_time,object_id,object_name,actor_id,actor_name,extra_data,translated_event_type",
        "since": since,
        "until": until,
        "limit": min(limit, 500),
        "access_token": access_token,
    }
    out = []
    next_url = url
    next_params = params
    pages = 0
    while next_url and pages < 5:  # max 5 pages = 2500 events
        r = requests.get(next_url, params=next_params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"Meta API: {data['error']}")
        out.extend(data.get("data", []))
        next_url = data.get("paging", {}).get("next")
        next_params = None  # next URL has params embedded
        pages += 1
        if len(out) >= limit:
            break
    return out[:limit]


def fetch_shopify_events(shop: str, token: str, since: str,
                         limit: int = 250) -> list[dict]:
    """Eventos de Shopify (orders, products, customers). Read-only.

    Returns: created_at, verb, message, subject_type, subject_id, author.
    """
    url = f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/events.json"
    params = {
        "created_at_min": f"{since}T00:00:00",
        "limit": min(limit, 250),
    }
    headers = {"X-Shopify-Access-Token": token}
    out = []
    page_info = None
    pages = 0
    while pages < 5:
        if page_info:
            r = requests.get(url, params={"page_info": page_info, "limit": 250},
                           headers=headers, timeout=60)
        else:
            r = requests.get(url, params=params, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("events", []))
        # Shopify uses Link header for pagination
        link = r.headers.get("Link", "")
        if 'rel="next"' in link:
            # crude extract: <url>; rel="next"
            import re
            m = re.search(r'<([^>]+)>; rel="next"', link)
            if m:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(m.group(1)).query)
                page_info = qs.get("page_info", [None])[0]
            else:
                break
        else:
            break
        pages += 1
    return out


# ──────────────────────────────────────────────────────────
# Normalize to common schema
# ──────────────────────────────────────────────────────────
META_EVENT_LABELS = {
    "update_campaign_budget": "Cambió budget de campaña",
    "update_ad_set_budget": "Cambió budget de ad set",
    "update_campaign_run_status": "Cambió status de campaña",
    "update_ad_set_run_status": "Cambió status de ad set",
    "update_ad_run_status": "Cambió status de ad",
    "update_ad_creative": "Editó creative",
    "update_ad_set_name": "Renombró ad set",
    "update_campaign_name": "Renombró campaña",
    "update_ad_targets_spec": "Cambió targeting",
    "create_campaign": "Creó campaña",
    "create_ad_set": "Creó ad set",
    "create_ad": "Creó ad",
    "edit_images": "Editó imagen",
    "add_images": "Subió imagen",
    "first_delivery_event": "Primera entrega",
}


def normalize_meta(activities: list[dict]) -> list[dict]:
    """Normaliza Meta events al schema común."""
    out = []
    for a in activities:
        actor = a.get("actor_name", "?")
        # Skip system events (Meta automatic)
        is_system = actor.lower() in ("meta", "system", "")
        event_type = a.get("event_type", "?")
        translated = a.get("translated_event_type") or META_EVENT_LABELS.get(event_type, event_type)

        extra = a.get("extra_data")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        elif not isinstance(extra, dict):
            extra = {}

        old_val = extra.get("old_value")
        new_val = extra.get("new_value")

        # Convert micros to currency for budget events
        if "budget" in event_type and old_val and new_val:
            try:
                old_v = int(old_val) if isinstance(old_val, (int, str)) else 0
                new_v = int(new_val) if isinstance(new_val, (int, str)) else 0
                # Meta budgets in minor units (cents/centavos)
                old_disp = f"${old_v / 100:,.0f}"
                new_disp = f"${new_v / 100:,.0f}"
                delta_pct = ((new_v - old_v) / old_v * 100) if old_v > 0 else None
                change_summary = f"{old_disp} → {new_disp}"
                if delta_pct is not None:
                    change_summary += f" ({'+' if delta_pct >= 0 else ''}{delta_pct:.0f}%)"
            except (TypeError, ValueError):
                change_summary = f"{old_val} → {new_val}"
                delta_pct = None
        else:
            change_summary = ""
            if old_val and new_val:
                change_summary = f"{old_val} → {new_val}"
            elif new_val:
                change_summary = str(new_val)
            delta_pct = None

        out.append({
            "platform": "meta",
            "timestamp": a.get("event_time"),
            "actor": actor,
            "is_system": is_system,
            "event_type": event_type,
            "event_label": translated,
            "object_name": a.get("object_name") or "?",
            "object_id": a.get("object_id"),
            "change_summary": change_summary,
            "delta_pct": delta_pct,
            "raw_extra": extra,
        })
    return out


def normalize_shopify(events: list[dict]) -> list[dict]:
    """Normaliza Shopify events al schema común."""
    out = []
    for e in events:
        verb = e.get("verb") or ""
        subject = e.get("subject_type") or "?"
        message = e.get("message") or ""
        # Shopify events don't have user_name usually — author is the app/user_id only
        author = e.get("author") or ""

        # Skip noise: order_placed gets created for every order, too much volume
        if verb in ("placed", "confirmed", "paid") and subject == "Order":
            continue

        # Categorize by verb
        if "create" in verb or verb == "created":
            label = f"Creó {subject.lower()}"
        elif "update" in verb or verb == "updated":
            label = f"Editó {subject.lower()}"
        elif "destroy" in verb or "delete" in verb:
            label = f"Eliminó {subject.lower()}"
        else:
            label = f"{verb} {subject.lower()}"

        out.append({
            "platform": "shopify",
            "timestamp": e.get("created_at"),
            "actor": author or "Shopify",
            "is_system": not author,
            "event_type": verb,
            "event_label": label,
            "object_name": message[:60],
            "object_id": e.get("subject_id"),
            "change_summary": "",
            "delta_pct": None,
            "raw_extra": e,
        })
    return out


def flag_important(events: list[dict]) -> list[dict]:
    """Aplica heuristics — marca eventos críticos con flag."""
    for e in events:
        flag = None
        # Budget +100% sin justificación
        if e["delta_pct"] is not None and e["delta_pct"] > 100:
            flag = {"severity": "critical", "label": f"Budget +{e['delta_pct']:.0f}%"}
        elif e["delta_pct"] is not None and e["delta_pct"] < -50:
            flag = {"severity": "warning", "label": f"Budget {e['delta_pct']:.0f}%"}
        # Campaign paused
        elif e["event_type"] == "update_campaign_run_status":
            new_val = (e.get("raw_extra") or {}).get("new_value", "")
            if "paus" in str(new_val).lower() or new_val == "PAUSED":
                flag = {"severity": "warning", "label": "Campaña pausada"}
        # Targeting changed
        elif e["event_type"] == "update_ad_targets_spec":
            flag = {"severity": "info", "label": "Targeting cambiado"}

        e["flag"] = flag
    return events


def build_snapshot(account_id: str, since: str, until: str) -> dict:
    """Pulla cross-platform y normaliza."""
    meta_token = os.environ.get("META_ACCESS_TOKEN")
    shopify_client_id = os.environ.get("SHOPIFY_CLIENT_ID")
    shopify_client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET")
    shop = os.environ.get("SHOPIFY_SHOP", "sereneleparfum.myshopify.com")

    all_events = []

    if meta_token:
        try:
            print(f"📡 Meta activities {since}→{until}…", file=sys.stderr)
            meta_acts = fetch_meta_activities(account_id, since, until, meta_token)
            normalized = normalize_meta(meta_acts)
            all_events.extend(normalized)
            print(f"   {len(meta_acts)} raw · {len([e for e in normalized if not e['is_system']])} humanas", file=sys.stderr)
        except Exception as e:
            print(f"⚠ Meta activities failed: {e}", file=sys.stderr)

    if shopify_client_id and shopify_client_secret:
        try:
            print(f"📡 Shopify token + events…", file=sys.stderr)
            token_resp = requests.post(
                f"https://{shop}/admin/oauth/access_token",
                data={"grant_type": "client_credentials",
                      "client_id": shopify_client_id,
                      "client_secret": shopify_client_secret},
                timeout=30,
            )
            token_resp.raise_for_status()
            shopify_token = token_resp.json()["access_token"]
            shopify_evs = fetch_shopify_events(shop, shopify_token, since)
            normalized = normalize_shopify(shopify_evs)
            all_events.extend(normalized)
            print(f"   {len(shopify_evs)} raw · {len(normalized)} relevantes", file=sys.stderr)
        except Exception as e:
            print(f"⚠ Shopify events failed: {e}", file=sys.stderr)

    # TODO Google Ads change_event — bloqueado por refresh token revoked
    # When token regen → add fetch_google_changes() here

    # Sort newest first
    all_events.sort(key=lambda e: e["timestamp"] or "", reverse=True)

    # Apply heuristics
    all_events = flag_important(all_events)

    # Top actors
    actor_count = {}
    for e in all_events:
        if not e["is_system"]:
            actor_count[e["actor"]] = actor_count.get(e["actor"], 0) + 1
    top_actors = sorted(actor_count.items(), key=lambda x: -x[1])

    return {
        "fetched_at": _dt.datetime.utcnow().isoformat() + "Z",
        "since": since,
        "until": until,
        "total": len(all_events),
        "total_human": len([e for e in all_events if not e["is_system"]]),
        "top_actors": [{"name": n, "count": c} for n, c in top_actors[:10]],
        "events": all_events[:300],  # cap for render perf
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--account-id", default="act_1020250386264513")
    p.add_argument("--since", required=True)
    p.add_argument("--until", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    snap = build_snapshot(args.account_id, args.since, args.until)
    with open(args.out, "w") as f:
        json.dump(snap, f, indent=2, default=str)
    print(f"✓ Activity snapshot saved: {args.out}", file=sys.stderr)
    print(f"  Total {snap['total']} · Human {snap['total_human']} · Actors: {[a['name'] for a in snap['top_actors']]}", file=sys.stderr)


if __name__ == "__main__":
    main()
