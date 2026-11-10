import asyncio, html, json, logging, os, re, shutil, sqlite3, subprocess, tempfile, urllib.request
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

TOPMUSIC_STOREFRONT = os.getenv("TOPMUSIC_STOREFRONT", "ru").strip().lower()
if not re.fullmatch(r"[a-z]{2}", TOPMUSIC_STOREFRONT):
    TOPMUSIC_STOREFRONT = "ru"
TOPMUSIC_FEED_URL = (
    f"https://rss.applemarketingtools.com/api/v2/"
    f"{TOPMUSIC_STOREFRONT}/music/most-played/100/songs.json"
)
TOPMUSIC_DB = Path(__file__).resolve().parent / "topmusic_seen.sqlite3"

if not FFMPEG_AVAILABLE:
    log.warning("FFmpeg is not installed; downloads will use the best single-file format available.")

WELCOME_TEXT = (
    "👋 Привет! Я - «Дай Скачать by @day_zvuk» 🎬\n\n"
    "🔗 Отправь публичную ссылку, и я попробую скачать видео, фото или аудио "
    "в максимальном доступном качестве.\n"
    "🎵 Для видео пришлю оригинал и отдельный MP3.\n\n"
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


def extract_mp3(video_path):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('FFmpeg is not installed')

    mp3_path = Path(video_path).with_suffix('.mp3')
    command = [
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-i', str(video_path),
        '-map', '0:a:0',
        '-vn',
        '-codec:a', 'libmp3lame',
        '-q:a', '2',
        '-id3v2_version', '3',
        str(mp3_path),
    ]
    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=600,
        )
    except subprocess.CalledProcessError as exc:
        mp3_path.unlink(missing_ok=True)
        if 'matches no streams' in (exc.stderr or '').lower():
            raise ValueError('Video has no audio track') from exc
        log.warning("FFmpeg MP3 extraction failed: %s", (exc.stderr or '')[-1000:])
        raise RuntimeError('MP3 extraction failed') from exc
    except (OSError, subprocess.SubprocessError):
        mp3_path.unlink(missing_ok=True)
        raise

    if not mp3_path.is_file() or mp3_path.stat().st_size == 0:
        mp3_path.unlink(missing_ok=True)
        raise RuntimeError('MP3 output is empty')
    if mp3_path.stat().st_size > MAX_SIZE:
        mp3_path.unlink(missing_ok=True)
        raise ValueError('MP3 exceeds the 45 MiB file limit')
    return mp3_path

async def process_url(msg, context, user, url, position, total):
    label = f"{position}/{total}: " if total > 1 else ""
    if host_matches(get_host(url), PROTECTED_MUSIC_HOSTS):
        await msg.reply_text(
            f"{label}Spotify и Apple Music не предоставляют боту скачиваемый аудиофайл. "
            "Нужен публичный источник, который разрешает скачивание."
        )
        return

    status = await msg.reply_text(f"⏳ Загружаю {position}/{total}…" if total > 1 else "⏳ Загружаю…")
    mp3_error = None
    try:
        async with DOWNLOAD_SEMAPHORE:
            await status.edit_text(f"⏳ Скачиваю {position}/{total}…" if total > 1 else "⏳ Скачиваю…")
            await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_DOCUMENT)
            with tempfile.TemporaryDirectory() as folder:
                path = await asyncio.to_thread(download, url, folder)
                suffix = path.suffix.lower()
                mp3_path = None
                if suffix in VIDEO_EXTENSIONS:
                    await status.edit_text(
                        f"🎵 Извлекаю MP3 {position}/{total}…" if total > 1 else "🎵 Извлекаю MP3…"
                    )
                    try:
                        mp3_path = await asyncio.to_thread(extract_mp3, path)
                    except Exception as exc:
                        log.warning("MP3 extraction failed for %s: %s", user.id, exc)
                        if isinstance(exc, ValueError) and 'no audio track' in str(exc).lower():
                            mp3_error = 'в видео нет аудиодорожки.'
                        elif isinstance(exc, ValueError) and '45 mib' in str(exc).lower():
                            mp3_error = 'MP3 превышает лимит 45 МиБ.'
                        elif isinstance(exc, RuntimeError) and 'ffmpeg' in str(exc).lower():
                            mp3_error = 'на сервере недоступен FFmpeg.'
                        else:
                            mp3_error = 'не удалось извлечь аудиодорожку.'
                caption = f"Готово. {position}/{total}" if total > 1 else "Готово."
                with path.open('rb') as media:
                    if suffix == '.mp4':
                        width, height = await asyncio.to_thread(get_video_dimensions, path)
                        await msg.reply_video(
                            video=media,
                            width=width,
                            height=height,
                            supports_streaming=True,
                            caption=caption,
                        )
                    elif suffix in AUDIO_EXTENSIONS:
                        await msg.reply_audio(audio=media, caption=caption)
                    elif suffix in IMAGE_EXTENSIONS and path.stat().st_size <= MAX_PHOTO_SIZE:
                        await msg.reply_photo(photo=media, caption=caption)
                    else:
                        await msg.reply_document(document=media, caption=caption)

                if mp3_path:
                    try:
                        with mp3_path.open('rb') as audio:
                            mp3_caption = f"🎵 MP3 из видео {position}/{total}" if total > 1 else "🎵 MP3 из видео"
                            await msg.reply_audio(audio=audio, caption=mp3_caption)
                    except Exception as exc:
                        log.warning("MP3 upload failed for %s: %s", user.id, exc)
                        mp3_error = 'не удалось отправить MP3-файл.'

        if mp3_error:
            await status.edit_text(f"{label}Видео отправлено, но {mp3_error}")
        else:
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

