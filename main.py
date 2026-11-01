import asyncio, logging, os, re, tempfile, time
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse
import yt_dlp
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MAX_SIZE = 45 * 1024 * 1024
COOLDOWN = 15
last_request = {}

HOSTS = (
    'youtube.com', 'youtu.be', 'youtube-nocookie.com',
    'tiktok.com',
    'instagram.com', 'instagr.am',
    'pinterest.com', 'pinterest.co.uk', 'pinterest.ca', 'pinterest.de',
    'pinterest.fr', 'pinterest.it', 'pinterest.jp', 'pinterest.es',
    'pinterest.com.au', 'pinterest.ru', 'pin.it',
    'facebook.com', 'fb.watch',
    'x.com', 'twitter.com', 't.co', 'threads.net',
    'vk.com', 'vk.ru', 'vkvideo.ru',
    'reddit.com', 'redd.it',
    'twitch.tv', 'vimeo.com', 'ok.ru', 'tumblr.com',
    'dailymotion.com', 'dai.ly',
    'likee.video', 'likee.com',
    'soundcloud.com', 'on.soundcloud.com',
    'spotify.com', 'spotify.link', 'spoti.fi',
    'music.apple.com', 'itunes.apple.com',
)
YOUTUBE_HOSTS = ('youtube.com', 'youtu.be', 'youtube-nocookie.com')
TIKTOK_HOSTS = ('tiktok.com',)
PROTECTED_MUSIC_HOSTS = ('spotify.com', 'spotify.link', 'spoti.fi', 'music.apple.com', 'itunes.apple.com')
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm'}
AUDIO_EXTENSIONS = {'.mp3', '.m4a'}

def host_matches(host, domains):
    return any(host == domain or host.endswith('.' + domain) for domain in domains)

def get_host(url):
    try:
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''

def get_url(text):
    m = re.search(r'https?://\S+', text)
    if not m: return None
    url = m.group(0).rstrip('.,!?)')
    return url if host_matches(get_host(url), HOSTS) else None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        'Пришли публичную ссылку на видео, фото или аудио. Поддерживаются YouTube, TikTok, Instagram, Pinterest, '
        'Facebook, X, Threads, VK, Reddit, Twitch, Vimeo, OK, Tumblr, Dailymotion, Likee и SoundCloud. '
        'Доступ к Stories и закрытым публикациям может требовать входа. Spotify и Apple Music не отдают боту '
        'скачиваемые аудиофайлы. Скачивай только материалы, на которые у тебя есть права.'
    )

def download(url, folder):
    host = get_host(url)
    opts = {
        'outtmpl': str(Path(folder) / '%(title).70s-%(id)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'max_filesize': MAX_SIZE,
        'quiet': True,
        'no_warnings': True,
        'retries': 2,
        'socket_timeout': 30,
    }
    if host_matches(host, TIKTOK_HOSTS):
        opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
        }
    if host_matches(host, YOUTUBE_HOSTS):
        opts['force_ipv4'] = True
        deno_candidates = (
            Path.home() / '.local' / 'bin' / 'deno',
            Path.home() / '.deno' / 'bin' / 'deno',
        )
        deno_path = next((path for path in deno_candidates if path.is_file()), None)
        if deno_path:
            opts['js_runtimes'] = {'deno': {'path': str(deno_path)}}
        if host == 'music.youtube.com':
            opts['format'] = 'bestaudio/best'
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        if not path.exists():
            files = list(Path(folder).glob('*'))
            if files: path = files[0]
        if not path.exists() or path.stat().st_size > MAX_SIZE:
            raise ValueError('File too large or missing')
        return path

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user or not msg.text: return
    now = time.monotonic()
    if now - last_request.get(user.id, 0) < COOLDOWN:
        await msg.reply_text('Подожди 15 секунд перед следующей загрузкой.'); return
    last_request[user.id] = now
    url = get_url(msg.text)
    if not url:
        await msg.reply_text('Пришли публичную ссылку с поддерживаемой платформы.'); return
    if host_matches(get_host(url), PROTECTED_MUSIC_HOSTS):
        await msg.reply_text('Spotify и Apple Music не предоставляют боту скачиваемый аудиофайл. Нужен публичный источник, который разрешает скачивание.')
        return
    status = await msg.reply_text('⏳ Загружаю…')
    try:
        await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_DOCUMENT)
        with tempfile.TemporaryDirectory() as folder:
            path = await asyncio.to_thread(download, url, folder)
            with path.open('rb') as media:
                suffix = path.suffix.lower()
                if suffix in VIDEO_EXTENSIONS:
                    await msg.reply_video(video=media, caption='Готово.')
                elif suffix in AUDIO_EXTENSIONS:
                    await msg.reply_audio(audio=media, caption='Готово.')
                else:
                    await msg.reply_document(document=media, caption='Готово.')
        await status.delete()
    except Exception as exc:
        error = str(exc)
        log.warning('Download failed for %s: %s', user.id, error)
        if host_matches(get_host(url), YOUTUBE_HOSTS) and 'sign in to confirm' in error.lower():
            await status.edit_text('YouTube сейчас требует подтверждение для запросов с IP хостинга. Я не запрашиваю пароли или cookies; попробуй эту ссылку позже.')
        else:
            await status.edit_text('Не удалось скачать: платформа могла ограничить доступ, ссылка приватная или файл слишком большим.')

def main():
    if not TOKEN: raise RuntimeError('Set TELEGRAM_BOT_TOKEN environment variable')
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
