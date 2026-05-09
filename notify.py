"""
Serene AI · Notifier
Genera resumen ejecutivo del dashboard + envía via backend configurado.

Backends soportados (auto-detect via env vars, primero match wins):
  1. Telegram       — TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
  2. CallMeBot WA   — CALLMEBOT_PHONE + CALLMEBOT_APIKEY
  3. Email SMTP     — SMTP_HOST + SMTP_USER + SMTP_PASS + NOTIFY_TO
  4. Webhook        — NOTIFY_WEBHOOK_URL  (POST JSON)
  5. Stdout (default fallback) — siempre imprime wa.me link

Uso standalone:
    python3 notify.py --summary "data/snap.json" --drive-link "..."
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path

import requests


# ──────────────────────────────────────────────────────────
# Summary generator
# ──────────────────────────────────────────────────────────
def generate_summary(analyzed: dict, drive_link: str | None = None) -> dict:
    """Build short summary message for notifications. Returns text + html versions."""
    acc = analyzed.get("account", {})
    s = analyzed.get("summary", {})
    stats = analyzed.get("stats", {})
    insights = analyzed.get("insights", [])
    audience = analyzed.get("audience", {}) or {}

    spend_k = (s.get("spend", 0) or 0) / 1000
    purchases = s.get("purchases", 0) or 0
    cpa = s.get("cpa") or 0
    freq = s.get("frequency", 0) or 0
    currency = acc.get("currency", "USD")

    # Critical alerts
    crit = [i for i in insights if i.get("severity") == "critical"]
    warn = [i for i in insights if i.get("severity") == "warning"]

    top_demo = audience.get("top_demo")
    demo_str = f"{top_demo['label']} ({top_demo['purchase_share']:.0f}%)" if top_demo else "—"

    # PLAIN TEXT (Telegram, WhatsApp, console)
    text_lines = [
        f"📊 *Serene AI Dashboard* · {acc.get('name', '?')}",
        f"_{analyzed.get('date_range', {}).get('since', '?')} → {analyzed.get('date_range', {}).get('until', '?')}_",
        "",
        f"💰 Spend: *{spend_k:,.0f}K {currency}*",
        f"🛒 Purchases: *{purchases}* · CPA *{cpa:,.0f} {currency}*",
        f"🔄 Frequency: *{freq:.2f}*" + (" ⚠️" if freq > 4 else ""),
        f"🎯 Top demo: *{demo_str}*",
        "",
        f"📋 {stats.get('campaigns_total', 0)} campañas · {stats.get('post_ids_with_purchases', 0)} posts con conversiones",
        f"🚨 {len(crit)} críticas · ⚠️ {len(warn)} warnings",
    ]

    if crit:
        text_lines.append("")
        text_lines.append("*Alertas críticas:*")
        for i in crit[:3]:
            text_lines.append(f"  🔴 {i.get('title', '?')}")

    if drive_link:
        text_lines.append("")
        text_lines.append(f"📁 Dashboard: {drive_link}")

    text = "\n".join(text_lines)

    # HTML version (email)
    html = (
        f"<h2>Serene AI Dashboard · {acc.get('name', '?')}</h2>"
        f"<p><em>{analyzed.get('date_range', {}).get('since', '?')} → {analyzed.get('date_range', {}).get('until', '?')}</em></p>"
        f"<ul>"
        f"<li>Spend: <strong>{spend_k:,.0f}K {currency}</strong></li>"
        f"<li>Purchases: <strong>{purchases}</strong> · CPA <strong>{cpa:,.0f} {currency}</strong></li>"
        f"<li>Frequency: <strong>{freq:.2f}</strong>{'⚠️' if freq > 4 else ''}</li>"
        f"<li>Top demo: <strong>{demo_str}</strong></li>"
        f"</ul>"
        f"<p>{stats.get('campaigns_total', 0)} campañas · {len(crit)} críticas · {len(warn)} warnings</p>"
    )
    if crit:
        html += "<h3>Alertas críticas:</h3><ul>"
        for i in crit[:5]:
            html += f"<li>🔴 {i.get('title', '?')}</li>"
        html += "</ul>"
    if drive_link:
        html += f'<p><a href="{drive_link}">Ver dashboard completo →</a></p>'

    return {
        "text": text,
        "html": html,
        "short_text": f"📊 Serene · {purchases} purch · CPA {cpa:,.0f} · {len(crit)} críticas",
    }


# ──────────────────────────────────────────────────────────
# Backends
# ──────────────────────────────────────────────────────────
def send_telegram(text: str, *, token: str, chat_id: str) -> bool:
    """Send via Telegram bot. Configure: BotFather → /newbot → save token."""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        if r.status_code == 200:
            return True
        print(f"⚠ Telegram HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"⚠ Telegram error: {e}", file=sys.stderr)
        return False


def send_callmebot(text: str, *, phone: str, apikey: str) -> bool:
    """Send via CallMeBot (free WhatsApp API). Setup: send 'I allow callmebot to send me messages' to +34 644 38 87 54, get apikey."""
    try:
        params = {"phone": phone, "text": text, "apikey": apikey}
        r = requests.get("https://api.callmebot.com/whatsapp.php", params=params, timeout=15)
        if r.status_code == 200 and "Message queued" in r.text:
            return True
        print(f"⚠ CallMeBot HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"⚠ CallMeBot error: {e}", file=sys.stderr)
        return False


def send_email(subject: str, html: str, *, host: str, port: int, user: str, password: str, to: str) -> bool:
    """Send via SMTP (Gmail, etc)."""
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(html, "html")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to
        with smtplib.SMTP_SSL(host, port) as srv:
            srv.login(user, password)
            srv.send_message(msg)
        return True
    except Exception as e:
        print(f"⚠ Email error: {e}", file=sys.stderr)
        return False


def send_webhook(payload: dict, *, url: str) -> bool:
    """Generic POST webhook."""
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.status_code < 400
    except Exception as e:
        print(f"⚠ Webhook error: {e}", file=sys.stderr)
        return False


def whatsapp_link(phone: str, text: str) -> str:
    """Build wa.me URL with prefilled text (manual fallback)."""
    encoded = urllib.parse.quote(text)
    return f"https://wa.me/{phone.lstrip('+').replace(' ','')}?text={encoded}"


# ──────────────────────────────────────────────────────────
# Auto-detect & dispatch
# ──────────────────────────────────────────────────────────
def notify(analyzed: dict, drive_link: str | None = None) -> dict:
    """Try backends in order, return result dict."""
    summary = generate_summary(analyzed, drive_link)
    result = {"sent_via": [], "failed": [], "fallback_link": None}

    # 1. Telegram
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        if send_telegram(summary["text"], token=tg_token, chat_id=tg_chat):
            result["sent_via"].append("telegram")
        else:
            result["failed"].append("telegram")

    # 2. CallMeBot WhatsApp
    cmb_phone = os.environ.get("CALLMEBOT_PHONE")
    cmb_apikey = os.environ.get("CALLMEBOT_APIKEY")
    if cmb_phone and cmb_apikey:
        if send_callmebot(summary["text"], phone=cmb_phone, apikey=cmb_apikey):
            result["sent_via"].append("callmebot")
        else:
            result["failed"].append("callmebot")

    # 3. Email
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    notify_to = os.environ.get("NOTIFY_TO")
    if smtp_host and smtp_user and smtp_pass and notify_to:
        if send_email(
            subject=summary["short_text"],
            html=summary["html"],
            host=smtp_host,
            port=int(os.environ.get("SMTP_PORT", "465")),
            user=smtp_user,
            password=smtp_pass,
            to=notify_to,
        ):
            result["sent_via"].append("email")
        else:
            result["failed"].append("email")

    # 4. Generic webhook
    webhook = os.environ.get("NOTIFY_WEBHOOK_URL")
    if webhook:
        if send_webhook({"text": summary["text"], "summary": summary["short_text"], "drive_link": drive_link}, url=webhook):
            result["sent_via"].append("webhook")
        else:
            result["failed"].append("webhook")

    # 5. Fallback: wa.me link to manually paste
    fallback_phone = os.environ.get("WHATSAPP_NUMBER")
    if fallback_phone and not result["sent_via"]:
        result["fallback_link"] = whatsapp_link(fallback_phone, summary["text"])

    return {**result, "summary": summary}


# ──────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Serene AI notifier")
    parser.add_argument("--snapshot", required=True, help="Snapshot JSON path (under data/)")
    parser.add_argument("--drive-link", help="Drive URL of the dashboard HTML")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without sending")
    args = parser.parse_args()

    # Load + analyze
    sys.path.insert(0, str(Path(__file__).parent))
    from analyze import analyze_snapshot
    snap_path = Path(__file__).parent / "data" / args.snapshot
    if not snap_path.exists():
        snap_path = Path(args.snapshot)
    with open(snap_path) as f:
        snap = json.load(f)
    analyzed = analyze_snapshot(snap)

    if args.dry_run:
        s = generate_summary(analyzed, args.drive_link)
        print("=== TEXT VERSION ===")
        print(s["text"])
        print("\n=== SHORT VERSION ===")
        print(s["short_text"])
        return

    result = notify(analyzed, args.drive_link)
    if result["sent_via"]:
        print(f"✓ Sent via: {', '.join(result['sent_via'])}")
    if result["failed"]:
        print(f"✗ Failed: {', '.join(result['failed'])}")
    if result.get("fallback_link"):
        print(f"📱 Manual WhatsApp link:\n  {result['fallback_link']}")
    if not result["sent_via"] and not result.get("fallback_link"):
        print("⚠ No backend configured. Set env vars TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID, CALLMEBOT_*, SMTP_*, NOTIFY_WEBHOOK_URL, or WHATSAPP_NUMBER")


if __name__ == "__main__":
    main()