def _topmusic_key(track):
    value = f"{track['artist']}|{track['title']}".casefold()
    key = re.sub(r"[^\w]+", "", value, flags=re.UNICODE)
    return key or track["id"]


def fetch_topmusic_chart():
    request = urllib.request.Request(
        TOPMUSIC_FEED_URL,
        headers={"User-Agent": "DayZVukBot/1.0"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"Apple chart returned HTTP {response.status}")
        payload = json.load(response)

    feed = payload.get("feed", {})
    tracks = []
    for item in feed.get("results", []):
        track_id = str(item.get("id", "")).strip()
        title = str(item.get("name", "")).strip()
        artist = str(item.get("artistName", "")).strip()
        url = str(item.get("url", "")).strip()
        if (
            track_id
            and title
            and artist
            and url.startswith("https://music.apple.com/")
        ):
            tracks.append({
                "id": track_id,
                "title": title,
                "artist": artist,
                "url": url,
            })
    if not tracks:
        raise RuntimeError("Apple chart returned no usable songs")
    return tracks


def reserve_new_topmusic_tracks(user_id, tracks, limit=10):
    user_id = int(user_id)
    with sqlite3.connect(TOPMUSIC_DB, timeout=10) as db:
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute(
            "CREATE TABLE IF NOT EXISTS topmusic_seen ("
            "user_id INTEGER NOT NULL, "
            "track_key TEXT NOT NULL, "
            "PRIMARY KEY (user_id, track_key))"
        )
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        seen = {
            row[0]
            for row in db.execute(
                "SELECT track_key FROM topmusic_seen WHERE user_id = ?",
                (user_id,),
            )
        }
        selected = []
        reserved_keys = set()
        for track in tracks:
            key = _topmusic_key(track)
            if key in seen or key in reserved_keys:
                continue
            selected.append({**track, "seen_key": key})
            reserved_keys.add(key)
            if len(selected) == limit:
                break
        db.executemany(
            "INSERT OR IGNORE INTO topmusic_seen (user_id, track_key) VALUES (?, ?)",
            [(user_id, track["seen_key"]) for track in selected],
        )
        db.commit()
        return selected


def release_topmusic_tracks(user_id, tracks):
    if not tracks:
        return
    with sqlite3.connect(TOPMUSIC_DB, timeout=10) as db:
        db.executemany(
            "DELETE FROM topmusic_seen WHERE user_id = ? AND track_key = ?",
            [(int(user_id), track["seen_key"]) for track in tracks],
        )
        db.commit()


async def topmusic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    status = await message.reply_text("⏳ Получаю актуальный чарт Apple Music…")
    try:
        tracks = await asyncio.to_thread(fetch_topmusic_chart)
        selected = await asyncio.to_thread(
            reserve_new_topmusic_tracks,
            user.id,
            tracks,
            10,
        )
    except Exception as exc:
        log.warning("Could not fetch top music chart: %s", type(exc).__name__)
        await status.edit_text(
            "Не удалось получить чарт Apple Music. Попробуй команду немного позже."
        )
        return

    if not selected:
        await status.edit_text(
            "В текущем топ-100 Apple Music нет новых для тебя треков. "
            "Повторов не будет; попробуй позже, когда чарт обновится."
        )
        return

    region = "Россия" if TOPMUSIC_STOREFRONT == "ru" else TOPMUSIC_STOREFRONT.upper()
    lines = [
        f"🎧 Новые треки из чарта Apple Music ({region}):"
    ]
    for rank, track in enumerate(selected, start=1):
        label = html.escape(f"{track['title']} — {track['artist']}")
        url = html.escape(track["url"], quote=True)
        lines.append(f'{rank}. <a href="{url}">{label}</a>')
    lines.append(
        "\nСлушай по официальным ссылкам. Защищённые треки бот не скачивает."
    )

    try:
        await status.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        await asyncio.to_thread(release_topmusic_tracks, user.id, selected)
        log.warning("Could not send top music chart: %s", type(exc).__name__)


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
    app.add_handler(CommandHandler('topmusic', topmusic))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
