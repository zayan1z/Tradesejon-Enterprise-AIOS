#!/usr/bin/env python3
"""Idempotent 1-Click n8n REST API Importer & Auto-Publisher for Client VPS."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

N8N_URL = os.environ.get("N8N_API_URL", "http://localhost:5678/api/v1").rstrip("/")
N8N_KEY = os.environ.get("N8N_API_KEY", "").strip()
WF_DIR = Path(__file__).resolve().parent.parent / "n8n-workflows"


def api_call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{N8N_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json", "X-N8N-API-KEY": N8N_KEY},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8") or "{}"
        return json.loads(raw)


def main() -> int:
    if not N8N_KEY:
        print("Set N8N_API_KEY environment variable (n8n UI -> Settings -> n8n API -> Create Key).")
        return 1

    existing_by_name: dict[str, str] = {}
    try:
        listing = api_call("GET", "/workflows?limit=200")
        for wf in listing.get("data", []):
            if wf.get("name") and wf.get("id"):
                existing_by_name[wf["name"]] = str(wf["id"])
    except Exception as exc:
        print(f"[WARN] Could not list existing workflows: {exc}")

    files = sorted(WF_DIR.glob("[0-2][0-9]_*.json"))
    tpl = WF_DIR / "Template_Native_Telegram_Bot_Agent.json"
    if tpl.exists():
        files.append(tpl)

    published = 0
    for wf_path in files:
        wf_obj = json.loads(wf_path.read_text(encoding="utf-8"))
        # Clean fields rejected on n8n REST POST/PUT
        clean_payload = {
            "name": wf_obj.get("name", wf_path.stem),
            "nodes": wf_obj.get("nodes", []),
            "connections": wf_obj.get("connections", {}),
            "settings": wf_obj.get("settings", {"executionOrder": "v1"}),
        }
        name = clean_payload["name"]
        try:
            if name in existing_by_name:
                wf_id = existing_by_name[name]
                api_call("PUT", f"/workflows/{wf_id}", clean_payload)
                action = "Updated"
            else:
                created = api_call("POST", "/workflows", clean_payload)
                wf_id = str(created.get("id", ""))
                action = "Imported"

            should_activate = not wf_path.name.startswith("Template_")
            if should_activate and wf_id:
                try:
                    api_call("POST", f"/workflows/{wf_id}/activate", {})
                    published += 1
                    print(f"[OK] {action} & Published: {wf_path.name} (id={wf_id})")
                except urllib.error.HTTPError as act_err:
                    print(f"[WARN] {action} {wf_path.name} (id={wf_id}), activate HTTP {act_err.code}")
            else:
                print(f"[OK] {action} template (inactive by default): {wf_path.name}")
        except Exception as exc:
            print(f"[FAIL] {wf_path.name}: {exc}")

    print(f"\nCompleted n8n API sync. Published active workflows: {published}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
