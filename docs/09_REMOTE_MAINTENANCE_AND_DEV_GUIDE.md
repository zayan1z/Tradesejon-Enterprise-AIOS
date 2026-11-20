# 09. Remote Maintenance & Development

Push updates to GitHub and apply on the client VPS with `git pull origin main && docker compose up -d --build` without touching `.env` or database volumes.