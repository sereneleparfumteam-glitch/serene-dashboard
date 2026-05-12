"""
Serene AI · Campaign duration predictor v2 (Nivel 2 + 3)

Combina:
- Nivel 2: features de la campaña activa (edad, freq, CPA, audience size si disponible)
- Nivel 3: matching con campañas similares pasadas → predicción basada en historial real

Output:
- predicted_days_remaining: int
- status: fresh|healthy|warming|fatigued|burnt
- confidence: 0-100 (basado en cantidad de comparables)
- comparable_count: int
- reason: str (explicación human-readable)
"""
from __future__ import annotations
import datetime as _dt
from typing import Any


def _parse_iso(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("+0000", "+00:00"))
    except Exception:
        return None


def _age_days(camp: dict) -> int | None:
    """Días desde start_time o created_time hasta hoy."""
    start = _parse_iso(camp.get("start_time") or camp.get("created_time"))
    if not start:
        return None
    now = _dt.datetime.now(_dt.timezone.utc)
    return max(0, (now - start).days)


def _similarity(active: dict, historical: dict) -> float:
    """Score 0-1 de similitud entre campaña activa e histórica.

    Factores:
    - Mismo objective (40% peso)
    - Mismo buying_type (10%)
    - Mismo bid_strategy (15%)
    - Daily budget similar dentro de 50% (15%)
    - Nombre similar — tokens compartidos (20%)
    """
    score = 0.0

    # Objective
    if active.get("objective") == historical.get("objective"):
        score += 0.40

    # Buying type
    if active.get("buying_type") == historical.get("buying_type"):
        score += 0.10

    # Bid strategy
    if active.get("bid_strategy") == historical.get("bid_strategy"):
        score += 0.15

    # Daily budget similar (within 50%)
    try:
        a_budget = float(active.get("daily_budget") or 0)
        h_budget = float(historical.get("daily_budget") or 0)
        if a_budget > 0 and h_budget > 0:
            ratio = min(a_budget, h_budget) / max(a_budget, h_budget)
            if ratio >= 0.5:
                score += 0.15 * ratio
    except (TypeError, ValueError):
        pass

    # Name token overlap (rough)
    a_name = (active.get("name") or "").lower()
    h_name = (historical.get("name") or "").lower()
    a_tokens = set(t for t in a_name.split() if len(t) > 3)
    h_tokens = set(t for t in h_name.split() if len(t) > 3)
    if a_tokens and h_tokens:
        overlap = len(a_tokens & h_tokens) / max(len(a_tokens), len(h_tokens))
        score += 0.20 * overlap

    return score


def find_comparables(active: dict, historical_pool: list[dict],
                     min_similarity: float = 0.45, top_k: int = 8) -> list[dict]:
    """Retorna las K campañas más similares con sus duraciones."""
    scored = []
    for h in historical_pool:
        if h.get("id") == active.get("id"):
            continue  # skip self
        dur = h.get("duration_days")
        if not dur:
            continue
        sim = _similarity(active, h)
        if sim >= min_similarity:
            scored.append((sim, h))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [h for _, h in scored[:top_k]]


def _percentile(values: list[float], p: float) -> float:
    """Simple percentile (0-100)."""
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] * (c - k) + s[c] * (k - f)


def predict_duration_v2(camp: dict, account_summary: dict,
                        historical: list[dict],
                        days_in_period: int = 7,
                        fatigue_threshold: float = 5.0) -> dict:
    """Predicción enriquecida con history.

    Returns dict con:
    - days_remaining: int or None
    - status: fresh|healthy|warming|fatigued|burnt|paused
    - confidence: 0-100
    - reason: str
    - freq_now, freq_per_day
    - comparable_count
    - comparable_median_days
    - comparable_range: [p25, p75]
    """
    if camp.get("effective_status") not in ("ACTIVE",):
        return {
            "days_remaining": None,
            "status": "paused",
            "reason": "Campaña pausada",
            "confidence": 100,
            "freq_now": camp.get("frequency", 0),
            "freq_per_day": 0,
            "comparable_count": 0,
            "comparable_median_days": None,
            "comparable_range": None,
        }

    freq = float(camp.get("frequency", 0) or 0)
    spend = float(camp.get("spend", 0) or 0)
    purchases = int(camp.get("purchases", 0) or 0)
    cpa_now = (spend / purchases) if purchases > 0 else None
    age = _age_days(camp) or 0

    # ─── Status based on current freq ────────────────────────
    if freq <= 1.5:
        status = "fresh"
    elif freq <= 2.5:
        status = "healthy"
    elif freq <= 3.5:
        status = "warming"
    elif freq <= 4.5:
        status = "fatigued"
    else:
        status = "burnt"

    # ─── Nivel 2: heuristic prediction from freq ────────────
    if days_in_period > 0 and freq > 0:
        freq_per_day = freq / days_in_period
        if freq >= fatigue_threshold:
            heuristic_days = 0
        elif freq_per_day > 0.05:
            heuristic_days = max(0, int((fatigue_threshold - freq) / freq_per_day))
        else:
            heuristic_days = 30
    else:
        freq_per_day = 0
        heuristic_days = 30

    # ─── Nivel 3: comparable past campaigns ─────────────────
    comparables = find_comparables(camp, historical)
    comp_count = len(comparables)
    comp_durations = [c["duration_days"] for c in comparables if c.get("duration_days")]

    if comp_durations:
        median_dur = sorted(comp_durations)[len(comp_durations) // 2]
        p25 = _percentile(comp_durations, 25)
        p75 = _percentile(comp_durations, 75)
        # Predicción Nivel 3: median_duration - age
        comparable_days = max(0, int(median_dur - age))
    else:
        median_dur = None
        p25, p75 = None, None
        comparable_days = None

    # ─── Fusion: weighted average ───────────────────────────
    if comparable_days is not None and comp_count >= 3:
        # Mucho match → confiar más en histórico
        w_hist = min(0.75, 0.30 + comp_count * 0.05)
        w_heur = 1 - w_hist
        days_remaining = int(comparable_days * w_hist + heuristic_days * w_heur)
        confidence = min(95, 40 + comp_count * 7)
        if comp_count >= 5:
            reason = f"Basado en {comp_count} campañas similares (median {median_dur}d). Esta lleva {age}d activa."
        else:
            reason = f"{comp_count} comparables encontradas (range {int(p25)}-{int(p75)}d). Edad actual: {age}d."
    elif comparable_days is not None:
        # Pocos matches → blend más conservador
        days_remaining = int(comparable_days * 0.40 + heuristic_days * 0.60)
        confidence = 50
        reason = f"Pocas comparables ({comp_count}). Predicción mezclada con heurística freq."
    else:
        # No comparables → solo heurística
        days_remaining = heuristic_days
        confidence = 35
        reason = "Sin campañas comparables en histórico. Solo heurística freq."

    # CPA degradation override: si CPA está alto vs account, restar días
    account_cpa = float(account_summary.get("cpa", 0) or 0)
    if cpa_now and account_cpa and cpa_now > account_cpa * 1.5:
        days_remaining = max(0, days_remaining - 3)
        reason += " CPA 1.5x sobre account avg → -3d."

    # Cap days_remaining at 60
    days_remaining = min(60, days_remaining)

    return {
        "days_remaining": days_remaining,
        "status": status,
        "reason": reason,
        "confidence": confidence,
        "freq_now": round(freq, 2),
        "freq_per_day": round(freq_per_day, 3),
        "age_days": age,
        "comparable_count": comp_count,
        "comparable_median_days": median_dur,
        "comparable_range": [int(p25), int(p75)] if p25 is not None else None,
    }
