import base64, asyncio, html, json, logging, os, re, shutil, sqlite3, subprocess, tempfile
from curl_cffi import requests as curl_requests
from mutagen import File as MutagenFile
from mutagen._vorbis import VCommentDict
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, COMM, ID3, TALB, TCOP, TCON, TIT2, TPE1, TPE2, TPUB, TSSE, TXXX, WXXX, WOAR
from mutagen.mp4 import MP4, MP4Cover
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
MAX_TELEGRAM_DOWNLOAD_SIZE = 20 * 1024 * 1024
AUDIO_INPUT_EXTENSIONS = {'.mp3', '.m4a', '.aac', '.flac', '.ogg', '.oga', '.opus', '.wav', '.aiff'}
MAX_URLS_PER_MESSAGE = 10
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)
MAX_PHOTO_SIZE = 9 * 1024 * 1024
WELCOME_IMAGE = Path(__file__).resolve().parent / "assets" / "welcome.png"
BRAND_COVER_IMAGE = Path(__file__).resolve().parent / "assets" / "day_zvuk_cover.jpg"
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None

TOPMUSIC_STOREFRONT = os.getenv("TOPMUSIC_STOREFRONT", "ru").strip().lower()
if not re.fullmatch(r"[a-z]{2}", TOPMUSIC_STOREFRONT):
    TOPMUSIC_STOREFRONT = "ru"
TOPMUSIC_FEED_URL = (
    f"https://itunes.apple.com/{TOPMUSIC_STOREFRONT}/rss/topsongs/limit=100/json"
)
TOPMUSIC_DATA_DIR = Path(os.getenv("TOPMUSIC_DATA_DIR", "").strip() or Path(__file__).resolve().parent)
TOPMUSIC_DATA_DIR.mkdir(parents=True, exist_ok=True)
TOPMUSIC_DB = TOPMUSIC_DATA_DIR / "topmusic_seen.sqlite3"

if not FFMPEG_AVAILABLE:
    log.warning("FFmpeg is not installed; downloads will use the best single-file format available.")

WELCOME_TEXT = (
    "👋 Привет! Я - «Дай Скачать by @day_zvuk» 🎬\n\n"
    "🔗 Отправь публичную ссылку, и я попробую скачать видео, фото или аудио "
    "в максимальном доступном качестве.\n"
    "🎵 Для видео пришлю оригинал и отдельный MP3.\n"
    "🎼 Пришли трек — поставлю обложку канала и ссылку в теги, сохранив название и исполнителя.\n\n"
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

def clean_tag_value(value):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()


def read_audio_metadata(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is not installed")
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_format", "-show_streams",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    info = json.loads(result.stdout or "{}")
    groups = [info.get("format", {}).get("tags", {})]
    groups.extend(stream.get("tags", {}) for stream in info.get("streams", []))

    def find_tag(names):
        for tags in groups:
            normalized = {
                str(key).casefold(): clean_tag_value(value)
                for key, value in tags.items()
            }
            for name in names:
                if normalized.get(name):
                    return normalized[name]
        return ""

    return (
        find_tag(("title", "track_name")),
        find_tag(("artist", "performer", "album_artist", "albumartist")),
    )


def strip_audio_metadata(source_path, output_path, title, artist):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")

    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source_path),
        "-map", "0:a:0",
        "-map_metadata", "-1",
        "-map_metadata:s:a:0", "-1",
        "-map_chapters", "-1",
        "-c:a", "copy",
        "-vn", "-sn", "-dn",
        "-metadata", f"title={clean_tag_value(title)}",
        "-metadata", "encoder=",
    ]
    if artist:
        command.extend(["-metadata", f"artist={clean_tag_value(artist)}"])
    if output_path.suffix.lower() == ".mp3":
        command.extend(["-id3v2_version", "3"])
    command.append(str(output_path))

    subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("clean audio output is empty")
    if output_path.stat().st_size > MAX_SIZE:
        output_path.unlink(missing_ok=True)
        raise ValueError("clean audio exceeds the 45 MiB file limit")


