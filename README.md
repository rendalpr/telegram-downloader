# Telegram Downloader

Python Telegram bot using `python-telegram-bot` and `yt-dlp`.

Supported sources include publicly accessible YouTube videos and YouTube Music, TikTok, Instagram, Pinterest, Facebook, X/Twitter, Threads, VK, Reddit, Twitch clips, Vimeo, OK, Tumblr, Dailymotion, Likee and SoundCloud. Support varies by site and can change. Instagram Stories and other login-gated content may require account access and may not work. Spotify and Apple Music links are recognized but their protected streams are not downloadable by this bot.

## Preferred host: Silly Development

[![Deploy to Silly Development](https://sillydev.co.uk/deploy/button.svg)](https://sillydev.co.uk/deploy?repository=https://github.com/rendalpr/telegram-downloader&runtime=python)

Choose a free Python server, set `TELEGRAM_BOT_TOKEN` in its Startup settings, then start it. The repository config selects Python 3.12 and `main.py`. Keep the token out of GitHub and chat.

Silly Development advertises 25% of one CPU core, 256 MB RAM and 512 MB disk with no card or monthly runtime cap. Its terms do not guarantee uptime and allow free service to be suspended or withdrawn. Treat it as best-effort hosting, not guaranteed 24/7 availability. If the deploy page says free servers are full, use the alternative below.

## Alternative when Silly Development is full: Bot-Hosting.net

Bot-Hosting.net's free plan currently has one Python deployment with 25% CPU, 256 MB RAM and 512 MB storage, with no card required. It requires a manual renewal every 4 days; its terms provide no uptime commitment, and a missed renewal suspends the deployment. This is not guaranteed 24/7 hosting.

1. Sign in or create an account with GitHub or Discord.
2. In the panel, create a new **Application** deployment, choose **GitHub** as the source, enter `rendalpr/telegram-downloader`, and select **Python**.
3. In **Startup**, set the entry file to `main.py`. In **Environment**, add `TELEGRAM_BOT_TOKEN` using the value from BotFather. Start the deployment, then send `/start` to the bot.
4. Renew the free plan from the dashboard every 4 days to keep it running.

See the provider's [deployment guide](https://bot-hosting.net/docs/guides/create-a-deployment), [Python setup guide](https://bot-hosting.net/docs/guides/set-up-a-deployment), and [free-plan terms](https://bot-hosting.net/legal).

## Run locally (Windows PowerShell)

1. Install Python 3.11 or newer.
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
- YouTube may reject requests from the hosting provider's IP and require verification.
- Download only content you own or have permission to save. Respect copyright and platform terms.
- The bot does not bypass private-account controls, DRM, paywalls or other access restrictions.
