"""
Serene AI Performance Command Center — Config
"""
import os
from pathlib import Path

# === Paths ===
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# === Meta API ===
API_BASE = "https://graph.facebook.com/v22.0"
ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")  # Set via env var when running standalone

# === Accounts ===
ACCOUNTS = {
    "serene_dormant": {
        "id": "act_935968735451363",
        "name": "SERENE COL 2.0 CTA 2",
        "currency": "COP",
        "timezone": "America/Bogota",
        "status": "dormant",
    },
    "serene_prod": {
        "id": "act_1020250386264513",
        "name": "PRUEBA GABRIELA",
        "currency": "USD",  # to confirm
        "timezone": "America/Bogota",
        "status": "pending_access",
    },
}

# Currency conversion (rough, for blended view)
CURRENCY_TO_USD = {
    "COP": 0.00025,    # ~4000 COP/USD
    "USD": 1.0,
    "MXN": 0.05,
    "EUR": 1.10,
}

# === Scoring rules (1-100) ===
SCORE_WEIGHTS = {
    "cpa_relative": 30,       # CPA vs account avg
    "ctr_relative": 20,       # CTR vs account avg
    "frequency_health": 20,   # 1.0-2.5 ideal, >4.0 bad
    "spend_efficiency": 15,   # purchases per spend unit
    "stability": 15,          # consistent performance
}

# === Alert thresholds ===
THRESHOLDS = {
    "roas_critical": 1.5,             # below = pause
    "roas_warning": 2.5,              # below = monitor
    "roas_target": 3.0,               # business minimum
    "cpa_increase_warn_pct": 0.20,    # +20% triggers warning
    "cpa_increase_critical_pct": 0.40, # +40% triggers critical
    "frequency_warn": 2.5,
    "frequency_critical": 4.0,
    "ctr_drop_warn_pct": 0.15,        # -15% triggers warning
    "kill_rule_multiplier": 3.0,      # 3x CPA target = pause
    "scale_min_stability_days": 5,
    "scale_min_score": 80,
    "scale_increase_pct": 0.20,       # +20% budget bump
}

# === Output ===
DASHBOARD_VERSION = "v3"
DRIVE_REMOTE = "serene"   # rclone remote name
DRIVE_FOLDER_ID = None    # None = root of remote

# === Date defaults ===
DEFAULT_DATE_PRESET = "last_28d"
