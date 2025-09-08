import os
import re
import logging
import asyncio
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
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")

PUBLIC_URL = "https://telegram-translator-bot-i9yl.onrender.com"  # Your fixed Render URL

TG_MAX = 4096
TG_SAFE = 4000
TRANSLATE_CHUNK = 1800

MODE_AUTO = "auto"
MODE_TO_UK = "to_uk"
MODE_TO_EN = "to_en"

chat_modes = {}
UA_CYRILLIC_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")

translator = Translator()

# -------------------- Utilities --------------------
def detect_direction(text: str) -> str:
    if UA_CYRILLIC_RE.search(text):
        return MODE_TO_EN
    return MODE_TO_UK

def chunk_text(text: str, limit: int) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    for word in text.split():
        if sum(len(w) + 1 for w in current) + len(word) + 1 <= limit:
            current.append(word)
        else:
            chunks.append(" ".join(current))
            current = [word]
    if current:
        chunks.append(" ".join(current))
    return chunks

def translate_text(text: str, direction: str) -> str:
    if direction == MODE_TO_UK:
        src, dest = "en", "uk"
    elif direction == MODE_TO_EN:
        src, dest = "uk", "en"
    else:
        src, dest = "auto", "uk"

    in_chunks = chunk_text(text, TRANSLATE_CHUNK)
    out_chunks = []
    for chunk in in_chunks:
        translated = translator.translate(chunk, src=src, dest=dest)
        out_chunks.append(translated.text)
    return "\n".join(out_chunks)

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    out_parts = chunk_text(text, TG_SAFE)
    first = True
    for part in out_parts:
        if first:
            await update.message.reply_text(part)
            first = False
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=part)

# -------------------- Handlers --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_modes[update.effective_chat.id] = MODE_AUTO
    await update.message.reply_text(
        "Hi! I translate English ↔ Ukrainian instantly.\n\n"
        "Send any text and I’ll auto-detect direction.\n"
        "Use /to_en for Ukrainian → English, /to_uk for English → Ukrainian, /auto for auto-detect."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/auto – Auto-detect\n"
        "/to_en – Ukrainian → English\n"
        "/to_uk – English → Ukrainian\n"
        "/help – Show help"
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
        translated = await asyncio.to_thread(translate_text, text, direction)
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

async def run_webhook(application: Application):
    port = int(os.environ.get("PORT", "10000"))
    path = "webhook"
    webhook_url = f"{PUBLIC_URL.rstrip('/')}/{path}"

    logger.info("Starting webhook at %s", webhook_url)
    await application.start()
    await application.updater.start_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
        webhook_url=webhook_url,
        drop_pending_updates=True,
    )
    await application.updater.wait_until_closed()

def main():
    application = build_application()
    asyncio.run(run_webhook(application))

if __name__ == "__main__":
    main()
