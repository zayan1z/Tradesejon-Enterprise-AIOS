<#
.SYNOPSIS
  White-Label Enterprise AIOS — 1-Click Beginner PowerShell Installer
  Guides any beginner step-by-step to enter credentials cleanly (zero data hardcoded),
  generates cryptographic secrets, launches all 5 Docker services, and opens the dashboard.
#>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "=========================================================================" -ForegroundColor Cyan
Write-Host "   TRADESAJON ENTERPRISE AIOS — 1-CLICK CLIENT SETUP & LAUNCHER" -ForegroundColor Yellow
Write-Host "   Zero Hardcoded Data • White-Label Ready • Cloud Autonomous" -ForegroundColor Gray
Write-Host "=========================================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path "rag-knowledge-base\uploads")) {
    New-Item -ItemType Directory -Path "rag-knowledge-base\uploads" -Force | Out-Null
}

# Step 1: Check Docker
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Docker Desktop is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this script again." -ForegroundColor Yellow
    exit 1
}

# Step 2: Interactive Beginner-Friendly Credential Builder (if .env missing or user wants reconfigure)
$createEnv = $true
if (Test-Path ".env") {
    $ans = Read-Host ".env already exists. Keep existing credentials? [Y/n]"
    if ($ans -notmatch "^[nN]") { $createEnv = $false }
}

if ($createEnv) {
    Write-Host ""
    Write-Host "--- Step 1 of 3: Easy Credential Setup (Press Enter to skip any optional item) ---" -ForegroundColor Green

    function Ask-Val([string]$Prompt, [string]$DefaultVal = "") {
        $v = Read-Host $Prompt
        if ([string]::IsNullOrWhiteSpace($v)) { return $DefaultVal }
        return $v.Trim()
    }

    function New-HexSecret {
        $bytes = New-Object byte[] 16
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLower()
    }

    $brand       = Ask-Val "1. Your Business / Brand Name [YourBrandAIOS]" "YourBrandAIOS"
    $mentors     = Ask-Val "2. Mentor Names (comma-separated) [Lead Strategist,Mentor Alpha,Mentor Beta]" "Lead Strategist,Mentor Alpha,Mentor Beta"
    $webhookUrl  = Ask-Val "3. Public HTTPS n8n URL (for Telegram bots, e.g. https://n8n.yourdomain.com/ or Enter for localhost)" "http://localhost:5678/"
    $groqKey     = Ask-Val "4. Groq AI API Key (get from console.groq.com)" ""
    $tgCeo       = Ask-Val "5. Telegram CEO Bot Token (from @BotFather)" ""
    $tgPr        = Ask-Val "6. Telegram PR Bot Token (from @BotFather)" ""
    $tgTrader    = Ask-Val "7. Telegram Trader Bot Token (from @BotFather)" ""
    $ceoIds      = Ask-Val "8. Your Numeric Telegram User ID (from @userinfobot)" ""
    $dcToken     = Ask-Val "9. Discord Bot Token (from discord.com/developers)" ""
    $dcGuild     = Ask-Val "10. Discord Server (Guild) ID" ""

    $encKey      = New-HexSecret
    $bridgeSec   = New-HexSecret
    $launchTok   = New-HexSecret
    $aiosTok     = New-HexSecret
    $firstCeoId  = ($ceoIds -split ",")[0].Trim()

    $template = Get-Content ".env.example" -Raw
    $map = @{
        "BRAND_NAME"                = $brand
        "MENTORS_LIST"              = $mentors
        "WEBHOOK_URL"               = $webhookUrl
        "GROQ_API_KEY"              = $groqKey
        "TELEGRAM_CEO_BOT_TOKEN"    = $tgCeo
        "TELEGRAM_PR_BOT_TOKEN"     = $tgPr
        "TELEGRAM_TRADER_BOT_TOKEN" = $tgTrader
        "CEO_USER_IDS"              = $ceoIds
        "CEO_CHAT_ID"               = $firstCeoId
        "DISCORD_BOT_TOKEN"         = $dcToken
        "DISCORD_GUILD_ID"          = $dcGuild
        "N8N_ENCRYPTION_KEY"        = $encKey
        "TRADESAJON_BRIDGE_SECRET"  = $bridgeSec
        "WORKSHOP_LAUNCH_TOKEN"     = $launchTok
        "AIOS_INTERNAL_TOKEN"       = $aiosTok
    }

    $lines = $template -split "`r?`n"
    $outLines = foreach ($line in $lines) {
        if ($line -match "^([A-Z0-9_]+)=(.*)$") {
            $k = $Matches[1]
            if ($map.ContainsKey($k)) {
                "$k=$($map[$k])"
                continue
            }
        }
        $line
    }
    ($outLines -join "`n") + "`n" | Set-Content -Path ".env" -Encoding UTF8
    Write-Host "[OK] Created production .env with auto-generated cryptographic security keys!" -ForegroundColor Green
}

Write-Host ""
Write-Host "--- Step 2 of 3: Launching Full Cloud Stack (MongoDB, Redis, n8n, AIOS, Discord Bots) ---" -ForegroundColor Green
docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker Compose failed. Make sure Docker Desktop is running." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "--- Step 3 of 3: Verifying Live Services ---" -ForegroundColor Green
Start-Sleep -Seconds 8
if (Get-Command python -ErrorAction SilentlyContinue) {
    python scripts\client_health_check.py
}

Write-Host ""
Write-Host "=========================================================================" -ForegroundColor Cyan
Write-Host "  DEPLOYMENT COMPLETE!" -ForegroundColor Green
Write-Host "  1) Open n8n Studio    : http://localhost:5678  -> Open Workflow #20 -> Click Execute" -ForegroundColor White
Write-Host "  2) Open AIOS Command  : http://localhost:3000" -ForegroundColor White
Write-Host "  3) Upload Docs / Text : http://localhost:3000/knowledge.html" -ForegroundColor Yellow
Write-Host "  4) Live CRM Explorer  : http://localhost:3000/db" -ForegroundColor White
Write-Host "=========================================================================" -ForegroundColor Cyan
