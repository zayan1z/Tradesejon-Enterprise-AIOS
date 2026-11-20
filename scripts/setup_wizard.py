#!/usr/bin/env python3
"""Interactive & Non-Interactive 1-Click Credential Wizard generating a production-ready .env."""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = ROOT / ".env.example"
ENV_PATH = ROOT / ".env"

AUTO_SECRETS = ("N8N_ENCRYPTION_KEY", "TRADESAJON_BRIDGE_SECRET", "WORKSHOP_LAUNCH_TOKEN", "AIOS_INTERNAL_TOKEN")

PROMPTS = [
    ("BRAND_NAME", "White-Label Brand Name [YourBrandAIOS]: "),
    ("WEBHOOK_URL", "Public n8n HTTPS URL for Telegram Webhooks (e.g. https://n8n.yourdomain.com/ or Enter for localhost): "),
    ("GROQ_API_KEY", "Groq API Key (for AI & Whisper Voice STT, Enter to configure later): "),
    ("TELEGRAM_CEO_BOT_TOKEN", "Telegram CEO Bot Token from @BotFather (optional, Enter to skip): "),
    ("TELEGRAM_PR_BOT_TOKEN", "Telegram PR Bot Token from @BotFather (optional, Enter to skip): "),
    ("TELEGRAM_TRADER_BOT_TOKEN", "Telegram Trader Bot Token from @BotFather (optional, Enter to skip): "),
    ("CEO_USER_IDS", "Authorized CEO Telegram Numeric User ID(s) comma-separated (optional, Enter to skip): "),
    ("DISCORD_BOT_TOKEN", "Discord Bot Token (optional, Enter to skip): "),
    ("DISCORD_GUILD_ID", "Discord Server (Guild) ID (optional, Enter to skip): "),
]


def build_env(*, non_interactive: bool = False) -> Path:
    lines = EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    prompt_map = dict(PROMPTS)
    collected: dict[str, str] = {}

    if not non_interactive and sys.stdin.isatty():
        print("\n=== White-Label Enterprise AIOS — 1-Click Client VPS Setup ===\n")
        print("Press Enter on any optional key to skip now (you can edit .env or n8n UI later).\n")
        for key, label in PROMPTS:
            val = os.environ.get(key) or input(label).strip()
            if val:
                collected[key] = val
    else:
        for key, _ in PROMPTS:
            val = os.environ.get(key, "").strip()
            if val:
                collected[key] = val

    out: list[str] = []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            k = k.strip()
            if k in AUTO_SECRETS:
                val = os.environ.get(k, "").strip()
                if not val or val.startswith("REPLACE_WITH_"):
                    val = secrets.token_hex(16)
                out.append(f"{k}={val}")
                continue
            if k in collected:
                out.append(f"{k}={collected[k]}")
                continue
            env_override = os.environ.get(k, "").strip()
            if env_override:
                out.append(f"{k}={env_override}")
                continue
            if k in prompt_map and v.startswith("REPLACE_WITH_"):
                out.append(f"{k}=")
                continue
            # Ensure empty CEO_CHAT_ID mirrors first CEO_USER_IDS if entered
            if k == "CEO_CHAT_ID" and not v and collected.get("CEO_USER_IDS"):
                first_id = collected["CEO_USER_IDS"].split(",")[0].strip()
                out.append(f"{k}={first_id}")
                continue
        out.append(line)

    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[OK] Generated {ENV_PATH} with unique cryptographic keys.")
    return ENV_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate production .env for Tradesejon Enterprise AIOS")
    parser.add_argument("--non-interactive", action="store_true", help="Do not prompt; use env overrides & cryptographic defaults")
    parser.add_argument("--force", action="store_true", help="Overwrite existing .env")
    args = parser.parse_args()

    if ENV_PATH.exists() and not args.force:
        print(f"[OK] {ENV_PATH} already exists (use --force to regenerate).")
        return 0

    build_env(non_interactive=args.non_interactive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
