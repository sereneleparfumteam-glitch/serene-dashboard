"""
Serene AI · Historical analytics
Carga snapshots archivados (data/history/) y computa trends, anomalies, forecasts.

Funciona automáticamente cuando hay >=3 días de history. Antes retorna data vacía/null.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any


HISTORY_DIR = Path(__file__).parent / "data" / "history"


def list_history(kind: str = "meta") -> list[Path]:
    """Lista snapshots históricos ordenados por fecha desc.
    kind: meta | shopify | activity
    """
    if not HISTORY_DIR.exists():
        return []
    files = sorted(HISTORY_DIR.glob(f"{kind}_*.json"), reverse=True)
    return files


def load_history_metric(kind: str, extract_fn, max_days: int = 30) -> list[tuple[str, float]]:
    """Carga métrica por día. extract_fn(snapshot) → float.
    Retorna lista de (date_str, value).
    """
    files = list_history(kind)[:max_days]
    out = []
    for fp in files:
        try:
            with open(fp) as f:
                snap = json.load(f)
            val = extract_fn(snap)
            # filename pattern: meta_YYYY-MM-DD.json
            date_str = fp.stem.split("_", 1)[1]
            out.append((date_str, float(val) if val is not None else 0))
        except Exception:
            continue
    return sorted(out)  # asc by date


def zscore_anomaly(series: list[float], threshold: float = 2.0) -> dict | None:
    """Detecta si el último valor es anómalo (z-score > threshold).
    Necesita mínimo 5 puntos.
    """
    if len(series) < 5:
        return None
    mean = sum(series[:-1]) / (len(series) - 1)
    var = sum((x - mean) ** 2 for x in series[:-1]) / (len(series) - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    z = (series[-1] - mean) / std
    if abs(z) >= threshold:
        return {
            "z_score": z,
            "current": series[-1],
            "mean": mean,
            "std": std,
            "direction": "up" if z > 0 else "down",
            "magnitude": "extreme" if abs(z) >= 3 else "significant",
        }
    return None


def linear_forecast(series: list[float], days_ahead: int = 7) -> list[float] | None:
    """Forecast lineal simple (least squares). Necesita >=7 puntos."""
    n = len(series)
    if n < 7:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(series) / n
    num = sum((xs[i] - mean_x) * (series[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return None
    slope = num / den
    intercept = mean_y - slope * mean_x
    forecast = []
    for i in range(n, n + days_ahead):
        v = intercept + slope * i
        forecast.append(max(0, v))  # no negativos
    return forecast


def compute_history_summary() -> dict:
    """Computa todo lo histórico: revenue trend, spend trend, ROAS trend, anomalies, forecasts."""
    # Revenue history
    revenue_series = load_history_metric(
        "shopify",
        lambda s: float(sum(
            (o.get("totalPriceSet", {}).get("shopMoney", {}) or {}).get("amount", 0) or 0
            for o in (s.get("orders") or [])
        )),
        max_days=30,
    )

    # Spend history
    spend_series = load_history_metric(
        "meta",
        lambda s: float((s.get("account_summary") or {}).get("spend", 0)),
        max_days=30,
    )

    # Activity volume
    activity_series = load_history_metric(
        "activity",
        lambda s: s.get("total_human", 0),
        max_days=30,
    )

    rev_vals = [v for _, v in revenue_series]
    spend_vals = [v for _, v in spend_series]
    act_vals = [v for _, v in activity_series]

    return {
        "days_collected": len(revenue_series),
        "revenue": {
            "series": revenue_series,
            "anomaly": zscore_anomaly(rev_vals) if rev_vals else None,
            "forecast_7d": linear_forecast(rev_vals) if len(rev_vals) >= 7 else None,
        },
        "spend": {
            "series": spend_series,
            "anomaly": zscore_anomaly(spend_vals) if spend_vals else None,
            "forecast_7d": linear_forecast(spend_vals) if len(spend_vals) >= 7 else None,
        },
        "activity_volume": {
            "series": activity_series,
            "anomaly": zscore_anomaly(act_vals) if act_vals else None,
        },
    }
