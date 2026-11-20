# 02. One-Click VPS Deployment Guide

## Single-Command Deployment
```bash
git clone <REPOSITORY_URL>
cd tradesejon-enterprise-aios
chmod +x 1_CLICK_DEPLOY.sh
./1_CLICK_DEPLOY.sh
```
The bundled `docker/n8n.Dockerfile` pre-installs `mongodb`, `nodemailer`, and `axios` globally and runs `docker/n8n-entrypoint.sh` on boot to automatically import and activate all 20 workflows inside n8n with zero manual JSON uploads required.
