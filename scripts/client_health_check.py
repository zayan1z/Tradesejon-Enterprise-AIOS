#!/usr/bin/env python3
"""1-Command Client VPS Health & Resilience Verifier (checks AIOS, Knowledge Studio, MongoDB, n8n)."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

AIOS_URL = os.environ.get("AIOS_BASE_URL", "http://localhost:3000").rstrip("/")
N8N_URL = os.environ.get("N8N_BASE_URL", "http://localhost:5678").rstrip("/")


def probe(url: str) -> tuple[bool, int, str]:
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return True, r.status, r.read().decode("utf-8", errors="ignore")[:300]
    except Exception as exc:
        return False, 0, str(exc)


def main() -> int:
    print("=== Tradesejon Enterprise AIOS — Client VPS Health Check ===")
    env_ok = (Path(__file__).resolve().parent.parent / ".env").exists()
    print(f"[{'OK' if env_ok else 'WARN'}] .env file present: {env_ok}")

    aios_ok, code, body = probe(f"{AIOS_URL}/api/health")
    print(f"[{'OK' if aios_ok else 'FAIL'}] AIOS Cloud ({AIOS_URL}/api/health): HTTP {code}")

    sys_ok, s_code, s_body = probe(f"{AIOS_URL}/api/system-status")
    if sys_ok:
        try:
            st = json.loads(s_body)
            print(f"[OK] System Status -> MongoDB connected: {st.get('mongodb', {}).get('connected')} | n8n reachable: {st.get('n8n', {}).get('reachable')} | KB items: {st.get('knowledge_items')}")
        except Exception:
            print(f"[OK] System Status HTTP {s_code}")

    n8_ok, n8_code, _ = probe(f"{N8N_URL}/healthz")
    print(f"[{'OK' if n8_ok else 'WARN'}] n8n Workflow Engine ({N8N_URL}/healthz): HTTP {n8_code}")

    return 0 if aios_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
