#!/usr/bin/env python3
import os, sys, urllib.request
from pathlib import Path
N8N_URL = os.environ.get("N8N_API_URL", "http://localhost:5678/api/v1").rstrip("/")
N8N_KEY = os.environ.get("N8N_API_KEY", "")
if not N8N_KEY: sys.exit("Set N8N_API_KEY environment variable.")
for wf in sorted((Path(__file__).resolve().parent.parent / "n8n-workflows").glob("[0-2][0-9]_*.json")):
    req = urllib.request.Request(f"{N8N_URL}/workflows", data=wf.read_bytes(), headers={"Content-Type": "application/json", "X-N8N-API-KEY": N8N_KEY}, method="POST")
    with urllib.request.urlopen(req) as r: print(f"[OK] Imported {wf.name} -> {r.status}")
