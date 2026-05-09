"""
Serene AI · Renderer
Toma el resultado del analyzer y produce HTML usando Jinja2 template.
"""
from __future__ import annotations
import datetime as _dt
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from config import TEMPLATES_DIR, OUTPUT_DIR, CURRENCY_TO_USD


# ──────────────────────────────────────────────────────────
# Jinja2 filters
# ──────────────────────────────────────────────────────────
def _fmt(v) -> str:
    """Format numbers compactly: 36732 -> 36.7K, 2827677 -> 2.83M, 0 -> 0."""
    if v is None or v == "":
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v == 0:
        return "0"
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{v/1_000:.1f}K"
    return f"{v:,.0f}"


def _truncate_smart(s: str, max_len: int = 40) -> str:
    if not s or len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _leak_step(funnel: dict) -> dict | None:
    """Detect funnel step with biggest drop."""
    drops = [
        ("CTR", 100 - funnel["ctr_rate"]),
        ("Click → LPV", 100 - funnel["click_to_lpv"]),
        ("LPV → ATC", 100 - funnel["lpv_to_atc"]),
        ("ATC → Checkout", 100 - funnel["atc_to_ic"]),
        ("Checkout → Buy", 100 - funnel["ic_to_purchase"]),
    ]
    if not drops:
        return None
    max_drop = max(drops, key=lambda x: x[1])
    return {"name": max_drop[0], "drop": round(max_drop[1], 1)}


# ──────────────────────────────────────────────────────────
# Renderer
# ──────────────────────────────────────────────────────────
def render_dashboard(analyzed: dict, output_filename: str | None = None) -> Path:
    """Renders dashboard.html.j2 with the analyzed data."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["fmt"] = _fmt
    env.filters["truncate_smart"] = _truncate_smart
    env.filters["leak_step"] = _leak_step

    template = env.get_template("dashboard.html.j2")

    # Compute extras
    summary = analyzed["summary"]
    account = analyzed["account"]
    currency = account.get("currency", "USD")
    fx_to_usd = CURRENCY_TO_USD.get(currency, 1.0)
    summary["spend_usd"] = summary["spend"] * fx_to_usd

    # Best/worst CPA per campaign
    valid_cpas = [c["cpa"] for c in analyzed["campaigns"] if c.get("cpa")]
    campaigns_best_cpa = min(valid_cpas) if valid_cpas else None
    campaigns_worst_cpa = max(valid_cpas) if valid_cpas else None

    now = _dt.datetime.now()

    ctx = {
        **analyzed,
        "campaigns_best_cpa": campaigns_best_cpa,
        "campaigns_worst_cpa": campaigns_worst_cpa,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "generated_at_short": now.strftime("%d %b %H:%M"),
        "data_source_label": "DATA REAL · META API",
    }

    html = template.render(**ctx)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if output_filename is None:
        slug = account["id"].replace("act_", "")
        output_filename = f"dashboard_{slug}_{now.strftime('%Y-%m-%d_%H%M')}.html"
    out_path = OUTPUT_DIR / output_filename
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import json
    from pathlib import Path
    from analyze import analyze_snapshot

    snap_path = Path(__file__).parent / "data" / "serene_dormant_2026_02.json"
    with open(snap_path) as f:
        snapshot = json.load(f)

    analyzed = analyze_snapshot(snapshot)
    out = render_dashboard(analyzed, output_filename="dashboard_test.html")
    print(f"✓ Rendered: {out} ({out.stat().st_size:,} bytes)")
