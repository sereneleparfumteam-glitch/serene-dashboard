"""
Serene AI · Main orchestrator
Carga snapshot → analiza → renderiza → sube al Drive (opcional)
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from analyze import analyze_snapshot
from render import render_dashboard
from config import DATA_DIR, OUTPUT_DIR, DRIVE_REMOTE


def load_snapshot(snapshot_path: Path) -> dict:
    if not snapshot_path.exists():
        raise FileNotFoundError(f"Snapshot no existe: {snapshot_path}")
    with open(snapshot_path) as f:
        return json.load(f)


def upload_to_drive(html_path: Path, drive_remote: str = DRIVE_REMOTE,
                    folder_id: str | None = None, public_name: str | None = None) -> str:
    """Sube via rclone. Retorna ruta remota."""
    import subprocess
    target = public_name or html_path.name
    args = ["rclone", "copyto", str(html_path), f"{drive_remote}:{target}"]
    if folder_id:
        args.extend(["--drive-root-folder-id", folder_id])
    try:
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)
        return f"{drive_remote}:{target}"
    except subprocess.CalledProcessError as e:
        print(f"⚠ rclone error: {e.stderr}", file=sys.stderr)
        return ""


def run(snapshot_filename: str, *, upload: bool = False, public_name: str | None = None) -> Path:
    snapshot_path = DATA_DIR / snapshot_filename
    print(f"📥 Loading: {snapshot_path}")
    snap = load_snapshot(snapshot_path)

    print(f"🧠 Analyzing {snap['account']['name']}…")
    analyzed = analyze_snapshot(snap)
    s = analyzed["stats"]
    print(f"   {s['campaigns_total']} campaigns ({s['campaigns_scale']} scale · {s['campaigns_monitor']} monitor · {s['campaigns_stop']} stop)")
    print(f"   {s['insights_critical']} critical + {s['insights_warning']} warning insights")

    print(f"🎨 Rendering HTML…")
    out_path = render_dashboard(analyzed)
    print(f"   ✓ {out_path} ({out_path.stat().st_size:,} bytes)")

    if upload:
        print(f"☁  Uploading to Drive {DRIVE_REMOTE}…")
        remote = upload_to_drive(out_path, public_name=public_name)
        if remote:
            print(f"   ✓ {remote}")
        else:
            print(f"   ✗ Upload failed (still in {out_path})")

    return out_path


def main():
    parser = argparse.ArgumentParser(description="Serene AI Dashboard generator")
    parser.add_argument("snapshot", help="Snapshot JSON filename (relative to data/)")
    parser.add_argument("--upload", action="store_true", help="Upload to Drive after render")
    parser.add_argument("--name", help="Custom name for the uploaded file")
    args = parser.parse_args()
    run(args.snapshot, upload=args.upload, public_name=args.name)


if __name__ == "__main__":
    main()
