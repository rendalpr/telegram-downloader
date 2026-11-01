import asyncio, logging, os, re, tempfile, time
from pathlib import Path
from urllib.parse import urlparse
import yt_dlp
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MAX_SIZE = 45 * 1024 * 1024
COOLDOWN = 15
last_request = {}
HOSTS = {'youtube.com','www.youtube.com','youtu.be','m.youtube.com','tiktok.com','www.tiktok.com','vm.tiktok.com','vt.tiktok.com','instagram.com','www.instagram.com','pinterest.com','www.pinterest.com','pin.it','facebook.com','www.facebook.com','fb.watch','x.com','www.x.com','twitter.com','www.twitter.com','threads.net','www.threads.net'}

def get_url(text):
    m = re.search(r'https?://\\S+', text)
    if not m: return None
    url = m.group(0).rstrip('.,!?)')
    try: host = (urlparse(url).hostname or '').lower()
    except ValueError: return None
    return url if host in HOSTS else None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text('Привет! Пришли публичную ссылку на видео или изображение. Поддерживаются YouTube, TikTok, Instagram, Pinterest, Facebook, X и Threads. Скачивай только материалы, на которые у тебя есть права.')

def download(url, folder):
    opts = {'outtmpl': str(Path(folder) / '%(title).70s-%(id)s.%(ext)s'), 'format': 'best[filesize<45M]/best', 'merge_output_format': 'mp4', 'noplaylist': True, 'max_filesize': MAX_SIZE, 'quiet': True, 'no_warnings': True, 'retries': 2, 'socket_timeout': 20}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        if not path.exists():
            files = list(Path(folder).glob('*'))
            if files: path = files[0]
        if not path.exists() or path.stat().st_size > MAX_SIZE: raise ValueError('File too large or missing')
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
    status = await msg.reply_text('⏳ Загружаю…')
    try:
        await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_DOCUMENT)
        with tempfile.TemporaryDirectory() as folder:
            path = await asyncio.to_thread(download, url, folder)
            with path.open('rb') as media:
                if path.suffix.lower() in {'.mp4','.mov','.mkv','.webm'}: await msg.reply_video(video=media, caption='Готово.')
                else: await msg.reply_document(document=media, caption='Готово.')
        await status.delete()
    except Exception as exc:
        log.warning('Download failed for %s: %s', user.id, exc)
        await status.edit_text('Не удалось скачать: платформа могла ограничить доступ, ссылка приватная или файл слишком большой.')

def main():
    if not TOKEN: raise RuntimeError('Set TELEGRAM_BOT_TOKEN environment variable')
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()