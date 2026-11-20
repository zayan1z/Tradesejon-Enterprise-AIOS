@echo off
title White-Label Enterprise AIOS - 1-Click Cloud Launcher
echo ===================================================================
echo    White-Label Enterprise AIOS - One-Click Setup ^& Launch
echo ===================================================================
if not exist .env (
  python scripts\setup_wizard.py
)
docker compose up -d --build
echo.
echo [+] All 5 containers launching! Open n8n (http://localhost:5678) & AIOS (http://localhost:3000).
pause
