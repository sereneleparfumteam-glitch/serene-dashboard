"""
Serene AI · Historical campaigns extractor (Nivel 3 de prediction).

Pulla TODAS las campañas paused de los últimos 180 días de Meta + sus insights
acumulados, para construir dataset de referencia para predicción de duración
de campañas activas.

Estrategia:
- 1 request batch para listar campañas pasadas
- 1 request por chunk de N campañas para insights agregados (start→stop)
- Cruce con activity log para identificar pause manual vs degradación
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


def _request(path: str, params: dict, token: str, retries: int = 3) -> dict:
    url = f"https://graph.facebook.com/{META_API_VERSION}/{path}"
    p = {**params, "access_token": token}
    for attempt in range(retries):
        r = requests.get(url, params=p, timeout=60)
        if r.status_code == 429:
            import time
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"Meta API: {data['error']}")
        return data
    raise RuntimeError("Rate limited after retries")


def list_paused_campaigns(account_id: str, token: str, days_back: int = 180) -> list[dict]:
    """Lista campañas que se pausaron en los últimos N días.

    Note: Meta API no permite filtrar paused desde fecha directamente.
    Estrategia: pullear TODAS las campañas (active + paused) con
    updated_time desde N días atrás. Filtrar PAUSED en código.
    """
    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    fields = (
        "id,name,objective,status,effective_status,"
        "created_time,updated_time,start_time,stop_time,"
        "daily_budget,lifetime_budget,buying_type,bid_strategy,special_ad_categories"
    )
    out = []
    cursor = None
    pages = 0
    while pages < 30:  # max 3000 campaigns safety
        params = {
            "fields": fields,
            "limit": 100,
        }
        if cursor:
            params["after"] = cursor
        d = _request(f"{account_id}/campaigns", params, token)
        out.extend(d.get("data", []))
        cursor = d.get("paging", {}).get("cursors", {}).get("after")
        if not d.get("paging", {}).get("next"):
            break
        pages += 1

    # Filter paused with updated_time in window
    cutoff_dt = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days_back)
    paused = []
    for c in out:
        if c.get("effective_status") not in ("PAUSED",):
            continue
        upd_str = c.get("updated_time")
        if not upd_str:
            continue
        try:
            upd_dt = _dt.datetime.fromisoformat(upd_str.replace("+0000", "+00:00"))
        except Exception:
            continue
        if upd_dt >= cutoff_dt:
            paused.append(c)
    return paused


def get_campaign_insights_aggregate(campaign_id: str, token: str,
                                     start: str, stop: str | None) -> dict | None:
    """Pull insights agregados desde start (or campaign created) hasta stop (or today)."""
    if not stop:
        stop = _dt.date.today().isoformat()

    params = {
        "fields": "spend,impressions,clicks,reach,frequency,ctr,actions",
        "time_range": json.dumps({"since": start, "until": stop}),
        "level": "campaign",
    }
    try:
        d = _request(f"{campaign_id}/insights", params, token)
        rows = d.get("data", [])
        if not rows:
            return None
        row = rows[0]
        # Extract purchases from actions
        purchases = 0
        for a in row.get("actions", []) or []:
            if a.get("action_type") in ("offsite_conversion.fb_pixel_purchase",
                                         "omni_purchase", "purchase"):
                try:
                    purchases = max(purchases, int(float(a.get("value", 0) or 0)))
                except (TypeError, ValueError):
                    pass
        spend = float(row.get("spend", 0) or 0)
        return {
            "spend": spend,
            "impressions": int(float(row.get("impressions", 0) or 0)),
            "clicks": int(float(row.get("clicks", 0) or 0)),
            "reach": int(float(row.get("reach", 0) or 0)),
            "frequency": float(row.get("frequency", 0) or 0),
            "ctr": float(row.get("ctr", 0) or 0),
            "purchases": purchases,
            "cpa": (spend / purchases) if purchases > 0 else None,
        }
    except Exception as e:
        print(f"  ⚠ insights {campaign_id}: {e}", file=sys.stderr)
        return None


def _compute_duration_days(camp: dict) -> int | None:
    """Días vivos = stop_time - start_time (or updated - start if stop missing)."""
    start = camp.get("start_time") or camp.get("created_time")
    end = camp.get("stop_time") or camp.get("updated_time")
    if not start or not end:
        return None
    try:
        s = _dt.datetime.fromisoformat(start.replace("+0000", "+00:00"))
        e = _dt.datetime.fromisoformat(end.replace("+0000", "+00:00"))
        return max(1, (e - s).days)
    except Exception:
        return None


def build_history(account_id: str, days_back: int = 180,
                  enrich_top_n: int = 50) -> dict:
    """Pulla campañas paused last N days + enriquece top N con insights agregados."""
    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("META_ACCESS_TOKEN no configurado")

    print(f"📡 Pulling paused campaigns last {days_back}d…", file=sys.stderr)
    paused = list_paused_campaigns(account_id, token, days_back)
    print(f"   {len(paused)} paused campaigns found", file=sys.stderr)

    # Compute duration for all
    for c in paused:
        c["duration_days"] = _compute_duration_days(c)
        c["start_date"] = (c.get("start_time") or c.get("created_time") or "")[:10]
        c["stop_date"] = (c.get("stop_time") or c.get("updated_time") or "")[:10]

    # Filter ones with reasonable duration (>=3d, <=365d) — drop noise
    valid = [c for c in paused if c.get("duration_days") and 3 <= c["duration_days"] <= 365]
    print(f"   {len(valid)} with valid duration (3-365d)", file=sys.stderr)

    # Enrich top N most recent with insights (rest keep duration + metadata only)
    valid.sort(key=lambda c: c.get("updated_time", ""), reverse=True)
    for i, c in enumerate(valid[:enrich_top_n]):
        if i % 10 == 0:
            print(f"   enriching {i}/{min(enrich_top_n, len(valid))}…", file=sys.stderr)
        ins = get_campaign_insights_aggregate(
            c["id"], token,
            c.get("start_date") or "2025-01-01",
            c.get("stop_date"),
        )
        if ins:
            c["insights"] = ins

    enriched_count = sum(1 for c in valid if c.get("insights"))

    return {
        "account_id": account_id,
        "days_back": days_back,
        "fetched_at": _dt.datetime.utcnow().isoformat() + "Z",
        "total_paused_in_window": len(paused),
        "with_valid_duration": len(valid),
        "enriched": enriched_count,
        "campaigns": valid,  # all valid, top N have insights
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--account-id", default="act_1020250386264513")
    p.add_argument("--days-back", type=int, default=180)
    p.add_argument("--enrich-top-n", type=int, default=50)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    snap = build_history(args.account_id, args.days_back, args.enrich_top_n)
    with open(args.out, "w") as f:
        json.dump(snap, f, indent=2, default=str)
    print(f"✓ History snapshot saved: {args.out}", file=sys.stderr)
    print(f"  Total paused: {snap['total_paused_in_window']} · Valid: {snap['with_valid_duration']} · Enriched: {snap['enriched']}", file=sys.stderr)


if __name__ == "__main__":
    main()
