"""
Serene AI · Multi-account combiner
Toma N snapshots, los une en uno solo con sección 'comparison' embebida.

Uso:
    python3 combine.py snap_A.json snap_B.json --out combined.json
    # Después:
    python3 main.py combined.json --upload --name "..."
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from copy import deepcopy

from config import DATA_DIR, CURRENCY_TO_USD


def _to_usd(amount: float, currency: str) -> float:
    """Normalize spend to USD for fair comparison across accounts."""
    return amount * CURRENCY_TO_USD.get(currency, 1.0)


def _summarize_for_comparison(snapshot: dict) -> dict:
    """Extract minimal metrics per account for the comparison block."""
    acc = snapshot.get("account", {})
    s = snapshot.get("account_summary", {})
    currency = acc.get("currency", "USD")
    spend = float(s.get("spend", 0) or 0)
    purchases = int(s.get("purchases", 0) or 0)
    cpa = (spend / purchases) if purchases else None
    return {
        "id": acc.get("id"),
        "name": acc.get("name"),
        "currency": currency,
        "status": acc.get("status_label", "?"),
        "date_range": snapshot.get("date_range", {}),
        "spend": spend,
        "spend_usd": _to_usd(spend, currency),
        "impressions": int(s.get("impressions", 0) or 0),
        "clicks": int(s.get("clicks", 0) or 0),
        "purchases": purchases,
        "reach": int(s.get("reach", 0) or 0),
        "frequency": float(s.get("frequency", 0) or 0),
        "ctr": float(s.get("ctr", 0) or 0),
        "cpa": cpa,
        "cpa_usd": _to_usd(cpa, currency) if cpa else None,
        "atc": int(s.get("atc", 0) or 0),
        "ic": int(s.get("ic", 0) or 0),
        "active_campaigns": sum(1 for c in snapshot.get("campaigns", []) if c.get("effective_status") == "ACTIVE"),
        "total_campaigns": len(snapshot.get("campaigns", [])),
        "tracking_health": snapshot.get("tracking_health", {}).get("value_tracking", False),
    }


def combine_snapshots(snapshots: list[dict], primary_idx: int = 0) -> dict:
    """
    Returns: copy of primary snapshot enriched with `comparison` section.
    Primary's account/campaigns stay as the main view.
    Other snapshots are summarized for side-by-side comparison.
    """
    if not snapshots:
        raise ValueError("Need at least 1 snapshot")

    primary = deepcopy(snapshots[primary_idx])
    others = [s for i, s in enumerate(snapshots) if i != primary_idx]

    # Build comparison block (all accounts including primary)
    accounts_summary = [_summarize_for_comparison(s) for s in snapshots]

    # Combined totals (USD-normalized)
    total_spend_usd = sum(a["spend_usd"] for a in accounts_summary)
    total_purchases = sum(a["purchases"] for a in accounts_summary)
    total_impressions = sum(a["impressions"] for a in accounts_summary)
    total_reach = sum(a["reach"] for a in accounts_summary)
    weighted_freq = (sum(a["frequency"] * a["impressions"] for a in accounts_summary) / total_impressions) if total_impressions else 0
    weighted_ctr = (sum(a["ctr"] * a["impressions"] for a in accounts_summary) / total_impressions) if total_impressions else 0
    blended_cpa_usd = (total_spend_usd / total_purchases) if total_purchases else None

    primary["comparison"] = {
        "primary_id": primary["account"]["id"],
        "accounts": accounts_summary,
        "combined": {
            "total_spend_usd": total_spend_usd,
            "total_purchases": total_purchases,
            "total_impressions": total_impressions,
            "total_reach": total_reach,
            "blended_cpa_usd": blended_cpa_usd,
            "weighted_frequency": weighted_freq,
            "weighted_ctr": weighted_ctr,
            "accounts_count": len(snapshots),
            "active_accounts": sum(1 for a in accounts_summary if a["active_campaigns"] > 0),
        },
    }

    return primary


def main():
    parser = argparse.ArgumentParser(description="Combine multiple Meta snapshots into one")
    parser.add_argument("snapshots", nargs="+", help="Snapshot JSON filenames (in data/) — first one is primary")
    parser.add_argument("--out", required=True, help="Output filename (in data/)")
    args = parser.parse_args()

    loaded = []
    for fname in args.snapshots:
        path = DATA_DIR / fname
        if not path.exists():
            path = Path(fname)
        if not path.exists():
            print(f"❌ Snapshot not found: {fname}", file=sys.stderr)
            sys.exit(1)
        with open(path) as f:
            loaded.append(json.load(f))
        print(f"📥 Loaded: {fname}")

    print(f"🔀 Combining {len(loaded)} snapshots (primary: {loaded[0]['account']['name']})…")
    combined = combine_snapshots(loaded)

    out_path = DATA_DIR / args.out
    out_path.write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")

    c = combined["comparison"]["combined"]
    print(f"\n✓ Combined snapshot: {out_path}")
    print(f"  Accounts:  {c['accounts_count']} ({c['active_accounts']} active)")
    print(f"  Spend:     ${c['total_spend_usd']:,.0f} USD blended")
    print(f"  Purchases: {c['total_purchases']:,}")
    print(f"  CPA:       ${c['blended_cpa_usd']:,.2f} USD blended" if c['blended_cpa_usd'] else "  CPA:       —")


if __name__ == "__main__":
    main()
