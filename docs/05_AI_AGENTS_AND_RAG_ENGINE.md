# 05. AI Agents, Advanced RAG Engine & Multi-Agent Architecture

Tradesejon Enterprise AIOS implements an autonomous multi-tier AI agent ecosystem inspired by state-of-the-art patterns (Corrective RAG, Multi-Agent Trust Layers, MCP Bridge, and Always-On Autonomous Schedulers).

---

## 1. Multi-Agent Ecosystem Roles

```
               ┌────────────────────────────────────────────────────────┐
               │         CEO Autonomous Executive Agent                 │
               │   (Strategic Decisions, Omnichannel Dispatch, CRM)    │
               └───────────────┬────────────────────────┬───────────────┘
                               │                        │
       ┌───────────────────────┴────────┐      ┌────────┴──────────────────────┐
       │   PR & Client Relations Desk   │      │   Market & Lead Intelligence  │
       │  (Public Inquiries, Onboarding)│      │  (Lead Qualification & Intake)│
       └────────────────────────────────┘      └───────────────────────────────┘
```

1. **CEO Executive Agent (`ask_ceo_agent`)**:
   - High-privilege orchestration engine.
   - Evaluates inquiries, coordinates sub-actions, authorizes secure targeted message dispatch to Telegram users and Discord channels, queries CRM database records, and updates persistent knowledge.
2. **PR & Community Desk Agent**:
   - Handles public client inquiries, provides verified brand information, walks prospects through onboarding funnels, and schedules live sessions.
3. **Market & Lead Qualification Agent**:
   - Interacts with leads, validates email/contact information, issues verification tokens, and updates pipeline stages across MongoDB and n8n workflows.

---

## 2. Advanced RAG Architecture (Corrective & Hybrid RAG)

The knowledge retrieval layer is designed to eliminate hallucinations and maintain continuous synchronization:

1. **Self-Corrective Verification Loop**:
   - Queries are evaluated against local indexed vector/text chunks (`rag-knowledge-base/` and MongoDB `kb_articles`).
   - If confidence falls below the acceptance threshold, the query triggers an automated web-search or knowledge expansion step before responding.
2. **Deterministic Fallback Layer**:
   - High-stakes queries (mentors list, pricing, access tiers, refund policies) are bound to explicit environment variables (`BRAND_NAME`, `MENTORS_LIST`, `SUPPORTED_PLATFORMS`, `COMMERCIAL_OFFERING_NOTE`).
   - Ground truth values override probabilistic model outputs, guaranteeing 100% brand accuracy.
3. **Universal Document Intake**:
   - Supports live upload and indexing of `.txt`, `.md`, `.json`, `.csv`, `.docx`, `.xlsx`, and `.pdf` files via the Knowledge & Data Sync Studio (`/knowledge.html`).

---

## 3. Multi-Agent Trust & Verification Layer

To prevent rogue agent actions or data exfiltration:

- **Role-Based Execution Guardrails (RBAC)**: Destructive actions (database purging, broadcasting, updating administrative credentials) require explicit CEO admin privilege and confirmation.
- **Targeted vs. Broadcast Routing Firewall**: Strict regex validation prevents broadcast messages from being accidentally sent when a specific user handle or contact ID (`t.me/contact/...` or `@user`) is targeted.
- **Zero-Secret Memory Scrubbing**: Outgoing responses and logs are filtered to prevent internal API tokens, passwords, or customer private data from appearing in client-facing chats.

---

## 4. MCP (Model Context Protocol) & Tool Integration

The agent architecture exposes standard MCP endpoints, enabling external client agents and IDE assistants to:
- Read omnichannel CRM contacts and audit logs.
- Trigger n8n workflow tasks (`00` through `21`).
- Query semantic knowledge chunks with score ranking.

---

## 5. Always-On Autonomous Background Tasks

Built-in continuous daemons operate 24/7 without user intervention:
- **Lead Sync Daemon**: Periodic synchronization between Discord interactions, Telegram bot conversations, and MongoDB.
- **Health & Heartbeat Monitor**: Pings internal microservices (`n8n:5678`, `aios:3000`, `mongodb:27017`) and automatically logs recovery diagnostics upon connection recovery.