def apply_channel_branding(path, title, artist):
    if not BRAND_COVER_IMAGE.is_file():
        raise RuntimeError("channel cover image is missing")
    cover_data = BRAND_COVER_IMAGE.read_bytes()
    channel_url = "https://t.me/day_zvuk"
    audio = MutagenFile(path, easy=False)
    if audio is None:
        raise ValueError("unsupported audio format")
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    if isinstance(audio, MP4):
        tags.clear()
        tags["©nam"] = [title]
        if artist:
            tags["©ART"] = [artist]
        for key in ("©alb", "©cmt", "©gen", "©wrt", "©too", "cprt", "purl", "desc", "catg", "keyw"):
            tags[key] = [channel_url]
        tags["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
    elif isinstance(audio, FLAC):
        tags.clear()
        tags.vendor = channel_url
        tags["title"] = [title]
        if artist:
            tags["artist"] = [artist]
        for key in ("album", "comment", "genre", "composer", "publisher", "copyright", "website", "url", "encoder"):
            tags[key] = [channel_url]
        audio.clear_pictures()
        picture = Picture()
        picture.type = 3
        picture.mime = "image/jpeg"
        picture.desc = "Cover"
        picture.width = 640
        picture.height = 640
        picture.depth = 24
        picture.data = cover_data
        audio.add_picture(picture)
    elif isinstance(tags, ID3):
        tags.clear()
        tags.add(TIT2(encoding=3, text=[title]))
        if artist:
            tags.add(TPE1(encoding=3, text=[artist]))
        tags.add(TALB(encoding=3, text=[channel_url]))
        tags.add(TPE2(encoding=3, text=[channel_url]))
        tags.add(TCON(encoding=3, text=[channel_url]))
        tags.add(TPUB(encoding=3, text=[channel_url]))
        tags.add(TCOP(encoding=3, text=[channel_url]))
        tags.add(TSSE(encoding=3, text=[channel_url]))
        tags.add(COMM(encoding=3, lang="eng", desc="Comment", text=[channel_url]))
        tags.add(TXXX(encoding=3, desc="Website", text=[channel_url]))
        tags.add(WXXX(encoding=3, desc="Website", url=channel_url))
        tags.add(WOAR(url=channel_url))
        tags.add(APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Cover",
            data=cover_data,
        ))
    elif isinstance(tags, VCommentDict):
        tags.clear()
        tags.vendor = channel_url
        tags["title"] = [title]
        if artist:
            tags["artist"] = [artist]
        for key in ("album", "comment", "genre", "composer", "publisher", "copyright", "website", "url", "encoder"):
            tags[key] = [channel_url]
        picture = Picture()
        picture.type = 3
        picture.mime = "image/jpeg"
        picture.desc = "Cover"
        picture.width = 640
        picture.height = 640
        picture.depth = 24
        picture.data = cover_data
        encoded_picture = base64.b64encode(picture.write()).decode("ascii")
        tags["metadata_block_picture"] = [encoded_picture]
    else:
        raise ValueError("unsupported audio metadata format")

    audio.save()


def audio_output_filename(title, artist, suffix):
    base = f"{artist} - {title}" if artist else title
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", base)
    base = re.sub(r"\s+", " ", base).strip(" .")[:140] or "cleaned-audio"
    return f"{base}{suffix}"


