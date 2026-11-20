#!/usr/bin/env python3
"""Interactive White-Label Credential Setup Wizard generating clean .env with secure random keys."""
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
example = ROOT / ".env.example"
env_path = ROOT / ".env"

if env_path.exists():
    print(".env already exists. Delete it or edit directly to reconfigure.")
else:
    lines = example.read_text(encoding="utf-8").splitlines()
    out = []
    prompts = {
        "BRAND_NAME": "Your White-Label Brand Name [Tradesejon]: ",
        "TELEGRAM_CEO_BOT_TOKEN": "Telegram CEO Bot Token (optional now, press Enter to skip): ",
        "DISCORD_BOT_TOKEN": "Discord Bot Token (optional now, press Enter to skip): ",
        "GROQ_API_KEY": "Groq LLM API Key (optional now, press Enter to skip): ",
    }
    print("\n--- White-Label Credential & Brand Configuration ---")
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            if k in ("N8N_ENCRYPTION_KEY", "TRADESAJON_BRIDGE_SECRET", "WORKSHOP_LAUNCH_TOKEN"):
                out.append(f"{k}={secrets.token_hex(16)}")
                continue
            if k in prompts:
                val = input(prompts[k]).strip()
                out.append(f"{k}={val if val else v}")
                continue
        out.append(line)
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n[OK] Generated production .env successfully with auto-generated cryptographic secrets!")
