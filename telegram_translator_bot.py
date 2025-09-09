import os
import re
import logging
from typing import List

from googletrans import Translator
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------- Logging --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# -------------------- Config --------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")

# Fixed Render URL you provided
PUBLIC_URL = "https://telegram-translator-bot-i9yl.onrender.com".rstrip("/")

# Render provides a port in $PORT for web services
PORT = int(os.environ.get("PORT", "10000"))

# Telegram message safety
TG_MAX = 4096
TG_SAFE = 4000  # safety margin below hard limit
TRANSLATE_CHUNK = 1800  # conservative chunk size for translation backend

# Modes
MODE_AUTO = "auto"
MODE_TO_UK = "to_uk"
MODE_TO_EN = "to_en"

# Simple per-chat mode store (resets when app restarts)
chat_modes = {}

# Heuristic: detect Ukrainian/Cyrillic
UA_CYRILLIC_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")

# Google Translate client
translator = Translator(service_urls=["translate.googleapis.com", "translate.google.com"])

# -------------------- Utilities --------------------
def detect_direction(text: str) -> str:
    return MODE_TO_EN if UA_CYRILLIC_RE.search(text) else MODE_TO_UK

def chunk_text(text: str, limit: int) -> List[str]:
    """
    Chunk text safely by paragraphs -> sentences -> words, keeping order and not breaking words.
    """
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = ""

    def push():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    # Split by double newlines, keeping structure
    parts = re.split(r"(\n{2,})", text)
    for part in parts:
        if len(current) + len(part) <= limit:
            current += part
            continue

        # Split by sentence when needed
        sentences = re.split(r"(?<=[.!?])\s+", part)
        for s in sentences:
            if len(current) + len(s) + 1 <= limit:
                current += (s + " ")
            elif len(s) <= limit:
                push()
                current += (s + " ")
            else:
                # Split long sentences by words
                words = s.split(" ")
                buf = ""
                for w in words:
                    if len(buf) + len(w) + 1 <= limit:
                        buf += (w + " ")
                    else:
                        chunks.append(buf.strip())
                        buf = w + " "
                if buf.strip():
                    chunks.append(buf.strip())
        push()

    push()
    return chunks

def translate_text(text: str, direction: str) -> str:
    if direction == MODE_TO_UK:
        src, dest = "en", "uk"
    elif direction == MODE_TO_EN:
        src, dest = "uk", "en"
    else:
        src, dest = "auto", "uk"

    in_chunks = chunk_text(text, TRANSLATE_CHUNK)
    out_chunks: List[str] = []

    for ch in in_chunks:
        # googletrans returns an object; we need .text
        result = translator.translate(ch, src=src, dest=dest)
        out_chunks.append(result.text)

    return "\n".join(out_chunks).strip()

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    parts = chunk_text(text, TG_SAFE)
    # Reply first, then follow-ups to preserve order visually
    if not parts:
        return
    await update.message.reply_text(parts[0])
    for p in parts[1:]:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=p)

# -------------------- Handlers --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_modes[update.effective_chat.id] = MODE_AUTO
    await update.message.reply_text(
        "Hi! I translate English ↔ Ukrainian instantly.\n\n"
        "- Send text: I auto-detect (Latin → UK, Cyrillic → EN).\n"
        "- /to_en: Ukrainian → English\n"
        "- /to_uk: English → Ukrainian\n"
        "- /auto: Auto-detect\n\n"
        "I handle long messages without losing any content."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/auto – Auto-detect per message\n"
        "/to_en – Force Ukrainian → English\n"
        "/to_uk – Force English → Ukrainian\n"
        "/help – Show this help"
    )

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_modes[update.effective_chat.id] = MODE_AUTO
    await update.message.reply_text("Mode set to auto-detect.")

async def to_en_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_modes[update.effective_chat.id] = MODE_TO_EN
    await update.message.reply_text("Mode set to Ukrainian → English.")

async def to_uk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_modes[update.effective_chat.id] = MODE_TO_UK
    await update.message.reply_text("Mode set to English → Ukrainian.")

async def translate_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return

    mode = chat_modes.get(update.effective_chat.id, MODE_AUTO)
    direction = detect_direction(text) if mode == MODE_AUTO else mode

    try:
        translated = await context.application.run_in_threadpool(translate_text, text, direction)
        if not translated:
            await update.message.reply_text("I couldn't translate that. Please try again.")
            return
        await send_long_text(update, context, translated)
    except Exception as e:
        logger.error("Translation failed: %s", e)
        await update.message.reply_text("Translation error. Please try again later.")

# -------------------- App setup --------------------
def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.add_handler(CommandHandler("to_en", to_en_cmd))
    app.add_handler(CommandHandler("to_uk", to_uk_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_msg))
    return app

def main():
    # Build app
    application = build_application()

    # Webhook settings: PTB runs its own HTTP server. No Flask.
    path = "webhook"
    webhook_url = f"{PUBLIC_URL}/{path}"

    logger.info("Starting PTB webhook server on 0.0.0.0:%s with URL %s", PORT, webhook_url)

    # IMPORTANT: run_webhook is a blocking call that handles initialization internally.
    # Do NOT wrap with asyncio.run and do NOT call initialize()/start() manually.
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=path,
        webhook_url=webhook_url,
        drop_pending_updates=True,
        stop_signals=None,  # Render sends SIGTERM; PTB handles graceful shutdown
    )

if __name__ == "__main__":
    main()
