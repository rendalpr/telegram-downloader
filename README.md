# Telegram Downloader

Python Telegram bot using `python-telegram-bot` and `yt-dlp`.

Supported public sources include YouTube, TikTok, Instagram, Pinterest, Facebook, X/Twitter and Threads. Support varies by platform and can change. Private or login-gated posts are not supported.

## Deploy a free, continuously running worker

[![Deploy to Silly Development](https://sillydev.co.uk/deploy/button.svg)](https://sillydev.co.uk/deploy?repository=https://github.com/rendalpr/telegram-downloader&runtime=python)

1. Open the deploy button, sign in or create an account, choose a free Python server, and confirm deployment of this public repository.
2. In the server's Startup settings, set `TELEGRAM_BOT_TOKEN` to the token from BotFather. The repository config selects Python 3.12 and `main.py`. Keep the token out of GitHub and chat.
3. Start or restart the server, then open the bot in Telegram and send `/start`.

The provider currently advertises 25% of one CPU core, 256 MB RAM and 512 MB disk for free, with no card or monthly runtime cap. It says free servers stay running, but its terms provide no uptime guarantee and allow free service to be suspended or withdrawn. Treat this as best-effort hosting, not guaranteed 24/7 availability. Deploy code changes manually from the provider panel; automatic deployment on every push requires connecting a GitHub account.

## Run locally (Windows PowerShell)

1. Install Python 3.10 or newer.
2. Run:

   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

3. Put your BotFather token in `.env` as `TELEGRAM_BOT_TOKEN=...` and run `python main.py`. `.env` is ignored by Git.

## Limits and responsible use

- Maximum download size: 45 MB.
- Per-user cooldown: 15 seconds.
- Only allowlisted public URLs are accepted.
- Download only content you own or have permission to save. Respect copyright and platform terms.
- The bot does not bypass private-account controls, DRM, paywalls or other access restrictions.
