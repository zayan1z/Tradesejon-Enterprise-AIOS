# 01. System Architecture

```mermaid
flowchart TD
    User([Traders / Prospects / Executive]) --> TG[Telegram Bots: CEO, PR, Trader]
    User --> DC[Discord Tri-Bots: VIP, PR Desk, Admin]
    User --> Web[Web AIOS Command Center]
    Payment[Whop Payment Gateway] -->|HMAC-SHA256 Webhook| N8N

    subgraph Containerized VPS Stack
        TG --> N8N[n8n Workflow Engine + Custom Drivers]
        DC --> N8N
        Web --> N8N
        N8N --> RAG[White-Label 17-FAQ RAG Retriever]
        N8N --> CRM[(MongoDB 7 CRM & Audit Store)]
        N8N --> LLM[Groq Whisper / Llama-3 / ElevenLabs Voice]
        N8N --> Guard{100% Verified Delivery Guard}
    end
```
