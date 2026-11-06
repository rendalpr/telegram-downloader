import asyncio, logging, os, re, shutil, subprocess, tempfile
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse
import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
YOUTUBE_PROXY = os.getenv('YOUTUBE_PROXY')
MAX_SIZE = 45 * 1024 * 1024
MAX_URLS_PER_MESSAGE = 10
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)
MAX_PHOTO_SIZE = 9 * 1024 * 1024
WELCOME_IMAGE = Path(__file__).resolve().parent / "assets" / "welcome.png"
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None

if not FFMPEG_AVAILABLE:
    log.warning("FFmpeg is not installed; downloads will use the best single-file format available.")

WELCOME_TEXT = (
    "👋 Привет! Я - «Дай Скачать by @day_zvuk» 🎬\n\n"
    "🔗 Отправь публичную ссылку, и я попробую скачать видео, фото или аудио "
    "в максимальном доступном качестве.\n\n"
    "🌐 <b>Платформы, ссылки которых я пробую скачать:</b>\n"
    "YouTube, YouTube Music, TikTok, Instagram, Pinterest, Facebook, X, Threads, "
    "VK, Reddit, Twitch, Vimeo, OK, Tumblr, Dailymotion, Likee и SoundCloud.\n\n"
    "ℹ️ Результат зависит от конкретной ссылки и ограничений сайта. "
    "Закрытые публикации и истории могут быть недоступны.\n"
    "🎵 Spotify и Apple Music распознаются, но аудиофайлы бот не скачивает.\n\n"
    "📦 Максимальный размер файла - 45 МБ.\n\n"
    "⚖️ Скачивай только материалы, на которые у тебя есть права."
)

WELCOME_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton(
        "Подписаться на канал",
        url="https://t.me/day_zvuk",
        style="primary",
    )],
    [InlineKeyboardButton(
        "Реклама треков",
        url="https://t.me/m/NEKbyJ1oNWM6",
        style="success",
    )],
])

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
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

def host_matches(host, domains):
    return any(host == domain or host.endswith('.' + domain) for domain in domains)

def get_host(url):
    try:
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
URL_TRAILING_PUNCTUATION = ".,!?;:)]}>»”’"

def get_urls(text):
    urls = []
    for candidate in URL_PATTERN.findall(text):
        url = candidate.rstrip(URL_TRAILING_PUNCTUATION)
        if host_matches(get_host(url), HOSTS) and url not in urls:
            urls.append(url)
    return urls

def get_video_dimensions(path):
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None, None
    try:
        result = subprocess.run(
            [
                ffprobe, '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'csv=s=x:p=0', str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        width, height = (int(value) for value in result.stdout.strip().split('x', 1))
        if width > 0 and height > 0:
            return width, height
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.warning("Could not read video dimensions: %s", exc)
    return None, None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    try:
        with WELCOME_IMAGE.open('rb') as photo:
            await message.reply_photo(photo=photo, caption=WELCOME_TEXT, parse_mode="HTML", reply_markup=WELCOME_KEYBOARD)
    except OSError:
        log.warning("Welcome image is missing; sending the text greeting only.")
        await message.reply_text(WELCOME_TEXT, parse_mode="HTML", reply_markup=WELCOME_KEYBOARD)

def download(url, folder):
    host = get_host(url)
    opts = {
        'outtmpl': str(Path(folder) / '%(title).70s-%(id)s.%(ext)s'),
        'format': 'bestvideo*+bestaudio/best/bestaudio' if FFMPEG_AVAILABLE else 'best/bestaudio',
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
        if YOUTUBE_PROXY:
            opts['proxy'] = YOUTUBE_PROXY
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

async def process_url(msg, context, user, url, position, total):
    label = f"{position}/{total}: " if total > 1 else ""
    if host_matches(get_host(url), PROTECTED_MUSIC_HOSTS):
        await msg.reply_text(
            f"{label}Spotify и Apple Music не предоставляют боту скачиваемый аудиофайл. "
            "Нужен публичный источник, который разрешает скачивание."
        )
        return

    status = await msg.reply_text(f"⏳ Загружаю {position}/{total}…" if total > 1 else "⏳ Загружаю…")
    try:
        async with DOWNLOAD_SEMAPHORE:
            await status.edit_text(f"⏳ Скачиваю {position}/{total}…" if total > 1 else "⏳ Скачиваю…")
            await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_DOCUMENT)
            with tempfile.TemporaryDirectory() as folder:
                path = await asyncio.to_thread(download, url, folder)
                with path.open('rb') as media:
                    suffix = path.suffix.lower()
                    if suffix == '.mp4':
                        width, height = await asyncio.to_thread(get_video_dimensions, path)
                        await msg.reply_video(
                            video=media,
                            width=width,
                            height=height,
                            supports_streaming=True,
                            caption='Готово.',
                        )
                    elif suffix in AUDIO_EXTENSIONS:
                        await msg.reply_audio(audio=media, caption='Готово.')
                    elif suffix in IMAGE_EXTENSIONS and path.stat().st_size <= MAX_PHOTO_SIZE:
                        await msg.reply_photo(photo=media, caption='Готово.')
                    else:
                        await msg.reply_document(document=media, caption='Готово.')
        try:
            await status.delete()
        except Exception as exc:
            log.debug("Could not remove completed status message: %s", exc)
    except Exception as exc:
        error = str(exc)
        log.warning('Download failed for %s: %s', user.id, error)
        if host_matches(get_host(url), YOUTUBE_HOSTS) and 'sign in to confirm' in error.lower():
            error_message = (
                'YouTube сейчас требует подтверждение для запросов с IP хостинга. '
                'Я не запрашиваю пароли или cookies; попробуй эту ссылку позже.'
            )
        else:
            error_message = (
                'Не удалось скачать: платформа могла ограничить доступ, '
                'ссылка приватная или файл слишком большим.'
            )
        try:
            await status.edit_text(f"{label}{error_message}")
        except Exception as status_exc:
            log.warning("Could not update download status for %s: %s", user.id, status_exc)
            try:
                await msg.reply_text(f"{label}{error_message}")
            except Exception as reply_exc:
                log.warning("Could not report download failure for %s: %s", user.id, reply_exc)

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user or not msg.text:
        return

    urls = get_urls(msg.text)
    if not urls:
        await msg.reply_text('Пришли публичную ссылку с поддерживаемой платформы.')
        return
    if len(urls) > MAX_URLS_PER_MESSAGE:
        await msg.reply_text(
            f'В одном сообщении можно отправить до {MAX_URLS_PER_MESSAGE} ссылок. '
            f'Я нашёл {len(urls)}; пришли их несколькими сообщениями.'
        )
        return

    if len(urls) > 1:
        await msg.reply_text(
            f'🔗 Нашёл {len(urls)} ссылок. Обрабатываю по очереди и пришлю результат для каждой.'
        )

    for position, url in enumerate(urls, start=1):
        await process_url(msg, context, user, url, position, len(urls))

def main():
    if not TOKEN: raise RuntimeError('Set TELEGRAM_BOT_TOKEN environment variable')
    app = Application.builder().token(TOKEN).concurrent_updates(4).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
