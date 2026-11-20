#!/usr/bin/env python3
"""Universal CLI Knowledge & File Sync Tool (syncs text, MD, JSON, CSV, DOCX, XLSX, PDF across AIOS, MongoDB & n8n)."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = ROOT / "rag-knowledge-base" / "uploads"
AIOS_BASE = os.environ.get("AIOS_BASE_URL", "http://localhost:3000").rstrip("/")


def sync_file(file_path: Path, category: str = "uploaded_file") -> bool:
    raw = file_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    payload = json.dumps({
        "title": file_path.stem,
        "filename": file_path.name,
        "category": category,
        "contentBase64": b64,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{AIOS_BASE}/api/knowledge",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("ok"):
                print(f"[SYNCED] {file_path.name} -> chunks={res['entry']['chunks_count']} n8n={res['sync_receipt']['n8n_rag'].get('via')}")
                return True
            print(f"[SKIP] {file_path.name}: {res.get('error')}")
            return False
    except Exception as exc:
        print(f"[ERROR] Could not reach {AIOS_BASE}/api/knowledge ({exc}). Start containers first with 1_CLICK_DEPLOY.sh")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync local text/files into AIOS Knowledge Base, MongoDB & n8n")
    parser.add_argument("target", nargs="?", help="File or directory path (default: rag-knowledge-base/uploads)")
    parser.add_argument("--text", help="Direct text content to ingest cleanly")
    parser.add_argument("--title", default="CLI Synced Rule", help="Title for --text ingestion")
    parser.add_argument("--category", default="general", help="Knowledge category")
    args = parser.parse_args()

    if args.text:
        payload = json.dumps({"title": args.title, "category": args.category, "content": args.text}).encode("utf-8")
        req = urllib.request.Request(f"{AIOS_BASE}/api/knowledge", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(resp.read().decode("utf-8"))
        return 0

    target = Path(args.target) if args.target else UPLOADS_DIR
    if not target.exists():
        print(f"[INFO] Nothing to sync at {target}")
        return 0

    files = [target] if target.is_file() else [p for p in sorted(target.iterdir()) if p.is_file() and not p.name.startswith(".")]
    if not files:
        print(f"[INFO] Drop files in {target} or open http://localhost:3000/knowledge.html to upload.")
        return 0

    ok_count = sum(1 for f in files if sync_file(f, args.category))
    print(f"\nCompleted: {ok_count}/{len(files)} file(s) synced across system.")
    return 0 if ok_count == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
