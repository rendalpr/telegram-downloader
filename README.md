# Telegram Downloader

Python Telegram bot using python-telegram-bot and yt-dlp.

Supported public sources include YouTube, TikTok, Instagram, Pinterest, Facebook, X/Twitter and Threads. Support varies by platform and can change. Private or login-gated posts are not supported.

## Run locally (Windows PowerShell)
1. Install Python 3.10 or newer.
2. Run:
   py -m venv .venv
   .venv\\Scripts\\Activate.ps1
   pip install -r requirements.txt
3. Set the token in your terminal (never commit it):
   $env:TELEGRAM_BOT_TOKEN='YOUR_BOT_TOKEN'
4. Run: python main.py

## Hosting
This repository contains the application, not an always-on host. GitHub Actions is not reliable continuous 24/7 hosting; scheduled workflows are not a permanent process. A persistent free worker is required for guaranteed continuous polling, and provider free plans can change.

## Limits and responsible use
- Maximum download size: 45 MB.
- Per-user cooldown: 15 seconds.
- Only allowlisted public URLs are accepted.
- Download only content you own or have permission to save. Respect copyright and platform terms.
- The bot does not bypass private-account controls, DRM, paywalls or other access restrictions.