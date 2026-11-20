"""Integration & resilience tests for 1-click env aliases, custom KB sync, and unpublished-workflow failover."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from aiohttp import web

from tradesejon_discord_bots.config import load_config
from tradesejon_discord_bots.crm_sync import OmnichannelSyncEngine
from tradesejon_discord_bots.shared_services import SharedServices


def test_load_config_supports_simple_env_aliases(tmp_path: Path, monkeypatch):
    for key in (
        "DISCORD_TRADER_BOT_TOKEN",
        "DISCORD_PR_BOT_TOKEN",
        "DISCORD_CEO_BOT_TOKEN",
        "DISCORD_BRIDGE_SECRET",
        "TELEGRAM_CEO_TOKEN",
        "TELEGRAM_TRADING_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=shared_discord_tok_123456",
                "TRADESAJON_BRIDGE_SECRET=shared_bridge_sec_123456",
                "TELEGRAM_CEO_BOT_TOKEN=tg_ceo_alias_tok_123456",
                "TELEGRAM_TRADER_BOT_TOKEN=tg_trd_alias_tok_123456",
                "CEO_USER_IDS=111222333",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(env_file)
    assert cfg.ceo_token == "shared_discord_tok_123456"
    assert cfg.pr_token == "shared_discord_tok_123456"
    assert cfg.trader_token == "shared_discord_tok_123456"
    assert cfg.bridge_secret == "shared_bridge_sec_123456"
    assert cfg.telegram_ceo_token == "tg_ceo_alias_tok_123456"
    assert cfg.telegram_trading_token == "tg_trd_alias_tok_123456"
    assert "111222333" in cfg.ceo_user_ids


def test_shared_services_loads_custom_synced_knowledge(tmp_path: Path):
    kb_dir = tmp_path / "rag-knowledge-base"
    kb_dir.mkdir(parents=True, exist_ok=True)
    (kb_dir / "custom_knowledge.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "slug": "vip_policy_1",
                        "title": "VIP Schedule Update",
                        "content": "Live mentorship opens at 21:30 Cairo time with full archive access.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(f"TRADESEJON_KNOWLEDGE_BASE_DIR={kb_dir}\n", encoding="utf-8")
    cfg = load_config(env_file)
    svc = SharedServices(cfg)
    prompt = svc.get_live_master_kb_prompt()
    assert "VIP Schedule Update" in prompt
    assert "21:30 Cairo time" in prompt


def test_funnel_sync_falls_back_to_unified_gateway_when_wf21_unpublished(tmp_path: Path):
    async def _run():
        captured_routes: list[tuple[str, dict]] = []

        async def handle_wf21(request: web.Request) -> web.Response:
            return web.json_response({"error": "webhook not registered"}, status=404)

        async def handle_wf00(request: web.Request) -> web.Response:
            data = await request.json()
            captured_routes.append((request.path, data))
            return web.json_response({"ok": True, "via": "wf00_fallback"})

        async def handle_aios_sync(request: web.Request) -> web.Response:
            return web.json_response({"ok": True, "synced": True})

        app = web.Application()
        app.router.add_post("/webhook/tradesejon-funnel-sync", handle_wf21)
        app.router.add_post("/webhook/tradesejon-unified", handle_wf00)
        app.router.add_post("/api/funnel-sync", handle_aios_sync)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            env_file = tmp_path / ".env"
            env_file.write_text(
                f"N8N_BASE_URL=http://127.0.0.1:{port}\nAIOS_BASE_URL=http://127.0.0.1:{port}\n",
                encoding="utf-8",
            )
            cfg = load_config(env_file)
            crm = OmnichannelSyncEngine(cfg)
            res = await crm.notify_n8n_funnel_sync({"email": "client@example.com", "status": "vip_recurring"})
            assert res["ok"] is True
            assert res["targets"]["n8n_local"]["status"] == 404
            assert res["targets"]["n8n_unified_fallback"]["ok"] is True
            assert len(captured_routes) == 1
            assert captured_routes[0][1]["route"] == "tradesejon-funnel-sync"
        finally:
            await runner.cleanup()

    asyncio.run(_run())