async def clean_uploaded_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return

    audio = message.audio
    document = message.document
    uploaded = audio or document
    if not uploaded:
        return

    file_name = getattr(uploaded, "file_name", None) or ""
    suffix = Path(file_name).suffix.lower()
    mime_type = (getattr(uploaded, "mime_type", None) or "").lower()
    mime_extensions = {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "audio/x-aac": ".aac",
        "audio/x-m4a": ".m4a",
        "audio/flac": ".flac",
        "audio/x-flac": ".flac",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/aiff": ".aiff",
        "audio/x-aiff": ".aiff",
    }
    if suffix not in AUDIO_INPUT_EXTENSIONS:
        suffix = mime_extensions.get(mime_type, "")
    if suffix not in AUDIO_INPUT_EXTENSIONS:
        await message.reply_text(
            "Пришли аудиофайл MP3, M4A, FLAC, OGG/Opus, WAV или AIFF."
        )
        return

    file_size = getattr(uploaded, "file_size", None)
    if file_size and file_size > MAX_TELEGRAM_DOWNLOAD_SIZE:
        await message.reply_text(
            "Файл больше 20 МБ — стандартный Telegram Bot API не даёт боту его скачать. "
            "Пришли файл поменьше."
        )
        return

    output_suffix = ".m4a" if suffix == ".aac" else suffix
    telegram_title = clean_tag_value(getattr(audio, "title", "") if audio else "")
    telegram_artist = clean_tag_value(getattr(audio, "performer", "") if audio else "")
    fallback_title = telegram_title or (Path(file_name).stem if file_name else "Аудиотрек")
    status = await message.reply_text("⏳ Ставлю обложку канала и обновляю теги…")

    try:
        async with DOWNLOAD_SEMAPHORE:
            telegram_file = await context.bot.get_file(uploaded.file_id)
            with tempfile.TemporaryDirectory(prefix="audio-cleanup-") as folder:
                source_path = Path(folder) / f"original{suffix}"
                output_path = Path(folder) / f"cleaned{output_suffix}"
                await telegram_file.download_to_drive(custom_path=source_path)
                if source_path.stat().st_size > MAX_TELEGRAM_DOWNLOAD_SIZE:
                    raise ValueError("uploaded audio exceeds 20 MB Telegram download limit")

                title, artist = await asyncio.to_thread(
                    read_audio_metadata,
                    source_path,
                )
                title = title or fallback_title
                artist = artist or telegram_artist
                await asyncio.to_thread(
                    strip_audio_metadata,
                    source_path,
                    output_path,
                    title,
                    artist,
                )
                await asyncio.to_thread(
                    apply_channel_branding,
                    output_path,
                    title,
                    artist,
                )

                output_name = audio_output_filename(title, artist, output_suffix)
                caption = "✅ Добавил обложку и ссылку канала в теги."
                with output_path.open("rb") as cleaned_file:
                    if output_suffix in {".mp3", ".m4a"}:
                        await message.reply_audio(
                            audio=cleaned_file,
                            filename=output_name,
                            title=title[:256],
                            performer=artist[:256] if artist else None,
                            caption=caption,
                        )
                    else:
                        await message.reply_document(
                            document=cleaned_file,
                            filename=output_name,
                            caption=caption,
                        )
            try:
                await status.delete()
            except Exception as exc:
                log.debug("Could not remove audio cleanup status: %s", exc)
    except Exception as exc:
        log.warning(
            "Audio cleanup failed for user %s (%s): %s",
            update.effective_user.id if update.effective_user else "unknown",
            type(exc).__name__,
            exc,
        )
        if isinstance(exc, ValueError) and "20 mb" in str(exc).lower():
            error_text = "Файл больше 20 МБ. Пришли аудио поменьше."
        elif isinstance(exc, RuntimeError) and ("ffmpeg" in str(exc).lower() or "ffprobe" in str(exc).lower()):
            error_text = "На сервере недоступен FFmpeg/FFprobe."
        else:
            error_text = (
                "Не удалось очистить файл. Проверь, что аудио не повреждено "
                "и его формат поддерживается."
            )
        try:
            await status.edit_text(error_text)
        except Exception as status_exc:
            log.warning("Could not report audio cleanup failure: %s", status_exc)


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
    response = curl_requests.get(
        TOPMUSIC_FEED_URL,
        impersonate="chrome",
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    feed = payload.get("feed", {})
    tracks = []
    items = feed.get("results") or feed.get("entry", [])
    for item in items:
        raw_id = item.get("id", "")
        if isinstance(raw_id, dict):
            track_id = str(raw_id.get("attributes", {}).get("im:id", "")).strip()
            title = str(item.get("im:name", {}).get("label", "")).strip()
            artist = str(item.get("im:artist", {}).get("label", "")).strip()
            url = str(raw_id.get("label", "")).strip()
        else:
            track_id = str(raw_id).strip()
            title = str(item.get("name", "")).strip()
            artist = str(item.get("artistName", "")).strip()
            url = str(item.get("url", "")).strip()
        if track_id and title and artist and url.startswith("https://music.apple.com/"):
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
        log.warning("Could not fetch top music chart (%s): %s", type(exc).__name__, exc)
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
        f"🎧 Новые треки из топ-чарта Apple / iTunes ({region}):"
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
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.ALL, clean_uploaded_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
